"""Types, plans and boundaries — the three things that make a graph checkable.

Each of these closes a hole where something *looked* fine: a port connected by
coincidence, a route whose meaning could be edited after the fact, and a node
that declared one thing and produced another.
"""
from __future__ import annotations

import pytest

from browsergraph.compile import CompileError, compile_route, diff
from browsergraph.demo import workbench
from browsergraph.manifest import PortSpec
from browsergraph.types import Lattice, guard, runtime_check
from browsergraph.types import check as type_check


@pytest.fixture(scope="module")
def bench():
    return workbench()


# --- the type lattice -------------------------------------------------------

def test_widening_is_safe_and_narrowing_is_not():
    lattice = Lattice.with_builtins().declare("CsvRecords", "Records")
    assert lattice.is_a("CsvRecords", "Records")
    assert not lattice.is_a("Records", "CsvRecords")


def test_a_type_may_have_several_parents():
    """Forcing a single parent would make one of the relations a lie."""
    lattice = Lattice.with_builtins().declare("CsvRecords", "Records", "Tabular")
    assert lattice.is_a("CsvRecords", "Records")
    assert lattice.is_a("CsvRecords", "Tabular")


def test_containers_are_checked_inside_the_brackets():
    """"A list of the wrong thing" is the most common shape error, and string
    equality cannot see it."""
    lattice = Lattice.with_builtins().declare("CsvRecord", "Record")
    assert lattice.is_a("List[CsvRecord]", "List[Record]")
    assert not lattice.is_a("List[int]", "List[Record]")


def test_a_declared_cycle_does_not_hang():
    lattice = Lattice().declare("a", "b").declare("b", "a")
    assert lattice.ancestors("a") == {"a", "b"}


def test_the_same_carrier_with_different_meanings_is_refused():
    """The `semantic` field existed and nothing read it, which is worse than
    not having it: it looked like the problem was handled."""
    problem = type_check(PortSpec("o", "text/plain", semantic="postal-address"),
                         PortSpec("i", "text/plain", semantic="summary"))
    assert problem and "coincidence" in problem.fix


def test_units_are_checked_because_seconds_are_not_milliseconds():
    problem = type_check(PortSpec("o", "float", units="s"),
                         PortSpec("i", "float", units="ms"))
    assert problem and "factor" in problem.fix


def test_a_matching_port_passes():
    assert type_check(PortSpec("o", "Records"), PortSpec("i", "Records")) is None


def test_every_rejection_says_what_to_do_instead():
    problem = type_check(PortSpec("o", "A"), PortSpec("i", "B"))
    assert problem.fix, "a rejection with no fix is a dead end"


# --- the compiled plan ------------------------------------------------------

def test_the_same_route_compiles_to_the_same_hash(bench):
    route = next(s for s in bench.solutions if s.id == "cheapest").route
    assert compile_route(bench, route).digest == compile_route(bench, route).digest


def test_a_different_binding_is_a_different_plan(bench):
    """Two workbenches differing in a bound parameter must not share evidence,
    however similar they look."""
    route = dict(next(s for s in bench.solutions if s.id == "cheapest").route)
    first = compile_route(bench, route)
    stage = next(s for s in bench.leaf_stages if s.id == "session")
    route["session"] = next(c for c in stage.candidates if c != route["session"])
    assert compile_route(bench, route).digest != first.digest


def test_a_plan_carries_the_union_of_authority(bench):
    """Per-candidate each permission looks small; the union is what has to be
    granted."""
    route = next(s for s in bench.solutions if s.id == "learned").route
    plan = compile_route(bench, route)
    assert "browser" in plan.permissions and "llm" in plan.permissions


def test_compilation_refuses_before_anything_runs(bench):
    route = dict(next(s for s in bench.solutions if s.id == "cheapest").route)
    route["act"] = "not-a-real-candidate"
    with pytest.raises(CompileError) as caught:
        compile_route(bench, route)
    assert any("not admitted" in p for p in caught.value.problems)


def test_compilation_reports_every_problem_not_the_first(bench):
    route = dict(next(s for s in bench.solutions if s.id == "cheapest").route)
    route["act"] = "nope"
    del route["shape"]
    with pytest.raises(CompileError) as caught:
        compile_route(bench, route)
    assert len(caught.value.problems) >= 2


def test_a_plan_knows_its_own_shape(bench):
    plan = compile_route(bench, next(s for s in bench.solutions).route)
    assert len(plan.steps) == len(bench.leaf_stages)
    assert plan.order[0] == "resolve"
    assert plan.parallel_width == 1, "the demonstration is a chain"


def test_diffing_two_plans_names_what_changed(bench):
    a = compile_route(bench, next(s for s in bench.solutions if s.id == "cheapest").route)
    b = compile_route(bench, next(s for s in bench.solutions if s.id == "learned").route)
    changes = diff(a, b)
    assert changes and any(line.startswith("~ act") for line in changes)
    assert any("now needs" in line for line in changes), \
        "a route that gains an authority should say so"


def test_an_identical_plan_diffs_to_nothing(bench):
    plan = compile_route(bench, next(s for s in bench.solutions).route)
    assert diff(plan, plan) == []


def test_a_receipt_is_keyed_on_the_plan(bench):
    from browsergraph import receipt as rc

    class Result:
        ok = True
        executed: list = []
        spec = None
        context = type("C", (), {"data": {}, "artifacts": [], "log": [],
                                 "error": ""})()

    plan = compile_route(bench, next(s for s in bench.solutions).route)
    got = rc.of_run(Result(), plan=plan)
    assert got.plan == plan.digest
    assert plan.matches(got.plan)


def test_a_receipt_from_a_different_plan_is_detectable(bench):
    from browsergraph import receipt as rc

    class Result:
        ok = True
        executed: list = []
        spec = None
        context = type("C", (), {"data": {}, "artifacts": [], "log": [],
                                 "error": ""})()

    a = compile_route(bench, next(s for s in bench.solutions if s.id == "cheapest").route)
    b = compile_route(bench, next(s for s in bench.solutions if s.id == "learned").route)
    changes = rc.compare(rc.of_run(Result(), plan=a), rc.of_run(Result(), plan=b))
    assert any("a different plan ran" in line for line in changes)


# --- the port boundary ------------------------------------------------------

def test_a_node_that_lies_about_its_output_is_caught_at_its_own_port():
    """Attributed to the producer. Without this the failure surfaces three
    steps later and the traceback names the victim."""
    ports = (PortSpec("records", "Records"),)
    problems = guard(ports, {"records": "not a list"}, node="parse")
    assert problems and "parse produced str" in problems[0]


def test_a_missing_required_output_is_caught():
    assert guard((PortSpec("records", "Records"),), {}, node="parse")


def test_an_optional_port_may_be_absent():
    assert guard((PortSpec("note", "str", required=False),), {}, node="x") == []


def test_a_bool_is_not_an_int_here():
    """True == 1 in Python, and that is exactly how a flag ends up in a count."""
    assert runtime_check(True, PortSpec("count", "int"), node="n")


def test_an_unknown_type_is_unchecked_rather_than_rejected():
    """A validator that rejects what it does not understand gets turned off."""
    assert runtime_check(object(), PortSpec("x", "SomeDomainType")) == ""


def test_the_checker_stays_shallow_on_purpose():
    """A validator that doubles the cost of a pipeline gets turned off, and one
    that is turned off catches nothing."""
    big = list(range(200_000))
    assert runtime_check(big, PortSpec("records", "List[Record]")) == ""


# --- the candidate has to fit the stage it was put in ------------------------

def test_a_candidate_that_cannot_satisfy_its_stage_is_refused():
    """`validate()` caught this and `compile_route` did not, so a plan with a
    content hash was obtainable for a graph the validator rejected.

    Discovery normally makes it impossible — `eligible()` admits nothing
    incompatible — but a hand-written workbench, a JSON document, or a model
    appending an id to a candidate list all bypass discovery. A digest that can
    certify an invalid plan is worse than no digest.
    """
    from dataclasses import replace

    from browsergraph import templates
    from browsergraph.manifest import NodeManifest
    from browsergraph.workbench import NodeCandidate

    def probe(node_id, capability, ins, outs):
        return NodeManifest(
            id=node_id, kind="probe", description=f"Probe for {capability}.",
            capabilities=(capability,),
            inputs=tuple(PortSpec(n, t) for n, t in ins),
            outputs=tuple(PortSpec(n, t) for n, t in outs))

    template = templates.get("data.quality")
    nodes = [probe(f"probe.{s.id}", s.capabilities[0], s.inputs, s.outputs)
             for s in template.slots]
    flat = replace(
        template.instantiate({s.id: [f"probe.{s.id}"] for s in template.slots}),
        nodes=tuple(nodes))
    route = {s.id: f"probe.{s.id}" for s in template.slots}
    assert compile_route(flat, route).digest.startswith("plan:")

    wrong = probe("probe.wrong", "check.schema",
                  [("in", "Profile")], [("out", "NothingLikeIt")])
    sabotaged = replace(
        flat,
        nodes=flat.nodes + (wrong,),
        candidates=flat.candidates + (NodeCandidate(id="probe.wrong",
                                                    node_id="probe.wrong"),),
        stages=tuple(replace(s, candidates=s.candidates + ("probe.wrong",))
                     if s.id == "schema" else s for s in flat.stages))

    # The validator already objected. The compiler must not disagree with it.
    assert any("probe.wrong" in problem for problem in sabotaged.validate())

    with pytest.raises(CompileError) as caught:
        compile_route(sabotaged, {**route, "schema": "probe.wrong"})
    assert any("does not produce" in p for p in caught.value.problems)
    assert any("NothingLikeIt" in p for p in caught.value.problems)


def test_the_compiler_is_at_least_as_strict_as_the_validator(bench):
    """Anything `validate()` rejects must not yield a plan. The two were out of
    step in exactly one direction, which is the dangerous one."""
    route = {s.id: s.candidates[0] for s in bench.leaf_stages if s.candidates}
    assert bench.validate() == []
    assert compile_route(bench, route).digest.startswith("plan:")
