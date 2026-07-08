---
docType: review
layer: project
reviewType: slice
slice: consolidation
project: squadron
verdict: PASS
sourceDocument: project-documents/user/slices/152-slice.consolidation.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260505
dateUpdated: 20260505
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Correctly supersedes the adjusted-on-write model with a sound adjusted-on-read replacement"
    location: 152-slice.consolidation.md#What's broken and what we're doing about it
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Cagg design correctly enforces one source of truth per timeframe"
    location: 152-slice.consolidation.md#Where caggs come from
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Granularity token map correctly scopes adjusted-on-read"
    location: 152-slice.consolidation.md#Granularity tokens
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Programmatic API additions align with existing patterns"
    location: 152-slice.consolidation.md#Programmatic API
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Deletion scope correctly mirrors the adjusted-on-read removal"
    location: 152-slice.consolidation.md#What deletes vs stays
  - id: F006
    severity: pass
    category: uncategorized
    summary: "Bulk-EOD steady-state correctly scoped to slice 152"
    location: 140-arch.data-quality-operations.md#Daily
  - id: F007
    severity: note
    category: uncategorized
    summary: "Architecture cross-references will need updating outside this slice"
    location: 140-arch.data-quality-operations.md#References
  - id: F008
    severity: note
    category: uncategorized
    summary: "`adjusted()` API contract is underspecified for deterministic replay"
    location: 152-slice.consolidation.md#One adjustment function
  - id: F009
    severity: pass
    category: uncategorized
    summary: "Backfill and cagg refresh behavior correctly scoped"
    location: 152-slice.consolidation.md#Backfill / how full the caggs get and when
---

# Review: slice — slice 152

**Verdict:** PASS
**Model:** minimax/minimax-m2.7

## Findings

### [PASS] Correctly supersedes the adjusted-on-write model with a sound adjusted-on-read replacement

The slice replaces stored `adj_*` columns with an `adjusted()` function computed on read. This eliminates the entire failure class (silent drift from stale stored adjustments) at the root. The rationale — "one DB, one ingest path, no daemon recompute, no audit machinery" — is coherent and consistent with the architecture's stated goals of transparency and trustworthiness. The architecture names this document as the vehicle for amendment, which is the correct handling.

### [PASS] Cagg design correctly enforces one source of truth per timeframe

The "withdraw that proposal" self-correction is architecturally important: slice 150 had proposed adding CAGGs over `daily_ohlcv` for weekly/monthly alongside minute-derived CAGGs for the same timeframes. Slice 152 explicitly rejects that as "two copies of partial truth." The replacement rule — each timeframe from its finest-grained source — is stated clearly and enforced: 5m/15m/1h/4h from minute, daily direct from `daily_ohlcv`, weekly/monthly/quarterly from daily. This is a tighter contract than the architecture's cagg section (which didn't specify CAGGs over daily at all), and it is correct.

### [PASS] Granularity token map correctly scopes adjusted-on-read

The token map specifies `adjusted=True` for every source (hypertable or cagg). This is architecturally sound: all OHLCV is stored raw; all retrieval paths run through `adjusted()` by default. The `--raw` flag for `mt data get` gives explicit access to unadjusted bars when needed. The contract is clear and correctly implemented.

### [PASS] Programmatic API additions align with existing patterns

`TimescaleMinuteDataDB.get_minute_data(..., adjusted=True)` extends an existing method. `TimescaleDailyDataDB.get_daily_data(symbol, start, end, granularity, adjusted=True)` is a parallel addition for daily timeframes. Both routes run their output through `adjusted()` when the flag is set. The thin CLI implementation (~40 lines to pick DB, call method, render) keeps the surface area small. No hidden dependencies.

### [PASS] Deletion scope correctly mirrors the adjusted-on-read removal

The deletes list is thorough and traceable: `band_writer.py`, `verify.py`, `verify_eod.py`, `audit.py`, `ca_drift.py`, the entire `adjustment/` package, `dailyohlcvadjusted` table, `adj_*` columns, `k_factor`, `adjusted_at`, `last_adjusted_ca_snapshot_id`, `ADJUSTMENT_DRIFT_EPSILON`, and all tests for deleted modules. Each deletion is motivated by the adjusted-on-read model, not by scope reduction for its own sake. The "~3000 lines" estimate is consistent with the breadth of the removed code.

### [PASS] Bulk-EOD steady-state correctly scoped to slice 152

The architecture explicitly defers the bulk-EOD steady-state to slice 152 (implementation note dated 2026-05-03). The slice correctly lands it: the daemon's daily cycle switches to `/eod-bulk-last-day` for routine updates, while per-symbol `/eod` remains for backfill and refetch. The cost model (100 credits vs ~13,000 credits/day) is stated and the scope boundary is correct.

### [NOTE] Architecture cross-references will need updating outside this slice

The architecture's reference [120] says "slice 146 adds ... CA-detection" and [140] says "slice 152 adds the bulk-EOD steady-state path." After this slice lands, CA-detection is removed entirely (band writer and `ca_drift.py` go away), making the [120] reference stale. Slice 152 names the amendment ("Architecture amendment: update 140-arch.data-quality-operations.md to describe adjusted-on-read. Mark adjusted-on-write sections as superseded") in its deletes/adds list, which is the correct treatment. No action needed in this slice; the architecture owner handles it as a follow-on.

### [NOTE] `adjusted()` API contract is underspecified for deterministic replay

The architecture spec for `compute_k_factor` includes a `ca_snapshot` argument specifically for "deterministic replay: given a historical snapshot (e.g., reconstructed from audit logs of CA arrivals), recompute what k_factor *would have been* at ingest time." Slice 152's `adjusted(bars, symbol, conn, *, ca_snapshot=None)` includes this argument but does not describe how `ca_snapshot` is used when non-None. The function signature allows replay; the behavior is not documented. This is minor — callers can infer from the function name — but the architecture's explicit contract for replay is worth restating here if replay use cases are expected.

### [PASS] Backfill and cagg refresh behavior correctly scoped

The slice correctly distinguishes between "caggs are derived state that fills on first refresh" and "minute history is sparse by design, not a 152 problem." Daily CAGGs over `daily_ohlcv` are immediately usable after post-migration refresh because `daily_ohlcv` is dense. The manual `CALL refresh_continuous_aggregate` step after migration is explicit. The scope boundary ("deep minute history later is a separate initiative") is correctly stated and prevents scope creep.
