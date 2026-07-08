---
docType: review
layer: project
reviewType: slice
slice: index-constituent-tracking-daily-snapshot-of-sp500-r2000-nasdaq-100
project: squadron
verdict: PASS
sourceDocument: project-documents/user/slices/161-slice.index-constituent-tracking-daily-snapshot-of-sp500-r2000-nasdaq-100.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260514
dateUpdated: 20260514
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Table schema aligns with architectural goals and index-membership future work"
    location: 161-slice.index-constituent-tracking-daily-snapshot-of-sp500-r2000-nasdaq-100.md#Table:_universe_members
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Daemon integration pattern matches architecture"
    location: 161-slice.index-constituent-tracking-daily-snapshot-of-sp500-r2000-nasdaq-100.md#Daemon_Integration
  - id: F003
    severity: pass
    category: uncategorized
    summary: "CLI command group follows architecture conventions"
    location: 161-slice.index-constituent-tracking-daily-snapshot-of-sp500-r2000-nasdaq-100.md#CLI:_mt_data_universes
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Credit budget is bounded and consistent with architecture"
    location: 161-slice.index-constituent-tracking-daily-snapshot-of-sp500-r2000-nasdaq-100.md#Data_Flow
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Idempotence and first-run seed logic are well-specified"
    location: 161-slice.index-constituent-tracking-daily-snapshot-of-sp500-r2000-nasdaq-100.md#Core_Tracking_Logic
  - id: F006
    severity: concern
    category: uncategorized
    summary: "Cross-slice column naming inconsistency requires coordination"
    location: 161-slice.index-constituent-tracking-daily-snapshot-of-sp500-r2000-nasdaq-100.md#Interface_Requirements_from_Other_Slices
  - id: F007
    severity: note
    category: uncategorized
    summary: "No failure-mode enumeration for EODHD API errors"
    location: 161-slice.index-constituent-tracking-daily-snapshot-of-sp500-r2000-nasdaq-100.md#Core_Tracking_Logic
  - id: F008
    severity: note
    category: uncategorized
    summary: "No `data_gaps` entry for universe-tracking staleness"
    location: 161-slice.index-constituent-tracking-daily-snapshot-of-sp500-r2000-nasdaq-100.md#Technical_Scope
---

# Review: slice — slice 161

**Verdict:** PASS
**Model:** minimax/minimax-m2.7

## Findings

### [PASS] Table schema aligns with architectural goals and index-membership future work

The `universe_members` table design (composite PK on `universe_name, symbol, added_date` with re-addition support, active-membership partial index on `removed_date IS NULL`) correctly supports point-in-time membership queries. This directly fulfills the architecture's stated future work: *"Index-membership-at-time-T — point-in-time SP500 / NDX / etc. membership for backtest scope filtering."* The no-FK design for `symbol` is explicitly rationalized and consistent with the rest of the system's instrument identification approach.

### [PASS] Daemon integration pattern matches architecture

The post-cycle hook design (appended after the OHLCV daily cycle, not embedded in the per-symbol loop) is the correct architectural choice for a once-per-day operation. The architecture's daily backfill behavior calls for a single-cycle integration point, and this design respects that boundary.

### [PASS] CLI command group follows architecture conventions

The `mt data universes` command group (`ls`, `as-of`, `refresh`) parallels the architecture's established `mt data ca` and `mt data status` command shapes. The `as-of` query logic is correctly scoped to the active-membership window (`added_date <= :date AND (removed_date IS NULL OR removed_date > :date)`).

### [PASS] Credit budget is bounded and consistent with architecture

The slice budgets 30 credits/day (3 indices × 10 credits each) and explicitly states it runs after the OHLCV cycle. The architecture's `EODHD_DAILY_QUOTA = 100_000` and steady-state path notes support this magnitude comfortably. No NFR restatement is needed here since the constraint is trivially satisfied.

### [PASS] Idempotence and first-run seed logic are well-specified

The `is_refreshed_today` guard and first-run seed pattern prevent duplicate rows and support safe re-runs. The architecture does not spec idempotence for universe operations, but this design choice is sound for a single-operator system.

### [CONCERN] Cross-slice column naming inconsistency requires coordination

The slice correctly identifies that slice 130's design doc uses `added_on/removed_on` while this table uses `added_date/removed_date`. The note flags this as needing update at task time. This is a coordination dependency, not an architectural violation, but it introduces a risk that slice 130 could be implemented against the wrong column names if both slices are not synchronized. Given that this slice declares slice 130 as a consumer, the column name resolution should be treated as a blocking pre-condition before slice 130 proceeds.

### [NOTE] No failure-mode enumeration for EODHD API errors

The spec addresses malformed-payload failure (returns empty set, logs ERROR), but does not enumerate failure modes for network errors, HTTP error codes, or rate-limit responses from EODHD. The architecture's gap-function design emphasizes explicit handling strategies rather than TBD. While the daily-cycle retry model absorbs transient failures over subsequent cycles, documenting the behavior (e.g., "log ERROR, skip universe for today, retry next cycle") would strengthen the specification and align with the architecture's explicit-handling requirement.

### [NOTE] No `data_gaps` entry for universe-tracking staleness

The architecture's core invariant centers on `data_gaps` for all data-layer freshness tracking, including staleness thresholds (DAILY_STALENESS_THRESHOLD = 2 days). The slice produces `universe_members` but does not hook into the `data_gaps` table or surface universe membership freshness via `data_status`. This may be intentional — the architecture explicitly defers index-membership-at-time-T until "needed" — but if `mt data status` is the operator's single pane of glass for data trust, universe staleness would be a natural addition. The current design is not wrong, but the gap between "data is trustworthy" (from `data_status`) and "universe membership is current" (from `universe_members` freshness) should be acknowledged, either by noting the disconnect as intentional deferral or by sketching the extension point for future `data_status` integration.
