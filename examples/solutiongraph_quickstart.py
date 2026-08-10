"""Compile and search a non-browser Universal Node Graph program."""
from __future__ import annotations

import json

from solutiongraph import (
    BeliefModel,
    Candidate,
    CandidateWeight,
    Compiler,
    Edge,
    GraphInput,
    GraphOutput,
    NodeSpec,
    Port,
    ProgramGraph,
    Registry,
    SearchBudget,
    SearchEngine,
    SearchMode,
    SemanticSlot,
    ValueType,
    sha256_digest,
)

RAW = ValueType("example.raw_document")
TEXT = ValueType("example.normalized_text")
RECORDS = ValueType("example.structured_records")


def implementation(
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
        entrypoint=f"my_nodes:{node_id.rsplit('.', 1)[-1]}",
        capabilities=(capability,),
        effects=effects,
        permissions=permissions,
    )


decode_in = Port("document", RAW)
decode_out = Port("text", TEXT)
extract_in = Port("text", TEXT)
extract_out = Port("records", RECORDS)

nodes = (
    implementation("example.decode.fast", "decode", decode_in, decode_out),
    implementation("example.decode.safe", "decode", decode_in, decode_out),
    implementation("example.extract.rules", "extract", extract_in, extract_out),
    implementation(
        "example.extract.model",
        "extract",
        extract_in,
        extract_out,
        effects=("model.invoke",),
        permissions=("model.invoke",),
    ),
)
registry = Registry(
    id="example.registry",
    version="1.0.0",
    nodes=nodes,
    candidates=tuple(
        Candidate(
            id=f"candidate.{node.id}",
            node_id=node.id,
            node_version=node.version,
            implementation_digest=node.implementation_digest,
        )
        for node in nodes
    ),
)

program = ProgramGraph(
    id="example.document_extraction",
    version="1.0.0",
    task="Extract typed records from an unknown document.",
    success_contract="An independent verifier accepts the schema and evidence.",
    slots=(
        SemanticSlot(
            "decode",
            "Convert the document to normalized text.",
            (decode_in,),
            (decode_out,),
            "Output is non-empty and source-grounded.",
            required_capabilities=("decode",),
        ),
        SemanticSlot(
            "extract",
            "Extract the requested typed fields.",
            (extract_in,),
            (extract_out,),
            "Output satisfies the record schema.",
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

compiler = Compiler()
space = compiler.admit(program, registry)
beliefs = BeliefModel(
    revision="cold-start-v1",
    candidate_weights=(
        CandidateWeight("decode", "candidate.example.decode.safe", 0.8),
        CandidateWeight("extract", "candidate.example.extract.rules", 0.5),
    ),
)
report = SearchEngine().search(
    space,
    beliefs,
    SearchBudget(SearchMode.EXHAUSTIVE, result_limit=4),
)
plan = compiler.compile(program, registry, space, report.proposals[0].selection)

print(json.dumps({
    "candidate_matrix": {
        slot: list(candidates) for slot, candidates in space.choices
    },
    "search_report": report.to_dict(),
    "frozen_plan": plan.to_dict(),
}, indent=2))

