"""The documentation has to still be true.

A diagram in a README drifts from the code the moment either changes, and a
diagram that used to be true is worse than none: a reader has no way to tell.
These render the real thing and compare.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from browsergraph import demo, viz

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"


def _fenced(text: str, language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)```", text, re.DOTALL)


def test_the_readme_diagram_is_what_the_code_actually_renders():
    blocks = _fenced(README.read_text(), "mermaid")
    assert blocks, "the README has lost its diagram"
    rendered = viz.to_mermaid(demo.tabular()).strip()
    assert rendered in [b.strip() for b in blocks], (
        "the README diagram no longer matches viz.to_mermaid(demo.tabular()).\n"
        "Regenerate it:\n\n" + rendered)


def test_the_readme_route_count_is_the_graphs_own():
    """Sixteen is a claim, and claims in prose are the ones that rot."""
    assert str(demo.tabular().route_count()) in README.read_text()


@pytest.mark.parametrize("path", sorted((ROOT / "notebooks").glob("build_*.py")),
                         ids=lambda p: p.stem)
def test_every_notebook_generator_has_a_notebook_to_show_for_it(path):
    """A generator whose output was never committed is a generator nobody runs."""
    import ast

    tree = ast.parse(path.read_text())
    names = {node.args[0].value
             for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "Notebook"
             and node.args and isinstance(node.args[0], ast.Constant)}
    if not names:
        pytest.skip("this generator builds no notebooks of its own")
    missing = [n for n in names if not (ROOT / "notebooks" / f"{n}.ipynb").exists()]
    assert not missing, f"{path.name} builds {missing} but they are not committed"


def test_the_test_count_badge_is_not_wildly_stale():
    """A badge is a claim. This one was 214 tests behind before anyone noticed.

    Deliberately a tolerance and not an equality: a test that fails whenever
    somebody adds a test is a test that gets deleted. Ten per cent is loose
    enough to survive a day's work and tight enough to catch a badge left over
    from a different version.
    """
    import subprocess
    import sys

    claimed = re.search(r"tests-(\d+)%20passing", README.read_text())
    assert claimed, "the README has lost its test-count badge"

    listing = subprocess.run(
        [sys.executable, "-m", "pytest", str(ROOT / "tests"), "--collect-only",
         "-q", "--no-header", "-p", "no:randomly"],
        capture_output=True, text=True, timeout=300, cwd=str(ROOT))
    found = sum(int(m) for m in re.findall(r"^tests/.*: (\d+)$",
                                           listing.stdout, re.MULTILINE))
    assert found, f"could not count the tests\n{listing.stdout[-800:]}"
    assert abs(found - int(claimed.group(1))) <= max(20, found * 0.1), (
        f"the badge says {claimed.group(1)} and there are {found}")
