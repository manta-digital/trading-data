---
docType: slice-design
slice: instrument-registry-integration
project: trading
parent: user/architecture/100-slices.data-storage.md
dependencies: [102]
interfaces: [104, 105]
dateCreated: 20260403
dateUpdated: 20260403
status: complete
---

# Slice Design: Instrument Registry Integration

## Overview

Rewrite the `InstrumentRegistry` stub (created in slice 102's foundation) into a working psycopg3-backed service that reads and writes the `instruments` and `provider_symbol_mapping` tables on the TimescaleDB host. Fix the `@lru_cache` cross-instance pollution bug by replacing module-level `lru_cache` with per-instance caching. Seed initial instrument data by reading symbols from the daily `MarketDB.symbol_list` table and inserting corresponding rows into `instruments` + `provider_symbol_mapping`. Provide a CLI command (`mt data instruments`) for listing and inspecting registered instruments.

## Value

**Developer-facing:** Provides a working instrument lookup service (`get_by_symbol()`, `get_by_canonical_id()`) that downstream code (slice 104 calendar integration, slice 105 tick schema, future ingestion) can use to resolve symbol strings to canonical instrument identities.

**Operator-facing:** The `mt data instruments` CLI command gives visibility into what instruments are registered, their venues, and their provider mappings. The seed command populates the registry from the existing AlphaVantage symbol list, bridging the gap between the daily data pipeline and the new instrument model.

## Technical Scope

### In Scope
- Rewrite `InstrumentRegistry` class for psycopg3 with `conninfo` URL-based connection
- Replace `@lru_cache` on instance methods with per-instance cache (dict-based)
- Implement DB-backed methods: `get_by_symbol()`, `get_by_canonical_id()`, `get_by_provider_symbol()`, `register_instrument()`, `update_provider_mapping()`, `list_instruments()`
- Seed command: read `symbol_list` from MarketDB, insert into `instruments` + `provider_symbol_mapping` on TimescaleDB
- CLI command `mt data instruments` with subcommands: `list`, `seed`
- Unit tests with mock DB connections
- Integration tests against real TimescaleDB (skip when unavailable)

### Out of Scope
- Backfilling `instrument_id` in `minute_ohlcv` (future work item in slice plan)
- Modifying `MarketDB` or daily pipeline code
- Trading calendar integration (slice 104)
- Tick event schema (slice 105)
- `IDataService` protocol implementation (Initiative 140)

## Dependencies

### Prerequisites
- **Slice 102 (complete):** `instruments` and `provider_symbol_mapping` tables exist on TimescaleDB. Migration runner is functional.
- **Slice 100 (complete):** psycopg3 connection patterns established. `Settings` provides `timescale_db_url` and `market_db_url`.

### Interfaces Required
- `instruments` table schema (slice 102, migration 002)
- `provider_symbol_mapping` table schema (slice 102, migration 003)
- `MarketDB.symbol_list` table on PostgreSQL 16 host (read-only access for seeding)
- `Settings.timescale_db_url` and `Settings.market_db_url`

## Architecture

### Component Structure

```
src/manta_trading/data/base/instrument_registry.py  (rewrite)
  ├── Instrument dataclass              (keep as-is)
  └── InstrumentRegistry class          (rewrite: psycopg3-backed)
        ├── __init__(conninfo)          → create ConnectionPool
        ├── close()                     → close pool
        ├── get_by_symbol(symbol)       → cached lookup
        ├── get_by_canonical_id(cid)    → cached lookup
        ├── get_by_provider_symbol()    → cached lookup with date range
        ├── register_instrument()       → INSERT + return Instrument
        ├── update_provider_mapping()   → INSERT into provider_symbol_mapping
        ├── list_instruments()          → filtered SELECT
        └── _invalidate_cache()         → clear per-instance cache

src/manta_trading/cli/commands/data.py  (extend)
  └── instruments_app (Typer sub-app)
        ├── list                        → display registered instruments
        └── seed                        → populate from MarketDB symbol_list
```

### Data Flow

**Lookup flow:**
1. Caller invokes `registry.get_by_symbol("AAPL")`
2. Check per-instance cache → return if hit
3. Query `instruments` table: `SELECT * FROM instruments WHERE symbol = %s AND active = TRUE`
4. Construct `Instrument` dataclass from row
5. Cache and return

**Seed flow:**
1. CLI `mt data instruments seed` invoked
2. Open MarketDB connection (read `symbol_list`)
3. For each symbol row: derive `canonical_id` as `{symbol}.{exchange}`, determine `venue` from `exchange`, set `asset_class` from `assettype`
4. Insert into `instruments` table (ON CONFLICT DO NOTHING on `canonical_id`)
5. Insert into `provider_symbol_mapping` (provider=`alphavantage`, provider_symbol=symbol)
6. Report count of newly registered instruments

## Technical Decisions

### Per-Instance Cache (replacing `@lru_cache`)

**Problem:** `@lru_cache` on instance methods caches at the class level, causing cross-instance pollution and preventing garbage collection of instances (the cache holds a strong reference to `self`).

**Solution:** Use a simple `dict` cache per instance, keyed by method name + arguments. The cache is created in `__init__` and cleared via `_invalidate_cache()`. This is simpler and correct — no `functools` machinery, no class-level state.

```python
def __init__(self, conninfo: str):
    self._pool = ConnectionPool(conninfo, min_size=1, max_size=5)
    self._cache: dict[str, Instrument] = {}

def get_by_symbol(self, symbol: str) -> Instrument | None:
    key = f"symbol:{symbol}"
    if key in self._cache:
        return self._cache[key]
    row = self._query_instrument_by_symbol(symbol)
    if row:
        inst = self._row_to_instrument(row)
        self._cache[key] = inst
        return inst
    return None

def _invalidate_cache(self) -> None:
    self._cache.clear()
```

### Canonical ID Convention

Canonical IDs follow the format `{SYMBOL}.{VENUE}` (e.g., `AAPL.NASDAQ`, `JPM.NYSE`). This is already established in the `Instrument` dataclass and slice 102's schema (VARCHAR(64), UNIQUE constraint).

**Venue mapping from AlphaVantage `exchange` field:**
- "NYSE" → venue "NYSE", calendar "NYSE"
- "NASDAQ" → venue "NASDAQ", calendar "NASDAQ"
- "NYSE ARCA" → venue "NYSE_ARCA", calendar "NYSE"
- "NYSE MKT" → venue "NYSE_MKT", calendar "NYSE"
- "BATS" → venue "BATS", calendar "NYSE"
- Other → venue as-is, calendar "NYSE" (default for US equities)

This mapping is defined as a constant dict, not scattered conditionals.

### Asset Class Mapping

AlphaVantage `assettype` field maps to `asset_class`:
- "Stock" → "equity"
- "ETF" → "etf"
- Other → lowercase of `assettype`

### Connection Pattern

Follow the established pattern from `TimescaleMinuteDataDB`:
- Accept `conninfo: str` in constructor
- Create `psycopg_pool.ConnectionPool` with `min_size=1, max_size=5`
- Provide `close()` method to shut down pool
- Each public method acquires/releases its own connection via `with pool.connection() as conn:`
- Use `dict_row` cursor factory for row results

### Parameterized Queries

All SQL uses `%s` parameterized placeholders — no f-strings in SQL. This is a project rule and a security requirement.

## Implementation Details

### InstrumentRegistry API

```python
class InstrumentRegistry:
    def __init__(self, conninfo: str) -> None: ...
    def close(self) -> None: ...

    # Lookups (cached)
    def get_by_symbol(self, symbol: str) -> Instrument | None: ...
    def get_by_canonical_id(self, canonical_id: str) -> Instrument | None: ...
    def get_by_provider_symbol(
        self, provider: str, provider_symbol: str,
        as_of_date: date | None = None,
    ) -> Instrument | None: ...

    # Write operations (invalidate cache)
    def register_instrument(
        self, canonical_id: str, symbol: str, asset_class: str, venue: str,
        currency: str = "USD", tick_size: float | None = None,
        lot_size: int = 1, trading_calendar_id: str | None = None,
        adjustment_policy: str = "split_adjusted",
        metadata: dict | None = None,
    ) -> Instrument: ...

    def update_provider_mapping(
        self, instrument_id: int, provider: str, provider_symbol: str,
    ) -> None: ...

    # Query
    def list_instruments(
        self, *, asset_class: str | None = None, venue: str | None = None,
        active_only: bool = True,
    ) -> list[Instrument]: ...

    # Cache management
    def _invalidate_cache(self) -> None: ...
```

### Seeding Logic

The seed operation reads from `MarketDB.symbol_list` on the daily DB host and writes to `instruments` + `provider_symbol_mapping` on the TimescaleDB host. Both connections are needed simultaneously.

```
Seed flow:
1. Connect to MarketDB (market_db_url), read symbol_list rows
2. For each row:
   a. Map exchange → venue (via VENUE_MAP constant)
   b. Map assettype → asset_class (via ASSET_CLASS_MAP constant)
   c. Derive canonical_id = f"{symbol}.{venue}"
   d. Determine trading_calendar_id from venue (NYSE family → "NYSE", NASDAQ → "NASDAQ")
   e. Call registry.register_instrument(...) — uses ON CONFLICT DO NOTHING
   f. Call registry.update_provider_mapping(instrument_id, "alphavantage", symbol)
3. Return count of new instruments and mappings
```

The seed logic lives in a dedicated module (`src/manta_trading/market/instrument_seed.py`) to keep InstrumentRegistry focused on CRUD operations. The CLI command calls the seed function.

### CLI Commands

**`mt data instruments list`**
- Default: table of symbol, canonical_id, venue, asset_class, active (using Rich table)
- `--venue VENUE` filter
- `--asset-class CLASS` filter
- `--json` for machine-readable output
- `--inactive` to include inactive instruments

**`mt data instruments seed`**
- Reads MarketDB symbol_list, inserts into instruments + provider_symbol_mapping
- Reports: total read, newly registered, already existed, mappings created
- `--json` for machine-readable output
- `--dry-run` to preview without writing
- Requires both `MT_MARKET_DB_URL` and `MT_TIMESCALE_DB_URL`

### SQL Queries

**get_by_symbol:**
```sql
SELECT instrument_id, canonical_id, symbol, asset_class, venue,
       currency, tick_size, lot_size, trading_calendar_id,
       adjustment_policy, active, metadata
FROM instruments
WHERE symbol = %s AND active = TRUE
LIMIT 1
```

Note: `symbol` is not unique — multiple venues may list the same symbol. `get_by_symbol` returns the first active match. Use `get_by_canonical_id` for unambiguous lookup.

**get_by_canonical_id:**
```sql
SELECT instrument_id, canonical_id, symbol, asset_class, venue,
       currency, tick_size, lot_size, trading_calendar_id,
       adjustment_policy, active, metadata
FROM instruments
WHERE canonical_id = %s
```

**get_by_provider_symbol:**
```sql
SELECT i.instrument_id, i.canonical_id, i.symbol, i.asset_class, i.venue,
       i.currency, i.tick_size, i.lot_size, i.trading_calendar_id,
       i.adjustment_policy, i.active, i.metadata
FROM instruments i
JOIN provider_symbol_mapping psm ON i.instrument_id = psm.instrument_id
WHERE psm.provider = %s AND psm.provider_symbol = %s
  AND psm.valid_from <= %s
  AND (psm.valid_to IS NULL OR psm.valid_to > %s)
```

**register_instrument:**
```sql
INSERT INTO instruments
    (canonical_id, symbol, asset_class, venue, currency, tick_size,
     lot_size, trading_calendar_id, adjustment_policy, metadata)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (canonical_id) DO NOTHING
RETURNING instrument_id, canonical_id, symbol, asset_class, venue,
          currency, tick_size, lot_size, trading_calendar_id,
          adjustment_policy, active, metadata
```

If `ON CONFLICT DO NOTHING` triggers (already exists), fetch and return the existing row.

**update_provider_mapping:**
```sql
INSERT INTO provider_symbol_mapping
    (instrument_id, provider, provider_symbol)
VALUES (%s, %s, %s)
ON CONFLICT DO NOTHING
```

Uses the partial unique index `(provider, provider_symbol) WHERE valid_to IS NULL` from migration 003.

**list_instruments:**
```sql
SELECT instrument_id, canonical_id, symbol, asset_class, venue,
       currency, tick_size, lot_size, trading_calendar_id,
       adjustment_policy, active, metadata
FROM instruments
WHERE ($1 IS NULL OR asset_class = $1)
  AND ($2 IS NULL OR venue = $2)
  AND ($3 = FALSE OR active = TRUE)
ORDER BY symbol, venue
```

Note: Use conditional WHERE clauses with parameterized queries, not string concatenation.

## Integration Points

### Provides to Other Slices
- **Slice 104 (Trading Calendar):** `InstrumentRegistry.get_by_symbol()` returns `Instrument` with `trading_calendar_id` — the calendar integration can look up which calendar applies to a given instrument
- **Slice 105 (Tick Event Hypertable):** `instrument_id` from the registry is the FK target for tick events
- **Future work (instrument_id backfill):** The registry provides the symbol→instrument_id mapping needed to populate `minute_ohlcv.instrument_id`

### Consumes from Other Slices
- **Slice 102:** `instruments` and `provider_symbol_mapping` tables with indexes and constraints
- **Slice 100:** psycopg3 connection patterns, `Settings` with `timescale_db_url` and `market_db_url`

## Success Criteria

### Functional Requirements
- `InstrumentRegistry` connects to TimescaleDB via `conninfo` URL and reads/writes `instruments` table
- `get_by_symbol()`, `get_by_canonical_id()`, `get_by_provider_symbol()` return `Instrument` dataclass or `None`
- `register_instrument()` inserts new instruments with ON CONFLICT DO NOTHING
- `update_provider_mapping()` creates provider symbol mappings
- `list_instruments()` supports filtering by asset_class, venue, and active status
- Per-instance cache replaces `@lru_cache` — no cross-instance pollution
- `mt data instruments seed` reads MarketDB `symbol_list` and populates instruments + mappings
- `mt data instruments list` displays registered instruments with optional filters
- Seed operation is idempotent (running twice produces no duplicates)

### Technical Requirements
- All SQL uses parameterized queries (no f-string SQL)
- Unit tests cover all lookup methods, cache behavior, and edge cases (not found, duplicate)
- Integration tests verify against real TimescaleDB (skip when unavailable)
- Existing tests continue to pass (538+ unit tests)

### Verification Walkthrough

**1. Run unit tests:**
```bash
uv run pytest test/unit/data/base/test_instrument_registry.py -v
uv run pytest test/unit/test_cli_data.py -v -k instruments
```

**2. Run seed command (requires both DB URLs):**
```bash
MT_MARKET_DB_URL=postgresql://... MT_TIMESCALE_DB_URL=postgresql://... \
  uv run mt data instruments seed
# Expected: "Registered N instruments, created N provider mappings"
```

**3. List instruments:**
```bash
MT_TIMESCALE_DB_URL=postgresql://... uv run mt data instruments list
# Expected: table of instruments with symbol, canonical_id, venue, asset_class

MT_TIMESCALE_DB_URL=postgresql://... uv run mt data instruments list --venue NYSE
# Expected: filtered to NYSE instruments only

MT_TIMESCALE_DB_URL=postgresql://... uv run mt data instruments list --json
# Expected: JSON array of instrument objects
```

**4. Verify idempotency:**
```bash
# Run seed again
MT_MARKET_DB_URL=postgresql://... MT_TIMESCALE_DB_URL=postgresql://... \
  uv run mt data instruments seed
# Expected: "0 new instruments registered (N already existed)"
```

**5. Integration tests (requires TimescaleDB):**
```bash
MT_TIMESCALE_DB_URL=postgresql://... uv run pytest test/integration/test_instrument_registry_integration.py -v
```

**6. Verify all tests pass:**
```bash
uv run pytest test/unit/ -v
```

## Implementation Notes

### Development Approach

1. **Rewrite InstrumentRegistry** — Replace stubs with psycopg3 implementation. Fix cache. Update existing unit tests.
2. **Add seed module** — `instrument_seed.py` with venue/asset_class mapping and MarketDB reading logic.
3. **Add CLI commands** — `mt data instruments list` and `mt data instruments seed`.
4. **Integration tests** — Test against real TimescaleDB.
5. **Verify full test suite** — Ensure no regressions.

### Special Considerations

- **Two-database seed operation:** The seed command reads from MarketDB (PostgreSQL 16) and writes to TimescaleDB. Both URLs must be configured. The CLI should fail explicitly if either is missing.
- **Symbol ambiguity:** Multiple exchanges may list the same symbol. `get_by_symbol()` returns the first active match — callers who need precision should use `get_by_canonical_id()` or `get_by_provider_symbol()`.
- **AlphaVantage exchange normalization:** The `exchange` field from AlphaVantage uses inconsistent naming (e.g., "NYSE ARCA" vs "NYSE Arca"). The venue mapping should be case-insensitive.
- **Delisted instruments:** Symbols with a `delistingdate` in `symbol_list` should be registered with `active = FALSE`.
