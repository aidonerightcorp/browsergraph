"""Audit a judge: is it measuring quality, or is it measuring something else?

If you ship a feature built on a language model, you almost certainly have a
judge — a model that reads an output and says whether it was good. It probably
produces a number every day, that number probably goes in a dashboard, and it
has probably never been checked against a person.

This module checks it. Nothing here needs a graph, a pipeline, or any other
part of this project; it takes lists and returns a report.

    from assay import judge

    report = judge.check(model=judge_verdicts, human=human_verdicts)
    print(report.text())

    # BELOW_CHANCE — kappa -0.15 on 120 items
    #   raw agreement 70.0%, but chance alone gives 73.9% on this
    #   distribution. The judge agrees with people less often than a coin
    #   weighted to the commonest answer would.

## The four ways a judge is wrong while looking right

**It agrees at chance.** Raw agreement is flattering on an imbalanced set: a
judge that always says "good" agrees 80% of the time with humans on data that is
80% good. `check()` reports kappa alongside the raw number, and they routinely
disagree about whether the judge works.

**It scores length.** A judge that prefers longer answers ranks systems
plausibly, correlates with human preference on many datasets, and is measuring
nothing you wanted. `length_bias()` finds it with a rank correlation.

**It scores position.** A pairwise judge asked "is A or B better" frequently
prefers whichever came first. `position_bias()` runs both orders and reports
how often the answer flips.

**The rubric decides, not the judge.** This is the one that surprises people:
the same judge on the same responses swings from strong agreement to worse than
chance depending only on how the question was phrased. `rubric_sensitivity()`
measures the spread, and a judge whose verdict depends more on its rubric than
on its input is not a measurement instrument.

## Could not check

`CANNOT_CHECK` is a verdict, not an error. A single-class human sample, fewer
labels than the floor, an empty run — each produces a report whose `ok` is
False and whose reason says what to fix. Reporting an unevaluable sample as a
failed judge convicts the judge of the evaluation's own sampling, and it is the
fastest way to get this tool switched off.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from assay.obligations import cohen_kappa

#: Below this, a judge's agreement with people is not usable as a measurement.
#: The convention, not a law: 0.4 is the low end of "moderate" in the usual
#: reading of kappa, and a judge sitting at it is one whose disagreements you
#: read rather than one you trust.
USABLE_KAPPA = 0.4

#: Fewer human labels than this and the interval around kappa is wider than the
#: distinctions anybody wants to draw with it.
MIN_LABELS = 30

VERDICTS = ("USABLE", "WEAK", "AT_CHANCE", "BELOW_CHANCE", "CANNOT_CHECK")


def _raw_agreement(a: Sequence[Any], b: Sequence[Any]) -> float:
    if not a:
        return 0.0
    return sum(1 for x, y in zip(a, b, strict=True) if x == y) / len(a)


def _expected_agreement(a: Sequence[Any], b: Sequence[Any]) -> float:
    """Agreement two independent raters would reach by luck alone."""
    if not a:
        return 0.0
    labels = set(a) | set(b)
    return sum((sum(1 for x in a if x == label) / len(a))
               * (sum(1 for y in b if y == label) / len(b))
               for label in labels)


@dataclass(frozen=True)
class JudgeReport:
    """What a judge is worth, with the arithmetic kept next to the claim."""

    verdict: str
    n: int = 0
    kappa: float = 0.0
    raw: float = 0.0
    expected: float = 0.0
    reason: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"{self.verdict!r} is not one of {VERDICTS}")

    @property
    def ok(self) -> bool:
        """True only for USABLE. `CANNOT_CHECK` is not a pass."""
        return self.verdict == "USABLE"

    @property
    def checked(self) -> bool:
        """Did the check run at all? Separate from whether it passed."""
        return self.verdict != "CANNOT_CHECK"

    def text(self) -> str:
        if not self.checked:
            return f"CANNOT_CHECK\n  {self.reason}"
        head = f"{self.verdict} — kappa {self.kappa:+.3f} on {self.n} items"
        return f"{head}\n  {self.reason}"

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "ok": self.ok, "n": self.n,
                "kappa": round(self.kappa, 4), "raw": round(self.raw, 4),
                "expected": round(self.expected, 4), "reason": self.reason,
                "detail": dict(self.detail)}


def check(model: Sequence[Any], human: Sequence[Any], *,
          floor: float = USABLE_KAPPA, min_labels: int = MIN_LABELS
          ) -> JudgeReport:
    """Does this judge agree with people more than chance would?

    Returns a verdict rather than a number, because a number invites a
    threshold argument and the interesting cases are the ones where the number
    cannot be computed at all.
    """
    if len(model) != len(human):
        raise ValueError(
            f"{len(model)} judge verdicts against {len(human)} human ones. "
            f"These have to be the same items in the same order — if they are "
            f"not aligned, every number below is meaningless rather than wrong.")
    if not human:
        return JudgeReport("CANNOT_CHECK", reason=(
            "no human labels. The judge has never been compared with a person, "
            "so the evaluation measures the judge's preferences and nobody has "
            "looked at whether those are the ones you wanted."))
    if len(set(human)) < 2:
        only = next(iter(set(human)))
        return JudgeReport("CANNOT_CHECK", n=len(human), reason=(
            f"all {len(human)} human labels are {only!r}. A single-class "
            f"sample cannot validate a judge — kappa is undefined and the 0.0 "
            f"that falls out is a fact about the sample, not about the judge. "
            f"Label some items you expect to fail."),
            detail={"classes": sorted(map(str, set(human)))})
    if len(human) < min_labels:
        return JudgeReport("CANNOT_CHECK", n=len(human), reason=(
            f"only {len(human)} human labels; {min_labels} is the floor. "
            f"Below it the uncertainty around kappa is wider than the "
            f"distinction you are trying to draw with it."),
            detail={"min_labels": min_labels})

    kappa = cohen_kappa(model, human)
    raw = _raw_agreement(model, human)
    expected = _expected_agreement(model, human)
    detail = {"floor": floor, "labels": sorted(map(str, set(human)))}

    if kappa < -0.05:
        return JudgeReport(
            "BELOW_CHANCE", len(human), kappa, raw, expected,
            f"raw agreement {raw:.1%}, but chance alone gives {expected:.1%} "
            f"on this distribution. The judge agrees with people less often "
            f"than a coin weighted to the commonest answer would — this is not "
            f"a weak signal, it is a signal pointing the wrong way.", detail)
    if kappa < 0.05:
        return JudgeReport(
            "AT_CHANCE", len(human), kappa, raw, expected,
            f"raw agreement {raw:.1%} is almost exactly the {expected:.1%} "
            f"that chance gives on this distribution. The judge is not "
            f"measuring what the humans were measuring.", detail)
    if kappa < floor:
        return JudgeReport(
            "WEAK", len(human), kappa, raw, expected,
            f"kappa {kappa:.3f} is above chance but below the {floor:.2f} "
            f"floor. Usable for spotting large regressions, not for ranking "
            f"systems that are close.", detail)
    return JudgeReport(
        "USABLE", len(human), kappa, raw, expected,
        f"kappa {kappa:.3f} on {len(human)} human-labelled items "
        f"(raw agreement {raw:.1%} against {expected:.1%} by chance).", detail)


@dataclass(frozen=True)
class RubricReport:
    """How much of the verdict came from the rubric rather than the input."""

    rows: tuple[tuple[str, float], ...] = ()
    reason: str = ""

    @property
    def checked(self) -> bool:
        return len(self.rows) >= 2

    @property
    def spread(self) -> float:
        if not self.rows:
            return 0.0
        values = [k for _, k in self.rows]
        return max(values) - min(values)

    @property
    def ok(self) -> bool:
        """Stable enough that the rubric is not doing the deciding.

        A spread wider than the usable floor means swapping the wording moves
        the judge further than the difference between a good and a bad system.
        """
        return self.checked and self.spread < USABLE_KAPPA

    def text(self) -> str:
        if not self.checked:
            return f"CANNOT_CHECK\n  {self.reason}"
        lines = [f"{'STABLE' if self.ok else 'RUBRIC-DEPENDENT'} — "
                 f"kappa spans {self.spread:.3f} across "
                 f"{len(self.rows)} rubrics"]
        for name, kappa in sorted(self.rows, key=lambda kv: -kv[1]):
            lines.append(f"  {kappa:+.3f}  {name}")
        if not self.ok:
            best, worst = max(self.rows, key=lambda kv: kv[1]), min(
                self.rows, key=lambda kv: kv[1])
            lines.append("")
            lines.append(
                f"Same judge, same responses: {best[1]:+.3f} under "
                f"{best[0]!r} and {worst[1]:+.3f} under {worst[0]!r}. The "
                f"rubric is deciding more than the input is.")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"rows": [{"rubric": n, "kappa": round(k, 4)}
                         for n, k in self.rows],
                "spread": round(self.spread, 4), "ok": self.ok,
                "checked": self.checked, "reason": self.reason}


def rubric_sensitivity(runs: Mapping[str, Sequence[Any]],
                       human: Sequence[Any]) -> RubricReport:
    """Same judge, same items, different wording. How much does it matter?

    `runs` maps a rubric's name to the verdicts the judge gave under it. Any
    rubric whose verdicts cannot be scored against the human sample is dropped
    from the table rather than entered as zero — a rubric that could not be
    checked is not a rubric that scored nothing.
    """
    if len(set(human)) < 2:
        return RubricReport(reason=(
            "the human sample is single-class, so no rubric can be scored "
            "against it. Nothing here is about the rubrics."))
    rows = []
    for name, verdicts in runs.items():
        if len(verdicts) != len(human):
            continue
        rows.append((name, cohen_kappa(list(verdicts), list(human))))
    if len(rows) < 2:
        return RubricReport(tuple(rows), reason=(
            f"{len(rows)} scoreable rubric(s); at least two are needed before "
            f"'the rubric decides' is a question with an answer."))
    return RubricReport(tuple(rows))


@dataclass(frozen=True)
class BiasReport:
    """One way a judge can be right for the wrong reason."""

    kind: str
    statistic: float = 0.0
    ok: bool | None = None
    reason: str = ""

    @property
    def checked(self) -> bool:
        return self.ok is not None

    def text(self) -> str:
        if not self.checked:
            return f"CANNOT_CHECK  {self.kind}\n  {self.reason}"
        return f"{'ok  ' if self.ok else 'FAIL'}  {self.kind}\n  {self.reason}"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "statistic": round(self.statistic, 4),
                "ok": self.ok, "checked": self.checked, "reason": self.reason}


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Rank correlation. Ties get average ranks, which matters for scores.

    Judges emit 1-5 far more often than they emit distinct floats, so a
    tie-naive implementation would report a correlation that is mostly an
    artefact of how the ties happened to be ordered.
    """
    def ranks(values: Sequence[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            average = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = average
            i = j + 1
        return out

    if len(xs) < 3:
        return 0.0
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def length_bias(scores: Sequence[float], texts: Sequence[str], *,
                limit: float = 0.5) -> BiasReport:
    """Is the judge scoring the answer, or scoring how long the answer is?

    A rank correlation, not a linear one: judges emit small integers and the
    relationship is monotone rather than straight.
    """
    if len(scores) != len(texts):
        raise ValueError(f"{len(scores)} scores against {len(texts)} texts")
    if len(scores) < 3:
        return BiasReport("length", reason=(
            f"{len(scores)} items; a correlation on fewer than three is not a "
            f"correlation."))
    if len(set(scores)) < 2:
        return BiasReport("length", reason=(
            "the judge gave every item the same score, so there is no "
            "variation for length to explain. That is its own finding."))
    rho = _spearman([float(s) for s in scores], [float(len(t)) for t in texts])
    if abs(rho) >= limit:
        direction = "longer" if rho > 0 else "shorter"
        return BiasReport("length", rho, False, (
            f"score and answer length correlate at rho {rho:+.2f} — the judge "
            f"systematically prefers {direction} answers. A system that pads "
            f"will beat one that does not, and the leaderboard will look "
            f"perfectly reasonable."))
    return BiasReport("length", rho, True, (
        f"score and length correlate at rho {rho:+.2f}, below the "
        f"{limit:.2f} limit."))


def position_bias(first: Sequence[Any], second: Sequence[Any], *,
                  limit: float = 0.1) -> BiasReport:
    """Ask the same pairwise question both ways round. How often does it flip?

    `first` holds the verdicts with A shown first, `second` the verdicts for the
    same pairs with B shown first, already mapped back to the same vocabulary.
    Perfect consistency means every pair matches; a judge reading position
    rather than content will agree with itself far less often than that.
    """
    if len(first) != len(second):
        raise ValueError(f"{len(first)} against {len(second)} — position bias "
                         f"needs the same pairs in both orders")
    if len(first) < 3:
        return BiasReport("position", reason=(
            f"{len(first)} pairs; too few to distinguish a flip from noise."))
    flips = sum(1 for a, b in zip(first, second, strict=True) if a != b)
    rate = flips / len(first)
    if rate > limit:
        return BiasReport("position", rate, False, (
            f"{flips} of {len(first)} pairs ({rate:.1%}) changed answer when "
            f"the two candidates swapped places. The judge is reading order "
            f"as well as content, so any A/B result depends on which system "
            f"you happened to list first."))
    return BiasReport("position", rate, True, (
        f"{flips} of {len(first)} pairs ({rate:.1%}) flipped on reorder, "
        f"within the {limit:.0%} limit."))


def self_preference(by_author: Mapping[str, Sequence[float]], *,
                    judge_family: str, limit: float = 0.1) -> BiasReport:
    """Does the judge score its own family's output higher than others do?

    Needs at least one other author to compare against; with a single author
    there is no contrast and the honest answer is that it could not be checked.
    """
    if judge_family not in by_author:
        return BiasReport("self_preference", reason=(
            f"no scores recorded for {judge_family!r}, so there is nothing to "
            f"compare the judge's own family against."))
    others = {k: v for k, v in by_author.items() if k != judge_family}
    if not others:
        return BiasReport("self_preference", reason=(
            "only one author in the sample; self-preference is a comparison "
            "and there is nothing to compare with."))
    own = list(by_author[judge_family])
    rest = [s for v in others.values() for s in v]
    if not own or not rest:
        return BiasReport("self_preference", reason=(
            "one side of the comparison is empty."))
    gap = (sum(own) / len(own)) - (sum(rest) / len(rest))
    if gap > limit:
        return BiasReport("self_preference", gap, False, (
            f"the judge scores {judge_family} output {gap:+.3f} above "
            f"everyone else's. Some of that may be real quality; none of it "
            f"can be separated from preference by this measurement alone."))
    return BiasReport("self_preference", gap, True, (
        f"{judge_family} scores {gap:+.3f} against the rest, within the "
        f"{limit:.2f} limit."))


@dataclass(frozen=True)
class Audit:
    """Everything that could be checked, and one verdict over the lot."""

    agreement: JudgeReport
    rubric: RubricReport | None = None
    biases: tuple[BiasReport, ...] = ()

    @property
    def failures(self) -> tuple[str, ...]:
        out = []
        if self.agreement.checked and not self.agreement.ok:
            out.append(f"agreement ({self.agreement.verdict})")
        if self.rubric is not None and self.rubric.checked and not self.rubric.ok:
            out.append("rubric sensitivity")
        out.extend(b.kind for b in self.biases if b.checked and not b.ok)
        return tuple(out)

    @property
    def unchecked(self) -> tuple[str, ...]:
        out = []
        if not self.agreement.checked:
            out.append("agreement")
        if self.rubric is not None and not self.rubric.checked:
            out.append("rubric sensitivity")
        out.extend(b.kind for b in self.biases if not b.checked)
        return tuple(out)

    @property
    def verdict(self) -> str:
        """FAIL beats PROVISIONAL beats PASS.

        A judge with one confirmed failure is not rescued by the checks that
        could not run, and a judge with no confirmed failure but nothing
        checked has not been shown to work.
        """
        if self.failures:
            return "FAIL"
        if not self.agreement.checked:
            return "PROVISIONAL"
        return "PASS"

    @property
    def ok(self) -> bool:
        return self.verdict == "PASS"

    def text(self) -> str:
        lines = [f"JUDGE AUDIT — {self.verdict}", ""]
        lines.append(self.agreement.text())
        if self.rubric is not None:
            lines.append("")
            lines.append(self.rubric.text())
        for bias in self.biases:
            lines.append("")
            lines.append(bias.text())
        if self.failures:
            lines.append("")
            lines.append(f"Failed: {', '.join(self.failures)}.")
        if self.unchecked:
            lines.append(f"Not checked: {', '.join(self.unchecked)} — absent, "
                         f"not passed.")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "ok": self.ok,
                "agreement": self.agreement.to_dict(),
                "rubric": self.rubric.to_dict() if self.rubric else None,
                "biases": [b.to_dict() for b in self.biases],
                "failures": list(self.failures),
                "unchecked": list(self.unchecked)}


def audit(*, model: Sequence[Any], human: Sequence[Any],
          rubrics: Mapping[str, Sequence[Any]] | None = None,
          scores: Sequence[float] | None = None,
          texts: Sequence[str] | None = None,
          orders: tuple[Sequence[Any], Sequence[Any]] | None = None,
          by_author: Mapping[str, Sequence[float]] | None = None,
          judge_family: str = "") -> Audit:
    """Run every check the inputs allow, and refuse to pass on silence.

    Only `model` and `human` are required. Everything else is a check you can
    afford to run; the report names the ones that did not run rather than
    quietly scoring the judge on the ones that did.
    """
    agreement = check(model, human)
    rubric = rubric_sensitivity(rubrics, human) if rubrics else None
    biases: list[BiasReport] = []
    if scores is not None and texts is not None:
        biases.append(length_bias(scores, texts))
    if orders is not None:
        biases.append(position_bias(orders[0], orders[1]))
    if by_author is not None and judge_family:
        biases.append(self_preference(by_author, judge_family=judge_family))
    return Audit(agreement, rubric, tuple(biases))


__all__ = [
    "MIN_LABELS", "USABLE_KAPPA", "VERDICTS", "Audit", "BiasReport",
    "JudgeReport", "RubricReport", "audit", "check", "length_bias",
    "position_bias", "rubric_sensitivity", "self_preference",
]
