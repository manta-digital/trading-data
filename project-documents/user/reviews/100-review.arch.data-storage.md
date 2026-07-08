---
docType: review
layer: project
reviewType: arch
slice: data-storage
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/architecture/100-arch.data-storage.md
aiModel: claude-haiku-4-5-20251001
status: complete
dateCreated: 20260401
dateUpdated: 20260401
---

# Review: arch — slice 100

**Verdict:** CONCERNS
**Model:** claude-haiku-4-5-20251001

## Findings

### [FAIL] Async/sync mismatch in coverage analysis will cause runtime failures

category: consistency

`TimescaleMinuteDataCoverage` (lines 34-35) is declared as async with async methods (`async def analyze_symbol_coverage`, `async def _detect_gaps`), but it calls sync methods from `TimescaleMinuteDataDB.get_coverage_analysis()` with await. `TimescaleMinuteDataDB.get_coverage_analysis()` is synchronous (returns `dict`, not coroutine), so line 46 (`coverage_data = await self.db.get_coverage_analysis(symbol)`) will fail: `TypeError: object dict can't be used in 'await' expression`. This is a critical architectural mismatch that prevents the coverage analysis from working.

### [FAIL] Settings class does not have the promised fields for dual-database configuration

category: consistency

The architecture (line 111) states: "Settings provides `market_db_url` and `timescale_db_url`". However, the actual `Settings` class in `src/manta_trading/config/__init__.py` only has a single field: `db_url: str | None = None`. This is a breaking gap between the design and current implementation. The architecture defers the Settings migration to anticipated slices but doesn't flag this as a blocking prerequisite.

### [FAIL] Symbol/instrument_id authority is contradictory, creating query ambiguity

category: consistency

The architecture states (line 101): "Symbol strings remain the primary query key until the instrument registry is populated and `instrument_id` is backfilled. Service interfaces continue to accept `symbol: str` — migrating these to `instrument_id` is a future step." But line 101 also claims "instruments table is a canonical registry... becomes authoritative for minute/tick data". If symbol is the "primary query key" during this initiative, it IS the authoritative key. Once `instrument_id` is backfilled, which becomes primary? The document doesn't specify the migration point or query interface behavior during the transition. This leaves implementers without a clear path for cross-system consistency.

### [CONCERN] Connection management pattern is scattered and incompatible across modules

category: abstraction

Four classes use incompatible connection management patterns:
- **MarketDB**: Instance `self.conn` with SimpleConnectionPool (psycopg2 pattern)
- **TimescaleMinuteDataDB**: SQLAlchemy engine with QueuePool
- **InstrumentRegistry**: Instance `self._conn`, creates connections ad-hoc in each method
- **TradingCalendar**: Instance `self._conn`, creates connections ad-hoc in each method

InstrumentRegistry and TradingCalendar will have concurrency issues if methods are called in parallel—they share a single `self._conn` across multiple async operations. When psycopg3 migration happens, all three classes will require independent, incompatible refactoring. This violates DRY and creates hidden coupling.

### [CONCERN] TradingCalendar hard-fails during construction, blocking initialization order

category: completeness

The architecture acknowledges (lines 117-118): "The existing `TradingCalendar` class hard-fails at construction (`ValueError`) if its `calendar_id` row is absent from the database". The code at line 93 calls `self._load_calendar_data()` unconditionally in `__init__`, and line 128 raises `ValueError` if the row is missing. This means:
1. Schema and seed data MUST exist before any `TradingCalendar` instantiation
2. Tests cannot create `TradingCalendar` without a database
3. No graceful degradation if calendar data is unavailable

The document recognizes this requires "a single atomic slice that completes before any code instantiates `TradingCalendar`" but provides no defensive initialization strategy, defensive option (lazy load), or test isolation solution.

### [CONCERN] Existing data gap characterization is blocking but coverage analysis is stub-implemented

category: completeness

The architecture states (line 134): "Before building new ingestion or quality infrastructure, we need to characterize what we have: which symbols, what date ranges, where the gaps are. This is a prerequisite for meaningful testing." But `TimescaleMinuteDataCoverage._detect_gaps()` (lines 97-135) only implements a trivial check: "is data older than 30 days?" (line 119). There is a TODO comment (line 128) acknowledging the gap detection is incomplete. This creates a Catch-22: the architecture depends on gap characterization, but the gap detection tool isn't built. The "get to testing quickly" slice (line 142) cannot deliver meaningful data assessment.

### [CONCERN] psycopg3 migration scope is underestimated, will require async refactoring

category: feasibility

The architecture claims (line 119) the psycopg3 migration is "mostly mechanical". However:
1. **InstrumentRegistry and TradingCalendar use `@lru_cache` on instance methods** (acknowledged in line 73), which doesn't work correctly; no fix is specified.
2. **InstrumentRegistry uses `RealDictCursor`** from psycopg2.extras—psycopg3 has no direct equivalent; requires different approach.
3. **TimescaleMinuteDataDB uses `copy_expert()` (line 138)**, but psycopg3's async `cursor.copy()` API has different semantics. Since the rest of the app is async, this forces async refactoring of the entire bulk write path.
4. **MarketDB uses `execute_values()` from psycopg2.extras** (line 525)—psycopg3 equivalent requires `cursor.executemany()` or `cursor.copy()`.
5. **No specification of transaction boundaries or autocommit behavior**, which differs between psycopg2 and psycopg3.

This is not mechanical migration; it requires testing of all data paths against a new driver with different semantics.

### [CONCERN] Duplicate responsibility for database schema ownership and initialization

category: abstraction

Three modules (`MarketDB`, `TimescaleMinuteDataDB`, `InstrumentRegistry`, `TradingCalendar`) all perform implicit schema initialization:
- `MarketDB.verifyDatabase()` (line 784) creates tables
- `TimescaleMinuteDataDB.__init__()` assumes the hypertable exists (no schema check)
- `InstrumentRegistry` assumes the `instruments` table exists
- `TradingCalendar._load_calendar_data()` assumes the `trading_calendars` and `trading_holidays` tables exist

The architecture says (line 41): "Schema-first — Define all TimescaleDB schemas before writing application code. Schema migrations are the deliverable; application integration follows." But there is no specification of:
- A single schema migration manager
- Order of schema creation
- What happens if application code runs before schema exists
- How to handle schema versioning and updates

Each module independently assumes schema existence. This creates hidden ordering dependencies and makes testing fragile.

### [CONCERN] Provider symbol mapping backfill is not addressed, blocking cross-provider resolution

category: completeness

The architecture (line 107) mentions "A provider symbol mapping table will eventually link AlphaVantage symbols to canonical instrument IDs" but defers this: "daily table migration is not in scope for this initiative." However, without provider symbol mapping, the existing `symbol_list` table (containing AlphaVantage symbols) cannot be linked to the new `instruments` table. This means:
1. Daily OHLCV data continues to use AlphaVantage symbols
2. Minute OHLCV data will use canonical instruments
3. No unified query interface works across both until mapping exists

The "unified data storage layer" (Section "Envisioned State") cannot exist without cross-provider symbol resolution. The architecture doesn't specify a plan, timeline, or even acknowledge this as a blocker.

### [CONCERN] IDataService protocol is defined but not integrated into the design

category: dependencies

`IDataService` (lines 78, 156) is defined in `service_interface.py` with three methods: `get_health_metrics()`, `detect_gaps()`, `get_quality_report()`. The document says it's "defined but not implemented by any service" and defers implementation to Initiative 140. However:
1. No service in THIS initiative is specified to implement it
2. Coverage analysis calls `db.get_coverage_analysis()` directly (line 46) instead of through IDataService
3. The "anticipated slices" section never mentions implementing or wiring IDataService
4. It's unclear which services should implement it (TimescaleMinuteDataDB? MarketDB? A wrapper?)

This creates a defined-but-unused abstraction, which violates the project's "Program to interfaces" guideline.

### [CONCERN] Continuous aggregate schema assumptions are not validated against actual continuous aggregates

category: completeness

The architecture (lines 67-68) assumes continuous aggregates exist with names like `minute_5min_ohlcv_v2`, `minute_15min_ohlcv_v2`, etc. The `TimescaleMinuteDataDB._get_aggregated_data()` method (lines 207-262) uses a hardcoded whitelist of view names. However:
1. No schema creation is specified for these aggregates in this initiative
2. The document doesn't specify WHO creates them or when
3. If aggregates don't exist, queries fail silently or return empty results
4. No validation that actual aggregates match the expected schema (column names, bucket sizes)

The "anticipated slices" section mentions creating aggregates exists but provides no detail on the schema, retention policy, or refresh strategy.

### [CONCERN] Tick hypertable schema is deferred without architectural validation

category: completeness

Lines 123-130 describe the tick schema intent but explicitly defer all specifics: "Detailed column types, indexes, and retention policies are slice-level decisions." However:
1. No sample schema is provided for validation
2. Single-table design with `event_type` discriminator may not scale if future event types (BBO, depth, NBBO) are added
3. Space partitioning by `instrument_id` may cause hot partitions if certain instruments have very high volume
4. No specification of retention policy (how long to keep tick data?)
5. No specification of compression strategy for tick data (1hr chunks mentioned but not validated for tick scale)

This leaves a critical component completely under-specified. If the actual schema proves infeasible later, the entire Initiative 120 (Data Acquisition) will be blocked.

### [CONCERN] MarketDB and related classes silently fail on connection errors, violating project guidelines

category: antipattern

The project guidelines state (from CLAUDE.md): "Never use silent fallback values. Fail explicitly with errors or obviously-placeholder values." However:
- **MarketDB.createConnectionPool()** (line 60) returns `None` instead of raising on error
- **MarketDB.__enter__()** (lines 85-93) doesn't raise; just logs and continues, leaving `self.conn = None`
- **TimescaleMinuteDataCoverage._detect_gaps()** (line 128) is a stub with only TODO comment
- **TimescaleMinuteDataDB._init_engine()** (line 72) catches all exceptions but still initializes `self.engine = None`

These silent failures defer errors to runtime when methods are called, producing cryptic "NoneType has no attribute" errors instead of explicit connection failures. The architecture doesn't address the error transparency requirement.

### [CONCERN] No transaction management strategy specified for psycopg3 migration

category: completeness

The architecture calls for migrating to psycopg3 but doesn't specify transaction handling:
- **psycopg2 default**: `autocommit=False`, explicit `conn.commit()` required
- **psycopg3 default**: `autocommit=False`, explicit `connection.begin()` required for transactions

Current code uses implicit transactions (psycopg2 pattern). After migration:
1. All three database classes need explicit transaction boundary specification
2. Bulk writes (execute_values, COPY) need transaction context
3. Connection pool behavior changes transaction semantics
4. Error recovery strategy (rollback on exception) must be explicit

No transaction specification exists. This will cause data corruption or inconsistency if transactions are not correctly demarcated.

### [CONCERN] No specification of dual-database failover or recovery procedures

category: completeness

The architecture assumes two separate PostgreSQL hosts (line 43) but provides no:
- Failover strategy if one host becomes unavailable
- Replication or backup strategy
- Recovery procedure
- How to detect which host is down
- Fallback behavior when a database is unreachable

This creates a single point of failure for each database. Initiative depends on both being available, but provides no resilience.

### [CONCERN] Global monitor singleton pattern is acknowledged but not banned or specified

category: antipattern

`TimescaleMonitor` (line 86) uses a global singleton (`_global_monitor` at line 275). The architecture acknowledges this is an antipattern but doesn't ban it or specify when it's acceptable. The global singleton:
1. Causes test isolation problems (tests pollute global state)
2. Causes issues with multiple service instances
3. Prevents dependency injection
4. Makes monitoring configuration non-obvious

Architecture should either remove the singleton or explicitly allow it with constraints.

### [CONCERN] Food comparison values scatter across trading calendar code, violating DRY principle

category: antipattern

The project guidelines state: "Never scatter comparison values across code. If a value is used in conditionals, define it once." However:
- **TradingCalendar** uses magic strings `'closed'`, `'early_close'`, `'late_open'` in lines 170, 212, 213 (conditionals checking market_status)
- **trading_calendar.py** doesn't define these as constants; they're scattered across multiple methods
- If the database schema changes or new statuses are added, multiple locations must be updated

These should be defined as an enum or constants module.

### [CONCERN] DST handling in bar count calculation is incomplete

category: feasibility

`TradingCalendar.get_expected_bar_count()` (line 298-302) has a `pass` statement where DST is "handled":
```python
if self._is_dst_transition(current_date):
    # DST spring forward: lose 1 hour (60 minutes)
    # DST fall back: gain 1 hour (60 minutes)
    # We'll handle this by checking the actual duration
    pass  # Duration already accounts for DST
```

This is incorrect. The duration calculation (line 295) uses `(hours.session_end - hours.session_start) / 60`, but on DST transition days, this calculation is wrong:
- Spring forward: actual trading minutes are 1 hour less
- Fall back: actual trading minutes are 1 hour more

The comment assumes the duration is correct, but it isn't. This will produce wrong bar counts on DST transition days. The architecture doesn't specify correct DST handling.
