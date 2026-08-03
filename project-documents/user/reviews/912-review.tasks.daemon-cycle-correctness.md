---
docType: review
layer: project
reviewType: tasks
slice: daemon-cycle-correctness
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/912-tasks.daemon-cycle-correctness.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260803
dateUpdated: 20260803
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "All twelve success criteria map to tasks"
    location: project-documents/user/tasks/912-tasks.daemon-cycle-correctness.md
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Task sequencing respects all dependencies"
    location: project-documents/user/tasks/912-tasks.daemon-cycle-correctness.md
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Task 6.4 correctly scopes issue #4 as out of closure"
    location: project-documents/user/tasks/912-tasks.daemon-cycle-correctness.md#6.4
  - id: F004
    severity: pass
    category: uncategorized
    summary: "No NFR restatement, no load test required"
    location: project-documents/user/slices/912-slice.daemon-cycle-correctness.md
  - id: F005
    severity: concern
    category: test-coverage
    summary: "Test-with pattern is violated; tests are batched in Task 5"
    location: project-documents/user/tasks/912-tasks.daemon-cycle-correctness.md
  - id: F006
    severity: concern
    category: criteria-clarity
    summary: "Task 1.3 success criterion is self-contradictory"
    location: project-documents/user/tasks/912-tasks.daemon-cycle-correctness.md#1.3
  - id: F007
    severity: concern
    category: deployment-safety
    summary: "Commit cadence is unspecified despite production reach"
    location: project-documents/user/tasks/912-tasks.daemon-cycle-correctness.md
  - id: F008
    severity: note
    category: uncategorized
    summary: "Branch note at top is information, not a tracked task"
    location: project-documents/user/tasks/912-tasks.daemon-cycle-correctness.md
---

# Review: tasks — slice 912

**Verdict:** CONCERNS
**Model:** minimax/minimax-m3

## Findings

### [PASS] All twelve success criteria map to tasks

Every functional criterion (1–6), technical criterion (7–10), and verification criterion (11–12) from the slice design has at least one corresponding task. Criterion 7 (RunnerState field rename + stamp at end) maps to Task 3.1 and Task 3.3. Criterion 8 (constant split) maps to Task 1.1–1.4. Criterion 9 (enum, no string branching) maps to Task 4.1–4.3. Criterion 12 (prod verification on .144) maps to Task 6.2 with the prod-query discipline (`statement_timeout`, no expression aggregates over compressed hypertables) explicitly carried into the task body. No orphan tasks were found — Task 2.5's audit of `report.total` consumers, Task 4.5's CLI help text update, and Task 5.7's rewrite of now-obsolete tests each trace back to an explicit requirement in the slice.

### [PASS] Task sequencing respects all dependencies

Constants (Task 1) precede every consumer; the work-list derivation and `CycleReport` extension (Task 2.1–2.2) precede the runner wiring (Task 2.3, 4.2–4.3); the cadence rename (Task 3.1) precedes the predicate rewrite (3.2) and completion stamp (3.3); the enum (Task 4.1) precedes the loop rewrite (4.2) which precedes the exit-path reporting (4.3) and wait behavior (4.4); tests (Task 5) follow all implementation; gates and prod verification (Task 6) follow tests. No circular dependencies.

### [PASS] Task 6.4 correctly scopes issue #4 as out of closure

The task explicitly closes #7 and #6 (which the slice design authorizes) while leaving #4 open and adding a comment that 912 surfaces the count rather than fixing the underlying calendar assignment. This matches D6's explicit out-of-scope statement.

### [PASS] No NFR restatement, no load test required

The slice design does not restate a measurable NFR (latency, throughput, capacity). The bounded 30-minute wait and 60 s SIGTERM latency are feature behaviors of D5, not performance NFRs requiring load-test coverage. The load-test / CI-wiring check does not apply.

### [CONCERN] Test-with pattern is violated; tests are batched in Task 5

Tasks 2.1, 2.3, 3.2, 3.4, 4.2, 4.3, and 4.4 each have success criteria that reference test assertions ("unit-tested against a mock connection for each branch", "Assert this with a mock `httpx.Client` that fails the test if called", "Verify termination", "`test_sleep_caps_at_60s` still passes"), yet none of those tests are written inside the implementing task. They all land in Task 5 (subtasks 5.1–5.6), which sits at the end of the implementation sequence. A junior implementer working through Tasks 2–4 cannot verify their own work as they go — each subtask ends with a description of the test that should exist, not the test itself. Either interleave the tests with each implementation subtask, or restate the success criteria to defer the assertions explicitly to Task 5 so the implementer knows to stop at "code written, behaviour-described" rather than searching for a missing test step.

### [CONCERN] Task 1.3 success criterion is self-contradictory

Task 1.3 states two conditions that cannot both be true: "`grep -n LATE_BAR_GRACE_PERIOD src/manta_trading/data/` returns nothing" and "the constant's remaining uses are the migration and `data_status` paths only." If the migration and `data_status` paths live under `src/manta_trading/data/`, the grep will find them; if the grep is meant to return nothing, the "remaining uses" clause is wrong. Either narrow the grep to `src/manta_trading/data/acquisition/daemon/runner.py` (and accept that the second clause is misleading) or keep the wider grep and correct the second clause to "the constant is used only in the migration and `data_status` paths, which the grep will surface — verify those are unchanged." A junior implementer will not know which clause to honour.

### [CONCERN] Commit cadence is unspecified despite production reach

The `projectState` metadata states "the daemon runs continuously on prod .144 from a git checkout, so every change here reaches production on the next restart." The breakdown does not specify commit checkpoints between tasks. With Task 1's constant split, Task 2's SQL change, and Task 3's runner restructure each independently restartable, an operator who restarts mid-implementation gets whatever subset has landed. The breakdown should either name commits at task boundaries (so a failed restart can be reverted to the last green task boundary) or state explicitly that intermediate commits are local-only and not deployed — current silence on this is a real risk for a daemon that reaches prod on next restart.

### [NOTE] Branch note at top is information, not a tracked task

The `## Branch` section flags that work must happen on `912-slice.daemon-cycle-correctness` branched from `trading-data-maintenance` (not `main`) and that the eventual merge target needs PM confirmation before integration. This is useful metadata but not a discrete task. Task 6.3 (`sq review code 912`) implies the integration step but does not capture the "confirm merge target with PM" prerequisite. Consider adding a Task 0 or folding this confirmation into 6.3's success criteria so it cannot be silently skipped.
