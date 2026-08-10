"""The pictures have to be honest, general, and the same twice.

Three properties are worth testing and the rest is pixels:

* **General** — a workbench with nothing browser-shaped in it must draw. If a
  figure ever needs a domain concept, the core stopped being general and the
  visualiser is where that shows first.
* **Honest** — a cap that hides candidates must say so in the caption. A diagram
  that silently drew 14 of 76 is worse than no diagram, because it looks
  complete.
* **Deterministic** — the same workbench draws the same bytes, or a figure
  committed next to the code churns the diff on every run.
"""
from __future__ import annotations

import pytest

from browsergraph import viz
from browsergraph.manifest import PortSpec
from browsergraph.workbench import (
    Edge,
    NodeCandidate,
    StageDefinition,
    WorkbenchDefinition,
)


def _stage(sid, name, ins, outs, cands):
    return StageDefinition(id=sid, name=name,
                           inputs=tuple(PortSpec(n, t) for n, t in ins),
                           outputs=tuple(PortSpec(n, t) for n, t in outs),
                           candidates=tuple(cands))


@pytest.fixture
def diamond():
    """Fan-out and join — the shape a sequence of stages cannot express."""
    ids = ["dom.lxml", "dom.regex", "price.css", "price.llm",
           "title.og", "title.llm", "join.strict", "join.lenient"]
    return WorkbenchDefinition(
        title="Listing extractor",
        task="Parse a page, pull price and title independently, join them.",
        stages=(
            _stage("parse", "Parse page", [("in", "Html")], [("out", "Dom")],
                   ["dom.lxml", "dom.regex"]),
            _stage("price", "Extract price", [("in", "Dom")], [("out", "Money")],
                   ["price.css", "price.llm"]),
            _stage("title", "Extract title", [("in", "Dom")], [("out", "Text")],
                   ["title.og", "title.llm"]),
            _stage("join", "Join record",
                   [("price", "Money"), ("title", "Text")], [("out", "Record")],
                   ["join.strict", "join.lenient"]),
        ),
        edges=(Edge("parse", "price"), Edge("parse", "title"),
               Edge("price", "join", to_port="price"),
               Edge("title", "join", to_port="title")),
        candidates=tuple(NodeCandidate(id=c, node_id=c) for c in ids),
    )


@pytest.fixture
def kitchen():
    """A domain with no browser in it anywhere."""
    return WorkbenchDefinition(
        title="Bread",
        task="Turn flour into bread.",
        stages=(
            _stage("mix", "Mix", [("in", "Flour")], [("out", "Dough")],
                   ["mix.hand", "mix.machine"]),
            _stage("bake", "Bake", [("in", "Dough")], [("out", "Loaf")],
                   ["bake.oven"]),
        ),
        candidates=(NodeCandidate(id="mix.hand", node_id="mix.hand"),
                    NodeCandidate(id="mix.machine", node_id="mix.machine"),
                    NodeCandidate(id="bake.oven", node_id="bake.oven")),
    )


# --- general ----------------------------------------------------------------

def test_a_domain_with_no_browser_in_it_draws(kitchen):
    """The claim the whole module exists to make good on."""
    for figure in (viz.dag(kitchen), viz.route_space(kitchen)):
        assert figure.svg.startswith("<svg")
        assert "browser" not in figure.svg.lower()


def test_the_shape_figure_shows_the_layers_a_chain_cannot_have(diamond):
    assert diamond.layers() == [["parse"], ["price", "title"], ["join"]]
    figure = viz.dag(diamond)
    assert "2 parallel" in figure.svg, "the fan-out layer is not announced"
    assert "3 layers, widest 2" in figure.note


def test_a_join_edge_is_labelled_with_the_port_it_lands_on(diamond):
    """An unlabelled join is the bug that started all of this: two edges into
    one box, and no way to see which input each one feeds."""
    svg = viz.dag(diamond).svg
    assert ">price</text>" in svg and ">title</text>" in svg


def test_a_chain_says_it_is_a_chain(kitchen):
    assert "a chain" in viz.dag(kitchen).note


# --- honest -----------------------------------------------------------------

def test_a_row_cap_that_hides_candidates_is_disclosed():
    bench = WorkbenchDefinition(
        title="Wide", stages=(_stage("only", "Only", [], [("out", "X")],
                                     [f"c{i}" for i in range(30)]),),
        candidates=tuple(NodeCandidate(id=f"c{i}", node_id=f"c{i}")
                         for i in range(30)))
    figure = viz.route_space(bench, max_rows=5)
    assert "25 candidates not drawn" in figure.note


def test_the_route_count_in_the_caption_is_the_workbenchs_own(diamond):
    assert str(diamond.route_count()) in viz.route_space(diamond).note


def test_the_funnel_keeps_a_zero_row_visible_because_none_is_a_result():
    figure = viz.funnel([("all", 500), ("legal", 0)])
    assert "→ none" in figure.svg
    assert figure.svg.count("<rect") == 2


def test_the_funnel_says_its_scale_is_logarithmic():
    """A log axis presented as linear is the chart equivalent of a silent cap."""
    assert "log-scaled" in viz.funnel([("a", 10), ("b", 1)]).svg


def test_evidence_separates_support_from_objection():
    svg = viz.evidence({"good": 1.0, "bad": -1.0})
    assert viz.GOOD in svg.svg and viz.CHOSEN in svg.svg
    assert "+1.00" in svg.svg and "-1.00" in svg.svg


# --- deterministic and safe -------------------------------------------------

def test_the_same_workbench_draws_the_same_bytes(diamond):
    assert viz.dag(diamond).svg == viz.dag(diamond).svg
    assert viz.route_space(diamond).svg == viz.route_space(diamond).svg


def test_a_label_containing_markup_cannot_escape_into_the_page():
    bench = WorkbenchDefinition(
        title="X", stages=(_stage("s", '<script>alert(1)</script>', [],
                                  [("out", "X")], ["c"]),),
        candidates=(NodeCandidate(id="c", node_id="c"),))
    assert "<script>" not in viz.dag(bench).svg


def test_a_wide_figure_keeps_its_pixels_instead_of_shrinking(diamond):
    """`width="100%"` on a 2,850px graph scales the labels to four pixels tall.
    Unreadable-but-complete is worse than scroll-to-see."""
    svg = viz.dag(diamond).svg
    assert 'width="100%"' not in svg
    assert "overflow-x:auto" in viz.dag(diamond).html(standalone=False)


def test_an_empty_workbench_returns_a_figure_rather_than_raising():
    empty = WorkbenchDefinition(title="Nothing")
    assert viz.dag(empty).svg
    assert viz.route_space(empty).svg
    assert viz.funnel([]).svg
    assert viz.evidence({}).svg


# --- other renderers --------------------------------------------------------

def test_mermaid_carries_the_same_edges_and_ports(diamond):
    text = viz.to_mermaid(diamond, {"parse": "dom.lxml"})
    assert "graph LR" in text
    assert "price -->|price| join" in text
    assert "title -->|title| join" in text


def test_the_json_form_carries_ids_even_though_the_picture_shows_names(diamond):
    import json
    data = json.loads(viz.to_json(diamond, {"parse": "dom.lxml"}))
    assert data["layers"] == [["parse"], ["price", "title"], ["join"]]
    assert data["route"] == {"parse": "dom.lxml"}
    assert data["route_count"] == diamond.route_count()
    assert {"source": "price", "target": "join", "to_port": "price"} in data["edges"]


def test_a_report_is_one_self_contained_page_with_no_network_calls(diamond):
    page = viz.report(diamond, route={"parse": "dom.lxml"},
                      search=[("all", 16), ("chosen", 1)], bits={"parse": 0.5})
    assert page.startswith("<!doctype html>")
    for forbidden in ("http://", "https://", "<script src", "@import"):
        assert forbidden not in page, f"report reaches for {forbidden}"


def test_a_figure_saves_as_a_standalone_file(diamond, tmp_path):
    out = viz.dag(diamond).save(tmp_path / "figures" / "shape.html")
    assert (tmp_path / "figures" / "shape.html").read_text().startswith("<!doctype")
    assert out.endswith("shape.html")
