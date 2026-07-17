---
docType: review
layer: project
reviewType: slice
slice: coverage-aware-minute-gap-seeding
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/162-slice.coverage-aware-minute-gap-seeding.md
aiModel: z-ai/glm-5.2
status: complete
dateCreated: 20260716
dateUpdated: 20260716
findings:
  - id: F001
    severity: pass
    category: alignment
    summary: "Core design aligns with architecture's session-granular gap model"
    location: 162-slice.coverage-aware-minute-gap-seeding.md#architecture
  - id: F002
    severity: concern
    category: error-handling
    summary: "Failure modes not enumerated for the new batch cagg query I/O path"
    location: 162-slice.coverage-aware-minute-gap-seeding.md#component-1--build_minute_coverage_index-new
  - id: F003
    severity: concern
    category: nfr-restatement
    summary: "NFR conflict: history-start resolution goes to 2004, not the architecture's 24-month target"
    location: 162-slice.coverage-aware-minute-gap-seeding.md#the-bug-current-behavior
  - id: F004
    severity: note
    category: architectural-boundary
    summary: "Coverage sourced from continuous aggregate instead of raw data table"
    location: 162-slice.coverage-aware-minute-gap-seeding.md#the-fix--batch-coverage-index--day-granularity-diff
  - id: F005
    severity: note
    category: scope
    summary: "History window discrepancy is pre-existing and partially out of scope"
    location: 162-slice.coverage-aware-minute-gap-seeding.md#technical-scope
---

# Review: slice — slice 162

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.2

## Findings

### [PASS] Core design aligns with architecture's session-granular gap model

The slice's day-granularity diff is fully consistent with the architecture's gap definition: "A session where the calendar says trading occurred and the data table contains zero bars for the symbol is a gap." Comparing covered days (from the cagg) against trading sessions produces exactly session-granular gaps. The `compute_missing_minute_sessions` function follows the same algorithmic shape as the architecture's `compute_missing_ranges` — lifecycle clamping, session-calendar fetch, set-diff, contiguous-run grouping — and reuses `_group_into_ranges` for the grouping step. The `precomputed_ranges` extension to `update_data_gaps` is additive and preserves the advisory-lock transactional-writer contract.

### [CONCERN] Failure modes not enumerated for the new batch cagg query I/O path

The slice introduces a new I/O path: a universe-wide grouped query against `minute_4hour_ohlcv` that runs once per cycle. The Risk Assessment section discusses cagg staleness (a data-correctness concern) and memory footprint (a resource concern), but does not enumerate failure modes for the query itself: What happens if the query hangs? If it times out? If the database connection drops mid-scan? The slice should specify an explicit handling strategy — e.g., query timeout, retry policy, and fallback behavior (skip coverage-aware seeding for this cycle vs. halt the daemon). Without this, a stalled query would block the entire minute cycle indefinitely with no documented recovery path.

### [CONCERN] NFR conflict: history-start resolution goes to 2004, not the architecture's 24-month target

The architecture specifies the minute target window as `target_start = max(first_trade_date, today - history_months)` with `MINUTE_HISTORY_MONTHS = 24`. For a long-lived symbol first listed in 2004, this yields a start of approximately 2 years ago, not 2004. The slice states that `_resolve_minute_history_start` resolves to `max(EODHD_INTRADAY_HORIZON, operator_floor, first_listing/first_data_date)` and that "for a long-lived symbol this is 2004-01-01." This implies the 24-month window is not being applied, and the slice says this function is "unchanged." The slice touches this path (it threads `history_start` into the new coverage-aware seeder) but neither restates the `MINUTE_HISTORY_MONTHS = 24` NFR nor acknowledges the discrepancy. If the window is genuinely 22 years rather than 2, the cost and gap-row volume implications are significant even after coverage-aware seeding is in place — a newly-added symbol would still generate ~69 chunks instead of ~6. The slice should either confirm that `operator_floor` already incorporates the 24-month clamp (and clarify why the result is 2004), or acknowledge the gap and note whether addressing it is in-scope or deferred.

### [NOTE] Coverage sourced from continuous aggregate instead of raw data table

The architecture's `compute_missing_ranges` step 3 says "From the data table, get the set of `date(time)` for stored bars." The slice sources coverage from the `minute_4hour_ohlcv` continuous aggregate instead of raw `minute_ohlcv`. This is a deviation from the literal specification, but it is well-justified: the cagg structurally cannot report coverage that raw lacks (it can only lag), the worst case is a harmless re-seed of today's partial session, and the per-symbol raw scan the architecture warns against is avoided. The architecture defines caggs as first-class schema objects projecting raw OHLCV, so using one as a coverage proxy is within the architectural boundary. This deviation is sound but would benefit from an explicit acknowledgment that it departs from the `compute_missing_ranges` step-3 specification by design.

### [NOTE] History window discrepancy is pre-existing and partially out of scope

The slice's stated scope is coverage-aware seeding — making the seeder diff against actual coverage rather than emitting a single full-window span. The history-start resolution (`_resolve_minute_history_start`) is explicitly listed as unchanged. While the 24-month vs 2004 discrepancy (see CONCERN above) is pre-existing, the slice's value proposition ("near-zero chunks for already-covered symbols on restart") does hold regardless of the window width, because coverage-aware seeding suppresses re-fetching for sessions that already have bars. The window-width issue would primarily affect cold-start backfill of new symbols, which is a separate concern.
