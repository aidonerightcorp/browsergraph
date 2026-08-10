"""Finding a browser executable that a driver will actually accept.

`shutil.which("firefox")` is not an answer. On Ubuntu it returns
`/usr/bin/firefox`, which is a **shell script** wrapping the snap, and
geckodriver rejects it with

    InvalidArgumentException: binary is not a Firefox executable

That message names neither the cause nor the fix, and the fix — the real ELF
binary buried at `/snap/firefox/current/usr/lib/firefox/firefox` — is not
guessable. The same shape of problem appears with Chrome wrappers, flatpaks and
`/etc/alternatives` symlinks.

So resolution here means: find something that is a real executable program, not
merely a file on PATH with the right name.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from browsergraph.dimensions import Binary

#: Candidate absolute paths per binary, in preference order. Real binaries
#: first, wrappers last — a wrapper works for launching a browser by hand and
#: fails for driving one.
CANDIDATES: dict[str, tuple[str, ...]] = {
    Binary.FIREFOX: (
        "/snap/firefox/current/usr/lib/firefox/firefox",
        "/usr/lib/firefox/firefox",
        "/usr/lib/firefox-esr/firefox-esr",
        "/opt/firefox/firefox",
        "/Applications/Firefox.app/Contents/MacOS/firefox",
        "/var/lib/flatpak/app/org.mozilla.firefox/current/active/files/lib/firefox/firefox",
    ),
    Binary.SYSTEM_CHROME: (
        "/opt/google/chrome/chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ),
    Binary.CHROME_FOR_TESTING: (
        "/usr/lib/chromium-browser/chromium-browser",
        "/usr/lib/chromium/chromium",
        "/snap/chromium/current/usr/lib/chromium-browser/chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ),
    Binary.BRAVE: (
        "/opt/brave.com/brave/brave",
        "/usr/bin/brave-browser-stable",
        "/usr/bin/brave-browser",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ),
}

#: PATH names to fall back to, per binary.
ON_PATH: dict[str, tuple[str, ...]] = {
    Binary.FIREFOX: ("firefox", "firefox-esr"),
    Binary.SYSTEM_CHROME: ("google-chrome-stable", "google-chrome", "chrome"),
    Binary.CHROME_FOR_TESTING: ("chromium", "chromium-browser"),
    Binary.BRAVE: ("brave-browser", "brave"),
}


#: Where Playwright keeps the browsers it downloads, and the shape of each.
#:
#: Searching only the system locations made `doctor` report `binary:firefox` and
#: `binary:webkit` as missing on a machine that had just driven both of them —
#: they were present, merely not where a package manager would put them. A
#: browser is "available" wherever it legitimately lives.
PLAYWRIGHT_GLOBS: dict[str, tuple[str, ...]] = {
    Binary.FIREFOX: ("firefox-*/firefox/firefox",),
    Binary.WEBKIT: ("webkit-*/pw_run.sh", "webkit-*/minibrowser-*/MiniBrowser"),
    Binary.BUNDLED_CHROMIUM: (
        "chromium-*/chrome-linux/chrome",
        "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
    ),
}


def playwright_cache() -> str:
    """The directory Playwright downloads browsers into on this platform."""
    import os
    import sys
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if override and override != "0":
        return override
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Caches/ms-playwright")
    if sys.platform.startswith("win"):
        return os.path.expandvars(r"%USERPROFILE%\AppData\Local\ms-playwright")
    return os.path.expanduser("~/.cache/ms-playwright")


def find_bundled(binary: Binary | str) -> str:
    """A Playwright-managed build of this browser, if one has been downloaded.

    WebKit ships behind a launcher script rather than a bare executable, which
    `is_real_program` would reject — correctly, for a driver that must supervise
    the process, and unhelpfully for the question "is WebKit installed". Only
    Playwright drives WebKit, and it knows what to do with its own launcher.
    """
    import glob
    import os
    root = playwright_cache()
    for pattern in PLAYWRIGHT_GLOBS.get(binary, ()):
        for hit in sorted(glob.glob(os.path.join(root, pattern)), reverse=True):
            if os.access(hit, os.X_OK):
                return hit
    return ""


def is_real_program(path: str) -> bool:
    """True for an executable that is not a shell-script wrapper.

    A wrapper launches a browser perfectly well from a terminal and is useless
    to a driver, which needs to supervise the actual process. Reading the first
    two bytes distinguishes them: ELF binaries and Mach-O start with a magic
    number, scripts start with `#!`.
    """
    if not path or not os.path.isfile(path) or not os.access(path, os.X_OK):
        return False
    try:
        with open(path, "rb") as fh:
            head = fh.read(2)
    except OSError:
        return False
    return head != b"#!"


@dataclass
class Resolved:
    binary: str
    path: str = ""
    wrapper: str = ""       # what was on PATH, when it was unusable
    bundled: bool = False   # found in Playwright's cache, not a system location

    @property
    def ok(self) -> bool:
        return bool(self.path)

    def explain(self) -> str:
        if self.ok and self.bundled:
            return f"{self.binary}: {self.path} (playwright-managed)"
        if self.ok and self.wrapper:
            return (f"{self.binary}: using {self.path} "
                    f"(PATH had {self.wrapper}, a wrapper script a driver cannot use)")
        if self.ok:
            return f"{self.binary}: {self.path}"
        if self.wrapper:
            return (f"{self.binary}: only found {self.wrapper}, which is a wrapper "
                    f"script; install a real build or set executable_path=")
        return f"{self.binary}: not found"


def resolve(binary: Binary | str) -> Resolved:
    """The path a driver can actually launch, or an explanation of why not."""
    key = getattr(binary, "value", binary)
    out = Resolved(binary=str(key))

    for path in CANDIDATES.get(binary, ()):
        if is_real_program(path):
            out.path = path
            break

    for name in ON_PATH.get(binary, ()):
        found = shutil.which(name)
        if not found:
            continue
        if not out.path and is_real_program(found):
            out.path = found
            break
        if not out.wrapper and not is_real_program(found):
            out.wrapper = found            # recorded so the report can say why

    if not out.path:
        # Nothing installed system-wide, but Playwright may have downloaded one.
        bundled = find_bundled(binary)
        if bundled:
            out.path, out.bundled = bundled, True

    return out


def report() -> list[Resolved]:
    """Every resolvable binary on this machine — used by `doctor`."""
    return [resolve(b) for b in (Binary.SYSTEM_CHROME, Binary.CHROME_FOR_TESTING,
                                 Binary.FIREFOX, Binary.WEBKIT, Binary.BRAVE,
                                 Binary.BUNDLED_CHROMIUM)]


# --- drivers ----------------------------------------------------------------
#
# The same problem as browsers, with a sharper edge. Ubuntu's geckodriver is a
# snap, and a snap-confined process **cannot be signalled by its own user**:
# `os.kill(pid, 0)` is permitted and `os.kill(pid, SIGTERM)` raises
# PermissionError, so selenium's teardown fails and the process survives.
#
# Every Firefox session therefore leaks one geckodriver. Measured: 46 of them
# accumulated across one test run, until a headed Firefox launch failed for
# want of resources. A long crawl would exhaust the machine.
#
# Selenium Manager will not help — it prefers whatever is on PATH and its own
# advice is "delete the driver in PATH", which is not available for a
# system-managed snap. So a killable driver is fetched and cached instead.

#: Where fetched drivers live. Under the user's cache, never the system.
DRIVER_CACHE = "~/.cache/browsergraph/drivers"

GECKODRIVER_VERSION = "0.37.1"
GECKODRIVER_URL = ("https://github.com/mozilla/geckodriver/releases/download/"
                   "v{v}/geckodriver-v{v}-linux64.tar.gz")


def is_confined(path: str) -> bool:
    """Is this executable snap-confined, and therefore unkillable by us?"""
    return "/snap/" in (path or "")


def cached_driver(name: str = "geckodriver") -> str:
    import os
    path = os.path.expanduser(f"{DRIVER_CACHE}/{name}")
    return path if is_real_program(path) else ""


def fetch_geckodriver(version: str = GECKODRIVER_VERSION) -> str:
    """Download a geckodriver we are allowed to terminate.

    Returns the path, or "" if it could not be fetched — in which case the
    caller falls back to whatever is on PATH and accepts the leak, because a
    leaked process is much better than no browser.
    """
    import io
    import os
    import tarfile
    import urllib.request

    dest = os.path.expanduser(DRIVER_CACHE)
    os.makedirs(dest, exist_ok=True)
    try:
        with urllib.request.urlopen(GECKODRIVER_URL.format(v=version), timeout=120) as r:
            blob = r.read()
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
            tf.extractall(dest, filter="data")
        exe = os.path.join(dest, "geckodriver")
        os.chmod(exe, 0o755)
        return exe if is_real_program(exe) else ""
    except Exception:
        return ""


def resolve_driver(binary: Binary | str, *, fetch: bool = True) -> Resolved:
    """A driver executable that can be started *and stopped*.

    Only Firefox needs this: chromedriver is not shipped as a snap, and its
    teardown works.
    """
    key = getattr(binary, "value", binary)
    out = Resolved(binary=f"{key}-driver")
    if key != Binary.FIREFOX.value:
        return out

    cached = cached_driver()
    if cached:
        out.path = cached
        return out

    found = shutil.which("geckodriver")
    if found and not is_confined(found) and is_real_program(found):
        out.path = found
        return out

    out.wrapper = found or ""
    if fetch:
        fetched = fetch_geckodriver()
        if fetched:
            out.path = fetched
    return out
