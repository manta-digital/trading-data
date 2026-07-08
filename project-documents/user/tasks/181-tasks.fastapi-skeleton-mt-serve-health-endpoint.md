---
docType: tasks
slice: fastapi-skeleton-mt-serve-health-endpoint
project: trading
lld: user/slices/181-slice.fastapi-skeleton-mt-serve-health-endpoint.md
dependencies: [154]
projectState: main is clean; initiative 180 planned; api_server/ package does not exist yet (existing api/ is unrelated provider-HTTP utilities)
dateCreated: 20260513
dateUpdated: 20260513
status: complete
---

# Tasks: FastAPI Skeleton + `mt serve` + Health Endpoint

## Context

Slice 181 creates the foundation for the Data Serving API (initiative 180).
It adds four new production dependencies, creates the
`src/manta_trading/api_server/` package, wires a lifespan-managed
`ConnectionPool` to `app.state.db_pool`,
implements `GET /api/v1/health`, adds a `mt serve` Typer command, and
covers the health endpoint with three unit tests.

No bar or symbol logic is included. Subsequent slices (182–184) build on
the structure created here.

Branch: `181-slice.fastapi-skeleton-mt-serve-health-endpoint`

---

## Tasks

### T1 — Create branch

- [x] From `main`, create and switch to branch
  `181-slice.fastapi-skeleton-mt-serve-health-endpoint`
- [x] Confirm working directory is `/Users/manta/source/repos/manta/trading`

---

### T2 — Add dependencies to `pyproject.toml`

File: `pyproject.toml`

- [x] Add the following to `[project].dependencies`:
  - `"fastapi>=0.115.0"`
  - `"uvicorn[standard]>=0.34.0"`
  - `"orjson>=3.10.0"`
  - `"msgpack>=1.1.0"`
- [x] Run `uv pip install -e ".[dev]"` — confirm it succeeds with no errors
- [x] Confirm `fastapi`, `uvicorn`, `orjson`, and `msgpack` appear in
  `uv pip list` output

---

### T3 — Create `api_server/` package skeleton

Create all directories and `__init__.py` stubs. No logic yet.
Note: the existing `src/manta_trading/api/` package holds unrelated
provider-HTTP utilities; the inbound FastAPI service lives under
`api_server/` to keep the two roles separate.

- [x] Create `src/manta_trading/api_server/__init__.py` — empty or single
  `from manta_trading.api_server.app import create_app as create_app` re-export
- [x] Create `src/manta_trading/api_server/routes/__init__.py` — empty
- [x] Create `src/manta_trading/api_server/models/__init__.py` — empty
- [x] Confirm `src/manta_trading/api_server/` tree matches:
  ```
  api_server/
    __init__.py
    routes/
        __init__.py
    models/
        __init__.py
  ```

---

### T4 — Implement `models/responses.py`

File: `src/manta_trading/api_server/models/responses.py`

- [x] Define `HealthResponse(BaseModel)` with fields:
  - `status: Literal["ok"]`
  - `db: Literal["ok", "error"]`
  - `detail: str | None = None`
- [x] Add stub comment block marking where `BarsResponse` and `BarRecord`
  will be added in slice 182 (one-line `# Slice 182: BarsResponse, BarRecord`)
- [x] Confirm file type-checks cleanly (`pyright` reports zero errors on
  this file alone)

---

### T5 — Implement `deps.py`

File: `src/manta_trading/api_server/deps.py`

- [x] Implement `get_db(request: Request) -> Generator[Connection, None, None]`
  that does `with request.app.state.db_pool.connection() as conn: yield conn`
- [x] Import `Connection` from `psycopg` and `ConnectionPool` is accessed
  only through `request.app.state` (no direct import of pool here)
- [x] Type-hint the generator return as `Generator[psycopg.Connection[Any], None, None]`
  (use `from __future__ import annotations` and import `Generator` from
  `collections.abc`)
- [x] Confirm pyright reports zero errors on this file

---

### T6 — Implement `app.py`

File: `src/manta_trading/api_server/app.py`

- [x] Implement `_configure_connection(conn) -> None` — sets autocommit,
  executes `SET timezone = 'UTC'`, `SET work_mem = '512MB'`,
  `SET statement_timeout = '300s'`, then restores autocommit to False.
  Mirrors the pattern in `TimescaleMinuteDataDB._configure_connection`.
- [x] Implement `lifespan(app: FastAPI)` as an `@asynccontextmanager`:
  - Read `Settings().timescale_db_url`; raise `RuntimeError` if `None`
  - Open `ConnectionPool` via `run_in_executor` (pool creation is blocking)
    with `min_size=2, max_size=8, max_lifetime=3600.0, configure=_configure_connection`
  - Store pool in `app.state.db_pool`
  - `yield`
  - Close pool on shutdown
- [x] Implement `create_app() -> FastAPI`:
  - Instantiate `FastAPI(title="Manta Trading API", version="0.1.0", lifespan=lifespan)`
  - Add `CORSMiddleware` with `allow_origins=["*"]`,
    `allow_methods=["*"]`, `allow_headers=["*"]`
  - Include the health router (imported from `routes.health`)
  - Return the app
- [x] Confirm all imports are grouped: stdlib → third-party → local
- [x] Confirm pyright reports zero errors on this file

---

### T7 — Implement `routes/health.py`

File: `src/manta_trading/api_server/routes/health.py`

- [x] Create an `APIRouter` with prefix `/api/v1`
- [x] Implement `GET /health` handler (full path: `GET /api/v1/health`):
  - Declare `db: Annotated[psycopg.Connection[Any], Depends(get_db)]`
  - Execute `db.execute("SELECT 1")` inside a `try/except psycopg.Error`
  - On success: return `HealthResponse(status="ok", db="ok")`
  - On `psycopg.Error`: log at WARNING level with `logger.warning(...)`;
    return `HealthResponse(status="ok", db="error", detail=str(exc))`
  - HTTP status code is 200 in both cases
- [x] Confirm the `detail` field is omitted from the JSON response when `None`
  (set `model_config = ConfigDict(exclude_none=True)` in `HealthResponse`,
  or use `response.model_dump(exclude_none=True)` — pick one approach and
  apply it consistently)
- [x] Confirm pyright reports zero errors on this file

### T8 — Test: health endpoint unit tests

File: `test/unit/api_server/test_health.py`

- [x] Create `test/unit/api_server/test_health.py` (create the
  `test/unit/api_server/` directory if it does not exist; add an empty
  `test/unit/api_server/__init__.py`)
- [x] Add a `@pytest.fixture` named `test_app` that:
  - Calls `create_app()`
  - Overrides the lifespan by assigning a mock pool to `app.state.db_pool`
    before the `TestClient` starts (use `app.state` directly or a
    dependency override on `get_db`)
- [x] **Test 1** — `test_health_ok`: override `get_db` to yield a mock
  `psycopg.Connection` whose `execute` succeeds (returns without error).
  Assert response status is 200 and body equals `{"status": "ok", "db": "ok"}`
- [x] **Test 2** — `test_health_db_error`: override `get_db` to yield a mock
  whose `execute` raises `psycopg.OperationalError("conn refused")`.
  Assert response status is 200 and body contains `{"status": "ok", "db": "error"}`
  (detail field may or may not be present — only assert the required fields)
- [x] **Test 3** — `test_health_route_registered`: assert that at least one
  route in `create_app().routes` has a path matching `/api/v1/health`
- [x] Run tests: `uv run pytest test/unit/api_server/test_health.py -v` — all 3 pass

---

### T9 — Implement `cli/commands/serve.py`

File: `src/manta_trading/cli/commands/serve.py`

- [x] Define a module-level `serve` function (not a Typer app — a plain
  function decorated with nothing; registration happens in `app.py`):
  ```python
  def serve(
      host: str = typer.Option("0.0.0.0", help="Bind host."),
      port: int = typer.Option(8100, help="Bind port."),
      reload: bool = typer.Option(False, "--reload",
          help="Auto-reload on code changes. Dev mode only."),
  ) -> None:
      """Start the Data Serving API server."""
      uvicorn.run(
          "manta_trading.api_server.app:create_app",
          factory=True,
          host=host,
          port=port,
          reload=reload,
      )
  ```
- [x] Import `uvicorn` at the top of the file (third-party group)
- [x] Confirm pyright reports zero errors on this file

### T10 — Register `mt serve` in `cli/app.py`

File: `src/manta_trading/cli/app.py`

- [x] Add import: `from manta_trading.cli.commands.serve import serve`
- [x] Register as a direct command: `app.command(name="serve")(serve)`
  (placed after the existing `add_typer` calls)
- [x] Run `mt serve --help` — confirm output shows `--host`, `--port`,
  `--reload` with their defaults and descriptions
- [x] Run `mt --help` — confirm `serve` appears in the command list

---

### T11 — Static analysis

- [x] Run `uv run ruff check src/manta_trading/api_server/ src/manta_trading/cli/commands/serve.py`
  — zero errors
- [x] Run `uv run pyright src/manta_trading/api_server/ src/manta_trading/cli/commands/serve.py`
  — zero errors
- [x] Fix any issues before continuing

---

### T12 — Full unit test suite

- [x] Run `uv run pytest test/unit -q` — all tests pass, no regressions

---

### T13 — Commit

- [x] `git add` all new and modified files
- [x] Commit: `feat: add FastAPI skeleton, mt serve command, and health endpoint`

---

### T14 — Verification walkthrough

- [x] Run `uv pip install -e ".[dev]"` — succeeds
- [x] Run `mt serve --help` — shows `--host`, `--port`, `--reload`
- [x] Run `mt serve` in one terminal (leave running)
- [x] In a second terminal: `curl -s http://localhost:8100/api/v1/health | python3 -m json.tool`
  — output is `{"status": "ok", "db": "ok"}`
- [x] Open `http://localhost:8100/docs` in browser — Swagger UI loads with
  `GET /api/v1/health` listed
- [x] Stop server with `CTRL+C` — clean shutdown message from uvicorn
