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
reviewedSha: c183ceabc211a655b4ab0388fbf94d1d923f1c7e
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 3
turns: 4
promptTokens: 180776
cachedTokens: 90753
cacheWriteTokens: 90017
completionTokens: 5350
durationSeconds: 47.2
costUsd: 0.423402
costBasis: provider
runId: run-20261101-p5-3ef67db9
squadronVersion: 0.24.10
findings:
  - id: F001
    severity: concern
    category: sequencing
    summary: "Task 2.4 integration test breaks once Section 4 lands"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:231-240"
  - id: F002
    severity: concern
    category: clarity
    summary: "Migration id hard-coded in names while the Working Rules say it will change"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:93-96"
  - id: F003
    severity: concern
    category: process
    summary: "Commit cadence is per section, not per task"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:66"
  - id: F004
    severity: concern
    category: proportionality
    summary: "Test tasks are heavier than the criteria need"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:231-240"
  - id: F005
    severity: note
    category: coverage
    summary: "Criteria 3 and 4 are checked more narrowly than written"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:170-181"
  - id: F006
    severity: note
    category: scope
    summary: "Pre-2020 exceptions live in new modules, not the existing seed modules"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md:119"
  - id: F007
    severity: pass
    category: coverage
    summary: "Success criteria coverage and dependency order"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:101-521"
---

# Review: tasks — slice 301

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Task 2.4 integration test breaks once Section 4 lands

Task 2.4 hand-creates `trading_calendar_hours` in the throwaway database and tests the "table absent" case. It is committed at 2.5, before migration 065 exists. After 4.1 the shared fixtures migrate through the new migration, so the table is already there. The hand-made `CREATE TABLE` then collides. The "table absent" case can no longer be reached either. The test would pass at 2.5 and fail at 4.6, and nothing in the file says how to build the pre-065 state. Name the mechanism. Either build the database with the chain stopped at the pre-065 tip, as 4.3 does for its "through 064" comparison. Or have the test drop the table, which is allowed because the database is throwaway. Alternatively, move the absent-table case into 4.3.

### [CONCERN] Migration id hard-coded in names while the Working Rules say it will change

`src/manta_trading/market/schema/migrations/minute.py:2777` shows `065_instruments_name` already registered. The Working Rules therefore say to use the next number and not to trust the `065` written in the file. But `065` is baked into the migration id (4.1), the `test_migration_065_cme_calendar_history.py` filename (4.3), the `-k migration_065` filter (4.6), the commit message (4.6) and the Section 3 prose. A junior following those names literally collides with #60's id. Define the id once, for example as a note at the top: "N = next id after the current tip, read at 4.1". Then refer to `{N}` everywhere else. This matches the project rule against scattered values.

### [CONCERN] Commit cadence is per section, not per task

The project rule is "Git add and commit from project root at least once per task". Working Rules say "Commit at each section's checkpoint". Section 3 holds eight tasks, including four archive-research tasks (3.2, 3.3, 3.5, 3.6), and commits only at 3.9. Section 1 has two such tasks (1.3, 1.4) and commits at 1.7. The data entry is slow, sourced work, so batching it risks losing it and makes the diff hard to review. Either commit after each research task, or state that the section checkpoints replace the per-task rule. The latter would need Project Manager sign-off, because it overrides a CLAUDE.md rule.

### [CONCERN] Test tasks are heavier than the criteria need

The same behaviour is tested up to three times. Hours on each side of an era change are checked in 2.2 (unit), 2.4 (integration) and 4.3 (migrated database). Task 2.4 exists mainly to cover the absent-table guard from 2.3, which the slice does not require, and 4.3 already states that it covers hours on both sides of a change. Task 4.4 is a separate task but is a case list on the same migrated database as 4.3. Task 3.7 is a one-line parametrisation (effort 1) that could be folded into 3.4. Suggested cuts:
- Fold 2.4's three cases into a single case on the pre-migration database, or drop it in favour of 4.3.
- Merge 4.4 into 4.3.
- Merge 3.7 into 3.4 by parametrising by calendar from the start.

### [NOTE] Criteria 3 and 4 are checked more narrowly than written

Criterion 3 says every era and exception names "a source document and its date". Task 1.6 only checks that the source is non-empty and contains `http`. The `# source:` comment test for exceptions has a similar gap. Criterion 4 says the latest era equals the `trading_calendars` row. Task 1.6 compares against seed constants, not the stored row. Task 4.3 does not add a database check for this. Both are acceptable if you treat the seed constants as the definition. Say so in 6.1's criterion-to-test mapping, or add a one-line assertion on the migrated database in 4.3.

### [NOTE] Pre-2020 exceptions live in new modules, not the existing seed modules

The slice design says the pre-2020 rows go "in the existing seed modules". The tasks (3.1) create `seed_cme_equity_history.py` and `seed_cme_metals_history.py`, and the Working Rules justify this by the 300-line limit. The slice's Technical Requirements allow a separate module when the seed modules would pass that size, so this is consistent. The tasks also add the `_insert_sql` keyword in 3.8 and the shared test helper in 3.1. Both trace to D3 and DRY, so they are not scope creep.

### [PASS] Success criteria coverage and dependency order

Each criterion maps to at least one task:
- **Criterion 1:** 4.3.
- **Criterion 2:** the 4.2 check and 4.3 `test_cme_history_2020_unchanged`.
- **Criteria 3 and 4:** 1.6, 3.4 and 3.7.
- **Criterion 5:** 2.2 and 4.3.
- **Criterion 6:** 4.4.
- **Criterion 7:** 5.1 and 5.2.
- **Criterion 8:** 4.3, which compares all four calendars.

The order is acyclic: eras, then population, then exceptions, then migration, then script, then validation. Test tasks sit next to the code they test. The slice states no NFR, so no load test or CI-gating task is needed. The stop rules for unsourced eras and exceptions are explicit and tied back to the single `CME_DATASET_START`.

### Run Digest

- Response length: 6006 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 180776 / 90753 / 90017 / 5350 / not computed
- Duration: 47.2 s
- Cost: $0.42 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
