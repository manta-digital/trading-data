---
docType: task-breakdown
slice: 131
sliceName: unified-schema-migration-tracking-across-both-databases
parent: user/slices/131-slice.unified-schema-migration-tracking-across-both-databases.md
project: trading
dateCreated: 20260422
dateUpdated: 20260502
status: complete
renumberedFrom: 150
---

# Tasks: Slice 131 — Unified Schema Migration Tracking Across Both Databases

## Context

The minute DB already has a Python-defined migration framework
(`src/manta_trading/market/schema/migrations.py`) with migrations 001–009
applied and tracked in a `schema_migrations` table. The daily DB has no
tracking table and no migration runner. This slice extends the framework to
cover both DBs, adds a `mt data migrate status` CLI command, and deletes
orphaned on-disk SQL files that are not part of the tracked system.

Key source files:
- `src/manta_trading/market/schema/migrations.py` — current flat migration list
- `src/manta_trading/market/schema/seed_calendar.py` — seed helpers (unchanged)
- `src/manta_trading/market/timescale_minute_db.py` — contains `apply_schema_migrations()` to be refactored
- `src/manta_trading/market/marketdb.py` — daily DB class; needs `apply_schema_migrations()`
- `src/manta_trading/cli/commands/data.py` — CLI entry points
- `test/unit/test_schema_migrations.py` — existing unit tests to update
- `test/integration/test_schema_integration.py` — existing integration tests to update
- `database/migrations/*.sql`, `sql/01_setup_database.sql` — orphans to delete

---

## Task 1: Restructure migrations module into a package

- [x] 1.1 Create directory `src/manta_trading/market/schema/migrations/`
- [x] 1.2 Move content of `migrations.py` into `migrations/minute.py`; update module docstring to reflect it is the minute-DB track; rename the top-level constant from `MIGRATIONS` to `MINUTE_MIGRATIONS`
- [x] 1.3 Create `migrations/daily.py` with `DAILY_MIGRATIONS: list[dict[str, str]]` containing exactly two entries:
  - `001_schema_migrations` — same `CREATE TABLE IF NOT EXISTS schema_migrations (...)` SQL as the minute track
  - `002_reconcile_existing_schema` — `SELECT 1;` with description `"Reconciliation marker: schema inherited from pre-migration state"`
- [x] 1.4 Create `migrations/__init__.py` that:
  - Imports `MINUTE_MIGRATIONS` from `.minute` and `DAILY_MIGRATIONS` from `.daily`
  - Exposes `TRACKS: dict[str, list[dict[str, str]]] = {"minute": MINUTE_MIGRATIONS, "daily": DAILY_MIGRATIONS}`
  - Re-exports `MIGRATIONS = MINUTE_MIGRATIONS` as a backward-compatibility alias (with a deprecation comment)
- [x] 1.5 Delete the old `src/manta_trading/market/schema/migrations.py` flat file
- [x] 1.6 Verify: `from manta_trading.market.schema.migrations import MIGRATIONS` still resolves (backward compat); `from manta_trading.market.schema.migrations import TRACKS` resolves; `TRACKS["minute"]` returns the same list as the old `MIGRATIONS`

## Task 2: Extract runner into standalone module

- [x] 2.1 Create `src/manta_trading/market/schema/runner.py` with two public functions:

  ```python
  def apply_migrations(pool: ConnectionPool, migrations: list[dict[str, str]]) -> list[str]:
      """Apply pending migrations; return IDs of newly applied ones."""

  def list_migration_state(
      pool: ConnectionPool, migrations: list[dict[str, str]]
  ) -> dict[str, list[dict[str, str]]]:
      """Return {"applied": [...], "pending": [...]} for the given track."""
  ```

- [x] 2.2 Move the bootstrap + apply logic from `TimescaleMinuteDataDB.apply_schema_migrations()` into `runner.apply_migrations()`. The body should be identical to the existing implementation; only the connection source changes from `self._ensure_pool()` to the passed `pool` argument.
- [x] 2.3 Implement `runner.list_migration_state()`:
  - If `schema_migrations` table does not exist: all migrations are `pending`, `applied` is empty
  - Otherwise: SELECT all rows (migration_id, description, applied_at); compute pending as entries in `migrations` not in the applied set
  - Each entry in `applied` dict list: `{id, description, applied_at}` (applied_at as ISO string)
  - Each entry in `pending` dict list: `{id, description}`
- [x] 2.4 Test — unit tests for `runner.py` (use a real psycopg3 pool against the test TimescaleDB):
  - [x] 2.4a `test_apply_migrations_bootstrap`: starting from no `schema_migrations` table, `apply_migrations` creates it and records the bootstrap migration
  - [x] 2.4b `test_apply_migrations_idempotent`: running `apply_migrations` twice applies each migration only once
  - [x] 2.4c `test_apply_migrations_partial_resume`: given a DB with migrations 001–003 already applied, `apply_migrations` applies only 004+ and returns only the new IDs
  - [x] 2.4d `test_list_migration_state_no_table`: when `schema_migrations` does not exist, all entries are pending
  - [x] 2.4e `test_list_migration_state_partial`: when some migrations are applied, applied and pending sets are correctly split

## Task 3: Update TimescaleMinuteDataDB to delegate to runner

- [x] 3.1 In `timescale_minute_db.py`, replace the body of `apply_schema_migrations()` with a one-liner that calls `runner.apply_migrations(self._ensure_pool(), TRACKS["minute"])`
- [x] 3.2 Add a `list_migration_state()` method to `TimescaleMinuteDataDB` that calls `runner.list_migration_state(self._ensure_pool(), TRACKS["minute"])` and returns the result
- [x] 3.3 Test — update `test/unit/test_schema_migrations.py` and `test/integration/test_schema_integration.py`:
  - [x] 3.3a Confirm all existing tests still pass without change (the public signature is unchanged)
  - [x] 3.3b Add a test for `list_migration_state()` via `TimescaleMinuteDataDB` returning the correct applied/pending split after a fresh migration run
  - [x] 3.3c Integration test `test_minute_track_full_run_matches_tracks_constant`: against the test TimescaleDB, drop `schema_migrations` (or use a clean DB fixture), call `apply_schema_migrations()`, then `SELECT migration_id FROM schema_migrations ORDER BY migration_id` and assert the result equals `[m["id"] for m in TRACKS["minute"]]` exactly
- [x] **Checkpoint commit:** `refactor(schema): extract migration runner and split tracks into package` — covers Tasks 1–3

## Task 4: Add apply_schema_migrations to MarketDB (daily track)

- [x] 4.1 In `src/manta_trading/market/marketdb.py`, add method `apply_schema_migrations() -> list[str]` that calls `runner.apply_migrations(pool, TRACKS["daily"])` using the MarketDB's psycopg3 connection pool
- [x] 4.2 Add method `list_migration_state() -> dict` that calls `runner.list_migration_state(pool, TRACKS["daily"])`
- [x] 4.3 Inspect and adapt MarketDB's connection pattern:
  - [x] 4.3a Inspect `MarketDB` and record one of two findings inline in `marketdb.py` as a comment: (i) already uses a psycopg3 `ConnectionPool` compatible with `runner.apply_migrations()` — no adaptation needed; (ii) uses a different pattern (single connection, psycopg2 legacy, etc.) — requires adaptation
  - [x] 4.3b If (i): Task 4.1/4.2 pass the existing pool directly; mark 4.3b complete. If (ii): either (a) widen `runner.apply_migrations()` to accept a `Connection` or callable producing one, with a narrow protocol/typing that covers both pool and connection sources, or (b) introduce a minimal `ConnectionPool` wrapper for MarketDB. Success criterion: `MarketDB.apply_schema_migrations()` and `MarketDB.list_migration_state()` run successfully against the test daily DB and the runner module is not duplicated
- [x] 4.4 Test — integration test against the test daily DB (`MT_MARKET_DB_URL`):
  - [x] 4.4a `test_daily_apply_creates_tracking_table`: running `apply_schema_migrations()` creates `schema_migrations` table and records both entries
  - [x] 4.4b `test_daily_reconcile_is_noop`: row counts in `symbol_list` (or any pre-existing daily table) are unchanged before and after running the daily track
  - [x] 4.4c `test_daily_list_state_after_apply`: `list_migration_state()` shows 2 applied, 0 pending after a run
- [x] **Checkpoint commit:** `feat(schema): add daily-DB migration track with reconciliation marker` — covers Task 4

## Task 5: Extend CLI — mt data migrate

- [x] 5.1 Add `--db` option to the existing `data_migrate` command in `data.py`:
  - Type: `str`, choices enforced: `minute`, `daily`, `all`; default `all`
  - Help text: `"Database track to migrate. Default: all."`
- [x] 5.2 Confirm the existing `--json` flag on `data_migrate` is preserved; if missing for any reason, add it back (the slice design specifies `mt data migrate [--db ...] [--json]`)
- [x] 5.3 Update `data_migrate` dispatch logic:
  - `minute`: call `TimescaleMinuteDataDB.apply_schema_migrations()`; error if `timescale_db_url` is unset
  - `daily`: call `MarketDB.apply_schema_migrations()`; error if `market_db_url` is unset
  - `all`: run both; if one URL is unset, skip that track with a printed warning (not a non-zero exit)
- [x] 5.4 Update human output to show track name prefix per line: `[minute] Applied: 010_trading_sessions`; summary line: `minute: 1 applied, daily: 0 applied`
- [x] 5.5 JSON output shape: `{"tracks": {"minute": {"applied": [...]}, "daily": {"applied": [...]}}}`
- [x] 5.6 Test — CLI unit tests (use Click/Typer test runner):
  - [x] 5.6a `test_migrate_all_runs_both_tracks`: `--db all` invokes both DB methods
  - [x] 5.6b `test_migrate_specific_track`: `--db minute` invokes only the minute method
  - [x] 5.6c `test_migrate_missing_url_all`: when one URL is unset with `--db all`, exits zero with a warning line
  - [x] 5.6d `test_migrate_missing_url_specific`: when URL is unset with `--db daily`, exits non-zero
  - [x] 5.6e `test_migrate_json_output`: `--json` produces the shape defined in 5.5 (top-level `tracks` key, nested per-track `applied` lists)

## Task 6: Add CLI — mt data migrate status

- [x] 6.1 Add a `status` subcommand under `data_migrate` (or as `data_migrate_status`) in `data.py`:
  - `mt data migrate status [--db minute|daily|all] [--json]`
  - Calls `list_migration_state()` on the relevant DB class(es)
- [x] 6.2 Rich table output (one table per track):
  - Columns: `ID | Status | Description | Applied At`
  - `Status` values: `applied` (green) or `pending` (yellow)
  - Footer line per track: `minute: N applied, M pending`
- [x] 6.3 JSON output matches the shape defined in the slice design (D7): `{"tracks": {"minute": {"connected": true, "applied": [...], "pending": [...]}, "daily": {...}}}`
- [x] 6.4 If a DB URL is unset: `connected: false`, `error: "URL not configured"`, no crash
- [x] 6.5 Test — CLI unit tests:
  - [x] 6.5a `test_status_json_shape`: `--json` output contains top-level `tracks` key with `minute` and `daily` sub-keys each having `applied` and `pending` lists
  - [x] 6.5b `test_status_missing_url`: unset URL results in `connected: false` entry, not an exception
  - [x] 6.5c `test_status_pending_shown`: a DB with unapplied migrations shows them under `pending`
  - [x] 6.5d `test_status_before_and_after_migrate`: end-to-end lifecycle through the CLI runner against the test daily DB — (1) assert `status --db daily` shows 2 pending, 0 applied; (2) invoke `migrate --db daily`; (3) assert `status --db daily` now shows 0 pending, 2 applied with non-null `applied_at` timestamps. Covers success criterion 5.

## Task 7: Repurpose mt data daily migrate

- [x] 7.1 In `data.py`, change `daily_migrate` to call `MarketDB.apply_schema_migrations()` instead of `MarketDB.verifyDatabase()`; update help text to `"Apply pending schema migrations to the daily OHLCV database."`
- [x] 7.2 Add new `daily_verify` command that contains the old `verifyDatabase()` call; help text: `"Verify the daily OHLCV database schema is accessible."`
- [x] 7.3 Test:
  - [x] 7.3a `test_daily_migrate_applies_migrations`: `mt data daily migrate` applies pending daily-track migrations
  - [x] 7.3b `test_daily_verify_succeeds`: `mt data daily verify` returns status ok
- [x] **Checkpoint commit:** `feat(cli): mt data migrate --db and migrate status; repurpose daily migrate` — covers Tasks 5–7

## Task 8: Delete orphaned SQL files

- [x] 8.1 Delete the following files (git rm):
  - `database/migrations/025_minute_acquisition_tables.sql`
  - `database/migrations/025_validate_migration.sql`
  - `database/migrations/750_create_foundation_tables.sql`
  - `database/migrations/750_rollback_foundation_tables.sql`
  - `database/migrations/750_validate_migration.sql`
  - `database/migrations/750_foundation_seed_data.sql` (if present under `database/seeds/`)
  - `database/migrations/760_create_tick_events_hypertable.sql`
  - `database/migrations/760_rollback_tick_events_hypertable.sql`
  - `database/migrations/760_validate_migration.sql`
  - `database/migrations/770_create_acquisition_state.sql`
  - `database/migrations/770_rollback_acquisition_state.sql`
  - `database/migrations/770_validate_migration.sql`
  - `database/migrations/780_create_daemon_heartbeat.sql`
  - `database/migrations/README.md`
  - `sql/01_setup_database.sql`
  - `sql/02_setup_verification.sql` (if present and not actively used)
- [x] 8.2 Verify no source file imports or references these paths: `grep -r "database/migrations" src/ test/`; resolve any hits before deleting
- [x] 8.3 If `database/` or `sql/` directories become empty after deletion, remove them too

## Task 9: Add migrations README

- [x] 9.1 Create `src/manta_trading/market/schema/migrations/README.md`:
  - State the single-source-of-truth rule: all schema changes go through the Python migration framework; no SQL files outside this package are authoritative
  - List the tracks (`minute`, `daily`) and where each track's list lives
  - Describe how to add a new migration (append a dict to the track's list)
  - Note that `database/migrations/*.sql` existed historically and were retired in slice 150; git history preserves them
- [x] **Checkpoint commit:** `chore: delete orphaned SQL migrations; add migrations package README` — covers Tasks 8–9

## Task 10: Final validation and cleanup

- [x] 10.1 Run full test suite: `pytest` — confirm no regressions from the module restructure
- [x] 10.2 Run `mt data migrate status` against both test DBs; confirm output matches the expected shape from the slice design verification walkthrough
- [x] 10.3 Run `mt data migrate` with no flags against both test DBs; confirm 0 new migrations applied (minute: 001-009 already applied; daily: 001-002 newly applied or already applied)
- [x] 10.4 Confirm `from manta_trading.market.schema.migrations import MIGRATIONS` still resolves (backward compat import)
- [x] 10.5 Run `ruff check src/ test/` and `pyright` — zero new errors introduced by this slice
- [x] 10.6 If Task 10 surfaced any fix-ups, commit them: `fix(schema): final validation fixups for slice 150` — otherwise skip (the per-task checkpoint commits are sufficient)
