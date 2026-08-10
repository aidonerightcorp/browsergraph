"""browsergraph — composable browser automation as nodes over a dimension space.

Any engine (playwright / patchright / selenium / cdp / mock) × any binary,
transport, display, stealth and behaviour setting, driven by graphs of reusable
nodes. Action nodes talk to a `BrowserPort`, never to an engine directly, so a
node written once runs on every engine.
"""
from browsergraph._version import __version__ as __version__
from browsergraph.compile import Plan, Step, compile_route
from browsergraph.contracts import Contract, ContractError, contract_of
from browsergraph.dimensions import (
    Behavior,
    Binary,
    Display,
    Engine,
    Identity,
    LLMConfig,
    LLMControl,
    Spec,
    Stealth,
    Transport,
    is_valid,
    validate,
)
from browsergraph.evidence import Evidence, Observation, Posterior

# Facets are exported under `facet_*` names. Their module-level names are
# deliberately plain (`validate`, `fields`, `merge`) because inside `facets.py`
# there is no ambiguity; at package level there is — `validate` already means
# dimension validation — so the prefix is added here rather than there.
from browsergraph.facets import WELL_KNOWN as WELL_KNOWN_FACETS
from browsergraph.facets import FacetSpec
from browsergraph.facets import fields as facet_fields
from browsergraph.facets import keywords as facet_keywords
from browsergraph.facets import merge as merge_facets
from browsergraph.facets import normalize as normalize_facet
from browsergraph.facets import specs_for as facet_specs_for
from browsergraph.facets import validate as validate_facets

# `Edge` at the top level is the *workbench* edge — the typed stage-to-stage
# connection people write in definitions. `graph.Edge` is the runtime execution
# edge, a different thing with the same name, so it is exported under a name
# that says which one it is instead of silently shadowing.
from browsergraph.graph import Edge as GraphEdge
from browsergraph.graph import EdgeKind, Graph, RunResult, run
from browsergraph.manifest import (
    NodeDefinition,
    NodeManifest,
    ParameterSpec,
    PortSpec,
    described_node,
    manifest_of,
)
from browsergraph.ports import BrowserPort, Context, Element, ExtendedPort, PageState
from browsergraph.receipt import Recorder, TaskReceipt
from browsergraph.router import Role, Router
from browsergraph.types import Lattice
from browsergraph.viz import Figure as VizFigure
from browsergraph.viz import report as viz_report
from browsergraph.workbench import (
    Edge,
    FeedbackDefinition,
    NodeCandidate,
    OptimizationObjective,
    OptimizationProfile,
    SolutionDefinition,
    StageDefinition,
    WorkbenchDefinition,
    candidate_id,
    expand_node_candidates,
)

__all__ = [
    "Behavior", "Binary", "Display", "Engine", "Identity", "LLMConfig",
    "LLMControl", "Spec", "Stealth", "Transport", "is_valid", "validate",
    "Graph", "RunResult", "run", "Edge", "EdgeKind", "GraphEdge",
    "BrowserPort", "Context", "Element", "PageState",
    "Contract", "ContractError", "contract_of",
    # the portable description layer: what a node is, separately from what it does
    "NodeManifest", "NodeDefinition", "PortSpec", "ParameterSpec",
    "described_node", "manifest_of",
    # stages, candidates, routes, feedback, optimization
    "NodeCandidate", "StageDefinition", "SolutionDefinition", "FeedbackDefinition",
    "OptimizationObjective", "OptimizationProfile", "WorkbenchDefinition",
    "candidate_id", "expand_node_candidates",
    # evidence, capability and model routing
    "ExtendedPort", "TaskReceipt", "Recorder", "Router", "Role",
    "Evidence", "Observation", "Posterior",
    # the graph, its types, and the compiled plan
    "Lattice", "Plan", "Step", "compile_route",
    # open description: types bind, facets rank
    "FacetSpec", "WELL_KNOWN_FACETS", "facet_fields", "facet_keywords",
    "facet_specs_for", "merge_facets", "normalize_facet", "validate_facets",
    # pictures of any workbench — `import browsergraph.viz` for the rest
    "VizFigure", "viz_report",
]
