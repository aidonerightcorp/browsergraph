"""Multi-model routing and a cost ledger.

`models.py` picks *a* model. Routing picks a **different model per job**: a
cheap one classifies, a strong one plans, a vision one only when the DOM path
has failed. Without that, multi-model means "we support several" rather than
"we use the right one".

The ledger is the other half. You cannot tune what you do not measure, and
tokens and latency are the two axes the learning layer needs to rank
preprocessing and model choices.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from browsergraph.dimensions import LLMConfig
from browsergraph.models import COMPLETION, THINKING, TOOLS, VISION, Catalog, ModelUnavailable

#: What each job needs and prefers. `capability` is a hard requirement;
#: `role` only breaks ties between models that already qualify.
JOBS: dict[str, dict[str, Any]] = {
    "classify":  {"capability": COMPLETION, "role": "fast",      "max_tokens": 2_000},
    "extract":   {"capability": COMPLETION, "role": "fast",      "max_tokens": 4_000},
    "selector":  {"capability": COMPLETION, "role": "code",      "max_tokens": 6_000},
    "verify":    {"capability": COMPLETION, "role": "fast",      "max_tokens": 4_000},
    "plan":      {"capability": THINKING,   "role": "reasoning", "max_tokens": 8_000},
    "agent":     {"capability": TOOLS,      "role": "reasoning", "max_tokens": 12_000},
    "vision":    {"capability": VISION,     "role": "",          "max_tokens": 4_000},
}


@dataclass
class Call:
    job: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    seconds: float = 0.0
    ok: bool = True
    error: str = ""

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    def to_dict(self) -> dict:
        return {"job": self.job, "model": self.model, "tokens": self.tokens,
                "seconds": round(self.seconds, 2), "ok": self.ok,
                "error": self.error[:120]}


@dataclass
class Ledger:
    """Every model call, so cost is measurable rather than anecdotal."""
    calls: list[Call] = field(default_factory=list)

    def add(self, call: Call) -> Call:
        self.calls.append(call)
        return call

    @property
    def tokens(self) -> int:
        return sum(c.tokens for c in self.calls)

    @property
    def seconds(self) -> float:
        return sum(c.seconds for c in self.calls)

    def by_model(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for c in self.calls:
            row = out.setdefault(c.model, {"calls": 0, "tokens": 0, "seconds": 0.0,
                                           "failures": 0})
            row["calls"] += 1
            row["tokens"] += c.tokens
            row["seconds"] += c.seconds
            row["failures"] += 0 if c.ok else 1
        for row in out.values():
            row["seconds"] = round(row["seconds"], 2)
        return out

    def report(self) -> str:
        if not self.calls:
            return "no model calls"
        lines = [f"{len(self.calls)} call(s), {self.tokens} tokens, "
                 f"{self.seconds:.1f}s"]
        for model, row in sorted(self.by_model().items()):
            lines.append(f"  {model:<30} {row['calls']:>3} calls  "
                         f"{row['tokens']:>7} tok  {row['seconds']:>6.1f}s"
                         + (f"  {row['failures']} failed" if row["failures"] else ""))
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"calls": len(self.calls), "tokens": self.tokens,
                "seconds": round(self.seconds, 2), "by_model": self.by_model()}


class Router:
    """Chooses a model per job, with a fallback chain and a ledger.

    A job whose capability no model satisfies raises rather than silently
    downgrading — a vision job answered by a text model returns confident
    fiction, which is worse than an error.
    """

    def __init__(self, catalog: Catalog | None = None, cfg: LLMConfig | None = None,
                 ledger: Ledger | None = None, overrides: dict[str, str] | None = None):
        self.cfg = cfg or LLMConfig()
        self.catalog = catalog or Catalog.load(self.cfg.host, self.cfg.api_key)
        self.ledger = ledger or Ledger()
        self.overrides = dict(overrides or {})
        self._chosen: dict[str, str] = {}

    def model_for(self, job: str) -> str:
        """Resolve (and cache) the model for a job."""
        if job in self.overrides:
            spec = JOBS.get(job, {})
            return self.catalog.choose(spec.get("capability", COMPLETION),
                                       requested=self.overrides[job]).name
        if job in self._chosen:
            return self._chosen[job]
        job_spec = JOBS.get(job)
        if job_spec is None:
            raise ModelUnavailable(f"unknown job {job!r}; known: {sorted(JOBS)}")
        name = self.catalog.best(job_spec["capability"], role=job_spec["role"]).name
        self._chosen[job] = name
        return name

    def config_for(self, job: str) -> LLMConfig:
        """An LLMConfig pinned to this job's model."""
        from dataclasses import replace
        return replace(self.cfg, model=self.model_for(job))

    def chain_for(self, job: str) -> list[str]:
        """Fallback order: the chosen model, then others with the capability."""
        spec = JOBS.get(job, {"capability": COMPLETION})
        qualified = [m.name for m in self.catalog.supporting(spec["capability"])]
        first = self.model_for(job)
        return [first] + [m for m in qualified if m != first]

    def call(self, job: str, fn, *args, **kwargs):
        """Run `fn(model, *args)` with fallback, recording cost either way.

        `fn` receives the model name so any client shape can be used.
        """
        last_error = ""
        for model in self.chain_for(job):
            started = time.monotonic()
            try:
                result = fn(model, *args, **kwargs)
            except Exception as e:
                self.ledger.add(Call(job=job, model=model,
                                     seconds=time.monotonic() - started,
                                     ok=False, error=f"{type(e).__name__}: {e}"))
                last_error = f"{type(e).__name__}: {e}"
                continue
            text = result if isinstance(result, str) else json.dumps(result, default=str)
            self.ledger.add(Call(
                job=job, model=model,
                tokens_in=_estimate_tokens(" ".join(str(a) for a in args)),
                tokens_out=_estimate_tokens(text),
                seconds=time.monotonic() - started, ok=True))
            return result
        raise ModelUnavailable(f"every model failed for job {job!r}: {last_error}")

    def report(self) -> dict:
        return {"assignments": dict(self._chosen), "ledger": self.ledger.to_dict()}


def _estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def plan_assignments(catalog: Catalog | None = None) -> dict[str, str]:
    """What each job would route to on this host — useful before a run."""
    router = Router(catalog=catalog)
    out = {}
    for job in JOBS:
        try:
            out[job] = router.model_for(job)
        except ModelUnavailable as e:
            out[job] = f"unavailable: {str(e)[:60]}"
    return out
