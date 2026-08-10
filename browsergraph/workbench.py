"""Stages, candidates, routes — and the rules that keep them apart.

The whole architecture rests on one distinction that is easy to state and easy
to lose:

    "Acquire inputs"                                    is a task stage.
    "Browser adapter · Playwright · Firefox · headless" is an atomic candidate.
    controller / binary / display                       are its parameters.
    InputHandle                                         is its output contract.
    browser, network                                    are permissions.
    latency, quality, cost                              are measurements.
    a weighted scorer                                   is optimization logic.

Only the first is a column in the graph. Every diagram that has ever confused me
did so by promoting one of the others into the task line — putting "Playwright"
next to "Verify" as if they were the same kind of thing, or drawing a learning
loop as a backward arrow among the steps, so the picture no longer says what
runs in what order.

So the invariants here are strict, and the validator enforces them:

* stages are ordered columns, left to right, and nothing reorders them;
* a route picks exactly one primary candidate per stage;
* fallbacks belong to a stage's choice, and never become extra stages;
* feedback and optimization are a separate control plane, typed and labelled;
* a configuration dimension expands the candidates *inside* a stage.

The other rule worth stating: a stage must contain **every** compatible
candidate the registry knows about. Quietly dropping the ones that scored badly
last time would make the picture a summary of previous opinions rather than of
what is actually possible — and there would be no way to tell from looking.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import pathlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from browsergraph import types as _types
from browsergraph.manifest import SCHEMA_VERSION, NodeManifest, PortSpec

DIRECTIONS = ("maximize", "minimize")

#: Scopes a feedback signal can be attributed to. Attribution is what makes
#: feedback usable: "this failed" is not actionable, "this candidate failed on
#: this edge in this task context" is.
SCOPES = ("candidate", "edge", "stage", "route", "task", "registry", "version",
          "context")


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")


def candidate_id(node_id: str, params: Mapping[str, Any] | None = None) -> str:
    """A stable, readable, parameter-sensitive identity.

    Readable because a bare hash makes every diagram and every log unusable;
    hashed because parameter values are arbitrary and a readable form alone
    would collide. Deterministically serialised, so the ID does not depend on
    the order a caller happened to build the mapping in — otherwise the same
    configuration would accumulate evidence under two different names.
    """
    params = dict(params or {})
    if not params:
        return node_id
    ordered = json.dumps(params, sort_keys=True, separators=(",", ":"),
                         default=str)
    digest = hashlib.sha256(f"{node_id}\x00{ordered}".encode()).hexdigest()[:8]
    readable = ".".join(_slug(params[key]) for key in sorted(params))
    return f"{node_id}.{readable}.{digest}"


@dataclass(frozen=True)
class NodeCandidate:
    """One node definition with every selectable parameter bound.

    A definition with three models and three strategies is not one candidate; it
    is nine, and a viewer that draws it as one is hiding the eight choices a
    person actually has.
    """
    id: str
    node_id: str
    name: str = ""
    params: Mapping[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", dict(self.params))
        object.__setattr__(self, "tags", tuple(self.tags))
        if not self.name:
            bits = [str(self.params[k]) for k in sorted(self.params)]
            base = self.node_id.rsplit(".", 1)[-1].replace("_", " ")
            object.__setattr__(self, "name",
                               " · ".join([base, *bits]) if bits else base)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"id": self.id, "node_id": self.node_id,
                               "name": self.name}
        if self.params:
            out["params"] = dict(self.params)
        if self.tags:
            out["tags"] = list(self.tags)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NodeCandidate:
        return cls(id=data.get("id", ""), node_id=data.get("node_id", ""),
                   name=data.get("name", ""), params=dict(data.get("params") or {}),
                   tags=tuple(data.get("tags") or ()))


def expand_node_candidates(manifests: Sequence[NodeManifest]) -> tuple[NodeCandidate, ...]:
    """Every concrete binding of every definition.

    This is where a search space becomes visible rather than implied. Five
    controllers × six binaries × two display modes is sixty things a person can
    choose, and the only honest way to show that is sixty entries.
    """
    out: list[NodeCandidate] = []
    for manifest in manifests:
        searchable = manifest.searchable_parameters
        if not searchable:
            fixed = {p.name: p.default for p in manifest.parameters
                     if p.default is not None}
            out.append(NodeCandidate(id=candidate_id(manifest.id, fixed),
                                     node_id=manifest.id, params=fixed,
                                     tags=manifest.tags))
            continue
        names = [p.name for p in searchable]
        for combo in itertools.product(*[p.choices for p in searchable]):
            params = dict(zip(names, combo, strict=True))
            params.update({p.name: p.default for p in manifest.parameters
                           if p.default is not None and p.name not in params})
            out.append(NodeCandidate(id=candidate_id(manifest.id, params),
                                     node_id=manifest.id, params=params,
                                     tags=manifest.tags))
    return tuple(out)


@dataclass(frozen=True)
class Edge:
    """One typed connection between two stages.

    The thing that turns a pipeline into a graph. A chain is the degenerate
    case — `to_port` fed by the single output of the stage before it — and a
    join is two edges into different ports of the same stage, which a sequence
    of stages simply cannot express.
    """
    source: str
    target: str
    from_port: str = ""
    to_port: str = ""

    def to_dict(self) -> dict:
        out = {"source": self.source, "target": self.target}
        if self.from_port:
            out["from_port"] = self.from_port
        if self.to_port:
            out["to_port"] = self.to_port
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Edge:
        return cls(source=data.get("source", ""), target=data.get("target", ""),
                   from_port=data.get("from_port", ""),
                   to_port=data.get("to_port", ""))

    def __str__(self) -> str:
        left = f"{self.source}.{self.from_port}" if self.from_port else self.source
        right = f"{self.target}.{self.to_port}" if self.to_port else self.target
        return f"{left} -> {right}"


#: The only three shapes a stage can have. Kept closed on purpose: each is a
#: real difference in how the executor runs the step, not a label.
STAGE_KINDS = ("atomic", "map", "branch")


@dataclass(frozen=True)
class StageDefinition:
    """One ordered requirement of the task. One column.

    `variant_axes` names dimensions worth exploring within the stage. They are
    documentation for a searcher — they do **not** become columns, which is
    exactly the confusion this type exists to prevent.
    """
    id: str
    name: str = ""
    description: str = ""
    input_type: str = ""
    output_type: str = ""
    success: str = ""
    optional: bool = False
    #: How this stage runs. Three shapes, and only three, because each one is a
    #: real difference in execution rather than a label:
    #:
    #: * ``atomic`` — runs once. What every stage was until now.
    #: * ``map``    — runs once per item of its input, and gives back a list.
    #:                This is how "do it to all ten thousand files" gets said.
    #: * ``branch`` — runs once and names which of its output ports gets the
    #:                value. The other ports stay empty and whatever they feed
    #:                is skipped, which is how a condition gets said.
    #:
    #: Loops are deliberately absent. A loop needs a termination argument, and
    #: a graph that can loop without one is a graph that can hang.
    kind: str = "atomic"
    variant_axes: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    substages: tuple[StageDefinition, ...] = ()
    #: Named typed ports. A stage with two inputs is a join — the shape a
    #: sequence of stages cannot express. `input_type`/`output_type` remain the
    #: shorthand for the single-port case and are folded into these, so every
    #: existing definition keeps working and nothing has to be rewritten to
    #: gain a second port.
    inputs: tuple[PortSpec, ...] = ()
    outputs: tuple[PortSpec, ...] = ()

    def __post_init__(self) -> None:
        for name in ("variant_axes", "required_capabilities", "candidates",
                     "substages", "inputs", "outputs"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not self.name:
            object.__setattr__(self, "name", self.id.replace("_", " ").capitalize())
        if not self.inputs and self.input_type:
            object.__setattr__(self, "inputs", (PortSpec("in", self.input_type),))
        if not self.outputs and self.output_type:
            object.__setattr__(self, "outputs", (PortSpec("out", self.output_type),))

    def port(self, name: str, outgoing: bool) -> PortSpec | None:
        ports = self.outputs if outgoing else self.inputs
        if not name:
            return ports[0] if len(ports) == 1 else None
        return next((p for p in ports if p.name == name), None)

    @property
    def is_composite(self) -> bool:
        """Does this stage decompose into ordered sub-steps?

        A stage is either a leaf that holds candidates or a composite that holds
        sub-steps — never both. Allowing both would make "one choice per stage"
        ambiguous, which is the one thing the whole model rests on.
        """
        return bool(self.substages)

    def leaves(self) -> tuple[StageDefinition, ...]:
        """The ordered sub-steps a route actually chooses between.

        A coarse stage hides its own combinatorics. "Acquire inputs" with 76
        candidates looks like one decision and is really three — resolve a
        target, open a session, read a payload — each with its own matrix. The
        product over sub-steps is the number of choices that actually exist;
        the flat count is the number you happen to be looking at.
        """
        if not self.substages:
            return (self,)
        return tuple(leaf for sub in self.substages for leaf in sub.leaves())

    def depth(self) -> int:
        return 1 + max((sub.depth() for sub in self.substages), default=0)

    def eligible(self, manifest: NodeManifest, lattice=None) -> bool:
        """Could this definition legally perform this stage?

        Capability and both port contracts — a node that can `verify` but cannot
        accept what the previous stage produces is not a candidate here, and
        finding that out at run time is finding it out too late.

        `lattice` makes this agree with the compiler. Without it, discovery
        compares type names with `==` while `compile_route` compares them
        through the subtype lattice, and the two disagree about exactly the
        nodes that declare an `is_a` relation: a loader producing `CsvRecords`
        for a stage that asks for `Records` compiles perfectly and was invisible
        to discovery, so the stage came back with zero candidates and the whole
        workbench reported zero routes. A registry whose search is stricter than
        its compiler hides the nodes that were most carefully described, which
        is precisely backwards.
        """
        if not manifest.can(*self.required_capabilities):
            return False
        # Every port this stage declares must be served by a port on the node.
        # The single-port case is unchanged; a two-input join now requires a
        # node that genuinely accepts both, instead of one that happens to
        # accept the first.
        #
        # Directions are not symmetric. For an input, the stage promises to hand
        # over `port.type`, so the node must accept that or something wider. For
        # an output, the stage promises to produce `port.type`, so the node must
        # produce that or something narrower.
        want = ((lambda t: _types.element_of(t)) if self.kind == "map"
                else (lambda t: t))
        for port in self.inputs:
            if manifest.accepts(want(port.type)):
                continue
            if lattice is not None and not manifest.inputs:
                continue                     # a source consumes nothing
            if lattice is None or not any(
                    lattice.is_a(want(port.type), p.type) for p in manifest.inputs):
                return False
        for port in self.outputs:
            if manifest.produces(want(port.type)):
                continue
            if lattice is None or not any(
                    lattice.is_a(p.type, want(port.type)) for p in manifest.outputs):
                return False
        return True

    def with_discovered_candidates(
            self, manifests: Sequence[NodeManifest],
            candidates: Sequence[NodeCandidate],
            lattice=None) -> StageDefinition:
        """Every compatible candidate in the registry, not a chosen few.

        Recurses: a composite stage discovers nothing itself and asks each
        sub-step instead, because only leaves hold candidates.

        The lattice is built from the manifests being searched unless one is
        supplied, so subtype declarations are honoured by default rather than
        only when the caller remembers to ask.
        """
        if lattice is None:
            from browsergraph import types as _types
            lattice = _types.lattice_from(manifests)
        if self.substages:
            return replace(self, substages=tuple(
                sub.with_discovered_candidates(manifests, candidates, lattice)
                for sub in self.substages))
        by_id = {m.id: m for m in manifests}
        found = tuple(c.id for c in candidates
                      if c.node_id in by_id
                      and self.eligible(by_id[c.node_id], lattice))
        return replace(self, candidates=found)

    def to_dict(self) -> dict:
        # The shorthand is written from the ports when it was never set, and
        # this is not a tidiness detail — it was a bug that silently destroyed
        # graphs. A stage built with `inputs=(PortSpec("in", "Profile"),)` and
        # no `input_type` wrote neither field: the shorthand was empty, and the
        # port list below is skipped for a single port named "in". Loading it
        # back gave a stage with no ports at all, and every edge then failed
        # with "names a port that does not exist" — on a workbench that had
        # been perfectly valid before it was saved.
        single_in = (self.inputs[0].type if len(self.inputs) == 1
                     and self.inputs[0].name == "in" else "")
        single_out = (self.outputs[0].type if len(self.outputs) == 1
                      and self.outputs[0].name == "out" else "")
        out: dict[str, Any] = {"id": self.id, "name": self.name,
                               "description": self.description,
                               "input_type": self.input_type or single_in,
                               "output_type": self.output_type or single_out,
                               "success": self.success}
        if self.optional:
            out["optional"] = True
        if self.kind != "atomic":
            out["kind"] = self.kind
        for key in ("variant_axes", "required_capabilities", "candidates"):
            if getattr(self, key):
                out[key] = list(getattr(self, key))
        # Only when they say more than input_type/output_type already do.
        if len(self.inputs) > 1 or any(p.name != "in" for p in self.inputs):
            out["inputs"] = [p.to_dict() for p in self.inputs]
        if len(self.outputs) > 1 or any(p.name != "out" for p in self.outputs):
            out["outputs"] = [p.to_dict() for p in self.outputs]
        if self.substages:
            out["substages"] = [s.to_dict() for s in self.substages]
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StageDefinition:
        return cls(id=data.get("id", ""), name=data.get("name", ""),
                   description=data.get("description", ""),
                   input_type=data.get("input_type", ""),
                   output_type=data.get("output_type", ""),
                   success=data.get("success", ""),
                   optional=bool(data.get("optional", False)),
                   kind=data.get("kind", "atomic"),
                   variant_axes=tuple(data.get("variant_axes") or ()),
                   required_capabilities=tuple(data.get("required_capabilities") or ()),
                   candidates=tuple(data.get("candidates") or ()),
                   inputs=tuple(PortSpec.from_dict(p)
                                for p in data.get("inputs") or ()),
                   outputs=tuple(PortSpec.from_dict(p)
                                 for p in data.get("outputs") or ()),
                   substages=tuple(cls.from_dict(s)
                                   for s in data.get("substages") or ()))


@dataclass(frozen=True)
class SolutionDefinition:
    """A complete route: exactly one primary candidate per stage."""
    id: str
    name: str = ""
    description: str = ""
    status: str = "candidate"
    route: Mapping[str, str] = field(default_factory=dict)
    fallbacks: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    metrics: Mapping[str, float] = field(default_factory=dict)
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "route", dict(self.route))
        object.__setattr__(self, "fallbacks",
                           {k: tuple(v) for k, v in dict(self.fallbacks).items()})
        object.__setattr__(self, "metrics", dict(self.metrics))
        object.__setattr__(self, "tags", tuple(self.tags))
        if not self.name:
            object.__setattr__(self, "name", self.id.replace("_", " ").capitalize())

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"id": self.id, "name": self.name,
                               "status": self.status, "route": dict(self.route)}
        if self.description:
            out["description"] = self.description
        if self.fallbacks:
            out["fallbacks"] = {k: list(v) for k, v in self.fallbacks.items()}
        if self.metrics:
            out["metrics"] = dict(self.metrics)
        if self.tags:
            out["tags"] = list(self.tags)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SolutionDefinition:
        return cls(id=data.get("id", ""), name=data.get("name", ""),
                   description=data.get("description", ""),
                   status=data.get("status", "candidate"),
                   route=dict(data.get("route") or {}),
                   fallbacks={k: tuple(v) for k, v in
                              (data.get("fallbacks") or {}).items()},
                   metrics=dict(data.get("metrics") or {}),
                   tags=tuple(data.get("tags") or ()))


@dataclass(frozen=True)
class FeedbackDefinition:
    """One typed learning signal.

    Typed and scoped because the alternative — a mess of unlabelled backward
    arrows — cannot be acted on. Every channel says who emits it, who may
    consume it, and what response it authorises.
    """
    id: str
    name: str = ""
    signal: str = ""
    scope: str = "candidate"
    producer: str = ""
    consumer: str = ""
    action: str = ""
    description: str = ""
    required: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            object.__setattr__(self, "name", self.id.rsplit(".", 1)[-1].replace("_", " "))

    def to_dict(self) -> dict:
        out = {"id": self.id, "name": self.name, "signal": self.signal,
               "scope": self.scope, "producer": self.producer,
               "consumer": self.consumer, "action": self.action,
               "description": self.description}
        if self.required:
            out["required"] = True    # type: ignore[assignment]
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FeedbackDefinition:
        return cls(id=data.get("id", ""), name=data.get("name", ""),
                   signal=data.get("signal", ""), scope=data.get("scope", "candidate"),
                   producer=data.get("producer", ""), consumer=data.get("consumer", ""),
                   action=data.get("action", ""),
                   description=data.get("description", ""),
                   required=bool(data.get("required", False)))


@dataclass(frozen=True)
class OptimizationObjective:
    metric: str
    direction: str = "maximize"
    weight: float = 1.0

    def to_dict(self) -> dict:
        return {"metric": self.metric, "direction": self.direction,
                "weight": self.weight}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OptimizationObjective:
        return cls(metric=data.get("metric", ""),
                   direction=data.get("direction", "maximize"),
                   weight=float(data.get("weight", 1.0)))


@dataclass(frozen=True)
class OptimizationProfile:
    """What "better" means, as data rather than as a rule buried in a viewer."""
    id: str
    name: str = ""
    strategy: str = "weighted"
    objectives: tuple[OptimizationObjective, ...] = ()
    minimum_evidence: int = 0
    exploration: float = 0.0
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "objectives", tuple(self.objectives))
        if not self.name:
            object.__setattr__(self, "name", self.id.rsplit(".", 1)[-1].capitalize())

    def score(self, metrics: Mapping[str, float],
              *, among: Sequence[Mapping[str, float]] = ()) -> float:
        """A weighted score, normalized within the set being compared.

        **Scoring is inherently comparative, and the first version of this was
        wrong.** It combined raw values directly, so a quality of 0.97 was
        weighed against a latency of 1420 — three orders of magnitude apart. The
        latency term swamped everything, and all four profiles produced the same
        ranking: "balanced", "quality first" and "speed first" were secretly all
        speed-only. Nothing failed; the numbers just meant nothing.

        Min-max within `among` puts every objective on the same 0..1 footing, so
        a weight of 0.5 actually buys half the decision. `among` defaults to the
        single item, which is degenerate — every present metric scores 1.0 —
        because one thing compared against itself has no ranking. Use `rank`.

        An unmeasured metric is skipped rather than scored zero: scoring it zero
        punishes anything new for being new, which is how a system stops
        exploring without anyone deciding that it should.
        """
        return self.score_within(metrics, self.ranges(list(among) or [metrics]))

    def ranges(self, pool: Sequence[Mapping[str, float]]
               ) -> dict[str, tuple[float, float]]:
        """The min and max of each objective across the comparison set.

        Computed once and reused. The first version recomputed them inside
        `score`, which made ranking O(n²): an exhaustive search over 122,472
        routes did fifteen billion comparisons and never finished. Nothing was
        *wrong* with the answer — it just could not be reached, which for a
        search strategy is the same thing.
        """
        spans: dict[str, tuple[float, float]] = {}
        for objective in self.objectives:
            values = [float(m[objective.metric]) for m in pool
                      if objective.metric in m]
            if values:
                spans[objective.metric] = (min(values), max(values))
        return spans

    def score_within(self, metrics: Mapping[str, float],
                     ranges: Mapping[str, tuple[float, float]]) -> float:
        """Score one item against precomputed ranges."""
        total, weight = 0.0, 0.0
        for objective in self.objectives:
            if objective.metric not in metrics or objective.metric not in ranges:
                continue
            low, high = ranges[objective.metric]
            span = high - low
            if span == 0:
                # Every candidate is identical on this metric, so it carries no
                # information about the choice. Skipping it keeps the score
                # meaning "how good, among the things that actually differed" —
                # and removes an asymmetry that made the number lie about
                # itself: a flat metric used to contribute its full weight when
                # maximized and nothing when minimized, which changed no
                # ranking but made two identical situations report different
                # scores depending on the direction someone wrote down.
                continue
            unit = (float(metrics[objective.metric]) - low) / span
            if objective.direction == "minimize":
                unit = 1.0 - unit
            total += objective.weight * unit
            weight += objective.weight
        return total / weight if weight else 0.0

    def rank(self, items: Mapping[str, Mapping[str, float]]
             ) -> list[tuple[str, float]]:
        """Every item scored against the others, best first.

        Ties break on the key so the order is stable — an optimizer that
        reshuffles equal candidates between runs makes its own evidence
        unattributable.
        """
        spans = self.ranges(list(items.values()))
        scored = [(key, self.score_within(metrics, spans))
                  for key, metrics in items.items()]
        return sorted(scored, key=lambda pair: (-pair[1], pair[0]))

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "strategy": self.strategy,
                "objectives": [o.to_dict() for o in self.objectives],
                "minimum_evidence": self.minimum_evidence,
                "exploration": self.exploration, "description": self.description}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OptimizationProfile:
        return cls(id=data.get("id", ""), name=data.get("name", ""),
                   strategy=data.get("strategy", "weighted"),
                   objectives=tuple(OptimizationObjective.from_dict(o)
                                    for o in data.get("objectives") or ()),
                   minimum_evidence=int(data.get("minimum_evidence", 0)),
                   exploration=float(data.get("exploration", 0.0)),
                   description=data.get("description", ""))


@dataclass(frozen=True)
class WorkbenchDefinition:
    """Everything a viewer, validator or planner needs, in one portable object."""
    title: str = "Universal graph solution studio"
    task: str = ""
    success: str = ""
    schema_version: str = SCHEMA_VERSION
    nodes: tuple[NodeManifest, ...] = ()
    candidates: tuple[NodeCandidate, ...] = ()
    stages: tuple[StageDefinition, ...] = ()
    solutions: tuple[SolutionDefinition, ...] = ()
    feedback_channels: tuple[FeedbackDefinition, ...] = ()
    optimization_profiles: tuple[OptimizationProfile, ...] = ()
    #: How the sub-steps connect. Empty means "a chain in declared order",
    #: which is what every workbench written before edges existed meant — so
    #: they keep working, and gain fan-out by naming edges rather than by being
    #: rewritten.
    edges: tuple[Edge, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("nodes", "candidates", "stages", "solutions",
                     "feedback_channels", "optimization_profiles", "edges"):
            object.__setattr__(self, name, tuple(getattr(self, name)))

    # --- lookups ------------------------------------------------------------

    @property
    def nodes_by_id(self) -> dict[str, NodeManifest]:
        return {n.id: n for n in self.nodes}

    @property
    def candidates_by_id(self) -> dict[str, NodeCandidate]:
        return {c.id: c for c in self.candidates}

    @property
    def leaf_stages(self) -> tuple[StageDefinition, ...]:
        """The ordered sub-steps a route actually chooses between.

        Top-level stages are how a task is *explained*; leaves are where the
        choices are. A route is one candidate per leaf, and every count that
        claims to describe the search space is computed over these.
        """
        return tuple(leaf for stage in self.stages for leaf in stage.leaves())

    def stage(self, stage_id: str) -> StageDefinition | None:
        """Any stage or sub-step, by id — the hierarchy is searched."""
        def walk(stages):
            for stage in stages:
                if stage.id == stage_id:
                    return stage
                found = walk(stage.substages)
                if found is not None:
                    return found
            return None
        return walk(self.stages)

    def parent_of(self, stage_id: str) -> StageDefinition | None:
        def walk(stages, parent=None):
            for stage in stages:
                if stage.id == stage_id:
                    return parent
                found = walk(stage.substages, stage)
                if found is not None or any(s.id == stage_id
                                            for s in stage.substages):
                    return found if found is not None else stage
            return None
        return walk(self.stages)

    # --- the numbers --------------------------------------------------------

    # --- the graph ----------------------------------------------------------

    def wiring(self) -> tuple[Edge, ...]:
        """The edges, inferred as a chain when none are declared.

        A chain is a DAG with one edge per adjacent pair. Inferring it keeps
        every existing workbench valid and means the general code path is the
        only code path — there is no "linear mode" to drift out of step.
        """
        if self.edges:
            return self.edges
        leaves = self.leaf_stages
        return tuple(Edge(source=a.id, target=b.id)
                     for a, b in zip(leaves, leaves[1:], strict=False))

    def successors(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {s.id: [] for s in self.leaf_stages}
        for edge in self.wiring():
            out.setdefault(edge.source, []).append(edge.target)
        return out

    def predecessors(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {s.id: [] for s in self.leaf_stages}
        for edge in self.wiring():
            out.setdefault(edge.target, []).append(edge.source)
        return out

    def sources(self) -> list[str]:
        into = self.predecessors()
        return [s.id for s in self.leaf_stages if not into.get(s.id)]

    def sinks(self) -> list[str]:
        out = self.successors()
        return [s.id for s in self.leaf_stages if not out.get(s.id)]

    def cycles(self) -> list[list[str]]:
        """Any cycle, as the path that closes it.

        A cycle is not a subtle problem — it means the plan cannot be ordered,
        so nothing can run — but it is invisible in a list of edges, which is
        why it is checked rather than assumed away by the drawing.
        """
        successors = self.successors()
        state: dict[str, int] = {}
        found: list[list[str]] = []

        def walk(node: str, path: list[str]) -> None:
            state[node] = 1
            for nxt in successors.get(node, []):
                if state.get(nxt) == 1:
                    found.append([*path[path.index(nxt):], node, nxt]
                                 if nxt in path else [node, nxt])
                elif state.get(nxt, 0) == 0:
                    walk(nxt, [*path, node])
            state[node] = 2

        for stage in self.leaf_stages:
            if state.get(stage.id, 0) == 0:
                walk(stage.id, [])
        return found

    def layers(self) -> list[list[str]]:
        """Sub-steps grouped by longest path from a source.

        This is what puts the columns back. Left-to-right was never the
        invariant — it is a *rendering* of a DAG, and a topological layering
        reproduces it for any shape, including the diamond a chain cannot hold.
        """
        depth: dict[str, int] = {s.id: 0 for s in self.leaf_stages}
        into = self.predecessors()
        for _ in range(len(depth)):
            changed = False
            for stage_id, parents in into.items():
                if not parents:
                    continue
                deepest = max(depth.get(p, 0) for p in parents) + 1
                if deepest > depth.get(stage_id, 0):
                    depth[stage_id] = deepest
                    changed = True
            if not changed:
                break
        out: dict[int, list[str]] = {}
        for stage in self.leaf_stages:       # declared order within a layer
            out.setdefault(depth.get(stage.id, 0), []).append(stage.id)
        return [out[k] for k in sorted(out)]

    @property
    def is_chain(self) -> bool:
        """True when every layer holds exactly one sub-step."""
        return all(len(layer) == 1 for layer in self.layers())

    def exclusive_paths(self) -> dict[str, dict[str, tuple[str, ...]]]:
        """For each branch, the stages only that path can reach.

        A branch names one output port and the others are not taken, so the
        stages behind an untaken port never run. Which stages those are is a
        reachability question: everything downstream of one port and of no
        other. Anything reachable from two ports is a re-join and runs either
        way, so it belongs to neither path.
        """
        successors = self.successors()
        branches = [s.id for s in self.leaf_stages if s.kind == "branch"]
        if not branches:
            return {}

        edges_by_port: dict[tuple[str, str], list[str]] = {}
        for edge in self.wiring():
            edges_by_port.setdefault((edge.source, edge.from_port), []).append(edge.target)

        def downstream(start: Iterable[str]) -> set[str]:
            seen, queue = set(), list(start)
            while queue:
                node = queue.pop()
                if node in seen:
                    continue
                seen.add(node)
                queue.extend(successors.get(node, ()))
            return seen

        out: dict[str, dict[str, tuple[str, ...]]] = {}
        for branch in branches:
            stage = self.stage(branch)
            ports = [p.name for p in (stage.outputs if stage else ())]
            reach = {port: downstream(edges_by_port.get((branch, port), []))
                     for port in ports}
            out[branch] = {}
            for port, reachable in reach.items():
                others = set().union(*(r for p, r in reach.items() if p != port)) \
                    if len(reach) > 1 else set()
                out[branch][port] = tuple(sorted(reachable - others))
        return out

    def route_count(self) -> int:
        """Complete primary routes, over **leaves**. The product, not the sum.

        Counting over coarse stages understates this badly, and the size of the
        understatement is the argument for decomposing at all: the demonstration
        reads as 32,864,832 routes across six stages and 4.2 trillion across the
        fifteen sub-steps those stages are actually made of. Same task, same
        registry — the coarse view was hiding almost all of the choices.

        Branches are counted as a **sum over paths**, not a product. Only one
        path can run, so two routes that differ solely in the candidates behind
        an untaken port are the same computation and counting both is a lie —
        the kind this repository spends its time objecting to elsewhere. With no
        branch present this is the plain product it always was, so every number
        published before is unchanged.
        """
        leaves = self.leaf_stages
        if not leaves:
            return 0

        widths = {stage.id: max(len(stage.candidates), 0) for stage in leaves}
        exclusive = self.exclusive_paths()
        spoken_for = {sid for paths in exclusive.values()
                      for path in paths.values() for sid in path}

        total = 1
        for stage in leaves:
            if stage.id not in spoken_for:
                total *= widths[stage.id]

        for paths in exclusive.values():
            alternatives = 0
            for path in paths.values():
                product = 1
                for sid in path:
                    product *= widths.get(sid, 1)
                alternatives += product
            total *= max(alternatives, 1)
        return total

    def coarse_route_count(self) -> int:
        """What the count looks like if each stage is drawn as one decision.

        Every candidate in a stage pooled into a single choice — which is what a
        coarse diagram is implicitly claiming. Kept so the gap between that and
        the real number can be shown rather than asserted.
        """
        total = 1
        for stage in self.stages:
            pooled = sum(len(leaf.candidates) for leaf in stage.leaves())
            total *= max(pooled, 0)
        return total if self.stages else 0

    def transition_count(self) -> int:
        """Candidate-level connections across every edge in the graph.

        Per edge rather than per adjacent pair: in a diamond the two branches
        both connect to the join, and a sequential count would miss one of them
        entirely.
        """
        by_id = {s.id: s for s in self.leaf_stages}
        total = 0
        for edge in self.wiring():
            left, right = by_id.get(edge.source), by_id.get(edge.target)
            if left and right:
                total += len(left.candidates) * len(right.candidates)
        return total

    def summary(self) -> str:
        leaves = self.leaf_stages
        shape = (f"{len(self.stages)} stages / {len(leaves)} sub-steps"
                 if len(leaves) != len(self.stages) else f"{len(leaves)} stages")
        return (f"{shape} · {len(self.nodes)} definitions · "
                f"{len(self.candidates)} atomic candidates · "
                f"{self.route_count():,} complete routes · "
                f"{self.transition_count():,} adjacent transitions")

    # --- validation ---------------------------------------------------------

    def validate(self) -> list[str]:
        """Every rule from the specification, reported all at once."""
        bad: list[str] = []
        nodes = self.nodes_by_id
        candidates = self.candidates_by_id

        if self.schema_version != SCHEMA_VERSION:
            bad.append(f"workbench schema_version {self.schema_version!r} "
                       f"!= {SCHEMA_VERSION!r}")

        for manifest in self.nodes:
            bad += manifest.validate()
        for dupe in _dupe_ids([n.id for n in self.nodes]):
            bad.append(f"two node definitions share the id {dupe!r}")

        # --- candidates
        for dupe in _dupe_ids([c.id for c in self.candidates]):
            bad.append(f"two candidates share the id {dupe!r}")
        for cand in self.candidates:
            owner = nodes.get(cand.node_id)
            if owner is None:
                bad.append(f"candidate {cand.id!r} refers to unknown node "
                           f"{cand.node_id!r}")
                continue
            by_name = {p.name: p for p in owner.parameters}
            for key, value in cand.params.items():
                param = by_name.get(key)
                if param is None:
                    bad.append(f"candidate {cand.id!r} binds unknown parameter {key!r}")
                elif param.choices and value not in param.choices:
                    bad.append(f"candidate {cand.id!r} sets {key}={value!r}, "
                               f"which is not one of its choices")
            for param in owner.parameters:
                if param.required and param.name not in cand.params:
                    bad.append(f"candidate {cand.id!r} leaves required parameter "
                               f"{param.name!r} unbound")

        # --- stages
        every = _walk(self.stages)
        for dupe in _dupe_ids([s.id for s in every]):
            bad.append(f"two stages share the id {dupe!r}")

        for stage in every:
            if stage.is_composite and stage.candidates:
                bad.append(f"stage {stage.id!r} has both sub-steps and its own "
                           f"candidates — a stage is either a leaf that holds "
                           f"candidates or a composite that holds sub-steps, "
                           f"never both, or 'one choice per stage' is ambiguous")
            if stage.is_composite:
                leaves = stage.leaves()
                if stage.input_type and leaves[0].input_type \
                        and stage.input_type != leaves[0].input_type:
                    bad.append(f"composite stage {stage.id!r} consumes "
                               f"{stage.input_type!r} but its first sub-step "
                               f"{leaves[0].id!r} consumes {leaves[0].input_type!r}")
                if stage.output_type and leaves[-1].output_type \
                        and stage.output_type != leaves[-1].output_type:
                    bad.append(f"composite stage {stage.id!r} produces "
                               f"{stage.output_type!r} but its last sub-step "
                               f"{leaves[-1].id!r} produces "
                               f"{leaves[-1].output_type!r}")

        for stage in self.leaf_stages:
            if not stage.candidates:
                bad.append(f"stage {stage.id!r} has no candidates — nothing could "
                           f"perform it")
            for cid in stage.candidates:
                admitted = candidates.get(cid)
                if admitted is None:
                    bad.append(f"stage {stage.id!r} lists unknown candidate {cid!r}")
                    continue
                owner = nodes.get(admitted.node_id)
                if owner and not stage.eligible(owner):
                    bad.append(f"stage {stage.id!r} admits {cid!r}, which cannot "
                               f"satisfy its contract "
                               f"({stage.input_type} -> {stage.output_type}, "
                               f"needs {', '.join(stage.required_capabilities)})")
            # Completeness: a stage that quietly omits a compatible candidate is
            # a picture of somebody's opinion, not of what is possible.
            complete = set(stage.with_discovered_candidates(
                self.nodes, self.candidates).candidates)
            missing = complete - set(stage.candidates)
            if missing:
                bad.append(f"stage {stage.id!r} omits {len(missing)} compatible "
                           f"candidate(s), e.g. {sorted(missing)[0]!r} — a stage "
                           f"must show everything that could perform it")
            if not stage.optional:
                for cid in stage.candidates:
                    admitted = candidates.get(cid)
                    if admitted and "pass-through" in admitted.tags:
                        bad.append(f"required stage {stage.id!r} admits the "
                                   f"pass-through candidate {cid!r}")

        # --- the graph: every edge, not every adjacent pair
        lattice = _types.lattice_from(self.nodes)
        by_id = {s.id: s for s in self.leaf_stages}
        for edge in self.wiring():
            left, right = by_id.get(edge.source), by_id.get(edge.target)
            if left is None:
                bad.append(f"edge {edge} starts at unknown sub-step "
                           f"{edge.source!r}")
                continue
            if right is None:
                bad.append(f"edge {edge} ends at unknown sub-step {edge.target!r}")
                continue
            produced = left.port(edge.from_port, outgoing=True)
            consumed = right.port(edge.to_port, outgoing=False)
            if produced is None:
                bad.append(f"edge {edge}: {left.id!r} has no output port "
                           f"{edge.from_port or '(single)'!r} — it declares "
                           f"{[p.name for p in left.outputs] or 'none'}")
                continue
            if consumed is None:
                bad.append(f"edge {edge}: {right.id!r} has no input port "
                           f"{edge.to_port or '(single)'!r} — it declares "
                           f"{[p.name for p in right.inputs] or 'none'}")
                continue
            mismatch = _types.check(produced, consumed, lattice)
            if mismatch is not None:
                bad.append(f"edge {edge}: {mismatch.reason} — "
                           f"{mismatch.fix or 'insert an adapter rather than coercing'}")

        for cycle in self.cycles():
            bad.append("the graph has a cycle: " + " -> ".join(cycle)
                       + " — a plan with a cycle cannot be ordered, so nothing "
                         "can run")

        # Every required input port must be fed by something, or the stage is a
        # source. A port nobody writes to is a stage that cannot start, and it
        # is invisible in a list of edges.
        fed: dict[str, set[str]] = {s.id: set() for s in self.leaf_stages}
        for edge in self.wiring():
            right = by_id.get(edge.target)
            if right is None:
                continue
            port = right.port(edge.to_port, outgoing=False)
            if port is not None:
                fed[edge.target].add(port.name)
        entry = set(self.sources())
        for stage in self.leaf_stages:
            if stage.id in entry:
                continue
            for port in stage.inputs:
                if port.required and port.name not in fed.get(stage.id, set()):
                    bad.append(f"sub-step {stage.id!r} needs input "
                               f"{port.name!r} ({port.type}) and no edge "
                               f"supplies it")

        # --- routes: one choice per leaf, not per top-level stage
        stage_ids = [s.id for s in self.leaf_stages]
        for solution in self.solutions:
            for dupe in _dupe_ids([solution.id]):
                bad.append(f"duplicate solution id {dupe!r}")
            for sid in stage_ids:
                if sid not in solution.route:
                    bad.append(f"route {solution.id!r} is incomplete: no choice "
                               f"for stage {sid!r}")
            for sid, cid in solution.route.items():
                chosen = self.stage(sid)
                if chosen is None:
                    bad.append(f"route {solution.id!r} names unknown stage {sid!r}")
                elif cid not in chosen.candidates:
                    bad.append(f"route {solution.id!r} picks {cid!r} for stage "
                               f"{sid!r}, which does not admit it")
            for sid, alts in solution.fallbacks.items():
                target = self.stage(sid)
                if target is None:
                    bad.append(f"route {solution.id!r} has fallbacks for unknown "
                               f"stage {sid!r}")
                    continue
                for alt in alts:
                    if alt not in target.candidates:
                        bad.append(f"route {solution.id!r} falls back to {alt!r}, "
                                   f"which stage {sid!r} does not admit — a "
                                   f"fallback stays inside its stage")
                if solution.route.get(sid) in alts:
                    bad.append(f"route {solution.id!r} lists its own primary as a "
                               f"fallback for {sid!r}")
                for dupe in _dupe_ids(list(alts)):
                    bad.append(f"route {solution.id!r} lists fallback {dupe!r} twice")
            for key, value in solution.metrics.items():
                if not isinstance(value, (int, float)) or value != value \
                        or value in (float("inf"), float("-inf")):
                    bad.append(f"route {solution.id!r} has a non-finite metric "
                               f"{key}={value!r}")
        for dupe in _dupe_ids([s.id for s in self.solutions]):
            bad.append(f"two solutions share the id {dupe!r}")

        # --- feedback and optimization
        for dupe in _dupe_ids([f.id for f in self.feedback_channels]):
            bad.append(f"two feedback channels share the id {dupe!r}")
        for channel in self.feedback_channels:
            if channel.scope not in SCOPES:
                bad.append(f"feedback {channel.id!r} has unknown scope "
                           f"{channel.scope!r} (known: {', '.join(SCOPES)})")
            if not channel.signal:
                bad.append(f"feedback {channel.id!r} declares no signal type")
            if not channel.action:
                bad.append(f"feedback {channel.id!r} authorises no action, so "
                           f"nothing can consume it")

        for dupe in _dupe_ids([p.id for p in self.optimization_profiles]):
            bad.append(f"two optimization profiles share the id {dupe!r}")
        for profile in self.optimization_profiles:
            if not profile.objectives:
                bad.append(f"profile {profile.id!r} has no objectives")
            if not 0.0 <= profile.exploration <= 1.0:
                bad.append(f"profile {profile.id!r} has exploration "
                           f"{profile.exploration!r} outside 0..1")
            for objective in profile.objectives:
                if objective.direction not in DIRECTIONS:
                    bad.append(f"profile {profile.id!r}: objective "
                               f"{objective.metric!r} has direction "
                               f"{objective.direction!r}, not one of "
                               f"{', '.join(DIRECTIONS)}")
                if objective.weight <= 0:
                    bad.append(f"profile {profile.id!r}: objective "
                               f"{objective.metric!r} has non-positive weight")
        for stage in self.leaf_stages:
            if stage.kind not in STAGE_KINDS:
                bad.append(f"stage {stage.id!r} has unknown kind {stage.kind!r} "
                           f"(known: {', '.join(STAGE_KINDS)})")
            if stage.kind == "branch" and len(stage.outputs) < 2:
                bad.append(f"stage {stage.id!r} is a branch with "
                           f"{len(stage.outputs)} output port(s) — a branch that "
                           f"can only go one way is not a branch")
            if stage.kind == "map" and len(stage.inputs) != 1:
                bad.append(f"stage {stage.id!r} is a map with "
                           f"{len(stage.inputs)} input port(s) — a map runs over "
                           f"exactly one collection")

        return bad

    def assert_valid(self) -> WorkbenchDefinition:
        problems = self.validate()
        if problems:
            raise ValueError("invalid workbench:\n  " + "\n  ".join(problems))
        return self

    # --- wire format --------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "title": self.title,
            "task": self.task,
            "success": self.success,
            "nodes": [n.to_dict() for n in self.nodes],
            "candidates": [c.to_dict() for c in self.candidates],
            "stages": [s.to_dict() for s in self.stages],
            "solutions": [s.to_dict() for s in self.solutions],
            "edges": [e.to_dict() for e in self.wiring()],
            "feedback_channels": [f.to_dict() for f in self.feedback_channels],
            "optimization_profiles": [p.to_dict() for p in self.optimization_profiles],
            "metadata": {**dict(self.metadata),
                         "route_count": self.route_count(),
                         "transition_count": self.transition_count(),
                         "stage_count": len(self.stages),
                         "leaf_count": len(self.leaf_stages),
                         "layers": len(self.layers()),
                         "is_chain": self.is_chain},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WorkbenchDefinition:
        # "planes" is what the first version of this called ordered stages. It
        # is still accepted so older exports keep loading; new output always
        # uses "stages", which does not collide with configuration planes.
        stages = data.get("stages") or data.get("planes") or ()
        return cls(
            title=data.get("title", ""), task=data.get("task", ""),
            success=data.get("success", ""),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            nodes=tuple(NodeManifest.from_dict(n) for n in data.get("nodes") or ()),
            candidates=tuple(NodeCandidate.from_dict(c)
                             for c in data.get("candidates") or ()),
            stages=tuple(StageDefinition.from_dict(s) for s in stages),
            solutions=tuple(SolutionDefinition.from_dict(s)
                            for s in data.get("solutions") or ()),
            edges=tuple(Edge.from_dict(e) for e in data.get("edges") or ()),
            feedback_channels=tuple(FeedbackDefinition.from_dict(f)
                                    for f in data.get("feedback_channels") or ()),
            optimization_profiles=tuple(OptimizationProfile.from_dict(p)
                                        for p in data.get("optimization_profiles") or ()),
            metadata=dict(data.get("metadata") or {}))

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def load(cls, path: str | pathlib.Path) -> WorkbenchDefinition:
        return cls.from_dict(json.loads(pathlib.Path(path).read_text(encoding="utf-8")))

    # --- rendering ----------------------------------------------------------

    def write_json(self, path: str | pathlib.Path) -> str:
        pathlib.Path(path).write_text(self.to_json(), encoding="utf-8")
        return str(path)

    def write_html(self, path: str | pathlib.Path, view: str = "candidates") -> str:
        """One self-contained offline file.

        Self-contained is a requirement rather than a nicety: this gets opened
        from a laptop, an artifact viewer, a CI artifact and a notebook output
        cell, and anything fetched from a CDN is missing in at least two of
        those.
        """
        from browsergraph.studio import render

        pathlib.Path(path).write_text(render(self, view=view), encoding="utf-8")
        return str(path)

    def write_suite(self, directory: str | pathlib.Path) -> list[str]:
        """The same data, one file per projection, plus the data itself."""
        from browsergraph.studio import VIEWS, render

        out_dir = pathlib.Path(directory)
        out_dir.mkdir(parents=True, exist_ok=True)
        written = [self.write_json(out_dir / "workbench.json")]
        for view, filename in VIEWS.items():
            target = out_dir / filename
            target.write_text(render(self, view=view), encoding="utf-8")
            written.append(str(target))
        (out_dir / "index.html").write_text(
            render(self, view="candidates"), encoding="utf-8")
        written.append(str(out_dir / "index.html"))
        return written


def _walk(stages: Sequence[StageDefinition]) -> list[StageDefinition]:
    """Every stage in the tree, parents included."""
    out: list[StageDefinition] = []
    for stage in stages:
        out.append(stage)
        out.extend(_walk(stage.substages))
    return out


def _dupe_ids(items: Sequence[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        if item in seen and item not in out:
            out.append(item)
        seen.add(item)
    return out
