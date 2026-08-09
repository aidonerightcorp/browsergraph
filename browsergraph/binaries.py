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

    @property
    def ok(self) -> bool:
        return bool(self.path)

    def explain(self) -> str:
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

    return out


def report() -> list[Resolved]:
    """Every resolvable binary on this machine — used by `doctor`."""
    return [resolve(b) for b in (Binary.SYSTEM_CHROME, Binary.CHROME_FOR_TESTING,
                                 Binary.FIREFOX, Binary.BRAVE)]
