"""Getting a binary that is not on this machine yet.

`binaries.py` answers *where is it*. This answers *how do I get one*, which is a
different question with a much worse failure mode: the wrong version of a driver
does not report itself as the wrong version. It reports

    session not created: This version of ChromeDriver only supports Chrome
    version 151. Current browser version is 150.0.7871.128

if you are lucky, and if you are not, it attaches, drives, and misbehaves in
ways that look like the site's fault.

This host is a good example of why matching cannot be assumed. It has

    Chromium 150.0.7871.128 (snap)   Google Chrome 148.0.7778.178   chromedriver: absent

— three states at once, and the current Chrome for Testing stable is 151. A tool
that downloads "the latest chromedriver" is wrong here in every direction, and
which browser you are pointed at decides which kind of wrong you get. So the
rule is: **read the version off the binary that will actually be launched, and
fetch the driver for that**, rather than off PATH, off the newest release, or
off a guess.

Everything lands in the user's cache, never a system path. Nothing here is
required — a fetch that fails returns its reason as data, because the caller
usually has a fallback and would rather take it than die.
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import urllib.request
import zipfile
from dataclasses import dataclass, field

#: Downloads live here. Under the user's cache — this library never writes to a
#: system location, because a tool that needs root to work does not get used.
CACHE = "~/.cache/browsergraph/bin"

#: A download that has not finished in this long is not going to.
TIMEOUT = 300


def cache_dir() -> str:
    path = os.path.expanduser(CACHE)
    os.makedirs(path, exist_ok=True)
    return path


def platform_key() -> str:
    """The platform slug Google, Mozilla and Microsoft each spell differently."""
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if platform.system() == "Darwin":
        return "mac-arm64" if arm else "mac-x64"
    if platform.system() == "Windows":
        return "win64"
    return "linux-arm64" if arm else "linux64"


# --- what we can get, and from where ----------------------------------------

@dataclass
class Plan:
    """What a fetch *would* do, without doing any of it.

    Kept separate from the fetch so that "what would you download, and from
    where" is answerable — and testable — without a network round trip and
    without 150MB landing on someone's disk.
    """
    what: str
    url: str = ""
    version: str = ""
    source: str = ""
    member: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.url) and not self.error


@dataclass
class Fetched:
    what: str
    path: str = ""
    version: str = ""
    source: str = ""
    error: str = ""
    cached: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.path) and not self.error

    def to_dict(self) -> dict:
        return {"what": self.what, "path": self.path, "version": self.version,
                "source": self.source, "cached": self.cached, "error": self.error}


def _json(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# --- Chrome for Testing ------------------------------------------------------
#
# Google publishes, for every milestone, a chrome and a chromedriver built from
# the same revision. That pairing is the entire point: it is the only source
# where "matched" is a guarantee rather than a hope.

CFT = "https://googlechromelabs.github.io/chrome-for-testing/"
CFT_MILESTONES = CFT + "latest-versions-per-milestone-with-downloads.json"
CFT_LATEST = CFT + "last-known-good-versions-with-downloads.json"

#: Chrome for Testing's name for each thing, and the file inside the archive.
CFT_PRODUCTS = {
    "chrome": "chrome",
    "chromedriver": "chromedriver",
    "chrome-headless-shell": "chrome-headless-shell",
}


def cft_plan(what: str, milestone: str | int = "", channel: str = "Stable") -> Plan:
    """Where to get `what`, matched to `milestone` if one is given.

    With no milestone this takes the current stable, which is the right default
    only when nothing is installed yet. Every caller that already has a browser
    should pass its major version.
    """
    plan = Plan(what=what, source="chrome-for-testing")
    if what not in CFT_PRODUCTS:
        plan.error = f"chrome-for-testing has no {what!r}"
        return plan
    try:
        if milestone:
            doc = _json(CFT_MILESTONES)
            entry = doc.get("milestones", {}).get(str(milestone))
            if not entry:
                have = sorted(doc.get("milestones", {}), key=lambda v: int(v))
                plan.error = (f"no Chrome for Testing build for milestone {milestone}"
                              f" (have {have[0]}-{have[-1]})")
                return plan
        else:
            entry = _json(CFT_LATEST)["channels"][channel]
    except Exception as e:
        plan.error = f"{type(e).__name__}: {e}"
        return plan

    plan.version = entry.get("version", "")
    want = platform_key()
    for item in entry.get("downloads", {}).get(CFT_PRODUCTS[what], []):
        if item.get("platform") == want:
            plan.url = item["url"]
            plan.member = what
            return plan
    plan.error = f"{what} {plan.version} is not published for {want}"
    return plan


# --- geckodriver -------------------------------------------------------------

GECKO_LATEST = "https://api.github.com/repos/mozilla/geckodriver/releases/latest"
#: Used when the releases API cannot be reached; a known-good pin beats nothing.
GECKO_PIN = "0.37.1"
GECKO_ASSET = {
    "linux64": "linux64.tar.gz", "linux-arm64": "linux-aarch64.tar.gz",
    "mac-x64": "macos.tar.gz", "mac-arm64": "macos-aarch64.tar.gz",
    "win64": "win64.zip",
}


def gecko_plan(version: str = "") -> Plan:
    plan = Plan(what="geckodriver", source="mozilla/geckodriver", member="geckodriver")
    suffix = GECKO_ASSET.get(platform_key())
    if not suffix:
        plan.error = f"no geckodriver for {platform_key()}"
        return plan
    if not version:
        try:
            version = _json(GECKO_LATEST).get("tag_name", "").lstrip("v") or GECKO_PIN
        except Exception:
            version = GECKO_PIN          # pinned fallback, not a failure
    plan.version = version
    plan.url = ("https://github.com/mozilla/geckodriver/releases/download/"
                f"v{version}/geckodriver-v{version}-{suffix}")
    return plan


# --- msedgedriver ------------------------------------------------------------

EDGE_STABLE = "https://msedgedriver.microsoft.com/LATEST_STABLE"
EDGE_ASSET = {"linux64": "edgedriver_linux64.zip", "mac-x64": "edgedriver_mac64.zip",
              "mac-arm64": "edgedriver_arm64.zip", "win64": "edgedriver_win64.zip"}


def edge_plan(version: str = "") -> Plan:
    plan = Plan(what="msedgedriver", source="microsoft", member="msedgedriver")
    asset = EDGE_ASSET.get(platform_key())
    if not asset:
        plan.error = f"no msedgedriver for {platform_key()}"
        return plan
    if not version:
        try:
            with urllib.request.urlopen(EDGE_STABLE, timeout=30) as r:
                raw = r.read()
            # Microsoft serves this as UTF-16 with a BOM. Decoding it as UTF-8
            # yields "1\x005\x001\x00..." — which parses as a version number if
            # you are careless with a regex, and is off by everything.
            version = raw.decode("utf-16").strip()
        except Exception as e:
            plan.error = f"could not read the stable version: {type(e).__name__}: {e}"
            return plan
    plan.version = version
    plan.url = f"https://msedgedriver.microsoft.com/{version}/{asset}"
    return plan


# --- Firefox itself ----------------------------------------------------------

FIREFOX_OS = {"linux64": "linux64", "linux-arm64": "linux64",
              "mac-x64": "osx", "mac-arm64": "osx", "win64": "win64"}


def firefox_plan(version: str = "latest") -> Plan:
    """A full Firefox, for a machine that has none.

    Mozilla's redirector rather than a versioned path, so this does not go stale.
    It is a ~85MB download; nothing calls it implicitly.
    """
    plan = Plan(what="firefox", source="mozilla", member="firefox")
    key = FIREFOX_OS.get(platform_key())
    if not key:
        plan.error = f"no Firefox build for {platform_key()}"
        return plan
    product = "firefox-latest-ssl" if version == "latest" else f"firefox-{version}"
    plan.version = version
    plan.url = f"https://download.mozilla.org/?product={product}&os={key}&lang=en-US"
    return plan


#: Everything fetchable, and how to work out its URL. Adding a source here makes
#: it available to `fetch`, the CLI and `report` at once.
SOURCES = {
    "chrome": lambda **kw: cft_plan("chrome", kw.get("milestone", "")),
    "chromedriver": lambda **kw: cft_plan("chromedriver", kw.get("milestone", "")),
    "chrome-headless-shell":
        lambda **kw: cft_plan("chrome-headless-shell", kw.get("milestone", "")),
    "geckodriver": lambda **kw: gecko_plan(kw.get("version", "")),
    "msedgedriver": lambda **kw: edge_plan(kw.get("version", "")),
    "firefox": lambda **kw: firefox_plan(kw.get("version", "latest")),
}


def catalogue() -> list[str]:
    return sorted(SOURCES)


def plan(what: str, **kw) -> Plan:
    """Where `what` would come from, without fetching it."""
    make = SOURCES.get(what)
    if make is None:
        return Plan(what=what,
                    error=f"unknown binary {what!r}; known: {', '.join(catalogue())}")
    return make(**kw)  # type: ignore[operator]


# --- unpacking ---------------------------------------------------------------

def _unpack(blob: bytes, dest: str) -> None:
    """Extract an archive by what it *is*, not what its URL is called.

    Mozilla's redirector, in particular, hands back a URL with no useful suffix.
    """
    if blob[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            # ZipFile.extractall sanitises member paths, but drops the execute
            # bit — every driver here arrives from a zip unrunnable unless the
            # mode is restored from the archive's own external_attr.
            for info in zf.infolist():
                out = zf.extract(info, dest)
                mode = info.external_attr >> 16
                if mode & 0o111:
                    os.chmod(out, mode & 0o777)
        return
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(blob)) as tf:   # sniffs gz/xz/bz2
        try:
            tf.extractall(dest, filter="data")
        except TypeError:
            # `filter` predates neither 3.10 nor some 3.11 patch releases. The
            # old geckodriver fetch passed it unconditionally, so on those
            # interpreters it raised, was swallowed, and looked like "download
            # failed" forever.
            tf.extractall(dest)   # noqa: S202


def _find(root: str, name: str) -> str:
    """The executable called `name` somewhere under `root`."""
    exact, near = "", ""
    for path in pathlib.Path(root).rglob("*"):
        if not path.is_file():
            continue
        base = path.name
        if base == name or base == name + ".exe":
            exact = exact or str(path)
        elif base.startswith(name) and not path.suffix:
            near = near or str(path)
    return exact or near


def fetch(what: str, *, force: bool = False, **kw) -> Fetched:
    """Download `what` into the cache and return where it landed.

    Already-cached versions are reused: the version is part of the path, so a
    matched driver never silently becomes an unmatched one.
    """
    spec = plan(what, **kw)
    if not spec.ok:
        return Fetched(what=what, source=spec.source, error=spec.error or "no URL")

    root = os.path.join(cache_dir(), f"{what}-{spec.version}")
    exe = _find(root, spec.member or what) if os.path.isdir(root) else ""
    if exe and not force:
        return Fetched(what=what, path=exe, version=spec.version,
                       source=spec.source, cached=True)

    try:
        with urllib.request.urlopen(spec.url, timeout=TIMEOUT) as r:
            blob = r.read()
        tmp = root + ".part"
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
        _unpack(blob, tmp)
        shutil.rmtree(root, ignore_errors=True)
        os.replace(tmp, root)
    except Exception as e:
        return Fetched(what=what, source=spec.source, version=spec.version,
                       error=f"{type(e).__name__}: {e}")

    exe = _find(root, spec.member or what)
    if not exe:
        return Fetched(what=what, source=spec.source, version=spec.version,
                       error=f"downloaded {what} but found no {spec.member!r} inside")
    os.chmod(exe, 0o755)
    return Fetched(what=what, path=exe, version=spec.version, source=spec.source)


# --- the matched-driver problem ---------------------------------------------

def binary_version(path: str) -> str:
    """The version string a browser reports about itself.

    Asking the binary is the only reliable source. A package name, a PATH entry
    and a snap revision all disagree with it on this machine.
    """
    if not path or not os.path.exists(path):
        return ""
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True,
                             timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r"(\d+\.\d+\.\d+(?:\.\d+)?)", (out.stdout or "") + (out.stderr or ""))
    return m.group(1) if m else ""


def major(version: str) -> str:
    return version.split(".")[0] if version else ""


def matching_chromedriver(binary: str = "", *, browser_path: str = "") -> Fetched:
    """A chromedriver built for the Chrome that will actually be launched.

    This is the fix for the failure documented in ENGINES.md as a
    "chromedriver/snap version skew". It was never really about snap: it is that
    Selenium Manager resolves a driver for whichever Chrome it finds first,
    while the spec may point at a different one. On a machine with Chromium 150
    and Chrome 148 installed side by side, exactly one of those two engines can
    work by luck, and which one changes with PATH.
    """
    from browsergraph.binaries import resolve

    path = browser_path
    if not path and binary:
        path = resolve(binary).path
    version = binary_version(path)
    if not version:
        return Fetched(what="chromedriver",
                       error=f"could not read a version from {path or binary!r}")
    got = fetch("chromedriver", milestone=major(version))
    if got.ok and major(got.version) != major(version):
        got.error = (f"asked for chromedriver {major(version)}, "
                     f"got {got.version} — refusing the mismatch")
        got.path = ""
    return got


def patchable_copy(path: str) -> str:
    """A private copy of a driver, for a tool that rewrites the binary.

    undetected-chromedriver strips the `cdc_` markers out of whatever driver it
    is handed, in place. Sharing the cached original with it would mean plain
    selenium later gets a patched driver it never asked for — and, worse, that
    re-fetching cannot repair, since the file is present and the right version.
    """
    if not path:
        return ""
    dest = os.path.join(cache_dir(), "patched")
    os.makedirs(dest, exist_ok=True)
    # Name the copy after the version directory it came from, so two browsers
    # at different majors do not share one patched driver.
    version = binary_version(path) or "unknown"
    out = os.path.join(dest, f"{os.path.basename(path)}-{version}")
    try:
        if not os.path.exists(out):
            shutil.copy2(path, out)
            os.chmod(out, 0o755)
        return out
    except OSError:
        return path


# --- reporting ---------------------------------------------------------------

@dataclass
class Status:
    what: str
    cached: str = ""
    plan: Plan = field(default_factory=lambda: Plan(what=""))


def cached(what: str, version: str = "") -> str:
    """A previously fetched copy, if there is one.

    `version` may be a full version or just a major: asking for "148" finds
    chromedriver-148.0.7778.178. Without that, a caller checking "do I have a
    driver for this browser" gets whichever version sorted last and concludes it
    has the wrong one while the right one sits in the same directory.
    """
    root = cache_dir()
    best = ""
    for entry in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if not entry.startswith(f"{what}-"):
            continue
        have = entry[len(what) + 1:]
        if version and have != version and not have.startswith(version + "."):
            continue
        found = _find(os.path.join(root, entry), what)
        if found:
            best = found
    return best


def report(*, offline: bool = True) -> str:
    """What is already downloaded, and what each thing would come from.

    Defaults to offline: a status command should not perform six network
    requests to tell you what is on your own disk.
    """
    lines = []
    for what in catalogue():
        have = cached(what)
        if have:
            lines.append(f"  [ok  ] {what:<22} {have}")
        elif offline:
            lines.append(f"  [    ] {what:<22} not fetched  "
                         f"(browsergraph fetch {what})")
        else:
            spec = plan(what)
            lines.append(f"  [    ] {what:<22} {spec.version or '?':<16} "
                         + (spec.url[:70] if spec.ok else f"unavailable: {spec.error}"))
    return "\n".join(lines)
