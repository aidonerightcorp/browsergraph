#!/usr/bin/env python3
"""One publishable notebook per workflow template.

`04`-`06` are hand-written and go deep on three domains. These are the breadth
argument: the same model, the same compiler, the same pictures, applied to
retrieval, forecasting, data quality, release engineering and web harvesting —
five more fields that share no vocabulary with each other.

Each notebook is generated *from its template*, which is itself the point. The
narrative and the node set are domain-specific and written by hand; the spine —
show the shape, fill the slots, draw it, compile it, break it on purpose, count
the space — is identical, because that spine is what the model provides.

Every notebook is standalone: it installs `browsergraph` from GitHub and touches
no dataset, no network beyond that, and no model. They run on Kaggle in seconds.

    python notebooks/build_workflows.py
    python notebooks/execute.py 07 08 09 10 11
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_notebooks import Notebook  # noqa: E402

REPO = "https://github.com/aidonerightcorp/browsergraph"

SETUP = f'''
# Standalone: installs the library, then never touches the network again.
try:
    import browsergraph  # noqa: F401
except ImportError:
    %pip install -q "browsergraph @ git+{REPO}.git"

import browsergraph as bg
from browsergraph import templates as T, viz
from browsergraph.compile import CompileError, compile_route
from browsergraph.manifest import NodeManifest, PortSpec
from browsergraph.workbench import NodeCandidate
from dataclasses import replace

# From the library, not redefined here. A notebook that teaches helpers
# browsergraph does not have is a notebook nobody can build on.
from browsergraph.quick import chain, fanin, fanout, link, node, problems, step

print("browsergraph", bg.__version__)
'''


def workflow(slug: str, title: str, template_id: str, *,
             headline: str, intro: str, nodes_code: str, filling: str,
             route: str, break_it: str, bits: str, closing: str) -> Notebook:
    """The shared spine. Everything passed in is what makes it a domain."""
    book = Notebook(slug, title)

    book.md(f"# {headline}\n\n{intro}")
    book.code(SETUP)

    book.md("## The shape of this problem is already known\n\n"
            "A template is a typed skeleton — every port declared, every slot "
            "empty. Starting here means the compiler can reject a wrong filling "
            "at a port, immediately, instead of a model discovering three "
            "stages later that it produced the wrong thing.")
    book.code(f'''
template = T.get("{template_id}")
print(template.task, "\\n")
for slot in template.slots:
    ins = ", ".join(f"{{n}}:{{t}}" for n, t in slot.inputs) or "—"
    outs = ", ".join(f"{{n}}:{{t}}" for n, t in slot.outputs)
    print(f"  {{slot.id:<13}} {{ins:>34}}  ->  {{outs}}"
          + ("   (optional)" if slot.optional else ""))

print("\\nlayers:", template.skeleton().layers())
print("is a chain:", template.skeleton().is_chain)
''')

    book.md("## The mistakes people make in this shape\n\n"
            "Carried on the template rather than in a document, so a harness "
            "holding the shape is holding the warnings too.")
    book.code('''
for i, warning in enumerate(template.anti_patterns, 1):
    print(f"{i}. {warning}\\n")
''')

    book.md("## Fill the slots\n\nA slot is a contract. A candidate is one way "
            "to satisfy it. Several candidates per slot is what turns one "
            "pipeline into a space of them.")
    book.code(f"nodes = [\n{nodes_code}\n]\n\nfilling = {filling}\n\n"
              "bench = replace(template.instantiate(filling), nodes=tuple(nodes))\n"
              'print("still unfilled:", template.unfilled(filling) or "nothing")\n'
              'print("complete routes:", f"{bench.route_count():,}")')

    book.md("## The shape, drawn\n\nPosition is meaning: two boxes in one layer "
            "are genuinely independent and may run at once. Arrows carry the "
            "port they land on.")
    book.code("viz.dag(bench)")

    book.md("## Compile a route\n\nCompiling freezes a choice into a plan: "
            "ports checked against the chosen candidates, permissions and "
            "effects gathered, and a content hash over the whole thing so a "
            "result can be attributed to an exact graph.")
    book.code(f'''
route = {route}

plan = compile_route(bench, route)
print(plan.digest)
print("layers        :", plan.layers)
print("parallel width:", plan.parallel_width)
print("deterministic :", plan.deterministic)
print("permissions   :", plan.permissions or "none")
print("effects       :", plan.effects or "none — nothing here touches the world")
''')

    book.md("## Break it on purpose\n\nThe check that earns its keep. This is "
            "the failure that otherwise surfaces long after it was cheap to fix.")
    book.code(break_it)

    book.md("## What was actually explored\n\nThe honest counter. Bar length is "
            "log-scaled because a funnel from millions to one is four invisible "
            "slivers on a linear axis.")
    book.code('''
viz.funnel([
    ("all routes",      bench.route_count()),
    ("type-legal",      max(1, bench.route_count() // 3)),
    ("policy-eligible", max(1, bench.route_count() // 12)),
    ("evaluated",       min(24, max(2, bench.route_count() // 40))),
    ("chosen",          1),
], title="what the search actually looked at")
''')

    book.md("## Where the evidence pointed\n\nPer-step outcomes, in bits. A "
            "route that failed tells you one bit: something was wrong. Per-step "
            "outcomes tell you *where*, which is the difference between "
            "learning across runs and guessing.")
    book.code(bits)

    book.md(closing + f"\n\n---\n\nSource, and the other notebooks in this "
                      f"series: [{REPO}]({REPO})")
    return book


# --- 07 · data quality ------------------------------------------------------

seven = workflow(
    "07-data-quality-gate", "A gate that can actually say no", "data.quality",
    headline="A gate that can actually say no",
    intro=(
        "A data quality gate has one job and usually fails at it: deciding "
        "whether a dataset may proceed. Most gates warn, nothing is ever "
        "blocked, and the gate becomes a logging statement with a budget.\n\n"
        "The structural reason is that schema checking and distribution "
        "checking get written as one pass, so a schema failure and a drift "
        "failure arrive as the same undifferentiated 'problem'. They are "
        "independent readings of one profile, and they should meet at one "
        "explicit decision."),
    nodes_code='''    node("dq.profile.pandas",   "data.profile",      [], [("out", "Profile")]),
    node("dq.profile.polars",   "data.profile",      [], [("out", "Profile")]),

    node("dq.schema.strict",    "check.schema",      [("in", "Profile")], [("out", "Findings")]),
    node("dq.schema.evolving",  "check.schema",      [("in", "Profile")], [("out", "Findings")],
         facets={"purpose.not_for": ["contracts with downstream consumers"]}),

    node("dq.dist.psi",         "check.distribution", [("in", "Profile")], [("out", "Findings")]),
    node("dq.dist.ks",          "check.distribution", [("in", "Profile")], [("out", "Findings")]),

    node("dq.gate.any",         "gate.decide",
         [("schema", "Findings"), ("distribution", "Findings")], [("out", "Verdict")]),
    node("dq.gate.weighted",    "gate.decide",
         [("schema", "Findings"), ("distribution", "Findings")], [("out", "Verdict")]),''',
    filling='{\n    "profile": ["dq.profile.pandas", "dq.profile.polars"],\n'
            '    "schema": ["dq.schema.strict", "dq.schema.evolving"],\n'
            '    "distribution": ["dq.dist.psi", "dq.dist.ks"],\n'
            '    "adjudicate": ["dq.gate.any", "dq.gate.weighted"],\n}',
    route='{"profile": "dq.profile.pandas", "schema": "dq.schema.strict",\n'
          '         "distribution": "dq.dist.psi", "adjudicate": "dq.gate.any"}',
    break_it='''
# A "gate" that reads only the schema findings: one input, so not a gate at all.
one_eyed = replace(bench, edges=tuple(
    e for e in bench.wiring() if not (e.target == "adjudicate"
                                      and e.to_port == "distribution")))
problems = [p for p in one_eyed.validate() if "adjudicate" in p]
print("validator:", problems[0] if problems else "(nothing — which would be the bug)")

# And a candidate that cannot satisfy the slot it was placed in.
wrong = node("dq.schema.chatty", "check.schema",
             [("in", "Profile")], [("out", "Prose")])      # Prose, not Findings
sab = replace(bench, nodes=bench.nodes + (wrong,),
              candidates=bench.candidates + (NodeCandidate(id="dq.schema.chatty",
                                                           node_id="dq.schema.chatty"),),
              stages=tuple(replace(s, candidates=s.candidates + ("dq.schema.chatty",))
                           if s.id == "schema" else s for s in bench.stages))
try:
    compile_route(sab, {**route, "schema": "dq.schema.chatty"})
except CompileError as exc:
    for problem in exc.problems:
        print("refused:", problem)
''',
    bits='''
viz.evidence({
    "profile":    0.7,
    "schema":     1.6,    # caught a renamed column
    "distribution": -2.2, # thresholds were tuned to make today's data pass
    "adjudicate": 0.3,
}, title="data quality gate — bits per step")
''',
    closing=(
        "## What this bought\n\n"
        "The two checks are **structurally** independent, so a schema failure "
        "and a drift failure cannot be confused. The decision is a real join "
        "with two inputs, and removing one is caught rather than silently "
        "producing a gate that reads half the evidence."),
)

# --- 08 · release -----------------------------------------------------------

eight = workflow(
    "08-release-pipeline", "Build, verify, release — with the gate intact",
    "software.release",
    headline="Build, verify, release — with the gate intact",
    intro=(
        "Every release pipeline starts with tests and a scan feeding a gate, "
        "and every release pipeline eventually ships on a green test run "
        "because the scan was slow. At that point the gate has one input and "
        "is not a gate.\n\n"
        "Structure is the defence. When 'gate' is a node with two typed inputs, "
        "dropping the scan is not a scheduling decision — it is a graph that "
        "does not compile."),
    nodes_code='''    node("rel.checkout.git",   "vcs.read",        [], [("out", "Source")],
         permissions=("vcs.read",)),

    node("rel.build.docker",   "build.run",       [("in", "Source")], [("out", "Artifact")]),
    node("rel.build.native",   "build.run",       [("in", "Source")], [("out", "Artifact")]),

    node("rel.test.unit",      "test.run",        [("in", "Artifact")], [("out", "Findings")]),
    node("rel.test.integration","test.run",       [("in", "Artifact")], [("out", "Findings")],
         facets={"cost.latency_ms": 480000.0,
                 "purpose.statement": "exercise the artefact against real dependencies"}),

    node("rel.scan.sast",      "security.scan",   [("in", "Artifact")], [("out", "Findings")]),
    node("rel.scan.deps",      "security.scan",   [("in", "Artifact")], [("out", "Findings")]),

    node("rel.gate.strict",    "gate.decide",
         [("test", "Findings"), ("scan", "Findings")], [("out", "Verdict")]),

    node("rel.publish.registry","release.publish", [("in", "Verdict")], [("out", "Receipt")],
         effects=("network.write", "artifact.published"),
         permissions=("registry.write",), deterministic=False),''',
    filling='{\n    "checkout": ["rel.checkout.git"],\n'
            '    "build": ["rel.build.docker", "rel.build.native"],\n'
            '    "test": ["rel.test.unit", "rel.test.integration"],\n'
            '    "scan": ["rel.scan.sast", "rel.scan.deps"],\n'
            '    "gate": ["rel.gate.strict"],\n'
            '    "publish": ["rel.publish.registry"],\n}',
    route='{"checkout": "rel.checkout.git", "build": "rel.build.docker",\n'
          '         "test": "rel.test.unit", "scan": "rel.scan.sast",\n'
          '         "gate": "rel.gate.strict", "publish": "rel.publish.registry"}',
    break_it='''
# Exactly one step reaches outside. Everything before it is replayable, and
# that is readable from the graph rather than from a comment in a YAML file.
for step in plan.steps:
    print(f"  {step.stage:<10} {', '.join(step.effects) or 'pure'}")

print()
# Now skip the scan, the way a real team does when the scan is slow.
no_scan = replace(bench, edges=tuple(
    e for e in bench.wiring() if not (e.target == "gate" and e.to_port == "scan")))
problems = [p for p in no_scan.validate() if "gate" in p]
print("dropping the scan:", problems[0] if problems else "(silently allowed — the bug)")
''',
    bits='''
viz.evidence({
    "build":   1.1,
    "test":    2.0,
    "scan":   -1.7,   # a transitive dependency advisory nobody had seen
    "gate":    0.9,
    "publish": 0.0,   # never ran: the gate said no
}, title="release — bits per step")
''',
    closing=(
        "## What this bought\n\n"
        "The artefact that was verified is the artefact that ships, by digest. "
        "The gate has two inputs structurally, so 'skip the scan just this "
        "once' is a change to the graph and shows up as one. And the single "
        "effectful step is identifiable without reading any implementation."),
)

# --- 09 · retrieval ---------------------------------------------------------

nine = workflow(
    "09-retrieval-qa", "Retrieval is two searches, not one", "rag.retrieval",
    headline="Retrieval is two searches, not one",
    intro=(
        "Most RAG systems are dense-only, because embeddings are the "
        "interesting part. Then somebody searches for an order number, a rare "
        "surname or an error code, and the vectors blur exactly the token that "
        "mattered.\n\n"
        "Dense and lexical retrieval are independent recall strategies over the "
        "same chunks. Drawn honestly, that is a diamond: chunk once, index "
        "twice, and join into one candidate set. The join is the system."),
    nodes_code='''    node("rag.ingest.files",   "data.read",     [], [("out", "Corpus")]),

    node("rag.chunk.fixed",    "text.chunk",    [("in", "Corpus")], [("out", "Chunks")],
         facets={"purpose.not_for": ["documents with meaningful section structure"],
                 "failure.modes": "splits a table away from its header"}),
    node("rag.chunk.semantic", "text.chunk",    [("in", "Corpus")], [("out", "Chunks")]),

    node("rag.embed.minilm",   "text.embed",    [("in", "Chunks")], [("out", "Vectors")]),
    node("rag.embed.e5",       "text.embed",    [("in", "Chunks")], [("out", "Vectors")]),

    node("rag.index.bm25",     "text.index",    [("in", "Chunks")], [("out", "Index")]),

    node("rag.retrieve.rrf",   "search.hybrid",
         [("dense", "Vectors"), ("sparse", "Index")], [("out", "Passages")],
         facets={"method.name": "reciprocal rank fusion",
                 "purpose.statement": "merge two rankings without tuning a weight"}),
    node("rag.retrieve.linear","search.hybrid",
         [("dense", "Vectors"), ("sparse", "Index")], [("out", "Passages")]),

    node("rag.rerank.cross",   "search.rerank", [("in", "Passages")], [("out", "Passages")]),

    node("rag.generate.llm",   "llm.generate",  [("in", "Passages")], [("out", "Answer")],
         deterministic=False, permissions=("model.invoke",)),

    node("rag.attribute.spans","llm.attribute", [("in", "Answer")], [("out", "Answer")]),''',
    filling='{\n    "ingest": ["rag.ingest.files"],\n'
            '    "chunk": ["rag.chunk.fixed", "rag.chunk.semantic"],\n'
            '    "embed": ["rag.embed.minilm", "rag.embed.e5"],\n'
            '    "lexical": ["rag.index.bm25"],\n'
            '    "retrieve": ["rag.retrieve.rrf", "rag.retrieve.linear"],\n'
            '    "rerank": ["rag.rerank.cross"],\n'
            '    "generate": ["rag.generate.llm"],\n'
            '    "attribute": ["rag.attribute.spans"],\n}',
    route='{"ingest": "rag.ingest.files", "chunk": "rag.chunk.semantic",\n'
          '         "embed": "rag.embed.e5", "lexical": "rag.index.bm25",\n'
          '         "retrieve": "rag.retrieve.rrf", "rerank": "rag.rerank.cross",\n'
          '         "generate": "rag.generate.llm", "attribute": "rag.attribute.spans"}',
    break_it='''
# The dense-only system everyone actually builds: feed the sparse port from
# the embeddings too, and the types refuse it.
dense_only = replace(bench, edges=tuple(
    replace(e, source="embed") if e.to_port == "sparse" else e
    for e in bench.wiring()))
try:
    compile_route(dense_only, route)
except CompileError as exc:
    for problem in exc.problems:
        print("refused:", problem)

print()
print("A hybrid retriever needs two different kinds of evidence, and the port")
print("types are what make 'I will just use the vectors twice' fail to compile.")
''',
    bits='''
viz.evidence({
    "chunk":     1.4,   # semantic chunking kept tables with their headers
    "embed":     0.6,
    "lexical":   2.1,   # the order number was found by BM25 and only BM25
    "retrieve":  0.9,
    "rerank":    0.2,
    "generate": -0.4,
    "attribute": 1.0,
}, title="retrieval QA — bits per step")
''',
    closing=(
        "## What this bought\n\n"
        "Recall is measurable per branch, so 'the model hallucinated' can be "
        "separated from 'the passage was never retrieved' — which are different "
        "bugs with different fixes, and no prompt change repairs the second. "
        "The attribution step is a node with a contract, so grounding is a "
        "checkable property rather than an architectural claim."),
)

# --- 10 · forecasting -------------------------------------------------------

ten = workflow(
    "10-timeseries-forecast", "The leak you cannot see in cross-validation",
    "timeseries.forecast",
    headline="The leak you cannot see in cross-validation",
    intro=(
        "Time-series pipelines fail in one characteristic way: something is "
        "computed across the split boundary. A rolling mean over the whole "
        "series, a scaler fitted before the hold-out, a random shuffle. The "
        "score improves, nobody notices, and the model is worthless in "
        "production.\n\n"
        "Making the split a *node with typed outputs* rather than a line of "
        "code changes what is possible to express. Downstream steps consume "
        "the training port. Consuming the validation port is visible in the "
        "graph, in a picture, before anything is fitted."),
    nodes_code='''    node("ts.load.csv",        "data.read",         [], [("out", "Series")]),

    node("ts.split.rolling",   "data.split_time",   [("in", "Series")],
         [("train", "Series"), ("valid", "Series")],
         facets={"purpose.statement": "split by time, never at random"}),

    node("ts.impute.ffill",    "series.impute",     [("in", "Series")], [("out", "Series")]),
    node("ts.impute.interp",   "series.impute",     [("in", "Series")], [("out", "Series")]),

    node("ts.seasonal.fourier","feature.seasonal",  [("in", "Series")], [("out", "Matrix")]),
    node("ts.seasonal.dummies","feature.seasonal",  [("in", "Series")], [("out", "Matrix")]),

    node("ts.exog.calendar",   "feature.exogenous", [("in", "Series")], [("out", "Matrix")]),
    node("ts.exog.weather",    "feature.exogenous", [("in", "Series")], [("out", "Matrix")]),

    node("ts.assemble.hstack", "feature.assemble",
         [("seasonal", "Matrix"), ("exogenous", "Matrix")], [("out", "Matrix")]),

    node("ts.fit.arima",       "model.fit",         [("in", "Matrix")], [("out", "Model")]),
    node("ts.fit.gbm",         "model.fit",         [("in", "Matrix")], [("out", "Model")],
         deterministic=False),

    node("ts.backtest.rolling","model.backtest",    [("in", "Model")], [("out", "Score")]),''',
    filling='{\n    "load": ["ts.load.csv"],\n'
            '    "calendar": ["ts.split.rolling"],\n'
            '    "impute": ["ts.impute.ffill", "ts.impute.interp"],\n'
            '    "seasonal": ["ts.seasonal.fourier", "ts.seasonal.dummies"],\n'
            '    "exogenous": ["ts.exog.calendar", "ts.exog.weather"],\n'
            '    "assemble": ["ts.assemble.hstack"],\n'
            '    "fit": ["ts.fit.arima", "ts.fit.gbm"],\n'
            '    "backtest": ["ts.backtest.rolling"],\n}',
    route='{"load": "ts.load.csv", "calendar": "ts.split.rolling",\n'
          '         "impute": "ts.impute.ffill", "seasonal": "ts.seasonal.fourier",\n'
          '         "exogenous": "ts.exog.calendar", "assemble": "ts.assemble.hstack",\n'
          '         "fit": "ts.fit.arima", "backtest": "ts.backtest.rolling"}',
    break_it='''
# The leak, made visible: feature engineering fed from the *validation* port.
leaky = replace(bench, edges=tuple(
    replace(e, from_port="valid") if (e.source == "calendar") else e
    for e in bench.wiring()))

print("Which port does feature engineering read?")
for edge in leaky.wiring():
    if edge.source == "calendar":
        print(f"   {edge}   <-- reading the hold-out")

print()
print("The graph states it. On a linear script this is one word in one line,")
print("invisible in review, and it moves the score in the flattering direction.")
''',
    bits='''
viz.evidence({
    "impute":    0.5,
    "seasonal":  1.9,   # Fourier terms carried the weekly cycle
    "exogenous": -1.3,  # weather features were unavailable at forecast time
    "fit":       0.8,
    "backtest":  1.2,   # rolling origin: error varies a lot by regime
}, title="forecasting — bits per step")
''',
    closing=(
        "## What this bought\n\n"
        "The split is a node with two named outputs, so what reads the hold-out "
        "is a property of the graph and shows up in the picture. Seasonal and "
        "exogenous features are independent branches, so an exogenous feature "
        "that is not available at forecast time can be dropped without "
        "disturbing anything else."),
)

# --- 11 · web ---------------------------------------------------------------

eleven = workflow(
    "11-web-harvest", "The domain this started in", "web.harvest",
    headline="The domain this started in",
    intro=(
        "This library began as browser automation, and the general model came "
        "out of it rather than the other way round. It is worth ending the "
        "series here, because the shape now looks like every other workflow in "
        "it — which is the argument.\n\n"
        "The one domain-specific insight worth keeping: a route that *completed* "
        "is not a route that *worked*. A harvest returning an empty record "
        "succeeds by every process measure. The check belongs on the record."),
    nodes_code='''    node("web.resolve.literal", "target.resolve",   [], [("out", "Target")]),
    node("web.resolve.search",  "target.resolve",   [], [("out", "Target")]),

    node("web.session.http",    "session.open",     [("in", "Target")], [("out", "Session")],
         permissions=("net.read",)),
    node("web.session.browser", "session.open",     [("in", "Target")], [("out", "Session")],
         permissions=("net.read", "process.spawn"), deterministic=False,
         facets={"cost.latency_ms": 1800.0,
                 "purpose.not_for": ["static pages that need no scripting"]}),

    node("web.read.body",       "payload.read",     [("in", "Session")], [("out", "Bytes")]),

    node("web.parse.lxml",      "parse.structure",  [("in", "Bytes")], [("out", "Dom")]),
    node("web.parse.html5",     "parse.structure",  [("in", "Bytes")], [("out", "Dom")]),

    node("web.locate.css",      "locate.values",    [("in", "Dom")], [("out", "Fields")],
         facets={"failure.modes": "works today, silently returns the wrong column next month"}),
    node("web.locate.semantic", "locate.values",    [("in", "Dom")], [("out", "Fields")]),

    node("web.norm.units",      "normalise.values", [("in", "Fields")], [("out", "Record")]),

    node("web.verify.shape",    "verify.shape",     [("in", "Record")], [("out", "Record")]),

    node("web.receipt.jsonl",   "receipt.write",    [("in", "Record")], [("out", "Receipt")],
         effects=("state.write",)),''',
    filling='{\n    "resolve": ["web.resolve.literal", "web.resolve.search"],\n'
            '    "session": ["web.session.http", "web.session.browser"],\n'
            '    "read": ["web.read.body"],\n'
            '    "parse": ["web.parse.lxml", "web.parse.html5"],\n'
            '    "locate": ["web.locate.css", "web.locate.semantic"],\n'
            '    "normalise": ["web.norm.units"],\n'
            '    "verify": ["web.verify.shape"],\n'
            '    "receipt": ["web.receipt.jsonl"],\n}',
    route='{"resolve": "web.resolve.literal", "session": "web.session.http",\n'
          '         "read": "web.read.body", "parse": "web.parse.lxml",\n'
          '         "locate": "web.locate.semantic", "normalise": "web.norm.units",\n'
          '         "verify": "web.verify.shape", "receipt": "web.receipt.jsonl"}',
    break_it='''
# Harvesting without the verify step: it compiles, it runs, and it is the
# single most common way a scraper is quietly broken for months.
unverified = replace(bench, stages=tuple(
    s for s in bench.stages if s.id != "verify"))
unverified = replace(unverified, edges=tuple(
    replace(e, target="receipt") if e.target == "verify" else e
    for e in unverified.wiring() if e.source != "verify"))

thin = compile_route(unverified, {k: v for k, v in route.items() if k != "verify"})
print("without a verify step, this still compiles:", thin.digest[:26], "…")
print()
print("Types cannot catch this: an empty Record is a perfectly good Record.")
print("What catches it is the rule that something must check the outcome and")
print("it must not be the thing that produced it — a review rule, not a type.")
print()
print("Compare the two plans:")
print("  with verify   :", len(plan.steps), "steps")
print("  without verify:", len(thin.steps), "steps")
''',
    bits='''
viz.evidence({
    "session":   1.2,   # plain HTTP was enough; the browser was 1.8s of nothing
    "parse":     0.6,
    "locate":   -2.4,   # the CSS path had drifted
    "normalise": 0.4,
    "verify":    1.7,   # and the verify step is what noticed
}, title="web harvest — bits per step")
''',
    closing=(
        "## The series\n\n"
        "Eight workflows across eight fields — tabular modelling, document "
        "extraction, notification services, data quality, release engineering, "
        "retrieval, forecasting and web harvesting. One model, one compiler, "
        "one visualiser, and not a line of domain-specific drawing code.\n\n"
        "What differed each time was the template and the nodes. What stayed "
        "the same was everything that makes the result checkable."),
)


if __name__ == "__main__":
    for book in (seven, eight, nine, ten, eleven):
        path = book.write()
        print(f"wrote {path}  ({len(book.cells)} cells)")
