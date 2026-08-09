#!/usr/bin/env python3
"""Generate the Kaggle tour notebook.

A generator rather than a hand-edited .ipynb: the notebook is ~50 cells whose
code must stay in step with the library, and reviewing a diff of JSON with
embedded output blobs is not a thing anyone should have to do.

    python notebooks/build_tour.py && kaggle kernels push -p .kaggle-nb
"""
from __future__ import annotations

import json
import pathlib

CELLS: list[tuple[str, str]] = []


def md(text: str) -> None:
    CELLS.append(("markdown", text.strip("\n")))


def code(text: str) -> None:
    CELLS.append(("code", text.strip("\n")))


# ---------------------------------------------------------------- intro ----
md("""
# browsergraph — one graph, every browser (and none at all)

**Repo:** https://github.com/aidonerightcorp/browsergraph

Write a browser-automation graph once. Run it on Playwright, Patchright, Selenium,
undetected-chromedriver, Camoufox — or with no browser at all.

This notebook runs **for real**: it installs a browser, drives it, and shows you the
screenshots and video it captured. Every number and image below is produced by the
cell above it. Nothing is illustrative.

**What this is actually about.** The interesting problem in browser automation is not
clicking things. It is that *a run which reports success can have accomplished nothing*
— and you find out weeks later. The design decisions here follow from that, and the
notebook shows the machinery that catches it.
""")

md("## 1. Install")

code("""
%pip install -q "browsergraph[http] @ git+https://github.com/aidonerightcorp/browsergraph.git"
import browsergraph
print('browsergraph', browsergraph.__version__)
""")

code("""
# A real browser, so the screenshots and video below are real.
%pip install -q playwright
""")

code("""
# The installer's exit code is not evidence. `ensure_browser` probes, installs
# the binary, installs the system libraries a slim image omits, falls back to a
# Chrome already on PATH — and re-launches after every step, because launching
# is the only proof that counts. It prints exactly what it tried.
from browsergraph import Engine, Spec
from browsergraph.bootstrap import ensure_browser
from browsergraph.dimensions import Display, Stealth
from browsergraph.drivers import build

boot = ensure_browser(verbose=True)
print()
print(boot.text())
HAVE_BROWSER = boot.ok
EXEC_PATH = boot.executable_path        # '' means the playwright-bundled build
""")

# ---------------------------------------------------------------- doctor ---
md("""
## 2. What can this machine actually run?

Browser automation fails for environmental reasons far more often than logical ones.
`doctor` reports what is present and prints the command that fixes anything missing.
""")

code("""
from browsergraph.doctor import run_all, available_engines
print(run_all().text())
print('\\nusable engines:', [e.value for e in available_engines()])
""")

# --------------------------------------------------------------- the graph -
md("""
## 3. A page to work against

Served from this kernel, so the demos below depend on nothing external — but driven
by a **real browser**, not a mock. The live public web comes in section 8.
""")

code('''
import functools, http.server, socketserver, threading, pathlib, tempfile

TMP = pathlib.Path(tempfile.mkdtemp(prefix='bg-'))
(TMP / 'index.html').write_text("""<!doctype html><html lang=en><head>
<meta charset=utf-8><title>Acme Roofing</title><style>
 body{font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;color:#182230}
 header{background:linear-gradient(135deg,#1e3a5f,#2d6cb5);color:#fff;padding:48px 40px}
 h1{margin:0 0 8px;font-size:34px} main{padding:32px 40px;max-width:760px}
 .card{border:1px solid #dde3ea;border-radius:10px;padding:20px;margin:18px 0}
 button{background:#2d6cb5;color:#fff;border:0;padding:12px 22px;border-radius:7px;
        font-size:15px;cursor:pointer} #out{margin-top:14px;color:#127a3d;font-weight:600}
</style></head><body>
<header><h1>Acme Roofing</h1><div>Commercial roofing &amp; gutters since 1984</div></header>
<main>
 <div class=card><h2>Contact</h2>
  <p>Email <a href="mailto:sales@acme.example">sales@acme.example</a>
     or call (303) 555-0142.</p></div>
 <div class=card><h2>Request a quote</h2>
  <button id=quote>Request quote</button><div id=out></div></div>
 <div class=card><h2>About</h2><p>We are a general contractor specialising in
  commercial roofing, gutters and sheet metal fabrication.</p>
  <p><a id=more href="/index.html">More about us</a></p></div>
</main>
<script>quote.onclick=()=>{out.textContent='Quote requested — we will call you back.'}</script>
</body></html>""", encoding='utf-8')

handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(TMP))
handler.log_message = lambda *a, **k: None
httpd = socketserver.TCPServer(('127.0.0.1', 0), handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f'http://127.0.0.1:{httpd.server_address[1]}'
print('serving', BASE)
''')

# --------------------------------------------------------------- the graph -
md("""
## 4. A graph is nodes over a dimension space

`Spec` is one point in `engine x binary x transport x display x stealth x preprocess x
vision x capture`. Nodes never touch an engine directly — they talk to a 12-method
`BrowserPort`. That seam is the whole reason one graph is portable.
""")

code("""
from browsergraph import Graph, Spec, Engine, run
from browsergraph.nodes.actions import Navigate, WaitFor, Click, Extract, Screenshot

graph = (Graph('login-check')
         .add(Navigate('https://acme.example'))
         .add(WaitFor('#login'))
         .add(Click('#login'))
         .add(Extract('h1', into='heading')))

print(graph.to_mermaid())
""")

md("""
Rendered — mutating steps are boxed in red, verifying steps green. That colouring is not
decoration: *"which steps change remote state, and does anything check them?"* is the
question this library exists to answer.
""")

code(r"""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

def draw_graph(g, ax=None, title=None):
    '''Draw a graph using its own topological levels — no layout library needed.'''
    levels = g.levels()
    pos, H = {}, len(levels)
    for y, level in enumerate(levels):
        for x, key in enumerate(level):
            pos[key] = (x - (len(level) - 1) / 2, H - y)
    if ax is None:
        _, ax = plt.subplots(figsize=(7, 1.5 + 1.25 * H))
    for e in g.edges:
        (x0, y0), (x1, y1) = pos[e.src], pos[e.dst]
        ax.annotate('', xy=(x1, y1 + .22), xytext=(x0, y0 - .22),
                    arrowprops=dict(arrowstyle='-|>', lw=1.4, color='#888',
                                    linestyle='--' if e.kind.value == 'dependency' else '-'))
    for key, (x, y) in pos.items():
        n = g.nodes[key]
        face, edge = '#eef1f5', '#8a93a0'
        if n.mutates:   face, edge = '#fde2e2', '#cc3333'
        elif n.verifies: face, edge = '#e2f5e6', '#22aa44'
        ax.add_patch(mpatches.FancyBboxPatch((x - .46, y - .21), .92, .42,
                     boxstyle='round,pad=0.02', fc=face, ec=edge, lw=1.6))
        # Auto-named nodes take their key from their kind; printing both just
        # repeats the same word twice.
        label = key if key == n.kind else f"{key}\n{n.kind}"
        ax.text(x, y, label, ha='center', va='center', fontsize=8)
    ax.set_xlim(-1.6, 1.6); ax.set_ylim(0.3, H + .8); ax.axis('off')
    ax.set_title(title or g.name, fontsize=11)
    ax.legend(handles=[mpatches.Patch(fc='#fde2e2', ec='#cc3333', label='mutates'),
                       mpatches.Patch(fc='#e2f5e6', ec='#22aa44', label='verifies'),
                       mpatches.Patch(fc='#eef1f5', ec='#8a93a0', label='read-only')],
              loc='upper right', fontsize=7, frameon=False)
    return ax

draw_graph(graph); plt.tight_layout(); plt.show()
""")

md("""
`graph.to_html()` renders the same thing **interactively** — hover a node for its
contract, click one to isolate everything it can reach. It is a single self-contained
blob: no CDN, no library, no network, so it renders the same in a notebook, in a saved
file, and offline.
""")

code("""
from IPython.display import HTML
HTML(graph.to_html())          # hover a node; click to trace what it reaches
""")

# ------------------------------------------------------------- contracts ---
md("""
## 5. Contracts: what a node promises, checked rather than assumed

Every check in this library reads a node's own declarations. The linter decides whether
a graph verifies its mutations by trusting `mutates`; the scheduler decides what may run
concurrently by trusting `reads`/`writes`.

So a node that misdeclares itself doesn't fail — **it silently switches those checks off.**
That makes contracts worth enforcing at all three moments where it is possible.
""")

code("""
from browsergraph.contracts import contract_of, describe_all
from browsergraph.nodes import REGISTRY

print(f'{len(REGISTRY)} node kinds\\n')
print(describe_all(REGISTRY.values()))
""")

md("**Moment 1 — definition.** A malformed node fails at *import*, not thirty seconds into a live browser session.")

code('''
from browsergraph.contracts import ContractError
from browsergraph.nodes.base import Node

def attempt(label, define):
    try:
        define(); print(f'  NOT CAUGHT  {label}')
    except ContractError as e:
        print(f'  caught      {label}\\n              {str(e).splitlines()[1].strip()[:110]}')

def missing_comma():
    class Bad(Node):
        kind = 'demo_missing_comma'
        writes = ('url')          # <- the classic missing comma: this is a str
        def run(self, ctx): return ctx

def bad_kind():
    class Bad(Node):
        kind = 'Not Snake Case'
        def run(self, ctx): return ctx

def no_run():
    class Bad(Node):
        kind = 'demo_no_run'

attempt("writes=('url') — missing comma", missing_comma)
attempt('kind is not snake_case', bad_kind)
attempt('run() never implemented', no_run)
''')

md("""
The first one is worth dwelling on. `writes = ("url")` is not a tuple — it is the string
`"url"`, and every consumer that iterates it sees the characters `u`, `r`, `l`. Nothing
raises. The graph simply believes three keys exist that never will.
""")

md("**Moment 2 — composition.** Individually valid nodes can still be wrongly *ordered*.")

code("""
from browsergraph.nodes.base import Node

class NeedsHeading(Node):
    kind = 'needs_heading'
    reads = ('heading',)
    needs_browser = False
    def run(self, ctx): return ctx

bad = Graph('mis-ordered').add(NeedsHeading()).add(Extract('h1', into='heading'))
print(bad.audit().text())
print()
good = Graph('ordered').add(Extract('h1', into='heading')).add(NeedsHeading())
print('reordered ->', good.audit().text())
""")

md("""
**Moment 3 — execution.** The one the other two cannot reach: a declaration that was
accurate when written and has since drifted from the code.

`Checked` wraps a node, records what it actually asks the browser to do, and compares
that against what it claims. Below, a node that clicks while declaring `mutates=False` —
exactly the lie that makes BG003 pass on a graph that is not safe.
""")

code("""
from browsergraph.nodes.checked import Checked, ContractViolation, checked
from browsergraph.dimensions import Stealth

class SneakyClick(Node):
    kind = 'sneaky_click'
    mutates = False                       # the lie
    TARGET = '#quote'
    def run(self, ctx):
        ctx.page.click(self.TARGET)       # the truth
        return ctx

# A real engine against the real page above — the violation is detected from
# what the node actually did, not from anything declared or simulated. With a
# browser it clicks a button; without one, engine=http follows a link. Either
# way `click` is a mutating call, and the recorder saw it.
if HAVE_BROWSER:
    spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS)
else:
    spec = Spec(engine=Engine.HTTP, stealth=Stealth.UNDETECTED)
    SneakyClick.TARGET = '#more'
g = Graph('sneaky').add(Navigate(f'{BASE}/index.html')).add(Checked(SneakyClick()))
try:
    run(g, spec, build(spec))
    print('NOT CAUGHT — the check failed to fire')
except ContractViolation as e:
    print('CONTRACT VIOLATION\\n ', e)
""")

# ----------------------------------------------------------- real browser --
md("""
## 6. Driving a real browser — with the screenshot to prove it

Everything above ran without a browser. Now a real chromium, against a page served from
this kernel, so nothing depends on the public internet.
""")


code("""
from browsergraph.dimensions import Display, Capture
from browsergraph.drivers import build

shot_graph = (Graph('quote')
              .add(Navigate(f'{BASE}/index.html'))
              .add(WaitFor('#quote'))
              .add(Extract('h1', into='company'))
              .add(Click('#quote'))
              .add(WaitFor('#out', name='confirm'))       # <- verifies the click
              .add(Extract('#out', into='confirmation'))
              .add(Screenshot(str(TMP / 'after.png'))))

if HAVE_BROWSER:
    spec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS)
    result = run(shot_graph, spec, build(spec))
    print(result.summary())
    for k, v in result.context.data.items():
        print(f'  {k}: {v!r}')
else:
    print('no browser available in this kernel')
""")

code("""
from IPython.display import Image, display
if HAVE_BROWSER and result.context.artifacts:
    display(Image(filename=result.context.artifacts[-1], width=760))
    print('screenshot:', result.context.artifacts[-1])
""")

md("""
Note what the graph did: it clicked, then **waited for the element that only appears if
the click worked**, then extracted it. That is the shape BG003 exists to enforce, and the
screenshot is the evidence.
""")

md("""
### Video of the same run

Playwright's bundled encoder produces webm — no system ffmpeg needed. It is also the
reason `capture=video` is a *dimension*: recording changes the browser context, so it
has to be declared up front rather than switched on later.
""")

code("""
import base64
from IPython.display import HTML

video_html = None
if HAVE_BROWSER:
    vspec = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS,
                 capture=Capture.VIDEO, artifact_dir=str(TMP / 'vid'))
    browser = build(vspec)
    vres = run(shot_graph, vspec, browser)
    path = getattr(browser, 'video_path', '')
    print('video:', path or '(none)')
    if path and pathlib.Path(path).exists():
        b64 = base64.b64encode(pathlib.Path(path).read_bytes()).decode()
        size = pathlib.Path(path).stat().st_size / 1024
        print(f'{size:.0f} KB')
        video_html = HTML(f'<video controls autoplay loop muted width="760" '
                          f'src="data:video/webm;base64,{b64}"></video>')
video_html
""")

# ------------------------------------------------------- engines compared --
md("""
## 7. The same graph on different engines — including no engine at all

Most pages are server-rendered and need no browser. The catch is that anti-bot vendors
fingerprint the **TLS handshake** before any JavaScript runs, so a stock Python client is
identifiable however good its User-Agent is. `Engine.HTTP` uses `curl-cffi` to present a
real browser's handshake.

It refuses what it cannot do rather than pretending — a driver that silently no-ops a
click surfaces later as missing data with no explanation.
""")

code("""
import time
from browsergraph.dimensions import Stealth

read_only = (Graph('read')
             .add(Navigate(f'{BASE}/index.html'))
             .add(Extract('h1', into='company')))

timings = {}
candidates = [('http', Spec(engine=Engine.HTTP, stealth=Stealth.UNDETECTED))]
if HAVE_BROWSER:
    candidates.append(('playwright', Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS)))

for label, sp in candidates:
    try:
        t0 = time.time()
        r = run(read_only, sp, build(sp))
        timings[label] = time.time() - t0
        print(f'{label:<12} {timings[label]:.2f}s   heading={r.context.data["company"]!r}')
    except Exception as e:
        print(f'{label:<12} unavailable: {type(e).__name__}: {e}')
""")

code("""
if len(timings) > 1:
    fig, ax = plt.subplots(figsize=(7, 2.4))
    names = list(timings); vals = [timings[n] for n in names]
    bars = ax.barh(names, vals, color=['#2d9e6f', '#2d6cb5'][:len(names)])
    for b, v in zip(bars, vals):
        ax.text(v, b.get_y() + b.get_height()/2, f'  {v:.2f}s', va='center', fontsize=10)
    ax.set_xlabel('seconds — same graph, same result')
    ax.set_title(f'Browser-less is {max(vals)/min(vals):.1f}x faster here')
    ax.spines[['top','right']].set_visible(False)
    plt.tight_layout(); plt.show()
    print(f'speedup: {max(vals)/min(vals):.1f}x')
""")

code("""
from browsergraph.dimensions import Vision, validate
print('http + vision ->', validate(Spec(engine=Engine.HTTP, vision=Vision.ALWAYS)))
b = build(Spec(engine=Engine.HTTP)); b.start(); b.goto(f'{BASE}/index.html')
try:
    b.eval_js('1+1')
except RuntimeError as e:
    print('http + eval_js ->', e)
finally:
    b.stop()
""")

# ------------------------------------------------------------ token costs --
md("""
## 8. Against the live web

Everything so far ran against a page served from this kernel — deterministic, but it
proves nothing about the real internet. So here are real public sites, fetched now.

Politeness first: the limiter is per-domain and process-wide, so these requests are
spaced whether they come from one crawler or ten.
""")

code("""
import time
from browsergraph.extract.content import parse_page
from browsergraph.throttle import SHARED, DomainPolicy
from browsergraph.dimensions import Stealth

SHARED.default = DomainPolicy(min_interval=1.0)     # one request per second per host

REAL = ['https://example.com', 'https://www.python.org', 'https://news.ycombinator.com']
live = []
for url in REAL:
    try:
        sp = Spec(engine=Engine.HTTP, stealth=Stealth.UNDETECTED)
        b = build(sp); b.start()
        SHARED.acquire(url)                          # be polite to a real host
        try:
            t0 = time.time(); state = b.goto(url); dt = time.time() - t0
            page = parse_page(b.html(), url)
            live.append({'url': url, 'status': state.status, 'title': state.title,
                         'seconds': dt, 'bytes': len(b.html()), 'links': len(page.links)})
        finally:
            SHARED.release(url); b.stop()
    except Exception as e:
        print(f'{url}: {type(e).__name__}: {str(e)[:90]}')

for r in live:
    print(f"{r['url']:<34} {r['status']}  {r['seconds']:.2f}s  "
          f"{r['bytes']:>7,}B  {r['links']:>3} links  {r['title'][:34]!r}")
""")

md("""
### The same real page, with and without a browser

A browser costs roughly an order of magnitude more time and memory. The question is
whether the page needs one — for server-rendered HTML it usually does not, and this is
measured on a live site rather than asserted.
""")

code("""
REAL_URL = 'https://www.python.org'
real_graph = Graph('live').add(Navigate(REAL_URL)).add(Extract('title', into='title'))

real_timings = {}
options = [('http', Spec(engine=Engine.HTTP, stealth=Stealth.UNDETECTED))]
if HAVE_BROWSER:
    options.append(('playwright', Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS)))

for label, sp in options:
    try:
        SHARED.acquire(REAL_URL)
        try:
            t0 = time.time(); r = run(real_graph, sp, build(sp)); dt = time.time() - t0
        finally:
            SHARED.release(REAL_URL)
        real_timings[label] = dt
        print(f'{label:<12} {dt:5.2f}s   title={r.context.data["title"]!r}')
    except Exception as e:
        print(f'{label:<12} failed: {type(e).__name__}: {str(e)[:90]}')
""")

code("""
if len(real_timings) > 1:
    fig, ax = plt.subplots(figsize=(7, 2.3))
    names = list(real_timings); vals = [real_timings[n] for n in names]
    bars = ax.barh(names, vals, color=['#2d9e6f', '#2d6cb5'])
    for b, v in zip(bars, vals):
        ax.text(v, b.get_y() + b.get_height()/2, f'  {v:.2f}s', va='center', fontsize=10)
    ax.set_xlabel(f'seconds — {REAL_URL}, same graph, same result')
    ax.set_title(f'Live site: browser-less is {max(vals)/min(vals):.1f}x faster')
    ax.spines[['top','right']].set_visible(False)
    plt.tight_layout(); plt.show()
""")

md("### A screenshot of a real site")

code("""
if HAVE_BROWSER:
    shot = TMP / 'live.png'
    live_graph = (Graph('live-shot').add(Navigate(REAL_URL))
                  .add(WaitFor('body', name='loaded'))
                  .add(Screenshot(str(shot))))
    SHARED.acquire(REAL_URL)
    try:
        sp = Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS)
        lr = run(live_graph, sp, build(sp))
    finally:
        SHARED.release(REAL_URL)
    print(lr.summary())
    display(Image(filename=str(shot), width=760))
""")

md("""
Real sites are also where the *conservative* extractors earn their keep — a live page is
full of things that look like phone numbers and are not.
""")

code("""
from browsergraph.extract.patterns import extract_contacts

for r in live[:3]:
    sp = Spec(engine=Engine.HTTP, stealth=Stealth.UNDETECTED)
    b = build(sp); b.start()
    SHARED.acquire(r['url'])
    try:
        b.goto(r['url']); pg = parse_page(b.html(), r['url'])
    finally:
        SHARED.release(r['url']); b.stop()
    found = extract_contacts(pg.text, pg.links, pg.mailtos)
    print(f"{r['url']:<34} emails={found.emails[:2] or '[]'} "
          f"phones={[p.raw for p in found.phones][:2] or '[]'}")
print('\\nNothing invented where there was nothing to find — an empty field is a '
      'visible miss;\\na wrong one silently poisons the dataset.')
""")


md("""
## 9. When one configuration fails, try the others — automatically

A graph is portable, but not every *spec* can run it. The page below renders its
price with JavaScript, so the browser-less engine cannot possibly succeed no matter
how long it waits.

`escalate` walks a ladder of specs, and after each failure a **diagnosis** decides what
to try next — this is the part that makes it more than a retry loop. It also stops dead
on a terminal diagnosis: escalating harder against a site that has already flagged you
is how accounts are lost.
""")

code("""
from browsergraph.strategy import escalate, ladder, suggest
from browsergraph.errors import classify as classify_error

(TMP / 'app.html').write_text(
    '<!doctype html><html><head><title>App</title></head><body><div id=root></div>'
    '<script>document.getElementById("root").innerHTML ='
    ' "<h1 id=price>$49.00</h1>";</script></body></html>', encoding='utf-8')

price_graph = (Graph('price')
               .add(Navigate(f'{BASE}/app.html'))
               .add(WaitFor('#price'))
               .add(Extract('#price', into='price')))

rungs = [Spec(engine=Engine.HTTP, stealth=Stealth.UNDETECTED)]     # cannot run JS
if HAVE_BROWSER:
    rungs.append(Spec(engine=Engine.PLAYWRIGHT, display=Display.HEADLESS))

esc = escalate(price_graph, rungs, build, url=f'{BASE}/app.html',
               sleep=lambda s: None)
print(esc.summary(), '\\n')
for i, a in enumerate(esc.attempts, 1):
    d = a.diagnosis
    print(f"  {i}. {a.spec.engine.value:<11} ok={str(a.ok):<6}"
          f"{(d.failure.value if d else '-'):<15}{(d.response.value if d else '')}")
if esc.ok:
    print('\\nextracted after escalating:', esc.attempts[-1].result.context.data['price'])
""")

md("""
Two details worth pointing at.

**The retry is bounded.** A timeout is a retryable failure, so the browser-less engine is
tried again — but only so many times. An unbounded retry is not a retry policy; it is a
way to never reach the rest of the ladder. (This was a real bug: the escalator spent all
six attempts re-trying the engine that could never work, and never reached the browser.)

**The suggestion is targeted, not random.** A missing element on an engine with no
JavaScript runtime cannot be waited into existence, so the suggested next step is a
different engine rather than a longer dwell.
""")

code("""
no_js = Spec(engine=Engine.HTTP, stealth=Stealth.UNDETECTED)
diag = classify_error('timeout waiting for #price')
print('diagnosis :', diag.failure.value, '->', diag.response.value,
      f'(terminal={diag.terminal})')
print('suggested next, for an engine that cannot run JavaScript:')
for s in suggest(no_js, diag)[:3]:
    print('   ', s.describe())

print('\\nand for a blocked browser — evasion, not repetition:')
blocked = classify_error('403 Forbidden')
print('diagnosis :', blocked.failure.value, '->', blocked.response.value,
      f'(terminal={blocked.terminal})  <- stops, never escalates into a ban')
for s in suggest(Spec(engine=Engine.PLAYWRIGHT), classify_error('429 too many requests'))[:3]:
    print('   ', s.describe())
""")

md("""
And once something works, it is remembered: `SiteMemory` puts the winning spec first for
that domain next time, so escalation is a one-off cost rather than a per-run tax. The
learning system in section 13 generalises the same idea across *similar* sites.
""")


md("""
## 10. Token reduction: eight strategies, then focus

Raw HTML is mostly framework noise. Preprocessing trades structure against size; `focus`
then keeps only the chunks that answer the question **plus their neighbours** — because
the chunk matching "contact" is rarely the one holding the phone number.
""")

code("""
from browsergraph.preprocess import Preprocess, compare, reduce, backends
from browsergraph.focus import focus

HTML = (TMP / 'index.html').read_text() + '<script>var pad="' + 'z'*6000 + '";</script>'
rows = sorted(compare(HTML), key=lambda r: r.chars)
for r in rows:
    print(f'{r.strategy.value:<15} {r.chars:>7} chars   saved {r.saved_pct:5.1f}%')

f = focus(reduce(HTML, Preprocess.MARKDOWN).content, 'sales email', budget=500)
print(f'\\nfocus -> {f.chars} chars (saved {f.saved_pct:.0f}%)   '
      f'answer kept: {"sales@acme.example" in f.content}')
print('optional backends:', backends())
""")

code("""
fig, ax = plt.subplots(figsize=(8, 3.6))
names = [r.strategy.value for r in rows]; chars = [r.chars for r in rows]
colors = ['#c0392b' if n == 'raw' else '#2d6cb5' for n in names]
bars = ax.barh(names, chars, color=colors)
for b, r in zip(bars, rows):
    ax.text(r.chars, b.get_y() + b.get_height()/2, f'  {r.saved_pct:.0f}% saved',
            va='center', fontsize=9)
ax.set_xlabel('characters sent to the model'); ax.set_xscale('log')
ax.set_title('Same page, eight preprocessing strategies (log scale)')
ax.spines[['top','right']].set_visible(False)
plt.tight_layout(); plt.show()
""")

# -------------------------------------------------------------- the linter -
md("""
## 11. The linter, and the failure that motivated it

**BG003 — a graph that changes remote state but never verifies the outcome.**

This rule exists because of a real incident: 551 emails reported "sent" successfully and
produced zero posts. Every layer said success. Nothing checked the destination.
""")

code("""
from browsergraph.lint import lint, report

risky = (Graph('risky').add(Navigate(f'{BASE}/index.html'))
         .add(WaitFor('#quote')).add(Click('#quote')))       # clicks, never verifies
safe  = (Graph('safe').add(Navigate(f'{BASE}/index.html'))
         .add(WaitFor('#quote')).add(Click('#quote'))
         .add(WaitFor('#out', name='confirm'))
         .add(Screenshot(str(TMP / 'safe.png'))))

print('RISKY\\n' + report(lint(risky)))
print('\\nSAFE\\n'  + report(lint(safe)))
""")

code("""
fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
draw_graph(risky, axes[0], 'risky — mutation never verified')
draw_graph(safe,  axes[1], 'safe — click, then confirm')
plt.tight_layout(); plt.show()
""")

# ------------------------------------------------------------ combinations -
md("""
## 12. Don't enumerate the space — sample it

Incompatible combinations are rejected *with reasons*. Full enumeration explodes, so
`sample` builds a pairwise covering array: most failures are two-value interactions, and
those are catchable at a fraction of the cost.
""")

code("""
from browsergraph.combos import count, rejected
from browsergraph.sample import coverage, sample_specs
from browsergraph.dimensions import Binary

total, ok = count()
print(f'{total} combinations -> {ok} runnable, {total-ok} rejected\\n')
for desc, why in rejected()[:3]:
    print(f'  {desc[:56]}\\n      {why[0][:100]}')

axes = {'engine': list(Engine), 'binary': list(Binary),
        'display': list(Display), 'stealth': list(Stealth)}
specs = sample_specs(axes)
cov, poss = coverage(axes, specs)
print(f'\\npairwise: {len(specs)} runs cover {cov}/{poss} value-pairs')
""")

code("""
fig, axes_ = plt.subplots(1, 2, figsize=(11, 3.6))

axes_[0].bar(['runnable', 'rejected'], [ok, total-ok], color=['#2d9e6f', '#c0392b'])
axes_[0].set_title(f'{total:,} combinations in the space')
for i, v in enumerate([ok, total-ok]):
    axes_[0].text(i, v, f'{v:,}', ha='center', va='bottom', fontsize=10)
axes_[0].spines[['top','right']].set_visible(False)

xs = list(range(1, len(specs) + 1))
ys = [coverage(axes, specs[:n])[0] for n in xs]
axes_[1].plot(xs, ys, lw=2, color='#2d6cb5')
axes_[1].axhline(poss, ls='--', lw=1, color='#999')
axes_[1].text(len(xs)*0.55, poss*0.94, f'{poss} pairs possible', fontsize=8, color='#666')
axes_[1].set_xlabel('runs executed'); axes_[1].set_ylabel('value-pairs covered')
axes_[1].set_title('Coverage rises fast, then saturates')
axes_[1].spines[['top','right']].set_visible(False)
plt.tight_layout(); plt.show()
""")

# ------------------------------------------------------------- learning ----
md("""
## 13. Self-tuning: learn from similar sites

Outcomes generalise `site -> org -> sector -> platform -> global`, weighted by
specificity. Evidence is reported honestly: one success is *p≈0.67, n=1* after smoothing,
never certainty.
""")

code("""
from browsergraph.learn import Features, Knowledge, Outcome, Budget, Estimate, plan
from browsergraph.strategy import ladder

k, winner = Knowledge(), Spec(engine=Engine.HTTP, stealth=Stealth.UNDETECTED)
f1 = Features.of('https://acme.example', task='contacts')
k.record(f1, winner, True)
print('after one win :', k.estimate(f1, winner.describe()))

trace = []
for i in range(12):
    k.record(Features.of(f'https://shop{i}.example', sector='44-45', task='contacts'),
             winner, True)
    e = k.estimate(Features.of('https://brandnew.example', sector='44-45', task='contacts'),
                   winner.describe())
    trace.append((i + 1, e.p, e.evidence))
print('unseen site   :', k.estimate(
    Features.of('https://brandnew.example', sector='44-45', task='contacts'),
    winner.describe()))
""")

code("""
fig, ax = plt.subplots(figsize=(7.5, 3.4))
xs = [t[0] for t in trace]
ax.plot(xs, [t[1] for t in trace], lw=2, color='#2d6cb5', marker='o', ms=3,
        label='estimated p(success)')
ax.axhline(0.5, ls='--', lw=1, color='#999')
ax.text(0.4, 0.52, 'prior — no information', fontsize=8, color='#666')
ax2 = ax.twinx()
ax2.plot(xs, [t[2] for t in trace], lw=1.5, color='#c98a2b', ls=':', label='evidence (n)')
ax2.set_ylabel('evidence n', color='#c98a2b')
ax.set_xlabel('successes observed on *other* sites in the same sector')
ax.set_ylabel('p(success) on an unseen site'); ax.set_ylim(0.4, 1.02)
ax.set_title('A never-before-seen site inherits from its sector')
ax.spines[['top']].set_visible(False); ax2.spines[['top']].set_visible(False)
h1, l1 = ax.get_legend_handles_labels()          # one legend, both axes
h2, l2 = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2, loc='lower right', fontsize=8, frameon=False)
plt.tight_layout(); plt.show()
""")

md("""
### Guardrails: never stop on ignorance

Early stopping needs *both* a low expected success rate **and** enough evidence to trust
the number. Abandoning an unfamiliar site on its first failure is exactly when
exploration is worth most.
""")

code("""
b = Budget(min_expected_success=0.5, min_evidence_to_stop=3)
print('thin evidence  ->', repr(b.should_stop_early(Estimate('s', p=0.1, evidence=1.0))))
print('solid evidence ->', b.should_stop_early(Estimate('s', p=0.1, evidence=8.0)))

print()
cheap = Outcome(ok=True, yield_count=8, tokens=1200, seconds=3)
dear  = Outcome(ok=True, yield_count=8, tokens=90_000, seconds=40)
print('same yield, cheap  :', round(cheap.utility(), 3))
print('same yield, costly :', round(dear.utility(), 3))
print('unmeasured success :', Outcome(ok=True).utility(), '  <- not 0.5: absence of')
print('failure            :', Outcome(ok=False, tokens=10).utility(),
      '     measurement is not mediocrity')
""")

# --------------------------------------------------------------- errors ----
md("""
## 14. A CAPTCHA is not a missing element

Retrying is not a universal remedy. A bot wall must **abort** — retrying into one is how
accounts get banned. Classification reads the page, not just the error string, because a
challenge usually presents as a missing selector.
""")

code("""
from browsergraph.errors import classify as classify_error

cases = [('click target not found: #login', ''),
         ('element not found: #x', 'Please complete the CAPTCHA'),
         ('HTTP 429 too many requests', ''),
         ('403 Forbidden', ''),
         ('timeout waiting for #results', '')]
rows_e = []
for err, page in cases:
    d = classify_error(err, page)
    rows_e.append((err[:34], d.failure.value, d.response.value, d.terminal))
    print(f'{err[:34]:<36} {d.failure.value:<14} -> {d.response.value:<11} terminal={d.terminal}')
""")

code("""
fig, ax = plt.subplots(figsize=(9, 2.6))
ax.axis('off')
tbl = ax.table(cellText=[[a, b, c, str(d)] for a, b, c, d in rows_e],
               colLabels=['observed', 'classified as', 'response', 'terminal'],
               cellLoc='left', loc='center')
tbl.auto_set_font_size(False); tbl.set_fontsize(9); tbl.scale(1, 1.5)
for (r, c), cell in tbl.get_celld().items():
    cell.set_edgecolor('#dde3ea')
    if r == 0:
        cell.set_facecolor('#eef1f5'); cell.set_text_props(weight='bold')
    elif c == 3 and cell.get_text().get_text() == 'True':
        cell.set_facecolor('#fde2e2')
ax.set_title('Same-looking failures, different correct responses', fontsize=11)
plt.tight_layout(); plt.show()
""")

# -------------------------------------------------------------- throttle ---
md("""
## 15. Politeness belongs where the contention is

A per-crawler delay lets ten concurrent tasks make ten requests per second at one host.
The limiter is **per-domain and process-wide**, and honours a robots `Crawl-delay` when
it is stricter — never when it is looser.
""")

code("""
from browsergraph.throttle import DomainPolicy, Limiter

class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def sleep(self, s): self.t += s

c, lim = Clock(), Limiter(default=DomainPolicy(min_interval=1.0))
waits = []
for _ in range(6):                       # six independent 'crawlers', one host
    waits.append(lim.acquire('https://one-host.example/p', sleep=c.sleep, clock=c))
    lim.release('https://one-host.example/p')
print('waits per request:', waits)

lim.observe_crawl_delay('slow.example', 10.0)
lim.observe_crawl_delay('slow.example', 0.1)     # must not relax
print('robots delay honoured:', lim.policy_for('slow.example').min_interval)
""")

# ------------------------------------------------------------ extraction ---
md("""
## 16. Deterministic extraction — conservative on purpose

No model involved. A false positive silently poisons a dataset; a miss is a visible empty
field. So dates, repeated digits and asset filenames are rejected rather than guessed at.

One of those rules exists because of the section above. Running this notebook against the
live web reported a phone number on python.org — which has none. The page prints a
Fibonacci series, and `... 233 377 610 987` matched the pattern for a nine-digit number.
No fixture would ever have contained that. Bare nine-digit runs, and numbers flanked by
other numbers, are now rejected.
""")

code("""
from browsergraph.extract.patterns import extract_contacts
from browsergraph.extract.content import parse_page
from browsergraph.classify.naics import classify

page = parse_page(HTML, BASE)
found = extract_contacts(page.text, page.links, page.mailtos)
print('emails :', found.emails)
print('phones :', [p.raw for p in found.phones])

noise = extract_contacts('Order 2026-03-04, SKU 000000000, logo@2x.png', [])
print('noise  ->', noise.emails, noise.phones, '  (nothing invented)')

print()
for label, text in [('this page', page.text),
                    ('restaurant', 'Our restaurant menu, dining and catering.'),
                    ('empty page', 'Welcome to our website. Hello.')]:
    cl = classify(text)
    print(f'{label:<12} NAICS {cl.code or "--":<4} {cl.confidence:<6} usable={cl.usable}')
""")

# ------------------------------------------------------------- the rest ----
md("""
## 17. Tasks, control flow and model routing

Control flow lives *inside* the graph — `branch`, `for_each`, `subgraph`, `frontier`,
`retry_until` are nodes, so healing, supervision and the linter apply to crawling too.
Crawling is where most of the runtime goes; leaving it outside the model meant none of
those guarantees reached it.
""")

code("""
from browsergraph.nodes.control import Subgraph
from browsergraph.tasks import catalog
from browsergraph.routing import JOBS

print('node kinds:', sorted(REGISTRY), '\\n')

inner = Graph('inner').add(Click('#quote'))
outer = (Graph('outer').add(Navigate(f'{BASE}/index.html')).add(Subgraph(inner)))
print('BG003 still fires through a subgraph:',
      'BG003' in {f.code for f in lint(outer)}, '\\n')

for t in catalog():
    print(f"  {t['name']:<13} {t['summary'][:64]}")

print()
for job, spec_ in JOBS.items():
    print(f"  {job:<10} needs={spec_['capability']:<11} prefers={spec_['role'] or '-'}")
""")

md("""
Model selection is by the model's **reported capability**, not its name. A vision job
answered by a text model returns confident fiction, so an unqualified model raises rather
than being silently substituted.
""")

md("""
## 18. Real models, on real pages

Everything so far is deterministic. The LLM nodes are not, and they are the ones where a
wrong answer is most expensive: a model asked to confirm an outcome will confirm it, if
you let it. So the demonstration below includes a claim that is **false**, because a
verifier that only ever gets shown true claims has not been tested at all.

The key is read from **Kaggle Secrets** — never from the notebook source, which is public.
Add-ons → Secrets → attach a secret named `OLLAMA_API_KEY`. Without it, this section
skips; nothing else in the notebook depends on it.
""")

code("""
OLLAMA_KEY = ''
try:
    from kaggle_secrets import UserSecretsClient
    OLLAMA_KEY = UserSecretsClient().get_secret('OLLAMA_API_KEY')
    print('Ollama key loaded from Kaggle Secrets')
except Exception as e:
    import os
    OLLAMA_KEY = os.environ.get('OLLAMA_API_KEY', '')
    print('no Kaggle secret;', 'using OLLAMA_API_KEY from the environment'
          if OLLAMA_KEY else f'LLM section will be skipped ({type(e).__name__})')
""")

code("""
from browsergraph.dimensions import LLMConfig, LLMControl
from browsergraph.nodes.llm import LLMVerify

LLM_HOST, LLM_MODEL = 'https://ollama.com', 'gpt-oss:120b'

if OLLAMA_KEY:
    cfg = LLMConfig(mode=LLMControl.VERIFY, host=LLM_HOST, model=LLM_MODEL,
                    api_key=OLLAMA_KEY, timeout=120)
    checks = [('https://example.com', 'this is the Example Domain placeholder page', True),
              ('https://example.com', 'this is a shopping cart checkout page',      False)]
    for url, claim, expected in checks:
        sp = Spec(engine=Engine.HTTP, stealth=Stealth.UNDETECTED, llm=cfg)
        SHARED.acquire(url)
        try:
            g = Graph('verify').add(Navigate(url)).add(LLMVerify(claim, cfg=cfg))
            r = run(g, sp, build(sp))
        finally:
            SHARED.release(url)
        got = r.context.data.get('verified')
        mark = 'OK ' if got == expected else 'XX '
        print(f"{mark} claim={claim[:44]!r:<48} verified={got}  (expected {expected})")
        print(f"     {str(r.context.data.get('verdict'))[:110]}")
else:
    print('skipped — no OLLAMA_API_KEY')
""")

md("""
The second row is the one that matters. A model that says *yes* to both is worse than no
model at all, because the graph would then report a verified outcome that never happened
— the exact failure BG003 exists to prevent, reintroduced one layer up.

That is also why `LLMControl` is a dimension rather than a flag: `none` (fully scripted),
`selector` (the model resolves selectors only when they fail), `verify`, `plan`, `agent`.
How much the model decides is a property of the run, chosen deliberately.
""")


# ---------------------------------------------------------------- close ----
code("""
httpd.shutdown(); httpd.server_close()
print('done — server stopped')
""")

md("""
## Read more

* **Repo:** https://github.com/aidonerightcorp/browsergraph
* `ARCHITECTURE.md` — the Protocol-vs-base-class seam
* `CONTRACTS.md` — what a node promises, and the three moments it is checked
* `DIMENSIONS.md` — axes worth adding, and why verification matters most
* `ISOLATION.md` — conflicting engines in separate virtualenvs
* `PLUGINS.md` — the open plugin format

Install: `pip install "browsergraph[http] @ git+https://github.com/aidonerightcorp/browsergraph.git"`

MIT licensed. Stdlib-only core — every dependency is an extra.
""")


def build() -> dict:
    cells = []
    for kind, src in CELLS:
        lines = src.splitlines(keepends=True)
        if kind == "markdown":
            cells.append({"cell_type": "markdown", "metadata": {}, "source": lines})
        else:
            cells.append({"cell_type": "code", "metadata": {}, "source": lines,
                          "execution_count": None, "outputs": []})
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python",
                           "name": "python3"},
            "language_info": {"name": "python", "version": "3.11"},
        },
        "nbformat": 4, "nbformat_minor": 5,
    }


if __name__ == "__main__":
    out = pathlib.Path(__file__).resolve().parent / "browsergraph-tour.ipynb"
    out.write_text(json.dumps(build(), indent=1), encoding="utf-8")
    n_code = sum(1 for k, _ in CELLS if k == "code")
    print(f"wrote {out}  ({len(CELLS)} cells, {n_code} code)")
