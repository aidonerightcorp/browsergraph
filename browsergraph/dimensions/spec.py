"""`Spec` — one resolved point in the dimension space.

A Spec is the complete description of a run. Nodes and drivers read it instead
of reading environment variables or globals, so a run is reproducible from the
Spec alone.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from browsergraph.dimensions.enums import (
    Binary,
    Capture,
    Display,
    Engine,
    Preprocess,
    Stealth,
    Transport,
    Vision,
)
from browsergraph.dimensions.settings import Behavior, Identity, LLMConfig


@dataclass(frozen=True)
class Spec:
    engine: Engine = Engine.MOCK
    binary: Binary = Binary.BUNDLED_CHROMIUM
    transport: Transport = Transport.LOCAL
    display: Display = Display.HEADLESS
    stealth: Stealth = Stealth.NONE
    preprocess: Preprocess = Preprocess.TEXT
    vision: Vision = Vision.NONE
    capture: Capture = Capture.SCREENSHOT_ON_FAILURE
    artifact_dir: str = ""          # where video/trace/screenshots land
    isolated: bool = False          # run the engine in its own venv
    behavior: Behavior = field(default_factory=Behavior.instant)
    identity: Identity = field(default_factory=Identity)
    llm: LLMConfig = field(default_factory=LLMConfig)
    endpoint: str = ""             # grid/browserless/CDP URL when remote
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def describe(self) -> str:
        return (f"{self.engine.value}/{self.binary.value}/{self.transport.value}"
                f"/{self.display.value}/stealth={self.stealth.value}"
                f"/pre={self.preprocess.value}/vision={self.vision.value}"
                f"/llm={self.llm.mode.value}")
