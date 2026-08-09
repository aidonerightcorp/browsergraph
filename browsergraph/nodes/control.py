"""Control-flow nodes: branch, loop, subgraph, frontier.

Crawling previously lived entirely outside the graph, which meant healing,
supervision, linting and the optimiser did not apply to it — and crawling is
where most of the runtime goes. These nodes bring iteration inside the model,
so a crawl is a graph and gets the same guarantees as everything else.

A subgraph is a node. That is what makes composition work: a crawl step is a
graph, a task is a graph of graphs, and every layer is linted and supervised
identically.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from browsergraph.nodes.base import Node, register
from browsergraph.ports import Context


@register
class Branch(Node):
    """Run one of two node sequences depending on a predicate over context.

    The predicate reads `ctx.data` only — a branch that re-queries the page
    would make the decision depend on timing rather than on what the graph
    has established.
    """

    kind: ClassVar[str] = "branch"

    def __init__(self, predicate: Callable[[dict], bool],
                 if_true: list[Node] | None = None,
                 if_false: list[Node] | None = None,
                 name: str = "", describe: str = ""):
        super().__init__(name)
        self.predicate = predicate
        self.if_true = list(if_true or [])
        self.if_false = list(if_false or [])
        self.describe_as = describe or "predicate"
        branch_nodes = self.if_true + self.if_false
        self.mutates = any(n.mutates for n in branch_nodes)
        self.verifies = all(n.verifies for n in self.if_true) and bool(self.if_true) \
            and all(n.verifies for n in self.if_false) and bool(self.if_false)

    @property
    def writes(self) -> tuple[str, ...]:  # type: ignore[override]
        return tuple({w for n in self.if_true + self.if_false for w in n.writes})

    def run(self, ctx: Context) -> Context:
        try:
            taken = bool(self.predicate(ctx.data))
        except Exception as e:
            ctx.fail(f"{self.name}: predicate raised {type(e).__name__}: {e}")
            return ctx
        chosen = self.if_true if taken else self.if_false
        ctx.note(f"branch {self.describe_as} -> {'true' if taken else 'false'} "
                 f"({len(chosen)} node(s))")
        for node in chosen:
            ctx = node.run(ctx)
            if ctx.failed:
                break
        return ctx


@register
class ForEach(Node):
    """Run a body once per item, writing each result into a collection.

    `max_items` is required rather than optional: an unbounded loop over
    page-derived items is how a run turns into an accidental crawl of the
    entire internet.
    """

    kind: ClassVar[str] = "for_each"

    def __init__(self, items_key: str, body: list[Node],
                 item_key: str = "item", into: str = "results",
                 max_items: int = 50, stop_on_error: bool = False,
                 name: str = ""):
        super().__init__(name)
        self.items_key = items_key
        self.body = list(body)
        self.item_key = item_key
        self.into = into
        self.max_items = max_items
        self.stop_on_error = stop_on_error
        self.mutates = any(n.mutates for n in self.body)

    @property
    def reads(self) -> tuple[str, ...]:  # type: ignore[override]
        return (self.items_key,)

    @property
    def writes(self) -> tuple[str, ...]:  # type: ignore[override]
        return (self.into,)

    def run(self, ctx: Context) -> Context:
        items = ctx.data.get(self.items_key) or []
        if not isinstance(items, (list, tuple)):
            ctx.fail(f"{self.name}: {self.items_key!r} is not a list")
            return ctx

        results: list[Any] = []
        errors = 0
        for i, item in enumerate(items[: self.max_items]):
            ctx.data[self.item_key] = item
            ctx.data[f"{self.item_key}_index"] = i
            failed_before = ctx.failed
            for node in self.body:
                ctx = node.run(ctx)
                if ctx.failed:
                    break
            if ctx.failed and not failed_before:
                errors += 1
                ctx.note(f"{self.name}: item {i} failed: {ctx.error}")
                if self.stop_on_error:
                    break
                ctx.failed, ctx.error = False, ""     # continue the batch
            else:
                results.append({k: v for k, v in ctx.data.items()
                                if k not in (self.item_key, f"{self.item_key}_index")})

        ctx.data[self.into] = results
        ctx.data[f"{self.into}_errors"] = errors
        if len(items) > self.max_items:
            ctx.note(f"{self.name}: capped at {self.max_items} of {len(items)} items")
        ctx.note(f"{self.name}: {len(results)} ok, {errors} failed")
        return ctx


@register
class Subgraph(Node):
    """A graph as a node.

    Runs against the same browser and context, so state flows through. The
    inner graph is linted and optimised like any other, which is what makes
    composition safe rather than a way to smuggle unchecked steps in.
    """

    kind: ClassVar[str] = "subgraph"

    def __init__(self, graph, name: str = "", strict: bool = True):
        super().__init__(name or getattr(graph, "name", "subgraph"))
        self.graph = graph
        self.strict = strict
        nodes = list(graph.nodes.values())
        self.mutates = any(n.mutates for n in nodes)
        self.verifies = any(n.verifies for n in nodes)

    @property
    def writes(self) -> tuple[str, ...]:  # type: ignore[override]
        return tuple({w for n in self.graph.nodes.values() for w in n.writes})

    @property
    def reads(self) -> tuple[str, ...]:  # type: ignore[override]
        produced = {w for n in self.graph.nodes.values() for w in n.writes}
        return tuple({r for n in self.graph.nodes.values()
                      for r in n.reads if r not in produced})

    def run(self, ctx: Context) -> Context:
        ctx.note(f"subgraph {self.graph.name}: {len(self.graph.nodes)} node(s)")
        for key in self.graph.topo():
            ctx = self.graph.nodes[key].run(ctx)
            if ctx.failed and self.strict:
                ctx.note(f"subgraph {self.graph.name} stopped at {key}")
                break
        return ctx


@register
class Frontier(Node):
    """Crawl a site inside the graph, running a body per page.

    This is the node that closes the gap: crawling used to bypass healing,
    supervision and the linter because it lived outside the graph entirely.
    """

    kind: ClassVar[str] = "frontier"

    def __init__(self, seed_key: str = "url", body: list[Node] | None = None,
                 into: str = "pages", max_pages: int = 25, max_depth: int = 2,
                 delay: float = 1.0, respect_robots: bool = True,
                 name: str = ""):
        super().__init__(name)
        self.seed_key = seed_key
        self.body = list(body or [])
        self.into = into
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.delay = delay
        self.respect_robots = respect_robots
        self.mutates = any(n.mutates for n in self.body)

    @property
    def writes(self) -> tuple[str, ...]:  # type: ignore[override]
        return (self.into,)

    def run(self, ctx: Context) -> Context:
        from browsergraph.crawl import Crawler, CrawlLimits

        seed = ctx.data.get(self.seed_key) or ""
        if not seed:
            ctx.fail(f"{self.name}: no seed url in ctx.data[{self.seed_key!r}]")
            return ctx

        crawler = Crawler(ctx.page, seed, CrawlLimits(
            max_pages=self.max_pages, max_depth=self.max_depth,
            delay=self.delay, respect_robots=self.respect_robots))

        collected: list[dict] = []
        for page, depth in crawler.pages():
            ctx.data["page"] = page
            ctx.data["page_url"] = page.url
            ctx.data["page_depth"] = depth
            for node in self.body:
                ctx = node.run(ctx)
                if ctx.failed:
                    ctx.note(f"{self.name}: body failed on {page.url}: {ctx.error}")
                    ctx.failed, ctx.error = False, ""
                    break
            collected.append({"url": page.url, "title": page.title,
                              "depth": depth, "words": page.word_count})

        ctx.data[self.into] = collected
        ctx.data[f"{self.into}_stats"] = crawler.report()
        ctx.note(f"{self.name}: crawled {len(collected)} page(s)")
        if crawler.stats.stopped_reason:
            ctx.note(f"{self.name}: stopped early — {crawler.stats.stopped_reason}")
        return ctx


@register
class Retry(Node):
    """Repeat a body until a predicate holds or attempts run out.

    Distinct from `Supervised`, which retries one node on failure: this retries
    a *sequence* until a condition is met — pagination, polling, waiting for a
    background job.
    """

    kind: ClassVar[str] = "retry_until"

    def __init__(self, body: list[Node], until: Callable[[dict], bool],
                 max_attempts: int = 3, name: str = "", describe: str = ""):
        super().__init__(name)
        self.body = list(body)
        self.until = until
        self.max_attempts = max_attempts
        self.describe_as = describe or "condition"
        self.mutates = any(n.mutates for n in self.body)
        self.verifies = True          # the predicate is the verification

    @property
    def writes(self) -> tuple[str, ...]:  # type: ignore[override]
        return tuple({w for n in self.body for w in n.writes})

    def run(self, ctx: Context) -> Context:
        for attempt in range(1, self.max_attempts + 1):
            for node in self.body:
                ctx = node.run(ctx)
                if ctx.failed:
                    break
            try:
                done = bool(self.until(ctx.data))
            except Exception as e:
                ctx.fail(f"{self.name}: predicate raised {type(e).__name__}: {e}")
                return ctx
            if done:
                ctx.note(f"{self.name}: {self.describe_as} met on attempt {attempt}")
                ctx.failed, ctx.error = False, ""
                return ctx
            if ctx.failed:
                ctx.failed, ctx.error = False, ""
            ctx.note(f"{self.name}: attempt {attempt}/{self.max_attempts}")
        ctx.fail(f"{self.name}: {self.describe_as} not met after "
                 f"{self.max_attempts} attempt(s)")
        return ctx
