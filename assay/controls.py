"""Controls: the checks that are about the harness, not about the system.

Every evaluation measures a system. Almost none of them measure the
*measurement*, and that is where the expensive mistakes live. A harness that
cannot tell a working system from a broken one will report both as working, for
months, and every number it produced in that time was noise wearing a decimal
point.

A control is a system whose score you already know. You run it through the
harness and check that the harness agrees with what you already know:

    null       something that should score at chance. If it scores well, the
               harness is measuring something other than quality.
    negative   something deliberately broken. If it passes, the harness cannot
               detect breakage — which is the only thing it was hired for.
    positive   something known to be good. If it fails, the harness is wrong in
               the other direction and will reject work that was fine.
    shuffle    the real system with the labels shuffled. Should collapse to
               chance. If it does not, something is leaking.

    from assay import controls

    c = controls.Controls()
    c.add(controls.null_control(observed=0.86, chance=0.83))
    c.add(controls.negative_control(broken=0.85, real=0.86))
    c.add(controls.positive_control(observed=0.86, floor=0.90))
    print(c.verdict(score=0.86).text())

    # FAIL — 0.860
    #   FAIL negative  a deliberately broken system scored 0.850 against 0.860
    #                  for the real one — a gap of 0.010. This harness cannot
    #                  tell them apart, so its other numbers mean nothing
    #   FAIL null      scored 0.860 where chance is 0.830 (+0.030); the harness
    #                  is rewarding something that is not correctness
    #   FAIL positive  a known-good system scored 0.860, below the 0.900 floor
    #   ---- absent    no shuffle control was run

## The rule this module exists to enforce

**A harness with no controls cannot return better than PROVISIONAL.** Not
because provisional is a punishment, but because "we did not check whether this
harness works" is the accurate description, and having a word for it is what
stops it being rendered as a pass. `Controls.verdict()` on an empty set says so.

## Could not check is not checked and failed

A control returns `ok=None` when it could not be evaluated — a single-class
sample, an empty run, a chance level that is undefined. That is *outstanding*,
never *failed*. Reporting an unevaluable control as a failure blames the system
for the evaluation's own sampling, and it is the fastest way to teach a team to
ignore this module.

## Chance is not 1/k

The most common way to make a null control lie is to compare accuracy against
`1/number_of_classes` when the classes are imbalanced. On a set that is 83% one
class, the majority baseline is 0.83 and uniform chance is 0.50 — a system
scoring 0.84 looks excellent against the second number and is worthless against
the first. `chance_level()` makes you choose, and defaults to the majority
baseline because that is the one that can embarrass you.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: What kinds of control this module knows how to build. A harness does not
#: need all four; it needs at least one, and the report says which are absent.
KINDS = ("null", "negative", "positive", "shuffle")

#: The order controls are reported in — by how bad the news is, not
#: alphabetically, so the reader meets the worst finding first.
_REPORT_ORDER = {"negative": 0, "null": 1, "shuffle": 2, "positive": 3}


@dataclass(frozen=True)
class Control:
    """One check on the harness, with the arithmetic that produced it.

    `ok` is deliberately three-valued. `True` and `False` are the two answers a
    control can give; `None` means the control did not run, which is a third
    thing and must not collapse into either.
    """

    kind: str
    #: What the reader should have expected, in words. Kept next to the number
    #: because a control without its expectation is an unlabelled measurement.
    expectation: str
    observed: float | None = None
    ok: bool | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(
                f"{self.kind!r} is not a kind of control. Known kinds: "
                f"{', '.join(KINDS)}. A check that is not one of these is a "
                f"check on the system, not on the harness — record it as an "
                f"obligation instead.")
        if not self.detail:
            raise ValueError(
                "a control needs a detail line. The number alone cannot be "
                "acted on, and a control nobody acts on is decoration.")

    @property
    def checked(self) -> bool:
        """Did this control actually run? Distinct from whether it passed."""
        return self.ok is not None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "expectation": self.expectation,
                "observed": self.observed, "ok": self.ok,
                "checked": self.checked, "detail": self.detail}


def chance_level(labels: Sequence[Any], *, kind: str = "majority") -> float:
    """The score a system that knows nothing would get.

    `majority` — always answer with the commonest label. This is the honest
    baseline for accuracy on imbalanced data and it is usually much higher than
    people expect.

    `uniform` — guess evenly among the labels seen. Only correct when the
    classes really are balanced, and flattering when they are not.

    Raises rather than guessing when there is nothing to count, because a
    silently-zero chance level makes every null control pass.
    """
    if kind not in ("majority", "uniform"):
        raise ValueError(f"chance kind must be 'majority' or 'uniform', "
                         f"not {kind!r}")
    rows = list(labels)
    if not rows:
        raise ValueError("no labels, so there is no chance level. An empty "
                         "sample cannot establish a baseline.")
    counts: dict[Any, int] = {}
    for label in rows:
        counts[label] = counts.get(label, 0) + 1
    if kind == "uniform":
        return 1.0 / len(counts)
    return max(counts.values()) / len(rows)


def null_control(*, observed: float, chance: float,
                 tolerance: float = 0.02) -> Control:
    """Something that should score at chance. Fails if it scored above it.

    The tolerance is one-sided on purpose. A null scoring *below* chance is
    odd but not evidence that the harness is broken; a null scoring *above* it
    means the harness is rewarding something other than being right.
    """
    margin = observed - chance
    if margin > tolerance:
        return Control(
            "null", f"at chance ({chance:.3f} ± {tolerance:.3f})",
            observed, False,
            f"scored {observed:.3f} where chance is {chance:.3f} "
            f"(+{margin:.3f}); the harness is rewarding something that is not "
            f"correctness")
    return Control("null", f"at chance ({chance:.3f} ± {tolerance:.3f})",
                   observed, True,
                   f"scored {observed:.3f} against a chance level of "
                   f"{chance:.3f}, as it should")


def negative_control(*, broken: float, real: float,
                     margin: float = 0.02) -> Control:
    """A deliberately broken system. Fails if the harness could not tell.

    This is the single most informative control and the one least often run.
    The question is not whether the broken system scored badly in absolute
    terms — it is whether the harness *separated* it from the real one.
    """
    gap = real - broken
    if gap < margin:
        return Control(
            "negative", f"at least {margin:.3f} below the real system",
            broken, False,
            f"a deliberately broken system scored {broken:.3f} against "
            f"{real:.3f} for the real one — a gap of {gap:.3f}. This harness "
            f"cannot tell them apart, so its other numbers mean nothing")
    return Control("negative", f"at least {margin:.3f} below the real system",
                   broken, True,
                   f"broken {broken:.3f} vs real {real:.3f}; the harness "
                   f"separates them by {gap:.3f}")


def positive_control(*, observed: float, floor: float = 0.9) -> Control:
    """A known-good system. Fails if the harness rejected it.

    The mirror of the negative control, and the one that catches a metric
    measuring the wrong thing. A harness nothing can pass is not strict, it is
    broken, and it will be quietly disabled by whoever it blocks first.
    """
    if observed < floor:
        return Control(
            "positive", f"at or above {floor:.3f}", observed, False,
            f"a known-good system scored {observed:.3f}, below the "
            f"{floor:.3f} floor. Before blaming the system, check that the "
            f"metric measures what the floor was set for")
    return Control("positive", f"at or above {floor:.3f}", observed, True,
                   f"a known-good system scored {observed:.3f}, clearing the "
                   f"{floor:.3f} floor")


def shuffle_control(*, shuffled: float, chance: float,
                    tolerance: float = 0.05) -> Control:
    """The real system with its labels shuffled. Should collapse to chance.

    When it does not, the score is coming from somewhere other than the
    relationship you think you are measuring: an id that encodes the answer, a
    row order that survived the shuffle, a leak from the split.
    """
    margin = shuffled - chance
    if margin > tolerance:
        return Control(
            "shuffle", f"collapses to chance ({chance:.3f} ± {tolerance:.3f})",
            shuffled, False,
            f"with the labels shuffled the score was {shuffled:.3f}, "
            f"{margin:.3f} above chance. Something other than the labels is "
            f"carrying the signal — look for a leak before reading any other "
            f"number here")
    return Control("shuffle",
                   f"collapses to chance ({chance:.3f} ± {tolerance:.3f})",
                   shuffled, True,
                   f"shuffled labels scored {shuffled:.3f} against a chance "
                   f"level of {chance:.3f}; no leak visible from here")


def could_not_check(kind: str, why: str) -> Control:
    """Record a control that could not be evaluated, without calling it a fail.

    Use this rather than omitting the control. An absent control and an
    unevaluable one look identical in a report that only lists what ran, and
    they are not the same: one is an oversight, the other is a fact about the
    data that the next person needs.
    """
    return Control(kind, "could not be established", None, None,
                   f"not checked: {why}")


@dataclass(frozen=True)
class ControlVerdict:
    """PASS, PROVISIONAL or FAIL, and why — never a bare boolean."""

    state: str
    score: float | None
    controls: tuple[Control, ...] = ()

    @property
    def ok(self) -> bool:
        """False for PROVISIONAL as well as FAIL.

        The middle state is not a soft pass. A number nobody may act on is not
        a number you may act on, and code that reads `.ok` is about to act.
        """
        return self.state == "PASS"

    @property
    def failed(self) -> tuple[Control, ...]:
        return tuple(c for c in self.controls if c.ok is False)

    @property
    def outstanding(self) -> tuple[Control, ...]:
        return tuple(c for c in self.controls if c.ok is None)

    @property
    def missing(self) -> tuple[str, ...]:
        """Kinds of control nobody attempted. Absence reported as absence."""
        present = {c.kind for c in self.controls}
        return tuple(k for k in KINDS if k not in present)

    def text(self) -> str:
        head = (f"{self.state} — {self.score:.3f}" if self.score is not None
                else self.state)
        lines = [head]
        for control in sorted(self.controls,
                              key=lambda c: _REPORT_ORDER.get(c.kind, 9)):
            mark = {True: "ok", False: "FAIL", None: "----"}[control.ok]
            lines.append(f"  {mark:<4} {control.kind:<9} {control.detail}")
        if self.missing:
            lines.append(f"  ---- absent   no {', '.join(self.missing)} "
                         f"control was run")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"state": self.state, "score": self.score, "ok": self.ok,
                "controls": [c.to_dict() for c in self.controls],
                "missing": list(self.missing)}


@dataclass
class Controls:
    """A set of controls and the verdict they license.

    Mutable on purpose: controls arrive one at a time as the harness runs, and
    forcing them to be collected up front is how the awkward one gets dropped.
    """

    controls: list[Control] = field(default_factory=list)

    def add(self, control: Control) -> Controls:
        self.controls.append(control)
        return self

    def extend(self, controls: Sequence[Control]) -> Controls:
        self.controls.extend(controls)
        return self

    def verdict(self, score: float | None = None) -> ControlVerdict:
        """PASS only when at least one control ran and none of them failed.

        The `at least one` clause is the point of the module. An empty set of
        controls is PROVISIONAL, not PASS — nothing has been established about
        this harness, and silence is not evidence.
        """
        rows = tuple(self.controls)
        if any(c.ok is False for c in rows):
            state = "FAIL"
        elif not any(c.checked for c in rows):
            state = "PROVISIONAL"
        else:
            state = "PASS"
        return ControlVerdict(state, score, rows)

    def text(self, score: float | None = None) -> str:
        return self.verdict(score).text()

    def to_dict(self) -> dict:
        return {"controls": [c.to_dict() for c in self.controls]}


def discharge_into(ledger: Any, verdict: ControlVerdict) -> Any:
    """Write a control verdict into an `obligations.Ledger`.

    Kept as a function taking `Any` rather than a method on either type: the
    two modules are usable apart, and a hard import in either direction would
    make the smaller one carry the larger one for no reason.

    Only the controls that actually ran are written. An outstanding control is
    left outstanding in the ledger too, which is exactly what it is.
    """
    for control in verdict.controls:
        obligation = {"null": "negative_control", "negative": "negative_control",
                      "positive": "positive_control",
                      "shuffle": "holdout"}.get(control.kind)
        if obligation is None or control.ok is None:
            continue
        if control.ok:
            ledger.discharge(obligation, control.detail, kind=control.kind)
        else:
            ledger.fail(obligation, control.detail, kind=control.kind)
    return ledger


def summarise(by_system: Mapping[str, float], *,
              controls: Controls) -> str:
    """Rank systems, but refuse to crown one if the controls did not hold.

    A leaderboard printed under a failed negative control is the specific
    artefact this whole module exists to prevent: it is maximally persuasive
    and completely uninformative.
    """
    verdict = controls.verdict()
    rows = sorted(by_system.items(), key=lambda kv: -kv[1])
    lines = [f"{name:<24} {score:.3f}" for name, score in rows]
    if verdict.state == "FAIL":
        lines.append("")
        lines.append("No ranking is reported: a control failed, so the "
                     "ordering above is not evidence of anything.")
        lines.append(verdict.text())
    elif verdict.state == "PROVISIONAL":
        lines.append("")
        lines.append("PROVISIONAL — no control was run, so nothing is known "
                     "about whether this harness can tell good from bad.")
    return "\n".join(lines)


__all__ = [
    "KINDS", "Control", "ControlVerdict", "Controls", "chance_level",
    "could_not_check", "discharge_into", "negative_control", "null_control",
    "positive_control", "shuffle_control", "summarise",
]
