"""Immutable run evidence, experiment design, Pareto ranking, and prior learning."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from math import log, sqrt
from statistics import fmean, pvariance
from typing import Any

from solutiongraph.model import DIGEST_RE, ID_RE
from solutiongraph.search import BeliefModel, CandidateWeight, InteractionWeight


@dataclass(frozen=True)
class Objective:
    metric: str
    direction: str
    weight: float = 1.0
    hard_minimum: float | None = None
    hard_maximum: float | None = None

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.metric.strip():
            problems.append("objective metric must not be empty")
        if self.direction not in ("maximize", "minimize"):
            problems.append("objective direction must be maximize or minimize")
        if self.weight < 0:
            problems.append("objective weight must be non-negative")
        if (self.hard_minimum is not None and self.hard_maximum is not None
                and self.hard_minimum > self.hard_maximum):
            problems.append("objective hard_minimum exceeds hard_maximum")
        return problems


@dataclass(frozen=True)
class NodeRunReceipt:
    slot_id: str
    candidate_id: str
    outcome: str
    started_at: str = ""
    completed_at: str = ""
    metrics: Mapping[str, float] = field(default_factory=dict)
    failure_class: str = ""
    artifact_digests: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunReceipt:
    """Append-only evidence for one frozen plan on one task case and seed."""

    id: str
    plan_digest: str
    program_digest: str
    task_case_id: str
    outcome: str
    accepted: bool | None
    verifier: str
    assignments: tuple[tuple[str, str], ...]
    metrics: Mapping[str, float] = field(default_factory=dict)
    node_receipts: tuple[NodeRunReceipt, ...] = ()
    seed: int | None = None
    started_at: str = ""
    completed_at: str = ""
    failure_class: str = ""
    executor: str = ""
    environment_digest: str = ""
    input_digest: str = ""
    belief_revision: str = ""

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not ID_RE.fullmatch(self.id):
            problems.append("receipt id must be a lowercase namespaced identifier")
        for label, digest in (
            ("plan_digest", self.plan_digest),
            ("program_digest", self.program_digest),
            ("environment_digest", self.environment_digest),
            ("input_digest", self.input_digest),
        ):
            if digest and not DIGEST_RE.fullmatch(digest):
                problems.append(f"{label} must be sha256:<64 lowercase hex chars>")
        if not self.plan_digest or not self.program_digest:
            problems.append("plan_digest and program_digest are required")
        if not self.task_case_id.strip() or not self.outcome.strip():
            problems.append("task_case_id and outcome are required")
        slots = [slot for slot, _ in self.assignments]
        if len(slots) != len(set(slots)):
            problems.append("assignments must contain one candidate per slot")
        if self.accepted is not None and not isinstance(self.accepted, bool):
            problems.append("accepted must be boolean or null")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "plan_digest": self.plan_digest,
            "program_digest": self.program_digest,
            "task_case_id": self.task_case_id,
            "outcome": self.outcome,
            "accepted": self.accepted,
            "verifier": self.verifier,
            "assignments": dict(self.assignments),
            "metrics": dict(self.metrics),
            "node_receipts": [
                {
                    "slot_id": item.slot_id,
                    "candidate_id": item.candidate_id,
                    "outcome": item.outcome,
                    "started_at": item.started_at,
                    "completed_at": item.completed_at,
                    "metrics": dict(item.metrics),
                    "failure_class": item.failure_class,
                    "artifact_digests": list(item.artifact_digests),
                }
                for item in self.node_receipts
            ],
            "seed": self.seed,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "failure_class": self.failure_class,
            "executor": self.executor,
            "environment_digest": self.environment_digest,
            "input_digest": self.input_digest,
            "belief_revision": self.belief_revision,
        }


@dataclass(frozen=True)
class ExperimentDesign:
    """A reproducible benchmark allocation, separate from the routes themselves."""

    id: str
    task_case_ids: tuple[str, ...]
    plan_digests: tuple[str, ...]
    seeds: tuple[int, ...]
    repetitions: int
    objectives: tuple[Objective, ...]
    control_plan_digest: str = ""
    holdout_case_ids: tuple[str, ...] = ()

    @property
    def scheduled_runs(self) -> int:
        return (len(self.task_case_ids) * len(self.plan_digests)
                * len(self.seeds) * self.repetitions)

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not ID_RE.fullmatch(self.id):
            problems.append("experiment id must be a lowercase namespaced identifier")
        if not self.task_case_ids or not self.plan_digests or not self.seeds:
            problems.append("task cases, plans, and seeds must not be empty")
        if self.repetitions <= 0:
            problems.append("repetitions must be positive")
        if self.control_plan_digest and self.control_plan_digest not in self.plan_digests:
            problems.append("control plan must be in plan_digests")
        if set(self.holdout_case_ids) - set(self.task_case_ids):
            problems.append("holdout cases must be a subset of task cases")
        for objective in self.objectives:
            problems.extend(objective.validate())
        return problems


@dataclass(frozen=True)
class RouteAggregate:
    plan_digest: str
    runs: int
    accepted_runs: int
    acceptance_rate: float
    metric_means: Mapping[str, float]
    metric_variances: Mapping[str, float]
    failure_classes: Mapping[str, int]


@dataclass(frozen=True)
class EvidenceLedger:
    """An immutable ledger; append returns a new value and never rewrites history."""

    receipts: tuple[RunReceipt, ...] = ()

    def append(self, *receipts: RunReceipt) -> EvidenceLedger:
        problems = [problem for receipt in receipts for problem in receipt.validate()]
        existing = {receipt.id for receipt in self.receipts}
        incoming = [receipt.id for receipt in receipts]
        if len(incoming) != len(set(incoming)) or existing.intersection(incoming):
            problems.append("receipt ids must be globally unique")
        if problems:
            raise ValueError("invalid evidence: " + "; ".join(problems))
        return EvidenceLedger(self.receipts + tuple(receipts))

    def aggregates(self) -> tuple[RouteAggregate, ...]:
        grouped: dict[str, list[RunReceipt]] = {}
        for receipt in self.receipts:
            grouped.setdefault(receipt.plan_digest, []).append(receipt)
        aggregates: list[RouteAggregate] = []
        for plan_digest, receipts in sorted(grouped.items()):
            metric_names = sorted({name for receipt in receipts for name in receipt.metrics})
            means: dict[str, float] = {}
            variances: dict[str, float] = {}
            for metric in metric_names:
                values = [float(receipt.metrics[metric]) for receipt in receipts
                          if metric in receipt.metrics]
                means[metric] = fmean(values)
                variances[metric] = pvariance(values) if len(values) > 1 else 0.0
            failures: dict[str, int] = {}
            for receipt in receipts:
                if receipt.failure_class:
                    failures[receipt.failure_class] = failures.get(receipt.failure_class, 0) + 1
            accepted = sum(receipt.accepted is True for receipt in receipts)
            aggregates.append(RouteAggregate(
                plan_digest=plan_digest,
                runs=len(receipts),
                accepted_runs=accepted,
                acceptance_rate=accepted / len(receipts),
                metric_means=means,
                metric_variances=variances,
                failure_classes=failures,
            ))
        return tuple(aggregates)


def pareto_front(
    aggregates: Iterable[RouteAggregate], objectives: tuple[Objective, ...]
) -> tuple[RouteAggregate, ...]:
    """Return nondominated routes after applying hard objective constraints."""
    candidates = [item for item in aggregates if _meets_constraints(item, objectives)]

    def dominates(left: RouteAggregate, right: RouteAggregate) -> bool:
        weakly_better = True
        strictly_better = False
        for objective in objectives:
            left_value = left.metric_means.get(objective.metric)
            right_value = right.metric_means.get(objective.metric)
            if left_value is None or right_value is None:
                return False
            if objective.direction == "maximize":
                weakly_better &= left_value >= right_value
                strictly_better |= left_value > right_value
            else:
                weakly_better &= left_value <= right_value
                strictly_better |= left_value < right_value
        return weakly_better and strictly_better

    return tuple(
        item for item in candidates
        if not any(other is not item and dominates(other, item) for other in candidates)
    )


def _meets_constraints(item: RouteAggregate, objectives: tuple[Objective, ...]) -> bool:
    for objective in objectives:
        value = item.metric_means.get(objective.metric)
        if value is None:
            return False
        if objective.hard_minimum is not None and value < objective.hard_minimum:
            return False
        if objective.hard_maximum is not None and value > objective.hard_maximum:
            return False
    return True


def learn_observational_beliefs(
    receipts: Iterable[RunReceipt],
    *,
    revision: str,
    interactions: tuple[tuple[str, str], ...] = (),
    alpha: float = 1.0,
    beta: float = 1.0,
) -> BeliefModel:
    """Fit smoothed success priors; these are correlations, not causal effects."""
    if alpha <= 0 or beta <= 0:
        raise ValueError("alpha and beta must be positive")
    receipts = tuple(receipt for receipt in receipts if receipt.accepted is not None)
    counts: dict[tuple[str, str], list[int]] = {}
    pair_counts: dict[tuple[str, str, str, str], list[int]] = {}
    for receipt in receipts:
        assignment = dict(receipt.assignments)
        success = int(receipt.accepted is True)
        for item in assignment.items():
            bucket = counts.setdefault(item, [0, 0])
            bucket[0] += success
            bucket[1] += 1
        for left_slot, right_slot in interactions:
            if left_slot not in assignment or right_slot not in assignment:
                continue
            key = (left_slot, assignment[left_slot], right_slot, assignment[right_slot])
            bucket = pair_counts.setdefault(key, [0, 0])
            bucket[0] += success
            bucket[1] += 1

    candidate_weights: list[CandidateWeight] = []
    for (slot_id, candidate_id), (successes, total) in sorted(counts.items()):
        posterior_total = total + alpha + beta
        probability = (successes + alpha) / posterior_total
        uncertainty = sqrt(probability * (1 - probability) / (posterior_total + 1))
        candidate_weights.append(CandidateWeight(
            slot_id=slot_id,
            candidate_id=candidate_id,
            log_weight=log(probability),
            evidence_count=total,
            uncertainty=uncertainty,
        ))
    interaction_weights: list[InteractionWeight] = []
    for key, (successes, total) in sorted(pair_counts.items()):
        probability = (successes + alpha) / (total + alpha + beta)
        interaction_weights.append(InteractionWeight(
            left_slot=key[0],
            left_candidate=key[1],
            right_slot=key[2],
            right_candidate=key[3],
            log_weight=log(probability),
            evidence_count=total,
        ))
    return BeliefModel(
        revision=revision,
        candidate_weights=tuple(candidate_weights),
        interaction_weights=tuple(interaction_weights),
    )
