"""The browser-less engine, and the optional extraction backends.

Most pages do not need a browser. This engine is roughly an order of magnitude
faster, and TLS impersonation is what makes it viable on defended sites — the
handshake is fingerprinted before any JavaScript runs.

What it must never do is *pretend*. A driver that silently no-ops a click or
returns an empty screenshot is worse than one that refuses, because the failure
surfaces later as missing data with no explanation.
"""
from __future__ import annotations

import functools
import http.server
import importlib.util
import pathlib
import socketserver
import threading

import pytest

from browsergraph import Engine, Graph, Spec, run
from browsergraph.dimensions import Capture, Display, Stealth, Vision, validate
from browsergraph.drivers import build
from browsergraph.extract.patterns import extract_contacts
from browsergraph.nodes.actions import Extract, Navigate, WaitFor
from browsergraph.preprocess import Preprocess, backends, reduce

HAVE_CURL = importlib.util.find_spec("curl_cffi") is not None
HAVE_SELECTOLAX = importlib.util.find_spec("selectolax") is not None
needs_http = pytest.mark.skipif(not (HAVE_CURL and HAVE_SELECTOLAX),
                                reason="engine=http needs curl-cffi + selectolax")

# Disk-backed, not /tmp: on this host /tmp is a RAM tmpfs, so browser
# downloads, videos and screenshots consume memory and hit its quota.
TMP = pathlib.Path(__file__).resolve().parent.parent / ".artifacts" / "bg_http"
TMP.mkdir(parents=True, exist_ok=True)

PAGE = """<!doctype html><html lang=en><head><title>Server Rendered</title>
<meta name=description content="no javascript needed"></head><body>
<h1 id=hdr>Server Rendered</h1>
<p>Contact sales@static.example or call (303) 555-0142.</p>
<a id=next href="/second.html">next page</a>
<button id=btn>Not clickable without JS</button>
</body></html>"""


@pytest.fixture(scope="module")
def server():
    (TMP / "p.html").write_text(PAGE, encoding="utf-8")
    (TMP / "second.html").write_text(
        "<!doctype html><html><head><title>Second</title></head>"
        "<body><h1>Second Page</h1></body></html>", encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(TMP))
    handler.log_message = lambda *a, **k: None
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def http_spec(**over) -> Spec:
    return Spec(engine=Engine.HTTP, stealth=Stealth.UNDETECTED, **over)


# --- capability declaration -------------------------------------------------

def test_http_is_a_declared_engine_with_metadata():
    from browsergraph.dimensions import ENGINE_FAMILY, ENGINE_IMPORT, ENGINE_REQUIREMENT
    assert ENGINE_FAMILY[Engine.HTTP] == "http"
    assert "curl" in ENGINE_REQUIREMENT[Engine.HTTP]
    assert ENGINE_IMPORT[Engine.HTTP]


def test_http_counts_as_evasion_capable():
    """TLS impersonation is the layer checked before any JS runs."""
    assert validate(http_spec()) == []


def test_http_rejects_what_it_cannot_do():
    assert any("vision" in p for p in validate(http_spec(vision=Vision.ALWAYS)))
    assert any("video" in p for p in
               validate(http_spec(capture=Capture.VIDEO, artifact_dir="/tmp")))
    assert any("display" in p for p in validate(http_spec(display=Display.HEADED)))


def test_binary_axis_does_not_constrain_http():
    from browsergraph.dimensions import Binary
    for binary in Binary:
        assert validate(http_spec(binary=binary)) == [], binary


# --- live behaviour ---------------------------------------------------------

@needs_http
def test_fetches_and_extracts(server):
    spec = http_spec()
    graph = (Graph("http").add(Navigate(f"{server}/p.html"))
             .add(WaitFor("#hdr")).add(Extract("#hdr", into="heading")))
    result = run(graph, spec, build(spec))
    assert result.ok, result.context.error
    assert result.context.data["heading"] == "Server Rendered"


@needs_http
def test_page_state_carries_status_and_title(server):
    b = build(http_spec())
    b.start()
    try:
        state = b.goto(f"{server}/p.html")
        assert state.status == 200 and state.title == "Server Rendered"
    finally:
        b.stop()


@needs_http
def test_extractors_work_on_fetched_html(server):
    b = build(http_spec())
    b.start()
    try:
        b.goto(f"{server}/p.html")
        from browsergraph.extract.content import parse_page
        page = parse_page(b.html(), f"{server}/p.html")
        found = extract_contacts(page.text, page.links, page.mailtos)
        assert "sales@static.example" in found.emails
        assert found.phones
    finally:
        b.stop()


@needs_http
def test_clicking_a_link_navigates(server):
    b = build(http_spec())
    b.start()
    try:
        b.goto(f"{server}/p.html")
        b.click("#next")
        assert b.state().title == "Second"
    finally:
        b.stop()


@needs_http
def test_unsupported_operations_refuse_rather_than_pretend(server):
    """A silent no-op surfaces later as missing data with no explanation."""
    b = build(http_spec())
    b.start()
    try:
        b.goto(f"{server}/p.html")
        with pytest.raises(RuntimeError, match="no JavaScript runtime"):
            b.eval_js("1+1")
        with pytest.raises(RuntimeError, match="cannot type"):
            b.type("#btn", "hello")
        with pytest.raises(RuntimeError, match="cannot screenshot"):
            b.screenshot("/tmp/x.png")
        with pytest.raises(RuntimeError, match="no href"):
            b.click("#btn")
    finally:
        b.stop()


@needs_http
def test_missing_element_reports_absence_not_an_error(server):
    b = build(http_spec())
    b.start()
    try:
        b.goto(f"{server}/p.html")
        assert b.find("#nope") is None
        assert b.wait_for("#nope", timeout=0.1) is False
        assert b.text_of("#nope") == ""
    finally:
        b.stop()


@needs_http
def test_same_graph_runs_on_http_and_a_browser(server):
    """The seam holds even for an engine with no browser at all."""
    graph = (Graph("both").add(Navigate(f"{server}/p.html"))
             .add(Extract("#hdr", into="heading")))
    http_result = run(graph, http_spec(), build(http_spec()))

    from browsergraph.doctor import available_engines
    if Engine.PLAYWRIGHT not in available_engines():
        pytest.skip("playwright not installed")
    pw_spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS)
    pw_result = run(graph, pw_spec, build(pw_spec))

    assert http_result.context.data["heading"] == pw_result.context.data["heading"]


# --- optional backends ------------------------------------------------------

def test_backends_are_reported():
    caps = backends()
    assert set(caps) >= {"trafilatura", "selectolax"}
    assert all(isinstance(v, bool) for v in caps.values())


def test_readability_degrades_without_the_optional_backend(monkeypatch):
    """A missing extra must degrade, never crash."""
    import browsergraph.preprocess as pre
    monkeypatch.setattr(pre, "_trafilatura", lambda html: None)
    html = ("<html><body><main><h1>T</h1><p>" + "Real content here. " * 40 +
            "</p></main><footer>(c) 2026</footer></body></html>")
    out = reduce(html, Preprocess.READABILITY)
    assert "Real content" in out.content


def test_readability_drops_boilerplate():
    html = ("<html><body><nav>Home About Contact</nav><main><h1>T</h1><p>"
            + "Real content sentence. " * 40 +
            "</p></main><footer>(c) 2026 Privacy Terms</footer></body></html>")
    content = reduce(html, Preprocess.READABILITY).content
    assert "Real content" in content
    assert "Privacy Terms" not in content
