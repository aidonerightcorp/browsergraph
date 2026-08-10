"""Strict admission and compilation for domain-neutral graph programs."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from solutiongraph.errors import Diagnostic, ValidationError
from solutiongraph.model import (
    ID_RE,
    PORT_RE,
    AdmissionDecision,
    AdmittedSpace,
    Edge,
    ForbiddenCombination,
    FrozenPlan,
    GraphInput,
    GraphOutput,
    NodeSpec,
    PlanBinding,
    ProgramGraph,
    Registry,
    SemanticSlot,
    sha256_digest,
)


def _duplicates(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


class Compiler:
    """Validate semantic meaning first, then admit and bind implementations."""

    def validate_program(self, program: ProgramGraph) -> tuple[Diagnostic, ...]:
        diagnostics: list[Diagnostic] = []
        if not program.task.strip() or not program.success_contract.strip():
            diagnostics.append(Diagnostic(
                "UNG-PROGRAM-001",
                "program task and success_contract are required",
                "program",
            ))
        if not program.id or not program.version.strip():
            diagnostics.append(Diagnostic(
                "UNG-PROGRAM-003", "program id and version are required", "program"))
        elif not ID_RE.fullmatch(program.id):
            diagnostics.append(Diagnostic(
                "UNG-PROGRAM-004",
                "program id must be a lowercase namespaced identifier",
                "program.id",
            ))
        for label, values in (
            ("allowed_effects", program.allowed_effects),
            ("granted_permissions", program.granted_permissions),
        ):
            if len(values) != len(set(values)):
                diagnostics.append(Diagnostic(
                    "UNG-PROGRAM-005", f"{label} must be unique", f"program.{label}"))
            if any(not ID_RE.fullmatch(value) for value in values):
                diagnostics.append(Diagnostic(
                    "UNG-PROGRAM-006",
                    f"{label} must contain namespaced identifiers",
                    f"program.{label}",
                ))
        slot_ids = [slot.id for slot in program.slots]
        for duplicate in _duplicates(slot_ids):
            diagnostics.append(Diagnostic(
                "UNG-PROGRAM-002", f"duplicate slot id {duplicate}", "program.slots"))
        slot_map = {slot.id: slot for slot in program.slots}
        for index, slot in enumerate(program.slots):
            diagnostics.extend(
                Diagnostic("UNG-SLOT-001", problem, f"program.slots[{index}]")
                for problem in slot.validate(f"program.slots[{index}]")
            )

        producers: dict[tuple[str, str], list[str]] = {}
        for index, edge in enumerate(program.edges):
            path = f"program.edges[{index}]"
            source = slot_map.get(edge.source_slot)
            target = slot_map.get(edge.target_slot)
            if source is None or target is None:
                diagnostics.append(Diagnostic(
                    "UNG-EDGE-001", "edge references an unknown slot", path))
                continue
            source_port = source.port("output", edge.source_port)
            target_port = target.port("input", edge.target_port)
            if source_port is None or target_port is None:
                diagnostics.append(Diagnostic(
                    "UNG-EDGE-002", "edge references an unknown source or target port", path))
                continue
            if not source_port.value_type.is_assignable_to(target_port.value_type):
                diagnostics.append(Diagnostic(
                    "UNG-TYPE-001",
                    f"{source_port.value_type.id}@{source_port.value_type.version} is not "
                    f"assignable to {target_port.value_type.id}@{target_port.value_type.version}",
                    path,
                    "Insert an explicit adapter slot; the compiler never coerces values.",
                ))
            producers.setdefault((edge.target_slot, edge.target_port), []).append(path)

        input_names = [item.name for item in program.inputs]
        for duplicate in _duplicates(input_names):
            diagnostics.append(Diagnostic(
                "UNG-INPUT-001", f"duplicate graph input {duplicate}", "program.inputs"))
        for index, graph_input in enumerate(program.inputs):
            path = f"program.inputs[{index}]"
            if not PORT_RE.fullmatch(graph_input.name):
                diagnostics.append(Diagnostic(
                    "UNG-INPUT-004", "graph input name must be snake_case", path))
            diagnostics.extend(
                Diagnostic("UNG-TYPE-004", problem, path)
                for problem in graph_input.value_type.validate(f"{path}.type")
            )
            self._validate_graph_input(graph_input, slot_map, producers, diagnostics, path)

        output_names = [item.name for item in program.outputs]
        for duplicate in _duplicates(output_names):
            diagnostics.append(Diagnostic(
                "UNG-OUTPUT-001", f"duplicate graph output {duplicate}", "program.outputs"))
        for index, graph_output in enumerate(program.outputs):
            path = f"program.outputs[{index}]"
            if not PORT_RE.fullmatch(graph_output.name):
                diagnostics.append(Diagnostic(
                    "UNG-OUTPUT-004", "graph output name must be snake_case", path))
            diagnostics.extend(
                Diagnostic("UNG-TYPE-005", problem, path)
                for problem in graph_output.value_type.validate(f"{path}.type")
            )
            self._validate_graph_output(
                graph_output, slot_map, diagnostics, path
            )

        for slot in program.slots:
            for port in slot.inputs:
                count = len(producers.get((slot.id, port.name), ()))
                if port.required and count == 0:
                    diagnostics.append(Diagnostic(
                        "UNG-PORT-001",
                        f"required input {slot.id}.{port.name} has no producer",
                        f"program.slots.{slot.id}.inputs.{port.name}",
                    ))
                if count > 1 and port.cardinality.value not in ("many", "stream"):
                    diagnostics.append(Diagnostic(
                        "UNG-PORT-002",
                        f"input {slot.id}.{port.name} has {count} producers",
                        f"program.slots.{slot.id}.inputs.{port.name}",
                        "Use a many/stream port or insert an explicit merge node.",
                    ))

        order = self._topological_order(program.slots, program.edges)
        if len(order) != len(program.slots):
            diagnostics.append(Diagnostic(
                "UNG-GRAPH-001",
                "semantic graph contains a cycle",
                "program.edges",
                "Represent iteration as a loop/composite slot with a nested graph.",
            ))
        return tuple(sorted(diagnostics))

    def _validate_graph_input(
        self,
        graph_input: GraphInput,
        slot_map: dict[str, SemanticSlot],
        producers: dict[tuple[str, str], list[str]],
        diagnostics: list[Diagnostic],
        path: str,
    ) -> None:
        target = slot_map.get(graph_input.target_slot)
        if target is None:
            diagnostics.append(Diagnostic(
                "UNG-INPUT-002", "graph input targets an unknown slot", path))
            return
        target_port = target.port("input", graph_input.target_port)
        if target_port is None:
            diagnostics.append(Diagnostic(
                "UNG-INPUT-003", "graph input targets an unknown port", path))
            return
        if not graph_input.value_type.is_assignable_to(target_port.value_type):
            diagnostics.append(Diagnostic(
                "UNG-TYPE-002", "graph input type is not assignable to target port", path))
        producers.setdefault(
            (graph_input.target_slot, graph_input.target_port), []).append(path)

    def _validate_graph_output(
        self,
        graph_output: GraphOutput,
        slot_map: dict[str, SemanticSlot],
        diagnostics: list[Diagnostic],
        path: str,
    ) -> None:
        source = slot_map.get(graph_output.source_slot)
        if source is None:
            diagnostics.append(Diagnostic(
                "UNG-OUTPUT-002", "graph output references an unknown slot", path))
            return
        source_port = source.port("output", graph_output.source_port)
        if source_port is None:
            diagnostics.append(Diagnostic(
                "UNG-OUTPUT-003", "graph output references an unknown port", path))
            return
        if not source_port.value_type.is_assignable_to(graph_output.value_type):
            diagnostics.append(Diagnostic(
                "UNG-TYPE-003", "source port type is not assignable to graph output", path))

    def validate_registry(self, registry: Registry) -> tuple[Diagnostic, ...]:
        diagnostics: list[Diagnostic] = []
        if not ID_RE.fullmatch(registry.id) or not registry.version.strip():
            diagnostics.append(Diagnostic(
                "UNG-REGISTRY-003",
                "registry id must be namespaced and version must not be empty",
                "registry",
            ))
        node_keys = [(node.id, node.version) for node in registry.nodes]
        for duplicate in sorted({key for key in node_keys if node_keys.count(key) > 1}):
            diagnostics.append(Diagnostic(
                "UNG-REGISTRY-001",
                f"duplicate node {duplicate[0]}@{duplicate[1]}",
                "registry.nodes",
            ))
        node_map = registry.node_map()
        for index, node in enumerate(registry.nodes):
            diagnostics.extend(
                Diagnostic("UNG-NODE-001", problem, f"registry.nodes[{index}]")
                for problem in node.validate(f"registry.nodes[{index}]")
            )
        candidate_ids = [candidate.id for candidate in registry.candidates]
        for duplicate in _duplicates(candidate_ids):
            diagnostics.append(Diagnostic(
                "UNG-REGISTRY-002",
                f"duplicate candidate id {duplicate}",
                "registry.candidates",
            ))
        for index, candidate in enumerate(registry.candidates):
            node = node_map.get((candidate.node_id, candidate.node_version))
            diagnostics.extend(
                Diagnostic("UNG-CANDIDATE-001", problem, f"registry.candidates[{index}]")
                for problem in candidate.validate(node, f"registry.candidates[{index}]")
            )
        return tuple(sorted(diagnostics))

    def admit(
        self,
        program: ProgramGraph,
        registry: Registry,
        *,
        constraints: tuple[ForbiddenCombination, ...] = (),
    ) -> AdmittedSpace:
        """Evaluate every registry candidate for every semantic slot."""
        diagnostics = (*self.validate_program(program), *self.validate_registry(registry))
        if diagnostics:
            raise ValidationError("cannot admit candidates", diagnostics)

        node_map = registry.node_map()
        decisions: list[AdmissionDecision] = []
        choices: list[tuple[str, tuple[str, ...]]] = []
        for slot in program.slots:
            admitted: list[str] = []
            for candidate in registry.candidates:
                node = node_map[(candidate.node_id, candidate.node_version)]
                reasons = self._implementation_problems(program, slot, node)
                is_admitted = not reasons
                decisions.append(AdmissionDecision(
                    slot_id=slot.id,
                    candidate_id=candidate.id,
                    admitted=is_admitted,
                    reasons=tuple(reasons),
                ))
                if is_admitted:
                    admitted.append(candidate.id)
            choices.append((slot.id, tuple(admitted)))

        empty = [slot for slot, candidates in choices if not candidates]
        if empty:
            rejected = [Diagnostic(
                "UNG-ADMISSION-001",
                f"slot has no compatible candidate after checking the full registry: {slot}",
                f"program.slots.{slot}",
            ) for slot in empty]
            raise ValidationError("candidate admission produced an unrunnable graph", rejected)

        self._validate_constraints(program, registry, constraints, dict(choices))
        return AdmittedSpace(
            program_digest=program.digest,
            registry_digest=registry.digest,
            choices=tuple(choices),
            decisions=tuple(decisions),
            constraints=constraints,
        )

    def _implementation_problems(
        self, program: ProgramGraph, slot: SemanticSlot, node: NodeSpec
    ) -> list[str]:
        reasons: list[str] = []
        missing = sorted(set(slot.required_capabilities) - set(node.capabilities))
        if missing:
            reasons.append("missing capabilities: " + ", ".join(missing))
        disallowed_slot = sorted(set(node.effects) - set(slot.allowed_effects))
        if disallowed_slot:
            reasons.append("effects not allowed by slot: " + ", ".join(disallowed_slot))
        disallowed_program = sorted(set(node.effects) - set(program.allowed_effects))
        if disallowed_program:
            reasons.append("effects not allowed by program: " + ", ".join(disallowed_program))
        missing_permissions = sorted(set(node.permissions) - set(program.granted_permissions))
        if missing_permissions:
            reasons.append("permissions not granted: " + ", ".join(missing_permissions))

        slot_inputs = {port.name: port for port in slot.inputs}
        node_inputs = {port.name: port for port in node.inputs}
        for name, required in slot_inputs.items():
            provided = node_inputs.get(name)
            if provided is None:
                reasons.append(f"missing input port {name}")
            elif not required.value_type.is_assignable_to(provided.value_type):
                reasons.append(f"input port {name} has an incompatible type")
            elif required.cardinality != provided.cardinality:
                reasons.append(f"input port {name} has incompatible cardinality")
        extra_required_inputs = sorted(
            name for name, port in node_inputs.items()
            if port.required and name not in slot_inputs
        )
        if extra_required_inputs:
            reasons.append("implementation requires undeclared inputs: " +
                           ", ".join(extra_required_inputs))

        node_outputs = {port.name: port for port in node.outputs}
        for name, promised in {port.name: port for port in slot.outputs}.items():
            actual = node_outputs.get(name)
            if actual is None:
                reasons.append(f"missing output port {name}")
            elif not actual.value_type.is_assignable_to(promised.value_type):
                reasons.append(f"output port {name} has an incompatible type")
            elif promised.cardinality != actual.cardinality:
                reasons.append(f"output port {name} has incompatible cardinality")
        return reasons

    def _validate_constraints(
        self,
        program: ProgramGraph,
        registry: Registry,
        constraints: tuple[ForbiddenCombination, ...],
        admitted: Mapping[str, tuple[str, ...]],
    ) -> None:
        diagnostics: list[Diagnostic] = []
        slots = {slot.id for slot in program.slots}
        candidates = {candidate.id for candidate in registry.candidates}
        for index, constraint in enumerate(constraints):
            path = f"constraints[{index}]"
            if not ID_RE.fullmatch(constraint.id) or not constraint.reason.strip():
                diagnostics.append(Diagnostic(
                    "UNG-CONSTRAINT-004",
                    "constraint id must be namespaced and reason must not be empty",
                    path,
                ))
            if len(constraint.assignments) < 2:
                diagnostics.append(Diagnostic(
                    "UNG-CONSTRAINT-001",
                    "a forbidden combination needs at least two assignments",
                    path,
                ))
            assignment_slots = [slot for slot, _ in constraint.assignments]
            if len(assignment_slots) != len(set(assignment_slots)):
                diagnostics.append(Diagnostic(
                    "UNG-CONSTRAINT-002", "constraint assigns one slot more than once", path))
            for slot, candidate in constraint.assignments:
                if slot not in slots or candidate not in candidates:
                    diagnostics.append(Diagnostic(
                        "UNG-CONSTRAINT-003",
                        "constraint references unknown slot or candidate",
                        path,
                    ))
                elif candidate not in admitted.get(slot, ()):
                    diagnostics.append(Diagnostic(
                        "UNG-CONSTRAINT-005",
                        f"candidate {candidate} is not admitted for slot {slot}",
                        path,
                    ))
        if diagnostics:
            raise ValidationError("invalid route constraints", diagnostics)

    def compile(
        self,
        program: ProgramGraph,
        registry: Registry,
        space: AdmittedSpace,
        selection: Mapping[str, str],
    ) -> FrozenPlan:
        """Bind one candidate per slot and freeze exact implementation identities."""
        diagnostics: list[Diagnostic] = []
        if space.program_digest != program.digest:
            diagnostics.append(Diagnostic(
                "UNG-COMPILE-001", "candidate space was produced for another program", "space"))
        if space.registry_digest != registry.digest:
            diagnostics.append(Diagnostic(
                "UNG-COMPILE-002", "candidate space was produced for another registry", "space"))
        expected = {slot.id for slot in program.slots}
        actual = set(selection)
        for missing in sorted(expected - actual):
            diagnostics.append(Diagnostic(
                "UNG-COMPILE-003", f"selection omits slot {missing}", "selection"))
        for extra in sorted(actual - expected):
            diagnostics.append(Diagnostic(
                "UNG-COMPILE-004", f"selection contains unknown slot {extra}", "selection"))
        for slot, candidate in selection.items():
            if slot in expected and candidate not in space.choices_for(slot):
                diagnostics.append(Diagnostic(
                    "UNG-COMPILE-005",
                    f"candidate {candidate} is not admitted for slot {slot}",
                    f"selection.{slot}",
                ))
        for constraint in space.constraints:
            if constraint.matches(selection):
                diagnostics.append(Diagnostic(
                    "UNG-COMPILE-006",
                    f"selection violates {constraint.id}: {constraint.reason}",
                    "selection",
                ))
        if diagnostics:
            raise ValidationError("cannot compile route", diagnostics)

        order = self._topological_order(program.slots, program.edges)
        candidate_map = registry.candidate_map()
        node_map = registry.node_map()
        bindings: list[PlanBinding] = []
        for slot_id in order:
            candidate = candidate_map[selection[slot_id]]
            node = node_map[(candidate.node_id, candidate.node_version)]
            bindings.append(PlanBinding(
                slot_id=slot_id,
                candidate_id=candidate.id,
                node_id=node.id,
                node_version=node.version,
                implementation_digest=node.implementation_digest,
                parameters=tuple(sorted(candidate.resolved_parameters(node).items())),
            ))
        plan = FrozenPlan(
            digest="",
            program_id=program.id,
            program_version=program.version,
            program_digest=program.digest,
            registry_digest=registry.digest,
            topological_order=order,
            bindings=tuple(bindings),
            edges=program.edges,
        )
        return replace(plan, digest=sha256_digest(plan.unsigned_dict()))

    @staticmethod
    def _topological_order(
        slots: tuple[SemanticSlot, ...], edges: tuple[Edge, ...]
    ) -> tuple[str, ...]:
        slot_ids = {slot.id for slot in slots}
        indegree = {slot_id: 0 for slot_id in slot_ids}
        children = {slot_id: set() for slot_id in slot_ids}
        for edge in edges:
            if edge.source_slot not in slot_ids or edge.target_slot not in slot_ids:
                continue
            if edge.target_slot not in children[edge.source_slot]:
                children[edge.source_slot].add(edge.target_slot)
                indegree[edge.target_slot] += 1
        ready = sorted(slot_id for slot_id, degree in indegree.items() if degree == 0)
        order: list[str] = []
        while ready:
            current = ready.pop(0)
            order.append(current)
            for child in sorted(children[current]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
                    ready.sort()
        return tuple(order)
