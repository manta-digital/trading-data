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
reviewedSha: b2d2ca014392e47a2e93d0d0de485be039e5f84c
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 19
effort: xhigh
turns: 20
promptTokens: 845442
cachedTokens: 689554
cacheWriteTokens: 155874
completionTokens: 54909
durationSeconds: 238.7
costUsd: 0.316245
costBasis: provider
runId: run-20261119-p5-94a32f17
squadronVersion: 0.24.34
findings:
  - id: F001
    severity: fail
    category: sequencing
    summary: "Cutover re-run contract contradicts its own abort rule"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:237-245"
  - id: F002
    severity: concern
    category: consistency
    summary: "2027–2028 closures have no defined bucket, so the 4.3 pin can fail"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:173"
  - id: F003
    severity: concern
    category: sequencing
    summary: "Section 2 depends on a module that section 4 creates"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:119-123"
  - id: F004
    severity: concern
    category: coverage
    summary: "The load test has no CI wiring task"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:427-429"
  - id: F005
    severity: concern
    category: process
    summary: "Slice commits typed `fix:` route through the fix workflow"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:200"
  - id: F006
    severity: concern
    category: coverage
    summary: "Criterion 11 can be met by \"not established\""
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:95-98"
  - id: F007
    severity: concern
    category: format
    summary: "Handoff items in the task file lack checkboxes"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:465-477"
  - id: F008
    severity: note
    category: format
    summary: "Two commit subjects exceed the ~72-character guideline"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:247"
  - id: F009
    severity: note
    category: sizing
    summary: "The file is large, but one file is justified"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:1"
  - id: F010
    severity: note
    category: process
    summary: "Commit markers sit at tested points"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:104"
  - id: F011
    severity: pass
    category: coverage
    summary: "Every success criterion and Included item has a task"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:263-275"
  - id: F012
    severity: pass
    category: scope
    summary: "No scope creep found"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:65-72"
  - id: F013
    severity: pass
    category: sequencing
    summary: "Test tasks follow their implementation tasks"
    location: "project-documents/user/tasks/174-tasks.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:132"
  - id: F014
    severity: pass
    category: consistency
    summary: "Referenced templates and code paths exist"
    location: "src/manta_trading/market/schema/migrations/minute.py:1047"
  - id: F015
    severity: pass
    category: sequencing
    summary: "Dependency order holds"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:324-335"
---

# Review: tasks — slice 174

**Verdict:** FAIL
**Model:** claude-haiku-5-5

## Findings

### [FAIL] Cutover re-run contract contradicts its own abort rule

Step 6.1 says to stop with an error if the plan differs from `EXPECTED_SESSION_CHANGES`. The same step says that after the first run the plan is empty and a re-run reports "nothing to change". An empty plan differs from the pinned list, so a re-run hits the abort instead. Test 6.2 requires the second-run report, so it cannot pass as written.

The migration can also be applied by the generic `mt data migrate apply` command (`src/manta_trading/cli/commands/data.py:259`) without the cutover. That leaves the migration applied and the gaps unreconciled, and the cutover's dry check then sees an empty plan.

Fix: state the idempotency condition explicitly. Abort on a plan mismatch only while the 174 migration is still pending. Once it is applied, verify the gap reconciliation (or report "nothing to change") instead of aborting. Also say how the generic migrate path is kept from applying this migration without reconciling gaps.

### [CONCERN] 2027–2028 closures have no defined bucket, so the 4.3 pin can fail

4.1 puts "dates past the current horizon" in `appended`, and 2027–2028 are past the horizon. But 2.1 (lines 119–123) defines `EXPECTED_SESSION_CHANGES` to include "each 2027-2028 closure and early close", and 4.3 (lines 192–193) requires the plan's `opened`, `closed` and `retimed` to equal it. As worded, those closures land in `appended`, so the pin fails and the 6.1 abort fires on every run.

The slice design treats these as dates that "stop being a session" (design lines 193–194), which means existing rows that move to `closed`. State that, and say which bucket a date with no existing row goes to.

Separately, criterion 10 says "2027 and 2028 holidays are not" sessions (design line 274), but 4.3 (line 194) checks only 2027-01-01. Assert every 2027–2028 closure from the pinned list.

### [CONCERN] Section 2 depends on a module that section 4 creates

2.1 places `EXPECTED_SESSION_CHANGES` in `nyse_session_plan.py` "(section 4)". Test 2.2 (lines 138–139) checks the table against that constant. But 4.1 (lines 169–170) is the step that creates the module. Section 2's tests cannot run in the order written.

Define the constant in section 2 in its own module, or move the transcription and its test into 4.1.

### [CONCERN] The load test has no CI wiring task

The design restates an NFR (p95 under 2 s, and `/health` p95 under 0.5 s; design lines 281–283), and 13.1 adds the load test. The project rule requires a CI wiring task to gate it. Section 13.1 defers the gate to #32 and slice 907.

The stated reason is accurate: `.github/workflows/ci.yml` has only a tag-triggered publish job and runs no tests. The 309 template (`test/load/test_309_time_bars_nfr.py:16-19`) uses the same deferral. So no task in this breakdown gates the NFR.

Add a CI wiring task here: a workflow job or gating script that runs `MT_RUN_LOAD_TESTS=1` on a runner that can reach the test cluster.

### [CONCERN] Slice commits typed `fix:` route through the fix workflow

The commits at lines 200 (section 4), 247 (section 6) and 274 (section 7) are typed `fix:`. Project rules say work committed as `fix:` follows the fix path: it commits to the target with no slice branch, and multi-step fixes go in the standing fixes task file. This breakdown runs them on the slice branch.

The design folds #26 and #28 into this slice (design lines 23–24), so the breakdown must choose. Either type those commits `feat:` or `refactor:` as slice work, or move the #26 and #28 pieces to a standing-fixes task group.

### [CONCERN] Criterion 11 can be met by "not established"

Criterion 11 (design line 275) requires the note to state what the close-stamp bar holds, with the evidence. It also requires both API references to describe the bar and its regular-session volume effect. Task 1.1 allows the finding to be "not established" with what was ruled out. If that happens, criterion 11 is not met, and 14.1 (lines 437–439) has no answer to document.

Either restate criterion 11 with the design owner, or define what 14.1 writes in the inconclusive case and leave criterion 11 open in 15.1.

### [CONCERN] Handoff items in the task file lack checkboxes

The Phase 7 handoff uses bullets with no `[ ]`. Document conventions require a checkbox on every item in a task file. The release step is gated on merge and code review, which fits the rule, but it still sits in the task file as a release step.

Add checkboxes, or move the handoff out of the task file.

### [NOTE] Two commit subjects exceed the ~72-character guideline

Line 247 is about 90 characters and line 389 about 86. Shorten both when the commit types are settled (see the `fix:` finding).

### [NOTE] The file is large, but one file is justified

The file has 124 checkboxes across 15 sections. The sections depend on one another in sequence (calendar, adjustment, SQL, admission, route, docs). The design has 11 criteria and 9 decisions, so one file is proportionate. No split is needed.

Trim the overlaps. 3.2 and 4.3 both assert extended bounds (lines 157–162 and 194–195). 5.2 and 6.2 both assert gap reconciliation (lines 221–228 and 243–245). Keep the cutover test to cutover behavior.

### [NOTE] Commit markers sit at tested points

There are ten markers, each after a tested unit. Sections 2–4, 5–6, 8–9 and 10–11 batch correctly, with explicit "No commit here" notes. No action needed.

### [PASS] Every success criterion and Included item has a task

Criteria 1–7 map to 9.3 and 7.2. Criterion 8 maps to 12.3. Criterion 9 maps to 11.2 and 12.3. Criteria 10 and 11 map to 4.3, 5.2, 1.1 and 14.1. Included items 1–6 map to sections 2–6, 7, 8–9, 11–12 and 14. Decisions D1–D9 each have a corresponding task. The gaps are in the findings above.

### [PASS] No scope creep found

The Excluded items (daily and wider session granularities, the daily-cagg half of #28, `closing_print`, new caggs, new granularity sizes, changing the default) have no tasks. The 422 refusals for `1d`/`1w`/`1mo`/`1q` are in D6 and in the Included API list, so they are in scope.

### [PASS] Test tasks follow their implementation tasks

Each implementation block is followed by its tests: 2.1/2.2, 3.1/3.2, 4.2/4.3, 5.1/5.2, 6.1/6.2, 7.1/7.2, 8.1/8.2, 9.1–9.2/9.3, 11.1/11.2 and 12.1–12.2/12.3.

### [PASS] Referenced templates and code paths exist

Verified: `MINUTE_MIGRATIONS` (minute.py:1047), `test/load/test_309_time_bars_nfr.py`, `scripts/cutover_301_calendar_history.py`, `project-documents/user/notes/309-time-bar-measurement.md`, and the `140-arch` and `180-arch` architecture files. The task's `test/load/` matches this repo's convention. The rule's `tests/load/` is the generic form.

### [PASS] Dependency order holds

The task order matches the design's Development Approach. The #78 investigation runs first (1.1). The migration (4) precedes gap reconciliation (5) and the cutover (6). Section 7 is independent. Section 9 follows 3, 7 and 8. Measurement (10) precedes admission (11), and the route (12) precedes the load test (13). The BLOCKED stop lists are consistent with these dependencies.

### Run Digest

- Response length: 10438 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 845442 / 689554 / 155874 / 54909 / not computed
- Duration: 238.7 s
- Cost: $0.32 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 15
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 15
- Finding-shaped matches — surviving validation: 15
