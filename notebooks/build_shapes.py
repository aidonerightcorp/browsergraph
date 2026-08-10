#!/usr/bin/env python3
"""The shapes a job comes in.

Notebook 24 is about the renderers — what each picture is for. This one is about
the patterns: nine shapes a real job actually takes, each drawn, each with the
sentence that tells you when you are looking at one.

It is a reference. Somebody with a job in front of them should be able to scroll
this, recognise their shape, and copy the twelve lines under it.

    python notebooks/build_shapes.py && python notebooks/execute.py 25
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_notebooks import Notebook  # noqa: E402
from build_problems import SETUP  # noqa: E402

shapes = Notebook("25-the-shapes-a-job-comes-in", "The shapes a job comes in")

shapes.md("""
# The shapes a job comes in

Almost every job is one of a small number of shapes. Knowing which one you have
tells you what can go wrong, what can run at once, and what the graph is going
to cost you.

Nine shapes. Each one drawn, each with the twelve lines that build it.

| | shape | you have this when |
|---|---|---|
| 1 | **chain** | each step needs the one before it |
| 2 | **fan-out** | two things need the same input and not each other |
| 3 | **diamond** | they fan out and then meet again |
| 4 | **map** | the same work, once per item |
| 5 | **branch** | the data decides which way to go |
| 6 | **gate** | it should be allowed to say no |
| 7 | **fallback** | there is more than one way to get the same thing |
| 8 | **reuse** | the same little shape appears three times |
| 9 | **tournament** | you want several answers and then to pick one |

Nothing here downloads anything. Every cell runs in under a second.
""")

shapes.code(SETUP)

shapes.md("""
One helper, so each shape below is only the interesting part. It builds the
graph, says whether anything is wrong with it, and draws it.
""")

shapes.code('''
from browsergraph.quick import (chain, fanin, fanout, graph, link, node,
                                passthrough, step, subgraph)

def show(title, task, steps, nodes, links, note=""):
    """Build it, check it, count it, draw it."""
    bench = graph(title, task, steps, nodes, links)
    problems = bench.validate()
    print(f"{title}")
    print(f"  problems: {problems or 'none'}")
    print(f"  layers:   {bench.layers()}")
    print(f"  routes:   {bench.route_count():,}"
          + (f"   computations: {bench.computation_count():,}"
             if bench.computation_count() != bench.route_count() else ""))
    if note:
        print(f"  {note}")
    return bench

print("helper ready — nine shapes follow")
''')

# ---------------------------------------------------------------- 1. chain ---

shapes.md("""
## 1 · Chain

Each step needs the one before it. Nothing runs at the same time as anything
else, and one failure stops everything after it.

This is the shape most people draw first, and quite often it is wrong — not
because chains are bad, but because two of the steps did not actually need each
other and drawing them in a line threw that away.
""")

shapes.code('''
nodes = [node("fetch.http", "fetch", gives=[("out", "Page")]),
         node("fetch.file", "fetch", gives=[("out", "Page")]),
         node("parse.html", "parse", [("in", "Page")], [("out", "Rows")]),
         node("save.csv",   "save",  [("in", "Rows")], [("out", "File")])]
steps = [step("fetch", "Get the page", [], [("out", "Page")], "fetch",
              ["fetch.http", "fetch.file"]),
         step("parse", "Pull the rows out", [("in", "Page")], [("out", "Rows")],
              "parse", ["parse.html"]),
         step("save",  "Write them down", [("in", "Rows")], [("out", "File")],
              "save", ["save.csv"])]

bench = show("Chain", "Fetch a page, parse it, save it.",
             steps, nodes, chain("fetch", "parse", "save"))
viz.dag(bench)
''')

shapes.md("""
Three layers, one step in each. The layering is not a drawing choice — it is
worked out from which port feeds which, so a chain draws as a chain because it
*is* one.

## 2 · Fan-out

Two things need the same input and do not need each other. They can run at the
same time, and one failing does not stop the other.

The only thing that makes this shape possible is that neither one consumes the
other's output. Say that in the ports and it falls out for free.
""")

shapes.code('''
nodes = [node("load.csv", "load", gives=[("out", "Table")]),
         node("stats.describe", "stats", [("in", "Table")], [("out", "Report")]),
         node("chart.hist",     "chart", [("in", "Table")], [("out", "Image")]),
         node("checks.nulls",   "checks",[("in", "Table")], [("out", "Report")])]
steps = [step("load",   "Load the table", [], [("out", "Table")], "load",
              ["load.csv"]),
         step("stats",  "Summarise it", [("in", "Table")], [("out", "Report")],
              "stats", ["stats.describe"]),
         step("chart",  "Draw it", [("in", "Table")], [("out", "Image")],
              "chart", ["chart.hist"]),
         step("checks", "Look for holes", [("in", "Table")], [("out", "Report")],
              "checks", ["checks.nulls"])]

bench = show("Fan-out", "Load once, then three independent readings.",
             steps, nodes, fanout("load", ["stats", "chart", "checks"]))
viz.dag(bench)
''')

shapes.md("""
Three boxes in one layer. That is the graph telling you those three can run at
once — and `workers=3` is all it takes to make them.

## 3 · Diamond

They fan out and then meet again. The join is the part worth caring about: two
different things arrive at one step, and it has to be clear which is which.

That is why a join names the port each arrival lands on. An unlabelled join is
the single most common way a graph like this goes quietly wrong.
""")

shapes.code('''
nodes = [node("load.csv", "load", gives=[("out", "Table")]),
         node("num.scale",  "num",  [("in", "Table")], [("out", "Numbers")]),
         node("cat.onehot", "cat",  [("in", "Table")], [("out", "Codes")]),
         node("join.concat","join", [("numbers", "Numbers"), ("codes", "Codes")],
              [("out", "Matrix")])]
steps = [step("load", "Load the table", [], [("out", "Table")], "load",
              ["load.csv"]),
         step("num",  "Scale the numbers", [("in", "Table")],
              [("out", "Numbers")], "num", ["num.scale"]),
         step("cat",  "Encode the words", [("in", "Table")], [("out", "Codes")],
              "cat", ["cat.onehot"]),
         step("join", "Put them together",
              [("numbers", "Numbers"), ("codes", "Codes")], [("out", "Matrix")],
              "join", ["join.concat"])]

links = [*fanout("load", ["num", "cat"]),
         # `fanin` takes a mapping, so you cannot write this wiring without
         # saying which port each side lands on.
         *fanin({"num": "numbers", "cat": "codes"}, "join")]

bench = show("Diamond", "Two treatments of one table, joined back up.",
             steps, nodes, links)
viz.dag(bench)
''')

shapes.md("""
Look at the two arrows into `join`. Each one carries the name of the port it
arrives at. Swap them by mistake and the types disagree, and you are told before
anything runs rather than after the model trains on nonsense.

## 4 · Map

The same work, once per item. One step, many items, one collection back.

The step declares `List[Row]` and the node declares `Row`. That difference is
what makes it a map: the node handles one, the library handles all of them, and
nobody writes the loop.
""")

shapes.code('''
nodes = [node("find.folder", "find", gives=[("out", "List[Path]")]),
         # One item in, one item out. The node has no idea there are others.
         node("read.one",  "read",  [("in", "Path")], [("out", "Row")]),
         node("total.sum", "total", [("in", "List[Row]")], [("out", "Report")])]
steps = [step("find",  "List the files", [], [("out", "List[Path]")], "find",
              ["find.folder"]),
         step("read",  "Read each one", [("in", "List[Path]")],
              [("out", "List[Row]")], "read", ["read.one"], kind="map"),
         step("total", "Add them up", [("in", "List[Row]")], [("out", "Report")],
              "total", ["total.sum"])]

bench = show("Map", "Read every file in a folder, then total them.",
             steps, nodes, chain("find", "read", "total"),
             note="the read step says List[Path]; its node says Path")
viz.dag(bench)
''')

shapes.md("""
The map step is drawn with a doubled edge and says MAP. One item failing is
reported as that item failing, with its index — not as "the batch failed", which
tells you nothing about which of four thousand files was the problem.

## 5 · Branch

The data decides which way to go. Only one way is taken; the other is skipped.

Skipped is a third outcome, and it needs to stay one. A path that never ran did
not fail, and did not succeed either.
""")

shapes.code('''
nodes = [node("size.check", "size", [("in", "Table")],
              [("small", "Table"), ("large", "Table")]),
         node("quick.pandas", "quick", [("in", "Table")], [("out", "Result")]),
         node("heavy.chunked","heavy", [("in", "Table")], [("out", "Result")]),
         node("write.parquet","write", [("in", "Result")], [("out", "File")])]
steps = [step("size",  "How big is it?", [("in", "Table")],
              [("small", "Table"), ("large", "Table")], "size", ["size.check"],
              kind="branch"),
         step("quick", "Small: do it in memory", [("in", "Table")],
              [("out", "Result")], "quick", ["quick.pandas"]),
         step("heavy", "Large: do it in chunks", [("in", "Table")],
              [("out", "Result")], "heavy", ["heavy.chunked"]),
         step("write", "Write the answer", [("in", "Result")], [("out", "File")],
              "write", ["write.parquet"])]

links = [link("size", "quick", from_port="small"),
         link("size", "heavy", from_port="large"),
         # Both paths rejoin here, so `write` belongs to neither side and runs
         # whichever way the branch went.
         link("quick", "write"), link("heavy", "write")]

bench = show("Branch", "Choose a strategy from the size of the data.",
             steps, nodes, links)
viz.dag(bench)
''')

shapes.md("""
Two counts appeared under this one, and they are different questions.

**Routes — 1.** How many plans exist. There is one candidate per step, so there
is exactly one plan. A plan names a candidate for `heavy` even on a run that
goes small, because the choice is made before the data is seen.

**Computations — 2.** How many different things that one plan can be seen doing.
It can go small or it can go large, and those are not the same thing happening.

One plan, two behaviours. Add more candidates behind each side and it flips the
other way — plans that differ only behind the side that was not taken do the
same thing, so behaviours grow slower than plans.

## 6 · Gate

A step that is allowed to say no.

A quality check that cannot stop the run is a log line. If bad data flows on
regardless, the check did not check anything — it described.
""")

shapes.code('''
nodes = [node("load.csv", "load", gives=[("out", "Table")]),
         node("gate.rules", "gate", [("in", "Table")],
              [("pass", "Table"), ("fail", "Report")]),
         node("use.model",  "use",   [("in", "Table")],  [("out", "Result")]),
         node("stop.report","stop",  [("in", "Report")], [("out", "Report")])]
steps = [step("load", "Load it", [], [("out", "Table")], "load", ["load.csv"]),
         step("gate", "Is it good enough?", [("in", "Table")],
              [("pass", "Table"), ("fail", "Report")], "gate", ["gate.rules"],
              kind="branch"),
         step("use",  "Use it", [("in", "Table")], [("out", "Result")], "use",
              ["use.model"]),
         step("stop", "Say why not", [("in", "Report")], [("out", "Report")],
              "stop", ["stop.report"])]

links = [link("load", "gate"),
         link("gate", "use",  from_port="pass"),
         link("gate", "stop", from_port="fail")]

bench = show("Gate", "Refuse to model data that failed its checks.",
             steps, nodes, links)
viz.dag(bench)
''')

shapes.md("""
A gate is a branch whose two sides are "carry on" and "stop and explain". Same
machinery, and worth its own name because it is the one people forget to build.

## 7 · Fallback

There is more than one way to get the same thing.

This one is not a shape in the graph at all — it is a shape in the *route*.
Several candidates on one step, each satisfying the same contract, and the
runner tries the next when the first fails.
""")

shapes.code('''
nodes = [node("get.api",   "get", gives=[("out", "Data")], deterministic=False),
         node("get.scrape","get", gives=[("out", "Data")], deterministic=False),
         node("get.cache",  "get", gives=[("out", "Data")]),
         node("use.it", "use", [("in", "Data")], [("out", "Report")])]
steps = [step("get", "Get the data", [], [("out", "Data")], "get",
              ["get.api", "get.scrape", "get.cache"]),
         step("use", "Use it", [("in", "Data")], [("out", "Report")], "use",
              ["use.it"])]

bench = show("Fallback", "Three ways to get the same data.",
             steps, nodes, chain("get", "use"),
             note="one step, three candidates — the choice is a route, not a shape")
viz.route_space(bench, route={"get": "get.api", "use": "use.it"},
                alternative={"get": "get.cache", "use": "use.it"})
''')

shapes.md("""
Drawn as a route space rather than a shape, because that is where the difference
lives. The graph is two boxes either way.

At run time you name the order:

```python
execute.run(plan, runtime, fallbacks={"get": ["get.scrape", "get.cache"]})
```

The step that fell back says so on its receipt, so "it worked" and "it worked on
the third try" stay distinguishable.

## 8 · Reuse

The same little shape appears three times.

Copy it and you have three things to keep in agreement. `subgraph` gives the
fragment a prefix and splices it in, so the ids cannot collide and there is one
definition.
""")

shapes.code('''
# Written once.
QUALITY = [step("check",  "Check it", [("in", "Table")], [("out", "Report")],
                "check", ["check.rules", "check.stats"]),
           step("decide", "Good enough?", [("in", "Report")], [("out", "Table")],
                "decide", ["decide.threshold"])]
QUALITY_LINKS = chain("check", "decide")

inbound,  wiring_in  = subgraph("inbound",  QUALITY, QUALITY_LINKS)
outbound, wiring_out = subgraph("outbound", QUALITY, QUALITY_LINKS)

nodes = [node("load.csv", "load", gives=[("out", "Table")]),
         node("check.rules", "check", [("in", "Table")], [("out", "Report")]),
         node("check.stats", "check", [("in", "Table")], [("out", "Report")]),
         node("decide.threshold", "decide", [("in", "Report")], [("out", "Table")]),
         node("ship.write", "ship", [("in", "Table")], [("out", "File")])]

steps = [step("load", "Load it", [], [("out", "Table")], "load", ["load.csv"]),
         *inbound, *outbound,
         step("ship", "Ship it", [("in", "Table")], [("out", "File")], "ship",
              ["ship.write"])]

links = [link("load", "inbound.check"), *wiring_in,
         link("inbound.decide", "outbound.check"), *wiring_out,
         link("outbound.decide", "ship")]

bench = show("Reuse", "The same quality check, on the way in and on the way out.",
             steps, nodes, links,
             note="one definition, two copies, no id collisions")
viz.dag(bench)
''')

shapes.md("""
`inbound.check` and `outbound.check` are separate steps with separate evidence —
the same check can be reliable on the way in and flaky on the way out, and you
would want to know that. What they share is the definition, not the history.

## 9 · Tournament

Get several answers, then pick one.

Different from a fallback. A fallback stops at the first thing that works; a
tournament runs them all on purpose, because you want to compare.
""")

shapes.code('''
nodes = [node("load.csv", "load", gives=[("out", "Table")]),
         node("model.linear", "linear", [("in", "Table")], [("out", "Score")]),
         node("model.tree",   "tree",   [("in", "Table")], [("out", "Score")]),
         node("model.knn",    "knn",    [("in", "Table")], [("out", "Score")]),
         node("pick.best", "pick",
              [("a", "Score"), ("b", "Score"), ("c", "Score")],
              [("out", "Score")])]
steps = [step("load", "Load it", [], [("out", "Table")], "load", ["load.csv"]),
         step("linear", "Try a line", [("in", "Table")], [("out", "Score")],
              "linear", ["model.linear"]),
         step("tree",   "Try a tree", [("in", "Table")], [("out", "Score")],
              "tree", ["model.tree"]),
         step("knn",    "Try neighbours", [("in", "Table")], [("out", "Score")],
              "knn", ["model.knn"]),
         step("pick", "Keep the best",
              [("a", "Score"), ("b", "Score"), ("c", "Score")],
              [("out", "Score")], "pick", ["pick.best"])]

links = [*fanout("load", ["linear", "tree", "knn"]),
         *fanin({"linear": "a", "tree": "b", "knn": "c"}, "pick")]

bench = show("Tournament", "Three models, then keep the best.",
             steps, nodes, links)
viz.dag(bench)
''')

shapes.md("""
Three in one layer, so they run together, and one step that sees all three.

Worth being honest about the cost: this runs every model every time. A search
over three candidates on one step would run one and learn which. Use a
tournament when you genuinely want all the answers — a report comparing them, or
an ensemble — and a search when you only want the winner.

---

## Picking your shape

Answer these in order and you will land on one:

1. **Does every step need the one before it?** → chain.
2. **Do two steps need the same input and not each other?** → fan-out. If they
   come back together, → diamond, and name the ports at the join.
3. **Is it the same work once per item?** → map. Write the node for one item.
4. **Does the data decide?** → branch. If one of the ways out is "stop", → gate.
5. **Is there more than one way to do a step?** → several candidates. Fallbacks
   at run time, a search when you want to find out which is best.
6. **Does the same fragment appear twice?** → subgraph with a prefix.
7. **Do you want all the answers, not just one?** → tournament.

Two things are worth writing on the wall:

**A loop is not on this list.** A loop with no argument for why it stops is how
a job runs forever. If you have one, either it is a map — the same work, once
per item, known in advance — or it needs a step that decides to stop, and that
step is a branch.

**A shape you drew as a chain that is really a diamond is the expensive
mistake.** Not because it fails, but because it quietly gives up all the
parallelism and every alternative route you could have had. That is what these
pictures are for: on paper the two look the same, and drawn they do not.
""")


if __name__ == "__main__":
    path = shapes.write()
    print(f"wrote {path}  ({len(shapes.cells)} cells)")
