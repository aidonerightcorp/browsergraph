#!/usr/bin/env python3
"""Two things at once, meeting at one place.

A list of steps cannot say "these two do not depend on each other". A graph can,
and everything else here follows from taking that seriously: the two branches
run independently, fail independently, and meet at a join whose inputs are named.

    python examples/02_parallel_and_join.py
"""
import re

from browsergraph import execute, viz
from browsergraph.compile import compile_route
from browsergraph.quick import fanin, fanout, graph, node, problems, step

PAGE = """<product><name>Tent 2P</name><price>$189.00</price></product>"""

nodes = [
    node("parse.xmlish", "parse", [("in", "Text")], [("out", "Blocks")]),
    node("price.regex", "price", [("in", "Blocks")], [("out", "Money")]),
    node("title.regex", "title", [("in", "Blocks")], [("out", "Words")]),
    node("join.record", "join", [("price", "Money"), ("title", "Words")],
         [("out", "Record")]),
]
steps = [
    step("parse", "Parse the page", [("in", "Text")], [("out", "Blocks")],
         "parse", ["parse.xmlish"]),
    step("price", "Find the price", [("in", "Blocks")], [("out", "Money")],
         "price", ["price.regex"]),
    step("title", "Find the title", [("in", "Blocks")], [("out", "Words")],
         "title", ["title.regex"]),
    step("join", "Make one record",
         [("price", "Money"), ("title", "Words")], [("out", "Record")],
         "join", ["join.record"]),
]
# fanout: one step feeding two independent ones.
# fanin: they meet, and each edge names the port it lands on — an unlabelled
# join is the bug this whole library started with.
links = (*fanout("parse", ["price", "title"]),
         *fanin({"price": "price", "title": "title"}, "join"))

bench = graph("Extract a product", "Pull the price and the title, then join.",
              steps, nodes, links)
print("problems:", problems(bench) or "none")
print("layers:  ", bench.layers())
print("a chain? ", bench.is_chain)

runtime = execute.Runtime({
    "parse.xmlish": lambda **kw: dict(re.findall(r"<(\w+)>([^<]*)</\1>", kw["in"])),
    "price.regex": lambda **kw: float(re.sub(r"[^0-9.]", "", kw["in"]["price"])),
    "title.regex": lambda **kw: kw["in"]["name"],
    "join.record": lambda price, title: {"title": title, "price": price},
})

plan = compile_route(bench, {s.id: s.candidates[0] for s in bench.leaf_stages})
run = execute.run(plan, runtime, {"parse": PAGE})
print()
print(run.text())
print("\nrecord:", run.output("join"))

# `price` and `title` share a layer, so they can run at the same time. The plan
# already knew that; `workers` is what uses it.
print("\nwidest layer:", plan.parallel_width, "steps may run together")
print(execute.run(plan, runtime, {"parse": PAGE}, workers=2).ok)

# The picture is derived from the ports, not drawn by hand.
viz.dag(bench).save("examples/out/02-shape.html")
print("\nwrote examples/out/02-shape.html")
