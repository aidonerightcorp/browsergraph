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

import hashlib
import inspect
import json
import pathlib
import time
import traceback
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
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
    #: Served from the cache rather than run.
    cached: bool = False
    #: An upstream branch went the other way, so this never had to run.
    skipped: bool = False
    #: The chosen candidate failed and one of its fallbacks did the work.
    fell_back: bool = False

    def to_dict(self) -> dict:
        out = {"stage": self.stage, "candidate": self.candidate,
               "ok": self.ok, "seconds": round(self.seconds, 4),
               "inputs": list(self.inputs), "outputs": list(self.outputs)}
        for flag in ("cached", "skipped", "fell_back"):
            if getattr(self, flag):
                out[flag] = True
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
            mark = ("skip" if step.skipped else "ok  " if step.ok else "FAIL")
            effects = f"  [{', '.join(step.effects)}]" if step.effects else ""
            note = (" (cached)" if step.cached else
                    " (fell back)" if step.fell_back else "")
            lines.append(f"  {mark} {step.stage:<14} {step.seconds:>7.3f}s  "
                         f"{step.candidate}{note}{effects}")
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

    def receipt(self, task: str = "", graph: str = ""):
        """Durable evidence for this run, keyed on the plan that produced it.

        `receipt.py` has existed all along and nothing here was writing one, so
        a run left no trace once the notebook closed. The plan digest is the
        part that matters: without it, "we learned this route is good" is a
        statement about a name, and the thing behind the name can change with
        nothing noticing.
        """
        from browsergraph.receipt import StepRecord, TaskReceipt

        return TaskReceipt(
            task=task, graph=graph, ok=self.ok, plan=self.plan_digest,
            seconds=self.seconds,
            route=tuple(f"{s.stage}={s.candidate}" for s in self.steps),
            # `key` holds the **candidate**, not the stage. `Evidence.from_receipt`
            # reads `step.key` as the thing being learned about, and a receipt
            # keyed by stage teaches that "schema" succeeded — which every
            # candidate in that stage then shares, so nothing can ever be
            # preferred over anything else. The stage goes in `kind`, where it
            # is still available for grouping and display.
            steps=tuple(StepRecord(key=s.candidate, kind=s.stage,
                                   name=s.candidate, ok=s.ok,
                                   seconds=s.seconds, error=s.error,
                                   mutates=bool(s.effects))
                        for s in self.steps),
            artifacts=tuple(self.artifacts),
            error=next((s.error for s in self.steps if not s.ok), ""),
            notes={"stopped_at": self.stopped_at,
                   "cached": sum(1 for s in self.steps if s.cached),
                   "skipped": sum(1 for s in self.steps if s.skipped),
                   "fell_back": [s.stage for s in self.steps if s.fell_back]})


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


#: A port a branch did not choose. Not `None`, because `None` is a value a node
#: might legitimately produce and we would then skip a step that should run.
class _NotTaken:
    def __repr__(self) -> str:
        return "<not taken>"


NOT_TAKEN = _NotTaken()


def _cache_key(plan_digest: str, step: Step, values: Mapping[str, Any]) -> str | None:
    """A key for this exact step on these exact inputs, or None if unsafe.

    Only deterministic steps with no effects are cacheable. Caching a step that
    reaches the network would serve a stale page; caching a non-deterministic
    one would hide the very variation you kept it for. Inputs that will not
    serialise mean no key at all — better to recompute than to collide.
    """
    if not step.deterministic or step.effects:
        return None
    try:
        blob = json.dumps({k: v for k, v in sorted(values.items())},
                          sort_keys=True, default=repr)
    except (TypeError, ValueError):
        return None
    payload = f"{plan_digest}\x00{step.stage}\x00{step.candidate}\x00{blob}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _shape(step: Step, produced: Any) -> dict:
    """Whatever the node returned, as a port-name to value mapping."""
    if len(step.outputs) == 1:
        return {step.outputs[0][0]: produced}
    return dict(produced) if isinstance(produced, Mapping) else {}


def _run_one(step: Step, function: Callable, values: Mapping[str, Any],
             folder: pathlib.Path | None) -> dict:
    """Run one step and give back its ports. Raises if the node raises."""
    if step.kind == "map":
        # One input port, holding a collection. Run the node per item.
        port = step.inputs[0][0] if step.inputs else "in"
        items = values.get(port)
        if items is None or isinstance(items, (str, bytes)) or not hasattr(items, "__iter__"):
            raise TypeError(
                f"a map step needs a collection on port {port!r}, got "
                f"{type(items).__name__}")
        collected: list[Any] = []
        for index, item in enumerate(items):
            try:
                collected.append(_call(function, step, {**values, port: item}, folder))
            except Exception as problem:
                raise RuntimeError(f"item {index}: {problem}") from problem
        return _shape(step, collected)

    produced = _call(function, step, values, folder)

    if step.kind == "branch":
        # The node names the port it chose. Everything else is explicitly not
        # taken, so the steps behind those ports are skipped rather than run
        # with an empty value.
        chosen: dict[str, Any] = {}
        if isinstance(produced, tuple) and len(produced) == 2:
            chosen = {produced[0]: produced[1]}
        elif isinstance(produced, Mapping):
            chosen = {k: v for k, v in produced.items() if v is not None}
        if len(chosen) != 1:
            raise ValueError(
                f"a branch step must name exactly one output port, got "
                f"{sorted(chosen) or 'none'}. Return (port_name, value).")
        taken = next(iter(chosen))
        if taken not in {name for name, _ in step.outputs}:
            raise ValueError(f"branch chose {taken!r}, which is not one of its "
                             f"ports {[n for n, _ in step.outputs]}")
        return {name: (chosen[name] if name == taken else NOT_TAKEN)
                for name, _ in step.outputs}

    return _shape(step, produced)


def run(plan: Plan, runtime: Runtime, inputs: Mapping[str, Any] | None = None,
        *, workspace: str | pathlib.Path | None = None,
        strict: bool = True, allow_effects: bool = True,
        workers: int = 1, fallbacks: Mapping[str, Sequence[str]] | None = None,
        cache: MutableMapping[str, Any] | None = None) -> Run:
    """Run every step of the plan in layer order.

    `inputs` feeds the steps that have no upstream edge. Key it by stage id, or
    by `stage.port` when a stage takes more than one thing.

    `strict` stops at the first failure. That is the default because carrying on
    after a step failed produces a result that looks complete and is not.

    `allow_effects=False` refuses to run any step that declared an effect. The
    plan already knows which those are, so a dry run needs no separate code
    path and no cooperation from the node.

    `workers` runs the steps within one layer at the same time. They share a
    layer because nothing connects them, so this is safe by construction rather
    than by hope — the graph worked out which steps are independent, and this
    is what that was for.

    `fallbacks` maps a stage to other candidates to try if the chosen one
    fails. A route already carries these; passing them here is how they finally
    get used.

    `cache` skips a step that has already run on these inputs. Only steps that
    are deterministic and effect-free are ever cached.
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
    fallbacks = dict(fallbacks or {})

    feeds: dict[str, list] = {}
    for edge in plan.edges:
        feeds.setdefault(edge.target, []).append(edge)

    by_stage = {s.stage: s for s in plan.steps}

    def gather(stage: str, step: Step) -> dict[str, Any]:
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
        return values

    def attempt(stage: str, step: Step) -> tuple[StepRun, dict]:
        """Run one step, trying its fallbacks, and return its row and ports."""
        values = gather(stage, step)

        # A branch upstream decided this path is not taken. Skipping is the
        # correct outcome, not a failure — so it is recorded as a skip.
        if any(value is NOT_TAKEN for value in values.values()):
            return StepRun(stage, step.candidate, ok=True, skipped=True,
                           inputs=tuple(values),
                           error="not taken — an upstream branch went the other way"), {}

        if step.effects and not allow_effects:
            return StepRun(stage, step.candidate, ok=False,
                           effects=tuple(step.effects),
                           error=f"refused: this step declares "
                                 f"{', '.join(step.effects)} and effects are off"), {}

        key = _cache_key(plan.digest, step, values) if cache is not None else None
        if key is not None and key in cache:
            produced = cache[key]
            return StepRun(stage, step.candidate, ok=True, cached=True,
                           inputs=tuple(values), outputs=tuple(produced),
                           effects=tuple(step.effects)), produced

        tried: list[str] = []
        for candidate in (step.candidate, *fallbacks.get(stage, ())):
            function = runtime.get(candidate)
            if function is None:
                tried.append(f"{candidate}: no function registered")
                continue
            began = time.monotonic()
            try:
                produced = _run_one(replace(step, candidate=candidate),
                                    function, values, folder)
            except Exception:                      # noqa: BLE001 - reported
                detail = traceback.format_exc().strip().splitlines()[-1]
                tried.append(f"{candidate}: {detail}")
                continue

            # Check only the ports that were actually taken. Filtering the
            # not-taken values out of the *values* but still checking their
            # ports makes the guard demand a value the branch deliberately did
            # not produce, which fails every branch that works.
            taken_ports = tuple(port for port in _as_ports(step)
                                if produced.get(port.name, None) is not NOT_TAKEN)
            problems = _types.guard(taken_ports,
                                    {k: v for k, v in produced.items()
                                     if v is not NOT_TAKEN},
                                    node=candidate)
            if problems:
                tried.append(f"{candidate}: {'; '.join(problems)}")
                continue

            if key is not None and cache is not None:
                cache[key] = produced
            return StepRun(stage, candidate, ok=True,
                           seconds=time.monotonic() - began,
                           inputs=tuple(values), outputs=tuple(produced),
                           effects=tuple(step.effects),
                           fell_back=candidate != step.candidate), produced

        # When nothing was even callable, say the useful thing rather than
        # listing the same complaint once per candidate.
        if tried and all("no function registered" in t for t in tried):
            detail = (f"no function registered for {step.candidate!r}. "
                      f"Add one with runtime.register(...).")
        else:
            detail = "; ".join(tried)
        return StepRun(stage, step.candidate, ok=False, inputs=tuple(values),
                       effects=tuple(step.effects), error=detail), {}

    for layer in plan.layers:
        runnable = [(stage, by_stage[stage]) for stage in layer if stage in by_stage]
        if not runnable:
            continue

        if workers > 1 and len(runnable) > 1:
            # Inputs are gathered inside each task, and nothing in a layer feeds
            # anything else in the same layer, so there is nothing to race on.
            with ThreadPoolExecutor(max_workers=workers) as pool:
                rows = list(pool.map(lambda pair: attempt(*pair), runnable))
        else:
            rows = [attempt(stage, step) for stage, step in runnable]

        for (stage, _step), (row, produced) in zip(runnable, rows, strict=True):
            for name, value in produced.items():
                result.values[(stage, name)] = value
            result.steps.append(row)
            if not row.ok and not result.stopped_at:
                result.stopped_at = stage
        if result.stopped_at and strict:
            break

    if folder is not None:
        touched = sorted(
            p for p in folder.rglob("*")
            if p.is_file()
            and before.get(p) != (p.stat().st_size, p.stat().st_mtime_ns))
        result.artifacts = [Artifact.of(str(p)) for p in touched]

    result.seconds = time.monotonic() - started
    result._sinks = {key for key in result.values
                     if key not in consumed
                     and result.values[key] is not NOT_TAKEN}
    return result


def dry_run(plan: Plan, runtime: Runtime,
            inputs: Mapping[str, Any] | None = None, **kwargs) -> Run:
    """Run everything that touches nothing, and stop at the first step that does.

    Useful because the plan already says which steps have effects. You do not
    need a second implementation, a flag on each node, or anyone's cooperation.
    """
    return run(plan, runtime, inputs, allow_effects=False, **kwargs)
