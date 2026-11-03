---
docType: review
layer: project
reviewType: tasks
slice: statistics-and-the-open-interest-roll-rule
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261103
dateUpdated: 20261103
reviewedSha: 068525c6436ab8a4d9c97c88db8f9583423a893a
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 113910
cachedTokens: 8831
cacheWriteTokens: 105075
completionTokens: 5210
durationSeconds: 45.1
costUsd: 0.474062
costBasis: provider
runId: run-20261103-p5-2a8a6cb1
squadronVersion: 0.24.16
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria coverage"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md"
  - id: F002
    severity: concern
    category: sequencing
    summary: "Task 1.1 cites a constant that is only renamed in task 10"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md:50"
  - id: F003
    severity: concern
    category: scope-clarity
    summary: "Ownership of `HISTORY_SCHEMAS` membership and of the both-families \"complete\" status is ambiguous"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md:55"
  - id: F004
    severity: note
    category: commit-markers
    summary: "Task 8's commit may carry code changes under a `docs:` message"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md:223"
  - id: F005
    severity: note
    category: proportionality
    summary: "Some tasks are small enough to merge"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md:259"
  - id: F006
    severity: note
    category: testing
    summary: "Recorder changes have no explicit test step"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md:326"
---

# Review: tasks — slice 303

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria coverage

Each criterion maps to tasks:
- **1–4 (ingest, session assignment, checks):** tasks 3–4.
- **5 (revision resolution):** task 5.
- **6–7 (status, coverage, exit 3):** tasks 6.1–6.7.
- **8 (`n` resolution):** task 12.
- **9 (volume basis, segment `basis`):** tasks 11 and 13.3–13.4.
- **10 (equivalence):** task 14.
- **11 (history-want validation):** 7.1–7.2.
- **12 (production plan unchanged, estimate row):** 7.2, 7.3 and 15.1.

Also covered:
- Drill and role tests: 7.4.
- `SOURCES.md` entries: 3.1 and 9.1.
- OpenAPI regeneration: 13.5.
- The D11 evaluation note: 8.3, 8.4, 14.4 and 15.3.

The slice states no NFR, so no load-test or CI-gating task is required.

### [CONCERN] Task 1.1 cites a constant that is only renamed in task 10

Task 1.1 asks for a comment citing `RANK_LAG_SESSIONS`'s note. That name does not exist until task 10.1 renames `VOLUME_RANK_LAG_SESSIONS`, and the task 10.1 grep for the old name will not catch the comment. The rename touches nothing from tasks 1–9, so it can move before task 1, or the comment in 1.1 can name the current constant. One more option is to have 10.1 also update that comment.

### [CONCERN] Ownership of `HISTORY_SCHEMAS` membership and of the both-families "complete" status is ambiguous

Two overlaps could lead to duplicated work or logic:
- **`HISTORY_SCHEMAS`.** Task 1.2 adds `statistics` to `HISTORY_SCHEMAS`. Task 7.1 then says `HISTORY_SCHEMAS` "now carries `statistics`", as if it were new there. State that 1.2 owns the membership and 7.1 owns validation and planner behaviour only. Also say whether the intermediate commits (tasks 1–6) can accept a statistics want before 7.1's validation exists.
- **Per-session status.** Task 6.4 needs sessions "load-complete in both families" (line 157). Task 11.3 separately says "Add the per-session status read for both families" (line 260). Name one implementation, built in 6.4 or reused from 302's plumbing, and have 11.3 consume it. Otherwise the project's no-duplicated-logic rule is at risk.

### [NOTE] Task 8's commit may carry code changes under a `docs:` message

Tasks 8.2, 8.4 and 8.5 allow projector, design or `REQUIRED_STAT_TYPES` fixes, and they add `scripts/measure_303_statistics.py`. The marker `docs: record statistics purchase and measurements` would then mislabel the commit. Say the commit type follows the content (`fix:`, `feat:` or `docs:`). The marker is otherwise at a sensible tested point.

### [NOTE] Some tasks are small enough to merge

The file has 15 top-level tasks with about 60 subtasks. That is heavy but consistent with the slice's 4/5 effort and its own plan of two task files. Micro-tasks that could fold into neighbours:
- 12.1/12.2 (an enum member plus a parse test) into 12.3.
- 4.1 (one enum value and reason) into 4.2.
- 10.1 into 11.1, if the rename is not moved earlier.

Several steps restate design SQL and structure nearly verbatim, for example 2.1 and 4.4. This is acceptable, but keep them from growing further.

### [NOTE] Recorder changes have no explicit test step

Task 14.1 changes `record_229_roll_fixture.py` and `roll_fixture.py` and says only that "unit tests for it pass". Confirm that an existing test loads the new `{product}_open_interest.json`. If none does, the 14.3 equivalence test is the first consumer. That is acceptable, but the dependency should be stated.

### Run Digest

- Response length: 4438 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 113910 / 8831 / 105075 / 5210 / not computed
- Duration: 45.1 s
- Cost: $0.47 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
