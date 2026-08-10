#!/usr/bin/env python3
"""A model may suggest. It may not decide.

Everything a model returns is compiled, gated by policy and scored exactly like
a route that came from sampling. A hallucinated candidate gets a rejection
naming it; a legal but poor suggestion gets a low score. Neither outcome
requires trusting it — which is what makes this safe with a small local model,
a model having a bad day, or none at all.

Runs with a canned "model" by default so it works offline. Point it at a real
one with:

    OLLAMA_MODEL=glm-5.2:cloud python examples/06_model_suggests_you_check.py
"""
import json
import os

from browsergraph import explore, search
from browsergraph.quick import chain, graph, node, step
from browsergraph.workbench import OptimizationObjective, OptimizationProfile

PRIOR = {"read.fast": 0.9, "read.careful": 0.4}


def build():
    nodes = [node(c, "read", gives=[("out", "Rows")],
                  metrics={"source": "illustrative-prior", "quality": PRIOR[c]})
             for c in PRIOR]
    nodes.append(node("count.rows", "count", [("in", "Rows")], [("out", "N")]))
    steps = [
        step("read", "Read it", [], [("out", "Rows")], "read", list(PRIOR)),
        step("count", "Count", [("in", "Rows")], [("out", "N")], "count",
             ["count.rows"]),
    ]
    return graph("Read and count", "Read a file and count the rows.",
                 steps, nodes, chain("read", "count"),
                 profiles=[OptimizationProfile(id="p", objectives=(
                     OptimizationObjective("quality", "maximize", 1.0),))])


def canned(_prompt: str) -> str:
    """Three suggestions: one good, one hallucinated, one incomplete."""
    return "Here you go:\n```json\n" + json.dumps([
        {"route": {"read": "read.careful", "count": "count.rows"},
         "why": "careful reading is worth the time"},
        {"route": {"read": "read.imaginary", "count": "count.rows"},
         "why": "this one does not exist"},
        {"route": {"read": "read.fast"}, "why": "forgot a step"},
    ]) + "\n```"


bench = build()
profile = bench.optimization_profiles[0]

model = os.environ.get("OLLAMA_MODEL")
proposer = explore.ollama_proposer(model=model, timeout=180) if model else canned
print(f"asking: {model or 'a canned reply (set OLLAMA_MODEL for a real one)'}")

alone = search.within(bench, profile, evaluations=20, explore=0.0)
got = explore.guided(bench, profile, proposer, evaluations=20, explore=0.0)

print(f"\nsearch alone: {alone.route}  score {alone.score:.4f}")
print(f"with a model: {got.route}  score {got.score:.4f}  ({got.strategy})")

print(f"\n{len(got.suggestions)} suggestion(s), each checked:")
for s in got.suggestions:
    if s.accepted:
        print(f"  accepted, scored {s.score:.4f}   {s.why[:50]}")
    else:
        print(f"  REFUSED: {s.reason[:70]}")

print("\n" + next(n for n in got.notes if "model" in n))
print("\nThe model never bypassed a type check or a policy gate. It proposed;")
print("the compiler disposed. That is the whole safety story.")
