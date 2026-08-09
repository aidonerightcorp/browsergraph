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


def _chrome_major() -> int | None:
    """Installed Chrome's major version, for undetected-chromedriver."""
    import re as _re
    import shutil as _shutil
    import subprocess as _sp
    for exe in ("google-chrome", "chromium", "chromium-browser"):
        path = _shutil.which(exe)
        if not path:
            continue
        try:
            out = _sp.run([path, "--version"], capture_output=True, text=True,
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
            major = _chrome_major()
            if major:
                kwargs["version_main"] = major
            self._driver = uc.Chrome(**kwargs)
        elif self.spec.binary is Binary.FIREFOX:
            self._driver = webdriver.Firefox(options=opts)
        else:
            self._driver = webdriver.Chrome(options=opts)

        self._driver.set_page_load_timeout(60)

    def stop(self) -> None:
        try:
            if self._driver:
                self._driver.quit()
        except Exception:
            pass

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
