from __future__ import annotations

from dataclasses import replace

import pytest

from solutiongraph import (
    BeliefModel,
    Candidate,
    CandidateWeight,
    Cardinality,
    Compiler,
    Edge,
    EvidenceLedger,
    ForbiddenCombination,
    GraphInput,
    GraphOutput,
    NodeSpec,
    Objective,
    Port,
    ProgramGraph,
    Registry,
    RunReceipt,
    SearchBudget,
    SearchEngine,
    SearchMode,
    SemanticSlot,
    ValidationError,
    ValueType,
    learn_observational_beliefs,
    pareto_front,
    sha256_digest,
)
from solutiongraph.schemas import SCHEMA_NAMES, load_all_schemas

RAW = ValueType("example.raw_document")
TEXT = ValueType("example.normalized_text")
RECORDS = ValueType("example.structured_records")


def node(
    node_id: str,
    capability: str,
    input_port: Port,
    output_port: Port,
    *,
    effects: tuple[str, ...] = (),
    permissions: tuple[str, ...] = (),
) -> NodeSpec:
    return NodeSpec(
        id=node_id,
        version="1.0.0",
        implementation_digest=sha256_digest(node_id),
        inputs=(input_port,),
        outputs=(output_port,),
        runtime="python",
        entrypoint=f"examples:{node_id.rsplit('.', 1)[-1]}",
        capabilities=(capability,),
        effects=effects,
        permissions=permissions,
    )


def fixture() -> tuple[ProgramGraph, Registry]:
    decode_input = Port("document", RAW)
    decode_output = Port("text", TEXT)
    extract_input = Port("text", TEXT)
    extract_output = Port("records", RECORDS)
    nodes = (
        node("example.decode.fast", "decode", decode_input, decode_output),
        node("example.decode.safe", "decode", decode_input, decode_output),
        node("example.extract.rules", "extract", extract_input, extract_output),
        node(
            "example.extract.model",
            "extract",
            extract_input,
            extract_output,
            effects=("model.invoke",),
            permissions=("model.invoke",),
        ),
    )
    candidates = tuple(
        Candidate(
            id=f"candidate.{spec.id}",
            node_id=spec.id,
            node_version=spec.version,
            implementation_digest=spec.implementation_digest,
        )
        for spec in nodes
    )
    program = ProgramGraph(
        id="example.document_extraction",
        version="1.0.0",
        task="Extract typed records from an unknown document.",
        success_contract="The independent verifier accepts the record schema.",
        slots=(
            SemanticSlot(
                id="decode",
                purpose="Convert the source to normalized text.",
                inputs=(decode_input,),
                outputs=(decode_output,),
                success_contract="Text is non-empty and source-grounded.",
                required_capabilities=("decode",),
            ),
            SemanticSlot(
                id="extract",
                purpose="Produce the requested structured fields.",
                inputs=(extract_input,),
                outputs=(extract_output,),
                success_contract="Records satisfy the requested schema.",
                required_capabilities=("extract",),
                allowed_effects=("model.invoke",),
            ),
        ),
        edges=(Edge("decode", "text", "extract", "text"),),
        inputs=(GraphInput("document", RAW, "decode", "document"),),
        outputs=(GraphOutput("records", RECORDS, "extract", "records"),),
        allowed_effects=("model.invoke",),
        granted_permissions=("model.invoke",),
    )
    return program, Registry("example.registry", "1.0.0", nodes, candidates)


def test_compiler_admits_every_compatible_candidate_and_explains_every_rejection():
    program, registry = fixture()
    space = Compiler().admit(program, registry)

    assert len(space.decisions) == len(program.slots) * len(registry.candidates)
    assert len(space.choices_for("decode")) == 2
    assert len(space.choices_for("extract")) == 2
    assert space.route_count_upper_bound == 4
    rejected = [decision for decision in space.decisions if not decision.admitted]
    assert len(rejected) == 4
    assert all(decision.reasons for decision in rejected)


def test_compilation_freezes_exact_versions_bindings_and_a_stable_content_digest():
    program, registry = fixture()
    compiler = Compiler()
    space = compiler.admit(program, registry)
    selection = {
        "decode": "candidate.example.decode.safe",
        "extract": "candidate.example.extract.rules",
    }
    first = compiler.compile(program, registry, space, selection)
    second = compiler.compile(program, registry, space, selection)

    assert first == second
    assert first.digest.startswith("sha256:")
    assert first.topological_order == ("decode", "extract")
    assert [binding.candidate_id for binding in first.bindings] == list(selection.values())


def test_invalid_types_cycles_and_implicit_coercions_are_rejected_before_search():
    program, registry = fixture()
    wrong = replace(program.edges[0], target_port="missing")
    cyclic = replace(
        program,
        edges=(wrong, Edge("extract", "records", "decode", "document")),
    )
    with pytest.raises(ValidationError) as error:
        Compiler().admit(cyclic, registry)
    codes = {diagnostic.code for diagnostic in error.value.diagnostics}
    assert "UNG-EDGE-002" in codes
    assert "UNG-GRAPH-001" in codes


def test_permissions_and_effects_are_hard_admission_rules_not_optimizer_preferences():
    program, registry = fixture()
    restricted = replace(program, granted_permissions=())
    space = Compiler().admit(restricted, registry)
    assert space.choices_for("extract") == ("candidate.example.extract.rules",)
    rejected = next(
        decision for decision in space.decisions
        if decision.slot_id == "extract"
        and decision.candidate_id == "candidate.example.extract.model"
    )
    assert any("permissions not granted" in reason for reason in rejected.reasons)


def test_forbidden_combinations_are_checked_by_search_and_compilation():
    program, registry = fixture()
    compiler = Compiler()
    constraint = ForbiddenCombination(
        "example.incompatible_pair",
        (
            ("decode", "candidate.example.decode.fast"),
            ("extract", "candidate.example.extract.model"),
        ),
        "This deployment pair exceeds the memory envelope.",
    )
    space = compiler.admit(program, registry, constraints=(constraint,))
    routes = list(SearchEngine().iter_exhaustive(space))
    assert len(routes) == 3
    with pytest.raises(ValidationError, match="exceeds the memory envelope"):
        compiler.compile(program, registry, space, dict(constraint.assignments))


def test_anytime_search_reports_what_it_evaluated_skipped_and_left_unvisited():
    program, registry = fixture()
    space = Compiler().admit(program, registry)
    beliefs = BeliefModel(
        revision="prior-v1",
        candidate_weights=(
            CandidateWeight("decode", "candidate.example.decode.safe", 2.0),
            CandidateWeight("extract", "candidate.example.extract.rules", 1.0),
        ),
    )
    engine = SearchEngine()
    prior = engine.search(space, beliefs, SearchBudget(SearchMode.PRIOR, result_limit=1))
    exhaustive = engine.search(
        space,
        beliefs,
        SearchBudget(SearchMode.EXHAUSTIVE, evaluation_limit=None, result_limit=4),
    )

    assert prior.proposals[0].selection == {
        "decode": "candidate.example.decode.safe",
        "extract": "candidate.example.extract.rules",
    }
    assert prior.heuristic_skipped_routes == 3
    assert not prior.optimality_proven
    assert exhaustive.evaluated_routes == 4
    assert exhaustive.evaluation_coverage == 1.0
    assert exhaustive.complete and exhaustive.optimality_proven


def test_exhaustive_search_has_no_implicit_cap_but_an_explicit_budget_stops_it():
    program, registry = fixture()
    space = Compiler().admit(program, registry)
    report = SearchEngine().search(
        space,
        budget=SearchBudget(SearchMode.EXHAUSTIVE, evaluation_limit=2, result_limit=2),
    )
    assert report.evaluated_routes == 2
    assert report.unvisited_routes == 2
    assert report.evaluation_limit == 2
    assert not report.complete


def receipt(
    receipt_id: str,
    plan: str,
    assignments: dict[str, str],
    accepted: bool,
    quality: float,
    cost: float,
) -> RunReceipt:
    return RunReceipt(
        id=receipt_id,
        plan_digest=sha256_digest(plan),
        program_digest=sha256_digest("program"),
        task_case_id="case-1",
        outcome="accepted" if accepted else "rejected",
        accepted=accepted,
        verifier="example.independent_verifier",
        assignments=tuple(assignments.items()),
        metrics={"quality": quality, "cost": cost},
        seed=7,
    )


def test_evidence_is_append_only_pareto_ranked_and_can_fit_observational_priors():
    route_a = {
        "decode": "candidate.example.decode.safe",
        "extract": "candidate.example.extract.rules",
    }
    route_b = {
        "decode": "candidate.example.decode.fast",
        "extract": "candidate.example.extract.model",
    }
    receipts = (
        receipt("receipt.a1", "plan-a", route_a, True, 0.92, 4.0),
        receipt("receipt.a2", "plan-a", route_a, True, 0.94, 4.2),
        receipt("receipt.b1", "plan-b", route_b, False, 0.70, 1.0),
    )
    ledger = EvidenceLedger().append(*receipts)
    with pytest.raises(ValueError, match="globally unique"):
        ledger.append(receipts[0])

    front = pareto_front(
        ledger.aggregates(),
        (Objective("quality", "maximize"), Objective("cost", "minimize")),
    )
    assert {item.plan_digest for item in front} == {
        sha256_digest("plan-a"), sha256_digest("plan-b")
    }
    beliefs = learn_observational_beliefs(receipts, revision="evidence-v1")
    safe = beliefs.candidate_score("decode", "candidate.example.decode.safe")
    fast = beliefs.candidate_score("decode", "candidate.example.decode.fast")
    assert safe > fast


def test_stream_cardinality_is_part_of_the_abi():
    program, registry = fixture()
    stream_node = replace(
        registry.nodes[0],
        outputs=(Port("text", TEXT, Cardinality.STREAM),),
    )
    stream_candidate = replace(
        registry.candidates[0],
        implementation_digest=stream_node.implementation_digest,
    )
    changed_registry = replace(
        registry,
        nodes=(stream_node,) + registry.nodes[1:],
        candidates=(stream_candidate,) + registry.candidates[1:],
    )
    space = Compiler().admit(program, changed_registry)
    assert "candidate.example.decode.fast" not in space.choices_for("decode")


def test_schema_media_type_and_units_are_exact_parts_of_the_wire_type():
    base = ValueType(
        "example.measurement",
        schema_digest=sha256_digest("schema-v1"),
        media_type="application/json",
        units="meters",
    )
    assert base.is_assignable_to(base)
    assert not base.is_assignable_to(replace(base, schema_digest=""))
    assert not base.is_assignable_to(replace(base, media_type="application/cbor"))
    assert not base.is_assignable_to(replace(base, units="feet"))


def test_constraints_can_only_reference_candidates_admitted_for_their_slots():
    program, registry = fixture()
    invalid = ForbiddenCombination(
        "example.invalid_constraint",
        (
            ("decode", "candidate.example.extract.rules"),
            ("extract", "candidate.example.extract.model"),
        ),
        "The first candidate cannot implement the decode obligation.",
    )
    with pytest.raises(ValidationError) as error:
        Compiler().admit(program, registry, constraints=(invalid,))
    assert {item.code for item in error.value.diagnostics} == {"UNG-CONSTRAINT-005"}


def test_beam_search_enforces_its_explicit_evaluation_budget():
    program, registry = fixture()
    space = Compiler().admit(program, registry)
    report = SearchEngine().search(
        space,
        budget=SearchBudget(
            SearchMode.BEAM,
            evaluation_limit=2,
            result_limit=2,
            beam_width=4,
        ),
    )
    assert report.evaluated_routes == 2
    assert report.heuristic_skipped_routes == 2
    assert not report.complete


def test_every_wire_representation_has_a_bundled_strict_json_schema():
    schemas = load_all_schemas()
    assert set(schemas) == set(SCHEMA_NAMES)
    assert schemas["node-spec.schema.json"]["additionalProperties"] is False
    assert schemas["program-graph.schema.json"]["properties"]["model_version"] == {
        "const": "0.1"
    }
    assert schemas["frozen-plan.schema.json"]["required"][0] == "digest"
