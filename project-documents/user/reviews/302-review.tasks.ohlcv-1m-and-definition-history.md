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
reviewedSha: f4a8831ed22a9220b63450d9c17ad7a0bc3147d9
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 105680
cachedTokens: 11198
cacheWriteTokens: 94478
completionTokens: 5730
durationSeconds: 47.5
costUsd: 0.437111
costBasis: provider
runId: run-20261103-p5-74a0f26d
squadronVersion: 0.24.14
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria 1–11 all trace to tasks"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md"
  - id: F002
    severity: concern
    category: sequencing
    summary: "Task 1.1 appends to `ESTIMATE_SCHEMAS` ten groups before the estimate work"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:49"
  - id: F003
    severity: concern
    category: completeness
    summary: "Task 6.4 overlap test and task 6.3 cross-family test have no stated setup"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:171-176"
  - id: F004
    severity: concern
    category: scope
    summary: "Task 6.1 helper bundles several concerns"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:159"
  - id: F005
    severity: concern
    category: test-with
    summary: "Tasks 9.3 and 9.4 have no tests before the shared integration task"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:233-243"
  - id: F006
    severity: concern
    category: process
    summary: "Task 12.1 diffs against `main`, not the target"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:276"
  - id: F007
    severity: note
    category: proportionality
    summary: "Duplicated and over-split work"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:217"
---

# Review: tasks — slice 302

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria 1–11 all trace to tasks

| Criterion | Task(s) |
|---|---|
| 1 | 6.3 |
| 2 | 6.2 |
| 3 | 4.2 and 6.4 |
| 4 | 6.3 |
| 5 | 9.5 |
| 6 | 10.1, 10.2 |
| 7 | 6.5 |
| 8 | 7.2 |
| 9 | 8.3 |
| 10 | 11.2 |
| 11 | 6.6 |

The technical requirements are covered too:
- Families, plan table and `FAMILY_LEDGER` are tested in 1.4.
- Reader decode of the v1 and v3 samples is in 2.2.
- `day_unit` per family is in 9.2.
- The archive path pin is in 4.6.
- The drill pin and role privilege tests are in 3.4 and 3.5.
- `SOURCES.md` is updated in 2.1 and 5.2.

No tasks are out of scope. The slice states no NFR, so no load test or CI gate is needed.

### [CONCERN] Task 1.1 appends to `ESTIMATE_SCHEMAS` ten groups before the estimate work

Task 1.1 adds `OHLCV_1M` to `ESTIMATE_SCHEMAS`, so the first commit changes `mt data tick estimate` output. Estimate tests are not mentioned until task 11. 1.1 says to update only the constants tests, and the 1.4 success check runs `test/unit/data/tick`. An existing estimate test that enumerates rows could therefore fail at a commit marker that is meant to be a working, tested point. Move the `ESTIMATE_SCHEMAS` append into 11.1, where the row and its tests are handled together. Then change 11.1's wording "already includes it (1.1)" to match. This also keeps 1.1 limited to the enum, as its own reasoning for deferring `ARCHIVED_SCHEMAS` suggests.

### [CONCERN] Task 6.4 overlap test and task 6.3 cross-family test have no stated setup

- **6.4 overlap:** it says "a second current unit holding the same key fails `overlap`." Two units of the same family, shape and day normally supersede each other. The task does not say how to build two current units with the same key, such as a different shape or a unit outside supersession. A junior will have to guess the setup.
- **6.3 cross-family:** it needs an ingested tick-tier unit beside the bar unit. The 6.1 helper records only a definition file and a bar file, so it cannot produce a tick-tier unit. Say which fixture and projector path supply it. The derived `trades` fixture is the likely one.

### [CONCERN] Task 6.1 helper bundles several concerns

Task 6.1 puts four things in one task:
- unit recording that goes through the acquisition pass's definition projection
- a primary database migrated through 301
- a DBN writer helper
- the claim that it is "what the walkthrough names `test_bar_ingest_e2e` as containing"

It also has the weakest success criterion in the file ("exercised by 6.2"). Split out the DBN writer helper and its own check. Reconcile the walkthrough wording with the actual helper location, `tick_bar_helpers.py`. The slice says the helper lives inside `test_bar_ingest_e2e`.

### [CONCERN] Tasks 9.3 and 9.4 have no tests before the shared integration task

9.1 has tests in 9.2. 9.3 (new verdict builder) and 9.4 (rendering and the new JSON `families` list) are tested only by the integration task 9.5. 9.4's "output identical to before" is also asserted without a named test. Add a unit-level check immediately after 9.3 (no bar verdicts without a want) and a golden-output check for 9.4. The same applies to the change in 10.1 (`--schema` rejection of a family the entry does not want), which 10.2 does not list. Add it to 10.2.

### [CONCERN] Task 12.1 diffs against `main`, not the target

12.1 says `git diff main`. Task 8.3 uses `<target>`, and the project rules say the target comes from `cf config get git.integration_branch`. With an integration branch set, `git diff main` shows unrelated history and hides swept lines. Use `<target>`, as 8.3 does.

### [NOTE] Duplicated and over-split work

- **Duplicate check:** the production-unchanged check runs in 8.3 and again as walkthrough step 4 in 12.3. Skip step 4 in 12.3 and cite 8.3's note.
- **Over-split pairs:** these could be merged without losing traceability:
  - 3.1 (constants) into 3.2 (migration)
  - 4.1 and 4.2 (a trivial check and its test)
  - 5.2 (`SOURCES.md` entry) into 5.1
- **Prescriptive steps:** 4.3 and 9.1 restate the slice's data flow line by line. A reference to D3 and D6 plus the success check would be enough.

None of this blocks work. The twelve-commit cadence is acceptable, and the commit markers sit at working points apart from the issue in the first concern.

### Run Digest

- Response length: 5252 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 105680 / 11198 / 94478 / 5730 / not computed
- Duration: 47.5 s
- Cost: $0.44 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
