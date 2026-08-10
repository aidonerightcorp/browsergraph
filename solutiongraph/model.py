"""Normative, runtime-neutral data model for Universal Node Graph programs.

The model intentionally keeps semantic program meaning, implementation
registry data, candidate admission, frozen executable plans, optimizer beliefs,
and execution evidence as different representations.  The compiler is the only
component allowed to turn one representation into the next.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from math import isfinite, prod
from typing import Any

ID_RE = re.compile(r"^[a-z][a-z0-9_.:/-]*$")
PORT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SEMANTIC_MODEL_VERSION = "0.1"


def canonical_json(value: Any) -> str:
    """Return the single JSON encoding used for identities and plan hashes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_digest(value: Any) -> str:
    """Content-address JSON-compatible data or UTF-8 text."""
    payload = value if isinstance(value, str) else canonical_json(value)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Cardinality(str, Enum):
    ONE = "one"
    OPTIONAL = "optional"
    MANY = "many"
    STREAM = "stream"


class Determinism(str, Enum):
    DETERMINISTIC = "deterministic"
    SEEDED = "seeded"
    RECORDED = "recorded"
    NONDETERMINISTIC = "nondeterministic"


class Idempotency(str, Enum):
    IDEMPOTENT = "idempotent"
    CONDITIONAL = "conditional"
    NON_IDEMPOTENT = "non_idempotent"
    UNKNOWN = "unknown"


class SlotKind(str, Enum):
    ATOMIC = "atomic"
    COMPOSITE = "composite"
    BRANCH = "branch"
    LOOP = "loop"
    MAP = "map"
    REDUCE = "reduce"
    BARRIER = "barrier"


@dataclass(frozen=True)
class ValueType:
    """A language-neutral value identity; schemas are addressed, not inlined."""

    id: str
    version: str = "1"
    schema_digest: str = ""
    media_type: str = "application/json"
    units: str = ""

    def validate(self, path: str = "type") -> list[str]:
        problems: list[str] = []
        if not ID_RE.fullmatch(self.id):
            problems.append(f"{path}.id must be a lowercase namespaced identifier")
        if not self.version.strip():
            problems.append(f"{path}.version must not be empty")
        if self.schema_digest and not DIGEST_RE.fullmatch(self.schema_digest):
            problems.append(f"{path}.schema_digest must be sha256:<64 lowercase hex chars>")
        if not self.media_type.strip():
            problems.append(f"{path}.media_type must not be empty")
        return problems

    def is_assignable_to(self, expected: ValueType) -> bool:
        """Use nominal, versioned typing; conversions require explicit nodes."""
        return (
            self.id == expected.id
            and self.version == expected.version
            and self.schema_digest == expected.schema_digest
            and self.media_type == expected.media_type
            and self.units == expected.units
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "version": self.version,
            "schema_digest": self.schema_digest,
            "media_type": self.media_type,
            "units": self.units,
        }


@dataclass(frozen=True)
class Port:
    """One named typed interface port."""

    name: str
    value_type: ValueType
    cardinality: Cardinality = Cardinality.ONE
    description: str = ""

    @property
    def required(self) -> bool:
        return self.cardinality not in (Cardinality.OPTIONAL,)

    def validate(self, path: str) -> list[str]:
        problems = self.value_type.validate(f"{path}.type")
        if not PORT_RE.fullmatch(self.name):
            problems.append(f"{path}.name must be snake_case")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.value_type.to_dict(),
            "cardinality": self.cardinality.value,
            "description": self.description,
        }


@dataclass(frozen=True)
class ParameterSpec:
    """A serialisable configuration input accepted by a node implementation."""

    name: str
    value_type: str
    required: bool = False
    default: Any = None
    choices: tuple[Any, ...] = ()
    description: str = ""

    def validate(self, path: str) -> list[str]:
        problems: list[str] = []
        if not PORT_RE.fullmatch(self.name):
            problems.append(f"{path}.name must be snake_case")
        if not self.value_type.strip():
            problems.append(f"{path}.value_type must not be empty")
        if self.choices and self.default is not None and self.default not in self.choices:
            problems.append(f"{path}.default must be one of choices")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value_type": self.value_type,
            "required": self.required,
            "default": self.default,
            "choices": list(self.choices),
            "description": self.description,
        }


@dataclass(frozen=True)
class FailureMode:
    code: str
    retryable: bool
    description: str = ""

    def validate(self, path: str) -> list[str]:
        if not ID_RE.fullmatch(self.code):
            return [f"{path}.code must be a lowercase namespaced identifier"]
        return []

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "retryable": self.retryable,
            "description": self.description,
        }


@dataclass(frozen=True)
class ResourceClaim:
    kind: str
    amount: float
    unit: str
    hard: bool = False

    def validate(self, path: str) -> list[str]:
        problems: list[str] = []
        if not ID_RE.fullmatch(self.kind):
            problems.append(f"{path}.kind must be a lowercase namespaced identifier")
        if not isfinite(self.amount) or self.amount < 0:
            problems.append(f"{path}.amount must be finite and non-negative")
        if not self.unit.strip():
            problems.append(f"{path}.unit must not be empty")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "amount": self.amount,
            "unit": self.unit,
            "hard": self.hard,
        }


@dataclass(frozen=True)
class NodeSpec:
    """The strict node ABI. It describes behavior but contains no learned score."""

    id: str
    version: str
    implementation_digest: str
    inputs: tuple[Port, ...]
    outputs: tuple[Port, ...]
    runtime: str
    entrypoint: str
    description: str = ""
    parameters: tuple[ParameterSpec, ...] = ()
    capabilities: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    determinism: Determinism = Determinism.DETERMINISTIC
    idempotency: Idempotency = Idempotency.UNKNOWN
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()
    invariants: tuple[str, ...] = ()
    failure_modes: tuple[FailureMode, ...] = ()
    resources: tuple[ResourceClaim, ...] = ()
    verifier: str = ""
    source: str = ""

    def validate(self, path: str = "node") -> list[str]:
        problems: list[str] = []
        if not ID_RE.fullmatch(self.id):
            problems.append(f"{path}.id must be a lowercase namespaced identifier")
        if not self.version.strip():
            problems.append(f"{path}.version must not be empty")
        if not DIGEST_RE.fullmatch(self.implementation_digest):
            problems.append(f"{path}.implementation_digest must be sha256:<64 hex chars>")
        if not self.runtime.strip():
            problems.append(f"{path}.runtime must not be empty")
        if not self.entrypoint.strip():
            problems.append(f"{path}.entrypoint must not be empty")
        for label, ports in (("inputs", self.inputs), ("outputs", self.outputs)):
            names = [port.name for port in ports]
            if len(names) != len(set(names)):
                problems.append(f"{path}.{label} must have unique names")
            for index, port in enumerate(ports):
                problems.extend(port.validate(f"{path}.{label}[{index}]"))
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            problems.append(f"{path}.parameters must have unique names")
        for index, parameter in enumerate(self.parameters):
            problems.extend(parameter.validate(f"{path}.parameters[{index}]"))
        for label, values in (
            ("capabilities", self.capabilities),
            ("effects", self.effects),
            ("permissions", self.permissions),
        ):
            if len(values) != len(set(values)):
                problems.append(f"{path}.{label} must be unique")
            if any(not ID_RE.fullmatch(value) for value in values):
                problems.append(f"{path}.{label} must contain namespaced identifiers")
        failure_codes = [mode.code for mode in self.failure_modes]
        if len(failure_codes) != len(set(failure_codes)):
            problems.append(f"{path}.failure_modes must have unique codes")
        for index, mode in enumerate(self.failure_modes):
            problems.extend(mode.validate(f"{path}.failure_modes[{index}]"))
        for index, claim in enumerate(self.resources):
            problems.extend(claim.validate(f"{path}.resources[{index}]"))
        if self.verifier and not ID_RE.fullmatch(self.verifier):
            problems.append(f"{path}.verifier must be a namespaced identifier")
        return problems

    def port(self, direction: str, name: str) -> Port | None:
        ports = self.inputs if direction == "input" else self.outputs
        return next((port for port in ports if port.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_version": SEMANTIC_MODEL_VERSION,
            "id": self.id,
            "version": self.version,
            "implementation_digest": self.implementation_digest,
            "description": self.description,
            "inputs": [port.to_dict() for port in self.inputs],
            "outputs": [port.to_dict() for port in self.outputs],
            "parameters": [parameter.to_dict() for parameter in self.parameters],
            "runtime": self.runtime,
            "entrypoint": self.entrypoint,
            "capabilities": list(self.capabilities),
            "effects": list(self.effects),
            "permissions": list(self.permissions),
            "determinism": self.determinism.value,
            "idempotency": self.idempotency.value,
            "preconditions": list(self.preconditions),
            "postconditions": list(self.postconditions),
            "invariants": list(self.invariants),
            "failure_modes": [mode.to_dict() for mode in self.failure_modes],
            "resources": [claim.to_dict() for claim in self.resources],
            "verifier": self.verifier,
            "source": self.source,
        }


@dataclass(frozen=True)
class Candidate:
    """One exact parameter binding of one content-addressed node implementation."""

    id: str
    node_id: str
    node_version: str
    implementation_digest: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    deployment: str = ""

    def validate(self, node: NodeSpec | None, path: str = "candidate") -> list[str]:
        problems: list[str] = []
        if not ID_RE.fullmatch(self.id):
            problems.append(f"{path}.id must be a lowercase namespaced identifier")
        if not ID_RE.fullmatch(self.node_id):
            problems.append(f"{path}.node_id must be a lowercase namespaced identifier")
        if not DIGEST_RE.fullmatch(self.implementation_digest):
            problems.append(f"{path}.implementation_digest must be sha256:<64 hex chars>")
        if node is None:
            problems.append(f"{path} references unknown node {self.node_id}@{self.node_version}")
            return problems
        if self.node_version != node.version:
            problems.append(f"{path}.node_version does not match the registry node")
        if self.implementation_digest != node.implementation_digest:
            problems.append(f"{path}.implementation_digest does not match the registry node")
        specs = {parameter.name: parameter for parameter in node.parameters}
        unknown = sorted(set(self.parameters) - set(specs))
        if unknown:
            problems.append(f"{path} binds unknown parameter(s): {', '.join(unknown)}")
        for parameter in node.parameters:
            value = self.parameters.get(parameter.name, parameter.default)
            if parameter.required and value is None:
                problems.append(f"{path} does not bind required parameter {parameter.name}")
            if parameter.choices and value not in parameter.choices:
                problems.append(f"{path}.{parameter.name} is not an admitted choice")
        return problems

    def resolved_parameters(self, node: NodeSpec) -> dict[str, Any]:
        values = {
            parameter.name: parameter.default
            for parameter in node.parameters
            if parameter.default is not None
        }
        values.update(self.parameters)
        return values

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "node_id": self.node_id,
            "node_version": self.node_version,
            "implementation_digest": self.implementation_digest,
            "parameters": dict(self.parameters),
            "deployment": self.deployment,
        }


@dataclass(frozen=True)
class SemanticSlot:
    """One semantic obligation in a program, independent of its implementation."""

    id: str
    purpose: str
    inputs: tuple[Port, ...]
    outputs: tuple[Port, ...]
    success_contract: str
    kind: SlotKind = SlotKind.ATOMIC
    group: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    allowed_effects: tuple[str, ...] = ()
    optional: bool = False
    subgraph_ref: str = ""

    def validate(self, path: str = "slot") -> list[str]:
        problems: list[str] = []
        if not ID_RE.fullmatch(self.id):
            problems.append(f"{path}.id must be a lowercase namespaced identifier")
        if not self.purpose.strip():
            problems.append(f"{path}.purpose must not be empty")
        if not self.success_contract.strip():
            problems.append(f"{path}.success_contract must not be empty")
        for label, ports in (("inputs", self.inputs), ("outputs", self.outputs)):
            names = [port.name for port in ports]
            if len(names) != len(set(names)):
                problems.append(f"{path}.{label} must have unique names")
            for index, port in enumerate(ports):
                problems.extend(port.validate(f"{path}.{label}[{index}]"))
        if self.kind == SlotKind.COMPOSITE and not self.subgraph_ref:
            problems.append(f"{path}.subgraph_ref is required for composite slots")
        if self.kind != SlotKind.COMPOSITE and self.subgraph_ref:
            problems.append(f"{path}.subgraph_ref is valid only for composite slots")
        for label, values in (
            ("group", self.group),
            ("required_capabilities", self.required_capabilities),
            ("allowed_effects", self.allowed_effects),
        ):
            if len(values) != len(set(values)):
                problems.append(f"{path}.{label} must be unique")
            if any(not ID_RE.fullmatch(value) for value in values):
                problems.append(f"{path}.{label} must contain namespaced identifiers")
        return problems

    def port(self, direction: str, name: str) -> Port | None:
        ports = self.inputs if direction == "input" else self.outputs
        return next((port for port in ports if port.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "purpose": self.purpose,
            "inputs": [port.to_dict() for port in self.inputs],
            "outputs": [port.to_dict() for port in self.outputs],
            "success_contract": self.success_contract,
            "kind": self.kind.value,
            "group": list(self.group),
            "required_capabilities": list(self.required_capabilities),
            "allowed_effects": list(self.allowed_effects),
            "optional": self.optional,
            "subgraph_ref": self.subgraph_ref,
        }


@dataclass(frozen=True)
class Edge:
    source_slot: str
    source_port: str
    target_slot: str
    target_port: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source_slot": self.source_slot,
            "source_port": self.source_port,
            "target_slot": self.target_slot,
            "target_port": self.target_port,
        }


@dataclass(frozen=True)
class GraphInput:
    name: str
    value_type: ValueType
    target_slot: str
    target_port: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.value_type.to_dict(),
            "target_slot": self.target_slot,
            "target_port": self.target_port,
        }


@dataclass(frozen=True)
class GraphOutput:
    name: str
    value_type: ValueType
    source_slot: str
    source_port: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.value_type.to_dict(),
            "source_slot": self.source_slot,
            "source_port": self.source_port,
        }


@dataclass(frozen=True)
class ProgramGraph:
    """A semantic graph. Loops and branches are structured slots, not back edges."""

    id: str
    version: str
    task: str
    success_contract: str
    slots: tuple[SemanticSlot, ...]
    edges: tuple[Edge, ...]
    inputs: tuple[GraphInput, ...] = ()
    outputs: tuple[GraphOutput, ...] = ()
    allowed_effects: tuple[str, ...] = ()
    granted_permissions: tuple[str, ...] = ()
    invariants: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_version": SEMANTIC_MODEL_VERSION,
            "id": self.id,
            "version": self.version,
            "task": self.task,
            "success_contract": self.success_contract,
            "slots": [slot.to_dict() for slot in self.slots],
            "edges": [edge.to_dict() for edge in self.edges],
            "inputs": [item.to_dict() for item in self.inputs],
            "outputs": [item.to_dict() for item in self.outputs],
            "allowed_effects": list(self.allowed_effects),
            "granted_permissions": list(self.granted_permissions),
            "invariants": list(self.invariants),
        }


@dataclass(frozen=True)
class Registry:
    """A versioned collection of definitions and concrete bindings."""

    id: str
    version: str
    nodes: tuple[NodeSpec, ...]
    candidates: tuple[Candidate, ...]

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_dict())

    def node_map(self) -> dict[tuple[str, str], NodeSpec]:
        return {(node.id, node.version): node for node in self.nodes}

    def candidate_map(self) -> dict[str, Candidate]:
        return {candidate.id: candidate for candidate in self.candidates}

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_version": SEMANTIC_MODEL_VERSION,
            "id": self.id,
            "version": self.version,
            "nodes": [node.to_dict() for node in self.nodes],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


@dataclass(frozen=True)
class AdmissionDecision:
    slot_id: str
    candidate_id: str
    admitted: bool
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "candidate_id": self.candidate_id,
            "admitted": self.admitted,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ForbiddenCombination:
    """A serialisable n-ary configuration constraint used during search."""

    id: str
    assignments: tuple[tuple[str, str], ...]
    reason: str

    def matches(self, selection: Mapping[str, str]) -> bool:
        expected = dict(self.assignments)
        return all(selection.get(slot) == candidate for slot, candidate in expected.items())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "assignments": [
                {"slot_id": slot, "candidate_id": candidate}
                for slot, candidate in self.assignments
            ],
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AdmittedSpace:
    """Compiler-produced candidate matrix; every rejection remains inspectable."""

    program_digest: str
    registry_digest: str
    choices: tuple[tuple[str, tuple[str, ...]], ...]
    decisions: tuple[AdmissionDecision, ...]
    constraints: tuple[ForbiddenCombination, ...] = ()

    @property
    def route_count_upper_bound(self) -> int:
        return prod(len(candidates) for _, candidates in self.choices)

    def choices_for(self, slot_id: str) -> tuple[str, ...]:
        return dict(self.choices).get(slot_id, ())

    def to_dict(self) -> dict[str, Any]:
        return {
            "program_digest": self.program_digest,
            "registry_digest": self.registry_digest,
            "choices": [
                {"slot_id": slot, "candidate_ids": list(candidates)}
                for slot, candidates in self.choices
            ],
            "decisions": [decision.to_dict() for decision in self.decisions],
            "constraints": [constraint.to_dict() for constraint in self.constraints],
        }


@dataclass(frozen=True)
class PlanBinding:
    slot_id: str
    candidate_id: str
    node_id: str
    node_version: str
    implementation_digest: str
    parameters: tuple[tuple[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "candidate_id": self.candidate_id,
            "node_id": self.node_id,
            "node_version": self.node_version,
            "implementation_digest": self.implementation_digest,
            "parameters": dict(self.parameters),
        }


@dataclass(frozen=True)
class FrozenPlan:
    """Content-addressed executable intent with no mutable optimizer state."""

    digest: str
    program_id: str
    program_version: str
    program_digest: str
    registry_digest: str
    topological_order: tuple[str, ...]
    bindings: tuple[PlanBinding, ...]
    edges: tuple[Edge, ...]

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "model_version": SEMANTIC_MODEL_VERSION,
            "program_id": self.program_id,
            "program_version": self.program_version,
            "program_digest": self.program_digest,
            "registry_digest": self.registry_digest,
            "topological_order": list(self.topological_order),
            "bindings": [binding.to_dict() for binding in self.bindings],
            "edges": [edge.to_dict() for edge in self.edges],
        }

    def to_dict(self) -> dict[str, Any]:
        return {"digest": self.digest, **self.unsigned_dict()}
