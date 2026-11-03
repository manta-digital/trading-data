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
reviewedSha: 734a3f34196f35bf088558282c05eb93064df934
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 114155
cachedTokens: 11198
cacheWriteTokens: 102953
completionTokens: 5402
durationSeconds: 46.7
costUsd: 0.46773
costBasis: provider
runId: run-20261103-p5-2a8a6cb1
squadronVersion: 0.24.16
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria 1–12 each trace to tasks"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md"
  - id: F002
    severity: concern
    category: task-sizing
    summary: "Task 6 bundles three separable units under a single commit"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md:142-175"
  - id: F003
    severity: concern
    category: coverage-gap
    summary: "No task runs `coverage` or `status` on the purchased GC/ES window"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md:220-222"
  - id: F004
    severity: note
    category: clarity
    summary: "11.1 success wording suggests a default value"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md:254"
  - id: F005
    severity: note
    category: proportionality
    summary: "Recorder serialisation unit test (14.1) goes slightly beyond the stated criteria"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md:325"
  - id: F006
    severity: note
    category: proportionality
    summary: "One task file with 15 top-level tasks, where the design suggested two"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md"
  - id: F007
    severity: pass
    category: sequencing
    summary: "Sequencing and commit markers"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md"
---

# Review: tasks — slice 303

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria 1–12 each trace to tasks

Every functional criterion has an implementing task and a test task:
- Criteria 1–4 are covered by 4.1–4.5.
- Criterion 5 is covered by 5.1–5.2.
- Criteria 6–7 are covered by 6.1–6.7.
- Criterion 8 is covered by 12.1–12.5.
- Criterion 9 is covered by 11.x and 13.3–13.4. The "every answer unchanged" requirement is tested in 11.4.
- Criterion 10 is covered by 14.3.
- Criterion 11 is covered by 7.1–7.2.
- Criterion 12 is covered by 7.2, 7.3 and 15.1.
- D12 (backup and roles) is covered by 7.4.
- D11's evaluation items are covered by 8.3, 8.4, 14.4 and 15.3.

Tasks 10 and 12.1 trace to D9. No task lacks a design anchor.

### [CONCERN] Task 6 bundles three separable units under a single commit

Task 6 has seven subtasks and covers three pieces of work:
- the status verdict and content findings (6.1–6.3);
- the cross-tier volume check (6.4–6.5);
- the coverage command (6.6–6.7).

All of it ends at one `**Commit**:` marker, which makes it the largest working unit in the file. The status piece (6.3) already ends at a working, tested point. Add a marker there, such as `feat: add statistics session status and content findings`. Keep the existing marker after 6.7. This gives the junior implementer a smaller rollback point without adding tasks.

### [CONCERN] No task runs `coverage` or `status` on the purchased GC/ES window

Walkthrough step 3 and criteria 6–7 expect these on the real window:
- `status --product GC` showing complete sessions;
- `coverage --schema statistics` over 2024-11-04 to 2025-01-01 with counts equal, verdicts `agree` or `under`, and exit 0.

Task 8.5 runs `status` only. Task 8.4 computes V/C with a throwaway script, not through the `coverage` command. Task 9.2 exercises coverage only on the two ES fixture files. The shipped coverage path therefore never runs against the full purchased window, and a defect in it, such as an unexpected exit 3, would surface after merge. Add a `coverage --schema statistics --json` run for GC and ES to 8.5 and record its exit code and any `over` or `no_trades` in the note.

### [NOTE] 11.1 success wording suggests a default value

"…with the new field defaulting only where the rule is calendar" reads like a field default. The design says `basis_family` is NULL for `c`, and 11.2 tests that calendar answers carry NULL. Reword to "calendar answers set it to NULL explicitly; the other rules always set it", so the implementer does not add a silent default that could mask a missing assignment.

### [NOTE] Recorder serialisation unit test (14.1) goes slightly beyond the stated criteria

The slice requires the recorder to write the new files and the equivalence test to load them. The synthetic-input unit test for the recorder is an extra layer. 14.3 already fails if `roll_fixture.py` cannot load the recorded files. Keeping it is defensible, but it could be reduced to a loader-only assertion if the task needs trimming.

### [NOTE] One task file with 15 top-level tasks, where the design suggested two

The design's Development Approach says "Two task files fit the slice: the tier, then the rule." This is a single 357-line file. It is cohesive, and the Context Summary makes it self-contained. The natural split would be tasks 1–9 (the tier and purchase) and 10–15 (the rule and validation). Single-file is acceptable, but if the implementer's context becomes a problem, that is where to cut. Task 10 (a rename-only commit) could also be merged into 11 if fewer commits are preferred.

### [PASS] Sequencing and commit markers

Dependencies run forward only:
- Constants lead to the migration, then the projector, the resolved read, and status/volume/coverage.
- The purchase (8) comes after everything it needs.
- Fixtures (9) and the recorder (14) depend on the purchase.
- The `n` rule (12) needs the statistics status (6.1), the resolved read (5.1) and the lag rename (10).
- Task 11.3 reuses 6.4's status combination explicitly.

Commit markers sit at tested boundaries, none are checkboxes, and they are neither per-task nor only at the end.

There are no NFR statements in the slice, so no load-test or CI-wiring task is needed. The resolved-read timing is a recorded measurement (8.4), which matches the design's choice to defer full-scale timing to 307.

### Run Digest

- Response length: 5531 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 114155 / 11198 / 102953 / 5402 / not computed
- Duration: 46.7 s
- Cost: $0.47 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
