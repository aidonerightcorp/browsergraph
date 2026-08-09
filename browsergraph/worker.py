"""Browser worker — runs one adapter inside an isolated environment.

Started as `python -m browsergraph.worker` by `isolate.Worker`. Reads one JSON
object per line on stdin, performs the `BrowserPort` operation, writes one JSON
object per line on stdout.

Errors are returned as data, never as a traceback on stdout: a crashed worker
that corrupts the stream is far harder to diagnose than one that replies
`{"ok": false, "error": ...}`.
"""
from __future__ import annotations

import json
import sys
import traceback
from typing import Any


def _build(spec_dict: dict):
    """Reconstruct a Spec and its adapter inside this environment."""
    from browsergraph.config import spec_from_dict
    from browsergraph.drivers import build

    spec = spec_from_dict(spec_dict)
    # `isolated` is a caller-side concern; building here must use the direct
    # adapter or the worker would try to spawn another worker.
    return build(spec, ) if True else None, spec


def main() -> int:
    browser = None
    out = sys.stdout.buffer

    for raw in sys.stdin.buffer:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw.decode())
        except (ValueError, UnicodeDecodeError) as e:
            out.write((json.dumps({"ok": False, "error": f"bad request: {e}"}) + "\n").encode())
            out.flush()
            continue

        op = msg.get("op", "")
        try:
            reply: dict[str, Any]
            if op == "open":
                browser, _spec = _build(msg.get("spec") or {})
                browser.start()
                reply = {"ok": True}
            elif op == "close":
                # Artifact paths only exist once the context has closed, so they
                # are returned from the close itself. Asking afterwards is too
                # late twice over: the browser is gone, and the `browser is None`
                # guard below would reject the question anyway.
                art = {"video_path": "", "trace_path": ""}
                if browser is not None:
                    browser.stop()
                    art = {"video_path": getattr(browser, "video_path", "") or "",
                           "trace_path": getattr(browser, "trace_path", "") or ""}
                    browser = None
                reply = {"ok": True, "result": art}
            elif browser is None:
                reply = {"ok": False, "error": "browser not open"}
            elif op == "goto":
                st = browser.goto(msg["url"])
                reply = {"ok": True, "result": {"url": st.url, "title": st.title,
                                                "status": st.status}}
            elif op == "state":
                st = browser.state()
                reply = {"ok": True, "result": {"url": st.url, "title": st.title,
                                                "status": st.status}}
            elif op == "find":
                el = browser.find(msg["selector"])
                reply = {"ok": True, "result": None if el is None
                         else {"selector": el.selector, "text": el.text}}
            elif op == "click":
                browser.click(msg["selector"])
                reply = {"ok": True}
            elif op == "type":
                browser.type(msg["selector"], msg["text"], cps=msg.get("cps", 0.0))
                reply = {"ok": True}
            elif op == "scroll":
                browser.scroll(int(msg.get("dy", 0)))
                reply = {"ok": True}
            elif op == "wait_for":
                reply = {"ok": True, "result": browser.wait_for(
                    msg["selector"], timeout=msg.get("timeout", 10.0))}
            elif op == "text_of":
                reply = {"ok": True, "result": browser.text_of(msg["selector"])}
            elif op == "html":
                reply = {"ok": True, "result": browser.html()}
            elif op == "screenshot":
                reply = {"ok": True, "result": browser.screenshot(msg["path"])}
            elif op == "eval_js":
                reply = {"ok": True, "result": browser.eval_js(msg["script"])}
            elif op == "artifacts":
                reply = {"ok": True, "result": {
                    "video_path": getattr(browser, "video_path", ""),
                    "trace_path": getattr(browser, "trace_path", "")}}
            elif op == "ping":
                reply = {"ok": True, "result": "pong"}
            else:
                reply = {"ok": False, "error": f"unknown op {op!r}"}
        except Exception as e:  # any adapter failure becomes data
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}",
                     "trace": traceback.format_exc()[-600:]}

        try:
            out.write((json.dumps(reply, default=str) + "\n").encode())
            out.flush()
        except (BrokenPipeError, OSError):
            break

    if browser is not None:
        try:
            browser.stop()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
