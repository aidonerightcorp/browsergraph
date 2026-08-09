import json

import pytest

from browsergraph.cli import main
from browsergraph.config import graph_from_list, spec_from_dict
from browsergraph.dimensions import (
    ENGINE_BINARIES,
    ENGINE_IMPORT,
    ENGINE_REQUIREMENT,
    Binary,
    Display,
    Engine,
    Spec,
    Stealth,
    Transport,
    validate,
)
from browsergraph.doctor import available_engines, run_all
from browsergraph.drivers.mock import MockBrowser
from browsergraph.graph import run
from browsergraph.sample import all_pairs, coverage, sample_specs

# --- engine matrix ----------------------------------------------------------

def test_every_engine_declares_its_metadata():
    for e in Engine:
        assert e in ENGINE_BINARIES, f"{e} missing binary matrix"
        assert e in ENGINE_IMPORT, f"{e} missing import probe"
        assert e in ENGINE_REQUIREMENT, f"{e} missing pip requirement"


def test_camoufox_is_firefox_only():
    assert ENGINE_BINARIES[Engine.CAMOUFOX] == (Binary.FIREFOX,)
    assert validate(Spec(engine=Engine.CAMOUFOX, binary=Binary.SYSTEM_CHROME))


def test_uc_engines_accept_undetected_stealth():
    for e in (Engine.SELENIUM_UC, Engine.SELENIUMBASE, Engine.NODRIVER,
              Engine.PATCHRIGHT):
        spec = Spec(engine=e, binary=Binary.SYSTEM_CHROME, stealth=Stealth.UNDETECTED)
        assert validate(spec) == [], (e, validate(spec))


def test_uc_cannot_run_on_grid():
    problems = validate(Spec(engine=Engine.SELENIUM_UC, binary=Binary.SYSTEM_CHROME,
                             transport=Transport.SELENIUM_GRID, endpoint="http://g"))
    assert any("grid" in p for p in problems)


# --- pairwise sampling ------------------------------------------------------

def test_all_pairs_covers_every_value_pair():
    axes = {"engine": list(Engine), "binary": list(Binary),
            "display": list(Display), "stealth": list(Stealth)}
    rows = all_pairs(axes)
    specs = [Spec(**r) for r in rows]
    covered, possible = coverage(axes, specs)
    assert covered == possible, f"{possible - covered} pairs uncovered"


def test_pairwise_is_far_smaller_than_full_product():
    axes = {"engine": list(Engine), "binary": list(Binary),
            "transport": list(Transport), "display": list(Display),
            "stealth": list(Stealth)}
    full = 1
    for v in axes.values():
        full *= len(v)
    rows = all_pairs(axes)
    assert len(rows) < full / 20, (len(rows), full)


def test_sample_specs_filters_to_runnable():
    specs = sample_specs({"engine": list(Engine), "binary": list(Binary)})
    assert specs and all(validate(s) == [] for s in specs)


# --- doctor -----------------------------------------------------------------

def test_doctor_runs_and_reports():
    rep = run_all()
    assert rep.checks
    names = {c.name for c in rep.checks}
    assert "python>=3.10" in names
    assert any(n.startswith("engine:") for n in names)
    assert any(n.startswith("binary:") for n in names)
    assert rep.text()


def test_missing_checks_carry_a_fix():
    for c in run_all().checks:
        if not c.ok:
            assert c.fix, f"{c.name} reports missing with no remedy"


def test_mock_always_available():
    assert Engine.MOCK in available_engines()


# --- config -----------------------------------------------------------------

def test_spec_from_dict_parses_enums_and_nested():
    spec = spec_from_dict({
        "engine": "playwright", "display": "headless", "behavior": "humanlike",
        "identity": {"viewport": [1280, 720], "locale": "en-GB"},
        "llm": {"mode": "selector", "model": "glm-5.2"},
    })
    assert spec.engine is Engine.PLAYWRIGHT
    assert spec.identity.viewport == (1280, 720)
    assert spec.llm.enabled and spec.llm.model == "glm-5.2"
    assert spec.behavior.typing_cps > 0


def test_graph_from_list_builds_runnable_graph():
    graph = graph_from_list([
        {"kind": "navigate", "url": "https://example.com"},
        {"kind": "extract", "selector": "h1", "into": "heading"},
    ])
    browser = MockBrowser(pages={"https://example.com": {"h1": "Welcome"}})
    result = run(graph, Spec(engine=Engine.MOCK), browser)
    assert result.ok and result.context.data["heading"] == "Welcome"


def test_config_roundtrip_from_json(tmp_path):
    cfg = tmp_path / "g.json"
    cfg.write_text(json.dumps({
        "spec": {"engine": "mock"},
        "nodes": [{"kind": "navigate", "url": "https://example.com"}],
    }))
    from browsergraph.config import load_graph
    graph, spec = load_graph(cfg)
    assert spec.engine is Engine.MOCK and len(graph.nodes) == 1


# --- cli --------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["doctor"], ["engines"], ["dimensions"], ["combos", "--limit", "3"],
    ["combos", "--limit", "2", "--why"], ["sample", "--limit", "3"],
])
def test_cli_commands_exit_zero(argv, capsys):
    assert main(argv) == 0
    assert capsys.readouterr().out.strip()


def test_cli_run_executes_config(tmp_path, capsys):
    cfg = tmp_path / "g.json"
    cfg.write_text(json.dumps({
        "spec": {"engine": "mock"},
        "nodes": [{"kind": "navigate", "url": "https://example.com"}],
    }))
    assert main(["run", str(cfg)]) == 0
    assert "mock" in capsys.readouterr().out


def test_top_up_recovers_pairs_lost_to_filtering():
    """Generate-then-filter silently drops coverage; top-up must restore it."""
    axes = {"engine": list(Engine), "binary": list(Binary),
            "display": list(Display), "stealth": list(Stealth)}
    naive = sample_specs(axes, top_up=False)
    full = sample_specs(axes, top_up=True)
    cov_naive, possible = coverage(axes, naive)
    cov_full, _ = coverage(axes, full)
    assert cov_full > cov_naive, (cov_naive, cov_full)
    assert cov_full / possible > 0.8
    assert all(validate(s) == [] for s in full)


def test_unreachable_pairs_are_not_forced():
    """Pairs no valid spec can carry (e.g. camoufox+chrome) stay uncovered."""
    axes = {"engine": [Engine.CAMOUFOX], "binary": list(Binary)}
    specs = sample_specs(axes)
    assert specs and all(s.binary is Binary.FIREFOX for s in specs)
