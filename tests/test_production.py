"""Rate limiting and multi-model routing — the production concerns."""
from __future__ import annotations

import threading

import pytest

from browsergraph.dimensions import LLMConfig
from browsergraph.models import Catalog, ModelInfo, ModelUnavailable
from browsergraph.routing import JOBS, Call, Ledger, Router, plan_assignments
from browsergraph.throttle import DomainPolicy, Gate, Limiter, domain_of


class FakeClock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def sleep(self, s): self.t += s


# --- throttling -------------------------------------------------------------

def test_domain_extraction_ignores_www_and_path():
    assert domain_of("https://www.Acme.example/a/b?x=1") == "acme.example"
    assert domain_of("") == ""


def test_first_request_is_not_delayed():
    clock = FakeClock()
    lim = Limiter(default=DomainPolicy(min_interval=2.0))
    assert lim.acquire("https://a.example/1", sleep=clock.sleep, clock=clock) == 0.0


def test_second_request_to_same_domain_waits():
    clock = FakeClock()
    lim = Limiter(default=DomainPolicy(min_interval=2.0))
    lim.acquire("https://a.example/1", sleep=clock.sleep, clock=clock)
    lim.release("https://a.example/1")
    waited = lim.acquire("https://a.example/2", sleep=clock.sleep, clock=clock)
    assert waited == pytest.approx(2.0)


def test_different_domains_do_not_block_each_other():
    clock = FakeClock()
    lim = Limiter(default=DomainPolicy(min_interval=5.0))
    lim.acquire("https://a.example/1", sleep=clock.sleep, clock=clock)
    assert lim.acquire("https://b.example/1", sleep=clock.sleep, clock=clock) == 0.0


def test_limiter_is_shared_across_crawlers():
    """The bug this fixes: ten tasks, ten independent delays, one host."""
    clock = FakeClock()
    lim = Limiter(default=DomainPolicy(min_interval=1.0))
    waits = []
    for _ in range(4):                       # four separate "crawlers"
        waits.append(lim.acquire("https://one-host.example/p", sleep=clock.sleep,
                                 clock=clock))
        lim.release("https://one-host.example/p")
    assert waits[0] == 0.0
    assert all(w == pytest.approx(1.0) for w in waits[1:]), waits


def test_robots_crawl_delay_tightens_policy_but_never_loosens_it():
    lim = Limiter(default=DomainPolicy(min_interval=1.0))
    lim.observe_crawl_delay("slow.example", 10.0)
    assert lim.policy_for("slow.example").min_interval == 10.0
    lim.observe_crawl_delay("slow.example", 0.1)
    assert lim.policy_for("slow.example").min_interval == 10.0, \
        "a lower crawl-delay must not relax our own politeness"


def test_concurrency_cap_is_enforced():
    lim = Limiter(default=DomainPolicy(min_interval=0.0, max_concurrent=1))
    lim.acquire("https://a.example/1")
    released = threading.Event()

    def second():
        lim.acquire("https://a.example/2")
        released.set()
        lim.release("https://a.example/2")

    t = threading.Thread(target=second, daemon=True)
    t.start()
    assert not released.wait(0.2), "concurrency cap was not enforced"
    lim.release("https://a.example/1")
    assert released.wait(2.0)
    t.join(timeout=2)


def test_gate_releases_even_when_the_body_raises():
    lim = Limiter(default=DomainPolicy(min_interval=0.0, max_concurrent=1))
    with pytest.raises(ValueError):
        with Gate(lim, "https://a.example/1"):
            raise ValueError("boom")
    # if release did not happen this would block forever
    assert lim.acquire("https://a.example/2", sleep=lambda s: None) == 0.0


def test_limiter_reports_requests_and_waiting():
    clock = FakeClock()
    lim = Limiter(default=DomainPolicy(min_interval=1.0))
    for _ in range(3):
        lim.acquire("https://a.example/x", sleep=clock.sleep, clock=clock)
        lim.release("https://a.example/x")
    rep = lim.report()
    assert rep["requests"]["a.example"] == 3
    assert rep["waited_sec"]["a.example"] > 0


def test_none_limiter_falls_back_to_per_crawler_delay():
    from browsergraph.crawl import Crawler, CrawlLimits
    from browsergraph.drivers.mock import MockBrowser
    slept = []
    c = Crawler(MockBrowser(pages={"https://a.example": {"h1": "x"}}),
                "https://a.example", CrawlLimits(delay=0.5, respect_robots=False),
                sleep=slept.append, limiter=None)
    list(c.pages())
    assert c.limiter is None


# --- routing ----------------------------------------------------------------

def catalog():
    return Catalog(host="http://x", reachable=True, models=[
        ModelInfo("glm-5.2:cloud", ("completion", "tools", "thinking")),
        ModelInfo("kimi-k2.7-code:cloud", ("vision", "completion", "tools", "thinking")),
        ModelInfo("deepseek-v4-flash:0731-cloud", ("completion", "tools")),
    ])


def test_each_job_routes_to_a_suitable_model():
    r = Router(catalog=catalog())
    assert r.model_for("vision").startswith("kimi")      # only vision-capable
    assert r.model_for("classify").startswith("deepseek")  # cheapest for the job
    assert r.model_for("plan").startswith("glm")           # thinking


def test_jobs_declare_capability_and_budget():
    for job, spec in JOBS.items():
        assert spec["capability"], f"{job} declares no capability"
        assert spec["max_tokens"] > 0


def test_unknown_job_is_rejected():
    with pytest.raises(ModelUnavailable, match="unknown job"):
        Router(catalog=catalog()).model_for("nonsense")


def test_override_still_validates_capability():
    """Silently substituting would make a run's behaviour untraceable."""
    r = Router(catalog=catalog(), overrides={"vision": "glm-5.2:cloud"})
    with pytest.raises(ModelUnavailable, match="does not support"):
        r.model_for("vision")


def test_missing_capability_raises_rather_than_downgrading():
    text_only = Catalog(host="http://x", reachable=True,
                        models=[ModelInfo("t", ("completion",))])
    with pytest.raises(ModelUnavailable, match="vision"):
        Router(catalog=text_only).model_for("vision")


def test_fallback_chain_starts_with_the_chosen_model():
    chain = Router(catalog=catalog()).chain_for("classify")
    assert chain[0].startswith("deepseek") and len(chain) == 3


def test_call_falls_back_and_records_both_attempts():
    r = Router(catalog=catalog())
    seen = []

    def flaky(model, prompt):
        seen.append(model)
        if len(seen) == 1:
            raise RuntimeError("first model down")
        return "answer"

    assert r.call("classify", flaky, "hello") == "answer"
    assert len(seen) == 2
    assert len(r.ledger.calls) == 2
    assert [c.ok for c in r.ledger.calls] == [False, True]


def test_call_raises_when_every_model_fails():
    r = Router(catalog=catalog())
    with pytest.raises(ModelUnavailable, match="every model failed"):
        r.call("classify", lambda m, p: (_ for _ in ()).throw(RuntimeError("no")), "x")


def test_ledger_totals_and_grouping():
    led = Ledger()
    led.add(Call("classify", "a", tokens_in=100, tokens_out=50, seconds=1.0))
    led.add(Call("plan", "b", tokens_in=200, tokens_out=100, seconds=2.0, ok=False))
    assert led.tokens == 450 and led.seconds == 3.0
    by = led.by_model()
    assert by["b"]["failures"] == 1
    assert "450 tokens" in led.report()


def test_config_for_pins_the_job_model():
    r = Router(catalog=catalog(), cfg=LLMConfig(model="ignored"))
    assert r.config_for("vision").model.startswith("kimi")


def test_plan_assignments_survives_a_missing_capability():
    text_only = Catalog(host="http://x", reachable=True,
                        models=[ModelInfo("t", ("completion",))])
    out = plan_assignments(text_only)
    assert "unavailable" in out["vision"]
    assert out["classify"] == "t"
