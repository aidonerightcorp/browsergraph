"""HTML preprocessing strategies — how a page is presented to a model.

This is a real dimension, not a detail. Raw HTML for a modern page is 200-800k
characters of framework noise; a model given that spends its context on
`<div class="css-1x2y3z">` and misses the content. The strategy chosen changes
both cost and accuracy, and the right one differs by task: a selector question
needs structure, a classification question needs prose.

Every strategy is deterministic and dependency-free, so it runs on every page
without a model and without a parser dependency.
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from enum import Enum

from browsergraph.extract.content import text_of

_SCRIPTY = re.compile(r"<(script|style|noscript|svg|template|iframe)\b.*?</\1>", re.I | re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_ATTRS_KEEP = ("id", "name", "type", "role", "aria-label", "placeholder",
               "href", "value", "alt", "title", "data-testid")

_INTERACTIVE = re.compile(
    r"<(a|button|input|select|textarea|form|label)\b([^>]*)>(.*?)(?=</\1>|<)",
    re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
# HTML permits unquoted attribute values (`id=send`), and hand-written
# markup uses them constantly — a quoted-only pattern silently loses ids.
_ATTR = re.compile(r"""(\w[\w-]*)\s*=\s*(?:["']([^"']*)["']|([^\s>"']+))""")


class Preprocess(str, Enum):
    """How page HTML is reduced before it reaches a model."""
    RAW = "raw"                     # unmodified; expensive, occasionally necessary
    CLEAN_HTML = "clean_html"       # scripts/styles/comments stripped, attrs pruned
    TEXT = "text"                   # readable text only
    READABILITY = "readability"     # main content block, boilerplate dropped
    DOM_SKELETON = "dom_skeleton"   # structure with ids/roles, no prose
    INTERACTIVE = "interactive"     # only actionable elements — for selector work
    ACCESSIBILITY = "accessibility" # role/name pairs, closest to how a user reads it
    MARKDOWN = "markdown"           # headings/links/lists as markdown


#: Which strategy suits which question. Used by `recommend`.
_SUITED = {
    "selector": (Preprocess.INTERACTIVE, Preprocess.ACCESSIBILITY, Preprocess.DOM_SKELETON),
    "classify": (Preprocess.READABILITY, Preprocess.TEXT, Preprocess.MARKDOWN),
    "extract": (Preprocess.MARKDOWN, Preprocess.READABILITY, Preprocess.TEXT),
    "verify": (Preprocess.TEXT, Preprocess.ACCESSIBILITY, Preprocess.READABILITY),
    "navigate": (Preprocess.INTERACTIVE, Preprocess.ACCESSIBILITY, Preprocess.MARKDOWN),
}


@dataclass
class Reduced:
    """The result, with the numbers needed to judge whether it was worth it."""
    strategy: Preprocess
    content: str
    original_chars: int
    chars: int

    @property
    def ratio(self) -> float:
        return self.chars / self.original_chars if self.original_chars else 1.0

    @property
    def saved_pct(self) -> float:
        return 100.0 * (1 - self.ratio)

    def to_dict(self) -> dict:
        return {"strategy": self.strategy.value, "original_chars": self.original_chars,
                "chars": self.chars, "saved_pct": round(self.saved_pct, 1)}


def _strip_noise(html: str) -> str:
    return _COMMENT.sub(" ", _SCRIPTY.sub(" ", html or ""))


def backends() -> dict[str, bool]:
    """Which optional extraction backends are available on this machine."""
    import importlib.util as _u
    return {name: _u.find_spec(mod) is not None for name, mod in (
        ("trafilatura", "trafilatura"),      # readability upgrade
        ("selectolax", "selectolax"),        # fast CSS over served HTML
        ("resiliparse", "resiliparse"),      # very fast text extraction
        ("markdownify", "markdownify"),      # richer html->markdown
    )}


def clean_html(html: str) -> str:
    """Drop scripts/styles/comments and every attribute except the useful ones."""
    def prune(m: re.Match) -> str:
        tag = m.group(0)
        name = re.match(r"</?\s*([\w-]+)", tag)
        if not name:
            return ""
        kept = [f'{k}="{v or v2}"' for k, v, v2 in _ATTR.findall(tag)
                if k.lower() in _ATTRS_KEEP and (v or v2)]
        closing = "/" if tag.startswith("</") else ""
        return f"<{closing}{name.group(1)}{(' ' + ' '.join(kept)) if kept else ''}>"

    out = _TAG.sub(prune, _strip_noise(html))
    return re.sub(r"\s+", " ", out).strip()


def dom_skeleton(html: str, max_depth: int = 6) -> str:
    """Tags with identifying attributes only — structure without prose."""
    kept = []
    for m in _TAG.finditer(_strip_noise(html)):
        tag = m.group(0)
        if tag.startswith("</"):
            continue
        name = re.match(r"<\s*([\w-]+)", tag)
        if not name:
            continue
        attrs = {k.lower(): (v or v2) for k, v, v2 in _ATTR.findall(tag)}
        ident = {k: attrs[k] for k in ("id", "role", "aria-label", "data-testid", "name")
                 if attrs.get(k)}
        if ident or name.group(1).lower() in ("form", "table", "nav", "main", "header"):
            bits = " ".join(f'{k}="{v}"' for k, v in ident.items())
            kept.append(f"<{name.group(1)}{(' ' + bits) if bits else ''}>")
    return "\n".join(kept)


def interactive(html: str, limit: int = 200) -> str:
    """Actionable elements with a usable selector — the shape a model needs to click."""
    rows = []
    for m in _INTERACTIVE.finditer(_strip_noise(html)):
        tag, attrs_raw, inner = m.group(1).lower(), m.group(2), m.group(3)
        attrs = {k.lower(): (v or v2) for k, v, v2 in _ATTR.findall(attrs_raw)}
        label = (_html.unescape(_TAG.sub("", inner)).strip()[:60]
                 or attrs.get("aria-label") or attrs.get("placeholder")
                 or attrs.get("value") or attrs.get("title") or "")
        if attrs.get("id"):
            selector = f"#{attrs['id']}"
        elif attrs.get("data-testid"):
            selector = f'[data-testid="{attrs["data-testid"]}"]'
        elif attrs.get("name"):
            selector = f'{tag}[name="{attrs["name"]}"]'
        else:
            selector = tag
        extra = f' type={attrs["type"]}' if attrs.get("type") else ""
        rows.append(f"{tag}{extra} selector={selector} label={label!r}")
        if len(rows) >= limit:
            break
    return "\n".join(rows)


def accessibility(html: str, limit: int = 300) -> str:
    """role: name pairs — closest to how a screen reader presents the page."""
    rows = []
    src = _strip_noise(html)
    # One pass per tag: a single alternation regex stops at the outermost
    # match, so <main> would swallow the <h1> inside it and the heading would
    # never be reported.
    pattern = "|".join(("h1", "h2", "h3", "h4", "h5", "h6", "a", "button",
                        "label", "li", "td", "th", "nav", "main", "form"))
    matches = []
    for tag_name in pattern.split("|"):
        for m in re.finditer(rf"<({tag_name})\b([^>]*)>(.*?)</\1>", src, re.I | re.S):
            matches.append((m.start(), m))
    for _, m in sorted(matches, key=lambda t: t[0]):
        tag, attrs_raw, inner = m.group(1).lower(), m.group(2), m.group(3)
        if tag in ("nav", "main", "form"):
            inner = ""          # containers contribute their role, not their prose
        attrs = {k.lower(): (v or v2) for k, v, v2 in _ATTR.findall(attrs_raw)}
        role = attrs.get("role") or {
            "a": "link", "button": "button", "input": "textbox", "nav": "navigation",
            "main": "main", "form": "form", "li": "listitem",
        }.get(tag, "heading" if tag.startswith("h") else tag)
        name = (attrs.get("aria-label") or
                _html.unescape(_TAG.sub(" ", inner)).strip()[:80])
        if name:
            rows.append(f"{role}: " + re.sub(r"\\s+", " ", name))
        if len(rows) >= limit:
            break
    return "\n".join(rows)


def _trafilatura(html: str) -> str | None:
    """Best-in-class boilerplate removal, when installed.

    Optional on purpose: the built-in density heuristic keeps the core
    stdlib-only, and a missing extra must degrade rather than crash.
    """
    try:
        import trafilatura
    except ImportError:
        return None
    try:
        out = trafilatura.extract(html, include_comments=False,
                                  include_tables=True, favor_precision=True)
    except Exception:
        return None
    return out or None


def readability(html: str) -> str:
    """The densest text block — an approximation of 'the article'.

    Picks the container with the best text-to-markup ratio rather than the
    longest, so a nav sidebar full of links does not win.
    """
    best_effort = _trafilatura(html)
    if best_effort and len(best_effort) > 80:
        return best_effort

    src = _strip_noise(html)
    # A page that marks its own main content is telling us where it is; density
    # scoring is the fallback for pages that do not.
    for tag in ("main", "article"):
        m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", src, re.I | re.S)
        if m and len(text_of(m.group(1))) > 40:
            return text_of(m.group(1))

    best, best_score = "", 0.0
    for m in re.finditer(r"<(article|main|section|div)\b[^>]*>(.*?)</\1>", src, re.I | re.S):
        block = m.group(2)
        text = text_of(block)
        if len(text) < 80:
            continue
        score = len(text) ** 1.2 / max(len(block), 1)
        if score > best_score:
            best, best_score = text, score
    return best or text_of(src)


def markdown(html: str) -> str:
    """Headings, links and list items as markdown — structure a model reads well."""
    src = _strip_noise(html)
    out = []
    for m in re.finditer(
            r"<(h[1-6])\b[^>]*>(.*?)</\1>|<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>"
            r"|<li\b[^>]*>(.*?)</li>|<p\b[^>]*>(.*?)</p>", src, re.I | re.S):
        if m.group(1):
            level = int(m.group(1)[1])
            out.append("#" * level + " " + _clean(m.group(2)))
        elif m.group(3):
            text = _clean(m.group(4))
            if text:
                out.append(f"[{text}]({m.group(3)})")
        elif m.group(5):
            out.append("- " + _clean(m.group(5)))
        elif m.group(6):
            t = _clean(m.group(6))
            if t:
                out.append(t)
    return "\n".join(x for x in out if x.strip())


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(_TAG.sub(" ", s or ""))).strip()


_DISPATCH = {
    Preprocess.RAW: lambda h: h or "",
    Preprocess.CLEAN_HTML: clean_html,
    Preprocess.TEXT: text_of,
    Preprocess.READABILITY: readability,
    Preprocess.DOM_SKELETON: dom_skeleton,
    Preprocess.INTERACTIVE: interactive,
    Preprocess.ACCESSIBILITY: accessibility,
    Preprocess.MARKDOWN: markdown,
}


def reduce(html: str, strategy: Preprocess = Preprocess.TEXT,
           max_chars: int = 0) -> Reduced:
    """Apply a strategy, optionally truncating to a character budget."""
    fn = _DISPATCH[strategy]
    content = fn(html or "")
    if max_chars and len(content) > max_chars:
        content = content[:max_chars] + f"\n…[truncated at {max_chars} chars]"
    return Reduced(strategy=strategy, content=content,
                   original_chars=len(html or ""), chars=len(content))


def recommend(question: str) -> tuple[Preprocess, ...]:
    """Strategies suited to a kind of question, best first."""
    return _SUITED.get(question, (Preprocess.TEXT, Preprocess.MARKDOWN))


def compare(html: str, strategies=None) -> list[Reduced]:
    """Run several strategies over one page — how you pick one with evidence."""
    return [reduce(html, s) for s in (strategies or list(Preprocess))]
