"""The dimension space, drawn as parallel planes.

Most of these tests are about the diagram not lying, because that is the only
way a diagram can fail badly. It renders either way; the question is whether
what it shows is true.
"""
from __future__ import annotations

import re

import pytest

from browsergraph.dimensions import Binary, Display, Engine, Stealth
from browsergraph.spacemap import PLANES, explore, to_html, to_text


@pytest.fixture(scope="module")
def small():
    """A space small enough to enumerate exhaustively, so counts are exact."""
    return explore({"engine": list(Engine), "binary": list(Binary),
                    "display": list(Display), "stealth": list(Stealth)})


# --- the space --------------------------------------------------------------

def test_a_small_space_is_enumerated_exhaustively(small):
    assert "exhaustive" in small.rule
    assert small.examined == small.total == 15 * 6 * 4 * 5


def test_every_drawn_path_is_actually_runnable(small):
    """The whole claim of the picture."""
    from browsergraph.dimensions import validate
    from browsergraph.spacemap import _spec_from, ground_spec
    names = [n for n, _ in small.planes]
    enums = dict(PLANES)
    for path in small.paths[:200]:
        choice = {n: enums[n](v) for n, v in zip(names, path, strict=True)}
        assert validate(_spec_from(choice, ground_spec())) == []


def test_a_sampled_space_says_so_and_stays_unbiased():
    """The first implementation took the first N in enumeration order.

    `itertools.product` varies the last axis fastest, so that sample was every
    path through playwright/bundled_chromium and nothing else — and the diagram
    rendered every other engine as unreachable. They are reachable.
    """
    space = explore(limit=400)
    assert "random" in space.rule and space.truncated

    counts = space.value_counts()
    engines_seen = {v for (plane, v), n in counts.items()
                    if plane == "engine" and n > 0}
    assert len(engines_seen) >= 8, f"sample is biased toward {engines_seen}"

    binaries_seen = {v for (plane, v), n in counts.items()
                     if plane == "binary" and n > 0}
    assert len(binaries_seen) >= 4, f"sample is biased toward {binaries_seen}"


def test_sampling_is_deterministic():
    assert explore(limit=120, seed=3).paths == explore(limit=120, seed=3).paths
    assert explore(limit=120, seed=3).paths != explore(limit=120, seed=4).paths


def test_values_needing_a_sibling_field_are_not_called_unreachable():
    """`transport=remote_cdp` needs an endpoint, `capture=video` a directory.

    Judged against a bare Spec they look impossible, which is false and tells a
    reader they cannot use video at all.
    """
    counts = explore(limit=800).value_counts()
    assert counts[("transport", "remote_cdp")] > 0
    assert counts[("capture", "video")] > 0
    assert counts[("display", "vnc")] > 0


def test_a_constraining_choice_carries_fewer_paths(small):
    """stealth=undetected needs an evasion engine, so it must look expensive."""
    counts = small.value_counts()
    assert counts[("stealth", "undetected")] < counts[("stealth", "none")]


def test_every_plane_is_represented(small):
    assert [n for n, _ in small.planes] == ["engine", "binary", "display", "stealth"]
    assert all(values for _, values in small.planes)


def test_summary_reports_totals_honestly(small):
    text = small.summary()
    assert f"{small.valid:,}" in text and f"{small.total:,}" in text


def test_text_view_marks_unreachable_values_explicitly():
    space = explore({"engine": [Engine.HTTP], "binary": list(Binary),
                     "display": list(Display), "stealth": list(Stealth)})
    out = to_text(space)
    assert "unreachable" in out, "a value with no paths must say so"


# --- the rendering ----------------------------------------------------------

def test_html_is_self_contained(small):
    html = to_html(small)
    assert "<svg" in html and "<script" in html and "<style" in html
    assert not re.findall(r'(?:src|href)="https?://', html), "external reference"


def test_every_value_is_drawn_even_with_no_paths():
    space = explore({"engine": [Engine.HTTP], "binary": list(Binary),
                     "display": list(Display), "stealth": list(Stealth)})
    html = to_html(space)
    for _, values in space.planes:
        for v in values:
            assert f'data-value="{v}"' in html, f"{v} missing from the diagram"


def test_paths_run_through_their_values_not_between_centres(small):
    """Centre-to-centre lines are hidden behind the labels — unreadable."""
    html = to_html(small)
    points = re.search(r'points="([^"]+)"', html)
    assert points
    coords = points.group(1).split()
    assert len(coords) == 2 * len(small.planes), \
        "expected a left and right edge point per plane"


def test_a_truncated_render_says_so():
    space = explore(limit=50)
    assert space.truncated
    assert "truncated" in to_html(space)


def test_render_survives_a_single_plane():
    space = explore({"engine": list(Engine)})
    assert "<svg" in to_html(space)


def test_ids_are_safe_for_html_attributes(small):
    html = to_html(small)
    for ident in re.findall(r'data-id="([^"]+)"', html):
        assert re.fullmatch(r"[A-Za-z0-9_]+", ident), ident
