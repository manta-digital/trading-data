---
docType: review
layer: project
reviewType: slice
slice: serving-the-new-tiers
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/306-slice.serving-the-new-tiers.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261108
dateUpdated: 20261108
reviewedSha: e91a67ba5500fbb037796c9201a8ef0f9868c40e
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 6
effort: high
turns: 7
promptTokens: 515122
cachedTokens: 406300
cacheWriteTokens: 108810
completionTokens: 6738
durationSeconds: 61.9
costUsd: 0.544045
costBasis: provider
runId: run-20261108-slices-plan-d03f6d49
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Bar-source rule matches the architecture"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md#Overview"
  - id: F002
    severity: pass
    category: dependencies
    summary: "Dependency direction and integration points are correct"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md#Dependencies"
  - id: F003
    severity: pass
    category: error-handling
    summary: "Failure modes are enumerated for the new I/O paths"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md#D7"
  - id: F004
    severity: concern
    category: proportionality
    summary: "Design is heavier than the plan entry requires; several parts can be cut or folded"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md#D7"
  - id: F005
    severity: note
    category: specification
    summary: "Meaning of `incomplete` for bars is thin on content-complete findings"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md#Data-Flow"
  - id: F006
    severity: note
    category: nfr
    summary: "Response compression is inherited, not restated"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md#D6"
  - id: F007
    severity: note
    category: data-semantics
    summary: "Close-stamped stub bucket ships a known artifact"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md#Special-Considerations"
  - id: F008
    severity: note
    category: integration
    summary: "`source` convention for 308's tick bars is claimed but not defined"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md#Provides-to-Other-Slices"
---

# Review: slice — slice 306

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Bar-source rule matches the architecture

The slice implements the architecture's "Which source serves bars" and "Never splice tiers silently" as written:
- Provider `ohlcv-1m` is the only time-bar source. 1m is served as stored, and coarser sizes are rolled up on the CME session.
- Ticks never fill a gap, and a session with no provider bars goes to `incomplete` (Data Flow, D2, Success Criterion 1).
- Every response names its source through `BAR_SOURCE` (D2, D4).
- The read-time tick SQL survives only as the parity check (D7).
- Provider `ohlcv-1h` and `1d` are neither archived nor served, and D1 records the bucketing check the architecture and slice plan ask for.

### [PASS] Dependency direction and integration points are correct

The slice consumes 301, 302 and 303 and adds only a family parameter to 230's plan code. Existing callers keep `TICK_TIER`, so their behaviour is unchanged (D3). `verdicts_for(family)` removes any chance of pairing a family with the wrong verdict reader. The slice gives 307 and 308 what they need: bars served as soon as they are bought, a bars path that no longer reads `tick_trade`, and a plan that takes a family. No layer boundary is crossed. The route layer calls the reader, the reader calls the table, and `resolved` gains an optional filter without changing existing callers.

### [PASS] Failure modes are enumerated for the new I/O paths

Each new path states its failure handling:
- **Statistics route:** the 422 row bound is checked before the read, and `tick_errors` maps failures to 503 and 504.
- **Parity:** one read-only snapshot, a report printed only after the last comparison, and an explicit mapping through `tick_exit.exit_code_for` (exit 1 for preflight failures, exit 4 for connection drops and statement timeouts). Exit 3 therefore always means a finished comparison with findings.
- **Bars:** the routes reuse `read_series` and its row ceiling.
- **Long reads:** D10 sets a 10 s bound with a stated fallback.

### [CONCERN] Design is heavier than the plan entry requires; several parts can be cut or folded

The plan entry (effort 3/5) assigns:
- the provider-bar source rule;
- retiring the tick bars and keeping them "only as the parity check";
- `source` on responses;
- routes for open interest and settlement, or a recorded decision not to serve them;
- docs;
- the D1 bucketing check.

The design adds this on top, and it can be trimmed:
- **Close-minute output (D7).** Issue #70's close-minute lines are a separate output block, tested separately. They are the minute list filtered to `bucket = close_ns`. Fold them into the `--json` non-agreeing-minute listing, or a single filter flag, instead of a distinct report section with its own spec.
- **D10 measurement.** It needs a synthetic 1,300-session data generator that shifts the derived bar day across sessions. Measure the worst read once on the real ES 2024 bars in Phase 7. The batch-into-one-statement fallback stays named as the remedy. This also removes a test-data mechanism that exists only for one measurement.
- **Parity arguments.** `--product/--rule` mode is not needed for the one stated use, which is the ES 2024 window. A contract-or-product choice already exists in the shared plan code, so the saving is small. Confirm the second mode is actually used before specifying it.
- **Statistics route.** The plan allows "routes, or a recorded decision", and this design takes the heaviest option: new models, a pivot module, a new route file, and a change to 303's `resolved`. D6 argues the value (a research input, and "one shared store"). Because the argument rests on the product-level route that D6 itself defers to issue #73, state which consumer needs the contract-only route now. Otherwise this is a candidate to defer with the product route.
- **Repeated content.** The Verification Walkthrough repeats Functional Requirements 1–7, and the Migration Plan table repeats the Component Structure. Shortening both would cut review load without losing information.

None of these contradicts the architecture, but together they make the slice larger than the plan's effort estimate.

### [NOTE] Meaning of `incomplete` for bars is thin on content-complete findings

The architecture defines bar completeness as load-complete plus content-complete. Content-complete includes the check that `ohlcv-1m` volume does not exceed `statistics` cleared volume. The slice says `incomplete` is "BAR_1M verdicts" via `session_verdicts`. It does not say whether a session that is load-complete but fails 303's cross-tier volume check appears in `incomplete`. It should state this in one line and match the reference docs to it. The doc update to `reference.md` is the natural place.

### [NOTE] Response compression is inherited, not restated

The architecture's Serving section says responses are gzipped at level 1 and encoded off the event loop. The statistics route goes through `send_timeseries`, which presumably carries this. One sentence saying the new route inherits it would close the gap. No target is missing, because the NFR has no number beyond level 1.

### [NOTE] Close-stamped stub bucket ships a known artifact

At 5m, 15m and 1h on a regular 23-hour session, the closed-interval rule produces a one-minute stub bucket stamped at the close. The slice documents this in the CHANGELOG and `reference.md` §6.1 and ties the rule to issue #70, so the decision is explicit. The Value section's "a bar means the same thing in every era" holds only after #70 resolves. If #70 finds the rule wrong, consumers will have seen the stub in the meantime. This is acceptable because no consumer calls the futures routes yet.

### [NOTE] `source` convention for 308's tick bars is claimed but not defined

Provides-to lists "the `source` convention for its tick-bar responses" for 308. `BAR_SOURCE` maps `Granularity → TickSchema`, which covers time bars only. The slice does not define what N-trade bars would put in `source`. Either narrow the claim to "the `source` field and its schema-name values", or leave the mapping for 308 to define.

### Run Digest

- Response length: 7078 chars
- Response is newline-free: no
- Tool calls made: 6
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: high
- Turns: 7
- Tokens — prompt / cached / cache write / completion / reasoning: 515122 / 406300 / 108810 / 6738 / not computed
- Duration: 61.9 s
- Cost: $0.54 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8

## Resolution

- **[PASS] Bar-source rule matches the architecture:** no change needed.
- **[PASS] Dependency direction and integration points are correct:** no change needed. `verdicts_for` now covers `TICK_TIER` and `BAR_1M` only, because statistics plans moved to issue #73.
- **[PASS] Failure modes are enumerated for the new I/O paths:** no change needed.
- **[CONCERN] Design heavier than the plan entry requires:** applied in parts.
  - *Close-minute output:* folded. Close-stamped minutes carry `at_close`, appear in the `--json` minute list whatever their class, and are counted per session. There is no separate report section.
  - *D10:* moved to Phase 7 on real data: product `1d` over the held ES 2024 year through the production API, extrapolated to 1,300 sessions. The synthetic data generator and Phase 6 criterion 11 are removed, and batching stays the one-line fallback.
  - *Parity `--product/--rule` mode:* rejected. The one stated use (ES 2024-08-30 to 2025-01-01) crosses ES's September and December rolls, so no single contract covers it. Both modes come from the shared plan code at no extra cost.
  - *Statistics route:* deferred. No consumer has asked for it, so D6 is now the recorded decision not to serve statistics in 306. The contract and product route drafts are in issue #73. Models, the pivot module, the route file and the `resolved` filter are all removed.
  - *Repeated content:* rejected. The walkthrough is the production demo script the slice-design guide requires, not a restatement of criteria. The Migration Plan table is the consumer map the guide requires for a migration, while the Component Structure lists files.
- **[NOTE] Meaning of `incomplete` for bars:** applied. Data Flow now states that `incomplete` carries the load verdict only. A load-complete session that fails 303's cross-tier volume check is served and is not listed, because that finding belongs to `coverage` and 305. `reference.md` will say the same.
- **[NOTE] Response compression is inherited:** applied as one sentence in Data Flow: responses go through `send_timeseries`, which applies gzip at level 1, off the event loop.
- **[NOTE] Close-stamped stub bucket:** no change. It is already disclosed in the CHANGELOG and `reference.md` §6.1 and tied to issue #70, and no consumer calls the futures routes yet.
- **[NOTE] `source` convention for 308:** applied. The claim is narrowed to "the `source` field, whose values are provider schema names", and 308 defines what its N-trade bars put there.
