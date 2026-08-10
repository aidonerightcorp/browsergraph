"""Let a model suggest routes, and never let it decide one.

The library can already search a space it can enumerate, sample one it cannot,
and learn from what it ran. What it cannot do is *use what a model knows*. A
model has read a great deal about which tool suits which job, and that is real
information the graph does not have — the graph knows what connects, not what
tends to work.

The rule this module is built on is the same one that runs through the rest of
the repository, applied one level up:

    the model proposes, the compiler disposes.

A suggestion is a candidate for evaluation, never an answer. Every route a model
returns is compiled, gated by policy, and scored exactly like one that came from
sampling. A model that hallucinates a candidate id gets a rejection with a
reason; a model that suggests something legal but bad gets a low score. Neither
outcome requires trusting it.

That is what makes this safe to use with a small local model, or a model having
a bad day, or no model at all. The worst case is that it proposes nothing usable
and the search falls back to sampling, which is what it would have done anyway.

Nothing here imports an LLM client. `Proposer` is "a callable that takes a
string and returns a string", so an Ollama call, an HTTP request, a canned reply
in a test and a human typing all satisfy it.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from browsergraph.policy import Policy
from browsergraph.workbench import OptimizationProfile, WorkbenchDefinition

#: Anything that turns a prompt into a reply. Deliberately the smallest
#: interface that can work, so nothing in this file depends on a client.
Proposer = Callable[[str], str]


@dataclass
class Suggestion:
    """One route a model proposed, and what became of it."""
    route: dict[str, str] = field(default_factory=dict)
    why: str = ""
    accepted: bool = False
    reason: str = ""
    score: float = 0.0

    def to_dict(self) -> dict:
        return {"route": dict(self.route), "why": self.why,
                "accepted": self.accepted, "reason": self.reason,
                "score": round(self.score, 6)}


def describe(bench: WorkbenchDefinition, *, evidence=None,
             context: Sequence[str] = ("global",), max_candidates: int = 12
             ) -> str:
    """The graph as text a model can read, with what is known so far.

    Deliberately compact. A model given every candidate of a 166-candidate
    workbench spends its attention reading a list, and the list is the part it
    is worst at. Capped per stage, and the cap is stated in the text rather than
    hidden, because a model told "12 of 76 shown" can ask for the rest and a
    model shown 12 silently cannot.
    """
    lines = [f"TASK: {bench.task or bench.title}", ""]
    if bench.success:
        lines += [f"SUCCESS MEANS: {bench.success}", ""]
    lines.append("STEPS, in order, with the options for each:")

    for stage in bench.leaf_stages:
        shown = list(stage.candidates[:max_candidates])
        hidden = len(stage.candidates) - len(shown)
        ports_in = ", ".join(f"{p.name}:{p.type}" for p in stage.inputs) or "—"
        ports_out = ", ".join(f"{p.name}:{p.type}" for p in stage.outputs)
        lines.append(f"\n  {stage.id}  ({stage.name})")
        lines.append(f"    takes {ports_in}  ->  gives {ports_out}")
        for cid in shown:
            note = ""
            if evidence is not None:
                posterior = evidence.posterior(cid, context)
                if posterior.runs:
                    note = (f"   [{posterior.runs} runs, "
                            f"{posterior.rate:.0%} worked]")
            lines.append(f"      - {cid}{note}")
        if hidden > 0:
            lines.append(f"      ... and {hidden} more not shown")

    if evidence is not None:
        clashes = evidence.interactions(minimum=10)
        if clashes:
            lines.append("\nPAIRS THAT DID WORSE TOGETHER THAN APART:")
            for a, b, gap, runs in clashes[:5]:
                lines.append(f"  {a} + {b}  ({gap:+.2f} over {runs} runs)")

    lines.append(
        "\nSuggest up to 3 complete routes: one candidate id per step. "
        "Reply as JSON only, like:\n"
        '[{"route": {"step_id": "candidate.id"}, "why": "one short sentence"}]'
    )
    return "\n".join(lines)


def parse(reply: str, bench: WorkbenchDefinition) -> list[Suggestion]:
    """Pull route suggestions out of whatever the model actually said.

    Models wrap JSON in prose, in code fences, or in an explanation nobody
    asked for. Being strict about the envelope means discarding good
    suggestions over formatting, so the first JSON array in the reply is used
    and the rest is ignored.

    What is *not* lenient is the content. A step that does not exist, or a
    candidate not admitted to it, is refused here with a reason — before the
    compiler, so the message names the mistake rather than the consequence.
    """
    if not reply or not reply.strip():
        return []

    block = re.search(r"\[.*\]", reply, re.S)
    if not block:
        return []
    try:
        raw = json.loads(block.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []

    stages = {s.id: set(s.candidates) for s in bench.leaf_stages}
    out: list[Suggestion] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        route = item.get("route")
        if not isinstance(route, Mapping):
            continue
        suggestion = Suggestion(route={str(k): str(v) for k, v in route.items()},
                                why=str(item.get("why", ""))[:200])

        missing = [sid for sid in stages if sid not in suggestion.route]
        unknown = [sid for sid in suggestion.route if sid not in stages]
        wrong = [f"{sid}={cid}" for sid, cid in suggestion.route.items()
                 if sid in stages and cid not in stages[sid]]
        if missing:
            suggestion.reason = f"no candidate for {', '.join(sorted(missing))}"
        elif unknown:
            suggestion.reason = f"no such step: {', '.join(sorted(unknown))}"
        elif wrong:
            suggestion.reason = f"not admitted to that step: {', '.join(wrong)}"
        else:
            suggestion.accepted = True
        out.append(suggestion)
    return out


def guided(bench: WorkbenchDefinition, profile: OptimizationProfile,
           proposer: Proposer, *, policy: Policy | None = None,
           evidence=None, context: Sequence[str] = ("global",),
           evaluations: int = 200, seed: int = 0, explore: float = 1.0,
           max_candidates: int = 12):
    """Ask a model for routes, check them, and keep the best of everything.

    The model's suggestions never replace the search — they join it. Whatever it
    proposes is compiled and scored, then compared against what the ordinary
    budgeted search finds on its own, and the better one wins. A model that
    helps, helps; a model that does not, costs one call.

    The returned proposal carries `suggestions` so the model's contribution is
    auditable after the fact: what it proposed, what was refused and why, and
    whether its best beat the baseline. A model that is quietly ignored looks
    identical to one that is quietly followed unless somebody writes that down.
    """
    from browsergraph import search
    from browsergraph.compile import CompileError, compile_route
    from browsergraph.policy import aggregate

    baseline = search.within(bench, profile, evaluations=evaluations,
                             policy=policy, seed=seed, evidence=evidence,
                             context=context, explore=explore)

    try:
        reply = proposer(describe(bench, evidence=evidence, context=context,
                                  max_candidates=max_candidates))
    except Exception as problem:                   # noqa: BLE001 - reported
        baseline.notes += (f"the model was asked and could not answer "
                           f"({type(problem).__name__}); using the search alone",)
        baseline.suggestions = ()
        return baseline

    suggestions = parse(reply, bench)
    if not suggestions:
        baseline.notes += ("the model returned nothing usable; using the search "
                           "alone",)
        baseline.suggestions = ()
        return baseline

    # Score every legal suggestion the same way the search scores its own.
    eligible, _blocked = search._eligible_by_stage(
        bench, policy or Policy.permissive())
    stages = list(bench.leaf_stages)
    overrides = None
    if evidence is not None:
        from browsergraph.evidence import measured_metrics
        nodes = bench.nodes_by_id
        by_id = bench.candidates_by_id
        priors = {cid: float((nodes[by_id[cid].node_id].metrics or {}).get("quality", 1.0))
                  for pool in eligible.values() for cid in pool
                  if cid in by_id and by_id[cid].node_id in nodes}
        overrides = measured_metrics(
            evidence, [c for pool in eligible.values() for c in pool],
            context, priors, explore=explore) or None
    spans = profile.ranges(search._reference_sample(bench, stages, eligible,
                                                    overrides))

    best = None
    for suggestion in suggestions:
        if not suggestion.accepted:
            continue
        # Policy still applies. A model cannot grant itself a permission.
        blocked = [f"{sid}={cid}" for sid, cid in suggestion.route.items()
                   if cid not in eligible.get(sid, ())]
        if blocked:
            suggestion.accepted = False
            suggestion.reason = f"blocked by policy: {', '.join(blocked)}"
            continue
        try:
            compile_route(bench, suggestion.route)
        except CompileError as problem:
            suggestion.accepted = False
            suggestion.reason = "; ".join(problem.problems[:2])
            continue
        suggestion.score = profile.score_within(
            aggregate(bench, suggestion.route, overrides), spans)
        if best is None or suggestion.score > best.score:
            best = suggestion

    accepted = sum(1 for s in suggestions if s.accepted)
    # Read before writing. `winner` and `baseline` are the same object, so
    # overwriting the score first made the audit note say "beat the search
    # (1.0304 vs 1.0304)" — a comparison against itself, which is worse than no
    # note at all because it reads like a real one.
    was = baseline.score
    if best is not None and best.score > was:
        winner = baseline
        winner.route = dict(best.route)
        winner.score = best.score
        winner.strategy = f"model+{baseline.strategy}"
        winner.notes += (f"the model proposed {len(suggestions)} route(s), "
                         f"{accepted} legal, and its best beat the search "
                         f"({best.score:.4f} vs {was:.4f}). "
                         f"Reason given: {best.why or '(none)'}",)
    else:
        winner = baseline
        winner.notes += (f"the model proposed {len(suggestions)} route(s), "
                         f"{accepted} legal, and none beat the search; "
                         f"its suggestions were scored and discarded",)
    winner.suggestions = tuple(suggestions)
    return winner


def ollama_proposer(model: str = "", host: str = "", timeout: float = 60.0
                    ) -> Proposer:
    """A `Proposer` backed by an Ollama-compatible endpoint.

    Kept at the edge on purpose. Everything above works with any callable, so
    this file is the only place that knows an HTTP API exists, and it is the
    only place that breaks when one changes.
    """
    import urllib.request

    from browsergraph import models as _models

    endpoint = (host or _models.DEFAULT_HOST).rstrip("/")
    chosen = model or ""

    def ask(prompt: str) -> str:
        payload = json.dumps({
            "model": chosen or "llama3.1",
            "prompt": prompt,
            "stream": False,
            # `num_predict` has to be generous even though the answer is short.
            # A reasoning model spends its budget thinking *before* it answers,
            # and at 400 it hit the limit mid-thought and returned an empty
            # `response` with `done_reason: length` — a silent nothing that
            # looks exactly like a model with no opinion.
            "options": {"temperature": 0.2, "num_predict": 3000},
        }).encode()
        request = urllib.request.Request(
            f"{endpoint}/api/generate", data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read())
        # Reasoning models split their output: the chain of thought goes to
        # `thinking` and the answer to `response`. Falling back to `thinking`
        # costs nothing when it is absent and rescues the case where the model
        # wrote its JSON there and then ran out of room to repeat it.
        return body.get("response") or body.get("thinking") or ""

    return ask
