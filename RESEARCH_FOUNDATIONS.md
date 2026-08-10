# Research foundations and architectural consequences

This is a design synthesis, not a claim that any cited system already implements
Universal Node Graph. Each source contributes a constraint that the architecture
should preserve.

## Compiler and interface foundations

| Primary source | Relevant idea | Consequence here |
|---|---|---|
| [MLIR rationale](https://mlir.llvm.org/docs/Rationale/Rationale/) and [Linalg rationale](https://mlir.llvm.org/docs/Rationale/RationaleLinalgDialect/) | Preserve semantics at multiple IR levels; separate transformation validity from profitability | Task graph, admitted space, belief model, and frozen plan are different IRs; the optimizer cannot decide validity |
| [WebAssembly Interface Types (WIT)](https://component-model.bytecodealliance.org/design/wit.html), [worlds](https://component-model.bytecodealliance.org/design/worlds.html), and [component composition](https://component-model.bytecodealliance.org/design/why-component-model.html) | Language-neutral imports/exports and capability-shaped composition | The node ABI is runtime-neutral; imports, effects, and permissions must be satisfied explicitly |
| [Common Workflow Language 1.2](https://www.commonwl.org/v1.2/CommandLineTool.html) | Versioned, portable input/output descriptions validated before execution | Program and node wire formats are strict, versioned JSON Schemas |
| [Apache Beam model](https://beam.apache.org/documentation/basics/) | A user graph can run through different execution backends | BrowserGraph is one adapter; executor/runtime choice must not leak into semantic meaning |
| [Alloy](https://alloytools.org/about) | Relational constraints can find valid instances and counterexamples | Route constraints and graph invariants should become machine-checkable, with counterexamples rather than vague failures |
| [Hoare, “An Axiomatic Basis for Computer Programming”](https://doi.org/10.1145/363235.363259) | Preconditions and postconditions support reasoning about program properties | Every slot/node carries behavioral contracts; correctness is checked before optimization |
| [Parnas, module decomposition criteria](https://doi.org/10.1145/361598.361623) | Good modularity hides likely-to-change decisions behind stable interfaces | Provider, framework, model, and binary details stay behind the node ABI |

## Execution, replay, and evidence

| Primary source | Relevant idea | Consequence here |
|---|---|---|
| [Temporal workflow determinism](https://docs.temporal.io/workflow-definition) | Replay requires the same command sequence; nondeterministic external work is isolated | Clocks, APIs, models, randomness, and human input are declared effects with recorded decisions |
| [Bazel hermeticity](https://bazel.build/basics/hermeticity) | Isolation plus source identity enables caching, parallelism, and reproducibility | Frozen plans include content digests and exact inputs; undeclared host state is a defect |
| [Reproducible Builds definition](https://reproducible-builds.org/docs/definition/) | Same source, environment, and instructions should recreate identical artifacts | Program, registry, implementation, environment, and input identities are separate receipt fields |
| [W3C PROV-O](https://www.w3.org/TR/prov-o/) | Entities, activities, agents, generation, use, and derivation form an interoperable provenance model | Receipts distinguish plans/artifacts, executions, responsible executors, and derivation links |
| [OpenLineage object model](https://openlineage.io/docs/next/spec/object-model/) | Design-time jobs/datasets differ from runtime run events | Node/program definitions are not run receipts; observations never mutate the definition |
| [OpenTelemetry traces](https://opentelemetry.io/docs/concepts/signals/traces/) | Spans, context propagation, links, status, and attributes reconstruct an end-to-end path | Node receipts need stable parentage, timing, status, links, and semantic attributes |
| [Lamport, *Specifying Systems*](https://www.microsoft.com/en-us/research/publication/specifying-systems-the-tla-language-and-tools-for-hardware-and-software-engineers/) | Safety and liveness properties require explicit temporal specifications | Long-running/composite nodes need lifecycle and progress contracts, not just input/output schemas |

## Search and synthesis

| Primary source | Relevant idea | Consequence here |
|---|---|---|
| [Hyperband](https://jmlr.org/papers/v18/16-558.html) | Allocate resources adaptively and stop weak configurations early | Large route spaces need budgeted multi-fidelity experiments rather than uniform full runs |
| [BOHB](https://proceedings.mlr.press/v80/falkner18a.html) | Combine model-guided proposals with robust budget allocation | Learned priors should guide proposals while an explicit scheduler controls experiment spend |
| [Open Source Vizier](https://arxiv.org/abs/2207.13676) | Distributed black-box optimization, multi-metric objectives, early stopping, transfer | The optimizer must be pluggable and treat route execution as an external evaluation service |
| [Optuna](https://www.kdd.org/kdd2019/accepted-papers/view/optuna-a-next-generation-hyperparameter-optimization-framework) | Dynamic conditional spaces and separate pruning logic | Candidate availability and experiment pruning are separate concerns |
| [egg / equality saturation](https://arxiv.org/abs/2004.03082) | Represent many equivalent programs without prematurely choosing one | Future graph rewrites should preserve equivalence and delay extraction until a cost model is selected |
| [DreamCoder](https://www.pldi21.org/poster_pldi.355.html) | Jointly learn reusable abstractions and a search policy | Repeated successful subgraphs can become versioned composite nodes without erasing their provenance |
| [Shannon, “A Mathematical Theory of Communication”](https://doi.org/10.1002/j.1538-7305.1948.tb01338.x) | Entropy quantifies unresolved uncertainty | Search should report how much of the route space remains unresolved; priors reduce search order, not candidate visibility |

## Coding-agent adoption

The repository uses one short canonical instruction file plus thin adapters:

- [OpenAI Codex `AGENTS.md`](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
  supports hierarchical repository instructions.
- [Claude Code project memory](https://code.claude.com/docs/en/memory) explicitly
  recommends a `CLAUDE.md` that imports `AGENTS.md` to avoid duplication.
- [Gemini CLI context files](https://geminicli.com/docs/cli/gemini-md/) support
  `GEMINI.md` imports, while [workspace Agent Skills](https://geminicli.com/docs/cli/skills/)
  support the interoperable `.agents/skills/` path.
- [GitHub Copilot repository instructions](https://docs.github.com/en/copilot/how-tos/configure-custom-instructions-in-your-ide/add-repository-instructions-in-your-ide)
  support `.github/copilot-instructions.md` and `AGENTS.md`.
- [Cursor rules](https://docs.cursor.com/context/rules-for-ai) and
  [Windsurf rules](https://docs.windsurf.com/windsurf/cascade/memories) recognize
  root-level `AGENTS.md` as project guidance.

The consequence is deliberately boring: `AGENTS.md` is canonical; `CLAUDE.md`,
`GEMINI.md`, and Copilot files import or point to it; detailed graph-modeling
procedure lives in an on-demand Agent Skill. Fewer duplicated instructions mean
less drift and less prompt noise.

## Critical conclusions

1. The product is a compiler and experiment system, not merely a graph viewer.
2. “Everything is a node” is useful only with strict ports, effects, authority,
   failure, replay, and verification contracts.
3. A giant mixed graph is not universal; multiple explicit IRs are.
4. Search-space size is an engineering variable, not an architectural objection.
   Use feasibility filtering, factorized priors, adaptive budgets, and explicit
   exhaustive mode.
5. Learned route quality is contextual and uncertain. Preserve raw evidence,
   report coverage, and avoid causal claims from observational receipts.
6. Fallback quality depends on failure diversity, not simply rank two.
7. The BrowserGraph implementation is valuable precisely because it becomes one
   demanding conformance adapter instead of defining the universal ontology.
