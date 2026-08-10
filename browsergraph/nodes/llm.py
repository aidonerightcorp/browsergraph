"""LLM nodes, backed by an Ollama-compatible endpoint.

Two rules hold throughout:

1. The endpoint, model and key are configuration (`LLMConfig`), never hardcoded.
2. A graph must still be describable when the model is unreachable — these
   nodes fail loudly rather than silently substituting guesses, so a run never
   half-succeeds on a hallucinated selector.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, ClassVar

from browsergraph.dimensions import LLMConfig
from browsergraph.models import Catalog, ModelUnavailable
from browsergraph.nodes.base import Node, register
from browsergraph.ports import Context

#: Catalogues, per host. Resolving a model costs a round trip, and every LLM
#: node in a graph would otherwise pay it again.
_CATALOGS: dict[tuple[str, str], Catalog] = {}


def resolve_model(cfg: LLMConfig, capability: str = "completion") -> str:
    """The model to actually call, given what this host has.

    Three cases, in order of how much the caller asked for:

    * an exact name that exists — used as given;
    * a name that exists only as a *tag*, e.g. `glm-5.2` where the host has
      `glm-5.2:cloud`. Ollama names models `name:tag` and people write the bare
      name constantly. Failing that with a 404 is technically correct and
      useless;
    * nothing asked for — the best model on the host with the needed capability,
      which is what `routing` already knows how to work out.

    Returns the requested name unchanged when the host cannot be reached, so an
    unreachable Ollama produces a connection error rather than being disguised
    as a model-selection problem.
    """
    key = (cfg.host, cfg.api_key)
    catalog = _CATALOGS.get(key)
    if catalog is None:
        catalog = Catalog.load(cfg.host, cfg.api_key)
        _CATALOGS[key] = catalog
    if not getattr(catalog, "reachable", False):
        return cfg.model

    names = [m.name for m in catalog.models]
    if cfg.model:
        if cfg.model in names:
            return cfg.model
        tagged = [n for n in names if n.split(":", 1)[0] == cfg.model]
        if len(tagged) == 1:
            return tagged[0]
        if tagged:
            return sorted(tagged)[0]
        raise ModelUnavailable(
            f"{cfg.model!r} is not on {cfg.host}. Available: {', '.join(names) or 'none'}. "
            f"Leave LLMConfig.model empty to pick one automatically.")

    best = catalog.best(capability)
    if best is None:
        raise ModelUnavailable(
            f"no model on {cfg.host} reports the {capability!r} capability. "
            f"Available: {', '.join(names) or 'none'}")
    return best.name


class OllamaClient:
    """Thin client for /api/chat. `api_key` is sent when set, for gateways."""

    def __init__(self, cfg: LLMConfig, opener=None, capability: str = "completion") -> None:
        self.cfg = cfg
        self.capability = capability
        self._opener = opener or urllib.request.urlopen

    def complete(self, prompt: str, system: str = "") -> str:
        messages = ([{"role": "system", "content": system}] if system else [])
        messages.append({"role": "user", "content": prompt})
        payload = json.dumps({
            "model": resolve_model(self.cfg, self.capability),
            "messages": messages,
            "stream": False,
            "options": {"temperature": self.cfg.temperature},
        }).encode()
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        req = urllib.request.Request(
            f"{self.cfg.host.rstrip('/')}/api/chat", data=payload,
            headers=headers, method="POST")
        with self._opener(req, timeout=self.cfg.timeout) as resp:
            body = json.loads(resp.read().decode())
        return (body.get("message") or {}).get("content", "")


@register
class LLMSelector(Node):
    """Ask the model for a selector when the scripted one fails.

    Only consulted after the deterministic selector misses, so a working graph
    costs no tokens.
    """

    kind: ClassVar[str] = "llm_selector"
    uses_llm: bool = True

    def __init__(self, goal: str, into: str = "selector", fallback: str = "",
                 cfg: LLMConfig | None = None, client: Any = None, name: str = ""):
        super().__init__(name)
        self.goal = goal
        self.into = into
        self.fallback = fallback
        self.cfg = cfg or LLMConfig()
        self.client = client

    def run(self, ctx: Context) -> Context:
        if self.fallback and ctx.page.find(self.fallback) is not None:
            ctx.data[self.into] = self.fallback
            ctx.note(f"selector {self.fallback!r} resolved without the model")
            return ctx

        client = self.client or OllamaClient(self.cfg)
        html = ctx.page.html()[:6000]
        prompt = (
            "Return ONE CSS selector and nothing else.\n"
            f"Goal: {self.goal}\n\nHTML:\n{html}"
        )
        try:
            selector = client.complete(prompt, system="You output only a CSS selector.").strip()
        except (urllib.error.URLError, OSError, ValueError) as e:
            ctx.fail(f"llm unreachable at {self.cfg.host}: {type(e).__name__}")
            return ctx

        selector = selector.splitlines()[0].strip().strip("`") if selector else ""
        if not selector:
            ctx.fail("llm returned no selector")
            return ctx
        ctx.data[self.into] = selector
        ctx.note(f"llm selector -> {selector!r}")
        return ctx


@register
class LLMVerify(Node):
    """Ask the model whether the page shows the expected outcome."""

    kind: ClassVar[str] = "llm_verify"
    uses_llm: bool = True
    verifies: bool = True
    writes: ClassVar[tuple[str, ...]] = ("verified", "verdict")

    def __init__(self, expectation: str, cfg: LLMConfig | None = None,
                 client: Any = None, name: str = ""):
        super().__init__(name)
        self.expectation = expectation
        self.cfg = cfg or LLMConfig()
        self.client = client

    def run(self, ctx: Context) -> Context:
        client = self.client or OllamaClient(self.cfg)
        state = ctx.page.state()
        prompt = (
            'Answer with JSON {"ok": true|false, "why": "..."} only.\n'
            f"Expectation: {self.expectation}\n"
            f"URL: {state.url}\nTitle: {state.title}\n"
            f"Page text (truncated):\n{ctx.page.html()[:4000]}"
        )
        try:
            raw = client.complete(prompt, system="You reply with JSON only.")
        except (urllib.error.URLError, OSError, ValueError) as e:
            ctx.fail(f"llm unreachable at {self.cfg.host}: {type(e).__name__}")
            return ctx

        verdict: dict[str, Any]
        try:
            start, end = raw.find("{"), raw.rfind("}")
            verdict = json.loads(raw[start:end + 1]) if start >= 0 else {}
        except (json.JSONDecodeError, ValueError):
            verdict = {}

        ok = bool(verdict.get("ok"))
        ctx.data["verified"] = ok
        ctx.data["verdict"] = verdict.get("why", raw[:200])
        ctx.note(f"llm verify -> {ok} ({ctx.data['verdict'][:80]})")
        if not ok:
            ctx.fail(f"verification failed: {ctx.data['verdict'][:120]}")
        return ctx
