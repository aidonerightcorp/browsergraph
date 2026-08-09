"""Task library — named, parameterised capabilities.

Importing this registers the built-in tasks.
"""
from browsergraph.tasks import library  # noqa: F401  (registration side-effect)
from browsergraph.tasks.base import (
    CRAWL_PARAMS,
    REGISTRY,
    Task,
    TaskResult,
    catalog,
    make,
    register,
)

__all__ = ["CRAWL_PARAMS", "REGISTRY", "Task", "TaskResult", "catalog", "make",
           "register", "library"]
