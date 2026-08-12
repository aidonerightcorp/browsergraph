"""The ledger has to be harder to satisfy than a checklist.

The three properties worth testing, and they are the ones a checklist does not
have: a waiver needs a reason, "we did not check" is not a pass, and "the check
came back no" is not the same as "we did not check".
"""
from __future__ import annotations

import pytest

from browsergraph import duecare


def _full() -> duecare.Ledger:
    """Every blocking obligation discharged, nothing failed."""
    ledger = duecare.Ledger.standard()
    ledger.discharge("holdout", "cases held out since March")
    ledger.record(duecare.check_negative_control(real=0.9, broken=0.2))
    ledger.record(duecare.check_sample_size([0.9] * 30 + [0.2] * 10))
    ledger.record(duecare.check_provenance([{"case": "a", "run": "r1"}]))
    return ledger


# --- the three states -------------------------------------------------------

def test_all_blocking_obligations_met_is_a_pass():
    assert _full().verdict(0.9).state == duecare.PASS


def test_an_unchecked_blocking_obligation_is_provisional_not_a_pass():
    ledger = duecare.Ledger.standard()
    ledger.discharge("holdout", "held out")
    verdict = ledger.verdict(0.99)
    assert verdict.state == duecare.PROVISIONAL
    assert not verdict.ok, "a provisional verdict must not gate like a pass"
    assert "negative_control" in verdict.outstanding


def test_a_failed_check_beats_an_outstanding_one():
    """Knowing a control did not hold is worse news than not having run it, and
    a verdict that reported PROVISIONAL would bury the finding."""
    ledger = duecare.Ledger.standard()
    ledger.record(duecare.check_negative_control(real=0.90, broken=0.89))
    verdict = ledger.verdict(0.9)
    assert verdict.state == duecare.FAIL
    assert verdict.outstanding, "there are still unchecked obligations too"
    assert verdict.failed[0][0] == "negative_control"


def test_a_material_obligation_does_not_block():
    ledger = _full()
    assert "slices" in [o.id for o in ledger.outstanding()]
    assert ledger.verdict(0.9).state == duecare.PASS


def test_provisional_is_not_actionable_and_fail_is():
    ledger = duecare.Ledger.standard()
    ledger.discharge("holdout", "held out")
    assert not ledger.verdict(0.5).actionable
    assert _full().verdict(0.9).actionable


# --- waivers ----------------------------------------------------------------

def test_a_waiver_needs_a_reason():
    ledger = duecare.Ledger.standard()
    with pytest.raises(ValueError, match="reason"):
        ledger.waive("slices", "")
    with pytest.raises(ValueError, match="reason"):
        ledger.waive("slices", "   ")


def test_a_waived_blocking_obligation_stops_blocking_and_stays_visible():
    ledger = duecare.Ledger.standard()
    ledger.discharge("holdout", "held out")
    ledger.waive("negative_control", "no broken variant exists to build")
    ledger.record(duecare.check_sample_size([0.5] * 20))
    ledger.record(duecare.check_provenance([{"case": "a", "run": "r"}]))
    verdict = ledger.verdict(0.5)
    assert verdict.state == duecare.PASS
    assert ("negative_control", "no broken variant exists to build") in verdict.waived
    assert "negative_control" in verdict.text()


def test_a_discharge_without_evidence_is_refused():
    with pytest.raises(ValueError, match="evidence"):
        duecare.Discharge("holdout", "discharged")
    with pytest.raises(ValueError, match="evidence"):
        duecare.Discharge("holdout", "failed")
    # Outstanding needs none — it is the absence of a check.
    assert duecare.Discharge("holdout", "outstanding").state == "outstanding"


def test_discharging_something_nobody_required_is_refused():
    ledger = duecare.Ledger.of(duecare.STANDARD[0])
    with pytest.raises(KeyError, match="negative_control"):
        ledger.discharge("negative_control", "we did it")


# --- could-not-check is not checked-and-failed ------------------------------

def test_a_single_class_human_sample_cannot_validate_a_grader():
    """Kappa is 0.0 there, and it is a fact about the sample."""
    found = duecare.check_grader_agreement(["good", "bad"], ["good", "good"])
    assert found.state == "outstanding"
    assert "single-class" in found.evidence


def test_a_grader_that_disagrees_with_people_fails():
    found = duecare.check_grader_agreement(
        ["good"] * 5 + ["bad"] * 5, ["bad"] * 5 + ["good"] * 5)
    assert found.state == "failed"


def test_a_grader_that_agrees_with_people_discharges():
    found = duecare.check_grader_agreement(
        ["good"] * 5 + ["bad"] * 5, ["good"] * 5 + ["bad"] * 5)
    assert found.state == "discharged"
    assert found.detail["kappa"] == pytest.approx(1.0)


def test_one_run_is_unreplicated_rather_than_failed():
    assert duecare.check_replication([0.9]).state == "outstanding"
    assert duecare.check_replication([0.9, 0.95]).state == "failed"
    assert duecare.check_replication([0.9, 0.905]).state == "discharged"


def test_two_items_cannot_carry_a_claimed_effect():
    assert duecare.check_sample_size([0.5]).state == "outstanding"
    small = duecare.check_sample_size([0.4, 0.9, 0.5, 0.8], effect=0.03)
    assert small.state == "failed", "0.03 is inside the interval on four items"


def test_one_slice_is_not_a_breakdown():
    assert duecare.check_slices({"all": [0.5, 0.6]}).state == "outstanding"


# --- the checks themselves --------------------------------------------------

def test_kappa_is_zero_when_chance_explains_everything():
    """A grader that says 'good' to everything, on a set that is 80% good."""
    human = ["good"] * 8 + ["bad"] * 2
    always_good = ["good"] * 10
    raw = sum(1 for a, b in zip(always_good, human, strict=True) if a == b) / 10
    assert raw == 0.8, "the flattering number"
    assert duecare.cohen_kappa(always_good, human) == 0.0


def test_a_regressed_slice_fails_even_when_the_mean_is_fine():
    found = duecare.check_slices({"common": [0.9] * 20, "rare": [0.4] * 3},
                                 regression=0.05)
    assert found.state == "failed"
    assert "rare" in found.evidence


def test_provenance_names_what_is_missing():
    found = duecare.check_provenance([{"case": "a", "run": "r"}, {"case": "b"}])
    assert found.state == "failed"
    assert found.detail["missing"] == {"run": 1}


def test_coverage_discharges_and_still_names_what_was_missed():
    found = duecare.check_coverage(["en", "fr"], ["en", "fr", "de", "ja"])
    assert found.state == "discharged"
    assert "de" in found.evidence and "ja" in found.evidence


def test_an_interval_on_one_value_is_infinite_rather_than_zero():
    _, half = duecare.interval([0.5])
    assert half == float("inf"), "one observation has no spread, not no error"


# --- the digest -------------------------------------------------------------

def test_two_evaluations_under_different_standards_are_not_comparable():
    strict = _full().verdict(0.90)
    loose = duecare.Ledger.of(duecare.STANDARD[0])
    loose.discharge("holdout", "held out")
    message = duecare.compare(strict, loose.verdict(0.95))
    assert "not comparable" in message


def test_the_same_standard_compares():
    before, after = _full().verdict(0.90), _full().verdict(0.93)
    message = duecare.compare(before, after)
    assert "same standard" in message and "up 0.030" in message


def test_the_digest_ignores_the_score_and_not_the_states():
    ledger = _full()
    assert ledger.verdict(0.1).digest == ledger.verdict(0.9).digest
    before = ledger.digest()
    ledger.record(duecare.check_slices({"a": [0.9], "b": [0.9]}))
    assert ledger.digest() != before


# --- the loop ---------------------------------------------------------------

def test_failures_become_permanent_regression_cases():
    loop = duecare.Loop()
    loop.round(cases=10, verdict=_full().verdict(0.8), failures=["c1", "c2"])
    loop.round(cases=12, verdict=_full().verdict(0.9), failures=["c2"])
    assert loop.regressions == {"c1", "c2"}
    assert loop.next_cases(["c9"]) == ("c9", "c1", "c2"), (
        "the base set comes first so a truncated run keeps the regressions "
        "it can and loses the cases nobody has ever failed")


def test_novelty_is_what_is_reported():
    """A total that goes down is also what deleting the hard cases looks like."""
    loop = duecare.Loop()
    first = loop.round(cases=10, verdict=_full().verdict(0.8),
                       failures=["c1", "c2"])
    second = loop.round(cases=10, verdict=_full().verdict(0.8),
                        failures=["c1", "c2"])
    assert first.novel == ("c1", "c2")
    assert second.novel == (), "nothing new, though two cases still fail"
    assert not loop.converging, "one quiet round could be luck"
    loop.round(cases=10, verdict=_full().verdict(0.8), failures=["c1", "c2"])
    assert loop.converging, "two consecutive quiet rounds is the bar"


def test_a_loop_that_keeps_finding_things_is_not_converging():
    loop = duecare.Loop()
    loop.round(cases=10, verdict=_full().verdict(0.8), failures=["c1"])
    loop.round(cases=10, verdict=_full().verdict(0.8), failures=["c2"])
    assert not loop.converging
    assert "Still finding things" in loop.text()


def test_only_a_pass_teaches_the_search_that_the_route_worked():
    from browsergraph.evidence import Evidence

    provisional = duecare.Ledger.standard()
    provisional.discharge("holdout", "held out")

    loop = duecare.Loop()
    loop.round(cases=10, verdict=provisional.verdict(0.95), failures=[])
    evidence = loop.fold_into(Evidence(), {"stage": "candidate.a"})
    assert evidence.posterior("candidate.a").rate < 1.0, (
        "a provisional verdict must not be recorded as a working route, or "
        "the search converges on whatever was least thoroughly checked")

    loop.round(cases=10, verdict=_full().verdict(0.95), failures=[])
    loop.fold_into(evidence, {"stage": "candidate.b"})
    assert evidence.posterior("candidate.b").rate > 0.5


def test_from_scores_leaves_the_controls_outstanding():
    """No arrangement of the scores you have can tell you what a broken system
    would have scored."""
    ledger = duecare.from_scores(
        [{"case": f"c{i}", "run": "r1", "score": 0.9, "slice": "a"}
         for i in range(5)]
        + [{"case": f"d{i}", "run": "r1", "score": 0.8, "slice": "b"}
           for i in range(5)])
    assert ledger.state_of("provenance") == "discharged"
    assert ledger.state_of("sample_size") == "discharged"
    assert ledger.state_of("negative_control") == "outstanding"
    assert ledger.verdict(0.85).state == duecare.PROVISIONAL
