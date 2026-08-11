# Asking better questions instead of trying more combinations

*A design note on node discovery, and on using a language model to propose graph
**structure** rather than to rank a fixed set of options. Written against the
implementation that exists, with the parts that do not exist marked as such.*

---

## The problem, stated with numbers

Five steps with five candidates each is 3,125 routes. Ten steps is 9.7 million.
The demonstration workbench in this repository has fourteen sub-steps and
3,802,314,700,800. A hundred steps is not a number anyone should write down.

The usual reply is "so search cleverly", and this repository does: per-step
posteriors cost the *sum* of the candidate counts rather than their product, so
166 draws stand in for 3.8 trillion evaluations. That reply is now measured
rather than asserted — `browsergraph/arena.py` builds tasks with a hidden ground
truth, and on a 15,625-route space with twelve runs allowed:

| task | what it tests | first | random | **solve** | gap to best |
|---|---|---|---|---|---|
| `flat` | negative control — nothing varies | 0.2621 | 0.2621 | 0.2621 | 0% for all |
| `needle` | independent per-step quality | 0.1497 | 0.3380 | **0.3807** | 77% → **32%** |
| `paired` | independence deliberately false | 0.0805 | 0.3898 | **0.4479** | 92% → **27%** |
| `noisy` | 5% observation noise | 0.1512 | 0.3320 | **0.3949** | 77% → **29%** |

So the searching works, and `flat` proves the harness cannot manufacture a win.

**But every one of those numbers is about choosing between candidates in a graph
whose shape was fixed by a person.** The search never asks whether the graph
should have an imputation step at all, whether a validator belongs before the
join, or whether two steps should be one. That question is not in the space.

That is the gap this note is about. Two things are needed and they are
different:

1. **A way to find nodes** — a queryable index over what exists, so a proposal
   can name something real.
2. **A way to propose structure** — targeted questions to a model that produce
   *candidate graph edits*, which the compiler then accepts or refuses.

---

## Part one: nodes as a queryable database

### What a node already declares

A `NodeManifest` carries, today:

| Field | Kind | Used for |
|---|---|---|
| `id`, `kind`, `description` | text | identity, display |
| `capabilities` | closed vocabulary | **admission** — which stage it may fill |
| `inputs`, `outputs` | typed ports | **admission** — whether it type-checks |
| `parameters` | typed, with `required` | binding into candidates |
| `effects`, `permissions` | closed | policy gating |
| `runtime.deterministic` | bool | caching, replay |
| `metrics` | numbers with provenance | ranking |
| `facets` | **open** | anything else |

The load-bearing split is already enforced and worth restating, because
everything below depends on it: **types bind, facets rank.** Ports, capabilities,
effects and permissions decide legality, exactly and cheaply, with no model
involved. Everything in `facets` is advisory. A test asserts that a facet can
never change whether a node compiles into a position.

That rule is what makes an embedding safe to use here. An embedding that likes
an illegal node does not get a vote; it only reorders the legal ones.

### What a node index needs next

These do not exist yet. They are the fields that would make a node findable by
something other than a person who already knows its id.

```python
NodeManifest(..., facets={
    # Identity beyond this repository. The point of a URI is that two packs
    # written by strangers can agree without coordinating.
    "capability.uri":    "https://schema.example/cap/impute-numeric@1",
    "capability.aliases": ["fillna", "missing-value-imputation"],

    # What it is for, in the words somebody searching would use.
    "purpose.statement": "fill missing numeric values with the column median",
    "purpose.not_for":   ["categorical columns", "series with structural gaps"],
    "purpose.verbs":     ["impute", "fill", "repair"],

    # What the ports *mean*, not only what they are typed as. `Rows` is a type;
    # "one row per customer, keyed on account id" is a semantic.
    "port.in.semantic":  "tabular rows, one per entity, may contain nulls",
    "port.out.semantic": "same rows, numeric nulls replaced, shape unchanged",

    # Behaviour a caller has to know and cannot read off the types.
    "shape.preserves_row_count": True,
    "shape.preserves_schema":    True,
    "failure.modes":     ["all-null column", "non-numeric column present"],

    # Optional, and never load-bearing.
    "embedding.purpose": [...],     # of purpose.statement
    "embedding.ports":   [...],     # of the port semantics
})
```

`browsergraph.facets` already validates declared facets by kind — text, keyword,
number, bool, json — and carries undeclared ones rather than rejecting them. So
this is a vocabulary to agree on, not a mechanism to build.

### The queries an index should answer

Four, in increasing order of how much they need a model:

1. **By contract.** *What can fill a step taking `List[Row]` and giving
   `Matrix`, under this policy?* Pure type and capability matching. Exact, fast,
   and already implemented — `StageDefinition.with_discovered_candidates`.
2. **By capability URI.** *What implements `impute-numeric@1`?* A dictionary
   lookup, once the URIs exist.
3. **By keyword and facet.** *What mentions "impute" and preserves row count?*
   Inverted index over `facets.keywords`, already produced by
   `facets.keywords()`.
4. **By meaning.** *What does something like "repair gaps in a sensor series"?*
   Embedding similarity over `purpose.statement` and port semantics.

**Query 4 must be filtered by query 1, never the reverse.** Retrieve by meaning
if you like, but the survivors are whatever the type checker admits. Doing it
the other way round produces a plausible node that does not fit, which is the
single most expensive failure mode available to a harness — it compiles nothing
and reads like a bug in the framework.

---

## Part two: questions, not combinations

Here is the actual idea, and it is the interesting one.

A search over candidates asks *which node goes in this slot*. A model can answer
a different and much more valuable question: **what should the graph look like?**
And it can answer it cheaply, because a well-posed question about structure has
a small answer — yes or no, or one of five named nodes — where the combinatorial
version has millions.

The discipline that makes this safe is already the repository's rule for model
involvement, and it does not change: **the model proposes, the compiler
disposes.** `browsergraph/explore.py` implements exactly this today for
candidate choice — `describe()` renders a graph as a prompt, `parse()` reads a
reply into `Suggestion` objects, and `guided()` scores them alongside the
search's own proposals and records every refusal. A suggestion that does not
type-check or that policy forbids is *recorded as refused*, so a model quietly
ignored looks different from one quietly followed.

What follows extends that from "which candidate" to "what shape", using the same
propose/dispose split.

### The five question kinds

Each is stated as: what the model is given, what it must return, and what the
compiler does with the answer.

---

#### Q1 · Does this specific node belong here?

The narrowest and most reliable, because it is a yes/no about one edit.

> **Given** the current graph (steps, ports, wiring, which candidate fills each),
> the results so far, and one specific node with its ports and purpose:
> **does inserting it between `A` and `B` make sense?**
> Answer `yes` or `no`, one sentence of reason, and a confidence in 0–1.

*Disposal:* the compiler checks the insertion type-checks before the model is
even asked — an illegal insertion is not a question worth a token. The answer
only decides whether to *try* a legal edit.

#### Q2 · Is a capability missing, and what could provide it?

> **Given** the graph, the task statement, and the outcomes of runs so far:
> **is there a capability this pipeline lacks?** If yes, name the capability,
> say where in the graph it belongs (between which two steps), and list up to
> five nodes from the index that provide it.

*Disposal:* the named capability is looked up in the index. Nodes the model
invented are dropped and the drop is recorded. Nodes that exist but do not
type-check at the named position are dropped with the type error attached — that
is a useful training signal, not just a rejection.

#### Q3 · Should this optional step be there at all?

The one the current implementation can already act on, because
`compile_route` now accepts a route that omits an optional stage and reconnects
around it.

> **Given** the graph, and that step `S` is optional and can legally be lifted
> out: **is it earning its place?** Consider what the evidence says about the
> runs that included it against the runs that did not.

*Disposal:* both topologies are already in the search space and have distinct
plan digests, so this question is a *prior* over a decision the search can also
make on its own. That is the ideal shape for a model's involvement: it can be
wrong without being harmful.

#### Q4 · Is the shape wrong?

The most valuable and the least reliable, so it must be the most constrained.

> **Given** the graph and a catalogue of known shapes — chain, fan-out, diamond,
> map, branch, gate, fallback, reuse, tournament: **is this the right shape?**
> Specifically: are two steps that could run at once drawn in sequence? Is a
> step that should map over items running once? Is there a decision that should
> be a branch?

*Disposal:* each answer becomes a concrete, checkable edit — "make `read` a map
step", "split `clean` into two parallel steps" — and each is applied to a copy
and compiled. The ones that compile become alternative *graphs* to search
within. The ones that do not are recorded with their type error.

#### Q5 · Have we solved something like this before?

Not a question for a model at all, until there is data.

> **Given** a task statement and the shape of a proposed graph: **which
> previously solved tasks are most similar, and what did their winning graphs
> look like?**

*Disposal:* returns blueprints, which are starting points, not answers. This is
the one with a real flywheel — every solved task adds a row — and it is also the
one that needs the most care, because "a graph that worked for a similar-sounding
task" is exactly the kind of recommendation that feels authoritative and is
frequently wrong. A blueprint should arrive with the evidence behind it: how
many tasks, how similar, how well it did.

---

### What a question is given

Every one of these needs the same context object, and defining it once is what
makes the questions comparable across models and across time:

```
GraphQuestion
  task            the statement of what must be true at the end
  graph           steps, typed ports, wiring, current candidate per step
  history         routes tried, scores, which steps varied and which did not
  evidence        per-candidate posteriors and per-step bits, if any
  catalogue       the capabilities available, not the nodes — five names beat
                  five hundred manifests, and the index resolves the rest
  question        which of Q1..Q5, with its parameters
```

`explore.describe()` already renders most of this. The missing parts are
`history` and `catalogue`.

### What comes back, and what happens to it

```
GraphEdit
  kind        insert | remove | replace | split | merge | make_map | make_branch
  where       between which steps, or which step
  what        a capability URI, or a specific node id
  why         one sentence, kept for the record
  confidence  0..1, advisory only
```

Every edit is applied to a **copy** of the workbench and compiled. Three
outcomes, and all three are worth storing:

* **compiles** — a new graph enters the search space, on its own merits.
* **refused by types** — recorded with the exact mismatch.
* **refused by policy** — recorded with the permission or effect that blocked it.

A model that produces mostly-refused edits is measurably worse than one that
does not, and *that is the metric* for this whole layer. It is also the reason
to store refusals rather than discard them: without them, every model looks
equally good.

---

## How to tell whether any of this helps

The honest answer is that nobody knows yet, and the machinery to find out
already exists. `arena.py` builds tasks with a known best answer; `benchmark.py`
runs strategies against each other on the same budget and reports a loss when
there is one.

So the experiment is well-defined:

1. Add an arena task whose **shape** is wrong — the best solution requires an
   extra step, or a map where there is a chain. The current search cannot reach
   it at any budget, so its gap-to-best has a floor above zero.
2. Add `guided` as a fifth strategy in the benchmark, with a stubbed proposer
   first (one that returns the known-correct edit) to establish the ceiling.
3. Replace the stub with a real model and measure the distance to that ceiling.
4. Report the refusal rate alongside the score, because a model whose edits are
   80% illegal is not helping even when the 20% is good.

If a model-guided search does not beat the unguided one on a task the unguided
one provably cannot solve, then this layer is not worth its latency, and that
result should be published as plainly as the positive one.

---

## Instructions for an LLM harness continuing this work

You are being asked to extend this design. Four rules first, then the work.

**Read `AGENTS.md` before writing anything.** It is the contract: what a stage
is, what a node is, why a facet may never decide legality. This note assumes it.

**Never let a proposal bypass the compiler.** Everything you generate is a
suggestion. `compile_route` and `bench.validate()` decide. If you find yourself
writing code that skips them because the model was confident, stop.

**Record refusals.** A rejected suggestion is data. The refusal rate is the only
honest measure of whether this layer earns its cost.

**Do not invent node ids.** Propose a *capability*; let the index resolve it. A
hallucinated id is the failure mode that reads like a framework bug.

### Task A — extend the question catalogue

Write more questions in the shape of Q1–Q5 above. For each, state: what context
it needs, what its answer looks like, and **what the compiler does with the
answer**. A question whose answer cannot be turned into a checkable edit is not
a question, it is a conversation. Candidates worth developing:

* Should this step be split into two that can run at once?
* Is this join receiving two things that should have been one?
* Which step is most likely responsible for the current failure, given the
  per-step evidence?
* Is there a cheaper node that would do at this position, given the quality
  achieved is already above the threshold?
* Does this graph need a verification step that is independent of the step it
  verifies?
* Is there a step whose output nothing consumes?
* Should this branch have been a policy gate?

### Task B — populate the capability vocabulary

The index needs stable names. Produce a catalogue of capability URIs with, for
each: a canonical name, aliases people actually use, the port types it typically
takes and gives, and the failure modes it typically has. Cover at minimum:
ingestion, parsing, validation, imputation, normalisation, deduplication,
entity resolution, feature encoding, model fitting, calibration, evaluation,
retrieval, reranking, extraction, redaction, notification, and persistence.

Where an external standard already names a thing, **use its name**. A vocabulary
that agrees with nobody is a vocabulary of one.

### Task C — propose pipeline steps per domain

For each domain in `browsergraph.templates`, list the steps practitioners
actually use and that the template does not have — and say which are load-bearing
and which are habit. The templates were written from a small number of examples
and they will be missing things.

### Task D — build the measurement, not just the proposals

Add the arena task described above whose shape is wrong, and wire `guided` into
`benchmark.STRATEGIES`. Until that exists, every claim in this document about
model-guided search is a hypothesis, and should be written as one.

### What to hand back

A pull request, or a patch, containing: the new questions as data rather than
prose, the capability catalogue as JSON, and the benchmark result — including the
refusal rate. If the result is negative, say so in the summary line. A negative
result here is worth more than another layer of abstraction, because it tells
everybody after you not to build it.

---

## The measurement, built — and what it says

Task D above said to build the measurement before believing anything else here,
so that happened first. `arena.missing_step` is a task whose best solution needs
a step the graph does not have. The node exists in a library; no stage holds it.

That gives a **structural ceiling**: a route search names one candidate per
stage, so it cannot reach a graph with an extra stage in it, at any budget.

    best possible                          0.7068   (requires the insertion)
    best reachable without an edit         0.3181
    -> no route search can beat a gap of   57.2%

Measured, 3,125 routes, same run budget for every strategy:

| strategy | budget 20 | budget 40 | gap to best |
|---|---|---|---|
| `first` | 0.0916 | 0.0916 | 90.5% |
| `random` | 0.1805 | 0.2379 | 77.4% → 69.0% |
| `solve` | 0.2415 | 0.3007 | 68.4% → **59.7%** — arriving at the floor |
| `guided` | **0.5076** | **0.5764** | 29.3% → **19.2%** — through it |

`solve` at budget 40 lands at 59.7% against a predicted floor of 57.2%: the
route search does its job and then stops, exactly where the arithmetic said it
would. `guided` crosses because it is allowed to change the shape, on the same
number of runs, split across the original graph and each variant.

**The proposer in that table is not a model.** `edits.mechanical` enumerates
every legal insertion by trying them, which on a small library and a small graph
is complete — and complete is a strong property a model cannot beat, only match
faster. That reframes the whole guided layer: it is not needed where enumeration
is affordable. It is needed where the library is large, or where the answer is a
removal or a retype that enumeration would propose hundreds of. **That is where
a model has to be measured, and against this baseline rather than against
nothing.**

Two things fell out of building it, both worth keeping:

**There is no `replace` edit.** Writing its test is how the invariant was
rediscovered: a stage must admit *every* compatible candidate, so narrowing one
is refused by the validator. Restricting what may run is a policy decision made
at gate time with a reason attached, not a structural edit. The vocabulary has
`widen`, which only ever adds.

**The first version of the experiment measured nothing and looked like a
finding.** The library node had no function registered, so every variant graph
compiled and none could run — and `guided` scored *below* the unguided search
while reporting 0% of edits refused. A benchmark that can produce a confident
number from a broken setup is the thing to watch for; here the tell was that a
0% refusal rate and a worse score cannot both be true.

---

## A real model, asked once

`edits.question` renders one of the questions above; `edits.parse` reads the
reply. Asked `missing` on `arena.missing_step`, a local `deepseek-v4-flash`
replied:

```json
[{"kind": "insert", "where": ["s0", "s1"], "what": "repair.fix",
  "why": "Adds the missing repair capability to remove the defect that
          penalizes all routes.", "confidence": 0.9}]
```

Which compiles. `1 of 1 edits compiled (0% refused)`.

**That is much less impressive than it looks, and the reason matters.** The
library held exactly one node, the prompt listed the two positions where it
legally fits, and the history said every route carries the same penalty. The
model's job was to notice a hint, not to have an idea. A correct answer here is
evidence that the *plumbing* works — question renders, reply parses, edit
compiles — and almost no evidence about whether a model is good at this.

The experiment that would be evidence: a library of twenty nodes of which one
helps, no hint in the history, and the refusal rate reported next to the score.
Against `edits.mechanical`, which would enumerate all twenty and be complete.
That is not built.

---

## Status of this document

Written 2026-08-11. Describes:

* **Implemented** — the facet/type split and its enforcing test; node discovery
  by contract; `explore.describe/parse/guided` for candidate choice with recorded
  refusals; optional-step topology in the search; `arena.py` and `benchmark.py`;
  `edits.py` — `GraphEdit`, `apply`, the five kinds, `variants` with a refusal
  rate, `mechanical` enumeration, and `insertion_points`; `benchmark`'s `guided`
  strategy. Every number above is reproducible via `pytest tests/test_arena.py`.
* **Designed, not implemented** — capability URIs, port semantics, embeddings,
  the node index, blueprints, and the experiment that would tell you whether a
  model beats enumeration. `edits.GraphEdit` is the answer format those questions would
  return, and `from_dicts` already parses it, so a proposer is a function
  returning a list of edits and nothing above it needs to change.

Nothing in the second list should be described as working until it is in the
first.
