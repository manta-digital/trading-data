---
docType: review
layer: project
reviewType: tasks
slice: tick-bars-and-compact-tick-serving
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md
aiModel: claude-haiku-5-5
aiProfile: sdk
status: complete
dateCreated: 20261111
dateUpdated: 20261111
reviewedSha: 1ad2dfed5f1a02c39f5b1b4e06cfe4334285860b
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 35
effort: xhigh
turns: 36
promptTokens: 1241077
cachedTokens: 1051414
cacheWriteTokens: 189645
completionTokens: 71013
durationSeconds: 308.7
costUsd: 0.406349
costBasis: provider
runId: run-20261111-p5-c058f9f9
squadronVersion: 0.24.26
findings:
  - id: F001
    severity: concern
    category: completability
    summary: "Task 3.2 leaves the batch loop and the `until` stop unresolved"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:203-216"
  - id: F002
    severity: concern
    category: sequencing
    summary: "Commit markers follow every top-level task; task 4 is not a working point"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:312"
  - id: F003
    severity: concern
    category: completability
    summary: "Task 7.3 names an operator runbook that its own grep cannot find"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:476-479"
  - id: F004
    severity: concern
    category: completability
    summary: "\"Known flakes\" and \"pre-existing failures\" references point at nothing"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:449-451"
  - id: F005
    severity: concern
    category: consistency
    summary: "7.7 scopes mypy to \"src kalshi paths\", contradicting line 58"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:499"
  - id: F006
    severity: note
    category: coverage
    summary: "Criterion 12 is deferred to Phase 7 with no tracking home"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md:363"
  - id: F007
    severity: note
    category: completability
    summary: "Task 3.7 does not name the constant its batch-count test patches"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:265-266"
  - id: F008
    severity: note
    category: sequencing
    summary: "Reader tests sit after unrelated subtasks"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:248-274"
  - id: F009
    severity: note
    category: proportionality
    summary: "Some test items go beyond what the criteria need"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:91-95"
  - id: F010
    severity: note
    category: completability
    summary: "Criterion 8's grep check cannot fail on what the criterion forbids"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:502-503"
  - id: F011
    severity: pass
    category: coverage
    summary: "Prerequisites and named symbols exist"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:67-75"
  - id: F012
    severity: pass
    category: coverage
    summary: "Success criteria trace to tasks; no scope creep; no cycles"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md:346-363"
---

# Review: tasks — slice 308

**Verdict:** CONCERNS
**Model:** claude-haiku-5-5

## Findings

### [CONCERN] Task 3.2 leaves the batch loop and the `until` stop unresolved

The task says to batch through `tick_reads.stream_rows` if it can, and otherwise to add a thin loop. It cannot. `OnBatch` is synchronous (`src/manta_trading/data/tick/tick_reads.py:63`), and `stream_rows` (`tick_reads.py:107-117`) calls `on_batch` inline, with no stop check and no await point. D3 and D10 need an awaited `stop()` before each fetch and an awaited executor push, so the loop is required, not optional. Decide it in the task: either add a loop in `tick_bar_reads.py` that copies `stream_rows`' cursor and fetch loop, or generalise `stream_rows` to take an async handler and a stop hook, and update `read_ticks`. The task also does not say how the `until` stop works. The batch function must signal stop before pushing a trade that would open a bar (no bar open) with `ts_event >= until`. Without that, 3.7's "stops fetching" test has no way to pass. Output stays correct because the `open_time` filter drops late bars, so this is an early-exit gap, not a wrong-result gap.

### [CONCERN] Commit markers follow every top-level task; task 4 is not a working point

The markers sit at lines 97, 180, 280, 312, 387, 453 and 506: one per top-level task. The rule is markers at working, tested points, not after every task. Task 4 is the weak one. Its models and `TicksPerBarParam` have no consumer until task 5 wires the routes, and task 4 has no format, lint or mypy step, unlike tasks 2, 3, 5, 6 and 7. The design's Development Approach (slice lines 414-420) has five steps and puts routes and models together as step 3. Remove the marker at line 312 and let the routes commit (after 5.7) cover tasks 4 and 5. Task 1's marker can stay, since the refactor is its own semantic commit.

### [CONCERN] Task 7.3 names an operator runbook that its own grep cannot find

7.3 says to document `MT_API_MAX_TICK_BAR_TRADES` beside `MT_API_MAX_BARS_PER_REQUEST` in "the operator runbook", and to locate it with `grep -rn MT_API_MAX_BARS_PER_REQUEST docs project-documents/user`. The runbooks folder (`project-documents/user/runbooks/*.md`, eight files) does not contain that variable. The grep's paths return no runbook. The places that do document the variable are `README.md:167` and `README.md:818`, which the grep does not search, and `docs/api/reference.md:158-159`. Name the file the design means and the anchor. Otherwise a junior will invent a runbook section.

### [CONCERN] "Known flakes" and "pre-existing failures" references point at nothing

6.6 says "Known flakes: see the integration-tier notes". No such notes exist in the repo. 7.7 (line 501) says "fail only on the documented pre-existing failures" and names no list. The 306 review flagged the same pattern (`project-documents/user/reviews/306-review.tasks.serving-the-new-tiers.md:137`). Name the failing tests in the task, or drop the reference. 6.6's "before investigating" also conflicts with the debugging rule, which requires the actual error before any fix. Re-running in isolation can classify a failure, but it does not replace reading its error.

### [CONCERN] 7.7 scopes mypy to "src kalshi paths", contradicting line 58

Lines 58-59 say to run mypy "with the src paths and tests in one invocation". Line 499 says "mypy (src kalshi paths and tests in one invocation)". The kalshi reference looks copied from another task file. A junior following 7.7 would scope mypy to a kalshi path and skip `data/tick` and `api_server`. Change it to match lines 58-59.

### [NOTE] Criterion 12 is deferred to Phase 7 with no tracking home

Criterion 12 requires the Phase 7 measurement, recorded with its ledger sum in `user/notes/308-tick-bar-measurement.md`. The task file says it is "not a task in this file" (lines 50-51), and nothing in the task file tracks it. Deferring it is right, because Phase 6 cannot run it. Name the Phase 7 step that owns it so it does not drop out at close-out. Walkthrough steps 5-6 (slice lines 393-407) depend on it.

### [NOTE] Task 3.7 does not name the constant its batch-count test patches

3.7 says to count batches "with a small `TICK_GET_FETCH_ROWS` monkeypatch" without naming the module attribute. The existing tests patch `tick_reads.TICK_GET_FETCH_ROWS` (`test/integration/data/test_tick_reads.py:98`, `test/integration/api_server/test_futures_series.py:149`). If the new loop imports the constant by name, as `tick_reads.py:24` does, a patch on `tick_reads` does nothing, and the early-stop assertion fails with no clear cause. Name the attribute once F1 settles where the loop lives.

### [NOTE] Reader tests sit after unrelated subtasks

3.6 tests the hook from 3.3, and 3.7 tests the reader from 3.2, but both sit after 3.3 through 3.5. They are inside one group, so this is minor. Move each test subtask to sit after the subtask it tests, or accept the slip.

### [NOTE] Some test items go beyond what the criteria need

1.4 asserts that `UNDEF_INT64` is the same object in both modules and that the side codes are `B` and `A`. No criterion needs that, and 1.3's grep already shows the value is defined once. Cut it or fold it into 2.5. The second bullet of 2.5 (lines 173-174) adds an AST walk for the same criterion 8 check as the subprocess test. `test/unit/data/tick/test_import_boundary.py` already runs that walk, so add `tick_bars` there as a case. 5.6's disconnect check (lines 380-381) repeats 3.7's pool assertion, so keep only what is route-specific: no response and no ERROR log. The msgpack-versus-JSON bar parity in 5.3 (line 358) is not required by criterion 4 or 7, so keep it only if it is cheap. The file's length is otherwise proportionate to the slice.

### [NOTE] Criterion 8's grep check cannot fail on what the criterion forbids

`grep -rn "GROUP BY.*ticks_per_bar\|row_number()" src` cannot catch the SQL that criterion 8 forbids. The first alternative can never match, because SQL does not contain `ticks_per_bar`. The second matches nothing in `src` today, so the check passes whatever the reader's SQL contains. Use a check on the one statement from 3.1: `grep -n "GROUP BY\|row_number\|OVER (" src/manta_trading/data/tick/tick_bar_reads.py` must return nothing.

### [PASS] Prerequisites and named symbols exist

306 is merged (`.git/logs/HEAD`: "merge 306-slice.serving-the-new-tiers: Fast-forward"). `verdicts_for` exists (`src/manta_trading/data/tick/tick_status_scope.py:53`). `contract_plan` and `product_plan` take `family` (`get_plan.py:233-240`, `280-287`). `stream_rows` is already public (`tick_reads.py:107`), so the rename listed under Interfaces (slice line 80) is done, as 1.1 says. `UNDEF_INT64` is defined once (`constants.py:292`), used at 302-303. `TickRecord` is at `models/futures.py:95`. `admission.over_ceiling` exists (`admission.py:58`). `tick_support` (`seed_ticks`, `client_for`, `seed_september_roll`) exists under `test/tick_support/`. The trades twin fixture is on disk, so 2.1's create branch is not needed. The architecture already states the partial rule (`300-arch...md:78`), so no task must amend it.

### [PASS] Success criteria trace to tasks; no scope creep; no cycles

Criteria map to tasks as follows: 1–2 → 2.4, 5.4; 3 → 2.4, 5.6; 4 → 4.3, 5.3, 5.4; 5 → 3.7, 5.3; 6 → 3.7, 5.5; 7 → 6.3, 6.5; 8 → 2.5, 7.7; 9 → the run steps (2.6, 3.8, 5.7, 6.6, 7.7); 10 → 7.1–7.5; 11 → 7.6. Decisions D1–D8 and D10 each have a build step. D9's production check is the Phase 7 gap in the criterion-12 note. Task content stays inside the Excluded list: no CLI, purchase, migration, #72 rename, or merge or push steps. Dependencies run 1→2→3→4→5→6→7 with no cycles.

### Run Digest

- Response length: 10799 chars
- Response is newline-free: no
- Tool calls made: 35
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: xhigh
- Turns: 36
- Tokens — prompt / cached / cache write / completion / reasoning: 1241077 / 1051414 / 189645 / 71013 / not computed
- Duration: 308.7 s
- Cost: $0.41 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 12
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 12
- Finding-shaped matches — surviving validation: 12
