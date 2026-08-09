"""Learning which configuration works, instead of trying all of them.

Enumerating the valid space is the right tool for *testing* and the wrong one
for *running*: it re-derives from scratch what previous runs already
established. This module records outcomes and recommends the configuration
most likely to work for a new request, generalising from similar ones.

Similarity is hierarchical, most specific first:

    exact site  ->  same organisation  ->  same topic/sector  ->  same platform  ->  global

Evidence from each level is blended with a weight, so one observation on the
exact site outranks a hundred weak global ones, but a brand-new site still
inherits something useful from its sector.

**Honesty about evidence is the point.** Every estimate carries the effective
number of observations behind it. A 100% success rate from one run is reported
as `p=0.67, n=1` after smoothing — not as certainty — and early stopping
refuses to fire on thin evidence, because stopping because you are ignorant is
not the same as stopping because you know.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from browsergraph.dimensions import Spec

# --- features ---------------------------------------------------------------

_PLATFORM_HINTS = (
    ("shopify", re.compile(r"cdn\.shopify|myshopify", re.I)),
    ("wordpress", re.compile(r"wp-content|wp-json|wordpress", re.I)),
    ("wix", re.compile(r"wix\.com|wixstatic", re.I)),
    ("squarespace", re.compile(r"squarespace", re.I)),
    ("webflow", re.compile(r"webflow", re.I)),
    ("cloudflare", re.compile(r"cdn-cgi/|cf-ray", re.I)),
    ("react_spa", re.compile(r"__NEXT_DATA__|data-reactroot|__NUXT__", re.I)),
)


@dataclass(frozen=True)
class Features:
    """What makes two requests 'similar'. Cheap to compute, no model needed."""
    domain: str = ""
    organisation: str = ""      # registrable domain without the public suffix
    tld: str = ""
    platform: str = ""          # shopify / wordpress / react_spa / ...
    sector: str = ""            # NAICS code, when known
    task: str = ""
    path_shape: str = ""        # /a/b/123 -> /s/s/n, so paginated URLs group

    @staticmethod
    def of(url: str, html: str = "", sector: str = "", task: str = "") -> Features:
        p = urlparse(url or "")
        host = (p.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        host = host.split(":")[0]
        parts = host.split(".")
        tld = parts[-1] if len(parts) > 1 else ""
        org = ".".join(parts[-2:]) if len(parts) >= 2 else host

        platform = ""
        for name, pattern in _PLATFORM_HINTS:
            if html and pattern.search(html):
                platform = name
                break

        shape = "/".join(
            "n" if seg.isdigit() else ("s" if seg else "")
            for seg in (p.path or "").split("/"))

        return Features(domain=host, organisation=org, tld=tld, platform=platform,
                        sector=sector, task=task, path_shape=shape)

    def buckets(self) -> list[tuple[str, str, float]]:
        """(level, key, weight), most specific first.

        Weights are the shrinkage: evidence from a broader bucket counts for
        less, so generalisation informs without overwhelming direct experience.
        """
        out: list[tuple[str, str, float]] = []
        if self.domain:
            out.append(("site", f"site:{self.domain}|{self.task}", 1.0))
        if self.organisation and self.organisation != self.domain:
            out.append(("org", f"org:{self.organisation}|{self.task}", 0.6))
        if self.sector:
            out.append(("sector", f"sector:{self.sector}|{self.task}", 0.4))
        if self.platform:
            out.append(("platform", f"platform:{self.platform}|{self.task}", 0.35))
        out.append(("global", f"global:{self.task}", 0.15))
        return out

    def to_dict(self) -> dict:
        return asdict(self)


# --- estimates --------------------------------------------------------------

@dataclass
class Outcome:
    """What a run actually produced — not just whether it finished.

    Binary success can only tune *which engine*. It cannot tune preprocessing
    strategy, focus budget or model choice, because those rarely change whether
    a run succeeds — they change what it costs and how much it found. Recording
    yield, tokens and latency is what makes those axes learnable.
    """
    ok: bool
    yield_count: int = 0          # items extracted (contacts, articles, pages)
    tokens: int = 0
    seconds: float = 0.0
    attempts: int = 1

    @property
    def measured(self) -> bool:
        """Whether this outcome carries cost/yield data at all."""
        return bool(self.yield_count or self.tokens or self.seconds)

    def utility(self, token_cost: float = 1 / 10_000,
                second_cost: float = 1 / 120) -> float:
        """A single comparable score in [0, 1].

        An *unmeasured* success scores 1.0. Absence of measurement is not
        mediocrity — scoring a bare `ok=True` as 0.5 would make every success
        indistinguishable from a coin flip and destroy the engine ranking that
        binary outcomes already support.

        A *measured* success is scaled by yield and penalised by spend, which
        is what separates preprocessing strategies that succeed equally but
        cost very differently. Failure is 0 either way — a cheap failure is
        still a failure.
        """
        if not self.ok:
            return 0.0
        if not self.measured:
            return 1.0
        found = 1.0 - 1.0 / (1.0 + max(self.yield_count, 0))   # 0->0, 1->.5, 9->.9
        spend = self.tokens * token_cost + self.seconds * second_cost
        return max(0.05, min(1.0, 0.55 + 0.45 * found - min(0.5, spend)))

    def to_dict(self) -> dict:
        return {"ok": self.ok, "yield": self.yield_count, "tokens": self.tokens,
                "seconds": round(self.seconds, 2), "attempts": self.attempts,
                "utility": round(self.utility(), 3)}


@dataclass
class Estimate:
    """A smoothed success probability with the evidence behind it."""
    spec_key: str
    p: float
    evidence: float               # effective observations after weighting
    wins: float = 0.0
    losses: float = 0.0
    source: str = "prior"         # most specific bucket that contributed
    exact_hit: bool = False       # a cached solution for this exact site+task

    @property
    def confident(self) -> bool:
        """Enough evidence to act on the number rather than explore."""
        return self.evidence >= 3.0

    def to_dict(self) -> dict:
        return {"spec": self.spec_key, "p": round(self.p, 3),
                "evidence": round(self.evidence, 2), "source": self.source,
                "confident": self.confident, "cached": self.exact_hit}

    def __str__(self) -> str:
        tag = " (cached)" if self.exact_hit else ""
        return (f"{self.spec_key} p={self.p:.2f} n={self.evidence:.1f} "
                f"via {self.source}{tag}")


@dataclass
class Knowledge:
    """Outcome store with hierarchical, similarity-weighted recall."""

    path: Path | None = None
    prior_wins: float = 1.0        # Laplace smoothing: never claim 0 or 1
    prior_losses: float = 1.0
    _counts: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    _solutions: dict[str, str] = field(default_factory=dict)
    _metrics: dict[str, dict[str, list[dict]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.path and Path(self.path).exists():
            try:
                blob = json.loads(Path(self.path).read_text())
                self._counts = blob.get("counts", {})
                self._solutions = blob.get("solutions", {})
                self._metrics = blob.get("metrics", {})
            except (OSError, json.JSONDecodeError):
                pass

    # -- writing
    def record(self, features: Features, spec: Spec, ok: bool | Outcome = True,
               attempts: int = 1) -> None:
        """Record a result. Accepts a bool for simple cases, an Outcome for tuning.

        An Outcome contributes fractional evidence by utility, so a run that
        succeeded expensively counts as a partial win rather than a full one —
        which is what lets the same store rank preprocessing strategies.
        """
        outcome = ok if isinstance(ok, Outcome) else Outcome(ok=bool(ok), attempts=attempts)
        key = spec.describe()
        util = outcome.utility()
        for _, bucket, _ in features.buckets():
            slot = self._counts.setdefault(bucket, {}).setdefault(key, [0.0, 0.0])
            slot[0] += util
            slot[1] += 1.0 - util
        ok = outcome.ok
        self._metrics.setdefault(features.buckets()[0][1], {}).setdefault(key, []).append(
            outcome.to_dict())
        site_key = features.buckets()[0][1]
        if ok:
            # the cached solution: exact site+task -> the spec that worked
            self._solutions[site_key] = key
        elif self._solutions.get(site_key) == key:
            # A cached solution that has since failed is stale. Leaving it in
            # place would keep steering every future run into a configuration
            # the site has stopped accepting.
            self._solutions.pop(site_key, None)
        self.save()

    def save(self) -> None:
        if not self.path:
            return
        Path(self.path).write_text(json.dumps(
            {"counts": self._counts, "solutions": self._solutions,
             "metrics": self._metrics}, indent=2))

    # -- reading
    def cached_solution(self, features: Features) -> str | None:
        return self._solutions.get(features.buckets()[0][1])

    def estimate(self, features: Features, spec_key: str) -> Estimate:
        alpha, beta, evidence, source = self.prior_wins, self.prior_losses, 0.0, "prior"
        for level, bucket, weight in features.buckets():  # noqa: B007
            wins, losses = self._counts.get(bucket, {}).get(spec_key, [0.0, 0.0])
            if wins or losses:
                alpha += weight * wins
                beta += weight * losses
                evidence += weight * (wins + losses)
                if source == "prior":
                    source = level
        return Estimate(spec_key=spec_key, p=alpha / (alpha + beta),
                        evidence=evidence, wins=alpha - self.prior_wins,
                        losses=beta - self.prior_losses, source=source,
                        exact_hit=self.cached_solution(features) == spec_key)

    def rank(self, features: Features, candidates: Iterable[Spec],
             explore: float = 0.0) -> list[tuple[Spec, Estimate]]:
        """Candidates ordered by expected success.

        `explore` adds an optimism bonus for thin evidence (UCB-style), so a
        never-tried option is not permanently buried behind a mediocre one
        that happens to have been tried once.
        """
        scored: list[tuple[Spec, Estimate, float]] = []
        for spec in candidates:
            est = self.estimate(features, spec.describe())
            bonus = explore * math.sqrt(1.0 / (1.0 + est.evidence)) if explore else 0.0
            score = est.p + bonus + (0.5 if est.exact_hit else 0.0)
            scored.append((spec, est, score))
        scored.sort(key=lambda t: t[2], reverse=True)
        return [(s, e) for s, e, _ in scored]

    def stats(self) -> dict:
        total = sum(w + n for b in self._counts.values() for w, n in b.values())
        wins = sum(w for b in self._counts.values() for w, _ in b.values())
        return {"buckets": len(self._counts), "observations": int(total),
                "win_rate": round(wins / total, 3) if total else 0.0,
                "cached_solutions": len(self._solutions)}


# --- guardrails -------------------------------------------------------------

@dataclass
class Budget:
    """Hard limits. Exceeding any of these stops the run regardless of promise."""
    max_attempts: int = 4
    max_seconds: float = 300.0
    max_llm_calls: int = 20
    min_expected_success: float = 0.15
    min_evidence_to_stop: float = 3.0

    attempts: int = 0
    seconds: float = 0.0
    llm_calls: int = 0

    def spend(self, seconds: float = 0.0, llm: int = 0) -> None:
        self.attempts += 1
        self.seconds += seconds
        self.llm_calls += llm

    def exhausted(self) -> str:
        if self.attempts >= self.max_attempts:
            return f"attempt limit reached ({self.max_attempts})"
        if self.seconds >= self.max_seconds:
            return f"time limit reached ({self.max_seconds:.0f}s)"
        if self.llm_calls >= self.max_llm_calls:
            return f"llm call limit reached ({self.max_llm_calls})"
        return ""

    def should_stop_early(self, best: Estimate | None) -> str:
        """Stop only when the evidence supports pessimism.

        Without the evidence test this would abandon every unfamiliar site on
        its first failure, which is exactly when exploration is worth most.
        """
        if best is None:
            return "no candidates remain"
        if best.evidence < self.min_evidence_to_stop:
            return ""
        if best.p < self.min_expected_success:
            return (f"best remaining option {best.spec_key} has p={best.p:.2f} "
                    f"(n={best.evidence:.1f}), below {self.min_expected_success:.2f}")
        return ""

    def report(self) -> dict:
        return {"attempts": self.attempts, "seconds": round(self.seconds, 1),
                "llm_calls": self.llm_calls,
                "limits": {"attempts": self.max_attempts,
                           "seconds": self.max_seconds,
                           "llm_calls": self.max_llm_calls}}


# --- planning ---------------------------------------------------------------

@dataclass
class Plan:
    """An ordered, justified shortlist — not an exhaustive sweep."""
    features: Features
    candidates: list[tuple[Spec, Estimate]] = field(default_factory=list)
    cached: bool = False
    rationale: str = ""

    @property
    def specs(self) -> list[Spec]:
        return [s for s, _ in self.candidates]

    def explain(self) -> str:
        lines = [self.rationale] if self.rationale else []
        for _spec, est in self.candidates:
            lines.append(f"  {est}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"features": self.features.to_dict(), "cached": self.cached,
                "rationale": self.rationale,
                "candidates": [e.to_dict() for _, e in self.candidates]}


def plan(knowledge: Knowledge, features: Features, candidates: list[Spec],
         shortlist: int = 3, explore: float = 0.1) -> Plan:
    """Rank candidates and keep only the shortlist worth attempting.

    A cached exact solution short-circuits ranking: if this site and task
    succeeded with a known configuration, try that first and stop reasoning.
    """
    ranked = knowledge.rank(features, candidates, explore=explore)
    cached_key = knowledge.cached_solution(features)

    if cached_key and ranked and ranked[0][1].exact_hit:
        rationale = (f"cached solution for {features.domain} + {features.task}: "
                     f"{cached_key}")
        keep = ranked[:max(2, shortlist)]     # keep one fallback behind the cache
        return Plan(features, keep, cached=True, rationale=rationale)

    best = ranked[0][1] if ranked else None
    if best and best.evidence == 0:
        rationale = (f"no prior evidence for {features.domain} — starting from the "
                     f"generic ladder, cheapest first")
    else:
        rationale = (f"ranked by similarity to prior runs "
                     f"(best source: {best.source if best else 'none'})")
    return Plan(features, ranked[:shortlist], cached=False, rationale=rationale)


def best_axis_value(knowledge: Knowledge, features: Features, base: Spec,
                    axis: str, values: Iterable) -> tuple[Any, list[Estimate]]:
    """Which value of one axis has performed best here, holding the rest fixed.

    This is what utility-based recording buys: `preprocess` and `vision` barely
    move success rates, so binary outcomes rank them all identically. Utility
    separates them by cost and yield.
    """
    from dataclasses import replace as _replace
    estimates = []
    for value in values:
        cand = _replace(base, **{axis: value})
        est = knowledge.estimate(features, cand.describe())
        est.spec_key = f"{axis}={getattr(value, 'value', value)}"
        estimates.append(est)
    estimates.sort(key=lambda e: (e.p, e.evidence), reverse=True)
    top = estimates[0]
    chosen = next((v for v in values
                   if f"{axis}={getattr(v, 'value', v)}" == top.spec_key), None)
    return chosen, estimates
