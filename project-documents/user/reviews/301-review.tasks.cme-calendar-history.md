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
reviewedSha: a2f95b1d43beeb207b00f8d5a4af38f67bca6e0e
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 6
turns: 7
promptTokens: 179770
cachedTokens: 90260
cacheWriteTokens: 89504
completionTokens: 5288
durationSeconds: 44.7
costUsd: 0.420683
costBasis: provider
runId: run-20261101-p5-3ef67db9
squadronVersion: 0.24.10
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria coverage"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md"
  - id: F002
    severity: pass
    category: sequencing
    summary: "Sequencing and test-with pattern"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md"
  - id: F003
    severity: concern
    category: sequencing
    summary: "Verification walkthrough test path does not match where the new integration tests live"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md:195-199"
  - id: F004
    severity: concern
    category: proportionality
    summary: "Task 2.4 overlaps with 4.3 and 4.4"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:220-229"
  - id: F005
    severity: concern
    category: source-control
    summary: "Unrelated uncommitted edits sit in files this slice will modify"
    location: "src/manta_trading/market/schema/migrations/minute.py"
  - id: F006
    severity: note
    category: testing
    summary: "Source tests only check that `source` is non-empty"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:160-169"
  - id: F007
    severity: note
    category: proportionality
    summary: "Task granularity is high but justified"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md"
  - id: F008
    severity: note
    category: coverage
    summary: "No load-test or CI-wiring task needed"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md"
---

# Review: tasks — slice 301

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria coverage

Every criterion maps to a task.
- **SC1:** 4.3 (first session, no gaps, holiday row counts).
- **SC2:** the 4.2 before/after comparison and `test_cme_history_2020_unchanged` in 4.3.
- **SC3:** the source tests in 1.6, 3.4 and 3.7.
- **SC4:** 1.6.
- **SC5:** 1.2, 2.2 and 4.3.
- **SC6:** 4.4.
- **SC7:** 5.1 and 5.2.
- **SC8:** 4.3 (all four calendars).

Task 6.1 then re-checks the mapping.

### [PASS] Sequencing and test-with pattern

Dependencies run in order and none are circular.
- The `HoursEra` type (1.1) comes before the code that reads it (2.1, 2.3).
- The era and exception builders (1.5, 3.8) come before migration 065 (Section 4).
- The `to_regclass` guard in 2.3 covers migrations 058 and 059 running before the table exists.
- The pre-2020 exceptions are kept in separate tuples so the 065 precondition does not abort on a fresh database.
- Stop-rule handling is explicit and is tied to the single `CME_DATASET_START` definition.

### [CONCERN] Verification walkthrough test path does not match where the new integration tests live

The design's walkthrough step 1 runs `uv run pytest test/integration/data -q -k "calendar or session"`. Task 4.3 puts the migration tests in `test/integration/test_migration_065_cme_calendar_history.py`, and 5.2 and 2.4 also use `test/integration/`. A glob for `test/integration/data/*calendar*` returned no files. So walkthrough step 1 would not collect the new tests, and 6.1 says it runs that step. Have 6.1 run the `test/integration` form that 4.6 already uses. Alternatively, record in the 6.1 task that the design's path is stale. Do not move the files.

### [CONCERN] Task 2.4 overlaps with 4.3 and 4.4

Task 2.4 hand-builds `trading_calendar_hours` in a throwaway database. It tests a range spanning an era change, a range before the first era, and the table being absent. Task 4.3 then tests the same behaviour through the real migration: sessions on each side of an era change, and the empty database migrated through 058 and 059 before 065. The only case unique to 2.4 is the `to_regclass` guard when the table is absent, and the empty-database path in 4.3 exercises it implicitly. The "calendar with no rows" case adds a little. Reduce 2.4 to the guard cases (table absent, no rows). Leave the era-change and before-first-era assertions to 2.2 (unit) and 4.3 (end to end).

### [CONCERN] Unrelated uncommitted edits sit in files this slice will modify

Git status at the start of the session shows uncommitted changes in `market/schema/migrations/minute.py` and `test/unit/test_schema_migrations.py`. Tasks 4.1 and 4.5 edit both files. They also leave the checkout on `main` with a dirty tree. The Working Rules say to check `git diff` for swept lines, but nothing says what to do with pre-existing unrelated edits. Those edits could end up in the 065 commit. Add a first step to Section 4 (or to the Working Rules) that stages only this slice's hunks. Alternatively, require the working tree to be clean before the slice branch starts.

### [NOTE] Source tests only check that `source` is non-empty

SC3 requires a source document "and its date". Task 1.6 only asserts a non-empty `source` string, and 3.4 only checks that a `# source:` comment is present. This matches the slice's own "fails a unit test without a source" wording and the 221 precedent. A stricter check such as a URL or a year pattern would be more than the criteria require, so I'm not asking for it. The review of the source list stays the real control.

### [NOTE] Task granularity is high but justified

The file has about 33 tasks in one document for a slice rated 3/5. Four research tasks (1.3, 1.4, 3.2, 3.5) and the two year-range splits (3.3, 3.6) mirror the real ES/GC and year-range structure of the work. Merging them would produce overweight research tasks, so I would keep them. The five commit-checkpoint tasks could be folded into the last task of each section. They are cheap and enforce the commit cadence, so this is optional. The step-level detail in 2.3 (`to_regclass`) and 3.8 (`dataclasses.replace`, the `_insert_sql` keyword) goes beyond the slice design. Each item addresses a concrete constraint, though: migrations 058 and 059 run before the table exists, and D3 needs a plain `INSERT` with no `ON CONFLICT`.

### [NOTE] No load-test or CI-wiring task needed

The slice states no NFR, so there is no `tests/load/` requirement. Existing CI runs the new unit and integration tests, so no separate wiring task is needed.

### Run Digest

- Response length: 5698 chars
- Response is newline-free: no
- Tool calls made: 6
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 7
- Tokens — prompt / cached / cache write / completion / reasoning: 179770 / 90260 / 89504 / 5288 / not computed
- Duration: 44.7 s
- Cost: $0.42 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
