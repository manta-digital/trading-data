---
docType: review
layer: project
reviewType: slice
slice: schema-migration-and-cold-start
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/slices/142-slice.schema-migration-and-cold-start.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260501
dateUpdated: 20260501
findings:
  - id: F001
    severity: concern
    category: scope-creep
    summary: "`data_status` view omits `bars_expected` column stated in architecture schema"
    location: 142-slice.schema-migration-and-cold-start.md#Migration 021 — data_status view
  - id: F002
    severity: concern
    category: error-handling
    summary: "EODHD probe failure mode not explicitly stated"
    location: 142-slice.schema-migration-and-cold-start.md#D1. Pre-flight is mandatory, not opt-in
  - id: F003
    severity: note
    category: integration-points
    summary: "`data_status` uses LEFT JOIN on `exchange_completed_close`; architecture uses regular JOIN"
    location: 142-slice.schema-migration-and-cold-start.md#Migration 021 — data_status view
  - id: F004
    severity: note
    category: under-specification
    summary: "Pre-flight count range [30_000, 80_000] accepts 50k universe growth without explanation"
    location: 142-slice.schema-migration-and-cold-start.md#D1. Pre-flight is mandatory, not opt-in
---

# Review: slice — slice 142

**Verdict:** CONCERNS
**Model:** minimax/minimax-m2.7

## Findings

### [CONCERN] `data_status` view omits `bars_expected` column stated in architecture schema

The architecture document's `data_status` view schema explicitly includes `bars_expected = expected sessions from trading_calendar within target window` as a projected column. Slice 142's view DDL does not project this column.

The slice provides rationale: "The view does not project `bars_expected`. Computing expected sessions per symbol per window is a per-row operation against `trading_calendar` that doesn't fit the CTE pattern at scale. Slice 145's status command computes `bars_expected` on the client side per symbol when the operator passes `--symbol X`."

This is a valid operational tradeoff, but it is a deviation from the stated architecture schema that should be explicitly called out as a designed-out column (similar to how the slice explicitly calls out `coverage_gaps` as "designed out, not deferred"). The slice's rationale lives in the migration notes rather than a formal architectural deviation statement.

**Recommendation:** Add a "Designed-out columns" note to the `data_status` section explicitly naming `bars_expected` as deferred to client-side computation, so future readers understand this was a deliberate decision, not an oversight.

### [CONCERN] EODHD probe failure mode not explicitly stated

Pre-flight rule D1.5 specifies an EODHD liveness probe and states the CLI "halts on failure," but does not specify the failure mode behavior (network timeout, HTTP error, empty response, invalid credentials). The slice documents success criteria for pre-flight failures but does not enumerate failure modes for the probe itself.

The architecture document lists EODHD provider-data limitations but does not specify error-handling strategy for the probe I/O path. Per the review criteria, "failure modes enumerated for each new I/O path or message type" should have explicit handling strategy.

**Recommendation:** Add explicit probe failure mode handling to D1.5: e.g., "Network timeout (>10s) → halt; HTTP 4xx (auth error) → halt with credential guidance; HTTP 5xx → retry once, then halt; Empty response → halt."

### [NOTE] `data_status` uses LEFT JOIN on `exchange_completed_close`; architecture uses regular JOIN

The architecture's performance pattern example shows `JOIN exchange_completed_close ec ON ec.exchange = i.trading_calendar_id`. Slice 142 uses `LEFT JOIN exchange_completed_close ec ON ec.calendar_id = s.trading_calendar_id`.

The slice's approach is more defensive: unknown-calendar symbols appear with `target_end_ts = NULL` and `health = STALE`, rather than being silently excluded. The architecture's rationale for joining on `trading_calendar_id` (vs `venue`) is preserved. The divergence is intentional (per slice's "Special Considerations" section) but not reconciled against the architecture's example DDL.

This is informational—the slice's approach is arguably better—but it represents a stated deviation from the architecture's example implementation.

### [NOTE] Pre-flight count range [30_000, 80_000] accepts 50k universe growth without explanation

Pre-flight rule D1.3 checks instruments row count in the range `[30_000, 80_000]`, with rationale stating "tighter than `> 0` to catch a partially-rolled-back 141 run, looser than the literal observed 32_875 to absorb day-over-day universe drift."

The architecture's Step 1 specification says "~50,000–60,000 range." Slice 141 produces ~33k (per verification walkthrough). The 80k upper bound accommodates ~2.4× growth from 33k, but no rationale explains why this ceiling is appropriate or what triggers would indicate growth is too large (e.g., >80k should fail pre-flight).

This is minor—pre-flight correctly catches missing/invalid states—but the upper bound choice lacks architectural grounding.
