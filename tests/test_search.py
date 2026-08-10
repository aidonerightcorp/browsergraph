"""Gating and searching — and the three bugs that made the numbers meaningless.

Each of these was found by measuring rather than by reading, and each one
produced output that looked entirely plausible:

* combining raw metric values made every objective profile rank identically,
  because a latency in the thousands swamps a quality in [0,1];
* renormalizing inside the scoring call made ranking O(n²), so an exhaustive
  search was correct and unreachable;
* renormalizing at each beam step moved the yardstick under the search, so beam
  matched plain greedy at every width — a search whose width buys nothing.
"""
from __future__ import annotations

import pytest

from browsergraph import search
from browsergraph.demo import workbench
from browsergraph.policy import (
    Policy,
    aggregate,
    check_route,
    gate_stage,
    review,
    route_permissions,
)
from browsergraph.workbench import OptimizationObjective, OptimizationProfile


@pytest.fixture(scope="module")
def bench():
    return workbench()


@pytest.fixture(scope="module")
def small_bench():
    """Four sub-steps of five candidates: 625 routes, so exhaustive is instant."""
    from browsergraph.manifest import NodeManifest, ParameterSpec, PortSpec
    from browsergraph.workbench import (
        StageDefinition,
        WorkbenchDefinition,
        expand_node_candidates,
    )

    types = ["A", "B", "C", "D", "E"]
    nodes, stages = [], []
    for index, (takes, gives) in enumerate(zip(types, types[1:], strict=False)):
        nodes.append(NodeManifest(
            id=f"small.step{index}", kind=f"step{index}",
            description=f"Step {index}.", roles=("transform",),
            capabilities=(f"cap{index}",),
            inputs=(PortSpec("in", takes),), outputs=(PortSpec("out", gives),),
            parameters=(ParameterSpec("setting", "string", default="a",
                                      choices=("a", "b", "c", "d", "e")),),
            metrics={"quality": 0.9 - index * 0.05,
                     "latency_ms": 10 + index * 7, "cost_usd": index * 0.001},
        ).assert_valid())
    candidates = expand_node_candidates(nodes)
    # Vary the metrics per binding so the search has something to find.
    for index, (takes, gives) in enumerate(zip(types, types[1:], strict=False)):
        stages.append(StageDefinition(
            id=f"step{index}", input_type=takes, output_type=gives,
            required_capabilities=(f"cap{index}",),
        ).with_discovered_candidates(nodes, candidates))
    return WorkbenchDefinition(nodes=tuple(nodes), candidates=candidates,
                               stages=tuple(stages)).assert_valid()


@pytest.fixture(scope="module")
def locked():
    return Policy(permissions=frozenset({"filesystem", "filesystem:read",
                                         "filesystem:write", "database",
                                         "database:read"}),
                  allow_external_effects=False, deterministic_only=True,
                  name="locked-down")


BALANCED = OptimizationProfile(id="p.balanced", objectives=(
    OptimizationObjective("quality", "maximize", 0.5),
    OptimizationObjective("latency_ms", "minimize", 0.25),
    OptimizationObjective("cost_usd", "minimize", 0.25)))


# --- scoring ----------------------------------------------------------------

def test_objectives_are_normalized_before_they_are_weighted():
    """The bug that made every profile identical.

    Weighing 0.97 against 1420 directly means the latency term decides
    everything, whatever the weights say.
    """
    fast_poor = {"quality": 0.10, "latency_ms": 10, "cost_usd": 0.0}
    slow_great = {"quality": 0.99, "latency_ms": 1000, "cost_usd": 0.0}
    quality_first = OptimizationProfile(id="p.q", objectives=(
        OptimizationObjective("quality", "maximize", 0.9),
        OptimizationObjective("latency_ms", "minimize", 0.1)))
    ranked = quality_first.rank({"fast": fast_poor, "great": slow_great})
    assert ranked[0][0] == "great", "a quality-first profile must prefer quality"


def test_a_minimized_objective_prefers_less():
    speedy = OptimizationProfile(id="p.s", objectives=(
        OptimizationObjective("latency_ms", "minimize", 1.0),))
    ranked = speedy.rank({"slow": {"latency_ms": 900}, "quick": {"latency_ms": 9}})
    assert ranked[0][0] == "quick"


def test_ranking_is_stable_when_scores_tie():
    """An optimizer that reshuffles equal candidates makes its own evidence
    unattributable."""
    same = {"quality": 0.5}
    profile = OptimizationProfile(id="p.x", objectives=(
        OptimizationObjective("quality", "maximize", 1.0),))
    once = profile.rank({"b": same, "a": same, "c": same})
    twice = profile.rank({"c": same, "a": same, "b": same})
    assert [k for k, _ in once] == [k for k, _ in twice] == ["a", "b", "c"]


def test_an_unmeasured_objective_is_skipped_not_scored_zero():
    profile = OptimizationProfile(id="p.x", objectives=(
        OptimizationObjective("quality", "maximize", 1.0),
        OptimizationObjective("never_measured", "maximize", 1.0)))
    ranked = profile.rank({"a": {"quality": 0.9}, "b": {"quality": 0.1}})
    assert ranked[0][0] == "a"


def test_ranges_are_computed_once(bench):
    """The O(n²) bug: recomputing min/max per item made 122,472 routes
    unreachable, which for a search strategy is the same as being wrong."""
    pool = [{"quality": 0.1}, {"quality": 0.9}]
    spans = BALANCED.ranges(pool)
    assert spans["quality"] == (0.1, 0.9)
    assert BALANCED.score_within({"quality": 0.9}, spans) > \
        BALANCED.score_within({"quality": 0.1}, spans)


def test_the_demonstration_profiles_no_longer_rank_identically(bench):
    """They all did, before normalization. Four profiles, one opinion."""
    metrics = {s.id: s.metrics for s in bench.solutions}
    orders = {p.id: tuple(k for k, _ in p.rank(metrics))
              for p in bench.optimization_profiles}
    assert len(set(orders.values())) > 1, "every profile still ranks the same"
    quality = orders["profile.quality"]
    speed = orders["profile.speed"]
    assert quality.index("human_in_the_loop") < speed.index("human_in_the_loop"), \
        "the slowest, highest-quality route should rise under quality-first"


# --- policy -----------------------------------------------------------------

def test_a_permission_family_grants_its_members():
    policy = Policy(permissions=frozenset({"database"}))
    assert policy.grants("database:read") and policy.grants("database")


def test_silence_does_not_grant(bench):
    """An empty policy blocks with a reason rather than permitting by omission.

    Checked on `session`, where every candidate needs *some* authority. The
    first sub-step needs none at all, so an empty policy correctly permits it —
    which is the rule working, not failing.
    """
    session = next(s for s in bench.leaf_stages if s.id == "session")
    gate = gate_stage(bench, session, Policy(name="empty"))
    assert gate.eligible == ()
    assert all(v.reason for v in gate.blocked)


def test_a_candidate_that_needs_no_authority_needs_no_grant(bench):
    resolve = next(s for s in bench.leaf_stages if s.id == "resolve")
    gate = gate_stage(bench, resolve, Policy(name="empty"))
    assert gate.eligible, "a node asking for nothing should not need permission"


def test_blocked_candidates_stay_visible_with_reasons(bench, locked):
    session = next(s for s in bench.leaf_stages if s.id == "session")
    gate = gate_stage(bench, session, locked)
    assert len(gate.verdicts) == len(session.candidates)
    assert gate.eligible and gate.blocked
    assert any("needs browser" in v.reason for v in gate.blocked)


def test_policy_removes_most_of_the_space_and_says_so(bench, locked):
    report = review(bench, locked)
    assert 0 < report.reachable_routes < bench.route_count()
    assert report.reachable_routes == 1_959_552_000
    assert "1,959,552,000" in report.text()


def test_gating_walks_the_leaves_not_the_parent_stages(bench, locked):
    """Walking top-level stages reported "0 of 0 candidates" everywhere, which
    reads like a brutally strict policy rather than a traversal bug."""
    report = review(bench, locked)
    assert set(report.gates) == {s.id for s in bench.leaf_stages}
    assert all(gate.verdicts for gate in report.gates.values())


def test_a_policy_that_kills_a_stage_is_diagnosed_not_silently_empty(bench):
    impossible = Policy(permissions=frozenset(), name="nothing")
    report = review(bench, impossible)
    assert report.dead_stages()
    assert "nothing can run" in report.text()


def test_determinism_and_effects_are_separate_gates(bench):
    everything = Policy.permissive()
    no_effects = Policy.permissive(allow_external_effects=False)
    deterministic = Policy.permissive(deterministic_only=True)
    persist = next(s for s in bench.leaf_stages if s.id == "persist")
    assert len(gate_stage(bench, persist, everything).eligible) > \
        len(gate_stage(bench, persist, no_effects).eligible)
    act = next(s for s in bench.leaf_stages if s.id == "act")
    assert len(gate_stage(bench, act, deterministic).eligible) < \
        len(gate_stage(bench, act, everything).eligible)


def test_gating_a_composite_stage_is_refused_not_silently_empty(bench):
    """It returned an empty verdict list, which looked exactly like a policy
    that blocked everything."""
    with pytest.raises(ValueError, match="is a composite of"):
        gate_stage(bench, bench.stages[0], Policy.permissive())


# --- route arithmetic -------------------------------------------------------

def test_quality_compounds_and_the_rest_adds(bench):
    """Averaging quality would let one excellent stage hide a step that fails
    half the time."""
    route = {s.id: s.candidates[0] for s in bench.leaf_stages}
    got = aggregate(bench, route)
    nodes = bench.nodes_by_id
    qualities = [nodes[bench.candidates_by_id[c].node_id].metrics["quality"]
                 for c in route.values()]
    expected = 1.0
    for q in qualities:
        expected *= q
    assert got["quality"] == pytest.approx(expected)
    assert got["quality"] < min(qualities) or len(qualities) == 1


def test_a_route_reports_the_union_of_authority_it_needs(bench):
    learned = next(s for s in bench.solutions if s.id == "learned")
    permissions, _ = route_permissions(bench, learned.route)
    assert "browser" in permissions and "llm" in permissions


def test_a_route_is_re_checked_against_the_policy(bench, locked):
    learned = next(s for s in bench.solutions if s.id == "learned")
    problems = check_route(bench, learned.route, locked)
    assert problems and any("browser" in p for p in problems)


# --- search -----------------------------------------------------------------

def test_hard_gates_precede_soft_scoring(bench):
    """No weighting may promote something the task is not permitted to run."""
    no_llm = Policy.permissive(
        permissions=Policy.permissive().permissions - {"llm"}, name="no-llm")
    quality = next(p for p in bench.optimization_profiles
                   if p.id == "profile.quality")
    proposal = search.propose(bench, quality, policy=no_llm)
    _, _ = route_permissions(bench, proposal.route)
    assert "llm" not in route_permissions(bench, proposal.route)[0]


def test_a_search_reports_how_much_it_examined(bench, locked):
    proposal = search.propose(bench, BALANCED, policy=locked, strategy="greedy")
    assert proposal.examined and proposal.eligible_total
    assert 0 < proposal.coverage < 1
    assert "examined" in proposal.text(bench)


def test_exhaustive_examines_everything_eligible(small_bench):
    """On the demonstration the gated space is two billion routes, so exhaustive
    is measured on a small workbench instead of pretending otherwise."""
    proposal = search.propose(small_bench, BALANCED, strategy="exhaustive")
    assert proposal.examined == proposal.eligible_total == 5 ** 4
    assert proposal.coverage == 1.0


def test_beam_reaches_the_optimum_far_more_cheaply(small_bench):
    """The regression test for the beam that renormalized at every step.

    That version matched plain greedy at width 1, 8, 32, 128 and 512, spending
    8,878 evaluations to reach the answer greedy found in 56.
    """
    best = search.propose(small_bench, BALANCED, strategy="exhaustive")
    beam = search.propose(small_bench, BALANCED, strategy="beam")
    assert beam.score == pytest.approx(best.score), "beam should reach the optimum"
    assert beam.examined < best.examined


def test_beam_can_beat_greedy_on_the_demonstration(bench, locked):
    """Greedy scores each sub-step in isolation; route metrics compound."""
    speed = next(p for p in bench.optimization_profiles if p.id == "profile.speed")
    greedy = search.propose(bench, speed, policy=locked, strategy="greedy")
    beam = search.propose(bench, speed, policy=locked, strategy="beam")
    assert beam.score > greedy.score


def test_auto_picks_exhaustive_only_when_it_fits(bench, small_bench, locked):
    assert search.propose(small_bench, BALANCED, strategy="auto").strategy \
        == "exhaustive"
    big = search.propose(bench, BALANCED, policy=locked, strategy="auto")
    assert big.strategy == "beam"
    assert any("exceeds" in note for note in big.notes)


def test_a_dead_policy_produces_a_problem_not_a_crash(bench):
    proposal = search.propose(bench, BALANCED, policy=Policy(name="nothing"))
    assert not proposal.ok
    assert any("no eligible candidate" in p for p in proposal.problems)


def test_a_whole_route_budget_is_checked_after_the_route_exists(bench):
    """Every candidate individually affordable, the route as a whole not."""
    generous = Policy.permissive(max_cost_usd=0.02)
    quality = next(p for p in bench.optimization_profiles
                   if p.id == "profile.quality")
    proposal = search.propose(bench, quality, policy=generous)
    if proposal.metrics.get("cost_usd", 0) > 0.02:
        assert any("whole route costs" in p for p in proposal.problems)


def test_an_unknown_strategy_is_refused(bench):
    with pytest.raises(ValueError, match="unknown strategy"):
        search.propose(bench, BALANCED, strategy="telepathy")


def test_the_percentile_is_bounded_even_when_the_score_is_not(bench):
    """A normalized score can exceed 1 — the reference sample sets the scale and
    a good search beats everything sampled. The headline must not."""
    quality = next(p for p in bench.optimization_profiles
                   if p.id == "profile.quality")
    proposal = search.propose(bench, quality, policy=Policy.permissive())
    assert 0.0 <= proposal.percentile <= 1.0


def test_a_proposal_serialises(bench, locked):
    data = search.propose(bench, BALANCED, policy=locked).to_dict()
    assert set(data) >= {"route", "score", "examined", "eligible_total", "notes"}


# --- the static graphic -----------------------------------------------------

def test_the_static_svg_has_no_style_block_or_script(bench):
    """Kaggle strips `<style>`; GitHub strips scripts. Attributes survive both."""
    from browsergraph.routegraph import to_svg
    svg = to_svg(bench, routes=("cheapest",), samples=5)
    assert "<style" not in svg and "<script" not in svg
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")


def test_the_static_svg_is_byte_identical_between_runs(bench):
    """Seeded sampling, so the checked-in file does not churn in git."""
    from browsergraph.routegraph import to_svg
    assert to_svg(bench, samples=40) == to_svg(bench, samples=40)


def test_every_candidate_appears_in_the_static_svg(bench):
    from browsergraph.routegraph import to_svg
    svg = to_svg(bench)
    assert svg.count("<circle") == len(bench.candidates)


def test_the_static_svg_can_focus_one_stage_without_pooling_it(bench):
    """Fourteen sub-steps side by side is five thousand pixels wide — honest and
    unreadable. Narrowing is a crop, not a summary: nothing inside is pooled."""
    from browsergraph.routegraph import to_svg
    acquire = next(s for s in bench.stages if s.id == "acquire")
    svg = to_svg(bench, only=("acquire",), background=False)
    inside = sum(len(leaf.candidates) for leaf in acquire.leaves())
    assert svg.count("<circle") == inside < len(bench.candidates)
    assert "ACQUIRE INPUTS" in svg


def test_the_static_svg_labels_keep_the_binding_that_distinguishes_rows(bench):
    """Truncating shorter made headless and headed rows identical."""
    from browsergraph.routegraph import to_svg
    svg = to_svg(bench, background=False)
    assert "browser · Chrome · BrowserPort · headless" in svg
    assert "browser · Chrome · BrowserPort · headed<" in svg


# --- the CLI ----------------------------------------------------------------

def test_the_route_command_proposes(capsys):
    from browsergraph.cli import main
    assert main(["route", "--profile", "profile.speed", "--strategy", "greedy"]) == 0
    out = capsys.readouterr().out
    assert "examined" in out and "Resolve target" in out


def test_the_route_command_can_show_the_gates(capsys):
    from browsergraph.cli import main
    assert main(["route", "--gates", "--deterministic", "--no-effects",
                 "--allow", "filesystem:read"]) == 0
    assert "eligible" in capsys.readouterr().out


# --- decision traces --------------------------------------------------------

def test_a_decision_explains_every_sub_step(bench):
    """A route without this is an assertion. The question is never "what did it
    pick" — that is in the route — it is *why*."""
    proposal = search.propose(bench, BALANCED, policy=Policy.permissive(),
                              strategy="greedy")
    assert len(proposal.decisions) == len(bench.leaf_stages)
    for decision in proposal.decisions:
        assert decision.chosen == proposal.route[decision.stage]
        assert decision.eligible > 0


def test_contributions_sum_to_the_score_they_explain(bench):
    """Parts that do not add up to the whole are decoration, not an
    explanation. Two bugs made this false: normalizing a candidate against
    route-level spans, and counting an objective the scorer had skipped."""
    proposal = search.propose(bench, BALANCED, policy=Policy.permissive(),
                              strategy="greedy")
    for decision in proposal.decisions:
        assert sum(decision.contributions.values()) == pytest.approx(
            decision.score), decision.stage


def test_a_metric_every_candidate_shares_carries_no_weight():
    """It says nothing about the choice. Counting it made two identical
    situations score differently depending on which direction someone wrote."""
    flat = OptimizationProfile(id="p.flat", objectives=(
        OptimizationObjective("quality", "maximize", 0.5),
        OptimizationObjective("cost_usd", "minimize", 0.5)))
    items = {"a": {"quality": 0.9, "cost_usd": 1.0},
             "b": {"quality": 0.1, "cost_usd": 1.0}}
    ranked = flat.rank(items)
    assert ranked[0] == ("a", pytest.approx(1.0))
    assert ranked[1] == ("b", pytest.approx(0.0))


def test_the_direction_of_a_flat_metric_does_not_change_the_score():
    up = OptimizationProfile(id="p.up", objectives=(
        OptimizationObjective("flat", "maximize", 1.0),
        OptimizationObjective("quality", "maximize", 1.0)))
    down = OptimizationProfile(id="p.down", objectives=(
        OptimizationObjective("flat", "minimize", 1.0),
        OptimizationObjective("quality", "maximize", 1.0)))
    items = {"a": {"flat": 5, "quality": 0.9}, "b": {"flat": 5, "quality": 0.1}}
    assert [s for _, s in up.rank(items)] == [s for _, s in down.rank(items)]


def test_a_decision_names_its_runner_up(bench):
    proposal = search.propose(bench, BALANCED, policy=Policy.permissive(),
                              strategy="greedy")
    session = next(d for d in proposal.decisions if d.stage == "session")
    assert session.alternatives, "70 candidates should leave a runner-up"
    assert all(a != session.chosen for a, _ in session.alternatives)


def test_decisions_serialise_with_the_proposal(bench):
    data = search.propose(bench, BALANCED, policy=Policy.permissive(),
                          strategy="greedy").to_dict()
    assert data["decisions"] and "contributions" in data["decisions"][0]


# --- enumeration has to be physically possible ------------------------------

def test_explicit_exhaustive_refuses_a_space_it_cannot_enumerate():
    """It used to try. On the demonstration workbench that is 3.8 trillion
    routes; the process reached 53GB and the machine had to be rescued.

    `strategy="auto"` always checked the limit — asking for exhaustive by name
    skipped the check, which is the worst possible place for a guard to be
    missing, because naming the strategy is what a person does when they want
    to be careful.
    """
    from browsergraph import search
    from browsergraph.demo import workbench
    from browsergraph.policy import Policy

    bench = workbench()
    with pytest.raises(search.SpaceTooLarge) as caught:
        search.propose(bench, bench.optimization_profiles[0],
                       policy=Policy.permissive(), strategy="exhaustive")
    message = str(caught.value)
    assert "exceeds" in message and "limit" in message
    assert "beam" in message, "a refusal should name the thing to do instead"


def test_compare_strategies_skips_exhaustive_and_says_so():
    """Silently returning two keys where three were expected is how a caller
    reports a best-of-three that was a best-of-two."""
    from browsergraph import search
    from browsergraph.demo import workbench
    from browsergraph.policy import Policy

    bench = workbench()
    got = search.compare_strategies(bench, bench.optimization_profiles[0],
                                    policy=Policy.permissive())
    assert set(got) == {"greedy", "beam"}
    assert any("exhaustive not run" in note
               for proposal in got.values() for note in proposal.notes)


def test_exhaustive_still_runs_when_the_space_genuinely_fits(small_bench):
    """The guard must not have turned enumeration off in general.

    `small_bench` is 625 routes and exists for exactly this. An earlier draft of
    this test raised the limit on the *demonstration* workbench instead, asking
    a fixed guard to enumerate two billion routes — committing, inside the test
    for the guard, the mistake the guard exists to prevent.
    """
    from browsergraph import search
    from browsergraph.policy import Policy

    got = search.propose(small_bench, BALANCED, policy=Policy.permissive(),
                         strategy="exhaustive")
    assert got.strategy == "exhaustive"
    assert got.examined == small_bench.route_count() == 625


# --- choosing a route without looking at all of them -------------------------

def _locked_policy():
    from browsergraph.policy import Policy
    return Policy(permissions=frozenset({"filesystem", "filesystem:read",
                                         "filesystem:write", "database",
                                         "database:read"}),
                  allow_external_effects=False, deterministic_only=True,
                  name="locked-down")


def test_a_budget_is_the_question_a_caller_actually_has(bench):
    """Every other entry point asks *how* to search. This one asks how much."""
    from browsergraph import search

    got = search.within(bench, bench.optimization_profiles[0],
                        policy=_locked_policy(), evaluations=300)
    assert got.ok
    assert got.examined <= 300
    assert got.strategy == "greedy+sprouts"


def test_a_budgeted_search_never_does_worse_than_the_cheap_baseline(bench):
    """Anchoring to the greedy route is what guarantees this. Sampling from
    scratch under the same budget scored 0.911 where greedy scored 1.0875 —
    it spent the whole allowance rediscovering what greedy knew for free."""
    from browsergraph import search

    policy = _locked_policy()
    for profile in bench.optimization_profiles:
        greedy = search.propose(bench, profile, policy=policy, strategy="greedy")
        budgeted = search.within(bench, profile, policy=policy, evaluations=600)
        assert budgeted.score >= greedy.score - 1e-9, profile.name


def test_refining_finds_what_greedy_misses_on_the_profile_it_loses(bench):
    """"Never worse" is trivially true if refinement never helps. It does:
    greedy is suboptimal on the speed profile, and the budget recovers it."""
    from browsergraph import search

    policy = _locked_policy()
    speed = next(p for p in bench.optimization_profiles
                 if "peed" in p.name)
    greedy = search.propose(bench, speed, policy=policy, strategy="greedy")
    budgeted = search.within(bench, speed, policy=policy, evaluations=800)
    assert budgeted.score > greedy.score


def test_a_sampled_search_says_it_only_found_a_best(bench):
    """A good score from 800 looks at 1.9 billion routes is not a proven
    optimum, and the proposal has to say so or someone will quote it as one."""
    from browsergraph import search

    got = search.within(bench, bench.optimization_profiles[0],
                        policy=_locked_policy(), evaluations=500)
    assert any("never a proven best" in note for note in got.notes)


def test_the_same_budget_and_seed_give_the_same_route(bench):
    """A search that answers differently each run cannot be compared with
    itself, and "we changed the graph and the score moved" stops meaning
    anything."""
    from browsergraph import search

    policy = _locked_policy()
    first = search.within(bench, bench.optimization_profiles[0],
                          policy=policy, evaluations=400, seed=7)
    second = search.within(bench, bench.optimization_profiles[0],
                           policy=policy, evaluations=400, seed=7)
    assert first.route == second.route


def test_a_space_that_fits_the_budget_is_enumerated_rather_than_sampled(small_bench):
    """Sampling a space you could have enumerated throws away certainty for
    nothing."""
    from browsergraph import search

    got = search.within(small_bench, BALANCED, evaluations=5000)
    assert got.strategy == "exhaustive"
    assert got.examined == small_bench.route_count()


def test_sprouts_and_halving_stay_inside_their_budget(bench):
    from browsergraph import search

    policy = _locked_policy()
    for strategy in ("sprouts", "halving"):
        got = search.propose(bench, bench.optimization_profiles[0],
                             policy=policy, strategy=strategy, budget=250)
        assert got.examined <= 250, strategy
        assert got.ok, strategy


# --- evidence has to reach the score, not just the starting point ------------

def _rigged(bench, policy, seed=11):
    """A hidden truth: one candidate per stage genuinely works better.

    Deliberately unrelated to the declared metrics, which are illustrative
    priors. That gap is the point — evidence is only worth keeping if it can
    overrule a guess.
    """
    import random

    from browsergraph.evidence import stages_of

    stages = stages_of(bench, policy)
    rng = random.Random(seed)
    truth = {c: rng.betavariate(2, 3) for cs in stages.values() for c in cs}
    return stages, truth


def _mean_true_quality(route, truth):
    return sum(truth[c] for c in route.values()) / len(route)


def test_evidence_changes_which_route_is_chosen(bench, locked):
    """It did not, and that was the bug.

    Evidence was used to pick a starting point while scoring still ran on the
    declared priors, so the search walked straight back to whatever the priors
    liked. Two hundred simulated runs moved the answer not at all — 7% of
    stages on the truly-best candidate before and after.
    """
    import random

    from browsergraph import search
    from browsergraph.evidence import Evidence, Observation

    stages, truth = _rigged(bench, locked)
    rng = random.Random(3)
    store = Evidence()

    cold = search.within(bench, bench.optimization_profiles[0], policy=locked,
                         evaluations=300, evidence=store)

    for run in range(400):
        route = store.suggest(stages, ("global",), seed=run)
        for candidate in route.values():
            store.observe(Observation(candidate=candidate, context="global",
                                      ok=rng.random() < truth[candidate]))

    warm = search.within(bench, bench.optimization_profiles[0], policy=locked,
                         evaluations=300, evidence=store)

    assert warm.route != cold.route, "evidence made no difference at all"
    assert _mean_true_quality(warm.route, truth) > \
        _mean_true_quality(cold.route, truth)


def test_more_evidence_keeps_moving_the_answer_the_right_way(bench, locked):
    """Monotone in the thing that matters, not in an exact-match count.

    "How many stages picked the single best candidate" is noisy — it can dip
    while the route genuinely improves. Mean true quality is the honest measure.
    """
    import random

    from browsergraph import search
    from browsergraph.evidence import Evidence, Observation

    stages, truth = _rigged(bench, locked)
    rng = random.Random(3)
    store = Evidence()
    scores = []

    for target in (0, 120, 600):
        while len(store.routes) < target:
            route = store.suggest(stages, ("global",), seed=len(store.routes))
            for candidate in route.values():
                store.observe(Observation(candidate=candidate, context="global",
                                          ok=rng.random() < truth[candidate]))
            store.routes.append((tuple(route.values()), "global", 1.0))
        got = search.within(bench, bench.optimization_profiles[0], policy=locked,
                            evaluations=300, evidence=store)
        scores.append(_mean_true_quality(got.route, truth))

    assert scores[-1] > scores[0], f"no improvement across evidence: {scores}"


def test_one_observation_nudges_a_prior_and_fifty_overrule_it():
    """Shrinkage, not replacement. The weight is `Posterior.confidence`, which
    exists for exactly this and says so in its own docstring."""
    from browsergraph.evidence import Evidence, Observation, measured_metrics

    store = Evidence()
    store.observe(Observation(candidate="c.one", context="global", ok=False))
    barely = measured_metrics(store, ["c.one"], ("global",), {"c.one": 0.95})

    for _ in range(60):
        store.observe(Observation(candidate="c.one", context="global", ok=False))
    firmly = measured_metrics(store, ["c.one"], ("global",), {"c.one": 0.95})

    assert barely["c.one"]["quality"] > firmly["c.one"]["quality"]
    assert barely["c.one"]["quality"] > 0.5, "one failure should not erase a prior"
    assert firmly["c.one"]["quality"] < 0.3, "sixty failures should overrule it"


def test_a_candidate_with_no_evidence_keeps_its_prior():
    """Silence is not a measurement of zero."""
    from browsergraph.evidence import Evidence, measured_metrics

    assert measured_metrics(Evidence(), ["c.unseen"], ("global",),
                            {"c.unseen": 0.9}) == {}


def test_metrics_can_be_overridden_per_candidate_not_only_per_node(bench):
    """Metrics live on the node and several candidates share one, so a route
    cannot be scored on evidence unless the override is per candidate."""
    from browsergraph.policy import aggregate

    route = {s.id: s.candidates[0] for s in bench.leaf_stages if s.candidates}
    plain = aggregate(bench, route)
    nudged = aggregate(bench, route,
                       {next(iter(route.values())): {"quality": 0.01}})
    assert nudged["quality"] < plain["quality"]
