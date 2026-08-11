"""Process a folder: list it, do the same work to each file, split, summarise.

The most ordinary batch job there is, and the one whose failure mode is worst:
a loop that dies on file eleven of four thousand and reports "the batch failed".
Expressed as a graph, the per-file work is a **map step** — the node handles one
item, the library handles all of them, and a failure names the item.

    from browsergraph import packs
    print(packs.get("files").solve().text())

Four choices, so there is something to search over: how to list, how to read one
file, what counts as a bad record, and how to summarise. Thirty-six routes.

Everything is standard library, and the parsers are real rather than stubs — the
pack is meant to be pointed at a folder you care about. `example()` writes one
so it runs with no arguments at all.

The example folder is mixed on purpose, and the result is the interesting part.
`parse.json` throws on the CSV and names the item; `parse.csv` reads the JSON
files as nonsense and the split step discards them. Neither is *wrong* — both
run — and only a verifier that counts records can tell them apart from
`parse.auto`, which reads all four. The search finds that rather than being told,
which is the whole argument for keeping candidates plural.
"""
from __future__ import annotations

import csv
import io
import json
import pathlib
import statistics
import tempfile
from typing import Any

from browsergraph.execute import Runtime
from browsergraph.manifest import NodeManifest, PortSpec
from browsergraph.templates import get as _template
from browsergraph.workbench import (
    NodeCandidate,
    OptimizationObjective,
    OptimizationProfile,
    WorkbenchDefinition,
)

TEMPLATE = "batch.files"
SUMMARY = "read every file in a folder, keep the good records, total them up"

#: Which candidate fills which slot. Written here rather than inferred, because
#: "which step can this node do" is a claim the manifest makes and this is where
#: it gets checked against the template's own slot list.
FILLING: dict[str, list[str]] = {
    "list":      ["list.folder", "list.recursive"],
    "each":      ["parse.json", "parse.csv", "parse.auto"],
    "split":     ["split.required", "split.strict"],
    "summarise": ["summarise.count", "summarise.stats", "summarise.by_kind"],
}


def _node(node_id: str, capability: str, takes, gives, *, description: str,
          **extra) -> NodeManifest:
    return NodeManifest(
        id=node_id, kind=node_id.split(".")[-1], description=description,
        capabilities=(capability,),
        inputs=tuple(PortSpec(n, t) for n, t in takes),
        outputs=tuple(PortSpec(n, t) for n, t in gives),
        metrics={"source": "illustrative-prior", "quality": 0.9},
        **extra)


NODES: tuple[NodeManifest, ...] = (
    _node("list.folder", "io.list", [], [("out", "List[Path]")],
          description="Every file directly in the folder.",
          permissions=("filesystem:read",)),
    _node("list.recursive", "io.list", [], [("out", "List[Path]")],
          description="Every file in the folder and every folder under it.",
          permissions=("filesystem:read",)),

    # A map node takes ONE item and returns ONE record. It has no idea there
    # are others, which is what makes "item 11 failed" possible to say.
    _node("parse.json", "work.one", [("in", "Path")], [("out", "Record")],
          description="Read one file as JSON.",
          permissions=("filesystem:read",)),
    _node("parse.csv", "work.one", [("in", "Path")], [("out", "Record")],
          description="Read one file as CSV and summarise its rows.",
          permissions=("filesystem:read",)),
    # The one that should win on a mixed folder, and the search finds that
    # rather than being told. Without it the pack's own example has no route
    # that reads every file: whichever single parser you pick, the other
    # format either throws or parses to nonsense.
    _node("parse.auto", "work.one", [("in", "Path")], [("out", "Record")],
          description="Read one file as whatever its extension says it is.",
          permissions=("filesystem:read",)),

    _node("split.required", "split.outcome", [("in", "List[Record]")],
          [("ok", "List[Record]"), ("bad", "List[Record]")],
          description="Bad means a required field is missing."),
    _node("split.strict", "split.outcome", [("in", "List[Record]")],
          [("ok", "List[Record]"), ("bad", "List[Record]")],
          description="Bad means anything went wrong, including a soft warning."),

    _node("summarise.count", "summarise", [("in", "List[Record]")],
          [("out", "Summary")], description="How many, and how many were not."),
    _node("summarise.stats", "summarise", [("in", "List[Record]")],
          [("out", "Summary")], description="Count, plus size mean and median."),
    _node("summarise.by_kind", "summarise", [("in", "List[Record]")],
          [("out", "Summary")], description="Counts grouped by file kind."),
)


def workbench() -> WorkbenchDefinition:
    """The template, filled and validated."""
    template = _template(TEMPLATE)
    bench = template.instantiate(FILLING)
    return WorkbenchDefinition(
        title=template.title, task=template.task,
        stages=bench.stages, edges=bench.edges, nodes=NODES,
        candidates=tuple(NodeCandidate(id=n.id, node_id=n.id) for n in NODES),
        optimization_profiles=(OptimizationProfile(
            id="p.quality", name="Get the most records",
            objectives=(OptimizationObjective("quality", "maximize", 1.0),)),),
        metadata=dict(bench.metadata, pack="files"),
    ).assert_valid()


# --- the implementations ----------------------------------------------------

def _list(folder: str, *, deep: bool) -> list[str]:
    root = pathlib.Path(folder)
    found = root.rglob("*") if deep else root.glob("*")
    return sorted(str(p) for p in found if p.is_file())


def _read_json(path: str) -> dict[str, Any]:
    text = pathlib.Path(path).read_text(encoding="utf-8")
    body = json.loads(text)
    return {"path": path, "kind": "json", "bytes": len(text),
            "fields": sorted(body) if isinstance(body, dict) else [],
            "value": body.get("value") if isinstance(body, dict) else None,
            "warnings": [] if isinstance(body, dict) else ["not an object"]}


def _read_csv(path: str) -> dict[str, Any]:
    text = pathlib.Path(path).read_text(encoding="utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    numbers = [float(r["value"]) for r in rows
               if str(r.get("value", "")).replace(".", "", 1).isdigit()]
    return {"path": path, "kind": "csv", "bytes": len(text),
            "fields": sorted(rows[0]) if rows else [],
            "value": sum(numbers) if numbers else None,
            "warnings": [] if rows else ["no rows"]}


def _read_auto(path: str) -> dict[str, Any]:
    return (_read_csv(path) if path.lower().endswith(".csv")
            else _read_json(path))


def _split(records, *, strict: bool):
    ok, bad = [], []
    for record in records:
        # "Required field missing" and "something warned" are different
        # standards, and which one you want depends on what happens downstream.
        # That is a decision, so it is two candidates rather than a flag.
        failed = record.get("value") is None
        if strict:
            failed = failed or bool(record.get("warnings"))
        (bad if failed else ok).append(record)
    return {"ok": ok, "bad": bad}


def _summary_count(records) -> dict[str, Any]:
    return {"files": len(records),
            "total": sum(r["value"] for r in records if r.get("value"))}


def _summary_stats(records) -> dict[str, Any]:
    sizes = [r["bytes"] for r in records] or [0]
    return {**_summary_count(records),
            "bytes_mean": round(statistics.fmean(sizes), 1),
            "bytes_median": statistics.median(sizes)}


def _summary_by_kind(records) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    for record in records:
        kinds[record["kind"]] = kinds.get(record["kind"], 0) + 1
    return {**_summary_count(records), "by_kind": kinds}


def runtime(folder: str) -> Runtime:
    """One function per candidate, reading from `folder`.

    The folder is bound here and not passed as a graph input, because the
    listing step has no input *ports* — it is a source. Ports carry values
    between steps; where the first step gets its material is configuration.
    The same plan pointed at a different folder is the same plan, which is
    exactly why it is not part of the plan.
    """
    def listing(deep: bool):
        return lambda **kwargs: _list(str(folder), deep=deep)

    return Runtime({
        "list.folder": listing(False),
        "list.recursive": listing(True),
        "parse.json": lambda **kw: _read_json(kw["in"]),
        "parse.csv": lambda **kw: _read_csv(kw["in"]),
        "parse.auto": lambda **kw: _read_auto(kw["in"]),
        "split.required": lambda **kw: _split(kw["in"], strict=False),
        "split.strict": lambda **kw: _split(kw["in"], strict=True),
        "summarise.count": lambda **kw: _summary_count(kw["in"]),
        "summarise.stats": lambda **kw: _summary_stats(kw["in"]),
        "summarise.by_kind": lambda **kw: _summary_by_kind(kw["in"]),
    })


def example() -> dict[str, Any]:
    """Arguments for `runtime()`: a folder of files that already exists.

    Written to a temporary directory so the pack runs with no arguments and
    leaves nothing behind in the caller's working directory. One file is
    deliberately missing its value, because a batch demo where everything
    succeeds demonstrates the easy half — and the `split` step exists precisely
    to have somewhere to put the one that did not.
    """
    folder = pathlib.Path(tempfile.mkdtemp(prefix="browsergraph-files-"))
    (folder / "a.json").write_text(json.dumps({"value": 3, "name": "a"}))
    (folder / "b.json").write_text(json.dumps({"value": 4, "name": "b"}))
    (folder / "c.json").write_text(json.dumps({"name": "no value here"}))
    (folder / "d.csv").write_text("name,value\nx,5\ny,6\n")
    return {"folder": str(folder)}
