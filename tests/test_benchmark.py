"""The instrument has to be able to report a loss.

A benchmark that can only produce good news is not a benchmark. These check the
comparison is fair — same budget, same judge — and that an honest negative
verdict is reachable, because the first three tasks it was pointed at produced
one.
"""
from __future__ import annotations

import pytest

from browsergraph import benchmark, packs
from browsergraph.quick import chain, graph, node, step
from browsergraph.workbench import OptimizationObjective, OptimizationProfile


def _graded_bench(spread: bool):
    """Five candidates that all run. With `spread`, they differ in *quality*.

    The case the evidence loop was blind to: nothing fails, so a pass/fail
    signal cannot tell them apart at all.
    """
    from browsergraph.execute import Runtime

    nodes = [node("pick.a", "pick", gives=[("out", "N")]),
             node("pick.b", "pick", gives=[("out", "N")]),
             node("pick.c", "pick", gives=[("out", "N")]),
             node("use.it", "use", [("in", "N")], [("out", "R")])]
    steps = [step("pick", "Pick", [], [("out", "N")], "pick",
                  ["pick.a", "pick.b", "pick.c"]),
             step("use", "Use", [("in", "N")], [("out", "R")], "use", ["use.it"])]
    bench = graph("Graded", "every route runs; they differ in how well", steps,
                  nodes, chain("pick", "use"),
                  profiles=[OptimizationProfile(id="p", objectives=(
                      OptimizationObjective("quality", "maximize", 1.0),))])
    values = {"pick.a": 1.0, "pick.b": 5.0, "pick.c": 9.0} if spread else \
             {"pick.a": 5.0, "pick.b": 5.0, "pick.c": 5.0}
    runtime = Runtime({cid: (lambda v=v, **kw: v) for cid, v in values.items()}
                      | {"use.it": lambda **kw: kw["in"]})
    return bench, runtime


def _score(run):
    if not run.ok:
        return False, 0.0
    return True, float(run.output("use"))


def test_a_grade_reaches_the_posteriors():
    """The bug the benchmark found: `solve` recorded pass/fail and threw the
    score away, so on a task where everything runs it learned nothing and
    degenerated to random sampling."""
    from browsergraph import solve
    from browsergraph.evidence import Evidence

    bench, runtime = _graded_bench(spread=True)
    store = Evidence()
    solve.solve(bench, runtime, verify=_score, attempts=6, evidence=store)

    good = store.posterior("pick.c").quality
    bad = store.posterior("pick.a").quality
    assert good and bad, "both were run, so both should carry a grade"
    assert good > bad, "the better candidate must be recorded as better"


def test_a_grade_changes_the_ranking():
    """Recording it is half. `measured_metrics` has to read it, or the store
    holds a number nothing consults."""
    from browsergraph import solve
    from browsergraph.evidence import Evidence, measured_metrics

    bench, runtime = _graded_bench(spread=True)
    store = Evidence()
    solve.solve(bench, runtime, verify=_score, attempts=6, evidence=store)

    scored = measured_metrics(store, ["pick.a", "pick.b", "pick.c"], explore=0.0)
    assert scored["pick.c"]["quality"] > scored["pick.a"]["quality"]


def test_a_worse_candidate_is_not_scored_to_zero():
    """Multiplying the worst by zero would stop it ever being tried again —
    the same blindness in the other direction."""
    from browsergraph import solve
    from browsergraph.evidence import Evidence, measured_metrics

    bench, runtime = _graded_bench(spread=True)
    store = Evidence()
    solve.solve(bench, runtime, verify=_score, attempts=6, evidence=store)
    scored = measured_metrics(store, ["pick.a", "pick.b", "pick.c"], explore=0.0)
    assert scored["pick.a"]["quality"] > 0.0


def test_identical_candidates_get_no_manufactured_preference():
    """With no spread there is nothing to learn, and inventing an ordering from
    noise is worse than reporting none."""
    from browsergraph import solve
    from browsergraph.evidence import Evidence, measured_metrics

    bench, runtime = _graded_bench(spread=False)
    store = Evidence()
    solve.solve(bench, runtime, verify=_score, attempts=6, evidence=store)
    scored = measured_metrics(store, ["pick.a", "pick.b", "pick.c"], explore=0.0)
    values = {round(m["quality"], 9) for m in scored.values()}
    assert len(values) == 1, f"no spread should mean no preference: {scored}"


# --- the instrument itself ---------------------------------------------------

def test_every_strategy_gets_the_same_run_budget():
    """Otherwise the finding is 'whichever we let run more won'."""
    bench, runtime = _graded_bench(spread=True)
    got = benchmark.compare(bench, runtime, verify=_score, budget=5, repeats=1)
    for result in got.results:
        if result.strategy in ("random", "solve"):
            assert result.runs <= 5
        if result.strategy in ("first", "greedy"):
            assert result.runs == 1, "these stand for one plausible choice"


def test_a_tie_is_reported_as_a_tie():
    """An earlier verdict called a margin of +0.0% a win, because two medians
    differing in the twelfth decimal are not equal."""
    bench, runtime = _graded_bench(spread=False)
    got = benchmark.compare(bench, runtime, verify=_score, budget=4, repeats=2)
    assert "did NOT beat" in got.verdict


def test_the_verdict_can_say_a_cheaper_strategy_won():
    got = benchmark.Comparison(results=[
        benchmark.Result("first", score=1.0, ok=True),
        benchmark.Result("random", score=1.0, ok=True),
        benchmark.Result("greedy", score=9.0, ok=True),
        benchmark.Result("solve", score=5.0, ok=True)])
    assert "greedy" in got.verdict and "one run" in got.verdict


def test_a_verifier_that_cannot_read_a_failed_run_does_not_kill_the_comparison():
    """It is being asked to judge nothing. The honest reading is 'not
    acceptable', not a crash that loses every other number."""
    from browsergraph.execute import Runtime

    nodes = [node("boom.it", "boom", gives=[("out", "N")]),
             node("fine.it", "boom", gives=[("out", "N")])]
    steps = [step("boom", "Boom", [], [("out", "N")], "boom",
                  ["boom.it", "fine.it"])]
    bench = graph("Half broken", "one candidate raises", steps, nodes,
                  profiles=[OptimizationProfile(id="p", objectives=(
                      OptimizationObjective("quality", "maximize", 1.0),))])

    def explode(**kwargs):
        raise RuntimeError("no")

    runtime = Runtime({"boom.it": explode, "fine.it": lambda **kw: 7})
    got = benchmark.compare(bench, runtime,
                            verify=lambda run: (True, float(run.output("boom"))),
                            budget=4, repeats=1)
    assert any(r.ok for r in got.results), "the working candidate should score"


def test_benchmarking_needs_something_to_rank_by():
    bench, runtime = _graded_bench(spread=True)
    from dataclasses import replace
    bare = replace(bench, optimization_profiles=())
    with pytest.raises(ValueError, match="optimization profile"):
        benchmark.compare(bare, runtime, verify=_score)


@pytest.mark.parametrize("name", packs.available())
def test_a_pack_can_be_benchmarked_end_to_end(name):
    """The instrument has to work on the real packs, whatever it concludes."""
    pack = packs.get(name)

    def judge(run):
        if not run.ok:
            return False, 0.0
        return True, float(len(run.steps))

    got = benchmark.compare(pack.workbench(), pack.runtime(**pack.example()),
                            verify=judge, budget=3, repeats=1)
    assert got.results and got.verdict
    assert got.total_routes > 1
