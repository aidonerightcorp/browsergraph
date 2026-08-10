#!/usr/bin/env python3
"""Six more jobs, using the parts that were missing until now.

    17  Batch process many files   map: do it to all of them
    18  Retry and fall back        fallbacks, and a branch that routes failures
    19  Find duplicate records     blocking, pairwise scoring, a real join
    20  Forecast next month        a time split, and why random is wrong here
    21  Watch for changes          a branch that only alerts when something moved
    22  Sort text into categories  bag of words, numpy only

Same rules as 12-16: stdlib plus numpy, real input, real files at the end.

    python notebooks/build_more_jobs.py && python notebooks/execute.py 17 18 19 20 21 22
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_notebooks import Notebook  # noqa: E402
from build_problems import SETUP, show_artifacts, show_timeline  # noqa: E402

# ========================= 17 · batch process many files ====================

batch = Notebook("17-batch-many-files", "Do it to all of them")

batch.md("""
# Batch process many files

**The job.** Forty files. Read each one, pull out the numbers, add them up.

Until now every job in this series handled one thing. A step ran once. That is
fine for one page or one table and useless for a folder.

A `map` step runs its node once per item and gives back a list. That is the
whole difference, and it is worth seeing because it changes what the graph can
say, not just how it runs.

**In:** 40 small text files, two of them broken.
**Out:** a total, and a list of the files that could not be read.
**Files:** a summary, and a list of the failures.
""")

batch.code(SETUP)

batch.md("""
## The input

Forty readings files. Two are corrupt, because forty real files always include
a couple that are.
""")

batch.code('''
import random
rng = random.Random(5)

folder = WORK / "readings"
folder.mkdir()
for i in range(40):
    rows = [f"{rng.randint(1, 500)}" for _ in range(rng.randint(3, 8))]
    if i in (11, 29):
        rows[1] = "n/a"                     # the two broken ones
    (folder / f"day-{i:02d}.txt").write_text("\\n".join(rows))

print(f"{len(list(folder.glob('*.txt')))} files")
print("day-00:", (folder / "day-00.txt").read_text().replace("\\n", " "))
print("day-11:", (folder / "day-11.txt").read_text().replace("\\n", " "), " <- broken")
''')

batch.md("""
## The steps

Four. The middle one is the new part: `kind="map"`.

Note the port types. The list step gives `List[Path]`. The map step consumes
`List[Path]` and produces `List[Reading]` — but the **node** inside it takes one
`Path` and gives one `Reading`. The stage talks about the collection, the node
talks about one item, and that is what `map` means.
""")

batch.code('''
nodes = [
    node("list.files",  "list",    [],                       [("out", "List[Path]")]),
    node("read.one",    "read",    [("in", "Path")],         [("out", "Reading")]),
    node("split.ok",    "split",   [("in", "List[Reading]")],[("ok", "List[Reading]"), ("bad", "List[Reading]")]),
    node("total.all",   "total",   [("in", "List[Reading]")],[("out", "Summary")]),
]

stages = [
    stage("list",  "List the files", [],                        [("out", "List[Path]")],    "list",  ["list.files"]),
    StageDefinition(id="read", name="Read each one", kind="map",
                    required_capabilities=("read",),
                    inputs=(PortSpec("in", "List[Path]"),),
                    outputs=(PortSpec("out", "List[Reading]"),),
                    success="every file was attempted",
                    candidates=("read.one",)),
    stage("split", "Good from bad",  [("in", "List[Reading]")], [("ok", "List[Reading]"), ("bad", "List[Reading]")], "split", ["split.ok"]),
    stage("total", "Add them up",    [("in", "List[Reading]")], [("out", "Summary")],       "total", ["total.all"]),
]

edges = [Edge("list", "read"), Edge("read", "split"),
         Edge("split", "total", from_port="ok")]

bench = build("Process a folder of files",
              "Read forty files, add up the numbers, report the broken ones.",
              stages, nodes, edges)
print("kinds:", {s.id: s.kind for s in bench.leaf_stages})
''')

batch.code("viz.dag(bench)")

batch.md("""
## The code

`read_one` handles **one** file. It never sees the list. That is the point: the
node stays simple and the graph handles the "for each".
""")

batch.code('''
def list_files():
    return sorted(folder.glob("*.txt"))

def read_one(**kw):
    """One file in, one reading out. No loop anywhere in here."""
    path = kw["in"]
    numbers, bad = [], []
    for line in path.read_text().splitlines():
        try:
            numbers.append(int(line))
        except ValueError:
            bad.append(line)
    return {"file": path.name, "values": numbers, "unreadable": bad}

def split_ok(**kw):
    readings = kw["in"]
    return {"ok": [r for r in readings if not r["unreadable"]],
            "bad": [r for r in readings if r["unreadable"]]}

def total_all(workspace, **kw):
    readings = kw["in"]
    total = sum(v for r in readings for v in r["values"])
    summary = {"files": len(readings), "readings": sum(len(r["values"]) for r in readings),
               "total": total}
    (workspace / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary

runtime = execute.Runtime({"list.files": list_files, "read.one": read_one,
                           "split.ok": split_ok, "total.all": total_all})

plan = compile_route(bench, {s.id: s.candidates[0] for s in bench.leaf_stages})
run = execute.run(plan, runtime, workspace=WORK)
print(run.text())
''')

batch.md("""
## What came out
""")

batch.code('''
ok = run.values[("split", "ok")]
bad = run.values[("split", "bad")]
summary = run.output("total")

print(f"read {len(ok) + len(bad)} files")
print(f"  usable: {len(ok)}")
print(f"  broken: {len(bad)}  -> {[r['file'] for r in bad]}")
print(f"\\n{summary['readings']:,} readings, total {summary['total']:,}")

for r in bad:
    print(f"\\n{r['file']} could not read: {r['unreadable']}")
''')

batch.md("""
## When one file blows up

`read_one` above never raises — it collects the bad lines instead. If a node
does raise, the map step names the item, not just the batch.
""")

batch.code('''
def read_strict(**kw):
    path = kw["in"]
    return {"file": path.name,
            "values": [int(line) for line in path.read_text().splitlines()],
            "unreadable": []}

strict_run = execute.run(plan, execute.Runtime(
    dict(runtime._functions, **{"read.one": read_strict})), workspace=WORK)

print("ok:", strict_run.ok)
print(next(s for s in strict_run.steps if s.stage == "read").error)
''')

batch.md("""
`item 11` — the eleventh file, which is `day-11.txt`. "The batch failed" would
have left you to find that yourself across forty files.
""")

batch.code(show_artifacts())
batch.code(show_timeline('one map step over forty files'))


# ========================= 18 · retry and fall back =========================

retry = Notebook("18-retry-and-fall-back", "When a step fails")

retry.md("""
# Retry and fall back

**The job.** Fetch some data. The fast source is flaky. Use the slow one when it
breaks, and say so.

Two separate ideas here and they are easy to confuse:

* A **fallback** is another way to do the same step. Same contract, different
  implementation. The graph does not change.
* A **branch** is a different path through the work. The graph changes shape
  depending on what happened.

Both are here. Neither is a retry loop, and that is deliberate — a retry with no
limit is how a job runs forever.
""")

retry.code(SETUP)

retry.md("""
## Two ways to get the data

`fetch.fast` fails about half the time. `fetch.slow` always works and takes
longer. Same ports, same contract, so either can fill the step.
""")

retry.code('''
import random, time as clock
rng = random.Random(3)

attempts = {"fast": 0, "slow": 0, "cache": 0}

def fetch_fast(**kw):
    attempts["fast"] += 1
    if rng.random() < 0.6:
        raise ConnectionError("the fast source timed out")
    return {"source": "fast", "rows": [1, 2, 3, 4]}

def fetch_slow(**kw):
    attempts["slow"] += 1
    clock.sleep(0.01)
    return {"source": "slow", "rows": [1, 2, 3, 4]}

def fetch_cache(**kw):
    attempts["cache"] += 1
    return {"source": "cache (yesterday)", "rows": [1, 2, 3]}

print("three ways to do one step")
''')

retry.code('''
nodes = [
    node("fetch.fast",  "fetch",  [], [("out", "Data")], runtime={"deterministic": False}),
    node("fetch.slow",  "fetch",  [], [("out", "Data")], runtime={"deterministic": False}),
    node("fetch.cache", "fetch",  [], [("out", "Data")]),
    node("grade.rows",  "grade",  [("in", "Data")], [("fresh", "Data"), ("stale", "Data")]),
    node("use.fresh",   "use",    [("in", "Data")], [("out", "Report")]),
    node("warn.stale",  "warn",   [("in", "Data")], [("out", "Report")]),
]

stages = [
    stage("fetch", "Get the data", [], [("out", "Data")], "fetch",
          ["fetch.fast", "fetch.slow", "fetch.cache"]),
    StageDefinition(id="grade", name="Fresh or stale?", kind="branch",
                    required_capabilities=("grade",),
                    inputs=(PortSpec("in", "Data"),),
                    outputs=(PortSpec("fresh", "Data"), PortSpec("stale", "Data")),
                    success="the data was graded",
                    candidates=("grade.rows",)),
    stage("use",  "Use it",       [("in", "Data")], [("out", "Report")], "use",  ["use.fresh"]),
    stage("warn", "Flag it",      [("in", "Data")], [("out", "Report")], "warn", ["warn.stale"]),
]

edges = [Edge("fetch", "grade"),
         Edge("grade", "use",  from_port="fresh"),
         Edge("grade", "warn", from_port="stale")]

bench = build("Fetch with a fallback",
              "Get the data from whichever source works, and say which one it was.",
              stages, nodes, edges)
''')

retry.code("viz.dag(bench)")

retry.code('''
def grade_rows(**kw):
    """Name the port. Cached data goes down the warning path."""
    data = kw["in"]
    return ("stale", data) if "cache" in data["source"] else ("fresh", data)

def use_fresh(**kw):
    return {"status": "used", "rows": len(kw["in"]["rows"]), "source": kw["in"]["source"]}

def warn_stale(**kw):
    return {"status": "used with a warning", "rows": len(kw["in"]["rows"]),
            "source": kw["in"]["source"], "warning": "this data is not from today"}

runtime = execute.Runtime({
    "fetch.fast": fetch_fast, "fetch.slow": fetch_slow, "fetch.cache": fetch_cache,
    "grade.rows": grade_rows, "use.fresh": use_fresh, "warn.stale": warn_stale,
})

route = {"fetch": "fetch.fast", "grade": "grade.rows",
         "use": "use.fresh", "warn": "warn.stale"}
plan = compile_route(bench, route)
''')

retry.md("""
## Run it ten times

Same plan every time. The fast source fails at random, so the fallbacks earn
their keep on some runs and not others.
""")

retry.code('''
FALLBACKS = {"fetch": ["fetch.slow", "fetch.cache"]}

rows = []
for i in range(10):
    got = execute.run(plan, runtime, fallbacks=FALLBACKS)
    fetch_step = next(s for s in got.steps if s.stage == "fetch")
    taken = "use" if any(s.stage == "use" and not s.skipped for s in got.steps) else "warn"
    rows.append((i + 1, fetch_step.candidate, fetch_step.fell_back, taken, got.ok))

print(f"{'run':>4}  {'source used':<14}{'fell back':<11}{'path':<7}ok")
for i, source, fell, taken, ok in rows:
    print(f"{i:>4}  {source:<14}{str(fell):<11}{taken:<7}{ok}")

print(f"\\nattempts: {attempts}")
''')

retry.md("""
The fast source was tried every single time. When it failed the slow one took
over, and the run still succeeded. Nothing in the graph changed — only which
candidate did the work, and the run says which one that was.

Here is one of those runs as a picture. The amber bar is the step that fell
back, and it is amber rather than green on purpose: a run that succeeded on its
second choice and a run that succeeded outright are not the same run, and a
green tick for both is how a source that has quietly stopped working stays
invisible for a month.
""")

retry.code('''
# Keep running until one falls back, so the picture has something to show.
fell_back_run = None
while fell_back_run is None:
    got = execute.run(plan, runtime, fallbacks=FALLBACKS)
    if any(s.fell_back for s in got.steps):
        fell_back_run = got

viz.timeline(fell_back_run, title="a run that succeeded on its second choice")
''')

retry.md("""
## What happens with no fallbacks
""")

retry.code('''
bare = [execute.run(plan, runtime).ok for _ in range(10)]
print(f"without fallbacks: {sum(bare)}/10 runs succeeded")
print(f"with fallbacks:    {sum(1 for r in rows if r[4])}/10")
''')

retry.md("""
## The branch, when the data is old

Force the cache source and the graph takes the other path.
""")

retry.code('''
forced = execute.run(compile_route(bench, dict(route, fetch="fetch.cache")), runtime)
print(forced.text())
print()
print("used path:  ", [s.stage for s in forced.steps if not s.skipped and s.stage in ("use", "warn")])
print("skipped:    ", [s.stage for s in forced.steps if s.skipped])
print("result:     ", forced.output("warn"))
''')

retry.code('''
viz.timeline(forced, title="the cache path — one step skipped, not failed")
''')

retry.md("""
The `use` step never ran, and it is recorded as **skipped** rather than failed —
grey in the picture, not red. A path not taken is a correct outcome. If it were
logged as a failure every branching run would look broken and nobody would read
the logs.

Three colours, three meanings, all of which finish without raising: green ran,
amber fell back to another candidate, grey was skipped by a branch. Collapsing
them into "ok" throws away the only information worth having.
""")


# ========================= 19 · find duplicate records ======================

dedupe = Notebook("19-find-duplicates", "Find duplicate records")

dedupe.md("""
# Find duplicate records

**The job.** One customer list. The same people are in it more than once, spelled
differently. Find the pairs.

The naive version compares every record with every other one. For 5,000 records
that is 12.5 million comparisons. The fix is **blocking**: only compare records
that share something cheap, like the first letter of a surname plus a postcode.

Blocking and scoring are independent, and they meet at the decision. Another
diamond.

**In:** a list of customers with duplicates in it.
**Out:** matched pairs, and the ones too close to call.
**Files:** matches.jsonl, review.jsonl.
""")

dedupe.code(SETUP)

dedupe.code('''
PEOPLE = [
    ("1",  "Jonathan Smith",   "jsmith@example.com",  "SW1A 1AA"),
    ("2",  "Jon Smith",        "jsmith@example.com",  "SW1A 1AA"),
    ("3",  "J. Smith",         "j.smith@example.com", "SW1A 1AA"),
    ("4",  "Priya Raman",      "praman@example.com",  "M1 4BT"),
    ("5",  "Priya Ramen",      "praman@example.com",  "M1 4BT"),
    ("6",  "Alex Okafor",      "aokafor@example.com", "EH1 2NG"),
    ("7",  "Alexandra Okafor", "a.okafor@example.com","EH1 2NG"),
    ("8",  "Wei Zhang",        "wzhang@example.com",  "LS1 5AA"),
    ("9",  "Wei Zhang",        "wei.zhang@work.com",  "BS1 6TP"),
    ("10", "Marta Nowak",      "mnowak@example.com",  "CF10 1EP"),
]
records = [{"id": i, "name": n, "email": e, "postcode": p} for i, n, e, p in PEOPLE]
print(f"{len(records)} records")
for r in records[:4]:
    print(" ", r)
''')

dedupe.code('''
nodes = [
    node("load.people",  "read",  [],                    [("out", "Records")]),
    node("block.key",    "block", [("in", "Records")],   [("out", "Blocks")]),
    node("score.name",   "score", [("in", "Blocks")],    [("out", "Scores")]),
    node("score.contact","score", [("in", "Blocks")],    [("out", "Scores")]),
    node("decide.pairs", "decide",[("name", "Scores"), ("contact", "Scores")],
         [("matched", "Pairs"), ("review", "Pairs")]),
    node("write.pairs",  "write", [("matched", "Pairs"), ("review", "Pairs")],
         [("out", "Receipt")], effects=("file.write",)),
]

stages = [
    stage("load",    "Load the list",     [],                  [("out", "Records")], "read",  ["load.people"]),
    stage("block",   "Group cheaply",     [("in", "Records")], [("out", "Blocks")],  "block", ["block.key"]),
    stage("name",    "Compare names",     [("in", "Blocks")],  [("out", "Scores")],  "score", ["score.name"]),
    stage("contact", "Compare contacts",  [("in", "Blocks")],  [("out", "Scores")],  "score", ["score.contact"]),
    stage("decide",  "Decide each pair",  [("name", "Scores"), ("contact", "Scores")],
          [("matched", "Pairs"), ("review", "Pairs")], "decide", ["decide.pairs"]),
    stage("write",   "Write both",        [("matched", "Pairs"), ("review", "Pairs")],
          [("out", "Receipt")], "write", ["write.pairs"]),
]

edges = [Edge("load", "block"), Edge("block", "name"), Edge("block", "contact"),
         Edge("name", "decide", to_port="name"),
         Edge("contact", "decide", to_port="contact"),
         Edge("decide", "write", from_port="matched", to_port="matched"),
         Edge("decide", "write", from_port="review", to_port="review")]

bench = build("Find duplicate records",
              "Find the same person listed twice, without comparing everything to everything.",
              stages, nodes, edges)
print("layers:", bench.layers())
''')

dedupe.code("viz.dag(bench)")

dedupe.code('''
import itertools
from difflib import SequenceMatcher

def load_people():
    return records

def block_key(**kw):
    """Only compare people who share a postcode. Cheap, and it cuts the work."""
    blocks = {}
    for record in kw["in"]:
        blocks.setdefault(record["postcode"], []).append(record)
    pairs = [(a, b) for group in blocks.values()
             for a, b in itertools.combinations(group, 2)]
    everything = len(kw["in"]) * (len(kw["in"]) - 1) // 2
    return {"pairs": pairs, "compared": len(pairs), "without_blocking": everything}

def score_name(**kw):
    return {f"{a['id']}-{b['id']}": SequenceMatcher(None, a["name"].lower(),
                                                    b["name"].lower()).ratio()
            for a, b in kw["in"]["pairs"]}

def score_contact(**kw):
    """Same email is strong evidence. Same domain is weak evidence."""
    out = {}
    for a, b in kw["in"]["pairs"]:
        if a["email"] == b["email"]:
            out[f"{a['id']}-{b['id']}"] = 1.0
        elif a["email"].split("@")[1] == b["email"].split("@")[1]:
            out[f"{a['id']}-{b['id']}"] = 0.4
        else:
            out[f"{a['id']}-{b['id']}"] = 0.0
    return out

def decide_pairs(**kw):
    """Two independent scores, one decision, and an honest middle."""
    matched, review = [], []
    by_id = {r["id"]: r for r in records}
    for key, name_score in kw["name"].items():
        contact_score = kw["contact"][key]
        combined = 0.5 * name_score + 0.5 * contact_score
        left, right = key.split("-")
        row = {"pair": key, "a": by_id[left]["name"], "b": by_id[right]["name"],
               "name": round(name_score, 3), "contact": contact_score,
               "combined": round(combined, 3)}
        if combined >= 0.75:
            matched.append(row)
        elif combined >= 0.45:
            review.append(row)
    return {"matched": matched, "review": review}

def write_pairs(workspace, **kw):
    (workspace / "matches.jsonl").write_text(
        "\\n".join(json.dumps(r) for r in kw["matched"]))
    (workspace / "review.jsonl").write_text(
        "\\n".join(json.dumps(r) for r in kw["review"]))
    return {"matched": len(kw["matched"]), "review": len(kw["review"])}

runtime = execute.Runtime({
    "load.people": load_people, "block.key": block_key, "score.name": score_name,
    "score.contact": score_contact, "decide.pairs": decide_pairs,
    "write.pairs": write_pairs})

plan = compile_route(bench, {s.id: s.candidates[0] for s in bench.leaf_stages})
run = execute.run(plan, runtime, workspace=WORK, workers=2)
print(run.text())
''')

dedupe.md("""
## How much work blocking saved
""")

dedupe.code('''
blocks = run.output("block")
print(f"compared {blocks['compared']} pairs")
print(f"without blocking it would be {blocks['without_blocking']}")
print(f"saved {1 - blocks['compared'] / blocks['without_blocking']:.0%} of the work")
''')

dedupe.md("""
## The pairs
""")

dedupe.code('''
matched = run.values[("decide", "matched")]
review = run.values[("decide", "review")]

print(f"{'pair':<7}{'a':<20}{'b':<20}{'name':>7}{'contact':>9}{'combined':>10}")
for row in matched:
    print(f"{row['pair']:<7}{row['a']:<20}{row['b']:<20}"
          f"{row['name']:>7}{row['contact']:>9}{row['combined']:>10}   MATCH")
for row in review:
    print(f"{row['pair']:<7}{row['a']:<20}{row['b']:<20}"
          f"{row['name']:>7}{row['contact']:>9}{row['combined']:>10}   review")
''')

dedupe.md("""
Note what is **not** here. Wei Zhang appears twice with the same name and
different postcodes, so blocking never compared them. That is the trade
blocking makes: less work, and some pairs you will never see.

Saying that out loud matters. A dedupe run that reports "3 duplicates found"
without mentioning what it never looked at is telling you half the answer.
""")

dedupe.code(show_artifacts())
dedupe.code(show_timeline('blocking, then comparing'))


# ========================= 20 · forecast next month =========================

forecast = Notebook("20-forecast-next-month", "Forecast, without cheating")

forecast.md("""
# Forecast next month

**The job.** Two years of monthly sales. Predict the next three months.

There is one way to get this wrong that beats all the others: split the data at
random. Every row after the split leaks into training, the score looks superb,
and the model is useless in production.

So the split is a step in the graph with two named outputs, and you can see in
the picture which one feeds the features.
""")

forecast.code(SETUP)

forecast.code('''
import numpy as np

months = 24
t = np.arange(months)
trend = 120 + 4.5 * t
season = 25 * np.sin(2 * np.pi * t / 12)
noise = np.random.default_rng(4).normal(0, 6, months)
sales = (trend + season + noise).round(1)

print(f"{'month':>6}{'sales':>9}")
for i in list(range(3)) + [-2, -1]:
    print(f"{i % months:>6}{sales[i]:>9.1f}")
print(f"\\n{months} months, {sales.min():.0f} to {sales.max():.0f}")
''')

forecast.code('''
nodes = [
    node("load.series",  "read",     [],                    [("out", "Series")]),
    node("split.time",   "split",    [("in", "Series")],    [("train", "Series"), ("test", "Series")]),
    node("split.random", "split",    [("in", "Series")],    [("train", "Series"), ("test", "Series")],
         runtime={"deterministic": False}),
    node("feat.trend",   "features", [("in", "Series")],    [("out", "Matrix")]),
    node("fit.linear",   "fit",      [("in", "Matrix")],    [("out", "Model")]),
    node("check.ahead",  "check",    [("in", "Model")],     [("out", "Score")]),
]

stages = [
    stage("load",  "Load the series", [],                 [("out", "Series")], "read",     ["load.series"]),
    stage("split", "Hold months back",[("in", "Series")], [("train", "Series"), ("test", "Series")], "split",
          ["split.time", "split.random"]),
    stage("feat",  "Build features",  [("in", "Series")], [("out", "Matrix")], "features", ["feat.trend"]),
    stage("fit",   "Fit",             [("in", "Matrix")], [("out", "Model")],  "fit",      ["fit.linear"]),
    stage("check", "Score ahead",     [("in", "Model")],  [("out", "Score")],  "check",    ["check.ahead"]),
]

edges = [Edge("load", "split"), Edge("split", "feat", from_port="train"),
         Edge("feat", "fit"), Edge("fit", "check")]

bench = build("Forecast next month",
              "Predict the next three months without letting the future leak in.",
              stages, nodes, edges)
''')

forecast.code("viz.dag(bench)")

forecast.md("""
The arrow from `split` is labelled `train`. That is the whole safety property,
and it is visible in the picture rather than buried in a line of code.
""")

forecast.code('''
HOLD = 6

def load_series():
    return {"t": t, "y": sales}

def split_time(**kw):
    """By time. The last six months are the future and stay unseen."""
    s = kw["in"]
    return {"train": {"t": s["t"][:-HOLD], "y": s["y"][:-HOLD]},
            "test":  {"t": s["t"][-HOLD:], "y": s["y"][-HOLD:]}}

def split_random_bad(**kw):
    """The wrong one, kept so the difference can be measured rather than asserted."""
    s = kw["in"]
    order = np.random.default_rng(1).permutation(len(s["t"]))
    keep, hold = order[:-HOLD], order[-HOLD:]
    return {"train": {"t": s["t"][keep], "y": s["y"][keep]},
            "test":  {"t": s["t"][hold], "y": s["y"][hold]}}

def feat_trend(**kw):
    s = kw["in"]
    X = np.column_stack([np.ones(len(s["t"])), s["t"],
                         np.sin(2 * np.pi * s["t"] / 12),
                         np.cos(2 * np.pi * s["t"] / 12)])
    return {"X": X, "y": s["y"], "t": s["t"]}

def fit_linear(**kw):
    d = kw["in"]
    w, *_ = np.linalg.lstsq(d["X"], d["y"], rcond=None)
    return {"weights": w, "data": d}

def check_ahead(**kw):
    m = kw["in"]
    future = np.arange(months, months + 3)
    Xf = np.column_stack([np.ones(3), future,
                          np.sin(2 * np.pi * future / 12),
                          np.cos(2 * np.pi * future / 12)])
    fitted = m["data"]["X"] @ m["weights"]
    return {"in_sample_mae": float(np.abs(m["data"]["y"] - fitted).mean()),
            "forecast": (Xf @ m["weights"]).round(1).tolist(),
            "months": future.tolist()}

runtime = execute.Runtime({
    "load.series": load_series, "split.time": split_time,
    "split.random": split_random_bad, "feat.trend": feat_trend,
    "fit.linear": fit_linear, "check.ahead": check_ahead})

base = {s.id: s.candidates[0] for s in bench.leaf_stages}
proper = compile_route(bench, dict(base, split="split.time"))
leaky = compile_route(bench, dict(base, split="split.random"))

good = execute.run(proper, runtime)
bad = execute.run(leaky, runtime)

print(f"{'split':<16}{'deterministic':<15}{'in-sample error':>16}")
print(f"{'by time':<16}{str(proper.deterministic):<15}{good.output('check')['in_sample_mae']:>16.2f}")
print(f"{'at random':<16}{str(leaky.deterministic):<15}{bad.output('check')['in_sample_mae']:>16.2f}")
''')

forecast.md("""
The random split is marked **not deterministic** by the plan, without anyone
saying so in this cell. It read that off the node.

## The forecast
""")

forecast.code('''
result = good.output("check")
print(f"{'month':>6}{'forecast':>11}")
for m, value in zip(result["months"], result["forecast"]):
    print(f"{m:>6}{value:>11.1f}")

held = split_time(**{"in": load_series()})["test"]
print(f"\\nlast six actual: {[float(v) for v in held['y']]}")
''')


# ========================= 21 · watch for changes ===========================

watch = Notebook("21-watch-for-changes", "Only shout when something moved")

watch.md("""
# Watch for changes

**The job.** Check a thing on a schedule. Alert only when it actually changed.

The failure here is alerting every run. People turn those off, and then the one
that mattered goes unread.

A branch does the work: compare against last time, and take the alert path only
if something moved.
""")

watch.code(SETUP)

watch.code('''
nodes = [
    node("read.now",    "read",    [],                  [("out", "Snapshot")]),
    node("read.last",   "recall",  [],                  [("out", "Snapshot")]),
    node("diff.two",    "compare", [("now", "Snapshot"), ("before", "Snapshot")],
         [("changed", "Diff"), ("same", "Diff")]),
    node("alert.write", "alert",   [("in", "Diff")],    [("out", "Receipt")],
         effects=("file.write",)),
    node("note.quiet",  "note",    [("in", "Diff")],    [("out", "Receipt")]),
    node("save.state",  "save",    [("in", "Snapshot")],[("out", "Receipt")],
         effects=("file.write",)),
]

stages = [
    stage("now",   "Read it now",   [], [("out", "Snapshot")], "read",   ["read.now"]),
    stage("last",  "What we saw",   [], [("out", "Snapshot")], "recall", ["read.last"]),
    StageDefinition(id="diff", name="Did it move?", kind="branch",
                    required_capabilities=("compare",),
                    inputs=(PortSpec("now", "Snapshot"), PortSpec("before", "Snapshot")),
                    outputs=(PortSpec("changed", "Diff"), PortSpec("same", "Diff")),
                    success="the two snapshots were compared",
                    candidates=("diff.two",)),
    stage("alert", "Raise an alert",[("in", "Diff")], [("out", "Receipt")], "alert", ["alert.write"]),
    stage("quiet", "Stay quiet",    [("in", "Diff")], [("out", "Receipt")], "note",  ["note.quiet"]),
    stage("save",  "Remember it",   [("in", "Snapshot")], [("out", "Receipt")], "save", ["save.state"]),
]

edges = [Edge("now", "diff", to_port="now"), Edge("last", "diff", to_port="before"),
         Edge("diff", "alert", from_port="changed"),
         Edge("diff", "quiet", from_port="same"),
         Edge("now", "save")]

bench = build("Watch for changes",
              "Check on a schedule, alert only when something moved.",
              stages, nodes, edges)
print("layers:", bench.layers())
''')

watch.code("viz.dag(bench)")

watch.code('''
STATE = WORK / "last-seen.json"

WATCHED = {
    "run 1": {"version": "2.1.0", "status": "green"},
    "run 2": {"version": "2.1.0", "status": "green"},
    "run 3": {"version": "2.2.0", "status": "green"},
    "run 4": {"version": "2.2.0", "status": "red"},
}
current = {"value": None}

def read_now():
    return current["value"]

def read_last():
    return json.loads(STATE.read_text()) if STATE.exists() else {}

def diff_two(**kw):
    now, before = kw["now"], kw["before"]
    moved = {k: (before.get(k), v) for k, v in now.items() if before.get(k) != v}
    payload = {"changes": moved, "first_run": not before}
    return ("changed", payload) if moved else ("same", payload)

def alert_write(workspace, **kw):
    lines = [f"{k}: {old} -> {new}" for k, (old, new) in kw["in"]["changes"].items()]
    path = workspace / "alerts.log"
    with path.open("a") as handle:
        handle.write("\\n".join(lines) + "\\n")
    return {"alerted": lines}

def note_quiet(**kw):
    return {"alerted": []}

def save_state(**kw):
    STATE.write_text(json.dumps(kw["in"]))
    return {"saved": True}

runtime = execute.Runtime({
    "read.now": read_now, "read.last": read_last, "diff.two": diff_two,
    "alert.write": alert_write, "note.quiet": note_quiet, "save.state": save_state})

plan = compile_route(bench, {s.id: s.candidates[0] for s in bench.leaf_stages})

print(f"{'run':<8}{'reading':<32}{'path taken':<12}alerted")
runs = {}
for label, reading in WATCHED.items():
    current["value"] = reading
    got = execute.run(plan, runtime, workspace=WORK)
    runs[label] = got
    taken = "alert" if not next(s for s in got.steps if s.stage == "alert").skipped else "quiet"
    alerted = got.values.get(("alert", "out"), {}).get("alerted", [])
    print(f"{label:<8}{str(reading):<32}{taken:<12}{alerted}")
''')

text_of_quiet = '''
# The two runs side by side. Same plan, same graph — the data chose the path.
quiet = next(r for r in runs.values()
             if next(s for s in r.steps if s.stage == "alert").skipped)
noisy = next(r for r in runs.values()
             if not next(s for s in r.steps if s.stage == "alert").skipped)

display(viz.timeline(quiet, title="nothing moved — the alert step is skipped, grey"))
display(viz.timeline(noisy, title="something moved — the alert step runs, green"))
'''
watch.code(text_of_quiet)

watch.md("""
Four runs, two alerts. Runs 2 and 4 read the same thing as the run before them
in one case and a real change in the other, and only the real changes made
noise.

The first run alerts because everything is new. That is a judgement call and it
is visible in `first_run`, so you can decide to suppress it rather than
discovering the behaviour later.
""")

watch.code('''
print((WORK / "alerts.log").read_text())
''')


# ====================== 22 · sort text into categories ======================

text = Notebook("22-sort-text", "Sort text into categories")

text.md("""
# Sort text into categories

**The job.** Short messages. Work out which of three teams each one belongs to.

No model downloads. Bag of words and a linear classifier, in numpy, so you can
read every line of the maths.

Feature building splits in two — the words themselves, and simple shape signals
like length and whether there is a question mark — and they meet at the
assemble step. Same diamond as the tabular notebook, different domain.
""")

text.code(SETUP)

text.code('''
MESSAGES = [
    ("billing",  "my invoice is wrong again"),
    ("billing",  "charged twice this month"),
    ("billing",  "can I get a refund for the duplicate charge"),
    ("billing",  "the invoice total does not match my order"),
    ("billing",  "please cancel my subscription and refund"),
    ("billing",  "why was I charged after cancelling"),
    ("access",   "cannot log in to my account"),
    ("access",   "password reset email never arrives"),
    ("access",   "locked out after too many attempts"),
    ("access",   "two factor code is not accepted"),
    ("access",   "my login stopped working today"),
    ("access",   "reset link expired before I could use it"),
    ("bug",      "the export button does nothing"),
    ("bug",      "page crashes when I upload a file"),
    ("bug",      "the report shows blank rows"),
    ("bug",      "app freezes on the settings screen"),
    ("bug",      "clicking save throws an error"),
    ("bug",      "the chart renders upside down"),
]
HOLD_OUT = [
    ("billing", "I was charged twice for one invoice"),
    ("access",  "cannot reset my password"),
    ("bug",     "the upload page crashes every time"),
]
print(f"{len(MESSAGES)} training messages, {len(HOLD_OUT)} held back")
for label, message in MESSAGES[:3]:
    print(f"  {label:<9}{message}")
''')

text.code('''
nodes = [
    node("load.msgs",   "read",     [],                  [("out", "Corpus")]),
    node("words.bag",   "words",    [("in", "Corpus")],  [("out", "Matrix")]),
    node("shape.simple","shape",    [("in", "Corpus")],  [("out", "Matrix")]),
    node("join.side",   "assemble", [("words", "Matrix"), ("shape", "Matrix")], [("out", "Matrix")]),
    node("fit.softmax", "fit",      [("in", "Matrix")],  [("out", "Model")]),
    node("score.held",  "score",    [("in", "Model")],   [("out", "Score")]),
]

stages = [
    stage("load",     "Load messages",   [],                 [("out", "Corpus")], "read",  ["load.msgs"]),
    stage("words",    "Count words",     [("in", "Corpus")], [("out", "Matrix")], "words", ["words.bag"]),
    stage("shape",    "Shape signals",   [("in", "Corpus")], [("out", "Matrix")], "shape", ["shape.simple"]),
    stage("assemble", "Put together",    [("words", "Matrix"), ("shape", "Matrix")], [("out", "Matrix")], "assemble", ["join.side"]),
    stage("fit",      "Fit",             [("in", "Matrix")], [("out", "Model")],  "fit",   ["fit.softmax"]),
    stage("score",    "Score held-out",  [("in", "Model")],  [("out", "Score")],  "score", ["score.held"]),
]

edges = [Edge("load", "words"), Edge("load", "shape"),
         Edge("words", "assemble", to_port="words"),
         Edge("shape", "assemble", to_port="shape"),
         Edge("assemble", "fit"), Edge("fit", "score")]

bench = build("Sort text into categories",
              "Put each message with the team that should read it.", stages, nodes, edges)
print("layers:", bench.layers())
''')

text.code("viz.dag(bench)")

text.code('''
import numpy as np

LABELS = ["billing", "access", "bug"]

def load_msgs():
    return {"train": MESSAGES, "test": HOLD_OUT}

def words_bag(**kw):
    """One column per word that shows up at least twice."""
    corpus = kw["in"]["train"]
    counts = {}
    for _, message in corpus:
        for word in message.lower().split():
            counts[word] = counts.get(word, 0) + 1
    vocab = sorted(w for w, c in counts.items() if c >= 2)

    def vectorise(rows):
        M = np.zeros((len(rows), len(vocab)))
        for i, (_, message) in enumerate(rows):
            words = message.lower().split()
            for j, word in enumerate(vocab):
                M[i, j] = words.count(word)
        return M

    return {"train": vectorise(corpus), "test": vectorise(kw["in"]["test"]),
            "vocab": vocab}

def shape_simple(**kw):
    """Length and punctuation. Nothing to do with which words were used."""
    def shape(rows):
        return np.array([[len(m.split()), len(m), float("?" in m)] for _, m in rows])
    return {"train": shape(kw["in"]["train"]), "test": shape(kw["in"]["test"])}

def join_side(**kw):
    words, shape = kw["words"], kw["shape"]
    return {"train": np.column_stack([np.ones(len(words["train"])), words["train"], shape["train"]]),
            "test": np.column_stack([np.ones(len(words["test"])), words["test"], shape["test"]]),
            "vocab": words["vocab"],
            "y_train": np.array([LABELS.index(l) for l, _ in MESSAGES]),
            "y_test": np.array([LABELS.index(l) for l, _ in HOLD_OUT])}

def fit_softmax(**kw):
    """Multi-class logistic regression by gradient descent."""
    d = kw["in"]
    X, y = d["train"], d["y_train"]
    W = np.zeros((X.shape[1], len(LABELS)))
    onehot = np.eye(len(LABELS))[y]
    for _ in range(600):
        scores = X @ W
        scores -= scores.max(1, keepdims=True)
        probs = np.exp(scores); probs /= probs.sum(1, keepdims=True)
        W -= 0.35 * (X.T @ (probs - onehot)) / len(y)
    return {"W": W, "data": d}

def score_held(**kw):
    m = kw["in"]; d = m["data"]
    def predict(X):
        s = X @ m["W"]
        return s.argmax(1)
    train_pred, test_pred = predict(d["train"]), predict(d["test"])
    return {"train_accuracy": float((train_pred == d["y_train"]).mean()),
            "test_accuracy": float((test_pred == d["y_test"]).mean()),
            "predictions": [LABELS[i] for i in test_pred],
            "truth": [LABELS[i] for i in d["y_test"]],
            "vocab_size": len(d["vocab"])}

runtime = execute.Runtime({
    "load.msgs": load_msgs, "words.bag": words_bag, "shape.simple": shape_simple,
    "join.side": join_side, "fit.softmax": fit_softmax, "score.held": score_held})

plan = compile_route(bench, {s.id: s.candidates[0] for s in bench.leaf_stages})
run = execute.run(plan, runtime, workers=2)
print(run.text())
''')

text.md("""
`workers=2` because the two feature steps do not need each other. Here is the
proof that they really did overlap — the graph says they *may*, and only a
picture of the run says they *did*.
""")

text.code(show_timeline("counting words and measuring shape, at the same time"))

text.code('''
got = run.output("score")
print(f"vocabulary: {got['vocab_size']} words")
print(f"training accuracy: {got['train_accuracy']:.0%}")
print(f"held-out accuracy: {got['test_accuracy']:.0%}\\n")

for (truth, message), predicted in zip(HOLD_OUT, got["predictions"]):
    mark = "ok " if truth == predicted else "NO "
    print(f"  {mark} {predicted:<9} (really {truth:<9}) {message}")
''')

text.md("""
Training accuracy of 100% on eighteen messages means very little. The held-out
three are the only honest number here, and three is far too few to trust.

Saying that is the point. A notebook that printed 100% and stopped would be
reporting the sample it fitted to.
""")


if __name__ == "__main__":
    for book in (batch, retry, dedupe, forecast, watch, text):
        path = book.write()
        print(f"wrote {path}  ({len(book.cells)} cells)")
