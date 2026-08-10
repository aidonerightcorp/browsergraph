# How it works, in plain English

*No jargon, no diagrams for the first half. If you only read one document about
this project, read this one.*

---

## The problem, stated once

You want a program to go to a website and get something — a price, a
confirmation, a file. The obvious way is to write down the steps: open Chrome,
go to the URL, click the button, read the number.

That works until it doesn't, and it stops working for boring reasons. The site
adds a bot check, so Chrome needs to look less like a robot. The button moves,
so the selector breaks. The page renders its price in a `<canvas>`, so there is
no text to read. The click succeeds and changes nothing, and your program
happily reports success.

The usual response is to patch the script. Add a `sleep`. Add a `try`. Swap
Playwright for Selenium. Six months later you have four scripts that each work
on one site, and nobody remembers why.

**This project takes a different position: don't write the steps. Write down
what has to be true, list everything that could make it true, and let the
program pick.**

---

## The idea in one page

### 1. A task is a list of requirements, in order

Not "click the button" — that is an implementation. The requirement is *"the
form has been submitted"*. Before that, *"the target element has been located"*.
Before that, *"the page has loaded and settled"*.

We call each requirement a **stage**. A stage says four things: what it takes
in, what it must produce, what proves it worked, and what kind of thing is
allowed to perform it. Nothing about *how*.

### 2. Every stage has many things that could perform it

"Open a browser session" can be done by Playwright driving Chrome headless, or
Selenium driving Firefox headed, or a stealth-patched Chromium, or — if the page
needs no JavaScript — no browser at all.

Each of those complete, concrete options is a **candidate**. Not "a browser
adapter" — that is a family. `Playwright · Firefox · headless` is a candidate,
and `Playwright · Firefox · headed` is a different one, because you can pick
either and they behave differently.

This matters more than it sounds. One browser adapter with 5 controllers,
6 browsers and 2 display modes is **60 candidates**, not one. Drawing it as one
box hides 59 decisions you are entitled to make.

### 3. A route is one candidate per stage

Pick one option at each stage, in order, and you have a complete plan: a
**route**. That is what actually runs.

And now the interesting part. If stage one has 70 options and stage two has 12
and stage three has 9, the number of possible routes is not 70 + 12 + 9. It is
70 × 12 × 9. **Routes multiply.**

In the shipped demonstration, that product is **3,802,314,700,800** — nearly
four trillion distinct ways to do one task. Not because the task is exotic, but
because fourteen ordinary decisions with a handful of options each is what four
trillion looks like.

That number is the whole argument. You cannot hand-pick from four trillion. You
need something that picks for you, from evidence, and can tell you why.

---

## How the matrix gets built

Nobody writes out four trillion routes. The matrix is *derived*, in four steps,
and each step is mechanical.

**Step one — every tool describes itself.** A node ships a manifest: what it can
do, what types it accepts and produces, what settings it has and what values
those settings can take, what permissions it needs, what it might change in the
world, and how fast and how good it has historically been. Crucially the
manifest is separate from the code, so a registry can reason about a tool that
is *not installed on this machine* — or not written in Python, or not written
yet.

**Step two — settings expand into candidates.** A manifest saying `controller:
one of five` and `browser: one of six` and `display: one of two` is not one
option. The expansion produces all sixty, each with a stable identity, so
evidence gathered about `Playwright · Firefox · headless` attaches to *that*
and not to browsers in general.

**Step three — each stage collects everything compatible.** For each stage, the
system asks every candidate three questions: can you do the kind of thing this
stage needs, will you accept what the previous stage produces, and will you
produce what the next stage needs? Everything that passes all three is admitted.

This is a rule, not a courtesy. **A stage must show every compatible candidate.**
If it quietly dropped the ones that scored badly last time, the picture would be
a summary of somebody's old opinion, and nothing on screen would tell you.

**Step four — sub-steps, where a stage was hiding structure.** "Acquire inputs"
looks like one decision. It is three: work out *what* to fetch, open something
capable of fetching it, then read the payload out of it. Each has its own
matrix. Stages nest recursively, to any depth.

This is not tidying. Pool every candidate in each stage into one choice — what a
coarse diagram implicitly claims — and you count 85 million routes. The
sub-steps expose 3.8 trillion. **The coarse view was hiding 44,343× of the
space**, from the search as well as from the reader.

---

## How a route gets chosen

Four things happen, always in this order. The order is the design.

### First: what is even allowed here?

Before anything is scored, every candidate is checked against a **policy** —
what this task is permitted to do. No network. No browser. No language model. No
changing anything outside this machine. Deterministic tools only. A budget.

A candidate that lacks a permission is not a low-scoring candidate. It is an
**unavailable** one, and no amount of "but it scores well" may promote it. Get
this backwards and you build a system that confidently recommends something it
is not allowed to run, then fails at execution having already reported a plan.

On the demonstration, a locked-down policy takes 3.8 trillion routes down to
1.96 billion — **99.95% removed before a single score is computed.** And every
removal states its reason:

```
browser · Chrome · headless      needs browser, network — not granted
embedding enricher · small       not deterministic
database writer · append         changes external state — not permitted
```

Blocked options stay **visible**, greyed out with the reason. Hide them and you
have silently changed the question from "what could do this?" to "what did the
policy leave?", and the screen looks identical either way.

### Second: what does "better" mean here?

Not a fixed answer. An **objective profile** is data: *quality matters half,
speed a quarter, cost a quarter*, or *quality is 80% of the decision*, or *cost
above all*.

One subtlety worth stating because getting it wrong is invisible. You cannot
weigh a quality of 0.97 against a latency of 1420 directly — one is a fraction,
the other is milliseconds, and the milliseconds win by three orders of
magnitude no matter what weights you write down. This project shipped that bug:
all four profiles produced *identical* rankings, and "balanced" was secretly
"speed-only". Nothing failed. The numbers just meant nothing. Everything is now
normalized within the set being compared, so a weight of 0.5 genuinely buys
half the decision.

### Third: search, and admit how much you looked at

Three strategies, because the honest choice depends on how big the space is:

- **Exhaustive** — when the space is small enough, check every route. Then
  "best" means best rather than best-found.
- **Beam** — keep the most promising partial routes as you go. For a space in
  the billions, this is the only realistic option.
- **Greedy** — take the best option at each stage independently. Fast, and
  sometimes wrong, because route quality *compounds*: a route is only as good as
  the joint probability that every step worked. Averaging would let one
  excellent stage hide a step that fails half the time.

Every result says how much of the space it examined. A search that looked at
1,020 routes out of 3.8 trillion and announces "the best route" is making a
claim it did not earn. The number costs nothing to carry, so it is always there.

### Fourth: check the whole route, not just its parts

A route assembled entirely from individually affordable candidates can still
blow a whole-route budget. So after a route exists, it is re-checked as a route:

```
PROBLEM: the whole route costs $0.0412, over the $0.03 budget
         — every candidate was individually affordable
```

---

## How fallback and adjustment actually work

This is the part people usually mean by "self-healing", and it is worth being
precise, because most of what gets called self-healing is a retry loop wearing a
costume.

### Fallbacks are alternatives for a step, not extra steps

A route can name backups per stage: *for "verify", try the oracle, then a human
review.* If the primary fails, the next one is tried **in the same position**.
A fallback never becomes a new stage, and it never reorders anything. The shape
of the plan is stable; only the occupant of one slot changes.

### The diagnosis chooses the fallback

Retrying the same thing is not adaptation. What matters is *what kind* of
failure happened:

| what happened | what that implies | what changes |
|---|---|---|
| Element never appeared, on an engine with no JavaScript | The page needs rendering | Switch engine, not timeout |
| Timeout, everything else fine | Might be transient | Retry — but a bounded number of times |
| Page says "unusual traffic" | You are detected | Stop. Escalating here earns a ban |
| Element found, click did nothing | Wrong element, or an overlay | Re-locate, don't re-click |
| Value in the DOM but invisible on screen | Rendering or font failure | Verify by OCR instead |

That last row is real. A page rendered here with a perfect layout and **not one
glyph** — every data-level check passed, the value was correct, the picture was
blank. Nothing caught it except reading the pixels.

Retries are **bounded per configuration**. An unbounded retry never reaches the
rest of the ladder, so the "smart" fallback logic behind it might as well not
exist. That was also a real bug here.

### Adjustment is evidence changing the ranking, not a special mode

Every run writes a **receipt**: which route ran, which engine and browser, how
long each step took, what each step wrote, which artifacts were produced and
their content hashes, which steps were *verifiers*, whether anything changed
remote state without being checked, and the exact command to run it again.

Receipts are written for failures too. The reflex is to record success and skip
the rest, which throws away the only runs that had something to teach.

Then: outcomes update the evidence, the evidence changes the ranking, and the
next run's search proposes a different route. There is no "learning mode". The
picker just has better numbers.

Three rules keep that honest:

1. **A proposal is re-gated before it runs.** Evidence can promote a candidate
   that policy still forbids. Scoring never bypasses permission.
2. **An unmeasured option is skipped, not scored zero.** Score it zero and
   anything new is punished for being new, and the system stops exploring
   without anyone deciding that it should.
3. **The producer does not get to grade its own work.** Verification comes from
   something independent — a different source, a re-computation, a consensus, a
   person, or the rendered pixels.

### Learning is not part of the task

The task runs left to right. Receipts come out the side. Feedback channels are
typed and labelled — contract failure, execution diagnosis, independent verdict,
quality, latency, cost, policy, drift — each saying who emits it, who may
consume it, and what response it authorises.

Drawn as backward arrows among the steps, none of that is actionable, and the
diagram stops saying what runs in what order. Which was the only thing it was
for.

---

## What the machine refuses to do

Worth listing, because each of these produces something that *looks* fine:

- run a graph on an engine that cannot do what the graph needs — checked before
  a browser is even launched, with a list of engines that could;
- let a good score override a missing permission;
- show a stage that omits a compatible candidate;
- satisfy a required stage with a "skip this" placeholder;
- connect two steps whose types do not match — insert an adapter, do not coerce;
- report success for a step that changed something with nothing verifying it;
- quietly do nothing when asked for a capability the engine lacks. A silently
  skipped upload is a run that reports success and uploaded no file.

---

## Try it

```bash
browsergraph capabilities              # what each engine can actually do
browsergraph route --gates             # what a policy blocks, and why
browsergraph route --compare           # greedy vs beam vs exhaustive, measured
browsergraph models                    # which model for which job, and why
browsergraph workbench -o studio.html  # all of it, five views, one offline file
```

## In one paragraph

Describe what must be true, in order. Let every capable tool declare itself.
Expand settings into concrete options so the real choices are visible. Filter by
what this task is *permitted* to do, before anything is scored. Search the
survivors under an objective you can state out loud, and admit how much of the
space you looked at. Run the winner, verify it with something that did not
produce it, and write down what happened — including the failures. Next time,
the same machinery picks better, and can tell you exactly why it changed its
mind.

---

*Deeper reading: [UNIVERSAL_GRAPH_SYSTEM.md](UNIVERSAL_GRAPH_SYSTEM.md) for the
formal model and the wire format; [docs/ROUTE_SEARCH_REPORT.md](docs/ROUTE_SEARCH_REPORT.md)
for the measured numbers and the three bugs that produced them;
[CONTRACTS.md](CONTRACTS.md) for what a node promises.*
