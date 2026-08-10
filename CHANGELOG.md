# Changelog

Notable changes. Dates are the release date; the format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) loosely.

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
