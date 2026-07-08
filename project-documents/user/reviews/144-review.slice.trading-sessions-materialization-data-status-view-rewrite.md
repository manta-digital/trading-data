---
docType: review
layer: project
reviewType: slice
slice: trading-sessions-materialization-data-status-view-rewrite
project: squadron
verdict: UNKNOWN
sourceDocument: project-documents/user/slices/144-slice.trading-sessions-materialization-data-status-view-rewrite.md
aiModel: z-ai/glm-5.1
status: complete
dateCreated: 20260502
dateUpdated: 20260502
findings:
  - id: F001
    severity: pass
    category: alignment
    summary: "Core deliverable aligns with architecture's implementation note"
    location: 144-slice.trading-sessions-materialization-data-status-view-rewrite.md#Overview
  - id: F002
    severity: addressed
    category: scope-creep-mismatch
    summary: "Architecture assigns daemon work to slice 144; slice excludes it"
    location: 144-slice.trading-sessions-materialization-data-status-view-rewrite.md#Non-Goals
    resolution: "Arch doc updated — all daemon references (band-based adj_* writes, gap-driven backfill, cold-start stages, first_data_date population) changed from slice 144 to slice 145/146. References section updated to name both slices."
  - id: F003
    severity: addressed
    category: error-handling
    summary: "Failure modes for new I/O paths not enumerated with explicit handling strategies"
    location: 144-slice.trading-sessions-materialization-data-status-view-rewrite.md#Risks
    resolution: "Risks section extended with explicit entries for: DB connection failure in get_trading_hours (propagates, no swallowing); maintenance job interruption (idempotent upsert, re-run to recover); CTE timeout under load (index design + NFR check; materialized view is the documented escalation path)."
  - id: F004
    severity: pass
    category: nfr
    summary: "NFR from architecture restated with specific target"
    location: 144-slice.trading-sessions-materialization-data-status-view-rewrite.md#Success-Criteria
  - id: F005
    severity: pass
    category: schema-alignment
    summary: "Schema and view CTE design consistent with architecture"
    location: 144-slice.trading-sessions-materialization-data-status-view-rewrite.md#Schema
  - id: F006
    severity: note
    category: operational-awareness
    summary: "Grace period constant baked into view definition at build time"
    location: 144-slice.trading-sessions-materialization-data-status-view-rewrite.md#View-Rewrite-(Migration-028)
  - id: F007
    severity: pass
    category: error-handling
    summary: "Out-of-horizon behavior is explicit and fail-loud"
    location: 144-slice.trading-sessions-materialization-data-status-view-rewrite.md#Out-of-horizon-behavior
---

# Review: slice — slice 144

**Verdict:** UNKNOWN
**Model:** z-ai/glm-5.1

## Findings

### [PASS] Core deliverable aligns with architecture's implementation note

The slice directly implements what the architecture document's implementation note (arch §"Performance pattern") promises for slice 144: materialized `trading_sessions` table, rewritten `data_status` view CTE projecting `target_end_ts`, and Python `TradingCalendar` refactored to read from the same table. Schema shape, index design, join key (`trading_calendar_id`), and single-source-of-truth goal all match the architecture's specification.

### [ADDRESSED] Architecture assigns daemon work to slice 144; slice excludes it

The architecture document stated in two places that "Slice 144 reopens 120's daemon code to add it." The arch doc has been updated: all daemon references (band-based adj_* writes, gap-driven backfill loop, cold-start stages, `first_data_date` population) now correctly point to slice 145; CA-detection and bulk-EOD steady-state point to slice 146. The References section at the bottom of the arch doc names both slices explicitly.

### [ADDRESSED] Failure modes for new I/O paths not enumerated with explicit handling strategies

The Risks section of the slice has been extended with explicit entries for each flagged path: (a) DB connection failure in `get_trading_hours` / `is_trading_day` — propagates as standard DB exceptions, no swallowing, handled at process boundary; (b) maintenance job interrupted mid-batch — `ON CONFLICT DO UPDATE` upsert is idempotent, recovery is re-running `mt data --extend`; (c) `data_status` CTE timeout under load — index design + NFR check in verification walkthrough step 6; escalation path is materializing `data_status` (existing future-work item).

### [PASS] NFR from architecture restated with specific target

Success criterion #4 restates the architecture's NFR: "query latency at full universe scope stays sub-second (slice 142's NFR holds)." The specific target (sub-second, full-universe scope, ~57k symbols) is carried forward from the arch document's §"Performance pattern" and §"One status view." The verification walkthrough step 6 includes a timing check.

### [PASS] Schema and view CTE design consistent with architecture

The `trading_sessions(calendar_id, session_date, session_open_utc, session_close_utc)` schema matches what the architecture's implementation note specifies. The primary key on `(calendar_id, session_date)` and the index on `(calendar_id, session_close_utc)` directly support the arch's CTE pattern (`MAX(session_close_utc) WHERE session_close_utc + grace < NOW()`). The view rewrite correctly uses `calendar_id` (more precise than the arch's placeholder `exchange`) while preserving the join on `i.trading_calendar_id` as the arch requires. The LEFT JOIN preserves symbols with unknown calendars, consistent with the arch's intent.

### [NOTE] Grace period constant baked into view definition at build time

The `LATE_BAR_GRACE_PERIOD` constant is emitted as a literal `'30 minutes'` into the view SQL at migration-build time. Changing it requires a follow-up migration to rebuild the view. The slice acknowledges this trade-off ("not free, but rare") and the arch marks the constant as configurable. This is a reasonable design given the rarity of changes, but worth noting as an operational awareness item — operators who change `LATE_BAR_GRACE_PERIOD` in Python config must also run a view-rebuild migration for SQL to agree.

### [PASS] Out-of-horizon behavior is explicit and fail-loud

The slice explicitly rejects silent fallback and auto-extension, choosing `OutOfHorizonError` with a message naming the maintenance command. This aligns with the architecture's philosophy of honest surfaced state over silent degradation, and the dual mitigation (90-day `--strict` warning + loud exception) provides both early detection and last-resort protection.
