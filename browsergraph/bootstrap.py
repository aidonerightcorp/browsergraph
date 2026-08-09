"""Get a working browser, by whatever route works here.

Installing a browser is where most first runs die, and the failures are
maddeningly indirect. The Kaggle kernel that motivated this module reported

    installer exit=0
    TargetClosedError: Target page, context or browser has been closed

which is what a *successful* download of a browser that cannot start looks like.
The real cause was six lines further down in a log nobody reads:

    chrome-headless-shell: error while loading shared libraries:
    libatk-1.0.so.0: cannot open shared object file

So this module never trusts an installer's exit code. Every step is followed by
an actual launch, because launching is the only evidence that matters, and the
steps are ordered cheapest-first:

    1. probe            — maybe a browser already works; do nothing
    2. pip              — install the engine package itself
    3. browser binary   — `playwright install chromium` (a download, no root)
    4. system libraries — `playwright install-deps` / apt-get (needs root)
    5. system browser   — a Chrome/Chromium already on PATH, via executable_path
    6. give up honestly — report every step tried, with the command to fix it

Nothing here is silent: `ensure_browser` returns a report of what it ran, what
each step changed, and what is still missing.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field

from browsergraph.dimensions import Display, Engine, Spec

#: Chrome/Chromium builds commonly present on a system, best first.
SYSTEM_BROWSERS = (
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "chrome", "brave-browser", "microsoft-edge",
)

#: The shared libraries a headless Chromium needs that a slim image omits.
#: Named explicitly so the apt fallback can install them without pulling in a
#: desktop, and so the report can say which one was missing.
APT_PACKAGES = (
    "libatk1.0-0", "libatk-bridge2.0-0", "libcups2", "libdrm2", "libxkbcommon0",
    "libxcomposite1", "libxdamage1", "libxfixes3", "libxrandr2", "libgbm1",
    "libasound2", "libpango-1.0-0", "libcairo2", "libnss3", "libnspr4",
)


@dataclass
class Step:
    name: str
    ran: str = ""
    ok: bool = False
    detail: str = ""

    def __str__(self) -> str:
        mark = "ok  " if self.ok else "-   "
        tail = f"  {self.detail}" if self.detail else ""
        return f"[{mark}] {self.name}{tail}"


@dataclass
class Bootstrap:
    """What was tried, and whether a browser works now."""
    ok: bool = False
    engine: Engine | None = None
    executable_path: str = ""
    steps: list[Step] = field(default_factory=list)

    def add(self, step: Step) -> Step:
        self.steps.append(step)
        return step

    def text(self) -> str:
        lines = [str(s) for s in self.steps]
        if self.ok:
            where = self.executable_path or "playwright-bundled"
            lines.append(f"\nbrowser ready: {self.engine.value if self.engine else '?'} "
                         f"({where})")
        else:
            lines.append("\nno working browser. Remaining options:")
            lines.append("  * run as root and retry (system libraries need installing)")
            lines.append("  * apt-get install -y " + " ".join(APT_PACKAGES[:5]) + " ...")
            lines.append("  * install a system Chrome and pass executable_path=")
            lines.append("  * use engine=http, which needs no browser at all")
        return "\n".join(lines)

    def spec(self, **overrides) -> Spec:
        """A Spec that uses whatever this bootstrap found."""
        kwargs: dict = {"engine": self.engine or Engine.HTTP,
                        "display": Display.HEADLESS}
        kwargs.update(overrides)
        return Spec(**kwargs)


def _run(cmd: list[str], timeout: float = 900) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(cmd[:3])}: timed out after {timeout:.0f}s"


def missing_library(log: str) -> str:
    """The shared library named in a launch failure, if it named one."""
    import re
    m = re.search(r"error while loading shared libraries: ([^:]+):", log or "")
    return m.group(1) if m else ""


def launches(engine: Engine = Engine.PLAYWRIGHT, executable_path: str = "",
             ) -> tuple[bool, str]:
    """Can this engine actually start a browser here?

    The only question worth asking. Returns (ok, log) — the log carries the
    missing-library name that makes the failure diagnosable.
    """
    from browsergraph.drivers import build
    spec = Spec(engine=engine, display=Display.HEADLESS)
    browser = None
    try:
        browser = build(spec, executable_path=executable_path) if executable_path \
            else build(spec)
        browser.start()
        browser.goto("about:blank")
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        try:
            if browser is not None:
                browser.stop()
        except Exception:
            pass


def is_root() -> bool:
    import os
    try:
        return os.geteuid() == 0
    except AttributeError:      # pragma: no cover - non-POSIX
        return False


def ensure_browser(engine: Engine = Engine.PLAYWRIGHT, *, install: bool = True,
                   apt: bool = True, verbose: bool = False,
                   browsers: tuple[str, ...] = ("chromium",)) -> Bootstrap:
    """Do whatever is needed, here, to make `engine` launch.

    `install` and `apt` exist because "install packages onto this machine" is
    not a thing a library should do without being asked; both are opt-out.
    """
    report = Bootstrap(engine=engine)

    def say(msg: str) -> None:
        if verbose:
            print(msg, flush=True)

    # 1. Already works?
    ok, log = launches(engine)
    step = report.add(Step("browser already launches", ok=ok))
    if ok:
        report.ok = True
        say(str(step))
        return report
    step.detail = log[:150]
    say(str(step))

    # 2. The engine package itself.
    from browsergraph.dimensions import ENGINE_IMPORT, ENGINE_REQUIREMENT
    modules = ENGINE_IMPORT.get(engine, ())
    if modules:
        import importlib.util
        have = all(importlib.util.find_spec(m.split(".")[0]) is not None for m in modules)
        if not have and install:
            req = ENGINE_REQUIREMENT.get(engine, " ".join(modules))
            code, out = _run([sys.executable, "-m", "pip", "install", "-q", *req.split()])
            s = report.add(Step(f"pip install {req}", ran=req, ok=code == 0,
                                detail="" if code == 0 else out[-160:]))
            say(str(s))
        else:
            report.add(Step(f"engine package {' '.join(modules)}", ok=have))

    # 3. The browser binary — a download, no privileges needed.
    if install and engine in (Engine.PLAYWRIGHT, Engine.PLAYWRIGHT_STEALTH,
                              Engine.PATCHRIGHT, Engine.REBROWSER):
        # `browsers` is a tuple because firefox and webkit are separate
        # downloads: a caller who wants Binary.FIREFOX and gets only chromium
        # sees "Executable doesn't exist", which reads like a bug rather than a
        # missing download.
        code, out = _run([sys.executable, "-m", "playwright", "install", *browsers])
        s = report.add(Step(f"playwright install {' '.join(browsers)}", ok=code == 0,
                            detail="" if code == 0 else out[-160:]))
        say(str(s))
        ok, log = launches(engine)
        if ok:
            report.ok = True
            report.add(Step("launch after binary install", ok=True))
            return report

    # 4. System libraries. This is the step the Kaggle failure needed, and the
    #    one that requires privileges — so say so rather than failing opaquely.
    lib = missing_library(log)
    if lib or "TargetClosed" in log or "shared libraries" in log:
        report.add(Step("diagnosis", ok=False,
                        detail=f"missing shared library: {lib}" if lib
                        else "browser exits immediately at launch"))
    if apt and (lib or "TargetClosed" in log):
        if not is_root():
            report.add(Step("system libraries", ok=False,
                            detail="needs root; re-run with sudo, or install: "
                                   + " ".join(APT_PACKAGES[:4]) + " ..."))
        else:
            code, out = _run([sys.executable, "-m", "playwright", "install-deps", *browsers])
            s = report.add(Step(f"playwright install-deps {' '.join(browsers)}", ok=code == 0,
                                detail="" if code == 0 else out[-160:]))
            say(str(s))
            if code != 0:
                _run(["apt-get", "update", "-qq"], timeout=600)
                code, out = _run(["apt-get", "install", "-y", "-qq", *APT_PACKAGES])
                s = report.add(Step("apt-get install libraries", ok=code == 0,
                                    detail="" if code == 0 else out[-160:]))
                say(str(s))
            ok, log = launches(engine)
            if ok:
                report.ok = True
                report.add(Step("launch after installing libraries", ok=True))
                return report

    # 5. A browser already on the system, used directly.
    for name in SYSTEM_BROWSERS:
        path = shutil.which(name)
        if not path:
            continue
        ok, log2 = launches(engine, executable_path=path)
        s = report.add(Step(f"system browser {name}", ran=path, ok=ok,
                            detail=path if ok else log2[:120]))
        say(str(s))
        if ok:
            report.ok = True
            report.executable_path = path
            return report

    # 6. Honest failure. engine=http still works and needs none of this.
    report.add(Step("browser-less fallback available", ok=True,
                    detail="engine=http needs no browser (no JavaScript)"))
    return report
