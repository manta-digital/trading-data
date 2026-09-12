---
docType: tasks
slice: operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh
project: trading-data
lld: user/slices/922-slice.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [919, 921]
interfaces: []
projectState: >
  Design 922 committed at 34debc9 (slice review CONCERNS, nine findings
  addressed, passes the gate). Version 0.14.9 on main and installed on
  manta9000: weekly minute trailing (MT_MINUTE_FIRING_DAYS=Sat), backfill-only
  on other days, `mt data accounting` available. Pass state exists only in
  journal lines; `mt data status` reads holes as gaps and, six days a week,
  every active minute symbol as STALE.
dateCreated: 20260911
dateUpdated: 20260912
status: in_progress
---

## Context Summary

- Working on **922 operator overview** in six sections: (1) pass-run storage
  and recorder; (2) the five writers; (3) firing schedules; (4) `mt data
  overview`; (5) the `data_status` view, the status footer and the default
  flip; (6) accounting units, docs and release.
- Source of truth: the slice design. Tasks cite its Technical Scope items
  (Scope 1–8), Technical Decisions (Decision 1–12) and Success Criteria
  (SC1–SC9, SC6a). Read the cited section before each task.
- Section order matters: writers need the recorder; the overview needs
  writers and schedules; the view migration (056) needs the table (055) and
  is the last schema change so `main` stays deployable at every checkpoint.
- Phase 6 branch: `922-slice.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh`,
  forked from the integration target (`cf config get git.integration_branch`,
  `main` if empty). Verify before Task 1.1.
- Code the tasks touch, all read during breakdown:
  - `data/acquisition/daemon/heartbeat.py` — `HeartbeatRepository`: the
    repository shape to copy (`_COLS`, row mapper, psycopg3 raw SQL, upsert).
  - `data/quality/fetch_status.py` + `market/schema/migrations/minute.py`
    `_fetch_status_check_sql` (~line 130) — the enum-to-CHECK pattern.
  - `market/schema/migrations/minute.py` — the list ends at
    `054_daily_monthly_refresh_window` (~line 2339); `021_data_status_view`
    (~1232) and the 038 position-critical block (~1171); the view builder
    `_build_data_status_view_sql` (~219), its `gap_counts` CTE (~336) and
    STALE `CASE` (~348), the pre-rendered variants (~371, ~456), the doc
    comment `_data_status_doc_comment` (~382), `_interval_literal` (~148,
    stays), `DAILY_STALENESS_THRESHOLD` / `MINUTE_STALENESS_THRESHOLD`
    (`constants.py` ~214–217, deleted).
  - `data/acquisition/daemon/runner.py` — `_loop` (~607): daily cycle call
    (~632–659), minute cycle call (~660–682), `_record_minute_pass_outcome`
    (~473); `RunnerConfig.is_explicit_scope` (~89).
  - `data/acquisition/daemon/minute.py` — `run_minute_cycle` (~242),
    `_run_trailing_phase` (~367), `_run_minute_phase` (~479), the progress
    log every `MINUTE_SEED_PROGRESS_LOG_INTERVAL` symbols (~611),
    `minute_pass_exit_code` and `_MINUTE_EXIT_BY_OUTCOME` (~99, ~135).
  - `data/acquisition/daemon/daily.py` — `CycleReport` (~71), the
    pass-boundary derivation (~245–300), `run_daily_cycle` (~308).
  - `data/kalshi/collection_pass.py` — `CollectionPass.run` (~137),
    `PassResult`, `PhaseReport`, `PassPhaseName`; `cli/commands/kalshi.py`
    `run_pass` (~180) and `EXIT_BY_OUTCOME` (~54); `SyncOutcome` in
    `data/kalshi/sync_types.py`.
  - `cli/commands/health.py` — `data_health` (~214), `render` (~193),
    `EXIT_UNAVAILABLE`; `cli/commands/accounting.py` `data_accounting` (~26)
    and `data/gaps/minute_accounting.py` `summary_line` (~211).
  - `minute_firing_schedule.py` — `next_minute_firing_at` already takes a
    `times` tuple; `constants.py` `MINUTE_PASS_FIRING_TIMES_UTC` (~185).
  - `api/eodhd_sync.py` `eodhd_get` (~83); `constants.py`
    `HEALTH_EODHD_USER_ENDPOINT` (~210, currently orphaned) and
    `EODHD_DAILY_QUOTA` (~976).
  - `cli/commands/data.py` — `data_status` (~860), the footer print (~1035),
    registration block (~86–104); `cli/rendering/status_table.py`
    `render_status_footer` (~199); `data/maintenance/status_queries.py`
    `fetch_all_health_counts_with_freshness` (~170).
  - `test/unit/deploy/test_units.py` — `TestHealthUnits`,
    `TestMinutePassTimerMatchesTheConstant` (the drift-guard pattern);
    `test/unit/test_schema_migrations.py`
    `test_ids_are_sorted_except_known_position_critical` (~154) and
    `test_migration_count`; `test/load/test_167_data_status_nfr.py`.
  - `deploy/systemd/`, `deploy/install-production.sh` (units array ~39,
    cutover hint ~213), `deploy/mt-run` (unchanged).
- Tests: `python scripts/run_tests.py unit` and
  `python scripts/run_tests.py integration` run separately (a conftest name
  clash breaks a combined run); integration needs `MT_TIMESCALE_TEST_URL`
  exported. DB-backed tests use the `migrated_db` fixture from
  `test/conftest.py`.
- Commit at least once per section; every checkpoint leaves `main`
  deployable. Scope `ruff format` to touched files.

## Section 1: Pass-run storage and recorder

Design *Scope 1*, *Decision 1, 2, 4*, *Database / Storage Schema*, *SC3*,
*SC6*, *SC7*.

- [x] **Task 1.1: `PassKind` and `PassRunOutcome` enums** (effort: 1)
  - [x] New module `data/acquisition/pass_runs.py` with `PassKind(StrEnum)`
        (`MINUTE`, `DAILY`, `KALSHI`, `HEALTH`, `ACCOUNTING`) and
        `PassRunOutcome(StrEnum)` (`COMPLETE`, `COMPLETE_QUOTA`, `INCOMPLETE`,
        `PROVIDER_UNAVAILABLE`, `FAILED`), each member documented with the
        one-line meaning from Decision 2.
  - [x] A `PassRun` dataclass whose fields mirror the table columns of the
        design's schema exactly, including `hostname`, `pid`, `walk_anchor_at`.
  - [x] Success: the enums are the only place these strings exist; no bare
        outcome or kind literal anywhere else in `src/`.
- [x] **Task 1.2: Migration 055 `create_pass_runs`, position-critical** (effort: 2)
  - [x] Add the table from the design's schema block with CHECK constraints
        rendered from the two enums via helpers in the `_fetch_status_check_sql`
        style, the `(ended_at IS NULL) = (outcome IS NULL)` constraint, and
        `idx_pass_runs_pass_started`. `IF NOT EXISTS` throughout.
  - [x] Insert the dict **immediately before `021_data_status_view`** in
        `MINUTE_MIGRATIONS`, with a position-critical comment modelled on
        038's, and add `"055_create_pass_runs"` to `position_critical_ids` in
        `test_ids_are_sorted_except_known_position_critical`; update
        `test_migration_count`.
  - [x] Success: `mt data migrate status` on a `migrated_db` lists 055 applied
        between 020 and 021; applying twice is a no-op.
- [x] **Task 1.3: Tests for the migration** (effort: 1)
  - [x] Integration test over `migrated_db`: the table exists with the exact
        column list; an insert with an outcome outside the enum is rejected;
        an insert with `ended_at` set and `outcome` NULL is rejected.
  - [x] Unit test: the CHECK text contains every enum member and nothing else.
  - [x] Success: `python scripts/run_tests.py unit` and the integration file
        pass.
- [x] **Task 1.4: `PassRunRepository`** (effort: 2)
  - [x] In `pass_runs.py`, copy the `HeartbeatRepository` shape: `_COLS`, a
        row mapper, `insert(run)`, `update_progress(run_id, phase, done,
        total, at)`, `close(run_id, ended_at, outcome, exit_code, detail)`,
        `open_runs(kind) -> list[PassRun]` (newest first),
        `latest_ended(kind) -> PassRun | None`, `close_abandoned(kind,
        hostname, dead_pids, now)`.
  - [x] All SQL parameterised; no interpolation.
  - [x] Success: each method has one query; a docstring on the class names
        Decision 1 (rows written by the process that runs the pass).
- [x] **Task 1.5: Tests for the repository** (effort: 2)
  - [x] Integration test over `migrated_db`: insert → open_runs returns it →
        update_progress reflected → close → latest_ended returns it and
        open_runs is empty; `close_abandoned` closes only the listed pids on
        the given host with outcome `FAILED` and detail `abandoned: pid N gone`,
        leaving a live pid and a foreign host untouched.
  - [x] Success: the file passes in the integration tier.
- [x] **Task 1.6: `PassRunRecorder`** (effort: 2)
  - [x] New module `data/acquisition/daemon/pass_run_recorder.py`. Constructor
        takes the repository, a clock, `hostname` and `pid` (defaults from
        `socket.gethostname()` / `os.getpid()`), and a pid-liveness callable
        (default `os.kill(pid, 0)` wrapped to return bool).
  - [x] `open(kind, walk_anchor_at)` first calls the liveness callable on every
        open row of this kind on this host and closes the dead ones via
        `close_abandoned` (Decision 4), then inserts the new row and returns
        its `run_id`. `progress(...)` and `close(...)` delegate.
  - [x] Every method catches `psycopg.Error`, logs with `logger.exception` at
        ERROR, and returns without raising: recording never aborts a pass. No
        other exception type is caught.
  - [x] Success: the docstring states the never-raises contract and cites
        Decision 4.
- [x] **Task 1.7: Tests for the recorder** (effort: 2)
  - [x] Unit tests with a fake repository: a dead-pid row on the same host is
        closed at open; a live-pid row and a foreign-host row are not; a
        repository error on `progress` is logged at ERROR and does not raise.
  - [x] Success: `test/unit/data/acquisition/daemon/test_pass_run_recorder.py`
        passes. Commit (section checkpoint).

## Section 2: Writers

Design *Scope 1*, *Decision 2, 3, 5*, the *Writers* table, *SC2*.

- [x] **Task 2.1: Outcome mappings** (effort: 2)
  - [x] Next to `minute_pass_exit_code` in `daemon/minute.py`:
        `pass_run_outcome_for_minute(outcome, *, trailing_completed,
        trailing_required) -> PassRunOutcome` with an exhaustiveness assert
        over `MinutePassOutcome` like `_MINUTE_EXIT_BY_OUTCOME`. Table:
        trailing required and not completed → `INCOMPLETE` (quota) or
        `PROVIDER_UNAVAILABLE`; trailing done or not required and backfill
        `COMPLETE` → `COMPLETE`; backfill `QUOTA_EXHAUSTED` → `COMPLETE_QUOTA`;
        backfill `PROVIDER_UNAVAILABLE` → `PROVIDER_UNAVAILABLE`.
  - [x] In `cli/commands/kalshi.py` next to `EXIT_BY_OUTCOME`:
        `pass_run_outcome_for_kalshi(SyncOutcome)`: `OK` → `COMPLETE`,
        `PARTIAL` → `INCOMPLETE`, `PROVIDER_ABORT` → `PROVIDER_UNAVAILABLE`,
        `STORAGE_ABORT` → `FAILED`; exhaustive.
  - [x] Success: both functions are the only mapping site for their source.
- [x] **Task 2.2: Tests for the mappings** (effort: 1)
  - [x] Parametrised unit tests over every member of each source enum and
        both trailing flags; a test that adding a member to a fake enum trips
        the assert.
  - [x] Success: tests pass in the unit tier.
- [x] **Task 2.3: Minute progress callback** (effort: 2)
  - [x] Add `on_progress: Callable[[MinutePassPhase, int, int], None] | None`
        to `run_minute_cycle`, `_run_trailing_phase` and `_run_minute_phase`,
        threaded exactly like `on_symbol`; call it at the existing
        `MINUTE_SEED_PROGRESS_LOG_INTERVAL` log site (~line 611) and once at
        phase completion with `done == total`.
  - [x] `CycleReport` gains `trailing_symbols_attempted`,
        `backfill_symbols_attempted` (ints) so the close detail can read
        `trailing n/N · backfill n symbols` without re-deriving.
  - [x] Success: existing minute tests pass unchanged with `on_progress=None`.
- [x] **Task 2.4: Runner writes minute and daily rows** (effort: 3)
  - [x] `Runner.__init__` takes an optional `PassRunRecorder`; the CLI
        (`daemon_run` in `cli/commands/data.py`) constructs it from the same
        connection factory it already builds, **only when the scope is
        `SCOPE_ALL_ACTIVE`** (Decision 3); explicit scopes pass `None`.
  - [x] Minute cycle site: before `_run_minute_cycle`, `recorder.open(MINUTE,
        walk_anchor_at = now if is_firing_day(now.date(),
        settings.minute_firing_days) else None)`; pass `on_progress` through;
        after the cycle, `close` with the mapping from Task 2.1, `exit_code =
        minute_pass_exit_code(...)`, detail from the new report counters;
        the `report is None` path closes `FAILED`, exit
        `MINUTE_EXIT_PASS_INCOMPLETE`, detail = exception class name.
  - [x] Daily cycle site: `open(DAILY, walk_anchor_at = the cycle's pass
        boundary)` — expose the boundary computation as a function in
        `daily.py` if it is inline today; `run_daily_cycle` sets a new
        `CycleReport.daily_pass_completed` (true when the pending list was
        walked to the end); close `COMPLETE` / `INCOMPLETE`, exception →
        `FAILED`; exit code 0 (Decision 5: the daily exit code is unchanged).
  - [x] Success: one row per cycle; a `--symbols` invocation writes none.
- [x] **Task 2.5: Tests for the runner writers** (effort: 2)
  - [x] Extend `test/unit/data/acquisition/daemon/test_runner.py` using its
        `_harness.py`: a fake recorder records open/progress/close for a
        minute cycle on a firing day (anchor set) and a non-firing day
        (anchor None); a raising cycle closes `FAILED`; explicit scope →
        no recorder calls; daily completed vs stopped-early outcomes.
  - [x] Success: unit tier passes. Commit.
- [x] **Task 2.6: Kalshi pass writes its row** (effort: 2)
  - [x] In `run_pass` (`cli/commands/kalshi.py`), open a `KALSHI` row (no
        anchor) using the existing `run_id`; `CollectionPass` gets an optional
        `on_phase(PassPhaseName)` callback called at each phase start, which
        the CLI wires to `recorder.progress(phase=…)`; close with
        `pass_run_outcome_for_kalshi`, `EXIT_BY_OUTCOME[...]`, detail = the
        per-phase outcome list already logged at "kalshi pass finished".
  - [x] Success: `mt data kalshi pass` on a test DB leaves one ended row.
- [x] **Task 2.7: Tests for the Kalshi writer** (effort: 1)
  - [x] Extend `test/integration/test_kalshi_pass.py` (or its unit sibling
        under `test/kalshi_support/`): a pass with all phases OK closes
        `COMPLETE`; a provider abort closes `PROVIDER_UNAVAILABLE` with the
        skipped phases named in detail.
  - [x] Success: tests pass.
- [x] **Task 2.8: Health and accounting write their rows** (effort: 2)
  - [x] `data_health`: open `HEALTH` before `gather`; close `COMPLETE` with
        detail = the last rendered line (`healthy` or `UNHEALTHY: …`) and the
        exit code; the unavailable branch closes `FAILED`, exit 2, detail =
        the error text.
  - [x] `data_accounting`: open `ACCOUNTING` before `compute_minute_accounting`;
        close `COMPLETE` with detail = `summary_line(rows)`; unavailable →
        `FAILED`.
  - [x] Both share one small helper (`cli/commands/_pass_run.py`) that builds
        the recorder from settings, so the two commands do not duplicate it.
  - [x] Success: rows appear for both commands; exit codes unchanged.
- [x] **Task 2.9: Tests for the health and accounting writers** (effort: 1)
  - [x] Extend `test/unit/cli/commands/test_data_health.py` and the accounting
        test with a fake recorder: healthy, unhealthy and unavailable paths
        record the expected outcome, detail and exit code.
  - [x] Success: unit tier passes. Commit.

## Section 3: Firing schedules

Design *Scope 6*, *Decision 10*, *SC8*.

- [x] **Task 3.1: Schedule constants and `FiringSchedule`** (effort: 2)
  - [x] `constants.py`: `DAILY_PASS_FIRING_TIMES_UTC = (00:35, 12:35)`,
        `KALSHI_PASS_FIRING_MINUTE = 20`, `HEALTH_FIRING_MINUTE = 50`,
        `ACCOUNTING_PASS_FIRING_TIMES_UTC = (16:30,)`, each documented as
        "must match deploy/systemd/<unit>.timer".
  - [x] Rename `minute_firing_schedule.py` → `firing_schedule.py` keeping the
        four existing functions (update imports); add
        `FiringSchedule(times_utc, weekdays)` and `next_firing_at(after,
        schedule)`; an `hourly(minute)` constructor builds the 24-entry tuple.
        `schedule_for(kind, settings) -> FiringSchedule` is the one place the
        cadence per kind is assembled (minute uses
        `settings.minute_firing_days`).
  - [x] Success: `next_minute_firing_at` behaviour unchanged (existing tests
        pass after the import rename).
- [x] **Task 3.2: Tests and drift guards** (effort: 1)
  - [x] Unit tests for `next_firing_at` on twice-daily and hourly schedules
        across midnight and across a weekday gap.
  - [x] In `test/unit/deploy/test_units.py`, one test per new constant
        asserting it against the unit file's `OnCalendar`, in the
        `TestMinutePassTimerMatchesTheConstant` style. The accounting
        timer's drift test lands with its unit in Task 6.1.
  - [x] Success: unit tier passes. Commit.

## Section 4: `mt data overview`

Design *Scope 2*, *Decision 8, 9, 12*, *CLI Specification*, *SC1*, *SC2*,
*SC7*.

- [ ] **Task 4.1: `fetch_credit_usage`** (effort: 1)
  - [ ] New `api/eodhd_account.py`: `CreditUsage(used, daily_limit, extra)`
        and `fetch_credit_usage(api_key) -> CreditUsage` calling
        `HEALTH_EODHD_USER_ENDPOINT` through `eodhd_get` with a five-second
        timeout; rename the constant to `EODHD_USER_ENDPOINT` (a deliberate
        touch on a health-named constant: its only remaining consumer is this
        module, and grep confirms no other reference in `src/` or `test/`).
  - [ ] Success: `apiRequests` is documented as *used*; no new HTTP client.
- [ ] **Task 4.2: Test for the credit fetch** (effort: 1)
  - [ ] Unit test with a mocked `eodhd_get`: the three fields parse; a
        non-200 raises the wrapper's error (not swallowed here).
  - [ ] Success: passes.
- [ ] **Task 4.3: Overview gather and build** (effort: 3)
  - [ ] `cli/commands/overview.py`: `gather(conn, settings, *, now)` reads,
        per kind, `open_runs` and `latest_ended`; newest bar per source
        (`max(time)` on `minute_ohlcv`, `daily_ohlcv`, Kalshi candles and
        trades via the existing status readers); the latest `HEALTH` row's
        detail and `ended_at`; the latest `ACCOUNTING` row's detail and
        `ended_at`; credit usage guarded so a missing key yields
        `unavailable (MT_EODHD_API_KEY not configured)` and any
        `eodhd_get` / network error yields `unavailable (<text>)`.
  - [ ] Pure `build_overview(facts, settings, *, now, hostname, pid_alive)`
        returns a dataclass: per kind cadence text (`describe_firing_days`
        for minute; `00:35, 12:35`; `hourly :20`; `hourly :50`), running rows
        (phase, done/total, since, progress age, or `abandoned (pid N gone)`
        for a local dead pid), last run (start–end, outcome text with
        `COMPLETE_QUOTA` → `complete (quota)`, exit code, detail on a second
        line when `FAILED`), next firing via `schedule_for`.
  - [ ] Success: `gather` is the only I/O; the build has no DB or HTTP.
- [ ] **Task 4.4: Renderer and command** (effort: 2)
  - [ ] `cli/rendering/overview.py` renders the design's mockup (PASSES,
        next line, SOURCES with the health verdict on its header, credits,
        universe line with its timestamp or `never computed — run mt data
        accounting`); `--json` emits `passes`, `sources`, `credits`,
        `universe`, `now`.
  - [ ] `data_overview(ctx, json_output)` registered as `data_app.command
        ("overview")` in the `data.py` registration block; exit 0 when the
        DB answered, `EXIT_UNAVAILABLE` otherwise.
  - [ ] Success: `mt data overview --help` shows no other options.
- [ ] **Task 4.5: Tests for the overview** (effort: 2)
  - [ ] Unit tests for `build_overview` + render: never run; idle with a last
        run; running with progress; abandoned dead pid; two live open rows of
        one kind listed newest first; failed last run with detail line;
        credits unavailable branches; universe line absent.
  - [ ] Integration test over `migrated_db`: seed rows for all five kinds and
        assert `gather` returns them (correctness only; the latency bound is
        Task 4.6).
  - [ ] Success: both tiers pass. Commit.
- [ ] **Task 4.6: Load-tier bound for the overview** (effort: 1)
  - [ ] New `test/load/test_922_overview_nfr.py` over `prod_shaped_db`, in
        the `test_167_data_status_nfr.py` style: seed `pass_runs` rows for
        every kind, call `gather` with the credit fetch stubbed (load tests do
        not reach the network), and assert the database part completes in
        under five seconds. Decision 12's ten-second end-to-end bound is the
        five-second database budget plus the five-second `eodhd_get` timeout
        that Task 4.1 caps the only HTTPS call at; state this split in the
        test's module docstring.
  - [ ] Gating: this repository has no CI test job (`.github/workflows/ci.yml`
        only publishes on tags); every tier runs locally through
        `python scripts/run_tests.py load` with `MT_RUN_LOAD_TESTS=1` and
        `MT_TIMESCALE_TEST_URL`, exactly as the 167 load test does. Task 6.3
        runs it before the version bump.
  - [ ] Success: the case passes at production shape; the measured seconds go
        in the CHANGELOG entry with the Task 5.5 numbers. Commit.

## Section 5: View migration, status footer, default flip

Design *Scope 3, 4, 8*, *Decision 6, 7, 11, 12*, *SC4*, *SC5*, *SC6*, *SC6a*.

- [ ] **Task 5.1: View builder changes and migration 056** (effort: 3)
  - [ ] In `_build_data_status_view_sql`: `gap_counts` counts only
        `UNKNOWN` and `FAILED_RETRYABLE` (render the two names from
        `FetchStatus`); add the `walk_anchor` CTE over `pass_runs` (ended,
        anchored, `MAX(walk_anchor_at)` per pass) joined on granularity;
        STALE `CASE` becomes `last_attempt_ts IS NULL OR last_attempt_ts <
        wa.anchor`. Delete `DAILY_STALENESS_THRESHOLD`,
        `MINUTE_STALENESS_THRESHOLD` and their pre-rendered literals; keep
        `_interval_literal`.
  - [ ] Re-render `_data_status_doc_comment` to state the anchor rule, the
        NULL-anchor behaviour (only never-attempted read STALE) and the
        open-gaps meaning of `gap_count`.
  - [ ] Append `056_data_status_view_open_gaps_and_walk_anchor`: `CREATE OR
        REPLACE VIEW` via the builder (no DROP, no CASCADE, no column change)
        plus the comment; update `test_migration_count`.
  - [ ] Update the `gap_count` docstring in `api_server/models/responses.py`
        to "open gaps (UNKNOWN, FAILED_RETRYABLE)".
  - [ ] Success: `test_data_status_view_sql` updated and passing; a fresh
        `migrated_db` applies the whole track.
- [ ] **Task 5.2: Tests for the view semantics** (effort: 2)
  - [ ] Integration test over `migrated_db` (model on
        `test_migration_051_052.py`): (a) minute symbol attempted after the
        latest anchored minute run reads OK; one attempted before it reads
        STALE; never attempted reads STALE; no anchored run → only the
        never-attempted symbol is STALE; (b) daily symbol attempted at 00:35
        stays OK after a 12:35 run carrying the same boundary anchor; (c) a
        symbol whose only gap rows are `PROVIDER_HOLE` reads OK with
        `gap_count` 0; a `RETRY_EXHAUSTED` row still reads FAILED.
  - [ ] Success: passes; D2 column list assertion unchanged. Commit.
- [ ] **Task 5.3: Status footer second line** (effort: 2)
  - [ ] `status_queries.py`: `fetch_gap_status_counts(conn) -> dict[(granularity,
        FetchStatus), int]` via `GROUP BY granularity, fetch_status` on
        `data_gaps`.
  - [ ] `render_status_footer` prints per granularity: the four health counts
        and `still asking N · holes N · exhausted N` (still asking = UNKNOWN +
        FAILED_RETRYABLE); JSON output gains the same counts under
        `gap_status_counts`.
  - [ ] Success: `test_status_table.py` covers the new line; footer numbers
        equal the direct `data_gaps` query on a seeded `migrated_db`.
- [ ] **Task 5.4: `mt data status` summary by default, `--detail`** (effort: 2)
  - [ ] Add `--detail`; with no options print the SOURCES block (reuse the
        overview gather's source part) and the footer; any of `--symbol`,
        `--health`, `--daily`, `--minute`, `--all`, `--json`, `--detail`
        prints today's table path with the new footer.
  - [ ] Success: `--help` documents the default; `test_cli_data.py` updated
        for both paths.
- [ ] **Task 5.5: Load-tier gate** (effort: 1)
  - [ ] Extend `test/load/test_167_data_status_nfr.py` with a case for the
        default summary path (health counts + `fetch_gap_status_counts`) under
        the same `_NFR_SECONDS` margin; the existing view case must still pass
        with the anchor CTE.
  - [ ] Gating is local, not CI: there is no CI test job in this repository,
        so the load tier runs on the test cluster via
        `python scripts/run_tests.py load` (needs `MT_RUN_LOAD_TESTS=1` and
        `MT_TIMESCALE_TEST_URL`), the same way the existing 167 case is
        gated; Task 6.3 runs it.
  - [ ] Success: both cases pass at production shape; the measured seconds
        are recorded in the CHANGELOG entry. Commit.
- [ ] **Task 5.6: 140-arch amendment block** (effort: 1)
  - [ ] Add `*(Architecture amendment, 2026-09-xx — slice 922.)*` blocks in
        `140-arch.data-quality-operations.md` at the STALE rule, the Constants
        section (two constants removed) and the `gap_count` definition,
        naming `/api/v1/status` and `/api/v1/health` as readers.
  - [ ] Success: the blocks match the shipped SQL wording. Commit.

## Section 6: Accounting units, docs, release

Design *Scope 7*, *Decision 9*, *systemd*, *SC8*, *SC9*.

- [ ] **Task 6.1: `mt-accounting-pass` units and installer** (effort: 2)
  - [ ] `deploy/systemd/mt-accounting-pass.service` / `.timer` per the
        design's systemd block, copied from the health pair per 916's
        add-a-source checklist in `runbooks/100-production-operations.md`;
        add both to the units array and the cutover hint in
        `install-production.sh`; extend the runbook's unit table.
  - [ ] `test/unit/deploy/test_units.py`: a `TestAccountingUnits` class in
        the `TestHealthUnits` style plus the constant-vs-timer drift test
        deferred from Task 3.2.
  - [ ] Success: unit tier passes.
- [ ] **Task 6.2: Docs and CHANGELOG** (effort: 1)
  - [ ] README "System health" section: `mt data overview` documented as the
        first command to run, with the mockup; `mt data status` default and
        `--detail` described.
  - [ ] CHANGELOG entry listing the migrations (055 position-critical, 056),
        the new command, the status default change, the outcome vocabulary,
        the accounting timer and the load measurements from Task 5.5.
  - [ ] Success: docs cite no `systemctl` or `journalctl` as operator steps.
- [ ] **Task 6.3: Full validation and version** (effort: 1)
  - [ ] `python scripts/run_tests.py unit`, `... integration`, `... load`
        (Tasks 4.6 and 5.5 at production shape), mypy over `src` and `test`
        in one invocation, ruff on touched files.
  - [ ] Bump `pyproject.toml` to 0.15.0. Commit.
  - [ ] Success: all tiers green; the branch is ready for the code review the
        PM launches. The host walkthrough (design *Verification Walkthrough*
        steps 1–6) runs after install with `install-production.sh --ref` and
        `mt data migrate apply`; results are recorded in the CHANGELOG.

## Review Response (2026-09-11, tasks review — CONCERNS)

| finding | response |
|---|---|
| F003 no load-tier task for the overview's ten-second bound | Task 4.6 added: `test/load/test_922_overview_nfr.py` over `prod_shaped_db`, database part under five seconds, the other five being the capped EODHD call; Task 4.5's integration case is correctness only. |
| F004 CI gating implicit | Stated in Tasks 4.6, 5.5 and 6.3: this repository has no CI test job (`ci.yml` publishes on tags only); the load tier is gated locally by `scripts/run_tests.py load` with `MT_RUN_LOAD_TESTS=1`, as the 167 case already is. |
| F005 constant rename beyond the design text | Task 4.1 now names the rename as deliberate and records that grep finds no other reference. |
| F006 Tasks 2.4 and 4.3 large | No change; the review's cut lines (minute/daily, gather/build) are the fallback if execution stalls. |
| F007 SC3 two-live-rows case unit-only | No change; the live scenario is walkthrough step 5 in Task 6.3, as the design places it. |
