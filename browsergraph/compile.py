"""Turning a description into an immutable thing that ran.

`validate()` checks a *description*. Nothing until now turned one into an
artifact, which is why replay was aspirational: a receipt named a route, and the
route could be edited underneath it with nothing noticing. "We learned that this
route is good" was a statement about a name, not about a thing.

A `Plan` fixes that. It is the fully resolved, topologically ordered form of one
route — every edge concrete, every parameter bound, the union of permissions and
effects computed rather than declared — and it carries a **content hash of all
of it**. That hash is the missing key:

* receipts are keyed on it, so evidence attaches to the plan that actually ran;
* two runs with the same hash are the same computation, and a difference in
  outcome is a difference in the world rather than in the graph;
* a plan whose hash is not the one in the receipt **is not the plan that ran**,
  and that can now be detected rather than assumed away.

Compilation is deliberately separate from execution. A plan can be built,
inspected, diffed against another plan and stored without anything running —
which is what makes it useful for review, and what makes an LLM harness able to
check its work before spending a browser session on it.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from browsergraph import types as _types
from browsergraph.workbench import Edge, WorkbenchDefinition


class CompileError(RuntimeError):
    """The route cannot be turned into a plan, with every reason listed."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("cannot compile:\n  " + "\n  ".join(self.problems))


@dataclass(frozen=True)
class Step:
    """One resolved sub-step: what runs, bound, with its ports concrete."""
    stage: str
    candidate: str
    node: str
    name: str = ""
    params: Mapping[str, Any] = field(default_factory=dict)
    inputs: tuple[tuple[str, str], ...] = ()      # (port, type)
    outputs: tuple[tuple[str, str], ...] = ()
    permissions: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    deterministic: bool = True
    #: atomic, map or branch. In the digest because a plan that maps over its
    #: input is a different computation from one that runs once, even with the
    #: same nodes in the same order.
    kind: str = "atomic"

    def to_dict(self) -> dict:
        return {"stage": self.stage, "candidate": self.candidate,
                "kind": self.kind,
                "node": self.node, "name": self.name,
                "params": dict(self.params),
                "inputs": [list(p) for p in self.inputs],
                "outputs": [list(p) for p in self.outputs],
                "permissions": list(self.permissions),
                "effects": list(self.effects),
                "deterministic": self.deterministic}


@dataclass(frozen=True)
class Plan:
    """One route, resolved, ordered, and content-addressed."""
    steps: tuple[Step, ...] = ()
    edges: tuple[Edge, ...] = ()
    order: tuple[str, ...] = ()
    layers: tuple[tuple[str, ...], ...] = ()
    permissions: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    deterministic: bool = True
    #: Optional stages this route left out. Part of the plan's identity: a
    #: pipeline that imputed and one that did not are different computations,
    #: and giving them the same digest would pool their evidence.
    omitted: tuple[str, ...] = ()
    schema_version: str = "1.0"
    source: str = ""

    # --- identity -----------------------------------------------------------

    @property
    def digest(self) -> str:
        """A hash of everything that decides what this computation is.

        Deliberately over the *resolved* form rather than the source document:
        two workbenches that differ only in a description compile to the same
        plan and should share evidence, and two that differ in a bound
        parameter must not, however similar they look.

        `omitted` is deliberately **not** in this payload, and its absence is
        load-bearing rather than an oversight. Leaving a stage out already
        changes `steps`, `edges` and `order`, so the two plans hash differently
        anyway; adding the key would additionally change the digest of every
        plan that omits nothing — which is all of them so far — and orphan every
        receipt and posterior already keyed on one.
        """
        payload = json.dumps({"steps": [s.to_dict() for s in self.steps],
                              "edges": [e.to_dict() for e in self.edges],
                              "order": list(self.order)},
                             sort_keys=True, separators=(",", ":"))
        return "plan:" + hashlib.sha256(payload.encode()).hexdigest()[:32]

    def matches(self, digest: str) -> bool:
        return self.digest == digest

    # --- reading ------------------------------------------------------------

    def step(self, stage: str) -> Step | None:
        return next((s for s in self.steps if s.stage == stage), None)

    @property
    def parallel_width(self) -> int:
        """The widest layer — how much of this could run at once."""
        return max((len(layer) for layer in self.layers), default=0)

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "digest": self.digest,
                "source": self.source,
                "steps": [s.to_dict() for s in self.steps],
                "edges": [e.to_dict() for e in self.edges],
                "order": list(self.order),
                "layers": [list(layer) for layer in self.layers],
                "omitted": list(self.omitted),
                "permissions": list(self.permissions),
                "effects": list(self.effects),
                "deterministic": self.deterministic}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def text(self) -> str:
        lines = [f"{self.digest}",
                 f"  {len(self.steps)} steps in {len(self.layers)} layers"
                 + (f", up to {self.parallel_width} at once"
                    if self.parallel_width > 1 else " (a chain)")]
        for index, layer in enumerate(self.layers):
            for stage in layer:
                step = self.step(stage)
                if step:
                    bound = " ".join(f"{k}={v}" for k, v in step.params.items())
                    lines.append(f"    {index + 1}. {step.stage:<14} "
                                 f"{step.name or step.node}"
                                 + (f"   [{bound}]" if bound else ""))
        if self.permissions:
            lines.append("  needs: " + ", ".join(self.permissions))
        if self.effects:
            lines.append("  changes: " + ", ".join(self.effects))
        if not self.deterministic:
            lines.append("  not deterministic — a re-run may differ legitimately")
        return "\n".join(lines)


def _rewired(edges, omitted) -> tuple[Edge, ...]:
    """The wiring with the omitted stages lifted out and their neighbours joined.

    `A -> skip -> B` becomes `A -> B`, carrying A's outgoing port and B's
    incoming port, because those are the ports that now meet. Done repeatedly
    so a run of two adjacent optional stages collapses properly rather than
    leaving a dangling edge into the second one.
    """
    if not omitted:
        return tuple(edges)

    kept = list(edges)
    for stage_id in omitted:
        incoming = [e for e in kept if e.target == stage_id]
        outgoing = [e for e in kept if e.source == stage_id]
        kept = [e for e in kept
                if e.source != stage_id and e.target != stage_id]
        for before in incoming:
            for after in outgoing:
                kept.append(Edge(source=before.source, target=after.target,
                                 from_port=before.from_port,
                                 to_port=after.to_port))
    return tuple(kept)


def _bypass_problems(workbench: WorkbenchDefinition, omitted, lattice) -> list[str]:
    """Whether leaving these stages out still type-checks.

    Omitting a step is not free. If `impute` turns `Rows` into `Rows` then it
    can be lifted out and its neighbours joined directly; if it turns `Rows`
    into `Matrix` then removing it leaves a hole nothing fills, and the graph
    that results is not a graph at all.

    So an optional stage has a real obligation: **what feeds it must satisfy
    what it feeds.** Checked here rather than trusted, because "optional" is a
    thing a person writes in a declaration and this is the only place that can
    tell whether it is true.

    Reported as a compile problem rather than raised, so a caller asking for an
    impossible topology gets it in the same list as every other reason a route
    would not compile.
    """
    if not omitted:
        return []

    can_omit = workbench.omittable()
    by_id = {s.id: s for s in workbench.leaf_stages}
    edges = workbench.wiring()
    problems: list[str] = []

    for stage_id in omitted:
        if can_omit.get(stage_id):
            continue

        incoming = [e for e in edges if e.target == stage_id]
        outgoing = [e for e in edges if e.source == stage_id]
        if not incoming and outgoing:
            problems.append(
                f"{stage_id!r} is optional but nothing feeds it, so leaving it "
                f"out removes the only source for "
                f"{', '.join(sorted(e.target for e in outgoing))}")
            continue
        if len(incoming) > 1 or len(outgoing) > 1:
            problems.append(
                f"{stage_id!r} is optional but has {len(incoming)} inputs and "
                f"{len(outgoing)} outputs — only a stage with one of each can "
                f"be left out, because otherwise there is no single connection "
                f"to make in its place")
            continue

        # `omittable` already decided; this loop exists to say *why* in the
        # reader's terms, naming the two stages that would have to meet.
        for before in incoming:
            upstream = by_id.get(before.source)
            produced = upstream.port(before.from_port, outgoing=True) if upstream else None
            for after in outgoing:
                downstream = by_id.get(after.target)
                consumed = (downstream.port(after.to_port, outgoing=False)
                            if downstream else None)
                if produced is None or consumed is None:
                    continue
                mismatch = _types.check(produced, consumed, lattice)
                if mismatch is not None:
                    problems.append(
                        f"{stage_id!r} cannot be left out: {before.source} then "
                        f"gives {produced.type!r} straight to {after.target}, "
                        f"which takes {consumed.type!r} — {mismatch}")
    return problems


def compile_route(workbench: WorkbenchDefinition, route: Mapping[str, str],
                  *, source: str = "") -> Plan:
    """Resolve one route into a plan, or refuse with every reason.

    Refusing here rather than at run time is the point: everything this checks
    is knowable without a browser, a model or a network, and finding it out
    thirty seconds into a session is finding it out too late.
    """
    problems: list[str] = []
    nodes = workbench.nodes_by_id
    candidates = workbench.candidates_by_id
    lattice = _types.lattice_from(workbench.nodes)
    by_id = {s.id: s for s in workbench.leaf_stages}

    for cycle in workbench.cycles():
        problems.append("the graph has a cycle: " + " -> ".join(cycle))

    # An optional stage a route did not name is *left out of the graph*, not
    # left unfilled. That is a different plan — a different topology — and it is
    # the smallest form of searching over graphs rather than only over nodes.
    #
    # `optional` was a declared field that nothing read: every route had to fill
    # every stage, so "optional" meant nothing at compile time, nothing at run
    # time, and nothing to the search. It means something now.
    omitted = tuple(sorted(s.id for s in workbench.leaf_stages
                           if s.optional and not route.get(s.id)))
    problems.extend(_bypass_problems(workbench, omitted, lattice))

    steps: list[Step] = []
    for stage in workbench.leaf_stages:
        if stage.id in omitted:
            continue
        chosen = route.get(stage.id)
        if not chosen:
            problems.append(
                f"no candidate chosen for {stage.id!r}"
                + ("" if stage.optional else
                   " (mark the stage optional if leaving it out is allowed)"))
            continue
        if chosen not in stage.candidates:
            problems.append(f"{chosen!r} is not admitted to {stage.id!r}")
            continue
        candidate = candidates.get(chosen)
        manifest = nodes.get(candidate.node_id) if candidate else None
        if manifest is None:
            problems.append(f"{chosen!r} resolves to no node manifest")
            continue
        for parameter in manifest.parameters:
            if parameter.required and parameter.name not in candidate.params:
                problems.append(f"{stage.id}: required parameter "
                                f"{parameter.name!r} is unbound")

        # Does the chosen candidate actually satisfy the stage it was placed in?
        #
        # This block used to be missing, while the comment below claimed edges
        # were "checked against the *chosen* candidates". They were not: edges
        # are compared stage-port to stage-port, and nothing compared a stage's
        # ports to those of the node bound to it. Discovery normally guarantees
        # it — `eligible()` admits nothing incompatible — but a hand-written
        # workbench, a JSON document, or a model appending an id to a candidate
        # list all bypass discovery. `validate()` caught those; `compile_route`
        # did not, so a plan with a content hash was obtainable for a graph the
        # validator rejected. A digest that can certify an invalid plan is worse
        # than no digest.
        # A map stage's ports are collections; the node inside it handles one
        # item. Compare against the element type or every map step would be
        # rejected for the difference that makes it a map.
        #
        # Bound per iteration rather than closed over `stage`: this is defined
        # inside a loop, and a closure that reads the loop variable is one
        # refactor away from checking every stage against the last one.
        _wanted = (_types.element_of if stage.kind == "map" else (lambda t: t))

        for port in stage.inputs:
            if manifest.inputs and not (
                    manifest.accepts(_wanted(port.type))
                    or any(lattice.is_a(_wanted(port.type), p.type)
                           for p in manifest.inputs)):
                problems.append(
                    f"{stage.id}: {chosen!r} does not accept {_wanted(port.type)!r} "
                    f"on port {port.name!r} — it takes "
                    f"{[p.type for p in manifest.inputs]}")
        for port in stage.outputs:
            if not (manifest.produces(_wanted(port.type))
                    or any(lattice.is_a(p.type, _wanted(port.type))
                           for p in manifest.outputs)):
                problems.append(
                    f"{stage.id}: {chosen!r} does not produce {_wanted(port.type)!r} "
                    f"on port {port.name!r} — it gives "
                    f"{[p.type for p in manifest.outputs]}")
        steps.append(Step(
            stage=stage.id, candidate=chosen, node=manifest.id,
            name=candidate.name, params=dict(candidate.params),
            inputs=tuple((p.name, p.type) for p in stage.inputs),
            outputs=tuple((p.name, p.type) for p in stage.outputs),
            permissions=tuple(manifest.permissions),
            effects=tuple(manifest.effects),
            deterministic=manifest.runtime.get("deterministic") is not False,
            kind=stage.kind))

    # Edges are checked against the *chosen* candidates, which is stricter than
    # checking the stage declarations: a stage may declare a general type while
    # the candidate bound to it produces something narrower.
    for edge in workbench.wiring():
        left, right = by_id.get(edge.source), by_id.get(edge.target)
        if left is None or right is None:
            problems.append(f"edge {edge} names an unknown sub-step")
            continue
        produced = left.port(edge.from_port, outgoing=True)
        consumed = right.port(edge.to_port, outgoing=False)
        if produced is None or consumed is None:
            problems.append(f"edge {edge} names a port that does not exist")
            continue
        mismatch = _types.check(produced, consumed, lattice)
        if mismatch is not None:
            problems.append(f"edge {edge}: {mismatch}")

    if problems:
        raise CompileError(problems)

    order = [stage for layer in workbench.layers() for stage in layer
             if stage not in omitted]
    # Reconnect around what was left out, so the plan is a graph in its own
    # right rather than the original graph with holes in it. Everything
    # downstream — execution, drawing, the digest — then sees one consistent
    # topology and needs to know nothing about optionality.
    edges = _rewired(workbench.wiring(), omitted)
    return Plan(
        steps=tuple(sorted(steps, key=lambda s: order.index(s.stage))),
        edges=edges,
        order=tuple(order),
        omitted=omitted,
        layers=tuple(tuple(s for s in layer if s not in omitted)
                     for layer in workbench.layers()
                     if any(s not in omitted for s in layer)),
        permissions=tuple(sorted({p for s in steps for p in s.permissions})),
        effects=tuple(sorted({e for s in steps for e in s.effects})),
        deterministic=all(s.deterministic for s in steps),
        source=source)


def diff(before: Plan, after: Plan) -> list[str]:
    """What changed between two plans.

    When a graph that worked stops working, this is the shortest path to why —
    and unlike a diff of the source document it ignores everything that does
    not affect what runs.
    """
    if before.digest == after.digest:
        return []
    out = []
    left = {s.stage: s for s in before.steps}
    right = {s.stage: s for s in after.steps}
    for stage in sorted(set(left) | set(right)):
        a, b = left.get(stage), right.get(stage)
        if a is None:
            out.append(f"+ {stage}: {b.name}")
        elif b is None:
            out.append(f"- {stage}: {a.name}")
        elif a.candidate != b.candidate:
            out.append(f"~ {stage}: {a.name} -> {b.name}")
        elif a.params != b.params:
            out.append(f"~ {stage} params: {a.params} -> {b.params}")
    if set(before.permissions) != set(after.permissions):
        gained = set(after.permissions) - set(before.permissions)
        if gained:
            out.append("now needs: " + ", ".join(sorted(gained)))
    if set(after.effects) - set(before.effects):
        out.append("now changes: "
                   + ", ".join(sorted(set(after.effects) - set(before.effects))))
    return out
