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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from browsergraph.manifest import SCHEMA_VERSION, NodeManifest

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
    variant_axes: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("variant_axes", "required_capabilities", "candidates"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not self.name:
            object.__setattr__(self, "name", self.id.replace("_", " ").capitalize())

    def eligible(self, manifest: NodeManifest) -> bool:
        """Could this definition legally perform this stage?

        Capability and both port contracts — a node that can `verify` but cannot
        accept what the previous stage produces is not a candidate here, and
        finding that out at run time is finding it out too late.
        """
        if not manifest.can(*self.required_capabilities):
            return False
        if self.input_type and not manifest.accepts(self.input_type):
            return False
        if self.output_type and not manifest.produces(self.output_type):
            return False
        return True

    def with_discovered_candidates(
            self, manifests: Sequence[NodeManifest],
            candidates: Sequence[NodeCandidate]) -> StageDefinition:
        """Every compatible candidate in the registry, not a chosen few."""
        by_id = {m.id: m for m in manifests}
        found = tuple(c.id for c in candidates
                      if c.node_id in by_id and self.eligible(by_id[c.node_id]))
        return replace(self, candidates=found)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"id": self.id, "name": self.name,
                               "description": self.description,
                               "input_type": self.input_type,
                               "output_type": self.output_type,
                               "success": self.success}
        if self.optional:
            out["optional"] = True
        for key in ("variant_axes", "required_capabilities", "candidates"):
            if getattr(self, key):
                out[key] = list(getattr(self, key))
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StageDefinition:
        return cls(id=data.get("id", ""), name=data.get("name", ""),
                   description=data.get("description", ""),
                   input_type=data.get("input_type", ""),
                   output_type=data.get("output_type", ""),
                   success=data.get("success", ""),
                   optional=bool(data.get("optional", False)),
                   variant_axes=tuple(data.get("variant_axes") or ()),
                   required_capabilities=tuple(data.get("required_capabilities") or ()),
                   candidates=tuple(data.get("candidates") or ()))


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

    def score(self, metrics: Mapping[str, float]) -> float:
        """A weighted score, with an unmeasured metric left out rather than
        counted as zero.

        Scoring a missing measurement as zero silently punishes anything new for
        being new, which is how a system stops exploring without anyone deciding
        that it should.
        """
        total, weight = 0.0, 0.0
        for objective in self.objectives:
            if objective.metric not in metrics:
                continue
            value = float(metrics[objective.metric])
            total += objective.weight * (value if objective.direction == "maximize"
                                         else -value)
            weight += objective.weight
        return total / weight if weight else 0.0

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
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("nodes", "candidates", "stages", "solutions",
                     "feedback_channels", "optimization_profiles"):
            object.__setattr__(self, name, tuple(getattr(self, name)))

    # --- lookups ------------------------------------------------------------

    @property
    def nodes_by_id(self) -> dict[str, NodeManifest]:
        return {n.id: n for n in self.nodes}

    @property
    def candidates_by_id(self) -> dict[str, NodeCandidate]:
        return {c.id: c for c in self.candidates}

    def stage(self, stage_id: str) -> StageDefinition | None:
        return next((s for s in self.stages if s.id == stage_id), None)

    # --- the numbers --------------------------------------------------------

    def route_count(self) -> int:
        """Complete primary routes. The product, not the sum.

        Worth printing somewhere visible: it is the difference between "we
        support several options" and the actual size of the space a search is
        working in.
        """
        total = 1
        for stage in self.stages:
            total *= max(len(stage.candidates), 0)
        return total if self.stages else 0

    def transition_count(self) -> int:
        """Edges between adjacent stages — what the network view can draw."""
        return sum(len(a.candidates) * len(b.candidates)
                   for a, b in zip(self.stages, self.stages[1:], strict=False))

    def summary(self) -> str:
        return (f"{len(self.stages)} stages · {len(self.nodes)} definitions · "
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
        for dupe in _dupe_ids([s.id for s in self.stages]):
            bad.append(f"two stages share the id {dupe!r}")
        for stage in self.stages:
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

        # --- adjacent contracts
        for before, after in zip(self.stages, self.stages[1:], strict=False):
            if before.output_type and after.input_type \
                    and before.output_type != after.input_type:
                bad.append(f"stage {before.id!r} produces {before.output_type!r} "
                           f"but {after.id!r} consumes {after.input_type!r} — "
                           f"insert an adapter stage rather than coercing")

        # --- routes
        stage_ids = [s.id for s in self.stages]
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
            "feedback_channels": [f.to_dict() for f in self.feedback_channels],
            "optimization_profiles": [p.to_dict() for p in self.optimization_profiles],
            "metadata": {**dict(self.metadata),
                         "route_count": self.route_count(),
                         "transition_count": self.transition_count()},
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


def _dupe_ids(items: Sequence[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        if item in seen and item not in out:
            out.append(item)
        seen.add(item)
    return out
