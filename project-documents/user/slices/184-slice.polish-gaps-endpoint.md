---
docType: slice-design
slice: polish-gaps-endpoint
project: trading
parent: user/architecture/180-slices.data-serving-api.md
dependencies: [183]
interfaces: []
dateCreated: 20260514
dateUpdated: 20260514
status: complete
effort: 1
---

# Slice Design: Polish — Gaps Endpoint, Error Handling, OpenAPI, `--workers`

## Overview

This is the final slice of initiative 180. It adds the gaps endpoint, hardens error handling, cleans up OpenAPI metadata, and adds `--workers N` to `mt serve`. No new DB classes, no schema migrations. All work is additive within the existing FastAPI app.

## Scope

**Included:**

- `src/manta_trading/api_server/routes/gaps.py` — `GET /api/v1/gaps/{symbol}`
- `src/manta_trading/api_server/models/responses.py` — add `GapRecord`, `GapsResponse`
- `src/manta_trading/api_server/app.py` — register gaps router; add global 500 handler; set `description` field
- `src/manta_trading/cli/commands/serve.py` — add `--workers N` option
- `test/unit/api_server/test_gaps.py` — unit tests via `TestClient`

**Excluded:**

- Schema migrations — `data_gaps` table exists (migration 018)
- Gap creation / daemon integration — the endpoint is read-only
- Pagination — consistent with other endpoints
- Auth, rate limiting — out of scope for all 180-series slices

---

## Technical Decisions

### Gaps Endpoint — Data Source and Column Mapping

The `data_gaps` table is the authoritative source. Schema (migration 018):

| Column | Type | Notes |
|---|---|---|
| `symbol` | TEXT | PK member |
| `granularity` | TEXT | `'daily'` or `'minute'` — NOT the API `Granularity` values |
| `gap_start` | TIMESTAMPTZ | PK member |
| `gap_end` | TIMESTAMPTZ | PK member |
| `fetch_status` | TEXT | `FetchStatus` enum values |
| `last_attempt_ts` | TIMESTAMPTZ | nullable |
| `attempt_count` | INTEGER | |

**Critical:** `granularity` in `data_gaps` uses coarse values `'daily'` and `'minute'`, not the fine-grained API tokens (`'1m'`, `'1d'`, etc.). The `?granularity` query param accepts the API `Granularity` StrEnum; the route maps it to the DB family before querying.

Mapping:

```python
_MINUTE_GRAINS = {Granularity.M1, Granularity.M5, Granularity.M15, Granularity.H1, Granularity.H4}
_DAILY_GRAINS  = {Granularity.D1, Granularity.W1, Granularity.MO1, Granularity.Q1}

_DB_GRANULARITY: dict[Granularity, str] = {g: "minute" for g in _MINUTE_GRAINS} | {g: "daily" for g in _DAILY_GRAINS}
```

If `?granularity` is omitted the query returns all gaps for the symbol across both families.

### `?start` / `?end` Windowing

`start` and `end` are ISO dates (same convention as `/bars`). The window filter is:

```sql
gap_start < %s  -- exclusive end: gaps that start before window end
AND gap_end > %s  -- exclusive start: gaps that end after window start
```

This is an overlapping-interval query — returns any gap that intersects the requested window, even if only partially. This matches the most useful semantics for an operator debugging missing data in a date range.

When `start`/`end` are omitted, no window filter is applied — returns all gaps for the symbol (and granularity if specified).

### Response Shape

```python
class GapRecord(BaseModel):
    gap_start: datetime
    gap_end: datetime
    granularity: str          # DB value: 'daily' or 'minute'
    fetch_status: str         # FetchStatus value
    attempt_count: int
    last_attempt_ts: datetime | None

class GapsResponse(BaseModel):
    symbol: str
    count: int
    gaps: list[GapRecord]
```

`granularity` in the response is the DB family string (`'daily'`/`'minute'`), not the API token — it accurately represents what the daemon tracks and avoids false precision about which sub-granularity is affected.

The endpoint does NOT return 404 for an unknown symbol — an empty gap list is a valid and correct result for a symbol with no gaps. This matches REST conventions for a collection resource.

### 500 Handler

Add a global `Exception` handler in `create_app()` alongside the existing `HTTPException` handler:

```python
@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    _logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"error": "internal server error"})
```

This replaces FastAPI's default behavior (which may leak stack traces in debug mode). The body is always sanitized — no SQL text, no traceback, no DB connection info. Full traceback is captured by `logger.exception` (which includes the `exc_info` automatically).

**Existing route audit:** `bars.py` and `symbols.py` let DB errors propagate as unhandled exceptions today. The new 500 handler catches them uniformly — no per-route changes needed.

### OpenAPI Cleanup

`app.py` currently sets `title` and `version` but no `description`. Add:

```python
app = FastAPI(
    title="Manta Trading API",
    description="Data serving API for OHLCV bars, symbol metadata, and gap status.",
    version="0.1.0",
    lifespan=lifespan,
)
```

No routes need to be suppressed from docs — the existing four endpoints are all public-facing. No `include_in_schema=False` changes required.

### `--workers N` on `mt serve`

Add a `workers` option to the `serve` CLI command:

```python
workers: int = typer.Option(
    1,
    "--workers",
    help=(
        "Number of uvicorn worker processes. Default 1. "
        "Multi-worker mode uses 'spawn', which requires the app factory "
        "to be importable from a separate process. "
        "Run the daemon in a separate terminal; slice 155 will add supervised launch."
    ),
)
```

Pass `workers=workers` to `uvicorn.run`. Single-worker default is correct — the daemon and API server are separate OS processes; no worker count coordination is needed between them.

---

## API Specification

```
GET /api/v1/gaps/{symbol}
    ?granularity=1m   # Optional. Any Granularity token. Mapped to 'minute' or 'daily' internally.
    ?start=2024-01-01 # Optional. ISO date. Window start (inclusive).
    ?end=2024-01-31   # Optional. ISO date. Window end (inclusive).
```

**Response (200):**

```json
{
  "symbol": "SPY",
  "count": 2,
  "gaps": [
    {
      "gap_start": "2024-01-15T00:00:00Z",
      "gap_end":   "2024-01-16T00:00:00Z",
      "granularity": "minute",
      "fetch_status": "UNKNOWN",
      "attempt_count": 0,
      "last_attempt_ts": null
    }
  ]
}
```

**Error cases:**

- `422` — invalid `granularity` token (FastAPI automatic via Pydantic/StrEnum)
- `500` — DB error (sanitized body; full traceback in server log)

---

## SQL

```python
# All gaps for symbol, no filter:
_ALL_GAPS_SQL = """
    SELECT gap_start, gap_end, granularity, fetch_status, attempt_count, last_attempt_ts
    FROM data_gaps
    WHERE symbol = %s
    ORDER BY gap_start
"""

# Filtered by granularity family:
_GRAN_GAPS_SQL = """
    SELECT gap_start, gap_end, granularity, fetch_status, attempt_count, last_attempt_ts
    FROM data_gaps
    WHERE symbol = %s AND granularity = %s
    ORDER BY gap_start
"""

# With window (overlapping interval query):
_WINDOWED_GAPS_SQL = """
    SELECT gap_start, gap_end, granularity, fetch_status, attempt_count, last_attempt_ts
    FROM data_gaps
    WHERE symbol = %s
      AND gap_start < %s
      AND gap_end   > %s
    ORDER BY gap_start
"""

_WINDOWED_GRAN_GAPS_SQL = """
    SELECT gap_start, gap_end, granularity, fetch_status, attempt_count, last_attempt_ts
    FROM data_gaps
    WHERE symbol = %s AND granularity = %s
      AND gap_start < %s
      AND gap_end   > %s
    ORDER BY gap_start
"""
```

Four SQL constants, each unambiguous — avoids dynamic SQL construction with conditional clauses. The route dispatches to the appropriate constant based on which params are present.

---

## Cross-Slice Dependencies and Interfaces

- **Depends on [183]**: `create_app()`, `get_db`, existing pool, lifespan, 404/custom handler
- **Terminal slice**: no downstream slice dependencies; no new interfaces exposed

---

## Success Criteria

1. `GET /api/v1/gaps/SPY` returns `{"symbol":"SPY","count":N,"gaps":[...]}` where `N >= 0`.
2. `GET /api/v1/gaps/SPY?granularity=1m` returns only gaps with `granularity="minute"`.
3. `GET /api/v1/gaps/SPY?start=2024-01-01&end=2024-01-31` returns only gaps overlapping that window.
4. `GET /api/v1/gaps/FAKESYMBOL` returns `200 {"symbol":"FAKESYMBOL","count":0,"gaps":[]}` (not 404).
5. An invalid `granularity` token returns `422`.
6. A DB error returns `500 {"error":"internal server error"}` (no SQL/traceback in body); full traceback appears in server log.
7. `mt serve --workers 2` starts with two uvicorn workers.
8. `GET /docs` shows all five endpoints with correct title and description.
9. Unit tests pass without a live DB (mock `get_db` dependency).
10. `ruff` and `pyright` report zero errors.

---

## Verification Walkthrough

**Prerequisites:** `MT_TIMESCALE_DB_URL` set to production DB (`trading`); server not running.

**1. Start server with explicit worker count:**
```bash
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading" uv run mt serve --workers 1
```
Wait for `Application startup complete.`

**2. All gaps for SPY (no filter):**
```bash
curl -s "http://localhost:8100/api/v1/gaps/SPY" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('count:', d['count'])"
```
Expected: `count: N` (any non-negative integer).

**3. Filter by granularity:**
```bash
curl -s "http://localhost:8100/api/v1/gaps/SPY?granularity=1m" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(set(g['granularity'] for g in d['gaps']))"
```
Expected: `{'minute'}` or `set()` (only minute-family gaps, no daily).

**4. Window filter:**
```bash
curl -s "http://localhost:8100/api/v1/gaps/SPY?start=2024-01-01&end=2024-12-31" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('count:', d['count'])"
```
Expected: `count: N` where all returned `gap_start` values fall within 2024.

**5. Unknown symbol returns empty list (not 404):**
```bash
curl -s -w "\nHTTP %{http_code}\n" "http://localhost:8100/api/v1/gaps/FAKESYMBOL"
```
Expected: `{"symbol":"FAKESYMBOL","count":0,"gaps":[]}` / `HTTP 200`

**6. Invalid granularity returns 422:**
```bash
curl -s -w "\nHTTP %{http_code}\n" "http://localhost:8100/api/v1/gaps/SPY?granularity=bad"
```
Expected: `HTTP 422`

**7. OpenAPI — all five endpoints visible:**
```bash
curl -s http://localhost:8100/openapi.json \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(sorted(d['paths']))"
```
Expected: `['/api/v1/bars/{symbol}', '/api/v1/gaps/{symbol}', '/api/v1/health', '/api/v1/symbols', '/api/v1/symbols/{symbol}']`

**8. OpenAPI description present:**
```bash
curl -s http://localhost:8100/openapi.json \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['info']['description'])"
```
Expected: non-empty description string.

**9. Unit tests (no live DB):**
```bash
uv run pytest test/unit/api_server/test_gaps.py -v
```
Expected: all tests pass.

**10. Full suite regression:**
```bash
uv run pytest test/unit/api_server/ -v
```
Expected: all tests pass (21 existing + new gaps tests).

**11. Health regression:**
```bash
curl -s http://localhost:8100/api/v1/health
```
Expected: `{"status":"ok","db":"ok"}`
