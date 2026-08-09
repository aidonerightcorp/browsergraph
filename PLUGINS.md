# Open Plugin Format v1.0

A plugin adds nodes, tasks, drivers, classifiers, extractors or dimensions to
browsergraph — or to any harness that reads this format. The manifest is plain
JSON with a versioned schema, so a consumer in another language can enumerate
what a plugin provides without running Python.

## Principles

1. **Declare before you load.** `discover()` reads manifests only. Nothing is
   imported until `load()` is called, because importing is what runs code.
2. **Capabilities are declared, and checked.** A plugin that registers something
   it did not declare is reported as `undeclared` rather than accepted silently.
3. **Built-ins are not replaceable by accident.** Registering an existing kind
   raises; a plugin cannot quietly swap out `click`.
4. **Permissions are stated.** `network`, `filesystem`, `llm`, `subprocess` —
   declared in the manifest so an operator can read intent before installing.

## `plugin.json`

```json
{
  "schema_version": "1.0",
  "name": "acme-linkedin",
  "version": "0.2.0",
  "description": "LinkedIn nodes and a profile-scrape task.",
  "author": "Acme",
  "license": "MIT",
  "homepage": "https://github.com/acme/acme-linkedin",
  "module": "acme_linkedin",
  "requires": ["httpx>=0.27"],
  "requires_browsergraph": ">=0.1",
  "provides": {
    "nodes": ["linkedin_login", "linkedin_profile"],
    "tasks": ["linkedin_company"],
    "drivers": [],
    "classifiers": [],
    "extractors": [],
    "dimensions": []
  },
  "permissions": ["network", "llm"]
}
```

| Field | Required | Meaning |
|---|---|---|
| `schema_version` | — | Major version must match the host's (`1.x`) |
| `name` | **yes** | Unique plugin id |
| `module` | **yes** | Import path; may define `plugin_init()` |
| `provides` | — | Capability → names. Checked against what registers |
| `requires` | — | pip requirements, surfaced when import fails |
| `permissions` | — | Declared intent: `network`, `filesystem`, `llm`, `subprocess` |

## The module

```python
# acme_linkedin/__init__.py
from browsergraph.nodes.base import Node, register
from browsergraph.ports import Context


def plugin_init() -> None:
    """Called once by load(). Registration may also happen at import."""

    @register
    class LinkedInLogin(Node):
        kind = "linkedin_login"
        mutates = True          # so the linter requires verification after it
        interacts = True

        def __init__(self, user: str, name: str = ""):
            super().__init__(name)
            self.user = user

        def run(self, ctx: Context) -> Context:
            ctx.browser.click("#login")     # BrowserPort only, never an engine
            return ctx
```

**The hook is `plugin_init`, not `register`.** Plugins import `register` as the
node decorator, so a hook of the same name would be invoked with no class and
fail on every well-formed plugin.

Declaring `mutates` / `verifies` / `interacts` is not decoration — `lint.py`
reasons about those flags, so an undeclared node silently opts out of the
safety checks.

## Discovery

**Entry points**, for installed packages:

```toml
[project.entry-points."browsergraph.plugins"]
acme-linkedin = "acme_linkedin"
```

**Directories**, for local plugins:

```bash
export BROWSERGRAPH_PLUGIN_PATH=/opt/bg-plugins:/home/me/plugins
browsergraph plugins --load
```

A directory may be a plugin itself (`./plugin.json`) or contain several
(`./*/plugin.json`).

## Loading

```python
from browsergraph.plugins import discover, load

for manifest in discover("/opt/bg-plugins"):
    report = load(manifest)
    print(report.to_dict())
```

`LoadReport` states what happened:

```json
{
  "plugin": "acme-linkedin", "version": "0.2.0", "loaded": true,
  "registered": {"nodes": ["linkedin_login"], "tasks": ["linkedin_company"]},
  "overrides": [], "undeclared": [], "error": ""
}
```

Failures are explained rather than raised — a missing dependency reports the
pip requirement from the manifest.

## Capabilities

| Capability | Extend by |
|---|---|
| `nodes` | subclass `Node`, decorate with `@register` |
| `tasks` | subclass `Task`, decorate with `@register` from `tasks.base` |
| `drivers` | implement the `BrowserPort` protocol (12 methods, no inheritance) |
| `classifiers` | any callable returning a `Classification`-shaped result |
| `extractors` | pure functions over page text |
| `dimensions` | new axis values plus capability-table entries |

`BrowserPort` is a Protocol precisely so a third-party driver need not depend
on this package to satisfy it.

## For other harnesses

The manifest is the contract; nothing in it is Python-specific except `module`.
A harness in another language can read `provides` to build a capability index,
`permissions` to gate installation, and `requires` to resolve dependencies —
then hand `module` to whatever runtime it uses.

Version negotiation: compare the major of `schema_version`. Minor versions add
optional fields only.
