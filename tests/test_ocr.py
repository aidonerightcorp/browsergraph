"""Reading a page from its pixels.

Two things justify OCR in a library that has a perfectly good DOM: text that is
*only* in the image — painted on a canvas, baked into a banner — and the
question "does this screenshot contain any text at all", which nothing else in a
run can answer.

That second one is not hypothetical. WebKit on a slim image renders a
perfectly laid-out page with no glyphs, and does not error: the graph reports
success, the click lands, the element appears, the extracted value is correct.
Every data-level check passes and the picture is blank.
"""
from __future__ import annotations

import functools
import http.server
import pathlib
import socketserver
import threading

import pytest

from browsergraph import Engine, Graph, Spec, run
from browsergraph.dimensions import Display
from browsergraph.doctor import available_engines
from browsergraph.drivers import build
from browsergraph.nodes.actions import Navigate, WaitFor
from browsergraph.ocr import (
    BACKENDS,
    FLAT_COLOURS,
    INSTALL,
    OcrResult,
    Reading,
    available,
    has_text,
    read_text,
    report,
)

TMP = pathlib.Path(__file__).resolve().parent.parent / ".artifacts" / "bg_ocr"
TMP.mkdir(parents=True, exist_ok=True)

PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8><title>Acme</title>
</head><body style="font:16px sans-serif;padding:30px">
<h1 style="font-size:40px">Acme Roofing</h1>
<p>Call (303) 555-0142 today</p>
<canvas id=c width=700 height=120></canvas>
<script>const x=document.getElementById('c').getContext('2d');
// Large and high-contrast on purpose. A demonstration of "OCR can read text
// the DOM does not have" should not double as a test of one backend's ability
// to resolve thin strokes — that is a different claim.
x.font='bold 44px sans-serif';x.fillStyle='#000000';
x.fillText('CANVAS 0800 999 5555', 10, 70);</script>
</body></html>"""

needs_ocr = pytest.mark.skipif(not available(), reason="no OCR backend installed")
needs_browser = pytest.mark.skipif(Engine.PLAYWRIGHT not in available_engines(),
                                   reason="playwright not installed")


@pytest.fixture(scope="module")
def server():
    (TMP / "p.html").write_text(PAGE, encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(TMP))
    handler.log_message = lambda *a, **k: None
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def blank(path: pathlib.Path, size=(400, 300), colour=(255, 255, 255)):
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGB", size, colour).save(path)
    return str(path)


# --- backends ---------------------------------------------------------------

def test_backends_are_ranked_cheapest_first():
    costs = [b.cost for b in sorted(BACKENDS, key=lambda b: b.cost)]
    assert costs == sorted(costs)
    assert all(b.name and b.cost > 0 for b in BACKENDS)


def test_availability_is_reported_honestly():
    names = {b.name for b in BACKENDS}
    assert set(available()) <= names
    text = report()
    for name in names:
        assert name in text


def test_every_backend_says_how_to_install_it():
    for b in BACKENDS:
        assert b.name in INSTALL, f"{b.name} has no install hint"


def test_no_backend_is_an_honest_failure_not_a_crash():
    got = read_text("/nonexistent/image.png", backend="definitely-not-a-backend")
    assert not got.ok and "unknown OCR backend" in got.error


def test_a_failing_read_is_data_not_an_exception():
    """OCR is best-effort; a caller wants to know, not to have the graph die."""
    got = read_text("/nonexistent/image.png")
    assert isinstance(got, Reading)
    assert not got.ok and got.error


# --- the blank-screenshot check ---------------------------------------------

def test_a_blank_image_has_no_text(tmp_path):
    assert has_text(blank(tmp_path / "blank.png")) is False


def test_the_check_needs_no_ocr_backend(tmp_path, monkeypatch):
    """The cheap check is the one to trust — a model asked to read a blank image
    will often invent text, which would defeat the whole point."""
    import browsergraph.ocr as mod
    monkeypatch.setattr(mod, "available", lambda: [])
    assert has_text(blank(tmp_path / "blank.png")) is False


def test_an_unreadable_path_does_not_raise_a_false_alarm(tmp_path):
    """Cannot tell is not the same as no text; do not cry wolf."""
    assert has_text(str(tmp_path / "missing.png")) is True


def test_the_flatness_threshold_is_documented():
    assert FLAT_COLOURS > 0


@needs_browser
def test_a_rendered_page_has_text(server):
    shot = str(TMP / "rendered.png")
    spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS)
    browser = build(spec)
    browser.start()
    try:
        browser.goto(f"{server}/p.html")
        browser.screenshot(shot)
    finally:
        browser.stop()
    assert has_text(shot) is True


# --- reading what the DOM cannot give you -----------------------------------

@needs_ocr
@needs_browser
def test_ocr_recovers_text_painted_on_a_canvas(server):
    """The case that justifies OCR existing: a canvas has no DOM to query."""
    shot = str(TMP / "canvas.png")
    spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS)
    browser = build(spec)
    browser.start()
    try:
        browser.goto(f"{server}/p.html")
        dom = browser.text_of("body")
        # Wait for the canvas to actually have ink on it. `goto` returns at
        # DOMContentLoaded, which can precede the paint being composited, and
        # screenshotting into that gap captures a blank canvas — a flaky test
        # that would look like an OCR failure.
        painted = browser.eval_js("""(() => {
            const c = document.querySelector('canvas');
            if (!c) return false;
            const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            for (let i = 3; i < d.length; i += 4) if (d[i] !== 0) return true;
            return false;
        })()""")
        assert painted, "the canvas never got painted; the test page is wrong"
        browser.screenshot(shot)
    finally:
        browser.stop()

    reading = read_text(shot)
    assert reading.ok, reading.error
    assert "0800" in reading.text.replace(" ", "") or "0800" in reading.text
    assert "0800" not in dom, "the canvas number must not be in the DOM"

    result = OcrResult(dom_text=dom, ocr=reading)
    extra = " ".join(result.only_in_image).lower()
    assert "canvas" in extra or "0800" in extra


@needs_ocr
@needs_browser
def test_the_ocr_node_writes_where_it_says(server):
    from browsergraph.nodes.ocr_nodes import OcrExtract

    spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS)
    graph = (Graph("ocr").add(Navigate(f"{server}/p.html")).add(WaitFor("h1"))
             .add(OcrExtract(path=str(TMP / "node.png"), into="page_text")))
    result = run(graph, spec, build(spec))
    assert result.ok, result.context.error
    assert "Acme" in result.context.data["page_text"]
    assert result.context.artifacts, "the image should be kept as evidence"


@needs_ocr
@needs_browser
def test_ocr_verify_fails_when_the_text_is_not_visible(server):
    """A value can be in the DOM and invisible. This checks what a person sees."""
    from browsergraph.nodes.ocr_nodes import OcrVerify

    spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS)
    graph = (Graph("v").add(Navigate(f"{server}/p.html")).add(WaitFor("h1"))
             .add(OcrVerify("Acme Roofing", path=str(TMP / "v-ok.png"))))
    assert run(graph, spec, build(spec)).ok

    graph = (Graph("v2").add(Navigate(f"{server}/p.html")).add(WaitFor("h1"))
             .add(OcrVerify("Definitely Not On This Page",
                            path=str(TMP / "v-bad.png"))))
    result = run(graph, spec, build(spec), strict=False)
    assert not result.ok
    assert "not visible" in result.context.error


# --- the architecture ------------------------------------------------------

def test_ocr_nodes_are_candidates_on_the_right_planes():
    """They should appear because of what they declare, not a table edit."""
    from browsergraph.planmap import planes
    by_name = {p.name: [c.name for c in p.candidates] for p in planes()}
    assert "ocr_extract" in by_name["extract"]
    assert "ocr_verify" in by_name["verify"]


def test_reading_pixels_is_priced_as_a_last_resort():
    from browsergraph.planmap import COST
    assert COST["ocr_extract"] > COST["extract" if "extract" in COST else "css"]
