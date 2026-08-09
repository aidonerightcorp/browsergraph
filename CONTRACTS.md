# Contracts

Every check in this library reads a node's own declarations.

* the linter decides whether a graph verifies its mutations by trusting `mutates`
  (**BG003**, the rule that exists because 551 "successful" sends produced zero posts);
* the scheduler decides what may run concurrently by trusting `reads` / `writes`;
* config- and plugin-driven construction resolves nodes by `kind`;
* the planner estimates cost from `uses_llm` and `needs_browser`.

So a node that misdeclares itself does not fail. **It silently switches those checks
off** — and a check that has quietly stopped running is worse than no check, because
the graph still reports that it passed.

That is why contracts are enforced rather than documented, at all three moments where
enforcement is possible. None of the three subsumes the others.

| Moment | Mechanism | Catches |
|---|---|---|
| Definition | `Node.__init_subclass__` → `contracts.check_class` | a malformed declaration, at import |
| Composition | `graph.audit()` → `contracts.audit` | nodes that are individually valid but wrongly ordered |
| Execution | `nodes.checked.Checked` | a declaration that has drifted from the code |

---

## The contract

```python
from browsergraph.contracts import contract_of

contract_of(Click("#login"))
# Contract(kind='click', reads=(), writes=(), needs_browser=True,
#          mutates=True, verifies=False, interacts=True, uses_llm=False, ...)
```

| Field | Meaning |
|---|---|
| `kind` | snake_case id; appears in JSON configs, CLI arguments, plugin manifests |
| `reads` / `writes` | context keys consumed and produced |
| `needs_browser` | whether a `BrowserPort` is required |
| `mutates` | changes state on the far side (click, type, submit) |
| `verifies` | checks an outcome |
| `interacts` | targets an element, so it must be present |
| `uses_llm` | costs tokens |
| `risky` | derived: `mutates and not verifies` |

Contracts are read **structurally**, with `getattr` — the same reason `BrowserPort` is a
Protocol. A plugin or wrapper is describable without inheriting from anything.

They are read from the **instance**, not the class. Composite nodes (`Branch`, `ForEach`,
`Retry`, `Healing`) derive their flags by aggregating their children, so a wrapped `Click`
still reports that it mutates. Reading only the class would lose that and disarm BG003 on
every wrapped node.

---

## Moment 1 — definition

`Node.__init_subclass__` runs `check_class` when the class statement executes, so the
traceback points at the offending `class`, not at a browser session thirty seconds in.

```python
class Bad(Node):
    kind = "bad"
    writes = ("url")          # ContractError: a missing comma — this is a str
```

That example is the rule worth having on its own. `("url")` is not a tuple; it is the
string `"url"`, and every consumer that iterates it sees the characters `u`, `r`, `l`.
Nothing raises. The graph simply believes three keys exist that never will.

Also rejected: a missing or non-snake_case `kind`, a `run` that was never implemented,
a non-boolean flag, and `interacts=True` on a node with no `selector` for BG004 to check.

Intermediate base classes opt out:

```python
class MyBase(Node):
    abstract = True           # exists to be subclassed, not run
```

`abstract` is never inherited — a subclass must declare it again or be checked.

---

## Moment 2 — composition

Individually valid nodes can still be wrongly *ordered*. A node reading `heading` placed
before the node that writes it typechecks fine and fails at run time with an empty value
— which looks like a bad selector and gets debugged in the wrong place.

```python
graph.audit().text()
# needs_heading: reads 'heading' before it exists  (written by extract, which runs later — reorder)
```

`audit(seed_keys=("start_url",))` accounts for values the caller injects up front.

---

## Moment 3 — execution

The one the other two cannot reach: a declaration that was accurate when written and has
since drifted from the code.

`Checked` wraps a node and, on every run, verifies that

* every declared `read` is present beforehand,
* every declared `write` is present afterwards,
* a node declaring `mutates = False` did not click or type.

The third protects BG003 directly:

```python
from browsergraph.nodes.checked import checked
run(checked(graph), spec, browser)     # strict: raises ContractViolation
run(checked(graph, strict=False), ...) # records; read with violations(graph)
```

```
ContractViolation: sneaky_click: declares mutates=False but called ['click'] —
BG003 cannot see this mutation, so a graph containing it will be reported as
safely verified when it is not
```

Behaviour is observed with `drivers.recording.RecordingBrowser`, which delegates every
call and keeps a log. Failures are recorded **and re-raised**: an observer that swallows
errors changes what it observes.

`Checked` re-exports the inner node's flags and keys, so the linter and scheduler see
exactly what they would have seen without it. A checking layer that changed the analysis
would invalidate the thing it exists to protect.

**Off by default.** A wrapper per node is a real cost and production runs should not pay
it. Turn it on in tests and CI, where a violation is cheap to find and free to fix.

---

## Edges

Edges are typed, because *why* an edge exists is not the same as what it constrains.

```python
Edge(src='navigate', dst='wait_for', kind=EdgeKind.SEQUENCE, reason='')
```

| Kind | Meaning |
|---|---|
| `SEQUENCE` | implied by add order — a scheduler may reorder across it when the data allows |
| `DEPENDENCY` | requested explicitly with `after=` — must be preserved |
| `BRANCH` | taken conditionally by a control node |

Both constrain order identically; collapsing them into a bare tuple loses exactly the
information needed to parallelise safely. `Edge` is a `NamedTuple`, so existing
`for src, dst, *_ in graph.edges` code keeps working.

---

## Inspecting

```bash
browsergraph nodes                        # every node kind and its contract
browsergraph graph g.yaml                 # levels, contracts, audit
browsergraph graph g.yaml --mermaid       # a diagram
browsergraph graph g.yaml --json          # the structure
```

```python
graph.contracts()      # every node's contract, in execution order
graph.to_mermaid()     # mutating nodes red, verifying nodes green
graph.to_dict()        # serialisable structure
```

The diagram colours mutating and verifying nodes deliberately: *"which steps change
remote state, and does anything check them?"* is the question a reader of a browser
automation diagram actually has.
