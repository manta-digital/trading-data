---
docType: tasks
slice: coverage-analysis-and-data-inventory
project: trading
lld: user/slices/101-slice.coverage-analysis-and-data-inventory.md
dependencies: [100]
projectState: Slice 100 (psycopg3 migration) is complete. TimescaleMinuteDataDB and MarketDB use psycopg3 with ConnectionPool. Settings has market_db_url and timescale_db_url. conftest.py has DB availability fixtures. Existing timescale_minute_coverage.py is broken (async/sync mismatch, wrong API shape, stub gap detection).
dateCreated: 20260402
dateUpdated: 20260402
status: complete
---

## Context Summary
- Working on slice 101: Coverage Analysis and Data Inventory
- Slice 100 (psycopg3 migration) is complete — all DB access uses psycopg3
- `TimescaleMinuteDataDB` has existing `get_coverage_analysis(symbol)` and `get_system_metrics()` methods
- `MarketDB` has `readSymbolsAtDate()` and symbol list queries, uses context manager pattern
- `timescale_minute_coverage.py` exists but is broken — full rewrite as `MinuteCoverageAnalyzer`
- This slice delivers CLI commands for data inventory: `mt data minute coverage`, `mt data minute metrics`, `mt data daily coverage`
- Next planned slice: 102 (Schema - Instrument Registry and Trading Calendar Tables)

---

## Tasks

### Task 1: Add `get_fleet_summary()` to `TimescaleMinuteDataDB`

- [x] **Add `get_fleet_summary(self) -> dict` method to `TimescaleMinuteDataDB`**
  - [x] Add method after existing `get_system_metrics()` in `timescale_minute_db.py`
  - [x] Query: `SELECT symbol, MIN(time) AS earliest, MAX(time) AS latest, COUNT(*) AS row_count FROM minute_ohlcv GROUP BY symbol ORDER BY symbol`
  - [x] Use existing pattern: `pool = self._ensure_pool(); with pool.connection() as conn: with conn.cursor(row_factory=dict_row) as cur:`
  - [x] Return dict with `symbols` (list of per-symbol dicts) and `total_symbols` count
  - [x] Wrap in try/except, log error, return `{"error": str(e)}` on failure (matching existing method pattern)
  - [x] Success: method exists, follows existing code patterns, returns typed dict

### Task 2: Add `detect_gaps()` to `TimescaleMinuteDataDB`

- [x] **Add `detect_gaps(self, symbol: str) -> list[dict]` method to `TimescaleMinuteDataDB`**
  - [x] Add method after `get_fleet_summary()`
  - [x] Use the Level 1 gap detection SQL from the slice design (CTE with `LAG()` window function, >3 calendar day threshold)
  - [x] Return list of `{"gap_start": date, "gap_end": date, "gap_days": int}` dicts
  - [x] Return empty list if no gaps found or on error (log error)
  - [x] Success: method exists, uses parameterized query with `%(symbol)s`, returns list of gap dicts

### Task 3: Add `get_daily_bar_counts()` to `TimescaleMinuteDataDB`

- [x] **Add `get_daily_bar_counts(self, symbol: str) -> list[dict]` method to `TimescaleMinuteDataDB`**
  - [x] Add method after `detect_gaps()`
  - [x] Query: `SELECT date_trunc('day', time)::date AS trade_date, COUNT(*) AS bar_count, MIN(time) AS first_bar, MAX(time) AS last_bar FROM minute_ohlcv WHERE symbol = %(symbol)s GROUP BY date_trunc('day', time)::date ORDER BY trade_date`
  - [x] Return list of `{"trade_date": date, "bar_count": int, "first_bar": datetime, "last_bar": datetime}` dicts
  - [x] Return empty list on error (log error)
  - [x] Success: method exists, uses parameterized query, returns list of bar count dicts

### Task 4: Unit tests for new `TimescaleMinuteDataDB` methods

- [x] **Write unit tests for `get_fleet_summary`, `detect_gaps`, `get_daily_bar_counts`**
  - [x] Add tests to `test/unit/testtimescaleminutedatadb.py` (existing test file)
  - [x] Test `get_fleet_summary`: mock cursor to return sample rows, verify dict structure and aggregation
  - [x] Test `get_fleet_summary`: mock cursor to raise exception, verify error dict returned
  - [x] Test `detect_gaps`: mock cursor to return rows with gaps, verify gap list structure
  - [x] Test `detect_gaps`: mock cursor to return rows with no gaps (all <=3 days), verify empty list
  - [x] Test `detect_gaps`: mock cursor to return empty result, verify empty list
  - [x] Test `get_daily_bar_counts`: mock cursor to return sample rows, verify list structure
  - [x] Test `get_daily_bar_counts`: mock cursor to return empty result, verify empty list
  - [x] Success: all tests pass with `uv run python -m pytest test/unit/testtimescaleminutedatadb.py -v`

**Commit:** `feat: add fleet summary, gap detection, and bar count methods to TimescaleMinuteDataDB`

### Task 5: Rewrite `timescale_minute_coverage.py` as `MinuteCoverageAnalyzer`

- [x] **Replace `TimescaleMinuteDataCoverage` with `MinuteCoverageAnalyzer` in `timescale_minute_coverage.py`**
  - [x] Remove entire `TimescaleMinuteDataCoverage` class and all imports specific to it (`dateutil`, `Optional`, `Any`, `pd`)
  - [x] Add `MinuteCoverageAnalyzer` class with `__init__(self, db: TimescaleMinuteDataDB)` — composition
  - [x] Add `get_fleet_summary(self) -> dict`:
    - [x] Calls `self.db.get_fleet_summary()`
    - [x] Computes `total_rows` (sum), `global_earliest`, `global_latest` from per-symbol results
    - [x] Identifies stalest symbols (oldest `latest` date)
    - [x] Returns composed summary dict
  - [x] Add `get_symbol_coverage(self, symbol: str) -> dict`:
    - [x] Calls `self.db.get_coverage_analysis(symbol)` for date range, row count, compression
    - [x] Calls `self.db.detect_gaps(symbol)` for missing-day gaps
    - [x] Calls `self.db.get_daily_bar_counts(symbol)` for per-day bar counts
    - [x] Merges into single coverage dict
    - [x] Returns composed coverage dict
  - [x] All methods sync, no async, no direct pool/SQL access
  - [x] Keep file under ~100 lines
  - [x] Success: class exists, calls only public `TimescaleMinuteDataDB` methods, no `_ensure_pool()` access, returned dict from `get_symbol_coverage()` includes compression data (ratio, status) from `get_coverage_analysis()`

### Task 6: Unit tests for `MinuteCoverageAnalyzer`

- [x] **Write unit tests for `MinuteCoverageAnalyzer`**
  - [x] Create `test/unit/test_minute_coverage_analyzer.py`
  - [x] Test `get_fleet_summary`: mock `db.get_fleet_summary()` to return sample data, verify global aggregation (total_rows, global dates, stalest symbols)
  - [x] Test `get_fleet_summary`: mock `db.get_fleet_summary()` to return error dict, verify error propagated
  - [x] Test `get_symbol_coverage`: mock all three db methods, verify merged dict contains coverage + gaps + bar_counts
  - [x] Test `get_symbol_coverage`: mock `db.get_coverage_analysis()` to return error, verify error in result
  - [x] Test `get_symbol_coverage`: mock with zero gaps and normal bar counts, verify clean result
  - [x] Success: all tests pass with `uv run python -m pytest test/unit/test_minute_coverage_analyzer.py -v`

**Commit:** `feat: add MinuteCoverageAnalyzer with fleet summary and per-symbol coverage`

### Task 7: Add `get_daily_coverage()` to `MarketDB`

- [x] **Add `get_daily_coverage(self) -> dict` method to `MarketDB`**
  - [x] Add method after existing read methods in `marketdb.py`
  - [x] Use the daily coverage SQL from the slice design (JOIN `symbol_list` with `dailyOHLCVAdjusted`)
  - [x] Return dict with:
    - [x] `total_symbols`: count of symbols in `symbol_list`
    - [x] `symbols_with_data`: count of symbols with at least one daily row
    - [x] `global_earliest`: earliest date across all symbols
    - [x] `global_latest`: latest date across all symbols
    - [x] `stale_symbols`: list of symbols where `lastupdatedday` is more than 7 days ago
    - [x] `error_symbols`: list of symbols where `lastupdatedstatus != 0` or `error_count > 0`
    - [x] `symbols`: list of per-symbol dicts with date range and row count
  - [x] Follow existing `_ensure_pool()` pattern
  - [x] Wrap in try/except, return error dict on failure
  - [x] Success: method exists, returns typed dict, follows existing code patterns

### Task 8: Unit tests for `MarketDB.get_daily_coverage()`

- [x] **Write unit tests for `get_daily_coverage`**
  - [x] Add tests to `test/unit/testmarketdb.py` (existing test file)
  - [x] Test with mocked cursor returning sample rows: verify dict structure, symbol counts, stale/error identification
  - [x] Test with mocked cursor returning empty result: verify zero counts
  - [x] Test with mocked cursor raising exception: verify error dict
  - [x] Success: all tests pass with `uv run python -m pytest test/unit/testmarketdb.py -v`

**Commit:** `feat: add daily coverage summary method to MarketDB`

### Task 9: Add `_create_timescale_db` helper and `minute_app` sub-typer to CLI

- [x] **Add TimescaleDB helper and minute command group to `cli/commands/data.py`**
  - [x] Add `_create_timescale_db(ctx)` helper function (parallel to existing `_create_market_db`):
    - [x] Import `TimescaleMinuteDataDB` inside function (lazy import pattern)
    - [x] Read `settings.timescale_db_url` from `ctx.obj["settings"]`
    - [x] If not set, call `print_error(...)` with clear message about `MT_TIMESCALE_DB_URL` and `raise typer.Exit(1)`
    - [x] Return `TimescaleMinuteDataDB(conninfo=settings.timescale_db_url)`
  - [x] Add `minute_app = typer.Typer(name="minute", help="Minute OHLCV data operations.", no_args_is_help=True)`
  - [x] Register: `data_app.add_typer(minute_app, name="minute")`
  - [x] Success: `mt data minute --help` displays help text, `_create_timescale_db` raises on missing URL

### Task 10: Add `mt data minute coverage` CLI command

- [x] **Add `minute_coverage` command to `minute_app`**
  - [x] Command: `@minute_app.command("coverage")`
  - [x] Parameters: `ctx: typer.Context`, `symbol: str | None = typer.Option(None, "--symbol", help="...")`, `json_output: bool = typer.Option(False, "--json", help="...")`
  - [x] If `--symbol` provided: create `TimescaleMinuteDataDB` via `_create_timescale_db`, create `MinuteCoverageAnalyzer(db)`, call `get_symbol_coverage(symbol)`, format as table or JSON, call `db.close()`
  - [x] If no `--symbol`: create DB, create analyzer, call `get_fleet_summary()`, format as table or JSON, call `db.close()`
  - [x] Text output: use `make_table` for fleet summary (columns: Symbol, Earliest, Latest, Rows), use `print_result` for per-symbol detail
  - [x] JSON output: pass dict to `print_result(..., json_mode=True)`
  - [x] Ensure `db.close()` is called in a `try/finally` block
  - [x] Success: `mt data minute coverage` shows fleet table, `mt data minute coverage --symbol AAPL` shows per-symbol detail including compression info, `--json` works for both

### Task 11: Add `mt data minute metrics` CLI command

- [x] **Add `minute_metrics` command to `minute_app`**
  - [x] Command: `@minute_app.command("metrics")`
  - [x] Parameters: `ctx: typer.Context`, `json_output: bool = typer.Option(False, "--json", help="...")`
  - [x] Create `TimescaleMinuteDataDB` via `_create_timescale_db`, call `db.get_system_metrics()`, format result, call `db.close()`
  - [x] Text output: use `make_table` or formatted text for hypertable stats, compression info, cagg health
  - [x] JSON output: pass dict to `print_result(..., json_mode=True)`
  - [x] Ensure `db.close()` in `try/finally`
  - [x] Success: `mt data minute metrics` shows system health, `--json` flag routes to `print_result(..., json_mode=True)` and produces valid JSON

### Task 12: Add `mt data daily coverage` CLI command

- [x] **Add `daily_coverage` command to `daily_app`**
  - [x] Command: `@daily_app.command("coverage")`
  - [x] Parameters: `ctx: typer.Context`, `json_output: bool = typer.Option(False, "--json", help="...")`
  - [x] Create `MarketDB` via `_create_market_db`, use context manager, call `db.get_daily_coverage()`, format result
  - [x] Text output: summary table with total symbols, symbols with data, date range; list stale/error symbols if any
  - [x] JSON output: pass dict to `print_result(..., json_mode=True)`
  - [x] Success: `mt data daily coverage` shows daily summary, `--json` works

### Task 13: Unit tests for CLI commands

- [x] **Write unit tests for all new CLI commands**
  - [x] Add tests to `test/unit/test_cli_data.py` (new file, or extend existing if present)
  - [x] Use `typer.testing.CliRunner` with the main app
  - [x] Test `mt data minute coverage`: mock `TimescaleMinuteDataDB` and `MinuteCoverageAnalyzer`, verify table output contains expected columns
  - [x] Test `mt data minute coverage --symbol AAPL`: mock per-symbol path, verify detail output
  - [x] Test `mt data minute coverage --json`: verify JSON output is valid
  - [x] Test `mt data minute metrics`: mock `get_system_metrics()`, verify text output
  - [x] Test `mt data minute metrics --json`: mock `get_system_metrics()`, verify valid JSON output
  - [x] Test `mt data daily coverage`: mock `MarketDB.get_daily_coverage()`, verify output
  - [x] Test missing URL error: patch `settings.timescale_db_url = None`, verify exit code 1 and error message
  - [x] Test missing market URL error: patch `settings.market_db_url = None`, verify exit code 1 and error message
  - [x] Success: all tests pass with `uv run python -m pytest test/unit/test_cli_data.py -v`

**Commit:** `feat: add minute coverage, metrics, and daily coverage CLI commands`

### Task 14: Integration tests

- [x] **Write integration tests for coverage queries against real databases**
  - [x] Create `test/integration/test_coverage_integration.py`
  - [x] Use `conftest.py` fixtures: `timescale_db_url` and `market_db_url` (skip when not available)
  - [x] Test `get_fleet_summary()` against real TimescaleDB: verify returns non-empty symbols list
  - [x] Test `get_coverage_analysis(symbol)` against real TimescaleDB with a known symbol: verify returns date range
  - [x] Test `detect_gaps(symbol)` against real TimescaleDB: verify returns list (may be empty)
  - [x] Test `get_daily_bar_counts(symbol)` against real TimescaleDB: verify returns list with bar counts
  - [x] Test `MinuteCoverageAnalyzer.get_fleet_summary()` end-to-end against real TimescaleDB
  - [x] Test `MarketDB.get_daily_coverage()` against real MarketDB: verify returns symbol count
  - [x] All tests use `@pytest.mark.skipif` based on DB URL availability
  - [x] Success: tests pass when DB is available, skip cleanly when not

**Commit:** `test: add integration tests for coverage analysis`

### Task 15: Full validation and cleanup

- [x] **Run full test suite and verify all commands work**
  - [x] Run `uv run python -m pytest test/ -v` — all tests pass, no regressions
  - [x] Verify `mt data minute coverage` output is readable and useful
  - [x] Verify `mt data minute coverage --symbol <symbol> --json` returns valid JSON
  - [x] Verify `mt data minute metrics` displays system health
  - [x] Verify `mt data daily coverage` displays daily summary
  - [x] Verify error messages when DB URLs are not configured
  - [x] Verify no `_ensure_pool()` calls from `MinuteCoverageAnalyzer` (grep check)
  - [x] Verify `timescale_minute_coverage.py` has no remnants of old `TimescaleMinuteDataCoverage` class
  - [x] Update slice design verification walkthrough with actual command output
  - [x] Update CHANGELOG.md with slice 101 entries (Added, Changed, Removed sections)
  - [x] Update slice plan `100-slices.data-storage.md`: check off slice 101
  - [x] Update slice design frontmatter: `status: complete`, `dateUpdated: today`
  - [x] Update this task file frontmatter: `status: complete`, `dateUpdated: today`
  - [x] Success: all tests green, CLI commands functional, docs updated

**Commit:** `docs: mark slice 101 complete, update changelog and verification walkthrough`
