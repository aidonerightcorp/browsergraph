---
name: model-solution-graph
description: Model, implement, validate, or benchmark a software problem using the Universal Node Graph architecture. Use for decomposing any workflow, DAG, service, pipeline, agent, data/ML/image/document/browser system, or application into semantic slots with multiple interchangeable candidates; defining strict node ABIs; compiling valid routes; designing exhaustive or budgeted search; and creating evidence-based optimization experiments.
---

# Model a Universal Node Graph solution

Read `../../../UNIVERSAL_NODE_GRAPH_SPEC.md` before changing the ontology. Read
`../../../RESEARCH_FOUNDATIONS.md` when making a new architectural tradeoff.

## Build the semantic program

1. State the task independently of any implementation.
2. Define typed external inputs, outputs, hard policy/resource limits, and an
   independent success oracle.
3. Decompose the task into semantic obligations. Split an obligation again when
   its candidates would not be genuine substitutes.
4. Group related obligations into nested subgraphs/submatrices for human
   navigation without hiding the atomic slots.
5. Represent branch, loop, map, reduce, barrier, and long-lived control as
   structured/composite slots. Keep the current semantic level acyclic.
6. Connect named typed ports. Insert explicit adapter nodes for conversions.

## Build the implementation registry

1. Define one `NodeSpec` per atomic implementation family.
2. Declare version and implementation digest, ports/cardinality, parameters,
   runtime/entrypoint, capabilities, effects, permissions, determinism,
   idempotency, contracts, failure modes, resources, and verifier.
3. Expand finite parameter choices into visible `Candidate` bindings.
4. Keep empirical performance in evidence/belief objects, never the node ABI.
5. Run full compiler admission. Preserve every admit/reject decision and reason.

## Compile and search

1. Compile at least one route before writing an executor.
2. Treat compiler validity as a hard boundary and belief scores as ordering only.
3. Provide a fast prior recommendation, a bounded anytime search with an
   explicit budget, and streaming exhaustive enumeration without a hidden cap.
4. Freeze exact versions, digests, parameters, topology, program, and registry
   into a content-addressed plan.
5. Report evaluated, eliminated, skipped, and unvisited route counts.

## Design the experiment

1. Select a fixed baseline and representative task cases.
2. Reserve holdout cases; repeat stochastic routes with recorded seeds.
3. Measure acceptance, quality, cost, latency, reliability, policy, and resource use.
4. Preserve plan/input/environment/artifact identities in immutable receipts.
5. Compare Pareto fronts and best-so-far-vs-budget curves.
6. Learn priors with evidence counts and uncertainty. Label observational
   attribution as correlation, not causation.
7. Choose fallbacks for independent failure modes and dependency diversity.

## Verify the implementation

Run:

```bash
pytest tests/test_solutiongraph.py -q
ruff check solutiongraph tests/test_solutiongraph.py
```

Reject the change if optimization became an execution step, candidates became
hidden parameters, implicit coercion appeared, authority is inferred, a node
self-verifies consequential output without justification, or a search limit is
not visible in its report.

