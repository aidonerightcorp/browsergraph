"""Process-wide per-domain rate limiting.

`CrawlLimits.delay` is per-crawler, which means ten concurrent tasks against
one host produce ten independent one-second delays — i.e. ten requests per
second at the host. Politeness has to be enforced where the contention is: per
domain, across every crawler in the process.

Thread-safe, because parallel graph execution and multiple tasks share it.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse


def domain_of(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower()
    return host[4:] if host.startswith("www.") else host


@dataclass
class DomainPolicy:
    """Limits for one host."""
    min_interval: float = 1.0      # seconds between requests
    burst: int = 1                 # requests allowed back-to-back
    max_concurrent: int = 2        # simultaneous in-flight requests


@dataclass
class Limiter:
    """A shared, per-domain token gate.

    `acquire` blocks until the domain's policy permits a request. It is
    deliberately blocking rather than raising: a crawler that is told to slow
    down should slow down, not fail.
    """
    default: DomainPolicy = field(default_factory=DomainPolicy)
    policies: dict[str, DomainPolicy] = field(default_factory=dict)
    _last: dict[str, float] = field(default_factory=dict)
    _sem: dict[str, threading.Semaphore] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    waits: dict[str, float] = field(default_factory=dict)
    requests: dict[str, int] = field(default_factory=dict)

    def policy_for(self, domain: str) -> DomainPolicy:
        return self.policies.get(domain, self.default)

    def set_policy(self, domain: str, policy: DomainPolicy) -> None:
        with self._lock:
            self.policies[domain] = policy

    def observe_crawl_delay(self, domain: str, delay: float) -> None:
        """Honour a robots.txt Crawl-delay when it is stricter than ours."""
        if delay <= 0:
            return
        current = self.policy_for(domain)
        if delay > current.min_interval:
            self.set_policy(domain, DomainPolicy(
                min_interval=delay, burst=current.burst,
                max_concurrent=current.max_concurrent))

    def _semaphore(self, domain: str, policy: DomainPolicy) -> threading.Semaphore:
        with self._lock:
            if domain not in self._sem:
                self._sem[domain] = threading.Semaphore(max(1, policy.max_concurrent))
            return self._sem[domain]

    def acquire(self, url: str, sleep=time.sleep, clock=time.monotonic) -> float:
        """Block until this URL's domain may be requested. Returns seconds waited."""
        domain = domain_of(url)
        if not domain:
            return 0.0
        policy = self.policy_for(domain)
        sem = self._semaphore(domain, policy)
        sem.acquire()

        waited = 0.0
        with self._lock:
            last = self._last.get(domain)
            now = clock()
            if last is not None:
                gap = now - last
                if gap < policy.min_interval:
                    waited = policy.min_interval - gap
            self._last[domain] = now + waited
            self.requests[domain] = self.requests.get(domain, 0) + 1
            self.waits[domain] = self.waits.get(domain, 0.0) + waited
        if waited > 0:
            sleep(waited)
        return waited

    def release(self, url: str) -> None:
        domain = domain_of(url)
        sem = self._sem.get(domain)
        if sem is not None:
            sem.release()

    def report(self) -> dict:
        return {"domains": len(self.requests),
                "requests": dict(self.requests),
                "waited_sec": {d: round(w, 2) for d, w in self.waits.items()}}


#: Process-wide default. Crawlers use this unless given their own.
SHARED = Limiter()


class Gate:
    """Context manager around one request.

        with Gate(limiter, url):
            browser.goto(url)
    """

    def __init__(self, limiter: Limiter | None, url: str, sleep=time.sleep):
        self.limiter = limiter
        self.url = url
        self.sleep = sleep
        self.waited = 0.0

    def __enter__(self) -> Gate:
        if self.limiter is not None:
            self.waited = self.limiter.acquire(self.url, sleep=self.sleep)
        return self

    def __exit__(self, *exc) -> None:
        if self.limiter is not None:
            self.limiter.release(self.url)
