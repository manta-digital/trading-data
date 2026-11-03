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
reviewedSha: 38132374ab80ceb43737df0dadf33189412e472d
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 105361
cachedTokens: 8831
cacheWriteTokens: 96526
completionTokens: 4286
durationSeconds: 40.7
costUsd: 0.430626
costBasis: provider
runId: run-20261103-p5-74a0f26d
squadronVersion: 0.24.14
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria coverage is complete"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md"
  - id: F002
    severity: concern
    category: sequencing
    summary: "Drill pin and role tests are broken at commits 3 through 11"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:264-278"
  - id: F003
    severity: concern
    category: test-with
    summary: "Projector and superseded-unit change have no tests in their own commit"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:111-127"
  - id: F004
    severity: concern
    category: clarity
    summary: "Pre-2020 tests do not say how to build their inputs"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:173-180"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Walkthrough steps are run three times"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:248,260,291-294"
  - id: F006
    severity: note
    category: commit-placement
    summary: "Commit marker after 6.1 is a very small increment"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:147-151"
  - id: F007
    severity: note
    category: risk
    summary: "Base-commit comparison method in 8.3"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md:208-210"
  - id: F008
    severity: note
    category: sequencing
    summary: "Task order otherwise respects dependencies"
    location: "project-documents/user/tasks/302-tasks.ohlcv-1m-and-definition-history.md"
---

# Review: tasks — slice 302

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria coverage is complete

Each criterion has a task that proves it:
- **1, 2, 3, 4:** 6.3 and 6.2 cover criteria 1, 2 and 4. 6.4 covers criterion 3 (`unit_day`, `resolution`, `session_boundary`, `overlap`).
- **5, 6:** 9.5 and 10.2.
- **7, 11:** 6.6 and 6.7.
- **8:** 7.2.
- **9:** 8.3.
- **10:** 11.2.

The technical requirements are also covered: the families and plan-table unit tests (1.4), `unit_day_reason` (4.2), the drill pin and role privilege tests (12.1, 12.2), the archive path pin (12.3) and `SOURCES.md` (2.1, 5.2). I found no scope creep. Every task traces to a decision or criterion in the slice. The slice states no NFR, so no load-test or CI-gating task is required.

### [CONCERN] Drill pin and role tests are broken at commits 3 through 11

Task 3 creates `tick_ohlcv_1m` and `tick_bar_ledger`. The slice (D9) says the drill pin test, which asserts the drill tables equal the migrated tick tables, "fails until this is done". Task 12.1 is the only place that fixes it. Every `**Commit**:` marker from Task 3 through Task 11 therefore lands with a failing existing test. That breaks the rule that commits sit at working, tested points. `test_tick_role_privileges.py` may have the same problem.

Fix: move 12.1 (the bookkeeping table and the `tick_ohlcv_1m` fingerprint) and 12.2 into the Task 3 commit, or into a task right after it. Task 12 can then keep only 12.3, or fold into Task 4 or Task 6.

### [CONCERN] Projector and superseded-unit change have no tests in their own commit

Tasks 4.3 (`Ohlcv1mProjector`, about 300 lines) and 4.4 (the `ingest_plan` change to read the ledger by family) are implemented and committed with only registry and ledger-key unit tests (4.5). Nothing exercises `load`, `stored_count`, `delete_unit`, `finish`, or the changed superseded read until Task 6, two commits later. The `ingest_plan` change touches existing tick-tier behaviour, so a regression there would not show at the Task 4 commit.

Fix: either add a small projector test to Task 4, or move the Task 4 commit marker to after 6.2/6.3. The 5.x fixture work and the 6.1 helper can then precede it. Alternatively, make the Task 4 commit explicitly "untested until Task 6" and drop the marker.

### [CONCERN] Pre-2020 tests do not say how to build their inputs

6.6 needs a 2012 `ohlcv-1m` bar file. 6.7 needs a definition record with an undefined activation or expiration. No pre-2020 file is held, and no task says how to produce either. For example, it does not say whether to write a synthetic DBN with the writer or to build records in memory and bypass the file. A junior implementer will have to invent this. 6.7 also says to fix the definition code if it does not already fail, which could be a large unbounded change inside a "test" task.

Fix: add a sub-item to each saying how the synthetic input is built, reusing the 2.x or 5.x tooling. Give 6.7 a separate fix sub-item with its own success line.

### [CONCERN] Walkthrough steps are run three times

10.2 runs walkthrough step 2. 11.2 runs step 5. 13.3 runs steps 1–5 again. 13.1 and 13.2 also repeat the lint, mypy and test runs that each task already does. Step 2 also depends on the test helper to record units, since no CLI command does it, and 10.2 does not say how to use it outside pytest.

Fix: keep one walkthrough pass in 13.3. Drop the walkthrough runs from 10.2 and 11.2, or state that they use the 6.1 helper. Task 13 can also be trimmed to the CHANGELOG and README edit plus the walkthrough.

### [NOTE] Commit marker after 6.1 is a very small increment

The `**Commit**:` after 6.1 covers a helper and a single trivial test. It is a legitimate working point, but merging it into the 6.2–6.7 commit would reduce commit noise. This is optional.

### [NOTE] Base-commit comparison method in 8.3

Using `git stash` to reach the base commit risks losing work on a feature branch. Prefer only the worktree option, or `git worktree add` at the base commit.

### [NOTE] Task order otherwise respects dependencies

There are no circular dependencies:
- 1 comes before 3, which comes before 4 and 6.
- 5 comes before 6.
- 7 comes before 8 and 9.
- 9 comes before 10.
- 11 is independent.

Task sizes are reasonable for a 4/5 effort slice. 4.3 is the largest single item but follows an existing pattern.

### Run Digest

- Response length: 5354 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 105361 / 8831 / 96526 / 4286 / not computed
- Duration: 40.7 s
- Cost: $0.43 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
