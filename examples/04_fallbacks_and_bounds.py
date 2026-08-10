#!/usr/bin/env python3
"""When a step fails, and when it will not stop.

Two different problems with two different answers:

  a step that *fails*  -> a fallback: another candidate, same contract
  a step that *hangs*  -> a bound: its own process, a clock and a ceiling

Neither is a retry loop. A retry with no limit is how a job runs forever.

Note the `if __name__ == "__main__"` guard at the bottom. It is required, not
style: a bounded step starts a subprocess that re-imports this module, so
without the guard the script runs itself once per child and hangs. I wrote this
example without it first, and it did exactly that.

    python examples/04_fallbacks_and_bounds.py
"""
import random
import time

from browsergraph import execute
from browsergraph.bounded import LimitExceeded, Limits, available, bound
from browsergraph.compile import compile_route
from browsergraph.quick import chain, graph, node, step

rng = random.Random(7)
attempts = {"fast": 0, "slow": 0}


def fast(**kw):
    """Fails about six times in ten."""
    attempts["fast"] += 1
    if rng.random() < 0.6:
        raise ConnectionError("the fast source timed out")
    return {"source": "fast", "rows": 4}


def slow(**kw):
    attempts["slow"] += 1
    return {"source": "slow", "rows": 4}


def sleeps_forever(**kw):
    """Module level, because a bounded step is pickled to its child."""
    time.sleep(300)
    return {"source": "never", "rows": 0}


def build():
    nodes = [
        node("fetch.fast", "fetch", gives=[("out", "Data")], deterministic=False),
        node("fetch.slow", "fetch", gives=[("out", "Data")], deterministic=False),
        node("use.it", "use", [("in", "Data")], [("out", "Report")]),
    ]
    steps = [
        step("fetch", "Get the data", [], [("out", "Data")], "fetch",
             ["fetch.fast", "fetch.slow"]),
        step("use", "Use it", [("in", "Data")], [("out", "Report")], "use",
             ["use.it"]),
    ]
    return graph("Fetch with a fallback",
                 "Get data from whichever source works.",
                 steps, nodes, chain("fetch", "use"))


def main() -> None:
    bench = build()
    runtime = execute.Runtime({
        "fetch.fast": fast, "fetch.slow": slow,
        "use.it": lambda **kw: f"used {kw['in']['rows']} rows "
                               f"from {kw['in']['source']}"})
    plan = compile_route(bench, {"fetch": "fetch.fast", "use": "use.it"})

    print("ten runs, no fallback:")
    bare = [execute.run(plan, runtime).ok for _ in range(10)]
    print(f"  {sum(bare)}/10 succeeded")

    print("\nten runs, falling back to the slow source:")
    good = [execute.run(plan, runtime, fallbacks={"fetch": ["fetch.slow"]})
            for _ in range(10)]
    print(f"  {sum(1 for r in good if r.ok)}/10 succeeded")
    used = [next(s for s in r.steps if s.stage == "fetch") for r in good]
    print(f"  fell back on {sum(1 for s in used if s.fell_back)} of them")
    print(f"  attempts: {attempts}  <- the fast one is still tried every time")

    if not available():
        print("\nbounded execution is not available on this platform")
        return

    print("\na step that hangs, given two seconds:")
    began = time.time()
    try:
        bound(sleeps_forever, Limits(seconds=2.0, memory_mb=256))()
    except LimitExceeded as problem:
        print(f"  stopped after {time.time() - began:.1f}s: {problem}")

    print("\nThis is lifecycle isolation, not a sandbox. It bounds time and")
    print("memory. It shares your filesystem, network and account, and arguments")
    print("cross by pickle. Safe for your own code failing unpredictably; no")
    print("protection at all against code you did not write.")


if __name__ == "__main__":     # required: a bounded step re-imports this file
    main()
