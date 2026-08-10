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
