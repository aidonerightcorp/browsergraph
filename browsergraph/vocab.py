"""What a step needs, named so two people can mean the same thing.

Sixty-four capability names are already in use across the templates and packs
here — `data.clean`, `check.schema`, `feature.numeric` — and nineteen of them
appear in more than one place. So a vocabulary exists. What did not exist is
anything that *knows* it: no aliases, no way to ask "what provides imputation",
no way to tell a capability nobody implements from one nobody has needed yet.

That gap has a concrete cost. `edits.question` asks a model to propose a
structural change and tells it to name a capability rather than invent a node
id, because a hallucinated id is the failure that reads like a framework bug.
But nothing could resolve a capability back to nodes, so the prompt had to hand
over concrete ids anyway and the advice was aspirational.

This closes that loop:

    from browsergraph import vocab

    vocab.find("fill in missing values")          # capabilities, best first
    vocab.resolve("data.clean", bench)            # nodes, with legal positions
    vocab.gaps()                                  # what nothing here provides

Named `vocab` and not `capabilities` because that module already exists and
means something else — what a *browser engine* can do. Two registries called
capabilities, one about engines and one about pipeline steps, is a collision
waiting for whoever greps next.

Three decisions worth stating, because each is a place this could have gone
wrong.

**Implementations are discovered, never declared.** `providers()` reads the
packs that actually exist. A table listing what it believed implemented a
capability would drift the first time a node was deleted, and drift silently.

**Resolution is filtered by the type checker, never by the description.** You
may search by meaning; the survivors are whatever compiles at the position
asked about. The other order produces a plausible node that does not fit, which
costs more than finding nothing.

**A capability nobody implements is recorded, not hidden.** `gaps()` is the
honest inventory: obligations templates declare that no pack can currently
fill. Knowing which is more useful than a vocabulary listing only what works.

The seed vocabulary is deliberately small and deliberately not a taxonomy of
everything. A name earns its place by being used by a template or a pack, or by
being the word somebody would search to find one.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

#: capability id -> the words somebody would actually search with. Aliases are
#: for finding, never for deciding: two capabilities sharing an alias is a
#: search collision, not a claim that they are interchangeable.
ALIASES: dict[str, tuple[str, ...]] = {
    "data.read":        ("load", "ingest", "read", "import", "acquire", "fetch"),
    "data.clean":       ("clean", "repair", "impute", "fill", "missing", "tidy",
                         "wrangle", "sanitise", "sanitize"),
    "data.split":       ("split", "holdout", "fold", "train", "test", "partition"),
    "data.profile":     ("profile", "describe", "summarise", "summarize",
                         "statistics", "eda"),
    "data.lookup":      ("lookup", "join", "enrich", "reference", "resolve"),
    "check.schema":     ("schema", "validate", "contract", "types", "columns"),
    "check.distribution": ("distribution", "drift", "outlier", "anomaly",
                           "range", "spread"),
    "gate.decide":      ("gate", "adjudicate", "decide", "verdict", "approve",
                         "accept", "reject"),
    "feature.numeric":  ("scale", "standardise", "standardize", "normalise",
                         "normalize", "numeric", "continuous"),
    "feature.categorical": ("encode", "categorical", "onehot", "ordinal",
                            "dummies"),
    "feature.assemble": ("assemble", "combine", "concatenate", "matrix"),
    "model.fit":        ("fit", "train", "estimator", "regressor", "classifier",
                         "learn"),
    "model.calibrate":  ("calibrate", "probability", "isotonic", "platt"),
    "model.evaluate":   ("evaluate", "score", "metric", "measure"),
    "io.list":          ("list", "glob", "enumerate", "discover"),
    "work.one":         ("parse", "item", "map", "each"),
    "split.outcome":    ("outcome", "quarantine", "reject", "partition"),
    "summarise":        ("summarise", "summarize", "aggregate", "total",
                         "report"),
    # The gaps need aliases *more* than the implemented ones, not less: nobody
    # searching for "remove duplicates" will type `data.deduplicate`, and a gap
    # nobody can find is a gap that stays open. Leaving these out is how the
    # first version returned nothing for "removes duplicates".
    "data.deduplicate": ("duplicate", "duplicates", "dedupe", "deduplicate",
                         "distinct", "unique"),
    "data.impute":      ("impute", "imputation", "missing", "fill", "nan",
                         "null"),
    "feature.select":   ("select", "selection", "prune", "subset", "importance"),
    "feature.reduce":   ("reduce", "dimensionality", "pca", "compress",
                         "projection"),
    "model.explain":    ("explain", "attribution", "shap", "importance",
                         "interpret"),
    "model.uncertainty": ("uncertainty", "confidence", "interval", "conformal",
                          "probability"),
    "data.balance":     ("balance", "imbalance", "oversample", "undersample",
                         "smote", "resample"),
    "text.vectorise":   ("text", "vectorise", "vectorize", "tfidf", "embedding",
                         "tokenise", "tokenize"),
    "time.lag":         ("lag", "rolling", "window", "temporal", "seasonal",
                         "lookback"),
}

#: Words that appear in every phrasing and distinguish nothing.
STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "that", "this", "it", "is", "are", "be", "by", "as", "at", "from", "into",
    "some", "any", "all", "my", "our", "we", "should", "would", "can", "need",
    "want", "add", "use", "step", "node", "graph", "data",
})

#: Capabilities worth naming even though nothing here implements them, and the
#: honest reason each is listed. A vocabulary containing only what is built
#: cannot express a gap, and a gap nobody can express is a gap nobody fills.
WANTED: dict[str, str] = {
    "data.deduplicate": "find and resolve duplicate or near-duplicate records",
    "data.impute":      "fill missing values as a step of its own, separable "
                        "from general cleaning",
    "feature.select":   "choose a subset of features on evidence rather than "
                        "using every column",
    "feature.reduce":   "reduce dimensionality while keeping what matters",
    "model.explain":    "attribute a prediction to its inputs",
    "model.uncertainty": "say how confident a prediction is, not only what it is",
    "data.balance":     "handle a target whose classes are badly uneven",
    "text.vectorise":   "turn text into something a model can consume",
    "time.lag":         "build features from earlier values without leaking "
                        "later ones",
}


@dataclass(frozen=True)
class Capability:
    """One named obligation, and where it is spoken."""
    id: str
    aliases: tuple[str, ...] = ()
    #: Templates declaring a slot that needs this. Discovered at call time.
    templates: tuple[str, ...] = ()
    #: Packs containing a step that requires it. Likewise discovered.
    packs: tuple[str, ...] = ()
    note: str = ""

    @property
    def implemented(self) -> bool:
        """Does anything here actually provide it?

        A template declaring the obligation is not an implementation, and that
        distinction is the whole value of this field: a shape somebody
        described before anybody built it is a gap, not a capability.
        """
        return bool(self.packs)

    def describe(self) -> str:
        # Provision is stated on every line, never implied by the presence of
        # other facts. An earlier version printed "1 template(s)" for a
        # capability nothing could perform and said nothing else — leaving a
        # reader unable to tell it apart from one that works, which is the
        # ambiguity this module exists to remove.
        where = ([f"packs: {', '.join(self.packs)}"] if self.packs
                 else ["nothing provides it"])
        if self.templates:
            where.append(f"declared by {len(self.templates)} template(s)")
        return f"{self.id:<20} {self.note or '—'}   [{'; '.join(where)}]"


def _from_templates() -> dict[str, set[str]]:
    from browsergraph import templates

    out: dict[str, set[str]] = {}
    for domain in templates.domains():
        for template in templates.by_domain(domain):
            for slot in template.slots:
                for capability in slot.capabilities:
                    out.setdefault(capability, set()).add(template.id)
    return out


def _from_packs() -> dict[str, set[str]]:
    from browsergraph import packs

    out: dict[str, set[str]] = {}
    for name in packs.available():
        for stage in packs.get(name).workbench().leaf_stages:
            for capability in stage.required_capabilities:
                out.setdefault(capability, set()).add(name)
    return out


def registry() -> dict[str, Capability]:
    """Every capability name in play, with where it is used.

    Built on each call from the templates and packs that exist right now.
    Slower than a cached table, and correct in the way that matters: it cannot
    claim something is implemented after the node providing it is gone.
    """
    in_templates, in_packs = _from_templates(), _from_packs()
    names = set(in_templates) | set(in_packs) | set(ALIASES) | set(WANTED)
    return {name: Capability(id=name,
                             aliases=ALIASES.get(name, ()),
                             templates=tuple(sorted(in_templates.get(name, ()))),
                             packs=tuple(sorted(in_packs.get(name, ()))),
                             note=WANTED.get(name, ""))
            for name in sorted(names)}


def find(query: str, limit: int = 8) -> list[Capability]:
    """Capabilities matching a phrase, best first.

    Scored on exact id, then substring, then alias overlap. Not an embedding:
    this vocabulary has dozens of entries and lexical matching over dozens is
    exact, explainable and instant. When it reaches thousands, the *ranking* is
    the part to replace — the filtering after it must not change, because
    meaning may rank and only types may decide.
    """
    # Short filler words match everything and rank nothing. "fill in the
    # missing values" scored `act.explain` above `data.clean` until these were
    # dropped, purely because "in" is a substring of half the vocabulary.
    words = {w for w in query.lower().replace("_", " ").replace(".", " ").split()
             if w and w not in STOPWORDS and len(w) > 2}
    scored: list[tuple[float, Capability]] = []

    for capability in registry().values():
        name = capability.id.lower()
        score = 0.0
        if name == query.lower():
            score += 100.0
        elif query.lower() in name:
            score += 20.0
        for word in words:
            if word in name:
                score += 5.0
            for alias in capability.aliases:
                if word == alias:
                    score += 8.0
                elif len(word) > 3 and (word in alias or alias in word):
                    score += 3.0
        if capability.note and words & set(capability.note.lower().split()):
            score += 1.0
        if score:
            # At equal relevance an implemented capability outranks an
            # aspirational one: a searcher wants something usable.
            scored.append((score + (0.5 if capability.implemented else 0.0),
                           capability))

    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [capability for _score, capability in scored[:limit]]


def providers(capability: str, library: Sequence[Any] = ()) -> list[Any]:
    """Node manifests declaring this capability.

    Searches the packs' own nodes plus any library given. Declaring a capability
    is a claim about *purpose*; whether a node fits a particular position is a
    different question, and `resolve` is what answers it.
    """
    import importlib

    from browsergraph import packs

    found: dict[str, Any] = {}
    for name in packs.available():
        module = importlib.import_module(f"browsergraph.packs.{name}")
        for manifest in getattr(module, "NODES", ()):
            if capability in manifest.capabilities:
                found[manifest.id] = manifest
    for manifest in library:
        if capability in getattr(manifest, "capabilities", ()):
            found[manifest.id] = manifest
    return sorted(found.values(), key=lambda m: m.id)


def resolve(capability: str, bench, *, library: Sequence[Any] = ()
            ) -> list[tuple[Any, list[tuple[str, str]]]]:
    """Nodes providing this capability, each with the places it legally fits.

    The point of the module, and the order is the point of the order: find by
    meaning, then let the compiler decide. A node providing `data.clean` whose
    types meet the graph nowhere comes back with an empty position list rather
    than being offered as a candidate.

    Returns `(manifest, [(after, before), ...])`, so a caller can show a model
    real ids at real positions — which is what stops it inventing either.
    """
    from browsergraph import edits

    out = [(manifest, edits.insertion_points(bench, manifest))
           for manifest in providers(capability, library)]
    out.sort(key=lambda pair: (-len(pair[1]), pair[0].id))
    return out


def gaps() -> list[Capability]:
    """Capabilities something declares and nothing here can perform.

    The honest inventory. A template naming an obligation no pack can fill is
    not a broken template — it is a shape described before it was built, which
    is the correct order. Recording it is how the next person knows what is
    worth writing.
    """
    return [c for c in registry().values()
            if not c.implemented and (c.templates or c.note)]


def catalog_text() -> str:
    """The vocabulary, with provided and merely-declared kept apart."""
    every = registry()
    built = [c for c in every.values() if c.implemented]
    missing = gaps()
    lines = [f"{len(every)} capability names — {len(built)} provided, "
             f"{len(missing)} declared with nothing to perform them", "",
             "PROVIDED:"]
    lines += [f"  {c.describe()}" for c in built]
    if missing:
        lines += ["", "DECLARED BUT NOT PROVIDED:"]
        lines += [f"  {c.describe()}" for c in missing]
    return "\n".join(lines)
