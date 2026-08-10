"""Run every notebook for real and write the outputs back in.

A notebook committed with zero outputs is a claim, not a demonstration. This
script is what turns it into the second thing, and it fails loudly rather than
writing a half-executed file, because a notebook whose cell 6 raised and whose
cells 7-10 are blank looks almost exactly like one that simply has short output.

    python notebooks/execute.py            # all of them
    python notebooks/execute.py 01 03      # the ones whose names contain these
"""
from __future__ import annotations

import os
import pathlib
import resource
import sys
import time

import nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError

HERE = pathlib.Path(__file__).resolve().parent
TIMEOUT = 600

#: A notebook cell that enumerates a combinatorial space is one typo away from
#: asking for every route in it, and the machine cannot say no. This is not
#: hypothetical: `compare_strategies` on the demonstration workbench requested
#: enumeration of 3.8 trillion routes, and the kernel reached 53GB on a 61GB box
#: before it was killed by hand. A cap turns that into a MemoryError in one
#: cell, which is a test result rather than an outage.
MEMORY_CAP_GB = int(os.environ.get("BG_NOTEBOOK_MEMORY_GB", "8"))


def _cap_memory() -> None:
    """Applied in this process, and inherited by the kernels it launches."""
    cap = MEMORY_CAP_GB * 1024**3
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard != resource.RLIM_INFINITY:
        cap = min(cap, hard)
    resource.setrlimit(resource.RLIMIT_AS, (cap, hard))


def execute(path: pathlib.Path) -> tuple[bool, str]:
    book = nbformat.read(path, as_version=4)
    client = NotebookClient(book, timeout=TIMEOUT, kernel_name="python3",
                            resources={"metadata": {"path": str(HERE)}},
                            allow_errors=False)
    started = time.time()
    try:
        client.execute()
    except CellExecutionError as exc:
        return False, str(exc).strip().splitlines()[-1][:300]
    finally:
        # Written even on failure: the outputs up to the break are the fastest
        # way to see *where* it broke.
        nbformat.write(book, path)
    filled = sum(1 for c in book.cells
                 if c.cell_type == "code" and c.get("outputs"))
    total = sum(1 for c in book.cells if c.cell_type == "code")
    return True, f"{filled}/{total} code cells produced output in {time.time() - started:.0f}s"


def _assert_local_package() -> None:
    """Refuse to run unless `browsergraph` resolves to this working tree.

    Kernels start with their cwd in this folder, where `browsergraph` no longer
    resolves to `../browsergraph` — it resolves to whatever is installed. If
    that is a released build, the notebooks silently verify the *last release*
    instead of the code being edited, and a run that says "11/11 ok" has tested
    nothing you changed.

    Worse, it cascades: an `ImportError` on a new module trips each notebook's
    install-if-missing fallback, pip serves a cached wheel from an older commit,
    and every subsequent notebook fails on the same missing module.
    """
    import subprocess

    # Resolved in a subprocess whose cwd is this folder, because that is where
    # the kernels run. Checking it in *this* process would resolve against the
    # repository root, where the source directory shadows site-packages and the
    # answer is right for the wrong reason.
    probe = subprocess.run(
        [sys.executable, "-c",
         "import browsergraph, pathlib; print(pathlib.Path(browsergraph.__file__).resolve().parent)"],
        cwd=str(HERE), capture_output=True, text=True)
    resolved = probe.stdout.strip()
    expected = str((HERE.parent / "browsergraph").resolve())
    if resolved != expected:
        raise SystemExit(
            f"browsergraph resolves to {resolved or '(not importable)'},\n"
            f"not {expected}.\n"
            f"These notebooks would verify that copy instead of this one. Fix:\n"
            f"  pip uninstall -y browsergraph && pip install -e . --no-deps")


def main(argv: list[str]) -> int:
    _assert_local_package()
    _cap_memory()
    print(f"memory cap {MEMORY_CAP_GB}GB per kernel "
          f"(BG_NOTEBOOK_MEMORY_GB to change)")
    wanted = argv[1:]
    books = sorted(p for p in HERE.glob("*.ipynb")
                   if not wanted or any(w in p.name for w in wanted))
    if not books:
        print("no notebooks matched")
        return 1

    failures = 0
    for path in books:
        print(f"→ {path.name}", flush=True)
        ok, detail = execute(path)
        print(f"  {'ok  ' if ok else 'FAIL'} {detail}", flush=True)
        failures += 0 if ok else 1
    print(f"\n{len(books) - failures}/{len(books)} notebooks executed cleanly")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
