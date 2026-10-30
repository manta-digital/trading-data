---
docType: review
layer: project
reviewType: tasks
slice: archive-generalisation
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/300-tasks.archive-generalisation.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261030
dateUpdated: 20261030
reviewedSha: 59eaa8100f88f8e196410cf163da02649d325046
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 143553
cachedTokens: 59813
cacheWriteTokens: 83736
completionTokens: 3229
durationSeconds: 28.3
costUsd: 0.373971
costBasis: provider
runId: run-20261030-p5-dbc05e02
squadronVersion: 0.24.9
findings:
  - id: F001
    severity: concern
    category: sequencing
    summary: "Task 5.3 needs a value that is only produced in task 6.4"
    location: "project-documents/user/tasks/300-tasks.archive-generalisation.md:166-170"
  - id: F002
    severity: concern
    category: coverage
    summary: "Load tick tests named by the design are not run in the validation group"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md:290"
  - id: F003
    severity: note
    category: test-coverage
    summary: "Unknown-plan refusal at the preflight is only checked manually"
    location: "project-documents/user/tasks/300-tasks.archive-generalisation.md:141"
  - id: F004
    severity: note
    category: test-coverage
    summary: "Standard-plan unheld-job classification is tested only at the `evaluate_spend` level"
    location: "project-documents/user/tasks/300-tasks.archive-generalisation.md:199-203"
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Success criteria 1-8 trace to tasks"
    location: "project-documents/user/tasks/300-tasks.archive-generalisation.md"
  - id: F006
    severity: pass
    category: uncategorized
    summary: "Scope, ordering and commits are proportionate"
    location: "project-documents/user/tasks/300-tasks.archive-generalisation.md"
---

# Review: tasks — slice 300

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Task 5.3 needs a value that is only produced in task 6.4

- Task 5.3 makes `insert_pending_request` take `is_plan_included` as a required argument, "from the plan item". It also says the guard and the row must use the same value.
- The plan item only carries that value after 6.4, when `purchase_plan` computes it and keeps it on the item.
- So at the 5.5 commit, `manifest_pass` has no value to pass. An implementer would have to compute inclusion in `manifest_pass`, which creates a second call site. Task 6.4 forbids that ("no second call site computes inclusion"). Or they would pass a placeholder `False`, which breaks criterion 4 for in-plan items until group 6.
- Likewise, 6.1 makes `PlannedCost.included` required, and `purchase_plan`'s constructor call must pass something until 6.4.
- Fix: add the `is_plan_included` field to the plan item and have `purchase_plan` set it in group 5, ahead of 5.3. Or move the `manifest_pass` writer change into group 6 after 6.4. Keep the `adopt` writer in group 5, since it classifies independently.

### [CONCERN] Load tick tests named by the design are not run in the validation group

- The design's "Behaviour verification" says the unit, integration and load tick tests must pass unchanged.
- Tasks 1.1 and 8.2 run only `test/unit/data/tick` and the `-k tick` integration tests.
- If tick load tests exist, a regression in the moved ingest code could pass the gate unnoticed.
- Fix: add the tick load test invocation to 1.1 (baseline) and 8.2, or state that none exist. The slice sets no new NFR, so no new load test or CI gating task is needed.

### [NOTE] Unknown-plan refusal at the preflight is only checked manually

- Criterion 8 requires the preflight to refuse before any provider call.
- Task 4.2 says to confirm this, and 8.6 runs the command by hand. The automated tests (4.4) cover settings validation only.
- This is probably enough for a free metadata check, but a small test that the preflight surfaces the settings error would make the criterion durable.

### [NOTE] Standard-plan unheld-job classification is tested only at the `evaluate_spend` level

- Task 6.5 covers unheld jobs only for the no-plan case.
- With plan `standard`, an unheld in-window job is covered by the 6.2 unit tests on `evaluate_spend`.
- `purchase_plan`'s own classification of an unheld job with `standard` set is not asserted. Add one case to 6.5 for criterion 6.

### [PASS] Success criteria 1-8 trace to tasks

- Criterion 1 is covered by 8.4-8.5.
- Criterion 2 is covered by 2.6.
- Criterion 3 is covered by 3.3-3.5.
- Criterion 4 is covered by 5.3-5.4.
- Criteria 5 and 6 are covered by 6.1-6.5.
- Criterion 7 is covered by 7.1-7.2.
- Criterion 8 is covered by 4.2, 4.4 and 8.6.
- The technical requirements are also covered: the total and disjoint family table, the registry keys equal to `STORED_TIERS`, the 008 migration tests, the grep audit and the file-size check.
- No task falls outside the design. The extra-family test, the parity run and the walkthrough recording are all design-mandated.

### [PASS] Scope, ordering and commits are proportionate

- The task file is a single file with eight groups, which fits an effort of 4/5.
- Tests follow their implementation within each group, and a commit closes every group. No merge or push tasks appear.
- Task 3.3 is the largest, but it is one cohesive rewiring step and does not need splitting.

### Run Digest

- Response length: 4289 chars
- Response is newline-free: no
- Tool calls made: 2
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 3
- Tokens — prompt / cached / cache write / completion / reasoning: 143553 / 59813 / 83736 / 3229 / not computed
- Duration: 28.3 s
- Cost: $0.37 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
