"""Every pack has to actually run.

A pack that has never been executed is a template with extra steps — which is
exactly the gap packs exist to close, so it would be a poor joke to ship one.
These build each pack, validate it, run it end to end, and check the specific
claim its docstring makes.
"""
from __future__ import annotations

import pytest

from browsergraph import execute, packs, solve
from browsergraph.compile import compile_route

ALL = packs.available()


def test_there_are_packs_to_test():
    """A registry that shrank to nothing would make every test below vacuous."""
    assert len(ALL) >= 3


@pytest.mark.parametrize("name", ALL)
def test_a_pack_builds_a_valid_workbench(name):
    bench = packs.get(name).workbench()
    assert bench.validate() == []
    assert bench.route_count() > 1, "a pack with one route is not a graph"


@pytest.mark.parametrize("name", ALL)
def test_every_step_has_a_function_and_every_function_has_a_step(name):
    """A candidate with no implementation fails only when that route is tried,
    and a function with no candidate is dead code nobody will delete."""
    pack = packs.get(name)
    bench = pack.workbench()
    runtime = pack.runtime(**pack.example())

    declared = {c for stage in bench.leaf_stages for c in stage.candidates}
    implemented = set(runtime.candidates)
    assert declared - implemented == set(), "candidates with no function"
    assert implemented - declared == set(), "functions no step can reach"


@pytest.mark.parametrize("name", ALL)
def test_a_pack_runs_every_one_of_its_routes(name):
    """Not a sample. A pack is small enough to try exhaustively, and 'most
    routes work' is the claim that hides the one that does not."""
    from browsergraph import search
    from browsergraph.policy import Policy

    pack = packs.get(name)
    bench = pack.workbench()
    # Permissive, because several packs read the filesystem and declare it.
    # Under the default policy those candidates are correctly unavailable and
    # there are no complete routes at all — which is the right answer to a
    # different question than the one this test asks.
    routes = list(search.eligible_routes(bench, policy=Policy.permissive()))
    assert routes, "no eligible routes"

    failures = []
    for route in routes:
        runtime = pack.runtime(**pack.example())
        run = execute.run(compile_route(bench, route), runtime, strict=True)
        if not run.ok:
            bad = next(s for s in run.steps if not s.ok)
            failures.append(f"{bad.stage}={bad.candidate}: {bad.error[:80]}")

    # Some routes are *meant* to fail — the files pack has a JSON parser aimed
    # at a folder holding a CSV. What must not happen is most of them failing,
    # which would mean the pack works by luck.
    assert len(failures) <= len(routes) // 3, (
        f"{len(failures)} of {len(routes)} routes failed:\n  "
        + "\n  ".join(failures[:6]))


@pytest.mark.parametrize("name", ALL)
def test_a_pack_solves_its_own_example(name):
    answer = packs.get(name).solve(attempts=6)
    assert answer.ok, f"{name} could not solve its own example"
    assert answer.champion


def test_the_catalog_names_every_pack():
    text = packs.catalog_text()
    for name in ALL:
        assert name in text


def test_an_unknown_pack_says_what_there_is():
    with pytest.raises(KeyError, match="files"):
        packs.get("no-such-pack")


# --- the specific claim each pack makes -------------------------------------

def test_files_needs_the_dispatching_parser_to_read_a_mixed_folder():
    """The pack's own argument: one parser per folder is not enough, and only a
    verifier that counts records can tell the difference."""
    pack = packs.get("files")
    bench = pack.workbench()
    kept = {}
    for parser in ("parse.json", "parse.csv", "parse.auto"):
        runtime = pack.runtime(**pack.example())
        route = {"list": "list.folder", "each": parser,
                 "split": "split.required", "summarise": "summarise.count"}
        run = execute.run(compile_route(bench, route), runtime, strict=False)
        kept[parser] = (len(run.output("split", "ok"))
                        if run.ok else 0)
    assert kept["parse.auto"] > kept["parse.json"]
    assert kept["parse.auto"] > kept["parse.csv"]


def test_quality_adjudicators_actually_disagree():
    """Three candidates that always agree are one candidate with extra names."""
    pack = packs.get("quality")
    bench = pack.workbench()
    verdicts = {}
    for rule in ("adjudicate.any", "adjudicate.severity", "adjudicate.schema_only"):
        runtime = pack.runtime(**pack.example())
        route = {"profile": "profile.basic", "schema": "schema.required",
                 "distribution": "distribution.nulls", "adjudicate": rule}
        run = execute.run(compile_route(bench, route), runtime)
        verdicts[rule] = run.output("adjudicate")["allow"]
    assert len(set(verdicts.values())) > 1, f"all agreed: {verdicts}"


def test_quality_robust_check_catches_what_three_sigma_masks():
    """The documented comparison, measured. One extreme value inflates the
    standard deviation enough to hide itself; the median does not move."""
    pack = packs.get("quality")
    bench = pack.workbench()
    found = {}
    for check in ("distribution.outliers", "distribution.robust"):
        runtime = pack.runtime(**pack.example())
        route = {"profile": "profile.detailed", "schema": "schema.required",
                 "distribution": check, "adjudicate": "adjudicate.any"}
        run = execute.run(compile_route(bench, route), runtime)
        found[check] = len(run.output("distribution")["findings"])
    assert found["distribution.outliers"] == 0, "three-sigma should miss it"
    assert found["distribution.robust"] >= 1, "the robust check should catch it"


def test_tabular_carries_its_encoding_with_the_model():
    """The bug this pack shipped with: evaluation re-derived the category levels
    from the held-out rows, so a weight meaning 'is a flat' was multiplied by a
    column meaning 'is a house'. Nothing raised and the wrong encoder won."""
    pack = packs.get("tabular")
    bench = pack.workbench()
    scores = {}
    for encoder in ("categorical.onehot", "categorical.ordinal"):
        runtime = pack.runtime(**pack.example())
        route = {"load": "load.rows", "split": "split.random",
                 "clean": "clean.drop", "numeric": "numeric.raw",
                 "categorical": encoder, "assemble": "assemble.concat",
                 "fit": "fit.ridge", "calibrate": "calibrate.none",
                 "evaluate": "evaluate.rmse"}
        run = execute.run(compile_route(bench, route), runtime)
        model = run.output("fit")
        assert "encoding" in model, "the model must carry its own encoding"
        assert model["encoding"]["levels"], "and the levels it was fitted under"
        scores[encoder] = run.output("evaluate")["score"]

    assert scores["categorical.onehot"] < scores["categorical.ordinal"], (
        f"one-hot is the right encoding for an unordered category and must "
        f"score better: {scores}")
    # The noise in the example has sd 12, so a correct fit lands near it. A
    # loose bound, because the point is "recovers the signal", not a golden
    # number that breaks when the example changes.
    assert scores["categorical.onehot"] < 20


def test_tabular_recovers_the_coefficients_it_was_built_from():
    """The example is price = 40*size + 3*age + a kind effect. If the pack
    cannot find that, it is arithmetic that runs rather than a model."""
    pack = packs.get("tabular")
    runtime = pack.runtime(**pack.example())
    route = {"load": "load.rows", "split": "split.ordered", "clean": "clean.drop",
             "numeric": "numeric.raw", "categorical": "categorical.onehot",
             "assemble": "assemble.concat", "fit": "fit.least_squares",
             "calibrate": "calibrate.none", "evaluate": "evaluate.mae"}
    run = execute.run(compile_route(pack.workbench(), route), runtime)
    weights = dict(zip(run.output("fit")["names"], run.output("fit")["weights"],
                       strict=True))
    assert abs(weights["size"] - 40) < 1.0
    assert abs(weights["age"] - 3) < 1.5


def test_a_pack_solve_helper_passes_its_example_to_the_runtime():
    """`example()` is configuration for `runtime()`, not graph inputs — a source
    step has no input ports, so 'which folder' cannot arrive as graph data."""
    pack = packs.get("files")
    answer = pack.solve(verify=solve.outputs_are_not_empty("summarise"),
                        attempts=4)
    assert answer.ok
