"""Node protocol and registry.

A node is a named, typed unit of work over a `Context`. Nodes declare what they
read and write so a graph can be checked before it runs, rather than failing
halfway through a browser session.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from browsergraph.ports import Context


class Node:
    """Base class. Subclasses implement `run` and declare `reads`/`writes`."""

    kind: ClassVar[str] = "node"
    reads: ClassVar[tuple[str, ...]] = ()
    writes: ClassVar[tuple[str, ...]] = ()
    needs_browser: ClassVar[bool] = True

    #: Semantics the linter reasons about.
    mutates: ClassVar[bool] = False    # changes remote state (click, type, submit)
    verifies: ClassVar[bool] = False   # checks an outcome
    interacts: ClassVar[bool] = False  # touches an element, so needs it present
    uses_llm: ClassVar[bool] = False
    selector: str = ""                 # set by nodes that target an element

    def __init__(self, name: str = "") -> None:
        self.name = name or self.kind

    def run(self, ctx: Context) -> Context:  # pragma: no cover - abstract
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{self.kind}:{self.name}>"


REGISTRY: dict[str, type[Node]] = {}


def register(cls: type[Node]) -> type[Node]:
    """Register a node class under its `kind`, for config-driven graph building."""
    if cls.kind in REGISTRY and REGISTRY[cls.kind] is not cls:
        raise ValueError(f"duplicate node kind: {cls.kind}")
    REGISTRY[cls.kind] = cls
    return cls


def make(kind: str, **kwargs) -> Node:
    """Instantiate a registered node by kind."""
    if kind not in REGISTRY:
        raise KeyError(f"unknown node kind {kind!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[kind](**kwargs)


@dataclass
class FnNode(Node):
    """Wrap a plain function as a node — for one-off steps not worth a class."""
    fn: Callable[[Context], Context] = lambda c: c
    kind: ClassVar[str] = "fn"
    name: str = "fn"
    needs_browser: ClassVar[bool] = False

    def run(self, ctx: Context) -> Context:
        return self.fn(ctx)
