"""The case studies must reproduce, and the document must not drift.

A case study is only worth having if its numbers come out of running
something. These tests execute every study and check three things: that it
runs, that every figure its prose quotes is actually produced, and that the
committed markdown is what the code generates right now.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from assay import taxonomy
from browsergraph import casestudies, packs

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "CASE_STUDIES.md"

STUDIES = casestudies.STUDIES
IDS = [s.id for s in STUDIES]


def test_there_are_studies_and_the_ids_are_unique():
    assert len(STUDIES) >= 12
    assert len(set(IDS)) == len(IDS)


@pytest.mark.parametrize("study", STUDIES, ids=IDS)
def test_every_study_runs(study):
    """A study that cannot be run is an anecdote."""
    assert isinstance(study.measured(), dict)


@pytest.mark.parametrize("study", STUDIES, ids=IDS)
def test_every_quoted_figure_is_actually_produced(study):
    """The prose quotes keys; the keys must exist.

    This is what stops the write-up and the code drifting apart. Renaming a
    measurement now fails here instead of leaving a number in the text that
    nothing computes.
    """
    measured = study.measured()
    missing = [key for key in study.quotes if key not in measured]
    assert not missing, (
        f"{study.id} quotes {missing}, which run() does not return. Either "
        f"the measurement was renamed or the prose is describing something "
        f"that is no longer computed.")
    assert study.quotes, f"{study.id} quotes nothing — it has no evidence"


@pytest.mark.parametrize("study", STUDIES, ids=IDS)
def test_every_study_is_deterministic(study):
    """Run twice, get the same thing. A study that wobbles cannot be cited."""
    assert study.measured() == study.measured()


@pytest.mark.parametrize("study", STUDIES, ids=IDS)
def test_every_study_names_a_real_category(study):
    assert study.category in taxonomy.BY_ID, (
        f"{study.id} is filed under {study.category!r}, which is not in the "
        f"taxonomy. A category that does not exist cannot be navigated to.")


@pytest.mark.parametrize("study", STUDIES, ids=IDS)
def test_a_named_pack_exists(study):
    if study.pack:
        assert study.pack in packs.available(), (
            f"{study.id} points at pack {study.pack!r}, which is not "
            f"registered — the reader would follow a dead reference")


@pytest.mark.parametrize("study", STUDIES, ids=IDS)
def test_every_study_has_the_five_parts(study):
    """Naive, looked fine, truth, caught by, fix. Any missing one guts it.

    `caught_by` is the load-bearing field: the rest is a story about one bug,
    and that is the part a reader can apply to their own pipeline.
    """
    for field_name in ("naive", "looked_fine", "truth", "caught_by", "fix"):
        value = getattr(study, field_name)
        assert value and len(value) > 30, (
            f"{study.id}.{field_name} is empty or too short to be useful")


def test_the_findings_are_what_the_titles_claim():
    """Spot-check the headline numbers, which are the ones people repeat."""
    judge = casestudies.get("judge-below-chance").measured()
    assert judge["verdict"] == "BELOW_CHANCE"
    assert judge["kappa"] < 0
    # The point of the study: answering "good" every time beats the judge.
    assert judge["always_good"] > judge["raw"]

    rubric = casestudies.get("rubric-decides").measured()
    assert rubric["specific"] > rubric["holistic"]
    assert rubric["spread"] > 0.4

    chance = casestudies.get("chance-is-not-one-over-k").measured()
    # The whole point: the same score reads as a triumph against 1/k and as
    # noise against the majority baseline.
    assert chance["looks_good_against_uniform"] > 0.3
    assert chance["over_majority"] < 0.02

    empty = casestudies.get("empty-result-passes").measured()
    assert empty["rows_found"] == 0
    assert empty["bytes_written"] == 0
    assert empty["exception_raised"] is False and empty["exit_code"] == 0

    year = casestudies.get("timezone-rolls-the-year").measured()
    assert year["same_year"] is False
    assert year["utc_year"] == year["local_year"] + 1

    label = casestudies.get("label-belongs-to-a-response").measured()
    assert label["systems_indistinguishable"] is True
    assert label["real_difference"] > 0, (
        "if the two systems are genuinely identical the study shows nothing")

    detector = casestudies.get("detector-fitted-to-its-own-attacks").measured()
    assert detector["holes_found_one_family"] == 0
    assert detector["families_missed"] == 4
    assert detector["false_positive_on_benign"] is True

    copier = casestudies.get("the-copier-wins").measured()
    assert copier["copier_wins_both"] is True
    assert copier["copier_memorised"] > copier["marginal_memorised"]

    routes = casestudies.get("routes-are-not-computations").measured()
    assert routes["route_count"] == 14 and routes["naive_product"] == 24

    dedupe = casestudies.get("blocking-hides-a-pair").measured()
    assert dedupe["compared"] < dedupe["all_pairs"]
    assert dedupe["unfindable_count"] >= 1


def test_the_generated_document_matches_the_committed_one():
    """`docs/CASE_STUDIES.md` is generated. Editing it by hand fails here.

    The failure message says how to fix it, because a test that only says
    "these differ" on a generated file wastes the reader's next ten minutes.
    """
    assert DOC.exists(), f"{DOC} is missing — regenerate it"
    expected = casestudies.render_all()
    assert DOC.read_text() == expected, (
        "docs/CASE_STUDIES.md is out of date. Regenerate it with:\n"
        "  python -c \"from browsergraph import casestudies; "
        "from pathlib import Path; "
        "Path('docs/CASE_STUDIES.md').write_text(casestudies.render_all())\"")


def test_the_document_quotes_measured_values():
    """The rendered tables hold the numbers, not placeholders."""
    text = DOC.read_text()
    for study in STUDIES:
        assert f"`{study.id}`" in text
        for key in study.quotes:
            assert f"`{key}`" in text


def test_get_refuses_an_unknown_id_and_says_what_exists():
    with pytest.raises(KeyError, match="Known:"):
        casestudies.get("no-such-study")


def test_the_index_lists_every_study():
    index = casestudies.index()
    for study in STUDIES:
        assert study.id in index


def test_studies_can_be_found_by_category_and_pack():
    assert casestudies.for_category("judge.model")
    assert casestudies.for_pack("judge")
    assert casestudies.for_category("nothing.here") == ()
