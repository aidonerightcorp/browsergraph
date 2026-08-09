"""Enumerate the runnable points in the dimension space.

The reason dimensions are separate objects rather than a pile of flags: you can
ask for every valid combination and run one graph across all of them, instead
of hand-writing a config per setup.
"""
from __future__ import annotations

import itertools
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import replace

from browsergraph.dimensions import (
    Behavior,
    Binary,
    Display,
    Engine,
    LLMConfig,
    LLMControl,
    Spec,
    Stealth,
    Transport,
    is_valid,
    validate,
)

DEFAULT_AXES: dict[str, tuple] = {
    "engine": tuple(Engine),
    "binary": tuple(Binary),
    "transport": tuple(Transport),
    "display": tuple(Display),
    "stealth": tuple(Stealth),
}


def enumerate_specs(
    axes: Mapping[str, Iterable] | None = None,
    base: Spec | None = None,
    valid_only: bool = True,
) -> Iterator[Spec]:
    """Yield one Spec per combination of the given axes.

    Any axis omitted keeps its value from `base`, so you can sweep two
    dimensions while holding the rest fixed.
    """
    axes = {k: tuple(v) for k, v in (axes or DEFAULT_AXES).items()}
    base = base or Spec()
    keys = list(axes)
    for values in itertools.product(*(axes[k] for k in keys)):
        spec = replace(base, **dict(zip(keys, values, strict=True)))
        if valid_only and not is_valid(spec):
            continue
        yield spec


def count(axes: Mapping[str, Iterable] | None = None, base: Spec | None = None) -> tuple[int, int]:
    """(total combinations, runnable combinations)."""
    resolved = {k: tuple(v) for k, v in (axes or DEFAULT_AXES).items()}
    total = 1
    for v in resolved.values():
        total *= len(v)
    ok = sum(1 for _ in enumerate_specs(resolved, base, valid_only=True))
    return total, ok


def rejected(axes: Mapping[str, Iterable] | None = None,
             base: Spec | None = None) -> list[tuple[str, list[str]]]:
    """Combinations that were filtered out, with the reasons.

    Useful when a sweep produces fewer runs than expected — the answer is here
    rather than in silence.
    """
    out: list[tuple[str, list[str]]] = []
    for spec in enumerate_specs(axes, base, valid_only=False):
        problems = validate(spec)
        if problems:
            out.append((spec.describe(), problems))
    return out


# --- convenient presets -----------------------------------------------------

def preset(name: str) -> Spec:
    """Named starting points for common setups."""
    presets = {
        "fast": Spec(
            engine=Engine.PLAYWRIGHT, display=Display.HEADLESS,
            stealth=Stealth.NONE, behavior=Behavior.instant(),
        ),
        "human": Spec(
            engine=Engine.PATCHRIGHT, display=Display.HEADED,
            stealth=Stealth.UNDETECTED, behavior=Behavior.humanlike(),
        ),
        # Grid cannot host undetected-chromedriver, so remote stealth tops out
        # at injected patches. For real evasion use `undetected` locally.
        "stealth_remote": Spec(
            engine=Engine.SELENIUM, transport=Transport.SELENIUM_GRID,
            display=Display.HEADLESS, stealth=Stealth.STEALTH_JS,
            behavior=Behavior.humanlike(), endpoint="http://grid:4444/wd/hub",
        ),
        "undetected": Spec(
            engine=Engine.SELENIUM_UC, binary=Binary.SYSTEM_CHROME,
            display=Display.HEADLESS, stealth=Stealth.UNDETECTED,
            behavior=Behavior.humanlike(),
        ),
        "camoufox": Spec(
            engine=Engine.CAMOUFOX, binary=Binary.FIREFOX,
            display=Display.HEADLESS, stealth=Stealth.UNDETECTED,
            behavior=Behavior.humanlike(),
        ),
        "llm_agent": Spec(
            engine=Engine.PLAYWRIGHT, display=Display.HEADLESS,
            behavior=Behavior.humanlike(),
            llm=LLMConfig(mode=LLMControl.AGENT, model="glm-5.2"),
        ),
        "test": Spec(engine=Engine.MOCK, display=Display.HEADLESS),
    }
    if name not in presets:
        raise KeyError(f"unknown preset {name!r}; known: {sorted(presets)}")
    return presets[name]
