"""Tasks with a known right answer, for finding out whether the search works.

The benchmark compares strategies on real work and reports which won. It cannot
tell you *why* one won, because on a real task nobody knows the best route — so
"searching tied with random" leaves two explanations standing and no way to
choose: the space was easy, or the search is not doing anything.

These tasks have a hidden ground truth. Every candidate is assigned a quality
nobody can see, the score of a route is computed from those, and the best
possible score is therefore known before anything runs. That converts an opinion
into an experiment.

    from browsergraph import arena, benchmark

    task = arena.needle(steps=6, width=5, seed=1)
    got = benchmark.compare(task.workbench, task.runtime, verify=task.verify,
                            budget=12, repeats=5)
    print(got.text())
    print(f"the best possible score is {task.best_score:.4f}")

Four shapes, and the point is that they answer different questions:

* **`needle`** — each step contributes independently, quality compounds. This is
  the library's headline claim in its purest form: cost is the *sum* of the
  candidate counts, not their product, so a per-step posterior should find a
  good route in far fewer runs than there are routes. If searching does not beat
  random here, the claim is wrong.
* **`flat`** — every candidate is identical. Searching *must not* win, and a
  strategy that appears to is reading noise. The negative control, and the one
  worth running first.
* **`paired`** — two specific candidates are excellent apart and terrible
  together. Per-step independence is exactly false, which is the assumption the
  cheap search rests on, so this is where it should struggle and where
  `interactions()` should have something to say.
* **`noisy`** — `needle` with observation noise on every run. Distinguishes a
  loop that converges from one that latches onto a lucky first result.

Everything is deterministic given a seed, including the noise, because an
experiment whose result moves between runs cannot be argued with.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from browsergraph.execute import Runtime
from browsergraph.quick import chain, graph, node, step
from browsergraph.workbench import (
    OptimizationObjective,
    OptimizationProfile,
    WorkbenchDefinition,
)


@dataclass
class Task:
    """A graph, its runtime, its judge, and the answer nobody running it can see."""
    name: str
    workbench: WorkbenchDefinition
    runtime: Runtime
    verify: Any
    #: candidate id -> the quality it really has
    truth: dict[str, float] = field(default_factory=dict)
    best_route: dict[str, str] = field(default_factory=dict)
    best_score: float = 0.0
    worst_score: float = 0.0
    note: str = ""

    def gap(self, score: float) -> float:
        """How far a score is from perfect, as a fraction of the whole range.

        0.0 is the best route; 1.0 is the worst. Reported rather than the raw
        score because "0.61" means nothing without knowing that the range was
        0.58 to 0.63 — which is the difference between a search that worked and
        a space where nothing mattered.
        """
        span = self.best_score - self.worst_score
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (self.best_score - score) / span))


def _build(name: str, steps: int, width: int, truth: dict[str, float],
           score_route, note: str) -> Task:
    nodes, stages = [], []
    for s in range(steps):
        ids = [f"s{s}.c{c}" for c in range(width)]
        takes = [("in", "V")] if s else []
        for cid in ids:
            nodes.append(node(cid, f"cap{s}", takes, [("out", "V")]))
        stages.append(step(f"s{s}", f"Step {s}", takes, [("out", "V")],
                           f"cap{s}", ids))

    bench = graph(f"arena.{name}", note, stages, nodes,
                  chain(*[f"s{i}" for i in range(steps)]),
                  profiles=[OptimizationProfile(
                      id="p", name="quality",
                      objectives=(OptimizationObjective("quality", "maximize", 1.0),))])

    # Every node is a pass-through. The work is entirely in the *choice*, which
    # is what makes this an experiment about searching rather than about code.
    runtime = Runtime({n.id: (lambda **kw: kw.get("in", 1.0)) for n in nodes})

    def verify(run):
        if not run.ok:
            return False, 0.0
        route = {s.stage: s.candidate for s in run.steps}
        return True, float(score_route(route))

    best = {f"s{s}": max((f"s{s}.c{c}" for c in range(width)),
                         key=lambda cid: truth[cid])
            for s in range(steps)}
    worst = {f"s{s}": min((f"s{s}.c{c}" for c in range(width)),
                          key=lambda cid: truth[cid])
             for s in range(steps)}
    return Task(name=name, workbench=bench, runtime=runtime, verify=verify,
                truth=truth, best_route=best, best_score=score_route(best),
                worst_score=score_route(worst), note=note)


def needle(steps: int = 6, width: int = 5, seed: int = 0) -> Task:
    """Independent per-step qualities, compounding. The headline claim, isolated.

    A route's score is the product of its candidates' hidden qualities, so the
    best route is the best candidate at every step and nothing interacts. That
    is precisely the condition under which per-step posteriors are valid, and
    the condition the whole "sum not product" argument assumes.
    """
    rng = random.Random(seed)
    truth = {f"s{s}.c{c}": round(rng.uniform(0.55, 0.99), 4)
             for s in range(steps) for c in range(width)}

    def score(route):
        total = 1.0
        for cid in route.values():
            total *= truth[cid]
        return total

    return _build("needle", steps, width, truth, score,
                  "independent per-step quality, compounding")


def flat(steps: int = 6, width: int = 5, seed: int = 0) -> Task:
    """Every candidate identical. The negative control.

    Searching must not win here. A strategy that appears to is reading noise,
    and a benchmark that cannot produce this result is not measuring anything.
    """
    truth = {f"s{s}.c{c}": 0.8 for s in range(steps) for c in range(width)}

    def score(route):
        return 0.8 ** len(route)

    return _build("flat", steps, width, truth, score,
                  "every candidate identical — searching must not win")


def paired(steps: int = 6, width: int = 5, seed: int = 0,
           penalty: float = 0.25) -> Task:
    """Two candidates that are excellent apart and terrible together.

    Per-step independence is exactly false here, which is the assumption the
    cheap search rests on. A per-step posterior learns that both members are
    good — they are, on average, because they are only bad in each other's
    company — and keeps proposing the pair. This is what `interactions()` is
    for, and this task is how you find out whether it helps.
    """
    rng = random.Random(seed)
    truth = {f"s{s}.c{c}": round(rng.uniform(0.55, 0.99), 4)
             for s in range(steps) for c in range(width)}
    left, right = "s0.c0", "s1.c0"
    truth[left] = truth[right] = 0.99          # the best of their steps, alone

    def score(route):
        total = 1.0
        for cid in route.values():
            total *= truth[cid]
        if route.get("s0") == left and route.get("s1") == right:
            total *= penalty
        return total

    task = _build("paired", steps, width, truth, score,
                  "two candidates that clash — independence is false here")
    # The declared best route is the pairwise-blind one, so `best_score` would
    # be wrong. Recompute it honestly over the whole space.
    best_route, best_value = {}, float("-inf")
    for combo in _every_route(steps, width):
        value = score(combo)
        if value > best_value:
            best_route, best_value = combo, value
    task.best_route, task.best_score = best_route, best_value
    return task


def noisy(steps: int = 6, width: int = 5, seed: int = 0,
          noise: float = 0.05) -> Task:
    """`needle`, but every run's score is disturbed.

    Separates a loop that converges from one that latched onto a lucky first
    result. The truth does not move; only what a run reports about it does,
    which is the situation every real measurement is in.
    """
    task = needle(steps, width, seed)
    rng = random.Random(seed + 977)
    inner = task.verify

    def verify(run):
        ok, value = inner(run)
        return ok, (value * (1.0 + rng.gauss(0.0, noise)) if ok else value)

    task.verify = verify
    task.note = f"independent quality, plus {noise:.0%} observation noise"
    return task


def _every_route(steps: int, width: int):
    import itertools

    for combo in itertools.product(range(width), repeat=steps):
        yield {f"s{s}": f"s{s}.c{c}" for s, c in enumerate(combo)}


TASKS = {"needle": needle, "flat": flat, "paired": paired, "noisy": noisy}


def get(name: str, **kwargs) -> Task:
    if name not in TASKS:
        raise KeyError(f"no arena task {name!r}; available: "
                       f"{', '.join(sorted(TASKS))}")
    return TASKS[name](**kwargs)
