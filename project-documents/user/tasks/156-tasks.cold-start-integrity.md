---
docType: tasks
slice: 156-cold-start-integrity
project: trading
lld: user/slices/156-slice.cold-start-integrity.md
dependencies: []
projectState: >
  Slice 154 complete. Issue #16 filed 2026-05-08 after a fresh `trading`
  DB on 144 could not be migrated (UndefinedTable: acquisition_state on
  migration 019). `trading_test` continues to work because it predates
  the deletion. Design 156 specifies four pieces: (1) fixup migration
  038 for acquisition_state, (2) `mt data init` CLI, (3) cold-start
  integration test, (4) fold `timescale_init.py` into the migration
  chain as 001a/b/c/d and delete the file. After this slice the
  migration list is the single source of schema truth and prod cutover
  is unblocked. Branch: `156-slice.cold-start-integrity` (create from
  main).
dateCreated: 20260508
dateUpdated: 20260509
status: complete
---

## Context Summary

- Slice 156 fixes a cold-start regression and folds the legacy
  `timescale_init.py` into the migration chain in one slice. Bundled
  because the integration test that gates regression-prevention is
  also the verification mechanism for the fold.
- Audit confirms `acquisition_state` is the only missing CREATE; tasks
  do not need to discover others.
- Migration list ordering is by Python list iteration, not numeric
  sort of the id. New migrations are inserted by *position* into
  `MINUTE_MIGRATIONS`. Comment block in the list documents this
  convention.
- All new migrations use `IF NOT EXISTS` (or Timescale's `if_not_exists
  => TRUE`) so they are no-ops on existing DBs.
- Verification path on `trading_test`: `pg_dump --schema-only` before
  the slice → land the slice → `pg_dump --schema-only` again → diff
  must be empty.
- Full design specs in `156-slice.cold-start-integrity.md`. This file
  is implementation tasks only.

---

## Tasks

- [x] **T01 — Branch setup**
  - [x] Verify `main` is current and clean: `git status` and `git
    branch --show-current`
  - [x] Create branch: `git checkout -b 156-slice.cold-start-integrity`
  - [x] Success: clean branch from current main

- [x] **T02 — Pre-flight: capture trading_test schema snapshot**
  - [x] Choose a durable working directory (NOT `/tmp` — survives
    across reboots and tmpfs cleanup). Suggest
    `~/.cache/manta-trading/156/` or a `var/` dir under the repo
    root that's gitignored.
  - [x] Capture: `pg_dump -h <db-host> -U postgres --schema-only
    --no-owner --no-privileges trading_test >
    <workdir>/trading_test.before.sql`
  - [x] Document the chosen workdir at the top of this task list (or
    in a one-line shell note) so T11 and T23 reference the same
    location.
  - [x] Commit nothing — these files are verification scaffolding,
    not project state.
  - [x] Success: file exists, non-empty, contains `acquisition_state`
    table definition

### Piece 1: fixup migration for `acquisition_state`

- [x] **T03 — Add migration 038 (`038_create_acquisition_state`)**
  - [x] Open `src/manta_trading/market/schema/migrations/minute.py`
  - [x] Locate the `MINUTE_MIGRATIONS` list entry for
    `019_slim_acquisition_state`
  - [x] Insert a new entry **immediately before it** (list-position
    matters; numeric id does not drive runner order):
    - id: `038_create_acquisition_state`
    - description: explains it restores a CREATE that was deleted by
      slice 152's demolition; idempotent
    - sql: `CREATE TABLE IF NOT EXISTS acquisition_state` with the
      post-030 column shape from the design doc (symbol, granularity,
      provider, last_attempt_ts, updated_at, last_attempt_outcome,
      PRIMARY KEY)
  - [x] Add an inline comment above the entry: "Position-critical:
    must run before 019_slim_acquisition_state. Do not reorder this
    list alphabetically by id."
  - [x] Success: list now contains 038 directly before 019

- [x] **T04 — Test: migration 038 idempotency and ordering**
  - [x] In `test/unit/test_schema_migrations.py`, bump the
    `test_migration_count` assertion to the new total
  - [x] Bump `_mock_db` default `num_extra_conns` to cover the new
    list size
  - [x] Add a new test: `test_038_precedes_019` — assert that the list
    index of `038_create_acquisition_state` is less than the list
    index of `019_slim_acquisition_state`
  - [x] Add a new test: `test_038_uses_if_not_exists` — assert `IF
    NOT EXISTS` substring is present in the migration's SQL
  - [x] Run: `uv run --extra dev pytest test/unit/test_schema_migrations.py`
  - [x] Success: tests pass

- [x] **T05 — Verify migration 038 against trading_test (idempotent
  no-op)**
  - [x] `MT_TIMESCALE_DB_URL=postgresql://...trading_test uv run mt
    data migrate apply`
  - [x] Confirm output reports migration 038 as applied
  - [x] `\d acquisition_state` in psql; column count and shape
    unchanged from T02 baseline
  - [x] Re-run `mt data migrate apply`; confirm 0 applied
  - [x] Success: no schema drift on existing DB

### Piece 4 (run before piece 2 to keep `mt data init` simple): fold `timescale_init.py` into migrations

- [x] **T06 — Add migration 001a (`001a_create_timescaledb_extension`)**
  - [x] Insert into `MINUTE_MIGRATIONS` *immediately before*
    `002_instruments` (and after `001_schema_migrations`)
  - [x] sql: `CREATE EXTENSION IF NOT EXISTS timescaledb`
  - [x] description: explains this is the migration-chain replacement
    for the old `timescale_init.create_timescaledb_extension`
  - [x] Inline comment: "Position-critical front-of-list block
    (001a/b/c/d). Replaces deleted timescale_init.py. Do not reorder."
  - [x] Success: list contains 001a directly before 002

- [x] **T07 — Add migration 001b (`001b_create_minute_ohlcv`)**
  - [x] Insert immediately after 001a
  - [x] sql: `CREATE TABLE IF NOT EXISTS minute_ohlcv (...)` with
    column shape copied verbatim from `timescale_init.create_minute_ohlcv_table`
  - [x] Success: matches existing `minute_ohlcv` table shape on
    `trading_test` byte-for-byte after restore

- [x] **T08 — Add migration 001c (`001c_create_minute_ohlcv_hypertable`)**
  - [x] Insert immediately after 001b
  - [x] sql: `SELECT create_hypertable('minute_ohlcv', 'time',
    chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE)`
  - [x] Use `if_not_exists => TRUE` so existing hypertable is a no-op
  - [x] Success: idempotent against `trading_test`

- [x] **T09 — Add migration 001d (`001d_create_minute_ohlcv_indexes`)**
  - [x] Insert immediately after 001c
  - [x] sql: each `CREATE INDEX IF NOT EXISTS` from
    `timescale_init.create_indexes` (4 indexes per the current source:
    `ix_minute_ohlcv_symbol_time`, `ix_minute_ohlcv_time_symbol`,
    `minute_ohlcv_time_idx` [auto-created by hypertable; check before
    duplicating], `ux_minute_ohlcv_symbol_time` UNIQUE)
  - [x] Verify against `\di` on `trading_test` after T05 to confirm
    the canonical set
  - [x] Success: list ends with 001d directly before 002

- [x] **T10 — Test: front-of-list ordering and idempotency markers**
  - [x] Bump `test_migration_count` in
    `test/unit/test_schema_migrations.py` for the new total (036 + 1
    fixup + 4 init-fold = 41)
  - [x] Add `test_001abcd_precedes_002` — assert all four 001x
    migrations come before `002_instruments` in list order
  - [x] Add `test_001abcd_use_if_not_exists` — assert each of the four
    has the appropriate idempotency marker
  - [x] Run: `uv run --extra dev pytest test/unit/test_schema_migrations.py`
  - [x] Success: tests pass

- [x] **T11 — Verify init-fold migrations against trading_test
  (idempotent no-op)**
  - [x] `MT_TIMESCALE_DB_URL=postgresql://...trading_test uv run mt
    data migrate apply`
  - [x] Confirm all four 001x migrations report applied
  - [x] Confirm 0 row mutations on `minute_ohlcv` (compare row count
    pre/post)
  - [x] `pg_dump -h ... --schema-only --no-owner --no-privileges
    trading_test > <workdir>/trading_test.after_fold.sql`
  - [x] `diff <workdir>/trading_test.before.sql <workdir>/trading_test.after_fold.sql`
    — expect only the four new rows in `schema_migrations` table data
    (which is *not* in --schema-only output, so diff should be empty)
  - [x] Success: empty diff or only ordering-of-comments differences

- [x] **T12 — Delete `timescale_init.py` and audit callers**
  - [x] Grep src for any import of `timescale_init` or
    `TimescaleDBInitializer`: `grep -rn "timescale_init\|TimescaleDBInitializer" src/ test/`
  - [x] Update or remove all callers (any test that imported
    `TimescaleDBInitializer` is now exercising migrations directly —
    simplest fix is deletion of those tests if they only validated
    init's internals)
  - [x] Delete file: `git rm src/manta_trading/market/timescale_init.py`
  - [x] Run full test suite: `uv run --extra dev pytest test/unit`
  - [x] Success: 0 references remain; tests pass

- [x] **T13 — Commit: piece 4 (init-fold) checkpoint**
  - [x] `uv run --extra dev pytest test/unit` — all pass
  - [x] `uv run ruff check src/ test/` — no new errors in changed
    files (pre-existing lint not in scope)
  - [x] Commit: `refactor: fold timescale_init.py into migration chain`
  - [x] Success: clean checkpoint with init folded; `trading_test`
    untouched

### Piece 2: `mt data init` CLI

> Ordering note: piece 2 runs *after* piece 4 (init-fold) even though
> the design's "Approach" section listed it as piece 2. Reason:
> post-fold, `mt data init` is a one-line wrapper around
> `apply_schema_migrations()`. If implemented before piece 4, it
> would need a transient two-phase implementation that gets ripped
> out immediately. Pieces still ship in one slice — only the
> implementation order swaps.

- [x] **T14 — Implement `mt data init` command**
  - [x] Add `init` command to `data_app` in
    `src/manta_trading/cli/commands/data.py`
  - [x] Behavior: instantiate `TimescaleMinuteDataDB`, call
    `apply_schema_migrations()`, print a Rich table with one row
    showing migrations-applied count
  - [x] Flag: `--validate-only` — call `migrate_status` equivalent and
    exit without applying
  - [x] Flag: `--yes` — accepted but currently a no-op (reserved for
    future destructive operations)
  - [x] Error path: missing `MT_TIMESCALE_DB_URL` → `print_error` and
    `typer.Exit(1)` (mirror existing pattern in `data.py`)
  - [x] Update `data_app` `--help` so `init` is listed near
    `migrate apply` for discoverability
  - [x] Success: `mt data init --help` documents flags; runs against
    a configured DB

- [x] **T15 — Test: `mt data init`**
  - [x] Unit test: missing URL → non-zero exit + clear error
  - [x] Unit test: `--validate-only` does not call
    `apply_schema_migrations`
  - [x] Unit test: default invocation calls
    `apply_schema_migrations` exactly once
  - [x] Run: `uv run --extra dev pytest
    test/unit/cli/commands/test_data_init.py` (new file)
  - [x] Success: tests pass

- [x] **T16 — Commit: piece 2 (CLI) checkpoint**
  - [x] `uv run --extra dev pytest test/unit` — all pass
  - [x] Commit: `feat(cli): add mt data init for one-step cold-start`
  - [x] Success: clean checkpoint

### Piece 3: cold-start integration test

- [x] **T17 — Implement integration test fixture for ephemeral DB**
  - [x] New file: `test/integration/test_cold_start.py`
  - [x] Module-level skip: `pytest.mark.skipif(not
    os.environ.get("MT_TIMESCALE_TEST_URL"), reason=...)`
  - [x] Fixture `ephemeral_db`: connects to admin URL with
    `autocommit=True`, creates a UUID-named database, yields its URL,
    drops the database on teardown (must terminate live connections
    first via `pg_terminate_backend`)
  - [x] Fixture is `function`-scoped so each test gets a fresh DB
  - [x] Success: pytest collects the fixture; it works with a local
    Timescale container

- [x] **T18 — Implement positive test: cold-start produces working
  schema**
  - [x] Test name: `test_cold_start_produces_working_schema`
  - [x] Body: invoke `mt data init` against the ephemeral DB via
    `subprocess.run` (full CLI path) or via
    `TimescaleMinuteDataDB.apply_schema_migrations` (faster)
  - [x] Assertions:
    - [x] `schema_migrations` row count equals `len(MINUTE_MIGRATIONS)`
    - [x] Every expected table exists (parametrize over the manifest:
      `instruments`, `provider_symbol_mapping`, `trading_calendars`,
      `trading_holidays`, `acquisition_state`, `backfill_state`,
      `data_gaps`, `daily_ohlcv`, `trading_sessions`, `splits`,
      `dividends`, `minute_ohlcv`)
    - [x] All 7 cagg materialized views exist
    - [x] All 7 cagg refresh policies installed (query
      `timescaledb_information.jobs` joined on view name)
    - [x] `data_status` view returns 0 rows (empty `instruments`) without
      error
  - [x] Success: test passes against a fresh ephemeral DB

- [x] **T19 — Implement negative test: missing CREATE detected**
  - [x] Test name: `test_deleted_create_migration_fails_clearly`
  - [x] Body: monkeypatch `MINUTE_MIGRATIONS` to remove the
    `038_create_acquisition_state` entry; run cold-start against
    ephemeral DB; assert it fails on a migration that references
    `acquisition_state` with a clear error
  - [x] Restore the list after the test (monkeypatch teardown handles
    this automatically when using `pytest.MonkeyPatch`)
  - [x] Success: test passes by catching the regression class

- [x] **T20 — Verify integration tests are runnable; document env**
  - [x] Confirm test passes locally with
    `MT_TIMESCALE_TEST_URL=postgresql://postgres:<password>@<db-host>:5432/postgres`
    (admin connection)
  - [x] Verify test cleans up: list databases before and after the
    run; no stragglers
  - [x] Document the env var in `README.md` (or a `TESTING.md` if
    that exists) under an "Integration tests" section
  - [x] **CI wiring is out of scope for this slice.** This repo has
    no CI yet; issue #17 tracks the bootstrap. Once #17 lands, this
    test will be picked up automatically by the integration job.
  - [x] Success: full integration suite green; cleanup verified

- [x] **T21 — Commit: piece 3 (integration test) checkpoint**
  - [x] `uv run --extra dev pytest test/unit test/integration` — all
    pass
  - [x] Commit: `test: add cold-start integration test`
  - [x] Success: clean checkpoint

### End-to-end verification

- [x] **T22 — End-to-end fresh DB cold-start (the unblock workflow)**
  - [x] `PGPASSWORD=manta dropdb -h <db-host> -U postgres trading
    --if-exists`
  - [x] `PGPASSWORD=manta createdb -h <db-host> -U postgres trading`
  - [x] `MT_TIMESCALE_DB_URL=postgresql://...trading uv run mt data
    init`
  - [x] `MT_TIMESCALE_DB_URL=postgresql://...trading uv run mt data
    migrate status` — every row `applied`
  - [x] `MT_TIMESCALE_DB_URL=postgresql://...trading uv run mt data
    caggs status` — 7 caggs, all `policy: yes`, all `current` (empty
    source)
  - [x] Success: fresh DB cold-start works in zero manual steps

- [x] **T23 — Final schema diff vs trading_test**
  - [x] If `<workdir>/trading_test.before.sql` from T02 is missing,
    re-capture it now: `pg_dump ... trading_test >
    <workdir>/trading_test.before.sql`
  - [x] `pg_dump -h <db-host> -U postgres --schema-only --no-owner
    --no-privileges trading > <workdir>/trading.after.sql`
  - [x] `diff <workdir>/trading_test.before.sql <workdir>/trading.after.sql`
  - [x] Expected: differences only in object identifiers that
    legitimately differ (chunk names, hypertable ids), nothing in
    user-table column shapes
  - [x] Success: schema parity confirmed

- [x] **T24 — Update README and prod-cutover documentation**
  - [x] Replace any `python -m manta_trading.market.timescale_init`
    references with `mt data init`
  - [x] Add a "Setting up a new database" section to `README.md` that
    documents: `createdb` → set `MT_TIMESCALE_DB_URL` → `mt data init`
  - [x] Success: docs reference only the supported path

- [x] **T25 — Bump version, close issue #16, push branch**
  - [x] `pyproject.toml` version bump (0.3.3 → 0.4.0; this is a
    structural refactor that justifies a minor bump)
  - [x] `uv sync` to update lockfile
  - [x] Final commit: `chore: bump version to 0.4.0` (or fold into a
    closing commit referencing #16)
  - [x] Push branch: `git push -u origin 156-slice.cold-start-integrity`
  - [x] Reference issue #16 in PR body or final commit message so it
    auto-closes on merge
  - [x] Success: branch pushed; #16 will close on merge

---

## Verification checklist (drawn from the design's verification
walkthrough — operator runs these at the end)

- [x] Bug-before reproduction (design §A) NO LONGER reproduces post-
  slice — fresh DB + `mt data migrate apply` (without `init`) still
  works because the init-fold migrations 001a/b/c/d run first.
- [x] Fresh DB success (design §B) — `mt data init` produces a
  working DB.
- [x] Existing DB unaffected (design §C) — `trading_test` row counts
  and schemas unchanged.
- [x] Integration tests in CI (design §D) — pass.
- [x] Prod cutover workflow (design §E) — `mt data init` followed by
  `instruments rebuild` and `pull` works end-to-end against a fresh
  prod DB on 144.
