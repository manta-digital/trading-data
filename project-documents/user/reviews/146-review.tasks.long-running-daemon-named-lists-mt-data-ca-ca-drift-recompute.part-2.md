---
docType: review
layer: project
reviewType: tasks
slice: long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute
project: squadron
verdict: FAIL
sourceDocument: project-documents/user/tasks/146-tasks.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute-2.md
aiModel: z-ai/glm-5.1
status: complete
dateCreated: 20260503
dateUpdated: 20260503
findings:
  - id: F001
    severity: fail
    category: completeness
    summary: "SC13 (no deadlock under co-execution) has no corresponding task"
    location: project-documents/user/tasks/146-tasks.long-running-daemon-nt-data-ca-ca-drift-recompute-2.md
  - id: F002
    severity: concern
    category: nfr-coverage
    summary: "No load test task in `tests/load/` for restated NFRs"
    location: project-documents/user/tasks/146-tasks.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute-2.md
  - id: F003
    severity: concern
    category: sequencing
    summary: "T26 depends on T27 but is sequenced before it"
    location: project-documents/user/tasks/146-tasks.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute-2.md
  - id: F004
    severity: concern
    category: test-coverage
    summary: "SC8 and SC9 (CA-drift recompute fires / no-op) lack automated integration tests"
    location: project-documents/user/tasks/146-tasks-long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute-2.md
  - id: F005
    severity: concern
    category: test-coverage
    summary: "SC3 (--list NAME exits when scope drains) lacks automated integration test"
    location: project-documents/user/tasks/146-tasks.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute-2.md
  - id: F006
    severity: concern
    category: test-coverage
    summary: "SC6 (200-credit bulk CA verification) not explicitly tested in automation"
    location: project-documents/user/tasks/146-tasks.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute-2.md
  - id: F007
    severity: concern
    category: scoping
    summary: "T29 STOP-GATE \"expected drift-recompute side effects\" is ambiguous"
    location: project-documents/user/tasks/146-tasks.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute-2.md
  - id: F008
    severity: note
    category: traceability
    summary: "T22 (ca show / ca list) traces to Outputs but not to a specific success criterion"
    location: project-documents/user/tasks/146-tasks.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute-2.md
  - id: F009
    severity: note
    category: test-organization
    summary: "Test-with pattern not followed for T21–T23"
    location: project-documents/user/tasks/146-tasks.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute-2.md
---

# Review: tasks — slice 146

**Verdict:** FAIL
**Model:** z-ai/glm-5.1

## Findings

### [FAIL] SC13 (no deadlock under co-execution) has no corresponding task

Success criterion 13 requires demonstrating that `mt data daemon run` and a refetch stand-in running simultaneously serialize via advisory lock with no deadlock over 30 minutes. No task in Part 1 (per the projectState summary) or Part 2 creates this test. The SC itself suggests a "test stand-in" for slice 148's refetch, making it feasible within this slice. A success criterion with zero task coverage must be addressed before the slice can be considered complete.

### [CONCERN] No load test task in `tests/load/` for restated NFRs

The slice design restates measurable NFRs: single-symbol fast path ≤ ~90s API time, RSS < 500 MB, token-bucket overhead < 1 ms/consume, and steady-state credit costs. No task creates a load test under `tests/load/`. T28's integration test checks the ~90s target but is not a dedicated load test, and it does not cover memory, per-call overhead, or steady-state credit budgets. Per the review criteria, if the slice restates NFRs, a `tests/load/` task should exist, and a CI wiring task should gate on it. Neither is present.

### [CONCERN] T26 depends on T27 but is sequenced before it

T26 (integration test — daemon performs `ca update` inline) acknowledges it needs the `mt data daemon run` CLI from T27 and includes the note "T26 runs after T27 lands." Despite this, T26 is listed before T27 in the task ordering. The tasks should be reordered so T27 precedes T26, or T26 should be renumbered, to prevent a junior AI from attempting T26 before its dependency exists.

### [CONCERN] SC8 and SC9 (CA-drift recompute fires / no-op) lack automated integration tests

SC8 requires seeding a stale `last_adjusted_ca_snapshot_id`, running the daemon, and verifying band UPDATEs fire, the snapshot advances, and the adj_* invariant holds. SC9 requires a second run issues zero drift-path UPDATEs. Neither has a dedicated automated test task in Part 2. They are only covered by the manual walkthrough (T32, steps 4–5). The `ca_drift` module was shipped in Part 1, but the specific end-to-end scenarios in SC8/SC9 require the full runner (completed in Part 2) and should have automated integration tests.

### [CONCERN] SC3 (--list NAME exits when scope drains) lacks automated integration test

SC3 requires `mt data daemon run --list priority1` to finish the priority1 set and exit 0. T27 creates the `--list NAME` flag with its termination default, but T28 only tests `--symbols SPY --stop-when-done`. No automated test verifies that `--list`-scoped invocations drain and exit. The walkthrough (T32, step 3) covers it manually, but an automated regression test should exist.

### [CONCERN] SC6 (200-credit bulk CA verification) not explicitly tested in automation

SC6 requires verifying that `mt data ca update` (no flags) costs exactly 200 credits via an instrumented test intercepting EODHD calls and asserting exactly two bulk calls. T24's unit tests mock the bulk helpers (bypassing credit accounting) and its integration tests focus on idempotent re-ingest and row-for-row diff. Credit-count verification is only in the manual walkthrough (step 6). An automated assertion on call count and credit consumption should be added to T24 or a companion test task.

### [CONCERN] T29 STOP-GATE "expected drift-recompute side effects" is ambiguous

T29 says "any non-zero diff (other than expected drift-recompute side effects) → halt." The term "expected drift-recompute side effects" is not defined. The new runner includes CA-drift recompute (which the old one-shot commands lack), so `last_adjusted_ca_snapshot_id` and potentially `adj_*` values will differ when CAs have changed. Without a concrete enumeration of what differences are expected, a junior AI implementing this STOP-GATE cannot reliably decide whether to halt or proceed. The task should explicitly list the columns/tables where drift-recompute differences are acceptable.

### [NOTE] T22 (ca show / ca list) traces to Outputs but not to a specific success criterion

T22 creates `mt data ca show` and `mt data ca list`, which are listed in the slice design's Outputs section but not referenced by any success criterion. This is not scope creep (the Outputs section explicitly includes them), but there is no automated verification that these commands produce correct output against seeded data. Consider adding a lightweight assertion to T24.

### [NOTE] Test-with pattern not followed for T21–T23

Three implementation tasks (T21, T22, T23) precede their sole test task (T24). The test-with pattern recommends that tests immediately follow their implementation. While T24 is still completable, the gap means T21–T23 could be merged or T24 could be split into per-task test subtasks that follow each implementation directly.
