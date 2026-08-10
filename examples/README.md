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

They are meant to be read in order and copied out of. Every one is short enough
to hold in your head, which is the point — a 300-line example teaches the
example rather than the library.

## The ten-minute version

If you read one thing, read `01`. The whole model is in it:

1. **Steps** say what has to happen, with typed ports.
2. **Nodes** say what could do each step.
3. **Compiling** checks the types line up and freezes the choice into a plan
   with a content hash.
4. **Running** takes that plan and a set of functions.

Everything else — search, evidence, pictures, bounded execution — is built on
those four and is optional.
