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
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
README = HERE.parent / "README.md"
sys.path.insert(0, str(HERE))
from publish_kaggle import KERNELS, USER, slugify  # noqa: E402


def live() -> set[str]:
    """Which of *our* kernels exist, asked one at a time.

    Deliberately not `kernels list`. Paging an account with several hundred
    kernels returned a different set each time and never reliably included the
    ones just pushed — so the first version of this script reported three
    notebooks as missing minutes after Kaggle had returned their URLs.

    `kernels status` answers about one kernel exactly: a 404 means it is not
    there, anything else means it is. Twenty-five calls instead of one, and
    twenty-five right answers instead of one plausible one.
    """
    found: set[str] = set()
    for title in KERNELS.values():
        slug = slugify(title)
        done = subprocess.run(
            ["kaggle", "kernels", "status", f"{USER}/{slug}"],
            capture_output=True, text=True, timeout=120)
        if "404" not in (done.stdout + done.stderr):
            found.add(slug)
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true",
                        help="rewrite dead README links to the notebook file")
    args = parser.parse_args()

    published = live()
    up = {s: t for s, t in KERNELS.items() if slugify(t) in published}
    missing = {s: t for s, t in KERNELS.items() if slugify(t) not in published}
    print(f"{len(up)} of {len(KERNELS)} notebooks published")
    for slug in sorted(missing):
        print(f"  not live: {slug}")

    # Reconcile in **both** directions, and only over kernels this file owns.
    #
    # The first version did neither. It compared every Kaggle URL in the README
    # against a set built only from `KERNELS`, so the tour — a real kernel that
    # is not in that map — was reported dead. And it could only ever demote a
    # link, so when the backlog finally published, the table went on saying "not
    # yet" about eight notebooks that were live. Wrong in the other direction,
    # and just as misleading to a reader.
    text = README.read_text()
    wrong: list[str] = []
    for slug, title in KERNELS.items():
        kernel = slugify(title)
        linked = f"[Kaggle](https://www.kaggle.com/code/{USER}/{kernel})"
        placeholder = f"[not yet](notebooks/{slug}.ipynb)"
        if kernel in published and placeholder in text:
            wrong.append(f"{slug}: live, and the table says 'not yet'")
            text = text.replace(placeholder, linked)
        elif kernel not in published and linked in text:
            wrong.append(f"{slug}: linked, and the kernel does not exist")
            text = text.replace(linked, placeholder)

    print(f"\n{len(wrong)} row(s) disagree with the account")
    for line in wrong:
        print(f"  {line}")

    if wrong and args.fix:
        README.write_text(text)
        print(f"\nrewrote {len(wrong)} row(s)")
    return 0 if not wrong else 1


if __name__ == "__main__":
    raise SystemExit(main())
