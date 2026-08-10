"""Every notebook cell must at least be Python.

This exists because of one line that shipped to Kaggle:

    print(f'
    wrote {OUT}/proposal.json')

The generator wrote `print(f'\\nwrote ...')` inside a triple-quoted block, the
`\\n` was consumed by the *outer* string, and the notebook got a literal newline
inside a single-quoted f-string. That is a syntax error, so the cell could never
run — and it sat in the published tour notebook, which is the first thing a
stranger sees.

Nothing caught it because the tests exercise the library and the notebooks are
generated data. Parsing them is cheap and turns a class of "committed something
that cannot run" into a test failure.
"""
from __future__ import annotations

import ast
import json
import pathlib

import pytest

NOTEBOOKS = sorted((pathlib.Path(__file__).resolve().parent.parent
                    / "notebooks").glob("*.ipynb"))


def _plain_python(source: str) -> str:
    """Replace IPython magics with `pass`, keeping their indentation.

    Blanking them instead looks simpler and is wrong: the install cells put a
    magic inside an `except ImportError:` block, and deleting the only
    statement in a block is itself a syntax error. The test would then report
    every notebook as broken and be ignored, which is worse than not having it.
    """
    out = []
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("%", "!")):
            out.append(" " * (len(line) - len(stripped)) + "pass")
        else:
            out.append(line)
    return "\n".join(out)


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
def test_every_code_cell_parses(path):
    book = json.loads(path.read_text())
    for index, cell in enumerate(book["cells"]):
        if cell["cell_type"] != "code":
            continue
        source = _plain_python("".join(cell["source"]))
        try:
            ast.parse(source)
        except SyntaxError as problem:
            pytest.fail(f"{path.name} cell {index} does not parse: "
                        f"{problem.msg} (line {problem.lineno})\n"
                        + source[:400])


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
def test_no_cell_holds_a_literal_newline_inside_a_quoted_fstring(path):
    """The exact shape of the bug, caught by shape rather than by parsing.

    Parsing already rejects it, but this says *what* is wrong when it recurs,
    which is a generator escaping problem and not a typo in the notebook.
    """
    import re

    book = json.loads(path.read_text())
    for index, cell in enumerate(book["cells"]):
        if cell["cell_type"] != "code":
            continue
        source = "".join(cell["source"])
        for quote in ("'", '"'):
            # The lookbehind matters. Without it this matches the `f` in a
            # format spec — `f"{x:>9.4f}"` ends in `4f"` — and every notebook
            # with a formatted float gets reported as broken.
            pattern = rf"(?<![\w.]){quote and 'f'}{quote}[^{quote}\n]*\n"
            for match in re.finditer(pattern, source):
                # A triple-quoted f-string may legitimately span lines.
                if quote * 3 in match.group(0):
                    continue
                pytest.fail(
                    f"{path.name} cell {index}: an f-string opened with "
                    f"{quote} runs to the end of the line — the generator "
                    f"probably wrote \\n and had it consumed by its own "
                    f"outer string. Use a separate print() or escape it.")


def test_there_are_notebooks_to_check():
    """A glob that matches nothing makes every test above pass silently."""
    assert len(NOTEBOOKS) >= 20
