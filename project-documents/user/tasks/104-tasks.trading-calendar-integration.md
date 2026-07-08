---
docType: tasks
slice: trading-calendar-integration
project: trading
lld: user/slices/104-slice.trading-calendar-integration.md
dependencies: [102]
projectState: Slice 102 (schema) complete — trading_calendars and trading_holidays tables exist on TimescaleDB with NYSE/NASDAQ seed data (2024-2025). Slice 103 (instrument registry) complete — established psycopg3 + ConnectionPool + per-instance cache pattern. TradingCalendar at src/manta_trading/data/base/trading_calendar.py is a stub with @lru_cache bug, pytz DST issues, magic strings, and hard-fail-on-construct. get_trading_hours() and get_holidays() raise NotImplementedError. SessionClassifier calls TradingCalendar methods correctly but is blocked by the stubs.
dateCreated: 20260403
dateUpdated: 20260403
status: complete
---

## Context Summary
- Working on slice 104: Trading Calendar Integration
- Slice 102 is complete: `trading_calendars` and `trading_holidays` tables exist with seed data (NYSE, NASDAQ, CME calendars; holidays 2024-2025)
- Slice 103 is complete: `InstrumentRegistry` established the psycopg3 + `ConnectionPool` + per-instance dict cache pattern to follow
- `TradingCalendar` at `src/manta_trading/data/base/trading_calendar.py` is a stub — `get_trading_hours()` and `get_holidays()` raise `NotImplementedError`; uses `@lru_cache` on instance methods (cross-instance pollution); uses `pytz` (DST ambiguity); uses magic strings for market status
- `SessionClassifier` at `src/manta_trading/data/base/session_classifier.py` already calls the correct `TradingCalendar` API — no changes needed there once stubs are replaced
- DB column names to use (from migration 750 DDL): `calendar_name`, `market_open_time`, `market_close_time`, `extended_open_time`, `extended_close_time` — NOT the names in `seed_calendar.py`
- Next planned slice: 105 (Tick Event Hypertable Schema)

---

## Tasks

### Task 1: Add `MarketStatus` StrEnum and update `Holiday` dataclass

- [x] **Add `MarketStatus` StrEnum to `src/manta_trading/data/base/trading_calendar.py`**
  - [x] Import `StrEnum` from `enum` module (Python 3.11+)
  - [x] Define `MarketStatus(StrEnum)` with values: `CLOSED = "closed"`, `EARLY_CLOSE = "early_close"`, `LATE_OPEN = "late_open"` — values match DB column values exactly
  - [x] Update `Holiday` dataclass: change `market_status: str` field to `market_status: MarketStatus`
  - [x] Remove `pytz` import (will be replaced by `zoneinfo.ZoneInfo` in later tasks)
  - [x] Remove `from functools import lru_cache` import
  - [x] Keep `TradingHours` dataclass unchanged (already uses `SessionType`)
  - [x] Success: module imports without error; `MarketStatus.CLOSED.value == "closed"`; `Holiday` accepts a `MarketStatus` value

### Task 2: Unit tests for `MarketStatus` and updated `Holiday`

- [x] **Create `test/unit/data/base/test_trading_calendar.py`**
  - [x] Test `MarketStatus` enum has exactly 3 members: `CLOSED`, `EARLY_CLOSE`, `LATE_OPEN`
  - [x] Test `MarketStatus` values match DB strings: `"closed"`, `"early_close"`, `"late_open"`
  - [x] Test `MarketStatus` is a `StrEnum` (string comparison works: `MarketStatus.CLOSED == "closed"`)
  - [x] Test `Holiday` dataclass instantiation with `MarketStatus` value
  - [x] Test `TradingHours` dataclass unchanged (basic instantiation)
  - [x] Success: `uv run pytest test/unit/data/base/test_trading_calendar.py -v` — all tests pass

**Commit:** `feat: add MarketStatus StrEnum and update Holiday dataclass`

### Task 3: Rewrite `TradingCalendar` core — init, lazy load, cache, close

- [x] **Rewrite `TradingCalendar` class in `src/manta_trading/data/base/trading_calendar.py`**
  - [x] Change `__init__(self, calendar_id: str, conninfo: str)` — `conninfo` is now required (no optional `None`)
  - [x] Create `psycopg_pool.ConnectionPool(conninfo, min_size=1, max_size=3)` in `__init__`
  - [x] Initialize `self._loaded = False` and `self._cache: dict = {}` in `__init__`
  - [x] Initialize instance attributes to `None`: `timezone`, `market_open_time`, `market_close_time`, `extended_open_time`, `extended_close_time`, `has_extended_hours`, `calendar_name`
  - [x] Implement `_ensure_loaded(self) -> None`:
    - [x] If `self._loaded` is True, return immediately
    - [x] Query `trading_calendars` table: `SELECT calendar_id, calendar_name, timezone, market_open_time, market_close_time, has_extended_hours, extended_open_time, extended_close_time FROM trading_calendars WHERE calendar_id = %s`
    - [x] If no row found, raise `ValueError(f"Trading calendar '{self.calendar_id}' not found in database")`
    - [x] Set instance attributes from row: `self.timezone = ZoneInfo(row["timezone"])`, time fields from row, `self.has_extended_hours = row["has_extended_hours"]`
    - [x] Set `self._loaded = True`
  - [x] Implement `_invalidate_cache(self) -> None` that calls `self._cache.clear()`
  - [x] Implement `close(self) -> None` that calls `self._pool.close()`
  - [x] Remove old `_clear_cache()` method (referenced `lru_cache.cache_clear()`)
  - [x] Remove `_is_dst_transition()` method (no longer needed with `ZoneInfo`)
  - [x] Import `ZoneInfo` from `zoneinfo` (stdlib); remove `pytz` import
  - [x] Import `ConnectionPool` from `psycopg_pool`; import `dict_row` from `psycopg.rows`
  - [x] Success: `TradingCalendar("NYSE", conninfo)` creates without querying DB; attributes are `None` until first method call

### Task 4: Unit tests for `TradingCalendar` core

- [x] **Add tests to `test/unit/data/base/test_trading_calendar.py`**
  - [x] Create test helpers: `_make_calendar_row()` returning a dict matching `trading_calendars` column names, `_make_calendar(pool_mock)` using `patch("...ConnectionPool")`, `_stub_cursor(pool_mock, fetchone, fetchall)` wiring mock cursor
  - [x] Test `__init__` does not call any DB methods (pool.connection not called)
  - [x] Test `_ensure_loaded` queries DB on first call, sets `self._loaded = True`
  - [x] Test `_ensure_loaded` returns immediately on second call (DB queried once)
  - [x] Test `_ensure_loaded` raises `ValueError` when calendar_id not found (fetchone returns None)
  - [x] Test `_ensure_loaded` sets `self.timezone` to `ZoneInfo` instance
  - [x] Test `_invalidate_cache` clears the cache dict
  - [x] Test `close` calls `pool.close()`
  - [x] Success: `uv run pytest test/unit/data/base/test_trading_calendar.py -v` — all tests pass

**Commit:** `feat: rewrite TradingCalendar core with lazy init and psycopg3 pool`

### Task 5: Implement `is_trading_day()` and `get_holidays()`

- [x] **Implement `is_trading_day(self, check_date: date) -> bool`**
  - [x] Call `_ensure_loaded()` at start
  - [x] Check weekend first (short-circuit): `check_date.weekday() >= 5` → return `False`
  - [x] Cache key: `f"is_trading_day:{check_date}"`; return cached value if present
  - [x] Query: `SELECT 1 FROM trading_holidays WHERE calendar_id = %s AND holiday_date = %s AND market_status = %s` with `MarketStatus.CLOSED.value` as third param
  - [x] If row found → not a trading day (cache `False`); if no row → trading day (cache `True`)
  - [x] Remove `@lru_cache` decorator from old implementation
- [x] **Implement `get_holidays(self, year: int) -> list[Holiday]`**
  - [x] Call `_ensure_loaded()` at start
  - [x] Cache key: `f"holidays:{year}"`; return cached value if present
  - [x] Query: `SELECT holiday_date, holiday_name, market_status, early_close_time, late_open_time FROM trading_holidays WHERE calendar_id = %s AND EXTRACT(YEAR FROM holiday_date) = %s ORDER BY holiday_date`
  - [x] Map each row to `Holiday` dataclass with `MarketStatus(row["market_status"])` for the enum field
  - [x] Cache and return the list
  - [x] Remove `@lru_cache` decorator and `NotImplementedError` from old implementation
  - [x] Success: both methods callable; `is_trading_day` returns `False` for weekends and closed holidays, `True` for early-close days; `get_holidays` returns `list[Holiday]` with `MarketStatus` enum values

### Task 6: Unit tests for `is_trading_day()` and `get_holidays()`

- [x] **Add tests to `test/unit/data/base/test_trading_calendar.py`**
  - [x] Test `is_trading_day` returns `False` for Saturday/Sunday (no DB query)
  - [x] Test `is_trading_day` returns `False` when holiday row with `market_status='closed'` found
  - [x] Test `is_trading_day` returns `True` when no closed holiday found (including early-close days)
  - [x] Test `is_trading_day` caches result — second call for same date does not query DB
  - [x] Test `is_trading_day` calls `_ensure_loaded`
  - [x] Test `get_holidays` returns `list[Holiday]` with correct `MarketStatus` enum values
  - [x] Test `get_holidays` returns empty list when no holidays for year
  - [x] Test `get_holidays` caches result — second call for same year does not query DB
  - [x] Test `get_holidays` calls `_ensure_loaded`
  - [x] Success: `uv run pytest test/unit/data/base/test_trading_calendar.py -v` — all tests pass

**Commit:** `feat: implement is_trading_day and get_holidays with DB-backed cache`

### Task 7: Implement `get_trading_hours()`

- [x] **Implement `get_trading_hours(self, trade_date: date, session_type: SessionType = SessionType.RTH) -> TradingHours | None`**
  - [x] Call `_ensure_loaded()` at start
  - [x] If `not self.is_trading_day(trade_date)` and session_type is RTH, return `None`
  - [x] Cache key: `f"trading_hours:{trade_date}:{session_type.value}"`; return cached if present
  - [x] Query for holiday override on this date: `SELECT market_status, early_close_time, late_open_time FROM trading_holidays WHERE calendar_id = %s AND holiday_date = %s`
  - [x] Determine open/close times based on session_type:
    - [x] **RTH:** start = `self.market_open_time`, end = `self.market_close_time`; apply overrides: if `early_close_time` → use as end, if `late_open_time` → use as start
    - [x] **ETH:** start = `self.extended_open_time`, end = `self.extended_close_time`; return `None` if `not self.has_extended_hours`
    - [x] **ALL:** start = earliest of open times, end = latest of close times (with overrides)
  - [x] Build timezone-aware datetimes: `datetime.combine(trade_date, open_time, tzinfo=self.timezone)`
  - [x] Return `TradingHours(session_start=..., session_end=..., session_type=session_type, is_trading_day=True)`
  - [x] Cache and return result
  - [x] Success: returns correct `TradingHours` for normal days, early-close days, and ETH; returns `None` for closed holidays and weekends (RTH)

### Task 8: Unit tests for `get_trading_hours()`

- [x] **Add tests to `test/unit/data/base/test_trading_calendar.py`**
  - [x] Test RTH on normal trading day returns `TradingHours` with 09:30-16:00 ET boundaries
  - [x] Test RTH on early-close day returns `TradingHours` with 09:30-13:00 ET boundaries
  - [x] Test RTH on late-open day returns `TradingHours` with override start time
  - [x] Test RTH on closed holiday returns `None`
  - [x] Test RTH on weekend returns `None`
  - [x] Test ETH returns `TradingHours` with 04:00-20:00 ET boundaries (when `has_extended_hours=True`)
  - [x] Test ETH returns `None` when `has_extended_hours=False`
  - [x] Test ALL session type returns full range from earliest open to latest close
  - [x] Test caching — second call for same date+session_type does not query DB
  - [x] Test that returned datetimes are timezone-aware (have `tzinfo`)
  - [x] Success: `uv run pytest test/unit/data/base/test_trading_calendar.py -v` — all tests pass

**Commit:** `feat: implement get_trading_hours with holiday overrides`

### Task 9: Fix `get_expected_bar_count()` with ZoneInfo DST handling

- [x] **Rewrite `get_expected_bar_count()` in `trading_calendar.py`**
  - [x] Method signature unchanged: `get_expected_bar_count(self, start_date, end_date, timeframe_minutes=1, session_type=SessionType.RTH) -> int`
  - [x] Call `_ensure_loaded()` at start
  - [x] Iterate from `start_date` to `end_date` inclusive
  - [x] For each date: call `self.get_trading_hours(current_date, session_type)` — if `None`, skip
  - [x] Compute duration: `hours.session_end - hours.session_start` (both are timezone-aware via `ZoneInfo`, so DST is handled correctly)
  - [x] Calculate bars: `int(duration.total_seconds() / 60) // timeframe_minutes`
  - [x] Accumulate total bars
  - [x] No `_is_dst_transition()` helper needed — `ZoneInfo`-aware subtraction handles it
  - [x] Success: returns correct bar counts including across DST transitions

### Task 10: Unit tests for `get_expected_bar_count()`

- [x] **Add tests to `test/unit/data/base/test_trading_calendar.py`**
  - [x] Test single normal RTH day: 390 bars for 1-minute timeframe (6.5 hours * 60)
  - [x] Test single early-close day (13:00): 210 bars for 1-minute timeframe (3.5 hours * 60)
  - [x] Test 5-minute timeframe: 78 bars for normal RTH day (390 / 5)
  - [x] Test date range spanning weekend: weekend days contribute 0 bars
  - [x] Test date range including closed holiday: holiday contributes 0 bars
  - [x] Test DST transition day (mock `get_trading_hours` to return same 09:30-16:00 times with `ZoneInfo`): bar count is 390 (DST does not affect wall-clock session duration)
  - [x] Test empty range (start > end): returns 0
  - [x] Success: `uv run pytest test/unit/data/base/test_trading_calendar.py -v` — all tests pass

**Commit:** `feat: fix get_expected_bar_count with ZoneInfo DST handling`

### Task 11: Add `mt data calendars` CLI subcommands

- [x] **Extend `src/manta_trading/cli/commands/data.py` with `calendars_app`**
  - [x] Create `calendars_app = typer.Typer(name="calendars", help="Trading calendar information")`
  - [x] Register on `data_app`: `data_app.add_typer(calendars_app)`
  - [x] Create helper `_create_trading_calendar(ctx, calendar_id: str) -> TradingCalendar` — gets `timescale_db_url` from Settings, fails explicitly if not configured
  - [x] Implement `calendars_list` command registered as `calendars_app.command("list")`:
    - [x] Query `trading_calendars` directly (simple `SELECT *` since no TradingCalendar instance needed for listing all calendars) using `psycopg.connect` with `Settings.timescale_db_url`
    - [x] Options: `--json` flag
    - [x] Default: Rich table with columns: calendar_id, calendar_name, timezone, market hours, ETH hours, has_extended_hours
    - [x] `--json`: JSON array of calendar dicts
  - [x] Implement `calendars_holidays` command registered as `calendars_app.command("holidays")`:
    - [x] Required option: `--calendar TEXT` (calendar_id)
    - [x] Required option: `--year INT` (defaults to current year)
    - [x] Options: `--json` flag
    - [x] Creates `TradingCalendar(calendar_id, conninfo)`, calls `get_holidays(year)`
    - [x] Default: Rich table with columns: date, holiday_name, market_status, early_close_time, late_open_time
    - [x] `--json`: JSON array of holiday dicts
    - [x] Calls `calendar.close()` in `finally`
  - [x] Success: `uv run mt data calendars --help` shows list and holidays subcommands

### Task 12: Unit tests for `calendars` CLI commands

- [x] **Add `TestCalendarsList` and `TestCalendarsHolidays` to `test/unit/test_cli_data.py`**
  - [x] Use Typer `CliRunner`; mock `psycopg.connect` for list, mock `TradingCalendar` for holidays
  - [x] `TestCalendarsList`:
    - [x] Test default output contains calendar rows (mock returns NYSE and NASDAQ rows)
    - [x] Test `--json` returns valid JSON array
    - [x] Test missing `MT_TIMESCALE_DB_URL` exits with error message
  - [x] `TestCalendarsHolidays`:
    - [x] Test default output contains holiday rows for a given year
    - [x] Test `--json` returns valid JSON array
    - [x] Test missing `MT_TIMESCALE_DB_URL` exits with error
    - [x] Test invalid calendar_id propagates error from `TradingCalendar`
  - [x] Success: `uv run pytest test/unit/test_cli_data.py -v -k calendars` — all tests pass

**Commit:** `feat: add mt data calendars CLI commands (list, holidays)`

### Task 13: Integration tests for `TradingCalendar`

- [x] **Create `test/integration/test_trading_calendar_integration.py`**
  - [x] All tests skip when `MT_TIMESCALE_DB_URL` is not set
  - [x] `test_load_nyse_calendar`: create `TradingCalendar("NYSE", conninfo)`, call `_ensure_loaded()`, assert timezone is `ZoneInfo("America/New_York")`, market_open_time is `time(9, 30)`, has_extended_hours is True
  - [x] `test_load_unknown_calendar_raises`: create `TradingCalendar("UNKNOWN", conninfo)`, assert calling a method raises `ValueError`
  - [x] `test_is_trading_day_weekday`: test a known weekday (non-holiday) returns True
  - [x] `test_is_trading_day_weekend`: test a Saturday returns False
  - [x] `test_is_trading_day_holiday`: test a known holiday (e.g., 2025-12-25) returns False
  - [x] `test_is_trading_day_early_close`: test a known early-close day (e.g., 2024-11-29 Black Friday) returns True (early close IS a trading day)
  - [x] `test_get_holidays_2025`: call `get_holidays(2025)` for NYSE, assert returns non-empty list with correct holiday names and `MarketStatus` enum values
  - [x] `test_get_trading_hours_normal_day`: test a normal trading day returns RTH 09:30-16:00
  - [x] `test_get_trading_hours_early_close`: test an early-close day returns RTH with 13:00 end
  - [x] `test_get_expected_bar_count_single_day`: test expected bar count for a single normal RTH day = 390
  - [x] `test_session_classifier_end_to_end`: create `TradingCalendar("NYSE", conninfo)`, pass to `classify_bar_session()` with a known RTH timestamp (e.g., 2025-01-02 10:00 ET) → assert returns `SessionType.RTH`; test with an ETH timestamp (e.g., 2025-01-02 07:00 ET) → assert returns `SessionType.ETH`
  - [x] Clean up: call `calendar.close()` in teardown
  - [x] Success: `MT_TIMESCALE_DB_URL=... uv run pytest test/integration/test_trading_calendar_integration.py -v` — all tests pass

**Commit:** `test: add integration tests for TradingCalendar`

### Task 14: Full test suite verification and completion

- [x] **Verify no regressions in existing test suite**
  - [x] Run `uv run pytest test/unit/ -v` — all tests pass (590+ expected)
  - [x] Confirm no new import errors or test collection warnings
  - [x] If any tests fail that were passing before, investigate and fix

- [x] **Update CHANGELOG.md**
  - [x] Add slice 104 entries under `[Unreleased]`:
    - Added: `MarketStatus` StrEnum, `TradingCalendar` psycopg3 rewrite with lazy init, `get_trading_hours()` with holiday overrides, `get_expected_bar_count()` with ZoneInfo DST fix, `mt data calendars list` and `mt data calendars holidays` CLI commands
    - Changed: `Holiday.market_status` type from `str` to `MarketStatus`, `TradingCalendar` caching from `@lru_cache` to per-instance dict, `TradingCalendar` timezone from `pytz` to `zoneinfo.ZoneInfo`
    - Removed: `_is_dst_transition()` helper (no longer needed)

- [x] **Mark slice 104 complete**
  - [x] Update `project-documents/user/architecture/100-slices.data-storage.md` — check off entry 5: `[ ] **(104)...` → `[x] **(104)...`
  - [x] Update `project-documents/user/slices/104-slice.trading-calendar-integration.md` frontmatter: `status: complete`, `dateUpdated` to today
  - [x] Update this task file frontmatter: `status: complete`, `dateUpdated` to today

**Commit:** `docs: mark slice 104 complete, update changelog`
