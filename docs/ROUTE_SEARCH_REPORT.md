# Choosing a route out of 3,802,314,700,800

*A measured report on policy gating and route search over the browsergraph
workbench. Every number here was produced by the commands shown; none was typed
in by hand.*

![one stage decomposed into its sub-steps](stage-decomposition.png)

*One stage, three sub-steps, all 80 candidates.
[The full 14-sub-step network →](route-network.png)*

---

## The space, and how much of it a coarse view hides

```
6 stages / 14 sub-steps · 57 definitions · 166 atomic candidates
3,802,314,700,800 complete primary routes · 1,337 adjacent transitions
```

Routes **multiply** — that is why a search is needed rather than a table of
recommendations. But the more interesting number is what decomposition revealed.
Pool every candidate in a stage into one choice, which is what a coarse diagram
implicitly claims, and you count **85,747,200**. The sub-steps those same stages
are made of expose **3,802,314,700,800**.

**The coarse view was hiding 44,343× of the space.** Same task, same registry,
same code — six stages, but "Acquire inputs" is three decisions, not one.

## Policy is a hard gate, and it runs first

A candidate that lacks a required permission is not a low-scoring candidate; it
is an unavailable one. No objective weighting may promote it. Under a
locked-down policy — no browser, no network, no LLM, no external state changes,
deterministic runtimes only:

```bash
browsergraph route --gates --deterministic --no-effects \
  --allow filesystem --allow filesystem:read --allow filesystem:write \
  --allow database --allow database:read
```

| stage | sub-step | eligible | of |
|---|---|---:|---:|
| Acquire inputs | Resolve target | 4 | 4 |
| | Open session | 7 | 70 |
| | Read payload | 3 | 6 |
| Canonicalize | Decode bytes | 6 | 6 |
| | Parse structure | 8 | 11 |
| | Normalize values | 12 | 12 |
| Enrich | Plan context | 5 | 5 |
| | Attach evidence | 3 | 9 |
| Transform or act | Locate target | 5 | 6 |
| | Apply or act | 4 | 14 |
| Verify | Check shape | 3 | 3 |
| | Check independently | 5 | 9 |
| Emit | Persist result | 3 | 7 |
| | Write receipt | 3 | 4 |

**1,959,552,000 complete routes remain, out of 3,802,314,700,800.** The policy
removed 99.95% of the space before a single score was computed — and every removal
states its reason:

```
demo.acquire.browser_adapter.chrome.browserport.headless…  needs browser, network — not granted
demo.enrich.embedding_enricher.small…                      not deterministic
demo.emit.database_writer.append…                          changes external state (external:row-state) — not permitted
```

Blocked candidates stay **visible**. Filtering them out silently would answer
"what could perform this step" with "what the policy left", and nothing on
screen would distinguish the two.

## Scoring had to be fixed before any of this meant anything

The first implementation combined raw metric values directly:

```
score = 0.5·quality − 0.25·latency_ms − 0.25·cost_usd
```

A quality of 0.97 was being weighed against a latency of 1420 — three orders of
magnitude apart. The latency term swamped everything, and **all four profiles
produced an identical ranking**:

| profile | ranking before the fix |
|---|---|
| Balanced | fastest > cheapest > accuracy_first > learned > human_in_the_loop |
| Quality first | *identical* |
| Speed first | *identical* |
| Cost first | (only this one differed) |

"Balanced" and "quality first" were secretly both speed-only. Nothing failed;
the numbers just meant nothing. Min-max normalization within the comparison set
puts every objective on the same 0..1 footing, so a weight of 0.5 buys half the
decision. Afterwards:

| profile | ranking |
|---|---|
| Balanced | accuracy_first > learned > cheapest > fastest > human_in_the_loop |
| Quality first | accuracy_first > learned > **human_in_the_loop** > cheapest > fastest |
| Speed first | accuracy_first > learned > cheapest > fastest > human_in_the_loop |
| Cost first | accuracy_first > learned > cheapest > fastest > human_in_the_loop |

The human-in-the-loop route — highest quality, 35 minutes, $5.50 — moves from
last to third under *quality first* and stays last everywhere else. That is the
profile doing its job.

## Three strategies, and where the cheap one loses

```bash
browsergraph route --compare --profile profile.balanced --deterministic --no-effects \
  --allow filesystem --allow filesystem:read --allow filesystem:write \
  --allow database --allow database:read
```

| profile | greedy (71 evals) | beam (512 evals) |
|---|---:|---:|
| Balanced | 0.8156 | 0.8156 |
| Quality first | 1.0149 | 1.0149 |
| **Speed first** | 0.9608 | **1.0304** |
| Cost first | 0.3443 | 0.3443 |

Exhaustive is no longer on this table, and that is the point: decomposition
pushed the gated space to 1.96 **billion** routes, well past any enumeration
limit. Before sub-steps the gated space was 122,472 and exhaustive was a real
option. It is exactly when the space stops being enumerable that the choice of
strategy starts to matter.

Greedy matches beam on three profiles and loses on the fourth, which is what you
would predict: it scores each sub-step in isolation, and route metrics do not
decompose. **Quality compounds** — it is the product across
stages, not the mean, because a route is only as good as the joint probability
that every step did its job. Averaging would let one excellent stage hide a step
that fails half the time.

### The beam was broken, and the measurement is what caught it

The first beam renormalized within each step's own set of surviving partials, so
the yardstick moved at every stage: a genuinely good prefix got pruned because
it looked ordinary against whatever happened to survive beside it. The symptom
was unmistakable once measured —

| beam width | 1 | 8 | 32 | 128 | 512 |
|---|---:|---:|---:|---:|---:|
| score (before the fix) | 0.6952 | 0.6952 | 0.6952 | 0.6952 | 0.6952 |
| routes examined | 56 | 385 | 892 | 2,812 | 8,878 |

*(measured on the flat six-stage registry, before sub-steps)*

— beam matched plain greedy at *every* width, spending 8,878 evaluations to
reach the answer greedy found in 56. **A search whose width buys nothing is not
a search.** Scoring partial routes against fixed reference ranges fixed it.

A second bug surfaced the same way: normalization originally recomputed min/max
inside the scoring call, making ranking O(n²). An exhaustive pass over 122,472
routes did roughly fifteen billion comparisons and never finished. Nothing was
*wrong* with the answer — it just could not be reached, which for a search
strategy is the same thing.

## What a proposal reports

```bash
browsergraph route --profile profile.quality
```

```
beam search under 'profile.quality' — score 1.466, better than 100.0% of the reference sample
  Resolve target               literal
  Open session                 filesystem · directory
  Read payload                 whole
  Decode bytes                 bom
  Parse structure              format · csv
  Normalize values             units · imperial
  Plan context                 rules
  Attach evidence              provenance
  Locate target                recorded
  Apply or act                 human
  Check shape                  invariants
  Check independently          consensus · majority
  Persist result               file · columnar
  Write receipt                bundle · full
  route metrics: quality 0.644 (compounded) · 902,720ms · $2.5200
  examined 1,020 of 3,802,314,700,800 eligible routes (0.0%) — 3,802,314,700,800 exist before policy
  needs: filesystem:read, filesystem:write, human
  note: auto chose beam: 3,802,314,700,800 eligible routes exceeds the 200,000 enumeration limit
  note: beam width 8
  note: scored against a fixed 512-route reference sample, so strategies stay comparable
```

Three things in that output are deliberate:

**`examined 1,020 of 3,802,314,700,800 … (0.0%)`.** A search that looks at a
thousand routes out of trillions and
announces "the best route" without saying so is making a claim it did not earn.
The number is free to carry.

**`needs: filesystem:read, filesystem:write, human`.** The union of authority the
whole route requires. Per-candidate, each permission looks small; the union is
what actually has to be granted.

**The score.** A normalized score *can* exceed 1 — the reference sample sets
the scale, and a good search finds routes better than anything sampled. That
reads like a bug, so the headline is the percentile, which cannot.

## Whole-route budgets are re-checked after the route exists

A route assembled entirely from individually affordable candidates can still
break a whole-route budget. Proposals are re-gated against the complete route,
and the message says exactly that:

```
PROBLEM: the whole route costs $0.0412, over the $0.03 budget
         — every candidate was individually affordable
```

## Reproducing all of it

```bash
pip install -e ".[dev]"
browsergraph route --gates                    # what the policy blocks, and why
browsergraph route --compare                  # greedy vs beam vs exhaustive
browsergraph route --profile profile.quality --json
browsergraph workbench -o studio.html         # the interactive five-view studio
pytest tests/test_workbench.py tests/test_search.py -q   # 770 tests overall
```

## What is still an illustration

Every metric in the demonstration registry carries
`"source": "illustrative-prior"`, and a test enforces it. They exist to give the
search something to sort by. **Real optimization consumes real receipts** —
these numbers demonstrate that the machinery ranks, gates and reports correctly,
not that any particular candidate is good.
