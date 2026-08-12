# Changelog

Notable changes. Dates are the release date; the format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) loosely.

## [Unreleased]

### The integrity layer moved out

`assay` is a new top-level package holding the half of this project that is not
about browsers: the pipeline taxonomy, the obligations an evaluation owes,
controls on the harness itself, and a judge audit. Standard library only, and
it imports nothing from `browsergraph` —
`tests/test_assay.py::test_assay_imports_nothing_from_browsergraph` reads the
AST to keep it that way, and **caught a real dependency the first time it ran**:
`Loop.fold_into` imported `browsergraph.evidence` inside the function body,
which felt like it avoided the dependency and did not. An evidence store now
advertises `observation_type` and the writer asks rather than imports.

- **`assay.judge`** (new) — audit a model used as a grader. Agreement with
  people above chance, rubric sensitivity, length bias, position bias, self
  preference, and one `audit()` over the lot. `CANNOT_CHECK` is a verdict, not
  an error: a single-class human sample cannot validate a judge, and reporting
  that as the judge's fault convicts it of the evaluation's own sampling.
  `python -m assay.cli judge --data labels.csv` needs nothing else from this
  project, and exits non-zero so it can go in CI.
- **`assay.controls`** (new) — null, negative, positive and shuffle controls as
  values, with `chance_level()` defaulting to the majority baseline rather than
  1/k. The rule it enforces: **a harness with no controls cannot return better
  than PROVISIONAL.**
- **`assay.obligations`** — was `browsergraph.duecare`, renamed because Due Care
  is also an unrelated product and one name for two things is how a search for
  either finds neither.
- **`assay.taxonomy`** — was `browsergraph.taxonomy`. The map is a claim about
  engineering work rather than a fact about this library, so any pipeline
  library can now report coverage against the same forty-one shapes.
- The old import paths still work and will keep working; twenty-three published
  notebooks use them. A test asserts the shims re-export the *same objects*, not
  copies.

### Added

- **`casestudies.py` and `docs/CASE_STUDIES.md`** — twelve real findings, each
  with a `run()` that reproduces its numbers, and a test that every figure the
  prose quotes is a key the code actually returns. The document is generated;
  editing it by hand fails a test that says how to regenerate it. Writing them
  found four studies that did not demonstrate what their titles claimed — two
  quoted numbers the code contradicted, one compared a system with an identical
  copy of itself, and one counted attack variants where it meant attack
  families.
- **`clean` pack** (13 packs, from 12) — filling the `data.clean` template, so
  `condition.clean` moves from a shape to code. Five steps, 72 routes. What it
  shows, all measured: `repair.drop` deletes the twelve rows holding the
  seventeen problems, `verify.recheck` re-reads the result, finds zero
  remaining, and reports ok — **an empty table is perfectly clean**, and the
  `rows_before`/`rows_after` line is the only thing in the graph that says so.
  `repair.impute` fixes 5 of 17 and the fixed-only ledger reports "5 fixed"
  with no denominator, while `verify.count` returns ok on a frame with twelve
  problems left in it.
- **`browsergraph cases`** and **`examples/14_audit_a_judge.py`**.

### Fixed

- `Evidence` now advertises `observation_type`, so a writer outside the package
  can build the record type the store accepts instead of importing it.


The theme is **the shapes engineering work comes in, and what an evaluation
owes**. 0.4.0 could express and run a graph. It could not tell you whether your
problem was one of the shapes it knew, and it had no answer to "would this
harness have noticed if the system were broken".

### Added

- **`taxonomy.py`** — 41 pipeline categories in 9 families, classified by
  **shape and silent failure mode** rather than by subject matter. Two jobs are
  the same category when they have the same graph shape and go wrong the same
  way; they are different categories when a correct implementation of one is a
  silently broken implementation of the other. Every category records how it
  fails *while reporting success*, and a test refuses to let one exist without
  that field. Coverage is counted from the template and pack registries on
  every call, so the 28 gaps are real gaps; `OUT_OF_SCOPE` names what the map
  deliberately excludes and why. `browsergraph taxonomy` and
  [docs/PIPELINE_TAXONOMY.md](docs/PIPELINE_TAXONOMY.md).
- **`duecare.py`** — what an evaluation owes, as values rather than habits.
  Nine obligations, each discharged with evidence, waived with a stated reason,
  failed, or visibly outstanding. A verdict computed while a blocking
  obligation is outstanding is `PROVISIONAL` — not a soft pass, and `ok` is
  false for it. `waive()` refuses an empty reason. "Could not check" is kept
  distinct from "checked and came back no": a single-class human sample cannot
  validate a grader, and reporting that as the grader's fault would convict it
  of the evaluation's own sampling. The ledger digest hashes the *standard*
  rather than the score, so `compare()` refuses two numbers produced under
  different obligations. `Loop` makes each round's failures permanent
  regression cases, folds outcomes into route evidence, and reports **new**
  failures per round because a falling total is also what deleting the hard
  cases looks like.
- **28 new templates**, taking the catalogue from 11 to 39: cleaning,
  imputation, entity resolution, merging, reference/geographic/temporal and
  place-and-time enrichment, EDA, anomalies, clustering, user flows, feature
  engineering, model bake-off, policy learning, synthetic tabular data,
  synthetic corpora, adversarial generation, document assembly, evaluation
  harness, LLM-as-judge, red team, supervisor-worker agents, tool use, drift
  monitoring, front-end rendering, image conditioning and stream windowing.
  The compiler rejected six of them on first instantiation, at a port, with a
  reason — a multi-output node needs its edge to name which port it leaves by.
- **Eight new packs**, taking the registry from 4 to 12: `harness`, `judge`,
  `redteam`, `agents`, `geo`, `spacetime`, `synth`, `models`. Every pack
  docstring carries a table of measured numbers and `tests/test_packs.py` turns
  each of those sentences into arithmetic.
- **Examples 09-13** — a defensible evaluation and its feedback loop;
  supervisors and workers; place and time; synthetic data; and finding your
  shape in the taxonomy.

### Findings from building it

Kept because the packs exist to show them, and each one is documented where it
happened rather than smoothed away.

- A **depth-three regression tree scored worse than predicting the mean** on
  the threshold dataset, because one-hot encoding drops a reference level and
  the tree then needs two of its three splits to isolate the dropped category.
  The encoding a linear model requires is not the encoding a tree requires.
- **Boosting depth-one stumps is an additive model** and cannot express an
  interaction at all, so it did no better than the linear fit on data built
  from one. The base learner is at depth two for that reason.
- A **detector fitted to its own attack set is wrong in both directions**: it
  fires on attempts the guard successfully blocked and is silent on the four
  families that got through.
- **`utility.tsts` did not flatter the marginal generator**, because a
  generator that destroys every relationship destroys it for itself too. A
  fifth generator was added — internally perfect, externally useless — because
  that is the case train-on-synthetic-test-on-synthetic cannot see through.
- A **human label belongs to a response, not to a case.** The first draft of
  the harness pack attached one to each case, which silently made it a label
  about whichever system happened to run.

### Changed

- `enrich.geo`'s `attach` slot grew a second output port. An enrichment that
  filters is a filter, and the rows it would not code now leave by their own
  port rather than out of the count.
- `tests/test_examples.py` globbed `0*.py`, which stopped covering the examples
  the moment there were more than nine.

## [0.4.0] — 2026-08-10

The theme is **running it, and looking at what happened**. 0.3.0 could describe a
graph, check it and choose a route through it. It could not execute one, and it
could not draw one without a domain baked into the drawing.

### Added

- **An executor.** `execute.py` runs a compiled plan against real functions:
  map steps, branches, parallel workers, fallbacks, a cache, artifacts with
  content hashes, and receipts for failures too. A step records `started` as
  well as `seconds`, so a picture of a run can show two steps overlapping
  rather than stacking them end to end.
- **`solve`.** One call that tries routes, runs them, judges the **output** and
  returns a champion *and* a fallback. Judging is a separate argument because
  "did it work" must not mean "did it not raise" — a route returning an empty
  record passes that test with full marks. A champion with no runner-up is a
  single point of failure dressed as a result.
- **Pictures of any workbench.** `viz.py`: `dag`, `route_space`, `funnel`,
  `evidence`, `timeline`, `scoreboard` and `trend`, plus Mermaid, JSON and
  matplotlib renderings and a self-contained HTML report. Domain-neutral — it
  takes a graph and knows nothing else, which is the same claim the core makes,
  tested a second way. Supersedes `spacemap` and `planmap`, which draw browsers.
- **`browsergraph draw` and `browsergraph solve`.** Both of the above were
  reachable only from Python. A library arguing "look at the shape before you
  believe it is that shape" should not need a script to look.
- **Problem templates.** `templates.py`: eleven skeletons across nine domains,
  each carrying the anti-patterns that shape it.
- **Bounded execution.** `bounded.py` gives a step its own process with a clock
  and a memory ceiling. **Lifecycle isolation, not a sandbox**, and it says so
  in its first paragraph: it contains a runaway step, not a hostile one.
- **A tamper-evident journal.** `journal.py` hash-chains receipts, so evidence
  survives the process and an edited history is detectable.
- **Model-guided exploration.** `explore.py`. The model proposes, the compiler
  disposes: a suggestion that does not type-check or that policy refuses is
  recorded as refused, so a model quietly ignored looks different from one
  quietly followed.
- **`quick.py`.** The thirty lines every notebook was pasting, including
  `passthrough` — doing nothing is a candidate, not a missing step.
- **`bridge.py`.** A workbench becomes a `solutiongraph` program graph, and the
  strict compiler checks it. Honest about what the translation cannot know.
- **`computation_count`.** How many distinguishable things a graph can do,
  counting each way a branch can go — a different question from `route_count`,
  which stays the plain product because that is what the searcher ranges over.
- **`search.eligible_routes`** and **`evidence.per_step_bits`**.

### Fixed

- **A run's verdict never reached the per-candidate posteriors.** `solve` stamps
  the judge's answer onto `receipt.ok`, and `from_receipt` read only `step.ok`.
  A reader returning an empty list raises nothing, so after six runs the reader
  that produced two records and the reader that produced none had identical
  posteriors and every per-step chart read flat zero.
- **`solve` reported a champion "out of 48 possible"** on a space it could only
  ever draw 24 routes from: `route_count` summed over branch paths while the
  searcher took the product.
- **`solve` could not cover a small space.** `within` enumerates when the space
  fits its budget, so it returns the same winner however the seed moves;
  `attempts=6` on a four-route graph produced two attempts and stopped silently.
- **The clamp that made search blind.** `min(1.0, ...)` on the optimism bonus
  saturated every candidate whose prior plus bonus reached 1.0 — which, since an
  undeclared prior *is* 1.0, was almost all of them. Tried and untried scored
  identically and the search stopped exploring.
- **`interactions()` compared a route outcome against `rate(a) * rate(b)`**, a
  category error rather than a tuning problem: on data built with no interaction
  at all it reported eleven. Now both sides are whole routes of the same shape.
- **Discovery and the compiler disagreed about types** — one used equality, the
  other the subtype lattice — so a notebook reported zero legal routes forever.
- **`compile_route` never checked candidate ports**, despite a comment saying it
  did.
- **Exhaustive search materialised the whole product**, which took 53GB and the
  machine with it on a 3.8-trillion-route space. It now refuses above a limit,
  with the number in the message, and streams below it.
- **Artifacts were detected by name rather than by content**, so a second run
  over the same folder reported that it had written nothing.

## [0.3.0] — 2026-08-10

The theme is **being able to say why**. A run that reports success, a picture that
shows one option where sixty exist, and a scorer whose weights mean nothing all fail
the same way: they are unfalsifiable.

### Added

- **The universal graph model.** `manifest.py` and `workbench.py`: portable node
  manifests, atomic candidates, ordered stages, complete routes, typed feedback
  channels and optimization profiles, with a validator that returns every problem
  rather than the first. See [UNIVERSAL_GRAPH_SYSTEM.md](UNIVERSAL_GRAPH_SYSTEM.md),
  and [HOW_IT_WORKS.md](HOW_IT_WORKS.md) for the plain-English version.
- **Sub-steps.** Stages decompose recursively. "Acquire inputs" is not one decision,
  it is three — and drawing it flat hid 44,343× of the search space. Pooling each
  stage into one choice counts 85,747,200 routes; the sub-steps expose
  3,802,314,700,800.
- **Policy gating and route search.** `policy.py` gates *before* anything is scored —
  a candidate lacking a permission is unavailable, not low-scoring — and `search.py`
  runs greedy, beam or exhaustive, always reporting how much of the space it examined.
- **Receipts.** `receipt.py` records route, spec, environment, per-step timing,
  artifacts with content hashes, which steps verified, whether anything mutated
  unchecked, and a pasteable replay line. Written for failures too.
- **A capability handshake.** Each engine declares what it can do beyond the twelve
  core methods; a graph is checked against that *before* a browser launches, and the
  refusal names the engines that could run it. A test cross-checks every declaration
  against the method that implements it.
- **Fourteen nodes.** `press`, `select_option`, `upload`, `download`, `use_frame`,
  `cookies`, `set_viewport`, `save_pdf`, plus `wait_stable`, `a11y_tree`,
  `assert_text`, `assert_url`, `attribute` and `count`, which need no capability.
- **A model router.** Ten roles routed to the right model with a recorded reason,
  instead of one default for every job.
- **OCR.** Five interchangeable backends, and `has_text` — which caught a WebKit
  render that laid out perfectly and drew not one glyph.
- **Binary fetching.** `fetch.py` gets a driver matched to the browser that will
  actually launch, from Chrome for Testing, Mozilla and Microsoft.
- **A studio, a static graphic and a Pages site.** Five synchronized projections in
  one offline file; `routegraph.py` renders the same data as a plain SVG for READMEs.

### Fixed

- **Every objective profile ranked identically.** The scorer combined raw values, so
  a latency in the thousands swamped a quality in [0,1] whatever the weights said.
  "Balanced" was secretly speed-only.
- **Beam search bought nothing.** It renormalized at every step, so the yardstick
  moved under the search: beam matched plain greedy at width 1, 8, 32, 128 *and* 512.
- **Ranking was O(n²)**, making an exhaustive pass over 122,472 routes correct and
  unreachable.
- **The driver came from PATH, not from the browser being launched** — on a machine
  with three Chrome majors installed, which one worked depended on PATH order.
- **Playwright's frame switch was decorative**, storing a handle nothing consulted.
- **`Attribute` read only attributes**, so `value` after an upload returned null
  instead of the filename.

## [0.2.0] — 2026-08-10

The theme of this release is **things that were declared and did not work**, which is the
worst state for a capability table to be in: the validator accepts your spec and the
failure arrives much later.

### Added

- **Contracts, enforced at three moments.** A node's declarations are load-bearing — the
  linter trusts `mutates`, the scheduler trusts `reads`/`writes` — so they are checked at
  import (`Node.__init_subclass__`), at composition (`graph.audit()`) and at run time
  (`nodes.checked.Checked`, which catches a node that clicks while declaring
  `mutates=False`). See `CONTRACTS.md`.
- **CDP-native engines.** `nodriver`, `zendriver` and `pydoll` were in the capability
  tables with no adapter. They are async while `BrowserPort` is deliberately synchronous,
  so each instance owns a private event loop on its own thread.
- **`browsergraph bootstrap`.** Probe → pip → browser binary → system libraries → a Chrome
  already on `PATH`, re-launching after every step, because an installer's exit code is
  not evidence.
- **Binary resolution** (`browsergraph.binaries`). What is on `PATH` is frequently a
  wrapper script a driver cannot launch.
- **Two diagrams.** `browsergraph space` draws the configuration space; `browsergraph
  planes` draws the architecture — task planes, interchangeable candidates, and the route
  moving as evidence arrives. Both render statically and interactively.
- **Typed edges** (`EdgeKind`), `graph.to_mermaid()`, `graph.to_html()`,
  `browsergraph nodes`, `browsergraph graph`.
- **HTTP API tests**, an entrypoint boot test, and mypy across the package.

### Fixed

- **A failed Playwright launch poisoned the whole interpreter.** `sync_playwright().start()`
  parks a greenlet inside an asyncio loop; if the launch then failed it was never unwound,
  and every later sync-playwright call in the process died through no fault of the caller.
- **`engine=playwright` did not work in any notebook.** Jupyter, Kaggle and Colab run cells
  inside an asyncio loop, which the sync API refuses. The adapter is now driven from a
  worker thread — no subprocess, no virtualenv, no setup.
- **Escalation never reached its alternatives.** A retryable failure re-queued the failing
  spec with no cap, so a timeout on an engine that could never succeed consumed every
  attempt. Retries are bounded per spec, and a missing element on an engine with no
  JavaScript runtime now suggests a *different engine* rather than a longer wait.
- **Selenium could not drive Firefox at all** — Ubuntu's `/usr/bin/firefox` is a snap
  wrapper script, and the driver was being handed Chrome's flag syntax.
- **Every Firefox session leaked a geckodriver process.** A snap-confined process cannot be
  signalled even by its owner, and selenium swallows its own teardown failure.
- **The default `LLMConfig` 404'd.** It named a specific model, so a healthy Ollama with
  different models pulled produced `HTTP Error 404`. Models are now resolved from the host
  by capability, including the `name` → `name:tag` case.
- **`IsolatedBrowser` always reported an empty `video_path`** — artifacts were queried
  before the close that populates them.
- **Phone extraction invented a number on python.org**, whose homepage prints a Fibonacci
  series. Nine bare digits, and numbers flanked by other numbers, are rejected.
- **`focus` reported negative savings** — it measured against the chunk total rather than
  the input, and inflated when nothing needed cutting.
- **`doctor` reported `engine:http` as missing** on a machine where it worked, because
  module names and pip requirement strings were the same table.
- Chromium now starts in containers (`--no-sandbox`, `--disable-dev-shm-usage` when
  running as root), and WebKit runs given its system packages.

### Changed

- `LLMConfig.model` defaults to empty, meaning *resolve from the host*.
- `LLMConfig.from_env()` reads `OLLAMA_HOST` / `OLLAMA_API_KEY` / `OLLAMA_MODEL`.
- `ENGINE_IMPORT` holds importable module names; `ENGINE_REQUIREMENT` holds pip strings.
- `mutates` / `verifies` / `interacts` / `needs_browser` are per-instance, so composite
  nodes can derive them from their children.
- CI lints `tests/` as well as the package.

## [0.1.0] — 2026-08-09

First public release. Graphs of nodes over a dimension space, a `BrowserPort` seam across
engines, the BG001–BG009 linter, escalation and learning, token reduction, deterministic
extraction, per-engine isolation, plugins, HTTP API, Docker and Kubernetes manifests.
