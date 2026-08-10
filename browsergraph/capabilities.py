"""What an engine can actually do, asked before anything is launched.

`BrowserPort` is deliberately small — twelve methods every engine must provide.
But a real automation wants to press Escape, pick from a `<select>`, attach a
file, catch a download, step into an iframe. Those are not universal: `engine=http`
has no keyboard because it has no browser, and Selenium cannot intercept a
download the way Playwright can.

There are two dishonest ways to handle that and this module avoids both.

**Widening the port** forces every engine to implement everything, so adapters
grow methods that raise, and "this engine supports downloads" becomes a claim
nobody checked. **Silently no-op'ing** is worse: the graph reports success and
the file was never downloaded, which is precisely the failure this library was
written after.

So capabilities are *declared*, checked at composition time, and — because a
declaration can drift from the code — cross-checked against what the adapters
actually implement. A test fails if a driver claims something it does not have.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from browsergraph.dimensions import Engine

#: Optional abilities beyond the twelve-method core. A capability is a promise
#: about behaviour, not merely about a method existing.
PRESS = "press"                # send keystrokes
SELECT = "select"              # choose an <option>
UPLOAD = "upload"              # attach a file to an <input type=file>
DOWNLOAD = "download"          # capture a file the page hands back
FRAMES = "frames"              # step into and out of an iframe
COOKIES = "cookies"            # read and write cookies, including httpOnly
VIEWPORT = "viewport"          # resize the rendering surface
PDF = "pdf"                    # print the page to PDF
SCRIPT = "script"              # run JavaScript in the page

ALL: tuple[str, ...] = (PRESS, SELECT, UPLOAD, DOWNLOAD, FRAMES, COOKIES,
                        VIEWPORT, PDF, SCRIPT)

#: The method each capability is implemented by, used to cross-check a
#: declaration against the adapter. A driver that declares PDF and has no `pdf`
#: method is lying, and a test says so.
METHOD = {PRESS: "press", SELECT: "select_option", UPLOAD: "upload",
          DOWNLOAD: "download", FRAMES: "use_frame", COOKIES: "cookies",
          VIEWPORT: "set_viewport", PDF: "pdf", SCRIPT: "eval_js"}

#: What each engine promises. Declared rather than probed, because a graph must
#: be checkable *before* a browser exists — the whole point of a pre-flight is
#: that it costs nothing.
#:
#: Chromium-family DevTools engines (nodriver, zendriver, pydoll, cdp) get the
#: script surface only: they drive a real browser, but this project's adapters
#: for them expose evaluation and not much else, and claiming more would be a
#: promise the adapter cannot keep.
ENGINE_CAPABILITIES: dict[Engine, frozenset[str]] = {
    Engine.PLAYWRIGHT: frozenset(ALL),
    Engine.PLAYWRIGHT_STEALTH: frozenset(ALL),
    Engine.PATCHRIGHT: frozenset(ALL),
    Engine.REBROWSER: frozenset(ALL),
    Engine.CAMOUFOX: frozenset(ALL) - {PDF},        # PDF is Chromium-only
    Engine.SELENIUM: frozenset({PRESS, SELECT, UPLOAD, FRAMES, COOKIES,
                                VIEWPORT, SCRIPT}),
    Engine.SELENIUM_UC: frozenset({PRESS, SELECT, UPLOAD, FRAMES, COOKIES,
                                   VIEWPORT, SCRIPT}),
    Engine.SELENIUMBASE: frozenset({PRESS, SELECT, UPLOAD, FRAMES, COOKIES,
                                    VIEWPORT, SCRIPT}),
    Engine.BOTASAURUS: frozenset({PRESS, SELECT, UPLOAD, FRAMES, COOKIES,
                                  VIEWPORT, SCRIPT}),
    Engine.NODRIVER: frozenset({SCRIPT}),
    Engine.ZENDRIVER: frozenset({SCRIPT}),
    Engine.PYDOLL: frozenset({SCRIPT}),
    Engine.CDP: frozenset({SCRIPT}),
    Engine.HTTP: frozenset(),          # no browser, so no keyboard and no frames
    Engine.MOCK: frozenset(ALL),       # so a graph can be exercised without one
}


class Unsupported(RuntimeError):
    """A node asked an engine for something it never claimed to do.

    Raised rather than ignored. A silently skipped upload produces a run that
    reports success and uploaded nothing, which is the exact shape of failure
    this project exists to make impossible.
    """


@dataclass(frozen=True)
class Gap:
    """One node that cannot run on one engine, and why."""
    node: str
    capability: str
    engine: str

    def __str__(self) -> str:
        return (f"{self.node} needs '{self.capability}', which engine "
                f"{self.engine} does not provide")


def of(engine: Engine) -> frozenset[str]:
    return ENGINE_CAPABILITIES.get(engine, frozenset())


def implemented(browser: object) -> frozenset[str]:
    """What this *object* actually implements, by inspection.

    The counterweight to the declared table. Used by the conformance test so a
    driver cannot claim a capability it has not written.
    """
    return frozenset(name for name, method in METHOD.items()
                     if callable(getattr(browser, method, None)))


def required(node: object) -> tuple[str, ...]:
    return tuple(getattr(node, "requires_capabilities", ()) or ())


def missing(engine: Engine, nodes: Iterable[object]) -> list[Gap]:
    """Every node in a graph that this engine cannot run.

    All of them, not the first: a person about to switch engines wants the
    whole bill, not to discover it one node at a time.
    """
    have = of(engine)
    out = []
    for node in nodes:
        for capability in required(node):
            if capability not in have:
                out.append(Gap(node=getattr(node, "name", repr(node)),
                               capability=capability, engine=engine.value))
    return out


def engines_for(nodes: Iterable[object]) -> list[Engine]:
    """Every engine that could run this graph. The useful half of a refusal."""
    wanted = {c for node in nodes for c in required(node)}
    return [e for e in Engine if wanted <= of(e)]


def require(browser: object, capability: str, node: str = "") -> None:
    """Assert at run time, with a message that names the way out.

    Belt and braces: composition-time checking catches this earlier and more
    cheaply, but a browser can be swapped for one the linter never saw.
    """
    if capability in implemented(browser):
        return
    engine = getattr(getattr(browser, "spec", None), "engine", None)
    name = getattr(engine, "value", type(browser).__name__)
    alternatives = [e.value for e in Engine if capability in of(e)][:5]
    raise Unsupported(
        f"{node or 'this node'} needs '{capability}' and engine {name} does not "
        f"provide it. Engines that do: {', '.join(alternatives)}.")


def report(nodes: Sequence[object] = ()) -> str:
    """The capability matrix, and which engines could run a given graph."""
    width = max((len(e.value) for e in Engine), default=10)
    lines = ["  " + " " * width + "  " + "  ".join(f"{c[:4]:<4}" for c in ALL)]
    for engine in Engine:
        have = of(engine)
        marks = "  ".join(f"{'yes ' if c in have else '  · '}" for c in ALL)
        lines.append(f"  {engine.value:<{width}}  {marks}")
    if nodes:
        usable = engines_for(nodes)
        lines.append("")
        lines.append(f"  this graph runs on: {', '.join(e.value for e in usable)}"
                     if usable else "  no engine can run this graph")
    return "\n".join(lines)
