---
docType: slice-design
slice: coverage-analysis-and-data-inventory
project: trading
parent: user/architecture/100-slices.data-storage.md
dependencies: [100]
interfaces: [140]
dateCreated: 20260402
dateUpdated: 20260402
status: complete
---

# Slice Design: Coverage Analysis and Data Inventory

## Overview

Build working coverage and gap queries against existing minute and daily data, wired into CLI as `mt data minute coverage` and `mt data daily coverage`. Rewrite the broken `TimescaleMinuteDataCoverage` module for psycopg3, replacing stubs with real SQL-driven gap detection. Deliver an actionable inventory of what data exists, where the gaps are, and what the system health looks like.

This is the "get to testing quickly" slice — it produces information the PM needs to understand existing data quality before building further ingestion or quality infrastructure.

## Value

**For the PM/operator:** Answers "what data do we have?" with concrete numbers — symbol counts, date ranges, row counts, gap locations, staleness, and compression stats — via simple CLI commands. Currently there is no way to assess data quality without writing ad-hoc SQL.

**For downstream slices:** Initiative 140 (Data Quality) consumes the coverage primitives built here. Gap detection output shapes prioritization for Initiative 120 (Data Acquisition).

## Technical Scope

### In Scope
- Rewrite `TimescaleMinuteDataCoverage` as sync, psycopg3-native `MinuteCoverageAnalyzer`
- Add fleet-wide (all symbols) and per-symbol coverage queries for minute data
- Add missing-day gap detection via SQL date-range analysis
- Add per-day bar count reporting for manual anomaly inspection (partial-day classification deferred to post-slice-104 when trading calendar is available)
- Add daily OHLCV coverage summary (from `MarketDB`)
- New CLI command group: `mt data minute coverage [--symbol X] [--json]`
- New CLI command: `mt data daily coverage [--json]`
- System metrics command: `mt data minute metrics [--json]`
- Unit tests with mocked DB; integration tests that skip when DB unavailable

### Out of Scope
- Gap filling or data repair (Initiative 120/140)
- Partial-day classification (requires trading calendar from slice 104 — early-close days like Christmas Eve have ~210 bars vs normal ~390, so bar-count thresholds alone produce false positives)
- Instrument registry integration (slice 103)
- `IDataService` protocol implementation (Initiative 140)
- Changes to existing `TimescaleMinuteDataDB` read/write methods (new public coverage methods are added, but existing methods are untouched)
- `TimescaleMonitor` module (separate concern, Initiative 140)

## Dependencies

### Prerequisites
- Slice 100 (psycopg3 migration) must be complete — all DB access is psycopg3
- `TimescaleMinuteDataDB` with `get_coverage_analysis()` and `get_system_metrics()` methods
- `MarketDB` with `readSymbolsAtDate()` and symbol list queries
- `Settings` with `timescale_db_url` and `market_db_url`
- `conftest.py` DB availability fixtures from slice 100

### Interfaces Required
- `TimescaleMinuteDataDB` public methods: `get_coverage_analysis()`, `get_system_metrics()` (existing), plus new `get_fleet_summary()`, `detect_gaps()`, `get_daily_bar_counts()` (added by this slice)
- `MarketDB` context manager pattern (`with MarketDB(conninfo=...) as db:`)
- CLI output helpers: `print_result`, `print_error`, `make_table` from `manta_trading.cli.output`

## Architecture

### Component Structure

```
src/manta_trading/
  market/
    timescale_minute_db.py        ← ADD: get_fleet_summary(), detect_gaps() public methods
    timescale_minute_coverage.py  ← REWRITE: MinuteCoverageAnalyzer (sync, public API only)
    marketdb.py                   ← ADD: get_daily_coverage() method
  cli/commands/
    data.py                       ← ADD: minute_app sub-typer, coverage/metrics commands
```

Three layers:
1. **DB layer** — `TimescaleMinuteDataDB` owns all SQL (existing `get_coverage_analysis()`, `get_system_metrics()`, plus new `get_fleet_summary()` and `detect_gaps()` methods). `MarketDB` gains `get_daily_coverage()`.
2. **Analyzer layer** — `MinuteCoverageAnalyzer` composes results from `TimescaleMinuteDataDB` public methods into richer coverage reports. No direct SQL or pool access.
3. **CLI layer** — Typer commands create DB instances, optionally wrap in analyzer, format results as tables or JSON.

### Data Flow

```
CLI command
  → creates TimescaleMinuteDataDB or MarketDB from settings URL
  → creates MinuteCoverageAnalyzer(db=timescale_db) or calls db.get_daily_coverage()
  → analyzer calls db.get_fleet_summary(), db.get_coverage_analysis(), db.detect_gaps()
  → composes results into coverage report dicts
  → CLI formats as Rich table or JSON
```

## Technical Decisions

### Rewrite vs Fix `TimescaleMinuteDataCoverage`

**Decision:** Full rewrite as `MinuteCoverageAnalyzer` in the same file.

**Rationale:** The existing class has six fundamental problems:
1. Async methods awaiting sync DB calls
2. Constructor takes `timescale_service` wrapper that may not exist post-slice-100
3. `get_coverage_summary()` calls `get_coverage_analysis()` with wrong signature
4. Gap detection is a stub (30-day check only)
5. `fill_gaps`, `extend_historical`, `ensure_current_data` are placeholders with no implementation
6. API shape assumptions don't match actual `get_coverage_analysis()` return value

A fix would preserve more problems than it solves. The new class is sync, takes `TimescaleMinuteDataDB` directly, and provides only coverage/gap analysis — no data repair methods (those belong in Initiative 140).

### Gap Detection Strategy

Gap detection operates at two levels, reflecting the reality that minute data gaps are the primary concern (and tick data will inherit the same patterns at higher granularity):

#### Level 1: Missing Days

Detect entire trading days absent from the minute dataset. Query `DISTINCT date_trunc('day', time)` per symbol, use `LAG()` window function to find gaps where consecutive days differ by more than 3 calendar days.

```sql
WITH daily_presence AS (
    SELECT DISTINCT date_trunc('day', time)::date AS trade_date
    FROM minute_ohlcv
    WHERE symbol = %(symbol)s
),
gaps AS (
    SELECT
        trade_date AS gap_end,
        LAG(trade_date) OVER (ORDER BY trade_date) AS gap_start,
        trade_date - LAG(trade_date) OVER (ORDER BY trade_date) AS gap_days
    FROM daily_presence
)
SELECT gap_start, gap_end, gap_days
FROM gaps
WHERE gap_days > 3
ORDER BY gap_start
```

The 3-day threshold handles weekends (2 calendar days between Friday and Monday = no gap) and single holidays (3 days = Friday to Monday with Monday holiday = no gap). Multi-day gaps (>3 calendar days) are reliably flagged. False positives from long weekends (e.g., 4-day gaps around Thanksgiving) are acceptable at this level — slice 104's calendar integration will refine this.

#### Level 2: Per-Day Bar Counts

Report actual bar count per trading day. This surfaces partial days — e.g., provider truncation at row limits ("I can return 1000 rows" cutting off mid-session at 10:04 AM) — without attempting to classify them as complete or incomplete.

```sql
SELECT
    date_trunc('day', time)::date AS trade_date,
    COUNT(*) AS bar_count,
    MIN(time) AS first_bar,
    MAX(time) AS last_bar
FROM minute_ohlcv
WHERE symbol = %(symbol)s
GROUP BY date_trunc('day', time)::date
ORDER BY trade_date
```

**Why we don't classify partial days yet:** A normal RTH session has ~390 bars, but early-close days (Christmas Eve, day before Thanksgiving, etc.) have ~210 bars. Any threshold-based classification without trading calendar data would flag these legitimate short sessions as gaps. Slice 104 provides the calendar; partial-day classification becomes straightforward after that.

**What the operator gets now:** The bar-count-per-day data in the coverage report. A day with 47 bars surrounded by days with 390 is obviously truncated — the data speaks for itself even without automated classification.

#### Design for Expandability

This two-level pattern (missing periods + granularity counts) applies directly to tick data when Initiative 120 arrives:
- Missing periods: same `LAG()` approach on `date_trunc('day', time)` over the tick hypertable
- Density anomalies: bar counts become tick counts per interval, same principle

The `detect_gaps()` and `get_daily_bar_counts()` methods on `TimescaleMinuteDataDB` establish the query patterns that a future `TickCoverageAnalyzer` can mirror.

### CLI Structure

**Decision:** Add `minute_app` sub-typer under `data_app`, parallel to existing `daily_app`.

```
mt data minute coverage          # fleet-wide minute data coverage summary
mt data minute coverage --symbol AAPL  # per-symbol detail with gap analysis
mt data minute metrics           # TimescaleDB system metrics (chunks, compression, caggs)
mt data daily coverage           # daily OHLCV coverage summary from MarketDB
```

All commands support `--json` for machine-readable output.

### New `TimescaleMinuteDataDB` Methods

All SQL stays in the DB layer. Three new public methods are added alongside the existing `get_coverage_analysis()` and `get_system_metrics()`:

1. **`get_fleet_summary()`** → Fleet-wide stats: per-symbol earliest, latest, row count. Single efficient query:
```sql
SELECT
    symbol,
    MIN(time) AS earliest,
    MAX(time) AS latest,
    COUNT(*) AS row_count
FROM minute_ohlcv
GROUP BY symbol
ORDER BY symbol
```

2. **`detect_gaps(symbol)`** → Missing-day gap analysis for a symbol: list of `{gap_start, gap_end, gap_days}` dicts. Uses the Level 1 gap detection SQL.

3. **`get_daily_bar_counts(symbol)`** → Per-day bar counts for a symbol: list of `{trade_date, bar_count, first_bar, last_bar}` dicts. Uses the Level 2 bar count SQL. Enables manual anomaly inspection and will feed automated partial-day classification once trading calendar (slice 104) is available.

All three follow the existing pattern: `pool = self._ensure_pool(); with pool.connection() as conn:`.

### MinuteCoverageAnalyzer Design

The analyzer takes a `TimescaleMinuteDataDB` instance (composition, not inheritance) and calls only its **public** methods. It provides two methods that compose DB results into richer reports:

1. **`get_fleet_summary()`** → Calls `db.get_fleet_summary()`, computes global date range, stalest symbols, and total row count from the per-symbol results
2. **`get_symbol_coverage(symbol)`** → Calls `db.get_coverage_analysis(symbol)` + `db.detect_gaps(symbol)` + `db.get_daily_bar_counts(symbol)`, merges into a single coverage dict with missing-day gaps and per-day bar counts

The analyzer adds no SQL of its own — it is a composition/formatting layer over the DB's public API. This respects the layer boundary: `TimescaleMinuteDataDB` owns all SQL and pool access; the analyzer owns report assembly.

### Daily Coverage via MarketDB

**Decision:** Add a `get_daily_coverage()` method to `MarketDB` rather than a separate analyzer class.

Daily coverage is simpler (one table, one query), and `MarketDB` already has the connection pool. The method returns a summary dict with:
- Total symbols in `symbol_list`
- Symbols with data in `dailyOHLCVAdjusted`
- Date range of daily data
- Symbols with stale data (last updated > 7 days ago)
- Symbols with update errors (where `lastupdatedstatus != 0` or `error_count > 0`)

```sql
SELECT
    s.symbol,
    s.lastupdatedday,
    s.lastupdatedstatus,
    s.error_count,
    MIN(d.date) AS earliest,
    MAX(d.date) AS latest,
    COUNT(d.date) AS row_count
FROM symbol_list s
LEFT JOIN "dailyOHLCVAdjusted" d ON s.symbol = d.symbol
GROUP BY s.symbol, s.lastupdatedday, s.lastupdatedstatus, s.error_count
ORDER BY s.symbol
```

## Implementation Details

### File Changes

**`market/timescale_minute_db.py`** — Add three public methods (~70 lines)
- `get_fleet_summary(self) -> dict` — all-symbols coverage query
- `detect_gaps(self, symbol: str) -> list[dict]` — per-symbol missing-day gap detection
- `get_daily_bar_counts(self, symbol: str) -> list[dict]` — per-day bar counts for anomaly inspection

**`market/timescale_minute_coverage.py`** — Full rewrite (~100 lines)
- Remove `TimescaleMinuteDataCoverage` class
- Add `MinuteCoverageAnalyzer` class:
  - `__init__(self, db: TimescaleMinuteDataDB)` — composition
  - `get_fleet_summary(self) -> dict` — composes `db.get_fleet_summary()` into report
  - `get_symbol_coverage(self, symbol: str) -> dict` — composes `db.get_coverage_analysis()` + `db.detect_gaps()` into report
- All methods are sync, calls only public `TimescaleMinuteDataDB` methods
- No direct pool or connection access

**`market/marketdb.py`** — Add method (~40 lines)
- `get_daily_coverage(self) -> dict` — daily data coverage summary

**`cli/commands/data.py`** — Add minute sub-typer and commands (~100 lines)
- `minute_app = typer.Typer(name="minute", ...)` registered under `data_app`
- `_create_timescale_db(ctx)` helper (parallel to `_create_market_db`)
- `minute_coverage` command — fleet summary or per-symbol detail
- `minute_metrics` command — system metrics
- `daily_coverage` command under `daily_app`

### Helper Pattern for TimescaleDB

```python
def _create_timescale_db(ctx: typer.Context):
    """Create TimescaleMinuteDataDB from settings timescale_db_url."""
    from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error(
            "MT_TIMESCALE_DB_URL not configured. "
            "Set the environment variable or add it to your .env file."
        )
        raise typer.Exit(1)

    return TimescaleMinuteDataDB(conninfo=settings.timescale_db_url)
```

Note: `TimescaleMinuteDataDB` initializes its pool in `__init__` (not via context manager like `MarketDB`), so the helper returns the instance directly. Cleanup is via `db.close()`.

## Integration Points

### Provides to Other Slices
- `MinuteCoverageAnalyzer` class — reusable by Initiative 140 (Data Quality) for coverage baselines
- `MarketDB.get_daily_coverage()` — reusable by any module needing daily data stats
- `_create_timescale_db()` CLI helper — reusable for future minute-data CLI commands
- Gap detection output — informs data acquisition prioritization (Initiative 120)

### Consumes from Other Slices
- Slice 100: psycopg3-based `TimescaleMinuteDataDB` and `MarketDB`, `Settings` with dual URLs, `conftest.py` fixtures

## Success Criteria

### Functional Requirements
- `mt data minute coverage` displays fleet-wide summary: symbol count, total rows, global date range, per-symbol row counts
- `mt data minute coverage --symbol AAPL` displays per-symbol detail: date range, row count, missing-day gaps, per-day bar counts (for anomaly inspection), compression info
- `mt data minute metrics` displays TimescaleDB health: hypertable size, chunk count, compression ratio, continuous aggregate status
- `mt data daily coverage` displays daily data summary: symbol count, date range, stale symbols, error symbols
- All commands support `--json` for machine-readable output
- Missing-day gap detection identifies multi-day gaps (>3 calendar days between consecutive trading days)
- Per-day bar counts are reported, enabling manual identification of partial/truncated days
- Commands fail explicitly with a clear error message when DB URL is not configured

### Technical Requirements
- `MinuteCoverageAnalyzer` has unit tests with mocked DB (fleet summary, symbol coverage, gap detection)
- `MarketDB.get_daily_coverage()` has unit tests with mocked cursor
- CLI commands have unit tests (using CliRunner with mocked DB)
- Integration tests skip when `MT_TIMESCALE_DB_URL` / `MT_MARKET_DB_URL` not set
- No async code — all methods are sync
- New `TimescaleMinuteDataDB` methods (`get_fleet_summary`, `detect_gaps`, `get_daily_bar_counts`) have their own unit tests
- No changes to existing `TimescaleMinuteDataDB` read/write methods

### Verification Walkthrough

These commands assume `MT_TIMESCALE_DB_URL` and `MT_MARKET_DB_URL` are set in `.env` or environment.

```bash
# 1. Fleet-wide minute coverage summary
mt data minute coverage
# Expected: Rich table "Minute Data Fleet Summary" with columns: Symbol, Earliest, Latest, Rows
# Shows per-symbol row counts with comma formatting (e.g. "500,000")
# Footer: "Total: N symbols, N rows"

# 2. Per-symbol minute detail with gaps and bar counts
mt data minute coverage --symbol AAPL
# Expected: Rich table "AAPL Coverage" with: Earliest, Latest, Total rows,
#   Gaps (>3 days) count, Days with data count, Avg compression, Compression status
# If gaps exist, a second table "Gaps (>3 calendar days)" with Start/End/Days columns

# 3. JSON output for scripting
mt data minute coverage --json
# Expected: JSON dict with total_symbols, total_rows, global_earliest, global_latest,
#   stalest_symbols, and symbols array

# 4. System metrics
mt data minute metrics
# Expected: Rich table "TimescaleDB System Metrics" with: Hypertable name,
#   Chunks, Size, Total/Compressed chunks, Avg compression ratio
# If caggs exist, a second table "Continuous Aggregates"

# 5. Daily coverage summary
mt data daily coverage
# Expected: Rich table "Daily OHLCV Coverage" with: Total symbols, Symbols with data,
#   Earliest/Latest dates, Stale symbols (>7d) count, Error symbols count
# If stale/error symbols exist, yellow/red-highlighted lists below the table

# 6. Error case — missing DB URL
MT_TIMESCALE_DB_URL= mt data minute coverage
# Expected: "Error: MT_TIMESCALE_DB_URL not configured..." to stderr, exit code 1

# 7. Tests pass
uv run python -m pytest test/unit test/integration --tb=short
# Verified: 497 passed, 7 skipped (DB-dependent), 0 failures
# Slice 101 tests: 63 passed, 13 skipped

# 8. Architectural checks
# grep -r "_ensure_pool" src/manta_trading/market/timescale_minute_coverage.py → no matches
# grep -r "TimescaleMinuteDataCoverage" src/ → no matches
```

## Implementation Notes

### Development Approach

Suggested order:
1. `MinuteCoverageAnalyzer` class with unit tests (gap detection is the core logic)
2. `MarketDB.get_daily_coverage()` with unit tests
3. CLI commands (`_create_timescale_db` helper, then commands) with unit tests
4. Integration tests against real DB (if available)
5. Verify via CLI against live data

### Testing Strategy
- **Unit tests:** Mock `TimescaleMinuteDataDB` and `MarketDB` cursor/pool. Test gap detection with known date sequences (no gaps, single gap, multiple gaps, weekend handling). Test fleet summary aggregation. Test CLI output formatting.
- **Integration tests:** Use `conftest.py` fixtures from slice 100 (`market_db_url`, `timescale_db_url`). Skip when unavailable. Verify actual query execution against real data. These are validation, not primary test coverage.
