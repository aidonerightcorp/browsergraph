# Instructions for an LLM harness

You are being asked to solve a problem using this repository's graph model. This
file is the contract. It is written as constraints rather than description,
because a constraint you can check beats a paragraph you can agree with.

**Run `browsergraph check --json` after every edit.** It answers in under a
second and names the fix. Do not tell the user something works until it passes.

---

## What you are building

Not a script. A **description of what must be true**, plus every option that
could make it true, plus a route through them. The runtime picks and runs; you
supply the possibilities and the contracts.

Four object kinds, and keeping them apart is the whole discipline:

| You are writing | It is | It is not |
|---|---|---|
| **Stage** | one ordered requirement — a column | a package, a model name, a score |
| **Node manifest** | a reusable implementation, described | a concrete choice |
| **Candidate** | one manifest with every parameter bound | a family of options |
| **Route** | one candidate per stage | a stage, or an optimizer |

The single most common mistake is putting an implementation name where a
requirement belongs. `Acquire inputs` is a stage. `Playwright · Firefox ·
headless` is a candidate that can perform it. If you find yourself writing a
stage called "Use Playwright", stop: that is a candidate.

---

## Start from a template, not a blank page

Before inventing stages, check whether the shape of this problem is already
known. `browsergraph.templates` holds skeletons — typed ports, edges, and no
candidates — for supervised tabular modelling, document extraction, event
notification, web harvesting, data-quality gating and software release.

```python
from browsergraph import templates as T

print(T.catalog_text())               # what shapes exist
template = T.get("tabular.supervised")
print(template.unfilled({}))          # the slots you must fill
bench = template.instantiate({"load": ["my.csv_reader"], ...})
```

Three reasons this is not optional politeness:

1. A skeleton is **checkable**. Fill it wrongly and the compiler rejects it at a
   port, immediately. Invent your own and you discover the mismatch three stages
   later, or never.
2. A skeleton is **shared**. Two harnesses starting from the same template
   produce comparable graphs, so evidence from one is worth something to the
   other. Two that each invented a pipeline produce two snowflakes.
3. A skeleton **bounds the search**. Stages fix the shape, so the space is the
   product over slots — large, but not the space of all graphs.

Each template carries its own anti-patterns in `template.anti_patterns`. Read
them. They are the mistakes people actually make in that shape, and they survive
into `bench.metadata` so they are still there when you come back to it.

If no template fits, write the stages yourself — and consider contributing the
shape back, because the next harness to meet this problem should not have to.

---

## Types bind, facets rank

Two kinds of description, and confusing them is the most expensive mistake
available here.

**Closed and load-bearing** — ports, types, capabilities, effects, permissions,
parameters. Legality is decided on these. They are checked exactly, cheaply, and
with no model involved.

**Open and advisory** — everything in `manifest.facets`: purpose, prose, verbs,
domain tags, method names, cost, quality priors, provenance, and whatever your
pack invents next. The key space has no fixed vocabulary. A facet becomes
searchable when a `FacetSpec` declares *how* — text, keyword, number, bool, or
carried-but-not-indexed json.

```python
NodeManifest(..., facets={
    "purpose.statement": "impute missing numeric values by column median",
    "purpose.not_for":   ["categorical columns", "time series with gaps"],
    "domain.tags":       ["tabular", "preprocessing"],
    "cost.latency_ms":   4.0,
})
```

The rule, and it is enforced by a test: **a facet may never change whether a
node compiles into a position.** Search may return only legal candidates; an
embedding that liked something illegal does not get a vote. This is also what
makes a model-free tier possible — legality alone narrows most positions to a
handful, and ranking the handful is a job a small local model can do.

An unknown facet is carried, never rejected. A *declared* facet holding the
wrong kind of value is a real error, because something downstream will compare
it.

---

## Three shapes a stage can have

Most stages run once. Two other shapes exist, and each is a real difference in
execution rather than a label.

**`kind="map"` — run once per item.** This is how "do it to all ten thousand
files" gets said. The node inside handles **one** item and never sees the list.

The port types are the part people get wrong. The *stage* talks about the
collection; the *node* talks about one item:

```python
StageDefinition(id="read", kind="map",
                inputs=(PortSpec("in", "List[Path]"),),      # the stage: many
                outputs=(PortSpec("out", "List[Row]"),))
node("read.one", "read", [("in", "Path")], [("out", "Row")]) # the node: one
```

If a node raises, the step names the item — `item 11` — not the batch.

**`kind="branch"` — take one path.** The node returns `(port_name, value)`.
Ports it did not name are marked not-taken, and the steps behind them are
**skipped, not failed**. A path not taken is a correct outcome; logging it as a
failure makes every branching run look broken.

```python
def decide(**kw):
    return ("large", kw["in"]) if kw["in"] > 10 else ("small", kw["in"])
```

**There is no loop.** A loop needs a termination argument, and a graph that can
loop without one is a graph that can hang. Repeat by mapping over a list you
already have, or by running the plan again.

Both kinds are visible in `viz.dag` — a doubled outline for map, a dashed one
for branch — so a graph that claims to batch and does not is visible rather than
merely documented.

---

## Run it

A plan is not a run. `browsergraph.execute` does the running.

```python
from browsergraph import execute
from browsergraph.compile import compile_route

runtime = execute.Runtime({"load.csv": load_csv, "clean.rows": clean_rows})
plan = compile_route(bench, route)

print(runtime.missing(plan))          # every step with no code behind it
run = execute.run(plan, runtime, {"load": "data.csv"}, workspace="work")
print(run.text())
```

Writing a node function:

* Take your input ports by name. Return your one output.
* Several output ports? Return a dict keyed by port name.
* **One** output port? The return value *is* the value, dict or not.
* Ask for `workspace` and you get a folder. Files you write there come back as
  artifacts with a size and a hash.
* Ask for `step` or `params` if the node needs its own settings.

`execute.dry_run(...)` runs everything that touches nothing and refuses the
first step that declares an effect. The plan already knows which those are, so
this needs no flag on the node and no second implementation.

Four more arguments, each using something the graph already knew:

* `workers=N` runs a layer at once. Steps share a layer because nothing connects
  them, so this is safe by construction rather than by hope.
* `fallbacks={"stage": ["other.candidate"]}` tries another candidate when the
  chosen one fails, and records which one did the work.
* `cache=some_dict` skips a step already run on these inputs. Only ever applied
  to steps that are deterministic and effect-free — caching a network step
  serves a stale answer, and caching a random one hides the variation you kept
  it for.
* `run.receipt(task=...)` writes durable evidence keyed on the plan digest.

From the shell:

```bash
browsergraph execute "" mygraph.json --runtime mypkg.nodes:RUNTIME \
    --workers 4 --workspace out --receipt run.json
```

The type check at each hand-off is the part that earns its keep. A node that
promises `Records` and returns `None` is caught at that node, not three steps
later somewhere unrelated.

---

## The strict model

`solutiongraph` is the domain-neutral core. It says things a workbench cannot:
port cardinality, four levels of determinism, idempotency, failure modes, and
slot kinds for branch, map, reduce and loop.

You do not have to write in it. `browsergraph.bridge` crosses over:

```python
from browsergraph import bridge

print(bridge.check(bench))        # what the strict compiler objects to, with codes
print(bridge.summary(bench))      # slots, edges, effects, permissions
space = bridge.admitted(bench)    # what is allowed in each slot, and why not
```

Use it when you want a second opinion with error codes a program can act on,
rather than prose a person has to read.

---

## Look at what you built

`browsergraph.viz` draws any workbench — it knows nothing about any domain.

```python
from browsergraph import viz

viz.dag(bench, route=chosen)      # layers, fan-out, joins, typed edges
viz.route_space(bench, route=chosen, alternative=before)
viz.funnel([("all routes", n), ("legal", m), ("evaluated", k), ("chosen", 1)])
viz.evidence({"parse": 1.9, "locate": -1.1})     # signed bits per step
viz.write_report(bench, "report.html", route=chosen)
```

Draw the graph before you believe it is a graph. A diamond rendered as one box
per layer is a chain, whatever the stage list implies — and that is far easier
to see than to reason about.

---

## Hard rules

1. **Every node declares typed input and output ports.** No untyped edges.
2. **A stage admits every compatible candidate.** Never filter to your
   favourites — completeness is checked and a stage that omits one is rejected.
3. **A parameter with alternatives expands into separate candidates.** Five
   controllers × six binaries × two displays is sixty candidates, not one.
4. **A stage is a leaf holding candidates, or a composite holding sub-steps —
   never both.**
5. **Adjacent stages must connect by type.** If they do not, insert an adapter
   node. Never coerce silently.
6. **Permissions and effects are declared.** If a node touches the network,
   spends money or changes something outside the process, say so.
7. **Metrics carry their source.** Use `"source": "illustrative-prior"` until a
   real run produces a receipt. Never invent benchmark numbers.
8. **Something must verify the outcome, and it must not be the thing that
   produced it.** A step that changes remote state with nothing checking it is
   the failure this project exists to prevent.
9. **A required stage may not be satisfied by a pass-through.**
10. **A fallback belongs to a stage's choice.** It never becomes an extra stage.

---

## The loop to follow

```bash
browsergraph check --json                 # after every edit; fix what it says
browsergraph nodes --json                 # the existing nodes, as manifests
browsergraph workbench -o studio.html     # look at what you built
browsergraph route --compare              # what the search would choose, and why
pytest -q                                 # if you added behaviour, add a test
```

Do not skip the check because the change looked small. The failures this model
is designed to catch all look small.

---

## Writing a node

```python
from browsergraph import NodeManifest, ParameterSpec, PortSpec

NodeManifest(
    id="mydomain.csv_loader",            # lowercase, namespaced, stable
    kind="csv_loader",
    description="Loads a CSV and records its content hash.",   # required
    roles=("source",),                   # source/adapter/transform/action/
                                         # model/verifier/sink/composite/control
    capabilities=("load",),              # what a stage discovers it by
    inputs=(PortSpec("task", "TaskReference"),),
    outputs=(PortSpec("records", "Records", semantic="tabular"),),
    parameters=(ParameterSpec("delimiter", "string", default=",",
                              choices=(",", ";", "\t")),),   # expands to 3
    permissions=("filesystem:read",),
    effects=(),                          # anything outside this process
    runtime={"deterministic": True},
    metrics={"source": "illustrative-prior", "quality": 0.9,
             "latency_ms": 20, "cost_usd": 0.0},
).assert_valid()
```

`assert_valid()` raises with **every** problem, not the first. Read all of them.

Open-ended values — a URL, a document body, an image — are **inputs on a port**,
never parameters. A parameter must be enumerable, or it cannot expand into
candidates and the search cannot see it.

---

## Decomposing a task

Ask, in order:

1. What must be true at the end, and **what would prove it** independently?
2. Working backwards, what must be true before that?
3. For each requirement: what type goes in, what type comes out?
4. What could perform it? If the answer is one thing, it is not a decision yet
   — either find the alternatives or fold it into its neighbour.

**Split a stage into sub-steps** whenever any of these can vary independently:
the capability required, the input or output type, whether it can be skipped,
the failure or fallback policy, the permission or effect, the evidence that
proves it, or its cost. A stage holding seventy candidates is usually three
stages that have not been separated yet.

Six stages is the demonstration, not a template. A document task grows OCR,
translation, chunking and reconciliation stages. Supply the stages your problem
has.

---

## What will be rejected

Do not bother trying; the checker refuses all of these:

- a stage with no candidates, or that omits a compatible one;
- a route that skips a stage, or picks a candidate the stage does not admit;
- a fallback pointing at a different stage's candidate;
- adjacent stages whose types do not connect;
- a node with no capabilities, no outputs, or no description;
- a parameter whose default is not among its own choices;
- a metric with no source;
- a required stage satisfied by a pass-through.

---

## Things you may be tempted to say, and the answer

**"The search space is too large to be practical."** It is 3.8 trillion routes
in the demonstration, and that is the wrong unit. A route is fourteen
independent choices — 41.8 bits. Evidence from dozens of runs resolves most of
them; `browsergraph route` samples per stage at *sum* cost, not product. Report
the bits, not the routes.

Also: nothing ever enumerates that space. `strategy="auto"` picks beam above the
enumeration limit, and asking for `"exhaustive"` on a space that does not fit
raises `SpaceTooLarge` naming the count — it does not try. If you catch yourself
writing a loop over `itertools.product` of the candidates, you have reinvented
the thing the searcher exists to avoid.

**"This should just be a function."** If there is exactly one way to do every
step and no step can fail in an interesting way, then yes — say so. This model
earns its cost when there are alternatives, when failure has more than one
cause, or when what is best changes with context.

**"I'll add a stage for retrying / logging / scoring."** No. Retries belong to a
stage's fallbacks, evidence belongs in receipts, and scoring is the optimizer.
None of them are task stages unless the task genuinely requires them as actions.

**"I'll pick the best candidate and drop the rest."** No. Completeness is
checked. Dropping options makes the picture a summary of your opinion, and
nothing on screen would say so.

---

## When you are done

State what you built, what the checker says, and **what you did not verify**.
If a stage's metrics are still priors, say that they are priors. If nothing has
run yet, say that nothing has run yet. A confident report of an unverified graph
is precisely the failure mode this whole repository is arranged against.

Further reading, in the order that helps most: [HOW_IT_WORKS.md](HOW_IT_WORKS.md)
(plain English), [UNIVERSAL_GRAPH_SYSTEM.md](UNIVERSAL_GRAPH_SYSTEM.md) (the
formal model), [docs/TOWARD_A_GENERAL_MODEL.md](docs/TOWARD_A_GENERAL_MODEL.md)
(what is still wrong with it).
