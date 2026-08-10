#!/usr/bin/env python3
"""One call: try routes, judge the output, keep the best and a fallback.

The other examples show the parts. This is the part that joins them, and it is
the one you would actually call:

    solve(bench, runtime, verify=...) -> champion + fallback + evidence

Two things it insists on. Judging is separate from running, because "did it
work" must not mean "did it not raise". And a fallback comes back with the
champion, because a best route with no runner-up is a single point of failure
dressed as a result.

    python examples/07_solve_it_for_me.py
"""
from browsergraph import execute, solve
from browsergraph.quick import chain, graph, node, passthrough, step
from browsergraph.workbench import OptimizationObjective, OptimizationProfile

# Five readings. Two are missing, which is the whole question: fill them,
# drop them, or leave them alone?
RAW = [{"v": 1}, {"v": None}, {"v": 3}, {"v": None}, {"v": 5}]

nodes = [
    node("load.rows", "load", gives=[("out", "Rows")]),
    node("impute.median", "impute", [("in", "Rows")], [("out", "Rows")]),
    node("impute.drop", "impute", [("in", "Rows")], [("out", "Rows")]),
    # Doing nothing is a candidate, not a missing step. Two routes that differ
    # only in whether they imputed stay comparable; a graph with the step
    # deleted is a different graph, and evidence from it cannot be pooled.
    passthrough("impute.none", "impute", "Rows"),
    node("check.rows", "check", [("in", "Rows")], [("out", "Rows")]),
]
steps = [
    step("load", "Load the readings", [], [("out", "Rows")], "load", ["load.rows"]),
    step("impute", "Fill the gaps", [("in", "Rows")], [("out", "Rows")],
         "impute", ["impute.median", "impute.drop", "impute.none"]),
    step("check", "Keep usable rows", [("in", "Rows")], [("out", "Rows")],
         "check", ["check.rows"]),
]
bench = graph("Clean the readings",
              "Fill the gaps if that helps, and keep every usable row.",
              steps, nodes, chain("load", "impute", "check"),
              profiles=[OptimizationProfile(id="p", objectives=(
                  OptimizationObjective("quality", "maximize", 1.0),))])

runtime = execute.Runtime({
    "load.rows": lambda: [dict(r) for r in RAW],
    "impute.median": lambda **kw: [{"v": 3 if r["v"] is None else r["v"]}
                                   for r in kw["in"]],
    "impute.drop": lambda **kw: [r for r in kw["in"] if r["v"] is not None],
    "impute.none": lambda **kw: kw["in"],
    "check.rows": lambda **kw: [r for r in kw["in"] if r["v"] is not None],
})

# Every one of these routes runs without raising. Only the verifier can tell
# them apart — which is exactly why it is a separate argument.
found = solve.solve(bench, runtime,
                    verify=solve.outputs_are_not_empty("check"),
                    attempts=8)

print(found.text())
print("\nwhat each route actually produced:")
for attempt in found.attempts:
    print(f"  {attempt.route['impute']:<16} kept {attempt.score:.0f} of "
          f"{len(RAW)} rows")

print("\nWithout the verifier, all three 'succeed' — none of them raise:")
lenient = solve.solve(bench, runtime, attempts=6)
print(f"  every attempt ok: {all(a.ok for a in lenient.attempts)}")
print("  which is how a pipeline that quietly drops 40% of your data passes.")

print(f"\nlearned about impute.median over "
      f"{found.evidence.posterior('impute.median').runs} run(s)")
