# Changelog

Notable changes. Dates are the release date; the format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) loosely.

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
