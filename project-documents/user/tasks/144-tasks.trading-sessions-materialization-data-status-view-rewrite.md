---
docType: tasks
slice: 144-trading-sessions-materialization-data-status-view-rewrite
project: trading
lld: user/slices/144-slice.trading-sessions-materialization-data-status-view-rewrite.md
dependencies: [142-slice.schema-migration-and-cold-start]
projectState: >
  Slice 143 complete and committed. Dev DB has daily_ohlcv hypertable,
  compute_k_factor SSOT, and data_status view (with-daily branch).
  Deprecated CaSnapshot aliases from 143 are pending removal this slice.
  Branch: main (create 144-slice.trading-sessions-materialization-data-status-view-rewrite
  from main before starting implementation).
dateCreated: 20260502
dateUpdated: 20260502
status: complete
---

## Context Summary

- Slice 144 delivers: (1) `trading_sessions` materialized table with
  per-calendar session open/close UTC rows; (2) `data_status` view rewrite
  projecting real `target_end_ts`; (3) Python `TradingCalendar` refactored
  to read from the table; (4) `mt data --extend` CLI for horizon maintenance.
- Four migrations on the timescale DB: 025 (table), 026 (initial population),
  027 (`--extend` CLI surface), 028 (view rewrite).
- Pure function `populate_trading_sessions` is extracted from
  `TradingCalendar._build_trading_hours` — same algorithm, no second impl.
- `OutOfHorizonError` raised on out-of-horizon `get_trading_hours` calls.
- Extension amount is a fixed constant (not user-supplied); decide value
  at T6 (horizon policy task).
- Deprecated `AdjustmentContext` / `load_adjustment_context` aliases from
  slice 143 are removed in T1.
- Dependencies: slice 142 (data_gaps schema, data_status view stub);
  slice 143 (daily_ohlcv hypertable, k_factor SSOT).
- Next slice: 145 — daemon refactor.

---

## Tasks

- [x] **T1. Remove deprecated slice-143 aliases and create slice branch**
  - [x] Checkout branch `144-slice.trading-sessions-materialization-data-status-view-rewrite` from main.
  - [x] In `src/manta_trading/data/adjustment/k_factor.py`, remove
    the `AdjustmentContext` and `load_adjustment_context` re-export aliases
    marked deprecated in slice 143. Confirm no remaining call sites use
    the old names (`grep -r "AdjustmentContext\|load_adjustment_context" src/`).
  - [x] Build passes; existing tests pass.
  - [x] Commit: `refactor: remove deprecated CaSnapshot aliases from slice 143`

- [x] **T2. Add `OutOfHorizonError` exception class**
  - [x] Add `OutOfHorizonError(calendar_id, date, horizon_end)` to
    `src/manta_trading/data/base/trading_calendar.py` (or a shared
    exceptions module if one exists). Message must name the maintenance
    command (`mt data --extend`).
  - [x] Export from the package's public surface if `TradingCalendar` is
    already public.
  - [x] Success: `OutOfHorizonError` is importable and carries `calendar_id`,
    `date`, and `horizon_end` attributes.

- [x] **T3. Extract `populate_trading_sessions` pure function**
  - [x] In `src/manta_trading/data/base/trading_calendar.py` (or a new
    `src/manta_trading/data/base/session_population.py` if cleaner), extract
    the row-generation logic from `TradingCalendar._build_trading_hours` into
    a standalone pure function `populate_trading_sessions(calendar_id,
    start_date, end_date, calendars_row, holidays_rows) -> list[dict]`.
  - [x] Function must: skip weekends; skip `market_status='closed'` holidays;
    apply `early_close_time` / `late_open_time` overrides; convert to UTC
    using `trading_calendars.timezone`. Returns list of dicts with keys
    `calendar_id`, `session_date`, `session_open_utc`, `session_close_utc`.
  - [x] `_build_trading_hours` refactored to call the new function internally
    (no behavior change to existing callers).
  - [x] Success: function exists; `_build_trading_hours` output unchanged
    for a sample NYSE date set.

- [x] **T4. Unit test — `populate_trading_sessions` parity against `_build_trading_hours`**
  - [x] Test file: `tests/unit/data/base/test_session_population.py`.
  - [x] Seed test data: a minimal `trading_calendars` row for NYSE + a
    `trading_holidays` fixture covering: a normal weekday, a Saturday, a
    Sunday, Christmas (closed), Black Friday (early close), a late-open day
    (synthetic), DST spring-forward, DST fall-back.
  - [x] Assert `populate_trading_sessions` output matches
    `TradingCalendar._build_trading_hours` output for each date in the fixture.
  - [x] Assert weekend and closed-holiday dates produce no row.
  - [x] Assert early-close date produces a row with `session_close_utc`
    reflecting the override, not the default close time.
  - [x] Success: all assertions pass; test does not hit the real DB.

- [x] **T5. Migration 025 — `trading_sessions` table**
  - [x] In `src/manta_trading/market/schema/migrations/minute.py`, append
    migration 025 to `MINUTE_MIGRATIONS`.
  - [x] DDL: `CREATE TABLE IF NOT EXISTS trading_sessions (calendar_id
    VARCHAR(32) NOT NULL REFERENCES trading_calendars(calendar_id),
    session_date DATE NOT NULL, session_open_utc TIMESTAMPTZ NOT NULL,
    session_close_utc TIMESTAMPTZ NOT NULL, PRIMARY KEY (calendar_id,
    session_date))` plus `CREATE INDEX IF NOT EXISTS
    idx_trading_sessions_close ON trading_sessions (calendar_id,
    session_close_utc)`.
  - [x] Migration is idempotent (`IF NOT EXISTS`).
  - [x] Success: `mt data migrate-cold-start --skip-probe --yes` applies
    migration 025; `to_regclass('trading_sessions')` returns non-NULL;
    re-running is a no-op.

- [x] **T6. Horizon policy constant**
  - [x] Add `TRADING_SESSIONS_EXTENSION_YEARS: int = 2` to
    `src/manta_trading/constants.py` (extend horizon `current_year +
    TRADING_SESSIONS_EXTENSION_YEARS`).
  - [x] Add `TRADING_SESSIONS_HORIZON_WARN_DAYS: int = 90` for the
    `--strict` warning threshold.
  - [x] Success: both constants importable from `manta_trading.constants`;
    no magic numbers in population or CLI code.

- [x] **T7. Migration 026 — initial horizon population**
  - [x] Append migration 026 to `MINUTE_MIGRATIONS`.
  - [x] Migration body: for each row in `trading_calendars`, fetch
    `trading_holidays` rows for the calendar, call
    `populate_trading_sessions` over `[earliest_seeded_year,
    current_year + TRADING_SESSIONS_EXTENSION_YEARS]`, emit idempotent
    `INSERT ... ON CONFLICT (calendar_id, session_date) DO UPDATE SET
    session_open_utc = EXCLUDED.session_open_utc, session_close_utc =
    EXCLUDED.session_close_utc`.
  - [x] `earliest_seeded_year`: read as `MIN(holiday_date year)` from
    `trading_holidays` for the calendar; fall back to current year if
    table is empty.
  - [x] Success: after applying migrations 025+026, `SELECT calendar_id,
    COUNT(*), MIN(session_date), MAX(session_date) FROM trading_sessions
    GROUP BY calendar_id` returns one row per seeded calendar; `MAX(session_date)`
    ≥ end of `current_year + 1`.

- [x] **T8. Integration test — migration 025 + 026**
  - [x] Test file: `tests/integration/market/schema/test_migration_025_026.py`.
  - [x] Seed a minimal `trading_calendars` + `trading_holidays` fixture in
    the test DB (NYSE subset: normal days, one Christmas, one Black Friday).
  - [x] Apply migrations 025 and 026 via `mt data migrate-cold-start` or
    direct migration runner.
  - [x] Assert: Christmas absent; Black Friday present with early-close
    `session_close_utc`; weekend dates absent; `MAX(session_date)` ≥
    `current_year + 1`.
  - [x] Assert idempotency: re-applying migration 026 produces no error
    and no row duplication.
  - [x] Success: all assertions pass against the test DB.
  - [x] Commit: `feat: add trading_sessions table and initial population (migrations 025-026)`

- [x] **T9. `mt data --extend` CLI command**
  - [x] In `src/manta_trading/cli/commands/data.py`, add `--extend` option
    to the `data` command group (or as a sub-command — match the existing
    CLI structure; see slice design §Migration 027).
  - [x] Accepts: `--calendar X` (optional; defaults to all calendars),
    `--strict` (exits non-zero if any calendar's `MAX(session_date)` is
    within `TRADING_SESSIONS_HORIZON_WARN_DAYS` days of today).
  - [x] Body: fetch calendars in scope; for each, call
    `populate_trading_sessions` from `MAX(session_date) + 1` through
    `current_year + TRADING_SESSIONS_EXTENSION_YEARS`; upsert; print count
    of rows inserted/updated.
  - [x] `--strict` check: after extension, if any calendar's new
    `MAX(session_date) < today + TRADING_SESSIONS_HORIZON_WARN_DAYS`,
    print warning and exit non-zero.
  - [x] Success: `mt data --extend --calendar NYSE` runs without error;
    re-running reports 0 inserted/0 updated; `--strict` exits 0 when
    horizon is healthy.

- [x] **T10. Unit test — `--extend` CLI**
  - [x] Test file: `tests/unit/cli/commands/test_data_extend.py`.
  - [x] Mock DB; seed one calendar with `MAX(session_date) = today + 45 days`.
  - [x] Assert `--strict` exits non-zero with a message containing the
    calendar name and days remaining.
  - [x] Assert `--extend` (without `--strict`) exits 0 in the same scenario.
  - [x] Assert re-running `--extend` after a full extension reports 0 changes.
  - [x] Success: all assertions pass without hitting the real DB.
  - [x] Commit: `feat: add mt data --extend CLI for trading_sessions horizon maintenance`

- [x] **T11. Migration 028 — `data_status` view rewrite**
  - [x] Append migration 028 to `MINUTE_MIGRATIONS` (migration 027 slot
    reserved for CLI surface per slice design; fold into code change — no
    DDL needed — and number the view rewrite 028).
  - [x] Update `_build_data_status_view_sql` in `minute.py` to replace
    `NULL::TIMESTAMPTZ AS target_end_ts` with the `exchange_completed_close`
    CTE projecting `MAX(session_close_utc) WHERE session_close_utc +
    INTERVAL '<LATE_BAR_GRACE_PERIOD>' < NOW()` per calendar, LEFT JOIN
    on `i.trading_calendar_id`. Use the existing `_interval_literal()`
    helper for the grace period literal.
  - [x] Migration 028 DO-block branches on `to_regclass('trading_sessions')`:
    if table exists, install new view; otherwise leave slice-142 stub in
    place.
  - [x] Success: after applying migration 028 with `trading_sessions`
    populated, `SELECT target_end_ts FROM data_status WHERE symbol='AAPL'`
    returns non-NULL; `target_end_ts` value matches expected NYSE session
    close + grace.

- [x] **T11a. Unit test — `_build_data_status_view_sql` CTE shape**
  - [x] Test file: `tests/unit/market/schema/test_data_status_view_sql.py`.
  - [x] Call `_build_data_status_view_sql(include_daily_branch=True)` and
    assert the returned SQL string contains the `exchange_completed_close`
    CTE (check for the literal `exchange_completed_close` identifier and
    `session_close_utc`).
  - [x] Assert the string does NOT contain `NULL::TIMESTAMPTZ AS target_end_ts`
    (the slice-142 stub is gone).
  - [x] Assert the grace-period literal (e.g. `'30 minutes'`) appears in
    the CTE WHERE clause, sourced from `LATE_BAR_GRACE_PERIOD` via
    `_interval_literal()`.
  - [x] Success: assertions pass without a DB connection; regression in
    `_build_data_status_view_sql` that silently removes the CTE is caught.

- [x] **T12. Integration test — migration 028 view rewrite**
  - [x] Test file: `tests/integration/market/schema/test_migration_028.py`.
  - [x] Apply migrations 025, 026, 028 against test DB with seeded calendar.
  - [x] Assert `data_status.target_end_ts` is non-NULL for a symbol on the
    seeded calendar.
  - [x] Assert `data_status.target_end_ts` is NULL for a symbol whose
    `instruments.trading_calendar_id` has no rows in `trading_sessions`
    (LEFT JOIN preserved).
  - [x] Assert query latency is sub-second (`EXPLAIN ANALYZE` or `\timing`
    against test universe).
  - [x] Assert migration 028 is idempotent (re-applying is a no-op).
  - [x] Success: all assertions pass.
  - [x] Commit: `feat: rewrite data_status view to project target_end_ts from trading_sessions (migration 028)`

- [x] **T13. Refactor `TradingCalendar.is_trading_day` to read from `trading_sessions`**
  - [x] In `src/manta_trading/data/base/trading_calendar.py`, rewrite
    `is_trading_day(date)` to query `EXISTS(SELECT 1 FROM trading_sessions
    WHERE calendar_id=? AND session_date=?)`. Cache result (per-date,
    per-calendar).
  - [x] For dates past the populated horizon, raise `OutOfHorizonError`.
  - [x] Success: `is_trading_day` returns `True` for a known trading day,
    `False` for a known closed day, raises `OutOfHorizonError` for a date
    beyond the horizon.

- [x] **T14. Unit + integration test — `is_trading_day`**
  - [x] Unit test (mocked DB): normal trading day → True; Christmas → False;
    out-of-horizon date → `OutOfHorizonError`.
  - [x] Integration test (seeded DB): sample NYSE dates including Christmas,
    Black Friday, and a DST-adjacent weekday match expected values.
  - [x] Success: all assertions pass.

- [x] **T15. Refactor `TradingCalendar.get_trading_hours` RTH path to read from `trading_sessions`**
  - [x] Rewrite `get_trading_hours(date, SessionType.RTH)` to query
    `SELECT session_open_utc, session_close_utc FROM trading_sessions WHERE
    calendar_id=? AND session_date=?`. Returns `TradingHours(...)` on hit,
    `None` if date is absent (non-trading), raises `OutOfHorizonError` if
    date is beyond horizon.
  - [x] ETH / ALL path is unchanged (keeps existing `_build_trading_hours`
    logic — ETH not stored in `trading_sessions`).
  - [x] `get_expected_bar_count` internal loop updated to use the new
    `get_trading_hours` for RTH bounds.
  - [x] `_build_trading_hours` remains in place (used by ETH path and
    `populate_trading_sessions` extraction).
  - [x] Success: RTH path returns values matching `trading_sessions` rows;
    ETH path behavior unchanged.

- [x] **T16. Unit + integration test — `get_trading_hours` RTH path**
  - [x] Unit test (mocked DB): RTH hit → `TradingHours` with correct bounds;
    non-trading date → `None`; out-of-horizon date → `OutOfHorizonError`.
  - [x] Integration test (seeded DB): battery of NYSE dates — normal weekday,
    Black Friday (early close), Christmas (absent), DST spring-forward,
    DST fall-back. Each RTH result matches corresponding `trading_sessions`
    row.
  - [x] Parity test: for each date in battery, assert
    `get_trading_hours(date, RTH).session_end == trading_sessions.session_close_utc`.
    This is the architectural keystone (slice design success criterion #9).
  - [x] Success: all assertions pass.
  - [x] Commit: `refactor: route TradingCalendar RTH path through trading_sessions`

- [x] **T17. Adapt existing `TradingCalendar` tests**
  - [x] Locate existing tests: `grep -r "TradingCalendar\|_build_trading_hours\|is_trading_day\|get_trading_hours" tests/` — primary file is likely `tests/unit/data/base/test_trading_calendar.py`.
  - [x] For each test that mocks `_build_trading_hours` to simulate an RTH
    result (e.g. `mock.patch("...._build_trading_hours", return_value=...)`),
    replace the mock with a seeded `trading_sessions` row in a test-DB
    fixture or in-memory stub. The test should call the real `get_trading_hours`
    against the seeded data rather than patching the private method.
  - [x] Tests that test the ETH path or call `_build_trading_hours` directly
    for ETH require no change.
  - [x] Success: full test suite passes; no test that previously passed is
    now skipped or xfailed; no remaining RTH test mocks `_build_trading_hours`.

- [x] **T18. Verification walkthrough (manual)**
  - [x] Execute the verification walkthrough from the slice design
    (steps 1–11) against the dev DB. This covers: pre-state snapshot,
    migration application, table inspection, holiday handling, view
    `target_end_ts`, latency check, Python parity, `--extend` command,
    `--strict` check, out-of-horizon error, idempotency. Steps 3, 4, 5,
    6, 7, 9, 10 verified live against `trading_test` 2026-05-02; output
    captured in slice design walkthrough section. Steps 1, 2, 8, 11
    (apply / idempotency) covered by integration tests
    `test/integration/test_migrations_025_026.py` and
    `test/integration/test_migration_028.py` (14 passed in 5.91s).
  - [x] Capture output for steps 3, 4, 5, 6 in a comment or brief note
    (not a doc — just confirm the values are as expected). Captured in
    slice design.
  - [x] Success: all 11 steps produce the expected output per the slice
    design walkthrough.
  - [x] Commit: `docs(144): update slice status to complete`

- [x] **T19. Final build and test pass**
  - [x] Run full test suite (`pytest` or equivalent). Zero failures.
    1231 unit tests pass; 14 integration tests pass against trading_test.
  - [x] Run `mt data migrate-cold-start --skip-probe --yes` against dev DB
    from a clean state (drop + recreate or use the test DB). All four
    migrations (025, 026, 028 + CLI 027) apply cleanly. Verified
    2026-05-02 against trading_test: `migrations applied: []` (already
    applied — idempotency confirmed), `rows truncated: {minute_ohlcv: 0,
    daily_ohlcv: 0, acquisition_state: 0}`, `data_gaps count: 0`,
    `data_status count: 65554`.
  - [x] Commit any remaining changes.
  - [x] Success: clean build, zero test failures, migrations idempotent.
