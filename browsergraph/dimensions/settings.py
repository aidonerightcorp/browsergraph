"""Structured dimensions.

These vary continuously or hold several fields, so they are dataclasses
rather than enums."""
from __future__ import annotations

from dataclasses import dataclass

from browsergraph.dimensions.enums import LLMControl


@dataclass(frozen=True)
class Behavior:
    """Human-likeness knobs. All timings in seconds."""
    min_action_delay: float = 0.0
    max_action_delay: float = 0.0
    typing_cps: float = 0.0        # 0 = instant
    mouse_curve: bool = False
    scroll_jitter: bool = False
    dwell_after_load: float = 0.0

    @staticmethod
    def instant() -> Behavior:
        return Behavior()

    @staticmethod
    def humanlike() -> Behavior:
        return Behavior(
            min_action_delay=0.4, max_action_delay=1.8, typing_cps=7.0,
            mouse_curve=True, scroll_jitter=True, dwell_after_load=1.2,
        )


@dataclass(frozen=True)
class Identity:
    """Who the browser claims to be."""
    profile_dir: str = ""
    proxy: str = ""
    user_agent: str = ""
    viewport: tuple[int, int] = (1440, 900)
    locale: str = "en-US"
    timezone: str = ""


@dataclass(frozen=True)
class LLMConfig:
    """Ollama-compatible endpoint. `api_key` supports gateways that require one."""
    mode: LLMControl = LLMControl.NONE
    host: str = "http://localhost:11434"
    model: str = "glm-5.2"
    api_key: str = ""
    temperature: float = 0.0
    timeout: float = 60.0

    @property
    def enabled(self) -> bool:
        return self.mode is not LLMControl.NONE
