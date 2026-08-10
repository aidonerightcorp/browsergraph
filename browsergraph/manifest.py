"""What a node *is*, described separately from what it does.

`nodes/base.py` already carries contracts — `reads`, `writes`, `mutates` — and
those are enforced at import, at composition and at run time. This module is the
portable form of the same idea, and the separation is the point: a manifest can
be read by a registry, a validator, a viewer, a planner or another language
without importing the runtime, launching a browser, or installing the engine the
node happens to need.

That matters more than it sounds. The interesting question — *what could perform
this step, and would it connect to what comes next* — has to be answerable about
nodes that are not installed, not written in Python, or not written yet.

A manifest describes:

* identity and version, so evidence can be attributed to an exact thing;
* roles and capabilities, so a stage can discover what may perform it;
* typed ports, so two candidates can be proven to connect *before* execution;
* parameters with finite choices, so a family expands into concrete candidates;
* permissions and effects, so policy can refuse before scoring gets a say;
* dependencies, resources and runtime, so eligibility is about this machine;
* metrics, always with their source — a prior is not a measurement.

Nothing here executes anything. `NodeDefinition` adds an optional factory and
stays deliberately thin, because forcing every implementation into an
inheritance hierarchy is exactly what stops a registry being open.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from browsergraph import facets

#: Wire-format version. Bumped when the shape changes, not when content does.
SCHEMA_VERSION = "2.0"

#: Namespaced, lowercase, dot-separated. Stable identity is what lets evidence
#: from one run be attributed to a thing in another.
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*(\.[a-z0-9][a-z0-9_]*)+$")

#: Broad role taxonomy for discovery. Deliberately short — roles are for
#: filtering and grouping; `capabilities` is what eligibility is decided on.
ROLES = ("source", "adapter", "transform", "action", "model", "verifier",
         "sink", "composite", "control")

PARAM_TYPES = ("string", "integer", "number", "boolean", "enum", "json")


def _tuple(value: Any) -> tuple:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(value)
    return (value,)


def _dupes(items: Sequence[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        if item in seen and item not in out:
            out.append(item)
        seen.add(item)
    return out


@dataclass(frozen=True)
class PortSpec:
    """One typed input or output.

    `type` is the technical type two candidates must agree on; `semantic` is
    what it means, which two nodes can share while disagreeing on the carrier
    (both produce `text/plain`, one is an address and one is a sentence).
    """
    name: str
    type: str
    semantic: str = ""
    schema: Mapping[str, Any] = field(default_factory=dict)
    units: str = ""
    required: bool = True
    description: str = ""

    def validate(self) -> list[str]:
        bad = []
        if not self.name:
            bad.append("port has no name")
        if not self.type:
            bad.append(f"port {self.name!r} has no type")
        return bad

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"name": self.name, "type": self.type}
        for key in ("semantic", "units", "description"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        if self.schema:
            out["schema"] = dict(self.schema)
        if not self.required:
            out["required"] = False
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PortSpec:
        return cls(name=data.get("name", ""), type=data.get("type", ""),
                   semantic=data.get("semantic", ""),
                   schema=dict(data.get("schema") or {}),
                   units=data.get("units", ""),
                   required=bool(data.get("required", True)),
                   description=data.get("description", ""))


@dataclass(frozen=True)
class ParameterSpec:
    """One configuration dimension.

    `choices` is what makes a parameter *searchable*: a dimension has to be
    enumerable before a family of settings can expand into concrete candidates
    that a user or an optimizer can select between. Open-ended values — a URL, a
    document body, an image — are inputs, not parameters, and belong on a port.
    """
    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    default: Any = None
    choices: tuple = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "choices", _tuple(self.choices))

    @property
    def searchable(self) -> bool:
        return len(self.choices) > 1

    def validate(self) -> list[str]:
        bad = []
        if not self.name:
            bad.append("parameter has no name")
        if self.type not in PARAM_TYPES:
            bad.append(f"parameter {self.name!r} has unknown type {self.type!r}")
        if self.choices and self.default is not None and self.default not in self.choices:
            bad.append(f"parameter {self.name!r} default {self.default!r} "
                       f"is not one of its choices")
        for dupe in _dupes([str(c) for c in self.choices]):
            bad.append(f"parameter {self.name!r} lists choice {dupe!r} twice")
        if self.required and self.default is not None:
            bad.append(f"parameter {self.name!r} is required and also has a default")
        return bad

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.description:
            out["description"] = self.description
        if self.required:
            out["required"] = True
        if self.default is not None:
            out["default"] = self.default
        if self.choices:
            out["choices"] = list(self.choices)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ParameterSpec:
        return cls(name=data.get("name", ""), type=data.get("type", "string"),
                   description=data.get("description", ""),
                   required=bool(data.get("required", False)),
                   default=data.get("default"),
                   choices=tuple(data.get("choices") or ()))


@dataclass(frozen=True)
class NodeManifest:
    """A portable description of one reusable implementation."""
    id: str
    kind: str
    name: str = ""
    version: str = "1.0"
    schema_version: str = SCHEMA_VERSION
    description: str = ""
    source: str = ""
    docs: str = ""

    roles: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    inputs: tuple[PortSpec, ...] = ()
    outputs: tuple[PortSpec, ...] = ()
    parameters: tuple[ParameterSpec, ...] = ()

    permissions: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    resources: Mapping[str, Any] = field(default_factory=dict)
    runtime: Mapping[str, Any] = field(default_factory=dict)
    metrics: Mapping[str, Any] = field(default_factory=dict)

    #: Every *other* way this node can be described — purpose, prose, verbs,
    #: domain tags, method names, cost, provenance, and whatever a pack invents
    #: next. The key space is open on purpose; see `facets.py`.
    #:
    #: The fields above are closed because legality is decided on them. This one
    #: is open because discovery is not. Nothing in here may change whether the
    #: node compiles into a position — it changes only which legal node is
    #: *preferred*. That asymmetry is the whole design: types bind, facets rank.
    facets: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("roles", "capabilities", "tags", "permissions", "effects",
                     "dependencies", "inputs", "outputs", "parameters"):
            object.__setattr__(self, name, _tuple(getattr(self, name)))
        if not self.name:
            object.__setattr__(self, "name", self.id.rsplit(".", 1)[-1].replace("_", " "))

    # --- validation ---------------------------------------------------------

    def validate(self) -> list[str]:
        """Every problem, not the first one.

        A validator that stops at the first error turns fixing a manifest into a
        guessing loop.
        """
        bad: list[str] = []
        if not ID_RE.match(self.id or ""):
            bad.append(f"node id {self.id!r} must be lowercase and namespaced, "
                       f"e.g. 'example.file_loader'")
        if not self.kind:
            bad.append(f"{self.id}: no kind")
        if not self.description:
            bad.append(f"{self.id}: no description — a registry entry nobody can "
                       f"interpret is not discoverable")
        if self.schema_version != SCHEMA_VERSION:
            bad.append(f"{self.id}: manifest schema_version {self.schema_version!r} "
                       f"!= {SCHEMA_VERSION!r}")
        for role in self.roles:
            if role not in ROLES:
                bad.append(f"{self.id}: unknown role {role!r} (known: {', '.join(ROLES)})")
        if not self.capabilities:
            bad.append(f"{self.id}: declares no capabilities, so no stage can "
                       f"ever discover it")

        for label, values in (("role", self.roles), ("capability", self.capabilities),
                              ("tag", self.tags), ("permission", self.permissions),
                              ("effect", self.effects),
                              ("dependency", self.dependencies)):
            for dupe in _dupes(list(values)):
                bad.append(f"{self.id}: {label} {dupe!r} listed twice")

        for label, ports in (("input", self.inputs), ("output", self.outputs)):
            for port in ports:
                bad += [f"{self.id}: {msg}" for msg in port.validate()]
            for dupe in _dupes([p.name for p in ports]):
                bad.append(f"{self.id}: two {label} ports named {dupe!r}")
        if not self.outputs:
            bad.append(f"{self.id}: no output port — nothing downstream could connect")

        for param in self.parameters:
            bad += [f"{self.id}: {msg}" for msg in param.validate()]
        for dupe in _dupes([p.name for p in self.parameters]):
            bad.append(f"{self.id}: two parameters named {dupe!r}")

        # Descriptors are checked for *shape*, never for presence. A node with
        # no facets is perfectly valid and merely harder to find; a node whose
        # declared number holds "quite fast" is broken, because something
        # downstream will try to compare it.
        bad += [f"{self.id}: {msg}" for msg in facets.validate(self.facets)]
        return bad

    def assert_valid(self) -> NodeManifest:
        problems = self.validate()
        if problems:
            raise ValueError(f"invalid node manifest {self.id!r}:\n  "
                             + "\n  ".join(problems))
        return self

    # --- questions a stage asks ---------------------------------------------

    def can(self, *capabilities: str) -> bool:
        return set(capabilities) <= set(self.capabilities)

    def accepts(self, type_name: str) -> bool:
        """Could this node consume that type? A node with no inputs is a source."""
        return not self.inputs or any(p.type == type_name for p in self.inputs)

    def produces(self, type_name: str) -> bool:
        return any(p.type == type_name for p in self.outputs)

    def output_port(self, type_name: str) -> PortSpec | None:
        for port in self.outputs:
            if port.type == type_name:
                return port
        return None

    @property
    def searchable_parameters(self) -> tuple[ParameterSpec, ...]:
        return tuple(p for p in self.parameters if p.searchable)

    def variants(self) -> int:
        """How many concrete candidates this one definition expands into."""
        total = 1
        for param in self.searchable_parameters:
            total *= len(param.choices)
        return total

    # --- wire format --------------------------------------------------------

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"id": self.id, "kind": self.kind, "name": self.name,
                               "version": self.version,
                               "schema_version": self.schema_version,
                               "description": self.description}
        for key in ("source", "docs"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        for key in ("roles", "capabilities", "tags", "permissions", "effects",
                    "dependencies"):
            if getattr(self, key):
                out[key] = list(getattr(self, key))
        if self.inputs:
            out["inputs"] = [p.to_dict() for p in self.inputs]
        if self.outputs:
            out["outputs"] = [p.to_dict() for p in self.outputs]
        if self.parameters:
            out["parameters"] = [p.to_dict() for p in self.parameters]
        for key in ("resources", "runtime", "metrics", "facets"):
            if getattr(self, key):
                out[key] = dict(getattr(self, key))
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NodeManifest:
        return cls(
            id=data.get("id", ""), kind=data.get("kind", ""),
            name=data.get("name", ""), version=data.get("version", "1.0"),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            description=data.get("description", ""),
            source=data.get("source", ""), docs=data.get("docs", ""),
            roles=tuple(data.get("roles") or ()),
            capabilities=tuple(data.get("capabilities") or ()),
            tags=tuple(data.get("tags") or ()),
            inputs=tuple(PortSpec.from_dict(p) for p in data.get("inputs") or ()),
            outputs=tuple(PortSpec.from_dict(p) for p in data.get("outputs") or ()),
            parameters=tuple(ParameterSpec.from_dict(p)
                             for p in data.get("parameters") or ()),
            permissions=tuple(data.get("permissions") or ()),
            effects=tuple(data.get("effects") or ()),
            dependencies=tuple(data.get("dependencies") or ()),
            resources=dict(data.get("resources") or {}),
            runtime=dict(data.get("runtime") or {}),
            metrics=dict(data.get("metrics") or {}),
            facets=dict(data.get("facets") or {}))

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    def with_(self, **changes: Any) -> NodeManifest:
        return replace(self, **changes)


@dataclass
class NodeDefinition:
    """A manifest, optionally coupled to something that can build the node.

    The factory is optional on purpose. A registry must be able to describe a
    node it cannot construct — one implemented in another language, living
    behind a service, generated but not yet admitted, or simply not installed
    here. Refusing to list those would make the answer to "what could do this"
    depend on what happens to be importable, which is the same mistake as
    reporting a browser missing because it is not on PATH.
    """
    manifest: NodeManifest
    factory: Callable[..., Any] | None = None

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def buildable(self) -> bool:
        return self.factory is not None

    def build(self, **params: Any) -> Any:
        if self.factory is None:
            raise TypeError(f"{self.id} is a description only — it has no factory "
                            f"here. It can be inspected, validated and planned "
                            f"with, but not constructed.")
        unknown = set(params) - {p.name for p in self.manifest.parameters}
        if unknown:
            raise TypeError(f"{self.id}: unknown parameter(s) "
                            f"{', '.join(sorted(unknown))}")
        for param in self.manifest.parameters:
            if param.name in params and param.choices \
                    and params[param.name] not in param.choices:
                raise ValueError(
                    f"{self.id}: {param.name}={params[param.name]!r} is not one of "
                    f"{', '.join(map(str, param.choices))}")
        return self.factory(**params)


# --- attaching manifests to code --------------------------------------------

#: Where `@described_node` stashes the manifest on the decorated class.
ATTR = "__node_manifest__"


def described_node(**fields: Any) -> Callable[[type], type]:
    """Attach a manifest to a node class.

    Used as a decorator so the description lives next to the implementation and
    goes stale less often than a separate table would.
    """
    def decorate(cls: type) -> type:
        data = dict(fields)
        data.setdefault("name", cls.__name__)
        data.setdefault("description", (cls.__doc__ or "").strip().split("\n")[0])
        manifest = NodeManifest(**data)
        setattr(cls, ATTR, manifest.assert_valid())
        return cls
    return decorate


#: How a built-in node's declared contract maps onto manifest capabilities.
#: Keeping this as data rather than branches means a new contract flag shows up
#: in manifests without anybody editing an inference function.
_CONTRACT_ROLES = (
    ("mutates", "action", "act"),
    ("verifies", "verifier", "verify"),
    ("needs_browser", "adapter", ""),
)


#: What a port is called when the node decides its name per instance.
DYNAMIC_PORT = "value"


def _keys(node_cls: type, attr: str) -> tuple[str, ...]:
    """The context keys a node class declares, when they are knowable at all.

    Several nodes — `extract`, `for_each`, `ocr_extract`, `subgraph` — write to
    whatever key their `into` argument names, so on the *class* the attribute is
    a property descriptor rather than a tuple. That is a real property of those
    nodes and not an error: the port exists, its name is bound per instance. The
    honest description is one dynamic port, not a port called "<property
    object>", which is what a naive `getattr` produces and what JSON then
    refuses to serialize.
    """
    value = getattr(node_cls, attr, ())
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (tuple, list, set, frozenset)):
        return tuple(str(v) for v in value)
    return (DYNAMIC_PORT,)


def manifest_of(node_cls: type, *, namespace: str = "browsergraph") -> NodeManifest:
    """The manifest for a node class, declared or inferred.

    Existing node classes already declare `reads`, `writes`, `mutates` and
    `verifies`; that is most of a manifest already, so they get one without
    being rewritten. An inferred manifest is a starting point rather than a
    good description — `@described_node` overrides it wherever an author cares
    enough to say more.
    """
    declared = getattr(node_cls, ATTR, None)
    if isinstance(declared, NodeManifest):
        return declared

    kind = getattr(node_cls, "kind", "") or node_cls.__name__.lower()
    reads = _keys(node_cls, "reads")
    writes = _keys(node_cls, "writes")
    roles, capabilities, effects = [], [], []
    for attr, role, capability in _CONTRACT_ROLES:
        if getattr(node_cls, attr, False):
            roles.append(role)
            if capability:
                capabilities.append(capability)
    if getattr(node_cls, "mutates", False):
        effects.append("external:page-state")
    if writes and "extract" not in capabilities:
        capabilities.append("extract")
    if not capabilities:
        capabilities.append(kind)
    if not roles:
        roles.append("transform")

    doc = (node_cls.__doc__ or "").strip().split("\n")[0]
    return NodeManifest(
        id=f"{namespace}.{kind}",
        kind=kind,
        name=node_cls.__name__,
        description=doc or f"The built-in {kind} node.",
        roles=tuple(dict.fromkeys(roles)),
        capabilities=tuple(dict.fromkeys(capabilities)),
        tags=("builtin",),
        inputs=tuple(PortSpec(name=key, type="ContextValue",
                              description="bound per instance"
                              if key == DYNAMIC_PORT else "")
                     for key in reads),
        outputs=tuple(PortSpec(name=key, type="ContextValue",
                               description="bound per instance"
                               if key == DYNAMIC_PORT else "")
                      for key in writes)
        or (PortSpec(name="state", type="PageState"),),
        permissions=("browser",) if getattr(node_cls, "needs_browser", False) else (),
        effects=tuple(effects),
        runtime={"needs_browser": bool(getattr(node_cls, "needs_browser", False)),
                 "mutates": bool(getattr(node_cls, "mutates", False)),
                 "verifies": bool(getattr(node_cls, "verifies", False))},
        source=f"{node_cls.__module__}.{node_cls.__qualname__}")


def registry_manifests(namespace: str = "browsergraph") -> tuple[NodeManifest, ...]:
    """A manifest for every registered built-in node kind."""
    import browsergraph.nodes  # noqa: F401  (registers the built-ins)
    from browsergraph.nodes.base import REGISTRY

    return tuple(manifest_of(cls, namespace=namespace)
                 for _, cls in sorted(REGISTRY.items()))
