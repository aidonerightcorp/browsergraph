"""Changing the shape, safely.

Every other search here chooses within a graph somebody drew. These check that
an edit produces a *new* graph or a reason, never a corrupted one, and that the
refusals — which are the metric for any proposer — are recorded rather than
discarded.
"""
from __future__ import annotations

import pytest

from browsergraph import edits
from browsergraph.quick import chain, graph, node, step

REPAIR = node("repair.fix", "repair", [("in", "V")], [("out", "V")])
CONVERT = node("convert.it", "convert", [("in", "V")], [("out", "W")])


@pytest.fixture
def bench():
    nodes = [node("read.a", "read", gives=[("out", "V")]),
             node("read.b", "read", gives=[("out", "V")]),
             node("use.it", "use", [("in", "V")], [("out", "V")])]
    steps = [step("read", "Read", [], [("out", "V")], "read",
                  ["read.a", "read.b"]),
             step("use", "Use", [("in", "V")], [("out", "V")], "use", ["use.it"])]
    return graph("Editable", "two steps", steps, nodes, chain("read", "use"))


def test_an_insert_produces_a_new_graph_and_leaves_the_old_one_alone(bench):
    """A proposal that corrupts the graph you already have is an accident
    waiting for a caller who forgot to check."""
    before = len(bench.leaf_stages)
    outcome = edits.apply(bench, edits.GraphEdit("insert", ("read", "use"),
                                                 "repair.fix"), library=[REPAIR])
    assert outcome.ok, outcome.reason
    assert len(outcome.workbench.leaf_stages) == before + 1
    assert len(bench.leaf_stages) == before, "the original must be untouched"


def test_an_inserted_step_is_wired_between_its_new_neighbours(bench):
    outcome = edits.apply(bench, edits.GraphEdit("insert", ("read", "use"),
                                                 "repair.fix"), library=[REPAIR])
    pairs = {(e.source, e.target) for e in outcome.workbench.wiring()}
    assert ("read", "use") not in pairs, "the old edge must be replaced"
    assert any(t == "use" for s, t in pairs)
    assert any(s == "read" for s, t in pairs)
    assert outcome.workbench.validate() == []


def test_inserting_something_that_does_not_type_check_is_refused(bench):
    """`convert` gives W where `use` takes V. Refused with the reason, not
    returned for somebody downstream to discover."""
    outcome = edits.apply(bench, edits.GraphEdit("insert", ("read", "use"),
                                                 "convert.it"), library=[CONVERT])
    assert not outcome.ok
    assert outcome.reason and outcome.workbench is None


def test_naming_a_node_that_does_not_exist_says_to_name_a_capability(bench):
    """The failure that reads like a framework bug: a model invents an id and
    the graph silently omits a step."""
    outcome = edits.apply(bench, edits.GraphEdit("insert", ("read", "use"),
                                                 "imagined.node"))
    assert not outcome.ok
    assert "capability" in outcome.reason


def test_inserting_where_nothing_connects_is_refused(bench):
    outcome = edits.apply(bench, edits.GraphEdit("insert", ("use", "read"),
                                                 "repair.fix"), library=[REPAIR])
    assert not outcome.ok and "nothing connects" in outcome.reason


def test_a_malformed_proposal_is_a_refusal_not_a_crash(bench):
    """These arrive from models. One bad field should not end a search."""
    outcome = edits.apply(bench, edits.GraphEdit("insert", "not-a-pair", "x"))
    assert not outcome.ok and outcome.reason


def test_an_unknown_kind_lists_the_known_ones(bench):
    outcome = edits.apply(bench, edits.GraphEdit("teleport", "read"))
    assert not outcome.ok and "insert" in outcome.reason


def test_a_removal_uses_the_same_rule_the_compiler_uses(bench):
    """`use` gives V and nothing follows it, so it lifts out. The one
    implementation of 'can this be omitted' decides, not a second copy."""
    outcome = edits.apply(bench, edits.GraphEdit("remove", "use"))
    assert outcome.ok, outcome.reason
    assert [s.id for s in outcome.workbench.leaf_stages] == ["read"]


def test_removing_a_step_that_converts_the_type_is_refused():
    nodes = [node("read.a", "read", gives=[("out", "V")]),
             node("mid.x", "mid", [("in", "V")], [("out", "W")]),
             node("use.it", "use", [("in", "W")], [("out", "W")])]
    steps = [step("read", "Read", [], [("out", "V")], "read", ["read.a"]),
             step("mid", "Mid", [("in", "V")], [("out", "W")], "mid", ["mid.x"]),
             step("use", "Use", [("in", "W")], [("out", "W")], "use", ["use.it"])]
    bench = graph("Converts", "the middle changes the type", steps, nodes,
                  chain("read", "mid", "use"))
    outcome = edits.apply(bench, edits.GraphEdit("remove", "mid"))
    assert not outcome.ok and "cannot be lifted out" in outcome.reason


def test_widen_adds_a_candidate_and_never_narrows(bench):
    """A stage must admit every compatible candidate — that invariant is what
    stops a graph hiding an option from the search. So "use this one instead"
    is not an edit anybody may make; restricting is what a policy does."""
    nodes = [node("read.c", "read", gives=[("out", "V")])]
    outcome = edits.apply(bench, edits.GraphEdit("widen", "read", ["read.c"]),
                          library=nodes)
    assert outcome.ok, outcome.reason
    read = next(s for s in outcome.workbench.leaf_stages if s.id == "read")
    assert set(read.candidates) == {"read.a", "read.b", "read.c"}


def test_there_is_no_edit_that_narrows_a_step(bench):
    """Writing this as `replace` is how the invariant was rediscovered: the
    validator refused it, correctly."""
    assert "replace" not in edits.KINDS
    outcome = edits.apply(bench, edits.GraphEdit("replace", "read", ["read.b"]))
    assert not outcome.ok and "widen" in outcome.reason


def test_a_branch_needs_somewhere_to_branch_to(bench):
    outcome = edits.apply(bench, edits.GraphEdit("make_branch", "use"))
    assert not outcome.ok and "one output port" in outcome.reason


def test_edits_are_each_applied_to_the_original_not_folded(bench):
    """Otherwise a good proposal is refused because a bad one went first, and
    the refusal rate measures the ordering rather than the proposer."""
    proposals = [edits.GraphEdit("remove", "use"),
                 edits.GraphEdit("insert", ("read", "use"), "repair.fix")]
    outcomes = edits.apply_all(bench, proposals, library=[REPAIR])
    assert all(o.ok for o in outcomes), [o.reason for o in outcomes]


def test_the_refusal_rate_is_reported(bench):
    """The metric for a proposer. A model whose edits are mostly illegal is not
    helping, however good the legal ones are."""
    proposals = [edits.GraphEdit("insert", ("read", "use"), "repair.fix"),
                 edits.GraphEdit("insert", ("read", "use"), "convert.it")]
    got = edits.variants(bench, proposals, library=[REPAIR, CONVERT])
    assert len(got.accepted) == 1
    assert got.refusal_rate == 0.5
    assert "1 of 2" in got.text()


def test_insertion_points_finds_only_the_legal_ones(bench):
    assert edits.insertion_points(bench, REPAIR) == [("read", "use")]
    assert edits.insertion_points(bench, CONVERT) == []


def test_mechanical_proposals_need_no_model(bench):
    """The baseline a guided proposer has to beat, and a real competitor: on a
    space this size, enumeration is complete."""
    proposals = edits.mechanical(bench, [REPAIR, CONVERT])
    assert any(p.what == "repair.fix" for p in proposals)
    got = edits.variants(bench, proposals, library=[REPAIR, CONVERT])
    assert got.accepted


def test_edits_round_trip_through_dicts(bench):
    """Because they arrive from a model as JSON."""
    original = [edits.GraphEdit("insert", ("read", "use"), "repair.fix",
                                why="because", confidence=0.8)]
    again = edits.from_dicts(edits.to_dicts(original))
    assert again[0].kind == "insert" and again[0].where == ("read", "use")
    assert again[0].confidence == 0.8


def test_a_reply_missing_the_kind_is_skipped_not_guessed():
    assert edits.from_dicts([{"where": "read"}, {"kind": "remove", "where": "x"}]) \
        == [edits.GraphEdit("remove", "x")]
