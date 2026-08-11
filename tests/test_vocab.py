"""The vocabulary has to know what it does and does not have.

`edits.question` tells a model to name a capability rather than invent a node
id, because a hallucinated id is the failure that reads like a framework bug.
Nothing could resolve a capability back to nodes, so that advice was
aspirational. These check it no longer is.
"""
from __future__ import annotations

import pytest

from browsergraph import arena, edits, vocab


def test_the_vocabulary_is_built_from_what_exists():
    """Discovered, never declared. A table listing what it believed implemented
    a capability would drift the first time a node was deleted."""
    registry = vocab.registry()
    assert len(registry) > 40
    tabular = registry.get("data.read")
    assert tabular and "tabular" in tabular.packs
    assert tabular.templates, "templates declare it too"


def test_a_template_declaring_something_is_not_an_implementation():
    """The distinction the whole module rests on: a shape described before it
    was built is a gap, not a capability."""
    registry = vocab.registry()
    declared_only = [c for c in registry.values() if c.templates and not c.packs]
    assert declared_only, "some template obligation should be unfilled"
    for capability in declared_only:
        assert not capability.implemented
        assert "nothing provides it" in capability.describe()


def test_the_gaps_are_reported_rather_than_hidden():
    missing = vocab.gaps()
    assert missing
    assert all(not c.implemented for c in missing)
    assert "DECLARED BUT NOT PROVIDED" in vocab.catalog_text()


@pytest.mark.parametrize("query,expected", [
    ("fill in the missing values", "data.impute"),
    ("should we add a step that removes duplicates", "data.deduplicate"),
    ("encode categorical columns", "feature.categorical"),
    ("score the model", "model.evaluate"),
    ("reduce the number of features", "feature.reduce"),
])
def test_a_plain_english_question_finds_the_right_capability(query, expected):
    assert expected in [c.id for c in vocab.find(query, limit=3)]


def test_filler_words_do_not_rank():
    """"fill in the missing values" scored `act.explain` above `data.clean`
    until stopwords were dropped, purely because "in" is a substring of half
    the vocabulary."""
    assert vocab.find("the and of to in") == []


def test_a_gap_needs_aliases_more_than_a_built_capability_does():
    """Nobody searching for "remove duplicates" types `data.deduplicate`, and a
    gap nobody can find is a gap that stays open."""
    for name in vocab.WANTED:
        assert vocab.ALIASES.get(name), f"{name} is unfindable without aliases"


def test_resolution_is_filtered_by_the_type_checker():
    """Search by meaning; the survivors are whatever compiles. The other order
    produces a plausible node that does not fit."""
    task = arena.haystack(steps=4, width=3, seed=1)
    resolved = vocab.resolve("repair", task.workbench, library=task.library)
    assert resolved, "the library provides it"
    manifest, positions = resolved[0]
    assert manifest.id == "repair.fix"
    assert positions, "and it legally fits somewhere"
    for after, before in positions:
        trial = edits.GraphEdit("insert", (after, before), manifest.id)
        assert edits.apply(task.workbench, trial, library=task.library).ok


def test_resolving_something_nobody_provides_returns_nothing():
    task = arena.needle(steps=3, width=3, seed=0)
    assert vocab.resolve("data.deduplicate", task.workbench) == []


def test_an_edit_may_name_a_capability_instead_of_a_node():
    """What the prompt asks a model to do, now actually supported."""
    task = arena.haystack(steps=4, width=3, seed=1)
    edge = task.workbench.wiring()[0]
    outcome = edits.apply(
        task.workbench,
        edits.GraphEdit("insert", (edge.source, edge.target), "repair"),
        library=task.library)
    assert outcome.ok, outcome.reason
    assert any("repair.fix" in s.candidates
               for s in outcome.workbench.leaf_stages)


def test_an_invented_name_is_refused_with_suggestions():
    """The refusal has to be useful. "no node called X" leaves a model with
    nowhere to go; naming the nearest real capabilities gives it one."""
    task = arena.haystack(steps=4, width=3, seed=1)
    edge = task.workbench.wiring()[0]
    outcome = edits.apply(
        task.workbench,
        edits.GraphEdit("insert", (edge.source, edge.target), "impute.magic"),
        library=task.library)
    assert not outcome.ok
    assert "Did you mean" in outcome.reason
    assert "impute" in outcome.reason


def test_the_engine_capability_module_is_a_different_thing():
    """Two registries called `capabilities` — one about browser engines, one
    about pipeline steps — is a collision waiting for whoever greps next. This
    module was very nearly written over that one."""
    from browsergraph import capabilities

    assert capabilities.PRESS == "press"
    assert not hasattr(capabilities, "registry")
    assert not hasattr(vocab, "ENGINE_CAPABILITIES")
