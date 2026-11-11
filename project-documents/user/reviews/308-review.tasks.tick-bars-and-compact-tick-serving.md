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
reviewedSha: cb8ca3108d57e430d674286534d5ce47b8621c70
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 33
effort: xhigh
turns: 34
promptTokens: 1532078
cachedTokens: 1351934
cacheWriteTokens: 180122
completionTokens: 72175
durationSeconds: 310.6
costUsd: 0.417261
costBasis: provider
runId: run-20261111-p5-c058f9f9
squadronVersion: 0.24.26
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria 1–11 each map to at least one task"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md:342-362"
  - id: F002
    severity: note
    category: coverage
    summary: "SC12 is completed only in Phase 7, outside this breakdown"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md:363"
  - id: F003
    severity: pass
    category: sequencing
    summary: "Sequencing and dependencies hold"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:76-84"
  - id: F004
    severity: pass
    category: accuracy
    summary: "Cited paths and symbols exist"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md"
  - id: F005
    severity: concern
    category: nfr-test
    summary: "Load-test instructions contradict each other"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:463-475"
  - id: F006
    severity: concern
    category: nfr-gating
    summary: "The new load test has no CI wiring"
    location: ".github/workflows/ci.yml"
  - id: F007
    severity: concern
    category: nfr-coverage
    summary: "The load test does not run at the trade limit that its bound is set against"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:467-469"
  - id: F008
    severity: concern
    category: design-consistency
    summary: "Subtask 5.2's delta rule contradicts D6 and the 5.3 decoder"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:367-369"
  - id: F009
    severity: concern
    category: design-drift
    summary: "The task adds exception types and a 499 response the design does not specify"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:317-322"
  - id: F010
    severity: concern
    category: commit-cadence
    summary: "Eight commit markers sit after all eight top-level sections"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md"
  - id: F011
    severity: concern
    category: scope
    summary: "Section 8 adds a measurement tool the design does not ask for"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:482-502"
  - id: F012
    severity: concern
    category: docs-coverage
    summary: "The operator runbook the slice names has no task, and the anchor it cites is absent"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md:328"
  - id: F013
    severity: note
    category: design-accuracy
    summary: "Stale statements in the slice"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md:80"
  - id: F014
    severity: note
    category: clarity
    summary: "Ambiguities a junior implementer could resolve differently"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:106-113"
  - id: F015
    severity: note
    category: granularity
    summary: "Subtask granularity and test placement"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:262-270"
  - id: F016
    severity: note
    category: verification
    summary: "The SC8 check covers two files only"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:448-450"
  - id: F017
    severity: note
    category: verification
    summary: "The compact-size claim has no check"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md:43"
---

# Review: tasks — slice 308

**Verdict:** CONCERNS
**Model:** claude-haiku-5-5

## Findings

### [PASS] Success criteria 1–11 each map to at least one task

SC1–3 map to 2.1–2.4 (oracle, cutter, `current`/`close_session`, errors). SC2's cross-window restart also maps to 4.6. SC4–6 map to 3.3–3.8 and 4.5–4.6, including 422 for N ≤ 0 and admission before any read. SC5 is tested in 3.4 and 4.6. SC7 maps to 5.1–5.5. SC8 maps to 2.5 and 6.6. SC9 is the per-commit gate plus 6.6. SC10–11 map to 6.1, 6.2, 6.4–6.6. The design's 10 s and 100 ms NFRs (D9) are covered by load test 7.1.

### [NOTE] SC12 is completed only in Phase 7, outside this breakdown

Task 8 builds the script. Nothing here runs it or writes `user/notes/308-tick-bar-measurement.md`. The design puts that run after release, and the Context Summary (task file lines 52–54) says so. That is consistent with the design. Keep it as a named Phase 7 agent step so SC12 is not orphaned.

### [PASS] Sequencing and dependencies hold

Checked: 1.2→1.3→2.2 (`dbn_values` before `tick_bars`); 2.3→2.4; 3.1 before 3.3 (`iter_batches`); 3.5–3.7 before 3.8 and 4.5; 4.1→4.3→4.5; 5.1→5.2→5.4→5.5; section 7 after 5, so `test_231` is updated before `busiest` moves. There are no cycles. The architecture's "Tick bars and raw ticks" paragraph already carries the partial-bar rule (`project-documents/user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md:78`), so D2 needs no task.

### [PASS] Cited paths and symbols exist

Verified: `stream_rows` (tick_reads.py:107), `ledger_totals`, `verdicts_for`, `family` on `contract_plan`/`product_plan`, `tick_support` (seed_ticks, client_for, seed_september_roll), the trades twin fixture, `busiest` in test_231 (line 136), `futures.py` at 342 lines, reference.md §6.5/6.7/6.8, the agents.md "Give me futures ticks/bars" headings, and README lines 167 and 818.

### [CONCERN] Load-test instructions contradict each other

7.1 says each bound is "about twice the median of five runs," then says "Run it once … and record the medians." One run has no median. Specify five runs, or one run with the bound set from it. An implementer will otherwise have to guess which to follow.

### [CONCERN] The new load test has no CI wiring

Section 7 has no CI subtask. Its only gate is a manual env-var command. `ci.yml` has no reference to the load tier, and the existing load tests use the same `MT_RUN_LOAD_TESTS=1` gate and are not in CI. Add a subtask that wires the 308 load test into CI, or state in 7.1 why gating stays manual.

### [CONCERN] The load test does not run at the trade limit that its bound is set against

Test 1 scans the archived day's 889,373 records, about 44% of the 2,000,000 default limit (D5). The design says Phase 7 measures the limit itself. A pass at 44% does not show the default meets 10 s. Say in 7.1 that the test is a proxy and that the limit-level bound is checked only in Phase 7.

### [CONCERN] Subtask 5.2's delta rule contradicts D6 and the 5.3 decoder

5.2 says deltas are "from the block's first `ts_event`." D6 (slice line 254) says each `ts_event[i]` is the delta from tick i−1, and 5.3's decoder is a prefix sum from `ts_base`. Read literally, 5.2 produces offsets from the first event, and the 5.3 decoder would return wrong timestamps. Rewrite 5.2 to match D6: delta from the previous tick, `ts_event[0] = 0`.

### [CONCERN] The task adds exception types and a 499 response the design does not specify

Slice D1 (line 164) says the route catches only `TickBarInputError`. Task 3.3 adds `TickBarWindowError` (lines 218–221) to carry session, symbol and instrument, and 4.5 catches that instead. The `TickBarWindowError` wrapper is a reasonable fix for a real gap: D1 asks the log to name the window, but nothing gives the route that window. Still, it is an unrecorded design change. 3.3 also raises `ReadAbandoned` (lines 204–205), which does not exist in `src` or `test` yet, and 4.5 answers it with 499. D10 (slice line 294) says the request "ends with no response." Amend the slice to name these types and their home module, or drop them.

### [CONCERN] Eight commit markers sit after all eight top-level sections

Markers are at lines 100, 174, 272, 350, 406, 453, 478 and 502. Each one lands at a working point, but the markers match one per top-level task, which is the same cadence as having none. Fold section 1 into section 2's commit: the move is a prerequisite for `tick_bars.py` and its tests run there. Fold section 8 into section 7, or drop it (see the scope finding). That leaves six markers at real boundaries.

### [CONCERN] Section 8 adds a measurement tool the design does not ask for

The slice's Included scope names no measurement script. D9 and Verification Walkthrough step 5 describe the Phase 7 check as curl, `time`, and the ledger query. 8.1 adds a Python tool with a concurrent probe and a notes writer, and 8.2 adds its unit tests. SC12 needs only the recorded result. Either cut section 8 and record the result from the walkthrough's steps, or keep `pick_range` and `check_totals` as a small script with its tests inside the same subtask. Drop the separate test subtask either way.

### [CONCERN] The operator runbook the slice names has no task, and the anchor it cites is absent

The slice says the setting is documented "beside `MT_API_MAX_BARS_PER_REQUEST` in the reference and the operator runbook." The runbook exists (`project-documents/user/runbooks/100-production-operations.md`), but it has no `MT_API_` settings to sit beside. Task 6.3 covers only README. Either add a subtask that adds the setting to the runbook with a new API-settings row, or amend the slice to drop the runbook.

### [NOTE] Stale statements in the slice

Line 80 says `_stream` is renamed to `stream_rows` "in step 2," but that rename is already in `tick_reads.py:107`, and task 1.1 checks it. Line 103 says `futures.py` is 274 lines; it is 342, so task 4.1 is right. Line 99 places the new response models in `futures.py`, while task 4.3 puts them in `futures_tick_bars.py`, which is the better choice. Amend the slice so the design and the tasks agree.

### [NOTE] Ambiguities a junior implementer could resolve differently

2.1 does not define "the busiest instrument" or how to pick the 1,000 trades on each side of the break. The premise assertion catches a wrong choice, but not how to make it. 1.3 leaves "re-export or `dbn_values`; pick one" for `tick_format.py` (which imports from `constants` at line 17), and its grep covers only `src`. `test/integration/data/test_tick_definitions.py:40` also defines `UNDEF_INT64`. Name the choices, and extend the grep to the test tree.

### [NOTE] Subtask granularity and test placement

3.2 (SELECT definition), 4.1 (rename), and 4.2 (one parameter) are one-line subtasks with no test of their own. Merge each into the subtask that uses it. 3.5–3.7 defer their tests to 3.8, which bundles three components into one list; that is acceptable inside one commit. The densest subtasks are 3.3 (lines 196–221) and 4.5 (lines 304–325). If 4.5 proves too large, its error-path bullets can become their own subtask with tests.

### [NOTE] The SC8 check covers two files only

6.6 greps `tick_bar_reads.py` and `futures_tick_bars.py` for count-grouping SQL. SC8 says no SQL statement anywhere groups trades by count. Widen the grep to `src/manta_trading/data/tick` and the futures route modules to check it more fully.

### [NOTE] The compact-size claim has no check

The Value section claims about a tenth of today's gzipped bytes per tick (ADR 9.5 against 18.5 B/tick). Verification step 6 measures it by hand, and no task or criterion covers it. That can stay a manual check, but the breakdown should say so.

### Run Digest

- Response length: 12001 chars
- Response is newline-free: no
- Tool calls made: 33
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: xhigh
- Turns: 34
- Tokens — prompt / cached / cache write / completion / reasoning: 1532078 / 1351934 / 180122 / 72175 / not computed
- Duration: 310.6 s
- Cost: $0.42 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 17
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 17
- Finding-shaped matches — surviving validation: 17

## Resolution

- F001 [pass] Success criteria 1–11 map to tasks: no change needed.
- F002 [note] SC12 only in Phase 7: the Context Summary now names running the measurement script (task 7.2) and writing its note as a Phase 7 step tracked by issue #75.
- F003 [pass] Sequencing holds: no change needed.
- F004 [pass] Cited paths and symbols exist: no change needed.
- F005 [concern] Load-test run count: 7.1 now says five runs, bound about twice the median, with the timings, median and date recorded in the docstring.
- F006 [concern] No CI wiring: rejected. CI has no test job and every other load test is gated by the manual `MT_RUN_LOAD_TESTS=1` command, so 7.1 now states that and adds no CI step.
- F007 [concern] Test is below the trade limit: 7.1 says it scans about 44% of the default limit, is a proxy, and the limit-level bound is checked only in Phase 7 (#75).
- F008 [concern] Delta rule: 5.2 now says each `ts_event[i]` is the delta from tick `i - 1`, with `ts_base` the first `ts_event`, matching D6 and the 5.3 decoder.
- F009 [concern] Unrecorded exception types and 499: kept, and the slice (D1, D10) now names `TickBarWindowError` and `ReadAbandoned` in `tick_bar_reads.py`, the 499 answer, and `keys` on `TickBarInputError`. The Context Summary points at it.
- F010 [concern] Commit markers: section 1 folded into section 2's commit and section 8 into section 7's, leaving six markers.
- F011 [concern] Measurement tool not in the design: kept as one small subtask (7.2) with its unit tests inside it, since the earlier review required a task for SC12. The separate test subtask is gone.
- F012 [concern] Operator runbook: the slice now documents the setting in the reference and the README settings tables and says the runbook has no API settings section; no runbook task added.
- F013 [note] Stale slice statements: fixed the `stream_rows` rename text (now describes `iter_batches`), the model file placement (`futures_tick_bars.py`, `plan_envelope`) and the `futures.py` size (342).
- F014 [note] Ambiguities: 2.1 defines the busiest instrument and how session dates are derived. 1.3 names `dbn_values` as the import for `tick_format.py`, replaces the duplicate in `test_tick_definitions.py`, and widens the grep to `src test`.
- F015 [note] Granularity: the SELECT definition merged into `read_tick_bars` (3.2), and the envelope rename and `TicksPerBarParam` merged into the models subtask (4.1); later subtasks renumbered. The dense route subtask was left whole, as the review allowed.
- F016 [note] SC8 grep: widened to `src/manta_trading/data/tick` and `api_server` for `row_number|ntile|width_bucket`, with every `group by` hit read and none allowed to select from `tick_trade`.
- F017 [note] Compact-size claim: 6.6 states it is a manual check (walkthrough step 6, Phase 7) and not gated; I added the step to issue #75 as a comment.
