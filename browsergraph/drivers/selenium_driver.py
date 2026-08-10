"""Selenium adapter.

Implements the same `BrowserPort` as the Playwright adapter, so every action
node works against it unchanged. `Stealth.UNDETECTED` routes through
undetected-chromedriver, which is why that stealth level is Selenium-only.
"""
from __future__ import annotations

import random
import time
from typing import Any

from browsergraph.dimensions import (
    Binary,
    Display,
    Engine,
    Spec,
    Stealth,
    Transport,
)
from browsergraph.ports import Element, PageState


def _chrome_major(path: str = "") -> int | None:
    """The major version of the Chrome that is *about to be launched*.

    Taking this from PATH was a real bug with a confusing signature. This host
    has Chrome 148 at /opt/google/chrome and Chromium 150 from a snap; PATH
    finds 148 first, so a spec asking for Chromium got a driver built for the
    other browser, and undetected-chromedriver failed to attach with a message
    naming neither. Ask the binary that `binary_location` points at.
    """
    import re as _re
    import shutil as _shutil
    import subprocess as _sp

    candidates = [path] if path else []
    candidates += [p for p in (_shutil.which(e) for e in
                               ("google-chrome", "chromium", "chromium-browser")) if p]
    for exe in candidates:
        try:
            out = _sp.run([exe, "--version"], capture_output=True, text=True,
                          timeout=15).stdout
        except (OSError, _sp.SubprocessError):
            continue
        m = _re.search(r"(\d+)\.", out or "")
        if m:
            return int(m.group(1))
    return None


class SeleniumBrowser:
    def __init__(self, spec: Spec, executable_path: str = "") -> None:
        self.spec = spec
        self.executable_path = executable_path
        # Any, not Optional[WebDriver]: selenium is an optional dependency
        # imported inside start(), so its types are unavailable at check time.
        self._driver: Any = None

    def start(self) -> None:
        from selenium import webdriver  # type: ignore

        ident = self.spec.identity
        firefox = self.spec.binary is Binary.FIREFOX
        opts: Any     # Firefox and Chrome option objects are unrelated types
        opts = webdriver.FirefoxOptions() if firefox else webdriver.ChromeOptions()

        # undetected-chromedriver injects its own headless handling; adding
        # --headless=new as well makes Chrome start in a mode the patched
        # driver cannot attach to ("cannot connect to chrome").
        wants_uc_early = (self.spec.engine in (Engine.SELENIUM_UC, Engine.SELENIUMBASE)
                          or self.spec.stealth is Stealth.UNDETECTED)

        if firefox:
            # Firefox takes none of Chrome's flag syntax. `--headless=new` and
            # `--window-size=W,H` are silently useless at best; geckodriver
            # rejects the launch at worst.
            if self.spec.display is Display.HEADLESS:
                opts.add_argument("-headless")
            # Firefox takes the value as a separate argument, not `--width=N`.
            opts.add_argument("--width")
            opts.add_argument(str(ident.viewport[0]))
            opts.add_argument("--height")
            opts.add_argument(str(ident.viewport[1]))
            if ident.user_agent:
                opts.set_preference("general.useragent.override", ident.user_agent)
            if ident.locale:
                opts.set_preference("intl.accept_languages", ident.locale)
            if ident.proxy:
                opts.set_preference("network.proxy.type", 1)
            if ident.profile_dir and self.spec.transport is Transport.LOCAL:
                opts.add_argument("-profile")
                opts.add_argument(ident.profile_dir)
        else:
            if self.spec.display is Display.HEADLESS and not wants_uc_early:
                opts.add_argument("--headless=new")
            opts.add_argument(f"--window-size={ident.viewport[0]},{ident.viewport[1]}")
            # Chrome will not start as root in a container without these; the
            # failure otherwise is an opaque "cannot connect to chrome".
            from browsergraph.drivers.playwright_driver import container_args, in_container
            for arg in list(self.spec.extra.get("launch_args", [])):
                opts.add_argument(arg)
            if self.spec.extra.get("container_args", in_container()):
                for arg in container_args():
                    opts.add_argument(arg)
            if ident.user_agent:
                opts.add_argument(f"--user-agent={ident.user_agent}")
            if ident.proxy:
                opts.add_argument(f"--proxy-server={ident.proxy}")
            if ident.profile_dir and self.spec.transport is Transport.LOCAL:
                opts.add_argument(f"--user-data-dir={ident.profile_dir}")

        # Resolve the binary ourselves. What is on PATH is frequently a wrapper
        # script — Ubuntu's /usr/bin/firefox is the snap launcher, and Chrome and
        # Brave ship the same shape — and a driver needs the real program. See
        # browsergraph.binaries.
        if self.executable_path:
            opts.binary_location = self.executable_path
        else:
            from browsergraph.binaries import resolve
            found = resolve(self.spec.binary)
            if found.ok:
                opts.binary_location = found.path
            elif found.wrapper:
                raise RuntimeError(
                    f"{found.explain()}. selenium cannot drive a wrapper script.")

        # The engine decides the launcher; stealth alone is not enough, since
        # engine=selenium_uc must use undetected-chromedriver whatever the
        # stealth level says.
        wants_uc = (self.spec.engine is Engine.SELENIUM_UC
                    or self.spec.stealth is Stealth.UNDETECTED)

        if self.spec.transport is Transport.SELENIUM_GRID:
            self._driver = webdriver.Remote(
                command_executor=self.spec.endpoint, options=opts)
        elif self.spec.engine is Engine.SELENIUMBASE:
            from seleniumbase import Driver  # type: ignore
            self._driver = Driver(uc=True, headless=self.spec.display is Display.HEADLESS)
        elif wants_uc:
            import undetected_chromedriver as uc  # type: ignore
            # version_main must track the installed Chrome or uc downloads a
            # mismatched patched driver and the session never attaches.
            kwargs = {"options": opts,
                      "headless": self.spec.display is Display.HEADLESS}
            major = _chrome_major(getattr(opts, "binary_location", "") or "")
            if major:
                kwargs["version_main"] = major
            # Hand it a driver built for that exact browser, on a copy: uc
            # rewrites the binary it is given to strip the cdc_ markers, and a
            # patched driver in the shared cache would then be handed to plain
            # selenium too.
            matched = self._matched_driver(opts, patchable=True)
            if matched:
                kwargs["driver_executable_path"] = matched
            self._driver = uc.Chrome(**kwargs)
        elif self.spec.binary is Binary.FIREFOX:
            # Point at a geckodriver we are permitted to terminate. Ubuntu ships
            # a snap-confined one, and a snap process cannot be signalled even by
            # its own user — so selenium's teardown fails and every session
            # leaks a driver. See browsergraph.binaries.resolve_driver.
            from browsergraph.binaries import resolve_driver
            found = resolve_driver(Binary.FIREFOX)
            if found.ok:
                from selenium.webdriver.firefox.service import Service  # type: ignore
                self._driver = webdriver.Firefox(
                    options=opts, service=Service(executable_path=found.path))
            else:
                self._driver = webdriver.Firefox(options=opts)
        else:
            # Selenium Manager resolves a driver for whichever Chrome it finds
            # first, which is not necessarily the one we just pointed it at.
            matched = self._matched_driver(opts)
            if matched:
                from selenium.webdriver.chrome.service import Service as ChromeService
                self._driver = webdriver.Chrome(
                    options=opts, service=ChromeService(executable_path=matched))
            else:
                self._driver = webdriver.Chrome(options=opts)

        self._driver.set_page_load_timeout(60)
        return None

    def _matched_driver(self, opts, *, patchable: bool = False) -> str:
        """A chromedriver built for the browser this session will launch.

        Best-effort by design: if it cannot be fetched we return "" and let the
        normal resolution happen, because a driver that might mismatch still
        beats refusing to start.
        """
        if self.spec.binary is Binary.FIREFOX:
            return ""
        try:
            from browsergraph import fetch as _fetch
            got = _fetch.matching_chromedriver(
                browser_path=getattr(opts, "binary_location", "") or "",
                binary=self.spec.binary)
            if not got.ok:
                return ""
            return _fetch.patchable_copy(got.path) if patchable else got.path
        except Exception:
            return ""

    def stop(self) -> None:
        """Quit the session, then make sure the driver process is really gone.

        `quit()` is not always enough: selenium logs and swallows a failure to
        terminate its own service, so a driver that refuses SIGTERM survives
        silently. Checking afterwards is the difference between a clean run and
        one that leaks a process per session.
        """
        driver, self._driver = self._driver, None
        if driver is None:
            return
        service = getattr(driver, "service", None)
        pid = getattr(getattr(service, "process", None), "pid", None)
        try:
            driver.quit()
        except Exception:
            pass
        self._reap(pid)

    @staticmethod
    def _reap(pid: int | None) -> None:
        """Kill a driver process that outlived its session, if we are allowed to.

        A snap-confined driver cannot be signalled and is left alone rather than
        raising — nothing here should turn a successful run into a failure at
        teardown.
        """
        if not pid:
            return
        import os
        import time
        for sig in (15, 9):
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, PermissionError):
                return              # gone, or not ours to signal
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.3)

    def goto(self, url: str) -> PageState:
        self._driver.get(url)
        return PageState(url=self._driver.current_url, title=self._driver.title, status=200)

    def state(self) -> PageState:
        return PageState(url=self._driver.current_url, title=self._driver.title, status=200)

    def _el(self, selector: str):
        from selenium.webdriver.common.by import By  # type: ignore
        found = self._driver.find_elements(By.CSS_SELECTOR, selector)
        return found[0] if found else None

    def find(self, selector: str) -> Element | None:
        el = self._el(selector)
        if el is None:
            return None
        return Element(selector=selector, handle=el, text=el.text)

    def click(self, selector: str) -> None:
        el = self._el(selector)
        if el is not None:
            el.click()

    def type(self, selector: str, text: str, cps: float = 0.0) -> None:
        el = self._el(selector)
        if el is None:
            return
        if cps <= 0:
            el.send_keys(text)
            return
        for ch in text:
            el.send_keys(ch)
            time.sleep(random.uniform(0.5 / cps, 1.5 / cps))

    def scroll(self, dy: int) -> None:
        self._driver.execute_script(f"window.scrollBy(0,{dy});")

    def wait_for(self, selector: str, timeout: float = 10.0) -> bool:
        from selenium.webdriver.common.by import By  # type: ignore
        from selenium.webdriver.support import expected_conditions as EC  # type: ignore
        from selenium.webdriver.support.ui import WebDriverWait  # type: ignore
        try:
            WebDriverWait(self._driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
            return True
        except Exception:
            return False

    def text_of(self, selector: str) -> str:
        el = self._el(selector)
        return el.text if el is not None else ""

    def html(self) -> str:
        return self._driver.page_source

    def screenshot(self, path: str) -> str:
        self._driver.save_screenshot(path)
        return path

    # --- extended capabilities (see browsergraph.capabilities) --------------

    def press(self, key: str, selector: str = "") -> None:
        from selenium.webdriver.common.by import By  # type: ignore
        from selenium.webdriver.common.keys import Keys  # type: ignore
        # Selenium names keys as constants; a caller writing "Enter" should not
        # have to know that. Fall back to the literal for ordinary characters.
        value = getattr(Keys, key.upper(), key)
        if selector:
            self._driver.find_element(By.CSS_SELECTOR, selector).send_keys(value)
        else:
            from selenium.webdriver import ActionChains  # type: ignore
            ActionChains(self._driver).send_keys(value).perform()

    def select_option(self, selector: str, value: str) -> None:
        from selenium.webdriver.common.by import By  # type: ignore
        from selenium.webdriver.support.ui import Select  # type: ignore
        element = self._driver.find_element(By.CSS_SELECTOR, selector)
        Select(element).select_by_value(value)

    def upload(self, selector: str, paths: list[str]) -> None:
        from selenium.webdriver.common.by import By  # type: ignore
        # An <input type=file> takes newline-separated paths through send_keys;
        # there is no other way in the WebDriver protocol.
        self._driver.find_element(By.CSS_SELECTOR, selector).send_keys(
            "\n".join(paths))

    def use_frame(self, selector: str | None) -> bool:
        from selenium.webdriver.common.by import By  # type: ignore
        if selector is None:
            self._driver.switch_to.default_content()
            return True
        try:
            element = self._driver.find_element(By.CSS_SELECTOR, selector)
        except Exception:
            return False
        self._driver.switch_to.frame(element)
        return True

    def cookies(self, set_to: list[dict] | None = None) -> list[dict]:
        if set_to is not None:
            for cookie in set_to:
                self._driver.add_cookie(cookie)
        return list(self._driver.get_cookies())

    def set_viewport(self, width: int, height: int) -> None:
        self._driver.set_window_size(width, height)

    def eval_js(self, script: str):
        """Evaluate and return a value, matching the Playwright adapter.

        Selenium's execute_script returns None unless the script explicitly
        returns, whereas Playwright evaluates an expression and yields its
        value. Without normalising this, `eval_js("window.scrollY")` gives a
        number on one engine and None on the other — the exact kind of drift
        the BrowserPort exists to prevent.
        """
        body = (script or "").strip().rstrip(";")
        if not body:
            return None
        looks_like_statement = ("return " in body or "\n" in body
                                or body.startswith(("function", "(", "{", "var ",
                                                    "let ", "const ")))
        return self._driver.execute_script(
            body if looks_like_statement else f"return ({body});")
