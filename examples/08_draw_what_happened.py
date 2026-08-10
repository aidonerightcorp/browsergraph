#!/usr/bin/env python3
"""Draw the run, not just the graph.

Every other picture in this library is of a *graph* — what could happen. Two of
them are of a *run* — what did:

    viz.timeline(run)        where each step actually sat on the clock
    viz.scoreboard(solution) every route solve tried, ranked, failures included

The timeline is the one that earns its keep. `viz.dag` says two steps *may* run
together, because nothing connects them. Whether they *did* is a different
question, and one number in one call decides it. This script runs the same plan
on one worker and then on two, and writes both pictures to a page you can open.

    python examples/08_draw_what_happened.py
"""
import pathlib
import tempfile
import time

from browsergraph import execute, solve, viz
from browsergraph.compile import compile_route
from browsergraph.evidence import per_step_bits, stages_of
from browsergraph.quick import fanin, fanout, graph, node, step
from browsergraph.workbench import OptimizationObjective, OptimizationProfile

#: A temp folder rather than the working directory. An example that leaves
#: files behind where it was run from is an example somebody has to clean up.
OUT = pathlib.Path(tempfile.gettempdir()) / "browsergraph-example-08"


def build():
    """Load a table, then two independent readings of it, then a verdict."""
    nodes = [
        node("load.good", "load", gives=[("out", "Table")]),
        # Returns an empty table. It never raises, which is the point.
        node("load.empty", "load", gives=[("out", "Table")]),
        node("stats.mean", "stats", [("in", "Table")], [("out", "Summary")]),
        node("stats.median", "stats", [("in", "Table")], [("out", "Summary")]),
        node("shape.count", "shape", [("in", "Table")], [("out", "Summary")]),
        node("verdict.join", "verdict",
             [("stats", "Summary"), ("shape", "Summary")], [("out", "Report")]),
    ]
    steps = [
        step("load", "Load the table", [], [("out", "Table")], "load",
             ["load.good", "load.empty"]),
        step("stats", "Summarise the values", [("in", "Table")],
             [("out", "Summary")], "stats", ["stats.mean", "stats.median"]),
        step("shape", "Measure the shape", [("in", "Table")],
             [("out", "Summary")], "shape", ["shape.count"]),
        step("verdict", "Write it up",
             [("stats", "Summary"), ("shape", "Summary")], [("out", "Report")],
             "verdict", ["verdict.join"]),
    ]
    links = [*fanout("load", ["stats", "shape"]),
             *fanin({"stats": "stats", "shape": "shape"}, "verdict")]
    return graph("Summarise a table",
                 "Load it, summarise and measure it at once, then write up.",
                 steps, nodes, links,
                 profiles=[OptimizationProfile(id="p", objectives=(
                     OptimizationObjective("quality", "maximize", 1.0),))])


def slow(work):
    """A beat per step, so the bars have something to show."""
    def call(**kwargs):
        time.sleep(0.1)
        return work(**kwargs)
    return call


def runtime():
    rows = [{"v": 1}, {"v": 4}, {"v": 9}, {"v": 16}]
    return execute.Runtime({
        "load.good": slow(lambda **kw: list(rows)),
        "load.empty": slow(lambda **kw: []),
        "stats.mean": slow(lambda **kw: {"mean": sum(r["v"] for r in kw["in"])
                                                 / max(len(kw["in"]), 1)}),
        "stats.median": slow(lambda **kw: {"median": sorted(
            r["v"] for r in kw["in"])[len(kw["in"]) // 2] if kw["in"] else 0}),
        "shape.count": slow(lambda **kw: {"rows": len(kw["in"])}),
        "verdict.join": slow(lambda **kw: {**kw["stats"], **kw["shape"]}),
    })


def main() -> None:
    bench = build()
    functions = runtime()
    route = {"load": "load.good", "stats": "stats.mean",
             "shape": "shape.count", "verdict": "verdict.join"}
    plan = compile_route(bench, route)

    one = execute.run(plan, functions, workers=1)
    two = execute.run(plan, functions, workers=2)
    print(f"one worker : {one.seconds:.2f}s")
    print(f"two workers: {two.seconds:.2f}s")
    for label, run in (("one", one), ("two", two)):
        began = {s.stage: s.started for s in run.steps}
        gap = abs(began["stats"] - began["shape"])
        print(f"  {label} worker(s): stats and shape began {gap * 1000:.0f}ms "
              f"apart{'  <- together' if gap < 0.03 else ''}")

    # A judge that looks at the output. Without one, the route that loads an
    # empty table succeeds: it raises nothing, and every step reports fine.
    def judge(run):
        report = run.output("verdict")
        return bool(report.get("rows")), float(report.get("rows", 0))

    answer = solve.solve(bench, functions, verify=judge, attempts=6, workers=2)
    print(f"\n{answer.text()}")

    bits = per_step_bits(answer.evidence, answer.champion, stages_of(bench))
    print("\nbits per step, from the runs that actually happened:")
    for stage, value in bits.items():
        print(f"  {stage:<10} {value:+.2f}")
    print("\n  load is the only step that moved, and that is the true answer.")
    print("  shape and verdict have one candidate each: nothing was chosen, so")
    print("  there is nothing to be right or wrong about. stats has two, and")
    print("  they tie — the judge counts rows, and no summary changes how many")
    print("  rows there are. A step your judge cannot see is a step your")
    print("  evidence cannot rank, and a zero there means 'not measured', not")
    print("  'does not matter'.")

    page = viz.write_report(bench, OUT / "what-happened.html",
                            route=answer.champion, alternative=route,
                            run=two, solution=answer, bits=bits)
    print(f"\nwrote {page} — open it in a browser, it needs no network")


if __name__ == "__main__":
    main()
