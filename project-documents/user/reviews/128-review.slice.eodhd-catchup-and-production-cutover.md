---
docType: review
layer: project
reviewType: slice
slice: eodhd-catchup-and-production-cutover
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/slices/128-slice.eodhd-catchup-and-production-cutover.md
aiModel: z-ai/glm-5.1
status: complete
dateCreated: 20260427
dateUpdated: 20260427
findings:
  - id: F001
    severity: pass
    category: scope-boundary
    summary: "Initiative 140 boundary handled with explicit handoff plan"
  - id: F002
    severity: concern
    category: scope-creep
    summary: "Speculative `bar_flags` column deviates from architecture principle"
  - id: F003
    severity: concern
    category: dependency-coordination
    summary: "Backfill and minute daemon concurrent operation not addressed"
  - id: F004
    severity: pass
    category: error-handling
    summary: "Error handling thoroughly enumerates failure modes for new I/O paths"
  - id: F005
    severity: pass
    category: dependency-direction
    summary: "Acquisition state centralization follows architecture principle"
  - id: F006
    severity: note
    category: nfr-tracking
    summary: "Architecture \"caught-up\" NFR definitions not explicitly restated for new paths"
  - id: F007
    severity: note
    category: data-seeding
    summary: "NVDA inaugural gap entry conflates schema migration with operational data"
  - id: F008
    severity: pass
    category: integration-points
    summary: "Provider compatibility contract aligns with architecture extensibility goal"
  - id: F009
    severity: pass
    category: integration-points
    summary: "Structured events match architecture-mandated schema"
---

# Review: slice — slice 128

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.1

## Findings

### [PASS] Initiative 140 boundary handled with explicit handoff plan

The architecture document assigns "is our data correct" to Initiative 140 and "fetch what's needed" to Initiative 120. Slice 128 ships coverage checking and Stage B verification — capabilities the architecture originally placed in 140's scope — but does so with full transparency: Decision 15 explains the operational necessity (Stage A is blind to provider data gaps, NVDA case proves it), the architecture document itself was updated with a boundary refinement paragraph, and Success Criterion 14 requires a formal handoff note in Initiative 140's architecture document. The analytical extensions (trending, dashboards, multi-provider cross-validation) remain explicitly out of scope. This is well-managed boundary work.

### [CONCERN] Speculative `bar_flags` column deviates from architecture principle

The architecture's principle is "reuse what exists, rewrite what's broken." The slice adds `bar_flags INTEGER NOT NULL DEFAULT 0` to `minute_ohlcv` with no reader or writer populating it, acknowledging in Decision 9 that "speculative columns generally don't align with that" principle. The cost-asymmetry justification (metadata-only ALTER on an empty/test hypertable now vs. costly ALTER on a production hypertable later) is reasonable on its own terms, but the slice's own language — "One-time exception, not a pattern" — confirms this is a principled deviation. The concern is that encoding exceptions in shipped schema makes them de facto patterns: once the column exists in production, future slices will naturally build on it rather than questioning whether it should have been added at all. A safer approach would be to defer the column to the slice that actually populates it, accepting the higher migration cost as the correct incentive to avoid premature schema expansion, or to document the exception in the architecture document itself so it is governed at that level rather than slice level alone.

### [CONCERN] Backfill and minute daemon concurrent operation not addressed

The slice introduces `mt data minute backfill` as a long-running CLI command (~21 days for full universe) that drives the same per-symbol minute-update path and `acquisition_state` watermarks as the minute daemon. The architecture states "CLI and daemon share the same orchestrator and state." However, the slice does not specify whether the minute daemon should be paused/stopped during backfill, or how concurrent operation is coordinated. If both run simultaneously: (a) they may redundantly fetch the same symbol's data, wasting EODHD quota that the backfill's quota guard does not account for (the guard tracks backfill calls only, not daemon calls), potentially causing daily-limit overruns; (b) concurrent UPSERTs on the same `acquisition_state` rows could cause either process to see stale watermarks and re-fetch already-completed work. The architecture explicitly discusses shared rate-limit coordination between daemons as a concern that must be handled. The slice should state the operational policy (e.g., "minute daemon must be stopped during backfill" or "backfill and daemon coordinate via a shared daily-call counter") and describe the failure mode if the policy is violated.

### [PASS] Error handling thoroughly enumerates failure modes for new I/O paths

The slice's "Error handling and failure modes for new HTTP paths" section explicitly classifies and provides handling strategies for: connection timeout, read timeout, connection hang, peer disconnect mid-response, HTTP 4xx (except 429), HTTP 429 with Retry-After handling, HTTP 5xx, and per-endpoint-specific handling for CA ingest and Stage B. No failure mode is left as "TBD" or implicit. Each classification includes the action (retry with backoff, mark failed and continue, exit with diagnostic), the retry policy, and the propagation behavior. This directly satisfies the architecture's "Fail loud, retry smart" principle and the review criterion requiring explicit handling strategies.

### [PASS] Acquisition state centralization follows architecture principle

The architecture mandates: "All acquisition state (watermarks, run status, error tracking) is centralized on the TimescaleDB host, regardless of where the data itself is written." Decision 4 places `coverage_gaps` and `backfill_state` on the TimescaleDB host alongside `acquisition_state`, with explicit reasoning that these are operational metadata about acquisition progress — the same category the architecture centralizes. An earlier draft placed them on the daily DB and was corrected during review. This aligns with the architecture's centralization principle.

### [NOTE] Architecture "caught-up" NFR definitions not explicitly restated for new paths

The architecture's "Envisioned State" section defines caught-up targets: daily daemon — every active symbol within 2 trading days of today; minute daemon — every active symbol's watermark within 1 trading day, no known fillable gaps. The slice touches both daemon cycles (daily now includes CA ingest; minute gains backfill mode) but does not restate these targets as specific NFRs for the new paths. The backfill rate budget (100K calls/day, ~21 days for 5K symbols) addresses throughput implicitly, and the quota guard mechanism supports the caught-up goal, but explicit restatement would make the NFR traceability clearer per the evaluation criterion. This is informational — the architecture's caught-up definitions are descriptive rather than formal NFRs with hard latency/throughput numbers.

### [NOTE] NVDA inaugural gap entry conflates schema migration with operational data

The slice acknowledges in scope item 5 that baking the NVDA gap INSERT into migration 015 conflates schema evolution with operational data seeding, calling it an "acknowledged trade-off" acceptable for a single inaugural entry that documents a known case. This is a style concern, not an architectural violation — the entry is reversible (operator can update `resolution_status` if the gap is eventually filled). However, it establishes a precedent that future slices should not follow; the slice itself notes "future bulk seed data should use a separate seed mechanism, not a migration."

### [PASS] Provider compatibility contract aligns with architecture extensibility goal

The slice documents a provider compatibility contract (scope item 11, to be recorded in the adjustment ADR) specifying minimum requirements for any provider stack to function with the 127+128 pipeline: raw intraday OHLCV, raw daily close, complete splits and dividends. Providers known to satisfy (EODHD, Polygon) and fail (Yahoo) are listed. This directly supports the architecture's goal of "Extensible to futures and new providers without redesign" and the `ICorporateActionsProvider` seam (Decision 2) mirrors slice 127's proven minute-provider pattern.

### [PASS] Structured events match architecture-mandated schema

Success Criterion 15 requires unit tests asserting that each new event type (`ca_ingest_splits`, `ca_ingest_dividends`, `verify_eod`, `backfill_symbol`, `quota_sleep`, `quota_window_advance`) emits events with the architecture-mandated fields: `run_id`, `symbol`, `granularity`, `provider`, `action`, `status`, `rows_fetched`, `time_range`, `duration_ms`, `error`, `timestamp`. This matches the architecture's structured event schema exactly and uses the existing `JsonlEventSink` from slice 121, consistent with the architecture's statement that "the daemon starts emitting events from day one."
