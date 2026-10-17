---
docType: review
layer: project
reviewType: tasks
slice: eligible-rollup-swap
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: derived
sourceDocument: project-documents/user/tasks/173-tasks.eligible-rollup-swap.md
aiModel: xiaomi/mimo-v2.6-pro
aiProfile: openrouter
status: complete
dateCreated: 20261017
dateUpdated: 20261017
reviewedSha: 0352322714f6e474f7e66f4288cf8a44b9d47325
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 10
turns: 6
promptTokens: 115493
cachedTokens: 93312
completionTokens: 10189
reasoningTokens: 8397
durationSeconds: 251.8
costUsd: 0.018886
costBasis: alias_pricing
runId: run-20261017-p5-05794663
squadronVersion: 0.23.0
findings:
  - id: F001
    severity: concern
    category: task-structure
    summary: "Task 5.1 is a read-only task with no success criterion and an unmet hand-off promise"
    location: "project-documents/user/tasks/173-tasks.eligible-rollup-swap.md:239-245"
  - id: F002
    severity: note
    category: documentation
    summary: "Frontmatter `project: trading-data` diverges from its own slice design and all other task files"
    location: "project-documents/user/tasks/173-tasks.eligible-rollup-swap.md:4"
  - id: F003
    severity: note
    category: scope
    summary: "Task 7.3 extends documentation scope beyond the design's named doc set"
    location: "project-documents/user/tasks/173-tasks.eligible-rollup-swap.md:317-331"
  - id: F004
    severity: pass
    category: coverage
    summary: "Every success criterion and requirement maps to a task, with no untraceable scope creep"
    location: "project-documents/user/tasks/173-tasks.eligible-rollup-swap.md"
---

# Review: tasks — slice 173

**Verdict:** CONCERNS
**Model:** xiaomi/mimo-v2.6-pro

## Findings

### [CONCERN] Task 5.1 is a read-only task with no success criterion and an unmet hand-off promise

Task 5.1 ("Read the model and plan the steps") states "Success: none (reading); the next task names what is reused." Two problems: (1) it is the only task in the file with no completable success criterion, which the review standard requires of every task; (2) the hand-off it promises is not delivered — 5.2 never enumerates the reused `cutover_common` helpers (hold/release timers, install, migrate, `verify_migrate_target`, `restart_serve_if_active`) that 5.1 lists. Fix by merging 5.1 into 5.2 as a first step ("read these three files; reuse hold/release, install, migrate, verify, restart via `cutover_common`"), or by making 5.2's first bullet name the reused helpers so 5.1's stated success is actually satisfiable. This is the "too granular — should be merged" case; it does not block Section 5 work.

### [NOTE] Frontmatter `project: trading-data` diverges from its own slice design and all other task files

The task file declares `project: trading-data`, while `173-slice.eligible-rollup-swap.md` declares `project: trading` and every other file in `project-documents/user/tasks/` uses `project: trading`. The corpus is mixed (recent notes/analysis files use `trading-data`), so this may reflect an in-flight rename rather than an error — but the paired slice and task documents disagree today. Align the task file's frontmatter with the slice design, or confirm which value `file-naming-conventions` intends for task documents.

### [NOTE] Task 7.3 extends documentation scope beyond the design's named doc set

The design's Technical Scope item 6 and Technical Requirements name only the 140 architecture amendment, the two API references, the CHANGELOG, and runbook 100. Task 7.3 additionally updates runbooks 300 and 310 and makes its success criterion a grep across `docs` and `project-documents/user/runbooks` ("prints=all (the default)" must be gone). This is justified stale-documentation cleanup forced by the default change, and it is correctly bounded ("only where they name `_next` objects as still existing"), so it is acceptable — but it is scope beyond what the design names and is worth a line in the design if the PM wants strict traceability.

### [PASS] Every success criterion and requirement maps to a task, with no untraceable scope creep

Cross-reference verified: FC1 → 3.4 (explicit "Criterion 1" assertions for the SPY/AVGO/MSFT cases at 5m and 1h); FC2 → 2.4 item 4 (reader-gap test on `data_status` and the 4h rollup, transactional path, matching the Technical Requirement wording); FC3 → 5.2 post-swap checks (verify on all four rollups, coverage freshness, value parity, no `_next`/`_prev` remains) plus 3.3/3.4 for the default and the 422; FC4 → 5.2 swap-state detection and failure handling plus 5.3's failure-table and kill-and-rerun tests; FC5 → 8.2 re-runs recorded against the design's pass numbers, with the 5m bars load case added in Section 6 exactly as scoped ("Do not add 15m, 1h or 4h cases"). Technical Requirements: probe-first → Section 1 with results recorded in the design; migration integration tests (cold, populated, partial-rerun, no-gap reader) → 2.4 items 1–4; NULL-bucket read behavior → 3.2/3.4; health failure states and most-marked-symbol sampling → 4.1/4.3; quality bar → 8.1 and 7.2/7.3. Integration Requirements: counts unchanged → 2.4 item 5; mt-ui charts → 8.3's named Phase 7 check. The load test lives in `test/load/test_187_api_nfr.py` (verified to exist, along with `_BARS_BOUND_S` and the named 1m test), and CI gating is explicitly deferred to slice 907 per slice 187 D9 (verified), not left implicit. Sequencing is sound (probe → 064 → read path → health check → cutover → load case → docs → NFR re-runs), the release-gate/merge is correctly kept out of the task list per the Git Rules, and every section ends in a commit checkpoint.

### Run Digest

- Response length: 4975 chars
- Response is newline-free: no
- Tool calls made: 10
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 131072 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 31808
- Effort: backend default
- Turns: 6
- Tokens — prompt / cached / cache write / completion / reasoning: 115493 / 93312 / not computed / 10189 / 8397
- Duration: 251.8 s
- Cost: $0.02 (alias_pricing)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 4
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 4
- Finding-shaped matches — surviving validation: 4

### Raw Response

## Summary
[overall assessment: CONCERNS]

The task breakdown is thorough and well-sequenced: all five functional criteria, the four technical requirements, and both integration requirements of the slice design trace to concrete tasks, the probe gate correctly precedes migration 064, test tasks immediately follow their implementation tasks, and commit checkpoints are distributed one per section. The load-test addition in `tests/load/` matches the design's NFR table, and the CI-wiring deferral to slice 907 is explicit and verified against slice 187 (D9) rather than left implicit. One structural defect (task 5.1) should be fixed; two minor notes below.

## Findings

### [CONCERN] Task 5.1 is a read-only task with no success criterion and an unmet hand-off promise
category: task-structure
location: project-documents/user/tasks/173-tasks.eligible-rollup-swap.md:239-245
Task 5.1 ("Read the model and plan the steps") states "Success: none (reading); the next task names what is reused." Two problems: (1) it is the only task in the file with no completable success criterion, which the review standard requires of every task; (2) the hand-off it promises is not delivered — 5.2 never enumerates the reused `cutover_common` helpers (hold/release timers, install, migrate, `verify_migrate_target`, `restart_serve_if_active`) that 5.1 lists. Fix by merging 5.1 into 5.2 as a first step ("read these three files; reuse hold/release, install, migrate, verify, restart via `cutover_common`"), or by making 5.2's first bullet name the reused helpers so 5.1's stated success is actually satisfiable. This is the "too granular — should be merged" case; it does not block Section 5 work.

### [NOTE] Frontmatter `project: trading-data` diverges from its own slice design and all other task files
category: documentation
location: project-documents/user/tasks/173-tasks.eligible-rollup-swap.md:4
The task file declares `project: trading-data`, while `173-slice.eligible-rollup-swap.md` declares `project: trading` and every other file in `project-documents/user/tasks/` uses `project: trading`. The corpus is mixed (recent notes/analysis files use `trading-data`), so this may reflect an in-flight rename rather than an error — but the paired slice and task documents disagree today. Align the task file's frontmatter with the slice design, or confirm which value `file-naming-conventions` intends for task documents.

### [NOTE] Task 7.3 extends documentation scope beyond the design's named doc set
category: scope
location: project-documents/user/tasks/173-tasks.eligible-rollup-swap.md:317-331
The design's Technical Scope item 6 and Technical Requirements name only the 140 architecture amendment, the two API references, the CHANGELOG, and runbook 100. Task 7.3 additionally updates runbooks 300 and 310 and makes its success criterion a grep across `docs` and `project-documents/user/runbooks` ("prints=all (the default)" must be gone). This is justified stale-documentation cleanup forced by the default change, and it is correctly bounded ("only where they name `_next` objects as still existing"), so it is acceptable — but it is scope beyond what the design names and is worth a line in the design if the PM wants strict traceability.

### [PASS] Every success criterion and requirement maps to a task, with no untraceable scope creep
category: coverage
location: project-documents/user/tasks/173-tasks.eligible-rollup-swap.md
Cross-reference verified: FC1 → 3.4 (explicit "Criterion 1" assertions for the SPY/AVGO/MSFT cases at 5m and 1h); FC2 → 2.4 item 4 (reader-gap test on `data_status` and the 4h rollup, transactional path, matching the Technical Requirement wording); FC3 → 5.2 post-swap checks (verify on all four rollups, coverage freshness, value parity, no `_next`/`_prev` remains) plus 3.3/3.4 for the default and the 422; FC4 → 5.2 swap-state detection and failure handling plus 5.3's failure-table and kill-and-rerun tests; FC5 → 8.2 re-runs recorded against the design's pass numbers, with the 5m bars load case added in Section 6 exactly as scoped ("Do not add 15m, 1h or 4h cases"). Technical Requirements: probe-first → Section 1 with results recorded in the design; migration integration tests (cold, populated, partial-rerun, no-gap reader) → 2.4 items 1–4; NULL-bucket read behavior → 3.2/3.4; health failure states and most-marked-symbol sampling → 4.1/4.3; quality bar → 8.1 and 7.2/7.3. Integration Requirements: counts unchanged → 2.4 item 5; mt-ui charts → 8.3's named Phase 7 check. The load test lives in `test/load/test_187_api_nfr.py` (verified to exist, along with `_BARS_BOUND_S` and the named 1m test), and CI gating is explicitly deferred to slice 907 per slice 187 D9 (verified), not left implicit. Sequencing is sound (probe → 064 → read path → health check → cutover → load case → docs → NFR re-runs), the release-gate/merge is correctly kept out of the task list per the Git Rules, and every section ends in a commit checkpoint.
