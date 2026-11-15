---
docType: review
layer: project
reviewType: tasks
slice: time-bars-from-held-bars-and-ticks
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: FAIL
verdictSource: stated
sourceDocument: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md
aiModel: claude-haiku-5-5
aiProfile: sdk
status: complete
dateCreated: 20261115
dateUpdated: 20261115
reviewedSha: ffb25a61f4ff9d0df86f5747339a56f28af29f80
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 18
effort: xhigh
turns: 19
promptTokens: 1346308
cachedTokens: 1171625
cacheWriteTokens: 174663
completionTokens: 81883
durationSeconds: 357.7
costUsd: 0.296838
costBasis: provider
runId: run-20261115-p5-353fa79c
squadronVersion: 0.24.31
findings:
  - id: F001
    severity: fail
    category: sequencing
    summary: "The 6.2–6.5 commit marker leaves the unit tier red"
    location: "docs/api/reference.md:1882"
  - id: F002
    severity: concern
    category: verification
    summary: "The load test has no CI wiring task"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:410"
  - id: F003
    severity: concern
    category: completeness
    summary: "The trade-limit default loop has no stop condition"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:157"
  - id: F004
    severity: concern
    category: completeness
    summary: "No-ledger sessions fail the whole request, against the design"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:257"
  - id: F005
    severity: concern
    category: completeness
    summary: "1-second bars have no test beyond parsing"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:114"
  - id: F006
    severity: concern
    category: format
    summary: "Production checks and criteria 1 and 8 are not checkable"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:550"
  - id: F007
    severity: concern
    category: scope
    summary: "The task file is overweight and prescriptive"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md"
  - id: F008
    severity: note
    category: completeness
    summary: "The CLI's handling of LedgerCountExceeded is unspecified"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:388"
  - id: F009
    severity: note
    category: testability
    summary: "Criterion 2 is tested against a reinterpreted oracle"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:115"
  - id: F010
    severity: note
    category: clarity
    summary: "The 8.1 grep also matches `BAR_SOURCE_NAME`"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:423"
  - id: F011
    severity: note
    category: clarity
    summary: "7.1 does not name the file that defines `TICK_GET_CHOICES`"
    location: "src/manta_trading/data/tick/constants.py:408"
  - id: F012
    severity: note
    category: completeness
    summary: "3.1's script placement conflicts with its import"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:142"
  - id: F013
    severity: note
    category: format
    summary: "Two commits are labelled `refactor` but change behavior"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:453"
  - id: F014
    severity: note
    category: verification
    summary: "Validation hardcodes `main` as the base"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:529"
  - id: F015
    severity: pass
    category: completeness
    summary: "Criteria 2 through 7 each trace to a task"
    location: "project-documents/user/slices/309-slice.time-bars-from-held-bars-and-ticks.md:136"
  - id: F016
    severity: pass
    category: sequencing
    summary: "Sequencing, test pairing and commit markers hold"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md"
  - id: F017
    severity: pass
    category: verification
    summary: "Referenced symbols and files exist"
    location: "src/manta_trading/api_server/routes/futures_params.py:29"
---

# Review: tasks — slice 309

**Verdict:** FAIL
**Model:** claude-haiku-5-5

## Findings

### [FAIL] The 6.2–6.5 commit marker leaves the unit tier red

Task 6.3 deletes `TickBarGranularity` (`src/manta_trading/api_server/routes/futures_params.py:29`) and makes `granularity` a plain string. Task 6.2 changes the response shape. Several checks then fail until section 9, yet the marker at task line 333 says this commit is green (task lines 57–58 make the same promise):
- `docs/api/reference.md:1882` and `docs/api/agents.md:628` hold `<!-- from: …futures_params.TickBarGranularity -->` markers. `reference.md:1872,1934` and `agents.md:379,384` document `granularity:TickBarGranularity`. `scripts/check_api_docs.py:112` resolves these markers, and `test/unit/api_server/test_api_docs.py:51` runs the gate on the committed tree. The gate's own tests cover unresolvable markers and retyped parameters, so this should fail.
- `docs/api/openapi.json:4096` still has the `TickBarGranularity` schema and the old `source` field. `test/unit/api_server/test_openapi_artifact.py:56` asserts that the committed artifact equals the generated one. Regeneration is only in 9.3 (task lines 498–500), so every marker from 6.2 to 9 is red for this test.
- The 9.1 grep (task lines 482–483) does not match `TickBarGranularity` or `from:` markers, so section 9 could miss them too.

Fix: move the OpenAPI regeneration and the reference and agents parameter and marker edits into the 6.2–6.5 commit. Add `TickBarGranularity|<!-- from:|granularity:` to the 9.1 and 9.2 greps.

### [CONCERN] The load test has no CI wiring task

Task 7.3 defers gating to issue #32. The claim that CI has no test job is correct: `.github/workflows/ci.yml:5` triggers only on `v*` tags and runs publish steps. The project rule requires a CI wiring task whenever a load test task exists. "Deferred to an issue" is explicit, but it is not a wiring task. Slices 146 and 167 shipped NFR tests that never ran automatically. Slice 907 exists to fix exactly that (`project-documents/user/slices/907-slice.ci-pipeline-and-load-test-gating.md:18`, status not_started). The earlier review (`project-documents/user/reviews/309-review.tasks.time-bars-from-held-bars-and-ticks.md:124`) raised the same point.
Fix: add a CI wiring item to 7.3, or name slice 907 as the owner and record that the gate is manual until it lands.

### [CONCERN] The trade-limit default loop has no stop condition

Task 3.1 lowers the limit to `floor(2,000,000 × 10 / seconds)` and re-measures "repeat until it holds". If the median session (a) alone takes more than 10 s, no limit holding one session passes, and the loop never ends. It may also push the limit below one median session, which nothing in the design can serve. Changing the default also changes which run meets the 95–100% criterion, and the rule does not say how criterion 5 applies at each step.
Fix: state the stop. If one median session exceeds 10 s, write `BLOCKED:` with the measured time and stop before 5.2. Set a minimum limit, and say whether the 95% run is re-applied at each limit.

### [CONCERN] No-ledger sessions fail the whole request, against the design

The design (`project-documents/user/slices/309-slice.time-bars-from-held-bars-and-ticks.md:94`) says a session with no ledger row "is not complete, so it is listed in `incomplete` and adds nothing to the admission count." Task 4.1 (lines 183–187) reads any tick-sourced session whose tick verdict is not `missing`. Task 5.3 (lines 261–262) gives a no-ledger session a count of 0, so any trade read from it raises `LedgerCountExceeded` and the request returns 500. Unless no-ledger sessions always have verdict `missing` (I did not verify this), a routine state fails a whole request that the design calls incomplete.
Fix: in 4.1, a session with no ledger row resolves to `None` (not read, listed incomplete, matching 5.2's "adds nothing"). Keep the 500 for a count mismatch on a session that has a ledger row, and say so in 5.3.

### [CONCERN] 1-second bars have no test beyond parsing

The design's Value section (`project-documents/user/slices/309-slice.time-bars-from-held-bars-and-ticks.md:33`) promises the 1-second bars that trading-engine asked for (issue #40). The cut is tested at 1m, 90m, 4h and 1d (2.2). The route is tested at 1h, 1m, 15s and 1d (6.6). `1s` appears only in the parser tests (1.3). Nothing checks that a `1s` cut buckets per second, and nothing checks a `1s` route response. A full session at 1s is about 82,800 bars, over the 75,000 row ceiling, so the route test needs a narrow window.
Fix: add a `1s` case on a short window to 2.2 (oracle with width 1 s) and a `1s` route test on a narrow range to 6.6. Add a full-day `1s` 422 to 6.7.

### [CONCERN] Production checks and criteria 1 and 8 are not checkable

Criterion 1 (production), the production half of criterion 7, and criterion 8 (messages to two peers) appear only as prose under "Phase 7 handoff (not tasks)" (lines 550–569). They are agent steps: check, message, record evidence, close issues. Because they have no `[ ]`, task-checker cannot track them, the file's checklist rule is broken, and the file cannot show the slice complete.
Fix: turn the checks, the two messages, the evidence note and the issue closures into checkbox items with the release as their precondition. Keep the release and merge out of the list, per the rule against writing pushes as tasks.

### [CONCERN] The task file is overweight and prescriptive

The file has roughly 130 checkbox items and 18 commit markers, against a design of about 155 lines. Most of the length is breadth that the slice needs. Some of it is implementation prescription the design does not require:
- exact user-facing text (5.2 lines 239–240, 7.1 `GET_EPILOG` lines 387–388)
- the sliding-window algorithm and script internals in 3.1 (lines 149–156)
- 7.3, the load test. The design restates no NFR and has no load criterion, so the breakdown adds a test beyond what the criteria need. The rule allows that, but it adds five timed runs and a probe, and it pulls the full 77-minute load tier into 10.1.

Cuts: specify 3.1's outputs only (ledger sums, timings, runs, default chosen). Drop exact message text unless a test asserts it. Replace 9.1's six-bullet list with "every grep hit is replaced and listed in the note." Keep 7.3 only if you can name the design sentence it serves.

### [NOTE] The CLI's handling of LedgerCountExceeded is unspecified

The ledger guard in 5.3 runs in the CLI too, since `max_trades=None` only exempts the limit. It can fire after rows are already written. 7.1 says exit codes are unchanged but gives no requirement for this error, and 7.2 has no test for it.
Fix: state the exit code and what the footer shows in that case, and add a test.

### [NOTE] Criterion 2 is tested against a reinterpreted oracle

Criterion 2 says "summed by hand" (design line 137). Task 2.2 substitutes a Python bucketing oracle with the same floor rule as the SQL. The file says so openly. The oracle checks the SQL against Python, not the bucketing rule itself.
Fix: accept it, but add one boundary case checked by hand (a first and last trade either side of a bucket edge), so the rule is not checked only against itself.

### [NOTE] The 8.1 grep also matches `BAR_SOURCE_NAME`

The pattern `BAR_SOURCE` is a substring match for `BAR_SOURCE_NAME`, which 8.3 keeps. Read literally, 8.1's "fix it, do not keep the symbol" would send the agent to change `overview_futures`.
Fix: grep for `\bBAR_SOURCE\b`, and list `BAR_SOURCE_NAME` in 8.1's kept items.

### [NOTE] 7.1 does not name the file that defines `TICK_GET_CHOICES`

7.1 (lines 376–378) says to delete `TICK_GET_CHOICES` but lists only the CLI files. The symbol is defined here and imported at `src/manta_trading/cli/commands/tick_get_cmds.py:43`.
Fix: add this file to 7.1.

### [NOTE] 3.1's script placement conflicts with its import

3.1 says to write the script outside the repo and also that it "may import `pick_range`" from `scripts/measure_tick_bars.py:97`. An import from outside the repo fails unless the path is set up, and the task does not say how.
Fix: say to copy `pick_range`, or to put the repo's `scripts` directory on the path.

### [NOTE] Two commits are labelled `refactor` but change behavior

8.2 removes the `mt data tick parity` command, and 8.3 changes production wants, which changes `mt data health` and overview output. The project defines `refactor` as no behavior change.
Fix: use `chore:` for 8.3 (configuration). For 8.2, pick a type from the CLAUDE.md list that fits a removed user-facing command.

### [NOTE] Validation hardcodes `main` as the base

10.1 uses `git diff main` three times. The project rule says the target is `cf config get git.integration_branch`, or `main` when empty. I could not read that value from this session, and it is not in `.git/config`. If it is set, these checks compare against the wrong base.
Fix: read the target at the top of 10.1 and use it throughout.

### [PASS] Criteria 2 through 7 each trace to a task

Criterion 2 maps to 2.2. Criterion 3 maps to 4.3, 5.4 and 6.6. Criterion 4 maps to 4.3, 6.1 and 6.6. Criterion 5 maps to 3.1, 5.2 and 5.4. Criterion 6 maps to 6.5 and 10.1. Criterion 7 at test scale maps to 8.3. Criteria 1 and 8 are covered only by the handoff prose (see the checkbox finding). The CHANGELOG, issue closures and other-slice cleanup trace to the design's "Documents and issues cleaned up" section. I found no scope creep beyond 7.3.

### [PASS] Sequencing, test pairing and commit markers hold

The trade limit is measured (3.1) before any task reads it (5.2). Every implementation task sits in the same commit as its tests, or is followed by them. The 18 markers fall at working points, and I found no circular dependencies. The `**Commit**:` lines are plain text, not checkboxes.

### [PASS] Referenced symbols and files exist

These symbols exist where the task says: `ledger_totals`, `sessions_dated`, `verdicts_for`, `ReadAbandoned`, `_entry`, `product_plan`, `TICK_JIT_OPTION`, `_TICK_MINUTES`, `_DAY_BUCKET`, `tick_minutes`, `seed_parity`, `run_parity`, `pick_range`, `tick_errors`, `CLIENT_CLOSED_REQUEST`, `GET_EPILOG`, `_HISTORY_WANTS`, and the 308 limit plumbing (`constants.py:1401`, `deps.py:55`, `app.py:100`). The files to delete or edit exist, and the new modules are absent as expected. `TickBarGranularity` is used only by the futures time-bar routes, so its removal does not touch the N-trade tests that criterion 6 keeps unchanged.

### Run Digest

- Response length: 14830 chars
- Response is newline-free: no
- Tool calls made: 18
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: xhigh
- Turns: 19
- Tokens — prompt / cached / cache write / completion / reasoning: 1346308 / 1171625 / 174663 / 81883 / not computed
- Duration: 357.7 s
- Cost: $0.30 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 17
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 17
- Finding-shaped matches — surviving validation: 17
