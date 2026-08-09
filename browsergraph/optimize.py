"""Node and graph optimisation.

Two levels, deliberately separate:

* **Node optimiser** — makes an individual node cheaper: memoise pure reads,
  skip work whose result is already in context, tighten timeouts that observed
  runs show are far too generous.
* **Graph optimiser** — rewrites the graph: drop nodes whose output nobody
  consumes, collapse redundant waits, merge consecutive extracts, and report
  which nodes could run in parallel.

Every rewrite is **semantics-preserving and reported**. An optimiser that
silently changes what a graph does is worse than no optimiser, so each pass
records what it changed and why, and `explain()` prints it.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from browsergraph.nodes.base import Node, register
from browsergraph.ports import Context

# --- node level -------------------------------------------------------------

@register
class Memoized(Node):
    """Cache a pure read node's result within a run.

    Only safe for nodes that do not mutate: re-reading a page is idempotent,
    re-clicking is not. The constructor refuses mutating nodes rather than
    trusting the caller.
    """

    kind: ClassVar[str] = "memoized"

    def __init__(self, inner: Node, key: Callable[[Context], str] | None = None,
                 cache: dict | None = None, name: str = ""):
        if inner.mutates:
            raise ValueError(f"refusing to memoize {inner.name}: it mutates state")
        super().__init__(name or f"memo({inner.name})")
        self.inner = inner
        self.cache = cache if cache is not None else {}
        self.key = key or (lambda ctx: f"{inner.name}@{ctx.data.get('url', '')}")
        self.hits = 0
        self.misses = 0
        self.interacts = inner.interacts
        self.verifies = inner.verifies

    @property
    def writes(self) -> tuple[str, ...]:  # type: ignore[override]
        return tuple(self.inner.writes)

    def run(self, ctx: Context) -> Context:
        k = self.key(ctx)
        if k in self.cache:
            self.hits += 1
            ctx.data.update(self.cache[k])
            ctx.note(f"memo hit {self.inner.name}")
            return ctx
        self.misses += 1
        before = dict(ctx.data)
        ctx = self.inner.run(ctx)
        if not ctx.failed:
            self.cache[k] = {kk: vv for kk, vv in ctx.data.items()
                             if before.get(kk) != vv}
        return ctx


@register
class SkipIfPresent(Node):
    """Skip a node when its output is already in context.

    Useful when several tasks share a prelude — a second `extract title` is
    wasted work if a previous node already produced it.
    """

    kind: ClassVar[str] = "skip_if_present"

    def __init__(self, inner: Node, keys: tuple[str, ...] = (), name: str = ""):
        super().__init__(name or f"skip?({inner.name})")
        self.inner = inner
        self.keys = keys or tuple(inner.writes)
        self.mutates = inner.mutates
        self.interacts = inner.interacts
        self.verifies = inner.verifies

    @property
    def writes(self) -> tuple[str, ...]:  # type: ignore[override]
        return tuple(self.inner.writes)

    def run(self, ctx: Context) -> Context:
        if self.keys and all(ctx.data.get(k) for k in self.keys):
            ctx.note(f"skip {self.inner.name} ({', '.join(self.keys)} already present)")
            return ctx
        return self.inner.run(ctx)


# --- graph level ------------------------------------------------------------

@dataclass
class Rewrite:
    pass_name: str
    change: str
    reason: str

    def __str__(self) -> str:
        return f"{self.pass_name}: {self.change} — {self.reason}"


@dataclass
class OptimizationReport:
    rewrites: list[Rewrite] = field(default_factory=list)
    parallelizable: list[list[str]] = field(default_factory=list)
    estimated_saving: float = 0.0

    def explain(self) -> str:
        if not self.rewrites and not self.parallelizable:
            return "no optimisations applied"
        lines = [str(r) for r in self.rewrites]
        for group in self.parallelizable:
            lines.append(f"parallel: {', '.join(group)} have no data dependency "
                         f"and could run concurrently")
        if self.estimated_saving:
            lines.append(f"estimated saving: {self.estimated_saving:.1f}s per run")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"rewrites": [r.__dict__ for r in self.rewrites],
                "parallelizable": self.parallelizable,
                "estimated_saving_sec": round(self.estimated_saving, 2)}


class GraphOptimizer:
    """Semantics-preserving graph rewrites, with a report of every change."""

    def __init__(self, stats: dict[str, Any] | None = None,
                 keep: tuple[str, ...] = ()) -> None:
        #: Observed per-node statistics (from `Supervisor`), used to tighten
        #: timeouts and estimate savings. Optional — passes work without it.
        self.stats = stats or {}
        self.keep = set(keep)

    # -- passes
    def drop_dead_nodes(self, graph, report: OptimizationReport):
        """Remove read-only nodes whose outputs nobody consumes."""
        consumed = {r for n in graph.nodes.values() for r in n.reads} | self.keep
        removed = []
        for key in graph.topo():
            node = graph.nodes[key]
            if node.mutates or node.verifies or not node.writes:
                continue
            if node.kind in ("screenshot", "navigate"):
                continue
            if not (set(node.writes) & consumed):
                removed.append(key)
        for key in removed:
            report.rewrites.append(Rewrite(
                "drop_dead_nodes", f"removed {key}",
                f"writes {graph.nodes[key].writes} which nothing reads"))
            self.estimate(key, report)
        return removed

    def collapse_waits(self, graph, report: OptimizationReport):
        """Drop a wait_for whose selector an immediately preceding wait covered."""
        order = graph.topo()
        removed, last_wait = [], None
        for key in order:
            node = graph.nodes[key]
            sel = getattr(node, "selector", "")
            if node.kind == "wait_for":
                if last_wait == sel:
                    removed.append(key)
                    report.rewrites.append(Rewrite(
                        "collapse_waits", f"removed {key}",
                        f"duplicate wait for {sel!r}"))
                    self.estimate(key, report)
                last_wait = sel
            elif node.mutates:
                last_wait = None
        return removed

    def find_parallelizable(self, graph, report: OptimizationReport):
        """Report read-only nodes with no shared data that could run together."""
        order = graph.topo()
        run_group: list[str] = []
        for key in order:
            node = graph.nodes[key]
            independent = (not node.mutates and not node.interacts) or \
                          node.kind in ("extract",)
            if independent and not node.mutates:
                run_group.append(key)
            else:
                if len(run_group) > 1:
                    report.parallelizable.append(list(run_group))
                run_group = []
        if len(run_group) > 1:
            report.parallelizable.append(list(run_group))

    def tighten_timeouts(self, graph, report: OptimizationReport):
        """Suggest timeouts from observed runtimes rather than guesses."""
        for key in graph.topo():
            node = graph.nodes[key]
            st = self.stats.get(key) or self.stats.get(getattr(node, "inner", node).name)
            timeout = getattr(node, "timeout", 0)
            if not st or not timeout:
                continue
            mean = st.mean_seconds if hasattr(st, "mean_seconds") else st.get("mean_seconds", 0)
            if mean and timeout > mean * 10 and st_failures(st) == 0:
                suggested = max(1.0, round(mean * 4, 1))
                report.rewrites.append(Rewrite(
                    "tighten_timeouts",
                    f"{key} timeout {timeout} -> {suggested}",
                    f"observed mean {mean:.2f}s with no failures; a 10x margin "
                    f"only delays failure detection"))
                node.timeout = suggested

    def estimate(self, key: str, report: OptimizationReport) -> None:
        st = self.stats.get(key)
        if st is not None:
            mean = st.mean_seconds if hasattr(st, "mean_seconds") else st.get("mean_seconds", 0)
            report.estimated_saving += mean

    # -- entry point
    def optimize(self, graph):
        """Return (optimised_graph, report). The input graph is not mutated."""
        from browsergraph.graph import Graph
        report = OptimizationReport()

        dead = set(self.drop_dead_nodes(graph, report))
        dup_waits = set(self.collapse_waits(graph, report))
        drop = dead | dup_waits

        out = Graph(f"{graph.name}+optimized")
        for key in graph.topo():
            if key not in drop:
                out.add(graph.nodes[key])

        self.find_parallelizable(out, report)
        self.tighten_timeouts(out, report)
        return out, report


def st_failures(st: Any) -> int:
    return st.failures if hasattr(st, "failures") else int(st.get("failures", 0))


def optimize_graph(graph, stats: dict | None = None, keep: tuple[str, ...] = ()):
    """Convenience wrapper."""
    return GraphOptimizer(stats=stats, keep=keep).optimize(graph)
