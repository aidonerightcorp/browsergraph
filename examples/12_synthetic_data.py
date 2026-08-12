#!/usr/bin/env python3
"""Five generators, and five different ways to score well.

Fidelity, utility and privacy pull against each other, and a generator that
copies its training data is at the top of the first two. That is not a corner
case — it is the direction any optimiser walks in.

    python examples/12_synthetic_data.py
"""
from browsergraph import execute, packs
from browsergraph.compile import compile_route
from browsergraph.packs import synth as synth_pack

pack = packs.get("synth")
bench = pack.workbench()

GENERATORS = ("generate.copy", "generate.noisy_copy", "generate.conditional",
              "generate.overconfident", "generate.marginal")


def measure(generator, **route):
    full = {"real": "real.rows", "split": "split.random",
            "generate": generator, "fidelity": "fidelity.joint",
            "utility": "utility.tstr", "privacy": "privacy.nearest",
            "decide": "decide.all_three", **route}
    return execute.run(compile_route(bench, full), pack.runtime())


print("Four hundred rows where price = 40*size + 3*age + a kind effect.\n")
print(f"{'generator':<26}{'marginals':>10}{'joint':>7}{'TSTR':>7}"
      f"{'TSTS':>7}{'exact':>7}{'near':>7}")

for generator in GENERATORS:
    joint = measure(generator).output("fidelity")
    tstr = measure(generator).output("utility")["score"]
    tsts = measure(generator, utility="utility.tsts").output("utility")["score"]
    near = measure(generator).output("privacy")["copied"]
    exact = measure(generator, privacy="privacy.exact").output("privacy")["copied"]
    print(f"{generator:<26}{joint['marginals']:>10.2f}{joint['joint']:>7.2f}"
          f"{tstr:>7.2f}{tsts:>7.2f}{exact:>7.2f}{near:>7.2f}")

print("""
Read it row by row.

  copy            top of every column anybody usually looks at, and every row
                  is a real record.
  noisy_copy      zero exact duplicates, 100% of rows sitting on top of the
                  one they came from. A thousandth of a standard deviation
                  defeats an exact-duplicate check entirely.
  conditional     the only one that passes all three — and the *worst* of the
                  five on marginal fidelity. A fidelity ranking puts it fourth.
  overconfident   the best utility score in the table, on the wrong test. Every
                  synthetic row obeys `price = 40 * size` exactly, so a model
                  fitted to synthetic data explains synthetic data perfectly
                  and real data not at all.
  marginal        every histogram matches — better than the generator that
                  works — and every relationship between columns is gone.
""")

print("=" * 74)
print("What each decision rule ships\n")
for generator in GENERATORS:
    strict = measure(generator).output("decide")
    lax = measure(generator, decide="decide.utility_only").output("decide")
    print(f"  {generator:<26}all three: {'ship  ' if strict['allow'] else 'refuse'}"
          f"   utility only: {'ship' if lax['allow'] else 'refuse'}")
    for reason in strict["reasons"]:
        print(f"      {reason}")

print("\n  Deciding on utility alone ships both copiers. Deciding on fidelity\n"
      "  alone would ship the marginal generator and reject the working one.\n")

print("=" * 74)
print("Scored by the pack's verifier, which multiplies rather than averages\n")
for generator in GENERATORS:
    ok, score = synth_pack.worth_shipping(measure(generator))
    print(f"  {generator:<26}{'usable' if ok else 'no    '}   {score:.3f}")

print("\n  Multiplying by (1 - privacy risk) rather than adding a weighted term\n"
      "  is the argument: memorisation is a categorical failure, and no weight\n"
      "  that leaves a copier a positive score is defensible.")
