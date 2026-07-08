---
docType: tasks
slice: symbols-endpoints-available-ranges-view
project: trading
lld: user/slices/183-slice.symbols-endpoints-available-ranges-view.md
dependencies: [182]
projectState: >
  Slice 182 complete and merged to main. GET /api/v1/bars/{symbol} live.
  api_server/ package: app.py (lifespan with minute_db/daily_db on app.state,
  custom 404 handler), deps.py (get_db, get_minute_db, get_daily_db),
  routes/health.py, routes/bars.py, models/responses.py (HealthResponse,
  BarRecord, BarsResponse). 11 unit tests passing. No symbol endpoints yet.
dateCreated: 20260514
dateUpdated: 20260513
status: complete
---

## Context Summary

- Adding `GET /api/v1/symbols` (list with optional prefix search) and
  `GET /api/v1/symbols/{symbol}` (metadata + available ranges) to the API.
- No materialized view — available ranges computed lazily at request time via
  two indexed queries: `MIN/MAX` on `minute_5min_ohlcv` (proxy for all minute
  granularities) and `MIN/MAX` on `daily_ohlcv` (proxy for all daily
  granularities). Both are index seeks — sub-millisecond at single-symbol scope.
- Both endpoints use raw psycopg connections from the existing pool (`get_db`).
  No new DB class instances on `app.state`.
- `instruments` table columns: `symbol`, `eodhd_exchange`, `eodhd_type`,
  `asset_class`, `active`. No human-readable name field — omitted from response.
- Design reference: `user/slices/183-slice.symbols-endpoints-available-ranges-view.md`.
- Next slice: 184 (gaps endpoint + polish).

---

- [x] **T1 — Branch setup**
  - [x] Confirm current branch is `main`.
  - [x] Create and checkout `183-slice.symbols-endpoints-available-ranges-view`:
        `git checkout -b 183-slice.symbols-endpoints-available-ranges-view`
  - [x] Success: `git branch --show-current` prints the new branch name.

- [x] **T2 — Response models: `AvailableRange`, `SymbolSummary`, `SymbolsResponse`, `SymbolDetail`**
  - Open `src/manta_trading/api_server/models/responses.py`. Add four models
    after `BarsResponse`.
  - [x] `AvailableRange(BaseModel)`: fields `start: date`, `end: date`.
        Import `date` from `datetime`.
  - [x] `SymbolSummary(BaseModel)`: fields `symbol: str`, `exchange: str | None`,
        `type: str | None`, `asset_class: str | None`, `active: bool`.
  - [x] `SymbolsResponse(BaseModel)`: fields `symbols: list[SymbolSummary]`,
        `count: int`.
  - [x] `SymbolDetail(BaseModel)`: fields `symbol: str`, `exchange: str | None`,
        `type: str | None`, `asset_class: str | None`, `active: bool`,
        `available: dict[str, AvailableRange]`.
  - [x] Success: `python -c "from manta_trading.api_server.models.responses
        import AvailableRange, SymbolSummary, SymbolsResponse, SymbolDetail;
        print('ok')"` exits 0.

- [x] **T3 — Model unit tests**
  - Create `test/unit/api_server/test_symbols.py`.
  - [x] `test_symbol_summary_nullable_fields`: construct `SymbolSummary` with
        `exchange=None`, `type=None`, `asset_class=None`. Assert serializes to
        JSON with null values (not omitted).
  - [x] `test_symbols_response_count`: construct `SymbolsResponse` with 2
        `SymbolSummary` items. Assert `count == 2` and `len(symbols) == 2`.
  - [x] `test_symbol_detail_available_empty`: construct `SymbolDetail` with
        `available={}`. Assert `available == {}`.
  - [x] `test_available_range_fields`: construct `AvailableRange(start=date(2024,1,1),
        end=date(2024,12,31))`. Assert `start` and `end` are `date` instances.
  - [x] Success: `uv run pytest test/unit/api_server/test_symbols.py
        -k "test_symbol" -v` passes (4 tests, no live DB).

- [x] **T4 — `routes/symbols.py`: list endpoint**
  - Create `src/manta_trading/api_server/routes/symbols.py`.
  - [x] `router = APIRouter()` at module level.
  - [x] `GET /api/v1/symbols` handler signature:
        ```python
        @router.get("/api/v1/symbols")
        async def list_symbols(
            search: str | None = None,
            db: Annotated[psycopg.Connection[Any], Depends(get_db)],
        ) -> SymbolsResponse
        ```
  - [x] SQL query (parameterized):
        ```sql
        SELECT symbol, eodhd_exchange, eodhd_type, asset_class, active
        FROM instruments
        WHERE ($1::text IS NULL OR symbol ILIKE $1 || '%')
        ORDER BY symbol
        ```
        Pass `search` as `$1` (or `None` to return all).
  - [x] Build `list[SymbolSummary]` from cursor rows. Return
        `SymbolsResponse(symbols=..., count=len(symbols))`.
  - [x] Run query via `run_in_executor` (blocking psycopg call in async handler).
  - [x] Success: `python -c "from manta_trading.api_server.routes.symbols
        import router; print('ok')"` exits 0.

- [x] **T5 — `routes/symbols.py`: detail endpoint**
  - Add to `src/manta_trading/api_server/routes/symbols.py`.
  - [x] `GET /api/v1/symbols/{symbol}` handler signature:
        ```python
        @router.get("/api/v1/symbols/{symbol}")
        async def get_symbol(
            symbol: str,
            db: Annotated[psycopg.Connection[Any], Depends(get_db)],
        ) -> SymbolDetail
        ```
  - [x] Phase 1 (instrument lookup, via executor):
        `SELECT symbol, eodhd_exchange, eodhd_type, asset_class, active
        FROM instruments WHERE symbol = $1`
        If no row, raise `HTTPException(status_code=404,
        detail=f"Symbol '{symbol}' not found")`.
  - [x] Phase 2 (concurrent range queries via `asyncio.gather` + executor):
        - Query 1: `SELECT MIN(time_bucket)::date, MAX(time_bucket)::date
          FROM minute_5min_ohlcv WHERE symbol = $1`
        - Query 2: `SELECT MIN(time)::date, MAX(time)::date
          FROM daily_ohlcv WHERE symbol = $1`
        Both return `(min_date, max_date)` or `(None, None)` if no data.
  - [x] Build `available` dict: if minute query returns non-NULL, add
        `M1/M5/M15/H1/H4` all pointing to same `AvailableRange`. If daily
        query returns non-NULL, add `D1/W1/MO1/Q1` same range.
        Use `str(Granularity.X)` as dict keys.
  - [x] Return `SymbolDetail(symbol=..., exchange=..., type=...,
        asset_class=..., active=..., available=available)`.
  - [x] Success: import check passes (same as T4).

- [x] **T6 — Route tests: list endpoint**
  - Add to `test/unit/api_server/test_symbols.py`. Use `test_app` fixture
    (same pattern as `test_bars.py` — create app, mock `get_db` dependency).
  - [x] `test_app` fixture (function scope): calls `create_app()`, sets
        `app.state.db_pool = MagicMock(name="sentinel_pool")`. Returns app.
        Override `get_db` per-test as needed.
  - [x] `test_list_symbols_no_filter`: override `get_db` to return a mock
        connection whose `execute().fetchall()` returns 2 fake rows. Assert
        response 200, `count == 2`, `len(symbols) == 2`.
  - [x] `test_list_symbols_search_filter`: same mock, but assert the SQL
        receives the `search` value (capture the `execute` call args and
        assert `search` param is passed correctly).
  - [x] `test_list_symbols_empty`: mock returns `[]`. Assert 200, `count == 0`,
        `symbols == []`.
  - [x] Success: `uv run pytest test/unit/api_server/test_symbols.py
        -k "test_list" -v` passes (3 tests).

- [x] **T7 — Route tests: detail endpoint**
  - Add to `test/unit/api_server/test_symbols.py`.
  - [x] `test_symbol_detail_with_both_ranges`: mock `get_db` connection —
        first `execute` returns instrument row; second returns minute range
        `(date(2024,1,1), date(2026,1,1))`; third returns daily range
        `(date(2000,1,1), date(2026,1,1))`. Assert 200, `available` has
        both `"1m"` and `"1d"` keys.
  - [x] `test_symbol_detail_daily_only`: minute query returns `(None, None)`.
        Assert `available` contains `"1d"` but not `"1m"`.
  - [x] `test_symbol_detail_not_found`: instrument query returns no row.
        Assert 404, `response.json()` has `"error"` key (not `"detail"`).
        The `_custom_http_exception_handler` from slice 182 transforms all
        404 `HTTPException(detail=...)` responses to `{"error": ...}` —
        this is why the test asserts `"error"` even though T5 raises
        `HTTPException` with a `detail` field.
  - [x] Success: `uv run pytest test/unit/api_server/test_symbols.py
        -k "test_symbol_detail" -v` passes (3 tests).

- [x] **T8 — `app.py`: register symbols router**
  - Open `src/manta_trading/api_server/app.py`.
  - [x] Import `symbols_router` from `manta_trading.api_server.routes.symbols`.
  - [x] In `create_app()`, add `app.include_router(symbols_router)` after
        `app.include_router(bars_router)`.
  - [x] Success: `python -c "from manta_trading.api_server.app import
        create_app; app = create_app(); paths = {getattr(r,'path',None)
        for r in app.routes}; assert '/api/v1/symbols' in paths; print('ok')"`
        exits 0.

- [x] **T9 — Full unit suite regression**
  - [x] Run `uv run pytest test/unit/api_server/ -v`.
  - [x] Assert: all pass. Count = 11 (existing) + 4 model tests + 3 list
        tests + 3 detail tests = 21 total.

- [x] **T10 — Static analysis**
  - [x] Run `uv run ruff check src/manta_trading/api_server/`. Assert 0 errors.
  - [x] Run `uv tool run pyright --pythonpath .venv/bin/python3
        src/manta_trading/api_server/`. Assert 0 errors.
  - [x] Success: both tools exit 0.

**Commit:** `feat: add GET /api/v1/symbols list and detail endpoints`

- [x] **T11 — Integration verification (live server)**
  - Start `uv run mt serve` (requires `MT_TIMESCALE_DB_URL` set).
  - [x] `curl -s "http://localhost:8100/api/v1/symbols"` returns 200 JSON
        with `count > 0` and `symbols` array.
  - [x] `curl -s "http://localhost:8100/api/v1/symbols?search=SPY"` returns
        only SPY-prefixed symbols.
  - [x] `curl -s "http://localhost:8100/api/v1/symbols/SPY"` returns 200 with
        `available` containing at least `"1d"` key with `start`/`end` dates.
  - [x] `curl -s "http://localhost:8100/api/v1/symbols/FAKESYMBOL"` returns
        404 `{"error": "..."}`.
  - [x] `curl -s "http://localhost:8100/api/v1/health"` returns
        `{"status":"ok","db":"ok"}` (regression).
  - [x] OpenAPI schema lists `/api/v1/symbols` and `/api/v1/symbols/{symbol}`.
  - [x] Stop server cleanly.

- [x] **T12 — Commit**
  - [x] Stage: `src/manta_trading/api_server/app.py`,
        `src/manta_trading/api_server/routes/symbols.py`,
        `src/manta_trading/api_server/models/responses.py`,
        `test/unit/api_server/test_symbols.py`.
  - [x] Commit: `feat: add GET /api/v1/symbols list and detail endpoints`
  - [x] Success: `git log --oneline -1` shows the new commit.
