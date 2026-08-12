"""Synthetic tabular data, and the three judgements that have to disagree.

A generator is easy to build and almost impossible to evaluate with one number,
because the three things you want from synthetic data pull against each other:

    fidelity   does it look like the real thing
    utility    can a model trained on it do the real job
    privacy    did it just memorise the rows it was given

**A generator that copies its training data scores perfectly on the first two.**
That is not a corner case, it is the direction every optimiser walks in, and it
is why this template has a three-way join at the end rather than a score.

    from browsergraph import packs
    print(packs.get("synth").solve().text())

Five generators, each of which is somebody's real implementation. Measured, by a
test that runs every route. `joint` is fidelity on the relationships between
columns; `TSTR` is train-on-synthetic-test-on-**real**; `TSTS` is the same thing
tested on synthetic; `exact` and `near` are the fraction of synthetic rows that
are, respectively, identical to a real row and indistinguishable from one:

    generator                marginals  joint   TSTR   TSTS  exact   near
    generate.copy                 0.98   0.97   0.99   0.99   1.00   1.00
    generate.noisy_copy           0.96   0.97   0.99   0.99   0.00   1.00
    generate.conditional          0.90   0.94   0.99   0.99   0.00   0.00
    generate.overconfident        0.57   0.60   0.00   1.00   0.00   0.00
    generate.marginal             0.96   0.60   0.00   0.00   0.00   0.01

**Every row is a different way to score well.** Take them in turn.

`generate.copy` is a bootstrap resample. It is at or near the top of every
column that anybody usually looks at — 0.98, 0.97, 0.99 — and every one of its
rows is a real record. This is the direction an optimiser walks in, and no
amount of fidelity or utility measurement will ever object.

`generate.noisy_copy` adds a thousandth of a standard deviation and drops the
*exact* duplicate count to zero while every row still sits on top of the one it
came from. That is why privacy has two candidates:

    privacy.exact     identical rows only      -> 0%,  looks clean
    privacy.nearest   distance to nearest row  -> 100%, entirely memorised

`generate.conditional` is the only one that passes all three checks, and it is
the **worst of the five on marginal fidelity** — 0.90, below two generators that
should be rejected outright. A ranking on fidelity puts it fourth. That is the
correct outcome, and the reason `decide.all_three` sits beside
`decide.utility_only`, which ships both copiers.

`generate.overconfident` is the one that defeats the wrong utility check. Every
synthetic row obeys `price = 40 × size` exactly, so a model fitted to synthetic
data explains synthetic data perfectly — **TSTS of 1.00, the best score in the
table** — and explains real data not at all. Train-on-synthetic-test-on-synthetic
measures whether a generator is self-consistent, and this is what a confidently
wrong generator looks like from the inside.

`generate.marginal` draws each column independently from its own empirical
distribution, so every histogram matches — 0.96 on marginals, better than the
generator that works — and every relationship between columns is gone. Joint
fidelity catches it at 0.60 and utility catches it at 0.00. Per-column checks do
not, and per-column checks are what most fidelity reports are made of.

The real holdout is the only thing that can settle any of this, which is why
`split` runs before `generate` and the generator never sees it.

Standard library and deterministic: a seeded generator, closed-form least
squares, and no numpy.
"""
from __future__ import annotations

import math
import random
import statistics
from typing import Any

from browsergraph.execute import Runtime
from browsergraph.manifest import NodeManifest, PortSpec
from browsergraph.templates import get as _template
from browsergraph.workbench import (
    NodeCandidate,
    OptimizationObjective,
    OptimizationProfile,
    WorkbenchDefinition,
)

TEMPLATE = "synth.tabular"
SUMMARY = "generate rows, then judge fidelity, utility and privacy separately"

FILLING: dict[str, list[str]] = {
    "real":     ["real.rows", "real.small"],
    "split":    ["split.random", "split.ordered"],
    "generate": ["generate.conditional", "generate.marginal",
                 "generate.overconfident", "generate.noisy_copy",
                 "generate.copy"],
    "fidelity": ["fidelity.joint", "fidelity.marginals"],
    "utility":  ["utility.tstr", "utility.tsts"],
    "privacy":  ["privacy.nearest", "privacy.exact"],
    "decide":   ["decide.all_three", "decide.utility_only"],
}

NUMERIC = ("size", "age")
CATEGORICAL = "kind"
TARGET = "price"
KINDS = ("house", "flat", "studio")
KIND_EFFECT = {"house": 60.0, "flat": 0.0, "studio": -35.0}

#: Two synthetic rows within this normalised distance of a real one are the
#: same record wearing a different hat.
TOO_CLOSE = 0.02


def _node(node_id: str, capability: str, takes, gives, *,
          description: str, **extra) -> NodeManifest:
    return NodeManifest(
        id=node_id, kind=node_id.split(".")[-1], description=description,
        capabilities=(capability,),
        inputs=tuple(PortSpec(n, t) for n, t in takes),
        outputs=tuple(PortSpec(n, t) for n, t in gives),
        metrics={"source": "illustrative-prior", "quality": 0.9},
        **extra)


NODES: tuple[NodeManifest, ...] = (
    _node("real.rows", "data.read", [], [("out", "Frame")],
          description="Four hundred rows with a real relationship in them."),
    _node("real.small", "data.read", [], [("out", "Frame")],
          description="Sixty rows. Synthesis is hardest exactly when you most "
                      "want it, and this is what that looks like."),

    _node("split.random", "data.split", [("in", "Frame")],
          [("train", "Frame"), ("holdout", "Frame")],
          description="Shuffle, then cut. The generator sees the training "
                      "half only."),
    _node("split.ordered", "data.split", [("in", "Frame")],
          [("train", "Frame"), ("holdout", "Frame")],
          description="First 70% to train. Honest when the rows have an "
                      "order, and this dataset does not, so the two should "
                      "agree — which makes them a check on each other."),

    _node("generate.conditional", "synth.generate", [("in", "Frame")],
          [("out", "Frame")],
          description="Draw the category, then the numbers from a fitted "
                      "per-category model plus noise. Keeps the relationships "
                      "and is the worst of the four on marginals."),
    _node("generate.marginal", "synth.generate", [("in", "Frame")],
          [("out", "Frame")],
          description="Each column resampled independently. Every histogram "
                      "matches and every relationship is gone."),
    _node("generate.overconfident", "synth.generate", [("in", "Frame")],
          [("out", "Frame")],
          description="Draws the columns independently, then computes the "
                      "target from a confident, wrong, noiseless rule. "
                      "Internally perfect and externally useless — the one "
                      "generator that train-on-synthetic-test-on-synthetic "
                      "cannot see through."),
    _node("generate.noisy_copy", "synth.generate", [("in", "Frame")],
          [("out", "Frame")],
          description="Real rows with a little noise added. Zero exact "
                      "duplicates and effectively no privacy at all."),
    _node("generate.copy", "synth.generate", [("in", "Frame")],
          [("out", "Frame")],
          description="Resample the real rows. The best-scoring generator on "
                      "any metric that is not looking for this."),

    _node("fidelity.joint", "synth.fidelity",
          [("synthetic", "Frame"), ("real", "Frame")], [("out", "Findings")],
          description="Marginals *and* the correlations between columns."),
    _node("fidelity.marginals", "synth.fidelity",
          [("synthetic", "Frame"), ("real", "Frame")], [("out", "Findings")],
          description="Per column only. What a fidelity report usually is."),

    _node("utility.tstr", "synth.utility",
          [("synthetic", "Frame"), ("holdout", "Frame")], [("out", "Score")],
          description="Train on synthetic, test on real held-out rows."),
    _node("utility.tsts", "synth.utility",
          [("synthetic", "Frame"), ("holdout", "Frame")], [("out", "Score")],
          description="Train on synthetic, test on synthetic. Measures "
                      "self-consistency, which every generator has."),

    _node("privacy.nearest", "synth.privacy",
          [("synthetic", "Frame"), ("train", "Frame")], [("out", "Findings")],
          description="Distance from each synthetic row to the nearest real "
                      "one."),
    _node("privacy.exact", "synth.privacy",
          [("synthetic", "Frame"), ("train", "Frame")], [("out", "Findings")],
          description="Identical rows only. Defeated by adding noise in the "
                      "sixth decimal place."),

    _node("decide.all_three", "synth.decide",
          [("fidelity", "Findings"), ("utility", "Score"),
           ("privacy", "Findings")], [("out", "Verdict")],
          description="All three have to hold. Rejects the copier."),
    _node("decide.utility_only", "synth.decide",
          [("fidelity", "Findings"), ("utility", "Score"),
           ("privacy", "Findings")], [("out", "Verdict")],
          description="Ships if a model trained on it works. Reads the other "
                      "two and records them, which is not the same as "
                      "ignoring them — and still ships the copier."),
)


def workbench() -> WorkbenchDefinition:
    template = _template(TEMPLATE)
    bench = template.instantiate(FILLING)
    return WorkbenchDefinition(
        title=template.title, task=template.task,
        stages=bench.stages, edges=bench.edges, nodes=NODES,
        candidates=tuple(NodeCandidate(id=n.id, node_id=n.id) for n in NODES),
        optimization_profiles=(OptimizationProfile(
            id="p.synth", name="Faithful, useful, and not a copy",
            objectives=(OptimizationObjective("quality", "maximize", 1.0),)),),
        metadata=dict(bench.metadata, pack="synth"),
    ).assert_valid()


# --- the data ---------------------------------------------------------------

def _make_rows(n: int, seed: int = 11) -> list[dict[str, Any]]:
    """price = 40*size + 3*age + kind effect + noise. A relationship to lose."""
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        kind = rng.choice(KINDS)
        size = round(rng.uniform(0.5, 4.0), 3)
        age = round(rng.uniform(0.0, 60.0), 1)
        price = (40 * size + 3 * age + KIND_EFFECT[kind]
                 + rng.gauss(0, 6))
        rows.append({"size": size, "age": age, "kind": kind,
                     "price": round(price, 3)})
    return rows


# --- small numerical helpers (no numpy) -------------------------------------

def _column(rows, name):
    return [float(r[name]) for r in rows]


def _corr(xs, ys) -> float:
    if len(xs) < 2:
        return 0.0
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx and dy else 0.0


def _design(rows) -> tuple[list[list[float]], list[float]]:
    matrix, target = [], []
    for row in rows:
        features = [1.0, float(row["size"]), float(row["age"])]
        features += [1.0 if row["kind"] == k else 0.0 for k in KINDS[1:]]
        matrix.append(features)
        target.append(float(row[TARGET]))
    return matrix, target


def _least_squares(matrix, target, ridge: float = 1e-6) -> list[float]:
    """Normal equations with a tiny ridge, solved by Gaussian elimination."""
    width = len(matrix[0])
    gram = [[sum(row[i] * row[j] for row in matrix) + (ridge if i == j else 0.0)
             for j in range(width)] for i in range(width)]
    rhs = [sum(row[i] * t for row, t in zip(matrix, target, strict=True))
           for i in range(width)]
    for i in range(width):
        pivot = max(range(i, width), key=lambda r: abs(gram[r][i]))
        if abs(gram[pivot][i]) < 1e-12:
            continue
        gram[i], gram[pivot] = gram[pivot], gram[i]
        rhs[i], rhs[pivot] = rhs[pivot], rhs[i]
        for r in range(i + 1, width):
            factor = gram[r][i] / gram[i][i]
            for c in range(i, width):
                gram[r][c] -= factor * gram[i][c]
            rhs[r] -= factor * rhs[i]
    weights = [0.0] * width
    for i in reversed(range(width)):
        if abs(gram[i][i]) < 1e-12:
            continue
        weights[i] = (rhs[i] - sum(gram[i][j] * weights[j]
                                   for j in range(i + 1, width))) / gram[i][i]
    return weights


def _r2(rows_train, rows_test) -> float:
    """Fit on one set, score on the other. Clipped at zero and honest about it.

    A negative R² means the model is worse than predicting the mean, and the
    distinction between -0.3 and -40 is not one this pack needs; what matters
    is that it is on the floor.
    """
    if len(rows_train) < 6 or len(rows_test) < 3:
        return 0.0
    weights = _least_squares(*_design(rows_train))
    matrix, truth = _design(rows_test)
    mean = statistics.fmean(truth)
    residual = sum((t - sum(w * x for w, x in zip(weights, row, strict=True))) ** 2
                   for row, t in zip(matrix, truth, strict=True))
    total = sum((t - mean) ** 2 for t in truth)
    return max(0.0, 1 - residual / total) if total else 0.0


# --- the implementations ----------------------------------------------------

def _split(rows, *, shuffle: bool) -> dict[str, Any]:
    ordered = list(rows)
    if shuffle:
        rng = random.Random(7)
        rng.shuffle(ordered)
    cut = max(4, int(len(ordered) * 0.7))
    return {"train": ordered[:cut], "holdout": ordered[cut:]}


def _generate(train, how: str) -> list[dict[str, Any]]:
    rng = random.Random(23)
    n = len(train)

    if how == "generate.copy":
        return [dict(rng.choice(train)) for _ in range(n)]

    if how == "generate.noisy_copy":
        spread = {c: statistics.pstdev(_column(train, c)) or 1.0
                  for c in (*NUMERIC, TARGET)}
        out = []
        for _ in range(n):
            base = dict(rng.choice(train))
            for column in (*NUMERIC, TARGET):
                # A thousandth of a standard deviation. Enough to make every
                # row unique and not enough to make any row private.
                base[column] = round(base[column]
                                     + rng.gauss(0, spread[column] * 0.001), 4)
            out.append(base)
        return out

    if how == "generate.overconfident":
        # A generator with a strong internal structure that is not the real
        # one. Every synthetic row obeys `price = 40 * size` exactly, so a
        # model fitted to synthetic data explains it perfectly — and the real
        # relationship also involves age and the category, neither of which is
        # in here at all.
        sizes, ages = _column(train, "size"), _column(train, "age")
        kinds = [r[CATEGORICAL] for r in train]
        return [{"size": (size := round(rng.choice(sizes), 3)),
                 "age": round(rng.choice(ages), 1),
                 CATEGORICAL: rng.choice(kinds),
                 TARGET: round(40 * size, 3)} for _ in range(n)]

    if how == "generate.marginal":
        pools = {c: _column(train, c) for c in (*NUMERIC, TARGET)}
        kinds = [r[CATEGORICAL] for r in train]
        return [{**{c: round(rng.choice(pools[c]), 3)
                    for c in (*NUMERIC, TARGET)},
                 CATEGORICAL: rng.choice(kinds)} for _ in range(n)]

    # conditional: fit the real relationship, then draw from it.
    weights = _least_squares(*_design(train))
    residuals = []
    matrix, truth = _design(train)
    for row, t in zip(matrix, truth, strict=True):
        residuals.append(t - sum(w * x for w, x in zip(weights, row, strict=True)))
    noise = statistics.pstdev(residuals) or 1.0
    sizes, ages = _column(train, "size"), _column(train, "age")
    kinds = [r[CATEGORICAL] for r in train]

    out = []
    for _ in range(n):
        kind = rng.choice(kinds)
        size = round(rng.uniform(min(sizes), max(sizes)), 3)
        age = round(rng.uniform(min(ages), max(ages)), 1)
        features = [1.0, size, age] + [1.0 if kind == k else 0.0
                                       for k in KINDS[1:]]
        price = sum(w * x for w, x in zip(weights, features, strict=True))
        out.append({"size": size, "age": age, CATEGORICAL: kind,
                    TARGET: round(price + rng.gauss(0, noise), 3)})
    return out


def _fidelity(synthetic, real, *, joint: bool) -> dict[str, Any]:
    columns = (*NUMERIC, TARGET)
    marginal_gaps = {}
    for column in columns:
        got, want = _column(synthetic, column), _column(real, column)
        spread = statistics.pstdev(want) or 1.0
        marginal_gaps[column] = abs(statistics.fmean(got)
                                    - statistics.fmean(want)) / spread
    marginals = max(0.0, 1 - statistics.fmean(marginal_gaps.values()))

    pairs, gaps = [], []
    for i, left in enumerate(columns):
        for right in columns[i + 1:]:
            got = _corr(_column(synthetic, left), _column(synthetic, right))
            want = _corr(_column(real, left), _column(real, right))
            pairs.append({"pair": f"{left}~{right}", "synthetic": round(got, 3),
                          "real": round(want, 3)})
            gaps.append(abs(got - want))
    joint_score = max(0.0, 1 - statistics.fmean(gaps)) if gaps else 0.0

    if not joint:
        return {"check": "fidelity.marginals", "score": marginals,
                "marginals": marginals,
                # Computed either way so the two candidates can be compared on
                # one run, and so "we only looked at marginals" is visible.
                "joint": None, "pairs": pairs, "looked_at_joint": False}
    return {"check": "fidelity.joint", "score": min(marginals, joint_score),
            "marginals": marginals, "joint": joint_score, "pairs": pairs,
            "looked_at_joint": True}


def _utility(synthetic, holdout, *, on_real: bool) -> dict[str, Any]:
    if on_real:
        score = _r2(synthetic, holdout)
        return {"check": "utility.tstr", "score": score,
                "tested_on": "real held-out rows", "n_test": len(holdout)}
    cut = max(4, int(len(synthetic) * 0.7))
    score = _r2(synthetic[:cut], synthetic[cut:])
    return {"check": "utility.tsts", "score": score,
            "tested_on": "synthetic rows", "n_test": len(synthetic) - cut}


def _privacy(synthetic, train, *, nearest: bool) -> dict[str, Any]:
    columns = (*NUMERIC, TARGET)
    spread = {c: statistics.pstdev(_column(train, c)) or 1.0 for c in columns}

    if not nearest:
        seen = {tuple(round(float(r[c]), 6) for c in columns) + (r[CATEGORICAL],)
                for r in train}
        copies = sum(1 for r in synthetic
                     if tuple(round(float(r[c]), 6) for c in columns)
                     + (r[CATEGORICAL],) in seen)
        share = copies / len(synthetic) if synthetic else 0.0
        return {"check": "privacy.exact", "copied": share, "risk": share,
                "exact_copies": copies,
                "findings": ([{"what": f"{copies} synthetic rows are identical "
                                       f"to a training row"}] if copies else []),
                "note": "identical rows only; noise in the last decimal place "
                        "defeats this entirely"}

    close = 0
    distances = []
    for row in synthetic:
        best = min(
            (sum(((float(row[c]) - float(other[c])) / spread[c]) ** 2
                 for c in columns) ** 0.5
             for other in train), default=float("inf"))
        distances.append(best)
        if best < TOO_CLOSE:
            close += 1
    share = close / len(synthetic) if synthetic else 0.0
    return {"check": "privacy.nearest", "copied": share, "risk": share,
            "median_distance": round(statistics.median(distances), 6)
            if distances else None,
            "findings": ([{"what": f"{close} of {len(synthetic)} synthetic "
                                   f"rows sit within {TOO_CLOSE} of a real "
                                   f"one"}] if close else []),
            "note": ""}


def _decide(fidelity, utility, privacy, *, all_three: bool) -> dict[str, Any]:
    reasons = []
    if fidelity["score"] < 0.75:
        reasons.append(f"fidelity {fidelity['score']:.2f} below 0.75 "
                       f"({fidelity['check']})")
    if utility["score"] < 0.6:
        reasons.append(f"utility {utility['score']:.2f} below 0.60 "
                       f"({utility['check']})")
    if privacy["risk"] > 0.05:
        reasons.append(f"{privacy['risk']:.0%} of rows are copies "
                       f"({privacy['check']})")

    if all_three:
        allow, rule = not reasons, "all three"
    else:
        allow = utility["score"] >= 0.6
        rule = "utility only"

    return {"allow": allow, "rule": rule, "reasons": reasons,
            "fidelity": fidelity["score"], "utility": utility["score"],
            "privacy_risk": privacy["risk"],
            "checks": [fidelity["check"], utility["check"], privacy["check"]],
            # Present whichever rule ran, so a report cannot quietly omit the
            # arm that would have refused.
            "would_all_three_allow": not reasons}


def runtime(**_: Any) -> Runtime:
    big, small = _make_rows(400), _make_rows(60, seed=5)
    return Runtime({
        "real.rows": lambda **kw: big,
        "real.small": lambda **kw: small,
        "split.random": lambda **kw: _split(kw["in"], shuffle=True),
        "split.ordered": lambda **kw: _split(kw["in"], shuffle=False),
        "generate.conditional": lambda **kw: _generate(kw["in"],
                                                       "generate.conditional"),
        "generate.marginal": lambda **kw: _generate(kw["in"],
                                                    "generate.marginal"),
        "generate.overconfident": lambda **kw: _generate(
            kw["in"], "generate.overconfident"),
        "generate.noisy_copy": lambda **kw: _generate(kw["in"],
                                                      "generate.noisy_copy"),
        "generate.copy": lambda **kw: _generate(kw["in"], "generate.copy"),
        "fidelity.joint": lambda **kw: _fidelity(kw["synthetic"], kw["real"],
                                                 joint=True),
        "fidelity.marginals": lambda **kw: _fidelity(kw["synthetic"],
                                                     kw["real"], joint=False),
        "utility.tstr": lambda **kw: _utility(kw["synthetic"], kw["holdout"],
                                              on_real=True),
        "utility.tsts": lambda **kw: _utility(kw["synthetic"], kw["holdout"],
                                              on_real=False),
        "privacy.nearest": lambda **kw: _privacy(kw["synthetic"], kw["train"],
                                                 nearest=True),
        "privacy.exact": lambda **kw: _privacy(kw["synthetic"], kw["train"],
                                               nearest=False),
        "decide.all_three": lambda **kw: _decide(kw["fidelity"], kw["utility"],
                                                 kw["privacy"], all_three=True),
        "decide.utility_only": lambda **kw: _decide(kw["fidelity"],
                                                    kw["utility"],
                                                    kw["privacy"],
                                                    all_three=False),
    })


def example() -> dict[str, Any]:
    return {}


def worth_shipping(run) -> tuple[bool, float]:
    """Faithful and useful, with memorisation charged at full price.

    The multiplication rather than a weighted sum is the whole argument: a
    generator that copies scores 1.0 and 0.96 on the first two terms, and no
    weighting that leaves it a positive score is defensible, because the
    failure is categorical. Multiplying by `1 - risk` makes a copier worth
    nothing however well it did on everything else.
    """
    if not run.ok:
        return False, 0.0
    verdict = run.output("decide")
    quality = min(verdict["fidelity"], max(0.0, verdict["utility"]))
    return bool(verdict["allow"] and verdict["would_all_three_allow"]), \
        max(0.0, quality * (1 - verdict["privacy_risk"]))
