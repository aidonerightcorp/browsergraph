"""Parallel execution, control flow, and utility-based tuning."""
from __future__ import annotations

import threading
import time

from browsergraph import Engine, Graph, Spec, run
from browsergraph.dimensions import Preprocess
from browsergraph.drivers.mock import MockBrowser
from browsergraph.learn import Features, Knowledge, Outcome, best_axis_value
from browsergraph.nodes.actions import Click, Extract, Navigate
from browsergraph.nodes.base import Node
from browsergraph.nodes.control import Branch, ForEach, Frontier, Retry, Subgraph
from browsergraph.ports import Context

PAGES = {"https://example.com": {"h1": "Welcome", "#login": "Log in", "p": "body"}}


def mock(pages=None):
    return MockBrowser(pages=pages or PAGES)


# --- parallel execution -----------------------------------------------------

class Slow(Node):
    """Read-only node that sleeps, so concurrency is observable."""
    kind = "slow_read"

    def __init__(self, key, delay=0.15, name=""):
        super().__init__(name or key)
        self.key, self.delay = key, delay
        self.threads: set = set()

    @property
    def writes(self): return (self.key,)

    def run(self, ctx: Context) -> Context:
        self.threads.add(threading.current_thread().name)
        time.sleep(self.delay)
        ctx.data[self.key] = True
        return ctx


def parallel_graph():
    g = Graph("par").add(Navigate("https://example.com"))
    for i in range(4):
        g.add(Slow(f"k{i}"), after="navigate")     # siblings: one level
    return g


def test_levels_group_independent_nodes():
    levels = parallel_graph().levels()
    assert levels[0] == ["navigate"]
    assert len(levels[1]) == 4, "siblings did not land in one level"


def test_parallel_is_faster_than_sequential():
    seq_start = time.monotonic()
    run(parallel_graph(), Spec(engine=Engine.MOCK), mock(), parallel=1)
    sequential = time.monotonic() - seq_start

    par_start = time.monotonic()
    result = run(parallel_graph(), Spec(engine=Engine.MOCK), mock(), parallel=4)
    concurrent = time.monotonic() - par_start

    assert result.ok
    assert concurrent < sequential * 0.7, (
        f"parallel={concurrent:.2f}s vs sequential={sequential:.2f}s — "
        "the optimiser reports parallelism the runner never performed")


def test_parallel_still_produces_every_result():
    result = run(parallel_graph(), Spec(engine=Engine.MOCK), mock(), parallel=4)
    assert all(result.context.data.get(f"k{i}") for i in range(4))


def test_mutating_nodes_are_never_parallelised():
    """A page is shared mutable state; two clicks racing is not an optimisation."""
    g = Graph("m").add(Navigate("https://example.com"))
    g.add(Click("#login", name="c1"), after="navigate")
    g.add(Click("#login", name="c2"), after="navigate")
    from browsergraph.graph import _concurrent_group
    assert _concurrent_group(g, ["c1", "c2"]) == []


def test_navigate_and_scroll_excluded_from_parallelism():
    g = Graph("n")
    g.add(Navigate("https://example.com", name="n1"))
    g.add(Navigate("https://example.com", name="n2"), after="n1")
    from browsergraph.graph import _concurrent_group
    assert _concurrent_group(g, ["n1", "n2"]) == []


def test_parallel_default_is_off():
    result = run(parallel_graph(), Spec(engine=Engine.MOCK), mock())
    assert result.ok
    assert not any("parallel:" in line for line in result.log)


# --- control flow -----------------------------------------------------------

def test_branch_takes_the_true_path():
    g = (Graph("b").add(Navigate("https://example.com"))
         .add(Branch(lambda d: d.get("title") == "",
                     if_true=[Extract("h1", into="taken")],
                     if_false=[Extract("p", into="not_taken")], name="br")))
    result = run(g, Spec(engine=Engine.MOCK), mock())
    assert result.ok and "taken" in result.context.data
    assert "not_taken" not in result.context.data


def test_branch_predicate_failure_is_explicit():
    def boom(d): raise ValueError("nope")
    g = Graph("b").add(Navigate("https://example.com")).add(
        Branch(boom, if_true=[], name="br"))
    result = run(g, Spec(engine=Engine.MOCK), mock())
    assert not result.ok and "predicate raised" in result.context.error


def test_for_each_iterates_and_caps():
    g = (Graph("fe")
         .add(Navigate("https://example.com"))
         .add(Extract("h1", into="items", name="seed")))
    ctx = Context(browser=mock(), data={"items": list(range(100))})
    node = ForEach("items", body=[Extract("h1", into="v")], max_items=5, name="fe")
    ctx = node.run(ctx)
    assert len(ctx.data["results"]) == 5
    assert any("capped at 5 of 100" in line for line in ctx.log)


def test_for_each_requires_a_bound():
    """An unbounded loop over page-derived items becomes an accidental crawl."""
    import inspect
    sig = inspect.signature(ForEach.__init__)
    assert sig.parameters["max_items"].default == 50


def test_for_each_continues_past_a_failed_item():
    ctx = Context(browser=mock(), data={"items": [1, 2, 3]})
    node = ForEach("items", body=[Click("#missing")], max_items=3, name="fe")
    ctx = node.run(ctx)
    assert ctx.data["results_errors"] == 3
    assert not ctx.failed, "one bad item aborted the whole batch"


def test_subgraph_runs_and_reports_its_io():
    inner = Graph("inner").add(Extract("h1", into="heading"))
    sub = Subgraph(inner, name="sub")
    assert sub.writes == ("heading",)
    g = Graph("outer").add(Navigate("https://example.com")).add(sub)
    result = run(g, Spec(engine=Engine.MOCK), mock())
    assert result.ok and result.context.data["heading"] == "Welcome"


def test_subgraph_propagates_mutation_for_the_linter():
    from browsergraph.lint import lint
    inner = Graph("inner").add(Click("#login"))
    g = Graph("outer").add(Navigate("https://example.com")).add(Subgraph(inner))
    assert "BG003" in {f.code for f in lint(g)}, \
        "a subgraph hid its mutation from the linter"


def test_retry_until_stops_when_satisfied():
    calls = {"n": 0}

    class Bump(Node):
        kind = "bump"
        writes = ("n",)
        def run(self, ctx):
            calls["n"] += 1
            ctx.data["n"] = calls["n"]
            return ctx

    node = Retry([Bump("bump")], until=lambda d: d.get("n", 0) >= 3,
                 max_attempts=5, name="r")
    ctx = node.run(Context(browser=mock()))
    assert not ctx.failed and calls["n"] == 3


def test_retry_until_gives_up_and_says_so():
    node = Retry([Extract("h1", into="v")], until=lambda d: False,
                 max_attempts=2, name="r", describe="never")
    ctx = node.run(Context(browser=mock()))
    assert ctx.failed and "not met after 2" in ctx.error


def test_frontier_brings_crawling_inside_the_graph():
    """Crawling used to bypass healing, supervision and the linter entirely."""
    node = Frontier(body=[Extract("h1", into="h")], max_pages=2, delay=0,
                    respect_robots=False, name="crawl")
    ctx = Context(browser=mock(), data={"url": "https://example.com"})
    ctx = node.run(ctx)
    assert ctx.data["pages"], "frontier collected nothing"
    assert "pages_stats" in ctx.data


def test_frontier_without_a_seed_fails_clearly():
    ctx = Frontier(name="crawl").run(Context(browser=mock()))
    assert ctx.failed and "no seed url" in ctx.error


def test_control_nodes_are_registered():
    from browsergraph.nodes import REGISTRY
    for kind in ("branch", "for_each", "subgraph", "frontier", "retry_until"):
        assert kind in REGISTRY


# --- utility-based tuning ---------------------------------------------------

def test_unmeasured_success_is_a_full_win():
    """Scoring a bare ok=True as 0.5 would make every success a coin flip."""
    assert Outcome(ok=True).utility() == 1.0
    assert Outcome(ok=False).utility() == 0.0


def test_failure_is_zero_however_cheap():
    assert Outcome(ok=False, tokens=0, seconds=0.1).utility() == 0.0


def test_higher_yield_scores_higher():
    lean = Outcome(ok=True, yield_count=1, tokens=1000, seconds=5)
    rich = Outcome(ok=True, yield_count=20, tokens=1000, seconds=5)
    assert rich.utility() > lean.utility()


def test_cheaper_run_scores_higher_for_the_same_yield():
    cheap = Outcome(ok=True, yield_count=5, tokens=500, seconds=2)
    dear = Outcome(ok=True, yield_count=5, tokens=90_000, seconds=200)
    assert cheap.utility() > dear.utility()


def test_utility_lets_preprocessing_be_ranked():
    """Binary outcomes rank every preprocessing strategy identically."""
    k = Knowledge()
    f = Features.of("https://acme.example", task="contacts")
    base = Spec(engine=Engine.MOCK)
    from dataclasses import replace
    for _ in range(4):
        k.record(f, replace(base, preprocess=Preprocess.MARKDOWN),
                 Outcome(ok=True, yield_count=8, tokens=1200, seconds=3))
        k.record(f, replace(base, preprocess=Preprocess.RAW),
                 Outcome(ok=True, yield_count=8, tokens=90_000, seconds=40))

    chosen, estimates = best_axis_value(
        k, f, base, "preprocess", [Preprocess.MARKDOWN, Preprocess.RAW])
    assert chosen is Preprocess.MARKDOWN, [e.to_dict() for e in estimates]


def test_metrics_persist(tmp_path):
    path = tmp_path / "k.json"
    k = Knowledge(path=path)
    f = Features.of("https://acme.example", task="t")
    k.record(f, Spec(engine=Engine.MOCK), Outcome(ok=True, yield_count=3, tokens=100))
    import json
    blob = json.loads(path.read_text())
    assert blob["metrics"], "outcome metrics were not persisted"


def test_bool_recording_still_works():
    k = Knowledge()
    f = Features.of("https://acme.example", task="t")
    k.record(f, Spec(engine=Engine.MOCK), True)
    assert k.estimate(f, Spec(engine=Engine.MOCK).describe()).p > 0.5
