"""A template is a promise that the shape of a known problem is already right.

So the tests are about the promise, not the contents: every skeleton must be a
valid graph, its joins must actually be joins, filling it must produce something
the compiler accepts, and mis-filling it must fail loudly rather than quietly
dropping a step.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from browsergraph import templates as T


@pytest.mark.parametrize("template", T.CATALOG, ids=lambda t: t.id)
def test_every_skeleton_is_a_valid_graph(template):
    """A template that does not validate teaches its shape to everyone who
    copies it, which is worse than not shipping it."""
    shape = template.skeleton()
    structural = [p for p in shape.validate()
                  if "candidate" not in p.lower() and "node" not in p.lower()]
    assert structural == [], f"{template.id}: {structural}"


@pytest.mark.parametrize("template", T.CATALOG, ids=lambda t: t.id)
def test_no_template_contains_a_cycle(template):
    assert template.skeleton().cycles() == []


@pytest.mark.parametrize("template", T.CATALOG, ids=lambda t: t.id)
def test_every_template_compiles_when_its_slots_are_filled(template):
    """The test that structural validation cannot replace.

    `validate()` checks the shape — ids exist, edges point somewhere, no
    cycles — and says nothing about whether the *types* line up across an edge.
    `document.extraction` passed every structural check while emitting a
    `Format` into a slot that consumes `Bytes`, and the mismatch only surfaced
    when a notebook instantiated it for real. A template that cannot compile is
    a trap: it looks authoritative and fails in the user's hands.
    """
    from browsergraph.compile import compile_route
    from browsergraph.manifest import NodeManifest, PortSpec
    from browsergraph.types import element_of

    nodes, filling = [], {}
    for slot in template.slots:
        node_id = f"probe.{slot.id}"
        # A map slot's *stage* talks about the collection; the node inside it
        # handles one item. Building the probe with the collection types would
        # fail for exactly the difference that makes it a map.
        unwrap = element_of if slot.kind == "map" else (lambda t: t)
        nodes.append(NodeManifest(
            id=node_id, kind="probe",
            description=f"Probe implementation of {slot.id}.",
            capabilities=slot.capabilities or ("probe",),
            inputs=tuple(PortSpec(n, unwrap(t)) for n, t in slot.inputs),
            outputs=tuple(PortSpec(n, unwrap(t)) for n, t in slot.outputs),
        ).assert_valid())
        filling[slot.id] = [node_id]

    bench = template.instantiate(filling)
    bench = replace(bench, nodes=tuple(nodes))
    plan = compile_route(bench, {s.id: f"probe.{s.id}" for s in template.slots})
    assert plan.digest.startswith("plan:")
    assert len(plan.steps) == len(template.slots)


@pytest.mark.parametrize("template", T.CATALOG, ids=lambda t: t.id)
def test_every_edge_points_at_slots_that_exist(template):
    ids = set(template.slot_ids)
    for edge in template.edges:
        assert edge.source in ids, f"{template.id}: edge from unknown {edge.source}"
        assert edge.target in ids, f"{template.id}: edge to unknown {edge.target}"


@pytest.mark.parametrize("template", T.CATALOG, ids=lambda t: t.id)
def test_every_template_carries_its_anti_patterns(template):
    """Guidance nobody loads is guidance nobody follows. The warnings travel
    with the shape so a harness holding one holds both."""
    assert template.anti_patterns, f"{template.id} ships no anti-patterns"
    assert all(len(a) > 40 for a in template.anti_patterns), (
        f"{template.id}: an anti-pattern too short to act on")


@pytest.mark.parametrize("template", T.CATALOG, ids=lambda t: t.id)
def test_the_anti_patterns_survive_into_the_instantiated_workbench(template):
    bench = template.instantiate({})
    assert bench.metadata["anti_patterns"] == list(template.anti_patterns)
    assert bench.metadata["template"] == template.id


# --- the joins are the point ------------------------------------------------

def test_a_multi_input_slot_is_wired_from_more_than_one_edge():
    """A 'join' fed by one edge is a chain with extra ceremony.

    Counted over `(source, port)` rather than over sources. Two edges leaving
    *different ports of the same node* is a real two-input step — `split` hands
    `prepare` a training half and a validation half, and no single-port chain
    can carry both. Counting distinct sources rejected that, which is stricter
    than the sentence above and stricter than it should be: the thing being
    forbidden is a slot that declares two inputs and receives one value.
    """
    for template in T.CATALOG:
        for slot in template.slots:
            if len(slot.inputs) < 2:
                continue
            feeders = {(e.source, e.from_port) for e in template.edges
                       if e.target == slot.id}
            assert len(feeders) >= 2, (
                f"{template.id}.{slot.id} declares {len(slot.inputs)} inputs but "
                f"is fed by {feeders}")


def test_the_tabular_template_encodes_numeric_and_categorical_in_parallel():
    """The claim that a Kaggle pipeline is a graph rests on exactly this."""
    shape = T.get("tabular.supervised").skeleton()
    assert not shape.is_chain
    assert ["numeric", "categorical"] in shape.layers()


def test_every_port_a_join_declares_is_actually_fed():
    for template in T.CATALOG:
        for slot in template.slots:
            if len(slot.inputs) < 2:
                continue
            fed = {e.to_port for e in template.edges if e.target == slot.id}
            declared = {name for name, _ in slot.inputs}
            assert declared <= fed, (
                f"{template.id}.{slot.id}: ports {declared - fed} never receive "
                f"an edge, so the join silently runs on a missing input")


# --- filling ----------------------------------------------------------------

def test_filling_slots_produces_a_workbench_with_those_candidates():
    bench = T.get("data.quality").instantiate({
        "profile": ["profile.pandas"], "schema": ["schema.strict", "schema.loose"],
        "distribution": ["dist.psi"], "adjudicate": ["gate.any"]})
    assert bench.route_count() == 2
    assert bench.stage("schema").candidates == ("schema.strict", "schema.loose")
    assert {c.id for c in bench.candidates} == {
        "profile.pandas", "schema.strict", "schema.loose", "dist.psi", "gate.any"}


def test_a_typo_in_a_slot_name_is_refused_rather_than_ignored():
    """Letting it through produces a graph that silently omits a step — the
    most expensive failure available, because everything else still compiles."""
    with pytest.raises(KeyError) as caught:
        T.get("data.quality").instantiate({"profiel": ["x"]})
    assert "profiel" in str(caught.value)
    assert "profile" in str(caught.value), "the error should list the real slots"


def test_a_partial_filling_reports_exactly_what_is_still_missing():
    template = T.get("tabular.supervised")
    missing = template.unfilled({"load": ["csv"], "split": ["holdout"]})
    assert "load" not in missing and "split" not in missing
    assert "fit" in missing and "assemble" in missing


def test_an_optional_slot_is_never_reported_as_missing():
    """Otherwise a model learns to fill optional steps with anything."""
    assert "calibrate" not in T.get("tabular.supervised").unfilled({})


def test_a_filled_template_draws_without_the_visualiser_knowing_the_domain():
    from browsergraph import viz
    bench = T.get("service.notification").instantiate(
        {slot: [f"{slot}.default"] for slot in T.get("service.notification").slot_ids})
    assert viz.dag(bench).svg.startswith("<svg")
    assert viz.route_space(bench).svg.startswith("<svg")


# --- the catalogue ----------------------------------------------------------

def test_the_catalogue_spans_unlike_domains():
    """Six variations on a data pipeline would be a weaker argument for
    generality than six things that look nothing like each other."""
    assert len(T.domains()) >= 5


def test_template_ids_are_unique():
    assert len(T.BY_ID) == len(T.CATALOG)


def test_an_unknown_template_id_says_what_is_available():
    with pytest.raises(KeyError) as caught:
        T.get("nope")
    assert "tabular.supervised" in str(caught.value)


def test_the_catalogue_text_names_every_template():
    text = T.catalog_text()
    for template in T.CATALOG:
        assert template.id in text and template.task in text
