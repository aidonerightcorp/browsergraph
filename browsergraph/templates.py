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

CATALOG: tuple[Template, ...] = (
    TABULAR_SUPERVISED, DOCUMENT_EXTRACTION, SERVICE_NOTIFICATION,
    WEB_HARVEST, DATA_QUALITY, RELEASE, RETRIEVAL_QA, TIMESERIES_FORECAST,
    BATCH_FILES, APPROVAL, MIGRATE,
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
