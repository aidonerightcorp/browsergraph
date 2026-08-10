"""Descriptors are open; legality is not.

The rule these tests exist to defend is one sentence: **types bind, facets
rank**. Ports, effects and permissions decide whether a node may occupy a
position. Facets — purpose, prose, keywords, embeddings, cost, provenance —
decide which of the *legal* candidates is preferable, and nothing else.

The failure this prevents is the one every metadata layer eventually has: a
descriptor quietly becomes load-bearing, and then a node cannot be used until
somebody writes marketing copy for it, or worse, an embedding decides what is
allowed to run.
"""
from __future__ import annotations

from dataclasses import replace

from browsergraph import facets
from browsergraph.compile import compile_route
from browsergraph.demo import workbench

# --- the boundary -----------------------------------------------------------

def test_facets_cannot_change_whether_a_route_compiles():
    """The load-bearing test. Everything else here is detail."""
    bench = workbench()
    route = {stage.id: stage.candidates[0]
             for stage in bench.leaf_stages if stage.candidates}
    before = compile_route(bench, route)

    described = tuple(
        replace(node, facets={"purpose.statement": "does a thing",
                              "wildly.invented": ["never", "seen"],
                              "cost.usd_per_call": 0.0})
        for node in bench.nodes)
    after = compile_route(replace(bench, nodes=described), route)

    assert after.digest == before.digest, (
        "adding descriptors changed the compiled plan — descriptors are "
        "ranking metadata and must not reach the plan digest")
    assert replace(bench, nodes=described).validate() == bench.validate(), (
        "descriptors changed what validates")


def test_an_unknown_facet_is_carried_not_rejected():
    """Forward compatibility is the whole point of an open key space.

    A reader that rejected descriptors it had not heard of would make every
    pack that innovates unreadable by every harness that has not caught up.
    """
    assert facets.validate({"some.future_idea": "hello"}) == []


def test_a_declared_facet_with_the_wrong_value_is_a_real_error():
    """Open does not mean unchecked. Something will try to compare this."""
    problems = facets.validate({"cost.latency_ms": "quite fast"})
    assert problems and "number" in problems[0]


def test_a_facet_key_must_be_namespaced():
    problems = facets.validate({"cost": 3})
    assert problems and "namespaced" in problems[0]


# --- how a facet becomes searchable ----------------------------------------

def test_an_undeclared_facet_is_still_searchable_at_low_weight():
    """Invisible-until-registered is the closed-registry failure this avoids."""
    spec = facets.specs_for({"odd.notion": "some prose"})["odd.notion"]
    assert spec.kind == "text"
    assert spec.weight < 1.0


def test_facet_kinds_are_inferred_from_the_value_when_undeclared():
    got = facets.specs_for({"a.list": ["x", "y"], "a.number": 3.5,
                            "a.flag": True, "a.blob": {"k": "v"}})
    assert got["a.list"].kind == "keyword"
    assert got["a.number"].kind == "number"
    assert got["a.flag"].kind == "bool"
    assert got["a.blob"].kind == "json"


def test_searchable_text_stays_per_facet_rather_than_one_blob():
    """A query about outputs must be answerable by the output facet alone."""
    got = facets.fields({"io.output_desc": "a table of prices",
                         "purpose.statement": "scrape a listing"})
    assert set(got) == {"io.output_desc", "purpose.statement"}
    assert got["purpose.statement"][1] > got["io.output_desc"][1], (
        "the well-known weights say purpose outranks output prose")


def test_numbers_and_blobs_are_carried_but_not_ranked_as_text():
    got = facets.fields({"cost.usd_per_call": 0.01, "raw.thing": {"a": 1}})
    assert got == {}


def test_an_embedding_may_only_be_asked_for_where_it_helps():
    """Embedding a keyword set is possible and nearly always worse than
    matching it exactly."""
    bad = facets.FacetSpec("domain.tags", "keyword", embed=True).validate()
    assert bad and "embeds well" in bad[0]


# --- normalisation ----------------------------------------------------------

def test_keywords_accept_the_shapes_authors_actually_write():
    """An author who finds descriptors fiddly writes none."""
    assert facets.normalize("a, b ,c", "keyword") == ("a", "b", "c")
    assert facets.normalize(["a", "b"], "keyword") == ("a", "b")
    assert facets.normalize("solo", "keyword") == ("solo",)


def test_duplicate_keywords_collapse_but_keep_their_order():
    assert facets.normalize(["b", "a", "b"], "keyword") == ("b", "a")


def test_a_number_that_is_not_a_number_becomes_none_rather_than_raising():
    assert facets.normalize("later", "number") is None


# --- merging ----------------------------------------------------------------

def test_a_later_pack_wins_on_prose():
    got = facets.merge({"purpose.statement": "old"}, {"purpose.statement": "new"})
    assert got["purpose.statement"] == "new"


def test_keyword_facets_union_so_an_overlay_cannot_hide_a_node():
    """Replacing would silently drop the original author's tags and the node
    would stop being findable the way it used to be."""
    got = facets.merge({"domain.tags": ["web"]}, {"domain.tags": ["tabular"]})
    assert set(got["domain.tags"]) == {"web", "tabular"}


def test_the_well_known_vocabulary_is_internally_valid():
    """A starter vocabulary that fails its own validator teaches the wrong
    shape to everyone who copies it."""
    for spec in facets.WELL_KNOWN:
        assert spec.validate() == [], f"{spec.name}: {spec.validate()}"


def test_a_spec_survives_a_round_trip_through_json():
    spec = facets.FacetSpec("io.input_desc", "text", weight=2.0, embed=True,
                            description="what arrives")
    assert facets.FacetSpec.from_dict(spec.to_dict()) == spec
