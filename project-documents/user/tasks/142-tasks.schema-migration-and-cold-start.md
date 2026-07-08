---
docType: tasks
slice: 142-schema-migration-and-cold-start
project: trading
lld: user/slices/142-slice.schema-migration-and-cold-start.md
dependencies: [141-slice.universe-rebuild-from-eodhd-instruments-schema-migration]
projectState: >
  Slice 141 merged and verified on main. Instruments table has ~32,875 rows with
  eodhd_type, eodhd_exchange, delisted_at_eodhd populated; active column dropped.
  A full Finnhub enrichment run is completing (~9 hours, 60/min). The AV-era bar
  tables (minute_ohlcv ~61.8M rows, daily_ohlcv ~2.4M rows) and old acquisition_state
  (~12,400 rows) exist and will be TRUNCATEd by this slice. coverage_gaps has 1 row
  (NVDA seed from slice 128) and will be dropped. No data_gaps table yet.
dateCreated: 20260501
dateUpdated: 20260501
status: complete
---

## Context Summary

- Slice 142 is the destructive, schema-pivot step of the data-quality initiative
- Delivers: `data_gaps` table, slimmed `acquisition_state`, `data_status` view,
  `manta_trading.constants` module, new `FetchStatus` / `LastAttemptOutcome` enums,
  `mt data migrate-cold-start` CLI command
- Removes: `coverage_gaps` table, `last_success_ts`, `retry_count`, `status`,
  `error_message`, `run_id` columns from `acquisition_state`, and the entire
  `src/manta_trading/data/coverage/` package
- Slice 143 (`compute_k_factor`), 144 (daemon), and 145–147 (operator commands)
  depend on the schema and constants landed here
- Next slice: 143 — `compute_k_factor` single source of truth

---

## Tasks

- [x] **T1. Create `manta_trading.constants` module**
  - [x] Create `src/manta_trading/constants.py` with exactly the values from LLD D10:
    - `ADJUSTMENT_DRIFT_EPSILON: Decimal = Decimal("1e-6")`
    - `MAX_RETRY_COUNT: int = 5`
    - `DAILY_STALENESS_THRESHOLD: timedelta = timedelta(days=2)`
    - `MINUTE_STALENESS_THRESHOLD: timedelta = timedelta(days=1)`
    - `DAILY_HISTORY_MONTHS: int | None = None`
    - `MINUTE_HISTORY_MONTHS: int = 24`
    - `LATE_BAR_GRACE_PERIOD: timedelta = timedelta(minutes=30)`
    - `MAX_GAP_STALENESS: timedelta = timedelta(minutes=5)`
  - [x] All constants are module-level typed; no class, no dict, no defaults
  - [x] File passes `pyright --strict` with zero errors

- [x] **T2. Test: constants module**
  - [x] Add `tests/unit/test_constants.py`
  - [x] Assert each constant has the correct type and value
  - [x] Assert `DAILY_HISTORY_MONTHS is None` (not 0, not a timedelta)
  - [x] `pytest tests/unit/test_constants.py` passes

- [x] **T3. Migrate `HISTORY_MONTHS` to `MINUTE_HISTORY_MONTHS`**
  - [x] In `src/manta_trading/data/acquisition/minute/freshness.py`, replace the
    module-level `HISTORY_MONTHS: int = 24` with an import:
    `from manta_trading.constants import MINUTE_HISTORY_MONTHS`
  - [x] Replace any local reference to `HISTORY_MONTHS` in that file and in
    `src/manta_trading/data/acquisition/minute/orchestrator.py` with
    `MINUTE_HISTORY_MONTHS`
  - [x] `grep -rn "^HISTORY_MONTHS" src/` returns 0 hits
  - [x] Existing freshness/orchestrator tests pass unchanged

- [x] **T4. Create `FetchStatus` and `LastAttemptOutcome` StrEnums**
  - [x] Create `src/manta_trading/data/quality/` package with `__init__.py`
  - [x] Create `src/manta_trading/data/quality/fetch_status.py`:
    ```python
    class FetchStatus(StrEnum):
        UNKNOWN = "UNKNOWN"
        PROVIDER_HOLE = "PROVIDER_HOLE"
        FAILED_RETRYABLE = "FAILED_RETRYABLE"
        RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    ```
  - [x] Add `LastAttemptOutcome` StrEnum to `src/manta_trading/data/acquisition/state.py`:
    ```python
    class LastAttemptOutcome(StrEnum):
        SUCCESS = "success"
        PARTIAL = "partial"
        EMPTY = "empty"
        TRANSIENT_FAILURE = "transient_failure"
    ```
  - [x] Both files pass `pyright --strict`

- [x] **T5. Test: `FetchStatus` and `LastAttemptOutcome`**
  - [x] Add `tests/unit/data/quality/test_fetch_status.py`
  - [x] Assert all four `FetchStatus` values exist and compare equal to their
    string literals (StrEnum contract)
  - [x] Assert all four `LastAttemptOutcome` values exist
  - [x] `pytest tests/unit/data/quality/` passes

- [x] **T6. Create `DataGap` read DTO**
  - [x] Create `src/manta_trading/data/quality/data_gaps.py` with a `DataGap`
    dataclass mirroring the table columns:
    `symbol, granularity: Granularity, gap_start: datetime, gap_end: datetime,
    fetch_status: FetchStatus, last_attempt_ts: datetime | None, attempt_count: int`
  - [x] No writer methods — read DTO only (writers are slice 144)
  - [x] Passes `pyright --strict`

- [x] **T7. Add migration helper for `FetchStatus` CHECK SQL**
  - [x] In `src/manta_trading/market/schema/migrations/minute.py`, add a helper
    `_fetch_status_check_sql() -> str` that renders `IN ('FAILED_RETRYABLE',
    'PROVIDER_HOLE', 'RETRY_EXHAUSTED', 'UNKNOWN')` from `FetchStatus` enum
    values (sorted, same pattern as `_eodhd_type_check_sql`)
  - [x] Add `_outcome_check_sql() -> str` the same way from `LastAttemptOutcome`
  - [x] Both helpers produce deterministic output (values sorted alphabetically)

- [x] **T8. Add migration helper for view interval literals**
  - [x] In `migrations/minute.py`, add `_interval_literal(td: timedelta) -> str`
    that converts a `timedelta` to a Postgres interval string, e.g.
    `timedelta(minutes=30)` → `"30 minutes"`, `timedelta(days=2)` → `"2 days"`
  - [x] Used by the `data_status` view DDL to render `LATE_BAR_GRACE_PERIOD`,
    `DAILY_STALENESS_THRESHOLD`, `MINUTE_STALENESS_THRESHOLD` from constants

- [x] **T9. Test: migration helpers**
  - [x] Add `tests/unit/market/test_migration_helpers.py`
  - [x] Assert `_fetch_status_check_sql()` contains all four FetchStatus values
    and none outside them
  - [x] Assert `_outcome_check_sql()` contains all four LastAttemptOutcome values
  - [x] Assert `_interval_literal(timedelta(minutes=30)) == "30 minutes"`
  - [x] Assert `_interval_literal(timedelta(days=2)) == "2 days"`
  - [x] `pytest tests/unit/market/test_migration_helpers.py` passes

- [x] **T10. Write migration 018 — create `data_gaps`**
  - [x] Append entry `"018_data_gaps"` to `MINUTE_MIGRATIONS` with the DDL from
    LLD §Migration 018:
    - table with PK `(symbol, granularity, gap_start, gap_end)`
    - `fetch_status` CHECK derived from `_fetch_status_check_sql()`
    - `granularity` CHECK `IN ('daily', 'minute')`
    - `gap_end >= gap_start` CHECK, `attempt_count >= 0` CHECK
    - two indexes: `(symbol, granularity)` and `(fetch_status)`
  - [x] All DDL is idempotent (`IF NOT EXISTS`, `DO $$ … END $$` guards)

- [x] **T11. Write migration 019 — slim `acquisition_state`**
  - [x] Append `"019_slim_acquisition_state"` dropping `last_success_ts`,
    `retry_count`, `error_message`, `run_id`, `status`; adding
    `last_attempt_outcome TEXT` and `last_adjusted_ca_snapshot_id TEXT`
  - [x] Use `ALTER TABLE … DROP COLUMN IF EXISTS` and `ADD COLUMN IF NOT EXISTS`
  - [x] Idempotent

- [x] **T12. Write migration 020 — drop `coverage_gaps`**
  - [x] Append `"020_drop_coverage_gaps"` with `DROP TABLE IF EXISTS coverage_gaps`
  - [x] No guards needed beyond `IF EXISTS`

- [x] **T13. Write migration 021 — `data_status` view**
  - [x] Append `"021_data_status_view"` with `CREATE OR REPLACE VIEW data_status AS`
    containing:
    - `exchange_completed_close` CTE reading `trading_calendar`, filtering by
      `session_close_utc + INTERVAL '{grace}' < NOW()` where `grace` is rendered
      from `LATE_BAR_GRACE_PERIOD` via `_interval_literal`
    - `symbols_x_granularity` CTE: `instruments CROSS JOIN (VALUES ('daily'),
      ('minute'))`, filter on delistings per LLD §Migration 021
    - `bars_summary` CTE: UNION of daily and minute bar counts
    - `gap_counts` CTE: count + `BOOL_OR(fetch_status = 'RETRY_EXHAUSTED')`
    - Final SELECT with LEFT JOINs for all CTEs and `acquisition_state`
    - CASE health expression using staleness interval literals from constants
  - [x] Join on `exchange_completed_close` is `LEFT JOIN` (not inner — see LLD D8)
  - [x] Join key is `trading_calendar_id`, not `venue` (LLD D9)

- [x] **T14. Write migration 022 — `last_attempt_outcome` CHECK**
  - [x] Append `"022_acquisition_state_outcome_check"` with `DO $$ … END $$`
    guard checking `pg_constraint` before adding the CHECK; constraint allows
    NULL plus the four `LastAttemptOutcome` values (rendered via
    `_outcome_check_sql()`)

- [x] **T15. Test: migrations 018–022 apply cleanly (integration)**
  - [x] In the integration test suite, apply all `MINUTE_MIGRATIONS` against
    a test DB that has slice 141's migrations already applied
  - [x] Assert `data_gaps` table exists with expected columns and constraints
  - [x] Assert `acquisition_state` has `last_attempt_outcome`,
    `last_adjusted_ca_snapshot_id`; does NOT have `last_success_ts`,
    `retry_count`
  - [x] Assert `coverage_gaps` table does not exist
  - [x] Assert `data_status` view exists and returns 0 rows on an empty
    instruments table (empty join = empty view)
  - [x] Re-run the migrations against the same DB: all are no-ops, exit 0
  - [x] `pytest tests/integration/test_migrations_018_022.py` passes

- [x] **T16. Slim `AcquisitionStateRow` DTO**
  - [x] In `src/manta_trading/data/acquisition/state.py`, update `AcquisitionStateRow`
    to remove `last_success_ts`, `retry_count`, `error_message`, `run_id`, `status`
    fields and add:
    - `last_attempt_outcome: LastAttemptOutcome | None = None`
    - `last_adjusted_ca_snapshot_id: str | None = None`
  - [x] Update the repository's SELECT / INSERT / UPDATE SQL in the same file
    to reflect the new column set (no reference to removed columns)
  - [x] Passes `pyright --strict`

- [x] **T17. Delete `src/manta_trading/data/coverage/` package**
  - [x] Remove the entire `src/manta_trading/data/coverage/` directory
  - [x] Search for all imports: `grep -rn "from manta_trading.data.coverage"
    src/ tests/` — fix or remove each one
  - [x] `CoverageGapStatus` enum references in `migrations/minute.py`
    (`_coverage_status_check_sql` and the `012_coverage_gaps` entry) remain
    as-is (the migration record is historic; the function is still called at
    import time — either inline the string values in that historic migration
    or keep the enum as a local constant in the migrations file, per whichever
    is simpler without touching the migration's SQL)
  - [x] Confirm: `python -c "import manta_trading"` succeeds with no ImportError

- [x] **T18. Remove dead column references from `cli/commands/data.py`**
  - [x] Remove every reference to `last_success_ts`, `retry_count`,
    `error_message`, `run_id`, and `status` as `AcquisitionStateRow` fields
  - [x] Where functions computed freshness from `last_success_ts`, update to
    use `last_attempt_ts` with the appropriate `STALENESS_THRESHOLD` from
    `manta_trading.constants`
  - [x] Where display tables showed `retry_count`, collapse or remove the column
  - [x] `grep -n "last_success_ts\|\.retry_count\|\.error_message\|\.run_id"
    src/manta_trading/cli/commands/data.py` returns 0 hits
  - [x] `pyright --strict src/manta_trading/cli/commands/data.py` passes

- [x] **T19. Remove dead column references from acquisition orchestrators**
  - [x] Audit `src/manta_trading/data/acquisition/orchestrator.py`,
    `daily/orchestrator.py`, `minute/orchestrator.py`, `minute/writer.py`
  - [x] Remove references to the dropped fields; update any writes to
    `acquisition_state` to use the slimmed column set
  - [x] Orchestrators may emit `last_attempt_outcome` on success/failure using
    `LastAttemptOutcome` enum — add if the column is being written, skip if
    the write is deferred to slice 144
  - [x] `pyright --strict` passes over the orchestrator files

- [x] **T20. Test: DTO and orchestrator surgery (unit)**
  - [x] Update any existing unit tests that construct `AcquisitionStateRow`
    with the old fields to use the new shape
  - [x] Add a test asserting `AcquisitionStateRow` has no attribute
    `last_success_ts` or `retry_count` (hasattr check)
  - [x] Confirm `from manta_trading.data.coverage import anything` raises
    `ImportError` (the package is gone)
  - [x] `pytest tests/unit/data/acquisition/` passes

- [x] **T21. Write `migrate_cold_start.py` — pre-flight module**
  - [x] Create `src/manta_trading/data/quality/migrate_cold_start.py`
  - [x] Implement `run_preflight(conn, skip_probe: bool) -> None` raising
    `PreflightFailed(msg: str)` on any of D1's five rules:
    1. `schema_migrations` contains `015_`, `016_`, `017_` rows
    2. `SELECT count(*) FROM instruments WHERE eodhd_type IS NULL` == 0
    3. `SELECT count(*) FROM instruments` in `[30_000, 80_000]`
    4. `information_schema.columns` shows no `active` column on `instruments`
    5. (unless `skip_probe`) EODHD `/eod` probe of AAPL, MSFT, SPY — per the
       failure-mode table in LLD D1.5: timeout (10s) → halt; 4xx auth → halt
       with key guidance; 4xx other / 5xx / empty body / malformed JSON → halt
       with typed message; no retries on any failure
  - [x] `PreflightFailed` is defined in this module, not a generic exception
  - [x] Passes `pyright --strict`

- [x] **T22. Test: pre-flight (unit, mocked DB)**
  - [x] Add `tests/unit/data/quality/test_migrate_cold_start.py`
  - [x] Test rule 1: mock `schema_migrations` missing `016_` → `PreflightFailed`
  - [x] Test rule 2: mock 1 row with `eodhd_type IS NULL` → `PreflightFailed`
  - [x] Test rule 3a: mock count = 1000 → `PreflightFailed`
  - [x] Test rule 3b: mock count = 90_000 → `PreflightFailed`
  - [x] Test rule 3c: mock count = 32_875 → passes
  - [x] Test rule 5 timeout: mock `httpx` timeout → `PreflightFailed` with
    message containing the host name
  - [x] Test rule 5 HTTP 401: mock 401 response → `PreflightFailed` with
    credential guidance text
  - [x] Test rule 5 HTTP 200 empty array: mock `[]` body → `PreflightFailed`
  - [x] Test `skip_probe=True`: EODHD is not called regardless of API state
  - [x] `pytest tests/unit/data/quality/test_migrate_cold_start.py` passes

- [x] **T23. Write `migrate_cold_start.py` — orchestration and TRUNCATE**
  - [x] Implement `run_migration(conn) -> dict` that:
    1. Applies migrations 018–022 via the existing `runner.apply_migrations`
       against the same `conn`
    2. Issues `TRUNCATE TABLE minute_ohlcv, daily_ohlcv, acquisition_state
       RESTART IDENTITY` on the same connection (coverage_gaps is already gone
       via migration 020)
    3. Returns a dict of counts (`migrations_applied`, `rows_truncated` per table)
  - [x] Steps 1–2 share one transaction (no intermediate commits); failure rolls
    back both DDL and TRUNCATE
  - [x] `run_post_flight(conn) -> dict` checks: `data_gaps` count = 0; `data_status`
    row count ≈ `2 × instruments` count; queries `EXPLAIN` to confirm no
    per-row Function Scan in the view plan

- [x] **T24. Wire the CLI: `mt data migrate-cold-start`**
  - [x] In `src/manta_trading/cli/commands/data.py`, add subcommand
    `migrate_cold_start` with options: `--yes`, `--skip-probe`, `--dry-run`,
    `--json`, `-v / --verbose`
  - [x] Flow: pre-flight → print destruction counts → 5s wait + `truncate`
    prompt (skipped under `--yes`) → `run_migration` → `run_post_flight` →
    print results table or JSON
  - [x] `--dry-run` runs pre-flight + prints counts, exits 0 with no DDL
  - [x] Exit codes: 0 success, 1 pre-flight failed, 2 operator declined,
    3 migration/TRUNCATE failed
  - [x] Typer help text matches LLD §CLI Specification verbatim (usage + options)

- [x] **T25. Test: CLI integration — cold-start command**
  - [x] Add `tests/integration/test_migrate_cold_start_cli.py`
  - [x] Test `--dry-run` against a test DB with 141 applied: pre-flight OK,
    no DDL applied (018 not in `schema_migrations` after dry-run)
  - [x] Test `--yes` against same DB: migrations applied, bar tables empty,
    `coverage_gaps` absent, `data_status` returns rows all STALE
  - [x] Test idempotent re-run with `--yes`: exits 0, no new migration rows,
    TRUNCATE on empty tables is silent no-op
  - [x] Test against DB missing slice 141 (no `015_` in schema_migrations):
    exits 1, no DDL applied
  - [x] `pytest tests/integration/test_migrate_cold_start_cli.py` passes

- [x] **T26. Test: `data_status` health rules (integration)**
  - [x] Seed a test DB (slice 141 + this slice's migrations applied)
  - [x] Insert one `instruments` + `trading_calendar` row for a test symbol
  - [x] Verify `health = 'STALE'` with no `acquisition_state` row
  - [x] Insert `acquisition_state` row with recent `last_attempt_ts`, no
    `data_gaps` rows → `health = 'OK'`
  - [x] Insert one `data_gaps` row with `fetch_status = 'UNKNOWN'` in target
    window → `health = 'GAPS'`
  - [x] Change that row to `fetch_status = 'RETRY_EXHAUSTED'` → `health = 'FAILED'`
  - [x] Insert `acquisition_state` row with `last_attempt_ts` older than
    `DAILY_STALENESS_THRESHOLD` and no gaps → `health = 'STALE'`
  - [x] `pytest tests/integration/test_data_status_view.py` passes

- [x] **T27. Final sweep: no dead references**
  - [x] `grep -rn "last_success_ts\|\.retry_count\|error_message.*AcquisitionState\|
    from manta_trading.data.coverage" src/ tests/ --include="*.py"` returns 0 hits
  - [x] `grep -rn "coverage_gaps" src/ --include="*.py"` returns 0 hits (only
    migration 012's historic SQL is acceptable, which lives in `MINUTE_MIGRATIONS`
    as a string, not as a Python symbol)
  - [x] `pyright --strict` over `src/manta_trading/` passes with 0 errors
  - [x] `pytest` full suite passes (unit + integration)

- [x] **T28. Build and commit**
  - [x] `mt --help` renders without error; `mt data migrate-cold-start --help`
    shows expected usage
  - [x] `python -c "from manta_trading.constants import MAX_RETRY_COUNT; print(MAX_RETRY_COUNT)"` → `5`
  - [x] Run `mt data migrate-cold-start --dry-run` against dev DB (slice 141
    applied): pre-flight OK, dry-run exit 0, no schema changes
  - [x] `git add -A && git commit` from project root with semantic message
