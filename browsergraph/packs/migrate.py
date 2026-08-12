"""Move data from one store to another, and prove it arrived.

Counting the source, counting the target, and comparing the two is the whole
job. It is also the job most often declared finished one step early, because a
write that silently dropped every third row does not raise either.

    from browsergraph import packs
    print(packs.get("migrate").solve().text())

The template this fills carries three anti-patterns, and the pack is built so
each one can be *shown* rather than asserted:

**"Declaring success because the write did not raise."** `write.lossy` drops
every third record and returns normally. Every route through it completes.

**"Counting the target only."** The graph counts before and after, and the
counts meet at a join — so the comparison exists in the shape, not in somebody's
discipline.

**"Reconciling on totals alone."** `write.scrambled` writes exactly as many
records as it read, with the values corrupted. `reconcile.count` passes it.
`reconcile.checksum` does not. Both ship, which is the only way that difference
is a lesson instead of a claim.

Everything is standard library and everything writes to a temporary folder, so
the pack runs with no arguments and leaves nothing behind.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
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

TEMPLATE = "data.migrate"
SUMMARY = "copy records between stores and prove nothing was lost or changed"

#: Note that the two counting steps are filled from *different* candidates. They
#: are the same capability and the same output type, but not the same operation:
#: the source counter is handed records that are already in memory, while the
#: target counter is handed a write receipt and has to go and read the
#: destination back. The template says so in its types — `count_after` takes a
#: `Receipt` — and the type is what makes the anti-pattern unwriteable rather
#: than merely discouraged.
FILLING: dict[str, list[str]] = {
    "read":         ["read.jsonl", "read.csv"],
    "count_before": ["count.source_rows", "count.source_checksum"],
    "transform":    ["transform.none", "transform.rename"],
    "write":        ["write.jsonl", "write.lossy", "write.scrambled"],
    "count_after":  ["count.target_rows", "count.target_checksum"],
    "reconcile":    ["reconcile.count", "reconcile.checksum"],
}


def _node(node_id: str, capability: str, takes, gives, *,
          description: str, **extra) -> NodeManifest:
    return NodeManifest(
        id=node_id, kind=node_id.split(".")[-1], description=description,
        capabilities=(capability,),
        inputs=tuple(PortSpec(n, t) for n, t in takes),
        outputs=tuple(PortSpec(n, t) for n, t in gives),
        metrics={"source": "illustrative-prior", "quality": 0.9},
        **extra)


NODES: tuple[NodeManifest, ...] = (
    _node("read.jsonl", "data.read", [], [("out", "Records")],
          description="Read records from a JSON Lines file.",
          permissions=("filesystem:read",)),
    _node("read.csv", "data.read", [], [("out", "Records")],
          description="Read records from a CSV file.",
          permissions=("filesystem:read",)),

    # Two tallies, and the difference between them is the whole argument. A
    # count says how many; a checksum says which. Only one of those can tell a
    # migration that swapped contents from one that did not.
    _node("count.source_rows", "data.count", [("in", "Records")],
          [("out", "Tally")],
          description="How many records were read from the source."),
    _node("count.source_checksum", "data.count", [("in", "Records")],
          [("out", "Tally")],
          description="How many were read, and a digest of what they contain."),

    # These take a `Receipt`, not `Records` — so they cannot count what the
    # writer handed back and must open the destination instead. The type is
    # doing the work: "count the target only" is not discouraged here, it is
    # unwriteable, because a receipt does not contain any records to count.
    _node("count.target_rows", "data.count", [("in", "Receipt")],
          [("out", "Tally")],
          description="Read the destination back and count what is in it."),
    _node("count.target_checksum", "data.count", [("in", "Receipt")],
          [("out", "Tally")],
          description="Read the destination back; count it and digest it."),

    _node("transform.none", "data.transform", [("in", "Records")],
          [("out", "Records")],
          description="Copy the records unchanged. Doing nothing is a candidate."),
    _node("transform.rename", "data.transform", [("in", "Records")],
          [("out", "Records")],
          description="Rename fields to the destination's names."),

    _node("write.jsonl", "data.write", [("in", "Records")], [("out", "Receipt")],
          description="Write every record to the destination.",
          effects=("file.write",), permissions=("filesystem:write",)),
    # Both of these complete without raising, which is the point of including
    # them. A pack where every candidate works cannot demonstrate a verifier.
    _node("write.lossy", "data.write", [("in", "Records")], [("out", "Receipt")],
          description="Write records, dropping every third. Raises nothing.",
          effects=("file.write",), permissions=("filesystem:write",)),
    _node("write.scrambled", "data.write", [("in", "Records")],
          [("out", "Receipt")],
          description="Write the right number of records with corrupted values.",
          effects=("file.write",), permissions=("filesystem:write",)),

    _node("reconcile.count", "data.reconcile",
          [("before", "Tally"), ("after", "Tally")], [("out", "Verdict")],
          description="Accept when the counts match. Cannot see a swap."),
    _node("reconcile.checksum", "data.reconcile",
          [("before", "Tally"), ("after", "Tally")], [("out", "Verdict")],
          description="Accept when the counts and the content digests match."),
)


def workbench() -> WorkbenchDefinition:
    template = _template(TEMPLATE)
    bench = template.instantiate(FILLING)
    return WorkbenchDefinition(
        title=template.title, task=template.task,
        stages=bench.stages, edges=bench.edges, nodes=NODES,
        candidates=tuple(NodeCandidate(id=n.id, node_id=n.id) for n in NODES),
        optimization_profiles=(OptimizationProfile(
            id="p.faithful", name="Move everything, change nothing",
            objectives=(OptimizationObjective("quality", "maximize", 1.0),)),),
        metadata=dict(bench.metadata, pack="migrate"),
    ).assert_valid()


# --- the implementations ----------------------------------------------------

def _digest(records) -> str:
    """A digest of the contents, over values and not over field names.

    Two deliberate blindnesses, each of which would otherwise make the strict
    reconciler useless on exactly the migrations it is meant to check.

    *Order-independent*, because a store that returns rows in a different
    sequence has not lost anything.

    *Name-independent*, because renaming fields is the single most common thing
    a migration does. A digest that changed when `id` became `record_id` could
    only ever report "you migrated", which is not news — and it would fail
    `transform.rename` on every run, teaching the reader that the strict
    reconciler cries wolf. What must survive a migration is the content; the
    schema is allowed to move.
    """
    parts = sorted(json.dumps(sorted(map(str, r.values())), sort_keys=True)
                   for r in records)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def _count(records, *, with_digest: bool) -> dict[str, Any]:
    tally = {"records": len(records)}
    if with_digest:
        tally["digest"] = _digest(records)
    return tally


def _rename(records, mapping: dict[str, str]):
    return [{mapping.get(k, k): v for k, v in row.items()} for row in records]


def _write(records, target: pathlib.Path, *, mode: str):
    if mode == "lossy":
        kept = [r for i, r in enumerate(records) if (i + 1) % 3]
    elif mode == "scrambled":
        # The same number of records, with the values ruined. This is the case
        # a count cannot see, and the reason the pack ships two reconcilers.
        kept = [{k: (f"?{v}" if isinstance(v, str) else v) for k, v in r.items()}
                for r in records]
    else:
        kept = list(records)

    target.write_text("\n".join(json.dumps(r, sort_keys=True) for r in kept),
                      encoding="utf-8")
    # The receipt names the file, not the rows. A writer reporting its own row
    # count is the writer marking its own homework — the count that matters is
    # taken by reading the destination back.
    return {"path": str(target), "bytes": target.stat().st_size}


def _read_back(receipt) -> list[dict[str, Any]]:
    text = pathlib.Path(receipt["path"]).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _reconcile(before, after, *, strict: bool) -> dict[str, Any]:
    same_count = before.get("records") == after.get("records")
    problems = [] if same_count else [
        f"{before.get('records')} records before, {after.get('records')} after"]

    checked_content = False
    if strict:
        left, right = before.get("digest"), after.get("digest")
        if left is None or right is None:
            # The strict reconciler needs digests to be strict *with*. Saying so
            # is the difference between "no problems found" and "could not look".
            problems.append("no content digest available — count the records "
                            "with count.checksum to compare contents")
        else:
            checked_content = True
            if left != right:
                problems.append("the contents differ")

    return {"ok": not problems, "problems": problems,
            "checked_content": checked_content,
            "records": after.get("records", 0)}


def runtime(source: str, destination: str, csv_source: str | None = None,
            renames: dict[str, str] | None = None) -> Runtime:
    """One function per candidate, moving `source` to `destination`.

    The paths are bound here rather than passed as graph inputs, because the
    reading step is a source: it has no input ports, and where the first step
    gets its material is configuration.

    `csv_source` is a second file holding the same records in the other format.
    Pointing the CSV reader at the JSON file would be cheaper and would not
    raise — `csv.DictReader` will happily treat a line of JSON as a header row —
    but it would produce nonsense that then migrates *consistently*, so every
    check downstream would pass. A candidate that quietly succeeds at the wrong
    thing is the failure this pack is about; it should not be in the fixture.
    """
    mapping = renames or {"id": "record_id", "name": "full_name"}
    target = pathlib.Path(destination)

    def read(as_csv: bool):
        def call(**kwargs):
            path = pathlib.Path(csv_source if as_csv else source)
            text = path.read_text(encoding="utf-8")
            if as_csv:
                import csv
                import io
                return [dict(row) for row in csv.DictReader(io.StringIO(text))]
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        return call

    def count_source(with_digest: bool):
        return lambda **kw: _count(kw["in"], with_digest=with_digest)

    def count_target(with_digest: bool):
        # Handed a receipt, so the only way to a number is to open the file.
        return lambda **kw: _count(_read_back(kw["in"]), with_digest=with_digest)

    return Runtime({
        "read.jsonl": read(False),
        "read.csv": read(True),
        "count.source_rows": count_source(False),
        "count.source_checksum": count_source(True),
        "count.target_rows": count_target(False),
        "count.target_checksum": count_target(True),
        "transform.none": lambda **kw: list(kw["in"]),
        "transform.rename": lambda **kw: _rename(kw["in"], mapping),
        "write.jsonl": lambda **kw: _write(kw["in"], target, mode="all"),
        "write.lossy": lambda **kw: _write(kw["in"], target, mode="lossy"),
        "write.scrambled": lambda **kw: _write(kw["in"], target, mode="scrambled"),
        "reconcile.count": lambda **kw: _reconcile(kw["before"], kw["after"],
                                                   strict=False),
        "reconcile.checksum": lambda **kw: _reconcile(kw["before"], kw["after"],
                                                      strict=True),
    })


def example() -> dict[str, Any]:
    """Arguments for `runtime()`: a small store, in both formats, and a target.

    Nine records, because nine divides by three — so `write.lossy` drops
    exactly three and the arithmetic in the failure is easy to check by hand.
    """
    folder = pathlib.Path(tempfile.mkdtemp(prefix="browsergraph-migrate-"))
    rows = [{"id": index, "name": f"row-{index}", "amount": index * 10}
            for index in range(1, 10)]

    source = folder / "source.jsonl"
    source.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    csv_source = folder / "source.csv"
    csv_source.write_text(
        "id,name,amount\n" + "\n".join(
            f"{r['id']},{r['name']},{r['amount']}" for r in rows),
        encoding="utf-8")

    return {"source": str(source), "csv_source": str(csv_source),
            "destination": str(folder / "target.jsonl")}
