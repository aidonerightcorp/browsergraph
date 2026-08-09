"""Open plugin format.

A plugin adds nodes, tasks, drivers or classifiers without forking. Two
discovery routes, both standard:

* **Entry points** (`pyproject.toml`) — for installed packages
* **Manifest files** (`plugin.json`) — for a directory of local plugins

The manifest is deliberately plain JSON with a versioned schema, so a harness
written in another language can read the same declaration. It describes
*capabilities*, not implementation: a consumer can list what a plugin provides
without importing it, which matters because importing is what runs code.

Trust: `discover()` only reads manifests. Nothing is imported until `load()`
is called, and `load()` reports exactly what each plugin registered — a plugin
that quietly overrides a built-in node is visible rather than silent.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = "1.0"

#: Entry-point group installed packages advertise under.
ENTRY_POINT_GROUP = "browsergraph.plugins"

#: What a plugin may contribute.
CAPABILITIES = ("nodes", "tasks", "drivers", "classifiers", "extractors", "dimensions")


class PluginError(RuntimeError):
    pass


@dataclass
class Manifest:
    """A plugin's declaration. Mirrors `plugin.json`."""
    name: str
    version: str = "0.0.0"
    schema_version: str = SCHEMA_VERSION
    description: str = ""
    author: str = ""
    license: str = ""
    homepage: str = ""
    module: str = ""                       # import path; may define plugin_init()
    requires: list[str] = field(default_factory=list)      # pip requirements
    requires_browsergraph: str = ""        # version constraint, informational
    provides: dict[str, list[str]] = field(default_factory=dict)
    permissions: list[str] = field(default_factory=list)   # network, filesystem, llm
    path: str = ""

    @staticmethod
    def from_dict(data: dict, path: str = "") -> Manifest:
        missing = [k for k in ("name", "module") if not data.get(k)]
        if missing:
            raise PluginError(f"manifest missing required field(s): {', '.join(missing)}")
        got = str(data.get("schema_version", SCHEMA_VERSION))
        if got.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
            raise PluginError(
                f"{data['name']}: manifest schema {got} is incompatible with "
                f"{SCHEMA_VERSION} (major version differs)")
        provides = {k: list(v) for k, v in (data.get("provides") or {}).items()}
        unknown = set(provides) - set(CAPABILITIES)
        if unknown:
            raise PluginError(
                f"{data['name']}: unknown capability {sorted(unknown)}; "
                f"valid: {list(CAPABILITIES)}")
        return Manifest(
            name=data["name"], version=str(data.get("version", "0.0.0")),
            schema_version=got, description=data.get("description", ""),
            author=data.get("author", ""), license=data.get("license", ""),
            homepage=data.get("homepage", ""), module=data["module"],
            requires=list(data.get("requires", [])),
            requires_browsergraph=data.get("requires_browsergraph", ""),
            provides=provides, permissions=list(data.get("permissions", [])),
            path=path,
        )

    @staticmethod
    def load(path: str | Path) -> Manifest:
        p = Path(path)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise PluginError(f"{p}: unreadable manifest: {e}") from e
        return Manifest.from_dict(data, path=str(p))

    def to_dict(self) -> dict:
        return {"name": self.name, "version": self.version,
                "schema_version": self.schema_version,
                "description": self.description, "author": self.author,
                "license": self.license, "homepage": self.homepage,
                "module": self.module, "requires": self.requires,
                "requires_browsergraph": self.requires_browsergraph,
                "provides": self.provides, "permissions": self.permissions}

    @property
    def capability_count(self) -> int:
        return sum(len(v) for v in self.provides.values())


@dataclass
class LoadReport:
    """What actually happened when a plugin was imported."""
    manifest: Manifest
    loaded: bool = False
    registered: dict[str, list[str]] = field(default_factory=dict)
    overrides: list[str] = field(default_factory=list)
    undeclared: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return {"plugin": self.manifest.name, "version": self.manifest.version,
                "loaded": self.loaded, "registered": self.registered,
                "overrides": self.overrides, "undeclared": self.undeclared,
                "error": self.error}


def _snapshot() -> dict[str, set[str]]:
    from browsergraph.nodes.base import REGISTRY as NODES
    from browsergraph.tasks.base import REGISTRY as TASKS
    return {"nodes": set(NODES), "tasks": set(TASKS)}


def discover_manifests(*dirs: str | Path) -> list[Manifest]:
    """Read `plugin.json` files without importing anything."""
    roots = [Path(d) for d in dirs] or [
        Path(p) for p in os.environ.get("BROWSERGRAPH_PLUGIN_PATH", "").split(os.pathsep)
        if p
    ]
    out: list[Manifest] = []
    for root in roots:
        if not root.exists():
            continue
        candidates = ([root / "plugin.json"] if (root / "plugin.json").exists()
                      else sorted(root.glob("*/plugin.json")))
        for path in candidates:
            out.append(Manifest.load(path))
    return out


def discover_entry_points() -> list[Manifest]:
    """Manifests advertised by installed packages."""
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover
        return []
    out: list[Manifest] = []
    # entry_points(group=...) is available on every Python this package
    # supports (>=3.10); the old mapping API it replaced was removed in 3.12.
    eps = entry_points(group=ENTRY_POINT_GROUP)
    for ep in eps:
        out.append(Manifest(name=ep.name, module=ep.value,
                            description=f"entry point {ep.value}"))
    return out


def discover(*dirs: str | Path, entry_points: bool = True) -> list[Manifest]:
    found = discover_manifests(*dirs)
    if entry_points:
        found += discover_entry_points()
    seen, unique = set(), []
    for m in found:
        if m.name not in seen:
            seen.add(m.name)
            unique.append(m)
    return unique


def load(manifest: Manifest, allow_override: bool = False) -> LoadReport:
    """Import a plugin and report exactly what it registered.

    A plugin that registers something it did not declare, or replaces a
    built-in, is reported rather than silently accepted.
    """
    report = LoadReport(manifest=manifest)
    before = _snapshot()

    import importlib
    try:
        module = importlib.import_module(manifest.module)
    except ImportError as e:
        report.error = (f"cannot import {manifest.module}: {e}. "
                        + (f"requires: {', '.join(manifest.requires)}"
                           if manifest.requires else ""))
        return report
    except Exception as e:
        report.error = f"{manifest.module} raised on import: {type(e).__name__}: {e}"
        return report

    # `plugin_init`, not `register`: plugins import `register` as the node
    # decorator, so calling module.register() would invoke the decorator with
    # no class and fail on every well-formed plugin.
    setup = getattr(module, "plugin_init", None)
    if callable(setup):
        try:
            setup()
        except Exception as e:
            report.error = (f"{manifest.module}.plugin_init() failed: "
                            f"{type(e).__name__}: {e}")
            return report

    after = _snapshot()
    for kind in ("nodes", "tasks"):
        added = sorted(after[kind] - before[kind])
        if added:
            report.registered[kind] = added
        declared = set(manifest.provides.get(kind, []))
        report.undeclared += [f"{kind}:{n}" for n in added if declared and n not in declared]
        # a name that existed before and now points elsewhere is an override
        report.overrides += [f"{kind}:{n}" for n in declared if n in before[kind]]

    if report.overrides and not allow_override:
        report.error = (f"refusing to load: would override built-ins "
                        f"{report.overrides}; pass allow_override=True to accept")
        return report

    report.loaded = True
    return report


def load_all(*dirs: str | Path, allow_override: bool = False) -> list[LoadReport]:
    return [load(m, allow_override=allow_override) for m in discover(*dirs)]


def template(name: str = "my-plugin") -> dict:
    """A starter manifest, so authors do not have to guess the shape."""
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "version": "0.1.0",
        "description": "What this plugin adds.",
        "author": "",
        "license": "MIT",
        "homepage": "",
        "module": name.replace("-", "_"),
        "requires": [],
        "requires_browsergraph": ">=0.1",
        "provides": {"nodes": [], "tasks": [], "drivers": []},
        "permissions": ["network"],
    }
