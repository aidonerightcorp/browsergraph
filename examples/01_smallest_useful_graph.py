#!/usr/bin/env python3
"""The smallest graph that does something, start to finish.

Read this one first. The whole model is here:

  steps say what must happen · nodes say what could do it ·
  compiling checks the types and freezes the choice · running does the work

    python examples/01_smallest_useful_graph.py
"""
from browsergraph import execute
from browsergraph.compile import compile_route
from browsergraph.quick import chain, graph, node, problems, step

# 1. What has to happen, and what could do each part.
nodes = [
    node("read.csv", "read", gives=[("out", "Rows")]),
    node("clean.trim", "clean", [("in", "Rows")], [("out", "Rows")]),
    node("count.rows", "count", [("in", "Rows")], [("out", "Number")]),
]
steps = [
    step("read", "Read the file", [], [("out", "Rows")], "read", ["read.csv"]),
    step("clean", "Trim whitespace", [("in", "Rows")], [("out", "Rows")],
         "clean", ["clean.trim"]),
    step("count", "Count them", [("in", "Rows")], [("out", "Number")],
         "count", ["count.rows"]),
]
bench = graph("Count the rows", "Read a file, tidy it, count what is left.",
              steps, nodes, chain("read", "clean", "count"))

print("problems:", problems(bench) or "none")

# 2. Freeze one choice into a plan. This checks the types *before* anything runs.
plan = compile_route(bench, {"read": "read.csv", "clean": "clean.trim",
                             "count": "count.rows"})
print("plan:", plan.digest)

# 3. The code. Ordinary functions; the graph never sees inside them.
CSV = "  alpha , 1 \n beta , 2 \n\n gamma , 3 \n"

runtime = execute.Runtime({
    "read.csv": lambda: [line for line in CSV.splitlines()],
    "clean.trim": lambda **kw: [line.strip() for line in kw["in"] if line.strip()],
    "count.rows": lambda **kw: len(kw["in"]),
})

# 4. Run it.
run = execute.run(plan, runtime)
print(run.text())
print("\nrows counted:", run.output("count"))

# The check that earns its keep: a node that lies about what it produces is
# caught at the node that lied, not three steps later somewhere unrelated.
broken = execute.Runtime(dict(runtime._functions, **{"clean.trim": lambda **kw: None}))
failed = execute.run(plan, broken)
print("\nwith a node that returns nothing:")
print("  ok:", failed.ok, "| stopped at:", failed.stopped_at)
print(" ", next(s for s in failed.steps if not s.ok).error)
