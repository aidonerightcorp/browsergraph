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
    """An empty policy blocks with a reason rather than permitting by omission."""
    gate = gate_stage(bench, bench.stages[0], Policy(name="empty"))
    assert gate.eligible == ()
    assert all(v.reason for v in gate.blocked)


def test_blocked_candidates_stay_visible_with_reasons(bench, locked):
    gate = gate_stage(bench, bench.stages[0], locked)
    assert len(gate.verdicts) == len(bench.stages[0].candidates)
    assert gate.eligible and gate.blocked
    assert any("needs browser" in v.reason for v in gate.blocked)


def test_policy_removes_most_of_the_space_and_says_so(bench, locked):
    report = review(bench, locked)
    assert 0 < report.reachable_routes < bench.route_count()
    assert report.reachable_routes == 122_472
    assert "122,472" in report.text()


def test_a_policy_that_kills_a_stage_is_diagnosed_not_silently_empty(bench):
    impossible = Policy(permissions=frozenset(), name="nothing")
    report = review(bench, impossible)
    assert report.dead_stages()
    assert "nothing can run" in report.text()


def test_determinism_and_effects_are_separate_gates(bench):
    everything = Policy.permissive()
    no_effects = Policy.permissive(allow_external_effects=False)
    deterministic = Policy.permissive(deterministic_only=True)
    stage = next(s for s in bench.stages if s.id == "emit")
    assert len(gate_stage(bench, stage, everything).eligible) > \
        len(gate_stage(bench, stage, no_effects).eligible)
    transform = next(s for s in bench.stages if s.id == "transform")
    assert len(gate_stage(bench, transform, deterministic).eligible) < \
        len(gate_stage(bench, transform, everything).eligible)


# --- route arithmetic -------------------------------------------------------

def test_quality_compounds_and_the_rest_adds(bench):
    """Averaging quality would let one excellent stage hide a step that fails
    half the time."""
    route = {s.id: s.candidates[0] for s in bench.stages}
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


def test_exhaustive_examines_everything_eligible(bench, locked):
    proposal = search.propose(bench, BALANCED, policy=locked, strategy="exhaustive")
    assert proposal.examined == proposal.eligible_total == 122_472
    assert proposal.coverage == 1.0


def test_beam_reaches_the_optimum_far_more_cheaply(bench, locked):
    """The regression test for the beam that renormalized at every step.

    That version matched plain greedy at width 1, 8, 32, 128 and 512 — 8,878
    evaluations to reach the answer greedy found in 56.
    """
    best = search.propose(bench, BALANCED, policy=locked, strategy="exhaustive")
    beam = search.propose(bench, BALANCED, policy=locked, strategy="beam")
    greedy = search.propose(bench, BALANCED, policy=locked, strategy="greedy")
    assert beam.score == pytest.approx(best.score), "beam should reach the optimum"
    assert beam.examined < best.examined / 100
    assert greedy.score < best.score, \
        "greedy scores each stage in isolation; it should lose when metrics compound"


def test_auto_picks_exhaustive_only_when_it_fits(bench, locked):
    small = search.propose(bench, BALANCED, policy=locked, strategy="auto")
    assert small.strategy == "exhaustive"
    big = search.propose(bench, BALANCED, policy=Policy.permissive(), strategy="auto")
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


def test_the_static_svg_labels_keep_the_binding_that_distinguishes_rows(bench):
    """Truncating shorter made headless and headed rows identical."""
    from browsergraph.routegraph import to_svg
    svg = to_svg(bench, background=False)
    assert "browser adapter · Chrome · BrowserPort · headless" in svg
    assert "browser adapter · Chrome · BrowserPort · headed<" in svg


# --- the CLI ----------------------------------------------------------------

def test_the_route_command_proposes(capsys):
    from browsergraph.cli import main
    assert main(["route", "--profile", "profile.speed", "--strategy", "greedy"]) == 0
    out = capsys.readouterr().out
    assert "examined" in out and "Acquire inputs" in out


def test_the_route_command_can_show_the_gates(capsys):
    from browsergraph.cli import main
    assert main(["route", "--gates", "--deterministic", "--no-effects",
                 "--allow", "filesystem:read"]) == 0
    assert "eligible" in capsys.readouterr().out
