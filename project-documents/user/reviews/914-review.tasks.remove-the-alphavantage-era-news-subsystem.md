---
docType: review
layer: project
reviewType: tasks
slice: remove-the-alphavantage-era-news-subsystem
project: trading-data
verdict: FAIL
sourceDocument: project-documents/user/tasks/914-tasks.remove-the-alphavantage-era-news-subsystem.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260808
dateUpdated: 20260808
reviewedSha: 2c8ea3b3c2efdcb28d958e9752fbc1cb0e4fdc13
findings:
  - id: F001
    severity: fail
    category: missing-task
    summary: "Load test verification is missing despite being a stated success criterion"
    location: unverified
  - id: F002
    severity: fail
    category: sequencing
    summary: "Integration test deletion is not followed by a test-with verification task"
    location: project-documents/user/tasks/914-tasks.remove-the-alphavantage-era-news-subsystem.md:2.1-2.3
  - id: F003
    severity: concern
    category: commit-distribution
    summary: "Task 1.2 has no immediately-following commit checkpoint tied to it"
    location: project-documents/user/tasks/914-tasks.remove-the-alphavantage-era-news-subsystem.md:phase-1
  - id: F004
    severity: concern
    category: scope-clarity
    summary: "Phase 4.1 ruff/mypy baseline comparison is ambiguous"
    location: project-documents/user/tasks/914-tasks.remove-the-alphavantage-era-news-subsystem.md:4.1
  - id: F005
    severity: concern
    category: scope-clarity
    summary: "Task 4.2's \"30-second pytest timeout\" claim is unverifiable as written"
    location: project-documents/user/tasks/914-tasks.remove-the-alphavantage-era-news-subsystem.md:4.2
  - id: F006
    severity: note
    category: process-observation
    summary: "Task 5.1 modifies the slice design as a work artifact"
    location: project-documents/user/tasks/914-tasks.remove-the-alphavantage-era-news-subsystem.md:5.1
  - id: F007
    severity: note
    category: commit-distribution
    summary: "Phase 1 commit message may conflict with Phase 2 commit message boundary"
    location: project-documents/user/tasks/914-tasks.remove-the-alphavantage-era-news-subsystem.md:phase-1-phase-2
---

# Review: tasks — slice 914

**Verdict:** FAIL
**Model:** minimax/minimax-m3

## Findings

### [FAIL] Load test verification is missing despite being a stated success criterion

The slice design's Technical Requirements state: "Per-subpackage test suites (unit, integration, load) all pass, matching or improving on the pre-slice baseline." The tasks only cover unit and integration tiers (Phase 4.2). There is no task to run the load test suite, and Phase 4.2's success criteria explicitly mention "no timeout hazard observed" in pytest, which is a different artifact than load test execution. The load tier must be run to satisfy this criterion. Note: no NFR restatement exists in the slice, so the `tests/load/` task and CI-wiring requirements from the evaluation criteria are not triggered — but the explicit per-subpackage success criterion still requires a load test run task.

### [FAIL] Integration test deletion is not followed by a test-with verification task

The test-with pattern requires that test tasks immediately follow their implementation tasks with verification. Phase 2 deletes integration tests (2.2) but no task immediately runs the integration suite to confirm the deletion didn't break imports or test discovery in surrounding files. Phase 4.2 runs the full suite at the end, but the immediate post-deletion verification step that would catch a test-collection error (e.g., a conftest that referenced the deleted modules) is absent. A task to run integration test discovery between 2.2 and 2.3, or immediately after 2.3, would satisfy the pattern.

### [CONCERN] Task 1.2 has no immediately-following commit checkpoint tied to it

Phase 1 bundles three sub-tasks (1.1 deletion, 1.2 grep verification) under a single commit. The grep verification in 1.2 is what proves the deletion is clean; if it fails, the commit should be blocked. This is functionally fine but the success criteria for 1.2 are documentation only — there is no enforcement mechanism. Consider making 1.2 a hard gate before the Phase 1 commit is made.

### [CONCERN] Phase 4.1 ruff/mypy baseline comparison is ambiguous

The success criteria say "pass at or below the pre-slice baseline" but a junior AI executing this task has no clear way to determine the pre-slice baseline numbers. The slice design's Verification Walkthrough does not record these. The task should either reference where the baseline is recorded (e.g., "see slice 913's wrap-up") or include a step to capture the baseline before Phase 1 begins. Currently the verification is partially subjective.

### [CONCERN] Task 4.2's "30-second pytest timeout" claim is unverifiable as written

The success criterion states "no test run hits the 30-second pytest timeout attributable to the former `pymongo` server-RTT background thread." A junior AI cannot distinguish a 30-second timeout attributable to the pymongo thread from any other 30-second timeout. The slice design mentions that the timeout "has taken an unrelated neighboring test down with it in an otherwise-clean run" but provides no mechanism to attribute the cause after the fact. Either drop this verification (since the cause is removed by construction) or replace it with a concrete signal like "all tests complete in under the per-test timeout limit."

### [NOTE] Task 5.1 modifies the slice design as a work artifact

Editing the slice design's Verification Walkthrough with actual output during closeout is reasonable for reproducibility, but worth noting that this means the design doc becomes a mutable execution artifact rather than a frozen spec. A separate closeout report (or appending a "Post-Execution Verification" section) might be cleaner, but this is a stylistic call, not a defect.

### [NOTE] Phase 1 commit message may conflict with Phase 2 commit message boundary

The slice design's Implementation Notes explicitly state: "A single commit deleting both source and tests together is correct; splitting further adds no safety." The task breakdown splits source deletion and test deletion into two separate commits. This is a deliberate deviation from the design's recommendation; flagging for the Project Manager to confirm intent. The split is defensible (it makes the source-only commit bisectable for blame) but contradicts the design's stated approach.
