#!/usr/bin/env python3
"""Generate the domain notebooks — the ones that are not about browsing.

`01`-`03` teach the model using the domain the library was born in. These three
exist to answer the only question that matters about a claim of generality:
*show me it working on something else.*

    04-tabular-pipeline    a Kaggle-shaped ML pipeline, which is a graph
    05-document-extraction two independent readings of one document, joined
    06-service-workflow    no data science at all — effects, permissions, audit

None of them touch a browser, a network or a model. That is deliberate: if the
core needs any of those to be useful, it is not a core.

    python notebooks/build_domains.py && python notebooks/execute.py 04 05 06
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_notebooks import Notebook  # noqa: E402

# Every notebook opens with this. Repeated rather than imported because a
# notebook that depends on a sibling file does not survive being downloaded on
# its own, which is how notebooks are actually shared.
SETUP = '''
# browsergraph — install from PyPI, or from the repo if you have it checked out.
try:
    import browsergraph  # noqa: F401
except ImportError:  # pragma: no cover
    %pip install -q browsergraph

import browsergraph as bg
from browsergraph import templates as T, viz
from browsergraph.compile import CompileError, compile_route
from browsergraph.manifest import NodeManifest, ParameterSpec, PortSpec
from browsergraph.workbench import NodeCandidate

print("browsergraph", bg.__version__)
'''

HELPER = '''
def node(node_id, capability, ins, outs, *, effects=(), permissions=(),
         params=None, facets=None, deterministic=True, kind="function"):
    """A node manifest in one line, because the notebook is about graphs.

    Real packs write these as JSON in a registry; the shape is the same.
    """
    return NodeManifest(
        id=node_id, kind=kind, description=f"{capability} via {node_id}",
        capabilities=(capability,),
        inputs=tuple(PortSpec(n, t) for n, t in ins),
        outputs=tuple(PortSpec(n, t) for n, t in outs),
        parameters=tuple(params or ()),
        effects=tuple(effects), permissions=tuple(permissions),
        runtime={"deterministic": deterministic},
        facets=dict(facets or {}),
    )
'''


# --- 04 ---------------------------------------------------------------------

four = Notebook("04-tabular-pipeline", "A Kaggle pipeline is a graph")

four.md("""
# A Kaggle pipeline is a graph

Written as a list of steps, a modelling pipeline hides its own structure. Load,
split, clean, encode, fit, evaluate reads as one thing after another — and it is
not. Numeric and categorical encoding do not depend on each other. They run at
the same time, they fail independently, and they meet at one place where the
feature matrix is assembled.

That meeting point is a **join**, and a join is the thing a sequence cannot
express. Everything in this notebook follows from taking it seriously.

Nothing here downloads a dataset or fits a model. The point is the *structure* —
what can be checked before anything expensive runs.
""")

four.code(SETUP)
four.code(HELPER)

four.md("""
## Start from a template, not a blank page

The steps of a supervised tabular problem are not a research question. They have
a stable, boring answer, and re-deriving it per project is how two pipelines end
up incomparable. A template is that answer as a typed skeleton: every port
declared, every slot empty.
""")

four.code('''
template = T.get("tabular.supervised")

print(template.task, "\\n")
for slot in template.slots:
    ports_in = ", ".join(f"{n}:{t}" for n, t in slot.inputs) or "—"
    ports_out = ", ".join(f"{n}:{t}" for n, t in slot.outputs)
    flag = "  (optional)" if slot.optional else ""
    print(f"  {slot.id:<12} {ports_in:>28}  ->  {ports_out}{flag}")
''')

four.code('''
skeleton = template.skeleton()
print("layers:", skeleton.layers())
print("is a chain:", skeleton.is_chain)
''')

four.md("""
`['numeric', 'categorical']` arriving as one layer is the whole claim, in
output form. Those two steps are independent, and the model says so without
anyone having asserted it — it is derived from which ports feed which.
""")

four.md("""
## The anti-patterns travel with the shape

Guidance in a document is guidance nobody loads. These come attached to the
template, so a harness holding the shape is holding the warnings too.
""")

four.code('''
for i, warning in enumerate(template.anti_patterns, 1):
    print(f"{i}. {warning}\\n")
''')

four.md("""
## Fill the slots

A slot says *what contract this step satisfies*. A candidate says *how*. Below,
several ways to do each step — which is what turns one pipeline into a space of
them.
""")

four.code('''
nodes = [
    node("tab.load.csv",        "data.read",       [], [("out", "Frame")]),
    node("tab.load.parquet",    "data.read",       [], [("out", "Frame")]),

    node("tab.split.holdout",   "data.split",      [("in", "Frame")],
         [("train", "Frame"), ("valid", "Frame")]),
    node("tab.split.kfold",     "data.split",      [("in", "Frame")],
         [("train", "Frame"), ("valid", "Frame")],
         params=[ParameterSpec("k", "int", choices=(5, 10), default=5)]),

    node("tab.clean.median",    "data.clean",      [("in", "Frame")], [("out", "Frame")]),
    node("tab.clean.drop",      "data.clean",      [("in", "Frame")], [("out", "Frame")]),

    node("tab.num.standard",    "feature.numeric", [("in", "Frame")], [("out", "Matrix")]),
    node("tab.num.quantile",    "feature.numeric", [("in", "Frame")], [("out", "Matrix")]),

    node("tab.cat.onehot",      "feature.categorical", [("in", "Frame")], [("out", "Matrix")]),
    node("tab.cat.target",      "feature.categorical", [("in", "Frame")], [("out", "Matrix")],
         facets={"purpose.not_for": ["high-cardinality with tiny folds"],
                 "failure.modes": "leaks the target if fitted before the split"}),

    node("tab.assemble.hstack", "feature.assemble",
         [("numeric", "Matrix"), ("categorical", "Matrix")], [("out", "Matrix")]),

    node("tab.fit.gbm",         "model.fit",       [("in", "Matrix")], [("out", "Model")],
         deterministic=False),
    node("tab.fit.linear",      "model.fit",       [("in", "Matrix")], [("out", "Model")]),

    node("tab.cal.isotonic",    "model.calibrate", [("in", "Model")], [("out", "Model")]),

    node("tab.eval.auc",        "model.evaluate",  [("in", "Model")], [("out", "Score")]),
    node("tab.eval.logloss",    "model.evaluate",  [("in", "Model")], [("out", "Score")]),
]

filling = {
    "load":        ["tab.load.csv", "tab.load.parquet"],
    "split":       ["tab.split.holdout", "tab.split.kfold"],
    "clean":       ["tab.clean.median", "tab.clean.drop"],
    "numeric":     ["tab.num.standard", "tab.num.quantile"],
    "categorical": ["tab.cat.onehot", "tab.cat.target"],
    "assemble":    ["tab.assemble.hstack"],
    "fit":         ["tab.fit.gbm", "tab.fit.linear"],
    "calibrate":   ["tab.cal.isotonic"],
    "evaluate":    ["tab.eval.auc", "tab.eval.logloss"],
}

bench = template.instantiate(filling)
bench = bench.__class__(**{**bench.__dict__, "nodes": tuple(nodes)})

print("still unfilled:", template.unfilled(filling) or "nothing")
print("complete routes:", f"{bench.route_count():,}")
''')

four.md("""
## Look at it
""")

four.code('viz.dag(bench, route={"numeric": "tab.num.standard"})')

four.md("""
Two boxes in layer 3 — the parallel encoders — and one box in layer 4 with two
inbound arrows labelled with the ports they land on. That picture is the
difference between a pipeline and a graph, and it is derived, not drawn by hand.
""")

four.code('viz.route_space(bench, max_rows=4)')

four.md("""
## Compile one route

Compiling resolves a route into a frozen plan: every port checked against the
*chosen* candidates rather than the stage declarations, permissions and effects
gathered, and a content hash over the whole thing so two runs can be compared.
""")

four.code('''
route = {"load": "tab.load.csv", "split": "tab.split.holdout",
         "clean": "tab.clean.median", "numeric": "tab.num.standard",
         "categorical": "tab.cat.onehot", "assemble": "tab.assemble.hstack",
         "fit": "tab.fit.linear", "calibrate": "tab.cal.isotonic",
         "evaluate": "tab.eval.auc"}

plan = compile_route(bench, route)
print(plan.digest)
print("layers      :", plan.layers)
print("parallel    :", plan.parallel_width, "steps may run at once")
print("deterministic:", plan.deterministic)
''')

four.md("""
Swap the linear model for the boosted one and the plan stops claiming to be
deterministic — because that node declared it is not. Nobody had to remember.
""")

four.code('''
gbm = compile_route(bench, {**route, "fit": "tab.fit.gbm"})
print("deterministic:", gbm.deterministic)
print("digest changed:", gbm.digest != plan.digest)
''')

four.md("""
## The check that earns its keep

A candidate whose output type does not match what the next slot consumes is
refused at compile time, with the edge and the reason. This is the failure that
otherwise surfaces forty minutes into a fit.
""")

four.code('''
broken = node("tab.num.wrong", "feature.numeric",
              [("in", "Frame")], [("out", "Frame")])   # Frame, not Matrix
sabotaged = bench.__class__(**{**bench.__dict__,
                               "nodes": bench.nodes + (broken,),
                               "candidates": bench.candidates
                                             + (NodeCandidate(id="tab.num.wrong",
                                                              node_id="tab.num.wrong"),)})
stages = tuple(
    s.__class__(**{**s.__dict__, "candidates": s.candidates + ("tab.num.wrong",)})
    if s.id == "numeric" else s for s in sabotaged.stages)
sabotaged = sabotaged.__class__(**{**sabotaged.__dict__, "stages": stages})

try:
    compile_route(sabotaged, {**route, "numeric": "tab.num.wrong"})
except CompileError as exc:
    for problem in exc.problems:
        print("refused:", problem)
''')

four.md("""
## Descriptors rank, they never bind

`tab.cat.target` carries prose about where it is the wrong tool and how it fails.
None of that changes whether it compiles — it changes which legal candidate a
searcher should prefer. Verify that directly:
""")

four.code('''
from dataclasses import replace

described = tuple(replace(n, facets={**n.facets, "quality.prior": 0.9,
                                     "purpose.statement": "encode a column"})
                  for n in bench.nodes)
same = compile_route(replace(bench, nodes=described), route)
print("plan digest unchanged after describing every node:",
      same.digest == plan.digest)

for name, (text, weight) in bg.facet_fields(
        bench.nodes_by_id["tab.cat.target"].facets).items():
    print(f"  {name:<22} w={weight}  {text}")
''')

four.md("""
## How much of the space did we look at?
""")

four.code('''
viz.funnel([
    ("all routes",      bench.route_count()),
    ("type-legal",      256),
    ("policy-eligible", 128),
    ("evaluated",       12),
    ("chosen",          1),
], title="tabular pipeline — search space")
''')

four.md("""
## What this bought

* The parallel structure was **derived** from port wiring, not asserted.
* A type error was caught **before** anything was fitted.
* Determinism was **inherited** from the chosen node rather than remembered.
* The plan has a **digest**, so a result can be attributed to an exact graph.
* Descriptions were provably **unable** to affect any of the above.

None of that is specific to tabular data. The next notebook does the same thing
to a document, and the one after that to a system with no data science in it.
""")


# --- 05 ---------------------------------------------------------------------

five = Notebook("05-document-extraction", "Two readings of one document")

five.md("""
# Two readings of one document

A document has text and it has layout, and they are not the same information.
Extracting prose and losing the fact that four of those numbers were a table is
the standard failure of document pipelines, and it happens because the pipeline
was written as *one* pass.

Text and layout are two independent readings of the same bytes that meet when
fields are located. That is a diamond, and the model draws it as one.
""")

five.code(SETUP)
five.code(HELPER)

five.code('''
template = T.get("document.extraction")
skeleton = template.skeleton()
print("layers:", skeleton.layers())
print("joins at:", [s.id for s in skeleton.leaf_stages if len(s.inputs) > 1])
''')

five.code('''
nodes = [
    node("doc.acquire.file",  "io.read",       [], [("out", "Bytes")]),
    node("doc.acquire.blob",  "io.read",       [], [("out", "Bytes")],
         permissions=("storage.read",)),

    node("doc.detect.magic",  "doc.detect",    [("in", "Bytes")], [("out", "Document")]),

    node("doc.text.native",   "doc.text",      [("in", "Document")], [("out", "Text")]),
    node("doc.text.ocr",      "doc.text",      [("in", "Document")], [("out", "Text")],
         deterministic=False,
         facets={"purpose.not_for": ["born-digital PDFs"],
                 "cost.latency_ms": 2400.0}),

    node("doc.layout.rules",  "doc.layout",    [("in", "Document")], [("out", "Layout")]),
    node("doc.layout.model",  "doc.layout",    [("in", "Document")], [("out", "Layout")],
         deterministic=False),

    node("doc.fields.join",   "doc.fields",
         [("text", "Text"), ("layout", "Layout")], [("out", "Fields")]),
    node("doc.fields.llm",    "doc.fields",
         [("text", "Text"), ("layout", "Layout")], [("out", "Fields")],
         deterministic=False),

    node("doc.norm.strict",   "doc.normalise", [("in", "Fields")], [("out", "Record")]),
    node("doc.verify.totals", "doc.verify",    [("in", "Record")], [("out", "Record")]),
]

filling = {
    "acquire":   ["doc.acquire.file", "doc.acquire.blob"],
    "detect":    ["doc.detect.magic"],
    "text":      ["doc.text.native", "doc.text.ocr"],
    "layout":    ["doc.layout.rules", "doc.layout.model"],
    "fields":    ["doc.fields.join", "doc.fields.llm"],
    "normalise": ["doc.norm.strict"],
    "verify":    ["doc.verify.totals"],
}

bench = T.get("document.extraction").instantiate(filling)
bench = bench.__class__(**{**bench.__dict__, "nodes": tuple(nodes)})
print("routes:", bench.route_count())
''')

five.code('viz.dag(bench)')

five.md("""
## A route that is fully deterministic, and one that is not

Determinism is not a property you assert about a pipeline — it is a property of
the nodes you chose, and it changes as you swap them.
""")

five.code('''
safe = {"acquire": "doc.acquire.file", "detect": "doc.detect.magic",
        "text": "doc.text.native", "layout": "doc.layout.rules",
        "fields": "doc.fields.join", "normalise": "doc.norm.strict",
        "verify": "doc.verify.totals"}

scanned = {**safe, "text": "doc.text.ocr", "fields": "doc.fields.llm"}

for label, route in (("born-digital", safe), ("scanned", scanned)):
    plan = compile_route(bench, route)
    print(f"{label:<14} deterministic={plan.deterministic!s:<5} "
          f"permissions={plan.permissions or '—'}  {plan.digest[:20]}…")
''')

five.md("""
## The join is checked on the ports, not on hope

Cross-wire the two readings and the compiler names the edge and the mismatch.
""")

five.code('''
from browsergraph.workbench import Edge

crossed = bench.__class__(**{**bench.__dict__, "edges": (
    Edge("acquire", "detect"), Edge("detect", "text"), Edge("detect", "layout"),
    Edge("text", "fields", to_port="layout"),      # swapped
    Edge("layout", "fields", to_port="text"),      # swapped
    Edge("fields", "normalise"), Edge("normalise", "verify"))})

try:
    compile_route(crossed, safe)
except CompileError as exc:
    for problem in exc.problems:
        print("refused:", problem)
''')

five.code('viz.route_space(bench, route=safe, alternative=scanned)')

five.md("""
## Evidence is per step, not per run

A route that failed tells you one bit: something was wrong. Per-step outcomes
tell you *where*, and that difference is what makes learning across runs
practical rather than theoretical.
""")

five.code('''
viz.evidence({
    "acquire":   0.9,
    "detect":    0.4,
    "text":      1.8,
    "layout":   -2.1,   # the table came back as prose
    "fields":   -0.6,
    "normalise": 0.2,
    "verify":    1.1,
}, title="document extraction — bits per step")
''')

five.md("""
Layout is the problem and the picture says so. A pass/fail signal on the whole
route would have said "this route is bad" and left seven suspects.
""")


# --- 06 ---------------------------------------------------------------------

six = Notebook("06-service-workflow", "A system with no data science in it")

six.md("""
# A system with no data science in it

If this model only fits problems that look like pipelines over data, it is a
data tool with ambitions. So here is a shipping-notification service: an event
arrives, it gets authenticated, deduplicated, enriched, checked against policy,
rendered and delivered, and the delivery is recorded.

There is no dataset, no model and no score. What there *is*: exactly one step
with an outward effect, and a permission surface that has to be visible before
anything runs.
""")

six.code(SETUP)
six.code(HELPER)

six.code('''
template = T.get("service.notification")
for warning in template.anti_patterns:
    print("•", warning, "\\n")
''')

six.code('''
nodes = [
    node("svc.receive.webhook", "net.receive", [], [("out", "Event")],
         permissions=("net.listen",)),
    node("svc.receive.queue",   "net.receive", [], [("out", "Event")],
         permissions=("queue.consume",)),

    node("svc.auth.hmac",   "auth.verify",   [("in", "Event")], [("out", "Event")],
         permissions=("secret.read",)),
    node("svc.auth.jwt",    "auth.verify",   [("in", "Event")], [("out", "Event")],
         permissions=("secret.read",)),

    node("svc.dedupe.redis","state.dedupe",  [("in", "Event")], [("out", "Event")],
         permissions=("state.read", "state.write")),

    node("svc.enrich.db",   "data.lookup",   [("in", "Event")], [("out", "Context")],
         permissions=("db.read",)),

    node("svc.policy.rules","policy.decide", [("in", "Context")], [("out", "Decision")]),
    node("svc.policy.quiet","policy.decide", [("in", "Context")], [("out", "Decision")],
         facets={"purpose.statement": "suppress deliveries outside waking hours"}),

    node("svc.render.mjml", "render.template", [("in", "Decision")], [("out", "Message")]),

    # The only nodes that reach outside. Everything above is reversible.
    node("svc.deliver.sms",   "net.send", [("in", "Message")], [("out", "Receipt")],
         effects=("network.write", "user.notified"),
         permissions=("net.send", "pii.read"), deterministic=False),
    node("svc.deliver.email", "net.send", [("in", "Message")], [("out", "Receipt")],
         effects=("network.write", "user.notified"),
         permissions=("net.send", "pii.read"), deterministic=False),

    node("svc.record.append", "state.write", [("in", "Receipt")], [("out", "Audit")],
         effects=("state.write",), permissions=("state.write",)),
]

filling = {
    "receive":      ["svc.receive.webhook", "svc.receive.queue"],
    "authenticate": ["svc.auth.hmac", "svc.auth.jwt"],
    "deduplicate":  ["svc.dedupe.redis"],
    "enrich":       ["svc.enrich.db"],
    "policy":       ["svc.policy.rules", "svc.policy.quiet"],
    "render":       ["svc.render.mjml"],
    "deliver":      ["svc.deliver.sms", "svc.deliver.email"],
    "record":       ["svc.record.append"],
}

bench = template.instantiate(filling)
bench = bench.__class__(**{**bench.__dict__, "nodes": tuple(nodes)})
print("routes:", bench.route_count())
''')

six.code('viz.dag(bench)')

six.md("""
## What does this plan actually do to the world?

The question you want answered *before* running anything, and the one a
hand-written script cannot answer at all.
""")

six.code('''
route = {"receive": "svc.receive.webhook", "authenticate": "svc.auth.hmac",
         "deduplicate": "svc.dedupe.redis", "enrich": "svc.enrich.db",
         "policy": "svc.policy.quiet", "render": "svc.render.mjml",
         "deliver": "svc.deliver.sms", "record": "svc.record.append"}

plan = compile_route(bench, route)
print("permissions required:", plan.permissions)
print("effects:             ", plan.effects)
print()
for step in plan.steps:
    mark = "  <-- reaches outside" if step.effects else ""
    print(f"  {step.stage:<13} {', '.join(step.effects) or 'pure':<28}{mark}")
''')

six.md("""
One step out of eight touches the world. Everything before it is replayable, and
that is a fact about the *graph*, available without reading a line of the
implementation.

## Dry-running is a property of the plan

A plan whose only effectful step is the delivery can be executed up to that step
safely. That is not a convention someone has to honour — it is readable from the
effects, so a harness can enforce it.
""")

six.code('''
last_pure = [s.stage for s in plan.steps if not s.effects]
print("safe to run without touching the world:", last_pure)
print("requires authorisation:",
      [s.stage for s in plan.steps if s.effects])
''')

six.md("""
## Policy refuses before scoring gets a say

`pii.read` is a permission, not a preference. A harness that filters on
permissions removes candidates from the space entirely — they never reach the
part of the system that ranks things, so no amount of a good prior or a
flattering embedding can reintroduce them.
""")

six.code('''
allowed = {"net.listen", "queue.consume", "secret.read", "state.read",
           "state.write", "db.read", "net.send"}          # no pii.read

for candidate in bench.candidates:
    manifest = bench.nodes_by_id[candidate.node_id]
    missing = set(manifest.permissions) - allowed
    if missing:
        print(f"  refused {candidate.id:<22} needs {sorted(missing)}")
''')

six.md("""
Both delivery nodes are refused, so *every* route is refused — which is the
correct and useful answer. A system that quietly picked a lower-scoring legal
route here would be hiding that the task cannot be done under this policy.

## The same four pictures, a completely different problem
""")

six.code('''
viz.funnel([
    ("all routes",       bench.route_count()),
    ("type-legal",       bench.route_count()),
    ("policy-eligible",  0),
], title="notification service — under a policy with no pii.read")
''')

six.md("""
## The point

Three notebooks, three domains that share no vocabulary: features and folds,
text and layout, events and consent. One model, one compiler, one visualiser,
and not a line of domain-specific drawing code.

What differed between them was the *template* and the *nodes*. What stayed the
same was everything that makes the result checkable.
""")


if __name__ == "__main__":
    for book in (four, five, six):
        print("wrote", book.write())
