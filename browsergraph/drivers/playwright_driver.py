"""Playwright / Patchright adapter.

Imports Playwright at construction, not at module import, so `drivers.build`
can raise a useful DriverUnavailable rather than an ImportError traceback.
Patchright is API-compatible, so one adapter covers both engines.
"""
from __future__ import annotations

import random
import time
from typing import Any

from browsergraph.dimensions import (
    Binary,
    Capture,
    Display,
    Engine,
    Spec,
    Stealth,
    Transport,
)
from browsergraph.ports import Element, PageState

_CHANNEL = {
    Binary.SYSTEM_CHROME: "chrome",
    Binary.CHROME_FOR_TESTING: "chromium",
    Binary.BRAVE: "chrome",
}

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}};
"""


def in_container() -> bool:
    """Best-effort: are we root inside a container?

    Docker, Kubernetes, CI runners, Kaggle and Colab all run as root, and
    Chrome's setuid sandbox cannot initialise as root. The failure is
    `TargetClosedError: Target page, context or browser has been closed`, which
    names neither the cause nor the fix.
    """
    import os
    if os.path.exists("/.dockerenv"):
        return True
    try:
        return os.geteuid() == 0
    except AttributeError:      # pragma: no cover - non-POSIX
        return False


def container_args() -> list[str]:
    """Chromium flags without which it will not start in a container.

    `--no-sandbox` looks alarming and is worth being precise about: Chrome's
    sandbox relies on user namespaces that are unavailable to a root process in
    a default container, so it is not a protection being given up — it is one
    that was never available. Without the flag Chrome exits immediately.

    `--disable-dev-shm-usage` moves shared memory off /dev/shm, which containers
    default to 64 MB; exceeding it crashes tabs unpredictably under load.
    """
    return ["--no-sandbox", "--disable-dev-shm-usage"]


class PlaywrightBrowser:
    def __init__(self, spec: Spec, executable_path: str = "") -> None:
        self.spec = spec
        self.executable_path = executable_path
        # Typed Any, not Optional[Playwright]: playwright is an optional
        # dependency imported inside start(), so its real types are not
        # importable at check time on an install that never uses this engine.
        self._pw: Any = None
        self._browser: Any = None
        self._ctx: Any = None
        self._page: Any = None
        self.video_path = ""
        self.trace_path = ""
        self._camoufox: Any = None

    # lifecycle
    def start(self) -> None:
        """Start the browser, or leave nothing behind.

        A *partial* start is worse than a failed one. `sync_playwright().start()`
        parks a greenlet inside its own asyncio loop, which marks this thread as
        "inside a running loop" for as long as it lives. If the launch then fails
        — a missing binary, a bad channel, a timeout — the greenlet is never
        unwound, and every subsequent sync-playwright call *anywhere in the
        process* fails with "Sync API inside the asyncio loop", through no fault
        of the caller. One unlucky launch poisons the whole interpreter.

        So a failure here must unwind everything this method created.
        """
        try:
            self._start()
        except BaseException:
            self.stop()      # idempotent; safe on a half-built object
            raise

    def _start(self) -> None:
        if self.spec.engine is Engine.PATCHRIGHT:
            from patchright.sync_api import sync_playwright  # type: ignore
        elif self.spec.engine is Engine.CAMOUFOX:
            # Camoufox ships a hardened Firefox behind the playwright API.
            from camoufox.sync_api import Camoufox  # type: ignore
            self._camoufox = Camoufox(headless=self.spec.display is Display.HEADLESS)
            self._ctx = self._camoufox.__enter__()
            self._page = self._ctx.new_page()
            return
        else:
            from playwright.sync_api import sync_playwright  # type: ignore

        self._pw = sync_playwright().start()
        launcher = {
            Binary.FIREFOX: self._pw.firefox,
            Binary.WEBKIT: self._pw.webkit,
        }.get(self.spec.binary, self._pw.chromium)

        kwargs: dict = {"headless": self.spec.display is Display.HEADLESS}
        if self.executable_path:
            kwargs["executable_path"] = self.executable_path
        elif self.spec.binary in _CHANNEL and launcher is self._pw.chromium:
            kwargs["channel"] = _CHANNEL[self.spec.binary]
        if self.spec.identity.proxy:
            kwargs["proxy"] = {"server": self.spec.identity.proxy}

        # Launch flags: caller-supplied, plus the ones a container cannot start
        # without. Firefox and WebKit take neither, so this is chromium-only.
        if launcher is self._pw.chromium:
            args = list(self.spec.extra.get("launch_args", []))
            if self.spec.extra.get("container_args", in_container()):
                args += [a for a in container_args() if a not in args]
            if args:
                kwargs["args"] = args

        if self.spec.transport is Transport.REMOTE_CDP:
            self._browser = launcher.connect_over_cdp(self.spec.endpoint)
        elif self.spec.transport is Transport.BROWSERLESS:
            self._browser = launcher.connect(self.spec.endpoint)
        else:
            self._browser = launcher.launch(**kwargs)

        ident = self.spec.identity
        ctx_kwargs: dict = {"viewport": {"width": ident.viewport[0],
                                         "height": ident.viewport[1]}}
        if ident.user_agent:
            ctx_kwargs["user_agent"] = ident.user_agent
        if ident.locale:
            ctx_kwargs["locale"] = ident.locale
        if ident.timezone:
            ctx_kwargs["timezone_id"] = ident.timezone

        # Video uses playwright's bundled ffmpeg, so no system install is
        # needed — but that encoder only emits webm.
        if self.spec.capture in (Capture.VIDEO, Capture.VIDEO_AND_TRACE):
            ctx_kwargs["record_video_dir"] = self.spec.artifact_dir
            ctx_kwargs["record_video_size"] = {"width": ident.viewport[0],
                                               "height": ident.viewport[1]}

        if ident.profile_dir and self.spec.transport is Transport.LOCAL:
            self._ctx = launcher.launch_persistent_context(
                ident.profile_dir, **{**kwargs, **ctx_kwargs})
            self._browser = None
        else:
            self._ctx = self._browser.new_context(**ctx_kwargs)

        if self.spec.stealth in (Stealth.STEALTH_JS, Stealth.UNDETECTED,
                                 Stealth.FULL_FINGERPRINT):
            self._ctx.add_init_script(_STEALTH_JS)

        if self.spec.capture in (Capture.TRACE, Capture.VIDEO_AND_TRACE):
            try:
                self._ctx.tracing.start(screenshots=True, snapshots=True, sources=False)
            except Exception:
                pass

        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

        if self.spec.engine is Engine.PLAYWRIGHT_STEALTH:
            try:
                from playwright_stealth import stealth_sync  # type: ignore
                stealth_sync(self._page)
            except ImportError:
                pass          # the STEALTH_JS init script above still applies

    def stop(self) -> None:
        # Artifacts are only finalised when the context closes, so their paths
        # must be captured here rather than during the run.
        try:
            if self._ctx and self.spec.capture in (Capture.TRACE, Capture.VIDEO_AND_TRACE):
                self.trace_path = f"{self.spec.artifact_dir.rstrip('/')}/trace.zip"
                self._ctx.tracing.stop(path=self.trace_path)
        except Exception:
            self.trace_path = ""
        try:
            if self._page and getattr(self._page, "video", None):
                self.video_path = self._page.video.path()
        except Exception:
            self.video_path = ""

        if self._camoufox is not None:
            try:
                self._camoufox.__exit__(None, None, None)
            except Exception:
                pass
            self._camoufox = None
            return

        for closer in (self._ctx, self._browser):
            try:
                if closer:
                    closer.close()
            except Exception:
                pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        # Cleared so stop() is idempotent and a half-built object is left inert:
        # start() calls stop() on failure, and the caller will usually call it
        # again from its own finally.
        self._pw = self._browser = self._ctx = self._page = None

    # navigation
    def goto(self, url: str) -> PageState:
        resp = self._page.goto(url, wait_until="domcontentloaded")
        return PageState(url=self._page.url, title=self._page.title(),
                         status=resp.status if resp else 0)

    def state(self) -> PageState:
        return PageState(url=self._page.url, title=self._page.title(), status=200)

    # elements
    def find(self, selector: str) -> Element | None:
        loc = self._page.locator(selector)
        if loc.count() == 0:
            return None
        return Element(selector=selector, handle=loc.first,
                       text=loc.first.inner_text() if loc.count() else "")

    def click(self, selector: str) -> None:
        self._page.locator(selector).first.click()

    def type(self, selector: str, text: str, cps: float = 0.0) -> None:
        loc = self._page.locator(selector).first
        if cps <= 0:
            loc.fill(text)
            return
        loc.click()
        for ch in text:
            loc.press_sequentially(ch) if hasattr(loc, "press_sequentially") else loc.type(ch)
            time.sleep(random.uniform(0.5 / cps, 1.5 / cps))

    def scroll(self, dy: int) -> None:
        # mouse.wheel depends on pointer position and silently does nothing on
        # a page the mouse has never entered; scrollBy always applies.
        self._page.evaluate("dy => window.scrollBy(0, dy)", dy)

    def wait_for(self, selector: str, timeout: float = 10.0) -> bool:
        try:
            self._page.locator(selector).first.wait_for(timeout=timeout * 1000)
            return True
        except Exception:
            return False

    def text_of(self, selector: str) -> str:
        loc = self._page.locator(selector)
        return loc.first.inner_text() if loc.count() else ""

    def html(self) -> str:
        return self._page.content()

    def screenshot(self, path: str) -> str:
        self._page.screenshot(path=path)
        return path

    def eval_js(self, script: str):
        return self._page.evaluate(script)
