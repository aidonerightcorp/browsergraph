"""URL handling and the crawl frontier.

Normalisation matters more than it looks: without it a crawler revisits the
same page as `/about`, `/about/`, `/about?utm_source=x` and `/about#team`, and
a 200-page budget is spent on 40 real pages.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

_TRACKING = re.compile(r"^(utm_\w+|fbclid|gclid|msclkid|mc_[ce]id|ref|source)$", re.I)

_NON_PAGE = re.compile(
    r"\.(?:png|jpe?g|gif|svg|webp|ico|css|js|json|xml|pdf|zip|gz|tar|mp[34]|"
    r"avi|mov|woff2?|ttf|eot|dmg|exe|apk)$", re.I)

_HREF = re.compile(r"""href\s*=\s*["']([^"'#][^"']*)["']""", re.I)


def normalize(url: str, base: str = "") -> str:
    """Absolute, fragment-free, tracking-free, trailing-slash-normalised."""
    if not url:
        return ""
    url = url.strip()
    if url.startswith(("mailto:", "tel:", "javascript:", "data:")):
        return ""
    if base:
        url = urljoin(base, url)
    url, _ = urldefrag(url)
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return ""

    query = "&".join(
        part for part in p.query.split("&")
        if part and not _TRACKING.match(part.split("=", 1)[0]))
    # Root is normalised to empty, not "/", so `https://x.example` and
    # `https://x.example/` are one URL rather than two frontier entries.
    path = re.sub(r"/{2,}", "/", p.path)
    while len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    if path == "/":
        path = ""
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if (p.scheme == "https" and host.endswith(":443")) or \
       (p.scheme == "http" and host.endswith(":80")):
        host = host.rsplit(":", 1)[0]
    return urlunparse((p.scheme, host, path, "", query, ""))


def registrable(url: str) -> str:
    """Host without `www.`, for same-site comparisons."""
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def same_site(a: str, b: str, include_subdomains: bool = True) -> bool:
    ha, hb = registrable(a), registrable(b)
    if not ha or not hb:
        return False
    if ha == hb:
        return True
    return include_subdomains and (ha.endswith("." + hb) or hb.endswith("." + ha))


def links_from_html(html: str, base: str = "") -> list[str]:
    """Normalised, page-like links in document order."""
    out, seen = [], set()
    for m in _HREF.finditer(html or ""):
        url = normalize(m.group(1), base)
        if not url or _NON_PAGE.search(urlparse(url).path):
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


# --- frontier ---------------------------------------------------------------

@dataclass
class Frontier:
    """Breadth-first queue with de-duplication and depth tracking.

    Breadth-first is deliberate: a site's valuable pages (contact, about,
    products) are usually shallow, so a depth-first crawler burns its budget in
    a pagination tunnel.
    """
    seed: str = ""
    include_subdomains: bool = True
    max_depth: int = 2
    max_pages: int = 50
    allow: re.Pattern | None = None
    deny: re.Pattern | None = None

    _queue: list[tuple[str, int]] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    visited: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.seed:
            self.push(self.seed, 0)

    def _skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def push(self, url: str, depth: int) -> bool:
        url = normalize(url, self.seed)
        if not url:
            return False
        if url in self.seen:
            self._skip("duplicate")
            return False
        if depth > self.max_depth:
            self._skip("too_deep")
            return False
        if self.seed and not same_site(url, self.seed, self.include_subdomains):
            self._skip("offsite")
            return False
        if self.deny and self.deny.search(url):
            self._skip("denied")
            return False
        if self.allow and not self.allow.search(url):
            self._skip("not_allowed")
            return False
        self.seen.add(url)
        self._queue.append((url, depth))
        return True

    def extend(self, urls: list[str], depth: int) -> int:
        return sum(1 for u in urls if self.push(u, depth))

    def pop(self) -> tuple[str, int] | None:
        if not self._queue or len(self.visited) >= self.max_pages:
            return None
        url, depth = self._queue.pop(0)
        self.visited.append(url)
        return url, depth

    @property
    def exhausted(self) -> bool:
        return not self._queue or len(self.visited) >= self.max_pages

    def report(self) -> dict:
        return {"visited": len(self.visited), "queued": len(self._queue),
                "seen": len(self.seen), "skipped": dict(self.skipped)}


# --- robots -----------------------------------------------------------------

@dataclass
class Robots:
    """Minimal robots.txt policy for a single user-agent.

    Not a full implementation — it covers Disallow/Allow/Crawl-delay, which is
    what a polite crawler needs. `respect=False` records the choice explicitly
    rather than leaving it implicit.
    """
    disallow: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)
    crawl_delay: float = 0.0
    respect: bool = True

    @staticmethod
    def parse(text: str, agent: str = "*") -> Robots:
        r = Robots()
        applies = False
        for line in (text or "").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                applies = value == "*" or value.lower() == agent.lower()
            elif not applies:
                continue
            elif key == "disallow" and value:
                r.disallow.append(value)
            elif key == "allow" and value:
                r.allow.append(value)
            elif key == "crawl-delay":
                try:
                    r.crawl_delay = float(value)
                except ValueError:
                    pass
        return r

    def allowed(self, url: str) -> bool:
        if not self.respect:
            return True
        path = urlparse(url).path or "/"
        best_allow = max((len(p) for p in self.allow if path.startswith(p)), default=-1)
        best_deny = max((len(p) for p in self.disallow if path.startswith(p)), default=-1)
        return best_allow >= best_deny
