"""Whether two ports may actually be connected.

Until now this was `==` on a string, which makes substitutability a promise
rather than a property. That gives three failures, all of them silent:

* **No subtyping.** `CsvRecords` cannot be offered where `Records` is wanted,
  so either every producer declares the general type and loses information, or
  nothing connects.
* **No semantics.** Two nodes producing `text/plain` — one an address, one a
  summary — are declared interchangeable and are not. The manifest already had
  a `semantic` field and *nothing read it*, which is worse than not having it,
  because it looks like the problem is handled.
* **No units.** A latency in seconds and a latency in milliseconds are the same
  type, and the graph will happily connect them.

Liskov's rule is the one being broken: candidates in a stage are claimed to be
substitutable, and substitutability is a statement about behaviour under a
contract, not about a matching label.

The design here is deliberately small. A full type system is not the goal and
would not be used; what is needed is enough structure to make the common wrong
connection impossible, and to make every rejection say what to do instead.

Compatibility is `producer ⊑ consumer` — the producer's type must be the
consumer's type or a descendant of it. Widening is safe (a `CsvRecords` is a
`Records`); narrowing is not, and needs an explicit adapter, which is a node
like any other.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: `List[Record]` and friends. Parametric containers matter because "a list of
#: the wrong thing" is the single most common shape error, and string equality
#: cannot see inside the brackets.
GENERIC = re.compile(r"^([A-Za-z_][\w.]*)\s*\[\s*(.+?)\s*\]$")

#: Conversions that are always safe, in the direction shown. Deliberately tiny:
#: an implicit conversion nobody declared is exactly the coercion this project
#: refuses elsewhere.
BUILTIN: dict[str, tuple[str, ...]] = {
    "int": ("float", "number"),
    "float": ("number",),
    "bool": ("int", "float", "number"),
}


@dataclass
class Lattice:
    """Declared subtype relations, and the questions asked of them.

    A lattice rather than a hierarchy because a type may have several parents:
    `CsvRecords` is both `Records` and `Tabular`, and forcing a single parent
    would make one of those a lie.
    """
    parents: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Semantic tags that are known to be interchangeable, both directions.
    aliases: dict[str, str] = field(default_factory=dict)

    @classmethod
    def with_builtins(cls) -> Lattice:
        return cls(parents={k: v for k, v in BUILTIN.items()})

    def declare(self, child: str, *parents: str) -> Lattice:
        existing = set(self.parents.get(child, ()))
        self.parents[child] = tuple(existing | set(parents))
        return self

    def ancestors(self, name: str, seen: set[str] | None = None) -> set[str]:
        seen = seen if seen is not None else set()
        for parent in self.parents.get(name, ()):
            if parent in seen:
                continue          # a declared cycle is a mistake, not a hang
            seen.add(parent)
            self.ancestors(parent, seen)
        return seen

    # --- the question everything else is built on ---------------------------

    def is_a(self, produced: str, expected: str) -> bool:
        """Can a value of `produced` be used where `expected` is wanted?

        Reflexive, then declared ancestry, then structural for containers:
        `List[CsvRecord]` satisfies `List[Record]` when `CsvRecord` satisfies
        `Record`, which is the whole reason to parse the brackets.
        """
        if not produced or not expected or produced == expected:
            return True
        if expected in ("any", "Any"):
            return True
        if expected in self.ancestors(produced):
            return True
        left, right = GENERIC.match(produced), GENERIC.match(expected)
        if left and right:
            outer_ok = (left.group(1) == right.group(1)
                        or right.group(1) in self.ancestors(left.group(1)))
            return outer_ok and self.is_a(left.group(2), right.group(2))
        return False


@dataclass(frozen=True)
class Mismatch:
    """Why two ports may not be joined, and what to do about it."""
    reason: str
    fix: str = ""

    def __str__(self) -> str:
        return f"{self.reason}" + (f" — {self.fix}" if self.fix else "")


def check(produced, expected, lattice: Lattice | None = None) -> Mismatch | None:
    """Compare two `PortSpec`s. `None` means they connect.

    Three checks in increasing subtlety, and each reports the specific thing
    that is wrong rather than "incompatible types":

    1. the carrier type, with subtyping;
    2. the **semantic** type, because the same carrier can mean different
       things and connecting those is how an address ends up in a summary field;
    3. **units**, because seconds and milliseconds are the same type and one of
       them is wrong by a factor of a thousand.
    """
    lattice = lattice or Lattice.with_builtins()
    if not lattice.is_a(produced.type, expected.type):
        return Mismatch(
            f"{produced.type!r} is not a {expected.type!r}",
            "declare the subtype relation if it holds, or insert an adapter node")
    left = lattice.aliases.get(produced.semantic, produced.semantic)
    right = lattice.aliases.get(expected.semantic, expected.semantic)
    if left and right and left != right:
        return Mismatch(
            f"both carry {produced.type!r} but one means {left!r} and the "
            f"other {right!r}",
            "the carrier matching is a coincidence; insert an adapter or "
            "correct the semantic tag")
    if produced.units and expected.units and produced.units != expected.units:
        return Mismatch(
            f"units differ: {produced.units!r} into {expected.units!r}",
            "insert a converting node — this is a factor, not a formatting "
            "difference")
    return None


def element_of(type_name: str) -> str:
    """`List[Row]` -> `Row`. Anything else comes back unchanged.

    A map stage and the node inside it talk about different things. The stage
    consumes and produces *collections*; the node handles one item. Writing the
    stage as `List[Row]` and the node as `Row` says that exactly, and this is
    the one line that connects them.
    """
    text = (type_name or "").strip()
    for prefix in ("List[", "list[", "Sequence[", "Iterable["):
        if text.startswith(prefix) and text.endswith("]"):
            return text[len(prefix):-1].strip()
    return text


def lattice_from(manifests: Iterable, extra: Mapping[str, Iterable[str]] | None = None
                 ) -> Lattice:
    """Build a lattice from `is_a` declarations carried on node manifests.

    Types are declared where they are used rather than in a central registry,
    so a domain pack can bring its own without editing anything shared — which
    is the property that lets this be extended by people who are not us.
    """
    lattice = Lattice.with_builtins()
    for manifest in manifests:
        for child, parents in (getattr(manifest, "runtime", {}) or {}).get(
                "is_a", {}).items():
            lattice.declare(child, *(parents if isinstance(parents, (list, tuple))
                                     else [parents]))
    for child, parents in (extra or {}).items():
        lattice.declare(child, *parents)
    return lattice


# --- run-time checking at the boundary --------------------------------------

#: What a declared type actually looks like in Python. Only the shapes worth
#: checking cheaply: a full runtime type system would cost more than the bugs
#: it caught, and would be skipped for being slow.
RUNTIME: dict[str, tuple[type, ...]] = {
    "str": (str,), "text": (str,), "text/plain": (str,),
    "int": (int,), "float": (int, float), "number": (int, float),
    "bool": (bool,),
    "Records": (list, tuple), "List": (list, tuple),
    "dict": (dict,), "Mapping": (dict,), "json": (dict, list, str, int, float, bool),
}


class PortViolation(TypeError):
    """A node produced something its own manifest says it does not produce.

    Attributed to the node that produced it, which is the entire point. Without
    a boundary check the failure surfaces three steps later inside whatever
    choked on the value, and the traceback names the victim rather than the
    culprit.
    """


def runtime_check(value, port, *, node: str = "", lattice: Lattice | None = None
                  ) -> str:
    """Does this value plausibly satisfy this port? "" if it does.

    Deliberately shallow — the element type of a large list is not checked,
    because a validator that doubles the cost of a pipeline gets turned off,
    and a validator that is turned off catches nothing. Shape, emptiness and
    obvious nonsense are where the real errors are.
    """
    declared = (port.type or "").strip()
    if not declared or declared in ("any", "Any"):
        return ""
    if value is None:
        return ("" if not port.required
                else f"{node or 'node'} produced None for required port "
                     f"{port.name!r} ({declared})")
    base = GENERIC.match(declared)
    lookup = base.group(1) if base else declared
    expected = RUNTIME.get(lookup)
    if expected is None:
        return ""              # an unknown type is not a violation, just unchecked
    if isinstance(value, bool) and expected == (int,):
        return (f"{node or 'node'} produced a bool for {port.name!r}, "
                f"declared {declared}")
    if not isinstance(value, expected):
        return (f"{node or 'node'} produced {type(value).__name__} for port "
                f"{port.name!r}, which declares {declared}")
    return ""


def guard(ports, values: Mapping[str, Any], *, node: str = "",
          lattice: Lattice | None = None) -> list[str]:
    """Check every declared output port against what was actually produced."""
    out = []
    for port in ports:
        problem = runtime_check(values.get(port.name), port, node=node,
                                lattice=lattice)
        if problem:
            out.append(problem)
    return out
