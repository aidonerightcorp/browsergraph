"""Page iteration: frontier + politeness + robots, over any BrowserPort.

Tasks that visit many pages share this rather than each reimplementing the
loop. Politeness is enforced here, once, so no task can accidentally hammer a
host — a per-task delay is a per-task bug waiting to happen.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from urllib.parse import urljoin

from browsergraph.errors import Failure, classify
from browsergraph.extract.content import Page, parse_page
from browsergraph.extract.links import Frontier, Robots
from browsergraph.throttle import SHARED, Gate, Limiter, domain_of


@dataclass
class CrawlLimits:
    max_pages: int = 25
    max_depth: int = 2
    delay: float = 1.0            # seconds between requests
    include_subdomains: bool = True
    respect_robots: bool = True
    stop_on_challenge: bool = True


@dataclass
class CrawlStats:
    fetched: int = 0
    failed: int = 0
    robots_blocked: int = 0
    stopped_reason: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"fetched": self.fetched, "failed": self.failed,
                "robots_blocked": self.robots_blocked,
                "stopped_reason": self.stopped_reason,
                "errors": self.errors[:10]}


class Crawler:
    """Walks a site through a BrowserPort, yielding parsed pages.

    Stops early on a challenge by default: a site that has served a CAPTCHA has
    already noticed, and continuing to crawl it is how an IP gets burned.
    """

    def __init__(self, browser, seed: str, limits: CrawlLimits | None = None,
                 allow=None, deny=None, sleep: Callable[[float], None] = time.sleep,
                 limiter: Limiter | None = SHARED):
        self.browser = browser
        self.seed = seed
        self.limits = limits or CrawlLimits()
        self.sleep = sleep
        # Per-domain, process-wide. A per-crawler delay lets ten concurrent
        # tasks make ten requests per second at the same host.
        self.limiter = limiter
        self.stats = CrawlStats()
        self.robots = Robots(respect=self.limits.respect_robots)
        self.frontier = Frontier(
            seed=seed, include_subdomains=self.limits.include_subdomains,
            max_depth=self.limits.max_depth, max_pages=self.limits.max_pages,
            allow=allow, deny=deny)

    def load_robots(self) -> Robots:
        if not self.limits.respect_robots:
            return self.robots
        try:
            self.browser.goto(urljoin(self.seed, "/robots.txt"))
            body = self.browser.html()
            if body and "<html" not in body[:200].lower():
                self.robots = Robots.parse(body)
                self.robots.respect = True
        except Exception:
            pass  # absent robots.txt is not an error
        return self.robots

    def pages(self) -> Iterator[tuple[Page, int]]:
        self.load_robots()
        delay = max(self.limits.delay, self.robots.crawl_delay)
        if self.limiter is not None:
            from browsergraph.throttle import DomainPolicy
            self.limiter.set_policy(domain_of(self.seed), DomainPolicy(min_interval=delay))
            self.limiter.observe_crawl_delay(domain_of(self.seed), self.robots.crawl_delay)
        first = True

        while True:
            item = self.frontier.pop()
            if item is None:
                break
            url, depth = item

            if not self.robots.allowed(url):
                self.stats.robots_blocked += 1
                continue

            if self.limiter is None:
                if not first and delay:
                    self.sleep(delay)
            first = False

            try:
                with Gate(self.limiter, url, sleep=self.sleep):
                    self.browser.goto(url)
                    html = self.browser.html()
            except Exception as e:
                self.stats.failed += 1
                self.stats.errors.append(f"{url}: {type(e).__name__}: {e}")
                diag = classify(f"{type(e).__name__}: {e}")
                if diag.failure in (Failure.CHALLENGE, Failure.BLOCKED):
                    self.stats.stopped_reason = str(diag)
                    return
                continue

            page = parse_page(html, url)

            if self.limits.stop_on_challenge:
                diag = classify("page check", page.text[:2000])
                if diag.failure is Failure.CHALLENGE:
                    self.stats.stopped_reason = f"challenge at {url}"
                    return

            self.stats.fetched += 1
            yield page, depth
            self.frontier.extend(page.links, depth + 1)

    def report(self) -> dict:
        return {**self.stats.to_dict(), "frontier": self.frontier.report(),
                "throttle": self.limiter.report() if self.limiter else {},
                "robots": {"respected": self.robots.respect,
                           "disallow_rules": len(self.robots.disallow),
                           "crawl_delay": self.robots.crawl_delay}}
