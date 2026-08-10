"""Run a compiled plan for real.

Up to now this library could describe a job, check it, and pick a route. It
could not run one. That is a big gap. A plan you cannot run is a drawing.

This module closes it. You give it a plan and a set of functions, one per
candidate. It runs them in layer order, passes values along the edges, checks
the types at each hand-off, and writes down what happened.

How to write a node function:

    def load(path):                  # takes its input ports by name
        return read_csv(path)        # returns its one output

    def split(rows):                 # two outputs? return a dict
        return {"train": rows[:80], "valid": rows[80:]}

Two extra rules, both optional:

* Ask for `workspace` and you get a folder to write files into. Anything you
  put there is collected as an artifact, with its size and a hash.
* Ask for `step` and you get the plan step you are running, so a node can see
  its own parameters.

What you get back is a `Run`. It holds every value produced, the artifacts, a
row per step, and whether the whole thing worked.

The type check at each hand-off is the part worth keeping. A node can promise
`Records` and hand back `None`, and without a check that `None` travels three
steps before something falls over somewhere unrelated. Here it stops at the
node that did it, and the message names that node.
"""
from __future__ import annotations

import inspect
import pathlib
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from browsergraph import types as _types
from browsergraph.compile import Plan, Step
from browsergraph.manifest import PortSpec
from browsergraph.receipt import Artifact


class ExecutionError(RuntimeError):
    """A step failed and the run stopped."""

    def __init__(self, stage: str, problem: str) -> None:
        self.stage = stage
        self.problem = problem
        super().__init__(f"{stage}: {problem}")


@dataclass
class StepRun:
    """What one step did."""
    stage: str
    candidate: str
    ok: bool = True
    seconds: float = 0.0
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    error: str = ""
    effects: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        out = {"stage": self.stage, "candidate": self.candidate,
               "ok": self.ok, "seconds": round(self.seconds, 4),
               "inputs": list(self.inputs), "outputs": list(self.outputs)}
        if self.effects:
            out["effects"] = list(self.effects)
        if self.error:
            out["error"] = self.error
        return out


@dataclass
class Run:
    """Everything that came out of running a plan."""
    plan_digest: str = ""
    values: dict[tuple[str, str], Any] = field(default_factory=dict)
    steps: list[StepRun] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    seconds: float = 0.0
    stopped_at: str = ""

    @property
    def ok(self) -> bool:
        return not self.stopped_at and all(s.ok for s in self.steps)

    def output(self, stage: str, port: str = "") -> Any:
        """One value a stage produced. Leave the port out if there is only one."""
        if port:
            return self.values.get((stage, port))
        found = [v for (s, _), v in self.values.items() if s == stage]
        if len(found) == 1:
            return found[0]
        if not found:
            raise KeyError(f"{stage} produced nothing")
        raise KeyError(f"{stage} has several outputs; name one of "
                       f"{[p for (s, p) in self.values if s == stage]}")

    def final(self) -> dict[str, Any]:
        """The values nothing downstream consumed. Usually what you wanted."""
        return {f"{stage}.{port}": value
                for (stage, port), value in self.values.items()
                if (stage, port) in self._sinks}

    _sinks: set = field(default_factory=set, repr=False)

    def text(self) -> str:
        lines = [f"plan {self.plan_digest[:26]}…",
                 f"{len(self.steps)} steps in {self.seconds:.3f}s "
                 f"— {'ok' if self.ok else 'FAILED at ' + self.stopped_at}"]
        for step in self.steps:
            mark = "ok  " if step.ok else "FAIL"
            effects = f"  [{', '.join(step.effects)}]" if step.effects else ""
            lines.append(f"  {mark} {step.stage:<14} {step.seconds:>7.3f}s  "
                         f"{step.candidate}{effects}")
            if step.error:
                lines.append(f"       {step.error}")
        for art in self.artifacts:
            lines.append(f"  file {art.path}  {art.bytes:,} bytes  "
                         f"{art.digest[:16]}…")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"plan": self.plan_digest, "ok": self.ok,
                "seconds": round(self.seconds, 4),
                "steps": [s.to_dict() for s in self.steps],
                "artifacts": [a.to_dict() for a in self.artifacts]}


class Runtime:
    """The functions that do the work, one per candidate.

    Kept apart from the graph on purpose. The graph says what must happen and
    what could do it. This says what actually runs. Swapping a fake for a real
    one is then a change of runtime, not a change of graph — which is what
    makes a dry run and a live run the same shape.
    """

    def __init__(self, functions: Mapping[str, Callable] | None = None) -> None:
        self._functions: dict[str, Callable] = dict(functions or {})

    def register(self, candidate_id: str, function: Callable) -> Runtime:
        self._functions[candidate_id] = function
        return self

    def add(self, **functions: Callable) -> Runtime:
        """Register several at once. Underscores become dots.

        `runtime.add(load_csv=fn)` registers `load.csv`, because a keyword
        argument cannot contain a dot and candidate ids nearly always do.
        """
        for name, function in functions.items():
            self._functions[name.replace("__", ".").replace("_", ".")] = function
        return self

    def get(self, candidate_id: str) -> Callable | None:
        return self._functions.get(candidate_id)

    def missing(self, plan: Plan) -> list[str]:
        """Candidates in the plan with no function behind them."""
        return [s.candidate for s in plan.steps if s.candidate not in self._functions]

    def __contains__(self, candidate_id: str) -> bool:
        return candidate_id in self._functions


def _call(function: Callable, step: Step, values: Mapping[str, Any],
          workspace: pathlib.Path | None) -> Any:
    """Call the node with only the arguments it actually asked for."""
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):          # builtins have no signature
        return function(**values)

    accepts = set(signature.parameters)
    takes_anything = any(p.kind is inspect.Parameter.VAR_KEYWORD
                         for p in signature.parameters.values())

    kwargs = dict(values) if takes_anything else {
        k: v for k, v in values.items() if k in accepts}
    if "workspace" in accepts and workspace is not None:
        kwargs["workspace"] = workspace
    if "step" in accepts:
        kwargs["step"] = step
    if "params" in accepts:
        kwargs["params"] = dict(step.params)
    return function(**kwargs)


def _as_ports(step: Step) -> tuple[PortSpec, ...]:
    return tuple(PortSpec(name, type_name) for name, type_name in step.outputs)


def run(plan: Plan, runtime: Runtime, inputs: Mapping[str, Any] | None = None,
        *, workspace: str | pathlib.Path | None = None,
        strict: bool = True, allow_effects: bool = True) -> Run:
    """Run every step of the plan in layer order.

    `inputs` feeds the steps that have no upstream edge. Key it by stage id, or
    by `stage.port` when a stage takes more than one thing.

    `strict` stops at the first failure. That is the default because carrying on
    after a step failed produces a result that looks complete and is not.

    `allow_effects=False` refuses to run any step that declared an effect. The
    plan already knows which those are, so a dry run needs no separate code
    path and no cooperation from the node.
    """
    started = time.monotonic()
    supplied = dict(inputs or {})
    folder = pathlib.Path(workspace).resolve() if workspace else None
    if folder is not None:
        folder.mkdir(parents=True, exist_ok=True)
        # Size and mtime, not just the names. "New files only" looks right the
        # first time and then quietly reports nothing on every run after that,
        # because the workspace already holds the file the run just rewrote —
        # which is exactly when you are re-running to check something.
        before = {p: (p.stat().st_size, p.stat().st_mtime_ns)
                  for p in folder.rglob("*") if p.is_file()}
    else:
        before = {}

    result = Run(plan_digest=plan.digest)
    consumed: set[tuple[str, str]] = set()

    # Which upstream port feeds which input port. Built once, read per step.
    feeds: dict[str, list] = {}
    for edge in plan.edges:
        feeds.setdefault(edge.target, []).append(edge)

    by_stage = {s.stage: s for s in plan.steps}

    for layer in plan.layers:
        for stage in layer:
            step = by_stage.get(stage)
            if step is None:
                continue

            function = runtime.get(step.candidate)
            if function is None:
                message = (f"no function registered for {step.candidate!r}. "
                           f"Add one with runtime.register(...).")
                result.steps.append(StepRun(stage, step.candidate, ok=False,
                                            error=message))
                result.stopped_at = stage
                if strict:
                    break
                continue

            if step.effects and not allow_effects:
                message = (f"refused: this step declares "
                           f"{', '.join(step.effects)} and effects are off")
                result.steps.append(StepRun(stage, step.candidate, ok=False,
                                            error=message,
                                            effects=tuple(step.effects)))
                result.stopped_at = stage
                if strict:
                    break
                continue

            # Gather this step's inputs: from upstream first, then whatever the
            # caller supplied for the ports nothing feeds.
            values: dict[str, Any] = {}
            for edge in feeds.get(stage, []):
                source = by_stage.get(edge.source)
                if source is None:
                    continue
                port = edge.from_port or (source.outputs[0][0]
                                          if source.outputs else "out")
                target = edge.to_port or (step.inputs[0][0]
                                          if step.inputs else "in")
                values[target] = result.values.get((edge.source, port))
                consumed.add((edge.source, port))

            for port_name, _type in step.inputs:
                if port_name in values:
                    continue
                if f"{stage}.{port_name}" in supplied:
                    values[port_name] = supplied[f"{stage}.{port_name}"]
                elif stage in supplied and len(step.inputs) == 1:
                    values[port_name] = supplied[stage]

            began = time.monotonic()
            try:
                produced = _call(function, step, values, folder)
            except Exception:                      # noqa: BLE001 - reported
                detail = traceback.format_exc().strip().splitlines()[-1]
                result.steps.append(StepRun(
                    stage, step.candidate, ok=False,
                    seconds=time.monotonic() - began,
                    inputs=tuple(values), error=detail,
                    effects=tuple(step.effects)))
                result.stopped_at = stage
                if strict:
                    break
                continue

            # One output port: whatever you returned is the value, full stop —
            # including a dict. Several ports: you must return a dict keyed by
            # port name, because there is no other way to say which is which.
            #
            # The tempting rule is "a dict is always a port map". It is wrong.
            # A node whose one job is to produce a record returns a dict, and
            # under that rule its keys get read as port names, every port comes
            # back empty, and the error blames the node for producing nothing.
            if len(step.outputs) == 1:
                produced = {step.outputs[0][0]: produced}
            elif not isinstance(produced, Mapping):
                produced = {}

            problems = _types.guard(_as_ports(step), produced, node=step.candidate)
            for name, value in produced.items():
                result.values[(stage, name)] = value

            result.steps.append(StepRun(
                stage, step.candidate, ok=not problems,
                seconds=time.monotonic() - began,
                inputs=tuple(values), outputs=tuple(produced),
                error="; ".join(problems), effects=tuple(step.effects)))

            if problems:
                result.stopped_at = stage
                if strict:
                    break
        if result.stopped_at and strict:
            break

    if folder is not None:
        touched = sorted(
            p for p in folder.rglob("*")
            if p.is_file()
            and before.get(p) != (p.stat().st_size, p.stat().st_mtime_ns))
        result.artifacts = [Artifact.of(str(p)) for p in touched]

    result.seconds = time.monotonic() - started
    result._sinks = {key for key in result.values if key not in consumed}
    return result


def dry_run(plan: Plan, runtime: Runtime,
            inputs: Mapping[str, Any] | None = None, **kwargs) -> Run:
    """Run everything that touches nothing, and stop at the first step that does.

    Useful because the plan already says which steps have effects. You do not
    need a second implementation, a flag on each node, or anyone's cooperation.
    """
    return run(plan, runtime, inputs, allow_effects=False, **kwargs)
