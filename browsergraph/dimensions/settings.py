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
    """Ollama-compatible endpoint. `api_key` supports gateways that require one.

    `model` is empty by default, meaning **resolve it from what the host
    actually has**. It used to name a specific model, which produced the worst
    possible first experience: a bare `HTTP Error 404: Not Found` on a perfectly
    healthy Ollama that simply had different models pulled. A default that only
    works on the machine it was written on is not a default.
    """
    mode: LLMControl = LLMControl.NONE
    host: str = "http://localhost:11434"
    model: str = ""                 # "" = pick from the host's catalogue
    api_key: str = ""
    temperature: float = 0.0
    timeout: float = 60.0

    @property
    def enabled(self) -> bool:
        return self.mode is not LLMControl.NONE

    @classmethod
    def from_env(cls, **overrides) -> LLMConfig:
        """Read the standard Ollama environment variables.

        `OLLAMA_HOST` and `OLLAMA_API_KEY` are what people already have set —
        for a local daemon, for Ollama Cloud, or for a gateway. Requiring them
        to be passed again in code is friction with nothing on the other side
        of it.
        """
        import os
        host = os.environ.get("OLLAMA_HOST", "").strip()
        if host and not host.startswith(("http://", "https://")):
            host = f"http://{host}"          # OLLAMA_HOST is often bare host:port
        fields = {"host": host or cls.host,
                  "api_key": os.environ.get("OLLAMA_API_KEY", "").strip(),
                  "model": os.environ.get("OLLAMA_MODEL", "").strip()}
        return cls(**{**{k: v for k, v in fields.items() if v}, **overrides})
