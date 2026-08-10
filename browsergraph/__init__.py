"""browsergraph — composable browser automation as nodes over a dimension space.

Any engine (playwright / patchright / selenium / cdp / mock) × any binary,
transport, display, stealth and behaviour setting, driven by graphs of reusable
nodes. Action nodes talk to a `BrowserPort`, never to an engine directly, so a
node written once runs on every engine.
"""
from browsergraph._version import __version__ as __version__
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
from browsergraph.graph import Edge, EdgeKind, Graph, RunResult, run
from browsergraph.manifest import (
    NodeDefinition,
    NodeManifest,
    ParameterSpec,
    PortSpec,
    described_node,
    manifest_of,
)
from browsergraph.ports import BrowserPort, Context, Element, PageState
from browsergraph.workbench import (
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
    "Graph", "RunResult", "run", "Edge", "EdgeKind",
    "BrowserPort", "Context", "Element", "PageState",
    "Contract", "ContractError", "contract_of",
    # the portable description layer: what a node is, separately from what it does
    "NodeManifest", "NodeDefinition", "PortSpec", "ParameterSpec",
    "described_node", "manifest_of",
    # stages, candidates, routes, feedback, optimization
    "NodeCandidate", "StageDefinition", "SolutionDefinition", "FeedbackDefinition",
    "OptimizationObjective", "OptimizationProfile", "WorkbenchDefinition",
    "candidate_id", "expand_node_candidates",
]
