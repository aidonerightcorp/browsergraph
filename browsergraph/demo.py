"""A domain-neutral registry, so the architecture can be seen without a domain.

Everything here is a description. Nothing executes, nothing is installed, and
the numbers attached to candidates are **illustrative priors** — they exist to
give the viewer something to sort by, and they are labelled as priors in the
data so nobody can mistake them for measurements. Real optimization consumes
real receipts; a demo that shipped invented benchmarks would be teaching exactly
the habit this project argues against.

The six stages are a demonstration, not a universal pipeline. A document
extraction task grows OCR, translation, chunking and reconciliation stages; a
machine-learning task grows splitting, imputation, calibration and stability
stages. The stages are data.

What is worth looking at is the arithmetic. Six stages with 76, 27, 13, 14, 11
and 8 candidates is 149 things to choose from — and 32,864,832 complete routes
through them. That number is why "the graph picks a route from evidence" is a
different proposition from "we support several options".
"""
from __future__ import annotations

from typing import Any

from browsergraph.manifest import NodeManifest, ParameterSpec, PortSpec
from browsergraph.workbench import (
    FeedbackDefinition,
    OptimizationObjective,
    OptimizationProfile,
    SolutionDefinition,
    StageDefinition,
    WorkbenchDefinition,
    candidate_id,
    expand_node_candidates,
)

#: Every metric in this file carries this. A number without a source is an
#: opinion wearing a lab coat.
PRIOR = {"source": "illustrative-prior", "evidence": 0}


def _node(node_id: str, kind: str, description: str, *, roles: tuple[str, ...],
          capability: str, takes: str, gives: str,
          params: tuple[ParameterSpec, ...] = (), permissions: tuple[str, ...] = (),
          effects: tuple[str, ...] = (), tags: tuple[str, ...] = (),
          deterministic: bool = True, quality: float = 0.8,
          latency_ms: int = 100, cost_usd: float = 0.0) -> NodeManifest:
    """One definition, with the boilerplate that every one of them shares."""
    return NodeManifest(
        id=node_id, kind=kind, description=description, roles=roles,
        capabilities=(capability,), tags=tags,
        inputs=(PortSpec("input", takes),),
        outputs=(PortSpec("output", gives),),
        parameters=params, permissions=permissions, effects=effects,
        runtime={"deterministic": deterministic, "sandbox": "process"},
        metrics={**PRIOR, "quality": quality, "latency_ms": latency_ms,
                 "cost_usd": cost_usd},
    ).assert_valid()


def _choice(name: str, *values: Any, description: str = "") -> ParameterSpec:
    return ParameterSpec(name=name, type="string", description=description,
                         default=values[0], choices=values)


# --- 1. acquire --------------------------------------------------------------
# The browser adapter is the point of the demonstration: one definition, three
# configuration dimensions, sixty concrete choices. Drawing it as one box would
# hide fifty-nine decisions a person is entitled to make.

ACQUIRE = (
    _node("demo.acquire.browser_adapter", "browser_adapter",
          "Drives a real browser to reach an authorized target.",
          roles=("source", "adapter"), capability="acquire",
          takes="TaskReference", gives="InputHandle",
          params=(_choice("controller", "BrowserPort", "Playwright", "Selenium",
                          "Puppeteer", "CDP"),
                  _choice("binary", "Chrome", "Chromium", "Edge", "Firefox",
                          "WebKit", "Brave"),
                  _choice("display", "headless", "headed")),
          permissions=("browser", "network"), deterministic=False,
          quality=0.88, latency_ms=2400, cost_usd=0.0),
    _node("demo.acquire.file_loader", "file_loader",
          "Loads and hashes an authorized local file.",
          roles=("source",), capability="acquire",
          takes="TaskReference", gives="InputHandle",
          params=(_choice("mode", "file", "directory", "glob"),),
          permissions=("filesystem:read",), quality=0.97, latency_ms=8),
    _node("demo.acquire.api_loader", "api_loader",
          "Loads a typed API response, with provenance.",
          roles=("source", "adapter"), capability="acquire",
          takes="TaskReference", gives="InputHandle",
          params=(_choice("method", "GET", "POST", "PUT"),),
          permissions=("network",), quality=0.9, latency_ms=320),
    _node("demo.acquire.database_loader", "database_loader",
          "Reads rows from an authorized database.",
          roles=("source",), capability="acquire",
          takes="TaskReference", gives="InputHandle",
          params=(_choice("dialect", "postgres", "mysql", "sqlite", "bigquery"),),
          permissions=("database:read",), quality=0.95, latency_ms=140),
    _node("demo.acquire.queue_kafka", "queue_consumer",
          "Consumes an authorized Kafka topic.", roles=("source",),
          capability="acquire", takes="TaskReference", gives="InputHandle",
          permissions=("network",), quality=0.86, latency_ms=60),
    _node("demo.acquire.queue_sqs", "queue_consumer",
          "Consumes an authorized SQS queue.", roles=("source",),
          capability="acquire", takes="TaskReference", gives="InputHandle",
          permissions=("network",), quality=0.85, latency_ms=90),
    _node("demo.acquire.stream_http", "stream_reader",
          "Reads a chunked HTTP stream.", roles=("source",),
          capability="acquire", takes="TaskReference", gives="InputHandle",
          permissions=("network",), quality=0.82, latency_ms=180),
    _node("demo.acquire.stream_websocket", "stream_reader",
          "Reads a websocket stream.", roles=("source",),
          capability="acquire", takes="TaskReference", gives="InputHandle",
          permissions=("network",), quality=0.8, latency_ms=200),
    _node("demo.acquire.archive_zip", "archive_loader",
          "Expands an authorized zip archive.", roles=("source",),
          capability="acquire", takes="TaskReference", gives="InputHandle",
          permissions=("filesystem:read",), quality=0.93, latency_ms=45),
    _node("demo.acquire.archive_tar", "archive_loader",
          "Expands an authorized tar archive.", roles=("source",),
          capability="acquire", takes="TaskReference", gives="InputHandle",
          permissions=("filesystem:read",), quality=0.93, latency_ms=40),
)

# --- 2. canonicalize ---------------------------------------------------------

CANONICALIZE = (
    _node("demo.canonicalize.text_normalizer", "text_normalizer",
          "Normalises unicode form and case.", roles=("transform",),
          capability="canonicalize", takes="InputHandle", gives="CanonicalState",
          params=(_choice("form", "NFC", "NFKC", "NFD"),
                  _choice("case", "preserve", "fold")),
          quality=0.9, latency_ms=6),
    _node("demo.canonicalize.schema_mapper", "schema_mapper",
          "Maps incoming fields onto a declared schema.", roles=("transform",),
          capability="canonicalize", takes="InputHandle", gives="CanonicalState",
          params=(_choice("strategy", "strict", "lenient", "inferred"),),
          quality=0.92, latency_ms=25),
    _node("demo.canonicalize.unit_metric", "unit_converter",
          "Converts measurements to metric units.", roles=("transform",),
          capability="canonicalize", takes="InputHandle", gives="CanonicalState",
          quality=0.96, latency_ms=4),
    _node("demo.canonicalize.unit_imperial", "unit_converter",
          "Converts measurements to imperial units.", roles=("transform",),
          capability="canonicalize", takes="InputHandle", gives="CanonicalState",
          quality=0.96, latency_ms=4),
    _node("demo.canonicalize.encoding_chardet", "encoding_detector",
          "Detects encoding statistically.", roles=("transform",),
          capability="canonicalize", takes="InputHandle", gives="CanonicalState",
          quality=0.88, latency_ms=12),
    _node("demo.canonicalize.encoding_bom", "encoding_detector",
          "Detects encoding from a byte-order mark.", roles=("transform",),
          capability="canonicalize", takes="InputHandle", gives="CanonicalState",
          quality=0.99, latency_ms=1),
    _node("demo.canonicalize.format_parser", "format_parser",
          "Parses a known container format.", roles=("transform",),
          capability="canonicalize", takes="InputHandle", gives="CanonicalState",
          params=(_choice("format", "json", "csv", "xml", "yaml"),),
          quality=0.94, latency_ms=18),
    _node("demo.canonicalize.identity_resolver", "identity_resolver",
          "Makes record identity explicit and stable.", roles=("transform",),
          capability="canonicalize", takes="InputHandle", gives="CanonicalState",
          params=(_choice("key", "natural", "surrogate", "composite"),),
          quality=0.87, latency_ms=30),
    _node("demo.canonicalize.table_canonicalizer", "table_canonicalizer",
          "Normalises tabular shape, headers and types.", roles=("transform",),
          capability="canonicalize", takes="InputHandle", gives="CanonicalState",
          params=(_choice("headers", "first-row", "declared", "inferred"),),
          quality=0.91, latency_ms=22),
    _node("demo.canonicalize.locale_normalizer", "locale_normalizer",
          "Normalises dates, numbers and separators for a locale.",
          roles=("transform",), capability="canonicalize",
          takes="InputHandle", gives="CanonicalState",
          params=(_choice("locale", "en-US", "en-GB", "de-DE"),),
          quality=0.9, latency_ms=9),
    _node("demo.canonicalize.passthrough", "passthrough",
          "Certifies the input already satisfies the canonical contract.",
          roles=("control",), capability="canonicalize",
          takes="InputHandle", gives="CanonicalState", tags=("pass-through",),
          quality=0.75, latency_ms=1),
)

# --- 3. enrich ---------------------------------------------------------------

ENRICH = (
    _node("demo.enrich.context_joiner", "context_joiner",
          "Joins authorized context onto the canonical state.",
          roles=("transform",), capability="enrich",
          takes="CanonicalState", gives="EnrichedState",
          params=(_choice("join", "inner", "left", "fuzzy"),),
          permissions=("database:read",), quality=0.89, latency_ms=70),
    _node("demo.enrich.reference_lookup", "reference_lookup",
          "Looks values up in an authoritative reference set.",
          roles=("transform",), capability="enrich",
          takes="CanonicalState", gives="EnrichedState",
          params=(_choice("source", "internal", "external"),),
          permissions=("network",), quality=0.91, latency_ms=210),
    _node("demo.enrich.embedding_enricher", "embedding_enricher",
          "Attaches vector representations for retrieval.",
          roles=("model", "transform"), capability="enrich",
          takes="CanonicalState", gives="EnrichedState",
          params=(_choice("model", "small", "base", "large"),),
          deterministic=False, quality=0.85, latency_ms=340, cost_usd=0.0004),
    _node("demo.enrich.provenance_stamper", "provenance_stamper",
          "Stamps origin, version and freshness onto every field.",
          roles=("transform",), capability="enrich",
          takes="CanonicalState", gives="EnrichedState",
          quality=0.99, latency_ms=3),
    _node("demo.enrich.freshness_ttl", "freshness_checker",
          "Rejects context older than a declared time to live.",
          roles=("control",), capability="enrich",
          takes="CanonicalState", gives="EnrichedState",
          quality=0.95, latency_ms=5),
    _node("demo.enrich.freshness_etag", "freshness_checker",
          "Revalidates context against an upstream entity tag.",
          roles=("control",), capability="enrich",
          takes="CanonicalState", gives="EnrichedState",
          permissions=("network",), quality=0.97, latency_ms=120),
    _node("demo.enrich.geocoder", "geocoder",
          "Resolves addresses to coordinates and reference regions.",
          roles=("transform",), capability="enrich",
          takes="CanonicalState", gives="EnrichedState",
          permissions=("network",), quality=0.86, latency_ms=260),
    _node("demo.enrich.passthrough", "passthrough",
          "Certifies the state already carries the required context.",
          roles=("control",), capability="enrich",
          takes="CanonicalState", gives="EnrichedState", tags=("pass-through",),
          quality=0.7, latency_ms=1),
)

# --- 4. transform ------------------------------------------------------------

TRANSFORM = (
    _node("demo.transform.deterministic_rules", "deterministic_rules",
          "Applies reviewed, deterministic rules.", roles=("transform",),
          capability="transform", takes="EnrichedState", gives="CandidateResult",
          params=(_choice("ruleset", "conservative", "aggressive"),),
          quality=0.84, latency_ms=15),
    _node("demo.transform.llm_parser", "llm_parser",
          "Asks a language model for a structured result.",
          roles=("model", "transform"), capability="transform",
          takes="EnrichedState", gives="CandidateResult",
          params=(_choice("model", "Gemini", "DeepSeek", "GLM"),
                  _choice("strategy", "single-schema", "field-by-field")),
          permissions=("llm",), deterministic=False,
          quality=0.9, latency_ms=1800, cost_usd=0.012),
    _node("demo.transform.statistical_model", "statistical_model",
          "Applies a fitted statistical model.", roles=("model",),
          capability="transform", takes="EnrichedState", gives="CandidateResult",
          params=(_choice("family", "linear", "tree"),),
          quality=0.87, latency_ms=45),
    _node("demo.transform.composite", "composite_transform",
          "Runs a validated child graph and exposes its external ports.",
          roles=("composite",), capability="transform",
          takes="EnrichedState", gives="CandidateResult",
          params=(_choice("children", "rules+llm", "rules+model"),),
          permissions=("llm",), deterministic=False,
          quality=0.93, latency_ms=2100, cost_usd=0.014),
    _node("demo.transform.human_operator", "human_operator",
          "Routes the decision to an authorized person.",
          roles=("action",), capability="transform",
          takes="EnrichedState", gives="CandidateResult",
          permissions=("human",), deterministic=False,
          quality=0.98, latency_ms=900000, cost_usd=2.5),
    _node("demo.transform.browser_action", "browser_action",
          "Performs a permitted external effect in a browser.",
          roles=("action",), capability="transform",
          takes="EnrichedState", gives="CandidateResult",
          permissions=("browser", "network"), effects=("external:remote-state",),
          deterministic=False, quality=0.8, latency_ms=3200),
)

# --- 5. verify ---------------------------------------------------------------
# Independence is the property that matters. A producer's own confidence is not
# verification, however well calibrated it claims to be.

VERIFY = (
    _node("demo.verify.schema_json", "schema_validator",
          "Checks the result against a JSON Schema.", roles=("verifier",),
          capability="verify", takes="CandidateResult", gives="VerifiedOutcome",
          quality=0.9, latency_ms=6),
    _node("demo.verify.schema_typed", "schema_validator",
          "Checks the result against typed models.", roles=("verifier",),
          capability="verify", takes="CandidateResult", gives="VerifiedOutcome",
          quality=0.92, latency_ms=9),
    _node("demo.verify.oracle", "oracle_check",
          "Compares against an independent source of truth.",
          roles=("verifier",), capability="verify",
          takes="CandidateResult", gives="VerifiedOutcome",
          params=(_choice("oracle", "reference-data", "recomputation"),),
          quality=0.95, latency_ms=180),
    _node("demo.verify.consensus", "consensus_vote",
          "Requires agreement between independent producers.",
          roles=("verifier",), capability="verify",
          takes="CandidateResult", gives="VerifiedOutcome",
          params=(_choice("rule", "unanimous", "majority", "weighted"),),
          deterministic=False, quality=0.97, latency_ms=2600, cost_usd=0.02),
    _node("demo.verify.human_review", "human_review",
          "An authorized person accepts or rejects the outcome.",
          roles=("verifier",), capability="verify",
          takes="CandidateResult", gives="VerifiedOutcome",
          permissions=("human",), quality=0.99, latency_ms=1200000, cost_usd=3.0),
    _node("demo.verify.visual_diff", "visual_diff",
          "Compares rendered output against an approved baseline.",
          roles=("verifier",), capability="verify",
          takes="CandidateResult", gives="VerifiedOutcome",
          quality=0.88, latency_ms=420),
    _node("demo.verify.ocr_check", "ocr_verifier",
          "Confirms the result is visible to a reader, not merely present.",
          roles=("verifier",), capability="verify",
          takes="CandidateResult", gives="VerifiedOutcome",
          params=(_choice("backend", "rapidocr", "tesseract"),),
          quality=0.86, latency_ms=650),
)

# --- 6. emit -----------------------------------------------------------------

EMIT = (
    _node("demo.emit.file_json", "file_writer",
          "Writes the result and receipt as JSON.", roles=("sink",),
          capability="emit", takes="VerifiedOutcome", gives="TaskReceipt",
          permissions=("filesystem:write",), effects=("local:file",),
          quality=0.99, latency_ms=7),
    _node("demo.emit.file_columnar", "file_writer",
          "Writes the result and receipt in a columnar format.",
          roles=("sink",), capability="emit",
          takes="VerifiedOutcome", gives="TaskReceipt",
          permissions=("filesystem:write",), effects=("local:file",),
          quality=0.98, latency_ms=26),
    _node("demo.emit.api_publisher", "api_publisher",
          "Publishes the result to an authorized endpoint.",
          roles=("sink", "action"), capability="emit",
          takes="VerifiedOutcome", gives="TaskReceipt",
          params=(_choice("delivery", "at-least-once", "exactly-once"),),
          permissions=("network",), effects=("external:published",),
          quality=0.93, latency_ms=290),
    _node("demo.emit.database_writer", "database_writer",
          "Writes the result to an authorized table.", roles=("sink",),
          capability="emit", takes="VerifiedOutcome", gives="TaskReceipt",
          params=(_choice("mode", "append", "upsert"),),
          permissions=("database:write",), effects=("external:row-state",),
          quality=0.96, latency_ms=110),
    _node("demo.emit.evidence_bundler", "evidence_bundler",
          "Bundles artifacts, provenance and replay data into a receipt.",
          roles=("sink",), capability="emit",
          takes="VerifiedOutcome", gives="TaskReceipt",
          permissions=("filesystem:write",), effects=("local:file",),
          quality=0.99, latency_ms=40),
    _node("demo.emit.notifier", "notifier",
          "Notifies authorized parties that the task completed.",
          roles=("sink", "action"), capability="emit",
          takes="VerifiedOutcome", gives="TaskReceipt",
          permissions=("network",), effects=("external:notification",),
          quality=0.9, latency_ms=150),
)

NODES = ACQUIRE + CANONICALIZE + ENRICH + TRANSFORM + VERIFY + EMIT

STAGE_SPECS = (
    ("acquire", "Acquire inputs", "TaskReference", "InputHandle", "acquire",
     "Resolve authorized task references into readable, versioned handles.",
     "the input is readable, identified and versioned", False,
     ("controller", "binary", "display", "transport")),
    ("canonicalize", "Canonicalize representation", "InputHandle", "CanonicalState",
     "canonicalize", "Make schema, units, encoding and identity explicit.",
     "downstream nodes receive a typed, versioned representation", True,
     ("format", "schema", "units", "locale", "precision")),
    ("enrich", "Enrich and derive context", "CanonicalState", "EnrichedState",
     "enrich", "Attach required context, evidence, provenance and freshness.",
     "every derived field carries a source and a freshness stamp", True,
     ("source", "recency", "depth")),
    ("transform", "Transform or act", "EnrichedState", "CandidateResult",
     "transform", "Produce the result, or perform a permitted external effect.",
     "a candidate result exists with its inputs recorded", False,
     ("method", "model", "strategy")),
    ("verify", "Verify success", "CandidateResult", "VerifiedOutcome",
     "verify", "Judge the outcome independently of whatever produced it.",
     "an independent check accepted the outcome", False,
     ("independence", "strictness", "sample")),
    ("emit", "Emit result and receipt", "VerifiedOutcome", "TaskReceipt",
     "emit", "Persist or return the result, evidence, provenance and replay data.",
     "the receipt is durable and the run is replayable", False,
     ("destination", "durability", "format")),
)

FEEDBACK = (
    FeedbackDefinition(
        id="feedback.contract", name="Contract compatibility",
        signal="ValidationReport", scope="edge",
        producer="validator", consumer="compiler",
        action="reject or repair the candidate or edge before execution",
        description="Ports, capabilities and bindings, checked before anything runs.",
        required=True),
    FeedbackDefinition(
        id="feedback.diagnosis", name="Execution diagnosis",
        signal="Diagnosis", scope="candidate",
        producer="runner", consumer="optimizer",
        action="retry, activate a fallback, or demote the candidate",
        description="Success, transient failure, permanent failure, timeout, "
                    "dependency failure, or invalid output — a classification, "
                    "not a log line."),
    FeedbackDefinition(
        id="feedback.verification", name="Independent verification",
        signal="Verdict", scope="route",
        producer="verifier", consumer="optimizer",
        action="accept, reject, escalate, retry, or activate a fallback",
        description="From an oracle independent of the producer. A technically "
                    "completed route can still be rejected here.",
        required=True),
    FeedbackDefinition(
        id="feedback.quality", name="Quality and yield",
        signal="Measurement", scope="context",
        producer="verifier", consumer="optimizer",
        action="update task-context performance evidence",
        description="Attributed to a task context, because a candidate can be "
                    "best for one document class and poor for another."),
    FeedbackDefinition(
        id="feedback.latency", name="Latency and resources",
        signal="Measurement", scope="candidate",
        producer="runner", consumer="optimizer",
        action="update runtime, resource and capacity estimates",
        description="Measured, not declared."),
    FeedbackDefinition(
        id="feedback.cost", name="Cost and tokens",
        signal="Measurement", scope="candidate",
        producer="runner", consumer="optimizer",
        action="update monetary, token and service usage",
        description="Cost is a first-class objective, not an afterthought."),
    FeedbackDefinition(
        id="feedback.policy", name="Policy, authority and effects",
        signal="PolicyEvent", scope="registry",
        producer="policy", consumer="compiler",
        action="block or constrain candidates beyond permission or budget",
        description="A high score cannot legalise a candidate that lacks a "
                    "required permission.",
        required=True),
    FeedbackDefinition(
        id="feedback.drift", name="Provenance, drift and freshness",
        signal="ProvenanceEvent", scope="version",
        producer="registry", consumer="optimizer",
        action="invalidate or discount evidence gathered before the change",
        description="When code, data, schema, model or environment moves, old "
                    "evidence describes something that no longer exists."),
)

PROFILES = (
    OptimizationProfile(
        id="profile.balanced", name="Balanced", strategy="weighted",
        objectives=(OptimizationObjective("quality", "maximize", 0.50),
                    OptimizationObjective("latency_ms", "minimize", 0.25),
                    OptimizationObjective("cost_usd", "minimize", 0.25)),
        minimum_evidence=5, exploration=0.10,
        description="The default when nothing about the task is unusual."),
    OptimizationProfile(
        id="profile.quality", name="Quality first", strategy="weighted",
        objectives=(OptimizationObjective("quality", "maximize", 0.80),
                    OptimizationObjective("cost_usd", "minimize", 0.10),
                    OptimizationObjective("latency_ms", "minimize", 0.10)),
        minimum_evidence=8, exploration=0.05,
        description="For work where being wrong costs more than being slow."),
    OptimizationProfile(
        id="profile.speed", name="Speed first", strategy="weighted",
        objectives=(OptimizationObjective("latency_ms", "minimize", 0.70),
                    OptimizationObjective("quality", "maximize", 0.30)),
        minimum_evidence=3, exploration=0.15,
        description="For interactive work with a human waiting."),
    OptimizationProfile(
        id="profile.cost", name="Cost first", strategy="weighted",
        objectives=(OptimizationObjective("cost_usd", "minimize", 0.70),
                    OptimizationObjective("quality", "maximize", 0.30)),
        minimum_evidence=3, exploration=0.20,
        description="For large batches where unit cost dominates."),
)


def _cid(node_id: str, **params: Any) -> str:
    """The candidate ID for one binding, so routes can name choices readably."""
    manifest = next(n for n in NODES if n.id == node_id)
    full = dict(params)
    full.update({p.name: p.default for p in manifest.parameters
                 if p.default is not None and p.name not in full})
    return candidate_id(node_id, full)


def workbench() -> WorkbenchDefinition:
    """The demonstration, assembled and validated."""
    candidates = expand_node_candidates(NODES)
    stages = tuple(
        StageDefinition(
            id=sid, name=name, input_type=takes, output_type=gives,
            required_capabilities=(capability,), description=description,
            success=success, optional=optional, variant_axes=axes,
        ).with_discovered_candidates(NODES, candidates)
        for sid, name, takes, gives, capability, description, success, optional, axes
        in STAGE_SPECS)

    cheapest = SolutionDefinition(
        id="cheapest", name="Cheapest route", status="baseline",
        description="What runs before there is any evidence: no browser, no "
                    "model, no external effect.",
        route={"acquire": _cid("demo.acquire.file_loader", mode="file"),
               "canonicalize": _cid("demo.canonicalize.encoding_bom"),
               "enrich": _cid("demo.enrich.provenance_stamper"),
               "transform": _cid("demo.transform.deterministic_rules",
                                 ruleset="conservative"),
               "verify": _cid("demo.verify.schema_json"),
               "emit": _cid("demo.emit.file_json")},
        metrics={"quality": 0.84, "latency_ms": 41, "cost_usd": 0.0},
        tags=("baseline", "deterministic"))

    accuracy = SolutionDefinition(
        id="accuracy_first", name="Accuracy-first route", status="candidate",
        description="Composite transformation with independent consensus, and a "
                    "human as the last fallback.",
        route={"acquire": _cid("demo.acquire.database_loader", dialect="postgres"),
               "canonicalize": _cid("demo.canonicalize.schema_mapper",
                                    strategy="strict"),
               "enrich": _cid("demo.enrich.reference_lookup", source="internal"),
               "transform": _cid("demo.transform.composite", children="rules+llm"),
               "verify": _cid("demo.verify.consensus", rule="majority"),
               "emit": _cid("demo.emit.evidence_bundler")},
        fallbacks={"verify": (_cid("demo.verify.oracle", oracle="reference-data"),
                              _cid("demo.verify.human_review"))},
        metrics={"quality": 0.97, "latency_ms": 1420, "cost_usd": 0.031},
        tags=("quality", "independent-verification"))

    learned = SolutionDefinition(
        id="learned", name="Learned route", status="learned",
        description="Where evidence moved the choice: a defended source needed a "
                    "real browser, and OCR caught a result that was present in "
                    "the data and invisible on the page.",
        route={"acquire": _cid("demo.acquire.browser_adapter",
                               controller="Playwright", binary="Firefox",
                               display="headless"),
               "canonicalize": _cid("demo.canonicalize.text_normalizer",
                                    form="NFKC", case="fold"),
               "enrich": _cid("demo.enrich.provenance_stamper"),
               "transform": _cid("demo.transform.llm_parser", model="GLM",
                                 strategy="field-by-field"),
               "verify": _cid("demo.verify.ocr_check", backend="rapidocr"),
               "emit": _cid("demo.emit.evidence_bundler")},
        fallbacks={"acquire": (_cid("demo.acquire.browser_adapter",
                                    controller="Selenium", binary="Chrome",
                                    display="headless"),),
                   "transform": (_cid("demo.transform.deterministic_rules",
                                      ruleset="aggressive"),)},
        metrics={"quality": 0.94, "latency_ms": 4300, "cost_usd": 0.018},
        tags=("learned", "browser", "ocr"))

    fastest = SolutionDefinition(
        id="fastest", name="Fastest route", status="candidate",
        description="Every stage taking its cheapest admitted candidate.",
        route={"acquire": _cid("demo.acquire.file_loader", mode="file"),
               "canonicalize": _cid("demo.canonicalize.passthrough"),
               "enrich": _cid("demo.enrich.passthrough"),
               "transform": _cid("demo.transform.deterministic_rules",
                                 ruleset="aggressive"),
               "verify": _cid("demo.verify.schema_json"),
               "emit": _cid("demo.emit.file_json")},
        metrics={"quality": 0.7, "latency_ms": 30, "cost_usd": 0.0},
        tags=("speed", "pass-through"))

    careful = SolutionDefinition(
        id="human_in_the_loop", name="Human-in-the-loop route", status="candidate",
        description="For effects that are hard to undo: a person decides, and a "
                    "person checks.",
        route={"acquire": _cid("demo.acquire.api_loader", method="GET"),
               "canonicalize": _cid("demo.canonicalize.format_parser", format="json"),
               "enrich": _cid("demo.enrich.freshness_etag"),
               "transform": _cid("demo.transform.human_operator"),
               "verify": _cid("demo.verify.human_review"),
               "emit": _cid("demo.emit.notifier")},
        metrics={"quality": 0.99, "latency_ms": 2100000, "cost_usd": 5.5},
        tags=("authority", "irreversible"))

    return WorkbenchDefinition(
        title="Universal graph solution studio",
        task="Turn an authorized reference into a verified, receipted result.",
        success="an independent verifier accepted the outcome and a replayable "
                "receipt exists",
        nodes=NODES,
        candidates=candidates,
        stages=stages,
        solutions=(cheapest, accuracy, learned, fastest, careful),
        feedback_channels=FEEDBACK,
        optimization_profiles=PROFILES,
        metadata={"generator": "browsergraph.demo",
                  "metrics_note": "Every metric here is an illustrative prior, "
                                  "not a measurement. Real optimization consumes "
                                  "real receipts."},
    ).assert_valid()
