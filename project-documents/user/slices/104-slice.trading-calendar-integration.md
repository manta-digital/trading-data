---
docType: slice-design
slice: trading-calendar-integration
project: trading
parent: user/architecture/100-slices.data-storage.md
dependencies: [102]
interfaces: []
dateCreated: 20260403
dateUpdated: 20260403
status: complete
---

# Slice Design: Trading Calendar Integration

## Overview

Rewrite the `TradingCalendar` class (currently a psycopg2 stub with broken caching and hard-fail-on-construct) into a working psycopg3-backed service backed by the `trading_calendars` and `trading_holidays` tables created in slice 102. Replace magic strings (`'closed'`, `'early_close'`, `'late_open'`) with a `MarketStatus` StrEnum. Fix the `@lru_cache` cross-instance pollution bug by switching to a per-instance dict cache (same pattern as `InstrumentRegistry` in slice 103). Fix DST handling in `get_expected_bar_count()`. Wire `SessionClassifier` to the completed calendar so RTH/ETH classification works end-to-end. Add CLI commands for inspecting calendars and holidays.

## Value

**Developer-facing:** Provides a working `TradingCalendar` service that downstream code (session classification, expected bar count validation, coverage gap analysis) can use to answer questions like "is 2024-11-29 a trading day for NYSE?" and "what are the RTH hours on an early-close day?" without maintaining separate holiday lists or hardcoded schedule data.

**Operator-facing:** The `mt data calendars` CLI command gives visibility into which calendars are registered and what holidays are configured, useful for verifying seed data and debugging schedule-related issues.

**Architectural:** Completes the calendar leg of the data foundation, enabling session-classified queries on minute data (RTH/ETH filtering) which is a prerequisite for data quality analysis in Initiative 140.

## Technical Scope

### In Scope
- Rewrite `TradingCalendar` class for psycopg3 with `conninfo` URL-based connection via `ConnectionPool`
- Fix hard-fail-on-construct: load calendar metadata lazily on first use via `_ensure_loaded()`
- Fix `@lru_cache` on instance methods: replace with per-instance dict cache
- Fix DST handling in `get_expected_bar_count()`: use `ZoneInfo` (stdlib) instead of `pytz`, compute session boundaries in UTC to avoid DST ambiguity
- Introduce `MarketStatus` StrEnum (`CLOSED`, `EARLY_CLOSE`, `LATE_OPEN`) — replace all bare string comparisons in `Holiday` and query logic
- Implement DB-backed methods: `is_trading_day()`, `get_trading_hours()`, `get_holidays()`, `get_expected_bar_count()`
- Wire `SessionClassifier` to use the completed `TradingCalendar` (currently calls methods that raise `NotImplementedError`)
- CLI sub-app `mt data calendars` with subcommands: `list`, `holidays`
- Unit tests with mocked DB connections
- Integration tests against real TimescaleDB (skip when unavailable)

### Out of Scope
- Pre-computing the `trading_sessions` table (exists in schema but is not needed for this slice — session boundaries are computed on-the-fly from calendar metadata + holidays)
- Modifying seed data generation (`seed_calendar.py`) — data already exists via migration 750
- Adding new exchanges beyond NYSE/NASDAQ (architecture supports it but no data to seed)
- `IDataService` protocol implementation (Initiative 140)
- Data quality validation or coverage gap analysis using calendars (Initiative 140)

## Dependencies

### Prerequisites
- **Slice 102 (complete):** `trading_calendars` and `trading_holidays` tables exist on TimescaleDB with seed data (NYSE, NASDAQ holidays 2024-2025).
- **Slice 100 (complete):** psycopg3 connection patterns established. `Settings` provides `timescale_db_url`.

### Interfaces Required
- `trading_calendars` table schema (slice 102, migration 750): `calendar_id`, `timezone`, `market_open_time`, `market_close_time`, `has_extended_hours`, `extended_open_time`, `extended_close_time`
- `trading_holidays` table schema (slice 102, migration 750): `calendar_id`, `holiday_date`, `holiday_name`, `market_status`, `early_close_time`, `late_open_time`
- `Settings.timescale_db_url`
- `SessionType` enum from `adjustment_policy.py`

## Architecture

### Component Structure

```
src/manta_trading/data/base/trading_calendar.py  (rewrite)
  ├── MarketStatus (StrEnum) — new
  ├── Holiday (dataclass) — update market_status type
  ├── TradingHours (dataclass) — keep as-is
  └── TradingCalendar (class) — full rewrite
        ├── __init__(calendar_id, conninfo)
        ├── close()
        ├── _ensure_loaded()          — lazy load calendar metadata
        ├── _invalidate_cache()
        ├── _load_calendar_data()     — query trading_calendars
        ├── is_trading_day(date)      — cached, queries trading_holidays
        ├── get_trading_hours(date, session_type) — computes from metadata + holidays
        ├── get_holidays(year)        — cached, queries trading_holidays
        └── get_expected_bar_count(start, end, timeframe, session_type)

src/manta_trading/data/base/session_classifier.py  (no changes needed)
  └── Already calls TradingCalendar API correctly; will work once methods are implemented

src/manta_trading/cli/commands/data.py  (extend)
  └── calendars_app (Typer sub-app)
        ├── calendars_list  — show registered calendars
        └── calendars_holidays — show holidays for a calendar/year
```

### Data Flow

1. **Calendar initialization:** `TradingCalendar("NYSE", conninfo)` creates pool but does NOT query DB
2. **First access:** Any public method calls `_ensure_loaded()` which queries `trading_calendars` for metadata (timezone, hours, ETH config) and caches it on the instance
3. **Holiday queries:** `is_trading_day()` and `get_holidays()` query `trading_holidays` filtered by `calendar_id` and cache results per-date/per-year
4. **Trading hours computation:** `get_trading_hours()` combines calendar metadata (open/close times) with holiday overrides (early close, late open) to return `TradingHours` with timezone-aware `datetime` boundaries
5. **Bar count:** `get_expected_bar_count()` iterates dates, uses `get_trading_hours()` for each, computes minutes in session. DST-safe because session boundaries are computed in local time with proper timezone handling

## Technical Decisions

### MarketStatus StrEnum

Replace bare strings `'closed'`, `'early_close'`, `'late_open'` with:

```python
class MarketStatus(StrEnum):
    CLOSED = "closed"
    EARLY_CLOSE = "early_close"
    LATE_OPEN = "late_open"
```

Values match the database column values exactly (lowercase), so no migration needed. The `Holiday` dataclass field `market_status` changes from `str` to `MarketStatus`. All comparisons use the enum, never string literals.

### Lazy Initialization Pattern

The current `TradingCalendar.__init__` hard-fails if the calendar row is missing. The rewrite defers DB access:

```python
def __init__(self, calendar_id: str, conninfo: str) -> None:
    self.calendar_id = calendar_id
    self._pool = ConnectionPool(conninfo, min_size=1, max_size=3)
    self._loaded = False
    self._cache: dict[str, Any] = {}
    # timezone, market_open_time, etc. set by _ensure_loaded()

def _ensure_loaded(self) -> None:
    if self._loaded:
        return
    # Query trading_calendars, raise if not found
    ...
    self._loaded = True
```

This allows `TradingCalendar` instances to be created during application startup (e.g., dependency injection) without requiring an immediate DB connection. The first actual method call triggers the load.

### Per-Instance Dict Cache

Same pattern as `InstrumentRegistry` (slice 103). Cache keys:

- `"is_trading_day:{date}"` → `bool`
- `"holidays:{year}"` → `list[Holiday]`
- `"trading_hours:{date}:{session_type}"` → `TradingHours | None`

Cache is cleared by `_invalidate_cache()` (called if we ever add write methods; currently read-only).

### DST Handling in get_expected_bar_count()

The current stub uses `pytz` which has known issues with DST transitions (`localize()` ambiguity). The rewrite uses `zoneinfo.ZoneInfo` (stdlib since 3.9):

```python
from zoneinfo import ZoneInfo

tz = ZoneInfo("America/New_York")
session_start = datetime.combine(trade_date, self.market_open_time, tzinfo=tz)
session_end = datetime.combine(trade_date, self.market_close_time, tzinfo=tz)
duration = session_end - session_start  # correct across DST
```

Using `datetime(..., tzinfo=tz)` with `ZoneInfo` correctly handles DST transitions — the duration between 09:30 and 16:00 ET is always 6.5 hours regardless of DST change. The `_is_dst_transition()` helper is removed as it's no longer needed.

### ConnectionPool Sizing

`TradingCalendar` is read-only and typically queried in tight loops (bar count iteration). A smaller pool (`min_size=1, max_size=3`) is sufficient since queries are simple key lookups and the per-instance cache eliminates most DB round-trips after the first call for a given date.

## Implementation Details

### Module Changes

**`trading_calendar.py` — full rewrite:**

| Component | Change |
|-----------|--------|
| `MarketStatus` | New StrEnum |
| `Holiday.market_status` | Type changes from `str` to `MarketStatus` |
| `TradingHours` | No changes (already uses `SessionType`) |
| `TradingCalendar.__init__` | Takes `conninfo`, creates pool, defers loading |
| `TradingCalendar._ensure_loaded()` | New — lazy metadata load |
| `TradingCalendar.is_trading_day()` | Remove `@lru_cache`, add dict cache, query DB |
| `TradingCalendar.get_trading_hours()` | Implement — compute from metadata + holidays |
| `TradingCalendar.get_holidays()` | Remove `@lru_cache`, add dict cache, query DB |
| `TradingCalendar.get_expected_bar_count()` | Fix DST — use `ZoneInfo`, remove pytz |
| `TradingCalendar._is_dst_transition()` | Remove (no longer needed) |
| `TradingCalendar.close()` | New — close pool |

**Key SQL queries:**

```sql
-- _load_calendar_data (one-time per instance)
SELECT calendar_id, calendar_name, timezone,
       market_open_time, market_close_time,
       has_extended_hours, extended_open_time, extended_close_time
FROM trading_calendars WHERE calendar_id = %s

-- is_trading_day (check for full closure)
SELECT 1 FROM trading_holidays
WHERE calendar_id = %s AND holiday_date = %s AND market_status = 'closed'

-- get_holidays (full year)
SELECT holiday_date, holiday_name, market_status,
       early_close_time, late_open_time
FROM trading_holidays
WHERE calendar_id = %s
  AND EXTRACT(YEAR FROM holiday_date) = %s
ORDER BY holiday_date

-- get_trading_hours helper: check for early close / late open
SELECT market_status, early_close_time, late_open_time
FROM trading_holidays
WHERE calendar_id = %s AND holiday_date = %s
```

**`session_classifier.py` — no code changes needed.** The module already calls `calendar.is_trading_day()`, `calendar.get_trading_hours()`, and `calendar.has_extended_hours` correctly. Once `TradingCalendar` methods return real data instead of raising `NotImplementedError`, the classifier works.

**`data.py` (CLI) — extend with calendars sub-app:**

- `mt data calendars list` — Rich table showing all calendars (ID, name, timezone, hours, ETH)
- `mt data calendars holidays --calendar NYSE --year 2025` — Rich table of holidays for a specific calendar and year

### get_trading_hours Logic

For a given date and session type:

1. If not a trading day (weekend or `market_status='closed'`), return `None`
2. Check for holiday overrides on this date (early close → override `session_end`, late open → override `session_start`)
3. For RTH: build `TradingHours` from `market_open_time`/`market_close_time` (with overrides)
4. For ETH: build from `extended_open_time`/`extended_close_time` (if `has_extended_hours`)
5. For ALL: build from earliest open to latest close
6. All times converted to timezone-aware `datetime` using `ZoneInfo`

### Consumer Updates

The only current consumer of `TradingCalendar` is `SessionClassifier`. It accesses:
- `calendar.timezone` — changes from `None` to a `ZoneInfo` instance (set by `_ensure_loaded`)
- `calendar.is_trading_day(date)` — already matches the signature
- `calendar.get_trading_hours(date, session_type)` — already matches the signature
- `calendar.has_extended_hours` — already matches (bool attribute)

**One adjustment needed:** `SessionClassifier.classify_bar_session()` calls `timestamp.astimezone(calendar.timezone)` which currently expects a `pytz` timezone. After the rewrite, `calendar.timezone` will be a `ZoneInfo` instance, which also works with `astimezone()`. No code change required in `session_classifier.py`.

## Integration Points

### Provides to Other Slices
- `TradingCalendar` class: fully functional calendar lookups usable by any component that needs to know trading schedules
- `MarketStatus` StrEnum: centralized constant for market status values, usable by any module that handles holiday data
- `SessionClassifier` becomes functional (currently blocked by `NotImplementedError`)

### Consumes from Other Slices
- **Slice 102:** `trading_calendars` and `trading_holidays` tables with seed data
- **Slice 100:** `Settings.timescale_db_url` for database connection

## Success Criteria

### Functional Requirements
- `TradingCalendar("NYSE", conninfo)` initializes without DB access; first method call triggers lazy load
- `is_trading_day()` correctly identifies weekends, full closures, and early-close days (early close IS a trading day)
- `get_trading_hours()` returns correct session boundaries including early-close override (13:00 close instead of 16:00)
- `get_holidays()` returns all holidays for a given year with `MarketStatus` enum values
- `get_expected_bar_count()` returns accurate counts across DST transitions
- `SessionClassifier.classify_bar_session()` works end-to-end with the rewritten calendar
- CLI `mt data calendars list` shows registered calendars
- CLI `mt data calendars holidays` shows holidays with status and special hours

### Technical Requirements
- No `@lru_cache` on instance methods — per-instance dict cache only
- No `pytz` dependency — use `zoneinfo.ZoneInfo`
- No magic strings — all market status comparisons use `MarketStatus` enum
- `MarketStatus` values match database column values (no migration needed)
- All SQL uses parameterized queries
- Unit tests with mocked DB connections for all methods
- Integration tests against real TimescaleDB (skip when unavailable)

### Verification Walkthrough

**1. Calendar listing (requires TimescaleDB):**
```bash
uv run mt data calendars list
```
Expected: Rich table showing NYSE, NASDAQ, CME with timezone, market hours, ETH hours, ETH status.

**2. Holiday listing (requires TimescaleDB):**
```bash
uv run mt data calendars holidays --calendar NYSE --year 2025
```
Expected: Table of NYSE holidays for 2025 with market status and special times (e.g., Christmas = closed, Day after Thanksgiving = early_close 13:00).

**3. Unit tests pass (40 tests):**
```bash
uv run pytest test/unit/data/base/test_trading_calendar.py -v
```
Actual: 40 passed in ~0.15s. Covers MarketStatus, Holiday, TradingHours, TradingCalendar core, is_trading_day, get_holidays, get_trading_hours, get_expected_bar_count.

**4. CLI tests pass (6 tests):**
```bash
uv run pytest test/unit/test_cli_data.py -v -k calendars
```
Actual: 6 passed in ~0.2s. Covers calendars list (default + JSON + missing URL), calendars holidays (default + JSON + missing URL).

**5. Full test suite — no regressions:**
```bash
uv run pytest test/unit/ -v
```
Actual: 618 passed, 7 skipped, 0 failed (was 612 before slice 104).

**6. Integration tests (requires TimescaleDB, 12 tests):**
```bash
MT_TIMESCALE_DB_URL=postgresql://... uv run pytest test/integration/test_trading_calendar_integration.py -v
```
Skips automatically when `MT_TIMESCALE_DB_URL` is not set. Includes `SessionClassifier` end-to-end verification (RTH and ETH timestamp classification).

**Caveat:** Steps 1, 2, and 6 require a running TimescaleDB with seed data from migration 750. If the DB is unreachable, the ConnectionPool will timeout after 30 seconds.

## Implementation Notes

### Development Approach

Suggested implementation order:
1. Add `MarketStatus` StrEnum, update `Holiday` dataclass
2. Rewrite `TradingCalendar` core: `__init__`, `_ensure_loaded`, `close`, `_invalidate_cache`
3. Implement `is_trading_day()` and `get_holidays()` with caching
4. Implement `get_trading_hours()` with holiday overrides
5. Fix `get_expected_bar_count()` with ZoneInfo DST handling
6. Remove `_is_dst_transition()` and `pytz` import
7. Add CLI sub-app for calendars
8. Unit tests for each method
9. Integration tests
10. Verify `SessionClassifier` works end-to-end

### Special Considerations

- **pytz removal:** Check if `pytz` is used elsewhere in the codebase before removing from dependencies. If other modules still use it, leave the dependency but stop using it in `trading_calendar.py`.
- **Cache warmup:** For `get_expected_bar_count()` across long date ranges, the per-date cache will naturally warm up. No batch preload is needed — the holiday query per-year is cached, and `is_trading_day` short-circuits on weekends before hitting the cache.
- **Column name mismatch:** The `seed_calendar.py` helper uses different attribute names (`exchange_name`, `market_open`, `market_close`, `extended_open`, `extended_close`) than the actual DDL columns in migration 750 (`calendar_name`, `market_open_time`, `market_close_time`, `extended_open_time`, `extended_close_time`). All SQL queries in this slice must use the migration column names, not the seed script attribute names.
