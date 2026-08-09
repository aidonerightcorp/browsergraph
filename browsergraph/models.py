"""Ollama model discovery and selection.

Choosing a model by name in config is how a graph breaks on a machine where
that model was never pulled. Instead, ask the server what it has, filter by
the capability the job needs, and pick by preference order.

Ollama reports capabilities per model (`completion`, `tools`, `thinking`,
`vision`), which is authoritative — far better than inferring from the name.
A vision node on a text-only model does not error; it hallucinates a
description of an image it never saw, so this check is a correctness
requirement, not a convenience.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, field

DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

#: Capability a job needs.
VISION = "vision"
TOOLS = "tools"
THINKING = "thinking"
COMPLETION = "completion"

#: Preference when several models qualify. Earlier is better.
#: Substring match, so `glm-5.2:cloud` matches `glm-5`.
PREFERENCE: tuple[str, ...] = (
    "glm-5", "kimi-k2", "deepseek-v4", "qwen3", "llama4", "mistral",
)

#: Rough role hints, used only to break ties.
ROLE_HINTS = {
    "code": ("code", "coder", "kimi"),
    "reasoning": ("thinking", "glm", "deepseek", "r1"),
    "fast": ("flash", "mini", "small", "3b", "7b"),
}


@dataclass
class ModelInfo:
    name: str
    capabilities: tuple[str, ...] = ()
    families: tuple[str, ...] = ()
    parameter_size: str = ""
    error: str = ""

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    @property
    def cloud(self) -> bool:
        return self.name.endswith(":cloud") or "-cloud" in self.name

    def to_dict(self) -> dict:
        return {"name": self.name, "capabilities": list(self.capabilities),
                "parameter_size": self.parameter_size, "cloud": self.cloud}


class ModelUnavailable(RuntimeError):
    pass


@dataclass
class Catalog:
    """What this Ollama host can actually do."""
    host: str = DEFAULT_HOST
    api_key: str = ""
    models: list[ModelInfo] = field(default_factory=list)
    reachable: bool = False
    error: str = ""

    def _request(self, path: str, data: dict | None = None, timeout: float = 20.0):
        req = urllib.request.Request(
            self.host.rstrip("/") + path,
            data=json.dumps(data).encode() if data else None,
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})},
            method="POST" if data else "GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

    @staticmethod
    def load(host: str = "", api_key: str = "", timeout: float = 20.0) -> Catalog:
        cat = Catalog(host=host or DEFAULT_HOST,
                      api_key=api_key or os.environ.get("OLLAMA_API_KEY", ""))
        try:
            tags = cat._request("/api/tags", timeout=timeout)
        except (urllib.error.URLError, OSError, ValueError) as e:
            cat.error = f"{type(e).__name__}: {e}"
            return cat
        cat.reachable = True
        for entry in tags.get("models", []):
            name = entry.get("name", "")
            info = ModelInfo(name=name)
            try:
                shown = cat._request("/api/show", {"model": name}, timeout=timeout)
                info.capabilities = tuple(shown.get("capabilities") or ())
                details = shown.get("details") or {}
                info.families = tuple(details.get("families") or ())
                info.parameter_size = details.get("parameter_size", "")
            except (urllib.error.URLError, OSError, ValueError) as e:
                info.error = f"{type(e).__name__}"
            cat.models.append(info)
        return cat

    # -- selection
    def supporting(self, capability: str) -> list[ModelInfo]:
        return [m for m in self.models if m.supports(capability)]

    def best(self, capability: str = COMPLETION, role: str = "",
             prefer: Iterable[str] = PREFERENCE) -> ModelInfo:
        """Best available model for a capability, by preference order.

        Raises rather than silently returning a model that cannot do the job —
        a text model asked to read a screenshot produces confident fiction.
        """
        candidates = self.supporting(capability)
        if not candidates:
            have = {c for m in self.models for c in m.capabilities}
            raise ModelUnavailable(
                f"no model on {self.host} supports {capability!r}. "
                f"Available capabilities: {sorted(have) or 'none'}. "
                f"Pull one, e.g. `ollama pull llama3.2-vision` for vision.")

        hints = ROLE_HINTS.get(role, ())

        def rank(m: ModelInfo) -> tuple:
            pref = next((i for i, p in enumerate(prefer) if p in m.name), len(tuple(prefer)))
            hinted = 0 if any(h in m.name.lower() for h in hints) else 1
            return (hinted, pref, m.name)

        return sorted(candidates, key=rank)[0]

    def choose(self, capability: str = COMPLETION, role: str = "",
               requested: str = "") -> ModelInfo:
        """Honour an explicit request when it qualifies, else pick the best.

        A requested model that lacks the capability is a hard error: silently
        substituting would make the run's behaviour untraceable.
        """
        if requested:
            match = next((m for m in self.models
                          if m.name == requested or m.name.startswith(requested + ":")), None)
            if match is None:
                raise ModelUnavailable(
                    f"model {requested!r} is not on {self.host}. "
                    f"Available: {[m.name for m in self.models]}")
            if capability and not match.supports(capability):
                raise ModelUnavailable(
                    f"model {requested!r} does not support {capability!r} "
                    f"(has: {list(match.capabilities)})")
            return match
        return self.best(capability, role=role)

    def report(self) -> str:
        if not self.reachable:
            return f"ollama unreachable at {self.host}: {self.error}"
        lines = [f"{self.host} — {len(self.models)} model(s)"]
        for m in self.models:
            caps = ",".join(sorted(m.capabilities)) or "unknown"
            lines.append(f"  {m.name:<32} {caps}")
        for cap in (VISION, TOOLS, THINKING):
            try:
                lines.append(f"  best[{cap}]: {self.best(cap).name}")
            except ModelUnavailable:
                lines.append(f"  best[{cap}]: none available")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"host": self.host, "reachable": self.reachable, "error": self.error,
                "models": [m.to_dict() for m in self.models]}


def select(capability: str = COMPLETION, role: str = "", requested: str = "",
           host: str = "", api_key: str = "") -> str:
    """Convenience: the model name to use, or raise with an actionable message."""
    return Catalog.load(host, api_key).choose(capability, role, requested).name
