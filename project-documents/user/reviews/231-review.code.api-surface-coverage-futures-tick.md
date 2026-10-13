---
docType: review
layer: project
reviewType: code
slice: api-surface-coverage-futures-tick
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/231-slice.api-surface-coverage-futures-tick.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261013
dateUpdated: 20261013
reviewedSha: 35788633abf1245a60d0a570fe9cace0de05acc4
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 1
diffTruncated: false
durationSeconds: 33.2
squadronVersion: 0.21.6
findings:
  - id: F001
    severity: concern
    category: typing
    summary: "Dynamic StrEnum defeats strict typing and needs a type-ignore"
    location: "src/manta_trading/api_server/routes/futures_params.py:22-24"
  - id: F002
    severity: concern
    category: error-handling
    summary: "open_tick_pool runs outside the try/finally that closes the primary resources"
    location: "src/manta_trading/api_server/app.py:137-141"
  - id: F003
    severity: concern
    category: error-handling
    summary: "Connection leak on non-psycopg errors in _checkout"
    location: "src/manta_trading/api_server/tick_access.py:_checkout"
  - id: F004
    severity: concern
    category: conventions
    summary: "Literal status codes beside the project's named-status convention"
    location: "src/manta_trading/api_server/app.py:_tick_unavailable_handler; src/manta_trading/api_server/routes/health.py:health"
  - id: F005
    severity: note
    category: dry
    summary: "Instant normalization is done twice in the ticks path"
    location: "src/manta_trading/api_server/routes/futures_series.py:_ticks"
  - id: F006
    severity: note
    category: performance
    summary: "Per-request migration check adds a round trip"
    location: "src/manta_trading/api_server/tick_access.py:_checkout"
  - id: F007
    severity: pass
    category: overall
    summary: "Exception handling, test isolation, and design"
    location: "src/manta_trading/api_server/tick_access.py"
---

# Review: code — slice 231

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Dynamic StrEnum defeats strict typing and needs a type-ignore

`TickBarGranularity` is built with the functional `StrEnum(...)` API from a non-literal list, and a `# type: ignore[misc]` suppresses the error. Strict pyright (zero errors is a merge blocker) cannot see its members. The value `granularity.value` is then re-wrapped in `Granularity(...)` in `futures_series.py:_bars_reader`. A statically declared enum, or a `Literal` type derived from `Granularity`, would remove the suppression. At minimum, add a test that pins the enum's members to `TICK_BAR_GRANULARITIES`.

### [CONCERN] open_tick_pool runs outside the try/finally that closes the primary resources

`await open_tick_pool(...)` is called before the `try: yield / finally:` block. If it raises (for example, a malformed `tick_db_url` makes `AsyncConnectionPool` or `pool.open` fail), the primary `ConnectionPool` and the executor are never closed. Move the call inside the `try`, or guard it, so startup failure still runs teardown. `close_tick_pool` already tolerates a `None` pool.

### [CONCERN] Connection leak on non-psycopg errors in _checkout

After `getconn()` succeeds, `missing_migrations(conn)` is guarded only by `except psycopg.Error`. Any other exception, such as a cancelled task or an unexpected error, skips `putconn`. With a pool max of 4, a few leaked checkouts would exhaust the tick tier. Use `try/finally`, or return the connection on every exit except the success path.

### [CONCERN] Literal status codes beside the project's named-status convention

The new handler uses `status_code=503` and health uses `status_code=200`. Elsewhere in the same change `http_status.HTTP_422_UNPROCESSABLE_CONTENT` and `HTTP_404_NOT_FOUND` are used. CLAUDE.md asks that comparison values be defined once, so use `http_status.HTTP_503_SERVICE_UNAVAILABLE` and `HTTP_200_OK`. The `503` is also repeated in `TICK_UNAVAILABLE_RESPONSE`.

### [NOTE] Instant normalization is done twice in the ticks path

`instant_bounds` already normalizes `from`/`until` through `instant_utc`. `_ticks` then calls `instant_utc` again to build the echoed values. This is harmless, but one helper returning both the aware datetimes and the epoch ns would keep a single conversion.

### [NOTE] Per-request migration check adds a round trip

Every futures, health, status and overview request runs `missing_migrations` on its checked-out connection. This is a deliberate design choice (recovery without restart is tested). The cost is one extra query per request, so it is worth revisiting if the load bounds tighten.

### [PASS] Exception handling, test isolation, and design

Exceptions are caught narrowly, and `tick_errors` orders `QueryCanceled` before `OperationalError` with a comment explaining why. Swallowing is documented where it happens, and password scrubbing is tested. The shared `classify` and `row_cells` helpers remove duplicated logic from the CLI. Tests use throwaway databases and an autouse guard that keeps the developer's tick URL out. Unit, integration and load coverage includes CLI/API parity, pinned ceiling behavior, and latency bounds.

### Run Digest

- Response length: 3923 chars
- Response is newline-free: no
- Tool calls made: 1
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 33.2 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
