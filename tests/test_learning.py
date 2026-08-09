import json

import pytest

from browsergraph import Engine, Graph, Spec
from browsergraph.dimensions import Behavior, Stealth
from browsergraph.drivers.mock import MockBrowser
from browsergraph.learn import Budget, Features, Knowledge, plan
from browsergraph.nodes.actions import Click, Extract, Navigate
from browsergraph.strategy import ladder, solve

PAGES = {"https://acme.example": {"h1": "Welcome", "#login": "Log in"}}


def good_graph():
    return Graph("g").add(Navigate("https://acme.example")).add(
        Extract("h1", into="heading"))


def bad_graph():
    return Graph("g").add(Navigate("https://acme.example")).add(Click("#nope"))


def factory(pages=None):
    return lambda spec: MockBrowser(spec, pages=pages or PAGES)


def no_sleep(_): pass


# --- features ---------------------------------------------------------------

def test_features_strip_www_and_derive_organisation():
    f = Features.of("https://www.shop.acme.example/products/12")
    assert f.domain == "shop.acme.example"
    assert f.organisation == "acme.example"
    assert f.tld == "example"


def test_path_shape_groups_paginated_urls():
    a = Features.of("https://x.example/news/2026/123")
    b = Features.of("https://x.example/news/2026/456")
    assert a.path_shape == b.path_shape


def test_platform_detected_from_html():
    f = Features.of("https://x.example", html='<div id="__NEXT_DATA__">{}</div>')
    assert f.platform == "react_spa"
    assert Features.of("https://x.example", html="<link href='/wp-content/x.css'>").platform == "wordpress"


def test_buckets_are_ordered_most_specific_first():
    levels = [lvl for lvl, _, _ in Features.of(
        "https://a.acme.example", sector="23", task="research").buckets()]
    assert levels[0] == "site" and levels[-1] == "global"
    assert "sector" in levels


# --- estimates --------------------------------------------------------------

def test_no_evidence_returns_prior_not_certainty():
    k = Knowledge()
    est = k.estimate(Features.of("https://new.example"), "any/spec")
    assert est.p == pytest.approx(0.5)
    assert est.evidence == 0 and not est.confident


def test_single_win_is_smoothed_not_absolute():
    """One success must not be reported as certainty."""
    k = Knowledge()
    f = Features.of("https://acme.example", task="research")
    k.record(f, Spec(engine=Engine.MOCK), ok=True)
    est = k.estimate(f, Spec(engine=Engine.MOCK).describe())
    assert 0.5 < est.p < 0.9, est.p
    assert not est.confident, "n=1 should not be treated as confident"


def test_repeated_wins_build_confidence():
    k = Knowledge()
    f = Features.of("https://acme.example", task="research")
    spec = Spec(engine=Engine.MOCK)
    for _ in range(6):
        k.record(f, spec, ok=True)
    est = k.estimate(f, spec.describe())
    assert est.p > 0.85 and est.confident


def test_losses_lower_the_estimate():
    k = Knowledge()
    f = Features.of("https://acme.example", task="research")
    spec = Spec(engine=Engine.MOCK)
    for _ in range(5):
        k.record(f, spec, ok=False)
    assert k.estimate(f, spec.describe()).p < 0.3


# --- generalisation ---------------------------------------------------------

def test_knowledge_generalises_to_a_similar_site():
    """A brand-new site in a known sector should inherit useful priors."""
    k = Knowledge()
    winner = Spec(engine=Engine.MOCK, stealth=Stealth.STEALTH_JS)
    for i in range(6):
        k.record(Features.of(f"https://shop{i}.example", sector="44-45",
                             task="contacts"), winner, ok=True)

    fresh = Features.of("https://brandnew.example", sector="44-45", task="contacts")
    est = k.estimate(fresh, winner.describe())
    assert est.p > 0.5, "sector experience did not transfer"
    assert est.source == "sector"


def test_exact_site_evidence_outweighs_sector_evidence():
    k = Knowledge()
    sector_spec = Spec(engine=Engine.MOCK, stealth=Stealth.STEALTH_JS)
    site_spec = Spec(engine=Engine.MOCK, behavior=Behavior.humanlike())

    for i in range(10):
        k.record(Features.of(f"https://other{i}.example", sector="23",
                             task="research"), sector_spec, ok=True)
    f = Features.of("https://acme.example", sector="23", task="research")
    for _ in range(3):
        k.record(f, site_spec, ok=True)

    ranked = k.rank(f, [sector_spec, site_spec], explore=0.0)
    assert ranked[0][0].describe() == site_spec.describe()


def test_task_is_part_of_the_key():
    """What works for a spider need not work for a login."""
    k = Knowledge()
    spec = Spec(engine=Engine.MOCK)
    for _ in range(5):
        k.record(Features.of("https://acme.example", task="spider"), spec, ok=True)
    other = k.estimate(Features.of("https://acme.example", task="contacts"),
                       spec.describe())
    assert other.evidence < 2, "evidence leaked across tasks"


# --- caching ----------------------------------------------------------------

def test_successful_spec_is_cached_and_planned_first():
    k = Knowledge()
    f = Features.of("https://acme.example", task="research")
    winner = Spec(engine=Engine.MOCK, stealth=Stealth.STEALTH_JS)
    k.record(f, winner, ok=True)

    assert k.cached_solution(f) == winner.describe()
    p = plan(k, f, ladder(Spec(engine=Engine.MOCK), depth=5) + [winner])
    assert p.cached and p.specs[0].describe() == winner.describe()
    assert "cached solution" in p.rationale


def test_cache_keeps_a_fallback_behind_it():
    k = Knowledge()
    f = Features.of("https://acme.example", task="research")
    winner = Spec(engine=Engine.MOCK, stealth=Stealth.STEALTH_JS)
    k.record(f, winner, ok=True)
    p = plan(k, f, ladder(Spec(engine=Engine.MOCK), depth=5) + [winner])
    assert len(p.specs) >= 2, "no fallback if the cached solution stops working"


def test_knowledge_persists(tmp_path):
    path = tmp_path / "k.json"
    f = Features.of("https://acme.example", task="research")
    k = Knowledge(path=path)
    k.record(f, Spec(engine=Engine.MOCK), ok=True)
    assert Knowledge(path=path).cached_solution(f)
    assert json.loads(path.read_text())["solutions"]


# --- planning ---------------------------------------------------------------

def test_plan_shortlists_rather_than_sweeping():
    """A plan is a shortlist, not the whole valid space."""
    from browsergraph.combos import enumerate_specs
    k = Knowledge()
    candidates = list(enumerate_specs(
        {"stealth": list(Stealth), "display": list(__import__(
            "browsergraph.dimensions", fromlist=["Display"]).Display)},
        base=Spec(engine=Engine.MOCK)))
    assert len(candidates) > 5
    p = plan(k, Features.of("https://new.example"), candidates, shortlist=3)
    assert len(p.specs) == 3 < len(candidates)


def test_cold_start_is_explained():
    p = plan(Knowledge(), Features.of("https://new.example"),
             ladder(Spec(engine=Engine.MOCK)))
    assert "no prior evidence" in p.rationale
    assert not p.cached


def test_exploration_bonus_favours_untried_options():
    k = Knowledge()
    f = Features.of("https://acme.example", task="t")
    tried = Spec(engine=Engine.MOCK)
    untried = Spec(engine=Engine.MOCK, stealth=Stealth.STEALTH_JS)
    k.record(f, tried, ok=True)
    k.record(f, tried, ok=False)          # mediocre: p ~= 0.5

    greedy = k.rank(f, [tried, untried], explore=0.0)
    curious = k.rank(f, [tried, untried], explore=0.5)
    assert curious[0][0].describe() == untried.describe()
    assert greedy != curious


# --- guardrails -------------------------------------------------------------

def test_budget_stops_on_attempts_time_and_llm():
    b = Budget(max_attempts=2, max_seconds=10, max_llm_calls=1)
    b.spend(); assert not b.exhausted()
    b.spend(); assert "attempt limit" in b.exhausted()
    assert "time limit" in Budget(max_seconds=1, attempts=0, seconds=5).exhausted()
    assert "llm call limit" in Budget(max_llm_calls=1, llm_calls=1).exhausted()


def test_early_stop_requires_evidence_not_just_pessimism():
    """Refusing to explore an unfamiliar site is the failure mode here."""
    from browsergraph.learn import Estimate
    b = Budget(min_expected_success=0.5, min_evidence_to_stop=3)
    thin = Estimate("s", p=0.1, evidence=1.0)
    assert b.should_stop_early(thin) == "", "stopped on ignorance"
    solid = Estimate("s", p=0.1, evidence=8.0)
    assert "below" in b.should_stop_early(solid)


def test_no_candidates_stops():
    assert Budget().should_stop_early(None) == "no candidates remain"


# --- solve ------------------------------------------------------------------

def test_solve_succeeds_and_learns():
    k = Knowledge()
    res = solve(good_graph(), factory(), url="https://acme.example",
                task="research", knowledge=k, base=Spec(engine=Engine.MOCK),
                sleep=no_sleep)
    assert res.ok and res.budget.attempts == 1
    f = Features.of("https://acme.example", task="research")
    assert k.cached_solution(f) == res.winner.describe()


def test_second_run_uses_the_cached_solution():
    k = Knowledge()
    solve(good_graph(), factory(), url="https://acme.example", task="research",
          knowledge=k, base=Spec(engine=Engine.MOCK), sleep=no_sleep)
    again = solve(good_graph(), factory(), url="https://acme.example",
                  task="research", knowledge=k, base=Spec(engine=Engine.MOCK),
                  sleep=no_sleep)
    assert again.ok and again.plan.cached
    assert again.budget.attempts == 1


def test_solve_respects_the_attempt_budget():
    res = solve(bad_graph(), factory(), url="https://acme.example", task="t",
                budget=Budget(max_attempts=2), base=Spec(engine=Engine.MOCK),
                sleep=no_sleep)
    assert not res.ok
    assert res.budget.attempts <= 2
    assert "attempt limit" in res.stopped_early


def test_solve_halts_on_terminal_diagnosis():
    pages = {"https://acme.example": {"h1": "Please complete the CAPTCHA"}}
    res = solve(bad_graph(), factory(pages), url="https://acme.example", task="t",
                base=Spec(engine=Engine.MOCK), sleep=no_sleep)
    assert not res.ok and res.stopped_early
    assert len(res.attempts) == 1, "escalated past a challenge"


def test_solve_records_failures_so_future_plans_avoid_them():
    k = Knowledge()
    solve(bad_graph(), factory(), url="https://acme.example", task="t",
          knowledge=k, budget=Budget(max_attempts=3),
          base=Spec(engine=Engine.MOCK), sleep=no_sleep)
    f = Features.of("https://acme.example", task="t")
    first = ladder(Spec(engine=Engine.MOCK))[0]
    assert k.estimate(f, first.describe()).p < 0.5


def test_result_serialises_with_plan_and_budget():
    res = solve(good_graph(), factory(), url="https://acme.example", task="t",
                base=Spec(engine=Engine.MOCK), sleep=no_sleep)
    blob = json.loads(json.dumps(res.to_dict(), default=str))
    assert blob["ok"] and blob["plan"]["candidates"] and blob["budget"]["attempts"] == 1


def test_knowledge_stats_summarise_what_was_learned():
    k = Knowledge()
    f = Features.of("https://acme.example", task="t")
    winner = Spec(engine=Engine.MOCK)
    loser = Spec(engine=Engine.MOCK, stealth=Stealth.STEALTH_JS)
    k.record(f, winner, ok=True)
    k.record(f, loser, ok=False)
    s = k.stats()
    assert s["observations"] == 2 * len(f.buckets())
    assert 0 < s["win_rate"] < 1 and s["cached_solutions"] == 1


def test_failed_cached_solution_is_invalidated():
    """A configuration that stops working must stop being recommended."""
    k = Knowledge()
    f = Features.of("https://acme.example", task="t")
    spec = Spec(engine=Engine.MOCK)
    k.record(f, spec, ok=True)
    assert k.cached_solution(f) == spec.describe()
    k.record(f, spec, ok=False)
    assert k.cached_solution(f) is None
