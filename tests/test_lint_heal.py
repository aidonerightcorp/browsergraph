from browsergraph import Engine, Graph, Spec, run
from browsergraph.dimensions import LLMConfig, LLMControl
from browsergraph.drivers.mock import MockBrowser
from browsergraph.heal import Healer, Healing, Ledger
from browsergraph.lint import ERROR, WARN, has_errors, lint, report
from browsergraph.nodes.actions import Click, Extract, Navigate, Screenshot, Type, WaitFor
from browsergraph.nodes.llm import LLMSelector

PAGES = {"https://example.com": {"h1": "Welcome", "#login": "Log in",
                                 "#user": "", "[data-testid=\"submit\"]": "Go"}}


def mock(pages=None):
    return MockBrowser(pages=pages or PAGES)


def codes(findings):
    return {f.code for f in findings}


# --- lint -------------------------------------------------------------------

def test_mutation_without_verification_is_flagged():
    """The rule this library exists to enforce."""
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(WaitFor("#login")).add(Click("#login")))
    findings = lint(g)
    bg003 = [f for f in findings if f.code == "BG003"]
    assert bg003, codes(findings)
    assert "silent failure" in bg003[0].message


def test_verification_after_mutation_clears_it():
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(WaitFor("#login")).add(Click("#login"))
         .add(WaitFor("h1", name="confirm")))
    assert "BG003" not in codes(lint(g))


def test_interaction_without_wait_is_flagged():
    g = Graph("g").add(Navigate("https://example.com")).add(Click("#login"))
    assert "BG004" in codes(lint(g))


def test_optional_interaction_is_not_flagged():
    g = Graph("g").add(Navigate("https://example.com")).add(
        Click("#banner", optional=True))
    assert "BG004" not in codes(lint(g))


def test_brittle_selectors_flagged():
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(WaitFor("div > ul > li > a:nth-child(3)", name="w"))
         .add(Extract("div > ul > li > a:nth-child(3)", into="x")))
    assert "BG005" in codes(lint(g))


def test_inline_credential_is_an_error():
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(WaitFor("#password", name="w"))
         .add(Type("#password", "hunter2")))
    findings = lint(g)
    assert "BG006" in codes(findings)
    assert has_errors(findings)


def test_llm_node_without_fallback_flagged():
    g = Graph("g").add(Navigate("https://example.com")).add(
        LLMSelector("the login button"))
    assert "BG008" in codes(lint(g))


def test_llm_node_with_fallback_not_flagged():
    g = Graph("g").add(Navigate("https://example.com")).add(
        LLMSelector("the login button", fallback="#login"))
    assert "BG008" not in codes(lint(g))


def test_spec_mismatch_flagged():
    g = Graph("g").add(Navigate("https://example.com")).add(
        LLMSelector("x", fallback="#login"))
    assert "BG009" in codes(lint(g, Spec(engine=Engine.MOCK)))
    ok = lint(g, Spec(engine=Engine.MOCK,
                      llm=LLMConfig(mode=LLMControl.SELECTOR)))
    assert "BG009" not in codes(ok)


def test_clean_graph_has_no_errors_or_warnings():
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(WaitFor("#login")).add(Click("#login"))
         .add(WaitFor("h1", name="confirm"))
         .add(Screenshot("/tmp/x.png")))
    findings = lint(g, Spec(engine=Engine.MOCK))
    assert not [f for f in findings if f.severity in (ERROR, WARN)], report(findings)


def test_report_is_readable():
    assert "no findings" in report([])
    assert "BG003" in report(lint(
        Graph("g").add(Navigate("https://example.com")).add(
            WaitFor("#login")).add(Click("#login"))))


# --- healing ----------------------------------------------------------------

def test_identity_resolution_records_no_drift():
    healer = Healer()
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(Healing(Click("#login"), healer=healer)))
    assert run(g, Spec(engine=Engine.MOCK), mock()).ok
    assert healer.ledger.drift_rate == 0.0


def test_alias_heals_a_renamed_selector():
    healer = Healer()
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(Healing(Click("#signin"), healer=healer, aliases=("#login",))))
    result = run(g, Spec(engine=Engine.MOCK), mock())
    assert result.ok
    assert healer.ledger.suggestions() == {"#signin": "#login"}
    assert any("healed" in line for line in result.log)


def test_relax_strategy_drops_nth_child():
    healer = Healer()
    pages = {"https://example.com": {"a": "link"}}
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(Healing(Extract("div > a:nth-child(2)", into="v"), healer=healer)))
    assert run(g, Spec(engine=Engine.MOCK), mock(pages)).ok
    assert healer.ledger.drifted()[0].resolved == "a"


def test_attr_swap_finds_data_testid():
    healer = Healer()
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(Healing(Click("#submit"), healer=healer)))
    assert run(g, Spec(engine=Engine.MOCK), mock()).ok
    assert healer.ledger.drifted()[0].resolved == '[data-testid="submit"]'


def test_unresolvable_selector_fails_rather_than_guessing():
    healer = Healer()
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(Healing(Click("#nowhere"), healer=healer)))
    result = run(g, Spec(engine=Engine.MOCK), mock())
    assert not result.ok and "could not resolve" in result.context.error


def test_llm_is_last_resort_only():
    class LLM:
        calls = 0
        def complete(self, *a, **k):
            LLM.calls += 1
            return "#login"
    healer = Healer(llm_client=LLM(), goal_hint="login button")
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(Healing(Click("#login"), healer=healer)))
    assert run(g, Spec(engine=Engine.MOCK), mock()).ok
    assert LLM.calls == 0, "model consulted although the selector worked"


def test_high_drift_is_called_out():
    ledger = Ledger()
    healer = Healer(ledger=ledger)
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(Healing(Click("#signin"), healer=healer, aliases=("#login",)))
         .add(Healing(Extract("#hdr", into="v"), healer=healer, aliases=("h1",))))
    run(g, Spec(engine=Engine.MOCK), mock())
    assert ledger.drift_rate == 1.0
    assert "page has probably changed" in ledger.report()


def test_ledger_saves_suggestions(tmp_path):
    ledger = Ledger()
    healer = Healer(ledger=ledger)
    g = (Graph("g").add(Navigate("https://example.com"))
         .add(Healing(Click("#signin"), healer=healer, aliases=("#login",))))
    run(g, Spec(engine=Engine.MOCK), mock())
    import json
    path = ledger.save(tmp_path / "heal.json")
    data = json.loads(open(path).read())
    assert data["suggestions"]["#signin"] == "#login"
