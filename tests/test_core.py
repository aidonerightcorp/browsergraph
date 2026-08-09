import pytest

from browsergraph import Behavior, Display, Engine, Graph, Spec, Stealth, Transport, run
from browsergraph.combos import count, enumerate_specs, preset, rejected
from browsergraph.dimensions import Binary, Identity, LLMConfig, LLMControl, validate
from browsergraph.drivers import DriverUnavailable, build
from browsergraph.drivers.mock import MockBrowser
from browsergraph.graph import GraphError
from browsergraph.nodes import make
from browsergraph.nodes.actions import Click, Extract, Navigate, Type, WaitFor
from browsergraph.nodes.llm import LLMSelector, LLMVerify

PAGES = {
    "https://example.com": {
        "title": "Example",
        "h1": "Welcome",
        "#login": "Log in",
        "#user": "",
    }
}


def mock(pages=None):
    return MockBrowser(Spec(engine=Engine.MOCK), pages=pages or PAGES)


# --- dimensions -------------------------------------------------------------

def test_valid_spec_has_no_problems():
    assert validate(Spec()) == []


@pytest.mark.parametrize("spec,fragment", [
    (Spec(engine=Engine.SELENIUM, binary=Binary.WEBKIT), "webkit"),
    (Spec(engine=Engine.PLAYWRIGHT, stealth=Stealth.UNDETECTED), "evasion engine"),
    (Spec(engine=Engine.CDP, binary=Binary.FIREFOX), "cannot drive binary=firefox"),
    (Spec(display=Display.HEADED, transport=Transport.BROWSERLESS,
          endpoint="ws://x"), "transport=local"),
    (Spec(transport=Transport.SELENIUM_GRID, endpoint="http://g",
          identity=Identity(profile_dir="/tmp/p")), "profile dir"),
    (Spec(transport=Transport.BROWSERLESS), "requires an endpoint"),
])
def test_incompatible_combinations_are_rejected(spec, fragment):
    problems = validate(spec)
    assert problems, f"expected {spec.describe()} to be invalid"
    assert any(fragment in p for p in problems), problems


def test_llm_disabled_by_default():
    assert Spec().llm.enabled is False
    assert Spec(llm=LLMConfig(mode=LLMControl.AGENT)).llm.enabled is True


# --- combinations -----------------------------------------------------------

def test_enumeration_filters_to_runnable_only():
    total, ok = count()
    assert total > ok > 0
    assert all(validate(s) == [] for s in enumerate_specs())


def test_rejected_reports_reasons():
    bad = rejected()
    assert bad
    assert all(reasons for _, reasons in bad)


def test_axis_subset_holds_other_dimensions_fixed():
    base = Spec(stealth=Stealth.STEALTH_JS)
    specs = list(enumerate_specs({"engine": [Engine.MOCK, Engine.SELENIUM]}, base=base))
    assert len(specs) == 2
    assert {s.stealth for s in specs} == {Stealth.STEALTH_JS}


def test_presets_are_valid_or_explain_themselves():
    for name in ("fast", "human", "stealth_remote", "undetected", "camoufox",
                 "llm_agent", "test"):
        spec = preset(name)
        assert validate(spec) == [], (name, validate(spec))


# --- graph ------------------------------------------------------------------

def build_graph() -> Graph:
    return (Graph("login")
            .add(Navigate("https://example.com"))
            .add(WaitFor("#login"))
            .add(Type("#user", "someone"))
            .add(Click("#login"))
            .add(Extract("h1", into="heading")))


def test_graph_runs_and_records_calls():
    browser = mock()
    result = run(build_graph(), Spec(engine=Engine.MOCK), browser)
    assert result.ok, result.context.error
    assert result.context.data["heading"] == "Welcome"
    assert browser.calls[0] == "start" and browser.calls[-1] == "stop"
    assert "click:#login" in browser.calls


def test_graph_stops_at_first_failure():
    g = (Graph("g")
         .add(Navigate("https://example.com"))
         .add(Click("#missing"))
         .add(Extract("h1", into="heading")))
    result = run(g, Spec(engine=Engine.MOCK), mock())
    assert not result.ok
    assert "not found" in result.context.error
    assert "extract" not in result.executed


def test_optional_click_does_not_fail():
    g = Graph("g").add(Navigate("https://example.com")).add(
        Click("#cookie-banner", optional=True))
    assert run(g, Spec(engine=Engine.MOCK), mock()).ok


def test_duplicate_node_names_rejected():
    g = Graph("g").add(Navigate("https://example.com", name="n"))
    with pytest.raises(GraphError, match="duplicate"):
        g.add(Navigate("https://other.com", name="n"))


def test_invalid_spec_refuses_to_run():
    with pytest.raises(GraphError, match="webkit"):
        run(build_graph(), Spec(engine=Engine.SELENIUM, binary=Binary.WEBKIT), mock())


def test_topological_order_follows_edges():
    g = build_graph()
    order = g.topo()
    assert order.index("navigate") < order.index("click") < order.index("extract")


# --- the point of the whole design -----------------------------------------

def test_one_graph_runs_across_every_valid_combination():
    """Same nodes, every runnable dimension combination, no per-combo code."""
    axes = {
        "display": [Display.HEADLESS],
        "stealth": list(Stealth),
        "binary": list(Binary),
    }
    specs = list(enumerate_specs(axes, base=Spec(engine=Engine.MOCK)))
    assert len(specs) > 10
    for spec in specs:
        result = run(build_graph(), spec, mock())
        assert result.ok, f"{spec.describe()}: {result.context.error}"


def test_behavior_is_orthogonal_to_nodes():
    """Swapping behaviour changes timing, not the call sequence."""
    fast = mock()
    run(Graph("g").add(Navigate("https://example.com",
                                behavior=Behavior.instant())),
        Spec(engine=Engine.MOCK), fast)
    slow = mock()
    run(Graph("g").add(Navigate("https://example.com",
                                behavior=Behavior(dwell_after_load=0.01))),
        Spec(engine=Engine.MOCK), slow)
    assert fast.calls == slow.calls


# --- llm nodes --------------------------------------------------------------

class FakeLLM:
    def __init__(self, reply): self.reply, self.calls = reply, 0
    def complete(self, prompt, system=""):
        self.calls += 1
        return self.reply


def test_llm_selector_skipped_when_fallback_present():
    llm = FakeLLM("#never-used")
    g = Graph("g").add(Navigate("https://example.com")).add(
        LLMSelector("the login button", fallback="#login", client=llm))
    result = run(g, Spec(engine=Engine.MOCK), mock())
    assert result.ok
    assert result.context.data["selector"] == "#login"
    assert llm.calls == 0, "model consulted despite a working selector"


def test_llm_selector_used_when_fallback_missing():
    llm = FakeLLM("#login")
    g = Graph("g").add(Navigate("https://example.com")).add(
        LLMSelector("the login button", fallback="#gone", client=llm))
    result = run(g, Spec(engine=Engine.MOCK), mock())
    assert result.ok and llm.calls == 1
    assert result.context.data["selector"] == "#login"


def test_llm_verify_parses_json_and_fails_loudly():
    ok = run(Graph("g").add(Navigate("https://example.com")).add(
        LLMVerify("logged in", client=FakeLLM('{"ok": true, "why": "banner"}'))),
        Spec(engine=Engine.MOCK), mock())
    assert ok.ok and ok.context.data["verified"] is True

    bad = run(Graph("g").add(Navigate("https://example.com")).add(
        LLMVerify("logged in", client=FakeLLM('{"ok": false, "why": "no banner"}'))),
        Spec(engine=Engine.MOCK), mock())
    assert not bad.ok and "no banner" in bad.context.error


def test_llm_failure_is_explicit_not_silent():
    class Broken:
        def complete(self, *a, **k): raise OSError("connection refused")
    result = run(Graph("g").add(Navigate("https://example.com")).add(
        LLMSelector("anything", client=Broken())), Spec(engine=Engine.MOCK), mock())
    assert not result.ok and "unreachable" in result.context.error


# --- registry / drivers -----------------------------------------------------

def test_nodes_constructible_from_registry():
    node = make("navigate", url="https://example.com")
    assert isinstance(node, Navigate)


def test_unknown_engine_reports_how_to_install():
    with pytest.raises(DriverUnavailable):
        build(Spec(engine=Engine.CDP))


def test_mock_driver_builds_from_spec():
    assert isinstance(build(Spec(engine=Engine.MOCK)), MockBrowser)
