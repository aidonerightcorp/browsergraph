"""One call: try routes, run them, judge them, keep the best and a fallback.

Everything needed for this already existed and none of it was joined up. To get
a task solved you had to search for a route, compile it, run it, decide whether
the output was any good, fold that into evidence, and go round again — six
imports and a loop that everybody writes slightly differently, which means
nobody's results compare with anybody else's.

`solve` is that loop, once:

    attempt = pick a route from what is known
              compile it            -> a type error is not a failed run
              run it                -> receipts, artifacts, bounded if asked
              judge the *output*    -> not whether the code threw
              remember what happened
    repeat, then rank and return a champion and its fallbacks

Two things it insists on, because they are the difference between a result and
a number:

**Judging is separate from running.** `verify` receives the run's outputs and
says whether they are acceptable. Without it "did it work" means "did it not
raise", and a route that returns an empty record succeeds by that measure — the
failure this whole library was arranged against. A default is provided and it is
deliberately weak, and says so.

**A fallback is part of the answer.** A champion with no runner-up is a single
point of failure dressed as a result. `solve` returns the best route *and* the
best route that differs from it, so the thing you deploy has somewhere to go
when its first choice stops working.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from browsergraph.compile import CompileError, compile_route
from browsergraph.execute import Run, Runtime
from browsergraph.policy import Policy
from browsergraph.workbench import OptimizationProfile, WorkbenchDefinition

#: Receives the finished run and returns either a bool, or a `(ok, score)` pair
#: when there is a number worth ranking on. Anything else is treated as a bool.
Verifier = Callable[[Run], Any]


def _accept_anything_that_ran(run: Run) -> tuple[bool, float]:
    """The default, and deliberately a poor one.

    It asks whether every step reported success, which is exactly the measure
    that cannot tell "extracted three products" from "extracted none without
    raising". Pass a real `verify` for anything you care about; this exists so
    the first call works, not so the last one does.
    """
    return bool(run.ok), 1.0 if run.ok else 0.0


@dataclass
class Attempt:
    """One route, tried."""
    route: dict[str, str] = field(default_factory=dict)
    ok: bool = False
    score: float = 0.0
    seconds: float = 0.0
    reason: str = ""
    plan: str = ""
    run: Run | None = None

    def to_dict(self) -> dict:
        return {"route": dict(self.route), "ok": self.ok,
                "score": round(self.score, 6), "seconds": round(self.seconds, 4),
                "plan": self.plan, "reason": self.reason}


@dataclass
class Solution:
    """What solving produced, and how much of the space it saw."""
    champion: dict[str, str] = field(default_factory=dict)
    fallbacks: list[dict[str, str]] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    score: float = 0.0
    total_routes: int = 0
    seconds: float = 0.0
    evidence: Any = None

    @property
    def ok(self) -> bool:
        return bool(self.champion)

    @property
    def worked(self) -> list[Attempt]:
        return [a for a in self.attempts if a.ok]

    def to_dict(self) -> dict:
        return {"champion": dict(self.champion),
                "fallbacks": [dict(f) for f in self.fallbacks],
                "score": round(self.score, 6),
                "attempted": len(self.attempts),
                "worked": len(self.worked),
                "total_routes": self.total_routes,
                "seconds": round(self.seconds, 3),
                "attempts": [a.to_dict() for a in self.attempts]}

    def text(self, bench: WorkbenchDefinition | None = None) -> str:
        lines = []
        if not self.champion:
            lines.append(f"no route worked — {len(self.attempts)} tried")
        else:
            lines.append(f"champion scored {self.score:.4f} "
                         f"({len(self.worked)} of {len(self.attempts)} tried "
                         f"worked, out of {self.total_routes:,} possible)")
            for stage, candidate in self.champion.items():
                lines.append(f"    {stage:<16} {candidate}")
        for index, fallback in enumerate(self.fallbacks, 1):
            differs = [s for s, c in fallback.items()
                       if self.champion.get(s) != c]
            lines.append(f"  fallback {index}: differs at "
                         f"{', '.join(differs) or '(nothing)'}")
        failed = [a for a in self.attempts if not a.ok]
        if failed:
            lines.append(f"  {len(failed)} did not work; first reason: "
                         f"{failed[0].reason[:90]}")
        lines.append(f"  {self.seconds:.2f}s")
        return "\n".join(lines)


def solve(bench: WorkbenchDefinition, runtime: Runtime, *,
          verify: Verifier | None = None,
          inputs: Mapping[str, Any] | None = None,
          profile: OptimizationProfile | None = None,
          policy: Policy | None = None,
          attempts: int = 8, budget: int = 60,
          workspace: str | None = None, workers: int = 1,
          fallbacks: int = 1, evidence=None, journal=None,
          context: Sequence[str] = ("global",), seed: int = 0) -> Solution:
    """Try routes until one works well, and keep a fallback.

    `attempts` bounds how many routes are *run*, which is the expensive part —
    `budget` bounds how many are *scored* while choosing each one, which is
    cheap. Confusing the two is how a solve that looked bounded takes an hour.

    A route already tried is skipped and the search asked again with a different
    seed. Once a search has settled it proposes its favourite repeatedly, which
    is correct behaviour and not a reason to stop looking.

    Every attempt is remembered in `evidence`, so the next route is chosen from
    what actually happened rather than from the numbers in the graph. Pass an
    existing store to carry learning across calls; pass a `journal` and it
    survives the process exiting.
    """
    from browsergraph import execute, search
    from browsergraph.evidence import Evidence

    began = time.monotonic()
    store = evidence if evidence is not None else Evidence()
    judge = verify or _accept_anything_that_ran
    picked = profile or (bench.optimization_profiles[0]
                         if bench.optimization_profiles else None)
    if picked is None:
        raise ValueError(
            "solve needs an optimization profile: either on the workbench or "
            "passed in. Without one there is nothing to rank routes by.")

    solution = Solution(total_routes=bench.route_count(), evidence=store)
    tried: set[tuple] = set()
    offset = 0
    repeats = 0

    def untried() -> dict[str, str]:
        """A route nobody has run yet, when the whole space is small enough.

        Searching is for spaces too big to look at. A four-route graph is not
        one, and on it `within` enumerates and returns the same winner however
        the seed moves — so asking again is asking the same question. Measured
        on a four-route example: `attempts=6` produced two attempts and stopped,
        with two routes never run and nothing saying so.

        Only reached once the search has repeated itself, so it costs nothing on
        the large spaces this is not for.
        """
        try:
            for candidate in search.eligible_routes(bench, policy=policy,
                                                    limit=10_000):
                if tuple(sorted(candidate.items())) not in tried:
                    return candidate
        except search.SpaceTooLarge:
            pass          # too big to enumerate: that is what the search is for
        return {}

    while len(solution.attempts) < attempts:
        found = search.within(bench, picked, evaluations=budget, policy=policy,
                              evidence=store, context=context,
                              seed=seed + offset)
        offset += 1
        route = dict(found.route)
        if not route:
            break

        key = tuple(sorted(route.items()))
        if key in tried:
            # Ask again with a different seed rather than giving up. An early
            # version stopped at the first repeat, so a three-route space got
            # two attempts and reported itself thorough — the search settles on
            # a good route quickly and then proposing it twice is *expected*,
            # not a signal that exploration is over.
            repeats += 1
            route = untried()
            key = tuple(sorted(route.items()))
            if not route or repeats >= max(3, attempts):
                break
        repeats = 0
        tried.add(key)

        attempt = Attempt(route=route)
        step_began = time.monotonic()
        try:
            plan = compile_route(bench, route)
            attempt.plan = plan.digest
        except CompileError as problem:
            attempt.reason = problem.problems[0]
            attempt.seconds = time.monotonic() - step_began
            solution.attempts.append(attempt)
            continue

        run = execute.run(plan, runtime, inputs, workspace=workspace,
                          workers=workers, strict=True)
        attempt.run = run
        attempt.seconds = time.monotonic() - step_began

        verdict = judge(run)
        if isinstance(verdict, tuple) and len(verdict) == 2:
            attempt.ok, attempt.score = bool(verdict[0]), float(verdict[1])
        else:
            attempt.ok = bool(verdict)
            attempt.score = 1.0 if attempt.ok else 0.0
        if not attempt.ok and not attempt.reason:
            bad = next((s for s in run.steps if not s.ok), None)
            attempt.reason = (bad.error if bad else
                              "ran, but the output was not acceptable")

        # The receipt records what *ran*; the verdict records whether it was any
        # good. Folding the verdict in is what stops the loop learning that a
        # route which completes is a route that worked.
        receipt = run.receipt(task=f"solve-{len(solution.attempts)}")
        receipt.ok = attempt.ok
        store.from_receipt(receipt, context=context[0] if context else "global")
        if journal is not None:
            journal.record(receipt)
        solution.attempts.append(attempt)

    ranked = sorted(solution.worked, key=lambda a: -a.score)
    if ranked:
        solution.champion = dict(ranked[0].route)
        solution.score = ranked[0].score
        for other in ranked[1:]:
            if len(solution.fallbacks) >= fallbacks:
                break
            if other.route != solution.champion:
                solution.fallbacks.append(dict(other.route))

    solution.seconds = time.monotonic() - began
    return solution


def outputs_are_not_empty(*stages: str) -> Verifier:
    """A verifier for the commonest real acceptance test there is.

    "It ran" and "it produced something" are different claims, and the gap
    between them is where scrapers, extractors and batch jobs quietly die. This
    checks the named stages produced something with a length, and scores on how
    much — so a route returning three records beats one returning one.
    """
    def judge(run: Run) -> tuple[bool, float]:
        if not run.ok:
            return False, 0.0
        counted = 0
        for stage in stages:
            try:
                value = run.output(stage)
            except KeyError:
                return False, 0.0
            if value is None:
                return False, 0.0
            counted += len(value) if hasattr(value, "__len__") else 1
        return counted > 0, float(counted)
    return judge
