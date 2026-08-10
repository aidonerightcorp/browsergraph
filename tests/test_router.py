"""Routing a job to a model, rather than sending everything to one default.

The failure this prevents is quiet: a pipeline that pays frontier prices to
classify a boolean, or hands a screenshot to a text model and gets confident
fiction back.
"""
from __future__ import annotations

import pytest

from browsergraph.models import Catalog, ModelInfo, ModelUnavailable
from browsergraph.router import (
    CLASSIFY,
    CODE,
    EMBED,
    EXTRACT,
    READ_IMAGE,
    ROLES,
    VERIFY,
    Router,
    capabilities_needed,
    describe_roles,
    size_of,
)


def catalog(*models, reachable=True, error="") -> Catalog:
    return Catalog(host="http://fake", reachable=reachable, error=error,
                   models=list(models))


TINY = ModelInfo("llama3.2:1b", ("completion",), parameter_size="1B")
INSTRUCT = ModelInfo("qwen3:8b-instruct", ("completion", "tools"),
                     parameter_size="8B")
BIG = ModelInfo("glm-5.2:cloud", ("completion", "tools", "thinking"),
                parameter_size="400B")
VISION = ModelInfo("llava:7b", ("completion", "vision"), parameter_size="7B")
EMBEDDER = ModelInfo("nomic-embed-text", ("embedding",), parameter_size="137M")
CODER = ModelInfo("deepseek-coder:14b", ("completion",), parameter_size="14B")

FULL = catalog(TINY, INSTRUCT, BIG, VISION, EMBEDDER, CODER)


def test_a_closed_short_answer_gets_the_smallest_model_that_qualifies():
    """A 400B model choosing between yes and no is money set on fire."""
    assert Router(catalog=FULL).choose(CLASSIFY).model == TINY.name


def test_a_job_where_being_wrong_is_expensive_gets_the_strongest():
    assert size_of(ModelInfo("x", parameter_size="400B")) == 400
    chosen = Router(catalog=catalog(TINY, BIG)).choose(VERIFY)
    assert chosen.model == BIG.name


def test_hints_are_ordered_not_a_binary_flag():
    """Treating them as a set made every hinted model tie, so the alphabet
    decided — and `extract` picked a *coder* model over an instruct one."""
    assert Router(catalog=FULL).choose(EXTRACT).model == INSTRUCT.name
    assert Router(catalog=FULL).choose(CODE).model == CODER.name


def test_an_image_job_requires_a_vision_model():
    assert Router(catalog=FULL).choose(READ_IMAGE).model == VISION.name


def test_a_missing_capability_is_reported_not_substituted():
    """A text model handed a screenshot does not fail. It invents."""
    choice = Router(catalog=catalog(TINY)).choose(READ_IMAGE)
    assert not choice.ok
    assert "vision" in choice.error


def test_millions_are_parsed_so_an_embedder_is_not_called_20b():
    assert size_of(EMBEDDER) == pytest.approx(0.137)
    assert "0.137" in Router(catalog=FULL).choose(EMBED).why


def test_a_local_model_wins_a_tie_against_a_cloud_one():
    local = ModelInfo("thing:8b", ("completion",), parameter_size="8B")
    remote = ModelInfo("thing:8b-cloud", ("completion",), parameter_size="8B")
    choice = Router(catalog=catalog(remote, local)).choose(CLASSIFY)
    assert choice.model == local.name


def test_an_override_is_honoured_when_it_qualifies():
    router = Router(catalog=FULL, overrides={CLASSIFY: BIG.name})
    choice = router.choose(CLASSIFY)
    assert choice.model == BIG.name and "requested" in choice.why


def test_an_override_that_cannot_do_the_job_is_a_hard_error():
    """Silently substituting would make the run untraceable, which is the thing
    an override exists to prevent."""
    router = Router(catalog=FULL, overrides={READ_IMAGE: TINY.name})
    assert not router.choose(READ_IMAGE).ok


def test_a_role_can_fall_back_to_another_role_and_says_so():
    router = Router(catalog=catalog(TINY))
    choice = router.choose(ROLES["plan"].name)
    assert choice.fallback_from == "extract"
    assert "no model for plan" in choice.why


def test_an_unreachable_host_is_an_honest_failure():
    router = Router(catalog=catalog(reachable=False, error="connection refused"))
    choice = router.choose(EXTRACT)
    assert not choice.ok and "connection refused" in choice.error


def test_an_unknown_role_names_the_known_ones():
    choice = Router(catalog=FULL).choose("telepathy")
    assert not choice.ok and "known:" in choice.error


def test_the_report_covers_every_role():
    text = Router(catalog=FULL).report()
    for role in ROLES:
        assert role in text


def test_unfilled_roles_are_listed_rather_than_hidden():
    router = Router(catalog=catalog(TINY))
    unfilled = {c.role for c in router.unfilled()}
    assert READ_IMAGE in unfilled and EMBED in unfilled


def test_choices_are_cached_so_routing_is_not_the_slow_part():
    router = Router(catalog=FULL)
    assert router.choose(EXTRACT) is router.choose(EXTRACT)


def test_the_pull_list_says_what_a_host_needs():
    needed = capabilities_needed()
    assert "vision" in needed and "embedding" in needed


def test_the_roles_are_documented():
    text = describe_roles()
    for role in ROLES:
        assert role in text


def test_route_raises_rather_than_returning_a_default(monkeypatch):
    import browsergraph.router as mod
    monkeypatch.setattr(mod.Catalog, "load",
                        classmethod(lambda cls, *a, **k: catalog(TINY)))
    with pytest.raises(ModelUnavailable):
        mod.route(READ_IMAGE)
