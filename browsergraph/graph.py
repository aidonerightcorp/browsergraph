"""Graph definition and execution.

A graph is a DAG of nodes. Edges carry control flow; data flows through
`Context.data`. Running a graph is deterministic: same spec + same nodes +
same driver behaviour produces the same call sequence, which is what makes
combinations comparable to each other.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from browsergraph.dimensions import Spec, validate
from browsergraph.nodes.base import Node
from browsergraph.ports import Context


class GraphError(RuntimeError):
    pass


@dataclass
class Graph:
    """Nodes plus edges. `add` returns self so graphs read as a pipeline."""

    name: str = "graph"
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)
    _order: list[str] = field(default_factory=list)

    def add(self, node: Node, after: str | None = None) -> Graph:
        key = node.name
        if key in self.nodes:
            # An auto-generated name (name == kind) means the author did not
            # choose it, so two `extract` nodes are a convenience, not a
            # mistake — suffix them. An explicitly chosen duplicate is still an
            # error, because edges and lint findings reference names.
            if key == node.kind:
                n = 2
                while f"{key}-{n}" in self.nodes:
                    n += 1
                key = f"{key}-{n}"
                node.name = key
            else:
                raise GraphError(f"duplicate node name: {key}")
        self.nodes[key] = node
        if after is not None:
            if after not in self.nodes:
                raise GraphError(f"unknown predecessor: {after}")
            self.edges.append((after, key))
        elif self._order:
            self.edges.append((self._order[-1], key))
        self._order.append(key)
        return self

    def chain(self, nodes: Iterable[Node]) -> Graph:
        for n in nodes:
            self.add(n)
        return self

    def levels(self) -> list[list[str]]:
        """Topological levels: every node in a level has no dependency on its peers.

        This is what makes concurrency possible without reordering semantics —
        a level is exactly the set of nodes whose predecessors have all run.
        """
        indeg = {k: 0 for k in self.nodes}
        adj: dict[str, list[str]] = {k: [] for k in self.nodes}
        for a, b in self.edges:
            adj[a].append(b)
            indeg[b] += 1
        ready = [k for k in self._order if indeg[k] == 0]
        out: list[list[str]] = []
        seen = 0
        while ready:
            out.append(list(ready))
            seen += len(ready)
            nxt: list[str] = []
            for cur in ready:
                for child in adj[cur]:
                    indeg[child] -= 1
                    if indeg[child] == 0:
                        nxt.append(child)
            nxt_set = set(nxt)
            ready = [k for k in self._order if k in nxt_set]
        if seen != len(self.nodes):
            raise GraphError("cycle detected in graph")
        return out

    def topo(self) -> list[str]:
        """Kahn's algorithm; raises on a cycle."""
        indeg = {k: 0 for k in self.nodes}
        adj: dict[str, list[str]] = {k: [] for k in self.nodes}
        for a, b in self.edges:
            adj[a].append(b)
            indeg[b] += 1
        queue = [k for k in self._order if indeg[k] == 0]
        out: list[str] = []
        while queue:
            cur = queue.pop(0)
            out.append(cur)
            for nxt in adj[cur]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    queue.append(nxt)
        if len(out) != len(self.nodes):
            raise GraphError("cycle detected in graph")
        return out

    def check(self, spec: Spec) -> list[str]:
        """Static problems, found before a browser is launched."""
        problems = list(validate(spec))
        produced: set[str] = set()
        for key in self.topo():
            node = self.nodes[key]
            for r in node.reads:
                if r not in produced:
                    problems.append(f"{key} reads {r!r} before anything writes it")
            produced.update(node.writes)
        return problems


@dataclass
class RunResult:
    ok: bool
    context: Context
    executed: list[str]
    spec: Spec

    @property
    def log(self) -> list[str]:
        return self.context.log

    def summary(self) -> str:
        status = "ok" if self.ok else f"FAILED ({self.context.error})"
        return f"[{self.spec.describe()}] {len(self.executed)} nodes -> {status}"


def _concurrent_group(graph: Graph, keys: list[str]) -> list[str]:
    """Which of these peers may genuinely run at the same time.

    A browser page is single-threaded state: two nodes that click, type or
    navigate cannot overlap without racing each other. Only read-only nodes
    qualify, and only when the driver tolerates concurrent reads.
    """
    if len(keys) < 2:
        return []
    safe = []
    for k in keys:
        node = graph.nodes[k]
        if node.mutates or not node.needs_browser:
            continue
        if node.kind in ("navigate", "scroll", "screenshot"):
            continue        # these move or capture shared page state
        safe.append(k)
    return safe if len(safe) > 1 else []


def run(graph: Graph, spec: Spec, browser, strict: bool = True,
        parallel: int = 1) -> RunResult:
    """Execute a graph against an already-constructed BrowserPort.

    `strict` stops at the first failure, which is almost always what you want:
    continuing after a failed click means every later assertion is meaningless.

    `parallel` > 1 runs independent **read-only** nodes in one topological
    level concurrently. Mutating nodes are never parallelised — a page is
    shared mutable state, and two clicks racing is not an optimisation.
    """
    problems = graph.check(spec)
    if problems:
        raise GraphError("; ".join(problems))

    ctx = Context(browser=browser)
    executed: list[str] = []
    browser.start()
    try:
        for level in graph.levels():
            group = _concurrent_group(graph, level) if parallel > 1 else []

            if group:
                from concurrent.futures import ThreadPoolExecutor
                ctx.note(f"parallel: {', '.join(group)}")
                with ThreadPoolExecutor(max_workers=min(parallel, len(group))) as pool:
                    def _run_one(key, _g=graph, _c=ctx):
                        return _g.nodes[key].run(_c)
                    list(pool.map(_run_one, group))
                executed.extend(group)
                if ctx.failed and strict:
                    break

            for key in level:
                if key in group:
                    continue
                node = graph.nodes[key]
                if node.needs_browser and ctx.browser is None:
                    ctx.fail(f"{key} needs a browser but none was supplied")
                    break
                ctx = node.run(ctx)
                executed.append(key)
                if ctx.failed and strict:
                    break
            if ctx.failed and strict:
                break
        if ctx.failed:
            # Snapshot the page before the browser closes: a CAPTCHA usually
            # presents as a plain selector miss, and without the page text a
            # challenge is indistinguishable from a missing element.
            try:
                ctx.data.setdefault("page_text", browser.html()[:8000])
            except Exception:
                pass
    finally:
        browser.stop()
    return RunResult(ok=not ctx.failed, context=ctx, executed=executed, spec=spec)
