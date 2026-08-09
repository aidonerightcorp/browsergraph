"""What a node promises — expressed as data, and checked at three moments.

A node's contract is the set of claims it makes about itself: the kind it is
registered under, the context keys it reads and writes, whether it needs a
browser, whether it changes remote state. Everything else in this library
trusts those claims rather than re-deriving them:

* the linter reasons about `mutates` / `verifies` (BG003, the rule that exists
  because 551 "successful" sends produced zero posts),
* the scheduler decides what may run concurrently from `reads` / `writes`,
* config- and plugin-driven construction resolves nodes by `kind`,
* the planner estimates cost from `uses_llm` and `needs_browser`.

A claim that has drifted from the behaviour is therefore not a cosmetic
problem. It silently disables the checks built on top of it — the most
expensive kind of bug this project has already paid for once.

So contracts are enforced at all three moments where that is possible, because
none of them subsumes the others:

    definition   `check_class`, called from `Node.__init_subclass__`, so a
                 malformed node fails at import — not thirty seconds into a
                 live browser session.
    composition  `audit(graph)`, so a graph whose nodes individually typecheck
                 but do not fit together is caught before it runs.
    execution    `Checked`, which verifies pre- and postconditions on each run.
                 This is the only one that catches a declaration that was
                 correct when written and has since diverged from the code.

This module deliberately imports no Node class. Contracts are read structurally
(`getattr`), so plugins, wrappers and third-party nodes are describable without
inheriting from anything — the same reason `BrowserPort` is a Protocol.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Node kinds are snake_case identifiers: they appear in JSON graph configs,
#: CLI arguments and plugin manifests, so they must survive all three.
KIND_RE = re.compile(r"^[a-z][a-z0-9_]*$")

#: Flags every node carries, and the type each must be.
FLAGS = ("needs_browser", "mutates", "verifies", "interacts", "uses_llm")

#: Tuple-of-string declarations.
KEY_TUPLES = ("reads", "writes")


class ContractError(TypeError):
    """A node's declaration is malformed. Raised at class-definition time."""


@dataclass(frozen=True)
class Contract:
    """The machine-readable description of one node.

    Frozen because it is a description, not state: two nodes with the same
    contract are interchangeable to everything upstream of them, and that
    property is worth being able to rely on (`==` is used by the plugin format
    to detect a node whose interface changed between versions).
    """

    kind: str
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    needs_browser: bool = True
    mutates: bool = False
    verifies: bool = False
    interacts: bool = False
    uses_llm: bool = False
    selector: str = ""
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "name": self.name,
            "reads": list(self.reads), "writes": list(self.writes),
            "needs_browser": self.needs_browser, "mutates": self.mutates,
            "verifies": self.verifies, "interacts": self.interacts,
            "uses_llm": self.uses_llm, "selector": self.selector,
        }

    def describe(self) -> str:
        bits = [f for f in FLAGS if getattr(self, f)]
        io = []
        if self.reads:
            io.append("reads " + ",".join(self.reads))
        if self.writes:
            io.append("writes " + ",".join(self.writes))
        tail = f"  [{'; '.join(io)}]" if io else ""
        return f"{self.kind}({' '.join(bits)}){tail}"

    @property
    def risky(self) -> bool:
        """Changes remote state without checking that it worked."""
        return self.mutates and not self.verifies


def contract_of(obj: Any) -> Contract:
    """Read the contract off a node class *or* instance.

    Instances, not just classes: composite nodes (Branch, ForEach, Retry,
    Healing) compute `mutates`/`writes` per instance by aggregating children,
    so a wrapped Click must still report that it mutates. Reading only the
    class would quietly lose that and disarm BG003 on every wrapped node.
    """
    def get(attr: str, default: Any) -> Any:
        val = getattr(obj, attr, default)
        # Composite nodes expose `writes`/`mutates` as properties. Read off the
        # *class* those come back as property objects, not values — describing a
        # class is still useful (docs, registries), so fall back to the default
        # rather than raising on something that only has a value per instance.
        return default if isinstance(val, property) else val

    return Contract(
        kind=str(get("kind", "")),
        reads=tuple(get("reads", ())),
        writes=tuple(get("writes", ())),
        needs_browser=bool(get("needs_browser", True)),
        mutates=bool(get("mutates", False)),
        verifies=bool(get("verifies", False)),
        interacts=bool(get("interacts", False)),
        uses_llm=bool(get("uses_llm", False)),
        selector=str(get("selector", "") or ""),
        name=str(get("name", "") or ""),
    )


# --- definition-time enforcement -------------------------------------------

def check_class(cls: type, *, base_kind: str = "node") -> list[str]:
    """Structural problems with a node class. Empty list means it conforms.

    Returned rather than raised so callers can choose: `Node.__init_subclass__`
    raises (a malformed node must not reach a registry), while tooling that
    audits third-party plugins prefers to report all of them at once.
    """
    problems: list[str] = []
    name = getattr(cls, "__name__", repr(cls))

    if getattr(cls, "abstract", False):
        return problems      # an intermediate base is not required to be runnable

    kind = getattr(cls, "kind", "")
    if not kind or kind == base_kind:
        problems.append(
            f"{name} does not set `kind`; it would be unregisterable and would "
            f"collide with every other undeclared node")
    elif not KIND_RE.match(str(kind)):
        problems.append(
            f"{name}.kind = {kind!r} is not snake_case; kinds appear in JSON "
            f"configs, CLI arguments and plugin manifests")

    # The classic missing-comma bug: `writes = ("url")` is the string "url",
    # and every consumer iterating it silently sees the characters u, r, l.
    for attr in KEY_TUPLES:
        val = getattr(cls, attr, ())
        if isinstance(val, str):
            problems.append(
                f"{name}.{attr} = {val!r} is a string, not a tuple — a missing "
                f"comma. Consumers would iterate it as individual characters. "
                f"Write ({val!r},)")
        elif not isinstance(val, (tuple, list, property)):
            problems.append(f"{name}.{attr} must be a tuple of str, got {type(val).__name__}")
        elif not isinstance(val, property) and not all(isinstance(k, str) for k in val):
            problems.append(f"{name}.{attr} must contain only str")

    for flag in FLAGS:
        val = getattr(cls, flag, False)
        if not isinstance(val, (bool, property)):
            problems.append(
                f"{name}.{flag} must be a bool, got {type(val).__name__} — the "
                f"linter and scheduler branch on it")

    run = getattr(cls, "run", None)
    if run is not None and getattr(run, "__isabstractnode__", False):
        problems.append(f"{name} does not implement run(ctx)")

    if getattr(cls, "interacts", False) and not hasattr(cls, "selector"):
        problems.append(
            f"{name} declares interacts=True but has no `selector`; BG004 "
            f"cannot check that the element was waited for")

    return problems


# --- composition-time enforcement ------------------------------------------

@dataclass
class AuditFinding:
    node: str
    problem: str
    hint: str = ""

    def __str__(self) -> str:
        return f"{self.node}: {self.problem}" + (f"  ({self.hint})" if self.hint else "")


@dataclass
class Audit:
    """The result of checking a whole graph's contracts against each other."""
    findings: list[AuditFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def text(self) -> str:
        return "\n".join(str(f) for f in self.findings) or "all contracts satisfied"


def audit(graph: Any, *, seed_keys: tuple[str, ...] = ()) -> Audit:
    """Check that every key a node reads is written before it runs.

    Individually valid nodes can still be wrongly *ordered*: a node reading
    `heading` placed before the node that writes it typechecks fine and fails
    at run time with an empty value — which usually looks like a bad selector
    rather than a bad graph, and gets debugged in the wrong place.

    `seed_keys` are values the caller injects into the context up front.
    """
    findings: list[AuditFinding] = []
    produced: set[str] = set(seed_keys)

    try:
        order = graph.topo()
    except Exception:                       # a cyclic graph is the linter's job
        return Audit(findings)

    for key in order:
        node = graph.nodes[key]
        c = contract_of(node)
        for want in c.reads:
            if want not in produced:
                writers = [k for k in order
                           if want in contract_of(graph.nodes[k]).writes]
                hint = (f"written by {writers[0]}, which runs later — reorder"
                        if writers else
                        f"nothing in this graph writes {want!r}")
                findings.append(AuditFinding(key, f"reads {want!r} before it exists", hint))
        produced.update(c.writes)

    return Audit(findings)


def describe_all(nodes: Any) -> str:
    """A table of contracts — used by `browsergraph nodes` and the docs."""
    rows = [contract_of(n) for n in nodes]
    width = max((len(r.kind) for r in rows), default=4)
    out = []
    for r in sorted(rows, key=lambda r: r.kind):
        flags = ",".join(f for f in FLAGS if getattr(r, f) and f != "needs_browser")
        out.append(f"  {r.kind:<{width}}  reads={list(r.reads)!s:<22} "
                   f"writes={list(r.writes)!s:<26} {flags}")
    return "\n".join(out)
