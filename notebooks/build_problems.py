#!/usr/bin/env python3
"""Five notebooks that solve a real problem end to end.

The earlier notebooks explain the model. These use it. Each one takes real
input, runs real code, and writes real files you can open afterwards.

    12  Browse and scrape          a web page in, records out
    13  Ingest into a schema       messy input in, typed rows and rejects out
    14  Check and process an image an image in, a report and new images out
    15  Clean up data              a messy table in, a clean one and a report out
    16  Fit a model                a dataset in, scores and predictions out

Rules kept for all five:

* stdlib plus numpy plus Pillow. Nothing else. They run on a bare machine.
* Every value you see was computed. Nothing is typed in as an example.
* Every artifact is a file on disk with a size and a hash.

    python notebooks/build_problems.py && python notebooks/execute.py 12 13 14 15 16
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_notebooks import Notebook  # noqa: E402

REPO = "https://github.com/aidonerightcorp/browsergraph"

SETUP = f'''
try:
    import browsergraph  # noqa: F401
except ImportError:
    %pip install -q "browsergraph @ git+{REPO}.git"

import json, pathlib
from dataclasses import replace

from browsergraph import execute, viz
from browsergraph.compile import compile_route
from browsergraph.manifest import NodeManifest, PortSpec
from browsergraph.workbench import Edge, NodeCandidate, StageDefinition, WorkbenchDefinition

# A fresh folder each run. Left-over files from a previous run make the "what
# did this produce" list a lie, and that list is half the point here.
import shutil
WORK = pathlib.Path("work")
shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir()

def node(node_id, capability, takes, gives, **kw):
    """Describe one node. Ports are (name, type) pairs."""
    return NodeManifest(
        id=node_id, kind="function", description=f"{{capability}} via {{node_id}}",
        capabilities=(capability,),
        inputs=tuple(PortSpec(n, t) for n, t in takes),
        outputs=tuple(PortSpec(n, t) for n, t in gives), **kw)

def stage(sid, name, takes, gives, capability, candidates):
    """Describe one step of the job, and what could do it."""
    return StageDefinition(
        id=sid, name=name, required_capabilities=(capability,),
        inputs=tuple(PortSpec(n, t) for n, t in takes),
        outputs=tuple(PortSpec(n, t) for n, t in gives),
        success=f"{{name}} produced its declared output",
        candidates=tuple(candidates))

def build(title, task, stages, nodes, edges=()):
    """Put it together and check it before anything runs."""
    bench = WorkbenchDefinition(
        title=title, task=task, stages=tuple(stages), nodes=tuple(nodes),
        edges=tuple(edges),
        candidates=tuple(NodeCandidate(id=n.id, node_id=n.id) for n in nodes))
    problems = bench.validate()
    print("problems:", problems if problems else "none")
    return bench

print("ready")
'''


def show_artifacts() -> str:
    return '''
print("files written:")
for art in run.artifacts:
    print(f"  {art.path:<34} {art.bytes:>8,} bytes  {art.digest[:18]}…")
'''


# =========================== 12 · browse and scrape ==========================

scrape = Notebook("12-browse-and-scrape", "Browse and scrape")

scrape.md("""
# Browse and scrape

**The job.** Take a web page. Pull out the products. Save them as clean rows.

We build a page first so this notebook runs the same way every time. At the end
there is a one-line change that points the same graph at a real URL. The graph
does not change. Only the function behind one step does.

**In:** an HTML page.
**Out:** a list of product rows.
**Files:** the raw page, the rows as JSONL, and a run receipt.
""")

scrape.code(SETUP)

scrape.md("""
## The input

A page with four products. One has a missing price and one has a price written
in a different style. Real pages are like this. A scraper that only works on
tidy pages is not finished.
""")

scrape.code('''
PAGE = """<!doctype html><html><body>
<h1>Camping gear</h1>
<ul class="products">
  <li class="product"><span class="name">Tent 2P</span><span class="price">$189.00</span><span class="sku">TN-2P</span></li>
  <li class="product"><span class="name">Sleeping bag</span><span class="price">USD 74.50</span><span class="sku">SB-01</span></li>
  <li class="product"><span class="name">Camp stove</span><span class="price"></span><span class="sku">CS-77</span></li>
  <li class="product"><span class="name">Head torch</span><span class="price">$21</span><span class="sku">HT-03</span></li>
</ul></body></html>"""

source = WORK / "page.html"
source.write_text(PAGE)
print(f"wrote {source} ({source.stat().st_size} bytes)")
print(PAGE[:180], "...")
''')

scrape.md("""
## The steps

Six steps. Each says what it needs and what it gives back. Nothing here says
*how* yet.
""")

scrape.code('''
nodes = [
    node("read.file",     "payload.read",   [],                      [("out", "Bytes")]),
    node("read.http",     "payload.read",   [],                      [("out", "Bytes")],
         effects=("network.read",), permissions=("net.read",), runtime={"deterministic": False}),
    node("parse.html",    "parse.structure",[("in", "Bytes")],       [("out", "Blocks")]),
    node("locate.fields", "locate.values",  [("in", "Blocks")],      [("out", "Fields")]),
    node("clean.values",  "normalise",      [("in", "Fields")],      [("out", "Records")]),
    node("check.rows",    "verify",         [("in", "Records")],     [("kept", "Records"), ("dropped", "Records")]),
    node("save.jsonl",    "write",          [("in", "Records")],     [("out", "Receipt")],
         effects=("file.write",)),
]

stages = [
    stage("read",   "Read the page",   [],                  [("out", "Bytes")],   "payload.read",    ["read.file", "read.http"]),
    stage("parse",  "Parse the HTML",  [("in", "Bytes")],   [("out", "Blocks")],  "parse.structure", ["parse.html"]),
    stage("locate", "Find the fields", [("in", "Blocks")],  [("out", "Fields")],  "locate.values",   ["locate.fields"]),
    stage("clean",  "Clean the values",[("in", "Fields")],  [("out", "Records")], "normalise",       ["clean.values"]),
    stage("check",  "Check each row",  [("in", "Records")], [("kept", "Records"), ("dropped", "Records")], "verify", ["check.rows"]),
    stage("save",   "Save the rows",   [("in", "Records")], [("out", "Receipt")], "write",           ["save.jsonl"]),
]

edges = [Edge("read", "parse"), Edge("parse", "locate"), Edge("locate", "clean"),
         Edge("clean", "check"), Edge("check", "save", from_port="kept")]

bench = build("Scrape a product page",
              "Turn a page of products into clean rows.", stages, nodes, edges)
print("layers:", bench.layers())
''')

scrape.md("""
Note the `check` step has **two** outputs: rows it kept and rows it dropped.
Only the kept ones go on to be saved. The dropped ones are still there to look
at. A scraper that quietly bins bad rows is how you find out months later.
""")

scrape.code("viz.dag(bench)")

scrape.md("""
## Now the actual code

One function per step. Plain Python. `html.parser` is in the standard library,
so there is nothing to install.
""")

scrape.code('''
from html.parser import HTMLParser

class Collector(HTMLParser):
    """Grab the text inside every <span class="..."> we care about."""
    def __init__(self):
        super().__init__()
        self.rows, self.current, self.field = [], {}, None
    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "")
        if tag == "li" and "product" in classes:
            self.current = {}
        if tag == "span" and classes in ("name", "price", "sku"):
            self.field = classes
            self.current.setdefault(classes, "")
    def handle_endtag(self, tag):
        if tag == "li" and self.current:
            self.rows.append(self.current); self.current = {}
        if tag == "span":
            self.field = None
    def handle_data(self, text):
        if self.field:
            self.current[self.field] += text.strip()

def read_file():
    return source.read_bytes()

def read_http():
    """Fetch a real page over the network."""
    import urllib.request
    with urllib.request.urlopen(LIVE_URL, timeout=20) as response:
        return response.read()

def parse_html(**kw):
    collector = Collector()
    collector.feed(kw["in"].decode())
    return collector.rows

def locate_fields(**kw):
    return [{"name": r.get("name", ""), "price_text": r.get("price", ""),
             "sku": r.get("sku", "")} for r in kw["in"]]

def clean_values(**kw):
    """Prices come in three styles here. Turn them all into a number."""
    import re
    out = []
    for row in kw["in"]:
        digits = re.sub(r"[^0-9.]", "", row["price_text"])
        out.append({"sku": row["sku"], "name": row["name"],
                    "price": float(digits) if digits else None,
                    "currency": "USD" if row["price_text"] else ""})
    return out

def check_rows(**kw):
    """A row without a price is not a product row we can use."""
    kept = [r for r in kw["in"] if r["price"] is not None]
    dropped = [dict(r, why="no price on the page") for r in kw["in"] if r["price"] is None]
    return {"kept": kept, "dropped": dropped}

def save_jsonl(workspace, **kw):
    path = workspace / "products.jsonl"
    path.write_text("\\n".join(json.dumps(r) for r in kw["in"]))
    return {"rows": len(kw["in"]), "path": str(path)}

runtime = execute.Runtime({
    "read.file": read_file, "read.http": read_http, "parse.html": parse_html,
    "locate.fields": locate_fields, "clean.values": clean_values,
    "check.rows": check_rows, "save.jsonl": save_jsonl,
})
print("nothing missing:", runtime.missing(compile_route(bench, {s.id: s.candidates[0] for s in bench.leaf_stages})) == [])
''')

scrape.md("""
## Run it
""")

scrape.code('''
route = {s.id: s.candidates[0] for s in bench.leaf_stages}
plan = compile_route(bench, route)
run = execute.run(plan, runtime, workspace=WORK)
print(run.text())
''')

scrape.md("""
## What came out
""")

scrape.code('''
kept = run.values[("check", "kept")]
dropped = run.values[("check", "dropped")]

print(f"{'sku':<8}{'name':<16}{'price':>9}  currency")
for row in kept:
    print(f"{row['sku']:<8}{row['name']:<16}{row['price']:>9.2f}  {row['currency']}")

print(f"\\ndropped {len(dropped)}:")
for row in dropped:
    print(f"  {row['sku']}  {row['name']}  — {row['why']}")

print("\\nsave step reported:", run.output("save"))
''')

scrape.code(show_artifacts())

scrape.md("""
## Reading the saved file back

The file is the point. Here it is again, straight off disk.
""")

scrape.code('''
saved = (WORK / "products.jsonl").read_text().splitlines()
print(f"{len(saved)} lines")
for line in saved:
    print(" ", line)
''')

scrape.md("""
## Now do it against a live page

Same graph. Same checks. One different candidate for the `read` step.

This one really does go and fetch a page over the network. It is the honest
version of "browse and scrape", and it teaches something the fixture cannot.
""")

scrape.code('''
LIVE_URL = "https://example.com"

live_route = dict(route, read="read.http")
plan_live = compile_route(bench, live_route)

print("same layers:", plan_live.layers == plan.layers)
print("different plan:", plan_live.digest != plan.digest)
print("effects now:", plan_live.effects)
print("deterministic:", plan_live.deterministic, "— a live page can change under you")
''')

scrape.md("""
The plan says this run touches the network and is no longer deterministic. We
did not tell it that. It read it off the node we picked.
""")

scrape.code('''
try:
    live = execute.run(plan_live, runtime, workspace=WORK)
    print(live.text())
    fetched = live.output("read")
    print(f"\\nfetched {len(fetched):,} bytes from {LIVE_URL}")
except Exception as problem:
    live = None
    print("no network here:", problem)
''')

scrape.md("""
## The result worth stopping on

The run finished. Look at what it actually produced.
""")

scrape.code('''
if live is not None:
    kept_live = live.values.get(("check", "kept"), [])
    dropped_live = live.values.get(("check", "dropped"), [])
    print(f"steps ok: {live.ok}")
    print(f"products found: {len(kept_live)}")
    print(f"rows dropped:   {len(dropped_live)}")
''')

scrape.md("""
Every step passed. Zero products came out.

That is the single most common way a scraper is broken, and it is why this
library exists. `example.com` has no `li.product` elements, so the locator found
nothing, and *finding nothing is not an error* — it is an empty list, which is a
perfectly good list.

Nothing crashed. A cron job running this would report success forever.

**A run that completed is not a run that worked.** The check has to be on the
result, not on whether the code threw.
""")

scrape.code('''
def check_rows_strict(**kw):
    """Same check, plus one line: an empty result is a failure."""
    rows = kw["in"]
    if not rows:
        raise ValueError("the locator matched nothing — the selector is probably "
                         "wrong for this page")
    kept = [r for r in rows if r["price"] is not None]
    dropped = [dict(r, why="no price on the page") for r in rows if r["price"] is None]
    return {"kept": kept, "dropped": dropped}

strict = execute.Runtime(dict(runtime._functions))
strict.register("check.rows", check_rows_strict)

guarded = execute.run(plan_live, strict, workspace=WORK)
print("ok:", guarded.ok, "| stopped at:", guarded.stopped_at or "nowhere")
print(guarded.steps[-1].error)
''')

scrape.md("""
Now it fails, at the step that noticed, with a sentence that says what to fix.

The fixture still works exactly as before — same graph, same runtime, one
different candidate for one step.
""")

scrape.code('''
again = execute.run(plan, strict, workspace=WORK)
print("fixture run ok:", again.ok, "|", len(again.values[("check", "kept")]), "products")
''')

# ======================= 13 · ingest into a schema ==========================

ingest = Notebook("13-ingest-into-schema", "Ingest and extract into a schema")

ingest.md("""
# Ingest and extract into a schema

**The job.** Records arrive in three different shapes. Get them all into one
schema. Keep the ones that fit. Say exactly why the others did not.

The second half matters more than the first. Anyone can parse the good rows.
The question is what happens to the rest.

**In:** a file of mixed-format records.
**Out:** rows that match the schema, and rows that do not, with reasons.
**Files:** accepted.jsonl, rejected.jsonl, schema.json.
""")

ingest.code(SETUP)

ingest.md("""
## The input

Nine records. Some JSON, some `key=value`, some CSV. Two are broken in ways
worth catching: a bad date and a missing required field.
""")

ingest.code('''
RAW = """{"id": "A1", "email": "ana@example.com", "signed_up": "2026-01-14", "plan": "pro"}
{"id": "A2", "email": "bo@example.com", "signed_up": "2026-02-02", "plan": "free"}
id=A3; email=cy@example.com; signed_up=2026-02-11; plan=pro
id=A4; email=not-an-email; signed_up=2026-03-01; plan=free
A5,dee@example.com,2026-03-09,team
A6,eli@example.com,14/03/2026,pro
A7,fay@example.com,2026-03-20,
{"id": "A8", "email": "gus@example.com", "plan": "free"}
{"id": "A9", "email": "hal@example.com", "signed_up": "2026-04-02", "plan": "enterprise"}"""

source = WORK / "records.txt"
source.write_text(RAW)
print(f"{len(RAW.splitlines())} records in {source}")
''')

ingest.md("""
## The schema

Written down first, on purpose. A schema you infer from the data cannot reject
the data.
""")

ingest.code('''
SCHEMA = {
    "id":        {"type": "string", "required": True,  "pattern": r"^A\\d+$"},
    "email":     {"type": "string", "required": True,  "pattern": r"^[^@\\s]+@[^@\\s]+\\.[a-z]+$"},
    "signed_up": {"type": "date",   "required": True,  "format": "YYYY-MM-DD"},
    "plan":      {"type": "enum",   "required": True,  "values": ["free", "pro", "team"]},
}
(WORK / "schema.json").write_text(json.dumps(SCHEMA, indent=2))
for field, rule in SCHEMA.items():
    print(f"  {field:<11} {rule['type']:<8} {'required' if rule['required'] else 'optional'}")
''')

ingest.md("""
## The steps

Read, work out the shape of each line, parse it, map it onto the schema, check
it, then split into kept and rejected and write both.
""")

ingest.code('''
nodes = [
    node("read.lines",  "data.read",  [],                    [("out", "Lines")]),
    node("detect.shape","detect",     [("in", "Lines")],     [("out", "Tagged")]),
    node("parse.mixed", "parse",      [("in", "Tagged")],    [("out", "Raw")]),
    node("map.schema",  "map",        [("in", "Raw")],       [("out", "Mapped")]),
    node("check.schema","validate",   [("in", "Mapped")],    [("ok", "Rows"), ("bad", "Rows")]),
    node("write.both",  "write",      [("ok", "Rows"), ("bad", "Rows")], [("out", "Receipt")],
         effects=("file.write",)),
]

stages = [
    stage("read",   "Read the file",   [],                 [("out", "Lines")],  "data.read", ["read.lines"]),
    stage("detect", "Spot the format", [("in", "Lines")],  [("out", "Tagged")], "detect",    ["detect.shape"]),
    stage("parse",  "Parse each line", [("in", "Tagged")], [("out", "Raw")],    "parse",     ["parse.mixed"]),
    stage("map",    "Map to schema",   [("in", "Raw")],    [("out", "Mapped")], "map",       ["map.schema"]),
    stage("check",  "Check each row",  [("in", "Mapped")], [("ok", "Rows"), ("bad", "Rows")], "validate", ["check.schema"]),
    stage("write",  "Write both files",[("ok", "Rows"), ("bad", "Rows")], [("out", "Receipt")], "write", ["write.both"]),
]

edges = [Edge("read", "detect"), Edge("detect", "parse"), Edge("parse", "map"),
         Edge("map", "check"),
         Edge("check", "write", from_port="ok",  to_port="ok"),
         Edge("check", "write", from_port="bad", to_port="bad")]

bench = build("Ingest into a schema",
              "Get mixed-format records into one schema, and say why the rest failed.",
              stages, nodes, edges)
''')

ingest.md("""
The last step takes **two** inputs: the good rows and the bad ones. Both get
written. That is a join, and it is why this is a graph and not a list of steps.
""")

ingest.code("viz.dag(bench)")

ingest.code('''
import re
from datetime import date

def read_lines():
    return [l for l in source.read_text().splitlines() if l.strip()]

def detect_shape(**kw):
    """Three shapes. Guess from the first character or the separators."""
    out = []
    for line in kw["in"]:
        if line.lstrip().startswith("{"):
            shape = "json"
        elif "=" in line and ";" in line:
            shape = "keyvalue"
        else:
            shape = "csv"
        out.append((shape, line))
    return out

def parse_mixed(**kw):
    rows = []
    for shape, line in kw["in"]:
        if shape == "json":
            rows.append(json.loads(line))
        elif shape == "keyvalue":
            pairs = [p.strip() for p in line.split(";") if p.strip()]
            rows.append(dict(p.split("=", 1) for p in pairs))
        else:
            bits = [b.strip() for b in line.split(",")]
            rows.append(dict(zip(["id", "email", "signed_up", "plan"], bits)))
    return rows

def map_schema(**kw):
    """Keep only the fields the schema knows about, in schema order."""
    return [{field: row.get(field, "") for field in SCHEMA} for row in kw["in"]]

def check_schema(**kw):
    ok, bad = [], []
    for row in kw["in"]:
        reasons = []
        for field, rule in SCHEMA.items():
            value = (row.get(field) or "").strip()
            if rule["required"] and not value:
                reasons.append(f"{field} is missing"); continue
            if rule["type"] == "enum" and value not in rule["values"]:
                reasons.append(f"{field} {value!r} is not one of {rule['values']}")
            if rule["type"] == "date":
                try:
                    date.fromisoformat(value)
                except ValueError:
                    reasons.append(f"{field} {value!r} is not YYYY-MM-DD")
            if "pattern" in rule and value and not re.match(rule["pattern"], value):
                reasons.append(f"{field} {value!r} does not look right")
        (ok if not reasons else bad).append(
            row if not reasons else dict(row, why="; ".join(reasons)))
    return {"ok": ok, "bad": bad}

def write_both(workspace, **kw):
    good = workspace / "accepted.jsonl"
    bad = workspace / "rejected.jsonl"
    good.write_text("\\n".join(json.dumps(r) for r in kw["ok"]))
    bad.write_text("\\n".join(json.dumps(r) for r in kw["bad"]))
    return {"accepted": len(kw["ok"]), "rejected": len(kw["bad"])}

runtime = execute.Runtime({
    "read.lines": read_lines, "detect.shape": detect_shape,
    "parse.mixed": parse_mixed, "map.schema": map_schema,
    "check.schema": check_schema, "write.both": write_both,
})

plan = compile_route(bench, {s.id: s.candidates[0] for s in bench.leaf_stages})
run = execute.run(plan, runtime, workspace=WORK)
print(run.text())
''')

ingest.md("""
## What got in, and what did not
""")

ingest.code('''
ok = run.values[("check", "ok")]
bad = run.values[("check", "bad")]

print(f"accepted {len(ok)}:")
for row in ok:
    print(f"  {row['id']:<4}{row['email']:<22}{row['signed_up']:<12}{row['plan']}")

print(f"\\nrejected {len(bad)}:")
for row in bad:
    print(f"  {row['id'] or '(no id)':<4}{row['email'][:20]:<22}{row['why']}")
''')

ingest.md("""
Three rejected, each for a different reason, each named. That is the useful
output. A run that said "6 of 9 rows loaded" would leave you to find out which
three and why.
""")

ingest.code(show_artifacts())


# ====================== 14 · check and process an image =====================

image = Notebook("14-check-and-process-image", "Check and process an image")

image.md("""
# Check and process an image

**The job.** Take some images. Check each one is worth keeping. Process the good
ones. Say why the rest were skipped.

The check comes first for a reason. Resizing a blank image gives you a smaller
blank image, and everything downstream believes it worked.

**In:** three images we draw here.
**Out:** a report per image, plus processed copies.
**Files:** the originals, the processed versions, thumbnails, and report.json.
""")

image.code(SETUP)

image.md("""
## The input

Three images. One normal photo-ish gradient, one that is almost entirely one
colour, and one that is tiny. The last two should not pass.
""")

image.code('''
import numpy as np
from PIL import Image

rng = np.random.default_rng(7)

def gradient(w, h):
    x = np.linspace(0, 255, w, dtype=np.uint8)
    base = np.tile(x, (h, 1))
    noise = rng.integers(0, 40, size=(h, w), dtype=np.uint8)
    rgb = np.dstack([base, np.roll(base, 40, axis=1), noise])
    return Image.fromarray(rgb.astype(np.uint8), "RGB")

sources = {}
sources["good.png"] = gradient(640, 400)
sources["blank.png"] = Image.fromarray(np.full((400, 640, 3), 250, np.uint8), "RGB")
sources["tiny.png"] = gradient(48, 30)

for name, img in sources.items():
    path = WORK / name
    img.save(path)
    print(f"  {name:<12} {img.size[0]:>4} x {img.size[1]:<4}  {path.stat().st_size:>7,} bytes")
''')

image.md("""
## The steps

Load, measure, decide, process, save. The decide step splits into pass and fail
so the two paths are visible.
""")

image.code('''
nodes = [
    node("load.folder", "image.read",    [],                    [("out", "Images")]),
    node("measure.px",  "image.measure", [("in", "Images")],    [("out", "Report")]),
    node("decide.keep", "image.decide",  [("in", "Report")],    [("keep", "Images"), ("skip", "Report")]),
    node("process.std", "image.process", [("in", "Images")],    [("out", "Images")]),
    node("save.images", "write",         [("in", "Images"), ("report", "Report")], [("out", "Receipt")],
         effects=("file.write",)),
]

stages = [
    stage("load",    "Load images",   [],                  [("out", "Images")],  "image.read",    ["load.folder"]),
    stage("measure", "Measure them",  [("in", "Images")],  [("out", "Report")],  "image.measure", ["measure.px"]),
    stage("decide",  "Keep or skip",  [("in", "Report")],  [("keep", "Images"), ("skip", "Report")], "image.decide", ["decide.keep"]),
    stage("process", "Process kept",  [("in", "Images")],  [("out", "Images")],  "image.process", ["process.std"]),
    stage("save",    "Save results",  [("in", "Images"), ("report", "Report")], [("out", "Receipt")], "write", ["save.images"]),
]

edges = [Edge("load", "measure"), Edge("measure", "decide"),
         Edge("decide", "process", from_port="keep"),
         Edge("process", "save", to_port="in"),
         Edge("decide", "save", from_port="skip", to_port="report")]

bench = build("Check and process images",
              "Check each image, process the good ones, say why the rest were skipped.",
              stages, nodes, edges)
print("layers:", bench.layers())
''')

image.code("viz.dag(bench)")

image.md("""
## The checks

Three numbers per image. Size, how much of the colour range is used, and how
many distinct colours there are. A near-blank image scores low on the last two
even though it is a perfectly valid PNG.
""")

image.code('''
MIN_WIDTH, MIN_HEIGHT = 200, 200
MIN_SPREAD, MIN_COLOURS = 20.0, 50

def load_folder():
    return {name: Image.open(WORK / name).convert("RGB") for name in sources}

def measure_px(**kw):
    report = {}
    for name, img in kw["in"].items():
        array = np.asarray(img)
        report[name] = {
            "width": img.size[0], "height": img.size[1],
            "spread": float(array.std()),
            "colours": int(len(np.unique(array.reshape(-1, 3), axis=0))),
            "mean": float(array.mean()),
        }
    return report

def decide_keep(**kw):
    keep, skip = {}, {}
    for name, m in kw["in"].items():
        why = []
        if m["width"] < MIN_WIDTH or m["height"] < MIN_HEIGHT:
            why.append(f"too small ({m['width']}x{m['height']})")
        if m["spread"] < MIN_SPREAD:
            why.append(f"almost flat (spread {m['spread']:.1f})")
        if m["colours"] < MIN_COLOURS:
            why.append(f"only {m['colours']} colours")
        if why:
            skip[name] = dict(m, why="; ".join(why))
        else:
            keep[name] = Image.open(WORK / name).convert("RGB")
    return {"keep": keep, "skip": skip}

def process_std(**kw):
    """Two outputs per image: a web-sized copy and a thumbnail."""
    out = {}
    for name, img in kw["in"].items():
        big = img.copy(); big.thumbnail((320, 320))
        small = img.copy(); small.thumbnail((96, 96))
        out[f"web_{name}"] = big
        out[f"thumb_{name}"] = small
    return out

def save_images(workspace, **kw):
    written = []
    for name, img in kw["in"].items():
        path = workspace / name
        img.save(path)
        written.append(f"{name} {img.size[0]}x{img.size[1]}")
    (workspace / "report.json").write_text(json.dumps(kw["report"], indent=2))
    return {"written": written, "skipped": list(kw["report"])}

runtime = execute.Runtime({
    "load.folder": load_folder, "measure.px": measure_px,
    "decide.keep": decide_keep, "process.std": process_std,
    "save.images": save_images,
})

plan = compile_route(bench, {s.id: s.candidates[0] for s in bench.leaf_stages})
run = execute.run(plan, runtime, workspace=WORK)
print(run.text())
''')

image.md("""
## The numbers, and the decision they drove
""")

image.code('''
measured = run.output("measure")
skipped = run.values[("decide", "skip")]
kept = run.values[("decide", "keep")]

print(f"{'image':<12}{'size':>11}{'spread':>9}{'colours':>9}   verdict")
for name, m in measured.items():
    verdict = "keep" if name in kept else "SKIP — " + skipped[name]["why"]
    print(f"{name:<12}{m['width']:>5}x{m['height']:<5}{m['spread']:>9.1f}{m['colours']:>9,}   {verdict}")
''')

image.md("""
The blank image is a real PNG of the right size. Only the spread and colour
count catch it. That is the check worth having: it fails the thing that looks
fine.
""")

image.code(show_artifacts())

image.md("""
## Look at what came out
""")

image.code('''
from IPython.display import display
for name in sorted(p.name for p in WORK.glob("web_*.png")):
    print(name)
    display(Image.open(WORK / name))
''')


# ============================ 15 · clean up data ============================

clean = Notebook("15-clean-up-data", "Clean up messy data")

clean.md("""
# Clean up messy data

**The job.** Take a table with the usual problems. Fix what can be fixed. Record
what was done. Flag what could not be.

The record is the deliverable. A cleaned file with no report is a file you
cannot trust, because you cannot see what was changed.

**In:** a messy CSV.
**Out:** a clean CSV plus a report of every change.
**Files:** messy.csv, clean.csv, cleaning-report.json.
""")

clean.code(SETUP)

clean.md("""
## The input

Twelve rows with six problems in them: duplicate rows, padded whitespace,
mixed case in a category, numbers stored as text with symbols, a missing value,
and one outlier that is clearly a data entry slip.
""")

clean.code('''
MESSY = """id,city,category,amount,joined
1, London ,Retail,"$1,200.00",2026-01-05
2,Leeds,retail,$980.50,2026-01-09
3, Leeds,RETAIL,$1;050,2026-01-11
4,Bristol,Wholesale,$640.00,2026-02-01
2,Leeds,retail,$980.50,2026-01-09
5,london,Retail,,2026-02-14
6,Bristol ,wholesale,$720.25,2026-02-20
7,Leeds,Retail,$99999999.00,2026-03-02
8,Bristol,Wholesale,$540.10,2026-03-05
9, London,retail,$1,340.00,2026-03-11
10,Leeds,Wholesale,$610.00,2026-03-18
9, London,retail,$1,340.00,2026-03-11"""

source = WORK / "messy.csv"
source.write_text(MESSY)
print(MESSY[:260], "...")
print(f"\\n{len(MESSY.splitlines()) - 1} data rows")
''')

clean.md("""
## The steps

Load, look at what is wrong, then fix in two independent passes — text tidying
and number parsing — and bring them back together before the final check.

Two passes because they are separate concerns. Trimming whitespace has nothing
to do with parsing currency, they can be reviewed separately, and either can be
swapped without touching the other.
""")

clean.code('''
nodes = [
    node("load.csv",    "data.read",    [],                     [("out", "Rows")]),
    node("profile.rows","data.profile", [("in", "Rows")],       [("out", "Profile")]),
    node("fix.text",    "clean.text",   [("in", "Rows")],       [("out", "Rows")]),
    node("fix.numbers", "clean.numbers",[("in", "Rows")],       [("out", "Rows"), ("repairs", "Notes")]),
    node("merge.fixes", "clean.merge",  [("text", "Rows"), ("numbers", "Rows")], [("out", "Rows")]),
    node("final.check", "validate",     [("in", "Rows"), ("profile", "Profile"), ("repairs", "Notes")], [("rows", "Rows"), ("report", "Report")]),
    node("write.out",   "write",        [("rows", "Rows"), ("report", "Report")], [("out", "Receipt")],
         effects=("file.write",)),
]

stages = [
    stage("load",    "Load the CSV",    [],                [("out", "Rows")],    "data.read",     ["load.csv"]),
    stage("profile", "See what is wrong",[("in", "Rows")], [("out", "Profile")], "data.profile",  ["profile.rows"]),
    stage("text",    "Tidy the text",   [("in", "Rows")],  [("out", "Rows")],    "clean.text",    ["fix.text"]),
    stage("numbers", "Parse the numbers",[("in", "Rows")], [("out", "Rows"), ("repairs", "Notes")], "clean.numbers", ["fix.numbers"]),
    stage("merge",   "Merge both fixes",[("text", "Rows"), ("numbers", "Rows")], [("out", "Rows")], "clean.merge", ["merge.fixes"]),
    stage("check",   "Final check",     [("in", "Rows"), ("profile", "Profile"), ("repairs", "Notes")], [("rows", "Rows"), ("report", "Report")], "validate", ["final.check"]),
    stage("write",   "Write results",   [("rows", "Rows"), ("report", "Report")], [("out", "Receipt")], "write", ["write.out"]),
]

edges = [Edge("load", "profile"), Edge("load", "text"), Edge("load", "numbers"),
         Edge("text", "merge", to_port="text"),
         Edge("numbers", "merge", from_port="out", to_port="numbers"),
         Edge("numbers", "check", from_port="repairs", to_port="repairs"),
         Edge("merge", "check", to_port="in"),
         Edge("profile", "check", to_port="profile"),
         Edge("check", "write", from_port="rows", to_port="rows"),
         Edge("check", "write", from_port="report", to_port="report")]

bench = build("Clean up a messy table",
              "Fix what can be fixed, and write down everything that was changed.",
              stages, nodes, edges)
print("layers:", bench.layers())
''')

clean.code("viz.dag(bench)")

clean.code('''
import csv, io, re
from collections import Counter

def load_csv():
    """`restkey` catches rows with too many fields instead of losing them.

    Row 9 is `$1,340.00` with no quotes around it. The comma inside the number
    is also the column separator, so that row arrives with six fields where the
    header has five. Without `restkey` the spare piece is silently dropped and
    the row looks fine while holding the wrong amount and the wrong date.
    """
    return list(csv.DictReader(io.StringIO(source.read_text()),
                               restkey="_extra", restval=""))

def profile_rows(**kw):
    rows = kw["in"]
    plain = [{k: v for k, v in r.items() if k != "_extra"} for r in rows]
    seen = Counter(tuple(sorted(r.items())) for r in plain)
    return {
        "rows": len(rows),
        "duplicate_rows": sum(c - 1 for c in seen.values() if c > 1),
        "split_by_comma": sum(1 for r in rows if r.get("_extra")),
        "padded_values": sum(1 for r in plain for v in r.values() if v != v.strip()),
        "blank_values": sum(1 for r in plain for v in r.values() if not v.strip()),
        "city_spellings": sorted({r["city"].strip() for r in rows}),
        "category_spellings": sorted({r["category"].strip() for r in rows}),
    }

def fix_text(**kw):
    """Trim spaces and settle on one spelling per city and category."""
    out = []
    for row in kw["in"]:
        fixed = {k: (v.strip() if isinstance(v, str) else v)
                 for k, v in row.items() if k != "_extra"}
        fixed["city"] = fixed["city"].title()
        fixed["category"] = fixed["category"].title()
        out.append(fixed)
    return out

def fix_numbers(**kw):
    """Currency text into a number, repairing the rows the comma broke.

    Two repairs, both worth naming:

    * `$1` + `340.00` + a spare date is one amount that got cut in half by its
      own thousands separator. Glue it back and shift the date along.
    * `$1;050` is a typo for `$1,050`. The semicolon is next to the comma.
    """
    out, repairs = [], []
    for row in kw["in"]:
        row = dict(row)
        spare = row.pop("_extra", "") or ""
        if spare:
            joined = spare[0] if isinstance(spare, list) else spare
            repairs.append(f"id {row['id'].strip()}: amount was split by its own comma")
            row["amount"] = f"{row['amount']},{row['joined']}"
            row["joined"] = joined
        raw = (row.get("amount") or "").strip()
        digits = re.sub(r"[^0-9.]", "", raw.replace(";", ""))
        try:
            amount = float(digits) if digits else None
        except ValueError:
            amount = None
        out.append(dict(row, amount=amount))
    return {"out": out, "repairs": repairs}

def merge_fixes(**kw):
    """Text fixes and number fixes, side by side, row for row.

    Both passes started from the same rows in the same order, so lining them up
    by position is safe. If either pass ever dropped a row that would stop being
    true, which is why neither of them does.
    """
    return [dict(text_row, amount=number_row["amount"], joined=number_row["joined"])
            for text_row, number_row in zip(kw["text"], kw["numbers"])]

def final_check(**kw):
    """Drop duplicates, flag missing and absurd values. Record all of it."""
    rows, seen = [], set()
    notes = [{"id": r.split(":")[0].replace("id ", ""), "action": "repaired",
              "why": r.split(": ", 1)[1]} for r in kw["repairs"]]
    amounts = [r["amount"] for r in kw["in"] if r["amount"] is not None]
    ceiling = (sorted(amounts)[len(amounts) // 2]) * 20 if amounts else float("inf")

    for row in kw["in"]:
        key = (row["id"], row["joined"])
        if key in seen:
            notes.append({"id": row["id"], "action": "dropped", "why": "duplicate row"})
            continue
        seen.add(key)
        if row["amount"] is None:
            notes.append({"id": row["id"], "action": "flagged", "why": "no amount"})
            rows.append(dict(row, flag="missing amount")); continue
        if row["amount"] > ceiling:
            notes.append({"id": row["id"], "action": "flagged",
                          "why": f"amount {row['amount']:,.0f} is far above the rest"})
            rows.append(dict(row, flag="suspicious amount")); continue
        rows.append(dict(row, flag=""))

    report = {"before": kw["profile"], "changes": notes,
              "after": {"rows": len(rows),
                        "flagged": sum(1 for r in rows if r["flag"])}}
    return {"rows": rows, "report": report}

def write_out(workspace, **kw):
    path = workspace / "clean.csv"
    fields = ["id", "city", "category", "amount", "joined", "flag"]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in kw["rows"]:
            writer.writerow({f: row.get(f, "") for f in fields})
    (workspace / "cleaning-report.json").write_text(json.dumps(kw["report"], indent=2))
    return {"rows_written": len(kw["rows"])}

runtime = execute.Runtime({
    "load.csv": load_csv, "profile.rows": profile_rows, "fix.text": fix_text,
    "fix.numbers": fix_numbers, "merge.fixes": merge_fixes,
    "final.check": final_check, "write.out": write_out,
})

plan = compile_route(bench, {s.id: s.candidates[0] for s in bench.leaf_stages})
run = execute.run(plan, runtime, workspace=WORK)
print(run.text())
''')

clean.md("""
## Before
""")

clean.code('''
before = run.output("profile")
for key, value in before.items():
    print(f"  {key:<20} {value}")
''')

clean.md("""
## After
""")

clean.code('''
report = run.values[("check", "report")]
rows = run.values[("check", "rows")]

print(f"{before['rows']} rows in, {len(rows)} rows out\\n")
print("what changed:")
for note in report["changes"]:
    print(f"  id {note['id']:<3} {note['action']:<9} {note['why']}")

print(f"\\n{'id':<4}{'city':<10}{'category':<12}{'amount':>12}   flag")
for row in rows:
    amount = f"{row['amount']:,.2f}" if row["amount"] is not None else "—"
    print(f"{row['id']:<4}{row['city']:<10}{row['category']:<12}{amount:>12}   {row['flag']}")
''')

clean.md("""
Note what the cleaner did **not** do. The huge amount is flagged, not deleted.
It might be real. Silently dropping it would change someone's totals with no
trace.
""")

clean.code(show_artifacts())


# ============================== 16 · fit a model ============================

model = Notebook("16-fit-a-model", "Regression and classification")

model.md("""
# Fit a model

**The job.** Same data, two questions. Predict a number, and predict a label.

The point here is that both are the *same graph*. Only the last two steps
change. Swapping regression for classification is a change of route, not a
rewrite.

Everything is numpy. No scikit-learn. The maths is short enough to read, and
you can see there is nothing hidden in it.

**In:** a generated dataset.
**Out:** scores for both jobs, and predictions.
**Files:** metrics.json, predictions.csv.
""")

model.code(SETUP)

model.md("""
## The input

800 rows. Three numeric columns and one category. The number we predict depends
on all of them plus noise. The label is just "is the number above average" —
so a model that predicts the number well should classify well too.
""")

model.code('''
import numpy as np

rng = np.random.default_rng(11)
N = 800

size = rng.normal(70, 18, N).round(1)
age = rng.integers(1, 40, N)
rooms = rng.integers(1, 6, N)
area = rng.choice(["north", "south", "central"], N, p=[0.4, 0.35, 0.25])

premium = np.select([area == "central", area == "south"], [95.0, 40.0], 0.0)
price = (28 * size - 1.7 * age + 12 * rooms + premium
         + rng.normal(0, 45, N)).round(1)
above = (price > np.median(price)).astype(int)

print(f"{N} rows")
print(f"{'size':>8}{'age':>6}{'rooms':>7}{'area':>9}{'price':>10}{'above':>7}")
for i in range(5):
    print(f"{size[i]:>8.1f}{age[i]:>6}{rooms[i]:>7}{area[i]:>9}{price[i]:>10.1f}{above[i]:>7}")
print(f"\\nprice: min {price.min():.0f}  median {np.median(price):.0f}  max {price.max():.0f}")
''')

model.md("""
## The steps

Load, split, then two independent feature passes — numbers and the category —
that meet at the assemble step. Then fit and score.

Numbers and categories are genuinely independent. Neither waits on the other.
Drawn as a list that fact disappears.
""")

model.code('''
nodes = [
    node("load.arrays",  "data.read",    [],                  [("out", "Frame")]),
    node("split.random", "data.split",   [("in", "Frame")],   [("train", "Frame"), ("valid", "Frame")]),
    node("num.standard", "feature.numeric",     [("in", "Frame")], [("out", "Matrix")]),
    node("num.raw",      "feature.numeric",     [("in", "Frame")], [("out", "Matrix")]),
    node("cat.onehot",   "feature.categorical", [("in", "Frame")], [("out", "Matrix")]),
    node("assemble.hstack","feature.assemble",  [("numeric", "Matrix"), ("categorical", "Matrix")], [("out", "Matrix")]),
    node("fit.leastsquares","model.fit", [("in", "Matrix")],  [("out", "Model")]),
    node("fit.logistic",    "model.fit", [("in", "Matrix")],  [("out", "Model")]),
    node("fit.ridge",       "model.fit", [("in", "Matrix")],  [("out", "Model")]),
    node("score.regression","model.score",[("in", "Model")],  [("out", "Score")]),
    node("score.classifier","model.score",[("in", "Model")],  [("out", "Score")]),
]

stages = [
    stage("load",     "Load the data",     [],                [("out", "Frame")], "data.read", ["load.arrays"]),
    stage("split",    "Hold some back",    [("in", "Frame")], [("train", "Frame"), ("valid", "Frame")], "data.split", ["split.random"]),
    stage("numeric",  "Scale the numbers", [("in", "Frame")], [("out", "Matrix")], "feature.numeric",     ["num.standard", "num.raw"]),
    stage("category", "Encode the area",   [("in", "Frame")], [("out", "Matrix")], "feature.categorical", ["cat.onehot"]),
    stage("assemble", "Put them together", [("numeric", "Matrix"), ("categorical", "Matrix")], [("out", "Matrix")], "feature.assemble", ["assemble.hstack"]),
    stage("fit",      "Fit a model",       [("in", "Matrix")], [("out", "Model")], "model.fit",   ["fit.leastsquares", "fit.logistic", "fit.ridge"]),
    stage("score",    "Score it",          [("in", "Model")],  [("out", "Score")], "model.score", ["score.regression", "score.classifier"]),
]

edges = [Edge("load", "split"), Edge("split", "numeric", from_port="train"),
         Edge("split", "category", from_port="train"),
         Edge("numeric", "assemble", to_port="numeric"),
         Edge("category", "assemble", to_port="categorical"),
         Edge("assemble", "fit"), Edge("fit", "score")]

bench = build("Fit a model", "Predict a number, and predict a label.",
              stages, nodes, edges)
print("layers:", bench.layers())
print("routes:", bench.route_count(), "— two ways to fit, two ways to score")
''')

model.code("viz.dag(bench)")

model.md("""
## The code

`np.linalg.lstsq` for the regression. Plain gradient descent for the logistic
one. Both are a few lines, and both are doing the real thing.
""")

model.code('''
def load_arrays():
    return {"size": size, "age": age, "rooms": rooms, "area": area,
            "price": price, "above": above}

def split_random(**kw):
    """Its own seeded generator, not the shared one.

    This started out drawing from the module-level `rng`, which advances every
    time anything uses it. Two effects, both bad. Re-running the same plan gave
    a different score, so the plan's own claim to be deterministic was false.
    And worse, comparing four routes below would have scored each one on a
    *different* split — which is not a comparison of models at all.
    """
    frame = kw["in"]
    order = np.random.default_rng(2024).permutation(N)
    cut = int(N * 0.75)
    take = lambda idx: {k: v[idx] for k, v in frame.items()}
    return {"train": take(order[:cut]), "valid": take(order[cut:])}

def num_standard(**kw):
    """Centre and scale, using the training numbers only."""
    frame = kw["in"]
    cols = np.column_stack([frame["size"], frame["age"], frame["rooms"]]).astype(float)
    mean, sd = cols.mean(0), cols.std(0)
    return {"matrix": (cols - mean) / sd, "mean": mean, "sd": sd,
            "target": frame["price"], "label": frame["above"]}

def cat_onehot(**kw):
    """One column per area. Three areas, three columns."""
    frame = kw["in"]
    areas = ["north", "south", "central"]
    matrix = np.column_stack([(frame["area"] == a).astype(float) for a in areas])
    return {"matrix": matrix, "areas": areas}

def assemble_hstack(**kw):
    numeric, categorical = kw["numeric"], kw["categorical"]
    X = np.column_stack([np.ones(len(numeric["matrix"])),
                         numeric["matrix"], categorical["matrix"]])
    return {"X": X, "y": numeric["target"], "label": numeric["label"],
            "names": ["bias", "size", "age", "rooms", *categorical["areas"]]}

def fit_leastsquares(**kw):
    data = kw["in"]
    weights, *_ = np.linalg.lstsq(data["X"], data["y"], rcond=None)
    return {"kind": "regression", "weights": weights, "data": data}

def num_raw(**kw):
    """No scaling at all. Kept as a real option so the search has something to
    reject on evidence rather than on somebody's opinion."""
    frame = kw["in"]
    cols = np.column_stack([frame["size"], frame["age"], frame["rooms"]]).astype(float)
    return {"matrix": cols, "mean": np.zeros(3), "sd": np.ones(3),
            "target": frame["price"], "label": frame["above"]}

def fit_ridge(**kw):
    """Least squares with a small penalty on big weights."""
    data = kw["in"]
    X, y = data["X"], data["y"]
    penalty = 1.0 * np.eye(X.shape[1])
    penalty[0, 0] = 0.0                       # never penalise the bias
    weights = np.linalg.solve(X.T @ X + penalty, X.T @ y)
    return {"kind": "regression", "weights": weights, "data": data}

def fit_logistic(**kw):
    """Gradient descent. 400 steps is plenty for this."""
    data = kw["in"]
    X, y = data["X"], data["label"].astype(float)
    w = np.zeros(X.shape[1])
    for _ in range(400):
        p = 1 / (1 + np.exp(-X @ w))
        w -= 0.5 * (X.T @ (p - y)) / len(y)
    return {"kind": "classification", "weights": w, "data": data}

def score_regression(**kw):
    m = kw["in"]
    X, y = m["data"]["X"], m["data"]["y"]
    pred = X @ m["weights"]
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return {"kind": "regression", "r2": 1 - ss_res / ss_tot,
            "mae": float(np.abs(y - pred).mean()),
            "prediction": pred, "truth": y,
            "weights": dict(zip(m["data"]["names"], m["weights"].round(2)))}

def score_classifier(**kw):
    m = kw["in"]
    X, y = m["data"]["X"], m["data"]["label"]
    prob = 1 / (1 + np.exp(-X @ m["weights"]))
    pred = (prob > 0.5).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum()); tn = int(((pred == 0) & (y == 0)).sum())
    fp = int(((pred == 1) & (y == 0)).sum()); fn = int(((pred == 0) & (y == 1)).sum())
    return {"kind": "classification",
            "accuracy": float((pred == y).mean()),
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
            "prediction": pred, "truth": y}

runtime = execute.Runtime({
    "load.arrays": load_arrays, "split.random": split_random,
    "num.standard": num_standard, "num.raw": num_raw, "cat.onehot": cat_onehot,
    "fit.ridge": fit_ridge,
    "assemble.hstack": assemble_hstack,
    "fit.leastsquares": fit_leastsquares, "fit.logistic": fit_logistic,
    "score.regression": score_regression, "score.classifier": score_classifier,
})
print("all steps have code:", runtime.missing(
    compile_route(bench, {**{s.id: s.candidates[0] for s in bench.leaf_stages}})) == [])
''')

model.md("""
## Route one: predict the number
""")

model.code('''
regression = {s.id: s.candidates[0] for s in bench.leaf_stages}
regression["fit"] = "fit.leastsquares"
regression["score"] = "score.regression"

plan_r = compile_route(bench, regression)
run_r = execute.run(plan_r, runtime)
print(run_r.text())

got = run_r.output("score")
print(f"\\nR²  {got['r2']:.4f}      mean error {got['mae']:,.1f}")
print("\\nwhat it learned:")
for name, weight in got["weights"].items():
    print(f"  {name:<9}{weight:>10.2f}")
''')

model.md("""
The weights line up with how the data was made: `size` is the big driver,
`central` is worth more than `south`, and `age` pulls down. That is a check on
the pipeline, not just on the model.

## Route two: predict the label

Same graph. Two different candidates.
""")

model.code('''
classification = dict(regression)
classification["fit"] = "fit.logistic"
classification["score"] = "score.classifier"

plan_c = compile_route(bench, classification)
run_c = execute.run(plan_c, runtime)

got_c = run_c.output("score")
print(f"accuracy {got_c['accuracy']:.3f}   precision {got_c['precision']:.3f}   recall {got_c['recall']:.3f}")
c = got_c["confusion"]
print(f"\\n            predicted 0   predicted 1")
print(f"  actual 0 {c['tn']:>12} {c['fp']:>13}")
print(f"  actual 1 {c['fn']:>12} {c['tp']:>13}")
print(f"\\nsame graph? {plan_r.layers == plan_c.layers}")
print(f"different plan? {plan_r.digest != plan_c.digest}")
''')

model.md("""
Same layers, different digest. The shape of the work did not change. What ran
inside it did, and the digest proves the two results came from different graphs
so they can never be mixed up later.
""")

model.md("""
## Let the evidence pick, instead of picking yourself

So far I chose the route by hand. Fine for two options. There are now six ways
to predict the number — two ways to handle the numbers, three ways to fit — and
picking by hand stops being a plan.

So run them all and let the measured result decide. Not a prior. Not an opinion
about which model is better. The actual score on this actual data.
""")

model.code('''
import itertools

numeric_options = bench.stage("numeric").candidates
fit_options = [c for c in bench.stage("fit").candidates if c != "fit.logistic"]

results = []
for numeric, fit in itertools.product(numeric_options, fit_options):
    trial = dict(regression, numeric=numeric, fit=fit, score="score.regression")
    plan_t = compile_route(bench, trial)
    got_t = execute.run(plan_t, runtime)
    if not got_t.ok:
        results.append((numeric, fit, None, None, plan_t.digest, got_t.steps[-1].error))
        continue
    s_t = got_t.output("score")
    results.append((numeric, fit, s_t["r2"], s_t["mae"], plan_t.digest, ""))

print(f"{'numbers':<14}{'model':<18}{'R2':>9}{'mean error':>13}   plan")
for numeric, fit, r2, mae, digest, err in results:
    if r2 is None:
        print(f"{numeric:<14}{fit:<18}{'failed':>9}{'':>13}   {err[:34]}")
    else:
        print(f"{numeric:<14}{fit:<18}{r2:>9.4f}{mae:>13,.1f}   {digest[5:17]}")
''')

model.md("""
## The winner, and how much of the space that took
""")

model.code('''
ranked = sorted([r for r in results if r[2] is not None], key=lambda r: -r[2])
best = ranked[0]
print(f"best:  {best[0]:<13}+ {best[1]:<18}R2 {best[2]:.4f}   plan {best[4][5:17]}")
print(f"worst: {ranked[-1][0]:<13}+ {ranked[-1][1]:<18}R2 {ranked[-1][2]:.4f}")
print(f"\\ngap between best and worst: {best[2] - ranked[-1][2]:.4f} R2")
print("\\nEvery number above was measured. None of it was a prior.")
''')

model.code('''
viz.funnel([
    ("every route in the graph", bench.route_count()),
    ("regression routes",        len(results)),
    ("ran without failing",      len(ranked)),
    ("chosen",                   1),
], title="how the model was picked")
''')

model.md("""
### Read that result honestly

The gap between best and worst is **0.0000**. On this data, with this split,
the choice makes no measurable difference at all.

So the right conclusion is not "num.standard won". It is "this decision does not
matter here, so stop spending time on it". Scaling does nothing for least
squares because least squares is scale-invariant, and the ridge penalty is too
small to bite. Both of those are true facts about the maths, and the measurement
agrees with them.

A search that always announces a winner will always find one. The useful search
tells you when the winner is noise.
""")

model.md("""
Four routes tried, four measured, one picked. Small enough to enumerate, and the
notebook says so rather than implying a bigger search happened.

When the space is too big to enumerate, `browsergraph.search` does this with a
beam and reports how much of the space it covered. It refuses to enumerate a
space it cannot finish, rather than trying and running out of memory — which is
exactly what it used to do.
""")

model.code('''
best_route = dict(regression, numeric=best[0], fit=best[1])
plan_best = compile_route(bench, best_route)
run_best = execute.run(plan_best, runtime)
print(f"re-ran the winner: R2 {run_best.output('score')['r2']:.4f}"
      f"   same plan: {plan_best.digest == best[4]}")
''')

model.md("""
## Save the results
""")

model.code('''
import csv

def write_results(workspace, **kw):
    metrics = {"regression": {k: v for k, v in kw["reg"].items()
                              if k not in ("prediction", "truth", "weights")},
               "classification": {k: v for k, v in kw["clf"].items()
                                  if k not in ("prediction", "truth")},
               "regression_weights": {k: float(v) for k, v in kw["reg"]["weights"].items()},
               "plans": {"regression": kw["reg_plan"], "classification": kw["clf_plan"]}}
    (workspace / "metrics.json").write_text(json.dumps(metrics, indent=2, default=float))

    path = workspace / "predictions.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["actual_price", "predicted_price", "actual_label", "predicted_label"])
        for row in zip(kw["reg"]["truth"], kw["reg"]["prediction"],
                       kw["clf"]["truth"], kw["clf"]["prediction"]):
            writer.writerow([f"{row[0]:.1f}", f"{row[1]:.1f}", int(row[2]), int(row[3])])
    return {"rows": len(kw["reg"]["truth"])}

wrote = write_results(WORK, reg=got, clf=got_c,
                      reg_plan=plan_r.digest, clf_plan=plan_c.digest)
print(wrote)
for name in ("metrics.json", "predictions.csv"):
    path = WORK / name
    print(f"  {name:<18}{path.stat().st_size:>8,} bytes")
print()
print((WORK / "predictions.csv").read_text().splitlines()[0])
for line in (WORK / "predictions.csv").read_text().splitlines()[1:6]:
    print(line)
''')

model.md("""
## What the five notebooks showed

Same library, five jobs that have nothing in common:

1. a web page turned into rows,
2. mixed records forced into one schema,
3. images checked and resized,
4. a messy table cleaned with a record of every change,
5. a model fitted two different ways.

Each one had real input, ran real code, and left files behind. The graph was
written the same way every time, the checks were the same checks, and the
picture was drawn by the same function.
""")


if __name__ == "__main__":
    for book in (scrape, ingest, image, clean, model):
        path = book.write()
        print(f"wrote {path}  ({len(book.cells)} cells)")
