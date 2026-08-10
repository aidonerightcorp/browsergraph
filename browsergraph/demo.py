"""A domain-neutral registry, so the architecture can be seen without a domain.

Everything here is a description. Nothing executes, nothing is installed, and
the numbers attached to candidates are **illustrative priors** — they exist to
give the search something to sort by, and they are labelled as priors in the
data so nobody can mistake them for measurements. Real optimization consumes
real receipts; a demo that shipped invented benchmarks would be teaching exactly
the habit this project argues against.

The six stages are a demonstration, not a universal pipeline. The stages are
data; supply different ones.

**Why the stages have sub-steps.** The first version of this registry drew six
stages, and "Acquire inputs" held seventy-odd candidates. That reads as one
decision and is three: work out *what* to fetch, open a session capable of
fetching it, then read a payload out of that session. Drawing it flat is the
same mistake as drawing a parameter family as one candidate — it hides choices
that are genuinely available, and it hides them from the search as well as from
the reader. Pooling every candidate in a stage into one decision counts about a
hundred million routes; the sub-steps those stages are actually made of expose
trillions. Same task, same registry, same code.

A stage is either a leaf that holds candidates or a composite that holds
sub-steps, never both — otherwise "one choice per stage" stops being well
defined, and that sentence is what the whole model rests on.
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
    """One definition, with the boilerplate every one of them shares."""
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


# === 1. ACQUIRE INPUTS: resolve -> open a session -> read a payload ==========

RESOLVE = (
    _node("demo.resolve.literal", "resolve_literal",
          "Treats the reference as an already-authorized literal locator.",
          roles=("control",), capability="resolve",
          takes="TaskReference", gives="ResolvedTarget",
          quality=0.99, latency_ms=1),
    _node("demo.resolve.template", "resolve_template",
          "Fills a declared template from task parameters.",
          roles=("transform",), capability="resolve",
          takes="TaskReference", gives="ResolvedTarget",
          params=(_choice("strictness", "strict", "lenient"),),
          quality=0.95, latency_ms=3),
    _node("demo.resolve.directory", "resolve_directory",
          "Resolves an identifier through an authorized directory.",
          roles=("source",), capability="resolve",
          takes="TaskReference", gives="ResolvedTarget",
          permissions=("database:read",), quality=0.93, latency_ms=40),
)

SESSION = (
    _node("demo.session.browser", "browser_session",
          "Opens a real browser session with a chosen controller and binary.",
          roles=("adapter",), capability="session",
          takes="ResolvedTarget", gives="OpenSession",
          params=(_choice("controller", "BrowserPort", "Playwright", "Selenium",
                          "Puppeteer", "CDP"),
                  _choice("binary", "Chrome", "Chromium", "Edge", "Firefox",
                          "WebKit", "Brave"),
                  _choice("display", "headless", "headed")),
          permissions=("browser", "network"), deterministic=False,
          quality=0.88, latency_ms=2200),
    _node("demo.session.filesystem", "file_session",
          "Opens an authorized filesystem handle.", roles=("adapter",),
          capability="session", takes="ResolvedTarget", gives="OpenSession",
          params=(_choice("mode", "file", "directory", "glob"),),
          permissions=("filesystem:read",), quality=0.98, latency_ms=4),
    _node("demo.session.http", "http_session",
          "Opens an HTTP session with a declared retry policy.",
          roles=("adapter",), capability="session",
          takes="ResolvedTarget", gives="OpenSession",
          params=(_choice("retries", "none", "bounded"),),
          permissions=("network",), quality=0.90, latency_ms=120),
    _node("demo.session.database", "database_session",
          "Opens an authorized database connection.", roles=("adapter",),
          capability="session", takes="ResolvedTarget", gives="OpenSession",
          params=(_choice("dialect", "postgres", "mysql", "sqlite", "bigquery"),),
          permissions=("database:read",), quality=0.96, latency_ms=90),
    _node("demo.session.queue", "queue_session",
          "Subscribes to an authorized queue or topic.", roles=("adapter",),
          capability="session", takes="ResolvedTarget", gives="OpenSession",
          permissions=("network",), quality=0.86, latency_ms=60),
)

READ = (
    _node("demo.read.whole", "read_whole",
          "Reads the entire payload and hashes it.", roles=("source",),
          capability="read", takes="OpenSession", gives="InputHandle",
          quality=0.97, latency_ms=20),
    _node("demo.read.streamed", "read_streamed",
          "Reads incrementally, bounding peak memory.", roles=("source",),
          capability="read", takes="OpenSession", gives="InputHandle",
          params=(_choice("chunking", "fixed", "adaptive"),),
          quality=0.94, latency_ms=35),
    _node("demo.read.rendered", "read_rendered",
          "Reads what the page rendered, after scripts have run.",
          roles=("source",), capability="read",
          takes="OpenSession", gives="InputHandle",
          params=(_choice("settle", "load", "network-idle", "mutation-quiet"),),
          permissions=("browser",), deterministic=False,
          quality=0.90, latency_ms=900),
)

# === 2. CANONICALIZE: decode -> parse -> normalize ===========================

DECODE = (
    _node("demo.decode.bom", "decode_bom",
          "Takes the encoding from a byte-order mark.", roles=("transform",),
          capability="decode", takes="InputHandle", gives="DecodedText",
          quality=0.99, latency_ms=1),
    _node("demo.decode.statistical", "decode_statistical",
          "Detects the encoding statistically.", roles=("transform",),
          capability="decode", takes="InputHandle", gives="DecodedText",
          quality=0.88, latency_ms=12),
    _node("demo.decode.unicode", "decode_unicode",
          "Applies a declared unicode normal form.", roles=("transform",),
          capability="decode", takes="InputHandle", gives="DecodedText",
          params=(_choice("form", "NFC", "NFKC", "NFD"),),
          quality=0.95, latency_ms=6),
    _node("demo.decode.passthrough", "decode_passthrough",
          "Certifies the payload is already decoded text.",
          roles=("control",), capability="decode",
          takes="InputHandle", gives="DecodedText", tags=("pass-through",),
          quality=0.75, latency_ms=1),
)

PARSE = (
    _node("demo.parse.format", "parse_format",
          "Parses a known container format.", roles=("transform",),
          capability="parse", takes="DecodedText", gives="ParsedRecords",
          params=(_choice("format", "json", "csv", "xml", "yaml"),),
          quality=0.94, latency_ms=18),
    _node("demo.parse.table", "parse_table",
          "Reads tabular shape and headers.", roles=("transform",),
          capability="parse", takes="DecodedText", gives="ParsedRecords",
          params=(_choice("headers", "first-row", "declared", "inferred"),),
          quality=0.91, latency_ms=22),
    _node("demo.parse.tolerant", "parse_tolerant",
          "Recovers records from malformed input, recording every repair.",
          roles=("transform",), capability="parse",
          takes="DecodedText", gives="ParsedRecords",
          quality=0.82, latency_ms=30),
    _node("demo.parse.model", "parse_model",
          "Asks a language model for structure when no grammar fits.",
          roles=("model",), capability="parse",
          takes="DecodedText", gives="ParsedRecords",
          params=(_choice("model", "Gemini", "DeepSeek", "GLM"),),
          permissions=("llm",), deterministic=False,
          quality=0.89, latency_ms=1500, cost_usd=0.008),
)

NORMALIZE = (
    _node("demo.normalize.units", "normalize_units",
          "Converts measurements to a declared unit system.",
          roles=("transform",), capability="normalize",
          takes="ParsedRecords", gives="CanonicalState",
          params=(_choice("system", "metric", "imperial"),),
          quality=0.96, latency_ms=4),
    _node("demo.normalize.locale", "normalize_locale",
          "Normalizes dates, numbers and separators for a locale.",
          roles=("transform",), capability="normalize",
          takes="ParsedRecords", gives="CanonicalState",
          params=(_choice("locale", "en-US", "en-GB", "de-DE"),),
          quality=0.90, latency_ms=9),
    _node("demo.normalize.identity", "normalize_identity",
          "Makes record identity explicit and stable.", roles=("transform",),
          capability="normalize", takes="ParsedRecords", gives="CanonicalState",
          params=(_choice("key", "natural", "surrogate", "composite"),),
          quality=0.87, latency_ms=30),
    _node("demo.normalize.schema", "normalize_schema",
          "Maps incoming fields onto a declared schema.", roles=("transform",),
          capability="normalize", takes="ParsedRecords", gives="CanonicalState",
          params=(_choice("strategy", "strict", "lenient", "inferred"),),
          quality=0.92, latency_ms=25),
    _node("demo.normalize.passthrough", "normalize_passthrough",
          "Certifies the records already satisfy the canonical contract.",
          roles=("control",), capability="normalize",
          takes="ParsedRecords", gives="CanonicalState", tags=("pass-through",),
          quality=0.74, latency_ms=1),
)

# === 3. ENRICH: plan what context is needed -> attach it =====================

PLAN = (
    _node("demo.plan.none", "plan_none",
          "Declares that no additional context is required.",
          roles=("control",), capability="plan",
          takes="CanonicalState", gives="ContextPlan", tags=("pass-through",),
          quality=0.72, latency_ms=1),
    _node("demo.plan.rules", "plan_rules",
          "Decides what to fetch from reviewed rules.", roles=("control",),
          capability="plan", takes="CanonicalState", gives="ContextPlan",
          quality=0.93, latency_ms=6),
    _node("demo.plan.retrieval", "plan_retrieval",
          "Ranks what context would help, by retrieval score.",
          roles=("transform",), capability="plan",
          takes="CanonicalState", gives="ContextPlan",
          params=(_choice("ranking", "bm25", "embedding", "hybrid"),),
          quality=0.89, latency_ms=120),
)

ATTACH = (
    _node("demo.attach.reference", "attach_reference",
          "Joins an authoritative reference set.", roles=("transform",),
          capability="attach", takes="ContextPlan", gives="EnrichedState",
          params=(_choice("source", "internal", "external"),),
          permissions=("network",), quality=0.91, latency_ms=210),
    _node("demo.attach.embedding", "attach_embedding",
          "Attaches vector representations for retrieval.",
          roles=("model",), capability="attach",
          takes="ContextPlan", gives="EnrichedState",
          params=(_choice("model", "small", "base", "large"),),
          deterministic=False, quality=0.85, latency_ms=340, cost_usd=0.0004),
    _node("demo.attach.provenance", "attach_provenance",
          "Stamps origin, version and freshness onto every field.",
          roles=("transform",), capability="attach",
          takes="ContextPlan", gives="EnrichedState",
          quality=0.99, latency_ms=3),
    _node("demo.attach.freshness", "attach_freshness",
          "Revalidates context against an upstream entity tag or time to live.",
          roles=("control",), capability="attach",
          takes="ContextPlan", gives="EnrichedState",
          params=(_choice("policy", "ttl", "etag"),),
          quality=0.96, latency_ms=90),
    _node("demo.attach.geocode", "attach_geocode",
          "Resolves addresses to coordinates and reference regions.",
          roles=("transform",), capability="attach",
          takes="ContextPlan", gives="EnrichedState",
          permissions=("network",), quality=0.86, latency_ms=260),
)

# === 4. TRANSFORM OR ACT: locate the target -> apply or act ==================

LOCATE = (
    _node("demo.locate.selector", "locate_selector",
          "Locates by an explicit, reviewed selector or key.",
          roles=("transform",), capability="locate",
          takes="EnrichedState", gives="LocatedTarget",
          quality=0.93, latency_ms=5),
    _node("demo.locate.heuristic", "locate_heuristic",
          "Locates by structural heuristics when the selector breaks.",
          roles=("transform",), capability="locate",
          takes="EnrichedState", gives="LocatedTarget",
          params=(_choice("strategy", "nearest-label", "role", "text"),),
          quality=0.84, latency_ms=40),
    _node("demo.locate.vision", "locate_vision",
          "Locates from a rendering, when the structure carries no meaning.",
          roles=("model",), capability="locate",
          takes="EnrichedState", gives="LocatedTarget",
          permissions=("llm",), deterministic=False,
          quality=0.80, latency_ms=2100, cost_usd=0.011),
    _node("demo.locate.recorded", "locate_recorded",
          "Replays a location that a previous verified run recorded.",
          roles=("control",), capability="locate",
          takes="EnrichedState", gives="LocatedTarget",
          quality=0.95, latency_ms=2),
)

ACT = (
    _node("demo.act.rules", "act_rules",
          "Applies reviewed, deterministic rules.", roles=("transform",),
          capability="act", takes="LocatedTarget", gives="CandidateResult",
          params=(_choice("ruleset", "conservative", "aggressive"),),
          quality=0.84, latency_ms=15),
    _node("demo.act.model", "act_model",
          "Asks a language model for a structured result.",
          roles=("model",), capability="act",
          takes="LocatedTarget", gives="CandidateResult",
          params=(_choice("model", "Gemini", "DeepSeek", "GLM"),
                  _choice("strategy", "single-schema", "field-by-field")),
          permissions=("llm",), deterministic=False,
          quality=0.90, latency_ms=1800, cost_usd=0.012),
    _node("demo.act.statistical", "act_statistical",
          "Applies a fitted statistical model.", roles=("model",),
          capability="act", takes="LocatedTarget", gives="CandidateResult",
          params=(_choice("family", "linear", "tree"),),
          quality=0.87, latency_ms=45),
    _node("demo.act.composite", "act_composite",
          "Runs a validated child graph and exposes its external ports.",
          roles=("composite",), capability="act",
          takes="LocatedTarget", gives="CandidateResult",
          params=(_choice("children", "rules+model", "rules+statistical"),),
          permissions=("llm",), deterministic=False,
          quality=0.93, latency_ms=2100, cost_usd=0.014),
    _node("demo.act.browser", "act_browser",
          "Performs a permitted external effect in a browser.",
          roles=("action",), capability="act",
          takes="LocatedTarget", gives="CandidateResult",
          permissions=("browser", "network"), effects=("external:remote-state",),
          deterministic=False, quality=0.80, latency_ms=3200),
    _node("demo.act.human", "act_human",
          "Routes the decision to an authorized person.", roles=("action",),
          capability="act", takes="LocatedTarget", gives="CandidateResult",
          permissions=("human",), deterministic=False,
          quality=0.98, latency_ms=900000, cost_usd=2.5),
)

# === 5. VERIFY: check the shape -> check it independently ====================

SHAPE = (
    _node("demo.shape.json_schema", "shape_json_schema",
          "Checks the result against a JSON Schema.", roles=("verifier",),
          capability="shape", takes="CandidateResult", gives="ShapeVerdict",
          quality=0.90, latency_ms=6),
    _node("demo.shape.typed", "shape_typed",
          "Checks the result against typed models.", roles=("verifier",),
          capability="shape", takes="CandidateResult", gives="ShapeVerdict",
          quality=0.92, latency_ms=9),
    _node("demo.shape.invariants", "shape_invariants",
          "Evaluates declared task invariants and postconditions.",
          roles=("verifier",), capability="shape",
          takes="CandidateResult", gives="ShapeVerdict",
          quality=0.94, latency_ms=14),
)

INDEPENDENT = (
    _node("demo.independent.oracle", "independent_oracle",
          "Compares against a source independent of the producer.",
          roles=("verifier",), capability="independent",
          takes="ShapeVerdict", gives="VerifiedOutcome",
          params=(_choice("oracle", "reference-data", "recomputation"),),
          quality=0.95, latency_ms=180),
    _node("demo.independent.consensus", "independent_consensus",
          "Requires agreement between independent producers.",
          roles=("verifier",), capability="independent",
          takes="ShapeVerdict", gives="VerifiedOutcome",
          params=(_choice("rule", "unanimous", "majority", "weighted"),),
          deterministic=False, quality=0.97, latency_ms=2600, cost_usd=0.02),
    _node("demo.independent.human", "independent_human",
          "An authorized person accepts or rejects the outcome.",
          roles=("verifier",), capability="independent",
          takes="ShapeVerdict", gives="VerifiedOutcome",
          permissions=("human",), quality=0.99, latency_ms=1200000, cost_usd=3.0),
    _node("demo.independent.visual", "independent_visual",
          "Compares a rendering against an approved baseline.",
          roles=("verifier",), capability="independent",
          takes="ShapeVerdict", gives="VerifiedOutcome",
          quality=0.88, latency_ms=420),
    _node("demo.independent.ocr", "independent_ocr",
          "Confirms the result is visible to a reader, not merely present.",
          roles=("verifier",), capability="independent",
          takes="ShapeVerdict", gives="VerifiedOutcome",
          params=(_choice("backend", "rapidocr", "tesseract"),),
          quality=0.86, latency_ms=650),
)

# === 6. EMIT: persist the result -> write the receipt ========================

PERSIST = (
    _node("demo.persist.file", "persist_file",
          "Writes the result to a file.", roles=("sink",),
          capability="persist", takes="VerifiedOutcome", gives="StoredResult",
          params=(_choice("format", "json", "columnar"),),
          permissions=("filesystem:write",), effects=("local:file",),
          quality=0.99, latency_ms=7),
    _node("demo.persist.database", "persist_database",
          "Writes the result to an authorized table.", roles=("sink",),
          capability="persist", takes="VerifiedOutcome", gives="StoredResult",
          params=(_choice("mode", "append", "upsert"),),
          permissions=("database:write",), effects=("external:row-state",),
          quality=0.96, latency_ms=110),
    _node("demo.persist.publish", "persist_publish",
          "Publishes the result to an authorized endpoint.",
          roles=("sink", "action"), capability="persist",
          takes="VerifiedOutcome", gives="StoredResult",
          params=(_choice("delivery", "at-least-once", "exactly-once"),),
          permissions=("network",), effects=("external:published",),
          quality=0.93, latency_ms=290),
    _node("demo.persist.memory", "persist_memory",
          "Returns the result in memory without storing it.",
          roles=("sink",), capability="persist",
          takes="VerifiedOutcome", gives="StoredResult",
          quality=0.85, latency_ms=1),
)

RECEIPT = (
    _node("demo.receipt.bundle", "receipt_bundle",
          "Bundles artifacts, provenance and replay data into a receipt.",
          roles=("sink",), capability="receipt",
          takes="StoredResult", gives="TaskReceipt",
          params=(_choice("detail", "summary", "full"),),
          permissions=("filesystem:write",), effects=("local:file",),
          quality=0.99, latency_ms=40),
    _node("demo.receipt.checkpoint", "receipt_checkpoint",
          "Writes a resumable, content-addressed checkpoint.",
          roles=("sink",), capability="receipt",
          takes="StoredResult", gives="TaskReceipt",
          permissions=("filesystem:write",), effects=("local:file",),
          quality=0.97, latency_ms=26),
    _node("demo.receipt.notify", "receipt_notify",
          "Notifies authorized parties that the task completed.",
          roles=("sink", "action"), capability="receipt",
          takes="StoredResult", gives="TaskReceipt",
          permissions=("network",), effects=("external:notification",),
          quality=0.90, latency_ms=150),
)

NODES = (RESOLVE + SESSION + READ + DECODE + PARSE + NORMALIZE + PLAN + ATTACH
         + LOCATE + ACT + SHAPE + INDEPENDENT + PERSIST + RECEIPT)


#: (id, name, input, output, capability, description, success, optional)
SUBSTEPS = {
    "acquire": (
        ("resolve", "Resolve target", "TaskReference", "ResolvedTarget", "resolve",
         "Turn the reference into an authorized, concrete target.",
         "the target is concrete and authorized", False),
        ("session", "Open session", "ResolvedTarget", "OpenSession", "session",
         "Open something capable of fetching that target.",
         "a session exists and is usable", False),
        ("read", "Read payload", "OpenSession", "InputHandle", "read",
         "Read a versioned, hashed payload out of the session.",
         "the payload is readable, identified and versioned", False),
    ),
    "canonicalize": (
        ("decode", "Decode bytes", "InputHandle", "DecodedText", "decode",
         "Establish the encoding and normal form.",
         "the text is decoded and in a declared normal form", True),
        ("parse", "Parse structure", "DecodedText", "ParsedRecords", "parse",
         "Recover records from the text.",
         "records exist, with every repair recorded", False),
        ("normalize", "Normalize values", "ParsedRecords", "CanonicalState",
         "normalize", "Make schema, units, locale and identity explicit.",
         "downstream nodes receive a typed, versioned representation", True),
    ),
    "enrich": (
        ("plan", "Plan context", "CanonicalState", "ContextPlan", "plan",
         "Decide what additional context the task actually needs.",
         "the plan names what is needed and why", True),
        ("attach", "Attach evidence", "ContextPlan", "EnrichedState", "attach",
         "Attach it, with provenance and freshness.",
         "every derived field carries a source and a freshness stamp", False),
    ),
    "transform": (
        ("locate", "Locate target", "EnrichedState", "LocatedTarget", "locate",
         "Find the thing to act on or read from.",
         "the target is identified and still present", False),
        ("act", "Apply or act", "LocatedTarget", "CandidateResult", "act",
         "Produce the result, or perform a permitted external effect.",
         "a candidate result exists with its inputs recorded", False),
    ),
    "verify": (
        ("shape", "Check shape", "CandidateResult", "ShapeVerdict", "shape",
         "Check the result is the right shape before judging its content.",
         "the result satisfies its declared schema and invariants", False),
        ("independent", "Check independently", "ShapeVerdict", "VerifiedOutcome",
         "independent", "Judge the outcome independently of what produced it.",
         "an independent check accepted the outcome", False),
    ),
    "emit": (
        ("persist", "Persist result", "VerifiedOutcome", "StoredResult", "persist",
         "Store or return the result.", "the result is durable or returned", False),
        ("receipt", "Write receipt", "StoredResult", "TaskReceipt", "receipt",
         "Record evidence, provenance and replay data.",
         "the run is replayable from the receipt", False),
    ),
}

#: (id, name, input, output, description, success, variant axes)
STAGE_SPECS = (
    ("acquire", "Acquire inputs", "TaskReference", "InputHandle",
     "Resolve authorized task references into readable, versioned handles.",
     "the input is readable, identified and versioned",
     ("controller", "binary", "display", "transport")),
    ("canonicalize", "Canonicalize representation", "InputHandle", "CanonicalState",
     "Make schema, units, encoding and identity explicit.",
     "downstream nodes receive a typed, versioned representation",
     ("format", "schema", "units", "locale", "precision")),
    ("enrich", "Enrich and derive context", "CanonicalState", "EnrichedState",
     "Attach required context, evidence, provenance and freshness.",
     "every derived field carries a source and a freshness stamp",
     ("source", "recency", "depth")),
    ("transform", "Transform or act", "EnrichedState", "CandidateResult",
     "Produce the result, or perform a permitted external effect.",
     "a candidate result exists with its inputs recorded",
     ("method", "model", "strategy")),
    ("verify", "Verify success", "CandidateResult", "VerifiedOutcome",
     "Judge the outcome independently of whatever produced it.",
     "an independent check accepted the outcome",
     ("independence", "strictness", "sample")),
    ("emit", "Emit result and receipt", "VerifiedOutcome", "TaskReceipt",
     "Persist or return the result, evidence, provenance and replay data.",
     "the receipt is durable and the run is replayable",
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


def _route(**by_substep: str) -> dict[str, str]:
    return dict(by_substep)


def workbench() -> WorkbenchDefinition:
    """The demonstration, assembled and validated."""
    candidates = expand_node_candidates(NODES)

    built: list[StageDefinition] = []
    for sid, name, takes, gives, description, success, axes in STAGE_SPECS:
        subs = tuple(
            StageDefinition(
                id=sub_id, name=sub_name, input_type=sub_in,
                output_type=sub_out, required_capabilities=(capability,),
                description=sub_desc, success=sub_success, optional=optional,
            ).with_discovered_candidates(NODES, candidates)
            for sub_id, sub_name, sub_in, sub_out, capability, sub_desc,
            sub_success, optional in SUBSTEPS[sid])
        built.append(StageDefinition(
            id=sid, name=name, input_type=takes, output_type=gives,
            description=description, success=success, variant_axes=axes,
            substages=subs))
    stages = tuple(built)

    cheapest = SolutionDefinition(
        id="cheapest", name="Cheapest route", status="baseline",
        description="What runs before there is any evidence: no browser, no "
                    "model, no external effect.",
        route=_route(
            resolve=_cid("demo.resolve.literal"),
            session=_cid("demo.session.filesystem", mode="file"),
            read=_cid("demo.read.whole"),
            decode=_cid("demo.decode.bom"),
            parse=_cid("demo.parse.format", format="json"),
            normalize=_cid("demo.normalize.schema", strategy="strict"),
            plan=_cid("demo.plan.rules"),
            attach=_cid("demo.attach.provenance"),
            locate=_cid("demo.locate.selector"),
            act=_cid("demo.act.rules", ruleset="conservative"),
            shape=_cid("demo.shape.json_schema"),
            independent=_cid("demo.independent.oracle", oracle="recomputation"),
            persist=_cid("demo.persist.file", format="json"),
            receipt=_cid("demo.receipt.bundle", detail="summary")),
        metrics={"quality": 0.78, "latency_ms": 320, "cost_usd": 0.0},
        tags=("baseline", "deterministic"))

    accuracy = SolutionDefinition(
        id="accuracy_first", name="Accuracy-first route", status="candidate",
        description="Composite action with independent consensus, and a human "
                    "as the last fallback.",
        route=_route(
            resolve=_cid("demo.resolve.directory"),
            session=_cid("demo.session.database", dialect="postgres"),
            read=_cid("demo.read.whole"),
            decode=_cid("demo.decode.unicode", form="NFKC"),
            parse=_cid("demo.parse.format", format="json"),
            normalize=_cid("demo.normalize.schema", strategy="strict"),
            plan=_cid("demo.plan.retrieval", ranking="hybrid"),
            attach=_cid("demo.attach.reference", source="internal"),
            locate=_cid("demo.locate.selector"),
            act=_cid("demo.act.composite", children="rules+model"),
            shape=_cid("demo.shape.invariants"),
            independent=_cid("demo.independent.consensus", rule="majority"),
            persist=_cid("demo.persist.database", mode="upsert"),
            receipt=_cid("demo.receipt.bundle", detail="full")),
        fallbacks={"independent": (_cid("demo.independent.oracle",
                                        oracle="reference-data"),
                                   _cid("demo.independent.human"))},
        metrics={"quality": 0.95, "latency_ms": 3400, "cost_usd": 0.036},
        tags=("quality", "independent-verification"))

    learned = SolutionDefinition(
        id="learned", name="Learned route", status="learned",
        description="Where evidence moved the choice: a defended source needed "
                    "a real browser, the DOM stopped being trustworthy, and OCR "
                    "caught a result that was present in the data and invisible "
                    "on the page.",
        route=_route(
            resolve=_cid("demo.resolve.template", strictness="lenient"),
            session=_cid("demo.session.browser", controller="Playwright",
                         binary="Firefox", display="headless"),
            read=_cid("demo.read.rendered", settle="mutation-quiet"),
            decode=_cid("demo.decode.unicode", form="NFKC"),
            parse=_cid("demo.parse.tolerant"),
            normalize=_cid("demo.normalize.identity", key="composite"),
            plan=_cid("demo.plan.rules"),
            attach=_cid("demo.attach.provenance"),
            locate=_cid("demo.locate.heuristic", strategy="nearest-label"),
            act=_cid("demo.act.model", model="GLM", strategy="field-by-field"),
            shape=_cid("demo.shape.typed"),
            independent=_cid("demo.independent.ocr", backend="rapidocr"),
            persist=_cid("demo.persist.file", format="json"),
            receipt=_cid("demo.receipt.bundle", detail="full")),
        fallbacks={"session": (_cid("demo.session.browser", controller="Selenium",
                                    binary="Chrome", display="headless"),),
                   "act": (_cid("demo.act.rules", ruleset="aggressive"),)},
        metrics={"quality": 0.91, "latency_ms": 6200, "cost_usd": 0.019},
        tags=("learned", "browser", "ocr"))

    fastest = SolutionDefinition(
        id="fastest", name="Fastest route", status="candidate",
        description="Every sub-step taking its cheapest admitted candidate.",
        route=_route(
            resolve=_cid("demo.resolve.literal"),
            session=_cid("demo.session.filesystem", mode="file"),
            read=_cid("demo.read.whole"),
            decode=_cid("demo.decode.passthrough"),
            parse=_cid("demo.parse.format", format="json"),
            normalize=_cid("demo.normalize.passthrough"),
            plan=_cid("demo.plan.none"),
            attach=_cid("demo.attach.provenance"),
            locate=_cid("demo.locate.recorded"),
            act=_cid("demo.act.rules", ruleset="aggressive"),
            shape=_cid("demo.shape.json_schema"),
            independent=_cid("demo.independent.oracle", oracle="recomputation"),
            persist=_cid("demo.persist.memory"),
            receipt=_cid("demo.receipt.checkpoint")),
        metrics={"quality": 0.61, "latency_ms": 240, "cost_usd": 0.0},
        tags=("speed", "pass-through"))

    careful = SolutionDefinition(
        id="human_in_the_loop", name="Human-in-the-loop route",
        status="candidate",
        description="For effects that are hard to undo: a person decides, and a "
                    "person checks.",
        route=_route(
            resolve=_cid("demo.resolve.directory"),
            session=_cid("demo.session.http", retries="bounded"),
            read=_cid("demo.read.streamed", chunking="adaptive"),
            decode=_cid("demo.decode.statistical"),
            parse=_cid("demo.parse.format", format="json"),
            normalize=_cid("demo.normalize.locale", locale="en-GB"),
            plan=_cid("demo.plan.rules"),
            attach=_cid("demo.attach.freshness", policy="etag"),
            locate=_cid("demo.locate.selector"),
            act=_cid("demo.act.human"),
            shape=_cid("demo.shape.invariants"),
            independent=_cid("demo.independent.human"),
            persist=_cid("demo.persist.publish", delivery="exactly-once"),
            receipt=_cid("demo.receipt.notify")),
        metrics={"quality": 0.985, "latency_ms": 2_150_000, "cost_usd": 5.6},
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
