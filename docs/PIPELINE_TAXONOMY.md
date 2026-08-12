# The shapes engineering work comes in

*A finite map of pipeline categories, and an honest account of which ones this
repository can actually run.*

The claim: **the pipelines engineering teams build are not infinitely various.**
Backend request handling, front-end analytics, data cleaning, geographic and
temporal enrichment, model training, synthetic data, LLM harnesses — most of it
falls into about forty shapes. This document lists 41 of them.

The classification is by **shape and failure mode**, not by subject matter,
because that is the only classification that helps:

* Two jobs are the *same category* when they have the same graph shape and go
  wrong in the same way. "Profile it, check it two independent ways, decide" is
  one category whether the thing being checked is a customer table or a model
  release.
* Two jobs are *different categories* when a correct implementation of one is a
  silently broken implementation of the other. Cleaning and validation look
  alike and are not: a validator that repairs data has destroyed the evidence
  that there was a problem, and a cleaner that refuses to write has done nothing.

Every category records how it **fails while reporting success**. That field is
the load-bearing one — a category with no characteristic silent failure is a
topic with a nice name, and `tests/test_taxonomy.py` refuses to let one exist.

## What is here, counted

| | |
|---|---:|
| Categories | 41 |
| Families | 9 |
| With a **checkable shape** (a template) | 41 (100%) |
| With **code that runs** (a pack, every route executed by a test) | 14 (34%) |
| Templates in the registry | 39 |
| Packs in the registry | 12 |

A **template** means the shape is written down with typed ports and a compiler
will reject a wrong filling of it. A **pack** means somebody has executed every
route of it. The first is a claim about the model; the second is a claim about
the world, and only the second one can embarrass you.

```bash
browsergraph taxonomy                  # the whole map
browsergraph taxonomy --coverage       # what is missing, and why
browsergraph taxonomy enrich.geo       # one category
browsergraph taxonomy --search address
```

| family | categories | shape | code |
|---|---:|---:|---:|
| acquire | 5 | 5 | 1 |
| condition | 6 | 6 | 2 |
| enrich | 4 | 4 | 2 |
| understand | 5 | 5 | 0 |
| predict | 5 | 5 | 2 |
| generate | 4 | 4 | 1 |
| judge | 4 | 4 | 3 |
| orchestrate | 5 | 5 | 1 |
| operate | 3 | 3 | 1 |

---

## The map

### Acquire

*Get material in, from somewhere that does not owe you a schema.*

| category | the question | template | pack |
|---|---|---|---|
| **`acquire.harvest`** | What does that page or endpoint say right now? | `web.harvest` | — |
| **`acquire.batch`** | I have a folder. What is in all of it? | `batch.files` | `files` |
| **`acquire.document`** | What are the fields in this invoice, contract or form? | `document.extraction` | — |
| **`acquire.image`** | Is this image usable, and what is in it? | `image.processing` | — |
| **`acquire.stream`** | What happened in each five-minute bucket? | `stream.window` | — |

**`acquire.harvest` — Harvest from a live source**  
Shape: chain with a verify step that is not the fetch step.  
Fails as: the fetch completed, the page was a login wall, and the empty result was written as if it were the answer.
For example: scrape a price; poll a status endpoint.

**`acquire.batch` — Do the same thing to every file**  
Shape: map over a collection, then split ok from failed, then summarise.  
Fails as: one bad file raises, forty good ones are lost, and the log says the batch failed rather than which item did.
For example: parse an inbox of mixed CSV and JSON.

**`acquire.document` — Read a document into records**  
Shape: fan-out to text and layout, join at field location.  
Fails as: tables come back as run-on prose, with one confidence for the whole document, so nobody can tell which field is shaky.
Not to be confused with: `generate.document`.

**`acquire.image` — Read and condition an image**  
Shape: fan-out to pixel statistics and content detection, join at a verdict, then derive the resized copies.  
Fails as: a blank or all-grey image passes every check that looks at file size and dimensions, because neither of those looks at the pixels.

**`acquire.stream` — Turn an event stream into windows**  
Shape: chain with an explicit watermark step before aggregation.  
Fails as: late events land after their window closed and are dropped silently, so yesterday's totals quietly change and nothing reports that they did.


### Condition

*Make what arrived fit to use, and record every change you made.*

| category | the question | template | pack |
|---|---|---|---|
| **`condition.validate`** | Is this fit to use, yes or no? | `data.quality` | `quality` |
| **`condition.clean`** | Can this be made usable without inventing anything? | `data.clean` | `clean` |
| **`condition.impute`** | What do I do about the gaps? | `data.impute` | — |
| **`condition.dedupe`** | Are these two rows one customer? | `entity.resolution` | — |
| **`condition.merge`** | Two systems say different things. Which do I write down? | `data.merge` | — |
| **`condition.migrate`** | Did everything arrive? | `data.migrate` | `migrate` |

**`condition.validate` — Decide whether data may proceed**  
Shape: fan-out to independent checks, join at one gate that may refuse.  
Fails as: the check runs, writes a warning nobody reads, and the bad data flows on — a gate that cannot stop anything is a log line.
Not to be confused with: `condition.clean`.

**`condition.clean` — Repair what is repairable**  
Shape: chain where every step emits the record *and* the change it made.  
Fails as: the repair is silent, so a value that was corrected and a value that was always right are indistinguishable afterwards.
Not to be confused with: `condition.validate`, `condition.impute`.

**`condition.impute` — Fill what is missing**  
Shape: fan-out to a missingness model and a fill, join at a comparison against not filling at all.  
Fails as: the imputed values are used as if observed, the model learns the fill constant, and accuracy improves on the rows that were never missing.
Not to be confused with: `condition.clean`.

**`condition.dedupe` — Decide which records are the same thing**  
Shape: block, compare within blocks, cluster, then report what blocking made unreachable.  
Fails as: blocking cuts the comparisons by 90% and the pairs it made impossible to find are never mentioned, so recall is reported against the pairs that survived blocking.

**`condition.merge` — Combine sources that disagree**  
Shape: join, with an explicit conflict step between the join and the write.  
Fails as: last-write-wins is applied by accident — the join silently picks one side, and the disagreement is not recorded anywhere.
Not to be confused with: `condition.dedupe`, `understand.conflict`.

**`condition.migrate` — Move it without losing any**  
Shape: count before and count after meeting at a reconcile step.  
Fails as: the write did not raise, a third of the rows are gone, and the only count taken was of the target.


### Enrich

*Add what the record did not carry, from somewhere that knows.*

| category | the question | template | pack |
|---|---|---|---|
| **`enrich.reference`** | What is the name for this code? | `enrich.reference` | — |
| **`enrich.geo`** | Where is this address, and is it real? | `enrich.geo` | `geo` |
| **`enrich.time`** | What else was true about this timestamp? | `enrich.time` | — |
| **`enrich.spacetime`** | Was anything happening in that city that day? | `enrich.spacetime` | `spacetime` |

**`enrich.reference` — Join a reference table**  
Shape: chain, with an unmatched branch that is a first-class output.  
Fails as: an inner join drops the rows that did not match and the count is never compared, so the enrichment silently filters.

**`enrich.geo` — Enrich from place**  
Shape: fan-out to parse-the-text and look-up-the-code, join at a reconcile that can disagree with itself.  
Fails as: a ZIP that does not exist in the state it was written with is geocoded to the centroid of something, and the row proceeds with a plausible latitude.
For example: normalise a US address; attach county and CBSA from ZIP; check a city/state/ZIP triple against a reference.

**`enrich.time` — Enrich from time**  
Shape: chain, with the as-of boundary declared as a port rather than assumed.  
Fails as: a feature is computed from data that did not exist yet at the row's timestamp; cross-validation cannot see it and it is fatal in production.
For example: business days since; holiday and fiscal calendars; lags and rolling windows that respect the as-of time.

**`enrich.spacetime` — Enrich from place *and* time**  
Shape: two independent enrichments — one spatial, one temporal — joining at a single record that must agree with both.  
Fails as: the spatial join uses today's boundaries for a five-year-old row, or the temporal join uses tomorrow's weather; each half is defensible and the pair is a leak.
For example: weather at the store on the day of the sale; was there a public holiday, a game, or a storm there then.


### Understand

*Find out what is in it, before anybody fits anything to it.*

| category | the question | template | pack |
|---|---|---|---|
| **`understand.eda`** | What does this dataset actually look like? | `analysis.eda` | — |
| **`understand.anomaly`** | Which of these does not belong? | `detect.anomaly` | — |
| **`understand.cluster`** | Are there natural groups in here? | `learn.unsupervised` | — |
| **`understand.conflict`** | Two reports disagree. Which is wrong, and where? | `data.merge` | — |
| **`understand.flow`** | Where do people drop out, and what did they do first? | `flow.user_actions` | — |

**`understand.eda` — Find out what is in it**  
Shape: fan-out to independent readings, join at a findings list that carries how many comparisons produced it.  
Fails as: two hundred correlations are computed, the three that cleared p<0.05 are reported, and the count of tests is not.

**`understand.anomaly` — Find the strange ones**  
Shape: baseline, score, adjudicate — with a control that must not fire.  
Fails as: the detector flags 3% of everything forever, nobody checks what it does on data known to be clean, and 'it found something' is read as 'something was there'.
Not to be confused with: `condition.validate`.

**`understand.cluster` — Find the groups nobody labelled**  
Shape: represent, cluster, then a stability check as a separate step.  
Fails as: k was chosen by looking at the answer, the clusters are named after the story they suggest, and re-running on a resample produces different groups nobody re-checks.

**`understand.conflict` — Explain why two answers differ**  
Shape: both readings kept as parallel branches, joined at a diff that attributes each difference to a step.  
Fails as: the discrepancy is reconciled by picking the number that matches expectations, and the cause is never located.

**`understand.flow` — Model what users did**  
Shape: events, sessionise, funnel, attribute — a DAG, because attribution and funnel are different readings of one session.  
Fails as: a session boundary of thirty minutes is chosen by convention, and the funnel is computed over sessions rather than people, so a user who returned is counted as two who dropped out.
For example: signup funnel; first-touch vs last-touch attribution.


### Predict

*Fit something, and produce a number with an honest error on it.*

| category | the question | template | pack |
|---|---|---|---|
| **`predict.features`** | What should the model see? | `feature.engineering` | — |
| **`predict.tabular`** | Given these columns, what is the number? | `tabular.supervised` | `tabular` |
| **`predict.family`** | Linear, trees, boosting, a net, or attention? | `model.bakeoff` | `models` |
| **`predict.timeseries`** | What happens next month? | `timeseries.forecast` | — |
| **`predict.policy`** | Which action should I take next time? | `decide.policy` | — |

**`predict.features` — Build features without leaking**  
Shape: fan-out per feature group, join at assembly, with the split *upstream* of every fit.  
Fails as: the encoder is fitted on the whole frame before the split; cross-validation looks excellent and the held-out set does not.

**`predict.tabular` — Fit a model on a table**  
Shape: split, clean, encode numeric ∥ categorical, assemble, fit, evaluate.  
Fails as: the best of forty validation scores is reported as an estimate of future performance; it is the maximum of a sample.

**`predict.family` — Choose the model family**  
Shape: one step with five candidates — the family is a *choice in the graph*, not five pipelines.  
Fails as: the families are compared under different preprocessing, so the comparison measures the preprocessing; or a tie is broken and reported as a winner when the difference is inside the noise.
For example: linear vs tree vs boosted vs MLP vs tabular attention.

**`predict.timeseries` — Forecast forward**  
Shape: the split is a node with two named outputs, so the order of the split cannot be got wrong silently.  
Fails as: a random shuffle is used for validation, the model sees the future, and the backtest is beautiful.

**`predict.policy` — Learn from feedback**  
Shape: propose, act, observe reward, update — plus an off-policy estimate that does not require deploying to find out.  
Fails as: the policy is evaluated on the data its own choices generated, so it looks better the more confidently it was wrong.


### Generate

*Make data that did not exist, and prove it is worth having.*

| category | the question | template | pack |
|---|---|---|---|
| **`generate.tabular`** | Can I have more data that behaves like the real data? | `synth.tabular` | `synth` |
| **`generate.corpus`** | Can I generate training examples? | `synth.corpus` | — |
| **`generate.adversarial`** | What input makes this fail? | `synth.adversarial` | — |
| **`generate.document`** | Turn this data into the report/letter/filing. | `document.assembly` | — |

**`generate.tabular` — Make synthetic rows**  
Shape: generate, then three independent judgements — fidelity, utility, privacy — joining at one decision.  
Fails as: fidelity is measured and utility is assumed; the synthetic rows match every marginal, destroy the correlation the model needed, and training on them scores well on synthetic test data.

**`generate.corpus` — Make synthetic text for training**  
Shape: generate, deduplicate against the real set, filter, mix at a declared ratio, and evaluate against a real-only control.  
Fails as: the generated set contains near-copies of the evaluation set, so the model is tested on its own training data through a paraphrase.

**`generate.adversarial` — Make cases designed to break it**  
Shape: seed, mutate, execute, detect — with the detector written before the attacks, not tuned to them.  
Fails as: the attack set is the set of attacks somebody thought of, and a 100% pass rate is reported as robustness rather than as the coverage of the imagination that produced it.
Not to be confused with: `judge.redteam`.

**`generate.document` — Assemble a document out of records**  
Shape: chain, ending in a check that reads the produced document back.  
Fails as: the template renders, a field is empty, and the document goes out with 'Dear {name}' — nothing read the output.
Not to be confused with: `acquire.document`.


### Judge

*Decide whether something is good enough, in a way that survives being asked how you know.*

| category | the question | template | pack |
|---|---|---|---|
| **`judge.harness`** | Is it good enough, and how would I know? | `eval.harness` | `harness` |
| **`judge.model`** | Can I use a model as the judge? | `eval.judge` | `judge` |
| **`judge.redteam`** | What can I get it to do that it should not? | `eval.redteam` | `redteam` |
| **`judge.release`** | Does this build go out? | `software.release` | — |

**`judge.harness` — Evaluate a system on a set of cases**  
Shape: cases → run → grade → aggregate → report, with the controls as steps in the graph rather than as discipline.  
Fails as: the number goes up; nobody ran the known-bad variant, so nobody knows the grader can tell good from broken.

**`judge.model` — Have a model do the grading**  
Shape: rubric, judge, and a calibration branch against human labels that meets the scores at an agreement step.  
Fails as: the judge prefers longer answers, agrees with itself across runs, and self-agreement is reported as reliability.

**`judge.redteam` — Try to make it misbehave**  
Shape: attack generation ∥ a detector built independently, joining at adjudication.  
Fails as: 'no successful attacks' is reported when the detector was the same component being tested, or when every attack was a variation of one idea.

**`judge.release` — Decide whether to ship**  
Shape: build and test and scan meeting at a gate with more than one input, so it cannot be half-skipped.  
Fails as: the gate reads one input because the others were still running, and green means 'the fast check passed'.


### Orchestrate

*Make several actors — services, models, agents — do work together.*

| category | the question | template | pack |
|---|---|---|---|
| **`orchestrate.agents`** | Split this job across several models and put it back together. | `agent.supervisor_worker` | `agents` |
| **`orchestrate.skill`** | Let it call this function, safely. | `llm.skill` | — |
| **`orchestrate.rag`** | What do our documents say about this? | `rag.retrieval` | — |
| **`orchestrate.request`** | Should this be approved, and on what grounds? | `workflow.approval` | — |
| **`orchestrate.notify`** | Tell them, exactly once, and be able to prove it. | `service.notification` | — |

**`orchestrate.agents` — Supervisor, workers, critic**  
Shape: plan → map over workers → critic → synthesise, where the critic is a separate node with its own inputs.  
Fails as: the supervisor synthesises from worker outputs nobody checked, one worker returned an apology, and the summary reads as if it had five answers.

**`orchestrate.skill` — Give a model a tool**  
Shape: select → validate arguments → execute → verify the effect, with validation as a node the model cannot skip.  
Fails as: the arguments are whatever the model produced, the call succeeded, and nothing checked the tool did the thing.

**`orchestrate.rag` — Answer from a corpus**  
Shape: dense and lexical retrieval as independent branches, joined before reranking.  
Fails as: the answer is fluent, the citations are plausible, and no step checked that the cited passage contains the claim.

**`orchestrate.request` — Decide a request against policy**  
Shape: receive → enrich → decide → fulfil, with the explanation produced from the same values the decision used.  
Fails as: the explanation is written separately from the decision and drifts from it, so the audit trail describes a decision nobody made.

**`orchestrate.notify` — Deliver a message once**  
Shape: authenticate → deduplicate → render → deliver → record.  
Fails as: the send returned 200, the destination never received it, and the record says delivered.
For example: the 551 emails that reported success and produced nothing.


### Operate

*Deliver it, watch it, and notice when it stops being true.*

| category | the question | template | pack |
|---|---|---|---|
| **`operate.monitor`** | Is it still behaving the way it did last week? | `monitor.drift` | — |
| **`operate.render`** | What does the user see, and is it the truth? | `frontend.render` | — |
| **`operate.backfill`** | Re-run this over the last two years. | `data.migrate` | `migrate` |

**`operate.monitor` — Notice when it changed**  
Shape: baseline ∥ current, joined at a comparison that emits nothing when nothing moved.  
Fails as: the monitor alerts on every run, everyone mutes it, and the real change arrives to an audience that stopped reading.

**`operate.render` — Put it in front of a person**  
Shape: fetch → shape → render → measure, where measure reads the rendered output rather than the data that went in.  
Fails as: the component renders, the empty state and the error state look identical, and 'no results' is shown for 'the request failed'.

**`operate.backfill` — Recompute history**  
Shape: the migrate shape with the as-of discipline of enrich.time.  
Fails as: the backfill uses today's reference data for every historical row, so the past is recomputed into something that never happened.
Not to be confused with: `condition.migrate`, `enrich.time`.


---

## What this deliberately does not cover

A taxonomy that claims everything explains nothing, and each of these is
excluded for a structural reason rather than because nobody got round to it.

**Anything with a hard real-time deadline** — control loops, trading paths and audio pipelines are scheduled, not searched; a route chosen from evidence is exactly the wrong idea when the deadline is the requirement.

**Iteration to a fixpoint** — solvers, simulations and training loops iterate until something converges. This model is a DAG on purpose — a loop is expressed by running the graph again with a different input, which is honest but clumsy, and gradient descent should not be drawn as ten thousand nodes.

**Distributed consensus and transactions** — the interesting part is what happens under partition and partial failure, which lives in the coordination protocol, not in the shape of the work.

**Interactive UI state** — a component tree responding to events is a graph, but a cyclic and re-entrant one. `operate.render` covers the data path *into* a view, which is the part that is a pipeline.

**The work of choosing what to build** — no shape, and pretending otherwise is how process documents get written.

---

## The gaps

28 categories have a shape and no code. This is the useful part of
the table: each one is a pack somebody could write in an afternoon, against a
template that already compiles.

| category | what is missing |
|---|---|
| `acquire.harvest` | shape 'web.harvest', no pack |
| `acquire.document` | shape 'document.extraction', no pack |
| `acquire.image` | shape 'image.processing', no pack |
| `acquire.stream` | shape 'stream.window', no pack |
| `condition.impute` | shape 'data.impute', no pack |
| `condition.dedupe` | shape 'entity.resolution', no pack |
| `condition.merge` | shape 'data.merge', no pack |
| `enrich.reference` | shape 'enrich.reference', no pack |
| `enrich.time` | shape 'enrich.time', no pack |
| `understand.eda` | shape 'analysis.eda', no pack |
| `understand.anomaly` | shape 'detect.anomaly', no pack |
| `understand.cluster` | shape 'learn.unsupervised', no pack |
| `understand.conflict` | shape 'data.merge', no pack |
| `understand.flow` | shape 'flow.user_actions', no pack |
| `predict.features` | shape 'feature.engineering', no pack |
| `predict.timeseries` | shape 'timeseries.forecast', no pack |
| `predict.policy` | shape 'decide.policy', no pack |
| `generate.corpus` | shape 'synth.corpus', no pack |
| `generate.adversarial` | shape 'synth.adversarial', no pack |
| `generate.document` | shape 'document.assembly', no pack |
| `judge.release` | shape 'software.release', no pack |
| `orchestrate.skill` | shape 'llm.skill', no pack |
| `orchestrate.rag` | shape 'rag.retrieval', no pack |
| `orchestrate.request` | shape 'workflow.approval', no pack |
| `orchestrate.notify` | shape 'service.notification', no pack |
| `operate.monitor` | shape 'monitor.drift', no pack |
| `operate.render` | shape 'frontend.render', no pack |

---

*Generated from `browsergraph/taxonomy.py`. `tests/test_taxonomy.py` checks that
every category here names a template and pack that exist, that every template
belongs to a category, and that the counts above are counted rather than typed.*
