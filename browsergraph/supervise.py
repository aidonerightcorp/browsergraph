"""Node supervision — per-node timeout, retry, circuit breaking and fallback.

Supervision is a **wrapper node**, not a feature of the runner. That keeps the
policy visible in the graph (you can see which nodes are supervised and how),
composable (a supervised node can itself be a healing node), and testable in
isolation.

The circuit breaker is the part that matters operationally: after repeated
failures of the same kind it stops trying instead of grinding, which is the
difference between a failed run and a burned account.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar

from browsergraph.errors import Diagnosis, Response, classify
from browsergraph.nodes.base import Node, register
from browsergraph.ports import Context


@dataclass
class Attempt:
    index: int
    ok: bool
    seconds: float
    diagnosis: Diagnosis | None = None


@dataclass
class NodeStats:
    """Per-node execution record — the input to the optimizer."""
    name: str
    runs: int = 0
    failures: int = 0
    total_seconds: float = 0.0
    attempts: list[Attempt] = field(default_factory=list)
    opened_circuit: bool = False

    @property
    def mean_seconds(self) -> float:
        return self.total_seconds / self.runs if self.runs else 0.0

    @property
    def failure_rate(self) -> float:
        return self.failures / self.runs if self.runs else 0.0

    @property
    def retries_used(self) -> int:
        return max(0, len(self.attempts) - self.runs)

    def to_dict(self) -> dict:
        return {"name": self.name, "runs": self.runs, "failures": self.failures,
                "failure_rate": round(self.failure_rate, 3),
                "mean_seconds": round(self.mean_seconds, 3),
                "retries_used": self.retries_used,
                "opened_circuit": self.opened_circuit}


@dataclass
class CircuitBreaker:
    """Trips after `threshold` consecutive failures; stays open for `cooldown`."""
    threshold: int = 3
    cooldown: float = 60.0
    _consecutive: int = 0
    _opened_at: float | None = None      # None = closed; 0.0 is a valid time

    def record(self, ok: bool, now: float) -> None:
        self._consecutive = 0 if ok else self._consecutive + 1
        if self._consecutive >= self.threshold:
            self._opened_at = now

    def is_open(self, now: float) -> bool:
        if self._opened_at is None:
            return False
        if now - self._opened_at >= self.cooldown:
            self._opened_at = None
            self._consecutive = 0
            return False
        return True


@register
class Supervised(Node):
    """Wrap a node with retry, timeout, circuit breaking and a fallback.

    Retry policy comes from the *diagnosis*, not a blanket count: a node that
    failed on a CAPTCHA is not retried at all, while a timeout backs off.
    """

    kind: ClassVar[str] = "supervised"

    def __init__(self, inner: Node, retries: int = 2, timeout: float = 0.0,
                 breaker: CircuitBreaker | None = None, fallback: Node | None = None,
                 stats: NodeStats | None = None, name: str = "",
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        super().__init__(name or f"sv({inner.name})")
        self.inner = inner
        self.retries = retries
        self.timeout = timeout
        self.breaker = breaker or CircuitBreaker()
        self.fallback = fallback
        self.stats = stats or NodeStats(name=inner.name)
        self.sleep = sleep
        self.clock = clock
        self.mutates = inner.mutates
        self.verifies = inner.verifies
        self.interacts = inner.interacts
        self.uses_llm = inner.uses_llm

    @property
    def selector(self) -> str:  # type: ignore[override]
        return getattr(self.inner, "selector", "")

    @property
    def writes(self) -> tuple[str, ...]:  # type: ignore[override]
        return tuple(self.inner.writes)

    def _run_once(self, ctx: Context) -> tuple[Context, float]:
        started = self.clock()
        ctx.failed, ctx.error = False, ""
        ctx = self.inner.run(ctx)
        return ctx, self.clock() - started

    def run(self, ctx: Context) -> Context:
        now = self.clock()
        if self.breaker.is_open(now):
            self.stats.opened_circuit = True
            ctx.fail(f"{self.inner.name}: circuit open, not attempting")
            return ctx

        self.stats.runs += 1
        last_error = ""

        for i in range(self.retries + 1):
            ctx, elapsed = self._run_once(ctx)
            self.stats.total_seconds += elapsed

            if not ctx.failed and self.timeout and elapsed > self.timeout:
                ctx.fail(f"{self.inner.name}: exceeded timeout "
                         f"({elapsed:.1f}s > {self.timeout:.1f}s)")

            if not ctx.failed:
                self.stats.attempts.append(Attempt(i, True, elapsed))
                self.breaker.record(True, self.clock())
                return ctx

            last_error = ctx.error
            diag = classify(ctx.error, ctx.data.get("page_text", ""), attempt=i)
            self.stats.attempts.append(Attempt(i, False, elapsed, diag))
            self.breaker.record(False, self.clock())

            if diag.terminal:
                ctx.note(f"{self.inner.name}: not retrying ({diag.failure.value})")
                break
            if i >= self.retries:
                break
            if diag.response is Response.WAIT_RETRY:
                self.sleep(diag.backoff())
            ctx.note(f"{self.inner.name}: retry {i + 1}/{self.retries} after {diag.failure.value}")

        if self.fallback is not None:
            ctx.note(f"{self.inner.name}: trying fallback {self.fallback.name}")
            ctx.failed, ctx.error = False, ""
            ctx = self.fallback.run(ctx)
            if not ctx.failed:
                return ctx

        self.stats.failures += 1
        if not ctx.failed:
            ctx.fail(last_error or f"{self.inner.name} failed")
        return ctx


@dataclass
class Supervisor:
    """Applies a supervision policy across a graph and collects the statistics."""
    retries: int = 2
    timeout: float = 0.0
    breaker_threshold: int = 3
    stats: dict[str, NodeStats] = field(default_factory=dict)

    def wrap(self, node: Node, fallback: Node | None = None,
             **over) -> Supervised:
        st = self.stats.setdefault(node.name, NodeStats(name=node.name))
        return Supervised(
            node, retries=over.get("retries", self.retries),
            timeout=over.get("timeout", self.timeout),
            breaker=CircuitBreaker(threshold=self.breaker_threshold),
            fallback=fallback, stats=st, sleep=over.get("sleep", time.sleep))

    def supervise(self, graph, only=None, **over):
        """Return a new graph with nodes wrapped. Order and edges are preserved."""
        from browsergraph.graph import Graph
        out = Graph(f"{graph.name}+supervised")
        for key in graph.topo():
            node = graph.nodes[key]
            if only is None or node.kind in only:
                wrapped = self.wrap(node, **over)
                wrapped.name = key          # keep names stable for edges/lint
                out.add(wrapped)
            else:
                out.add(node)
        return out

    def report(self) -> dict:
        return {"nodes": [s.to_dict() for s in self.stats.values()],
                "total_seconds": round(sum(s.total_seconds for s in self.stats.values()), 2),
                "total_failures": sum(s.failures for s in self.stats.values())}
