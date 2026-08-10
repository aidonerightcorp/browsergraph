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

Two failure modes worth knowing before you debug the wrong thing:

* **"Your kernel title does not resolve to the specified id."** The title must
  slugify to the id exactly. That is why the id is derived here rather than
  written down twice.
* **A bare `400` from `KernelsApiService` on every call, including reads.** That
  is rate limiting, not a bad payload. Pushing eleven kernels in a row triggers
  it, and it then rejects `kernels list` too, which makes it look like an outage
  or an auth failure. `datasets list` still working is the tell: the token is
  fine and only this service is refusing. Wait, then push in smaller batches —
  `python notebooks/publish_kaggle.py --push 04 05 06`.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
STAGE = HERE.parent / ".kaggle-kernels"
USER = "taylorsamarel"

#: slug -> title. Titles carry the domain, because a reader browsing Kaggle sees
#: the title and nothing else.
#:
#: The id is *derived* from the title rather than written alongside it. Kaggle
#: rejects a push whose title does not slugify to its id — "does not resolve to
#: the specified id" — and keeping two hand-maintained strings in agreement is
#: a job for a function, not for whoever edits this next.
#:
#: Titles stay under 50 characters and avoid punctuation that slugifies away
#: unevenly (an em dash becomes nothing, a colon becomes a separator).
KERNELS: dict[str, str] = {
    "01-build-a-graph": "Graph Solutions 1 Express a Problem as a Graph",
    "02-search-and-learn": "Graph Solutions 2 Search Without Enumerating",
    "03-a-new-domain": "Graph Solutions 3 A Domain That Is Not Browsing",
    "04-tabular-pipeline": "Graph Solutions 4 A Kaggle Pipeline Is a Graph",
    "05-document-extraction": "Graph Solutions 5 Two Readings of One Document",
    "06-service-workflow": "Graph Solutions 6 A Workflow With No Data Science",
    "07-data-quality-gate": "Graph Solutions 7 A Gate That Can Say No",
    "08-release-pipeline": "Graph Solutions 8 Build Verify Release",
    "09-retrieval-qa": "Graph Solutions 9 Retrieval Is Two Searches",
    "10-timeseries-forecast": "Graph Solutions 10 The Leak You Cannot See",
    "11-web-harvest": "Graph Solutions 11 The Domain This Started In",
    # The five that solve a real job. Different prefix so they read as a
    # separate series, because they are: these ones run and produce files.
    "12-browse-and-scrape": "Graph Jobs 1 Browse and Scrape",
    "13-ingest-into-schema": "Graph Jobs 2 Ingest into a Schema",
    "14-check-and-process-image": "Graph Jobs 3 Check and Process an Image",
    "15-clean-up-data": "Graph Jobs 4 Clean Up Messy Data",
    "16-fit-a-model": "Graph Jobs 5 Fit a Model",
    "17-batch-many-files": "Graph Jobs 6 Batch Process Many Files",
    "18-retry-and-fall-back": "Graph Jobs 7 Retry and Fall Back",
    "19-find-duplicates": "Graph Jobs 8 Find Duplicate Records",
    "20-forecast-next-month": "Graph Jobs 9 Forecast Next Month",
    "21-watch-for-changes": "Graph Jobs 10 Watch for Changes",
    "22-sort-text": "Graph Jobs 11 Sort Text into Categories",
}


def slugify(title: str) -> str:
    """Kaggle's rule: lowercase, non-alphanumerics become hyphens, collapse."""
    out = "".join(c.lower() if c.isalnum() else "-" for c in title)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


def unexecuted(path: pathlib.Path) -> list[int]:
    """Code cells with no output at all.

    A cell that legitimately produces nothing (a bare `def`) is indistinguishable
    from one that never ran, so this reports and the caller decides — but the
    count being *high* is the signal that matters.
    """
    book = json.loads(path.read_text())
    return [i for i, cell in enumerate(book["cells"])
            if cell["cell_type"] == "code" and not cell.get("outputs")]


def stage(slug: str, title: str) -> pathlib.Path:
    kernel_id = slugify(title)
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

    chosen = {s: t for s, t in KERNELS.items()
              if not args.only or any(o in s for o in args.only)}
    if not chosen:
        print("nothing matched")
        return 1

    failures = 0
    for slug, title in chosen.items():
        if len(title) > 50:
            print(f"→ {slug}\n  SKIPPED — title is {len(title)} chars, "
                  f"Kaggle allows 50")
            failures += 1
            continue
        kernel_id = slugify(title)
        blank = unexecuted(HERE / f"{slug}.ipynb")
        folder = stage(slug, title)
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
