"""Page content extraction — title, metadata, readable text, articles.

Stdlib only. HTML is stripped with a tolerant tag-aware pass rather than a
parser dependency: the goal is usable text for pattern extraction and
classification, not a faithful DOM.
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from datetime import date

_SCRIPTY = re.compile(r"<(script|style|noscript|svg|template)\b.*?</\1>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"</(p|div|section|article|li|h[1-6]|tr|br)\s*>", re.I)

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META = re.compile(
    r"""<meta\s+[^>]*?(?:name|property)\s*=\s*["']([^"']+)["'][^>]*?"""
    r"""content\s*=\s*["']([^"']*)["']""", re.I)
_META_REV = re.compile(
    r"""<meta\s+[^>]*?content\s*=\s*["']([^"']*)["'][^>]*?"""
    r"""(?:name|property)\s*=\s*["']([^"']+)["']""", re.I)
_H = re.compile(r"<h([1-3])[^>]*>(.*?)</h\1>", re.I | re.S)
_JSONLD = re.compile(
    r'<script[^>]+type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S)

_DATE_PATTERNS = (
    re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(20\d{2})\b", re.I),
    re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(20\d{2})\b", re.I),
)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def text_of(html: str) -> str:
    """Readable text, with block boundaries preserved as newlines."""
    if not html:
        return ""
    s = _SCRIPTY.sub(" ", html)
    s = _BLOCK.sub("\n", s)
    s = _TAG.sub(" ", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n\s*", "\n\n", s)
    return s.strip()


def meta_tags(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for pattern, order in ((_META, (1, 2)), (_META_REV, (2, 1))):
        for m in pattern.finditer(html or ""):
            key = _html.unescape(m.group(order[0])).strip().lower()
            val = _html.unescape(m.group(order[1])).strip()
            if key and val:
                out.setdefault(key, val)
    return out


def title_of(html: str) -> str:
    m = _TITLE.search(html or "")
    if m:
        return re.sub(r"\s+", " ", _html.unescape(_TAG.sub("", m.group(1)))).strip()
    meta = meta_tags(html)
    return meta.get("og:title", "")


def headings(html: str) -> list[tuple[int, str]]:
    out = []
    for m in _H.finditer(html or ""):
        text = re.sub(r"\s+", " ", _html.unescape(_TAG.sub("", m.group(2)))).strip()
        if text:
            out.append((int(m.group(1)), text))
    return out


def jsonld(html: str) -> list[dict]:
    """Embedded JSON-LD blocks — the most reliable structured source when present."""
    import json
    out = []
    for m in _JSONLD.finditer(html or ""):
        try:
            data = json.loads(m.group(1).strip())
        except (ValueError, TypeError):
            continue
        out.extend(data if isinstance(data, list) else [data])
    return [d for d in out if isinstance(d, dict)]


def find_date(text: str) -> str | None:
    """First plausible publication date as ISO, or None."""
    for pattern in _DATE_PATTERNS:
        m = pattern.search(text or "")
        if not m:
            continue
        try:
            groups = m.groups()
            if pattern is _DATE_PATTERNS[0]:
                y, mo, d = int(groups[0]), int(groups[1]), int(groups[2])
            elif pattern is _DATE_PATTERNS[1]:
                d, mo, y = int(groups[0]), _MONTHS[groups[1][:3].lower()], int(groups[2])
            else:
                mo, d, y = _MONTHS[groups[0][:3].lower()], int(groups[1]), int(groups[2])
            return date(y, mo, d).isoformat()
        except (ValueError, KeyError):
            continue
    return None


@dataclass
class Page:
    url: str = ""
    title: str = ""
    description: str = ""
    text: str = ""
    headings: list[tuple[int, str]] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    published: str | None = None
    lang: str = ""
    structured: list[dict] = field(default_factory=list)
    mailtos: list[str] = field(default_factory=list)   # from mailto: hrefs

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    def to_dict(self) -> dict:
        return {"url": self.url, "title": self.title, "description": self.description,
                "published": self.published, "lang": self.lang,
                "word_count": self.word_count,
                "headings": [h[1] for h in self.headings[:10]]}


_MAILTO = re.compile(r"""mailto:([^"'?\s>]+)""", re.I)


def parse_page(html: str, url: str = "") -> Page:
    from browsergraph.extract.links import links_from_html
    meta = meta_tags(html)
    text = text_of(html)
    lang = ""
    m = re.search(r"<html[^>]+lang\s*=\s*[\"']([\w-]+)", html or "", re.I)
    if m:
        lang = m.group(1)
    published = (meta.get("article:published_time") or meta.get("datepublished")
                 or meta.get("date") or "")
    return Page(
        url=url,
        title=title_of(html),
        description=meta.get("description") or meta.get("og:description", ""),
        text=text,
        headings=headings(html),
        links=links_from_html(html, url),
        published=(published[:10] if published else find_date(text[:1500])),
        lang=lang,
        structured=jsonld(html),
        # mailto links are normalised away as non-page URLs, so the address
        # would otherwise be lost on any site that only links its email.
        mailtos=list(dict.fromkeys(m.group(1).lower() for m in _MAILTO.finditer(html or ""))),
    )


# --- article detection ------------------------------------------------------

_NEWS_HINT = re.compile(
    r"/(news|blog|press|articles?|stories|insights?|updates?|media|"
    r"announcements?|20\d{2}/\d{2})/", re.I)


def looks_like_article(page: Page) -> bool:
    """Heuristic: a story, not a listing or a landing page.

    Requires substance *and* a signal — a long page with no date and no
    article markers is usually a product or category page.
    """
    if page.word_count < 120:
        return False
    signals = 0
    if page.published:
        signals += 1
    if _NEWS_HINT.search(page.url):
        signals += 1
    if any(d.get("@type") in ("Article", "NewsArticle", "BlogPosting")
           for d in page.structured):
        signals += 2
    if len([h for h in page.headings if h[0] == 1]) == 1:
        signals += 1
    return signals >= 2
