"""One call that tries routes, judges the output, and keeps a fallback.

The pieces all existed and none of them were joined up. These tests are about
the joins — that judging is separate from running, that a fallback is part of
the answer, and that the loop actually explores rather than settling on its
first idea and reporting itself thorough.
"""
from __future__ import annotations

import pytest

from browsergraph import execute, solve
from browsergraph.quick import chain, graph, node, passthrough, step
from browsergraph.workbench import OptimizationObjective, OptimizationProfile

RAW = [{"v": 1}, {"v": None}, {"v": 3}, {"v": None}, {"v": 5}]


@pytest.fixture
def bench():
    nodes = [node("load.rows", "load", gives=[("out", "Rows")]),
             node("impute.median", "impute", [("in", "Rows")], [("out", "Rows")]),
             node("impute.drop", "impute", [("in", "Rows")], [("out", "Rows")]),
             passthrough("impute.none", "impute", "Rows"),
             node("check.rows", "check", [("in", "Rows")], [("out", "Rows")])]
    steps = [
        step("load", "Load", [], [("out", "Rows")], "load", ["load.rows"]),
        step("impute", "Fill the gaps", [("in", "Rows")], [("out", "Rows")],
             "impute", ["impute.median", "impute.drop", "impute.none"]),
        step("check", "Keep good rows", [("in", "Rows")], [("out", "Rows")],
             "check", ["check.rows"]),
    ]
    return graph("Clean records", "Fill gaps if needed, keep usable rows.",
                 steps, nodes, chain("load", "impute", "check"),
                 profiles=[OptimizationProfile(id="p", objectives=(
                     OptimizationObjective("quality", "maximize", 1.0),))])


@pytest.fixture
def runtime():
    return execute.Runtime({
        "load.rows": lambda: [dict(r) for r in RAW],
        "impute.median": lambda **kw: [{"v": 3 if r["v"] is None else r["v"]}
                                       for r in kw["in"]],
        "impute.drop": lambda **kw: [r for r in kw["in"] if r["v"] is not None],
        "impute.none": lambda **kw: kw["in"],
        "check.rows": lambda **kw: [r for r in kw["in"] if r["v"] is not None],
    })


def test_it_finds_the_route_that_produces_the_most(bench, runtime):
    """Imputing keeps five rows; dropping and doing nothing keep three."""
    got = solve.solve(bench, runtime,
                      verify=solve.outputs_are_not_empty("check"), attempts=8)
    assert got.ok
    assert got.champion["impute"] == "impute.median"
    assert got.score == 5.0


def test_it_actually_explores_rather_than_settling_on_its_first_idea(bench, runtime):
    """An early version stopped at the first repeated proposal, so a three-route
    space got two attempts and called itself thorough. A settled search
    proposing its favourite twice is expected, not a signal to stop."""
    got = solve.solve(bench, runtime,
                      verify=solve.outputs_are_not_empty("check"), attempts=8)
    assert len({tuple(sorted(a.route.items())) for a in got.attempts}) == 3


def test_a_fallback_is_part_of_the_answer(bench, runtime):
    """A champion with no runner-up is a single point of failure dressed as a
    result."""
    got = solve.solve(bench, runtime,
                      verify=solve.outputs_are_not_empty("check"), attempts=8)
    assert got.fallbacks
    assert got.fallbacks[0] != got.champion


def test_judging_is_separate_from_running(bench, runtime):
    """Every route here runs without raising. Only the verifier can tell them
    apart, which is the whole point of it being a separate argument."""
    lenient = solve.solve(bench, runtime, attempts=6)
    assert all(a.ok for a in lenient.attempts), "every route ran cleanly"

    strict = solve.solve(bench, runtime, attempts=6,
                         verify=lambda run: (len(run.output("check")) >= 5,
                                             float(len(run.output("check")))))
    assert strict.champion["impute"] == "impute.median"
    assert any(not a.ok for a in strict.attempts), \
        "a stricter verifier must reject something the lenient one accepted"


def test_the_default_verifier_says_it_is_weak():
    assert "deliberately a poor one" in solve._accept_anything_that_ran.__doc__


def test_a_workbench_with_no_profile_is_refused_with_a_reason(runtime):
    from browsergraph.quick import graph as _graph

    bare = _graph("No profile", "t",
                  [step("s", "S", [], [("out", "X")], "cap", ["n.one"])],
                  [node("n.one", "cap", gives=[("out", "X")])])
    with pytest.raises(ValueError, match="nothing to rank routes by"):
        solve.solve(bare, runtime)


def test_what_it_learned_survives_into_the_evidence_store(bench, runtime):
    got = solve.solve(bench, runtime,
                      verify=solve.outputs_are_not_empty("check"), attempts=6)
    assert got.evidence.posterior("impute.median").runs > 0


def test_the_report_says_how_much_of_the_space_it_saw(bench, runtime):
    text = solve.solve(bench, runtime,
                       verify=solve.outputs_are_not_empty("check"),
                       attempts=8).text()
    assert "out of 3 possible" in text


# --- the bug that made all of the above impossible --------------------------

def test_the_optimism_bonus_is_not_clamped_away():
    """It was, and the effect was total.

    `min(1.0, shrunk + explore * spread)` saturated every candidate whose prior
    plus bonus reached 1.0 — and an undeclared prior *is* 1.0, so that was
    almost all of them. Tried and untried came out identical, the search went
    blind, and `solve` could not get past its first route.
    """
    from browsergraph.evidence import Evidence, Observation, measured_metrics

    store = Evidence()
    for _ in range(2):
        store.observe(Observation(candidate="tried", context="global", ok=True))

    got = measured_metrics(store, ["tried", "untried"], ("global",),
                           {"tried": 1.0, "untried": 1.0})
    assert got["untried"]["quality"] > got["tried"]["quality"], \
        "an untried candidate must outrank a tried one when priors are equal"


def test_a_passthrough_keeps_an_optional_step_in_the_graph():
    """An optional step is not the same as a missing one. Two routes differing
    only in whether they imputed are comparable when both have an impute step;
    they are different graphs when one does not."""
    doing_nothing = passthrough("impute.none", "impute", "Rows")
    assert [p.type for p in doing_nothing.inputs] == ["Rows"]
    assert [p.type for p in doing_nothing.outputs] == ["Rows"]
    assert doing_nothing.runtime["deterministic"] is True
