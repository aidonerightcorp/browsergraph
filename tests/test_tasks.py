import io
import json
import logging as _logging

import pytest

from browsergraph.classify.naics import SECTORS, refine_with_llm
from browsergraph.classify.naics import classify as naics_classify
from browsergraph.crawl import Crawler, CrawlLimits
from browsergraph.extract.content import looks_like_article, parse_page, text_of
from browsergraph.extract.links import Frontier, Robots, links_from_html, normalize, same_site
from browsergraph.extract.patterns import addresses, emails, extract_contacts, phones, socials
from browsergraph.logging import JsonFormatter, configure, new_run, redact
from browsergraph.tasks import catalog, make

# --- a small fake site ------------------------------------------------------

HOME = """<html lang="en"><head><title>Acme Roofing | Contractors</title>
<meta name="description" content="Roofing contractor in Denver"></head><body>
<h1>Acme Roofing</h1>
<p>We are a general contractor specialising in roofing and gutter installation.
Call us on (303) 555-0142 or email info@acmeroofing.example.</p>
<p>1234 Elm Street, Suite 200, Denver, CO 80202</p>
<a href="/about">About</a><a href="/contact">Contact</a><a href="/news/roof-tips">Tips</a>
<a href="https://www.linkedin.com/company/acme">LinkedIn</a>
<a href="/about?utm_source=nav">About again</a>
</body></html>"""

CONTACT = """<html><head><title>Contact</title></head><body>
<p>Sales: sales@acmeroofing.example, +1 303 555 0199 ext 12</p>
<p>Head office: 900 Broadway Ave, Denver, CO 80203</p>
<p>Do not use noreply@acmeroofing.example</p></body></html>"""

ARTICLE = """<html><head><title>Roof Tips</title>
<meta property="article:published_time" content="2026-03-04T10:00:00Z"></head><body>
<h1>Five roof maintenance tips</h1>
<p>%s</p></body></html>""" % ("Maintaining a roof requires attention. " * 40)

SITE = {
    "https://acme.example": HOME,
    "https://acme.example/about": "<html><title>About</title><body><p>Founded 1994. We are a roofing contractor.</p></body></html>",
    "https://acme.example/contact": CONTACT,
    "https://acme.example/news/roof-tips": ARTICLE,
    "https://acme.example/robots.txt": "User-agent: *\nDisallow: /private\nCrawl-delay: 0",
}


class FakeBrowser:
    """BrowserPort over a dict of url -> html."""

    def __init__(self, pages=None):
        self.pages = pages or SITE
        self._url = ""
        self.visited = []

    def start(self): pass
    def stop(self): pass

    def goto(self, url):
        from browsergraph.ports import PageState
        self._url = url
        self.visited.append(url)
        return PageState(url=url, title="", status=200)

    def state(self):
        from browsergraph.ports import PageState
        return PageState(url=self._url)

    def html(self): return self.pages.get(self._url, "<html><body>404</body></html>")
    def find(self, s): return None
    def click(self, s): pass
    def type(self, s, t, cps=0.0): pass
    def scroll(self, dy): pass
    def wait_for(self, s, timeout=10.0): return False
    def text_of(self, s): return ""
    def screenshot(self, p): return p
    def eval_js(self, s): return None


def no_sleep(_): pass


# --- extractors -------------------------------------------------------------

def test_emails_found_and_noise_rejected():
    found = emails(text_of(CONTACT))
    assert "sales@acmeroofing.example" in found
    assert not any("noreply" in e for e in found)


def test_email_rejects_asset_filenames():
    assert emails("logo@2x.png and sprite@3x.jpg") == []


def test_phones_parsed_with_country_and_extension():
    got = phones("Call (303) 555-0142 or +1 303 555 0199 ext 12")
    assert len(got) == 2
    assert any(p.country == "+1" and p.ext == "12" for p in got)


@pytest.mark.parametrize("text", [
    "Order 2026-03-04 shipped",       # a date
    "SKU 000000000",                  # repeated digits
    "id 12345",                       # too short
    # Found by running against the live web rather than fixtures: python.org's
    # homepage prints a Fibonacci series, and "... 233 377 610 987" was being
    # reported as the phone number 377 610 987. Any page with a numeric table,
    # a code sample or a list of statistics has the same shape.
    "0 1 1 2 3 5 8 13 21 34 55 89 144 233 377 610 987",
    "Q1 120 340 990 Q2 150 360 995",  # a stats table
    "checksum 377 610 987",           # nine bare digits are not dialable
])
def test_phone_false_positives_rejected(text):
    assert phones(text) == []


@pytest.mark.parametrize("text,expected", [
    ("call (303) 555-0142 today", "(303) 555-0142"),
    ("call 303 555 0142 today", "303 555 0142"),
    ("tel. 303.555.0142", "303.555.0142"),
    ("reach us on +44 20 7946 0958", "+44 20 7946 0958"),
])
def test_real_phone_numbers_still_extracted(text, expected):
    """The rejection rules must not be paid for with misses."""
    assert [p.raw for p in phones(text)] == [expected]


def test_addresses_with_state_and_zip():
    got = addresses("Head office: 900 Broadway Ave, Denver, CO 80203")
    assert got and got[0].postcode == "80203" and got[0].state == "CO"


def test_uk_postcode_detected():
    got = addresses("Our office: 12 High Street, London SW1A 1AA")
    assert any(a.country_hint == "UK" for a in got)


def test_socials_ignores_share_links():
    assert socials(["https://www.linkedin.com/company/acme"])["linkedin"]
    assert socials(["https://twitter.com/intent/tweet?url=x"]) == {}


def test_contacts_merge_is_deduplicated():
    a = extract_contacts("a@x.example", [])
    b = extract_contacts("a@x.example b@x.example", [])
    assert a.merge(b).emails == ["a@x.example", "b@x.example"]


# --- links / frontier -------------------------------------------------------

def test_normalize_strips_tracking_fragment_and_slash():
    assert (normalize("https://WWW.Acme.example/about/?utm_source=x#team")
            == "https://acme.example/about")


def test_same_site_handles_subdomains():
    assert same_site("https://a.acme.example/x", "https://acme.example/")
    assert not same_site("https://other.example/x", "https://acme.example/")


def test_links_skip_assets_and_mailto():
    links = links_from_html(HOME, "https://acme.example")
    assert "https://acme.example/about" in links
    assert not any(l.endswith((".png", ".css")) for l in links)


def test_frontier_dedups_and_bounds():
    f = Frontier(seed="https://acme.example", max_pages=2, max_depth=1)
    f.extend(["https://acme.example/a", "https://acme.example/a",
              "https://other.example/b"], 1)
    assert f.pop() and f.pop()
    assert f.pop() is None                      # max_pages reached
    assert f.report()["skipped"]["offsite"] == 1
    assert f.report()["skipped"]["duplicate"] == 1


def test_robots_longest_match_wins():
    r = Robots.parse("User-agent: *\nDisallow: /a\nAllow: /a/public")
    assert not r.allowed("https://x.example/a/secret")
    assert r.allowed("https://x.example/a/public/page")


# --- content ----------------------------------------------------------------

def test_parse_page_pulls_title_meta_and_links():
    p = parse_page(HOME, "https://acme.example")
    assert p.title.startswith("Acme Roofing")
    assert p.description == "Roofing contractor in Denver"
    assert p.lang == "en" and p.links


def test_article_detection_requires_substance_and_signal():
    assert looks_like_article(parse_page(ARTICLE, "https://acme.example/news/roof-tips"))
    assert not looks_like_article(parse_page(HOME, "https://acme.example"))


# --- naics ------------------------------------------------------------------

def test_construction_classified_confidently():
    c = naics_classify(text_of(HOME), title="Acme Roofing", url="https://acme.example")
    assert c.code == "23" and c.sector == SECTORS["23"] and c.usable


def test_weak_evidence_is_not_usable():
    c = naics_classify("Welcome to our website. Hello.", title="Home")
    assert not c.usable and c.confidence in ("none", "low")


def test_repeated_keyword_does_not_dominate():
    """Unweighted counts would let a nav menu decide the classification."""
    once = naics_classify("we do construction")
    spammed = naics_classify("construction " * 200)
    assert spammed.score < once.score * 6


def test_llm_refinement_only_when_weak_and_stays_in_vocabulary():
    class LLM:
        calls = 0
        def __init__(self, reply): self.reply = reply
        def complete(self, *a, **k):
            LLM.calls += 1
            return self.reply

    strong = naics_classify(text_of(HOME), title="Acme Roofing")
    assert refine_with_llm(strong, "", LLM("62")).code == "23"   # untouched
    assert LLM.calls == 0

    weak = naics_classify("hello world")
    assert refine_with_llm(weak, "x", LLM("nonsense-code")).code == weak.code


# --- crawler ----------------------------------------------------------------

def test_crawler_respects_limits_and_reports():
    c = Crawler(FakeBrowser(), "https://acme.example",
                CrawlLimits(max_pages=3, delay=0), sleep=no_sleep)
    pages = [p for p, _ in c.pages()]
    assert 1 <= len(pages) <= 3
    assert c.report()["fetched"] == len(pages)


def test_crawler_stops_on_challenge():
    pages = {"https://x.example": "<html><body>Please complete the CAPTCHA</body></html>"}
    c = Crawler(FakeBrowser(pages), "https://x.example",
                CrawlLimits(delay=0), sleep=no_sleep)
    assert [p for p, _ in c.pages()] == []
    assert "challenge" in c.stats.stopped_reason


def test_crawler_honours_robots_disallow():
    pages = dict(SITE)
    pages["https://acme.example/robots.txt"] = "User-agent: *\nDisallow: /"
    c = Crawler(FakeBrowser(pages), "https://acme.example",
                CrawlLimits(delay=0), sleep=no_sleep)
    assert [p for p, _ in c.pages()] == []
    assert c.stats.robots_blocked >= 1


# --- tasks ------------------------------------------------------------------

def test_catalog_lists_every_task():
    names = {t["name"] for t in catalog()}
    assert names == {"spider", "contacts", "news", "research", "naics", "public_data"}
    assert all(t["params"] for t in catalog())


def test_task_rejects_bad_params_before_running():
    from browsergraph.params import ParamError
    with pytest.raises(ParamError):
        make("spider", url="not-a-url")
    with pytest.raises(ParamError, match="missing required"):
        make("spider")


def test_spider_maps_the_site():
    r = make("spider", url="https://acme.example", delay=0, max_pages=5).run(FakeBrowser())
    assert r.ok and r.data["sitemap"]
    assert all(u.startswith("https://acme.example") for u in r.data["sitemap"])


def test_contacts_task_aggregates_across_pages():
    r = make("contacts", url="https://acme.example", delay=0, max_pages=5).run(FakeBrowser())
    assert r.ok
    got = r.data["contacts"]
    assert "info@acmeroofing.example" in got["emails"]
    assert "sales@acmeroofing.example" in got["emails"]
    assert got["phones"] and got["addresses"]
    assert got["socials"].get("linkedin")


def test_news_task_finds_articles_and_skips_pages():
    r = make("news", url="https://acme.example", delay=0, max_pages=6).run(FakeBrowser())
    assert r.ok and r.data["count"] == 1
    assert r.data["articles"][0]["published"] == "2026-03-04"
    assert r.stats["skipped_non_article"] >= 1


def test_news_since_filter():
    r = make("news", url="https://acme.example", delay=0, max_pages=6,
             since="2027-01-01").run(FakeBrowser())
    assert r.data["count"] == 0


def test_research_task_profiles_the_business():
    r = make("research", url="https://acme.example", delay=0, max_pages=5).run(FakeBrowser())
    assert r.ok
    assert r.data["identity"]["name"].startswith("Acme")
    assert r.data["naics"]["code"] == "23"
    assert r.data["contacts"]["emails"]


def test_naics_task_reports_unusable_honestly():
    blank = {"https://x.example": "<html><title>Hi</title><body>Hello.</body></html>"}
    r = make("naics", url="https://x.example", delay=0).run(FakeBrowser(blank))
    assert not r.ok
    assert not r.data["naics"]["usable"]
    assert any("confidence" in w for w in r.warnings)


def test_public_data_single_pass():
    r = make("public_data", url="https://acme.example", delay=0, max_pages=6).run(FakeBrowser())
    assert r.ok
    assert r.data["contacts"]["emails"] and r.data["articles"]
    assert r.data["naics"]["code"] == "23"


def test_task_result_serialises():
    r = make("spider", url="https://acme.example", delay=0, max_pages=2).run(FakeBrowser())
    assert json.loads(json.dumps(r.to_dict()))["task"] == "spider"


def test_task_failure_is_captured_not_raised():
    class Broken(FakeBrowser):
        def goto(self, url): raise RuntimeError("boom")
    r = make("spider", url="https://acme.example", delay=0).run(Broken())
    assert not r.ok and r.error


# --- logging ----------------------------------------------------------------

def test_json_log_line_is_parseable_and_carries_run_id():
    stream = io.StringIO()
    configure(level="INFO", stream=stream)
    rid = new_run("test")
    _logging.getLogger("bg").info("hello", extra={"fields": {"url": "https://x"}})
    line = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert line["msg"] == "hello" and line["level"] == "info"
    assert line["run_id"] == rid and line["url"] == "https://x"


def test_secrets_redacted_by_the_formatter():
    assert redact({"password": "hunter2"})["password"] == "<redacted>"
    assert redact({"api_key": "x"})["api_key"] == "<redacted>"
    assert "<redacted>" in redact("Authorization: Bearer abcdefghijklmno")
    assert redact({"nested": [{"token": "t"}]})["nested"][0]["token"] == "<redacted>"


def test_email_redaction_is_opt_in():
    assert redact("a@b.example") == "a@b.example"
    assert redact("a@b.example", redact_emails=True) == "<redacted>"


def test_formatter_survives_unserialisable_fields():
    rec = _logging.LogRecord("n", _logging.INFO, "p", 1, "m", None, None)
    rec.fields = {"obj": object()}
    assert json.loads(JsonFormatter().format(rec))["msg"] == "m"
