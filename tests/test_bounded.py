"""A clock and a ceiling on one step.

Lifecycle isolation, not a sandbox — the tests say the same, because a test
named `test_sandbox_blocks_x` is how somebody later concludes it is one.
"""
from __future__ import annotations

import pytest

from browsergraph.bounded import LimitExceeded, Limits, available, bound

pytestmark = pytest.mark.skipif(not available(),
                                reason="bounded execution needs POSIX rlimits")


# Module level on purpose: a bounded step is pickled to its child, so a
# closure or a lambda cannot cross. That constraint is the subject of a test
# below rather than a footnote.
def _quick(**kw):
    return {"value": 42}


def _slow(**kw):
    import time
    time.sleep(30)
    return {"value": "never"}


def _greedy(**kw):
    blob = bytearray(8 * 1024 * 1024 * 1024)
    return len(blob)


def _wrong(**kw):
    raise ValueError("this step is simply wrong")


def _echo(**kw):
    return dict(kw)


def test_a_normal_step_returns_its_value():
    assert bound(_quick, Limits(seconds=20, memory_mb=512))() == {"value": 42}


def test_arguments_reach_the_child_and_the_result_comes_back():
    got = bound(_echo, Limits(seconds=20, memory_mb=512))(a=1, b="two")
    assert got == {"a": 1, "b": "two"}


def test_a_step_that_hangs_is_stopped_at_the_clock():
    with pytest.raises(LimitExceeded, match="longer than"):
        bound(_slow, Limits(seconds=1.0, memory_mb=512))()


def test_a_step_that_allocates_without_end_hits_the_ceiling():
    """The reason this module exists: a search asked to enumerate 3.8 trillion
    routes took a 61GB box to its knees, and the only thing that stopped it was
    somebody watching."""
    with pytest.raises(LimitExceeded):
        bound(_greedy, Limits(seconds=30, memory_mb=256))()


def test_an_ordinary_error_arrives_as_itself_not_as_a_limit():
    """Reporting a bug as a resource limit sends the reader to the wrong place."""
    with pytest.raises(RuntimeError, match="simply wrong") as caught:
        bound(_wrong, Limits(seconds=20, memory_mb=512))()
    assert not isinstance(caught.value, LimitExceeded)


def test_something_unpicklable_is_refused_early_and_says_why():
    """It raises rather than silently running in-process, because a step you
    asked to be bounded and which quietly was not is worse than an error."""
    with pytest.raises(ValueError, match="module-level function"):
        bound(lambda **kw: 1, Limits(seconds=5))()


def test_the_wrapper_says_what_it_is_and_is_not():
    doc = bound(_quick, Limits(seconds=5, memory_mb=64)).__doc__
    assert "5s, 64MB" in doc
    assert "not a security sandbox" in doc


def test_a_bounded_runtime_leaves_unnamed_steps_alone():
    """Most steps do not need a subprocess, and paying the spawn cost for each
    one is a poor trade."""
    from browsergraph.bounded import bounded_runtime

    runtime = bounded_runtime({"a.quick": _quick, "b.quick": _quick},
                              only={"a.quick": Limits(seconds=10)})
    assert runtime.get("b.quick") is _quick
    assert runtime.get("a.quick") is not _quick


def test_a_bounded_step_drops_into_a_plan_unchanged():
    """Bounding is a decision about *how* a step runs. Nothing about the graph
    should have to change for it."""
    from dataclasses import replace

    from browsergraph import execute
    from browsergraph import templates as T
    from browsergraph.compile import compile_route
    from browsergraph.manifest import NodeManifest, PortSpec

    template = T.get("data.quality")
    nodes = tuple(NodeManifest(
        id=f"probe.{s.id}", kind="probe", description=f"probe {s.id}",
        capabilities=(s.capabilities[0],),
        inputs=tuple(PortSpec(n, t) for n, t in s.inputs),
        outputs=tuple(PortSpec(n, t) for n, t in s.outputs))
        for s in template.slots)
    bench = replace(template.instantiate(
        {s.id: [f"probe.{s.id}"] for s in template.slots}), nodes=nodes)
    plan = compile_route(bench, {s.id: f"probe.{s.id}" for s in template.slots})

    limits = Limits(seconds=20, memory_mb=512)
    runtime = execute.Runtime({f"probe.{s.id}": bound(_quick, limits)
                               for s in template.slots})
    got = execute.run(plan, runtime, {"profile": None})
    assert got.ok
