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


# --- the claims the new packs make ------------------------------------------
#
# Each of these is a sentence from a pack's docstring, turned into arithmetic.
# A pack that argues something in prose and cannot demonstrate it is a pack
# making a claim, which is the thing this repository is arranged against.

def _route(pack_name: str, **route):
    """Run one route of a pack and give back the finished run."""
    pack = packs.get(pack_name)
    return execute.run(compile_route(pack.workbench(), route),
                       pack.runtime(**pack.example()))


def test_harness_controls_catch_the_grader_that_measures_length():
    """The pack's argument: a grader that produces a moving number and
    measures nothing is caught by the controls and by nothing else."""
    base = {"cases": "cases.builtin", "controls": "controls.paired",
            "run": "run.keyword", "aggregate": "aggregate.sliced",
            "duecare": "duecare.full", "report": "report.text"}
    fraud = _route("harness", **base, grade="grade.length").output("duecare")
    real = _route("harness", **base, grade="grade.contains").output("duecare")

    assert fraud["state"] == "FAIL"
    assert "negative_control" in [f["obligation"] for f in fraud["failed"]]
    assert real["state"] == "PASS", real["failed"]
    assert real["score"] > fraud["score"]


def test_harness_without_controls_can_never_pass_only_go_provisional():
    """'We have not checked' has to be a state, or it gets rendered as a pass."""
    verdict = _route("harness", cases="cases.builtin", controls="controls.none",
                     run="run.keyword", grade="grade.contains",
                     aggregate="aggregate.sliced", duecare="duecare.full",
                     report="report.text").output("duecare")
    assert verdict["state"] == "PROVISIONAL"
    assert "negative_control" in verdict["outstanding"]


def test_harness_verdict_is_not_the_mean():
    """A route graded by answer length scores well on the mean it reports and
    zero through the pack's own verifier."""
    from browsergraph.packs import harness

    run = _route("harness", cases="cases.builtin", controls="controls.paired",
                 run="run.first", grade="grade.length",
                 aggregate="aggregate.mean", duecare="duecare.full",
                 report="report.text")
    ok, score = harness.trustworthy(run)
    assert run.output("duecare")["summary"]["overall"] > 0
    assert not ok and score == 0.0


def test_judge_raw_agreement_flatters_where_kappa_does_not():
    base = {"items": "items.natural", "rubric": "rubric.criteria",
            "human": "human.full", "calibrate": "calibrate.none"}
    biased = _route("judge", **base, judge="judge.confident",
                    agreement="agreement.kappa").output("agreement")
    assert biased["raw"] >= 0.7, "the number a team would report"
    assert biased["kappa"] < 0, "and it agrees with people less than chance"
    assert biased["raw"] <= biased["always_good_would_score"], (
        "a judge that never disagreed would have scored at least as well")


def test_judge_the_rubric_matters_more_than_the_judge():
    """One candidate changed, upstream of the judge, and it inverts it."""
    base = {"items": "items.balanced", "judge": "judge.rubric",
            "human": "human.full", "agreement": "agreement.kappa",
            "calibrate": "calibrate.none"}
    good = _route("judge", **base, rubric="rubric.criteria").output("agreement")
    vague = _route("judge", **base, rubric="rubric.holistic").output("agreement")
    assert good["kappa"] > 0.6
    assert vague["kappa"] < 0
    assert good["kappa"] - vague["kappa"] > 1.0


def test_redteam_more_attacks_can_mean_less_coverage():
    base = {"surface": "surface.declared", "detector": "detector.policy",
            "execute": "execute.guarded", "detect": "detect.any_rule",
            "adjudicate": "adjudicate.coverage"}
    narrow = _route("redteam", **base, attacks="attacks.direct").output("adjudicate")
    broad = _route("redteam", **base, attacks="attacks.families").output("adjudicate")

    assert narrow["attacks"] > broad["attacks"], "more attempts"
    assert narrow["families_tried"] < broad["families_tried"], "less coverage"
    assert not narrow["families_breached"], "and it finds nothing"
    assert len(broad["families_breached"]) == 4


def test_redteam_a_system_grading_itself_finds_nothing():
    base = {"surface": "surface.declared", "attacks": "attacks.families",
            "execute": "execute.guarded", "detect": "detect.any_rule",
            "adjudicate": "adjudicate.coverage"}
    honest = _route("redteam", **base, detector="detector.policy").output("adjudicate")
    itself = _route("redteam", **base,
                    detector="detector.selfreport").output("adjudicate")
    assert len(honest["families_breached"]) == 4
    assert itself["findings"] == 0, (
        "an attack that succeeds is one the system did not notice, so a "
        "self-report detector cannot find one")


def test_redteam_a_detector_fitted_to_its_attacks_is_wrong_in_both_directions():
    """One false positive on the family that was blocked, and silence on the
    four that got through."""
    found = _route("redteam", surface="surface.declared",
                   attacks="attacks.families", detector="detector.keywords",
                   execute="execute.guarded", detect="detect.any_rule",
                   adjudicate="adjudicate.coverage").output("adjudicate")
    assert found["families_breached"] == ["direct"], (
        "direct is the family the guard blocks — this finding is false")
    assert found["findings"] > 0


def test_agents_the_best_looking_answer_is_the_fabricated_one():
    base = {"brief": "brief.four_fields", "plan": "plan.disjoint",
            "work": "work.confident"}
    invented = _route("agents", **base, critic="critic.none",
                      synthesise="synthesise.all", verify="verify.covers_brief")
    checked = _route("agents", **base, critic="critic.grounded",
                     synthesise="synthesise.filtered", verify="verify.covers_brief")

    assert len(invented.output("synthesise")["fields"]) == 4, "looks complete"
    assert len(checked.output("synthesise")["fields"]) == 3, "looks incomplete"
    assert not invented.output("verify")["ok"]
    assert checked.output("verify")["ok"]
    assert invented.output("verify")["ungrounded_fields"] == ["insurer"]


def test_agents_a_nonempty_check_passes_the_fabrication():
    """What most pipelines actually check, on the row that is wrong."""
    lenient = _route("agents", brief="brief.four_fields", plan="plan.disjoint",
                     work="work.confident", critic="critic.none",
                     synthesise="synthesise.all",
                     verify="verify.nonempty").output("verify")
    assert lenient["ok"]
    assert lenient["ungrounded"] == 1


def test_agents_a_silent_refusal_is_caught_and_a_declared_gap_is_not():
    """A worker that returned nothing loses a field; a worker that said the
    source does not answer has answered."""
    refused = _route("agents", brief="brief.four_fields", plan="plan.disjoint",
                     work="work.flaky", critic="critic.grounded",
                     synthesise="synthesise.filtered",
                     verify="verify.covers_brief").output("verify")
    declared = _route("agents", brief="brief.four_fields", plan="plan.disjoint",
                      work="work.grounded", critic="critic.grounded",
                      synthesise="synthesise.filtered",
                      verify="verify.covers_brief").output("verify")
    assert not refused["ok"] and refused["missing"] == ["rent"]
    assert declared["ok"]


def test_geo_a_format_check_accepts_places_that_do_not_exist():
    base = {"load": "load.trimmed", "parse": "parse.patterns",
            "lookup": "lookup.zip", "reconcile": "reconcile.flag",
            "attach": "attach.all"}
    shape = _route("geo", **base, validate="validate.format").output("validate")
    exists = _route("geo", **base, validate="validate.triple").output("validate")

    assert shape["accepted"] > exists["accepted"]
    rejected_by_existence = {f["id"] for f in exists["findings"]}
    rejected_by_format = {f["id"] for f in shape["findings"]}
    # The nonexistent postal code, the state/ZIP conflict and the nonexistent
    # state are all well-formed, and all three sail through.
    assert {"r6", "r7", "r8"} <= rejected_by_existence - rejected_by_format


def test_geo_the_reconciler_that_is_usually_right_hides_the_correction():
    """`prefer_zip` produces a correct state and no trace that it changed one
    — except the `disputed` flag every reconciler here is made to carry."""
    quiet = _route("geo", load="load.trimmed", parse="parse.patterns",
                   lookup="lookup.zip", reconcile="reconcile.prefer_zip",
                   validate="validate.triple", attach="attach.all")
    rows = {r["id"]: r for r in quiet.output("attach", "out")}
    assert rows["r6"]["state"] == "GA", "silently corrected from NY"
    assert rows["r6"]["disputed"], "and it has to be possible to find out"
    assert rows["r6"]["typed_state"] == "NY"


def test_geo_a_filtering_enrichment_reports_what_it_dropped():
    kept = _route("geo", load="load.trimmed", parse="parse.patterns",
                  lookup="lookup.zip", reconcile="reconcile.flag",
                  validate="validate.triple", attach="attach.valid_only")
    assert kept.output("attach", "dropped"), (
        "an enrichment that filters without saying so is data loss")
    assert len(kept.output("attach", "out")) + \
        len(kept.output("attach", "dropped")) == 10


def test_spacetime_the_better_data_is_the_leak():
    base = {"load": "load.sales", "place": "place.nearest",
            "period": "period.local_day", "vintage": "vintage.checked",
            "attach": "attach.with_provenance", "audit": "audit.leak"}
    honest = _route("spacetime", **base, context="context.asof").output("audit")
    leaky = _route("spacetime", **base, context="context.final").output("audit")

    assert leaky["enriched"] == honest["enriched"], "same coverage"
    assert honest["leaks"] == 0
    assert leaky["leaks"] >= 2, "and two rows used a figure published later"


def test_spacetime_the_timezone_choice_moves_a_row_into_the_next_year():
    local = _route("spacetime", load="load.sales", place="place.nearest",
                   period="period.local_day", context="context.asof",
                   vintage="vintage.checked", attach="attach.with_provenance",
                   audit="audit.count")
    utc = _route("spacetime", load="load.sales", place="place.nearest",
                 period="period.utc_day", context="context.asof",
                 vintage="vintage.checked", attach="attach.with_provenance",
                 audit="audit.count")
    days = {r["id"]: r["day"] for r in local.output("attach")}
    utc_days = {r["id"]: r["day"] for r in utc.output("attach")}
    assert days["s6"] == "2024-12-31"
    assert utc_days["s6"] == "2025-01-01"


def test_spacetime_todays_boundaries_rewrite_last_years_rows():
    base = {"load": "load.sales", "place": "place.nearest",
            "period": "period.local_day", "context": "context.asof",
            "attach": "attach.with_provenance", "audit": "audit.leak"}
    checked = _route("spacetime", **base, vintage="vintage.checked").output("audit")
    current = _route("spacetime", **base, vintage="vintage.current").output("audit")
    assert not [f for f in checked["findings"] if f["kind"] == "spatial"]
    wrong = [f["id"] for f in current["findings"] if f["kind"] == "spatial"]
    assert wrong == ["s1"], "the only row from before the store moved region"


def test_spacetime_stripping_provenance_does_not_make_a_pipeline_clean():
    """It makes it unauditable, and the two look the same on a dashboard."""
    base = {"load": "load.sales", "place": "place.same_city",
            "period": "period.local_day", "context": "context.asof",
            "vintage": "vintage.checked", "audit": "audit.leak"}
    full = _route("spacetime", **base,
                  attach="attach.with_provenance").output("audit")
    stripped = _route("spacetime", **base,
                      attach="attach.value_only").output("audit")
    far = [f for f in full["findings"] if f["kind"] == "distance"]
    assert far, "five rows came from a station over 25km away"
    assert not [f for f in stripped["findings"] if f["kind"] == "distance"], (
        "and with the distance dropped the audit cannot see any of them")
    assert [f for f in stripped["findings"] if f["kind"] == "provenance"]


def test_synth_the_copier_wins_on_everything_except_privacy():
    base = {"real": "real.rows", "split": "split.random",
            "fidelity": "fidelity.joint", "utility": "utility.tstr",
            "decide": "decide.all_three"}
    copier = _route("synth", **base, generate="generate.copy",
                    privacy="privacy.nearest")
    honest = _route("synth", **base, generate="generate.conditional",
                    privacy="privacy.nearest")

    assert copier.output("fidelity")["score"] > honest.output("fidelity")["score"]
    assert copier.output("utility")["score"] >= honest.output("utility")["score"] - 0.02
    assert copier.output("privacy")["copied"] > 0.9
    assert not copier.output("decide")["allow"]
    assert honest.output("decide")["allow"]


def test_synth_noise_in_the_last_decimal_defeats_an_exact_duplicate_check():
    base = {"real": "real.rows", "split": "split.random",
            "generate": "generate.noisy_copy", "fidelity": "fidelity.joint",
            "utility": "utility.tstr", "decide": "decide.all_three"}
    exact = _route("synth", **base, privacy="privacy.exact").output("privacy")
    near = _route("synth", **base, privacy="privacy.nearest").output("privacy")
    assert exact["copied"] == 0.0, "no two rows are identical"
    assert near["copied"] > 0.9, "and every one sits on top of a real row"


def test_synth_matching_every_histogram_is_not_fidelity():
    base = {"real": "real.rows", "split": "split.random",
            "generate": "generate.marginal", "utility": "utility.tstr",
            "privacy": "privacy.nearest", "decide": "decide.all_three"}
    per_column = _route("synth", **base,
                        fidelity="fidelity.marginals").output("fidelity")
    joint = _route("synth", **base, fidelity="fidelity.joint").output("fidelity")
    assert per_column["score"] > 0.9, "every marginal matches"
    assert joint["joint"] < 0.7, "and every relationship is gone"
    assert _route("synth", **base,
                  fidelity="fidelity.joint").output("utility")["score"] < 0.1


def test_synth_testing_on_synthetic_cannot_see_a_confidently_wrong_generator():
    base = {"real": "real.rows", "split": "split.random",
            "generate": "generate.overconfident", "fidelity": "fidelity.joint",
            "privacy": "privacy.nearest", "decide": "decide.all_three"}
    on_synthetic = _route("synth", **base,
                          utility="utility.tsts").output("utility")
    on_real = _route("synth", **base, utility="utility.tstr").output("utility")
    assert on_synthetic["score"] > 0.95, "the best utility score in the pack"
    assert on_real["score"] < 0.1, "and it has learned nothing transferable"


def test_synth_deciding_on_utility_alone_ships_the_copier():
    base = {"real": "real.rows", "split": "split.random",
            "generate": "generate.copy", "fidelity": "fidelity.joint",
            "utility": "utility.tstr", "privacy": "privacy.nearest"}
    lax = _route("synth", **base, decide="decide.utility_only").output("decide")
    strict = _route("synth", **base, decide="decide.all_three").output("decide")
    assert lax["allow"] and not strict["allow"]
    assert not lax["would_all_three_allow"], (
        "the arm that would have refused is recorded either way")


def test_models_there_is_no_winner_across_data_generating_processes():
    """Linear wins by a distance on additive data and loses on a threshold."""
    def rmse(data, family):
        return _route("models", load=data, split="split.random",
                      prepare="prepare.standardised", fit=family,
                      evaluate="evaluate.rmse",
                      compare="compare.interval").output("evaluate")["score"]

    linear_data = {f: rmse("load.linear_truth", f)
                   for f in ("fit.linear", "fit.boosted", "fit.constant")}
    threshold_data = {f: rmse("load.threshold_truth", f)
                      for f in ("fit.linear", "fit.boosted", "fit.constant")}

    assert linear_data["fit.linear"] < linear_data["fit.boosted"] / 2
    assert threshold_data["fit.boosted"] < threshold_data["fit.linear"] / 1.5
    # And the bar itself moves between the two datasets, which is why an
    # absolute error threshold carried between projects means nothing.
    assert linear_data["fit.constant"] > threshold_data["fit.constant"] * 1.2


def test_models_every_family_is_measured_against_the_null_control():
    for family in ("fit.linear", "fit.tree", "fit.boosted", "fit.mlp",
                   "fit.attention"):
        report = _route("models", load="load.threshold_truth",
                        split="split.random", prepare="prepare.standardised",
                        fit=family, evaluate="evaluate.rmse",
                        compare="compare.interval").output("compare")
        assert report["beats_null_by"] > 0, f"{family} did not beat the mean"
        assert report["decision_matters"] is not None


def test_models_the_constant_does_not_beat_itself():
    report = _route("models", load="load.linear_truth", split="split.random",
                    prepare="prepare.standardised", fit="fit.constant",
                    evaluate="evaluate.rmse",
                    compare="compare.interval").output("compare")
    assert abs(report["beats_null_by"]) < 1e-9
    assert report["decision_matters"] is False, (
        "the null control against itself is the clearest case of a decision "
        "that does not matter, and the reporter has to say so")


def test_models_attention_does_not_win_at_this_size():
    """Kept as a test because an attention model beating ridge on a few hundred
    tabular rows would be evidence of a bug rather than of progress."""
    def rmse(data, family):
        return _route("models", load=data, split="split.random",
                      prepare="prepare.standardised", fit=family,
                      evaluate="evaluate.rmse",
                      compare="compare.best").output("evaluate")["score"]

    for data in ("load.linear_truth", "load.threshold_truth"):
        assert rmse(data, "fit.attention") > rmse(data, "fit.boosted")
        assert rmse(data, "fit.attention") < rmse(data, "fit.constant")
