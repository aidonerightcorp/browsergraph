"""The examples have to run.

An example that no longer works is worse than no example: it is the first thing
a newcomer tries, and it fails on their machine rather than in a log. These run
each one and check its exit code — slow-ish, and cheaper than the alternative.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

EXAMPLES = sorted((pathlib.Path(__file__).resolve().parent.parent
                   / "examples").glob("0*.py"))


def test_there_are_examples_to_run():
    """A glob that matches nothing makes every test below pass silently."""
    assert len(EXAMPLES) >= 5


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_an_example_runs_and_exits_cleanly(path):
    done = subprocess.run([sys.executable, str(path)], capture_output=True,
                          text=True, timeout=180,
                          cwd=str(path.parent.parent))
    assert done.returncode == 0, (
        f"{path.name} exited {done.returncode}\n"
        f"{done.stdout[-1500:]}\n{done.stderr[-1500:]}")
    assert done.stdout.strip(), f"{path.name} printed nothing"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_an_example_that_starts_a_subprocess_guards_its_main(path):
    """`spawn` re-imports the module, so a bounded call at import time runs the
    whole script once per child and hangs. I wrote example 04 without the guard
    first and it timed out."""
    source = path.read_text()
    if "bound(" not in source:
        pytest.skip("no bounded execution in this example")
    assert '__name__ == "__main__"' in source
