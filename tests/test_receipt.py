"""Receipts: evidence a run happened, in a form you can act on later.

`ok=True` and nothing else is unfalsifiable. These check the questions a receipt
exists to answer — which route ran, what it produced, whether anything changed
without being verified, and whether the artifacts are still what they were.
"""
from __future__ import annotations

import json

from browsergraph import receipt as rc


class FakeSpec:
    def to_dict(self):
        from browsergraph.dimensions import Binary, Display, Engine
        return {"engine": Engine.PLAYWRIGHT, "binary": Binary.SYSTEM_CHROME,
                "display": Display.HEADLESS}


class FakeCtx:
    def __init__(self, artifacts=(), error=""):
        self.data = {"price": "$49.00"}
        self.artifacts = list(artifacts)
        self.log = ["navigate -> x"]
        self.error = error


class FakeResult:
    def __init__(self, ok=True, artifacts=(), error=""):
        self.ok = ok
        self.context = FakeCtx(artifacts, error)
        self.executed = ["navigate", "click"]
        self.spec = FakeSpec()


def test_a_receipt_records_the_route_and_the_spec():
    got = rc.of_run(FakeResult())
    assert got.route == ("navigate", "click")
    assert got.spec["engine"].value == "playwright"


def test_the_replay_line_is_pasteable():
    """Enum reprs are not a command. `BG_ENGINE=Engine.PLAYWRIGHT` has to be
    edited by hand, which makes it not a replay line."""
    line = rc.of_run(FakeResult()).replay()
    assert "BG_ENGINE=playwright" in line
    assert "Engine." not in line


def test_a_failure_is_recorded_not_skipped():
    """The failures are the runs with something to teach."""
    got = rc.of_run(FakeResult(ok=False, error="element not found"))
    assert got.ok is False
    assert got.error == "element not found"


def test_artifacts_are_hashed_so_the_promise_is_checkable(tmp_path):
    target = tmp_path / "shot.png"
    target.write_bytes(b"pretend png")
    got = rc.of_run(FakeResult(artifacts=[str(target)]))
    assert got.artifacts[0].digest.startswith("sha256:")
    assert got.verify_artifacts() == []
    target.write_bytes(b"different bytes entirely")
    assert got.verify_artifacts() == [str(target)]


def test_a_missing_artifact_is_reported_not_crashed(tmp_path):
    got = rc.of_run(FakeResult(artifacts=[str(tmp_path / "never-written.png")]))
    assert "unreadable" in got.artifacts[0].digest


def test_mutation_without_verification_is_visible_after_the_fact():
    """BG003 catches this before running; this catches a verifier that was
    skipped, short-circuited or silently failed."""
    steps = (rc.StepRecord(kind="click", name="click", mutates=True),)
    assert rc.of_run(FakeResult(), steps=steps).unverified_mutation

    verified = steps + (rc.StepRecord(kind="wait_for", name="confirm",
                                      verifies=True),)
    assert not rc.of_run(FakeResult(), steps=verified).unverified_mutation


def test_a_verifier_that_failed_does_not_count_as_verification():
    steps = (rc.StepRecord(kind="click", name="click", mutates=True),
             rc.StepRecord(kind="assert_text", name="check", verifies=True,
                           ok=False, error="not found"))
    assert rc.of_run(FakeResult(), steps=steps).unverified_mutation


def test_the_slowest_step_is_findable():
    steps = (rc.StepRecord(kind="a", seconds=0.1),
             rc.StepRecord(kind="b", seconds=2.5))
    assert rc.of_run(FakeResult(), steps=steps).slowest.kind == "b"


def test_big_values_are_truncated_with_a_note():
    """A page's full HTML in the receipt makes the file unreadable."""
    context = FakeCtx()
    context.data["html"] = "x" * 10_000
    result = FakeResult()
    result.context = context
    text = rc.of_run(result).to_dict()["data"]["html"]
    assert "truncated" in text and len(text) < 5000


def test_a_receipt_round_trips(tmp_path):
    original = rc.of_run(FakeResult(), steps=(rc.StepRecord(kind="click"),))
    path = original.write(str(tmp_path / "r.json"))
    again = rc.TaskReceipt.load(path)
    assert again.route == original.route
    assert again.steps[0].kind == "click"
    json.loads(again.to_json())


def test_comparing_two_runs_says_what_changed():
    """When a graph that worked stops working, the diff is the shortest path."""
    before = rc.of_run(FakeResult())
    after_result = FakeResult(ok=False, error="timeout")
    after_result.context.data["price"] = "$52.00"
    after = rc.of_run(after_result)
    changes = rc.compare(before, after)
    assert any("outcome True -> False" in c for c in changes)
    assert any("price" in c for c in changes)


def test_the_environment_records_what_is_installed():
    env = rc.environment()
    assert env["python"] and env["platform"]
    assert isinstance(env["packages"], dict)


def test_bundling_many_receipts(tmp_path):
    path = rc.bundle([rc.of_run(FakeResult()) for _ in range(3)],
                     str(tmp_path / "all.json"))
    assert len(json.loads(open(path).read())["receipts"]) == 3
