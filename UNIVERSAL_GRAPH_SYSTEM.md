# The universal graph solution system

**A solution is not a fixed capability. It is an ordered graph assembled from
interchangeable atomic candidates, validated through typed contracts, evaluated
from execution evidence, and improved by selecting better routes.**

browsergraph is the proof of concept, but nothing in this model is about
browsers. The same primitives describe document ingestion, image processing,
data cleaning, machine learning, API orchestration and business workflows —
because the stages, the candidates and the objectives are *data*.

```bash
browsergraph workbench -o studio.html     # one self-contained file, five views
browsergraph nodes --json                 # the existing node registry as manifests
```

---

## The distinction everything rests on

One confusion ruins every diagram of this kind, and it is worth stating before
anything else:

| | |
|---|---|
| **Acquire inputs** | is a **task stage** — a column |
| Browser adapter · Playwright · Firefox · headless | is an **atomic candidate** |
| controller, binary, display | are its **parameters** |
| `InputHandle` | is its **output contract** |
| browser, network | are **permissions** |
| quality, latency, cost | are **measurements** |
| a weighted scorer | is **optimization logic** |

Only the first is a column. Promote any of the others into the task line — put
"Playwright" beside "Verify" as if they were the same kind of thing — and the
picture stops saying what runs in what order, which was the only thing it was
for.

So the invariants are strict, and `WorkbenchDefinition.validate()` enforces
them:

- stages are ordered columns, left to right, and nothing reorders them;
- a route selects exactly **one** primary candidate per stage;
- fallbacks belong to a stage's choice and never become extra stages;
- feedback and optimization are a separate, typed control plane;
- a configuration dimension expands the candidates *inside* a stage.

## Completeness is a rule, not a courtesy

A stage must admit **every** compatible candidate the registry knows about.
`validate()` rejects a stage that omits one:

```
stage 'verify' omits 3 compatible candidate(s), e.g. 'demo.verify.ocr_check…'
  — a stage must show everything that could perform it
```

Silently dropping whatever scored badly last time turns the diagram into a
summary of previous opinions, and nothing on screen would say so. Presentation
filters may hide candidates temporarily; the viewer prints `149 of 149
candidates visible` so a filtered view can never be mistaken for the whole
registry.

## The arithmetic

Six stages with 76, 27, 13, 14, 11 and 8 candidates is 149 things to choose
from — and

```
76 × 27 × 13 × 14 × 11 × 8 = 32,864,832 complete primary routes
                             2,827 adjacent-stage transitions
```

Routes **multiply**. That number is the difference between "we support several
options" and the actual size of the space a search works in.

## The pieces

| type | what it is |
|---|---|
| `NodeManifest` | a portable description — identity, roles, capabilities, typed ports, parameters, permissions, effects, dependencies, runtime, metrics |
| `NodeDefinition` | a manifest plus an *optional* factory |
| `NodeCandidate` | one definition with every selectable parameter bound |
| `StageDefinition` | one ordered requirement; one column |
| `SolutionDefinition` | a complete route, with per-stage fallbacks |
| `FeedbackDefinition` | one typed learning signal, with scope and authorised action |
| `OptimizationProfile` | what "better" means, as data |
| `WorkbenchDefinition` | all of it, portable and validatable |

Schemas: [`browsergraph/schemas/node-manifest.schema.json`](browsergraph/schemas/node-manifest.schema.json),
[`browsergraph/schemas/workbench.schema.json`](browsergraph/schemas/workbench.schema.json).

### Manifests are separate from runtime on purpose

The interesting question — *what could perform this step, and would it connect
to what comes next* — has to be answerable about nodes that are **not
installed**, not written in Python, or not written yet. So `NodeDefinition.factory`
is optional, and a description-only node can be inspected, validated and planned
with; only `build()` refuses.

The existing browsergraph nodes get manifests without being rewritten — they
already declare `reads`, `writes`, `mutates` and `verifies`, which is most of a
manifest. Authors who want to say more use `@described_node(...)`.

### Atomic candidates: show the real choices

```
Browser adapter
├── controller: BrowserPort | Playwright | Selenium | Puppeteer | CDP
├── binary:     Chrome | Chromium | Edge | Firefox | WebKit | Brave
└── display:    headless | headed
```

That is **60 candidates**, not one. Drawing it as a single box hides
fifty-nine decisions a person is entitled to make. `expand_node_candidates()`
materialises all of them, with IDs that are readable, stable, and sensitive to
values but *not* to key order — otherwise one configuration accumulates evidence
under two different names.

```python
candidate_id("demo.llm_parser", {"model": "DeepSeek", "strategy": "map-reduce"})
# 'demo.llm_parser.deepseek.map-reduce.a1b2c3d4'   — same regardless of key order
```

## What the validator refuses

Each of these produces a diagram that looks entirely reasonable and is not true:

- a stage that omits a compatible candidate;
- a route that is incomplete, or picks a candidate the stage does not admit;
- a fallback pointing at a candidate from a **different** stage;
- a route listing its own primary as a fallback;
- a **required** stage satisfied by a pass-through candidate;
- adjacent stages whose types do not connect — *insert an adapter, do not coerce*;
- a feedback channel that authorises no action, or has an unknown scope;
- an objective with a direction that is not a direction, or a weight ≤ 0;
- a non-finite metric;
- a node with no capabilities (nothing could ever discover it) or no outputs.

`validate()` returns **every** problem rather than the first, because stopping
at the first turns fixing a workbench into a guessing loop.

## Five synchronized projections

All five read the same data. Changing a view never changes what the graph means.

| view | the question it answers |
|---|---|
| **All candidates** | what can perform each step? Grouped by definition, bindings shown separately, several routes highlighted at once |
| **Path network** | what does the whole choice topology look like? Every candidate once, in its column |
| **Compare routes** | how do complete solutions differ? One route per row, cells differing from the baseline highlighted |
| **Build step by step** | which candidate should this route select now? Ineligible candidates stay **visible** with their blocking reason |
| **Feedback & optimization** | how does this learn without corrupting the task topology? |

Two rules about the network view earned themselves the hard way:

- **Edges live in the gutters between columns.** Drawn dot-to-dot, 2,827
  hairlines ran straight through every candidate name and the columns became
  unreadable.
- **Every candidate is labelled.** A first draft labelled only stages with 30 or
  fewer, which left the 76-strong acquire column as a wall of anonymous dots —
  and that column is the entire point.

There is no force layout, no backward edge and no diagonal across stages.
Position *is* the meaning, so nothing is allowed to move it. Route sampling is
deterministic: the same seed draws the same picture.

## Feedback is typed, and outside the task line

Execution stays left to right. Receipts are emitted *beside* it. The optimizer
is not a stage.

Eight channels, each declaring signal, scope, producer, consumer and the action
it authorises: contract compatibility, execution diagnosis, independent
verification, quality and yield, latency and resources, cost and tokens, policy
and authority, and provenance/drift/freshness.

Scope is what makes a signal usable. "This failed" is not actionable; "this
candidate failed on this edge in this task context at this version" is.

The control plane runs **Observe → Attribute → Diagnose → Propose → Gate →
Learn**, and hard constraints always precede soft scoring:

```
registry discovery → binding validation → contract validation → permission,
effect, dependency, runtime and resource policy → evidence sufficiency →
objective scoring → explore/exploit → proposal → full revalidation →
execution → independent verification → receipt
```

**A high predicted score cannot legalise a candidate that lacks a required
permission or produces the wrong type.**

### Two things the scorer does deliberately

An **unmeasured** metric is skipped, not scored zero. Scoring it zero punishes
anything new for being new, and the system stops exploring without anyone
deciding that it should.

Every metric in the demonstration carries `"source": "illustrative-prior"`, and
a test enforces it. They exist to give the viewer something to sort by. Real
optimization consumes real receipts; shipping invented benchmarks would teach
exactly the habit this project argues against.

## Building one

```python
from browsergraph import (
    NodeManifest, ParameterSpec, PortSpec, StageDefinition, SolutionDefinition,
    WorkbenchDefinition, candidate_id, expand_node_candidates,
)

nodes = (NodeManifest(
    id="example.file_loader", kind="file_loader",
    description="Loads and hashes an authorized file.",
    roles=("source",), capabilities=("acquire",),
    inputs=(PortSpec("task", "TaskReference"),),
    outputs=(PortSpec("handle", "InputHandle"),),
    parameters=(ParameterSpec("mode", "string", default="file",
                              choices=("file", "directory", "glob")),),
    permissions=("filesystem:read",),
    runtime={"deterministic": True},
).assert_valid(),)

candidates = expand_node_candidates(nodes)
acquire = StageDefinition(
    id="acquire", name="Acquire inputs",
    input_type="TaskReference", output_type="InputHandle",
    success="the input is readable, identified and versioned",
    required_capabilities=("acquire",),
).with_discovered_candidates(nodes, candidates)      # every compatible one

WorkbenchDefinition(
    title="My studio", task="…", success="…",
    nodes=nodes, candidates=candidates, stages=(acquire,),
    solutions=(SolutionDefinition(
        id="local", route={"acquire": candidate_id("example.file_loader",
                                                   {"mode": "file"})}),),
).assert_valid().write_html("studio.html")
```

## Other domains, same primitives

**Document extraction** — load → detect type → OCR → clean → detect language →
translate → chunk → deterministic extract → LLM extract → reconcile → validate →
verify against source → emit.

**Machine learning** — acquire → leakage constraints → split → clean → impute →
encode → generate features → reduce collinearity → select → fit → calibrate →
ensemble → quantify uncertainty → stability → package → emit.

**Image processing** — load → decode → inspect metadata → detect synthetic
signals → OCR → analyse compression → analyse geometry → enhance → verify visual
constraints → emit.

The six demonstration stages are **not** a universal pipeline. Stages are data;
supply different ones.

## Extensibility, and the limits that are not there

Unlimited stages, candidates per stage, definitions per capability and bindings
per definition. Deterministic, statistical, model, LLM, human, browser, API,
generated, remote and composite nodes. Nested child graphs behind a composite
node's external ports. New port types, permission taxonomies, metrics, feedback
channels, optimization profiles and search strategies — exhaustive enumeration,
deterministic sampling, beam search, Bayesian optimization, bandits, evolutionary
search, Pareto fronts — without changing the canonical model.

Scale is managed by indexing, filtering, lazy expansion and hierarchy. There is
**no architectural top-k**: search may evaluate a subset under an explicit
budget, but the registry retains the full admitted set and reports what it did
and did not examine.

## Anti-patterns this implementation rejects

Mixing task stages with model names, engines or scores · representing a
parameter family as one candidate · hiding compatible candidates behind an
unexplained cutoff · force-directed layouts that reorder the task topology ·
backward or mixed-direction routes · unlabelled feedback arrows · treating the
optimizer as a stage · letting scoring bypass contracts or permissions · silent
port coercion · skipping an optional stage without a pass-through certification
· changing a binding without a new candidate identity · metrics without context,
version or source · declaring success from the producer's own self-assessment ·
losing failed runs and rejected routes · fabricated benchmark results ·
hard-coding the six-stage demonstration as universal.

## Verified

Measured on this implementation, not asserted:

```
6 stages · 48 definitions · 149 atomic candidates
32,864,832 complete routes · 2,827 adjacent transitions
5 named routes + an interactive custom route · 8 feedback channels · 4 profiles
```

The studio is opened in a real headless browser as part of verification, and
every projection is asserted to render with **zero JavaScript errors** —
149 candidate cards, 149 network nodes, 2,827 background transitions, 5 compare
rows, 8 feedback rows, 4 profile rows. Revoking `browser` authority in the
builder blocks exactly the 60 browser-adapter candidates, each stating its
reason, and the route validator reports the violation.

That check is there because two genuine bugs — `Node.append()` returning
`undefined`, in four places — passed every Python test, produced a valid file,
and rendered a blank panel. Nothing catches that except opening the page.
