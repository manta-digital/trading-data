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
reviewedSha: 16542d8352cce0a954fceacc3090408f0ba30aa2
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 3
turns: 4
promptTokens: 190859
cachedTokens: 96481
cacheWriteTokens: 94372
completionTokens: 3265
durationSeconds: 37.4
costUsd: 0.420545
costBasis: provider
runId: run-20261030-p5-dbc05e02
squadronVersion: 0.24.9
findings:
  - id: F001
    severity: concern
    category: test-coverage
    summary: "Criterion 3 `not_selectable` behaviour has no test task"
    location: "project-documents/user/tasks/300-tasks.archive-generalisation.md:116-125"
  - id: F002
    severity: pass
    category: coverage
    summary: "All success criteria trace to tasks"
    location: "project-documents/user/tasks/300-tasks.archive-generalisation.md:57-260"
  - id: F003
    severity: pass
    category: sequencing
    summary: "Sequencing, test-with pattern and commit distribution"
    location: "project-documents/user/tasks/300-tasks.archive-generalisation.md:95-228"
  - id: F004
    severity: pass
    category: proportionality
    summary: "Proportionality and load-test/CI items"
    location: "project-documents/user/tasks/300-tasks.archive-generalisation.md:43-56"
  - id: F005
    severity: note
    category: process
    summary: "Branch creation is not a task"
    location: "project-documents/user/tasks/300-tasks.archive-generalisation.md:52"
---

# Review: tasks — slice 300

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Criterion 3 `not_selectable` behaviour has no test task

Task 3.4 changes the not-selectable message so it names the schema and says there is no projector. Its Success line reads "criterion 3 holds", but nothing tests it. Task 3.5 lists the registry-keys test, the import updates and two worker tests. None of them checks that `--unit-id` of a `definition` unit is reported `not_selectable` with its schema named. The only existing `not_selectable` tests in `test/` assert the "no such unit" reason (`test/integration/data/test_tick_ingest_cli.py:81`, `test/unit/cli/commands/test_data_tick_ingest.py`). None covers a schema with no projector. Add one `select_units` test to 3.5 for a `definition` unit selected by id.

### [PASS] All success criteria trace to tasks

- **Criterion 1:** parity in 8.4/8.5, with the marker check in 8.5.
- **Criterion 2:** 2.6.
- **Criterion 4:** 5.3/5.4.
- **Criterion 5:** 6.4/6.5.
- **Criterion 6:** 6.2/6.3/6.5.
- **Criterion 7:** 7.1/7.2.
- **Criterion 8:** 4.2/4.4, with the walkthrough in 8.6.

The unit and integration tests the design lists are all present. The totality tests are in 2.3 and 4.4, and the registry-keys test is in 3.5. The `tick_008` tests are in 5.2. The migration plan is covered by 5.1. No task falls outside the design, so there is no scope creep. The render change in 6.6 and the estimate column in 7.1 are both named in the design.

### [PASS] Sequencing, test-with pattern and commit distribution

- **Dependencies:** each group depends only on earlier work. The baseline hash is recorded before any code change, and 8.4 builds from it.
- **Request-writer handoff:** 5.3 computes the marker at the submit call site, and 6.4 moves that call onto the plan item. The task text says so explicitly.
- **Required-field changes:** 6.1 makes `included` required with production sites passing `False`, so the tree stays consistent between tasks.
- **Tests:** each implementation group is followed by its tests.
- **Commits:** there is a commit at the end of groups 2 through 7, then a checkpoint in 8.3 before the parity runs.
- **No merge or push task:** none is written, which matches the git rules.

### [PASS] Proportionality and load-test/CI items

For effort 4/5, one task file with eight groups is proportionate. The extra-family test in 2.3 is the design's stated mitigation. The preflight test in 4.4 is required by criterion 8. The slice restates no NFR, so no `tests/load/` task or CI wiring task is needed. Running the existing load-tier tick tests once in 8.2 matches the design's "pass unchanged" requirement.

### [NOTE] Branch creation is not a task

The context summary says work happens on `300-slice.archive-generalisation`, but no task creates or checks out that branch. Task 1.1 commits `docs: record 300 baseline` and records the base hash. The implementer needs to create the branch from the target before 1.1's commit, so the recorded hash is the fork point. The git rules already say this, so no task change is required.

### Run Digest

- Response length: 3713 chars
- Response is newline-free: no
- Tool calls made: 3
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 4
- Tokens — prompt / cached / cache write / completion / reasoning: 190859 / 96481 / 94372 / 3265 / not computed
- Duration: 37.4 s
- Cost: $0.42 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 5
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 5
- Finding-shaped matches — surviving validation: 5
