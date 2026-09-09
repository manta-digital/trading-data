---
docType: review
layer: project
reviewType: tasks
slice: minute-acquisition-correctness
project: trading-data
verdict: FAIL
sourceDocument: project-documents/user/tasks/921-tasks.minute-acquisition-correctness-1.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260908
dateUpdated: 20260908
reviewedSha: 7ebbe871afef43aa69bd1fd598e35e25194ee377
findings:
  - id: F001
    severity: fail
    category: coverage-gap
    summary: "No implementation task makes a 5xx stop moving accounting; SC4's tests cannot pass"
    location: "src/manta_trading/data/acquisition/daemon/minute.py:455-506"
  - id: F002
    severity: fail
    category: correctness
    summary: "The backfill phase re-seeds from the stale coverage index and re-fetches the trailing session"
    location: "src/manta_trading/data/acquisition/daemon/minute.py:355-395"
  - id: F003
    severity: concern
    category: sequencing
    summary: "Section 4's exit mapping and its tests depend on the phases Section 5 introduces"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-1.md:287-312"
  - id: F004
    severity: concern
    category: correctness
    summary: "Task 4.4's breaker cannot distinguish provider failures from DB failures as written"
    location: "src/manta_trading/data/acquisition/daemon/minute.py:226-281"
  - id: F005
    severity: concern
    category: under-specified
    summary: "Task 4.5's \"existing exit-code constant\" does not exist, and the outcome has no path to the process exit"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-1.md:296-303"
  - id: F006
    severity: concern
    category: test-coverage
    summary: "No load test for the health check's stated read NFR, and no CI gating task"
    location: "test/load"
  - id: F007
    severity: concern
    category: sequencing
    summary: "Task 8.5 is wait-blocked on two nightly timer firings"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:272-284"
  - id: F008
    severity: note
    category: consistency
    summary: "Section 2 has no commit checkpoint"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-1.md:160-183"
  - id: F009
    severity: note
    category: consistency
    summary: "Health floor is stated as both 975,000 and 1,000,000"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:106-118"
  - id: F010
    severity: note
    category: test-with-pattern
    summary: "Tests are batched at the end of Sections 4 and 6 rather than following each implementation task"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-1.md:304-312"
  - id: F011
    severity: pass
    category: accuracy
    summary: "Every code reference, test path, and pattern the tasks cite was verified to exist"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-1.md:40-70"
---

# Review: tasks — slice 921

**Verdict:** FAIL
**Model:** claude-opus-5

## Findings

### [FAIL] No implementation task makes a 5xx stop moving accounting; SC4's tests cannot pass

SC4 requires that 402, 429 exhaustion, 5xx, timeout, and connection reset touch no gap row. Section 3 only fixes the **seed** path in `update_data_gaps` (Tasks 3.1, 3.2); Task 3.3 is tests only. But in the chunk loop, `classify_outcome` returns `TRANSIENT_FAILURE` for `status_code >= 500` (outcomes.py:76), and the code then calls `_advance_minute_gap(outcome=TRANSIENT_FAILURE, fetch_status=FAILED_RETRYABLE)` and `_record_minute_attempt` — so a 500 today increments `attempt_count`, can promote to `RETRY_EXHAUSTED`, and writes `acquisition_state.last_attempt_outcome`.

Task 3.2 asserts the opposite as fact: "`_advance_minute_gap`'s non-success branch, which already runs only after `classify_outcome` on a real response" (line 210). A 5xx *is* a `classify_outcome` return value, not an exception, so a junior following that sentence will change nothing in `minute.py`.

Failure scenario: implementer completes Tasks 3.1–3.2, then writes Task 3.3's "HTTP 500 asserts no `attempt_count`, `fetch_status`, or `last_attempt_ts` changed" test. It fails against the code, with no task authorizing the fix. Add a task in Section 3 that makes a `TRANSIENT_FAILURE`-from-a-response skip `_advance_minute_gap` and `_record_minute_attempt`, and correct Task 3.2's claim.

### [FAIL] The backfill phase re-seeds from the stale coverage index and re-fetches the trailing session

Task 5.2 (line 329) specifies two full walks of the active universe with "the coverage index built once and shared by both phases," each walk going through `_do_minute_symbol`. In `_do_minute_symbol` the seed gate is `_needs_full_seed = force_reset_terminal or not _has_bars or not _has_any_gaps or _has_unknown_gaps` — with a 70k-row UNKNOWN backlog, `_has_unknown_gaps` is true for essentially every symbol, so the gate fires again on the second walk. The re-seed calls `compute_missing_minute_sessions` with the cycle-start coverage index, which still reports the just-fetched trailing day as uncovered, and re-inserts it as UNKNOWN. The backfill phase then calls `pick_most_recent_actionable_gap` with no `min_gap_end` and `ORDER BY gap_end DESC`, so the first row it picks is exactly the session the trailing phase just filled.

Failure scenario: on the first post-cutover 01:05 firing, ~13,000 symbols each get one redundant `/intraday` call at 5 credits — roughly 65k of the 100k daily allowance spent re-fetching the day just fetched, which is the precise resource this slice exists to protect. Task 5.2 must state whether the backfill phase seeds at all (and if not, how), or how the coverage index is refreshed between phases; Task 5.3 should assert no symbol is fetched twice in one cycle.

### [CONCERN] Section 4's exit mapping and its tests depend on the phases Section 5 introduces

Task 4.5 defines the mapping as "`QUOTA_EXHAUSTED` **after the trailing phase completed** → 0; `QUOTA_EXHAUSTED` **inside the trailing phase** → 3," and Task 4.6 asserts "trailing-phase 402 → exit 3; post-trailing 402 → exit 0." The trailing/backfill phases do not exist until Task 5.2. As ordered, Task 4.6 is unwritable at its checkpoint (Task 4.7), and Section 4 cannot go green independently.

Failure scenario: implementer reaches Task 4.6, has no phase concept to condition on, and either invents a placeholder flag that Section 5 then reworks, or checks the section in with the phase-dependent cases stubbed. Either move the exit mapping (4.5) and its phase-dependent tests into Section 5 after 5.2, or move the two-phase cycle ahead of the outcome/exit work.

### [CONCERN] Task 4.4's breaker cannot distinguish provider failures from DB failures as written

Task 4.4 requires the breaker to count 5xx / timeout / connection-reset / 429-exhaustion symbols but explicitly **not** pool timeouts or advisory-lock timeouts. `_process_minute_symbol` collapses all of these to the same return value — `LastAttemptOutcome.TRANSIENT_FAILURE, None, None, 0, 0` — in five separate `except` handlers (`ProviderResponseError`, `LockNotAvailable`, `PoolTimeout`, `httpx.HTTPError`, bare `Exception`). `run_minute_cycle` sees only that one enum value and has no way to tell them apart.

Failure scenario: a Postgres pool exhaustion causes five consecutive `PoolTimeout` symbols; the breaker counts them as provider failures and aborts the pass with `PROVIDER_UNAVAILABLE` and exit 3, misattributing a database problem to the provider — precisely the case the task says must not trip it. The task needs to state that `_process_minute_symbol`'s return (or an exception type reaching the cycle) must carry the failure kind.

### [CONCERN] Task 4.5's "existing exit-code constant" does not exist, and the outcome has no path to the process exit

Task 4.5 says to "reuse the existing exit-code constant rather than a new literal." There is no shared exit-code constant in the project. The nearest precedent is `EXIT_BY_OUTCOME`, a dict local to `src/manta_trading/cli/commands/kalshi.py:54`; `cli/commands/data.py` otherwise uses per-command private ints (`_EXIT_PREFLIGHT_FAILED`, `_EXIT_HORIZON_WARN`, …) and one bare `typer.Exit(3)` at data.py:3758. Separately, no task describes how a `MinutePassOutcome` reaches the process exit: `run_minute_cycle` returns a `CycleReport` (defined in daemon/daily.py:69 and shared with the daily path), the runner returns its own int from `Runner.start()`, and `daemon_run` ends at `sys.exit(runner.start())` (data.py:1470).

Failure scenario: the implementer greps for an exit-code constant, finds none matching the description, and either invents a literal `3` inside `minute.py` (violating the slice's no-magic-values rule) or bolts a minute-only field onto the shared `CycleReport` without deciding what a `--forever` runner that ran several minute cycles should exit with. Name the plumbing: outcome → report/runner → exit, and name the constant to create.

### [CONCERN] No load test for the health check's stated read NFR, and no CI gating task

The slice restates a read NFR for the new check: "One session is ~41k cagg rows (measured 2026-08-27), a **sub-second read**," with `HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT = "30s"` and exit 2 on timeout, explicitly citing the §166/§167 raw-table latency cliff. This project's convention for exactly that kind of claim is a load-tier test — `test/load/test_167_data_status_nfr.py` and `test/load/test_169_coverage_freshness_probe_nfr.py` are the direct precedents. Neither task file adds one; Task 6.3 and Task 6.6 (file 2) cover only mocked unit assertions on the query text and a simulated timeout.

Failure scenario: the query is written against `minute_4hour_ohlcv` correctly but with a shape that scans all buckets before filtering; the unit tests pass (they assert the table name, not the latency), and the regression only surfaces in production as an exit-2 timeout on the 30s statement timeout. Note also that `.github/workflows/ci.yml` is publish-on-tag only — there is no test job at all — so if a load test is added, a CI wiring task is needed rather than assumed.

### [CONCERN] Task 8.5 is wait-blocked on two nightly timer firings

Task 8.5 ("Verify against production after two firings") cannot start until two nightly `mt-minute-pass.timer` firings have elapsed, and Task 8.6 (close #19/#20) depends on it. The standing rule on this project is that no task may wait on tonight/tomorrow; the work should be restructured into something measurable now. `cutover_common` already exposes a `fire` helper, and the cutover script (Task 8.1) is root-capable, so the passes can be started directly rather than waited for.

Failure scenario: the slice branch sits open across two calendar days with two tasks unfinished and no way to check them off, which is the pattern that has been vetoed before. Restructure 8.5 to fire the daily and minute passes explicitly after `--apply` and measure immediately, keeping only the "at least one healthy run after 23:00 UTC" observation as a recorded follow-up.

### [NOTE] Section 2 has no commit checkpoint

Sections 1, 3, 4, and 5 each end with an explicit checkpoint task (1.6, 3.4, 4.7, 5.4) running the unit tier plus mypy, scoping `ruff format`, and committing. Section 2 ends at Task 2.2 with no checkpoint, so its two regression tests ride into Section 3's commit — inconsistent with the file's own stated rule ("Commit checkpoint at the end of each section"). Either add a checkpoint or fold Section 2's two tests into Section 1 before Task 1.6.

### [NOTE] Health floor is stated as both 975,000 and 1,000,000

Task 6.4 correctly derives `2,500 × 390 = 975,000`, matching the design's example output line. But the design's Verification Walkthrough prints "floors 1,000,000 / 5,000," and Task 8.5 uses "≥ 1,000,000" for the acceptance measurement. The two numbers serve different purposes (health floor vs. cutover acceptance) but are never distinguished, so a junior reconciling them may hardcode the wrong one. State explicitly that the floor is computed and the 1,000,000 is a separate, stricter acceptance bar.

### [NOTE] Tests are batched at the end of Sections 4 and 6 rather than following each implementation task

Sections 1, 3, and 5 follow the test-with pattern closely (1.2→1.3, 1.4→1.5, 3.2→3.3, 5.2→5.3). Section 4 stacks three implementation tasks (4.3 abort, 4.4 breaker, 4.5 journal/exit) before Task 4.6's combined test task, and file 2's Section 6 stacks 6.1–6.5 before Task 6.6. Tasks 4.1 and 4.2 carry their own inline test assertions, so the exposure is limited, but splitting 4.6 into a test task after 4.3/4.4 and another after 4.5 would keep each unit independently verifiable.

### [PASS] Every code reference, test path, and pattern the tasks cite was verified to exist

Spot-checked against the tree: `fetch_sessions` (compute_missing_ranges.py:151) selects only `session_open_utc`; `group_sessions_into_ranges` (:195) is shared with the daily path; `run_minute_cycle` is at minute.py:107, `_process_minute_symbol` at :226, `_do_minute_symbol` at :285, `_advance_minute_gap` at :636 — all matching the cited line numbers; `update_data_gaps` already accepts `force_reset_terminal` and `precomputed_ranges`; `MAX_RETRY_COUNT`, `PULL_MAX_CONSECUTIVE_PROVIDER_ERRORS`, `DAEMON_LOCK_TIMEOUT`, `CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT`, and `HEALTH_EODHD_QUOTA_HEADROOM_MIN` all exist in `constants.py`; `trading_sessions.session_close_utc` and the cagg's `minute_count` column both exist; and all eleven referenced test files plus `cutover_common.py`, `test_cutover_267.py`, and the systemd unit/timer are present. No hallucinated paths or symbols were found.
