"""Tasks with a known best answer, so "did the search work" has an answer.

The benchmark can say which strategy won on real work. It cannot say *why*,
because nobody knows the best route on a real task — so "searching tied with
random" leaves two explanations standing. These tasks have a hidden ground
truth, which turns that into an experiment.
"""
from __future__ import annotations

import pytest

from browsergraph import arena, benchmark
from browsergraph import edits as edits_mod

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
    # Only the strategies that actually ran. `guided` is dropped when there is
    # no library, and a strategy that never ran cannot tie or win.
    scores = {s: got.best(s) for s in got.by_strategy()}
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


# --- the ceiling a route search cannot cross ---------------------------------

def test_the_missing_step_task_has_a_structural_ceiling():
    """Every other task here is winnable by choosing better candidates. This
    one is not, at any budget: a route names one candidate per stage, so a
    search over routes cannot reach a graph with an extra stage in it."""
    task = arena.missing_step(steps=5, width=5, seed=1)
    assert task.ceiling_without_edit < task.best_score
    assert task.gap(task.ceiling_without_edit) > 0.5, (
        "the ceiling has to be far enough below perfect for the experiment to "
        "be able to show anything")


def test_a_route_search_cannot_beat_that_ceiling():
    """The floor, measured rather than argued. `solve` gets a budget large
    enough to search the space well and still cannot cross it."""
    task = arena.missing_step(steps=5, width=5, seed=1)
    got = benchmark.compare(task.workbench, task.runtime, verify=task.verify,
                            budget=40, repeats=2, strategies=("solve",))
    assert got.best("solve") <= task.ceiling_without_edit * 1.001


def test_editing_the_shape_crosses_it():
    """The whole argument for structural search, in one assertion. Same task,
    same run budget, and the only difference is being allowed to change the
    graph."""
    task = arena.missing_step(steps=5, width=5, seed=1)
    got = benchmark.compare(task.workbench, task.runtime, verify=task.verify,
                            budget=20, repeats=2, library=task.library)
    assert got.best("guided") > task.ceiling_without_edit, (
        f"guided {got.best('guided'):.4f} should pass the structural ceiling "
        f"{task.ceiling_without_edit:.4f}")
    assert got.best("guided") > got.best("solve")


def test_guided_is_dropped_when_there_is_nothing_to_propose():
    """Without a library there is no shape to change, so reporting it as a tie
    it never had a chance to break would be misleading."""
    task = arena.needle(steps=3, width=3, seed=0)
    got = benchmark.compare(task.workbench, task.runtime, verify=task.verify,
                            budget=4, repeats=1)
    assert "guided" not in got.by_strategy()


def test_guided_spends_no_more_runs_than_anything_else():
    """If it wins it must not be because it was allowed to run more."""
    task = arena.missing_step(steps=4, width=3, seed=0)
    got = benchmark.compare(task.workbench, task.runtime, verify=task.verify,
                            budget=12, repeats=1, library=task.library)
    guided = [r for r in got.results if r.strategy == "guided"][0]
    assert guided.runs <= 12
    assert "compiled" in guided.note, "the refusal rate belongs in the record"


# --- the haystack: is pruning the thing guidance is actually for? ------------

def test_the_haystack_distractors_are_all_legal():
    """The point is that the type checker cannot help. Twenty nodes, every one
    of which inserts anywhere and changes nothing, so telling them apart is a
    judgement rather than a check."""
    from browsergraph import edits

    task = arena.haystack(steps=5, width=4, seed=1)
    assert len(task.library) == 20
    proposals = edits.mechanical(task.workbench, task.library)
    got = edits.variants(task.workbench, proposals, library=task.library)
    assert got.refusal_rate == 0.0, "a distractor that cannot compile is not one"
    assert len(got.accepted) >= 60, "each node should fit at several positions"


def test_only_one_library_node_actually_helps():
    from browsergraph import edits, execute
    from browsergraph.compile import compile_route

    task = arena.haystack(steps=4, width=3, seed=2)
    scores = {}
    for manifest in task.library:
        first_edge = task.workbench.wiring()[0]
        outcome = edits.apply(task.workbench, edits.GraphEdit(
            "insert", (first_edge.source, first_edge.target), manifest.id),
            library=task.library)
        assert outcome.ok
        route = {s.id: s.candidates[0] for s in outcome.workbench.leaf_stages}
        run = execute.run(compile_route(outcome.workbench, route), task.runtime)
        scores[manifest.id] = task.verify(run)[1]

    better = [nid for nid, v in scores.items()
              if v > scores.get("cache.warm", 0) * 1.5]
    assert better == ["repair.fix"], f"exactly one should help: {better}"


def test_enumeration_is_complete_and_that_is_not_enough():
    """The finding worth keeping. Eighty legal edits and forty runs means one
    run each, whatever the allocator does — so a proposer offering everything is
    offering nothing usable. Guidance earns its keep by *pruning*, not by
    knowing something the type checker does not."""
    from browsergraph import edits

    task = arena.haystack(steps=5, width=4, seed=1)
    proposals = edits.mechanical(task.workbench, task.library)
    assert len(proposals) > 40, (
        "the whole point is that enumeration produces more variants than a "
        "realistic budget can evaluate")


def test_a_proposer_that_picks_one_concentrates_the_budget():
    """The mechanism, with the model's part written by hand so it is testable.
    One correct edit turns a structural floor into a real score; the same budget
    spread over eighty variants does not."""
    task = arena.haystack(steps=5, width=4, seed=1)
    right = [edits_mod.GraphEdit("insert", ("s0", "s1"), "repair.fix")]

    focused = benchmark.compare(
        task.workbench, task.runtime, verify=task.verify, budget=40, repeats=1,
        library=task.library, propose=lambda b, lib: right,
        strategies=("guided",))
    assert focused.best("guided") > task.ceiling_without_edit, (
        "one right edit should cross the structural ceiling")

