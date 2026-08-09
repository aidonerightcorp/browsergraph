"""Engine isolation — conflicting engines in separate environments.

Camoufox pins its own Playwright build, so co-installing it breaks the shared
playwright and patchright adapters. The fix is per-family virtualenvs with the
adapter running in a worker process, not dropping the engine.

Tests that need a built environment skip when it is absent, so the suite runs
on a fresh checkout; `browsergraph envs create <name>` builds them.
"""
from __future__ import annotations

import functools
import http.server
import pathlib
import socketserver
import threading

import pytest

from browsergraph import Engine, Graph, Spec, run
from browsergraph.dimensions import Binary, Display
from browsergraph.drivers import build
from browsergraph.drivers.isolated import IsolatedBrowser
from browsergraph.drivers.mock import MockBrowser
from browsergraph.isolate import (
    DEFAULT_ROOT,
    ISOLATED_FAMILIES,
    Env,
    IsolationError,
    Worker,
    decode,
    encode,
    env_for,
)
from browsergraph.nodes.actions import Click, Extract, Navigate, WaitFor

# Disk-backed, not /tmp: on this host /tmp is a RAM tmpfs, so browser
# downloads, videos and screenshots consume memory and hit its quota.
TMP = pathlib.Path(__file__).resolve().parent.parent / ".artifacts" / "bg_isolation"
TMP.mkdir(parents=True, exist_ok=True)

PAGE = ("<!doctype html><html lang=en><head><title>Isolated</title></head><body>"
        "<h1 id=h>Isolated Engine</h1><button id=go>Go</button><div id=out></div>"
        "<script>go.onclick=()=>out.textContent='clicked'</script></body></html>")


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


def have(name: str) -> bool:
    return Env(name=name).exists


# --- routing ----------------------------------------------------------------

def test_engines_map_to_environments():
    assert env_for(Engine.CAMOUFOX).name == "camoufox"
    assert env_for(Engine.PLAYWRIGHT).name == "playwright"
    assert env_for(Engine.SELENIUM_UC).name == "selenium"


def test_camoufox_gets_its_own_env_not_the_shared_playwright_one():
    """The whole reason isolation exists: camoufox must not share playwright's env."""
    assert env_for(Engine.CAMOUFOX).name != env_for(Engine.PLAYWRIGHT).name


def test_isolated_spec_builds_a_worker_backed_driver():
    spec = Spec(engine=Engine.PLAYWRIGHT, isolated=True)
    assert isinstance(build(spec), IsolatedBrowser)


def test_isolation_is_opt_in():
    """In-process stays the default; a process hop is not free."""
    assert not isinstance(build(Spec(engine=Engine.MOCK)), IsolatedBrowser)
    assert isinstance(build(Spec(engine=Engine.MOCK, isolated=True)), MockBrowser), \
        "mock never needs isolating"


def test_isolated_browser_satisfies_the_port():
    from browsergraph.ports import BrowserPort
    assert isinstance(IsolatedBrowser(Spec(engine=Engine.PLAYWRIGHT)), BrowserPort)


def test_child_spec_disables_isolation_to_avoid_recursion():
    b = IsolatedBrowser(Spec(engine=Engine.PLAYWRIGHT, isolated=True))
    assert b._worker.spec_dict["isolated"] is False, \
        "the worker would spawn another worker"


# --- protocol ---------------------------------------------------------------

def test_protocol_round_trips():
    assert decode(encode({"op": "ping", "n": 1})) == {"op": "ping", "n": 1}


def test_missing_env_reports_how_to_create_it():
    worker = Worker(Env(name="definitely-not-built", root=DEFAULT_ROOT), {})
    with pytest.raises(IsolationError, match="envs create"):
        worker.start()


def test_env_reports_its_state():
    rep = Env(name="playwright").report()
    assert set(rep) == {"name", "path", "exists", "python"}


def test_every_isolatable_family_declares_packages():
    for name, pkgs in ISOLATED_FAMILIES.items():
        assert pkgs, f"{name} declares no packages to install"


# --- live -------------------------------------------------------------------

@pytest.mark.skipif(not have("playwright"), reason="playwright env not built")
def test_graph_runs_through_an_isolated_worker(server):
    """Same graph, same assertions — but executing in another interpreter."""
    spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS, isolated=True)
    graph = (Graph("iso").add(Navigate(f"{server}/p.html")).add(WaitFor("#go"))
             .add(Extract("#h", into="heading")).add(Click("#go"))
             .add(WaitFor("#out", name="confirm")).add(Extract("#out", into="result")))
    result = run(graph, spec, build(spec))
    assert result.ok, result.context.error
    assert result.context.data["heading"] == "Isolated Engine"
    assert result.context.data["result"] == "clicked"


@pytest.mark.skipif(not have("playwright"), reason="playwright env not built")
def test_worker_errors_surface_as_exceptions_not_silence(server):
    spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS, isolated=True)
    b = build(spec)
    b.start()
    try:
        b.goto(f"{server}/p.html")
        with pytest.raises(RuntimeError, match="failed in playwright"):
            b.eval_js("this is not valid javascript ((")
    finally:
        b.stop()


@pytest.mark.skipif(not have("camoufox"), reason="camoufox env not built")
def test_camoufox_runs_isolated(server):
    spec = Spec(engine=Engine.CAMOUFOX, binary=Binary.FIREFOX,
                display=Display.HEADLESS, isolated=True)
    b = build(spec)
    b.start()
    try:
        assert b.goto(f"{server}/p.html").title == "Isolated"
        assert "Isolated Engine" in b.text_of("#h")
    finally:
        b.stop()


@pytest.mark.skipif(not (have("camoufox") and have("playwright")),
                    reason="both envs required")
def test_conflicting_engines_coexist(server):
    """Camoufox and playwright, in one process, without breaking each other.

    Co-installing them makes the last one win; isolation is what makes this
    assertion possible at all.
    """
    camo = build(Spec(engine=Engine.CAMOUFOX, binary=Binary.FIREFOX,
                      display=Display.HEADLESS, isolated=True))
    camo.start()
    try:
        assert camo.goto(f"{server}/p.html").title == "Isolated"
    finally:
        camo.stop()

    direct = build(Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS))
    direct.start()
    try:
        assert direct.goto(f"{server}/p.html").title == "Isolated", \
            "camoufox broke the in-process playwright adapter"
    finally:
        direct.stop()
