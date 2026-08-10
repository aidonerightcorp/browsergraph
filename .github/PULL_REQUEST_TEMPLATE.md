## What this changes

## Why

If this fixes something, what did the failure look like? Failure messages and the
reasoning behind a fix are the part of this codebase most worth keeping.

## Checks

- [ ] `pytest -q`
- [ ] `mypy browsergraph --ignore-missing-imports`
- [ ] `ruff check browsergraph tests`
- [ ] A test that fails without this change
