---
docType: review
layer: project
reviewType: arch
slice: data-storage
project: trading
verdict: FAIL
sourceDocument: project-documents/user/architecture/900-arch.data-storage.md
aiModel: sonnet
status: complete
dateCreated: 20260401
dateUpdated: 20260401
---
  
  [FAIL] Connection consolidation is incomplete for InstrumentRegistry and TradingCalendar
    category: completeness
    
    The "Connection consolidation" slice says: standardize all modules on `Settings.db_url`, and update `TimescaleMinuteDataDB` to accept a URL string.
`MarketDB` already accepts individual params and is handled by the `_create_market_db` URL-parsing helper in `data.py`.
    
    But `InstrumentRegistry.__init__` takes `db_config: dict` (keys: host, port, database, user, password) and `TradingCalendar.__init__` takes the 
same shape. Neither accepts a URL string. The document mentions only `TimescaleMinuteDataDB` needs updating. The other two modules — the 
newly-integrated ones that don't even have DB tables yet — are completely silent on how they will receive their connection configuration from 
`Settings.db_url`.
    
    This leaves three options, none of which are stated: (a) add URL-to-dict decomposition at every call site for these two modules, (b) modify both 
modules to accept a URL (contradicting "Keep what works"), or (c) these modules silently continue to be configured via a separate dict that comes from 
somewhere unspecified. The consolidation strategy is half-described.
    
    ---
  [FAIL] TradingCalendar hard-fails at construction if table rows are absent
    category: consistency
    
    `TradingCalendar.__init__` calls `_load_calendar_data()` unconditionally. That method executes `SELECT … FROM trading_calendars WHERE calendar_id =
%s` and raises `ValueError(f"Calendar '{self.calendar_id}' not found in database")` if the row is missing.
    
    The "Schema and migrations" slice creates the `trading_calendars` table and seeds US equity calendar data. The "Calendar integration" slice then 
wires `TradingCalendar` to it. Between those two slices — and even within "Schema and migrations" before the seed data is loaded — any code path that 
instantiates `TradingCalendar` will raise a `ValueError`. The architecture states this module should be "connected," with no note that it is completely
non-functional until both DDL and seed data are present. This is not a soft warning; it is an uncaught exception at object construction. Any slice that
touches this module must be ordered strictly after seed data exists.
    
    ---
  [FAIL] Continuous aggregate migration cost is deferred but is a blocking decision
    category: feasibility
    
    The document says (Technical Considerations): "Adding `instrument_id` to `minute_ohlcv` may require recreating continuous aggregates… This should 
be evaluated during slice design."
    
    This is the wrong place to defer it. TimescaleDB's continuous aggregate constraints are not speculative — an `ALTER TABLE` on a hypertable with 
dependent continuous aggregates requires explicitly dropping those views, altering the table, then recreating and refreshing all views. With 7 
continuous aggregates (5min, 15min, 1hr, 4hr, daily, weekly, monthly) across 500+ symbols and 95% compression, this is potentially hours of work during
the migration window. The alternative path — adding the nullable column without altering the continuous aggregate queries — is only viable in newer 
TimescaleDB versions (2.x) and only if the continuous aggregate SELECT lists do not include `*`. Whether that path is safe depends on the actual 
TimescaleDB version in use, which is not stated anywhere in the document. This is not an implementation detail; it determines whether the "Schema and 
migrations" slice is a 30-minute migration or a multi-hour production event.
    
    ---
  [CONCERN] Two existing coverage/monitoring modules are absent from Current State
    category: completeness
    
    `src/manta_trading/market/timescale_minute_coverage.py` contains `TimescaleMinuteDataCoverage`, which already performs coverage analysis against 
the minute hypertable using `analyze_symbol_coverage`, gap detection, and date-range queries. `src/manta_trading/market/timescale_monitoring.py` 
contains `TimescaleMonitor` with performance metrics and alerting.
    
    The "Current State" section lists `marketdb.py`, `timescale_minute_db.py`, and `data/base/` modules. Neither `timescale_minute_coverage.py` nor 
`timescale_monitoring.py` appear anywhere in the document. The "Coverage analysis" slice says "Build coverage/gap queries against existing minute and 
daily data." That work is largely already written. Building a second coverage implementation without reconciling with the existing one will produce 
duplicate, diverging code — exactly the kind of thing the architecture says it wants to avoid.
    
    ---
  [CONCERN] `IDataService.detect_gaps` uses `symbol: str`, contradicting the instrument_id migration
    category: consistency
    
    `service_interface.py` defines `IDataService.detect_gaps(self, symbol: str, start: datetime, end: datetime)` and `GapInfo` carries `symbol: str`. 
The architecture states that `instrument_id` should be the canonical identity for all storage and query paths, and the instrument registry integration 
slice backfills `instrument_id` into `minute_ohlcv` for exactly this reason.
    
    Yet the gap detection protocol — which will be the primary interface Initiative 140 consumes — is symbol-string-based. The document notes that 
`IDataService` will be "implemented in Initiative 140" and defers the question entirely. If Initiative 100 builds coverage analysis using the 
`IDataService` protocol as-is, and Initiative 140 implements it, the instrument_id migration becomes a breaking change to the protocol boundary rather 
than an internal storage detail. The architecture needs to decide whether `IDataService` should be updated to use `instrument_id` before 
implementations are written.
    
    ---
  [CONCERN] `symbol_list` and `instruments` are parallel symbol registries with no defined relationship
    category: abstraction
    
    `MarketDB`'s `dailyOHLCVAdjusted` table has a foreign key to `symbol_list.symbol`. The new `instruments` table introduces a separate canonical 
identity with its own `canonical_id` and `symbol` fields. The architecture says "All time-series tables reference `instrument_id`" but then says 
"MarketDB daily tables remain unchanged."
    
    The result is two independent registries. `symbol_list` is authoritative for daily OHLCV data. `instruments` is being positioned as authoritative 
for minute data and future tables. The document describes a backfill for `minute_ohlcv.instrument_id` but says nothing about daily data. There is no 
defined mapping between `symbol_list.symbol` and `instruments.canonical_id`. It is unspecified whether a symbol must exist in both tables, in either, 
or only one. Any query joining daily and minute data by instrument will have no basis for the join.
    
    ---
  [CONCERN] `@lru_cache` on instance methods prevents garbage collection and pollutes across instances
    category: technology
    
    `InstrumentRegistry` applies `@lru_cache(maxsize=1000)` to `get_instrument_by_canonical_id` and `get_instrument_by_provider_symbol`. 
`TradingCalendar` applies it to `is_trading_day` and `get_holidays`. In Python, `@lru_cache` applied at class definition creates a single cache per 
function, shared across all instances, with `self` as part of the key. The cache holds a strong reference to each `self` that has been cached, 
preventing garbage collection of any instance that has been called.
    
    `_clear_cache()` calling `self.method.cache_clear()` clears the entire class-level cache, not just the current instance's entries. In test fixtures
— where `conftest.py` fixtures create and tear down registry instances — this will produce cross-test cache poisoning. The document calls these modules
"functional" and intends to integrate them without modification. The `@lru_cache` pattern as used here will cause test isolation failures and memory 
leaks in long-running CLI sessions.
    
    ---
  [CONCERN] Tick hypertable schema is within scope but not defined
    category: completeness
    
    The "Scope" section explicitly includes tick hypertable schema design as a deliverable. The "Envisioned State" section describes `tick_events` with
`(instrument_id, timestamp, sequence, source)` as the natural key, space-partitioned by `instrument_id`, 1hr chunks. The "Technical Considerations" 
section says to focus on trade and quote events only.
    
    No column definitions, data types, TimescaleDB compression policy, indexing strategy, or retention policy appear anywhere in the document. The 
archived 050-arch is declared "superseded." Initiative 120 ("Data Acquisition") is listed as depending on the tick schema from this initiative. As 
written, the schema that Initiative 120 will write to is undefined — the architecture delivers a paragraph describing the key, not a schema.
    
    ---
  [CONCERN] `_create_market_db` silently exits on missing `db_url` — named as the pattern to extend
    category: antipattern
    
    `data.py` lines 52–53:
    ```python
    if not settings.db_url:
        raise typer.Exit(1)
    ```
    
    No error message is printed. The user gets a silent non-zero exit. The architecture document calls this helper "the pattern to extend" for the 
connection consolidation slice. The project guidelines explicitly state: "Never use silent fallback values. Fail explicitly with errors." Designating a
guidelines-violating pattern as the canonical template for new code will propagate the violation into every new storage module wired through the CLI.
manta@anemone ~/source/repos/manta/trading $ 
