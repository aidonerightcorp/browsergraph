"""Prerequisite checks.

Browser automation fails for environmental reasons far more often than logical
ones — a missing driver, no display, no browser binary, an unreachable model.
`doctor` reports what is actually available so a failure is diagnosed before a
run rather than during one.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from browsergraph.dimensions import (
    ENGINE_IMPORT,
    ENGINE_REQUIREMENT,
    Engine,
    LLMConfig,
)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, *checks: Check) -> None:
        self.checks.extend(checks)

    def text(self) -> str:
        lines = []
        for c in self.checks:
            mark = "ok  " if c.ok else "MISS"
            lines.append(f"[{mark}] {c.name:<28} {c.detail}")
            if not c.ok and c.fix:
                lines.append(f"       fix: {c.fix}")
        return "\n".join(lines)


def _importable(mods: tuple[str, ...] | str) -> bool:
    """True when every named module can be found.

    Takes a tuple because some engines need more than one — `engine=http` needs
    both curl_cffi and selectolax, and reporting it usable with only one of them
    would be a lie in the direction that costs the most.
    """
    names = (mods,) if isinstance(mods, str) else tuple(mods)
    for mod in names:
        if not mod:
            continue
        try:
            if importlib.util.find_spec(mod) is None:
                return False
        except (ImportError, ValueError):
            return False
    return True


def check_python() -> Check:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 10)
    return Check("python>=3.10", ok, f"{v.major}.{v.minor}.{v.micro}",
                 "install python 3.10 or newer")


def check_engines() -> list[Check]:
    out = []
    for engine in Engine:
        if engine is Engine.MOCK:
            continue
        mods = ENGINE_IMPORT.get(engine, ())
        ok = _importable(mods)
        out.append(Check(
            f"engine:{engine.value}", ok,
            ("import " + " ".join(mods)) if mods else "",
            f"pip install {ENGINE_REQUIREMENT.get(engine, engine.value)}"))
    return out


def check_browsers() -> list[Check]:
    out = []
    # Resolved rather than `which`, because what is on PATH is very often a
    # wrapper script a driver cannot launch — Ubuntu's /usr/bin/firefox is the
    # snap launcher, and Chrome and Brave ship the same shape. Reporting those
    # as present is exactly the kind of confident-but-wrong answer this project
    # exists to avoid. See browsergraph.binaries.
    from browsergraph.binaries import report as binary_report
    for res in binary_report():
        label = res.binary.replace("_", " ")
        out.append(Check(f"binary:{label}", res.ok,
                         res.explain().split(": ", 1)[-1],
                         f"install {label} or set executable_path"))
    pw_cache = os.path.expanduser("~/.cache/ms-playwright")
    have = os.path.isdir(pw_cache) and bool(os.listdir(pw_cache))
    out.append(Check("binary:playwright-bundled", have,
                     pw_cache if have else "no downloaded browsers",
                     "playwright install chromium"))
    return out


def check_display() -> list[Check]:
    disp = os.environ.get("DISPLAY", "")
    out = [Check("display:X", bool(disp), disp or "DISPLAY unset",
                 "run headless, or export DISPLAY=:0")]
    out.append(Check("display:xvfb", bool(shutil.which("Xvfb")),
                     shutil.which("Xvfb") or "not installed",
                     "apt install xvfb  (needed for unattended headed runs)"))
    return out


def check_media() -> list[Check]:
    """Video needs an encoder — playwright ships one, so system ffmpeg is optional."""
    out = []
    bundled = ""
    cache = os.path.expanduser("~/.cache/ms-playwright")
    if os.path.isdir(cache):
        for entry in sorted(os.listdir(cache)):
            candidate = os.path.join(cache, entry, "ffmpeg-linux")
            if entry.startswith("ffmpeg") and os.path.exists(candidate):
                bundled = candidate
                break
    out.append(Check("video:playwright-ffmpeg", bool(bundled),
                     bundled or "not downloaded",
                     "playwright install chromium  (bundles the encoder)"))
    system = shutil.which("ffmpeg")
    out.append(Check("ffmpeg:system", bool(system),
                     system or "not installed (only needed for webm->mp4)",
                     "apt install ffmpeg  (bundled encoder emits webm only)"))
    return out


def check_ollama(cfg: LLMConfig | None = None) -> list[Check]:
    cfg = cfg or LLMConfig(
        host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        model=os.environ.get("OLLAMA_MODEL", ""),
        api_key=os.environ.get("OLLAMA_API_KEY", ""),
    )
    out: list[Check] = []
    models: list[str] = []
    try:
        req = urllib.request.Request(f"{cfg.host.rstrip('/')}/api/tags")
        if cfg.api_key:
            req.add_header("Authorization", f"Bearer {cfg.api_key}")
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read().decode())
        models = [m.get("name", "") for m in body.get("models", [])]
        out.append(Check("ollama:reachable", True, f"{cfg.host} ({len(models)} models)"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        out.append(Check("ollama:reachable", False,
                         f"{cfg.host}: {type(e).__name__}",
                         "start ollama, or set OLLAMA_HOST; LLM nodes are optional"))
        return out

    if cfg.model:
        ok = any(cfg.model == m or m.startswith(f"{cfg.model}:") for m in models)
        out.append(Check("ollama:model", ok,
                         cfg.model if ok else f"{cfg.model} not pulled",
                         f"ollama pull {cfg.model}"))
    else:
        out.append(Check("ollama:model", bool(models),
                         ", ".join(models[:4]) or "none pulled",
                         "set OLLAMA_MODEL, e.g. OLLAMA_MODEL=glm-5.2"))
    return out


def run_all(cfg: LLMConfig | None = None) -> Report:
    rep = Report()
    rep.add(check_python())
    rep.add(*check_engines())
    rep.add(*check_browsers())
    rep.add(*check_display())
    rep.add(*check_media())
    rep.add(*check_ollama(cfg))
    return rep


def available_engines() -> list[Engine]:
    """Engines usable right now — what a sweep should actually run."""
    return [Engine.MOCK] + [
        e for e in Engine
        if e is not Engine.MOCK and _importable(ENGINE_IMPORT.get(e, ()))
    ]
