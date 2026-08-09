"""Run the 100-task corpus against a real chromium.

One browser instance is shared across all scenarios; each page is written to a
temp file and loaded over `file://`. This is the suite that exercises the real
driver rather than the mock — if the Playwright adapter regresses, it fails
here first.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from browsergraph import Engine, Graph, Spec, run
from browsergraph.dimensions import Display
from browsergraph.drivers import DriverUnavailable, build
from browsergraph.errors import classify
from browsergraph.extract.content import parse_page
from browsergraph.extract.patterns import extract_contacts
from browsergraph.focus import chunk, focus
from browsergraph.nodes.actions import Click, Navigate
from browsergraph.preprocess import Preprocess, reduce
from browsergraph.tasks import make as make_task
from tests.corpus import CORPUS, by_category

# Disk-backed, not /tmp: on this host /tmp is a RAM tmpfs, so browser
# downloads, videos and screenshots consume memory and hit its quota.
TMP = pathlib.Path(__file__).resolve().parent.parent / ".artifacts" / "bg_corpus"
TMP.mkdir(parents=True, exist_ok=True)


@pytest.fixture(scope="module")
def server():
    """Serve the corpus over real HTTP.

    `file://` was the obvious shortcut and the wrong one: it has no origin,
    resolves root-relative links against the filesystem root, and is rejected
    by the url validator. Serving over HTTP exercises the same code paths a
    real site does.
    """
    import functools
    import http.server
    import socketserver
    import threading
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(TMP))
    handler.log_message = lambda *a, **k: None
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


# Module scope, not session: a sync Playwright instance held open across
# modules makes later `sync_playwright().start()` calls fail with
# "Sync API inside the asyncio loop". The browser must close with the
# module that opened it.
@pytest.fixture(scope="module")
def browser():
    spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS)
    try:
        b = build(spec)
        b.start()
    except (DriverUnavailable, Exception) as e:  # pragma: no cover
        pytest.skip(f"real browser unavailable: {type(e).__name__}: {e}")
    yield b
    b.stop()


def url_for(scenario, base: str) -> str:
    (TMP / f"{scenario.id}.html").write_text(scenario.html, encoding="utf-8")
    return f"{base}/{scenario.id}.html"


def run_scenario(scenario, browser, base: str) -> dict:
    """Execute one scenario and return a flat result dict for its check."""
    kind, p = scenario.kind, scenario.params

    if kind == "classify_error":
        return {"failure": classify(p["error"]).failure.value}

    if kind in ("preprocess", "focus", "chunk"):
        html = scenario.html
        if kind == "preprocess":
            return reduce(html, Preprocess(p["strategy"])).to_dict() | {
                "content": reduce(html, Preprocess(p["strategy"])).content}
        text = reduce(html, Preprocess.MARKDOWN).content
        if kind == "chunk":
            return {"chunks": len(chunk(text, max_chars=800))}
        f = focus(text, p["query"], budget=p.get("budget", 800))
        return f.to_dict() | {"content": f.content}

    url = url_for(scenario, base)

    if kind == "task":
        task = make_task(p["task"], url=url, delay=0, max_pages=3, respect_robots=False)
        return task.run(browser).to_dict()

    browser.goto(url)
    html = browser.html()

    if kind == "classify_page":
        return {"failure": classify("element not found: #x", html).failure.value}
    if kind == "extract":
        pg = parse_page(html, url)
        return {"contacts": extract_contacts(pg.text, pg.links, pg.mailtos).to_dict()}
    if kind == "page":
        pg = parse_page(html, url)
        return pg.to_dict() | {"text": pg.text, "links": pg.links,
                               "structured": pg.structured,
                               "headings": pg.headings}
    if kind == "wait":
        return {"found": browser.wait_for(p["selector"], timeout=1.5)}
    if kind == "scroll":
        before = browser.eval_js("window.scrollY")
        browser.scroll(500)
        return {"scrolled": (browser.eval_js("window.scrollY") or 0) - (before or 0)}
    if kind == "screenshot":
        shot = TMP / f"{scenario.id}.png"
        browser.screenshot(str(shot))
        return {"bytes": shot.stat().st_size if shot.exists() else 0}
    if kind == "optional_click":
        g = Graph(scenario.id).add(Navigate(url)).add(
            Click(p["selector"], optional=True))
        return {"ok": run(g, Spec(engine=Engine.PLAYWRIGHT), browser_noop(browser)).ok}
    if kind in ("interact", "type"):
        if kind == "type":
            browser.type(p["selector"], p["text"])
        else:
            el = browser.find(p["selector"])
            if el is None:
                return {"failed": True, "page_text": html}
            browser.click(p["selector"])
        browser.wait_for(p.get("read", "body"), timeout=1.5)
        return {"out": browser.text_of(p["read"]) if p.get("read") else "",
                "failed": False}
    raise AssertionError(f"unknown scenario kind: {kind}")


class browser_noop:
    """Wrap the shared browser so graph.run()'s start/stop does not close it."""
    def __init__(self, inner): self._i = inner
    def start(self): pass
    def stop(self): pass
    def __getattr__(self, name): return getattr(self._i, name)


# --- the corpus -------------------------------------------------------------

def test_corpus_is_the_advertised_size():
    assert len(CORPUS) == 100, f"corpus has {len(CORPUS)} scenarios"
    assert len({s.id for s in CORPUS}) == 100, "duplicate scenario ids"


def test_corpus_covers_every_category():
    counts = by_category()
    assert set(counts) == {"contacts", "extract", "interact", "task",
                           "preprocess", "fail"}
    assert min(counts.values()) >= 8, counts


@pytest.mark.parametrize("scenario", CORPUS, ids=lambda s: s.id)
def test_scenario(scenario, browser, server):
    result = run_scenario(scenario, browser, server)
    ok = scenario.check(result)
    detail = json.dumps(result, default=str)[:400]
    assert ok, (f"[{scenario.category}] {scenario.title}\n"
                f"  why it matters: {scenario.why or 'correctness'}\n"
                f"  got: {detail}")
