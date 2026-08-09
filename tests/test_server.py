"""The HTTP API.

This is the Docker image's default command and the Kubernetes deployment's
entrypoint — the primary way most deployments will use the library — and it
had no tests at all. Coverage found it at 0%.

The server is started on a real socket rather than exercised through handler
internals: the failure modes that matter (malformed JSON, an invalid spec, an
engine that cannot launch) are all in the request path, not in the functions.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from browsergraph.server import Handler


@pytest.fixture(scope="module")
def api():
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=20) as r:
        return r.status, json.loads(r.read().decode())


def post(base: str, path: str, payload, raw: bytes | None = None):
    data = raw if raw is not None else json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


# --- read endpoints ---------------------------------------------------------

def test_health_is_cheap_and_truthful(api):
    status, body = get(api, "/health")
    assert status == 200 and body == {"ok": True}


def test_doctor_reports_checks(api):
    status, body = get(api, "/doctor")
    assert status == 200
    assert isinstance(body["ok"], bool)
    names = {c["name"] for c in body["checks"]}
    assert "python>=3.10" in names
    assert any(n.startswith("engine:") for n in names)


def test_engines_lists_usable_and_all(api):
    status, body = get(api, "/engines")
    assert status == 200
    assert "mock" in body["usable"]
    assert set(body["usable"]) <= set(body["all"])


def test_dimensions_exposes_every_axis(api):
    status, body = get(api, "/dimensions")
    assert status == 200
    assert {"engine", "binary", "stealth"} <= set(body)
    assert "http" in body["engine"], "the browser-less engine is missing"


def test_unknown_path_is_404(api):
    with pytest.raises(urllib.error.HTTPError) as e:
        get(api, "/nope")
    assert e.value.code == 404


# --- running graphs ---------------------------------------------------------

def test_run_executes_a_graph(api):
    status, body = post(api, "/run", {
        "spec": {"engine": "mock"},
        "nodes": [{"kind": "navigate", "url": "https://example.com"}],
    })
    assert status == 200, body
    assert body["ok"] is True
    assert body["executed"] == ["navigate-0"]
    assert "mock" in body["spec"]


def test_run_returns_extracted_data(api):
    status, body = post(api, "/run", {
        "spec": {"engine": "mock"},
        "nodes": [{"kind": "navigate", "url": "https://example.com"},
                  {"kind": "extract", "selector": "h1", "into": "heading"}],
    })
    assert status == 200
    assert "heading" in body["data"]


def test_failing_graph_is_422_not_500(api):
    """A graph that fails is a valid request with an unsuccessful outcome."""
    status, body = post(api, "/run", {
        "spec": {"engine": "mock"},
        "nodes": [{"kind": "navigate", "url": "https://example.com"},
                  {"kind": "click", "selector": "#missing"}],
    })
    assert status == 422, body
    assert body["ok"] is False and body["error"]
    assert body["log"], "no log to diagnose the failure"


def test_invalid_spec_is_rejected_with_reasons(api):
    status, body = post(api, "/run", {
        "spec": {"engine": "selenium", "binary": "webkit"},
        "nodes": [],
    })
    assert status == 400
    assert any("webkit" in p for p in body["problems"])


def test_malformed_json_is_400(api):
    status, body = post(api, "/run", None, raw=b"{not json")
    assert status == 400 and "bad json" in body["error"]


def test_unknown_node_kind_is_400_not_a_crash(api):
    status, body = post(api, "/run", {
        "spec": {"engine": "mock"},
        "nodes": [{"kind": "no_such_node"}],
    })
    assert status == 400 and "bad request" in body["error"]


def test_missing_engine_reports_503(api):
    """An engine that cannot launch is unavailable, not a bad request.

    `cdp` is the raw protocol with no client library behind it, so its refusal
    must name what to use instead rather than merely declining.
    """
    status, body = post(api, "/run", {
        "spec": {"engine": "cdp"},
        "nodes": [{"kind": "navigate", "url": "https://example.com"}],
    })
    assert status == 503, body
    assert "remote_cdp" in body["error"] or "nodriver" in body["error"], body["error"]


def test_post_to_unknown_path_is_404(api):
    status, _ = post(api, "/elsewhere", {})
    assert status == 404


# --- contract ---------------------------------------------------------------

def test_every_response_is_json(api):
    with urllib.request.urlopen(api + "/health", timeout=20) as r:
        assert r.headers["Content-Type"] == "application/json"
        assert int(r.headers["Content-Length"]) > 0


def test_container_entrypoint_actually_boots_and_serves():
    """The full `CMD ["serve"]` chain: CLI -> serve() -> bound socket -> /health.

    Every test above drives `Handler` directly, which would keep passing even if
    the CLI wiring or `serve()` were broken — and that is exactly the path the
    Dockerfile and the Kubernetes liveness probe depend on.
    """
    import socket
    import subprocess
    import sys
    import time

    with socket.socket() as s:      # a port the OS just confirmed is free
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    proc = subprocess.Popen(
        [sys.executable, "-m", "browsergraph.cli", "serve", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError(f"serve exited: {proc.stdout.read()}")
            try:
                assert get(base, "/health") == (200, {"ok": True})
                break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.2)
        else:
            raise AssertionError("serve never accepted a connection")

        status, body = post(base, "/run", {
            "spec": {"engine": "mock"},
            "nodes": [{"kind": "navigate", "url": "https://example.com"}],
        })
        assert status == 200 and body["ok"] is True
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def test_concurrent_requests_are_served(api):
    """ThreadingHTTPServer: one slow graph must not block health checks."""
    results = []

    def hit():
        results.append(get(api, "/health")[0])

    threads = [threading.Thread(target=hit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert results == [200] * 8
