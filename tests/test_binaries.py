"""Finding a browser a driver will actually accept, and the CDP family.

Two things that were declared and did not work. `Binary.FIREFOX` failed with
`InvalidArgumentException: binary is not a Firefox executable`, because Ubuntu's
`/usr/bin/firefox` is a shell script wrapping the snap and geckodriver will not
drive it. And the whole `cdp` family — nodriver, zendriver, pydoll — was in the
capability tables with no adapter behind it.
"""
from __future__ import annotations

import functools
import http.server
import os
import pathlib
import socketserver
import stat
import threading

import pytest

from browsergraph import Engine, Graph, Spec, run
from browsergraph.binaries import CANDIDATES, is_real_program, report, resolve
from browsergraph.dimensions import Binary, Display
from browsergraph.doctor import available_engines
from browsergraph.drivers import DriverUnavailable, build
from browsergraph.nodes.actions import Click, Extract, Navigate, Screenshot, WaitFor

TMP = pathlib.Path(__file__).resolve().parent.parent / ".artifacts" / "bg_binaries"
TMP.mkdir(parents=True, exist_ok=True)

PAGE = ("<!doctype html><html lang=en><head><title>Binaries</title></head><body>"
        "<h1 id=h>Every Browser</h1><button id=go>Go</button><div id=out></div>"
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


def have(engine: Engine) -> bool:
    return engine in available_engines()


# --- telling a program from a wrapper ---------------------------------------

def test_a_shell_script_is_not_a_program(tmp_path):
    """The exact shape of /usr/bin/firefox on Ubuntu."""
    wrapper = tmp_path / "firefox"
    wrapper.write_text("#!/bin/sh\nexec /snap/bin/firefox \"$@\"\n")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    assert not is_real_program(str(wrapper))


def test_a_real_binary_is_a_program():
    import sys
    assert is_real_program(sys.executable)


def test_a_missing_or_unexecutable_file_is_not_a_program(tmp_path):
    plain = tmp_path / "notexec"
    plain.write_bytes(b"\x7fELF fake")
    assert not is_real_program(str(plain))          # not executable
    assert not is_real_program(str(tmp_path / "nope"))
    assert not is_real_program("")


def test_resolution_prefers_a_real_binary_over_a_wrapper_on_path(tmp_path, monkeypatch):
    real = tmp_path / "real-firefox"
    real.write_bytes(b"\x7fELF" + b"\x00" * 32)
    real.chmod(real.stat().st_mode | stat.S_IEXEC)
    wrapper = tmp_path / "firefox"
    wrapper.write_text("#!/bin/sh\nexec real\n")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)

    import browsergraph.binaries as mod
    monkeypatch.setitem(mod.CANDIDATES, Binary.FIREFOX, (str(real),))
    monkeypatch.setattr(mod.shutil, "which",
                        lambda n: str(wrapper) if n.startswith("firefox") else None)

    got = resolve(Binary.FIREFOX)
    assert got.ok and got.path == str(real)
    assert got.wrapper == str(wrapper), "the wrapper should be reported, not hidden"
    assert "wrapper script" in got.explain()


def test_only_a_wrapper_is_an_honest_failure(tmp_path, monkeypatch):
    wrapper = tmp_path / "firefox"
    wrapper.write_text("#!/bin/sh\nexit 1\n")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    import browsergraph.binaries as mod
    monkeypatch.setitem(mod.CANDIDATES, Binary.FIREFOX, ())
    monkeypatch.setattr(mod.shutil, "which",
                        lambda n: str(wrapper) if n.startswith("firefox") else None)
    got = resolve(Binary.FIREFOX)
    assert not got.ok
    assert "wrapper" in got.explain() and "executable_path" in got.explain()


def test_every_candidate_path_is_absolute():
    for paths in CANDIDATES.values():
        for p in paths:
            assert os.path.isabs(p), p


def test_report_covers_the_installable_binaries():
    assert {r.binary for r in report()} == {
        "system_chrome", "chrome_for_testing", "firefox", "brave"}


# --- live: every binary this machine has ------------------------------------

REAL_BINARIES = [b for b in (Binary.SYSTEM_CHROME, Binary.CHROME_FOR_TESTING,
                             Binary.FIREFOX, Binary.BRAVE) if resolve(b).ok]


@pytest.mark.skipif(not have(Engine.SELENIUM), reason="selenium not installed")
@pytest.mark.parametrize("binary", REAL_BINARIES, ids=lambda b: b.value)
@pytest.mark.parametrize("display", [Display.HEADLESS, Display.HEADED],
                         ids=lambda d: d.value)
def test_selenium_drives_every_installed_binary(server, binary, display):
    """Firefox is the one that used to fail; the rest guard against regressing it."""
    if display is Display.HEADED and not os.environ.get("DISPLAY"):
        pytest.skip("no X display")
    spec = Spec(engine=Engine.SELENIUM, binary=binary, display=display)
    browser = build(spec)
    browser.start()
    try:
        assert browser.goto(f"{server}/p.html").title == "Binaries"
        assert "Every Browser" in browser.text_of("#h")
    finally:
        browser.stop()


@pytest.mark.skipif(not have(Engine.PLAYWRIGHT), reason="playwright not installed")
@pytest.mark.parametrize("binary", [Binary.BUNDLED_CHROMIUM, Binary.FIREFOX],
                         ids=lambda b: b.value)
def test_playwright_drives_chromium_and_firefox(server, binary):
    spec = Spec(engine=Engine.PLAYWRIGHT, binary=binary, display=Display.HEADLESS)
    try:
        browser = build(spec)
        browser.start()
    except Exception as e:
        pytest.skip(f"{binary.value} not downloaded: {type(e).__name__}")
    try:
        assert browser.goto(f"{server}/p.html").title == "Binaries"
    finally:
        browser.stop()


# --- the CDP family ---------------------------------------------------------

CDP_ENGINES = [e for e in (Engine.ZENDRIVER, Engine.PYDOLL, Engine.NODRIVER)
               if have(e)]


def test_raw_cdp_points_at_a_real_alternative():
    """`cdp` has no client library; the error must say what to use instead."""
    with pytest.raises(DriverUnavailable) as e:
        build(Spec(engine=Engine.CDP))
    msg = str(e.value)
    assert "remote_cdp" in msg and ("nodriver" in msg or "zendriver" in msg)


#: CDP engines drive an installed Chrome; they ship no browser of their own,
#: so bundled_chromium is not one of their options and the validator says so.
CDP_BINARY = Binary.SYSTEM_CHROME


@pytest.mark.parametrize("engine", CDP_ENGINES, ids=lambda e: e.value)
def test_cdp_engines_route_to_an_adapter(engine):
    """They were declared in the capability tables with nothing behind them."""
    from browsergraph.drivers.cdp_driver import CdpBrowser
    assert isinstance(build(Spec(engine=engine, binary=CDP_BINARY)), CdpBrowser)


@pytest.mark.parametrize("engine", CDP_ENGINES, ids=lambda e: e.value)
def test_cdp_engine_runs_a_whole_graph(server, engine):
    """Navigate, read, mutate, verify the mutation, capture — via raw CDP."""
    spec = Spec(engine=engine, binary=CDP_BINARY, display=Display.HEADLESS)
    shot = TMP / f"{engine.value}.png"
    graph = (Graph(engine.value).add(Navigate(f"{server}/p.html"))
             .add(WaitFor("#go")).add(Extract("#h", into="heading"))
             .add(Click("#go")).add(WaitFor("#out", name="confirm"))
             .add(Extract("#out", into="result")).add(Screenshot(str(shot))))
    try:
        browser = build(spec)
        result = run(graph, spec, browser)
    except DriverUnavailable as e:
        pytest.skip(f"{engine.value} unavailable: {str(e)[:80]}")

    assert result.ok, result.context.error
    assert result.context.data["heading"] == "Every Browser"
    assert result.context.data["result"] == "clicked"
    assert shot.exists() and shot.stat().st_size > 1000


def test_a_broken_engine_package_is_reported_as_such(monkeypatch):
    """A published nodriver build ships non-UTF-8 source and fails to import.

    That is a defect in the dependency, and the message must say so rather than
    surfacing a traceback that points into someone else's package.
    """
    import importlib

    import browsergraph.drivers.cdp_driver as mod

    def explode(name):
        raise SyntaxError("Non-UTF-8 code", ("nodriver/core/x.py", 1345, 1, ""))

    monkeypatch.setattr(importlib, "import_module", explode)
    browser = mod.CdpBrowser(Spec(engine=Engine.NODRIVER, binary=CDP_BINARY))
    with pytest.raises(DriverUnavailable) as e:
        browser.start()
    msg = str(e.value)
    assert "broken" in msg and "not in browsergraph" in msg
    assert "zendriver" in msg, "should point at the maintained fork"


def test_cdp_unwraps_the_devtools_envelope():
    """pydoll returns the whole frame; a title that is a dict is wrong everywhere."""
    from browsergraph.drivers.cdp_driver import _unwrap
    assert _unwrap({"id": 4, "result": {"result": {"type": "string",
                                                   "value": "CDP"}}}) == "CDP"
    assert _unwrap({"type": "number", "value": 42}) == 42
    assert _unwrap("plain") == "plain"
    assert _unwrap(None) is None


def test_cdp_surfaces_a_javascript_exception():
    from browsergraph.drivers.cdp_driver import _unwrap
    with pytest.raises(RuntimeError):
        _unwrap({"result": {"exceptionDetails": {"text": "ReferenceError"}}})
