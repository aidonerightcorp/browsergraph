"""A corpus of 100 browser-oriented tasks.

Each scenario is a small, self-contained page plus an expectation. They run
against a **real chromium** over `file://`, not against mocks, so the drivers,
extractors, preprocessing and task layer are all exercised together.

Categories are deliberately uneven — extraction and failure handling carry more
weight than navigation, because that is where silent wrongness lives.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

HTML_HEAD = "<!doctype html><html lang=en><head><meta charset=utf-8>"


@dataclass
class Scenario:
    id: str
    category: str
    title: str
    html: str
    kind: str = "extract"          # extract | interact | task | classify | reduce | fail
    check: Callable[[dict], bool] = lambda r: True
    why: str = ""                  # what a failure would mean
    params: dict[str, Any] = field(default_factory=dict)


def page(body: str, title: str = "Test", head: str = "") -> str:
    return f"{HTML_HEAD}<title>{title}</title>{head}</head><body>{body}</body></html>"


S: list[Scenario] = []


def add(id, category, title, html, kind="extract", check=lambda r: True, why="", **params):
    S.append(Scenario(id, category, title, html, kind, check, why, params))


# ---------------------------------------------------------------- contacts (20)
add("c01", "contacts", "plain email", page("<p>Write to hello@acme.example</p>"),
    check=lambda r: "hello@acme.example" in r["contacts"]["emails"])
add("c02", "contacts", "mailto link",
    page('<a href="mailto:sales@acme.example">Sales</a>'),
    check=lambda r: "sales@acme.example" in r["contacts"]["emails"])
add("c03", "contacts", "obfuscated email is not invented",
    page("<p>hello [at] acme [dot] example</p>"),
    check=lambda r: r["contacts"]["emails"] == [],
    why="inventing an address from obfuscated text would poison a dataset")
add("c04", "contacts", "noreply excluded",
    page("<p>noreply@acme.example and real@acme.example</p>"),
    check=lambda r: r["contacts"]["emails"] == ["real@acme.example"])
add("c05", "contacts", "image filename not an email",
    page("<p>logo@2x.png sprite@3x.jpg</p>"),
    check=lambda r: r["contacts"]["emails"] == [])
add("c06", "contacts", "us phone bracketed", page("<p>Call (303) 555-0142</p>"),
    check=lambda r: any(p["digits"].endswith("5550142") for p in r["contacts"]["phones"]))
add("c07", "contacts", "international phone", page("<p>+44 20 7946 0958</p>"),
    check=lambda r: any(p["country"] == "+44" for p in r["contacts"]["phones"]))
add("c08", "contacts", "phone with extension", page("<p>+1 303 555 0199 ext 12</p>"),
    check=lambda r: any(p["ext"] == "12" for p in r["contacts"]["phones"]))
add("c09", "contacts", "date is not a phone", page("<p>Published 2026-03-04</p>"),
    check=lambda r: r["contacts"]["phones"] == [],
    why="dates parsed as phones are the classic silent corruption")
add("c10", "contacts", "order id is not a phone", page("<p>Order 000000000</p>"),
    check=lambda r: r["contacts"]["phones"] == [])
add("c11", "contacts", "us address with zip",
    page("<p>900 Broadway Ave, Denver, CO 80203</p>"),
    check=lambda r: any(a["postcode"] == "80203" for a in r["contacts"]["addresses"]))
add("c12", "contacts", "address with suite",
    page("<p>1234 Elm Street, Suite 200, Denver, CO 80202</p>"),
    check=lambda r: bool(r["contacts"]["addresses"]))
add("c13", "contacts", "uk postcode",
    page("<p>12 High Street, London SW1A 1AA</p>"),
    check=lambda r: any(a["country_hint"] == "UK" for a in r["contacts"]["addresses"]))
add("c14", "contacts", "linkedin profile",
    page('<a href="https://www.linkedin.com/company/acme">in</a>'),
    check=lambda r: "linkedin" in r["contacts"]["socials"])
add("c15", "contacts", "share link is not a profile",
    page('<a href="https://twitter.com/intent/tweet?url=x">tweet</a>'),
    check=lambda r: "twitter" not in r["contacts"]["socials"])
add("c16", "contacts", "multiple socials",
    page('<a href="https://github.com/acme">gh</a>'
         '<a href="https://instagram.com/acme">ig</a>'),
    check=lambda r: len(r["contacts"]["socials"]) >= 2)
add("c17", "contacts", "vat id", page("<p>VAT GB123456789</p>"),
    check=lambda r: bool(r["contacts"]["tax_ids"]))
add("c18", "contacts", "emails deduplicated",
    page("<p>a@x.example a@x.example a@x.example</p>"),
    check=lambda r: r["contacts"]["emails"].count("a@x.example") == 1)
add("c19", "contacts", "contact details in footer",
    page("<main><p>Body</p></main><footer>hi@acme.example</footer>"),
    check=lambda r: "hi@acme.example" in r["contacts"]["emails"])
add("c20", "contacts", "empty page yields nothing, not noise", page("<p>Hello</p>"),
    check=lambda r: not r["contacts"]["emails"] and not r["contacts"]["phones"])

# ------------------------------------------------------------- extraction (15)
add("x01", "extract", "title", page("<h1>Heading</h1>", title="My Title"),
    kind="page", check=lambda r: r["title"] == "My Title")
add("x02", "extract", "meta description",
    page("<p>x</p>", head='<meta name=description content="the desc">'),
    kind="page", check=lambda r: r["description"] == "the desc")
add("x03", "extract", "og:description fallback",
    page("<p>x</p>", head='<meta property="og:description" content="og desc">'),
    kind="page", check=lambda r: r["description"] == "og desc")
add("x04", "extract", "lang attribute", page("<p>x</p>"),
    kind="page", check=lambda r: r["lang"] == "en")
add("x05", "extract", "headings collected",
    page("<h1>One</h1><h2>Two</h2><h3>Three</h3>"),
    kind="page", check=lambda r: len(r["headings"]) >= 3)
add("x06", "extract", "links normalised",
    page('<a href="/about/?utm_source=x#top">About</a>'),
    kind="page", check=lambda r: any(l.endswith("/about") for l in r["links"]))
add("x07", "extract", "asset links excluded",
    page('<a href="/x.png">img</a><a href="/real">real</a>'),
    kind="page", check=lambda r: not any(l.endswith(".png") for l in r["links"]))
add("x08", "extract", "json-ld parsed",
    page('<script type="application/ld+json">{"@type":"Organization","name":"Acme"}</script>'),
    kind="page", check=lambda r: any(d.get("name") == "Acme" for d in r["structured"]))
add("x09", "extract", "published date from meta",
    page("<p>x</p>", head='<meta property="article:published_time" content="2026-03-04T00:00:00Z">'),
    kind="page", check=lambda r: r["published"] == "2026-03-04")
add("x10", "extract", "date from prose", page("<p>Posted 4 March 2026 by us</p>"),
    kind="page", check=lambda r: r["published"] == "2026-03-04")
add("x11", "extract", "script contents excluded from text",
    page("<script>var secret='SHOULDNOTAPPEAR'</script><p>visible</p>"),
    kind="page", check=lambda r: "SHOULDNOTAPPEAR" not in r["text"])
add("x12", "extract", "style contents excluded",
    page("<style>.a{color:red}</style><p>visible</p>"),
    kind="page", check=lambda r: "color:red" not in r["text"])
add("x13", "extract", "entities unescaped", page("<p>Tom &amp; Jerry</p>"),
    kind="page", check=lambda r: "Tom & Jerry" in r["text"])
add("x14", "extract", "word count sane", page("<p>" + "word " * 50 + "</p>"),
    kind="page", check=lambda r: 45 <= r["word_count"] <= 60)
add("x15", "extract", "nested markup flattened",
    page("<div><div><div><p>deep <b>text</b></p></div></div></div>"),
    kind="page", check=lambda r: "deep text" in r["text"].replace("  ", " "))

# ------------------------------------------------------------ interaction (20)
_CLICK = """<button id=go>Go</button><div id=out></div>
<script>go.onclick=()=>out.textContent='clicked'</script>"""
add("i01", "interact", "click updates dom", page(_CLICK), kind="interact",
    check=lambda r: r.get("out") == "clicked", selector="#go", read="#out")
add("i02", "interact", "click by data-testid",
    page("""<button data-testid=send>Send</button><div id=out></div>
    <script>document.querySelector('[data-testid=send]').onclick=()=>out.textContent='sent'</script>"""),
    kind="interact", check=lambda r: r.get("out") == "sent",
    selector="[data-testid=send]", read="#out")
add("i03", "interact", "type into input",
    page("""<input id=q><div id=out></div>
    <script>q.oninput=e=>out.textContent=e.target.value</script>"""),
    kind="type", check=lambda r: r.get("out") == "hello", selector="#q",
    text="hello", read="#out")
add("i04", "interact", "wait for delayed element",
    page("""<div id=late></div>
    <script>setTimeout(()=>late.innerHTML='<span id=ready>ok</span>',150)</script>"""),
    kind="wait", check=lambda r: r.get("found") is True, selector="#ready")
add("i05", "interact", "wait times out on absent element", page("<p>x</p>"),
    kind="wait", check=lambda r: r.get("found") is False, selector="#never",
    why="a wait that reports success on a missing element makes every later step meaningless")
add("i06", "interact", "optional click on absent element", page("<p>x</p>"),
    kind="optional_click", check=lambda r: r.get("ok") is True, selector="#absent")
add("i07", "interact", "click hidden-then-shown",
    page("""<button id=b style=display:none>B</button><div id=out></div>
    <script>setTimeout(()=>b.style.display='block',100);b.onclick=()=>out.textContent='y'</script>"""),
    kind="interact", check=lambda r: r.get("out") == "y", selector="#b", read="#out")
add("i08", "interact", "scroll changes position",
    page("<div style='height:4000px'>tall</div>"), kind="scroll",
    check=lambda r: r.get("scrolled", 0) > 0)
add("i09", "interact", "form submit handled",
    page("""<form id=f onsubmit="event.preventDefault();out.textContent='submitted'">
    <input name=e><button id=s type=submit>go</button></form><div id=out></div>"""),
    kind="interact", check=lambda r: r.get("out") == "submitted", selector="#s", read="#out")
add("i10", "interact", "screenshot produced", page("<h1>shot</h1>"), kind="screenshot",
    check=lambda r: r.get("bytes", 0) > 1000)
for n, (sel, html) in enumerate([
    ("#a", "<a id=a href=#>x</a><div id=out></div><script>a.onclick=()=>out.textContent='ok'</script>"),
    ("button", "<button>x</button><div id=out></div><script>document.querySelector('button').onclick=()=>out.textContent='ok'</script>"),
    ("[role=button]", "<div role=button id=r>x</div><div id=out></div><script>r.onclick=()=>out.textContent='ok'</script>"),
    ("input[name=q]", "<input name=q><div id=out></div><script>document.querySelector('input').onclick=()=>out.textContent='ok'</script>"),
    (".btn", "<button class=btn>x</button><div id=out></div><script>document.querySelector('.btn').onclick=()=>out.textContent='ok'</script>"),
    ("#nested", "<div><span><button id=nested>x</button></span></div><div id=out></div><script>nested.onclick=()=>out.textContent='ok'</script>"),
    ("#uni", "<button id=uni>Ünïcödé</button><div id=out></div><script>uni.onclick=()=>out.textContent='ok'</script>"),
    ("#long", "<button id=long>" + "x" * 300 + "</button><div id=out></div><script>long.onclick=()=>out.textContent='ok'</script>"),
    ("#svgbtn", "<button id=svgbtn><svg width=10 height=10></svg>go</button><div id=out></div><script>svgbtn.onclick=()=>out.textContent='ok'</script>"),
    ("#dyn", "<div id=host></div><div id=out></div><script>host.innerHTML='<button id=dyn>d</button>';dyn.onclick=()=>out.textContent='ok'</script>"),
], start=11):
    add(f"i{n}", "interact", f"selector form {sel}", page(html), kind="interact",
        check=lambda r: r.get("out") == "ok", selector=sel, read="#out")

# ------------------------------------------------------------------ tasks (15)
_SITE_BODY = """<h1>Acme Roofing</h1>
<p>We are a general contractor specialising in roofing. Call (303) 555-0142
or email info@acme.example.</p><p>1234 Elm Street, Denver, CO 80202</p>
<a href="/about">About</a><a href="/contact">Contact</a>"""
add("t01", "task", "spider maps pages", page(_SITE_BODY, title="Acme"),
    kind="task", task="spider", check=lambda r: bool(r["data"]["sitemap"]))
add("t02", "task", "contacts task finds email", page(_SITE_BODY),
    kind="task", task="contacts",
    check=lambda r: "info@acme.example" in r["data"]["contacts"]["emails"])
add("t03", "task", "contacts task finds phone", page(_SITE_BODY),
    kind="task", task="contacts", check=lambda r: bool(r["data"]["contacts"]["phones"]))
add("t04", "task", "research identifies name", page(_SITE_BODY, title="Acme Roofing"),
    kind="task", task="research",
    check=lambda r: "Acme" in r["data"]["identity"]["name"])
add("t05", "task", "research classifies construction", page(_SITE_BODY, title="Acme Roofing"),
    kind="task", task="research", check=lambda r: r["data"]["naics"]["code"] == "23")
add("t06", "task", "naics on a restaurant",
    page("<h1>Bella Cafe</h1><p>Our restaurant menu, dining and reservations. "
         "Catering available.</p>", title="Bella Cafe Restaurant"),
    kind="task", task="naics", check=lambda r: r["data"]["naics"]["code"] == "72")
add("t07", "task", "naics on a law firm",
    page("<h1>Smith &amp; Co</h1><p>Our law firm attorneys provide legal advisory "
         "and consulting services.</p>", title="Smith Law Firm"),
    kind="task", task="naics", check=lambda r: r["data"]["naics"]["code"] == "54")
add("t08", "task", "naics on a clinic",
    page("<h1>Northside Clinic</h1><p>Our medical center physicians treat patients. "
         "Dental and nursing care.</p>", title="Northside Clinic"),
    kind="task", task="naics", check=lambda r: r["data"]["naics"]["code"] == "62")
add("t09", "task", "naics on a school",
    page("<h1>Oak Academy</h1><p>Our school curriculum and tutoring for students. "
         "Enrollment open at the college.</p>", title="Oak Academy School"),
    kind="task", task="naics", check=lambda r: r["data"]["naics"]["code"] == "61")
add("t10", "task", "naics refuses a blank page",
    page("<h1>Hello</h1><p>Welcome to our website.</p>", title="Home"),
    kind="task", task="naics",
    check=lambda r: r["data"]["naics"]["usable"] is False,
    why="emitting a code from no evidence is worse than emitting nothing")
add("t11", "task", "news finds an article",
    page("<h1>Five roof tips</h1><p>" + "Maintaining a roof takes care. " * 40 + "</p>",
         head='<meta property="article:published_time" content="2026-03-04T00:00:00Z">'),
    kind="task", task="news", check=lambda r: r["data"]["count"] >= 1)
add("t12", "task", "news skips a landing page", page(_SITE_BODY),
    kind="task", task="news", check=lambda r: r["data"]["count"] == 0)
add("t13", "task", "public_data single pass", page(_SITE_BODY, title="Acme Roofing"),
    kind="task", task="public_data",
    check=lambda r: bool(r["data"]["contacts"]["emails"]) and r["data"]["naics"]["code"] == "23")
add("t14", "task", "task result serialises", page(_SITE_BODY),
    kind="task", task="spider", check=lambda r: isinstance(r["data"], dict))
add("t15", "task", "task records pages visited", page(_SITE_BODY),
    kind="task", task="spider", check=lambda r: r["page_count"] >= 1)

# ------------------------------------------------------------ preprocess (15)
_RICH = page("""<nav><a href=/>Home</a></nav><main><h1>Title</h1>
<p>Body text here that is reasonably long so readability has something to pick.</p>
<form><input id=email name=email><button id=send data-testid=send>Send</button></form>
</main><footer>(c) 2026</footer>""", head="<style>.x{color:red}</style>"
             "<script>var big='"+"z"*3000+"'</script>")
for n, (strategy, checker, why) in enumerate([
    ("raw", lambda c: len(c) > 0, ""),
    ("clean_html", lambda c: "zzz" not in c and "<h1" in c, "script leaked"),
    ("text", lambda c: "Title" in c and "zzz" not in c, "script leaked into text"),
    ("readability", lambda c: "Body text" in c and "(c) 2026" not in c, ""),
    ("dom_skeleton", lambda c: "<form" in c and "Body text" not in c, ""),
    ("interactive", lambda c: "#send" in c or "send" in c, ""),
    ("accessibility", lambda c: "heading: Title" in c, ""),
    ("markdown", lambda c: "# Title" in c, ""),
], start=1):
    add(f"p{n:02d}", "preprocess", f"strategy {strategy}", _RICH, kind="preprocess",
        check=lambda r, f=checker: f(r["content"]), strategy=strategy, why=why)
add("p09", "preprocess", "text is much smaller than raw", _RICH, kind="preprocess",
    check=lambda r: r["saved_pct"] > 60, strategy="text")
add("p10", "preprocess", "markdown smaller than raw", _RICH, kind="preprocess",
    check=lambda r: r["saved_pct"] > 50, strategy="markdown")
add("p11", "preprocess", "interactive is the smallest useful view", _RICH,
    kind="preprocess", check=lambda r: r["saved_pct"] > 80, strategy="interactive")
add("p12", "preprocess", "focus keeps the answer",
    page("<h1>A</h1><p>" + "filler. " * 200 + "</p><h2>Contact</h2>"
         "<p>Email us at find@me.example</p>"),
    kind="focus", check=lambda r: "find@me.example" in r["content"],
    query="contact email", budget=600)
add("p13", "preprocess", "focus reduces size",
    page("<h1>A</h1><p>" + "filler. " * 400 + "</p><h2>Contact</h2><p>find@me.example</p>"),
    kind="focus", check=lambda r: r["saved_pct"] > 40, query="contact email", budget=600)
add("p14", "preprocess", "focus on no match still returns content",
    page("<p>" + "unrelated. " * 100 + "</p>"), kind="focus",
    check=lambda r: len(r["content"]) > 0, query="zzzz nothing", budget=400)
add("p15", "preprocess", "chunking splits long text",
    page("<h1>A</h1><p>" + "x " * 2000 + "</p><h2>B</h2><p>" + "y " * 2000 + "</p>"),
    kind="chunk", check=lambda r: r["chunks"] >= 3)

# -------------------------------------------------------------- failures (15)
add("f01", "fail", "captcha detected as challenge",
    page("<h1>Verify you are human</h1><p>Please complete the CAPTCHA.</p>"),
    kind="classify_page", check=lambda r: r["failure"] == "challenge",
    why="misreading a bot wall as a missing element is how a run gets an IP banned")
add("f02", "fail", "cloudflare wall detected",
    page("<p>Checking your browser before accessing. Cloudflare</p>"),
    kind="classify_page", check=lambda r: r["failure"] == "challenge")
add("f03", "fail", "unusual traffic detected",
    page("<p>Our systems have detected unusual traffic</p>"),
    kind="classify_page", check=lambda r: r["failure"] == "challenge")
add("f04", "fail", "ordinary page is not a challenge", page("<p>Welcome</p>"),
    kind="classify_page", check=lambda r: r["failure"] != "challenge")
add("f05", "fail", "missing selector fails the run", page("<p>x</p>"),
    kind="interact", check=lambda r: r.get("failed") is True, selector="#nope",
    read="#out", expect_fail=True)
add("f06", "fail", "failure captures page text for diagnosis",
    page("<p>Please complete the CAPTCHA</p>"), kind="interact",
    check=lambda r: "CAPTCHA" in (r.get("page_text") or ""), selector="#nope",
    expect_fail=True,
    why="without the page snapshot a challenge is indistinguishable from a bad selector")
for n, (err, expected) in enumerate([
    ("timeout waiting for #x", "timeout"),
    ("HTTP 429 too many requests", "rate_limit"),
    ("403 Forbidden", "blocked"),
    ("401 unauthorized", "auth"),
    ("ECONNREFUSED proxy", "network"),
    ("llm unreachable at http://x", "llm"),
    ("target closed", "crash"),
    ("verification failed: no banner", "verify"),
    ("something odd happened", "unknown"),
], start=7):
    add(f"f{n:02d}", "fail", f"classify {expected}", page("<p>x</p>"),
        kind="classify_error", check=lambda r, e=expected: r["failure"] == e, error=err)

CORPUS = S
CATEGORIES = sorted({s.category for s in CORPUS})


def by_category() -> dict[str, int]:
    out: dict[str, int] = {}
    for s in CORPUS:
        out[s.category] = out.get(s.category, 0) + 1
    return out
