# Engines

Thirteen engines drive the same graph. This page is about **which to reach for**, and —
just as usefully — what does not work and why.

Everything here was measured by launching it against a served page, not read off a
capability table. `browsergraph doctor` reports the same for your machine.

## Which one

| engine | reach for it when |
|---|---|
| `http` | the page is server-rendered. ~8x faster, no browser, and `curl-cffi` presents a real browser's TLS handshake — the layer anti-bot vendors check *before* any JavaScript runs |
| `playwright` | the default. Fast, three rendering engines, video and trace built in |
| `patchright` | Playwright with its automation footprint removed. Same API |
| `playwright_stealth` | Playwright plus evasion patches, when patchright is not an option |
| `rebrowser` | a patched Playwright build; another footprint trade-off |
| `selenium` | you need WebDriver — a Grid, an existing suite, or Firefox |
| `selenium_uc` | undetected-chromedriver, against a defended site with real Chrome |
| `seleniumbase` | its own UC mode and tooling |
| `botasaurus` | scraping-oriented framework with its own anti-detect defaults |
| `camoufox` | a hardened Firefox build. Strongest evasion here; needs isolation |
| `nodriver` / `zendriver` / `pydoll` | CDP-native: no WebDriver, no `navigator.webdriver`, no driver binary to version-match |
| `mock` | tests. No I/O at all |

## Verified matrix

126 combinations of engine × binary × display, each one launched.

| engine | chromium | chrome | firefox | webkit | brave | headless | headed | xvfb |
|---|---|---|---|---|---|---|---|---|
| playwright | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| playwright_stealth | ✅ | ✅ | — | — | ✅ | ✅ | ✅ | ✅ |
| patchright | ✅ | ✅ | — | — | ✅ | ✅ | ✅ | ✅ |
| rebrowser | ✅ | ✅ | — | — | ✅ | ✅ | ✅ | ✅ |
| selenium | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ | ✅ |
| selenium_uc | — | ✅ | — | — | ✅ | ✅ | ✅ | ✅ |
| seleniumbase | — | ✅ | — | — | ✅ | ✅ | ✅ | ✅ |
| botasaurus | — | ✅ | — | — | ✅ | ✅ | ✅ | ✅ |
| camoufox *(isolated)* | — | — | ✅ | — | — | ✅ | ✅ | ✅ |
| nodriver / zendriver / pydoll | — | ✅ | — | — | ✅ | ✅ | ✅ | ✅ |
| http | n/a | n/a | n/a | n/a | n/a | ✅ | — | ✅ |

## What does not work, and why

Every gap below is either intentional or an environment fact outside this library. None
of it is a bug waiting to be fixed silently.

**`engine=cdp`** is the bare DevTools protocol with no client library behind it. It
refuses and names what to use instead: `transport=remote_cdp` with a Playwright endpoint,
or one of the CDP-native engines.

**`selenium_uc` and `botasaurus` against snap Chromium.** Snap keeps the browser current
while the matching chromedriver lags, so a session cannot be created:

```
SessionNotCreatedException: This version of ChromeDriver only supports Chrome version 148
Current browser version is 150.0.7871.128
```

Both drive system Chrome perfectly well. Nothing in the library can reconcile a driver
that predates its browser.

**`nodriver` 0.48 – 0.50.3** ship a source file with non-UTF-8 bytes and raise
`SyntaxError` on import. The requirement pins `nodriver<0.48`, and if you hit it anyway
the error says so and points at `zendriver`, a maintained fork of the same design.

**WebKit** needs 79 system packages Chromium does not:

```bash
sudo playwright install-deps webkit
```

**Camoufox** pins its own Playwright build, so co-installing it breaks the shared
playwright and patchright adapters. It runs in a per-engine virtualenv instead — see
[ISOLATION.md](ISOLATION.md):

```bash
browsergraph envs create --name camoufox
```

```python
Spec(engine=Engine.CAMOUFOX, binary=Binary.FIREFOX, isolated=True)
```

## Finding a browser a driver will accept

`shutil.which("firefox")` is not an answer. On Ubuntu it returns `/usr/bin/firefox`, which
is a **shell script** wrapping the snap, and geckodriver rejects it:

```
InvalidArgumentException: binary is not a Firefox executable
```

That message names neither the cause nor the fix, and the fix —
`/snap/firefox/current/usr/lib/firefox/firefox` — is not guessable. On the machine this
was developed on, **three of four** installed browsers are wrapper scripts on `PATH`.
`browsergraph.binaries` resolves the real program by checking the file is not a `#!`
script, and says what it did:

```
[ok] binary:firefox   using /snap/firefox/current/usr/lib/firefox/firefox
                      (PATH had /usr/bin/firefox, a wrapper script a driver cannot use)
```

The same problem has a sharper edge for **drivers**. A snap-confined geckodriver cannot be
signalled *even by the user who owns it* — `os.kill(pid, 0)` succeeds, `os.kill(pid,
SIGTERM)` raises `PermissionError` — so selenium's teardown fails silently and every
Firefox session leaks a process. Forty-six accumulated in one test run. A killable
geckodriver is fetched and cached instead, and `stop()` confirms the process is gone
rather than trusting `quit()`.

## Containers

Chrome cannot use its sandbox as root, which is every Docker, Kubernetes, CI and Kaggle
environment. `--no-sandbox` is not a protection being given up there — it is one that was
never available, and without it Chrome exits immediately. Those flags are added
automatically when a container is detected, and `Spec.extra` takes your own:

```python
Spec(extra={"launch_args": ["--window-size=1920,1080"], "container_args": True})
```

## Adding one

An adapter is a class satisfying the 12-method `BrowserPort`, plus three table entries —
family, pip requirement, importable module. See [CONTRIBUTING.md](CONTRIBUTING.md);
`test_every_declared_engine_can_be_routed` fails if an engine is declared without one.
