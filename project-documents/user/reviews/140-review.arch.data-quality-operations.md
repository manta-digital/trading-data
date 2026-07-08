---
docType: review
layer: project
reviewType: arch
slice: data-quality-operations
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/architecture/140-arch.data-quality-operations.md
aiModel: z-ai/glm-5.1
status: complete
dateCreated: 20260430
dateUpdated: 20260430
findings:
  - id: F001
    severity: concern
    category: consistency
    summary: "acquisition_state.retry_count is ambiguous and conflates per-gap attempt counts with symbol-level health"
    location: 140-arch.data-quality-operations.md#Slimmed-acquisition_state
  - id: F002
    severity: concern
    category: consistency
    summary: "Partial success outcome for acquisition_state.last_attempt_outcome is undefined"
    location: 140-arch.data-quality-operations.md#Slimmed-acquisition_state
  - id: F003
    severity: concern
    category: feasibility
    summary: "compute_k_factor is referenced as a Python function but CA recompute uses set-based SQL with no bridge specification"
    location: 140-arch.data-quality-operations.md#One-adjustment-function
  - id: F004
    severity: concern
    category: completeness
    summary: "Step 2 TRUNCATE is destructive with no rollback path and no data verification after wipe"
    location: 140-arch.data-acquisition.md#Step-2--Schema-migration-+-TRUNCATE-(slice-142)
  - id: F005
    severity: concern
    category: feasibility
    summary: "data_status view definition is pseudocode with undefined SQL semantics for per-row target window computation"
    location: 140-arch.data-quality-operations.md#One-status-view
  - id: F006
    severity: concern
    category: dependencies
    summary: "Backtest acquires advisory locks in sorted order but deadlock risk exists with daemon"
    location: 140-arch.data-quality-operations.md#Backtest-contract
  - id: F007
    severity: concern
    category: feasibility
    summary: "coalesce_data_gaps merge-eligibility check requires querying trading_calendar for every gap pair"
    location: 140-arch.data-quality-operations.md#coalesce_data_gaps(symbol,-granularity)
  - id: F008
    severity: concern
    category: feasibility
    summary: "coalesce_data_gaps repeats until no merges remain — potential O(n²) behavior"
    location: 140-arch.data-quality-operations.md#coalesce_data_gaps(symbol,-granularity)
  - id: F009
    severity: concern
    category: consistency
    summary: "refetch --reapply-only does not update acquisition_state.last_adjusted_ca_snapshot_id"
    location: 140-arch.data-quality-operations.md#Three-operator-commands
  - id: F010
    severity: note
    category: feasibility
    summary: "Daily backfill claim of \"within a single quota-day\" may be tight"
    location: 140-arch.data-quality-operations.md#Refetch
  - id: F011
    severity: concern
    category: abstraction
    summary: "Stage B audit compares against vendor's adjusted_close but compute_k_factor is a custom calculation that may legitimately differ"
    location: 140-arch.data-quality-operations.md#Three-operator-commands
  - id: F012
    severity: concern
    category: completeness
    summary: "compute_k_factor function signature includes ca_snapshot but no specification of the actual adjustment math"
    location: 140-arch.data-quality-operations.md#One-adjustment-function
  - id: F013
    severity: concern
    category: consistency
    summary: "Refetch resets PROVIDER_HOLE rows to UNKNOWN but update_data_gaps step 4 only carries forward attempt_count for matching fetch_status"
    location: 140-arch.data-quality-operations.md#Gap-function-(the-core-invariant)
  - id: F014
    severity: concern
    category: completeness
    summary: "data_status view health computation is under-specified for the case where acquisition_state has no row"
    location: 140-arch.data-quality-operations.md#Health-rules
  - id: F015
    severity: note
    category: extension-points
    summary: "Tick data extension explicitly rejects reusing data_gaps but the pattern transfer is underspecified"
    location: 140-arch.data-quality-operations.md#Future-work
---

# Review: arch — slice 140

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.1

## Findings

### [CONCERN] acquisition_state.retry_count is ambiguous and conflates per-gap attempt counts with symbol-level health

The health rules check `retry_count >= MAX_RETRY_COUNT` on `acquisition_state` to set FAILED, but `update_data_gaps` step 6 says it updates `acquisition_state.retry_count` using "the same logic (max prior + 1 on failure, 0 on success)." This is the per-gap-row carry-forward logic from step 4, applied to the symbol-level row. The semantics are unclear: if a symbol has 5 distinct gaps and each fails once on separate calls, does `retry_count` accumulate to 5 (hitting MAX_RETRY_COUNT=5) even though no single gap was retried 5 times? Or does it reset to 0 on any success, potentially masking that one specific gap is exhausted? The document never clarifies whether `acquisition_state.retry_count` represents "consecutive failures for the most recent gap" or "cumulative failures across all gaps." Either interpretation creates a mismatch with the per-gap `attempt_count` in `data_gaps`, which is the authoritative retry tracker. The health rule should reference `data_gaps` state (e.g., "any RETRY_EXHAUSTED row exists") rather than a redundant and ambiguously-maintained counter on `acquisition_state`.

### [CONCERN] Partial success outcome for acquisition_state.last_attempt_outcome is undefined

The `acquisition_state.last_attempt_outcome` enum is `'success' | 'partial' | 'empty' | 'transient_failure'`. The health rules only check for `transient_failure` to set FAILED. When `last_attempt_outcome = 'partial'`, the health evaluation falls through to the STALE/GAPS/OK checks. This means a symbol that just returned partial data (some bars but still has gaps) will show health=GAPS, not something indicating partial progress. While defensible, it means the operator cannot distinguish "we just tried and got partial data" from "gaps exist from some previous attempt" — the partial outcome is lost after the first health rule check. The mapping table for data_gaps correctly handles partial (fetch_status=UNKNOWN), but acquisition_state loses the nuance.

### [CONCERN] compute_k_factor is referenced as a Python function but CA recompute uses set-based SQL with no bridge specification

The document specifies `compute_k_factor(symbol, date, ca_snapshot) -> float` as a Python function used by ingest, refetch, and audit. But the CA detection recompute section says "The recompute uses **set-based SQL UPDATEs**, not per-bar Python" and describes computing `k_factor_band = compute_k_factor(symbol, ex_date_i, ca_snapshot)` once per band, then issuing one UPDATE per band. This means `compute_k_factor` must be callable from both Python (for ingest/audit) and from whatever drives the SQL UPDATEs (the daemon). The daemon is Python, so it calls `compute_k_factor` to get the band value, then issues a parameterized SQL UPDATE. This works, but the document doesn't address what happens when `compute_k_factor` is used during ingest for individual bars — does ingest also use band-based updates, or per-bar Python calls? If ingest processes bars one session at a time and calls `compute_k_factor` per session, that's fine but potentially slow for minute data (960 bars per session × many sessions). The performance characteristics of the two code paths (per-bar Python during ingest vs. band-based SQL during recompute) are not analyzed.

### [CONCERN] Step 2 TRUNCATE is destructive with no rollback path and no data verification after wipe

The migration Step 2 runs `TRUNCATE minute_ohlcv, daily_ohlcv, acquisition_state, coverage_gaps` in a single transaction with the schema changes. But:
1. There is no verification step between the schema landing and the TRUNCATE — if the new schema DDL has an error, the transaction rolls back, but if it succeeds and TRUNCATE runs, 61.8M minute rows and all daily rows are gone with no backup.
2. There is no rollback plan documented. If the daemon backfill in slice 144 fails partway through (EODHD outage, quota exhaustion), the operator has an empty database with no way to revert to the AV-era data.
3. The pre-flight checks verify instruments were rebuilt (Step 1) and EODHD access works for a sample, but this doesn't guarantee that 57k symbols can be backfilled successfully. A quota check or capacity verification is missing.

### [CONCERN] data_status view definition is pseudocode with undefined SQL semantics for per-row target window computation

The view definition says `bars_expected = expected sessions from trading_calendar within target window` and `gap_count = COUNT(*) from data_gaps within target window`, where the target window is per-symbol and per-granularity (computed from `first_trade_date`, `history_months`, and `most_recent_completed_session_close_utc`). This means every row in the view needs a correlated subquery or LATERAL join with a per-row target window that depends on the symbol's exchange and the current time. For a 13k-symbol daily universe, this view would be expensive to compute on every `mt data status` call. The document mentions materialized data_status as future work "only if `mt data status` becomes too slow at full-universe scope" but doesn't acknowledge that the per-row window computation with `most_recent_completed_session_close_utc(exchange)` (which itself requires a lookup per exchange) makes this a potentially very slow correlated query. No query plan or performance estimate is provided.

### [CONCERN] Backtest acquires advisory locks in sorted order but deadlock risk exists with daemon

The backtest acquires advisory locks on `(symbol, granularity)` for each symbol "in sorted order to prevent deadlock." This prevents deadlocks between concurrent backtests. However, the daemon also acquires the same advisory locks one symbol at a time during its cycle. If the daemon is processing symbol B while holding the lock for symbol B, and a backtest has already acquired the lock for symbol A and is waiting for symbol B, and then the daemon moves on to try symbol A — deadlock. The document says the daemon processes "symbols one at a time" but doesn't specify that the daemon acquires and releases locks one at a time (acquire A, process A, release A, acquire B, process B, release B) vs. holding multiple locks. If the daemon only holds one lock at a time, there's no deadlock, but this is not explicitly stated. The backtest contract says it acquires locks for ALL symbols in scope before processing, creating a window where it holds multiple locks simultaneously.

### [CONCERN] coalesce_data_gaps merge-eligibility check requires querying trading_calendar for every gap pair

Step 2 says two adjacent rows A and B are merge-eligible when "the trading sessions in (A.gap_end, B.gap_start) from trading_calendar are empty — i.e. there are no trading sessions strictly between A's last session and B's first session that are not themselves missing." This requires querying trading_calendar for every consecutive pair of gap rows. If a symbol has many small gaps (e.g., 100 gap rows for 100 missing sessions), this is ~99 queries to trading_calendar. More critically, the condition is stated as "no trading sessions strictly between that are not themselves missing" — but checking whether sessions between A and B are "themselves missing" requires checking data tables, not just trading_calendar. If there are sessions between A and B that are NOT missing (they have bars), then A and B should NOT merge — there's a present session between them, meaning they aren't contiguous missing ranges. But `compute_missing_ranges` should have already produced non-overlapping ranges, so if A and B are adjacent in data_gaps, there should be no present sessions between them by construction. The merge-eligibility check seems to be solving a problem that shouldn't exist if gap computation is correct, and the extra trading_calendar query adds complexity and potential bugs.

### [CONCERN] coalesce_data_gaps repeats until no merges remain — potential O(n²) behavior

Step 4 says "Repeat until no merge-eligible pairs remain." If implemented naively (read all rows, check pairs, merge, re-read, repeat), this is O(n²) in the number of gap rows. A single-pass approach would suffice if processing the sorted list left-to-right and accumulating the current merge candidate. The "repeat until" phrasing suggests an iterative approach that may be less efficient. Not a correctness issue, but the algorithm specification is imprecise enough to lead to an inefficient implementation.

### [CONCERN] refetch --reapply-only does not update acquisition_state.last_adjusted_ca_snapshot_id

`mt data refetch --reapply-only` "skips the fetch and re-runs `compute_k_factor` for stored bars in window." The CA detection section says the daemon updates `acquisition_state.last_adjusted_ca_snapshot_id = current_snapshot_id` as step 4 of the recompute. But `--reapply-only` is described as running `compute_k_factor` for stored bars — it's unclear whether it also updates the snapshot_id. If it doesn't, the daemon's next cycle will detect a mismatch and re-run the same recompute, wasting work. If it does, the document should say so explicitly. The asymmetric update of `acquisition_state` between the daemon recompute path and the `--reapply-only` path is unspecified.

### [NOTE] Daily backfill claim of "within a single quota-day" may be tight

The daily backfill requires ~57k per-symbol `/eod` calls against EODHD's 100k/day quota. 57k calls leaves 43k of headroom. However, the document doesn't account for: (a) retry overhead from transient failures, (b) any other EODHD API usage during the same day (e.g., the operator running `mt data audit --all`), (c) EODHD rate limits that may be per-minute not just per-day (the document mentions Finnhub's 60/min limit but doesn't specify EODHD's per-minute rate limit for /eod calls). If EODHD has a per-minute rate limit, 57k calls at (say) 100/min would take ~9.5 hours, which is feasible but should be stated explicitly.

### [CONCERN] Stage B audit compares against vendor's adjusted_close but compute_k_factor is a custom calculation that may legitimately differ

Stage B asserts `abs(stored_k_factor - published_k) < tolerance` where `published_k = adjusted_close / close` from the vendor's daily endpoint. But `compute_k_factor` is the project's own adjustment function, and the document never specifies what adjustment model it uses (split-only? split+dividend? total-return?). EODHD's `adjusted_close` uses a specific adjustment model that may differ from the project's. If `compute_k_factor` computes a split-adjustment-only k_factor but EODHD's adjusted_close includes dividends, the Stage B comparison will flag systematic "drift" that is actually a model mismatch, not a data error. The document needs to specify what adjustment model `compute_k_factor` implements and confirm it matches EODHD's model, or Stage B will produce false positives.

### [CONCERN] compute_k_factor function signature includes ca_snapshot but no specification of the actual adjustment math

The document specifies the `compute_k_factor` signature, the `ca_snapshot` shape, and the `snapshot_id` computation in detail. But the actual adjustment algorithm — how splits and dividends are combined into a single k_factor — is never specified. This is the single most important function in the entire system (used by ingest, refetch, audit, and the daemon recompute), and its implementation is left as "step 3: magic happens." Without specifying whether k_factor is cumulative from IPO, whether it resets at each ex-date, how dividend yield vs. dividend amount affects the factor, and whether it accounts for special distributions, two implementers could produce different results that both pass the type signature but produce different stored values.

### [CONCERN] Refetch resets PROVIDER_HOLE rows to UNKNOWN but update_data_gaps step 4 only carries forward attempt_count for matching fetch_status

The document says "Operator can manually trigger retry via `mt data refetch`, which resets the rows to `UNKNOWN`." But `update_data_gaps` deletes intersecting rows and reinserts with carry-forward logic. When refetch is called, it presumably calls `update_data_gaps` with `fetch_status_for_unfilled = UNKNOWN` (or maybe 'FAILED_RETRYABLE' depending on outcome). The step 4 carry-forward logic says "Find any prior row whose [gap_start, gap_end] overlaps new_gap and whose fetch_status matches fetch_status_for_unfilled." If the prior row was PROVIDER_HOLE and the new fetch_status_for_unfilled is UNKNOWN (because refetch is retrying), the statuses don't match, so attempt_count doesn't carry forward — it starts at 1. The document also says `mt data refetch` "resets to UNKNOWN, attempt_count = 0" — but this reset happens where? The `update_data_gaps` function doesn't have a "reset" path. There's a disconnect between the stated operator behavior (reset to 0) and the specified function behavior (carry-forward from matching status).

### [CONCERN] data_status view health computation is under-specified for the case where acquisition_state has no row

The health rules check `last_attempt_outcome == 'transient_failure'` then `last_attempt_ts IS NULL` for STALE. But if `acquisition_state` has no row at all for a (symbol, granularity) pair (e.g., a newly added symbol that the daemon hasn't attempted yet), the LEFT JOIN from data tables to acquisition_state will produce NULLs for all acquisition_state columns. The rules will fall through to the `last_attempt_ts IS NULL` check → STALE, which is correct per the document's stated semantics. However, the gap_count check then queries data_gaps — but if the daemon hasn't run, there are no data_gaps rows either, so gap_count=0, and health would be STALE not GAPS. The document says "A symbol that the daemon has never attempted shows as STALE because its target window is empty of bars, not OK because the empty interval is internally consistent." This is consistent, but the view SQL must use LEFT JOINs (not INNER JOINs) against acquisition_state, which is not specified.

### [NOTE] Tick data extension explicitly rejects reusing data_gaps but the pattern transfer is underspecified

The future work section says tick data uses `tick_gaps` keyed by sequence-number ranges and the *pattern* transfers (single gap table, status view, compute_missing_ranges analog, same CLI shape). This is reasonable but the advisory lock scheme changes fundamentally: sequence-number-based gap ranges don't map to the `(symbol, granularity)` lock key used here, and tick data's real-time ingestion requirements may not tolerate the same serialization strategy. The extension point is correctly identified but the "pattern transfer" claim should be validated when initiative 200 is scoped.
