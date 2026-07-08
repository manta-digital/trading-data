---
docType: review
layer: project
reviewType: slice
slice: adjusted-on-read-core
project: squadron
verdict: PASS
sourceDocument: project-documents/user/slices/153-slice.adjusted-on-read-core.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260505
dateUpdated: 20260505
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Full compliance with adjusted-on-read contract"
    location: src/manta_trading/data/adjustment.py (new file, slice doc#adjusted()-function-outline)
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Read API default behavior aligns with architecture"
    location: slice doc#Technical Scope
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Pure function constraint honored"
    location: slice doc#Technical Decisions#adjusted() signature
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Failure contract explicitly specified"
    location: slice doc#Technical Decisions#Failure contract
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Correct dependency direction on k-factor math"
    location: slice doc#Dependencies#Prerequisites
  - id: F006
    severity: pass
    category: uncategorized
    summary: "Granularity token table consistent with continuous aggregates specification"
    location: slice doc#Granularity token table
  - id: F007
    severity: pass
    category: uncategorized
    summary: "Scope boundary respected"
    location: slice doc#Technical Scope
  - id: F008
    severity: pass
    category: uncategorized
    summary: "No NFR gap identified"
    location: slice doc
  - id: F009
    severity: pass
    category: uncategorized
    summary: "`parent` field correctly references slice plan, not architecture"
    location: slice doc#parent field
  - id: F010
    severity: pass
    category: uncategorized
    summary: "Success criteria are measurable and complete"
    location: slice doc#Success Criteria
  - id: F011
    severity: pass
    category: uncategorized
    summary: "Verification walkthrough validates the key invariant"
    location: slice doc#Verification Walkthrough
---

# Review: slice — slice 153

**Verdict:** PASS
**Model:** minimax/minimax-m2.7

## Findings

### [PASS] Full compliance with adjusted-on-read contract

The `adjusted()` function signature matches the architecture's specification exactly:
- `def adjusted(bars, symbol, conn, *, ca_snapshot=None) -> bars`
- Reads `splits` and `dividends` from TimescaleDB for the symbol and date range
- Looks up `prev_close` from `daily_ohlcv` for each dividend ex-date
- Returns bars unchanged when no CAs exist
- Optional `ca_snapshot` for replay

### [PASS] Read API default behavior aligns with architecture

Both `TimescaleMinuteDataDB.get_minute_data` and `TimescaleDailyDataDB.get_daily_data` accept `adjusted: bool = True`. The slice implements this correctly — default is `True`, callers get adjusted bars unless they explicitly opt out with `adjusted=False`. This matches the architecture's "When `True` they call `adjusted()` before returning. Default is `True`" specification.

### [PASS] Pure function constraint honored

The slice explicitly states: "It does not own or manage connections. ... `adjusted()` is a pure function." The implementation outline shows no side effects — a DataFrame goes in, a (possibly modified) DataFrame comes out. This aligns with the architecture's "Pure function. No side effects." requirement.

### [PASS] Failure contract explicitly specified

The slice enumerates failure modes with explicit handling:
- `KeyError` propagates from `compute_k_factor` when `prev_close` is missing — "Do not swallow. Callers convert to user-facing error messages."
- `ValueError` for minute-grain tokens passed to `TimescaleDailyDataDB`

This follows the instruction to state explicit handling strategies, not "TBD."

### [PASS] Correct dependency direction on k-factor math

The slice correctly depends on `compute_k_factor` and `CaSnapshot` from `src/manta_trading/data/adjustment/k_factor.py` rather than reimplementing the math. The architecture explicitly preserves "compute_k_factor math" as the single source of truth. No duplication.

### [PASS] Granularity token table consistent with continuous aggregates specification

The slice defines 9 granularity tokens (M1, M5, M15, H1, H4, D1, W1, MO1, Q1) that match the architecture's 7-cagg specification:
- 4 minute caggs: `minute_5min_ohlcv`, `minute_15min_ohlcv`, `minute_hourly_ohlcv`, `minute_4hour_ohlcv`
- 3 daily caggs: `daily_weekly_ohlcv`, `daily_monthly_ohlcv`, `daily_quarterly_ohliv`
Plus M1 (raw minute_ohlcv) and D1 (raw daily_ohlcv) which are not caggs but base tables.

### [PASS] Scope boundary respected

The slice explicitly excludes CLI commands (slice 154), daemon changes (slice 154), ingest path changes, and new schema migrations. This correctly keeps slice 153 focused on the core function and DB read layer only, with interface contracts established for consumers in slice 154.

### [PASS] No NFR gap identified

The architecture document does not state NFRs (latency, throughput targets) for the adjusted-on-read path. No NFR restatement is required. No gap.

### [PASS] `parent` field correctly references slice plan, not architecture

The `parent: user/architecture/140-slices.data-quality-operations.md` points to the slice plan document, not the architecture document (`140-arch.data-quality-operations.md`). Per instructions, this is not a flaggable error — the reviewer should not flag it.

### [PASS] Success criteria are measurable and complete

All functional requirements are testable:
- Enum importability assertions
- `adjusted()` behavior with/without CAs, with/without `ca_snapshot`
- Routing correctness for each granularity token
- `adjusted` kwarg default and opt-out behavior

Technical requirements include zero pyright errors and all tests passing. These are unambiguous pass/fail criteria.

### [PASS] Verification walkthrough validates the key invariant

The AAPL split continuity check validates the core backward-adjustment math: adjusted close on 2020-08-28 should be ~1/4 of raw close, and continuous with the post-split 2020-08-31 adjusted close. This directly tests the architecture's specified model: "The most recent close has k_factor = 1.0 exactly, and prices on dates earlier than corporate actions are scaled down."
