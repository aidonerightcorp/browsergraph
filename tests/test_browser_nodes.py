"""The new nodes, against a real page in a real browser.

Every one of these could be asserted against a mock and prove nothing. A
`<select>` cannot be driven by clicking, a download that starts before anything
is listening is simply lost, and an iframe is exactly the case where "it worked
on the mock" means least. So this serves a page and drives it.
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
from browsergraph.nodes.browser_nodes import (
    A11yTree,
    AssertText,
    AssertUrl,
    Attribute,
    Cookies,
    CountElements,
    Download,
    Press,
    SavePdf,
    SelectOption,
    SetViewport,
    Upload,
    UseFrame,
    WaitStable,
)

TMP = pathlib.Path(__file__).resolve().parent.parent / ".artifacts" / "bg_nodes"
needs_browser = pytest.mark.skipif(Engine.PLAYWRIGHT not in available_engines(),
                                   reason="playwright not installed")

PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<title>Node demo</title></head><body>
<h1 id=title>Node demo</h1>
<nav><a href="#one" id=lnk data-testid=nav-one>Go to one</a></nav>
<form id=f onsubmit="return false"><label for=size>Size</label>
<select id=size name=size><option value=s>Small</option><option value=l>Large</option></select>
<input id=file type=file><input id=q placeholder="Search here" name=q>
<button id=go type=button>Go</button></form>
<a id=dl href="data:text/plain;charset=utf-8,downloaded%20payload" download="out.txt">Download</a>
<iframe id=frame src="inner.html" width=300 height=80></iframe>
<ul id=items><li>a</li><li>b</li><li>c</li></ul>
<p id=echo></p>
<div id=hidden style="display:none">SECRET MENU</div>
<script>
document.getElementById('q').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('echo').textContent = 'ENTER SEEN';
});
document.getElementById('size').addEventListener('change', e => {
  document.getElementById('echo').textContent = 'SIZE ' + e.target.value;
});
setTimeout(() => { const d = document.createElement('div');
  d.id = 'late'; d.textContent = 'LATE CONTENT'; document.body.appendChild(d); }, 300);
</script></body></html>"""


@pytest.fixture(scope="module")
def server():
    TMP.mkdir(parents=True, exist_ok=True)
    (TMP / "p.html").write_text(PAGE, encoding="utf-8")
    (TMP / "inner.html").write_text(
        "<!doctype html><title>Inner</title><p id=deep>INSIDE THE FRAME</p>",
        encoding="utf-8")
    (TMP / "attach.txt").write_text("attached file body\n", encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(TMP))
    handler.log_message = lambda *a, **k: None
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def drive(server, *nodes, strict=True):
    spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS)
    graph = (Graph("t").add(Navigate(f"{server}/p.html")).add(WaitFor("#title")))
    for node in nodes:
        graph.add(node)
    return run(graph, spec, build(spec), strict=strict)


@needs_browser
def test_wait_stable_waits_for_content_that_arrives_late(server):
    """`goto` returns at DOMContentLoaded, which can precede the paint. This
    repository's own canvas test screenshotted into that gap and captured a
    blank image — it looked like an OCR failure for far too long."""
    result = drive(server, WaitStable(quiet_ms=250), AssertText("LATE CONTENT"))
    assert result.ok, result.context.error


@needs_browser
def test_press_reaches_the_page(server):
    result = drive(server, Press("Enter", "#q"), AssertText("ENTER SEEN", "#echo"))
    assert result.ok, result.context.error


@needs_browser
def test_select_option_is_not_a_click(server):
    """A native select opens an OS-level menu a click cannot reach."""
    result = drive(server, SelectOption("#size", "l"),
                   AssertText("SIZE l", "#echo"))
    assert result.ok, result.context.error


@needs_browser
def test_upload_attaches_a_file(server):
    result = drive(server, Upload("#file", str(TMP / "attach.txt")),
                   Attribute("#file", "value", into="picked"))
    assert result.ok, result.context.error
    assert "attach.txt" in (result.context.data["picked"] or "")


@needs_browser
def test_download_keeps_the_file_and_records_it(server, tmp_path):
    dest = str(tmp_path / "kept.txt")
    result = drive(server, Download("#dl", dest))
    assert result.ok, result.context.error
    assert pathlib.Path(dest).read_text().strip() == "downloaded payload"
    assert dest in result.context.artifacts, "a download must be evidence"


@needs_browser
def test_frames_are_modal_in_both_directions(server):
    """Selenium's frame switch is modal and Playwright's is a handle; a node
    written once has to mean the same thing on both."""
    result = drive(server, UseFrame("#frame"),
                   AssertText("INSIDE THE FRAME", "#deep", name="inside"),
                   UseFrame(None, name="top"),
                   AssertText("Node demo", "#title", name="outside"))
    assert result.ok, result.context.error


@needs_browser
def test_a_missing_frame_fails_rather_than_silently_staying_put(server):
    """Reporting success would put every later lookup in a frame that is not
    there, and the failure would surface several nodes later."""
    result = drive(server, UseFrame("#no-such-frame"), strict=False)
    assert not result.ok and "iframe" in result.context.error


@needs_browser
def test_the_a11y_tree_names_things_and_offers_selectors(server):
    result = drive(server, A11yTree(limit=60))
    assert result.ok, result.context.error
    tree = result.context.data["a11y"]
    roles = {entry["role"] for entry in tree}
    assert {"heading", "link", "select", "button"} <= roles
    assert any(e.get("selector") == "#lnk" for e in tree)
    assert any(e.get("selector") == '[data-testid="nav-one"]' or
               e.get("selector") == "#size" for e in tree)


@needs_browser
def test_the_a11y_tree_omits_what_nobody_can_see(server):
    """A tree full of hidden menus is how a model confidently clicks something
    that is not there."""
    result = drive(server, A11yTree(limit=200))
    names = " ".join(e.get("name", "") for e in result.context.data["a11y"])
    assert "SECRET MENU" not in names


@needs_browser
def test_the_a11y_tree_renders_compactly_for_a_prompt(server):
    result = drive(server, A11yTree(limit=40))
    text = A11yTree.as_text(result.context.data["a11y"])
    assert "heading: Node demo" in text and "[#title]" in text
    assert len(text) < 4000, "the point is that it is cheap"


@needs_browser
def test_count_and_attribute(server):
    result = drive(server, CountElements("#items li", into="n"),
                   Attribute("#lnk", "href", into="href"))
    assert result.context.data["n"] == 3
    assert result.context.data["href"] == "#one"


@needs_browser
def test_assert_text_fails_when_the_text_is_absent(server):
    result = drive(server, AssertText("NOT ON THIS PAGE"), strict=False)
    assert not result.ok and "not in" in result.context.error


@needs_browser
def test_assert_text_can_require_absence(server):
    assert drive(server, AssertText("SECRET MENU", "#title", absent=True)).ok


@needs_browser
def test_assert_url(server):
    assert drive(server, AssertUrl("/p.html")).ok
    assert not drive(server, AssertUrl("/nowhere"), strict=False).ok


@needs_browser
def test_cookies_round_trip(server):
    result = drive(server, Cookies(set_to=[{"name": "seen", "value": "1",
                                            "url": f"{server}/p.html"}]))
    assert "seen" in {c["name"] for c in result.context.data["cookies"]}


@needs_browser
def test_viewport_and_pdf(server, tmp_path):
    out = str(tmp_path / "page.pdf")
    result = drive(server, SetViewport(900, 700), SavePdf(out))
    assert result.ok, result.context.error
    assert pathlib.Path(out).stat().st_size > 1000
    assert out in result.context.artifacts


def test_the_new_nodes_declare_the_capabilities_they_need():
    from browsergraph import capabilities as caps
    assert caps.required(Press("Enter")) == (caps.PRESS,)
    assert caps.required(Download("#a", "/tmp/x")) == (caps.DOWNLOAD,)
    assert caps.required(WaitStable()) == ()


def test_the_new_nodes_appear_on_the_planes_by_themselves():
    """They land there because of what their contracts declare."""
    from browsergraph.planmap import planes
    by_name = {p.name: [c.name for c in p.candidates] for p in planes()}
    assert "assert_text" in by_name["verify"]
    assert "wait_stable" in by_name["verify"]
