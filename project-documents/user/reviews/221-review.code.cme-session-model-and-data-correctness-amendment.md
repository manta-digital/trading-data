---
docType: review
layer: project
reviewType: code
slice: cme-session-model-and-data-correctness-amendment
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/221-slice.cme-session-model-and-data-correctness-amendment.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260928
dateUpdated: 20260928
reviewedSha: 2e4bca646c9c22f36416ae9f8723cc0f0556cdb9
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 18
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "`mt data calendars sessions` crashes ungracefully on an unknown calendar"
    location: "src/manta_trading/cli/commands/calendar_sessions.py:63-72"
  - id: F002
    severity: concern
    category: architecture
    summary: "Runtime constants module coupled to migration seed data"
    location: "src/manta_trading/data/tick/constants.py:12"
  - id: F003
    severity: note
    category: error-handling
    summary: "Broad `except Exception` in the per-calendar auto-extend loop"
    location: "src/manta_trading/data/maintenance/auto_extend.py:97-108"
  - id: F004
    severity: note
    category: correctness
    summary: "Sub-second truncation in verification script's nearest-session lookup"
    location: "scripts/verify_cme_sessions.py#_nearest"
  - id: F005
    severity: note
    category: migrations
    summary: "Migration 057's `SET NOT NULL` step is coupled to exactly two hardcoded calendars"
    location: "src/manta_trading/market/schema/migrations/minute.py#_holidays_seeded_through_sql"
  - id: F006
    severity: pass
    category: uncategorized
    summary: "`SessionIndex` and `session_interval` boundary logic"
    location: "src/manta_trading/data/base/session_index.py"
  - id: F007
    severity: pass
    category: uncategorized
    summary: "CME_EQUITY seed data uses parameterized inserts and matches the documented span"
    location: "src/manta_trading/market/schema/seed_cme_calendar.py:414-439"
---

# Review: code — slice 221

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] `mt data calendars sessions` crashes ungracefully on an unknown calendar

`data_calendar_sessions` only catches `OutOfPopulatedRangeError`. `_session_rows` calls `cal.get_holidays(year)`, which calls `TradingCalendar._ensure_loaded()` (`src/manta_trading/data/base/trading_calendar.py:187-190`); for an unknown `calendar_id` that raises a bare `ValueError` ("Trading calendar '...' not found in database"). That exception is not caught anywhere in `data_calendar_sessions`, so it propagates as an unhandled traceback instead of the clean `print_error(...)` + `typer.Exit` pattern the sibling `data extend` command already uses for the identical situation (`src/manta_trading/cli/commands/data.py:1294-1297`, which pre-validates `calendar_id` against `trading_calendars`). The new test file `test/unit/cli/commands/test_calendar_sessions.py` mocks `TradingCalendar` entirely and never exercises this path, so the gap is untested. Validate the calendar exists (or catch `ValueError`) before proceeding, mirroring `data_extend`.

### [CONCERN] Runtime constants module coupled to migration seed data

`FUTURES_PRODUCT_CALENDAR`/`calendar_for_product` import `CME_EQUITY_CALENDAR_ID` from `manta_trading.market.schema.seed_cme_calendar`. That module's own docstring says it exists to hold "the CME Globex equity calendar" seed data for migrations — it's ~440 lines dominated by one-off, dated holiday literals and Wayback-machine source comments (`src/manta_trading/market/schema/seed_cme_calendar.py:1-15, 60-390`). `tick/constants.py` is documented elsewhere in the codebase as "the one location" for tick-domain mappings (module docstring, slice 220 design). Pulling a calendar-id constant from a migration/reference-data file into the runtime constants module used by every tick ingestion path is a layering inversion: it couples a hot runtime module to migration internals, and every future calendar this seed file gains grows that import for no runtime benefit. `CME_EQUITY_CALENDAR_ID` should live in a small shared module (e.g. alongside `TradingCalendar`) that both `seed_cme_calendar.py` and `tick/constants.py` depend on, rather than one depending on the other.

### [NOTE] Broad `except Exception` in the per-calendar auto-extend loop

The refactored loop wraps calendar lookup + `extend_calendar_sessions` + commit in a single `except Exception as exc: _logger.exception(...); result.error = str(exc); continue`. This satisfies the letter of the exception-handling rule (logs at ERROR via `logger.exception`, doesn't silently pass, and functions as a per-item boundary so one bad calendar doesn't stop the whole background job), but it's broader than "catch specific exception types" and would also silently swallow, e.g., a programming error inside `extend_calendar_sessions`. Since `extend_calendar_sessions` now has a well-defined exception surface (`ValueError` for an unknown calendar, `psycopg.Error` for DB failures), narrowing the catch would let unexpected bugs surface instead of being reported only as a generic per-calendar "error" string. Low priority since this mirrors the pre-existing pattern being consolidated, not a new anti-pattern.

### [NOTE] Sub-second truncation in verification script's nearest-session lookup

`_nearest` computes `int(s.open_utc.timestamp()) * 1_000_000_000` — truncating to whole seconds *before* scaling to nanoseconds, discarding any sub-second component of `open_utc`. Every session open in this codebase currently falls on a whole second (built from `time()` objects with no microseconds), so this is harmless today, but it's inconsistent with the nanosecond-precise handling used everywhere else in the same file (`_to_ns`-equivalent logic, `_utc`) and would silently misreport if a sub-second boundary were ever introduced.

### [NOTE] Migration 057's `SET NOT NULL` step is coupled to exactly two hardcoded calendars

`_holidays_seeded_through_sql` only backfills `holidays_seeded_through` for `NYSE_CALENDAR`/`NASDAQ_CALENDAR` before making the column `NOT NULL`. This is correct today (confirmed only NYSE/NASDAQ are seeded before migration 057, and migration 058's `cme_equity_calendar_insert` always supplies the column — `src/manta_trading/market/schema/seed_cme_calendar.py:420-426`), but the migration would fail outright if any other calendar row existed in `trading_calendars` prior to 057 without this backfill covering it. Worth a one-line comment noting the invariant it depends on, for the next person adding a calendar-seeding migration.

### [PASS] `SessionIndex` and `session_interval` boundary logic

Vectorized (`locate_ns`) and scalar (`locate`) lookups share one position routine and correctly handle inclusive open/close boundaries and adjacent (non-overlapping) sessions; construction validates ordering/overlap/inversion. `session_interval`'s open-after-close dating rule is applied uniformly across `populate_trading_sessions`, `TradingCalendar._build_trading_hours`, and the new `session_extension` path, removing the prior duplicated `datetime.combine` logic (`trading_calendar.py:550-561`, `session_population.py:23-46`).

### [PASS] CME_EQUITY seed data uses parameterized inserts and matches the documented span

`cme_equity_calendar_insert`/`cme_equity_holidays_insert` build parameterized `INSERT ... ON CONFLICT DO NOTHING` statements (no f-string SQL with untrusted data), and the clamping tests (`test/integration/test_calendar_extension.py`) directly exercise `clamped_by_holiday_bound` both inside and beyond the holiday bound.

### Run Digest

- Response length: 7554 chars
- Response is newline-free: no
- Tool calls made: 18
- Tool calls failed: 0
- Stop reason: end_turn
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
