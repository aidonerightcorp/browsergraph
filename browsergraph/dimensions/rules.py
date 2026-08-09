"""Validity rules over a Spec.

`validate` answers "can this run at all?". It deliberately does not answer
"does this look like a real browser?" — that is coherence, a separate question
(see DIMENSIONS.md), and conflating them makes both harder to reason about.

Rules live here rather than inside Spec so a rule can be added without touching
the data model.
"""
from __future__ import annotations

from browsergraph.dimensions.capability import ENGINE_BINARIES, NATIVELY_UNDETECTED
from browsergraph.dimensions.enums import (
    Capture,
    Display,
    Engine,
    Stealth,
    Transport,
    Vision,
)
from browsergraph.dimensions.spec import Spec


def validate(spec: Spec) -> list[str]:
    """Return reasons this spec cannot run. Empty list means runnable."""
    problems: list[str] = []
    remote = spec.transport is not Transport.LOCAL

    allowed = ENGINE_BINARIES.get(spec.engine, ())
    if allowed and spec.binary not in allowed:
        problems.append(
            f"engine={spec.engine.value} cannot drive binary={spec.binary.value} "
            f"(supports: {', '.join(b.value for b in allowed)})")
    if spec.stealth is Stealth.UNDETECTED and spec.engine not in NATIVELY_UNDETECTED:
        problems.append(
            f"stealth=undetected needs an evasion engine "
            f"({', '.join(e.value for e in NATIVELY_UNDETECTED)}), not {spec.engine.value}")
    if spec.engine is Engine.HTTP:
        if spec.vision is not Vision.NONE:
            problems.append("engine=http renders nothing, so vision has no image")
        if spec.capture in (Capture.VIDEO, Capture.TRACE, Capture.VIDEO_AND_TRACE):
            problems.append(f"engine=http cannot produce {spec.capture.value}")
        if spec.display in (Display.HEADED, Display.VNC):
            problems.append("engine=http has no display")
    if spec.engine is Engine.CAMOUFOX and spec.transport is not Transport.LOCAL:
        problems.append("camoufox runs locally only")
    if spec.engine in (Engine.SELENIUM_UC, Engine.NODRIVER) and \
            spec.transport is Transport.SELENIUM_GRID:
        problems.append(f"{spec.engine.value} cannot run on selenium grid")
    if spec.transport is Transport.SELENIUM_GRID and spec.identity.profile_dir:
        problems.append("selenium grid cannot mount a local profile dir")
    if spec.display in (Display.HEADED, Display.XVFB) and remote:
        problems.append(f"display={spec.display.value} requires transport=local")
    if remote and not spec.endpoint:
        problems.append(f"transport={spec.transport.value} requires an endpoint")
    if spec.llm.enabled and not spec.llm.host:
        problems.append("llm mode enabled but no host configured")
    if spec.capture in (Capture.VIDEO, Capture.TRACE, Capture.VIDEO_AND_TRACE) \
            and not spec.artifact_dir:
        problems.append(f"capture={spec.capture.value} requires artifact_dir")
    if spec.capture in (Capture.VIDEO, Capture.VIDEO_AND_TRACE) and \
            spec.engine not in (Engine.PLAYWRIGHT, Engine.PATCHRIGHT, Engine.CAMOUFOX):
        problems.append(f"capture={spec.capture.value} is only supported on the "
                        "playwright family (it uses playwright's bundled ffmpeg)")
    if spec.vision is not Vision.NONE and not spec.llm.host:
        problems.append(f"vision={spec.vision.value} needs an llm host "
                        "(a multimodal model must be reachable)")
    return problems


def is_valid(spec: Spec) -> bool:
    return not validate(spec)
