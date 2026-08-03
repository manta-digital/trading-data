---
docType: slice-design
slice: api-client-contract-hardening
project: trading-data
parent: user/architecture/180-slices.data-serving-api.md
dependencies: [184, 185]
interfaces: [187]
dateCreated: 20260803
dateUpdated: 20260803
status: not_started
effort: 2
---

# Slice Design: API Client-Contract Hardening

## Overview

The serving API is functionally complete (181–185) and about to acquire real
clients. This slice closes the gap between "the endpoints work" and "the
contract is safe to hand to a client team": it bounds what a single request can
cost the server, removes two client traps in the bars contract (404-on-weekend,
unbounded ranges), makes the published schema truthful and reviewable, and
records the security posture as a decision rather than an omission.

Every item traces to the 2026-07-31 API evaluation, captured in the slice-plan
entry. Nothing here is new capability — it is contract, limits, and metadata on
surfaces that already exist.

## Value

**For the client team:** a bars request either succeeds within a bounded payload
or fails with a message that says exactly what to change. A `404` starts meaning
"no such symbol" instead of "no such symbol, or you asked for a weekend." Every
error this codebase raises has one body shape. The schema they code against is a
committed file they can diff, not a live endpoint that drifts.

**Operationally:** a single misdirected request (20 years of `1m`) currently
serializes millions of rows through one executor thread under a 300 s statement
timeout, on a host shared with the acquisition daemon. After this slice it is
rejected before a connection is checked out, and the session settings the API
uses are sized for interactive serving rather than inherited from the
bulk-analytics DB classes.

## Technical Scope

**Included:**

- `src/manta_trading/constants.py` — new named constants: API session settings,
  bars range-cap ceiling and its derivation inputs (the **defaults**, with their
  derivations documented).
- `src/manta_trading/config/__init__.py` — two operator-overridable settings,
  `MT_API_MAX_BARS_PER_REQUEST` and `MT_API_STATEMENT_TIMEOUT`, each defaulting
  to the constant above it (D9).
- `src/manta_trading/version.py` (new, ~10 lines) — `package_version()` helper,
  shared by the CLI and the API.
- `src/manta_trading/market/timescale_minute_db.py`,
  `timescale_daily_db.py` — accept an optional session-settings argument;
  defaults preserve today's behavior exactly.
- `src/manta_trading/api_server/app.py` — API-sized session settings on all
  three pools; OpenAPI `version` from package metadata; unified error-body
  handling.
- `src/manta_trading/api_server/routes/bars.py` — range admission check,
  `start > end` rejection, empty-window contract change.
- `src/manta_trading/api_server/routes/status.py` — its two `422`s emit the
  unified error body.
- `docs/api/openapi.json` (new) + `scripts/dump_openapi.py` (new) — committed
  schema artifact and its regenerator; README pointer.
- `test/unit/api_server/` — tests for the cap, the empty-window contract, the
  error bodies, the version, and schema-artifact drift.

**Excluded:**

- Pagination (`limit`/`cursor`) — D4 rejects it; the arch doc's no-pagination
  stance stands.
- Auth, rate limiting, caching — D8 records the posture; no code.
- Pool *sizing* changes — D2 defers to 187, which builds the load-test tier that
  can actually measure them.
- Background refresh of coverage verdicts — Future Work 902 / issue #8.
- `symbols`/`gaps` response semantics — untouched apart from error bodies.

---

## Technical Decisions

### D1 — Session settings reach all three pools, via a plumbed argument

**The stated problem is bigger than the stated fix.** The slice-plan entry names
`app.py::_configure_connection` as the place to tighten `statement_timeout` and
`work_mem`. That function governs only `app.state.db_pool`. The API process
actually opens **three** pools, and the bars data path — the one that motivated
the item — does not use the one named:

| Pool | Opened by | min/max | `statement_timeout` | `work_mem` | Serves |
|---|---|---|---|---|---|
| `app.state.db_pool` | `app.py` lifespan | 2 / 8 | 300 s | 512 MB | health, status, symbols, gaps, bars freshness probe |
| `TimescaleMinuteDataDB._pool` | class `__init__` | 4 / 10 | 300 s | 512 MB | **bars**, minute family |
| `TimescaleDailyDataDB._pool` | class `__init__` | 2 / 8 | 300 s | 512 MB | **bars**, daily family |

Both DB classes are constructed with a conninfo in the lifespan hook
(`app.py:72-73`) and open their own pools with their own `_configure_connection`.
Changing only `app.py` would leave every bars query at 300 s and 512 MB — the
opposite of the item's intent.

**Decision:** both DB classes gain an optional session-settings argument,
defaulting to today's values so the CLI and daemon are bit-for-bit unchanged;
the API constructs them with serving-sized values.

```python
# manta_trading/constants.py
@dataclass(frozen=True)
class DbSessionSettings:
    work_mem: str
    statement_timeout: str

DB_BULK_SESSION = DbSessionSettings(work_mem="512MB", statement_timeout="300s")
API_SERVING_SESSION = DbSessionSettings(work_mem="64MB", statement_timeout="20s")
```

```python
# api_server/app.py lifespan
app.state.minute_db = TimescaleMinuteDataDB(conninfo, session=API_SERVING_SESSION)
app.state.daily_db  = TimescaleDailyDataDB(conninfo,  session=API_SERVING_SESSION)
```

The classes' other session settings (`max_parallel_workers_per_gather`,
`enable_partitionwise_aggregate` on the minute class) are **not** parameterized —
they are not being tuned, and widening the seam past the two values in question
buys nothing.

**Why 20 s.** Every serving read path is sub-second to a few seconds after
163/166/167: bars `1d` over five years measured 2–4 s (185), a cold `/health`
coverage probe 3.19 s, the freshness probe is independently capped at
`CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT` (10 s). With D4's range cap admitting
at most ~75 k bars, 20 s is a comfortable multiple of the worst legitimate
request and still fails fast enough to be a real limit. It is operator-settable
(D9) — the two knobs interact, so raising the cap without room in the timeout
would trade a fast `422` for a slow `500`. The freshness guard's
save/restore (168) reads the session value and puts it back, so under this
change it restores 20 s instead of 300 s — correct, and the probe's own 10 s
still nests inside.

The walkthrough measures the four slowest legitimate calls on prod. **If any
exceeds 8 s (40 % of the budget), the constant is raised and the measurement
recorded in this document** — the value is derived from measurement, not
asserted.

**Why 64 MB.** `work_mem` is allocated *per sort/hash/materialize node per
query*, not per connection, so 512 MB × 26 connections understates the ceiling
rather than overstating it. 512 MB was chosen for bulk COPY and universe-wide
aggregation; a single-symbol windowed read sorts a bounded row set. 64 MB is
still ample for a 75 k-row sort and cuts the worst case by 8×.

### D2 — Pool sizing is not changed here

185 D8a left sizing to this slice. Three pools with a combined `max_size` of 26
and `min_size` of 8 is more than a single-user API needs, but every candidate
change (shrink the class pools, share one pool across all three consumers)
trades concurrency for memory with **no measurement to justify a direction**.
Slice 187 adds `tests/load/` for `api_server`; sizing is decided there, against
numbers. This slice records the topology (D1's table) so 187 starts from fact.
Deliberate non-change, not an oversight.

### D3 — OpenAPI `version` from package metadata

`create_app()` hardcodes `version="0.1.0"`; the distribution is at **0.7.3**
(the slice-plan entry says 0.5.0 — it was written earlier). Two versions in the
repo is one too many.

`cli/app.py:41-49` already resolves this correctly. Rather than copy the
try/except, extract it once:

```python
# manta_trading/version.py
def package_version() -> str:
    """Installed distribution version, or "dev" when metadata is absent."""
    try:
        return importlib.metadata.version(DISTRIBUTION_NAME)
    except importlib.metadata.PackageNotFoundError:
        logger.warning(
            "No installed distribution metadata found for %r; reporting 'dev'.",
            DISTRIBUTION_NAME,
        )
        return "dev"
```

Both `cli/app.py::_version_callback` and `create_app()` call it. `"dev"` is a
logged, obviously-placeholder value for an editable checkout without metadata —
not a silent fallback.

### D4 — Bars range cap: pre-query admission check, `422`, no pagination

A bare 20-year `1m` request is admitted today and serializes unbounded rows
through one executor thread. **Decision: reject before any DB work**, based on an
estimate computed from the request alone.

Pagination was considered and rejected: it changes `BarsResponse`, adds cursor
state and a client loop, and reverses the arch doc's explicit no-pagination
stance — all to serve a request shape (multi-year minute data over HTTP) that
should go through a direct DB connection anyway.

Post-query truncation was rejected outright: silently returning fewer bars than
the window contains is the failure mode this project's rules exist to prevent.

**The estimate.** `estimated_bars = span_calendar_days × bars_per_trading_day ×
(252/365)`, all derived from constants, none hardcoded per granularity:

```python
INTRADAY_MINUTES_PER_TRADING_DAY: int = 960
GRANULARITY_BAR_MINUTES: dict[Granularity, int] = {M1: 1, M5: 5, M15: 15, H1: 60, H4: 240}
TRADING_DAYS_PER_CALENDAR_DAY: float = 252 / 365
API_MAX_BARS_PER_REQUEST: int = 75_000  # default; override via MT_API_MAX_BARS_PER_REQUEST (D9)

BARS_PER_TRADING_DAY: dict[Granularity, float] = {
    **{g: INTRADAY_MINUTES_PER_TRADING_DAY / m for g, m in GRANULARITY_BAR_MINUTES.items()},
    Granularity.D1: 1.0, Granularity.W1: 1 / 5,
    Granularity.MO1: 1 / 21, Granularity.Q1: 1 / 63,
}
```

**`INTRADAY_MINUTES_PER_TRADING_DAY = 960` is measured, not the 390-minute
regular session.** Queried on prod 2026-08-03 for 2024-06-10: the store covers
**08:00–23:59 UTC** (16 h — EODHD US intraday includes extended hours). AAPL
returned 960 `1m` bars that day (a full dense session), SPY 724 (sparse
minutes), SPY 187 `5m` bars against a 192-bucket ceiling. The dense case is the
one a cap must survive, so 960 is the right input.

Resulting maximum spans at the 75 k default (derived in code; approximate here):

| Granularity | bars / trading day | max span |
|---|---|---|
| `1m` | 960 | ~113 days (~3.7 months) |
| `5m` | 192 | ~565 days (~1.5 years) |
| `15m` | 64 | ~1,697 days (~4.6 years) |
| `1h` | 16 | ~6,790 days (~18 years) |
| `4h` | 4 | ~27,150 days |
| `1d` and coarser | ≤ 1 | ~108,600 days and up — never binds |

The cap therefore binds `1m`, `5m`, and `15m` and is invisible everywhere else.

**How 75 k was chosen.** The policy was approved at 50 k on the assumption of a
390-minute regular session, which would have given `1m` ~6 months. The measured
960-bar day cuts that to ~75 days. 75 k is the agreed compromise (PM,
2026-08-03): `1m` reaches ~113 days — comfortably more than a single call needs
for a 3-month chart — at roughly 8–10 MB JSON / 3.5–4 MB msgpack for a worst-case
dense response. D9 makes it an operator setting, so this number is a starting
point rather than a commitment.

**Semantics.** The check is a request-admission policy on the *window*, not a
promise about `count` — a sparse symbol over an admitted span returns far fewer
bars. It is deliberately computed without touching the DB, so a rejected request
costs one comparison.

**Also rejected here: `start > end`.** A reversed range currently returns an
empty frame and (today) a misleading `404`. It becomes an explicit `422`.

Both checks run before the executor dispatch in `get_bars`, after FastAPI's own
type validation.

### D5 — `404` means "unknown symbol"; an empty window is `200`

Today `get_bars` raises one `404` for two unrelated conditions
(`bars.py:111-115`): the symbol does not exist, and the symbol exists but has no
bars in the window. A weekend is indistinguishable from a typo.

**Decision:** when the frame is empty, look the symbol up in `instruments`
(`symbols.py::_INSTRUMENT_SQL` is the existing precedent — a primary-key seek).
Unknown → `404`. Known → `200` with `count: 0, bars: []`.

| Case | Before | After |
|---|---|---|
| Known symbol, bars in window | 200 | 200 |
| Known symbol, empty window (weekend, pre-listing) | **404** | **200**, `count: 0` |
| Unknown symbol | 404 | 404 |

The lookup runs **only on the already-empty path**, so a normal response pays
nothing for it, and it takes its connection from `get_db_pool` with the checkout
scoped to the query — the pattern 185 D8a established for exactly this reason.

`is_stale` is still populated on an empty `200` response: "no bars *and* the
cagg is stale" is precisely the case a client needs to tell apart from "no bars
because the market was closed."

This is a **breaking change** to the 182 contract. It ships with the regenerated
`openapi.json`, a CHANGELOG entry, and a README note.

### D6 — One error-body shape for every error this codebase raises

Three shapes are in circulation: `{"error": …}` (404/500 handlers, 184),
`{"detail": [{…}]}` (FastAPI validation), `{"detail": "…"}` (status route's two
422s, 185 D1b). 185 flagged this and left it unowned.

**Decision:** every error raised by *this codebase* emits `{"error": "<message>"}`
— 404, 500, the status route's 422s, and D4's new 422s. FastAPI's own
`RequestValidationError` body stays native and is documented as the one
deliberate exception.

Implementation: extend the existing `HTTPException` handler in `create_app()`
from its 404-only special case to all `HTTPException`s, emitting
`{"error": str(exc.detail)}`. The `Exception` handler already emits
`{"error": …}` and is unchanged.

Why keep FastAPI's validation body: it carries per-field `loc`/`msg` that a
flattened string loses, it is what every FastAPI-aware client and code generator
expects, and overriding it means re-implementing a structure to carry the same
information. Two shapes, both documented in the committed schema and the README,
beats one shape that is worse at its job.

`HealthResponse.detail` is untouched — it is a field on a `200` body, not an
error envelope.

### D7 — Committed `openapi.json` with a drift test

`docs/api/openapi.json` is generated by `scripts/dump_openapi.py`
(`create_app().openapi()` — schema generation does not enter the lifespan, so no
DB is needed) and committed. The README's API section points at it.

The artifact is guarded by `test/unit/api_server/test_openapi_artifact.py`:

1. The committed document equals the generated one **ignoring `info.version`**.
2. The generated `info.version` equals `package_version()` (D3).

Splitting the assertion keeps a routine version bump from failing an unrelated
test run while still failing any *shape* drift. The artifact is regenerated at
slice close and whenever the schema changes; the committed `info.version`
records the release it was generated at.

Note: CI (`.github/workflows/ci.yml`) is publish-on-tag only and runs no test
job — this test gates in the local suite, like every other test in this repo.

### D8 — Auth and CORS posture: recorded decision, no change

**Decision: unchanged for this slice, and deliberately so.** No authentication,
no rate limiting, `allow_origins=["*"]`, `allow_methods=["*"]`,
`allow_headers=["*"]`.

The rationale, stated once so it is a decision rather than an omission:

- The server binds `0.0.0.0:8100` on a LAN host and is not internet-exposed. The
  API is strictly read-only — no route mutates data, so the worst case for an
  unauthenticated reader on the LAN is reading market data they could buy.
- `allow_origins=["*"]` with no credentials and no cookies means CORS grants a
  browser nothing it could not get from `curl`. Tightening to a fixed origin
  list would break the UI's dev server on an arbitrary Vite port while adding no
  real control.

**The conditions that would reverse this**, recorded so the trigger is
recognizable rather than rediscovered: any route that writes; any exposure
beyond the LAN (port forward, tunnel, VPS move); or any second user whose access
should differ from the first. Any one of those makes auth a prerequisite, not an
enhancement.

### D9 — The two policy ceilings are operator-settable

A bar ceiling and a statement timeout are exactly the values that get tuned
after real client traffic arrives. As module constants they would require a code
change and a release to move, which is the wrong shape for a policy knob — and
the project rule on centralizing defaults at the config level says so directly.

**Decision:** the default and its derivation stay in `constants.py`; `Settings`
adds a field per knob that defaults to that constant; all consuming code reads
`Settings`.

```python
# manta_trading/config/__init__.py
api_max_bars_per_request: int = API_MAX_BARS_PER_REQUEST   # MT_API_MAX_BARS_PER_REQUEST
api_statement_timeout: str = API_SERVING_SESSION.statement_timeout  # MT_API_STATEMENT_TIMEOUT
```

One definition of the number, one place to override it, and `pydantic-settings`
validates the override at load time (`MT_API_MAX_BARS_PER_REQUEST=lots` fails at
startup rather than at the first request). Precedent: `eodhd_daily_limit`.

**Scope of the knobs:**

- `MT_API_MAX_BARS_PER_REQUEST` — default 75,000. The `422` message computes the
  maximum span from the live value, so an override never produces a message that
  contradicts the enforced limit.
- `MT_API_STATEMENT_TIMEOUT` — default `20s`, applied to all three API pools.

**Not settable:** `work_mem`, the derivation inputs
(`INTRADAY_MINUTES_PER_TRADING_DAY`, `GRANULARITY_BAR_MINUTES`,
`TRADING_DAYS_PER_CALENDAR_DAY`), and the pool sizes. The derivation inputs are
*measurements of the data*, not policy — an operator overriding them would be
falsifying the estimate, not tuning it. `work_mem` and pool sizing can adopt the
same pattern later if a reason appears; adding knobs nobody has asked to turn is
the complexity the guidelines warn about.

**Read once at startup**, not per request: `Settings()` is instantiated in the
lifespan hook and the resolved values are held on `app.state`. Changing an
override requires a server restart — the same contract as `MT_TIMESCALE_DB_URL`.

---

## API Specification — changed surfaces

```
GET /api/v1/bars/{symbol}
```

New rejections, all `422` with `{"error": "..."}`:

```json
{
  "error": "requested range spans ~2,145,000 1m bars; the maximum is 75,000 (about 113 days). Narrow the window or request a coarser granularity."
}
```

```json
{ "error": "start (2024-06-30) is after end (2024-06-01)." }
```

New success case (known symbol, empty window):

```json
{ "symbol": "SPY", "granularity": "1m", "adjusted": true,
  "count": 0, "bars": [], "is_stale": false }
```

Unchanged: `404 {"error": "..."}` for an unknown symbol; msgpack encoding of the
same bodies; `is_stale` semantics (185 D7).

```
GET /api/v1/status
```

Its two `422` bodies change from `{"detail": "..."}` to `{"error": "..."}`.
Message text and trigger conditions are unchanged.

```
GET /openapi.json
```

`info.version` becomes the installed distribution version (0.7.3 at time of
writing) instead of `0.1.0`.

---

## Cross-Slice Dependencies and Interfaces

- **Depends on [184]** — `create_app()`, the `HTTPException`/`Exception`
  handlers, and the OpenAPI metadata this slice modifies.
- **Depends on [185]** — landed and merged (`cf64b8e`). Task breakdown must diff
  against 185's *landed* `bars.py`: `is_stale`, `get_db_pool` (not `get_db`),
  and the `CAGG_BASE_GRANULARITY` probe branch already exist and are not this
  slice's to reintroduce.
- **Feeds [187]** — 187's `tests/load/` tier inherits D1's session settings and
  D4's cap as the bounds it asserts against, and owns the pool-sizing decision
  D2 defers.
- **Client-facing** — D5 and D6 are breaking contract changes. They ship
  together, with the regenerated schema artifact and a CHANGELOG entry.

---

## Success Criteria

1. All three pools in the API process run `statement_timeout = 20s` and
   `work_mem = 64MB`, verifiable from `pg_stat_activity` during a request; the
   CLI and daemon still run 300 s / 512 MB.
2. The four slowest legitimate API calls, measured on prod, complete in under
   8 s — or the constant is raised and the measurement recorded in D1.
3. `GET /openapi.json` reports `info.version` equal to
   `importlib.metadata.version("manta-trading-data")`.
4. A `1m` request spanning more than the derived maximum returns `422` with a
   message naming the estimate, the ceiling, and the maximum span — and issues
   no DB query (assertable via a mock pool).
5. `MT_API_MAX_BARS_PER_REQUEST=1000` makes a previously-admitted request return
   `422` with a message naming 1,000 and the correspondingly shorter span; an
   invalid override fails at startup rather than at the first request. Same for
   `MT_API_STATEMENT_TIMEOUT`, observable in `pg_settings`.
6. A request with `start > end` returns `422`.
7. A known symbol with an empty window returns `200` with `count: 0` and a
   populated `is_stale`; an unknown symbol still returns `404`.
8. Every error body produced by this codebase has an `"error"` key; FastAPI
   validation errors keep `"detail"` and that exception is documented.
9. `docs/api/openapi.json` is committed, matches the served schema modulo
   `info.version`, and is linked from the README.
10. The auth/CORS posture and its reversal conditions are recorded in this
    document (D8) — no code change.
11. `uv run --extra dev mypy src/manta_trading/api_server/` and `ruff` are clean
    on touched files; `test/unit/api_server/` passes.

---

## Verification Walkthrough (draft — refined at Phase 6 close)

**Prerequisites:** `MT_TIMESCALE_DB_URL` pointed at prod `trading`; server not
running.

**1. Start the server**
```bash
uv run mt serve --port 8100
```
Wait for `Application startup complete.`

**2. Session settings on all three pools**
```bash
uv run python - <<'PY'
from manta_trading.config import Settings
import psycopg
with psycopg.connect(str(Settings().timescale_db_url)) as c:
    c.execute("SET statement_timeout='15s'")
    for row in c.execute("""
        SELECT application_name, setting FROM pg_settings, pg_stat_activity
        WHERE name='statement_timeout' AND state='idle'
    """).fetchall()[:10]:
        print(row)
PY
```
Better signal: issue one request per family first (`/health`, `/bars/SPY?granularity=1d…`,
`/bars/SPY?granularity=5m…`) so all three pools have live connections, then read
`statement_timeout` / `work_mem` from each backend. Expected: `20s` / `64MB` on
every backend owned by the API process.

**3. Timeout headroom (feeds success criterion 2)**
```bash
for u in \
  "bars/SPY?granularity=1d&start=2019-01-01&end=2026-01-01" \
  "bars/SPY?granularity=1m&start=2024-01-01&end=2024-03-01" \
  "status" "health"; do
  echo -n "$u  "; curl -s -o /dev/null -w "%{time_total}s\n" "http://localhost:8100/api/v1/$u"
done
```
Expected: all under 8 s. Record the numbers in D1; raise
`API_SERVING_SESSION.statement_timeout` if any is over.

**4. OpenAPI version**
```bash
curl -s http://localhost:8100/openapi.json | python3 -c "import sys,json;print(json.load(sys.stdin)['info']['version'])"
uv run mt --version
```
Expected: identical versions, neither `0.1.0`.

**5. Range cap rejects, and costs nothing**
```bash
curl -s -w "\nHTTP %{http_code} %{time_total}s\n" \
  "http://localhost:8100/api/v1/bars/SPY?granularity=1m&start=2004-01-01&end=2026-01-01"
```
Expected: `HTTP 422`, sub-10 ms, body `{"error": "requested range spans ~… 1m bars; the maximum is 75,000 (about 113 days)…"}`.

**6. Cap admits the boundary**
```bash
curl -s "http://localhost:8100/api/v1/bars/AAPL?granularity=1m&start=2024-03-01&end=2024-06-20" \
  -o /tmp/bars.json -w "HTTP %{http_code}  %{time_total}s  %{size_download} bytes\n"
python3 -c "import json;print('count:', json.load(open('/tmp/bars.json'))['count'])"
```
Expected: `HTTP 200` (112-day span, dense symbol — the case the ceiling was
sized against). Record `count`, elapsed time, and payload size; the payload
should land near the ~8–10 MB estimate in D4.

**7. Ceiling is operator-settable (D9)**
```bash
# Restart the server with a low override, then repeat the boundary request:
MT_API_MAX_BARS_PER_REQUEST=1000 uv run mt serve --port 8101 &
curl -s -w "\nHTTP %{http_code}\n" \
  "http://localhost:8101/api/v1/bars/AAPL?granularity=1m&start=2024-03-01&end=2024-06-20"
# Invalid override must fail at startup, not at first request:
MT_API_MAX_BARS_PER_REQUEST=lots uv run mt serve --port 8102
```
Expected: `HTTP 422` naming 1,000 and a ~1.5-day maximum span; the invalid
override exits with a pydantic validation error before the server binds.

**8. Reversed range**
```bash
curl -s -w "\nHTTP %{http_code}\n" \
  "http://localhost:8100/api/v1/bars/SPY?granularity=1d&start=2024-06-30&end=2024-06-01"
```
Expected: `HTTP 422`, `{"error": "start (2024-06-30) is after end (2024-06-01)."}`

**9. Empty window vs unknown symbol**
```bash
# Known symbol, a weekend:
curl -s -w "\nHTTP %{http_code}\n" \
  "http://localhost:8100/api/v1/bars/SPY?granularity=1d&start=2024-06-08&end=2024-06-09"
# Unknown symbol:
curl -s -w "\nHTTP %{http_code}\n" \
  "http://localhost:8100/api/v1/bars/XYZZYQ?granularity=1d&start=2024-06-03&end=2024-06-07"
```
Expected: `HTTP 200` with `"count":0,"bars":[]` and an `is_stale` field; then
`HTTP 404` with `{"error": "..."}`.

**10. Unified error bodies**
```bash
curl -s "http://localhost:8100/api/v1/status?health="          # 422, {"error": …}
curl -s "http://localhost:8100/api/v1/bars/XYZZYQ?granularity=1d&start=2024-06-03&end=2024-06-07"  # 404, {"error": …}
curl -s "http://localhost:8100/api/v1/bars/SPY?granularity=bogus&start=2024-06-03&end=2024-06-07"  # 422, {"detail": [...]} — documented exception
```

**11. Schema artifact matches the server**
```bash
uv run python scripts/dump_openapi.py --check
curl -s http://localhost:8100/openapi.json | python3 -m json.tool > /tmp/served.json
python3 -c "
import json
a=json.load(open('docs/api/openapi.json')); b=json.load(open('/tmp/served.json'))
a['info'].pop('version'); b['info'].pop('version')
print('schemas match:', a==b)"
```
Expected: `schemas match: True`.

**12. CLI and daemon unaffected**
```bash
uv run mt data status --symbol SPY
```
Expected: unchanged output; the DB classes still default to 300 s / 512 MB
outside the API process (assert in a unit test as well as here).

**13. Test suite**
```bash
uv run pytest test/unit/api_server/ -q
uv run --extra dev mypy src/manta_trading/api_server/
```
Expected: all pass; mypy clean on touched files.

---

## Risks

- **Two breaking contract changes (D5, D6).** Any client already treating `404`
  as "no data" or parsing `detail` from a status-route error will need a change.
  Mitigation: both ship in one release with the regenerated schema, a CHANGELOG
  entry, and a README note — not trickled out.
- **The `1m` cap is tighter than the ~6 months implied when the policy was
  chosen** — ~113 days at the agreed 75 k ceiling, because extended-hours
  coverage (960 bars/day, not 390) was measured afterwards. Mitigation: D4 states
  the measurement and D9 makes the ceiling an env override, so the client team
  can be moved without a release.
- **A raised ceiling can outrun the statement timeout.** The two knobs are
  independent settings but not independent behaviors: a large
  `MT_API_MAX_BARS_PER_REQUEST` with the default `20s` converts a fast, explicit
  `422` into a slow `500`. Mitigation: D9 says so, and the tuning path is to move
  both together after measuring.
