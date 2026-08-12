#!/usr/bin/env python3
"""An evaluation you could defend, and the loop that keeps it defensible.

Three things happen here, in order, and the middle one is the point:

1. Two systems and four graders are run against the same cases. Every route
   produces a number, and one of the graders is measuring answer length.
2. The **due-care ledger** decides which of those numbers anybody may act on.
   A grader that cannot separate an injected-correct answer from an
   injected-wrong one fails a control, and its score is not a result.
3. The **feedback loop** turns this round's failures into next round's cases,
   permanently, and folds the outcome back into route evidence — so the next
   run starts from what this one learned.

    python examples/09_due_care_evaluation.py
"""
from browsergraph import duecare, evidence, execute, packs
from browsergraph.compile import compile_route
from browsergraph.packs import harness

pack = packs.get("harness")
bench = pack.workbench()

BASE = {"cases": "cases.builtin", "controls": "controls.paired",
        "aggregate": "aggregate.sliced", "duecare": "duecare.full",
        "report": "report.text"}


def evaluate(system: str, grader: str):
    route = {**BASE, "run": system, "grade": grader}
    return execute.run(compile_route(bench, route), pack.runtime(**pack.example()))


# --- 1. every route produces a number ---------------------------------------

print("Every one of these ran without raising, and every one produced a score.\n")
print(f"{'system':<14}{'grader':<17}{'mean':>7}   verdict")
runs = {}
for system in ("run.keyword", "run.first"):
    for grader in ("grade.contains", "grade.overlap", "grade.length"):
        run = runs[(system, grader)] = evaluate(system, grader)
        verdict = run.output("duecare")
        print(f"{system:<14}{grader:<17}"
              f"{verdict['summary']['overall']:>7.3f}   {verdict['state']}")

print("\nThe means are not comparable. Two of the graders are measuring "
      "something\nother than whether the answer is right, and the mean cannot "
      "say which.")

# --- 2. the ledger decides what is actionable -------------------------------

print("\n" + "=" * 72)
print("What the controls found\n")
for grader in ("grade.contains", "grade.length"):
    verdict = runs[("run.keyword", grader)].output("duecare")
    print(f"  {grader}: {verdict['state']}")
    for failure in verdict["failed"]:
        print(f"    FAILED {failure['obligation']}")
        print(f"      {failure['evidence']}")
    if not verdict["failed"]:
        print("    every obligation discharged — this number can be used")
    print()

# Without controls in the graph at all, the best a run can be is provisional.
without = execute.run(
    compile_route(bench, {**BASE, "controls": "controls.none",
                          "run": "run.keyword", "grade": "grade.contains"}),
    pack.runtime(**pack.example())).output("duecare")
print(f"  the same good grader, with no controls: {without['state']}")
print(f"    outstanding: {', '.join(without['outstanding'])}")
print("    'we have not checked' is a state, or it gets rendered as a pass.")

# --- 3. the loop ------------------------------------------------------------

print("\n" + "=" * 72)
print("The feedback loop\n")

loop = duecare.Loop()
learned = evidence.Evidence()

for index, system in enumerate(("run.first", "run.keyword", "run.keyword")):
    run = evaluate(system, "grade.contains")
    verdict = run.output("duecare")
    loop.round(cases=verdict["summary"]["n"],
               verdict=duecare.Verdict(state=verdict["state"],
                                       score=verdict["score"],
                                       digest=verdict["digest"]),
               failures=verdict["failures"],
               label=f"r{index} {system}")
    # The same observation that graded the output teaches the search which
    # route produced it. Without this the harness improves the system and
    # forgets how it was built.
    loop.fold_into(learned, {**BASE, "run": system, "grade": "grade.contains"})

print(loop.text())
base = [case["id"] for case in harness.CASES]
print(f"\n  next round runs {len(loop.next_cases(base))} cases: the {len(base)} "
      f"in the set, with every case that has ever failed guaranteed a place")
print(f"  regression set: {', '.join(sorted(loop.regressions)) or '(none)'}")

best = learned.posterior("run.keyword")
worse = learned.posterior("run.first")
print("\n  Round 0 failed on a slice rather than on a control: `run.first`\n"
      "  scores 0.00 on the five buried cases and 1.00 on the other ten, and\n"
      "  the mean of 0.667 hides that completely.")
print("\n  and the route evidence moved with it:")
print(f"    run.keyword  {best.runs} run(s), success rate {best.rate:.2f}")
print(f"    run.first    {worse.runs} run(s), success rate {worse.rate:.2f}")

# --- what a verifier that reads the verdict does ----------------------------

print("\n" + "=" * 72)
print("A verifier that will not accept a score it cannot stand behind\n")
for (system, grader), run in runs.items():
    ok, score = harness.trustworthy(run)
    mean = run.output("duecare")["summary"]["overall"]
    print(f"  {system:<13}{grader:<17} mean {mean:.3f} -> "
          f"{'accepted' if ok else 'rejected'} at {score:.3f}")

print("\nThe route graded by answer length reports a mean and scores zero, "
      "because\nthe verifier reads the verdict rather than the number.")
