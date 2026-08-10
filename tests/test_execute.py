"""Running a plan for real.

The library could describe a job and pick a route but never run one. These
tests cover the part that closes that gap: values move along the edges, the
types are checked at each hand-off, effects can be refused, and files written
by a node come back as artifacts.
"""
from __future__ import annotations

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
    assert "p.schema" in got.steps[-1].error
    assert "Findings" in got.steps[-1].error


def test_a_missing_function_says_what_to_do(diamond):
    runtime = _working()
    runtime._functions.pop("p.adjudicate")
    got = execute.run(diamond, runtime, {"profile": None})
    assert not got.ok
    assert "runtime.register" in got.steps[-1].error


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
