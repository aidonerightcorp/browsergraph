"""Turn a browsergraph workbench into a solutiongraph program.

Two models now live in this repo. `WorkbenchDefinition` is the friendly one you
write by hand: stages, candidates, a few typed ports. `ProgramGraph` is the
strict one: versioned types, port cardinality, content-addressed nodes, slot
kinds for branch and map and reduce.

Both are good at different jobs, and having both is only a problem if they never
meet. That is what this file is for. You write a workbench, you get a program
graph, and the strict compiler checks it. Nothing has to be written twice.

The translation is honest about what it cannot know:

* A workbench type is a bare name like `Records`. A solutiongraph type is a name
  plus a version plus a media type. We fill in version "1" and JSON, and say so
  here rather than pretending the extra precision came from somewhere.
* A workbench node has no implementation digest, because it does not point at
  code. We hash the manifest instead. That is a real digest of a real thing —
  it just answers "did the description change", not "did the code change".
* Determinism in a workbench is a yes/no flag. Solutiongraph has four levels.
  A `False` flag becomes `nondeterministic`, which is the safe reading: it
  claims less than the other three.

Going the other way is lossy on purpose. A program graph can say things a
workbench cannot hold, so `to_workbench` keeps what fits and drops the rest.
Use it to view or draw a program graph, not to round-trip one.
"""
from __future__ import annotations

import hashlib
import json

from browsergraph.manifest import NodeManifest, PortSpec
from browsergraph.workbench import (
    Edge as WorkbenchEdge,
)
from browsergraph.workbench import (
    NodeCandidate,
    StageDefinition,
    WorkbenchDefinition,
)
from solutiongraph.model import (
    Candidate,
    Cardinality,
    Determinism,
    Edge,
    Idempotency,
    NodeSpec,
    ParameterSpec,
    Port,
    ProgramGraph,
    Registry,
    SemanticSlot,
    SlotKind,
    ValueType,
)

#: Solutiongraph wants a `sha256:<64 hex>` digest for every implementation. A
#: workbench manifest does not point at code, so there is nothing to hash but
#: the description. We hash that. It is a truthful digest of the manifest, and
#: it changes when the manifest changes, which is what a caller actually uses it
#: for here.
DIGEST_PREFIX = "sha256:"


def _digest(payload) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return DIGEST_PREFIX + hashlib.sha256(blob.encode()).hexdigest()


def _identifier(name: str) -> str:
    """Make a name solutiongraph will accept: lowercase, dotted, no spaces."""
    cleaned = "".join(c.lower() if (c.isalnum() or c in "._") else "_"
                      for c in name.strip())
    cleaned = cleaned.strip("._") or "unnamed"
    # Solutiongraph ids must be namespaced. A bare `records` becomes
    # `type.records` rather than being rejected, because a caller who wrote a
    # one-word type meant a type, not a mistake.
    return cleaned if "." in cleaned else f"type.{cleaned}"


def value_type(type_name: str, units: str = "") -> ValueType:
    """A workbench type name as a solutiongraph value type.

    Everything here except the name is a default we are choosing, not something
    the workbench told us. Version "1" and JSON are the least surprising
    choices, and both are visible in the output so nobody has to guess.
    """
    return ValueType(id=_identifier(type_name or "any"), version="1",
                     media_type="application/json", units=units)


def _port(spec: PortSpec, *, outgoing: bool = False) -> Port:
    return Port(
        name=spec.name or ("out" if outgoing else "in"),
        value_type=value_type(spec.type, spec.units),
        # A workbench port is either required or not. `required=False` maps to
        # OPTIONAL. Nothing in a workbench says "many", so we never claim it.
        cardinality=Cardinality.ONE if spec.required else Cardinality.OPTIONAL,
        description=spec.description,
    )


def _determinism(manifest: NodeManifest) -> Determinism:
    """A yes/no flag into a four-level scale, erring toward claiming less."""
    flag = (manifest.runtime or {}).get("deterministic")
    if flag is False:
        return Determinism.NONDETERMINISTIC
    if flag is True:
        return Determinism.DETERMINISTIC
    return Determinism.NONDETERMINISTIC


def _idempotency(manifest: NodeManifest) -> Idempotency:
    """Unknown unless the node says. A node with no effects is idempotent.

    That is not a guess. A step that changes nothing outside itself can be run
    twice with the same result, by definition of having no effects.
    """
    stated = (manifest.runtime or {}).get("idempotency")
    if stated in {i.value for i in Idempotency}:
        return Idempotency(stated)
    return Idempotency.IDEMPOTENT if not manifest.effects else Idempotency.UNKNOWN


def node_spec(manifest: NodeManifest) -> NodeSpec:
    """One manifest as a strict node spec."""
    return NodeSpec(
        id=_identifier(manifest.id) if "." in manifest.id else manifest.id,
        version=manifest.version or "1.0",
        implementation_digest=_digest(manifest.to_dict()),
        inputs=tuple(_port(p) for p in manifest.inputs),
        outputs=tuple(_port(p, outgoing=True) for p in manifest.outputs),
        # A workbench does not record how a node is launched. Saying "python"
        # and naming the node is honest about that: it is where the thing lives
        # in this repo, not a claim about a packaged entrypoint elsewhere.
        runtime=str((manifest.runtime or {}).get("engine") or "python"),
        entrypoint=manifest.source or manifest.id,
        description=manifest.description,
        parameters=tuple(
            ParameterSpec(name=p.name, value_type=value_type(p.type),
                          required=p.required, default=p.default,
                          enum=tuple(str(c) for c in p.choices),
                          description=p.description)
            for p in manifest.parameters),
        capabilities=tuple(manifest.capabilities),
        effects=tuple(manifest.effects),
        permissions=tuple(manifest.permissions),
        determinism=_determinism(manifest),
        idempotency=_idempotency(manifest),
        source=manifest.source,
    )


def _slot_kind(stage: StageDefinition) -> SlotKind:
    """The workbench kind, as the strict model's kind.

    This used to always answer atomic-or-composite, because a workbench had no
    way to say anything else. It does now, so map and branch cross over as
    themselves. Reduce and loop are still never returned — a workbench cannot
    express them, and claiming a shape the source could not say would make the
    strict model less trustworthy rather than more capable.
    """
    if stage.substages:
        return SlotKind.COMPOSITE
    return {"map": SlotKind.MAP,
            "branch": SlotKind.BRANCH}.get(stage.kind, SlotKind.ATOMIC)


def semantic_slot(stage: StageDefinition) -> SemanticSlot:
    return SemanticSlot(
        id=_identifier(stage.id) if "." in stage.id else f"slot.{stage.id}",
        purpose=stage.description or stage.name or stage.id,
        inputs=tuple(_port(p) for p in stage.inputs),
        outputs=tuple(_port(p, outgoing=True) for p in stage.outputs),
        # Solutiongraph insists every slot says how you know it worked. A
        # workbench stage may leave `success` empty, and an empty contract is
        # refused — rightly. We write down the weakest honest one instead of
        # inventing a specific claim.
        success_contract=stage.success or (
            f"{stage.name or stage.id} produced its declared output"),
        kind=_slot_kind(stage),
        required_capabilities=tuple(stage.required_capabilities),
        optional=stage.optional,
    )


def _candidate(candidate: NodeCandidate, manifest: NodeManifest) -> Candidate:
    return Candidate(
        id=_identifier(candidate.id) if "." in candidate.id else f"cand.{candidate.id}",
        node_id=_identifier(manifest.id) if "." in manifest.id else manifest.id,
        node_version=manifest.version or "1.0",
        implementation_digest=_digest(manifest.to_dict()),
        parameters=dict(candidate.params),
    )


def to_registry(bench: WorkbenchDefinition) -> Registry:
    """Every node and binding in the workbench, as a solutiongraph registry."""
    nodes = tuple(node_spec(m) for m in bench.nodes)
    by_id = bench.nodes_by_id
    candidates = tuple(_candidate(c, by_id[c.node_id])
                       for c in bench.candidates if c.node_id in by_id)
    return Registry(id=_identifier(bench.title or "registry"),
                    version="1", nodes=nodes, candidates=candidates)


def to_program_graph(bench: WorkbenchDefinition) -> ProgramGraph:
    """The workbench as a strict program graph.

    Uses `leaf_stages` and `wiring()`, so a workbench that never declared edges
    still comes out as a real graph — the chain it always meant.
    """
    leaves = bench.leaf_stages
    slots = tuple(semantic_slot(s) for s in leaves)
    ids = {s.id: slot.id for s, slot in zip(leaves, slots, strict=True)}

    edges = []
    for edge in bench.wiring():
        if edge.source not in ids or edge.target not in ids:
            continue
        source = next(s for s in leaves if s.id == edge.source)
        target = next(s for s in leaves if s.id == edge.target)
        from_port = source.port(edge.from_port, outgoing=True)
        to_port = target.port(edge.to_port, outgoing=False)
        edges.append(Edge(
            source_slot=ids[edge.source],
            source_port=(from_port.name if from_port else "out"),
            target_slot=ids[edge.target],
            target_port=(to_port.name if to_port else "in")))

    return ProgramGraph(
        id=_identifier(bench.title or "program"),
        version="1",
        task=bench.task or bench.title or "unnamed task",
        success_contract=bench.success or "every slot met its own contract",
        slots=slots,
        edges=tuple(edges),
        allowed_effects=tuple(sorted({e for n in bench.nodes for e in n.effects})),
        granted_permissions=tuple(sorted({p for n in bench.nodes
                                          for p in n.permissions})),
    )


def to_workbench(program: ProgramGraph, registry: Registry | None = None
                 ) -> WorkbenchDefinition:
    """A program graph as a workbench, so the pictures work on it too.

    Lossy on purpose. Cardinality, versioned types, digests, idempotency and
    slot kinds have nowhere to live in a workbench, so they are dropped. What
    survives is enough to draw the graph and read its shape.
    """
    stages = tuple(
        StageDefinition(
            id=slot.id.split(".", 1)[-1],
            name=slot.purpose[:60] or slot.id,
            description=slot.purpose,
            success=slot.success_contract,
            optional=slot.optional,
            required_capabilities=tuple(slot.required_capabilities),
            inputs=tuple(PortSpec(p.name, p.value_type.id.split(".", 1)[-1],
                                  required=p.cardinality is not Cardinality.OPTIONAL)
                         for p in slot.inputs),
            outputs=tuple(PortSpec(p.name, p.value_type.id.split(".", 1)[-1])
                          for p in slot.outputs))
        for slot in program.slots)

    edges = tuple(WorkbenchEdge(source=e.source_slot.split(".", 1)[-1],
                                target=e.target_slot.split(".", 1)[-1],
                                from_port=e.source_port, to_port=e.target_port)
                  for e in program.edges)

    return WorkbenchDefinition(title=program.id, task=program.task,
                               success=program.success_contract,
                               stages=stages, edges=edges,
                               metadata={"program_digest": program.digest})


def belief_model(bench: WorkbenchDefinition, evidence, *,
                 context=("global",), minimum: int = 10):
    """browsergraph evidence as a solutiongraph `BeliefModel`.

    The strict core shipped a belief model with *interaction* weights and
    nothing outside its own package ever built one, so the most expressive part
    of the merge sat unused. This makes one from measurements: a log weight per
    candidate from its posterior, and a log weight per pair from the measured
    contrast between routes holding both and routes holding one.

    Log weights because `incremental_score` adds them, and adding logs is
    multiplying — which is how route quality composes everywhere else here. A
    pair that halves the odds contributes `log(0.5)`, and that lands as a
    halving rather than as a subtraction of an arbitrary constant.
    """
    import math

    from browsergraph.evidence import pair_effects
    from solutiongraph.search import BeliefModel, CandidateWeight, InteractionWeight

    def as_log(value: float) -> float:
        return math.log(max(value, 1e-6))

    stage_of = {cid: stage.id for stage in bench.leaf_stages
                for cid in stage.candidates}

    unary = []
    for stage in bench.leaf_stages:
        for cid in stage.candidates:
            posterior = evidence.posterior(cid, context)
            if not posterior.runs:
                continue
            unary.append(CandidateWeight(
                slot_id=f"slot.{stage.id}", candidate_id=cid,
                log_weight=as_log(posterior.rate),
                evidence_count=posterior.runs,
                uncertainty=posterior.spread))

    pairwise = []
    for pair, factor in pair_effects(evidence, minimum=minimum).items():
        left, right = sorted(pair)
        if left not in stage_of or right not in stage_of:
            continue
        pairwise.append(InteractionWeight(
            left_slot=f"slot.{stage_of[left]}", left_candidate=left,
            right_slot=f"slot.{stage_of[right]}", right_candidate=right,
            log_weight=as_log(factor), evidence_count=minimum))

    return BeliefModel(revision=f"from-evidence-{len(unary)}u-{len(pairwise)}p",
                       candidate_weights=tuple(unary),
                       interaction_weights=tuple(pairwise))


def check(bench: WorkbenchDefinition) -> list[str]:
    """Run the strict compiler over a workbench and say what it complains about.

    The point of the merge, in one function. You keep writing the easy model.
    The strict one still gets to object, and it objects with a code and a path
    rather than a sentence, so a harness can act on the answer.
    """
    from solutiongraph.compiler import Compiler
    from solutiongraph.errors import ValidationError

    compiler = Compiler()
    program = to_program_graph(bench)
    registry = to_registry(bench)

    found = [*compiler.validate_program(program),
             *compiler.validate_registry(registry)]
    if not found:
        try:
            compiler.admit(program, registry)
        except ValidationError as exc:
            found.extend(exc.diagnostics)
    return [f"{d.code} {d.path}: {d.message}".strip() for d in found]


def admitted(bench: WorkbenchDefinition):
    """What the strict compiler would allow in each slot, with reasons.

    Returns solutiongraph's `AdmittedSpace`. Useful because it says *why* a
    candidate was refused, one reason per candidate per slot, instead of just
    leaving it out of a list.
    """
    from solutiongraph.compiler import Compiler

    return Compiler().admit(to_program_graph(bench), to_registry(bench))


def summary(bench: WorkbenchDefinition) -> str:
    """A short readable report of what the workbench looks like when made strict."""
    program = to_program_graph(bench)
    registry = to_registry(bench)
    effectful = [s.id for s in program.slots
                 if any(n.effects for n in registry.nodes)]
    lines = [
        f"program      {program.id}  digest {program.digest[:19]}…",
        f"slots        {len(program.slots)}  edges {len(program.edges)}",
        f"registry     {len(registry.nodes)} nodes, {len(registry.candidates)} candidates",
        f"effects      {', '.join(program.allowed_effects) or 'none declared'}",
        f"permissions  {', '.join(program.granted_permissions) or 'none declared'}",
    ]
    if effectful:
        lines.append(f"kinds        {', '.join(sorted({s.kind.value for s in program.slots}))}")
    return "\n".join(lines)
