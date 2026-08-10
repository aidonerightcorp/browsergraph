"""The short way to write a graph, and the pieces to build one out of.

Every notebook in this repository opened by defining the same three helpers —
one to describe a node, one to describe a step, one to put them together and
check the result. Thirty lines, copied nine times, and anyone who copied a
notebook to start their own project got helpers that did not exist in the
library. That is a bad trade: the convenient way to use something should be the
supported way.

So they live here. Nothing in this file adds capability; it removes ceremony.

    from browsergraph.quick import graph, link, node, step

    nodes = [node("read.csv", "read", gives=[("out", "Rows")]),
             node("clean.trim", "clean", [("in", "Rows")], [("out", "Rows")])]

    bench = graph("Tidy a file", "Read a CSV and trim the whitespace.",
                  steps=[step("read",  "Read it",  [], [("out", "Rows")], "read",  ["read.csv"]),
                         step("clean", "Clean it", [("in", "Rows")], [("out", "Rows")], "clean", ["clean.trim"])],
                  nodes=nodes, links=[link("read", "clean")])

`subgraph` is the other half. A shape you have written once — profile, check,
adjudicate; or fetch, retry, verify — can be given a prefix and spliced into a
bigger graph as many times as you like, without the ids colliding.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from browsergraph.manifest import NodeManifest, PortSpec
from browsergraph.workbench import (
    Edge,
    NodeCandidate,
    StageDefinition,
    WorkbenchDefinition,
)

#: `(name, type)` pairs. The notebooks all wrote ports this way because it is
#: the shortest thing that still says both halves.
Ports = Sequence[tuple[str, str]]


def node(node_id: str, capability: str, takes: Ports = (), gives: Ports = (),
         *, description: str = "", **extra: Any) -> NodeManifest:
    """One node manifest, with the boilerplate filled in.

    `extra` goes straight to `NodeManifest`, so `effects=`, `permissions=`,
    `runtime=`, `metrics=` and `facets=` all work unchanged.
    """
    return NodeManifest(
        id=node_id, kind=extra.pop("kind", "function"),
        description=description or f"{capability} via {node_id}",
        capabilities=(capability,),
        inputs=tuple(PortSpec(n, t) for n, t in takes),
        outputs=tuple(PortSpec(n, t) for n, t in gives),
        **extra)


def step(step_id: str, name: str, takes: Ports, gives: Ports, capability: str,
         candidates: Sequence[str] = (), *, kind: str = "atomic",
         success: str = "", optional: bool = False) -> StageDefinition:
    """One step of the job: what it needs, what it gives, what could do it."""
    return StageDefinition(
        id=step_id, name=name, kind=kind, optional=optional,
        required_capabilities=(capability,),
        inputs=tuple(PortSpec(n, t) for n, t in takes),
        outputs=tuple(PortSpec(n, t) for n, t in gives),
        success=success or f"{name} produced its declared output",
        candidates=tuple(candidates))


def link(source: str, target: str, from_port: str = "", to_port: str = "") -> Edge:
    """One typed connection. Name the ports when either end has more than one."""
    return Edge(source=source, target=target, from_port=from_port, to_port=to_port)


def graph(title: str, task: str, steps: Sequence[StageDefinition],
          nodes: Sequence[NodeManifest] = (), links: Sequence[Edge] = (),
          *, success: str = "", strict: bool = False,
          profiles: Sequence = ()) -> WorkbenchDefinition:
    """Put it together, and check it before anything runs.

    Candidates are derived from the nodes, which is what the notebooks all did
    by hand. `strict=True` raises on any problem instead of returning a graph
    that will fail later — worth turning on in a script, and off in a notebook
    where seeing the complaint is the lesson.
    """
    bench = WorkbenchDefinition(
        title=title, task=task, success=success,
        stages=tuple(steps), nodes=tuple(nodes), edges=tuple(links),
        candidates=tuple(NodeCandidate(id=n.id, node_id=n.id) for n in nodes),
        optimization_profiles=tuple(profiles))
    if strict:
        return bench.assert_valid()
    return bench


def problems(bench: WorkbenchDefinition) -> list[str]:
    """Everything wrong with it, or an empty list. A friendlier `validate`."""
    return list(bench.validate())


def subgraph(prefix: str, steps: Sequence[StageDefinition],
             links: Sequence[Edge] = (), *,
             rename: Mapping[str, str] | None = None
             ) -> tuple[tuple[StageDefinition, ...], tuple[Edge, ...]]:
    """A shape you already wrote, ready to be used again under a new prefix.

    Reuse is the thing `substages` never gave you. A composite stage groups
    steps for *display*; it does not let you take "profile, check, adjudicate"
    and drop it into three different graphs, because the ids would collide the
    moment you used it twice.

    This renames every step and every edge endpoint to `prefix.step`, so the
    same fragment can appear as many times as you like:

        checks, wiring = subgraph("inbound", QUALITY_STEPS, QUALITY_LINKS)
        more, more_wiring = subgraph("outbound", QUALITY_STEPS, QUALITY_LINKS)

    `rename` maps a step id to something other than the prefixed default, for
    the joins where a fragment has to meet the graph around it.
    """
    mapping = {s.id: f"{prefix}.{s.id}" for s in steps}
    mapping.update(rename or {})

    from dataclasses import replace

    moved = tuple(replace(s, id=mapping[s.id],
                          name=s.name or s.id.replace("_", " ").capitalize())
                  for s in steps)
    rewired = tuple(replace(e,
                            source=mapping.get(e.source, e.source),
                            target=mapping.get(e.target, e.target))
                    for e in links)
    return moved, rewired


def fanout(source: str, targets: Iterable[str], *, from_port: str = "") -> tuple[Edge, ...]:
    """One step feeding several independent ones. The shape of a parallel."""
    return tuple(link(source, target, from_port=from_port) for target in targets)


def fanin(sources: Mapping[str, str], target: str) -> tuple[Edge, ...]:
    """Several steps meeting at one. `sources` maps a step id to the port it
    lands on, because an unlabelled join is the bug this repository started
    with."""
    return tuple(link(source, target, to_port=port)
                 for source, port in sources.items())


def chain(*step_ids: str) -> tuple[Edge, ...]:
    """The boring case, written once."""
    return tuple(link(a, b) for a, b in zip(step_ids, step_ids[1:], strict=False))
