"""Nodes for the things a real automation actually has to do.

The core `BrowserPort` covers navigating, clicking, typing and reading. Real
pages also want a keyboard, a `<select>`, a file to attach, a download to keep,
an iframe to step into. Those are not universal — `engine=http` has no keyboard
because it has no browser — so each node here declares the capability it needs
and `browsergraph.capabilities` checks it against the engine **before** anything
launches. The alternative, silently doing nothing, produces a run that reports
success and uploaded no file.

Three of these need no capability at all because they are built from
`eval_js`, which every engine with a browser has:

* `WaitStable` waits for the DOM to stop changing. This exists because of a
  real bug in this repository's own tests: `goto` returns at DOMContentLoaded,
  which can precede the paint, and a screenshot taken in that gap captured a
  blank canvas. It looked like an OCR failure for an embarrassingly long time.
* `A11yTree` extracts the accessible name/role tree — what a screen reader
  would announce. It is the cheap way to give a language model the page: a few
  hundred tokens of structure instead of an image, and unlike a screenshot it
  says what things *are* rather than what they look like.
* `AssertText` / `AssertUrl` verify, which is the point of the whole library.
"""
from __future__ import annotations

import json
import time
from typing import ClassVar

from browsergraph import capabilities as caps
from browsergraph.nodes.base import Node, register
from browsergraph.ports import Context


@register
class Press(Node):
    """Send a keystroke — Enter to submit, Escape to dismiss, Tab to move on."""
    kind: ClassVar[str] = "press"
    mutates: bool = True
    requires_capabilities: ClassVar[tuple[str, ...]] = (caps.PRESS,)

    def __init__(self, key: str, selector: str = "", name: str = ""):
        super().__init__(name)
        self.key = key
        self.selector = selector

    def run(self, ctx: Context) -> Context:
        caps.require(ctx.page, caps.PRESS, self.name)
        ctx.page.press(self.key, self.selector)      # type: ignore[attr-defined]
        ctx.note(f"press {self.key}" + (f" on {self.selector}" if self.selector else ""))
        return ctx


@register
class SelectOption(Node):
    """Choose an option in a `<select>`.

    Not the same as clicking it: a native select opens an OS-level menu that a
    click cannot reach, which is why this is its own capability rather than a
    composition of clicks.
    """
    kind: ClassVar[str] = "select_option"
    mutates: bool = True
    interacts: bool = True
    requires_capabilities: ClassVar[tuple[str, ...]] = (caps.SELECT,)

    def __init__(self, selector: str, value: str, name: str = ""):
        super().__init__(name)
        self.selector = selector
        self.value = value

    def run(self, ctx: Context) -> Context:
        caps.require(ctx.page, caps.SELECT, self.name)
        ctx.page.select_option(self.selector, self.value)   # type: ignore[attr-defined]
        ctx.note(f"select {self.selector} = {self.value!r}")
        return ctx


@register
class Upload(Node):
    """Attach files to an `<input type=file>`."""
    kind: ClassVar[str] = "upload"
    mutates: bool = True
    interacts: bool = True
    requires_capabilities: ClassVar[tuple[str, ...]] = (caps.UPLOAD,)

    def __init__(self, selector: str, paths: list[str] | str, name: str = ""):
        super().__init__(name)
        self.selector = selector
        self.paths = [paths] if isinstance(paths, str) else list(paths)

    def run(self, ctx: Context) -> Context:
        caps.require(ctx.page, caps.UPLOAD, self.name)
        ctx.page.upload(self.selector, self.paths)   # type: ignore[attr-defined]
        ctx.note(f"upload {len(self.paths)} file(s) -> {self.selector}")
        return ctx


@register
class Download(Node):
    """Click something and keep the file it hands back.

    Writes the saved path into the context and records it as an artifact, so a
    run that claims to have downloaded something has the file to show for it.
    """
    kind: ClassVar[str] = "download"
    mutates: bool = True
    interacts: bool = True
    requires_capabilities: ClassVar[tuple[str, ...]] = (caps.DOWNLOAD,)

    def __init__(self, selector: str, dest: str, into: str = "download",
                 name: str = ""):
        super().__init__(name)
        self.selector = selector
        self.dest = dest
        self.into = into

    @property
    def writes(self) -> tuple[str, ...]:      # type: ignore[override]
        return (self.into,)

    def run(self, ctx: Context) -> Context:
        caps.require(ctx.page, caps.DOWNLOAD, self.name)
        saved = ctx.page.download(self.selector, self.dest)  # type: ignore[attr-defined]
        ctx.data[self.into] = saved
        ctx.artifacts.append(saved)
        ctx.note(f"download {self.selector} -> {saved}")
        return ctx


@register
class UseFrame(Node):
    """Step into an iframe, or back out to the top document with `None`.

    Modal on purpose: Selenium's frame switch is modal, Playwright's is a
    separate handle, and a node written once has to mean the same thing on both
    or the port is a fiction. The adapters reconcile it.
    """
    kind: ClassVar[str] = "use_frame"
    requires_capabilities: ClassVar[tuple[str, ...]] = (caps.FRAMES,)

    def __init__(self, selector: str | None, name: str = ""):
        super().__init__(name)
        self.selector = selector or ""
        self._target = selector

    def run(self, ctx: Context) -> Context:
        caps.require(ctx.page, caps.FRAMES, self.name)
        ok = ctx.page.use_frame(self._target)      # type: ignore[attr-defined]
        if not ok:
            ctx.fail(f"no iframe matching {self._target!r}")
        else:
            ctx.note(f"frame -> {self._target or 'top document'}")
        return ctx


@register
class Cookies(Node):
    """Read the cookie jar, and optionally seed it first."""
    kind: ClassVar[str] = "cookies"
    requires_capabilities: ClassVar[tuple[str, ...]] = (caps.COOKIES,)

    def __init__(self, into: str = "cookies", set_to: list[dict] | None = None,
                 name: str = ""):
        super().__init__(name)
        self.into = into
        self.set_to = set_to

    @property
    def writes(self) -> tuple[str, ...]:      # type: ignore[override]
        return (self.into,)

    @property
    def mutates(self) -> bool:                # type: ignore[override]
        # Seeding a jar changes the session; reading it does not.
        return self.set_to is not None

    def run(self, ctx: Context) -> Context:
        caps.require(ctx.page, caps.COOKIES, self.name)
        jar = ctx.page.cookies(self.set_to)        # type: ignore[attr-defined]
        ctx.data[self.into] = jar
        ctx.note(f"cookies: {len(jar)}")
        return ctx


@register
class SetViewport(Node):
    """Resize the rendering surface.

    Layout is a function of width, so a screenshot or a selector that works at
    1280 can fail at 390. Making it explicit lets a graph test both.
    """
    kind: ClassVar[str] = "set_viewport"
    requires_capabilities: ClassVar[tuple[str, ...]] = (caps.VIEWPORT,)

    def __init__(self, width: int, height: int, name: str = ""):
        super().__init__(name)
        self.width = width
        self.height = height

    def run(self, ctx: Context) -> Context:
        caps.require(ctx.page, caps.VIEWPORT, self.name)
        ctx.page.set_viewport(self.width, self.height)   # type: ignore[attr-defined]
        ctx.note(f"viewport {self.width}x{self.height}")
        return ctx


@register
class SavePdf(Node):
    """Print the page to PDF. Chromium-family only, and declared as such."""
    kind: ClassVar[str] = "save_pdf"
    requires_capabilities: ClassVar[tuple[str, ...]] = (caps.PDF,)

    def __init__(self, path: str, name: str = ""):
        super().__init__(name)
        self.path = path

    def run(self, ctx: Context) -> Context:
        caps.require(ctx.page, caps.PDF, self.name)
        saved = ctx.page.pdf(self.path)            # type: ignore[attr-defined]
        ctx.artifacts.append(saved)
        ctx.note(f"pdf -> {saved}")
        return ctx


# --- built from eval_js, so they need no extra capability -------------------

STABLE_JS = """
(quiet => new Promise(resolve => {
  let timer = null;
  const done = () => { observer.disconnect(); resolve(true); };
  const observer = new MutationObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(done, quiet);
  });
  observer.observe(document.documentElement,
    {childList: true, subtree: true, attributes: true, characterData: true});
  timer = setTimeout(done, quiet);
}))
"""


@register
class WaitStable(Node):
    """Wait until the DOM stops changing for `quiet` milliseconds.

    `goto` returns at DOMContentLoaded, which can precede the paint and almost
    always precedes whatever the page's scripts do next. This library's own
    canvas test screenshotted into exactly that gap and captured a blank image
    — it looked like an OCR failure for far too long. Waiting on a mutation
    observer is the honest version of the `time.sleep(2)` everyone writes.
    """
    kind: ClassVar[str] = "wait_stable"
    verifies: bool = True

    def __init__(self, quiet_ms: int = 400, timeout: float = 10.0, name: str = ""):
        super().__init__(name)
        self.quiet_ms = quiet_ms
        self.timeout = timeout

    def run(self, ctx: Context) -> Context:
        started = time.monotonic()
        try:
            ctx.page.eval_js(f"{STABLE_JS.strip()}({self.quiet_ms})")
        except Exception as e:
            # A page that never settles is a real condition, not a crash: some
            # sites animate forever. Say so and carry on.
            ctx.note(f"wait_stable: gave up ({type(e).__name__})")
            return ctx
        ctx.note(f"wait_stable: quiet after {time.monotonic() - started:.2f}s")
        return ctx


A11Y_JS = """
(limit => {
  const out = [];
  const named = el => (el.getAttribute('aria-label')
    || el.getAttribute('alt') || el.getAttribute('placeholder')
    || el.getAttribute('title') || el.getAttribute('name')
    || (el.innerText || '').trim().split('\\n')[0] || '').slice(0, 80);
  const role = el => el.getAttribute('role') || ({
    A: 'link', BUTTON: 'button', INPUT: 'input', SELECT: 'select',
    TEXTAREA: 'textarea', H1: 'heading', H2: 'heading', H3: 'heading',
    IMG: 'image', FORM: 'form', NAV: 'navigation', TABLE: 'table',
    LABEL: 'label', SUMMARY: 'summary'}[el.tagName] || '');
  const visible = el => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0')
      return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const walk = (el, depth) => {
    if (out.length >= limit) return;
    const r = role(el);
    if (r && visible(el)) {
      const entry = {role: r, name: named(el), depth: depth};
      if (el.id) entry.selector = '#' + el.id;
      else if (el.getAttribute('data-testid'))
        entry.selector = '[data-testid="' + el.getAttribute('data-testid') + '"]';
      if (el.disabled) entry.disabled = true;
      out.push(entry);
    }
    for (const child of el.children) walk(child, depth + 1);
  };
  walk(document.body, 0);
  return out;
})
"""


@register
class A11yTree(Node):
    """The page as a screen reader would announce it: roles and names.

    The cheap way to hand a page to a language model. A screenshot costs
    thousands of image tokens and shows what things *look* like; this costs a
    few hundred text tokens and says what they *are* — and it carries a
    selector for anything with an id or a test id, so a model's answer can be
    acted on directly instead of guessed at.

    Only visible elements, because an accessibility tree full of hidden menus
    is how a model ends up confidently clicking something nobody can see.
    """
    kind: ClassVar[str] = "a11y_tree"

    def __init__(self, into: str = "a11y", limit: int = 300, name: str = ""):
        super().__init__(name)
        self.into = into
        self.limit = limit

    @property
    def writes(self) -> tuple[str, ...]:      # type: ignore[override]
        return (self.into,)

    def run(self, ctx: Context) -> Context:
        tree = ctx.page.eval_js(f"{A11Y_JS.strip()}({self.limit})") or []
        ctx.data[self.into] = tree
        ctx.note(f"a11y_tree: {len(tree)} elements")
        return ctx

    @staticmethod
    def as_text(tree: list[dict]) -> str:
        """A compact rendering, for putting in a prompt."""
        lines = []
        for entry in tree:
            indent = "  " * min(entry.get("depth", 0), 6)
            selector = f"  [{entry['selector']}]" if entry.get("selector") else ""
            state = " (disabled)" if entry.get("disabled") else ""
            lines.append(f"{indent}{entry.get('role', '?')}: "
                         f"{entry.get('name', '')}{state}{selector}")
        return "\n".join(lines)


@register
class AssertText(Node):
    """Fail unless the page contains this text.

    A verification node in the plainest sense. `WaitFor` proves an element
    exists; this proves it says what it should.
    """
    kind: ClassVar[str] = "assert_text"
    verifies: bool = True

    def __init__(self, text: str, selector: str = "body", name: str = "",
                 absent: bool = False):
        super().__init__(name)
        self.text = text
        self.selector = selector
        self.absent = absent

    def run(self, ctx: Context) -> Context:
        found = ctx.page.text_of(self.selector) or ""
        present = self.text.lower() in found.lower()
        if present == self.absent:
            ctx.fail(f"{self.text!r} is "
                     + ("present in " if present else "not in ")
                     + f"{self.selector}")
        else:
            ctx.note(f"assert_text ok: {self.text!r}"
                     + (" absent" if self.absent else ""))
        return ctx


@register
class AssertUrl(Node):
    """Fail unless the current URL contains this fragment.

    The cheapest possible check that a navigation or a submit actually landed
    somewhere new — and the one most often left out.
    """
    kind: ClassVar[str] = "assert_url"
    verifies: bool = True

    def __init__(self, fragment: str, name: str = ""):
        super().__init__(name)
        self.fragment = fragment

    def run(self, ctx: Context) -> Context:
        url = ctx.page.state().url or ""
        if self.fragment.lower() not in url.lower():
            ctx.fail(f"url {url!r} does not contain {self.fragment!r}")
        else:
            ctx.note(f"assert_url ok: {url}")
        return ctx


@register
class Attribute(Node):
    """Extract an attribute rather than text — href, src, value, data-*.

    Falls back to the DOM **property** when there is no such attribute. The two
    are not the same thing and the difference bites exactly where it is least
    expected: `value` on an `<input>`, `checked` on a checkbox and `disabled`
    on a button are properties that reflect the live state, while the attribute
    holds whatever the HTML said at parse time — often nothing at all. Asking
    for `value` after a file upload returns null from `getAttribute` and the
    filename from the property, and only one of those answers the question.
    """
    kind: ClassVar[str] = "attribute"

    def __init__(self, selector: str, attribute: str, into: str = "",
                 name: str = ""):
        super().__init__(name)
        self.selector = selector
        self.attribute = attribute
        self.into = into or attribute

    @property
    def writes(self) -> tuple[str, ...]:      # type: ignore[override]
        return (self.into,)

    def run(self, ctx: Context) -> Context:
        selector = json.dumps(self.selector)
        attribute = json.dumps(self.attribute)
        script = (f"(() => {{ const el = document.querySelector({selector});"
                  f" if (!el) return null;"
                  f" const attr = el.getAttribute({attribute});"
                  f" if (attr !== null) return attr;"
                  f" const prop = el[{attribute}];"
                  f" return prop === undefined ? null : prop; }})()")
        value = ctx.page.eval_js(script)
        ctx.data[self.into] = value
        ctx.note(f"attribute {self.selector}@{self.attribute} = {value!r}")
        return ctx


@register
class CountElements(Node):
    """How many elements match — for pagination, result counts, empty states."""
    kind: ClassVar[str] = "count"

    def __init__(self, selector: str, into: str = "count", name: str = ""):
        super().__init__(name)
        self.selector = selector
        self.into = into

    @property
    def writes(self) -> tuple[str, ...]:      # type: ignore[override]
        return (self.into,)

    def run(self, ctx: Context) -> Context:
        script = (f"document.querySelectorAll({json.dumps(self.selector)}).length")
        count = ctx.page.eval_js(script)
        ctx.data[self.into] = int(count or 0)
        ctx.note(f"count {self.selector} = {ctx.data[self.into]}")
        return ctx
