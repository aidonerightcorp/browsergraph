"""Running inside an asyncio loop — i.e. inside a notebook.

Jupyter, IPython, Kaggle and Colab execute every cell inside a running asyncio
loop, and Playwright's sync API refuses to start in one. That is not an exotic
configuration; it is where a large share of users first try a library like
this, and until this worked `engine=playwright` was simply unusable there.

The fix is a worker *thread*: the sync API objects to a loop in the calling
thread, and a fresh thread has none. No subprocess and no prebuilt virtualenv,
which matters because a fresh Kaggle kernel has neither.
"""
from __future__ import annotations

import asyncio
import functools
import http.server
import pathlib
import socketserver
import threading

import pytest

from browsergraph import Engine, Graph, Spec, run
from browsergraph.dimensions import Capture, Display
from browsergraph.doctor import available_engines
from browsergraph.drivers import build
from browsergraph.drivers.threaded import ThreadedBrowser
from browsergraph.nodes.actions import Click, Extract, Navigate, Screenshot, WaitFor

TMP = pathlib.Path(__file__).resolve().parent.parent / ".artifacts" / "bg_threaded"
TMP.mkdir(parents=True, exist_ok=True)

PAGE = ("<!doctype html><html lang=en><head><title>Threaded</title></head><body>"
        "<h1 id=h>In A Loop</h1><button id=go>Go</button><div id=out></div>"
        "<script>go.onclick=()=>out.textContent='clicked'</script></body></html>")

needs_pw = pytest.mark.skipif(Engine.PLAYWRIGHT not in available_engines(),
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


def in_a_loop(fn):
    """Run `fn` the way a notebook cell runs: inside a live asyncio loop."""
    async def main():
        assert asyncio.get_running_loop() is not None
        return fn()
    return asyncio.run(main())


# --- routing ----------------------------------------------------------------

def test_a_running_loop_routes_to_a_thread_not_a_subprocess():
    """The notebook case must not require a prebuilt virtualenv."""
    driver = in_a_loop(lambda: build(Spec(engine=Engine.PLAYWRIGHT,
                                          display=Display.HEADLESS)))
    assert isinstance(driver, ThreadedBrowser)


def test_without_a_loop_the_driver_stays_in_process():
    """A process hop is not free; it is only paid when it is needed."""
    driver = build(Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS))
    assert not isinstance(driver, ThreadedBrowser)


def test_explicit_isolation_still_wins_over_the_thread():
    """A thread shares the interpreter, so it cannot resolve a version conflict."""
    from browsergraph.drivers.isolated import IsolatedBrowser
    driver = in_a_loop(lambda: build(Spec(engine=Engine.PLAYWRIGHT, isolated=True)))
    assert isinstance(driver, IsolatedBrowser)


def test_threaded_browser_satisfies_the_port():
    from browsergraph.ports import BrowserPort
    assert isinstance(ThreadedBrowser(Spec(engine=Engine.PLAYWRIGHT)), BrowserPort)


# --- live -------------------------------------------------------------------

@needs_pw
def test_a_whole_graph_runs_from_inside_a_loop(server):
    """The end-to-end notebook scenario, including a verified mutation."""
    def go():
        spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS)
        graph = (Graph("nb").add(Navigate(f"{server}/p.html")).add(WaitFor("#go"))
                 .add(Extract("#h", into="heading")).add(Click("#go"))
                 .add(WaitFor("#out", name="confirm"))
                 .add(Extract("#out", into="result")))
        return run(graph, spec, build(spec))

    result = in_a_loop(go)
    assert result.ok, result.context.error
    assert result.context.data["heading"] == "In A Loop"
    assert result.context.data["result"] == "clicked"


@needs_pw
def test_screenshots_survive_the_thread_hop(server):
    shot = TMP / "threaded.png"

    def go():
        spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS)
        graph = (Graph("shot").add(Navigate(f"{server}/p.html"))
                 .add(Screenshot(str(shot))))
        return run(graph, spec, build(spec))

    result = in_a_loop(go)
    assert result.ok, result.context.error
    assert shot.exists() and shot.stat().st_size > 1000


@needs_pw
def test_video_path_is_reported_back(server):
    """Regression: the notebook reported a successful run and no video."""
    vdir = TMP / "vid"

    def go():
        spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS,
                    capture=Capture.VIDEO, artifact_dir=str(vdir))
        browser = build(spec)
        graph = Graph("vid").add(Navigate(f"{server}/p.html"))
        return run(graph, spec, browser), browser

    result, browser = in_a_loop(go)
    assert result.ok, result.context.error
    path = browser.video_path
    assert path, "a video was recorded but no path was reported"
    assert pathlib.Path(path).exists()


@needs_pw
def test_errors_from_the_thread_reach_the_caller_intact(server):
    """A marshalled call must not swallow or rewrap the failure."""
    def go():
        spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS)
        browser = build(spec)
        browser.start()
        try:
            browser.goto(f"{server}/p.html")
            with pytest.raises(Exception, match="(?i)syntax|error"):
                browser.eval_js("this is not valid javascript ((")
        finally:
            browser.stop()
        return True

    assert in_a_loop(go)


@needs_pw
def test_stop_is_safe_to_call_twice(server):
    def go():
        spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS)
        browser = build(spec)
        browser.start()
        browser.goto(f"{server}/p.html")
        browser.stop()
        browser.stop()          # a caller's finally, after an explicit stop
        return True

    assert in_a_loop(go)


# --- container launch flags -------------------------------------------------

def test_container_args_are_the_ones_chrome_cannot_start_without():
    from browsergraph.drivers.playwright_driver import container_args
    assert "--no-sandbox" in container_args()
    assert "--disable-dev-shm-usage" in container_args()


def test_container_detection_is_boolean():
    from browsergraph.drivers.playwright_driver import in_container
    assert isinstance(in_container(), bool)


@needs_pw
def test_explicit_launch_args_reach_the_browser(server):
    """Kaggle installs chromium fine and then cannot launch it without these."""
    spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS,
                extra={"container_args": True,
                       "launch_args": ["--window-size=900,700"]})
    browser = build(spec)
    browser.start()
    try:
        assert browser.goto(f"{server}/p.html").title == "Threaded"
    finally:
        browser.stop()


@needs_pw
def test_container_args_can_be_switched_off(server):
    """A caller who knows the sandbox works must be able to keep it."""
    spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS,
                extra={"container_args": False})
    browser = build(spec)
    browser.start()
    try:
        assert browser.goto(f"{server}/p.html").title == "Threaded"
    finally:
        browser.stop()


# --- bootstrap --------------------------------------------------------------

def test_missing_library_is_parsed_from_a_real_launch_log():
    """The Kaggle failure this module exists for."""
    from browsergraph.bootstrap import missing_library
    log = ("chrome-headless-shell: error while loading shared libraries: "
           "libatk-1.0.so.0: cannot open shared object file: No such file")
    assert missing_library(log) == "libatk-1.0.so.0"
    assert missing_library("TargetClosedError: browser has been closed") == ""
    assert missing_library("") == ""


def test_bootstrap_short_circuits_or_reports_honestly():
    """Both outcomes are correct; which one depends on the machine.

    Written this way because the first version assumed a browser exists and
    passed locally while failing on a clean CI runner that has no playwright —
    where walking every fallback and reporting the browser-less route is exactly
    the right behaviour.
    """
    from browsergraph.bootstrap import ensure_browser
    rep = ensure_browser(install=False, apt=False)

    if rep.ok:
        assert rep.steps[0].ok, "a working browser must be found by the first probe"
        assert len(rep.steps) <= 3, "a working browser should not walk the fallbacks"
    else:
        assert len(rep.steps) > 1, "a failure should show what was tried"
        assert "engine=http" in rep.text(), "should name the route that needs no browser"
        assert not any(s.name.startswith("pip install") for s in rep.steps), \
            "install=False must not install anything"


def test_bootstrap_never_installs_when_asked_not_to(monkeypatch):
    import browsergraph.bootstrap as bs
    calls = []
    monkeypatch.setattr(bs, "_run", lambda cmd, timeout=900: calls.append(cmd) or (0, ""))
    monkeypatch.setattr(bs, "launches", lambda *a, **k: (False, "TargetClosedError: x"))
    rep = bs.ensure_browser(install=False, apt=False)
    assert calls == [], f"ran commands despite install=False: {calls}"
    assert not rep.ok


def test_bootstrap_reports_the_browser_less_route_when_all_else_fails(monkeypatch):
    """engine=http needs none of this, and the report should say so."""
    import browsergraph.bootstrap as bs
    monkeypatch.setattr(bs, "launches", lambda *a, **k: (False, "TargetClosedError: x"))
    monkeypatch.setattr(bs, "shutil", type("S", (), {"which": staticmethod(lambda n: None)}))
    rep = bs.ensure_browser(install=False, apt=False)
    assert "engine=http" in rep.text()
    assert "apt-get install" in rep.text(), "should print an actionable command"


def test_bootstrap_uses_a_system_browser_when_the_bundled_one_fails(monkeypatch):
    import browsergraph.bootstrap as bs
    monkeypatch.setattr(bs, "shutil",
                        type("S", (), {"which": staticmethod(
                            lambda n: "/usr/bin/google-chrome" if n == "google-chrome" else None)}))
    monkeypatch.setattr(bs, "launches",
                        lambda engine=None, executable_path="": (bool(executable_path), "no"))
    rep = bs.ensure_browser(install=False, apt=False)
    assert rep.ok and rep.executable_path == "/usr/bin/google-chrome"
