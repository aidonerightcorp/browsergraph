"""Task layer — named, parameterised capabilities.

A graph describes *how*; a task describes *what*. "Spider this site" or
"classify this business" is the unit a caller actually wants, and it maps to a
crawl plus extraction rather than to a single DAG.

Every task declares its parameters, so a bad request fails at submission
rather than mid-crawl, and every result carries provenance: which URLs were
visited, what was skipped, and how confident the output is.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, ClassVar

from browsergraph.crawl import Crawler, CrawlLimits
from browsergraph.params import ParamSet


@dataclass
class TaskResult:
    task: str
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    pages_visited: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    elapsed: float = 0.0

    def to_dict(self) -> dict:
        return {"task": self.task, "ok": self.ok, "data": self.data,
                "pages_visited": self.pages_visited[:200],
                "page_count": len(self.pages_visited), "stats": self.stats,
                "warnings": self.warnings, "error": self.error,
                "elapsed_sec": round(self.elapsed, 2)}


class Task:
    """Base class. Subclasses declare `name`/`params` and implement `execute`."""

    name: ClassVar[str] = "task"
    summary: ClassVar[str] = ""
    param_spec: ClassVar[list[dict]] = []

    def __init__(self, **values: Any) -> None:
        self.params = ParamSet.from_list(self.param_spec)
        self.values = self.params.resolve(values)

    # -- helpers available to every task
    def limits(self) -> CrawlLimits:
        v = self.values
        return CrawlLimits(
            max_pages=int(v.get("max_pages", 25)),
            max_depth=int(v.get("max_depth", 2)),
            delay=float(v.get("delay", 1.0)),
            include_subdomains=bool(v.get("include_subdomains", True)),
            respect_robots=bool(v.get("respect_robots", True)),
        )

    def crawler(self, browser, seed: str = "", **kw) -> Crawler:
        return Crawler(browser, seed or self.values["url"], self.limits(), **kw)

    # -- contract
    def execute(self, browser) -> TaskResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def run(self, browser) -> TaskResult:
        started = time.time()
        try:
            result = self.execute(browser)
        except Exception as e:
            result = TaskResult(task=self.name, ok=False,
                                error=f"{type(e).__name__}: {e}")
        result.elapsed = time.time() - started
        return result

    @classmethod
    def describe(cls) -> dict:
        return {"name": cls.name, "summary": cls.summary,
                "params": cls.param_spec}


# --- registry ---------------------------------------------------------------

REGISTRY: dict[str, type[Task]] = {}


def register(cls: type[Task]) -> type[Task]:
    if cls.name in REGISTRY and REGISTRY[cls.name] is not cls:
        raise ValueError(f"duplicate task name: {cls.name}")
    REGISTRY[cls.name] = cls
    return cls


def make(name: str, **values) -> Task:
    if name not in REGISTRY:
        raise KeyError(f"unknown task {name!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[name](**values)


def catalog() -> list[dict]:
    return [cls.describe() for cls in sorted(REGISTRY.values(), key=lambda c: c.name)]


#: Parameters every crawling task accepts.
CRAWL_PARAMS: list[dict] = [
    {"name": "url", "type": "url", "description": "site or page to start from"},
    {"name": "max_pages", "type": "int", "required": False, "default": 25},
    {"name": "max_depth", "type": "int", "required": False, "default": 2},
    {"name": "delay", "type": "float", "required": False, "default": 1.0,
     "description": "seconds between requests"},
    {"name": "include_subdomains", "type": "bool", "required": False, "default": True},
    {"name": "respect_robots", "type": "bool", "required": False, "default": True},
]
