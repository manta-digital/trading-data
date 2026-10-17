---
docType: tasks
slice: eligible-rollups
project: trading-data
lld: user/slices/172-slice.eligible-rollups.md
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [171, 163, 169]
interfaces: [173]
projectState: >
  Slice 171 is merged and released as v0.24.0; migration 062 (off_tape) is
  applied in production and the history sweep is running there. The minute
  migration chain ends at 063 (test_chain_ends_at_063). The four minute
  rollups and minute_coverage aggregate raw values. Slice 172 was split from
  173 on 2026-10-16: this slice builds the eligible rollups beside the current
  ones; 173 swaps them in and switches the API default. Slice 172 is built:
  migration 063 (the `_next` rollups and coverage, WITH NO DATA), `--target`
  and `--order` on the caggs commands, the cutover script
  scripts/cutover_172_eligible_rollups.py, the docs, and final validation.
  The production cutover runs in Phase 7 after the off-tape sweep finishes
  (`off-tape unevaluated` is zero).
dateCreated: 20261016
dateUpdated: 20261016
status: complete
---

# Tasks: Eligible Rollups

## Context Summary

- Working on slice 172. Slice 171 marks off-tape prints on every minute bar
  (`minute_ohlcv.off_tape`), but the 5m, 15m, 1h and 4h rollups still
  aggregate raw values.
- This slice:
  - adds migration 063: `minute_{5min,15min,hourly,4hour}_ohlcv_next` with
    eligible definitions, and `minute_coverage_next` over the 4h one, all
    `WITH NO DATA`, with refresh and columnstore policies;
  - adds `--target current|next` to `mt data caggs repair`, `verify` and
    `rebuild-coverage`, and `--order newest-first` to `repair`;
  - adds `scripts/cutover_172_eligible_rollups.py`, which releases 063 and
    then builds and verifies the `_next` objects in production.
- **Nothing a reader sees changes.** Bars, `data_status`, `mt data health`,
  freshness probes and the seeder keep reading the current objects.
- **The design is the specification.** Rollup definition, targeting, build
  order and failure table: see the slice design's Technical Decisions.
- Not in this slice: the swap (migration 064), the eligible API default, the
  value-parity health check, NFR re-runs (all slice 173).
- **After merge (Phase 7, not a task here):** the cutover script runs in
  production once `off-tape unevaluated` is zero. Success criterion 5
  (`verify --target next` exits 0 in production) is verified then.
- Next planned slice: 173 (Eligible Rollup Swap).

**Conventions for every task below**

- Run from the repository root. Confirm with `pwd` before file or shell work.
- **Target branch:** read `cf config get git.integration_branch`; empty means
  `main`. Work on `172-slice.eligible-rollups`, forked from the target per the
  Git Rules.
- **Tests never read the production URL.**
- Integration tests need `MT_TIMESCALE_TEST_URL` exported from `.env` (strip
  the quotes). Run the unit, integration and load tiers separately. Never mix
  `test/unit/api_server` and `test/integration/api_server` in one run.
- `ruff format` only the files the task touched. Before each commit, run
  `git diff <target> --stat` and look for deletions in files you did not mean
  to change.
- mypy: one invocation covering the src kalshi paths plus tests, never a
  narrower run (narrower runs report false errors).
- Every name and comparison value (`CAGG_NEXT_SUFFIX`, `CaggTarget`, the
  repair order values) is defined once in `src/manta_trading/constants.py`.
  View names are always derived, never spelled out.
- Commit messages use the project's semantic prefixes and end with the
  attribution lines in force for the session.

---

## Section 1 — Targeting the `_next` views (effort 2/5)

- [x] **1.1 Define the constants**
  - [x] In `constants.py`: `CAGG_NEXT_SUFFIX: Final[str] = "_next"`;
        `CaggTarget` (`StrEnum`: `CURRENT="current"`, `NEXT="next"`);
        `RepairOrder` (`StrEnum`: `OLDEST_FIRST="oldest-first"`,
        `NEWEST_FIRST="newest-first"`). Each has a one-line comment naming
        slice 172.
  - [x] Add one helper beside `GRANULARITY_SOURCE`'s consumers, e.g.
        `target_view_name(view: str, target: CaggTarget) -> str`, returning
        `view` for `CURRENT` and `view + CAGG_NEXT_SUFFIX` for `NEXT`. Every
        `_next` name in this slice goes through it.
  - [x] Success: `ruff` and `mypy` clean.

- [x] **1.2 Resolve rollup views by target**
  - [x] `cagg_parity.cagg_view(granularity, target=CaggTarget.CURRENT)`
        applies the helper. `compute_parity` and `run_repair` take a `target`
        keyword (default `CURRENT`) and pass it to every `cagg_view` call.
        Find the call sites with `grep -rn "cagg_view(" src`; the line
        numbers cited in this file are hints only.
  - [x] `repair`'s pre-flight is unchanged in meaning: check 4 still guards
        `_COVERAGE_INDEX_VIEW` (the **current** 4h rollup). Repairing
        `minute_4hour_ohlcv_next` must not count as repairing the coverage
        index view.
  - [x] Success: with the default target every existing call resolves the
        same names as before.

- [x] **1.3 Resolve the minute coverage family by target**
  - [x] `coverage_rebuild.rebuild_coverage` takes `target` (default
        `CURRENT`). For `NEXT` and `CoverageFamily.MINUTE`: the view is
        `minute_coverage_next`, the source is `minute_4hour_ohlcv_next`, and
        `assert_coverage_index_scheduled` checks the **target's** parent
        (`minute_4hour_ohlcv_next`). The daily family rejects `NEXT` with a
        `ValueError` (there is no `daily_coverage_next`).
  - [x] The verify path of `rebuild-coverage` takes the same target.
  - [x] Success: default behaviour unchanged.

- [x] **1.4 Add `--target` and `--order` to the CLI**
  - [x] In `cli/commands/data.py`: `--target [current|next]` (default
        `current`) on `caggs verify`, `caggs repair` and
        `caggs rebuild-coverage`; `--order [oldest-first|newest-first]`
        (default `oldest-first`) on `caggs repair`. Values come from the 1.1
        enums.
  - [x] `run_repair` takes `order`; `NEWEST_FIRST` reverses the window list
        after it is built. The parity check per window is unchanged.
  - [x] Output and JSON name the resolved view, so a `--target next` run
        visibly acts on `_next` names.
  - [x] Success: `uv run mt data caggs repair --help` lists both options.

- [x] **1.5 Targeting unit tests**
  - [x] `test/unit/market/test_cagg_parity.py`: `cagg_view` for every
        granularity and both targets.
  - [x] `test/unit/market/test_cagg_repair.py`: newest-first window order;
        the coverage-index guard still keys on the current 4h view when the
        target is `minute_4hour_ohlcv_next`.
  - [x] `test/unit/market/test_coverage_rebuild.py`: minute family with
        `NEXT` resolves view, source and parent check; daily with `NEXT`
        raises.
  - [x] `test/unit/cli/commands/test_data_caggs.py`: `--target` and `--order`
        parse, defaults preserved, an invalid value exits non-zero.
  - [x] Success: the unit tier passes.

- [x] **1.6 Commit:** `feat: target _next caggs in repair, verify and rebuild-coverage`

## Section 2 — Migration 063 (effort 3/5)

- [x] **2.1 Render the eligible rollup and coverage SQL**
  - [x] New module `market/schema/eligible_rollups_sql.py` (keep
        `migrations/minute.py` from growing): a function rendering one eligible
        minute rollup `CREATE MATERIALIZED VIEW ... WITH NO DATA` for a
        granularity, named via the 1.1 helper with `NEXT`, from
        `eligible_sql.py`'s expressions as the design's Rollup definition
        shows (`FILTER (WHERE <has eligible>)` on every price and on volume;
        `COUNT(*) AS minute_count` unfiltered). Column names, order and types
        match the current rollups (migration 033's shape, with `time_bucket`
        as the bucket column).
  - [x] Extract the current `minute_coverage` definition (the migration at
        `minute.py` ~2441) into a function taking the view name and parent
        view name. The existing migration calls it with the current names, so
        its SQL is byte-identical; 063 calls it with the `_next` names.
  - [x] Success: a unit test asserts the extracted coverage SQL for the
        current names equals the pre-extraction text; the rollup SQL contains
        no sub-query in `FROM`.

- [x] **2.2 Add migration 063**
  - [x] Append `063_minute_eligible_rollups_next` to `MINUTE_MIGRATIONS`,
        `requires_autocommit: True`, one `execute()` per
        `CREATE MATERIALIZED VIEW`, every statement `IF NOT EXISTS` so a rerun
        converges. Order: the four rollups (5m, 15m, 1h, 4h), then
        `minute_coverage_next`.
  - [x] Add each object's refresh policy with the same constants migration 061
        and slice 169 use for its current twin, and the columnstore policy the
        current minute rollups get (`_setup_minute_cagg_columnstore`;
        parameterize it by view name if it is not already). The `_next` mat
        hypertables use `MINUTE_CAGG_CHUNK_INTERVAL` (repair's pre-flight
        check 2 requires it).
  - [x] Description text: what 063 creates, that it is `WITH NO DATA`, and
        that the build is `scripts/cutover_172_eligible_rollups.py`.
  - [x] Bump the chain-tip test: `test_chain_ends_at_062` →
        `test_chain_ends_at_063` and the migration count 65 → 66
        (`test/unit/test_schema_migrations.py`).
  - [x] Success: `uv run mt data migrate apply` on the test database applies
        063, and a second run is a no-op.

- [x] **2.3 Migration 063 integration tests**
  - [x] New `test/integration/test_migration_063_eligible_rollups_next.py`:
    1. cold start: all five `_next` objects exist, empty, each with a refresh
       and a columnstore job, chunk interval `MINUTE_CAGG_CHUNK_INTERVAL`;

       minute_coverage_next mirrors minute_coverage: refresh policy only, no columnstore, and its twin's chunk interval; the four rollups have both jobs at MINUTE_CAGG_CHUNK_INTERVAL.
    2. populated: on a database holding the current rollups with data, 063
       leaves every current object's row count and definition unchanged;
    3. rerun of 063 after a partial apply (drop one `_next` object, rerun)
       converges.
  - [x] Eligible-value tests on fixture minutes, after refreshing the `_next`
        rollups:
    1. a 5m bucket with one all-marked minute: prices and volume exclude it,
       `minute_count` counts it;
    2. a 5m bucket of only all-marked minutes: the row exists with NULL
       `open/high/low/close/volume` and full `minute_count`;
    3. a bar marked HIGH only: the bucket high is the clamped eligible high;
    4. eligible 1m bars (171's `get_minute_data(prints=eligible)` path) rolled
       up to 5m in SQL equal `minute_5min_ohlcv_next` row for row, volume
       included.
  - [x] Load 171's off-tape fixtures (`test/fixtures/off_tape/`), mark them
        with 171's `mark_days`, refresh, and assert each print is absent from
        the `_next` 5m and 1h rows (slice success criterion 1):
    1. `SPY_2026-09-08.csv`: 770.19 and 770.3516 highs, 724.228 lows;
    2. `SPY_2026-09-11.csv`: the 17:33 ET 787.5169 high;
    3. `AVGO_2026-06-03.csv`: the 16:15 ET 295.4665 low;
    4. `MSFT_2026-08-05.csv`: the 16:13 ET 384.28 low;
    5. `MSFT_2026-06-01.csv` (the walkthrough's case): the after-hours 5m max
       high from 16:40 ET on is lower in `minute_5min_ohlcv_next` than in a
       raw rollup of the same minutes, and equals the eligible 1m max for
       that span.
  - [x] Success: the new file passes; the existing migration tests pass apart
        from the known failures (`test_migration_051_052` ×2).

- [x] **2.4 Commit:** `feat: add migration 063 eligible _next rollups`

## Section 3 — Targeting against real `_next` views (effort 2/5)

- [x] **3.1 Targeting integration tests**
  - [x] Extend `test/integration/test_coverage_rebuild_sweep.py` and add
        `test/integration/test_cagg_repair_target_next.py` (fixture minutes
        over at least three 70-day windows):
    1. `repair --target next --order newest-first` on each `_next` rollup
       (policies paused by the test) brings it to parity; the newest window
       is rebuilt first (progress output order);
    2. `verify --target next` exits 0 afterwards and non-zero before;
    3. `rebuild-coverage --family minute --target next` fills
       `minute_coverage_next`; `--verify` passes;
    4. throughout, every current object's row count, policy `scheduled` flag
       and definition are unchanged;
    5. with `minute_4hour_ohlcv_next`'s refresh paused,
       `rebuild-coverage --target next` refuses naming that view.
  - [x] Readers are unaffected with `_next` partly built (slice success
        criterion 3). Capture each reader's output before 063, then again
        after 063 plus a partial `repair --target next` of one window, and
        assert equality:
    1. bars: `get_minute_data` at 5m and 4h for one fixture symbol-day;
    2. `data_status`: `SELECT * FROM data_status` for the fixture symbols;
    3. seeder: `build_minute_coverage_index` and
       `compute_missing_minute_sessions` (`data/gaps/minute_coverage.py`);
    4. `mt data health`: same verdicts, no `_next` row.

    `/api/v1/status` reads `data_status`, so item 2 covers it. Also assert
    `mt data caggs status` lists the five `_next` views.
  - [x] Success: the new tests pass.

- [x] **3.2 Commit:** `test: cover repair, verify and coverage against _next caggs`

## Section 4 — Cutover script (effort 3/5)

- [x] **4.1 Release steps**
  - [x] `scripts/cutover_172_eligible_rollups.py <ref>`, modelled on
        `scripts/cutover_921_minute_sessions.py`, using `cutover_common`
        (`preflight`, `hold_timer`/`release_timer`, `install`, `migrate`,
        `restart_serve_if_active`, `start_log`). Steps: preflight → hold
        timers → install → `verify_migrate_target` → apply 063 → restart
        mt-serve → release timers. Each step check-then-act, so a rerun skips
        what is done (063 already applied, version already installed).
  - [x] Success: `--help` lists the steps; the module docstring lists every
        step and its failure behaviour, as 921's does.

- [x] **4.2 Build step**
  - [x] Refuse while `off-tape unevaluated` is non-zero, reusing
        `health_off_tape.fetch_off_tape_counts`; the message gives the count
        and says the history sweep must finish first.

    Done with the sweep's own check (`off_tape_sweep.unevaluated_chunks`,
    every chunk) instead of `fetch_off_tape_counts`, which counts one session
    only and reads zero while older history is still unswept.

  - [x] Measure free space on the database volume (`SHOW data_directory`,
        then `shutil.disk_usage`); fail with a clear message if either read
        fails. Pass the value as `--assume-headroom-gb`.

    `SHOW data_directory` is denied to the migration role in production (no
    `pg_read_all_settings`), so the data directory comes from `pg_lsclusters`
    for the URL's port, and the script refuses if the database host is not
    this machine.

  - [x] For 4h, 1h, 15m, 5m in that order: resolve the `_next` view's jobs
        from the catalog by name; pause them; run
        `mt data caggs repair --target next --order newest-first
        --granularity <g>`; resume them. Log each pause and resume with the
        job ids.

    No verify before each repair: repair skips windows at parity itself, and
    a whole-table verify per rollup would add four raw scans.

  - [x] On start, resume any `_next` job left paused by a killed run, and log
        it.
  - [x] Then pause `minute_coverage_next`'s jobs, run
        `rebuild-coverage --family minute --target next`, resume, run
        `rebuild-coverage --family minute --target next --verify`.
  - [x] Finally `mt data caggs verify --target next --granularity all`; a
        non-zero exit fails the script naming the view.
  - [x] Success: every step skips cleanly when already done (parity-derived).

- [x] **4.3 Report**
  - [x] Write the report to `project-documents/user/notes/` (the
        `cutover_common` convention): each step's outcome, rows per `_next`
        view, verify output, and each `_next` refresh job's last run duration
        against its schedule interval.
  - [x] Exit 0 only when every check passed.

- [x] **4.4 Cutover script tests**
  - [x] `test/unit/test_cutover_172.py`, modelled on
        `test/unit/test_cutover_921.py` (subprocess and database calls
        mocked):
    1. build refused while unevaluated > 0;
    2. headroom read failure stops before any repair;
    3. order is 4h, 1h, 15m, 5m, then coverage, then verify;
    4. every pause is followed by a resume, including when repair fails
       (the resume still runs and the script exits non-zero);
    5. a leftover paused `_next` job is resumed at start;
    6. a failing verify exits non-zero naming the view;
    7. rerun skips: with 063 already applied and the ref already installed,
       the release steps do nothing and the build step proceeds.
  - [x] Success: the new tests pass.

- [x] **4.5 Commit:** `feat: add slice 172 cutover and eligible rollup build`

## Section 5 — Documentation (effort 1/5)

- [x] **5.1 Runbook 100 and CHANGELOG**
  - [x] Runbook 100: a section for the 172 cutover. When to run it (after
        `off-tape unevaluated` is zero), the one command, how to read the
        report, how to rerun after a failure, and that the five `_next` rows
        in `mt data caggs status` are expected until slice 173. Name views and
        jobs by name, never by job id.
  - [x] `--target` and `--order`: document them where runbook 310 and the
        repair runbook describe `rebuild-coverage` and `repair`.
  - [x] CHANGELOG: an entry under the next version (patch: no reader-visible
        change).
  - [x] Success: every command in the runbook section runs as written on the
        test database.

- [x] **5.2 Commit:** `docs: document the eligible rollup build`

## Section 6 — Final validation (effort 1/5)

- [x] **6.1 Run the gates**
  - [x] `ruff check` and `ruff format --check` on the touched files; mypy per
        the conventions.
  - [x] Unit tier passes.
  - [x] Integration tier: only the known failures remain
        (`test_cli_lists` priority1 ×2, `test_migration_051_052` ×2,
        `test_policy_advances_head` unaided ×2).

    test_cold_start's refresh-policy count failed once 063 added five policies;
    fixed in 3dee602 by adding the _next views to its expected caggs.

  - [x] The load tier is not run: this slice changes no read path.
  - [x] Success: all of the above, with any other failure re-run in
        isolation before it is investigated.

- [x] **6.2 Record results in the design**
  - [x] Update the slice design's Verification Walkthrough with the commands
        as actually run on the test database and their output summaries.
  - [x] Set the design's and this file's `status` to `complete`.

- [x] **6.3 Commit:** `docs: record 172 verification and complete slice`
