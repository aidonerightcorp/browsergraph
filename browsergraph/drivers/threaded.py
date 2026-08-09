"""Run a sync-API driver in a worker thread, for callers that live in a loop.

Playwright's sync API refuses to start inside a running asyncio loop, and the
loop cannot be swapped out — a running loop is not replaceable. That is not an
edge case: **Jupyter, IPython, Kaggle and Colab run every cell inside a loop**,
as does any caller in async code. Without a way around it, `engine=playwright`
simply does not work in a notebook, which is where a great many people first
try a library like this.

The existing answer was `IsolatedBrowser` — a separate interpreter in a
per-engine virtualenv. That is the right tool when engines genuinely conflict
(camoufox pins its own playwright build), but it is far too heavy here: it
needs a venv built ahead of time, which a fresh Kaggle kernel does not have,
so the notebook fails with an instruction the reader cannot act on.

The observation that makes this simple: the sync API objects to a loop *in the
calling thread*. A plain worker thread has none. So the driver is constructed
and driven entirely inside one dedicated thread, and every call is marshalled
onto it — no subprocess, no venv, no setup.

A single-worker ThreadPoolExecutor is the whole mechanism: `max_workers=1`
guarantees every submitted call runs on the same thread, which is exactly the
thread-confinement the sync API requires. Exceptions surface through
`Future.result()` unchanged, so callers see the same errors they always did.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from browsergraph.dimensions import Spec
from browsergraph.ports import Element, PageState


class ThreadedBrowser:
    """A BrowserPort whose real driver lives in a dedicated worker thread."""

    def __init__(self, spec: Spec, **kwargs: Any) -> None:
        self.spec = spec
        self._kwargs = kwargs
        self._inner: Any = None
        self._stopped = False
        # max_workers=1 is load-bearing: the sync API is thread-confined, so
        # every call must land on the same thread that created the driver.
        self._pool = ThreadPoolExecutor(max_workers=1,
                                        thread_name_prefix="browsergraph-driver")

    def _submit(self, fn, *args, **kwargs):
        return self._pool.submit(fn, *args, **kwargs).result()

    # lifecycle --------------------------------------------------------------
    def start(self) -> None:
        def _build_and_start():
            from browsergraph.drivers.playwright_driver import PlaywrightBrowser
            self._inner = PlaywrightBrowser(self.spec, **self._kwargs)
            self._inner.start()

        self._submit(_build_and_start)

    def stop(self) -> None:
        """Idempotent: `run()` stops the browser in a finally, and callers
        routinely stop it again in one of their own. Submitting to an
        already-shut-down pool raises, so a second stop must be a no-op rather
        than a crash during cleanup — the worst possible time for one."""
        if self._stopped:
            return
        self._stopped = True
        try:
            if self._inner is not None:
                self._submit(self._inner.stop)
        finally:
            # Shut the pool down after the driver, never before: the driver's
            # own teardown has to run on the thread that owns it.
            self._pool.shutdown(wait=True)

    # navigation -------------------------------------------------------------
    def goto(self, url: str) -> PageState:
        return self._submit(lambda: self._inner.goto(url))

    def state(self) -> PageState:
        return self._submit(lambda: self._inner.state())

    # elements ---------------------------------------------------------------
    def find(self, selector: str) -> Element | None:
        return self._submit(lambda: self._inner.find(selector))

    def click(self, selector: str) -> None:
        self._submit(lambda: self._inner.click(selector))

    def type(self, selector: str, text: str, cps: float = 0.0) -> None:
        self._submit(lambda: self._inner.type(selector, text, cps))

    def scroll(self, dy: int) -> None:
        self._submit(lambda: self._inner.scroll(dy))

    def wait_for(self, selector: str, timeout: float = 10.0) -> bool:
        return self._submit(lambda: self._inner.wait_for(selector, timeout))

    def text_of(self, selector: str) -> str:
        return self._submit(lambda: self._inner.text_of(selector))

    def html(self) -> str:
        return self._submit(lambda: self._inner.html())

    def screenshot(self, path: str) -> str:
        return self._submit(lambda: self._inner.screenshot(path))

    def eval_js(self, script: str) -> Any:
        return self._submit(lambda: self._inner.eval_js(script))

    # artifacts --------------------------------------------------------------
    # Read after stop(), so they must survive the pool being shut down; the
    # inner driver object outlives its thread, only its methods are confined.
    @property
    def video_path(self) -> str:
        return getattr(self._inner, "video_path", "")

    @property
    def trace_path(self) -> str:
        return getattr(self._inner, "trace_path", "")

    @property
    def supports_javascript(self) -> bool:
        return True
