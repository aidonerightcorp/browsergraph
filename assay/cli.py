"""`assay` on the command line — one command per question.

    assay judge --demo                     is my judge measuring anything?
    assay judge --data labels.csv --model verdict --human truth
    assay controls --demo                  would this harness notice a break?
    assay taxonomy --search address        which shape is this job?
    assay obligations                      what does an evaluation owe?

The `--demo` flags run on data that ships with the package and reproduce a
finding rather than printing a tidy example. Someone evaluating this tool should
be able to see what it catches before they have prepared any data of their own.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from assay import controls as controls_mod
from assay import judge as judge_mod
from assay import obligations as obligations_mod
from assay import taxonomy as taxonomy_mod
from assay._version import __version__


def _load_table(path: Path) -> list[dict[str, Any]]:
    """Read a CSV or JSON table into rows of dicts. One reader, two shapes.

    JSON may be a list of records or a dict of columns; both are common and
    guessing between them is cheaper than making a person reshape a file.
    """
    text = path.read_text()
    if path.suffix.lower() == ".csv":
        return list(csv.DictReader(text.splitlines()))
    data = json.loads(text)
    if isinstance(data, list):
        return [dict(row) for row in data]
    if isinstance(data, dict):
        keys = list(data)
        if not keys:
            return []
        lengths = {len(v) for v in data.values() if isinstance(v, list)}
        if len(lengths) != 1:
            raise SystemExit(
                f"{path}: columns have different lengths ({sorted(lengths)}). "
                f"These have to be the same items in the same order.")
        n = lengths.pop()
        return [{k: data[k][i] for k in keys} for i in range(n)]
    raise SystemExit(f"{path}: expected a list of records or a dict of columns")


def _column(rows: Sequence[dict], name: str, path: Path) -> list[Any]:
    missing = [i for i, row in enumerate(rows) if name not in row]
    if missing:
        available = ", ".join(sorted(rows[0])) if rows else "(no rows)"
        raise SystemExit(
            f"{path}: no column {name!r} (row {missing[0]} has none). "
            f"Columns present: {available}")
    return [row[name] for row in rows]


def _demo_judge() -> tuple[list[str], list[str], dict[str, list[str]],
                           list[float], list[str]]:
    """A judge that looks fine and is not. One mechanism, deterministic.

    120 responses, 102 of them genuinely good. That imbalance is the first
    trap: answering "good" every single time agrees with the humans 85% of the
    time, which reads as a working judge.

    The mechanism is a single realistic failure, not three separate fixtures:

        the bad answers are the longer ones   — models pad when they do not
                                                know, so length is *inversely*
                                                related to quality here
        the judge scores length               — score rises with word count,
                                                with a little jitter
        the verdict is a threshold on score   — so the judge systematically
                                                prefers the answers a person
                                                marked down

    Everything the report prints falls out of that. Nothing in the table is
    chosen to make a number come out; the numbers are measured from the
    construction and printed back.
    """
    human: list[str] = []
    model: list[str] = []
    texts: list[str] = []
    scores: list[float] = []
    for i in range(120):
        good = (i * 17) % 20 >= 3                      # 102 good, 18 bad
        human.append("good" if good else "bad")
        # Padding on the answers a person marked down.
        words = 26 + (0 if good else 22) + ((i * 37) % 23)
        texts.append("word " * words)
        # The judge reads length. The jitter keeps the rank correlation off a
        # perfect 1.0, which no real judge produces.
        score = round(1.0 + words / 22.0 + ((i * 13) % 5) / 10.0, 2)
        scores.append(score)
        model.append("good" if score >= 2.6 else "bad")

    # The same judge, the same responses, three rubrics. Only wording changes.
    rubrics = {
        "did it answer the question asked": list(model),
        "rate the overall quality": ["good"] * len(human),
        "is anything in it false": [
            h if i % 6 else ("bad" if h == "good" else "good")
            for i, h in enumerate(human)],
    }
    return human, model, rubrics, scores, texts


def cmd_judge(args: argparse.Namespace) -> int:
    if args.demo:
        human, model, rubrics, scores, texts = _demo_judge()
        report = judge_mod.audit(model=model, human=human, rubrics=rubrics,
                                 scores=scores, texts=texts)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(report.text())
            print()
            # Every number in this paragraph is read back off the report. A
            # demo whose prose and whose output disagree teaches the reader to
            # trust neither.
            majority = max(human.count("good"), human.count("bad")) / len(human)
            agree = report.agreement
            both_bad = sum(1 for m, h in zip(model, human, strict=True)
                           if m == "bad" and h == "bad")
            print(f"The demo sample: {len(human)} items, raw agreement "
                  f"{agree.raw:.1%}, chance {agree.expected:.1%}, kappa "
                  f"{agree.kappa:+.3f}. Answering 'good' every time would "
                  f"agree {majority:.1%} of the time — better than this judge "
                  f"manages. Items where the judge and a person both said the "
                  f"answer was bad: {both_bad}.")
        return 0 if report.ok else 1

    if not args.data:
        print("assay judge needs --data FILE (or --demo). The file wants one "
              "row per item, with the judge's verdict and a human's verdict "
              "for the same item.", file=sys.stderr)
        return 2

    path = Path(args.data)
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        return 2
    rows = _load_table(path)
    if not rows:
        print(f"{path} has no rows.", file=sys.stderr)
        return 2

    model = _column(rows, args.model, path)
    human = _column(rows, args.human, path)
    answers: list[str] | None = None
    grades: list[float] | None = None
    if args.text:
        answers = [str(value) for value in _column(rows, args.text, path)]
    if args.score:
        grades = [float(value) for value in _column(rows, args.score, path)]

    report = judge_mod.audit(model=model, human=human,
                             scores=grades, texts=answers)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.text())
    return 0 if report.ok else 1


def cmd_controls(args: argparse.Namespace) -> int:
    if args.demo:
        bundle = controls_mod.Controls()
        labels = ["good"] * 83 + ["bad"] * 17
        chance = controls_mod.chance_level(labels)
        bundle.add(controls_mod.null_control(observed=0.86, chance=chance))
        bundle.add(controls_mod.negative_control(broken=0.85, real=0.86))
        bundle.add(controls_mod.positive_control(observed=0.86, floor=0.90))
        verdict = bundle.verdict(score=0.86)
        if args.json:
            print(json.dumps(verdict.to_dict(), indent=2))
        else:
            print(verdict.text())
            print()
            print(f"Chance on this label distribution is {chance:.3f}, not "
                  f"0.500. Against 0.500 every number above looks like a "
                  f"result.")
        return 0 if verdict.ok else 1

    print("assay controls --demo shows what a control set catches.")
    print()
    print("In code:")
    print("    from assay import controls")
    print("    c = controls.Controls()")
    print("    c.add(controls.negative_control(broken=0.85, real=0.86))")
    print("    print(c.verdict(score=0.86).text())")
    print()
    print("A harness with no controls cannot return better than PROVISIONAL.")
    return 0


def cmd_taxonomy(args: argparse.Namespace) -> int:
    if args.search:
        found = taxonomy_mod.search(args.search)
        if not found:
            print(f"nothing about {args.search!r} in "
                  f"{len(taxonomy_mod.CATEGORIES)} categories.")
            return 1
        for category in found:
            print(f"{category.id:<24} {category.title}")
            print(f"{'':<24} {category.question}")
            print(f"{'':<24} fails as: {category.fails_as}")
        return 0
    if args.coverage:
        print(taxonomy_mod.coverage().text())
        return 0
    print(taxonomy_mod.catalog_text(args.family or ""))
    return 0


def cmd_obligations(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps([{"id": o.id, "severity": o.severity,
                           "what": o.what, "why": o.why}
                          for o in obligations_mod.STANDARD], indent=2))
        return 0
    print("What an evaluation owes. A blocking obligation left outstanding "
          "makes the verdict PROVISIONAL until it is discharged or waived.\n")
    for obligation in obligations_mod.STANDARD:
        print(f"  {obligation.severity:<9} {obligation.id:<20} "
              f"{obligation.what}")
        print(f"  {'':<9} {'':<20} without it: {obligation.why}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assay",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__)
    parser.add_argument("--version", action="version",
                        version=f"assay {__version__}")
    subs = parser.add_subparsers(dest="command")

    judge = subs.add_parser("judge", help="audit a judge against human labels")
    judge.add_argument("--data", help="CSV or JSON table, one row per item")
    judge.add_argument("--model", default="model",
                       help="column holding the judge's verdict")
    judge.add_argument("--human", default="human",
                       help="column holding the human verdict")
    judge.add_argument("--text", help="column holding the answer text, to "
                                      "check for length bias")
    judge.add_argument("--score", help="column holding the judge's numeric "
                                       "score, if it gave one")
    judge.add_argument("--demo", action="store_true",
                       help="run on the sample that ships with the package")
    judge.add_argument("--json", action="store_true")
    judge.set_defaults(func=cmd_judge)

    controls = subs.add_parser("controls",
                               help="what a harness owes before it is believed")
    controls.add_argument("--demo", action="store_true")
    controls.add_argument("--json", action="store_true")
    controls.set_defaults(func=cmd_controls)

    taxonomy = subs.add_parser("taxonomy", help="which shape is this job")
    taxonomy.add_argument("--search", help="find categories by words")
    taxonomy.add_argument("--coverage", action="store_true",
                          help="what has a shape, what has code")
    taxonomy.add_argument("--family", help="show one family only")
    taxonomy.set_defaults(func=cmd_taxonomy)

    obligations = subs.add_parser("obligations",
                                  help="the standard set of obligations")
    obligations.add_argument("--json", action="store_true")
    obligations.set_defaults(func=cmd_obligations)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return int(args.func(args))


if __name__ == "__main__":                                # pragma: no cover
    raise SystemExit(main())
