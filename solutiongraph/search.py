"""Anytime, inspectable route search over compiler-admitted candidates.

Search scores influence ordering and experiment allocation only. They can never
make an invalid route valid or alter a frozen plan.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from itertools import product
from math import isfinite, prod

from solutiongraph.model import AdmittedSpace


class SearchMode(str, Enum):
    PRIOR = "prior"
    BEAM = "beam"
    EXHAUSTIVE = "exhaustive"


@dataclass(frozen=True)
class CandidateWeight:
    slot_id: str
    candidate_id: str
    log_weight: float
    evidence_count: int = 0
    uncertainty: float = 1.0


@dataclass(frozen=True)
class InteractionWeight:
    left_slot: str
    left_candidate: str
    right_slot: str
    right_candidate: str
    log_weight: float
    evidence_count: int = 0


@dataclass(frozen=True)
class BeliefModel:
    """A versioned optimizer overlay, deliberately absent from program semantics."""

    revision: str = "untrained"
    candidate_weights: tuple[CandidateWeight, ...] = ()
    interaction_weights: tuple[InteractionWeight, ...] = ()
    default_log_weight: float = 0.0

    def validate(self) -> list[str]:
        problems: list[str] = []
        for weight in self.candidate_weights:
            if not isfinite(weight.log_weight) or not isfinite(weight.uncertainty):
                problems.append("candidate weights and uncertainty must be finite")
            if weight.evidence_count < 0 or weight.uncertainty < 0:
                problems.append("evidence_count and uncertainty must be non-negative")
        for weight in self.interaction_weights:
            if not isfinite(weight.log_weight) or weight.evidence_count < 0:
                problems.append("interaction weights must be finite with non-negative evidence")
        return problems

    def candidate_score(self, slot_id: str, candidate_id: str) -> float:
        for weight in self.candidate_weights:
            if weight.slot_id == slot_id and weight.candidate_id == candidate_id:
                return weight.log_weight
        return self.default_log_weight

    def incremental_score(
        self,
        selection: Mapping[str, str],
        slot_id: str,
        candidate_id: str,
    ) -> tuple[float, tuple[tuple[str, float], ...]]:
        contributions: list[tuple[str, float]] = []
        unary = self.candidate_score(slot_id, candidate_id)
        contributions.append((f"candidate:{slot_id}:{candidate_id}", unary))
        score = unary
        for interaction in self.interaction_weights:
            forward = (
                interaction.right_slot == slot_id
                and interaction.right_candidate == candidate_id
                and selection.get(interaction.left_slot) == interaction.left_candidate
            )
            reverse = (
                interaction.left_slot == slot_id
                and interaction.left_candidate == candidate_id
                and selection.get(interaction.right_slot) == interaction.right_candidate
            )
            if forward or reverse:
                score += interaction.log_weight
                contributions.append((
                    "interaction:"
                    f"{interaction.left_slot}:{interaction.left_candidate}:"
                    f"{interaction.right_slot}:{interaction.right_candidate}",
                    interaction.log_weight,
                ))
        return score, tuple(contributions)


@dataclass(frozen=True)
class SearchBudget:
    """Every search limitation is explicit and preserved in the report."""

    mode: SearchMode = SearchMode.PRIOR
    evaluation_limit: int | None = None
    result_limit: int = 10
    beam_width: int = 32

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.evaluation_limit is not None and self.evaluation_limit <= 0:
            problems.append("evaluation_limit must be positive or null")
        if self.result_limit <= 0:
            problems.append("result_limit must be positive")
        if self.beam_width <= 0:
            problems.append("beam_width must be positive")
        return problems


@dataclass(frozen=True)
class RouteProposal:
    assignments: tuple[tuple[str, str], ...]
    log_score: float
    contributions: tuple[tuple[str, float], ...] = ()

    @property
    def selection(self) -> dict[str, str]:
        return dict(self.assignments)

    def to_dict(self) -> dict[str, object]:
        return {
            "selection": dict(self.assignments),
            "log_score": self.log_score,
            "contributions": [
                {"factor": factor, "value": value}
                for factor, value in self.contributions
            ],
        }


@dataclass(frozen=True)
class SearchReport:
    mode: SearchMode
    belief_revision: str
    total_cartesian_routes: int
    evaluated_routes: int
    constraint_eliminated_routes: int
    heuristic_skipped_routes: int
    unvisited_routes: int
    complete: bool
    optimality_proven: bool
    evaluation_limit: int | None
    result_limit: int
    beam_width: int | None
    proposals: tuple[RouteProposal, ...]

    @property
    def evaluation_coverage(self) -> float:
        if self.total_cartesian_routes == 0:
            return 0.0
        return self.evaluated_routes / self.total_cartesian_routes

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "belief_revision": self.belief_revision,
            "total_cartesian_routes": self.total_cartesian_routes,
            "evaluated_routes": self.evaluated_routes,
            "constraint_eliminated_routes": self.constraint_eliminated_routes,
            "heuristic_skipped_routes": self.heuristic_skipped_routes,
            "unvisited_routes": self.unvisited_routes,
            "complete": self.complete,
            "optimality_proven": self.optimality_proven,
            "evaluation_coverage": self.evaluation_coverage,
            "budget": {
                "evaluation_limit": self.evaluation_limit,
                "result_limit": self.result_limit,
                "beam_width": self.beam_width,
            },
            "proposals": [proposal.to_dict() for proposal in self.proposals],
        }


@dataclass
class _Partial:
    selection: dict[str, str]
    score: float
    contributions: tuple[tuple[str, float], ...]


class SearchEngine:
    """Provide a fast prior path, bounded beam search, and true exhaustive search."""

    def iter_exhaustive(
        self,
        space: AdmittedSpace,
        beliefs: BeliefModel | None = None,
    ) -> Iterator[RouteProposal]:
        """Yield every feasible route with no implicit cap, in prior-guided order."""
        beliefs = beliefs or BeliefModel()
        choices = self._ordered_choices(space, beliefs)

        def walk(
            index: int,
            selection: dict[str, str],
            score: float,
            contributions: tuple[tuple[str, float], ...],
        ) -> Iterator[RouteProposal]:
            if index == len(choices):
                yield RouteProposal(tuple(selection.items()), score, contributions)
                return
            slot_id, candidates = choices[index]
            for candidate_id in candidates:
                selection[slot_id] = candidate_id
                if not self._violates_constraint(space, selection):
                    increment, factors = beliefs.incremental_score(
                        selection, slot_id, candidate_id)
                    yield from walk(
                        index + 1,
                        selection,
                        score + increment,
                        contributions + factors,
                    )
                selection.pop(slot_id)

        yield from walk(0, {}, 0.0, ())

    def search(
        self,
        space: AdmittedSpace,
        beliefs: BeliefModel | None = None,
        budget: SearchBudget | None = None,
    ) -> SearchReport:
        beliefs = beliefs or BeliefModel()
        budget = budget or SearchBudget()
        problems = (*beliefs.validate(), *budget.validate())
        if problems:
            raise ValueError("invalid search configuration: " + "; ".join(problems))
        if budget.mode == SearchMode.EXHAUSTIVE:
            return self._search_exhaustive(space, beliefs, budget)
        width = 1 if budget.mode == SearchMode.PRIOR else budget.beam_width
        return self._search_beam(space, beliefs, budget, width)

    def _search_exhaustive(
        self, space: AdmittedSpace, beliefs: BeliefModel, budget: SearchBudget
    ) -> SearchReport:
        choices = self._ordered_choices(space, beliefs)
        total = space.route_count_upper_bound
        evaluated = 0
        constraint_eliminated = 0
        stopped = False
        best: list[RouteProposal] = []

        def retain(proposal: RouteProposal) -> None:
            best.append(proposal)
            best.sort(key=self._proposal_key)
            if len(best) > budget.result_limit:
                best.pop()

        def walk(
            index: int,
            selection: dict[str, str],
            score: float,
            contributions: tuple[tuple[str, float], ...],
        ) -> None:
            nonlocal evaluated, constraint_eliminated, stopped
            if stopped:
                return
            if index == len(choices):
                if budget.evaluation_limit is not None and evaluated >= budget.evaluation_limit:
                    stopped = True
                    return
                evaluated += 1
                retain(RouteProposal(tuple(selection.items()), score, contributions))
                return
            slot_id, candidates = choices[index]
            for candidate_id in candidates:
                if stopped:
                    return
                selection[slot_id] = candidate_id
                if self._violates_constraint(space, selection):
                    constraint_eliminated += prod(
                        len(remaining) for _, remaining in choices[index + 1:]
                    )
                else:
                    increment, factors = beliefs.incremental_score(
                        selection, slot_id, candidate_id)
                    walk(index + 1, selection, score + increment, contributions + factors)
                selection.pop(slot_id)

        walk(0, {}, 0.0, ())
        accounted = evaluated + constraint_eliminated
        complete = not stopped and accounted == total
        return SearchReport(
            mode=budget.mode,
            belief_revision=beliefs.revision,
            total_cartesian_routes=total,
            evaluated_routes=evaluated,
            constraint_eliminated_routes=constraint_eliminated,
            heuristic_skipped_routes=0,
            unvisited_routes=max(0, total - accounted),
            complete=complete,
            optimality_proven=complete,
            evaluation_limit=budget.evaluation_limit,
            result_limit=budget.result_limit,
            beam_width=None,
            proposals=tuple(best),
        )

    def _search_beam(
        self,
        space: AdmittedSpace,
        beliefs: BeliefModel,
        budget: SearchBudget,
        width: int,
    ) -> SearchReport:
        choices = self._ordered_choices(space, beliefs)
        total = space.route_count_upper_bound
        partials = [_Partial({}, 0.0, ())]
        constraint_eliminated = 0
        heuristic_skipped = 0
        for index, (slot_id, candidates) in enumerate(choices):
            expanded: list[_Partial] = []
            for partial in partials:
                for candidate_id in candidates:
                    selection = {**partial.selection, slot_id: candidate_id}
                    remaining_count = prod(
                        len(remaining) for _, remaining in choices[index + 1:]
                    )
                    if self._violates_constraint(space, selection):
                        constraint_eliminated += remaining_count
                        continue
                    increment, factors = beliefs.incremental_score(
                        selection, slot_id, candidate_id)
                    expanded.append(_Partial(
                        selection,
                        partial.score + increment,
                        partial.contributions + factors,
                    ))
            expanded.sort(key=lambda item: (-item.score, tuple(item.selection.items())))
            dropped = expanded[width:]
            if dropped:
                remaining_count = prod(
                    len(remaining) for _, remaining in choices[index + 1:]
                )
                heuristic_skipped += len(dropped) * remaining_count
            partials = expanded[:width]

        if budget.evaluation_limit is not None and len(partials) > budget.evaluation_limit:
            partials.sort(key=lambda item: (-item.score, tuple(item.selection.items())))
            heuristic_skipped += len(partials) - budget.evaluation_limit
            partials = partials[:budget.evaluation_limit]

        proposals = tuple(sorted((
            RouteProposal(tuple(item.selection.items()), item.score, item.contributions)
            for item in partials
        ), key=self._proposal_key)[:budget.result_limit])
        evaluated = len(partials)
        accounted = evaluated + constraint_eliminated + heuristic_skipped
        complete = accounted == total and heuristic_skipped == 0
        return SearchReport(
            mode=budget.mode,
            belief_revision=beliefs.revision,
            total_cartesian_routes=total,
            evaluated_routes=evaluated,
            constraint_eliminated_routes=constraint_eliminated,
            heuristic_skipped_routes=heuristic_skipped,
            unvisited_routes=max(0, total - accounted),
            complete=complete,
            optimality_proven=complete,
            evaluation_limit=budget.evaluation_limit,
            result_limit=budget.result_limit,
            beam_width=width,
            proposals=proposals,
        )

    @staticmethod
    def _violates_constraint(space: AdmittedSpace, selection: Mapping[str, str]) -> bool:
        return any(constraint.matches(selection) for constraint in space.constraints)

    @staticmethod
    def _ordered_choices(
        space: AdmittedSpace, beliefs: BeliefModel
    ) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return tuple(
            (
                slot_id,
                tuple(sorted(
                    candidates,
                    key=lambda candidate: (
                        -beliefs.candidate_score(slot_id, candidate), candidate
                    ),
                )),
            )
            for slot_id, candidates in space.choices
        )

    @staticmethod
    def _proposal_key(proposal: RouteProposal) -> tuple[float, tuple[tuple[str, str], ...]]:
        return -proposal.log_score, proposal.assignments


def cartesian_routes(space: AdmittedSpace) -> Iterator[dict[str, str]]:
    """Simple reference enumeration useful for conformance tests and exports."""
    slots = [slot for slot, _ in space.choices]
    choices = [candidates for _, candidates in space.choices]
    for values in product(*choices):
        selection = dict(zip(slots, values, strict=True))
        if not any(constraint.matches(selection) for constraint in space.constraints):
            yield selection
