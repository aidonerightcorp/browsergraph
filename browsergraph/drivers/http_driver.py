"""A BrowserPort with no browser.

Most pages do not need one. Server-rendered HTML answers extraction,
classification and contact-harvesting questions perfectly well, and skipping
the browser is roughly 10-100x faster and a fraction of the memory.

The reason this is not just `requests` is **TLS impersonation**. Anti-bot
vendors fingerprint the TLS handshake (JA3/JA4) and the HTTP/2 settings frame
*before any JavaScript runs*, so a stock Python client is identifiable no
matter how good its User-Agent is. `curl_cffi` presents a real browser's
handshake, which is what makes a browser-less fetch viable on defended sites.

What it cannot do is run JavaScript. `find`/`click`/`type` operate on the
served HTML only, and `eval_js` raises rather than pretending — a driver that
silently no-ops an interaction is worse than one that refuses it.
"""
from __future__ import annotations

import re
from typing import Any

from browsergraph.dimensions import Spec, Stealth
from browsergraph.ports import Element, PageState

#: curl_cffi impersonation targets, chosen by stealth level.
_IMPERSONATE = {
    Stealth.NONE: None,
    Stealth.BASIC: "chrome",
    Stealth.STEALTH_JS: "chrome",
    Stealth.UNDETECTED: "chrome",
    Stealth.FULL_FINGERPRINT: "chrome",
}

_TAG = re.compile(r"<[^>]+>")


class HttpBrowser:
    """Fetches HTML over an impersonated TLS stack. No JavaScript."""

    supports_javascript = False

    def __init__(self, spec: Spec, timeout: float = 30.0) -> None:
        self.spec = spec
        self.timeout = timeout
        self._session: Any = None
        self._html = ""
        self._url = ""
        self._status = 0

    # lifecycle
    def start(self) -> None:
        from curl_cffi import requests  # imported here so the package stays optional

        ident = self.spec.identity
        headers = {}
        if ident.user_agent:
            headers["User-Agent"] = ident.user_agent
        if ident.locale:
            headers["Accept-Language"] = ident.locale
        proxies = {"http": ident.proxy, "https": ident.proxy} if ident.proxy else None
        self._session = requests.Session(
            impersonate=_IMPERSONATE.get(self.spec.stealth, "chrome"),
            headers=headers or None, proxies=proxies,  # type: ignore[arg-type]
            timeout=self.timeout)

    def stop(self) -> None:
        try:
            if self._session is not None:
                self._session.close()
        except Exception:
            pass
        self._session = None

    # navigation
    def goto(self, url: str) -> PageState:
        resp = self._session.get(url, allow_redirects=True)
        self._url = str(resp.url)
        self._status = resp.status_code
        self._html = resp.text or ""
        return PageState(url=self._url, title=self._title(), status=self._status)

    def state(self) -> PageState:
        return PageState(url=self._url, title=self._title(), status=self._status)

    def _title(self) -> str:
        m = re.search(r"<title[^>]*>(.*?)</title>", self._html, re.I | re.S)
        return re.sub(r"\s+", " ", _TAG.sub("", m.group(1))).strip() if m else ""

    # elements — CSS over the served HTML
    def _query(self, selector: str):
        try:
            from selectolax.parser import HTMLParser
        except ImportError as e:      # pragma: no cover - optional dependency
            raise RuntimeError(
                "selector support for engine=http needs selectolax: "
                "pip install browsergraph[http]") from e
        return HTMLParser(self._html).css(selector)

    def find(self, selector: str) -> Element | None:
        hits = self._query(selector)
        if not hits:
            return None
        return Element(selector=selector, text=(hits[0].text() or "").strip())

    def click(self, selector: str) -> None:
        """Follow a link's href. Anything else is not clickable without JS."""
        hits = self._query(selector)
        if not hits:
            raise RuntimeError(f"no element matches {selector!r}")
        href = hits[0].attributes.get("href") if hits[0].attributes else None
        if not href:
            raise RuntimeError(
                f"engine=http cannot click {selector!r}: it has no href and "
                f"there is no JavaScript runtime. Use a browser engine.")
        from urllib.parse import urljoin
        self.goto(urljoin(self._url, href))

    def type(self, selector: str, text: str, cps: float = 0.0) -> None:
        raise RuntimeError("engine=http cannot type: no JavaScript runtime. "
                           "Use a browser engine for form interaction.")

    def scroll(self, dy: int) -> None:
        return None      # a fetched document has no viewport; harmless no-op

    def wait_for(self, selector: str, timeout: float = 10.0) -> bool:
        """Presence only. Nothing appears later without JS, so this is immediate."""
        return bool(self._query(selector))

    def text_of(self, selector: str) -> str:
        hits = self._query(selector)
        return (hits[0].text() or "").strip() if hits else ""

    def html(self) -> str:
        return self._html

    def screenshot(self, path: str) -> str:
        raise RuntimeError("engine=http cannot screenshot: nothing is rendered. "
                           "Use a browser engine, or capture=none.")

    def eval_js(self, script: str):
        raise RuntimeError("engine=http has no JavaScript runtime")
