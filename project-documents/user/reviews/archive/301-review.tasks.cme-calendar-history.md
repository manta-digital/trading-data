---
docType: review
layer: project
reviewType: tasks
slice: cme-calendar-history
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/301-tasks.cme-calendar-history.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261101
dateUpdated: 20261101
reviewedSha: c4e68516f0c0c98eb3f62e5754e5ee33be7b7c6b
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 101513
cachedTokens: 11118
cacheWriteTokens: 90391
completionTokens: 5337
durationSeconds: 44.8
costUsd: 0.416819
costBasis: provider
runId: run-20261101-p5-3165da8f
squadronVersion: 0.24.11
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria coverage and sequencing"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:42-268"
  - id: F002
    severity: concern
    category: completeness
    summary: "Unsourced exception years have no resolution path"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:154-181"
  - id: F003
    severity: concern
    category: sequencing
    summary: "The \"reopen D1\" branch in 1.4 is not carried into downstream tasks"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:61-66"
  - id: F004
    severity: concern
    category: proportionality
    summary: "Test tasks beyond what the slice requires"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:142-144"
  - id: F005
    severity: concern
    category: task-sizing
    summary: "Task 7.4 is oversized"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:206-216"
  - id: F006
    severity: concern
    category: task-sizing
    summary: "Read-only subtasks are too granular"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:44-47"
  - id: F007
    severity: concern
    category: completeness
    summary: "Count-check runs do not say which database to use or how the script connects"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:134-148"
  - id: F008
    severity: note
    category: conventions
    summary: "Commit type for task 8"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:236"
  - id: F009
    severity: note
    category: sequencing
    summary: "Test-with pattern in task 6"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:177-181"
---

# Review: tasks — slice 301

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria coverage and sequencing

- **Criteria 1 and 2** are covered by 7.2 and 7.4 (continuity, and the 2020-onward comparison inside the migration and in a test).
- **Criteria 3 and 4** are covered by 2.2 and 6.7.
- **Criterion 5** is covered by 4.4.
- **Criterion 6** is covered by the boundary-instant bullet in 7.4.
- **Criterion 7** is covered by 3.2 and 4.4.
- **Criterion 8** is covered by 8.1 and 8.2.
- **Ordering:** the 058/059 snapshot (task 3) is taken before `session_extension.py` is touched (task 4). The migration (7) follows the seeds (2, 6) and the extension code (4). The count run (8) follows the migration. No circular dependencies.
- **Commit markers:** there is one per top-level task, so none are batched at the end.
- **Load testing:** the slice states no NFR, so no load test or CI gating task is needed.

### [CONCERN] Unsourced exception years have no resolution path

- Tasks 6.1, 6.2, 6.4 and 6.5 say "any year with no document is written down as unsourced", and then nothing happens to it.
- Only unsourced eras are tied to moving `CME_DATASET_START` (1.4, per D5). The slice's special considerations say to stop when sources for an era or a day cannot be found.
- If a year's exceptions cannot be sourced, its holidays would be generated as full trading sessions, and that would break success criterion 1 and the `[CME_DATASET_START, 2020-01-01)` test in 6.7.
- Add a step (for example in 6.3 and 6.6) that decides what to do with an unsourced year: move `CME_DATASET_START` past it, or record it as unresolved. It should also write the Findings entry.

### [CONCERN] The "reopen D1" branch in 1.4 is not carried into downstream tasks

- If a Sunday open differs from the daily reopen, 1.4 adds a column and stops coding. But 2.1 (era dataclass), 4.3 (segmentation) and 7.2 (table creation) all assume the D1 columns.
- `populate_trading_sessions` takes one fixed open and is declared unchanged, so a Sunday-open era would need a design change.
- The task file should say that this outcome halts the breakdown and requires it to be revised. A revised design should not just be patched in mid-flight.

### [CONCERN] Test tasks beyond what the slice requires

- Slice D8 calls `check_cme_history_counts.py` a throwaway script, "deliberately not polished". Its Technical Requirements list no unit tests for it.
- Task 5.3 adds a test file for it anyway. Task 5.2 also demands logic separated from the network call, purely to make it testable. Cut 5.3, or shrink it to a minimal classification test.
- Task 9.3 likewise adds tests for the cutover script, which no slice requirement asks for. Keep it only if the 0244 fakes make it nearly free. Otherwise rely on the cutover run and its verify step.

### [CONCERN] Task 7.4 is oversized

- 7.4 packs about nine distinct integration tests into one subtask. They are continuity, fresh chain, populated database, two precondition aborts, the 2020 comparison abort, the latest-era abort, the second-application refusal, forward-extension equality and boundary instants.
- A junior implementer cannot check this off as one unit. Split it, for example into migration behaviour and abort paths (7.4) and boundary and continuity tests (7.5).
- "The runner refuses a second application" tests existing runner behaviour, not new code. Consider dropping it.

### [CONCERN] Read-only subtasks are too granular

- 1.1, 4.1, 5.1, 7.1 and 9.1 only read code or the SDK and produce no artifact.
- Fold each into the first implementing subtask of its task, as a "first, read X" step. That removes five checkboxes without losing anything.

### [CONCERN] Count-check runs do not say which database to use or how the script connects

- 5.2 reads sessions from "the calendar" but never says which database or connection it uses.
- 5.4 needs a database holding 2020-onward CME sessions. 8.1 needs a "migrated test database" but does not say how to get one.
- Say how the script gets its DB URL, and name the concrete setup for 5.4 and 8.1 (for example, a fresh test database with the full chain applied). Otherwise the junior implementer may reach for the production URL variable, which CLAUDE.md forbids tests from reading.

### [NOTE] Commit type for task 8

- `fix:` is reserved for fixes outside the slice workflow. Task 8 adds sourced rows and findings as part of the slice. `feat:` or `docs:` fits better.

### [NOTE] Test-with pattern in task 6

- The single test task 6.7 covers both history seed modules and comes after both. It does not follow the equity module (6.3) immediately.
- This is acceptable because the test is shared. The test file could be created alongside 6.3 and extended in 6.6.

### Run Digest

- Response length: 5982 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 101513 / 11118 / 90391 / 5337 / not computed
- Duration: 44.8 s
- Cost: $0.42 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
