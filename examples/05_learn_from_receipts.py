#!/usr/bin/env python3
"""Get better at a job by doing it.

Run the plan. Keep the receipt. Fold it into evidence. Let the next search start
from what actually happened rather than from numbers somebody guessed when the
graph was drawn.

The journal is what makes it survive the process exiting.

    python examples/05_learn_from_receipts.py
"""
import pathlib
import random
import shutil
import tempfile

from browsergraph import execute, search
from browsergraph.compile import compile_route
from browsergraph.evidence import Evidence
from browsergraph.journal import Journal, replay
from browsergraph.quick import chain, graph, node, step
from browsergraph.workbench import OptimizationObjective, OptimizationProfile

# How the world really behaves. Nothing below is told this — it only decides
# whether a run worked.
TRUTH = {"fetch.a": 0.30, "fetch.b": 0.55, "fetch.c": 0.90,
         "tidy.x": 0.50, "tidy.y": 0.85}
BEST = {"fetch": "fetch.c", "tidy": "tidy.y"}

rng = random.Random(20260810)


def make(candidate):
    def run_step(**kw):
        if rng.random() > TRUTH[candidate]:
            raise RuntimeError(f"{candidate} failed this time")
        return {"by": candidate}
    return run_step


def build():
    nodes = ([node(c, "fetch", gives=[("out", "R")], deterministic=False)
              for c in TRUTH if c.startswith("fetch")]
             + [node(c, "tidy", [("in", "R")], [("out", "R")])
                for c in TRUTH if c.startswith("tidy")])
    steps = [
        step("fetch", "Fetch", [], [("out", "R")], "fetch",
             [c for c in TRUTH if c.startswith("fetch")]),
        step("tidy", "Tidy", [("in", "R")], [("out", "R")], "tidy",
             [c for c in TRUTH if c.startswith("tidy")]),
    ]
    return graph("Fetch and tidy", "Use whichever combination actually works.",
                 steps, nodes, chain("fetch", "tidy"),
                 profiles=[OptimizationProfile(id="p", objectives=(
                     OptimizationObjective("quality", "maximize", 1.0),))])


def true_quality(route):
    out = 1.0
    for candidate in route.values():
        out *= TRUTH[candidate]
    return out


bench = build()
runtime = execute.Runtime({c: make(c) for c in TRUTH})
workspace = pathlib.Path(tempfile.mkdtemp())
journal = Journal(workspace / "runs.jsonl")
store = Evidence()

cold = search.within(bench, bench.optimization_profiles[0], evaluations=20,
                     evidence=store, explore=0.0)
print(f"before any runs: {cold.route}  true quality {true_quality(cold.route):.3f}")

for index in range(120):
    found = search.within(bench, bench.optimization_profiles[0],
                          evaluations=20, evidence=store, seed=index)
    result = execute.run(compile_route(bench, found.route), runtime, strict=False)
    receipt = result.receipt(task=f"run-{index}")
    journal.record(receipt)          # durable, and in order
    store.from_receipt(receipt)      # and folded into what is believed

warm = search.within(bench, bench.optimization_profiles[0], evaluations=20,
                     evidence=store, explore=0.0)
print(f"after 120 runs:  {warm.route}  true quality {true_quality(warm.route):.3f}")
print(f"the best there is:                    {true_quality(BEST):.3f}")

print()
print(journal.text())

# The journal is not just a log. Lose the evidence and rebuild it from receipts.
rebuilt = replay(journal.path)
same = all(abs(rebuilt.posterior(c).rate - store.posterior(c).rate) < 1e-9
           for c in TRUTH)
print(f"\nevidence rebuilt from the journal alone matches: {same}")

shutil.rmtree(workspace, ignore_errors=True)
