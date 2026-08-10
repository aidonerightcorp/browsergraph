"""Structured diagnostics for the domain-neutral graph compiler."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Diagnostic:
    """One stable, machine-readable compiler or registry diagnostic."""

    code: str
    message: str
    path: str = ""
    hint: str = ""
    severity: str = "error"

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
            "hint": self.hint,
        }


class ValidationError(ValueError):
    """Raised only after all useful diagnostics have been collected."""

    def __init__(self, summary: str, diagnostics: tuple[Diagnostic, ...] | list[Diagnostic]):
        self.summary = summary
        self.diagnostics = tuple(diagnostics)
        detail = "\n".join(
            f"  - {item.code} {item.path}: {item.message}".rstrip()
            for item in self.diagnostics
        )
        super().__init__(f"{summary}\n{detail}" if detail else summary)

