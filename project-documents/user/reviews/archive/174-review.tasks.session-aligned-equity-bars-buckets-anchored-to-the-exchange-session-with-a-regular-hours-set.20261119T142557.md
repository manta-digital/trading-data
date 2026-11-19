---
docType: review
layer: project
reviewType: tasks
slice: session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: FAIL
verdictSource: stated
sourceDocument: project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md
aiModel: claude-haiku-5-5
aiProfile: sdk
status: complete
dateCreated: 20261119
dateUpdated: 20261119
reviewedSha: fedb2eacf86f568df4dca8028d8b84652d980d04
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 19
effort: xhigh
turns: 20
promptTokens: 573565
cachedTokens: 403379
cacheWriteTokens: 170176
completionTokens: 66637
durationSeconds: 288.6
costUsd: 0.343021
costBasis: provider
runId: run-20261119-p5-94a32f17
squadronVersion: 0.24.34
findings:
  - id: F001
    severity: fail
    category: ci-gating
    summary: "Load test has no CI gate and no task adds one"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:395-404"
  - id: F002
    severity: concern
    category: coverage
    summary: "2027–2028 holiday rows have no independent check, and the migration pin is derived from them"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:108-120"
  - id: F003
    severity: concern
    category: coverage
    summary: "Bucket assignment has no unit test"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:274-289"
  - id: F004
    severity: concern
    category: test-design
    summary: "The summer case in the adjustment test cannot catch the defect"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:253-254"
  - id: F005
    severity: concern
    category: coverage
    summary: "Intraday utc 1h and 4h buckets change under D5, and nothing tests that"
    location: "src/manta_trading/market/timescale_minute_db.py:295-298"
  - id: F006
    severity: concern
    category: consistency
    summary: "The 10.1 ceiling and the 13.1 load test use different latency bars"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:332-334"
  - id: F007
    severity: concern
    category: scope
    summary: "The task adds a `limit` refusal the design rules out"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:359-362"
  - id: F008
    severity: concern
    category: commit-cadence
    summary: "Commit markers sit after nearly every top-level task"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md"
  - id: F009
    severity: note
    category: sequencing
    summary: "BLOCKED scope in 10.1 contradicts the standing rule"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:332-334"
  - id: F010
    severity: note
    category: coverage
    summary: "Criterion 5 asks for a symbol sample, and 9.3 tests one fixture"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:310-312"
  - id: F011
    severity: note
    category: completeness
    summary: "Seven existing `adjusted()` test calls break once `dates_in` is required"
    location: "test/unit/data/adjustment/test_adjusted.py:97"
  - id: F012
    severity: note
    category: completeness
    summary: "Load test file and template are not named"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:397-398"
  - id: F013
    severity: note
    category: consistency
    summary: "Phase 7 names \"trading-ui\" where the design names \"mt-ui\""
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:447"
  - id: F014
    severity: note
    category: formatting
    summary: "Numbered migration steps under 4.2 have no checkboxes"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:169-177"
---

# Review: tasks — slice 174

**Verdict:** FAIL
**Model:** claude-haiku-5-5

## Findings

### [FAIL] Load test has no CI gate and no task adds one

Section 13 adds a load test (13.1: one-year regular 1h read p95 under 2 s, `/health` p95 under 0.5 s), but nothing gates it. `.github/workflows/ci.yml` has one job, `publish`, triggered only by `v*` tags. It runs no pytest, ruff, mypy or load tier. The existing `test/load/` files are not gated either. The rules require a CI wiring task whenever a load test task exists. The `python.md` rule also requires CI to gate load tests for slices touching network paths, and the bars endpoint is one. Add a task after 13.1 that creates a CI job running the 174 load test and failing on the NFR bounds. The CI job has no database service today, so the task must also say which TimescaleDB instance the gate uses.

### [CONCERN] 2027–2028 holiday rows have no independent check, and the migration pin is derived from them

The comparison against `generate_holidays` stops at 2026 (2.1, line 116). Criterion 10 and the #26 deadline depend on 2027–2028 rows, and for those years 2.2 only asserts that closures exist. The pinned plan in 4.3 (lines 180–183) takes its 2027–2028 closures and early-close retimes from the section 2 table, and the 6.1 guard (lines 227–229) compares the plan with that same list. A wrong 2027–2028 row therefore passes both checks. The design requires a test that pins the date list. The source step also has no failure path. NYSE's schedule for 2028 may not be published as of 2026-11-19, and the task does not say what to do if the source cannot be fetched. An agent could then write rows from memory. Add a BLOCKED path for an unreachable or unpublished source, and pin 2027–2028 against literal dates from the source, not against the table under test.

### [CONCERN] Bucket assignment has no unit test

The Technical Requirements call for unit tests of "phase and bucket assignment (full day, early close, DST)". Task 8.2 tests phase intervals and bar and minute counts only. The bucket formula `anchor + floor((time - anchor) / width) * width` lives in the SQL of 9.1 and is covered only by the 9.3 integration tests, which need a throwaway database. Put the bucket arithmetic in a pure function in `session_phases.py` and unit-test it at full-day, early-close and DST boundaries. The SQL parity test in 9.3 then checks the SQL against that function.

### [CONCERN] The summer case in the adjustment test cannot catch the defect

7.2 says a summer 23:30Z minute should take the previous day's factor. At 23:30Z in EDT the minute is 19:30 on the same calendar date in both UTC and ET, so the old UTC-date rule and the new exchange-date rule agree. The test passes on the unfixed code. In summer the defect only affects the 20:00 EDT close stamp, which is 00:00Z on the next UTC date. Replace the summer case with that close stamp and confirm it fails before the fix. The winter 00:30Z case is correct.

### [CONCERN] Intraday utc 1h and 4h buckets change under D5, and nothing tests that

The `adjusted_fn` call at line 295 runs after both the minute branch and the `_get_aggregated_data` branch. So the D5 change reaches utc 1h and 4h buckets, as the design says. The 7.2 integration test covers only 1m. The 9.3 criterion 7 test covers only `session=extended` 1m. The 12.3 test checks that the body "equals today's", which does not detect the changed values. The CHANGELOG entry in 14.3 will describe a change no test pins. Add an integration case: in winter the utc 1h bucket at 00:00Z holds exactly the 19:00–19:59 ET minutes, so its adjusted value must use the previous session's factor.

### [CONCERN] The 10.1 ceiling and the 13.1 load test use different latency bars

10.1 sets the ceiling at the largest minute count whose p95 is within 5 s. 13.1 requires the one-year regular 1h read to have p95 under 2 s. A one-year regular read is about 98,000 minutes (design D1). If the measured ceiling is at or above that count and the p95 falls between 2 s and 5 s, the ceiling admits the read and 13.1 fails. If the ceiling is below that count, admission refuses the read with `minute_ceiling`, so 13.1 cannot pass through the route. The task also does not say whether 13.1 calls `get_session_bars` directly or the HTTP route (lines 397–402). Pick one bound for both, and state the entry point 13.1 uses.

### [CONCERN] The task adds a `limit` refusal the design rules out

11.1 adds a refusal (422 `minute_ceiling`) when `limit × width_minutes` exceeds the minute ceiling. The task says the design leaves this case open, but design D6 (slice design line 181) says `limit` works unchanged. No success criterion pins a `limit` refusal. Under this rule, a `limit=N` request that clients get today can start failing. Ask the PM to decide, and record the decision in the design. Do not add it silently to the task.

### [CONCERN] Commit markers sit after nearly every top-level task

There are 14 markers across 15 top-level sections, and only section 3 has none. That is the "after every task" pattern the commit rules reject. Every marker sits at a tested point, so the fix is to merge, not move. Suggested grouping, about 10 commits: 2 into 3+4 (section 2 changes no runtime behaviour until the 4.2 migration runs, so its `fix:` message describes a change that lands at 4); 6 into 5; 8 into 9; 10 into 11; keep 1, 3+4, 7, 9, 12, 13, 14 and 15 as they are.

### [NOTE] BLOCKED scope in 10.1 contradicts the standing rule

"Stop before task 11.1" reads as stopping all work. The dependent tasks are 11, 12.2–12.3, 13 and the measured numbers in 14.1. The independent ones are 12.1 (models), 14.2, 14.3 and the rest. Name them explicitly: "BLOCKED: stop tasks 11, 12.2–12.3 and 13; continue everything else."

### [NOTE] Criterion 5 asks for a symbol sample, and 9.3 tests one fixture

Design criterion 5 (slice design line 269) requires the aggregate check to "hold for a sample of symbols, sizes and both modes." 9.3 names sizes and modes only. Add at least two symbols to the fixture, one of them with off-tape marks.

### [NOTE] Seven existing `adjusted()` test calls break once `dates_in` is required

The existing calls at lines 97, 116, 127, 139, 152, 164 and 176 all omit the new keyword, so a required `dates_in` breaks every one of them. The 7.1 caller list is otherwise complete: the only src callers are `timescale_minute_db.py:298` and `timescale_daily_db.py`. Tell 7.1 to update these seven calls.

### [NOTE] Load test file and template are not named

13.1 says "follow the existing bars load tests" but names no file. The repo has `test/load/test_309_time_bars_nfr.py` and `test/load/test_187_api_nfr.py`. Name the new file on the existing `test_<slice>_<topic>_nfr.py` pattern, for example `test/load/test_174_session_bars_nfr.py`, and name the template.

### [NOTE] Phase 7 names "trading-ui" where the design names "mt-ui"

The design uses "mt-ui" at lines 32, 251 and 291. The handoff uses "trading-ui". I cannot confirm the peer name from here. Confirm it with `ListAgents` at handoff rather than guessing.

### [NOTE] Numbered migration steps under 4.2 have no checkboxes

CLAUDE.md requires a `[ ]` on every item and subitem. Steps 1–5 under 4.2 are plain numbers. Make them checkbox subitems so each step can be checked off.

### Run Digest

- Response length: 10901 chars
- Response is newline-free: no
- Tool calls made: 19
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: xhigh
- Turns: 20
- Tokens — prompt / cached / cache write / completion / reasoning: 573565 / 403379 / 170176 / 66637 / not computed
- Duration: 288.6 s
- Cost: $0.34 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 14
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 14
- Finding-shaped matches — surviving validation: 14
