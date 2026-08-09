"""Self-healing selectors.

When a selector misses, try progressively less certain strategies rather than
failing immediately — but record what happened, because **healing that hides
drift is worse than failing.** A selector that needs the model on every run is
a site that has changed; the point of the ledger is to make that visible
instead of quietly paying tokens forever.

Order is deliberate: cheap and deterministic first, model last.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from browsergraph.nodes.base import Node, register
from browsergraph.ports import Context

Strategy = Callable[[str, Context], list[str]]


# --- strategies -------------------------------------------------------------

def s_identity(selector: str, ctx: Context) -> list[str]:
    return [selector]


def s_aliases(selector: str, ctx: Context) -> list[str]:
    """Selectors supplied by the author as known alternates."""
    return list(ctx.data.get("_aliases", {}).get(selector, []))


def s_relax(selector: str, ctx: Context) -> list[str]:
    """Drop the most fragile parts: nth-child, then leading ancestors.

    Ancestors are dropped from the *stripped* selector, so the surviving leaf
    is not still carrying the positional predicate we just removed.
    """
    out = []
    stripped = re.sub(r":nth-child\(\d+\)", "", selector).strip()
    if stripped and stripped != selector:
        out.append(stripped)
    parts = [p for p in re.split(r"\s*>\s*|\s+", stripped or selector) if p]
    if len(parts) > 1:
        out.append(parts[-1])
    return out


def s_attr_swap(selector: str, ctx: Context) -> list[str]:
    """#id -> [data-testid], and the reverse — the two most common conventions."""
    out = []
    m = re.fullmatch(r"#([\w-]+)", selector)
    if m:
        out += [f'[data-testid="{m.group(1)}"]', f'[name="{m.group(1)}"]',
                f'[aria-label="{m.group(1)}"]']
    m = re.fullmatch(r'\[data-testid="([^"]+)"\]', selector)
    if m:
        out.append(f"#{m.group(1)}")
    return out


DEFAULT_STRATEGIES: tuple[tuple[str, Strategy], ...] = (
    ("identity", s_identity),
    ("aliases", s_aliases),
    ("relax", s_relax),
    ("attr_swap", s_attr_swap),
)


# --- ledger -----------------------------------------------------------------

@dataclass
class HealEvent:
    original: str
    resolved: str
    strategy: str
    node: str = ""

    def drifted(self) -> bool:
        return self.strategy != "identity"


@dataclass
class Ledger:
    """Records every resolution so drift is measurable, not invisible."""
    events: list[HealEvent] = field(default_factory=list)

    def add(self, ev: HealEvent) -> None:
        self.events.append(ev)

    @property
    def drift_rate(self) -> float:
        if not self.events:
            return 0.0
        return sum(1 for e in self.events if e.drifted()) / len(self.events)

    def drifted(self) -> list[HealEvent]:
        return [e for e in self.events if e.drifted()]

    def suggestions(self) -> dict[str, str]:
        """original -> resolved, for updating the graph config."""
        return {e.original: e.resolved for e in self.drifted()}

    def save(self, path: str | Path) -> str:
        payload = {
            "drift_rate": round(self.drift_rate, 3),
            "events": [e.__dict__ for e in self.events],
            "suggestions": self.suggestions(),
        }
        Path(path).write_text(json.dumps(payload, indent=2))
        return str(path)

    def report(self) -> str:
        if not self.events:
            return "no selector resolutions recorded"
        lines = [f"{len(self.events)} resolutions, drift rate {self.drift_rate:.0%}"]
        for e in self.drifted():
            lines.append(f"  {e.node}: {e.original!r} -> {e.resolved!r} (via {e.strategy})")
        if self.drift_rate > 0.5:
            lines.append("  NOTE: majority of selectors needed healing — the page has "
                         "probably changed; update the graph rather than relying on this")
        return "\n".join(lines)


# --- healer -----------------------------------------------------------------

class Healer:
    """Resolve a selector, trying strategies in order of increasing uncertainty."""

    def __init__(self, ledger: Ledger | None = None,
                 strategies: tuple[tuple[str, Strategy], ...] = DEFAULT_STRATEGIES,
                 llm_client: Any = None, goal_hint: str = "") -> None:
        self.ledger = ledger or Ledger()
        self.strategies = strategies
        self.llm_client = llm_client
        self.goal_hint = goal_hint

    def resolve(self, selector: str, ctx: Context, node_name: str = "") -> str | None:
        for name, strategy in self.strategies:
            for candidate in strategy(selector, ctx):
                if candidate and ctx.page.find(candidate) is not None:
                    self.ledger.add(HealEvent(selector, candidate, name, node_name))
                    return candidate

        if self.llm_client is not None:
            try:
                raw = self.llm_client.complete(
                    "Return ONE CSS selector and nothing else.\n"
                    f"Goal: {self.goal_hint or selector}\n\n{ctx.page.html()[:6000]}",
                    system="You output only a CSS selector.")
            except Exception:
                return None
            cand = (raw or "").strip().splitlines()[0].strip().strip("`") if raw else ""
            if cand and ctx.page.find(cand) is not None:
                self.ledger.add(HealEvent(selector, cand, "llm", node_name))
                return cand
        return None


@register
class Healing(Node):
    """Wrap a node so its selector heals on miss.

    The wrapped node runs unchanged once a selector resolves, so healing is
    orthogonal to what the node does.
    """

    kind: ClassVar[str] = "healing"

    def __init__(self, inner: Node, healer: Healer | None = None,
                 aliases: tuple[str, ...] = (), name: str = ""):
        super().__init__(name or f"heal({inner.name})")
        self.inner = inner
        self.healer = healer or Healer()
        self.aliases = aliases
        self.mutates = inner.mutates
        self.interacts = inner.interacts
        self.verifies = inner.verifies

    @property
    def selector(self) -> str:  # type: ignore[override]
        return getattr(self.inner, "selector", "")

    @property
    def writes(self) -> tuple[str, ...]:  # type: ignore[override]
        return tuple(self.inner.writes)

    def run(self, ctx: Context) -> Context:
        target = getattr(self.inner, "selector", "")
        if not target:
            return self.inner.run(ctx)

        if self.aliases:
            ctx.data.setdefault("_aliases", {})[target] = list(self.aliases)

        resolved = self.healer.resolve(target, ctx, node_name=self.inner.name)
        if resolved is None:
            ctx.fail(f"{self.inner.name}: could not resolve {target!r} by any strategy")
            return ctx
        if resolved != target:
            ctx.note(f"healed {target!r} -> {resolved!r}")
            self.inner.selector = resolved
        return self.inner.run(ctx)
