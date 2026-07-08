---
docType: slice-plan
parent: user/architecture/100-arch.data-storage.md
project: trading
dateCreated: 20260402
dateUpdated: 20260402
status: complete
---

# Slice Plan: Data Storage

## Parent Document
100-arch.data-storage.md — Persistence and retrieval layer for market data: psycopg3 consolidation, instrument registry, trading calendars, coverage analysis, tick schema.

## Foundation Work

1. [x] **(100) psycopg3 Migration and Connection Consolidation** — Migrate `MarketDB` from psycopg2 to psycopg3. Replace SQLAlchemy in `TimescaleMinuteDataDB` with psycopg3 + `psycopg_pool`. Add `market_db_url` and `timescale_db_url` to `Settings` (env vars: `MT_MARKET_DB_URL`, `MT_TIMESCALE_DB_URL`), replacing the `MARKET_PSQL_*`/`TRADING_PSQL_*` env var families and `TimescaleDBConfig`. Remove `psycopg2-binary` and `sqlalchemy` from dependencies. Fix `_create_market_db` silent exit. Update all existing tests. Key API changes: `execute_values` → `cursor.executemany()`/`cursor.copy()`, `RealDictCursor` → `row_factory = dict_row`, `copy_expert` → `cursor.copy()`, explicit transaction management. Effort: 4/5

## Feature Slices

2. [x] **(101) Coverage Analysis and Data Inventory** — Build working coverage and gap queries against existing minute and daily data, wired into CLI as `mt data minute coverage` (or similar). Rewrite `TimescaleMinuteDataCoverage` for psycopg3 (fix async/sync mismatch, replace stub gap detection with real date-range analysis). Report: symbol count, date ranges, row counts, gap locations, compression stats. This is the "get to testing quickly" slice — it produces an actionable inventory of what data we have and where the gaps are. Dependencies: [100]. Risk: Low. Effort: 2/5

3. [x] **(102) Schema - Instrument Registry and Trading Calendar Tables** — Create `instruments` table (canonical_id, symbol, asset_class, venue, tick_size, lot_size, currency, trading_calendar_id, active) and `provider_symbol_mapping` table on the TimescaleDB host. Create `trading_calendars` and `trading_holidays` tables. Seed US equity calendar data (NYSE/NASDAQ market hours, holidays through current year). Add nullable `instrument_id` column to `minute_ohlcv` (safe — continuous aggregates use explicit column lists, no drop/recreate needed). SQL migration scripts, not application code. Dependencies: [100]. Risk: Low. Effort: 2/5

4. [x] **(103) Instrument Registry Integration** — Rewrite `InstrumentRegistry` for psycopg3 with URL-based connection. Fix `@lru_cache` on instance methods (use per-instance cache). Wire to the `instruments` table created in slice 102. Provide `get_by_symbol()` and `get_by_canonical_id()` lookups. Seed initial instrument data from existing `symbol_list` (map AlphaVantage symbols to canonical instruments). CLI command for listing instruments (`mt data instruments` or similar). Dependencies: [102]. Risk: Low. Effort: 2/5

5. [x] **(104) Trading Calendar Integration** — Rewrite `TradingCalendar` for psycopg3 with URL-based connection. Fix hard-fail-on-construct (lazy initialization or explicit `connect()` method). Fix `@lru_cache` on instance methods. Fix DST handling in `get_expected_bar_count()`. Replace magic strings (`'closed'`, `'early_close'`, `'late_open'`) with `StrEnum`. Wire to calendar/holiday tables from slice 102. Enable session-classified queries on minute data (RTH/ETH filtering via `SessionClassifier`). Dependencies: [102]. Risk: Low. Effort: 2/5

## Integration Work

6. [x] **(105) Tick Event Hypertable Schema** — Create `tick_events` hypertable on a separate database instance (not the minute data host). Schema: natural key `(instrument_id, timestamp, sequence_number, source)`, event type discriminator (trade/quote), trade fields (`price, size, exchange, conditions`), quote fields (`bid_price, bid_size, ask_price, ask_size, exchange`). 1hr chunks, space-partitioned by `instrument_id`. Compression policy segmented by `instrument_id`, ordered by `timestamp, sequence_number`. Add `tick_db_url` to `Settings`. SQL migration scripts only — no application code or ingestion (that's Initiative 120). Dependencies: [100]. Risk: Med. Effort: 2/5

## Notes

- Slice 100 is the largest because it touches the two most critical modules (`MarketDB`, `TimescaleMinuteDataDB`) and requires careful testing of all data paths. It's foundation work — everything else depends on psycopg3 being in place.
- Slice 101 is prioritized immediately after foundation because the PM wants to understand existing data gaps before building further. It delivers actionable information without requiring schema changes.
- Slices 102-104 can be done in any order relative to 101, but 103 and 104 both depend on 102 (tables must exist before application code wires to them). 103 and 104 are independent of each other and could be parallelized.
- Slice 105 (tick schema) is integration work because it prepares infrastructure for Initiative 120 but delivers no user-facing functionality on its own. It's independent of slices 101-104 and only depends on slice 100 (for the `Settings` pattern). It carries medium risk because the tick DB instance may not exist yet and the schema design has not been validated against real tick data volumes.
- `IDataService` protocol implementation is explicitly deferred to Initiative 140 (Data Quality). This initiative provides the storage and coverage primitives that 140 will consume.
- The existing `TimescaleMonitor` module is not modified or integrated in this initiative. It may be useful as a foundation for Initiative 140 monitoring but is out of scope here.
- Daily-from-minutes cross-validation (comparing `minute_daily_ohlcv` continuous aggregate against AlphaVantage daily data) is a natural extension of slice 101 but belongs in Initiative 140 (Data Quality).

## Future Work

1. [ ] **Provider Symbol Mapping Backfill** — Link `symbol_list` entries to `instruments` table via `provider_symbol_mapping`. Enable unified queries across daily and minute data by canonical instrument ID. Dependencies: [103]. Effort: 2/5

2. [ ] **instrument_id Backfill for minute_ohlcv** — Populate the nullable `instrument_id` column added in slice 102 by mapping existing `symbol` strings to instrument IDs from the registry. Dependencies: [103]. Effort: 1/5

3. [ ] **Daily-from-Minutes Cross-Validation** — Compare `minute_daily_ohlcv` continuous aggregate against AlphaVantage daily OHLCV data to identify discrepancies. Dependencies: [101, provider symbol mapping]. Effort: 2/5
