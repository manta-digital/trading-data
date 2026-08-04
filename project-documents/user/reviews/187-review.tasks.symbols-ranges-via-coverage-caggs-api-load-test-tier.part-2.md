---
docType: review
layer: project
reviewType: tasks
slice: symbols-ranges-via-coverage-caggs-api-load-test-tier
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/187-tasks.symbols-ranges-via-coverage-caggs-api-load-test-tier-2.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260804
dateUpdated: 20260804
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "All nine success criteria map to tasks"
    location: project-documents/user/tasks/187-tasks.symbols-ranges-via-coverage-caggs-api-load-test-tier-2.md
  - id: F002
    severity: pass
    category: uncategorized
    summary: "CI gating for the load tier is explicitly delegated, not implicit"
    location: project-documents/user/tasks/187-tasks.symbols-ranges-via-coverage-caggs-api-load-test-tier-2.md
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Load test covers the request-latency NFR that the slice restates"
    location: project-documents/user/tasks/187-tasks.symbols-ranges-via-coverage-caggs-api-load-test-tier-2.md
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Test-with-implementation pattern is followed"
    location: project-documents/user/tasks/187-tasks.symbols-ranges-via-coverage-caggs-api-load-test-tier-2.md
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Sequencing respects file-1 to file-2 dependency and intra-file ordering"
    location: project-documents/user/tasks/187-tasks.symbols-ranges-via-coverage-caggs-api-load-test-tier-2.md
  - id: F006
    severity: concern
    category: scope-creep
    summary: "Task 14 hides conditional implementation inside a documentation task"
    location: project-documents/user/tasks/187-tasks.symbols-ranges-via-coverage-caggs-api-load-test-tier-2.md
  - id: F007
    severity: concern
    category: task-sizing
    summary: "Task 12 totals effort 11 across five subtasks; borderline oversized"
    location: project-documents/user/tasks/187-tasks.symbols-ranges-via-coverage-caggs-api-load-test-tier-2.md
  - id: F008
    severity: note
    category: sequencing
    summary: "Commit checkpoints are not explicitly per-task"
    location: project-documents/user/tasks/187-tasks.symbols-ranges-via-coverage-caggs-api-load-test-tier-2.md
  - id: F009
    severity: pass
    category: uncategorized
    summary: "No scope-creep tasks beyond the slice design"
    location: project-documents/user/tasks/187-tasks.symbols-ranges-via-coverage-caggs-api-load-test-tier-2.md
---

# Review: tasks — slice 187

**Verdict:** CONCERNS
**Model:** minimax/minimax-m3

## Findings

### [PASS] All nine success criteria map to tasks

Cross-referencing each slice success criterion:
- **SC1** (no unbounded aggregate) — covered by Task 10's integration test against real SQL and Task 15 step 2's `EXPLAIN ANALYZE`.
- **SC2** (20+ symbol equivalence) — covered by Task 15 step 3's prod walkthrough (Task 10 covers 1 symbol per D2 case; the 20+ sample is verification evidence, not a CI test, so walkthrough is the right vehicle).
- **SC3** (no-coverage-row and no-data families) — covered by Task 10's "after-edge-only" and "no-data" cases plus file-1 Task 7's unit tests.
- **SC4** (freshness reports stale with `CONTENT_EDGE_TOO_OLD`, fresh on a fixture) — file-1 Task 3 implements both halves; Task 15 step 5 verifies the prod-stale half.
- **SC5** (test pins the one-bucket detection floor) — file-1 Task 3 implements; Task 15 step 6 verifies.
- **SC6** (load tier green with traceable bounds) — Tasks 11–12 implement; Task 15 step 7 measures.
- **SC7** (`create_app(db_url=...)` seam) — file-1 Task 4 implements; Tasks 11–12 consume it.
- **SC8** (D11 decision recorded either way) — Task 13.
- **SC9** (full suite green, mypy/ruff/cf-check clean, OpenAPI drift test) — Task 16.

No gap exists for any criterion.

### [PASS] CI gating for the load tier is explicitly delegated, not implicit

The slice design's D9 explicitly assigns CI wiring to slice 907. Task 12's "Wire the gating and module docstring (D9)" subtask requires the module docstring to state "CI wiring is slice 907's deliverable, not this slice's (D9)". The delegation is captured in the task itself, satisfying the "not left implicit" rule.

### [PASS] Load test covers the request-latency NFR that the slice restates

D10.2 explicitly restates the request-latency gap (`statement_timeout` bounds statements, not requests) that 186 D12b proved. Task 12's Assertion 2 covers it with a wall-clock bound plus an assertion that the measured time exceeds `statement_timeout` without producing a 504 — exactly the structural gap the slice identifies. The dense-minute fixture (~115k rows exceeding the 75k ceiling) is built in Task 11 to support this assertion.

### [PASS] Test-with-implementation pattern is followed

Task 10 immediately tests the file-1 implementation against real caggs. Task 11's success criterion explicitly re-runs the existing 167 test after extracting the fixture, proving no behavior moved. Task 12's four assertions live in the same module as the implementation they exercise (no skip between implementation and tests). Task 13's "if code changed, tests from file-1's Tasks 5–9 still pass" closes the loop on any D11-driven code change.

### [PASS] Sequencing respects file-1 to file-2 dependency and intra-file ordering

File 1's Tasks 1–9 must complete before Task 10 starts (stated explicitly in Context Summary). Task 11 (extract fixture, add dense-minute) precedes Task 12 (assertions that consume those fixtures). Task 12 assertion 3 must precede Task 13 (D11 uses assertion-3 latencies). Task 15's "rewrite the walkthrough" must come after the measurements it documents. No circular dependencies.

### [CONCERN] Task 14 hides conditional implementation inside a documentation task

The first subtask of Task 14 (`180-slices.data-serving-api.md`, entry 7) says: "Materialize the `(187)` index **if not already done in Phase 4**; correct the scope text…". This is a conditional implementation step embedded in what is otherwise a docs-correction task. D12 in the slice design does mention "Materialize the `(187)` index" so it is in-scope, but:
- If the index is already materialized, the subtask is pure docs.
- If it is not, the subtask does real implementation that was not allocated its own task or effort budget.
- Either way, a junior AI hitting this bullet mid-documentation cannot tell whether to attempt an implementation or skip it without consulting outside context.

Either split this into a separate "index materialization check" task up front, or scope the doc-correction subtask strictly to documentation and track the materialize step elsewhere.

### [CONCERN] Task 12 totals effort 11 across five subtasks; borderline oversized

Assertion 1 (Effort 2), Assertion 2 (Effort 3), Assertion 3 (Effort 3), Assertion 4 (Effort 2), and the wire-gating subtask (Effort 1) all live in Task 12. Defensible as one test module with one commit and shared gating, but at 11 effort units it is the largest task in the file. Splitting into Task 12a (assertions 1+4, both symbol-detail latency), 12b (assertion 2, the headline), 12c (assertion 3, concurrency), and 12d (gating/docstring) would distribute commits and let a failure in one assertion not block the others' evidence. Borderline; flagging for visibility rather than as a blocker.

### [NOTE] Commit checkpoints are not explicitly per-task

Only Task 16's "Commit and merge" subtask mentions commits ("Semantic commits throughout"). "Throughout" implies per-task commits, which is consistent with the project's standard semantic-commits pattern, but the breakdown does not call out a commit checkpoint at the end of each prior task. No action required if "throughout" is taken at face value; flagging because "not batched at end" is easier to verify when each task names its own commit.

### [PASS] No scope-creep tasks beyond the slice design

Every task maps to a decision (D2, D6, D9, D10, D11, D12), a success criterion, or a verification walkthrough step. No task introduces work the slice design does not authorize. Task 13's conditional D11 consolidation matches D11's own conditional ("only if the concurrency numbers show the three-pool arrangement costing something real").
