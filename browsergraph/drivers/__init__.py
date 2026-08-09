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

    if family == "cdp":
        raise DriverUnavailable(
            f"{spec.engine.value} has no adapter yet — the raw CDP/nodriver "
            f"family is declared but not implemented. Use engine=playwright "
            f"with transport=remote_cdp for a DevTools connection.")

    raise DriverUnavailable(f"no adapter for engine {spec.engine.value}")
