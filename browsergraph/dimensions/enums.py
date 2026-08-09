"""Dimension axes.

One enum per axis. Values are strings so a spec round-trips through
JSON/YAML and env vars without a custom codec."""
from __future__ import annotations

from enum import Enum


class Engine(str, Enum):
    """What drives the browser.

    Stealth-oriented engines are separate entries rather than a flag, because
    each has its own install, its own binary constraints and its own failure
    modes — collapsing them into `stealth` hid real incompatibilities.
    """
    PLAYWRIGHT = "playwright"                # baseline, fastest, most detectable
    PLAYWRIGHT_STEALTH = "playwright_stealth"  # playwright + stealth plugin
    PATCHRIGHT = "patchright"                # patched playwright, drop-in
    CAMOUFOX = "camoufox"                    # hardened firefox, strong fingerprint story
    SELENIUM = "selenium"                    # baseline webdriver
    SELENIUM_UC = "selenium_uc"              # undetected-chromedriver
    SELENIUMBASE = "seleniumbase"            # seleniumbase UC mode + tooling
    NODRIVER = "nodriver"                    # uc successor, no webdriver binary
    ZENDRIVER = "zendriver"                  # async-first undetectable, nodriver fork
    PYDOLL = "pydoll"                        # chromium via CDP, no webdriver binary
    BOTASAURUS = "botasaurus"                # batteries-included scraping framework
    REBROWSER = "rebrowser"                  # patched playwright runtime
    CDP = "cdp"                              # raw DevTools protocol
    HTTP = "http"                            # no browser: TLS-impersonated fetch
    MOCK = "mock"                            # in-memory, for tests and dry runs


class Binary(str, Enum):
    """Which browser executable."""
    BUNDLED_CHROMIUM = "bundled_chromium"
    SYSTEM_CHROME = "system_chrome"
    CHROME_FOR_TESTING = "chrome_for_testing"
    BRAVE = "brave"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


class Transport(str, Enum):
    """Where the browser actually runs."""
    LOCAL = "local"
    REMOTE_CDP = "remote_cdp"
    SELENIUM_GRID = "selenium_grid"
    BROWSERLESS = "browserless"
    KASM = "kasm"


class Display(str, Enum):
    """How it is displayed."""
    HEADLESS = "headless"
    HEADED = "headed"              # real X display, e.g. :0
    XVFB = "xvfb"                  # virtual framebuffer
    VNC = "vnc"


class Stealth(str, Enum):
    """Anti-detection posture."""
    NONE = "none"
    BASIC = "basic"                # UA + viewport + webdriver flag
    STEALTH_JS = "stealth_js"      # navigator patches
    UNDETECTED = "undetected"      # undetected-chromedriver / patchright
    FULL_FINGERPRINT = "full_fingerprint"


class LLMControl(str, Enum):
    """How much the model decides."""
    NONE = "none"                  # fully scripted
    SELECTOR = "selector"          # model resolves selectors when they fail
    VERIFY = "verify"              # model checks the outcome
    PLAN = "plan"                  # model plans the steps up front
    AGENT = "agent"                # model drives the loop


class Preprocess(str, Enum):
    """How page HTML is reduced before a model sees it. See preprocess.py."""
    RAW = "raw"
    CLEAN_HTML = "clean_html"
    TEXT = "text"
    READABILITY = "readability"
    DOM_SKELETON = "dom_skeleton"
    INTERACTIVE = "interactive"
    ACCESSIBILITY = "accessibility"
    MARKDOWN = "markdown"


class Vision(str, Enum):
    """When a screenshot is sent to a multimodal model. See vision.py."""
    NONE = "none"
    ON_FAILURE = "on_failure"
    ALWAYS = "always"
    ANNOTATED = "annotated"


class Capture(str, Enum):
    """What evidence a run records. See drivers + doctor.

    Video is produced by Playwright's bundled ffmpeg, so it needs no system
    install — but that encoder only emits **webm**. Converting to mp4 does
    require system ffmpeg.
    """
    NONE = "none"
    SCREENSHOT_ON_FAILURE = "screenshot_on_failure"
    VIDEO = "video"
    TRACE = "trace"                 # playwright trace: dom snapshots + network
    VIDEO_AND_TRACE = "video_and_trace"
