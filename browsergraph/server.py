"""HTTP API — stdlib only, so the service adds no dependencies.

    GET  /health              liveness
    GET  /doctor              prerequisite report as JSON
    GET  /engines             usable engines
    GET  /dimensions          axes and values
    POST /run                 {"spec": {...}, "nodes": [...]} -> run result
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from browsergraph.config import graph_from_list, spec_from_dict
from browsergraph.dimensions import (
    Binary,
    Display,
    Engine,
    LLMControl,
    Stealth,
    Transport,
    validate,
)


class Handler(BaseHTTPRequestHandler):
    server_version = "browsergraph"

    def _send(self, code: int, payload) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quieter default logging
        pass

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/health"):
            return self._send(200, {"ok": True})
        if self.path.startswith("/doctor"):
            from browsergraph.doctor import run_all
            rep = run_all()
            return self._send(200, {"ok": rep.ok, "checks": [c.__dict__ for c in rep.checks]})
        if self.path.startswith("/engines"):
            from browsergraph.doctor import available_engines
            return self._send(200, {"usable": [e.value for e in available_engines()],
                                    "all": [e.value for e in Engine]})
        if self.path.startswith("/dimensions"):
            return self._send(200, {
                enum.__name__.lower(): [v.value for v in enum]
                for enum in (Engine, Binary, Transport, Display, Stealth, LLMControl)
            })
        return self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self.path.startswith("/run"):
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"error": f"bad json: {e}"})

        try:
            spec = spec_from_dict(payload.get("spec", {}))
            problems = validate(spec)
            if problems:
                return self._send(400, {"error": "spec not runnable", "problems": problems})
            graph = graph_from_list(payload.get("nodes", []), behavior=spec.behavior)
        except (KeyError, TypeError, ValueError) as e:
            return self._send(400, {"error": f"bad request: {e}"})

        from browsergraph.drivers import DriverUnavailable, build
        from browsergraph.graph import run as run_graph
        try:
            result = run_graph(graph, spec, build(spec))
        except DriverUnavailable as e:
            return self._send(503, {"error": str(e)})

        return self._send(200 if result.ok else 422, {
            "ok": result.ok, "spec": spec.describe(), "executed": result.executed,
            "data": result.context.data, "artifacts": result.context.artifacts,
            "log": result.context.log, "error": result.context.error,
        })


def serve(port: int = 8800, host: str = "0.0.0.0") -> None:
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"browsergraph serving on http://{host}:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        srv.server_close()
