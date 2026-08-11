"""Does the searching actually help? Measured, against what you would have done.

This library's whole argument is that keeping the options open and choosing
between them beats picking one and writing it down. That is a claim about
outcomes, and nothing in here was testing it. Every notebook shows the machinery
working; none of them shows it *winning*, and a reader is entitled to ask.

So: run the same task several ways and put the numbers side by side.

    from browsergraph import benchmark, packs

    pack = packs.get("tabular")
    print(benchmark.compare(pack.workbench(), pack.runtime(**pack.example()),
                            verify=score_it).text())

Four strategies, chosen because they are what somebody would actually do:

* **first** — the first candidate at every step. This is the hand-written
  pipeline: one plausible choice per stage, never revisited. It is the baseline
  everything else has to beat, and it runs exactly one route because that is
  what writing a script costs.
* **random** — routes drawn at random, same run budget as the searches. The
  control that matters most. A search that cannot beat random sampling is not a
  search, it is a ritual, and this is the comparison that says so.
* **greedy** — the built-in search with no evidence: score by declared priors,
  run the winner.
* **solve** — the full loop. Try, judge the output, fold the verdict into
  evidence, choose the next route from what happened.

**Every strategy gets the same number of runs**, because otherwise the finding
is "whichever we let run more won" and that is not a finding. `first` is the
exception and it is stated: it runs once, because that is the honest cost of the
thing it stands for.

Three things this module will not do:

**It will not hide a negative result.** If random sampling matches the search,
`text()` says so in the summary line. That outcome is worth publishing — it
tells you the space is flat and the machinery is not earning its keep here.

**It will not compare on training data.** The verifier you pass is the only
judge, and it is the same judge for every strategy. Handing the searches a
different one is how a benchmark gets written that its author believes.

**It will not average away the variance.** `repeats` runs the whole comparison
several times with different seeds and reports the spread, because a single seed
deciding which strategy wins is the most common way this kind of table lies.
"""
from __future__ import annotations

import math
import random
import statistics
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from browsergraph.compile import CompileError, compile_route
from browsergraph.execute import Runtime
from browsergraph.workbench import OptimizationProfile, WorkbenchDefinition

STRATEGIES = ("first", "random", "greedy", "solve", "guided")

#: A win below this is a tie. Two medians differing in the twelfth decimal are
#: not a result, and reporting one as "+0.0% better" is how a benchmark starts
#: telling its author what they hoped.
TIE = 0.01


@dataclass
class Result:
    """One strategy, one seed."""
    strategy: str
    seed: int = 0
    score: float = 0.0
    ok: bool = False
    runs: int = 0
    seconds: float = 0.0
    route: dict[str, str] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict:
        return {"strategy": self.strategy, "seed": self.seed,
                "score": round(self.score, 6), "ok": self.ok,
                "runs": self.runs, "seconds": round(self.seconds, 4),
                "route": dict(self.route), "note": self.note}


@dataclass
class Comparison:
    """Every strategy, every seed, and what it adds up to."""
    results: list[Result] = field(default_factory=list)
    budget: int = 0
    total_routes: int = 0

    def by_strategy(self) -> dict[str, list[Result]]:
        out: dict[str, list[Result]] = {}
        for result in self.results:
            out.setdefault(result.strategy, []).append(result)
        return out

    def best(self, strategy: str) -> float:
        """Median score, not mean. One catastrophic seed should not decide it."""
        got = [r.score for r in self.by_strategy().get(strategy, []) if r.ok]
        return statistics.median(got) if got else float("-inf")

    def lift(self, strategy: str, over: str = "first") -> float:
        """How much better than the baseline, as a fraction of the baseline."""
        base, mine = self.best(over), self.best(strategy)
        if base in (0.0, float("-inf")) or mine == float("-inf"):
            return 0.0
        return (mine - base) / abs(base)

    @property
    def verdict(self) -> str:
        """The honest one-line reading, including when it is unflattering.

        Written to be quotable, because the alternative is a reader taking a
        table of numbers and quoting the flattering row.
        """
        search, chance, base = self.best("solve"), self.best("random"), self.best("first")
        if search == float("-inf"):
            return "no strategy produced a working route"

        # A tie is a tie. An earlier version tested `search <= base` and then
        # reported a margin of +0.0% as a win, because two medians differing in
        # the twelfth decimal are not equal — which is precisely the kind of
        # claim this module exists to refuse.
        margin = self.lift("solve")
        if base != float("-inf") and margin <= TIE:
            return ("searching did NOT beat the first-candidate baseline here — "
                    "the space is flat, the verifier cannot see what varies, or "
                    "the budget is too small to find the difference")
        if chance != float("-inf") and self.lift("solve", "random") <= TIE:
            return ("searching did NOT beat random sampling at the same budget — "
                    "worth knowing, and worth not calling it a search")

        cheaper = self.best("greedy")
        if cheaper != float("-inf") and cheaper > search:
            return (f"searching beat the baseline by {margin:+.1%}, but plain "
                    f"greedy on the declared priors beat it with one run — the "
                    f"priors in this graph are already good")
        return (f"searching beat the hand-written baseline by {margin:+.1%} "
                f"and beat random sampling at the same budget")

    def text(self) -> str:
        lines = [f"{len(self.by_strategy())} strategies, {self.budget} runs each "
                 f"(except 'first'), out of {self.total_routes:,} routes",
                 f"{'strategy':<10}{'score':>12}{'vs first':>11}"
                 f"{'runs':>7}{'seconds':>9}  seeds"]
        for name in STRATEGIES:
            rows = self.by_strategy().get(name)
            if not rows:
                continue
            score = self.best(name)
            shown = "—" if score == float("-inf") else f"{score:.4g}"
            lift = "" if name == "first" else f"{self.lift(name):+.1%}"
            lines.append(
                f"{name:<10}{shown:>12}{lift:>11}"
                f"{statistics.median([r.runs for r in rows]):>7.0f}"
                f"{statistics.median([r.seconds for r in rows]):>9.2f}"
                f"  {len(rows)}")
        spread = [r.score for r in self.by_strategy().get("solve", []) if r.ok]
        if len(spread) > 1:
            lines.append(f"  solve across seeds: {min(spread):.4g} to "
                         f"{max(spread):.4g} — one seed deciding a winner is "
                         f"how this kind of table lies")
        lines.append("  " + self.verdict)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"budget": self.budget, "total_routes": self.total_routes,
                "verdict": self.verdict,
                "results": [r.to_dict() for r in self.results]}


def _run_route(bench, runtime, route, verify, inputs, workspace) -> tuple[bool, float]:
    from browsergraph import execute

    try:
        plan = compile_route(bench, route)
    except CompileError:
        return False, 0.0
    run = execute.run(plan, runtime, inputs, workspace=workspace, strict=True)
    try:
        verdict = verify(run)
    except Exception:                       # noqa: BLE001 - a verdict, not a crash
        # A verifier that reaches for an output a failed run never produced is
        # not a broken verifier, it is one being asked to judge nothing. The
        # honest reading is "not acceptable", and crashing the whole comparison
        # because one route of forty failed would lose every other number.
        return False, 0.0
    if isinstance(verdict, tuple) and len(verdict) == 2:
        return bool(verdict[0]), float(verdict[1])
    return bool(verdict), (1.0 if verdict else 0.0)


def _first_route(bench) -> dict[str, str]:
    return {s.id: s.candidates[0] for s in bench.leaf_stages if s.candidates}


def compare(bench: WorkbenchDefinition, runtime: Runtime, *,
            verify: Callable[[Any], Any],
            profile: OptimizationProfile | None = None,
            budget: int = 8, repeats: int = 3,
            inputs: Mapping[str, Any] | None = None,
            workspace: str | None = None,
            strategies: Sequence[str] = STRATEGIES,
            library: Sequence[Any] = (), propose=None,
            seed: int = 0) -> Comparison:
    """Run the task every way and return the table.

    `budget` is the number of routes each strategy is allowed to **run**, which
    is the expensive part and the only fair currency. `repeats` re-runs the
    whole comparison with different seeds, because one seed deciding the winner
    is how a benchmark ends up saying whatever its author hoped.
    """
    from browsergraph import search
    from browsergraph import solve as _solve
    from browsergraph.evidence import Evidence

    # `guided` is the only strategy that can change the shape, so without a
    # library there is nothing for it to add and it is dropped rather than
    # reported as a tie it never had a chance to break.
    strategies = [s for s in strategies
                  if s != "guided" or library or propose]

    picked = profile or (bench.optimization_profiles[0]
                         if bench.optimization_profiles else None)
    if picked is None:
        raise ValueError(
            "benchmarking needs an optimization profile to rank routes by — "
            "the same one every strategy is judged with.")

    comparison = Comparison(budget=budget, total_routes=bench.route_count())

    for repeat in range(repeats):
        offset = seed + repeat * 1000

        if "first" in strategies:
            began = time.monotonic()
            route = _first_route(bench)
            ok, score = _run_route(bench, runtime, route, verify, inputs, workspace)
            comparison.results.append(Result(
                "first", offset, score, ok, 1, time.monotonic() - began, route,
                "one plausible choice per stage, never revisited"))

        if "random" in strategies:
            began = time.monotonic()
            rng = random.Random(offset)
            best_score, best_route, any_ok = float("-inf"), {}, False
            for _ in range(budget):
                route = {s.id: rng.choice(list(s.candidates))
                         for s in bench.leaf_stages if s.candidates}
                ok, score = _run_route(bench, runtime, route, verify, inputs,
                                       workspace)
                any_ok |= ok
                if ok and score > best_score:
                    best_score, best_route = score, route
            comparison.results.append(Result(
                "random", offset, best_score if any_ok else 0.0, any_ok, budget,
                time.monotonic() - began, best_route,
                "the control: a search that cannot beat this is not a search"))

        if "greedy" in strategies:
            began = time.monotonic()
            found = search.propose(bench, picked, strategy="greedy")
            ok, score = _run_route(bench, runtime, dict(found.route), verify,
                                   inputs, workspace)
            comparison.results.append(Result(
                "greedy", offset, score, ok, 1, time.monotonic() - began,
                dict(found.route), "declared priors only, no evidence"))

        if "solve" in strategies:
            began = time.monotonic()
            answer = _solve.solve(bench, runtime, verify=verify, inputs=inputs,
                                  profile=picked, attempts=budget,
                                  workspace=workspace, evidence=Evidence(),
                                  seed=offset)
            comparison.results.append(Result(
                "solve", offset, answer.score, answer.ok, len(answer.attempts),
                time.monotonic() - began, dict(answer.champion),
                "try, judge the output, learn, choose again"))

        if "guided" in strategies:
            comparison.results.append(_guided(
                bench, runtime, verify, picked, budget, offset, inputs,
                workspace, library, propose))

    return comparison


def _guided(bench, runtime, verify, picked, budget, offset, inputs, workspace,
            library, propose) -> Result:
    """Change the *shape*, then search inside each shape that compiles.

    The only strategy here that can reach a graph the others cannot, and the
    only one whose budget needs arguing about. It gets the same total number of
    runs as everything else, split across the original graph and each variant —
    so if it wins it is not because it was allowed to run more.

    A variant that fails to compile costs nothing and is counted, because the
    refusal rate is what says whether the proposer is worth its latency.
    """
    from browsergraph import edits as _edits
    from browsergraph import solve as _solve
    from browsergraph.evidence import Evidence

    began = time.monotonic()
    proposals = (propose or _edits.mechanical)(bench, library)
    tried = _edits.variants(bench, proposals, library=library)

    graphs = [bench] + [o.workbench for o in tried.accepted]
    best_score, best_route, any_ok, runs = float("-inf"), {}, False, 0

    # Successive halving, not an even split.
    #
    # The even split was actively harmful and the haystack task proved it: with
    # eighty legal edits and forty runs, every graph got one run, which is a
    # single random route each and scores *worse than not editing at all*. A
    # proposer offering many options was punished for offering them, so the
    # comparison measured the allocator rather than the proposals.
    #
    # Halving spends a little on everything, keeps the better half, and spends
    # again — so eighty cheap looks narrow to a few deep ones. It is the
    # standard answer to exactly this shape of problem, and it makes a wide
    # proposer merely slower rather than worse.
    alive = list(graphs)
    while alive and runs < budget:
        rounds_left = max(1, math.ceil(math.log2(len(alive))) + 1) if len(alive) > 1 else 1
        per_graph = max(1, (budget - runs) // (len(alive) * rounds_left))
        scored: list[tuple[float, Any, dict]] = []
        for graph in alive:
            if runs >= budget:
                break
            answer = _solve.solve(graph, runtime, verify=verify, inputs=inputs,
                                  profile=picked,
                                  attempts=min(per_graph, budget - runs),
                                  workspace=workspace, evidence=Evidence(),
                                  seed=offset + runs)
            runs += len(answer.attempts)
            any_ok |= answer.ok
            score = answer.score if answer.ok else float("-inf")
            scored.append((score, graph, dict(answer.champion)))
            if answer.ok and score > best_score:
                best_score, best_route = score, dict(answer.champion)

        if len(scored) <= 1:
            break
        scored.sort(key=lambda row: -row[0])
        alive = [graph for _s, graph, _r in scored[:max(1, len(scored) // 2)]]
        if len(alive) == len(graphs):       # nothing was eliminated; stop
            break

    return Result(
        "guided", offset, best_score if any_ok else 0.0, any_ok, runs,
        time.monotonic() - began, best_route,
        f"{len(tried.accepted)} of {len(proposals)} edits compiled "
        f"({tried.refusal_rate:.0%} refused)")
