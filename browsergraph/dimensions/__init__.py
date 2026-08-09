"""Dimensions — the axes a browser run varies along.

Split by concern so adding an axis touches one file:

    enums.py       the axes themselves (Engine, Binary, Transport, …)
    settings.py    structured axes (Behavior, Identity, LLMConfig)
    capability.py  per-engine metadata (binaries, pip name, import probe)
    spec.py        Spec — one resolved point in the space
    rules.py       validate() — which points can actually run

Everything is re-exported here, so `from browsergraph.dimensions import Spec`
keeps working regardless of which module it lives in.
"""
from browsergraph.dimensions.capability import (
    ENGINE_BINARIES,
    ENGINE_FAMILY,
    ENGINE_IMPORT,
    ENGINE_REQUIREMENT,
    ENGINE_RUNS_JS,
    NATIVELY_UNDETECTED,
)
from browsergraph.dimensions.enums import (
    Binary,
    Capture,
    Display,
    Engine,
    LLMControl,
    Preprocess,
    Stealth,
    Transport,
    Vision,
)
from browsergraph.dimensions.rules import is_valid, validate
from browsergraph.dimensions.settings import Behavior, Identity, LLMConfig
from browsergraph.dimensions.spec import Spec

__all__ = [
    "Binary", "Capture", "Display", "Engine", "LLMControl", "Preprocess", "Stealth",
    "Transport", "Vision",
    "Behavior", "Identity", "LLMConfig", "Spec",
    "validate", "is_valid",
    "ENGINE_BINARIES", "ENGINE_FAMILY", "ENGINE_RUNS_JS", "ENGINE_IMPORT", "ENGINE_REQUIREMENT",
    "NATIVELY_UNDETECTED",
]
