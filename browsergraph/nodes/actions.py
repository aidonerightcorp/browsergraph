"""Action nodes — engine-agnostic by construction.

Every node here goes through `Context.browser`, which is a `BrowserPort`. None
of them import Playwright or Selenium, so each works unchanged against any
adapter.
"""
from __future__ import annotations

import random
import time
from typing import ClassVar

from browsergraph.dimensions import Behavior
from browsergraph.nodes.base import Node, register
from browsergraph.ports import Context


def _pause(behavior: Behavior | None) -> None:
    if not behavior:
        return
    lo, hi = behavior.min_action_delay, behavior.max_action_delay
    if hi > 0:
        time.sleep(random.uniform(lo, hi))


@register
class Navigate(Node):
    kind: ClassVar[str] = "navigate"
    writes: ClassVar[tuple[str, ...]] = ("url", "title")

    def __init__(self, url: str, name: str = "", behavior: Behavior | None = None):
        super().__init__(name)
        self.url = url
        self.behavior = behavior

    def run(self, ctx: Context) -> Context:
        state = ctx.page.goto(self.url)
        ctx.data["url"] = state.url
        ctx.data["title"] = state.title
        ctx.note(f"navigate -> {state.url} ({state.title!r})")
        if self.behavior and self.behavior.dwell_after_load:
            time.sleep(self.behavior.dwell_after_load)
        return ctx


@register
class Click(Node):
    kind: ClassVar[str] = "click"
    mutates: bool = True
    interacts: bool = True

    def __init__(self, selector: str, name: str = "", behavior: Behavior | None = None,
                 optional: bool = False):
        super().__init__(name)
        self.selector = selector
        self.behavior = behavior
        self.optional = optional

    def run(self, ctx: Context) -> Context:
        _pause(self.behavior)
        if ctx.page.find(self.selector) is None:
            if self.optional:
                ctx.note(f"click {self.selector!r} skipped (absent, optional)")
                return ctx
            ctx.fail(f"click target not found: {self.selector}")
            return ctx
        ctx.page.click(self.selector)
        ctx.note(f"click {self.selector!r}")
        return ctx


@register
class Type(Node):
    kind: ClassVar[str] = "type"
    mutates: bool = True
    interacts: bool = True

    def __init__(self, selector: str, text: str, name: str = "",
                 behavior: Behavior | None = None):
        super().__init__(name)
        self.selector = selector
        self.text = text
        self.behavior = behavior

    def run(self, ctx: Context) -> Context:
        _pause(self.behavior)
        cps = self.behavior.typing_cps if self.behavior else 0.0
        ctx.page.type(self.selector, self.text, cps=cps)
        ctx.note(f"type into {self.selector!r} ({len(self.text)} chars, cps={cps})")
        return ctx


@register
class WaitFor(Node):
    kind: ClassVar[str] = "wait_for"
    verifies: bool = True

    def __init__(self, selector: str, timeout: float = 10.0, name: str = ""):
        super().__init__(name)
        self.selector = selector
        self.timeout = timeout

    def run(self, ctx: Context) -> Context:
        ok = ctx.page.wait_for(self.selector, timeout=self.timeout)
        ctx.note(f"wait_for {self.selector!r} -> {ok}")
        if not ok:
            ctx.fail(f"timeout waiting for {self.selector}")
        return ctx


@register
class Extract(Node):
    kind: ClassVar[str] = "extract"
    interacts: bool = True

    def __init__(self, selector: str, into: str, name: str = ""):
        super().__init__(name)
        self.selector = selector
        self.into = into

    @property
    def writes(self) -> tuple[str, ...]:  # type: ignore[override]
        return (self.into,)

    def run(self, ctx: Context) -> Context:
        value = ctx.page.text_of(self.selector)
        ctx.data[self.into] = value
        ctx.note(f"extract {self.selector!r} -> {self.into} ({len(value)} chars)")
        return ctx


@register
class Scroll(Node):
    kind: ClassVar[str] = "scroll"

    def __init__(self, dy: int = 600, name: str = "", behavior: Behavior | None = None):
        super().__init__(name)
        self.dy = dy
        self.behavior = behavior

    def run(self, ctx: Context) -> Context:
        _pause(self.behavior)
        dy = self.dy
        if self.behavior and self.behavior.scroll_jitter:
            dy = int(dy * random.uniform(0.75, 1.25))
        ctx.page.scroll(dy)
        ctx.note(f"scroll {dy}")
        return ctx


@register
class Screenshot(Node):
    kind: ClassVar[str] = "screenshot"

    def __init__(self, path: str, name: str = ""):
        super().__init__(name)
        self.path = path

    def run(self, ctx: Context) -> Context:
        saved = ctx.page.screenshot(self.path)
        ctx.artifacts.append(saved)
        ctx.note(f"screenshot -> {saved}")
        return ctx
