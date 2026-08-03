---
docType: review
layer: project
reviewType: tasks
slice: staleness-surface-for-api-clients
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/tasks/185-tasks.staleness-surface-for-api-clients.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260803
dateUpdated: 20260803
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "All seven success criteria are covered by traceable tasks"
    location: 185-tasks.staleness-surface-for-api-clients.md
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Tasks 2/3 and 4/5 follow a tight test-with pattern"
    location: 185-tasks.staleness-surface-for-api-clients.md:tasks-2-5
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Commit checkpoints are distributed across surfaces, not batched"
    location: 185-tasks.staleness-surface-for-api-clients.md:tasks-6,9,12
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Slice ownership boundary with 186 is preserved"
    location: 185-tasks.staleness-surface-for-api-clients.md:notes
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Tasks are appropriately scoped for a junior AI with clear success criteria"
    location: 185-tasks.staleness-surface-for-api-clients.md:tasks-1-15
  - id: F006
    severity: pass
    category: uncategorized
    summary: "D7 NFR budget is operationalized without a missing load-test task"
    location: 185-tasks.staleness-surface-for-api-clients.md:task-14
  - id: F007
    severity: pass
    category: uncategorized
    summary: "Sequencing respects dependencies with no cycles"
    location: 185-tasks.staleness-surface-for-api-clients.md:tasks-1-15
  - id: F008
    severity: note
    category: uncategorized
    summary: "`from_dataframe` call-site update is handled implicitly rather than as a subtask"
    location: 185-tasks.staleness-surface-for-api-clients.md:task-10
  - id: F009
    severity: note
    category: uncategorized
    summary: "Live verification step depends on a disposable DB the PM must confirm"
    location: 185-tasks.staleness-surface-for-api-clients.md:task-14
---

# Review: tasks — slice 185

**Verdict:** PASS
**Model:** minimax/minimax-m3

## Findings

### [PASS] All seven success criteria are covered by traceable tasks

Cross-referenced each functional success criterion (1–7) and the two technical requirements against tasks 2–14. Every criterion has at least one task whose success conditions explicitly assert it, and no task asserts something that traces to nothing in the slice design.

### [PASS] Tasks 2/3 and 4/5 follow a tight test-with pattern

Model implementation in Task 2 is immediately followed by model tests in Task 3; route implementation in Task 4 is immediately followed by route tests in Task 5. The same shape repeats for health (7/8) and bars (10/11). No test tasks are deferred past a commit checkpoint.

### [PASS] Commit checkpoints are distributed across surfaces, not batched

Three checkpoints (Tasks 6, 9, 12) bracket the three independent surfaces (status endpoint, health coverage, bars staleness), each producing a revertable commit. Task 15 is the wrap-up, not a deferred batch.

### [PASS] Slice ownership boundary with 186 is preserved

Task 15 explicitly notes that 186 must diff against 185's landed `bars.py`/`responses.py`. The Context Summary's "Ownership boundary" section and Task 4's "Do not call `maybe_extend_trading_sessions`" + "Do not embed gaps" guard rails correctly exclude the 186-scoped work (range caps, pagination, `openapi.json` version, pool tuning) per D10.

### [PASS] Tasks are appropriately scoped for a junior AI with clear success criteria

Effort estimates are low (mostly 1–3, with one 2 and a few 3s). Each subtask has a concrete success condition (a command to run, an assertion to make, or a file/line-budget to hit). The two largest tasks (Task 4 at effort 3+3+1=7 and Task 14 at 2+3=5) are decomposed into named subtasks with distinct success conditions, which is the right level of granularity for a wrapper route that has both contract details (params, 422) and a body to implement, and for a verification walkthrough with a separately gated induced-staleness substep.

### [PASS] D7 NFR budget is operationalized without a missing load-test task

The D7 budget ("near-zero on cache hit; ≤ +2.5s on cache miss") is restated as an explicit measurement obligation in Task 14 with a defined escalation path (report to PM as new information if miss cost exceeds the budget), rather than silently dropped. The slice design itself rates the underlying risk Low and explicitly defers a load-test assertion to the task breakdown as optional, not mandatory, so the absence of a `tests/load/` task is consistent with the parent design — no NFR was restated at the task-breakdown level that would require a gated load test. No CI-wiring gap exists because no load test exists.

### [PASS] Sequencing respects dependencies with no cycles

Branch setup (1) → models (2) → model tests (3) → route + register (4) → route tests (5) → checkpoint (6) → health model + route (7) → health tests (8) → checkpoint (9) → bars model + db dep + probe (10) → bars tests (11) → checkpoint (12) → CLI regression check (13) → live walkthrough (14) → wrap-up (15). No task depends on a later task; the bars surface correctly comes last because it touches the most existing tests, matching the design's suggested order.

### [NOTE] `from_dataframe` call-site update is handled implicitly rather than as a subtask

Task 10 says "`pyright` flags every existing `from_dataframe` call site that needs updating — fix each rather than defaulting the parameter." This is an acceptable approach (the type checker is the discovery mechanism), but the implementer should be aware that the scope of "fix each" is unbounded until `pyright` is run. If the repo has many call sites, this could swell Task 10 beyond its effort 2 estimate. Not a defect — the discoverability is via a tool, not a guess — but worth flagging so the implementer doesn't merge it with Task 11.

### [NOTE] Live verification step depends on a disposable DB the PM must confirm

The induced-staleness step (D7 walkthrough step 7) is correctly gated on PM confirmation of a disposable DB, and the task explicitly directs the implementer to record the step as not run rather than substituting `trading_test`. This is the right call given the design's constraint, but it means success criterion 7's "all three surfaces report stale" assertion is contingent on environment availability, not just code correctness. The task documents this honestly rather than hiding it.
