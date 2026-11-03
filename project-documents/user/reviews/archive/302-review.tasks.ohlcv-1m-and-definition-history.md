---
docType: review
layer: project
reviewType: tasks
slice: ohlcv-1m-and-definition-history
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261103
dateUpdated: 20261103
reviewedSha: ffc1b852c2854f1284e33dd5f8830629a2fc9cb4
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 105842
cachedTokens: 11198
cacheWriteTokens: 94640
completionTokens: 5192
durationSeconds: 43.4
costUsd: 0.432379
costBasis: provider
runId: run-20261103-p5-74a0f26d
squadronVersion: 0.24.14
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria coverage is complete"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:163-282"
  - id: F002
    severity: pass
    category: sequencing
    summary: "Sequencing and test-with pattern"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:43-299"
  - id: F003
    severity: concern
    category: clarity
    summary: "Task 6.1 does not say how the definition unit becomes \"ingested\""
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:165-167"
  - id: F004
    severity: concern
    category: proportionality
    summary: "A few tests go beyond what the success criteria need"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:60-64,132-134,157-159,185-187,165-167,227-229"
  - id: F005
    severity: concern
    category: sequencing
    summary: "Task 1 commit leaves `ARCHIVED_SCHEMAS` ahead of the database check constraint"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:48-66"
  - id: F006
    severity: note
    category: scoping
    summary: "Tasks 9 and 10 are the heaviest and could be split if they run long"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:236-267"
  - id: F007
    severity: note
    category: nfr
    summary: "No load-test or CI wiring task is needed"
    location: "project-documents/user/slices/302-slice.ohlcv-1m-and-definition-history.md:406"
---

# Review: tasks — slice 302

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria coverage is complete

Each criterion traces to at least one task:

| Criterion | Task |
|---|---|
| 1 (awaiting definitions) | 6.3 |
| 2 (end-to-end ingest) | 6.2 |
| 3 (`unit_day`, `resolution`, `session_boundary`, `overlap`) | 4.2 and 6.4 |
| 4 (no cross-family supersession) | 6.3 and 4.6 |
| 5 (status section, sparse bars, `provider_hole`) | 9.3–9.5 |
| 6 (`coverage --schema`, exit 3, unchanged default output) | 10.1–10.2 |
| 7 (2012 bar placement) | 6.6 |
| 8 (import-time validation, one test per rule) | 7.1–7.2 |
| 9 (production plan unchanged) | 8.3 |
| 10 (estimate row) | 11.1–11.2 |
| 11 (pre-2020 definition failure) | 6.7 |

The D9 items are covered by 3.4 (drill tables and fingerprint), 3.5 (roles) and 4.7 (archive path). Every Technical Requirements unit and integration test bullet has a home. D10 and D11 need no code task.

### [PASS] Sequencing and test-with pattern

Dependencies run in order with no cycles.
- Constants and the family come first, then the migration, projector, fixture, ingest tests, `HistoryWant`, planner, status, coverage and estimate.
- 3.4 fixes the drill pin test in the same commit that breaks it.
- 6.1's helper is reused by 9.5.
- Each implementation task is followed by its own tests.
- `**Commit**:` markers sit at the end of each coherent group, are plain lines, and are neither per-task nor only at the end.

### [CONCERN] Task 6.1 does not say how the definition unit becomes "ingested"

The slice design says definition units are projected by the acquisition pass, not by ingest. `FAMILY_LEDGER` deliberately has no `DEFINITION` entry. Yet 6.2, 6.3 and 6.7 assume a definition unit reaches the state the `has_definitions` prerequisite requires, and the walkthrough has ingest reporting the bar unit "after the definition unit".

Task 6.1 only says "record a definition file and a bar file as units". It names no existing function that projects definitions into `tick_definition` or marks the unit ingested, and it doesn't tell the implementer to read that code first. A junior is likely to stall or invent a path.

Fix: add a read-first step naming the existing definition projection/test helper (in `test_tick_definitions.py` or the acquisition-pass code) and state what the helper must leave behind (`tick_definition` rows plus the unit's status).

### [CONCERN] A few tests go beyond what the success criteria need

The file is a single task file of 12 tasks for a 4/5-effort slice, which is a reasonable shape. These items overlap or add little:
- **1.4 and 4.5:** both test the `FAMILY_LEDGER` to `INGEST_PROJECTORS` relationship. 1.4 even says the registry test is deferred to task 4. Keep the check only in 4.5.
- **5.3:** a separate fixture sanity test, including re-summing volume from the source `tbbo` file, is not a stated criterion. Criterion 2 and 5.1's own success line already require that the file decodes and spans both sides of the break. Fold the decode and count check into 6.2 and drop the volume re-derivation, or justify it.
- **6.5:** the v3-sample projection test duplicates the decode test in 2.2 and the projector test in 4.6. It is a defensible smoke test but traces to no criterion. Merge it into 4.6 or cut it.
- **6.1:** "a trivial test records both units and ingest can see them" is redundant once 6.2 uses the helper.
- **8.3 and 12.3:** both run the "production pass unchanged" check (walkthrough step 4). Run it once.

### [CONCERN] Task 1 commit leaves `ARCHIVED_SCHEMAS` ahead of the database check constraint

1.1 adds `OHLCV_1M` to `ARCHIVED_SCHEMAS`, but the `tick_request_schema_check` constraint is only widened in task 3 (3.2). The first commit's verification (1.4) runs only `test/unit/data/tick`. Any integration test that compares the rendered constraint to `ARCHIVED_SCHEMAS`, such as the `tick_008` test, or that records a request for every archived schema, could fail at that commit without anyone noticing.

Either move the `ARCHIVED_SCHEMAS` membership change into task 3 beside the migration, or have 1.4's success line also run the existing tick migration integration tests.

### [NOTE] Tasks 9 and 10 are the heaviest and could be split if they run long

- **9.1** touches three status modules with a signature-threading change.
- **10.1** combines the `build_coverage` family parameter, the new CLI flag, a new raw-scan query, an exit-code path and an error path.

Both have clear success lines and regression guards ("tick tier unchanged"), so splitting is optional. 10.1 would split cleanly into the library change and the CLI flag with rejection. Also, 9.3 has no direct unit test and relies on 9.5's integration tests, which is acceptable.

### [NOTE] No load-test or CI wiring task is needed

The slice restates no performance NFR and defers manifest-scale timing to 305, so the absence of a `tests/load/` task is correct.

### Run Digest

- Response length: 5892 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 105842 / 11198 / 94640 / 5192 / not computed
- Duration: 43.4 s
- Cost: $0.43 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
