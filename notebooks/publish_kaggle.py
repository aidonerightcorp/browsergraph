#!/usr/bin/env python3
"""Package the workflow notebooks as Kaggle kernels and push them.

One kernel per notebook, each in its own directory with its own metadata,
because Kaggle's CLI takes a folder and a folder may hold exactly one kernel.

    python notebooks/publish_kaggle.py            # stage only, no push
    python notebooks/publish_kaggle.py --push     # stage and push

Staging is the default and pushing is opt-in on purpose: this writes to a public
account, and a script that publishes as a side effect of being run is a script
that eventually publishes something half-finished.

A notebook is refused if any code cell has no output. Kaggle renders what you
upload, so an unexecuted notebook publishes as a page of grey boxes claiming to
demonstrate something.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
STAGE = HERE.parent / ".kaggle-kernels"
USER = "taylorsamarel"

#: slug -> (kaggle id suffix, title). Titles carry the domain, because a reader
#: browsing Kaggle sees the title and nothing else.
KERNELS: dict[str, tuple[str, str]] = {
    "01-build-a-graph": (
        "graph-solutions-1-express-a-problem-as-a-graph",
        "Graph Solutions 1 — Express a Problem as a Graph"),
    "02-search-and-learn": (
        "graph-solutions-2-search-without-enumerating",
        "Graph Solutions 2 — Search Without Enumerating"),
    "03-a-new-domain": (
        "graph-solutions-3-a-domain-that-is-not-browsing",
        "Graph Solutions 3 — A Domain That Is Not Browsing"),
    "04-tabular-pipeline": (
        "graph-solutions-4-a-kaggle-pipeline-is-a-graph",
        "Graph Solutions 4 — A Kaggle Pipeline Is a Graph"),
    "05-document-extraction": (
        "graph-solutions-5-two-readings-of-one-document",
        "Graph Solutions 5 — Two Readings of One Document"),
    "06-service-workflow": (
        "graph-solutions-6-a-system-with-no-data-science",
        "Graph Solutions 6 — A System With No Data Science In It"),
    "07-data-quality-gate": (
        "graph-solutions-7-a-gate-that-can-say-no",
        "Graph Solutions 7 — A Gate That Can Actually Say No"),
    "08-release-pipeline": (
        "graph-solutions-8-build-verify-release",
        "Graph Solutions 8 — Build, Verify, Release"),
    "09-retrieval-qa": (
        "graph-solutions-9-retrieval-is-two-searches",
        "Graph Solutions 9 — Retrieval Is Two Searches, Not One"),
    "10-timeseries-forecast": (
        "graph-solutions-10-the-leak-you-cannot-see",
        "Graph Solutions 10 — The Leak You Cannot See in CV"),
    "11-web-harvest": (
        "graph-solutions-11-the-domain-this-started-in",
        "Graph Solutions 11 — The Domain This Started In"),
}


def unexecuted(path: pathlib.Path) -> list[int]:
    """Code cells with no output at all.

    A cell that legitimately produces nothing (a bare `def`) is indistinguishable
    from one that never ran, so this reports and the caller decides — but the
    count being *high* is the signal that matters.
    """
    book = json.loads(path.read_text())
    return [i for i, cell in enumerate(book["cells"])
            if cell["cell_type"] == "code" and not cell.get("outputs")]


def stage(slug: str, kernel_id: str, title: str) -> pathlib.Path:
    source = HERE / f"{slug}.ipynb"
    if not source.exists():
        raise FileNotFoundError(source)

    folder = STAGE / slug
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(source, folder / f"{slug}.ipynb")
    (folder / "kernel-metadata.json").write_text(json.dumps({
        "id": f"{USER}/{kernel_id}",
        "title": title,
        "code_file": f"{slug}.ipynb",
        "language": "python",
        "kernel_type": "notebook",
        "is_private": False,
        "enable_gpu": False,
        "enable_tpu": False,
        # The first cell pip-installs browsergraph from GitHub. Without this the
        # notebook fails on Kaggle at cell one, which is a confusing way to
        # introduce a library about checking things before they run.
        "enable_internet": True,
        "dataset_sources": [],
        "competition_sources": [],
        "kernel_sources": [],
    }, indent=2), encoding="utf-8")
    return folder


def push(folder: pathlib.Path) -> tuple[bool, str]:
    done = subprocess.run(["kaggle", "kernels", "push", "-p", str(folder)],
                          capture_output=True, text=True, timeout=300)
    out = (done.stdout + done.stderr).strip()
    ok = done.returncode == 0 and "error" not in out.lower()
    return ok, out.splitlines()[-1][:200] if out else "(no output)"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true",
                        help="actually publish; otherwise stage only")
    parser.add_argument("only", nargs="*", help="substrings of slugs to include")
    args = parser.parse_args()

    chosen = {s: v for s, v in KERNELS.items()
              if not args.only or any(o in s for o in args.only)}
    if not chosen:
        print("nothing matched")
        return 1

    failures = 0
    for slug, (kernel_id, title) in chosen.items():
        blank = unexecuted(HERE / f"{slug}.ipynb")
        folder = stage(slug, kernel_id, title)
        note = f"{len(blank)} cell(s) with no output" if blank else "all cells have output"
        print(f"→ {slug}\n  staged {folder.relative_to(HERE.parent)}  ({note})")

        if len(blank) > 2:
            print("  SKIPPED — too many empty cells; run notebooks/execute.py first")
            failures += 1
            continue
        if args.push:
            ok, detail = push(folder)
            print(f"  {'pushed' if ok else 'FAILED'}: {detail}")
            failures += 0 if ok else 1
            if ok:
                print(f"  https://www.kaggle.com/code/{USER}/{kernel_id}")

    print(f"\n{len(chosen) - failures}/{len(chosen)} "
          f"{'published' if args.push else 'staged'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
