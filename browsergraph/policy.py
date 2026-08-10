"""What a task is allowed to do — checked before anything is scored.

The ordering rule is the whole point, and it is easy to get backwards: **hard
constraints precede soft scoring**. A candidate that lacks a required permission
is not a low-scoring candidate, it is an unavailable one, and no objective
weighting may promote it. Conflating the two produces a system that will
cheerfully recommend something it is not permitted to run, and then fail at
execution having already reported a plan.

The second rule is that a blocked candidate stays **visible**, with its reason.
Filtering it out silently answers "what could perform this step" with "what the
current policy left", and nothing on screen distinguishes the two. Every gate
here returns a verdict for every candidate rather than a shortened list.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from browsergraph.manifest import NodeManifest
    from browsergraph.workbench import StageDefinition, WorkbenchDefinition


@dataclass(frozen=True)
class Verdict:
    """One candidate, and whether this task may use it."""
    candidate_id: str
    ok: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


@dataclass(frozen=True)
class Policy:
    """The authority, budget and runtime this task actually has.

    `permissions` is what has been *granted*. A node asking for
    `database:read` is satisfied by a grant of `database:read` or of the whole
    `database` family — but never by silence. The default grants nothing, which
    is the right default for a thing that can drive browsers and spend money:
    an empty policy blocks everything with a stated reason rather than
    permitting everything by omission.
    """
    permissions: frozenset[str] = frozenset()
    allow_external_effects: bool = True
    deterministic_only: bool = False
    max_cost_usd: float | None = None
    max_latency_ms: float | None = None
    minimum_evidence: int = 0
    denied_nodes: frozenset[str] = frozenset()
    name: str = "policy"

    @classmethod
    def permissive(cls, **over) -> Policy:
        """Everything the demonstration knows how to ask for.

        For exploring the space rather than running it. Named so that no one
        reaches for it by accident.
        """
        # `over` wins, so `permissive(name="no-llm")` and
        # `permissive(permissions=...)` both work instead of colliding with the
        # defaults this sets.
        defaults = {
            "permissions": frozenset({
                "filesystem", "filesystem:read", "filesystem:write", "network",
                "browser", "llm", "human", "database", "database:read",
                "database:write", "sandbox"}),
            "name": "permissive",
        }
        return cls(**{**defaults, **over})

    def grants(self, permission: str) -> bool:
        family = permission.split(":")[0]
        return permission in self.permissions or family in self.permissions

    def blocks(self, manifest: NodeManifest) -> str:
        """Why this task may not use this node — "" if it may.

        Returns the *first* blocking reason rather than all of them: a person
        reading a disabled candidate wants to know what to change, and a list of
        five reasons for one node is not more actionable than one.
        """
        if manifest.id in self.denied_nodes:
            return "denied by policy"
        missing = [p for p in manifest.permissions if not self.grants(p)]
        if missing:
            return f"needs {', '.join(sorted(missing))} — not granted"
        if not self.allow_external_effects:
            external = [e for e in manifest.effects if e.startswith("external")]
            if external:
                return f"changes external state ({external[0]}) — not permitted"
        if self.deterministic_only and manifest.runtime.get("deterministic") is False:
            return "not deterministic"
        metrics = manifest.metrics or {}
        if self.max_cost_usd is not None:
            cost = metrics.get("cost_usd")
            if cost is not None and float(cost) > self.max_cost_usd:
                return f"costs {cost} > budget {self.max_cost_usd}"
        if self.max_latency_ms is not None:
            latency = metrics.get("latency_ms")
            if latency is not None and float(latency) > self.max_latency_ms:
                return f"takes {latency}ms > budget {self.max_latency_ms}ms"
        if self.minimum_evidence:
            evidence = int(metrics.get("evidence", 0) or 0)
            if evidence < self.minimum_evidence:
                return (f"only {evidence} run(s) of evidence, "
                        f"{self.minimum_evidence} required")
        return ""

    def to_dict(self) -> dict:
        return {"name": self.name, "permissions": sorted(self.permissions),
                "allow_external_effects": self.allow_external_effects,
                "deterministic_only": self.deterministic_only,
                "max_cost_usd": self.max_cost_usd,
                "max_latency_ms": self.max_latency_ms,
                "minimum_evidence": self.minimum_evidence,
                "denied_nodes": sorted(self.denied_nodes)}


@dataclass
class Gate:
    """Every candidate on a stage, judged. Nothing is dropped."""
    stage_id: str
    verdicts: tuple[Verdict, ...] = ()

    @property
    def eligible(self) -> tuple[str, ...]:
        return tuple(v.candidate_id for v in self.verdicts if v.ok)

    @property
    def blocked(self) -> tuple[Verdict, ...]:
        return tuple(v for v in self.verdicts if not v.ok)

    def reason(self, candidate_id: str) -> str:
        return next((v.reason for v in self.verdicts
                     if v.candidate_id == candidate_id), "")

    def summary(self) -> str:
        return (f"{self.stage_id}: {len(self.eligible)} of "
                f"{len(self.verdicts)} candidates eligible")


def gate_stage(workbench: WorkbenchDefinition, stage: StageDefinition,
               policy: Policy) -> Gate:
    """Judge every candidate on one **leaf** sub-step.

    A composite holds sub-steps rather than candidates, so gating one is a
    category error. It used to return an empty verdict list, which looked
    exactly like a policy that blocked everything.
    """
    if stage.is_composite:
        raise ValueError(
            f"stage {stage.id!r} is a composite of "
            f"{', '.join(s.id for s in stage.substages)} — gate its sub-steps, "
            f"or use gate_all() which walks the leaves for you")
    nodes = workbench.nodes_by_id
    candidates = workbench.candidates_by_id
    verdicts = []
    for cid in stage.candidates:
        candidate = candidates.get(cid)
        manifest = nodes.get(candidate.node_id) if candidate else None
        if manifest is None:
            verdicts.append(Verdict(cid, False, "no manifest for this candidate"))
            continue
        reason = policy.blocks(manifest)
        verdicts.append(Verdict(cid, not reason, reason))
    return Gate(stage_id=stage.id, verdicts=tuple(verdicts))


def gate_all(workbench: WorkbenchDefinition, policy: Policy) -> dict[str, Gate]:
    """Gate the **leaves**, because that is where candidates live.

    Walking the top-level stages returned nothing eligible the moment stages
    grew sub-steps — every stage reported "0 of 0 candidates", which reads like
    a brutally strict policy rather than a traversal bug.
    """
    return {stage.id: gate_stage(workbench, stage, policy)
            for stage in workbench.leaf_stages}


@dataclass
class PolicyReport:
    """What a policy does to the whole space, as numbers rather than a feeling."""
    policy: Policy
    gates: Mapping[str, Gate] = field(default_factory=dict)

    @property
    def reachable_routes(self) -> int:
        """Complete routes still available. Zero means the policy is a wall."""
        total = 1
        for gate in self.gates.values():
            total *= len(gate.eligible)
        return total if self.gates else 0

    def dead_stages(self) -> list[str]:
        """Stages with nothing left. The useful diagnosis when nothing runs."""
        return [sid for sid, gate in self.gates.items() if not gate.eligible]

    def text(self) -> str:
        lines = [f"policy {self.policy.name!r}"]
        for gate in self.gates.values():
            lines.append("  " + gate.summary())
            for verdict in gate.blocked[:3]:
                lines.append(f"      {verdict.candidate_id}: {verdict.reason}")
            if len(gate.blocked) > 3:
                lines.append(f"      ... and {len(gate.blocked) - 3} more blocked")
        dead = self.dead_stages()
        lines.append(f"  {self.reachable_routes:,} complete routes remain"
                     + (f" — but {', '.join(dead)} has no eligible candidate, "
                        f"so nothing can run" if dead else ""))
        return "\n".join(lines)


def review(workbench: WorkbenchDefinition, policy: Policy) -> PolicyReport:
    return PolicyReport(policy=policy, gates=gate_all(workbench, policy))


def check_route(workbench: WorkbenchDefinition, route: Mapping[str, str],
                policy: Policy) -> list[str]:
    """Every policy problem with a chosen route.

    Used to re-gate a proposal before it runs. A route that was legal when it
    was proposed can stop being legal — a budget shrinks, an authority is
    revoked, evidence goes stale — so the check happens again at the point of
    execution rather than once at planning time.
    """
    problems = []
    gates = gate_all(workbench, policy)
    for stage in workbench.leaf_stages:
        cid = route.get(stage.id)
        if not cid:
            problems.append(f"{stage.id}: no candidate chosen")
            continue
        gate = gates[stage.id]
        reason = gate.reason(cid)
        if reason:
            name = workbench.candidates_by_id.get(cid)
            problems.append(f"{stage.id}: {name.name if name else cid} — {reason}")
    return problems


def aggregate(workbench: WorkbenchDefinition, route: Mapping[str, str]
              ) -> dict[str, float]:
    """What a whole route costs, from its parts.

    Latency and cost **add** along a chain. Quality **compounds** — it is the
    product, not the mean, because a route is only as good as the joint
    probability that every step did its job. Averaging lets one excellent stage
    hide a step that fails half the time, which is the same arithmetic mistake
    as scoring a route by its best link.
    """
    nodes = workbench.nodes_by_id
    candidates = workbench.candidates_by_id
    quality, latency, cost = 1.0, 0.0, 0.0
    seen = False
    for stage in workbench.leaf_stages:
        candidate = candidates.get(route.get(stage.id, ""))
        manifest = nodes.get(candidate.node_id) if candidate else None
        if manifest is None:
            continue
        metrics = manifest.metrics or {}
        seen = True
        quality *= float(metrics.get("quality", 1.0))
        latency += float(metrics.get("latency_ms", 0.0))
        cost += float(metrics.get("cost_usd", 0.0))
    if not seen:
        return {}
    return {"quality": quality, "latency_ms": latency, "cost_usd": cost}


def route_permissions(workbench: WorkbenchDefinition, route: Mapping[str, str]
                      ) -> tuple[list[str], list[str]]:
    """Every authority a route needs and every effect it may have.

    Worth surfacing next to a proposal: the per-candidate view makes each
    permission look small, and the union is what actually has to be granted.
    """
    nodes = workbench.nodes_by_id
    candidates = workbench.candidates_by_id
    permissions: set[str] = set()
    effects: set[str] = set()
    for cid in route.values():
        candidate = candidates.get(cid)
        manifest = nodes.get(candidate.node_id) if candidate else None
        if manifest:
            permissions |= set(manifest.permissions)
            effects |= set(manifest.effects)
    return sorted(permissions), sorted(effects)


def sequence() -> Sequence[str]:
    """The order optimization must follow. Stated so it can be pointed at."""
    return ("registry discovery", "parameter binding validation",
            "technical contract validation",
            "permission, effect, dependency, runtime and resource policy",
            "evidence sufficiency and freshness", "objective scoring",
            "explore or exploit", "route proposal", "full route revalidation",
            "execution", "independent verification", "receipt update")
