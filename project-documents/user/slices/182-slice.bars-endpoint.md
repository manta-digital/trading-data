---
docType: slice-design
slice: bars-endpoint
project: trading
parent: user/architecture/180-slices.data-serving-api.md
dependencies: [181]
interfaces: [183]
dateCreated: 20260513
dateUpdated: 20260513
status: complete
---

# Slice Design: Bars Endpoint

## Overview

This slice adds `GET /api/v1/bars/{symbol}` to the Data Serving API introduced
in slice 181. It is the critical path for trading-ui (which cannot import Python
directly and needs OHLCV data over HTTP) and the most complex routing logic in
the 180 initiative: minute vs. daily dispatch, `date`→`datetime` conversion, and
the async/sync bridge to the existing synchronous DB methods.

The route wraps `TimescaleMinuteDataDB.get_minute_data` and
`TimescaleDailyDataDB.get_daily_data` without adding business logic. Serialization
is JSON (default, via orjson) or msgpack (when `format=msgpack`).

## Scope

**Included:**

- `src/manta_trading/api_server/routes/bars.py` — `GET /api/v1/bars/{symbol}`
  with query params `granularity`, `start`, `end`, `adjusted`, `fmt` (aliased
  as `format`).
- `src/manta_trading/api_server/models/responses.py` — add `BarRecord` and
  `BarsResponse` (with `BarsResponse.from_dataframe` classmethod).
- `src/manta_trading/api_server/deps.py` — add `get_minute_db` and
  `get_daily_db` DI helpers.
- `src/manta_trading/api_server/app.py` — instantiate DB objects in the
  lifespan hook; register the bars router; register the 404 exception handler.
- `test/unit/api_server/test_bars.py` — unit tests via `TestClient`.

**Excluded:**

- Symbols endpoints (slice 183).
- Gaps endpoint (slice 184).
- Global 500 error handler (slice 184).
- OpenAPI doc cleanup (slice 184).
- `--workers` flag for `mt serve` (slice 184).

## Technical Decisions

### DB Instance Lifecycle

`TimescaleMinuteDataDB` and `TimescaleDailyDataDB` each take a `conninfo: str`
and create their own `ConnectionPool` internally (confirmed in both constructors).
Creating them per-request would create a new pool on every call — unacceptable.

**Decision:** instantiate both DB objects once during the lifespan hook and
store them on `app.state`:

```python
# lifespan addition (conceptual — not final code)
conninfo = str(settings.timescale_db_url)
app.state.minute_db = TimescaleMinuteDataDB(conninfo)
app.state.daily_db = TimescaleDailyDataDB(conninfo)
```

Both objects are read-only from the request path. No shared-mutation concern.
Three pools in total (API pool + minute pool + daily pool) is acceptable for a
single-user tool.

### Dependency Injection

Add two helpers to `deps.py` mirroring `get_db`:

```python
def get_minute_db(request: Request) -> TimescaleMinuteDataDB:
    return request.app.state.minute_db

def get_daily_db(request: Request) -> TimescaleDailyDataDB:
    return request.app.state.daily_db
```

Route handlers declare:
```python
minute_db: Annotated[TimescaleMinuteDataDB, Depends(get_minute_db)]
daily_db: Annotated[TimescaleDailyDataDB, Depends(get_daily_db)]
```

### Granularity Routing

`bars.py` declares `_MINUTE_GRAINS` as a module-level constant (mirrors the
private set in `timescale_minute_db.py`; imported private symbols are fragile):

```python
_MINUTE_GRAINS = frozenset({
    Granularity.M1, Granularity.M5, Granularity.M15,
    Granularity.H1, Granularity.H4,
})
```

Route logic:
- `granularity in _MINUTE_GRAINS` → call `minute_db.get_minute_data`
- otherwise → call `daily_db.get_daily_data`

`TimescaleDailyDataDB.get_daily_data` raises `ValueError` if passed a
minute-grain token. Because the route dispatches before calling, this `ValueError`
is a programming error (not a user error) and is not caught here. The global 500
handler in slice 184 will log it.

### Date → Datetime Conversion

`get_minute_data` requires `datetime` (UTC). The route accepts `date` for both
`start` and `end` (ISO date strings only; FastAPI/Pydantic auto-parses `date`
from `YYYY-MM-DD` query params and rejects time components). Conversion:

```python
from datetime import datetime, time, timezone

start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
end_dt = datetime.combine(end, time.min, tzinfo=timezone.utc)
```

`time.min` is `00:00:00`, so `end_dt` is midnight of the end date (exclusive of
that day's bars). This matches the existing CLI behavior and is consistent with
how the daemon queries the minute table.

`get_daily_data` accepts `date` directly; no conversion needed.

### Async/Sync Bridge

Both DB methods are synchronous psycopg3. Route handlers are async. Use
`asyncio.get_running_loop().run_in_executor(None, ...)` — `get_running_loop()`
is the 3.10+ idiom; the ASYNC ruff rule flags `get_event_loop()` inside async
functions.

```python
loop = asyncio.get_running_loop()
df = await loop.run_in_executor(
    None,
    lambda: minute_db.get_minute_data(
        symbol, start_dt, end_dt,
        aggregation=granularity,
        adjusted=adjusted,
    ),
)
```

The executor call is the only blocking operation in the route handler. All other
operations (model construction, serialization) are fast CPU work on the calling
coroutine, which is correct.

### `format` Query Parameter

Python's `format` is a builtin. To avoid shadowing it and to satisfy ruff's
`A002` rule (if enabled), the function parameter is named `fmt` with a Query
alias:

```python
fmt: Annotated[Literal["json", "msgpack"], Query(alias="format")] = "json"
```

The OpenAPI schema shows `format` as the query param name (correct for clients).

### 404 Handling

Both DB methods return an empty `DataFrame` for an unknown symbol or a date
range with no data — they do not raise. After the executor call, the route
checks:

```python
if df.empty:
    raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found or no data in range")
```

This covers both "symbol doesn't exist" and "symbol exists but no bars in
range" with a single 404. Distinguishing the two cases (instruments lookup vs.
data lookup) is unnecessary overhead for this slice.

FastAPI's default 404 response body is `{"detail": "..."}`. The initiative
error convention is `{"error": "..."}`. Register a 404 override handler in
`app.py`:

```python
from fastapi import HTTPException, Request
from fastapi.exception_handlers import http_exception_handler as _default_http_handler
from fastapi.responses import JSONResponse

@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if exc.status_code == 404:
        return JSONResponse(status_code=404, content={"error": str(exc.detail)})
    return await _default_http_handler(request, exc)
```

This delegates all non-404 HTTP exceptions (including 422 validation errors) to
FastAPI's default handler, preserving the automatic `{"detail": [...]}` shape
for validation errors.

### Response Serialization

**JSON (default):** Use `orjson.dumps` directly and return a `Response` with
`media_type="application/json"`. FastAPI's `ORJSONResponse` is an alias for
this. `BarsResponse.model_dump()` produces a plain dict; orjson handles
`datetime` fields natively (serializes to ISO 8601 with `Z` suffix for UTC
datetimes).

**msgpack:** Use `msgpack.packb(response.model_dump(), default=str)` to handle
`datetime` and other non-native msgpack types via `str` fallback. Return a
`Response` with `media_type="application/x-msgpack"`.

Since the route returns `Response` directly (not a Pydantic model), declare the
endpoint with `response_class=Response` and omit `response_model`. This prevents
FastAPI from attempting a second serialization pass.

### Response Models

Add to `src/manta_trading/api_server/models/responses.py`:

```python
import pandas as pd
from datetime import datetime
from manta_trading.constants import Granularity

class BarRecord(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

class BarsResponse(BaseModel):
    symbol: str
    granularity: str
    adjusted: bool
    count: int
    bars: list[BarRecord]

    @classmethod
    def from_dataframe(
        cls,
        symbol: str,
        granularity: Granularity,
        adjusted: bool,
        df: pd.DataFrame,
    ) -> "BarsResponse":
        bars = [
            BarRecord(
                timestamp=idx.to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["volume"]),
            )
            for idx, row in df.iterrows()
        ]
        return cls(
            symbol=symbol,
            granularity=str(granularity),
            adjusted=adjusted,
            count=len(bars),
            bars=bars,
        )
```

The `granularity` field is `str` (not the `Granularity` StrEnum) in the
response model so orjson serializes it directly as `"1d"` without a custom
encoder. Because `Granularity` is a `StrEnum`, `str(granularity)` produces the
token value (`"1d"`, `"1m"`, etc.).

`volume` is cast to `int` because pandas may infer float columns from queries
that return NULL-able numeric columns (none expected here, but explicit cast is
safe).

## Data Flow

```
Client
  │  GET /api/v1/bars/SPY?granularity=1d&start=2024-01-01&end=2024-01-31
  ▼
FastAPI route handler (async)
  │  parse: symbol, granularity (Granularity), start (date), end (date),
  │         adjusted (bool), fmt (Literal["json","msgpack"])
  │
  ├─ granularity in _MINUTE_GRAINS?
  │    YES → convert date→datetime (midnight UTC)
  │          run_in_executor → minute_db.get_minute_data(...)
  │    NO  → run_in_executor → daily_db.get_daily_data(...)
  │
  ├─ df.empty? → HTTPException(404)
  │
  ├─ BarsResponse.from_dataframe(symbol, granularity, adjusted, df)
  │
  ├─ fmt == "msgpack"?
  │    YES → Response(msgpack.packb(...), media_type="application/x-msgpack")
  │    NO  → Response(orjson.dumps(...), media_type="application/json")
  ▼
Client receives serialized bars
```

## Files Changed

| File | Change |
|------|--------|
| `src/manta_trading/api_server/app.py` | Add DB instance creation in lifespan; register bars router; register 404 handler |
| `src/manta_trading/api_server/deps.py` | Add `get_minute_db`, `get_daily_db` |
| `src/manta_trading/api_server/routes/bars.py` | New file — full bars route implementation |
| `src/manta_trading/api_server/models/responses.py` | Add `BarRecord`, `BarsResponse` |
| `test/unit/api_server/test_bars.py` | New file — unit tests |

## Cross-Slice Interfaces

**Consumed from slice 181:**
- `app.state.db_pool` — exists (for health; not used by bars route directly)
- `create_app()` factory — bars router registered here
- `models/responses.py` stub — `BarsResponse`, `BarRecord` comments already present
- `deps.py` — `get_minute_db`, `get_daily_db` added here

**Consumed from existing codebase:**
- `TimescaleMinuteDataDB(conninfo)` — `get_minute_data(...)`
- `TimescaleDailyDataDB(conninfo)` — `get_daily_data(...)`
- `Granularity` StrEnum from `manta_trading.constants`
- `Settings.timescale_db_url` — conninfo source for DB construction

**Provided to slice 183:**
- `models/responses.py` — `SymbolSummary`, `SymbolDetail`, etc. stubs will be added here

## Unit Tests

File: `test/unit/api_server/test_bars.py`

All tests use `TestClient` with the DB instances mocked via
`app.dependency_overrides`. No live DB required.

**Test 1 — daily bars return JSON with correct shape:**
Inject `get_daily_db` to return a mock whose `get_daily_data` returns a
3-row DataFrame with known OHLCV values. `GET /api/v1/bars/SPY?granularity=1d
&start=2024-01-01&end=2024-01-03`. Assert: 200, `count=3`, `granularity="1d"`,
`bars[0].open` matches injected value, `Content-Type: application/json`.

**Test 2 — minute bars route to minute DB with datetime conversion:**
Inject `get_minute_db` to capture the `start_time` argument and return a
non-empty DataFrame. `GET /api/v1/bars/SPY?granularity=1m&start=2024-01-01
&end=2024-01-02`. Assert: `start_time` passed to mock is
`datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)` (midnight UTC). Assert
200 with JSON response.

**Test 3 — msgpack format returns binary with correct Content-Type:**
Inject `get_daily_db` returning a non-empty DataFrame. `GET /api/v1/bars/SPY
?granularity=1d&start=2024-01-01&end=2024-01-03&format=msgpack`. Assert:
`Content-Type: application/x-msgpack`; `msgpack.unpackb(response.content)` is
a dict with `"symbol"`, `"count"`, `"bars"` keys.

**Test 4 — unknown symbol / empty result returns 404 with error shape:**
Inject `get_daily_db` returning an empty DataFrame. Assert: 404,
`response.json() == {"error": ...}` (not `{"detail": ...}`).

**Test 5 — invalid granularity returns 422:**
`GET /api/v1/bars/SPY?granularity=invalid&start=2024-01-01&end=2024-01-31`.
Assert: 422 (FastAPI Pydantic validation error — no DB call).

**Test 6 — adjusted=false passes through to DB method:**
Inject `get_daily_db` to capture the `adjusted` kwarg. `GET /api/v1/bars/SPY
?granularity=1d&start=2024-01-01&end=2024-01-03&adjusted=false`. Assert:
`adjusted=False` was forwarded to `get_daily_data`.

## Success Criteria

1. `GET /api/v1/bars/SPY?granularity=1d&start=2024-01-01&end=2024-01-31` returns
   200 JSON with `count=21` and 21 bar objects.
2. Same request with `format=msgpack` returns binary with
   `Content-Type: application/x-msgpack`; decoding with `msgpack.unpackb`
   yields the same bar count.
3. Minute granularity (`granularity=1m`) routes to the minute DB; daily
   granularity (`granularity=1d`) routes to the daily DB — verified by unit
   tests capturing which mock is called.
4. `GET /api/v1/bars/FAKESYMBOL?granularity=1d&start=2024-01-01&end=2024-01-31`
   returns 404 `{"error": "..."}`.
5. `GET /api/v1/bars/SPY?granularity=bad&start=2024-01-01&end=2024-01-31`
   returns 422.
6. `GET /api/v1/bars/SPY?granularity=1d&start=2024-01-01&end=2024-01-31
   &adjusted=false` returns unadjusted bars (close price differs from adjusted).
7. `pytest test/unit/api_server/test_bars.py` passes — all 6 tests, no live DB.
8. `ruff check src/manta_trading/api_server/` and `pyright src/manta_trading/api_server/` both report zero errors.
9. `GET /api/v1/health` continues to return 200 `{"status":"ok","db":"ok"}`
   (regression check: lifespan changes did not break the health endpoint).

## Verification Walkthrough

**Prerequisites:** `MT_TIMESCALE_DB_URL` set; server not yet running. SPY daily
data present in the `trading` DB. Minute data available for AAPL, MSFT, NVDA,
AMZN (SPY has no minute data in this DB). Note: `end` date for minute queries
is exclusive (midnight UTC), so use `end=<next day>` to include a full trading day.

**1. Start the server:**
```
uv run mt serve
```
Expected log output before `Application startup complete.`:
```
INFO manta_trading.api_server.app: API server connection pool opened
INFO manta_trading.market.timescale_minute_db: TimescaleDB connection pool initialized
INFO manta_trading.market.timescale_daily_db: TimescaleDailyDataDB connection pool initialized
INFO manta_trading.api_server.app: Minute and daily DB instances initialized
```

**2. Daily bars — JSON:**
```bash
curl -s "http://localhost:8100/api/v1/bars/SPY?granularity=1d&start=2024-01-01&end=2024-01-31" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['symbol'], d['granularity'], d['count'])"
```
Actual: `SPY 1d 21`

**3. Daily bars — msgpack:**
```bash
curl -s "http://localhost:8100/api/v1/bars/SPY?granularity=1d&start=2024-01-01&end=2024-01-31&format=msgpack" \
  | python3 -c "import sys,msgpack; d=msgpack.unpackb(sys.stdin.buffer.read(), raw=False); print(d['count'])"
```
Actual: `21`; `Content-Type: application/x-msgpack`

**4. Minute bars:**

> SPY has no minute data in this DB. Use AAPL (or MSFT/NVDA/AMZN). `end` is exclusive midnight UTC — use next calendar day.

```bash
curl -s "http://localhost:8100/api/v1/bars/AAPL?granularity=1m&start=2024-04-01&end=2024-04-02" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['granularity'], d['count'])"
```
Actual: `1m 959`

**5. Unadjusted bars:**
```bash
adj=$(curl -s "http://localhost:8100/api/v1/bars/SPY?granularity=1d&start=2024-01-02&end=2024-01-02" \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['bars'][0]['close'])")
raw=$(curl -s "http://localhost:8100/api/v1/bars/SPY?granularity=1d&start=2024-01-02&end=2024-01-02&adjusted=false" \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['bars'][0]['close'])")
echo "adjusted=$adj raw=$raw"
```
Actual: `adjusted=459.99159786713994 raw=472.65`

**6. 404 for unknown symbol:**
```bash
curl -s -w "\nHTTP %{http_code}\n" \
  "http://localhost:8100/api/v1/bars/FAKESYMBOL?granularity=1d&start=2024-01-01&end=2024-01-31"
```
Actual: `{"error":"Symbol 'FAKESYMBOL' not found or no data in range"}` and `HTTP 404`.
Note: response key is `"error"`, not `"detail"` (custom 404 handler active).

**7. 422 for invalid granularity:**
```bash
curl -s -w "\nHTTP %{http_code}\n" \
  "http://localhost:8100/api/v1/bars/SPY?granularity=bad&start=2024-01-01&end=2024-01-31"
```
Actual: FastAPI validation error body (`{"detail": [...]}`) and `HTTP 422`.

**8. Health regression check:**
```bash
curl -s http://localhost:8100/api/v1/health
```
Actual: `{"status":"ok","db":"ok"}`

**9. Unit tests (no live DB):**
```bash
uv run pytest test/unit/api_server/ -v
```
Actual: `11 passed` (3 health + 8 bars)

**10. Stop server:** `Ctrl+C` — process exits 0.
