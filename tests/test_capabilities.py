"""Capability declarations, and the one way they can go wrong.

The risk with a declared table is that it drifts from the code: an engine
claims it can download files, nobody checks, and the failure surfaces months
later as an empty directory. So the table is cross-checked against what the
adapters actually implement.
"""
from __future__ import annotations

import pytest

from browsergraph import capabilities as caps
from browsergraph.dimensions import Engine
from browsergraph.drivers.mock import MockBrowser


def test_every_engine_declares_something():
    for engine in Engine:
        assert engine in caps.ENGINE_CAPABILITIES, f"{engine} declares nothing"


def test_every_capability_names_the_method_that_provides_it():
    for capability in caps.ALL:
        assert capability in caps.METHOD


def test_a_declaration_matches_what_the_adapter_implements():
    """The whole point of a table: it must not be able to lie.

    The mock declares everything, so it must implement everything.
    """
    declared = caps.of(Engine.MOCK)
    actual = caps.implemented(MockBrowser())
    assert declared <= actual, f"mock claims but lacks: {sorted(declared - actual)}"


def test_the_playwright_adapter_implements_what_it_claims():
    from browsergraph.drivers.playwright_driver import PlaywrightBrowser
    declared = caps.of(Engine.PLAYWRIGHT)
    actual = caps.implemented(PlaywrightBrowser)
    assert declared <= actual, f"playwright claims but lacks: {sorted(declared - actual)}"


def test_the_selenium_adapter_implements_what_it_claims():
    from browsergraph.drivers.selenium_driver import SeleniumBrowser
    declared = caps.of(Engine.SELENIUM)
    actual = caps.implemented(SeleniumBrowser)
    assert declared <= actual, f"selenium claims but lacks: {sorted(declared - actual)}"


def test_selenium_does_not_claim_downloads():
    """It cannot intercept one the way Playwright can, and says so."""
    assert caps.DOWNLOAD not in caps.of(Engine.SELENIUM)
    assert caps.DOWNLOAD in caps.of(Engine.PLAYWRIGHT)


def test_a_browser_less_engine_claims_no_browser_abilities():
    assert caps.of(Engine.HTTP) == frozenset()


def test_a_graph_is_checked_before_a_browser_exists():
    """The point of a pre-flight is that it costs nothing."""
    from browsergraph.nodes.browser_nodes import Download, Press

    nodes = [Press("Enter"), Download("#a", "/tmp/x")]
    assert caps.missing(Engine.PLAYWRIGHT, nodes) == []
    gaps = caps.missing(Engine.HTTP, nodes)
    assert len(gaps) == 2
    assert "needs 'press'" in str(gaps[0])


def test_the_refusal_names_the_way_out():
    from browsergraph.nodes.browser_nodes import Download

    usable = caps.engines_for([Download("#a", "/tmp/x")])
    assert Engine.PLAYWRIGHT in usable
    assert Engine.HTTP not in usable and Engine.SELENIUM not in usable


def test_asking_an_engine_for_what_it_lacks_raises_rather_than_no_ops():
    """A silently skipped upload is a run that reports success and uploaded
    nothing — the exact failure this library was written after."""
    class Bare:
        pass

    with pytest.raises(caps.Unsupported, match="Engines that do"):
        caps.require(Bare(), caps.UPLOAD, "upload")


def test_the_report_lists_every_engine_and_capability():
    text = caps.report()
    for engine in Engine:
        assert engine.value in text


def test_nodes_that_need_nothing_run_anywhere():
    from browsergraph.nodes.browser_nodes import A11yTree, AssertText, WaitStable

    for node in (WaitStable(), A11yTree(), AssertText("x")):
        assert caps.required(node) == (), f"{node.kind} should need no capability"
