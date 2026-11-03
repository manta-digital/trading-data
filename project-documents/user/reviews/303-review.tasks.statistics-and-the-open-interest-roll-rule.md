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
reviewedSha: 16b6f1b5e73394abf875881ff3291d1c3fa6fbb0
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 114169
cachedTokens: 11198
cacheWriteTokens: 102967
completionTokens: 7104
durationSeconds: 60.9
costUsd: 0.484807
costBasis: provider
runId: run-20261103-p5-2a8a6cb1
squadronVersion: 0.24.16
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria traceability"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md"
  - id: F002
    severity: concern
    category: sequencing
    summary: "Task 11.1 success criterion cannot be met at that point"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md:256-258"
  - id: F003
    severity: concern
    category: test-coverage
    summary: "Pre-2010 reference handling is not tested at the projector level"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md:123-126"
  - id: F004
    severity: concern
    category: scope-duplication
    summary: "Estimate-test update is assigned twice"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md:69"
  - id: F005
    severity: note
    category: handoff
    summary: "Throwaway database identity is not persisted for later tasks"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md:205-208"
  - id: F006
    severity: note
    category: coverage
    summary: "Walkthrough step 5 (live API check) has no task"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md:311-317"
  - id: F007
    severity: note
    category: proportionality
    summary: "One task file instead of the design's two"
    location: "project-documents/user/tasks/303-tasks.statistics-and-the-open-interest-roll-rule.md"
---

# Review: tasks — slice 303

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria traceability

Each criterion maps to a task:
- **Criteria 1–4:** tasks 4.1–4.5.
- **Criterion 5:** tasks 5.1–5.2, including a later-unit `DELETE`.
- **Criterion 6:** tasks 6.1–6.3.
- **Criterion 7:** tasks 6.4–6.7.
- **Criterion 8:** tasks 12.1–12.5.
- **Criterion 9:** tasks 11.1–11.4 and 13.3–13.4.
- **Criterion 10:** tasks 14.3–14.4.
- **Criterion 11:** tasks 7.1–7.2.
- **Criterion 12:** tasks 7.2, 7.3 and 15.1.

I found no scope creep. Every task traces to a D1–D13 decision or a walkthrough step. The slice restates no NFR, so no load test or CI gating task is needed. The `**Commit**:` markers sit at tested points, and the ordering is consistent: the lag rename comes before the volume basis and `n`, and the real-file fixtures come after the purchase.

### [CONCERN] Task 11.1 success criterion cannot be met at that point

Task 11.1 adds `basis_family` to `ActiveContract`. Its success line says "the field is set by every resolver". The volume resolver only chooses a basis in task 11.3, and the `n` resolver that sets `statistics` arrives in task 12.4. A junior cannot satisfy that line at 11.1 while also keeping existing roll tests passing. Reword 11.1 to say what it can deliver:
- The field exists.
- The calendar rule sets NULL.
- The volume rule sets `tick_tier` by default, so existing constructors compile.

Then move the "every resolver sets it" check to 11.4 and 12.5.

### [CONCERN] Pre-2010 reference handling is not tested at the projector level

Criterion 3 requires that a reference before `CME_DATASET_START` is stored with NULL session and counted in the unit result. Task 4.3 tests only the pure function. The projector tests in 4.5 never check the stored NULL `session_date`/`calendar_id` or the pre-2010 count in the unit result. Add one unit or integration case to 4.5 for that path.

### [CONCERN] Estimate-test update is assigned twice

Task 1.5 updates `test_estimate.py` for `ESTIMATE_SCHEMAS` membership ("update them if estimate tests enumerate rows"). Task 7.3 then updates `test_estimate.py` again for the same row. Keep it in one place. I suggest 7.3, since task 1.2 only adds the membership and 7.3 confirms the printed row. Delete the estimate item from 1.5.

### [NOTE] Throwaway database identity is not persisted for later tasks

Tasks 14.2 and 15.1 (and 15.3, which drops the databases) reuse the databases created in 8.1. Nothing records their names, the scratch archive path or the wrapper's location where a resumed session would find them. Task 8.3's note records only job ids and the archive path. Add the database names, wrapper path and env variable names (no secrets) to the evaluation note in 8.3.

### [NOTE] Walkthrough step 5 (live API check) has no task

The slice's `mt serve` and `curl` check of `rule=n.0` and the `422` on `n.0+2s` is not tasked. Tasks 13.3 and 13.4 cover the same behaviour with route tests on seeded data, so no criterion is unmet. Optionally add a one-line manual check to 13.4.

### [NOTE] One task file instead of the design's two

The slice's Development Approach says two task files fit (the tier, then the rule). This is a single file with 15 top-level tasks and 12 commit markers. It stays coherent and each task is junior-sized, so I raise no objection. The tier and rule halves split cleanly at task 10 if the length becomes a problem.

### Run Digest

- Response length: 4484 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 114169 / 11198 / 102967 / 7104 / not computed
- Duration: 60.9 s
- Cost: $0.48 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
