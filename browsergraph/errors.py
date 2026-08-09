"""Failure classification, and the response each class warrants.

Retrying is not a universal remedy. A missing selector deserves healing; a
timeout deserves patience; a CAPTCHA deserves **stopping**. Treating them alike
is how automation retries itself into a ban.

Classification is deterministic and inspectable — no model in the loop — so the
same failure always produces the same decision.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Failure(str, Enum):
    NONE = "none"
    SELECTOR_MISS = "selector_miss"    # element not found
    TIMEOUT = "timeout"                # present eventually, or never
    NAVIGATION = "navigation"          # page did not load
    CHALLENGE = "challenge"            # CAPTCHA / bot wall / "unusual traffic"
    AUTH = "auth"                      # login required or rejected
    RATE_LIMIT = "rate_limit"          # 429 / "too many requests"
    BLOCKED = "blocked"                # 403 / IP or account ban
    NETWORK = "network"                # DNS, connection, proxy
    LLM = "llm"                        # model unreachable or unusable
    VERIFY = "verify"                  # acted, but the outcome was wrong
    CRASH = "crash"                    # driver died
    UNKNOWN = "unknown"


class Response(str, Enum):
    """What to do about it."""
    HEAL = "heal"              # try alternative selectors
    RETRY = "retry"            # same spec, again
    WAIT_RETRY = "wait_retry"  # back off, then retry
    ESCALATE = "escalate"      # different spec (stealth, engine, proxy)
    ABORT = "abort"            # stop — retrying makes it worse
    FAIL = "fail"              # give up on this item, continue the batch


_PATTERNS: tuple[tuple[Failure, re.Pattern], ...] = (
    (Failure.CHALLENGE, re.compile(
        r"captcha|recaptcha|hcaptcha|cloudflare|unusual traffic|are you (a )?human|"
        r"verify you|challenge|bot detect|access denied.*security", re.I)),
    (Failure.RATE_LIMIT, re.compile(r"\b429\b|rate.?limit|too many requests|slow down", re.I)),
    (Failure.BLOCKED, re.compile(r"\b403\b|forbidden|banned|suspended|blocked", re.I)),
    (Failure.AUTH, re.compile(r"\b401\b|unauthor|login required|sign in to continue|"
                              r"invalid (password|credentials)", re.I)),
    (Failure.LLM, re.compile(r"llm unreachable|llm returned no|model not found", re.I)),
    (Failure.NETWORK, re.compile(r"dns|econnrefused|connection (refused|reset)|"
                                 r"proxy|tunnel|ssl|certificate|unreachable", re.I)),
    (Failure.TIMEOUT, re.compile(r"timeout|timed out|waiting for", re.I)),
    (Failure.SELECTOR_MISS, re.compile(r"not found|could not resolve|no such element|"
                                       r"no element|locator", re.I)),
    (Failure.VERIFY, re.compile(r"verification failed|expected .* but", re.I)),
    (Failure.NAVIGATION, re.compile(r"navigation|net::err|page (crash|closed)", re.I)),
    (Failure.CRASH, re.compile(r"browser (closed|crashed)|target closed|session deleted", re.I)),
)

#: What each failure warrants. CHALLENGE and BLOCKED deliberately do not retry.
POLICY: dict[Failure, Response] = {
    Failure.NONE: Response.RETRY,
    Failure.SELECTOR_MISS: Response.HEAL,
    Failure.TIMEOUT: Response.WAIT_RETRY,
    Failure.NAVIGATION: Response.RETRY,
    Failure.CHALLENGE: Response.ABORT,
    Failure.AUTH: Response.FAIL,
    Failure.RATE_LIMIT: Response.WAIT_RETRY,
    Failure.BLOCKED: Response.ABORT,
    Failure.NETWORK: Response.ESCALATE,
    Failure.LLM: Response.FAIL,
    Failure.VERIFY: Response.ESCALATE,
    Failure.CRASH: Response.RETRY,
    Failure.UNKNOWN: Response.ESCALATE,
}

#: Backoff seconds per attempt for WAIT_RETRY.
BACKOFF = (5, 20, 60, 180)


@dataclass
class Diagnosis:
    failure: Failure
    response: Response
    reason: str
    attempt: int = 0

    @property
    def terminal(self) -> bool:
        return self.response in (Response.ABORT, Response.FAIL)

    def backoff(self) -> float:
        if self.response is not Response.WAIT_RETRY:
            return 0.0
        return float(BACKOFF[min(self.attempt, len(BACKOFF) - 1)])

    def __str__(self) -> str:
        return f"{self.failure.value} -> {self.response.value} ({self.reason[:80]})"


def classify(error: str, page_text: str = "", attempt: int = 0) -> Diagnosis:
    """Classify a failure from the error message and, when available, the page.

    The page is checked first: a CAPTCHA usually surfaces as a plain selector
    miss, and mistaking a bot wall for a missing element is how a run keeps
    hammering a site that has already flagged it.
    """
    if not error:
        return Diagnosis(Failure.NONE, POLICY[Failure.NONE], "no error", attempt)

    for failure, pattern in _PATTERNS:
        if failure is Failure.CHALLENGE and page_text and pattern.search(page_text):
            return Diagnosis(failure, POLICY[failure],
                             "challenge detected in page content", attempt)

    haystack = f"{error}\n{page_text[:2000]}"
    for failure, pattern in _PATTERNS:
        m = pattern.search(haystack)
        if m:
            return Diagnosis(failure, POLICY[failure], f"matched {m.group(0)!r}", attempt)

    return Diagnosis(Failure.UNKNOWN, POLICY[Failure.UNKNOWN], error[:120], attempt)
