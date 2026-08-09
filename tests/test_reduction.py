"""Preprocessing, token focus, model selection and vision — the reduction layer."""
import json

import pytest

from browsergraph.dimensions import Preprocess as PreDim
from browsergraph.dimensions import Spec, validate
from browsergraph.dimensions import Vision as VisionDim
from browsergraph.focus import (
    Chunk,
    chunk,
    estimate_tokens,
    focus,
    score,
    select,
    strip_boilerplate,
    tokenize,
)
from browsergraph.models import VISION, Catalog, ModelInfo, ModelUnavailable
from browsergraph.preprocess import Preprocess, compare, recommend, reduce
from browsergraph.vision import VisionLocate, VisionVerify, selector_for

BIG_PAGE = """<!doctype html><html lang="en"><head>
<title>Acme Corp | Contact</title>
<meta name="description" content="Get in touch">
<style>.a{color:red}.b{color:blue}</style>
<script>var x = {big: "%s"};</script>
</head><body>
<nav><a href="/">Home</a><a href="/about">About</a><a href="/contact">Contact</a></nav>
<main>
<h1>Contact Acme</h1>
<p>%s</p>
<h2>Sales enquiries</h2>
<p>Call sales on (303) 555-0142 or email sales@acme.example for pricing.</p>
<h2>Support</h2>
<p>%s</p>
<form id="contact-form">
  <label for="email">Email</label>
  <input id="email" name="email" type="email" placeholder="you@example.com">
  <button id="submit-btn" data-testid="send">Send message</button>
</form>
</main>
<footer><p>(c) Acme. Privacy. Terms.</p></footer>
</body></html>""" % ("x" * 4000, "Filler about the company. " * 60,
                     "Support hours are nine to five. " * 60)


# --- preprocessing ----------------------------------------------------------

def test_every_strategy_produces_output():
    for s in Preprocess:
        assert reduce(BIG_PAGE, s).content, f"{s} produced nothing"


def test_reduction_actually_reduces():
    raw = reduce(BIG_PAGE, Preprocess.RAW)
    for s in (Preprocess.TEXT, Preprocess.MARKDOWN, Preprocess.INTERACTIVE,
              Preprocess.ACCESSIBILITY, Preprocess.DOM_SKELETON):
        r = reduce(BIG_PAGE, s)
        assert r.chars < raw.chars, f"{s} did not reduce"
        assert r.saved_pct > 20, f"{s} saved only {r.saved_pct:.0f}%"


def test_scripts_and_styles_are_dropped():
    for s in (Preprocess.TEXT, Preprocess.CLEAN_HTML, Preprocess.MARKDOWN):
        content = reduce(BIG_PAGE, s).content
        assert "xxxx" not in content, f"{s} leaked script contents"
        assert "color:red" not in content


def test_interactive_yields_usable_selectors():
    content = reduce(BIG_PAGE, Preprocess.INTERACTIVE).content
    assert "#submit-btn" in content or '[data-testid="send"]' in content
    assert "selector=" in content and "label=" in content


def test_markdown_keeps_headings_and_links():
    md = reduce(BIG_PAGE, Preprocess.MARKDOWN).content
    assert "# Contact Acme" in md
    assert "## Sales enquiries" in md
    assert "[Contact](/contact)" in md


def test_accessibility_gives_role_name_pairs():
    acc = reduce(BIG_PAGE, Preprocess.ACCESSIBILITY).content
    assert "heading: Contact Acme" in acc
    assert any(line.startswith("link:") for line in acc.splitlines())


def test_readability_drops_nav_and_footer():
    text = reduce(BIG_PAGE, Preprocess.READABILITY).content
    assert "Contact Acme" in text
    assert "Privacy. Terms." not in text


def test_dom_skeleton_has_structure_without_prose():
    sk = reduce(BIG_PAGE, Preprocess.DOM_SKELETON).content
    assert "<form" in sk and "id=\"contact-form\"" in sk
    assert "Filler about the company" not in sk


def test_truncation_is_marked_not_silent():
    r = reduce(BIG_PAGE, Preprocess.TEXT, max_chars=200)
    assert r.chars <= 260 and "truncated" in r.content


def test_recommend_matches_question_to_strategy():
    assert Preprocess.INTERACTIVE in recommend("selector")
    assert Preprocess.READABILITY in recommend("classify")


def test_compare_ranks_strategies_by_size():
    results = sorted(compare(BIG_PAGE), key=lambda r: r.chars)
    assert results[0].chars < results[-1].chars
    assert all(json.dumps(r.to_dict()) for r in results)


# --- focus / chunking -------------------------------------------------------

def test_chunking_splits_on_headings():
    md = reduce(BIG_PAGE, Preprocess.MARKDOWN).content
    chunks = chunk(md, max_chars=400)
    assert len(chunks) > 2
    assert any("Sales" in c.heading for c in chunks)


def test_chunks_respect_size_limit():
    chunks = chunk("word " * 5000, max_chars=500)
    assert chunks and all(c.chars <= 700 for c in chunks)


def test_scoring_ranks_the_relevant_chunk_first():
    md = reduce(BIG_PAGE, Preprocess.MARKDOWN).content
    ranked = sorted(score(chunk(md, max_chars=400), "sales pricing email"),
                    key=lambda c: c.score, reverse=True)
    assert "sales@acme.example" in ranked[0].text or "Sales" in ranked[0].heading


def test_stopwords_do_not_drive_scoring():
    assert "the" not in tokenize("the quick brown fox")


def test_focus_reduces_and_keeps_the_answer():
    md = reduce(BIG_PAGE, Preprocess.MARKDOWN).content
    f = focus(md, "sales email address", budget=800)
    assert "sales@acme.example" in f.content
    assert f.chars < f.original_chars and f.saved_pct > 30


def test_neighbour_expansion_included():
    """The match and the answer are often in adjacent blocks."""
    chunks = [Chunk(0, "Contact us"), Chunk(1, "Phone: 303-555-0142"),
              Chunk(2, "Unrelated")]
    picked = select(chunks, "contact", budget=500, neighbors=1)
    assert 1 in picked.kept, "neighbour dropped; the answer would be missed"


def test_gaps_are_marked_not_hidden():
    chunks = [Chunk(i, f"block {i} " + ("filler " * 20)) for i in range(8)]
    chunks[0].text += " needle"
    chunks[7].text += " needle"
    picked = select(chunks, "needle", budget=400, neighbors=0)
    assert "…" in picked.content


def test_empty_query_falls_back_to_head_not_nothing():
    chunks = chunk("a b c. " * 500, max_chars=200)
    picked = select(chunks, "", budget=300)
    assert picked.content


def test_boilerplate_removed_across_pages():
    nav = "Home | About | Contact"
    pages = [f"{nav}\n\nUnique {i} content here" for i in range(4)]
    cleaned = strip_boilerplate(pages)
    assert all(nav not in p for p in cleaned)
    assert all(f"Unique {i}" in cleaned[i] for i in range(4))


def test_boilerplate_not_stripped_from_too_few_pages():
    pages = ["nav\n\nbody one", "nav\n\nbody two"]
    assert strip_boilerplate(pages) == pages


def test_token_estimate_tracks_length():
    assert estimate_tokens("x" * 400) == 100


def test_full_pipeline_saves_most_of_the_page():
    """raw HTML -> markdown -> focused answer."""
    raw = len(BIG_PAGE)
    md = reduce(BIG_PAGE, Preprocess.MARKDOWN).content
    f = focus(md, "how do I contact sales", budget=600)
    assert f.chars < raw * 0.15, f"only got to {100*f.chars/raw:.0f}% of raw"
    assert "sales@acme.example" in f.content


# --- model selection --------------------------------------------------------

def fake_catalog():
    return Catalog(host="http://x", reachable=True, models=[
        ModelInfo("glm-5.2:cloud", ("completion", "tools", "thinking")),
        ModelInfo("kimi-k2.7-code:cloud", ("vision", "completion", "tools", "thinking")),
        ModelInfo("deepseek-v4-flash:0731-cloud", ("completion", "tools")),
    ])


def test_best_vision_model_is_the_one_with_the_capability():
    assert fake_catalog().best(VISION).name.startswith("kimi")


def test_preference_order_applied_for_general_completion():
    assert fake_catalog().best("completion").name.startswith("glm-5")


def test_missing_capability_raises_with_guidance():
    cat = Catalog(host="http://x", reachable=True,
                  models=[ModelInfo("text-only", ("completion",))])
    with pytest.raises(ModelUnavailable, match="vision"):
        cat.best(VISION)


def test_requested_model_without_capability_is_refused():
    """Silently substituting would make the run untraceable."""
    with pytest.raises(ModelUnavailable, match="does not support"):
        fake_catalog().choose(VISION, requested="glm-5.2:cloud")


def test_unknown_requested_model_lists_alternatives():
    with pytest.raises(ModelUnavailable, match="not on"):
        fake_catalog().choose("completion", requested="nope")


def test_role_hint_breaks_ties():
    assert fake_catalog().best("completion", role="code").name.startswith("kimi")
    assert fake_catalog().best("completion", role="fast").name.startswith("deepseek")


def test_report_is_readable_and_serialises():
    cat = fake_catalog()
    assert "best[vision]" in cat.report()
    assert json.loads(json.dumps(cat.to_dict()))["reachable"]


def test_unreachable_catalog_reports_rather_than_raises():
    cat = Catalog(host="http://127.0.0.1:1", api_key="")
    loaded = Catalog.load("http://127.0.0.1:1")
    assert not loaded.reachable and "unreachable" in loaded.report()


# --- vision -----------------------------------------------------------------

class FakeVisionBrowser:
    def __init__(self, marks=None, has=()):
        self.marks, self.has, self.shots = marks or [], set(has), []
    def find(self, s): return object() if s in self.has else None
    def eval_js(self, s): return json.dumps(self.marks)
    def screenshot(self, p): self.shots.append(p); return p


class FakeVisionClient:
    def __init__(self, reply): self.reply, self.calls = reply, 0
    def ask(self, prompt, image_path, system=""):
        self.calls += 1
        return self.reply


def ctx_with(browser):
    from browsergraph.ports import Context
    return Context(browser=browser)


def test_vision_skipped_when_dom_selector_works():
    """Vision is the expensive fallback, never the default path."""
    client = FakeVisionClient("1")
    node = VisionLocate("the send button", fallback="#submit-btn", client=client)
    ctx = node.run(ctx_with(FakeVisionBrowser(has=["#submit-btn"])))
    assert ctx.data["selector"] == "#submit-btn"
    assert client.calls == 0 and not ctx.failed


def test_vision_resolves_a_numbered_mark_to_a_selector():
    marks = [{"n": 1, "tag": "a", "id": "", "testid": "", "name": "", "text": "Home"},
             {"n": 2, "tag": "button", "id": "submit-btn", "testid": "", "name": "",
              "text": "Send message"}]
    node = VisionLocate("the send button", client=FakeVisionClient("2"))
    ctx = node.run(ctx_with(FakeVisionBrowser(marks=marks)))
    assert ctx.data["selector"] == "#submit-btn"
    assert ctx.data["vision_used"]


def test_vision_failure_is_explicit():
    node = VisionLocate("something", client=FakeVisionClient("no idea"))
    ctx = node.run(ctx_with(FakeVisionBrowser(marks=[])))
    assert ctx.failed and "could not resolve" in ctx.error


def test_vision_unreachable_model_reports_clearly():
    class Broken:
        def ask(self, *a, **k): raise OSError("connection refused")
    ctx = VisionLocate("x", client=Broken()).run(ctx_with(FakeVisionBrowser()))
    assert ctx.failed and "unreachable" in ctx.error


def test_vision_verify_parses_json():
    ok = VisionVerify("the form was submitted",
                      client=FakeVisionClient('{"ok": true, "why": "banner"}')
                      ).run(ctx_with(FakeVisionBrowser()))
    assert ok.data["verified"] and not ok.failed
    bad = VisionVerify("x", client=FakeVisionClient('{"ok": false, "why": "no"}')
                       ).run(ctx_with(FakeVisionBrowser()))
    assert bad.failed


def test_selector_for_prefers_stable_attributes():
    assert selector_for({"id": "a", "testid": "b", "tag": "button"}) == "#a"
    assert selector_for({"testid": "b", "tag": "button"}) == '[data-testid="b"]'
    assert selector_for({"tag": "button"}) == "button"


# --- dimension wiring -------------------------------------------------------

def test_spec_carries_new_axes_and_describes_them():
    s = Spec(preprocess=PreDim.MARKDOWN, vision=VisionDim.ON_FAILURE)
    assert "pre=markdown" in s.describe() and "vision=on_failure" in s.describe()


def test_vision_requires_a_model_host():
    from browsergraph.dimensions import LLMConfig
    bad = Spec(vision=VisionDim.ALWAYS, llm=LLMConfig(host=""))
    assert any("multimodal" in p for p in validate(bad))
    assert validate(Spec(vision=VisionDim.ALWAYS)) == []


def test_new_axes_participate_in_enumeration():
    from browsergraph.combos import enumerate_specs
    specs = list(enumerate_specs({"preprocess": list(PreDim)}, base=Spec()))
    assert len(specs) == len(PreDim)
