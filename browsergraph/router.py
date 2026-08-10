"""Which model for which job — routed by role, not by a single default.

`Catalog.best(capability)` answers "can this model see images". That is one bit
of a decision with several. Extracting fields from a page, deciding whether a
click worked, reading a screenshot, embedding a chunk for retrieval and
re-ranking twenty candidates are five different jobs, and the model that is
best at one is routinely a poor and expensive choice for another. Sending all
five to whatever is at the top of a preference list is how a pipeline ends up
paying frontier prices to classify a boolean.

So a **role** declares what the job actually needs: the capability it cannot do
without, whether determinism matters, how much output it needs, and how much it
is worth paying. The router picks per role, records *why*, and — the part that
matters — reports honestly when a role cannot be filled instead of quietly
substituting something that will produce confident nonsense.

Nothing here talks to a model. It decides which one to talk to.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from browsergraph.models import (
    COMPLETION,
    THINKING,
    TOOLS,
    VISION,
    Catalog,
    ModelInfo,
    ModelUnavailable,
)

#: Jobs this library actually has for a model.
EXTRACT = "extract"        # pull structured fields out of text
VERIFY = "verify"          # judge whether an outcome happened
LOCATE = "locate"          # find the element that matches an intent
READ_IMAGE = "read_image"  # transcribe or answer about a screenshot
CLASSIFY = "classify"      # a label from a small closed set
SUMMARIZE = "summarize"    # compress a page into a paragraph
EMBED = "embed"            # a vector for retrieval
RERANK = "rerank"          # order candidates by relevance
CODE = "code"              # write or repair a selector, a script, a schema
PLAN = "plan"              # decide the next action, with tools


@dataclass(frozen=True)
class Role:
    """What a job needs from a model, separately from which model that is."""
    name: str
    capability: str = COMPLETION
    description: str = ""
    #: Substrings that suggest a model is built for this job, **best first**.
    #: Ordered, not a set: treating them as a binary flag made every hinted
    #: model tie, so the alphabet decided — and `extract` picked a *coder*
    #: model over an instruct one purely because "d" sorts before "q".
    #: Hints break ties; they never override a missing capability.
    hints: tuple[str, ...] = ()
    #: Prefer the smallest model that qualifies. True for jobs where the answer
    #: is short and closed; a 400B model choosing between "yes" and "no" is
    #: money set on fire.
    prefer_small: bool = False
    #: A wrong answer here is expensive, so prefer the strongest model.
    prefer_strong: bool = False
    #: Roles where a different answer on a re-run is a defect rather than
    #: variation — a verifier that changes its mind makes its own evidence
    #: worthless.
    wants_determinism: bool = False
    fallback: tuple[str, ...] = ()


ROLES: dict[str, Role] = {
    EXTRACT: Role(EXTRACT, COMPLETION,
                  "Pull declared fields out of text, as JSON.",
                  hints=("instruct", "glm", "qwen", "deepseek"),
                  wants_determinism=True),  # instruct first: a coder model is
                                            # not the right tool for this
    VERIFY: Role(VERIFY, COMPLETION,
                 "Judge whether the outcome actually happened.",
                 hints=("instruct", "glm", "thinking"),
                 wants_determinism=True, prefer_strong=True),
    LOCATE: Role(LOCATE, COMPLETION,
                 "Choose the element matching an intent, from an "
                 "accessibility tree.",
                 hints=("instruct", "qwen", "glm"),
                 prefer_small=True, fallback=(CLASSIFY,)),
    READ_IMAGE: Role(READ_IMAGE, VISION,
                     "Transcribe a screenshot, or answer a question about it.",
                     hints=("vision", "vl", "llava", "glm", "pixtral")),
    CLASSIFY: Role(CLASSIFY, COMPLETION,
                   "One label from a small closed set.",
                   hints=("mini", "small", "flash", "3b", "1b", "7b"),
                   prefer_small=True, wants_determinism=True),
    SUMMARIZE: Role(SUMMARIZE, COMPLETION,
                    "Compress a page into a paragraph.",
                    hints=("instruct", "flash", "mini"), prefer_small=True),
    EMBED: Role(EMBED, "embedding",
                "A vector for retrieval.",
                hints=("embed", "bge", "nomic", "minilm", "gte"),
                prefer_small=True),
    RERANK: Role(RERANK, COMPLETION,
                 "Order candidates by relevance to a query.",
                 hints=("rerank", "bge", "cross"), prefer_small=True,
                 fallback=(CLASSIFY,)),
    CODE: Role(CODE, COMPLETION,
               "Write or repair a selector, a script or a schema.",
               hints=("code", "coder", "kimi", "deepseek"), prefer_strong=True),
    PLAN: Role(PLAN, TOOLS,
               "Decide the next action, calling tools.",
               hints=("thinking", "glm", "kimi", "deepseek"),
               prefer_strong=True, fallback=(EXTRACT,)),
}

#: Rough parameter counts, for "prefer the smallest that qualifies". Parsed
#: from the name because that is the only size signal a catalogue reliably
#: carries; unknown sizes sort in the middle rather than winning by default.
_SIZE_MARKERS = (("400b", 400), ("235b", 235), ("120b", 120), ("70b", 70),
                 ("32b", 32), ("30b", 30), ("27b", 27), ("14b", 14),
                 ("13b", 13), ("9b", 9), ("8b", 8), ("7b", 7), ("4b", 4),
                 ("3b", 3), ("1.5b", 1.5), ("1b", 1), ("0.5b", 0.5))
UNKNOWN_SIZE = 20.0

_MILLIONS = re.compile(r"(\d+(?:\.\d+)?)\s*m\b")
_BILLIONS = re.compile(r"(\d+(?:\.\d+)?)\s*b\b")


def size_of(model: ModelInfo) -> float:
    """Rough parameter count in billions.

    Millions are parsed too: an embedding model is typically 100-300M, and
    reporting `nomic-embed-text` as "~20B" while calling it the smallest that
    qualifies is a sentence that argues with itself.
    """
    declared = (model.parameter_size or "").lower().replace(" ", "")
    for text in (declared, model.name.lower()):
        found = _BILLIONS.search(text)
        if found:
            return float(found.group(1))
        found = _MILLIONS.search(text)
        if found:
            return float(found.group(1)) / 1000.0
    for marker, size in _SIZE_MARKERS:
        if marker in declared or marker in model.name.lower():
            return size
    return UNKNOWN_SIZE


@dataclass
class Choice:
    """One role, the model chosen for it, and the reasoning.

    `why` is not decoration. When a pipeline behaves differently after someone
    pulls a new model, the recorded reason is what distinguishes "the router
    changed its mind" from "the model changed its behaviour".
    """
    role: str
    model: str = ""
    capability: str = ""
    why: str = ""
    fallback_from: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.model) and not self.error

    def to_dict(self) -> dict:
        out = {"role": self.role, "model": self.model,
               "capability": self.capability, "why": self.why}
        if self.fallback_from:
            out["fallback_from"] = self.fallback_from
        if self.error:
            out["error"] = self.error
        return out


@dataclass
class Router:
    """Picks a model per role from one catalogue.

    Built once and reused: a catalogue lookup is a network call, and re-asking
    per node would make routing the slowest part of a graph.
    """
    catalog: Catalog
    overrides: Mapping[str, str] = field(default_factory=dict)
    _cache: dict[str, Choice] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, host: str = "", api_key: str = "",
             overrides: Mapping[str, str] | None = None) -> Router:
        return cls(catalog=Catalog.load(host, api_key),
                   overrides=dict(overrides or {}))

    # --- choosing -----------------------------------------------------------

    def choose(self, role: str) -> Choice:
        if role in self._cache:
            return self._cache[role]
        self._cache[role] = self._choose(role)
        return self._cache[role]

    def _choose(self, role_name: str) -> Choice:
        role = ROLES.get(role_name)
        if role is None:
            return Choice(role=role_name,
                          error=f"unknown role {role_name!r}; "
                                f"known: {', '.join(sorted(ROLES))}")
        if not self.catalog.reachable:
            return Choice(role=role_name, capability=role.capability,
                          error=f"no model host reachable: {self.catalog.error}")

        requested = self.overrides.get(role_name)
        if requested:
            try:
                model = self.catalog.choose(role.capability, requested=requested)
                return Choice(role=role_name, model=model.name,
                              capability=role.capability,
                              why="explicitly requested for this role")
            except ModelUnavailable as e:
                # An override that cannot do the job is a hard error. Silently
                # substituting would make the run untraceable, which is the
                # thing overrides exist to prevent.
                return Choice(role=role_name, capability=role.capability,
                              error=str(e))

        picked = self._rank(role)
        if picked:
            model, why = picked
            return Choice(role=role_name, model=model.name,
                          capability=role.capability, why=why)

        for alternative in role.fallback:
            spare = ROLES.get(alternative)
            if not spare:
                continue
            picked = self._rank(spare)
            if picked:
                model, why = picked
                return Choice(role=role_name, model=model.name,
                              capability=spare.capability,
                              fallback_from=alternative,
                              why=f"no model for {role_name}; using the "
                                  f"{alternative} model — {why}")

        have = sorted({c for m in self.catalog.models for c in m.capabilities})
        return Choice(role=role_name, capability=role.capability,
                      error=f"no model supports {role.capability!r}. "
                            f"Available capabilities: {have or 'none'}.")

    def _rank(self, role: Role) -> tuple[ModelInfo, str] | None:
        candidates = [m for m in self.catalog.models
                      if m.supports(role.capability)]
        if not candidates:
            return None

        def key(model: ModelInfo) -> tuple:
            lowered = model.name.lower()
            hinted = next((i for i, h in enumerate(role.hints) if h in lowered),
                          len(role.hints))
            size = size_of(model)
            # Smallest-that-qualifies for closed short answers; largest for the
            # jobs where being wrong is what costs money.
            by_size = size if role.prefer_small else (-size if role.prefer_strong else 0)
            # A local model is preferred over a cloud one at equal rank: same
            # answer, no egress, no per-token cost.
            return (hinted, by_size, 1 if model.cloud else 0, model.name)

        best = sorted(candidates, key=key)[0]
        reasons = []
        matched = next((h for h in role.hints if h in best.name.lower()), "")
        if matched:
            reasons.append(f"name matches the {matched!r} hint for this role")
        if role.prefer_small:
            reasons.append(f"smallest model that qualifies (~{size_of(best):g}B)")
        elif role.prefer_strong:
            reasons.append(f"strongest model that qualifies (~{size_of(best):g}B)")
        if not best.cloud:
            reasons.append("local")
        reasons.append(f"supports {role.capability}")
        return best, "; ".join(reasons)

    # --- reporting ----------------------------------------------------------

    def plan(self, roles: Iterable[str] = ()) -> list[Choice]:
        return [self.choose(role) for role in (roles or ROLES)]

    def unfilled(self, roles: Iterable[str] = ()) -> list[Choice]:
        """Roles this host cannot serve. The honest half of the report."""
        return [c for c in self.plan(roles) if not c.ok]

    def report(self, roles: Iterable[str] = ()) -> str:
        lines = [f"model router — {self.catalog.host}"]
        if not self.catalog.reachable:
            return lines[0] + f"\n  unreachable: {self.catalog.error}"
        for choice in self.plan(roles):
            if choice.ok:
                tail = (f"  (fallback from {choice.fallback_from})"
                        if choice.fallback_from else "")
                lines.append(f"  {choice.role:<11} {choice.model:<28} "
                             f"{choice.why}{tail}")
            else:
                lines.append(f"  {choice.role:<11} {'—':<28} {choice.error}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"host": self.catalog.host, "reachable": self.catalog.reachable,
                "choices": [c.to_dict() for c in self.plan()]}


def route(role: str, host: str = "", api_key: str = "",
          overrides: Mapping[str, str] | None = None) -> str:
    """The model name for one role, or a clear failure.

    Convenience for callers that want a single answer. Raises rather than
    returning a default: a text model handed a screenshot does not fail, it
    invents.
    """
    choice = Router.load(host, api_key, overrides).choose(role)
    if not choice.ok:
        raise ModelUnavailable(choice.error)
    return choice.model


def describe_roles() -> str:
    lines = ["role         capability   notes"]
    for role in ROLES.values():
        notes = []
        if role.prefer_small:
            notes.append("smallest that qualifies")
        if role.prefer_strong:
            notes.append("strongest that qualifies")
        if role.wants_determinism:
            notes.append("determinism matters")
        if role.fallback:
            notes.append("falls back to " + ", ".join(role.fallback))
        lines.append(f"{role.name:<13}{role.capability:<13}"
                     + "; ".join(notes or [role.description]))
    return "\n".join(lines)


def capabilities_needed(roles: Sequence[str] = ()) -> list[str]:
    """What a host must offer to serve these roles — for a pull list."""
    return sorted({ROLES[r].capability for r in (roles or ROLES) if r in ROLES})


__all__ = ["CLASSIFY", "CODE", "EMBED", "EXTRACT", "LOCATE", "PLAN", "READ_IMAGE",
           "RERANK", "ROLES", "SUMMARIZE", "THINKING", "VERIFY", "Choice", "Role",
           "Router", "capabilities_needed", "describe_roles", "route", "size_of"]
