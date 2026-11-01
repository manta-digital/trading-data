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
reviewedSha: dc299d51da9a1fb4f13dec03eff4b0837e41dd81
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 101654
cachedTokens: 11118
cacheWriteTokens: 90532
completionTokens: 5340
durationSeconds: 43.6
costUsd: 0.417413
costBasis: provider
runId: run-20261101-p5-3165da8f
squadronVersion: 0.24.11
findings:
  - id: F001
    severity: concern
    category: task-clarity
    summary: "Sourced eras from tasks 1.1–1.3 are never written to a committed artifact"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:44-64"
  - id: F002
    severity: concern
    category: scope
    summary: "Cutover-script unit tests go beyond the slice's stated test requirements"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:242-244"
  - id: F003
    severity: concern
    category: task-sizing
    summary: "Task 8.3 is too granular and should be merged into 8.2"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:226-228"
  - id: F004
    severity: concern
    category: scope
    summary: "Task 7.4 tests existing runner behaviour"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:208"
  - id: F005
    severity: concern
    category: process
    summary: "Task 10.1 hard-codes `git diff main` as the comparison base"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:251"
  - id: F006
    severity: note
    category: test-coverage
    summary: "Task 4.3 does not assert the `NoHoursEraError` message content"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:114-115"
  - id: F007
    severity: note
    category: risk
    summary: "Pre-2020 sourcing tasks are research-heavy and have a defined exit"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:142-166"
  - id: F008
    severity: pass
    category: coverage
    summary: "Success criteria coverage and traceability"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:42-259"
  - id: F009
    severity: pass
    category: sequencing
    summary: "Sequencing and commit distribution"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:64-259"
---

# Review: tasks — slice 301

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Sourced eras from tasks 1.1–1.3 are never written to a committed artifact

Tasks 1.1 and 1.2 end with "a working list of eras", but neither says where the list is kept. Task 1.3 writes only a Findings header and the table shape to the slice design. The task 1 commit is titled "record 301 sourced CME hours eras", yet the era values, dates and citations are not recorded anywhere that gets committed. Task 2.1 then has to enter them as tuples. If 2.1 runs in a fresh context, the sourced values are gone, and re-deriving them risks a plausible-value entry, which D2 forbids. Name a durable place for the list (for example an "Era sources" subsection of the slice design, or a notes file) and have 1.1 and 1.2 write to it.

### [CONCERN] Cutover-script unit tests go beyond the slice's stated test requirements

Task 9.2 adds unit tests for the cutover verify step and for timer-release behaviour. The slice's Technical Requirements list no test for the cutover script. Success criteria 1–8 do not cover it either. D9 asks only that the script be assembled from `cutover_common` with no new mechanism. The task has a fallback ("if 0244 has no tests for this, test the verify function directly"), which suggests this is optional. Either cut 9.2, or reduce it to a single test of the verify function and say which requirement it protects.

### [CONCERN] Task 8.3 is too granular and should be merged into 8.2

Task 8.3 only reruns existing tests after a 8.2 seed change. That is a verification step of 8.2, not independently scoped work. It is also unconditional even when 8.2 changes nothing. Fold it into 8.2 as a sub-item ("after any seed change, rerun the tests from tasks 2, 6 and 7").

### [CONCERN] Task 7.4 tests existing runner behaviour

"The runner refuses a second application" tests the migration runner, not code this slice adds. D6 notes it as a property, but neither the Technical Requirements nor the success criteria require a test for it. Drop it unless an existing runner test does not already cover non-idempotent re-application.

### [CONCERN] Task 10.1 hard-codes `git diff main` as the comparison base

The project rules say the target is whatever `cf config get git.integration_branch` returns, falling back to `main` only when it is empty. A diff against `main` can sweep in unrelated commits, or miss swept lines relative to the real fork point, when an integration branch is configured. Write "diff against the target (read from `cf config get git.integration_branch`, or `main` if empty)".

### [NOTE] Task 4.3 does not assert the `NoHoursEraError` message content

Success criterion 5 requires the error to name the calendar and the date. Task 4.3 says only "(raises)" for the range before the first era. Add an assertion that the message contains both.

### [NOTE] Pre-2020 sourcing tasks are research-heavy and have a defined exit

Tasks 6.1, 6.2, 6.5 and 6.6 each cover five years of archived CME documents. Each one defines what to do when a year has no document (move `CME_DATASET_START`, record as unresolved). That keeps a junior AI from guessing, so the split is acceptable. Note that a moved `CME_DATASET_START` reaches back into the already-committed era seed and its tests from task 2. 6.1 mentions the era seed, but 6.2, 6.5 and 6.6 only say "same method and success criteria". Their wording should be explicit about it too.

### [PASS] Success criteria coverage and traceability

- **Criterion 1** (no gaps in sessions): 7.3 continuity test.
- **Criterion 2** (2020-onward unchanged): 7.1 steps 2 and 6, and 7.4.
- **Criterion 3** (every row sourced): 2.2, 6.4, 6.8.
- **Criterion 4** (latest era equals calendar hours): 2.2, 7.1 step 4, 7.4.
- **Criterion 5** (era segmentation, `NoHoursEraError`): 4.1–4.3.
- **Criterion 6** (`locate_sessions` boundaries): 7.3.
- **Criterion 7** (NYSE/NASDAQ and 058/059 unchanged): task 3 and 4.3.
- **Criterion 8** (count check run over the full range): tasks 5 and 8.

No task lacks a source in the design.

### [PASS] Sequencing and commit distribution

There are no circular dependencies. The snapshot (task 3) comes before the extension change (task 4). The seeds (tasks 2 and 6) come before the migration (task 7). The count check runs on a migrated throwaway database in task 8 and never touches production. Tests immediately follow their implementation, and commits fall at the end of every section. No commit is batched at the end. The task file is proportionate to the slice's 3/5 effort.

### Run Digest

- Response length: 5593 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 101654 / 11118 / 90532 / 5340 / not computed
- Duration: 43.6 s
- Cost: $0.42 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
