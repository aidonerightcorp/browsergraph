# browsergraph

[![CI](https://github.com/aidonerightcorp/browsergraph/actions/workflows/ci.yml/badge.svg)](https://github.com/aidonerightcorp/browsergraph/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Core deps: none](https://img.shields.io/badge/core%20deps-stdlib--only-brightgreen)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-728%20passing-brightgreen)](tests/)
[![Kaggle](https://img.shields.io/badge/Kaggle-run%20it%20now-20BEFF?logo=kaggle)](https://www.kaggle.com/code/taylorsamarel/browsergraph-composable-browser-automation)

**Write a browser automation once. Run it on any engine — or on none.**

Playwright, Patchright, Selenium, undetected-chromedriver, SeleniumBase, nodriver,
zendriver, pydoll, Botasaurus, rebrowser, Camoufox — or `engine=http`, which fetches with
a real browser's TLS fingerprint and no browser at all. Same graph, no code change.

```python
from browsergraph import Engine, Graph, Spec, run
from browsergraph.drivers import build
from browsergraph.nodes.actions import Click, Extract, Navigate, WaitFor

graph = (Graph("quote")
         .add(Navigate("https://example.com"))
         .add(WaitFor("#quote"))
         .add(Click("#quote"))
         .add(WaitFor("#result", name="confirm"))   # <- proves the click landed
         .add(Extract("#result", into="quote")))

spec = Spec(engine=Engine.PLAYWRIGHT)               # swap to HTTP, SELENIUM, ...
print(run(graph, spec, build(spec)).context.data)
```

## Install

```bash
pip install "browsergraph[playwright] @ git+https://github.com/aidonerightcorp/browsergraph.git"
browsergraph bootstrap        # gets a browser actually running, whatever it takes
browsergraph doctor           # what works here, and the command to fix what doesn't
```

The core is **stdlib-only** — every engine is an optional extra, so a graph can be built,
linted and mock-run with nothing installed.

**Try it without installing anything:** the
[Kaggle notebook](https://www.kaggle.com/code/taylorsamarel/browsergraph-composable-browser-automation)
installs a browser, drives it, and shows the screenshots and video it captured.

---

## Why this exists

The interesting problem in browser automation is not clicking things. It is that **a run
which reports success can have accomplished nothing** — and you find out weeks later.

This library was written after an incident where 551 emails reported "sent" successfully
and produced zero posts. Every layer said success. Nothing checked the destination. So the
linter's flagship rule is BG003 — *changes remote state, never verifies the outcome* — and
the rest of the design follows from there.

```python
from browsergraph.lint import lint, report
print(report(lint(graph)))
# [WARN] BG003 click: graph changes remote state but never verifies the outcome
#        — a silent failure will look like success
```

## The architecture

A task decomposes into ordered **stages**. Each stage offers every candidate that
could perform it; a route picks one per stage. That model is written down in full
— generalized past browsers, with portable manifests, contract validation, typed
feedback and optimization profiles — in
[**UNIVERSAL_GRAPH_SYSTEM.md**](UNIVERSAL_GRAPH_SYSTEM.md).

```bash
browsergraph workbench -o studio.html    # 6 stages, 149 candidates, 32,864,832 routes
```

The demonstration registry is domain-neutral on purpose: the same primitives
describe document ingestion, image processing, data cleaning and machine
learning. `Browser adapter` alone expands to **60 atomic candidates**
(5 controllers × 6 binaries × 2 display modes) — because drawing that as one box
hides fifty-nine decisions.

A task decomposes into **planes**. Each plane offers several interchangeable ways to
answer it. A route through them is one candidate solution — and the route is chosen from
evidence, not fixed in advance.

![task planes and candidate routes](docs/architecture.png)

With no evidence the cheapest route wins: `http → dwell → css → click → screenshot →
extract` — no browser, no model. After a few dozen runs against a defended,
JavaScript-rendered site the same machinery picks `patchright → wait_for → healing → …`
and can say why: *6/6 steps measured*.

The planes are **derived from node contracts**, not written down. `click` is on *act*
because it declares `mutates`; adding a node adds a candidate and the diagram changes
with nobody editing it.

```bash
browsergraph planes --demo               # the planes and the chosen route
browsergraph planes --html planes.html   # both routes drawn over all the others
```

## Failure is a first-class path

When a configuration fails, the next one is tried — and the *diagnosis* chooses what to
try next, which is what makes it more than a retry loop.

```
1. http        ok=False  timeout        wait_retry
2. http        ok=False  timeout        wait_retry
3. playwright  ok=True                          → extracted: $49.00
```

A missing element on an engine with **no JavaScript runtime** suggests a different engine,
not a longer wait. Retries are bounded per spec — an unbounded retry never reaches the
rest of the ladder. A terminal diagnosis (CAPTCHA, block) stops immediately rather than
escalating into a ban, and `SiteMemory` puts the winner first next time.

## Every engine, every browser, headless and headed

Measured, not declared — a real launch matrix against a served page.
`browsergraph doctor` reports the same for your machine.

| engine | chromium | chrome | firefox | webkit | brave | headless | headed | xvfb |
|---|---|---|---|---|---|---|---|---|
| playwright | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| playwright_stealth | ✅ | ✅ | — | — | ✅ | ✅ | ✅ | ✅ |
| patchright | ✅ | ✅ | — | — | ✅ | ✅ | ✅ | ✅ |
| rebrowser | ✅ | ✅ | — | — | ✅ | ✅ | ✅ | ✅ |
| selenium | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ | ✅ |
| selenium_uc | — | ✅ | — | — | ✅ | ✅ | ✅ | ✅ |
| seleniumbase | — | ✅ | — | — | ✅ | ✅ | ✅ | ✅ |
| botasaurus | — | ✅ | — | — | ✅ | ✅ | ✅ | ✅ |
| camoufox *(isolated)* | — | — | ✅ | — | — | ✅ | ✅ | ✅ |
| nodriver / zendriver / pydoll *(CDP)* | — | ✅ | — | — | ✅ | ✅ | ✅ | ✅ |
| http *(no browser)* | n/a | n/a | n/a | n/a | n/a | ✅ | — | ✅ |

**126 verified combinations.** What does not work is documented in
[ENGINES.md](ENGINES.md) with the reason — a bare protocol with no client library, a
chromedriver/snap version skew, a dependency that ships broken source.

## What comes in the box

| | |
|---|---|
| **Contracts** | nodes declare what they read, write and mutate — enforced at import, at composition and at run time ([CONTRACTS.md](CONTRACTS.md)) |
| **Linter** | BG001–BG009 over a graph, before a browser starts |
| **Escalation** | diagnose the failure, try the next configuration, remember the winner |
| **Learning** | outcomes generalise site → org → sector → platform → global |
| **Token reduction** | 8 preprocessing strategies, then keyword focus with neighbour expansion |
| **Extraction** | conservative, deterministic contacts / NAICS / articles — no model needed |
| **Politeness** | per-domain, process-wide rate limiting that honours robots `Crawl-delay` |
| **Isolation** | conflicting engines in per-engine virtualenvs, over a worker protocol |
| **Notebooks** | Jupyter/Kaggle/Colab run cells inside an asyncio loop; the sync API is driven from a worker thread so it just works |
| **Universal graph** | portable node manifests, atomic candidates, stage/route validation and a five-view studio — [UNIVERSAL_GRAPH_SYSTEM.md](UNIVERSAL_GRAPH_SYSTEM.md) |
| **Binaries** | fetches a browser or a driver *matched to the browser it will drive* — the fix for "cannot connect to chrome" |
| **OCR (optional)** | read a page from its pixels when the DOM cannot answer — canvas text, baked-in images, and "does this screenshot contain any text at all" |
| **LLM (optional)** | Ollama-compatible; the model is resolved from the host by *capability*, never hardcoded |

## Documentation

| | |
|---|---|
| [QUICKSTART.md](QUICKSTART.md) | first graph, first real browser, first task |
| [UNIVERSAL_GRAPH_SYSTEM.md](UNIVERSAL_GRAPH_SYSTEM.md) | stages, candidates, routes, contracts, feedback, optimization |
| [ARCHITECTURE.md](ARCHITECTURE.md) | the Protocol-vs-base-class seam |
| [CONTRACTS.md](CONTRACTS.md) | what a node promises, and the three moments it is checked |
| [ENGINES.md](ENGINES.md) | every engine, what it is for, and what does not work |
| [DIMENSIONS.md](DIMENSIONS.md) | the axes, and why verification matters most |
| [ISOLATION.md](ISOLATION.md) | conflicting engines in separate virtualenvs |
| [PLUGINS.md](PLUGINS.md) | the open plugin format |
| [CONTRIBUTING.md](CONTRIBUTING.md) | how to add an engine, a node or an extraction path |

## Related

[**extractgraph**](https://github.com/aidonerightcorp/extractgraph) — the other half.
browsergraph *reaches* the page; extractgraph gets the data out of it, with several
independent paths, provenance, and the disagreements kept.

## Contributing

Issues and pull requests welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). The most
useful contributions are a new engine adapter, a new extraction path, or a page that
breaks something.

```bash
pip install -e ".[dev]"
pytest -q                                  # 728 tests; browser suites skip when absent
mypy browsergraph --ignore-missing-imports
ruff check browsergraph tests
```

MIT.
