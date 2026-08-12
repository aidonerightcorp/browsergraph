#!/usr/bin/env python3
"""Is the judge measuring quality, or measuring something else?

If you ship anything built on a language model you probably have a judge, it
probably emits a number every day, and it has probably never been compared with
a person. This script does that comparison four ways and prints what it finds.

Nothing here needs a graph, a pipeline or the rest of this project — `assay`
takes lists and returns reports.

    python examples/14_audit_a_judge.py
"""
from assay import controls, judge

# --- the sample -------------------------------------------------------------
#
# 120 answers from one system. 102 are genuinely good, which is the imbalance
# that makes raw agreement flattering. The bad answers are the *longer* ones,
# because a model that does not know pads — and the judge scores length.

human, model, texts, scores = [], [], [], []
for i in range(120):
    good = (i * 17) % 20 >= 3
    human.append("good" if good else "bad")
    words = 26 + (0 if good else 22) + ((i * 37) % 23)
    texts.append("word " * words)
    score = round(1.0 + words / 22.0 + ((i * 13) % 5) / 10.0, 2)
    scores.append(score)
    model.append("good" if score >= 2.6 else "bad")

# The same judge under three rubrics. Only the wording changes.
rubrics = {
    "did it answer the question asked": list(model),
    "rate the overall quality": ["good"] * len(human),
    "is anything in it false": [h if i % 6 else ("bad" if h == "good" else "good")
                                for i, h in enumerate(human)],
}

# --- 1. does it agree with people more than chance would? -------------------

print("=" * 74)
print("1. Agreement with people\n")

report = judge.check(model, human)
print(report.text())

majority = max(human.count("good"), human.count("bad")) / len(human)
print(f"\n  Answering 'good' every time would agree {majority:.1%} of the "
      f"time.")
print(f"  This judge manages {report.raw:.1%}.")

both_bad = sum(1 for m, h in zip(model, human, strict=True)
               if m == "bad" and h == "bad")
print(f"  Times the judge and a person agreed an answer was bad: {both_bad}.")

# --- 2. how much of the verdict is the rubric? ------------------------------

print("\n" + "=" * 74)
print("2. Rubric sensitivity — same judge, same answers\n")

rubric = judge.rubric_sensitivity(rubrics, human)
print(rubric.text())

# --- 3. is it reading length? -----------------------------------------------

print("\n" + "=" * 74)
print("3. Length bias\n")

bias = judge.length_bias(scores, texts)
print(bias.text())

# --- 4. what does the whole audit say? --------------------------------------

print("\n" + "=" * 74)
print("4. The audit\n")

full = judge.audit(model=model, human=human, rubrics=rubrics,
                   scores=scores, texts=texts)
print(f"  verdict: {full.verdict}")
print(f"  failed:  {', '.join(full.failures) or 'nothing'}")
print(f"  not run: {', '.join(full.unchecked) or 'everything was checked'}")

# --- 5. and what a single-class sample can and cannot tell you --------------

print("\n" + "=" * 74)
print("5. The check that refuses to run\n")

everyone_agrees = judge.check(["good"] * 60, ["good"] * 60)
print(everyone_agrees.text())
print(f"\n  ok:      {everyone_agrees.ok}")
print(f"  checked: {everyone_agrees.checked}")
print("  Note both are false. 'We could not check' is not 'we checked and it")
print("  failed' — the judge here might be excellent, and nobody has looked.")

# --- 6. the same discipline, one level up -----------------------------------

print("\n" + "=" * 74)
print("6. Controls on the harness itself\n")

bundle = controls.Controls()
chance = controls.chance_level(human)
bundle.add(controls.null_control(observed=0.86, chance=chance))
bundle.add(controls.negative_control(broken=0.85, real=0.86))
print(bundle.verdict(score=0.86).text())
print(f"\n  Chance on this label distribution is {chance:.3f}, not 0.500.")
print("  Against 0.500 every number above would look like a result.")

empty = controls.Controls().verdict(score=0.86)
print(f"\n  And with no controls at all: {empty.state} (ok={empty.ok})")
print("  A harness nobody has tested cannot report better than provisional.")
