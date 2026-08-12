# Case studies

Twelve ways a pipeline reports success and has done nothing.

Every one of these was hit while building this repository, usually while writing a demonstration of the feature it broke. None of them raised an exception. None would have been caught by a green test suite. Each was found by running something and looking at the answer.

**This file is generated** by `browsergraph.casestudies.render_all()` and the numbers in it are measured when it is generated — `tests/test_casestudies.py` fails if the committed copy drifts from what the code produces. Run `browsergraph cases --id <id>` to reproduce any of them.

| # | study | category | why nobody questioned it |
|---|---|---|---|
| 1 | [The judge agrees 73% of the time and knows nothing](#the-judge-agrees-73-of-the-time-and-knows-nothing) | `judge.model` | Agreement in the low seventies. |
| 2 | [Same judge, same answers, opposite verdicts](#same-judge-same-answers-opposite-verdicts) | `judge.model` | The judge produces confident, well-formatted grades and a leaderboard that moves when systems change. |
| 3 | [A grader that reads length, and ranks plausibly](#a-grader-that-reads-length-and-ranks-plausibly) | `judge.harness` | The ordering is defensible. |
| 4 | [84% accuracy, and one point of it is the model](#84-accuracy-and-one-point-of-it-is-the-model) | `predict.family` | 84% against 50% reads as a model doing most of the work — thirty-four points of lift. |
| 5 | [Zero rows, zero bytes, exit code zero](#zero-rows-zero-bytes-exit-code-zero) | `acquire.harvest` | Nothing throws. |
| 6 | [Denver, XZ 80202 passes validation](#denver-xz-80202-passes-validation) | `condition.validate` | Malformed input is rejected and the pass rate looks healthy. |
| 7 | [A December event lands in the wrong year](#a-december-event-lands-in-the-wrong-year) | `enrich.time` | Every timestamp is unambiguous and comparable. |
| 8 | [The evaluation compared a system with itself](#the-evaluation-compared-a-system-with-itself) | `judge.harness` | Labelling is cheap, the sample covers more cases, and every system is graded against the same standard. |
| 9 | [Wrong in both directions at once](#wrong-in-both-directions-at-once) | `judge.redteam` | Thirty-two attacks, all blocked. |
| 10 | [The best synthetic data was a copy of the real data](#the-best-synthetic-data-was-a-copy-of-the-real-data) | `generate.tabular` | One generator tops both fidelity and downstream utility by a wide margin. |
| 11 | [A search that reported 24 of 14 routes](#a-search-that-reported-24-of-14-routes) | `orchestrate.request` | The product is the right answer for a chain, and every graph in the examples was a chain. |
| 12 | [Deduplication that saved 73% and lost a match](#deduplication-that-saved-73-and-lost-a-match) | `condition.dedupe` | A large reduction in comparisons and the same duplicates found on the sample everybody looked at. |

---
## The judge agrees 73% of the time and knows nothing

`judge-below-chance` · category `judge.model` · pack `judge`

**What was done.** Sample a hundred answers, have the judge grade them, have a person grade the same hundred, and report how often they agree.

**Why it looked fine.** Agreement in the low seventies. Everyone reads that as a judge that mostly works and occasionally slips.

**What was actually happening.** The set is 85% good answers. Agreeing by luck alone gets you most of the way to that number, and answering 'good' every single time beats the judge outright.

**What caught it.** Cohen's kappa next to the raw figure. It subtracts the agreement chance explains, and here it comes out negative — the judge is not weakly right, it is pointing the wrong way.

**What to do instead.** Never report raw agreement without the chance level beside it. `assay.judge.check` refuses to return a bare number.

| measured | value |
|---|---|
| `raw` | 0.73 |
| `chance` | 0.766 |
| `kappa` | -0.154 |
| `always_good` | 0.85 |
| `verdict` | BELOW_CHANCE |

---

## Same judge, same answers, opposite verdicts

`rubric-decides` · category `judge.model` · pack `judge`

**What was done.** Ask the model to 'rate the overall quality' of each answer. It is the phrasing every team starts with.

**Why it looked fine.** The judge produces confident, well-formatted grades and a leaderboard that moves when systems change.

**What was actually happening.** The wording is doing more work than the input. Handed a specific question the same judge tracks people well; handed 'overall quality' it converges on saying everything is fine.

**What caught it.** Running the judge under two rubrics and comparing the kappa of each against the same human sample.

**What to do instead.** Treat the rubric as part of the instrument and version it with the results. `assay.judge.rubric_sensitivity` reports the spread.

| measured | value |
|---|---|
| `specific` | 0.787 |
| `holistic` | 0 |
| `spread` | 0.787 |

---

## A grader that reads length, and ranks plausibly

`length-is-the-grader` · category `judge.harness` · pack `harness`

**What was done.** Score each answer with a heuristic grader, rank the systems, ship the leaderboard.

**Why it looked fine.** The ordering is defensible. Terse systems come last, which matches most people's intuition about answer quality.

**What was actually happening.** The grader is measuring word count. A system that pads wins, and a fixed block of filler that answers nothing places second.

**What caught it.** A null control in the graph — a system that cannot be right by construction. If the harness ranks it anywhere but last, the harness is not measuring correctness.

**What to do instead.** Put the control in the graph as a step, not on a checklist. A harness with no control cannot return better than PROVISIONAL.

| measured | value |
|---|---|
| `ranking` | padded, wordy, normal, terse |
| `null_placed` | 2 |
| `of` | 5 |
| `control_ok` | no |

---

## 84% accuracy, and one point of it is the model

`chance-is-not-one-over-k` · category `predict.family` · pack `models`

**What was done.** Report accuracy against a 50% coin-flip baseline, because there are two classes.

**Why it looked fine.** 84% against 50% reads as a model doing most of the work — thirty-four points of lift.

**What was actually happening.** 83% of the cases are one class. The majority baseline is 0.83, so the model is worth one percentage point, and on this sample that is inside the noise.

**What caught it.** Computing the baseline from the label distribution instead of from the number of classes.

**What to do instead.** `assay.controls.chance_level` defaults to the majority baseline and makes you name the alternative explicitly.

| measured | value |
|---|---|
| `majority` | 0.83 |
| `uniform` | 0.5 |
| `observed` | 0.84 |
| `looks_good_against_uniform` | 0.34 |
| `over_majority` | 0.01 |

---

## Zero rows, zero bytes, exit code zero

`empty-result-passes` · category `acquire.harvest`

**What was done.** Fetch the page, extract the rows with a selector, write them out. Fail loudly if anything throws.

**Why it looked fine.** Nothing throws. The job runs nightly, exits 0 every time, and the monitoring stays green for weeks.

**What was actually happening.** The site changed its markup. The selector matches nothing, `findall` returns an empty list — which is a perfectly valid list — and a zero-byte file is written over yesterday's data.

**What caught it.** Judging the *output* separately from whether the run raised. 'Did it work' must not mean 'did it not crash'.

**What to do instead.** `solve()` takes the verifier as a required argument for this reason; there is no default, because the obvious default is the bug.

| measured | value |
|---|---|
| `rows_found` | 0 |
| `bytes_written` | 0 |
| `exception_raised` | no |
| `exit_code` | 0 |

---

## Denver, XZ 80202 passes validation

`zip-that-does-not-exist` · category `condition.validate` · pack `geo`

**What was done.** Validate addresses with a regular expression: words, comma, two capitals, five digits.

**Why it looked fine.** Malformed input is rejected and the pass rate looks healthy. The check is fast and has no dependencies.

**What was actually happening.** A format check tests shape, not existence. XZ is not a state, 00000 is not a ZIP, and Atlanta NY is a real-looking pair of a real city and the wrong state.

**What caught it.** Feeding it four addresses where only one is genuinely valid, and counting how many it let through.

**What to do instead.** Separate the two questions. Shape checks belong in validation; existence needs a reference table, and an enrichment that cannot resolve a row must say so rather than guessing.

| measured | value |
|---|---|
| `checked` | 4 |
| `accepted_by_format` | 4 |
| `actually_valid` | 1 |
| `wrong_but_accepted` | 3 |

---

## A December event lands in the wrong year

`timezone-rolls-the-year` · category `enrich.time` · pack `spacetime`

**What was done.** Store timestamps in UTC, which is the standard advice and is correct.

**Why it looked fine.** Every timestamp is unambiguous and comparable. The annual report aggregates by year off the stored value.

**What was actually happening.** 23:30 on 31 December in Denver is 06:30 on 1 January in UTC. Aggregating the UTC year moves the event into the next reporting period, and the error only appears in the rows that matter most for a year-end number.

**What caught it.** Asserting on the boundary rather than the middle. Nobody finds this by testing a Tuesday in March.

**What to do instead.** Keep the offset with the instant, and aggregate on the local calendar when the question is a local one.

| measured | value |
|---|---|
| `local` | 2025-12-31T23:30:00-07:00 |
| `utc` | 2026-01-01T06:30:00+00:00 |
| `local_year` | 2025 |
| `utc_year` | 2026 |
| `same_year` | no |

---

## The evaluation compared a system with itself

`label-belongs-to-a-response` · category `judge.harness` · pack `harness`

**What was done.** Label each case once — a human decides what a good answer looks like — and reuse that label for every system.

**Why it looked fine.** Labelling is cheap, the sample covers more cases, and every system is graded against the same standard.

**What was actually happening.** The label was formed while looking at one system's output. It describes that response, not the case, so every system gets scored on whether it matched the incumbent.

**What caught it.** Noticing that two genuinely different systems scored identically, and asking what the label could possibly be a label of.

**What to do instead.** A human label attaches to a response. If that is too expensive, say the evaluation ranks similarity to the incumbent, which is a real and sometimes useful thing to measure.

| measured | value |
|---|---|
| `per_case_scores` | alpha 0.667; beta 0.667 |
| `per_response_scores` | alpha 0.667; beta 0.333 |
| `systems_indistinguishable` | yes |
| `real_difference` | 0.333 |

---

## Wrong in both directions at once

`detector-fitted-to-its-own-attacks` · category `judge.redteam` · pack `redteam`

**What was done.** Collect the prompt-injection attempts you know about, build a keyword detector from them, and test it on that set.

**Why it looked fine.** Thirty-two attacks, all blocked. A clean sheet, and the obvious reading is that the guard works.

**What was actually happening.** The attacks and the detector came from the same source, so the test could only ever pass. Given five distinct families the same detector misses four of them — and it fires on a benign request that happens to contain a blocked phrase.

**What caught it.** Attacking with families the detector was not built from, and separately measuring false positives on benign traffic.

**What to do instead.** Report both directions. A detector evaluated only on the attacks that shaped it has been asked a question it cannot fail.

| measured | value |
|---|---|
| `attacks_one_family` | 32 |
| `holes_found_one_family` | 0 |
| `families_tried` | 5 |
| `families_missed` | 4 |
| `false_positive_on_benign` | yes |

---

## The best synthetic data was a copy of the real data

`the-copier-wins` · category `generate.tabular` · pack `synth`

**What was done.** Generate synthetic rows, score them on how closely their distribution matches the real data, and pick the best.

**Why it looked fine.** One generator tops both fidelity and downstream utility by a wide margin. It is the obvious choice.

**What was actually happening.** It is returning the input. Perfect fidelity and perfect utility are exactly what memorisation looks like, and the privacy property the synthetic data existed to provide is gone.

**What caught it.** Measuring memorisation as its own axis rather than inferring privacy from a low fidelity score.

**What to do instead.** Score fidelity, utility and privacy separately and refuse to aggregate them. A generator can only trade between them, so a single number hides the trade.

| measured | value |
|---|---|
| `copier_fidelity` | 1 |
| `marginal_fidelity` | 0.933 |
| `copier_utility` | 1 |
| `marginal_utility` | 0.5 |
| `copier_memorised` | 1 |
| `marginal_memorised` | 0.05 |
| `copier_wins_both` | yes |

---

## A search that reported 24 of 14 routes

`routes-are-not-computations` · category `orchestrate.request`

**What was done.** Count the routes through a graph by multiplying the number of candidates at each step.

**Why it looked fine.** The product is the right answer for a chain, and every graph in the examples was a chain.

**What was actually happening.** A branch is a sum, not a product: only one arm runs. Multiplying counts routes that cannot exist, and the search then reports a champion 'out of 24' on a space holding 14.

**What caught it.** Drawing the route space and counting the lines in the picture against the number in the summary.

**What to do instead.** Two named questions. `route_count` is what search ranges over; `computation_count` is how many distinguishable things the graph can do. Neither is always the larger.

| measured | value |
|---|---|
| `route_count` | 14 |
| `naive_product` | 24 |
| `overcounted_by` | 10 |

---

## Deduplication that saved 73% and lost a match

`blocking-hides-a-pair` · category `condition.dedupe`

**What was done.** Block on ZIP before comparing records, because comparing every pair does not scale.

**Why it looked fine.** A large reduction in comparisons and the same duplicates found on the sample everybody looked at.

**What was actually happening.** Blocking is a filter applied before the matcher, so any pair split across blocks is unreachable at any threshold. The one person who moved house is now two people, permanently.

**What caught it.** Reporting what the blocking key made unfindable, not only what the matcher found.

**What to do instead.** State the recall ceiling the blocking key imposes, and use more than one key when the cost of a missed match is higher than the cost of a comparison.

| measured | value |
|---|---|
| `all_pairs` | 15 |
| `compared` | 4 |
| `saved` | 0.733 |
| `unfindable_count` | 1 |
