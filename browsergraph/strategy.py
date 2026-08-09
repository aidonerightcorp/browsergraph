"""Adaptive execution: when one configuration fails, try better ones.

Three parts:

* `suggest`   — given a failure, which spec to try next (targeted, not random)
* `escalate`  — walk a ladder until something works, or a terminal failure stops it
* `SiteMemory` — remember what worked per domain, so the next run starts there

The escalation ladder is ordered cheapest-first: a stealth engine costs more
than a longer wait, so it is not the first thing tried. And a terminal
diagnosis (challenge, block) halts the ladder immediately — continuing to
escalate against a site that has already flagged you is how accounts are lost.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlparse

from browsergraph.dimensions import (
    ENGINE_RUNS_JS,
    Behavior,
    Display,
    Engine,
    LLMControl,
    Spec,
    Stealth,
    is_valid,
)
from browsergraph.errors import Diagnosis, Failure, Response, classify
from browsergraph.graph import Graph, RunResult
from browsergraph.graph import run as run_graph
from browsergraph.learn import Budget, Estimate, Features, Knowledge, Plan, plan

# --- targeted suggestions ---------------------------------------------------

def suggest(spec: Spec, diagnosis: Diagnosis) -> list[Spec]:
    """Specs worth trying next, most promising first.

    Each suggestion addresses the diagnosed cause rather than shuffling
    settings at random.
    """
    out: list[Spec] = []

    def add(**changes) -> None:
        cand = replace(spec, **changes)
        if is_valid(cand) and cand != spec:
            out.append(cand)

    f = diagnosis.failure

    # An element that is missing or late on a JavaScript-rendered page cannot be
    # waited into existence by an engine that has no JavaScript runtime. When
    # engine=http misses a selector, the useful next move is a browser — not a
    # longer dwell, which is what every generic timeout rule would suggest.
    # `mock` is excluded deliberately: it has no JavaScript runtime either, but
    # it is a test double serving fixtures, so a real browser would not find the
    # missing element any more than it did.
    if f in (Failure.SELECTOR_MISS, Failure.TIMEOUT, Failure.VERIFY) \
            and not ENGINE_RUNS_JS.get(spec.engine, True) \
            and spec.engine is not Engine.MOCK:
        add(engine=Engine.PLAYWRIGHT, stealth=Stealth.NONE)

    if f in (Failure.SELECTOR_MISS, Failure.VERIFY):
        # let the model resolve selectors / confirm outcomes
        if not spec.llm.enabled:
            add(llm=replace(spec.llm, mode=LLMControl.SELECTOR))
        # some content only renders in a real window
        if spec.display is Display.HEADLESS:
            add(display=Display.HEADED)

    if f is Failure.TIMEOUT:
        if spec.behavior.dwell_after_load < 3:
            add(behavior=replace(spec.behavior, dwell_after_load=3.0))
        if spec.display is Display.HEADLESS:
            add(display=Display.HEADED)

    if f in (Failure.CHALLENGE, Failure.BLOCKED, Failure.RATE_LIMIT, Failure.UNKNOWN):
        # look less automated: evasion engine, then human pacing
        if spec.engine in (Engine.PLAYWRIGHT, Engine.PLAYWRIGHT_STEALTH):
            add(engine=Engine.PATCHRIGHT, stealth=Stealth.UNDETECTED)
        if spec.engine is Engine.SELENIUM:
            add(engine=Engine.SELENIUM_UC, stealth=Stealth.UNDETECTED)
        if spec.behavior.max_action_delay == 0:
            add(behavior=Behavior.humanlike())
        if spec.stealth in (Stealth.NONE, Stealth.BASIC):
            add(stealth=Stealth.STEALTH_JS)

    if f is Failure.NETWORK:
        add(display=Display.HEADLESS)  # rule out display-related launch failure

    if f is Failure.CRASH and spec.engine is not Engine.SELENIUM:
        add(engine=Engine.SELENIUM)

    # de-duplicate, preserving order
    seen, unique = set(), []
    for s in out:
        key = s.describe()
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


def ladder(spec: Spec, depth: int = 4) -> list[Spec]:
    """A generic escalation ladder, for when there is no diagnosis to work from."""
    steps = [
        spec,
        replace(spec, behavior=Behavior.humanlike()),
        replace(spec, stealth=Stealth.STEALTH_JS, behavior=Behavior.humanlike()),
        replace(spec, engine=Engine.PATCHRIGHT, stealth=Stealth.UNDETECTED,
                behavior=Behavior.humanlike()),
        replace(spec, engine=Engine.SELENIUM_UC, stealth=Stealth.UNDETECTED,
                behavior=Behavior.humanlike()),
    ]
    out, seen = [], set()
    for s in steps:
        if is_valid(s) and s.describe() not in seen:
            seen.add(s.describe())
            out.append(s)
    return out[:depth]


# --- memory -----------------------------------------------------------------

@dataclass
class SiteMemory:
    """Which spec last worked for a domain.

    Turns escalation from a per-run cost into a one-off: the second run against
    a hostile site starts from the configuration that succeeded.
    """
    path: Path | None = None
    _data: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.path and Path(self.path).exists():
            try:
                self._data = json.loads(Path(self.path).read_text())
            except (OSError, json.JSONDecodeError):
                self._data = {}

    @staticmethod
    def domain(url: str) -> str:
        return (urlparse(url).netloc or url).lower()

    def record(self, url: str, spec: Spec, ok: bool, attempts: int = 1) -> None:
        d = self._data.setdefault(self.domain(url), {"wins": {}, "losses": {}})
        bucket = d["wins"] if ok else d["losses"]
        bucket[spec.describe()] = bucket.get(spec.describe(), 0) + 1
        if ok:
            d["best"] = spec.describe()
            d["attempts_to_win"] = attempts
        if self.path:
            Path(self.path).write_text(json.dumps(self._data, indent=2))

    def preferred(self, url: str) -> str | None:
        return self._data.get(self.domain(url), {}).get("best")

    def reorder(self, url: str, specs: list[Spec]) -> list[Spec]:
        """Put the known-good spec first, if we have one."""
        best = self.preferred(url)
        if not best:
            return specs
        return sorted(specs, key=lambda s: s.describe() != best)


# --- escalation -------------------------------------------------------------

@dataclass
class Attempt:
    spec: Spec
    ok: bool
    diagnosis: Diagnosis | None
    result: RunResult | None = None


@dataclass
class EscalationResult:
    ok: bool
    attempts: list[Attempt]
    winner: Spec | None = None
    stopped_early: str = ""
    plan: Plan | None = None
    budget: Budget | None = None

    def to_dict(self) -> dict:
        return {"ok": self.ok, "attempts": len(self.attempts),
                "winner": self.winner.describe() if self.winner else None,
                "stopped_early": self.stopped_early,
                "plan": self.plan.to_dict() if self.plan else None,
                "budget": self.budget.report() if self.budget else None}

    def summary(self) -> str:
        head = (f"succeeded on attempt {len(self.attempts)} "
                f"({self.winner.describe()})" if self.ok and self.winner
                else f"failed after {len(self.attempts)} attempt(s)")
        if self.stopped_early:
            head += f" — halted: {self.stopped_early}"
        return head


def escalate(graph: Graph, specs: list[Spec], browser_factory,
             url: str = "", memory: SiteMemory | None = None,
             adaptive: bool = True, max_attempts: int = 6,
             per_spec_retries: int = 1, sleep=time.sleep) -> EscalationResult:
    """Try specs until one succeeds.

    `adaptive` lets a diagnosis insert targeted suggestions ahead of the
    remaining ladder — a timeout should try a longer dwell before it tries a
    different engine.

    `per_spec_retries` bounds how often one spec may be retried after a
    retryable failure. It exists because an unbounded retry is not a retry
    policy, it is a way to never reach the rest of the ladder.
    """
    queue = list(memory.reorder(url, specs) if (memory and url) else specs)
    attempts: list[Attempt] = []
    tried: set[str] = set()
    retries: dict[str, int] = {}

    while queue and len(attempts) < max_attempts:
        spec = queue.pop(0)
        if spec.describe() in tried:
            continue
        tried.add(spec.describe())

        try:
            result = run_graph(graph, spec, browser_factory(spec))
        except Exception as e:  # driver construction or launch failure
            diag = classify(f"{type(e).__name__}: {e}", attempt=len(attempts))
            attempts.append(Attempt(spec, False, diag))
            if diag.terminal:
                if memory and url:
                    memory.record(url, spec, False, len(attempts))
                return EscalationResult(False, attempts, stopped_early=str(diag))
            continue

        if result.ok:
            attempts.append(Attempt(spec, True, None, result))
            if memory and url:
                memory.record(url, spec, True, len(attempts))
            return EscalationResult(True, attempts, winner=spec)

        page = ""
        try:
            page = result.context.data.get("page_text", "") or ""
        except AttributeError:
            pass
        diag = classify(result.context.error, page, attempt=len(attempts))
        attempts.append(Attempt(spec, False, diag, result))

        if memory and url:
            memory.record(url, spec, False, len(attempts))

        if diag.terminal:
            # challenge / block / auth: stop rather than escalate into a ban
            return EscalationResult(False, attempts, stopped_early=str(diag))

        if diag.response is Response.WAIT_RETRY:
            # Retry the same spec, but a bounded number of times. Without a cap
            # this starves the ladder: a timeout re-queues the failing spec at
            # the front and un-tries it, so an engine that can never succeed
            # (no JavaScript runtime, say) consumes every attempt and the
            # alternatives below it are never reached — which defeats the entire
            # point of escalating.
            used = retries.get(spec.describe(), 0)
            if used < per_spec_retries:
                retries[spec.describe()] = used + 1
                sleep(diag.backoff())
                tried.discard(spec.describe())
                queue.insert(0, spec)
                continue
            # give up on this spec and fall through to the alternatives

        if adaptive:
            for cand in reversed(suggest(spec, diag)):
                if cand.describe() not in tried:
                    queue.insert(0, cand)

    return EscalationResult(False, attempts)


# --- learned execution ------------------------------------------------------

def solve(graph: Graph, browser_factory, url: str, task: str = "",
          knowledge: Knowledge | None = None, budget: Budget | None = None,
          base: Spec | None = None, sector: str = "", html: str = "",
          shortlist: int = 3, explore: float = 0.1,
          sleep=time.sleep) -> EscalationResult:
    """Run a graph using learned experience instead of an exhaustive sweep.

    Order of operations:

    1. Build features for this request (site, org, sector, platform, task).
    2. Recall a cached solution for this exact site+task, if one exists.
    3. Otherwise rank the generic ladder by similarity-weighted success.
    4. Attempt the shortlist, stopping on a terminal diagnosis, an exhausted
       budget, or evidence that nothing left is likely to work.
    5. Record the outcome so the next similar request starts better.
    """
    knowledge = knowledge or Knowledge()
    budget = budget or Budget()
    features = Features.of(url, html=html, sector=sector, task=task)
    candidates = ladder(base or Spec(), depth=6)

    the_plan = plan(knowledge, features, candidates,
                    shortlist=shortlist, explore=explore)
    attempts: list[Attempt] = []
    tried: set[str] = set()
    queue: list[tuple[Spec, Estimate | None]] = list(the_plan.candidates)

    while queue:
        stop = budget.exhausted()
        if stop:
            return EscalationResult(False, attempts, stopped_early=stop,
                                    plan=the_plan, budget=budget)

        best_remaining = queue[0][1]
        early = budget.should_stop_early(best_remaining)
        if early:
            return EscalationResult(False, attempts, stopped_early=early,
                                    plan=the_plan, budget=budget)

        spec, _est = queue.pop(0)
        if spec.describe() in tried:
            continue
        tried.add(spec.describe())

        started = time.monotonic()
        try:
            result = run_graph(graph, spec, browser_factory(spec))
        except Exception as e:
            budget.spend(seconds=time.monotonic() - started)
            diag = classify(f"{type(e).__name__}: {e}", attempt=len(attempts))
            attempts.append(Attempt(spec, False, diag))
            knowledge.record(features, spec, ok=False)
            if diag.terminal:
                return EscalationResult(False, attempts, stopped_early=str(diag),
                                        plan=the_plan, budget=budget)
            continue

        budget.spend(seconds=time.monotonic() - started)

        if result.ok:
            attempts.append(Attempt(spec, True, None, result))
            knowledge.record(features, spec, ok=True, attempts=len(attempts))
            return EscalationResult(True, attempts, winner=spec,
                                    plan=the_plan, budget=budget)

        diag = classify(result.context.error,
                        result.context.data.get("page_text", ""),
                        attempt=len(attempts))
        attempts.append(Attempt(spec, False, diag, result))
        knowledge.record(features, spec, ok=False)

        if diag.terminal:
            return EscalationResult(False, attempts, stopped_early=str(diag),
                                    plan=the_plan, budget=budget)

        if diag.response is Response.WAIT_RETRY:
            sleep(diag.backoff())

        # a diagnosis may justify an option the plan did not shortlist
        for cand in suggest(spec, diag):
            if cand.describe() not in tried:
                est = knowledge.estimate(features, cand.describe())
                queue.insert(0, (cand, est))

    return EscalationResult(False, attempts, stopped_early="shortlist exhausted",
                            plan=the_plan, budget=budget)
