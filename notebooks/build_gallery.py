#!/usr/bin/env python3
"""The picture book.

Every other notebook explains something and draws a diagram when it helps. This
one is the other way round: it is the diagrams, with just enough text to say
what each one is for and why the flat version of it would mislead you.

It is also the honest tour of what the library can draw, which is worth having
in one place — the pictures are scattered across twenty-four notebooks and
nobody has seen them side by side.

    python notebooks/build_gallery.py && python notebooks/execute.py 24
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_notebooks import Notebook  # noqa: E402
from build_problems import SETUP  # noqa: E402

gallery = Notebook("24-the-picture-book", "Every picture, and why it exists")

gallery.md("""
# Every picture, and why it exists

A graph you cannot see is a graph you have to take on trust. This notebook draws
each kind of picture the library makes, on a real job, and says what each one
would hide if you drew it the flat way instead.

Seven pictures, seven different questions:

| picture | the question |
|---|---|
| **shape** | what runs when, and what runs together? |
| **route space** | what am I choosing between? |
| **funnel** | how much of that did I actually look at? |
| **evidence** | which step is the problem? |
| **timeline** | what actually happened, and did it really run in parallel? |
| **scoreboard** | what did the winning route beat? |
| **figure** | the same thing, for a document that is not a web page |

Nothing here needs a browser, a dataset or a model. It all runs in a few
seconds on a bare machine.
""")

gallery.code(SETUP)

gallery.md("""
## The job

A support-ticket pipeline. It reads tickets, then does two independent things —
works out the topic, and works out the urgency — and those meet at a routing
decision. Then it either escalates or files, which is a branch: only one of
those ever runs.

Three ways to do most steps, so there is something to choose between.
""")

gallery.code('''
from browsergraph.quick import fanin, fanout, graph, link, node, passthrough, step
from browsergraph.workbench import OptimizationObjective, OptimizationProfile

nodes = [
    node("read.jsonl",  "read",   gives=[("out", "Tickets")]),
    node("read.csv",    "read",   gives=[("out", "Tickets")]),

    node("topic.keywords", "topic", [("in", "Tickets")], [("out", "Topics")]),
    node("topic.bagofwords","topic", [("in", "Tickets")], [("out", "Topics")]),
    node("topic.embed",    "topic", [("in", "Tickets")], [("out", "Topics")],
         deterministic=False),

    node("urgency.rules",  "urgency", [("in", "Tickets")], [("out", "Urgency")]),
    node("urgency.model",  "urgency", [("in", "Tickets")], [("out", "Urgency")],
         deterministic=False),

    # Doing nothing is a candidate. "Enrich the ticket" is a real obligation
    # even when there is nothing to add, and deleting the step would make two
    # routes incomparable rather than comparable.
    passthrough("enrich.none", "enrich", "Topics"),
    node("enrich.history",  "enrich", [("in", "Topics")], [("out", "Topics")]),

    node("route.decide", "route", [("topic", "Topics"), ("urgency", "Urgency")],
         [("escalate", "Ticket"), ("file", "Ticket")]),
    node("escalate.page", "escalate", [("in", "Ticket")], [("out", "Receipt")],
         effects=("notify.person",)),
    node("file.queue",    "file",     [("in", "Ticket")], [("out", "Receipt")]),
]

steps = [
    step("read",    "Read the tickets", [], [("out", "Tickets")], "read",
         ["read.jsonl", "read.csv"]),
    step("topic",   "Work out the topic", [("in", "Tickets")], [("out", "Topics")],
         "topic", ["topic.keywords", "topic.bagofwords", "topic.embed"]),
    step("urgency", "Work out the urgency", [("in", "Tickets")], [("out", "Urgency")],
         "urgency", ["urgency.rules", "urgency.model"]),
    step("enrich",  "Add what we know", [("in", "Topics")], [("out", "Topics")],
         "enrich", ["enrich.none", "enrich.history"]),
    step("route",   "Escalate or file?",
         [("topic", "Topics"), ("urgency", "Urgency")],
         [("escalate", "Ticket"), ("file", "Ticket")], "route",
         ["route.decide"], kind="branch"),
    step("escalate","Page somebody", [("in", "Ticket")], [("out", "Receipt")],
         "escalate", ["escalate.page"]),
    step("file",    "Put it in the queue", [("in", "Ticket")], [("out", "Receipt")],
         "file", ["file.queue"]),
]

links = [*fanout("read", ["topic", "urgency"]),
         link("topic", "enrich"),
         # A join has to say which port each arrival lands on. `fanin` takes
         # that mapping, so the wiring cannot be written without it.
         *fanin({"enrich": "topic", "urgency": "urgency"}, "route"),
         link("route", "escalate", from_port="escalate"),
         link("route", "file", from_port="file")]

bench = graph("Triage a ticket",
              "Read tickets, judge topic and urgency, then escalate or file.",
              steps, nodes, links,
              profiles=[OptimizationProfile(id="p", objectives=(
                  OptimizationObjective("quality", "maximize", 1.0),))])

print("problems:    ", bench.validate() or "none")
print("layers:      ", bench.layers())
print("routes:      ", bench.route_count())
print("computations:", bench.computation_count())
''')

gallery.md("""
Two counts, because there are two questions.

**Routes — 24.** How many plans you could build: one candidate for each of the
seven steps, multiplied out. That includes naming a candidate for `file` even on
a run that escalates, because the plan is fixed before anyone knows which way
the branch will go.

**Computations — 48.** How many different things this graph can be seen doing.
Each of the 24 plans can escalate or it can file, and those are not the same
thing happening.

Which is bigger depends on the graph, so it is worth having both. Put four ways
of escalating and three ways of filing behind that branch and it flips: twelve
plans, but only seven behaviours, because plans differing solely behind the side
that was not taken do exactly the same thing.

The one printed next to a search has to be **routes**, because that is what the
search can reach. An earlier version printed the other here and had `solve`
announce a champion "out of 48 possible" for a space it could only ever draw 24
routes from. Drawing this notebook is what caught it.

---

## 1 · Shape — what runs when

Position is meaning. Two boxes in the same layer are genuinely independent and
may run at once; that is derived from which ports feed which, not asserted by
whoever drew it.
""")

gallery.code("viz.dag(bench)")

gallery.md("""
Three things this picture says that a numbered list of steps cannot.

**`topic` and `urgency` sit side by side**, so they are independent — they can
run together and they fail separately.

**`route` has a dashed outline** and says BRANCH: one way out is taken and the
other path is skipped, not failed.

**Two arrows arrive at `route`, each labelled with the port it lands on.** An
unlabelled join is the bug this library was written to stop: two things arriving
at one step with no way to see which input each one feeds.
""")

gallery.md("""
## 2 · Route space — what am I choosing between

One column per step, one box per option, one faint line per route. The red line
is a route chosen after evidence; the dashed amber one is what got chosen with
none.
""")

gallery.code('''
before = {"read": "read.csv", "topic": "topic.keywords", "urgency": "urgency.rules",
          "enrich": "enrich.none", "route": "route.decide",
          "escalate": "escalate.page", "file": "file.queue"}
after  = dict(before, read="read.jsonl", topic="topic.embed",
              enrich="enrich.history")

viz.route_space(bench, route=after, alternative=before)
''')

gallery.md("""
The picture is the *difference* between two routes, which is why two are drawn.
One highlighted path would show a fixed pipeline — the exact thing this design
argues against.

Three of the seven steps changed. The other four are the same in both, and you
can see that at a glance instead of diffing two dictionaries.

## 3 · Funnel — how much of it did I look at

The honest counter. Every number in it comes from a real search — none of them
are typed in, which is the only thing that makes a funnel worth drawing.

Bar length is log-scaled and the caption says so. A funnel from thousands down
to one is four invisible slivers on a linear axis, and a chart nobody can read
is a chart that can claim whatever it likes.
""")

gallery.code('''
from browsergraph import search

# A deliberately small budget, so the picture has something to show. Given more
# evaluations than the space has routes, a search simply enumerates it and the
# funnel is two bars of the same length.
found = search.within(bench, bench.optimization_profiles[0], evaluations=6, seed=1)

viz.funnel([
    ("every route",     found.total),
    ("policy-eligible", found.eligible_total),
    ("scored",          found.examined),
    ("chosen",          1),
], title=f"what a {found.strategy} search with 6 evaluations really saw")
''')

gallery.md("""
A search that scored a handful of routes and then announces "the best route",
without saying how many, is making a claim it did not earn. The number costs
nothing to carry — the search already knows it.

## 4 · Timeline — what actually happened

Everything so far has been about the graph. This one is about a run.

The shape picture says `topic` and `urgency` *may* run together. Whether they
*did* is a different question, and the only way to answer it is to look at where
the bars sit. So let us run it — twice, on one worker and then on two — and put
the two pictures side by side.
""")

gallery.code('''
import time

def slow(work):
    """Each step takes a beat, so the bars have something to show."""
    def do(**kw):
        time.sleep(0.12)
        return work(**kw)
    return do

runtime = execute.Runtime({
    "read.jsonl":  slow(lambda **kw: [{"id": 1, "text": "cannot log in"},
                                      {"id": 2, "text": "invoice wrong"}]),
    "read.csv":    slow(lambda **kw: []),
    "topic.keywords":  slow(lambda **kw: ["login", "billing"]),
    "topic.bagofwords":slow(lambda **kw: ["login", "billing"]),
    "topic.embed":     slow(lambda **kw: ["access", "billing"]),
    "urgency.rules":   slow(lambda **kw: ["high", "low"]),
    "urgency.model":   slow(lambda **kw: ["high", "low"]),
    "enrich.none":     lambda **kw: kw["in"],
    "enrich.history":  slow(lambda **kw: list(kw["in"]) + ["seen before"]),
    # A branch node names the one port it chose: (port, value). The library
    # marks every other port "not taken", which is how the steps behind them
    # end up skipped instead of running on an empty value.
    "route.decide":    slow(lambda **kw: ("escalate", {"id": 1})),
    "escalate.page":   slow(lambda **kw: "paged the on-call engineer"),
    "file.queue":      slow(lambda **kw: "queued"),
})

plan = compile_route(bench, after)
one = execute.run(plan, runtime, workers=1)
two = execute.run(plan, runtime, workers=2)

print(f"one worker : {one.seconds:.2f}s   ok={one.ok}")
print(f"two workers: {two.seconds:.2f}s   ok={two.ok}")
viz.timeline(one, title="one worker — the same plan, run sequentially")
''')

gallery.code("viz.timeline(two, title='two workers — topic and urgency overlap')")

gallery.md("""
Same plan, same graph, same route. The only difference is `workers=2`, and the
picture is where you can see it: `topic` and `urgency` start at the same moment
instead of one after the other. Nothing about the graph changed to allow that —
the independence was already in the wiring, and one number decided whether to
use it.

Look at `file` too. It is grey, meaning skipped: the branch went to `escalate`,
so `file` never had to run. That is not a failure and it is not a success. It is
a third thing, and merging it into either would misreport what happened.

One more run, to show the last colour. `escalate.page` declares the effect
`notify.person` — it pages a human. Turn effects off and it is refused *before*
it runs.
""")

gallery.code('''
refused = execute.run(plan, runtime, workers=2, allow_effects=False, strict=False)
print("ok:", refused.ok, " stopped at:", refused.stopped_at)
viz.timeline(refused, title="effects off — the paging step is refused, not run")
''')

gallery.md("""
Declaring the effect is what makes that possible. A step that quietly sends an
email is indistinguishable from one that does not until the email arrives.

## 5 · Scoreboard — what did the winner beat

`solve` tries routes, runs them, judges the output and keeps the best plus a
fallback. The champion on its own is a number with no denominator. This is the
denominator.
""")

gallery.code('''
from browsergraph.solve import solve

# The judge looks at the *output*, not at whether the code threw. Without this,
# "it worked" means "it did not raise" — and a route that returns nothing
# passes that test with full marks.
def judge(run):
    tickets = run.output("read")
    return bool(tickets), float(len(tickets))

answer = solve(bench, runtime, verify=judge, attempts=6,
               inputs=None, workspace=str(WORK), seed=3)

print(answer.text(bench))
viz.scoreboard(answer)
''')

gallery.md("""
`read.csv` returns an empty list. It never raises, so it is not a crash — and
every route using it scores zero because the judge looked at the output. That
is the whole argument for keeping judging separate from running, in one picture.

## 6 · Evidence — which step is the problem

Solving left something behind: a store of what each candidate did, every time it
ran. That is what this last chart is drawn from.

For each step of the champion, it asks one question — *is this the right pick,
and by how much?* — and answers in signed bits:

```
bits = log2(how often my pick worked / how often its best rival worked)
```

Positive means the choice is beating every alternative the evidence has seen.
Negative means something else is doing better and this is the step to change.
""")

gallery.code('''
from browsergraph.evidence import per_step_bits, stages_of

bits = per_step_bits(answer.evidence, answer.champion, stages_of(bench))
for stage, value in bits.items():
    print(f"  {stage:<10} {value:+.2f} bits")

viz.evidence(bits, title="the champion, step by step")
''')

gallery.md("""
Three things to read off this, and the third one is the important one.

**A step with one candidate sits at zero, and should.** There was no choice, so
there is nothing to be right or wrong about. A number there would invite reading
meaning into a decision nobody made.

**`read` is strongly positive.** `read.jsonl` produced tickets and `read.csv`
produced none, every time. That is a real difference and the evidence found it.

**`topic` comes out slightly negative — and that is noise, not a finding.** Look
at the judge: it counts tickets, and the topic step cannot change how many
tickets there are. So no amount of running will ever tell you which topic
candidate is better; the small number is just which routes happened to get
tried. A chart cannot know that. You have to.

The rule that falls out: **a step your judge cannot see is a step your evidence
cannot rank.** If it matters, measure it — and if you are not measuring it, do
not read its bar.

## 7 · A figure, for somewhere that is not a web page

The same route space as matplotlib, because you cannot paste an SVG into a
LaTeX document without a conversion step.
""")

gallery.code('''
import matplotlib
matplotlib.use("Agg")

ax = viz.to_figure(bench, route=after, alternative=before)
ax.figure.savefig(WORK / "route-space.png", dpi=120, bbox_inches="tight")
print("wrote", WORK / "route-space.png")
ax.figure
''')

gallery.md("""
## Everything at once

`viz.report` writes all of it to one self-contained page — no CDN, no fonts, no
fetch, so it opens from a `file://` URL on a machine with no network. That is
the only kind of artefact worth committing next to the code that made it.
""")

gallery.code('''
page = viz.write_report(bench, WORK / "triage.html", route=after, alternative=before,
                        search=[("every route", found.total),
                                ("policy-eligible", found.eligible_total),
                                ("scored", found.examined), ("chosen", 1)],
                        bits=bits, run=two, solution=answer)
text = pathlib.Path(page).read_text()
print(f"{page}  ({len(text):,} bytes)")
for forbidden in ("http://", "https://", "<script src", "@import"):
    print(f"  reaches for {forbidden!r}: {forbidden in text}")
''')

gallery.md("""
## And the same graph, as text

Two other renderers, for the places a picture will not go. Mermaid renders in a
GitHub README, where inline SVG does not. JSON is for a front end that would
rather draw it itself — emitting it costs nothing and removes the argument that
adopting the format means adopting this renderer.
""")

gallery.code('''
print(viz.to_mermaid(bench, after))
''')

gallery.md("""
Notice `route{{...}}` — Mermaid's rhombus, because the step is a branch — and
`enrich[...]` as an ordinary box. The shape carries the meaning in whichever
renderer you are using.

## What the pictures are for

Not decoration. Each one is a claim that can be checked:

* the **shape** says two steps are independent, and the layering proves it;
* the **route space** says how many options there were, and counts them;
* the **funnel** says how many were examined, and does not round it up;
* the **evidence** chart says which step to look at, and signs it;
* the **timeline** says whether the parallel plan really ran in parallel;
* the **scoreboard** says what the winner beat, failures included;
* the **figure** says the same thing somewhere a browser is not.

A diagram that cannot be wrong is a diagram that is not saying anything.

## Draw your own

Every function here takes a graph and nothing else. There is no browser code in
the drawing, no ticket code, nothing about this example:

```python
from browsergraph import viz

viz.dag(bench)                       # any workbench
viz.route_space(bench, route=r)      # any route
viz.timeline(run)                    # any finished run
viz.scoreboard(answer)               # any solve result
viz.trend(scores, reference={...})   # any series, against what it should beat
viz.write_report(bench, "out.html", route=r, run=run, solution=answer)
```

There is an eighth figure not shown here, because it needs something this
notebook does not have: a loop. `viz.trend` draws a series over time against the
lines it should be judged by — notebook 23 runs the evidence loop a hundred and
twenty times and draws exactly that.

If your problem can be written as steps with typed ports, it can be drawn, and
no drawing code changes. That is the whole claim, and this notebook is the test
of it — the pictures above are of support tickets, and not one line of the
renderer knows what a ticket is.
""")


if __name__ == "__main__":
    path = gallery.write()
    print(f"wrote {path}  ({len(gallery.cells)} cells)")
