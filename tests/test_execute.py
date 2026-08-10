"""Running a plan for real.

The library could describe a job and pick a route but never run one. These
tests cover the part that closes that gap: values move along the edges, the
types are checked at each hand-off, effects can be refused, and files written
by a node come back as artifacts.
"""
from __future__ import annotations

import time
from dataclasses import replace

import pytest

from browsergraph import execute
from browsergraph import templates as T
from browsergraph.compile import compile_route
from browsergraph.manifest import NodeManifest, PortSpec


def _probe(slot, **extra):
    return NodeManifest(
        id=f"p.{slot.id}", kind="probe", description=f"Probe for {slot.id}.",
        capabilities=(slot.capabilities[0] if slot.capabilities else "probe",),
        inputs=tuple(PortSpec(n, t) for n, t in slot.inputs),
        outputs=tuple(PortSpec(n, t) for n, t in slot.outputs), **extra)


@pytest.fixture
def diamond():
    """Fan-out and join: profile -> (schema, distribution) -> adjudicate."""
    template = T.get("data.quality")
    nodes = tuple(_probe(s) for s in template.slots)
    bench = replace(template.instantiate(
        {s.id: [f"p.{s.id}"] for s in template.slots}), nodes=nodes)
    return compile_route(bench, {s.id: f"p.{s.id}" for s in template.slots})


def _working() -> execute.Runtime:
    return execute.Runtime({
        "p.profile": lambda: {"rows": 100},
        "p.schema": lambda **kw: ["age changed type"],
        "p.distribution": lambda **kw: ["psi 0.31"],
        "p.adjudicate": lambda schema, distribution: {
            "verdict": "block", "why": schema + distribution},
    })


# --- the basics --------------------------------------------------------------

def test_a_plan_runs_and_reports_every_step(diamond):
    got = execute.run(diamond, _working(), {"profile": None})
    assert got.ok
    assert [s.stage for s in got.steps] == ["profile", "schema",
                                            "distribution", "adjudicate"]
    assert all(s.ok for s in got.steps)


def test_a_join_receives_both_inputs_on_the_right_ports(diamond):
    """The thing a chain cannot do. Both branches arrive, each on its own port."""
    got = execute.run(diamond, _working(), {"profile": None})
    assert got.output("adjudicate")["why"] == ["age changed type", "psi 0.31"]


def test_the_final_values_are_the_ones_nothing_consumed(diamond):
    got = execute.run(diamond, _working(), {"profile": None})
    assert list(got.final()) == ["adjudicate.out"]


def test_steps_run_in_layer_order(diamond):
    order = []
    runtime = execute.Runtime({
        "p.profile": lambda: order.append("profile") or {"rows": 1},
        "p.schema": lambda **kw: order.append("schema") or [],
        "p.distribution": lambda **kw: order.append("distribution") or [],
        "p.adjudicate": lambda **kw: order.append("adjudicate") or "ok",
    })
    execute.run(diamond, runtime, {"profile": None})
    assert order.index("profile") < order.index("schema")
    assert order.index("schema") < order.index("adjudicate")
    assert order.index("distribution") < order.index("adjudicate")


# --- the check that earns its keep -------------------------------------------

def test_a_node_that_returns_nothing_is_caught_where_it_happened(diamond):
    """Without this the None travels on and something unrelated falls over."""
    runtime = _working().register("p.schema", lambda **kw: None)
    got = execute.run(diamond, runtime, {"profile": None})
    assert not got.ok
    assert got.stopped_at == "schema"
    # Steps in one layer all run, so the failure is not necessarily last.
    failed = next(s for s in got.steps if s.stage == "schema")
    assert "p.schema" in failed.error
    assert "Findings" in failed.error


def test_a_missing_function_says_what_to_do(diamond):
    runtime = _working()
    runtime._functions.pop("p.adjudicate")
    got = execute.run(diamond, runtime, {"profile": None})
    assert not got.ok
    failed = next(s for s in got.steps if s.stage == "adjudicate")
    assert "runtime.register" in failed.error


def test_missing_lists_every_gap_before_you_start(diamond):
    assert execute.Runtime().missing(diamond) == [
        "p.profile", "p.schema", "p.distribution", "p.adjudicate"]
    assert _working().missing(diamond) == []


def test_a_raising_node_records_the_error_and_stops(diamond):
    def boom(**kw):
        raise ValueError("upstream is empty")

    got = execute.run(diamond, _working().register("p.distribution", boom),
                      {"profile": None})
    assert not got.ok and got.stopped_at == "distribution"
    assert "upstream is empty" in got.steps[-1].error


def test_carrying_on_after_a_failure_is_opt_in(diamond):
    """A run that continues past a failure looks complete and is not."""
    runtime = _working().register("p.schema", lambda **kw: None)
    lenient = execute.run(diamond, runtime, {"profile": None}, strict=False)
    assert len(lenient.steps) > 2
    assert not lenient.ok


# --- one output port versus several ------------------------------------------

def test_one_output_port_takes_the_return_value_even_when_it_is_a_dict(diamond):
    """The tempting rule is 'a dict is always a port map'. It is wrong: a node
    whose job is to produce a record returns a dict."""
    got = execute.run(diamond, _working(), {"profile": None})
    assert got.output("profile") == {"rows": 100}


def test_several_output_ports_need_a_dict_keyed_by_port():
    template = T.get("tabular.supervised")
    nodes = tuple(_probe(s) for s in template.slots)
    bench = replace(template.instantiate(
        {s.id: [f"p.{s.id}"] for s in template.slots}), nodes=nodes)
    plan = compile_route(bench, {s.id: f"p.{s.id}" for s in template.slots})

    runtime = execute.Runtime({f"p.{s.id}": (lambda **kw: "x") for s in template.slots})
    runtime.register("p.load", lambda: "frame")
    runtime.register("p.split", lambda **kw: {"train": "T", "valid": "V"})
    got = execute.run(plan, runtime, {"load": None}, strict=False)
    assert got.values[("split", "train")] == "T"
    assert got.values[("split", "valid")] == "V"


# --- effects ------------------------------------------------------------------

def test_a_dry_run_refuses_the_step_that_touches_the_world():
    template = T.get("service.notification")
    nodes = tuple(
        _probe(s, effects=("network.write",)) if s.id == "deliver" else _probe(s)
        for s in template.slots)
    bench = replace(template.instantiate(
        {s.id: [f"p.{s.id}"] for s in template.slots}), nodes=nodes)
    plan = compile_route(bench, {s.id: f"p.{s.id}" for s in template.slots})

    runtime = execute.Runtime({f"p.{s.id}": (lambda **kw: "value")
                               for s in template.slots})
    got = execute.dry_run(plan, runtime, {"receive": None})

    assert not got.ok
    assert got.stopped_at == "deliver"
    assert "effects are off" in got.steps[-1].error
    # Everything before it still ran, which is what makes a dry run useful.
    assert [s.stage for s in got.steps if s.ok][:2] == ["receive", "authenticate"]


def test_the_same_plan_runs_fully_when_effects_are_allowed():
    template = T.get("service.notification")
    nodes = tuple(
        _probe(s, effects=("network.write",)) if s.id == "deliver" else _probe(s)
        for s in template.slots)
    bench = replace(template.instantiate(
        {s.id: [f"p.{s.id}"] for s in template.slots}), nodes=nodes)
    plan = compile_route(bench, {s.id: f"p.{s.id}" for s in template.slots})
    runtime = execute.Runtime({f"p.{s.id}": (lambda **kw: "value")
                               for s in template.slots})
    assert execute.run(plan, runtime, {"receive": None}).ok


# --- artifacts ----------------------------------------------------------------

def test_files_a_node_writes_come_back_as_artifacts(diamond, tmp_path):
    def writer(workspace, **kw):
        (workspace / "findings.txt").write_text("age changed type")
        return ["age changed type"]

    got = execute.run(diamond, _working().register("p.schema", writer),
                      {"profile": None}, workspace=tmp_path)
    assert [a.path.split("/")[-1] for a in got.artifacts] == ["findings.txt"]
    assert got.artifacts[0].bytes == len("age changed type")
    assert got.artifacts[0].digest


def test_a_file_that_was_already_there_and_untouched_is_not_claimed(diamond, tmp_path):
    (tmp_path / "old.txt").write_text("before")
    got = execute.run(diamond, _working(), {"profile": None}, workspace=tmp_path)
    assert got.artifacts == []


def test_a_file_that_is_rewritten_is_still_reported(diamond, tmp_path):
    """"New files only" looks right the first time, then reports nothing on
    every run after that — exactly when you are re-running to check."""
    def writer(workspace, **kw):
        (workspace / "findings.txt").write_text("age changed type")
        return ["age changed type"]

    runtime = _working().register("p.schema", writer)
    execute.run(diamond, runtime, {"profile": None}, workspace=tmp_path)
    again = execute.run(diamond, runtime, {"profile": None}, workspace=tmp_path)
    assert [a.path.split("/")[-1] for a in again.artifacts] == ["findings.txt"]


def test_a_node_only_gets_the_arguments_it_asked_for(diamond):
    """Nodes stay simple: no **kwargs required just to be callable."""
    seen = {}

    def picky(schema):                    # ignores `distribution` entirely
        seen["got"] = schema
        return "verdict"

    got = execute.run(diamond, _working().register("p.adjudicate", picky),
                      {"profile": None})
    assert got.ok and seen["got"] == ["age changed type"]


def test_a_node_can_ask_for_its_own_step_and_parameters(diamond):
    seen = {}

    def curious(step, params, **kw):
        seen["stage"] = step.stage
        seen["params"] = params
        return ["ok"]

    execute.run(diamond, _working().register("p.schema", curious),
                {"profile": None})
    assert seen["stage"] == "schema"
    assert seen["params"] == {}


def test_the_run_summary_reads_like_a_report(diamond):
    text = execute.run(diamond, _working(), {"profile": None}).text()
    assert "4 steps" in text and "ok" in text
    assert "adjudicate" in text


# --- map: do it to every item -------------------------------------------------

def _map_bench():
    """load -> process(map) -> count."""
    from browsergraph.workbench import (
        Edge,
        NodeCandidate,
        StageDefinition,
        WorkbenchDefinition,
    )

    def n(nid, cap, takes, gives, **kw):
        return NodeManifest(id=nid, kind="fn", description=f"{cap}",
                            capabilities=(cap,),
                            inputs=tuple(PortSpec(a, b) for a, b in takes),
                            outputs=tuple(PortSpec(a, b) for a, b in gives), **kw)

    nodes = [n("load.list", "read", [], [("out", "List[Item]")]),
             n("upper.one", "process", [("in", "Item")], [("out", "Item")]),
             n("count.all", "count", [("in", "List[Item]")], [("out", "Number")])]
    stages = (
        StageDefinition(id="load", output_type="List[Item]",
                        required_capabilities=("read",), candidates=("load.list",)),
        StageDefinition(id="process", kind="map", input_type="List[Item]",
                        output_type="List[Item]", required_capabilities=("process",),
                        candidates=("upper.one",)),
        StageDefinition(id="count", input_type="List[Item]", output_type="Number",
                        required_capabilities=("count",), candidates=("count.all",)))
    bench = WorkbenchDefinition(
        title="map", stages=stages, nodes=tuple(nodes),
        edges=(Edge("load", "process"), Edge("process", "count")),
        candidates=tuple(NodeCandidate(id=x.id, node_id=x.id) for x in nodes))
    return compile_route(bench, {"load": "load.list", "process": "upper.one",
                                 "count": "count.all"})


def test_a_map_step_runs_once_per_item():
    """The thing a workbench could not say at all: do it to all of them."""
    plan = _map_bench()
    seen = []

    def upper(**kw):
        seen.append(kw["in"])
        return kw["in"].upper()

    runtime = execute.Runtime({
        "load.list": lambda: ["a", "b", "c"],
        "upper.one": upper,
        "count.all": lambda **kw: len(kw["in"]),
    })
    got = execute.run(plan, runtime)
    assert got.ok
    assert seen == ["a", "b", "c"]
    assert got.output("process") == ["A", "B", "C"]
    assert got.output("count") == 3


def test_a_map_step_names_the_item_that_broke():
    """"Something in the batch failed" is not actionable. The index is."""
    plan = _map_bench()
    runtime = execute.Runtime({
        "load.list": lambda: ["a", "b", "c"],
        "upper.one": lambda **kw: kw["in"].upper() if kw["in"] != "b" else 1 / 0,
        "count.all": lambda **kw: len(kw["in"]),
    })
    got = execute.run(plan, runtime)
    assert not got.ok
    assert "item 1" in next(s for s in got.steps if s.stage == "process").error


def test_a_map_step_over_something_that_is_not_a_collection_says_so():
    """Tested on the step directly. Run through a whole plan the output guard
    catches a scalar first, earlier and with a better message — which is the
    right order, and leaves this rule untested unless it is checked here."""
    from browsergraph.compile import Step

    step = Step(stage="process", candidate="upper.one", node="upper.one",
                kind="map", inputs=(("in", "List[Item]"),),
                outputs=(("out", "List[Item]"),))
    with pytest.raises(TypeError) as caught:
        execute._run_one(step, lambda **kw: kw["in"], {"in": 5}, None)
    assert "needs a collection" in str(caught.value)


def test_a_map_step_does_not_walk_through_a_string():
    """A string is iterable, and mapping over one silently processes it letter
    by letter — which looks like it worked."""
    from browsergraph.compile import Step

    step = Step(stage="process", candidate="upper.one", node="upper.one",
                kind="map", inputs=(("in", "List[Item]"),),
                outputs=(("out", "List[Item]"),))
    with pytest.raises(TypeError):
        execute._run_one(step, lambda **kw: kw["in"], {"in": "abc"}, None)


def test_a_map_plan_is_a_different_computation_from_an_atomic_one():
    """Same nodes, same order — but mapping is not running once, so the digest
    must differ or evidence from the two would be pooled."""
    from dataclasses import replace as dc_replace
    mapped = _map_bench()
    atomic = dc_replace(mapped, steps=tuple(
        dc_replace(s, kind="atomic") if s.stage == "process" else s
        for s in mapped.steps))
    assert mapped.digest != atomic.digest


# --- branch: take one path ----------------------------------------------------

def _branch_bench():
    """check(branch) -> {small, large} -> report."""
    from browsergraph.workbench import (
        Edge,
        NodeCandidate,
        StageDefinition,
        WorkbenchDefinition,
    )

    def n(nid, cap, takes, gives):
        return NodeManifest(id=nid, kind="fn", description=cap,
                            capabilities=(cap,),
                            inputs=tuple(PortSpec(a, b) for a, b in takes),
                            outputs=tuple(PortSpec(a, b) for a, b in gives))

    nodes = [n("check.size", "decide", [("in", "Number")],
               [("small", "Number"), ("large", "Number")]),
             n("handle.small", "small", [("in", "Number")], [("out", "Text")]),
             n("handle.large", "large", [("in", "Number")], [("out", "Text")])]
    stages = (
        StageDefinition(id="check", kind="branch", required_capabilities=("decide",),
                        inputs=(PortSpec("in", "Number"),),
                        outputs=(PortSpec("small", "Number"),
                                 PortSpec("large", "Number")),
                        candidates=("check.size",)),
        StageDefinition(id="small", input_type="Number", output_type="Text",
                        required_capabilities=("small",), candidates=("handle.small",)),
        StageDefinition(id="large", input_type="Number", output_type="Text",
                        required_capabilities=("large",), candidates=("handle.large",)))
    bench = WorkbenchDefinition(
        title="branch", stages=stages, nodes=tuple(nodes),
        edges=(Edge("check", "small", from_port="small"),
               Edge("check", "large", from_port="large")),
        candidates=tuple(NodeCandidate(id=x.id, node_id=x.id) for x in nodes))
    return compile_route(bench, {"check": "check.size", "small": "handle.small",
                                 "large": "handle.large"})


def test_a_branch_runs_only_the_path_it_chose():
    plan = _branch_bench()
    ran = []
    runtime = execute.Runtime({
        "check.size": lambda **kw: ("large", kw["in"]) if kw["in"] > 10 else ("small", kw["in"]),
        "handle.small": lambda **kw: ran.append("small") or "it was small",
        "handle.large": lambda **kw: ran.append("large") or "it was large",
    })
    got = execute.run(plan, runtime, {"check": 99})
    assert got.ok
    assert ran == ["large"]
    assert got.output("large") == "it was large"
    assert next(s for s in got.steps if s.stage == "small").skipped


def test_the_other_path_is_skipped_not_failed():
    """A path not taken is a correct outcome. Recording it as a failure would
    make every branching run look broken."""
    plan = _branch_bench()
    runtime = execute.Runtime({
        "check.size": lambda **kw: ("small", kw["in"]),
        "handle.small": lambda **kw: "small",
        "handle.large": lambda **kw: "large",
    })
    got = execute.run(plan, runtime, {"check": 1})
    skipped = next(s for s in got.steps if s.stage == "large")
    assert skipped.skipped and skipped.ok
    assert "not taken" in skipped.error


def test_a_branch_that_names_no_port_or_two_is_refused():
    plan = _branch_bench()
    runtime = execute.Runtime({
        "check.size": lambda **kw: {"small": 1, "large": 2},
        "handle.small": lambda **kw: "s", "handle.large": lambda **kw: "l"})
    got = execute.run(plan, runtime, {"check": 1})
    assert not got.ok
    assert "exactly one output port" in next(
        s for s in got.steps if s.stage == "check").error


# --- parallel, fallbacks, cache, receipt --------------------------------------

def test_independent_steps_can_run_at_the_same_time(diamond):
    """They share a layer because nothing connects them, so this is safe by
    construction rather than by hope."""
    import threading
    live, peak = [], []
    lock = threading.Lock()

    def slow(**kw):
        with lock:
            live.append(1); peak.append(len(live))
        time.sleep(0.05)
        with lock:
            live.pop()
        return ["finding"]

    runtime = _working().register("p.schema", slow).register("p.distribution", slow)
    got = execute.run(diamond, runtime, {"profile": None}, workers=4)
    assert got.ok
    assert max(peak) == 2, "the two independent steps did not overlap"


def test_a_fallback_takes_over_when_the_chosen_candidate_fails(diamond):
    """Routes have carried fallbacks all along and nothing ever used them."""
    def broken(**kw):
        raise RuntimeError("the good one is down")

    runtime = _working().register("p.schema", broken)
    runtime.register("p.schema.backup", lambda **kw: ["from the backup"])

    got = execute.run(diamond, runtime, {"profile": None},
                      fallbacks={"schema": ["p.schema.backup"]})
    assert got.ok
    row = next(s for s in got.steps if s.stage == "schema")
    assert row.fell_back and row.candidate == "p.schema.backup"
    assert got.output("schema") == ["from the backup"]


def test_a_run_says_when_every_fallback_also_failed(diamond):
    def broken(**kw):
        raise RuntimeError("down")

    runtime = _working().register("p.schema", broken).register("p.schema.backup", broken)
    got = execute.run(diamond, runtime, {"profile": None},
                      fallbacks={"schema": ["p.schema.backup"]})
    assert not got.ok
    error = next(s for s in got.steps if s.stage == "schema").error
    assert "p.schema:" in error and "p.schema.backup:" in error


def test_a_cached_step_is_not_run_twice(diamond):
    calls = []
    runtime = _working().register(
        "p.schema", lambda **kw: calls.append(1) or ["finding"])
    cache: dict = {}

    first = execute.run(diamond, runtime, {"profile": None}, cache=cache)
    second = execute.run(diamond, runtime, {"profile": None}, cache=cache)

    assert first.ok and second.ok
    assert len(calls) == 1, "the second run recomputed a cached step"
    assert next(s for s in second.steps if s.stage == "schema").cached


def test_a_step_with_effects_is_never_cached():
    """Caching a step that touches the world would serve a stale answer."""
    from browsergraph.compile import Step
    step = Step(stage="s", candidate="c", node="n", effects=("network.write",))
    assert execute._cache_key("plan:x", step, {"in": 1}) is None


def test_a_non_deterministic_step_is_never_cached():
    """Caching it would hide the variation you kept it for."""
    from browsergraph.compile import Step
    step = Step(stage="s", candidate="c", node="n", deterministic=False)
    assert execute._cache_key("plan:x", step, {"in": 1}) is None


def test_a_run_can_produce_a_receipt(diamond):
    got = execute.run(diamond, _working(), {"profile": None})
    receipt = got.receipt(task="quality gate")
    assert receipt.plan == got.plan_digest
    assert receipt.ok
    assert len(receipt.steps) == len(got.steps)
    assert "quality gate" == receipt.task
    assert receipt.to_json().startswith("{")
