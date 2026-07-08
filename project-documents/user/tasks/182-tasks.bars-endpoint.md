---
docType: tasks
slice: bars-endpoint
project: trading
lld: user/slices/182-slice.bars-endpoint.md
dependencies: [181]
projectState: >
  Slice 181 complete and merged to main. FastAPI skeleton is live:
  mt serve starts, pool opens, GET /api/v1/health returns {"status":"ok","db":"ok"}.
  api_server/ package exists with app.py, deps.py, routes/health.py,
  models/responses.py (HealthResponse + stub comment for BarsResponse/BarRecord).
  Unit tests pass (test_health.py, 3 tests). No bar endpoints yet.
dateCreated: 20260513
dateUpdated: 20260513
status: complete
---

## Context Summary

- Adding `GET /api/v1/bars/{symbol}` to the Data Serving API (initiative 180).
- Wraps existing synchronous `TimescaleMinuteDataDB.get_minute_data` and
  `TimescaleDailyDataDB.get_daily_data` behind an async FastAPI route using
  `run_in_executor` as the async/sync bridge.
- Both DB classes take `conninfo: str` and own their own pool — they are
  instantiated once in the app lifespan hook (not per-request) and stored on
  `app.state`.
- Routing: `_MINUTE_GRAINS = frozenset({M1, M5, M15, H1, H4})` declared in
  `bars.py`; all other granularities route to the daily DB.
- Response: JSON via orjson (default) or msgpack when `format=msgpack`.
- 404 shape must be `{"error": "..."}` — requires a custom exception handler
  in `app.py` that rewrites FastAPI's default `{"detail": "..."}` for 404s.
- Design reference: `user/slices/182-slice.bars-endpoint.md`.
- Next slice: 183 (symbols endpoints + available ranges view).

---

- [x] **T1 — Branch setup**
  - [x] Confirm current branch is `main` (`git status`).
  - [x] Create and checkout `182-slice.bars-endpoint` from main:
        `git checkout -b 182-slice.bars-endpoint`
  - [x] Success: `git branch --show-current` prints `182-slice.bars-endpoint`.

- [x] **T2 — Pydantic response models: `BarRecord` and `BarsResponse`**
  - [x] Open `src/manta_trading/api_server/models/responses.py`. Replace the
    `# Slice 182: BarsResponse, BarRecord` stub comment with the two models.
  - [x] `BarRecord(BaseModel)`: fields `timestamp: datetime`, `open: float`,
        `high: float`, `low: float`, `close: float`, `volume: int`.
  - [x] `BarsResponse(BaseModel)`: fields `symbol: str`, `granularity: str`,
        `adjusted: bool`, `count: int`, `bars: list[BarRecord]`.
  - [x] `BarsResponse.from_dataframe(cls, symbol, granularity, adjusted, df)`
        classmethod: iterates `df.iterrows()`, casts `open/high/low/close` to
        `float`, `volume` to `int`, index row to `datetime` via
        `idx.to_pydatetime()`. Sets `count=len(bars)`.
        See slice design "Response Models" section for the full signature.
  - [x] `granularity` field is `str` (not `Granularity`) so orjson serializes
        it directly as the string token (`"1d"`, `"1m"`, etc.).
  - [x] `from __future__ import annotations` present at top of file.
        Import `pandas as pd` and `Granularity` only inside the classmethod
        signature annotation (or at module level — pyright must be happy).
  - [x] Success: `python -c "from manta_trading.api_server.models.responses
        import BarsResponse, BarRecord; print('ok')"` exits 0.

- [x] **T3 — Model unit tests: `BarsResponse.from_dataframe`**
  - [x] Create `test/unit/api_server/test_bars.py` (new file).
  - [x] Add `_make_ohlcv_df(n)` helper at module level: builds a pandas
        DataFrame with `n` rows, `DatetimeIndex` in UTC (1-minute spacing
        from `2024-01-02 09:30 UTC`), columns `open/high/low/close` (float),
        `volume` (int). Mirrors the structure returned by the real DB methods.
  - [x] `test_from_dataframe_count`: call `BarsResponse.from_dataframe("SPY",
        Granularity.D1, True, _make_ohlcv_df(3))`. Assert `count == 3`,
        `len(bars) == 3`, `symbol == "SPY"`, `granularity == "1d"`.
  - [x] `test_from_dataframe_field_types`: assert `bars[0].volume` is `int`,
        `bars[0].open` is `float`, `bars[0].timestamp` is a UTC-aware
        `datetime`.
  - [x] Success: `uv run pytest test/unit/api_server/test_bars.py::test_from_dataframe_count
        test/unit/api_server/test_bars.py::test_from_dataframe_field_types -v`
        passes without a live DB.

- [x] **T4 — `deps.py`: add `get_minute_db` and `get_daily_db`**
  - [x] Open `src/manta_trading/api_server/deps.py`. Add two helpers below `get_db`.
  - [x] `get_minute_db(request: Request) -> TimescaleMinuteDataDB`:
        returns `request.app.state.minute_db`.
  - [x] `get_daily_db(request: Request) -> TimescaleDailyDataDB`:
        returns `request.app.state.daily_db`.
  - [x] Imports: `TimescaleMinuteDataDB` from
        `manta_trading.market.timescale_minute_db`;
        `TimescaleDailyDataDB` from
        `manta_trading.market.timescale_daily_db`.
  - [x] Success: `python -c "from manta_trading.api_server.deps import
        get_minute_db, get_daily_db; print('ok')"` exits 0.

- [x] **T5 — `app.py` lifespan: instantiate DB objects**
  - [x] Open `src/manta_trading/api_server/app.py`. Add DB instantiation inside
    the existing `lifespan` async context manager, immediately after the pool
    is created and stored on `app.state`.
  - [x] Import `TimescaleMinuteDataDB` and `TimescaleDailyDataDB` at the top
        of `app.py`.
  - [x] In lifespan, after `app.state.db_pool = pool`: extract
        `conninfo = str(settings.timescale_db_url)` and assign:
        `app.state.minute_db = TimescaleMinuteDataDB(conninfo)`
        `app.state.daily_db = TimescaleDailyDataDB(conninfo)`.
  - [x] Both assignments go inside the `try` block (before `yield`) so the
        lifespan `finally` block continues to close the API pool on shutdown.
        The DB objects manage their own pool lifecycle.
  - [x] Log one INFO line after both objects are created:
        `_logger.info("Minute and daily DB instances initialized")`.
  - [x] Success: running `uv run mt serve` (with `MT_TIMESCALE_DB_URL` set)
        shows both the existing pool-opened log line and the new DB init log
        line before `Application startup complete.`.

- [x] **T6 — Test infrastructure: `test_app` fixture for bars tests**
  - [x] In `test/unit/api_server/test_bars.py` (created in T3), add the
    `test_app` fixture used by all route tests below.
  - [x] `test_app` fixture (function scope): calls `create_app()`, sets
        `app.state.db_pool = MagicMock(name="sentinel_pool")`,
        `app.state.minute_db = MagicMock(spec=TimescaleMinuteDataDB)`,
        `app.state.daily_db = MagicMock(spec=TimescaleDailyDataDB)`.
        Returns the configured `FastAPI` instance.
  - [x] The `test_app` fixture does NOT enter `TestClient` as a context
        manager — lifespan must not fire (same pattern as `test_health.py`).
  - [x] Each test function that needs specific mock behavior configures
        `test_app.state.minute_db.get_minute_data.return_value` or
        `test_app.state.daily_db.get_daily_data.return_value` directly.
  - [x] Success: `uv run pytest test/unit/api_server/test_bars.py -v --collect-only`
        shows the two model tests from T3 plus the fixture is recognized
        (no import errors).

- [x] **T7 — `routes/bars.py`: route handler**
  - [x] Create `src/manta_trading/api_server/routes/bars.py`.
  - [x] Module-level constant:
        `_MINUTE_GRAINS = frozenset({Granularity.M1, Granularity.M5,
        Granularity.M15, Granularity.H1, Granularity.H4})`.
        Import `Granularity` from `manta_trading.constants`.
  - [x] `router = APIRouter()` at module level.
  - [x] Route signature:
        ```
        @router.get("/api/v1/bars/{symbol}", response_class=Response)
        async def get_bars(
            symbol: str,
            granularity: Granularity,
            start: date,
            end: date,
            adjusted: bool = True,
            fmt: Annotated[Literal["json","msgpack"], Query(alias="format")] = "json",
            minute_db: Annotated[TimescaleMinuteDataDB, Depends(get_minute_db)] = ...,
            daily_db: Annotated[TimescaleDailyDataDB, Depends(get_daily_db)] = ...,
        ) -> Response
        ```
        Use `response_class=Response` (not `response_model`) since the handler
        returns `Response` directly for both JSON and msgpack branches.
  - [x] Routing logic: if `granularity in _MINUTE_GRAINS` convert `start`/`end`
        to `datetime` at midnight UTC (`datetime.combine(d, time.min,
        tzinfo=timezone.utc)`) and call `minute_db.get_minute_data` via
        `run_in_executor`. Otherwise call `daily_db.get_daily_data` via
        `run_in_executor`. Use `asyncio.get_running_loop()` (not
        `get_event_loop()`). See slice design "Async/Sync Bridge" section.
  - [x] After executor call: if `df.empty` raise
        `HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found
        or no data in range")`.
  - [x] Build `response = BarsResponse.from_dataframe(symbol, granularity,
        adjusted, df)`.
  - [x] If `fmt == "msgpack"`: return `Response(content=msgpack.packb(
        response.model_dump(), default=str),
        media_type="application/x-msgpack")`.
  - [x] Else: return `Response(content=orjson.dumps(response.model_dump()),
        media_type="application/json")`.
  - [x] Imports: `asyncio`, `datetime`, `time`, `timezone` from stdlib;
        `Annotated`, `Literal` from `typing`; `date` from `datetime`; `msgpack`,
        `orjson`; FastAPI `APIRouter`, `Depends`, `HTTPException`, `Query`,
        `Request`, `Response`; deps; models; Granularity.
  - [x] Success: `python -c "from manta_trading.api_server.routes.bars import
        router; print('ok')"` exits 0.

- [x] **T8 — `app.py`: register bars router and 404 handler**
  - [x] Open `src/manta_trading/api_server/app.py`.
  - [x] Import `bars_router` from `manta_trading.api_server.routes.bars` and
        the FastAPI exception-handler imports needed for the 404 handler.
        Import `http_exception_handler as _default_http_handler` from
        `fastapi.exception_handlers`.
  - [x] In `create_app()`, add `app.include_router(bars_router)` after the
        existing `app.include_router(health_router)` line.
  - [x] Register the 404 handler on the app instance:
        ```python
        @app.exception_handler(HTTPException)
        async def _custom_http_exception_handler(
            request: Request, exc: HTTPException
        ) -> JSONResponse:
            if exc.status_code == 404:
                return JSONResponse(status_code=404,
                                    content={"error": str(exc.detail)})
            return await _default_http_handler(request, exc)
        ```
  - [x] Import `JSONResponse` from `fastapi.responses` at top of file.
  - [x] Success: `curl -s http://localhost:8100/openapi.json | python3 -c
        "import sys,json; print(list(json.load(sys.stdin)['paths']))"` (with
        server running) shows both `/api/v1/health` and `/api/v1/bars/{symbol}`.

- [x] **T9 — Route tests (all six scenarios)**
  - [x] Add six test functions to `test/unit/api_server/test_bars.py`. Each uses
    `TestClient(test_app)` (no context manager) and configures the relevant
    mock before making the request.
  - [x] `test_daily_bars_json`: configure `test_app.state.daily_db
        .get_daily_data.return_value` to return `_make_ohlcv_df(3)`. GET
        `?granularity=1d&start=2024-01-01&end=2024-01-03`. Assert: 200,
        `Content-Type: application/json`, `count==3`, `granularity=="1d"`,
        `symbol=="SPY"`, `bars[0]` has all five OHLCV fields.
  - [x] `test_minute_routing_and_datetime_conversion`: configure `test_app
        .state.minute_db.get_minute_data.return_value` to return
        `_make_ohlcv_df(2)`. GET `?granularity=1m&start=2024-01-01
        &end=2024-01-02`. Assert: `get_minute_data` was called (not
        `get_daily_data`). Capture the `start_time` kwarg passed and assert
        it equals `datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)`.
  - [x] `test_msgpack_format`: configure daily DB mock to return
        `_make_ohlcv_df(2)`. GET with `&format=msgpack`. Assert:
        `Content-Type: application/x-msgpack`;
        `msgpack.unpackb(response.content, raw=False)["count"] == 2`.
  - [x] `test_empty_result_returns_404`: configure daily DB mock to return an
        empty DataFrame (`pd.DataFrame()`). GET `?granularity=1d
        &start=2024-01-01&end=2024-01-03`. Assert: 404,
        `response.json() == {"error": ...}` (key is `"error"`, not
        `"detail"`).
  - [x] `test_invalid_granularity_returns_422`: GET `?granularity=bad
        &start=2024-01-01&end=2024-01-03`. No mock configuration needed
        (FastAPI rejects before the handler runs). Assert: 422.
  - [x] `test_adjusted_false_forwarded`: configure daily DB mock. GET with
        `&adjusted=false`. Capture the `adjusted` kwarg passed to
        `get_daily_data`. Assert `adjusted is False`.
  - [x] Success: `uv run pytest test/unit/api_server/test_bars.py -v`
        prints `8 passed` (2 model tests from T3 + 6 route tests).

- [x] **T10 — Health endpoint regression check**
  - [x] Run `uv run pytest test/unit/api_server/test_health.py -v`.
  - [x] Assert: `3 passed`. The 404 handler registration and bars router
        addition must not break the existing health tests.
  - [x] Run `uv run pytest test/unit/api_server/ -v`.
  - [x] Assert: `11 passed` total (3 health + 8 bars).

- [x] **T11 — Static analysis**
  - [x] Run `uv run ruff check src/manta_trading/api_server/`.
        Assert: zero errors.
  - [x] Run `uv run pyright src/manta_trading/api_server/`.
        Assert: zero errors. Fix any type errors before continuing.
  - [x] Success: both tools exit 0.

- [x] **T12 — Integration verification (live server)**
  - Start `uv run mt serve` in a separate terminal (requires
    `MT_TIMESCALE_DB_URL` set and the `trading` DB reachable).
  - [x] `curl -s "http://localhost:8100/api/v1/bars/SPY?granularity=1d
        &start=2024-01-01&end=2024-01-31"` returns 200 JSON with
        `count==21` and 21 bar objects.
  - [x] Same request with `&format=msgpack` returns binary with
        `Content-Type: application/x-msgpack`. Decode with
        `msgpack.unpackb` and assert count matches.
  - [x] `?granularity=1m&start=2024-04-01&end=2024-04-01` (or any date
        with minute data) returns 200 with `granularity=="1m"` and
        `count > 0`.
  - [x] `?granularity=1d&start=2024-01-02&end=2024-01-02&adjusted=false`
        close price differs from the adjusted version of the same request.
  - [x] `GET /api/v1/bars/FAKESYMBOL?granularity=1d&start=2024-01-01
        &end=2024-01-31` returns 404 `{"error":"..."}`.
  - [x] `GET /api/v1/bars/SPY?granularity=bad&start=2024-01-01
        &end=2024-01-31` returns 422.
  - [x] `curl -s http://localhost:8100/api/v1/health` still returns
        `{"status":"ok","db":"ok"}` (regression).
  - [x] Stop server (`Ctrl+C`); process exits 0.

- [x] **T13 — Commit**
  - [x] Stage changed/created files:
        `src/manta_trading/api_server/app.py`,
        `src/manta_trading/api_server/deps.py`,
        `src/manta_trading/api_server/routes/bars.py`,
        `src/manta_trading/api_server/models/responses.py`,
        `test/unit/api_server/test_bars.py`.
  - [x] Commit from project root with message:
        `feat: add GET /api/v1/bars endpoint with JSON and msgpack support`
  - [x] Success: `git log --oneline -1` shows the new commit.
