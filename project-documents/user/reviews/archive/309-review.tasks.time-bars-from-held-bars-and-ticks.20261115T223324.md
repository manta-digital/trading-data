---
docType: review
layer: project
reviewType: tasks
slice: time-bars-from-held-bars-and-ticks
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: FAIL
verdictSource: derived
sourceDocument: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md
aiModel: claude-haiku-5-5
aiProfile: sdk
status: complete
dateCreated: 20261115
dateUpdated: 20261115
reviewedSha: 397d7ee933fb58db3f57c85a4c56d1ff898ced72
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 13
effort: xhigh
turns: 14
promptTokens: 538037
cachedTokens: 377848
cacheWriteTokens: 160179
completionTokens: 61940
durationSeconds: 272.0
costUsd: 0.207459
costBasis: provider
runId: run-20261115-p5-353fa79c
squadronVersion: 0.24.28
findings:
  - id: F001
    severity: fail
    category: correctness
    summary: "Measurement asks for the widest run but reuses a function that returns the earliest one"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:141-144"
  - id: F002
    severity: concern
    category: ci-gating
    summary: "The load test is not gated in CI, and the gate is not owned by this breakdown"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:366-368"
  - id: F003
    severity: concern
    category: completeness
    summary: "The ledger guard has no rule for a session that has no ledger row"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:257-261"
  - id: F004
    severity: concern
    category: sequencing
    summary: "The BLOCKED rule stops more work than it needs to"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:167-169"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Tasks 6.3 and 9.1 are too large for one checkbox each"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:304-327"
  - id: F006
    severity: concern
    category: proportionality
    summary: "The oracle comparison runs at five sizes, which repeats coverage the criteria do not need"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:119-122"
  - id: F007
    severity: note
    category: completeness
    summary: "`BAR_SOURCE_NAME` is not named in any task"
    location: "src/manta_trading/data/tick/constants.py:443"
  - id: F008
    severity: note
    category: traceability
    summary: "Production checks for criteria 1, 7 and 8 have no evidence record"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:494-506"
  - id: F009
    severity: note
    category: sequencing
    summary: "Issues are closed before merge and release"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:467-473"
  - id: F010
    severity: note
    category: proportionality
    summary: "The measurement script is a committed tool for a one-off number"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:134-152"
  - id: F011
    severity: pass
    category: traceability
    summary: "Every success criterion has a named test or a handoff step"
    location: "project-documents/user/slices/309-slice.time-bars-from-held-bars-and-ticks.md:134-141"
  - id: F012
    severity: pass
    category: dependencies
    summary: "Prerequisite symbols exist, and the removals have no hidden consumers"
    location: "src/manta_trading/data/tick/bar_parity.py:37"
  - id: F013
    severity: pass
    category: sequencing
    summary: "Commit markers sit at green points, and tests sit with their code"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:94"
---

# Review: tasks — slice 309

**Verdict:** FAIL
**Model:** claude-haiku-5-5

## Findings

### [FAIL] Measurement asks for the widest run but reuses a function that returns the earliest one

Task 3.1 measures "the widest run of consecutive sessions whose ledger counts sum to 95-100% of `--limit`" and says to reuse `pick_range` without copying it. `pick_range` (`scripts/measure_tick_bars.py:97-112`) scans start positions in order and returns the first run that lands in the band. It does not search for the widest run. Criterion 5 and the default rule in 3.2 both depend on the widest range, so reusing `pick_range` as written would record the wrong range as "widest".

3.2 also has no handling for the cases it will hit. If no run lands in the band at the lowered limit, `pick_range` raises `ValueError`, and "repeat until it holds" has no bound.

Fix: say that 3.1 adds a widest-run search (moving or changing `pick_range` only if its existing behaviour is kept). In 3.2, bound the reduction loop and say what happens when no run fits, for example write `BLOCKED:` with the reason.

### [CONCERN] The load test is not gated in CI, and the gate is not owned by this breakdown

7.3 adds `test/load/test_309_time_bars_nfr.py` and says "CI has no test job; the load-tier job is slice 907's. Task 11.1 runs this file." Slice 907 exists (`project-documents/user/slices/907-slice.ci-pipeline-and-load-test-gating.md`). Its scope covers running `test/load/`, and its status is `not_started`. The frontmatter (lines 7-9) does not name 907, and no task here wires CI.

The deferral is explicit, which is better than silent. But the file ships ungated, and the Python rules require CI to gate load tests on concurrency paths. 7.3 is a loop-starvation check, so it is one of those paths.

Fix: add 907 to `dependencies`, state what 309 needs in 907's gate (the file path and `MT_RUN_LOAD_TESTS=1`), or add a CI wiring task.

### [CONCERN] The ledger guard has no rule for a session that has no ledger row

5.3's guard compares a tick-cut's `trade_count` sum with `status_reads.ledger_totals` for the session. 4.1 reads every tick-sourced session that is not `missing`. The design says a session with no ledger row is "not complete", which is not the same as `missing`. So a session can be read with no ledger count, and 5.3 does not say what to do.

A junior will either skip the guard, which is the silent fallback the project forbids, or invent a failure. Fix: state the behaviour. The simplest consistent rule is that a session with no ledger row is not read (`None`, listed in `incomplete`), which matches the design's "adds nothing to the admission count" and gives the guard a count to compare against.

### [CONCERN] The BLOCKED rule stops more work than it needs to

If production is unreachable, 3.2 writes `BLOCKED:` and "later sections do not start without the measured constant." Only 5.2 onward reads `API_MAX_TIME_BAR_TRADES`. Section 4 (source choice) and 5.1 (provider reader at any size) do not touch the limit. Both could proceed while 3.2 waits.

Fix: narrow the stop to 5.2 and later. Either move 4 and 5.1 ahead of 3.2 in the file or say explicitly that they may run first. This keeps every commit green, because the limit is still measured before anything reads it.

### [CONCERN] Tasks 6.3 and 9.1 are too large for one checkbox each

6.3 covers two unit test files, a replacement integration file with about fifteen named cases on two routes, the 422, 499, 504 and 500 mappings with a log assertion, updates to two existing files, and the criterion 6 check. All of it lands in one commit with 6.1 and 6.2. The earlier review asked for a split and the revision did not make one.

Split 6.3 into a unit item, a route-integration item and an existing-tests item.

9.1 (lines 432-446) bundles three documents, the OpenAPI regeneration and two example tests. Split the documents from the regeneration.

### [CONCERN] The oracle comparison runs at five sizes, which repeats coverage the criteria do not need

Criterion 2 needs one comparison at 1m. The design's 4-hour anchoring (issue #47) justifies one 4h comparison. 2.2 also repeats the oracle comparison at 15s, 5m, 1h and 1d, and the reader and route tests cover sizes again. The earlier review (F011) flagged the same repetition.

Keep 1m, 4h, and the per-size `trade_count` total that 2.2 already has. Drop the other comparisons.

### [NOTE] `BAR_SOURCE_NAME` is not named in any task

`BAR_SOURCE_NAME = "futures ohlcv-1m"` is used by `cli/commands/overview_futures.py` (lines 14, 35, 38). 8.1's grep for `BAR_SOURCE` will match it. But the task does not say whether it is deleted, since its text is 306's provider-only wording, or kept as "the source label" under "Kept as is". Name it in 8.1 or 8.3.

### [NOTE] Production checks for criteria 1, 7 and 8 have no evidence record

These three criteria are carried only by the Phase 7 handoff, which has no checkboxes and no place to record results. Keeping merge and release out of the task list follows CLAUDE.md, and the handoff names each check. The gap is only the evidence: nothing records that the production responses were seen, the health output was checked, or the two projects were messaged. Say where those results go. The earlier review's F002 raised the same point.

### [NOTE] Issues are closed before merge and release

10.2 closes #69, #74 and #40 on the slice branch, before merge and release. If the slice is not merged, #40 is closed while 1-second bars are not served. The design's order of work (step 5) also puts issues before release, so this follows the design. Consider moving 10.2 into the handoff after the release check.

### [NOTE] The measurement script is a committed tool for a one-off number

3.1 commits a second measurement script, its unit tests and a shared-module refactor of `pick_range`. Criterion 5 needs a recorded measurement. The precedent is `scripts/measure_tick_bars.py` from 308, so this is defensible. A smaller option is a size or range parameter on the existing script rather than a sibling. Either way, the task should say which.

### [PASS] Every success criterion has a named test or a handoff step

- C2 → 2.2
- C3 → 4.3, 5.4
- C4 → 4.3, 6.3
- C5 → 3.1, 3.2, 5.2, 5.4, 6.3 (the widest-run defect above applies)
- C6 → 2.2, 5.2, 6.3, 11.1
- C7 at test scale → 8.3; C1, C7 and C8 in production → Phase 7 handoff

No design section lacks a task, apart from the ledger-row case above.

### [PASS] Prerequisite symbols exist, and the removals have no hidden consumers

I confirmed `_TICK_MINUTES`, `_DAY_BUCKET` (`bar_reads.py:44`), `verdicts_for`, `ledger_totals`, `sessions_dated`, `_entry` and `trades_to_scan` (`tick_bar_reads.py`), `ReadAbandoned`, `CLIENT_CLOSED_REQUEST`, `_HISTORY_WANTS` (`universe.py:89`), and the three `read_provider_bars` callers 5.1 names. `TickBarGranularity` is used only by the time-bar route code in `futures_series.py`, so removing it does not touch the N-trade bar route. The dependency graph has no cycles, and 4.1 avoids `parity_plan.py`, which 8.2 deletes.

### [PASS] Commit markers sit at green points, and tests sit with their code

There are 16 plain `**Commit**:` lines, each after a section or sub-section that carries its own tests. The earlier review's broken-tree marker after task 4 is fixed: 5.1 now updates the route callers in the same commit. I have not run the tree, so the "green" claims are by reading.

### Run Digest

- Response length: 10368 chars
- Response is newline-free: no
- Tool calls made: 13
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: xhigh
- Turns: 14
- Tokens — prompt / cached / cache write / completion / reasoning: 538037 / 377848 / 160179 / 61940 / not computed
- Duration: 272.0 s
- Cost: $0.21 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 13
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 13
- Finding-shaped matches — surviving validation: 13

### Raw Response

Both documents are read. Next I'll check the code and docs the task file relies on: the existing symbols and files it names, the enum it removes, the load-tier gate, and the "Future Work" entry.
The grep turned up an existing review of an earlier revision of this file (`reviews/309-review.tasks...`), and the load-tier gating is deferred to slice 907. I still need to confirm the "Future Work" entry the task cites, the 907 owner, and the remaining named files and symbols.
Most of the cited symbols and files check out. I'll read the prior task review to see which findings the current revision already addressed, the 907 slice to see whether it covers this load test, and `pick_range`, which the measurement task reuses.
## Summary

**Verdict: FAIL.** One finding must be fixed before section 3 is built. The measurement is specified as the *widest* run, but the function it says to reuse returns the *earliest* run that reaches the band. Four concerns need a sentence each in the task file, and one note is about proportionality.

The rest of the plan holds. Each success criterion has a named test or a Phase 7 handoff step, every symbol the tasks extend exists, and the 16 commit markers sit at tested points.

I reviewed the on-disk task file. The session-start git status showed it modified against HEAD. The earlier review in `reviews/309-review.tasks...` is mostly addressed in this revision: F004, F006–F010, F012 and F013 are resolved. F003 is now an explicit deferral to slice 907, which is a concern below. F002, F005 and F011 are only partly addressed.

## Findings

### [FAIL] Measurement asks for the widest run but reuses a function that returns the earliest one
category: correctness
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:141-144

Task 3.1 measures "the widest run of consecutive sessions whose ledger counts sum to 95-100% of `--limit`" and says to reuse `pick_range` without copying it. `pick_range` (`scripts/measure_tick_bars.py:97-112`) scans start positions in order and returns the first run that lands in the band. It does not search for the widest run. Criterion 5 and the default rule in 3.2 both depend on the widest range, so reusing `pick_range` as written would record the wrong range as "widest".

3.2 also has no handling for the cases it will hit. If no run lands in the band at the lowered limit, `pick_range` raises `ValueError`, and "repeat until it holds" has no bound.

Fix: say that 3.1 adds a widest-run search (moving or changing `pick_range` only if its existing behaviour is kept). In 3.2, bound the reduction loop and say what happens when no run fits, for example write `BLOCKED:` with the reason.

### [CONCERN] The load test is not gated in CI, and the gate is not owned by this breakdown
category: ci-gating
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:366-368

7.3 adds `test/load/test_309_time_bars_nfr.py` and says "CI has no test job; the load-tier job is slice 907's. Task 11.1 runs this file." Slice 907 exists (`project-documents/user/slices/907-slice.ci-pipeline-and-load-test-gating.md`). Its scope covers running `test/load/`, and its status is `not_started`. The frontmatter (lines 7-9) does not name 907, and no task here wires CI.

The deferral is explicit, which is better than silent. But the file ships ungated, and the Python rules require CI to gate load tests on concurrency paths. 7.3 is a loop-starvation check, so it is one of those paths.

Fix: add 907 to `dependencies`, state what 309 needs in 907's gate (the file path and `MT_RUN_LOAD_TESTS=1`), or add a CI wiring task.

### [CONCERN] The ledger guard has no rule for a session that has no ledger row
category: completeness
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:257-261

5.3's guard compares a tick-cut's `trade_count` sum with `status_reads.ledger_totals` for the session. 4.1 reads every tick-sourced session that is not `missing`. The design says a session with no ledger row is "not complete", which is not the same as `missing`. So a session can be read with no ledger count, and 5.3 does not say what to do.

A junior will either skip the guard, which is the silent fallback the project forbids, or invent a failure. Fix: state the behaviour. The simplest consistent rule is that a session with no ledger row is not read (`None`, listed in `incomplete`), which matches the design's "adds nothing to the admission count" and gives the guard a count to compare against.

### [CONCERN] The BLOCKED rule stops more work than it needs to
category: sequencing
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:167-169

If production is unreachable, 3.2 writes `BLOCKED:` and "later sections do not start without the measured constant." Only 5.2 onward reads `API_MAX_TIME_BAR_TRADES`. Section 4 (source choice) and 5.1 (provider reader at any size) do not touch the limit. Both could proceed while 3.2 waits.

Fix: narrow the stop to 5.2 and later. Either move 4 and 5.1 ahead of 3.2 in the file or say explicitly that they may run first. This keeps every commit green, because the limit is still measured before anything reads it.

### [CONCERN] Tasks 6.3 and 9.1 are too large for one checkbox each
category: proportionality
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:304-327

6.3 covers two unit test files, a replacement integration file with about fifteen named cases on two routes, the 422, 499, 504 and 500 mappings with a log assertion, updates to two existing files, and the criterion 6 check. All of it lands in one commit with 6.1 and 6.2. The earlier review asked for a split and the revision did not make one.

Split 6.3 into a unit item, a route-integration item and an existing-tests item.

9.1 (lines 432-446) bundles three documents, the OpenAPI regeneration and two example tests. Split the documents from the regeneration.

### [CONCERN] The oracle comparison runs at five sizes, which repeats coverage the criteria do not need
category: proportionality
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:119-122

Criterion 2 needs one comparison at 1m. The design's 4-hour anchoring (issue #47) justifies one 4h comparison. 2.2 also repeats the oracle comparison at 15s, 5m, 1h and 1d, and the reader and route tests cover sizes again. The earlier review (F011) flagged the same repetition.

Keep 1m, 4h, and the per-size `trade_count` total that 2.2 already has. Drop the other comparisons.

### [NOTE] `BAR_SOURCE_NAME` is not named in any task
category: completeness
location: src/manta_trading/data/tick/constants.py:443

`BAR_SOURCE_NAME = "futures ohlcv-1m"` is used by `cli/commands/overview_futures.py` (lines 14, 35, 38). 8.1's grep for `BAR_SOURCE` will match it. But the task does not say whether it is deleted, since its text is 306's provider-only wording, or kept as "the source label" under "Kept as is". Name it in 8.1 or 8.3.

### [NOTE] Production checks for criteria 1, 7 and 8 have no evidence record
category: traceability
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:494-506

These three criteria are carried only by the Phase 7 handoff, which has no checkboxes and no place to record results. Keeping merge and release out of the task list follows CLAUDE.md, and the handoff names each check. The gap is only the evidence: nothing records that the production responses were seen, the health output was checked, or the two projects were messaged. Say where those results go. The earlier review's F002 raised the same point.

### [NOTE] Issues are closed before merge and release
category: sequencing
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:467-473

10.2 closes #69, #74 and #40 on the slice branch, before merge and release. If the slice is not merged, #40 is closed while 1-second bars are not served. The design's order of work (step 5) also puts issues before release, so this follows the design. Consider moving 10.2 into the handoff after the release check.

### [NOTE] The measurement script is a committed tool for a one-off number
category: proportionality
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:134-152

3.1 commits a second measurement script, its unit tests and a shared-module refactor of `pick_range`. Criterion 5 needs a recorded measurement. The precedent is `scripts/measure_tick_bars.py` from 308, so this is defensible. A smaller option is a size or range parameter on the existing script rather than a sibling. Either way, the task should say which.

### [PASS] Every success criterion has a named test or a handoff step
category: traceability
location: project-documents/user/slices/309-slice.time-bars-from-held-bars-and-ticks.md:134-141

- C2 → 2.2
- C3 → 4.3, 5.4
- C4 → 4.3, 6.3
- C5 → 3.1, 3.2, 5.2, 5.4, 6.3 (the widest-run defect above applies)
- C6 → 2.2, 5.2, 6.3, 11.1
- C7 at test scale → 8.3; C1, C7 and C8 in production → Phase 7 handoff

No design section lacks a task, apart from the ledger-row case above.

### [PASS] Prerequisite symbols exist, and the removals have no hidden consumers
category: dependencies
location: src/manta_trading/data/tick/bar_parity.py:37

I confirmed `_TICK_MINUTES`, `_DAY_BUCKET` (`bar_reads.py:44`), `verdicts_for`, `ledger_totals`, `sessions_dated`, `_entry` and `trades_to_scan` (`tick_bar_reads.py`), `ReadAbandoned`, `CLIENT_CLOSED_REQUEST`, `_HISTORY_WANTS` (`universe.py:89`), and the three `read_provider_bars` callers 5.1 names. `TickBarGranularity` is used only by the time-bar route code in `futures_series.py`, so removing it does not touch the N-trade bar route. The dependency graph has no cycles, and 4.1 avoids `parity_plan.py`, which 8.2 deletes.

### [PASS] Commit markers sit at green points, and tests sit with their code
category: sequencing
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:94

There are 16 plain `**Commit**:` lines, each after a section or sub-section that carries its own tests. The earlier review's broken-tree marker after task 4 is fixed: 5.1 now updates the route callers in the same commit. I have not run the tree, so the "green" claims are by reading.
