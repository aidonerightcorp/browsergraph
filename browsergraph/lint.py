"""Static checks on a graph, before a browser is launched.

Browser automation fails expensively — a bad graph burns a session, trips rate
limits, or worse, reports success having done nothing. Most of that is
detectable by reading the graph.

The rule this library exists to enforce is BG003: **a graph that changes remote
state and never verifies the outcome cannot tell success from silent failure.**
That is not theoretical — 551 emails once reported "sent" successfully and
produced zero posts, because nothing checked the destination.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from browsergraph.dimensions import Spec
    from browsergraph.graph import Graph

ERROR, WARN, INFO = "error", "warn", "info"

#: Selectors that break when a page is regenerated.
_BRITTLE = (
    (re.compile(r":nth-child\(\d+\)"), "positional selector breaks on reorder"),
    (re.compile(r"\b\w+(?:\s*>\s*\w+){3,}"), "deep descendant chain is fragile"),
    (re.compile(r"\.(?:css|sc|jsx)-[a-z0-9]{5,}", re.I), "generated class name changes on rebuild"),
    (re.compile(r"^\s*(div|span)\s*$"), "selector matches nearly everything"),
)

_SECRETISH = re.compile(r"pass|pwd|secret|token|api[_-]?key|otp", re.I)


@dataclass
class Finding:
    code: str
    severity: str
    node: str
    message: str
    fix: str = ""

    def __str__(self) -> str:
        return f"[{self.severity.upper():5}] {self.code} {self.node}: {self.message}"


def lint(graph: Graph, spec: Spec | None = None) -> list[Finding]:
    """Return findings, most severe first. Empty means nothing detectable."""
    out: list[Finding] = []
    order = graph.topo()
    nodes = [graph.nodes[k] for k in order]

    # BG001 — nodes never reached
    reachable = {order[0]} if order else set()
    for a, b, *_ in graph.edges:
        if a in reachable:
            reachable.add(b)
    for key in order:
        if key not in reachable:
            out.append(Finding("BG001", ERROR, key,
                               "node is unreachable from the graph entry point",
                               "connect it with add(node, after=...) or remove it"))

    # BG002 — values written but never read
    read_keys = {r for n in nodes for r in n.reads}
    for n in nodes:
        for w in n.writes:
            if w not in read_keys and w not in {"url", "title"}:
                out.append(Finding("BG002", INFO, n.name,
                                   f"writes {w!r} but nothing reads it",
                                   "drop the node, or consume the value"))

    # BG003 — mutation without verification  (the important one)
    mutators = [n for n in nodes if n.mutates]
    if mutators:
        idx = {k: i for i, k in enumerate(order)}
        last_mutation = max(idx[n.name] for n in mutators)
        verified_after = any(n.verifies and idx[n.name] > last_mutation for n in nodes)
        if not verified_after:
            out.append(Finding(
                "BG003", WARN, mutators[-1].name,
                "graph changes remote state but never verifies the outcome — "
                "a silent failure will look like success",
                "add wait_for/llm_verify after the last mutating node"))

    # BG004 — interacting without establishing presence
    seen_wait: set[str] = set()
    for n in nodes:
        if n.verifies and n.selector:
            seen_wait.add(n.selector)
        if n.interacts and n.selector and not getattr(n, "optional", False):
            if n.selector not in seen_wait:
                out.append(Finding("BG004", WARN, n.name,
                                   f"interacts with {n.selector!r} without waiting for it",
                                   f"add wait_for(selector={n.selector!r}) first"))

    # BG005 — brittle selectors
    for n in nodes:
        sel = getattr(n, "selector", "")
        for pattern, why in _BRITTLE:
            if sel and pattern.search(sel):
                out.append(Finding("BG005", WARN, n.name,
                                   f"brittle selector {sel!r}: {why}",
                                   "prefer id, data-testid, or an aria role"))
                break

    # BG006 — secrets inline
    for n in nodes:
        text = getattr(n, "text", "")
        sel = getattr(n, "selector", "")
        if text and (_SECRETISH.search(sel) or _SECRETISH.search(getattr(n, "name", ""))):
            out.append(Finding("BG006", ERROR, n.name,
                               "a credential appears inline in the graph",
                               "reference an env var or the credential broker instead"))

    # BG007 — no artifact on which to debug a failure
    if not any(n.kind == "screenshot" for n in nodes) and len(nodes) > 2:
        out.append(Finding("BG007", INFO, graph.name,
                           "no screenshot node — failures will have no visual evidence",
                           "add screenshot(path=...) at the end, or on failure"))

    # BG008 — LLM selector with no deterministic fallback
    for n in nodes:
        if n.uses_llm and hasattr(n, "fallback") and not n.fallback:
            out.append(Finding("BG008", WARN, n.name,
                               "llm selector has no deterministic fallback, so every "
                               "run costs tokens and depends on the model",
                               "set fallback= to the known selector"))

    # BG009 — spec/graph mismatch
    if spec is not None:
        if any(n.uses_llm for n in nodes) and not spec.llm.enabled:
            out.append(Finding("BG009", WARN, graph.name,
                               "graph uses llm nodes but spec.llm.mode is none",
                               "set llm mode, or the nodes will use defaults"))
        if spec.behavior.max_action_delay == 0 and len(mutators) > 3:
            out.append(Finding("BG009", INFO, graph.name,
                               "several mutations with zero delay looks automated",
                               "consider Behavior.humanlike()"))

    rank = {ERROR: 0, WARN: 1, INFO: 2}
    return sorted(out, key=lambda f: (rank[f.severity], f.code))


def report(findings: list[Finding]) -> str:
    if not findings:
        return "no findings"
    lines = [str(f) for f in findings]
    lines += ["", f"{sum(1 for f in findings if f.severity == ERROR)} error(s), "
              f"{sum(1 for f in findings if f.severity == WARN)} warning(s), "
              f"{sum(1 for f in findings if f.severity == INFO)} info"]
    return "\n".join(lines)


def has_errors(findings: list[Finding]) -> bool:
    return any(f.severity == ERROR for f in findings)
