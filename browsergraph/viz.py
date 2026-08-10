"""Pictures of any workbench, not just this one's domain.

`spacemap` and `planmap` draw browsers: their planes are engines, binaries and
transports, named in code. That was fine while the only task was browsing, and
it is exactly the wrong shape for a system that claims a tabular pipeline, a
document extractor and a shipping-notification workflow are the same kind of
object. A visualiser that has to be rewritten per domain is a visualiser that
quietly says the core is not general.

So everything here takes a `WorkbenchDefinition` and nothing else. If a domain
can be expressed as stages, typed ports, edges and candidates — the only things
the compiler knows about — it can be drawn, and no drawing code changes.

Four pictures, each answering a question the others cannot:

* `dag` — *what shape is this?* Layers, fan-out, joins. The picture that would
  have caught "this is a pipeline, not a graph" on sight, because a diamond
  drawn as a chain looks obviously wrong and a diamond counted as a product
  looks merely large.
* `route_space` — *what am I choosing between?* One column per sub-step, one box
  per candidate, one line per route. Hover to trace, click to lock.
* `funnel` — *how much of that did we actually look at?* The honest counter:
  total, legal, eligible, evaluated, chosen — with the ratio spelled out rather
  than implied.
* `evidence` — *why did it pick that?* Per-step outcomes in bits.

Implementation notes, both learned the hard way and both non-obvious:

1. Colours are **inline attributes**, never CSS classes. Kaggle's notebook
   viewer drops the `<style>` block, and a diagram styled only by CSS collapses
   there into unreadable boxes on whatever background the host theme uses. The
   stylesheet carries interaction only, so losing it costs hover, not legibility.
2. Every id is namespaced with a per-figure `uid`. Two figures in one notebook
   otherwise share element ids and the second one's clicks drive the first.
"""
from __future__ import annotations

import html
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from browsergraph.workbench import WorkbenchDefinition

# One palette, used by every figure here, so four pictures of the same run read
# as one document. Deliberately not the notebook's default matplotlib cycle:
# these are drawn as SVG and have to sit on white, on Kaggle's near-white, and
# on a dark README preview without becoming invisible.
INK = "#22303f"
MUTED = "#68737f"
LINE = "#dfe5ec"
FILL = "#eef1f5"
EDGE = "#8a93a0"
CHOSEN = "#c0392b"
ALT = "#c98a2b"
GOOD = "#1f8a4c"
COLD = "#2d6cb5"


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _uid(seed: str) -> str:
    return "bg" + str(abs(hash(seed)) % 10**8)


def _fmt(n: float) -> str:
    """Big numbers as words, because 4212739891200 is not a readable quantity.

    The point of these figures is often that a space is absurdly large; a reader
    who has to count digits to discover that has been shown the number and told
    nothing.
    """
    n = float(n)
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= limit:
            return f"{n / limit:.3g}{suffix}"
    return f"{n:,.0f}" if n == int(n) else f"{n:.3g}"


@dataclass(frozen=True)
class Figure:
    """An SVG fragment that knows how to become a page, a file, or a cell.

    Kept as a value rather than written straight to disk so a notebook can show
    it, a test can assert on it, and a report can concatenate several without
    any of them having touched the filesystem.
    """
    svg: str
    title: str = ""
    note: str = ""
    width: int = 1100
    height: int = 460

    def _repr_html_(self) -> str:
        return self.html(standalone=False)

    @staticmethod
    def _svg(body: str, width: int, height: int) -> str:
        """A fixed-width SVG that scrolls, rather than a fluid one that shrinks.

        `width="100%"` looks responsive and is a trap: a fourteen-layer graph is
        2,850px wide, and fitting that into a 1,000px column scales the labels
        to four pixels tall. Unreadable-but-complete is worse than
        scroll-to-see, because it is not obvious anything is wrong. The wrapper
        supplies `overflow-x:auto`, so wide figures get a scrollbar and normal
        ones simply fit.
        """
        return (f'<svg viewBox="0 0 {width} {height}" width="{width}" '
                f'height="{height}" style="max-width:none" role="img">'
                f'{body}</svg>')

    def html(self, standalone: bool = True) -> str:
        body = (f'<div style="background:#fff;color:{INK};border:1px solid #e3e8ee;'
                f'border-radius:10px;padding:12px 14px 8px;margin:0 0 14px;'
                f'font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
                f'max-width:100%;overflow-x:auto">'
                + (f'<h4 style="margin:0 0 2px;font-size:13.5px;color:{INK}">'
                   f'{_esc(self.title)}</h4>' if self.title else "")
                + (f'<p style="font-size:11px;color:{MUTED};margin:0 0 6px">'
                   f'{_esc(self.note)}</p>' if self.note else "")
                + self.svg + "</div>")
        if not standalone:
            return body
        return ('<!doctype html><html><head><meta charset="utf-8">'
                f'<title>{_esc(self.title or "browsergraph")}</title></head>'
                '<body style="margin:0;padding:18px;background:#f6f8fa">'
                + body + "</body></html>")

    def save(self, path) -> str:
        import pathlib
        target = pathlib.Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.html(standalone=True), encoding="utf-8")
        return str(target)


# --- 1. the shape -----------------------------------------------------------

def dag(bench: WorkbenchDefinition, *, route: Mapping[str, str] | None = None,
        width: int = 1100, title: str = "") -> Figure:
    """The graph as layers, with typed edges and joins drawn as joins.

    Position is meaning here: a stage sits in the layer equal to its longest
    path from a source, so two boxes side by side are genuinely independent and
    can run at once. A chain renders as one box per layer — which is the honest
    picture of a pipeline, and looks visibly different from a graph.
    """
    layers = bench.layers()
    leaves = {s.id: s for s in bench.leaf_stages}
    if not layers or not leaves:
        return Figure('<svg width="10" height="10"></svg>', title or "empty graph")

    uid = _uid("dag" + bench.title + str(len(leaves)))
    rows = max(len(layer) for layer in layers)
    box_w, box_h = 186, 52
    # Candidate ids are namespaced and long — `demo.session.browser.chrome.
    # browserport.headless.5b950ef5` is precise and unreadable in a 186px box.
    # The display name is what a person recognises; the id stays in `to_json`
    # for anything that needs to act on it.
    names = {c.id: c.name for c in bench.candidates}
    gap_x = max(210, (width - 120) // max(1, len(layers)))
    top, left = 74, 60
    height = top + rows * (box_h + 30) + 70
    width = max(width, left * 2 + gap_x * max(1, len(layers) - 1))

    pos: dict[str, tuple[float, float]] = {}
    parts: list[str] = []

    for i, layer in enumerate(layers):
        x = left + i * gap_x
        parts.append(f'<text x="{x + box_w / 2}" y="{top - 30}" text-anchor="middle" '
                     f'font-size="10" fill="{MUTED}">layer {i}'
                     + (f' · {len(layer)} parallel' if len(layer) > 1 else '')
                     + '</text>')
        # Centre each layer vertically so a wide layer reads as a fan rather
        # than as a list that happens to start at the top.
        offset = (rows - len(layer)) * (box_h + 30) / 2
        for j, stage_id in enumerate(layer):
            y = top + offset + j * (box_h + 30)
            pos[stage_id] = (x, y)
            stage = leaves[stage_id]
            n = len(stage.candidates)
            picked = (route or {}).get(stage_id)
            border = CHOSEN if picked else EDGE
            kind = getattr(stage, "kind", "atomic")

            # A map stage runs its node once per item and a branch takes one of
            # its outputs. Drawing all three the same made the picture say
            # something untrue the moment those kinds existed — and the whole
            # case for these diagrams is that the shape is visible.
            #
            # A doubled outline for map (many runs, one box) and a dashed one
            # for branch (only one way out is taken). Shape, not just a word,
            # so it survives being skimmed.
            shadow = ""
            if kind == "map":
                shadow = (f'<rect x="{x + 5}" y="{y + 5}" width="{box_w}" '
                          f'height="{box_h}" rx="7" fill="none" stroke="{EDGE}" '
                          f'stroke-width="1" opacity=".45"/>')
            dash = ' stroke-dasharray="6 3"' if kind == "branch" else ""

            parts.append(
                f'<g>{shadow}<rect x="{x}" y="{y}" width="{box_w}" '
                f'height="{box_h}" rx="7" fill="{FILL}" stroke="{border}" '
                f'stroke-width="{2 if picked else 1}"{dash}/>'
                f'<text x="{x + 9}" y="{y + 19}" font-size="11.5" font-weight="700" '
                f'fill="{INK}">{_esc(stage.name[:22])}</text>'
                + (f'<text x="{x + box_w - 9}" y="{y + 19}" text-anchor="end" '
                   f'font-size="9" font-weight="700" fill="{COLD}">'
                   f'{kind.upper()}</text>' if kind != "atomic" else '')
                + f'<text x="{x + 9}" y="{y + 34}" font-size="9.5" fill="{MUTED}">'
                f'{n} candidate{"s" if n != 1 else ""}'
                + (" · per item" if kind == "map" else
                   " · one way out" if kind == "branch" else "") + '</text>'
                + (f'<text x="{x + 9}" y="{y + 46}" font-size="9.5" fill="{CHOSEN}">'
                   f'{_esc(names.get(picked, picked)[:26])}</text>' if picked else '')
                + '</g>')

    # Edges last so they never sit under a box, and labelled with the port when
    # one is named — an unlabelled join is the bug that started all this.
    for edge in bench.wiring():
        if edge.source not in pos or edge.target not in pos:
            continue
        x1, y1 = pos[edge.source]
        x2, y2 = pos[edge.target]
        sx, sy = x1 + box_w, y1 + box_h / 2
        tx, ty = x2, y2 + box_h / 2
        mid = (sx + tx) / 2
        parts.append(f'<path d="M{sx},{sy} C{mid},{sy} {mid},{ty} {tx},{ty}" '
                     f'fill="none" stroke="{EDGE}" stroke-width="1.4" opacity=".75" '
                     f'marker-end="url(#{uid}-arrow)"/>')
        if edge.to_port or edge.from_port:
            label = edge.to_port or edge.from_port
            parts.append(f'<text x="{(sx + tx) / 2}" y="{(sy + ty) / 2 - 5}" '
                         f'text-anchor="middle" font-size="9" fill="{MUTED}">'
                         f'{_esc(label)}</text>')

    shape = ("a chain" if bench.is_chain
             else f"{len(layers)} layers, widest {rows}")
    kinds = {getattr(s, "kind", "atomic") for s in bench.leaf_stages} - {"atomic"}
    if kinds:
        shape += (". " + " and ".join(
            "a doubled outline runs once per item" if k == "map"
            else "a dashed outline takes only one way out"
            for k in sorted(kinds, reverse=True)).capitalize())
    defs = (f'<defs><marker id="{uid}-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="7" markerHeight="7" orient="auto-start-end">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{EDGE}"/></marker></defs>')
    svg = Figure._svg(defs + "".join(parts), width, height)
    return Figure(svg, title or f"{bench.title} — shape",
                  f"{shape}. Boxes in the same layer are independent and may run "
                  f"together; every arrow is a typed port-to-port connection.",
                  width, height)


# --- 2. what is being chosen between ----------------------------------------

def route_space(bench: WorkbenchDefinition, *,
                route: Mapping[str, str] | None = None,
                alternative: Mapping[str, str] | None = None,
                max_rows: int = 14, max_paths: int = 260,
                width: int = 1180, title: str = "") -> Figure:
    """Parallel coordinates over the sub-steps: every column a decision.

    Two routes are drawn rather than one, because a single highlighted path
    shows a *fixed* pipeline — the exact thing this design argues against. The
    picture is the difference between them.

    `max_rows` and `max_paths` are caps, and when they bite the figure says so
    in its own caption. A diagram that silently drew 14 of 76 candidates would
    be worse than no diagram, because it looks complete.
    """
    leaves = [s for s in bench.leaf_stages if s.candidates]
    if not leaves:
        return Figure('<svg width="10" height="10"></svg>', title or "no candidates")

    uid = _uid("rs" + bench.title + str(len(leaves)))
    shown = [(s, list(s.candidates[:max_rows])) for s in leaves]
    hidden = sum(max(0, len(s.candidates) - max_rows) for s in leaves)
    rows = max(len(c) for _, c in shown)
    names = {c.id: c.name for c in bench.candidates}

    row_h, top, left, half = 30, 96, 108, 62
    gap = max(158, (width - 2 * left) // max(1, len(shown) - 1))
    height = top + rows * row_h + 74
    width = max(width, left * 2 + gap * max(1, len(shown) - 1))

    pos: dict[tuple[str, str], tuple[float, float]] = {}
    parts: list[str] = []

    for i, (stage, cands) in enumerate(shown):
        x = left + i * gap
        parts.append(f'<line x1="{x}" y1="{top - 34}" x2="{x}" y2="{height - 56}" '
                     f'stroke="{LINE}" stroke-width="1"/>')
        parts.append(f'<text x="{x}" y="{top - 50}" text-anchor="middle" '
                     f'font-size="12" font-weight="700" fill="{INK}">'
                     f'{_esc(stage.name[:20])}</text>')
        parts.append(f'<text x="{x}" y="{top - 38}" text-anchor="middle" '
                     f'font-size="9" fill="{MUTED}">{len(stage.candidates)} options</text>')
        for j, cid in enumerate(cands):
            y = top + j * row_h
            pos[(stage.id, cid)] = (x, y)
            picked = (route or {}).get(stage.id) == cid
            other = (alternative or {}).get(stage.id) == cid
            fill = "#fdeceb" if picked else ("#fdf5e7" if other else FILL)
            stroke = CHOSEN if picked else (ALT if other else EDGE)
            label = names.get(cid, cid).replace("_", " ")
            parts.append(
                f'<g class="{uid}-v" data-stage="{_esc(stage.id)}" '
                f'data-cid="{_esc(cid)}">'
                f'<rect x="{x - half}" y="{y - 10}" width="{half * 2}" height="21" '
                f'rx="5" fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
                f'<text x="{x}" y="{y + 5}" text-anchor="middle" font-size="10" '
                f'fill="{INK}">{_esc(label[:18])}</text></g>')

    # Sample paths: the background bundle exists to show density, not to be read
    # individually, so a deterministic stride beats a random sample — the same
    # workbench draws the same picture twice, which matters for a doc committed
    # to a repo.
    def _paths() -> Iterable[list[str]]:
        widths = [len(c) for _, c in shown]
        total = math.prod(widths) if widths else 0
        if not total:
            return []
        stride = max(1, total // max_paths)
        out = []
        for n in range(0, total, stride):
            combo, rest = [], n
            for w in widths:
                combo.append(rest % w)
                rest //= w
            out.append([shown[i][1][k] for i, k in enumerate(combo)])
        return out

    lines = []
    for path in _paths():
        pts = " ".join(f"{pos[(shown[i][0].id, cid)][0] - half},"
                       f"{pos[(shown[i][0].id, cid)][1]} "
                       f"{pos[(shown[i][0].id, cid)][0] + half},"
                       f"{pos[(shown[i][0].id, cid)][1]}"
                       for i, cid in enumerate(path))
        lines.append(f'<polyline points="{pts}" fill="none" stroke="{COLD}" '
                     f'stroke-width="1" opacity=".07"/>')

    for chosen, colour, dash in ((route, CHOSEN, ""),
                                 (alternative, ALT, '7 4')):
        if not chosen:
            continue
        pts = []
        for stage, _cands in shown:
            cid = chosen.get(stage.id)
            if cid is None or (stage.id, cid) not in pos:
                continue
            x, y = pos[(stage.id, cid)]
            pts.append(f"{x - half},{y} {x + half},{y}")
        if pts:
            lines.append(f'<polyline points="{" ".join(pts)}" fill="none" '
                         f'stroke="{colour}" stroke-width="2.6" opacity=".95"'
                         + (f' stroke-dasharray="{dash}"' if dash else '') + '/>')

    caption = (f"{_fmt(bench.route_count())} complete routes over "
               f"{len(bench.leaf_stages)} sub-steps.")
    if hidden:
        caption += f" {hidden} candidates not drawn (row cap {max_rows})."
    if route and alternative:
        caption += "  Dashed amber: chosen with no evidence. Solid red: chosen after."

    style = (f'<style>.{uid}-v{{cursor:pointer}}'
             f'.{uid}-v:hover rect{{stroke:{CHOSEN};stroke-width:2}}</style>')
    svg = Figure._svg(style + "".join(lines) + "".join(parts), width, height)
    return Figure(svg, title or f"{bench.title} — the space of routes",
                  caption, width, height)


# --- 3. how much of it was looked at ----------------------------------------

def funnel(stages: Sequence[tuple[str, float]], *, width: int = 1000,
           title: str = "search space", note: str = "") -> Figure:
    """Total → legal → eligible → evaluated → chosen, with the ratios shown.

    Takes plain `(label, count)` pairs rather than a search result object, so a
    proposal, a simulation or a hand-written argument can all be drawn the same
    way — and so this module never has to import the searcher.

    Bar length is logarithmic. On a linear scale a funnel from 4.2 trillion to 1
    renders as one bar and four invisible slivers, which shows nothing; the axis
    is labelled as log so the picture is not quietly lying about proportion.
    """
    stages = [(str(k), float(v)) for k, v in stages if float(v) >= 0]
    if not stages:
        return Figure('<svg width="10" height="10"></svg>', title)

    row_h, top, left, bar_max = 46, 66, 190, width - 330
    height = top + len(stages) * row_h + 46
    top_value = max(v for _, v in stages) or 1
    parts: list[str] = []

    for i, (label, value) in enumerate(stages):
        y = top + i * row_h
        # log1p keeps a zero-count stage visible as a hairline rather than
        # vanishing, which matters because "nothing was eligible" is a result.
        frac = math.log1p(value) / math.log1p(top_value) if top_value > 0 else 0
        bar = max(2.0, frac * bar_max)
        colour = GOOD if i == len(stages) - 1 else COLD
        parts.append(
            f'<text x="{left - 14}" y="{y + 17}" text-anchor="end" font-size="11.5" '
            f'font-weight="700" fill="{INK}">{_esc(label)}</text>'
            f'<rect x="{left}" y="{y}" width="{bar:.1f}" height="26" rx="4" '
            f'fill="{colour}" opacity="{0.22 + 0.5 * frac:.2f}" stroke="{colour}" '
            f'stroke-width="1"/>'
            f'<text x="{left + bar + 10:.1f}" y="{y + 17}" font-size="11" '
            f'fill="{INK}">{_fmt(value)}</text>')
        if i:
            before = stages[i - 1][1]
            ratio = (f"÷{_fmt(before / value)}" if value else "→ none")
            parts.append(f'<text x="{left + bar + 74:.1f}" y="{y + 17}" '
                         f'font-size="10" fill="{MUTED}">{_esc(ratio)}</text>')

    parts.append(f'<text x="{left}" y="{height - 18}" font-size="9.5" '
                 f'fill="{MUTED}">bar length is log-scaled; labels are exact counts</text>')
    svg = Figure._svg("".join(parts), width, height)
    return Figure(svg, title, note or "Every row is a real filter, in order.",
                  width, height)


# --- 4. why it picked that --------------------------------------------------

def evidence(steps: Mapping[str, float], *, width: int = 940,
             title: str = "evidence per step",
             note: str = "") -> Figure:
    """Signed bits per step: what each observation actually told us.

    Signed, because "this step is fine" and "this step is the problem" are
    different findings and a magnitude-only chart merges them. Bits, because
    that is the unit in which "how much did I learn" is comparable across steps
    that ran different numbers of times.
    """
    items = [(str(k), float(v)) for k, v in steps.items()]
    if not items:
        return Figure('<svg width="10" height="10"></svg>', title)

    row_h, top, left = 30, 58, 200
    height = top + len(items) * row_h + 40
    span = max((abs(v) for _, v in items), default=1) or 1
    axis = left + (width - left - 90) / 2
    half = (width - left - 90) / 2
    parts = [f'<line x1="{axis}" y1="{top - 14}" x2="{axis}" y2="{height - 30}" '
             f'stroke="{LINE}" stroke-width="1"/>',
             f'<text x="{axis}" y="{height - 14}" text-anchor="middle" '
             f'font-size="9.5" fill="{MUTED}">0 bits</text>']

    for i, (label, bits) in enumerate(items):
        y = top + i * row_h
        w = abs(bits) / span * half
        x = axis if bits >= 0 else axis - w
        colour = GOOD if bits >= 0 else CHOSEN
        parts.append(
            f'<text x="{left - 16}" y="{y + 15}" text-anchor="end" font-size="11" '
            f'fill="{INK}">{_esc(label)}</text>'
            f'<rect x="{x:.1f}" y="{y + 2}" width="{max(w, 1.5):.1f}" height="18" '
            f'rx="3" fill="{colour}" opacity=".72"/>'
            f'<text x="{(x + w + 8) if bits >= 0 else (x - 8):.1f}" y="{y + 15}" '
            f'font-size="10" text-anchor="{"start" if bits >= 0 else "end"}" '
            f'fill="{MUTED}">{bits:+.2f}</text>')

    svg = Figure._svg("".join(parts), width, height)
    return Figure(svg, title,
                  note or "Positive: this step supported the route. Negative: it "
                          "argued against it.", width, height)


# --- putting them together --------------------------------------------------

def report(bench: WorkbenchDefinition, *,
           route: Mapping[str, str] | None = None,
           alternative: Mapping[str, str] | None = None,
           search: Sequence[tuple[str, float]] | None = None,
           bits: Mapping[str, float] | None = None,
           title: str = "") -> str:
    """One self-contained page: shape, space, funnel, evidence.

    Self-contained means no CDN, no fonts, no fetch — it opens from a file:// URL
    on a machine with no network, which is the only kind of artefact worth
    committing next to the code that produced it.
    """
    figures = [dag(bench, route=route), route_space(bench, route=route,
                                                    alternative=alternative)]
    if search:
        figures.append(funnel(search))
    if bits:
        figures.append(evidence(bits))

    head = (f'<h2 style="font:700 19px -apple-system,Segoe UI,Roboto,sans-serif;'
            f'color:{INK};margin:0 0 4px">{_esc(title or bench.title)}</h2>'
            f'<p style="font:400 12px -apple-system,Segoe UI,Roboto,sans-serif;'
            f'color:{MUTED};margin:0 0 16px">{_esc(bench.task)}</p>')
    return ('<!doctype html><html><head><meta charset="utf-8">'
            f'<title>{_esc(title or bench.title)}</title></head>'
            '<body style="margin:0;padding:22px;background:#f6f8fa">'
            + head + "".join(f.html(standalone=False) for f in figures)
            + "</body></html>")


def write_report(bench: WorkbenchDefinition, path, **kwargs) -> str:
    import pathlib
    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report(bench, **kwargs), encoding="utf-8")
    return str(target)


def to_mermaid(bench: WorkbenchDefinition,
               route: Mapping[str, str] | None = None) -> str:
    """The same graph as Mermaid, for places that render it and not SVG.

    GitHub renders Mermaid in Markdown and does not render arbitrary inline SVG
    in a README, so a project whose whole argument is a picture needs both.
    """
    leaves = {s.id: s for s in bench.leaf_stages}
    lines = ["graph LR"]
    for stage_id, stage in leaves.items():
        picked = (route or {}).get(stage_id)
        kind = getattr(stage, "kind", "atomic")
        label = stage.name + (f"<br/><i>{picked}</i>" if picked else
                              f"<br/>{len(stage.candidates)} options")
        if kind != "atomic":
            label += f"<br/>[{kind}]"
        # Mermaid's own shapes carry the meaning where it has one: a subroutine
        # box for map, a rhombus for branch.
        open_, close = (("[[", "]]") if kind == "map"
                        else ("{{", "}}") if kind == "branch" else ("[", "]"))
        lines.append(f'  {stage_id}{open_}"{label}"{close}')
    for edge in bench.wiring():
        port = f'|{edge.to_port}|' if edge.to_port else ""
        lines.append(f"  {edge.source} -->{port} {edge.target}")
    for stage_id in (route or {}):
        if stage_id in leaves:
            lines.append(f"  style {stage_id} stroke:{CHOSEN},stroke-width:2px")
    return "\n".join(lines)


def to_json(bench: WorkbenchDefinition,
            route: Mapping[str, str] | None = None) -> str:
    """The drawing's own data, for a front-end that wants to render it itself.

    Emitting this costs nothing and removes the argument that adopting the
    format means adopting this renderer.
    """
    return json.dumps({
        "title": bench.title,
        "layers": bench.layers(),
        "stages": [{"id": s.id, "name": s.name,
                    "kind": getattr(s, "kind", "atomic"),
                    "candidates": list(s.candidates),
                    "inputs": [p.name + ":" + p.type for p in s.inputs],
                    "outputs": [p.name + ":" + p.type for p in s.outputs]}
                   for s in bench.leaf_stages],
        "edges": [e.to_dict() for e in bench.wiring()],
        "route": dict(route or {}),
        "route_count": bench.route_count(),
    }, indent=2)
