"""The CDP family: nodriver, zendriver, pydoll — and raw DevTools.

These engines exist because they are *not* WebDriver and *not* Playwright. They
speak the Chrome DevTools Protocol directly, so there is no `navigator.webdriver`
to hide, no CDP-injection footprint from an automation framework, and no
driver binary whose version must match the browser. That is the entire point of
them, and it is why they belong in an evasion-capable ladder.

They are also all **async**, which is the design problem. `BrowserPort` is
synchronous, deliberately: a graph is a sequence of steps and colouring the
whole library async to accommodate three engines would be the tail wagging the
dog. So each instance owns a private event loop on a dedicated thread and
marshals calls onto it — the mirror image of `drivers.threaded`, which exists
because *Playwright's sync API* refuses to run inside someone else's loop.

The three libraries have deliberately similar APIs (zendriver is a maintained
fork of nodriver; pydoll is an independent implementation of the same idea), so
one adapter covers them with a small amount of per-engine dispatch rather than
three near-identical files.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from browsergraph.dimensions import Binary, Display, Engine, Spec
from browsergraph.ports import Element, PageState


def _unwrap(result: Any) -> Any:
    """The plain value inside whatever the library handed back.

    The three libraries disagree about how much of the DevTools envelope to
    remove. zendriver returns the value; pydoll returns the whole frame,
    `{"id": 4, "result": {"result": {"type": "string", "value": "CDP"}}}`, and
    a caller that does not unwrap it gets a page title that is a dict — which
    passes every type check and is wrong everywhere it is used.
    """
    seen = 0
    while isinstance(result, dict) and seen < 6:
        if "value" in result and "type" in result:
            return result["value"]
        if "result" in result:
            result = result["result"]
            seen += 1
            continue
        if "exceptionDetails" in result:
            raise RuntimeError(str(result["exceptionDetails"])[:200])
        break
    return getattr(result, "value", result)


class _Loop:
    """A private asyncio loop on its own thread.

    Private rather than `asyncio.run` per call because the browser objects are
    bound to the loop that created them: a second loop cannot drive a page the
    first one opened, and the failure is an obscure "attached to a different
    loop" much later.
    """

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._serve, daemon=True,
                                       name="browsergraph-cdp")
        self.thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro, timeout: float = 120.0):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=10)
        try:
            self.loop.close()
        except RuntimeError:      # already closing
            pass


class CdpBrowser:
    """A synchronous BrowserPort over an async CDP library."""

    supports_javascript = True

    def __init__(self, spec: Spec, executable_path: str = "") -> None:
        self.spec = spec
        self.executable_path = executable_path
        self._loop: _Loop | None = None
        self._browser: Any = None
        self._page: Any = None
        self._stopped = False

    # lifecycle --------------------------------------------------------------
    def _module(self):
        """Import the engine's library, or say precisely what is wrong with it.

        The import is deferred to `start()`, so a package that is installed but
        *unimportable* surfaces here rather than at construction. That is not
        hypothetical: a published nodriver build ships a source file with
        non-UTF-8 bytes and raises SyntaxError on import. Left bare, the
        traceback points into someone else's package and looks like a bug here.
        """
        engine = self.spec.engine
        name = {Engine.ZENDRIVER: "zendriver", Engine.PYDOLL: "pydoll"}.get(
            engine, "nodriver")
        import importlib
        try:
            return importlib.import_module(name), name
        except ImportError as e:
            from browsergraph.drivers import DriverUnavailable
            raise DriverUnavailable(
                f"{engine.value} needs the {name} package: {e}. "
                f"pip install {name}") from e
        except SyntaxError as e:
            from browsergraph.drivers import DriverUnavailable
            raise DriverUnavailable(
                f"{engine.value}: the installed {name} package is broken and "
                f"cannot be imported ({e.msg} at {e.filename}:{e.lineno}). "
                f"This is a defect in {name}, not in browsergraph — try a "
                f"different version, or use engine=zendriver, which is a "
                f"maintained fork of the same design.") from e

    def _browser_args(self) -> list[str]:
        args = list(self.spec.extra.get("launch_args", []))
        ident = self.spec.identity
        args.append(f"--window-size={ident.viewport[0]},{ident.viewport[1]}")
        if ident.user_agent:
            args.append(f"--user-agent={ident.user_agent}")
        if ident.proxy:
            args.append(f"--proxy-server={ident.proxy}")
        from browsergraph.drivers.playwright_driver import container_args, in_container
        if self.spec.extra.get("container_args", in_container()):
            args += [a for a in container_args() if a not in args]
        return args

    def _executable(self) -> str:
        if self.executable_path:
            return self.executable_path
        # These engines drive an installed Chrome; they ship no browser of their
        # own, so a wrapper script on PATH would fail the same way it does for
        # selenium. See browsergraph.binaries.
        from browsergraph.binaries import resolve
        for binary in (self.spec.binary, Binary.SYSTEM_CHROME,
                       Binary.CHROME_FOR_TESTING, Binary.BRAVE):
            found = resolve(binary)
            if found.ok:
                return found.path
        return ""

    def start(self) -> None:
        try:
            self._start()
        except BaseException:
            self.stop()          # never leave a loop thread or browser behind
            raise

    def _start(self) -> None:
        mod, name = self._module()
        self._loop = _Loop()
        headless = self.spec.display is Display.HEADLESS
        exe = self._executable()

        async def launch():
            if name == "pydoll":
                from pydoll.browser import Chrome  # type: ignore
                from pydoll.browser.options import ChromiumOptions  # type: ignore
                opts = ChromiumOptions()
                if headless:
                    opts.add_argument("--headless=new")
                for a in self._browser_args():
                    opts.add_argument(a)
                if exe:
                    opts.binary_location = exe
                browser = Chrome(options=opts)
                tab = await browser.start()
                return browser, tab

            kwargs: dict = {"headless": headless, "browser_args": self._browser_args()}
            if exe:
                kwargs["browser_executable_path"] = exe
            if self.spec.identity.profile_dir:
                kwargs["user_data_dir"] = self.spec.identity.profile_dir
            browser = await mod.start(**kwargs)
            page = await browser.get("about:blank")
            return browser, page

        self._browser, self._page = self._loop.run(launch())

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        try:
            if self._browser is not None and self._loop is not None:
                async def shut():
                    stop = getattr(self._browser, "stop", None)
                    if stop is None:
                        return
                    result = stop()
                    if asyncio.iscoroutine(result):
                        await result
                try:
                    self._loop.run(shut(), timeout=20)
                except Exception:
                    pass
        finally:
            if self._loop is not None:
                self._loop.close()
                self._loop = None
            self._browser = self._page = None

    # navigation -------------------------------------------------------------
    def _run(self, coro, timeout: float = 120.0):
        if self._loop is None:
            raise RuntimeError("browser is not started")
        return self._loop.run(coro, timeout=timeout)

    def goto(self, url: str) -> PageState:
        async def go():
            if hasattr(self._page, "go_to"):          # pydoll
                await self._page.go_to(url)
            else:                                      # nodriver / zendriver
                self._page = await self._browser.get(url)
            return await self._state()
        return self._run(go())

    async def _state(self) -> PageState:
        title = await self._eval_async("document.title") or ""
        current = await self._eval_async("location.href") or ""
        return PageState(url=str(current), title=str(title), status=200)

    def state(self) -> PageState:
        return self._run(self._state())

    # elements ---------------------------------------------------------------
    async def _eval_async(self, script: str):
        for attr in ("evaluate", "execute_script"):
            fn = getattr(self._page, attr, None)
            if fn is None:
                continue
            result = fn(script)
            if asyncio.iscoroutine(result):
                result = await result
            return _unwrap(result)
        raise RuntimeError(f"{self.spec.engine.value} exposes no evaluate()")

    def eval_js(self, script: str) -> Any:
        return self._run(self._eval_async(script))

    def find(self, selector: str) -> Element | None:
        text = self._run(self._eval_async(
            f"(()=>{{const e=document.querySelector({selector!r});"
            f"return e?(e.innerText||e.textContent||''):null}})()"))
        return None if text is None else Element(selector=selector,
                                                 text=str(text).strip())

    def click(self, selector: str) -> None:
        ok = self._run(self._eval_async(
            f"(()=>{{const e=document.querySelector({selector!r});"
            f"if(!e)return false;e.click();return true}})()"))
        if not ok:
            raise RuntimeError(f"click target not found: {selector}")

    def type(self, selector: str, text: str, cps: float = 0.0) -> None:
        ok = self._run(self._eval_async(
            f"(()=>{{const e=document.querySelector({selector!r});if(!e)return false;"
            f"e.focus();e.value={text!r};"
            f"e.dispatchEvent(new Event('input',{{bubbles:true}}));"
            f"e.dispatchEvent(new Event('change',{{bubbles:true}}));return true}})()"))
        if not ok:
            raise RuntimeError(f"type target not found: {selector}")

    def scroll(self, dy: int) -> None:
        self._run(self._eval_async(f"window.scrollBy(0,{int(dy)})"))

    def wait_for(self, selector: str, timeout: float = 10.0) -> bool:
        async def poll():
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                if await self._eval_async(
                        f"!!document.querySelector({selector!r})"):
                    return True
                await asyncio.sleep(0.15)
            return False
        return bool(self._run(poll(), timeout=timeout + 30))

    def text_of(self, selector: str) -> str:
        el = self.find(selector)
        return el.text if el else ""

    def html(self) -> str:
        return str(self._run(self._eval_async("document.documentElement.outerHTML")) or "")

    def screenshot(self, path: str) -> str:
        async def shot():
            for attr in ("save_screenshot", "screenshot", "take_screenshot"):
                fn = getattr(self._page, attr, None)
                if fn is None:
                    continue
                result = fn(path)
                if asyncio.iscoroutine(result):
                    await result
                return path
            raise RuntimeError(
                f"{self.spec.engine.value} exposes no screenshot method")
        return str(self._run(shot()))
