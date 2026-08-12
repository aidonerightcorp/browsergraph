"""Addresses: two independent readings of one location, and what to do when they differ.

An address is written by a person and a postal code is a key into a reference
table. Both say where something is, they are produced by completely different
means, and **the interesting cases are the ones where they disagree**. Most
geocoding pipelines never find out, because they read one and use it.

    from browsergraph import packs
    print(packs.get("geo").solve().text())

The shape is therefore a fan-out and a join: parse what was typed, look up what
the code means, and reconcile. The ten example rows are ordinary — five clean,
five broken in the five ways addresses are actually broken:

    "9 Peachtree, Atlanta, NY 30303"      the state and the ZIP disagree
    "700 Pike St, Seattle, WA 98999"      a ZIP that does not exist
    "1600 Broadway, Denver, XZ 80202"     a state that does not exist
    "Chicago IL"                          no code at all
    "  55 Water St , new york , ny , 10001 "   the same address, typed by a human

Measured, by a test that runs every route:

    validator          accepted   rejected   what it let through
    validate.format         9          1      98999, which does not exist;
                                              Atlanta NY, whose state and code
                                              disagree; and Denver XZ, whose
                                              state is not a state
    validate.triple         6          4      nothing

`validate.format` is not a bad implementation. It is the correct implementation
of a *different question* — is this well-formed — and it answers that question
correctly: `98999` is five digits, `XZ` is two letters, and `Atlanta, NY 30303`
is a perfectly well-formed way to write a place that does not exist. The one
row it rejects is the one with no code at all.

Existence is a lookup, not a pattern, and a pipeline that checks the shape of a
value and files the result under "validated" has answered the cheap question and
charged for the expensive one. This is the single most common defect in address
handling and it survives review every time, because the regular expression is
visibly correct.

The reconcilers are the second lesson, and they are three answers to a real
question with no default:

    reconcile.prefer_zip   the code wins — "Atlanta, NY" silently becomes GA
    reconcile.prefer_text  the typed value wins — the ZIP is now in the wrong state
    reconcile.flag         emit both, mark the conflict, decide downstream

`reconcile.prefer_zip` is what almost every geocoder does and it is *usually
right*, which is exactly the problem: a row whose state was quietly corrected
and a row that was always correct come out identical, and the upstream form that
let somebody pick the wrong state never gets fixed.

The reference is a ten-row extract, real values, standard library. A deployment
swaps `lookup.zip` for one reading the Census or USPS file — which is a
candidate change and nothing else in the graph moves.
"""
from __future__ import annotations

import re
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

TEMPLATE = "enrich.geo"
SUMMARY = "parse an address, look the code up, and disagree out loud"

FILLING: dict[str, list[str]] = {
    "load":      ["load.rows", "load.trimmed"],
    "parse":     ["parse.positional", "parse.patterns"],
    "lookup":    ["lookup.zip", "lookup.prefix"],
    "reconcile": ["reconcile.flag", "reconcile.prefer_zip",
                  "reconcile.prefer_text"],
    "validate":  ["validate.triple", "validate.format"],
    "attach":    ["attach.all", "attach.valid_only"],
}

#: A ten-row extract of a postal reference. Real values, so the failure modes
#: below are the real ones rather than ones arranged to be catchable.
#:
#: `place` is the primary city, and this is where the first real subtlety
#: lives: a postal code has one primary city and any number of acceptable
#: alternates, so "the city does not match" is a weaker finding than it looks
#: and this pack says so rather than failing rows on it.
ZIPS: dict[str, dict[str, str]] = {
    "10001": {"place": "New York", "state": "NY", "county": "New York",
              "cbsa": "New York-Newark-Jersey City"},
    "60601": {"place": "Chicago", "state": "IL", "county": "Cook",
              "cbsa": "Chicago-Naperville-Elgin"},
    "94105": {"place": "San Francisco", "state": "CA",
              "county": "San Francisco",
              "cbsa": "San Francisco-Oakland-Berkeley"},
    "02108": {"place": "Boston", "state": "MA", "county": "Suffolk",
              "cbsa": "Boston-Cambridge-Newton"},
    "78701": {"place": "Austin", "state": "TX", "county": "Travis",
              "cbsa": "Austin-Round Rock-Georgetown"},
    "30303": {"place": "Atlanta", "state": "GA", "county": "Fulton",
              "cbsa": "Atlanta-Sandy Springs-Alpharetta"},
    "98101": {"place": "Seattle", "state": "WA", "county": "King",
              "cbsa": "Seattle-Tacoma-Bellevue"},
    "80202": {"place": "Denver", "state": "CO", "county": "Denver",
              "cbsa": "Denver-Aurora-Lakewood"},
    "19103": {"place": "Philadelphia", "state": "PA", "county": "Philadelphia",
              "cbsa": "Philadelphia-Camden-Wilmington"},
    "33131": {"place": "Miami", "state": "FL", "county": "Miami-Dade",
              "cbsa": "Miami-Fort Lauderdale-Pompano Beach"},
}

#: The first three digits of a postal code identify a sectional centre, which
#: is a real thing and always inside one state. Coarser than the full code,
#: matches more rows, and tells you less — the granularity trade, made visible.
PREFIXES: dict[str, str] = {code[:3]: value["state"]
                            for code, value in ZIPS.items()}

STATES: frozenset[str] = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV "
    "WI WY DC".split())

ZIP_PATTERN = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
STATE_PATTERN = re.compile(r"\b([A-Za-z]{2})\b(?=[\s,]*\d{5}|[\s,]*$)")


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
    _node("load.rows", "data.read", [], [("out", "Records")],
          description="The addresses as they arrived."),
    _node("load.trimmed", "data.read", [], [("out", "Records")],
          description="Whitespace collapsed and separators tidied first. "
                      "Changes what the parsers can see, which is the point "
                      "of having it as a separate decision."),

    _node("parse.positional", "geo.parse", [("in", "Records")],
          [("out", "Addresses")],
          description="Split on commas and read by position: street, city, "
                      "state, postal code. Correct on tidy input and wrong on "
                      "everything else."),
    _node("parse.patterns", "geo.parse", [("in", "Records")],
          [("out", "Addresses")],
          description="Find the postal code and the state by pattern wherever "
                      "they are, and take the city from what is left."),

    _node("lookup.zip", "geo.lookup", [("in", "Records")],
          [("out", "GeoCodes")],
          description="Full postal code to state, county and statistical "
                      "area."),
    _node("lookup.prefix", "geo.lookup", [("in", "Records")],
          [("out", "GeoCodes")],
          description="First three digits to state only. Matches codes the "
                      "full table has never seen, and gives back one field "
                      "instead of three."),

    _node("reconcile.flag", "geo.reconcile",
          [("parsed", "Addresses"), ("coded", "GeoCodes")],
          [("out", "Places"), ("conflicts", "Conflicts")],
          description="Keep both readings and record the disagreement. The "
                      "only candidate that leaves the decision to somebody "
                      "who knows the domain."),
    _node("reconcile.prefer_zip", "geo.reconcile",
          [("parsed", "Addresses"), ("coded", "GeoCodes")],
          [("out", "Places"), ("conflicts", "Conflicts")],
          description="The code wins. Usually right, silently."),
    _node("reconcile.prefer_text", "geo.reconcile",
          [("parsed", "Addresses"), ("coded", "GeoCodes")],
          [("out", "Places"), ("conflicts", "Conflicts")],
          description="What the person typed wins. Usually wrong, and honest "
                      "about whose fault it is."),

    _node("validate.triple", "geo.validate", [("in", "Places")],
          [("out", "Findings")],
          description="Does this place exist: is the code in the reference, "
                      "and does its state match the one on the row?"),
    _node("validate.format", "geo.validate", [("in", "Places")],
          [("out", "Findings")],
          description="Five digits and two letters. Answers a different "
                      "question, correctly."),

    _node("attach.all", "data.enrich",
          [("places", "Places"), ("findings", "Findings")],
          [("out", "Records"), ("dropped", "Records")],
          description="Every row, carrying its findings. Nothing is dropped, "
                      "so the second port comes back empty — which is a "
                      "measurement, not a formality."),
    _node("attach.valid_only", "data.enrich",
          [("places", "Places"), ("findings", "Findings")],
          [("out", "Records"), ("dropped", "Records")],
          description="Only the rows that validated. What it would not code "
                      "leaves by the second port, because an enrichment that "
                      "filters without saying so is data loss."),
)


def workbench() -> WorkbenchDefinition:
    template = _template(TEMPLATE)
    bench = template.instantiate(FILLING)
    return WorkbenchDefinition(
        title=template.title, task=template.task,
        stages=bench.stages, edges=bench.edges, nodes=NODES,
        candidates=tuple(NodeCandidate(id=n.id, node_id=n.id) for n in NODES),
        optimization_profiles=(OptimizationProfile(
            id="p.geo", name="Code the most rows without inventing a place",
            objectives=(OptimizationObjective("quality", "maximize", 1.0),)),),
        metadata=dict(bench.metadata, pack="geo"),
    ).assert_valid()


# --- the implementations ----------------------------------------------------

def _trim(text: str) -> str:
    return re.sub(r"\s*,\s*", ", ", re.sub(r"\s+", " ", str(text))).strip(" ,")


def _parse_positional(row: dict[str, Any]) -> dict[str, Any]:
    parts = [p.strip() for p in str(row["address"]).split(",") if p.strip()]
    street = parts[0] if parts else ""
    city = parts[1] if len(parts) > 1 else ""
    tail = parts[2] if len(parts) > 2 else ""
    # "NY 10001", or a bare state with the code in the next field entirely.
    bits = tail.split()
    state = bits[0].upper() if bits else ""
    code = bits[1] if len(bits) > 1 else (parts[3].strip() if len(parts) > 3 else "")
    return {"id": row["id"], "street": street, "city": city,
            "state": state, "zip": re.sub(r"\D", "", code)[:5],
            "reader": "parse.positional", "raw": row["address"]}


def _parse_patterns(row: dict[str, Any]) -> dict[str, Any]:
    text = str(row["address"])
    code = ZIP_PATTERN.search(text)
    state = STATE_PATTERN.search(text)
    parts = [p.strip() for p in text.split(",") if p.strip()]
    # The city is the last part that is not the state and not the code — a
    # heuristic, and it is written as one rather than dressed up as parsing.
    city = ""
    for part in reversed(parts):
        stripped = part.strip()
        if ZIP_PATTERN.fullmatch(stripped) or len(stripped) == 2:
            continue
        if code and code.group(1) in stripped and len(stripped) <= 9:
            continue
        city = re.sub(r"\b[A-Za-z]{2}\b\s*\d{5}.*$", "", stripped).strip()
        if city and not city[0].isdigit():
            break
    return {"id": row["id"], "street": parts[0] if parts else "",
            "city": city, "state": (state.group(1).upper() if state else ""),
            "zip": code.group(1) if code else "",
            "reader": "parse.patterns", "raw": text}


def _parse(rows: list[dict[str, Any]], how: str) -> list[dict[str, Any]]:
    read = _parse_positional if how == "positional" else _parse_patterns
    return [read(row) for row in rows]


def _lookup(rows: list[dict[str, Any]], *, prefix: bool) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        code = ZIP_PATTERN.search(str(row["address"]))
        digits = code.group(1) if code else ""
        if not prefix:
            found = ZIPS.get(digits)
            out.append({"id": row["id"], "zip": digits,
                        "state": found["state"] if found else "",
                        "place": found["place"] if found else "",
                        "county": found["county"] if found else "",
                        "cbsa": found["cbsa"] if found else "",
                        "granularity": "zip" if found else "none",
                        "source": "lookup.zip"})
            continue
        state = PREFIXES.get(digits[:3], "")
        out.append({"id": row["id"], "zip": digits, "state": state,
                    "place": "", "county": "", "cbsa": "",
                    # The honest label. A row coded to a sectional centre is
                    # not a row coded to a postal code, and anything joining
                    # on this downstream needs to know which it has.
                    "granularity": "zip3" if state else "none",
                    "source": "lookup.prefix"})
    return out


def _reconcile(parsed: list[dict[str, Any]], coded: list[dict[str, Any]],
               how: str) -> dict[str, Any]:
    by_id = {row["id"]: row for row in coded}
    places, conflicts = [], []
    for address in parsed:
        code = by_id.get(address["id"], {})
        typed_state, coded_state = address.get("state", ""), code.get("state", "")
        disagrees = bool(typed_state and coded_state and typed_state != coded_state)

        if how == "prefer_zip":
            state = coded_state or typed_state
        elif how == "prefer_text":
            state = typed_state or coded_state
        else:                                   # flag
            state = coded_state or typed_state

        if disagrees:
            conflicts.append({
                "id": address["id"], "field": "state",
                "typed": typed_state, "coded": coded_state,
                "resolved_to": state, "rule": how,
                "why": f"the address says {typed_state} and postal code "
                       f"{code.get('zip')} is in {coded_state}"})

        places.append({
            "id": address["id"], "street": address.get("street", ""),
            "city": address.get("city", ""), "state": state,
            "zip": address.get("zip") or code.get("zip", ""),
            "county": code.get("county", ""), "cbsa": code.get("cbsa", ""),
            "granularity": code.get("granularity", "none"),
            "typed_state": typed_state, "coded_state": coded_state,
            # Present on every row whichever reconciler ran, so a downstream
            # reader is never left guessing whether a value was corrected.
            "disputed": disagrees, "rule": how,
            "reference_place": code.get("place", ""),
        })
    return {"out": places, "conflicts": conflicts}


def _validate(places: list[dict[str, Any]], *, existence: bool
              ) -> dict[str, Any]:
    findings, verdicts = [], {}
    for place in places:
        problems = []
        code, state = place.get("zip", ""), place.get("state", "")

        if not existence:
            if not re.fullmatch(r"\d{5}", code or ""):
                problems.append("postal code is not five digits")
            if not re.fullmatch(r"[A-Z]{2}", state or ""):
                problems.append("state is not two letters")
        else:
            if code not in ZIPS:
                problems.append(f"postal code {code or '(none)'} is not in the "
                                f"reference")
            elif state and ZIPS[code]["state"] != state:
                problems.append(f"postal code {code} is in "
                                f"{ZIPS[code]['state']}, not {state}")
            if state and state not in STATES:
                problems.append(f"{state} is not a state")
            if place.get("disputed"):
                problems.append("the typed state and the coded state disagree")

        verdicts[place["id"]] = not problems
        if problems:
            findings.append({"id": place["id"], "problems": problems,
                             "check": "triple" if existence else "format"})
    return {"check": "validate.triple" if existence else "validate.format",
            "findings": findings, "valid": verdicts,
            "accepted": sum(1 for ok in verdicts.values() if ok),
            "rejected": sum(1 for ok in verdicts.values() if not ok)}


def _attach(places: list[dict[str, Any]], findings: dict[str, Any], *,
            valid_only: bool) -> dict[str, Any]:
    valid = findings["valid"]
    problems = {f["id"]: f["problems"] for f in findings["findings"]}
    rows = [{**place, "valid": valid.get(place["id"], False),
             "problems": problems.get(place["id"], [])}
            for place in places]
    kept = [row for row in rows if row["valid"]] if valid_only else rows
    lost = [row for row in rows if row not in kept] if valid_only else []
    return {"out": kept, "dropped": lost}


#: Ten rows: five that are fine and five broken in the five ways that matter.
EXAMPLE_ROWS: tuple[dict[str, str], ...] = (
    {"id": "r1", "address": "350 Fifth Ave, New York, NY 10001"},
    {"id": "r2", "address": "1 Financial Pl, Chicago, IL 60601"},
    {"id": "r3", "address": "500 Howard St, San Francisco, CA 94105"},
    {"id": "r4", "address": "22 Beacon St, Boston, MA 02108"},
    {"id": "r5", "address": "100 Congress Ave, Austin, TX 78701"},
    # The state and the code disagree. Atlanta is in Georgia; 30303 is a
    # Georgia code; somebody picked NY from a dropdown.
    {"id": "r6", "address": "9 Peachtree St, Atlanta, NY 30303"},
    # A well-formed code that does not exist.
    {"id": "r7", "address": "700 Pike St, Seattle, WA 98999"},
    # A well-formed state that does not exist.
    {"id": "r8", "address": "1600 Broadway, Denver, XZ 80202"},
    # No code at all — not an error, and not something to invent one for.
    {"id": "r9", "address": "Chicago, IL"},
    # The same address as r1, typed by a person.
    {"id": "r10", "address": "  55 Water St ,  new york , ny , 10001  "},
)


def runtime(rows: list[dict[str, Any]] | None = None) -> Runtime:
    source = [dict(row) for row in (rows or EXAMPLE_ROWS)]
    trimmed = [{**row, "address": _trim(row["address"])} for row in source]

    return Runtime({
        "load.rows": lambda **kw: source,
        "load.trimmed": lambda **kw: trimmed,
        "parse.positional": lambda **kw: _parse(kw["in"], "positional"),
        "parse.patterns": lambda **kw: _parse(kw["in"], "patterns"),
        "lookup.zip": lambda **kw: _lookup(kw["in"], prefix=False),
        "lookup.prefix": lambda **kw: _lookup(kw["in"], prefix=True),
        "reconcile.flag": lambda **kw: _reconcile(kw["parsed"], kw["coded"],
                                                  "flag"),
        "reconcile.prefer_zip": lambda **kw: _reconcile(kw["parsed"],
                                                        kw["coded"],
                                                        "prefer_zip"),
        "reconcile.prefer_text": lambda **kw: _reconcile(kw["parsed"],
                                                         kw["coded"],
                                                         "prefer_text"),
        "validate.triple": lambda **kw: _validate(kw["in"], existence=True),
        "validate.format": lambda **kw: _validate(kw["in"], existence=False),
        "attach.all": lambda **kw: _attach(kw["places"], kw["findings"],
                                           valid_only=False),
        "attach.valid_only": lambda **kw: _attach(kw["places"], kw["findings"],
                                                  valid_only=True),
    })


def example() -> dict[str, Any]:
    return {"rows": list(EXAMPLE_ROWS)}


def codes_without_inventing(run) -> tuple[bool, float]:
    """Reward rows coded to a real place; charge for rows coded to a wrong one.

    The naive objective — "how many rows came out with a state on them" — is
    maximised by `reconcile.prefer_zip` with `validate.format`, which codes all
    ten and is wrong about two of them. A geocoder is not judged on its match
    rate, and writing the verifier is where that stops being a slogan.
    """
    if not run.ok:
        return False, 0.0
    rows = run.output("attach", "out")
    if not rows:
        return False, 0.0
    good = sum(1 for row in rows
               if row.get("zip") in ZIPS
               and ZIPS[row["zip"]]["state"] == row.get("state")
               and not row.get("disputed"))
    wrong = sum(1 for row in rows
                if row.get("state")
                and row.get("zip") in ZIPS
                and ZIPS[row["zip"]]["state"] != row.get("state"))
    total = len(rows) + len(run.output("attach", "dropped")) or 1
    return good > 0, max(0.0, (good - 2 * wrong) / total)
