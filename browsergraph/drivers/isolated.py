"""A BrowserPort backed by a worker in another virtualenv.

Every method is the same call shape as the in-process adapters, so nodes,
tasks, healing, supervision and the linter cannot tell the difference. That is
the point: isolation is a deployment decision, not an API change.
"""
from __future__ import annotations

from browsergraph.dimensions import Spec
from browsergraph.isolate import Env, IsolationError, Worker, env_for
from browsergraph.ports import Element, PageState


class IsolatedBrowser:
    """Runs the real adapter in `env`, relaying BrowserPort calls to it."""

    def __init__(self, spec: Spec, env: Env | None = None, timeout: float = 120.0):
        self.spec = spec
        self.env = env or env_for(spec.engine)
        # The child must build the adapter directly, or it would recurse into
        # spawning another worker.
        child_spec = spec.to_dict()
        child_spec["isolated"] = False
        self._worker = Worker(self.env, child_spec, timeout=timeout)
        self.video_path = ""
        self.trace_path = ""

    # lifecycle
    def start(self) -> None:
        self._worker.start()

    def stop(self) -> None:
        """Close the remote browser and collect what it produced.

        The close and the artifact query are the same round trip on purpose:
        `video_path` is only populated by the inner `stop()`, so asking before
        the close always returned an empty string, and asking after it is too
        late — the worker has dropped the browser.
        """
        try:
            art = self._worker.call("close").get("result") or {}
            self.video_path = art.get("video_path", "") or ""
            self.trace_path = art.get("trace_path", "") or ""
        except IsolationError:
            pass
        self._worker.stop()

    def _call(self, op: str, **kw):
        reply = self._worker.call(op, **kw)
        if not reply.get("ok"):
            raise RuntimeError(f"{op} failed in {self.env.name}: {reply.get('error')}")
        return reply.get("result")

    # navigation
    def goto(self, url: str) -> PageState:
        r = self._call("goto", url=url) or {}
        return PageState(url=r.get("url", ""), title=r.get("title", ""),
                         status=r.get("status", 0))

    def state(self) -> PageState:
        r = self._call("state") or {}
        return PageState(url=r.get("url", ""), title=r.get("title", ""),
                         status=r.get("status", 0))

    # elements
    def find(self, selector: str) -> Element | None:
        r = self._call("find", selector=selector)
        return None if r is None else Element(selector=r["selector"], text=r.get("text", ""))

    def click(self, selector: str) -> None:
        self._call("click", selector=selector)

    def type(self, selector: str, text: str, cps: float = 0.0) -> None:
        self._call("type", selector=selector, text=text, cps=cps)

    def scroll(self, dy: int) -> None:
        self._call("scroll", dy=dy)

    def wait_for(self, selector: str, timeout: float = 10.0) -> bool:
        return bool(self._call("wait_for", selector=selector, timeout=timeout))

    def text_of(self, selector: str) -> str:
        return self._call("text_of", selector=selector) or ""

    def html(self) -> str:
        return self._call("html") or ""

    def screenshot(self, path: str) -> str:
        return self._call("screenshot", path=path) or path

    def eval_js(self, script: str):
        return self._call("eval_js", script=script)
