# Dimensions — What Else a Browser Run Varies Along

The six axes currently implemented (`engine`, `binary`, `transport`, `display`,
`stealth`, `llm.mode`) cover *how the browser is built*. They miss most of what
determines whether a run succeeds against a real target.

Below: the axes worth adding, the coupling problem they create, and where to
start.

---

## A. Network — the layer detection actually looks at

| Axis | Values | Why it matters |
|---|---|---|
| `proxy_type` | none / datacenter / residential / mobile / rotating | The single strongest signal separating "bot" from "user". Datacenter IPs are blocked by default on many targets. |
| `proxy_rotation` | none / per_run / per_request / sticky_session | Rotating mid-session breaks logins; not rotating gets the IP burned. |
| `tls_fingerprint` | default / chrome_ja3 / firefox_ja3 / randomised | Anti-bot vendors fingerprint the TLS handshake **before any JS runs**. Stealth JS cannot help here. |
| `http_version` | h1 / h2 / h3 | Fingerprintable, and mismatched with the claimed UA is a tell. |
| `request_policy` | all / block_media / block_ads / record_har | Blocking images is 3-5× faster; also changes the request pattern a site sees. |
| `dns` | system / doh / custom | Leak vector, and a way to pin a host during testing. |
| `throttle` | none / 3g / 4g / custom | Some flows only reveal race conditions under latency. |

`tls_fingerprint` is the one people discover last and matters most. It also
constrains `engine` — matching a Chrome JA3 from Firefox is incoherent.

## B. Fingerprint — finer than the current `stealth` axis

`stealth` currently conflates several independent things. Worth splitting:

| Axis | Values |
|---|---|
| `canvas` | off / noise_per_profile / noise_random |
| `webgl` | off / spoof_vendor / spoof_full |
| `audio_ctx` | off / noise |
| `fonts` | system / curated_list |
| `hardware` | real / spoof (cores, deviceMemory) |
| `webrtc` | default / proxy_only / disabled |
| `screen_vs_viewport` | coherent / independent |

**`noise_per_profile` vs `noise_random` is the important distinction.** Random
canvas noise on every page load is *itself* a detection signal — real browsers
are consistent. Stable-per-profile is what you want.

## C. Session and state

| Axis | Values |
|---|---|
| `cookies` | fresh / persisted / imported / pooled |
| `storage` | ephemeral / persisted |
| `profile_warmth` | cold / warmed / aged |
| `auth_state` | anonymous / authenticated / reauth_on_expiry |
| `session_pinning` | none / pin_to_proxy |

`profile_warmth` is underrated: a brand-new profile whose first action is a
login is a strong bot signal. Warming (some ordinary browsing first) changes
outcomes materially on hostile targets.

## D. Concurrency and pacing

| Axis | Values |
|---|---|
| `parallelism` | 1 / n / pool |
| `pacing` | immediate / fixed / jittered / active_hours |
| `rate_limit` | per_domain / per_account / global |
| `backoff` | none / fixed / exponential |

This host is memory-tight, so `parallelism` needs a hard cap and a memory gate,
not a best-effort semaphore.

## E. Resilience — where the Pinterest work is directly instructive

| Axis | Values |
|---|---|
| `retry` | none / fixed / exponential |
| `on_challenge` | abort / retry / solver / human_handoff |
| `escalation` | none / headless→headed / rotate_proxy / change_engine |
| `checkpoint` | none / per_node / per_run |

**`on_challenge=abort` should be the default.** Retrying into a CAPTCHA or a
block is how accounts get banned. A run that stops on the first challenge and
says so is more valuable than one that keeps trying.

## F. Verification — the dimension this session proved you cannot omit

| Axis | Values |
|---|---|
| `verify` | none / dom_assert / llm_verify / external_check |
| `verify_timing` | immediate / delayed / both |

The Pinterest investigation is the argument for making this first-class: **551
emails reported "sent" successfully and produced zero posts.** Every layer said
success; only an independent check of the destination revealed the truth.

`external_check` — verify at the destination, not in the automation — is the one
that catches silent failure. `verify_timing=delayed` matters when the effect is
asynchronous (a post appears a minute later).

## G. LLM — deeper than `mode`

| Axis | Values |
|---|---|
| `llm_location` | local_ollama / remote_ollama / cloud |
| `structured_output` | free_text / json / json_schema |
| `fallback_chain` | single / ordered_list |
| `context_budget` | tokens |
| `cost_cap` | per run / per day |
| `prompt_strategy` | zero_shot / few_shot / self_consistency |

`self_consistency` (N samples, take the majority) is worth having as a dimension
rather than a hardcoded choice — it trades tokens for reliability, and which you
want differs per task.

## H. Observability

| Axis | Values |
|---|---|
| `screenshots` | never / on_failure / every_step |
| `trace` | none / har / playwright_trace |
| `video` | off / on_failure / always |
| `redaction` | none / credentials / pii |

`redaction` is not optional once traces and HARs are being written — a HAR
captures auth headers and session cookies verbatim.

## I. Compliance

| Axis | Values |
|---|---|
| `robots` | ignore / respect |
| `politeness` | aggressive / normal / gentle |
| `artifact_pii` | keep / redact / drop |

Worth being explicit rather than implicit, so a graph records the posture it ran
under.

---

## The coupling problem

Several of these are **not independent**, and treating them as free axes
produces incoherent runs that are *more* detectable than naive ones:

- `identity.timezone` must match the proxy's IP geolocation. A residential
  Philippine IP with `America/New_York` is a stronger tell than no proxy at all.
- `identity.locale` should match too.
- `screen` must be ≥ `viewport`, and both should be plausible for the claimed device.
- `user_agent` must agree with `binary`, `http_version` and `tls_fingerprint`.
- `stealth=undetected` implies Selenium (already encoded).
- `session_pinning=pin_to_proxy` requires `proxy_rotation=sticky_session`.

**Suggestion: add a `coherence` check alongside `validate()`.** `validate()`
answers "can this run?"; `coherence()` answers "does this look like a real
browser?" They are different questions and both are needed.

---

## The explosion problem, and what to do about it

Six axes already give 3000 combinations (516 runnable). Adding the axes above
takes it past **10⁹**. Full enumeration stops being meaningful.

Three practical responses:

1. **Pairwise (all-pairs) coverage.** Most failures are caused by an interaction
   between *two* values, not five. A covering array tests every pair of values
   in tens of runs rather than billions. This is the single highest-leverage
   addition — see `browsergraph/sample.py`.

2. **Profiles over combinations.** Ship named, coherent bundles (`stealth_residential`,
   `fast_scrape`, `warmed_login`) as the primary interface, with free
   combination available for exploration.

3. **Constraint-first generation.** Generate only from the coherent subspace,
   rather than generating everything and filtering.

---

## Suggested starting points, in order

**1. `verify` (F).** Cheapest to add, highest value, and this session proved the
cost of omitting it. Without it every other dimension is measured against an
unreliable success signal.

**2. `proxy_type` + `session_pinning` (A/C).** The dimensions that most affect
whether real targets work at all. Everything else is optimisation on top.

**3. `on_challenge` + `escalation` (E).** Prevents the failure mode where
automation retries itself into a ban. `abort` as default.

**4. Pairwise sampling.** Do this before adding more axes, not after — it is
what keeps the space testable.

**5. `coherence()` checks.** Cheap, and prevents the "spoofed into being more
detectable" trap.

**6. Split `stealth` into (B).** Only once there is a real target to measure
against — otherwise it is untestable theory.

**Deliberately last: `canvas`/`webgl`/`audio` spoofing.** It is the most-written-
about and least-useful area until the network layer (A) is right. TLS and IP
reputation gate you long before canvas noise does.
