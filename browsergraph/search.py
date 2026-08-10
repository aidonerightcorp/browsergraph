"""Choosing a route out of thirty-two million, and saying how.

Three strategies, because the honest answer depends on the size of the space:

* **exhaustive** — when the space is small enough to enumerate, enumerate it.
  Then "best" means best, not best-found, and that distinction is worth the
  compute whenever it is affordable.
* **beam** — keep the best `k` partial routes at each stage. Necessary because
  route metrics do not decompose: quality compounds, so a stage's contribution
  depends on what the rest of the route already spent.
* **greedy** — the best eligible candidate per stage, independently. Fast, and
  genuinely optimal only when the objectives are separable, which they are not
  here. It is kept as the cheap baseline, and the tests show where it loses.

Every result reports how much of the space it looked at. A search that examined
40 of 32,864,832 routes and announces "the best route" without saying so is
making a claim it did not earn — and the number is free to carry.
"""
from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from browsergraph.policy import Policy, aggregate, gate_all, route_permissions
from browsergraph.workbench import OptimizationProfile, WorkbenchDefinition

#: Enumerate exhaustively below this many complete routes. Above it, beam.
EXHAUSTIVE_LIMIT = 200_000

STRATEGIES = ("auto", "exhaustive", "beam", "greedy")


@dataclass
class Proposal:
    """A complete route, and an honest account of how it was found."""
    route: dict[str, str] = field(default_factory=dict)
    score: float = 0.0
    metrics: dict[str, float] = field(default_factory=dict)
    strategy: str = ""
    profile_id: str = ""
    examined: int = 0
    total: int = 0
    percentile: float = 0.0
    eligible_total: int = 0
    blocked: dict[str, int] = field(default_factory=dict)
    permissions: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.route) and not self.problems

    @property
    def coverage(self) -> float:
        """The fraction of the *eligible* space actually evaluated."""
        return self.examined / self.eligible_total if self.eligible_total else 0.0

    def to_dict(self) -> dict:
        return {"route": dict(self.route), "score": round(self.score, 6),
                "percentile": round(self.percentile, 4),
                "metrics": dict(self.metrics), "strategy": self.strategy,
                "profile": self.profile_id, "examined": self.examined,
                "total": self.total, "eligible_total": self.eligible_total,
                "blocked": dict(self.blocked),
                "permissions": list(self.permissions),
                "effects": list(self.effects),
                "problems": list(self.problems), "notes": list(self.notes)}

    def text(self, workbench: WorkbenchDefinition) -> str:
        names = workbench.candidates_by_id
        lines = [f"{self.strategy} search under {self.profile_id!r} — score "
                 f"{self.score:.3f}, better than {self.percentile:.1%} of the "
                 f"reference sample"]
        for stage in workbench.leaf_stages:
            cid = self.route.get(stage.id, "")
            chosen = names.get(cid)
            blocked = self.blocked.get(stage.id, 0)
            lines.append(f"  {stage.name:<28} {chosen.name if chosen else '—'}"
                         + (f"   ({blocked} blocked)" if blocked else ""))
        if self.metrics:
            lines.append(f"  route metrics: quality {self.metrics['quality']:.3f} "
                         f"(compounded) · {self.metrics['latency_ms']:,.0f}ms · "
                         f"${self.metrics['cost_usd']:.4f}")
        lines.append(f"  examined {self.examined:,} of {self.eligible_total:,} "
                     f"eligible routes ({self.coverage:.1%}) — "
                     f"{self.total:,} exist before policy")
        if self.permissions:
            lines.append("  needs: " + ", ".join(self.permissions))
        for note in self.notes:
            lines.append("  note: " + note)
        for problem in self.problems:
            lines.append("  PROBLEM: " + problem)
        return "\n".join(lines)


#: Routes in the fixed reference sample used to normalize reported scores.
REFERENCE = 513


def _reference_sample(workbench: WorkbenchDefinition, stages, eligible
                      ) -> list[dict[str, float]]:
    """A deterministic spread of the eligible space, for scale.

    Seeded rather than random: a score that moves between runs because the
    yardstick moved is worse than no score.
    """
    import random

    rng = random.Random(20260810)
    out = []
    for _ in range(REFERENCE - 1):
        route = {stage.id: rng.choice(eligible[stage.id]) for stage in stages}
        out.append(aggregate(workbench, route))
    return out


def _eligible_by_stage(workbench: WorkbenchDefinition, policy: Policy
                       ) -> tuple[dict[str, list[str]], dict[str, int]]:
    gates = gate_all(workbench, policy)
    eligible = {sid: list(gate.eligible) for sid, gate in gates.items()}
    blocked = {sid: len(gate.blocked) for sid, gate in gates.items()}
    return eligible, blocked


def _score_routes(workbench: WorkbenchDefinition, profile: OptimizationProfile,
                  routes: Sequence[Mapping[str, str]]) -> list[tuple[int, float]]:
    """Score whole routes against each other, never in isolation.

    The comparison set is the routes under consideration, which is what makes
    the weights mean anything — see OptimizationProfile.score.
    """
    metrics = [aggregate(workbench, route) for route in routes]
    spans = profile.ranges(metrics)          # once, not once per route
    return [(index, profile.score_within(m, spans))
            for index, m in enumerate(metrics)]


def propose(workbench: WorkbenchDefinition, profile: OptimizationProfile, *,
            policy: Policy | None = None, strategy: str = "auto",
            beam: int = 8, limit: int = EXHAUSTIVE_LIMIT) -> Proposal:
    """The best route this profile can find, under this policy.

    Policy first, always: candidates are gated before a single score is
    computed, so no weighting can promote something the task is not allowed to
    run.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; "
                         f"known: {', '.join(STRATEGIES)}")
    policy = policy or Policy.permissive()
    stages = list(workbench.leaf_stages)
    proposal = Proposal(strategy=strategy, profile_id=profile.id,
                        total=workbench.route_count())
    if not stages:
        proposal.problems = ("the workbench has no stages",)
        return proposal

    eligible, blocked = _eligible_by_stage(workbench, policy)
    proposal.blocked = blocked
    empty = [s.id for s in stages if not eligible[s.id]]
    if empty:
        proposal.problems = tuple(
            f"stage {sid!r} has no eligible candidate under policy "
            f"{policy.name!r} — {blocked[sid]} blocked" for sid in empty)
        return proposal

    space = 1
    for stage in stages:
        space *= len(eligible[stage.id])
    proposal.eligible_total = space

    chosen = strategy
    if strategy == "auto":
        chosen = "exhaustive" if space <= limit else "beam"
        proposal.notes += (f"auto chose {chosen}: {space:,} eligible routes "
                           f"{'fits' if chosen == 'exhaustive' else 'exceeds'} "
                           f"the {limit:,} enumeration limit",)
    proposal.strategy = chosen

    # Stable ranges for every strategy that scores partial routes, computed
    # once from the reference sample.
    spans = profile.ranges(_reference_sample(workbench, stages, eligible))

    if chosen == "greedy":
        route, examined = _greedy(workbench, profile, stages, eligible)
        proposal.notes += ("greedy scores each stage independently; route "
                           "quality compounds, so this is a baseline rather "
                           "than an optimum",)
    elif chosen == "exhaustive":
        route, examined = _exhaustive(workbench, profile, stages, eligible)
    else:
        route, examined = _beam(workbench, profile, stages, eligible, beam, spans)
        proposal.notes += (f"beam width {beam}",)

    proposal.route = route
    proposal.examined = examined
    proposal.metrics = aggregate(workbench, route)

    # Score the winner against a fixed reference sample rather than against
    # whatever each strategy happened to look at. Greedy examines 56 routes and
    # exhaustive 122,472; normalizing each within its own pool would produce two
    # numbers that cannot be compared, which defeats the point of reporting a
    # score at all. The sample is seeded, so the reference is the same every run.
    proposal.score = profile.score_within(proposal.metrics, spans)

    # A raw normalized score can exceed 1.0 — the reference sample sets the
    # scale, and a good search finds routes better than anything sampled. That
    # is informative but reads like a bug, so the headline number is a
    # percentile against the same sample, which cannot.
    reference = _reference_sample(workbench, stages, eligible)
    beaten = sum(1 for m in reference
                 if profile.score_within(m, spans) < proposal.score)
    proposal.percentile = beaten / len(reference) if reference else 0.0
    proposal.notes += (f"scored against a fixed {REFERENCE - 1}-route reference "
                       f"sample, so strategies stay comparable",)

    # Re-gate the proposal itself. A route assembled from individually eligible
    # candidates can still break a whole-route budget, and the specification is
    # explicit that a proposal is revalidated before it runs.
    proposal.problems += tuple(_route_problems(workbench, proposal, policy))
    permissions, effects = route_permissions(workbench, route)
    proposal.permissions = tuple(permissions)
    proposal.effects = tuple(effects)
    return proposal


def _route_problems(workbench: WorkbenchDefinition, proposal: Proposal,
                    policy: Policy) -> list[str]:
    problems = []
    metrics = proposal.metrics
    if policy.max_cost_usd is not None and metrics.get("cost_usd", 0) > policy.max_cost_usd:
        problems.append(f"the whole route costs ${metrics['cost_usd']:.4f}, over "
                        f"the ${policy.max_cost_usd} budget — every candidate was "
                        f"individually affordable")
    if policy.max_latency_ms is not None \
            and metrics.get("latency_ms", 0) > policy.max_latency_ms:
        problems.append(f"the whole route takes {metrics['latency_ms']:,.0f}ms, over "
                        f"the {policy.max_latency_ms:,.0f}ms budget")
    return problems


def _greedy(workbench, profile, stages, eligible) -> tuple[dict[str, str], int]:
    nodes = workbench.nodes_by_id
    candidates = workbench.candidates_by_id
    route, examined = {}, 0
    for stage in stages:
        pool = {}
        for cid in eligible[stage.id]:
            manifest = nodes.get(candidates[cid].node_id)
            pool[cid] = dict(manifest.metrics or {}) if manifest else {}
            examined += 1
        route[stage.id] = profile.rank(pool)[0][0]
    return route, examined


def _exhaustive(workbench, profile, stages, eligible) -> tuple[dict[str, str], int]:
    keys = [stage.id for stage in stages]
    best: dict[str, str] = {}
    best_score, examined = float("-inf"), 0
    combos = [dict(zip(keys, combo, strict=True))
              for combo in itertools.product(*[eligible[k] for k in keys])]
    for index, score in _score_routes(workbench, profile, combos):
        examined += 1
        if score > best_score or (score == best_score and not best):
            best, best_score = combos[index], score
    return best, examined


def _beam(workbench, profile, stages, eligible, width, spans
          ) -> tuple[dict[str, str], int]:
    """Keep the best `width` partial routes at each stage.

    Partial routes are scored against **fixed** ranges, and that detail is the
    difference between working and not. The first version renormalized within
    each step's own set of partials, so the yardstick changed at every stage:
    a prefix that was genuinely good got pruned because it looked ordinary
    against whatever else happened to survive alongside it. The symptom was
    unmistakable once measured — beam matched plain greedy at width 1, 8, 32,
    128 *and* 512, examining 8,878 routes to reach the answer greedy found in
    56. A search whose width buys nothing is not a search.

    With stable ranges, prefixes compare against the same scale at every depth,
    and widening the beam does what widening a beam is supposed to do.
    """
    partials: list[dict[str, str]] = [{}]
    examined = 0
    for stage in stages:
        grown = [dict(partial, **{stage.id: cid})
                 for partial in partials for cid in eligible[stage.id]]
        examined += len(grown)
        metrics = [aggregate(workbench, route) for route in grown]
        scored = [(index, profile.score_within(m, spans))
                  for index, m in enumerate(metrics)]
        scored.sort(key=lambda pair: (-pair[1], grown[pair[0]].get(stage.id, "")))
        partials = [grown[index] for index, _ in scored[:width]]
    return partials[0] if partials else {}, examined


def compare_strategies(workbench: WorkbenchDefinition,
                       profile: OptimizationProfile, *,
                       policy: Policy | None = None) -> dict[str, Proposal]:
    """All three, so the cost of the cheap one is visible rather than assumed."""
    return {name: propose(workbench, profile, policy=policy, strategy=name)
            for name in ("greedy", "beam", "exhaustive")}
