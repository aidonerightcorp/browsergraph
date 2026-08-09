import json

import pytest

from browsergraph import Engine, Graph, Spec
from browsergraph.dimensions import LLMControl, Stealth
from browsergraph.drivers.mock import MockBrowser
from browsergraph.errors import Failure, Response, classify
from browsergraph.nodes.actions import Click, Extract, Navigate
from browsergraph.params import ParamError, ParamSet, check_template, references, substitute
from browsergraph.strategy import SiteMemory, escalate, ladder, suggest

PAGES = {"https://example.com": {"h1": "Welcome", "#login": "Log in"}}


# --- error classification ---------------------------------------------------

@pytest.mark.parametrize("error,expected", [
    ("click target not found: #login", Failure.SELECTOR_MISS),
    ("timeout waiting for #results", Failure.TIMEOUT),
    ("HTTP 429 too many requests", Failure.RATE_LIMIT),
    ("403 Forbidden", Failure.BLOCKED),
    ("401 unauthorized", Failure.AUTH),
    ("ECONNREFUSED proxy", Failure.NETWORK),
    ("llm unreachable at http://x", Failure.LLM),
    ("verification failed: no banner", Failure.VERIFY),
    ("target closed", Failure.CRASH),
])
def test_classification(error, expected):
    assert classify(error).failure is expected


def test_challenge_in_page_beats_selector_miss():
    """A CAPTCHA usually surfaces as a missing element — misreading it is how
    a run keeps hammering a site that already flagged it."""
    d = classify("element not found: #results",
                 page_text="Please complete the CAPTCHA to continue")
    assert d.failure is Failure.CHALLENGE
    assert d.response is Response.ABORT


def test_terminal_failures_do_not_retry():
    for err in ("403 forbidden", "captcha required"):
        assert classify(err).terminal


def test_backoff_grows_then_caps():
    waits = [classify("429 rate limit", attempt=i).backoff() for i in range(6)]
    assert waits[0] < waits[1] < waits[2]
    assert waits[-1] == waits[-2]


def test_unknown_escalates_rather_than_aborting():
    d = classify("something strange happened")
    assert d.failure is Failure.UNKNOWN and d.response is Response.ESCALATE


# --- suggestions ------------------------------------------------------------

def test_selector_miss_suggests_llm_help():
    base = Spec(engine=Engine.MOCK)
    out = suggest(base, classify("not found: #x"))
    assert any(s.llm.mode is LLMControl.SELECTOR for s in out)


def test_challenge_suggests_evasion_engine_and_human_pacing():
    base = Spec(engine=Engine.PLAYWRIGHT)
    out = suggest(base, classify("captcha"))
    assert any(s.engine is Engine.PATCHRIGHT and s.stealth is Stealth.UNDETECTED
               for s in out)
    assert any(s.behavior.max_action_delay > 0 for s in out)


def test_timeout_suggests_waiting_before_changing_engine():
    out = suggest(Spec(engine=Engine.MOCK), classify("timeout waiting for #x"))
    assert out and out[0].behavior.dwell_after_load >= 3


def test_suggestions_are_always_valid_and_novel():
    base = Spec(engine=Engine.PLAYWRIGHT)
    for err in ("not found", "captcha", "timeout", "econnrefused"):
        for s in suggest(base, classify(err)):
            assert s != base
            from browsergraph.dimensions import validate
            assert validate(s) == []


def test_ladder_is_ordered_and_valid():
    steps = ladder(Spec(engine=Engine.PLAYWRIGHT))
    assert len(steps) >= 3
    assert steps[0].stealth is Stealth.NONE          # cheapest first
    assert steps[-1].stealth is Stealth.UNDETECTED   # costliest last


# --- escalation -------------------------------------------------------------

def good_graph():
    return Graph("g").add(Navigate("https://example.com")).add(
        Extract("h1", into="heading"))


def bad_graph():
    return Graph("g").add(Navigate("https://example.com")).add(
        Click("#nope"))


def factory(pages=None):
    return lambda spec: MockBrowser(spec, pages=pages or PAGES)


def test_first_spec_wins_without_escalating():
    res = escalate(good_graph(), ladder(Spec(engine=Engine.MOCK)), factory())
    assert res.ok and len(res.attempts) == 1


def test_escalation_tries_alternatives_then_gives_up():
    res = escalate(bad_graph(), ladder(Spec(engine=Engine.MOCK)), factory(),
                   max_attempts=4, sleep=lambda s: None)
    assert not res.ok
    assert len(res.attempts) > 1, "never escalated"
    assert all(a.diagnosis for a in res.attempts)


def test_terminal_diagnosis_halts_the_ladder():
    """A blocked site must stop the run, not walk the whole ladder."""
    pages = {"https://example.com": {"h1": "Please complete the CAPTCHA"}}
    g = Graph("g").add(Navigate("https://example.com")).add(Click("#nope"))

    def fac(spec):
        b = MockBrowser(spec, pages=pages)
        return b

    res = escalate(g, ladder(Spec(engine=Engine.MOCK)), fac, sleep=lambda s: None)
    assert not res.ok
    assert res.stopped_early, "escalated past a terminal failure"
    assert len(res.attempts) == 1


def test_memory_reorders_to_the_known_good_spec(tmp_path):
    mem = SiteMemory(path=tmp_path / "mem.json")
    winner = Spec(engine=Engine.MOCK, stealth=Stealth.STEALTH_JS)
    mem.record("https://example.com/page", winner, ok=True)

    specs = [Spec(engine=Engine.MOCK), winner]
    ordered = mem.reorder("https://example.com/other", specs)
    assert ordered[0].describe() == winner.describe()


def test_memory_persists_across_instances(tmp_path):
    path = tmp_path / "mem.json"
    SiteMemory(path=path).record("https://a.com", Spec(engine=Engine.MOCK), ok=True)
    assert SiteMemory(path=path).preferred("https://a.com")
    assert json.loads(path.read_text())["a.com"]["best"]


# --- parameters -------------------------------------------------------------

def test_resolve_applies_defaults_and_types():
    ps = ParamSet.from_list([
        {"name": "url", "type": "url"},
        {"name": "limit", "type": "int", "required": False, "default": 5},
    ])
    vals = ps.resolve({"url": "https://example.com"})
    assert vals["url"] == "https://example.com" and vals["limit"] == 5


def test_missing_required_fails_at_load_not_midrun():
    ps = ParamSet.from_list([{"name": "url", "type": "url"}])
    with pytest.raises(ParamError, match="missing required"):
        ps.resolve({})


def test_unknown_parameter_rejected():
    ps = ParamSet.from_list([{"name": "url", "type": "url"}])
    with pytest.raises(ParamError, match="unknown"):
        ps.resolve({"url": "https://a.com", "typo": 1})


def test_bad_url_and_choices_rejected():
    ps = ParamSet.from_list([{"name": "url", "type": "url"}])
    with pytest.raises(ParamError, match="not an http"):
        ps.resolve({"url": "ftp://x"})
    ps2 = ParamSet.from_list([{"name": "mode", "choices": ["a", "b"]}])
    with pytest.raises(ParamError, match="not in"):
        ps2.resolve({"mode": "c"})


def test_secrets_come_from_env_and_are_redacted(monkeypatch):
    monkeypatch.setenv("SITE_PASSWORD", "hunter2")
    ps = ParamSet.from_list([{"name": "password", "env": "SITE_PASSWORD"}])
    vals = ps.resolve({})
    assert vals["password"] == "hunter2"
    assert "password" in ps.secret_names          # inferred from the name
    assert ps.redact("logging in with hunter2", vals) == "logging in with <password>"


def test_substitution_preserves_type_for_whole_string_refs():
    vals = {"limit": 5, "url": "https://a.com"}
    assert substitute("${limit}", vals) == 5                    # int, not "5"
    assert substitute("go to ${url} now", vals) == "go to https://a.com now"
    assert substitute({"a": ["${limit}"]}, vals) == {"a": [5]}


def test_unresolved_reference_is_an_error():
    with pytest.raises(ParamError, match="unresolved"):
        substitute("${nope}", {})


def test_template_check_catches_both_directions():
    raw = {"params": [{"name": "declared_unused"}],
           "nodes": [{"kind": "navigate", "url": "${undeclared}"}]}
    problems = check_template(raw)
    assert any("undeclared" in p for p in problems)
    assert any("never used" in p for p in problems)


def test_references_walks_nested_structures():
    assert references({"a": [{"b": "${x}"}], "c": "${y}"}) == {"x", "y"}


def test_parameterised_config_end_to_end(tmp_path):
    from browsergraph.config import load_graph
    from browsergraph.graph import run
    cfg = tmp_path / "t.json"
    cfg.write_text(json.dumps({
        "spec": {"engine": "mock"},
        "params": [{"name": "target", "type": "url"}],
        "nodes": [{"kind": "navigate", "url": "${target}"},
                  {"kind": "extract", "selector": "h1", "into": "heading"}],
    }))
    graph, spec = load_graph(cfg, {"target": "https://example.com"})
    result = run(graph, spec, MockBrowser(pages=PAGES))
    assert result.ok and result.context.data["heading"] == "Welcome"
