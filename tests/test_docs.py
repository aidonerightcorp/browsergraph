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


def test_the_changelog_covers_the_version_being_shipped():
    """A changelog a version behind is worse than none: a reader diffing two
    releases sees the older one's notes and concludes nothing changed."""
    from browsergraph._version import __version__

    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert f"## [{__version__}]" in changelog, (
        f"_version.py says {__version__} and CHANGELOG.md has no entry for it")


def test_every_public_viz_figure_is_named_in_the_module_docstring():
    """The docstring lists the pictures and says how many. Adding one without
    listing it is how the count drifted from four to seven unannounced."""
    named = {name for name in dir(viz)
             if not name.startswith("_")
             and callable(getattr(viz, name))
             and getattr(getattr(viz, name), "__module__", "") == viz.__name__
             and getattr(viz, name).__doc__
             and "Figure" in str(getattr(viz, name).__annotations__.get("return", ""))}
    missing = [n for n in named if f"`{n}`" not in (viz.__doc__ or "")]
    assert not missing, f"undocumented figures: {sorted(missing)}"


def test_every_documented_command_exists():
    """A README that names a command the parser does not have is worse than a
    README with no commands in it — the reader blames their install."""
    import re as _re
    import subprocess
    import sys

    text = (ROOT / "AGENTS.md").read_text() + (ROOT / "README.md").read_text()
    named = {m for m in _re.findall(r"^browsergraph (\w[\w-]*)", text, _re.MULTILINE)}
    assert named, "no commands documented, so this proves nothing"

    listing = subprocess.run([sys.executable, "-m", "browsergraph.cli", "--help"],
                             capture_output=True, text=True, timeout=120,
                             cwd=str(ROOT))
    # argparse prints the subcommands as one comma-separated brace group. An
    # earlier version of this scanned for `[\s,{](name)[,}]`, which consumed the
    # separator it needed for the next match and so saw every other command —
    # then reported half the README as undocumented.
    group = _re.search(r"\{([a-z][a-z,-]+)\}", listing.stdout)
    assert group, f"could not read the command list\n{listing.stdout[:600]}"
    real = set(group.group(1).split(","))

    missing = sorted(n for n in named if n not in real)
    assert not missing, (
        f"documented but not a command: {missing}\nreal: {sorted(real)}")


def test_the_dockerfile_copies_every_package_the_build_declares():
    """The image build fails at `pip install .` otherwise, and only on a tag.

    `pyproject.toml` gained `solutiongraph` and the Dockerfile kept copying only
    `browsergraph`, so every image build died with "package directory
    'solutiongraph' does not exist" — invisible until a release was cut, because
    that is the only time the image is built.
    """
    import re as _re

    manifest = (ROOT / "pyproject.toml").read_text()
    block = _re.search(r"packages\s*=\s*\[(.*?)\]", manifest, _re.DOTALL)
    assert block, "pyproject no longer lists packages explicitly"
    tops = {name.split(".")[0]
            for name in _re.findall(r'"([\w.]+)"', block.group(1))}

    dockerfile = (ROOT / "Dockerfile").read_text()
    copied = set(_re.findall(r"^COPY\s+([\w-]+)\s+\./", dockerfile, _re.MULTILINE))
    missing = sorted(tops - copied)
    assert not missing, (
        f"pyproject declares {missing} but the Dockerfile never copies them")
