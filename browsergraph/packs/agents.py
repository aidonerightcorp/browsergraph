"""A supervisor, several workers and a critic — and the answer that looks complete.

The failure this pack exists to show is not that agents are unreliable. It is
that **a synthesiser reads five worker outputs and produces one confident
answer regardless of what was in them**, so a run where one worker refused and
another invented a figure is indistinguishable, at the output, from a run where
five workers did the job.

    from browsergraph import packs
    print(packs.get("agents").solve().text())

The brief asks for four fields. The source document contains three of them —
there is no insurer named anywhere in it — which is the ordinary situation and
the one every demo avoids.

Measured, by a test that runs every route:

    workers         critic           synthesise           fields  grounded  gaps
    work.grounded   critic.grounded  synthesise.filtered     3       3       1
    work.grounded   critic.none      synthesise.all          3       3       1
    work.confident  critic.grounded  synthesise.filtered     3       3       1
    work.confident  critic.none      synthesise.all          4       3       0
    work.flaky      critic.none      synthesise.all          2       2       1

Row four is the one to look at. It is the **best-looking** output in the table:
four fields for four asked, no gaps, nothing to explain. It is also the only
row containing a fabrication.

    verify.nonempty      "the answer has content"             passes row four
    verify.covers_brief  "present and grounded, or declared"  fails row four

The second verifier turns on a distinction the first cannot see. A **declared
gap** — "the source does not name an insurer" — is a correct answer to the
question asked. A **silent absence** is a field that went missing between the
brief and the answer with nobody noticing. Rows one to three declare their gap
and pass; row four has no gap because it filled it; row five has two fields and
a worker that refused, and the refusal never reached the answer.

That last row is the quiet one. `work.flaky` is a rate limit, and everything
downstream of it behaved correctly — which is exactly why the loss is invisible
without a verifier that counts against the brief.

Three worker candidates, and none of them is a broken model:

    work.grounded   answers from the source, and says so when it cannot
    work.confident  answers from the source, and fills the gap when it cannot
    work.flaky      one worker returns an apology instead of a result

`work.confident` is not misbehaving. It is doing what a helpful assistant does
when asked a question the material does not answer, which is why the remedy is
architectural: a critic that reads the results *against the tasks*, and a
synthesiser that can see what the critic said.

The `work` step is a **map**: one worker per task, and workers cannot see each
other. That independence is what makes the critic's job possible — if the
workers can read each other they converge, and their agreement stops being
evidence of anything.

Standard library, deterministic, no model.
"""
from __future__ import annotations

from typing import Any

from browsergraph.execute import Runtime
from browsergraph.manifest import NodeManifest, PortSpec
from browsergraph.templates import get as _template
from browsergraph.workbench import (
    NodeCandidate,
    OptimizationObjective,
    OptimizationProfile,
    WorkbenchDefinition,
)

TEMPLATE = "agent.supervisor_worker"
SUMMARY = "split a job across workers, criticise the parts, and check the whole"

FILLING: dict[str, list[str]] = {
    "brief":      ["brief.four_fields", "brief.three_fields"],
    "plan":       ["plan.disjoint", "plan.overlapping", "plan.single"],
    "work":       ["work.grounded", "work.confident", "work.flaky"],
    "critic":     ["critic.grounded", "critic.none"],
    "synthesise": ["synthesise.filtered", "synthesise.all"],
    "verify":     ["verify.covers_brief", "verify.nonempty"],
}

#: The material the workers are given. Three of the four requested fields are
#: in here. There is no insurer, on purpose — a source that answers everything
#: cannot demonstrate what happens when one does not.
SOURCE: dict[str, str] = {
    "parties": "The tenant is Ferris Holdings Ltd. The landlord is Marlow Estates.",
    "money": "Rent is 1,420 per month, payable on the fifth.",
    "termination": "The notice period is sixty days, in writing.",
    "fixtures": "The premises include forty desks and a tiled floor.",
}

FIELDS: dict[str, tuple[str, str]] = {
    # field -> (which section should hold it, the value that is really there)
    "tenant": ("parties", "Ferris Holdings Ltd"),
    "rent": ("money", "1,420 per month"),
    "notice_period": ("termination", "sixty days"),
    "insurer": ("fixtures", ""),          # not in the document at all
}

#: What `work.confident` produces when the source does not answer. Plausible,
#: correctly formatted, and invented.
INVENTED: dict[str, str] = {"insurer": "Standard Commercial Underwriters"}


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
    _node("brief.four_fields", "agent.brief", [], [("out", "Brief")],
          description="Four fields, one of which the source does not answer."),
    _node("brief.three_fields", "agent.brief", [], [("out", "Brief")],
          description="Only the three the source answers. The easy case, kept "
                      "so the difference is a measurement."),

    _node("plan.disjoint", "agent.plan", [("in", "Brief")],
          [("out", "List[Task]")],
          description="One task per field, no overlap."),
    _node("plan.overlapping", "agent.plan", [("in", "Brief")],
          [("out", "List[Task]")],
          description="Two tasks covering the same field. Their answers agree, "
                      "and the agreement is one answer counted twice."),
    _node("plan.single", "agent.plan", [("in", "Brief")],
          [("out", "List[Task]")],
          description="One task for everything. No parallelism, and no "
                      "independent second opinion on anything."),

    # A map node declares the *element* types: the step consumes List[Task]
    # and this handles one Task. Declaring the collection here is the mistake
    # the compiler catches, and it caught it while this pack was being written.
    _node("work.grounded", "agent.work", [("in", "Task")], [("out", "Result")],
          description="Answers from the section it was given, and returns "
                      "nothing when the section does not answer."),
    _node("work.confident", "agent.work", [("in", "Task")], [("out", "Result")],
          description="The same, except it fills the gap with something "
                      "plausible. Not a broken worker — a helpful one."),
    _node("work.flaky", "agent.work", [("in", "Task")], [("out", "Result")],
          description="One worker returns an apology instead of a result, "
                      "which is what a rate limit looks like from here."),

    _node("critic.grounded", "agent.critic",
          [("results", "List[Result]"), ("tasks", "List[Task]")],
          [("out", "Findings")],
          description="Checks each answer against the text the worker was "
                      "given, and notices tasks that cover the same ground."),
    _node("critic.none", "agent.critic",
          [("results", "List[Result]"), ("tasks", "List[Task]")],
          [("out", "Findings")],
          description="No criticism. Kept as a candidate because 'we added a "
                      "critic' should be a measurable change."),

    _node("synthesise.filtered", "agent.synthesise",
          [("results", "List[Result]"), ("findings", "Findings")],
          [("out", "Answer")],
          description="Drops what the critic rejected and records the gap."),
    _node("synthesise.all", "agent.synthesise",
          [("results", "List[Result]"), ("findings", "Findings")],
          [("out", "Answer")],
          description="Includes everything. Produces the most complete-looking "
                      "answer in the pack and the only fabricated one."),

    _node("verify.covers_brief", "agent.verify",
          [("answer", "Answer"), ("brief", "Brief")], [("out", "Verdict")],
          description="Every field asked for is present *and* traceable to the "
                      "source."),
    _node("verify.nonempty", "agent.verify",
          [("answer", "Answer"), ("brief", "Brief")], [("out", "Verdict")],
          description="The answer has content. What most pipelines check, and "
                      "it passes the fabricated row."),
)


def workbench() -> WorkbenchDefinition:
    template = _template(TEMPLATE)
    bench = template.instantiate(FILLING)
    return WorkbenchDefinition(
        title=template.title, task=template.task,
        stages=bench.stages, edges=bench.edges, nodes=NODES,
        candidates=tuple(NodeCandidate(id=n.id, node_id=n.id) for n in NODES),
        optimization_profiles=(OptimizationProfile(
            id="p.agents", name="Answer the brief without inventing any of it",
            objectives=(OptimizationObjective("quality", "maximize", 1.0),)),),
        metadata=dict(bench.metadata, pack="agents"),
    ).assert_valid()


# --- the implementations ----------------------------------------------------

def _brief(*, fields: tuple[str, ...]) -> dict[str, Any]:
    return {"ask": "Pull these fields out of the lease file.",
            "fields": list(fields), "source": dict(SOURCE)}


def _plan(brief: dict[str, Any], how: str) -> list[dict[str, Any]]:
    """Turn the brief into tasks, each carrying the text its worker may read.

    Which section a worker sees is the supervisor's real decision, and giving
    it to the worker as data is what makes `critic.grounded` possible: the
    critic can check an answer against exactly the text it came from.
    """
    fields = list(brief["fields"])
    if how == "plan.single":
        return [{"id": "t0", "fields": fields,
                 "text": " ".join(brief["source"].values()),
                 "section": "all"}]

    tasks: list[dict[str, Any]] = []
    for field in fields:
        section = FIELDS[field][0]
        tasks.append({"id": f"t{len(tasks)}", "fields": [field],
                      "section": section,
                      "text": brief["source"].get(section, "")})
    if how == "plan.overlapping" and fields:
        repeat = fields[0]
        section = FIELDS[repeat][0]
        tasks.append({"id": f"t{len(tasks)}", "fields": [repeat],
                      "section": section,
                      "text": brief["source"].get(section, "")})
    return tasks


def _work_one(task: dict[str, Any], how: str) -> dict[str, Any]:
    """One worker, one task. This is the node inside the map step."""
    if how == "work.flaky" and task["id"] == "t1":
        return {"task": task["id"], "worker": how, "values": {},
                "note": "I was not able to complete this request.",
                "refused": True}

    values, missing = {}, []
    for field in task["fields"]:
        _, truth = FIELDS[field]
        if truth and truth.lower() in task["text"].lower():
            values[field] = truth
        elif how == "work.confident" and field in INVENTED:
            values[field] = INVENTED[field]
        else:
            missing.append(field)
    return {"task": task["id"], "worker": how, "values": values,
            "missing": missing, "refused": False,
            "note": "" if not missing else f"not in the material: {missing}"}


def _critic(results: list[dict[str, Any]], tasks: list[dict[str, Any]], *,
            active: bool) -> dict[str, Any]:
    if not active:
        return {"critic": "critic.none", "rejected": [], "notes": [],
                "duplicated": [], "refused": []}

    by_task = {task["id"]: task for task in tasks}
    rejected, notes = [], []
    for result in results:
        task = by_task.get(result["task"], {})
        text = str(task.get("text", "")).lower()
        for field, value in result.get("values", {}).items():
            if str(value).lower() not in text:
                rejected.append({"task": result["task"], "field": field,
                                 "value": value,
                                 "why": "not present in the text this worker "
                                        "was given"})
        if result.get("refused"):
            notes.append(f"{result['task']}: worker returned no result")

    seen: dict[str, list[str]] = {}
    for task in tasks:
        for field in task["fields"]:
            seen.setdefault(field, []).append(task["id"])
    duplicated = [f"{field} covered by {ids}" for field, ids in seen.items()
                  if len(ids) > 1]

    return {"critic": "critic.grounded", "rejected": rejected, "notes": notes,
            "duplicated": duplicated,
            "refused": [r["task"] for r in results if r.get("refused")]}


def _synthesise(results: list[dict[str, Any]], findings: dict[str, Any], *,
                filtered: bool) -> dict[str, Any]:
    rejected = {(r["task"], r["field"]) for r in findings.get("rejected", ())}
    fields: dict[str, Any] = {}
    dropped = []
    # Gaps are a *result*, not an absence. A worker that reported "not in the
    # material" has answered the question; a worker that returned nothing has
    # not, and the difference has to survive into the answer or the verifier
    # downstream cannot tell a declared gap from a silent loss.
    gaps: list[str] = []
    for result in results:
        for field in result.get("missing", ()):
            if field not in gaps:
                gaps.append(field)
        for field, value in result.get("values", {}).items():
            if filtered and (result["task"], field) in rejected:
                dropped.append({"field": field, "value": value,
                                "task": result["task"]})
                if field not in gaps:
                    gaps.append(field)
                continue
            fields.setdefault(field, value)
    return {"fields": fields, "dropped": dropped, "gaps": gaps,
            "workers": len(results),
            "refused": len([r for r in results if r.get("refused")]),
            "critic": findings.get("critic", ""),
            "synthesiser": "filtered" if filtered else "all"}


def _grounded(field: str, value: Any) -> bool:
    """Is this value actually in the source document, anywhere?"""
    return bool(value) and str(value).lower() in " ".join(
        SOURCE.values()).lower()


def _verify(answer: dict[str, Any], brief: dict[str, Any], *,
            covers: bool) -> dict[str, Any]:
    asked = list(brief["fields"])
    present = [f for f in asked if f in answer["fields"]]
    ungrounded = [f for f in present if not _grounded(f, answer["fields"][f])]

    if not covers:
        return {"ok": bool(answer["fields"]), "check": "verify.nonempty",
                "fields": len(answer["fields"]), "asked": len(asked),
                "ungrounded": len(ungrounded),
                "why": ("the answer has content"
                        if answer["fields"] else "the answer is empty"),
                # Recorded even though this check ignores it, so the two
                # verifiers can be compared on identical data.
                "ungrounded_fields": ungrounded}

    declared = set(answer.get("gaps", ()))
    # A field is accounted for when it arrived grounded, or when somebody said
    # plainly that the source does not contain it. Everything else is a field
    # that went missing between the brief and the answer with nobody noticing.
    missing = [f for f in asked if f not in present and f not in declared]
    ok = not missing and not ungrounded
    reasons = []
    if missing:
        reasons.append(f"asked for {missing}, and got neither a value nor a "
                       f"statement that the source does not have one")
    if ungrounded:
        reasons.append(f"{ungrounded} are not in the source document")
    return {"ok": ok, "check": "verify.covers_brief",
            "fields": len(answer["fields"]), "asked": len(asked),
            "missing": missing, "ungrounded": len(ungrounded),
            "ungrounded_fields": ungrounded,
            "why": "; ".join(reasons) or "every field present and grounded"}


def runtime(**_: Any) -> Runtime:
    return Runtime({
        "brief.four_fields": lambda **kw: _brief(
            fields=("tenant", "rent", "notice_period", "insurer")),
        "brief.three_fields": lambda **kw: _brief(
            fields=("tenant", "rent", "notice_period")),
        "plan.disjoint": lambda **kw: _plan(kw["in"], "plan.disjoint"),
        "plan.overlapping": lambda **kw: _plan(kw["in"], "plan.overlapping"),
        "plan.single": lambda **kw: _plan(kw["in"], "plan.single"),
        # The map step calls these once per task, so `in` is one task.
        "work.grounded": lambda **kw: _work_one(kw["in"], "work.grounded"),
        "work.confident": lambda **kw: _work_one(kw["in"], "work.confident"),
        "work.flaky": lambda **kw: _work_one(kw["in"], "work.flaky"),
        "critic.grounded": lambda **kw: _critic(kw["results"], kw["tasks"],
                                                active=True),
        "critic.none": lambda **kw: _critic(kw["results"], kw["tasks"],
                                            active=False),
        "synthesise.filtered": lambda **kw: _synthesise(kw["results"],
                                                        kw["findings"],
                                                        filtered=True),
        "synthesise.all": lambda **kw: _synthesise(kw["results"], kw["findings"],
                                                   filtered=False),
        "verify.covers_brief": lambda **kw: _verify(kw["answer"], kw["brief"],
                                                    covers=True),
        "verify.nonempty": lambda **kw: _verify(kw["answer"], kw["brief"],
                                                covers=False),
    })


def example() -> dict[str, Any]:
    return {}


def answers_without_inventing(run) -> tuple[bool, float]:
    """Coverage of the brief, with a fabricated field costing more than a gap.

    The weighting is the argument. A missing field is a known unknown and the
    person reading the answer can go and look it up; an invented field is a
    wrong answer wearing a complete one's clothes, and nobody will look it up
    because nothing suggests they should.
    """
    if not run.ok:
        return False, 0.0
    verdict = run.output("verify")
    answer = run.output("synthesise")
    asked = max(1, verdict["asked"])
    grounded = len([f for f, v in answer["fields"].items() if _grounded(f, v)])
    declared = len([f for f in answer.get("gaps", ()) if f in
                    (verdict.get("missing", ()) or answer.get("gaps", ()))])
    invented = len(verdict.get("ungrounded_fields", ()))
    # A declared gap counts, at a discount: "the source does not say" is a
    # correct answer and a less useful one than the value would have been.
    score = max(0.0, (grounded + 0.5 * min(declared, asked - grounded)
                      - 2 * invented) / asked)
    return invented == 0 and grounded > 0, score
