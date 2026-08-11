"""Domain packs: templates that come with the code already written.

`templates.py` gives you eleven problem shapes. Every one of them ships with
**zero candidates** — the shape says a step called `numeric` turns a `Frame`
into a `Matrix`, and then you write that function, and the one after it, and the
nine after that. The shape was the easy half.

A pack is the other half: real candidates for a real template, with real
implementations, so a domain goes from "expressible" to "runnable" in two calls.

    from browsergraph import packs, solve

    pack = packs.get("files")
    answer = pack.solve(verify=solve.outputs_are_not_empty("summarise"))

    # or the long way, when the folder is yours rather than the example's
    answer = solve.solve(pack.workbench(), pack.runtime(folder="/data/inbox"),
                         verify=solve.outputs_are_not_empty("summarise"))

Three rules every pack here keeps, and they are the interesting part:

**Standard library only.** The core of this project has no dependencies and that
is a claim on the front page. A pack that quietly required numpy would make
`import browsergraph.packs` a lie about the install. The implementations are
therefore plain Python and honest about being baselines — which is the point of
a graph whose candidates are swappable: replace `fit.least_squares` with
scikit-learn and nothing else in the graph changes.

**More than one candidate per step, or none.** A step with a single candidate is
a hard-coded choice wearing a graph's clothes. Where a pack offers only one way
to do something, it says so rather than pretending there was a decision.

**They actually run.** `example()` returns arguments that work, and a test runs
**every route of every pack**, not a sample — a pack is small enough to try
exhaustively, and "most routes work" is the claim that hides the one that does
not. A pack nobody has executed is a template with extra steps.

Packs are not a plugin system and deliberately not extensible from here: they
are worked examples that happen to be importable. Copy one, change it, keep the
half you liked.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:                                   # pragma: no cover
    from browsergraph.execute import Runtime
    from browsergraph.workbench import WorkbenchDefinition


@dataclass(frozen=True)
class Pack:
    """A domain, with the code written.

    Deliberately four fields and no lifecycle. A pack is a value you can print,
    compare and put in a registry — the moment it grows a `setup()` it stops
    being a worked example and becomes a framework.
    """
    name: str
    template: str
    summary: str
    #: Returns a filled, validated workbench. A callable rather than a value so
    #: importing the registry does not build eleven graphs nobody asked for.
    workbench: Callable[[], WorkbenchDefinition]
    #: Takes whatever `example()` returns, as keyword arguments.
    runtime: Callable[..., Runtime]
    #: **Configuration for `runtime()`, not graph inputs.** The distinction is
    #: real and this is where people trip: a source step — one that reads a
    #: folder or opens a file — has no input *ports*, so "which folder" cannot
    #: arrive as graph data. Ports carry values between steps; where the first
    #: step gets its material is configuration, and it belongs to the runtime.
    #:
    #: That is not a gap to work around. Letting `inputs=` reach a step with no
    #: declared port would be untyped data entering through the back door, which
    #: is the exact thing the port discipline exists to prevent.
    example: Callable[[], dict[str, Any]]

    def solve(self, **kwargs):
        """Build it, run it, and return the best route. The two-line version."""
        from browsergraph import solve as _solve

        return _solve.solve(self.workbench(), self.runtime(**self.example()),
                            **kwargs)


def available() -> tuple[str, ...]:
    return ("files", "quality", "tabular")


def get(name: str) -> Pack:
    """One pack by name, imported on demand."""
    if name not in available():
        raise KeyError(f"no pack {name!r}; available: {', '.join(available())}")

    import importlib

    module = importlib.import_module(f"browsergraph.packs.{name}")
    return Pack(name=name, template=module.TEMPLATE, summary=module.SUMMARY,
                workbench=module.workbench, runtime=module.runtime,
                example=module.example)


def catalog_text() -> str:
    """One line per pack, for a terminal and for a model deciding which to use."""
    lines = []
    for name in available():
        pack = get(name)
        bench = pack.workbench()
        lines.append(f"{name:<10} {pack.summary}")
        lines.append(f"{'':<10} template {pack.template} · "
                     f"{len(bench.leaf_stages)} steps · "
                     f"{bench.route_count():,} routes")
    return "\n".join(lines)
