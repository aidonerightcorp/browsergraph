"""In-memory BrowserPort — the reference implementation.

Exists so graphs, nodes and combinations can be tested without launching a
browser. It is also the fastest way to check a new node behaves, and it keeps
the test suite runnable in CI and on Kaggle where no browser exists.
"""
from __future__ import annotations

from browsergraph.dimensions import Spec
from browsergraph.ports import Element, PageState


class MockBrowser:
    """Serves canned pages. `pages` maps url -> {selector: text}."""

    def __init__(self, spec: Spec | None = None, pages: dict | None = None) -> None:
        self.spec = spec or Spec()
        self.pages = pages or {}
        self.started = False
        self.calls: list[str] = []
        self._url = ""
        self._scroll = 0

    # lifecycle
    def start(self) -> None:
        self.started = True
        self.calls.append("start")

    def stop(self) -> None:
        self.started = False
        self.calls.append("stop")

    # navigation
    def goto(self, url: str) -> PageState:
        self.calls.append(f"goto:{url}")
        self._url = url
        page = self.pages.get(url, {})
        return PageState(url=url, title=page.get("title", ""), status=200)

    def state(self) -> PageState:
        page = self.pages.get(self._url, {})
        return PageState(url=self._url, title=page.get("title", ""), status=200)

    # elements
    def _page(self) -> dict:
        return self.pages.get(self._url, {})

    def find(self, selector: str) -> Element | None:
        page = self._page()
        if selector in page:
            return Element(selector=selector, text=str(page[selector]))
        return None

    def click(self, selector: str) -> None:
        self.calls.append(f"click:{selector}")

    def type(self, selector: str, text: str, cps: float = 0.0) -> None:
        self.calls.append(f"type:{selector}:{text}")

    def scroll(self, dy: int) -> None:
        self._scroll += dy
        self.calls.append(f"scroll:{dy}")

    def wait_for(self, selector: str, timeout: float = 10.0) -> bool:
        self.calls.append(f"wait:{selector}")
        return selector in self._page()

    def text_of(self, selector: str) -> str:
        el = self.find(selector)
        return el.text if el else ""

    def html(self) -> str:
        page = self._page()
        return "".join(f"<div data-sel='{k}'>{v}</div>" for k, v in page.items())

    def screenshot(self, path: str) -> str:
        self.calls.append(f"screenshot:{path}")
        return path

    def eval_js(self, script: str):
        self.calls.append("eval_js")
        return None
