"""A model may suggest; it may not decide.

Everything a model returns is compiled, gated and scored exactly like a route
that came from sampling. These tests are mostly about what happens when the
model is wrong — which is the case that matters, because the case where it is
right needs no safeguards.
"""
from __future__ import annotations

import json

import pytest

from browsergraph import explore, search
from browsergraph.demo import workbench
from browsergraph.policy import Policy


@pytest.fixture(scope="module")
def bench():
    return workbench()


@pytest.fixture(scope="module")
def locked():
    return Policy(permissions=frozenset({"filesystem", "filesystem:read",
                                         "filesystem:write", "database",
                                         "database:read"}),
                  allow_external_effects=False, deterministic_only=True,
                  name="locked-down")


def _legal_route(bench):
    return {s.id: s.candidates[0] for s in bench.leaf_stages if s.candidates}


def _says(payload) -> explore.Proposer:
    return lambda _prompt: json.dumps(payload)


# --- the model is wrong ------------------------------------------------------

def test_a_hallucinated_candidate_is_refused_by_name(bench):
    route = dict(_legal_route(bench), resolve="does.not.exist")
    got = explore.parse(json.dumps([{"route": route, "why": "made up"}]), bench)
    assert not got[0].accepted
    assert "not admitted" in got[0].reason
    assert "does.not.exist" in got[0].reason


def test_a_route_missing_a_step_is_refused(bench):
    got = explore.parse(json.dumps([{"route": {"resolve": "x"}}]), bench)
    assert not got[0].accepted
    assert "no candidate for" in got[0].reason


def test_a_step_that_does_not_exist_is_refused(bench):
    route = dict(_legal_route(bench), imaginary="whatever")
    got = explore.parse(json.dumps([{"route": route}]), bench)
    assert not got[0].accepted
    assert "no such step" in got[0].reason


def test_a_model_cannot_grant_itself_a_permission(bench, locked):
    """Policy still applies to a suggestion. Otherwise "ask the model" becomes
    a way around the gate."""
    blocked = None
    for stage in bench.leaf_stages:
        eligible, _ = search._eligible_by_stage(bench, locked)
        refused = [c for c in stage.candidates if c not in eligible[stage.id]]
        if refused:
            blocked = (stage.id, refused[0])
            break
    assert blocked, "the locked policy blocks nothing, so this proves nothing"

    route = dict(_legal_route(bench))
    route[blocked[0]] = blocked[1]
    got = explore.guided(bench, bench.optimization_profiles[0],
                         _says([{"route": route, "why": "let me in"}]),
                         policy=locked, evaluations=40)
    assert any("blocked by policy" in s.reason for s in got.suggestions)


def test_nonsense_costs_one_call_and_nothing_else(bench):
    got = explore.guided(bench, bench.optimization_profiles[0],
                         lambda _prompt: "I am a language model and cannot help",
                         evaluations=40)
    assert got.ok
    assert any("nothing usable" in note for note in got.notes)


def test_a_model_that_raises_does_not_take_the_search_with_it(bench):
    def broken(_prompt):
        raise ConnectionError("the endpoint is down")

    got = explore.guided(bench, bench.optimization_profiles[0], broken,
                         evaluations=40)
    assert got.ok
    assert any("could not answer" in note for note in got.notes)


# --- the model is right ------------------------------------------------------

def test_a_better_suggestion_wins_and_says_so(bench, locked):
    """On a space too large to enumerate, a model that knows something the
    small-budget search does not should be able to contribute it."""
    speed = next(p for p in bench.optimization_profiles if "peed" in p.name)
    better = search.propose(bench, speed, policy=locked, strategy="beam").route

    alone = search.within(bench, speed, policy=locked, evaluations=60)
    got = explore.guided(bench, speed, _says([{"route": better, "why": "try this"}]),
                         policy=locked, evaluations=60)

    assert got.score > alone.score
    assert got.strategy.startswith("model+")
    assert any("beat the search" in note for note in got.notes)


def test_the_audit_note_compares_against_the_real_baseline(bench, locked):
    """`winner` and `baseline` are the same object. Overwriting the score before
    formatting the note made it read "beat the search (1.0304 vs 1.0304)" — a
    comparison against itself, which is worse than no note because it reads
    like a real one."""
    speed = next(p for p in bench.optimization_profiles if "peed" in p.name)
    better = search.propose(bench, speed, policy=locked, strategy="beam").route
    got = explore.guided(bench, speed, _says([{"route": better, "why": "try"}]),
                         policy=locked, evaluations=60)
    import re

    note = next(n for n in got.notes if "beat the search" in n)
    match = re.search(r"\(([0-9.]+) vs ([0-9.]+)\)", note)
    assert match, f"the note lost its comparison: {note}"
    assert float(match.group(1)) > float(match.group(2)), \
        f"the note compares a score to itself: {note}"


def test_a_worse_suggestion_is_scored_and_discarded(bench):
    """Not ignored — scored. A suggestion that loses on the numbers should lose
    for a reason that is written down."""
    poor = {s.id: s.candidates[-1] for s in bench.leaf_stages if s.candidates}
    got = explore.guided(bench, bench.optimization_profiles[0],
                         _says([{"route": poor, "why": "trust me"}]),
                         evaluations=60)
    assert any("none beat the search" in note for note in got.notes)
    assert got.suggestions and got.suggestions[0].accepted


# --- what the model is shown -------------------------------------------------

def test_the_description_states_its_own_cap(bench):
    """A model told "12 of 76 shown" can ask for the rest. A model shown 12
    silently cannot."""
    text = explore.describe(bench, max_candidates=2)
    assert "more not shown" in text


def test_the_description_carries_what_was_measured(bench):
    from browsergraph.evidence import Evidence, Observation

    store = Evidence()
    candidate = bench.leaf_stages[0].candidates[0]
    for _ in range(4):
        store.observe(Observation(candidate=candidate, context="global", ok=True))
    text = explore.describe(bench, evidence=store)
    assert "4 runs" in text


def test_the_description_names_the_ports(bench):
    """Types are the part a model gets wrong most, so they are in front of it."""
    text = explore.describe(bench, max_candidates=1)
    assert "takes" in text and "gives" in text


# --- parsing what models actually send ---------------------------------------

def test_json_wrapped_in_prose_and_fences_is_still_read(bench):
    route = _legal_route(bench)
    reply = ("Sure! Here you go:\n```json\n"
             + json.dumps([{"route": route, "why": "ok"}]) + "\n```\nHope that helps!")
    got = explore.parse(reply, bench)
    assert got and got[0].accepted


def test_a_reply_with_no_json_yields_nothing_rather_than_raising(bench):
    assert explore.parse("I refuse to answer in JSON.", bench) == []
    assert explore.parse("", bench) == []
    assert explore.parse("[not json at all", bench) == []


def test_a_suggestion_survives_a_round_trip_to_json():
    suggestion = explore.Suggestion(route={"a": "b"}, why="because",
                                    accepted=True, score=0.5)
    assert suggestion.to_dict()["route"] == {"a": "b"}
    assert suggestion.to_dict()["accepted"] is True
