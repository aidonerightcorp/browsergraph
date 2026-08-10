#!/usr/bin/env python3
"""Generate the teaching notebooks.

Generators rather than hand-edited `.ipynb` files, for the same reason as
`build_tour.py`: the code in them has to stay in step with the library, and
reviewing a diff of JSON with embedded output blobs is not a thing anyone
should have to do.

Three notebooks, because they answer three different questions and mixing them
produces something that answers none of them well:

    01-build-a-graph      how do I express a problem in this model?
    02-search-and-learn   how do I find a good route without enumerating?
    03-a-new-domain       how do I use this for something that is not browsing?

    python notebooks/build_notebooks.py
"""
from __future__ import annotations

import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent


class Notebook:
    def __init__(self, name: str, title: str) -> None:
        self.name = name
        self.title = title
        self.cells: list[tuple[str, str]] = []

    def md(self, text: str) -> Notebook:
        self.cells.append(("markdown", text.strip("\n")))
        return self

    def code(self, text: str) -> Notebook:
        self.cells.append(("code", text.strip("\n")))
        return self

    def write(self) -> pathlib.Path:
        payload = {
            "cells": [
                {"cell_type": kind, "metadata": {},
                 **({"outputs": [], "execution_count": None} if kind == "code" else {}),
                 "source": body.splitlines(keepends=True)}
                for kind, body in self.cells
            ],
            "metadata": {
                "kernelspec": {"display_name": "Python 3", "language": "python",
                               "name": "python3"},
                "language_info": {"name": "python", "version": "3.12"},
                "title": self.title,
            },
            "nbformat": 4, "nbformat_minor": 5,
        }
        path = OUT / f"{self.name}.ipynb"
        path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        return path


SETUP = """
# Nothing here needs a browser, a model or a network. The core is stdlib-only.
#
# Installed from the repository rather than from a pinned release wheel: these
# notebooks use `types`, `facets` and `viz`, and pinning v0.3.0 meant installing
# a build from before those existed — so the notebook failed at cell one while
# looking, from the source, entirely correct.
try:
    import browsergraph  # noqa: F401
except ImportError:
    import subprocess, sys
    subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                    "browsergraph @ git+https://github.com/"
                    "aidonerightcorp/browsergraph.git"], check=True)

import browsergraph as bg
print("browsergraph", bg.__version__)
"""


# ============================================================ notebook 01 ====

one = Notebook("01-build-a-graph", "Build a graph solution from scratch")
one.md("""
# 1 · Building a graph solution

**The idea in one sentence:** don't write the steps — write down what has to be
true, list everything that could make it true, and let the program pick.

By the end of this notebook you will have built a small solution from nothing,
watched the validator reject four different kinds of mistake, and seen the same
description compile into an immutable plan with a content hash.

Nothing here touches a browser. The model is not about browsers.
""")
one.code(SETUP)

one.md("""
## A node describes itself

A **node manifest** is a portable description: what it can do, what types go in
and out, what settings it has, what authority it needs. It is deliberately
separate from the code that runs it, because the interesting question — *what
could perform this step?* — has to be answerable about nodes that are not
installed, not written in Python, or not written yet.
""")
one.code("""
from browsergraph import NodeManifest, ParameterSpec, PortSpec

csv_loader = NodeManifest(
    id="demo.csv_loader",                # lowercase, namespaced, stable
    kind="csv_loader",
    description="Loads a CSV file and records its content hash.",
    roles=("source",),
    capabilities=("load",),              # what a stage discovers it by
    inputs=(PortSpec("path", "FilePath"),),
    outputs=(PortSpec("records", "CsvRecords", semantic="tabular"),),
    parameters=(ParameterSpec("delimiter", "string", default=",",
                              choices=(",", ";", "\\t")),),
    permissions=("filesystem:read",),
    runtime={"deterministic": True, "is_a": {"CsvRecords": ["Records"]}},
    metrics={"source": "illustrative-prior", "quality": 0.95,
             "latency_ms": 20, "cost_usd": 0.0},
).assert_valid()

print(csv_loader.id, "->", [f"{p.name}:{p.type}" for p in csv_loader.outputs])
print("expands into", csv_loader.variants(), "candidates (one per delimiter)")
""")

one.md("""
### `assert_valid()` reports *every* problem, not the first

Stopping at the first error turns fixing a manifest into a guessing loop.
""")
one.code("""
try:
    NodeManifest(id="badname", kind="", description="").assert_valid()
except ValueError as e:
    print(e)
""")

one.md("""
## Settings expand into candidates

This is the part people skip. A definition with three delimiters is **three
candidates**, not one — because you can pick either, and a picture that draws it
as one box is hiding two decisions you are entitled to make.
""")
one.code("""
from browsergraph import expand_node_candidates

json_loader = NodeManifest(
    id="demo.json_loader", kind="json_loader",
    description="Loads a JSON file.",
    roles=("source",), capabilities=("load",),
    inputs=(PortSpec("path", "FilePath"),),
    outputs=(PortSpec("records", "JsonRecords", semantic="tabular"),),
    runtime={"deterministic": True, "is_a": {"JsonRecords": ["Records"]}},
    metrics={"source": "illustrative-prior", "quality": 0.9, "latency_ms": 12},
).assert_valid()

nodes = (csv_loader, json_loader)
candidates = expand_node_candidates(nodes)
for c in candidates:
    print(f"  {c.name:<28} {c.params}")
""")

one.md("""
## A stage is a requirement, not an implementation

`Load the records` is a stage. `csv loader · delimiter=;` is a candidate that
can perform it. Getting these two confused is the single most common mistake,
and it is why the picture stops meaning anything.

`with_discovered_candidates` finds **every** compatible candidate. That is a
rule, not a courtesy: a stage that quietly omits one makes the diagram a summary
of somebody's old opinion, and nothing on screen would say so.
""")
one.code("""
from browsergraph import StageDefinition

load = StageDefinition(
    id="load", name="Load records",
    input_type="FilePath", output_type="Records",
    success="records exist and are counted",
    required_capabilities=("load",),
).with_discovered_candidates(nodes, candidates)

print(f"{len(load.candidates)} candidates admitted to {load.id!r}:")
for cid in load.candidates:
    print("   ", cid)
""")

one.md("""
Notice what just happened. Both loaders produce a *narrower* type than the stage
asks for — `CsvRecords` and `JsonRecords` where the stage wants `Records` — and
they were admitted anyway, because each declared `is_a`. Widening is safe.
Narrowing is not, and would need an explicit adapter.
""")
one.code("""
from browsergraph.types import Lattice, check
from browsergraph.manifest import PortSpec as P

lattice = Lattice.with_builtins().declare("CsvRecords", "Records")
print("CsvRecords where Records is wanted:", lattice.is_a("CsvRecords", "Records"))
print("Records where CsvRecords is wanted:", lattice.is_a("Records", "CsvRecords"))
print()
# Same carrier, different meaning — a connection that would look fine and be wrong
print(check(P("o", "text/plain", semantic="postal-address"),
            P("i", "text/plain", semantic="summary")))
print(check(P("o", "float", units="s"), P("i", "float", units="ms")))
""")

one.md("""
## Fan-out: this is a graph, not a pipeline

Here is the shape that matters — two independent branches that join:

```
            ┌─ count rows ──┐
load ───────┤               ├────── report
            └─ sum column ──┘
```

A sequence of stages cannot express this. Edges can.
""")
one.md("""
The two helpers below are local to this notebook on purpose. `01`-`03` are about
the primitives — `NodeManifest`, `StageDefinition`, and discovery via
`with_discovered_candidates` — so they build them by hand where the later
notebooks would not.

For a real project there is a short way that ships with the library:

```python
from browsergraph.quick import chain, fanin, fanout, graph, link, node, step
```

Same objects, less ceremony. It is used from notebook 04 onwards.
""")

one.code("""
from browsergraph import Edge, WorkbenchDefinition

def simple(node_id, ins, outs, capability, quality=0.9, latency=10):
    return NodeManifest(
        id=node_id, kind=node_id.split(".")[-1],
        description=f"The {node_id.split('.')[-1]} step.",
        roles=("transform",), capabilities=(capability,),
        inputs=tuple(PortSpec(n, t) for n, t in ins),
        outputs=tuple(PortSpec(n, t) for n, t in outs),
        metrics={"source": "illustrative-prior", "quality": quality,
                 "latency_ms": latency, "cost_usd": 0.0},
    ).assert_valid()

nodes = (csv_loader, json_loader,
         simple("demo.count_fast", [("r", "Records")], [("n", "Count")], "count", 0.99, 5),
         simple("demo.count_exact", [("r", "Records")], [("n", "Count")], "count", 1.0, 40),
         simple("demo.sum_naive", [("r", "Records")], [("s", "Total")], "total", 0.9, 8),
         simple("demo.sum_kahan", [("r", "Records")], [("s", "Total")], "total", 0.99, 25),
         simple("demo.report", [("n", "Count"), ("s", "Total")], [("o", "Report")], "report"))
candidates = expand_node_candidates(nodes)

def stage(sid, ins, outs, capability):
    return StageDefinition(
        id=sid, required_capabilities=(capability,),
        inputs=tuple(PortSpec(n, t) for n, t in ins),
        outputs=tuple(PortSpec(n, t) for n, t in outs),
        success=f"{sid} produced its declared output",
    ).with_discovered_candidates(nodes, candidates)

stages = (stage("load",   [("path", "FilePath")], [("records", "Records")], "load"),
          stage("count",  [("r", "Records")],     [("n", "Count")],  "count"),
          stage("total",  [("r", "Records")],     [("s", "Total")],  "total"),
          stage("report", [("n", "Count"), ("s", "Total")], [("o", "Report")], "report"))

edges = (Edge("load", "count", to_port="r"),
         Edge("load", "total", to_port="r"),
         Edge("count", "report", to_port="n"),
         Edge("total", "report", to_port="s"))

wb = WorkbenchDefinition(
    title="Summarise a table", task="Turn a file into a counted, totalled report",
    success="the report matches an independent recount",
    nodes=nodes, candidates=candidates, stages=stages, edges=edges)

print("valid:", wb.validate() == [])
print("layers:", wb.layers())
print("a chain?", wb.is_chain)
print(wb.summary())
""")

one.md("""
`layers()` is what puts the columns back. Left-to-right was never the real
invariant — the invariant is **one candidate per node, and every edge
type-checks**, which holds in any DAG. Layering is just how you draw it.

Here it is drawn, which is faster than reading the layer list and harder to
misread. `count` and `total` sit side by side because nothing connects them.
""")

one.code("""
from browsergraph import viz
viz.dag(wb)
""")

one.md("""
Two arrows arrive at `report`, each labelled with the port it lands on. That
labelling is not decoration: the count and the total are both numbers, and a
join that does not say which is which is a bug waiting for the day they get
swapped.

## Four mistakes, and what the validator says about each
""")
one.code("""
def problems_with(**changes):
    broken = WorkbenchDefinition(
        nodes=nodes, candidates=candidates,
        stages=changes.get("stages", stages),
        edges=changes.get("edges", edges))
    return broken.validate()

print("1. wrong port —")
print("  ", problems_with(edges=(
    Edge("load", "count", to_port="r"), Edge("load", "total", to_port="r"),
    Edge("count", "report", to_port="s"),        # Count into the Total port
    Edge("total", "report", to_port="n")))[0])

print("\\n2. an input nobody feeds —")
print("  ", problems_with(edges=(
    Edge("load", "count", to_port="r"), Edge("load", "total", to_port="r"),
    Edge("count", "report", to_port="n")))[0])

print("\\n3. a cycle —")
print("  ", [p for p in problems_with(edges=(
    Edge("load", "count", to_port="r"), Edge("count", "total", to_port="r"),
    Edge("total", "load", to_port="path"))) if "cycle" in p][0])

print("\\n4. a stage that drops a compatible candidate —")
thin = tuple(s if s.id != "count" else
             StageDefinition(**{**s.to_dict(), "candidates": s.candidates[:1],
                                "required_capabilities": s.required_capabilities,
                                "inputs": s.inputs, "outputs": s.outputs})
             for s in stages)
print("  ", [p for p in problems_with(stages=thin) if "omits" in p][0])
""")

one.md("""
## Compile it

A description is not a thing that ran. Compiling resolves every choice, orders
the graph, computes the union of authority, and hashes all of it. **That hash is
what evidence and receipts key on** — a plan whose hash is not the one in the
receipt is not the plan that ran, and now that is detectable rather than assumed.
""")
one.code("""
from browsergraph import compile_route
from browsergraph.compile import diff

fast = {"load": [c for c in stages[0].candidates if "csv" in c][0],
        "count": [c for c in stages[1].candidates if "fast" in c][0],
        "total": [c for c in stages[2].candidates if "naive" in c][0],
        "report": stages[3].candidates[0]}
careful = dict(fast,
               count=[c for c in stages[1].candidates if "exact" in c][0],
               total=[c for c in stages[2].candidates if "kahan" in c][0])

a, b = compile_route(wb, fast, source="fast"), compile_route(wb, careful, source="careful")
print(a.text())
print()
print("fast vs careful:")
for line in diff(a, b):
    print("   ", line)
""")

one.md("""
## The boundary check

A node can declare `Records` and hand back a string. The port is the natural
place to catch that, because the failure is then attributed to the node that
*produced* it rather than to the node three steps later that choked on it.
""")
one.code("""
from browsergraph.types import guard

ports = (PortSpec("records", "Records"), PortSpec("n", "int"))
print("good :", guard(ports, {"records": [1, 2, 3], "n": 3}, node="load") or "no problems")
print("bad  :", guard(ports, {"records": "oops", "n": 3}, node="load"))
print("bool :", guard((PortSpec("n", "int"),), {"n": True}, node="count"))
""")

one.md("""
## What you built

A four-node DAG with a fan-out and a join, every stage holding every compatible
candidate, edges checked by type *and* meaning *and* units, compiled to a hashed
plan, with a runtime guard at the boundary.

Nothing about it is browser-specific. Next: **02 · search and learn** — how to
find a good route without enumerating, and how to know when you have.
""")


# ============================================================ notebook 02 ====

two = Notebook("02-search-and-learn", "Search and learn without enumerating")
two.md("""
# 2 · Finding a good route without trying them all

The shipped demonstration has **3,802,314,700,800** routes. Enumerating them is
not a plan, and "use a heuristic" is not one either.

The useful reframing is information-theoretic, and it makes the problem look
completely different:

> A route is one choice per sub-step. With no knowledge, naming a good one costs
> **Σ log₂(candidates) = 41.8 bits**. Every measured run supplies some of those
> bits. **The number of experiments scales with the entropy of your posterior,
> not with the size of the space.**

41.8 bits sounds hopeless until you notice it decomposes into 14 independent
choices of 2 to 6 bits each.
""")
two.code(SETUP)

two.code("""
from browsergraph.demo import workbench
from browsergraph.evidence import Evidence, stages_of
from browsergraph.policy import Policy

wb = workbench()
stages = stages_of(wb, Policy.permissive())
store = Evidence()

print(wb.summary())
print()
print(f"{wb.route_count():,} routes  =  {store.bits_of_choice(stages):.1f} bits of choice")
for sid, cs in list(stages.items())[:5]:
    import math
    print(f"   {sid:<12} {len(cs):>3} candidates = {math.log2(len(cs)):.1f} bits")
""")

two.md("""
## Policy first, and it is a hard gate

Before anything is scored, candidates are filtered by what the task is
*permitted* to do. A candidate lacking a permission is **unavailable**, not
low-scoring — no amount of "but it scores well" may promote it. Get this
backwards and you build a system that confidently recommends something it is not
allowed to run.
""")
two.code("""
from browsergraph.policy import review

locked = Policy(
    permissions=frozenset({"filesystem", "filesystem:read", "filesystem:write",
                           "database", "database:read"}),
    allow_external_effects=False, deterministic_only=True, name="locked-down")

report = review(wb, locked)
print(f"{wb.route_count():,} routes before policy")
print(f"{report.reachable_routes:,} after "
      f"({100 * (1 - report.reachable_routes / wb.route_count()):.2f}% removed)")
print()
for gate in list(report.gates.values())[:3]:
    print(" ", gate.summary())
    for verdict in gate.blocked[:2]:
        print(f"      {verdict.reason}")
""")

two.md("""
Every removal states its reason, and blocked candidates stay **visible**.
Filtering them out silently would answer "what could perform this step?" with
"what the policy left", and the screen would look identical either way.

Those two numbers are the top of a funnel, and the funnel is worth drawing. The
bars are log-scaled, because on a linear axis a drop from trillions to one is
one bar and three invisible slivers.
""")

two.code("""
from browsergraph import viz

viz.funnel([("every route", wb.route_count()),
            ("policy allows", report.reachable_routes)],
           title="what the lockdown policy removed")
""")

two.md("""
## Three search strategies

**greedy** takes the best option per stage independently. **beam** keeps the most
promising partial routes. **exhaustive** enumerates — so "best" means best rather
than best-found.

Greedy is sometimes wrong, and the reason is worth understanding: route quality
**compounds**. A route is only as good as the joint probability that every step
worked, so a stage's real contribution depends on what the rest already spent.
""")
two.code("""
from browsergraph import search

rows = []
for profile in wb.optimization_profiles:
    got = search.compare_strategies(wb, profile, policy=locked)
    rows.append((profile.name, got["greedy"], got["beam"]))

print(f"{'profile':<16}{'greedy':>9}{'beam':>9}   {'evaluations':>12}")
for name, g, b in rows:
    print(f"{name:<16}{g.score:>9.4f}{b.score:>9.4f}   {g.examined:>5} vs {b.examined:<5}")
""")

two.md("""
## Why it chose that — with numbers that add up

A route is an assertion until you can interrogate it. Each sub-step records how
many candidates were eligible, how many policy blocked, what won, its runner-up,
and **what each objective actually contributed** — the realised weighted terms,
not the profile's declared weights. A weight of 0.7 on a metric every candidate
shares contributes nothing, and only the realised term shows that.
""")
two.code("""
proposal = search.propose(wb, wb.optimization_profiles[0],
                          policy=Policy.permissive(), strategy="greedy")
for d in proposal.decisions[:6]:
    terms = "  ".join(f"{m}={v:+.3f}" for m, v in sorted(d.contributions.items()))
    print(f"{d.stage:<12} score={d.score:.3f}  sum={sum(d.contributions.values()):.3f}  {terms}")
print()
print("the parts sum to the whole for every sub-step:",
      all(abs(sum(d.contributions.values()) - d.score) < 1e-9
          for d in proposal.decisions))
""")

two.md("""
## Learning: Thompson sampling at *sum* cost

Keep a posterior per candidate per context. Propose by sampling each sub-step —
**166 draws, not 3.8 trillion evaluations**. Exploration falls out of the
posterior's width; there is no ε to tune and it stops on its own.

The simulation below invents a hidden "true" success rate per candidate that the
learner cannot see, then watches it find them.
""")
two.code("""
import random
from browsergraph.evidence import Observation, context_chain

rng = random.Random(7)
truth = {c: rng.betavariate(2, 3) for cs in stages.values() for c in cs}
best = {sid: max(cs, key=lambda c: truth[c]) for sid, cs in stages.items()}
ctx = context_chain("site:acme.com", "sector:retail")

store = Evidence()
print(f"{'runs':>6}{'bits left':>11}{'resolved':>10}{'correct picks':>15}")
for target in (0, 50, 200, 600):
    while sum(p.runs for c in store.posteriors.values()
              for p in c.values()) < target * len(stages):
        route = store.suggest(stages, ctx, seed=rng.randrange(1 << 30))
        for c in route.values():
            store.observe(Observation(candidate=c, context=ctx[0],
                                      ok=rng.random() < truth[c]))
    hits = sum(1 for sid, cs in stages.items()
               if store.ranked(cs, ctx)[0][0] == best[sid])
    print(f"{target:>6}{store.bits_remaining(stages, ctx):>11.1f}"
          f"{store.resolved(stages, ctx):>10.0%}{hits:>10} of {len(stages)}")
""")

two.md("""
## The finding that justifies per-step receipts

Same simulation, one change: learn only from whether the **whole run** passed,
instead of from each step's own outcome.
""")
two.code("""
def learn(per_step, runs=600):
    store, local = Evidence(), random.Random(3)
    for _ in range(runs):
        route = store.suggest(stages, seed=local.randrange(1 << 30))
        q = 1.0
        for c in route.values():
            q *= truth[c]
        if per_step:
            for c in route.values():
                store.observe(Observation(candidate=c,
                                          ok=local.random() < truth[c]))
        else:
            store.observe_route(list(route.values()), quality=q,
                                ok=local.random() < q ** (1 / len(route)))
    hits = sum(1 for sid, cs in stages.items()
               if store.ranked(cs)[0][0] == best[sid])
    return store.resolved(stages), hits

for label, per_step in (("route-level pass/fail", False), ("per-step outcomes", True)):
    resolved, hits = learn(per_step)
    print(f"{label:<24} {resolved:>6.0%} resolved   {hits:>2} of {len(stages)} correct")
""")

two.md("""
Route-level success dilutes credit across fourteen candidates equally, so every
posterior converges to the *average* route quality rather than its own truth.
More runs do not help — the signal is not there.

That is a **measured** argument for something the design already had for other
reasons: per-step receipts and independent verification are not bookkeeping,
they are what makes learning tractable at all.

## Where independence breaks

Cheap search assumes the choices are independent. That assumption should be
measured, not believed: when routes containing a particular *pair* do much worse
than otherwise-similar routes containing only one of them, that pair deserves
joint search. Everything else can stay greedy.

The comparison has to be **both against exactly one**, and getting that wrong is
easy. An earlier version compared a route's outcome against `rate(a) * rate(b)`,
which is a category error rather than a tuning problem: a route's quality is the
product over *every* step in it, so on a three-step route the observed value
sits near 0.8³ while the expectation was 0.8², and every pair looks guilty. On
data built with no interaction at all it reported eleven.

So the demonstration below needs both kinds of route, and that is the point of
the middle loops: without routes containing exactly one of the pair, there is
nothing to compare against and nothing can be found.
""")
two.code("""
store = Evidence()
a, b = list(stages.values())[0][0], list(stages.values())[1][0]
other_a, other_b = list(stages.values())[0][1], list(stages.values())[1][1]
# A third step that *varies*. If it were the same in every route there would be
# no route without it, so "both" versus "exactly one" would collapse into a
# statement about the other candidate — and this reports two extra pairs that
# are really about `a` and `b` wearing a third name.
tails = [list(stages.values())[2][0], list(stages.values())[2][1]]

for i in range(12):                     # each is fine on its own
    store.routes.append(((a, other_b, tails[i % 2]), "global", 0.80))
    store.routes.append(((other_a, b, tails[i % 2]), "global", 0.78))
for i in range(8):                      # and this pair is fine too
    store.routes.append(((other_a, other_b, tails[i % 2]), "global", 0.79))
for i in range(8):                      # but these two together are not
    store.routes.append(((a, b, tails[i % 2]), "global", 0.15))

found = store.interactions()
for x, y, gap, runs in found:
    print(f"{x}\\n  + {y}\\n  {gap:+.2f} worse together than either alone, "
          f"over {runs} runs")
print(f"\\n{len(found)} interacting pair reported, out of "
      f"{len({p for r, _, _ in store.routes for p in r})} candidates seen — "
      f"the one that was planted, and nothing else")
""")

two.md("""
## The two regimes, and how they relate

- **Fast:** Thompson-sample a route, run it, fold the receipt back in. Suggested
  routes are the posterior's argmax; **fallbacks are its second and third place
  for this context**, not whatever was written down when the graph was drawn.
- **Exhaustive:** the same posteriors, swept rather than sampled. What that buys
  is not a better single answer — it is the interaction structure, the Pareto
  front, and the true second-best route.

They are the same machinery at different budgets. The cheap path is the
exhaustive path with a stopping rule, and `bits_remaining()` is the stopping
rule.
""")


# ============================================================ notebook 03 ====

three = Notebook("03-a-new-domain", "Apply the model to a domain that is not browsing")
three.md("""
# 3 · A domain that is not browsing

The claim is that this model is not about browsers. This notebook tests that
claim by building something with no browser anywhere in it: **a document
extraction pipeline** with a real fan-out, a real join, and two independent
extractors that get reconciled.

The point is not the domain. It is that the *only* thing that changes is the
data — manifests, stages, edges — while every mechanism (completeness, typing,
policy, search, evidence, receipts) is unchanged.
""")
three.code(SETUP)

three.md("""
## The task, decomposed

Ask four questions, in this order:

1. What must be true at the end, and **what would prove it independently**?
2. Working backwards, what must be true before that?
3. For each requirement: what type goes in, what comes out?
4. What could perform it? *If the answer is one thing, it is not a decision yet.*

```
                    ┌─ rules extractor ─┐
load → detect → OCR ┤                   ├→ reconcile → verify
                    └─ model extractor ─┘
```

Two extractors on purpose. Independent producers whose disagreements are
information — a reconciler that sees both can flag a conflict that either alone
would report as confident truth.
""")
three.code("""
from browsergraph import (Edge, NodeManifest, ParameterSpec, PortSpec,
                          StageDefinition, WorkbenchDefinition,
                          expand_node_candidates)

PRIOR = {"source": "illustrative-prior", "evidence": 0}

def node(node_id, description, capability, ins, outs, *, params=(),
         permissions=(), quality=0.9, latency=50, cost=0.0,
         deterministic=True, roles=("transform",), is_a=None):
    return NodeManifest(
        id=node_id, kind=node_id.split(".")[-1], description=description,
        roles=roles, capabilities=(capability,),
        inputs=tuple(PortSpec(n, t, semantic=s) for n, t, s in ins),
        outputs=tuple(PortSpec(n, t, semantic=s) for n, t, s in outs),
        parameters=params, permissions=permissions,
        runtime={"deterministic": deterministic, **({"is_a": is_a} if is_a else {})},
        metrics={**PRIOR, "quality": quality, "latency_ms": latency,
                 "cost_usd": cost},
    ).assert_valid()

def choice(name, *values):
    return ParameterSpec(name, "string", default=values[0], choices=values)

NODES = (
    node("doc.load.file", "Reads an authorized document from disk.", "load",
         [("ref", "DocRef", "")], [("doc", "Document", "")],
         params=(choice("mode", "single", "batch"),),
         permissions=("filesystem:read",), quality=0.99, latency=15),
    node("doc.load.url", "Fetches a document over HTTP with provenance.", "load",
         [("ref", "DocRef", "")], [("doc", "Document", "")],
         permissions=("network",), quality=0.93, latency=300),

    node("doc.detect.magic", "Identifies the format from its magic bytes.",
         "detect", [("doc", "Document", "")], [("typed", "TypedDocument", "")],
         quality=0.98, latency=4),
    node("doc.detect.extension", "Trusts the file extension.", "detect",
         [("doc", "Document", "")], [("typed", "TypedDocument", "")],
         quality=0.75, latency=1),

    node("doc.text.embedded", "Takes the text layer the PDF already carries.",
         "text", [("typed", "TypedDocument", "")], [("text", "PageText", "prose")],
         quality=0.97, latency=40),
    node("doc.text.ocr", "Reads the page from its pixels.", "text",
         [("typed", "TypedDocument", "")], [("text", "PageText", "prose")],
         params=(choice("engine", "tesseract", "rapidocr", "paddle"),),
         quality=0.86, latency=900, deterministic=False),

    node("doc.rules.regex", "Deterministic patterns, reviewed and versioned.",
         "rules", [("text", "PageText", "prose")], [("fields", "Fields", "extracted")],
         params=(choice("strictness", "strict", "lenient"),),
         quality=0.82, latency=20),
    node("doc.rules.grammar", "A declared grammar with error positions.", "rules",
         [("text", "PageText", "prose")], [("fields", "Fields", "extracted")],
         quality=0.88, latency=60),

    node("doc.model.schema", "A language model constrained to a schema.", "model",
         [("text", "PageText", "prose")], [("fields", "Fields", "extracted")],
         params=(choice("model", "GLM", "Qwen", "DeepSeek"),),
         permissions=("llm",), quality=0.91, latency=1800, cost=0.012,
         deterministic=False, roles=("model",)),
    node("doc.model.field_by_field", "One model call per field, slower and surer.",
         "model", [("text", "PageText", "prose")], [("fields", "Fields", "extracted")],
         params=(choice("model", "GLM", "Qwen"),),
         permissions=("llm",), quality=0.94, latency=4200, cost=0.03,
         deterministic=False, roles=("model",)),

    node("doc.reconcile.prefer_rules", "Rules win; the model fills the gaps.",
         "reconcile",
         [("a", "Fields", "extracted"), ("b", "Fields", "extracted")],
         [("merged", "Fields", "extracted")], quality=0.9, latency=10),
    node("doc.reconcile.agreement", "Keeps only fields both producers agree on.",
         "reconcile",
         [("a", "Fields", "extracted"), ("b", "Fields", "extracted")],
         [("merged", "Fields", "extracted")], quality=0.96, latency=15),

    node("doc.verify.schema", "Checks the merged fields against a schema.",
         "verify", [("fields", "Fields", "extracted")],
         [("out", "VerifiedFields", "extracted")],
         quality=0.9, latency=8, roles=("verifier",)),
    node("doc.verify.source", "Finds every value back in the source text.",
         "verify", [("fields", "Fields", "extracted")],
         [("out", "VerifiedFields", "extracted")],
         quality=0.97, latency=120, roles=("verifier",)),
)

CANDIDATES = expand_node_candidates(NODES)
print(len(NODES), "definitions ->", len(CANDIDATES), "atomic candidates")
""")

three.md("""
## Wiring it as a DAG

Note `reconcile`: **two input ports**, one per extractor. That is the shape a
sequence of stages cannot express, and it is not exotic — it is the ordinary way
you combine two independent opinions.
""")
three.code("""
def stage(sid, name, ins, outs, capability, success):
    return StageDefinition(
        id=sid, name=name, required_capabilities=(capability,), success=success,
        inputs=tuple(PortSpec(n, t, semantic=s) for n, t, s in ins),
        outputs=tuple(PortSpec(n, t, semantic=s) for n, t, s in outs),
    ).with_discovered_candidates(NODES, CANDIDATES)

STAGES = (
    stage("load", "Load document", [("ref", "DocRef", "")],
          [("doc", "Document", "")], "load", "the document is readable and hashed"),
    stage("detect", "Detect format", [("doc", "Document", "")],
          [("typed", "TypedDocument", "")], "detect", "the format is known"),
    stage("text", "Get text", [("typed", "TypedDocument", "")],
          [("text", "PageText", "prose")], "text", "text exists for every page"),
    stage("rules", "Deterministic extraction", [("text", "PageText", "prose")],
          [("fields", "Fields", "extracted")], "rules", "fields extracted by rule"),
    stage("model", "Model extraction", [("text", "PageText", "prose")],
          [("fields", "Fields", "extracted")], "model", "fields extracted by model"),
    stage("reconcile", "Reconcile",
          [("a", "Fields", "extracted"), ("b", "Fields", "extracted")],
          [("merged", "Fields", "extracted")], "reconcile",
          "disagreements are resolved or flagged"),
    stage("verify", "Verify", [("fields", "Fields", "extracted")],
          [("out", "VerifiedFields", "extracted")], "verify",
          "an independent check accepted the fields"),
)

EDGES = (Edge("load", "detect"), Edge("detect", "text"),
         Edge("text", "rules"), Edge("text", "model"),
         Edge("rules", "reconcile", to_port="a"),
         Edge("model", "reconcile", to_port="b"),
         Edge("reconcile", "verify"))

doc = WorkbenchDefinition(
    title="Document extraction",
    task="Extract a declared schema from an unknown document",
    success="every field is found back in the source by an independent check",
    nodes=NODES, candidates=CANDIDATES, stages=STAGES, edges=EDGES)

print("valid  :", doc.validate() == [])
print("layers :", doc.layers())
print("chain? :", doc.is_chain)
print(doc.summary())
""")

three.md("""
The two extractors sit in the **same layer** — they are independent, and the
layering says so without anyone drawing it. That is also the parallelism budget:
`parallel_width` tells you how much of this could run at once.

Compare the drawing below with the sketch further up. They agree, and that is
the point: the sketch was a wish, this is derived from the ports.
""")

three.code("""
from browsergraph import viz
viz.dag(doc)
""")

three.md("""
No browser code anywhere in that picture, and none in the code that drew it.
`viz` takes a workbench and knows nothing about any domain — which is the same
claim this notebook is making, tested a second way.

## Every mechanism works unchanged
""")
three.code("""
from browsergraph.policy import Policy, review

offline = Policy(permissions=frozenset({"filesystem", "filesystem:read"}),
                 deterministic_only=True, name="offline-deterministic")
report = review(doc, offline)
print(report.text())
""")

three.code("""
from browsergraph import search
from browsergraph.workbench import OptimizationObjective, OptimizationProfile

accuracy = OptimizationProfile(id="p.accuracy", name="Accuracy first", objectives=(
    OptimizationObjective("quality", "maximize", 0.8),
    OptimizationObjective("latency_ms", "minimize", 0.1),
    OptimizationObjective("cost_usd", "minimize", 0.1)))
cheap = OptimizationProfile(id="p.cheap", name="Cheap", objectives=(
    OptimizationObjective("cost_usd", "minimize", 0.6),
    OptimizationObjective("latency_ms", "minimize", 0.3),
    OptimizationObjective("quality", "maximize", 0.1)))

for profile in (accuracy, cheap):
    p = search.propose(doc, profile, policy=Policy.permissive(), strategy="exhaustive")
    print(f"--- {profile.name} ---")
    print(p.text(doc).split("route metrics")[0].rstrip())
    print()
""")

three.md("""
Two objectives, two genuinely different pipelines out of the same registry —
and each says which sub-step it examined, how many were blocked, and what it
cost. Nothing about the search knew this was documents.

## Compile it, and see what it would need
""")
three.code("""
from browsergraph import compile_route

best = search.propose(doc, accuracy, policy=Policy.permissive(),
                      strategy="exhaustive")
plan = compile_route(doc, best.route, source="accuracy-first")
print(plan.text())
print()
print("could run", plan.parallel_width, "steps at once")
""")

three.md("""
## Save it and look at it

The workbench is portable data. The studio is one self-contained offline file
that reads it — five synchronized views over the same description.
""")
three.code("""
import pathlib
out = pathlib.Path("/kaggle/working") if pathlib.Path("/kaggle/working").is_dir() \\
    else pathlib.Path(".")
doc.write_json(str(out / "document-extraction.json"))
doc.write_html(str(out / "document-extraction-studio.html"))
for p in sorted(out.glob("document-extraction*")):
    print(f"{p.name:<38} {p.stat().st_size/1000:>7.0f} KB")
""")

three.md("""
## What this proves, and what it does not

**Does:** the model is not about browsers. A document pipeline with fan-out and
a join uses exactly the same primitives, the same validator, the same policy
gate, the same search, and the same compiler — and the only thing written by
hand was the data.

**Does not:** none of these numbers are measurements. Every metric here carries
`"source": "illustrative-prior"`, which is enforced by a test. They exist to
give the search something to sort by. **Real optimization consumes real
receipts** — see notebook 02 for how those become evidence.

To apply this to your own problem: list the requirements in order, list what
could satisfy each, declare the types, wire the edges, and run
`browsergraph check`. If a stage has only one candidate, it is not a decision
yet — either find the alternatives or fold it into its neighbour.
""")


# Guarded, because writing on *import* means any module that borrows the
# `Notebook` helper silently regenerates these three and discards their executed
# outputs. That happened exactly once, which was enough.
if __name__ == "__main__":
    for notebook in (one, two, three):
        path = notebook.write()
        code_cells = sum(1 for kind, _ in notebook.cells if kind == "code")
        print(f"wrote {path}  ({len(notebook.cells)} cells, {code_cells} code)")
