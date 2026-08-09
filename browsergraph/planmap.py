"""The architecture the library is actually arguing for.

`spacemap` draws the *configuration* space — engine × binary × display and so
on. That is a catalogue of fixed capabilities, and a catalogue is not the point.

The point is this: **a task decomposes into a sequence of action planes, each
plane offers several interchangeable nodes that can accomplish it, and a route
through the planes is a candidate solution.** Nothing about the route is fixed.
The system tries one, watches what happens, and prefers what worked next time.

That is why a plane is not a list of features. It is a *question about the
task* — "how do I reach the page", "how do I find the thing", "how do I know it
worked" — and the candidates are the different answers this library happens to
have. Adding a node adds a candidate; the shape of the diagram changes without
anyone editing the diagram.

So the planes here are **derived**, not written down. Each plane declares a
predicate over a node's contract, and the candidates are whatever satisfies it.
`Click` appears under *act* because it declares `mutates`, not because a table
says so. That is the whole reason contracts are enforced: they are the thing
the architecture reasons over.

Routes are scored by `learn.Knowledge`, which is what makes this
self-optimising rather than merely combinatorial — the same evidence that picks
an engine after a failure picks a locator after one.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from browsergraph.contracts import Contract, contract_of
from browsergraph.dimensions import Engine

#: A plane is a question the task asks. The predicate says which nodes answer it.
#:
#: Read top to bottom this is the anatomy of every browser task there is: get
#: to the page, wait for it to be usable, find the thing, do something to it,
#: confirm it happened, take the data away.
PLANE_RULES: list[tuple[str, str, Callable[[Contract], bool]]] = [
    ("reach", "get the page at all",
     lambda c: c.kind == "navigate"),
    ("settle", "wait until it is usable",
     lambda c: c.kind in ("wait_for", "retry_until")),
    ("locate", "find the target element",
     lambda c: c.kind in ("llm_selector", "vision_locate", "healing")),
    ("act", "change something",
     lambda c: c.mutates or c.kind == "scroll"),
    ("verify", "confirm it happened",
     lambda c: c.verifies or c.kind == "screenshot"),
    ("extract", "take the data away",
     lambda c: c.kind in ("extract", "for_each", "frontier")),
]

#: Candidates that are real answers but not node classes.
#:
#: A plain CSS selector is how `click` and `extract` find their element by
#: default, and a fixed dwell is how a graph waits without asserting anything.
#: Neither is a separate class, and leaving them out would make the diagram
#: claim you must reach for a model to locate an element — the opposite of what
#: the library recommends. They are the baseline every other candidate on the
#: plane is an escalation *from*.
SYNTHETIC: dict[str, list[tuple[str, str]]] = {
    "settle": [("dwell", "fixed delay")],
    "locate": [("css", "baseline")],
    "extract": [("extract", "selector")],
}

#: `reach` is the one plane whose candidates are not nodes. Every engine is a
#: different way of getting the same page, and that choice is as much part of
#: the solution as which locator is used — which is exactly why escalation
#: swaps engines on failure.
REACH_ENGINES = (Engine.HTTP, Engine.PLAYWRIGHT, Engine.PATCHRIGHT,
                 Engine.SELENIUM, Engine.SELENIUM_UC, Engine.ZENDRIVER,
                 Engine.CAMOUFOX)


@dataclass
class Candidate:
    """One way of answering one plane's question."""
    name: str
    kind: str = "node"          # "node" | "engine"
    cost: float = 1.0           # relative expense, for ranking
    note: str = ""
    p: float | None = None      # learned success, when there is evidence
    evidence: float = 0.0

    @property
    def label(self) -> str:
        return self.name


@dataclass
class Plane:
    name: str
    question: str
    candidates: list[Candidate] = field(default_factory=list)


@dataclass
class Route:
    """One candidate solution: a choice per plane."""
    picks: dict[str, str]
    score: float = 0.0
    why: str = ""

    def as_list(self, planes: list[Plane]) -> list[str]:
        return [self.picks[p.name] for p in planes]


#: Relative expense. Not measured — declared, and used only for ranking ties, so
#: a cheap answer is preferred when the evidence does not distinguish two.
COST = {"css": 0.3, "dwell": 0.4, "http": 0.2, "playwright": 1.0, "patchright": 1.3, "selenium": 1.4,
        "selenium_uc": 2.0, "zendriver": 1.5, "camoufox": 2.4,
        "llm_selector": 3.0, "llm_verify": 3.0, "vision_locate": 4.0,
        "vision_verify": 4.0, "screenshot": 0.6, "healing": 1.2}


def planes(registry: dict | None = None) -> list[Plane]:
    """Derive the planes and their candidates from the node registry.

    Nothing here is a hard-coded capability list: a node lands on a plane
    because of what its contract *says it does*.
    """
    if registry is None:
        from browsergraph.nodes import REGISTRY
        registry = dict(REGISTRY)
        # Nodes that live outside the registry but are real candidates.
        try:
            from browsergraph.heal import Healing
            from browsergraph.vision import VisionLocate, VisionVerify
            registry |= {"healing": Healing, "vision_locate": VisionLocate,
                         "vision_verify": VisionVerify}
        except Exception:      # pragma: no cover - optional extras
            pass

    out: list[Plane] = []
    for name, question, rule in PLANE_RULES:
        plane = Plane(name=name, question=question)

        if name == "reach":
            for engine in REACH_ENGINES:
                plane.candidates.append(Candidate(
                    engine.value, kind="engine", cost=COST.get(engine.value, 1.0),
                    note="no browser" if engine is Engine.HTTP else "browser"))
        else:
            for kind, cls in sorted(registry.items()):
                c = contract_of(cls)
                if not c.kind:
                    c = Contract(kind=kind)
                if rule(c):
                    plane.candidates.append(Candidate(
                        kind, cost=COST.get(kind, 1.0),
                        note="model" if c.uses_llm else ""))

        for syn_name, note in SYNTHETIC.get(name, []):
            if not any(c.name == syn_name for c in plane.candidates):
                plane.candidates.insert(0, Candidate(
                    syn_name, kind="builtin", cost=COST.get(syn_name, 0.5), note=note))

        if plane.candidates:
            out.append(plane)
    return out


def routes(planes_: list[Plane], limit: int = 400) -> list[Route]:
    """Every combination of one candidate per plane — the candidate solutions."""
    import itertools
    names = [p.name for p in planes_]
    pools = [[c.name for c in p.candidates] for p in planes_]
    out: list[Route] = []
    for combo in itertools.product(*pools):
        out.append(Route(dict(zip(names, combo, strict=False))))
        if len(out) >= limit:
            break
    return out


#: What to believe about a candidate nobody has tried.
PRIOR = 0.5

#: Evidence needed before an observation outweighs the prior. Small, because
#: these are cheap observations, but not zero: one success is not a fact.
SHRINK = 3.0


def belief(c: Candidate) -> float:
    """A candidate's success rate, shrunk toward the prior by how little is known.

    Unobserved candidates get the prior, **not** exclusion. Scoring a route over
    only its measured steps was the first implementation and it inverted the
    whole point: a route with one well-evidenced step and five untried ones beat
    a route that was measured end to end, so "after learning" recommended the
    parts nobody had ever run. Absence of measurement is not evidence of
    quality any more than it is evidence of mediocrity.
    """
    if c.p is None:
        return PRIOR
    w = c.evidence / (c.evidence + SHRINK)
    return w * c.p + (1 - w) * PRIOR


def score_routes(planes_: list[Plane], routes_: list[Route],
                 knowledge=None, features=None) -> list[Route]:
    """Rank candidate solutions by evidence, tie-broken by cost.

    With no evidence anywhere every route scores the same and the cheapest wins
    — the right default, and an honest one: preferring cheap is a policy, not a
    prediction. As outcomes accumulate the evidence dominates, and that shift is
    the self-optimisation this architecture is claiming.
    """
    by_name = {c.name: c for p in planes_ for c in p.candidates}
    for r in routes_:
        picks = [by_name[n] for n in r.picks.values()]
        cost = sum(c.cost for c in picks)
        beliefs = [belief(c) for c in picks]
        measured = sum(1 for c in picks if c.p is not None)
        # The product, not the mean: every plane has to work for the route to
        # work, so one weak step should drag the whole route down rather than
        # be averaged away by five strong ones.
        score = 1.0
        for b in beliefs:
            score *= b
        r.score = score - 0.001 * cost
        r.why = (f"{measured}/{len(picks)} steps measured, cost {cost:.1f}"
                 if measured else f"no evidence anywhere — cheapest first (cost {cost:.1f})")
    routes_.sort(key=lambda r: -r.score)
    return routes_


def observe(planes_: list[Plane], outcomes: dict[str, tuple[int, int]]) -> None:
    """Fold observed (successes, attempts) into the candidates.

    Smoothed, so a single success is not certainty — the same Laplace treatment
    `learn` uses, for the same reason: one win is p≈0.67, n=1, not 1.0.
    """
    for plane in planes_:
        for c in plane.candidates:
            if c.name in outcomes:
                wins, tries = outcomes[c.name]
                c.p = (wins + 1) / (tries + 2)
                c.evidence = float(tries)


def to_text(planes_: list[Plane], best: Route | None = None) -> str:
    out = []
    for p in planes_:
        out.append(f"  {p.name:<8} — {p.question}")
        for c in p.candidates:
            mark = "*" if best and best.picks.get(p.name) == c.name else " "
            ev = f"  p={c.p:.2f} n={c.evidence:.0f}" if c.p is not None else ""
            out.append(f"    {mark} {c.label:<16}{('(' + c.note + ')') if c.note else '':<10}{ev}")
    if best:
        out.append("\n  chosen route: " + " -> ".join(best.as_list(planes_)))
        out.append(f"  because: {best.why}")
    return "\n".join(out)


def _sid(plane: str, value: str) -> str:
    return f"{plane}__{value}".replace("-", "_").replace(".", "_")


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def to_html(planes_: list[Plane], *, before: Route | None = None,
            after: Route | None = None, width: int = 1180,
            title: str = "one task, many routes") -> str:
    """The architecture as a picture: planes of a task, candidates, routes.

    Two routes are drawn rather than one, because a single highlighted path
    would show a *fixed* pipeline — the exact thing this design is arguing
    against. `before` is what gets chosen with no evidence; `after` is what the
    same machinery chooses once outcomes exist. The diagram is the difference
    between them.
    """
    gap = max(150, (width - 180) // max(1, len(planes_) - 1))
    rows = max(len(p.candidates) for p in planes_)
    row_h, top, left, half = 36, 108, 108, 68
    height = top + rows * row_h + 64
    uid = "pm" + str(abs(hash(tuple(p.name for p in planes_))) % 10**8)

    pos: dict[tuple[str, str], tuple[float, float]] = {}
    parts: list[str] = []

    for i, plane in enumerate(planes_):
        x = left + i * gap
        parts.append(f'<line x1="{x}" y1="{top - 40}" x2="{x}" y2="{height - 54}" '
                     f'stroke="#e1e7ee"/>')
        parts.append(f'<text x="{x}" y="{top - 58}" text-anchor="middle" '
                     f'font-size="13" font-weight="700" fill="#22303f">{plane.name}</text>')
        parts.append(f'<text x="{x}" y="{top - 43}" text-anchor="middle" '
                     f'font-size="9.5" fill="#7b8794">{_esc(plane.question)}</text>')
        for j, cand in enumerate(plane.candidates):
            y = top + j * row_h
            pos[(plane.name, cand.name)] = (x, y)
            cls = f"{uid}-c"
            if cand.kind == "engine":
                cls += f" {uid}-eng"
            elif cand.kind == "builtin":
                cls += f" {uid}-base"
            if cand.note == "model":
                cls += f" {uid}-model"
            badge = (f'<text x="{x + half - 5}" y="{y + 11}" text-anchor="end" '
                     f'font-size="7.5" fill="#8a6d1f">p={cand.p:.2f} n={cand.evidence:.0f}'
                     f'</text>' if cand.p is not None else "")
            parts.append(
                f'<g class="{cls}" data-id="{_sid(plane.name, cand.name)}">'
                f'<rect x="{x - half}" y="{y - 12}" width="{half * 2}" height="25" rx="6"/>'
                f'<text x="{x}" y="{y + 1}" text-anchor="middle" font-size="10.5">'
                f'{_esc(cand.label)}</text>'
                f'<text x="{x - half + 5}" y="{y + 11}" text-anchor="start" '
                f'font-size="7.5" fill="#8794a3">{_esc(cand.note)}</text>{badge}</g>')

    def polyline(route: Route, cls: str) -> str:
        pts = " ".join(f"{pos[(p.name, route.picks[p.name])][0] - half},"
                       f"{pos[(p.name, route.picks[p.name])][1]} "
                       f"{pos[(p.name, route.picks[p.name])][0] + half},"
                       f"{pos[(p.name, route.picks[p.name])][1]}"
                       for p in planes_)
        return f'<polyline class="{cls}" points="{pts}"/>'

    faint = []
    for route in routes(planes_, limit=600):
        faint.append(polyline(route, f"{uid}-r"))
    picked = ""
    if before:
        picked += polyline(before, f"{uid}-before")
    if after:
        picked += polyline(after, f"{uid}-after")

    legend = (
        f'<span class="{uid}-k {uid}-kb"></span>first choice — no evidence, cheapest'
        f'<span class="{uid}-k {uid}-ka"></span>after learning from outcomes'
        '<span style="opacity:.7">every faint line is another runnable route</span>')

    return f"""<div class="{uid}-wrap">
<style>
 .{uid}-wrap{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;border:1px solid #e3e8ee;
   border-radius:10px;padding:10px 12px 6px;background:#fff}}
 .{uid}-wrap h4{{margin:0 0 1px;font-size:13.5px;color:#22303f}}
 .{uid}-sub{{font-size:11px;color:#6b7785;margin:0 0 6px}}
 .{uid}-r{{fill:none;stroke:#9fb4cc;stroke-width:1;opacity:.10}}
 .{uid}-before{{fill:none;stroke:#c98a2b;stroke-width:2.6;opacity:.95;
   stroke-dasharray:7 4}}
 .{uid}-after{{fill:none;stroke:#1f8a4c;stroke-width:3;opacity:.95}}
 .{uid}-c rect{{fill:#eef1f5;stroke:#aab3bf;fill-opacity:.96}}
 .{uid}-c text{{fill:#22303f}}
 .{uid}-eng rect{{fill:#e8f0fb;stroke:#2d6cb5}}
 .{uid}-base rect{{fill:#f3f6f2;stroke:#7fa07f;stroke-dasharray:4 3}}
 .{uid}-model rect{{fill:#fdf3e2;stroke:#c98a2b}}
 .{uid}-leg{{font-size:10.5px;color:#6b7785;margin:4px 0 0;display:flex;
   gap:16px;align-items:center;flex-wrap:wrap}}
 .{uid}-k{{display:inline-block;width:20px;height:0;border-top-width:3px;
   border-top-style:solid;margin-right:5px;vertical-align:middle}}
 .{uid}-kb{{border-color:#c98a2b;border-top-style:dashed}}
 .{uid}-ka{{border-color:#1f8a4c}}
</style>
<h4>{_esc(title)}</h4>
<p class="{uid}-sub">a task is decomposed into planes; each plane offers
 interchangeable candidates; a route through them is one candidate solution</p>
<svg viewBox="0 0 {width} {height}" width="100%" style="display:block">
{''.join(faint)}{picked}{''.join(parts)}</svg>
<p class="{uid}-leg">{legend}</p>
</div>"""
