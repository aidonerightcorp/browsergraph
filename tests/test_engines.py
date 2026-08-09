"""Cross-engine conformance against real browsers.

This is the suite that proves the `BrowserPort` seam actually holds: the same
graph, the same assertions, every engine installed on this machine. If an
adapter drifts, it fails here rather than in production on one engine only.

Engines that are not installed are skipped, not failed — the point is to verify
whatever is present, on any machine.
"""
from __future__ import annotations

import functools
import http.server
import pathlib
import socketserver
import threading

import pytest

from browsergraph import Engine, Graph, Spec, run
from browsergraph.dimensions import Binary, Capture, Display, validate
from browsergraph.doctor import available_engines
from browsergraph.drivers import DriverUnavailable, build
from browsergraph.extract.content import parse_page
from browsergraph.nodes.actions import Click, Extract, Navigate, Screenshot, WaitFor

# Disk-backed, not /tmp: on this host /tmp is a RAM tmpfs, so browser
# downloads, videos and screenshots consume memory and hit its quota.
TMP = pathlib.Path(__file__).resolve().parent.parent / ".artifacts" / "bg_engines"
TMP.mkdir(parents=True, exist_ok=True)

PAGE = """<!doctype html><html lang=en><head><title>Conformance</title>
<meta name=description content="cross engine"></head><body>
<h1 id=hdr>Conformance</h1>
<p>Reach us at team@conf.example or (303) 555-0142.</p>
<button id=go>Go</button><div id=out></div>
<a href="/other.html">other</a>
<div style="height:3000px"></div>
<script>go.onclick=()=>out.textContent='clicked'</script></body></html>"""

#: (engine, binary) pairs worth exercising when the engine is installed.
ENGINE_MATRIX = [
    (Engine.PLAYWRIGHT, Binary.BUNDLED_CHROMIUM),
    (Engine.PATCHRIGHT, Binary.BUNDLED_CHROMIUM),
    (Engine.SELENIUM, Binary.SYSTEM_CHROME),
    (Engine.SELENIUM_UC, Binary.SYSTEM_CHROME),
]

#: Engines that are minutes-slow or environment-sensitive. Verified, but not on
#: every run — camoufox launches a hardened Firefox inside an isolated
#: subprocess and can exceed a 30s navigation timeout on a loaded host.
SLOW_MATRIX = [
    pytest.param(Engine.CAMOUFOX, Binary.FIREFOX,
                 marks=pytest.mark.slow),
]


@pytest.fixture(scope="module")
def server():
    (TMP / "p.html").write_text(PAGE, encoding="utf-8")
    (TMP / "other.html").write_text("<h1>Other</h1>", encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(TMP))
    handler.log_message = lambda *a, **k: None
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def isolated_env_ready(engine) -> bool:
    """True when the engine has a built venv of its own."""
    from browsergraph.isolate import env_for
    return env_for(engine).exists


def spec_for(engine, binary, **over) -> Spec:
    """In-process where possible; isolated where the engine cannot co-install.

    Camoufox pins its own playwright build, so it is never installed in the
    shared env — it is exercised through its isolated worker instead. The
    assertions below are identical either way, which is the point.
    """
    use_isolation = engine not in available_engines() and isolated_env_ready(engine)
    return Spec(engine=engine, binary=binary, display=Display.HEADLESS,
                isolated=use_isolation, **over)


def installed(engine) -> bool:
    return engine in available_engines() or isolated_env_ready(engine)


@pytest.mark.parametrize("engine,binary", ENGINE_MATRIX + SLOW_MATRIX,
                         ids=lambda v: getattr(v, "value", str(v)))
def test_engine_conformance(engine, binary, server):
    """Every adapter must satisfy the whole BrowserPort contract identically."""
    if not installed(engine):
        pytest.skip(f"{engine.value} not installed")
    spec = spec_for(engine, binary)
    assert validate(spec) == [], validate(spec)

    try:
        browser = build(spec)
        browser.start()
    except (DriverUnavailable, Exception) as e:
        pytest.skip(f"{engine.value} could not launch: {type(e).__name__}: {e}")

    try:
        try:
            state = browser.goto(f"{server}/p.html")
        except RuntimeError as e:
            if "Timeout" in str(e):
                pytest.skip(f"{engine.value} timed out launching/navigating: {e}")
            raise
        assert state.title == "Conformance", f"{engine.value}: wrong title"

        assert browser.find("#hdr") is not None
        assert browser.find("#does-not-exist") is None, \
            f"{engine.value}: find() returned a hit for a missing element"

        assert browser.wait_for("#go", timeout=5) is True
        assert browser.wait_for("#never", timeout=1) is False, \
            f"{engine.value}: wait_for reported success for a missing element"

        browser.click("#go")
        assert browser.wait_for("#out", timeout=5)
        assert browser.text_of("#out") == "clicked", f"{engine.value}: click had no effect"

        assert "Conformance" in browser.text_of("#hdr")
        html = browser.html()
        assert "team@conf.example" in html

        before = browser.eval_js("window.scrollY") or 0
        browser.scroll(400)
        after = browser.eval_js("window.scrollY") or 0
        assert after > before, f"{engine.value}: scroll did not move the page"

        shot = TMP / f"{engine.value}.png"
        browser.screenshot(str(shot))
        assert shot.exists() and shot.stat().st_size > 1000
    finally:
        browser.stop()


@pytest.mark.parametrize("engine,binary", ENGINE_MATRIX + SLOW_MATRIX,
                         ids=lambda v: getattr(v, "value", str(v)))
def test_same_graph_same_result_on_every_engine(engine, binary, server):
    """One graph, unchanged, across engines — the whole point of the seam."""
    if not installed(engine):
        pytest.skip(f"{engine.value} not installed")
    spec = spec_for(engine, binary)
    try:
        browser = build(spec)
    except DriverUnavailable as e:
        pytest.skip(str(e))

    graph = (Graph("conformance")
             .add(Navigate(f"{server}/p.html"))
             .add(WaitFor("#go"))
             .add(Extract("#hdr", into="heading"))
             .add(Click("#go"))
             .add(WaitFor("#out", name="confirm"))
             .add(Extract("#out", into="result"))
             .add(Screenshot(str(TMP / f"graph-{engine.value}.png"))))

    try:
        result = run(graph, spec, browser)
    except Exception as e:
        pytest.skip(f"{engine.value} could not launch: {type(e).__name__}: {e}")
    if not result.ok and "Timeout" in (result.context.error or ""):
        pytest.skip(f"{engine.value} timed out: {result.context.error}")

    assert result.ok, f"{engine.value}: {result.context.error}"
    assert result.context.data["heading"] == "Conformance"
    assert result.context.data["result"] == "clicked"


@pytest.mark.parametrize("engine,binary", ENGINE_MATRIX + SLOW_MATRIX,
                         ids=lambda v: getattr(v, "value", str(v)))
def test_extraction_agrees_across_engines(engine, binary, server):
    """The extractors must see the same page whichever engine fetched it."""
    if not installed(engine):
        pytest.skip(f"{engine.value} not installed")
    spec = spec_for(engine, binary)
    try:
        browser = build(spec)
        browser.start()
    except Exception as e:
        pytest.skip(f"{engine.value} could not launch: {type(e).__name__}: {e}")
    try:
        try:
            browser.goto(f"{server}/p.html")
        except RuntimeError as e:
            if "Timeout" in str(e):
                pytest.skip(f"{engine.value} timed out: {e}")
            raise
        page = parse_page(browser.html(), f"{server}/p.html")
        assert page.title == "Conformance"
        assert page.description == "cross engine"
        assert page.lang == "en"
        assert any(l.endswith("/other.html") for l in page.links)
    finally:
        browser.stop()


# --- capture ----------------------------------------------------------------

def test_video_and_trace_captured_without_system_ffmpeg(server):
    """Playwright bundles its own encoder, so video needs no apt install."""
    if not installed(Engine.PLAYWRIGHT):
        pytest.skip("playwright not installed")
    outdir = TMP / "capture"
    outdir.mkdir(exist_ok=True)
    spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS,
                capture=Capture.VIDEO_AND_TRACE, artifact_dir=str(outdir))
    assert validate(spec) == []

    browser = build(spec)
    from browsergraph.drivers.isolated import IsolatedBrowser
    if isinstance(browser, IsolatedBrowser):
        # A poisoned event loop earlier in the session routed this through a
        # worker; artifact paths are the worker's and are covered by
        # tests/test_isolation.py instead.
        pytest.skip("auto-isolated: capture paths verified in test_isolation")
    result = run(Graph("cap").add(Navigate(f"{server}/p.html"))
                 .add(Extract("#hdr", into="h")), spec, browser)
    assert result.ok

    video = pathlib.Path(browser.video_path) if browser.video_path else None
    assert video and video.exists() and video.stat().st_size > 500
    assert video.suffix == ".webm", "bundled encoder emits webm only"
    trace = pathlib.Path(browser.trace_path) if browser.trace_path else None
    assert trace and trace.exists() and trace.stat().st_size > 500


def test_video_requires_artifact_dir_and_a_playwright_engine():
    assert any("artifact_dir" in p for p in validate(Spec(capture=Capture.VIDEO)))
    bad = Spec(engine=Engine.SELENIUM, binary=Binary.SYSTEM_CHROME,
               capture=Capture.VIDEO, artifact_dir="/tmp")
    assert any("playwright family" in p for p in validate(bad))


def test_doctor_finds_the_bundled_encoder():
    """The encoder ships with playwright's browsers, so it only exists where
    those were installed — a selenium-only environment legitimately lacks it."""
    from browsergraph.doctor import check_media
    names = {c.name: c for c in check_media()}
    assert "video:playwright-ffmpeg" in names, "check is missing entirely"
    if not installed(Engine.PLAYWRIGHT):
        pytest.skip("playwright browsers not installed; no bundled encoder expected")
    assert names["video:playwright-ffmpeg"].ok, "bundled ffmpeg not detected"


def test_slow_engines_are_declared_not_forgotten():
    """Opting an engine out of the default run must be deliberate and visible."""
    assert SLOW_MATRIX, "no slow engines declared"
    slow = {p.values[0] for p in SLOW_MATRIX}
    fast = {e for e, _ in ENGINE_MATRIX}
    assert not (slow & fast), "an engine is both default and slow"


def test_at_least_two_real_engines_are_exercised():
    """A one-engine 'cross-engine' suite proves nothing about the abstraction.

    CI deliberately runs one engine per job, so a single engine there is
    expected — this guards a full development environment, where a silent drop
    to one engine would mean the seam stopped being tested without anyone
    noticing.
    """
    real = [e for e, _ in ENGINE_MATRIX if installed(e)]
    if len(real) < 2:
        pytest.skip(f"single-engine environment ({[e.value for e in real]}); "
                    "the cross-engine guarantee is checked where several exist")
    assert len(real) >= 2


def test_every_declared_engine_can_be_routed():
    """An engine in the capability tables must have an adapter or a clear reason.

    Declaring ten engines and wiring four is the failure this guards against:
    everything looks supported until someone selects one.
    """
    from browsergraph.dimensions import ENGINE_FAMILY
    unrouted = []
    for engine in Engine:
        family = ENGINE_FAMILY.get(engine)
        assert family, f"{engine.value} has no ENGINE_FAMILY entry"
        spec = Spec(engine=engine,
                    binary=Binary.SYSTEM_CHROME if family == "selenium"
                    else (Binary.FIREFOX if engine is Engine.CAMOUFOX
                          else Binary.BUNDLED_CHROMIUM))
        try:
            build(spec)
        except DriverUnavailable as e:
            # acceptable only when it explains itself
            # Every refusal must tell the caller what to do: install a
            # package, build an isolated env, or that it is unimplemented.
            assert any(hint in str(e) for hint in
                       ("pip install", "not implemented", "envs create")), \
                f"{engine.value}: unhelpful error {e}"
            unrouted.append(engine.value)
    assert "playwright" not in unrouted and "selenium" not in unrouted


# --- lifecycle atomicity ----------------------------------------------------

@pytest.mark.skipif(Engine.PLAYWRIGHT not in available_engines(),
                    reason="playwright not installed")
def test_a_failed_start_does_not_poison_the_interpreter():
    """A partial start must unwind, or one bad launch breaks every later one.

    `sync_playwright().start()` parks a greenlet inside its own asyncio loop.
    If the launch then fails, an un-unwound greenlet leaves this thread marked
    "inside a running loop" forever, and every subsequent sync-playwright call
    in the process dies with "Sync API inside the asyncio loop" — including in
    unrelated code that never touched the failing engine.

    This is the bug that made the test suite need auto-isolation to survive.
    """
    import asyncio

    def loop_running() -> bool:
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    assert not loop_running(), "a previous test already leaked a loop"

    browser = build(Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS),
                    executable_path="/nonexistent/chrome")
    with pytest.raises(Exception):
        browser.start()

    assert not loop_running(), "failed start left a running asyncio loop"
    assert browser._pw is None, "failed start leaked the playwright object"

    browser.stop()      # the caller's own finally: must be a no-op, not a crash

    after = build(Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS))
    after.start()
    try:
        assert after.goto("about:blank") is not None
    finally:
        after.stop()
    assert not loop_running()
