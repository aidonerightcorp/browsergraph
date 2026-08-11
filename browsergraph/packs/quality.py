"""A data quality gate: profile it, check it two independent ways, decide.

The shape that matters here is the **join**. Schema and distribution are two
different readings of the same profile — they can run at once, they fail
separately, and they meet at one decision that sees both. A checker that sees
only one of them is confident for the wrong reason.

    from browsergraph import packs
    print(packs.get("quality").solve().text())

The point of a gate is that it is allowed to say no. A quality check that cannot
stop the run is a log line: if bad data flows on regardless, the check did not
check anything, it described. So `adjudicate` returns a verdict with `allow`
either true or false, and the pack's example data is bad enough that a strict
adjudicator refuses it — which is what makes the demonstration worth running.

Three adjudicators, and they disagree on purpose. On the example data, checking
`schema.required` and `distribution.nulls`:

    adjudicate.any          allow=False    (one warning is enough)
    adjudicate.severity     allow=True     (warnings are not errors)
    adjudicate.schema_only  allow=True     (distribution reported, not fatal)

Which is right depends on what happens downstream, and that is a decision — so
it is three candidates rather than a threshold argument buried in one.

The two outlier checks are worth comparing for the same reason. `outliers` uses
mean and standard deviation, and **misses an amount of 900 sitting among tens**,
because the extreme value inflates the spread it is measured against. `robust`
uses the median and the median absolute deviation, which one value cannot move,
and flags it. Both ship, neither is deleted, and the comparison is the lesson.
"""
from __future__ import annotations

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

TEMPLATE = "data.quality"
SUMMARY = "profile a table, check schema and distribution at once, then decide"

FILLING: dict[str, list[str]] = {
    "profile":      ["profile.basic", "profile.detailed"],
    "schema":       ["schema.required", "schema.typed"],
    "distribution": ["distribution.nulls", "distribution.outliers",
                     "distribution.robust"],
    "adjudicate":   ["adjudicate.any", "adjudicate.severity",
                     "adjudicate.schema_only"],
}

#: What every row is expected to have, and as what. Data, not code, so a caller
#: can pass their own to `runtime()` without touching a node.
EXPECTED: dict[str, str] = {"id": "int", "name": "str", "amount": "float"}


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
    _node("profile.basic", "data.profile", [], [("out", "Profile")],
          description="Row count, columns, and how many nulls in each."),
    _node("profile.detailed", "data.profile", [], [("out", "Profile")],
          description="As basic, plus per-column types and numeric spread."),

    _node("schema.required", "check.schema", [("in", "Profile")],
          [("out", "Findings")],
          description="Every expected column is present."),
    _node("schema.typed", "check.schema", [("in", "Profile")],
          [("out", "Findings")],
          description="Present, and holding the type it should."),

    _node("distribution.nulls", "check.distribution", [("in", "Profile")],
          [("out", "Findings")],
          description="Any column more than a tenth empty."),
    _node("distribution.outliers", "check.distribution", [("in", "Profile")],
          [("out", "Findings")],
          description="Numeric values more than three deviations out. Misses "
                      "the extreme ones — see the docstring on masking."),
    _node("distribution.robust", "check.distribution", [("in", "Profile")],
          [("out", "Findings")],
          description="Outliers by median absolute deviation, which one "
                      "extreme value cannot move."),

    _node("adjudicate.any", "gate.decide",
          [("schema", "Findings"), ("distribution", "Findings")],
          [("out", "Verdict")],
          description="Refuse if either check found anything at all."),
    _node("adjudicate.severity", "gate.decide",
          [("schema", "Findings"), ("distribution", "Findings")],
          [("out", "Verdict")],
          description="Refuse on any error; warnings are recorded, not fatal."),
    _node("adjudicate.schema_only", "gate.decide",
          [("schema", "Findings"), ("distribution", "Findings")],
          [("out", "Verdict")],
          description="Refuse only on schema problems. Reads distribution and "
                      "reports it, which is not the same as ignoring it."),
)


def workbench() -> WorkbenchDefinition:
    template = _template(TEMPLATE)
    bench = template.instantiate(FILLING)
    return WorkbenchDefinition(
        title=template.title, task=template.task,
        stages=bench.stages, edges=bench.edges, nodes=NODES,
        candidates=tuple(NodeCandidate(id=n.id, node_id=n.id) for n in NODES),
        optimization_profiles=(OptimizationProfile(
            id="p.quality", name="Catch the most, wrongly refuse the least",
            objectives=(OptimizationObjective("quality", "maximize", 1.0),)),),
        metadata=dict(bench.metadata, pack="quality"),
    ).assert_valid()


# --- the implementations ----------------------------------------------------

def _numbers(rows, column: str) -> list[float]:
    out = []
    for row in rows:
        value = row.get(column)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int | float):
            out.append(float(value))
    return out


def _profile(rows, *, detailed: bool) -> dict[str, Any]:
    columns = sorted({key for row in rows for key in row})
    nulls = {c: sum(1 for r in rows if r.get(c) is None) for c in columns}
    profile: dict[str, Any] = {"rows": len(rows), "columns": columns,
                               "nulls": nulls}
    if not detailed:
        return profile

    types, spread = {}, {}
    for column in columns:
        seen = {type(r[column]).__name__ for r in rows if r.get(column) is not None}
        types[column] = sorted(seen)
        numbers = _numbers(rows, column)
        if len(numbers) > 1:
            spread[column] = {"mean": statistics.fmean(numbers),
                              "stdev": statistics.pstdev(numbers),
                              "values": numbers}
    return {**profile, "types": types, "spread": spread}


def _finding(what: str, where: str, severity: str) -> dict[str, str]:
    return {"what": what, "where": where, "severity": severity}


def _schema_required(profile, expected) -> dict[str, Any]:
    missing = [c for c in expected if c not in profile["columns"]]
    return {"check": "schema.required",
            "findings": [_finding("column missing", c, "error") for c in missing]}


def _schema_typed(profile, expected) -> dict[str, Any]:
    findings = [_finding("column missing", c, "error")
                for c in expected if c not in profile["columns"]]
    # A detailed profile carries types; a basic one does not, and saying so is
    # better than silently reporting no problems. A check that cannot run and
    # returns "all clear" is the most dangerous kind.
    if "types" not in profile:
        findings.append(_finding("no type information in this profile",
                                 "profile", "warning"))
        return {"check": "schema.typed", "findings": findings}

    wanted = {"int": {"int"}, "float": {"int", "float"}, "str": {"str"}}
    for column, kind in expected.items():
        seen = set(profile["types"].get(column, []))
        if seen and not seen <= wanted.get(kind, {kind}):
            findings.append(_finding(f"expected {kind}, saw {sorted(seen)}",
                                     column, "error"))
    return {"check": "schema.typed", "findings": findings}


def _distribution_nulls(profile) -> dict[str, Any]:
    rows = profile["rows"] or 1
    findings = [_finding(f"{count} of {rows} empty", column,
                         "error" if count / rows > 0.5 else "warning")
                for column, count in profile["nulls"].items()
                if count / rows > 0.1]
    return {"check": "distribution.nulls", "findings": findings}


def _distribution_outliers(profile) -> dict[str, Any]:
    """Values more than three deviations from the mean.

    **This misses the outliers it most ought to catch, and that is a property of
    the method, not a bug here.** Mean and standard deviation are both computed
    from the data the outlier is in, so one extreme value inflates the spread
    enough to hide itself — on the pack's own example, an amount of 900 among
    tens sits 741 from a mean of 158 with three deviations at 995, and is
    reported as fine. Statisticians call it masking.

    Kept as written, with the limitation stated, because that is what a
    three-sigma rule actually does. If you need it caught, the fix is a
    different candidate — a median-absolute-deviation check, which does not
    move when one value is extreme — and swapping one candidate for another is
    the operation this whole library is arranged around. Silently "fixing" the
    threshold here would hide the same failure one value further out.
    """
    if "spread" not in profile:
        return {"check": "distribution.outliers",
                "findings": [_finding("no numeric spread in this profile",
                                      "profile", "warning")]}
    findings = []
    for column, stats in profile["spread"].items():
        if not stats["stdev"]:
            continue
        far = [v for v in stats["values"]
               if abs(v - stats["mean"]) > 3 * stats["stdev"]]
        if far:
            findings.append(_finding(f"{len(far)} value(s) far out: {far}",
                                     column, "warning"))
    return {"check": "distribution.outliers", "findings": findings}


def _distribution_robust(profile) -> dict[str, Any]:
    """Outliers by median absolute deviation. What the three-sigma rule misses.

    The median and the MAD are both unmoved by a single extreme value, so the
    thing that hides itself from a mean-and-deviation check does not hide from
    this one. Same example, same column: 900 is flagged here and not there.

    The 0.6745 converts MAD to a comparable scale for normal-ish data, so the
    threshold of 3.5 reads like the familiar three-sigma one.
    """
    if "spread" not in profile:
        return {"check": "distribution.robust",
                "findings": [_finding("no numeric spread in this profile",
                                      "profile", "warning")]}
    findings = []
    for column, stats in profile["spread"].items():
        values = stats["values"]
        middle = statistics.median(values)
        deviation = statistics.median([abs(v - middle) for v in values])
        if not deviation:
            continue
        far = [v for v in values
               if abs(0.6745 * (v - middle) / deviation) > 3.5]
        if far:
            findings.append(_finding(f"{len(far)} value(s) far from the median: "
                                     f"{far}", column, "warning"))
    return {"check": "distribution.robust", "findings": findings}


def _verdict(schema, distribution, *, rule: str) -> dict[str, Any]:
    every = list(schema["findings"]) + list(distribution["findings"])
    errors = [f for f in every if f["severity"] == "error"]

    if rule == "any":
        allow = not every
    elif rule == "severity":
        allow = not errors
    else:                                   # schema_only
        allow = not [f for f in schema["findings"] if f["severity"] == "error"]

    return {"allow": allow, "rule": rule, "findings": every,
            "errors": len(errors), "warnings": len(every) - len(errors),
            # Both checks are reported whichever rule decided, so "we ignored
            # distribution" stays visible instead of being lost in a boolean.
            "saw": [schema["check"], distribution["check"]]}


def runtime(rows: list[dict[str, Any]],
            expected: dict[str, str] | None = None) -> Runtime:
    """One function per candidate, checking `rows` against `expected`.

    The rows are configuration for the same reason the folder is in the files
    pack: `profile` is a source step with no input ports. Where the first step
    gets its material is not something the graph carries.
    """
    wanted = expected or EXPECTED
    return Runtime({
        "profile.basic": lambda **kw: _profile(rows, detailed=False),
        "profile.detailed": lambda **kw: _profile(rows, detailed=True),
        "schema.required": lambda **kw: _schema_required(kw["in"], wanted),
        "schema.typed": lambda **kw: _schema_typed(kw["in"], wanted),
        "distribution.nulls": lambda **kw: _distribution_nulls(kw["in"]),
        "distribution.outliers": lambda **kw: _distribution_outliers(kw["in"]),
        "distribution.robust": lambda **kw: _distribution_robust(kw["in"]),
        "adjudicate.any": lambda **kw: _verdict(kw["schema"], kw["distribution"],
                                                rule="any"),
        "adjudicate.severity": lambda **kw: _verdict(kw["schema"],
                                                     kw["distribution"],
                                                     rule="severity"),
        "adjudicate.schema_only": lambda **kw: _verdict(kw["schema"],
                                                        kw["distribution"],
                                                        rule="schema_only"),
    })


def example() -> dict[str, Any]:
    """Rows with one of each problem, so every check has something to find.

    A gate demonstrated on clean data proves only that it does not fire. There
    is a missing column, a wrong type, a column a third empty, and one value
    far out — and the three adjudicators reach different verdicts on it, which
    is the thing worth seeing.
    """
    rows: list[dict[str, Any]] = [
        {"id": 1, "name": "alpha", "amount": 10.0},
        {"id": 2, "name": "beta", "amount": 11.5},
        {"id": 3, "name": None, "amount": 9.0},
        {"id": 4, "name": "delta", "amount": 10.5},
        {"id": 5, "name": None, "amount": 900.0},      # far out
        {"id": "six", "name": "eps", "amount": 10.2},  # wrong type
    ]
    return {"rows": rows}
