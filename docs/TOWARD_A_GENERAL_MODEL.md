# Toward a general model — a critical review

*What would have to be true for this to be a way of building software, rather
than a good way of building browser automations. Written against the
implementation as it was, not about it. Where I am criticising my own work I say
so plainly.*

> **Status, later.** Findings 1–5 and 7 have been fixed and the fixes are
> described below, each next to the finding it answers. The review is kept
> whole rather than edited into agreement with the code, because the argument
> for the shape is worth more than a list of resolved tickets — and because a
> critical review quietly rewritten to say the problems never existed is not a
> document anybody should trust. What remains open is finding 6.

---

## Summary of findings

| # | Finding | Severity | Now |
|---|---|---|---|
| 1 | **The model is a pipeline, not a graph.** It cannot express fan-out, fan-in, or a join. The headline route arithmetic only works because it is a chain. | **Fatal to the universal claim** | **Fixed.** Typed named ports, an explicit edge list, per-edge checking, longest-path layering. `map` and `branch` are step kinds; loops are deliberately absent. |
| 2 | **Types are string equality.** No subtyping, no parametric containers, no semantic distinction. Substitutability is asserted, never checked. | High | **Fixed.** A subtype lattice with `is_a`, and `element_of` for the `List[Row]`/`Row` relationship a map step needs. |
| 3 | **Route-level success is a poor teacher.** Measured: it resolves 27% of the choice in 600 runs against 81% for per-step outcomes. | High — but the fix is already in the design | **Fixed**, and then fixed again: the verdict was reaching the receipt but not the per-candidate posteriors, so a reader that produced nothing and one that produced two records were believed equally. |
| 4 | **No compiled plan.** `validate()` checks a description; nothing produces an immutable, content-addressed thing that *ran*. | High | **Fixed.** `compile_route` freezes a plan with a content digest, and receipts and evidence key on it. |
| 5 | **Nothing checks a node at its ports at run time.** A node can declare `Record[]` and return a string. | Medium | **Fixed.** Every step's output is guarded against its declared ports, and the branch case checks only the ports actually taken. |
| 6 | **The package name fights the claim.** `pip install browsergraph` to do document extraction is a tax on every adopter. | Medium, and cheap to fix | **Open.** `solutiongraph` exists as the domain-neutral core and ships in the same distribution, so the layering is real and the name is still wrong. |
| 7 | **LLM adoption needs a checker, not prose.** Documentation does not stop a model inventing a field. | Medium, mostly addressed this round | **Fixed.** `explore.py`: the model proposes, the compiler disposes, and a refused suggestion is recorded as refused. |

The list further down — *What I would do next, in order* — has been worked
through as far as item 5. Items 6 and 7 of that list are the live ones.

---

## 1. The fatal one: this is a pipeline

The specification states the invariant proudly — *"task stages are ordered
columns from left to right"* — and treats it as what makes the architecture
comprehensible. It is comprehensible because it is a **chain**, and a chain is
a strict special case of a DAG. Every claim about generality rests on a shape
that cannot express the most ordinary graph there is:

```
            ┌─ extract price ─┐
parse ──────┤                 ├────── reconcile
            └─ extract title ─┘
```

That is not a limitation I inferred from reading. I tried it:

```
stage 'price' produces 'Price' but 'title' consumes 'Doc'  — insert an adapter
stage 'title' produces 'Title' but 'join' consumes 'Price' — insert an adapter
```

The validator is *correct by its own rules* and the rules are wrong. Two
consequences, and the second is worse than the first:

**Expressiveness.** No fan-out, no fan-in, no join, no map-over-collection, no
conditional branch as a first-class shape. Every real domain in the
specification's own list needs at least one: document extraction reconciles
independent extractors; ML pipelines fan out over folds; business workflows
branch on a decision. The examples were chosen to fit the shape.

**The arithmetic.** `route_count = c₁ × c₂ × … × cₙ` is a product over a
*sequence*. In a DAG, a route is an assignment of one candidate to each node,
so the count is still a product over nodes — but "adjacent transitions" becomes
edge-wise, type-checking becomes per-edge rather than pairwise-sequential, and
"one candidate per stage, in order" stops being a well-formed sentence. The
headline numbers survive; the mental model does not.

### What the fix looks like

The invariant that is actually load-bearing is not "left to right". It is
**"exactly one candidate per node, and every edge type-checks"**. That holds in
any DAG. The linearity is a *rendering* choice — and a good one, because a
topological layering of a DAG still draws left to right.

```
Node        := id, input ports (typed), output ports (typed), candidates
Edge        := (from_node, from_port) → (to_node, to_port)
Route       := {node_id: candidate_id} for every node
Valid route := every edge's producer output type ⊑ consumer input type
```

Concretely: `StageDefinition` keeps its candidates and grows `inputs`/`outputs`
as named typed ports; a `WorkbenchDefinition` grows an `edges` list; the
sequential adjacency check becomes an edge check; layering by longest-path
gives back the columns for free. Sub-steps already prove the recursion works.
**This is the next piece of work and it is the one that matters.**

---

## 2. Types are strings, so substitutability is a promise

`input_type: "InputHandle"` compared by `==`. That gives:

- no subtyping — a `CsvRecords` cannot be offered where `Records` is wanted;
- no parametric containers — `List[Record]` is a different string from
  `List[Address]` and neither relates to `List`;
- no semantic distinction — two nodes producing `text/plain`, one an address
  and one a summary, are declared interchangeable and are not;
- no units — `latency` in seconds and milliseconds are the same type.

The manifest has a `semantic` field on ports and **nothing reads it**. That is
worse than not having it: it looks like the problem is handled.

Liskov's rule is the one being violated. Candidates in a node are claimed to be
substitutable, and substitutability is a statement about *behaviour under the
contract*, not about a matching label. Minimum viable fix, in order of value:

1. **Structural compatibility with declared coercions.** A type may declare
   `is_a` parents; `⊑` becomes reachability. Cheap, and kills the biggest class
   of false matches.
2. **Semantic type participates in the check.** Same carrier plus different
   semantics is incompatible unless an adapter says otherwise.
3. **Units on numeric ports**, checked. Trivially cheap, catches a genuinely
   expensive class of bug.
4. **Refinements as postconditions.** `NonEmpty[Records]` is a runtime check at
   the port, which is §5.

---

## 3. How to not search 3.8 trillion routes

The right framing is information-theoretic, and it makes the problem look very
different from the way the size of the space makes it look.

A route is a choice per sub-step. With no knowledge, naming a good one costs

```
Σ log₂(candidates per sub-step) = 41.8 bits
```

for the demonstration. 2⁴¹·⁸ is 3.8 trillion, which sounds hopeless. But 41.8
bits **decomposes into 14 independent choices of 2 to 6 bits each**, and every
measured run supplies some of them. *The number of experiments scales with the
entropy of your posterior, not with the size of the space.* Dozens of runs, not
trillions — **provided the choices are independent.**

So the practical algorithm is:

1. Keep a posterior per candidate, per context — Beta for "does it work",
   running moments for quality, latency, cost.
2. Propose by **Thompson sampling** each sub-step. Cost is the *sum* of the
   candidate counts (166 draws), not their product. Exploration falls out of
   the posterior width; there is no ε to tune and it stops on its own.
3. **Measure where independence fails** rather than assuming it holds. When an
   observed route does much worse than the product of its parts predicted, that
   *specific pair* interacts and deserves joint search. Everything else stays
   greedy.

`browsergraph/evidence.py` implements this. `bits_remaining()` is the number
that matters: it answers "should I run more experiments" in a way that a
progress bar over the search space cannot, because it *falls with evidence*
rather than only with enumeration.

### One metric I got wrong, and the fix

My first `bits_remaining` normalized success rates into a distribution and took
its entropy. After a thousand simulated runs it reported **1% resolved** while
the picks were visibly improving. Rates cluster near one half, so normalizing
them yields a near-uniform distribution whose entropy barely moves however
confident the underlying beliefs get. It was measuring the spread of the rates,
not the thing anyone cares about.

The right quantity is **uncertainty about which candidate is best**: sample each
posterior repeatedly, count argmax wins, take the entropy of *that*. Zero bits
means one candidate wins every draw.

### A finding worth keeping: credit assignment decides everything

With the metric fixed, simulating against a hidden ground truth:

| teacher | after 600 runs | correct picks |
|---|---:|---:|
| route-level pass/fail | 27% resolved | **3 of 14** — not converging |
| per-step outcomes | **81% resolved** | **14 of 14** |

Route-level success dilutes credit across fourteen candidates equally, so every
posterior converges to the average route quality rather than to its own truth.
More runs do not help; the signal is not there.

This is a measured argument for something the design already had for other
reasons: **per-step receipts and independent verification are not bookkeeping,
they are what makes learning tractable at all.** A system that only knows
whether the whole run passed cannot learn which part was responsible, however
much compute you give it.

### The two regimes the user asked for, and how they relate

- **Fast, good-enough:** Thompson-sample a route (sum-cost), run it, fold the
  receipt back in. Suggested routes and suggested configurations are the
  posterior's argmax; fallbacks are its second and third place *for this
  context*, not whatever was written down when the graph was drawn.
- **Exhaustive experimentation:** the same posteriors, but sweep rather than
  sample, and record everything. What that buys is not a better single answer —
  it is the *interaction structure*, the Pareto front, and the second-best
  route, which the cheap path can only guess at.

They are the same machinery at different budgets, and the honest way to present
them is that the cheap path is the exhaustive path with a stopping rule.

---

## 4. There is no compiler, and there needs to be

`validate()` checks a *description*. Nothing turns a description into an
immutable artifact that then runs. That absence is why "replay" is currently
aspirational: a receipt names a route, but the route can be edited underneath
it and nothing notices.

What a compile step must produce:

- a topologically ordered plan over the DAG;
- every edge resolved, including inserted adapters, with concrete types;
- every parameter bound and defaulted;
- the union of permissions and effects, computed not declared;
- **a content hash of all of the above.**

That hash is the missing key. Receipts should be keyed on it, evidence should
be attributed to it, and a plan whose hash is not the one in the receipt is not
the plan that ran. Without it, "we learned that this route is good" is a
statement about a name, not about a thing.

---

## 5. Nothing checks a node at its ports

`Checked` and `RecordingBrowser` verify that browser nodes do not lie about
mutation. Nothing does the equivalent for data: a node declaring
`outputs: Records` can return `None` and the graph carries on. For a general
paradigm the port is the natural boundary — validate on the way out, in one
place, with the failure attributed to the node that produced it rather than to
the node that choked on it three steps later.

This is cheap and I have no excuse for not having it.

---

## 6. The name is a tax

`pip install browsergraph` to do document extraction, or an ML pipeline, is a
sentence people have to explain to their colleagues. Names shape adoption more
than architecture does.

The clean split, and it is mostly mechanical because the layering already
exists:

```
graphspec   manifest, workbench, policy, search, evidence, studio, schemas
            — zero dependencies, no browser anywhere
browsergraph  the browser domain pack: ports, drivers, nodes, engines
```

`manifest.py` and `workbench.py` already import nothing from the runtime. The
extraction is a package boundary, not a rewrite.

---

## 7. What LLM harnesses actually need

The instinct is to write more documentation. That is not what fails. A model
does not invent a field because it lacked prose; it invents a field because
**nothing told it, in under a second, that the field does not exist.**

In rough order of value:

1. **A fast local checker with actionable errors.** `browsergraph check
   --json` — 0.02 s on the demo, `{"ok", "findings":[{"severity","where",
   "message","fix"}]}`. A harness can run it after every edit and repair
   itself. This is worth more than every other item combined.
2. **A machine-readable schema** the checker enforces. Shipped.
3. **Worked examples in unrelated domains.** One browser example teaches a model
   that this is a browser tool. Three examples across three domains teach it the
   shape.
4. **Scaffolding.** `new-node` and `new-domain` commands, so the first file a
   model writes is already the right shape.
5. **Instructions written as constraints, not description** —
   [`AGENTS.md`](../AGENTS.md). "Every node declares typed ports; run
   `browsergraph check` before claiming it works" beats three pages of theory.

The failure mode to design against is not hallucination, it is **plausible
adjacency**: a model that writes something reasonable-looking that this system
does not accept. The checker converts that from a silent divergence into a
fixable error, which is the only intervention that reliably works.

On "complaining about the search space": a model told it must consider 3.8
trillion routes will object, and it should. Told that a route is fourteen
independent choices and that evidence has already resolved 81% of them, it will
not — because that is a different and true statement about the same system.

---

## What I would do next, in order

1. **Generalize to a DAG.** Typed named ports, an explicit edge list, per-edge
   checking, topological layering for the view. Everything else is downstream.
2. **Type lattice.** `is_a` declarations, semantic participation, units.
3. **Compile to a hashed plan**, and key receipts and evidence on the hash.
4. **Port-boundary validation** at run time.
5. **Split the packages.** `graphspec` + `browsergraph`.
6. **Three domain packs** that are not browsers, each with a worked example and
   a scaffold.
7. **Only then**: richer search — Pareto fronts, joint search where interaction
   was measured, learned cost models.

Items 1–4 are the difference between a good tool and a way of building
software. Items 5–6 are the difference between one that gets used and one that
does not.

**Where that list stands.** 1 to 4 are done. 5 is half done — `solutiongraph`
is a real package boundary with its own schemas, and it ships inside the
`browsergraph` distribution, so the layering exists and the name still lies.

6 is done: `browsergraph.packs` has three non-browser domains — batching a
folder, gating data quality, fitting a tabular model — each filling one of the
templates, each standard-library only, and each with **every one of its routes
executed by a test** rather than a sampled few. Building them was worth it for
what they found: the tabular pack shipped with the category encoding re-derived
from the held-out rows, which silently multiplied a weight meaning *is a flat*
by a column meaning *is a house*, and made the correct encoder score worse than
the wrong one. That bug is invisible in a template. It is only findable by
running the thing.

7 is the live one. `interactions()` measures where independence breaks and
`pair_effects()` feeds that back into the search, so joint search has its input;
Pareto fronts and learned cost models do not exist.

---

## The honest bit

The strongest thing about this project is not the graph model — it is the
discipline that surrounds it: capabilities that cannot be claimed without being
implemented, candidates that cannot be quietly dropped, metrics that carry their
provenance, scores whose parts sum to the whole, verification that is separate
from the thing it verifies.

The weakest thing is that the graph model is a chain, and a lot of careful work
sits on top of a shape that cannot express the problems it claims to
generalise to. That is fixable, and it should be fixed before anything else is
built on top of it.
