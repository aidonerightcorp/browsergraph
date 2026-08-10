"""Every other way a node can be described — open-ended, and still checkable.

`NodeManifest` carries the *spine*: identity, ports, capabilities, effects. That
part is closed on purpose, because it is what legality is decided on and a
vocabulary you can extend at will is a vocabulary you cannot check.

Everything else about a node is open. What problem it solves, what it is for,
which verbs it performs, what its inputs mean in prose, how it tends to fail,
what it costs, who wrote it, which paper it implements — the list has no natural
end, and any fixed set of columns picked today is wrong by next month. So
descriptors live in a `facets` map with no fixed key space.

The obvious objection is that an open map is a swamp: nothing can search what
nothing understands. The answer here is that a facet becomes searchable when
something declares **how** — a `FacetSpec` saying this facet is free text, or a
keyword set, or a number, or a vector. Specs travel *with* the pack that
introduces the facets, so a node repository can invent descriptors this code has
never heard of and still be searched properly by a harness that has never heard
of them either.

The ordering that keeps this honest:

    types bind, facets rank.

Ports, effects and permissions decide whether a node is *legal* in a position —
exactly, cheaply, with no model involved. Facets and their embeddings decide
which of the legal candidates is *preferable*. A search may never return an
illegal node because an embedding liked it. That separation is also what makes a
model-free tier possible: legality alone narrows most positions to a handful.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: `group.name`, lowercase. Namespacing is what lets two packs both describe
#: "cost" without colliding, and what makes an unknown facet's origin guessable.
FACET_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

#: How a facet may be searched. The kind is the whole contract: an indexer that
#: has never seen the facet name still knows what to do with it.
#:
#: * ``text``    — prose. Tokenised, ranked lexically, and embeddable.
#: * ``keyword`` — a set of exact labels. Filtered by overlap, never analysed.
#: * ``number``  — ordered. Filtered by range, compared.
#: * ``bool``    — a flag.
#: * ``json``    — carried and shown, but not indexed. The honest home for
#:                 structure nobody has decided how to search yet.
KINDS = ("text", "keyword", "number", "bool", "json")

#: Which kinds an embedding may be computed from. Embedding a keyword set is
#: possible and nearly always worse than matching it exactly.
EMBEDDABLE = ("text",)


@dataclass(frozen=True)
class FacetSpec:
    """One declared descriptor, and how to search it.

    `weight` is a *prior* on usefulness for ranking, not a measurement. A pack
    saying its `purpose.statement` matters more than its `provenance.author` is
    almost always right, and a search that ignored the pack's opinion would
    rediscover it slowly and expensively.
    """
    name: str
    kind: str = "text"
    description: str = ""
    weight: float = 1.0
    #: Advisory: this facet is worth its own embedding, kept separate from the
    #: others so a query about *outputs* is not answered by a match on purpose.
    embed: bool = False
    #: Free-form; an indexer may honour it or ignore it.
    options: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        bad = []
        if not FACET_RE.match(self.name or ""):
            bad.append(f"facet name {self.name!r} must be lowercase and "
                       f"namespaced, e.g. 'purpose.statement'")
        if self.kind not in KINDS:
            bad.append(f"facet {self.name!r} has unknown kind {self.kind!r} "
                       f"(known: {', '.join(KINDS)})")
        if self.embed and self.kind not in EMBEDDABLE:
            bad.append(f"facet {self.name!r} asks to be embedded but is "
                       f"{self.kind!r}; only {', '.join(EMBEDDABLE)} embeds well")
        if self.weight < 0:
            bad.append(f"facet {self.name!r} has negative weight")
        return bad

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"name": self.name, "kind": self.kind}
        if self.description:
            out["description"] = self.description
        if self.weight != 1.0:
            out["weight"] = self.weight
        if self.embed:
            out["embed"] = True
        if self.options:
            out["options"] = dict(self.options)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FacetSpec:
        return cls(name=data.get("name", ""), kind=data.get("kind", "text"),
                   description=data.get("description", ""),
                   weight=float(data.get("weight", 1.0)),
                   embed=bool(data.get("embed", False)),
                   options=dict(data.get("options") or {}))


#: A starter vocabulary. Not a schema and not a requirement — no node needs any
#: of these, and a pack may replace the lot. They exist so that the common
#: descriptors mean the same thing across packs written by people who never
#: spoke, which is the only reason a shared vocabulary is ever worth having.
WELL_KNOWN: tuple[FacetSpec, ...] = (
    FacetSpec("purpose.statement", "text", weight=2.0, embed=True,
              description="One sentence, imperative: what this node is for."),
    FacetSpec("purpose.solves", "keyword", weight=1.5,
              description="Problem phrases it addresses — 'missing values', "
                          "'class imbalance', 'rate limiting'."),
    FacetSpec("purpose.not_for", "keyword",
              description="Where it is the wrong tool. Cheap to write, and it "
                          "prevents more bad matches than another synonym."),
    FacetSpec("action.verbs", "keyword", weight=1.2,
              description="What it does: impute, scale, join, click, retry."),
    FacetSpec("domain.tags", "keyword", weight=1.2,
              description="tabular, timeseries, text, vision, web, infra."),
    FacetSpec("io.input_desc", "text", embed=True,
              description="What arrives, in prose. Ports say the type; this "
                          "says what the type means here."),
    FacetSpec("io.output_desc", "text", embed=True,
              description="What leaves, in prose."),
    FacetSpec("method.name", "keyword",
              description="The named technique — 'target encoding', 'XGBoost', "
                          "'exponential backoff'."),
    FacetSpec("method.reference", "text",
              description="Paper, RFC or documentation URL."),
    FacetSpec("failure.modes", "text",
              description="How it goes wrong, and what that looks like."),
    FacetSpec("precondition.text", "text",
              description="What must already be true. Prose; the checkable form "
                          "belongs on ports and effects."),
    FacetSpec("postcondition.text", "text",
              description="What is true afterwards."),
    FacetSpec("cost.usd_per_call", "number",
              description="Marginal money. 0 is a claim worth making explicitly."),
    FacetSpec("cost.latency_ms", "number", description="Typical wall-clock."),
    FacetSpec("quality.prior", "number",
              description="Believed success rate before any receipt exists. A "
                          "prior, and labelled as one."),
    FacetSpec("maturity.level", "keyword",
              description="experimental | usable | proven | deprecated."),
    FacetSpec("provenance.author", "keyword", description="Who wrote it."),
    FacetSpec("provenance.generated_by", "keyword",
              description="Model id, when a node was written by one. Nodes that "
                          "wrote themselves should say so."),
    FacetSpec("license.spdx", "keyword", description="SPDX identifier."),
)

WELL_KNOWN_BY_NAME: dict[str, FacetSpec] = {s.name: s for s in WELL_KNOWN}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [f"{k} {v}" for k, v in value.items()]
    if isinstance(value, Sequence):
        return [str(v) for v in value]
    return [str(value)]


def normalize(value: Any, kind: str) -> Any:
    """Coerce a facet value into the shape its kind promises.

    Lenient by design. A `keyword` facet given a single string means one
    keyword; given a comma-joined string it means several. Rejecting those would
    only teach authors that facets are fiddly, and an author who finds
    descriptors fiddly writes none.
    """
    if kind == "keyword":
        out: list[str] = []
        for item in _as_list(value):
            out += [p.strip() for p in item.split(",") if p.strip()]
        return tuple(dict.fromkeys(out))
    if kind == "text":
        return " ".join(_as_list(value)).strip()
    if kind == "number":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    return value


def validate(facets: Mapping[str, Any],
             specs: Mapping[str, FacetSpec] | None = None) -> list[str]:
    """Problems with a facet map. An *unknown* facet is never a problem.

    That asymmetry is deliberate and is the whole forward-compatibility story: a
    reader that rejected descriptors it did not recognise would make every pack
    that innovates unreadable by every harness that has not caught up. What is
    checked is the part that was declared — a facet whose spec says `number` and
    whose value is "quite fast" is a real error, because something will try to
    compare it.
    """
    known = dict(WELL_KNOWN_BY_NAME)
    known.update(specs or {})
    bad = []
    for name, value in facets.items():
        if not FACET_RE.match(name):
            bad.append(f"facet key {name!r} must be lowercase and namespaced, "
                       f"e.g. 'purpose.statement'")
            continue
        spec = known.get(name)
        if spec is None:
            continue                      # unknown, carried, not judged
        if spec.kind == "number" and normalize(value, "number") is None:
            bad.append(f"facet {name!r} is declared 'number' but holds "
                       f"{value!r}")
        if spec.kind == "text" and not isinstance(value, (str, list, tuple)):
            bad.append(f"facet {name!r} is declared 'text' but holds "
                       f"{type(value).__name__}")
    return bad


def specs_for(facets: Mapping[str, Any],
              declared: Iterable[FacetSpec] = ()) -> dict[str, FacetSpec]:
    """The spec for every facet present, inventing plausible ones as needed.

    A facet nobody declared still gets searched — as text, at low weight. The
    alternative is that descriptors a pack invented are invisible until someone
    edits a central table, which is precisely the closed-registry failure this
    module exists to avoid. Guessing `text` is the cautious choice: it is the
    kind that degrades most gracefully when wrong.
    """
    out = {s.name: s for s in declared}
    for name, value in facets.items():
        if name in out:
            continue
        if name in WELL_KNOWN_BY_NAME:
            out[name] = WELL_KNOWN_BY_NAME[name]
        else:
            kind = ("keyword" if isinstance(value, (list, tuple))
                    else "number" if isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    else "bool" if isinstance(value, bool)
                    else "text" if isinstance(value, str) else "json")
            out[name] = FacetSpec(name, kind, weight=0.5,
                                  description="undeclared; inferred")
    return out


def fields(facets: Mapping[str, Any],
           specs: Mapping[str, FacetSpec] | None = None
           ) -> dict[str, tuple[str, float]]:
    """The searchable text of a node, per facet, with its weight.

    Returned per facet rather than concatenated, because keeping them apart is
    the point: a query about what a node *outputs* should be answered by
    `io.output_desc`, and a single blob makes that impossible.
    """
    known = specs_for(facets, (specs or {}).values())
    out: dict[str, tuple[str, float]] = {}
    for name, value in facets.items():
        spec = known.get(name)
        if spec is None or spec.kind in ("json", "bool", "number"):
            continue
        text = (" ".join(normalize(value, "keyword")) if spec.kind == "keyword"
                else normalize(value, "text"))
        if text:
            out[name] = (text, spec.weight)
    return out


def keywords(facets: Mapping[str, Any],
             specs: Mapping[str, FacetSpec] | None = None) -> dict[str, tuple]:
    """Every keyword facet, normalised — the part matched exactly rather than ranked."""
    known = specs_for(facets, (specs or {}).values())
    return {name: normalize(value, "keyword")
            for name, value in facets.items()
            if known.get(name) is not None and known[name].kind == "keyword"}


def merge(base: Mapping[str, Any], *overlays: Mapping[str, Any]) -> dict[str, Any]:
    """Later facets win. Keyword facets union rather than replace.

    Unioning keywords matters when a pack adds domain tags to a node it did not
    write: replacing would silently drop the original author's tags, and the
    node would stop being findable the way it used to be.
    """
    out = dict(base)
    for overlay in overlays:
        for name, value in overlay.items():
            spec = WELL_KNOWN_BY_NAME.get(name)
            if spec is not None and spec.kind == "keyword" and name in out:
                out[name] = tuple(dict.fromkeys(
                    tuple(normalize(out[name], "keyword"))
                    + tuple(normalize(value, "keyword"))))
            else:
                out[name] = value
    return out
