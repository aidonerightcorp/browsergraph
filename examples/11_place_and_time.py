#!/usr/bin/env python3
"""Where was it, when was it, and what else was true there then.

Two enrichments and the join between them. Each half is a join somebody knows
how to write; the pair is where the errors live, because every one of them is
defensible on its own axis.

    python examples/11_place_and_time.py
"""
from browsergraph import execute, packs
from browsergraph.compile import compile_route

# --- part one: an address is two readings of one place ----------------------

geo = packs.get("geo")
geo_bench = geo.workbench()


def code(**route):
    full = {"load": "load.trimmed", "parse": "parse.patterns",
            "lookup": "lookup.zip", "attach": "attach.all", **route}
    return execute.run(compile_route(geo_bench, full),
                       geo.runtime(**geo.example()))


print("Ten addresses, five of them broken in the five ways addresses break.\n")
run = code(reconcile="reconcile.flag", validate="validate.triple")
for row in run.output("attach", "out"):
    mark = "ok " if row["valid"] else "NO "
    note = "; ".join(row["problems"]) if row["problems"] else ""
    print(f"  {mark}{row['id']:<4}{row['city']!r:<17}{row['state']:<3}"
          f"{row['zip']:<7}{row['county']:<14}{note}")

print("\n" + "=" * 74)
print("The same rows, checked for shape instead of for existence\n")
for check in ("validate.format", "validate.triple"):
    found = code(reconcile="reconcile.flag", validate=check).output("validate")
    rejected = sorted(f["id"] for f in found["findings"])
    print(f"  {check:<18} accepted {found['accepted']:>2}  "
          f"rejected {found['rejected']:>2}  {rejected}")

print("\n  `98999` is five digits. `XZ` is two letters. `Atlanta, NY 30303` is\n"
      "  a well-formed way to write a place that does not exist. Existence is\n"
      "  a lookup, and a pipeline that files a format check under 'validated'\n"
      "  has answered the cheap question and charged for the expensive one.\n")

print("=" * 74)
print("Three reconcilers, one row where the text and the code disagree\n")
for rule in ("reconcile.prefer_zip", "reconcile.prefer_text", "reconcile.flag"):
    rows = {r["id"]: r for r in code(reconcile=rule,
                                     validate="validate.triple").output("attach", "out")}
    row = rows["r6"]
    print(f"  {rule:<24} state={row['state']}  "
          f"typed={row['typed_state']}  coded={row['coded_state']}  "
          f"disputed={row['disputed']}")

print("\n  `prefer_zip` is what almost every geocoder does and it is usually\n"
      "  right — which is the problem. Without the `disputed` flag, a row that\n"
      "  was quietly corrected and a row that was always correct look the same,\n"
      "  and the form that let somebody pick the wrong state never gets fixed.\n")

# --- part two: place and time together --------------------------------------

st = packs.get("spacetime")
st_bench = st.workbench()


def enrich(**route):
    full = {"load": "load.sales", "place": "place.nearest",
            "period": "period.local_day", "context": "context.asof",
            "vintage": "vintage.checked", "attach": "attach.with_provenance",
            "audit": "audit.leak", **route}
    return execute.run(compile_route(st_bench, full),
                       st.runtime(**st.example()))


print("=" * 74)
print("Six sales. What was happening in that city on that day?\n")
for row in enrich().output("attach"):
    print(f"  {row['id']:<4}{row['city']:<9}{row['day']:<12}"
          f"rain={str(row['rain_mm']):<6}{row['region']:<11}{row['holiday']}")

print("\n" + "=" * 74)
print("The same six, enriched with the *better* figure\n")
for context in ("context.asof", "context.final"):
    audit = enrich(context=context).output("audit")
    print(f"  {context:<16} enriched {audit['enriched']}/6   "
          f"leaks {audit['leaks']}")
    for finding in audit["findings"]:
        print(f"    {finding['id']}: {finding['why']}")

print("\n  Same coverage, better data, and two of the rows used a rainfall\n"
      "  figure published after the sale happened. A model trained on it learns\n"
      "  from a number that will not exist at prediction time.\n")

print("=" * 74)
print("The timezone, on the row that crosses midnight\n")
for period in ("period.local_day", "period.utc_day"):
    rows = {r["id"]: r for r in enrich(period=period).output("attach")}
    print(f"  {period:<20} s6 falls on {rows['s6']['day']}")
print("\n  23:30 on the thirty-first of December in Denver is the first of\n"
      "  January in UTC. Different day, month, year and fiscal period.\n")

print("=" * 74)
print("Today's boundaries, applied to last year's rows\n")
for vintage in ("vintage.checked", "vintage.current"):
    audit = enrich(vintage=vintage).output("audit")
    wrong = [f["id"] for f in audit["findings"] if f["kind"] == "spatial"]
    print(f"  {vintage:<20} rows in a region they were not in: {wrong or 'none'}")

print("\n" + "=" * 74)
print("And what happens when the provenance is dropped\n")
for attach in ("attach.with_provenance", "attach.value_only"):
    audit = enrich(place="place.same_city", attach=attach).output("audit")
    kinds = sorted({f["kind"] for f in audit["findings"]})
    print(f"  {attach:<24} findings: {len(audit['findings'])}  kinds: {kinds}")

print("\n  Five of those rows took their rainfall from a station 41 to 78km\n"
      "  away. With the distance dropped, the audit cannot see a single one.\n"
      "  A pipeline that strips its provenance does not become clean. It\n"
      "  becomes unauditable, and the two look identical on a dashboard.")
