---
docType: tasks
slice: polish-gaps-endpoint
project: trading
lld: user/slices/184-slice.polish-gaps-endpoint.md
dependencies: [183]
projectState: >
  Slice 183 complete and merged to main. GET /api/v1/symbols and
  GET /api/v1/symbols/{symbol} live. api_server/ package: app.py
  (create_app with 404 handler, pool, minute_db/daily_db on app.state),
  deps.py (get_db), routes/health.py, routes/bars.py, routes/symbols.py,
  models/responses.py (HealthResponse, BarRecord, BarsResponse,
  AvailableRange, SymbolSummary, SymbolsResponse, SymbolDetail).
  21 unit tests passing. ruff + pyright clean. No gaps endpoint yet,
  no global 500 handler, no --workers option on mt serve.
dateCreated: 20260514
dateUpdated: 20260514
status: complete
---

## Context Summary

- Final slice of initiative 180. Four independent additions to the existing API.
- `GET /api/v1/gaps/{symbol}` — read from `data_gaps` table. Granularity param
  maps API tokens (`1m`, `1d`) to DB families (`'minute'`, `'daily'`). Empty
  result is 200, not 404. Window query is overlapping-interval (gap overlaps
  the window, not fully contained).
- Global 500 handler in `create_app()` — catches unhandled exceptions, logs
  full traceback, returns `{"error": "internal server error"}`.
- OpenAPI: add `description` field to `FastAPI(...)`.
- `mt serve --workers N`: pass through to `uvicorn.run`.
- Design reference: `user/slices/184-slice.polish-gaps-endpoint.md`.

---

- [x] **T1 — Branch setup**
  - [x] Confirm current branch is `main`: `git branch --show-current`
  - [x] Create and checkout branch:
        `git checkout -b 184-slice.polish-gaps-endpoint`
  - [x] Success: `git branch --show-current` prints
        `184-slice.polish-gaps-endpoint`.

- [x] **T2 — Response models: `GapRecord`, `GapsResponse`**
  - Open `src/manta_trading/api_server/models/responses.py`.
  - [x] Add `GapRecord(BaseModel)` after `SymbolDetail`:
        fields `gap_start: datetime`, `gap_end: datetime`,
        `granularity: str`, `fetch_status: str`,
        `attempt_count: int`, `last_attempt_ts: datetime | None`.
        Import `datetime` (already present via `date`; confirm `datetime`
        is also imported from the `datetime` module).
  - [x] Add `GapsResponse(BaseModel)`:
        fields `symbol: str`, `count: int`, `gaps: list[GapRecord]`.
  - [x] Success: `python -c "from manta_trading.api_server.models.responses
        import GapRecord, GapsResponse; print('ok')"` exits 0.

- [x] **T3 — Model unit tests**
  - Create `test/unit/api_server/test_gaps.py`.
  - [x] `test_gap_record_nullable_last_attempt`: construct `GapRecord` with
        `last_attempt_ts=None`. Assert field is `None` in serialized output.
  - [x] `test_gaps_response_empty`: construct `GapsResponse(symbol="X",
        count=0, gaps=[])`. Assert `count == 0` and `gaps == []`.
  - [x] `test_gaps_response_count_matches`: construct `GapsResponse` with
        two `GapRecord` items and `count=2`. Assert `len(gaps) == count`.
  - [x] Success: `uv run pytest test/unit/api_server/test_gaps.py
        -k "test_gap" -v` passes (3 tests, no live DB).

- [x] **T4 — `routes/gaps.py`: granularity mapping constants**
  - Create `src/manta_trading/api_server/routes/gaps.py`.
  - [x] `router = APIRouter()` at module level.
  - [x] Define `_MINUTE_GRAINS` and `_DAILY_GRAINS` sets (same boundary as
        `bars.py` and `symbols.py`) using `Granularity` from
        `manta_trading.constants`.
  - [x] Define `_DB_GRANULARITY: dict[Granularity, str]` mapping each
        `Granularity` member to `"minute"` or `"daily"`.
  - [x] Define four SQL constants (no dynamic SQL):
        `_ALL_GAPS_SQL`, `_GRAN_GAPS_SQL`, `_WINDOWED_GAPS_SQL`,
        `_WINDOWED_GRAN_GAPS_SQL`.
        See slice design for exact SQL. All use `%s` placeholders.
  - [x] Success: `python -c "from manta_trading.api_server.routes.gaps
        import router; print('ok')"` exits 0.

- [x] **T5 — `routes/gaps.py`: route handler**
  - [x] `GET /api/v1/gaps/{symbol}` handler signature:
        ```python
        @router.get("/api/v1/gaps/{symbol}")
        async def get_gaps(
            symbol: str,
            granularity: Granularity | None = None,
            start: date | None = None,
            end: date | None = None,
            db: Annotated[psycopg.Connection[Any], Depends(get_db)] = None,
        ) -> GapsResponse
        ```
  - [x] Resolve DB granularity string: if `granularity` is provided, look up
        `_DB_GRANULARITY[granularity]`; else `None`.
  - [x] Dispatch to the correct SQL constant based on whether `db_gran` and
        `start`/`end` are present. Convert `start`/`end` date → datetime at
        midnight UTC before passing as params.
  - [x] Execute via `loop.run_in_executor(None, _query)` (same async bridge
        pattern as `bars.py` and `symbols.py`).
  - [x] Build `list[GapRecord]` from cursor rows (columns in order:
        `gap_start`, `gap_end`, `granularity`, `fetch_status`,
        `attempt_count`, `last_attempt_ts`).
  - [x] Return `GapsResponse(symbol=symbol, count=len(gaps), gaps=gaps)`.
        Never raise 404 — empty list is valid.
  - [x] Success: module imports cleanly; `pyright` reports no errors on file.

- [x] **T6 — Route handler unit tests**
  - Add to `test/unit/api_server/test_gaps.py`.
  - [x] Fixture: mock `get_db` to return a connection whose `execute` returns
        a cursor with `fetchall()` returning a fixed list of gap rows.
  - [x] `test_gaps_no_filter`: call `GET /api/v1/gaps/SPY` with no params.
        Assert response 200, `symbol == "SPY"`, `count` matches row count.
  - [x] `test_gaps_granularity_filter`: call with `?granularity=1m`.
        Capture SQL executed; assert it contains `granularity = %s` and
        the param value is `"minute"`.
  - [x] `test_gaps_window_filter`: call with `?start=2024-01-01&end=2024-01-31`.
        Assert SQL used contains `gap_start <` and `gap_end >`.
  - [x] `test_gaps_unknown_symbol_returns_200`: mock returns 0 rows.
        Assert `200` with `{"symbol":"FAKE","count":0,"gaps":[]}`.
  - [x] `test_gaps_invalid_granularity`: call with `?granularity=bad`.
        Assert `422`.
  - [x] Success: `uv run pytest test/unit/api_server/test_gaps.py -v`
        passes (8 tests total).

- [x] **T7 — Register gaps router in `app.py`**
  - Open `src/manta_trading/api_server/app.py`.
  - [x] Add import: `from manta_trading.api_server.routes.gaps import
        router as gaps_router`.
  - [x] Add `app.include_router(gaps_router)` after `symbols_router`.
  - [x] Success: `python -c "from manta_trading.api_server.app import
        create_app; app = create_app(); print([r.path for r in app.routes
        if hasattr(r,'path')])"` includes `/api/v1/gaps/{symbol}`.

- [x] **T8 — Global 500 exception handler**
  - In `src/manta_trading/api_server/app.py`, inside `create_app()`, after
    the `HTTPException` handler:
  - [x] Add `@app.exception_handler(Exception)` handler that calls
        `_logger.exception(...)` with method and path, then returns
        `JSONResponse(status_code=500, content={"error": "internal server error"})`.
  - [x] Confirm no SQL text, no traceback, no DB info appears in the JSON body.
  - [x] Unit test — add `test_500_handler_sanitizes_body` to
        `test/unit/api_server/test_gaps.py` (or a new `test_app.py`):
        inject a route that raises `RuntimeError("secret sql detail")`;
        assert response is `500` with body `{"error": "internal server error"}`.
  - [x] Success: test passes; `pyright` reports no errors on `app.py`.

- [x] **T9 — OpenAPI description**
  - In `src/manta_trading/api_server/app.py`, in the `FastAPI(...)` constructor:
  - [x] Add `description="Data serving API for OHLCV bars, symbol metadata,
        and gap status."`.
  - [x] Success: `curl -s http://localhost:8100/openapi.json | python3 -c
        "import sys,json; d=json.load(sys.stdin); i=d['info']; print(i['title'], '|', i['description'])"
        ` prints `Manta Trading API | Data serving API for OHLCV bars, symbol metadata, and gap status.`
        (requires running server).

- [x] **T10 — `--workers N` on `mt serve`**
  - Open `src/manta_trading/cli/commands/serve.py`.
  - [x] Add `workers: int = typer.Option(1, "--workers", help="...")` parameter.
        Help text: `"Number of uvicorn worker processes (default 1). "
        "Run the daemon in a separate terminal; slice 155 adds supervised launch."`.
  - [x] Pass `workers=workers` to `uvicorn.run(...)`.
  - [x] Success: `uv run mt serve --help` shows `--workers` option;
        `uv run mt serve --workers 1` starts without error.

- [x] **T11 — Static analysis**
  - [x] `uv run ruff check src/manta_trading/api_server/routes/gaps.py`
        — zero errors.
  - [x] `uv run ruff check src/manta_trading/api_server/app.py
        src/manta_trading/cli/commands/serve.py` — zero errors.
  - [x] `uv run pyright src/manta_trading/api_server/routes/gaps.py
        src/manta_trading/api_server/app.py
        src/manta_trading/api_server/models/responses.py
        src/manta_trading/cli/commands/serve.py` — zero errors.
  - [x] Fix any issues before proceeding to T12.

- [x] **T12 — Full test suite regression**
  - [x] `uv run pytest test/unit/api_server/ -v` — all tests pass.
  - [x] Expected: 21 existing + 8+ new gaps/500 tests = 29+ total.

- [x] **T13 — Integration verification (prod DB)**
  - Prerequisites: `MT_TIMESCALE_DB_URL` set to prod (`trading`).
  - [x] Start server: `uv run mt serve --workers 1`
  - [x] `GET /api/v1/gaps/SPY` → 200, `count >= 0`.
  - [x] `GET /api/v1/gaps/SPY?granularity=1m` → 200, all returned `granularity`
        values are `"minute"` (or empty list).
  - [x] Window filter correctness — run:
        ```bash
        curl -s "http://localhost:8100/api/v1/gaps/SPY?start=2024-01-01&end=2024-01-31" \
          | python3 -c "
        import sys, json
        from datetime import datetime, timezone
        d = json.load(sys.stdin)
        window_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        window_end   = datetime(2024, 1, 31, tzinfo=timezone.utc)
        for g in d['gaps']:
            gs = datetime.fromisoformat(g['gap_start'])
            ge = datetime.fromisoformat(g['gap_end'])
            assert ge > window_start and gs < window_end, f'Gap outside window: {g}'
        print('window filter ok, count:', d['count'])
        "
        ```
        Assert exits 0 and prints `window filter ok, count: N`.
  - [x] `GET /api/v1/gaps/FAKESYMBOL` → 200, `count == 0`.
  - [x] `GET /api/v1/gaps/SPY?granularity=bad` → 422.
  - [x] `curl -s http://localhost:8100/openapi.json | python3 -c
        "import sys,json; d=json.load(sys.stdin); print(sorted(d['paths']))"
        ` → 5 paths including `/api/v1/gaps/{symbol}`.
  - [x] `GET /api/v1/health` → `{"status":"ok","db":"ok"}` (regression).

- [x] **T14 — Commit and merge**
  - [x] `git add src/manta_trading/api_server/routes/gaps.py
              src/manta_trading/api_server/models/responses.py
              src/manta_trading/api_server/app.py
              src/manta_trading/cli/commands/serve.py
              test/unit/api_server/test_gaps.py`
  - [x] `git commit -m "feat: add gaps endpoint, 500 handler, OpenAPI desc,
        --workers flag"`
  - [x] Open PR to `main`; confirm CI passes.
  - [x] Merge to `main`.

- [x] **T15 — Update project artifacts**
  - [x] Mark slice 184 complete in `180-slices.data-serving-api.md`
        (entry 4: `[ ]` → `[x]`).
  - [x] Update slice doc status to `complete`.
  - [x] Update this task file status to `complete`.
  - [x] Add CHANGELOG entry for slice 184.
  - [x] Commit: `git commit -m "docs: mark slice 184 complete; update
        walkthrough actuals and CHANGELOG"`
