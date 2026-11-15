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
reviewedSha: d1fa0a5694af0b6ccbea97dc058ad6a77f24a63a
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 17
effort: xhigh
turns: 18
promptTokens: 815373
cachedTokens: 653351
cacheWriteTokens: 162008
completionTokens: 48142
durationSeconds: 216.4
costUsd: 0.195679
costBasis: provider
runId: run-20261115-p5-353fa79c
squadronVersion: 0.24.28
findings:
  - id: F001
    severity: pass
    category: sequencing
    summary: "Prerequisites and dependency order hold"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:1-10"
  - id: F002
    severity: concern
    category: traceability
    summary: "Production criteria and peer notice have no owning task"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:56-58"
  - id: F003
    severity: fail
    category: ci-gating
    summary: "The new load test has no CI gate"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:298-304"
  - id: F004
    severity: concern
    category: sequencing
    summary: "Commit marker after task 4 sits on a broken tree"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:238"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Tasks 4 and 5 are too large for one commit each"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:240"
  - id: F006
    severity: concern
    category: completeness
    summary: "An existing test that imports the removed enum is not listed"
    location: "test/unit/api_server/test_tick_access.py:18"
  - id: F007
    severity: concern
    category: dependencies
    summary: "Task 3.1 builds on a module that 7.2 deletes"
    location: "src/manta_trading/data/tick/parity_plan.py:50"
  - id: F008
    severity: concern
    category: completeness
    summary: "The provisional trade-limit default can ship unmeasured"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:188-191"
  - id: F009
    severity: concern
    category: consistency
    summary: "Issue #40 is closed in the design and deferred in the task"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:440-442"
  - id: F010
    severity: concern
    category: correctness
    summary: "The load-test size does not match its bar count"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:299-300"
  - id: F011
    severity: concern
    category: proportionality
    summary: "The same behaviour is tested at several layers"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:259-270"
  - id: F012
    severity: note
    category: dry
    summary: "Reuse the existing Python bucketing oracle"
    location: "test/tick_support/parity_seed.py:54-72"
  - id: F013
    severity: note
    category: conventions
    summary: "The new measurement note needs frontmatter"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:322-323"
  - id: F014
    severity: note
    category: proportionality
    summary: "Some steps over-specify, and one assertion is left open"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:401-402"
---

# Review: tasks — slice 309

**Verdict:** FAIL
**Model:** claude-haiku-5-5

## Findings

### [PASS] Prerequisites and dependency order hold

Every symbol the tasks extend exists: `_TICK_MINUTES` (`bar_parity.py:37`), `_DAY_BUCKET` (`bar_reads.py:44`), `verdicts_for` (`tick_status_scope.py:53`), `ledger_totals` (`status_reads.py:149`), `_entry` and `trades_to_scan` (`tick_bar_reads.py:246,254`), `ReadAbandoned` (`tick_bar_reads.py:102`), `_uncuttable` and `CLIENT_CLOSED_REQUEST` (308 routes), the 308 settings chain (`config/__init__.py:303`, `app.py:100`, `deps.py:55`), `_HISTORY_WANTS` (`universe.py:89`), and the test helpers the tasks name. The dependency graph has no cycles. Each implementation group carries its tests.

### [CONCERN] Production criteria and peer notice have no owning task

Design criteria 1 (production), 7 (production) and 8 (trading-ui and trading-engine told) have no task. The Context Summary excludes them as "Not tasks (Phase 7 and the `/release` skill)". Task 10.1 then calls them "the Phase 7 steps listed in the Context Summary", but that list names them only as exclusions, not as steps. The design's order of work (step 6) puts release, the production check and the notice inside this slice. Name them as closing steps with the evidence each produces: the `sources`/`applied` response for criterion 1, the `mt data health` output for criterion 7, and the messages sent for criterion 8. All of this is agent work.

### [FAIL] The new load test has no CI gate

5.5 adds `test/load/test_309_time_bars_nfr.py`, gated by `MT_RUN_LOAD_TESTS=1` as the 308 file is. Nothing runs it in CI. `.github/workflows/ci.yml` has only a tag-triggered `publish` job, and `test_308_tick_bars_nfr.py` states "CI runs no test job; see slice 907". Task 10.1 runs the load tier by hand. Slice 907 (`user/slices/907-slice.ci-pipeline-and-load-test-gating.md`) exists for this, but 309 neither depends on it nor adds a wiring task. Add a CI wiring task that gates on the 309 file (and the 308 file it reuses), or declare the dependency on 907 and list what 309 must add to that gate.

### [CONCERN] Commit marker after task 4 sits on a broken tree

4.1 changes `read_provider_bars` to take a `BarSize`, and 4.5 changes `FuturesBarsResponse` and `from_plan`. The route code that builds the reader and the response (`api_server/routes/futures_series.py`, around line 131) is not updated until 5.1. The commit after 4 therefore has routes that no longer match their callees. Move the marker to the end of 5, or fold the route signature changes into 4.

### [CONCERN] Tasks 4 and 5 are too large for one commit each

Task 5 is one commit covering two routes with six error mappings, an executor change, the CLI and its writers, more than a dozen route tests, CLI tests, and a new load test. Task 4 covers the reader, admission across four settings files plus a `trades_to_scan` refactor, the ledger guard, and the response model. Split 5 into 5a routes and route tests, 5b CLI and CLI tests, and 5c load test (after 5a, since it exercises the routes). Split 4 into 4a reader and ledger guard, 4b admission and settings, and 4c response models, each green on its own.

### [CONCERN] An existing test that imports the removed enum is not listed

This file imports `TickBarGranularity` (line 18) and asserts its member values (line 137). 5.1 removes the enum, but no 5.x task names this file, so the commit at the end of 5 would fail its unit tier. The 7.1 grep would catch it, but only after that commit. Add it to 5.1 or 5.2 with the change to make: delete the assertion or replace it with a `parse_bar_size` test.

### [CONCERN] Task 3.1 builds on a module that 7.2 deletes

3.1 says to build the planned `Session` objects "as `parity_plan.parity_inputs` does". 7.2 deletes `data/tick/parity_plan.py`. A junior either copies that logic, which duplicates it against the no-duplication rule, or reuses it, in which case 7.2 must keep it. Choose one in 3.1: extract the session-building helper into a module that stays, and have `parity_inputs` call it, or let 7.2 remove `parity_inputs` and keep the helper.

### [CONCERN] The provisional trade-limit default can ship unmeasured

4.2 sets `API_MAX_TIME_BAR_TRADES` provisionally to `API_MAX_TICK_BAR_TRADES` (2,000,000). Design criterion 5 requires the default to be measured, "not guessed". If 6.2 writes `BLOCKED:`, the task stops, but nothing in 10.1 checks that the provisional value was replaced, so it can reach a release. Add a 10.1 check that the default equals the value recorded in `user/notes/309-time-bar-measurement.md`, and make the release wait on it.

### [CONCERN] Issue #40 is closed in the design and deferred in the task

The design lists "#40 (1-second bars, answered here)" among the issues closed in this slice. 9.2 keeps #40 open until Phase 7. Waiting until the release serves 1-second bars is sound, but the two documents disagree. Amend the design's list, or record the deviation in its cleanup section.

### [CONCERN] The load-test size does not match its bar count

5.5 says "a product `15s` request (about 82,000 bars)" on the busiest session. An ES session is about 23 hours (82,800 s), so one session at 15 s is about 5,520 bars. The 82,000 figure matches 1-second bars, which is what the design's rationale describes ("a 1-second request returns tens of thousands of rows"). Change the size to `1s`, or correct the count.

### [CONCERN] The same behaviour is tested at several layers

4.4 tests the limit below the scan, the `stop` before the second window, and the ledger guard at reader level. 5.2 tests the 422 over the limit, the 499 and the 500 again at route level, and 5.4 repeats some CLI cases. Keep the logic tests at reader level and have the route tests assert only the HTTP mapping, one test per status code. Task 2.2 also repeats the 1-minute comparison at 15s, 5m, 1h and 4h. Criterion 2 needs 1 minute only, and 4.4 and 5.2 already cover other sizes, so keep one non-1-minute size and rely on the `1d` total and `trade_count` checks for the rest.

### [NOTE] Reuse the existing Python bucketing oracle

2.2 says the test "owns its own" Python bucket computation. `parity_seed.tick_minutes` already computes that for a window, independently of `src`, and the existing parity tests use it. Reuse it at 1 minute, and generalise it to a size parameter rather than writing a second copy. Criterion 2's fixture is already real: `parity_seed.py:1-9` says the cut tbbo day is ingested for real. Because 2.2 keeps using it, 7.2's "remove anything with no user left" will not remove it.

### [NOTE] The new measurement note needs frontmatter

6.1 creates `user/notes/309-time-bar-measurement.md`. AGENTS.md requires YAML frontmatter on every markdown file, and the task does not mention it. Add "with frontmatter per `file-naming-conventions`".

### [NOTE] Some steps over-specify, and one assertion is left open

Several steps dictate exact signatures and messages the design does not require: `TIME_BAR_CUT_COLUMNS`, `TimeBarRead(...)`, the `SourceRun` fields, and the 422 text. The documentation line ranges in 8.1 ("about lines 327-373 …") will drift, so have the implementer grep instead. In 4.4, the check that `tick_trade` is never queried is left as "a stub `read`/spy … or by checking no rows were collected". Name the spy, since that is the assertion the criterion needs.

### Run Digest

- Response length: 10384 chars
- Response is newline-free: no
- Tool calls made: 17
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: xhigh
- Turns: 18
- Tokens — prompt / cached / cache write / completion / reasoning: 815373 / 653351 / 162008 / 48142 / not computed
- Duration: 216.4 s
- Cost: $0.20 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 14
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 14
- Finding-shaped matches — surviving validation: 14
