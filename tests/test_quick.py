"""The short way to write a graph.

This exists because every notebook opened by defining the same three helpers.
Thirty lines, copied nine times, and anyone who copied a notebook to start a
project got helpers the library did not have. The convenient way to use
something should be the supported way.

Nothing here adds capability, so the tests are about the two things that go
wrong with convenience wrappers: they quietly drop an argument, or they build
something the validator would have refused.
"""
from __future__ import annotations

import pytest

from browsergraph.quick import (
    chain,
    fanin,
    fanout,
    graph,
    link,
    node,
    problems,
    step,
    subgraph,
)


def _pair():
    nodes = [node("read.csv", "read", gives=[("out", "Rows")]),
             node("clean.trim", "clean", [("in", "Rows")], [("out", "Rows")])]
    steps = [step("read", "Read it", [], [("out", "Rows")], "read", ["read.csv"]),
             step("clean", "Clean it", [("in", "Rows")], [("out", "Rows")],
                  "clean", ["clean.trim"])]
    return steps, nodes


# --- it builds something the validator accepts -------------------------------

def test_a_graph_built_this_way_validates():
    steps, nodes = _pair()
    bench = graph("Tidy", "Read a CSV and trim it.", steps, nodes,
                  chain("read", "clean"))
    assert problems(bench) == []
    assert bench.route_count() == 1


def test_candidates_are_derived_so_nobody_writes_them_twice():
    steps, nodes = _pair()
    bench = graph("Tidy", "t", steps, nodes)
    assert {c.id for c in bench.candidates} == {"read.csv", "clean.trim"}


def test_strict_refuses_rather_than_returning_something_broken():
    """A script wants the exception; a notebook wants to see the complaint."""
    broken = [step("only", "Only", [], [], "cap", ["missing.node"])]
    with pytest.raises(ValueError):
        graph("Bad", "t", broken, [], strict=True)
    assert problems(graph("Bad", "t", broken, [])), "not strict should still report"


# --- nothing is quietly dropped ----------------------------------------------

def test_extra_node_arguments_reach_the_manifest():
    """`effects`, `permissions` and the rest have to survive the wrapper, or it
    is a wrapper that silently weakens every graph written with it."""
    made = node("send.email", "send", [("in", "Message")], [("out", "Receipt")],
                effects=("network.write",), permissions=("net.send",),
                runtime={"deterministic": False},
                metrics={"source": "illustrative-prior", "quality": 0.9},
                facets={"purpose.statement": "deliver a message"})
    assert made.effects == ("network.write",)
    assert made.permissions == ("net.send",)
    assert made.runtime["deterministic"] is False
    assert made.facets["purpose.statement"] == "deliver a message"


def test_a_step_keeps_its_kind_and_optionality():
    mapped = step("each", "Each", [("in", "List[Row]")], [("out", "List[Row]")],
                  "work", ["w.one"], kind="map", optional=True)
    assert mapped.kind == "map"
    assert mapped.optional is True


def test_a_step_gets_a_success_line_it_did_not_have_to_write():
    """The strict model refuses an empty contract, so the wrapper supplies the
    weakest true one rather than letting the graph fail later."""
    assert "produced its declared output" in step(
        "s", "Do it", [], [("out", "X")], "cap").success


# --- the shapes --------------------------------------------------------------

def test_fanout_and_fanin_build_a_diamond():
    edges = (*fanout("parse", ["price", "title"]),
             *fanin({"price": "price", "title": "title"}, "join"))
    assert len(edges) == 4
    assert {e.to_port for e in edges if e.target == "join"} == {"price", "title"}


def test_fanin_labels_every_edge_because_an_unlabelled_join_is_the_bug():
    edges = fanin({"a": "left", "b": "right"}, "join")
    assert all(e.to_port for e in edges)


def test_chain_is_the_boring_case_written_once():
    assert [(e.source, e.target) for e in chain("a", "b", "c")] == \
        [("a", "b"), ("b", "c")]


def test_chain_of_one_step_has_no_edges():
    assert chain("only") == ()


# --- reuse, which substages never gave you -----------------------------------

def _fragment():
    steps = [
        step("profile", "Profile", [("in", "Rows")], [("out", "Profile")],
             "profile", ["p.one"]),
        step("schema", "Schema", [("in", "Profile")], [("out", "Findings")],
             "schema", ["s.one"]),
        step("dist", "Distribution", [("in", "Profile")], [("out", "Findings")],
             "dist", ["d.one"]),
        step("verdict", "Verdict",
             [("schema", "Findings"), ("dist", "Findings")],
             [("out", "Verdict")], "gate", ["g.one"]),
    ]
    links = (*fanout("profile", ["schema", "dist"]),
             *fanin({"schema": "schema", "dist": "dist"}, "verdict"))
    return steps, links


def test_the_same_fragment_can_appear_twice_without_colliding():
    """A composite stage groups steps for display. It does not let you take a
    shape and use it in two places, because the ids would collide the moment
    you did."""
    steps, links = _fragment()
    first, first_links = subgraph("inbound", steps, links)
    second, second_links = subgraph("outbound", steps, links)

    ids = [s.id for s in (*first, *second)]
    assert len(ids) == len(set(ids)), "the two copies collided"
    assert "inbound.profile" in ids and "outbound.profile" in ids
    assert all(e.source.startswith("inbound.") for e in first_links)


def test_a_spliced_fragment_keeps_its_internal_wiring():
    steps, links = _fragment()
    moved, rewired = subgraph("x", steps, links)
    joins = [e for e in rewired if e.target == "x.verdict"]
    assert {e.to_port for e in joins} == {"schema", "dist"}
    assert {e.source for e in joins} == {"x.schema", "x.dist"}


def test_rename_lets_a_fragment_meet_the_graph_around_it():
    steps, links = _fragment()
    moved, rewired = subgraph("x", steps, links,
                              rename={"profile": "shared.profile"})
    assert any(s.id == "shared.profile" for s in moved)
    assert any(e.source == "shared.profile" for e in rewired)


def test_two_copies_of_a_fragment_layer_independently():
    steps, links = _fragment()
    first, first_links = subgraph("a", steps, links)
    second, second_links = subgraph("b", steps, links)
    nodes = [node("load.csv", "load", gives=[("out", "Rows")]),
             node("p.one", "profile", [("in", "Rows")], [("out", "Profile")]),
             node("s.one", "schema", [("in", "Profile")], [("out", "Findings")]),
             node("d.one", "dist", [("in", "Profile")], [("out", "Findings")]),
             node("g.one", "gate", [("schema", "Findings"), ("dist", "Findings")],
                  [("out", "Verdict")])]
    bench = graph(
        "two", "t",
        [step("load", "Load", [], [("out", "Rows")], "load", ["load.csv"]),
         *first, *second], nodes,
        [link("load", "a.profile"), link("load", "b.profile"),
         *first_links, *second_links])

    assert problems(bench) == []
    layers = bench.layers()
    assert ["a.profile", "b.profile"] in layers, \
        "the two copies should be independent and share a layer"
