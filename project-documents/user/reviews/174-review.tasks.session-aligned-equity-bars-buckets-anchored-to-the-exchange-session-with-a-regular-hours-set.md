---
docType: review
layer: project
reviewType: tasks
slice: session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md
aiModel: claude-haiku-5-5
aiProfile: sdk
status: complete
dateCreated: 20261119
dateUpdated: 20261119
reviewedSha: f94c4219c17f5e8a6ae2380df37eddecabb4d769
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 15
effort: xhigh
turns: 16
promptTokens: 559216
cachedTokens: 403598
cacheWriteTokens: 155608
completionTokens: 51932
durationSeconds: 226.3
costUsd: 0.29423
costBasis: provider
runId: run-20261119-p5-94a32f17
squadronVersion: 0.24.34
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria and technical requirements map to tasks"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:74-491"
  - id: F002
    severity: pass
    category: coverage
    summary: "Repo references check out"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:44-72"
  - id: F003
    severity: pass
    category: sequencing
    summary: "Test-with pattern and commit markers"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:106"
  - id: F004
    severity: concern
    category: scope-creep
    summary: "`limit` reads can scan the whole held history, outside the minute ceiling"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:401-402"
  - id: F005
    severity: concern
    category: coverage
    summary: "Load-test NFR has no CI gate task"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:446-449"
  - id: F006
    severity: concern
    category: decision-gate
    summary: "Criterion 11 can end unmet with no decision owner"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:96-100"
  - id: F007
    severity: concern
    category: coverage
    summary: "Criterion 5 omits 1m; the walkthrough depends on 1m session reads"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:348-349"
  - id: F008
    severity: concern
    category: testing
    summary: "The America/Denver session-timezone hazard has no test"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:63-65"
  - id: F009
    severity: concern
    category: sizing
    summary: "Section 9 is too large for one commit unit"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:320-360"
  - id: F010
    severity: concern
    category: scope-creep
    summary: "`--dry-run` is untested scope the design does not ask for"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:258-264"
  - id: F011
    severity: concern
    category: sequencing
    summary: "Phase 7 does not name the cutover command or its order"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:486-491"
  - id: F012
    severity: note
    category: sequencing
    summary: "Section 2 is one live fetch that gates most of the slice"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:115-120"
  - id: F013
    severity: note
    category: sizing
    summary: "Section 5.1 prescribes internals the design does not require"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:213-231"
  - id: F014
    severity: note
    category: docs
    summary: "Design and task disagree on the bar key"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:227"
  - id: F015
    severity: note
    category: decision-gate
    summary: "10.1 has no rule for timed-out measurements"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:369-377"
  - id: F016
    severity: note
    category: coverage
    summary: "Integration Requirements are verified only in Phase 7"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:290"
---

# Review: tasks — slice 174

**Verdict:** CONCERNS
**Model:** claude-haiku-5-5

## Findings

### [PASS] Success criteria and technical requirements map to tasks

Criteria 1-4 and 6 map to 9.3; 5 maps to 9.3 (5m-4h only, see the criterion 5 finding); 7 maps to 7.2 and 9.3; 8 maps to 12.3; 9 maps to 11.2 and 12.3; 10 maps to 4.3 and 5.2; 11 maps to 1.1, 14.1 and 15.1. Unit tests (8.2, 7.2, 11.2), the load test (13.1), ruff/mypy (15.1), openapi (14.1) and the 140/180 amendments (14.2) are all present. Excluded items (`closing_print`, session caggs, daily `session`, default change) are not built.

### [PASS] Repo references check out

Verified: `scripts/cutover_301_calendar_history.py`, `migrations/cme_calendar_history.py`, `test/load/test_309_time_bars_nfr.py` and `user/notes/309-time-bar-measurement.md` exist. The latest minute migration is `067_index_instruments_indx_symbols`, matching `projectState`. `test_adjusted.py` has exactly seven `adjusted(` calls, matching 7.1. The claim that CI runs no test job holds: `.github/workflows/ci.yml` has only a tag-triggered publish job.

### [PASS] Test-with pattern and commit markers

Each implementation section is followed by its tests. Commit markers are plain `**Commit**:` lines at ten working points. Sections 2-4 and 5-6 share a commit, which is justified because section 3 writes columns that section 4's migration adds.

### [CONCERN] `limit` reads can scan the whole held history, outside the minute ceiling

11.1 exempts `limit` reads from the minute ceiling and defers a minute bound to #82. The design's D6 applies the minute estimate to every session request, and its mitigation says admission covers minutes read. But `read_latest` (`src/manta_trading/api_server/latest_bars.py:108-114`) doubles the window until it has `limit` bars or reaches the first held date. On a sparse symbol that scans the full history with no minute check. D6's "limit works unchanged" line is the design's own justification, so the deferral is traceable, but the cost is unbounded and no criterion covers it. The PM should either add a minute check on the windows `read_latest` will scan, or accept the deferral in writing.

### [CONCERN] Load-test NFR has no CI gate task

The slice restates the NFR (p95 under 2 s; `/health` under 0.5 s) and 13.1 adds the load test. There is no CI-wiring task: 13.1 defers the gate to #32 and slice 907. Slice 907 exists and targets this exact gap, since `ci.yml` runs no test job. The deferral is explicit, not implicit, but the review rule requires a CI-wiring task to exist. Either add one, or state in the task that 907 must land before this slice ships.

### [CONCERN] Criterion 11 can end unmet with no decision owner

Criterion 11 requires the note to state what the close-stamp bar holds, with evidence. 1.1 allows a "not established" finding, and 15.1 then lists criterion 11 as unmet "under this task", with no stop and no owner. The design should settle now which outcome closes criterion 11: either the criterion accepts a documented "not established" with the evidence ruled out, or a not-established result marks criterion 11 BLOCKED.

### [CONCERN] Criterion 5 omits 1m; the walkthrough depends on 1m session reads

Criterion 5 says "sizes", but 9.3 lists only 5m, 15m, 1h and 4h. The design's walkthrough steps 5 and 6 use `1m&session=regular` and `1m&session=extended`, and step 6 is the only route-level check on criterion 6. No implementation test runs a 1m session read. Add 1m to the 9.3 size list and state which size the criterion 6 test uses.

### [CONCERN] The America/Denver session-timezone hazard has no test

The context summary and the design (slice design:342) both say production SQL must not cast a timestamp to a date, because production runs in America/Denver. No test runs the session statement under a non-UTC session timezone. A bare `::date` would pass the suite and fail in production. Add a 9.3 case that runs the statement under `SET TIME ZONE 'America/Denver'` and asserts session dates for a bar near UTC midnight.

### [CONCERN] Section 9 is too large for one commit unit

Section 9 covers a new SQL renderer, a refactor of existing eligible-rollup SQL (9.1 extracts a shared helper from `eligible_rollups_sql.py`), the read method, and six groups of integration criteria. It shares one commit (line 360) with section 8. Split it: give the helper extraction its own refactor step with a `**Commit**:` marker, so the refactor does not ride in a `feat` commit, and make the 9.3 integration checks a separate step from the renderer and method.

### [CONCERN] `--dry-run` is untested scope the design does not ask for

6.1 adds a `--dry-run` mode. The design requires the dry check to run first, which is already the first step of the single command. 6.2 tests four behaviors and not `--dry-run`. Either drop the flag, or add a test that a dry run writes nothing.

### [CONCERN] Phase 7 does not name the cutover command or its order

Criterion 10's gap reconciliation reaches production only when the cutover runs, and 4.2 says gap rows are reconciled "by the cutover." Phase 7 mentions "the cutover" without naming `scripts/cutover_174_calendar.py` or saying whether it runs before or after `mt data migrate apply`. 6.2 supports either order, so the handoff should state which one the release uses.

### [NOTE] Section 2 is one live fetch that gates most of the slice

A BLOCKED on an unreachable NYSE schedule stops sections 2-6 and 9-13, which is most of the slice. Making a reachability check the first step of section 2 would surface that block before the work that depends on it.

### [NOTE] Section 5.1 prescribes internals the design does not require

Preserving `last_attempt_outcome` is a behavior requirement, tested in 5.2. Mirroring `precomputed_ranges`, the advisory lock and `FetchStatus.UNKNOWN` is implementation detail that the design (D8: "reconcile through the existing gap writer") does not require. Keep the behavior requirement and drop the internal mechanics unless they are needed for correctness.

### [NOTE] Design and task disagree on the bar key

The design's JSON example and walkthrough use `time`. The served field is `timestamp` (`BarRecord`), which the task correctly keeps (task lines 66-67). Amend the design so a later reviewer does not flag the task as wrong.

### [NOTE] 10.1 has no rule for timed-out measurements

The serving timeout is 20 s, and the 36-month window will probably hit it. The task does not say how to record a cancelled run, and p95 is undefined for one. State that a cancelled run counts as over 5 s, so the largest passing window is well defined.

### [NOTE] Integration Requirements are verified only in Phase 7

The trading-engine curl check can only run after release, so deferring it to Phase 7 is correct. 15.1 walks success criteria 1-11 but not the Integration Requirements. That is acceptable as long as Phase 7 carries them, which walkthrough step 10 does.

### Run Digest

- Response length: 11005 chars
- Response is newline-free: no
- Tool calls made: 15
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: xhigh
- Turns: 16
- Tokens — prompt / cached / cache write / completion / reasoning: 559216 / 403598 / 155608 / 51932 / not computed
- Duration: 226.3 s
- Cost: $0.29 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 16
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 16
- Finding-shaped matches — surviving validation: 16

## Resolution

- **[pass] Success criteria and technical requirements map to tasks:** no change needed.
- **[pass] Repo references check out:** no change needed.
- **[pass] Test-with pattern and commit markers:** no change needed.
- **[concern] `limit` reads can scan the whole held history:** deferred, no change. Design D6 says `limit` works unchanged, and 11.1 names issue #82 for the minute bound on session `limit` reads. Accepting or reversing that is the PM's decision on #82.
- **[concern] Load-test NFR has no CI gate task:** rejected, no change. 13.1 defers the gate to issue #32 (slice 907), and #32 now names this test. GitHub-hosted runners cannot reach the test cluster. Making slice 907 a ship precondition would hold the #26 calendar fix, due before 2027-01-01, behind a slice that has not started.
- **[concern] Criterion 11 can end unmet with no decision owner:** changed. If the close-stamp finding is "not established", 15.1 writes `BLOCKED:` for criterion 11 with the evidence, so the PM decides whether it closes.
- **[concern] Criterion 5 omits 1m:** changed. 9.4 adds `1m` to the criterion 5 sizes, and the criterion 6 test reads `1m` with `extended`, as walkthrough step 6 does.
- **[concern] America/Denver session-timezone hazard has no test:** changed. 9.4 runs a session whose post-market minutes cross UTC midnight with the connection's time zone set to `America/Denver`, and asserts the same `session_date` on every bar as under UTC.
- **[concern] Section 9 is too large for one commit unit:** changed. The shared-expression extraction is now task 9.1, with its own `refactor:` commit and a byte-for-byte test that the rendered rollup SQL is unchanged. The statement, the read method and the integration tests are 9.2–9.4.
- **[concern] `--dry-run` is untested scope:** changed. The flag is dropped. The dry check stays the first step of the single command.
- **[concern] Phase 7 does not name the cutover command or its order:** changed. Phase 7 names `uv run python scripts/cutover_174_calendar.py vX.Y.Z` (the `/release` new-migration step). It applies the migration itself, so `mt data migrate apply` is not run first.
- **[note] Section 2 is one live fetch that gates most of the slice:** changed. Fetching the NYSE schedule, with its `BLOCKED:` path, is now the first item of 2.1.
- **[note] Section 5.1 prescribes internals:** kept, with the reason each is needed for correctness. Without `precomputed_ranges` the minute writer inserts one gap over the whole window, and the advisory lock is needed because the daemons may run at the same time.
- **[note] Design and task disagree on the bar key:** changed. The design's example JSON, the walkthrough `jq` and the short-bar line now say `timestamp`. The task's note about the mismatch is removed.
- **[note] 10.1 has no rule for timed-out measurements:** changed. A run cancelled by the statement timeout counts as over 5 s.
- **[note] Integration Requirements are verified only in Phase 7:** no change. As the finding says, the trading-engine check can only run after release, and walkthrough step 10 carries it.
