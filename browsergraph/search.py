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
import random
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field

from browsergraph.policy import Policy, aggregate, gate_all, route_permissions
from browsergraph.workbench import OptimizationProfile, WorkbenchDefinition

#: Enumerate exhaustively below this many complete routes. Above it, beam.
EXHAUSTIVE_LIMIT = 200_000

STRATEGIES = ("auto", "exhaustive", "beam", "greedy", "sprouts", "halving")

#: How many routes a search will look at when nobody says. Small on purpose: a
#: default budget that quietly costs minutes is a default nobody can trust.
DEFAULT_BUDGET = 400


@dataclass
class Decision:
    """Why one sub-step chose what it chose.

    A route without this is an assertion. When a pipeline starts behaving
    differently, the question is never "what did it pick" — that is in the
    route — it is *why*, and specifically which objective moved the answer.
    Recording the per-objective contributions makes that a lookup instead of an
    argument, and it distinguishes "the optimizer changed its mind" from "the
    evidence changed underneath it".
    """
    stage: str = ""
    chosen: str = ""
    score: float = 0.0
    eligible: int = 0
    blocked: int = 0
    contributions: dict[str, float] = field(default_factory=dict)
    alternatives: tuple[tuple[str, float], ...] = ()
    reason: str = ""

    def to_dict(self) -> dict:
        return {"stage": self.stage, "chosen": self.chosen,
                "score": round(self.score, 6), "eligible": self.eligible,
                "blocked": self.blocked,
                "contributions": {k: round(v, 6)
                                  for k, v in self.contributions.items()},
                "alternatives": [[a, round(s, 6)] for a, s in self.alternatives],
                "reason": self.reason}


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
    decisions: tuple[Decision, ...] = ()
    #: What a model proposed, if one was asked. Kept so its contribution is
    #: auditable: a model quietly ignored looks identical to one quietly
    #: followed unless somebody writes down which happened.
    suggestions: tuple = ()

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
                "problems": list(self.problems), "notes": list(self.notes),
                "decisions": [d.to_dict() for d in self.decisions]}

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
        for decision in self.decisions:
            if decision.contributions:
                terms = "  ".join(f"{m}={v:+.3f}"
                                  for m, v in sorted(decision.contributions.items()))
                lines.append(f"    why {decision.stage}: {terms}"
                             f"   ({decision.eligible} eligible, "
                             f"{decision.blocked} blocked)")
        for note in self.notes:
            lines.append("  note: " + note)
        for problem in self.problems:
            lines.append("  PROBLEM: " + problem)
        return "\n".join(lines)


#: Routes in the fixed reference sample used to normalize reported scores.
REFERENCE = 513


def _reference_sample(workbench: WorkbenchDefinition, stages, eligible,
                      overrides=None, pairs=None) -> list[dict[str, float]]:
    """A deterministic spread of the eligible space, for scale.

    Seeded rather than random: a score that moves between runs because the
    yardstick moved is worse than no score.
    """
    rng = random.Random(20260810)
    out = []
    for _ in range(REFERENCE - 1):
        route = {stage.id: rng.choice(eligible[stage.id]) for stage in stages}
        out.append(aggregate(workbench, route, overrides, pairs))
    return out


def _eligible_by_stage(workbench: WorkbenchDefinition, policy: Policy
                       ) -> tuple[dict[str, list[str]], dict[str, int]]:
    gates = gate_all(workbench, policy)
    eligible = {sid: list(gate.eligible) for sid, gate in gates.items()}
    blocked = {sid: len(gate.blocked) for sid, gate in gates.items()}
    return eligible, blocked


def _score_routes(workbench: WorkbenchDefinition, profile: OptimizationProfile,
                  routes: Sequence[Mapping[str, str]], overrides=None,
                  pairs=None) -> list[tuple[int, float]]:
    """Score whole routes against each other, never in isolation.

    The comparison set is the routes under consideration, which is what makes
    the weights mean anything — see OptimizationProfile.score.
    """
    metrics = [aggregate(workbench, route, overrides, pairs) for route in routes]
    spans = profile.ranges(metrics)          # once, not once per route
    return [(index, profile.score_within(m, spans))
            for index, m in enumerate(metrics)]


def propose(workbench: WorkbenchDefinition, profile: OptimizationProfile, *,
            policy: Policy | None = None, strategy: str = "auto",
            beam: int = 8, limit: int = EXHAUSTIVE_LIMIT,
            budget: int = DEFAULT_BUDGET, seed: int = 0,
            around: Mapping[str, str] | None = None,
            overrides=None, pairs=None) -> Proposal:
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
    spans = profile.ranges(_reference_sample(workbench, stages, eligible,
                                             overrides, pairs))

    if chosen == "greedy":
        route, examined = _greedy(workbench, profile, stages, eligible,
                                  overrides, pairs)
        proposal.notes += ("greedy scores each stage independently; route "
                           "quality compounds, so this is a baseline rather "
                           "than an optimum",)
    elif chosen == "sprouts":
        route, examined = _sprouts(workbench, profile, stages, eligible, spans,
                                   budget, seed, around, overrides, pairs)
        proposal.notes += (f"sampled {examined:,} routes of {space:,} under a "
                           f"budget of {budget:,} — a good score here is the "
                           f"best seen, not a proven best",)
    elif chosen == "halving":
        route, examined = _halving(workbench, profile, stages, eligible, spans,
                                   budget, seed, overrides, pairs)
        proposal.notes += (f"successive halving inside a budget of {budget:,}; "
                           f"{examined:,} routes of {space:,} were scored",)
    elif chosen == "exhaustive":
        route, examined = _exhaustive(workbench, profile, stages, eligible,
                                      spans, limit, overrides, pairs)
    else:
        route, examined = _beam(workbench, profile, stages, eligible, beam,
                                spans, overrides, pairs)
        proposal.notes += (f"beam width {beam}",)

    proposal.route = route
    proposal.examined = examined
    proposal.metrics = aggregate(workbench, route, overrides, pairs)

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
    reference = _reference_sample(workbench, stages, eligible, overrides, pairs)
    beaten = sum(1 for m in reference
                 if profile.score_within(m, spans) < proposal.score)
    proposal.percentile = beaten / len(reference) if reference else 0.0
    proposal.notes += (f"scored against a fixed {REFERENCE - 1}-route reference "
                       f"sample, so strategies stay comparable",)

    # Re-gate the proposal itself. A route assembled from individually eligible
    # candidates can still break a whole-route budget, and the specification is
    # explicit that a proposal is revalidated before it runs.
    proposal.decisions = _explain(workbench, profile, proposal, eligible,
                                  blocked)
    proposal.problems += tuple(_route_problems(workbench, proposal, policy))
    permissions, effects = route_permissions(workbench, route)
    proposal.permissions = tuple(permissions)
    proposal.effects = tuple(effects)
    return proposal


def _explain(workbench: WorkbenchDefinition, profile: OptimizationProfile,
             proposal: Proposal, eligible, blocked
             ) -> tuple[Decision, ...]:
    """Per-sub-step: what was available, what won, and what each objective
    contributed to that.

    Contributions are the weighted, normalized terms that actually summed to
    the score — not the profile's declared weights. A weight of 0.7 on a metric
    every candidate shares contributes nothing to the decision, and only the
    realised term shows that.
    """
    nodes = workbench.nodes_by_id
    candidates = workbench.candidates_by_id
    out = []
    for stage in workbench.leaf_stages:
        chosen = proposal.route.get(stage.id, "")
        pool = {}
        for cid in eligible.get(stage.id, ()):
            manifest = nodes.get(candidates[cid].node_id)
            pool[cid] = dict(manifest.metrics or {}) if manifest else {}
        ranked = profile.rank(pool) if pool else []
        score = next((s for c, s in ranked if c == chosen), 0.0)

        # Normalized against **this sub-step's own candidates**, not against the
        # route-level reference spans. Route spans describe sums of latency and
        # products of quality across fourteen stages; measuring one candidate
        # against them produced contributions above 1.0 that summed past the
        # score they were supposed to explain.
        local = profile.ranges(list(pool.values())) if pool else {}
        metrics = pool.get(chosen, {})

        # Mirror `score_within` exactly, including which objectives it drops:
        # a metric every candidate shares carries no information about the
        # choice and is skipped by the scorer, so counting it here would make
        # the parts fail to sum to the whole they claim to explain.
        discriminating = [
            o for o in profile.objectives
            if o.metric in metrics and o.metric in local
            and local[o.metric][1] != local[o.metric][0]]
        total_weight = sum(o.weight for o in discriminating) or 1.0
        contributions = {}
        for objective in discriminating:
            low, high = local[objective.metric]
            unit = (float(metrics[objective.metric]) - low) / (high - low)
            if objective.direction == "minimize":
                unit = 1.0 - unit
            contributions[objective.metric] = objective.weight * unit / total_weight

        best = ranked[0][0] if ranked else ""
        reason = ("highest-scoring eligible candidate" if chosen == best
                  else "chosen by the route search over the whole chain, not by "
                       "this sub-step alone")
        out.append(Decision(
            stage=stage.id, chosen=chosen, score=score,
            eligible=len(pool), blocked=blocked.get(stage.id, 0),
            contributions=contributions,
            alternatives=tuple((c, s) for c, s in ranked[:3] if c != chosen),
            reason=reason))
    return tuple(out)


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


def _greedy(workbench, profile, stages, eligible, overrides=None, pairs=None
            ) -> tuple[dict[str, str], int]:
    nodes = workbench.nodes_by_id
    candidates = workbench.candidates_by_id
    route, examined = {}, 0
    for stage in stages:
        pool = {}
        for cid in eligible[stage.id]:
            manifest = nodes.get(candidates[cid].node_id)
            pool[cid] = dict(manifest.metrics or {}) if manifest else {}
            if overrides and cid in overrides:
                pool[cid].update(overrides[cid])
            examined += 1
        route[stage.id] = profile.rank(pool)[0][0]
    return route, examined


class SpaceTooLarge(ValueError):
    """Raised when enumeration was asked for and is not physically possible."""


def _exhaustive(workbench, profile, stages, eligible, spans,
                limit: int = EXHAUSTIVE_LIMIT, overrides=None, pairs=None
                ) -> tuple[dict[str, str], int]:
    """Every eligible route, scored, streamed.

    Two things here are load-bearing and were both wrong before.

    **It refuses rather than tries.** `strategy="auto"` already checks the space
    against `limit`; asking for `"exhaustive"` explicitly used to skip that check
    and go straight to enumeration. On the demonstration workbench that is 3.8
    trillion routes, and the process died taking 53GB of the machine with it.
    Refusing with the number in the message is the only honest answer: the
    caller asked for something that cannot be done, and quietly doing a
    different search instead would report coverage it did not have.

    **It streams.** Even at the limit, materialising the product as a list of
    dicts costs hundreds of megabytes to hold routes that are looked at once.
    `itertools.product` yields them; only the best is kept.

    Scoring uses the same fixed `spans` as every other strategy, so a score from
    here is comparable with one from beam or greedy. Scoring within the
    enumerated set instead would make the exhaustive number incomparable with
    the others, which defeats the point of running all three.
    """
    keys = [stage.id for stage in stages]
    space = 1
    for key in keys:
        space *= len(eligible[key])
    if space > limit:
        raise SpaceTooLarge(
            f"exhaustive search over {space:,} eligible routes exceeds the "
            f"{limit:,} enumeration limit. Use strategy='auto' (which picks "
            f"beam above the limit), strategy='beam', or raise `limit` "
            f"deliberately if you have the time and memory for it.")

    best: dict[str, str] = {}
    best_score, examined = float("-inf"), 0
    for combo in itertools.product(*[eligible[k] for k in keys]):
        route = dict(zip(keys, combo, strict=True))
        score = profile.score_within(aggregate(workbench, route, overrides, pairs), spans)
        examined += 1
        if score > best_score or (score == best_score and not best):
            best, best_score = route, score
    return best, examined


def _beam(workbench, profile, stages, eligible, width, spans,
          overrides=None, pairs=None) -> tuple[dict[str, str], int]:
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
        metrics = [aggregate(workbench, route, overrides, pairs) for route in grown]
        scored = [(index, profile.score_within(m, spans))
                  for index, m in enumerate(metrics)]
        scored.sort(key=lambda pair: (-pair[1], grown[pair[0]].get(stage.id, "")))
        partials = [grown[index] for index, _ in scored[:width]]
    return partials[0] if partials else {}, examined


def _sprouts(workbench, profile, stages, eligible, spans, budget: int,
             seed: int = 0, around: Mapping[str, str] | None = None,
             overrides=None, pairs=None) -> tuple[dict[str, str], int]:
    """Sample routes instead of enumerating them, and stay inside a budget.

    Beam is good but its shape is fixed: it commits stage by stage, so a route
    whose merit only appears once two later choices are made is one it can never
    reach. Sampling has the opposite bias — it sees anywhere, shallowly.

    Two sources, mixed deliberately:

    * uniform draws, so nothing is unreachable;
    * neighbours of the best route so far, differing in one or two choices,
      which is where the improvement usually is once you are close.

    Seeded, so the same workbench and budget give the same answer. A search that
    returns something different every run cannot be compared with itself, and
    "we changed the graph and the score moved" stops meaning anything.
    """
    rng = random.Random(seed)
    keys = [stage.id for stage in stages]
    pools = {key: list(eligible[key]) for key in keys}

    def score_of(route: Mapping[str, str]) -> float:
        return profile.score_within(aggregate(workbench, route, overrides, pairs), spans)

    best = dict(around) if around else {k: pools[k][0] for k in keys}
    best_score, examined = score_of(best), 1

    # A third of the budget wandering, the rest improving on the best found.
    explore = max(1, budget // 3)
    for step in range(budget - 1):
        if step < explore or not best:
            candidate = {k: rng.choice(pools[k]) for k in keys}
        else:
            candidate = dict(best)
            for key in rng.sample(keys, k=min(2, len(keys))):
                candidate[key] = rng.choice(pools[key])
        value = score_of(candidate)
        examined += 1
        if value > best_score:
            best, best_score = candidate, value
    return best, examined


def _halving(workbench, profile, stages, eligible, spans, budget: int,
             seed: int = 0, overrides=None, pairs=None
             ) -> tuple[dict[str, str], int]:
    """Look at many routes cheaply, then spend what is left on the survivors.

    Successive halving. Draw a wide field, score it, keep the better half, and
    re-spend the remaining budget refining those — each round mutating one
    choice at a time rather than starting over.

    The reason to prefer this over plain sampling is that most of a large space
    is obviously bad, and finding that out costs the same per route as finding
    out something is good. Halving stops paying full price for the obviously bad
    after the first look.
    """
    rng = random.Random(seed)
    keys = [stage.id for stage in stages]
    pools = {key: list(eligible[key]) for key in keys}

    def score_of(route):
        return profile.score_within(aggregate(workbench, route, overrides, pairs), spans)

    field = [{k: rng.choice(pools[k]) for k in keys}
             for _ in range(max(2, budget // 2))]
    scored = [(score_of(route), route) for route in field]
    examined = len(scored)

    while len(scored) > 1 and examined < budget:
        scored.sort(key=lambda pair: -pair[0])
        scored = scored[:max(1, len(scored) // 2)]
        refined = []
        for value, route in scored:
            if examined >= budget:
                refined.append((value, route))
                continue
            nudged = dict(route)
            key = rng.choice(keys)
            nudged[key] = rng.choice(pools[key])
            fresh = score_of(nudged)
            examined += 1
            refined.append(max((value, route), (fresh, nudged),
                               key=lambda pair: pair[0]))
        scored = refined

    scored.sort(key=lambda pair: -pair[0])
    return scored[0][1], examined


def within(workbench: WorkbenchDefinition, profile: OptimizationProfile, *,
           evaluations: int = DEFAULT_BUDGET, policy: Policy | None = None,
           seed: int = 0, evidence=None,
           context: Sequence[str] = ("global",),
           explore: float = 1.0) -> Proposal:
    """The best route findable in this many evaluations. Budget first.

    Every other entry point asks *how* to search. This one asks *how much*,
    which is the question a caller actually has.

    What it does, and why in this order:

    1. Enumerate, if the whole eligible space fits inside the budget. Then
       "best" means best.
    2. Otherwise pick a starting route. With `evidence` from real runs that is
       a Thompson draw over the posteriors — cost is the *sum* of the candidate
       counts, not their product. Without it, greedy, which costs about one
       look per candidate and exploits the per-stage structure.
    3. Spend everything left sampling around that route.

    Step 3 starting from step 2 is the whole point, and it was worth measuring
    rather than assuming. Sampling from scratch under the same budget scored
    0.911 on the demonstration workbench where greedy alone scored 1.0875 — the
    random search spent its whole allowance rediscovering what greedy knew for
    free. Anchored to a real starting point it can only improve on it, because
    that point is already in the running.

    `evidence` is what closes the loop this library exists to argue for: a run
    produces a receipt, `Evidence.from_receipt` folds it in, and the next search
    starts from what actually happened rather than from the metrics somebody
    guessed when the graph was drawn. Priors do not become measurements by
    being searched over.

    `explore` decides which question you are asking, and they are different
    questions:

    * `explore > 0` — *what should I run next?* Untried candidates get credit
      for being untried, so the loop keeps learning. This is the default,
      because a search fed evidence is usually about to produce more of it.
      Without it the loop locks in: given a prior that named the worst
      candidate best, a search on means picked it sixty times out of sixty.
    * `explore = 0` — *what is the best I currently know?* No optimism, just the
      prior pulled toward what was measured. Use this when the answer is going
      into production rather than into another experiment.
    """
    eligible, _blocked = _eligible_by_stage(workbench, policy or Policy.permissive())
    stages = list(workbench.leaf_stages)
    space = 1
    for stage in stages:
        space *= max(1, len(eligible[stage.id]))

    # Metrics first, and *before* the small-space shortcut below. Computing
    # them after it meant the enumerate path returned without them, so on any
    # space small enough to enumerate — which is most teaching examples and
    # plenty of real graphs — evidence was collected, stored, and then ignored
    # completely. Fifty runs of a nine-route job picked the same route every
    # time and never tried the other six candidates.
    overrides = None
    pairs = None
    if evidence is not None:
        from browsergraph.evidence import measured_metrics
        nodes = workbench.nodes_by_id
        by_id = workbench.candidates_by_id
        priors = {cid: float((nodes[by_id[cid].node_id].metrics or {}).get("quality", 1.0))
                  for pool in eligible.values() for cid in pool
                  if cid in by_id and by_id[cid].node_id in nodes}
        overrides = measured_metrics(
            evidence, [c for pool in eligible.values() for c in pool],
            context, priors, explore=explore) or None
        # Pairwise effects reach the score too, not just the starting route.
        from browsergraph.evidence import pair_effects
        pairs = pair_effects(evidence) or None

    if space <= evaluations:
        # Enumerating scores every route on measured metrics where they exist,
        # so this is the *best* case for evidence, not an excuse to skip it.
        return propose(workbench, profile, policy=policy, strategy="exhaustive",
                       limit=max(evaluations, space), overrides=overrides,
                       pairs=pairs)

    learned = None
    if evidence is not None:
        pools = {stage.id: eligible[stage.id] for stage in stages
                 if eligible[stage.id]}
        drawn = evidence.suggest(pools, context, seed=seed)
        if drawn and len(drawn) == len(pools):
            learned = drawn

    if learned is not None:
        start_route, start_examined = dict(learned), len(
            [c for pool in eligible.values() for c in pool])
        start_score = profile.score_within(
            aggregate(workbench, learned, overrides),
            profile.ranges(_reference_sample(workbench, stages, eligible,
                                             overrides)))
        origin = "evidence"
    else:
        cheap = propose(workbench, profile, policy=policy, strategy="greedy")
        start_route, start_examined = dict(cheap.route), cheap.examined
        start_score, origin = cheap.score, "greedy"

    remaining = max(0, evaluations - start_examined)
    if remaining < 2 or not start_route:
        fallback = propose(workbench, profile, policy=policy, strategy="greedy")
        fallback.notes += (f"budget of {evaluations:,} covered the {origin} pass "
                           f"only; nothing left to refine with",)
        return fallback

    # Pairs that measurably do worse together than apart. `suggest()` samples
    # each sub-step on its own, which is only right while the choices are
    # independent — its own docstring says so and points at this check. Running
    # the check and ignoring it was the gap: a known-bad pair could be proposed
    # every time and nothing would stop it.
    clashing: set[tuple[str, str]] = set()
    if evidence is not None:
        clashing = {(a, b) for a, b, _gap, _runs in evidence.interactions()}
        if clashing and start_route:
            chosen = set(start_route.values())
            hit = [(a, b) for a, b in clashing
                   if a in chosen and b in chosen]
            if hit:
                # Break the pair by re-drawing one side, rather than abandoning
                # a start that is otherwise good.
                import random as _random

                breaker = _random.Random(seed)
                for a, _b in hit:
                    for sid, cid in list(start_route.items()):
                        if cid == a and len(eligible.get(sid, ())) > 1:
                            options = [c for c in eligible[sid] if c != a]
                            start_route[sid] = breaker.choice(options)
                            break
                start_score = profile.score_within(
                    aggregate(workbench, start_route, overrides),
                    profile.ranges(_reference_sample(workbench, stages,
                                                     eligible, overrides)))

    refined = propose(workbench, profile, policy=policy, strategy="sprouts",
                      budget=remaining, seed=seed, around=start_route,
                      overrides=overrides, pairs=pairs)

    if refined.score >= start_score:
        best = refined
    else:
        best = propose(workbench, profile, policy=policy, strategy="greedy")
        best.route, best.score = dict(start_route), start_score

    best.examined = start_examined + refined.examined
    best.strategy = f"{origin}+sprouts"
    if clashing:
        best.notes += (f"{len(clashing)} candidate pair(s) measurably do worse "
                       f"together than apart; the starting route was adjusted "
                       f"to avoid them",)
    best.notes += (
        f"started from the {origin} route ({start_examined:,} looks, scoring "
        f"{start_score:.4f}), then {refined.examined:,} samples around it — "
        + (f"improved to {refined.score:.4f}" if refined.score > start_score
           else "no improvement found")
        + f". {best.examined:,} of {space:,} routes seen, so a good score here "
          f"is the best found and never a proven best.",)
    return best


def compare_strategies(workbench: WorkbenchDefinition,
                       profile: OptimizationProfile, *,
                       policy: Policy | None = None,
                       limit: int = EXHAUSTIVE_LIMIT) -> dict[str, Proposal]:
    """All three, so the cost of the cheap one is visible rather than assumed.

    Exhaustive is skipped — not attempted — when the space is too large to
    enumerate, and its absence is reported in the surviving proposals' notes
    rather than left for the caller to notice. Comparing greedy against beam is
    still worth doing; comparing them against a search that could not run is
    not, and silently returning two keys where three were expected is how a
    caller ends up reporting a best-of-three that was a best-of-two.
    """
    out: dict[str, Proposal] = {}
    for name in ("greedy", "beam", "exhaustive"):
        try:
            out[name] = propose(workbench, profile, policy=policy,
                                strategy=name, limit=limit)
        except SpaceTooLarge as exc:
            for proposal in out.values():
                proposal.notes += (f"exhaustive not run: {exc}",)
    return out


def eligible_routes(workbench: WorkbenchDefinition, *,
                    policy: Policy | None = None,
                    limit: int = EXHAUSTIVE_LIMIT) -> Iterator[dict[str, str]]:
    """Every route policy allows, streamed in a stable order.

    Searching is for spaces too big to look at. Some spaces are not: a graph
    with four routes does not need Thompson sampling, it needs somebody to try
    all four. Without a way to ask for them, a caller wanting complete coverage
    of a small space has to re-derive per-stage eligibility, which means
    re-deriving the policy — and a second implementation of "what is allowed"
    is a second thing to get wrong.

    Streamed rather than returned as a list, because the same call on a large
    graph would otherwise be an out-of-memory bug waiting for someone to make
    it. It refuses past `limit` for the same reason `_exhaustive` does: quietly
    returning a prefix would report coverage that was never achieved.
    """
    stages = [s for s in workbench.leaf_stages if s.candidates]
    eligible, _blocked = _eligible_by_stage(workbench, policy or Policy())
    keys = [s.id for s in stages if eligible.get(s.id)]
    if not keys:
        return

    space = 1
    for key in keys:
        space *= len(eligible[key])
    if space > limit:
        raise SpaceTooLarge(
            f"{space:,} eligible routes exceeds the enumeration limit of "
            f"{limit:,}. This is the case search exists for — use `within` "
            f"with a budget instead of asking for all of them.")

    for combination in itertools.product(*(eligible[key] for key in keys)):
        yield dict(zip(keys, combination, strict=True))
