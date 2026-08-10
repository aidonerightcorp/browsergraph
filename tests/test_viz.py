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


# --- the kinds have to be visible --------------------------------------------

def _kinded(bench, **kinds):
    from dataclasses import replace
    return replace(bench, stages=tuple(
        replace(s, kind=kinds[s.id]) if s.id in kinds else s
        for s in bench.stages))


def test_a_map_stage_does_not_look_like_an_ordinary_one(diamond):
    """Adding kinds without drawing them made the picture say something untrue,
    and the whole case for these diagrams is that the shape is visible."""
    plain = viz.dag(diamond).svg
    mapped = viz.dag(_kinded(diamond, price="map")).svg
    assert plain != mapped
    assert "MAP" in mapped and "per item" in mapped


def test_a_branch_stage_is_drawn_as_taking_one_way_out(diamond):
    svg = viz.dag(_kinded(diamond, parse="branch")).svg
    assert "BRANCH" in svg
    assert "stroke-dasharray" in svg


def test_the_caption_explains_the_outlines_it_used(diamond):
    assert "once per item" in viz.dag(_kinded(diamond, price="map")).note
    assert "outline" not in viz.dag(diamond).note


def test_mermaid_uses_its_own_shapes_for_the_kinds(diamond):
    assert "price[[" in viz.to_mermaid(_kinded(diamond, price="map"))
    assert "parse{{" in viz.to_mermaid(_kinded(diamond, parse="branch"))


def test_the_json_form_carries_the_kind(diamond):
    import json
    data = json.loads(viz.to_json(_kinded(diamond, price="map")))
    kinds = {s["id"]: s["kind"] for s in data["stages"]}
    assert kinds["price"] == "map" and kinds["parse"] == "atomic"


# --- matplotlib, the one thing planmap had that viz did not ------------------

def test_a_matplotlib_figure_can_be_drawn_for_any_workbench(diamond):
    """`spacemap` and `planmap` had this and `viz` did not, which was the only
    thing standing between them and being fully superseded. You cannot paste an
    SVG into a LaTeX document without a conversion step."""
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")

    ax = viz.to_figure(diamond, route={"parse": "dom.lxml", "price": "price.css",
                                       "title": "title.og", "join": "join.strict"})
    assert ax.get_title().startswith("Listing extractor")
    assert len(ax.lines) >= 1


def test_the_route_line_runs_through_the_labels_not_over_them(diamond):
    """Joining box centres strikes every label out. Two points per column — the
    label's left and right edge — is what the SVG version learned the same way."""
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")

    route = {"parse": "dom.lxml", "price": "price.css",
             "title": "title.og", "join": "join.strict"}
    ax = viz.to_figure(diamond, route=route)
    xs = ax.lines[0].get_xdata()
    assert len(xs) == 2 * len(route), "one point per column strikes the text out"


def test_both_routes_are_drawn_and_labelled(diamond):
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")

    ax = viz.to_figure(
        diamond,
        route={"parse": "dom.lxml", "price": "price.css",
               "title": "title.og", "join": "join.strict"},
        alternative={"parse": "dom.regex", "price": "price.llm",
                     "title": "title.llm", "join": "join.lenient"})
    labels = {line.get_label() for line in ax.lines}
    assert {"before", "after"} <= labels


def test_a_workbench_with_no_candidates_says_so_rather_than_drawing_nothing():
    from browsergraph.workbench import WorkbenchDefinition

    pytest.importorskip("matplotlib")
    with pytest.raises(ValueError, match="nothing to draw"):
        viz.to_figure(WorkbenchDefinition(title="Empty"))


# --- the two pictures of a run, rather than of a graph -----------------------

def _ran(stage, candidate, started, seconds, **flags):
    from browsergraph.execute import StepRun
    return StepRun(stage=stage, candidate=candidate, started=started,
                   seconds=seconds, **flags)


def _run_of(*steps):
    from browsergraph.execute import Run
    return Run(plan_digest="x", steps=list(steps),
               seconds=max(s.started + s.seconds for s in steps))


def test_a_timeline_places_overlapping_steps_at_the_same_offset():
    """The whole reason `started` exists. Stacking durations end to end draws a
    parallel run as a sequential one and hides the only thing worth seeing."""
    run = _run_of(_ran("read", "r", 0.0, 0.10),
                  _ran("topic", "t", 0.10, 0.20),
                  _ran("urgency", "u", 0.10, 0.18))
    svg = viz.timeline(run).svg
    starts = [float(x.split('"')[0])
              for x in svg.split('<rect x="')[1:]]
    assert starts[1] == pytest.approx(starts[2]), \
        "two steps that began together must be drawn together"
    assert starts[0] < starts[1]


def test_a_timeline_says_when_more_work_happened_than_clock_elapsed():
    run = _run_of(_ran("a", "a1", 0.0, 0.5), _ran("b", "b1", 0.0, 0.5))
    assert "of work in" in viz.timeline(run).note


def test_a_timeline_tells_skipped_cached_and_failed_apart():
    """All three finish without raising and mean entirely different things."""
    run = _run_of(_ran("a", "a1", 0.0, 0.1),
                  _ran("b", "b1", 0.1, 0.0, skipped=True),
                  _ran("c", "c1", 0.1, 0.0, cached=True),
                  _ran("d", "d1", 0.1, 0.1, ok=False, error="boom"))
    svg = viz.timeline(run).svg
    for word in ("ran", "skipped", "cached", "failed"):
        assert f"· {word}" in svg
    assert "boom" in svg, "the reason belongs on the bar, not only in the log"


def test_a_run_with_no_steps_draws_nothing_rather_than_dividing_by_zero():
    from browsergraph.execute import Run
    assert viz.timeline(Run()).svg.startswith("<svg")


def test_instant_steps_do_not_divide_by_a_zero_span():
    run = _run_of(_ran("a", "a1", 0.0, 0.0), _ran("b", "b1", 0.0, 0.0))
    assert viz.timeline(run).svg.count("<rect") == 2 + len(viz._OUTCOMES)


def _solution():
    from browsergraph.solve import Attempt, Solution
    champion = {"read": "r1", "clean": "c1"}
    return Solution(
        champion=champion, fallbacks=[{"read": "r1", "clean": "c2"}],
        score=3.0, total_routes=8, seconds=1.0,
        attempts=[Attempt(route=champion, ok=True, score=3.0, seconds=0.2),
                  Attempt(route={"read": "r1", "clean": "c2"}, ok=True,
                          score=1.0, seconds=0.2),
                  Attempt(route={"read": "r2", "clean": "c1"}, ok=False,
                          reason="produced nothing", seconds=0.1)])


def test_a_scoreboard_keeps_the_attempts_that_did_not_work():
    """An attempt that failed is evidence about the space, not a gap to tidy
    away — a champion shown alone is a number with no denominator."""
    svg = viz.scoreboard(_solution()).svg
    assert "did not work" in svg
    assert "produced nothing" in svg


def test_a_scoreboard_marks_the_champion_and_its_fallback():
    svg = viz.scoreboard(_solution()).svg
    assert "★ champion" in svg
    assert "↳ " in svg, "the fallback has to be distinguishable from a loser"


def test_a_scoreboard_labels_only_what_differs_from_the_champion():
    """Naming all seven steps in every row is a wall of identical text."""
    svg = viz.scoreboard(_solution()).svg
    assert ">c2<" in svg or "c2" in svg
    assert svg.count("read=r1") >= 1, "the full route stays available on hover"


def test_an_unsolved_solution_still_draws():
    from browsergraph.solve import Attempt, Solution
    empty = Solution(attempts=[Attempt(route={"a": "a1"}, ok=False,
                                       reason="no")], total_routes=2)
    assert "did not work" in viz.scoreboard(empty).svg


def test_a_report_omits_the_charts_it_was_given_no_data_for(diamond):
    """An empty chart implies a measurement somebody made and did not like."""
    bare = viz.report(diamond)
    assert "scoreboard" not in bare.lower()
    full = viz.report(diamond, run=_run_of(_ran("a", "a1", 0.0, 0.1)),
                      solution=_solution())
    assert full.count("<svg") == bare.count("<svg") + 2


# --- is it getting better ---------------------------------------------------

def test_a_trend_draws_its_reference_lines_with_names():
    """A rising line proves nothing on its own — it could be rising towards
    mediocre. The ceiling next to it is what makes it a claim."""
    figure = viz.trend([0.3, 0.4, 0.6],
                       reference={"best possible": 0.65, "at random": 0.32})
    assert "best possible" in figure.svg
    assert "at random" in figure.svg
    assert figure.svg.count("stroke-dasharray") == 2


def test_a_trend_shows_the_raw_series_under_the_smoothed_one():
    """A smoothed line with the noise hidden is a claim about how steady the
    improvement was, and that claim is usually the first one to be wrong."""
    figure = viz.trend([0.1, 0.9] * 10, smooth=5)
    assert figure.svg.count("<polyline") == 2
    assert "mean of 5" in figure.svg
    assert "Faint line is every value" in figure.note


def test_a_trend_without_smoothing_draws_one_line():
    assert viz.trend([1.0, 2.0, 3.0]).svg.count("<polyline") == 1


def test_a_flat_trend_does_not_divide_by_a_zero_range():
    assert viz.trend([0.5] * 5).svg.startswith("<svg")


def test_an_empty_trend_returns_a_figure_rather_than_raising():
    assert viz.trend([]).svg.startswith("<svg")


def test_a_trend_scales_to_include_its_references():
    """A reference above every observation must still be on the canvas —
    clipping the ceiling is how a chart implies the ceiling was reached."""
    figure = viz.trend([0.1, 0.2], reference={"target": 0.9})
    ys = [float(chunk.split('"')[0])
          for chunk in figure.svg.split('y1="')[1:]]
    assert min(ys) >= 0 and max(ys) <= figure.height
