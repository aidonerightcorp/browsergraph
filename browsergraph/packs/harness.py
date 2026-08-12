"""An evaluation harness where the controls are steps in the graph.

Most harnesses are a loop over cases, a grader, and a mean. This one is the same
loop with the checks that make the mean mean something moved *into the shape*,
so they cannot be the thing that gets skipped when the deadline moves.

    from browsergraph import packs
    print(packs.get("harness").solve().text())

The demonstration, and it is the reason to run this rather than read it: **four
graders, and one of them is a fraud.**

    grade.overlap    token overlap with the reference
    grade.contains   the reference appears in the answer
    grade.exact      exact match, which is brittle and honest about it
    grade.length     longer answers score higher

`grade.length` is not a straw man. It is the shape of a great many real graders —
anything that rewards fluency, completeness or confident phrasing without
checking the claim — and on the surface it behaves beautifully: it produces a
number, the number moves when the system changes, and it ranks the three systems
in a plausible order.

The control cases are what expose it. Each one is a case with an answer
*injected*: a `known_good` case is given the reference answer, a `known_bad`
case is given a long, fluent, wrong one. A grader that cannot separate those two
cannot separate anything, and `duecare.check_negative_control` says so in the
verdict rather than in a footnote:

    FAIL — 0.742
      failed: negative_control — a deliberately wrong answer scored 0.887
              against 0.410: a gap of -0.477 does not clear 0.150.

The system under test is also a candidate, because "which system" and "which
grader" are two choices and pretending only the first one exists is how a
harness ends up measuring itself:

    run.keyword   picks the sentence that shares the most words with the question
    run.first     always returns the first sentence
    run.empty     returns nothing at all

`run.empty` is the negative control at the *system* level. A harness whose score
does not collapse on it is not measuring the system.

Everything is standard library and deterministic. There is no model here — a
model would make the demonstration about the model, and every claim above is
about the harness.
"""
from __future__ import annotations

import json
import statistics
from typing import Any

from browsergraph import duecare
from browsergraph.execute import Runtime
from browsergraph.manifest import NodeManifest, PortSpec
from browsergraph.templates import get as _template
from browsergraph.workbench import (
    NodeCandidate,
    OptimizationObjective,
    OptimizationProfile,
    WorkbenchDefinition,
)

TEMPLATE = "eval.harness"
SUMMARY = "grade a system on cases, with the controls as steps in the graph"

FILLING: dict[str, list[str]] = {
    "cases":     ["cases.builtin", "cases.regression"],
    "controls":  ["controls.paired", "controls.none"],
    "run":       ["run.keyword", "run.first", "run.empty"],
    "grade":     ["grade.overlap", "grade.contains", "grade.exact",
                  "grade.length"],
    "aggregate": ["aggregate.sliced", "aggregate.mean"],
    "duecare":   ["duecare.full", "duecare.blocking"],
    "report":    ["report.text", "report.json"],
}

#: How much better a known-good answer has to score than a known-bad one before
#: the grader has shown it can tell them apart. Higher than `duecare`'s default
#: because these controls are not subtle — the bad answer is wrong in a way a
#: person would spot instantly, and a grader that only just separates them is
#: not going to separate the real cases.
CONTROL_MARGIN = 0.15


def _node(node_id: str, capability: str, takes, gives, *,
          description: str, **extra) -> NodeManifest:
    return NodeManifest(
        id=node_id, kind=node_id.split(".")[-1], description=description,
        capabilities=(capability,),
        inputs=tuple(PortSpec(n, t) for n, t in takes),
        outputs=tuple(PortSpec(n, t) for n, t in gives),
        metrics={"source": "illustrative-prior", "quality": 0.9},
        **extra)


NODES: tuple[NodeManifest, ...] = (
    _node("cases.builtin", "eval.cases", [], [("out", "Cases")],
          description="Fifteen question-answering cases in three slices."),
    _node("cases.regression", "eval.cases", [], [("out", "Cases")],
          description="The same set plus every case that has ever failed, "
                      "which is what a harness in its second month runs."),

    _node("controls.paired", "eval.controls", [("in", "Cases")],
          [("out", "Cases")],
          description="Adds a known-good and a known-bad copy of each case, "
                      "with the answer injected rather than produced."),
    _node("controls.none", "eval.controls", [("in", "Cases")],
          [("out", "Cases")],
          description="Adds nothing. Kept as a candidate so the difference "
                      "between having controls and not is measurable rather "
                      "than argued."),

    _node("run.keyword", "eval.run", [("in", "Cases")], [("out", "Responses")],
          description="Returns the context sentence sharing the most words "
                      "with the question."),
    _node("run.first", "eval.run", [("in", "Cases")], [("out", "Responses")],
          description="Always returns the first sentence. Weak, not broken."),
    _node("run.empty", "eval.run", [("in", "Cases")], [("out", "Responses")],
          description="Returns nothing. The system-level negative control: a "
                      "harness whose score survives this is not measuring the "
                      "system."),

    _node("grade.overlap", "eval.grade", [("in", "Responses")],
          [("out", "Grades")],
          description="Token F1 against the reference."),
    _node("grade.contains", "eval.grade", [("in", "Responses")],
          [("out", "Grades")],
          description="1.0 if the reference text appears in the answer."),
    _node("grade.exact", "eval.grade", [("in", "Responses")],
          [("out", "Grades")],
          description="Exact match after normalisation. Brittle, and honest "
                      "about being brittle, which the next one is not."),
    _node("grade.length", "eval.grade", [("in", "Responses")],
          [("out", "Grades")],
          description="Longer answers score higher. Produces a number that "
                      "moves when the system changes and measures nothing — "
                      "the failure the controls exist to catch."),

    _node("aggregate.sliced", "eval.aggregate",
          [("grades", "Grades"), ("cases", "Cases")], [("out", "Summary")],
          description="Per slice, with an interval, and the controls kept "
                      "separate from the real cases."),
    _node("aggregate.mean", "eval.aggregate",
          [("grades", "Grades"), ("cases", "Cases")], [("out", "Summary")],
          description="One number over everything. What most harnesses do."),

    _node("duecare.full", "eval.duecare",
          [("summary", "Summary"), ("grades", "Grades")], [("out", "Verdict")],
          description="All nine obligations, each discharged, waived with a "
                      "reason, failed, or left visibly outstanding."),
    _node("duecare.blocking", "eval.duecare",
          [("summary", "Summary"), ("grades", "Grades")], [("out", "Verdict")],
          description="Only the four that block. Faster, and the report is "
                      "quieter about what it did not look at."),

    _node("report.text", "eval.report", [("in", "Verdict")], [("out", "Report")],
          description="The ledger as prose, for a review."),
    _node("report.json", "eval.report", [("in", "Verdict")], [("out", "Report")],
          description="The same thing as JSON, for a receipt."),
)


def workbench() -> WorkbenchDefinition:
    template = _template(TEMPLATE)
    bench = template.instantiate(FILLING)
    return WorkbenchDefinition(
        title=template.title, task=template.task,
        stages=bench.stages, edges=bench.edges, nodes=NODES,
        candidates=tuple(NodeCandidate(id=n.id, node_id=n.id) for n in NODES),
        optimization_profiles=(OptimizationProfile(
            id="p.harness",
            name="Separate the systems, and be able to say how you know",
            objectives=(OptimizationObjective("quality", "maximize", 1.0),)),),
        metadata=dict(bench.metadata, pack="harness"),
    ).assert_valid()


# --- the cases --------------------------------------------------------------

#: Three slices, because a harness with one slice cannot demonstrate the thing
#: slices exist for.
CASES: tuple[dict[str, Any], ...] = (
    # short: the answer is a single word or number in the context
    {"id": "s1", "slice": "short",
     "question": "What colour is the roof?",
     "context": "The roof is green. The walls are white. The door is oak.",
     "reference": "green"},
    {"id": "s2", "slice": "short",
     "question": "Who signed the lease?",
     "context": "The lease was signed by Marlow. It runs for three years. "
                "Rent is paid monthly.",
     "reference": "Marlow"},
    {"id": "s3", "slice": "short",
     "question": "Which city is the office in?",
     "context": "The office is in Leeds. It opened in 2019. It seats forty.",
     "reference": "Leeds"},
    {"id": "s4", "slice": "short",
     "question": "What material is the door?",
     "context": "The door is oak. The roof is green. The floor is tiled.",
     "reference": "oak"},
    {"id": "s5", "slice": "short",
     "question": "How many seats are there?",
     "context": "It seats forty. The office is in Leeds. It opened in 2019.",
     "reference": "forty"},

    # numeric: the answer is a figure, and the distractors are also figures
    {"id": "n1", "slice": "numeric",
     "question": "What was the total?",
     "context": "The total was 1420. The deposit was 300. The balance was 1120.",
     "reference": "1420"},
    {"id": "n2", "slice": "numeric",
     "question": "What was the deposit?",
     "context": "The deposit was 300. The total was 1420. The balance was 1120.",
     "reference": "300"},
    {"id": "n3", "slice": "numeric",
     "question": "In which year did it open?",
     "context": "It opened in 2019. It seats forty. The office is in Leeds.",
     "reference": "2019"},
    {"id": "n4", "slice": "numeric",
     "question": "How long does the lease run?",
     "context": "It runs for three years. The lease was signed by Marlow. "
                "Rent is paid monthly.",
     "reference": "three years"},
    {"id": "n5", "slice": "numeric",
     "question": "What was the balance?",
     "context": "The balance was 1120. The total was 1420. The deposit was 300.",
     "reference": "1120"},

    # buried: the answer is not in the first sentence, which is the whole
    # difficulty — `run.first` scores zero here and well on the others, and a
    # harness that only reports a mean will call it a middling system
    {"id": "b1", "slice": "buried",
     "question": "What is the notice period?",
     "context": "Rent is paid monthly. The lease was signed by Marlow. "
                "The notice period is sixty days.",
     "reference": "sixty days"},
    {"id": "b2", "slice": "buried",
     "question": "Who holds the keys?",
     "context": "The office is in Leeds. It seats forty. "
                "The keys are held by Ferris.",
     "reference": "Ferris"},
    {"id": "b3", "slice": "buried",
     "question": "When is rent due?",
     "context": "The lease runs three years. It was signed by Marlow. "
                "Rent is due on the fifth.",
     "reference": "the fifth"},
    {"id": "b4", "slice": "buried",
     "question": "What is the floor made of?",
     "context": "The door is oak. The roof is green. The floor is tiled.",
     "reference": "tiled"},
    {"id": "b5", "slice": "buried",
     "question": "What is the parking arrangement?",
     "context": "The office is in Leeds. It opened in 2019. "
                "Parking is on the street.",
     "reference": "on the street"},
)

#: The answers a person rejected, having read all fifteen from each system.
#:
#: This is the anchor the grader is checked against, and it is keyed by
#: **(system, case)** rather than by case — which is the correction that matters.
#: A human label is a judgement about an *answer*; a label attached to a case
#: would silently be a label about whichever system happened to produce it.
#:
#: What the reader decided, and why:
#:
#: * `run.keyword` — right on fourteen. On `n4` it was asked how long the lease
#:   runs and returned the sentence about who signed it, because "lease" appears
#:   in both and the question's other words appear in neither.
#: * `run.first` — right on the ten cases whose answer happens to sit in the
#:   first sentence, wrong on all five of the `buried` slice. This is the slice
#:   effect the aggregate mean hides.
#: * `run.empty` — wrong on everything, and therefore a **single-class sample**:
#:   it cannot validate a grader at all, which the ledger reports as
#:   outstanding rather than as the grader's fault.
HUMAN_BAD: dict[str, frozenset[str]] = {
    "run.keyword": frozenset({"n4"}),
    "run.first": frozenset({"b1", "b2", "b3", "b4", "b5"}),
    "run.empty": frozenset(case["id"] for case in CASES),
}


def _human(case_id: str, system: str) -> str:
    """What the person said about this system's answer to this case."""
    return "bad" if case_id in HUMAN_BAD.get(system, frozenset()) else "good"


#: A long, fluent, confidently wrong answer. Long on purpose: it is what a
#: grader rewarding completeness scores highest, and it is wrong in a way no
#: person would miss.
WRONG_ANSWER = (
    "Based on a careful reading of the provided context and the surrounding "
    "documentation, the most defensible answer here is that the arrangement "
    "was never formally specified, and any figure quoted would therefore be "
    "an approximation rather than a fact of record."
)


def _tokens(text: str) -> list[str]:
    return [t.strip(".,;:!?()").lower() for t in str(text).split() if t.strip()]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in str(text).split(".") if s.strip()]


# --- the implementations ----------------------------------------------------

def _cases_builtin() -> list[dict[str, Any]]:
    return [dict(case) for case in CASES]


def _cases_regression(previously_failed: tuple[str, ...]) -> list[dict[str, Any]]:
    """The base set, plus every case that has ever failed, marked as such.

    Nothing is added that was not already in the set here — the point is the
    *marking*, which is what lets a report say "and the four we fixed in March
    are still passing" rather than losing them in the mean.
    """
    failed = set(previously_failed)
    rows = []
    for case in CASES:
        row = dict(case)
        if row["id"] in failed:
            row["regression"] = True
        rows.append(row)
    return rows


def _controls_paired(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each case, plus a known-good and a known-bad copy of it.

    The copies carry `control`, and the runner honours it by *injecting* an
    answer rather than producing one. That is the design: these cases test the
    grader, and a grader tested through the system cannot be separated from it.
    """
    rows = [dict(case) for case in cases]
    for case in cases:
        rows.append({**case, "id": f"{case['id']}#good", "control": "good"})
        rows.append({**case, "id": f"{case['id']}#bad", "control": "bad"})
    return rows


def _controls_none(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(case) for case in cases]


def _respond(case: dict[str, Any], system: str) -> str:
    control = case.get("control")
    if control == "good":
        return str(case["reference"])
    if control == "bad":
        return WRONG_ANSWER

    if system == "run.empty":
        return ""
    sentences = _sentences(case["context"])
    if not sentences:
        return ""
    if system == "run.first":
        return sentences[0]
    wanted = set(_tokens(case["question"]))
    return max(sentences, key=lambda s: len(wanted & set(_tokens(s))))


def _run(cases: list[dict[str, Any]], system: str) -> list[dict[str, Any]]:
    return [{"case": case["id"], "slice": case.get("slice", ""),
             "control": case.get("control", ""),
             "regression": bool(case.get("regression")),
             "human": _human(case["id"], system),
             "question": case["question"], "reference": case["reference"],
             "answer": _respond(case, system), "run": system}
            for case in cases]


def _grade_one(response: dict[str, Any], how: str) -> float:
    answer, reference = str(response["answer"]), str(response["reference"])
    if how == "grade.exact":
        return 1.0 if answer.strip().lower() == reference.strip().lower() else 0.0
    if how == "grade.contains":
        return 1.0 if reference.strip().lower() in answer.lower() else 0.0
    if how == "grade.length":
        # Deliberately measures the wrong thing, and does it smoothly enough to
        # look like a score. Capped at 1.0 at forty tokens so it has the same
        # range as the others and slots into any table beside them.
        return min(1.0, len(_tokens(answer)) / 40.0)
    got, want = set(_tokens(answer)), set(_tokens(reference))
    if not got or not want:
        return 0.0
    shared = len(got & want)
    if not shared:
        return 0.0
    precision, recall = shared / len(got), shared / len(want)
    return 2 * precision * recall / (precision + recall)


def _grade(responses: list[dict[str, Any]], how: str) -> list[dict[str, Any]]:
    return [{**response, "score": _grade_one(response, how), "grader": how}
            for response in responses]


def _aggregate(grades: list[dict[str, Any]], cases: list[dict[str, Any]], *,
               sliced: bool) -> dict[str, Any]:
    real = [g for g in grades if not g.get("control")]
    good = [g["score"] for g in grades if g.get("control") == "good"]
    bad = [g["score"] for g in grades if g.get("control") == "bad"]
    scores = [g["score"] for g in real]
    overall = statistics.fmean(scores) if scores else 0.0

    summary: dict[str, Any] = {
        "n": len(real), "overall": overall,
        "controls": {"good": statistics.fmean(good) if good else None,
                     "bad": statistics.fmean(bad) if bad else None,
                     "n": len(good) + len(bad)},
        "grader": grades[0]["grader"] if grades else "",
        "system": grades[0]["run"] if grades else "",
        "cases_in": len(cases),
        "sliced": sliced,
    }
    if not sliced:
        # An honest record of what this candidate did *not* compute, so the
        # due-care step can leave `slices` outstanding rather than guessing.
        summary["by_slice"] = {}
        return summary

    by_slice: dict[str, list[float]] = {}
    for grade in real:
        by_slice.setdefault(grade.get("slice", "?"), []).append(grade["score"])
    mean, half = duecare.interval(scores)
    summary["by_slice"] = by_slice
    summary["interval"] = {"mean": mean, "half_width": half}
    return summary


def _binary(score: float) -> str:
    """A grade turned into the label a person would have written."""
    return "good" if score >= 0.5 else "bad"


def _duecare(summary: dict[str, Any], grades: list[dict[str, Any]], *,
             full: bool) -> dict[str, Any]:
    """Build the ledger from what the run actually produced.

    Every obligation here is discharged from data or left outstanding. Nothing
    is discharged because the code intended to do it — which is the failure
    mode of every checklist that lives in a document.
    """
    real = [g for g in grades if not g.get("control")]
    scores = [g["score"] for g in real]

    obligations = ("holdout", "negative_control", "sample_size", "provenance")
    ledger = (duecare.Ledger.standard() if full
              else duecare.Ledger.of(*[o for o in duecare.STANDARD
                                       if o.id in obligations]))

    # Holdout. True here for an unusual reason, and saying which reason is the
    # difference between a discharge and a rubber stamp: the systems are rule
    # based, so nothing was fitted to these cases at all.
    ledger.discharge("holdout",
                     f"the {len(real)} cases were not used to build any of the "
                     f"three systems — all are rule-based and fitted to "
                     f"nothing")

    good = [g["score"] for g in grades if g.get("control") == "good"]
    bad = [g["score"] for g in grades if g.get("control") == "bad"]
    if good and bad:
        ledger.record(duecare.check_negative_control(
            real=statistics.fmean(good), broken=statistics.fmean(bad),
            margin=CONTROL_MARGIN,
            what="an injected, fluent, wrong answer"))
    # else: left outstanding, which is what `controls.none` is for.

    ledger.record(duecare.check_sample_size(scores))
    ledger.record(duecare.check_provenance(real))

    if full:
        if good:
            ledger.record(duecare.check_positive_control(
                statistics.fmean(good), floor=0.9,
                what="an injected, correct answer"))
        by_slice = summary.get("by_slice") or {}
        if by_slice:
            ledger.record(duecare.check_slices(by_slice, regression=0.25))
        labelled = [g for g in real if g.get("human")]
        if labelled:
            ledger.record(duecare.check_grader_agreement(
                [_binary(g["score"]) for g in labelled],
                [g["human"] for g in labelled]))
        # Replication, and the evidence line refuses to overclaim: these
        # graders are pure functions, so re-running is not evidence about a
        # sampled system. Recording that honestly is worth more than a tick.
        ledger.record(duecare.check_replication([summary["overall"]] * 2))
        ledger.note("replication is trivial here — every candidate is "
                    "deterministic, so a zero spread is a property of the "
                    "pack and not evidence about a sampled system")
        ledger.record(duecare.check_coverage(
            sorted({g.get("slice", "") for g in real}),
            ["short", "numeric", "buried"]))

    verdict = ledger.verdict(summary["overall"])
    return {**verdict.to_dict(), "ledger": ledger.to_dict(),
            "summary": summary,
            "failures": sorted(g["case"] for g in real if g["score"] < 0.5),
            "text": ledger.text(summary["overall"])}


def _report(verdict: dict[str, Any], *, as_json: bool) -> str:
    if as_json:
        return json.dumps({k: v for k, v in verdict.items() if k != "text"},
                          indent=2, sort_keys=True, default=str)
    summary = verdict["summary"]
    lines = [f"system {summary['system']} graded by {summary['grader']}",
             verdict["text"]]
    if summary.get("by_slice"):
        lines.append("")
        for name, values in sorted(summary["by_slice"].items()):
            lines.append(f"  {name:<10} {statistics.fmean(values):.3f} "
                         f"({len(values)} cases)")
    return "\n".join(lines)


def runtime(previously_failed: tuple[str, ...] = ()) -> Runtime:
    """One function per candidate.

    `previously_failed` is what a second round would carry in — the regression
    set from `duecare.Loop`. It is configuration rather than graph input for
    the usual reason: `cases` is a source step and has no input ports.
    """
    return Runtime({
        "cases.builtin": lambda **kw: _cases_builtin(),
        "cases.regression": lambda **kw: _cases_regression(previously_failed),
        "controls.paired": lambda **kw: _controls_paired(kw["in"]),
        "controls.none": lambda **kw: _controls_none(kw["in"]),
        "run.keyword": lambda **kw: _run(kw["in"], "run.keyword"),
        "run.first": lambda **kw: _run(kw["in"], "run.first"),
        "run.empty": lambda **kw: _run(kw["in"], "run.empty"),
        "grade.overlap": lambda **kw: _grade(kw["in"], "grade.overlap"),
        "grade.contains": lambda **kw: _grade(kw["in"], "grade.contains"),
        "grade.exact": lambda **kw: _grade(kw["in"], "grade.exact"),
        "grade.length": lambda **kw: _grade(kw["in"], "grade.length"),
        "aggregate.sliced": lambda **kw: _aggregate(kw["grades"], kw["cases"],
                                                    sliced=True),
        "aggregate.mean": lambda **kw: _aggregate(kw["grades"], kw["cases"],
                                                  sliced=False),
        "duecare.full": lambda **kw: _duecare(kw["summary"], kw["grades"],
                                              full=True),
        "duecare.blocking": lambda **kw: _duecare(kw["summary"], kw["grades"],
                                                  full=False),
        "report.text": lambda **kw: _report(kw["in"], as_json=False),
        "report.json": lambda **kw: _report(kw["in"], as_json=True),
    })


def example() -> dict[str, Any]:
    return {"previously_failed": ("b1", "b3")}


def trustworthy(run) -> tuple[bool, float]:
    """A verifier that will not accept a score it cannot stand behind.

    This is the pack's argument in one function, and it is worth reading beside
    `solve.outputs_are_not_empty`: the obvious verifier for an evaluation
    harness is "it produced a number", and every route produces a number —
    including the ones graded by answer length and the ones run against a
    system that returns nothing.

    So the score here is the *verdict*, not the mean. A FAIL scores zero
    however high the mean was. A PROVISIONAL is discounted rather than
    rejected, because "we have not finished checking" is a real state and a
    harness that treats it as failure teaches people to stop reporting it.
    """
    verdict = run.output("duecare") if run.ok else None
    if not verdict:
        return False, 0.0
    state, score = verdict["state"], float(verdict["score"])
    if state == duecare.FAIL:
        return False, 0.0
    if state == duecare.PROVISIONAL:
        return True, score * 0.5
    return True, score
