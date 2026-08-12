"""Repair messy data, and leave a record of every change you made.

Cleaning and validation are the pair the taxonomy exists to keep apart. They
look alike — both read a table and both complain about it — and a correct
implementation of one is a **silently broken implementation of the other**:

* a validator that repairs has hidden the problem it was hired to find
* a cleaner that only refuses to write has done nothing

So the shape of this graph is not "read, fix, save". It is **detect before
repairing, repair with a change log, account for found against fixed, and
re-read the result.** The two middle steps are where the money is.

    from browsergraph import packs
    print(packs.get("clean").solve().text())

Five steps, seventy-two routes, standard library only.

## What the routes disagree about, and what running them shows

Every number below is measured on the twelve-row table in this file, and
`tests/test_packs.py` recomputes each of them.

**`account.fixed_only` vs `account.found_vs_fixed`.** The first reports what
was changed. The second reports it against what was *found*, and the difference
is what you still have. From one `repair.impute` run: the fixed-only ledger
says **5 fixed**; the found-against-fixed ledger says **5 fixed of 17 found,
12 still broken.** Neither run raises. Only the second is a report anybody can
act on.

**`verify.count` vs `verify.recheck`.** Counting the change log and re-reading
the repaired frame are not the same check, and the first is the one everybody
writes. After `repair.impute`, `verify.count` returns **ok** and
`verify.recheck` finds **12 problems remaining** — because median imputation
cannot fill `region`, which is empty in all twelve rows, so it skips those
cells and the count never notices the skip.

**`repair.drop` empties the table, and every check passes.** It deletes the 12
rows holding the 17 problems, `verify.recheck` re-reads the result, finds
**0 remaining**, and reports ok. An empty table is perfectly clean. The
`rows_before`/`rows_after` line in the ledger is the only thing in the whole
graph that says what happened, which is why it is in the ledger rather than
left for the reader to infer.

**`detect.robust` finds one thing `detect.rules` does not** — the 900 among
tens, by median absolute deviation. Seventeen issues against eighteen. Three
sigma would miss it, because the 900 is most of what inflates the sigma.

**`detect.schema_only` is close to a null control.** Types only: **5 issues**
where the others find 17, and it is silent about the column that is missing in
every single row.
"""
from __future__ import annotations

import csv
import io
import statistics
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

TEMPLATE = "data.clean"
SUMMARY = "repair a messy table, and account for what the repair did not fix"

FILLING: dict[str, list[str]] = {
    "load":   ["read.csv", "read.typed"],
    "detect": ["detect.rules", "detect.robust", "detect.schema_only"],
    "repair": ["repair.impute", "repair.flag", "repair.drop"],
    "ledger": ["account.found_vs_fixed", "account.fixed_only"],
    "verify": ["verify.recheck", "verify.count"],
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
    _node("read.csv", "data.read", [], [("out", "Frame")],
          description="Every cell as text. Nothing is coerced, so nothing is "
                      "silently lost on the way in."),
    _node("read.typed", "data.read", [], [("out", "Frame")],
          description="Coerce numbers where they parse. A cell that will not "
                      "parse becomes None and is a detectable problem rather "
                      "than an exception during the load."),

    _node("detect.rules", "data.detect", [("in", "Frame")], [("out", "Issues")],
          description="Missing values, wrong types, and values outside a "
                      "declared range."),
    _node("detect.robust", "data.detect", [("in", "Frame")], [("out", "Issues")],
          description="The rules, plus outliers by median absolute deviation — "
                      "which finds the 900 hiding among tens that three sigma "
                      "masks, because the 900 inflates the sigma."),
    _node("detect.schema_only", "data.detect", [("in", "Frame")],
          [("out", "Issues")],
          description="Types only. Close to a null control: it will pass a "
                      "table of impossible numbers as long as they are numbers."),

    _node("repair.impute", "data.repair",
          [("frame", "Frame"), ("issues", "Issues")],
          [("out", "Frame"), ("changes", "ChangeLog")],
          description="Fill gaps with the column median. Cannot fill a column "
                      "that is entirely missing, and says so rather than "
                      "inventing a zero."),
    _node("repair.flag", "data.repair",
          [("frame", "Frame"), ("issues", "Issues")],
          [("out", "Frame"), ("changes", "ChangeLog")],
          description="Change nothing; mark every affected row. The honest "
                      "option when a wrong value is worse than a missing one."),
    _node("repair.drop", "data.repair",
          [("frame", "Frame"), ("issues", "Issues")],
          [("out", "Frame"), ("changes", "ChangeLog")],
          description="Delete the affected rows. Scores well on any metric "
                      "that counts remaining problems, for the wrong reason."),

    _node("account.found_vs_fixed", "data.account",
          [("changes", "ChangeLog"), ("issues", "Issues")],
          [("out", "ChangeLog")],
          description="Found against fixed. The difference is what you still "
                      "have, and it is the only number worth circulating."),
    _node("account.fixed_only", "data.account",
          [("changes", "ChangeLog"), ("issues", "Issues")],
          [("out", "ChangeLog")],
          description="What changed, with no denominator. Reads as a clean "
                      "run whatever was left behind."),

    _node("verify.recheck", "data.verify",
          [("frame", "Frame"), ("ledger", "ChangeLog")], [("out", "Verdict")],
          description="Re-read the repaired frame and detect again. The only "
                      "check that can see a repair that did not take."),
    _node("verify.count", "data.verify",
          [("frame", "Frame"), ("ledger", "ChangeLog")], [("out", "Verdict")],
          description="Trust the ledger's arithmetic. Fast, and blind to "
                      "anything the repair step failed to notice it skipped."),
)


def workbench() -> WorkbenchDefinition:
    template = _template(TEMPLATE)
    bench = template.instantiate(FILLING)
    return WorkbenchDefinition(
        title=template.title, task=template.task,
        stages=bench.stages, edges=bench.edges, nodes=NODES,
        candidates=tuple(NodeCandidate(id=n.id, node_id=n.id) for n in NODES),
        optimization_profiles=(OptimizationProfile(
            id="p.trustworthy", name="Leave the least unexplained",
            objectives=(OptimizationObjective("quality", "maximize", 1.0),)),),
        metadata=dict(bench.metadata, pack="clean"),
    ).assert_valid()


# --- the data ---------------------------------------------------------------

#: Twelve rows with four kinds of problem, and one column (`region`) that is
#: missing for every row — the case that makes median imputation a no-op while
#: still reporting a change.
TABLE = """id,price,quantity,region
1,10.0,2,
2,12.5,3,
3,,4,
4,11.0,,
5,900.0,2,
6,9.5,3,
7,,2,
8,10.5,not-a-number,
9,11.5,3,
10,,4,
11,10.0,2,
12,12.0,3,
"""

#: What a price is allowed to be. Declared, because a range check with no
#: declared range is somebody's memory of what looked normal last quarter.
#:
#: The price ceiling is deliberately loose. A tight one would catch the 900 as
#: a range violation and `detect.robust` would then find nothing `detect.rules`
#: does not — two candidates that always agree are one candidate and a longer
#: run time.
LIMITS = {"price": (0.0, 100000.0), "quantity": (0.0, 1000.0)}
NUMERIC = ("price", "quantity")


def _rows(text: str, *, typed: bool) -> list[dict[str, Any]]:
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        record = dict(row)
        if typed:
            for column in NUMERIC:
                value = (record.get(column) or "").strip()
                try:
                    record[column] = float(value) if value else None
                except ValueError:
                    # Not an exception: an unparseable number is a problem for
                    # `detect` to find, and raising here would lose the other
                    # eleven rows to one bad cell.
                    record[column] = None
        out.append(record)
    return out


def _issue(row: int, column: str, kind: str, detail: str) -> dict[str, Any]:
    return {"row": row, "column": column, "kind": kind, "detail": detail}


def _detect(rows, *, rules: bool, robust: bool) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        for column in NUMERIC:
            value = row.get(column)
            if value in (None, ""):
                issues.append(_issue(i, column, "missing", "no value"))
                continue
            if not rules:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                issues.append(_issue(i, column, "type", f"{value!r} is not a number"))
                continue
            low, high = LIMITS[column]
            if not low <= number <= high:
                issues.append(_issue(i, column, "range",
                                     f"{number} outside {low}-{high}"))
        if rules and not (row.get("region") or "").strip():
            issues.append(_issue(i, "region", "missing", "no value"))

    if robust:
        for column in NUMERIC:
            values = [float(r[column]) for r in rows
                      if r.get(column) not in (None, "")
                      and str(r[column]).replace(".", "", 1).isdigit()]
            if len(values) < 3:
                continue
            middle = statistics.median(values)
            spread = statistics.median([abs(v - middle) for v in values])
            if spread == 0:
                continue
            for i, row in enumerate(rows):
                raw = row.get(column)
                if raw in (None, ""):
                    continue
                try:
                    number = float(raw)
                except (TypeError, ValueError):
                    continue
                # 3.5 is the usual cut for a modified z-score. Named rather
                # than inlined so the number can be argued with.
                if abs(number - middle) / (1.4826 * spread) > 3.5:
                    already = any(x["row"] == i and x["column"] == column
                                  for x in issues)
                    if not already:
                        issues.append(_issue(i, column, "outlier",
                                             f"{number} far from median {middle}"))
    return {"issues": issues, "rows": len(rows)}


def _repair(rows, found, *, how: str) -> dict[str, Any]:
    issues = found["issues"]
    frame = [dict(r) for r in rows]
    changes: list[dict[str, Any]] = []

    if how == "flag":
        for issue in issues:
            frame[issue["row"]].setdefault("_flags", []).append(
                f"{issue['column']}:{issue['kind']}")
            changes.append({**issue, "action": "flagged"})
        return {"out": frame, "changes": {"changes": changes,
                                          "rows_before": len(rows),
                                          "rows_after": len(frame)}}

    if how == "drop":
        bad = {issue["row"] for issue in issues}
        frame = [r for i, r in enumerate(frame) if i not in bad]
        for issue in issues:
            changes.append({**issue, "action": "row deleted"})
        return {"out": frame, "changes": {"changes": changes,
                                          "rows_before": len(rows),
                                          "rows_after": len(frame)}}

    # impute
    medians = {}
    for column in NUMERIC:
        values = [float(r[column]) for r in rows
                  if r.get(column) not in (None, "")
                  and str(r[column]).replace(".", "", 1).isdigit()]
        if values:
            medians[column] = statistics.median(values)
    for issue in issues:
        column = issue["column"]
        if issue["kind"] not in ("missing", "type"):
            continue
        if column not in medians:
            # The whole column is empty. Filling it with a made-up number
            # would be the worst outcome here, so the repair declines — and
            # the ledger is what makes the decline visible.
            continue
        frame[issue["row"]][column] = medians[column]
        changes.append({**issue, "action": f"set to median {medians[column]}"})
    return {"out": frame, "changes": {"changes": changes,
                                      "rows_before": len(rows),
                                      "rows_after": len(frame)}}


def _account(changes, found, *, denominator: bool) -> dict[str, Any]:
    fixed = len(changes["changes"])
    ledger = {"fixed": fixed,
              "rows_before": changes["rows_before"],
              "rows_after": changes["rows_after"],
              "rows_deleted": changes["rows_before"] - changes["rows_after"],
              "entries": changes["changes"]}
    if denominator:
        ledger["found"] = len(found["issues"])
        ledger["still_broken"] = len(found["issues"]) - fixed
    return ledger


def _verify(frame, ledger, *, recheck: bool) -> dict[str, Any]:
    if not recheck:
        # Believe the arithmetic. Cannot see a repair that never happened.
        remaining = ledger.get("still_broken")
        return {"ok": remaining in (0, None), "method": "count",
                "remaining": remaining,
                "note": "counted from the ledger; the repaired frame was not "
                        "re-read"}
    after = _detect(frame, rules=True, robust=False)
    return {"ok": not after["issues"], "method": "recheck",
            "remaining": len(after["issues"]),
            "note": "re-read the repaired frame"}


def runtime(**_: Any) -> Runtime:
    return Runtime({
        "read.csv": lambda **kw: _rows(TABLE, typed=False),
        "read.typed": lambda **kw: _rows(TABLE, typed=True),
        "detect.rules": lambda **kw: _detect(kw["in"], rules=True, robust=False),
        "detect.robust": lambda **kw: _detect(kw["in"], rules=True, robust=True),
        "detect.schema_only": lambda **kw: _detect(kw["in"], rules=False,
                                                   robust=False),
        "repair.impute": lambda **kw: _repair(kw["frame"], kw["issues"],
                                              how="impute"),
        "repair.flag": lambda **kw: _repair(kw["frame"], kw["issues"],
                                            how="flag"),
        "repair.drop": lambda **kw: _repair(kw["frame"], kw["issues"],
                                            how="drop"),
        "account.found_vs_fixed": lambda **kw: _account(kw["changes"],
                                                        kw["issues"],
                                                        denominator=True),
        "account.fixed_only": lambda **kw: _account(kw["changes"], kw["issues"],
                                                    denominator=False),
        "verify.recheck": lambda **kw: _verify(kw["frame"], kw["ledger"],
                                               recheck=True),
        "verify.count": lambda **kw: _verify(kw["frame"], kw["ledger"],
                                             recheck=False),
    })


def example() -> dict[str, Any]:
    """No arguments: the table is in this file so the pack runs anywhere."""
    return {}


def leaves_nothing_unexplained(run) -> tuple[bool, float]:
    """A verifier for `solve`: did the run say what it did *not* fix?

    Deliberately not "did it fix everything". A route that flags every problem
    and fixes none is a correct answer to "make this safe to use"; a route that
    fixes twelve problems and cannot tell you three remain is not.
    """
    verdict = run.output("verify")
    ledger = run.output("ledger")
    if not isinstance(verdict, dict) or not isinstance(ledger, dict):
        return False, 0.0
    accounted = "still_broken" in ledger or verdict.get("method") == "recheck"
    if not accounted:
        return False, 0.0
    remaining = verdict.get("remaining")
    kept = ledger.get("rows_after", 0)
    # Rows kept matters: deleting the problem rows removes the problems.
    return True, round(kept / 12 * (1.0 if remaining in (0, None) else 0.8), 3)
