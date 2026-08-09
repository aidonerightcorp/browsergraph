"""A BrowserPort that records what was asked of it, then delegates.

Two uses, both of which need the same thing — an honest log of the calls a node
actually made:

* **contract checking** — a node declaring `mutates = False` that turns out to
  call `click` has a declaration the linter is quietly trusting (see
  `nodes.checked`);
* **debugging a graph that "did nothing"** — the call log distinguishes *no
  attempt was made* from *the attempt was made and silently failed*, which look
  identical in the data and are usually debugged in the wrong place.

It wraps rather than subclasses, so it composes with every engine including
the isolated worker and the browser-less HTTP driver.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from browsergraph.ports import BrowserPort, Element, PageState

#: Calls that change state on the far side. `goto` is not one of them: fetching
#: a page is not a mutation, which is why Navigate declares `mutates = False`.
#: `eval_js` is deliberately absent — arbitrary script may or may not mutate, so
#: flagging it would produce false positives on every read-only evaluation.
MUTATING_CALLS = frozenset({"click", "type"})


@dataclass
class Call:
    method: str
    args: tuple = ()
    error: str = ""

    def __str__(self) -> str:
        arg = ", ".join(repr(a) for a in self.args)
        return f"{self.method}({arg})" + (f" -> {self.error}" if self.error else "")


@dataclass
class RecordingBrowser:
    """Delegates every BrowserPort call to `inner`, keeping a log.

    Failures are recorded *and re-raised*: swallowing them here would turn this
    from an observer into a behaviour change, and a debugging aid that alters
    what it observes is worse than none.
    """

    inner: BrowserPort
    calls: list[Call] = field(default_factory=list)

    # bookkeeping ------------------------------------------------------------
    def _record(self, method: str, *args) -> Call:
        call = Call(method=method, args=args)
        self.calls.append(call)
        return call

    def _do(self, method: str, fn, *args):
        call = self._record(method, *args)
        try:
            return fn()
        except Exception as e:
            call.error = f"{type(e).__name__}: {e}"
            raise

    @property
    def mutated(self) -> bool:
        return any(c.method in MUTATING_CALLS for c in self.calls)

    def methods_used(self) -> set[str]:
        return {c.method for c in self.calls}

    def log(self) -> list[str]:
        return [str(c) for c in self.calls]

    def reset(self) -> None:
        self.calls.clear()

    # BrowserPort ------------------------------------------------------------
    def start(self) -> None:
        self._do("start", lambda: self.inner.start())

    def stop(self) -> None:
        self._do("stop", lambda: self.inner.stop())

    def goto(self, url: str) -> PageState:
        return self._do("goto", lambda: self.inner.goto(url), url)

    def state(self) -> PageState:
        return self._do("state", lambda: self.inner.state())

    def find(self, selector: str) -> Element | None:
        return self._do("find", lambda: self.inner.find(selector), selector)

    def click(self, selector: str) -> None:
        self._do("click", lambda: self.inner.click(selector), selector)

    def type(self, selector: str, text: str, cps: float = 0.0) -> None:
        self._do("type", lambda: self.inner.type(selector, text, cps), selector, text)

    def scroll(self, dy: int) -> None:
        self._do("scroll", lambda: self.inner.scroll(dy), dy)

    def wait_for(self, selector: str, timeout: float = 10.0) -> bool:
        return self._do("wait_for", lambda: self.inner.wait_for(selector, timeout), selector)

    def text_of(self, selector: str) -> str:
        return self._do("text_of", lambda: self.inner.text_of(selector), selector)

    def html(self) -> str:
        return self._do("html", lambda: self.inner.html())

    def screenshot(self, path: str) -> str:
        return self._do("screenshot", lambda: self.inner.screenshot(path), path)

    def eval_js(self, script: str) -> Any:
        return self._do("eval_js", lambda: self.inner.eval_js(script), script)

    def __getattr__(self, item: str) -> Any:
        """Pass through engine-specific extras (video_path, trace_path, ...).

        Only reached for names this class does not define, so it cannot
        accidentally bypass the recording of a real BrowserPort method.
        """
        return getattr(self.inner, item)
