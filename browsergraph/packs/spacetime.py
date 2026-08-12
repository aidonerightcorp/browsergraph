"""Was anything happening in that city that day? — and four ways to answer it wrongly.

This is the enrichment everybody wants and nobody audits. Take a row with a
place and a time, go and find out what else was true there and then — weather,
a holiday, an event, a boundary — and attach it. Each half is a join somebody
knows how to write. The pair is where the errors live, because **every one of
them is defensible on its own axis**:

    the spatial half is right, and used a boundary drawn after the row happened
    the temporal half is right, and used a value revised after the row happened
    both halves are right, and the observation came from a station 78km away

    from browsergraph import packs
    print(packs.get("spacetime").solve().text())

Six rows: sales at three stores, over a year. Measured, by a test that runs
every route. `signal` is how many rows came out with something attached — the
number a coverage metric rewards — and everything to its right is what that
number cost:

    period              context          signal  leaks  which rows
    period.local_day    context.asof        6      0     —
    period.local_day    context.final       6      2     s2, s4
    period.utc_day      context.asof        6      0     —
    period.utc_day      context.final       6      3     s2, s4, s6
    period.processing   context.asof        5      0     —
    period.processing   context.final       5      2     s4, s6

    place.nearest             6 enriched,  0 rows from a distant station
    place.same_city           6 enriched,  5 rows from 41-78km away
    place.centroid            3 enriched,  no station, so nothing to check

    vintage.checked           0 rows in a region they were not in
    vintage.current           1 row  (s1, before the store moved region)

    attach.with_provenance    the audit finds what is there
    attach.value_only         6 rows the audit can no longer see into at all

**Every column but the first is flat or better on the leaking routes.** That is
the whole difficulty: `context.final` attaches the same six rows and attaches
*better* data — the revised rainfall figure is the correct one — and it is a
leak, because on the day the sale happened the revision had not been published.
A model trained on it learns from a number that will not exist at prediction
time; the backtest is excellent and production is not.

The `period` candidates are the timezone story, and row six is the whole of it:
a sale at 23:30 on the thirty-first of December in Denver is, in UTC, a sale on
the first of January of the following year. Neither reading is wrong. Choosing
without noticing is — and note that `period.utc_day` *adds* a leak, because
reading tomorrow's UTC day means reading a figure published tomorrow.

`place.same_city` is the join everybody writes: take a station in the same city.
It matches as often as the nearest-station version, returns a plausible number
on every row, and five of those numbers were measured 41 to 78km away. On the
fourth of July it rained 12.4mm at the Denver store and nothing at all at the
station 78km out. Both observations are correct; only one is about the store.

`vintage` is the spatial mirror of the temporal leak. The Denver store moved
sales region in June; `vintage.current` assigns every row the region the store
is in *now*, quietly rewriting the first half of the year into a shape it never
had. This is what breaks a year-on-year comparison and is never suspected,
because the join is correct.

The last pair is the quietest and the worst. `attach.value_only` keeps the
number and drops the station, the distance, the as-of and the vintage — so the
audit, which is arithmetic on exactly those fields, finds nothing to check on
any row. **A pipeline that strips its provenance does not become clean. It
becomes unauditable**, and the two look identical on a dashboard.

Standard library, deterministic. The reference tables are small and real in
kind: two of the holidays are state observances rather than federal ones, which
is what makes the holiday lookup a genuine place-*and*-time join rather than a
date lookup wearing one.
"""
from __future__ import annotations

import datetime as dt
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

TEMPLATE = "enrich.spacetime"
SUMMARY = "attach what was happening there then, using only what was knowable then"

FILLING: dict[str, list[str]] = {
    "place":   ["place.nearest", "place.same_city", "place.centroid"],
    "period":  ["period.local_day", "period.utc_day", "period.processing"],
    "context": ["context.asof", "context.final"],
    "vintage": ["vintage.checked", "vintage.current"],
    "attach":  ["attach.with_provenance", "attach.value_only"],
    "audit":   ["audit.leak", "audit.count"],
    "load":    ["load.sales", "load.sales_late"],
}

#: Weather stations, with how far each is from the store it might be used for.
#: The distance is the field that makes "it rained there" a checkable claim.
#: In file order, which is the order a reference table arrives in and is not
#: sorted by distance from anything. `place.same_city` takes the first match it
#: finds, which is how most joins are written.
STATIONS: tuple[dict[str, Any], ...] = (
    {"id": "DEN-2", "city": "Denver", "km": 78.0},
    {"id": "DEN-1", "city": "Denver", "km": 2.4},
    {"id": "AUS-1", "city": "Austin", "km": 5.1},
    {"id": "BOS-2", "city": "Boston", "km": 41.7},
    {"id": "BOS-1", "city": "Boston", "km": 3.3},
)

#: Observations, and the two values every revised series has. `revised_at` is
#: the date the corrected figure was published — the field that decides whether
#: using it is enrichment or a leak.
OBSERVATIONS: tuple[dict[str, Any], ...] = (
    {"station": "DEN-1", "date": "2024-05-20", "first": 0.0, "final": 0.0,
     "revised_at": "2024-05-20"},
    {"station": "DEN-2", "date": "2024-05-20", "first": 11.2, "final": 11.2,
     "revised_at": "2024-05-20"},
    # Published as dry on the day, corrected to 12.4mm two days later.
    {"station": "DEN-1", "date": "2024-07-04", "first": 0.0, "final": 12.4,
     "revised_at": "2024-07-06"},
    {"station": "AUS-1", "date": "2024-03-02", "first": 3.1, "final": 3.1,
     "revised_at": "2024-03-02"},
    {"station": "BOS-1", "date": "2024-04-15", "first": 0.0, "final": 6.8,
     "revised_at": "2024-04-18"},
    {"station": "BOS-1", "date": "2024-11-05", "first": 1.0, "final": 1.0,
     "revised_at": "2024-11-05"},
    {"station": "DEN-1", "date": "2024-12-31", "first": 4.0, "final": 4.0,
     "revised_at": "2024-12-31"},
    {"station": "DEN-1", "date": "2025-01-01", "first": 0.5, "final": 0.5,
     "revised_at": "2025-01-01"},
    # The far stations have data too, and it disagrees. On the fourth of July
    # it rained 12.4mm at the store and nothing at all 78km away; both numbers
    # are correct observations and only one of them is about the store.
    {"station": "DEN-2", "date": "2024-07-04", "first": 0.0, "final": 0.0,
     "revised_at": "2024-07-04"},
    {"station": "DEN-2", "date": "2024-12-31", "first": 0.0, "final": 0.0,
     "revised_at": "2024-12-31"},
    {"station": "BOS-2", "date": "2024-04-15", "first": 0.0, "final": 0.0,
     "revised_at": "2024-04-15"},
    {"station": "BOS-2", "date": "2024-11-05", "first": 0.0, "final": 0.0,
     "revised_at": "2024-11-05"},
)

#: Two of these are state observances and one is federal. That is the point:
#: a holiday lookup keyed on date alone is wrong for two of the three, and the
#: error only appears for the states that have them.
HOLIDAYS: dict[tuple[str, str], str] = {
    ("CO", "2024-07-04"): "Independence Day (federal)",
    ("TX", "2024-07-04"): "Independence Day (federal)",
    ("MA", "2024-07-04"): "Independence Day (federal)",
    ("TX", "2024-03-02"): "Texas Independence Day (state)",
    ("MA", "2024-04-15"): "Patriots' Day (state)",
}

#: The Denver store changed sales region in June. Rows before that date belong
#: to the region it was in then, and there is no way to recover that from the
#: current assignment — which is why the vintage has to be part of the join.
REGIONS: tuple[dict[str, str], ...] = (
    {"city": "Denver", "region": "Mountain", "from": "2000-01-01",
     "to": "2024-06-01"},
    {"city": "Denver", "region": "West", "from": "2024-06-01", "to": "2999-01-01"},
    {"city": "Austin", "region": "South", "from": "2000-01-01",
     "to": "2999-01-01"},
    {"city": "Boston", "region": "Northeast", "from": "2000-01-01",
     "to": "2999-01-01"},
)

STATE_OF = {"Denver": "CO", "Austin": "TX", "Boston": "MA"}

#: Farther than this and "it rained at the store" is a different claim from the
#: one the observation supports.
FAR_KM = 25.0


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
    _node("load.sales", "data.read", [], [("out", "Records")],
          description="Six sales, each with a store, a local timestamp and a "
                      "UTC offset."),
    _node("load.sales_late", "data.read", [], [("out", "Records")],
          description="The same six, two of which arrived a day after they "
                      "happened — which is what makes processing time and "
                      "event time different numbers rather than a lecture."),

    _node("place.nearest", "geo.resolve", [("in", "Records")],
          [("out", "Places")],
          description="The closest station, carrying its distance."),
    _node("place.same_city", "geo.resolve", [("in", "Records")],
          [("out", "Places")],
          description="Any station in the same city, first one found. Matches "
                      "as often and can be 78km away."),
    _node("place.centroid", "geo.resolve", [("in", "Records")],
          [("out", "Places")],
          description="The city centroid, with no station and no distance. "
                      "Always matches, and cannot say how good the match is."),

    _node("period.local_day", "time.resolve", [("in", "Records")],
          [("out", "Periods")],
          description="The calendar day where the thing happened."),
    _node("period.utc_day", "time.resolve", [("in", "Records")],
          [("out", "Periods")],
          description="The calendar day in UTC. Differs from the local day "
                      "for anything late at night."),
    _node("period.processing", "time.resolve", [("in", "Records")],
          [("out", "Periods")],
          description="The day the row was written. What a naive pipeline "
                      "uses, and it is the day the *record* exists, not the "
                      "day the sale happened."),

    _node("context.asof", "context.lookup",
          [("places", "Places"), ("periods", "Periods")], [("out", "Context")],
          description="Only observations published by the end of the row's "
                      "day. Attaches the figure that was actually available."),
    _node("context.final", "context.lookup",
          [("places", "Places"), ("periods", "Periods")], [("out", "Context")],
          description="The revised, correct figure. Better data, and it did "
                      "not exist yet."),

    _node("vintage.checked", "context.vintage", [("in", "Context")],
          [("out", "Context")],
          description="The region assignment in force on the row's date."),
    _node("vintage.current", "context.vintage", [("in", "Context")],
          [("out", "Context")],
          description="Today's assignment, applied to every row. The spatial "
                      "version of the same leak."),

    _node("attach.with_provenance", "data.enrich",
          [("records", "Records"), ("context", "Context")],
          [("out", "Records")],
          description="Value, station, distance, vintage and the as-of it was "
                      "read under, all on the row."),
    _node("attach.value_only", "data.enrich",
          [("records", "Records"), ("context", "Context")],
          [("out", "Records")],
          description="Just the number. Downstream cannot tell an observation "
                      "from 2km away from one from 78km away, which is the "
                      "usual state of an enriched table."),

    _node("audit.leak", "context.audit", [("in", "Records")],
          [("out", "Findings")],
          description="For every attached value, was it knowable on the row's "
                      "date? Arithmetic on two timestamps, and it is the "
                      "check nobody writes."),
    _node("audit.count", "context.audit", [("in", "Records")],
          [("out", "Findings")],
          description="How many rows got enriched. The metric that rewards "
                      "the leak."),
)


def workbench() -> WorkbenchDefinition:
    template = _template(TEMPLATE)
    bench = template.instantiate(FILLING)
    return WorkbenchDefinition(
        title=template.title, task=template.task,
        stages=bench.stages, edges=bench.edges, nodes=NODES,
        candidates=tuple(NodeCandidate(id=n.id, node_id=n.id) for n in NODES),
        optimization_profiles=(OptimizationProfile(
            id="p.spacetime",
            name="Attach the most context that was knowable at the time",
            objectives=(OptimizationObjective("quality", "maximize", 1.0),)),),
        metadata=dict(bench.metadata, pack="spacetime"),
    ).assert_valid()


# --- the rows ---------------------------------------------------------------

#: `when` is local wall-clock; `offset` is hours from UTC; `written` is when the
#: row reached the warehouse. Row six is the interesting one and it is not a
#: contrived edge case — every retailer has thousands of them.
SALES: tuple[dict[str, Any], ...] = (
    {"id": "s1", "city": "Denver", "when": "2024-05-20T14:05", "offset": -6,
     "written": "2024-05-20", "amount": 118.0},
    {"id": "s2", "city": "Denver", "when": "2024-07-04T11:30", "offset": -6,
     "written": "2024-07-04", "amount": 402.5},
    {"id": "s3", "city": "Austin", "when": "2024-03-02T16:45", "offset": -6,
     "written": "2024-03-02", "amount": 260.0},
    {"id": "s4", "city": "Boston", "when": "2024-04-15T09:15", "offset": -4,
     "written": "2024-04-15", "amount": 95.5},
    {"id": "s5", "city": "Boston", "when": "2024-11-05T18:00", "offset": -5,
     "written": "2024-11-05", "amount": 143.0},
    # 23:30 on the 31st in Denver is 06:30 on the 1st in UTC. Different day,
    # different month, different year, different fiscal period.
    {"id": "s6", "city": "Denver", "when": "2024-12-31T23:30", "offset": -7,
     "written": "2024-12-31", "amount": 88.0},
)


def _late(rows: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """Two rows that arrived the day after they happened."""
    out = []
    for row in rows:
        row = dict(row)
        if row["id"] in ("s2", "s6"):
            row["written"] = (dt.date.fromisoformat(row["written"])
                              + dt.timedelta(days=1)).isoformat()
        out.append(row)
    return out


# --- the implementations ----------------------------------------------------

def _place(rows: list[dict[str, Any]], how: str) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        here = [s for s in STATIONS if s["city"] == row["city"]]
        if how == "place.centroid" or not here:
            out.append({"id": row["id"], "city": row["city"],
                        "state": STATE_OF.get(row["city"], ""),
                        "station": "", "km": None, "how": how})
            continue
        chosen = min(here, key=lambda s: s["km"]) if how == "place.nearest" \
            else here[0]
        out.append({"id": row["id"], "city": row["city"],
                    "state": STATE_OF.get(row["city"], ""),
                    "station": chosen["id"], "km": chosen["km"], "how": how})
    return out


def _period(rows: list[dict[str, Any]], how: str) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        local = dt.datetime.fromisoformat(row["when"])
        if how == "period.local_day":
            day = local.date().isoformat()
        elif how == "period.utc_day":
            day = (local - dt.timedelta(hours=row["offset"])).date().isoformat()
        else:                                   # processing
            day = row["written"]
        out.append({"id": row["id"], "day": day, "how": how,
                    # The as-of is what the row could know: the end of the day
                    # it happened, never the day it was processed.
                    "asof": local.date().isoformat(),
                    "written": row["written"]})
    return out


def _context(places: list[dict[str, Any]], periods: list[dict[str, Any]], *,
             final: bool) -> list[dict[str, Any]]:
    by_id = {p["id"]: p for p in periods}
    rows = []
    for place in places:
        period = by_id.get(place["id"], {})
        day, asof = period.get("day", ""), period.get("asof", "")

        found = next((o for o in OBSERVATIONS
                      if o["station"] == place["station"] and o["date"] == day),
                     None)
        rain, knowable = None, True
        if found:
            if final:
                rain = found["final"]
                # The one line that decides whether this is enrichment or a
                # leak: was the value we are attaching published by the time
                # the row happened?
                knowable = found["revised_at"] <= asof
            else:
                rain = found["first"]

        holiday = HOLIDAYS.get((place["state"], day), "")
        rows.append({
            "id": place["id"], "city": place["city"], "state": place["state"],
            "day": day, "asof": asof, "station": place["station"],
            "km": place["km"], "rain_mm": rain, "holiday": holiday,
            "value_knowable_on_the_day": knowable,
            "reading": "final" if final else "as-of",
            "region": "", "region_vintage": "",
        })
    return rows


def _region_at(city: str, day: str) -> str:
    """The assignment in force on that date, or "" if the date is unknown."""
    for row in REGIONS:
        if row["city"] == city and row["from"] <= day < row["to"]:
            return row["region"]
    return ""


def _region_now(city: str) -> str:
    """The assignment that is in force today — the latest one for this city.

    Written as "the row with the greatest start date" rather than "the row
    covering `date.today()`", because a pack whose output depends on when it is
    run cannot be tested, and this is exactly the sort of function that quietly
    becomes wrong in eighteen months.
    """
    here = [row for row in REGIONS if row["city"] == city]
    return max(here, key=lambda row: row["from"])["region"] if here else ""


def _vintage(context: list[dict[str, Any]], *, checked: bool
             ) -> list[dict[str, Any]]:
    out = []
    for row in context:
        if checked:
            region = _region_at(row["city"], row["day"] or "2999-01-01")
            vintage = row["day"]
        else:
            region = _region_now(row["city"])
            vintage = "current"
        correct = _region_at(row["city"], row["day"] or "2999-01-01")
        out.append({**row, "region": region, "region_vintage": vintage,
                    "region_is_current_for_the_row": region == correct})
    return out


def _attach(records: list[dict[str, Any]], context: list[dict[str, Any]], *,
            provenance: bool) -> list[dict[str, Any]]:
    by_id = {c["id"]: c for c in context}
    out = []
    for record in records:
        found = by_id.get(record["id"], {})
        if provenance:
            out.append({**record, **found})
            continue
        # Everything a downstream reader would need to catch either error,
        # removed. The row still looks enriched.
        out.append({**record, "rain_mm": found.get("rain_mm"),
                    "holiday": found.get("holiday", ""),
                    "region": found.get("region", ""),
                    "day": found.get("day", "")})
    return out


def _audit(rows: list[dict[str, Any]], *, leak: bool) -> dict[str, Any]:
    enriched = [r for r in rows
                if r.get("rain_mm") is not None or r.get("holiday")]
    if not leak:
        return {"check": "audit.count", "rows": len(rows),
                "enriched": len(enriched),
                "coverage": len(enriched) / len(rows) if rows else 0.0,
                # Recorded but not judged, so the two auditors can be compared
                # on identical data rather than on different runs.
                "findings": []}

    findings = []
    for row in rows:
        if row.get("value_knowable_on_the_day") is False:
            findings.append({"id": row["id"], "kind": "temporal",
                             "why": f"the value attached for {row.get('day')} "
                                    f"was published after that day"})
        if row.get("region_is_current_for_the_row") is False:
            findings.append({"id": row["id"], "kind": "spatial",
                             "why": f"region {row.get('region')!r} was not the "
                                    f"assignment in force on {row.get('day')}"})
        km = row.get("km")
        if km is not None and km > FAR_KM and row.get("rain_mm") is not None:
            findings.append({"id": row["id"], "kind": "distance",
                             "why": f"observation taken {km}km away"})
        if row.get("km") is None and row.get("rain_mm") is not None:
            findings.append({"id": row["id"], "kind": "provenance",
                             "why": "a value with no station and no distance "
                                    "cannot be checked at all"})
    return {"check": "audit.leak", "rows": len(rows),
            "enriched": len(enriched),
            "coverage": len(enriched) / len(rows) if rows else 0.0,
            "findings": findings,
            "leaks": len([f for f in findings if f["kind"] in
                          ("temporal", "spatial")])}


def runtime(rows: list[dict[str, Any]] | None = None) -> Runtime:
    source = [dict(r) for r in (rows or SALES)]
    late = _late(tuple(source))
    return Runtime({
        "load.sales": lambda **kw: source,
        "load.sales_late": lambda **kw: late,
        "place.nearest": lambda **kw: _place(kw["in"], "place.nearest"),
        "place.same_city": lambda **kw: _place(kw["in"], "place.same_city"),
        "place.centroid": lambda **kw: _place(kw["in"], "place.centroid"),
        "period.local_day": lambda **kw: _period(kw["in"], "period.local_day"),
        "period.utc_day": lambda **kw: _period(kw["in"], "period.utc_day"),
        "period.processing": lambda **kw: _period(kw["in"], "period.processing"),
        "context.asof": lambda **kw: _context(kw["places"], kw["periods"],
                                              final=False),
        "context.final": lambda **kw: _context(kw["places"], kw["periods"],
                                               final=True),
        "vintage.checked": lambda **kw: _vintage(kw["in"], checked=True),
        "vintage.current": lambda **kw: _vintage(kw["in"], checked=False),
        "attach.with_provenance": lambda **kw: _attach(kw["records"],
                                                       kw["context"],
                                                       provenance=True),
        "attach.value_only": lambda **kw: _attach(kw["records"], kw["context"],
                                                  provenance=False),
        "audit.leak": lambda **kw: _audit(kw["in"], leak=True),
        "audit.count": lambda **kw: _audit(kw["in"], leak=False),
    })


def example() -> dict[str, Any]:
    return {"rows": list(SALES)}


def enriches_without_leaking(run) -> tuple[bool, float]:
    """Coverage, minus what was not knowable at the time.

    The naive objective is coverage, and coverage alone is maximised by the
    route that leaks: `context.final` attaches a value on every row it can find
    a station for, and two of them are values from the future. So a leak costs
    more than a gap, and a route the audit cannot see into — `attach.value_only`
    strips the fields the audit reads — cannot score above a route it can,
    because unauditable is not the same as clean.
    """
    if not run.ok:
        return False, 0.0
    audit = run.output("audit")
    rows = max(1, audit["rows"])
    coverage = audit["enriched"] / rows
    if audit["check"] != "audit.leak":
        # Counted only. Half credit: this route may be perfect and nobody has
        # looked, which is exactly the provisional state `duecare` names.
        return audit["enriched"] > 0, coverage * 0.5
    penalty = sum({"temporal": 1.0, "spatial": 1.0, "distance": 0.5,
                   "provenance": 0.5}[f["kind"]] for f in audit["findings"])
    return audit["leaks"] == 0, max(0.0, (audit["enriched"] - penalty) / rows)
