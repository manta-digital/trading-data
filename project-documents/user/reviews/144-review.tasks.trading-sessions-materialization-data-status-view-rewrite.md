---
docType: review
layer: project
reviewType: tasks
slice: trading-sessions-materialization-data-status-view-rewrite
project: squadron
verdict: CONCERNS_ADDRESSED
sourceDocument: project-documents/user/tasks/144-tasks.trading-sessions-materialization-data-status-view-rewrite.md
aiModel: z-ai/glm-5.1
status: complete
dateCreated: 20260502
dateUpdated: 20260502
findings:
  - id: F001
    severity: addressed
    category: test-coverage
    summary: "Missing unit test for `_build_data_status_view_sql` CTE shape"
    location: unverified
    resolution: "Added T11a: unit test asserting exchange_completed_close CTE present, NULL stub absent, grace-period literal sourced from _interval_literal()."
  - id: F002
    severity: declined
    category: nfr-coverage
    summary: "No load test in `tests/load/` for the sub-second query latency NFR"
    location: unverified
    resolution: "NFR validated by T12 integration test (EXPLAIN ANALYZE) and T18 manual walkthrough step 6. The CTE returns ~5 rows (one per calendar); a dedicated tests/load/ suite with CI wiring is disproportionate to the risk."
  - id: F003
    severity: addressed
    category: commit-checkpoint
    summary: "`mt data --extend` CLI (T9+T10) lacks a commit checkpoint"
    location: unverified
    resolution: "Added commit to T10: `feat: add mt data --extend CLI for trading_sessions horizon maintenance`."
  - id: F004
    severity: pass
    category: traceability
    summary: "Success criteria 1–3, 5–10 all trace to specific tasks"
    location: unverified
  - id: F005
    severity: pass
    category: sequencing
    summary: "Task sequencing respects dependencies with no circular dependencies"
    location: unverified
  - id: F006
    severity: pass
    category: task-sizing
    summary: "Tasks are appropriately scoped for a junior AI"
    location: unverified
  - id: F007
    severity: addressed
    category: task-clarity
    summary: "T17 (\"Adapt existing TradingCalendar tests\") is underspecified for a junior AI"
    location: unverified
    resolution: "T17 now includes a grep command to locate test files, names the likely primary file, and gives a concrete example of the mock→seed transformation expected."
---

# Review: tasks — slice 144

**Verdict:** CONCERNS_ADDRESSED
**Model:** z-ai/glm-5.1

## Findings

### [ADDRESSED] Missing unit test for `_build_data_status_view_sql` CTE shape

Added T11a between T11 and T12: unit test asserting `_build_data_status_view_sql(include_daily_branch=True)` produces SQL containing the `exchange_completed_close` CTE identifier and `session_close_utc`, that the `NULL::TIMESTAMPTZ` stub is absent, and that the grace-period literal is sourced via `_interval_literal()`. Runs without a DB connection.

### [DECLINED] No load test in `tests/load/` for the sub-second query latency NFR

The NFR is validated at two levels: T12 integration test (`EXPLAIN ANALYZE` against the test universe) and T18 manual walkthrough step 6 (`\timing` against the dev DB). The CTE returns approximately five rows — one per seeded calendar — so a dedicated `tests/load/` suite with CI wiring is disproportionate to the actual risk. Existing coverage is sufficient.

### [ADDRESSED] `mt data --extend` CLI (T9+T10) lacks a commit checkpoint

Added commit to T10: `feat: add mt data --extend CLI for trading_sessions horizon maintenance`. The CLI group now has its own checkpoint consistent with T8 and T12.

### [PASS] Success criteria 1–3, 5–10 all trace to specific tasks

Every numbered success criterion from the slice design has at least one task that directly addresses it: SC1→T5/T8, SC2→T7/T8, SC3→T4/T8, SC5→T9/T10, SC6→T13–T16, SC7→T2/T13/T14/T15/T16, SC8→T12, SC9→T3/T4, SC10→T18/T19. Task-to-criterion traceability is solid.

### [PASS] Task sequencing respects dependencies with no circular dependencies

Tasks are ordered so that dependencies come first: T3 (extract function) before T4 (test it) and T7/T9 (use it); T5 (create table) before T7 (populate it) and T11 (reference it in view); T2 (exception class) before T13/T15 (raise it); T6 (constants) before T7/T9 (consume them). Test tasks immediately follow their implementation tasks (T4→T3, T8→T7, T10→T9, T12→T11, T14→T13, T16→T15). No circular dependencies exist.

### [PASS] Tasks are appropriately scoped for a junior AI

All tasks have clear, specific success criteria and bounded scope. No task appears too large (the largest—T7, T9, T11, T15—are each focused on a single migration or refactoring with well-defined inputs/outputs). T6 (adding two constants) is small but justifiable since it establishes the horizon policy before T7 and T9 consume it.

### [ADDRESSED] T17 ("Adapt existing TradingCalendar tests") is underspecified for a junior AI

T17 now includes a grep command to locate affected test files, names the likely primary file (`tests/unit/data/base/test_trading_calendar.py`), and gives a concrete description of the mock→seed transformation: replace `mock.patch("...._build_trading_hours", return_value=...)` RTH mocks with seeded `trading_sessions` rows and calls against the real method.
