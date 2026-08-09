"""Task planes and candidate routes — the architecture, not the catalogue.

The claim under test is that a task decomposes into planes, each plane offers
interchangeable candidates, and the chosen route *changes* as evidence arrives.
A diagram that showed one fixed pipeline would be arguing the opposite.
"""
from __future__ import annotations

import re

import pytest

from browsergraph.contracts import contract_of
from browsergraph.planmap import (
    PLANE_RULES,
    PRIOR,
    Candidate,
    belief,
    observe,
    planes,
    routes,
    score_routes,
    to_html,
    to_text,
)


@pytest.fixture
def ps():
    return planes()


# --- derived, not written down ----------------------------------------------

def test_candidates_are_derived_from_contracts(ps):
    """Click is on `act` because it declares mutates, not because a table says so."""
    act = next(p for p in ps if p.name == "act")
    assert "click" in [c.name for c in act.candidates]
    assert contract_of(__import__(
        "browsergraph.nodes.actions", fromlist=["Click"]).Click).mutates


def test_a_new_node_appears_without_editing_the_diagram():
    """The point of deriving: the architecture reflects what exists."""
    from browsergraph.nodes.base import Node

    class Teleport(Node):
        kind = "teleport_demo"
        mutates = True

        def run(self, ctx):
            return ctx

    got = planes({"teleport_demo": Teleport})
    act = next(p for p in got if p.name == "act")
    assert "teleport_demo" in [c.name for c in act.candidates]


def test_every_plane_has_more_than_one_way_to_answer_it(ps):
    """A plane with a single candidate is a fixed capability, not a dimension."""
    for plane in ps:
        assert len(plane.candidates) >= 2, f"{plane.name} offers no alternative"


def test_planes_are_ordered_as_a_task_runs(ps):
    assert [p.name for p in ps] == [n for n, _, _ in PLANE_RULES]


def test_each_plane_states_its_question(ps):
    assert all(p.question for p in ps)


def test_baseline_candidates_are_present(ps):
    """css and dwell are real answers that happen not to be node classes.

    Omitting them would make the diagram claim you need a model to find an
    element — the opposite of what the library recommends.
    """
    locate = next(p for p in ps if p.name == "locate")
    settle = next(p for p in ps if p.name == "settle")
    assert "css" in [c.name for c in locate.candidates]
    assert "dwell" in [c.name for c in settle.candidates]


# --- routes and self-optimisation -------------------------------------------

def test_routes_are_one_candidate_per_plane(ps):
    for r in routes(ps, limit=50):
        assert set(r.picks) == {p.name for p in ps}


def test_with_no_evidence_the_cheapest_route_wins(ps):
    best = score_routes(ps, routes(ps, limit=100000))[0]
    assert best.picks["reach"] == "http"        # no browser at all
    assert best.picks["locate"] == "css"        # no model
    assert "no evidence" in best.why


def test_the_route_changes_once_outcomes_exist(ps):
    """The whole claim: this is self-optimising, not a fixed pipeline."""
    naive = score_routes(ps, routes(ps, limit=100000))[0]
    naive_picks = dict(naive.picks)

    observe(ps, {"http": (1, 20), "patchright": (18, 20), "css": (4, 20),
                 "healing": (15, 18), "wait_for": (19, 20), "dwell": (6, 20),
                 "click": (18, 20), "screenshot": (20, 20), "extract": (17, 18)})
    learned = score_routes(ps, routes(ps, limit=100000))[0]

    assert learned.picks != naive_picks, "evidence changed nothing"
    assert learned.picks["reach"] == "patchright", "should abandon the failing engine"
    assert learned.picks["locate"] == "healing", "should abandon the failing locator"


def test_an_unmeasured_candidate_is_a_coin_flip_not_a_free_win(ps):
    """The bug this replaced: scoring only over measured steps.

    A route with one well-evidenced step and five untried ones beat a route
    measured end to end, so "after learning" recommended the parts nobody had
    ever run.
    """
    observe(ps, {"screenshot": (20, 20)})
    best = score_routes(ps, routes(ps, limit=100000))[0]
    measured = [c for p in ps for c in p.candidates if c.p is not None]
    assert len(measured) == 1
    assert best.picks["verify"] == "screenshot", "the one measured win should be taken"
    # ...but the rest of the route must still fall back to cheap, not to unknowns
    assert best.picks["reach"] == "http"


def test_belief_shrinks_toward_the_prior_on_thin_evidence():
    thin = Candidate("x", p=1.0, evidence=1)
    solid = Candidate("y", p=1.0, evidence=50)
    assert belief(Candidate("z")) == PRIOR
    assert belief(thin) < belief(solid)
    assert PRIOR < belief(thin) < 1.0


def test_one_weak_step_drags_a_route_down(ps):
    """Every plane has to work, so a route is a product, not an average."""
    observe(ps, {"patchright": (20, 20), "wait_for": (20, 20), "healing": (20, 20),
                 "click": (20, 20), "screenshot": (20, 20), "extract": (0, 20)})
    best = score_routes(ps, routes(ps, limit=100000))[0]
    assert best.picks["extract"] != "extract", "a step that always fails was kept"


# --- rendering --------------------------------------------------------------

def test_html_is_self_contained_and_shows_both_routes(ps):
    naive = score_routes(ps, routes(ps, limit=2000))[0]
    observe(ps, {"patchright": (18, 20), "healing": (15, 18)})
    learned = score_routes(ps, routes(ps, limit=2000))[0]
    html = to_html(ps, before=naive, after=learned)
    assert "<svg" in html and "-before" in html and "-after" in html
    assert not re.findall(r'(?:src|href)="https?://', html)


def test_every_candidate_is_drawn(ps):
    html = to_html(ps)
    for plane in ps:
        for c in plane.candidates:
            assert f">{c.name}<" in html, f"{c.name} missing from the diagram"


def test_text_view_marks_the_chosen_route(ps):
    best = score_routes(ps, routes(ps, limit=2000))[0]
    out = to_text(ps, best)
    assert "chosen route" in out and "because" in out
    assert out.count("*") >= len(ps)


def test_evidence_is_shown_when_it_exists(ps):
    observe(ps, {"patchright": (18, 20)})
    assert "p=0.86" in to_html(ps) or "p=0.85" in to_html(ps)
