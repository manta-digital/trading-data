---
docType: review
layer: project
reviewType: slice
slice: daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/slices/170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260809
dateUpdated: 20260809
reviewedSha: f477a716cda1efc5c0cb29eb0c4335973072991f
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Generalizes slice 166 mechanism without altering driver semantics"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#D2 — Generalize `mt data rechunk` via a target registry
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Reuses slice 166 grid-alignment lesson and per-window transaction discipline"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#D1 — Target chunk interval: 70 days (`DAILY_OHLCV_CHUNK_INTERVAL`)
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Enforces slice 166 A5-Q3 lesson: dependent caggs must be paused during chunk rewrite"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#D5 — Job pause/resume scope (runbook `cagg-maintenance-pausing.md`)
  - id: F004
    severity: pass
    category: uncategorized
    summary: "NFR targets from parent architecture are restated as concrete acceptance criteria"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#Success Criteria
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Respects slice 152 adjusted-on-read contract; does not touch `adj_*` schema"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md
  - id: F006
    severity: pass
    category: uncategorized
    summary: "Correct dependency direction and integration sequencing"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#Integration Points
  - id: F007
    severity: pass
    category: uncategorized
    summary: "Failure modes enumerated with explicit handling strategy"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#Risk Assessment
  - id: F008
    severity: pass
    category: uncategorized
    summary: "Cold-start schema source-of-truth preserved"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#D3 — Migration pair (166 Phase-B pattern)
  - id: F009
    severity: pass
    category: uncategorized
    summary: "Verification walkthrough aligns with parent architecture query discipline"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#Verification Walkthrough
---

# Review: slice — slice 170

**Verdict:** PASS
**Model:** minimax/minimax-m3

## Findings

### [PASS] Generalizes slice 166 mechanism without altering driver semantics

The registry refactor keeps `run_rechunk` driver logic untouched (parameters only) and defaults to `minute`, so existing invocation behavior is bit-identical (regression-guarded by Success Criterion 7). This matches the architecture's "single source of truth" constants rule and avoids scope creep into unrelated code paths.

### [PASS] Reuses slice 166 grid-alignment lesson and per-window transaction discipline

70 days = 10 × 7 days, so existing 7-day chunks nest exactly inside the epoch-aligned 70-day grid. This satisfies the 166 grid-alignment caveat by construction, and each rewritten window yields exactly one chunk — matching the wall-clock sizing rule (span ÷ target chunk count, not data volume).

### [PASS] Enforces slice 166 A5-Q3 lesson: dependent caggs must be paused during chunk rewrite

The pre-flight refuses to run while daily-family jobs are scheduled, and `force_refresh_continuous_aggregate(..., force => true)` on resume heals history the scheduled policy can never reach (start_offset ≥ 270 days). R1 holds: minute-family jobs stay running. The D4 acceptance of cagg default divergence (12 chunks on daily caggs) correctly identifies this as harmless given the over-chunking pathology class.

### [PASS] NFR targets from parent architecture are restated as concrete acceptance criteria

The architecture's coverage-aggregate freshness intent (slice 167/168) requires fast raw-edge probes; this slice restates that as: `MAX(time)` sub-second, plan latency in seconds (not minutes), chunk count drops to ~120. Each target is measurable against the prod baseline captured in the "Measured Baseline" table.

### [PASS] Respects slice 152 adjusted-on-read contract; does not touch `adj_*` schema

No `adj_*` columns, `k_factor`, or `last_adjusted_ca_snapshot_id` are referenced. The slice operates purely on raw hypertable chunking and cagg refresh policies, which is correct given the post-152 architecture where adjustment is a read-time concern.

### [PASS] Correct dependency direction and integration sequencing

Consumes from slice 166 (mechanism) and is correctly ordered as a strict precondition for slice 169 (coverage-cagg refresh repair). The honest acknowledgment that step 8's `daily_coverage` full refresh heals content staleness but not the policy defect (so 169 remains required) shows the slice understands boundary scope.

### [PASS] Failure modes enumerated with explicit handling strategy

Three concrete residual risks are named: (1) registry refactor regressing minute path → mitigated by untouched driver logic + regression guard; (2) cagg corruption via mid-rewrite job firing → mitigated by pre-flight + force-refresh on exit; (3) bulk prod mutation → mitigated by PM-confirmed backup gate + per-window transactions leaving valid partial state on interruption. No "TBD" placeholders.

### [PASS] Cold-start schema source-of-truth preserved

The slice-143 creation migration literal `INTERVAL '7 days'` (`migrations/minute.py:1236`) is updated to render `DAILY_OHLCV_CHUNK_INTERVAL`, satisfying the slice 156 contract that the migration chain is the single schema source of truth. New `set_chunk_time_interval` migration affects future chunks only, safe regardless of rewrite timing.

### [PASS] Verification walkthrough aligns with parent architecture query discipline

Always sets `statement_timeout` before prod queries (matching the slice 168 `CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT` discipline). R5 closed-window parity query is the verification discriminator, and the cold-start DB check uses throwaway DB per DB-protection rules.
