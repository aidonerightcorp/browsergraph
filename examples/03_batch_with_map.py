#!/usr/bin/env python3
"""Do it to all of them.

Every other example runs each step once. A `map` step runs its node once per
item and hands back a list — which is how "process this folder" gets said at
all. The node inside handles *one* file and never sees the list.

    python examples/03_batch_with_map.py
"""
import pathlib
import shutil
import tempfile

from browsergraph import execute
from browsergraph.compile import compile_route
from browsergraph.quick import chain, graph, node, problems, step

# A folder of readings, two of them corrupt — because a real folder always has
# a couple.
folder = pathlib.Path(tempfile.mkdtemp()) / "readings"
folder.mkdir(parents=True)
for i in range(12):
    rows = [str(10 * i + n) for n in range(4)]
    if i in (3, 9):
        rows[1] = "n/a"
    (folder / f"day-{i:02d}.txt").write_text("\n".join(rows))

# The stage talks about the *collection*; the node inside handles one item.
# `List[Path]` and `Path` is how that difference is written down.
nodes = [
    node("list.dir", "list", gives=[("out", "List[Path]")]),
    node("read.one", "read", [("in", "Path")], [("out", "Reading")]),
    node("total.all", "total", [("in", "List[Reading]")], [("out", "Summary")]),
]
steps = [
    step("list", "List the files", [], [("out", "List[Path]")], "list", ["list.dir"]),
    step("read", "Read each one", [("in", "List[Path]")],
         [("out", "List[Reading]")], "read", ["read.one"], kind="map"),
    step("total", "Add them up", [("in", "List[Reading]")], [("out", "Summary")],
         "total", ["total.all"]),
]
bench = graph("Process a folder", "Read every file and total the numbers.",
              steps, nodes, chain("list", "read", "total"))
print("problems:", problems(bench) or "none")
print("kinds:   ", {s.id: s.kind for s in bench.leaf_stages})


def read_one(**kw):
    """One file in, one reading out. No loop anywhere in here."""
    path = kw["in"]
    numbers, bad = [], []
    for line in path.read_text().splitlines():
        (numbers if line.strip().isdigit() else bad).append(line)
    return {"file": path.name, "values": [int(n) for n in numbers], "bad": bad}


runtime = execute.Runtime({
    "list.dir": lambda: sorted(folder.glob("*.txt")),
    "read.one": read_one,
    "total.all": lambda **kw: {
        "files": len(kw["in"]),
        "total": sum(v for r in kw["in"] for v in r["values"]),
        "unreadable": [r["file"] for r in kw["in"] if r["bad"]]},
})

plan = compile_route(bench, {s.id: s.candidates[0] for s in bench.leaf_stages})
run = execute.run(plan, runtime)
print()
print(run.text())
print("\nsummary:", run.output("total"))

# When a node raises, a map step names the item rather than the batch.
def strict(**kw):
    path = kw["in"]
    return {"file": path.name,
            "values": [int(x) for x in path.read_text().splitlines()], "bad": []}

harsh = execute.run(plan, execute.Runtime(
    dict(runtime._functions, **{"read.one": strict})))
print("\nwith a node that will not tolerate a bad line:")
print(" ", next(s for s in harsh.steps if not s.ok).error)
print("  -> item 3 is day-03.txt. 'the batch failed' would leave you to find that.")

shutil.rmtree(folder.parent, ignore_errors=True)
