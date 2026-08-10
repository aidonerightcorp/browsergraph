"""Ollama wiring: the model you get when you do not name one.

The default `LLMConfig` used to name a specific model, which gave the worst
possible first experience — a bare `HTTP Error 404: Not Found` against a
perfectly healthy Ollama that simply had different models pulled. A default that
only works on the machine it was written on is not a default.

The live tests skip when no Ollama is reachable, so the suite still runs
anywhere; the resolution logic itself is tested against a stub.
"""
from __future__ import annotations

import pytest

from browsergraph.dimensions import LLMConfig, LLMControl
from browsergraph.models import Catalog, ModelInfo, ModelUnavailable
from browsergraph.nodes.llm import OllamaClient, resolve_model


def reachable() -> bool:
    cat = Catalog.load(LLMConfig().host)
    return bool(cat.reachable and cat.models)


needs_ollama = pytest.mark.skipif(not reachable(), reason="no Ollama reachable")


@pytest.fixture(autouse=True)
def _clear_cache():
    from browsergraph.nodes import llm
    llm._CATALOGS.clear()
    yield
    llm._CATALOGS.clear()


def stub(monkeypatch, names_caps: list[tuple[str, tuple[str, ...]]], reach=True):
    cat = Catalog(host="http://stub:11434")
    cat.reachable = reach
    cat.models = [ModelInfo(name=n, capabilities=tuple(c)) for n, c in names_caps]
    monkeypatch.setattr(Catalog, "load", staticmethod(lambda *a, **k: cat))
    return cat


# --- resolution -------------------------------------------------------------

def test_an_unnamed_model_is_chosen_from_the_host(monkeypatch):
    stub(monkeypatch, [("llama3:8b", ("completion",)),
                       ("qwen-vl:7b", ("completion", "vision"))])
    assert resolve_model(LLMConfig()) in ("llama3:8b", "qwen-vl:7b")


def test_a_bare_name_matches_its_tag(monkeypatch):
    """Ollama names models `name:tag`; people write the bare name constantly.

    Failing that with a 404 is technically correct and useless — it was the
    exact shape of the original bug (`glm-5.2` vs `glm-5.2:cloud`).
    """
    stub(monkeypatch, [("glm-5.2:cloud", ("completion",))])
    assert resolve_model(LLMConfig(model="glm-5.2")) == "glm-5.2:cloud"


def test_an_exact_name_is_used_unchanged(monkeypatch):
    stub(monkeypatch, [("glm-5.2:cloud", ("completion",)),
                       ("glm-5.2:local", ("completion",))])
    assert resolve_model(LLMConfig(model="glm-5.2:local")) == "glm-5.2:local"


def test_an_ambiguous_bare_name_resolves_deterministically(monkeypatch):
    stub(monkeypatch, [("glm-5.2:b", ("completion",)), ("glm-5.2:a", ("completion",))])
    assert resolve_model(LLMConfig(model="glm-5.2")) == "glm-5.2:a"


def test_a_vision_job_never_gets_a_text_only_model(monkeypatch):
    """A text model handed an image answers from the prompt, fluently and wrongly."""
    stub(monkeypatch, [("text-only:1", ("completion",)),
                       ("seer:1", ("completion", "vision"))])
    assert resolve_model(LLMConfig(), capability="vision") == "seer:1"


def test_no_qualifying_model_is_an_explicit_refusal(monkeypatch):
    stub(monkeypatch, [("text-only:1", ("completion",))])
    with pytest.raises(ModelUnavailable, match="vision"):
        resolve_model(LLMConfig(), capability="vision")


def test_an_unknown_model_lists_what_is_available(monkeypatch):
    stub(monkeypatch, [("llama3:8b", ("completion",))])
    with pytest.raises(ModelUnavailable) as e:
        resolve_model(LLMConfig(model="gpt-9"))
    assert "llama3:8b" in str(e.value)
    assert "empty" in str(e.value), "should say how to get automatic selection"


def test_an_unreachable_host_is_not_disguised_as_a_model_problem(monkeypatch):
    """A connection failure must surface as one, not as 'no such model'."""
    stub(monkeypatch, [], reach=False)
    assert resolve_model(LLMConfig(model="whatever")) == "whatever"


def test_the_catalogue_is_fetched_once_per_host(monkeypatch):
    calls = []
    cat = Catalog(host="http://stub:11434")
    cat.reachable = True
    cat.models = [ModelInfo(name="m:1", capabilities=("completion",))]

    def load(*a, **k):
        calls.append(a)
        return cat

    monkeypatch.setattr(Catalog, "load", staticmethod(load))
    for _ in range(4):
        resolve_model(LLMConfig())
    assert len(calls) == 1, "every LLM node would pay a round trip"


# --- configuration ----------------------------------------------------------

def test_the_default_names_no_model():
    assert LLMConfig().model == ""


def test_from_env_reads_the_standard_variables(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "ollama.internal:11434")
    monkeypatch.setenv("OLLAMA_API_KEY", "secret")
    cfg = LLMConfig.from_env()
    assert cfg.host == "http://ollama.internal:11434", "bare host:port needs a scheme"
    assert cfg.api_key == "secret"


def test_from_env_keeps_an_explicit_scheme(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "https://ollama.com")
    assert LLMConfig.from_env().host == "https://ollama.com"


def test_from_env_falls_back_to_the_default(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    assert LLMConfig.from_env().host == "http://localhost:11434"


def test_overrides_beat_the_environment(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://from-env:11434")
    assert LLMConfig.from_env(host="http://explicit:1").host == "http://explicit:1"


# --- live -------------------------------------------------------------------

@needs_ollama
def test_the_default_config_actually_completes():
    """The regression: this used to 404 on a perfectly healthy Ollama."""
    out = OllamaClient(LLMConfig(mode=LLMControl.VERIFY)).complete(
        "Reply with exactly: OK")
    assert "ok" in out.lower()


@needs_ollama
def test_a_bare_name_works_against_the_real_host():
    cat = Catalog.load(LLMConfig().host)
    bare = cat.models[0].name.split(":", 1)[0]
    assert resolve_model(LLMConfig(model=bare)) in [m.name for m in cat.models]


@needs_ollama
def test_capabilities_come_from_the_host_not_a_guess():
    cat = Catalog.load(LLMConfig().host)
    assert all(m.capabilities for m in cat.models), "a model with no capabilities"
    assert any("completion" in m.capabilities for m in cat.models)
