"""Driver adapters, selected by `Spec.engine`.

Routing is driven by `ENGINE_FAMILY` rather than a hand-maintained if-chain, so
an engine declared in the capability tables cannot silently lack an adapter —
`test_every_declared_engine_can_be_routed` fails if one does.

Adapters are imported lazily so the package installs and tests with no browser
libraries present: a graph can be built, checked and mock-run anywhere.
"""
from __future__ import annotations

from browsergraph.dimensions import ENGINE_FAMILY, ENGINE_REQUIREMENT, Engine, Spec
from browsergraph.ports import BrowserPort


class DriverUnavailable(RuntimeError):
    """An engine was requested whose library or adapter is not available."""


def _requirement(engine: Engine) -> str:
    req = ENGINE_REQUIREMENT.get(engine, engine.value)
    return f"pip install {req}" if req else ""


def _loop_is_running() -> bool:
    """True when this thread already has a running asyncio loop.

    Playwright's sync API refuses to start in that case, and the loop cannot
    simply be replaced — a running loop is not swappable.

    The usual reason is legitimate and not a bug to fix: Jupyter and IPython
    run every cell inside a loop, as does any caller in async code. Routing
    those through a worker process is what makes `engine=playwright` usable
    from a notebook at all.

    (It was *also* caused by this library: a playwright launch that failed
    partway left its greenlet parked in a loop and poisoned the thread. That
    was a real bug and is fixed in PlaywrightBrowser.start, which now unwinds
    itself — see test_a_failed_start_does_not_poison_the_interpreter.)
    """
    import asyncio
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def build(spec: Spec, **kwargs) -> BrowserPort:
    """Construct the BrowserPort for this spec.

    `spec.isolated` runs the adapter in a per-family virtualenv, which is how
    engines with conflicting pins (camoufox vs playwright) coexist. Everything
    downstream sees an ordinary BrowserPort either way.
    """
    if getattr(spec, "isolated", False) and spec.engine is not Engine.MOCK:
        from browsergraph.drivers.isolated import IsolatedBrowser
        return IsolatedBrowser(spec, **kwargs)

    family = ENGINE_FAMILY.get(spec.engine)

    if family == "playwright" and _loop_is_running():
        # A worker *thread*, not a worker process. The sync API objects to a
        # loop in the calling thread, and a fresh thread has none — so this
        # needs no virtualenv and no setup, which matters because the common
        # case here is a notebook (Jupyter, Kaggle, Colab all run cells inside
        # a loop). The heavier per-engine venv stays available via
        # spec.isolated for engines that genuinely conflict.
        from browsergraph.drivers.threaded import ThreadedBrowser
        return ThreadedBrowser(spec, **kwargs)

    if family == "mock":
        from browsergraph.drivers.mock import MockBrowser
        return MockBrowser(spec, **kwargs)

    if family == "playwright":
        try:
            from browsergraph.drivers.playwright_driver import PlaywrightBrowser
        except ImportError as e:  # pragma: no cover - depends on env
            raise DriverUnavailable(
                f"{spec.engine.value} adapter unavailable: {e}. {_requirement(spec.engine)}"
            ) from e
        return PlaywrightBrowser(spec, **kwargs)

    if family == "selenium":
        try:
            from browsergraph.drivers.selenium_driver import SeleniumBrowser
        except ImportError as e:  # pragma: no cover - depends on env
            raise DriverUnavailable(
                f"{spec.engine.value} adapter unavailable: {e}. {_requirement(spec.engine)}"
            ) from e
        return SeleniumBrowser(spec, **kwargs)

    if family == "http":
        try:
            from browsergraph.drivers.http_driver import HttpBrowser
        except ImportError as e:  # pragma: no cover - depends on env
            raise DriverUnavailable(
                f"engine=http needs curl-cffi: {e}. {_requirement(spec.engine)}"
            ) from e
        return HttpBrowser(spec, **kwargs)

    if family == "cdp":
        if spec.engine is Engine.CDP:
            # `cdp` is the bare protocol with no library behind it. Playwright
            # already speaks it, so pointing at that is a real answer rather
            # than a second implementation of the same wire format.
            raise DriverUnavailable(
                "engine=cdp is the raw protocol with no client library. Use "
                "engine=playwright with transport=remote_cdp and endpoint=..., "
                "or engine=nodriver/zendriver/pydoll for a CDP-native driver. "
                "pip install nodriver")
        try:
            from browsergraph.drivers.cdp_driver import CdpBrowser
        except (ImportError, SyntaxError) as e:  # pragma: no cover - depends on env
            raise DriverUnavailable(
                f"{spec.engine.value} adapter unavailable: {e}. "
                f"{_requirement(spec.engine)}") from e
        return CdpBrowser(spec, **kwargs)

    raise DriverUnavailable(f"no adapter for engine {spec.engine.value}")
