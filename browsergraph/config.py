"""Graphs as config, so a run is data rather than code.

JSON always works; YAML is used when PyYAML is installed (it is not a
dependency — the core stays stdlib-only).

    spec:
      engine: playwright
      display: headless
      behavior: humanlike
      llm: {mode: selector, model: glm-5.2}
    nodes:
      - {kind: navigate, url: "https://example.com"}
      - {kind: wait_for, selector: "#login"}
      - {kind: extract, selector: "h1", into: heading}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from browsergraph.dimensions import (
    Behavior,
    Binary,
    Display,
    Engine,
    Identity,
    LLMConfig,
    LLMControl,
    Spec,
    Stealth,
    Transport,
)
from browsergraph.graph import Graph
from browsergraph.nodes import make  # noqa: F401  (registers built-ins)


def _load_raw(path: str | Path) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    if str(path).endswith((".yaml", ".yml")):
        try:
            import yaml  # type: ignore
        except ImportError as e:
            raise SystemExit(
                "YAML config needs PyYAML: pip install browsergraph[yaml] "
                "(or use JSON)") from e
        return yaml.safe_load(text) or {}
    return json.loads(text)


def spec_from_dict(d: dict[str, Any]) -> Spec:
    d = dict(d or {})
    behavior = d.pop("behavior", "instant")
    if isinstance(behavior, str):
        behavior = Behavior.humanlike() if behavior == "humanlike" else Behavior.instant()
    elif isinstance(behavior, dict):
        behavior = Behavior(**behavior)

    identity = d.pop("identity", {}) or {}
    if isinstance(identity.get("viewport"), list):
        identity["viewport"] = tuple(identity["viewport"])

    llm = d.pop("llm", {}) or {}
    if "mode" in llm:
        llm["mode"] = LLMControl(llm["mode"])

    from browsergraph.dimensions import Capture, Preprocess, Vision
    enums = {"engine": Engine, "binary": Binary, "transport": Transport,
             "display": Display, "stealth": Stealth, "preprocess": Preprocess,
             "vision": Vision, "capture": Capture}
    for key, enum in enums.items():
        if key in d and d[key] is not None:
            d[key] = enum(d[key])

    return Spec(behavior=behavior, identity=Identity(**identity),
                llm=LLMConfig(**llm), **d)


def graph_from_list(items: list[dict], name: str = "graph",
                    behavior: Behavior | None = None) -> Graph:
    g = Graph(name)
    for i, item in enumerate(items):
        item = dict(item)
        kind = item.pop("kind")
        item.setdefault("name", f"{kind}-{i}")
        # actions that accept behaviour get the spec's, unless overridden
        if behavior is not None and kind in {"navigate", "click", "type", "scroll"}:
            item.setdefault("behavior", behavior)
        g.add(make(kind, **item))
    return g


def load_graph(path: str | Path,
               params: dict[str, Any] | None = None) -> tuple[Graph, Spec]:
    """Load a config, substituting any declared parameters.

    A template with `params:` is a capability, not a single task — supply
    values per run and the same file serves a whole batch.
    """
    raw = _load_raw(path)

    if raw.get("params"):
        from browsergraph.params import ParamSet, check_template, substitute
        problems = check_template(raw)
        if problems:
            raise SystemExit("template problems: " + "; ".join(problems))
        values = ParamSet.from_list(raw["params"]).resolve(params)
        raw = {**raw, "spec": substitute(raw.get("spec", {}), values),
               "nodes": substitute(raw.get("nodes", []), values)}
    elif params:
        raise SystemExit("parameters supplied but the config declares none")

    spec = spec_from_dict(raw.get("spec", {}))
    graph = graph_from_list(raw.get("nodes", []),
                            name=raw.get("name", Path(path).stem),
                            behavior=spec.behavior)
    return graph, spec
