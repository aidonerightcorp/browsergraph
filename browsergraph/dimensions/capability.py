"""Per-engine capability metadata.

Kept beside the enum rather than scattered through validation, so adding an
engine is one table entry per concern and `doctor` can report on it."""
from __future__ import annotations

from browsergraph.dimensions.enums import Binary, Engine

#: Which family each engine belongs to — drives adapter selection.
ENGINE_FAMILY: dict[str, str] = {
    Engine.PLAYWRIGHT: "playwright",
    Engine.PLAYWRIGHT_STEALTH: "playwright",
    Engine.PATCHRIGHT: "playwright",
    Engine.CAMOUFOX: "playwright",
    Engine.SELENIUM: "selenium",
    Engine.SELENIUM_UC: "selenium",
    Engine.SELENIUMBASE: "selenium",
    Engine.NODRIVER: "cdp",
    Engine.ZENDRIVER: "cdp",
    Engine.PYDOLL: "cdp",
    Engine.BOTASAURUS: "selenium",
    Engine.REBROWSER: "playwright",
    Engine.CDP: "cdp",
    Engine.HTTP: "http",
    Engine.MOCK: "mock",
}

#: Does this engine execute JavaScript?
#:
#: The distinction is not cosmetic: on a JavaScript-rendered page, an engine
#: without a runtime cannot be made to succeed by waiting longer, retrying, or
#: any other tuning. Escalation reads this to know that the useful next move is
#: a different engine rather than a different setting.
ENGINE_RUNS_JS: dict[str, bool] = {
    engine: family not in ("http", "mock") for engine, family in ENGINE_FAMILY.items()
}

#: pip requirement per engine, surfaced by `browsergraph doctor`.
ENGINE_REQUIREMENT: dict[str, str] = {
    Engine.PLAYWRIGHT: "playwright",
    Engine.PLAYWRIGHT_STEALTH: "playwright playwright-stealth",
    Engine.PATCHRIGHT: "patchright",
    Engine.CAMOUFOX: "camoufox[geoip]",
    Engine.SELENIUM: "selenium",
    Engine.SELENIUM_UC: "selenium undetected-chromedriver",
    Engine.SELENIUMBASE: "seleniumbase",
    Engine.NODRIVER: "nodriver",
    Engine.ZENDRIVER: "zendriver",
    Engine.PYDOLL: "pydoll-python",
    Engine.BOTASAURUS: "botasaurus",
    Engine.REBROWSER: "rebrowser-playwright",
    Engine.CDP: "websockets",
    Engine.HTTP: "curl-cffi selectolax",
    Engine.MOCK: "",
}

#: Import name to probe when checking whether an engine is usable.
#: Module(s) that must import for an engine to be usable.
#:
#: A tuple of *importable module names*, deliberately separate from
#: ENGINE_REQUIREMENT (the pip string). Conflating them made `doctor` report
#: engine:http as missing on a machine where it demonstrably worked, because
#: "curl-cffi selectolax" is a pip argument, not a module — and doctor is the
#: one component whose whole job is to be believed.
ENGINE_IMPORT: dict[str, tuple[str, ...]] = {
    Engine.PLAYWRIGHT: ("playwright",),
    Engine.PLAYWRIGHT_STEALTH: ("playwright", "playwright_stealth"),
    Engine.PATCHRIGHT: ("patchright",),
    Engine.CAMOUFOX: ("camoufox",),
    Engine.SELENIUM: ("selenium",),
    Engine.SELENIUM_UC: ("selenium", "undetected_chromedriver"),
    Engine.SELENIUMBASE: ("seleniumbase",),
    Engine.NODRIVER: ("nodriver",),
    Engine.ZENDRIVER: ("zendriver",),
    Engine.PYDOLL: ("pydoll",),                 # distributed as pydoll-python
    Engine.BOTASAURUS: ("botasaurus",),
    Engine.REBROWSER: ("rebrowser_playwright",),
    Engine.CDP: ("websockets",),
    Engine.HTTP: ("curl_cffi", "selectolax"),
    Engine.MOCK: (),
}


#: Binaries each engine can actually drive.
ENGINE_BINARIES: dict[str, tuple] = {
    Engine.PLAYWRIGHT: (Binary.BUNDLED_CHROMIUM, Binary.SYSTEM_CHROME,
                        Binary.CHROME_FOR_TESTING, Binary.BRAVE,
                        Binary.FIREFOX, Binary.WEBKIT),
    Engine.PLAYWRIGHT_STEALTH: (Binary.BUNDLED_CHROMIUM, Binary.SYSTEM_CHROME,
                                Binary.CHROME_FOR_TESTING, Binary.BRAVE),
    Engine.PATCHRIGHT: (Binary.BUNDLED_CHROMIUM, Binary.SYSTEM_CHROME,
                        Binary.CHROME_FOR_TESTING, Binary.BRAVE),
    Engine.CAMOUFOX: (Binary.FIREFOX,),
    Engine.SELENIUM: (Binary.BUNDLED_CHROMIUM, Binary.SYSTEM_CHROME,
                      Binary.CHROME_FOR_TESTING, Binary.BRAVE, Binary.FIREFOX),
    Engine.SELENIUM_UC: (Binary.SYSTEM_CHROME, Binary.CHROME_FOR_TESTING, Binary.BRAVE),
    Engine.SELENIUMBASE: (Binary.SYSTEM_CHROME, Binary.CHROME_FOR_TESTING, Binary.BRAVE),
    Engine.NODRIVER: (Binary.SYSTEM_CHROME, Binary.CHROME_FOR_TESTING, Binary.BRAVE),
    Engine.CDP: (Binary.BUNDLED_CHROMIUM, Binary.SYSTEM_CHROME,
                 Binary.CHROME_FOR_TESTING, Binary.BRAVE),
    Engine.ZENDRIVER: (Binary.SYSTEM_CHROME, Binary.CHROME_FOR_TESTING, Binary.BRAVE),
    Engine.PYDOLL: (Binary.SYSTEM_CHROME, Binary.CHROME_FOR_TESTING, Binary.BRAVE),
    Engine.BOTASAURUS: (Binary.SYSTEM_CHROME, Binary.CHROME_FOR_TESTING),
    Engine.REBROWSER: (Binary.BUNDLED_CHROMIUM, Binary.SYSTEM_CHROME,
                       Binary.CHROME_FOR_TESTING, Binary.BRAVE),
    # No browser is launched, so the binary axis does not apply.
    Engine.HTTP: tuple(Binary),
    Engine.MOCK: tuple(Binary),
}

#: Engines that already provide undetected-grade evasion themselves.
NATIVELY_UNDETECTED = (Engine.PATCHRIGHT, Engine.SELENIUM_UC, Engine.SELENIUMBASE,
                       Engine.NODRIVER, Engine.CAMOUFOX, Engine.ZENDRIVER,
                       Engine.PYDOLL, Engine.BOTASAURUS, Engine.REBROWSER,
                       # curl_cffi impersonates a real TLS/HTTP2 fingerprint,
                       # which is the layer checked before any JS runs.
                       Engine.HTTP)
