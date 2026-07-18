---
docType: architecture
component: data-storage
project: trading
parent: user/project-guides/001-initiative-plan.trading.md
dependencies:
  - 900-arch.foundation-cleanup
relatedSlices: []
riskLevel: medium
dateCreated: 20260401
dateUpdated: 20260418
status: complete
archIndex: 100
---

# Data Storage Architecture

## Overview

Data Storage is the persistence and retrieval layer for all market data in manta-trading. It manages daily OHLCV, minute OHLCV, and (future) tick-level data across TimescaleDB hypertables, providing continuous aggregates, compression, and a unified query interface.

**Scope:** TimescaleDB schema design, instrument registry, trading calendars, the storage side of idempotent ingestion, and the query interface consumed by downstream services. Does not include data acquisition (Initiative 120), quality monitoring (Initiative 140), or serving APIs (Initiative 160).

**Motivation:** The project has two proven storage components — a psycopg2-based daily OHLCV layer (`MarketDB`) and a SQLAlchemy/TimescaleDB minute layer (`TimescaleMinuteDataDB`) — plus a set of `data/base/` modules (instrument registry, trading calendar, session classifier, adjustment policy) that were designed but not yet integrated into a working pipeline. The existing data has gaps that need to be understood before building further. This initiative consolidates these components under a coherent architecture, migrates to a single modern DB access layer (psycopg3), establishes the instrument model, and gets the storage layer to a state where we can actually run queries and tests against real data.

## Design Goals

- **Consolidate on psycopg3** — Migrate all database access from psycopg2 and SQLAlchemy to psycopg3 (`psycopg`). psycopg2 is in maintenance mode; SQLAlchemy adds ORM/query-builder weight we don't use (all queries are raw SQL against TimescaleDB hypertables). psycopg3 provides native async, built-in connection pooling (`psycopg_pool`), improved COPY support, and pipeline mode — covering everything we need with a single, modern dependency. This parallels the aiohttp→httpx consolidation in slice 903.

- **Establish the instrument model** — Define a canonical instrument identity (`instrument_id`) that all storage tables reference. The existing `InstrumentRegistry` dataclass provides the shape; this initiative wires it into actual schema and makes it the source of truth for symbol resolution.

- **Integrate trading calendars** — Connect the existing `TradingCalendar` and `SessionClassifier` modules so that gap detection and session-aware queries work against real data. These modules exist but are not yet used by any storage or query path.

- **Get to testable state quickly** — The existing minute data (500+ symbols, continuous aggregates, 95% compression) has known gaps. The priority is understanding and documenting those gaps over building new ingestion. We should be able to run coverage queries and session-classified reads against existing data as soon as possible.

- **Prepare for tick storage** — Design the tick hypertable schema (trade, quote, BBO event model) so that Initiative 120 can write to it, but do not build tick ingestion or processing in this initiative.

## Architectural Principles

- **Keep what works** — `MarketDB` and `TimescaleMinuteDataDB` are proven at scale. The psycopg3 migration changes the driver, not the logic — query patterns, bulk write strategies, and table schemas remain the same. The psycopg3 sync API is close to psycopg2's, making `MarketDB` migration straightforward. `TimescaleMinuteDataDB`'s SQLAlchemy usage is thin (raw SQL + COPY), so replacing the engine with a psycopg3 connection pool is a simplification, not a rewrite.

- **Schema-first** — Define all TimescaleDB schemas (tick hypertable, instrument registry table, trading calendar table) before writing application code. Schema migrations are the deliverable; application integration follows.

- **Single DB access layer, multiple databases** — All modules use psycopg3 with `psycopg_pool.ConnectionPool` (sync) or `AsyncConnectionPool` (async). The project has two PostgreSQL instances serving different roles: a plain PostgreSQL 16 host (`<prototype-host>`) for daily OHLCV data, and a TimescaleDB-enabled PostgreSQL 17 host (`<db-host>`) for minute data and continuous aggregates. `Settings` provides separate connection URLs: `market_db_url` (daily) and `timescale_db_url` (minute/tick). No module accepts a `db_config` dict or individual connection params — all take a connection URL string. The `data/base/` modules don't have real tables yet and will be written directly for psycopg3 with URL-based connection, not migrated from the existing psycopg2 `db_config` pattern.

- **Real data over abstractions** — Prefer testing against the existing TimescaleDB instance with real minute/daily data over building elaborate mock frameworks. Use `conftest.py` fixtures that skip when DB is unavailable, but design tests to run against real data when present.

- **Incremental integration** — Each slice should produce something testable. Don't build the full instrument→calendar→session→storage pipeline in one pass.

## Current State

Two independent storage layers exist on separate hosts, using two different DB drivers:

**Daily OHLCV (`MarketDB`) — PostgreSQL 16 on `<prototype-host>`:**
- Database: `market-stocks-test`, user: `postgres`
- psycopg2 with connection pooling, `execute_values` bulk writes
- Tables: `symbol_list`, `objects_last_updated`, `dailyOHLCVAdjusted`
- Functional and wired into `mt data daily` CLI commands (slice 903)
- Takes individual connection params (`dbname`, `user`, `password`, `host`, `port`)
- Env vars: `MARKET_PSQL_*` (not integrated with `Settings`)

**Minute OHLCV (`TimescaleMinuteDataDB`) — PostgreSQL 17 + TimescaleDB on `<db-host>`:**
- Database: `trading_test`, user: `trading_app`
- SQLAlchemy engine with `QueuePool`, `COPY FROM STDIN` bulk writes (13k+ rows/sec)
- All queries are raw SQL via `connection.execute(text(...))` — no ORM or query builder usage
- SQLAlchemy serves only as a connection pool manager; its weight is unjustified for this use case
- Hypertable: `minute_ohlcv` (7-day chunks per `MINUTE_OHLCV_CHUNK_INTERVAL`; re-chunked from the pathological 4hr interval in slice 166), columns: `time, symbol, open, high, low, close, volume`
- Continuous aggregates: 5min, 15min, 1hr, 4hr, daily, weekly, monthly (v2 materialized views)
- Compression: ~95% ratio
- Takes a `db_config` dict via `TimescaleDBConfig` which reads `TRADING_PSQL_*` env vars
- Existing data has gaps that have not been characterized

**`data/base/` modules (designed, not integrated):**
- `InstrumentRegistry`: psycopg2-backed, `lru_cache` lookups, `Instrument` dataclass with `canonical_id`, `asset_class`, `venue`, `tick_size`, etc. — no corresponding DB table exists yet. Uses `@lru_cache` on instance methods (causes cross-instance cache pollution and prevents GC; must be fixed in psycopg3 rewrite)
- `TradingCalendar`: psycopg2-backed, holiday-aware, session hours with ETH support — no corresponding DB table exists yet. Same `@lru_cache` instance method issue. Also hard-fails at construction if DB row is missing (see Technical Considerations)
- `SessionClassifier`: vectorized bar classification into RTH/ETH — functional but not used by any query path
- `AdjustmentPolicy`: enums and OHLCV validation — functional, used by minute processor
- `IDataService` protocol: `get_health_metrics()`, `detect_gaps()`, `get_quality_report()` — defined but not implemented by any service

**DB driver state:**
- psycopg2 is in maintenance mode (no new features, superseded by psycopg3)
- SQLAlchemy adds ORM/query-builder capability we don't use
- Three dependencies (psycopg2-binary, sqlalchemy, psycopg_pool would be needed anyway) can be replaced by one (psycopg + psycopg_pool)

**Partially built modules (exist but incomplete):**
- `TimescaleMinuteDataCoverage` (`timescale_minute_coverage.py`): delegates to `TimescaleMinuteDataDB.get_coverage_analysis()` for basic coverage queries (date range, row count, compression stats). Gap detection is a stub (only checks "is data older than 30 days"). `fill_gaps`, `extend_historical`, `ensure_current_data` are placeholder methods. The coverage query is usable; everything else needs replacement.
- `TimescaleMonitor` (`timescale_monitoring.py`): async context manager for operation timing and threshold-based log alerts. Functional as instrumentation but has no consumers, no persistence, and uses a global singleton anti-pattern. May be useful as a foundation for Initiative 140 but should not be treated as complete.

**What's missing:**
- No instrument registry table in the database
- No trading calendar/holiday table in the database
- No tick-level schema
- No unified DB access layer — two drivers (psycopg2, SQLAlchemy), two connection patterns, two env var families (`MARKET_PSQL_*`, `TRADING_PSQL_*`), neither integrated with `Settings`
- `Settings.db_url` exists but is a single field — needs to become `market_db_url` and `timescale_db_url`
- No way to run coverage or gap analysis against existing minute data via CLI
- `symbol` column in `minute_ohlcv` is a plain string, not linked to an instrument registry

## Envisioned State

A unified data storage layer where:

- An **instrument registry table** maps canonical IDs to provider symbols, with metadata (asset class, venue, tick size). All time-series tables gain an optional `instrument_id` column alongside the existing `symbol` string. The instrument registry is authoritative for *metadata* (what instruments exist, their venues, tick sizes, calendars). **Symbol strings remain the primary *query* key for time-series data** until `instrument_id` is backfilled and proven. Service interfaces (`IDataService`, gap detection, coverage) continue to accept `symbol: str` — migrating query interfaces to `instrument_id` is a future step, not a prerequisite for this initiative.

- **Trading calendar and holiday tables** store market hours and closures. The existing `TradingCalendar` class reads from these tables instead of requiring configuration at construction time.

- A **tick hypertable** (`tick_events`) stores trade/quote/BBO events with `(instrument_id, timestamp, sequence, source)` natural key, space-partitioned by instrument_id, 1hr chunks. Schema exists and is tested; population happens in Initiative 120.

- **Existing minute and daily storage** continues to work as-is, with minimal modifications: `minute_ohlcv` gains an optional `instrument_id` column (nullable, backfilled later). `MarketDB` daily tables remain unchanged. **Note on parallel registries:** `MarketDB.symbol_list` is an AlphaVantage-sourced table (symbol, name, exchange, IPO date, delisting status). The new `instruments` table is a canonical registry with richer metadata (venue, tick size, asset class, trading calendar). These coexist: `symbol_list` serves the daily pipeline as-is; `instruments` becomes authoritative for minute/tick data and cross-provider resolution. A provider symbol mapping table will eventually link AlphaVantage symbols to canonical instrument IDs, but daily table migration is not in scope for this initiative.

- **Coverage and gap queries** are available via CLI (`mt data coverage` or similar) so that existing data quality can be assessed before building new ingestion.

- **All DB access uses psycopg3** with `psycopg_pool` for connection management. `Settings` provides `market_db_url` and `timescale_db_url` for the two database instances. `psycopg2-binary` and `sqlalchemy` are removed from dependencies. `TimescaleDBConfig` and the `MARKET_PSQL_*`/`TRADING_PSQL_*` env var families are replaced by the `Settings` URL fields (env vars: `MT_MARKET_DB_URL`, `MT_TIMESCALE_DB_URL`).

## Technical Considerations

- **instrument_id migration for minute_ohlcv** — Adding `instrument_id` to a large hypertable with existing data requires care. The column should be nullable initially and backfilled via a migration script that maps existing `symbol` strings to instrument IDs. This is a data migration, not a schema-breaking change — existing queries by symbol continue to work.

- **Trading calendar data source and ordering** — The calendar tables need to be populated with actual market hours and holidays. For US equities (the primary use case), this data is well-known and can be seeded from a static dataset. Database storage is preferred for consistency with the instrument registry pattern. **Ordering constraint:** The existing `TradingCalendar` class hard-fails at construction (`ValueError`) if its `calendar_id` row is absent from the database — `_load_calendar_data()` runs unconditionally in `__init__`. This means schema creation and seed data must be a single atomic slice that completes before any code instantiates `TradingCalendar`. The psycopg3 rewrite of this module should also make initialization more defensive (lazy-load or explicit `connect()` method rather than fail-on-construct).

- **psycopg2 → psycopg3 migration** — psycopg3's sync API is similar to psycopg2 but not identical. Key differences: `cursor.execute()` returns the cursor (chainable), `%s` placeholders still work but the native format is `%(name)s` or positional `$1`, `execute_values` moves to `cursor.executemany()` or `cursor.copy()`, `RealDictCursor` is replaced by `cursor.row_factory = dict_row`, connection pooling uses `psycopg_pool.ConnectionPool` instead of `psycopg2.pool.SimpleConnectionPool`, and transaction defaults differ (psycopg3 requires explicit transaction management or `autocommit=True`). This is moderate-effort migration requiring careful testing of all data paths — not a find-and-replace. `MarketDB` has the largest surface area. The `data/base/` modules aren't wired to tables yet so they can be written directly for psycopg3.

- **SQLAlchemy removal from TimescaleMinuteDataDB** — The module uses SQLAlchemy purely as a connection pool around raw SQL. Migration path: replace `create_engine()` + `engine.connect()` with `psycopg_pool.ConnectionPool(conninfo)`. The `COPY FROM STDIN` path currently drops through SQLAlchemy to the underlying psycopg2 connection; with psycopg3 this becomes a first-class `cursor.copy()` call, which is simpler and more efficient.

- **Tick schema design** — The archived architecture doc (050-arch) defines a comprehensive tick event model (trade, quote, BBO, NBBO, depth, status). For the initial schema, focus on trade and quote events only — these cover the primary use cases. The tick hypertable should be on a **separate database instance** from minute data — volume difference (~28x at modest scale), different chunk sizing (1hr vs the 7-day minute interval set in slice 166), different compression/retention policies, and operational independence all argue against sharing. Architectural outline for the schema slice:
  - Natural key: `(instrument_id, timestamp, sequence_number, source)`
  - Event type column: enum discriminator (trade, quote) — single table, not separate tables per type
  - Trade fields: `price, size, exchange, conditions`
  - Quote fields: `bid_price, bid_size, ask_price, ask_size, exchange`
  - Hypertable: 1hr chunks, space-partitioned by `instrument_id`
  - Compression: segment by `instrument_id`, order by `timestamp, sequence_number`
  - Detailed column types, indexes, and retention policies are slice-level decisions

- **Continuous aggregate compatibility** — The existing continuous aggregates explicitly SELECT `(time_bucket, symbol, open, high, low, close, volume)` and GROUP BY `(time_bucket, symbol)`. They do not use `SELECT *`. Adding a nullable `instrument_id` column to `minute_ohlcv` is therefore invisible to the continuous aggregates — no drop/recreate cycle is needed. The continuous aggregates remain symbol-based. If a future slice needs instrument_id-based aggregates, those would be new views, not modifications to existing ones.

- **Existing data gaps** — The PM has noted that existing data has gaps. Before building new ingestion or quality infrastructure, we need to characterize what we have: which symbols, what date ranges, where the gaps are. This is a prerequisite for meaningful testing and should be an early slice.

## Anticipated Slices

- **Schema and migrations** — Create instrument registry, trading calendar, and holiday tables. Add `instrument_id` column to `minute_ohlcv`. Create tick event hypertable. Seed US equity calendar data.

- **psycopg3 migration and connection consolidation** — Migrate `MarketDB` from psycopg2 to psycopg3. Replace SQLAlchemy in `TimescaleMinuteDataDB` with psycopg3 + `psycopg_pool`. Add `market_db_url` and `timescale_db_url` to `Settings`, replacing the `MARKET_PSQL_*`/`TRADING_PSQL_*` env var families and `TimescaleDBConfig`. Remove `psycopg2-binary` and `sqlalchemy` from dependencies. Fix `_create_market_db` silent exit (CONCERN from review).

- **Coverage analysis** — Build coverage/gap queries against existing minute and daily data. Wire into CLI. This is the "get to testing quickly" slice — it produces actionable information about what data we have.

- **Instrument registry integration** — Wire `InstrumentRegistry` to its new DB table. Provide symbol→instrument_id resolution. Backfill `instrument_id` in `minute_ohlcv` for existing data.

- **Calendar integration** — Wire `TradingCalendar` to its new DB table. Enable session-classified queries (RTH/ETH filtering on minute data reads).

## Related Work

- **900-arch.foundation-cleanup** (complete) — Provides CLI framework, config system (`Settings`), logging, and provider registry that this initiative builds on.
- **Archived 050-arch.data-storage-and-acquisition** — Contains detailed prior design work for instrument model, tick schema, continuous aggregates, and IDataService interface. Informs this architecture but is superseded by it.
- **Existing modules:**
  - `src/manta_trading/market/marketdb.py` — Daily OHLCV storage (migrate psycopg2 → psycopg3)
  - `src/manta_trading/market/timescale_minute_db.py` — Minute OHLCV storage (migrate SQLAlchemy → psycopg3)
  - `src/manta_trading/data/base/` — Instrument, calendar, session, adjustment modules (integrate)
  - `src/manta_trading/data/base/service_interface.py` — `IDataService` protocol (implement in Initiative 140)
  - `src/manta_trading/market/schema/migrations/` — Schema-migration framework (per-DB tracks: `minute`, `daily`; `schema_migrations` tracking table per DB). Originally introduced for the minute DB in slice 102; extended to unified cross-DB management in slice 150 (Initiative 140). Authoritative source for all DB schema changes; no other migration path is sanctioned. Any initiative adding a schema change appends to the relevant track's migration list.
- **Initiative 120 (Data Acquisition)** — Depends on tick schema and instrument registry from this initiative.
- **Initiative 140 (Data Quality)** — Depends on coverage analysis and calendar integration from this initiative.
