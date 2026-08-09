# Architecture

Layout, and the conventions that keep it extensible.

```
browsergraph/
├── ports.py              BrowserPort protocol + Context — the seam
├── graph.py              Graph (DAG), run(), RunResult
├── dimensions/           the axes a run varies along
│   ├── enums.py            Engine, Binary, Transport, Display, Stealth, LLMControl
│   ├── settings.py         Behavior, Identity, LLMConfig (structured axes)
│   ├── capability.py       per-engine metadata tables
│   ├── spec.py             Spec — one resolved point
│   └── rules.py            validate() — which points can run
├── nodes/                units of work
│   ├── base.py             Node, REGISTRY, register(), make()
│   ├── actions.py          navigate, click, type, wait_for, extract, scroll, screenshot
│   └── llm.py              llm_selector, llm_verify
├── drivers/              engine adapters implementing BrowserPort
│   ├── mock.py             reference implementation, no browser needed
│   ├── playwright_driver.py  playwright / patchright / camoufox
│   └── selenium_driver.py    selenium / selenium_uc / seleniumbase
├── heal.py               self-healing selectors + drift ledger
├── lint.py               static checks on a graph
├── combos.py             enumerate the valid space
├── sample.py             pairwise covering arrays
├── doctor.py             prerequisite checks
├── config.py             YAML/JSON -> Graph + Spec
├── server.py             HTTP adapter (stdlib)
└── cli.py                CLI adapter
```

## Why dimensions is a package and nodes is a package

Both started as single files. `dimensions.py` reached 274 lines carrying five
distinct concerns — the axes, their metadata, structured settings, the Spec,
and the rules — and DIMENSIONS.md proposes nine more axis groups. Splitting by
**concern** (not by individual dimension) means adding an axis touches one file:

- a new axis value → `enums.py`
- a new engine's install/binary facts → `capability.py`
- a new compatibility rule → `rules.py`

One file per dimension would be over-fragmentation: `Display` is six lines and
has no behaviour of its own.

The same reasoning applies to `nodes/`: grouped by **category** (actions, llm,
and later control/flow), not one file per node. A node is typically 20 lines;
a file each would be noise.

## The three layers, and the one-way rule

```
core        ports.py, graph.py, dimensions/     pure, no I/O
adapters    drivers/, server.py, cli.py         translate to/from the outside
extensions  nodes/, heal.py, lint.py            build on core, never on adapters
```

**Nothing in `core` imports from `adapters`.** A node that reached into
Playwright directly would work on one engine and silently break the rest, and
that is precisely the failure mode this design exists to prevent.

## Base classes: what is a Protocol, what is a class

Two different needs, deliberately handled differently.

**`BrowserPort` is a `Protocol`** (structural). A driver need not inherit
anything — it just implements twelve methods. That keeps adapters free of a
dependency on us and makes third-party drivers trivial. It is also why
`MockBrowser` inherits nothing.

**`Node` is a base class** (nominal). Nodes share real behaviour and defaults —
`name`, the `reads`/`writes`/`mutates`/`verifies` declarations, `__repr__` — so
inheritance carries weight rather than just a contract.

Rule of thumb used here: **Protocol when you want implementations you do not
control; base class when you want to give implementations something.**

## Extending

### A new node

```python
from browsergraph.nodes.base import Node, register
from browsergraph.ports import Context

@register                          # makes it available to config-driven graphs
class SelectOption(Node):
    kind = "select_option"         # the key used in YAML/JSON
    interacts = True               # so the linter knows it needs the element
    mutates = True                 # so BG003 requires verification afterwards

    def __init__(self, selector: str, value: str, name: str = ""):
        super().__init__(name)
        self.selector, self.value = selector, value

    def run(self, ctx: Context) -> Context:
        ctx.browser.click(self.selector)   # BrowserPort only — never an engine
        return ctx
```

Declaring `mutates`/`interacts`/`verifies` is not decoration: `lint.py` reasons
about them, so an undeclared node silently opts out of the checks.

### A new engine

1. Add the value to `Engine` in `dimensions/enums.py`
2. Add its rows to `ENGINE_FAMILY`, `ENGINE_BINARIES`, `ENGINE_IMPORT`,
   `ENGINE_REQUIREMENT` in `capability.py`
3. Add any incompatibility to `rules.py`
4. Write an adapter implementing `BrowserPort`
5. Route it in `drivers/build()`

**No node changes.** That is the test of whether the seam is holding.

`test_every_engine_declares_its_metadata` fails if step 2 is skipped, so the
tables cannot silently drift from the enum.

### A new dimension

Add to `enums.py` (or `settings.py` if structured), add a field to `Spec`, add
rules to `rules.py`. `combos.py` and `sample.py` pick it up automatically —
they read axes generically rather than naming them.

## Conventions

- **Stdlib-only core.** Engines are optional extras, so `pip install
  browsergraph` is small and the suite runs with no browser and no network.
- **Lazy adapter imports.** `drivers/build()` imports inside the function so a
  missing engine raises `DriverUnavailable` with the pip command, not an
  `ImportError` traceback.
- **Enums are `str`-valued**, so a Spec round-trips through JSON, YAML and env
  vars with no custom codec.
- **`Spec` is frozen.** Vary it with `dataclasses.replace`, which is what makes
  combination enumeration safe.
- **Failures are explicit.** Nodes call `ctx.fail()` rather than raising; the
  run stops and the reason is in the log. LLM nodes fail rather than guess.
- **Every check carries a fix.** `doctor` and `lint` findings include the
  remedy — a report you cannot act on is noise.

## Testing

`MockBrowser` is the reference `BrowserPort`, so graphs, nodes, linting,
healing and combination sweeps are all testable with no browser. 68 tests run
in under a second, which is what makes the pairwise sweeps practical to run on
every change.

The load-bearing test is
`test_one_graph_runs_across_every_valid_combination` — one graph, every
runnable combination, no per-combination code. If the seam ever breaks, that
fails first.
