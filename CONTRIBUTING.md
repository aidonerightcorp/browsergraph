# Contributing

Issues and pull requests are welcome. The most useful contributions are a **new engine
adapter**, a **new node**, or — best of all — **a page that breaks something**.

## The house rule

Every check in this library reads a node's own declarations, so the bar for a change is
not "the tests pass". It is: *would this still be true on a page nobody has seen?*

Several rules here exist only because a real page produced them — python.org printing a
Fibonacci series that matched a phone number pattern, Hacker News linking a dozen GitHub
repositories that are nobody's profile. **A failing real-world example is worth more than
a feature request.** Open an issue with the URL and what came back.

## Setup

```bash
git clone https://github.com/aidonerightcorp/browsergraph.git
cd browsergraph
pip install -e ".[dev]"

pytest -q                                  # browser suites skip when absent
mypy browsergraph --ignore-missing-imports
ruff check browsergraph tests
```

CI runs the suite on Python 3.10–3.13, per-engine jobs for playwright/patchright/selenium,
an isolated-environment job, and lint. All four must be green.

## Adding an engine

1. **Capability tables** — `browsergraph/dimensions/capability.py`: `ENGINE_FAMILY`,
   `ENGINE_REQUIREMENT` (the pip string), `ENGINE_IMPORT` (importable module names, a
   tuple — these are different things and conflating them made `doctor` lie once),
   `ENGINE_BINARIES`, `ENGINE_RUNS_JS`.
2. **Adapter** — if it belongs to an existing family (playwright / selenium / cdp), the
   family adapter probably covers it. Otherwise add one satisfying `BrowserPort`.
3. **Route it** — `browsergraph/drivers/__init__.py`.
4. **Test it live** — add it to `tests/test_engines.py`; the conformance suite runs the
   same graph and the same assertions on every installed engine.

`test_every_declared_engine_can_be_routed` fails if an engine is declared with no adapter,
so a half-added engine cannot merge.

## Adding a node

```python
from browsergraph.nodes.base import Node, register

@register
class Highlight(Node):
    kind = "highlight"          # snake_case; it appears in JSON configs and the CLI
    interacts = True            # touches an element
    writes = ("highlighted",)   # note the comma — ("x") is a string, not a tuple

    def __init__(self, selector: str, name: str = ""):
        super().__init__(name)
        self.selector = selector

    def run(self, ctx):
        ctx.page.eval_js(f"document.querySelector({self.selector!r}).style.outline='2px solid red'")
        ctx.data["highlighted"] = self.selector
        return ctx
```

The declarations are load-bearing, not documentation: the linter decides whether a graph
verifies its mutations by trusting `mutates`, and the scheduler parallelises on
`reads`/`writes`. A node that misdeclares itself does not fail — it silently switches
those checks off. `Node.__init_subclass__` rejects a malformed declaration at import;
`nodes.checked.Checked` catches one that has drifted from the code at run time. See
[CONTRACTS.md](CONTRACTS.md).

A node also lands on an architecture plane because of what its contract says — declaring
`mutates` puts it on *act* with no other wiring. See [planmap](browsergraph/planmap.py).

## Style

Match the surrounding code. Two things it does consistently:

* **Comments explain why, not what.** Especially where the obvious thing is wrong — a
  reader should learn what was already tried.
* **Failure messages name the fix.** `binary is not a Firefox executable` is a true
  message that helps nobody. Say what to do.

Tests are named as sentences about behaviour, and their docstrings carry the incident that
motivated them where there is one.

## What gets declined

* Anything that makes a run *look* more successful than it was — silently swallowing an
  error, defaulting a missing value, or reporting a saving that did not happen.
* Silent capability downgrades. A vision job answered by a text model returns confident
  fiction; refusing is correct.
* Hardcoded defaults that only work on the machine they were written on.
