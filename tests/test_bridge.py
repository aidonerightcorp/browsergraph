"""The two models have to agree, or having both is just confusing.

`WorkbenchDefinition` is the one you write by hand. `ProgramGraph` is the strict
one. These tests check that everything you can write in the first survives into
the second, and that the second still says no when it should.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from browsergraph import bridge
from browsergraph import templates as T
from browsergraph.manifest import NodeManifest, PortSpec
from solutiongraph.model import Cardinality, Determinism, Idempotency, SlotKind


def _probe(slot):
    return NodeManifest(
        id=f"probe.{slot.id}", kind="probe",
        description=f"Probe for {slot.id}.",
        capabilities=slot.capabilities or ("probe",),
        inputs=tuple(PortSpec(n, t) for n, t in slot.inputs),
        outputs=tuple(PortSpec(n, t) for n, t in slot.outputs))


def _filled(template):
    nodes = [_probe(s) for s in template.slots]
    bench = template.instantiate({s.id: [f"probe.{s.id}"] for s in template.slots})
    return replace(bench, nodes=tuple(nodes))


# --- every template survives the crossing ------------------------------------

@pytest.mark.parametrize("template", T.CATALOG, ids=lambda t: t.id)
def test_every_template_becomes_a_valid_program_graph(template):
    """The whole point of merging. If a template cannot cross, the two models
    are separate products that happen to share a repo."""
    assert bridge.check(_filled(template)) == []


@pytest.mark.parametrize("template", T.CATALOG, ids=lambda t: t.id)
def test_no_slot_or_edge_is_lost_on_the_way_across(template):
    bench = _filled(template)
    program = bridge.to_program_graph(bench)
    assert len(program.slots) == len(bench.leaf_stages)
    assert len(program.edges) == len(bench.wiring())


@pytest.mark.parametrize("template", T.CATALOG, ids=lambda t: t.id)
def test_the_strict_compiler_admits_a_candidate_for_every_slot(template):
    space = bridge.admitted(_filled(template))
    for slot in bridge.to_program_graph(_filled(template)).slots:
        options = dict(space.choices).get(slot.id, ())
        assert options, f"{slot.id} has nothing admitted"


# --- the parts we choose, stated plainly -------------------------------------

def test_a_type_name_gains_a_version_and_a_media_type():
    """A workbench type is a bare word. The strict model wants more. We add
    the least surprising defaults and they are visible in the output."""
    got = bridge.value_type("Records")
    assert got.id == "type.records"
    assert got.version == "1"
    assert got.media_type == "application/json"


def test_a_one_word_type_is_namespaced_rather_than_refused():
    assert bridge.value_type("Frame").id.startswith("type.")
    assert bridge.value_type("my.frame").id == "my.frame"


def test_an_optional_port_becomes_optional_cardinality():
    manifest = NodeManifest(
        id="x.y", kind="k", description="d", capabilities=("c",),
        inputs=(PortSpec("a", "A", required=False),),
        outputs=(PortSpec("out", "B"),))
    spec = bridge.node_spec(manifest)
    assert spec.inputs[0].cardinality is Cardinality.OPTIONAL
    assert spec.outputs[0].cardinality is Cardinality.ONE


def test_a_workbench_never_claims_many_because_it_cannot_say_it():
    """Claiming a shape the source could not express would make the strict
    model less trustworthy, not more capable."""
    for template in T.CATALOG:
        for node in bridge.to_registry(_filled(template)).nodes:
            for port in (*node.inputs, *node.outputs):
                assert port.cardinality in (Cardinality.ONE, Cardinality.OPTIONAL)


def test_a_false_determinism_flag_claims_the_least():
    """Four levels, one flag. `False` maps to the weakest claim on purpose."""
    loose = NodeManifest(id="x.y", kind="k", description="d", capabilities=("c",),
                         outputs=(PortSpec("out", "B"),),
                         runtime={"deterministic": False})
    tight = replace(loose, runtime={"deterministic": True})
    assert bridge.node_spec(loose).determinism is Determinism.NONDETERMINISTIC
    assert bridge.node_spec(tight).determinism is Determinism.DETERMINISTIC


def test_a_node_with_no_effects_is_idempotent_by_definition():
    """Not a guess: a step that changes nothing outside itself gives the same
    result when run twice."""
    pure = NodeManifest(id="x.y", kind="k", description="d", capabilities=("c",),
                        outputs=(PortSpec("out", "B"),))
    dirty = replace(pure, effects=("network.write",))
    assert bridge.node_spec(pure).idempotency is Idempotency.IDEMPOTENT
    assert bridge.node_spec(dirty).idempotency is Idempotency.UNKNOWN


def test_a_composite_stage_crosses_as_a_composite_slot():
    from browsergraph.workbench import StageDefinition
    leaf = StageDefinition(id="inner", output_type="B", required_capabilities=("c",))
    outer = StageDefinition(id="outer", substages=(leaf,))
    assert bridge.semantic_slot(outer).kind is SlotKind.COMPOSITE
    assert bridge.semantic_slot(leaf).kind is SlotKind.ATOMIC


def test_a_stage_with_no_success_line_gets_the_weakest_honest_one():
    """The strict model refuses an empty contract, rightly. We write down the
    weakest true statement rather than inventing a specific claim."""
    from browsergraph.workbench import StageDefinition
    slot = bridge.semantic_slot(StageDefinition(id="thing", output_type="B"))
    assert "produced its declared output" in slot.success_contract


# --- the strict model still says no ------------------------------------------

def test_a_mismatched_edge_is_reported_with_a_code_not_a_sentence():
    """A harness can act on a code and a path. It cannot act on prose."""
    template = T.get("data.quality")
    bench = _filled(template)
    broken = replace(bench, nodes=tuple(
        replace(n, outputs=(PortSpec("out", "SomethingElse"),))
        if n.id == "probe.profile" else n for n in bench.nodes))
    problems = bridge.check(broken)
    assert problems, "the strict compiler accepted a type mismatch"
    assert all(p.split()[0].startswith("UNG-") for p in problems)


def test_effects_and_permissions_are_carried_up_to_the_program():
    bench = _filled(T.get("service.notification"))
    bench = replace(bench, nodes=tuple(
        replace(n, effects=("network.write",), permissions=("net.send",))
        if n.id == "probe.deliver" else n for n in bench.nodes))
    program = bridge.to_program_graph(bench)
    assert "network.write" in program.allowed_effects
    assert "net.send" in program.granted_permissions


# --- coming back is lossy, and says so ---------------------------------------

def test_a_program_graph_can_be_viewed_as_a_workbench():
    """Lossy on purpose. Enough survives to draw it and read its shape."""
    from browsergraph import viz

    bench = _filled(T.get("tabular.supervised"))
    program = bridge.to_program_graph(bench)
    back = bridge.to_workbench(program)

    assert len(back.leaf_stages) == len(program.slots)
    assert back.layers() == bench.layers()
    assert viz.dag(back).svg.startswith("<svg")


def test_the_round_trip_keeps_the_program_digest_so_you_can_tell_them_apart():
    bench = _filled(T.get("data.quality"))
    program = bridge.to_program_graph(bench)
    assert bridge.to_workbench(program).metadata["program_digest"] == program.digest


def test_the_summary_reads_as_a_report_rather_than_a_dump():
    text = bridge.summary(_filled(T.get("software.release")))
    for expected in ("program", "slots", "registry", "effects", "permissions"):
        assert expected in text
