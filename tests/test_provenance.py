"""Receipts must become documents other tools can read.

A lineage record that guesses is worse than none: it is indistinguishable from
one that knows. These check the exports carry what the receipt actually said,
and refuse rather than invent when it did not say enough.
"""
from __future__ import annotations

import json

import pytest

from browsergraph import execute, provenance
from browsergraph.compile import compile_route
from browsergraph.quick import chain, graph, node, step


@pytest.fixture
def receipt(tmp_path):
    nodes = [node("read.a", "read", gives=[("out", "Rows")]),
             node("save.it", "save", [("in", "Rows")], [("out", "Note")],
                  effects=("file.write",))]
    steps = [step("read", "Read", [], [("out", "Rows")], "read", ["read.a"]),
             step("save", "Save", [("in", "Rows")], [("out", "Note")], "save",
                  ["save.it"])]
    bench = graph("Prov", "read then save", steps, nodes, chain("read", "save"))

    def save(**kwargs):
        (tmp_path / "out.json").write_text(json.dumps(kwargs["in"]))
        return {"wrote": "out.json"}

    runtime = execute.Runtime({"read.a": lambda **kw: [1, 2, 3], "save.it": save})
    plan = compile_route(bench, {"read": "read.a", "save": "save.it"})
    run = execute.run(plan, runtime, workspace=str(tmp_path))
    assert run.ok and run.artifacts, "the fixture must produce an artifact"
    return run.receipt(task="prov-demo", graph="Prov")


@pytest.mark.parametrize("form", sorted(provenance.FORMATS))
def test_every_export_is_valid_json(form, receipt):
    assert json.loads(provenance.export(receipt, form))


def test_a_run_records_when_it_happened_not_only_how_long_it_took(receipt):
    """`seconds` comes off the monotonic clock — right for durations, useless
    for saying *when*, and a receipt with no when cannot become an event."""
    assert receipt.started_at > 1_000_000_000


def test_prov_names_the_artifact_as_an_entity_generated_by_the_run(receipt):
    document = json.loads(provenance.to_prov(receipt))
    assert document["@context"]["prov"] == "http://www.w3.org/ns/prov#"
    assert document["wasGeneratedBy"], "an artifact nothing generated is not lineage"
    entities = json.dumps(document["entity"])
    assert "out.json" in entities


def test_prov_makes_each_candidate_a_software_agent(receipt):
    document = json.loads(provenance.to_prov(receipt))
    agents = document["agent"]
    assert any(a["prov:type"] == "prov:SoftwareAgent" for a in agents.values())
    assert any("read.a" in key for key in agents)


def test_prov_treats_the_plan_as_an_input_to_the_run(receipt):
    """The plan is content-addressed, so two runs of it point at one entity —
    the property that makes lineage answerable across runs."""
    document = json.loads(provenance.to_prov(receipt))
    assert document["used"], "the plan should be used by the run"
    assert receipt.plan in json.dumps(document["entity"])


def test_openlineage_carries_a_real_event_time(receipt):
    event = json.loads(provenance.to_openlineage(receipt))
    assert event["eventTime"].endswith("Z") and len(event["eventTime"]) > 10
    assert event["eventType"] == "COMPLETE"


def test_openlineage_refuses_a_receipt_with_no_time_rather_than_emitting_one(receipt):
    """eventTime is required. An empty string produces a document that looks
    like an event and is rejected in somebody else's ingest log."""
    from dataclasses import replace

    with pytest.raises(ValueError, match="eventTime"):
        provenance.to_openlineage(replace(receipt, started_at=0.0))


def test_openlineage_puts_our_own_fields_in_a_namespaced_facet(receipt):
    """The specification's mechanism for what it does not standardise. A
    consumer can drop what it does not understand instead of misreading it."""
    event = json.loads(provenance.to_openlineage(receipt))
    facet = event["run"]["facets"]["browsergraph_route"]
    assert facet["_producer"] == provenance.NAMESPACE
    assert facet["plan"] == receipt.plan
    assert [s["candidate"] for s in facet["steps"]] == ["read.a", "save.it"]


def test_a_failed_run_becomes_a_fail_event_with_its_reason(tmp_path):
    nodes = [node("boom.it", "boom", gives=[("out", "Rows")])]
    steps = [step("boom", "Boom", [], [("out", "Rows")], "boom", ["boom.it"])]
    bench = graph("Fails", "always", steps, nodes)

    def boom(**kwargs):
        raise RuntimeError("nope")

    run = execute.run(compile_route(bench, {"boom": "boom.it"}),
                      execute.Runtime({"boom.it": boom}), strict=False)
    event = json.loads(provenance.to_openlineage(run.receipt(task="t")))
    assert event["eventType"] == "FAIL"
    assert "nope" in json.dumps(event["run"]["facets"]["errorMessage"])


def test_the_attestation_states_that_it_claims_nothing_about_isolation(receipt):
    """An unsigned statement is a statement. Saying so in the document means a
    consumer reading only the JSON is told what a reader of the source is."""
    document = json.loads(provenance.to_attestation(receipt))
    assert document["_type"] == "https://in-toto.io/Statement/v1"
    assert "Unsigned" in document["_note"]
    assert "reproducib" in document["_note"]
    metadata = document["predicate"]["runDetails"]["metadata"]
    assert "reproducible" not in json.dumps(metadata)


def test_the_attestation_subjects_are_the_artifacts_with_digests(receipt):
    document = json.loads(provenance.to_attestation(receipt))
    subject = document["subject"][0]
    assert subject["name"].endswith("out.json")
    assert len(subject["digest"]["sha256"]) >= 16
    assert not subject["digest"]["sha256"].startswith("sha256:"), \
        "the prefix belongs to the key, not the value"


def test_an_unknown_format_says_what_there_is(receipt):
    with pytest.raises(KeyError, match="openlineage"):
        provenance.export(receipt, "yaml-please")
