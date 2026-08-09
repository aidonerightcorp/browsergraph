"""Parameterised graphs — one template, many tasks.

A graph that hardcodes a URL and a search term is one task. The same graph with
declared parameters is a *capability* you can point at a list of inputs.

Parameters are declared with types and requirements so a bad batch fails at
load time rather than on item 400 of 500. Secrets resolve from the environment
and are redacted in logs — a template is checked into a repo, its values are not.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

_REF = re.compile(r"\$\{([a-zA-Z_][\w.]*)\}")
_SECRETISH = re.compile(r"pass|pwd|secret|token|api[_-]?key|otp", re.I)


class ParamError(ValueError):
    pass


@dataclass
class Param:
    name: str
    required: bool = True
    default: Any = None
    type: str = "str"           # str | int | float | bool | url
    secret: bool = False
    env: str = ""               # resolve from this env var when not supplied
    choices: tuple = ()
    description: str = ""

    def coerce(self, value: Any) -> Any:
        if value is None:
            return None
        try:
            if self.type == "int":
                return int(value)
            if self.type == "float":
                return float(value)
            if self.type == "bool":
                if isinstance(value, bool):
                    return value
                return str(value).strip().lower() in {"1", "true", "yes", "on"}
            if self.type == "url":
                s = str(value)
                if not s.startswith(("http://", "https://")):
                    raise ParamError(f"{self.name}: {s!r} is not an http(s) url")
                return s
            return str(value)
        except ParamError:
            raise
        except (TypeError, ValueError) as e:
            raise ParamError(f"{self.name}: cannot read {value!r} as {self.type}") from e


@dataclass
class ParamSet:
    params: list[Param] = field(default_factory=list)

    @staticmethod
    def from_list(items: list[dict]) -> ParamSet:
        out = []
        for item in items or []:
            item = dict(item)
            name = item.pop("name")
            item.setdefault("secret", bool(_SECRETISH.search(name)))
            if "choices" in item:
                item["choices"] = tuple(item["choices"])
            out.append(Param(name=name, **item))
        return ParamSet(out)

    def resolve(self, supplied: dict[str, Any] | None = None) -> dict[str, Any]:
        """Merge supplied values with env and defaults; validate the result."""
        supplied = dict(supplied or {})
        values: dict[str, Any] = {}
        missing: list[str] = []

        for p in self.params:
            if p.name in supplied and supplied[p.name] is not None:
                raw = supplied[p.name]
            elif p.env and os.environ.get(p.env):
                raw = os.environ[p.env]
            elif p.secret and os.environ.get(p.name.upper()):
                raw = os.environ[p.name.upper()]
            elif p.default is not None:
                raw = p.default
            elif p.required:
                missing.append(p.name)
                continue
            else:
                continue

            value = p.coerce(raw)
            if p.choices and value not in p.choices:
                raise ParamError(
                    f"{p.name}: {value!r} not in {list(p.choices)}")
            values[p.name] = value

        if missing:
            hint = ", ".join(missing)
            raise ParamError(f"missing required parameter(s): {hint}")

        unknown = set(supplied) - {p.name for p in self.params}
        if unknown:
            raise ParamError(f"unknown parameter(s): {', '.join(sorted(unknown))}")
        return values

    @property
    def secret_names(self) -> set[str]:
        return {p.name for p in self.params if p.secret}

    def redact(self, text: str, values: dict[str, Any]) -> str:
        """Replace secret values wherever they appear in a string."""
        out = text
        for name in self.secret_names:
            val = values.get(name)
            if val:
                out = out.replace(str(val), f"<{name}>")
        return out


def substitute(obj: Any, values: dict[str, Any]) -> Any:
    """Recursively replace ${name} references in strings.

    A whole-string reference (`"${limit}"`) keeps the value's type, so an int
    parameter does not silently become a string.
    """
    if isinstance(obj, str):
        whole = _REF.fullmatch(obj.strip())
        if whole:
            key = whole.group(1)
            if key not in values:
                raise ParamError(f"unresolved reference ${{{key}}}")
            return values[key]

        def repl(m: re.Match) -> str:
            key = m.group(1)
            if key not in values:
                raise ParamError(f"unresolved reference ${{{key}}}")
            return str(values[key])

        return _REF.sub(repl, obj)
    if isinstance(obj, list):
        return [substitute(v, values) for v in obj]
    if isinstance(obj, dict):
        return {k: substitute(v, values) for k, v in obj.items()}
    return obj


def references(obj: Any) -> set[str]:
    """Every ${name} used, for checking a template declares what it uses."""
    found: set[str] = set()
    if isinstance(obj, str):
        found |= set(_REF.findall(obj))
    elif isinstance(obj, list):
        for v in obj:
            found |= references(v)
    elif isinstance(obj, dict):
        for v in obj.values():
            found |= references(v)
    return found


def check_template(raw: dict) -> list[str]:
    """Problems in a parameterised config, found before any run."""
    problems: list[str] = []
    declared = {p["name"] for p in raw.get("params", [])}
    used = references(raw.get("nodes", [])) | references(raw.get("spec", {}))
    for name in sorted(used - declared):
        problems.append(f"${{{name}}} is used but not declared in params")
    for name in sorted(declared - used):
        problems.append(f"param {name!r} is declared but never used")
    return problems
