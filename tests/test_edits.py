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


# --- asking a model for a shape ---------------------------------------------

def test_a_question_offers_only_legal_insertion_points(bench):
    """Working out where a node type-checks costs nothing and removes the
    largest category of wasted proposals."""
    text = edits.question(bench, kind="missing", library=[REPAIR, CONVERT])
    assert "between read and use" in text
    assert "repair.fix" in text
    # `convert` gives W where `use` takes V, so there is nowhere for it to go
    # and the prompt must say so rather than offer a position. Located by line
    # rather than by splitting on the id, which also appears in a description.
    lines = text.splitlines()
    header = next(i for i, line in enumerate(lines)
                  if line.startswith("  convert.it"))
    assert "nowhere" in lines[header + 2]


def test_a_question_states_the_reply_format_in_the_compilers_vocabulary(bench):
    text = edits.question(bench, library=[REPAIR])
    for kind in edits.KINDS:
        assert kind in text
    assert '"kind"' in text and '"where"' in text


def test_a_question_carries_what_has_been_tried_when_given_it(bench):
    text = edits.question(bench, library=[REPAIR], history="8 routes, best 0.4")
    assert "8 routes, best 0.4" in text


def test_an_unknown_question_says_what_there_is(bench):
    with pytest.raises(KeyError, match="missing"):
        edits.question(bench, kind="what-colour-is-it")


def test_a_reply_wrapped_in_prose_and_fences_still_parses():
    """Models wrap JSON in explanations nobody asked for. Discarding a good
    edit over a code fence is strictness in the one place it buys nothing."""
    reply = ('Sure! Here is my suggestion:\n```json\n'
             '[{"kind": "insert", "where": ["read", "use"], '
             '"what": "repair.fix", "why": "it repairs", "confidence": 0.9}]\n'
             '```\nHope that helps.')
    got = edits.parse(reply)
    assert len(got) == 1
    assert got[0].kind == "insert" and got[0].where == ("read", "use")
    assert got[0].confidence == 0.9


def test_a_reply_with_no_json_yields_nothing_rather_than_raising():
    assert edits.parse("I don't think it needs anything.") == []
    assert edits.parse("[not json at all}") == []


def test_the_whole_loop_from_a_reply_to_a_compiled_graph(bench):
    """Question, reply, parse, apply. What a model-guided proposer does, with
    the model's part written out by hand so the rest can be tested."""
    edits.question(bench, library=[REPAIR])
    reply = ('[{"kind": "insert", "where": ["read", "use"], '
             '"what": "repair.fix", "why": "the data needs repairing"},'
             ' {"kind": "insert", "where": ["read", "use"], '
             '"what": "convert.it", "why": "this one cannot fit"}]')
    got = edits.variants(bench, edits.parse(reply), library=[REPAIR, CONVERT])
    assert len(got.accepted) == 1, got.text()
    assert got.refusal_rate == 0.5
    assert got.accepted[0].workbench.validate() == []
