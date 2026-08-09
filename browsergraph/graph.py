"""Graph definition and execution.

A graph is a DAG of nodes. Edges carry control flow; data flows through
`Context.data`. Running a graph is deterministic: same spec + same nodes +
same driver behaviour produces the same call sequence, which is what makes
combinations comparable to each other.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple

from browsergraph.dimensions import Spec, validate
from browsergraph.nodes.base import Node
from browsergraph.ports import Context


class GraphError(RuntimeError):
    pass


def _mid(key: str) -> str:
    """Mermaid node ids may not contain punctuation that appears in node names."""
    return "n_" + "".join(c if c.isalnum() else "_" for c in key)


def _esc(text: str) -> str:
    """Escape for an HTML attribute or text node.

    Node names and selectors are author-supplied and routinely contain `<`, `>`
    and quotes (`div[data-id="x"] > a`), which would otherwise break the SVG or
    smuggle markup into the page.
    """
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


class EdgeKind(str, Enum):
    """Why an edge exists — which is not the same as what it constrains.

    Both kinds constrain order identically; they differ in *intent*, and intent
    is what tooling needs. A SEQUENCE edge exists only because two nodes were
    added one after another, so a scheduler may reorder across it when the data
    allows. A DEPENDENCY edge was requested explicitly via `after=` and must be
    preserved. Collapsing the two into a bare tuple loses exactly the
    information needed to parallelise safely.
    """
    SEQUENCE = "sequence"        # implied by add order
    DEPENDENCY = "dependency"    # requested explicitly with after=
    BRANCH = "branch"            # taken conditionally by a control node


class Edge(NamedTuple):
    """A directed edge. A NamedTuple so `(src, dst)` destructuring still works."""
    src: str
    dst: str
    kind: EdgeKind = EdgeKind.SEQUENCE
    reason: str = ""

    def __str__(self) -> str:
        tail = f"  # {self.reason}" if self.reason else ""
        return f"{self.src} -> {self.dst} [{self.kind.value}]{tail}"


@dataclass
class Graph:
    """Nodes plus edges. `add` returns self so graphs read as a pipeline."""

    name: str = "graph"
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
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
            self.edges.append(Edge(after, key, EdgeKind.DEPENDENCY,
                                   reason="requested with after="))
        elif self._order:
            self.edges.append(Edge(self._order[-1], key, EdgeKind.SEQUENCE))
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
        for e in self.edges:
            adj[e.src].append(e.dst)
            indeg[e.dst] += 1
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
        for e in self.edges:
            adj[e.src].append(e.dst)
            indeg[e.dst] += 1
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

    # --- introspection ------------------------------------------------------

    def contracts(self) -> list:
        """Every node's contract, in execution order."""
        from browsergraph.contracts import contract_of
        return [contract_of(self.nodes[k]) for k in self.topo()]

    def audit(self, seed_keys: tuple[str, ...] = ()):
        """Check the nodes' contracts against each other. See contracts.audit."""
        from browsergraph.contracts import audit as _audit
        return _audit(self, seed_keys=seed_keys)

    def to_dict(self) -> dict:
        """Serialisable description — the shape the HTTP API and configs use."""
        from browsergraph.contracts import contract_of
        return {
            "name": self.name,
            "nodes": [contract_of(self.nodes[k]).to_dict() | {"key": k}
                      for k in self._order],
            "edges": [{"src": e.src, "dst": e.dst, "kind": e.kind.value}
                      for e in self.edges],
            "levels": self.levels(),
        }

    def to_mermaid(self) -> str:
        """A Mermaid flowchart of the graph.

        Mutating nodes are marked, because "which steps change remote state and
        does anything check them" is the question a reader of a browser
        automation diagram actually has — see BG003.
        """
        lines = ["flowchart TD", f"  %% {self.name}"]
        for key in self._order:
            node = self.nodes[key]
            label = f"{key}<br/><i>{node.kind}</i>"
            if node.mutates:
                lines.append(f'  {_mid(key)}[["{label}"]]:::mutates')
            elif node.verifies:
                lines.append(f'  {_mid(key)}("{label}"):::verifies')
            else:
                lines.append(f'  {_mid(key)}["{label}"]')
        for e in self.edges:
            arrow = "-.->" if e.kind is EdgeKind.DEPENDENCY else "-->"
            lines.append(f"  {_mid(e.src)} {arrow} {_mid(e.dst)}")
        lines += [
            "  classDef mutates fill:#fde2e2,stroke:#c33,stroke-width:2px;",
            "  classDef verifies fill:#e2f5e6,stroke:#2a2,stroke-width:2px;",
        ]
        return "\n".join(lines)

    def to_html(self, width: int = 760) -> str:
        """A self-contained interactive diagram of this graph.

        No CDN, no library, no network: a notebook cell, a Kaggle output and a
        saved HTML file all render it identically, and it still works offline
        five years from now. Everything is inline SVG plus a few lines of JS.

        Hovering a node shows its contract — which is the point. A picture of a
        graph tells you the order; the question people actually have is *which
        steps change remote state, and does anything check them*, and that lives
        in the contracts rather than in the shape.

        Ids are namespaced per render so several graphs can coexist in one page.
        """
        from browsergraph.contracts import contract_of

        levels = self.levels()
        uid = "bg" + str(abs(hash((self.name, tuple(self.nodes)))) % 10**8)
        row_h, node_w, node_h = 92, 168, 46
        height = max(46 + (len(levels) - 1) * row_h + node_h + 24, 130)

        placed: dict[str, tuple[float, float]] = {}
        for y, level in enumerate(levels):
            for x, key in enumerate(level):
                cx = width / 2 + (x - (len(level) - 1) / 2) * (node_w + 26)
                placed[key] = (cx, 46 + y * row_h)

        parts: list[str] = []
        for e in self.edges:
            (x0, y0), (x1, y1) = placed[e.src], placed[e.dst]
            dash = ' stroke-dasharray="5 4"' if e.kind is EdgeKind.DEPENDENCY else ""
            mid = (y0 + node_h / 2 + y1 - node_h / 2) / 2
            parts.append(
                f'<path class="{uid}-edge" data-from="{_mid(e.src)}" data-to="{_mid(e.dst)}" '
                f'd="M{x0},{y0 + node_h/2} C{x0},{mid} {x1},{mid} {x1},{y1 - node_h/2}" '
                f'fill="none" stroke="#9aa4b2" stroke-width="1.6"{dash} '
                f'marker-end="url(#{uid}-arrow)"><title>{e.kind.value}</title></path>')

        for key, (cx, cy) in placed.items():
            node = self.nodes[key]
            c = contract_of(node)
            fill, stroke = "#eef1f5", "#8a93a0"
            if c.mutates:
                fill, stroke = "#fde2e2", "#cc3333"
            elif c.verifies:
                fill, stroke = "#e2f5e6", "#22aa44"
            flags = ", ".join(f for f in ("mutates", "verifies", "interacts", "uses_llm")
                              if getattr(c, f)) or "read-only"
            tip = (f"{key}  ({c.kind})\\nflags: {flags}"
                   f"\\nreads: {list(c.reads) or '-'}\\nwrites: {list(c.writes) or '-'}"
                   + (f"\\nselector: {c.selector}" if c.selector else "")
                   + ("\\n\\nRISKY: changes remote state, nothing verifies it"
                      if c.risky else ""))
            # An auto-named node takes its key from its kind; showing both
            # just prints the same word twice.
            label = key if len(key) <= 20 else key[:19] + "…"
            sub = "" if key == c.kind else c.kind
            parts.append(
                f'<g class="{uid}-node" id="{_mid(key)}" data-tip="{_esc(tip)}" '
                f'transform="translate({cx - node_w/2},{cy - node_h/2})">'
                f'<rect width="{node_w}" height="{node_h}" rx="9" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="1.7"/>'
                f'<text x="{node_w/2}" y="{19 if sub or c.risky else 27}" text-anchor="middle" '
                f'font-size="12.5" font-weight="600" fill="#182230">{_esc(label)}</text>'
                f'<text x="{node_w/2}" y="35" text-anchor="middle" font-size="10.5" '
                f'fill="#5b6472">{_esc(sub)}{" &#9888; unverified" if c.risky else ""}'
                f'</text></g>')

        legend = (
            '<span><i style="background:#fde2e2;border-color:#cc3333"></i>mutates</span>'
            '<span><i style="background:#e2f5e6;border-color:#22aa44"></i>verifies</span>'
            '<span><i style="background:#eef1f5;border-color:#8a93a0"></i>read-only</span>'
            '<span style="opacity:.75">dashed edge = explicit dependency</span>')

        return f"""<div class="{uid}-wrap" style="font-family:-apple-system,Segoe UI,Roboto,sans-serif">
<style>
 .{uid}-wrap{{position:relative;border:1px solid #e3e8ee;border-radius:10px;padding:8px 8px 4px;background:#fff}}
 .{uid}-wrap h4{{margin:2px 6px 0;font-size:13px;color:#3a4552;font-weight:600}}
 .{uid}-node{{cursor:pointer}}
 .{uid}-node rect{{transition:filter .12s}}
 .{uid}-node:hover rect{{filter:brightness(.95)}}
 .{uid}-dim{{opacity:.18}}
 .{uid}-tip{{position:absolute;pointer-events:none;background:#182230;color:#fff;
   padding:7px 10px;border-radius:7px;font-size:11.5px;line-height:1.5;white-space:pre;
   opacity:0;transition:opacity .12s;z-index:9;box-shadow:0 3px 12px rgba(0,0,0,.25)}}
 .{uid}-leg{{display:flex;gap:14px;flex-wrap:wrap;font-size:11px;color:#5b6472;margin:2px 8px 4px}}
 .{uid}-leg i{{display:inline-block;width:11px;height:11px;border-radius:3px;
   border:1.5px solid;margin-right:5px;vertical-align:-1px}}
</style>
<h4>{_esc(self.name)} &middot; {len(self.nodes)} nodes &middot; {len(levels)} levels</h4>
<div class="{uid}-leg">{legend}</div>
<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px;display:block">
 <defs><marker id="{uid}-arrow" viewBox="0 0 10 10" refX="9" refY="5"
   markerWidth="7" markerHeight="7" orient="auto-start-reverse">
   <path d="M0,0 L10,5 L0,10 z" fill="#9aa4b2"/></marker></defs>
 {''.join(parts)}
</svg>
<div class="{uid}-tip"></div>
<script>
(function(){{
 var wrap=document.currentScript.closest('.{uid}-wrap');
 var tip=wrap.querySelector('.{uid}-tip');
 var nodes=wrap.querySelectorAll('.{uid}-node');
 var edges=wrap.querySelectorAll('.{uid}-edge');
 // adjacency, so clicking a node can show exactly what it can reach
 var next={{}};
 edges.forEach(function(e){{
   var f=e.getAttribute('data-from');(next[f]=next[f]||[]).push(e.getAttribute('data-to'));}});
 nodes.forEach(function(n){{
  n.addEventListener('mousemove',function(ev){{
    tip.textContent=n.getAttribute('data-tip');
    var r=wrap.getBoundingClientRect();
    tip.style.left=(ev.clientX-r.left+14)+'px';
    tip.style.top=(ev.clientY-r.top+14)+'px';tip.style.opacity=1;}});
  n.addEventListener('mouseleave',function(){{tip.style.opacity=0;}});
  n.addEventListener('click',function(){{
    if(n.dataset.on==='1'){{
      nodes.forEach(function(m){{m.classList.remove('{uid}-dim');m.dataset.on='';}});
      edges.forEach(function(e){{e.classList.remove('{uid}-dim');}});return;}}
    var keep={{}},stack=[n.id];keep[n.id]=1;
    while(stack.length){{var c=stack.pop();(next[c]||[]).forEach(function(d){{
      if(!keep[d]){{keep[d]=1;stack.push(d);}}}});}}
    nodes.forEach(function(m){{m.classList.toggle('{uid}-dim',!keep[m.id]);m.dataset.on='';}});
    edges.forEach(function(e){{e.classList.toggle('{uid}-dim',
      !(keep[e.getAttribute('data-from')]&&keep[e.getAttribute('data-to')]));}});
    n.dataset.on='1';}});
 }});
}})();
</script></div>"""

    def _repr_html_(self) -> str:
        """Notebooks render a graph as its diagram when it is the last expression."""
        return self.to_html()

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
