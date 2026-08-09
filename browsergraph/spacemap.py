"""The dimension space, drawn as parallel planes.

`Spec` is a point in a nine-dimensional space, and prose is a bad way to convey
what that space looks like. A table of counts tells you 980 of 9000 combinations
are runnable; it does not tell you *which* choices are the expensive ones, which
values are nearly free, or that picking `stealth=undetected` eliminates most of
the engine column in one move.

So: one vertical plane per dimension, its values stacked within it, and every
runnable Spec drawn as a polyline threading exactly one value per plane. The
shape of the bundle is the answer — a value that carries few lines is a value
that constrains everything downstream of it.

Two honesty problems this has to solve, because a diagram that lies is worse
than no diagram:

**Not every path can be drawn.** Nine axes multiply out to millions of
combinations. Drawing a sample and presenting it as the whole is exactly the
kind of quiet dishonesty the rest of this library exists to avoid, so the
render states how many paths it drew, out of how many, and by what rule it
chose them.

**Absence must be distinguishable from rejection.** A value with no lines
through it may be unreachable given the current filter, or unreachable at all.
Those are different facts and they are rendered differently.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from browsergraph.dimensions import (
    Binary,
    Capture,
    Display,
    Engine,
    LLMControl,
    Preprocess,
    Spec,
    Stealth,
    Transport,
    Vision,
    validate,
)

#: The planes, in the order a reader should meet them: what runs it, what it
#: runs, where, how it looks, how hard it hides, then what it produces.
PLANES: list[tuple[str, type]] = [
    ("engine", Engine),
    ("binary", Binary),
    ("transport", Transport),
    ("display", Display),
    ("stealth", Stealth),
    ("preprocess", Preprocess),
    ("vision", Vision),
    ("capture", Capture),
    ("llm", LLMControl),
]

#: Spec fields that are not named after their enum.
FIELD = {"llm": "llm"}


#: Fields that are not dimensions but which some dimension values require.
#:
#: `transport=remote_cdp` is invalid without an endpoint, and `capture=video`
#: without an artifact directory — so with a bare Spec() those values look
#: *unreachable*, when in truth they are reachable and simply need one more
#: field set. The diagram is about which dimension values can coexist, so it
#: supplies these and judges each value on its own merits. Without them the
#: picture says "you cannot use video", which is false and discouraging in the
#: same breath.
REQUIRED_SIBLINGS = {"endpoint": "ws://127.0.0.1:9222",
                     "artifact_dir": "/tmp/browsergraph"}


def ground_spec() -> Spec:
    """A Spec with the non-dimension fields those values need, and nothing else."""
    return Spec(endpoint=REQUIRED_SIBLINGS["endpoint"],
                artifact_dir=REQUIRED_SIBLINGS["artifact_dir"])


def _spec_from(choice: dict[str, object], base: Spec | None = None) -> Spec:
    """Build a Spec from a {plane: value} mapping.

    Deliberately dynamic — the planes are data — so the enum types cannot be
    checked statically here. `validate` is what decides whether the result is
    meaningful, and it runs on every one of these.
    """
    from dataclasses import replace as dc_replace
    kwargs: dict = dict(choice)
    llm = kwargs.pop("llm", None)
    spec = dc_replace(base, **kwargs) if base is not None else Spec(**kwargs)
    if llm is not None:
        spec = dc_replace(spec, llm=dc_replace(spec.llm, mode=llm))
    return spec


@dataclass
class Space:
    """Which paths through the space are runnable, and how many were examined."""

    planes: list[tuple[str, list[str]]] = field(default_factory=list)
    paths: list[list[str]] = field(default_factory=list)
    examined: int = 0
    total: int = 0
    truncated: bool = False
    rule: str = ""

    @property
    def valid(self) -> int:
        return len(self.paths)

    def value_counts(self) -> dict[tuple[str, str], int]:
        """How many drawn paths pass through each value."""
        counts: dict[tuple[str, str], int] = {}
        for (name, values) in self.planes:
            for v in values:
                counts[(name, v)] = 0
        for path in self.paths:
            for (name, _), value in zip(self.planes, path, strict=False):
                counts[(name, value)] = counts.get((name, value), 0) + 1
        return counts

    def summary(self) -> str:
        head = (f"{self.valid:,} runnable of {self.examined:,} examined"
                + (f" (of {self.total:,} total)" if self.total > self.examined else ""))
        return f"{head} — {self.rule}" if self.rule else head


def explore(axes: dict[str, list] | None = None, *, limit: int = 1500,
            base: Spec | None = None, seed: int = 7) -> Space:
    """Walk the space, keeping the runnable paths.

    Two modes, chosen by size, because the honest answer differs:

    **exhaustive** — when the space is small enough to enumerate, every runnable
    path is found and the per-value counts are exact.

    **sampled** — otherwise, combinations are drawn uniformly at random and
    validated. Nine axes multiply out to millions; enumerating them takes
    minutes and no diagram needs every line.

    What it must *not* do is take the first N in enumeration order. That was the
    first implementation and it produced a diagram that was worse than none:
    `itertools.product` varies the last axis fastest, so the first 4,000
    runnable paths were all playwright/bundled_chromium/local, and every other
    engine and binary was rendered as unreachable. They are reachable. A biased
    sample drawn as if it were the whole space is exactly the failure this
    library exists to avoid, so the sampling is uniform and the render says
    which mode produced it.

    `seed` is fixed so the same space always draws the same picture.
    """
    import random

    chosen: dict[str, list] = axes or {n: list(e) for n, e in PLANES}  # type: ignore[call-overload]
    names = [n for n, _ in PLANES if n in chosen]
    values = [list(chosen[n]) for n in names]

    total = 1
    for v in values:
        total *= len(v)

    space = Space(
        planes=[(n, [getattr(v, "value", str(v)) for v in chosen[n]]) for n in names],
        total=total,
    )

    ground = base if base is not None else ground_spec()

    def keep(combo) -> bool:
        if validate(_spec_from(dict(zip(names, combo, strict=False)), ground)):
            return False
        space.paths.append([getattr(v, "value", str(v)) for v in combo])
        return True

    #: Enumerating beyond this takes long enough to be felt in a notebook cell.
    EXHAUSTIVE_MAX = 300_000

    if total <= EXHAUSTIVE_MAX:
        for combo in itertools.product(*values):
            space.examined += 1
            keep(combo)
        space.rule = f"exhaustive — every one of {total:,} combinations checked"
        if len(space.paths) > limit:
            # Thin for legibility only, evenly across the whole valid set, so
            # the picture stays representative.
            step = len(space.paths) / limit
            space.paths = [space.paths[int(i * step)] for i in range(limit)]
            space.truncated = True
            space.rule += f"; {limit:,} drawn, evenly spaced"
        return space

    rng = random.Random(seed)
    # Cap attempts so a space that is almost entirely invalid still terminates.
    attempts = 0
    while len(space.paths) < limit and attempts < limit * 400:
        attempts += 1
        space.examined += 1
        keep(tuple(rng.choice(v) for v in values))
    space.truncated = True
    space.rule = (f"uniform random sample — {len(space.paths):,} runnable of "
                  f"{space.examined:,} drawn at random from {total:,}")
    return space


def _sid(name: str, value: str) -> str:
    return f"{name}__{value}".replace("-", "_").replace(".", "_")


def to_html(space: Space, *, width: int = 1320, plane_gap: int = 146,
            title: str = "browsergraph dimension space") -> str:
    """A self-contained interactive parallel-planes diagram.

    Inline SVG and a little JS: no CDN, no library, no network, so it renders
    the same in a notebook, a saved file and offline.

    Hovering a value highlights every runnable path through it. Clicking locks
    that choice and dims the values no longer reachable — which is the question
    the diagram exists to answer: *what does this choice cost me?*
    """
    counts = space.value_counts()
    rows = max((len(v) for _, v in space.planes), default=1)
    row_h, top, left = 30, 92, 96
    height = top + rows * row_h + 62
    width = max(width, left * 2 + plane_gap * max(1, len(space.planes) - 1))
    uid = "sp" + str(abs(hash(tuple(n for n, _ in space.planes))) % 10**8)

    pos: dict[tuple[str, str], tuple[float, float]] = {}
    parts: list[str] = []

    for i, (name, values) in enumerate(space.planes):
        x = left + i * plane_gap
        parts.append(f'<line x1="{x}" y1="{top - 34}" x2="{x}" y2="{height - 52}" '
                     f'stroke="#dfe5ec" stroke-width="1"/>')
        parts.append(f'<text x="{x}" y="{top - 46}" text-anchor="middle" '
                     f'font-size="12" font-weight="700" fill="#2b3444">{name}</text>')
        for j, value in enumerate(values):
            y = top + j * row_h
            pos[(name, value)] = (x, y)
            n = counts.get((name, value), 0)
            dead = " " + uid + "-dead" if n == 0 else ""
            parts.append(
                f'<g class="{uid}-v{dead}" data-plane="{name}" data-value="{value}" '
                f'data-id="{_sid(name, value)}">'
                f'<rect x="{x - 54}" y="{y - 10}" width="108" height="21" rx="5"/>'
                f'<text x="{x}" y="{y + 5}" text-anchor="middle" font-size="10.5">'
                f'{value}</text></g>')

    # Two points per plane — the box's left and right edge — so each path runs
    # *through* its value and the diagonals between planes stay visible. Joining
    # box centres instead buries the whole bundle behind the labels, which was
    # the first attempt and made the picture unreadable.
    half = 54
    lines: list[str] = []
    for path in space.paths:
        pts = " ".join(
            f"{pos[(n, v)][0] - half},{pos[(n, v)][1]} "
            f"{pos[(n, v)][0] + half},{pos[(n, v)][1]}"
            for (n, _), v in zip(space.planes, path, strict=False))
        ids = " ".join(_sid(n, v) for (n, _), v in zip(space.planes, path, strict=False))
        lines.append(f'<polyline class="{uid}-p" data-ids="{ids}" points="{pts}"/>')

    note = space.summary()
    if space.truncated:
        note += "  ⚠ truncated"

    return f"""<div class="{uid}-wrap">
<style>
 .{uid}-wrap{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;border:1px solid #e3e8ee;
   border-radius:10px;padding:10px 12px 6px;background:#fff;position:relative}}
 .{uid}-wrap h4{{margin:0 0 2px;font-size:13.5px;color:#22303f}}
 .{uid}-note{{font-size:11px;color:#68737f;margin:0 0 4px}}
 .{uid}-p{{fill:none;stroke:#2d6cb5;stroke-width:1;opacity:.09;
   stroke-linejoin:round}}
 .{uid}-p.on{{stroke:#c0392b;stroke-width:1.5;opacity:.75}}
 .{uid}-v rect{{fill:#eef1f5;fill-opacity:.94;stroke:#aab3bf;stroke-width:1}}
 .{uid}-v text{{fill:#22303f;pointer-events:none}}
 .{uid}-v{{cursor:pointer}}
 .{uid}-v.sel rect{{fill:#fde2e2;stroke:#c0392b;stroke-width:2}}
 .{uid}-v.off rect{{fill:#f6f7f9;stroke:#e4e8ed}} .{uid}-v.off text{{fill:#b9c0c9}}
 .{uid}-dead rect{{fill:#fff;stroke:#eceff3;stroke-dasharray:3 3}}
 .{uid}-dead text{{fill:#c8ced6;text-decoration:line-through}}
 .{uid}-legend{{font-size:10.5px;color:#68737f;margin:2px 0 0}}
</style>
<h4>{title}</h4>
<p class="{uid}-note">{note}</p>
<svg viewBox="0 0 {width} {height}" width="100%" style="display:block">{''.join(lines)}
{''.join(parts)}</svg>
<p class="{uid}-legend">hover a value to trace its paths &middot; click to lock a choice
 &middot; struck-through values are unreachable</p>
<script>
(function(){{
 var w=document.currentScript.closest('.{uid}-wrap');
 var vs=w.querySelectorAll('.{uid}-v'), ps=w.querySelectorAll('.{uid}-p');
 var locked={{}};
 function survives(p){{
   var ids=p.getAttribute('data-ids').split(' ');
   for(var k in locked){{ if(ids.indexOf(k)<0) return false; }}
   return true;
 }}
 function paint(hover){{
   var live={{}}, any=false;
   ps.forEach(function(p){{
     var ok=survives(p);
     var ids=p.getAttribute('data-ids').split(' ');
     if(ok){{ any=true; ids.forEach(function(i){{live[i]=1;}}); }}
     var lit = ok && (!hover || ids.indexOf(hover)>=0);
     p.classList.toggle('on', !!(lit && (hover || Object.keys(locked).length)));
     p.style.display = ok ? '' : 'none';
   }});
   vs.forEach(function(v){{
     var id=v.getAttribute('data-id');
     v.classList.toggle('sel', !!locked[id]);
     v.classList.toggle('off', any && !live[id] && !v.classList.contains('{uid}-dead'));
   }});
 }}
 vs.forEach(function(v){{
   var id=v.getAttribute('data-id');
   v.addEventListener('mouseenter',function(){{paint(id);}});
   v.addEventListener('mouseleave',function(){{paint(null);}});
   v.addEventListener('click',function(){{
     var plane=v.getAttribute('data-plane');
     // one lock per plane: a Spec picks exactly one value per dimension
     vs.forEach(function(o){{ if(o.getAttribute('data-plane')===plane)
        delete locked[o.getAttribute('data-id')]; }});
     if(!v.classList.contains('sel')) locked[id]=1;
     paint(null);
   }});
 }});
 paint(null);
}})();
</script></div>"""


def to_text(space: Space, top: int = 6) -> str:
    """A terminal summary: which values carry the most runnable paths."""
    counts = space.value_counts()
    out = [space.summary(), ""]
    for name, values in space.planes:
        ranked = sorted(values, key=lambda v: -counts.get((name, v), 0))
        bits = []
        for v in ranked[:top]:
            n = counts.get((name, v), 0)
            bits.append(f"{v}={n}" if n else f"{v}=0(unreachable)")
        extra = f"  (+{len(ranked) - top} more)" if len(ranked) > top else ""
        out.append(f"  {name:<11} {', '.join(bits)}{extra}")
    return "\n".join(out)
