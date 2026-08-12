"""A model grading other models, and the arithmetic that says whether to believe it.

Using a model as a judge is the only affordable way to grade open-ended output,
and the standard way of validating one is to report how often it agrees with a
person. That number is almost always flattering, for a reason that has nothing
to do with the judge:

    from browsergraph import packs
    print(packs.get("judge").solve().text())

Every route here is executed by a test, so the table below is measured rather
than illustrative — `raw` is the percentage of labels that matched, `kappa` is
what is left after chance:

    items           rubric            judge               kappa    raw
    items.natural   rubric.criteria   judge.rubric        +0.74    90%
    items.natural   rubric.criteria   judge.confident     -0.15    70%
    items.natural   rubric.criteria   judge.length        -0.45    10%
    items.natural   rubric.holistic   judge.rubric        -0.47     0%
    items.balanced  rubric.criteria   judge.rubric        +0.75    88%
    items.balanced  rubric.holistic   judge.rubric        -0.75    12%

Three things fall out of it, and the second is the one worth the pack.

**Raw agreement flatters.** `judge.confident` matched the human label on 70% of
the natural set — a number most teams would ship — while agreeing with people
*less than chance would*. On a set that is 80% good, saying "good" to everything
scores 80%, and every raw figure has to be read against that. Both metrics are
candidates for one step here, computed from identical inputs, and they disagree
completely.

**The rubric matters more than the judge.** The same `judge.rubric`, unchanged,
goes from +0.74 to −0.47 when the rubric it is handed changes from named
criteria to "rate the overall quality". Given nothing specific to check, it
falls back to surface features, which is what people do too. Most effort in this
area goes into choosing a judge model; this measurement says the cheaper lever
is upstream.

**A length-preferring judge can be inverted, not merely useless.** `judge.length`
scores −0.45 because in this corpus — as in most — the fabricated answers are the
long, hedge-free, confident ones. A judge rewarding completeness on a system that
hallucinates verbosely is not adding noise. It is ranking backwards.

Neither bias is a straw man. Length and confidence are the two most reliably
documented judge biases, and they are dangerous precisely because a biased judge
is a *consistent* one — run it twice, get the same answer, and report the
self-agreement as reliability.

Standard library, deterministic, no model. The judges here are small functions
that reproduce those biases exactly, because a demonstration that needs an API
key is a demonstration nobody runs.
"""
from __future__ import annotations

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

TEMPLATE = "eval.judge"
SUMMARY = "use a model as a judge, and check it against people above chance"

FILLING: dict[str, list[str]] = {
    "items":     ["items.natural", "items.balanced"],
    "rubric":    ["rubric.criteria", "rubric.holistic"],
    "judge":     ["judge.rubric", "judge.length", "judge.confident"],
    "human":     ["human.full", "human.sample"],
    "agreement": ["agreement.kappa", "agreement.raw"],
    "calibrate": ["calibrate.threshold", "calibrate.none"],
}


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
    _node("items.natural", "eval.items", [], [("out", "Items")],
          description="The distribution as it arrives: mostly acceptable "
                      "answers, which is what makes raw agreement flattering."),
    _node("items.balanced", "eval.items", [], [("out", "Items")],
          description="Equal numbers of good and bad. Costs more to build and "
                      "is the only version on which agreement is informative."),

    _node("rubric.criteria", "eval.rubric", [("in", "Items")],
          [("out", "Rubric")],
          description="Named criteria with a rule each: is the claim in the "
                      "source, does it answer the question, is it contradicted."),
    _node("rubric.holistic", "eval.rubric", [("in", "Items")],
          [("out", "Rubric")],
          description="'Rate the overall quality.' Produces a number "
                      "correlated with length and confidence."),

    _node("judge.rubric", "eval.judge",
          [("items", "Items"), ("rubric", "Rubric")], [("out", "Grades")],
          description="Applies the criteria it was given. Under a holistic "
                      "rubric there are no criteria to apply, so it falls back "
                      "to surface features — which is the finding."),
    _node("judge.length", "eval.judge",
          [("items", "Items"), ("rubric", "Rubric")], [("out", "Grades")],
          description="Prefers the longer answer. The single best-documented "
                      "judge bias."),
    _node("judge.confident", "eval.judge",
          [("items", "Items"), ("rubric", "Rubric")], [("out", "Grades")],
          description="Prefers answers that hedge less. Rewards exactly the "
                      "answers that are wrong without saying so."),

    _node("human.full", "eval.human", [("in", "Items")], [("out", "Grades")],
          description="Every item labelled. Expensive, and the only version "
                      "that supports a per-slice agreement number."),
    _node("human.sample", "eval.human", [("in", "Items")], [("out", "Grades")],
          description="A third of them, which is what teams actually have."),

    _node("agreement.kappa", "eval.agreement",
          [("model", "Grades"), ("human", "Grades")], [("out", "Findings")],
          description="Agreement above what chance explains."),
    _node("agreement.raw", "eval.agreement",
          [("model", "Grades"), ("human", "Grades")], [("out", "Findings")],
          description="The percentage that matched. Flattering on any "
                      "imbalanced set, which is every natural one."),

    _node("calibrate.threshold", "eval.calibrate",
          [("grades", "Grades"), ("agreement", "Findings")],
          [("out", "Grades")],
          description="Move the pass mark so the judge's positive rate "
                      "matches the humans'. Corrects the bias it can see."),
    _node("calibrate.none", "eval.calibrate",
          [("grades", "Grades"), ("agreement", "Findings")],
          [("out", "Grades")],
          description="Leave it. Kept so the effect of calibrating is "
                      "measurable rather than assumed."),
)


def workbench() -> WorkbenchDefinition:
    template = _template(TEMPLATE)
    bench = template.instantiate(FILLING)
    return WorkbenchDefinition(
        title=template.title, task=template.task,
        stages=bench.stages, edges=bench.edges, nodes=NODES,
        candidates=tuple(NodeCandidate(id=n.id, node_id=n.id) for n in NODES),
        optimization_profiles=(OptimizationProfile(
            id="p.judge", name="Agree with people for the right reason",
            objectives=(OptimizationObjective("quality", "maximize", 1.0),)),),
        metadata=dict(bench.metadata, pack="judge"),
    ).assert_valid()


# --- the items --------------------------------------------------------------
#
# Each carries the truth (`label`), which nothing but `human.*` may read. The
# answers are built so the three biases pull apart: some good answers are short,
# some bad answers are long, and the hedged ones are right.

HEDGE = ("might", "possibly", "unclear", "appears", "roughly", "approximately")

ITEMS: tuple[dict[str, Any], ...] = (
    {"id": "i1", "label": "good", "question": "What is the deposit?",
     "answer": "300.", "source": "The deposit was 300."},
    {"id": "i2", "label": "good", "question": "Who signed?",
     "answer": "Marlow.", "source": "Signed by Marlow."},
    {"id": "i3", "label": "good", "question": "When does it start?",
     "answer": "It starts on the fifth of March, as stated in the schedule.",
     "source": "Starts on the fifth of March."},
    {"id": "i4", "label": "good", "question": "How many seats?",
     "answer": "Forty.", "source": "It seats forty."},
    {"id": "i5", "label": "good", "question": "What is the notice period?",
     "answer": "Sixty days.", "source": "The notice period is sixty days."},
    {"id": "i6", "label": "good", "question": "Is parking included?",
     "answer": "It appears parking is on the street and not included, "
               "though the schedule is roughly worded on this point.",
     "source": "Parking is on the street."},
    {"id": "i7", "label": "good", "question": "What is the total?",
     "answer": "1420.", "source": "The total was 1420."},
    {"id": "i8", "label": "good", "question": "Which city?",
     "answer": "Leeds.", "source": "The office is in Leeds."},

    {"id": "i9", "label": "bad", "question": "What is the balance?",
     "answer": "Having reviewed the schedule in full and cross-referenced the "
               "figures given, the balance is clearly stated as 2200 and there "
               "is no ambiguity whatsoever about that figure.",
     "source": "The balance was 1120."},
    {"id": "i10", "label": "bad", "question": "Who holds the keys?",
     "answer": "The keys are held by the managing agent, who is certainly "
               "responsible for all access arrangements under the terms set "
               "out in the agreement as written.",
     "source": "The keys are held by Ferris."},
    {"id": "i11", "label": "bad", "question": "What is the rent due date?",
     "answer": "Rent is definitely due at the end of each month without "
               "exception, and this is standard across all such agreements.",
     "source": "Rent is due on the fifth."},
    {"id": "i12", "label": "bad", "question": "What year did it open?",
     "answer": "2021.", "source": "It opened in 2019."},
)

#: `items.natural` drops most of the bad ones to get the imbalance a real
#: sample has. Eight good and two bad is 80% — the number in the docstring.
NATURAL = ("i1", "i2", "i3", "i4", "i5", "i6", "i7", "i8", "i9", "i10")
#: Four and four. Costs more to assemble and is what makes kappa informative.
BALANCED = ("i1", "i2", "i3", "i6", "i9", "i10", "i11", "i12")


def _tokens(text: str) -> list[str]:
    return [t.strip(".,;:!?()").lower() for t in str(text).split() if t.strip()]


# --- the implementations ----------------------------------------------------

def _items(which: str) -> list[dict[str, Any]]:
    wanted = NATURAL if which == "natural" else BALANCED
    return [dict(item) for item in ITEMS if item["id"] in wanted]


def _rubric(items: list[dict[str, Any]], *, holistic: bool) -> dict[str, Any]:
    if holistic:
        return {"kind": "holistic", "criteria": (),
                "instruction": "Rate the overall quality of the answer.",
                "n": len(items)}
    return {"kind": "criteria",
            "criteria": ("the answer's claim appears in the source",
                         "the answer addresses the question asked",
                         "nothing in the answer contradicts the source"),
            "instruction": "Mark each criterion, then pass only if all hold.",
            "n": len(items)}


def _grounded(item: dict[str, Any]) -> bool:
    """Is the answer's substance actually in the source?

    Content words only, because every answer shares the function words with
    every source and counting those makes any answer look grounded.
    """
    source = set(_tokens(item["source"]))
    answer = [t for t in _tokens(item["answer"]) if len(t) > 2]
    if not answer:
        return False
    numbers = [t for t in answer if any(c.isdigit() for c in t)]
    if numbers:
        # A figure that is not in the source is the clearest possible signal,
        # and it is the one a length-biased judge reliably misses.
        return all(n in source for n in numbers)
    content = [t for t in answer if t not in source]
    return len(content) / len(answer) < 0.5


def _judge(items: list[dict[str, Any]], rubric: dict[str, Any], how: str
           ) -> list[dict[str, Any]]:
    grades = []
    for item in items:
        if how == "judge.length":
            score = min(1.0, len(_tokens(item["answer"])) / 25.0)
        elif how == "judge.confident":
            hedges = sum(1 for t in _tokens(item["answer"]) if t in HEDGE)
            score = max(0.0, 1.0 - hedges * 0.4)
        elif rubric["kind"] == "holistic":
            # A faithful judge with nothing to be faithful to. Given no
            # criteria it does what people do: rewards a long, confident
            # answer. This is not the judge failing — it is the rubric.
            hedges = sum(1 for t in _tokens(item["answer"]) if t in HEDGE)
            score = min(1.0, len(_tokens(item["answer"])) / 25.0) - hedges * 0.2
            score = max(0.0, score)
        else:
            score = 1.0 if _grounded(item) else 0.0
        grades.append({"id": item["id"], "judge": how, "score": score,
                       "label": "good" if score >= 0.5 else "bad",
                       "rubric": rubric["kind"], "run": how})
    return grades


def _human(items: list[dict[str, Any]], *, full: bool) -> list[dict[str, Any]]:
    chosen = items if full else items[::3]
    return [{"id": item["id"], "label": item["label"], "score":
             1.0 if item["label"] == "good" else 0.0, "run": "human"}
            for item in chosen]


def _agreement(model: list[dict[str, Any]], human: list[dict[str, Any]], *,
               kappa: bool) -> dict[str, Any]:
    by_id = {g["id"]: g for g in model}
    paired = [(by_id[h["id"]]["label"], h["label"]) for h in human
              if h["id"] in by_id]
    if not paired:
        return {"method": "kappa" if kappa else "raw", "n": 0, "value": 0.0,
                "verdict": "no overlap between the judged and labelled sets"}

    model_labels = [m for m, _ in paired]
    human_labels = [h for _, h in paired]
    raw = sum(1 for m, h in paired if m == h) / len(paired)

    if not kappa:
        return {"method": "raw", "n": len(paired), "value": raw, "raw": raw,
                "kappa": None,
                # The line that matters, present whichever candidate ran: the
                # score a judge that never disagrees would get on this set.
                "always_good_would_score":
                    sum(1 for h in human_labels if h == "good") / len(paired),
                "verdict": f"{raw:.0%} of {len(paired)} labels matched"}

    value = duecare.cohen_kappa(model_labels, human_labels)
    return {"method": "kappa", "n": len(paired), "value": value, "raw": raw,
            "kappa": value,
            "always_good_would_score":
                sum(1 for h in human_labels if h == "good") / len(paired),
            "verdict": f"kappa {value:.2f} against raw agreement of {raw:.0%}"}


def _calibrate(grades: list[dict[str, Any]], agreement: dict[str, Any], *,
               shift: bool) -> list[dict[str, Any]]:
    if not shift or not grades:
        return [dict(g) for g in grades]
    # Move the pass mark so the judge marks the same fraction good as the
    # humans did. It cannot fix a judge that ranks the wrong things — it makes
    # the *rate* right while leaving the *ordering* wrong, which is worth
    # seeing: on a length-biased judge, calibration improves nothing.
    target = float(agreement.get("always_good_would_score") or 0.5)
    scores = sorted((g["score"] for g in grades), reverse=True)
    cut = max(0, min(len(scores) - 1, int(round(target * len(scores))) - 1))
    threshold = scores[cut] if scores else 0.5
    return [{**g, "label": "good" if g["score"] >= threshold else "bad",
             "calibrated": True, "threshold": threshold} for g in grades]


def runtime(**_: Any) -> Runtime:
    return Runtime({
        "items.natural": lambda **kw: _items("natural"),
        "items.balanced": lambda **kw: _items("balanced"),
        "rubric.criteria": lambda **kw: _rubric(kw["in"], holistic=False),
        "rubric.holistic": lambda **kw: _rubric(kw["in"], holistic=True),
        "judge.rubric": lambda **kw: _judge(kw["items"], kw["rubric"],
                                            "judge.rubric"),
        "judge.length": lambda **kw: _judge(kw["items"], kw["rubric"],
                                            "judge.length"),
        "judge.confident": lambda **kw: _judge(kw["items"], kw["rubric"],
                                               "judge.confident"),
        "human.full": lambda **kw: _human(kw["in"], full=True),
        "human.sample": lambda **kw: _human(kw["in"], full=False),
        "agreement.kappa": lambda **kw: _agreement(kw["model"], kw["human"],
                                                   kappa=True),
        "agreement.raw": lambda **kw: _agreement(kw["model"], kw["human"],
                                                 kappa=False),
        "calibrate.threshold": lambda **kw: _calibrate(kw["grades"],
                                                       kw["agreement"],
                                                       shift=True),
        "calibrate.none": lambda **kw: _calibrate(kw["grades"], kw["agreement"],
                                                  shift=False),
    })


def example() -> dict[str, Any]:
    return {}


def agrees_with_people(run) -> tuple[bool, float]:
    """Score a route by how much of the agreement is not chance.

    Deliberately reads `kappa` rather than `value`, so a route that chose
    `agreement.raw` scores on the same basis as one that chose
    `agreement.kappa`. Otherwise the search would learn that the way to look
    good is to pick the flattering metric — which is the human version of this
    failure and the reason the metric is a candidate at all.
    """
    if not run.ok:
        return False, 0.0
    found = run.output("agreement")
    kappa = found.get("kappa")
    if kappa is None:
        model = {g["id"]: g["label"] for g in run.output("calibrate")}
        human = run.output("human")
        pairs = [(model[h["id"]], h["label"]) for h in human if h["id"] in model]
        if not pairs:
            return False, 0.0
        kappa = duecare.cohen_kappa([m for m, _ in pairs],
                                    [h for _, h in pairs])
    return kappa > 0.0, max(0.0, float(kappa))
