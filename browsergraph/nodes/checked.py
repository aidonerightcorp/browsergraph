"""Runtime contract enforcement: the declaration, checked against the behaviour.

`contracts.check_class` catches a node whose *shape* is wrong at import time.
It cannot catch the more common and more expensive case: a declaration that was
accurate when written and has since drifted from the code. A node that quietly
stopped writing the key it advertises does not fail — it hands the next node an
empty value, which surfaces later as a bad selector or a thin dataset, and gets
debugged in entirely the wrong place.

`Checked` closes that gap by verifying, per run:

    preconditions   every key the node declares it reads is present first
    postconditions  every key it declares it writes is present afterwards
    honesty         a node declaring `mutates = False` did not click or type

The third is the one that protects BG003. The linter decides whether a graph
verifies its mutations by trusting `mutates`; a node that mutates while
declaring otherwise makes the whole rule silently pass.

Off by default — a wrapper per node is a real cost, and production runs should
not pay it. Turn it on in tests and in CI, where a contract violation is
cheap to find and free to fix:

    graph = checked(graph)          # wrap every node
    graph.add(Checked(MyNode()))    # or just the one you are working on
"""
from __future__ import annotations

from browsergraph.contracts import Contract, contract_of
from browsergraph.drivers.recording import RecordingBrowser
from browsergraph.nodes.base import Node
from browsergraph.ports import Context


class ContractViolation(AssertionError):
    """A node did not do what it said it would."""


class Checked(Node):
    """Wraps a node and verifies its contract on every run.

    Transparent by design: it re-exports the inner node's flags and keys, so
    the linter, the scheduler and BG003 see exactly what they would have seen
    without the wrapper. A checking layer that changed the analysis would
    invalidate the thing it is meant to protect.
    """

    kind = "checked"
    needs_browser = False        # delegated; the inner node's value is what counts

    def __init__(self, inner: Node, *, strict: bool = True, name: str = ""):
        super().__init__(name or f"checked({inner.name})")
        self.inner = inner
        self.strict = strict
        self.violations: list[str] = []
        c = contract_of(inner)
        self.mutates = c.mutates
        self.verifies = c.verifies
        self.interacts = c.interacts
        self.uses_llm = c.uses_llm
        self.needs_browser = c.needs_browser

    # keep the inner node's declarations visible to every consumer
    @property
    def reads(self) -> tuple[str, ...]:      # type: ignore[override]
        return tuple(contract_of(self.inner).reads)

    @property
    def writes(self) -> tuple[str, ...]:     # type: ignore[override]
        return tuple(contract_of(self.inner).writes)

    @property
    def selector(self) -> str:               # type: ignore[override]
        return contract_of(self.inner).selector

    def contract(self) -> Contract:
        return contract_of(self.inner)

    def _fail(self, ctx: Context, msg: str) -> None:
        self.violations.append(msg)
        ctx.note(f"CONTRACT {self.inner.name}: {msg}")
        if self.strict:
            raise ContractViolation(f"{self.inner.name}: {msg}")

    def run(self, ctx: Context) -> Context:
        c = contract_of(self.inner)

        missing = [k for k in c.reads if k not in ctx.data]
        if missing:
            self._fail(ctx, f"declares reads={list(c.reads)} but {missing} are not in "
                            f"the context (available: {sorted(ctx.data)})")

        # Record what the node actually asks the browser to do. Restored
        # afterwards so nothing downstream sees the proxy.
        original = ctx.browser
        recorder = RecordingBrowser(original) if original is not None else None
        if recorder is not None:
            ctx.browser = recorder
        try:
            ctx = self.inner.run(ctx)
        finally:
            ctx.browser = original

        if recorder is not None and recorder.mutated and not c.mutates:
            used = sorted(recorder.methods_used() & {"click", "type"})
            self._fail(ctx, f"declares mutates=False but called {used} — BG003 "
                            f"cannot see this mutation, so a graph containing it "
                            f"will be reported as safely verified when it is not")

        if not ctx.failed:
            absent = [k for k in c.writes if k not in ctx.data]
            if absent:
                self._fail(ctx, f"declares writes={list(c.writes)} but {absent} were "
                                f"never set; the next node will read an empty value")
        return ctx


def checked(graph, *, strict: bool = True):
    """Wrap every node in a graph with `Checked`, preserving names and edges.

    Mutates in place and returns the graph, so it reads as a decorator step:

        run(checked(build_graph()), spec, browser)
    """
    for key, node in list(graph.nodes.items()):
        if isinstance(node, Checked):
            continue
        wrapper = Checked(node, strict=strict, name=key)
        graph.nodes[key] = wrapper
    return graph


def violations(graph) -> list[str]:
    """Every contract violation recorded by a non-strict checked run."""
    out: list[str] = []
    for node in graph.nodes.values():
        if isinstance(node, Checked):
            out.extend(f"{node.inner.name}: {v}" for v in node.violations)
    return out
