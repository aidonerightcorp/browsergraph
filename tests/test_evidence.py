"""Learning which routes are worth trying — and knowing how much you know.

The claim this module makes is quantitative, so the tests are too: evidence
should reduce the *bits* of uncertainty about which candidate wins, and the
sampler should cost the sum of the candidate counts rather than their product.
"""
from __future__ import annotations

import random

import pytest

from browsergraph.evidence import (
    Evidence,
    Observation,
    Posterior,
    context_chain,
    stages_of,
)

STAGES = {"a": ["a1", "a2", "a3"], "b": ["b1", "b2"]}


def test_an_unmeasured_candidate_gets_the_prior_not_zero():
    """Scoring it zero punishes anything new for being new, and the system
    stops exploring without anyone deciding that it should."""
    assert Evidence().posterior("never-seen").rate == pytest.approx(0.5)


def test_evidence_moves_the_belief():
    store = Evidence()
    for _ in range(10):
        store.observe(Observation(candidate="a1", ok=True))
    for _ in range(10):
        store.observe(Observation(candidate="a2", ok=False))
    assert store.posterior("a1").rate > 0.8
    assert store.posterior("a2").rate < 0.2


def test_a_wide_posterior_says_try_it_again():
    thin, thick = Posterior(), Posterior(alpha=50, beta=50)
    assert thin.spread > thick.spread
    assert thick.confidence > thin.confidence


def test_context_specific_evidence_outweighs_its_generalisations():
    """What works on one site says little about another; pooling them produces
    an average of incompatible regimes that is correct nowhere."""
    store = Evidence()
    for _ in range(6):
        store.observe(Observation(candidate="x", context="site:a", ok=True))
    for _ in range(6):
        store.observe(Observation(candidate="x", context="global", ok=False))
    here = store.posterior("x", context_chain("site:a"))
    elsewhere = store.posterior("x", context_chain("site:b"))
    # A relationship, not a magic threshold. Six local successes against six
    # global failures at 0.35 weight lands at 0.69, which is the correct
    # Bayesian answer — contrary general evidence should still count for
    # something when the local sample is this small.
    assert here.rate - elsewhere.rate > 0.3, "local evidence must dominate"
    assert here.rate > 0.6 > elsewhere.rate


def test_a_new_context_is_not_a_cold_start():
    store = Evidence()
    for _ in range(8):
        store.observe(Observation(candidate="x", context="global", ok=True))
    assert store.posterior("x", context_chain("site:new")).rate > 0.5


def test_bits_of_choice_is_the_size_of_the_problem():
    # 3 candidates x 2 candidates = 6 routes = log2(6) bits.
    assert Evidence().bits_of_choice(STAGES) == pytest.approx(2.585, abs=0.01)


def test_evidence_reduces_the_bits_that_remain():
    """The metric that answers "should I run more experiments".

    The first version normalized success rates and took their entropy; it
    reported 1% resolved after a thousand runs while the picks were visibly
    improving, because rates cluster near one half. This measures uncertainty
    about *which candidate wins*, which is the thing anyone cares about.
    """
    store = Evidence()
    before = store.bits_remaining(STAGES)
    for _ in range(30):
        store.observe(Observation(candidate="a1", ok=True))
        store.observe(Observation(candidate="a2", ok=False))
        store.observe(Observation(candidate="a3", ok=False))
        store.observe(Observation(candidate="b1", ok=True))
        store.observe(Observation(candidate="b2", ok=False))
    after = store.bits_remaining(STAGES)
    assert after < before / 2, f"{before:.2f} -> {after:.2f} is not learning"
    assert store.resolved(STAGES) > 0.5


def test_no_evidence_means_nothing_is_resolved():
    assert Evidence().resolved(STAGES) < 0.05


def test_suggest_costs_the_sum_not_the_product():
    """166 draws instead of 3.8 trillion evaluations is the entire practical
    argument for keeping evidence at all."""
    calls = {"n": 0}
    store = Evidence()
    real = Posterior.sample

    def counted(self, rng):
        calls["n"] += 1
        return real(self, rng)

    Posterior.sample = counted
    try:
        route = store.suggest(STAGES, seed=1)
    finally:
        Posterior.sample = real
    assert set(route) == set(STAGES)
    assert calls["n"] == sum(len(c) for c in STAGES.values())


def test_suggestions_are_reproducible_from_a_seed():
    store = Evidence()
    assert store.suggest(STAGES, seed=4) == store.suggest(STAGES, seed=4)


def test_the_sampler_converges_on_the_better_candidate():
    store = Evidence()
    for _ in range(40):
        store.observe(Observation(candidate="a1", ok=True))
        store.observe(Observation(candidate="a2", ok=False))
        store.observe(Observation(candidate="a3", ok=False))
    picks = [store.suggest(STAGES, seed=s)["a"] for s in range(40)]
    assert picks.count("a1") > 30, "Thompson sampling should exploit by now"


def test_ranked_gives_the_fallback_order():
    """A fallback should be the second-best candidate for *this* context, not
    whatever was written down when the graph was drawn."""
    store = Evidence()
    for _ in range(10):
        store.observe(Observation(candidate="a1", ok=True))
        store.observe(Observation(candidate="a2", ok=True))
        store.observe(Observation(candidate="a3", ok=False))
    order = [c for c, _ in store.ranked(STAGES["a"])]
    assert order[-1] == "a3"


def test_interaction_is_measured_not_assumed():
    """Independence is what makes cheap search work, so where it breaks should
    be detected rather than believed.

    The rig needs routes where the pair appears *apart*, and that is a real
    requirement rather than test scaffolding. If a1 and b1 only ever run
    together, their joint outcome cannot distinguish "the pair is bad" from
    "one of them is bad" — there is nothing to compare against. An earlier
    version appeared to manage it by comparing the route outcome to
    `rate(a) * rate(b)`, which is not a comparison of like with like and
    reported eleven clashes on data containing none.
    """
    store = Evidence()
    for _ in range(12):                      # each is fine on its own
        store.observe(Observation(candidate="a1", ok=True))
        store.observe(Observation(candidate="b1", ok=True))
    for _ in range(8):                       # apart, they do well
        store.routes.append((("a1", "c1"), "global", 0.9))
        store.routes.append((("b1", "c1"), "global", 0.9))
    for _ in range(8):                       # together they do not
        store.routes.append((("a1", "b1"), "global", 0.1))

    clash = store.interactions(minimum=3)
    assert clash and clash[0][:2] == ("a1", "b1")
    assert clash[0][2] < 0


def test_a_pair_that_never_appears_apart_cannot_be_blamed():
    """No contrast, no attribution. Saying otherwise would be inventing
    evidence, and the pair may be innocent while one member is not."""
    store = Evidence()
    for _ in range(20):
        store.routes.append((("a1", "b1"), "global", 0.1))
    assert store.interactions(minimum=3) == []


def test_two_observations_of_anything_prove_nothing():
    store = Evidence()
    store.routes.append((("a1", "b1"), "global", 0.0))
    assert store.interactions(minimum=3) == []


def test_a_receipt_becomes_evidence():
    from browsergraph import receipt as rc

    class Result:
        ok = True
        executed = ["one"]
        spec = None
        context = type("C", (), {"data": {}, "artifacts": [], "log": [],
                                 "error": ""})()

    steps = (rc.StepRecord(key="one", kind="click", ok=True, seconds=0.2),)
    store = Evidence()
    store.from_receipt(rc.of_run(Result(), steps=steps), context="site:a")
    assert store.posterior("one", context_chain("site:a")).rate > 0.5


def test_evidence_round_trips(tmp_path):
    store = Evidence()
    store.observe_route(["a1", "b1"], context="site:a", ok=True, quality=0.8)
    again = Evidence.load(store.save(str(tmp_path / "e.json")))
    assert again.posterior("a1", context_chain("site:a")).runs == 1
    assert again.routes == store.routes


def test_a_missing_store_is_empty_not_an_error(tmp_path):
    assert Evidence.load(str(tmp_path / "never-written.json")).posteriors == {}


def test_stages_of_respects_policy():
    from browsergraph.demo import workbench
    from browsergraph.policy import Policy

    bench = workbench()
    everything = stages_of(bench, Policy.permissive())
    locked = stages_of(bench, Policy(name="nothing"))
    assert sum(len(v) for v in everything.values()) > \
        sum(len(v) for v in locked.values())


def test_per_step_outcomes_teach_far_better_than_route_level_ones():
    """The measured argument for per-step receipts.

    Route-level pass/fail dilutes credit across every candidate equally, so each
    posterior converges to the average route quality rather than to its own
    truth. More runs do not help; the signal is not there.
    """
    stages = {f"s{i}": [f"s{i}c{j}" for j in range(4)] for i in range(5)}
    rng = random.Random(11)
    truth = {c: rng.betavariate(2, 3) for cs in stages.values() for c in cs}
    best = {s: max(cs, key=lambda c: truth[c]) for s, cs in stages.items()}

    def learn(per_step: bool) -> int:
        store = Evidence()
        local = random.Random(3)
        for _ in range(300):
            route = store.suggest(stages, seed=local.randrange(1 << 30))
            quality = 1.0
            for c in route.values():
                quality *= truth[c]
            if per_step:
                for c in route.values():
                    store.observe(Observation(candidate=c,
                                              ok=local.random() < truth[c]))
            else:
                store.observe_route(list(route.values()),
                                    ok=local.random() < quality ** (1 / len(route)),
                                    quality=quality)
        return sum(1 for s, cs in stages.items()
                   if store.ranked(cs)[0][0] == best[s])

    assert learn(per_step=True) > learn(per_step=False)


# --- what counts as an interaction -------------------------------------------

def _rig(interaction: bool, n: int = 500, seed: int = 2):
    """Three steps, three equal candidates each. Optionally one planted clash."""
    import random

    from browsergraph.evidence import Evidence, Observation

    rng = random.Random(seed)
    truth = {f"s{i}.{c}": 0.8 for i in range(3) for c in "abc"}
    store = Evidence()
    for _ in range(n):
        route = tuple(f"s{i}.{rng.choice('abc')}" for i in range(3))
        clash = interaction and "s0.a" in route and "s2.c" in route
        quality = 1.0
        for candidate in route:
            ok = rng.random() < (0.15 if clash else truth[candidate])
            store.observe(Observation(candidate=candidate, context="global", ok=ok))
            quality *= 1.0 if ok else 0.0
        store.routes.append((route, "global", quality))
    return store


def test_no_interaction_in_the_data_means_none_reported():
    """It used to report eleven.

    The comparison was `rate(a) * rate(b)`, and that is a category error rather
    than a tuning problem: a route outcome is the product over *every* step, so
    on a three-step route the observed quality sits near 0.8³ = 0.51 while the
    expectation was 0.8² = 0.64. Every pair looked like it clashed, and the
    longer the route the worse it got.
    """
    assert _rig(interaction=False).interactions(minimum=10) == []


def test_a_planted_interaction_is_still_found():
    """Fixing the false positives is worthless if it also stopped detecting."""
    found = _rig(interaction=True).interactions(minimum=10)
    assert found, "the planted clash was not detected"
    worst = found[0]
    assert {worst[0], worst[1]} == {"s0.a", "s2.c"}
    assert worst[2] < -0.3


def test_a_pair_seen_too_few_times_together_is_not_reported():
    """Two observations of anything prove nothing."""
    from browsergraph.evidence import Evidence

    store = Evidence()
    store.routes = [(("a", "b"), "global", 0.0), (("a", "c"), "global", 1.0)]
    assert store.interactions(minimum=10) == []


def test_both_sides_of_the_comparison_are_whole_routes():
    """Routes with the pair are compared against routes with exactly one of
    them — same shape, differing only in whether the pair co-occurs. Comparing
    a route outcome against a product of two candidate rates cannot be read as
    evidence about the pair."""
    from browsergraph.evidence import Evidence

    store = Evidence()
    # Long routes, so a two-candidate product would be badly wrong.
    for _ in range(40):
        store.routes.append((("a", "b", "x", "y", "z"), "global", 0.5))
        store.routes.append((("a", "c", "x", "y", "z"), "global", 0.5))
    assert store.interactions(minimum=10) == [], \
        "equal outcomes must not register as a clash however long the route"
