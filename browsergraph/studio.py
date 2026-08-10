"""Turning a workbench into one file you can open.

The template is an asset rather than a Python string so it can be edited as
HTML, with a syntax highlighter and a linter, instead of as a wall of escaped
quotes. Rendering is a substitution and nothing more: the viewer is an adapter
over serialized data, never part of the runtime, and it must not be able to
change what the graph means.

Self-contained is a hard requirement. These files get opened from a laptop, a CI
artifact, a notebook output cell and an offline machine, and anything fetched
from a CDN is missing in at least two of those.
"""
from __future__ import annotations

import json
import pathlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from browsergraph.workbench import WorkbenchDefinition

ASSETS = pathlib.Path(__file__).resolve().parent / "assets"
TEMPLATE = ASSETS / "workbench-studio-template.html"

#: The projections, and the file each becomes in a suite.
VIEWS = {
    "candidates": "all-candidates.html",
    "network": "path-network.html",
    "compare": "compare-routes.html",
    "builder": "build-route.html",
    "feedback": "feedback-loop.html",
}


def template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _embed(data: dict) -> str:
    """JSON safe to sit inside a <script> element.

    `</script>` anywhere in the data — in a description, a URL, a node name —
    ends the element early and produces a blank page with a syntax error, which
    is a memorable way to learn that HTML is not JSON's parent context. The
    forward slash escape is legal JSON and defuses it.
    """
    return (json.dumps(data, ensure_ascii=False)
            .replace("</", "<\\/")
            .replace(" ", "\\u2028")
            .replace(" ", "\\u2029"))


def render(workbench: WorkbenchDefinition, view: str = "candidates") -> str:
    if view not in VIEWS:
        raise ValueError(f"unknown view {view!r}; known: {', '.join(VIEWS)}")
    html = template()
    title = workbench.title or "Universal graph solution studio"
    return (html.replace("__TITLE__", title.replace("<", "&lt;"))
                .replace("__DATA__", _embed(workbench.to_dict()))
                .replace("__VIEW__", view))
