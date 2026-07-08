# Data Storage and Acquisition Architecture

## Overview

This document defines the complete system architecture for the trading application, encompassing three tiers: Data Infrastructure, Analysis & Strategy, and Application & UX. The primary focus is establishing robust data storage and acquisition patterns that scale from minute-resolution historical data through realtime tick data processing.

The architecture addresses fundamental design issues discovered during development, establishes clear service boundaries, and provides a scaling path from initial implementation (4-32 symbols) to future expansion (100+ symbols, multiple asset classes).

## System Architecture - Three Tiers

The system is organized into three distinct tiers with clear boundaries and responsibilities:

**Tier 1: Data Infrastructure** - Storage, acquisition, and data quality (current focus)
**Tier 2: Analysis & Strategy** - Technical analysis, signal generation, backtesting
**Tier 3: Application & UX** - Desktop application, visualization, user interaction

### Architectural Principles

1. **No Tier Skipping**: Higher tiers consume lower tier interfaces, never direct data access
2. **Module Interfaces Initially**: Python module interfaces (not HTTP APIs) until distributed deployment needed
3. **Service Isolation**: Each service has clear responsibilities and can be independently tested/deployed
4. **Provider Abstraction**: Data providers are replaceable without architectural chaos
5. **Future-Proof Boundaries**: Clean interfaces allow evolution from monorepo modules to microservices

## Design Parameters and Background
This section is provided by the Project Manager.

### Key Design Facts and General Guidelines
* For stock market data we are mostly concerned with historical.  
* Stock market has 1000s of symbols but we rarely if ever care about < 1 minute
* Futures market we need ticks
* We do want aggregation, and we want to keep historical ticks (we buy this data)
* We should consider that we may need different microservices to provide and process data at some point -- gathering realtime data, aggregation and processing, different data sources for minute and ticks, stocks and crypto, etc.
* We do NOT need full design of all phases now.  We DO need to design an architecture that can support the phases and services needed in the future.

#### General Data Guidelines
* We need to be able to replace data providers, but this is not expected to be trivial or occur often.  We just wouldn't want to fold if our data provider became inaccessible (business conditions, costs, availability, etc).
* We will use different providers for stock market and futures market data (cost effect, availability, etc)

##### Data Granularity
* For stock market data, 1 minute granularity (+ aggregation) is sufficient (1000s of symbols)
* For futures data, tick granularity (less than 1 sec but more than 50 msec) is needed (~4-32 symbols)
* We will add crypto. It is 24/7, less than tick speed but potentially sub-minute (16-256 symbols)
* It is reasonable to assume these could be partially or completely handled by different services.

#### Tick Data Guidelines
Also there are some additional parameters that might help:
* Tick data will be concerned with a relatively small set of symbols (~4 to start, expanding later).  
* Tick data aggregation is extremely useful, but is less realtime-critical.  In general we will process realtime data in the future, and will use aggregate data in historical contexts.  This could be 15 minutes old or even 5, but it doesn't need to be processed in milliseconds or even seconds.
* Tick data flow will target < 32 symbols initially and may expand later.  Early version will be <= 4 symbols.  
* Unsure of acq speed.  Figure a few ticks/sec/symbol during busy market times, less during non-peak hours during trading day, way slower in off hours.  Not 500 symbols @ 1000/symbol/sec
* We will buy historical tick data.  It will likely come from DataBento and have a different format and access method.  We don't need the specifics of that yet, just know that it will be.  It won't come from AlphaVantage, so it will not use AlphaVantageAPI or its rate limits.  It will have a new provider, service, etc.

#### Data Storage and Acquisition Evaluation
* We should continue phased approach.
* We must ensure each phase is solid before continuing.  Primary concern is to verify data accessibility, integrity, and completeness.  Ex: We have 1B points of minute data.  Is it "right"?  Complete?  Accurate?  No junk, noise, duplication, missing pieces.  How do we know?  We must be able to answer that question.




## Tier 1: Data Infrastructure (Current Focus)

### Service Overview

Tier 1 provides all data storage, acquisition, and quality monitoring capabilities. It consists of multiple independent services, each handling a specific data type and timeframe combination.

**Core Services:**
1. **Historical Minute Data Service** - Historical stock market minute bars (AlphaVantage)
2. **Realtime Minute Data Service** - Realtime stock market minute bars (AlphaVantage WebSocket)
3. **Historical Tick Data Service** - Historical futures tick data (DataBento)
4. **Realtime Tick Data Service** - Realtime futures tick data (DataBento streams)
5. **Daily OHLC Service** - Existing daily data (AlphaVantage, PostgreSQL) - separate, remains as-is

**Future Services:**
- Crypto Data Service (historical + realtime, 24/7 operation)

### Service Characteristics

Each service is characterized by:
- **Data Provider**: AlphaVantage, DataBento, etc. (replaceable via provider abstraction)
- **Data Granularity**: Daily, minute, sub-minute, tick
- **Operational Mode**: Historical (batch) vs Realtime (streaming)
- **Symbol Volume**: Thousands (stocks) vs dozens (futures)
- **Rate Characteristics**: API limits vs streaming throughput

### Standardized Service Interface

All data services implement a common base interface providing standardized health monitoring and quality reporting:

**IDataService Base Interface** - All services must implement:
- `get_health_metrics()` - Service health status, error counts, quality scores
- `detect_gaps(symbol, start, end)` - Data gap detection
- `get_quality_report(symbol)` - Completeness, accuracy, timeliness, consistency metrics

**Standard Quality Metrics** (all services report):
- **Completeness**: Percentage of expected data present
- **Accuracy**: Percentage of data passing validation
- **Timeliness**: Lag between data time and ingestion time
- **Consistency**: Internal consistency check pass rate

Each service extends the base with service-specific capabilities and quality metrics relevant to its data type.

### Instrument & Symbol Management

**Canonical Instrument Registry:**
- Maintain first-class instrument metadata table
- Fields: canonical_id, asset_class (stock/future/crypto), venue, tick_size, lot_size, currency, trading_calendar_id
- Tracks corporate action policies, futures roll conventions
- Single source of truth for all instrument properties

**Provider Symbol Mapping:**
- Map provider-specific symbols to canonical IDs with validity ranges
- Prevents silent symbol renames and contract code drift
- Example: AlphaVantage "AAPL" → canonical_id "AAPL.NASDAQ" valid from 1980-12-12
- Example: DataBento "ESH5" → canonical_id "ES.CME.202503" valid 2024-12-15 to 2025-03-15

**Futures Roll Strategy:**
- Document futures continuous contract methodology (back-adjusted, ratio-adjusted, panama, per-contract)
- Store roll rules alongside dataset for deterministic replays
- Enable consistent historical analysis across contract boundaries

**Trading Calendars:**
- Exchange-accurate trading schedules (market hours, holidays, early closes, DST transitions)
- Used for gap detection: compute "expected bars" from calendars, not "every minute"
- Prevents false gap alerts on holidays, weekends, and exchange closures
- Critical for accurate data quality metrics

### Data Correctness & Adjustment Policies

**Adjustment Policy for Minute Data:**
- **Primary dataset: Split-adjusted prices only**
- Rationale: Matches analysis use cases (backtesting, strategy development)
- Matches existing daily OHLC data (also split-adjusted)
- Metadata tracks adjustment_policy="split_adjusted" in all bar records
- If raw prices needed in future: Acquire on-demand, store in separate table

**Session Partitioning (Equities):**
- Partition bars by trading session: RTH (regular trading hours) vs ETH (extended trading hours)
- Prevents mixing different market regimes in analysis
- Enables session-specific validation and quality checks
- Session type stored in metadata for each bar

**Validation Framework (Advisory):**
- Compare aggregated minute data with daily OHLC data (same adjustment policy)
- Compute: `aggregate(minute_bars, day) == daily_bar`
- Report discrepancies as advisory warnings (do not block ingestion)
- Investigate mismatches: Provider errors, timing differences, session mismatches
- Use calendars to validate expected bar counts per day/symbol

**Data Quality Checks:**
- OHLCV consistency: High >= Low, High >= Open, High >= Close, Low <= Open, Low <= Close
- Volume non-negative
- Timestamps align with trading calendar
- No duplicate bars (same symbol, timestamp, session)
- Gap detection uses calendar-aware expected counts

### Storage Architecture

**TimescaleDB** - Primary time-series storage for all minute and tick data:
- Hypertable structure with automatic chunking
- Continuous aggregations for multi-timeframe views
- Compression policies (95% space reduction after 2 hours)
- Retention policies (2-year default)
- Proven performance: 13k+ rows/sec writes, <50ms query latency

**Partitioning Strategy (by data type):**
- **Minute data**: Time-based partitioning only (4hr chunks)
  - Current implementation proven adequate for 1000s of symbols
  - Excellent performance validated (13k+ rows/sec, <50ms queries)
  - No restructuring required for existing minute_ohlcv hypertable
  - Evaluate two-dimensional partitioning only if performance degrades at very high symbol counts (>5000)

- **Tick data** (future): Two-dimensional partitioning (time + instrument_id)
  - 1hr time chunks + space partitioning by instrument_id
  - Optimized for symbol-specific access patterns
  - Handles high row count per symbol efficiently
  - Composite primary key: (time, instrument_id, sequence, source)

**Storage Organization:**
- `minute_ohlcv` hypertable - Minute-resolution bars (4hr time chunks)
- `tick_data` hypertable (future) - Raw tick data (1hr time chunks, space-partitioned by instrument_id)
- Continuous aggregations: 5min, 15min, 1hr, 4hr, 1day, 1week, 1month
- Automatic aggregation refresh policies with lookback window (handle late-arriving data)

**Continuous Aggregation Refresh Strategy:**
- Configure rolling refresh windows (e.g., re-materialize last 2 days)
- Handles late-arriving data without stale aggregations
- Balance between freshness and compute cost
- Critical for 5min/15min bars when minute data arrives late

**PostgreSQL** - Daily OHLC data (existing, separate):
- Simple schema for daily bars
- Remains independent from TimescaleDB
- Used for verification/validation against aggregated minute data
- Both datasets are split-adjusted for direct comparison

**Redis** - Realtime tick cache (< 24 hours):
- **Architecture: Redis Streams** (not simple key-value cache)
- Ordered append-only log per instrument
- Supports replay, consumer groups, natural backpressure
- Enables multi-consumer analysis without additional message bus
- Fast access to recent tick data for realtime analysis and signal generation
- Automatic expiration, eventual archival to TimescaleDB
- Durability: AOF (append-only file) persistence every second, document RPO (recovery point objective)

**Data Flow:**
```
Realtime Ticks → Redis Streams (< 24hr, ordered, replayable) → Analysis/Signals
                           ↓ (periodic archival, idempotent)
                     TimescaleDB (permanent storage)
                           ↓ (continuous aggregation, 5-15 min lag acceptable)
                     Multi-timeframe views (1min, 5min, 15min, etc.)
```

### Service Internal Architecture

Each data service follows a consistent internal structure with clear async/sync boundaries:

**Orchestrator (Async)** - Coordinates I/O operations:
- Job scheduling and progress tracking
- Rate limiting coordination
- Multi-symbol batch management
- Error recovery and retry logic

**Provider Abstraction (Async I/O)** - Isolates data source specifics:
- API client or WebSocket connection
- Authentication and connection management
- Raw data fetching
- Provider-specific rate limiting
- Format conversion to standard internal format

**Data Processor (Sync)** - Transforms and validates:
- Data validation and quality checks
- Pandas DataFrame operations
- OHLCV consistency validation
- Gap detection
- Format normalization

**Storage Manager (Sync)** - Persists data:
- TimescaleDB bulk write operations
- Transaction management
- Query operations
- Aggregation management

**Data Flow Pattern:**
```
[Orchestrator] → [Provider] → [Processor] → [Storage]
   (async)        (async I/O)    (sync)       (sync)
      ↓              ↓             ↓            ↓
  Schedules      Fetches       Validates    Persists
  Coordinates    Converts      Cleans       Queries
  Tracks         Limits        Checks       Manages
```

**Async/Sync Boundary Rules:**
- **Async**: I/O operations (network, WebSocket), orchestration, scheduling
- **Sync**: Data processing (Pandas), validation, database operations (SQLAlchemy synchronous)
- **Never Mix**: No await calls on sync methods, no sync blocking in async methods

### Provider Abstraction Layer

Each service implements its own provider abstraction tailored to its data type and operational characteristics:

**Historical Minute Provider Interface:**
- `fetch_minute_data(symbol, start_date, end_date)` → Returns DataFrame
- `get_rate_limits()` → Returns rate limit information
- `validate_response(raw_data)` → Checks data integrity
- `convert_to_standard_format(raw_data)` → Normalizes to internal schema

**Historical Tick Provider Interface:**
- `fetch_tick_data(symbol, start_datetime, end_datetime)` → Returns tick stream
- `get_batch_download_url(symbol, date)` → For bulk historical downloads
- `parse_tick_format(raw_ticks)` → Converts provider format to internal schema
- `validate_tick_integrity(ticks)` → Sequence and timing validation

**Realtime Stream Provider Interface:**
- `connect()` → Establishes stream connection
- `subscribe(symbols)` → Subscribes to symbol updates
- `on_tick(callback)` → Event-driven tick delivery
- `get_connection_health()` → Stream health status
- `reconnect()` → Handles disconnection recovery

**Concrete Implementations:**
- AlphaVantageMinuteProvider (historical + realtime minutes)
- DataBentoTickProvider (historical + realtime ticks)
- Future providers can be added without service changes

**Provider Configuration:**
- Per-deployment configuration (set once)
- Not runtime switchable (requires service restart)
- Initial deployment: AlphaVantage + DataBento
- Adding/changing providers is infrequent but must be clean

### Historical Minute Data Service

**Purpose**: Acquire and store historical minute-resolution OHLCV data for stocks

**Characteristics:**
- Provider: AlphaVantage REST API
- Volume: Potentially 1000s of symbols
- Realtime subset: < 256 symbols (typically < 64)
- Rate limiting: AlphaVantage API limits
- Storage: TimescaleDB minute_ohlcv hypertable

**Operations:**
- Bulk historical acquisition (scheduled or on-demand)
- End-of-day updates
- Gap detection and backfilling
- Data quality monitoring per symbol

### Historical Tick Data Service

**Purpose**: Acquire and store historical tick-level data for futures

**Characteristics:**
- Provider: DataBento bulk historical data
- Volume: 4-32 symbols initially
- Granularity: Tick-level (< 1 sec, > 50 msec)
- Storage: TimescaleDB tick_data hypertable (future)
- Cost: Premium data, purchased and retained permanently

**Tick Event Model:**
- Schema supports multiple event types: trade, quote, BBO, NBBO, depth, status
- Start simple (trades only), expand schema to handle other event types later
- Fields: instrument_id, timestamp, sequence, source, event_type, price, size, trade_condition, bid, ask, etc.
- Natural key for idempotency: (instrument_id, timestamp, sequence, source)
- Use ON CONFLICT DO UPDATE for safe re-ingestion (late data, patches, corrections)

**Operations:**
- Bulk historical data import (idempotent, can re-run safely)
- Tick sequence validation (detect missing sequences, out-of-order data)
- Data integrity verification
- Cross-timeframe consistency checks (tick aggregations vs minute data)
- Late/out-of-order data handling: Order-tolerant ingestion, sequence tracking

**Aggregation Rules:**
- Explicit documentation: OHLCV built from trades only? Include quotes? Zero-volume bars?
- Store aggregation methodology in metadata for deterministic replay
- Same aggregation code used for both historical backfill and realtime processing

### Realtime Minute Data Service

**Purpose**: Stream realtime minute bars for selected stocks

**Characteristics:**
- Provider: AlphaVantage WebSocket
- Volume: < 256 symbols (typically < 64)
- Latency: Within seconds (not milliseconds)
- Storage: TimescaleDB (same schema as historical)
- Rate limiting: Connection-based

**Operations:**
- Market hours monitoring
- Symbol subscription management
- Stream health monitoring
- Seamless integration with historical data

### Realtime Tick Data Service

**Purpose**: Stream realtime tick data for futures trading

**Characteristics:**
- Provider: DataBento realtime streams
- Volume: 4-32 symbols initially
- Tick rate: Few ticks/sec/symbol (peak: ~10-50 ticks/sec total system)
- Latency: Sub-second ingestion required
- Cache: Redis (< 24 hours)
- Storage: TimescaleDB (periodic archival)

**Operations:**
- Real-time tick ingestion to Redis cache
- Stream health monitoring and reconnection
- Gap detection and recovery
- Periodic archival to TimescaleDB
- Ring buffer management (Python collections.deque with maxlen)

**Realtime Processing:**
- Tick ingestion: Sub-second to Redis
- Analysis/signal generation: Direct from Redis cache
- Aggregation to higher timeframes: 5-15 min lag acceptable
- Not optimized for sub-millisecond requirements

### Data Lineage & Versioning

**Dataset Versioning:**
- Track provider version, transform version, adjustment policy, calendar version
- Each ingestion includes dataset signature/version metadata
- Enables tracing any strategy result to exact input data configuration
- Critical for reproducibility and debugging

**Ingestion Tracking:**
- Maintain `ingest_runs` audit table: timestamp, user, symbol range, row counts before/after, data hash
- Tracks what data was ingested when and by whom
- Enables investigation of "why does this day look weird?"
- Supports compliance and audit requirements

**Metadata Fields (All Bar Records):**
- adjustment_policy (e.g., "split_adjusted")
- session_type (RTH/ETH for equities)
- provider_version
- transform_version
- ingestion_timestamp
- data_version

**Idempotent Ingestion Pattern:**
- All batch jobs designed for safe re-runs
- Write to staging table, validate counts and quality, then upsert to production in transaction
- Natural keys prevent duplicate data
- Re-running same job produces same result (exactly-once effect)

**Backfill Policy:**
- Formalized gap backfill process: scan frequency, max lookback, retry limits
- Annotate "known provider holes" (e.g., exchange outage) to prevent endless alerts
- Document gap resolution process and escalation

### Observability & Service Level Objectives

**Per-Service Metrics (Prometheus/Grafana):**
- Ingest lag: Time between data timestamp and ingestion completion
- Completeness: % of expected data present (per day, per instrument)
- Duplicate rate: % of duplicate events detected and handled
- Late arrival rate: % of data arriving after initial ingestion window
- Aggregation refresh delay: Lag in continuous aggregation materialization
- Redis queue depth: Number of pending ticks per instrument
- Error rate: Failed ingestion attempts per time window

**Service Level Objectives (SLOs):**
- Minute data: 99% completeness within 24 hours of market close
- Tick data: 99.9% completeness within 1 hour of ingestion
- Query latency: p95 < 100ms for 1-day minute data queries
- Aggregation lag: < 15 minutes for 5min/15min bars
- Redis tick cache: < 5 second ingestion latency

**Alerting Strategy:**
- Critical: Service down, data ingestion stopped, Redis memory > 90%
- Warning: Completeness < 95%, ingestion lag > 1 hour, duplicate rate > 1%
- Info: Gap detected (check against known outages), late data arrival

**Health Dashboards:**
- Per-service health overview (leveraging IDataService.get_health_metrics())
- Data quality metrics by instrument and timeframe
- Ingestion pipeline status and throughput
- Storage utilization and query performance

### Operational Concerns

**Provider Licensing & Capabilities:**
- Verify AlphaVantage: Realtime minute via WebSocket capability and licensing for local storage/backtesting
- Verify DataBento: Historical and realtime tick data licensing, redistribution restrictions
- Document true delivery semantics (push vs poll-and-debounce)
- Track licensing terms: Can data be stored? Used for backtesting? Redistributed between internal processes?

**Rate Limiting Strategy:**
- Centralize rate-limit state per provider (token bucket, burst/window tracking)
- Prevents multi-process jobs from exceeding provider limits
- Coordinate rate limits across concurrent acquisition jobs
- Graceful degradation when limits approached

**Secrets Management:**
- API keys and credentials via Vault or 1Password Connect
- Never store secrets in environment files or code repositories
- Implement key rotation procedures
- Tag data with sensitivity classifications

**Backup & Disaster Recovery:**
- Point-in-time recovery (PITR) for PostgreSQL and TimescaleDB
- Tested restore procedures (regular DR drills)
- Object storage lifecycle rules for archived tick data
- Document RPO (Recovery Point Objective) and RTO (Recovery Time Objective) per tier
- Cold storage: Consider Parquet files in object storage (S3/MinIO) as immutable ledger for tick data

## Tier 2: Analysis & Strategy (Future)

### Overview

Tier 2 provides all technical analysis, strategy evaluation, and signal generation capabilities. It consumes data from Tier 1 services and provides analysis results to Tier 3 application.

**Core Responsibilities:**
- Technical indicator calculation (SMA, EMA, RSI, MACD, Bollinger Bands, etc.)
- Strategy framework and backtesting
- Signal generation (buy/sell/hold)
- Regime detection and market state classification
- Performance analytics and strategy evaluation

**Data Access Pattern:**
- Consumes Tier 1 service interfaces (never direct database access)
- Requests data via service calls: `HistoricalMinuteService.get_minute_data(...)`
- Processes DataFrames for analysis
- Returns analysis results and signals

**Example Usage:**
```
In Analysis Tier - building a strategy:
  vix = HistoricalOHLCService.get_minute_data("VIX", start, end, timeframe="1min")
  es = HistoricalTickService.get_tick_data("ES", start, end)

  Calculate technicals:
    es_sma = calculate_sma(es, period=20)
    vix_regime = detect_regime(vix)

  Generate signal:
    signal = my_strategy.evaluate(es, es_sma, vix_regime)
```

### Key Components

**Technical Analysis Engine:**
- Indicator library (overlays, oscillators, volume)
- Custom indicator framework
- Multi-timeframe analysis
- Cross-asset analysis (e.g., VIX regime with ES signals)

**Strategy Framework:**
- Strategy definition and composition
- Backtesting engine
- Walk-forward testing
- Strategy parameter optimization
- Signal generation pipeline

**Performance Analytics:**
- Return calculations
- Risk metrics (Sharpe, Sortino, max drawdown)
- Trade statistics
- Equity curves
- Strategy comparison

## Tier 3: Application & UX (Future)

### Overview

Tier 3 is the user-facing desktop application providing visualization, interactive analysis, and eventually trade execution capabilities.

**Application Platform:**
- Electron or Tauri (final decision pending)
- Native desktop application
- Cross-platform (Windows, macOS, Linux)

**Core Technologies:**
- SciChart for professional-grade charting
- Manta-templates for UI framework
- TradingView-level presentation quality

**Core Capabilities:**
- Data visualization (historical and realtime)
- Interactive charting and drawing tools
- Strategy testing interface
- Backtesting visualization
- Signal monitoring
- Eventually: Trade execution via API

**Data Flow:**
```
User Interaction → Tier 3 App
                      ↓
                 Tier 2 Analysis APIs
                      ↓
                 Tier 1 Data Services
                      ↓
                 Storage (TimescaleDB, Redis)
```

**Key Principle:** Application never directly queries databases, never calls data providers, never implements analysis logic. It consumes services from lower tiers.

## Cross-Tier Communication

### Module Interfaces (Initial Implementation)

All tier communication uses Python module interfaces:

**Benefits:**
- Zero network overhead (direct function calls)
- No serialization/deserialization
- No authentication/authorization complexity
- Type safety with Python type hints
- Simple testing and debugging

**Implementation Pattern:**
- Services are Python modules in monorepo
- Clean interface definitions (abstract base classes)
- Dependency injection for testing
- Same interface whether local or remote

**Example:**
```
from manta_trading.data.historical_minute import HistoricalMinuteService
from manta_trading.data.historical_tick import HistoricalTickService

# Direct Python calls - no HTTP, no network overhead:
minute_data = HistoricalMinuteService.get_minute_data("AAPL", start, end)
tick_data = HistoricalTickService.get_tick_data("ES", start, end)
```

### Future Migration to Service APIs

When services need distributed deployment:
- Same interfaces, different implementation
- HTTP/gRPC client replaces direct module call
- Transparent to consumer code
- No tier-skipping remains enforced

**Not needed initially:**
- Network protocols
- Authentication/authorization
- Serialization
- Service discovery
- API versioning

## Deployment & Scaling Strategy

### Initial Deployment Model

**Monorepo Structure:**
- Single codebase, multiple Python modules
- Clear module boundaries enforce service separation
- Services communicate via Python module interfaces
- Shared utilities (validation, configuration, logging)

**Module Organization:**
```
manta_trading/
├── data/                      # Tier 1: Data Infrastructure
│   ├── base/                  # IDataService base interface
│   ├── historical_minute/     # Historical minute service
│   ├── historical_tick/       # Historical tick service
│   ├── realtime_minute/       # Realtime minute service
│   └── realtime_tick/         # Realtime tick service
├── analysis/                  # Tier 2: Analysis & Strategy (future)
│   ├── indicators/
│   ├── strategies/
│   └── backtesting/
├── app/                       # Tier 3: Application (future)
│   └── desktop/
├── shared/                    # Shared utilities
│   ├── validation/
│   ├── storage/              # TimescaleDB patterns
│   └── monitoring/
└── cli/                       # Command-line interface
```

**Benefits:**
- Simple deployment (single application initially)
- Easy refactoring and code sharing
- Fast development iteration
- Clear boundaries allow future service extraction

### Future Scaling Path

**When to split into separate services:**
- Symbol volume exceeds ~100-200 total
- Tick rate exceeds ~500-1000 ticks/sec total system
- Different services need independent scaling
- Team organization benefits from service ownership

**Migration Strategy:**
- Keep same interfaces
- Implement HTTP/gRPC clients
- Deploy services independently
- No consumer code changes

## Scaling Thresholds & Performance Targets

### Current Design (Phase 1)

**Minute Data:**
- Symbols: 1000+ historical, < 256 realtime
- Write performance: 13k+ rows/sec (validated)
- Query performance: < 50ms for 1-day ranges (validated)
- Storage: TimescaleDB with 95% compression

**Tick Data:**
- Symbols: 4-32 initially
- Tick rate: Few ticks/sec/symbol (peak: ~10-50 ticks/sec total)
- Ingestion: Sub-second to Redis cache
- Aggregation lag: 5-15 min acceptable
- Implementation: Python with collections.deque ring buffers

### Refactor Triggers

**When performance exceeds design:**
- Tick volume > 500 ticks/sec total → Revisit buffering and batching strategy
- Symbol count > 100 → Consider per-symbol service allocation
- TimescaleDB write lag > 10 sec → Revisit batch sizing and write optimization
- Redis memory > 80% capacity → Adjust retention or add sharding

**Architecture stays valid until:**
- Need distributed deployment (multiple machines)
- Need language-specific optimization (C++ for performance-critical paths)
- Need real sub-millisecond latency (not currently required)

## Implementation Priorities

### Priority 1: Historical Minute Data Service (Immediate)
**Goal:** Establish clean service architecture with historical minute data

**Foundation Tasks (Do First):**
- Implement instrument metadata registry and provider symbol mapping
- Implement trading calendar system (exchange schedules, holidays)
- Define split-adjusted data policy and metadata schema
- Set up session partitioning (RTH/ETH) for equities

**Service Implementation Tasks:**
- Implement IDataService base interface
- Build HistoricalMinuteService with provider abstraction
- AlphaVantageMinuteProvider implementation
- Clean async/sync boundaries
- Reuse TimescaleMinuteDataDB storage with two-dimensional partitioning
- Implement idempotent ingestion pattern (staging → validate → upsert)
- Gap detection and quality monitoring (calendar-aware)
- Advisory validation: Compare minute→daily aggregation
- Data lineage tracking (ingest_runs audit table)
- CLI integration

**Success Criteria:**
- Instrument registry and calendars operational
- Historical minute acquisition works reliably for 500+ symbols
- Clean async (orchestration/I/O) and sync (processing/storage) separation
- Provider is replaceable without service changes
- Standard health and quality metrics available
- Idempotent ingestion (can re-run safely)
- Advisory validation reports minute vs daily discrepancies

### Priority 2: Historical Tick Data Service (Next)
**Goal:** Open up tick-level analysis capabilities

**Tasks:**
- Implement IHistoricalTickDataService extending IDataService
- DataBentoTickProvider implementation
- TimescaleDB tick_data schema with event model (trade, quote, BBO, etc.)
- Natural key for idempotency: (instrument_id, timestamp, sequence, source)
- Tick sequence validation (detect missing sequences, out-of-order)
- Late/out-of-order data handling
- Document aggregation rules (trades-only OHLCV? zero-volume bars?)
- Cross-timeframe consistency checks (tick aggs vs minute data)
- Consider Parquet cold storage for immutable tick ledger

**Success Criteria:**
- Bulk historical tick import working (idempotent, can re-run)
- Tick data queryable alongside minute data
- Sequence validation detects gaps and ordering issues
- Same aggregation code works for historical and realtime
- Can aggregate ticks to minutes and compare with minute data for verification

### Priority 3: Realtime Services (Future)
**Goal:** Real-time data feeds for live analysis

**Tasks:**
- Realtime minute service (WebSocket integration, verify AlphaVantage capabilities)
- Realtime tick service (stream ingestion to Redis Streams, not simple cache)
- Redis Streams architecture (ordered log per instrument, consumer groups)
- Stream health monitoring (connection status, ingest lag, drop/duplicate counters)
- Gap detection and recovery (reconnection logic, sequence tracking)
- Integration with historical data (seamless API boundary)
- Periodic archival from Redis to TimescaleDB (idempotent)
- Per-symbol SLO metrics (ingest lag, completeness, duplicate rate)

**Success Criteria:**
- Realtime ticks flowing to Redis Streams
- Sub-second ingestion latency
- Seamless historical + realtime queries through same service interface
- Robust reconnection and error recovery
- Monitoring dashboards show per-symbol health metrics
- Archival to TimescaleDB working without data loss

### Priority 4: Analysis Tier (Future)
### Priority 5: Application Tier (Future)

## What We Keep, What We Replace

### Keep (Proven Effective)
- ✅ TimescaleMinuteDataDB class and storage patterns
- ✅ TimescaleDB schema, continuous aggregations, compression policies
- ✅ Existing minute_ohlcv hypertable partitioning (time-based, 4hr chunks - no restructuring needed)
- ✅ Storage performance characteristics (13k+ rows/sec, <50ms queries)
- ✅ Daily OHLC PostgreSQL data (separate, for validation)
- ✅ CLI command structure (refactor to use new services)

### Replace (Architectural Issues)
- ❌ TimescaleMinuteService (async/sync confusion)
- ❌ Current acquisition orchestration
- ❌ Mixed responsibility boundaries
- ❌ Direct database access from upper tiers

### Build New (Missing Capabilities)
- ⭐ Service interface standardization (IDataService)
- ⭐ Provider abstraction layer (per-service)
- ⭐ Tick data services (historical + realtime)
- ⭐ Health and quality monitoring framework
- ⭐ Analysis tier (technical indicators, strategies)
- ⭐ Application tier (visualization, user interface)

## Summary

This architecture provides:

### Core Architecture
1. **Clear three-tier separation** with no tier-skipping
2. **Service boundaries** that scale from modules to microservices
3. **Provider abstraction** enabling data source changes without chaos
4. **Standardized monitoring** via IDataService base interface
5. **Realistic scaling targets** (4-32 symbols, few ticks/sec initially)
6. **Future-proof design** supporting analysis and application tiers
7. **Proven storage patterns** (TimescaleDB) extended to new data types

### Data Correctness & Quality
8. **Instrument metadata registry** preventing symbol drift and corporate action confusion
9. **Trading calendars** for accurate gap detection and validation
10. **Split-adjusted data policy** documented and enforced (matches backtesting use case)
11. **Session partitioning** (RTH/ETH) preventing regime mixing
12. **Advisory validation framework** comparing minute→daily aggregations
13. **Idempotent ingestion** enabling safe re-runs and backfills

### Operational Excellence
14. **Data lineage tracking** (provider version, transform version, adjustment policy)
15. **Observability & SLOs** (per-service metrics, dashboards, alerting)
16. **Redis Streams architecture** (ordered logs, replay, consumer groups)
17. **Tick event model** supporting multiple event types with natural keys
18. **Continuous aggregation refresh** handling late-arriving data
19. **Rate limiting centralization** preventing multi-process provider exhaustion
20. **Backup & DR strategy** with documented RPO/RTO

### Implementation Approach
The architecture is designed to be simple initially (monorepo, module interfaces) while establishing boundaries that support future growth (distributed services, HTTP APIs, increased volume). Foundation elements (instrument registry, calendars, adjustment policies) are built first to ensure data correctness from day one.

Critical items like instrument metadata, trading calendars, idempotent ingestion, and data lineage are not deferred—they're foundational requirements that prevent expensive refactoring and data quality issues later.