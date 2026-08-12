"""Skeletons of problems, so nobody has to invent the shape of a known one.

A harness pointed at "win this Kaggle competition" has two jobs: work out what
the *steps* are, and work out what should perform each one. The first job has
been done thousands of times and has a boring, stable answer — load, split,
clean, encode numeric and categorical features separately, join them, fit,
calibrate, ensemble, submit. The second job is the interesting one and is where
search and evidence belong.

Templates exist so the boring job is a lookup. A template is a graph with typed
ports and edges and **no candidates**: it says a stage called `encode_categorical`
consumes `Categorical` and produces `Matrix`, and says nothing about whether
that is target encoding or one-hot. Filling those slots is `instantiate`.

Why this is not just documentation:

* A skeleton is *checkable*. Instantiate it wrongly and the compiler rejects it
  at a port, immediately, with a reason — rather than a model discovering three
  stages later that it produced a frame where a matrix was needed.
* A skeleton is *shared*. Two harnesses that both start from `tabular.supervised`
  produce comparable graphs, so evidence from one is worth something to the
  other. Two harnesses that each invented a pipeline produce two snowflakes.
* A skeleton *bounds the search*. Stages fix the shape, so the space to explore
  is the product over slots — large, but not the space of all graphs, which is
  infinite and is what "search the entire space" people are objecting to when
  they say this cannot work.

The anti-patterns are carried with each template rather than in a document,
because guidance nobody loads is guidance nobody follows, and a harness that has
the template in context has the warnings in context too.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from browsergraph.manifest import PortSpec
from browsergraph.workbench import (
    Edge,
    NodeCandidate,
    StageDefinition,
    WorkbenchDefinition,
)


@dataclass(frozen=True)
class Slot:
    """One step of a known problem, with its contract and none of its content."""
    id: str
    name: str
    inputs: tuple[tuple[str, str], ...] = ()
    outputs: tuple[tuple[str, str], ...] = ()
    #: What a candidate for this slot has to be able to do. A registry query,
    #: not a description — this is how discovery finds fillers.
    capabilities: tuple[str, ...] = ()
    description: str = ""
    optional: bool = False
    #: atomic, map or branch. Without this a template could not say "do it to
    #: every item" or "take one of these paths", so a batch template rendered
    #: as a chain and lied about its own shape.
    kind: str = "atomic"

    def to_stage(self, candidates: Sequence[str] = ()) -> StageDefinition:
        return StageDefinition(
            id=self.id, name=self.name, description=self.description,
            optional=self.optional, kind=self.kind,
            required_capabilities=self.capabilities,
            inputs=tuple(PortSpec(n, t) for n, t in self.inputs),
            outputs=tuple(PortSpec(n, t) for n, t in self.outputs),
            candidates=tuple(candidates))


@dataclass(frozen=True)
class Template:
    """A problem shape: slots, wiring, and the mistakes people make in it."""
    id: str
    domain: str
    title: str
    task: str
    slots: tuple[Slot, ...]
    edges: tuple[Edge, ...] = ()
    #: Free prose, deliberately opinionated. These are the errors seen in this
    #: shape, phrased so a model reading them can act.
    anti_patterns: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def slot_ids(self) -> tuple[str, ...]:
        return tuple(s.id for s in self.slots)

    def skeleton(self) -> WorkbenchDefinition:
        """The empty shape — every port typed, every slot unfilled.

        Useful on its own: it draws, it validates its own wiring, and it is what
        you hand a model when the question is "what would fill this?"
        """
        return WorkbenchDefinition(
            title=self.title, task=self.task,
            stages=tuple(s.to_stage() for s in self.slots),
            edges=self.edges,
            metadata={"template": self.id, "domain": self.domain,
                      "anti_patterns": list(self.anti_patterns)})

    def instantiate(self, filling: Mapping[str, Sequence[str]], *,
                    strict: bool = True) -> WorkbenchDefinition:
        """Fill the slots with candidate ids and get a real workbench.

        `strict` refuses a filling that names a slot this template does not
        have. That is nearly always a typo or a stale template id, and letting
        it through produces a graph that silently omits a step — the most
        expensive failure in the whole system, because everything downstream
        still compiles.
        """
        unknown = set(filling) - set(self.slot_ids)
        if unknown and strict:
            raise KeyError(
                f"template {self.id!r} has no slot(s) {sorted(unknown)}; "
                f"its slots are {list(self.slot_ids)}")

        stages = tuple(s.to_stage(filling.get(s.id, ())) for s in self.slots)
        candidates = tuple(
            NodeCandidate(id=cid, node_id=cid)
            for cid in dict.fromkeys(c for ids in filling.values() for c in ids))
        return WorkbenchDefinition(
            title=self.title, task=self.task, stages=stages, edges=self.edges,
            candidates=candidates,
            metadata={"template": self.id, "domain": self.domain,
                      "anti_patterns": list(self.anti_patterns)})

    def unfilled(self, filling: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
        """Slots with nothing in them yet — the harness's to-do list.

        Optional slots are excluded. A template that reported its optional steps
        as missing forever would train a model to fill them with anything, which
        is worse than leaving them out.
        """
        return tuple(s.id for s in self.slots
                     if not s.optional and not filling.get(s.id))


def _slots(*rows) -> tuple[Slot, ...]:
    return tuple(Slot(*row) if not isinstance(row, Slot) else row for row in rows)


# --- the catalogue ----------------------------------------------------------
#
# Deliberately small and deliberately across unlike domains. Six templates that
# each look different are a better argument for generality than sixty variations
# on a data pipeline, and a catalogue nobody can read is a catalogue nobody uses.

TABULAR_SUPERVISED = Template(
    id="tabular.supervised",
    domain="machine learning",
    title="Supervised tabular pipeline",
    task="Fit a model on a tabular dataset and produce scored predictions.",
    slots=_slots(
        Slot("load", "Load dataset", (), (("out", "Frame"),),
             ("data.read",), "Read the raw table."),
        Slot("split", "Split", (("in", "Frame"),),
             (("train", "Frame"), ("valid", "Frame")),
             ("data.split",),
             "Hold out before anything is fitted. Order matters more than method."),
        Slot("clean", "Clean", (("in", "Frame"),), (("out", "Frame"),),
             ("data.clean",), "Missing values, outliers, dtypes."),
        Slot("numeric", "Encode numeric", (("in", "Frame"),), (("out", "Matrix"),),
             ("feature.numeric",), "Scaling, binning, transforms."),
        Slot("categorical", "Encode categorical", (("in", "Frame"),),
             (("out", "Matrix"),), ("feature.categorical",),
             "One-hot, target, ordinal, hashing."),
        Slot("assemble", "Assemble features",
             (("numeric", "Matrix"), ("categorical", "Matrix")),
             (("out", "Matrix"),), ("feature.assemble",),
             "The join. Two independent encodings meeting is why this is a "
             "graph and not a list."),
        Slot("fit", "Fit model", (("in", "Matrix"),), (("out", "Model"),),
             ("model.fit",), "The step everyone starts with and that matters least."),
        Slot("calibrate", "Calibrate", (("in", "Model"),), (("out", "Model"),),
             ("model.calibrate",), "Probabilities that mean what they say.",
             True),
        Slot("evaluate", "Evaluate", (("in", "Model"),), (("out", "Score"),),
             ("model.evaluate",), "Against the held-out split, not the training one."),
    ),
    edges=(Edge("load", "split"), Edge("split", "clean", from_port="train"),
           Edge("clean", "numeric"), Edge("clean", "categorical"),
           Edge("numeric", "assemble", to_port="numeric"),
           Edge("categorical", "assemble", to_port="categorical"),
           Edge("assemble", "fit"), Edge("fit", "calibrate"),
           Edge("calibrate", "evaluate")),
    anti_patterns=(
        "Fitting the encoder on the full frame before splitting. The leak is "
        "invisible in cross-validation and fatal on the held-out set — this is "
        "the single most common way a good-looking pipeline is wrong.",
        "Treating 'more preprocessing steps' as progress. Each step is a place "
        "to leak and a thing to maintain; the count is not the score.",
        "Ensembling models that share a failure mode. Three gradient-boosted "
        "trees on the same features are one model with more variance.",
        "Reporting the best validation score seen across many attempts as if it "
        "were an estimate of future performance. It is the maximum of a sample "
        "and biased upward by exactly the amount of searching you did.",
    ),
    notes=("Slots are the shape; which encoder or model fills them is what "
           "search and receipts are for.",),
)

DOCUMENT_EXTRACTION = Template(
    id="document.extraction",
    domain="documents",
    title="Document extraction",
    task="Turn a document into typed records with a confidence per field.",
    slots=_slots(
        Slot("acquire", "Acquire", (), (("out", "Bytes"),), ("io.read",)),
        # Produces `Document`, not `Format`. An earlier draft had this emit a
        # bare format and fed it to two slots that consume `Bytes` — which the
        # compiler refused the first time the template was actually
        # instantiated, with "edge detect -> layout: 'Format' is not a 'Bytes'".
        # The template was wrong, the checker was right, and the fix is for
        # detection to carry the bytes forward alongside what it worked out.
        Slot("detect", "Detect format", (("in", "Bytes"),),
             (("out", "Document"),), ("doc.detect",),
             "Sniff rather than trust the extension, and pass the bytes on."),
        Slot("text", "Extract text", (("in", "Document"),), (("out", "Text"),),
             ("doc.text",)),
        Slot("layout", "Extract layout", (("in", "Document"),),
             (("out", "Layout"),), ("doc.layout",),
             "Independent of text extraction: a table is a layout fact."),
        Slot("fields", "Locate fields",
             (("text", "Text"), ("layout", "Layout")), (("out", "Fields"),),
             ("doc.fields",), "The join, and where most extractors go wrong."),
        Slot("normalise", "Normalise", (("in", "Fields"),), (("out", "Record"),),
             ("doc.normalise",), "Dates, currencies, names into typed values."),
        Slot("verify", "Verify", (("in", "Record"),), (("out", "Record"),),
             ("doc.verify",), "Cross-field checks: totals, ranges, formats."),
    ),
    edges=(Edge("acquire", "detect"), Edge("detect", "text"),
           Edge("detect", "layout"), Edge("text", "fields", to_port="text"),
           Edge("layout", "fields", to_port="layout"),
           Edge("fields", "normalise"), Edge("normalise", "verify")),
    anti_patterns=(
        "Reading text and ignoring layout, then wondering why tables come out "
        "as run-on prose. They are two different extractions of the same bytes.",
        "One confidence for the whole document. Confidence is per field, or it "
        "is decoration.",
        "Sending an unverified record downstream because the model sounded "
        "certain. Verification is a cross-field arithmetic check, not a vibe.",
    ),
)

SERVICE_NOTIFICATION = Template(
    id="service.notification",
    domain="services",
    title="Event-driven notification",
    task="Turn a domain event into a delivered, idempotent, auditable message.",
    slots=_slots(
        Slot("receive", "Receive event", (), (("out", "Event"),),
             ("net.receive",), "Webhook, queue or poll."),
        Slot("authenticate", "Authenticate", (("in", "Event"),),
             (("out", "Event"),), ("auth.verify",),
             "Signature or token. Before anything is read."),
        Slot("deduplicate", "Deduplicate", (("in", "Event"),), (("out", "Event"),),
             ("state.dedupe",),
             "At-least-once delivery is the norm; exactly-once is your job."),
        Slot("enrich", "Enrich", (("in", "Event"),), (("out", "Context"),),
             ("data.lookup",), "Fetch what the event references."),
        Slot("policy", "Apply policy", (("in", "Context"),), (("out", "Decision"),),
             ("policy.decide",),
             "Quiet hours, preferences, rate limits, consent."),
        Slot("render", "Render message", (("in", "Decision"),),
             (("out", "Message"),), ("render.template",)),
        Slot("deliver", "Deliver", (("in", "Message"),), (("out", "Receipt"),),
             ("net.send",), "The only step with an outward effect."),
        Slot("record", "Record", (("in", "Receipt"),), (("out", "Audit"),),
             ("state.write",), "What was sent, to whom, why, and under which policy."),
    ),
    edges=(Edge("receive", "authenticate"), Edge("authenticate", "deduplicate"),
           Edge("deduplicate", "enrich"), Edge("enrich", "policy"),
           Edge("policy", "render"), Edge("render", "deliver"),
           Edge("deliver", "record")),
    anti_patterns=(
        "Deduplicating after enrichment. The expensive lookup runs on every "
        "retry, and retries are the normal case, not the exception.",
        "Deciding policy inside the renderer. 'Do not send' then depends on a "
        "template, and nobody can answer why a message went out.",
        "Retrying delivery without an idempotency key. At-least-once plus retry "
        "is how one event becomes eleven text messages.",
        "Treating the audit record as optional because it has no user-visible "
        "effect. It is the only evidence the policy was applied at all.",
    ),
)

WEB_HARVEST = Template(
    id="web.harvest",
    domain="web",
    title="Web harvest",
    task="Turn an authorised reference into a verified, receipted record.",
    slots=_slots(
        Slot("resolve", "Resolve target", (), (("out", "Target"),),
             ("target.resolve",)),
        Slot("session", "Open session", (("in", "Target"),), (("out", "Session"),),
             ("session.open",)),
        Slot("read", "Read payload", (("in", "Session"),), (("out", "Bytes"),),
             ("payload.read",)),
        Slot("parse", "Parse structure", (("in", "Bytes"),), (("out", "Dom"),),
             ("parse.structure",)),
        Slot("locate", "Locate values", (("in", "Dom"),), (("out", "Fields"),),
             ("locate.values",)),
        Slot("normalise", "Normalise", (("in", "Fields"),), (("out", "Record"),),
             ("normalise.values",)),
        Slot("verify", "Verify shape", (("in", "Record"),), (("out", "Record"),),
             ("verify.shape",)),
        Slot("receipt", "Write receipt", (("in", "Record"),), (("out", "Receipt"),),
             ("receipt.write",)),
    ),
    edges=(),  # a chain; `wiring()` infers it
    anti_patterns=(
        "Selecting on a CSS path that encodes the page's current layout. It "
        "works today and silently returns the wrong column next month.",
        "Judging a route by whether it completed. A route that returns an empty "
        "record 'succeeds' — the check is on the record, not the run.",
    ),
)

DATA_QUALITY = Template(
    id="data.quality",
    domain="data engineering",
    title="Data quality gate",
    task="Decide whether a dataset may proceed, with the reason recorded.",
    slots=_slots(
        Slot("profile", "Profile", (), (("out", "Profile"),), ("data.profile",)),
        Slot("schema", "Check schema", (("in", "Profile"),), (("out", "Findings"),),
             ("check.schema",)),
        Slot("distribution", "Check distribution", (("in", "Profile"),),
             (("out", "Findings"),), ("check.distribution",),
             "Drift against a reference, not against intuition."),
        Slot("adjudicate", "Adjudicate",
             (("schema", "Findings"), ("distribution", "Findings")),
             (("out", "Verdict"),), ("gate.decide",),
             "Two independent checks meeting at one decision."),
    ),
    edges=(Edge("profile", "schema"), Edge("profile", "distribution"),
           Edge("schema", "adjudicate", to_port="schema"),
           Edge("distribution", "adjudicate", to_port="distribution")),
    anti_patterns=(
        "A gate that only ever warns. If nothing is ever blocked, the gate is a "
        "logging statement with a budget.",
        "Thresholds chosen to make today's data pass. Choose them against a "
        "reference period, and record which one.",
    ),
)

RELEASE = Template(
    id="software.release",
    domain="software delivery",
    title="Build, verify and release",
    task="Take a commit to a released artefact without a human remembering a step.",
    slots=_slots(
        Slot("checkout", "Check out", (), (("out", "Source"),), ("vcs.read",)),
        Slot("build", "Build", (("in", "Source"),), (("out", "Artifact"),),
             ("build.run",)),
        Slot("test", "Test", (("in", "Artifact"),), (("out", "Findings"),),
             ("test.run",)),
        Slot("scan", "Scan", (("in", "Artifact"),), (("out", "Findings"),),
             ("security.scan",), "Independent of tests, and often disagrees."),
        Slot("gate", "Gate", (("test", "Findings"), ("scan", "Findings")),
             (("out", "Verdict"),), ("gate.decide",)),
        Slot("publish", "Publish", (("in", "Verdict"),), (("out", "Receipt"),),
             ("release.publish",), "The only step with an outward effect."),
    ),
    edges=(Edge("checkout", "build"), Edge("build", "test"), Edge("build", "scan"),
           Edge("test", "gate", to_port="test"),
           Edge("scan", "gate", to_port="scan"), Edge("gate", "publish")),
    anti_patterns=(
        "Publishing on a green test run without the scan, because the scan is "
        "slow. Then the gate has one input and is not a gate.",
        "Rebuilding between test and publish. The artefact that was verified "
        "must be the artefact that ships, by digest.",
    ),
)

RETRIEVAL_QA = Template(
    id="rag.retrieval",
    domain="language models",
    title="Retrieval-augmented answering",
    task="Answer a question from a corpus, with the passages that justify it.",
    slots=_slots(
        Slot("ingest", "Ingest corpus", (), (("out", "Corpus"),), ("data.read",)),
        Slot("chunk", "Chunk", (("in", "Corpus"),), (("out", "Chunks"),),
             ("text.chunk",), "Where most retrieval quality is won or lost."),
        Slot("embed", "Embed", (("in", "Chunks"),), (("out", "Vectors"),),
             ("text.embed",)),
        Slot("lexical", "Lexical index", (("in", "Chunks"),),
             (("out", "Index"),), ("text.index",),
             "Independent of embedding: BM25 finds what vectors miss."),
        Slot("retrieve", "Retrieve",
             (("dense", "Vectors"), ("sparse", "Index")), (("out", "Passages"),),
             ("search.hybrid",), "The join. Two recall strategies, one candidate set."),
        Slot("rerank", "Rerank", (("in", "Passages"),), (("out", "Passages"),),
             ("search.rerank",), "", True),
        Slot("generate", "Generate", (("in", "Passages"),), (("out", "Answer"),),
             ("llm.generate",)),
        Slot("attribute", "Attribute", (("in", "Answer"),), (("out", "Answer"),),
             ("llm.attribute",),
             "Every claim tied to a passage, or the answer is not supported."),
    ),
    edges=(Edge("ingest", "chunk"), Edge("chunk", "embed"),
           Edge("chunk", "lexical"),
           Edge("embed", "retrieve", to_port="dense"),
           Edge("lexical", "retrieve", to_port="sparse"),
           Edge("retrieve", "rerank"), Edge("rerank", "generate"),
           Edge("generate", "attribute")),
    anti_patterns=(
        "Evaluating the generator when retrieval is what failed. If the passage "
        "was never retrieved, no prompt fixes it — measure recall@k first.",
        "Dense retrieval only, because embeddings are the interesting part. "
        "Exact identifiers, rare names and codes are precisely what BM25 gets "
        "and vectors blur.",
        "Chunking by a fixed token count because it is easy. The chunk boundary "
        "decides what can ever be retrieved together.",
        "Reporting an answer without attribution and calling the system "
        "grounded. Grounding is a checkable property, not an architecture.",
    ),
)

TIMESERIES_FORECAST = Template(
    id="timeseries.forecast",
    domain="forecasting",
    title="Time-series forecast",
    task="Forecast future values with an honest estimate of the error.",
    slots=_slots(
        Slot("load", "Load series", (), (("out", "Series"),), ("data.read",)),
        Slot("calendar", "Split by time", (("in", "Series"),),
             (("train", "Series"), ("valid", "Series")), ("data.split_time",),
             "By time, never at random. A shuffled split leaks the future."),
        Slot("impute", "Handle gaps", (("in", "Series"),), (("out", "Series"),),
             ("series.impute",)),
        Slot("seasonal", "Seasonal features", (("in", "Series"),),
             (("out", "Matrix"),), ("feature.seasonal",)),
        Slot("exogenous", "Exogenous features", (("in", "Series"),),
             (("out", "Matrix"),), ("feature.exogenous",),
             "Holidays, prices, weather — independent of seasonality."),
        Slot("assemble", "Assemble",
             (("seasonal", "Matrix"), ("exogenous", "Matrix")),
             (("out", "Matrix"),), ("feature.assemble",)),
        Slot("fit", "Fit", (("in", "Matrix"),), (("out", "Model"),),
             ("model.fit",)),
        Slot("backtest", "Backtest", (("in", "Model"),), (("out", "Score"),),
             ("model.backtest",),
             "Rolling origin. A single hold-out is one sample of one regime."),
    ),
    edges=(Edge("load", "calendar"), Edge("calendar", "impute", from_port="train"),
           Edge("impute", "seasonal"), Edge("impute", "exogenous"),
           Edge("seasonal", "assemble", to_port="seasonal"),
           Edge("exogenous", "assemble", to_port="exogenous"),
           Edge("assemble", "fit"), Edge("fit", "backtest")),
    anti_patterns=(
        "Splitting at random. Every row after the split leaks into training and "
        "the score becomes meaningless in the one way nobody notices.",
        "Computing a rolling feature over the whole series before splitting. "
        "The window reaches across the boundary and the leak is silent.",
        "One hold-out period reported as 'the' error. Different regimes have "
        "different errors; rolling origin measures that, a single split hides it.",
    ),
)

BATCH_FILES = Template(
    id="batch.files",
    domain="data engineering",
    title="Process a folder",
    task="Do the same thing to every file, and say which ones failed.",
    slots=_slots(
        Slot("list", "List the inputs", (), (("out", "List[Path]"),),
             ("io.list",)),
        Slot("each", "Process each one", (("in", "List[Path]"),),
             (("out", "List[Record]"),), ("work.one",),
             "Runs once per item. The node inside handles one file.",
             kind="map"),
        Slot("split", "Good from bad", (("in", "List[Record]"),),
             (("ok", "List[Record]"), ("bad", "List[Record]")),
             ("split.outcome",)),
        Slot("summarise", "Summarise", (("in", "List[Record]"),),
             (("out", "Summary"),), ("summarise",)),
    ),
    edges=(Edge("list", "each"), Edge("each", "split"),
           Edge("split", "summarise", from_port="ok")),
    anti_patterns=(
        "Failing the whole batch because one file was bad. Split the outcomes "
        "and carry the failures forward with a reason each.",
        "Reporting 'processed 40 files' when eight of them threw. The count "
        "that matters is how many produced a usable result.",
        "Loading every file before processing any. A folder is not always "
        "small, and the first error then arrives after the memory has gone.",
    ),
    notes=("The middle slot is a map: it consumes a collection and its node "
           "handles one item.",),
)

APPROVAL = Template(
    id="workflow.approval",
    domain="business process",
    title="Request, decide, act",
    task="Take a request, decide it against policy, and do the right thing.",
    slots=_slots(
        Slot("receive", "Receive the request", (), (("out", "Request"),),
             ("io.receive",)),
        Slot("enrich", "Look up the context", (("in", "Request"),),
             (("out", "Context"),), ("data.lookup",)),
        Slot("decide", "Approve or refuse", (("in", "Context"),),
             (("approved", "Decision"), ("refused", "Decision")),
             ("policy.decide",),
             "A branch: one way out, and the other path never runs.",
             kind="branch"),
        Slot("fulfil", "Do the thing", (("in", "Decision"),),
             (("out", "Receipt"),), ("act.fulfil",)),
        Slot("explain", "Say why not", (("in", "Decision"),),
             (("out", "Receipt"),), ("act.explain",)),
    ),
    edges=(Edge("receive", "enrich"), Edge("enrich", "decide"),
           Edge("decide", "fulfil", from_port="approved"),
           Edge("decide", "explain", from_port="refused")),
    anti_patterns=(
        "A refusal with no explanation. The person on the other end has to know "
        "what would change the answer, or they will simply ask again.",
        "Deciding inside the step that acts. Then 'why was this approved' has "
        "no answer that does not involve reading the fulfilment code.",
        "Treating the refused path as an error. It is a correct outcome and "
        "logging it as a failure makes the error rate meaningless.",
    ),
)

MIGRATE = Template(
    id="data.migrate",
    domain="data engineering",
    title="Move data, and prove it arrived",
    task="Copy records from one store to another without losing or duplicating any.",
    slots=_slots(
        Slot("read", "Read the source", (), (("out", "Records"),), ("data.read",)),
        Slot("count_before", "Count what left", (("in", "Records"),),
             (("out", "Tally"),), ("data.count",)),
        Slot("transform", "Reshape", (("in", "Records"),), (("out", "Records"),),
             ("data.transform",)),
        Slot("write", "Write the target", (("in", "Records"),),
             (("out", "Receipt"),), ("data.write",)),
        Slot("count_after", "Count what arrived", (("in", "Receipt"),),
             (("out", "Tally"),), ("data.count",)),
        Slot("reconcile", "Reconcile",
             (("before", "Tally"), ("after", "Tally")), (("out", "Verdict"),),
             ("data.reconcile",),
             "The join, and the only step that can say the migration worked."),
    ),
    edges=(Edge("read", "count_before"), Edge("read", "transform"),
           Edge("transform", "write"), Edge("write", "count_after"),
           Edge("count_before", "reconcile", to_port="before"),
           Edge("count_after", "reconcile", to_port="after")),
    anti_patterns=(
        "Declaring success because the write did not raise. A write that "
        "silently dropped every third row also does not raise.",
        "Counting the target only. Without the source count there is nothing "
        "to compare it against, and 'we moved 9,412 rows' is not a claim.",
        "Reconciling on totals alone. Equal counts with swapped contents look "
        "identical; check a checksum or a sample of keys as well.",
    ),
)

# ============================================================================
# The rest of the map.
#
# The eleven above were written one at a time, as each was needed. What follows
# was written against `taxonomy.py`, which is a claim that engineering pipelines
# come in about forty shapes — so these exist to make that claim checkable
# rather than to be individually admired. Each one is the shape of a category in
# that map, and `tests/test_taxonomy.py` refuses to let the map name a template
# that is not here.
#
# They are grouped by what they are *for*, in the order work happens: condition
# what arrived, enrich it, understand it, predict from it, generate more of it,
# judge the result, orchestrate the actors, operate the thing.
# ============================================================================

# --- condition: make what arrived fit to use --------------------------------

DATA_CLEAN = Template(
    id="data.clean",
    domain="data engineering",
    title="Repair what is repairable",
    task="Fix what can be fixed without inventing anything, and record every change.",
    slots=_slots(
        Slot("load", "Load", (), (("out", "Frame"),), ("data.read",)),
        Slot("detect", "Find the problems", (("in", "Frame"),),
             (("out", "Issues"),), ("data.detect",),
             "What is wrong, before anything is changed."),
        Slot("repair", "Repair",
             (("frame", "Frame"), ("issues", "Issues")),
             (("out", "Frame"), ("changes", "ChangeLog")),
             ("data.repair",),
             "Two outputs, and the second one is not optional. A repair "
             "nobody can see afterwards is indistinguishable from data that "
             "was always right."),
        Slot("ledger", "Account for it",
             (("changes", "ChangeLog"), ("issues", "Issues")),
             (("out", "ChangeLog"),), ("data.account",),
             "Found against fixed. The difference is what you still have."),
        Slot("verify", "Check the repair",
             (("frame", "Frame"), ("ledger", "ChangeLog")),
             (("out", "Verdict"),), ("data.verify",),
             "Re-read the repaired frame. A repair counted rather than "
             "re-checked is a hope."),
    ),
    edges=(Edge("load", "detect"), Edge("load", "repair", to_port="frame"),
           Edge("detect", "repair", to_port="issues"),
           Edge("repair", "ledger", from_port="changes", to_port="changes"),
           Edge("detect", "ledger", to_port="issues"),
           Edge("repair", "verify", from_port="out", to_port="frame"),
           Edge("ledger", "verify", to_port="ledger")),
    anti_patterns=(
        "Repairing silently. A corrected value and a value that was always "
        "right must not be indistinguishable downstream, or the next person "
        "to ask 'is this figure real' has no way to find out.",
        "Cleaning inside the validator. The validator's job is to find the "
        "problem; a validator that fixes it has destroyed the evidence that "
        "there was one, and the upstream source never gets told.",
        "Dropping rows as a repair without reporting how many. Deletion is "
        "the most aggressive repair there is and the easiest one to hide.",
        "Counting repairs as fixes. The verify step re-reads the frame "
        "because 'we applied 412 corrections' is a measure of effort.",
    ),
    notes=("Distinct from `data.quality`, which decides and does not touch. "
           "Cleaning changes the data; validation refuses it. Doing both in "
           "one step is how a dataset gets quietly improved into a shape "
           "nobody can reproduce.",),
)

DATA_IMPUTE = Template(
    id="data.impute",
    domain="data engineering",
    title="Fill the gaps, and find out whether that helped",
    task="Decide what to do about missing values, against the option of doing nothing.",
    slots=_slots(
        Slot("load", "Load", (), (("out", "Frame"),), ("data.read",)),
        Slot("missingness", "Model the missingness", (("in", "Frame"),),
             (("out", "Missingness"),), ("data.missingness",),
             "Is it missing at random, or is missing itself informative? "
             "Filling before asking this is the whole error."),
        Slot("fill", "Fill",
             (("frame", "Frame"), ("pattern", "Missingness")),
             (("out", "Frame"), ("mask", "Mask")), ("data.impute",),
             "The mask travels with the frame. A filled value used as if "
             "observed is a fabrication with a straight face."),
        Slot("control", "Do nothing", (("in", "Frame"),), (("out", "Frame"),),
             ("data.control",),
             "The comparison arm. Dropping the incomplete rows, or leaving "
             "the holes in — whichever the downstream step can take."),
        Slot("compare", "Did it help?",
             (("filled", "Frame"), ("control", "Frame")),
             (("out", "Findings"),), ("data.compare",),
             "The join. Imputation that was never compared against not "
             "imputing is a preference, not a decision."),
    ),
    edges=(Edge("load", "missingness"), Edge("load", "fill", to_port="frame"),
           Edge("missingness", "fill", to_port="pattern"),
           Edge("load", "control"),
           Edge("fill", "compare", from_port="out", to_port="filled"),
           Edge("control", "compare", to_port="control")),
    anti_patterns=(
        "Using the filled values as if they were observed. Carry the mask, or "
        "the model learns the fill constant and reports it as signal.",
        "Fitting the imputer on the whole frame before the split. The column "
        "mean computed over the validation rows is a leak, and a small one "
        "that survives every review because it looks like housekeeping.",
        "Never running the control. 'We imputed' answers a different question "
        "from 'imputing was better than not', and only the second is a result.",
        "Imputing the target. If the answer was missing, that row is not "
        "training data, it is a prediction you are grading yourself on.",
    ),
)

ENTITY_RESOLUTION = Template(
    id="entity.resolution",
    domain="data engineering",
    title="Decide which records are the same thing",
    task="Match records that refer to one real entity, and say what blocking made unreachable.",
    slots=_slots(
        Slot("load", "Load records", (), (("out", "Records"),), ("data.read",)),
        Slot("block", "Block",
             (("in", "Records"),),
             (("pairs", "Pairs"), ("forfeited", "Coverage")),
             ("match.block",),
             "Two outputs, because the pairs you chose not to compare are a "
             "result. Blocking is a recall decision wearing a performance "
             "optimisation's clothes."),
        Slot("compare", "Compare within blocks", (("in", "Pairs"),),
             (("out", "Scores"),), ("match.compare",)),
        Slot("cluster", "Cluster into entities", (("in", "Scores"),),
             (("out", "Clusters"),), ("match.cluster",),
             "Transitivity is a choice here, not a fact: A~B and B~C does not "
             "make A~C, and whether you close it is the decision."),
        Slot("assess", "Assess against the whole space",
             (("clusters", "Clusters"), ("forfeited", "Coverage")),
             (("out", "Findings"),), ("match.assess",)),
    ),
    edges=(Edge("load", "block"),
           Edge("block", "compare", from_port="pairs"),
           Edge("compare", "cluster"),
           Edge("cluster", "assess", to_port="clusters"),
           Edge("block", "assess", from_port="forfeited", to_port="forfeited")),
    anti_patterns=(
        "Reporting recall against the pairs that survived blocking. Blocking "
        "removed 89% of the comparisons; the pairs it made impossible to find "
        "are not in the denominator and the score is therefore not recall.",
        "Closing transitivity by default. One bad link merges two entities "
        "into one and the error is unrecoverable downstream — every later "
        "count is wrong and nothing says why.",
        "Tuning the threshold on the pairs you labelled, which are the pairs "
        "that were easy to label.",
    ),
)

DATA_MERGE = Template(
    id="data.merge",
    domain="data engineering",
    title="Combine sources that disagree",
    task="Join two sources and decide what to write where they conflict.",
    slots=_slots(
        Slot("left", "Read the first source", (), (("out", "Records"),),
             ("data.read",)),
        Slot("right", "Read the second source", (), (("out", "Records"),),
             ("data.read",)),
        Slot("align", "Align on a key",
             (("left", "Records"), ("right", "Records")),
             (("out", "Aligned"),), ("data.align",)),
        Slot("conflicts", "Find the disagreements", (("in", "Aligned"),),
             (("out", "Conflicts"),), ("data.conflicts",),
             "A separate step, so 'they disagreed about 1,204 rows' is a "
             "number somebody can be shown."),
        Slot("resolve", "Decide each one",
             (("aligned", "Aligned"), ("conflicts", "Conflicts")),
             (("out", "Records"), ("unresolved", "Conflicts")),
             ("data.resolve",),
             "Some conflicts have no right answer. Those come out of the "
             "second port rather than being resolved by whichever side the "
             "join happened to prefer."),
        Slot("report", "Report",
             (("records", "Records"), ("found", "Conflicts"),
              ("unresolved", "Conflicts")),
             (("out", "Findings"),), ("data.report",),
             "Three inputs, and the middle one is why. 'We wrote 9,412 rows' "
             "is not a report; 'we found 1,204 disagreements and could not "
             "settle 87 of them' is, and the two numbers come from different "
             "steps."),
    ),
    edges=(Edge("left", "align", to_port="left"),
           Edge("right", "align", to_port="right"),
           Edge("align", "conflicts"),
           Edge("align", "resolve", to_port="aligned"),
           Edge("conflicts", "resolve", to_port="conflicts"),
           Edge("resolve", "report", from_port="out", to_port="records"),
           Edge("conflicts", "report", to_port="found"),
           Edge("resolve", "report", from_port="unresolved",
                to_port="unresolved")),
    anti_patterns=(
        "Letting the join pick. `LEFT JOIN` with a `COALESCE` is a conflict "
        "resolution policy — 'prefer the left source, always, silently' — and "
        "nobody chose it or wrote it down.",
        "Resolving by recency without checking clocks. Last-write-wins across "
        "two systems whose clocks differ resolves in favour of the one that "
        "runs fast.",
        "Discarding the losing value. The disagreement is evidence about an "
        "upstream problem, and once merged it is gone.",
    ),
    notes=("The same shape answers 'why do these two reports differ' — the "
           "conflicts step is the diff, and attributing each difference to a "
           "step is what turns a discrepancy into a bug report.",),
)

# --- enrich: add what the record did not carry ------------------------------

ENRICH_REFERENCE = Template(
    id="enrich.reference",
    domain="data engineering",
    title="Join a reference table",
    task="Attach the attributes a code stands for, and account for what did not match.",
    slots=_slots(
        Slot("load", "Load records", (), (("out", "Records"),), ("data.read",)),
        Slot("reference", "Load the reference", (), (("out", "Reference"),),
             ("data.reference",),
             "With its vintage. A reference table has a date and joining "
             "against 'the current one' is a decision."),
        Slot("match", "Match",
             (("records", "Records"), ("reference", "Reference")),
             (("matched", "Records"), ("unmatched", "Records")),
             ("data.match",),
             "Both outputs exist because an inner join is a filter, and a "
             "filter that nobody counted is data loss."),
        Slot("fill", "Attach the attributes", (("in", "Records"),),
             (("out", "Records"),), ("data.enrich",)),
        Slot("account", "Account",
             (("matched", "Records"), ("unmatched", "Records")),
             (("out", "Findings"),), ("data.account",),
             "In plus out equals what arrived, or something is wrong."),
    ),
    edges=(Edge("load", "match", to_port="records"),
           Edge("reference", "match", to_port="reference"),
           Edge("match", "fill", from_port="matched"),
           Edge("fill", "account", to_port="matched"),
           Edge("match", "account", from_port="unmatched", to_port="unmatched")),
    anti_patterns=(
        "An inner join with no count comparison. The enrichment silently "
        "became a filter and the row count is 4% lower than last month for a "
        "reason nobody will find.",
        "Joining against the current reference for historical rows. Codes are "
        "reassigned; a 2019 row enriched with 2026 meanings is fiction.",
        "Treating an unmatched row as a null. 'We do not know' and 'it is "
        "empty' are different facts and only one of them is a data problem.",
    ),
)

ENRICH_GEO = Template(
    id="enrich.geo",
    domain="data engineering",
    title="Enrich from place",
    task="Turn written locations into checked, coded places — and disagree out loud.",
    slots=_slots(
        Slot("load", "Load records", (), (("out", "Records"),), ("data.read",)),
        Slot("parse", "Read the address text", (("in", "Records"),),
             (("out", "Addresses"),), ("geo.parse",),
             "Street, city, state, postal code, pulled out of whatever the "
             "user typed."),
        Slot("lookup", "Look the code up", (("in", "Records"),),
             (("out", "GeoCodes"),), ("geo.lookup",),
             "Independent of the text: a postal code resolves to a state, a "
             "county and a statistical area without reading the city line."),
        Slot("reconcile", "Make the two agree",
             (("parsed", "Addresses"), ("coded", "GeoCodes")),
             (("out", "Places"), ("conflicts", "Conflicts")),
             ("geo.reconcile",),
             "The join, and the point of the whole shape. The city the user "
             "typed and the state the ZIP belongs to are two readings of one "
             "location, and when they differ that is the finding."),
        Slot("validate", "Is it a real place?", (("in", "Places"),),
             (("out", "Findings"),), ("geo.validate",),
             "Against a reference of places that exist — a city/state/ZIP "
             "triple is valid or it is not, and 'it geocoded' is not the "
             "same question."),
        Slot("attach", "Attach",
             (("places", "Places"), ("findings", "Findings")),
             (("out", "Records"), ("dropped", "Records")), ("data.enrich",),
             "Two outputs, because an enrichment that filters is a filter. "
             "The rows it would not code come out of the second port instead "
             "of out of the count."),
    ),
    edges=(Edge("load", "parse"), Edge("load", "lookup"),
           Edge("parse", "reconcile", to_port="parsed"),
           Edge("lookup", "reconcile", to_port="coded"),
           Edge("reconcile", "validate", from_port="out"),
           Edge("reconcile", "attach", from_port="out", to_port="places"),
           Edge("validate", "attach", to_port="findings")),
    anti_patterns=(
        "Geocoding to a centroid and treating the result as an address. A "
        "postal code centroid is a point in a field; using it for anything "
        "distance-based produces a systematic error nobody can see on a map "
        "zoomed out.",
        "Trusting a geocoder's confidence score without the reconcile step. "
        "A ZIP that does not exist in the state it was written with resolves "
        "to *something* with a plausible latitude, and the row proceeds.",
        "Normalising the state to two letters and calling it validated. "
        "Format and existence are different checks; 'XZ' is well-formed.",
        "One place column. Where a thing is has a granularity — point, "
        "street, city, county, statistical area — and collapsing them means "
        "the join downstream is at whatever granularity is coarsest.",
    ),
    notes=("Written for US addresses because that is where the reference data "
           "is public and the failure modes are documented; the shape is not "
           "US-specific and the reference table is a candidate like any other.",),
)

ENRICH_TIME = Template(
    id="enrich.time",
    domain="data engineering",
    title="Enrich from time",
    task="Attach what else was true at that moment, using only what was knowable then.",
    slots=_slots(
        Slot("load", "Load records", (), (("out", "Records"),), ("data.read",)),
        Slot("asof", "Declare the as-of boundary", (("in", "Records"),),
             (("out", "Records"),), ("time.asof",),
             "Per row: the moment after which nothing may be used. This is "
             "the step everybody omits, and its absence is why the leak is "
             "invisible."),
        Slot("calendar", "Calendar features", (("in", "Records"),),
             (("out", "Features"),), ("time.calendar",),
             "Day of week, business day, holiday, fiscal period. Knowable in "
             "advance, so no boundary risk — which is exactly why these are "
             "safe to compute and rarely the reason a model works."),
        Slot("history", "Backward-looking features", (("in", "Records"),),
             (("out", "Features"),), ("time.history",),
             "Lags, rolling windows, time since the last event. Every one of "
             "these is a leak if its window crosses the as-of boundary."),
        Slot("assemble", "Assemble",
             (("calendar", "Features"), ("history", "Features")),
             (("out", "Features"),), ("feature.assemble",)),
        Slot("audit", "Audit for a leak",
             (("features", "Features"), ("records", "Records")),
             (("out", "Findings"),), ("time.audit",),
             "Mechanical: for each feature, the latest input timestamp it "
             "used against the row's as-of. A number, not a conviction."),
    ),
    edges=(Edge("load", "asof"), Edge("asof", "calendar"),
           Edge("asof", "history"),
           Edge("calendar", "assemble", to_port="calendar"),
           Edge("history", "assemble", to_port="history"),
           Edge("assemble", "audit", to_port="features"),
           Edge("asof", "audit", to_port="records")),
    anti_patterns=(
        "Computing a rolling mean over the whole series and joining it back "
        "by date. Every row now contains a summary of its own future, "
        "cross-validation cannot see it, and production performance is a "
        "fraction of the backtest.",
        "Using the row's *processing* timestamp as its as-of. Records arrive "
        "late; the moment you learned it and the moment it happened are "
        "different, and features must respect whichever one the deployed "
        "system will actually have.",
        "Time zones resolved at read time. A local midnight and a UTC "
        "midnight put a transaction on different days, and 'daily totals' "
        "then depends on where the reader is sitting.",
        "Holiday calendars without a country. A model trained on one "
        "market's holidays and scored on another's has a feature that is "
        "noise on half the rows.",
    ),
)

ENRICH_SPACETIME = Template(
    id="enrich.spacetime",
    domain="data engineering",
    title="Enrich from place and time together",
    task="Answer 'was anything happening there, then' without using anything from later.",
    slots=_slots(
        Slot("load", "Load records", (), (("out", "Records"),), ("data.read",)),
        Slot("place", "Resolve where", (("in", "Records"),),
             (("out", "Places"),), ("geo.resolve",)),
        Slot("period", "Resolve when", (("in", "Records"),),
             (("out", "Periods"),), ("time.resolve",)),
        Slot("context", "What was happening there then",
             (("places", "Places"), ("periods", "Periods")),
             (("out", "Context"),), ("context.lookup",),
             "The join that only exists because both halves are resolved. "
             "Weather at that station on that day; a public holiday in that "
             "state that week; an event in that city."),
        Slot("vintage", "Check the vintage", (("in", "Context"),),
             (("out", "Context"),), ("context.vintage",),
             "Was the reference data as of the row's date, or as of today? "
             "Boundaries move, stations close, holidays are legislated — a "
             "spatial join is a temporal statement too."),
        Slot("attach", "Attach",
             (("records", "Records"), ("context", "Context")),
             (("out", "Records"),), ("data.enrich",)),
        Slot("audit", "Audit", (("in", "Records"),), (("out", "Findings"),),
             ("context.audit",)),
    ),
    edges=(Edge("load", "place"), Edge("load", "period"),
           Edge("place", "context", to_port="places"),
           Edge("period", "context", to_port="periods"),
           Edge("context", "vintage"),
           Edge("load", "attach", to_port="records"),
           Edge("vintage", "attach", to_port="context"),
           Edge("attach", "audit")),
    anti_patterns=(
        "Joining a five-year-old row to today's boundaries. County lines, "
        "postal codes and statistical areas are redrawn; each half of the "
        "join is defensible and the pair is a quiet rewriting of history.",
        "Taking the nearest weather station without its distance. 'It rained "
        "there' from a station 80km away is a different claim from one 2km "
        "away, and the distance is the only thing that says which you have.",
        "Taking the day's *final* value. Yesterday's closing weather, "
        "revised traffic counts and finalised case numbers were not "
        "available at the moment the row happened.",
        "Treating a missing context as a zero. No event found and no data "
        "for that place-day are different, and only one of them means "
        "nothing was happening.",
    ),
    notes=("This is where the two enrichment shapes meet, and it is the one "
           "worth drawing: neither half can catch the other's error, because "
           "each is correct on its own axis.",),
)

# --- understand: find out what is in it -------------------------------------

ANALYSIS_EDA = Template(
    id="analysis.eda",
    domain="analysis",
    title="Find out what is in it",
    task="Explore a dataset and produce findings that survive being counted.",
    slots=_slots(
        Slot("load", "Load", (), (("out", "Frame"),), ("data.read",)),
        Slot("univariate", "One column at a time", (("in", "Frame"),),
             (("out", "Findings"),), ("analysis.univariate",)),
        Slot("bivariate", "Columns against each other", (("in", "Frame"),),
             (("out", "Findings"),), ("analysis.bivariate",)),
        Slot("collect", "Collect",
             (("univariate", "Findings"), ("bivariate", "Findings")),
             (("out", "Findings"),), ("analysis.collect",)),
        Slot("adjust", "Adjust for how much you looked", (("in", "Findings"),),
             (("out", "Findings"),), ("analysis.adjust",),
             "Carries the number of comparisons through to the end. Twenty "
             "columns is a hundred and ninety pairs, and five of them clear "
             "p<0.05 on noise."),
        Slot("narrate", "Write it down", (("in", "Findings"),),
             (("out", "Report"),), ("analysis.narrate",)),
    ),
    edges=(Edge("load", "univariate"), Edge("load", "bivariate"),
           Edge("univariate", "collect", to_port="univariate"),
           Edge("bivariate", "collect", to_port="bivariate"),
           Edge("collect", "adjust"), Edge("adjust", "narrate")),
    anti_patterns=(
        "Reporting the three findings that cleared significance without the "
        "count of tests that produced them. The count is the finding.",
        "Exploring on the test set. Every look is a decision, and decisions "
        "made after looking are fitted to the data you looked at.",
        "A conclusion drawn from a plot nobody kept. If the figure is not an "
        "artifact of the run, the finding cannot be checked later.",
        "Confusing 'no relationship found' with 'no relationship'. The power "
        "of the check is part of the result.",
    ),
)

DETECT_ANOMALY = Template(
    id="detect.anomaly",
    domain="analysis",
    title="Find the strange ones",
    task="Flag what does not belong, with a false-positive rate somebody measured.",
    slots=_slots(
        Slot("load", "Load", (), (("out", "Records"),), ("data.read",)),
        Slot("baseline", "Establish normal", (("in", "Records"),),
             (("out", "Baseline"),), ("detect.baseline",)),
        Slot("score", "Score the live data",
             (("records", "Records"), ("baseline", "Baseline")),
             (("out", "Scores"),), ("detect.score",)),
        Slot("control", "Score data known to be clean", (("in", "Baseline"),),
             (("out", "Scores"),), ("detect.control",),
             "The negative control, as a step in the graph. A detector that "
             "fires on data with nothing wrong in it has a false-positive "
             "rate, and without this branch nobody knows what it is."),
        Slot("adjudicate", "Decide",
             (("scores", "Scores"), ("control", "Scores")),
             (("out", "Findings"),), ("detect.adjudicate",),
             "Sees both, so 'we found 40 anomalies' arrives next to 'and 38 "
             "on data with nothing wrong in it'."),
    ),
    edges=(Edge("load", "baseline"), Edge("load", "score", to_port="records"),
           Edge("baseline", "score", to_port="baseline"),
           Edge("baseline", "control"),
           Edge("score", "adjudicate", to_port="scores"),
           Edge("control", "adjudicate", to_port="control")),
    anti_patterns=(
        "Shipping a detector without a control. 'It found something' reads "
        "as 'something was there', and a detector that flags 3% of "
        "everything forever will find something every single day.",
        "Tuning the threshold until the count looks reasonable. That fits "
        "the detector to your expectation of how much is wrong.",
        "Anomaly as a synonym for error. An outlier is a statement about the "
        "distribution; whether it is a problem is a separate judgement and "
        "belongs to whoever owns the data.",
        "Re-fitting the baseline on data that includes the anomalies, every "
        "run, until the strange thing is normal.",
    ),
)

LEARN_UNSUPERVISED = Template(
    id="learn.unsupervised",
    domain="machine learning",
    title="Find the groups nobody labelled",
    task="Cluster, then find out whether the clusters are there or are an artifact.",
    slots=_slots(
        Slot("load", "Load", (), (("out", "Frame"),), ("data.read",)),
        Slot("represent", "Represent", (("in", "Frame"),),
             (("out", "Matrix"),), ("feature.represent",),
             "Scaling decides the answer here more than the algorithm does: "
             "an unscaled column with a big range *is* the clustering."),
        Slot("cluster", "Cluster", (("in", "Matrix"),),
             (("out", "Clusters"),), ("cluster.fit",)),
        Slot("stability", "Do they survive a resample?",
             (("matrix", "Matrix"), ("clusters", "Clusters")),
             (("out", "Findings"),), ("cluster.stability",),
             "Re-cluster a resample and measure the agreement. Any algorithm "
             "returns k groups when asked for k; the question is whether the "
             "same rows stay together."),
        Slot("describe", "Describe them",
             (("clusters", "Clusters"), ("stability", "Findings")),
             (("out", "Report"),), ("cluster.describe",)),
    ),
    edges=(Edge("load", "represent"), Edge("represent", "cluster"),
           Edge("represent", "stability", to_port="matrix"),
           Edge("cluster", "stability", to_port="clusters"),
           Edge("cluster", "describe", to_port="clusters"),
           Edge("stability", "describe", to_port="stability")),
    anti_patterns=(
        "Choosing k by looking at which k gives the nicest story. That is "
        "fitting the hypothesis to the sample.",
        "Naming the clusters before the stability check, at which point "
        "everyone is attached to 'the price-sensitive segment' and the check "
        "cannot come back negative.",
        "Clustering unscaled data. The column measured in dollars decided it.",
        "Reporting silhouette as evidence the groups are real. It says the "
        "partition is tidy, which a partition of noise can also be.",
    ),
)

FLOW_USER_ACTIONS = Template(
    id="flow.user_actions",
    domain="product analytics",
    title="Model what users did",
    task="Turn an event log into a funnel and an attribution that count the same people.",
    slots=_slots(
        Slot("events", "Read the events", (), (("out", "Events"),),
             ("data.read",)),
        Slot("identify", "Decide who is who", (("in", "Events"),),
             (("out", "Events"),), ("flow.identify",),
             "Stitching anonymous activity to a person. Get this wrong and "
             "one user who came back twice is three users who dropped out."),
        Slot("sessionise", "Cut into sessions", (("in", "Events"),),
             (("out", "Sessions"),), ("flow.sessionise",),
             "The thirty-minute rule is a convention, not a fact, and it is "
             "a candidate here because it changes every number downstream."),
        Slot("funnel", "Where they dropped out", (("in", "Sessions"),),
             (("out", "Funnel"),), ("flow.funnel",)),
        Slot("attribute", "What got them there", (("in", "Sessions"),),
             (("out", "Attribution"),), ("flow.attribute",),
             "First touch, last touch, or spread. Three answers to one "
             "question, and none of them is the true one."),
        Slot("report", "Report",
             (("funnel", "Funnel"), ("attribution", "Attribution")),
             (("out", "Report"),), ("flow.report",),
             "Both readings meet, so the funnel and the attribution cannot "
             "quietly be computed over different populations."),
    ),
    edges=(Edge("events", "identify"), Edge("identify", "sessionise"),
           Edge("sessionise", "funnel"), Edge("sessionise", "attribute"),
           Edge("funnel", "report", to_port="funnel"),
           Edge("attribute", "report", to_port="attribution")),
    anti_patterns=(
        "A funnel over sessions reported as a funnel over people. A user who "
        "left and came back tomorrow is one conversion and two sessions, and "
        "the two numbers differ by however loyal your users are.",
        "Steps defined so that skipping one is impossible. A funnel where "
        "every user must pass through step 3 to reach step 4 measures the "
        "instrumentation, not the behaviour.",
        "Last-touch attribution presented without saying it is last-touch. "
        "Every channel's value depends entirely on that choice.",
        "Dropping events that failed to parse. The broken ones are usually a "
        "particular client version, which is to say a particular population.",
    ),
)

# --- predict: fit something and score it honestly ---------------------------

FEATURE_ENGINEERING = Template(
    id="feature.engineering",
    domain="machine learning",
    title="Build features without leaking",
    task="Derive features whose statistics came only from the training half.",
    slots=_slots(
        Slot("load", "Load", (), (("out", "Frame"),), ("data.read",)),
        Slot("split", "Split first", (("in", "Frame"),),
             (("train", "Frame"), ("valid", "Frame")), ("data.split",),
             "Upstream of everything. A split downstream of a fitted "
             "transformer is a split that has already been crossed."),
        Slot("fit", "Fit the transformer", (("in", "Frame"),),
             (("out", "Transformer"),), ("feature.fit",),
             "On the training half only. The transformer carries its own "
             "statistics, so applying it later cannot recompute them."),
        Slot("apply", "Apply it",
             (("transformer", "Transformer"), ("frame", "Frame")),
             (("out", "Matrix"),), ("feature.apply",)),
        Slot("select", "Select", (("in", "Matrix"),), (("out", "Matrix"),),
             ("feature.select",),
             "Selection is fitting. Choosing the top k features by "
             "correlation with the target, computed over everything, is the "
             "leak that hides inside a preprocessing step.", True),
        Slot("audit", "Audit for a leak",
             (("transformer", "Transformer"), ("frame", "Frame")),
             (("out", "Findings"),), ("feature.audit",),
             "Ask the transformer which rows its statistics came from, and "
             "check none of them are in the half it is being applied to."),
    ),
    edges=(Edge("load", "split"), Edge("split", "fit", from_port="train"),
           Edge("fit", "apply", to_port="transformer"),
           Edge("split", "apply", from_port="valid", to_port="frame"),
           Edge("apply", "select"),
           Edge("fit", "audit", to_port="transformer"),
           Edge("split", "audit", from_port="valid", to_port="frame")),
    anti_patterns=(
        "Scaling before splitting. The mean of the validation rows is now "
        "inside every training row, the effect is small, and it is the "
        "reason your held-out score is optimistic by a consistent amount.",
        "Target encoding without out-of-fold computation. The category's "
        "mean target includes the row's own target, which is the answer.",
        "Feature selection on the full dataset, then cross-validating the "
        "model. The selection already saw every fold.",
        "Counting features as progress. Each one is a place to leak and a "
        "thing to maintain.",
    ),
)

MODEL_BAKEOFF = Template(
    id="model.bakeoff",
    domain="machine learning",
    title="Choose the model family",
    task="Compare linear, tree, boosted, network and attention models on identical inputs.",
    slots=_slots(
        Slot("load", "Load", (), (("out", "Frame"),), ("data.read",)),
        Slot("split", "Split", (("in", "Frame"),),
             (("train", "Frame"), ("valid", "Frame")), ("data.split",)),
        Slot("prepare", "Prepare, identically for everyone",
             (("train", "Frame"), ("valid", "Frame")), (("out", "Fold"),),
             ("feature.prepare",),
             "One preparation, shared by every family. The moment each "
             "family gets its own preprocessing, the comparison measures the "
             "preprocessing."),
        Slot("fit", "Fit", (("in", "Fold"),), (("out", "Model"),),
             ("model.fit",),
             "The families live here, as candidates for one step: linear, "
             "tree, boosted trees, a small network, an attention model over "
             "the columns. That is the whole argument — the family is a "
             "choice inside the graph, not five graphs."),
        Slot("evaluate", "Evaluate",
             (("model", "Model"), ("fold", "Fold")), (("out", "Score"),),
             ("model.evaluate",)),
        Slot("compare", "Compare, with an interval", (("in", "Score"),),
             (("out", "Report"),), ("model.compare",),
             "A difference smaller than the spread across resamples is a "
             "tie, and reporting it as a winner is how a project spends six "
             "weeks on the wrong axis."),
    ),
    edges=(Edge("load", "split"),
           Edge("split", "prepare", from_port="train", to_port="train"),
           Edge("split", "prepare", from_port="valid", to_port="valid"),
           Edge("prepare", "fit"),
           Edge("fit", "evaluate", to_port="model"),
           Edge("prepare", "evaluate", to_port="fold"),
           Edge("evaluate", "compare")),
    anti_patterns=(
        "Comparing families under different preprocessing. One-hot for the "
        "linear model and raw categories for the trees measures the encoding.",
        "Tuning one family and taking another's defaults. That is a "
        "comparison of how much attention each got.",
        "Declaring a winner on a difference inside the noise. Resample and "
        "report the spread, or say the decision does not matter — which is a "
        "genuinely useful result and nobody publishes it.",
        "Reaching for the deepest model available because the problem feels "
        "hard. On a few thousand rows of tabular data, regularised linear "
        "and boosted trees are the honest baselines, and beating them is the "
        "claim that needs evidence.",
    ),
    notes=("The families deliberately span the space rather than sampling one "
           "corner of it: additive, partitioning, boosted-ensemble, "
           "densely-connected, and attention-over-columns fail differently, "
           "which is the only reason to run more than one.",),
)

DECIDE_POLICY = Template(
    id="decide.policy",
    domain="machine learning",
    title="Learn from feedback",
    task="Choose actions, observe what happened, and update — with an estimate that did not require deploying.",
    slots=_slots(
        Slot("context", "Observe the situation", (), (("out", "Contexts"),),
             ("rl.context",)),
        Slot("policy", "Choose an action", (("in", "Contexts"),),
             (("out", "Actions"),), ("rl.policy",),
             "Carries the probability it assigned to what it chose. Without "
             "that number, none of the off-policy arithmetic below is "
             "possible, and it is the field everyone forgets to log."),
        Slot("act", "Do it", (("in", "Actions"),), (("out", "Outcomes"),),
             ("rl.act",), "The step that reaches outside and cannot be undone."),
        Slot("reward", "Score what happened", (("in", "Outcomes"),),
             (("out", "Rewards"),), ("rl.reward",),
             "The reward definition is the specification of the system. Most "
             "reinforcement learning failures are here, not in the learner."),
        Slot("update", "Update the policy",
             (("rewards", "Rewards"), ("actions", "Actions")),
             (("out", "Policy"),), ("rl.update",)),
        Slot("offpolicy", "Estimate without deploying",
             (("rewards", "Rewards"), ("actions", "Actions")),
             (("out", "Score"),), ("rl.offpolicy",),
             "What a different policy would have scored on the log you "
             "already have, weighted by how likely the logged policy was to "
             "have taken that action. The branch that makes this a graph "
             "rather than a loop."),
    ),
    edges=(Edge("context", "policy"), Edge("policy", "act"),
           Edge("act", "reward"),
           Edge("reward", "update", to_port="rewards"),
           Edge("policy", "update", to_port="actions"),
           Edge("reward", "offpolicy", to_port="rewards"),
           Edge("policy", "offpolicy", to_port="actions")),
    anti_patterns=(
        "Evaluating a policy on the data its own choices produced. It looks "
        "better the more confidently it was wrong, because it never "
        "collected the counter-evidence.",
        "Not logging the action probability. Every off-policy estimator "
        "needs it and it cannot be reconstructed afterwards — this single "
        "omission is what forces teams into A/B tests they could have "
        "avoided.",
        "A reward that is easy to measure standing in for the one that "
        "matters. Clicks for satisfaction, session length for value; the "
        "policy will optimise exactly what you wrote down.",
        "Deploying an update without a floor. Exploration with no bound is "
        "an outage with a research justification.",
    ),
    notes=("A DAG, not a loop: one round is one pass, and learning across "
           "rounds is running the graph again with the updated policy as an "
           "input. That is the honest version — a loop drawn as a cycle "
           "hides where the data from round n-1 entered.",),
)

# --- generate: make data that did not exist ---------------------------------

SYNTH_TABULAR = Template(
    id="synth.tabular",
    domain="synthetic data",
    title="Make synthetic rows",
    task="Generate tabular data, and prove it is faithful, useful and not a copy.",
    slots=_slots(
        Slot("real", "Load the real data", (), (("out", "Frame"),),
             ("data.read",)),
        Slot("split", "Hold some real data back", (("in", "Frame"),),
             (("train", "Frame"), ("holdout", "Frame")), ("data.split",),
             "The generator sees the training half only, and the holdout is "
             "what utility is measured against. Without this the whole "
             "exercise is circular."),
        Slot("generate", "Generate", (("in", "Frame"),), (("out", "Frame"),),
             ("synth.generate",)),
        Slot("fidelity", "Does it look like the real thing?",
             (("synthetic", "Frame"), ("real", "Frame")),
             (("out", "Findings"),), ("synth.fidelity",),
             "Marginals *and* the relationships between columns. Matching "
             "every column's histogram while destroying every correlation is "
             "the classic result, and it passes any per-column check."),
        Slot("utility", "Is it useful?",
             (("synthetic", "Frame"), ("holdout", "Frame")),
             (("out", "Score"),), ("synth.utility",),
             "Train on synthetic, test on **real** held-out data. The other "
             "direction — testing on synthetic — measures whether the "
             "generator is self-consistent, which it always is."),
        Slot("privacy", "Did it just memorise?",
             (("synthetic", "Frame"), ("train", "Frame")),
             (("out", "Findings"),), ("synth.privacy",),
             "Distance from each synthetic row to its nearest real one. A "
             "generator that copies scores perfectly on fidelity and utility "
             "and is a data breach."),
        Slot("decide", "Decide",
             (("fidelity", "Findings"), ("utility", "Score"),
              ("privacy", "Findings")),
             (("out", "Verdict"),), ("synth.decide",),
             "Three inputs, and it needs all three. Any two of them can look "
             "excellent while the third condemns the dataset."),
    ),
    edges=(Edge("real", "split"),
           Edge("split", "generate", from_port="train"),
           Edge("generate", "fidelity", to_port="synthetic"),
           Edge("split", "fidelity", from_port="train", to_port="real"),
           Edge("generate", "utility", to_port="synthetic"),
           Edge("split", "utility", from_port="holdout", to_port="holdout"),
           Edge("generate", "privacy", to_port="synthetic"),
           Edge("split", "privacy", from_port="train", to_port="train"),
           Edge("fidelity", "decide", to_port="fidelity"),
           Edge("utility", "decide", to_port="utility"),
           Edge("privacy", "decide", to_port="privacy")),
    anti_patterns=(
        "Measuring fidelity and assuming utility. Per-column histograms can "
        "match perfectly while every joint relationship is destroyed, and a "
        "model trained on that data learns nothing that transfers.",
        "Testing on synthetic data. Train-on-synthetic-test-on-synthetic "
        "measures the generator's self-consistency and always looks good.",
        "No privacy arm. The best way to score well on fidelity is to copy, "
        "and a generator drifting toward memorisation improves on every "
        "metric you were watching.",
        "Generating from the whole dataset, then evaluating against part of "
        "it. The holdout was in the generator's training data.",
        "Reporting one number. Fidelity, utility and privacy trade off "
        "against each other; a single score has already made that trade on "
        "your behalf.",
    ),
)

SYNTH_CORPUS = Template(
    id="synth.corpus",
    domain="synthetic data",
    title="Make synthetic text for training",
    task="Generate training examples that add something a real-only run does not have.",
    slots=_slots(
        Slot("seed", "Real examples", (), (("out", "Corpus"),), ("data.read",)),
        Slot("generate", "Generate", (("in", "Corpus"),), (("out", "Corpus"),),
             ("synth.generate",)),
        Slot("dedupe", "Remove near-copies",
             (("generated", "Corpus"), ("seed", "Corpus")),
             (("out", "Corpus"), ("removed", "Findings")),
             ("synth.dedupe",),
             "Against the evaluation set especially. A paraphrase of a test "
             "item is a test item, and it will not look like contamination "
             "in any exact-match check."),
        Slot("filter", "Filter", (("in", "Corpus"),), (("out", "Corpus"),),
             ("synth.filter",),
             "Quality, safety, and whatever the generator does badly. This "
             "step is where most of the value is, and it is the one skipped."),
        Slot("mix", "Mix at a declared ratio",
             (("synthetic", "Corpus"), ("real", "Corpus")),
             (("out", "Corpus"),), ("synth.mix",),
             "The ratio is a parameter somebody chose, so it is in the graph "
             "where it can be varied and recorded."),
        Slot("evaluate", "Against a real-only control",
             (("mixed", "Corpus"), ("control", "Corpus")),
             (("out", "Score"),), ("synth.evaluate",),
             "The control arm is real data only. 'The model improved' after "
             "adding synthetic data and after adding *any* data are different "
             "claims."),
    ),
    edges=(Edge("seed", "generate"),
           Edge("generate", "dedupe", to_port="generated"),
           Edge("seed", "dedupe", to_port="seed"),
           Edge("dedupe", "filter", from_port="out"),
           Edge("filter", "mix", to_port="synthetic"),
           Edge("seed", "mix", to_port="real"),
           Edge("mix", "evaluate", to_port="mixed"),
           Edge("seed", "evaluate", to_port="control")),
    anti_patterns=(
        "Generating from the model you are about to train, then evaluating "
        "on a benchmark the generator has seen. The contamination arrives by "
        "paraphrase and no exact-match check finds it.",
        "No real-only control. Adding data helps; the question is whether "
        "adding *this* data helped more than adding an equal amount of "
        "anything else.",
        "Amplifying the generator's biases. Synthetic data inherits the "
        "distribution of whatever produced it, and training on it makes the "
        "narrow parts narrower.",
        "Mixing without recording the ratio. The result is unreproducible "
        "and the ratio is the most important hyperparameter in the run.",
    ),
)

SYNTH_ADVERSARIAL = Template(
    id="synth.adversarial",
    domain="synthetic data",
    title="Make cases designed to break it",
    task="Generate adversarial inputs, and report the coverage of what you tried.",
    slots=_slots(
        Slot("seeds", "Seed attacks", (), (("out", "Attacks"),),
             ("adv.seed",),
             "Grouped into families, because coverage is measured over "
             "families and not over the count of strings you generated."),
        Slot("mutate", "Mutate", (("in", "Attacks"),), (("out", "Attacks"),),
             ("adv.mutate",)),
        Slot("execute", "Run them", (("in", "Attacks"),),
             (("out", "Responses"),), ("adv.execute",)),
        Slot("detect", "Did anything get through?", (("in", "Responses"),),
             (("out", "Findings"),), ("adv.detect",)),
        Slot("coverage", "What did you not try?",
             (("attacks", "Attacks"), ("findings", "Findings")),
             (("out", "Report"),), ("adv.coverage",),
             "The output that matters. A pass rate without the families it "
             "was measured over is the coverage of somebody's imagination, "
             "reported as robustness."),
    ),
    edges=(Edge("seeds", "mutate"), Edge("mutate", "execute"),
           Edge("execute", "detect"),
           Edge("mutate", "coverage", to_port="attacks"),
           Edge("detect", "coverage", to_port="findings")),
    anti_patterns=(
        "Reporting a pass rate without the attack families. 100% against "
        "twelve variations of one idea is one data point, not a score.",
        "Tuning the system until the attack set passes. You have fixed the "
        "attack set, and the next one is generated by somebody else.",
        "Generating attacks with the same model being defended. It will "
        "avoid the ideas it does not have.",
        "Counting mutations as coverage. Ten thousand strings from one "
        "template is one template.",
    ),
)

DOCUMENT_ASSEMBLY = Template(
    id="document.assembly",
    domain="documents",
    title="Assemble a document out of records",
    task="Produce a document, then read it back and check it says what it should.",
    slots=_slots(
        Slot("records", "The data", (), (("out", "Records"),), ("data.read",)),
        Slot("plan", "Decide the structure", (("in", "Records"),),
             (("out", "Outline"),), ("doc.plan",)),
        Slot("render", "Render",
             (("outline", "Outline"), ("records", "Records")),
             (("out", "Document"),), ("doc.render",)),
        Slot("readback", "Read the output back", (("in", "Document"),),
             (("out", "Fields"),), ("doc.text",),
             "Extraction, pointed at what you just produced. The same "
             "machinery as `document.extraction`, run in the other "
             "direction — which is the only check that does not trust the "
             "renderer."),
        Slot("verify", "Round-trip",
             (("extracted", "Fields"), ("records", "Records")),
             (("out", "Verdict"),), ("doc.verify",),
             "Every value that went in comes back out, or the document is "
             "wrong. This catches the empty placeholder that renders "
             "perfectly."),
    ),
    edges=(Edge("records", "plan"),
           Edge("plan", "render", to_port="outline"),
           Edge("records", "render", to_port="records"),
           Edge("render", "readback"),
           Edge("readback", "verify", to_port="extracted"),
           Edge("records", "verify", to_port="records")),
    anti_patterns=(
        "Shipping a document nobody read back. The template rendered, a "
        "field was empty, and it went out as 'Dear ,'.",
        "Checking the model rather than the output. The record had the name; "
        "the question is whether the produced document contains it.",
        "Formatting numbers in the renderer. A total that is right in the "
        "data and wrong on the page is a rendering bug that reads as a data "
        "bug for two days.",
    ),
    notes=("The mirror of `document.extraction`, and deliberately reuses its "
           "vocabulary: `Fields` here means the same thing it does there, so "
           "an extractor written for reading invoices is a verifier for "
           "producing them.",),
)

# --- judge: decide whether it is good enough --------------------------------

EVAL_HARNESS = Template(
    id="eval.harness",
    domain="evaluation",
    title="Evaluate a system on a set of cases",
    task="Produce a score somebody can defend when asked how they know.",
    slots=_slots(
        Slot("cases", "The cases", (), (("out", "Cases"),), ("eval.cases",)),
        Slot("controls", "Add the controls", (("in", "Cases"),),
             (("out", "Cases"),), ("eval.controls",),
             "Known-good and known-bad variants, mixed in and labelled. A "
             "grader that cannot separate those measures nothing, and this "
             "is the cheapest check in the whole discipline."),
        Slot("run", "Run the system", (("in", "Cases"),),
             (("out", "Responses"),), ("eval.run",)),
        Slot("grade", "Grade", (("in", "Responses"),), (("out", "Grades"),),
             ("eval.grade",)),
        Slot("aggregate", "Aggregate",
             (("grades", "Grades"), ("cases", "Cases")),
             (("out", "Summary"),), ("eval.aggregate",),
             "By slice, and with an interval. One mean over everything hides "
             "the slice that regressed."),
        Slot("duecare", "Check the obligations",
             (("summary", "Summary"), ("grades", "Grades")),
             (("out", "Verdict"),), ("eval.duecare",),
             "Did this evaluation discharge what an evaluation owes — a "
             "holdout, controls that behaved, enough items to see the "
             "difference, provenance for every score? A verdict computed "
             "with obligations outstanding is provisional and says so."),
        Slot("report", "Report", (("in", "Verdict"),), (("out", "Report"),),
             ("eval.report",)),
    ),
    edges=(Edge("cases", "controls"), Edge("controls", "run"),
           Edge("run", "grade"),
           Edge("grade", "aggregate", to_port="grades"),
           Edge("controls", "aggregate", to_port="cases"),
           Edge("aggregate", "duecare", to_port="summary"),
           Edge("grade", "duecare", to_port="grades"),
           Edge("duecare", "report")),
    anti_patterns=(
        "The number went up. Against what, on which cases, graded by what, "
        "and is the difference bigger than re-running it twice?",
        "No known-bad control. If a deliberately broken system scores the "
        "same as the real one, the harness measures nothing and every "
        "previous result was noise.",
        "Averaging over slices. A 2% overall gain made of +8% on the common "
        "case and -30% on the rare one is a regression with good marketing.",
        "Grading the format. Most cheap graders reward answers shaped like "
        "the reference, which is why a system that learned the format scores "
        "well and helps nobody.",
        "Evaluating on cases that were used to build it. The set has to be "
        "held out, and 'we only looked at it a few times' is how it stops "
        "being held out.",
    ),
    notes=("The `duecare` slot is the unusual one and the reason this shape "
           "exists: the obligations of an evaluation are made into a node "
           "with inputs, so 'we checked' is a value in the graph rather than "
           "a claim in a review.",),
)

EVAL_JUDGE = Template(
    id="eval.judge",
    domain="evaluation",
    title="Have a model do the grading",
    task="Use a model as a judge, calibrated against labels a person produced.",
    slots=_slots(
        Slot("items", "The items to grade", (), (("out", "Items"),),
             ("eval.items",)),
        Slot("rubric", "Write the rubric", (("in", "Items"),),
             (("out", "Rubric"),), ("eval.rubric",),
             "Explicit criteria beat 'rate this 1-10', which produces a "
             "number correlated with length and confidence."),
        Slot("judge", "Judge",
             (("items", "Items"), ("rubric", "Rubric")),
             (("out", "Grades"),), ("eval.judge",)),
        Slot("human", "The labels you already have", (("in", "Items"),),
             (("out", "Grades"),), ("eval.human",),
             "Not the whole set — a sample is enough, and a judge with no "
             "human anchor at all is an opinion generator."),
        Slot("agreement", "Do they agree?",
             (("model", "Grades"), ("human", "Grades")),
             (("out", "Findings"),), ("eval.agreement",),
             "Against chance, and per class. A judge that always says "
             "'good' agrees with humans 80% of the time on data that is 80% "
             "good."),
        Slot("calibrate", "Calibrate",
             (("grades", "Grades"), ("agreement", "Findings")),
             (("out", "Grades"),), ("eval.calibrate",),
             "Correct the known bias rather than pretending it is absent."),
    ),
    edges=(Edge("items", "rubric"), Edge("items", "judge", to_port="items"),
           Edge("rubric", "judge", to_port="rubric"),
           Edge("items", "human"),
           Edge("judge", "agreement", to_port="model"),
           Edge("human", "agreement", to_port="human"),
           Edge("judge", "calibrate", to_port="grades"),
           Edge("agreement", "calibrate", to_port="agreement")),
    anti_patterns=(
        "Self-agreement reported as reliability. Running the judge twice and "
        "getting the same answer measures determinism, and a broken judge is "
        "usually a consistent one.",
        "No chance correction. Raw agreement on an imbalanced set is "
        "flattering to a judge that always guesses the majority.",
        "Position and length bias left unmeasured. Judges prefer the first "
        "option and the longer answer; both are measurable in an afternoon "
        "and neither usually is.",
        "The judge and the system being the same model. It will rate its own "
        "failure modes as fine, because they are the ones it cannot see.",
        "Grading with a rubric written after seeing the outputs.",
    ),
)

EVAL_REDTEAM = Template(
    id="eval.redteam",
    domain="evaluation",
    title="Try to make it misbehave",
    task="Attack a system on purpose, with a detector that was not built from the attacks.",
    slots=_slots(
        Slot("surface", "Declare the surface", (), (("out", "Surface"),),
             ("redteam.surface",),
             "What is in scope, what the system is not allowed to do, and "
             "who authorised this. A red team without a written scope is an "
             "incident."),
        Slot("attacks", "Generate attacks", (("in", "Surface"),),
             (("out", "Attacks"),), ("redteam.attacks",)),
        Slot("detector", "Build the detector", (("in", "Surface"),),
             (("out", "Detector"),), ("redteam.detector",),
             "From the policy, not from the attacks. A detector fitted to "
             "the attack set finds exactly the attack set — this branch is "
             "parallel for that reason and the parallelism is the design."),
        Slot("execute", "Run them", (("in", "Attacks"),),
             (("out", "Responses"),), ("redteam.execute",)),
        Slot("detect", "Judge the responses",
             (("responses", "Responses"), ("detector", "Detector")),
             (("out", "Findings"),), ("redteam.detect",)),
        Slot("adjudicate", "Report",
             (("findings", "Findings"), ("attacks", "Attacks")),
             (("out", "Report"),), ("redteam.adjudicate",),
             "Findings next to the coverage that produced them, so 'nothing "
             "got through' arrives with what was tried."),
    ),
    edges=(Edge("surface", "attacks"), Edge("surface", "detector"),
           Edge("attacks", "execute"),
           Edge("execute", "detect", to_port="responses"),
           Edge("detector", "detect", to_port="detector"),
           Edge("detect", "adjudicate", to_port="findings"),
           Edge("attacks", "adjudicate", to_port="attacks")),
    anti_patterns=(
        "The system under test judging whether it was broken. If the model "
        "decides whether its own output violated the policy, a successful "
        "attack is one it also fails to notice.",
        "'No successful attacks' with no coverage statement. That sentence "
        "is about the attacks, not about the system.",
        "Fixing the specific strings that worked. The finding is the "
        "*family*; patching the instance moves it one paraphrase away.",
        "Running without written authorisation and a scope. The difference "
        "between a red team and an attack is a document.",
    ),
)

# --- orchestrate: several actors doing work together ------------------------

AGENT_SUPERVISOR_WORKER = Template(
    id="agent.supervisor_worker",
    domain="language models",
    title="Supervisor, workers, critic",
    task="Split a job across several models, check the parts, and put them back together.",
    slots=_slots(
        Slot("brief", "The job", (), (("out", "Brief"),), ("agent.brief",)),
        Slot("plan", "Split it up", (("in", "Brief"),),
             (("out", "List[Task]"),), ("agent.plan",),
             "The supervisor's only real decision. A split that produces "
             "overlapping tasks produces agreeing answers that are one "
             "answer counted twice."),
        Slot("work", "Do each part", (("in", "List[Task]"),),
             (("out", "List[Result]"),), ("agent.work",),
             "A map: one worker per task, and they cannot see each other. "
             "That independence is what makes the critic's job possible.",
             False, "map"),
        Slot("critic", "Check the parts",
             (("results", "List[Result]"), ("tasks", "List[Task]")),
             (("out", "Findings"),), ("agent.critic",),
             "Its own node with its own inputs, because a supervisor that "
             "critiques its own plan finds the plan excellent."),
        Slot("synthesise", "Put it together",
             (("results", "List[Result]"), ("findings", "Findings")),
             (("out", "Answer"),), ("agent.synthesise",),
             "Sees the criticism, so a worker that returned an apology is "
             "not silently averaged in with four that returned work."),
        Slot("verify", "Does it answer the brief?",
             (("answer", "Answer"), ("brief", "Brief")),
             (("out", "Verdict"),), ("agent.verify",)),
    ),
    edges=(Edge("brief", "plan"), Edge("plan", "work"),
           Edge("work", "critic", to_port="results"),
           Edge("plan", "critic", to_port="tasks"),
           Edge("work", "synthesise", to_port="results"),
           Edge("critic", "synthesise", to_port="findings"),
           Edge("synthesise", "verify", to_port="answer"),
           Edge("brief", "verify", to_port="brief")),
    anti_patterns=(
        "Synthesising from unchecked worker output. One worker refused, one "
        "hallucinated a citation, and the summary reads as though five "
        "answers arrived — this is the failure mode of every multi-agent "
        "demo that looks good.",
        "The supervisor critiquing its own plan. It will find it excellent. "
        "The critic needs to be a separate node with the tasks *and* the "
        "results in front of it.",
        "Workers that can see each other's answers. They converge, and "
        "agreement between them stops being evidence of anything.",
        "No verification against the original brief. Five good answers to "
        "the wrong decomposition is a confident, well-cited miss.",
        "Counting agents as capability. Each one is a place to fail and a "
        "cost; two good workers beat six that need a critic to sort out.",
    ),
    notes=("The map step is what makes this different from a chain of "
           "prompts: workers are independent by construction, so a failure "
           "is one item rather than the run.",),
)

LLM_SKILL = Template(
    id="llm.skill",
    domain="language models",
    title="Give a model a tool",
    task="Let a model call a function, with validation it cannot skip and a check that it worked.",
    slots=_slots(
        Slot("request", "The request", (), (("out", "Request"),),
             ("agent.request",)),
        Slot("select", "Choose the tool", (("in", "Request"),),
             (("out", "Call"),), ("skill.select",),
             "Including the option of choosing none. A tool set with no "
             "'do nothing' option guarantees a call for every request."),
        Slot("validate", "Check the arguments", (("in", "Call"),),
             (("out", "Call"),), ("skill.validate",),
             "Against the schema, as a node in the graph. Validation the "
             "model performs on itself is validation the model can skip."),
        Slot("execute", "Call it", (("in", "Call"),), (("out", "Result"),),
             ("skill.execute",), "The step that reaches outside."),
        Slot("verify", "Did it do the thing?",
             (("result", "Result"), ("call", "Call")),
             (("out", "Verdict"),), ("skill.verify",),
             "Read the destination, not the return value. This is BG003 "
             "again, in the place it now matters most."),
        Slot("explain", "Say what happened",
             (("verdict", "Verdict"), ("result", "Result")),
             (("out", "Answer"),), ("skill.explain",)),
    ),
    edges=(Edge("request", "select"), Edge("select", "validate"),
           Edge("validate", "execute"),
           Edge("execute", "verify", to_port="result"),
           Edge("validate", "verify", to_port="call"),
           Edge("verify", "explain", to_port="verdict"),
           Edge("execute", "explain", to_port="result")),
    anti_patterns=(
        "Trusting the arguments because the model produced them. They are "
        "user input that passed through a language model, which makes them "
        "user input with better grammar.",
        "Reporting success because the call returned. The tool returned 200 "
        "and wrote nothing; something has to read the destination.",
        "A tool description that is a prompt. The model chooses from the "
        "description, so vagueness there is a wrong-tool bug that looks like "
        "a model failure.",
        "No way to decline. If the tool set cannot express 'this request "
        "needs none of these', every request gets a call.",
    ),
)

# --- operate: deliver it and notice when it stops being true ----------------

MONITOR_DRIFT = Template(
    id="monitor.drift",
    domain="operations",
    title="Notice when it changed",
    task="Compare now against then, and say nothing when nothing moved.",
    slots=_slots(
        Slot("baseline", "How it was", (), (("out", "Snapshot"),),
             ("monitor.baseline",)),
        Slot("current", "How it is", (), (("out", "Snapshot"),),
             ("monitor.observe",)),
        Slot("compare", "Compare",
             (("baseline", "Snapshot"), ("current", "Snapshot")),
             (("out", "Findings"),), ("monitor.compare",)),
        Slot("threshold", "Is it worth saying?", (("in", "Findings"),),
             (("out", "Findings"),), ("monitor.threshold",),
             "The step that earns the monitor its audience. An alert on "
             "every run is an alert nobody reads within a fortnight."),
        Slot("notify", "Tell somebody", (("in", "Findings"),),
             (("out", "Receipt"),), ("monitor.notify",),
             "Optional, and reaches outside.", True),
    ),
    edges=(Edge("baseline", "compare", to_port="baseline"),
           Edge("current", "compare", to_port="current"),
           Edge("compare", "threshold"), Edge("threshold", "notify")),
    anti_patterns=(
        "Alerting on every difference. Everything drifts a little; a monitor "
        "that says so every morning has trained its readers to close it.",
        "Comparing against last run rather than a baseline. Slow drift never "
        "trips a run-to-run comparison, which is the drift that matters.",
        "A threshold with no memory. Three consecutive small moves in one "
        "direction is a signal that no single-run threshold sees.",
        "Monitoring the input and calling it monitoring the output. The "
        "features can be stable while the thing you care about is not.",
    ),
)

FRONTEND_RENDER = Template(
    id="frontend.render",
    domain="user interface",
    title="Put it in front of a person",
    task="Get data into a view, and check the view says what actually happened.",
    slots=_slots(
        Slot("fetch", "Fetch", (), (("out", "Payload"),), ("net.fetch",)),
        Slot("classify", "Which state is this?", (("in", "Payload"),),
             (("out", "ViewState"),), ("view.classify",),
             "Loading, empty, error and data are four states and they are "
             "not interchangeable. Collapsing 'the request failed' into 'no "
             "results' is the most common lie a UI tells."),
        Slot("shape", "Shape it for the view", (("in", "ViewState"),),
             (("out", "ViewModel"),), ("view.shape",)),
        Slot("render", "Render", (("in", "ViewModel"),),
             (("out", "Rendered"),), ("view.render",)),
        Slot("measure", "Read what rendered",
             (("rendered", "Rendered"), ("state", "ViewState")),
             (("out", "Findings"),), ("view.measure",),
             "Against the state, not against the data. The check is that a "
             "user looking at this can tell which of the four happened."),
    ),
    edges=(Edge("fetch", "classify"), Edge("classify", "shape"),
           Edge("shape", "render"),
           Edge("render", "measure", to_port="rendered"),
           Edge("classify", "measure", to_port="state")),
    anti_patterns=(
        "An empty state that also serves as the error state. The user is "
        "told there is nothing when the truth is that nobody knows.",
        "Formatting in the component. A number that is right in the store "
        "and wrong on screen is a rendering bug investigated as a data bug.",
        "Testing the view model instead of the view. The check has to read "
        "the output, or it is testing the shaping step twice.",
        "Loading states that resolve to nothing on failure, so a flaky "
        "endpoint looks like a product with no content.",
    ),
)

IMAGE_PROCESSING = Template(
    id="image.processing",
    domain="media",
    title="Read and condition an image",
    task="Decide whether an image is usable, then derive what you need from it.",
    slots=_slots(
        Slot("read", "Read it", (), (("out", "Image"),), ("io.read",)),
        Slot("stats", "Measure the pixels", (("in", "Image"),),
             (("out", "Stats"),), ("image.stats",),
             "Colour spread, not file size. A blank PNG has plausible "
             "dimensions and a plausible byte count."),
        Slot("content", "Is anything in it?", (("in", "Image"),),
             (("out", "Findings"),), ("image.content",)),
        Slot("verdict", "Usable?",
             (("stats", "Stats"), ("content", "Findings")),
             (("out", "Verdict"),), ("image.verdict",)),
        Slot("derive", "Produce the outputs",
             (("image", "Image"), ("verdict", "Verdict")),
             (("out", "Artifacts"),), ("image.derive",)),
    ),
    edges=(Edge("read", "stats"), Edge("read", "content"),
           Edge("stats", "verdict", to_port="stats"),
           Edge("content", "verdict", to_port="content"),
           Edge("read", "derive", to_port="image"),
           Edge("verdict", "derive", to_port="verdict")),
    anti_patterns=(
        "Checking dimensions and file size. Both are fine on an all-white "
        "image, which is the failure that actually happens.",
        "Resizing before deciding whether the image was any good. Now the "
        "bad one has four derivatives and a thumbnail.",
        "One quality score. Blurry, blank, and wrong-subject are three "
        "different problems with three different remedies.",
    ),
)

STREAM_WINDOW = Template(
    id="stream.window",
    domain="data engineering",
    title="Turn an event stream into windows",
    task="Bucket events by time, and account for the ones that arrived late.",
    slots=_slots(
        Slot("receive", "Receive", (), (("out", "Events"),), ("net.receive",)),
        Slot("watermark", "Decide how long to wait", (("in", "Events"),),
             (("out", "Watermark"),), ("stream.watermark",),
             "Explicit, because it is the trade between latency and "
             "completeness and somebody has to make it on purpose."),
        Slot("window", "Bucket",
             (("events", "Events"), ("watermark", "Watermark")),
             (("windows", "Windows"), ("late", "Events")), ("stream.window",),
             "Late events come out of their own port rather than being "
             "dropped inside the bucketing."),
        Slot("aggregate", "Aggregate", (("in", "Windows"),),
             (("out", "Aggregates"),), ("stream.aggregate",)),
        Slot("account", "Account for the late ones",
             (("aggregates", "Aggregates"), ("late", "Events")),
             (("out", "Report"),), ("stream.account",)),
    ),
    edges=(Edge("receive", "watermark"),
           Edge("receive", "window", to_port="events"),
           Edge("watermark", "window", to_port="watermark"),
           Edge("window", "aggregate", from_port="windows"),
           Edge("aggregate", "account", to_port="aggregates"),
           Edge("window", "account", from_port="late", to_port="late")),
    anti_patterns=(
        "Dropping late events inside the bucketing step. Yesterday's total "
        "quietly differs from what was reported yesterday and nothing says "
        "so.",
        "Event time and processing time used interchangeably. They differ by "
        "exactly the amount your queue was backed up, which is exactly when "
        "the numbers matter.",
        "A watermark chosen once and never measured. The right wait is a "
        "property of the pipeline's actual lateness distribution.",
        "Windows that overlap without saying so. An event counted in two "
        "buckets doubles a total that everyone reads as a sum.",
    ),
)

CATALOG: tuple[Template, ...] = (
    TABULAR_SUPERVISED, DOCUMENT_EXTRACTION, SERVICE_NOTIFICATION,
    WEB_HARVEST, DATA_QUALITY, RELEASE, RETRIEVAL_QA, TIMESERIES_FORECAST,
    BATCH_FILES, APPROVAL, MIGRATE,
    # condition
    DATA_CLEAN, DATA_IMPUTE, ENTITY_RESOLUTION, DATA_MERGE,
    # enrich
    ENRICH_REFERENCE, ENRICH_GEO, ENRICH_TIME, ENRICH_SPACETIME,
    # understand
    ANALYSIS_EDA, DETECT_ANOMALY, LEARN_UNSUPERVISED, FLOW_USER_ACTIONS,
    # predict
    FEATURE_ENGINEERING, MODEL_BAKEOFF, DECIDE_POLICY,
    # generate
    SYNTH_TABULAR, SYNTH_CORPUS, SYNTH_ADVERSARIAL, DOCUMENT_ASSEMBLY,
    # judge
    EVAL_HARNESS, EVAL_JUDGE, EVAL_REDTEAM,
    # orchestrate
    AGENT_SUPERVISOR_WORKER, LLM_SKILL,
    # operate
    MONITOR_DRIFT, FRONTEND_RENDER, IMAGE_PROCESSING, STREAM_WINDOW,
)

BY_ID: dict[str, Template] = {t.id: t for t in CATALOG}


def get(template_id: str) -> Template:
    if template_id not in BY_ID:
        raise KeyError(f"unknown template {template_id!r}; "
                       f"known: {', '.join(sorted(BY_ID))}")
    return BY_ID[template_id]


def by_domain(domain: str) -> tuple[Template, ...]:
    return tuple(t for t in CATALOG if t.domain == domain)


def domains() -> tuple[str, ...]:
    return tuple(dict.fromkeys(t.domain for t in CATALOG))


def catalog_text() -> str:
    """The catalogue as prose, for pasting into a model's context.

    A harness that can see the shapes will pick one; a harness that has to guess
    invents a pipeline, and two harnesses that each invented one produce results
    that cannot be compared.
    """
    lines = ["Available problem templates:", ""]
    for t in CATALOG:
        lines.append(f"  {t.id}  [{t.domain}]")
        lines.append(f"    {t.task}")
        lines.append(f"    slots: {' -> '.join(t.slot_ids)}")
        shape = t.skeleton()
        if not shape.is_chain:
            lines.append(f"    shape: {len(shape.layers())} layers, "
                         f"joins at {[s.id for s in shape.leaf_stages if len(s.inputs) > 1]}")
        lines.append("")
    return "\n".join(lines)
