"""Pairwise (all-pairs) sampling over the dimension space.

Full enumeration stops being useful quickly — six axes already give thousands
of combinations, and the axes in DIMENSIONS.md take it past 10^9. Most real
failures come from an interaction between *two* values, so a covering array
that exercises every pair reduces a billion runs to a few dozen while still
catching those interactions.

Greedy construction: not provably minimal, but deterministic, dependency-free
and typically within a few rows of optimal.
"""
from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping
from dataclasses import replace

from browsergraph.dimensions import Spec, is_valid


def all_pairs(axes: Mapping[str, Iterable]) -> list[dict]:
    """Rows covering every value-pair across axes at least once."""
    axis = {k: list(v) for k, v in axes.items() if list(v)}
    names = list(axis)
    if len(names) < 2:
        return [{n: v} for n in names for v in axis[n]]

    uncovered: set[tuple] = set()
    for a, b in itertools.combinations(names, 2):
        for va in axis[a]:
            for vb in axis[b]:
                uncovered.add((a, va, b, vb))

    rows: list[dict] = []
    while uncovered:
        row: dict = {}
        # seed from an uncovered pair so every row makes progress
        a, va, b, vb = sorted(uncovered, key=lambda t: (str(t[0]), str(t[1])))[0]
        row[a], row[b] = va, vb
        for n in names:
            if n in row:
                continue
            row[n] = max(
                axis[n],
                key=lambda cand: sum(
                    1 for other in row
                    if (n, cand, other, row[other]) in uncovered
                    or (other, row[other], n, cand) in uncovered
                ),
            )
        for x, y in itertools.combinations(names, 2):
            uncovered.discard((x, row[x], y, row[y]))
            uncovered.discard((y, row[y], x, row[x]))
        rows.append(row)
        if len(rows) > 10_000:  # pragma: no cover - runaway guard
            break
    return rows


def sample_specs(axes: Mapping[str, Iterable], base: Spec | None = None,
                 valid_only: bool = True, top_up: bool = True) -> list[Spec]:
    """Pairwise-covering specs, restricted to runnable ones.

    Generating a covering array and then discarding invalid rows silently loses
    coverage — the discarded rows were carrying pairs. When `top_up` is set,
    pairs orphaned by that filtering are re-covered by searching the valid
    subspace, so the reported coverage is real rather than an artifact of
    generate-then-filter.
    """
    base = base or Spec()
    specs = [replace(base, **row) for row in all_pairs(axes)]
    if not valid_only:
        return specs

    specs = [s for s in specs if is_valid(s)]
    if not top_up:
        return specs

    axis = {k: list(v) for k, v in axes.items() if list(v)}
    names = list(axis)
    covered, _ = _pair_sets(axis, specs)

    # Which pairs are reachable at all? A pair only counts as missing if some
    # valid spec could carry it.
    for a, b in itertools.combinations(names, 2):
        for va in axis[a]:
            for vb in axis[b]:
                if (a, va, b, vb) in covered:
                    continue
                found = _find_valid_with(axis, base, {a: va, b: vb})
                if found is not None:
                    specs.append(found)
                    new_cov, _ = _pair_sets(axis, [found])
                    covered |= new_cov
    return specs


def _pair_sets(axis: dict[str, list], specs: list[Spec]) -> tuple[set, set]:
    names = list(axis)
    covered: set[tuple] = set()
    for s in specs:
        d = {n: getattr(s, n) for n in names}
        for a, b in itertools.combinations(names, 2):
            covered.add((a, d[a], b, d[b]))
    return covered, set()


def _find_valid_with(axis: dict[str, list], base: Spec, fixed: dict) -> Spec | None:
    """Cheapest valid spec containing `fixed`, or None if the pair is unreachable."""
    free = [n for n in axis if n not in fixed]
    for values in itertools.product(*(axis[n] for n in free)):
        cand = replace(base, **{**fixed, **dict(zip(free, values, strict=True))})
        if is_valid(cand):
            return cand
    return None


def coverage(axes: Mapping[str, Iterable], specs: list[Spec]) -> tuple[int, int]:
    """(pairs covered, pairs possible) — report this rather than implying full coverage."""
    axis = {k: list(v) for k, v in axes.items() if list(v)}
    names = list(axis)
    possible: set[tuple] = set()
    for a, b in itertools.combinations(names, 2):
        for va in axis[a]:
            for vb in axis[b]:
                possible.add((a, va, b, vb))
    covered: set[tuple] = set()
    for s in specs:
        d = {n: getattr(s, n) for n in names}
        for a, b in itertools.combinations(names, 2):
            if (a, d[a], b, d[b]) in possible:
                covered.add((a, d[a], b, d[b]))
    return len(covered), len(possible)
