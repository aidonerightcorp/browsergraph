# assay

**Check the measurement, not just the thing being measured.**

An assay is a test for the presence, amount or purity of something. This
package applies that to evaluations: before you believe a number, establish
that whatever produced it can tell good from bad at all.

Standard library only. No network, no model calls, no dependencies. It takes
lists and returns reports.

```bash
python -m assay.cli judge --demo
```

```
JUDGE AUDIT — FAIL

BELOW_CHANCE — kappa -0.207 on 120 items
  raw agreement 65.0%, but chance alone gives 71.0% on this distribution. The
  judge agrees with people less often than a coin weighted to the commonest
  answer would — this is not a weak signal, it is a signal pointing the wrong
  way.

RUBRIC-DEPENDENT — kappa spans 0.694 across 3 rubrics
  +0.487  is anything in it false
  +0.000  rate the overall quality
  -0.207  did it answer the question asked

FAIL  length
  score and answer length correlate at rho +0.95 — the judge systematically
  prefers longer answers.
```

Every number above is produced by the command, not written into this page.
`tests/test_assay.py::test_the_demo_reproduces_its_documented_numbers` fails if
they drift apart.

## The problem

If you ship a feature built on a language model you almost certainly have a
judge — a model that reads an output and says whether it was good. It probably
produces a number every day, that number probably goes in a dashboard, and it
has probably never been checked against a person.

Four ways a judge is wrong while looking right:

| | what it looks like | how to find it |
|---|---|---|
| **agrees at chance** | 85% agreement on a set that is 85% one class | `judge.check` reports kappa next to the raw number |
| **scores length** | ranks systems plausibly; a padded answer wins | `judge.length_bias` — rank correlation |
| **scores position** | A beats B, and B beats A | `judge.position_bias` — run both orders |
| **the rubric decides** | same judge, same answers, opposite verdicts | `judge.rubric_sensitivity` — spread across wordings |

The demo above is a single realistic mechanism, not three fixtures: the bad
answers are the longer ones because models pad when they do not know, the judge
scores length, and the verdict is a threshold on the score. All three findings
fall out of that one construction.

## Check your own

```bash
python -m assay.cli judge --data labels.csv --model verdict --human truth
```

A CSV or JSON table, one row per item, with the judge's verdict and a person's
verdict for the same item. Exits non-zero when the audit fails, so it works in
CI.

```python
from assay import judge

report = judge.audit(model=verdicts, human=labels,
                     scores=judge_scores, texts=answers)
print(report.text())
report.ok          # False unless everything that could be checked passed
```

## Could not check is a verdict

`CANNOT_CHECK` is not an error and not a pass. A single-class human sample, an
empty run, fewer labels than the floor — each returns a report whose `ok` is
false and whose reason says what to fix.

This matters more than it sounds. Kappa on a sample where every human label is
"good" is 0.0, and reporting that as a bad judge convicts it of the
evaluation's own sampling. The judge might be fine. Nobody has looked.

## The other three modules

**`controls`** — null, negative, positive and shuffle controls as values. The
rule it enforces: *a harness with no controls cannot return better than
PROVISIONAL.* Also `chance_level()`, which defaults to the majority baseline
rather than 1/k, because comparing accuracy against 1/k on imbalanced data is
the most common way to make a null control lie.

```python
from assay import controls

c = controls.Controls()
c.add(controls.negative_control(broken=0.85, real=0.86))
print(c.verdict(score=0.86).text())      # FAIL — the harness cannot separate them
```

**`obligations`** — nine things an evaluation owes, each discharged with
evidence, waived with a stated reason, failed, or visibly outstanding. Three
verdict states, and the middle one is the point: `PROVISIONAL` means the number
exists and nobody may act on it, and `.ok` is false for it. The ledger digest
hashes the *standard* rather than the score, so `compare()` refuses two numbers
held to different bars.

**`taxonomy`** — the forty-one shapes engineering pipelines come in, in nine
families, classified by graph shape and **how each one fails while reporting
success**. Not by subject matter: a fraud scorer and a document extractor are
the same graph with different nouns. `fails_as` is a required field, and a test
rejects any category without one.

```bash
python -m assay.cli taxonomy --search address
python -m assay.cli taxonomy --coverage
```

## What this is not

It does not call models, run your evaluation, or store results. It has no
opinion about your prompt. It reads the output of whatever you already do and
tells you which parts of it are load-bearing.

It also will not tell you a judge is good. The best available verdict is that
nothing checkable came back wrong — which is why `audit()` reports the checks
that did not run rather than quietly scoring on the ones that did.

## Relationship to browsergraph

`browsergraph` is the reference implementation of the pipeline half — building
the graph, searching routes, running them, collecting evidence. It imports
these modules. Nothing here imports it, and
`tests/test_assay.py::test_assay_imports_nothing_from_browsergraph` reads the
AST to make sure that stays true. It caught a real dependency the first time it
ran.

The old import paths still work and will keep working:

```python
from browsergraph import duecare    # -> assay.obligations
from browsergraph import taxonomy   # -> assay.taxonomy
```

`duecare` was renamed to `obligations` on the way out, because Due Care is also
an unrelated product and one name for two things is how a search for either
finds neither.

## Licence

MIT.
