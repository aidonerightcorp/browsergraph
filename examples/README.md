# Examples

Complete scripts. Each one runs on its own, does a real job, and prints what it
did. No notebook, no dataset to download, no model.

```bash
pip install "browsergraph @ git+https://github.com/aidonerightcorp/browsergraph.git"
python examples/01_smallest_useful_graph.py
```

| | Script | What it shows |
|---|---|---|
| 01 | [The smallest useful graph](01_smallest_useful_graph.py) | build, check, compile, run — twenty lines |
| 02 | [Two things at once](02_parallel_and_join.py) | a join, and why it is not a list of steps |
| 03 | [Do it to all of them](03_batch_with_map.py) | a map step over a folder |
| 04 | [When a step fails](04_fallbacks_and_bounds.py) | fallbacks, and a clock and ceiling on one step |
| 05 | [Get better at it](05_learn_from_receipts.py) | run, keep the receipt, choose better next time |
| 06 | [Ask a model](06_model_suggests_you_check.py) | a model proposes, the compiler disposes |
| 07 | [Solve it for me](07_solve_it_for_me.py) | one call: try, judge, champion plus fallback |
| 08 | [Draw what happened](08_draw_what_happened.py) | a timeline and a scoreboard of a real run, in one page |

01-08 are meant to be read in order and copied out of. Every one is short enough
to hold in your head, which is the point — a 300-line example teaches the
example rather than the library.

## Whole jobs

These are longer, and each one is a category of work rather than a feature of
the library. Every number they print is computed when you run them.

| | Script | What it shows |
|---|---|---|
| 09 | [An evaluation you could defend](09_due_care_evaluation.py) | six routes, six scores, and the ledger that says which one you may act on — plus the loop that turns this round's failures into next round's cases |
| 10 | [Supervisors and workers](10_supervisors_and_workers.py) | the four-field answer to a three-field document, and why the best-looking output is the wrong one |
| 11 | [Place and time](11_place_and_time.py) | addresses that are well-formed and do not exist; a rainfall figure published two days after the sale; midnight in Denver landing in next year |
| 12 | [Synthetic data](12_synthetic_data.py) | five generators, five different ways to score well, and the one metric that catches the copier |
| 13 | [Find your shape](13_find_your_shape.py) | look the job up in the taxonomy, get a typed skeleton and the mistakes people make in it |
| 14 | [Audit a judge](14_audit_a_judge.py) | a judge that agrees with people less often than answering "good" every time would, and the three separate checks that say so — needs no graph, only `assay` |

## The ten-minute version

If you read one thing, read `01`. The whole model is in it:

1. **Steps** say what has to happen, with typed ports.
2. **Nodes** say what could do each step.
3. **Compiling** checks the types line up and freezes the choice into a plan
   with a content hash.
4. **Running** takes that plan and a set of functions.

Everything else — search, evidence, pictures, bounded execution — is built on
those four and is optional.
