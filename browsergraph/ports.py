"""The driver-agnostic browser interface.

This is the load-bearing abstraction. Action nodes (Navigate, Click, Type…)
talk only to `BrowserPort`, never to Playwright or Selenium directly. That is
what makes "any engine × any action" real rather than two parallel
implementations that drift apart.

A driver adapter implements this protocol; adding an engine means writing one
adapter, not touching a single action node.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Element:
    """A resolved element handle, opaque to callers."""
    selector: str
    handle: Any = None
    text: str = ""


@dataclass
class PageState:
    url: str = ""
    title: str = ""
    status: int = 0


@runtime_checkable
class BrowserPort(Protocol):
    """Minimal surface every engine must provide.

    Deliberately small. Anything expressible as a composition of these belongs
    in a node, not here — a wide port means every new engine is expensive.
    """

    def start(self) -> None: ...
    def stop(self) -> None: ...

    def goto(self, url: str) -> PageState: ...
    def state(self) -> PageState: ...

    def find(self, selector: str) -> Element | None: ...
    def click(self, selector: str) -> None: ...
    def type(self, selector: str, text: str, cps: float = 0.0) -> None: ...
    def scroll(self, dy: int) -> None: ...
    def wait_for(self, selector: str, timeout: float = 10.0) -> bool: ...

    def text_of(self, selector: str) -> str: ...
    def html(self) -> str: ...
    def screenshot(self, path: str) -> str: ...
    def eval_js(self, script: str) -> Any: ...


@dataclass
class Context:
    """Everything flowing through a graph run.

    Nodes read and write `data`; `artifacts` collects file paths produced along
    the way; `log` is the run's narrative, which is what you read when a graph
    behaves unexpectedly.
    """
    browser: BrowserPort | None = None
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    failed: bool = False
    error: str = ""

    @property
    def page(self) -> BrowserPort:
        """The browser, or a clear explanation of why there isn't one.

        Nodes that declare `needs_browser` reach for it unconditionally. Without
        this, a graph run with no browser attached dies on `'NoneType' object has
        no attribute 'goto'` — a message that names neither the node nor the
        cause, three frames below the code the caller actually wrote.
        """
        if self.browser is None:
            raise RuntimeError(
                "this node needs a browser but the run context has none. "
                "Pass one to run(graph, spec, browser=...), or use a node "
                "with needs_browser = False.")
        return self.browser

    def note(self, msg: str) -> None:
        self.log.append(msg)

    def fail(self, msg: str) -> None:
        self.failed = True
        self.error = msg
        self.log.append(f"FAIL: {msg}")
