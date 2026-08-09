"""Structured JSON logging.

One event per line, stable field names, secrets redacted. Designed for a
container: stdout only, no file handling, no rotation — the platform does that.

Redaction is applied at the formatter, not at call sites. Relying on every
caller to remember is how credentials end up in a log aggregator, and a HAR or
a `Type` node's text will otherwise carry them verbatim.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

#: Correlation id, propagated across every event in one run.
run_id: ContextVar[str] = ContextVar("run_id", default="")

_SECRET_KEYS = re.compile(
    r"pass|pwd|secret|token|api[_-]?key|authorization|cookie|otp|credential", re.I)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.I)
_EMAILISH = re.compile(r"[\w.+-]{1,64}@[\w.-]{1,255}\.[A-Za-z]{2,24}")

REDACTED = "<redacted>"


def redact(value: Any, redact_emails: bool = False) -> Any:
    """Recursively mask secret-looking keys and bearer tokens."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[k] = REDACTED if _SECRET_KEYS.search(str(k)) else redact(v, redact_emails)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v, redact_emails) for v in value]
    if isinstance(value, str):
        s = _BEARER.sub(r"\1" + REDACTED, value)
        if redact_emails:
            s = _EMAILISH.sub(REDACTED, s)
        return s
    return value


class JsonFormatter(logging.Formatter):
    """Emits one JSON object per record."""

    def __init__(self, service: str = "browsergraph", redact_emails: bool = False):
        super().__init__()
        self.service = service
        self.redact_emails = redact_emails

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "service": self.service,
            "msg": record.getMessage(),
        }
        rid = run_id.get()
        if rid:
            payload["run_id"] = rid
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info).splitlines()[-1]

        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(redact(extra, self.redact_emails))

        try:
            return json.dumps(payload, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            return json.dumps({**{k: str(v) for k, v in payload.items()}})


class Log:
    """Thin wrapper so call sites pass fields rather than formatting strings."""

    def __init__(self, name: str = "browsergraph") -> None:
        self._log = logging.getLogger(name)

    def _emit(self, level: int, msg: str, **fields) -> None:
        self._log.log(level, msg, extra={"fields": fields})

    def debug(self, msg: str, **f) -> None: self._emit(logging.DEBUG, msg, **f)
    def info(self, msg: str, **f) -> None: self._emit(logging.INFO, msg, **f)
    def warn(self, msg: str, **f) -> None: self._emit(logging.WARNING, msg, **f)
    def error(self, msg: str, **f) -> None: self._emit(logging.ERROR, msg, **f)


def configure(level: str = "", service: str = "browsergraph",
              stream=None, redact_emails: bool | None = None) -> logging.Logger:
    """Install the JSON handler on the root logger. Idempotent.

    Env: `LOG_LEVEL`, `LOG_SERVICE`, `LOG_REDACT_EMAILS`.
    """
    level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    service = service or os.environ.get("LOG_SERVICE", "browsergraph")
    if redact_emails is None:
        redact_emails = os.environ.get("LOG_REDACT_EMAILS", "").lower() in {"1", "true", "yes"}

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter(service=service, redact_emails=redact_emails))
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))
    return root


def new_run(prefix: str = "run") -> str:
    """Start a correlation scope; every subsequent event carries this id."""
    rid = f"{prefix}-{uuid.uuid4().hex[:12]}"
    run_id.set(rid)
    return rid


log = Log()
