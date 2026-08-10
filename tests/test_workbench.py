"""The rules that keep stages, candidates and routes from blurring together.

Almost every test here is a rejection. That is deliberate: the architecture is
defined much more by what it refuses than by what it permits, and the failures
worth catching are the quiet ones — a stage that omits a compatible candidate, a
route whose fallback points at another stage, a required step satisfied by a
pass-through, a metric with no source. Each of those produces a diagram that
looks entirely reasonable and is not true.
"""
from __future__ import annotations

import json

import pytest

from browsergraph.manifest import (
    NodeDefinition,
    NodeManifest,
    ParameterSpec,
    PortSpec,
    described_node,
    manifest_of,
    registry_manifests,
)
from browsergraph.workbench import (
    FeedbackDefinition,
    NodeCandidate,
    OptimizationObjective,
    OptimizationProfile,
    SolutionDefinition,
    StageDefinition,
    WorkbenchDefinition,
    candidate_id,
    expand_node_candidates,
)


def manifest(node_id="test.thing", **over) -> NodeManifest:
    base = dict(id=node_id, kind="thing", description="A thing.",
                roles=("transform",), capabilities=("do",),
                inputs=(PortSpec("in", "A"),), outputs=(PortSpec("out", "B"),))
    base.update(over)
    return NodeManifest(**base)


# --- manifests --------------------------------------------------------------

def test_a_valid_manifest_passes():
    assert manifest().validate() == []


def test_an_id_must_be_namespaced():
    """A bare name collides the moment two registries meet."""
    assert any("namespaced" in m for m in manifest("thing").validate())


def test_a_node_with_no_capabilities_can_never_be_discovered():
    problems = manifest(capabilities=()).validate()
    assert any("no stage can ever discover it" in m for m in problems)


def test_a_node_with_no_output_has_nothing_to_connect_to():
    assert any("no output port" in m for m in manifest(outputs=()).validate())


def test_a_node_with_no_description_is_not_discoverable():
    assert any("nobody can interpret" in m for m in manifest(description="").validate())


def test_every_problem_is_reported_not_just_the_first():
    """Stopping at the first error turns fixing a manifest into a guessing loop."""
    problems = manifest("bad id", capabilities=(), outputs=()).validate()
    assert len(problems) >= 3


def test_an_unknown_role_is_rejected():
    assert any("unknown role" in m for m in manifest(roles=("wizard",)).validate())


def test_a_default_outside_its_own_choices_is_rejected():
    bad = manifest(parameters=(ParameterSpec("mode", "string", default="z",
                                             choices=("a", "b")),))
    assert any("not one of its choices" in m for m in bad.validate())


def test_duplicate_ports_are_rejected():
    bad = manifest(inputs=(PortSpec("x", "A"), PortSpec("x", "B")))
    assert any("two input ports named" in m for m in bad.validate())


def test_assert_valid_raises_with_everything_wrong_listed():
    with pytest.raises(ValueError, match="invalid node manifest"):
        manifest(capabilities=()).assert_valid()


def test_a_parameter_is_only_searchable_when_it_has_alternatives():
    assert not ParameterSpec("x", choices=("only",)).searchable
    assert ParameterSpec("x", choices=("a", "b")).searchable


def test_a_manifest_round_trips_through_json():
    original = manifest(parameters=(ParameterSpec("mode", "string", default="a",
                                                  choices=("a", "b")),),
                        permissions=("browser",), metrics={"quality": 0.9})
    again = NodeManifest.from_dict(json.loads(original.to_json()))
    assert again == original


# --- describing existing code -----------------------------------------------

def test_a_node_class_can_carry_its_own_manifest():
    @described_node(id="test.described", kind="described",
                    description="Declared, not inferred.",
                    roles=("transform",), capabilities=("do",),
                    outputs=(PortSpec("out", "B"),))
    class Described:
        pass

    assert manifest_of(Described).id == "test.described"


def test_existing_nodes_get_manifests_without_being_rewritten():
    """The built-ins already declare reads/writes/mutates — that is most of a
    manifest, so they should not have to be described twice."""
    manifests = registry_manifests()
    assert len(manifests) > 10
    for m in manifests:
        assert m.validate() == [], f"{m.id}: {m.validate()}"


def test_a_mutating_node_is_described_as_having_an_effect():
    from browsergraph.nodes.actions import Click
    described = manifest_of(Click)
    assert "act" in described.capabilities
    assert any(e.startswith("external") for e in described.effects)


def test_a_definition_without_a_factory_can_still_be_inspected():
    """A registry must be able to describe what it cannot construct — another
    language, a service, or simply not installed here."""
    definition = NodeDefinition(manifest=manifest())
    assert not definition.buildable
    with pytest.raises(TypeError, match="description only"):
        definition.build()


def test_building_rejects_a_value_outside_the_declared_choices():
    built = []
    definition = NodeDefinition(
        manifest=manifest(parameters=(ParameterSpec("mode", "string", default="a",
                                                    choices=("a", "b")),)),
        factory=lambda **kw: built.append(kw) or kw)
    assert definition.build(mode="b") == {"mode": "b"}
    with pytest.raises(ValueError, match="not one of"):
        definition.build(mode="zzz")
    with pytest.raises(TypeError, match="unknown parameter"):
        definition.build(nope=1)


# --- candidate identity -----------------------------------------------------

def test_candidate_ids_do_not_depend_on_key_order():
    """Otherwise the same configuration accumulates evidence under two names."""
    a = candidate_id("x.y", {"model": "GLM", "strategy": "map-reduce"})
    b = candidate_id("x.y", {"strategy": "map-reduce", "model": "GLM"})
    assert a == b


def test_changing_a_value_changes_the_candidate_id():
    a = candidate_id("x.y", {"model": "GLM"})
    b = candidate_id("x.y", {"model": "Qwen"})
    assert a != b


def test_a_candidate_id_stays_readable():
    got = candidate_id("x.llm_parser", {"model": "DeepSeek", "strategy": "map-reduce"})
    assert "deepseek" in got and "map-reduce" in got


def test_a_family_expands_into_every_concrete_binding():
    """Five controllers x six binaries x two displays is sixty decisions, and
    drawing it as one box hides fifty-nine of them."""
    family = manifest(parameters=(
        ParameterSpec("controller", choices=("a", "b", "c", "d", "e")),
        ParameterSpec("binary", choices=("1", "2", "3", "4", "5", "6")),
        ParameterSpec("display", choices=("headless", "headed"))))
    assert family.variants() == 60
    assert len(expand_node_candidates([family])) == 60


def test_a_definition_with_nothing_to_choose_is_one_candidate():
    assert len(expand_node_candidates([manifest()])) == 1


# --- stages -----------------------------------------------------------------

def stage(**over) -> StageDefinition:
    base = dict(id="s", input_type="A", output_type="B",
                required_capabilities=("do",))
    base.update(over)
    return StageDefinition(**base)


def test_discovery_finds_every_compatible_candidate():
    nodes = [manifest("test.one"), manifest("test.two")]
    cands = expand_node_candidates(nodes)
    assert len(stage().with_discovered_candidates(nodes, cands).candidates) == 2


def test_a_node_with_the_wrong_capability_is_not_a_candidate():
    nodes = [manifest("test.one", capabilities=("something_else",))]
    cands = expand_node_candidates(nodes)
    assert stage().with_discovered_candidates(nodes, cands).candidates == ()


def test_a_node_that_cannot_connect_is_not_a_candidate():
    """Finding this out at run time is finding it out too late."""
    nodes = [manifest("test.one", outputs=(PortSpec("out", "WRONG"),))]
    cands = expand_node_candidates(nodes)
    assert stage().with_discovered_candidates(nodes, cands).candidates == ()


# --- workbench validation ---------------------------------------------------

def tiny(**over) -> WorkbenchDefinition:
    nodes = (manifest("test.one"), manifest("test.two"))
    cands = expand_node_candidates(nodes)
    one = stage(id="only").with_discovered_candidates(nodes, cands)
    base = dict(nodes=nodes, candidates=cands, stages=(one,),
                solutions=(SolutionDefinition(id="r", route={"only": cands[0].id}),))
    base.update(over)
    return WorkbenchDefinition(**base)


def test_the_demonstration_validates():
    from browsergraph.demo import workbench
    assert workbench().validate() == []


def test_a_stage_that_omits_a_compatible_candidate_is_rejected():
    """The rule that stops the picture becoming a summary of past opinions.

    Dropping the candidates that scored badly last time makes a diagram of what
    somebody once believed, and nothing on screen says so.
    """
    bench = tiny()
    thinned = bench.stages[0].__class__(
        **{**bench.stages[0].to_dict(),
           "candidates": bench.stages[0].candidates[:1],
           "required_capabilities": bench.stages[0].required_capabilities})
    problems = WorkbenchDefinition(nodes=bench.nodes, candidates=bench.candidates,
                                   stages=(thinned,)).validate()
    assert any("omits 1 compatible candidate" in p for p in problems)


def test_an_incomplete_route_is_rejected():
    bench = tiny(solutions=(SolutionDefinition(id="r", route={}),))
    assert any("incomplete" in p for p in bench.validate())


def test_a_route_cannot_pick_a_candidate_the_stage_does_not_admit():
    bench = tiny(solutions=(SolutionDefinition(id="r",
                                               route={"only": "not.a.candidate"}),))
    assert any("does not admit it" in p for p in bench.validate())


def test_a_fallback_must_stay_inside_its_own_stage():
    """A fallback is a second choice for a step, not an extra step."""
    bench = tiny()
    bad = SolutionDefinition(id="r", route={"only": bench.candidates[0].id},
                             fallbacks={"only": ("elsewhere.candidate",)})
    assert any("stays inside its stage" in p
               for p in tiny(solutions=(bad,)).validate())


def test_a_route_cannot_list_its_own_primary_as_a_fallback():
    bench = tiny()
    cid = bench.candidates[0].id
    bad = SolutionDefinition(id="r", route={"only": cid}, fallbacks={"only": (cid,)})
    assert any("own primary as a fallback" in p
               for p in tiny(solutions=(bad,)).validate())


def test_a_required_stage_cannot_be_satisfied_by_a_pass_through():
    """Skipping a required step because it is cheaper is not an optimization."""
    nodes = (manifest("test.one"), manifest("test.skip", tags=("pass-through",)))
    cands = expand_node_candidates(nodes)
    required = stage(id="only", optional=False).with_discovered_candidates(nodes, cands)
    problems = WorkbenchDefinition(nodes=nodes, candidates=cands,
                                   stages=(required,)).validate()
    assert any("pass-through candidate" in p for p in problems)


def test_an_optional_stage_may_use_a_pass_through():
    nodes = (manifest("test.one"), manifest("test.skip", tags=("pass-through",)))
    cands = expand_node_candidates(nodes)
    optional = stage(id="only", optional=True).with_discovered_candidates(nodes, cands)
    problems = WorkbenchDefinition(nodes=nodes, candidates=cands,
                                   stages=(optional,)).validate()
    assert not any("pass-through" in p for p in problems)


def test_adjacent_stages_that_cannot_connect_are_rejected():
    """Silently coercing here is how a route that cannot work looks fine."""
    nodes = (manifest("test.one"), manifest("test.two", inputs=(PortSpec("in", "C"),),
                                            outputs=(PortSpec("out", "D"),),
                                            capabilities=("later",)))
    cands = expand_node_candidates(nodes)
    first = stage(id="first").with_discovered_candidates(nodes, cands)
    second = stage(id="second", input_type="C", output_type="D",
                   required_capabilities=("later",)
                   ).with_discovered_candidates(nodes, cands)
    problems = WorkbenchDefinition(nodes=nodes, candidates=cands,
                                   stages=(first, second)).validate()
    assert any("insert an adapter stage rather than coercing" in p for p in problems)


def test_an_empty_stage_is_rejected():
    assert any("no candidates" in p
               for p in WorkbenchDefinition(stages=(stage(),)).validate())


def test_a_non_finite_metric_is_rejected():
    bench = tiny()
    bad = SolutionDefinition(id="r", route={"only": bench.candidates[0].id},
                             metrics={"quality": float("inf")})
    assert any("non-finite metric" in p for p in tiny(solutions=(bad,)).validate())


def test_feedback_must_authorise_something():
    channel = FeedbackDefinition(id="f.x", signal="S", scope="candidate", action="")
    assert any("authorises no action" in p
               for p in tiny(feedback_channels=(channel,)).validate())


def test_feedback_scope_must_be_one_we_know():
    channel = FeedbackDefinition(id="f.x", signal="S", scope="vibes", action="do")
    assert any("unknown scope" in p for p in tiny(feedback_channels=(channel,)).validate())


def test_an_objective_direction_must_be_a_direction():
    profile = OptimizationProfile(id="p.x", objectives=(
        OptimizationObjective("quality", "sideways", 1.0),))
    assert any("not one of" in p for p in tiny(optimization_profiles=(profile,)).validate())


def test_exploration_outside_zero_to_one_is_rejected():
    profile = OptimizationProfile(id="p.x", exploration=1.5, objectives=(
        OptimizationObjective("quality", "maximize", 1.0),))
    assert any("outside 0..1" in p for p in tiny(optimization_profiles=(profile,)).validate())


def test_an_unmeasured_metric_is_skipped_rather_than_scored_zero():
    """Scoring a missing measurement as zero punishes anything new for being new,
    and the system stops exploring without anyone deciding that it should."""
    profile = OptimizationProfile(id="p.x", objectives=(
        OptimizationObjective("quality", "maximize", 1.0),
        OptimizationObjective("unmeasured", "maximize", 1.0)))
    assert profile.score({"quality": 0.9}) == pytest.approx(0.9)


def test_a_profile_with_nothing_measured_scores_zero_not_a_crash():
    profile = OptimizationProfile(id="p.x", objectives=(
        OptimizationObjective("quality", "maximize", 1.0),))
    assert profile.score({}) == 0.0


# --- arithmetic -------------------------------------------------------------

def test_routes_multiply_they_do_not_add():
    from browsergraph.demo import workbench
    bench = workbench()
    counts = [len(s.candidates) for s in bench.stages]
    expected = 1
    for c in counts:
        expected *= c
    assert bench.route_count() == expected
    assert bench.route_count() == 32_864_832


def test_transitions_are_between_adjacent_stages_only():
    from browsergraph.demo import workbench
    bench = workbench()
    counts = [len(s.candidates) for s in bench.stages]
    expected = sum(a * b for a, b in zip(counts, counts[1:], strict=False))
    assert bench.transition_count() == expected == 2_827


def test_the_demonstration_has_the_shape_it_claims():
    from browsergraph.demo import workbench
    bench = workbench()
    assert len(bench.stages) == 6
    assert len(bench.nodes) == 48
    assert len(bench.candidates) == 149
    assert [len(s.candidates) for s in bench.stages] == [76, 27, 13, 14, 11, 8]


def test_every_demonstration_metric_says_it_is_a_prior():
    """A number without a source is an opinion wearing a lab coat."""
    from browsergraph.demo import workbench
    for node in workbench().nodes:
        if node.metrics:
            assert node.metrics.get("source") == "illustrative-prior", node.id


# --- wire format ------------------------------------------------------------

def test_a_workbench_round_trips():
    from browsergraph.demo import workbench
    original = workbench()
    again = WorkbenchDefinition.from_dict(json.loads(original.to_json()))
    assert again.validate() == []
    assert again.route_count() == original.route_count()
    assert [s.id for s in again.stages] == [s.id for s in original.stages]
    assert len(again.candidates) == len(original.candidates)


def test_the_version_one_planes_key_still_loads():
    """Older exports called ordered stages 'planes'. New output says 'stages',
    which does not collide with configuration planes."""
    data = {"schema_version": "2.0",
            "planes": [{"id": "old", "input_type": "A", "output_type": "B"}]}
    assert WorkbenchDefinition.from_dict(data).stages[0].id == "old"


def test_new_output_uses_stages_not_planes():
    from browsergraph.demo import workbench
    data = workbench().to_dict()
    assert "stages" in data and "planes" not in data


# --- the studio -------------------------------------------------------------

def test_the_studio_fetches_nothing(tmp_path):
    """These get opened from a laptop, a CI artifact and an offline machine.

    The SVG namespace URI is the one allowed http(s) string in the file: it
    identifies an XML namespace and is never requested. Everything else that
    looks like a URL would be a blank panel on a machine with no network.
    """
    import re

    from browsergraph.demo import workbench
    from browsergraph.studio import render
    html = render(workbench())
    for forbidden in ("<link", "<iframe", '@import', 'src="http', "src='http"):
        assert forbidden not in html, f"studio reaches for {forbidden}"
    urls = set(re.findall(r"https?://[^\s\"'<>)]+", html))
    assert urls <= {"http://www.w3.org/2000/svg"}, f"unexpected URLs: {urls}"


def test_the_dynamic_ports_of_a_per_instance_node_are_described(tmp_path):
    """`extract` writes to whatever `into` names, so on the class the attribute
    is a property. Describing that as a port called "<property object>" made the
    manifests unserializable — and would have been wrong even if it had worked."""
    from browsergraph.nodes.actions import Extract
    described = manifest_of(Extract)
    assert [p.name for p in described.outputs] == ["value"]
    assert described.outputs[0].description == "bound per instance"
    json.dumps(described.to_dict())      # must not raise


def test_every_view_renders():
    from browsergraph.demo import workbench
    from browsergraph.studio import VIEWS, render
    bench = workbench()
    for view in VIEWS:
        html = render(bench, view=view)
        assert f'show("{view}")' in html


def test_an_unknown_view_is_refused():
    from browsergraph.demo import workbench
    from browsergraph.studio import render
    with pytest.raises(ValueError, match="unknown view"):
        render(workbench(), view="kaleidoscope")


def test_a_closing_script_tag_in_the_data_cannot_break_the_page():
    """`</script>` in any description ends the element early and produces a blank
    page with a syntax error — a memorable way to learn that HTML is not JSON's
    parent context."""
    from browsergraph.studio import render
    nodes = (manifest("test.one", description="Handles </script> in text."),)
    cands = expand_node_candidates(nodes)
    bench = WorkbenchDefinition(
        nodes=nodes, candidates=cands,
        stages=(stage(id="only").with_discovered_candidates(nodes, cands),))
    html = render(bench)
    assert "</script>" not in html.split('id="workbench-data"')[1].split("</script>")[0]
    assert "<\\/script>" in html


def test_the_shipped_schemas_are_valid_json():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "browsergraph" / "schemas"
    for path in root.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["$schema"].startswith("https://json-schema.org")


def test_writing_a_suite_produces_every_projection(tmp_path):
    from browsergraph.demo import workbench
    from browsergraph.studio import VIEWS
    written = workbench().write_suite(tmp_path)
    names = {p.split("/")[-1] for p in written}
    assert "workbench.json" in names and "index.html" in names
    for filename in VIEWS.values():
        assert filename in names


# --- the CLI ----------------------------------------------------------------

def test_the_cli_writes_a_studio(tmp_path, capsys):
    from browsergraph.cli import main
    out = tmp_path / "studio.html"
    assert main(["workbench", "-o", str(out)]) == 0
    assert out.exists() and out.stat().st_size > 50_000
    assert "32,864,832" in capsys.readouterr().out


def test_the_cli_refuses_an_invalid_workbench(tmp_path, capsys):
    from browsergraph.cli import main
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"schema_version": "2.0",
                                  "stages": [{"id": "empty"}]}), encoding="utf-8")
    assert main(["workbench", str(broken), "-o", str(tmp_path / "x.html")]) == 1
    assert "does not validate" in capsys.readouterr().out


def test_nodes_json_emits_portable_manifests(capsys):
    from browsergraph.cli import main
    assert main(["nodes", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) > 10
    assert all(NodeManifest.from_dict(d).validate() == [] for d in data)


def test_a_candidate_names_itself_from_its_bindings():
    cand = NodeCandidate(id="x", node_id="demo.llm_parser",
                         params={"model": "GLM", "strategy": "map-reduce"})
    assert "GLM" in cand.name and "llm parser" in cand.name
