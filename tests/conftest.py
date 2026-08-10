"""Shared test setup.

The one thing here worth explaining: tests marked `network` reach real download
services — Google's Chrome for Testing index, Mozilla's releases, Microsoft's
driver endpoint. On a machine without a route to those, the honest outcome is
*skipped*, not *failed*: nothing is wrong with the code, and a suite that goes
red on an aeroplane is a suite people stop running.

The probe is done once and cached, so a disconnected run costs one timeout
rather than one per test.
"""
from __future__ import annotations

import functools
import os
import socket

import pytest


@functools.lru_cache(maxsize=1)
def _online() -> bool:
    """Can we reach a download service at all?

    A TCP connect rather than a request: it answers the only question that
    matters here, costs almost nothing, and does not depend on any particular
    service's uptime or on a specific URL still existing.

    `BG_SKIP_NETWORK_TESTS=1` forces the answer to no. That exists so the skip
    path can be exercised deliberately — an escape hatch nobody has ever tried
    is not an escape hatch — and so a sandboxed CI job can opt out without
    waiting for a connect to time out.
    """
    if os.environ.get("BG_SKIP_NETWORK_TESTS") == "1":
        return False
    for host in ("googlechromelabs.github.io", "github.com"):
        try:
            with socket.create_connection((host, 443), timeout=5):
                return True
        except OSError:
            continue
    return False


def pytest_collection_modifyitems(config, items):
    if _online():
        return
    skip = pytest.mark.skip(reason="no route to a download service")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)
