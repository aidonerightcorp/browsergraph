"""What an evaluation owes, made into something a program can check.

Every team that evaluates anything eventually has this conversation:

    "Is it better?"      "The score went up."
    "Better than what?"  "Than last time."
    "Graded how?"        "By the grader."
    "Would it have noticed if the system were broken?"      "...."

The last question is the one that matters and it is almost never asked before
the result is circulated. This module is the answer to it: the set of things a
careful person would have checked, discharged one at a time, recorded, and
carried alongside the number so that anybody reading the number can see what
stands behind it.

This module makes those checks first-class values rather than habits.

    from assay import obligations

    ledger = obligations.Ledger.standard()
    ledger.discharge("holdout", "cases 0-199 were never used in development")
    ledger.record(obligations.check_negative_control(real=0.91, broken=0.89))
    ledger.waive("slices", "single-slice dataset; nothing to break down by")

    print(ledger.verdict(score=0.91).text())
    # PROVISIONAL — 0.910
    #   failed:      negative_control (a deliberately broken system scored
    #                within 0.02; this harness cannot tell them apart)
    #   outstanding: sample_size, provenance
    #   waived:      slices — single-slice dataset; nothing to break down by

Three states, and the middle one is the whole point:

* **PASS** — every blocking obligation discharged or explicitly waived.
* **PROVISIONAL** — the number exists and nobody may act on it yet. This is not
  a failure. It is the honest description of most evaluations, and having a word
  for it is what stops "we have not checked" from being rendered as a pass.
* **FAIL** — a check ran and came back no. A failed control is worse news than
  an outstanding one: it means the previous results were noise, not that the
  current one is unknown.

**A waiver needs a reason.** `waive()` refuses an empty one. Waivers appear in
the report exactly as prominently as discharges, because "not applicable here"
is a claim somebody made and should have to sign.

## The feedback loop

An evaluation that runs once is an opinion with a date on it. `Loop` is the
other half: each round's failures become the next round's cases, permanently, so
a bug fixed in March is still being checked in November; and each round's
outcome folds into an evidence store, so the *choice of route* improves from the
same observations that graded the output.

    loop = obligations.Loop()
    loop.round(cases=200, verdict=v1, failures=["case-17", "case-88"])
    loop.round(cases=202, verdict=v2, failures=["case-91"])
    print(loop.text())        # and whether it is converging or churning

The loop reports **new** failures per round rather than total, because a total
that goes down is also what happens when you delete the hard cases.

Nothing here is specific to language models. A grader is anything that turns an
output into a number, and the obligations below are the same for a fraud model,
a document extractor and a chat assistant.

## On the name

This was called `duecare` while it lived inside browsergraph. It is
`obligations` here because Due Care is also an unrelated product — an offline
legal companion — and one name for two things is how a search for either finds
neither.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: A blocking obligation, outstanding, makes the verdict provisional. A material
#: one is reported prominently and does not block. An advisory one is a note.
SEVERITIES = ("blocking", "material", "advisory")

#: What a discharge can be. `failed` is distinct from `outstanding` on purpose:
#: not knowing and knowing it is wrong are different situations, and collapsing
#: them is how a red control becomes "we should look into that some time".
STATES = ("discharged", "waived", "failed", "outstanding")

PASS, PROVISIONAL, FAIL = "PASS", "PROVISIONAL", "FAIL"


@dataclass(frozen=True)
class Obligation:
    """One thing an evaluation owes before its number means anything."""

    id: str
    what: str
    #: What goes wrong when this is skipped. Written as the specific failure,
    #: not as a principle — a reader deciding whether to waive it needs to know
    #: what they are accepting.
    why: str
    severity: str = "material"

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}, "
                             f"got {self.severity!r}")


@dataclass(frozen=True)
class Discharge:
    """What happened to one obligation, and what shows it."""

    obligation: str
    state: str
    #: One line a human can check. "cases 0-199 held out since 3 March" beats
    #: "yes" by the entire value of this module.
    evidence: str = ""
    #: The numbers behind it, when a check computed them.
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"state must be one of {STATES}, got {self.state!r}")
        if self.state in ("discharged", "waived", "failed") and not self.evidence:
            raise ValueError(
                f"a {self.state} obligation needs evidence — an unexplained "
                f"{self.state!r} is exactly the thing this module exists to stop")

    def to_dict(self) -> dict:
        return {"obligation": self.obligation, "state": self.state,
                "evidence": self.evidence, "detail": dict(self.detail)}


# --- the standard obligations -----------------------------------------------
#
# Nine, and the list is deliberately short enough to read before a review. Each
# one earned its place by being the thing that, when missing, made somebody's
# published number wrong rather than merely incomplete.

STANDARD: tuple[Obligation, ...] = (
    Obligation(
        "holdout", "The score is computed on items the system never saw.",
        "Otherwise the number measures memorisation. This includes items seen "
        "during development: a test set consulted forty times while tuning is "
        "a validation set with a misleading name.",
        "blocking"),
    Obligation(
        "negative_control",
        "A deliberately broken variant scores materially worse.",
        "If a system with its retrieval disabled, its model swapped for a "
        "constant, or its prompt emptied scores the same, the harness is not "
        "measuring the thing it names — and every previous comparison it "
        "produced was noise.",
        "blocking"),
    Obligation(
        "positive_control", "A known-good variant scores well.",
        "A grader that fails everything is as useless as one that passes "
        "everything, and it is harder to notice because a low score reads as "
        "a hard benchmark.",
        "material"),
    Obligation(
        "sample_size",
        "The reported difference is larger than the interval around it.",
        "With 40 items, a 3-point difference is noise. Reporting it as an "
        "improvement is how a team spends a quarter on a change that did "
        "nothing.",
        "blocking"),
    Obligation(
        "slices", "The result is broken down, not only averaged.",
        "A 2% overall gain made of +8% on the common case and -30% on the "
        "rare one is a regression, and the mean hides it perfectly.",
        "material"),
    Obligation(
        "grader_agreement",
        "The grader agrees with a human anchor above chance.",
        "An automatic grader is a model of a judgement. Without a sample of "
        "human labels to check it against, the evaluation measures the "
        "grader's preferences — usually for length and confident phrasing.",
        "material"),
    Obligation(
        "replication", "Re-running gives the same answer, or the spread is "
        "reported.",
        "A result that moves by more between two runs than between two "
        "systems is not a comparison of systems.",
        "material"),
    Obligation(
        "provenance", "Every score traces to an item and the run that made it.",
        "Without it, a suspicious result cannot be investigated, only "
        "re-run — and a re-run that disagrees leaves you with two numbers "
        "and no way to choose.",
        "blocking"),
    Obligation(
        "coverage", "What was not exercised is named.",
        "Silence about the untested part reads as coverage. The languages, "
        "sizes, or customer segments absent from the case set are the ones "
        "that will produce the incident.",
        "advisory"),
)


@dataclass(frozen=True)
class Verdict:
    """A score, and what stands behind it."""

    state: str
    score: float = 0.0
    discharged: tuple[str, ...] = ()
    waived: tuple[tuple[str, str], ...] = ()
    failed: tuple[tuple[str, str], ...] = ()
    outstanding: tuple[str, ...] = ()
    #: Content hash over the obligations and their states. Two evaluations with
    #: the same digest were held to the same standard, which is the only basis
    #: on which two scores may be compared.
    digest: str = ""

    @property
    def ok(self) -> bool:
        """Deliberately true only for PASS.

        A provisional verdict is not a soft pass and the property that gates a
        deployment should not treat it as one.
        """
        return self.state == PASS

    @property
    def actionable(self) -> bool:
        """May somebody make a decision on this? PASS or a stated FAIL."""
        return self.state in (PASS, FAIL)

    def text(self) -> str:
        lines = [f"{self.state} — {self.score:.3f}  [{self.digest[:12]}]"]
        if self.failed:
            for name, why in self.failed:
                lines.append(f"  failed:      {name} — {why}")
        if self.outstanding:
            lines.append(f"  outstanding: {', '.join(self.outstanding)}")
        if self.waived:
            for name, why in self.waived:
                lines.append(f"  waived:      {name} — {why}")
        if self.discharged:
            lines.append(f"  discharged:  {', '.join(self.discharged)}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"state": self.state, "score": self.score, "digest": self.digest,
                "discharged": list(self.discharged),
                "waived": [{"obligation": o, "reason": r} for o, r in self.waived],
                "failed": [{"obligation": o, "evidence": e} for o, e in self.failed],
                "outstanding": list(self.outstanding)}


@dataclass
class Ledger:
    """The obligations of one evaluation, and what became of each.

    Mutable on purpose: an evaluation discharges its obligations as it goes,
    and forcing every check to be known up front would mean building the ledger
    after the fact — which is how a ledger becomes a description of what you
    happened to do.
    """

    obligations: tuple[Obligation, ...] = ()
    discharges: dict[str, Discharge] = field(default_factory=dict)
    #: Free notes that travel with the ledger into the report.
    notes: list[str] = field(default_factory=list)

    @classmethod
    def standard(cls, extra: Sequence[Obligation] = ()) -> Ledger:
        """The nine, plus anything this domain adds."""
        return cls(obligations=tuple(STANDARD) + tuple(extra))

    @classmethod
    def of(cls, *obligations: Obligation) -> Ledger:
        """A ledger of exactly these. For a domain the standard nine do not fit."""
        return cls(obligations=tuple(obligations))

    # --- recording ---------------------------------------------------------

    def _known(self, obligation_id: str) -> Obligation:
        for obligation in self.obligations:
            if obligation.id == obligation_id:
                return obligation
        raise KeyError(
            f"no obligation {obligation_id!r} in this ledger; it holds "
            f"{[o.id for o in self.obligations]}. Add it with `extra=` rather "
            f"than discharging something nobody required.")

    def record(self, discharge: Discharge) -> Ledger:
        """Put a computed discharge in. What the `check_*` functions return."""
        self._known(discharge.obligation)
        self.discharges[discharge.obligation] = discharge
        return self

    def discharge(self, obligation_id: str, evidence: str, **detail: Any) -> Ledger:
        """This was done, and here is the line that shows it."""
        return self.record(Discharge(obligation_id, "discharged", evidence,
                                     detail))

    def fail(self, obligation_id: str, evidence: str, **detail: Any) -> Ledger:
        """The check ran and the answer was no."""
        return self.record(Discharge(obligation_id, "failed", evidence, detail))

    def waive(self, obligation_id: str, reason: str) -> Ledger:
        """Not applicable here — and say why, because somebody will ask.

        Refusing an empty reason is the single most useful line in this file.
        A waiver with no reason is indistinguishable from an oversight, and
        after three months so is the memory of the person who made it.
        """
        if not reason or not reason.strip():
            raise ValueError(
                f"waiving {obligation_id!r} needs a reason. An unexplained "
                f"waiver is an oversight with better paperwork.")
        return self.record(Discharge(obligation_id, "waived", reason.strip()))

    def note(self, text: str) -> Ledger:
        self.notes.append(text)
        return self

    # --- reading -----------------------------------------------------------

    def state_of(self, obligation_id: str) -> str:
        self._known(obligation_id)
        found = self.discharges.get(obligation_id)
        return found.state if found else "outstanding"

    def outstanding(self) -> tuple[Obligation, ...]:
        return tuple(o for o in self.obligations
                     if self.state_of(o.id) == "outstanding")

    def failed(self) -> tuple[Discharge, ...]:
        return tuple(d for d in self.discharges.values() if d.state == "failed")

    def blocking_gaps(self) -> tuple[Obligation, ...]:
        """Outstanding *and* blocking — what makes a verdict provisional."""
        return tuple(o for o in self.outstanding() if o.severity == "blocking")

    def digest(self) -> str:
        """A hash of the standard this evaluation was held to.

        Over the obligations and their states, not over the score. Two runs
        with the same digest are comparable; two with different digests are
        two different claims and putting them in one table is the mistake
        this is here to make visible.
        """
        payload = json.dumps(
            {"obligations": [[o.id, o.severity] for o in self.obligations],
             "states": {k: v.state for k, v in sorted(self.discharges.items())}},
            sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def verdict(self, score: float = 0.0) -> Verdict:
        """Turn the ledger into a decision.

        The order matters: a failed check beats an outstanding one. Knowing a
        control did not hold is worse news than not having run it, and a
        verdict that reported PROVISIONAL when something had actually come
        back red would be burying the finding under the paperwork.
        """
        failed = tuple((d.obligation, d.evidence) for d in self.failed())
        waived = tuple((d.obligation, d.evidence)
                       for d in self.discharges.values() if d.state == "waived")
        discharged = tuple(sorted(d.obligation for d in self.discharges.values()
                                  if d.state == "discharged"))
        gaps = tuple(o.id for o in self.outstanding())

        if failed:
            state = FAIL
        elif self.blocking_gaps():
            state = PROVISIONAL
        else:
            state = PASS

        return Verdict(state=state, score=score, discharged=discharged,
                       waived=waived, failed=failed, outstanding=gaps,
                       digest=self.digest())

    def text(self, score: float = 0.0) -> str:
        """The whole ledger, obligation by obligation. For a review."""
        lines = [self.verdict(score).text(), ""]
        for obligation in self.obligations:
            state = self.state_of(obligation.id)
            found = self.discharges.get(obligation.id)
            mark = {"discharged": "ok  ", "waived": "n/a ", "failed": "FAIL",
                    "outstanding": "--  "}[state]
            lines.append(f"  [{mark}] {obligation.id:<18} {obligation.what}")
            if found and found.evidence:
                lines.append(f"{'':<26} {found.evidence}")
            elif state == "outstanding":
                lines.append(f"{'':<26} not checked — {obligation.why}")
            # An outstanding discharge that carries an evidence line is a check
            # that ran and could not conclude, which is worth more to a reader
            # than the generic "not checked" above.
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"digest": self.digest(),
                "obligations": [{"id": o.id, "what": o.what, "why": o.why,
                                 "severity": o.severity, "state": self.state_of(o.id)}
                                for o in self.obligations],
                "discharges": [d.to_dict() for d in self.discharges.values()],
                "notes": list(self.notes)}


# --- checks that compute a discharge from data ------------------------------
#
# Each returns a Discharge rather than a bool, so the evidence line and the
# numbers travel with the answer. A check that returned True would leave the
# caller to write down why, and nobody writes down why.


def check_negative_control(real: float, broken: float, *,
                           margin: float = 0.05,
                           higher_is_better: bool = True,
                           what: str = "a deliberately broken variant"
                           ) -> Discharge:
    """Did the harness notice a system that should not work?

    `margin` is how much worse the broken variant has to score before the
    harness has demonstrated it can tell them apart. The default of 0.05 is a
    convention and a poor one for any specific case — pass the number your
    interval actually supports.
    """
    gap = (real - broken) if higher_is_better else (broken - real)
    detail = {"real": real, "broken": broken, "gap": round(gap, 6),
              "margin": margin}
    if gap >= margin:
        return Discharge("negative_control", "discharged",
                         f"{what} scored {broken:.3f} against {real:.3f} — "
                         f"a gap of {gap:.3f}, past the {margin:.3f} margin",
                         detail)
    return Discharge("negative_control", "failed",
                     f"{what} scored {broken:.3f} against {real:.3f}: a gap of "
                     f"{gap:.3f} does not clear {margin:.3f}. This harness "
                     f"cannot separate the system from a broken one, so its "
                     f"previous results do not mean what they appeared to",
                     detail)


def check_positive_control(score: float, *, floor: float = 0.9,
                           what: str = "a known-good variant") -> Discharge:
    """Did a system that should work, work? The check nobody runs."""
    detail = {"score": score, "floor": floor}
    if score >= floor:
        return Discharge("positive_control", "discharged",
                         f"{what} scored {score:.3f}, at or above {floor:.3f}",
                         detail)
    return Discharge("positive_control", "failed",
                     f"{what} scored only {score:.3f} against a floor of "
                     f"{floor:.3f} — the grader is rejecting answers that are "
                     f"correct, so the benchmark is measuring the grader",
                     detail)


def interval(scores: Sequence[float], *, z: float = 1.96) -> tuple[float, float]:
    """Mean and half-width of a normal interval. Small, and honest about it.

    The normal approximation is wrong for tiny n and for bounded scores near
    their bounds. It is here because it is one line and it stops the specific
    error this module cares about — quoting a difference of 0.03 from forty
    items — and anything more careful belongs to whoever has the distribution.
    """
    values = [float(s) for s in scores]
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return values[0], float("inf")
    mean = statistics.fmean(values)
    error = statistics.stdev(values) / math.sqrt(len(values))
    return mean, z * error


def check_sample_size(scores: Sequence[float], *, effect: float = 0.0,
                      z: float = 1.96) -> Discharge:
    """Is the claimed difference bigger than the noise around the mean?

    Pass `effect` as the difference you want to claim. With `effect=0` this
    reports the interval and discharges — you have measured something and are
    claiming nothing about a comparison, which is a legitimate position.
    """
    mean, half = interval(scores, z=z)
    detail = {"n": len(scores), "mean": round(mean, 6),
              "half_width": None if half == float("inf") else round(half, 6),
              "effect": effect}
    if len(scores) < 2:
        return Discharge("sample_size", "outstanding",
                         f"{len(scores)} item(s) — no interval can be computed, "
                         f"so this has not been checked rather than checked "
                         f"and found wanting", detail)
    if effect and abs(effect) < half:
        return Discharge("sample_size", "failed",
                         f"a claimed difference of {effect:.3f} is inside the "
                         f"±{half:.3f} interval on {len(scores)} items; the "
                         f"honest statement is that the decision does not "
                         f"matter at this sample size", detail)
    return Discharge("sample_size", "discharged",
                     f"{len(scores)} items, mean {mean:.3f} ±{half:.3f}"
                     + (f", claimed effect {effect:.3f} clears it" if effect else ""),
                     detail)


def check_replication(runs: Sequence[float], *, tolerance: float = 0.02
                      ) -> Discharge:
    """Same input, same answer — or the spread reported rather than hidden."""
    if len(runs) < 2:
        return Discharge("replication", "outstanding",
                         "one run — nothing has been replicated, so this is "
                         "unchecked; a score reproduced zero times is a "
                         "measurement of one afternoon",
                         {"runs": len(runs)})
    spread = max(runs) - min(runs)
    detail = {"runs": len(runs), "spread": round(spread, 6),
              "tolerance": tolerance,
              "values": [round(r, 6) for r in runs]}
    if spread <= tolerance:
        return Discharge("replication", "discharged",
                         f"{len(runs)} runs spread {spread:.4f}, inside "
                         f"{tolerance:.4f}", detail)
    return Discharge("replication", "failed",
                     f"{len(runs)} runs spread {spread:.4f}, wider than the "
                     f"{tolerance:.4f} tolerance — a difference between two "
                     f"systems smaller than this is not a difference",
                     detail)


def cohen_kappa(a: Sequence[Any], b: Sequence[Any]) -> float:
    """Agreement above chance. Returns 0.0 when chance explains everything.

    Raw agreement is flattering on an imbalanced set: a grader that always says
    "good" agrees with humans 80% of the time on data that is 80% good, and 80%
    sounds like a working grader. Kappa subtracts that.
    """
    if len(a) != len(b):
        raise ValueError(f"{len(a)} labels against {len(b)} — kappa needs pairs")
    if not a:
        return 0.0
    observed = sum(1 for x, y in zip(a, b, strict=True) if x == y) / len(a)
    labels = set(a) | set(b)
    expected = sum((sum(1 for x in a if x == label) / len(a))
                   * (sum(1 for y in b if y == label) / len(b))
                   for label in labels)
    if expected >= 1.0:
        # Everything is one class: agreement is certain and means nothing.
        return 0.0
    return (observed - expected) / (1 - expected)


def check_grader_agreement(model: Sequence[Any], human: Sequence[Any], *,
                           floor: float = 0.4) -> Discharge:
    """Does the automatic grader agree with people more than chance would?

    The floor of 0.4 is the low end of what is usually called moderate
    agreement. It is a convention, not a law, and a grader at 0.4 is one whose
    disagreements you should read rather than one you should trust.
    """
    if not model or not human:
        return Discharge("grader_agreement", "outstanding",
                         "no human labels — the grader has never been checked "
                         "against a person, so the evaluation measures the "
                         "grader's preferences and nobody has looked", {"n": 0})
    if len(set(human)) < 2:
        # Kappa is undefined when one label explains the whole sample, and the
        # 0.0 that falls out is a fact about the sample rather than about the
        # grader. Reporting it as a failure would convict the grader of the
        # evaluation's own poor sampling.
        return Discharge("grader_agreement", "outstanding",
                         f"all {len(human)} human labels are "
                         f"{next(iter(set(human)))!r} — a single-class sample "
                         f"cannot validate a grader at all, and the kappa of "
                         f"0.0 it produces says nothing about this one",
                         {"n": len(human), "classes": sorted(set(human))})
    kappa = cohen_kappa(model, human)
    raw = sum(1 for x, y in zip(model, human, strict=True) if x == y) / len(model)
    detail = {"n": len(model), "kappa": round(kappa, 4), "raw": round(raw, 4),
              "floor": floor}
    if kappa >= floor:
        return Discharge("grader_agreement", "discharged",
                         f"kappa {kappa:.3f} on {len(model)} human-labelled "
                         f"items (raw agreement {raw:.1%})", detail)
    return Discharge("grader_agreement", "failed",
                     f"kappa {kappa:.3f} on {len(model)} items — raw agreement "
                     f"of {raw:.1%} is mostly chance on this label "
                     f"distribution, so the grader is not measuring what the "
                     f"humans were measuring", detail)


def check_slices(by_slice: Mapping[str, Sequence[float]], *,
                 regression: float = 0.05) -> Discharge:
    """Break the result down, and fail when one slice went backwards.

    The overall mean is compared against each slice's mean, and a slice more
    than `regression` below the whole is reported — because the gain that reads
    as progress is often one population's loss paying for another's.
    """
    if len(by_slice) < 2:
        return Discharge("slices", "outstanding",
                         f"{len(by_slice)} slice(s) — nothing was broken down, "
                         f"so no slice has been shown to be fine",
                         {"slices": len(by_slice)})
    means = {name: statistics.fmean(values) if values else 0.0
             for name, values in by_slice.items()}
    overall = statistics.fmean([v for values in by_slice.values() for v in values]
                               or [0.0])
    worst = min(means, key=lambda k: means[k])
    detail = {"overall": round(overall, 6),
              "means": {k: round(v, 6) for k, v in means.items()},
              "counts": {k: len(v) for k, v in by_slice.items()},
              "regression": regression}
    if overall - means[worst] > regression:
        return Discharge("slices", "failed",
                         f"slice {worst!r} scored {means[worst]:.3f} against "
                         f"{overall:.3f} overall — the average is carrying a "
                         f"regression of {overall - means[worst]:.3f}",
                         detail)
    return Discharge("slices", "discharged",
                     f"{len(by_slice)} slices, worst {worst!r} at "
                     f"{means[worst]:.3f} against {overall:.3f} overall",
                     detail)


def check_provenance(records: Sequence[Mapping[str, Any]], *,
                     required: Sequence[str] = ("case", "run")) -> Discharge:
    """Can every score be traced back to the item and run that produced it?"""
    if not records:
        return Discharge("provenance", "failed",
                         "no records — nothing can be traced", {"n": 0})
    missing: dict[str, int] = {}
    for record in records:
        for key in required:
            if not record.get(key):
                missing[key] = missing.get(key, 0) + 1
    detail = {"n": len(records), "required": list(required), "missing": missing}
    if not missing:
        return Discharge("provenance", "discharged",
                         f"{len(records)} scores each carry "
                         f"{', '.join(required)}", detail)
    worst = ", ".join(f"{k} missing on {v}" for k, v in sorted(missing.items()))
    return Discharge("provenance", "failed",
                     f"{worst} of {len(records)} scores — those results can "
                     f"only be re-run, not investigated", detail)


def check_coverage(exercised: Sequence[str], expected: Sequence[str]
                   ) -> Discharge:
    """Name what was not tried. Advisory, and the most-read line in the report.

    Discharges either way — coverage is a *statement*, and a report that says
    "these four segments were never exercised" has discharged the obligation to
    say so. What it must never do is stay quiet.
    """
    seen, wanted = set(exercised), set(expected)
    absent = sorted(wanted - seen)
    detail = {"exercised": sorted(seen), "expected": sorted(wanted),
              "absent": absent,
              "fraction": round(len(seen & wanted) / len(wanted), 4) if wanted else 1.0}
    if not absent:
        return Discharge("coverage", "discharged",
                         f"all {len(wanted)} expected slices exercised", detail)
    return Discharge("coverage", "discharged",
                     f"{len(seen & wanted)}/{len(wanted)} slices exercised; "
                     f"not tried: {', '.join(absent)}", detail)


# --- the feedback loop ------------------------------------------------------


@dataclass(frozen=True)
class Round:
    """One pass of an evaluation, and what it left behind for the next."""

    index: int
    cases: int
    verdict: Verdict
    #: Case ids that failed. These become permanent cases in the next round.
    failures: tuple[str, ...] = ()
    #: Failures not seen in any earlier round. The number that matters.
    novel: tuple[str, ...] = ()
    label: str = ""

    def to_dict(self) -> dict:
        return {"index": self.index, "cases": self.cases, "label": self.label,
                "verdict": self.verdict.to_dict(),
                "failures": list(self.failures), "novel": list(self.novel)}


@dataclass
class Loop:
    """Rounds of evaluation, where each round's failures join the next one's cases.

    Two things this gets right that an ad-hoc loop usually does not.

    **Regression cases are permanent.** A case that failed in March is still
    being run in November, because the alternative — re-deriving the case set
    each round from whatever is currently interesting — is how a fixed bug
    comes back unnoticed.

    **Novelty is what is reported.** Total failures going down is also what
    happens when somebody quietly drops the hard cases. New failures per round
    cannot be gamed that way, and a run of rounds with no novel failures is the
    only honest evidence that a harness has stopped finding things — which is
    a reason to write harder cases, not a reason to celebrate.
    """

    rounds: list[Round] = field(default_factory=list)
    #: Every case id that has ever failed. The regression set.
    regressions: set[str] = field(default_factory=set)

    def round(self, *, cases: int, verdict: Verdict,
              failures: Sequence[str] = (), label: str = "") -> Round:
        """Record a round, and work out what is new in it."""
        novel = tuple(sorted(set(failures) - self.regressions))
        entry = Round(index=len(self.rounds), cases=cases, verdict=verdict,
                      failures=tuple(failures), novel=novel, label=label)
        self.rounds.append(entry)
        self.regressions |= set(failures)
        return entry

    def next_cases(self, base: Sequence[str] = ()) -> tuple[str, ...]:
        """What the next round should run: the base set plus every regression.

        Order is stable and regressions come last, so a truncated run still
        covers the base — a harness that runs out of budget should lose the
        cases nobody has ever failed, not the ones somebody fixed.
        """
        seen: dict[str, None] = {}
        for case in list(base) + sorted(self.regressions):
            seen.setdefault(case, None)
        return tuple(seen)

    @property
    def converging(self) -> bool:
        """Two consecutive rounds with nothing new. Not the same as 'done'."""
        return (len(self.rounds) >= 2
                and not self.rounds[-1].novel and not self.rounds[-2].novel)

    def text(self) -> str:
        if not self.rounds:
            return "no rounds yet"
        lines = [f"{len(self.rounds)} round(s), "
                 f"{len(self.regressions)} case(s) in the regression set", ""]
        for entry in self.rounds:
            name = entry.label or f"round {entry.index}"
            lines.append(f"  {name:<16} {entry.cases:>4} cases  "
                         f"{entry.verdict.state:<12} {entry.verdict.score:.3f}  "
                         f"{len(entry.failures):>3} failed, "
                         f"{len(entry.novel):>3} new")
        lines.append("")
        if self.converging:
            lines.append("  Two rounds with nothing new. The harness has "
                         "stopped finding things, which is a reason to write "
                         "harder cases rather than a result.")
        else:
            newest = self.rounds[-1]
            if newest.novel:
                lines.append(f"  Still finding things: {len(newest.novel)} new "
                             f"failure(s) last round "
                             f"({', '.join(newest.novel[:5])}"
                             f"{'...' if len(newest.novel) > 5 else ''}).")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"rounds": [r.to_dict() for r in self.rounds],
                "regressions": sorted(self.regressions),
                "converging": self.converging}

    # --- the other half of the loop ---------------------------------------

    def fold_into(self, evidence: Any, route: Mapping[str, str],
                  context: str = "global", *,
                  observation: Any = None) -> Any:
        """Feed a round's outcome into route evidence.

        This is what closes the loop. The same observation that graded the
        *output* also tells the search which *route* produced it, so the next
        run starts from what the last one learned instead of from the same
        prior. Without this the harness improves the system and forgets how it
        was built.

        Takes an evidence store and the route that was evaluated; returns the
        store so it can be chained.

        The store is asked what record type it accepts rather than having one
        imported for it. An earlier version imported `browsergraph.evidence`
        inside this function, which felt like it avoided the dependency and did
        not: the call still failed on any machine without browsergraph
        installed, just later and with a worse message. A store advertises
        `observation_type`; anything else can be passed explicitly through
        `observation`.
        """
        factory = observation or getattr(evidence, "observation_type", None)
        if factory is None:
            raise TypeError(
                f"{type(evidence).__name__} does not advertise an "
                f"`observation_type`, so there is no way to know what record "
                f"it accepts. Pass `observation=` with the record type, or "
                f"set `observation_type` on the store.")

        if not self.rounds:
            return evidence
        newest = self.rounds[-1]
        # `ok` is PASS only. A provisional verdict must not teach the search
        # that the route worked — the whole point of PROVISIONAL is that
        # nobody knows yet, and an optimiser fed "probably fine" converges on
        # whichever route was least thoroughly checked.
        ok = newest.verdict.state == PASS
        chosen = tuple(route[stage] for stage in sorted(route))
        for candidate in chosen:
            evidence.observe(factory(
                candidate=candidate, context=context, ok=ok,
                quality=newest.verdict.score, route=chosen,
                run=newest.label or f"round {newest.index}"))
        return evidence


def from_scores(scores: Sequence[Mapping[str, Any]], *,
                key: str = "score") -> Ledger:
    """Build a ledger from graded records, discharging what the data supports.

    A convenience with a sharp edge, and the edge is deliberate: it discharges
    `provenance` and `sample_size` because it can compute them, and leaves the
    controls **outstanding** because no arrangement of the scores you already
    have can tell you whether a broken system would have scored differently.
    That is the point — the obligations this cannot discharge are exactly the
    ones that require you to have run something extra.
    """
    ledger = Ledger.standard()
    values = [float(record.get(key, 0.0)) for record in scores]
    ledger.record(check_provenance(scores))
    ledger.record(check_sample_size(values))
    slices: dict[str, list[float]] = {}
    for record in scores:
        name = str(record.get("slice", ""))
        if name:
            slices.setdefault(name, []).append(float(record.get(key, 0.0)))
    if slices:
        ledger.record(check_slices(slices))
    return ledger


def compare(before: Verdict, after: Verdict) -> str:
    """Two verdicts, and whether they may be compared at all.

    The digest check comes first and is the useful part. Two scores produced
    under different obligations are two different claims, and putting them in
    one table is the mistake that makes evaluation reports untrustworthy over
    time — the standard slips a little each quarter and every number stays
    comparable to the one before it.
    """
    if before.digest != after.digest:
        return (f"not comparable: these were held to different standards "
                f"({before.digest[:8]} against {after.digest[:8]}). Re-run the "
                f"earlier one under the current ledger, or say plainly that "
                f"the standard changed.")
    delta = after.score - before.score
    direction = "up" if delta > 0 else "down" if delta < 0 else "unchanged"
    return (f"{before.state} {before.score:.3f} -> {after.state} "
            f"{after.score:.3f} ({direction} {abs(delta):.3f}), same standard "
            f"[{after.digest[:12]}]")


__all__ = [
    "FAIL", "PASS", "PROVISIONAL", "STANDARD",
    "Discharge", "Ledger", "Loop", "Obligation", "Round", "Verdict",
    "check_coverage", "check_grader_agreement", "check_negative_control",
    "check_positive_control", "check_provenance", "check_replication",
    "check_sample_size", "check_slices", "cohen_kappa", "compare",
    "from_scores", "interval",
]
