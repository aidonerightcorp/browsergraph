"""Screenshot-driven guidance via a multimodal model.

DOM text answers "what does this page say"; a screenshot answers "what does
this page look like", which is the only way to resolve questions the markup
cannot: which button is visually primary, whether a cookie wall is covering
the content, whether an element is actually visible.

It is expensive — an image is worth roughly a thousand tokens and a model
round-trip — so vision is a *fallback*, not a default. `Vision.ON_FAILURE`,
the recommended setting, spends nothing until the DOM path has already failed.

Requires a model with the `vision` capability; `models.py` selects one, and
these nodes fail with a clear message rather than sending an image to a
text-only model that will hallucinate a description of it.
"""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar

from browsergraph.dimensions import LLMConfig
from browsergraph.nodes.base import Node, register
from browsergraph.ports import Context


class Vision(str, Enum):
    """When a screenshot is sent to a multimodal model."""
    NONE = "none"                 # never; DOM only
    ON_FAILURE = "on_failure"     # only after the DOM path fails — recommended
    ALWAYS = "always"             # every step; expensive, for hostile pages
    ANNOTATED = "annotated"       # set-of-mark: number the elements first


@dataclass
class VisionResult:
    answer: str = ""
    selector: str = ""
    confidence: str = "low"
    raw: str = ""
    image_bytes: int = 0

    def to_dict(self) -> dict:
        return {"answer": self.answer, "selector": self.selector,
                "confidence": self.confidence, "image_bytes": self.image_bytes}


class VisionClient:
    """Ollama multimodal client. Images go in the `images` array as base64."""

    def __init__(self, cfg: LLMConfig, opener=None) -> None:
        self.cfg = cfg
        self._opener = opener or urllib.request.urlopen

    def ask(self, prompt: str, image_path: str, system: str = "") -> str:
        with open(image_path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
        messages: list[dict[str, Any]] = (
            [{"role": "system", "content": system}] if system else [])
        messages.append({"role": "user", "content": prompt, "images": [b64]})
        payload = json.dumps({
            "model": self.cfg.model, "messages": messages, "stream": False,
            "options": {"temperature": self.cfg.temperature},
        }).encode()
        headers = {"Content-Type": "application/json"}
        key = self.cfg.api_key or os.environ.get("OLLAMA_API_KEY", "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(
            f"{self.cfg.host.rstrip('/')}/api/chat", data=payload,
            headers=headers, method="POST")
        with self._opener(req, timeout=self.cfg.timeout) as resp:
            body = json.loads(resp.read().decode())
        return (body.get("message") or {}).get("content", "")


# --- set-of-mark ------------------------------------------------------------

_SOM_JS = """
(() => {
  const sels = 'a,button,input,select,textarea,[role=button],[onclick]';
  const out = [];
  document.querySelectorAll('.__bg_mark').forEach(e => e.remove());
  [...document.querySelectorAll(sels)].forEach((el, i) => {
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8 || r.top > innerHeight || r.bottom < 0) return;
    const n = out.length + 1;
    const tag = document.createElement('div');
    tag.className = '__bg_mark';
    tag.textContent = n;
    Object.assign(tag.style, {position:'fixed', zIndex:2147483647,
      left:r.left+'px', top:r.top+'px', background:'#e11', color:'#fff',
      font:'bold 12px monospace', padding:'0 3px', pointerEvents:'none'});
    document.body.appendChild(tag);
    out.push({n, tag: el.tagName.toLowerCase(),
              id: el.id || '', testid: el.getAttribute('data-testid') || '',
              name: el.getAttribute('name') || '',
              text: (el.innerText || el.value || '').trim().slice(0, 60)});
  });
  return JSON.stringify(out);
})()
"""


def annotate(browser) -> list[dict]:
    """Overlay numbered marks on interactive elements; return the legend.

    Set-of-mark turns "click the blue button" into "click 7", which a model
    answers far more reliably than free-form coordinates.
    """
    try:
        raw = browser.eval_js(_SOM_JS)
        return json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        return []


def selector_for(mark: dict) -> str:
    if mark.get("id"):
        return f"#{mark['id']}"
    if mark.get("testid"):
        return f'[data-testid="{mark["testid"]}"]'
    if mark.get("name"):
        return f'{mark.get("tag", "*")}[name="{mark["name"]}"]'
    return mark.get("tag", "*")


# --- nodes ------------------------------------------------------------------

@register
class VisionLocate(Node):
    """Find an element by describing it, using a screenshot.

    Deterministic first: if `fallback` resolves, no image is captured and no
    model is called.
    """

    kind: ClassVar[str] = "vision_locate"
    uses_llm: bool = True

    def __init__(self, goal: str, into: str = "selector", fallback: str = "",
                 cfg: LLMConfig | None = None, client: Any = None,
                 annotated: bool = True, name: str = ""):
        super().__init__(name)
        self.goal, self.into, self.fallback = goal, into, fallback
        self.cfg = cfg or LLMConfig()
        self.client = client
        self.annotated = annotated

    @property
    def writes(self) -> tuple[str, ...]:  # type: ignore[override]
        return (self.into,)

    def run(self, ctx: Context) -> Context:
        if self.fallback and ctx.page.find(self.fallback) is not None:
            ctx.data[self.into] = self.fallback
            ctx.note(f"vision skipped: {self.fallback!r} resolved from the DOM")
            return ctx

        marks = annotate(ctx.page) if self.annotated else []
        shot = os.path.join(tempfile.gettempdir(), f"bg_vision_{id(ctx)}.png")
        try:
            ctx.page.screenshot(shot)
        except Exception as e:
            ctx.fail(f"vision: screenshot failed: {type(e).__name__}: {e}")
            return ctx

        if marks:
            legend = "\n".join(
                f"{m['n']}: <{m['tag']}> {m.get('text','')!r}" for m in marks[:60])
            prompt = (f"The screenshot has numbered red markers on clickable "
                      f"elements.\nWhich number is: {self.goal}?\n"
                      f"Reply with the number only.\n\nLegend:\n{legend}")
        else:
            prompt = (f"Reply with ONE CSS selector and nothing else.\n"
                      f"Which element is: {self.goal}?")

        client = self.client or VisionClient(self.cfg)
        try:
            raw = client.ask(prompt, shot, system="You answer with a single token.")
        except (urllib.error.URLError, OSError, ValueError) as e:
            ctx.fail(f"vision model unreachable at {self.cfg.host}: {type(e).__name__}")
            return ctx

        answer = (raw or "").strip().splitlines()[0].strip() if raw else ""
        selector = ""
        if marks:
            m = re.search(r"\d+", answer)
            if m:
                idx = int(m.group(0))
                hit = next((x for x in marks if x["n"] == idx), None)
                if hit:
                    selector = selector_for(hit)
        else:
            candidate = answer.strip("`'\" ")
            # A model that cannot see the element often replies in prose.
            # Accepting that as a selector produces a confident nonsense click.
            if candidate and re.fullmatch(r"[#.\[\]\w\-=\"'>~+:()\s]{1,120}", candidate) \
                    and not re.search(r"\s(the|a|is|are|no|not|idea|sorry)\s",
                                      f" {candidate.lower()} "):
                selector = candidate

        if not selector:
            ctx.fail(f"vision: could not resolve {self.goal!r} from {answer[:60]!r}")
            return ctx

        ctx.data[self.into] = selector
        ctx.data["vision_used"] = True
        ctx.note(f"vision located {self.goal!r} -> {selector}")
        return ctx


@register
class VisionVerify(Node):
    """Confirm an expected outcome from what the page looks like."""

    kind: ClassVar[str] = "vision_verify"
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
        shot = os.path.join(tempfile.gettempdir(), f"bg_verify_{id(ctx)}.png")
        try:
            ctx.page.screenshot(shot)
        except Exception as e:
            ctx.fail(f"vision: screenshot failed: {type(e).__name__}: {e}")
            return ctx

        client = self.client or VisionClient(self.cfg)
        prompt = ('Answer with JSON {"ok": true|false, "why": "..."} only.\n'
                  f"Does this screenshot show: {self.expectation}?")
        try:
            raw = client.ask(prompt, shot, system="You reply with JSON only.")
        except (urllib.error.URLError, OSError, ValueError) as e:
            ctx.fail(f"vision model unreachable: {type(e).__name__}")
            return ctx

        try:
            start, end = raw.find("{"), raw.rfind("}")
            verdict = json.loads(raw[start:end + 1]) if start >= 0 else {}
        except (json.JSONDecodeError, ValueError):
            verdict = {}

        ok = bool(verdict.get("ok"))
        ctx.data["verified"] = ok
        ctx.data["verdict"] = verdict.get("why", (raw or "")[:200])
        ctx.note(f"vision verify -> {ok}")
        if not ok:
            ctx.fail(f"vision verification failed: {ctx.data['verdict'][:120]}")
        return ctx
