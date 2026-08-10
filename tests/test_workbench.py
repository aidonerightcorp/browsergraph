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
    # Reported per *edge* now rather than per adjacent pair, so a diamond's two
    # branches are both checked instead of only whichever came first in the list.
    # Asserted by substance rather than by sentence: the edge is named, both
    # types appear, and a way out is offered. Pinning the exact prose only means
    # the test fails when the message improves.
    edge = [p for p in problems if "first -> second" in p]
    assert edge, problems
    assert all(x in edge[0] for x in ("'B'", "'C'", "adapter")), edge[0]


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
    and the system stops exploring without anyone deciding that it should.

    Checked through `rank`, because scoring is comparative: this test used to
    assert `score({"quality": 0.9}) == 0.9`, which held only because the scorer
    passed raw values straight through — the very bug that made every objective
    profile produce an identical ranking.
    """
    profile = OptimizationProfile(id="p.x", objectives=(
        OptimizationObjective("quality", "maximize", 1.0),
        OptimizationObjective("unmeasured", "maximize", 1.0)))
    ranked = profile.rank({"good": {"quality": 0.9}, "poor": {"quality": 0.1}})
    assert [key for key, _ in ranked] == ["good", "poor"]
    assert ranked[0][1] == pytest.approx(1.0)


def test_a_profile_with_nothing_measured_scores_zero_not_a_crash():
    profile = OptimizationProfile(id="p.x", objectives=(
        OptimizationObjective("quality", "maximize", 1.0),))
    assert profile.score({}) == 0.0


# --- arithmetic -------------------------------------------------------------

def test_routes_multiply_they_do_not_add():
    from browsergraph.demo import workbench
    bench = workbench()
    counts = [len(s.candidates) for s in bench.leaf_stages]
    expected = 1
    for c in counts:
        expected *= c
    assert bench.route_count() == expected
    assert bench.route_count() == 3_802_314_700_800


def test_transitions_are_between_adjacent_stages_only():
    from browsergraph.demo import workbench
    bench = workbench()
    counts = [len(s.candidates) for s in bench.leaf_stages]
    expected = sum(a * b for a, b in zip(counts, counts[1:], strict=False))
    assert bench.transition_count() == expected == 1_337


def test_the_demonstration_has_the_shape_it_claims():
    from browsergraph.demo import workbench
    bench = workbench()
    assert len(bench.stages) == 6
    assert len(bench.leaf_stages) == 14
    assert len(bench.nodes) == 57
    assert len(bench.candidates) == 166
    assert [len(s.candidates) for s in bench.leaf_stages] == \
        [4, 70, 6, 6, 11, 12, 5, 9, 6, 14, 3, 9, 7, 4]


def test_decomposing_a_stage_exposes_the_choices_it_was_hiding():
    """The reason sub-steps exist at all.

    Pooling every candidate in a stage into one decision — which is what a
    coarse diagram implicitly claims — counts 85,747,200 routes. The sub-steps
    those same stages are made of expose 3.8 trillion. Same task, same registry,
    same code: the coarse view was hiding 44,343x of the space.
    """
    from browsergraph.demo import workbench
    bench = workbench()
    assert bench.coarse_route_count() == 85_747_200
    assert bench.route_count() // bench.coarse_route_count() == 44_343


def test_a_stage_is_a_leaf_or_a_composite_but_never_both():
    """Otherwise "one choice per stage" stops being well defined."""
    from browsergraph.demo import workbench
    for stage in workbench().stages:
        assert stage.is_composite
        assert not stage.candidates, "a composite stage must not hold candidates"
        for leaf in stage.leaves():
            assert leaf.candidates and not leaf.substages


def test_a_composite_stage_must_agree_with_its_own_sub_steps():
    nodes = (manifest("test.one"),)
    cands = expand_node_candidates(nodes)
    leaf = stage(id="inner").with_discovered_candidates(nodes, cands)
    parent = StageDefinition(id="outer", input_type="A", output_type="WRONG",
                             substages=(leaf,))
    problems = WorkbenchDefinition(nodes=nodes, candidates=cands,
                                   stages=(parent,)).validate()
    assert any("produces 'WRONG'" in p for p in problems)


def test_a_stage_holding_both_candidates_and_sub_steps_is_rejected():
    nodes = (manifest("test.one"),)
    cands = expand_node_candidates(nodes)
    leaf = stage(id="inner").with_discovered_candidates(nodes, cands)
    both = StageDefinition(id="outer", input_type="A", output_type="B",
                           substages=(leaf,), candidates=(cands[0].id,))
    problems = WorkbenchDefinition(nodes=nodes, candidates=cands,
                                   stages=(both,)).validate()
    assert any("never both" in p for p in problems)


def test_sub_steps_nest_arbitrarily_deep():
    """Sub-matrices are recursive, so a sub-step can itself decompose.

    Three levels here; nothing in the model caps it. `leaves()` flattens
    whatever depth exists, and a route is one choice per leaf however deep the
    leaf sits.
    """
    nodes = (manifest("test.one"),)
    cands = expand_node_candidates(nodes)
    inner = stage(id="inner").with_discovered_candidates(nodes, cands)
    middle = StageDefinition(id="middle", input_type="A", output_type="B",
                             substages=(inner,))
    outer = StageDefinition(id="outer", input_type="A", output_type="B",
                            substages=(middle,))
    bench = WorkbenchDefinition(nodes=nodes, candidates=cands, stages=(outer,))
    assert bench.validate() == []
    assert outer.depth() == 3
    assert [s.id for s in bench.leaf_stages] == ["inner"]
    assert bench.route_count() == len(cands)


def test_sub_steps_survive_a_round_trip():
    from browsergraph.demo import workbench
    again = WorkbenchDefinition.from_dict(json.loads(workbench().to_json()))
    assert len(again.leaf_stages) == 14
    assert again.validate() == []


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
    assert "3,802,314,700,800" in capsys.readouterr().out


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


# --- the graph, rather than the chain ---------------------------------------

def diamond():
    """parse -> (price || title) -> join: the shape a sequence cannot hold."""
    from browsergraph.workbench import Edge

    def node(node_id, ins, outs, capability):
        return NodeManifest(
            id=node_id, kind=node_id.split(".")[-1], description="x",
            roles=("transform",), capabilities=(capability,),
            inputs=tuple(PortSpec(n, t) for n, t in ins),
            outputs=tuple(PortSpec(n, t) for n, t in outs)).assert_valid()

    nodes = (node("d.parse", [("i", "Raw")], [("o", "Doc")], "parse"),
             node("d.price", [("i", "Doc")], [("o", "Price")], "price"),
             node("d.title", [("i", "Doc")], [("o", "Title")], "title"),
             node("d.join", [("price", "Price"), ("title", "Title")],
                  [("o", "Result")], "join"))
    cands = expand_node_candidates(nodes)
    stages = tuple(s.with_discovered_candidates(nodes, cands) for s in (
        StageDefinition(id="parse", input_type="Raw", output_type="Doc",
                        required_capabilities=("parse",)),
        StageDefinition(id="price", input_type="Doc", output_type="Price",
                        required_capabilities=("price",)),
        StageDefinition(id="title", input_type="Doc", output_type="Title",
                        required_capabilities=("title",)),
        StageDefinition(id="join", required_capabilities=("join",),
                        inputs=(PortSpec("price", "Price"),
                                PortSpec("title", "Title")),
                        outputs=(PortSpec("o", "Result"),)),
    ))
    edges = (Edge("parse", "price"), Edge("parse", "title"),
             Edge("price", "join", to_port="price"),
             Edge("title", "join", to_port="title"))
    return nodes, cands, stages, edges


def test_a_diamond_is_expressible():
    """The finding that motivated all of this: the old model could not express
    fan-out, and rejected it *correctly by its own rules* — the rules assumed a
    chain."""
    nodes, cands, stages, edges = diamond()
    bench = WorkbenchDefinition(nodes=nodes, candidates=cands, stages=stages,
                                edges=edges)
    assert bench.validate() == []
    assert not bench.is_chain
    assert bench.layers() == [["parse"], ["price", "title"], ["join"]]
    assert bench.sources() == ["parse"] and bench.sinks() == ["join"]


def test_a_chain_still_works_with_no_edges_declared():
    """Every workbench written before edges existed meant "a chain in declared
    order", so that is what an empty edge list means."""
    from browsergraph.demo import workbench
    bench = workbench()
    assert bench.is_chain
    assert len(bench.wiring()) == len(bench.leaf_stages) - 1
    assert bench.validate() == []


def test_a_wrong_port_is_caught_even_when_the_types_exist_elsewhere():
    """Both branches produce a type the join accepts — just not on that port."""
    from browsergraph.workbench import Edge
    nodes, cands, stages, _ = diamond()
    crossed = (Edge("parse", "price"), Edge("parse", "title"),
               Edge("price", "join", to_port="title"),
               Edge("title", "join", to_port="price"))
    problems = WorkbenchDefinition(nodes=nodes, candidates=cands, stages=stages,
                                   edges=crossed).validate()
    # Both crossings are reported, and each names the *port* it went into —
    # without that, "Price is not a Title" is unactionable in a node with four
    # inputs, because nothing says which one was wired wrongly.
    assert any("price -> join.title" in p and "'Price'" in p and "'Title'" in p
               for p in problems), problems
    assert any("title -> join.price" in p and "'Title'" in p and "'Price'" in p
               for p in problems), problems


def test_a_cycle_is_refused():
    """Invisible in a list of edges, and fatal: a plan with a cycle cannot be
    ordered, so nothing can run."""
    from browsergraph.workbench import Edge
    nodes, cands, stages, _ = diamond()
    looped = (Edge("parse", "price"), Edge("price", "title"),
              Edge("title", "parse"))
    problems = WorkbenchDefinition(nodes=nodes, candidates=cands, stages=stages,
                                   edges=looped).validate()
    assert any("cycle" in p for p in problems)


def test_an_unfed_required_input_is_refused():
    """A port nobody writes to is a stage that cannot start."""
    from browsergraph.workbench import Edge
    nodes, cands, stages, _ = diamond()
    partial = (Edge("parse", "price"), Edge("parse", "title"),
               Edge("price", "join", to_port="price"))     # title never joined
    problems = WorkbenchDefinition(nodes=nodes, candidates=cands, stages=stages,
                                   edges=partial).validate()
    assert any("needs input 'title'" in p for p in problems)


def test_an_edge_to_a_port_that_does_not_exist_says_which_do():
    from browsergraph.workbench import Edge
    nodes, cands, stages, _ = diamond()
    problems = WorkbenchDefinition(
        nodes=nodes, candidates=cands, stages=stages,
        edges=(Edge("price", "join", to_port="nope"),)).validate()
    assert any("has no input port" in p and "price" in p for p in problems)


def test_transitions_are_counted_per_edge_not_per_adjacent_pair():
    """In a diamond, both branches connect to the join; a sequential count
    would miss one of them entirely."""
    nodes, cands, stages, edges = diamond()
    bench = WorkbenchDefinition(nodes=nodes, candidates=cands, stages=stages,
                                edges=edges)
    by_id = {s.id: s for s in bench.leaf_stages}
    expected = sum(len(by_id[e.source].candidates) * len(by_id[e.target].candidates)
                   for e in edges)
    assert bench.transition_count() == expected


def test_a_join_needs_a_node_that_accepts_both_inputs():
    """Not one that happens to accept the first."""
    nodes, cands, stages, _ = diamond()
    join = next(s for s in stages if s.id == "join")
    half = NodeManifest(id="d.half", kind="half", description="x",
                        roles=("transform",), capabilities=("join",),
                        inputs=(PortSpec("price", "Price"),),
                        outputs=(PortSpec("o", "Result"),)).assert_valid()
    assert not join.eligible(half), "a node missing an input port is not eligible"
    assert join.eligible(next(n for n in nodes if n.id == "d.join"))


def test_the_graph_shape_travels_in_the_wire_format():
    nodes, cands, stages, edges = diamond()
    original = WorkbenchDefinition(nodes=nodes, candidates=cands, stages=stages,
                                   edges=edges)
    again = WorkbenchDefinition.from_dict(json.loads(original.to_json()))
    assert again.validate() == []
    assert again.layers() == original.layers()
    assert len(again.wiring()) == len(edges)


# --- discovery and compilation must agree about types -----------------------

def test_discovery_honours_a_declared_subtype_relation():
    """Discovery used `==` on type names while the compiler used the lattice.

    A loader producing `CsvRecords` for a stage asking for `Records` compiles
    perfectly and was invisible to discovery, so the stage came back empty and
    the workbench reported zero routes — which is what notebook 01 did, in
    public, for its whole existence. A registry whose search is stricter than
    its compiler hides exactly the nodes that were most carefully described.
    """
    from browsergraph.manifest import NodeManifest, PortSpec
    from browsergraph.workbench import (
        StageDefinition,
        expand_node_candidates,
    )

    loader = NodeManifest(
        id="probe.csv", kind="csv", description="Loads a CSV file.",
        capabilities=("load",),
        inputs=(PortSpec("path", "FilePath"),),
        outputs=(PortSpec("records", "CsvRecords"),),
        runtime={"is_a": {"CsvRecords": ["Records"]}},
    ).assert_valid()

    nodes = (loader,)
    stage = StageDefinition(
        id="load", input_type="FilePath", output_type="Records",
        required_capabilities=("load",),
    ).with_discovered_candidates(nodes, expand_node_candidates(nodes))

    assert stage.candidates, "a declared subtype was not discovered"


def test_discovery_still_refuses_a_type_that_is_not_related():
    """The fix must widen discovery to the lattice, not switch it off."""
    from browsergraph.manifest import NodeManifest, PortSpec
    from browsergraph.workbench import (
        StageDefinition,
        expand_node_candidates,
    )

    loader = NodeManifest(
        id="probe.csv", kind="csv", description="Loads a CSV file.",
        capabilities=("load",),
        inputs=(PortSpec("path", "FilePath"),),
        outputs=(PortSpec("records", "CsvRecords"),),
        runtime={"is_a": {"CsvRecords": ["Records"]}},
    ).assert_valid()

    nodes = (loader,)
    stage = StageDefinition(
        id="load", input_type="FilePath", output_type="Image",
        required_capabilities=("load",),
    ).with_discovered_candidates(nodes, expand_node_candidates(nodes))

    assert stage.candidates == ()


def test_a_supertype_is_not_accepted_where_a_subtype_is_required():
    """Widening is safe in one direction only. A stage promising to hand over
    `Records` cannot be served by a node that only accepts `CsvRecords`."""
    from browsergraph.manifest import NodeManifest, PortSpec
    from browsergraph.workbench import (
        StageDefinition,
        expand_node_candidates,
    )

    narrow = NodeManifest(
        id="probe.narrow", kind="narrow", description="Only takes CSV records.",
        capabilities=("count",),
        inputs=(PortSpec("r", "CsvRecords"),),
        outputs=(PortSpec("n", "Count"),),
        runtime={"is_a": {"CsvRecords": ["Records"]}},
    ).assert_valid()

    nodes = (narrow,)
    stage = StageDefinition(
        id="count", input_type="Records", output_type="Count",
        required_capabilities=("count",),
    ).with_discovered_candidates(nodes, expand_node_candidates(nodes))

    assert stage.candidates == ()


# --- saving must not destroy the thing being saved ---------------------------

def test_a_stage_keeps_its_ports_through_json():
    """This was a bug that silently wrecked graphs.

    A stage built with `inputs=(PortSpec("in", "Profile"),)` and no
    `input_type` wrote *neither* field: the shorthand was empty and the port
    list is skipped for a single port named "in". Loading it back gave a stage
    with no ports, and every edge then failed with "names a port that does not
    exist" — on a workbench that had been perfectly valid before it was saved.
    """
    from browsergraph.manifest import PortSpec
    from browsergraph.workbench import StageDefinition

    stage = StageDefinition(id="profile",
                            inputs=(PortSpec("in", "Profile"),),
                            outputs=(PortSpec("out", "Findings"),))
    back = StageDefinition.from_dict(stage.to_dict())
    assert [(p.name, p.type) for p in back.inputs] == [("in", "Profile")]
    assert [(p.name, p.type) for p in back.outputs] == [("out", "Findings")]


def test_a_whole_workbench_survives_a_round_trip_and_still_compiles():
    """The portable format is the claim that another language could read this.
    A round trip that loses ports makes that claim false."""
    from dataclasses import replace

    from browsergraph import templates
    from browsergraph.compile import compile_route
    from browsergraph.manifest import NodeManifest, PortSpec
    from browsergraph.workbench import WorkbenchDefinition

    template = templates.get("data.quality")
    nodes = tuple(NodeManifest(
        id=f"probe.{s.id}", kind="probe", description=f"Probe {s.id}.",
        capabilities=(s.capabilities[0],),
        inputs=tuple(PortSpec(n, t) for n, t in s.inputs),
        outputs=tuple(PortSpec(n, t) for n, t in s.outputs))
        for s in template.slots)
    bench = replace(template.instantiate(
        {s.id: [f"probe.{s.id}"] for s in template.slots}), nodes=nodes)

    reloaded = WorkbenchDefinition.from_dict(bench.to_dict())
    route = {s.id: f"probe.{s.id}" for s in template.slots}
    assert reloaded.validate() == bench.validate()
    assert compile_route(reloaded, route).digest == compile_route(bench, route).digest


def test_a_named_port_still_round_trips():
    """The shorthand only covers the single in/out case. Named ports must keep
    using the explicit list."""
    from browsergraph.manifest import PortSpec
    from browsergraph.workbench import StageDefinition

    stage = StageDefinition(id="join",
                            inputs=(PortSpec("price", "Money"),
                                    PortSpec("title", "Text")),
                            outputs=(PortSpec("out", "Record"),))
    back = StageDefinition.from_dict(stage.to_dict())
    assert [p.name for p in back.inputs] == ["price", "title"]
