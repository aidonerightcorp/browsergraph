"""Tasks with a known best answer, so "did the search work" has an answer.

The benchmark can say which strategy won on real work. It cannot say *why*,
because nobody knows the best route on a real task — so "searching tied with
random" leaves two explanations standing. These tasks have a hidden ground
truth, which turns that into an experiment.
"""
from __future__ import annotations

import pytest

from browsergraph import arena, benchmark

BUDGET, REPEATS, SEED = 12, 5, 1


@pytest.fixture(scope="module")
def measured():
    """Every arena task, benchmarked once. Slow-ish, so shared."""
    out = {}
    for name in arena.TASKS:
        task = arena.get(name, steps=6, width=5, seed=SEED)
        out[name] = (task, benchmark.compare(
            task.workbench, task.runtime, verify=task.verify,
            budget=BUDGET, repeats=REPEATS))
    return out


def test_the_space_is_big_enough_for_the_question_to_be_interesting():
    """Six steps of five is 15,625 routes and the budget is twelve. If random
    sampling can cover the space the comparison proves nothing."""
    task = arena.needle(steps=6, width=5, seed=SEED)
    assert task.workbench.route_count() == 5 ** 6 == 15_625
    assert BUDGET * REPEATS < task.workbench.route_count() / 100


def test_the_flat_task_lets_nobody_win(measured):
    """The negative control, and the one worth running first. A benchmark that
    cannot produce this result is not measuring anything."""
    task, got = measured["flat"]
    assert task.best_score == task.worst_score
    scores = {s: got.best(s) for s in benchmark.STRATEGIES}
    assert len(set(round(v, 9) for v in scores.values())) == 1, scores
    assert "did NOT beat" in got.verdict


def test_searching_beats_random_when_the_steps_are_independent(measured):
    """The headline claim, isolated: per-step posteriors cost the sum of the
    candidate counts, not their product. If it fails here it is wrong."""
    task, got = measured["needle"]
    assert got.best("solve") > got.best("random")
    assert got.best("solve") > got.best("first")
    assert task.gap(got.best("solve")) < task.gap(got.best("random"))


def test_twelve_runs_out_of_fifteen_thousand_close_most_of_the_gap(measured):
    """The number that makes the claim worth anything: not that it improves,
    but that it improves this much from this little."""
    task, got = measured["needle"]
    assert task.gap(got.best("first")) > 0.6, "the baseline should be poor"
    assert task.gap(got.best("solve")) < 0.45, "and searching should close it"


def test_searching_still_helps_when_independence_is_false(measured):
    """`paired` makes two candidates excellent apart and terrible together,
    which is exactly the assumption the cheap search rests on."""
    task, got = measured["paired"]
    assert got.best("solve") > got.best("random")


def test_searching_survives_observation_noise(measured):
    """Separates a loop that converges from one that latched onto a lucky
    first result."""
    task, got = measured["noisy"]
    assert got.best("solve") > got.best("random")
    assert task.gap(got.best("solve")) < 0.45


def test_a_task_knows_its_own_best_and_worst_route():
    task = arena.needle(steps=4, width=4, seed=3)
    assert task.gap(task.best_score) == 0.0
    assert task.gap(task.worst_score) == 1.0
    assert task.best_score > task.worst_score


def test_the_paired_task_recomputes_its_best_over_the_whole_space():
    """Its best route is not the best candidate at every step — that pair is
    exactly what it penalises — so a per-step maximum would be wrong."""
    task = arena.paired(steps=3, width=3, seed=2)
    naive = {f"s{s}": max((f"s{s}.c{c}" for c in range(3)),
                          key=lambda cid: task.truth[cid]) for s in range(3)}
    assert task.best_route != naive or task.best_route["s0"] != "s0.c0"


def test_an_unknown_task_says_what_there_is():
    with pytest.raises(KeyError, match="needle"):
        arena.get("no-such-task")


@pytest.mark.parametrize("name", sorted(arena.TASKS))
def test_every_arena_task_builds_a_valid_graph(name):
    task = arena.get(name, steps=3, width=3, seed=0)
    assert task.workbench.validate() == []
    assert task.runtime.candidates
