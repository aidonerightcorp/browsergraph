"""Learning which routes are worth trying, and knowing how much you have learned.

The demonstration has 3,802,314,700,800 routes. Enumerating them is not a plan,
and "use a heuristic" is not one either. The useful question is precise, and it
is an information-theory question:

    A route is a choice of one candidate per sub-step. With no knowledge, the
    number of *bits* you must supply to name a good one is the sum of log2 of
    each sub-step's candidate count — 41.8 bits for the demonstration.
    Every measured run supplies some of those bits. **The number of experiments
    you need scales with the entropy of your posterior, not with the size of the
    space.**

That reframing is the whole module. 41.8 bits sounds hopeless until you notice
it decomposes: 14 independent choices of 2 to 6 bits each. Fourteen dozens of
runs — not trillions — resolve most of it, *if* the choices are independent.

They are not entirely, and that is the interesting part. Independence is the
assumption that makes cheap search work, so this module measures where it
breaks rather than assuming it holds: when an observed route does much worse
than the product of its parts predicted, that pair of choices interacts, and
that specific pair is worth searching jointly. Everything else can stay greedy.

Three properties are non-negotiable here, because getting any of them wrong
makes the learning worse than none at all:

**Evidence is keyed to a context.** What works on one site, document class or
data distribution says little about another. Pooling them produces an average
of incompatible regimes that is correct nowhere. Contexts back off from
specific to general, so a new context starts from what its neighbours know
instead of from nothing.

**Absence is not failure.** An unmeasured candidate gets the prior, not zero.
Scoring it zero punishes anything new for being new, and a system that stops
exploring has stopped learning without anyone deciding that it should.

**Failures are recorded.** The reflex is to keep the wins. The failures are
what stop you repeating them.
"""
from __future__ import annotations

import json
import math
import pathlib
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Weight given to a parent context's evidence relative to the child's. A
#: neighbouring context is informative, not authoritative.
BACKOFF = 0.35

#: Beta(1,1) — uniform. An unmeasured candidate is neither good nor bad, and
#: the width of the posterior is what makes exploration happen on its own.
PRIOR_ALPHA = 1.0
PRIOR_BETA = 1.0


def context_chain(*keys: str) -> tuple[str, ...]:
    """A context and its generalisations, most specific first.

    `context_chain("site:acme.com", "sector:retail")` means: prefer what we know
    about this site, fall back to retail sites, fall back to everything. The
    global bucket is always last so a first run is never a cold start.
    """
    return (*[k for k in keys if k], "global")


@dataclass
class Observation:
    """One candidate's turn in one run, in one context."""
    candidate: str
    context: str = "global"
    ok: bool = True
    quality: float | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None
    route: tuple[str, ...] = ()
    run: str = ""

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"candidate": self.candidate, "context": self.context,
                               "ok": self.ok}
        for key in ("quality", "latency_ms", "cost_usd", "run"):
            if getattr(self, key) is not None and getattr(self, key) != "":
                out[key] = getattr(self, key)
        if self.route:
            out["route"] = list(self.route)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Observation:
        return cls(candidate=data.get("candidate", ""),
                   context=data.get("context", "global"),
                   ok=bool(data.get("ok", True)),
                   quality=data.get("quality"), latency_ms=data.get("latency_ms"),
                   cost_usd=data.get("cost_usd"),
                   route=tuple(data.get("route") or ()), run=data.get("run", ""))


@dataclass
class Posterior:
    """What is believed about one candidate, and how strongly.

    Beta for "does it work", running moments for the continuous metrics. The
    *width* matters as much as the mean: it is what tells a sampler to try
    something again, and what tells a human that a number is not yet a fact.
    """
    alpha: float = PRIOR_ALPHA
    beta: float = PRIOR_BETA
    runs: int = 0
    quality: float = 0.0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    measured: int = 0

    @property
    def rate(self) -> float:
        """Expected success rate."""
        return self.alpha / (self.alpha + self.beta)

    @property
    def confidence(self) -> float:
        """0 with no evidence, approaching 1 with a lot. Deliberately not a
        p-value: it is a weight for shrinkage, and calling it anything more
        precise would be a claim this data cannot support."""
        n = self.alpha + self.beta - PRIOR_ALPHA - PRIOR_BETA
        return n / (n + 8.0)

    @property
    def spread(self) -> float:
        """Standard deviation of the Beta. Wide means "try it again"."""
        a, b = self.alpha, self.beta
        return math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))

    def sample(self, rng: random.Random) -> float:
        """One draw. Thompson sampling: pick by sampling beliefs, so a
        candidate is explored in proportion to the chance it is best — no
        epsilon to tune, and exploration stops on its own as evidence
        accumulates."""
        return rng.betavariate(max(self.alpha, 1e-6), max(self.beta, 1e-6))

    def metrics(self) -> dict[str, float]:
        out = {}
        if self.measured:
            out = {"quality": self.quality, "latency_ms": self.latency_ms,
                   "cost_usd": self.cost_usd}
        return {k: v for k, v in out.items() if v}

    def to_dict(self) -> dict:
        return {"alpha": round(self.alpha, 4), "beta": round(self.beta, 4),
                "runs": self.runs, "measured": self.measured,
                "rate": round(self.rate, 4),
                "confidence": round(self.confidence, 4),
                "quality": round(self.quality, 4),
                "latency_ms": round(self.latency_ms, 2),
                "cost_usd": round(self.cost_usd, 6)}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Posterior:
        return cls(alpha=float(d.get("alpha", PRIOR_ALPHA)),
                   beta=float(d.get("beta", PRIOR_BETA)),
                   runs=int(d.get("runs", 0)), measured=int(d.get("measured", 0)),
                   quality=float(d.get("quality", 0.0)),
                   latency_ms=float(d.get("latency_ms", 0.0)),
                   cost_usd=float(d.get("cost_usd", 0.0)))

    def observe(self, obs: Observation) -> None:
        self.runs += 1
        if obs.ok:
            self.alpha += 1.0
        else:
            self.beta += 1.0
        if obs.quality is None and obs.latency_ms is None and obs.cost_usd is None:
            return
        # Running means, so a store stays O(candidates) rather than O(runs).
        self.measured += 1
        n = self.measured
        for name, value in (("quality", obs.quality), ("latency_ms", obs.latency_ms),
                            ("cost_usd", obs.cost_usd)):
            if value is None:
                continue
            current = getattr(self, name)
            setattr(self, name, current + (float(value) - current) / n)


@dataclass
class Evidence:
    """Everything measured, keyed by candidate and context.

    Deliberately a plain dict of dicts written as JSON: evidence that needs a
    database to read is evidence nobody inspects, and being able to open the
    file and argue with it is most of its value.
    """
    posteriors: dict[str, dict[str, Posterior]] = field(default_factory=dict)
    #: Route-level outcomes, for detecting interaction between choices.
    routes: list[tuple[tuple[str, ...], str, float]] = field(default_factory=list)

    # --- recording ----------------------------------------------------------

    def observe(self, obs: Observation) -> None:
        bucket = self.posteriors.setdefault(obs.candidate, {})
        bucket.setdefault(obs.context, Posterior()).observe(obs)

    def observe_route(self, route: Sequence[str], context: str = "global",
                      ok: bool = True, quality: float | None = None,
                      **metrics) -> None:
        """One complete run: every candidate in it, plus the joint outcome.

        The joint outcome is kept separately because it is the only thing that
        can reveal interaction. Per-candidate records alone cannot distinguish
        "both are fine" from "both are fine except together".
        """
        for candidate in route:
            self.observe(Observation(candidate=candidate, context=context, ok=ok,
                                     quality=quality, route=tuple(route),
                                     **metrics))
        if quality is not None:
            self.routes.append((tuple(route), context, float(quality)))

    def from_receipt(self, receipt: Any, context: str = "global") -> None:
        """Fold a `TaskReceipt` in, so evidence comes from real runs.

        Steps that verified are the ones whose outcome is worth believing: a
        node reporting its own success is not evidence, which is the whole
        reason verification is a separate stage.
        """
        route = tuple(step.key or step.kind for step in getattr(receipt, "steps", ()))
        ok = bool(getattr(receipt, "ok", False))
        for step in getattr(receipt, "steps", ()):
            self.observe(Observation(
                candidate=step.key or step.kind, context=context,
                ok=step.ok, latency_ms=step.seconds * 1000.0, route=route,
                run=getattr(receipt, "task", "")))
        if route:
            self.routes.append((route, context, 1.0 if ok else 0.0))

    # --- reading ------------------------------------------------------------

    def posterior(self, candidate: str, context: Sequence[str] = ("global",)
                  ) -> Posterior:
        """Belief about a candidate, backing off through the context chain.

        A specific context's evidence dominates; its generalisations inform.
        Without the backoff every new context is a cold start, and with equal
        weighting a busy neighbouring context drowns out the one you are in.
        """
        merged = Posterior()
        for depth, key in enumerate(context):
            found = self.posteriors.get(candidate, {}).get(key)
            if not found:
                continue
            weight = BACKOFF ** depth
            merged.alpha += (found.alpha - PRIOR_ALPHA) * weight
            merged.beta += (found.beta - PRIOR_BETA) * weight
            merged.runs += found.runs
            if found.measured:
                total = merged.measured + found.measured
                for name in ("quality", "latency_ms", "cost_usd"):
                    blended = (getattr(merged, name) * merged.measured
                               + getattr(found, name) * found.measured) / total
                    setattr(merged, name, blended)
                merged.measured = total
        return merged

    def known(self, candidate: str) -> bool:
        return bool(self.posteriors.get(candidate))

    # --- the information-theory accounting ----------------------------------

    def bits_of_choice(self, stages: Mapping[str, Sequence[str]]) -> float:
        """Bits needed to name a route with no knowledge at all.

        log2 of the route count — the honest measure of how big the problem is,
        and the baseline everything else is measured against.
        """
        return sum(math.log2(len(c)) for c in stages.values() if c)

    def bits_remaining(self, stages: Mapping[str, Sequence[str]],
                       context: Sequence[str] = ("global",),
                       draws: int = 96, seed: int = 20260810) -> float:
        """Bits still unresolved: uncertainty about *which candidate is best*.

        The first version normalized the success rates into a distribution and
        took its entropy. That measured the wrong thing and it showed — after a
        thousand runs it reported 1% resolved while the picks were visibly
        improving. Rates live in a narrow band around one half, so normalizing
        them produces a near-uniform distribution whose entropy barely moves no
        matter how confident the beliefs underneath it become.

        What matters is not how the rates are spread, it is **how sure you are
        which one wins**. So: sample each candidate's posterior repeatedly, count
        how often each is the argmax, and take the entropy of that. Zero bits
        means one candidate wins every draw; full bits means a coin toss between
        all of them. It falls as evidence accumulates, which is the only
        behaviour that makes it useful for deciding whether to run more.
        """
        rng = random.Random(seed)
        total = 0.0
        for candidates in stages.values():
            if len(candidates) < 2:
                continue
            posteriors = [self.posterior(c, context) for c in candidates]
            wins = [0] * len(candidates)
            for _ in range(draws):
                samples = [p.sample(rng) for p in posteriors]
                wins[samples.index(max(samples))] += 1
            for count in wins:
                if count:
                    share = count / draws
                    total += -share * math.log2(share)
        return total

    def resolved(self, stages: Mapping[str, Sequence[str]],
                 context: Sequence[str] = ("global",)) -> float:
        """The fraction of the choice that evidence has actually settled."""
        start = self.bits_of_choice(stages)
        if start <= 0:
            return 1.0
        return max(0.0, 1.0 - self.bits_remaining(stages, context) / start)

    # --- proposing ----------------------------------------------------------

    def suggest(self, stages: Mapping[str, Sequence[str]],
                context: Sequence[str] = ("global",), seed: int | None = None
                ) -> dict[str, str]:
        """A route worth trying, by Thompson sampling each sub-step.

        Cost is the **sum** of the candidate counts, not their product: 166
        draws instead of 3.8 trillion evaluations. That is the entire practical
        argument for keeping evidence — and it is exactly right only while the
        sub-steps are independent, which `interactions()` is there to check.
        """
        rng = random.Random(seed)
        return {stage: max(candidates,
                           key=lambda c: self.posterior(c, context).sample(rng))
                for stage, candidates in stages.items() if candidates}

    def ranked(self, candidates: Sequence[str],
               context: Sequence[str] = ("global",)) -> list[tuple[str, float]]:
        """Candidates by expected success, best first — the fallback order.

        This is where a route's fallbacks should come from: the second-best
        candidate for *this* sub-step in *this* context, rather than whatever
        somebody wrote down when the graph was first drawn.
        """
        scored = [(c, self.posterior(c, context).rate) for c in candidates]
        return sorted(scored, key=lambda pair: (-pair[1], pair[0]))

    # --- where independence breaks ------------------------------------------

    def interactions(self, minimum: int = 3, threshold: float = 0.15
                     ) -> list[tuple[str, str, float, int]]:
        """Pairs of choices that do worse together than one of them alone.

        Independence is the assumption that makes cheap search work, so it
        should be measured rather than believed. For each pair seen together,
        compare how routes containing **both** did against routes containing
        **exactly one** of them. A large negative gap means those two
        specifically need joint search; every other sub-step can stay greedy.

        The comparison used to be against `rate(a) * rate(b)`, and that was a
        category error rather than a tuning problem. A route outcome is the
        product over *every* step in it, so on a three-step route the observed
        quality sits near 0.8³ = 0.51 while the expectation was 0.8² = 0.64 —
        and every pair looked like it clashed. Measured on data built with no
        interaction whatsoever, it reported eleven. The longer the route the
        worse it got.

        Both sides of the comparison are now whole routes of the same shape,
        differing only in whether the pair co-occurs. That is the only version
        of this that can be read as evidence about the *pair*.

        Returns `(a, b, gap, runs)`, worst first. Requires `minimum` runs of the
        pair together, because two observations of anything prove nothing.
        """
        together: dict[tuple[str, str], list[float]] = {}
        apart: dict[tuple[str, str], list[float]] = {}

        seen_pairs = set()
        for route, _context, _quality in self.routes:
            for index, a in enumerate(route):
                for b in route[index + 1:]:
                    seen_pairs.add((a, b) if a < b else (b, a))

        for route, _context, quality in self.routes:
            present = set(route)
            for pair in seen_pairs:
                a, b = pair
                if a in present and b in present:
                    together.setdefault(pair, []).append(quality)
                elif a in present or b in present:
                    apart.setdefault(pair, []).append(quality)

        out = []
        for pair, both in together.items():
            one = apart.get(pair, [])
            if len(both) < minimum or len(one) < minimum:
                continue
            gap = sum(both) / len(both) - sum(one) / len(one)
            if gap < -threshold:
                out.append((*pair, gap, len(both)))
        return sorted(out, key=lambda row: row[2])

    # --- persistence --------------------------------------------------------

    def to_dict(self) -> dict:
        return {"posteriors": {c: {k: p.to_dict() for k, p in ctx.items()}
                               for c, ctx in self.posteriors.items()},
                "routes": [[list(r), c, q] for r, c, q in self.routes]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Evidence:
        store = cls()
        for candidate, contexts in (data.get("posteriors") or {}).items():
            store.posteriors[candidate] = {
                key: Posterior.from_dict(p) for key, p in contexts.items()}
        store.routes = [(tuple(r), c, float(q))
                        for r, c, q in (data.get("routes") or [])]
        return store

    def save(self, path: str) -> str:
        pathlib.Path(path).write_text(json.dumps(self.to_dict(), indent=2),
                                      encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str) -> Evidence:
        target = pathlib.Path(path)
        if not target.exists():
            return cls()
        return cls.from_dict(json.loads(target.read_text(encoding="utf-8")))

    # --- reporting ----------------------------------------------------------

    def report(self, stages: Mapping[str, Sequence[str]],
               context: Sequence[str] = ("global",)) -> str:
        start = self.bits_of_choice(stages)
        left = self.bits_remaining(stages, context)
        lines = [f"{start:.1f} bits of choice · {left:.1f} still open · "
                 f"{self.resolved(stages, context):.0%} resolved by evidence",
                 f"(the space is 2^{start:.1f} routes; the question is how many "
                 f"bits you have, not how many routes there are)"]
        for stage, candidates in stages.items():
            ranked = self.ranked(candidates, context)
            best, rate = ranked[0]
            posterior = self.posterior(best, context)
            lines.append(f"  {stage:<14} {best:<44} "
                         f"p={rate:.2f} ±{posterior.spread:.2f} "
                         f"n={posterior.runs}")
        clashes = self.interactions()
        if clashes:
            lines.append("  interactions worth searching jointly:")
            for a, b, gap, runs in clashes[:5]:
                lines.append(f"      {a} + {b}: {gap:+.2f} over {runs} runs")
        return "\n".join(lines)


def measured_metrics(evidence: Evidence, candidates: Sequence[str],
                     context: Sequence[str] = ("global",),
                     prior_quality: Mapping[str, float] | None = None,
                     explore: float = 1.0
                     ) -> dict[str, dict[str, float]]:
    """Per-candidate metrics: the prior pulled toward measurement, plus optimism.

    Two things happen here and both are load-bearing.

    **Shrinkage.** One observation should nudge a prior; fifty should overrule
    it. `Posterior.confidence` is exactly that weight and says so in its own
    docstring — deliberately not a p-value, just how much to trust the
    measurement against the guess.

        shrunk = (1 - confidence) * prior + confidence * measured

    **Optimism.** The shrunk mean alone is not enough, and the failure is not
    subtle. Given a workbench whose declared prior said the worst candidate was
    the best one, a search on means picked it sixty times out of sixty and never
    tried either alternative — each failure lowered its score a little, and the
    untried candidates sat at their own priors with nothing to raise them. The
    loop cannot learn about a thing it never runs.

    So the score is the shrunk mean plus a bonus for not knowing:

        effective = shrunk + explore * spread

    `spread` is the Beta standard deviation: wide when little has been seen,
    narrow once a lot has. That makes an untried candidate attractive *because*
    it is untried, and stops being attractive once it has been tried enough —
    the whole of optimism under uncertainty, with no schedule to tune.

    Every candidate gets an entry now, measured or not. Returning only the
    measured ones was the other half of the same bug: an unmeasured candidate
    fell back to its declared prior with no credit for being unknown.

    `explore=0` gives the plain shrunk mean, for when you want the current best
    guess rather than the next thing worth trying.
    """
    out: dict[str, dict[str, float]] = {}
    priors = dict(prior_quality or {})
    for candidate in candidates:
        posterior = evidence.posterior(candidate, context)
        weight = posterior.confidence
        prior = priors.get(candidate, posterior.rate)
        shrunk = (1.0 - weight) * prior + weight * posterior.rate
        # Not clamped to 1.0, and that is not an oversight. Clamping saturated
        # every candidate whose prior plus bonus reached 1.0 — which, since an
        # undeclared prior *is* 1.0, meant almost all of them. Tried and untried
        # came out identical, the search went blind, and `solve` could not
        # explore past its first route. Quality is normalised against a
        # reference sample before it is scored, so a value above 1 is
        # meaningful and a saturated one is not.
        metrics = {"quality": shrunk + explore * posterior.spread}
        if posterior.measured and posterior.latency_ms:
            metrics["latency_ms"] = posterior.latency_ms
        out[candidate] = metrics
    return out


def pair_effects(evidence: Evidence, minimum: int = 10,
                 threshold: float = 0.05) -> dict[frozenset, float]:
    """How much worse (or better) two candidates are together, as a multiplier.

    `interactions()` finds the pairs and reports a gap. Nothing consumed that
    number: the search nudged its *starting route* away from a known-bad pair
    and then scored every route as though the pair did not exist, so sampling
    could walk straight back into it.

    A gap of -0.4 becomes a multiplier of 0.6 on any route holding both. That
    is the same shape as the rest of route scoring, where quality compounds —
    a pair that halves the odds should halve the route's quality, not subtract
    a constant from it.

    Positive gaps are kept too. Two things that work *better* together is a real
    finding and there is no reason to report only the bad half.
    """
    out: dict[frozenset, float] = {}
    for a, b, gap, _runs in evidence.interactions(minimum=minimum,
                                                 threshold=threshold):
        out[frozenset((a, b))] = max(0.0, 1.0 + gap)
    # `interactions` only returns the negative side; ask again for the positive
    # one by inverting the threshold test on the same measured contrast.
    for a, b, gap, _runs in evidence.interactions(minimum=minimum,
                                                 threshold=-1e9):
        if gap > threshold:
            out[frozenset((a, b))] = 1.0 + gap
    return out


def stages_of(workbench: Any, policy: Any = None) -> dict[str, list[str]]:
    """The eligible candidates per sub-step, as `suggest` wants them."""
    if policy is None:
        return {s.id: list(s.candidates) for s in workbench.leaf_stages}
    from browsergraph.policy import gate_all
    return {sid: list(gate.eligible)
            for sid, gate in gate_all(workbench, policy).items()}
