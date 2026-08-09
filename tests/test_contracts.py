"""Node contracts: declaration, composition, execution.

The library's checks are only as good as the claims they read. BG003 decides
whether a graph verifies its mutations by trusting `mutates`; the scheduler
decides what may run concurrently by trusting `reads`/`writes`. A node that
misdeclares itself does not fail — it silently switches those checks off.

So these tests enforce the claims at all three moments: when a node class is
defined, when nodes are composed into a graph, and when the graph runs.
"""
from __future__ import annotations

import pytest

from browsergraph import Engine, Graph, Spec, run
from browsergraph.contracts import (
    FLAGS,
    Contract,
    ContractError,
    audit,
    check_class,
    contract_of,
)
from browsergraph.drivers.mock import MockBrowser
from browsergraph.drivers.recording import RecordingBrowser
from browsergraph.graph import Edge, EdgeKind
from browsergraph.nodes import REGISTRY
from browsergraph.nodes.actions import Click, Extract, Navigate, WaitFor
from browsergraph.nodes.base import Node
from browsergraph.nodes.checked import Checked, ContractViolation, checked, violations

PAGES = {"https://acme.example": {"h1": "Acme Roofing", "#login": "Log in"}}


def mock() -> MockBrowser:
    return MockBrowser(pages=PAGES)


# --- every shipped node conforms -------------------------------------------

@pytest.mark.parametrize("kind", sorted(REGISTRY))
def test_registered_node_satisfies_the_contract(kind):
    """The conformance suite: run the class rules over everything we ship."""
    assert check_class(REGISTRY[kind]) == []


@pytest.mark.parametrize("kind", sorted(REGISTRY))
def test_registered_kind_matches_its_registry_key(kind):
    assert REGISTRY[kind].kind == kind, "a node registered under the wrong key"


def test_every_node_kind_is_unique():
    kinds = [cls.kind for cls in REGISTRY.values()]
    assert len(kinds) == len(set(kinds))


def test_contract_is_readable_off_an_instance():
    c = contract_of(Extract("h1", into="heading"))
    assert c.kind == "extract"
    assert "heading" in c.writes
    assert c.describe()


def test_contract_flags_are_all_booleans_on_real_nodes():
    for cls in REGISTRY.values():
        c = contract_of(cls)
        for flag in FLAGS:
            assert isinstance(getattr(c, flag), bool), f"{cls.__name__}.{flag}"


def test_risky_identifies_mutation_without_verification():
    assert contract_of(Click("#login")).risky
    assert not contract_of(WaitFor("#login")).risky


# --- definition-time enforcement -------------------------------------------

def test_missing_comma_in_writes_is_rejected():
    """`writes = ("url")` is the string "url"; consumers iterate u, r, l."""
    with pytest.raises(ContractError, match="missing comma"):
        class Bad(Node):
            kind = "bad_missing_comma"
            writes = ("url")            # noqa: UP034 - the bug under test

            def run(self, ctx):
                return ctx


def test_a_node_without_a_kind_is_rejected():
    with pytest.raises(ContractError, match="does not set `kind`"):
        class Bad(Node):
            def run(self, ctx):
                return ctx


def test_a_kind_that_is_not_snake_case_is_rejected():
    """Kinds travel through JSON configs, CLI args and plugin manifests."""
    with pytest.raises(ContractError, match="snake_case"):
        class Bad(Node):
            kind = "Not Snake"

            def run(self, ctx):
                return ctx


def test_a_node_that_never_implements_run_is_rejected():
    with pytest.raises(ContractError, match="does not implement run"):
        class Bad(Node):
            kind = "no_run_at_all"


def test_a_non_boolean_flag_is_rejected():
    with pytest.raises(ContractError, match="must be a bool"):
        class Bad(Node):
            kind = "bad_flag"
            mutates = "yes"

            def run(self, ctx):
                return ctx


def test_an_abstract_base_is_exempt():
    """Intermediate bases exist to be subclassed, not run."""
    class Mixin(Node):
        abstract = True

    assert Mixin.abstract

    class Real(Mixin):
        kind = "real_child"

        def run(self, ctx):
            return ctx

    assert check_class(Real) == []
    assert not Real.abstract, "abstract must not be inherited"


def test_a_conforming_node_is_accepted():
    class Fine(Node):
        kind = "fine_node"
        writes = ("thing",)
        needs_browser = False

        def run(self, ctx):
            ctx.data["thing"] = 1
            return ctx

    assert contract_of(Fine()).writes == ("thing",)


# --- composition-time audit -------------------------------------------------

def test_audit_passes_on_a_well_ordered_graph():
    g = (Graph("ok").add(Navigate("https://acme.example"))
         .add(Extract("h1", into="heading")))
    assert audit(g).ok


def test_audit_catches_a_read_before_its_write():
    class NeedsHeading(Node):
        kind = "needs_heading"
        reads = ("heading",)
        needs_browser = False

        def run(self, ctx):
            return ctx

    g = Graph("bad").add(NeedsHeading()).add(Extract("h1", into="heading"))
    result = g.audit()
    assert not result.ok
    assert "runs later" in result.text(), "the hint should name the fix"


def test_audit_reports_a_key_nothing_writes():
    class NeedsGhost(Node):
        kind = "needs_ghost"
        reads = ("ghost",)
        needs_browser = False

        def run(self, ctx):
            return ctx

    result = Graph("g").add(NeedsGhost()).audit()
    assert "nothing in this graph writes" in result.text()


def test_seed_keys_satisfy_a_read():
    class NeedsSeed(Node):
        kind = "needs_seed"
        reads = ("start_url",)
        needs_browser = False

        def run(self, ctx):
            return ctx

    assert Graph("g").add(NeedsSeed()).audit(seed_keys=("start_url",)).ok


# --- typed edges ------------------------------------------------------------

def test_sequence_and_dependency_edges_are_distinguished():
    g = (Graph("e").add(Navigate("https://acme.example")).add(WaitFor("#login"))
         .add(Extract("h1", into="h"), after="navigate"))
    kinds = {e.kind for e in g.edges}
    assert kinds == {EdgeKind.SEQUENCE, EdgeKind.DEPENDENCY}
    dep = [e for e in g.edges if e.kind is EdgeKind.DEPENDENCY][0]
    assert dep.reason, "an explicit edge should say why it exists"


def test_edge_still_destructures_as_a_pair():
    """Existing code does `for a, b in graph.edges` — that must keep working."""
    e = Edge("a", "b")
    src, dst, *_ = e
    assert (src, dst) == ("a", "b")
    assert e.src == "a" and e.kind is EdgeKind.SEQUENCE


def test_dependency_edge_creates_a_parallel_level():
    g = (Graph("p").add(Navigate("https://acme.example")).add(WaitFor("#login"))
         .add(Extract("h1", into="h"), after="navigate"))
    assert any(len(lvl) > 1 for lvl in g.levels())


def test_mermaid_marks_mutating_and_verifying_nodes():
    g = (Graph("m").add(Navigate("https://acme.example"))
         .add(WaitFor("#login")).add(Click("#login")))
    dia = g.to_mermaid()
    assert "flowchart TD" in dia
    assert ":::mutates" in dia and ":::verifies" in dia
    assert "classDef mutates" in dia


def test_to_dict_round_trips_the_structure():
    g = (Graph("d").add(Navigate("https://acme.example")).add(WaitFor("#login")))
    d = g.to_dict()
    assert [n["key"] for n in d["nodes"]] == ["navigate", "wait_for"]
    assert d["edges"][0]["kind"] == "sequence"
    assert d["levels"]


# --- execution-time enforcement --------------------------------------------

def test_recording_browser_logs_calls_without_changing_behaviour():
    rec = RecordingBrowser(mock())
    rec.start()
    rec.goto("https://acme.example")
    assert rec.methods_used() == {"start", "goto"}
    assert not rec.mutated
    assert rec.text_of("h1") == "Acme Roofing", "delegation must be transparent"

    rec.click("#login")
    assert rec.mutated
    assert "click('#login')" in rec.log()


def test_recording_browser_records_a_failure_and_reraises_it():
    """An observer that swallows errors changes what it observes."""
    class Exploding:
        def start(self): pass
        def stop(self): pass
        def click(self, selector): raise RuntimeError("element detached")

    rec = RecordingBrowser(Exploding())
    with pytest.raises(RuntimeError, match="element detached"):
        rec.click("#login")
    assert rec.calls[-1].error.startswith("RuntimeError")
    assert "element detached" in rec.log()[-1]


def test_checked_passes_a_well_behaved_graph():
    g = checked(Graph("good").add(Navigate("https://acme.example"))
                .add(Extract("h1", into="heading")))
    result = run(g, Spec(engine=Engine.MOCK), mock())
    assert result.ok, result.context.error
    assert result.context.data["heading"] == "Acme Roofing"
    assert violations(g) == []


def test_checked_catches_a_node_that_does_not_write_what_it_declares():
    class Liar(Node):
        kind = "liar_writes"
        writes = ("promised",)
        needs_browser = False

        def run(self, ctx):
            return ctx            # never writes `promised`

    g = Graph("liar").add(Checked(Liar()))
    with pytest.raises(ContractViolation, match="never set"):
        run(g, Spec(engine=Engine.MOCK), mock())


def test_checked_catches_an_undeclared_mutation():
    """The violation that would silently disarm BG003."""
    class SneakyClick(Node):
        kind = "sneaky_click"
        mutates = False           # the lie

        def run(self, ctx):
            ctx.page.click("#login")
            return ctx

    g = Graph("sneak").add(Navigate("https://acme.example")).add(Checked(SneakyClick()))
    with pytest.raises(ContractViolation, match="mutates=False but called"):
        run(g, Spec(engine=Engine.MOCK), mock())


def test_non_strict_mode_records_instead_of_raising():
    class Liar(Node):
        kind = "liar_soft"
        writes = ("promised",)
        needs_browser = False

        def run(self, ctx):
            return ctx

    g = Graph("soft").add(Checked(Liar(), strict=False))
    result = run(g, Spec(engine=Engine.MOCK), mock())
    assert result.ok, "non-strict must not fail the run"
    assert violations(g) and "never set" in violations(g)[0]


def test_checked_is_transparent_to_the_linter():
    """Wrapping must not change what BG003 sees, or it defeats its own purpose."""
    from browsergraph.lint import lint

    plain = (Graph("plain").add(Navigate("https://acme.example"))
             .add(WaitFor("#login")).add(Click("#login")))
    wrapped = checked((Graph("wrapped").add(Navigate("https://acme.example"))
                       .add(WaitFor("#login")).add(Click("#login"))))
    assert ({f.code for f in lint(plain)} == {f.code for f in lint(wrapped)})
    assert "BG003" in {f.code for f in lint(wrapped)}


def test_checked_restores_the_original_browser():
    """The recorder must not leak into later nodes."""
    seen = {}

    class Peek(Node):
        kind = "peek_browser"

        def run(self, ctx):
            seen["type"] = type(ctx.browser).__name__
            return ctx

    g = (Graph("restore").add(Checked(Navigate("https://acme.example")))
         .add(Peek()))
    run(g, Spec(engine=Engine.MOCK), mock())
    assert seen["type"] == "MockBrowser"


def test_checked_reports_the_inner_contract_not_its_own():
    c = Checked(Click("#login")).contract()
    assert c.kind == "click" and c.mutates


def test_a_read_satisfied_on_paper_but_not_at_runtime_is_caught():
    """The drift case the static check cannot see.

    `graph.check` is satisfied because the upstream node *declares* the write.
    Only running it reveals that the declaration is no longer true — and the
    node downstream is the one that would otherwise get blamed.
    """
    class Promises(Node):
        kind = "promises_thing"
        writes = ("thing",)
        needs_browser = False

        def run(self, ctx):
            return ctx                # the declaration has drifted

    class NeedsThing(Node):
        kind = "needs_thing_runtime"
        reads = ("thing",)
        needs_browser = False

        def run(self, ctx):
            return ctx

    g = (Graph("drift").add(Checked(Promises(), strict=False))
         .add(Checked(NeedsThing())))
    assert g.check(Spec(engine=Engine.MOCK)) == [], "static check should pass"
    with pytest.raises(ContractViolation, match="available:"):
        run(g, Spec(engine=Engine.MOCK), mock())
