---
docType: slice-design
slice: symbols-endpoints-available-ranges-view
project: trading
parent: user/architecture/180-slices.data-serving-api.md
dependencies: [182]
interfaces: [184]
dateCreated: 20260514
dateUpdated: 20260513
status: complete
---

# Slice Design: Symbols Endpoints + Available Ranges

## Overview

This slice adds two symbol discovery endpoints to the Data Serving API:

- `GET /api/v1/symbols` — list symbols with optional prefix search
- `GET /api/v1/symbols/{symbol}` — instrument metadata + available data ranges per granularity

The original slice plan called for a `symbol_data_ranges` materialized view refreshed hourly. That approach does not scale: at ~36k symbols × 9 granularity tables, a full MIN/MAX scan on 1B+ minute rows is prohibitively expensive to refresh. This slice replaces it with **lazy per-symbol queries** — two indexed lookups at request time, one per DB family (minute, daily). This is fast (index seek on `symbol` column), requires no schema migration, and eliminates the refresh machinery entirely.

## Scope

**Included:**

- `src/manta_trading/api_server/routes/symbols.py` — `GET /api/v1/symbols` and `GET /api/v1/symbols/{symbol}`
- `src/manta_trading/api_server/models/responses.py` — add `SymbolSummary`, `SymbolDetail`, `AvailableRange`, `SymbolsResponse`
- `src/manta_trading/api_server/deps.py` — add `get_symbols_db` (raw psycopg connection from pool)
- `src/manta_trading/api_server/app.py` — register symbols router
- `test/unit/api_server/test_symbols.py` — unit tests via `TestClient`

**Excluded:**

- Materialized view / `symbol_data_ranges` table — replaced by lazy per-symbol queries; see Technical Decisions
- `pg_cron` or refresh policies — not needed with lazy approach
- Pagination — consistent with `/bars`; no server-side pagination
- Symbol `name` field — not present in `instruments` table; see Field Mapping; arch doc updated to reflect this
- `?list=sp500` named-list filter — deferred; list membership not tracked in DB yet; arch doc updated
- `api/` → `api_server/` path — package named `api_server` in slice 181 to avoid collision with existing `manta_trading.api` (outbound provider HTTP utilities); arch doc predates that decision

## Technical Decisions

### No Materialized View

The slice plan proposed a `symbol_data_ranges` matview. At production scale (~36k symbols, 1B+ raw minute rows, 7 CAGGs), a full refresh is a large sequential scan. The lazy approach fires two indexed queries per symbol detail request:

1. `SELECT MIN(time_bucket)::date, MAX(time_bucket)::date FROM minute_5min_ohlcv WHERE symbol = $1`
2. `SELECT MIN(time)::date, MAX(time)::date FROM daily_ohlcv WHERE symbol = $1`

Both hit an index on `(symbol, time_bucket)` / `(symbol, time)`. At single-symbol scope, these are index seeks — sub-millisecond on the TimescaleDB hypertable. No schema migration required.

**Minute granularity proxy:** `minute_5min_ohlcv` covers the same symbol/time range as `minute_ohlcv` but with far fewer rows (÷5). Its MIN/MAX equals the raw minute range. The same date range is reported for all minute granularities (M1, M5, M15, H1, H4) — the range boundary is identical regardless of aggregation level.

**Daily granularity source:** `daily_ohlcv` is queried directly. Its range applies to D1, W1, MO1, Q1.

**Future option:** A daemon-written `symbol_data_ranges` table (updated at ingest time) would give zero-cost reads. Defer until the ingest write path is stable and the range tracking can be proven reliable across backfills and restores.

**Approved deviation from architecture:** The arch doc specified a materialized view refreshed hourly. This slice replaces it with lazy indexed queries. The arch doc has been updated to document this decision and its rationale.

### Field Mapping: `instruments` Table

The `instruments` table does not have `name`, `exchange`, or `type` columns matching the arch doc's response shape. Actual columns:

| Response field | Source column |
|---|---|
| `symbol` | `symbol` |
| `exchange` | `eodhd_exchange` |
| `type` | `eodhd_type` |
| `asset_class` | `asset_class` |
| `active` | `NOT delisted_at_eodhd` (inverted — `false` = active in DB) |

**Implementation note:** The `instruments` table has no `active` column. The production schema uses `delisted_at_eodhd boolean` (false = actively listed). The API exposes `active = NOT delisted_at_eodhd` to give callers a positive, semantically stable field that doesn't leak the storage column name.

`SymbolSummary` and `SymbolDetail` expose `symbol`, `exchange`, `type`, `asset_class`, and `active`. The `name` field is omitted — `canonical_id` is not a human-readable name and exposing it as `name` would be misleading. If a name field is added to `instruments` in a future slice, it can be added to the response then.

### `GET /api/v1/symbols` — List Endpoint

Query `instruments` table directly via the API server's existing `ConnectionPool` (accessed through `get_db`). No new DB object needed.

```sql
-- No filter (psycopg3 %s placeholders):
SELECT symbol, eodhd_exchange, eodhd_type, asset_class,
       NOT delisted_at_eodhd AS active
FROM instruments ORDER BY symbol

-- With prefix filter:
SELECT symbol, eodhd_exchange, eodhd_type, asset_class,
       NOT delisted_at_eodhd AS active
FROM instruments WHERE symbol ILIKE %s ORDER BY symbol
```

`%s` receives `search + '%'` when a prefix is provided; no parameter passed for the unfiltered query. `ILIKE` for case-insensitive prefix match.

Response: `SymbolsResponse(symbols: list[SymbolSummary], count: int)`.

No pagination — consistent with `/bars`. Callers requesting all symbols get the full list (~36k rows, small payload since it's metadata only).

### `GET /api/v1/symbols/{symbol}` — Detail Endpoint

Two-phase query:

1. Fetch instrument row from `instruments WHERE symbol = $1`. If not found, raise `HTTPException(404)`.
2. Fire two async executor calls (can be concurrent via `asyncio.gather`):
   - Minute range from `minute_5min_ohlcv`
   - Daily range from `daily_ohlcv`

Both phase-2 queries run via `run_in_executor` (same pattern as `/bars`). They can run concurrently since they hit different hypertables.

Build `available` dict: include only granularities for which the DB returned a non-NULL range.

```python
available = {}
if minute_range:
    for g in (Granularity.M1, Granularity.M5, Granularity.M15, Granularity.H1, Granularity.H4):
        available[str(g)] = AvailableRange(start=minute_range.start, end=minute_range.end)
if daily_range:
    for g in (Granularity.D1, Granularity.W1, Granularity.MO1, Granularity.Q1):
        available[str(g)] = AvailableRange(start=daily_range.start, end=daily_range.end)
```

### Dependency Injection

Both endpoints use raw psycopg connections from the existing pool (`get_db` from `deps.py`). No new DB class instances needed — these are simple `SELECT` queries, not wrapped in a DB class.

The detail endpoint's range queries are plain SQL fired directly on the connection, not routed through `TimescaleMinuteDataDB` or `TimescaleDailyDataDB` (those classes are for OHLCV data retrieval with adjustment logic, not for metadata queries).

### 404 Shape

Reuses the existing `_custom_http_exception_handler` registered in `app.py` (slice 182). Unknown symbol returns `404 {"error": "Symbol 'XYZ' not found"}`.

## Response Models

```python
class AvailableRange(BaseModel):
    start: date
    end: date

class SymbolSummary(BaseModel):
    symbol: str
    exchange: str | None
    type: str | None
    asset_class: str | None
    active: bool

class SymbolsResponse(BaseModel):
    symbols: list[SymbolSummary]
    count: int

class SymbolDetail(BaseModel):
    symbol: str
    exchange: str | None
    type: str | None
    asset_class: str | None
    active: bool
    available: dict[str, AvailableRange]  # keyed by granularity string e.g. "1d"
```

## API Specification

```
GET /api/v1/symbols
    ?search=SPY     # Optional. Case-insensitive prefix match on symbol.

GET /api/v1/symbols/{symbol}
```

Both endpoints return JSON only (no msgpack — metadata responses are small).

## Cross-Slice Dependencies and Interfaces

- **Depends on [182]**: `create_app()`, `get_db`, existing pool and lifespan, 404 handler
- **Interfaces to [184]**: `routes/symbols.py` router registered in `app.py`; no new state on `app.state`

## Success Criteria

1. `GET /api/v1/symbols` returns a JSON array of symbol summaries; `count` matches `len(symbols)`.
2. `GET /api/v1/symbols?search=SPY` returns only symbols with `symbol` starting with `SPY` (case-insensitive).
3. `GET /api/v1/symbols/SPY` returns instrument metadata with `available` containing at least `"1d"` range.
4. `GET /api/v1/symbols/SPY` `available` dict omits granularities for which no data exists.
5. `GET /api/v1/symbols/FAKESYMBOL` returns `404 {"error": "..."}`.
6. `GET /api/v1/health` still returns `{"status": "ok", "db": "ok"}` (regression).
7. Unit tests pass without a live DB (mock `get_db` dependency).
8. `ruff` and `pyright` report zero errors.

## Verification Walkthrough

**Prerequisites:** `MT_TIMESCALE_DB_URL` set to production DB (`trading`); server not running.

> **Note:** `trading_test` has 0 instrument rows. Steps 2–6 require the production `trading` DB.

**1. Start server:**
```bash
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading" uv run mt serve
```
Wait for `Application startup complete.`

**2. List all symbols (no filter):**
```bash
curl -s "http://localhost:8100/api/v1/symbols" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('count:', d['count'])"
```
Actual: `count: 31688`

**3. Prefix search:**
```bash
curl -s "http://localhost:8100/api/v1/symbols?search=SPY" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print([s['symbol'] for s in d['symbols']])"
```
Actual: `['SPY', 'SPYA', 'SPYB', 'SPYB_old', 'SPYC', 'SPYD', 'SPYG', 'SPYG1', 'SPYH', 'SPYI', 'SPYM', 'SPYM_old', 'SPYQ', 'SPYT', 'SPYU', 'SPYV', 'SPYX']` (17 symbols, no non-SPY symbols)

**4. Symbol detail with available ranges:**
```bash
curl -s "http://localhost:8100/api/v1/symbols/SPY" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['symbol'], d['active']); print(list(d['available'].keys()))"
```
Actual: `SPY True` / `['1d', '1w', '1mo', '1q']`

> SPY has daily data but no minute data loaded yet — minute granularities correctly absent from `available`.

**5. Available range values:**
```bash
curl -s "http://localhost:8100/api/v1/symbols/SPY" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); r=d['available']['1d']; print(r['start'], '->', r['end'])"
```
Actual: `1993-01-29 -> 2026-05-12`

**6. Unknown symbol returns 404:**
```bash
curl -s -w "\nHTTP %{http_code}\n" "http://localhost:8100/api/v1/symbols/FAKESYMBOL"
```
Actual: `{"error":"Symbol 'FAKESYMBOL' not found"}` / `HTTP 404`

**7. Health regression:**
```bash
curl -s http://localhost:8100/api/v1/health
```
Actual: `{"status":"ok","db":"ok"}`

**8. Unit tests (no live DB):**
```bash
uv run pytest test/unit/api_server/test_symbols.py -v
```
Actual: 10 passed (4 model + 3 list + 3 detail)

**9. Full suite regression:**
```bash
uv run pytest test/unit/api_server/ -v
```
Actual: 21 passed

**10. OpenAPI includes symbols routes:**
```bash
curl -s http://localhost:8100/openapi.json \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(sorted(d['paths']))"
```
Actual: `['/api/v1/bars/{symbol}', '/api/v1/health', '/api/v1/symbols', '/api/v1/symbols/{symbol}']`
