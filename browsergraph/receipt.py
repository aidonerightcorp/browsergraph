"""What actually happened, in a form you can act on later.

A run that says `ok=True` and nothing else is unfalsifiable. Six weeks later,
when the extracted price is wrong, the only questions that matter are: which
engine, which binary, which node produced it, what did the verifier check, how
long did it take, and can I run exactly this again. A log line answers none of
them.

So a receipt records the **route** (which node did each step), the **spec** it
ran under, the **environment** it ran in, per-node timing and outcome, every
artifact with a content hash, and the verdict — including which nodes were
verifiers, because "it worked" from the thing that did the work is not
evidence.

Two design choices worth stating:

**Artifacts are hashed.** A path in a receipt is a promise about a file. Files
get overwritten, so the hash is what makes the promise checkable — and
`verify_artifacts()` will tell you which ones have changed since.

**A receipt records failures too.** The reflex is to write one on success and
skip it otherwise, which throws away the only runs that had something to teach.
`ok` is a field, not a precondition.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import platform
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"


def _hash(path: str, limit: int = 64 * 1024 * 1024) -> str:
    """A content hash, or a reason there isn't one.

    Bounded: a receipt should not stall for two minutes hashing a video, and a
    truncated hash that says so is more useful than an absent one.
    """
    try:
        digest = hashlib.sha256()
        size = 0
        with open(path, "rb") as fh:
            while chunk := fh.read(1 << 20):
                digest.update(chunk)
                size += len(chunk)
                if size >= limit:
                    return f"sha256-partial:{digest.hexdigest()[:32]}"
        return f"sha256:{digest.hexdigest()[:32]}"
    except OSError as e:
        return f"unreadable: {type(e).__name__}"


@dataclass
class Artifact:
    path: str
    bytes: int = 0
    digest: str = ""

    @classmethod
    def of(cls, path: str) -> Artifact:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        return cls(path=path, bytes=size, digest=_hash(path))

    def unchanged(self) -> bool:
        return bool(self.digest) and _hash(self.path) == self.digest

    def to_dict(self) -> dict:
        return {"path": self.path, "bytes": self.bytes, "digest": self.digest}


@dataclass
class StepRecord:
    """One node's turn: what it was, how long, and what it produced."""
    key: str = ""
    kind: str = ""
    name: str = ""
    ok: bool = True
    seconds: float = 0.0
    wrote: tuple[str, ...] = ()
    verifies: bool = False
    mutates: bool = False
    selector: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        out = {"key": self.key, "kind": self.kind, "name": self.name,
               "ok": self.ok, "seconds": round(self.seconds, 4)}
        for field_name in ("wrote", "verifies", "mutates", "selector", "error"):
            value = getattr(self, field_name)
            if value:
                out[field_name] = list(value) if isinstance(value, tuple) else value
        return out


def environment() -> dict[str, Any]:
    """Enough to know whether a difference is yours or the machine's.

    Versions of the engines that are actually installed, not a fixed list: a
    receipt that claims `playwright: not installed` on a box that never had it
    is noise, and one that omits the version it *did* use is a gap.
    """
    import importlib.metadata as meta

    versions = {}
    for package in ("playwright", "patchright", "selenium",
                    "undetected-chromedriver", "seleniumbase", "nodriver",
                    "zendriver", "pydoll-python", "curl-cffi", "rapidocr-onnxruntime"):
        try:
            versions[package] = meta.version(package)
        except meta.PackageNotFoundError:
            continue
    return {"python": sys.version.split()[0],
            "platform": f"{platform.system()} {platform.release()}",
            "machine": platform.machine(),
            "browsergraph": _own_version(),
            "packages": versions}


def _own_version() -> str:
    try:
        from browsergraph._version import __version__
        return __version__
    except Exception:
        return "unknown"


@dataclass
class TaskReceipt:
    """Durable evidence for one run.

    Written whether the run succeeded or not — the failures are the ones with
    something to teach.
    """
    task: str = ""
    graph: str = ""
    ok: bool = False
    schema_version: str = SCHEMA_VERSION
    started_at: float = 0.0
    seconds: float = 0.0
    spec: Mapping[str, Any] = field(default_factory=dict)
    route: tuple[str, ...] = ()
    steps: tuple[StepRecord, ...] = ()
    data: Mapping[str, Any] = field(default_factory=dict)
    artifacts: tuple[Artifact, ...] = ()
    error: str = ""
    log: tuple[str, ...] = ()
    env: Mapping[str, Any] = field(default_factory=dict)
    verified_by: tuple[str, ...] = ()
    mutated_by: tuple[str, ...] = ()
    #: The content hash of the compiled plan. Without it, "we learned this
    #: route is good" is a statement about a *name*, and the thing behind the
    #: name can be edited with nothing noticing.
    plan: str = ""
    notes: Mapping[str, Any] = field(default_factory=dict)

    # --- the questions a receipt exists to answer ---------------------------

    @property
    def unverified_mutation(self) -> bool:
        """Did this run change something and never check the result?

        The library's founding failure, asked of a completed run rather than of
        a graph. BG003 catches it before running; this catches the case where a
        verifying node was skipped, short-circuited or silently failed.
        """
        return bool(self.mutated_by) and not self.verified_by

    @property
    def slowest(self) -> StepRecord | None:
        return max(self.steps, key=lambda s: s.seconds, default=None)

    def verify_artifacts(self) -> list[str]:
        """Artifacts whose contents no longer match what was recorded."""
        return [a.path for a in self.artifacts if not a.unchanged()]

    def replay(self) -> str:
        """The command that runs this again.

        Enum *values*, not their reprs: `BG_ENGINE=Engine.PLAYWRIGHT` is not a
        command anyone can paste, and a replay line that has to be edited by
        hand is not a replay line.
        """
        def value(key: str, default: str = "") -> str:
            raw = self.spec.get(key, default)
            return str(getattr(raw, "value", raw))

        bits = [f"BG_ENGINE={value('engine', 'playwright')}"]
        for key, name in (("binary", "BG_BINARY"), ("display", "BG_DISPLAY")):
            if self.spec.get(key):
                bits.append(f"{name}={value(key)}")
        return " ".join(bits) + f"  browsergraph run {self.graph or 'graph.yaml'}"

    # --- serialisation ------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "task": self.task, "graph": self.graph, "ok": self.ok,
            "started_at": self.started_at, "seconds": round(self.seconds, 4),
            "spec": dict(self.spec), "route": list(self.route),
            "steps": [s.to_dict() for s in self.steps],
            "data": _safe(self.data),
            "artifacts": [a.to_dict() for a in self.artifacts],
            "error": self.error, "log": list(self.log), "env": dict(self.env),
            "verified_by": list(self.verified_by),
            "mutated_by": list(self.mutated_by),
            "plan": self.plan,
            "unverified_mutation": self.unverified_mutation,
            "replay": self.replay(),
            "notes": dict(self.notes),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def write(self, path: str) -> str:
        pathlib.Path(path).write_text(self.to_json(), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str) -> TaskReceipt:
        return cls.from_dict(json.loads(pathlib.Path(path).read_text("utf-8")))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TaskReceipt:
        return cls(
            task=data.get("task", ""), graph=data.get("graph", ""),
            ok=bool(data.get("ok")),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            started_at=float(data.get("started_at", 0.0)),
            seconds=float(data.get("seconds", 0.0)),
            spec=dict(data.get("spec") or {}),
            route=tuple(data.get("route") or ()),
            steps=tuple(StepRecord(**s) for s in data.get("steps") or ()),
            data=dict(data.get("data") or {}),
            artifacts=tuple(Artifact(**a) for a in data.get("artifacts") or ()),
            error=data.get("error", ""), log=tuple(data.get("log") or ()),
            env=dict(data.get("env") or {}),
            verified_by=tuple(data.get("verified_by") or ()),
            mutated_by=tuple(data.get("mutated_by") or ()),
            plan=data.get("plan", ""),
            notes=dict(data.get("notes") or {}))

    def text(self) -> str:
        status = "ok" if self.ok else f"FAILED — {self.error}"
        lines = [f"{self.graph or 'run'} [{self.spec.get('engine', '?')}] "
                 f"{self.seconds:.2f}s — {status}"]
        for step in self.steps:
            mark = "ok " if step.ok else "FAIL"
            flags = ("verify" if step.verifies else
                     "mutate" if step.mutates else "")
            lines.append(f"  [{mark}] {step.seconds:>6.2f}s  {step.kind:<14}"
                         f"{step.name:<18} {flags}")
        if self.artifacts:
            lines.append(f"  {len(self.artifacts)} artifact(s):")
            for artifact in self.artifacts:
                lines.append(f"      {artifact.path}  "
                             f"{artifact.bytes:,}B  {artifact.digest}")
        if self.unverified_mutation:
            lines.append("  WARNING: this run changed remote state and nothing "
                         "verified the outcome")
        if self.plan:
            lines.append(f"  plan: {self.plan}")
        lines.append(f"  replay: {self.replay()}")
        return "\n".join(lines)


def _safe(data: Mapping[str, Any], limit: int = 4000) -> dict:
    """Context data, trimmed so a receipt stays a receipt.

    A page's full HTML in `data` would make the file unreadable and unshippable;
    truncation is noted inline rather than done silently.
    """
    out = {}
    for key, value in data.items():
        text = value if isinstance(value, (int, float, bool, type(None))) else str(value)
        if isinstance(text, str) and len(text) > limit:
            text = text[:limit] + f"… [{len(str(value))} chars truncated]"
        out[key] = text
    return out


class Recorder:
    """Times each node and builds the receipt.

    Kept outside `run()` on purpose. Recording is not the graph's job, and a
    caller who does not want a receipt should not pay for one.
    """

    def __init__(self, task: str = "") -> None:
        self.task = task
        self.steps: list[StepRecord] = []
        self._started = 0.0
        self._t0 = 0.0

    def __enter__(self) -> Recorder:
        self._started = time.time()
        self._t0 = time.monotonic()
        return self

    def __exit__(self, *exc) -> None:
        self.seconds = time.monotonic() - self._t0

    def step(self, node: object, ok: bool = True, seconds: float = 0.0,
             error: str = "", key: str = "") -> None:
        self.steps.append(StepRecord(
            key=key, kind=getattr(node, "kind", ""),
            name=getattr(node, "name", ""),
            ok=ok, seconds=seconds, error=error,
            wrote=tuple(getattr(node, "writes", ()) or ()),
            verifies=bool(getattr(node, "verifies", False)),
            mutates=bool(getattr(node, "mutates", False)),
            selector=str(getattr(node, "selector", "") or "")))

    def finish(self, result: Any, graph: Any = None) -> TaskReceipt:
        return of_run(result, graph=graph, task=self.task,
                      steps=tuple(self.steps),
                      seconds=getattr(self, "seconds", 0.0),
                      started_at=self._started)


def of_run(result: Any, *, graph: Any = None, task: str = "",
           steps: Sequence[StepRecord] = (), seconds: float = 0.0,
           started_at: float = 0.0, notes: Mapping[str, Any] | None = None,
           plan: Any = None) -> TaskReceipt:
    """Build a receipt from a finished `RunResult`.

    Works without a `Recorder` — per-node timings are simply absent — because a
    receipt after the fact is far better than no receipt, and demanding
    instrumentation up front is how evidence ends up optional.
    """
    context = getattr(result, "context", None)
    spec = getattr(result, "spec", None)
    nodes = _nodes_of(graph)
    if not steps and nodes:
        steps = tuple(StepRecord(
            key=key, kind=getattr(node, "kind", ""),
            name=getattr(node, "name", ""),
            wrote=tuple(getattr(node, "writes", ()) or ()),
            verifies=bool(getattr(node, "verifies", False)),
            mutates=bool(getattr(node, "mutates", False)),
            selector=str(getattr(node, "selector", "") or ""))
            for key, node in nodes.items()
            if not getattr(result, "executed", None) or key in result.executed)

    return TaskReceipt(
        task=task or getattr(graph, "name", ""),
        graph=getattr(graph, "name", ""),
        ok=bool(getattr(result, "ok", False)),
        started_at=started_at or time.time(),
        seconds=seconds,
        spec=spec.to_dict() if spec is not None and hasattr(spec, "to_dict") else {},
        route=tuple(getattr(result, "executed", ()) or ()),
        steps=tuple(steps),
        data=dict(getattr(context, "data", {}) or {}),
        artifacts=tuple(Artifact.of(p)
                        for p in (getattr(context, "artifacts", []) or [])),
        error=getattr(context, "error", "") or "",
        log=tuple(getattr(context, "log", []) or []),
        env=environment(),
        verified_by=tuple(s.name for s in steps if s.verifies and s.ok),
        mutated_by=tuple(s.name for s in steps if s.mutates and s.ok),
        plan=getattr(plan, "digest", plan) or "",
        notes=dict(notes or {}))


def _nodes_of(graph: Any) -> dict[str, Any]:
    nodes = getattr(graph, "nodes", None)
    return dict(nodes) if isinstance(nodes, dict) else {}


def compare(before: TaskReceipt, after: TaskReceipt) -> list[str]:
    """What changed between two runs of the same graph.

    The point of keeping receipts: when a graph that worked stops working, the
    diff between the last good run and this one is the shortest path to why.
    """
    out = []
    if before.plan and after.plan and before.plan != after.plan:
        out.append(f"a different plan ran: {before.plan} -> {after.plan}")
    if before.spec.get("engine") != after.spec.get("engine"):
        out.append(f"engine {before.spec.get('engine')} -> {after.spec.get('engine')}")
    if before.ok != after.ok:
        out.append(f"outcome {before.ok} -> {after.ok}"
                   + (f" ({after.error})" if after.error else ""))
    if set(before.route) != set(after.route):
        gone = [k for k in before.route if k not in after.route]
        new = [k for k in after.route if k not in before.route]
        if gone:
            out.append("no longer ran: " + ", ".join(gone))
        if new:
            out.append("newly ran: " + ", ".join(new))
    before_env = (before.env or {}).get("packages", {})
    after_env = (after.env or {}).get("packages", {})
    for package, version in after_env.items():
        if package in before_env and before_env[package] != version:
            out.append(f"{package} {before_env[package]} -> {version}")
    for key in set(before.data) | set(after.data):
        if before.data.get(key) != after.data.get(key):
            out.append(f"data[{key}]: {before.data.get(key)!r} -> "
                       f"{after.data.get(key)!r}")
    if before.seconds and after.seconds > before.seconds * 2:
        out.append(f"{after.seconds / before.seconds:.1f}x slower "
                   f"({before.seconds:.2f}s -> {after.seconds:.2f}s)")
    return out


def bundle(receipts: Iterable[TaskReceipt], path: str) -> str:
    """Many receipts in one file, for feeding an optimizer real evidence."""
    payload = {"schema_version": SCHEMA_VERSION,
               "receipts": [r.to_dict() for r in receipts]}
    pathlib.Path(path).write_text(json.dumps(payload, indent=2, default=str),
                                  encoding="utf-8")
    return path
