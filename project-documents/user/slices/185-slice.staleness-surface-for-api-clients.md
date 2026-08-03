---
docType: slice-design
slice: staleness-surface-for-api-clients
project: trading-data
parent: user/architecture/180-slices.data-serving-api.md
dependencies: [167, 168]
interfaces: [187]
dateCreated: 20260803
dateUpdated: 20260803
status: in-progress
effort: 2
---

# Slice Design: Staleness Surface for API Clients

## Overview

The serving API (slices 181–184) was built 2026-05-13/14 and predates the
data-quality era (163/166/167/168) entirely. `GET /api/v1/bars` serves
5m/15m/1h/4h/1w/1mo/1q from continuous aggregates; `GET /api/v1/health` only
checks `SELECT 1`. Neither has any concept of cagg staleness. If a refresh
policy stalls — the exact failure mode slice 163 hit in production and slice
167/168 built machinery to detect — a client gets a `200 OK` with silently
truncated or stale bars, and a passing health check while `mt data status`
would already be showing an `OUT OF DATE` banner.

This slice wires the existing, already-built freshness machinery
(`assert_cagg_fresh` from 168, `status_coverage`/`status_queries` from 167)
into three API surfaces: a new `GET /api/v1/status` endpoint, a `coverage`
field on `GET /api/v1/health`, and an `is_stale` field on bars responses for
cagg-served granularities. No new freshness logic is written — this slice is
entirely new call sites onto machinery that already exists and is already
proven in production use by `mt data status`.

## Value

**Client-facing (trading-ui, trading-engine):** an API consumer can currently
never distinguish "market closed, no new bars" from "cagg stalled days ago" —
both look identical (a `200` with the same or slightly-fewer bars). After this
slice, a client can check `is_stale` on a bars response or poll
`/api/v1/status` / `/api/v1/health` and know which case it is in, without
guessing from bar counts.

**Operational:** closes the gap the 167 design doc flagged and deferred:
*"the API may expose timestamps at full precision, where the up-to-4h
minute-side coarsening becomes visible... How to present that is [the API
slice]'s decision; 167 only documents it."* This slice is that decision.

## Technical Scope

**Included:**

- `src/manta_trading/api_server/routes/status.py` — new `GET /api/v1/status`
  route, wrapping `status_queries.fetch_status_rows_with_freshness` /
  `fetch_all_health_counts_with_freshness` (both already guarded per 167 D6).
- `src/manta_trading/api_server/routes/health.py` — add a `coverage` field to
  the response, computed via `status_coverage.check_coverage_freshness`.
- `src/manta_trading/api_server/routes/bars.py` — add an `is_stale` field to
  `BarsResponse`, computed via `cagg_freshness.assert_cagg_fresh` against the
  specific cagg view backing the requested granularity, for cagg-served
  granularities only.
- `src/manta_trading/api_server/models/responses.py` — `CoverageVerdict`,
  `CoverageStatus`, `StatusRowRecord`, `StatusResponse`; extend
  `HealthResponse` and `BarsResponse`.
- `src/manta_trading/api_server/app.py` — register the status router.
- Unit tests for all three surfaces, mocking at the same boundary the
  existing CLI staleness tests use (`FreshnessVerdict` / `CoverageFreshness`
  fixtures), not a live DB.

**Excluded:**

- Any change to `assert_cagg_fresh`, `check_coverage_freshness`,
  `status_coverage`, or `status_queries` — this slice is a pure consumer.
- `gaps` data on the status response — already served by
  `GET /api/v1/gaps/{symbol}` (slice 184); status stays focused on
  health + freshness (see D3).
- Auto-extend side effects — `mt data status` triggers
  `maybe_extend_trading_sessions` on every invocation; the API endpoint does
  not (see D4).
- Pagination / range caps on `/api/v1/status` — same "no pagination, caller
  requests bounded scope" policy as the rest of the API (180-arch, Range
  Policy). A default-scope decision is made below (D2) precisely so this
  doesn't need one.
- Bars range-cap policy, `openapi.json` version fix, pool timeout tuning —
  all slice 186.
- Coverage-cagg-backed `available` ranges — slice 187, which depends on this
  slice's `is_stale`/status groundwork existing first.

## Dependencies

### Prerequisites

- **[167]** — `data_status`, `status_coverage.check_coverage_freshness`,
  `status_queries.fetch_status_rows_with_freshness` /
  `fetch_all_health_counts_with_freshness`. Complete.
- **[168]** — `cagg_freshness.assert_cagg_fresh`. Complete.
- **[184]** — current state of `src/manta_trading/api_server/`: `app.py`,
  `deps.py` (`get_db`), `routes/{health,bars,symbols,gaps}.py`,
  `models/responses.py`. Not listed as a formal dependency in the 180 slice
  plan (only [167, 168] are), but this slice edits those files directly, so
  it necessarily starts from 184's delivered state. All four prior 180-series
  slices are complete.

### Interfaces Required

- `manta_trading.data.maintenance.status_coverage.check_coverage_freshness`,
  `CoverageFreshness`, `COVERAGE_VIEWS`
- `manta_trading.data.maintenance.status_queries.fetch_status_rows_with_freshness`,
  `fetch_all_health_counts_with_freshness`
- `manta_trading.market.maintenance.cagg_freshness.assert_cagg_fresh`,
  `FreshnessVerdict`, `StalenessSignal`
- `manta_trading.constants.GRANULARITY_SOURCE`, `CAGG_BASE_GRANULARITY`
- `manta_trading.api_server.deps.get_db` (existing pooled-connection
  dependency, already used by `health.py` and `symbols.py`)

## Architecture

### Component Structure

```
api_server/
    app.py                  # + register status_router
    deps.py                 # unchanged — get_db reused
    routes/
        health.py            # + coverage field
        bars.py               # + is_stale field, + db dependency
        status.py             # NEW
        symbols.py           # unchanged
        gaps.py               # unchanged
    models/
        responses.py          # + CoverageVerdict, CoverageStatus,
                               #   StatusRowRecord, StatusResponse;
                               #   extend HealthResponse, BarsResponse
```

`status.py` is a thin translation layer: it calls the existing
`status_queries` functions (which already call through
`status_coverage.query_data_status`, which already calls
`cagg_freshness.assert_cagg_fresh`) and maps their dataclass results
(`StatusRow`, `CoverageFreshness`, `FreshnessVerdict`) onto Pydantic response
models. No SQL lives in this route — consistent with the "thin wrapper, no
new business logic" principle in the 180-arch doc.

### Data Flow

**`GET /api/v1/status`:**
```
status.py route
  → status_queries.fetch_status_rows_with_freshness(conn, symbol, health_filter, granularity)
      → status_coverage.query_data_status  (guarded: asserts minute_coverage + daily_coverage fresh)
          → cagg_freshness.assert_cagg_fresh  (TTL-cached 60s per view)
  → status_queries.fetch_all_health_counts_with_freshness(conn)   # summary counts
  → map StatusRow[] + CoverageFreshness → StatusResponse
```

**`GET /api/v1/health`:**
```
health.py route
  → db.execute("SELECT 1")                     # existing
  → (only if db == "ok") status_coverage.check_coverage_freshness(db)
  → HealthResponse(status="ok", db=..., coverage="ok"|"stale"|None)
```

**`GET /api/v1/bars/{symbol}`:**
```
bars.py route
  is_cagg = CAGG_BASE_GRANULARITY[granularity] != granularity
  if is_cagg:
      asyncio.gather(
          run_in_executor(fetch bars via minute_db/daily_db),   # existing
          run_in_executor(cagg_freshness.assert_cagg_fresh(db, GRANULARITY_SOURCE[granularity])),
      )
  else:
      fetch bars only; is_stale = False   # M1/D1 are raw tables, not caggs
  → BarsResponse(..., is_stale=verdict is not None and not verdict.is_fresh)
```

### State Management

None. All freshness state is the existing process-local TTL verdict cache in
`cagg_freshness._VERDICT_CACHE` (168 D6), shared automatically by every
consumer in the same process — the API server gets the same 60s amortization
`mt data status` already relies on. No new caching is introduced by this
slice.

## Technical Decisions

### D1 — `GET /api/v1/status`: one route, `symbol` as an optional query param

The slice plan specifies a single endpoint token, `GET /api/v1/status` — no
`/status/{symbol}` variant. Considered mirroring `symbols.py`'s
list-endpoint/detail-endpoint split (`/symbols` + `/symbols/{symbol}`), but
rejected: that split exists because `/symbols` and `/symbols/{symbol}` return
structurally different resources (a list of summaries vs. one full
instrument). `/status` with or without `?symbol=` returns the same shape
(a `StatusResponse` with `rows` narrowed to one symbol) — exactly how the CLI
already models it (`--symbol` is a filter, not a different subcommand). One
route with an optional query param matches both the plan text and the CLI
precedent; a second route would be unrequested surface area.

Query parameters (mirroring `mt data status`'s existing flags, since this is
the same accessor):

| Param | Type | Default | Meaning |
|---|---|---|---|
| `symbol` | `str \| None` | `None` | Filter to one symbol (CLI `--symbol`) |
| `health` | `str \| None` | `None` | Comma-separated `OK,GAPS,STALE,FAILED` |
| `granularity` | `Literal["daily","minute"] \| None` | `None` | CLI `--daily`/`--minute` |
| `all` | `bool` | `False` | Include `OK` rows; overrides `health` (CLI `--all`) |

Default filtering matches the CLI default exactly: when `health` is omitted
and `all=false`, the health filter is `GAPS,STALE,FAILED` (the CLI's
`_VALID_HEALTH_VALUES` default) — non-`OK` rows only. This keeps the default
response bounded without inventing a new default policy; a client that wants
everything passes `?all=true` (matching `--all`), same tradeoff the CLI
already made and documented (`render_status_footer`'s advisory line).

**Handler is `async def` (resolves review F003).** `status.py` makes two
blocking DB calls per request (`fetch_status_rows_with_freshness`,
`fetch_all_health_counts_with_freshness`) — more DB work than `health.py`'s
single `SELECT 1`, which is why `health()` can stay a sync handler (D6) but
`status.py` cannot follow that precedent. `get_status` is `async def`, and
both calls are dispatched via `asyncio.gather(loop.run_in_executor(...),
loop.run_in_executor(...))`, identical in shape to `symbols.py`'s
`get_symbol`, which already runs its two DB fetches (`_fetch_minute_range`,
`_fetch_daily_range`) concurrently the same way. This follows the majority
`async def` + `run_in_executor` pattern this codebase uses for any route with
real DB work (`bars.py`, `symbols.py`), reserving the sync-handler exception
for the one route cheap enough not to need it.

### D2 — Response shape: `StatusResponse`, no pagination, bounded by the same filter defaults as the CLI

```python
class CoverageVerdict(BaseModel):
    view_name: str
    is_fresh: bool
    signals: list[str]              # StalenessSignal values
    lag_seconds: float | None
    threshold_seconds: float | None
    detail: str

class CoverageStatus(BaseModel):
    is_stale: bool
    verdicts: list[CoverageVerdict]  # one per coverage cagg: minute_coverage, daily_coverage

class StatusRowRecord(BaseModel):
    symbol: str
    granularity: str
    health: str
    bars_stored: int | None
    first_bar_ts: datetime | None
    last_bar_ts: datetime | None
    gap_count: int | None
    last_attempt_ts: datetime | None
    last_attempt_outcome: str | None
    target_end_ts: datetime | None
    effective_start: date | None

class StatusResponse(BaseModel):
    scope: Literal["symbol", "all"]
    symbol: str | None
    count: int
    rows: list[StatusRowRecord]
    summary: dict[str, int]          # health -> count, full-universe (unfiltered by health)
    coverage: CoverageStatus
```

`coverage.verdicts` carries exactly the two entries in
`status_coverage.COVERAGE_VIEWS` (`minute_coverage`, `daily_coverage`) — this
*is* the "per-granularity-family coverage freshness verdicts" the slice plan
asks for; no new grouping logic is needed because `status_coverage` already
groups at that granularity.

`summary` is always the full-universe health-count breakdown (unfiltered by
`health`/`symbol`), matching `StatusReport.summary`'s existing meaning in the
CLI — it answers "how healthy is the whole registry", independent of what
`rows` happens to show.

### D3 — Gaps are not embedded in the status response

The CLI's `StatusReport` includes a `gaps` list when `--symbol` is given.
This slice's `StatusResponse` deliberately omits it: `GET /api/v1/gaps/{symbol}`
already exists (slice 184) and is the authoritative gaps resource, with its
own filtering (`?granularity`, `?start`/`?end`). Duplicating that shape inside
`/status` would create two representations of the same rows with no single
source of truth for either. A client wanting both calls both endpoints.

### D4 — No auto-extend side effect on the API path

`mt data status` calls `maybe_extend_trading_sessions(..., bypass_gate=True)`
on every invocation — a write side effect (extending `trading_sessions`)
triggered by what looks like a read command, justified for an
operator-invoked CLI command run at human cadence. `GET /api/v1/status` is a
different consumer: a monitoring tool or UI dashboard may poll it every few
seconds. Wiring the same unconditional write into that path risks far more
frequent auto-extend attempts than the mechanism was designed for, for no
benefit to the staleness-surfacing goal of this slice. **Decision:** the API
endpoint does not call `maybe_extend_trading_sessions`. Auto-extend remains
exclusively a CLI concern (`mt data status`, `mt data --extend`).

### D5 — `/api/v1/status` with an unknown or empty-match symbol returns `200` with empty `rows`

Consistent with `GET /api/v1/gaps/{symbol}` (slice 184), not with
`GET /api/v1/symbols/{symbol}`. The distinction is what the endpoint
represents: `/symbols/{symbol}` asserts "this instrument exists" (404 is
correct — identity lookup). `/status` and `/gaps` return records *about* a
symbol under a filter; zero matching records is a valid, correct answer, not
an error — CLI already treats "no rows for these filters" as an informational
0-exit-code case, not a failure. `scope` in the response is always `"symbol"`
when `?symbol=` was passed, even with `rows: []`, so a client can distinguish
"filtered scope, nothing matched" from "all-registry scope."

### D6 — `GET /api/v1/health`: `coverage` reuses `status_coverage`, not a 7-view probe of every bar-serving cagg

The bars endpoint serves seven cagg-backed granularities (M5/M15/H1/H4/
W1/MO1/Q1). A maximally literal reading of "liveness probe also catches
serving-data staleness" would probe all seven on every health check. Rejected:
even with the 60s TTL cache, the first probe after cache expiry pays real cost
per view (167's measured range: ~0.19s–2.1s per probe, the high end from
`daily_ohlcv`'s pre-166-style chunk-catalog planning cost) — up to seven times
that on a cold cache is a bad shape for a liveness endpoint expected to be
cheap and fast.

**Decision:** `health.py` calls `status_coverage.check_coverage_freshness`
(exactly two probes: `minute_coverage`, `daily_coverage`) — the same signal
`mt data status` already surfaces, already TTL-cached, already the "single
guarded door" 167 D6 established. This is a coarse liveness signal ("is the
data pipeline generally healthy"), not a per-request guarantee about any one
granularity — that precision belongs to the bars endpoint's own `is_stale`
field (D7), which checks the *exact* cagg a given response was served from.
The two are complementary: `coverage` for cheap liveness polling, `is_stale`
for per-response truth.

`HealthResponse.coverage` is only populated when `db == "ok"` — if the DB is
already unreachable, "stale" is meaningless noise on top of a real outage:

```python
class HealthResponse(BaseModel):
    status: Literal["ok"]
    db: Literal["ok", "error"]
    coverage: Literal["ok", "stale"] | None = None
    detail: str | None = None
```

`health()` remains a **sync** route handler (unchanged from 181) — FastAPI
runs sync route functions in a worker thread pool automatically, so the
blocking `check_coverage_freshness` call needs no `run_in_executor` wrapping,
unlike the `async def` bars route (D7).

### D7 — Bars: `is_stale` for cagg-served granularities, probed against the exact serving cagg

**Cagg-served vs. raw** is defined by the existing constant, not a new list:
`CAGG_BASE_GRANULARITY[granularity] != granularity` (`False` only for `M1`
and `D1`, the two raw base tables; `True` for the other seven). This reuses
167/168's existing granularity-to-cagg mapping rather than restating it as a
second `_MINUTE_GRAINS`-style set.

For a cagg-served granularity, the probe target is `GRANULARITY_SOURCE[granularity]`
— the specific view the request was actually served from (e.g. `minute_5min_ohlcv`
for `5m`, `daily_monthly_ohlcv` for `1mo`), not the `data_status` coverage
caggs. This is a materially different question from D6/status: "is *this*
response's data current," not "is the pipeline generally healthy." Calling
`assert_cagg_fresh(conn, GRANULARITY_SOURCE[granularity])` with no
`source_table` override lets it resolve the raw table itself via
`GRANULARITY_SOURCE`/`CAGG_BASE_GRANULARITY` — exactly the seam it was built
for; this is a new *caller*, not new logic.

For `M1`/`D1` (raw tables), `is_stale` is always `False` — there is no cagg in
the path, so there is nothing to probe and no risk class to report.

```python
class BarsResponse(BaseModel):
    symbol: str
    granularity: str
    adjusted: bool
    is_stale: bool
    count: int
    bars: list[BarRecord]
```

Field placement is the response **body**, not a header. The codebase already
has one established spelling for this signal — `is_stale` as a boolean
property on `CoverageFreshness`/rendered in CLI JSON — and every existing
serialized-format consumer (JSON and msgpack) reads the body already; a
parallel header would be a second representation of the same fact with no
consumer needing header-only access (this is a LAN single-user tool, not a
CDN/proxy context where header-based cache invalidation would matter).

**Cost:** `assert_cagg_fresh` is TTL-cached per view for 60s (168 D6), so this
adds at most one extra ~0.2–2s probe per (view, 60s-window) — not per request
— and runs concurrently with the existing bars fetch via `asyncio.gather`
(both are already `run_in_executor`-wrapped blocking calls), so it does not
serialize behind the data fetch.

**NFR restatement (resolves review F004).** 180-arch states no formal bars
latency NFR (unlike 140-arch's explicit sub-second `data_status` target); the
API's only stated latency posture is qualitative — "single-user local network
tool," DB query latency "acceptable... without caching." Given that, this
slice sets an explicit budget rather than leaving the concern open: **on a
cache-hit (the steady-state case, any request to the same granularity within
60s of a prior one), the freshness check adds no measurable latency** — a
process-local dict lookup, not a DB round trip. **On a cache-miss, added
latency is bounded by the single slowest probe this slice issues, not by the
sum of the two `gather` branches** (they run concurrently), and per 167's
measured probe range (~0.19s–2.1s, the high end specific to a
pre-rechunk `daily_ohlcv` catalog-planning cost, not the minute caggs this
slice's granularities mostly hit) should not exceed **+2.5s** over the
existing bars baseline in the worst case. If implementation measurement on the
actual bars-serving caggs (`minute_5min_ohlcv` etc., not `daily_ohlcv`) shows
worse, that is new information for task breakdown to act on — e.g. a load-test
assertion — not a defect in this estimate, which is deliberately upper-bounded
by the worst measured case in the codebase today.

**`run_in_executor` requirement (project Python rules):** `get_bars` is
`async def`; `assert_cagg_fresh` issues blocking psycopg calls. Per the
project's async-correctness rule, it is wrapped in `run_in_executor` exactly
like the existing `minute_db`/`daily_db` calls — never called directly on the
event loop.

### D8 — `bars.py` gains a `db` dependency

`bars.py` currently depends only on `get_minute_db`/`get_daily_db` (direct
`TimescaleMinuteDataDB`/`TimescaleDailyDataDB` instances built from
`conninfo` at startup, not pool-backed). `assert_cagg_fresh` needs a
`psycopg.Connection`. **Decision:** add
`db: Annotated[psycopg.Connection, Depends(get_db)]` to `get_bars`, reusing
the existing pooled-connection dependency already used by `health.py` and
`symbols.py` — no new connection-management code.

### D8a — Implementation amendment: `bars.py` depends on the *pool*, not a checked-out connection

**Recorded 2026-08-03, after the code review.** D8 specifies adding
`db: Annotated[psycopg.Connection, Depends(get_db)]` to `get_bars`. That was
implemented as written and is **wrong**: FastAPI resolves `get_db` for the whole
request, and `get_db` holds a pooled connection for as long as the route runs.
So every bars request checked out one of the pool's 8 connections for its entire
duration — including `1m`/`1d`, which issue no probe and never touch it. Bars
requests are the slowest endpoint (measured 2–4s on a 5-year daily range), so
this converted a previously pool-free endpoint into the pool's heaviest consumer.

Measured on prod 2026-08-03, `/api/v1/health` latency while N bars requests
(`granularity=1d`, which never uses the connection) ran concurrently:

| Concurrent bars requests | `/health` latency |
|---|---|
| 0 | 0.010s |
| 4 | 0.009s |
| 7 | 0.009s |
| **8** (= `max_size`) | **1.83s** |
| 10 | 2.35s |
| 16 | (not measured pre-fix) |

The cliff at exactly `max_size=8` identifies pool exhaustion rather than
DB-side contention. Every other endpoint depends on `get_db`, so a burst of
bars traffic could stall all of them.

**Fix:** `get_bars` now depends on the new `deps.get_db_pool`, which returns
`app.state.db_pool` without checking anything out, and the checkout is scoped to
the probe itself inside `_probe_freshness` (`with pool.connection() as conn:`).
A raw-granularity request now checks out **no** connection at all; a cagg
granularity checks out exactly one, for the probe's duration only. Re-measured
after the fix: `/health` stays at 0.009s under 8 and 16 concurrent bars
requests. Two unit tests pin the behavior
(`test_raw_granularity_checks_out_no_connection`,
`test_cagg_granularity_checks_out_exactly_one_connection`).

`get_db` itself is unchanged and still correct for `health`/`status`/`symbols`/
`gaps`, which use their connection for essentially their whole runtime; its
docstring now warns about the whole-request hold. Pool *sizing* remains slice
186's call (D10) — this amendment removes an artificial consumer rather than
tuning the pool.

### D9 — Failure modes on the new I/O paths (resolves review F001)

Three new I/O call sites are added: `status.py`'s two `status_queries` calls,
`health.py`'s `check_coverage_freshness` call, and `bars.py`'s
`assert_cagg_fresh` call. Each is addressed explicitly rather than left to
inherited behavior:

**Freshness probes never raise on I/O failure.** Every statement
`cagg_freshness.py` issues is bounded by `CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT`
(10s, existing constant) via `_set_probe_timeout`, and `_evaluate` wraps all
four probe calls in `try/except psycopg.Error`, converting a timeout,
connection drop, or any other DB error into a `PROBE_FAILED` signal — a
`FreshnessVerdict` with `is_fresh=False`, not an exception (168 D3:
"indeterminate freshness is stale"). This is existing, unchanged behavior;
`check_coverage_freshness` and the new bars-side `assert_cagg_fresh` call both
inherit it directly. Neither can hang past 10s per probe, and neither can
raise from a probe failure — a stale verdict is the worst case, not a crash.

**The one exception this slice's new code can propagate is a genuine data-query
failure** — `status_coverage.query_data_status`'s actual `cur.execute(sql,
params)` against `data_status` (not the freshness probe, which already ran and
returned by that point). This is unguarded by design in the existing 167 code
(`query_data_status` does not wrap its own query execution), and this slice
does not add a try/except around it either: it propagates to the existing
global `@app.exception_handler(Exception)` registered in `app.py` (slice 184),
which logs the full traceback at ERROR and returns a sanitized `500`. This is
the same contract `bars.py` and `symbols.py` already rely on for their own DB
calls (184's design doc: *"Existing route audit: `bars.py` and `symbols.py`
let DB errors propagate as unhandled exceptions today. The new 500 handler
catches them uniformly — no per-route changes needed."*). `status.py` follows
the identical pattern; no new exception handling is introduced.

**`asyncio.gather` in bars (D7) does not need `return_exceptions=True`.**
Given the probe-never-raises guarantee above, the only way the freshness task
in `gather` can raise is `assert_cagg_fresh`'s `ValueError` for an unrecognized
`view_name` — which cannot occur here, since the argument is always
`GRANULARITY_SOURCE[granularity]` for a `granularity` already validated by
FastAPI's `Granularity` enum binding, i.e. always a known key. Practically,
the freshness branch of `gather` cannot raise in production; if it somehow did
(a genuine code bug, not an I/O condition), default `gather` semantics
(propagate, cancel the sibling task) are correct — the same global 500 handler
catches it, and failing the whole request is the right behavior for a bug in
this slice's own code, as opposed to a DB hiccup, which never reaches this
path per the guarantee above.

### D1a — Implementation amendment: `status.py`'s two DB calls run sequentially, not gathered

**Recorded 2026-08-03 during implementation. This is the slice's only deviation
from D1–D10.** D1 specifies that `get_status` dispatch
`fetch_status_rows_with_freshness` and `fetch_all_health_counts_with_freshness`
concurrently via `asyncio.gather(loop.run_in_executor(...), ...)`, by analogy
with `symbols.py::get_symbol`. The handler stays `async def` and both calls
still run off the event loop in an executor, but they run **in order inside a
single `run_in_executor`**. The analogy to `symbols.py` does not hold, for two
reasons found by reading the code rather than by measurement:

1. **The concurrency is not real.** `symbols.py` and `status.py` both issue
   their two calls on the *same* pooled connection, and `psycopg.Cursor.execute`
   holds `self._conn.lock` for the duration of every statement (verified in
   psycopg 3's source). Two executor threads on one connection therefore
   serialize at the driver, so `gather` buys no parallelism here. (`bars.py`'s
   `gather` in D7 is unaffected and stays as designed — its two branches use
   *different* connections: the pooled `db` for the probe and the
   `minute_db`/`daily_db` instances for the data fetch.)
2. **It introduces a session-state race.** Both calls pass through
   `check_coverage_freshness` → `assert_cagg_fresh`, which on a cache miss does
   `SHOW statement_timeout` → `SET statement_timeout = '10s'` → restore, on the
   caller's connection (168's `_read_statement_timeout`/`_set_probe_timeout`/
   `_restore_probe_timeout`). Concurrently on one connection these interleave:
   the second thread can read the *probe's* 10s as the "prior" value and restore
   that instead of the pool's configured 300s, leaving a pooled connection
   clamped to 10s for every later request that checks it out.

Sequential execution also preserves an amortization the CLI documents and
relies on (`cli/commands/data.py`, the comment above its own two calls): the row
fetch warms slice 168's TTL verdict cache, so the health-count fetch's guard is
a dict lookup rather than a second cold probe. Gathering them would have both
threads probe cold simultaneously, doubling the probe work.

No other decision changed. The handler's `async def` shape, the response
contract, the filter semantics, and the no-try/except posture (D9) are as
designed.

### D10 — Explicit `bars.py` / `responses.py` ownership boundary with slice 186 (resolves review F002)

Both this slice and 186 touch `bars.py` and `responses.py`, and 186 was not
designed yet at the time this doc was written, so the boundary is stated here
rather than left implicit:

| File / region | Owned by 185 | Owned by 186 |
|---|---|---|
| `app.py::_configure_connection` (pool `statement_timeout`, `work_mem`) | — | Yes — different file/function entirely, no overlap |
| `get_bars`'s dependency list | Adds `db: Depends(get_db)` (D8) | — |
| `get_bars` body, freshness branch | Adds the `is_cagg` check, `asyncio.gather` freshness probe (D7) | — |
| `get_bars` body, range/pagination enforcement | — | Adds range-cap/pagination policy and the 404-vs-empty-window contract decision (186's plan entry, item 3) |
| `models/responses.py::BarsResponse` | Adds `is_stale: bool` field | — |
| OpenAPI `version` field, `openapi.json` artifact | — | Yes (186's plan entry, item 2/4) |

Both slices touch the *same function* (`get_bars`) but in different, additive
regions — 185's changes are the connection dependency and a new branch before
the response is built; 186's are the range/pagination checks on the
`start`/`end`/`granularity` inputs, logically upstream of both. No field or
line is claimed by both.

**Sequencing consequence:** since 185 is being implemented first (PM
decision, this session), 186's task breakdown must diff against 185's landed
`bars.py`/`responses.py`, not the pre-185 version — its `is_stale` field and
`db` dependency should be treated as already present, not something 186
reintroduces or conflicts with. If 186 implementation starts from a worktree
branched before 185 merges, it must rebase onto 185's landed state before
touching `bars.py`.

## Cross-Slice Dependencies and Interfaces

### Consumes from Other Slices

- **[167]** `status_coverage`, `status_queries` — consumed unchanged, per D6
  of that slice's own design ("182 is contractually required to use it" —
  this slice is the first API-side consumer to actually land that
  requirement for `data_status` reads).
- **[168]** `cagg_freshness.assert_cagg_fresh` — consumed unchanged, as a new
  direct caller for per-granularity bars freshness (D7), distinct from the
  `status_coverage`-mediated path.

### Provides to Other Slices

- **[187]** (symbols ranges via coverage caggs + load-test tier) depends on
  `[167, 185]` per the slice plan. 187's own freshness-guarded fallback
  behavior for `available` ranges can follow this slice's D6/D7 precedent for
  how a `is_stale`/`"ok"|"stale"` signal is surfaced on a serving-API
  response, rather than establishing a third convention.
- No new interface contract is exposed beyond the three response-model
  additions (`StatusResponse`, `HealthResponse.coverage`,
  `BarsResponse.is_stale`) — these are additive fields/routes; no existing
  field changes shape or meaning.

## Success Criteria

### Functional Requirements

1. `GET /api/v1/status` returns a `StatusResponse` with `rows`, `summary`,
   and `coverage.verdicts` (exactly 2 entries: `minute_coverage`,
   `daily_coverage`).
2. `GET /api/v1/status?symbol=SPY` narrows `rows` to that symbol and sets
   `scope="symbol"`; an unknown symbol returns `200` with `rows: []`, not
   `404`.
3. `GET /api/v1/status?health=OK` / `?all=true` / `?granularity=daily`
   filter identically to the equivalent `mt data status` flags.
4. `GET /api/v1/health` includes `coverage: "ok"` when both coverage caggs
   are fresh, `"stale"` when either is not, and `null`/absent when
   `db == "error"`.
5. `GET /api/v1/bars/{symbol}?granularity=5m` (or any of M5/M15/H1/H4/W1/
   MO1/Q1) includes `is_stale: true` when the specific serving cagg for that
   granularity is stale, `false` when fresh.
6. `GET /api/v1/bars/{symbol}?granularity=1m` and `?granularity=1d` always
   return `is_stale: false` — no probe is issued for raw-table granularities.
7. `mt data status` output and behavior are byte-for-byte unchanged — this
   slice adds callers, it does not touch `status_coverage`, `status_queries`,
   or `cagg_freshness`.

### Technical Requirements

- `ruff` and `pyright` (strict) report zero new errors on touched files.
- Unit tests for all three surfaces run without a live DB, mocking at the
  `FreshnessVerdict`/`CoverageFreshness` boundary — the same pattern
  `test/unit/cli/commands/test_data_status_coverage.py` already establishes,
  not a re-implementation of it.
- Existing `test/unit/api_server/test_bars.py` cases that exercise
  cagg-served granularities are updated to override `get_db` (new dependency
  from D8); cases exercising `M1`/`D1` are unaffected since no probe runs.

### Verification Walkthrough

**Prerequisites:** `MT_TIMESCALE_DB_URL` set; server not running.

**Executed 2026-08-03** against production `trading` @ 192.168.1.144 (PM-approved;
steps 1–6 are read-only and issue the same `data_status` reads and
`assert_cagg_fresh` probes `mt data status` already performs). Steps 1–6 and 8
passed as written. Step 7 was **not run** — see the note under it. Observed
outputs below are from that run; row counts and health totals are point-in-time
and will differ on re-run.

1. **Start the server:**
   ```bash
   uv run mt serve --host 127.0.0.1 --port 8100
   ```
   The walkthrough binds loopback explicitly; `mt serve`'s default is
   `0.0.0.0:8100`, which is unnecessary exposure for a verification run.
   Startup is complete when the log reads `Application startup complete.`

2. **Status, full registry (default filter — non-OK only):**
   ```bash
   curl -s http://127.0.0.1:8100/api/v1/status | python3 -m json.tool | head -30
   ```
   Expect `coverage.verdicts` with 2 entries, `coverage.is_stale: false` on a
   healthy DB, `summary` with all four health keys. Observed:

   ```
   scope: all | symbol: None | count: 51915
   summary: {'FAILED': 1822, 'GAPS': 558, 'STALE': 49535, 'OK': 11309}
   coverage.is_stale: False | verdicts: 2
      minute_coverage is_fresh=True lag_s=0.0 thr_s=100800.0
      daily_coverage  is_fresh=True lag_s=0.0 thr_s=90000.0
   ```

   **Caveat — default response size.** The CLI-matching default filter
   (non-`OK` rows) returned 51,915 rows against the production registry, so
   "bounded by the same filter defaults as the CLI" (D2) bounds the *filter*,
   not the payload. This is the documented no-pagination policy (180-arch,
   Range Policy), not a defect, but a client polling `/status` should pass
   `?symbol=` or `?health=` rather than relying on the default to stay small.
   Piping through `head -30` matters here.

3. **Status, single symbol:**
   ```bash
   curl -s "http://127.0.0.1:8100/api/v1/status?symbol=SPY&all=true"
   ```
   Expect `scope: "symbol"`, `rows` containing SPY's daily and minute rows.
   Observed: `scope: symbol`, `count: 2` — `SPY daily OK` and
   `SPY minute FAILED` — in 0.22s.

   Two contract details from D1/D5 verified at the same time:
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" \
     "http://127.0.0.1:8100/api/v1/status?symbol=NOSUCHSYM"      # → 200 (D5)
   curl -s -w "\n%{http_code}\n" \
     "http://127.0.0.1:8100/api/v1/status?symbol=SPY&health=BOGUS"
   # → 422 {"detail":"Invalid health values: BOGUS. Valid: FAILED, GAPS, OK, STALE"}
   ```

4. **Health, freshness field:**
   ```bash
   curl -s http://127.0.0.1:8100/api/v1/health
   ```
   Expect `{"status":"ok","db":"ok","coverage":"ok"}` on a healthy DB.
   Observed exactly that, in 0.008s — but **only because step 2 had already
   warmed the coverage verdict cache**. Measured separately on a fresh server
   process, warming the pool first (via `/api/v1/symbols?search=SPY`, 0.15s) so
   the number isolates the probe:

   | `/api/v1/health` | Latency |
   |---|---|
   | Cold verdict cache (first call in a process, or first after the 60s TTL) | **3.19s** |
   | Warm verdict cache | 0.010s |

   Pre-185 this endpoint was a bare `SELECT 1`. D6 chose two coverage probes
   over seven bar-cagg probes specifically to keep the liveness endpoint cheap;
   the two probes still cost ~3.2s cold, above the ~0.19–2.1s per-probe range
   167 measured and above the +2.5s budget D7 set for bars. Consequences a
   monitoring configuration has to account for: any liveness probe timeout
   below ~4s will trip on a cold cache, and a poller running slower than the
   60s TTL pays 3.2s on **every** call. Flagged to the PM as new information
   (design's own D7 language) — not a defect, but the D6 cost argument is
   weaker in practice than on paper.

5. **Bars, staleness field on a cagg-served granularity:**
   ```bash
   curl -s "http://127.0.0.1:8100/api/v1/bars/SPY?granularity=5m&start=2024-01-01&end=2024-01-31" \
     | python3 -c "import sys,json; d=json.load(sys.stdin); print('is_stale:', d['is_stale'])"
   ```
   Expect `is_stale: False` on a healthy DB. Observed `is_stale: False`,
   `count: 3744`.

   **Measured cold-cache cost against the D7 budget (≤ +2.5s).** Cold miss is
   the first request for a given view in a fresh server process; warm is a
   repeat inside the 60s TTL window:

   | Granularity | Probe? | Cold | Warm | Probe cost |
   |---|---|---|---|---|
   | `5m` (`minute_5min_ohlcv`) | yes | 0.399s | 0.130s | ~0.27s |
   | `1mo` (`daily_monthly_ohlcv`) | yes | 1.327s | 0.052s | ~1.28s |
   | `1d` (raw `daily_ohlcv`) | no | 3.876s | 2.090s | n/a |

   Both probed granularities land inside the budget, the daily-family one
   (measured against raw `daily_ohlcv`, the expensive case 167 clocked at
   ~2.1s) with ~1.2s of headroom. No escalation to the PM was required. Note
   the `1d` row: it issues **no** probe yet is the slowest of the three in both
   states — that variance is the `daily_ohlcv` data fetch, and it predates this
   slice. Do not read it as freshness-probe cost.

6. **Bars, raw granularity never probes:**
   ```bash
   curl -s "http://127.0.0.1:8100/api/v1/bars/SPY?granularity=1d&start=2024-01-01&end=2024-01-31" \
     | python3 -c "import sys,json; d=json.load(sys.stdin); print('is_stale:', d['is_stale'])"
   ```
   Expect `is_stale: False` unconditionally. Observed `is_stale: False` for
   both `1d` (`count: 21`) and `1m`.

7. **Induced staleness (per `user/runbooks/cagg-maintenance-pausing.md`), on a
   disposable test DB — not prod:**
   - Pause the refresh policy backing `minute_5min_ohlcv` (or `minute_coverage`
     via runbook R2b's job-catalog route).
   - Advance raw data past the threshold, or wait past
     `MAX_COVERAGE_SOURCE_STALENESS`.
   - Re-run steps 2, 4, 5 against that DB: `coverage.is_stale`/`coverage`/
     `is_stale` all report stale; rows are still returned (report, don't
     refuse — D3a inherited from 167/168).
   - Resume the policy; confirm all three surfaces report fresh again after
     the 60s TTL window elapses.

   **NOT RUN (2026-08-03, PM decision).** No suitable database exists: prod is
   off-limits for pausing a refresh policy, and `trading_test` is unsuitable
   because its views are plain views rather than caggs, so there is no policy
   to pause and no verdict to induce. Consequence: the stale path on all three
   surfaces is verified in unit tests (which inject stale `FreshnessVerdict`s
   at the `assert_cagg_fresh`/`check_coverage_freshness` boundary) but **not**
   end to end against a real stalled cagg. Filed as future work item 3 on
   `user/architecture/180-slices.data-serving-api.md` — a representative
   disposable test DB with real caggs and policies over a small data subset.
   Re-run this step once that exists.

8. **Unit tests:**
   ```bash
   uv run pytest test/unit/api_server/ -q
   ```
   Expect all existing tests plus new `test_status.py` cases to pass. Observed:
   55 passed (30 pre-slice + 25 added). Full `uv run pytest test/unit -q`:
   1603 passed, 10 skipped, 35 errors — every error is a pre-existing
   `psycopg.OperationalError: failed to resolve host '<db-host>'` in
   DB-requiring tests under `test/unit/universe/` and
   `test/unit/cli/commands/test_data_universes.py`, unrelated to this slice
   and present on `main`.

## Risk Assessment

### Technical Risks

- **Cold-cache probe latency on bars responses.** The first request for a
  given cagg-served granularity after the 60s TTL expires pays the probe cost
  (167 measured up to ~2.1s on `daily_ohlcv` pre-rechunk) concurrently with
  the data fetch. Mitigated by `asyncio.gather` (D7) so it does not add
  serially, and by the existing TTL cache amortizing repeat requests. If this
  proves user-visible at implementation time, the daily-rechunk follow-up
  filed under 167 (not this slice's scope) is the structural fix.

### Mitigation Strategies

- No mitigation beyond the above is needed at Low risk per the slice plan's
  own rating — flagged here only so task breakdown can add a latency
  assertion if the implementer judges it warranted, not as a mandatory task.

## Implementation Notes

### Development Approach

Suggested order: (1) response models in `responses.py`; (2) `status.py` route
(purely additive, lowest risk, no existing-file behavior change); (3) health
`coverage` field; (4) bars `is_stale` field + `db` dependency (touches the
most existing tests). Each is independently testable and independently
committable.

### Special Considerations

- `check_coverage_freshness`/`assert_cagg_fresh` both log at ERROR on a stale
  verdict (167/168's existing behavior) — every API request that trips a
  stale check will also produce a server-side ERROR log line. This is
  intentional and inherited, not new noise introduced by this slice; no
  change to logging behavior is in scope.
