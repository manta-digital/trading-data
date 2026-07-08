---
docType: slice-plan
parent: user/architecture/180-arch.data-serving.md
project: trading
dateCreated: 20260513
dateUpdated: 20260514
status: complete
---

# Slice Plan: Data Serving API

## Source

[180-arch.data-serving.md](180-arch.data-serving.md)

## Approach

Four slices. Skeleton first — FastAPI app wired to the DB, `mt serve` command, health endpoint — so we have a running server before adding any real endpoints. Bars second because it is the critical path for trading-ui and the most complex routing logic (minute vs. daily, date→datetime conversion, async/sync bridge). Symbols third, including the `available` ranges materialized view. Polish last: msgpack support, gaps endpoint, and OpenAPI cleanup.

Daemon and API server run as separate OS processes in separate terminals for now. Supervised launch (slice 155) is a future dependency; no work here. Redis/in-memory caching is similarly deferred — query latency is acceptable at current data volumes without it (future slice if profiling shows otherwise).

Each slice delivers a testable, operator-visible capability. The server does not need to be running for unit tests — all route handlers are tested via FastAPI's `TestClient`.

## Slices

1. [x] **(181) FastAPI skeleton + `mt serve` + health endpoint** — [181-slice.fastapi-skeleton-mt-serve-health-endpoint.md](../slices/181-slice.fastapi-skeleton-mt-serve-health-endpoint.md) — Add `fastapi`, `uvicorn[standard]`, `orjson`, and `msgpack` to `pyproject.toml`. Create `src/manta_trading/api/` package: `app.py` (FastAPI instance, CORS middleware allowing all local-network origins, lifespan hook that opens a psycopg3 connection pool from `Settings` on startup and closes it on shutdown), `deps.py` (dependency-injection helper that yields a `Connection` from the pool), `routes/health.py` (`GET /api/v1/health` — returns `{"status": "ok", "db": "ok"|"error"}`), `models/responses.py` (initial Pydantic models). Add `mt serve` Typer command in `src/manta_trading/cli/commands/serve.py` — wraps `uvicorn.run` with `--host`, `--port` (default `8100`), and `--reload` options. Register the command in the CLI app. This slice proves the server starts, connects to the DB, and returns a health response. No bar or symbol logic yet. Verifiable: `mt serve` starts without error; `curl http://localhost:8100/api/v1/health` returns `{"status": "ok", "db": "ok"}`; `curl http://localhost:8100/docs` returns the OpenAPI UI; unit test asserts health returns 200 with correct body; unit test asserts health returns DB error shape when pool is broken. Dependencies: [154]. Effort: 2/5.

2. [x] **(182) Bars endpoint** — [182-slice.bars-endpoint.md](../slices/182-slice.bars-endpoint.md) — Add `routes/bars.py`: `GET /api/v1/bars/{symbol}` with query params `granularity` (`Granularity` StrEnum from `manta_trading.constants`), `start` (ISO date), `end` (ISO date), `adjusted` (bool, default `true`), `format` (`"json"` | `"msgpack"`, default `"json"`). Route logic: define `_MINUTE_GRAINS = {M1, M5, M15, H1, H4}` in `bars.py`; convert `date` → `datetime` at midnight UTC before calling `TimescaleMinuteDataDB.get_minute_data(..., aggregation=granularity)`; call `TimescaleDailyDataDB.get_daily_data` for daily+ granularities. Both calls via `run_in_executor` (async/sync bridge). Serialize DataFrame to `BarsResponse` Pydantic model (fields: `symbol`, `granularity`, `adjusted`, `count`, `bars: list[BarRecord]`). Return orjson-serialized JSON by default; return msgpack bytes with `Content-Type: application/x-msgpack` when `format=msgpack`. Register 404 exception handler in `app.py` for empty result or unknown symbol. Verifiable: `GET /api/v1/bars/SPY?granularity=1d&start=2024-01-01&end=2024-01-31` returns correct OHLCV JSON with `count=21`; same request with `format=msgpack` returns binary with correct Content-Type; minute granularity routes to minute DB with datetime conversion; unknown symbol returns 404 `{"error": "..."}`; invalid granularity returns 422; `adjusted=false` returns unadjusted bars. Dependencies: [181]. Effort: 2/5.

3. [x] **(183) Symbols endpoints + `available` ranges view** — Add DB migration creating `symbol_data_ranges` materialized view: for each symbol and each granularity source table/aggregate, `MIN(bucket)` and `MAX(bucket)` partitioned by symbol. Add `pg_cron` or TimescaleDB refresh policy to refresh the view hourly (if available on the target DB); document manual refresh command as fallback. Add `routes/symbols.py`: `GET /api/v1/symbols` with optional `?search=` (prefix match on symbol) query param — returns array of `{symbol, name, exchange, type}` from `instruments` ordered by symbol. `GET /api/v1/symbols/{symbol}` — returns instrument metadata plus `available` dict from `symbol_data_ranges` view (granularities with no data are omitted). Register 404 for unknown symbol. Update `models/responses.py` with `SymbolSummary`, `SymbolDetail`, `SymbolsResponse`, `AvailableRange`. Verifiable: `GET /api/v1/symbols?search=SPY` returns SPY and any other SPY-prefixed symbols; `GET /api/v1/symbols/SPY` returns metadata with `available` block containing at least `1d` range; unknown symbol returns 404; `available` only includes granularities for which data exists. Dependencies: [182]. Effort: 2/5.

4. [x] **(184) Polish: gaps endpoint**, error handling hardening, OpenAPI cleanup — Add `routes/gaps.py`: `GET /api/v1/gaps/{symbol}` with `?granularity` and `?start`/`?end` query params — returns rows from `data_gaps` table for the symbol/granularity/window. Add consistent 500 exception handler in `app.py` that logs the full traceback at ERROR level and returns `{"error": "internal server error"}` (no SQL or stack trace in body). Audit all routes to confirm no DB errors leak detail to clients. Set FastAPI `title`, `description`, and `version` in `app.py` for clean OpenAPI docs. Suppress internal utility routes from docs if any. Add `--workers N` option to `mt serve` (default 1; documents single-worker rationale in help text). Note in `mt serve --help` that running alongside the daemon requires a separate terminal; reference slice 155 as the future supervised launcher. Verifiable: `GET /api/v1/gaps/SPY?granularity=1m&start=2024-01-01&end=2024-01-31` returns gap rows or empty list; injecting a DB error returns 500 with sanitized body and logs full traceback server-side; `GET /docs` shows clean API documentation with correct title/version; all four endpoints appear in the OpenAPI schema. Dependencies: [183]. Effort: 1/5.

## Future Work 

1. [ ] **Supervised process launcher**: systemd unit(s) for the data acquisition daemon and API server on .144 (Ubuntu 24.04). Decisions to resolve before picking this up: user vs. system unit, env-var injection mechanism, daemon_id resolution, log routing (journald vs. file). The Python worker already handles SIGTERM cleanly; this is purely ops infrastructure. No longer depends on a prior slice — design from scratch when the time is right.
2. [ ] **Response caching** (Redis or in-memory): defer until profiling identifies a bottleneck. At current data volumes and single-user access patterns, DB query latency is acceptable without caching.

## Notes

- Slices 181 → 182 → 183 → 184 are a strict dependency chain. No slice can ship out of order.
- Slice 182 unblocks trading-ui development. Slices 183 and 184 can proceed in parallel with UI work once 182 is merged.
- Existing DB methods (`get_minute_data`, `get_daily_data`) are synchronous psycopg3. All API route handlers bridge to them via `run_in_executor`. Migrating to `AsyncConnection` is future work only if profiling shows contention at this access pattern.
- `Granularity` StrEnum lives in `manta_trading.constants` (confirmed). The minute/daily routing boundary mirrors the private `_MINUTE_GRAINS` set in `timescale_minute_db.py`; `bars.py` redeclares it as a module-level constant rather than importing the private symbol.
- CORS is permissive for local network use (all origins). No auth, no rate limiting — single-user tool.
- No pagination. Callers are responsible for requesting bounded date ranges. Range policy documented in the arch doc.
