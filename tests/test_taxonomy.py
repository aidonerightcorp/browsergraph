"""The map has to agree with the territory.

A taxonomy is worth having only if it cannot quietly drift from the code. These
tests are the mechanism: every template a category names must exist, every pack
must exist, every template must belong to some category, and the coverage
numbers must be counted rather than written down.

A row claiming a pack that was renamed is worse than no row at all, because
somebody will plan against it.
"""
from __future__ import annotations

import pytest

from browsergraph import packs, taxonomy, templates


def test_every_named_template_exists():
    named = {c.template for c in taxonomy.CATEGORIES if c.template}
    missing = sorted(named - set(templates.BY_ID))
    assert not missing, f"categories name templates that do not exist: {missing}"


def test_every_named_pack_exists():
    named = {c.pack for c in taxonomy.CATEGORIES if c.pack}
    missing = sorted(named - set(packs.available()))
    assert not missing, f"categories name packs that do not exist: {missing}"


def test_every_template_belongs_to_a_category():
    """A shape nobody can find is a shape nobody uses."""
    orphans = sorted(set(templates.BY_ID)
                     - {c.template for c in taxonomy.CATEGORIES})
    assert not orphans, (
        f"templates in no category: {orphans}. Add a category or say why the "
        f"shape exists.")


def test_every_pack_belongs_to_a_category():
    orphans = sorted(set(packs.available())
                     - {c.pack for c in taxonomy.CATEGORIES})
    assert not orphans, f"packs in no category: {orphans}"


def test_a_pack_is_only_claimed_where_its_template_matches():
    """A category claiming a pack must be claiming the pack's own shape.

    This is the failure a coverage table invites: a pack gets attached to a
    category it nearly fits, the number goes up, and the row is wrong in a way
    nobody reads carefully enough to notice.
    """
    for category in taxonomy.CATEGORIES:
        if not category.pack:
            continue
        module = __import__(f"browsergraph.packs.{category.pack}",
                            fromlist=["TEMPLATE"])
        assert module.TEMPLATE == category.template, (
            f"{category.id} claims pack {category.pack!r}, whose template is "
            f"{module.TEMPLATE!r}, but the category's shape is "
            f"{category.template!r}")


def test_category_ids_are_unique_and_family_prefixed():
    seen = [c.id for c in taxonomy.CATEGORIES]
    assert len(seen) == len(set(seen)), "duplicate category id"
    for category in taxonomy.CATEGORIES:
        assert category.id.startswith(f"{category.family}."), (
            f"{category.id} is in family {category.family!r} and does not say so")
        assert category.family in taxonomy.BY_FAMILY, (
            f"{category.id} names family {category.family!r}, which is not one")


def test_every_category_says_how_it_fails():
    """The field that separates a category from a topic.

    A length floor rather than a presence check, because "it breaks" is not a
    failure mode and would satisfy any test that only asked for a non-empty
    string.
    """
    for category in taxonomy.CATEGORIES:
        assert len(category.fails_as) > 40, (
            f"{category.id} has no characteristic silent failure written down; "
            f"without one it is a topic with a nice name")
        assert len(category.question) > 10, f"{category.id} has no question"
        assert len(category.shape) > 10, f"{category.id} has no shape"


def test_confusions_point_at_real_categories():
    for category in taxonomy.CATEGORIES:
        for other in category.not_to_be_confused_with:
            assert other in taxonomy.BY_ID, (
                f"{category.id} warns about {other!r}, which is not a category")
            assert other != category.id


def test_coverage_is_counted_not_asserted():
    found = taxonomy.coverage()
    assert found.total == len(taxonomy.CATEGORIES)
    assert found.expressible == sum(1 for c in taxonomy.CATEGORIES if c.template)
    assert found.runnable == sum(1 for c in taxonomy.CATEGORIES if c.pack)
    assert found.runnable <= found.expressible, (
        "a category cannot have code that runs and no shape to run it in")
    # Every gap named is a real gap.
    for category_id, _ in found.gaps:
        category = taxonomy.get(category_id)
        assert not (category.expressible and category.runnable)


def test_coverage_by_family_adds_up():
    found = taxonomy.coverage()
    assert sum(total for _, total, _, _ in found.by_family) == found.total
    assert sum(e for _, _, e, _ in found.by_family) == found.expressible
    assert sum(r for _, _, _, r in found.by_family) == found.runnable


def test_the_catalogue_mentions_every_category():
    text = taxonomy.catalog_text()
    for category in taxonomy.CATEGORIES:
        assert category.id in text


def test_one_family_at_a_time():
    text = taxonomy.catalog_text("enrich")
    assert "enrich.geo" in text
    assert "predict.tabular" not in text


def test_search_finds_the_obvious_things():
    """The question a newcomer actually asks."""
    assert any(c.id == "enrich.geo" for c in taxonomy.search("address"))
    assert any(c.id == "judge.redteam" for c in taxonomy.search("attack"))
    assert taxonomy.search("") == ()
    assert taxonomy.search("no-such-thing-in-here") == ()


def test_for_template_and_for_pack():
    assert taxonomy.for_pack("geo") == (taxonomy.get("enrich.geo"),)
    shared = taxonomy.for_template("data.migrate")
    assert len(shared) > 1, (
        "data.migrate serves both a migration and a backfill; if it stops "
        "doing so, one of those categories needs its own shape")


def test_an_unknown_category_says_how_many_there_are():
    with pytest.raises(KeyError, match="41"):
        taxonomy.get("nope.nothing")


def test_the_scope_is_bounded():
    """A taxonomy that claims everything explains nothing."""
    assert len(taxonomy.OUT_OF_SCOPE) >= 4
    for name, why in taxonomy.OUT_OF_SCOPE:
        assert name and len(why) > 40, f"{name} is excluded without a reason"


def test_every_family_has_categories():
    for family in taxonomy.FAMILIES:
        assert taxonomy.in_family(family.id), (
            f"family {family.id!r} is empty and should be deleted")


# --- the document has to agree with the code --------------------------------

def test_the_taxonomy_document_lists_every_category():
    """A published map that has drifted from the registry is worse than none."""
    import pathlib

    doc = (pathlib.Path(__file__).resolve().parent.parent
           / "docs" / "PIPELINE_TAXONOMY.md").read_text()
    missing = [c.id for c in taxonomy.CATEGORIES if f"`{c.id}`" not in doc]
    assert not missing, (
        f"docs/PIPELINE_TAXONOMY.md is missing {missing}. Regenerate it — the "
        f"counts in it are counted, and a stale table is planned against.")


def test_the_taxonomy_document_reports_the_real_counts():
    import pathlib
    import re

    doc = (pathlib.Path(__file__).resolve().parent.parent
           / "docs" / "PIPELINE_TAXONOMY.md").read_text()
    found = taxonomy.coverage()
    assert re.search(rf"\| Categories \| {found.total} \|", doc)
    assert re.search(
        rf"\| With \*\*code that runs\*\*[^|]*\| {found.runnable} ", doc)
