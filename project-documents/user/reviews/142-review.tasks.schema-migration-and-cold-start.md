---
docType: review
layer: project
reviewType: tasks
slice: schema-migration-and-cold-start
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/142-tasks.schema-migration-and-cold-start.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260501
dateUpdated: 20260501
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Cross-reference success criteria → tasks"
    location: 142-tasks.schema-migration-and-cold-start.md
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Task → success criterion traceability (no scope creep)"
    location: 142-tasks.schema-migration-and-cold-start.md
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Test-implementation pairing (test-with pattern)"
    location: 142-tasks.schema-migration-and-cold-start.md
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Task sequencing and dependencies"
    location: 142-tasks.schema-migration-and-cold-start.md
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Tasks are independently completable"
    location: 142-tasks.schema-migration-and-cold-start.md
  - id: F006
    severity: pass
    category: uncategorized
    summary: "Commit checkpoints distributed throughout"
    location: 142-tasks.schema-migration-and-cold-start.md
  - id: F007
    severity: concern
    category: uncategorized
    summary: "`HISTORY_HORIZON` interval literal not explicitly covered in any task"
    location: unverified
  - id: F008
    severity: concern
    category: uncategorized
    summary: "SC16 (`EXPLAIN` plan verification) is not a standalone test task"
    location: unverified
  - id: F009
    severity: note
    category: uncategorized
    summary: "`HISTORY_HORIZON` unbounded case may need explicit migration logic"
    location: unverified
  - id: F010
    severity: note
    category: uncategorized
    summary: "`HISTORY_MONTHS` migration (T3) ordering relative to constant creation (T1)"
    location: 142-tasks.schema-migration-and-cold-start.md
  - id: F011
    severity: note
    category: uncategorized
    summary: "Success criterion SC15 references \"migrations 018–023\"; no migration 023 exists"
    location: 142-tasks.schema-migration-and-cold-start.md
---

# Review: tasks — slice 142

**Verdict:** CONCERNS
**Model:** minimax/minimax-m2.7

## Findings

### [PASS] Cross-reference success criteria → tasks

All 23 success criteria (SC1–SC23) from the slice design have at least one corresponding task. Migrations 018–022 are covered by T10–T14; the pre-flight rules map to T21/T22; health rules map to T26; dead-code removal maps to T17–T19/T27; SC23 (data_status columns) is covered by T13/T26.

---

### [PASS] Task → success criterion traceability (no scope creep)

Every task traces to at least one listed success criterion. All "out of scope" items from the slice design (`update_data_gaps`, `coalesce_data_gaps`, `compute_missing_ranges`, advisory locking, tick granularity) are correctly absent from the task list.

---

### [PASS] Test-implementation pairing (test-with pattern)

Tests immediately follow their implementation tasks throughout: T1→T2 (constants), T4→T5 (enums), T7–T8→T9 (migration helpers), T10–T14→T15 (migrations 018–022 integration), T16→T20 (DTO surgery), T21→T22 (pre-flight), T24→T25 (CLI integration), T26 stands alone for view health rules. No implementation task lacks a corresponding test.

---

### [PASS] Task sequencing and dependencies

The ordering is logically sound: constants and enums first (no dependencies), then migrations, then the orchestrator/CLI wiring, then surgical code removal, then integration tests. No circular dependencies exist.

---

### [PASS] Tasks are independently completable

Each task has explicit acceptance criteria (grep commands, pyright invocations, `pytest` targets). A junior AI can verify completion without cross-referencing other tasks.

---

### [PASS] Commit checkpoints distributed throughout

Checkpoint tests exist at T2 (constants), T5 (enums), T9 (helpers), T15 (migrations), T20 (DTO surgery), T22 (pre-flight), T25 (CLI), T26 (health rules), T27 (sweep) — spread across the full 28-task sequence, not batched at the end. T28 is the explicit build-and-commit task.

---

### [CONCERN] `HISTORY_HORIZON` interval literal not explicitly covered in any task

**Severity: CONCERN**

Migration 021's `data_status` view DDL (slice design §Migration Plan) contains a `WHERE` clause filter using `{HISTORY_HORIZON}`:

```sql
WHERE i.delisted_at_eodhd = FALSE
   OR i.delisted_date IS NULL
   OR i.delisted_date >= (NOW() - INTERVAL '{HISTORY_HORIZON}')
```

This placeholder must be rendered from `DAILY_HISTORY_MONTHS` (from `manta_trading.constants`) into a Postgres interval string — e.g., if `DAILY_HISTORY_MONTHS` is None (unbounded), the filter's third disjunct should degenerate to `TRUE`. This rendering logic is not mentioned in T8 (`_interval_literal` helper) or T21 (pre-flight module). Only `LATE_BAR_GRACE_PERIOD`, `DAILY_STALENESS_THRESHOLD`, and `MINUTE_STALENESS_THRESHOLD` are explicitly called out in T8.

**Recommendation:** Either expand T8 to explicitly include `HISTORY_HORIZON` rendering (with the unbounded/None case), or add a note that `HISTORY_HORIZON` is derived from `DAILY_HISTORY_MONTHS` within the migration 021 writing task (T13).

---

### [CONCERN] SC16 (`EXPLAIN` plan verification) is not a standalone test task

**Severity: CONCERN**

SC16 requires that `EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) SELECT * FROM data_status` shows a CTE-driven plan (Hash Aggregate, no per-row Function Scan, < 1000ms). The verification walkthrough in the slice design (Step 5) runs this manually with `psql`. The task list has no dedicated test asserting this plan shape.

T23 (`run_migration`) mentions `run_post_flight` which checks `data_gaps` count, `data_status` row count, and queries `EXPLAIN` — but this is integration-level, not a pytest-able assertion with pass/fail on plan structure. T15 (migration integration test) verifies schema existence but not plan quality.

**Recommendation:** Either add a note to T15 or T26 that the post-flight `EXPLAIN` check is part of the integration test assertion, or add a separate lightweight test (e.g., `tests/integration/test_data_status_plan.py`) that parses the `EXPLAIN` output and asserts `Function Scan` count == 0.

---

### [NOTE] `HISTORY_HORIZON` unbounded case may need explicit migration logic

When `DAILY_HISTORY_MONTHS is None`, the migration DDL should emit no upper-bound on `delisted_date` (the third disjunct becomes `TRUE`). This is documented in the slice design but the task list (T13) does not call out this conditional rendering as an acceptance criterion. If the current plan always renders the interval and the third disjunct is always present, a `None` value would produce invalid SQL (`NOW() - INTERVAL 'None'`). The fix is likely a one-line conditional in the migration builder, but the task should state it explicitly.

---

### [NOTE] `HISTORY_MONTHS` migration (T3) ordering relative to constant creation (T1)

T3 ("Migrate `HISTORY_MONTHS` to `MINUTE_HISTORY_MONTHS`") should logically come **after** T1 creates the constants module and **after** T4/T5 create the quality package, since T3 changes `minute/freshness.py` which may import from the quality package in the future. The current ordering (T1, T2, T3, T4, T5) is acceptable but the dependency is implicit. No functional risk, but noting for clarity.

---

### [NOTE] Success criterion SC15 references "migrations 018–023"; no migration 023 exists

The slice design's SC15 states: *"Migrations 018–022, the TRUNCATE, and the DROP TABLE all run in one BEGIN/COMMIT."* The text in the migration plan section refers to "migration 023's transaction body" for the TRUNCATE, but the task list correctly treats TRUNCATE as an implementation detail of `migrate_cold_start.py` (T23), not as a separate migration entry. This is a documentation inconsistency in the slice design's wording, not in the task file. The task file is correct. No action required, but the slice design should be amended to remove the "023" reference for consistency.
