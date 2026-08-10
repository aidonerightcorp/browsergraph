"""Bundled JSON Schemas for non-Python registries and executors."""
from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

SCHEMA_NAMES = (
    "common.schema.json",
    "node-spec.schema.json",
    "program-graph.schema.json",
    "registry.schema.json",
    "frozen-plan.schema.json",
    "run-receipt.schema.json",
)


def load_schema(name: str) -> dict[str, Any]:
    if name not in SCHEMA_NAMES:
        raise ValueError(f"unknown SolutionGraph schema {name!r}")
    resource = files("solutiongraph.schemas").joinpath(name)
    return json.loads(resource.read_text(encoding="utf-8"))


def load_all_schemas() -> dict[str, dict[str, Any]]:
    return {name: load_schema(name) for name in SCHEMA_NAMES}


__all__ = ["SCHEMA_NAMES", "load_all_schemas", "load_schema"]

