---
docType: slice-design
slice: fastapi-skeleton-mt-serve-health-endpoint
project: trading
parent: user/architecture/180-slices.data-serving-api.md
dependencies: [154]
interfaces: [182]
dateCreated: 20260513
dateUpdated: 20260513
status: complete
---

# Slice Design: FastAPI Skeleton + `mt serve` + Health Endpoint

## Overview

This slice creates the structural foundation for the Data Serving API
(initiative 180): a runnable FastAPI application, a `mt serve` CLI command
that starts the ASGI server, and a single `GET /api/v1/health` endpoint
that confirms the server is up and the database is reachable.

No bar or symbol logic is included. The goal is a running, testable server
that subsequent slices (182, 183, 184) can build on.

## Scope

**Included:**

- Add `fastapi`, `uvicorn[standard]`, `orjson`, and `msgpack` to
  `pyproject.toml` as production dependencies.
- Create `src/manta_trading/api_server/` package with the full directory
  structure specified below (even if some files are stubs). Note: the
  existing `src/manta_trading/api/` package holds outbound provider HTTP
  utilities (eodhd_sync, http_retry, finnhub, etc.) — the inbound FastAPI
  service lives under `api_server/` to keep the two roles separate.
- `app.py` — FastAPI instance, CORS middleware, lifespan hook that opens
  and closes a `ConnectionPool` using `Settings.timescale_db_url`.
- `deps.py` — dependency-injection helper that yields a connection from the
  pool.
- `routes/health.py` — `GET /api/v1/health` endpoint.
- `models/responses.py` — `HealthResponse` Pydantic model (and empty stubs
  for models slices 182+ will fill in).
- `src/manta_trading/cli/commands/serve.py` — `mt serve` Typer command.
- Register `mt serve` in `src/manta_trading/cli/app.py`.
- Unit tests: `test/unit/api_server/test_health.py`.

**Excluded:**

- Bar, symbol, or gaps endpoints (slices 182–184).
- msgpack response serialization — dependency is added now, usage deferred
  to slice 182.
- `--workers` flag (slice 184).
- Supervised launch / `mt start` (future, requires slice 155).

## Technical Decisions

### Package and Dependency Additions

```toml
# pyproject.toml — add to [project].dependencies
"fastapi>=0.115.0",
"uvicorn[standard]>=0.34.0",
"orjson>=3.10.0",
"msgpack>=1.1.0",
```

`fastapi` pulls in `pydantic` (already an indirect dep via `pydantic-settings`)
and `starlette`. `uvicorn[standard]` adds `uvloop` and `httptools` for
production ASGI performance on macOS/Linux.

### Directory Layout

```
src/manta_trading/api_server/
    __init__.py          # re-exports create_app for import convenience
    app.py               # FastAPI instance, CORS, lifespan
    deps.py              # get_db() dependency
    routes/
        __init__.py
        health.py        # GET /api/v1/health
    models/
        __init__.py
        responses.py     # HealthResponse (+ future stubs)
```

### Application Factory

`app.py` exposes `create_app() -> FastAPI` rather than a module-level
`app` instance. This makes the lifespan hook testable: the `TestClient` in
unit tests creates its own `app` instance with a patched pool. `uvicorn` is
pointed at `manta_trading.api_server.app:create_app` with the factory pattern
(`factory=True` in uvicorn config, or imported directly in `serve.py`).

### Database Connection Pool in the Lifespan

The lifespan hook owns the pool lifecycle. It reads
`Settings().timescale_db_url` at startup and raises `RuntimeError` if the
URL is not set (no silent fallback). The pool is stored in
`app.state.db_pool` so `deps.py` can yield connections from it.

```python
# Conceptual only — implementation detail, not final code
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    if not settings.timescale_db_url:
        raise RuntimeError("MT_TIMESCALE_DB_URL is required for the API server")
    loop = asyncio.get_event_loop()
    pool = await loop.run_in_executor(
        None,
        lambda: ConnectionPool(
            str(settings.timescale_db_url),
            min_size=2,
            max_size=8,
            max_lifetime=3600.0,
            configure=_configure_connection,
        ),
    )
    app.state.db_pool = pool
    yield
    pool.close()
```

The `_configure_connection` callback replicates the session parameters used
by `TimescaleMinuteDataDB` (UTC timezone, work_mem, statement_timeout). It
lives in `app.py` as a module-level private function.

Pool sizing: `min_size=2, max_size=8`. Single-user tool — this is
conservative and can be adjusted without a slice.

### Dependency Injection

`deps.py` exposes one function:

```python
def get_db(request: Request) -> Generator[Connection, None, None]:
    with request.app.state.db_pool.connection() as conn:
        yield conn
```

Route handlers declare `db: Annotated[Connection, Depends(get_db)]`.
The `Connection` type is `psycopg.Connection` from psycopg3.

### CORS

`CORSMiddleware` with `allow_origins=["*"]` (permissive — single-user local
network). This matches the arch decision; no auth layer is added.

### Health Endpoint

`GET /api/v1/health` is registered as a standalone router (not a sub-app).
It performs a lightweight `SELECT 1` using the injected connection to
confirm DB reachability. On success: `{"status": "ok", "db": "ok"}` with
HTTP 200. On DB error: `{"status": "ok", "db": "error", "detail": "<msg>"}`
with HTTP 200 — the server is up even when the DB is down; callers
distinguish the DB state from the `db` field, not from the HTTP status.
This is the conventional liveness/readiness pattern for health endpoints.

`HealthResponse` Pydantic model:
```python
class HealthResponse(BaseModel):
    status: Literal["ok"]
    db: Literal["ok", "error"]
    detail: str | None = None
```

### `mt serve` Command

`serve.py` adds a top-level Typer command (not a sub-app):

```python
@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(8100, help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev only)"),
) -> None:
    """Start the Data Serving API server."""
    ...
```

It calls `uvicorn.run("manta_trading.api_server.app:create_app",
factory=True, host=host, port=port, reload=reload)`. The `reload` flag is
documented as
dev-only — production use should run with `reload=False` (the default).

Registration in `app.py`:
```python
from manta_trading.cli.commands.serve import serve as serve_command
app.command(name="serve")(serve_command)
```

Note: `serve` is registered as a direct command on the Typer `app`, **not**
as a sub-app Typer. Existing commands (status, config, data, provider) are
sub-apps. `mt serve` takes no subcommands, so a direct command is correct.

### Error Shape Convention

All error responses in this initiative use `{"error": "<message>"}`. The
health endpoint is the exception — it uses its own `HealthResponse` shape
because it's a status report, not an error response. The global 500 handler
(slice 184) will standardize error shapes across all endpoints.

## Cross-Slice Interfaces

**Provided to slice 182:**

- `app.state.db_pool` — a live `ConnectionPool` accessible via `get_db`.
- `create_app()` factory in `manta_trading.api_server.app`.
- `models/responses.py` — stub file where `BarsResponse`, `BarRecord` will
  be added.
- `routes/` directory — `bars.py` will be added and registered here.

**Consumed from slice 154 (CLI foundation):**
- `Settings.timescale_db_url` — used to open the pool.
- `manta_trading.cli.app.app` — `serve` command is registered here.

## Unit Tests

File: `test/unit/api_server/test_health.py`

Tests use FastAPI's `TestClient` (synchronous, from `starlette.testclient`).
The pool is patched so tests do not require a live DB.

**Test 1 — health returns 200 with ok body when DB is reachable:**
Patch `get_db` to yield a mock connection whose `execute` succeeds. Assert
response is `{"status": "ok", "db": "ok"}` and status code is 200.

**Test 2 — health returns 200 with error body when DB query raises:**
Patch `get_db` to yield a mock connection whose `execute` raises
`psycopg.OperationalError`. Assert response is `{"status": "ok", "db":
"error"}` and status code is 200.

**Test 3 — health endpoint is mounted at `/api/v1/health`:**
Confirm the route exists in `app.routes` (a structural sanity check that
catches missed router registration).

Test fixture creates a fresh `create_app()` instance with the lifespan
disabled (use `with TestClient(app, raise_server_exceptions=False)` or
override `app.state.db_pool` before the client starts).

## Success Criteria

1. `uv pip install -e ".[dev]"` succeeds after adding the four new deps.
2. `mt serve --help` displays `--host`, `--port`, and `--reload` options.
3. `mt serve` starts without error and logs the uvicorn startup line.
4. `curl http://localhost:8100/api/v1/health` returns `{"status":"ok","db":"ok"}` with HTTP 200.
5. `curl http://localhost:8100/docs` returns the Swagger UI (HTTP 200).
6. `pytest test/unit/api_server/test_health.py` passes (all three tests, no live DB).
7. `ruff check src/manta_trading/api_server/ src/manta_trading/cli/commands/serve.py` reports zero errors.
8. `pyright src/manta_trading/api_server/ src/manta_trading/cli/commands/serve.py` reports zero errors.

## Verification Walkthrough

Verified end-to-end on 2026-05-13 against the slice branch
`181-slice.fastapi-skeleton-mt-serve-health-endpoint`. The walkthrough
below is reproducible by any agent (human or AI) with `MT_TIMESCALE_DB_URL`
set in the environment.

**Prerequisite:** `MT_TIMESCALE_DB_URL` must point at a reachable
PostgreSQL/TimescaleDB instance (the lifespan hook raises `RuntimeError`
on startup if it is unset — no silent fallback).

**1. Install and check CLI:**
```
uv pip install -e ".[dev]"
uv run mt serve --help
```
Actual output (header + options block):
```
 Usage: mt serve [OPTIONS]
 Start the Data Serving API server.
 --host          TEXT     Bind host. [default: 0.0.0.0]
 --port          INTEGER  Bind port. [default: 8100]
 --reload                 Auto-reload on code changes. Dev mode only.
 --help                   Show this message and exit.
```
Also confirm `uv run mt --help` lists `serve` among the commands
(it does — alongside `status`, `config`, `data`, `provider`).

**2. Start the server:**
```
uv run mt serve
```
Actual uvicorn startup log (one app-level INFO line interleaved):
```
INFO:     Started server process [NNNNN]
INFO:     Waiting for application startup.
2026-05-13 HH:MM:SS,SSS INFO     manta_trading.api_server.app: API server connection pool opened
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8100 (Press CTRL+C to quit)
```

**3. Health check:**
```
curl -s http://localhost:8100/api/v1/health | python3 -m json.tool
```
Actual output:
```json
{
    "status": "ok",
    "db": "ok"
}
```
HTTP status: 200. Note: `detail` is absent (the route applies
`model_dump(exclude_none=True)` so the field is omitted when null).

**4. OpenAPI docs:**
Open `http://localhost:8100/docs` in a browser. Expected: Swagger UI
loads (HTTP 200) and shows one endpoint, `GET /api/v1/health`. Verified
non-interactively via:
```
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8100/docs   # 200
curl -s http://localhost:8100/openapi.json | \
  python3 -c "import sys,json; d=json.load(sys.stdin); \
              print(list(d['paths']), d['info']['title'], d['info']['version'])"
# ['/api/v1/health'] Manta Trading API 0.1.0
```

**5. Unit tests (no server required):**
```
uv run pytest test/unit/api_server/test_health.py -v
```
Actual result: `3 passed in 0.82s`. Full unit suite
(`uv run pytest test/unit -q`) also passes: `1252 passed, 12 skipped`
with one pre-existing unrelated warning.

**6. Stop the server:**
`CTRL+C` in the serve terminal (or `kill -INT <pid>`).
Actual shutdown log:
```
INFO:     Shutting down
INFO:     Waiting for application shutdown.
2026-05-13 HH:MM:SS,SSS INFO     manta_trading.api_server.app: API server connection pool closed
INFO:     Application shutdown complete.
INFO:     Finished server process [NNNNN]
```
Process exits 0. The lifespan hook closes the `ConnectionPool` cleanly.

### Caveats discovered

- The slice originally specified `src/manta_trading/api/` for the new
  FastAPI package; that namespace was already in use for outbound
  provider HTTP utilities (`eodhd_sync`, `http_retry`, `finnhub`). The
  package was renamed to `api_server/` before implementation — see
  `feat: add FastAPI skeleton, mt serve command, and health endpoint`
  commit and the preceding `docs:` rename commit. URL prefix and CLI
  command are unaffected.
- Task T4 said `exclude_none=True` could go in `ConfigDict`, but Pydantic
  v2's `ConfigDict` does not have that field; it is a `model_dump`
  argument. The route handler uses `model_dump(exclude_none=True)` per
  the second option the task allowed.
- The test fixture intentionally constructs `TestClient` *without*
  entering it as a context manager so the lifespan hook never fires
  (and no live DB is contacted). `app.state.db_pool` is set to a
  `MagicMock` sentinel and `get_db` is replaced via
  `app.dependency_overrides`.
