"""Tests for `assay` — the integrity layer, and the boundary around it.

Two things get tested here that are easy to skip and expensive to lose:

* **the package boundary** — `assay` must import nothing from `browsergraph`
  or `solutiongraph`. The whole reason it was extracted is that its subject is
  not browsers, and a single convenience import would quietly undo that. The
  test reads the AST rather than trusting anyone to remember.
* **the numbers in the prose** — the demo prints figures, the README repeats
  them, and both are checked against what the code actually produces.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from assay import controls, judge, obligations, taxonomy
from assay.cli import _demo_judge, main

ROOT = Path(__file__).resolve().parent.parent
ASSAY = ROOT / "assay"


# --- the boundary -----------------------------------------------------------

def test_assay_imports_nothing_from_browsergraph():
    """The extraction is only real if it holds. One import would undo it."""
    offenders = []
    for path in sorted(ASSAY.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root in ("browsergraph", "solutiongraph"):
                    offenders.append(f"{path.name}:{node.lineno} imports {name}")
    assert not offenders, (
        "assay must not depend on browsergraph — it is the half that is not "
        "about browsers, and these imports put it back:\n  "
        + "\n  ".join(offenders))


def test_assay_is_standard_library_only():
    """No third-party imports either: the core claim is zero dependencies."""
    allowed = {"assay", "__future__"}
    stdlib = set(sys.stdlib_module_names)
    offenders = []
    for path in sorted(ASSAY.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root not in allowed and root not in stdlib:
                    offenders.append(f"{path.name}:{node.lineno} {name}")
    assert not offenders, f"non-stdlib imports in assay: {offenders}"


def test_the_shims_re_export_the_same_objects():
    """Existing imports must keep working, and mean the same thing.

    Twenty-three published notebooks import from the old locations. A shim
    that returned a copy would be worse than no shim: two Ledger classes that
    are not each other fail an isinstance check nobody thought to look at.
    """
    from browsergraph import duecare as shim_duecare
    from browsergraph import taxonomy as shim_taxonomy

    assert shim_duecare.Ledger is obligations.Ledger
    assert shim_duecare.cohen_kappa is obligations.cohen_kappa
    assert shim_taxonomy.CATEGORIES is taxonomy.CATEGORIES
    assert shim_taxonomy.coverage is taxonomy.coverage


def test_the_packaging_manifest_ships_assay():
    """A package absent from the manifest is a package absent from the wheel."""
    text = (ROOT / "pyproject.toml").read_text()
    assert '"assay"' in text, "assay is not in [tool.setuptools] packages"
    assert 'assay = "assay.cli:main"' in text, "no assay console script"
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "COPY assay" in dockerfile, (
        "the Dockerfile copies the packages it installs; missing one breaks "
        "the image build, and the image is only built on a tag")


# --- controls ---------------------------------------------------------------

def test_chance_is_the_majority_baseline_not_one_over_k():
    """The single most common way to make a null control lie."""
    labels = ["good"] * 83 + ["bad"] * 17
    assert controls.chance_level(labels) == pytest.approx(0.83)
    assert controls.chance_level(labels, kind="uniform") == pytest.approx(0.5)


def test_chance_level_refuses_an_empty_sample():
    """A silently-zero chance level makes every null control pass."""
    with pytest.raises(ValueError, match="no labels"):
        controls.chance_level([])


def test_a_null_control_above_chance_fails():
    assert controls.null_control(observed=0.86, chance=0.83).ok is False
    assert controls.null_control(observed=0.84, chance=0.83).ok is True


def test_a_null_below_chance_is_not_a_failure():
    """Odd, but not evidence the harness is broken. The tolerance is one-sided."""
    assert controls.null_control(observed=0.70, chance=0.83).ok is True


def test_a_negative_control_the_harness_cannot_separate_fails():
    assert controls.negative_control(broken=0.85, real=0.86).ok is False
    assert controls.negative_control(broken=0.40, real=0.86).ok is True


def test_a_gap_of_exactly_the_margin_passes():
    """The boundary, pinned. `margin` is the minimum acceptable separation,
    so meeting it exactly is meeting it — and leaving this to be rediscovered
    from the source is how a threshold quietly moves."""
    assert controls.negative_control(broken=0.84, real=0.86,
                                     margin=0.02).ok is True
    assert controls.negative_control(broken=0.8401, real=0.86,
                                     margin=0.02).ok is False


def test_could_not_check_is_outstanding_never_failed():
    """The distinction the whole module is built on."""
    control = controls.could_not_check("negative", "no broken variant exists")
    assert control.ok is None
    assert control.checked is False
    verdict = controls.Controls().add(control).verdict(0.9)
    assert verdict.state == "PROVISIONAL"
    assert verdict.failed == ()
    assert len(verdict.outstanding) == 1


def test_no_controls_at_all_is_provisional_not_pass():
    """The rule this module exists to enforce."""
    verdict = controls.Controls().verdict(score=0.97)
    assert verdict.state == "PROVISIONAL"
    assert verdict.ok is False
    assert set(verdict.missing) == set(controls.KINDS)


def test_provisional_is_not_ok():
    """`.ok` is read by code that is about to act on the number."""
    bundle = controls.Controls().add(
        controls.could_not_check("null", "no null variant"))
    assert bundle.verdict(0.9).ok is False


def test_one_failure_outweighs_any_number_of_passes():
    bundle = controls.Controls()
    bundle.add(controls.null_control(observed=0.10, chance=0.50))
    bundle.add(controls.positive_control(observed=0.95, floor=0.9))
    bundle.add(controls.negative_control(broken=0.85, real=0.86))
    assert bundle.verdict(0.86).state == "FAIL"


def test_a_control_needs_a_detail_line():
    with pytest.raises(ValueError, match="detail line"):
        controls.Control("null", "at chance", 0.5, True, "")


def test_an_unknown_kind_of_control_is_refused():
    with pytest.raises(ValueError, match="not a kind of control"):
        controls.Control("vibes", "good", 1.0, True, "felt right")


def test_a_leaderboard_under_a_failed_control_reports_no_ranking():
    """The specific artefact this module exists to prevent."""
    bundle = controls.Controls().add(
        controls.negative_control(broken=0.85, real=0.86))
    text = controls.summarise({"a": 0.86, "b": 0.71}, controls=bundle)
    assert "No ranking is reported" in text


def test_controls_discharge_into_a_ledger_without_a_hard_import():
    ledger = obligations.Ledger.standard()
    bundle = controls.Controls().add(
        controls.negative_control(broken=0.85, real=0.86))
    controls.discharge_into(ledger, bundle.verdict(0.86))
    entry = ledger.discharges["negative_control"]
    assert entry.state == "failed"


def test_an_outstanding_control_is_not_written_into_the_ledger():
    """Outstanding stays outstanding; a ledger entry would claim a check ran."""
    ledger = obligations.Ledger.standard()
    bundle = controls.Controls().add(
        controls.could_not_check("negative", "nothing broken to compare with"))
    controls.discharge_into(ledger, bundle.verdict(0.9))
    assert "negative_control" not in ledger.discharges


# --- the judge --------------------------------------------------------------

def test_high_raw_agreement_at_chance_is_not_usable():
    """The headline claim: 85% agreement, and the judge knows nothing."""
    human = ["good"] * 85 + ["bad"] * 15
    model = ["good"] * 100
    report = judge.check(model, human)
    assert report.raw == pytest.approx(0.85)
    assert report.verdict == "AT_CHANCE"
    assert report.ok is False


def test_a_judge_can_be_below_chance():
    human = (["good"] * 8 + ["bad"] * 2) * 6
    model = [("bad" if h == "good" else "good") for h in human]
    report = judge.check(model, human)
    assert report.kappa < 0
    assert report.verdict == "BELOW_CHANCE"


def test_a_good_judge_is_usable():
    human = (["good"] * 7 + ["bad"] * 3) * 6
    model = [h if i % 9 else ("bad" if h == "good" else "good")
             for i, h in enumerate(human)]
    report = judge.check(model, human)
    assert report.verdict == "USABLE"
    assert report.ok is True


def test_a_single_class_human_sample_cannot_check_anything():
    """Not the judge's fault, and the report must not say it is."""
    report = judge.check(["good"] * 40, ["good"] * 40)
    assert report.verdict == "CANNOT_CHECK"
    assert report.ok is False
    assert report.checked is False
    assert "single-class" in report.reason


def test_too_few_labels_cannot_check():
    human = ["good"] * 10 + ["bad"] * 5
    report = judge.check(list(human), human)
    assert report.verdict == "CANNOT_CHECK"
    assert "floor" in report.reason


def test_no_human_labels_cannot_check():
    assert judge.check([], []).verdict == "CANNOT_CHECK"


def test_misaligned_inputs_raise_rather_than_score():
    with pytest.raises(ValueError, match="same items in the same order"):
        judge.check(["good"] * 5, ["good"] * 4)


def test_rubric_sensitivity_needs_two_scoreable_rubrics():
    human = (["good"] * 7 + ["bad"] * 3) * 6
    one = judge.rubric_sensitivity({"only": list(human)}, human)
    assert one.checked is False
    assert one.ok is False


def test_the_rubric_can_decide_more_than_the_input():
    human = (["good"] * 7 + ["bad"] * 3) * 6
    flipped = [("bad" if h == "good" else "good") for h in human]
    report = judge.rubric_sensitivity(
        {"strict": list(human), "loose": flipped}, human)
    assert report.checked is True
    assert report.spread > judge.USABLE_KAPPA
    assert report.ok is False
    assert "rubric is deciding" in report.text()


def test_a_rubric_that_cannot_be_scored_is_dropped_not_zeroed():
    """Entering it as 0.0 would report a rubric that never ran as a bad one."""
    human = (["good"] * 7 + ["bad"] * 3) * 6
    report = judge.rubric_sensitivity(
        {"ok": list(human), "wrong length": ["good"] * 3}, human)
    assert [name for name, _ in report.rows] == ["ok"]


def test_length_bias_is_found_and_named():
    scores = [float(i % 5) for i in range(40)]
    texts = ["x" * (10 + int(s) * 30) for s in scores]
    report = judge.length_bias(scores, texts)
    assert report.ok is False
    assert report.statistic > 0.5


def test_length_bias_absent_when_length_is_unrelated():
    scores = [float(i % 5) for i in range(40)]
    texts = ["x" * (10 + (i * 37) % 50) for i in range(40)]
    assert judge.length_bias(scores, texts).ok is True


def test_a_judge_that_gives_every_item_the_same_score_cannot_be_checked():
    report = judge.length_bias([3.0] * 20, ["x" * i for i in range(20)])
    assert report.checked is False
    assert "same score" in report.reason


def test_spearman_handles_ties():
    """Judges emit small integers; a tie-naive rank is mostly an artefact."""
    assert judge._spearman([1, 1, 2, 2], [1, 1, 2, 2]) == pytest.approx(1.0)
    assert judge._spearman([1, 1, 1, 1], [1, 2, 3, 4]) == pytest.approx(0.0)


def test_position_bias_counts_flips():
    first = ["a"] * 20
    second = ["a"] * 10 + ["b"] * 10
    report = judge.position_bias(first, second)
    assert report.ok is False
    assert report.statistic == pytest.approx(0.5)
    assert judge.position_bias(["a"] * 20, ["a"] * 20).ok is True


def test_self_preference_needs_somebody_else_to_compare_with():
    only_me = judge.self_preference({"mine": [4.0, 4.5]}, judge_family="mine")
    assert only_me.checked is False
    biased = judge.self_preference(
        {"mine": [4.5, 4.6], "theirs": [3.0, 3.1]}, judge_family="mine")
    assert biased.ok is False


def test_audit_fails_when_any_check_fails():
    human = ["good"] * 85 + ["bad"] * 15
    report = judge.audit(model=["good"] * 100, human=human)
    assert report.verdict == "FAIL"
    assert report.ok is False


def test_audit_is_provisional_when_nothing_could_be_checked():
    """No confirmed failure is not the same as a working judge."""
    report = judge.audit(model=["good"] * 40, human=["good"] * 40)
    assert report.verdict == "PROVISIONAL"
    assert "agreement" in report.unchecked


def test_audit_names_the_checks_that_did_not_run():
    human = (["good"] * 7 + ["bad"] * 3) * 6
    model = [h if i % 9 else ("bad" if h == "good" else "good")
             for i, h in enumerate(human)]
    report = judge.audit(model=model, human=human)
    assert report.verdict == "PASS"
    assert "Not checked" not in report.text() or report.unchecked == ()


# --- the demo, and the numbers it prints -------------------------------------

def test_the_demo_reproduces_its_documented_numbers():
    """Every figure quoted in the README and the module docstring.

    The demo is the first thing anyone runs. If its prose and its output ever
    disagree, the honest reading is that neither can be trusted, so the
    numbers are pinned here rather than described.
    """
    human, model, rubrics, scores, texts = _demo_judge()

    assert len(human) == 120
    assert human.count("good") == 102          # 85%, the flattering imbalance
    assert human.count("bad") == 18

    agreement = judge.check(model, human)
    assert agreement.verdict == "BELOW_CHANCE"
    assert agreement.raw == pytest.approx(0.65, abs=0.005)
    assert agreement.expected == pytest.approx(0.71, abs=0.005)
    assert agreement.kappa == pytest.approx(-0.207, abs=0.002)

    # The judge and a person never once agree that an answer is bad.
    both_bad = sum(1 for m, h in zip(model, human, strict=True)
                   if m == "bad" and h == "bad")
    assert both_bad == 0

    rubric = judge.rubric_sensitivity(rubrics, human)
    assert rubric.spread == pytest.approx(0.694, abs=0.002)
    assert rubric.ok is False

    bias = judge.length_bias(scores, texts)
    assert bias.ok is False
    assert bias.statistic == pytest.approx(0.95, abs=0.01)


def test_the_demo_length_correlation_is_not_a_perfect_one():
    """A rho of exactly 1.0 would mean the fixture, not the judge, did it."""
    _, _, _, scores, texts = _demo_judge()
    assert judge.length_bias(scores, texts).statistic < 0.99


# --- the command line -------------------------------------------------------

def test_judge_demo_exits_nonzero(capsys):
    """A failing audit must not exit 0; somebody will put this in CI."""
    assert main(["judge", "--demo"]) == 1
    out = capsys.readouterr().out
    assert "BELOW_CHANCE" in out
    assert "RUBRIC-DEPENDENT" in out


def test_judge_demo_json_is_machine_readable(capsys):
    main(["judge", "--demo", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "FAIL"
    assert payload["agreement"]["verdict"] == "BELOW_CHANCE"


def test_controls_demo_reports_the_real_chance_level(capsys):
    assert main(["controls", "--demo"]) == 1
    out = capsys.readouterr().out
    assert "0.830" in out and "not \n" not in out
    assert "absent" in out          # the shuffle control nobody ran


def test_judge_reads_a_csv(tmp_path, capsys):
    rows = ["model,human"]
    for i in range(60):
        human = "good" if i % 10 else "bad"
        rows.append(f"good,{human}")
    path = tmp_path / "labels.csv"
    path.write_text("\n".join(rows) + "\n")
    code = main(["judge", "--data", str(path)])
    assert code == 1
    assert "AT_CHANCE" in capsys.readouterr().out


def test_judge_reads_json_columns(tmp_path, capsys):
    human = ["good" if i % 10 else "bad" for i in range(60)]
    path = tmp_path / "labels.json"
    path.write_text(json.dumps({"model": ["good"] * 60, "human": human}))
    main(["judge", "--data", str(path)])
    assert "AT_CHANCE" in capsys.readouterr().out


def test_a_missing_column_says_which_columns_exist(tmp_path, capsys):
    path = tmp_path / "labels.csv"
    path.write_text("verdict,truth\ngood,good\n")
    with pytest.raises(SystemExit, match="Columns present"):
        main(["judge", "--data", str(path)])


def test_mismatched_json_columns_are_refused(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"model": ["good"] * 5, "human": ["good"] * 4}))
    with pytest.raises(SystemExit, match="different lengths"):
        main(["judge", "--data", str(path)])


def test_taxonomy_and_obligations_are_reachable_from_the_cli(capsys):
    assert main(["taxonomy", "--coverage"]) == 0
    assert "categories" in capsys.readouterr().out
    assert main(["obligations"]) == 0
    assert "PROVISIONAL" in capsys.readouterr().out


def test_the_console_script_runs_as_a_module():
    """`python -m assay.cli` is what the docs tell people to try first."""
    result = subprocess.run(
        [sys.executable, "-m", "assay.cli", "--version"],
        capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0
    assert "assay" in result.stdout


def test_the_readmes_quote_the_demo_numbers_the_demo_actually_prints():
    """Both READMEs paste the demo's output. Pasted output goes stale.

    This is the same rule the packs keep: a number in prose that nothing
    recomputes is a number that was true once. Checking the strings is crude
    and it is enough — the failure mode being guarded against is somebody
    changing the fixture and not the page.
    """
    human, model, rubrics, scores, texts = _demo_judge()
    report = judge.audit(model=model, human=human, rubrics=rubrics,
                         scores=scores, texts=texts)

    kappa = f"{report.agreement.kappa:+.3f}"          # -0.207
    raw = f"{report.agreement.raw:.1%}"               # 65.0%
    chance = f"{report.agreement.expected:.1%}"       # 71.0%
    spread = f"{report.rubric.spread:.3f}"            # 0.694
    rho = f"{report.biases[0].statistic:+.2f}"        # +0.95

    for path in (ASSAY / "README.md", ROOT / "README.md"):
        text = path.read_text()
        for value in (kappa, raw, chance, spread, rho):
            assert value in text, (
                f"{path.name} does not contain {value!r}, which is what "
                f"`assay judge --demo` prints now. Re-paste the block.")
