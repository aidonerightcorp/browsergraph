"""Token reduction: chunk, search, expand, fit.

Preprocessing decides *how* a page is represented; this decides *which parts*
survive. On a long page the answer usually occupies a few hundred characters,
and sending the other 200k is pure cost.

The pipeline is deliberately deterministic — chunk, score against the question,
keep the best chunks plus their neighbours, drop cross-page boilerplate, fit to
a budget. No model is involved in deciding what the model sees, which keeps the
reduction reproducible and debuggable.

Neighbour expansion matters more than it looks: the sentence that *matches*
"contact" is rarely the one holding the phone number — that sits in the block
after it. Retrieving a hit without its neighbours is how keyword search
produces confident, empty answers.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

_WORD = re.compile(r"[a-z0-9][a-z0-9'+-]{1,}", re.I)
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.M)

_STOP = frozenset("""a an and are as at be by for from has have he in is it its of on
that the to was were will with this these those or if then than but not you your we
our us they them their i me my""".split())


def tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "") if w.lower() not in _STOP]


# --- chunking ---------------------------------------------------------------

@dataclass
class Chunk:
    index: int
    text: str
    heading: str = ""
    score: float = 0.0
    matched: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return len(self.text)

    def to_dict(self) -> dict:
        return {"index": self.index, "heading": self.heading,
                "chars": self.chars, "score": round(self.score, 3),
                "matched": self.matched[:6]}


def chunk(text: str, max_chars: int = 1200, overlap: int = 120) -> list[Chunk]:
    """Split on headings first, then size — so a chunk is a coherent section.

    Splitting purely by size cuts mid-sentence and separates a heading from the
    content it introduces, which destroys exactly the context that makes a
    chunk retrievable.
    """
    text = (text or "").strip()
    if not text:
        return []

    sections: list[tuple[str, str]] = []
    marks = list(_HEADING.finditer(text))
    if marks:
        if marks[0].start() > 0:
            sections.append(("", text[: marks[0].start()]))
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            sections.append((m.group(2).strip(), text[m.end():end]))
    else:
        for block in re.split(r"\n\s*\n", text):
            sections.append(("", block))

    out: list[Chunk] = []
    for heading, body in sections:
        body = body.strip()
        if not body:
            continue
        if len(body) <= max_chars:
            out.append(Chunk(index=len(out), text=body, heading=heading))
            continue
        start = 0
        while start < len(body):
            end = min(start + max_chars, len(body))
            if end < len(body):                       # avoid cutting mid-sentence
                dot = body.rfind(". ", start + max_chars // 2, end)
                if dot > 0:
                    end = dot + 1
            out.append(Chunk(index=len(out), text=body[start:end].strip(),
                             heading=heading))
            if end >= len(body):
                break
            start = max(start + 1, end - overlap)
    return [c for c in out if c.text]


# --- scoring ----------------------------------------------------------------

def score(chunks: list[Chunk], query: str) -> list[Chunk]:
    """BM25-lite relevance. Idf keeps common words from dominating."""
    terms = tokenize(query)
    if not terms or not chunks:
        return chunks

    docs = [Counter(tokenize(c.text + " " + c.heading)) for c in chunks]
    n = len(chunks)
    df: Counter[str] = Counter()
    for d in docs:
        for t in set(terms):
            if d.get(t):
                df[t] += 1
    avg_len = sum(sum(d.values()) for d in docs) / max(n, 1) or 1.0
    k1, b = 1.5, 0.75

    for c, d in zip(chunks, docs, strict=True):
        length = sum(d.values()) or 1
        total, hits = 0.0, []
        for t in set(terms):
            tf = d.get(t, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            total += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * length / avg_len))
            hits.append(t)
        # a heading match is a strong signal about what the section is about
        if c.heading and any(t in c.heading.lower() for t in terms):
            total *= 1.5
            hits.append(f"heading:{c.heading[:24]}")
        c.score, c.matched = total, hits
    return chunks


# --- selection --------------------------------------------------------------

@dataclass
class Focused:
    content: str
    kept: list[int]
    original_chars: int
    chars: int
    chunks_total: int
    query: str = ""

    @property
    def saved_pct(self) -> float:
        return 100.0 * (1 - self.chars / self.original_chars) if self.original_chars else 0.0

    def to_dict(self) -> dict:
        return {"query": self.query, "chunks_total": self.chunks_total,
                "chunks_kept": len(self.kept), "kept": self.kept,
                "original_chars": self.original_chars, "chars": self.chars,
                "saved_pct": round(self.saved_pct, 1)}


def select(chunks: list[Chunk], query: str, budget: int = 6000,
           neighbors: int = 1, min_score: float = 0.0) -> Focused:
    """Best-scoring chunks plus their neighbours, fitted to a character budget.

    Chunks are emitted in document order, not score order — a model reading
    them out of sequence loses the narrative, and adjacent chunks stop reading
    as continuous text.
    """
    original = sum(c.chars for c in chunks)
    if not chunks:
        return Focused("", [], 0, 0, 0, query)

    ranked = sorted(score(chunks, query), key=lambda c: c.score, reverse=True)
    keep: set[int] = set()
    used = 0

    for c in ranked:
        if c.score <= min_score and keep:
            break
        # The matching chunk goes in first and on its own budget check, so a
        # large neighbour can never crowd out the chunk that actually matched.
        if c.index not in keep:
            if used + c.chars > budget and keep:
                continue
            keep.add(c.index)
            used += c.chars
        for i in range(max(0, c.index - neighbors),
                       min(len(chunks), c.index + neighbors + 1)):
            if i in keep:
                continue
            if used + chunks[i].chars > budget:
                continue
            keep.add(i)
            used += chunks[i].chars
        if used >= budget:
            break

    if not keep:                                   # nothing matched: head of document
        for c in chunks:
            if used + c.chars > budget:
                break
            keep.add(c.index)
            used += c.chars

    ordered = sorted(keep)
    parts, prev = [], None
    for i in ordered:
        if prev is not None and i != prev + 1:
            parts.append("…")                      # mark the gap, do not hide it
        c = chunks[i]
        parts.append((f"## {c.heading}\n" if c.heading else "") + c.text)
        prev = i
    content = "\n\n".join(parts)
    return Focused(content, ordered, original, len(content), len(chunks), query)


# --- boilerplate ------------------------------------------------------------

def strip_boilerplate(pages: list[str], threshold: float = 0.6,
                      min_pages: int = 3) -> list[str]:
    """Remove blocks that appear on most pages — nav, footer, cookie banners.

    Only applied with enough pages to judge; on two pages a repeated block may
    simply be the content.
    """
    if len(pages) < min_pages:
        return pages
    blocks = [[b.strip() for b in re.split(r"\n\s*\n", p or "") if b.strip()]
              for p in pages]
    freq: Counter[str] = Counter()
    for bl in blocks:
        for b in set(bl):
            freq[b] += 1
    cutoff = max(min_pages - 1, int(len(pages) * threshold))
    common = {b for b, n in freq.items() if n >= cutoff and len(b) < 2000}
    return ["\n\n".join(b for b in bl if b not in common) for bl in blocks]


# --- entry point ------------------------------------------------------------

def focus(text: str, query: str, budget: int = 6000, chunk_chars: int = 1200,
          neighbors: int = 1) -> Focused:
    """Chunk, score, expand and fit in one call."""
    return select(chunk(text, max_chars=chunk_chars), query,
                  budget=budget, neighbors=neighbors)


def estimate_tokens(text: str) -> int:
    """~4 chars per token. Rough, but enough to compare strategies."""
    return max(1, len(text or "") // 4)
