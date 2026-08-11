#!/usr/bin/env python3
"""Which notebooks are actually on Kaggle, and does the README agree?

Run after publishing, and before believing a table of links.

    python notebooks/check_published.py            # report
    python notebooks/check_published.py --fix      # rewrite dead README links

The reason this exists: the README linked ten kernels that do not exist. Each
row said "Kaggle" and each one 404s, which is worse than no link — a reader
concludes the project is abandoned rather than that the table is stale. The
count in the session notes said fifteen published; the account says twelve.

Deliberately a script and not a test. It needs the network and a token, so as a
test it would fail in CI for reasons that have nothing to do with the code, and
a test that fails for the wrong reason gets deleted.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
README = HERE.parent / "README.md"
sys.path.insert(0, str(HERE))
from publish_kaggle import KERNELS, USER, slugify  # noqa: E402


def live() -> set[str]:
    """Every kernel slug on the account, paged until it stops growing."""
    found: set[str] = set()
    for page in range(1, 8):
        done = subprocess.run(
            ["kaggle", "kernels", "list", "--user", USER,
             "--page-size", "100", "--page", str(page)],
            capture_output=True, text=True, timeout=180)
        rows = {line.split()[0].split("/", 1)[1]
                for line in done.stdout.splitlines()
                if line.startswith(f"{USER}/")}
        if rows <= found:
            break
        found |= rows
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true",
                        help="rewrite dead README links to the notebook file")
    args = parser.parse_args()

    published = live()
    if not published:
        print("could not list any kernels — is the token present?")
        return 1

    up = {s: t for s, t in KERNELS.items() if slugify(t) in published}
    missing = {s: t for s, t in KERNELS.items() if slugify(t) not in published}
    print(f"{len(published)} kernels on the account")
    print(f"{len(up)} of {len(KERNELS)} notebooks published")
    for slug in sorted(missing):
        print(f"  not live: {slug}")

    text = README.read_text()
    dead = sorted({s for s in re.findall(
        rf"https://www\.kaggle\.com/code/{USER}/([\w-]+)", text)
        if s not in published})
    print(f"\n{len(dead)} dead link(s) in README.md")
    for slug in dead:
        print(f"  {slug}")

    if dead and args.fix:
        by_slug = {slugify(t): s for s, t in KERNELS.items()}
        for kernel in dead:
            notebook = by_slug.get(kernel)
            replacement = (f"[not yet](notebooks/{notebook}.ipynb)" if notebook
                           else "not yet")
            text = re.sub(
                rf"\[Kaggle\]\(https://www\.kaggle\.com/code/{USER}/{kernel}\)",
                replacement, text)
        README.write_text(text)
        print(f"\nrewrote {len(dead)} link(s) — run again after publishing")
    return 0 if not dead else 1


if __name__ == "__main__":
    raise SystemExit(main())
