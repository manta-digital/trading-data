---
docType: review
layer: project
reviewType: slice
slice: api-surface-coverage-kalshi
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/slices/188-slice.api-surface-coverage-kalshi.md
aiModel: moonshotai/kimi-k3
status: complete
dateCreated: 20260912
dateUpdated: 20260912
reviewedSha: 505a6677f26a50394c287f1238117f7e042bb99f
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 14
findings:
  - id: F001
    severity: pass
    category: alignment
    summary: "Mandate executed exactly as commissioned; both named open questions settled by measurement"
    location: "project-documents/user/slices/188-slice.api-surface-coverage-kalshi.md#technical-decisions"
  - id: F002
    severity: pass
    category: boundaries
    summary: "Boundary and dependency directions are correct; scope discipline is exemplary"
    location: "project-documents/user/slices/188-slice.api-surface-coverage-kalshi.md#technical-scope"
  - id: F003
    severity: pass
    category: error-handling
    summary: "Contracts, failure modes, and I/O-path risks are enumerated with explicit handling"
    location: "project-documents/user/slices/188-slice.api-surface-coverage-kalshi.md#d10--failure-modes-on-the-new-paths"
  - id: F004
    severity: pass
    category: nfr
    summary: "NFR restated with specific targets and the load-tier conventions inherited correctly"
    location: "project-documents/user/slices/188-slice.api-surface-coverage-kalshi.md#d11--load-tier-what-only-a-load-test-can-assert"
  - id: F005
    severity: note
    category: verification-limit
    summary: "`test/kalshi_support/` helper relocation is asserted, not verified"
    location: "project-documents/user/slices/188-slice.api-surface-coverage-kalshi.md#d11--load-tier-what-only-a-load-test-can-assert"
  - id: F006
    severity: note
    category: contract-design
    summary: "Optional `start`/`end` is a deliberate, justified deviation from the bars convention"
    location: "project-documents/user/slices/188-slice.api-surface-coverage-kalshi.md#d4--time-series-range-policy"
---

# Review: slice — slice 188

**Verdict:** PASS
**Model:** moonshotai/kimi-k3

## Findings

### [PASS] Mandate executed exactly as commissioned; both named open questions settled by measurement

The slice-plan entry (180-slices entry 8) requires the design to settle three things: namespace placement, the range-cap policy for a 345M-row table, and settlement outcome placement. D1 (`/api/v1/kalshi/*` namespace with Kalshi's own hierarchy), D4 (count-first admission against the shared ceiling, sized from stored-data measurements rather than window estimates), and D3 (settlement as a nested object on the market resource) answer each directly. D1's reasoning (a Kalshi market is not an `instruments` row; `adjusted`/`granularity` would become meaningless) correctly honors the arch's "thin wrapper over stored data" principle rather than contorting the equities vocabulary. The arch doc's 2026-09-12 amendment names this gap explicitly and assigns it to slices 188–190; D12 updates the arch doc in-slice (endpoints section + Range Policy note), following the precedent 186 D11 set for keeping the parent truthful.

### [PASS] Boundary and dependency directions are correct; scope discipline is exemplary

Reads live in `data/kalshi/` as sync functions over `psycopg.Connection` returning frozen dataclasses, routes stay thin and map to Pydantic models (D9) — the arch's "new query capability lands in the data layer first" rule applied verbatim, and consistent with the existing `status.py`/`trade_status.py` discipline in that package. The `effective_tape_floor` promotion (Interfaces required, lines 113–116) is the DRY-correct move: one public definition called by both CLI and API, so the two surfaces cannot report different floors — verified against 267's Decision 8 semantics. Dependencies point the right way: 189 consumes the namespace this slice provides, 190 consumes the docstrings/OpenAPI; freshness verdicts are explicitly deferred to 189 and documentation to 190. The two explicit exclusions (cross-cutting markets list — measured 120 MB, above every ceiling; derived candle periods — `time_bucket` over fourteen nullable OHLC columns) are each quantified, recorded as Future Work items 4 and 5 in the slice plan, and protected against shape-change (`period_minutes` on every response). No new tables, no migration, no new serving machinery — matching the slice plan's "reuses the existing pool, error handling, and staleness posture."

### [PASS] Contracts, failure modes, and I/O-path risks are enumerated with explicit handling

The slice inherits the 186/187 contract stack correctly: `404` only for unknown ticker with the market row sought up front (D6 — one sub-millisecond seek per request, justified against the D5 `tape_filtered` need); `200, count: 0` with completeness facts otherwise; `QueryCanceled → 504`; other `psycopg.Error →` sanitized 500, never caught in readers, never defaulted (explicitly: "a failed existence seek must not become a 404"). D10 enumerates the new failure modes including the tricky one — `sync_state['trades']` absent → honest `200` with nulls, documented in field descriptions. D4's count-then-fetch race is analyzed in the safe direction (no `LIMIT`, so the response can be one row over, never one short). Pool-responsibility is handled per 185 D8a: time-series routes scope the checkout to seek+count+fetch and release before serialization, with the `/health` stall rationale stated. D11's assertion 2 even pins that a rejected request issues no row fetch. The 187 D8 `timestamptz`-binding lesson (3,100 ms planning) is restated and applied to the hypertable predicates. Decimal-as-string serialization (D7) with the measured `default=str` removal is a genuinely good catch that preserves the storage layer's exactness work (261 Decision 5).

### [PASS] NFR restated with specific targets and the load-tier conventions inherited correctly

The slice touches network/concurrency paths, and the parent arch (Error Handling section) records the structural NFR gap — `statement_timeout` bounds a statement, not a request — that 187's load tier exists to close. D11 restates this with specific provisional targets (< 15 s trades-at-ceiling, < 500 ms rejection, < 2 s events list, concurrency-16 vs pool-8 queueing factor), each marked provisional with the 187 rule that Phase 6 re-derives every bound from a recorded measurement before committing it. The fixture (`kalshi_dense_db`) is sized above measured production maxima (25,000 events vs measured 24,382; ceiling + 1,000 rows). The 20 s statement-timeout budget is justified against the measured slowest statement (~100 ms ceiling-sized count). The D8 candle-row-width risk (~2× bars' 11.58 MB) is flagged with a Phase 6 measurement plan and an explicit anti-silent-change rule.

### [NOTE] `test/kalshi_support/` helper relocation is asserted, not verified

D11 and the Implementation Notes state that `test/integration/kalshi_helpers.apply_kalshi_track` exists and moves to `test/kalshi_support/` so load and integration tiers import it from one place. I could not confirm the helper's existence from the documents available to this review (grep across slice and review docs found no other mention of it); this is a design doc, so the assertion rests on the author's knowledge of the repo. Flagging as informational only — tasks phase should confirm the source module and update the integration conftest import as the notes describe.

### [NOTE] Optional `start`/`end` is a deliberate, justified deviation from the bars convention

Bars requires `start`/`end` (arch: both "Required"); this slice makes them optional ("omitted means the market's whole stored tape"). The deviation is measured, not casual: the largest stored market's whole history is 132,552 trades / 68,663 candles against a 75,000 ceiling, the count guard bounds the worst case before any fetch, and the natural request shape for 15-minute/one-day markets is the whole tape. The alternative (forcing clients to supply windows) would push them toward guessing dates for a catalog whose span is unknown up front. Consistent with the arch's range policy as revised by 186 D4 — the admission cap, not the window requirement, is what protects the server.
