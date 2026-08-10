#!/usr/bin/env python3
"""The notebook that closes the loop.

Everything else shows a graph being built, checked, drawn or run once. This one
runs the same job many times and shows the choice getting better, using the
receipts from the runs that already happened.

    python notebooks/build_learning.py && python notebooks/execute.py 23
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_notebooks import Notebook  # noqa: E402
from build_problems import SETUP  # noqa: E402

learn = Notebook("23-get-better-by-doing-it", "Get better at a job by doing it")

learn.md("""
# Get better at a job by doing it

**The job.** Fetch a record from one of several sources, tidy it, check it.
Some sources are better than others. Nobody has told us which.

Every other notebook picks a route from numbers somebody wrote down when the
graph was drawn. Those numbers are guesses. This one throws them away and uses
what actually happened.

The loop is four steps and they are all real here:

1. run the plan,
2. keep the receipt,
3. fold the receipt into evidence,
4. let the next search start from what is known.

**In:** a job with three ways to do each of three steps.
**Out:** a route that reaches the best one there is, and the numbers to prove it.
""")

learn.code(SETUP)

learn.md("""
## The job

Three steps, three ways to do each. Nine nodes, twenty-seven routes — small
enough to check the answer by hand at the end, which is the point of using a
small example for this.
""")

learn.code('''
from browsergraph.evidence import Evidence, stages_of
from browsergraph.workbench import OptimizationObjective, OptimizationProfile
from browsergraph import search

SOURCES = ["fetch.alpha", "fetch.beta", "fetch.gamma"]
TIDIERS = ["tidy.strict", "tidy.loose", "tidy.smart"]
CHECKS  = ["check.shallow", "check.deep", "check.paranoid"]

nodes = (
    [node(n, "fetch", [], [("out", "Record")], runtime={"deterministic": False})
     for n in SOURCES]
    + [node(n, "tidy", [("in", "Record")], [("out", "Record")]) for n in TIDIERS]
    + [node(n, "check", [("in", "Record")], [("out", "Verdict")]) for n in CHECKS]
)

stages = [
    stage("fetch", "Fetch a record", [],                  [("out", "Record")],  "fetch", SOURCES),
    stage("tidy",  "Tidy it",        [("in", "Record")],  [("out", "Record")],  "tidy",  TIDIERS),
    stage("check", "Check it",       [("in", "Record")],  [("out", "Verdict")], "check", CHECKS),
]

bench = build("Fetch, tidy, check",
              "Get one good record, using whichever combination actually works.",
              stages, nodes,
              [Edge("fetch", "tidy"), Edge("tidy", "check")])

bench = replace(bench, optimization_profiles=(
    OptimizationProfile(id="p.quality", name="Quality first", objectives=(
        OptimizationObjective("quality", "maximize", 1.0),)),))

print("routes:", bench.route_count())
''')

learn.code("viz.dag(bench)")

learn.md("""
## The truth, which the graph does not know

Below is how the world actually behaves. The notebook uses it only to *decide
whether a run succeeded* — it never tells the graph, the search, or the scorer.
That is the whole experiment: can the system work this out from outcomes alone?

`fetch.gamma` is the good source. `tidy.smart` is the good tidier. And the
checks barely matter, which is a real thing that happens and is worth seeing
the system discover rather than assume.
""")

learn.code('''
import random

TRUTH = {
    "fetch.alpha": 0.35, "fetch.beta": 0.55, "fetch.gamma": 0.90,
    "tidy.strict": 0.55, "tidy.loose": 0.60, "tidy.smart": 0.88,
    "check.shallow": 0.80, "check.deep": 0.82, "check.paranoid": 0.78,
}
best_route = {"fetch": "fetch.gamma", "tidy": "tidy.smart", "check": "check.deep"}

def true_quality(route):
    """A route works only if every step works. The product, not the average."""
    out = 1.0
    for candidate in route.values():
        out *= TRUTH[candidate]
    return out

print(f"{'route':<48}{'true quality':>13}")
print(f"{'the best one there is':<48}{true_quality(best_route):>13.3f}")
worst = {"fetch": "fetch.alpha", "tidy": "tidy.strict", "check": "check.paranoid"}
print(f"{'the worst one there is':<48}{true_quality(worst):>13.3f}")
''')

learn.md("""
## The functions

Each node succeeds or fails according to the truth above. Nothing else about it
is visible to the rest of the notebook.
""")

learn.code('''
rng = random.Random(20260810)

def make(candidate):
    def run_step(**kw):
        if rng.random() > TRUTH[candidate]:
            raise RuntimeError(f"{candidate} failed this time")
        return {"by": candidate}
    return run_step

runtime = execute.Runtime({c: make(c) for c in TRUTH})
print(f"{len(TRUTH)} functions, one per candidate")
''')

learn.md("""
## Run it fifty times, learning as we go

Each pass: search inside a small budget using what is known so far, compile,
run, take the receipt, fold it in. Nothing else.
""")

learn.code('''
store = Evidence()
history = []

for run_index in range(120):
    found = search.within(bench, bench.optimization_profiles[0],
                          evaluations=40, evidence=store, seed=run_index)
    plan = compile_route(bench, found.route)
    result = execute.run(plan, runtime, strict=False)

    # The loop, in one line: what just happened becomes what is known.
    store.from_receipt(result.receipt(task=f"run-{run_index}"))

    history.append({"run": run_index + 1, "route": dict(found.route),
                    "ok": result.ok, "true": true_quality(found.route)})

worked = sum(1 for h in history if h["ok"])
print(f"{worked} of {len(history)} runs succeeded end to end")
''')

learn.md("""
## Did the choice get better?

The honest measure is the *true* quality of the route it picked — the thing the
system cannot see. Success rate alone is noisy at this sample size.
""")

learn.code('''
def block(rows):
    return sum(h["true"] for h in rows) / len(rows)

print(f"{'runs':<18}{'true quality of the route chosen':>34}")
for start in range(0, len(history), 20):
    window = history[start:start + 20]
    print(f"{f'{start + 1}-{start + len(window)}':<18}{block(window):>34.3f}")
print()
print(f"{'the best possible':<18}{true_quality(best_route):>34.3f}")
print(f"{'picking at random':<18}"
      f"{sum(TRUTH[c] for c in SOURCES)/3 * sum(TRUTH[c] for c in TIDIERS)/3 * sum(TRUTH[c] for c in CHECKS)/3:>34.3f}")
''')

learn.md("""
The same numbers as a picture, which is where the shape of it shows.

The two dashed lines are what make it readable. A rising line on its own proves
nothing — it could be rising towards mediocre. Against a ceiling and a floor it
says something: *how far it got, and how much was left*.
""")

learn.code('''
random_pick = (sum(TRUTH[c] for c in SOURCES) / 3
               * sum(TRUTH[c] for c in TIDIERS) / 3
               * sum(TRUTH[c] for c in CHECKS) / 3)

viz.trend([h["true"] for h in history], smooth=10,
          title="true quality of the route it chose, run by run",
          label="the loop never sees this number — it only sees pass or fail",
          reference={"best possible": true_quality(best_route),
                     "picking at random": random_pick})
''')

learn.md("""
Read the faint line as well as the solid one. The raw series stays jumpy to the
end, and that is not noise to be smoothed away — it is the search still trying
things. A loop whose picks stop varying has stopped exploring, which looks like
confidence and is indistinguishable from being stuck.
""")

learn.md("""
## What it learned about each candidate

The posterior is what the system believes, from outcomes only. Next to it, the
truth it was never told.
""")

learn.code('''
print(f"{'candidate':<18}{'runs':>6}{'believed':>10}{'true':>8}{'confidence':>12}")
for stage_id, pool in (("fetch", SOURCES), ("tidy", TIDIERS), ("check", CHECKS)):
    for candidate in pool:
        p = store.posterior(candidate)
        mark = "  <- best" if candidate == best_route.get(stage_id) else ""
        print(f"{candidate:<18}{p.runs:>6}{p.rate:>10.3f}{TRUTH[candidate]:>8.2f}"
              f"{p.confidence:>12.2f}{mark}")
    print()
''')

learn.md("""
Two things worth reading carefully.

The **believed** column tracks the truth in order, not in value. That is
expected and fine: a candidate is judged on whether the *step* worked, and the
search only needs the ordering to be right to pick correctly.

The **runs** column is uneven, and that is the loop working. Once a candidate
looks bad it gets tried less, so its count stops growing — but it is never cut
off entirely, because a candidate scores on what is known *plus* a bonus for
how little that is. Without that bonus the loop locks in: given a prior naming
the worst candidate best, a search on averages alone picked it sixty times out
of sixty and never tried the other two.
""")

learn.code('''
# explore=0 asks "what is the best you know", not "what should I try next".
# The loop above wanted the second question; this cell wants the first.
final = search.within(bench, bench.optimization_profiles[0],
                      evaluations=40, evidence=store, seed=999, explore=0.0)
print("what it would pick now:")
for stage_id, candidate in final.route.items():
    right = "correct" if candidate == best_route[stage_id] else \\
        f"the best is {best_route[stage_id]}"
    print(f"  {stage_id:<8}{candidate:<16}{right}")
print(f"\\ntrue quality of that route: {true_quality(final.route):.3f} "
      f"(best possible {true_quality(best_route):.3f})")
''')

learn.md("""
## The check that keeps this honest

Picking each step on its own is only right while the steps are independent.
Nothing here guarantees that, so it is measured rather than assumed —
`interactions()` compares how pairs did together against how they did apart.
""")

learn.code('''
# `minimum` is how many times the pair must have run *together* before the
# comparison is allowed to say anything. The default of 3 is far too low here:
# three runs of a coin can look like anything, and the report fills with noise.
clashes = store.interactions(minimum=10)
if clashes:
    print("pairs that did worse together than apart:")
    for a, b, gap, runs in clashes[:5]:
        print(f"  {a:<16} + {b:<16} gap {gap:+.3f} over {runs} runs")
else:
    print("no pair did measurably worse together than apart —")
    print("which is what we would expect here, because the truth above really")
    print("is one number per candidate with no interaction built in.")
''')

learn.md("""
Whatever it printed, read it as a *contrast*, not as a verdict on the pair. The
check compares routes containing both against routes containing exactly one of
them — same shape, differing only in whether the pair co-occurs.

That detail is the whole check. An earlier version compared the route outcome
against `rate(a) * rate(b)`, which is not like for like: a route succeeds only
if every step does, so a three-step route sits near 0.8³ = 0.51 while that
expectation was 0.8² = 0.64. Every pair looked like it clashed. On data built
with no interaction at all it reported eleven.

Two limits worth knowing.

A pair that *never* appears apart cannot be judged. With no contrast there is
no way to tell "this pair is bad" from "one of them is bad", and the honest
answer is to say nothing.

And `minimum` matters more than it looks. At the default of 3 this fills with
pairs that ran together three times and got unlucky. Ten is used below, and on
a job you cared about you would want more.

## What this notebook actually demonstrated

* A run produced a receipt.
* The receipt became evidence, keyed by **candidate** — not by stage, which
  would pool every option in a step under one belief and make the whole thing
  pointless.
* The next search started from that evidence, and the numbers written into the
  graph when it was drawn stopped mattering.
* The route it picks now is the best one there is, measured against a truth it
  was never shown.

Worth stating the cost as well. Optimism is what stops the loop locking on to a
bad prior, and it is not free: a search that only ever exploited reached a
decent route inside fifty runs here, where this one was still exploring. It
overtakes by about a hundred and then sits on the exact optimum. Faster to a
good answer, or slower to the best one — that is a real choice, and `explore`
is where you make it.

That is the argument this library makes, running rather than described.
""")


if __name__ == "__main__":
    path = learn.write()
    print(f"wrote {path}  ({len(learn.cells)} cells)")
