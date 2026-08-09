import json

import pytest

from browsergraph import Engine, Graph, Spec, run
from browsergraph.drivers.mock import MockBrowser
from browsergraph.nodes.actions import Click, Extract, Navigate, Scroll, WaitFor
from browsergraph.nodes.base import Node
from browsergraph.optimize import (
    Memoized,
    SkipIfPresent,
    optimize_graph,
)
from browsergraph.plugins import (
    CAPABILITIES,
    SCHEMA_VERSION,
    Manifest,
    PluginError,
    discover,
    load,
    template,
)
from browsergraph.ports import Context
from browsergraph.supervise import CircuitBreaker, NodeStats, Supervised, Supervisor

PAGES = {"https://example.com": {"h1": "Welcome", "#login": "Log in"}}


def mock(pages=None):
    return MockBrowser(pages=pages or PAGES)


class Flaky(Node):
    """Fails `fail_times` then succeeds."""
    kind = "flaky"
    writes = ("flaky_done",)

    def __init__(self, fail_times=1, error="not found: #x", name="flaky"):
        super().__init__(name)
        self.fail_times, self.error, self.calls = fail_times, error, 0

    def run(self, ctx: Context) -> Context:
        self.calls += 1
        if self.calls <= self.fail_times:
            ctx.fail(self.error)
            return ctx
        ctx.data["flaky_done"] = True
        return ctx


# --- supervision ------------------------------------------------------------

def test_retry_recovers_a_flaky_node():
    inner = Flaky(fail_times=2)
    g = Graph("g").add(Navigate("https://example.com")).add(
        Supervised(inner, retries=3, sleep=lambda s: None, name="sv"))
    result = run(g, Spec(engine=Engine.MOCK), mock())
    assert result.ok and inner.calls == 3


def test_retries_are_bounded():
    inner = Flaky(fail_times=99)
    sv = Supervised(inner, retries=2, sleep=lambda s: None, name="sv")
    g = Graph("g").add(Navigate("https://example.com")).add(sv)
    assert not run(g, Spec(engine=Engine.MOCK), mock()).ok
    assert inner.calls == 3          # initial + 2 retries
    assert sv.stats.failures == 1


def test_terminal_failure_is_not_retried():
    """A CAPTCHA must not be retried — that is how accounts get banned."""
    inner = Flaky(fail_times=99, error="captcha required")
    sv = Supervised(inner, retries=5, sleep=lambda s: None, name="sv")
    run(Graph("g").add(Navigate("https://example.com")).add(sv),
        Spec(engine=Engine.MOCK), mock())
    assert inner.calls == 1


def test_fallback_node_runs_after_retries_exhausted():
    class Fallback(Node):
        kind = "fb"
        writes = ("flaky_done",)
        def run(self, ctx):
            ctx.data["flaky_done"] = "via-fallback"
            return ctx
    sv = Supervised(Flaky(fail_times=99), retries=1, fallback=Fallback("fb"),
                    sleep=lambda s: None, name="sv")
    result = run(Graph("g").add(Navigate("https://example.com")).add(sv),
                 Spec(engine=Engine.MOCK), mock())
    assert result.ok and result.context.data["flaky_done"] == "via-fallback"


def test_timeout_marks_a_slow_success_as_failure():
    clock = iter([0.0, 0.0, 5.0, 5.0, 10.0, 10.0, 15.0, 20.0])

    class Slow(Node):
        kind = "slow"
        def run(self, ctx): return ctx

    sv = Supervised(Slow("slow"), retries=0, timeout=1.0,
                    clock=lambda: next(clock), sleep=lambda s: None, name="sv")
    result = run(Graph("g").add(Navigate("https://example.com")).add(sv),
                 Spec(engine=Engine.MOCK), mock())
    assert not result.ok and "timeout" in result.context.error


def test_circuit_breaker_opens_then_recovers():
    cb = CircuitBreaker(threshold=2, cooldown=10)
    cb.record(False, 0); cb.record(False, 0)
    assert cb.is_open(1)
    assert not cb.is_open(11)         # cooldown elapsed


def test_open_circuit_short_circuits_the_node():
    inner = Flaky(fail_times=99)
    cb = CircuitBreaker(threshold=1, cooldown=999)
    sv = Supervised(inner, retries=0, breaker=cb, sleep=lambda s: None, name="sv")
    ctx = Context(browser=mock())
    sv.run(ctx)                       # trips the breaker
    calls_after_first = inner.calls
    ctx2 = Context(browser=mock())
    sv.run(ctx2)
    assert inner.calls == calls_after_first      # not attempted again
    assert "circuit open" in ctx2.error


def test_supervisor_wraps_a_graph_and_reports():
    sup = Supervisor(retries=1)
    g = Graph("g").add(Navigate("https://example.com")).add(Extract("h1", into="v"))
    sg = sup.supervise(g)
    assert run(sg, Spec(engine=Engine.MOCK), mock()).ok
    rep = sup.report()
    assert rep["nodes"] and rep["total_failures"] == 0


def test_supervision_preserves_node_semantics_for_lint():
    from browsergraph.lint import lint
    sup = Supervisor()
    g = Graph("g").add(Navigate("https://example.com")).add(
        WaitFor("#login")).add(Click("#login"))
    codes = {f.code for f in lint(sup.supervise(g))}
    assert "BG003" in codes, "wrapping hid the mutation from the linter"


# --- node optimisation ------------------------------------------------------

def test_memoize_serves_second_call_from_cache():
    inner = Flaky(fail_times=0)
    memo = Memoized(inner, name="m")
    ctx = Context(browser=mock(), data={"url": "https://example.com"})
    memo.run(ctx); memo.run(ctx)
    assert inner.calls == 1 and memo.hits == 1


def test_memoize_refuses_mutating_nodes():
    with pytest.raises(ValueError, match="mutates"):
        Memoized(Click("#login"))


def test_skip_if_present_avoids_redundant_work():
    inner = Flaky(fail_times=0)
    node = SkipIfPresent(inner, keys=("flaky_done",), name="s")
    ctx = Context(browser=mock(), data={"flaky_done": True})
    node.run(ctx)
    assert inner.calls == 0


# --- graph optimisation -----------------------------------------------------

def test_dead_node_removed_and_explained():
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(Extract("h1", into="unused"))
         .add(WaitFor("#login")))
    out, report = optimize_graph(g)
    assert "extract" not in out.nodes
    assert any("removed extract" in r.change for r in report.rewrites)
    assert "nothing reads" in report.explain()


def test_kept_outputs_are_not_removed():
    g = Graph("g").add(Navigate("https://example.com")).add(Extract("h1", into="wanted"))
    out, _ = optimize_graph(g, keep=("wanted",))
    assert "extract" in out.nodes


def test_duplicate_wait_collapsed():
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(WaitFor("#login", name="w1")).add(WaitFor("#login", name="w2")))
    out, report = optimize_graph(g)
    assert "w2" not in out.nodes and "w1" in out.nodes
    assert any("duplicate wait" in r.reason for r in report.rewrites)


def test_wait_after_mutation_is_preserved():
    """A wait after a click is a verification, not a duplicate."""
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(WaitFor("#login", name="w1")).add(Click("#login"))
         .add(WaitFor("#login", name="w2")))
    out, _ = optimize_graph(g)
    assert "w2" in out.nodes


def test_optimised_graph_still_runs_identically():
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(Extract("h1", into="heading")).add(Extract("h1", into="dead")))
    out, _ = optimize_graph(g, keep=("heading",))
    result = run(out, Spec(engine=Engine.MOCK), mock())
    assert result.ok and result.context.data["heading"] == "Welcome"


def test_parallelizable_groups_reported():
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(Extract("h1", into="a")).add(Extract("h1", into="b"))
         .add(Scroll(100)))
    _, report = optimize_graph(g, keep=("a", "b"))
    assert report.parallelizable
    assert "could run concurrently" in report.explain()


def test_timeout_tightened_from_observed_runtime():
    stats = {"w1": NodeStats(name="w1", runs=10, failures=0, total_seconds=2.0)}
    g = Graph("g").add(Navigate("https://example.com")).add(
        WaitFor("#login", timeout=100.0, name="w1"))
    out, report = optimize_graph(g, stats=stats)
    assert out.nodes["w1"].timeout < 100
    assert any("tighten_timeouts" == r.pass_name for r in report.rewrites)


def test_report_serialises():
    _, report = optimize_graph(Graph("g").add(Navigate("https://example.com")))
    assert json.loads(json.dumps(report.to_dict()))["rewrites"] == []


# --- plugins ----------------------------------------------------------------

def test_template_is_a_valid_manifest():
    m = Manifest.from_dict(template("demo"))
    assert m.name == "demo" and m.schema_version == SCHEMA_VERSION


def test_manifest_requires_name_and_module():
    with pytest.raises(PluginError, match="missing required"):
        Manifest.from_dict({"name": "x"})


def test_incompatible_schema_major_rejected():
    with pytest.raises(PluginError, match="incompatible"):
        Manifest.from_dict({"name": "x", "module": "m", "schema_version": "99.0"})


def test_unknown_capability_rejected():
    with pytest.raises(PluginError, match="unknown capability"):
        Manifest.from_dict({"name": "x", "module": "m",
                            "provides": {"weapons": ["x"]}})


def test_discovery_reads_manifests_without_importing(tmp_path):
    d = tmp_path / "myplugin"
    d.mkdir()
    (d / "plugin.json").write_text(json.dumps(
        {**template("myplugin"), "module": "does.not.exist"}))
    found = discover(tmp_path, entry_points=False)
    assert len(found) == 1 and found[0].name == "myplugin"


def test_load_reports_missing_module_with_requirements(tmp_path):
    m = Manifest.from_dict({**template("p"), "module": "nope_not_real",
                            "requires": ["nope-lib>=1"]})
    rep = load(m)
    assert not rep.loaded and "nope-lib>=1" in rep.error


def test_load_registers_and_reports_new_nodes(monkeypatch):
    """Registration must happen during load(), not before it."""
    import sys
    import types
    mod = types.ModuleType("bg_demo_plugin")

    def plugin_init():
        from browsergraph.nodes.base import Node, register

        @register
        class DemoNode(Node):
            kind = "demo_node"
            def run(self, ctx):
                return ctx

    mod.plugin_init = plugin_init
    monkeypatch.setitem(sys.modules, "bg_demo_plugin", mod)

    from browsergraph.nodes.base import REGISTRY
    assert "demo_node" not in REGISTRY
    try:
        m = Manifest.from_dict({**template("demo"), "module": "bg_demo_plugin",
                                "provides": {"nodes": ["demo_node"]}})
        rep = load(m)
        assert rep.loaded, rep.error
        assert rep.registered.get("nodes") == ["demo_node"]
        assert not rep.undeclared and not rep.overrides
    finally:
        REGISTRY.pop("demo_node", None)


def test_undeclared_registration_is_reported(monkeypatch):
    """A plugin that registers more than it declared must not do so silently."""
    import sys
    import types
    mod = types.ModuleType("bg_sneaky_plugin")

    def plugin_init():
        from browsergraph.nodes.base import Node, register

        @register
        class Sneaky(Node):
            kind = "sneaky_node"
            def run(self, ctx):
                return ctx

    mod.plugin_init = plugin_init
    monkeypatch.setitem(sys.modules, "bg_sneaky_plugin", mod)

    from browsergraph.nodes.base import REGISTRY
    try:
        m = Manifest.from_dict({**template("sneaky"), "module": "bg_sneaky_plugin",
                                "provides": {"nodes": ["declared_only"]}})
        rep = load(m)
        assert "nodes:sneaky_node" in rep.undeclared
    finally:
        REGISTRY.pop("sneaky_node", None)


def test_overriding_a_builtin_kind_fails_loudly(monkeypatch):
    """The node registry itself refuses a duplicate kind — the plugin cannot
    silently replace `click`."""
    import sys
    import types
    mod = types.ModuleType("bg_evil_plugin")

    def plugin_init():
        from browsergraph.nodes.base import Node, register

        @register
        class FakeClick(Node):
            kind = "click"
            def run(self, ctx):
                return ctx

    mod.plugin_init = plugin_init
    monkeypatch.setitem(sys.modules, "bg_evil_plugin", mod)

    m = Manifest.from_dict({**template("evil"), "module": "bg_evil_plugin",
                            "provides": {"nodes": ["click"]}})
    rep = load(m)
    assert not rep.loaded
    assert "duplicate node kind" in rep.error or "override" in rep.error

    from browsergraph.nodes.actions import Click
    from browsergraph.nodes.base import REGISTRY
    assert REGISTRY["click"] is Click, "a plugin replaced a built-in node"


def test_capabilities_are_documented():
    assert set(CAPABILITIES) >= {"nodes", "tasks", "drivers"}
