---
docType: architecture
component: data-serving
project: trading
parent: 001-initiative-plan_trading.md
dependencies:
  - 100-arch_data-storage.md
  - 140-arch_data-quality-operations.md
relatedSlices: [181, 182, 183, 184, 185, 186, 187]
riskLevel: low
archIndex: 180
dateCreated: 20260512
dateUpdated: 20260803
status: in-progress
---

# Data Serving API Architecture

## Overview

Initiative 180 adds an HTTP API to trading-data, exposing the existing data access layer to external consumers — primarily trading-ui (TypeScript/React, cannot import Python) and optionally trading-engine (which can also connect to TimescaleDB directly).

The API wraps class methods that already exist on `TimescaleMinuteDataDB` and `TimescaleDailyDataDB`. There is minimal new logic; the value is in making the data accessible over HTTP with proper serialization, CORS, and error handling.

## Why This Exists

The database stores unadjusted prices. The adjustment logic (`adjusted()` function) lives in Python. Any consumer that needs adjusted prices must either:

1. Import the Python package (only works for Python consumers), or
2. Call through an API that applies adjustment before returning data.

trading-ui is TypeScript. Option 1 is not available. Therefore, an API.

## Design Principle: Thin Wrapper

The API does not contain business logic. It:

- Parses HTTP request parameters
- Calls existing methods (`TimescaleMinuteDataDB.get_minute_data`, `TimescaleDailyDataDB.get_daily_data`)
- Serializes the result to JSON
- Returns it with appropriate status codes and headers

If a new query capability is needed, it gets added to the data access layer first (where it's testable without HTTP), then exposed via an endpoint.

## Endpoints

### Bars

```
GET /api/v1/bars/{symbol}
    ?granularity=1m        # Required. Granularity StrEnum value (1m/5m/15m/1h/4h/1d/1w/1mo/1q).
    &start=2024-06-01      # Required. ISO date (YYYY-MM-DD). Time component not accepted.
    &end=2024-06-15        # Required. ISO date (YYYY-MM-DD). Time component not accepted.
    &adjusted=true         # Optional, default true.
    &format=json           # Optional. json (default) or msgpack.
```

Returns OHLCV array. `Granularity` is `manta_trading.constants.Granularity` (StrEnum). Minute granularities: `1m, 5m, 15m, 1h, 4h` — routed to `TimescaleMinuteDataDB.get_minute_data` (which takes `datetime` for start/end; the API converts ISO date strings to `datetime` at midnight UTC). Daily granularities: `1d, 1w, 1mo, 1q` — routed to `TimescaleDailyDataDB.get_daily_data` (which takes `date`). The caller doesn't need to know which method handles which granularity — the API routes internally. The private sets `_MINUTE_GRAINS` and `_DAILY_GRAINS` in the respective DB modules define the split; the API will replicate this as a module-level constant in `routes/bars.py`.

Response shape:

```json
{
  "symbol": "SPY",
  "granularity": "1m",
  "adjusted": true,
  "count": 2340,
  "bars": [
    {
      "timestamp": "2024-06-10T09:30:00Z",
      "open": 531.24,
      "high": 531.45,
      "low": 531.10,
      "close": 531.38,
      "volume": 1234567
    }
  ]
}
```

For large responses (minute data over weeks — thousands of bars), msgpack format reduces payload size ~40-60% vs JSON. The UI can request this once the TypeScript msgpack decoder is wired.

### Symbols

```
GET /api/v1/symbols
    ?search=APP             # Optional. Case-insensitive prefix match on symbol.
```

Returns array of available symbols with basic metadata. Note: the `instruments` table does not store a human-readable name — the response omits `name`. Fields map from `instruments` columns: `symbol`, `eodhd_exchange` → `exchange`, `eodhd_type` → `type`, `asset_class`, `active`. A named-list filter (`?list=sp500`) is deferred to a future slice once list membership is tracked in the DB.

```json
{
  "symbols": [
    {
      "symbol": "AAPL",
      "exchange": "NASDAQ",
      "type": "Common Stock",
      "asset_class": "equity",
      "active": true
    }
  ],
  "count": 1
}
```

### Symbol Detail

```
GET /api/v1/symbols/{symbol}
```

Returns instrument metadata and available data ranges per granularity. Lets the UI know what date ranges it can request.

```json
{
  "symbol": "SPY",
  "exchange": "NYSE",
  "type": "ETF",
  "asset_class": "equity",
  "active": true,
  "available": {
    "1d": { "start": "2000-01-03", "end": "2026-05-09" },
    "1w": { "start": "2000-01-03", "end": "2026-05-09" },
    "1mo": { "start": "2000-01-03", "end": "2026-05-09" },
    "1q": { "start": "2000-01-03", "end": "2026-05-09" },
    "1m": { "start": "2024-03-15", "end": "2026-05-09" },
    "5m": { "start": "2024-03-15", "end": "2026-05-09" },
    "15m": { "start": "2024-03-15", "end": "2026-05-09" },
    "1h": { "start": "2024-03-15", "end": "2026-05-09" },
    "4h": { "start": "2024-03-15", "end": "2026-05-09" }
  }
}
```

**Corrected by slice 187 (D1, D2).** The original text here said the `available` ranges were two lazy `MIN/MAX` queries, "both index seeks on `(symbol, time_bucket)` — sub-millisecond at single-symbol scope." That was wrong on both counts, and the mistake was load-bearing enough to change the slice that fixed it.

Measured on prod `trading`, 2026-08-04: the minute-side `MIN/MAX` on `minute_5min_ohlcv` costs ~32 ms — not sub-millisecond, but not the problem either. The daily-side `MIN/MAX` on `daily_ohlcv` costs **2.5–4.0 s**, and **96% of that is planning**, across `daily_ohlcv`'s 3,371 chunks. An unbounded aggregate over a hypertable is not an index seek; it is slow *before it reads anything*. The two queries were also dispatched through `asyncio.gather` on a single pooled connection, which psycopg serializes — so the endpoint paid the sum, ~2.7–4.1 s.

As of slice 187 the ranges are read as a **coverage floor merged with a bounded head probe**, and every statement on the path carries a bound the planner can prune on:

1. **Universe edges** — `MAX(last_bucket)` per coverage cagg across all symbols, cached on `app.state` under `CAGG_FRESHNESS_CACHE_TTL` (60 s).
2. **Per-symbol coverage** — `MIN(first_bucket)/MAX(last_bucket)` from `minute_coverage` and `daily_coverage` in one `UNION ALL`.
3. **Per-symbol head probe** — `MIN/MAX` on `minute_5min_ohlcv` and `daily_ohlcv`, each bounded by its family's universe edge.

Merged as `start = COALESCE(coverage_start, head_start)`, `end = COALESCE(head_end, coverage_end)`, with a family omitted when both resolve to NULL. The coverage caggs are structurally stale on production (slice 169 repairs them), so the head probe — not coverage — is what makes the advertised leading edge correct. The three statements run sequentially inside one `run_in_executor` dispatch on one connection; the `gather` is gone.

Measured after the change: **16–35 ms per symbol**, planning 3.0 / 6.4 / 3.1 ms. The bound must be bound as a `timestamptz`, not a `date` — a `date` parameter makes `timestamptz > date` resolve through a conversion the planner cannot prune on, which costs 3,100 ms for the identical rows.

A materialized view was considered in the original design and ruled out at ~36k symbols and 1B+ minute rows; slice 167's coverage caggs are that structure, built hierarchically so the refresh cost is bounded. Granularities with no data are still omitted from the `available` dict.

### Data Gaps (optional, for engine consumers)

```
GET /api/v1/gaps/{symbol}
    ?granularity=1m
    &start=2024-06-01
    &end=2024-06-15
```

Returns gap information from the `data_gaps` table. Useful for trading-engine's gap policy enforcement. Less relevant for the UI initially.

### Health

```
GET /api/v1/health
```

Returns server status and database connectivity. Standard liveness check.

## Technical Stack

- **Framework**: FastAPI (new dependency). Async support for concurrent requests. Automatic OpenAPI docs. Pydantic for request/response models.
- **ASGI server**: Uvicorn (new dependency). Single worker is fine for single-user local network use.
- **CORS**: Permissive for local network. trading-ui runs on a different port (Vite dev server on 5173 or similar), so CORS must allow the origin.
- **Database connection**: the API shares the existing `Settings` and the same `MT_TIMESCALE_DB_URL` the CLI uses — no new *connection* config (no separate credentials, host, or database). **Corrected by slice 186 (D1, D11):** "same pool" was never true as built. The API process opens **three** independent psycopg3 pools — its own (`app.state.db_pool`, for health/status/symbols/gaps and the freshness probe) plus the class-owned pools inside `TimescaleMinuteDataDB` and `TimescaleDailyDataDB`, which serve the bars path. Slice 186 gives all three serving-sized session settings (`statement_timeout`, `work_mem`) via an optional constructor argument whose defaults preserve CLI and daemon behavior. Consolidating to a single shared pool was deferred to slice 187, which built the load-test tier that could size it. **Decided there (D11, 2026-08-04): not now.** 16 concurrent symbol-detail requests against `app.state.db_pool` at `max_size=8` all completed in 144 ms wall clock, per-request median 88 ms and max 141 ms against a 32 ms uncontended median — a clean two-wave staircase, which is the pool working rather than a bottleneck. The two class-owned pools are idle on that path, so consolidation would not have moved any measured number; the remaining concern is idle-connection *resource* cost, which this measurement cannot speak to. Reopening it needs a load assertion that drives the **bars** path concurrently, and the tier now exists to host one. Two API *policy* settings are added (`MT_API_MAX_BARS_PER_REQUEST`, `MT_API_STATEMENT_TIMEOUT`); both default to constants in `constants.py`. Existing DB methods are synchronous; the API will use `asyncio.get_event_loop().run_in_executor(None, ...)` to call them from async route handlers. This is appropriate for single-user local network use; migrate to `AsyncConnection` only if profiling shows contention.
- **Serialization**: orjson (new dependency) for fast JSON serialization. Optional msgpack via the `msgpack` library (new dependency) for large bar responses — reduces payload ~40-60% vs JSON for minute data over weeks. The TypeScript client must use a compatible msgpack decoder (e.g. `@msgpack/msgpack`) when requesting this format.
- **New pyproject.toml additions**: `fastapi`, `uvicorn[standard]`, `orjson`, `msgpack`.

## CLI Integration

```
mt serve
    --host 0.0.0.0         # Default: 0.0.0.0
    --port 8100             # Default: 8100
    --reload                # Dev mode: auto-reload on code changes
```

One new Typer command. Starts Uvicorn with the FastAPI app. That's it.

## Code Location

The package is `api_server/`, not `api/` as this document originally specified. The name was forced, not drifted: `src/manta_trading/api/` already exists and holds the **outbound** provider HTTP clients (EODHD, Finnhub), which predate the server. Landed layout (181–185), with `status.py` added by 185 and `queries.py` by 186:

```
src/manta_trading/api_server/
    __init__.py
    app.py              # FastAPI app instance, CORS, lifespan (DB pool), exception handlers
    routes/
        bars.py         # GET /api/v1/bars/{symbol}
        symbols.py      # GET /api/v1/symbols, GET /api/v1/symbols/{symbol}
        gaps.py         # GET /api/v1/gaps/{symbol}
        health.py       # GET /api/v1/health
        status.py       # GET /api/v1/status                    (slice 185)
    models/
        responses.py    # Pydantic models for response shapes
    queries.py          # Shared route-level SQL (symbol_exists) (slice 186)
    deps.py             # Dependency injection (pool, connection, DB instances)
```

`models/requests.py` was never needed — query params are declared inline on the route signatures and validated by FastAPI.

The route handlers are thin. Example for bars:

```python
# _MINUTE_GRAINS defined here, mirroring timescale_minute_db._MINUTE_GRAINS
_MINUTE_GRAINS = {Granularity.M1, Granularity.M5, Granularity.M15, Granularity.H1, Granularity.H4}

@router.get("/bars/{symbol}")
async def get_bars(
    symbol: str,
    granularity: Granularity,
    start: date,
    end: date,
    adjusted: bool = True,
    db: DbPool = Depends(get_db),
) -> BarsResponse:
    loop = asyncio.get_event_loop()
    if granularity in _MINUTE_GRAINS:
        # get_minute_data takes datetime; convert date to midnight UTC
        start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(end, time.min, tzinfo=timezone.utc)
        bars_df = await loop.run_in_executor(
            None, lambda: TimescaleMinuteDataDB(db).get_minute_data(
                symbol, start_dt, end_dt, aggregation=granularity, adjusted=adjusted
            )
        )
    else:
        bars_df = await loop.run_in_executor(
            None, lambda: TimescaleDailyDataDB(db).get_daily_data(
                symbol, start, end, granularity=granularity, adjusted=adjusted
            )
        )
    return BarsResponse.from_dataframe(symbol, granularity, adjusted, bars_df)
```

The async/sync bridge (`run_in_executor`) is the chosen approach. Existing DB methods are synchronous psycopg3; FastAPI is async. For single-user local network use this is correct — migrate to `AsyncConnection` only if profiling shows a bottleneck.

## Error Handling

FastAPI exception handlers, registered in `app.py`:

- **404** — symbol not found in the instruments table. **Revised by slice 186 (D5):** an empty result for a *known* symbol is `200` with `count: 0`, not `404` — a weekend must not be indistinguishable from a typo.
- **422** — invalid query params (FastAPI/Pydantic raises this automatically for type errors); also, per slice 186 (D4), a window exceeding the range cap or a `start` after its `end`.
- **500** — unhandled DB error. Error detail is logged server-side but response body returns a sanitized message only — no SQL leaks to clients.
- **504** — added by slice 186 (D10): the DB cancelled a query on `statement_timeout`. Distinguished from `500` because it is not a server fault and a narrower window is a reasonable retry. **`statement_timeout` bounds a statement, not a request** — a route that issues many statements can exceed the budget many times over without any of them being cancelled. Slice 186 D12b measured a 95-second request under a 20-second budget for exactly this reason. Request-level latency is not enforced anywhere; treat the timeout as a guard against one runaway scan, not as a latency ceiling.

All error responses raised by this codebase use a consistent shape: `{"error": "<message>"}`. The one deliberate exception is FastAPI's own `RequestValidationError` body (`{"detail": [{"loc": …, "msg": …}]}`), kept native because it carries per-field structure a flattened string would lose (slice 186 D6).

## Range Policy

**Revised by slice 186 (2026-08-03).** No server-side pagination — that decision stands. But "the API trusts callers to request bounded ranges… a UI concern, not an API concern," as originally written here, did not survive contact with the measured data: the store carries extended-hours minute bars (08:00–23:59 UTC, up to 960 `1m` bars per symbol-day, not the 390 a regular session implies), and an unbounded request serializes millions of rows through a single executor thread on a host shared with the acquisition daemon.

The API now applies a **pre-query admission cap**: estimated bars for the requested window are computed from the request alone and rejected with `422` above a configurable ceiling (`MT_API_MAX_BARS_PER_REQUEST`, default 75,000 — ~113 days of `1m`, ~1.5 years of `5m`; never binds at `1d` or coarser). No DB work is done for a rejected request, and no response is ever silently truncated. See slice 186 D4 and D9.

## What This Does NOT Include

- **Authentication/authorization** — single-user tool on local network. No auth.
- **Rate limiting** — unnecessary for single-user.
- **Caching layer** — the data rarely changes (daily update at EOD, minute backfill in progress). If the UI re-requests the same range, the DB query is fast enough. Add Redis or in-memory caching only if measurement shows a problem.
- **WebSocket streaming** — not needed until live tick data flows, which is a trading-feed concern. Historical data is request/response.
- **Tick data endpoints** — initiative 220 (Futures Tick) hasn't landed yet. Tick endpoints are added when tick storage exists.
- **Aggregation or computation** — the API serves stored data. Indicator computation, regime classification, and strategy results come from trading-engine's API, not this one.

## Relationship to trading-engine

trading-engine can consume data two ways:

1. **Direct DB connection** — Python process, imports psycopg3, runs the same queries. No API needed. This is the default for backtesting (avoids HTTP overhead for bulk data pulls).
2. **Via this API** — useful if the engine runs on a different host, or for standardization. The `IDataClient` protocol in the engine concept can have both `TimescaleDataClient` and `ApiDataClient` implementations.

Both paths return the same data. The API is primarily for non-Python consumers (trading-ui) and convenience.

## Sequencing

This is a small initiative. Estimated 3-4 slices:

1. **FastAPI skeleton + `mt serve` + health endpoint** — proves the server starts and connects to the DB.
2. **Bars endpoint** — the critical path for trading-ui. Wraps `get_minute_data` / `get_daily_data`.
3. **Symbols endpoints** — symbol list and detail with available ranges.
4. **Polish** — error handling, msgpack support, gaps endpoint, OpenAPI docs cleanup.

Slice 2 unblocks trading-ui development. Everything else can happen in parallel with UI work.
