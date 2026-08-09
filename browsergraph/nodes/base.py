"""Node protocol and registry.

A node is a named, typed unit of work over a `Context`. Nodes declare what they
read and write so a graph can be checked before it runs, rather than failing
halfway through a browser session.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from browsergraph.contracts import Contract, ContractError, check_class, contract_of
from browsergraph.ports import Context


class Node:
    """Base class. Subclasses implement `run` and declare `reads`/`writes`."""

    kind: ClassVar[str] = "node"
    reads: ClassVar[tuple[str, ...]] = ()
    writes: ClassVar[tuple[str, ...]] = ()
    #: Not ClassVar for the same reason as the flags below: wrappers and
    #: composites take their value from what they wrap.
    needs_browser: bool = True

    #: Semantics the linter reasons about.
    #:
    #: Deliberately *not* ClassVar: composite nodes (Branch, Loop, Retry,
    #: HealingNode) derive these per instance by aggregating their children, so
    #: a wrapped Click still reports `mutates`. BG003 — "mutation without
    #: verification" — reads these flags, and it would silently stop firing on
    #: every wrapped node if the values could not vary per instance.
    #: Subclasses still set class-level defaults; only the annotation changed.
    mutates: bool = False              # changes remote state (click, type, submit)
    verifies: bool = False             # checks an outcome
    interacts: bool = False            # touches an element, so needs it present
    uses_llm: bool = False
    selector: str = ""                 # set by nodes that target an element

    #: Set on intermediate base classes that are not themselves runnable.
    abstract: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs) -> None:
        """Enforce the node contract when the class is defined.

        Deliberately at import time rather than at registration: an unregistered
        node used directly in a graph is just as capable of lying about itself,
        and a malformed declaration silently disables the linter rules built on
        top of it. Failing here means the traceback points at the offending
        `class` statement instead of at a browser session thirty seconds in.

        Set `abstract = True` on a base class that exists only to be subclassed.
        """
        super().__init_subclass__(**kwargs)
        cls.abstract = cls.__dict__.get("abstract", False)
        problems = check_class(cls)
        if problems:
            raise ContractError(
                f"{cls.__name__} violates the node contract:\n  - "
                + "\n  - ".join(problems))

    def __init__(self, name: str = "") -> None:
        self.name = name or self.kind

    def contract(self) -> Contract:
        """This node's contract, read from the instance.

        Instance-level because composite nodes derive their flags from their
        children — see `contracts.contract_of`.
        """
        return contract_of(self)

    def run(self, ctx: Context) -> Context:  # pragma: no cover - abstract
        raise NotImplementedError

    run.__isabstractnode__ = True    # type: ignore[attr-defined]

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
    needs_browser: bool = False

    def run(self, ctx: Context) -> Context:
        return self.fn(ctx)
