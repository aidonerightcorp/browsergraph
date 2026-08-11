"""Changing the graph itself, safely.

Everything else in this library chooses *within* a graph: a route names one
candidate per stage, and the search ranges over those names. The shape is fixed
before any of it starts, by whoever wrote the stages down.

That is the ceiling. A search cannot add the imputation step nobody thought of,
cannot notice that two steps could run at once, and cannot discover that the
missing validator was the whole problem. No budget helps, because the better
graph is not in the space being searched.

This module makes the shape searchable too. An **edit** is one structural
change — insert a step here, remove that one, make this a map — and applying it
produces a *new workbench*, which the ordinary machinery then treats like any
other: validate it, compile routes through it, run them, compare.

    from browsergraph import edits

    edit = edits.GraphEdit("insert", where=("clean", "encode"), what="impute.median")
    outcome = edits.apply(bench, edit, library=MY_NODES)
    if outcome.ok:
        better = outcome.workbench
    else:
        print(outcome.reason)          # kept, because refusals are the metric

Three rules, and they are the whole safety argument:

**Nothing is applied in place.** `apply` returns a new workbench or a reason. A
proposal that would corrupt the graph you already have is not a proposal, it is
an accident waiting for a caller who forgot to check.

**Every edit is validated before it is offered.** An inserted step whose ports
do not meet its new neighbours is refused with the type error attached, not
returned for somebody downstream to discover.

**A refusal is a result.** `Outcome.reason` carries why, in the reader's terms.
When a model is proposing these — which is the point — the refusal rate is the
only honest measure of whether it is helping, and refusals thrown away make
every model look equally good.

The vocabulary is deliberately small. Five kinds, each of which can be checked:

| kind | what it does |
|---|---|
| `insert` | put a new step between two existing ones |
| `remove` | lift a step out and reconnect around it |
| `widen` | let a step also use nodes it does not currently admit |
| `make_map` | turn an atomic step into one that runs per item |
| `make_branch` | turn an atomic step into one that takes a path |

`widen` and not `replace`, and the difference is not cosmetic. A stage must
admit **every** candidate compatible with it — that invariant is checked by
`validate()` and it is what stops a graph quietly hiding an option from the
search. So narrowing a step's candidate list is not an edit anybody may make;
it is what a *policy* does, at gate time, with a reason attached. An edit can
only ever add. Writing the test for `replace` is how that was discovered.

Things like "split this step in two" are absent on purpose: there is no single
correct way to do it, so an edit that claimed to would be inventing a graph
nobody wrote.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from browsergraph.manifest import NodeManifest, PortSpec
from browsergraph.workbench import (
    Edge,
    NodeCandidate,
    StageDefinition,
    WorkbenchDefinition,
)

KINDS = ("insert", "remove", "widen", "make_map", "make_branch")


@dataclass(frozen=True)
class GraphEdit:
    """One structural change, proposed. Not yet applied, and possibly refused."""
    kind: str
    #: For `insert`, the pair it goes between. For everything else, one step id.
    where: Any = ""
    #: A node id, or several for `widen`. For `insert`, the node that fills
    #: the new step — which is why a proposer should name a *capability* and let
    #: an index resolve it, rather than inventing an id.
    what: Any = ""
    why: str = ""
    #: Advisory only, and never consulted by anything that decides legality.
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {"kind": self.kind, "where": self.where, "what": self.what,
                "why": self.why, "confidence": round(self.confidence, 4)}

    def describe(self) -> str:
        if self.kind == "insert":
            after, before = self.where
            return f"insert {self.what} between {after} and {before}"
        return f"{self.kind} {self.where}" + (f" -> {self.what}" if self.what else "")


@dataclass
class Outcome:
    """What became of an edit. A refusal is as much a result as a success."""
    edit: GraphEdit
    ok: bool = False
    reason: str = ""
    workbench: WorkbenchDefinition | None = None

    def to_dict(self) -> dict:
        return {"edit": self.edit.to_dict(), "ok": self.ok, "reason": self.reason}


def _stage_id_for(bench: WorkbenchDefinition, node_id: str) -> str:
    """A readable, unused stage id derived from the node going into it."""
    base = node_id.split(".")[0] or "step"
    taken = {s.id for s in bench.stages}
    if base not in taken:
        return base
    for suffix in range(2, 99):
        if f"{base}{suffix}" not in taken:
            return f"{base}{suffix}"
    return f"{base}.{len(taken)}"


def _validated(bench: WorkbenchDefinition, edit: GraphEdit) -> Outcome:
    problems = bench.validate()
    if problems:
        return Outcome(edit, False, "; ".join(problems[:3]))
    return Outcome(edit, True, "", bench)


def apply(bench: WorkbenchDefinition, edit: GraphEdit, *,
          library: Sequence[NodeManifest] = ()) -> Outcome:
    """Apply one edit and return a new workbench, or a reason it was refused.

    `library` is where an inserted node comes from. Nodes already on the
    workbench are searched first, so a proposer can reuse what is there; the
    library is for the case this module exists to serve, which is adding
    something the graph does not have yet.
    """
    if edit.kind not in KINDS:
        return Outcome(edit, False,
                       f"unknown edit kind {edit.kind!r}; known: {', '.join(KINDS)}")
    handler = {"insert": _insert, "remove": _remove, "widen": _widen,
               "make_map": _make_map, "make_branch": _make_branch}[edit.kind]
    try:
        return handler(bench, edit, library)
    except Exception as problem:            # noqa: BLE001 - reported, not raised
        # A malformed proposal is a refusal, not a crash. These arrive from
        # models, and one bad field should not end a search.
        return Outcome(edit, False, f"could not apply: {problem}")


def apply_all(bench: WorkbenchDefinition, proposals: Sequence[GraphEdit], *,
              library: Sequence[NodeManifest] = ()) -> list[Outcome]:
    """Each edit against the *original* graph, independently.

    Deliberately not a fold. Applying edits in sequence makes the second one's
    legality depend on the first, so a good proposal can be refused because a
    bad one went ahead of it — and the refusal rate then measures the ordering
    rather than the proposer.
    """
    return [apply(bench, edit, library=library) for edit in proposals]


# --- the five kinds ---------------------------------------------------------

def _insert(bench, edit, library) -> Outcome:
    after, before = edit.where
    ids = {s.id for s in bench.stages}
    if after not in ids or before not in ids:
        return Outcome(edit, False,
                       f"insert names {after!r} and {before!r}; the graph has "
                       f"{sorted(ids)}")

    edge = next((e for e in bench.wiring()
                 if e.source == after and e.target == before), None)
    if edge is None:
        return Outcome(edit, False,
                       f"nothing connects {after} to {before}, so there is no "
                       f"place between them to insert into")

    wanted = [edit.what] if isinstance(edit.what, str) else list(edit.what)
    known = {n.id: n for n in (*bench.nodes, *library)}
    missing = [w for w in wanted if w not in known]
    if missing:
        # The failure mode that reads like a framework bug: a model names a
        # node that does not exist and the graph silently omits a step.
        return Outcome(edit, False,
                       f"no node called {missing[0]!r} — propose a capability "
                       f"and let the index resolve it, rather than an id")

    manifests = [known[w] for w in wanted]
    capabilities = tuple(dict.fromkeys(c for m in manifests
                                       for c in m.capabilities))
    first = manifests[0]
    stage_id = _stage_id_for(bench, first.id)
    fresh = StageDefinition(
        id=stage_id, name=f"Inserted {first.id}",
        description=edit.why or f"inserted by an edit: {first.description}",
        required_capabilities=capabilities,
        inputs=tuple(PortSpec(p.name, p.type) for p in first.inputs),
        outputs=tuple(PortSpec(p.name, p.type) for p in first.outputs),
        success=f"{stage_id} produced its declared output",
        candidates=tuple(w for w in wanted))

    rewired = [e for e in bench.wiring() if e is not edge]
    rewired += [Edge(source=after, target=stage_id, from_port=edge.from_port),
                Edge(source=stage_id, target=before, to_port=edge.to_port)]

    # Position it where it now belongs, so `leaf_stages` order still reads as
    # the graph does. Layers are computed from edges, but a stage list in a
    # surprising order makes every printed view harder to trust.
    stages = list(bench.stages)
    stages.insert(next(i for i, s in enumerate(stages) if s.id == before), fresh)

    return _validated(replace(
        bench, stages=tuple(stages), edges=tuple(rewired),
        nodes=tuple({n.id: n for n in (*bench.nodes, *manifests)}.values()),
        candidates=tuple({c.id: c for c in (
            *bench.candidates,
            *(NodeCandidate(id=m.id, node_id=m.id) for m in manifests))}.values()),
    ), edit)


def _remove(bench, edit, library) -> Outcome:
    stage_id = edit.where
    stage = next((s for s in bench.leaf_stages if s.id == stage_id), None)
    if stage is None:
        return Outcome(edit, False, f"no step called {stage_id!r}")

    # Marked optional first, then removed, so the one implementation of "can
    # this be lifted out" decides — the same rule the compiler and the counter
    # use, rather than a second copy of it here.
    marked = replace(bench, stages=tuple(
        replace(s, optional=True) if s.id == stage_id else s
        for s in bench.stages))
    if not marked.omittable().get(stage_id):
        return Outcome(edit, False,
                       f"{stage_id!r} cannot be lifted out: what feeds it does "
                       f"not satisfy what it feeds")

    from browsergraph.compile import _rewired

    return _validated(replace(
        bench,
        stages=tuple(s for s in bench.stages if s.id != stage_id),
        edges=_rewired(bench.wiring(), (stage_id,))), edit)


def _widen(bench, edit, library) -> Outcome:
    """Let a step also use nodes it does not currently admit.

    Adds, never narrows. A stage that omits a compatible candidate is rejected
    by `validate()`, because hiding an option from the search is the thing this
    whole model exists to prevent — so "use this one instead" is not an edit
    that can exist. Restricting what may run is a policy decision, made at gate
    time with a reason recorded.
    """
    stage_id = edit.where
    stage = next((s for s in bench.leaf_stages if s.id == stage_id), None)
    if stage is None:
        return Outcome(edit, False, f"no step called {stage_id!r}")

    wanted = [edit.what] if isinstance(edit.what, str) else list(edit.what)
    known = {n.id: n for n in (*bench.nodes, *library)}
    missing = [w for w in wanted if w not in known]
    if missing:
        return Outcome(edit, False, f"no node called {missing[0]!r}")

    manifests = [known[w] for w in wanted]
    return _validated(replace(
        bench,
        stages=tuple(
            replace(s, candidates=tuple(dict.fromkeys((*s.candidates, *wanted))))
            if s.id == stage_id else s
            for s in bench.stages),
        nodes=tuple({n.id: n for n in (*bench.nodes, *manifests)}.values()),
        candidates=tuple({c.id: c for c in (
            *bench.candidates,
            *(NodeCandidate(id=m.id, node_id=m.id) for m in manifests))}.values()),
    ), edit)


def _retype(bench, edit, kind: str) -> Outcome:
    stage_id = edit.where
    stage = next((s for s in bench.leaf_stages if s.id == stage_id), None)
    if stage is None:
        return Outcome(edit, False, f"no step called {stage_id!r}")
    if stage.kind == kind:
        return Outcome(edit, False, f"{stage_id!r} is already a {kind} step")
    return _validated(replace(bench, stages=tuple(
        replace(s, kind=kind) if s.id == stage_id else s
        for s in bench.stages)), edit)


def _make_map(bench, edit, library) -> Outcome:
    """A map stage talks about collections; the node inside handles one item.

    Left to `validate()` to check, because the rule about element types lives
    there and a second implementation of it here would be a second thing to get
    wrong.
    """
    return _retype(bench, edit, "map")


def _make_branch(bench, edit, library) -> Outcome:
    stage = next((s for s in bench.leaf_stages if s.id == edit.where), None)
    if stage is not None and len(stage.outputs) < 2:
        return Outcome(edit, False,
                       f"{edit.where!r} has one output port, so there is nothing "
                       f"for a branch to choose between — give it two first")
    return _retype(bench, edit, "branch")


# --- proposing them ---------------------------------------------------------

@dataclass
class Proposal:
    """A set of edits and what became of each. The refusal rate lives here."""
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def accepted(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.ok]

    @property
    def refusal_rate(self) -> float:
        """The metric for a proposer. A model whose edits are mostly illegal is
        not helping, however good the legal ones are."""
        if not self.outcomes:
            return 0.0
        return 1.0 - len(self.accepted) / len(self.outcomes)

    def text(self) -> str:
        lines = [f"{len(self.accepted)} of {len(self.outcomes)} edits compiled "
                 f"({self.refusal_rate:.0%} refused)"]
        for outcome in self.outcomes:
            mark = "ok  " if outcome.ok else "NO  "
            lines.append(f"  {mark}{outcome.edit.describe()}")
            if not outcome.ok:
                lines.append(f"       {outcome.reason}")
        return "\n".join(lines)


def variants(bench: WorkbenchDefinition, proposals: Sequence[GraphEdit], *,
             library: Sequence[NodeManifest] = ()) -> Proposal:
    """Every proposed edit, applied and checked. The graphs that survive are the
    ones worth searching within."""
    return Proposal(outcomes=apply_all(bench, proposals, library=library))


def insertion_points(bench: WorkbenchDefinition,
                     manifest: NodeManifest) -> list[tuple[str, str]]:
    """Every edge where this node would actually type-check.

    The question worth asking a model is narrow — *should* this go here — and it
    is only worth asking about places where it legally could. Working that out
    costs nothing and removes the largest category of wasted proposals.
    """
    out = []
    for edge in bench.wiring():
        trial = GraphEdit("insert", (edge.source, edge.target), manifest.id)
        if apply(bench, trial, library=[manifest]).ok:
            out.append((edge.source, edge.target))
    return out


def mechanical(bench: WorkbenchDefinition,
               library: Sequence[NodeManifest] = ()) -> list[GraphEdit]:
    """Every structural edit that is legal, found by trying them. No model.

    The baseline a model-guided proposer has to beat, and it is a real
    competitor rather than a straw one: for a small library and a small graph,
    enumerating the legal insertions is cheap and complete. Complete is a strong
    property — a model can only ever be *faster*, never more thorough, on a
    space this size.

    Which is the useful framing for the whole guided layer. It is not needed
    where enumeration is affordable; it is needed where the library is large,
    where the edit is a removal or a retype that enumeration would propose
    hundreds of, or where the question is which of fifty legal insertions is
    worth spending a run on. Measure it there, against this.
    """
    proposals: list[GraphEdit] = []
    for manifest in library:
        for after, before in insertion_points(bench, manifest):
            proposals.append(GraphEdit(
                "insert", (after, before), manifest.id,
                why="legal here, found by enumeration", confidence=0.0))
    for stage in bench.leaf_stages:
        if stage.optional:
            proposals.append(GraphEdit(
                "remove", stage.id, why="declared optional", confidence=0.0))
    return proposals


def to_dicts(proposals: Sequence[GraphEdit]) -> list[dict]:
    return [p.to_dict() for p in proposals]


def from_dicts(data: Sequence[Mapping[str, Any]]) -> list[GraphEdit]:
    """Parse edits from a model's reply, skipping what cannot be read.

    Lenient about extra keys and strict about the ones it needs, because a
    model that adds a field is being helpful and one that omits `kind` has not
    answered the question.
    """
    out = []
    for row in data:
        kind = str(row.get("kind", "")).strip()
        if not kind:
            continue
        where = row.get("where", "")
        if isinstance(where, list):
            where = tuple(where)
        out.append(GraphEdit(kind=kind, where=where, what=row.get("what", ""),
                             why=str(row.get("why", "")),
                             confidence=float(row.get("confidence", 0.0) or 0.0)))
    return out
