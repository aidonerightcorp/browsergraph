"""Receipts in formats other tools already read.

A receipt is this project's own record of what ran. It is good, and it is
private: nothing else on a machine knows how to read it, so the lineage stops at
the edge of this library. That is a bad place for lineage to stop — the question
"where did this file come from" is asked by people holding a catalogue, not a
`TaskReceipt`.

So three exports, into standards that already exist and are already consumed:

* **W3C PROV** — the general answer. Entity, Activity, Agent, and the relations
  between them. If you only take one, take this: it is a W3C Recommendation and
  it is what a provenance store expects.
* **OpenLineage** — the data-engineering answer. Job, Run, inputs, outputs, in
  the shape Marquez and its friends ingest.
* **in-toto / SLSA-style attestation** — the supply-chain answer. What was
  produced, by what, from what, with digests.

Three things this module refuses to do, and each refusal is the point:

**It does not invent facts to fill a schema.** Every field is derived from the
receipt or omitted. A lineage record that guesses is worse than no record,
because it is indistinguishable from one that knows.

**It does not claim a build was hermetic, reproducible or isolated.** SLSA
predicates have fields for those and they are real claims about a builder. This
runs in whatever process you were in.

**It does not sign anything.** An attestation without a signature is a
statement, not a guarantee, and it says so in its own `_note` field rather than
letting the shape imply otherwise.

    from browsergraph import provenance
    print(provenance.to_prov(receipt))
    print(provenance.to_openlineage(receipt))
    print(provenance.to_attestation(receipt))
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Any

#: The namespace for terms that are ours rather than the standard's. Kept
#: explicit so a consumer can tell our vocabulary from the specification's, and
#: drop what it does not understand instead of misreading it.
NAMESPACE = "https://github.com/aidonerightcorp/browsergraph/ns#"

PROV_CONTEXT = {
    "prov": "http://www.w3.org/ns/prov#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "bg": NAMESPACE,
}


def _when(epoch: float) -> str:
    """An epoch second as RFC 3339 UTC, which is what every one of these wants."""
    if not epoch:
        return ""
    return (_dt.datetime.fromtimestamp(epoch, tz=_dt.timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def _artifacts(receipt) -> list:
    return list(getattr(receipt, "artifacts", ()) or [])


def _run_id(receipt) -> str:
    """A stable identifier for this run.

    The plan digest names the *computation*; it is deliberately the same for two
    runs of the same plan, which is exactly what makes it useful and exactly
    what makes it wrong as a run id. Started-at disambiguates them.
    """
    plan = getattr(receipt, "plan", "") or "plan:none"
    return f"{plan}@{getattr(receipt, 'started_at', 0.0):.6f}"


# --- W3C PROV ---------------------------------------------------------------

def to_prov(receipt, *, indent: int = 2) -> str:
    """The run as PROV-JSON: activities, entities, agents and their relations.

    The mapping, stated so it can be argued with:

    * the run is an **Activity**, and so is each step;
    * every artifact is an **Entity**, `wasGeneratedBy` the step that wrote it;
    * each candidate is a **SoftwareAgent**, and its step `wasAssociatedWith` it;
    * the plan digest is an Entity the run `used`, because the plan is an input
      to the run in every sense that matters.

    Steps are nested under the run with `wasInformedBy`, rather than flattened,
    so a consumer can ask "what did this run do" and get four answers instead of
    forty.
    """
    run = _run_id(receipt)
    started, ended = getattr(receipt, "started_at", 0.0), 0.0
    if started:
        ended = started + float(getattr(receipt, "seconds", 0.0) or 0.0)

    activities: dict[str, Any] = {
        f"bg:run/{run}": {
            "prov:startTime": _when(started),
            "prov:endTime": _when(ended),
            "bg:task": getattr(receipt, "task", ""),
            "bg:graph": getattr(receipt, "graph", ""),
            "bg:ok": bool(getattr(receipt, "ok", False)),
        }
    }
    entities: dict[str, Any] = {}
    agents: dict[str, Any] = {}
    used: dict[str, Any] = {}
    generated: dict[str, Any] = {}
    associated: dict[str, Any] = {}
    informed: dict[str, Any] = {}

    plan = getattr(receipt, "plan", "")
    if plan:
        entities[f"bg:plan/{plan}"] = {
            "prov:type": "bg:CompiledPlan",
            "bg:digest": plan,
            # The plan is content-addressed, so this entity *is* its identity —
            # two runs of the same plan point at the same entity, which is the
            # property that makes lineage across runs answerable at all.
        }
        used["bg:used/plan"] = {"prov:activity": f"bg:run/{run}",
                                "prov:entity": f"bg:plan/{plan}"}

    for index, step in enumerate(getattr(receipt, "steps", ()) or ()):
        candidate = getattr(step, "key", "") or getattr(step, "kind", "")
        step_id = f"bg:step/{run}/{index}/{candidate}"
        activities[step_id] = {
            "bg:stage": getattr(step, "kind", ""),
            "bg:candidate": candidate,
            "bg:seconds": round(float(getattr(step, "seconds", 0.0) or 0.0), 6),
            "bg:ok": bool(getattr(step, "ok", False)),
        }
        if getattr(step, "error", ""):
            activities[step_id]["bg:error"] = step.error
        informed[f"bg:informed/{index}"] = {
            "prov:informed": step_id, "prov:informant": f"bg:run/{run}"}

        if candidate:
            agents[f"bg:agent/{candidate}"] = {
                "prov:type": "prov:SoftwareAgent", "bg:candidate": candidate}
            associated[f"bg:assoc/{index}"] = {
                "prov:activity": step_id, "prov:agent": f"bg:agent/{candidate}"}

    for index, artifact in enumerate(_artifacts(receipt)):
        entity_id = f"bg:artifact/{getattr(artifact, 'digest', index)}"
        entities[entity_id] = {
            "prov:type": "prov:Entity",
            "bg:path": getattr(artifact, "path", ""),
            "bg:bytes": getattr(artifact, "bytes", 0),
            "bg:digest": getattr(artifact, "digest", ""),
        }
        generated[f"bg:gen/{index}"] = {
            "prov:entity": entity_id, "prov:activity": f"bg:run/{run}"}

    document = {"@context": PROV_CONTEXT, "activity": activities}
    for name, block in (("entity", entities), ("agent", agents), ("used", used),
                        ("wasGeneratedBy", generated),
                        ("wasAssociatedWith", associated),
                        ("wasInformedBy", informed)):
        if block:
            document[name] = block
    return json.dumps(document, indent=indent, sort_keys=True)


# --- OpenLineage ------------------------------------------------------------

def to_openlineage(receipt, *, namespace: str = "browsergraph",
                   indent: int = 2) -> str:
    """A single terminal OpenLineage run event.

    One event rather than the START/COMPLETE pair, because a receipt is written
    after the fact: emitting a START whose timestamp was reconstructed later
    would be inventing an observation nobody made. Consumers accept a lone
    terminal event, and one true event beats two plausible ones.

    Facets are where the honest detail goes. The route, the plan digest and the
    per-step outcomes are ours, so they live under a `browsergraph_` facet with
    `_producer` pointing here — the specification's own mechanism for carrying
    what it does not standardise.
    """
    started = getattr(receipt, "started_at", 0.0)
    if not started:
        # `eventTime` is required by the specification. Emitting an empty string
        # produces a document that looks like an event and is rejected by
        # anything that validates — the worst of both, since it is only
        # discovered in somebody else's ingest log.
        raise ValueError(
            "this receipt has no start time, and OpenLineage requires an "
            "eventTime. Receipts from `Run.receipt()` carry one; a hand-built "
            "TaskReceipt needs `started_at` set to an epoch second.")
    ended = started + float(getattr(receipt, "seconds", 0.0) or 0.0)
    ok = bool(getattr(receipt, "ok", False))

    outputs = []
    for artifact in _artifacts(receipt):
        outputs.append({
            "namespace": namespace,
            "name": getattr(artifact, "path", ""),
            "facets": {"browsergraph_artifact": {
                "_producer": NAMESPACE,
                "_schemaURL": NAMESPACE + "artifact",
                "digest": getattr(artifact, "digest", ""),
                "bytes": getattr(artifact, "bytes", 0)}},
        })

    event = {
        "eventType": "COMPLETE" if ok else "FAIL",
        "eventTime": _when(ended or started),
        "producer": NAMESPACE,
        "schemaURL": "https://openlineage.io/spec/1-0-5/OpenLineage.json"
                     "#/definitions/RunEvent",
        "run": {
            "runId": _run_id(receipt),
            "facets": {
                "nominalTime": {"_producer": NAMESPACE,
                                "nominalStartTime": _when(started),
                                "nominalEndTime": _when(ended)},
                "browsergraph_route": {
                    "_producer": NAMESPACE,
                    "_schemaURL": NAMESPACE + "route",
                    "plan": getattr(receipt, "plan", ""),
                    "route": list(getattr(receipt, "route", ()) or ()),
                    "steps": [{"stage": getattr(s, "kind", ""),
                               "candidate": getattr(s, "key", ""),
                               "ok": bool(getattr(s, "ok", False)),
                               "seconds": round(float(getattr(s, "seconds", 0.0) or 0.0), 6)}
                              for s in getattr(receipt, "steps", ()) or ()],
                },
            },
        },
        "job": {"namespace": namespace,
                "name": getattr(receipt, "task", "") or "browsergraph.run"},
        "inputs": [],
        "outputs": outputs,
    }
    if not ok and getattr(receipt, "error", ""):
        event["run"]["facets"]["errorMessage"] = {
            "_producer": NAMESPACE, "message": receipt.error,
            "programmingLanguage": "PYTHON"}
    return json.dumps(event, indent=indent, sort_keys=True)


# --- in-toto / SLSA-shaped attestation --------------------------------------

def to_attestation(receipt, *, indent: int = 2) -> str:
    """What was produced, by what, from what — in in-toto statement shape.

    Deliberately conservative about the claims it makes:

    * `buildType` points at this library's own URI, not at a SLSA build type
      somebody else defined and would be entitled to expect guarantees from.
    * `externalParameters` carries the plan digest, because the plan is what
      determined the build.
    * There is **no** `metadata.reproducible: true`, no hermeticity claim, and
      no signature. A run happened in whatever process you were in, and an
      unsigned statement is a statement.

    That last point is in the document itself under `_note`, so a consumer
    reading only the JSON is told the same thing as a reader of this docstring.
    """
    subjects = [{"name": getattr(a, "path", ""),
                 "digest": {"sha256": (getattr(a, "digest", "") or "")
                            .removeprefix("sha256:")}}
                for a in _artifacts(receipt)]

    started = getattr(receipt, "started_at", 0.0)
    return json.dumps({
        "_type": "https://in-toto.io/Statement/v1",
        "subject": subjects,
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": NAMESPACE + "run",
                "externalParameters": {
                    "task": getattr(receipt, "task", ""),
                    "graph": getattr(receipt, "graph", ""),
                    "plan": getattr(receipt, "plan", ""),
                    "route": list(getattr(receipt, "route", ()) or ()),
                },
                "internalParameters": dict(getattr(receipt, "env", {}) or {}),
            },
            "runDetails": {
                "builder": {"id": NAMESPACE + "browsergraph"},
                "metadata": {
                    "invocationId": _run_id(receipt),
                    "startedOn": _when(started),
                    "finishedOn": _when(
                        started + float(getattr(receipt, "seconds", 0.0) or 0.0)
                        if started else 0.0),
                },
            },
        },
        "_note": "Unsigned, and makes no hermeticity or reproducibility claim. "
                 "This records what ran; it does not attest to the isolation of "
                 "the environment it ran in.",
    }, indent=indent, sort_keys=True)


FORMATS = {"prov": to_prov, "openlineage": to_openlineage,
           "attestation": to_attestation}


def export(receipt, form: str = "prov", **kwargs) -> str:
    """One entry point, so a CLI flag maps to a format without a lookup table."""
    if form not in FORMATS:
        raise KeyError(f"no exporter {form!r}; available: "
                       f"{', '.join(sorted(FORMATS))}")
    return FORMATS[form](receipt, **kwargs)
