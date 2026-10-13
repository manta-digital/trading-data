---
docType: slice-design
slice: api-surface-coverage-futures-tick
project: trading-data
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [229, 230]
interfaces: [232, 234]
dateCreated: 20261012
dateUpdated: 20261013
status: in_progress
---

# Slice Design: api-surface-coverage-futures-tick

## Overview

This slice serves the futures tick tier over HTTP. It applies the
`/api/v1/futures/*` namespace that slice 220 chose for the API (its
Technical Decision 3), and adds:

- **Catalog:** the configured products and the active contract under each
  status rule, each product's contract definitions, and a product's roll
  schedule over a range.
- **Time series:** ticks and session-bounded bars, either for one contract
  or for a product under a roll rule.
- **Composed lines** in three existing routes: a tick-database field in
  `/api/v1/health`, a tick block in `/api/v1/status`, and a `futures ticks`
  source in `/api/v1/overview`.

Every endpoint is a thin route over readers that already exist and are
already tested by the CLI. Slice 230 built them for this purpose: the read
plan, the per-session tick and bar reads, exact price and time formatting,
the accounting figures, the ledger freshness read, and the line-state
vocabulary. 229's roll resolver supplies the schedules. This slice writes no
new resolution or aggregation logic. One small read is new (definitions plus
the sessions held for each contract, for the catalog).

The new part is how the API behaves when it fails. `mt-serve` starts and
keeps serving equities and Kalshi when the tick database is missing or down.
Tick endpoints answer `503` until it comes back, and `/api/v1/health` reports
the tick state in a field beside `db`, always with HTTP 200. The 220
architecture fixed this posture under "Serving-layer failure posture"; this
slice builds it.

## Value

- **Clients get tick data without Python or SQL.** trading-ui and any future
  consumer can chart ES at any bar size, read raw ticks over a bounded
  window, and look up contracts and rolls. Today the only route to tick data
  is `mt data tick get` on the dev checkout.
- **Roll choices are visible on the wire.** Every product-level response
  names the rule, the contract that served each run of sessions, the
  sessions it could not resolve, and the sessions whose data is incomplete.
  A client never gets roll-stitched data without being told how it was
  stitched.
- **Closes 180's tick exclusion.** The serving architecture deferred tick
  endpoints "until tick storage exists". The storage exists, so the gap
  closes.
- **Monitoring covers ticks.** Health, status and overview show the tick
  tier's state, so a dashboard built on the API sees a tick outage instead
  of silence.
- **Unblocks 232 and the export work.** 232 must show GC in "status,
  overview, and the API … with no code change", and the deferred
  NautilusTrader export lists 231 as a dependency.

## Technical Scope

**Included**

- Seven `GET` routes under `/api/v1/futures` (Technical Decision 1).
- A second, asynchronous connection pool for the tick database. It opens
  without blocking startup and maps its failures to `503` (Technical
  Decision 2).
- A `tick_db_url` parameter on `create_app` and `lifespan`, so the load tier
  can point tick endpoints at a test database without reading the production
  setting.
- Tick additions to `/api/v1/health`, `/api/v1/status` and
  `/api/v1/overview` (Technical Decision 7).
- Response models for every new route, published in `openapi.json`
  (Technical Decision 5).
- Moving the URL-password scrubbing and failure classification from
  `cli/tick_composed.py` to `data/tick/line_state.py`, so the API and the
  CLI share one copy.
- Both reference documents (`docs/api/reference.md`, `docs/api/agents.md`)
  extended under the 190 documentation gate, and `openapi.json` regenerated.
- Load-tier latency bounds for the new endpoints, set from measurement.
- Document updates: 180's tick exclusion marked closed; an "Extended by
  slice 231" paragraph in 180's Range Policy (TD4); and a 220 architecture
  Revision Log entry (TD7).

**Excluded**

- **Any write or operator action:** `pass`, `ingest`, `estimate`,
  `backfill`, `adopt`. The API is read-only; these are operator tools.
- **`debug` reads.** 230 answered "no": they show internal bookkeeping such
  as file paths, job ids and costs. The contracts endpoint serves the part of
  the definitions a client needs.
- **Pagination, streaming and bulk export.** A response is complete or
  refused, as in 188 (Technical Decision 4). Bulk tick extraction belongs to
  the deferred NautilusTrader catalog export or to direct database access,
  not to a JSON API.
- **Back-adjusted prices.** The architecture allows them only as an opt-in
  read-time transform, if they exist at all. Nothing asks for them yet.
- **Production deployment** of the tick setting: `MT_TICK_DB_URL` in
  `/etc/manta-trading.env` is 234's job. Until then, production's tick
  endpoints answer `503 not configured`, which is the intended state.
- **Caching.** Roll answers must reflect current units (229, State
  Management), and every other read is cheap or bounded by the row ceiling.

## Dependencies

### Prerequisites

- **229 (complete):** `build_schedule`, `latest_lines`, `RollRule.parse`,
  `DEFAULT_ROLL_RULE`, `STATUS_ROLL_RULES`, the `TICK_ROLL_MAX_SESSIONS`
  cap, and `roll_payload`'s dictionaries.
- **230 (complete):** `get_plan`, `tick_reads`, `tick_format`,
  `tick_accounting`, `status_reads.newest_event_ns`, `line_state`, and the
  `TICK_SOURCE_NAME` constant in `cli/commands/overview.py`.
- **923 (complete):** `resolve_database_url(settings, Database.TICK,
  Credential.APPLICATION)`.
- **190 (complete):** `scripts/check_api_docs.py`, `scripts/dump_openapi.py`,
  and the marker convention the two documents use.
- No new package. `psycopg_pool.AsyncConnectionPool` ships in the
  `psycopg_pool` package the API already uses.

### Interfaces Required

| From | Interface | Used for |
|---|---|---|
| 230 `get_plan` | `contract_plan(conn, calendar_url, symbol, start, end)`, `product_plan(conn, (tick_url, calendar_url), entry, rule, start, end)` → `GetPlan`; `ContractNotFound`, `ContractAmbiguous` | All four time-series routes |
| 230 `tick_reads` | `read_ticks`, `read_bars`, `TickWindow`, `TICK_COLUMNS`, `BAR_COLUMNS`, the price and time column sets | Row reads |
| 230 `tick_format` | `price_text`, `ts_text` | Every price and nanosecond time on the wire |
| 230 `tick_accounting` | `accounting_lines(conn, calendar_url, universe)` | `/api/v1/status` tick block |
| 230 `status_reads` | `newest_event_ns(conn)` | `/api/v1/overview` source |
| 230 `line_state` | `TickLineState`, `TickUnavailable`, `unavailable_text`, `first_line` | `503` bodies, health field, status and overview states |
| 229 `active_contract` | `build_schedule(tick_url, calendar_url, entry, rule, start, end)`, `latest_lines(tick_url, calendar_url, entry, rules)` (the raising variant of `latest_active`), `roll_conninfo`, `RollRangeTooLong` | Schedule and products routes; product-level plans |
| 229 `roll_payload` | `contract_dict`, `unresolved_dict`, `active_line_dict`, `schedule_dict` | Field sets for the catalog models |
| 222/225 `store_context` | `missing_migrations(conn)` | Tick track check per checkout |
| `universe` | `TICK_UNIVERSE`, `TickUniverseEntry` | Known products; unknown → `404` |

## Architecture

### Component Structure

| Module | Role |
|---|---|
| `api_server/tick_access.py` (new) | Owns the tick pool: opens it in `lifespan` without blocking, closes it on shutdown. The `tick_connection` dependency checks out a connection, verifies the tick track's migrations, and yields; otherwise it raises `TickServiceUnavailable`. `probe_tick()` returns a `TickLineState` for health. The app-level handler turns `TickServiceUnavailable` into `503 {"error": …}`. |
| `api_server/routes/futures_catalog.py` (new) | `products`, `contracts`, `schedule`. |
| `api_server/routes/futures_series.py` (new) | The four time-series routes over one serve path. |
| `api_server/futures_read.py` (new) | The shared read helper: plan, then read under the ceiling in one snapshot, with `[from, until)` clipping. |
| `api_server/routes/futures_params.py` (new) | Product lookup (`404`), rule parsing (`422`), the `TickBarGranularity` enum built from `TICK_BAR_GRANULARITIES`, instant bounds, and the shared query-parameter types. |
| `api_server/models/futures.py` (new) | The time-series response models and the `TickBlock` used by `/api/v1/status`. |
| `api_server/models/futures_catalog.py` (new) | The catalog response models. |
| `data/tick/catalog_reads.py` (new) | `contracts_for(conn, product)`: definition rows joined to each instrument's first and last held session under current units. No API import, so the CLI could use it later. |
| `data/tick/line_state.py` (changed) | Gains `classify(exc, password, statement_timeout)`, moved from `cli/tick_composed.py` with the scrubbing and the outage and read-failure tuples. The CLI's `classify(exc, settings)` becomes a thin wrapper. |
| `data/tick/tick_format.py`, `get_plan.py`, `active_contract.py` (changed) | Shared pieces the API reuses instead of copying: `cell_value`/`row_cells` (the CLI writer's row formatting), `resolve_range` (held-session defaults), `error_lines` (one failure on every rule's line). |
| `data/base/trading_calendar.py`, `data/tick/status_reads.py` (changed) | `TradingCalendar` takes an optional `checkout_timeout`; `open_calendar` passes `TICK_DB_CONNECT_TIMEOUT_SECONDS`, so a calendar outage in the roll path fails within the connect timeout instead of the pool's 30 s default. |
| `data/tick/constants.py` (changed) | `TICK_API_POOL_MIN`, `TICK_API_POOL_MAX`, `TICK_API_CHECKOUT_TIMEOUT_SECONDS`; and `TICK_SOURCE_NAME`, moved here from `cli/commands/overview.py`, which now imports it. |
| `api_server/app.py` (changed) | `create_app(db_url=None, tick_db_url=None)`; `lifespan` opens and closes the tick pool; registers both routers and the `503` handler. |
| `api_server/routes/health.py`, `status.py`, `operations.py` (changed) | The composed additions. |
| `api_server/models/responses.py`, `models/operations.py` (changed) | `HealthResponse.tick_db` and `tick_detail`, `StatusResponse.tick`, `SourceRecord.unavailable`. |

The routes import no `cli/` module for tick work. `TICK_SOURCE_NAME` moves to
`data/tick/constants.py`, so the source name has one home that the CLI and
the API both import. `operations.py`'s existing imports of `build_overview`
and `gather_db_facts` from `cli/commands/overview.py` predate this slice and
are unchanged.

### Data Flow

**Time series** (`/futures/{contracts/{symbol} | products/{product}}/{ticks | bars}`):

```
request ──► parse: symbol | product (404 if not in TICK_UNIVERSE),
            rule (RollRule.parse → 422), granularity (TICK_BAR_GRANULARITIES → 422),
            start/end session dates, from/until instants (ticks only)
        ──► tick_connection (503 if not configured / unreachable / migrations pending)
        ──► one REPEATABLE READ READ ONLY transaction on that connection:
              GetPlan  ◄── contract_plan | product_plan
                              └─ product: build_schedule opens its own
                                 snapshot (229 rule), calendar from the primary URL
              for each TickWindow (clipped by from/until for ticks):
                  read_ticks | read_bars ──batches──► collector
                       collector raises RowCeilingExceeded past the ceiling
        ──► connection back to the pool
        ──► 422 if the ceiling was exceeded
        ──► FuturesTicksResponse | FuturesBarsResponse (price_text / ts_text)
        ──► timeseries_response(model, format)  (json | msgpack)
```

**Catalog:** every catalog route depends on `tick_connection` too, so
not-configured, unreachable and migrations-pending are a `503` before any
roll call (TD2).

- `products` calls `latest_lines(tick_url, calendar_url, entry,
  STATUS_ROLL_RULES)` for each universe entry.
- `contracts` calls `catalog_reads.contracts_for` on the pooled connection.
- `schedule` calls `build_schedule` with the parsed rule and range.

`latest_lines` and `build_schedule` own their connections and snapshots
(229, Technical Decision 9), so these two routes pass URLs. They still hold
the pooled checkout for the request, and that checkout is what bounds how
many roll connections can be open at once (TD2).

**Composed:**

- **`/health`** runs `probe_tick()`: a checkout with the short timeout plus
  the migration check, giving a `TickLineState`.
- **`/status`** runs `accounting_lines` on a pooled connection, giving a
  `TickBlock` with `state`, `detail` and `products`. A failure becomes the
  block's state, never a `5xx`.
- **`/overview`** runs `newest_event_ns` on a pooled connection, giving the
  `futures ticks` `SourceRecord`. A failure fills the record's
  `unavailable` text.

### State Management

The only new state is the tick pool on `app.state.tick_pool`, or `None`
when no tick URL is configured. `app.state.tick_url` and
`app.state.calendar_url` (the resolved primary URL) are held for the roll
functions. All three are resolved once in `lifespan`; changing them requires
a restart, the same rule as `MT_TIMESCALE_DB_URL`. Nothing is cached:
schedules and active contracts are computed on every request (229).

## Technical Decisions

### TD1. Seven routes; product and contract are path resources

```
GET /api/v1/futures/products
GET /api/v1/futures/products/{product}/contracts
GET /api/v1/futures/products/{product}/schedule   ?rule=&start=&end=
GET /api/v1/futures/products/{product}/ticks      ?rule=&start=&end=&from=&until=&format=
GET /api/v1/futures/products/{product}/bars       ?granularity=&rule=&start=&end=&format=
GET /api/v1/futures/contracts/{symbol}/ticks      ?start=&end=&from=&until=&format=
GET /api/v1/futures/contracts/{symbol}/bars       ?granularity=&start=&end=&format=
```

- **Product and contract are separate path families.** A product read goes
  through a roll rule and a contract read does not, which is the same split
  as the CLI's `--product` and `--contract`. A query switch on one path would
  let a request carry both or neither.
- **Granularity is a query parameter**, as on `/api/v1/bars/{symbol}`. Its
  values are `TICK_BAR_GRANULARITIES`. Ticks are their own path because their
  rows have a different shape.
- **`rule` uses 229's grammar** (`{method}.{rank}[+{offset}s]`) through
  `RollRule.parse`, which accepts any case and surrounding whitespace. It
  defaults to `DEFAULT_ROLL_RULE`, and the response says so with
  `is_default`.
- **There is no single-contract detail route.** The contracts list holds 51
  rows today and covers that need.

### TD2. The tick pool opens without blocking and maps failure to `503`

`lifespan` resolves the tick URL: the explicit `tick_db_url` when given,
otherwise `resolve_database_url(settings, Database.TICK,
Credential.APPLICATION)`.

- **Unset:** `app.state.tick_pool = None`. Every tick route answers
  `503 {"error": "tick database not configured (MT_TICK_DB_URL unset)"}`.
  The wording comes from `unavailable_text`. Startup does not fail: per the
  architecture, the tick endpoints fail and the process keeps running.
- **Set:** build `AsyncConnectionPool(url, open=False,
  min_size=TICK_API_POOL_MIN, max_size=TICK_API_POOL_MAX,
  timeout=TICK_API_CHECKOUT_TIMEOUT_SECONDS, configure=…)`, then
  `await pool.open(wait=False)`. `configure` sets
  `default_transaction_read_only=on`, the API's `statement_timeout` and its
  `work_mem`, the same session budget the primary pools get (186 D1). An
  unreachable database at startup is logged at WARNING and not raised. The
  pool reconnects on its own; there is no retry loop of ours.
- **`tick_connection` dependency:**
  - checks out a connection;
  - on `PoolTimeout` or `OperationalError`, raises
    `TickServiceUnavailable(TickUnavailable(UNREACHABLE, …))`;
  - runs `missing_migrations`; if any are missing, raises with
    `MIGRATIONS_PENDING`;
  - otherwise yields the connection.

  The migration check is one indexed lookup per checkout. Checking per
  request is what makes a database migrated after startup recover with no
  restart.
- **Failures during a read:**
  - `QueryCanceled` keeps the existing `504`. It is a subclass of
    `OperationalError`, so it is matched first.
  - Any other `OperationalError`, or a `TickCalendarError`, from a read or
    from the roll functions' own connections becomes `503 unreachable`.
  - Every `503` body is `{"error": unavailable_text(...)}`. The detail is
    scrubbed of the URL's password by the moved `classify`.
- **Equities and Kalshi routes never touch the tick pool.** A test asserts
  that they still answer `200` with the tick database unreachable.

`TickServiceUnavailable` is raised from the dependency and from the read
helper. One app-level handler turns it into the response, the same pattern
as the existing `QueryCanceled` handler.

**Every futures route depends on `tick_connection`, catalog routes
included.** Not-configured, unreachable and migrations-pending are therefore
a `503` before any planner or roll call runs.

**The roll functions' own connections** (`build_schedule`, `latest_lines`,
and the calendar reads behind `sessions_dated`) are outside the pool, so the
checkout timeout does not bound them. Three things do:

- **Timeouts:** they connect through 229's `roll_conninfo`, which sets
  `connect_timeout=TICK_DB_CONNECT_TIMEOUT_SECONDS`, TCP keepalives,
  `statement_timeout=TICK_ROLL_STATEMENT_TIMEOUT_SECONDS` (30 s) and
  read-only transactions. An unreachable host is therefore a `503` within
  the connect timeout, never the operating system's TCP timeout. The
  closed-port integration test covers a product route as well as a contract
  route.
- **Session budget:** it is 229's roll budget, not the pool's `configure`.
  That is deliberate: 229 requires these connections to keep
  `roll_conninfo`'s bounds, and a pooled variant is allowed only if it keeps
  them. This slice does not build one.
- **Count:** each one runs inside a request that already holds a pooled
  tick checkout, so at most `TICK_API_POOL_MAX` requests per worker can be
  in the roll path at once. Special Considerations gives the resulting
  connection budget.

**Outage versus refusal in the roll path.** One rule applies to every
futures route:

- an outage (`OperationalError` other than `QueryCanceled`, or
  `TickCalendarError`) is a `503`;
- a statement timeout is a `504`;
- a refusal (`TickPreflightError`) is a `422`, except in `products`.

`products` keeps 229's per-line reporting for anything that is not an
outage. It calls `latest_lines`, the raising variant, and not
`latest_active`, which would turn an outage into a line `error` with HTTP
200. It maps outages to `503`, and turns `TickPreflightError` and
`RollInputError` into that product's line `error`s, exactly as
`latest_active` does. A product with nothing held is a line `reason`, not
an error. So `/futures/products` is a `503` only when the tick or calendar
database is down.

### TD3. Windows: session dates with an exclusive end, plus instant bounds for ticks

- **`start` and `end` are session dates, and `end` is exclusive.** This is
  the tick tier's convention, which 230 recorded as a deliberate departure
  (220 architecture, Revision Log 2026-10-12). `get_plan` takes it as it
  stands. Futures sessions open the evening before their date, so a UTC-day
  window would cut every session in two.
- This conflicts with `/api/v1/bars`, whose `end` is inclusive in UTC days.
  `reference.md` §2.2 ("Date windows are inclusive at both ends") gains a
  stated exception for `/api/v1/futures/*`, with the reason, and
  `agents.md` §3 ("Choosing a window") gets the same rule.
- **Omitted `start`/`end` default to the held sessions**, as in the CLI.
- **Ticks only: `from` and `until` are UTC instants.** They are aware
  datetimes, or naive ones read as UTC as `windows.py` does. The bound is
  half-open, `[from, until)`, and clips each planned window to
  `max(open_ns, from_ns) … min(close_ns, until_ns − 1)`; windows clipped to
  nothing are dropped.
  - Half-open is what lets a client page through a session: the next
    request's `from` is this one's `until`, with no overlap and no gap.
  - Microsecond inputs are enough, because these are range bounds, not
    tick identities.
  - The busiest session held has 687,287 ticks (measured on the tick
    database 2026-10-12; the median is 88, because most rows are thin
    spreads). With a 75,000-row ceiling, ticks cannot be served at all
    without bounds inside a session. Re-read 2026-10-13 against more held
    data: the busiest is now ESU4 2024-09-06, 690,908 ticks.
- **Bars take no `from`/`until`.** Clipping `open_ns` would move the first
  bucket's stamp, because a bar's start is
  `GREATEST(time_bucket, open_ns)`. Bar requests are bounded by session
  count instead: about 1,380 one-minute bars per session, so about 54
  sessions per request at `1m`.

### TD4. One row ceiling, enforced while reading, no pagination

- **The ceiling is the existing `api_max_bars_per_request`** (75,000), the
  one ceiling Kalshi already shares. Introducing a separate tick setting
  would need a reason, and there is none.
- **Enforced during the read, not by a count first.** The read helper's
  `on_batch` collector raises `RowCeilingExceeded` once the total passes the
  ceiling. The named cursor closes in its `async with`, the transaction rolls
  back, and the route raises `422`.
  - The cost of a refused request is bounded by the ceiling plus one batch
    of `TICK_GET_FETCH_ROWS` (10,000), however large the session. A
    `count(*)` first would cost a full scan on top of every admitted read.
- **The refusal names the ceiling but not the true count**, which is never
  computed: "the request matches more than 75,000 rows; narrow start/end or
  from/until". It goes through `admission.py` with a new
  `admit_rows`-style helper and the remedy as a parameter, so the wording
  lives beside the existing refusal.
- There is no pagination, matching 188 D8: complete or refused. The ticks
  routes' `from`/`until` are the paging tool, and they are stateless.
- The roll cap still applies: `RollRangeTooLong` (more than
  `TICK_ROLL_MAX_SESSIONS` = 1,300 sessions) is a `422`.
- **This is a third enforcement style**, so 180's Range Policy gains an
  "Extended by slice 231" paragraph, as 188 added its own:
  - **186 bars:** the bars are estimated before the query, and no database
    work is done for a refused request.
  - **188 Kalshi:** an exact `count(*)` is taken first, and the `422`
    quotes the true count.
  - **231 futures:** the ceiling is enforced during the read. A refused
    request reads at most the ceiling plus one batch (85,000 rows) and
    quotes no count.

  The reason is measured: a single session holds up to 687,287 ticks, so a
  `count(*)` costs a full session scan on every request, admitted or not,
  and a window estimate is useless because tick density varies by orders of
  magnitude across instruments and hours. The paragraph also states that
  "no DB work is done for a rejected request" holds only for the bars
  route.

### TD5. Response shape: the plan's facts, then rows; exact text for prices and times

Both time-series responses share an envelope. Every array is always
present, possibly empty.

```json
{
  "target": {"kind": "product", "product": "ES", "rule": "v.0", "is_default": true,
             "contract": null, "instrument_id": null},
  "granularity": "1m",
  "start": "2024-09-16", "end": "2024-09-20",
  "definitions_as_of": "2024-12-31",
  "segments": [{"first_session": "2024-09-16", "last_session": "2024-09-18",
                "raw_symbol": "ESU4", "instrument_id": 118},
               {"first_session": "2024-09-19", "last_session": "2024-09-19",
                "raw_symbol": "ESZ4", "instrument_id": 183748}],
  "unresolved": [],
  "incomplete": [{"session_date": "2024-09-17", "status": "missing"}],
  "count": 5512,
  "bars": [{"time": "2024-09-15T22:00:00.000000000Z", "session_date": "2024-09-16",
            "raw_symbol": "ESU4", "instrument_id": 118,
            "open": "5650.25", "high": "5651", "low": "5650", "close": "5650.75",
            "volume": 412}]
}
```

- **Ticks responses** carry `from` and `until` (null when absent) and a
  `ticks` array whose fields are `TICK_COLUMNS` in order. Bars carry the
  `BAR_COLUMNS` in order.
- **Prices are strings from `price_text`, so they are exact.** An
  undefined price (`UNDEF_INT64`) becomes `null`. Times are strings from
  `ts_text`, with nine fraction digits.
- **No nanosecond integer is on the wire.** Epoch nanoseconds exceed 2^53,
  and JavaScript clients would silently round them. `instrument_id` is a
  32-bit value and stays an integer.
- **`unresolved` and `incomplete` are served with HTTP 200.** The rows
  returned are correct; these arrays say what is missing. The CLI exits 3 in
  the same situation. `reference.md` says when each array appears (229
  required this).
- **Contract targets** have `kind: "contract"` and a null `rule` and
  `is_default`, and their `unresolved` array is always empty.
- **Both JSON and msgpack go through `timeseries_response`.** Routes use
  `response_class=Response` with `timeseries_200(Model)`, so the schema
  publishes the body (190 fixed this).
- **Rows are validated, not built with `model_construct`.** The design
  proposed `model_construct` to skip validating 75,000 rows; the load test
  measured the opposite (2026-10-13, 75,000 ticks, build plus JSON dump):
  `model_construct` 1.446 s, validated 1.185 s, because dumping
  unvalidated models takes pydantic's slower path. The models validate, and
  the load test keeps that choice honest.

**Catalog bodies:**

- `products`: `[{product, calendar, active: [active_line_dict…]}]`, one
  active line per status rule. A refusal or broken invariant sets the
  line's `error` (as in the CLI, 229 TD9); an outage is a `503` (TD2).
- `contracts`: `{product, contracts: [{instrument_id, raw_symbol,
  instrument_class, activation, expiration, exchange, currency,
  min_price_increment, contract_multiplier, unit_of_measure,
  unit_of_measure_qty, held: {first_session, last_session} | null}]}`,
  ordered by expiration then instrument id. `min_price_increment` goes
  through `price_text`. Spreads are included, with their
  `instrument_class`.
- `schedule`: the `schedule_dict` fields (`product`, `rule`, `is_default`,
  `segments`, `answers`, `unresolved`). The answers carry
  `definition_unit_id` and `basis_unit_ids`, so a client can tell when the
  inputs changed (229).

Each catalog model is defined once in `models/futures.py`. It is built from
229's dataclasses by a `from_…` classmethod, not by re-keying the
`roll_payload` dictionaries. A test pins each model's field set to the
matching dictionary's keys, so the CLI JSON and the API cannot drift.

### TD6. One snapshot per time-series read

The plan and every window read run in one `REPEATABLE READ READ ONLY`
transaction on one pooled connection, as `mt data tick get` does. So the
plan's `incomplete` verdicts and the rows come from the same state. A
product plan's schedule is resolved in 229's own snapshot (it owns its
connections), exactly as in the CLI. That gap is accepted and documented in
229: the schedule is decided from the definitions and ledger, and the rows
are read after it.

### TD7. Composed lines: health, status, overview

- **`/api/v1/health`** gains `tick_db`, one of the five `TickLineState`
  values, and `tick_detail` (present when not `ok`, absent otherwise, using
  the same `exclude_none` rule as `detail`). HTTP stays 200. The handler
  becomes `async`: the existing blocking probe moves into
  `run_in_executor`, and `probe_tick` is awaited.
  - The architecture wrote this field as "`ok` or `error`". This slice uses
    the five-state vocabulary that 230 settled for every tick line, because
    "error" would merge "you forgot a setting" with "the database is down".
    This is recorded as a 220 architecture Revision Log entry.
- **`/api/v1/status`** gains a `tick` block that is always present:
  `{state, detail, products: [{product, sessions_in_scope,
  sessions_complete, buckets, records}]}`. It is the same shape as
  `mt data accounting --json`'s `tick`, from the same function, and does not
  depend on the `symbol`, `health` and `granularity` filters, which select
  equities rows. A tick failure is the block's state; the route still
  returns its equities rows.
- **`/api/v1/overview`**:
  - `sources` gains the `futures ticks` record;
  - every `SourceRecord` gains `unavailable: str | null`, null for the
    existing four;
  - `newest` comes from `newest_event_ns`, the newest stored tick read from
    the ledger, not a `max()` over the hypertable.

  This matches the CLI JSON that 230 shipped, and closes the gap 230 left on
  purpose.

### TD8. Does this belong in the API? Per surface

| Surface | Answer |
|---|---|
| Ticks and bars by contract and by product plus rule | **Yes:** the tier's data, at bars parity |
| Active contract per rule, contract definitions, roll schedule | **Yes:** a client cannot interpret product-level data without them |
| Coverage and completeness | **Yes, composed:** `/api/v1/status` carries the bucket counts, and each data response lists its `incomplete` sessions. No per-session coverage endpoint; nothing asks for one |
| Freshness | **Yes:** `/api/v1/overview` source |
| `debug` reads | **No:** internal bookkeeping (230) |
| `estimate`, `pass`, `ingest`, `backfill`, `adopt` | **No:** they spend money or write; the API is read-only |

### TD9. Realtime paths

Neither path is ruled out:

- Path A (rows assembled from realtime capture) would write the same table
  and ledger the endpoints read.
- Path B (historical data after the delay) is what is stored now.

A session still being written reads as `incomplete`, which is the honest
answer under either path. The overview source reads the ledger's newest
event, which whichever writer keeps the ledger advances.

### Patterns and Conventions

- **Routes stay thin.** Parsing and error mapping live in the routes, and
  everything else lives in `data/tick/`. A derivation appearing in a route
  module is a defect, as 189 states.
- **Every comparison value has one home:** granularities
  (`TICK_BAR_GRANULARITIES`), rules (`roll_rules`), line states
  (`TickLineState`), the source name (`TICK_SOURCE_NAME`), and pool sizes
  and timeouts (`data/tick/constants.py`).
- **Status codes:**
  - `404`: unknown product, or `ContractNotFound`.
  - `422`: a bad rule or granularity, `ContractAmbiguous`, an empty range,
    `RollRangeTooLong`, any other `TickPreflightError` (such as a calendar
    range refusal), `from` not before `until`, or the row ceiling.
  - `503`: tick unavailable.
  - `504`: statement timeout.
  - `500`: anything else, through the existing sanitized handler.
- **Error bodies** use the one existing shape, `{"error": "…"}` (186 D6).

## Implementation Details

### API Contracts

| Route | Success | Errors | Ceiling |
|---|---|---|---|
| `GET /futures/products` | 200 `FuturesProductsResponse` | 503, 504 | — |
| `GET /futures/products/{product}/contracts` | 200 `FuturesContractsResponse` | 404, 503, 504 | — |
| `GET /futures/products/{product}/schedule` | 200 `FuturesScheduleResponse` | 404, 422, 503, 504 | roll cap |
| `GET /futures/products/{product}/ticks` | 200 `FuturesTicksResponse` (json or msgpack) | 404, 422, 503, 504 | rows + roll cap |
| `GET /futures/products/{product}/bars` | 200 `FuturesBarsResponse` (json or msgpack) | 404, 422, 503, 504 | rows + roll cap |
| `GET /futures/contracts/{symbol}/ticks` | 200 `FuturesTicksResponse` (json or msgpack) | 404, 422, 503, 504 | rows |
| `GET /futures/contracts/{symbol}/bars` | 200 `FuturesBarsResponse` (json or msgpack) | 404, 422, 503, 504 | rows |
| `GET /health` (changed) | 200, plus `tick_db` and `tick_detail` | — | — |
| `GET /status` (changed) | 200, plus `tick` | unchanged | — |
| `GET /overview` (changed) | 200, plus a `futures ticks` source and `sources[].unavailable` | unchanged | — |

A shared `TICK_UNAVAILABLE_RESPONSE` declares the `503` in the schema, as
`GATEWAY_TIMEOUT_RESPONSE` does for `504`. Authentication is unchanged: none,
with CORS open (reference §2.8).

`create_app(db_url: str | None = None, tick_db_url: str | None = None)`.
`None` means "resolve from `Settings`", which is every production path. The
load tier passes the test tick database explicitly.

## Integration Points

### Provides to Other Slices

- **232 (GC):** GC appears in every route with no code change once it is in
  `TICK_UNIVERSE`, because routes resolve products from the universe and
  never from a literal.
- **234 (production wiring):** adding `MT_TICK_DB_URL` to the service
  environment turns production's `503 not configured` into live endpoints,
  and `tick_db` in `/health` is the check that it took effect.
- **trading-ui:** typed models in `openapi.json` for client generation.
- **NautilusTrader export (deferred):** the contracts and schedule bodies
  carry the roll and definition facts an export needs.

### Consumes from Other Slices

- **229 and 230, unchanged** apart from moving `classify` into
  `line_state.py`. The CLI behaves identically afterwards; its tests pin
  that.
- **Failures in what this slice consumes:**
  - an outage in the tick database, or in the calendar the roll functions
    read, is a `503`;
  - a refusal from the planner or the roll functions is a `422` naming the
    problem;
  - neither touches equities or Kalshi routes.

## Success Criteria

### Functional Requirements

1. **Contract ticks.** `GET /futures/contracts/ESZ4/ticks?start=2024-09-18&end=2024-09-19&from=…&until=…`
   returns ticks identical, field for field, to
   `mt data tick get ticks --contract ESZ4 --json` over the same bounds,
   in prices, times and order.
2. **Product bars.** `GET /futures/products/ES/bars?granularity=1m&start=…&end=…`
   across the September 2024 roll:
   - returns `segments` naming ESU4 then ESZ4 under `v.0`;
   - its bars equal `mt data tick get 1m --product ES --json`'s;
   - `is_default` is true.
3. **Unresolved and incomplete.** A product read whose range starts at the
   first held session returns those sessions in `unresolved`, with HTTP 200.
   A session whose verdict is not `complete` appears in `incomplete`.
4. **Errors:**
   - an unknown product, or a contract with no definition, is a `404`;
   - an ambiguous contract, a bad rule, a bad granularity, an empty range, a
     range over 1,300 sessions, or `from` not before `until` is a `422` with
     a message naming the problem.
5. **Ceiling.** A ticks request over 75,000 rows is a `422` naming the
   ceiling and the remedy, and the refusal reads at most the ceiling plus
   one batch. A request at or under it succeeds completely.
6. **Paging.** Consecutive `[from, until)` windows covering a whole session
   return exactly the session's ledger `record_count`, with no duplicates.
7. **Catalog:**
   - `products` lists ES with one active line per status rule, matching
     `mt data tick active --json` for the latest complete session;
   - `contracts` lists all 51 held definitions with `held` ranges;
   - `schedule` equals `mt data tick active --product ES --start … --end …
     --json`'s schedule.
8. **Not configured.** With `MT_TICK_DB_URL` unset, `mt-serve` starts,
   every futures route is `503` with the not-configured text,
   `/health` reports `tick_db: "not_configured"`, `/status`'s `tick.state`
   and the overview source's `unavailable` say the same, and every equities
   and Kalshi route answers as before.
9. **Unreachable.** With the tick URL pointing at a closed port, the same
   holds with `unreachable`. When the database comes back, the routes
   recover with no restart.
10. **Migrations pending.** A tick database missing a track migration gives
    `503` with the `migrations pending` remedy, and `tick_db:
    "migrations_pending"`.
11. **Secrets.** No `503`, `/health` or `/status` text ever contains the
    tick URL's password.

### Technical Requirements

- Every new route publishes its `200` body in `openapi.json`. No route
  answers with an untyped body.
- `scripts/check_api_docs.py --check` passes:
  - every new path, parameter and error status is documented by marker in
    both documents;
  - the 75,000 ceiling and the 1,300 session cap carry `from:` value
    markers;
  - the granularity and line-state vocabularies are enum-checked.
- `openapi.json` is regenerated with `scripts/dump_openapi.py`, and
  `test_openapi_artifact` passes.
- `ruff`, `mypy` (project invocation), and the unit and integration tiers
  pass, with no new failures beyond the known list.
- Source files stay under ~300 lines; `futures_series.py` and
  `futures_catalog.py` are split if either grows past that.
- Load tier (`test/load/test_231_futures_api_nfr.py`) on the test cluster,
  against a database seeded to the busiest-session size (about 690,000
  ticks for one instrument-session), with `create_app(db_url=…,
  tick_db_url=…)`:
  - the latency of a 75,000-tick read;
  - the latency of a ceiling-refused read;
  - the latency of a 54-session `1m` product bars read across a roll;
  - the cost the tick block adds to `/api/v1/status`, measured as the
    difference with and without a tick pool configured.

  Each bound is set from the measured value and recorded with the
  measurement in the test's docstring, and each is pass/fail.
  - **The tick block's added cost is its own pass/fail item.**
    `/api/v1/status`'s existing whole-route bound (2.79 s measured against
    1.5 s) already fails on `main`. That failure is known debt from before
    this slice (load-tier memory), not 231's to fix or to make worse
    unnoticed.
  - The equities and Kalshi load tests still pass with a tick pool
    configured, apart from that known failure.

### Integration Requirements

- 232 needs no API change for GC; a unit test drives the routes from a
  two-product universe fixture.
- `/api/v1/overview` and `mt data overview --json` agree on the
  `futures ticks` record's `newest` and `unavailable`.
- `/api/v1/status`'s `tick` block equals `mt data accounting --json`'s
  `tick` against the same database.

### Verification Walkthrough

This runs from the dev checkout, against the dev tick database (MT_TICK_DB_URL
in `.env`, holding the 2024 ES `tbbo` adoption) and the production primary
database for the calendar. These commands work only after this slice is
implemented; none exist today.

1. **Start the server with the tick database configured.**
   ```bash
   set -a; source .env; set +a
   uv run mt serve --host 127.0.0.1 --port 8137
   ```
   Then `curl -s localhost:8137/api/v1/health | jq`. Expect `"db": "ok"`
   and `"tick_db": "ok"`, with no `tick_detail`.

2. **Catalog.**
   ```bash
   curl -s localhost:8137/api/v1/futures/products | jq '.products[0].active'
   curl -s localhost:8137/api/v1/futures/products/ES/contracts | jq '.contracts | length'
   ```
   Expect two active lines (`v.0`, `c.0`) naming the same contract as
   `mt data tick active --product ES --json`, and 51 contracts. ESZ4 has
   `held.first_session` and `held.last_session`.

3. **Product bars across a roll.**
   ```bash
   curl -s 'localhost:8137/api/v1/futures/products/ES/bars?granularity=1d&start=2024-09-16&end=2024-09-21' | jq '{segments, unresolved, incomplete, count}'
   ```
   Expect ESU4 then ESZ4 in `segments`, with the switch on the session
   `mt data tick active` names. Compare the bars with `uv run mt data tick
   get 1d --product ES --start 2024-09-16 --end 2024-09-21 --json`: they are
   equal.

4. **Ticks within a session, and the ceiling.**
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' 'localhost:8137/api/v1/futures/contracts/ESZ4/ticks?start=2024-12-18&end=2024-12-19'
   curl -s 'localhost:8137/api/v1/futures/contracts/ESZ4/ticks?start=2024-12-18&end=2024-12-19&from=2024-12-18T14:30:00Z&until=2024-12-18T14:35:00Z' | jq '.count, .ticks[0]'
   ```
   Expect `422` for the whole session, with a message naming 75,000 and
   `from/until`. The 5-minute window returns 200 with string prices and
   9-digit times. Pick the session from `mt data tick coverage` if
   2024-12-18 is not among the largest.

5. **msgpack.** Add `&format=msgpack` to step 4's window. Expect
   `Content-Type: application/x-msgpack`, decoding to the same body.

6. **Status and overview.**
   ```bash
   curl -s localhost:8137/api/v1/status | jq .tick
   curl -s localhost:8137/api/v1/overview | jq '.sources[] | select(.name=="futures ticks")'
   ```
   Expect the same figures as `uv run mt data accounting --json | jq .tick`,
   and a `newest` of 2024-12-31 21:59 UTC.

7. **Tick database down: equities unaffected.** Restart the server with
   `MT_TICK_DB_URL=postgresql://x@127.0.0.1:1/x`.
   - `/health`: `tick_db: "unreachable"` with HTTP 200.
   - `/futures/products`: `503` with `{"error": "unreachable: …"}`.
   - `/api/v1/bars/AAPL?granularity=1d&start=2024-09-16&end=2024-09-20`:
     200 as before.
   - `/status`: `.tick.state == "unreachable"`.

8. **Not configured.** Restart with `MT_TICK_DB_URL` set **empty**
   (`MT_TICK_DB_URL= uv run mt serve …`). `env -u` is not enough: `Settings`
   reads `.env` itself, so an absent variable falls back to the file's value,
   while an empty one overrides it. Expect the not-configured text in the
   futures `503`s and `tick_db: "not_configured"`. (Corrected during the
   2026-10-13 walkthrough, `user/notes/231-walkthrough.md`.)

9. **Documentation gate.**
   ```bash
   uv run python scripts/check_api_docs.py --check
   ```
   Expect a pass. Open `docs/api/reference.md`'s futures section and check
   that the exclusive-end rule and the `unresolved`/`incomplete` semantics
   are stated.

10. **Production (after 234 only).** `mt-run`'s API on manta9000 answers
    `503 not configured` on the futures routes until 234 lands. That is the
    expected state for this slice, not a failure.

## Risk Assessment

### Technical Risks

- **Product-level latency.** Each product request opens 229's own
  connections (tick snapshot and calendar) on top of the pooled one, and
  `/api/v1/status` gains `accounting_lines`, which judges every in-scope
  session. Status is already over its load bound on `main` (2.79 s against
  1.5 s, a known failure).
- **Async pool behaviour on outage.** `AsyncConnectionPool` with
  `open(wait=False)` keeps retrying in the background. A checkout timeout
  set too long would make every tick request hang before its `503`.

### Mitigation Strategies

- **Latency:** measure in the load tier before setting bounds; the tick
  block's added cost to `/status` is a separate pass/fail item, so it cannot
  hide inside the route's pre-existing failure. If that cost is too high, the
  fix goes in the shared accounting path, which helps the CLI as well. A slow
  block is not grounds to drop it.
- **Outage behaviour:** the checkout timeout is a named constant, and an
  integration test against a closed port asserts that a `503` arrives
  within it.

## Implementation Notes

### Development Approach

1. Move `classify` into `line_state.py`; the CLI tests stay green.
2. `tick_access.py`, the `create_app`/`lifespan` changes and the `503`
   handler, with integration tests for the not-configured, unreachable,
   migrations-pending and recovery cases.
3. `catalog_reads.py` and the three catalog routes.
4. The read helper (plan, ceiling collector, clipping) and the four
   time-series routes, with field-for-field comparison tests against the
   CLI writers' output over `roll_seed.seed_september_roll` and
   `seed_ticks`.
5. The health, status and overview additions.
6. Documents: `reference.md` (a new futures section, and the §2.2
   exception, §2.7 `503`, §3.1, §3.5 and §5.3 updates); `agents.md`
   (capability index, window choice, the `503` retry verdict "retry later;
   the tick database is down or unconfigured", vocabularies); regenerate
   `openapi.json`; run the gate.
7. Load tier, then the document updates: the 220 architecture Revision Log
   entry, 180's tick exclusion marked closed, and 180's Range Policy
   "Extended by slice 231" paragraph.

Commit at each numbered step.

Test helpers: `test/tick_support/cli.py` (`clear_tick_env`, `closed_port`),
`test/tick_support/ticks.py` (`seed_ticks`), and `roll_seed`. Integration
tests use FastAPI's `TestClient` with `create_app(db_url=test_url,
tick_db_url=tick_test_url)`.

### Special Considerations

- **Event loop.** `tick_reads` and `get_plan` are async psycopg, so the
  time-series routes run them directly on the event loop, unlike the
  existing routes' `run_in_executor` over sync connections. 229's calendar
  reads already use `asyncio.to_thread`. Nothing blocking may be added to
  these routes.
- **Connection budget under `mt serve --workers N`.** Each worker has its
  own tick pool. A request in the roll path holds its pooled checkout and
  opens at most one roll snapshot connection to the tick database at a time,
  plus short-lived calendar connections to the primary, opened one after
  another (TD2). Per worker, at most:
  - tick database: `2 × TICK_API_POOL_MAX` = 8;
  - primary database: `TICK_API_POOL_MAX` = 4 calendar connections, on top
    of the existing pools.

  With 4 workers that is 32 tick connections and 16 extra primary
  connections. The task list checks both clusters' `max_connections`
  against these figures and records the values.

  **Dev values, checked 2026-10-13 (task 9.3).** `mt-serve.service` runs
  `mt serve` with no `--workers`, so one worker:

  | Cluster | `max_connections` | Existing API pools | Added by 231 | Per worker |
  |---|---|---|---|---|
  | Tick (manta9000:5433) | 30 | 0 | 8 | 8 |
  | Primary (192.168.1.144) | 100 | 26 (`db_pool` 8, minute 10, daily 8) | 4 | 30 |

  One worker fits both. Four workers would not fit the dev tick cluster
  (32 > 30), and the primary's existing pools alone would need 104 of 100
  before 231 adds any. Nothing configures more than one worker today;
  production's limits, for the configured `--workers`, are 234's check.
- **Security:** read-only sessions at the database level; no secret in any
  body; the tick credential is the DML-only application role (923), never
  the maintenance role.
