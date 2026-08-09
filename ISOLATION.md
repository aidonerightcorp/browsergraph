# Engine Isolation

Some browser engines cannot share a virtualenv. Camoufox pins its own
Playwright build; installing it downgrades the shared one and breaks the
`playwright` and `patchright` adapters. Undetected-chromedriver needs a
`distutils` shim and a Chrome major that matches the installed browser.

The wrong answer is to drop the engine. The right one is to stop assuming a
single environment must hold them all.

## How it works

```
caller ──BrowserPort──> IsolatedBrowser ──JSON lines over stdio──> worker
                                                                     │
                                              ~/.cache/browsergraph/envs/<family>/
                                              its own venv, its own pins
```

Each engine family gets a venv. `browsergraph.worker` runs inside it, holds the
real adapter, and answers `BrowserPort` operations one JSON object per line.
`IsolatedBrowser` satisfies the same protocol, so nodes, tasks, healing,
supervision and the linter cannot tell the difference — **isolation is a
deployment decision, not an API change.**

## Using it

```bash
browsergraph envs list                    # what exists, what would be installed
browsergraph envs create --name camoufox  # build one (installs + fetches browser)
browsergraph envs remove --name camoufox
```

```python
spec = Spec(engine=Engine.CAMOUFOX, binary=Binary.FIREFOX, isolated=True)
run(graph, spec, build(spec))             # identical graph, isolated engine
```

Config:

```yaml
spec:
  engine: camoufox
  binary: firefox
  isolated: true
```

## When to use it

**Isolate** when an engine conflicts with another (camoufox), when you need two
versions of the same engine at once, or when a crash must not take the parent
process down.

**Do not isolate** by default. It costs a process hop per call (~0.2-1 ms
locally) and a venv on disk. `isolated` is opt-in for exactly that reason, and
`mock` ignores it entirely.

## What it fixed here

Installing camoufox into the shared env broke three working engines: Playwright
started looking for `chromium_headless_shell-1223` instead of the installed
`1234`, and both playwright and patchright began failing with *"Sync API inside
the asyncio loop"*.

With isolation, `test_conflicting_engines_coexist` asserts what was previously
impossible — camoufox and the in-process Playwright adapter both working in one
process:

```python
camo = build(Spec(engine=Engine.CAMOUFOX, isolated=True))   # its own venv
...
direct = build(Spec(engine=Engine.PLAYWRIGHT))              # shared venv, untouched
```

## Known environment fixes folded in

| Symptom | Cause | Fix |
|---|---|---|
| `No module named 'distutils'` | undetected-chromedriver on Python ≥3.12 | `setuptools<81` in the selenium env |
| `cannot connect to chrome` | uc adds its own headless flag; `--headless=new` conflicts | omit the flag for uc, pin `version_main` to installed Chrome |
| `eval_js` returns `None` on Selenium | `execute_script` needs an explicit `return` | expressions auto-wrapped in the adapter |
| `Sync API inside the asyncio loop` | a sync Playwright instance held open across test modules | browser fixtures are module-scoped |

## Protocol

Line-delimited JSON, one request/response per line. Operations mirror
`BrowserPort`: `open`, `goto`, `state`, `find`, `click`, `type`, `scroll`,
`wait_for`, `text_of`, `html`, `screenshot`, `eval_js`, `artifacts`, `close`.

Adapter failures come back as `{"ok": false, "error": "..."}` rather than a
traceback on stdout — a worker that corrupts the stream is far harder to
diagnose than one that reports the failure as data.

The protocol is deliberately language-neutral: a worker in another runtime
could serve the same contract.
