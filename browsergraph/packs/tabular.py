"""A supervised tabular pipeline, in pure Python, that actually fits something.

Nine steps, a genuine fan-out over the two kinds of column, and a join before
the model. The shape most people write as a script, expressed as a graph so the
choices in it are visible and comparable.

    from browsergraph import packs
    print(packs.get("tabular").solve().text())

**No numpy.** The core of this project has no dependencies and a pack that
required one would make that claim false at import time. Least squares here is
solved by Gaussian elimination on the normal equations — real arithmetic, small
matrices, and entirely replaceable: swap `fit.least_squares` for a scikit-learn
node and nothing else in the graph changes. That is the point of candidates.

Two things this pack is careful about, because they are the ways a tabular
pipeline lies to you:

**The split comes first, and it is a step.** `split` runs before `clean`, so
anything learned while cleaning is learned from training rows only. A pipeline
that cleans before splitting has already leaked, and the leak is invisible in
every metric it reports afterwards.

**`evaluate` scores on the held-out rows.** `fit` never sees them. Scoring on
training data is the single most common way a tabular result is overstated, and
it is not something a graph can prevent — but it *is* something a graph can make
visible, because the split's two output ports go to different places and you can
see it in the picture.

**The encoding is part of the model, and this pack shipped a version where it
was not.** Evaluation re-derived the category levels from the held-out rows, so
the model's fourth weight meant "is a flat" while the fourth column of the
held-out design meant "is a house". Nothing raised. Every number was arithmetic
on mismatched columns, and the damage was not that the score was noisy — it was
that the *wrong encoder won*:

    encoding             before the fix     after
    categorical.onehot        63.91         10.77     <- correct, and now wins
    categorical.ordinal       41.03         41.03

The noise in the data has a standard deviation of 12, so 10.77 is about as good
as this data allows. The bug made the right answer look like the worst one, and
a search told to minimise error would have confidently chosen wrong. Fills,
scaling statistics and category levels are now learned on training rows and
carried with the model.
"""
from __future__ import annotations

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

TEMPLATE = "tabular.supervised"
SUMMARY = "load a table, encode two kinds of column at once, fit and score"

FILLING: dict[str, list[str]] = {
    "load":        ["load.rows"],
    "split":       ["split.random", "split.ordered"],
    "clean":       ["clean.drop", "clean.impute_mean", "clean.impute_median"],
    "numeric":     ["numeric.raw", "numeric.standard"],
    "categorical": ["categorical.onehot", "categorical.ordinal"],
    "assemble":    ["assemble.concat"],
    "fit":         ["fit.least_squares", "fit.ridge"],
    "calibrate":   ["calibrate.none", "calibrate.shift"],
    "evaluate":    ["evaluate.mae", "evaluate.rmse"],
}


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
    _node("load.rows", "data.read", [], [("out", "Frame")],
          description="The rows handed to the runtime."),

    _node("split.random", "data.split", [("in", "Frame")],
          [("train", "Frame"), ("valid", "Frame")],
          description="A seeded random split.", runtime={"deterministic": True}),
    _node("split.ordered", "data.split", [("in", "Frame")],
          [("train", "Frame"), ("valid", "Frame")],
          description="The last fifth held out in order. The only correct one "
                      "if the rows are a time series."),

    _node("clean.drop", "data.clean", [("in", "Frame")], [("out", "Frame")],
          description="Drop any row with a gap in it."),
    _node("clean.impute_mean", "data.clean", [("in", "Frame")], [("out", "Frame")],
          description="Fill numeric gaps with the column mean."),
    _node("clean.impute_median", "data.clean", [("in", "Frame")], [("out", "Frame")],
          description="Fill numeric gaps with the column median."),

    _node("numeric.raw", "feature.numeric", [("in", "Frame")], [("out", "Matrix")],
          description="The numbers as they are."),
    _node("numeric.standard", "feature.numeric", [("in", "Frame")],
          [("out", "Matrix")],
          description="Centred and scaled by the training spread."),

    _node("categorical.onehot", "feature.categorical", [("in", "Frame")],
          [("out", "Matrix")], description="One column per value."),
    _node("categorical.ordinal", "feature.categorical", [("in", "Frame")],
          [("out", "Matrix")],
          description="One column of integers. Wrong unless the values really "
                      "are ordered, and kept so the search can show that."),

    _node("assemble.concat", "feature.assemble",
          [("numeric", "Matrix"), ("categorical", "Matrix")], [("out", "Matrix")],
          description="Put the two feature blocks side by side."),

    _node("fit.least_squares", "model.fit", [("in", "Matrix")], [("out", "Model")],
          description="Ordinary least squares by Gaussian elimination."),
    _node("fit.ridge", "model.fit", [("in", "Matrix")], [("out", "Model")],
          description="Least squares with a small penalty, which also rescues "
                      "the singular case one-hot encoding creates."),

    _node("calibrate.none", "model.calibrate", [("in", "Model")], [("out", "Model")],
          description="Leave it alone. Doing nothing is a candidate."),
    _node("calibrate.shift", "model.calibrate", [("in", "Model")],
          [("out", "Model")],
          description="Remove the mean training residual."),

    _node("evaluate.mae", "model.evaluate", [("in", "Model")], [("out", "Score")],
          description="Mean absolute error on the held-out rows."),
    _node("evaluate.rmse", "model.evaluate", [("in", "Model")], [("out", "Score")],
          description="Root mean squared error on the held-out rows."),
)


def workbench() -> WorkbenchDefinition:
    template = _template(TEMPLATE)
    bench = template.instantiate(FILLING)
    return WorkbenchDefinition(
        title=template.title, task=template.task,
        stages=bench.stages, edges=bench.edges, nodes=NODES,
        candidates=tuple(NodeCandidate(id=n.id, node_id=n.id) for n in NODES),
        optimization_profiles=(OptimizationProfile(
            id="p.accuracy", name="Lowest held-out error",
            objectives=(OptimizationObjective("quality", "maximize", 1.0),)),),
        metadata=dict(bench.metadata, pack="tabular"),
    ).assert_valid()


# --- the arithmetic ---------------------------------------------------------

def _solve_normal(xtx: list[list[float]], xty: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting. Small, and enough.

    Partial pivoting is not optional here: without it a zero on the diagonal —
    which one-hot encoding produces routinely — divides by zero instead of
    swapping a row. Ridge exists as a candidate for the case where the matrix is
    genuinely singular rather than merely awkwardly ordered.
    """
    size = len(xtx)
    matrix = [row[:] + [xty[i]] for i, row in enumerate(xtx)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(matrix[r][column]))
        if abs(matrix[pivot][column]) < 1e-12:
            raise ValueError(
                f"the feature matrix is singular at column {column}: these "
                f"features are exactly redundant. Use fit.ridge, or drop one "
                f"level of the one-hot encoding.")
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        for row in range(size):
            if row == column:
                continue
            factor = matrix[row][column] / matrix[column][column]
            for k in range(column, size + 1):
                matrix[row][k] -= factor * matrix[column][k]
    return [matrix[i][size] / matrix[i][i] for i in range(size)]


def _fit(design: list[list[float]], target: list[float],
         penalty: float = 0.0) -> list[float]:
    width = len(design[0])
    xtx = [[sum(row[i] * row[j] for row in design) for j in range(width)]
           for i in range(width)]
    for i in range(width):
        xtx[i][i] += penalty
    xty = [sum(row[i] * y for row, y in zip(design, target, strict=True))
           for i in range(width)]
    return _solve_normal(xtx, xty)


def _predict(design, weights) -> list[float]:
    return [sum(v * w for v, w in zip(row, weights, strict=True))
            for row in design]


# --- the implementations ----------------------------------------------------

NUMERIC = ("size", "age")
CATEGORICAL = ("kind",)
TARGET = "price"


def _split_rows(rows, *, random_order: bool, seed: int = 7):
    rows = list(rows)
    if random_order:
        shuffled = list(rows)
        random.Random(seed).shuffle(shuffled)
        rows = shuffled
    cut = max(1, int(len(rows) * 0.8))
    return {"train": rows[:cut], "valid": rows[cut:] or rows[-1:]}


def _fills(frame, how: str) -> dict[str, float]:
    """What each numeric gap should be filled with, from these rows."""
    out = {}
    for column in NUMERIC:
        seen = [r[column] for r in frame if r.get(column) is not None]
        if seen:
            out[column] = (statistics.fmean(seen) if how == "mean"
                           else statistics.median(seen))
    return out


def _clean(frame, *, how: str, fills: dict | None = None):
    """Deal with the gaps. `fills` reuses values already learned from training.

    Same rule as the encoders: a mean computed from the held-out rows is not the
    mean the model was fitted with, and using it is a leak that no metric will
    reveal. `_clean` records what it used so evaluation can reuse it.
    """
    rows = list(frame)
    if how == "drop":
        return [r for r in rows
                if all(r.get(c) is not None for c in (*NUMERIC, *CATEGORICAL))]

    if fills is None:
        fills = _fills(rows, how)
    out = []
    for row in rows:
        filled = dict(row)
        for column, value in fills.items():
            if filled.get(column) is None:
                filled[column] = value
        # A gap in a category cannot be averaged, so it becomes its own level.
        # Silently dropping those rows here would make the two imputers quietly
        # disagree with `clean.drop` about how much data there is.
        for column in CATEGORICAL:
            if filled.get(column) is None:
                filled[column] = "unknown"
        out.append(filled)
    return out


def _numeric(frame, *, standard: bool, stats: dict | None = None):
    """Numeric features. `stats` fixes the centring and scaling.

    Passing the training statistics in is not an optimisation, it is the whole
    correctness of the step: standardising held-out rows by their *own* mean and
    spread is a different transform from the one the model was fitted under, and
    the error it causes is silent.
    """
    rows = list(frame)
    if stats is None:
        stats = {}
        for column in NUMERIC:
            values = [float(r[column]) for r in rows]
            stats[column] = (statistics.fmean(values),
                             statistics.pstdev(values) or 1.0)
    block = []
    for row in rows:
        if standard:
            block.append([(float(row[c]) - stats[c][0]) / stats[c][1]
                          for c in NUMERIC])
        else:
            block.append([float(row[c]) for c in NUMERIC])
    return {"block": block, "rows": rows, "names": list(NUMERIC),
            "stats": stats}


def _categorical(frame, *, onehot: bool, levels: list[str] | None = None):
    """Categorical features. `levels` fixes what each column means.

    **This is the bug this pack shipped with, and it is worth naming.** The
    first version derived the levels from whatever rows it was handed — so the
    held-out set, holding a different set of categories, produced a block whose
    third column meant "is a house" where the model's third weight meant "is a
    flat". Every number downstream was arithmetic on mismatched columns, nothing
    raised, and the reported error was merely wrong rather than obviously wrong.

    An encoding is part of the model. It is learned on training rows and then
    applied, never re-derived.
    """
    rows = list(frame)
    if levels is None:
        levels = sorted({str(r[c]) for r in rows for c in CATEGORICAL})
    kept = levels[:-1]
    block = []
    for row in rows:
        if onehot:
            # The last level is dropped. Keeping every level plus an intercept
            # makes the matrix exactly singular, and "it is singular" is a much
            # worse error message than never creating the redundancy.
            block.append([1.0 if str(row[c]) == level else 0.0
                          for c in CATEGORICAL for level in kept])
        else:
            # A level never seen in training gets the end of the scale. Made
            # explicit because `levels.index` would raise, and a crash at
            # evaluation on an unseen category is a worse answer than a number
            # with a stated convention behind it.
            block.append([float(levels.index(str(row[c])))
                          if str(row[c]) in levels else float(len(levels))
                          for c in CATEGORICAL])
    names = ([f"{c}={v}" for c in CATEGORICAL for v in kept] if onehot
             else list(CATEGORICAL))
    return {"block": block, "rows": rows, "names": names, "levels": levels}


def _assemble(numeric, categorical):
    rows = numeric["rows"]
    design = [[1.0, *n, *c] for n, c in zip(numeric["block"],
                                            categorical["block"], strict=True)]
    return {"design": design, "rows": rows,
            "names": ["intercept", *numeric["names"], *categorical["names"]],
            "target": [float(r[TARGET]) for r in rows],
            # Everything needed to reproduce this transform on rows the model
            # has never seen. Carried with the features so it reaches the model,
            # because a model without its encoding cannot be applied to anything.
            "encoding": {"stats": numeric["stats"], "levels": categorical["levels"]}}


def _fit_model(matrix, *, penalty: float):
    weights = _fit(matrix["design"], matrix["target"], penalty)
    fitted = _predict(matrix["design"], weights)
    residuals = [y - f for y, f in zip(matrix["target"], fitted, strict=True)]
    return {"weights": weights, "names": matrix["names"], "shift": 0.0,
            "train_residual_mean": statistics.fmean(residuals),
            "penalty": penalty, "encoding": matrix["encoding"]}


def _calibrate(model, *, shift: bool):
    if not shift:
        return dict(model)
    return {**model, "shift": model["train_residual_mean"]}


def _evaluate(model, held_out, *, squared: bool):
    """Score on the rows `fit` never saw. That is the whole point of the split.

    The held-out rows are cleaned and encoded with the *training* recipe — the
    fills, the scaling statistics and the category levels the model was fitted
    under. Re-deriving any of them from these rows would be measuring a
    different model, and would do it silently.
    """
    encoding = model["encoding"]
    rows = _clean(held_out["rows"], how=held_out["how"], fills=held_out["fills"])
    numeric = _numeric(rows, standard=held_out["standard"],
                       stats=encoding["stats"])
    categorical = _categorical(rows, onehot=held_out["onehot"],
                               levels=encoding["levels"])
    matrix = _assemble(numeric, categorical)
    design = matrix["design"]
    if len(design[0]) != len(model["weights"]):     # pragma: no cover - guard
        raise ValueError(
            f"the held-out design has {len(design[0])} columns and the model "
            f"has {len(model['weights'])} weights. The encoding did not travel "
            f"with the model.")

    predicted = [p + model["shift"] for p in _predict(design, model["weights"])]
    errors = [abs(y - p) for y, p in zip(matrix["target"], predicted, strict=True)]
    score = (statistics.fmean([e * e for e in errors]) ** 0.5 if squared
             else statistics.fmean(errors))
    return {"metric": "rmse" if squared else "mae", "score": score,
            "held_out_rows": len(errors),
            "predicted": [round(p, 2) for p in predicted],
            "actual": matrix["target"]}


def runtime(rows: list[dict[str, Any]]) -> Runtime:
    """One function per candidate, over `rows`.

    The held-out frame is threaded to `evaluate` through a mutable box rather
    than the graph, because the template wires `split.valid` nowhere — the
    shape says training flows through cleaning and features, and the validation
    rows meet the model at the end. Keeping that outside the graph is honest
    about the template's shape; the alternative is a second edge the template
    does not have.
    """
    held: dict[str, Any] = {"rows": [], "standard": False, "onehot": True,
                            "how": "drop", "fills": None}

    def split(random_order: bool):
        def call(**kwargs):
            parts = _split_rows(kwargs["in"], random_order=random_order)
            held["rows"] = parts["valid"]
            return parts
        return call

    def clean(how: str):
        def call(**kwargs):
            cleaned = _clean(kwargs["in"], how=how)
            held["how"] = how
            # Recompute the fills from the training rows and remember them, so
            # evaluation applies the same numbers rather than its own.
            held["fills"] = None if how == "drop" else _fills(kwargs["in"], how)
            return cleaned
        return call

    def numeric(standard: bool):
        def call(**kwargs):
            held["standard"] = standard
            return _numeric(kwargs["in"], standard=standard)
        return call

    def categorical(onehot: bool):
        def call(**kwargs):
            held["onehot"] = onehot
            return _categorical(kwargs["in"], onehot=onehot)
        return call

    return Runtime({
        "load.rows": lambda **kw: list(rows),
        "split.random": split(True),
        "split.ordered": split(False),
        "clean.drop": clean("drop"),
        "clean.impute_mean": clean("mean"),
        "clean.impute_median": clean("median"),
        "numeric.raw": numeric(False),
        "numeric.standard": numeric(True),
        "categorical.onehot": categorical(True),
        "categorical.ordinal": categorical(False),
        "assemble.concat": lambda **kw: _assemble(kw["numeric"], kw["categorical"]),
        "fit.least_squares": lambda **kw: _fit_model(kw["in"], penalty=0.0),
        "fit.ridge": lambda **kw: _fit_model(kw["in"], penalty=0.1),
        "calibrate.none": lambda **kw: _calibrate(kw["in"], shift=False),
        "calibrate.shift": lambda **kw: _calibrate(kw["in"], shift=True),
        "evaluate.mae": lambda **kw: _evaluate(kw["in"], held, squared=False),
        "evaluate.rmse": lambda **kw: _evaluate(kw["in"], held, squared=True),
    })


def example() -> dict[str, Any]:
    """Rows with a signal a linear model can find, and two gaps to clean.

    Price is `40*size + 3*age + a kind effect + noise`, so a fitted model should
    land close and a badly-encoded one should visibly not. Deterministic, so the
    numbers are the same every run — a demonstration whose result moves is a
    demonstration nobody can check.
    """
    rng = random.Random(11)
    kinds = {"flat": 0.0, "house": 60.0, "studio": -25.0}
    rows: list[dict[str, Any]] = []
    for index in range(60):
        kind = list(kinds)[index % 3]
        size = round(rng.uniform(30, 120), 1)
        age = rng.randint(0, 40)
        price = 40 * size + 3 * age + kinds[kind] + rng.gauss(0, 12)
        rows.append({"size": size, "age": age, "kind": kind,
                     "price": round(price, 2)})
    rows[7]["size"] = None                     # a numeric gap
    rows[23]["kind"] = None                    # a categorical gap
    return {"rows": rows}
