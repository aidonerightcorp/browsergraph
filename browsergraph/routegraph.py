"""The candidate path network as a static picture.

The interactive studio is the real tool, but it cannot go in a README: GitHub
strips scripts from embedded HTML, Kaggle strips `<style>` blocks, and neither
will run a viewer. So this renders the same data as a plain SVG with every
colour inlined as an attribute — no CSS block, no script, no external anything.

The picture makes one argument, and it should survive being looked at for three
seconds: **stages are ordered columns, every candidate is in its column, and a
route is one choice per column.** Everything else on the canvas is subordinate
to that.

Two constraints learned the hard way and encoded here:

* edges are drawn in the gutters *between* columns, never across a label, or
  2,827 hairlines run through every candidate name;
* the palette is fixed and mid-contrast rather than theme-aware, because a
  README image cannot know whether it is being viewed on a light or dark page,
  and a picture that is invisible on half of GitHub is worse than a plain one.
"""
from __future__ import annotations

import html
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from browsergraph.workbench import WorkbenchDefinition

#: Fixed and mid-contrast: readable on a white page and on a dark one.
INK = "#1b2430"
MUTED = "#6b7684"
LINE = "#c9d1da"
PANEL = "#ffffff"
FRAME = "#dde3ea"
ROUTE_COLOURS = {
    "baseline": "#0d7d84",
    "learned": "#7b3fe4",
    "candidate": "#2f6fed",
}


@dataclass
class Layout:
    column_width: int = 272
    gutter: int = 88
    row_height: int = 15
    # Clear of the subtitle: at 84 the summary line ran into the stage headers.
    top: int = 104
    pad: int = 22
    font: float = 8.6
    #: Fits "browser adapter · Chrome · BrowserPort · headless" (48 chars).
    #: Truncating shorter made the headless and headed rows identical — the one
    #: binding a reader most needs to tell apart.
    label_chars: int = 54


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def to_svg(workbench: WorkbenchDefinition, *,
           routes: Sequence[str] = (), samples: int = 0,
           background: bool = True, layout: Layout | None = None,
           title: str = "") -> str:
    """One SVG, self-contained, no CSS block and no script.

    `routes` names the solutions to draw. `samples` adds that many deterministic
    random complete routes as faint context — seeded, so the same call always
    produces byte-identical output and the file does not churn in git.
    """
    box = layout or Layout()
    stages = list(workbench.stages)
    if not stages:
        return "<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'/>"

    candidates = workbench.candidates_by_id
    tallest = max(len(stage.candidates) for stage in stages)
    width = (box.pad * 2 + len(stages) * box.column_width
             + (len(stages) - 1) * box.gutter)
    height = box.top + tallest * box.row_height + 58

    place: dict[str, tuple[float, float]] = {}
    for index, stage in enumerate(stages):
        x = box.pad + index * (box.column_width + box.gutter)
        for row, cid in enumerate(stage.candidates):
            place[cid] = (x, box.top + row * box.row_height)

    out: list[str] = [
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}' "
        f"width='{width}' height='{height}' font-family='ui-sans-serif,"
        f"-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif' "
        f"role='img' aria-label='Candidate path network'>",
        f"<rect x='0' y='0' width='{width}' height='{height}' rx='14' "
        f"fill='{PANEL}' stroke='{FRAME}'/>",
    ]

    heading = title or workbench.title
    out.append(f"<text x='{box.pad}' y='30' font-size='17' font-weight='650' "
               f"fill='{INK}'>{_esc(heading)}</text>")
    out.append(f"<text x='{box.pad}' y='50' font-size='11.5' fill='{MUTED}'>"
               f"{_esc(workbench.summary())}</text>")

    # --- background structure, in the gutters only
    if background:
        out.append(f"<g stroke='{LINE}' stroke-width='0.4' opacity='0.30'>")
        for left, right in zip(stages, stages[1:], strict=False):
            for a in left.candidates:
                ax, ay = place[a]
                for b in right.candidates:
                    bx, by = place[b]
                    out.append(f"<line x1='{ax + box.column_width:.0f}' "
                               f"y1='{ay:.0f}' x2='{bx:.0f}' y2='{by:.0f}'/>")
        out.append("</g>")

    if samples:
        rng = random.Random(20260810)
        out.append(f"<g stroke='{MUTED}' stroke-width='0.7' opacity='0.30' "
                   f"fill='none'>")
        for _ in range(samples):
            picked = [rng.choice(stage.candidates) for stage in stages]
            out.append(_route_path(picked, place, box))
        out.append("</g>")

    # --- the named routes
    wanted = [s for s in workbench.solutions if not routes or s.id in routes]
    for solution in wanted:
        colour = ROUTE_COLOURS.get(solution.status, ROUTE_COLOURS["candidate"])
        picked = [solution.route.get(stage.id, "") for stage in stages]
        out.append(f"<g stroke='{colour}' stroke-width='2.4' fill='none' "
                   f"stroke-linecap='round'>")
        out.append(_route_path(picked, place, box))
        out.append("</g>")

    # --- columns, dots and labels, drawn last so they sit above every edge
    highlighted: dict[str, str] = {}
    for solution in wanted:
        for stage in stages:
            cid = solution.route.get(stage.id, "")
            if cid:
                highlighted[cid] = ROUTE_COLOURS.get(solution.status,
                                                     ROUTE_COLOURS["candidate"])

    for index, stage in enumerate(stages):
        x = box.pad + index * (box.column_width + box.gutter)
        out.append(f"<text x='{x}' y='{box.top - 26}' font-size='12.5' "
                   f"font-weight='650' fill='{INK}'>"
                   f"{index + 1}. {_esc(stage.name)}</text>")
        out.append(f"<text x='{x}' y='{box.top - 11}' font-size='10.5' "
                   f"fill='{MUTED}'>{len(stage.candidates)} candidates &#183; "
                   f"{_esc(stage.input_type)} &#8594; "
                   f"{_esc(stage.output_type)}</text>")
        for cid in stage.candidates:
            cx, cy = place[cid]
            marked = highlighted.get(cid, "")
            out.append(f"<circle cx='{cx:.0f}' cy='{cy:.0f}' "
                       f"r='{3.6 if marked else 2.6}' "
                       f"fill='{marked or MUTED}' "
                       f"opacity='{1 if marked else 0.45}'/>")
            name = candidates[cid].name if cid in candidates else cid
            out.append(f"<text x='{cx + 8:.0f}' y='{cy + 3.2:.0f}' "
                       f"font-size='{box.font}' "
                       f"fill='{marked or MUTED}' "
                       f"font-weight='{600 if marked else 400}'>"
                       f"{_esc(_clip(name, box.label_chars))}</text>")

    out.append(_legend(wanted, width, height, box))
    out.append("</svg>")
    return "\n".join(out)


def _route_path(ids: Sequence[str], place: Mapping[str, tuple[float, float]],
                box: Layout) -> str:
    """A stub across each label band, then one segment through each gutter."""
    parts = []
    for index, cid in enumerate(ids):
        if cid not in place:
            continue
        x, y = place[cid]
        parts.append(f"<line x1='{x:.0f}' y1='{y:.0f}' "
                     f"x2='{x + box.column_width:.0f}' y2='{y:.0f}' "
                     f"opacity='0.45'/>")
        nxt = ids[index + 1] if index + 1 < len(ids) else ""
        if nxt in place:
            nx, ny = place[nxt]
            parts.append(f"<line x1='{x + box.column_width:.0f}' y1='{y:.0f}' "
                         f"x2='{nx:.0f}' y2='{ny:.0f}'/>")
    return "".join(parts)


def _legend(solutions, width: int, height: int, box: Layout) -> str:
    parts = [f"<g font-size='11' fill='{MUTED}'>"]
    x = box.pad
    y = height - 20
    for solution in solutions[:6]:
        colour = ROUTE_COLOURS.get(solution.status, ROUTE_COLOURS["candidate"])
        parts.append(f"<line x1='{x}' y1='{y - 4}' x2='{x + 22}' y2='{y - 4}' "
                     f"stroke='{colour}' stroke-width='2.6'/>")
        label = f"{solution.name} ({solution.status})"
        parts.append(f"<text x='{x + 28}' y='{y}' fill='{INK}'>"
                     f"{_esc(label)}</text>")
        x += 34 + int(len(label) * 5.9)
    parts.append(f"<text x='{width - box.pad}' y='{y}' text-anchor='end'>"
                 f"edges join adjacent stages only &#183; no backward or "
                 f"diagonal order</text>")
    parts.append("</g>")
    return "".join(parts)


def write_svg(workbench: WorkbenchDefinition, path: str, **kw) -> str:
    import pathlib

    pathlib.Path(path).write_text(to_svg(workbench, **kw), encoding="utf-8")
    return path
