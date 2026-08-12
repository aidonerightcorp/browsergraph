"""The finite list of shapes engineering work comes in.

The claim this file makes, and it is a strong one: **the pipelines that engineering
teams build are not infinitely various.** Backend request handling, front-end
analytics, data cleaning, enrichment, model training, LLM harnesses — most of it
falls into about forty shapes, and the shapes repeat across domains that have
nothing else in common. A fraud-scoring pipeline and a document extractor are
the same graph with different nouns.

That is not a claim about subject matter. It is a claim about **shape and
failure mode**, which is the only classification worth having here:

* Two jobs are the *same category* when they have the same graph shape and go
  wrong in the same way. "Profile it, check it two independent ways, decide" is
  one category whether the thing being checked is a customer table or a model
  release.
* Two jobs are *different categories* when a correct implementation of one is a
  silently broken implementation of the other. Cleaning and validation look
  alike and are not: a validator that repairs data has hidden the problem it was
  hired to find, and a cleaner that refuses to write has done nothing.

So each category here records three things, and the third is the one that earns
its place:

    question   what somebody wanted to know or have done
    shape      what the graph looks like — fan-out, join, map, gate, loop-free
    fails_as   how it fails *while reporting success*

`fails_as` is the field to read first. A category with no characteristic silent
failure is not a category, it is a topic.

    from assay import taxonomy

    print(taxonomy.catalog_text())          # every family, every category
    print(taxonomy.coverage().text())       # what is expressible, what runs
    taxonomy.get("enrich.spacetime")        # one category
    taxonomy.for_template("eval.harness")   # which categories a shape serves

**Coverage is reported, not claimed.** A category can have a template (the shape
is expressible and checkable), a pack (the code is written and every route of it
is executed by a test), both, or neither. `coverage()` counts them honestly and
`tests/test_taxonomy.py` refuses to let a row name a template or pack that does
not exist — a map whose legend disagrees with the territory is worse than no
map, because people plan against it.

The `template` and `pack` fields name things in **browsergraph**, which is the
reference implementation and not the only possible one. This module imports
nothing from it: the map is a claim about engineering work, and any library that
wants to report coverage against these forty-one shapes can, by naming them.

**What this deliberately does not cover** is listed in `OUT_OF_SCOPE` at the
bottom, with the reason for each. A taxonomy that claims everything explains
nothing, and the honest bound is the interesting part of the claim.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Category:
    """One shape of work, with the way it silently fails."""

    id: str
    family: str
    title: str
    #: What somebody actually wanted. Phrased as a question because a category
    #: is defined by what it answers, not by the technology used to answer it.
    question: str
    #: The graph, in one line: chain, fan-out, join, map, gate, branch.
    shape: str
    #: How a wrong implementation of this reports success. The load-bearing
    #: field — a category without one is a topic with a nice name.
    fails_as: str
    #: Template id that expresses this shape, or "" if nobody has written it.
    template: str = ""
    #: Pack name that runs it end to end, or "" if the shape has no code yet.
    pack: str = ""
    #: Other categories this is most often confused with, and shouldn't be.
    not_to_be_confused_with: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()

    @property
    def expressible(self) -> bool:
        """Is there a checkable shape for this, or only a description?"""
        return bool(self.template)

    @property
    def runnable(self) -> bool:
        """Is there code that executes it, or only a shape?"""
        return bool(self.pack)

    def to_dict(self) -> dict:
        return {"id": self.id, "family": self.family, "title": self.title,
                "question": self.question, "shape": self.shape,
                "fails_as": self.fails_as, "template": self.template,
                "pack": self.pack, "expressible": self.expressible,
                "runnable": self.runnable}


@dataclass(frozen=True)
class Family:
    """A group of categories that share a purpose, not a technology."""

    id: str
    title: str
    purpose: str


FAMILIES: tuple[Family, ...] = (
    Family("acquire", "Acquire",
           "Get material in, from somewhere that does not owe you a schema."),
    Family("condition", "Condition",
           "Make what arrived fit to use, and record every change you made."),
    Family("enrich", "Enrich",
           "Add what the record did not carry, from somewhere that knows."),
    Family("understand", "Understand",
           "Find out what is in it, before anybody fits anything to it."),
    Family("predict", "Predict",
           "Fit something, and produce a number with an honest error on it."),
    Family("generate", "Generate",
           "Make data that did not exist, and prove it is worth having."),
    Family("judge", "Judge",
           "Decide whether something is good enough, in a way that survives "
           "being asked how you know."),
    Family("orchestrate", "Orchestrate",
           "Make several actors — services, models, agents — do work together."),
    Family("operate", "Operate",
           "Deliver it, watch it, and notice when it stops being true."),
)


# --- the catalogue ----------------------------------------------------------
#
# Ordered by family, and within a family roughly by when the work happens. The
# ids are `family.thing` and are stable: a pack, a notebook or somebody's
# internal document may key on them.

CATEGORIES: tuple[Category, ...] = (

    # ---- acquire -----------------------------------------------------------
    Category(
        "acquire.harvest", "acquire", "Harvest from a live source",
        question="What does that page or endpoint say right now?",
        shape="chain with a verify step that is not the fetch step",
        fails_as="the fetch completed, the page was a login wall, and the "
                 "empty result was written as if it were the answer",
        template="web.harvest", pack="",
        examples=("scrape a price", "poll a status endpoint"),
    ),
    Category(
        "acquire.batch", "acquire", "Do the same thing to every file",
        question="I have a folder. What is in all of it?",
        shape="map over a collection, then split ok from failed, then summarise",
        fails_as="one bad file raises, forty good ones are lost, and the log "
                 "says the batch failed rather than which item did",
        template="batch.files", pack="files",
        examples=("parse an inbox of mixed CSV and JSON",),
    ),
    Category(
        "acquire.document", "acquire", "Read a document into records",
        question="What are the fields in this invoice, contract or form?",
        shape="fan-out to text and layout, join at field location",
        fails_as="tables come back as run-on prose, with one confidence for "
                 "the whole document, so nobody can tell which field is shaky",
        template="document.extraction", pack="",
        not_to_be_confused_with=("generate.document",),
    ),
    Category(
        "acquire.image", "acquire", "Read and condition an image",
        question="Is this image usable, and what is in it?",
        shape="fan-out to pixel statistics and content detection, join at a "
              "verdict, then derive the resized copies",
        fails_as="a blank or all-grey image passes every check that looks at "
                 "file size and dimensions, because neither of those looks at "
                 "the pixels",
        template="image.processing", pack="",
    ),
    Category(
        "acquire.stream", "acquire", "Turn an event stream into windows",
        question="What happened in each five-minute bucket?",
        shape="chain with an explicit watermark step before aggregation",
        fails_as="late events land after their window closed and are dropped "
                 "silently, so yesterday's totals quietly change and nothing "
                 "reports that they did",
        template="stream.window", pack="",
    ),

    # ---- condition ---------------------------------------------------------
    Category(
        "condition.validate", "condition", "Decide whether data may proceed",
        question="Is this fit to use, yes or no?",
        shape="fan-out to independent checks, join at one gate that may refuse",
        fails_as="the check runs, writes a warning nobody reads, and the bad "
                 "data flows on — a gate that cannot stop anything is a log line",
        template="data.quality", pack="quality",
        not_to_be_confused_with=("condition.clean",),
    ),
    Category(
        "condition.clean", "condition", "Repair what is repairable",
        question="Can this be made usable without inventing anything?",
        shape="chain where every step emits the record *and* the change it made",
        fails_as="the repair is silent, so a value that was corrected and a "
                 "value that was always right are indistinguishable afterwards",
        template="data.clean", pack="clean",
        not_to_be_confused_with=("condition.validate", "condition.impute"),
    ),
    Category(
        "condition.impute", "condition", "Fill what is missing",
        question="What do I do about the gaps?",
        shape="fan-out to a missingness model and a fill, join at a comparison "
              "against not filling at all",
        fails_as="the imputed values are used as if observed, the model learns "
                 "the fill constant, and accuracy improves on the rows that "
                 "were never missing",
        template="data.impute", pack="",
        not_to_be_confused_with=("condition.clean",),
    ),
    Category(
        "condition.dedupe", "condition", "Decide which records are the same thing",
        question="Are these two rows one customer?",
        shape="block, compare within blocks, cluster, then report what blocking "
              "made unreachable",
        fails_as="blocking cuts the comparisons by 90% and the pairs it made "
                 "impossible to find are never mentioned, so recall is reported "
                 "against the pairs that survived blocking",
        template="entity.resolution", pack="",
    ),
    Category(
        "condition.merge", "condition", "Combine sources that disagree",
        question="Two systems say different things. Which do I write down?",
        shape="join, with an explicit conflict step between the join and the write",
        fails_as="last-write-wins is applied by accident — the join silently "
                 "picks one side, and the disagreement is not recorded anywhere",
        template="data.merge", pack="",
        not_to_be_confused_with=("condition.dedupe", "understand.conflict"),
    ),
    Category(
        "condition.migrate", "condition", "Move it without losing any",
        question="Did everything arrive?",
        shape="count before and count after meeting at a reconcile step",
        fails_as="the write did not raise, a third of the rows are gone, and "
                 "the only count taken was of the target",
        template="data.migrate", pack="migrate",
    ),

    # ---- enrich ------------------------------------------------------------
    Category(
        "enrich.reference", "enrich", "Join a reference table",
        question="What is the name for this code?",
        shape="chain, with an unmatched branch that is a first-class output",
        fails_as="an inner join drops the rows that did not match and the "
                 "count is never compared, so the enrichment silently filters",
        template="enrich.reference", pack="",
    ),
    Category(
        "enrich.geo", "enrich", "Enrich from place",
        question="Where is this address, and is it real?",
        shape="fan-out to parse-the-text and look-up-the-code, join at a "
              "reconcile that can disagree with itself",
        fails_as="a ZIP that does not exist in the state it was written with is "
                 "geocoded to the centroid of something, and the row proceeds "
                 "with a plausible latitude",
        template="enrich.geo", pack="geo",
        examples=("normalise a US address", "attach county and CBSA from ZIP",
                  "check a city/state/ZIP triple against a reference"),
    ),
    Category(
        "enrich.time", "enrich", "Enrich from time",
        question="What else was true about this timestamp?",
        shape="chain, with the as-of boundary declared as a port rather than "
              "assumed",
        fails_as="a feature is computed from data that did not exist yet at the "
                 "row's timestamp; cross-validation cannot see it and it is "
                 "fatal in production",
        template="enrich.time", pack="",
        examples=("business days since", "holiday and fiscal calendars",
                  "lags and rolling windows that respect the as-of time"),
    ),
    Category(
        "enrich.spacetime", "enrich", "Enrich from place *and* time",
        question="Was anything happening in that city that day?",
        shape="two independent enrichments — one spatial, one temporal — "
              "joining at a single record that must agree with both",
        fails_as="the spatial join uses today's boundaries for a five-year-old "
                 "row, or the temporal join uses tomorrow's weather; each half "
                 "is defensible and the pair is a leak",
        template="enrich.spacetime", pack="spacetime",
        examples=("weather at the store on the day of the sale",
                  "was there a public holiday, a game, or a storm there then"),
    ),

    # ---- understand --------------------------------------------------------
    Category(
        "understand.eda", "understand", "Find out what is in it",
        question="What does this dataset actually look like?",
        shape="fan-out to independent readings, join at a findings list that "
              "carries how many comparisons produced it",
        fails_as="two hundred correlations are computed, the three that "
                 "cleared p<0.05 are reported, and the count of tests is not",
        template="analysis.eda", pack="",
    ),
    Category(
        "understand.anomaly", "understand", "Find the strange ones",
        question="Which of these does not belong?",
        shape="baseline, score, adjudicate — with a control that must not fire",
        fails_as="the detector flags 3% of everything forever, nobody checks "
                 "what it does on data known to be clean, and 'it found "
                 "something' is read as 'something was there'",
        template="detect.anomaly", pack="",
        not_to_be_confused_with=("condition.validate",),
    ),
    Category(
        "understand.cluster", "understand", "Find the groups nobody labelled",
        question="Are there natural groups in here?",
        shape="represent, cluster, then a stability check as a separate step",
        fails_as="k was chosen by looking at the answer, the clusters are named "
                 "after the story they suggest, and re-running on a resample "
                 "produces different groups nobody re-checks",
        template="learn.unsupervised", pack="",
    ),
    Category(
        "understand.conflict", "understand", "Explain why two answers differ",
        question="Two reports disagree. Which is wrong, and where?",
        shape="both readings kept as parallel branches, joined at a diff that "
              "attributes each difference to a step",
        fails_as="the discrepancy is reconciled by picking the number that "
                 "matches expectations, and the cause is never located",
        template="data.merge", pack="",
    ),
    Category(
        "understand.flow", "understand", "Model what users did",
        question="Where do people drop out, and what did they do first?",
        shape="events, sessionise, funnel, attribute — a DAG, because "
              "attribution and funnel are different readings of one session",
        fails_as="a session boundary of thirty minutes is chosen by convention, "
                 "and the funnel is computed over sessions rather than people, "
                 "so a user who returned is counted as two who dropped out",
        template="flow.user_actions", pack="",
        examples=("signup funnel", "first-touch vs last-touch attribution"),
    ),

    # ---- predict -----------------------------------------------------------
    Category(
        "predict.features", "predict", "Build features without leaking",
        question="What should the model see?",
        shape="fan-out per feature group, join at assembly, with the split "
              "*upstream* of every fit",
        fails_as="the encoder is fitted on the whole frame before the split; "
                 "cross-validation looks excellent and the held-out set does not",
        template="feature.engineering", pack="",
    ),
    Category(
        "predict.tabular", "predict", "Fit a model on a table",
        question="Given these columns, what is the number?",
        shape="split, clean, encode numeric ∥ categorical, assemble, fit, evaluate",
        fails_as="the best of forty validation scores is reported as an "
                 "estimate of future performance; it is the maximum of a sample",
        template="tabular.supervised", pack="tabular",
    ),
    Category(
        "predict.family", "predict", "Choose the model family",
        question="Linear, trees, boosting, a net, or attention?",
        shape="one step with five candidates — the family is a *choice in the "
              "graph*, not five pipelines",
        fails_as="the families are compared under different preprocessing, so "
                 "the comparison measures the preprocessing; or a tie is broken "
                 "and reported as a winner when the difference is inside the "
                 "noise",
        template="model.bakeoff", pack="models",
        examples=("linear vs tree vs boosted vs MLP vs tabular attention",),
    ),
    Category(
        "predict.timeseries", "predict", "Forecast forward",
        question="What happens next month?",
        shape="the split is a node with two named outputs, so the order of the "
              "split cannot be got wrong silently",
        fails_as="a random shuffle is used for validation, the model sees the "
                 "future, and the backtest is beautiful",
        template="timeseries.forecast", pack="",
    ),
    Category(
        "predict.policy", "predict", "Learn from feedback",
        question="Which action should I take next time?",
        shape="propose, act, observe reward, update — plus an off-policy "
              "estimate that does not require deploying to find out",
        fails_as="the policy is evaluated on the data its own choices "
                 "generated, so it looks better the more confidently it was "
                 "wrong",
        template="decide.policy", pack="",
    ),

    # ---- generate ----------------------------------------------------------
    Category(
        "generate.tabular", "generate", "Make synthetic rows",
        question="Can I have more data that behaves like the real data?",
        shape="generate, then three independent judgements — fidelity, utility, "
              "privacy — joining at one decision",
        fails_as="fidelity is measured and utility is assumed; the synthetic "
                 "rows match every marginal, destroy the correlation the model "
                 "needed, and training on them scores well on synthetic test data",
        template="synth.tabular", pack="synth",
    ),
    Category(
        "generate.corpus", "generate", "Make synthetic text for training",
        question="Can I generate training examples?",
        shape="generate, deduplicate against the real set, filter, mix at a "
              "declared ratio, and evaluate against a real-only control",
        fails_as="the generated set contains near-copies of the evaluation set, "
                 "so the model is tested on its own training data through a "
                 "paraphrase",
        template="synth.corpus", pack="",
    ),
    Category(
        "generate.adversarial", "generate", "Make cases designed to break it",
        question="What input makes this fail?",
        shape="seed, mutate, execute, detect — with the detector written before "
              "the attacks, not tuned to them",
        fails_as="the attack set is the set of attacks somebody thought of, and "
                 "a 100% pass rate is reported as robustness rather than as the "
                 "coverage of the imagination that produced it",
        template="synth.adversarial", pack="",
        not_to_be_confused_with=("judge.redteam",),
    ),
    Category(
        "generate.document", "generate", "Assemble a document out of records",
        question="Turn this data into the report/letter/filing.",
        shape="chain, ending in a check that reads the produced document back",
        fails_as="the template renders, a field is empty, and the document goes "
                 "out with 'Dear {name}' — nothing read the output",
        template="document.assembly", pack="",
        not_to_be_confused_with=("acquire.document",),
    ),

    # ---- judge -------------------------------------------------------------
    Category(
        "judge.harness", "judge", "Evaluate a system on a set of cases",
        question="Is it good enough, and how would I know?",
        shape="cases → run → grade → aggregate → report, with the controls as "
              "steps in the graph rather than as discipline",
        fails_as="the number goes up; nobody ran the known-bad variant, so "
                 "nobody knows the grader can tell good from broken",
        template="eval.harness", pack="harness",
    ),
    Category(
        "judge.model", "judge", "Have a model do the grading",
        question="Can I use a model as the judge?",
        shape="rubric, judge, and a calibration branch against human labels "
              "that meets the scores at an agreement step",
        fails_as="the judge prefers longer answers, agrees with itself across "
                 "runs, and self-agreement is reported as reliability",
        template="eval.judge", pack="judge",
    ),
    Category(
        "judge.redteam", "judge", "Try to make it misbehave",
        question="What can I get it to do that it should not?",
        shape="attack generation ∥ a detector built independently, joining at "
              "adjudication",
        fails_as="'no successful attacks' is reported when the detector was "
                 "the same component being tested, or when every attack was a "
                 "variation of one idea",
        template="eval.redteam", pack="redteam",
    ),
    Category(
        "judge.release", "judge", "Decide whether to ship",
        question="Does this build go out?",
        shape="build and test and scan meeting at a gate with more than one "
              "input, so it cannot be half-skipped",
        fails_as="the gate reads one input because the others were still "
                 "running, and green means 'the fast check passed'",
        template="software.release", pack="",
    ),

    # ---- orchestrate -------------------------------------------------------
    Category(
        "orchestrate.agents", "orchestrate", "Supervisor, workers, critic",
        question="Split this job across several models and put it back together.",
        shape="plan → map over workers → critic → synthesise, where the critic "
              "is a separate node with its own inputs",
        fails_as="the supervisor synthesises from worker outputs nobody "
                 "checked, one worker returned an apology, and the summary "
                 "reads as if it had five answers",
        template="agent.supervisor_worker", pack="agents",
    ),
    Category(
        "orchestrate.skill", "orchestrate", "Give a model a tool",
        question="Let it call this function, safely.",
        shape="select → validate arguments → execute → verify the effect, with "
              "validation as a node the model cannot skip",
        fails_as="the arguments are whatever the model produced, the call "
                 "succeeded, and nothing checked the tool did the thing",
        template="llm.skill", pack="",
    ),
    Category(
        "orchestrate.rag", "orchestrate", "Answer from a corpus",
        question="What do our documents say about this?",
        shape="dense and lexical retrieval as independent branches, joined "
              "before reranking",
        fails_as="the answer is fluent, the citations are plausible, and no "
                 "step checked that the cited passage contains the claim",
        template="rag.retrieval", pack="",
    ),
    Category(
        "orchestrate.request", "orchestrate", "Decide a request against policy",
        question="Should this be approved, and on what grounds?",
        shape="receive → enrich → decide → fulfil, with the explanation "
              "produced from the same values the decision used",
        fails_as="the explanation is written separately from the decision and "
                 "drifts from it, so the audit trail describes a decision "
                 "nobody made",
        template="workflow.approval", pack="",
    ),
    Category(
        "orchestrate.notify", "orchestrate", "Deliver a message once",
        question="Tell them, exactly once, and be able to prove it.",
        shape="authenticate → deduplicate → render → deliver → record",
        fails_as="the send returned 200, the destination never received it, "
                 "and the record says delivered",
        template="service.notification", pack="",
        examples=("the 551 emails that reported success and produced nothing",),
    ),

    # ---- operate -----------------------------------------------------------
    Category(
        "operate.monitor", "operate", "Notice when it changed",
        question="Is it still behaving the way it did last week?",
        shape="baseline ∥ current, joined at a comparison that emits nothing "
              "when nothing moved",
        fails_as="the monitor alerts on every run, everyone mutes it, and the "
                 "real change arrives to an audience that stopped reading",
        template="monitor.drift", pack="",
    ),
    Category(
        "operate.render", "operate", "Put it in front of a person",
        question="What does the user see, and is it the truth?",
        shape="fetch → shape → render → measure, where measure reads the "
              "rendered output rather than the data that went in",
        fails_as="the component renders, the empty state and the error state "
                 "look identical, and 'no results' is shown for 'the request "
                 "failed'",
        template="frontend.render", pack="",
    ),
    Category(
        "operate.backfill", "operate", "Recompute history",
        question="Re-run this over the last two years.",
        shape="the migrate shape with the as-of discipline of enrich.time",
        fails_as="the backfill uses today's reference data for every historical "
                 "row, so the past is recomputed into something that never "
                 "happened",
        template="data.migrate", pack="migrate",
        not_to_be_confused_with=("condition.migrate", "enrich.time"),
    ),
)


BY_ID: dict[str, Category] = {c.id: c for c in CATEGORIES}
BY_FAMILY: dict[str, Family] = {f.id: f for f in FAMILIES}


def get(category_id: str) -> Category:
    try:
        return BY_ID[category_id]
    except KeyError:
        raise KeyError(f"no category {category_id!r}; there are "
                       f"{len(CATEGORIES)}, see taxonomy.catalog_text()") from None


def families() -> tuple[str, ...]:
    return tuple(f.id for f in FAMILIES)


def in_family(family: str) -> tuple[Category, ...]:
    return tuple(c for c in CATEGORIES if c.family == family)


def for_template(template_id: str) -> tuple[Category, ...]:
    """Which categories a shape serves. More than one is the normal case.

    A template that serves several categories is doing its job: `data.migrate`
    is the shape of a migration *and* of a backfill, and pretending those are
    different graphs would be two copies of one thing to keep in step.
    """
    return tuple(c for c in CATEGORIES if c.template == template_id)


def for_pack(pack: str) -> tuple[Category, ...]:
    return tuple(c for c in CATEGORIES if c.pack == pack)


def search(text: str) -> tuple[Category, ...]:
    """Everything mentioning a word, across every field a person might use.

    Deliberately dumb substring matching over the whole record. The question a
    newcomer asks is "is there something in here about addresses", and that is
    answered by looking in the prose, not by a curated keyword list that will
    be out of date by the second contribution.
    """
    needle = text.lower().strip()
    if not needle:
        return ()
    hits = []
    for category in CATEGORIES:
        haystack = " ".join((category.id, category.title, category.question,
                             category.shape, category.fails_as,
                             " ".join(category.examples))).lower()
        if needle in haystack:
            hits.append(category)
    return tuple(hits)


@dataclass(frozen=True)
class Coverage:
    """How much of the map has been walked, counted rather than asserted."""

    total: int = 0
    expressible: int = 0
    runnable: int = 0
    #: category id -> what is missing. Present so a reader can act on it.
    gaps: tuple[tuple[str, str], ...] = ()
    by_family: tuple[tuple[str, int, int, int], ...] = field(default_factory=tuple)

    @property
    def expressible_fraction(self) -> float:
        return self.expressible / self.total if self.total else 0.0

    @property
    def runnable_fraction(self) -> float:
        return self.runnable / self.total if self.total else 0.0

    def text(self) -> str:
        lines = [f"{self.total} categories in {len(self.by_family)} families",
                 f"  {self.expressible:>3} have a checkable shape "
                 f"({self.expressible_fraction:.0%})",
                 f"  {self.runnable:>3} have code that runs "
                 f"({self.runnable_fraction:.0%})",
                 ""]
        lines.append(f"{'family':<12} {'total':>5} {'shape':>6} {'code':>5}")
        for name, total, shaped, coded in self.by_family:
            lines.append(f"{name:<12} {total:>5} {shaped:>6} {coded:>5}")
        if self.gaps:
            lines.append("")
            lines.append(f"{len(self.gaps)} gap(s), which is the useful part:")
            for category_id, missing in self.gaps:
                lines.append(f"  {category_id:<24} {missing}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"total": self.total, "expressible": self.expressible,
                "runnable": self.runnable,
                "gaps": [{"category": c, "missing": m} for c, m in self.gaps],
                "by_family": [{"family": f, "total": t, "expressible": e,
                               "runnable": r} for f, t, e, r in self.by_family]}


def coverage(categories: Sequence[Category] | None = None) -> Coverage:
    """Count what is expressible and what runs. No rounding up.

    The distinction that matters: a **template** means the shape is written
    down and a compiler will reject a wrong filling of it. A **pack** means
    somebody has executed every route of it. The first is a claim about the
    model; the second is a claim about the world, and only the second one can
    embarrass you.
    """
    rows = tuple(categories if categories is not None else CATEGORIES)
    gaps = []
    for category in rows:
        if not category.expressible:
            gaps.append((category.id, "no template — the shape is prose only"))
        elif not category.runnable:
            gaps.append((category.id, f"shape {category.template!r}, no pack"))

    by_family = []
    for family in FAMILIES:
        mine = [c for c in rows if c.family == family.id]
        if mine:
            by_family.append((family.id, len(mine),
                              sum(1 for c in mine if c.expressible),
                              sum(1 for c in mine if c.runnable)))

    return Coverage(total=len(rows),
                    expressible=sum(1 for c in rows if c.expressible),
                    runnable=sum(1 for c in rows if c.runnable),
                    gaps=tuple(gaps), by_family=tuple(by_family))


def catalog_text(family: str = "") -> str:
    """The whole map, or one family of it, for a terminal and for a model."""
    lines = []
    for definition in FAMILIES:
        if family and definition.id != family:
            continue
        mine = in_family(definition.id)
        if not mine:
            continue
        lines.append(f"\n{definition.title.upper()} — {definition.purpose}")
        for category in mine:
            marks = ("shape" if category.expressible else "     ",
                     "code" if category.runnable else "    ")
            lines.append(f"  [{' '.join(marks)}] {category.id:<24} "
                         f"{category.title}")
            lines.append(f"{'':<32} {category.question}")
            lines.append(f"{'':<32} fails as: {category.fails_as}")
    return "\n".join(lines).lstrip("\n")


#: What this map deliberately leaves out, and why. A taxonomy that claims
#: everything explains nothing — and each of these is excluded for a structural
#: reason, not because nobody got round to it.
OUT_OF_SCOPE: tuple[tuple[str, str], ...] = (
    ("Anything with a hard real-time deadline",
     "control loops, trading paths and audio pipelines are scheduled, not "
     "searched; a route chosen from evidence is exactly the wrong idea when "
     "the deadline is the requirement."),
    ("Iteration to a fixpoint",
     "solvers, simulations and training loops iterate until something "
     "converges. This model is a DAG on purpose — a loop is expressed by "
     "running the graph again with a different input, which is honest but "
     "clumsy, and gradient descent should not be drawn as ten thousand nodes."),
    ("Distributed consensus and transactions",
     "the interesting part is what happens under partition and partial "
     "failure, which lives in the coordination protocol, not in the shape of "
     "the work."),
    ("Interactive UI state",
     "a component tree responding to events is a graph, but a cyclic and "
     "re-entrant one. `operate.render` covers the data path *into* a view, "
     "which is the part that is a pipeline."),
    ("The work of choosing what to build",
     "no shape, and pretending otherwise is how process documents get written."),
)
