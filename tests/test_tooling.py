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


def test_the_packaged_version_matches_the_package():
    """These lived in two files and drifted.

    `pyproject.toml` said 0.1.0 while `browsergraph.__version__` said 0.2.0, so
    the release artifact would have shipped under the wrong version. The version
    is now single-sourced from `browsergraph/_version.py`, which is a module
    containing nothing but a literal so setuptools can read it *statically* —
    given anything else it falls back to importing the package, which fails in an
    isolated build environment and silently yields 0.0.0.
    """
    import browsergraph
    from browsergraph._version import __version__ as source

    assert browsergraph.__version__ == source

    try:
        import importlib.metadata as md
        installed = md.version("browsergraph")
    except Exception:
        return          # not installed as a distribution; nothing to compare
    assert installed == source, (
        f"distribution says {installed}, package says {source}")


def test_the_version_module_holds_only_a_literal():
    """An import in here would break static parsing and yield 0.0.0."""
    import pathlib

    import browsergraph
    src = (pathlib.Path(browsergraph.__file__).parent / "_version.py").read_text()
    code = [ln for ln in src.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    code = [ln for ln in code if not ln.strip().startswith(('"""', "'''"))]
    assert not any(ln.startswith(("import ", "from ")) for ln in code), src


# --- draw and solve, the two the library could do and the CLI could not ------

def _tiny_workbench():
    from browsergraph.quick import chain, graph, node, step
    from browsergraph.workbench import OptimizationObjective, OptimizationProfile

    nodes = [node("load.good", "load", gives=[("out", "Rows")]),
             node("load.empty", "load", gives=[("out", "Rows")]),
             node("keep.all", "keep", [("in", "Rows")], [("out", "Rows")]),
             node("keep.none", "keep", [("in", "Rows")], [("out", "Rows")])]
    steps = [step("load", "Load", [], [("out", "Rows")], "load",
                  ["load.good", "load.empty"]),
             step("keep", "Keep", [("in", "Rows")], [("out", "Rows")], "keep",
                  ["keep.all", "keep.none"])]
    return graph("Tiny", "Load rows and keep the good ones.", steps, nodes,
                 chain("load", "keep"),
                 profiles=[OptimizationProfile(id="p", objectives=(
                     OptimizationObjective("quality", "maximize", 1.0),))])


@pytest.fixture
def tiny(tmp_path):
    path = tmp_path / "tiny.json"
    path.write_text(json.dumps(_tiny_workbench().to_dict()))
    return path


def test_draw_writes_a_page_that_needs_no_network(tiny, tmp_path, capsys):
    out = tmp_path / "page.html"
    assert main(["draw", str(tiny), "-o", str(out)]) == 0
    page = out.read_text()
    assert "<svg" in page
    for forbidden in ("http://", "https://", "<script src", "@import"):
        assert forbidden not in page, f"the page reaches for {forbidden}"
    assert "4 routes" in capsys.readouterr().out.replace(",", "")


@pytest.mark.parametrize("form", ["mermaid", "json"])
def test_draw_prints_the_text_forms(tiny, capsys, form):
    assert main(["draw", str(tiny), "--format", form]) == 0
    assert "load" in capsys.readouterr().out


def test_draw_names_the_routes_it_knows_when_given_an_unknown_one(tiny, capsys):
    assert main(["draw", str(tiny), "--route", "nope"]) == 1
    assert "unknown route" in capsys.readouterr().out


def test_solve_refuses_rather_than_defaulting_to_a_weak_judge(tiny, capsys):
    """Defaulting to 'did it not raise' is the failure the design is arranged
    against, so the command names the three ways to say what good means."""
    assert main(["solve", str(tiny)]) == 1
    said = capsys.readouterr().out
    for option in ("--stage", "--verify", "--accept-anything"):
        assert option in said


def test_solve_finds_the_only_route_that_produces_anything(tiny, tmp_path,
                                                           capsys, monkeypatch):
    module = tmp_path / "tiny_runtime.py"
    module.write_text(
        "from browsergraph.execute import Runtime\n"
        "RUNTIME = Runtime({'load.good': lambda **kw: [1, 2, 3],\n"
        "                   'load.empty': lambda **kw: [],\n"
        "                   'keep.all': lambda **kw: list(kw['in']),\n"
        "                   'keep.none': lambda **kw: []})\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    out = tmp_path / "report.html"
    assert main(["solve", str(tiny), "--runtime", "tiny_runtime:RUNTIME",
                 "--stage", "keep", "--attempts", "6", "-o", str(out)]) == 0
    said = capsys.readouterr().out
    assert "load.good" in said and "keep.all" in said
    assert "3 did not work" in said, "the empty routes must be reported, not hidden"
    assert out.exists() and "<svg" in out.read_text()


def test_solve_says_so_when_there_is_nothing_to_rank_by(tmp_path, capsys):
    from dataclasses import replace

    bare = replace(_tiny_workbench(), optimization_profiles=())
    path = tmp_path / "bare.json"
    path.write_text(json.dumps(bare.to_dict()))
    assert main(["solve", str(path), "--accept-anything"]) == 1
    assert "optimization profile" in capsys.readouterr().out
