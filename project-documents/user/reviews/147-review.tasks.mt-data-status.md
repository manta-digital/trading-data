---
docType: review
layer: project
reviewType: tasks
slice: mt-data-status
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/147-tasks.mt-data-status.md
aiModel: z-ai/glm-5.1
status: complete
dateCreated: 20260504
dateUpdated: 20260504
findings:
  - id: F001
    severity: concern
    category: load-testing
    summary: "Missing load test for restated latency NFRs"
    location: unverified
  - id: F002
    severity: concern
    category: completeness
    summary: "Missing `--all` row-count warning footer"
    location: unverified
  - id: F003
    severity: note
    category: test-coverage
    summary: "No test verifying default filter excludes OK rows"
    location: unverified
  - id: F004
    severity: note
    category: test-coverage
    summary: "No test for invalid `--health` flag error"
    location: unverified
---

# Review: tasks — slice 147

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.1

## Findings

### [CONCERN] Missing load test for restated latency NFRs

The slice design restates three latency NFRs: <2s end-to-end for `mt data status`, <2s for `--json` at full universe, and <100ms auto-extend overhead. T15 step 11 includes a manual `time` check but explicitly states "No automated load test is created." Per the evaluation criteria, when a parent slice restates an NFR, a load test task should exist in `tests/load/`. A `tests/load/test_data_status_latency.py` (or similar) that gates on the <2s threshold should be added, along with a CI wiring task to gate on it. The manual timing in T15 is a reasonable supplementary check but doesn't satisfy the automated gating requirement.

### [CONCERN] Missing `--all` row-count warning footer

Decision C specifies: "`--all` flag: print every row including OK. Footer warns '57,234 rows printed; use `--health` or `--symbol` to filter.'" Neither T4 (rendering) nor T8 (command) includes this warning behavior. T4's `render_status_footer` only produces the `OK: N GAPS: N STALE: N FAILED: N` summary line. A task or subtask should explicitly implement the `--all` row-count warning — either in `render_status_footer` (adding a conditional parameter) or in the command logic — and a corresponding test should verify the warning appears when `--all` is used.

### [NOTE] No test verifying default filter excludes OK rows

T9's `test_status_all_flag` verifies that `--all --json` includes OK rows (SC2), but no test verifies the inverse: that the default invocation (no `--health`, no `--all`) excludes OK rows from the output. This is the core of Decision C's default filter behavior and directly supports SC1 ("filtered to non-OK rows"). A `test_status_default_excludes_ok` or similar should assert that `mt data status --json` (no flags) returns zero rows with `health == "OK"`.

### [NOTE] No test for invalid `--health` flag error

T8 specifies that `--health` is "validated against allowed values" with `typer.BadParameter` raised for unknown values, but no integration test (T9) covers this error path. Adding a `test_status_invalid_health_flag` that asserts a non-zero exit and error message for `--health INVALID` would close this gap.
