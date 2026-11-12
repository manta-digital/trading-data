---
docType: reference
title: Data Serving API reference
project: trading-data
dateCreated: 20260916
dateUpdated: 20261013
status: complete
---

# Data Serving API reference

Read-only HTTP routes over the equity bar store, the futures catalog, bars,
ticks and tick bars, the Kalshi prediction-market catalog and tape, and this
system's own operational state.

This document is **route-indexed**: one section per path, with a field table
giving each field's type, nullability, unit or time base, and meaning. If you
are an LLM client choosing a route by the question you are trying to answer,
read [`agents.md`](agents.md) instead — it indexes the same surface by
capability and states a retry verdict for every failure.

**Examples are executed, not invented.** Every `curl` block below was run
against production and its real output pasted, with a capture date. The
**values are illustrative and the shapes are contractual**: a price will have
moved by the time you read this, a `count` will differ, and `now` is always
stale. What does not change without a corresponding change to this document is
the set of fields, their types, and their meanings.

**This is not `docs/api/foundation_usage_examples.md`.** Despite living in this
directory, that file documents the slice-750 Python library modules — how to
call the acquisition and storage layers from Python. It has nothing to say
about the HTTP API. If you arrived there looking for endpoints, you want this
file.

## 1. Quick start

The server is started with `mt serve`:

```sh
mt serve                                          # default: 0.0.0.0:8100
mt serve --host 127.0.0.1 --port 8200 --workers 4
mt serve --reload                                 # dev, auto-reload
```

All routes are prefixed `/api/v1`. There is no authentication — see
[§2.8](#28-auth-cors-and-pagination).

A first request:

```sh
curl 'http://127.0.0.1:8137/api/v1/health'
```

```json
{"status":"ok","db":"ok","coverage":"ok"}
```

*Captured 2026-09-16.*

Interactive documentation is at `/docs` (Swagger UI), and the machine-readable
schema is committed at [`openapi.json`](openapi.json), regenerated with
`uv run python scripts/dump_openapi.py`. A test fails the build on drift.

## 2. Conventions

These apply to every route. Each endpoint section below assumes them rather
than restating them.

### 2.1 Time

Every timestamp field — anything named `*_ts`, `*_time`, or `*_at` — is **UTC**,
serialized as RFC 3339 with a `Z` suffix (`2026-09-15T00:00:01.953793Z`).

Timestamp *input* is read as UTC when it carries no offset: a naive
`2026-09-10T00:00:00` means midnight UTC, not midnight local. Supply an offset
if you mean something else.

Date-only fields (`available.*.start`, `available.*.end`) are UTC calendar
dates with no time component.

### 2.2 Date windows are inclusive at both ends

(The Kalshi routes are the exception: half-open `[start, end)`, §5.1 and §5.2.)

`start` and `end` are both inclusive, at every granularity:
`start=2024-06-10&end=2024-06-14` returns Monday through Friday, and
`start=2024-06-10&end=2024-06-10` returns that whole day. The equity store
covers 08:00–23:59 UTC.

**The one exception is `/api/v1/gaps/{symbol}`**, whose day end is resolved to
the *next* midnight and treated as exclusive. This is not an inconsistency to
be tidied away — it is a fix. Converting an inclusive `end` to midnight made
the bound effectively exclusive on the minute path: a Monday–Friday `1m`
request returned Monday–Thursday, silently, with nothing in the response to say
so. Measured on production 2026-08-04: 2,975 bars ending 06-13 23:59 for a
window ending 06-14. The correction lives at
[`routes/windows.py:25-36`](../../src/manta_trading/api_server/routes/windows.py),
and `window_end_utc` now resolves an inclusive day end to that day's last
instant.

**The futures routes (`/api/v1/futures/*`) differ by design**: their `start`
and `end` are *session dates* and `end` is **exclusive**, plus half-open
`from`/`until` instants on the tick routes. A CME session opens the evening
before its date, so a UTC-day window would cut every session in two. See §6.1.

### 2.3 Numbers: decimals are strings on the wire

Every field suffixed `_dollars` or `_fp` is a **decimal string**, not a JSON
number: `"0.4900"`, `"661247.92"`. This is deliberate and applies to both
encodings.

The reason is that `NUMERIC` values must not round-trip through a float.
Pydantic serializes with `mode="json"`, which renders `Decimal` as a string
before either encoder runs
([`serialization.py:30-36`](../../src/manta_trading/api_server/serialization.py)).
A client that needs arithmetic should parse these into its own decimal type,
never into a float.

This has a consequence for `format=msgpack`, covered in
[§7](#7-msgpack-what-it-actually-saves): because the values are already strings
by the time msgpack sees them, it cannot pack them any tighter than JSON can.

Plain integers and floats — `count`, `volume`, `bars_stored`, `lag_seconds` —
are ordinary JSON numbers. OHLCV prices on equity bars are JSON floats.

### 2.4 Reading an empty result

**A `200` with zero rows is the normal case, not an error.** A weekend, a
holiday, a pre-listing date, or a quiet market are all ordinary. A `404` means
exactly one thing: the identifier is unknown.

For Kalshi time series, a `count: 0` has **four** distinct meanings, and every
response carries the facts needed to tell them apart without a second call:

- **`collected: false`** (candlesticks) — this market is not in the candle
  collection set, so no candles are stored for it at all. Not an empty window.
- **`tape_filtered: true`** (trades) — this market's category is excluded from
  trade collection by policy, so an empty result is expected rather than a gap.
- **The window is outside coverage.** `coverage_from` is the oldest instant the
  data reaches; `complete_through` (candles) is what was requested and stored
  through — not "the newest stored candle", since a quiet market produces no
  candle for a period it was nonetheless asked for. `tape_complete_through`
  (trades) is the created time of the newest stored trade; `null` means the
  trades collection phase has never run, not that the tape is empty.
- **Genuinely no activity**, when none of the above applies.

For equity bars, an empty `available` map on `/api/v1/symbols/{symbol}` means
"no bars for this symbol", not "unknown symbol".

For `/api/v1/status`, a `count: 0` means **nothing is wrong** — see
[§3.5](#35-get-apiv1status).

### 2.5 The range cap

There is no pagination and no silent truncation: a response is complete or it
is refused with a `422`.

The ceiling is **75,000 rows**, shared by every list and time-series route.
<!-- from: manta_trading.constants.API_MAX_BARS_PER_REQUEST -->
It is configurable as `MT_API_MAX_BARS_PER_REQUEST`.

The ceiling counts **rows, not bytes**. A wide row and a narrow one cost the
same against it.

Two routes reach the ceiling by different routes, and the difference is visible
in the error message:

- **Equity bars estimate** from the request window alone, *before any database
  work*: `span_days × bars_per_trading_day × (252/365)`. The `422` names the
  estimate, the ceiling, and the maximum span for that granularity.
- **Kalshi list and time-series routes take an exact `count(*)`** before
  reading any rows, and quote the actual count. Their row density does not
  follow the window the way a bar series' does, so an estimate would be
  worthless.
- **Futures routes stop reading at the ceiling** and quote no count: a single
  session can hold hundreds of thousands of ticks, so a `count(*)` would cost a
  full scan on every request, and tick density varies too much to estimate. A
  refused request reads at most the ceiling plus one fetch batch. See §6.1.

Because the equity store covers extended hours (08:00–23:59 UTC, ~960
one-minute bars on a dense day), the cap binds only at intraday grains:

| Granularity | Max span per request (at 75,000) |
|---|---|
| `1m` | ~113 days |
| `5m` | ~565 days |
| `15m` | ~1,697 days |
| `1h` and coarser | effectively unbounded |

For bulk history beyond these spans, query TimescaleDB directly rather than
paging over HTTP.

### 2.6 Staleness

Two different signals, on different routes, answering different questions.

| Signal | Where | What it means |
|---|---|---|
| `is_stale` | `/api/v1/bars/{symbol}` | The continuous aggregate serving this granularity is behind its source, so the bars may be incomplete. |
| `coverage` | `/api/v1/health` | A coarse whole-system freshness verdict: `ok` or not. |
| `health` | `/api/v1/status` | Per-symbol, per-granularity data health. See [§3.5](#35-get-apiv1status). |

**Raw grains are never stale by construction.** `1m` and `1d` are read straight
from their hypertables; there is no continuous aggregate between the store and
the response that could be behind. `is_stale` is `false` for them because
nothing exists that could make it true — not because they happen to be fresh
right now. The derived grains (`5m`, `15m`, `1h`, `4h`, `1w`, `1mo`, `1q`) are
cagg-backed and can genuinely lag.

What to do about each: `is_stale: true` means re-request later if you need
completeness, or drop to a raw grain and aggregate yourself. A `coverage` that
is not `ok` is an operational condition, not a client error — the data you get
is still what is stored.

### 2.7 Errors

Every error this server raises has the same body:

```json
{ "error": "<message>" }
```

The **one exception** is FastAPI's own request-validation failure, which keeps
its native body so clients retain the per-field detail:

```json
{ "detail": [ { "loc": ["query", "granularity"], "msg": "…", "type": "…" } ] }
```

| Status | Meaning |
|---|---|
| `404` | The symbol, series, event or market is not known. **Only** that. |
| `422` | The request is malformed, the range is reversed, or the result would exceed the row ceiling. |
| `500` | An unexpected server fault. The body is sanitized. |
| `503` | Futures routes only: the tick database is not configured, unreachable, or has migrations pending (§6.1). Every other route is unaffected. |
| `504` | The database cancelled the query at the statement timeout. Narrow the range or use a coarser granularity. |

#### The two `422` shapes

**This is the most likely thing to break a client**, so it is stated plainly:
`openapi.json` declares `HTTPValidationError` — the `{"detail": [...]}` shape —
for `422` on **every** route that has one. But the hand-written `422`s in
this codebase send `{"error": "…"}` instead, through the exception handler at
[`app.py:192`](../../src/manta_trading/api_server/app.py). A generated client
that parses the declared shape will fail to parse the most informative
messages the API produces.

Which condition produces which:

| Condition | Shape | Produced by |
|---|---|---|
| Unparseable date, unknown enum value, wrong scalar type | `{"detail": [...]}` | FastAPI's own request validation, before the handler runs |
| Reversed range, result over the row ceiling, invalid status filter | `{"error": "…"}` | A hand-written refusal inside the route |

Both, from the same route, captured 2026-09-16:

```sh
# FastAPI's own validation: '2d' is not a Granularity member
curl -s 'http://127.0.0.1:8137/api/v1/bars/SPY?granularity=2d&start=2024-06-10&end=2024-06-14'
```

```json
{"detail":[{"type":"enum","loc":["query","granularity"],"msg":"Input should be '1m', '5m', '15m', '1h', '4h', '1d', '1w', '1mo' or '1q'","input":"2d","ctx":{"expected":"'1m', '5m', '15m', '1h', '4h', '1d', '1w', '1mo' or '1q'"}}]}
```

```sh
# A hand-written refusal: the window exceeds the row ceiling
curl -s 'http://127.0.0.1:8137/api/v1/bars/SPY?granularity=1m&start=2013-08-13&end=2026-09-12'
```

```json
{"error":"requested range spans about 3,167,495 1m bars, over the 75,000 bar limit; at 1m request at most 113 days per call, or use a coarser granularity"}
```

Parse defensively: check for `error` first, then `detail`.

This mismatch is **documented rather than fixed** in this slice — the fix
changes a published schema, which is a client-visible contract change. It is
recorded as Future Work in
[`180-slices.data-serving-api.md`](../../project-documents/user/architecture/180-slices.data-serving-api.md).

#### Remedy strings

Refusals that a caller can act on quote one of four fixed remedies, defined
once in [`admission.py`](../../src/manta_trading/api_server/admission.py):

- `narrow the filter` — a catalog list; bound it with query parameters.
- `narrow start/end` — a time series; bound it with a shorter window.
- `narrow start/end or from/until` — futures ticks; a session can exceed the
  ceiling, so the instant bounds are the way through it (§6.1).
- `raise ticks_per_bar, or narrow start/end or from/until` — futures tick
  bars over the row ceiling; fewer, larger bars also fit (§6.9).

### 2.8 Auth, CORS and pagination

- **No authentication.** Every route is unauthenticated and read-only. The
  server is intended to run behind a trusted boundary; do not expose it
  directly to the internet.
- **CORS** is controlled by `MT_API_CORS_ORIGINS`. It is empty by default,
  which means no cross-origin browser access.
- **No pagination anywhere.** A request is answered completely or refused with
  a `422` (§2.5). There is no cursor, no page token, and no `Link` header.
- **No rate limiting** at the HTTP layer.
- Every route is `GET`. Nothing in this API mutates anything.

## 3. Endpoints: equity bars and operations

### 3.1 `GET /api/v1/health`

<!-- endpoint: /api/v1/health
     params: -
     errors: 200 -->

Liveness, plus a coarse freshness verdict. Takes no parameters. This is the
route to poll from a monitor.

| Field | Type | Null? | Meaning |
|---|---|---|---|
| `status` | string | no | `ok` when the server is serving. |
| `db` | string | no | `ok` when the pooled connection answers. |
| `coverage` | string | no | Coarse whole-system freshness verdict. |
| `tick_db` | string | no | The futures tick database's state; see below. |
| `tick_detail` | string | — | Why `tick_db` is not `ok`. Absent when it is. |

Tick states are `ok` `not_configured` `unreachable` `migrations_pending`
`failed`.
<!-- from: manta_trading.data.tick.line_state.TickLineState -->
They are kept apart because the remedies differ: `not_configured` needs a
setting, `unreachable` needs the database, `migrations_pending` needs a
migration. A tick outage never changes `status` or `db`, and the route still
answers `200`.

```sh
curl 'http://127.0.0.1:8137/api/v1/health'
```

```json
{"status":"ok","db":"ok","coverage":"ok","tick_db":"ok"}
```

*Captured 2026-10-13 with the dev tick database configured.* Without one:
`"tick_db":"not_configured","tick_detail":"MT_TICK_DB_URL unset"`.

**Errors**: none. This route answers `200` or the server is not running.

**Do not use this to ask whether your data is fresh** — `coverage` is a
whole-system verdict, not a statement about the symbol and granularity you care
about. For that, use `/api/v1/status`.

### 3.2 `GET /api/v1/bars/{symbol}`

<!-- endpoint: /api/v1/bars/{symbol}
     params: granularity:Granularity, start:date, end:date,
             adjusted:boolean, prints:PrintMode, format:json|msgpack
     errors: 422, 504 -->

OHLCV bars for one instrument over an inclusive date window.

**Parameters**

| Name | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `granularity` | `Granularity` | yes | — | Bar size; see the token set below. |
| `start` | date | yes | — | Inclusive first day, UTC. |
| `end` | date | yes | — | Inclusive last day, UTC (§2.2). |
| `adjusted` | boolean | no | `true` | Split/dividend-adjusted prices. |
| `prints` | `PrintMode` | no | `eligible` | `eligible`: tape-eligible bars, every grain. `all`: raw bars, `1m` only (below). |
| `format` | `json` \| `msgpack` | no | `json` | Response encoding (§7). |

Granularity tokens are
`1m` `5m` `15m` `1h` `4h` `1d` `1w` `1mo` `1q`.
<!-- from: manta_trading.constants.Granularity -->

`1m` and `1d` are raw; every other grain is served by a continuous aggregate
and can report `is_stale: true` (§2.6).

Print modes are `all` `eligible`.
<!-- from: manta_trading.constants.PrintMode -->

**Off-tape prints and `prints`.** The provider's 1-minute bars
include trades that the consolidated tape's rules keep out of a bar's high,
low and last price: late-reported, average-price and prior-reference-price
trades. They show up as tall isolated wicks, mostly before 09:30 and after
16:00 ET. Every stored minute bar carries a mark saying which of its open,
high, low and close are such prints; stored prices are never changed.

- **Client-visible change (slice 173):** `prints` defaults to `eligible` at
  every grain. It used to default to `all`, and `5m` and coarser served raw
  highs and lows. A client that wants raw prints asks for `prints=all` at
  `1m` and rolls them up itself.
- `prints=eligible` (the default) serves each marked price clamped into the
  range of the bar's unmarked prices: a marked high falls to the bar's
  highest eligible price. A minute whose four prices are all marked is
  **absent**, price and volume, the way a minute with no trades is absent.
  `5m`, `15m`, `1h` and `4h` bars are built from those eligible minutes, and
  a bucket whose minutes are all marked is absent the same way. `1d` and
  coarser are the provider's end-of-day bars and are served unchanged.
  Adjustment (`adjusted=true`) applies after the clamp.
- `prints=all` serves the stored bars exactly, at `1m` only. Any other
  granularity answers `422` with `prints=all is served at 1m only; roll up
  the 1m bars`.
- The detector judges each price against the closes of the ten bars on each
  side. A print close to the market (on SPY, within about 0.3%) stays
  unmarked, so `eligible` removes the spikes, not every off-tape trade.

**Response**

| Field | Type | Null? | Unit / time base | Meaning |
|---|---|---|---|---|
| `symbol` | string | no | — | The instrument, echoed back. |
| `granularity` | string | no | — | The requested grain, echoed back. |
| `adjusted` | boolean | no | — | Whether prices are adjusted. |
| `prints` | string | no | — | The `prints` mode served, echoed back. |
| `is_stale` | boolean | no | — | The serving aggregate is behind its source (§2.6). |
| `count` | integer | no | rows | Number of bars returned. |
| `bars[].timestamp` | string | no | UTC, RFC 3339 | Bar open instant. |
| `bars[].open` | float | no | price | First trade price in the bar. |
| `bars[].high` | float | no | price | Highest trade price. |
| `bars[].low` | float | no | price | Lowest trade price. |
| `bars[].close` | float | no | price | Last trade price. |
| `bars[].volume` | integer | no | shares | Shares traded in the bar. |

Equity OHLCV prices are JSON floats, not decimal strings — unlike the Kalshi
routes (§2.3).

```sh
curl 'http://127.0.0.1:8137/api/v1/bars/SPY?granularity=1d&start=2024-06-10&end=2024-06-14'
```

```json
{
  "symbol": "SPY",
  "granularity": "1d",
  "adjusted": true,
  "prints": "eligible",
  "is_stale": false,
  "count": 5,
  "bars": [
    {"timestamp":"2024-06-10T00:00:00Z","open":531.8102650701792,"high":534.6130462038436,"low":531.2018321550421,"close":534.2838939710646,"volume":35686100},
    {"timestamp":"2024-06-11T00:00:00Z","open":532.6979786676744,"high":535.6304258324336,"low":530.6831680306628,"close":535.5705799719284,"volume":36383400}
  ]
}
```

*Captured 2026-09-16, `prints` added 2026-10-16 (slice 171) and its default
changed to `eligible` (slice 173). Truncated: 5 bars
returned, 2 shown — Monday through Friday inclusive, which is §2.2 in one
request.*

The same minute raw and eligible. SPY's 16:01 ET bar carries a 770.3516 high
the market never traded near (it traded about 766). The default serves the
eligible bar:

```sh
curl 'http://127.0.0.1:8137/api/v1/bars/SPY?granularity=1m&start=2026-09-08&end=2026-09-08&adjusted=false'
```

```json
{
  "symbol": "SPY",
  "granularity": "1m",
  "adjusted": false,
  "prints": "eligible",
  "is_stale": false,
  "count": 958,
  "bars": [
    {"timestamp":"2026-09-08T20:01:00Z","open":765.96,"high":765.96,"low":765.85,"close":765.9196,"volume":227166},
    {"timestamp":"2026-09-08T20:02:00Z","open":765.88,"high":766.02,"low":765.71,"close":765.85,"volume":326122}
  ]
}
```

*Captured 2026-10-16 against a test database holding production's SPY
2026-09-08 bars (production serves `prints` from the release that applies
migration 062). Truncated: 958 bars returned, 2 shown. With `prints=all` the
20:01 bar's `high` is `770.3516`. The 5m and 1h bars holding that minute do
not carry it either.*

**Errors**

| Status | Condition |
|---|---|
| `404` | The symbol is not in `instruments`. |
| `422` | Reversed range, unparseable date, unknown granularity or `prints` value, `prints=all` above `1m`, or the estimate exceeds the row ceiling (§2.5). |
| `504` | The database cancelled the query at the statement timeout. |

### 3.3 `GET /api/v1/symbols`

<!-- endpoint: /api/v1/symbols
     params: search:string
     errors: 422, 504 -->

List instruments, optionally filtered by ticker prefix or company name.

**Parameters**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `search` | string | no | Case-insensitive text matched against the start of the ticker or anywhere in the name. An exact ticker comes first, then other ticker-prefix matches, then name-only matches, each group by ticker. `%` and `_` match literally. Omit for the whole registry. |

**Response**

| Field | Type | Null? | Meaning |
|---|---|---|---|
| `count` | integer | no | Instruments returned. |
| `symbols[].symbol` | string | no | Ticker. |
| `symbols[].exchange` | string | yes | Listing exchange. |
| `symbols[].type` | string | yes | Instrument type, e.g. `ETF`, `Common Stock`. |
| `symbols[].asset_class` | string | yes | e.g. `equity`. |
| `symbols[].active` | boolean | no | Whether the instrument is currently listed. |
| `symbols[].name` | string | yes | Company or index name from EODHD's symbol list; null until the registry is rebuilt after migration 065. |

```sh
curl 'http://127.0.0.1:8137/api/v1/symbols?search=SPY'
```

```json
{
  "symbols": [
    {"symbol":"SPY","exchange":"NYSE ARCA","type":"ETF","asset_class":"equity","active":true,"name":"State Street® SPDR® S&P 500® ETF Trust"},
    {"symbol":"SPYA","exchange":"BATS","type":"ETF","asset_class":"equity","active":true,"name":"Twin Oak Endure ETF"}
  ],
  "count": 17
}
```

*Captured 2026-09-16; `name` values from EODHD's symbol list, 2026-11-01. Truncated: 17 instruments returned, 2 shown.*

There is **no row ceiling** on this route: an unfiltered call returns the whole
registry (about 32,000 rows, 3 MB, measured 0.31 s on production, 2026-10-03).
Filter with `search` only to reduce what you transfer.

**Errors**: `504` on statement timeout. The `422` in the marker above is
FastAPI's generic validation response; no input here triggers it, and the route
never sends the row-ceiling `422` (§2.7).

### 3.4 `GET /api/v1/symbols/{symbol}`

<!-- endpoint: /api/v1/symbols/{symbol}
     params: -
     errors: 422, 504 -->

One instrument, with the date range available at each granularity.

**Response**

The instrument fields are those of §3.3, plus:

| Field | Type | Null? | Unit / time base | Meaning |
|---|---|---|---|---|
| `available` | object | no | — | Granularity to `{start, end}`. A grain with no data is **omitted entirely**. |
| `available.<grain>.start` | string | no | UTC date | First day with data — see the asymmetry below. |
| `available.<grain>.end` | string | no | UTC date | Last day with data. |

**`start` and `end` are computed differently and carry different guarantees.**
This matters if you use them to decide what to request:

- **`end` is exact.** It comes from a direct probe of the bar tables, bounded so
  it stays fast, and it reflects data written right up to the moment of the
  request. If a bar exists, `end` includes it.
- **`start` is as of the last coverage materialization.** It comes from the
  coverage continuous aggregates, which a background policy refreshes. Deep
  history *backfilled after* the relevant coverage bucket was last materialized
  will not move `start` until that bucket is rebuilt — so `start` can be later
  than the true first bar, never earlier. There is no cheap exact answer:
  probing below the coverage floor costs 0.4–1.4 s per symbol on production
  (measured), because the bound excludes chunks *after* the start, which for a
  symbol with deep history is almost none of them.

**One documented gap.** The leading-edge probe is bounded by a universe-wide
coverage edge rather than each symbol's own. A bar could in principle be missed
if it falls between an individual symbol's coverage end and that universe edge
*and* was written after coverage last materialized. Measured across a 28-symbol
sample on production 2026-08-04 — dense, delisted, daily-only, and no-data
instruments — the merged answer was **identical to a direct `MIN/MAX` scan for
every symbol**, and no symbol had a single raw bar inside that window. The gap
closes on its own when the coverage refresh repair lands.

An empty `available` means "no bars for this symbol", not "unknown symbol" — an
unknown symbol is a `404`.

**Errors**: `404` for an unknown symbol; `504` on statement timeout. The `422` in
the marker above is FastAPI's generic validation response; the route has no
other `422` path.

```sh
curl 'http://127.0.0.1:8137/api/v1/symbols/SPY'
```

```json
{
  "symbol": "SPY",
  "exchange": "NYSE ARCA",
  "type": "ETF",
  "asset_class": "equity",
  "active": true,
  "name": "State Street® SPDR® S&P 500® ETF Trust",
  "available": {
    "1d": {"start":"1993-01-29","end":"2026-09-15"},
    "1m": {"start":"2013-08-13","end":"2026-09-12"}
  }
}
```

*Captured 2026-09-16. Truncated: nine grains returned
(`1d` `1w` `1mo` `1q` `1m` `5m` `15m` `1h` `4h`), two shown.*

**Errors**: `404` if the symbol is unknown; `504` on statement timeout.

### 3.5 `GET /api/v1/status`

<!-- endpoint: /api/v1/status
     params: symbol:string, health:string, granularity:daily|minute,
             all:boolean
     errors: 422, 504 -->

Per-symbol data-health rows, a whole-registry health summary, and the coverage
freshness verdict.

**`count: 0` means nothing is wrong — not "no such symbol".** This is the
single most expensive misreading this API affords, and it follows from the
default filter: `rows` defaults to **unhealthy entries only** (`GAPS`, `STALE`,
`FAILED`), matching `mt data status`. A perfectly healthy symbol therefore
returns `count: 0` and an empty `rows`. Pass `all=true` for everything, or
`health=OK` for healthy rows. An unknown symbol is a `404`.

`summary` is always the **full unfiltered whole-registry breakdown**, whatever
`rows` was filtered to. It does not describe the rows beside it.

**Parameters**

| Name | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `symbol` | string | no | — | Restrict rows to one instrument. |
| `health` | string | no | — | Filter by health token. |
| `granularity` | `daily` \| `minute` | no | — | Restrict to one storage family. |
| `all` | boolean | no | `false` | Return healthy rows too. |

**This route's `granularity` is a different vocabulary** from the bars route's.
It takes the storage family — `daily` or `minute` — not a `Granularity` member.
See §2 and §3.6 for the three related-looking granularity parameters.

Health tokens are `OK` `GAPS` `STALE` `FAILED`.
<!-- from: manta_trading.cli.rendering.status_table.HealthStatus -->

**Response**

| Field | Type | Null? | Unit / time base | Meaning |
|---|---|---|---|---|
| `scope` | string | no | — | `symbol` or `registry`. |
| `symbol` | string | yes | — | Echoed when scoped to one instrument. |
| `count` | integer | no | rows | Rows returned **after filtering** — see above. |
| `rows[].symbol` | string | no | — | Ticker. |
| `rows[].granularity` | string | no | — | `daily` or `minute`. |
| `rows[].health` | string | no | — | One of the four health tokens. |
| `rows[].bars_stored` | integer | no | rows | Bars held for this pair. |
| `rows[].first_bar_ts` | string | yes | UTC | Oldest stored bar. |
| `rows[].last_bar_ts` | string | yes | UTC | Newest stored bar. |
| `rows[].gap_count` | integer | no | gaps | **Open** gaps only — see below. |
| `rows[].last_attempt_ts` | string | yes | UTC | Last acquisition attempt. |
| `rows[].last_attempt_outcome` | string | yes | — | Outcome of that attempt. |
| `rows[].target_end_ts` | string | yes | UTC | Where acquisition is trying to reach. |
| `rows[].effective_start` | string | yes | UTC date | First day acquisition considers in scope. |
| `summary` | object | no | counts | Whole-registry health breakdown, unfiltered. |
| `coverage` | object | no | — | Freshness verdict; see below. |
| `tick` | object | no | — | The futures tick tier; see below. |

Two of these changed meaning in slice 922 without changing name or type
([`models/responses.py:208-222`](../../src/manta_trading/api_server/models/responses.py)):

- **`gap_count` counts open gaps only** — those with `fetch_status` `UNKNOWN`
  or `FAILED_RETRYABLE`, the ones still being asked about. `PROVIDER_HOLE` is a
  terminal answer from the provider and `RETRY_EXHAUSTED` a terminal failure
  (reflected in `health: FAILED`); counting either made this a number that
  could never reach zero.
- **`health: STALE`** now means "not attempted in the last recorded universe
  walk", rather than "not attempted within a fixed interval".

**The coverage verdict**

| Field | Type | Null? | Unit | Meaning |
|---|---|---|---|---|
| `coverage.is_stale` | boolean | no | — | Any view is behind. |
| `coverage.verdicts[].view_name` | string | no | — | The continuous aggregate judged. |
| `coverage.verdicts[].is_fresh` | boolean | no | — | Whether it is within threshold. |
| `coverage.verdicts[].signals` | array of string | no | — | Why it is not fresh, when it is not. |
| `coverage.verdicts[].lag_seconds` | float | yes | seconds | How far behind. **`null` is not `0.0`.** |
| `coverage.verdicts[].threshold_seconds` | float | yes | seconds | The bound it is judged against. |
| `coverage.verdicts[].detail` | string | no | — | Human-readable summary. |

`lag_seconds` is a float, and `null` is preserved rather than collapsed to
`0.0`: "could not be measured" and "no lag" are different facts, and a client
must be able to tell them apart
([`models/responses.py:171-178`](../../src/manta_trading/api_server/models/responses.py)).

```sh
curl 'http://127.0.0.1:8137/api/v1/status?symbol=SPY'
```

```json
{
  "scope": "symbol",
  "symbol": "SPY",
  "count": 0,
  "rows": [],
  "summary": {"FAILED":273,"GAPS":4349,"STALE":38590,"OK":20939},
  "coverage": {"is_stale":false,"verdicts":[
    {"view_name":"minute_coverage","is_fresh":true,"signals":[],"lag_seconds":0.0,"threshold_seconds":705600.0,"detail":"minute_coverage: fresh (lag=0:00:00, threshold=8 days, 4:00:00)"}
  ]}
}
```

*Captured 2026-09-16. `count: 0` with a healthy SPY — the default filter at
work, not an absent symbol. Truncated: two verdicts returned, one shown.*

**The `tick` block** is always present and ignores `symbol`, `health` and
`granularity`, which select equity rows. It holds the same figures as
`mt data accounting --json`'s `tick`, from the same function:

| Field | Type | Null? | Meaning |
|---|---|---|---|
| `tick.state` | string | no | A tick state (§3.1). |
| `tick.detail` | string | yes | Why it is not `ok`. |
| `tick.products[].product` | string | no | Product code. |
| `tick.products[].sessions_in_scope` | integer | no | Sessions judged. |
| `tick.products[].sessions_complete` | integer | no | Of those, complete. |
| `tick.products[].buckets` | object | no | Sessions per status (§6.1's session statuses). |
| `tick.products[].records` | integer | no | Ticks held under current data. |
| `tick.products[].families[]` | array | no | One entry per family the product wants (the tick tier, `ohlcv-1m` bars, `statistics`); empty when it wants none. The product-level fields above are the tick tier's. |
| `tick.products[].families[].family` | string | no | `tick_tier`, `bar_1m` or `statistics`. |
| `tick.products[].families[].schema` | string | no | The schema the product wants the family as (`tbbo`, `ohlcv-1m`, `statistics`). |
| `tick.products[].families[].sessions_in_scope` | integer | no | Sessions judged for this family. |
| `tick.products[].families[].sessions_complete` | integer | no | Of those, complete. |
| `tick.products[].families[].buckets` | object | no | Sessions per status, as above. |
| `tick.products[].families[].records` | integer | no | Records held for this family under current data. |
| `tick.products[].families[].caught_up` | boolean | yes | Every session judged up to `source_edge` is complete, or a hole the provider declared. **`null` means no dataset edge is known or no range is wanted. It is never "assume yes".** |
| `tick.products[].families[].source_edge` | string | yes | UTC instant: the last moment the provider can have published this family (statistics lag a session behind the rest). `null` when no dataset edge is known. |
| `tick.products[].families[].load_complete_only` | boolean | no | `true` when nothing checks this family's traded volume against CME's cleared volume, so it is judged on units loaded only. |

The cross-tier counts (`traded_vs_cleared`) are not served here; `mt data
accounting`, `mt data health` and `mt data tick status` carry them.

```json
"tick": {"state":"ok","detail":null,"products":[{"product":"ES","sessions_in_scope":64,"sessions_complete":61,
  "buckets":{"retry_exhausted":0,"failed":0,"provider_hole":0,"in_flight":0,"awaiting_ingest":0,"missing":2,"pending":0,"edge_unknown":1,"complete":61},
  "records":27691412,"families":[{"family":"tick_tier","schema":"tbbo","sessions_in_scope":64,"sessions_complete":61,
  "buckets":{"complete":61,"missing":2,"edge_unknown":1},"records":27691412,"caught_up":false,
  "source_edge":"2025-01-01T00:00:00Z","load_complete_only":false}]}]}
```

*Captured 2026-10-13 against the dev tick database.* A tick outage is the
block's `state` with `products: []`; the route still answers `200` with its
equity rows.

**Errors**: `404` for an unknown symbol; `422` for an invalid health or
granularity token; `504` on statement timeout.

### 3.6 `GET /api/v1/gaps/{symbol}`

<!-- endpoint: /api/v1/gaps/{symbol}
     params: granularity:Granularity, start:date, end:date
     errors: 422, 504 -->

Known gaps in one instrument's stored data.

**Parameters**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `granularity` | `Granularity` | no | A `Granularity` member, mapped to its storage family. |
| `start` | date | no | Window start, UTC. |
| `end` | date | no | Window end — **exclusive at the next midnight**, see §2.2. |

**Three related-looking granularity parameters, documented once.** They are not
interchangeable:

| Route | Accepts | Note |
|---|---|---|
| `/api/v1/bars/{symbol}` | a `Granularity` member | Used directly as the bar size. |
| `/api/v1/gaps/{symbol}` | a `Granularity` member | Mapped to the storage family (`daily` or `minute`) at [`gaps.py:29`](../../src/manta_trading/api_server/routes/gaps.py); the response echoes the **family**, not the member you sent. |
| `/api/v1/status` | `daily` \| `minute` | The storage family directly. **Not** a `Granularity`. |

**Response**

| Field | Type | Null? | Unit / time base | Meaning |
|---|---|---|---|---|
| `symbol` | string | no | — | Echoed back. |
| `count` | integer | no | rows | Gaps returned. |
| `gaps[].gap_start` | string | no | UTC | First missing instant. |
| `gaps[].gap_end` | string | no | UTC | Last missing instant. |
| `gaps[].granularity` | string | no | — | Storage **family**, not the member requested. |
| `gaps[].fetch_status` | string | no | — | `UNKNOWN`, `FAILED_RETRYABLE`, `PROVIDER_HOLE`, `RETRY_EXHAUSTED`. |
| `gaps[].attempt_count` | integer | no | attempts | Times acquisition has tried. |
| `gaps[].last_attempt_ts` | string | yes | UTC | Last attempt. |

Unlike `/api/v1/status`'s `gap_count`, this listing includes terminal statuses.

```sh
curl 'http://127.0.0.1:8137/api/v1/gaps/GJT?granularity=1m&start=2026-01-02&end=2026-01-06'
```

```json
{
  "symbol": "GJT",
  "count": 1,
  "gaps": [
    {"gap_start":"2026-01-05T14:30:00Z","gap_end":"2026-01-09T21:00:00Z","granularity":"minute","fetch_status":"UNKNOWN","attempt_count":2,"last_attempt_ts":"2026-09-12T13:54:19.629072Z"}
  ]
}
```

*Captured 2026-09-16. Note `granularity` echoes `minute` — the family — for a
request that sent `1m`.*

**This route does not tell you whether a symbol exists.** A symbol with no
gaps and a symbol whose data is perfect both return `count: 0`. Use
`/api/v1/symbols/{symbol}` for existence.

**Errors**: `404` for an unknown symbol; `422` for a reversed range or unknown
granularity; `504` on statement timeout.

## 4. Endpoints: Kalshi catalog

Eight routes over the prediction-market catalog: categories, series, events and
markets, in that containment order, plus a plain-language search across all
three levels (§4.8).

**Field names are Kalshi's, verbatim.** Catalog records use the DB/Kalshi
column names unchanged, so Kalshi's own field documentation applies to them
without translation
([`models/kalshi_catalog.py:201-206`](../../src/manta_trading/api_server/models/kalshi_catalog.py)).
Where a field's meaning is Kalshi's to define — `fee_type`,
`collateral_return_type`, `strike_type` — this reference links out rather than
paraphrasing a vocabulary this project does not own. See
[Kalshi's API documentation](https://trading-api.readme.io/).

Market status tokens, used by the filter on §4.6 and reported by §4.7, are
`active` `amended` `closed` `determined` `finalized` `inactive` `initialized`.
<!-- from: manta_trading.data.kalshi.constants.MarketStatus -->

### 4.1 `GET /api/v1/kalshi/categories`

<!-- endpoint: /api/v1/kalshi/categories
     params: -
     errors: 504 -->

Every series category with its series count.

**Start here.** Categories are free text Kalshi assigns rather than a fixed
vocabulary, so this is the only way to learn what `category=` accepts on §4.2,
and the counts double as a size map for planning calls into a large category.

| Field | Type | Null? | Meaning |
|---|---|---|---|
| `count` | integer | no | Number of distinct categories. |
| `categories[].category` | string | no | The category name, as Kalshi assigns it. |
| `categories[].series_count` | integer | no | Series in that category. |

```sh
curl 'http://127.0.0.1:8137/api/v1/kalshi/categories'
```

```json
{
  "count": 20,
  "categories": [
    {"category":"AI","series_count":5},
    {"category":"Climate and Weather","series_count":390},
    {"category":"Commodities","series_count":82}
  ]
}
```

*Captured 2026-09-16. Truncated: 20 categories returned, 3 shown.*

**Errors**: `504` on statement timeout.

### 4.2 `GET /api/v1/kalshi/series`

<!-- endpoint: /api/v1/kalshi/series
     params: category:string, search:string
     errors: 422, 504 -->

Series matching a filter.

| Name | Type | Required | Meaning |
|---|---|---|---|
| `category` | string | no | Exact category match; get valid values from §4.1. |
| `search` | string | no | Ticker prefix filter. To search by title, use §4.8. |

**Response**

| Field | Type | Null? | Unit / time base | Meaning |
|---|---|---|---|---|
| `count` | integer | no | rows | Series returned. |
| `series[].ticker` | string | no | — | Series ticker. |
| `series[].frequency` | string | yes | — | How often the series produces events, e.g. `hourly`. |
| `series[].title` | string | yes | — | Human-readable name. |
| `series[].category` | string | yes | — | Kalshi's category. |
| `series[].tags` | array of string | yes | — | Kalshi's tags. |
| `series[].settlement_sources` | array of object | yes | — | `{url, name}` of each settlement source. |
| `series[].fee_type` | string | yes | — | Kalshi's fee model, e.g. `quadratic`. |
| `series[].fee_multiplier` | string | yes | decimal string | Fee multiplier. |
| `series[].contract_url` | string | yes | — | Product certification document. |
| `series[].contract_terms_url` | string | yes | — | Contract terms document. |
| `series[].product_metadata` | object | yes | — | Kalshi's free-form metadata. |
| `series[].last_updated_ts` | string | yes | UTC | When Kalshi last changed the record. |
| `series[].first_seen_at` | string | yes | UTC | When this system first stored it. |

```sh
curl 'http://127.0.0.1:8137/api/v1/kalshi/series?search=KXBTCD'
```

```json
{
  "count": 3,
  "series": [
    {"ticker":"KXBTCD","frequency":"hourly","title":"Bitcoin price Above/below","category":"Crypto","tags":["Hourly","BTC"],"settlement_sources":[{"url":"https://www.cfbenchmarks.com/data/indices/BRTI?ref=blog.cfbenchmarks.com","name":"CF Benchmarks"}],"fee_type":"quadratic","fee_multiplier":"1"}
  ]
}
```

*Captured 2026-09-16. Truncated: 3 series returned, 1 shown with its later
fields elided.*

**Errors**: `422` if the unfiltered catalog would exceed the row ceiling —
14,092 series exist, so an unbounded call is close to the limit; narrow the
filter (§2.7). `504` on statement timeout.

### 4.3 `GET /api/v1/kalshi/series/{ticker}`

<!-- endpoint: /api/v1/kalshi/series/{ticker}
     params: -
     errors: 422, 504 -->

One series. Fields are those of §4.2.

```sh
curl 'http://127.0.0.1:8137/api/v1/kalshi/series/KXBTCD'
```

*Captured 2026-09-16. Returns the single series object shown in §4.2.*

**Errors**: `404` if the series ticker is unknown (see §2.7 — the `404` is real
but undeclared in the schema); `504` on statement timeout.

### 4.4 `GET /api/v1/kalshi/series/{ticker}/events`

<!-- endpoint: /api/v1/kalshi/series/{ticker}/events
     params: strike_from:date-time, strike_to:date-time
     errors: 422, 504 -->

That series' events, optionally bounded by strike date.

| Name | Type | Required | Meaning |
|---|---|---|---|
| `strike_from` | date-time | no | Inclusive lower bound on `strike_date`, UTC. |
| `strike_to` | date-time | no | Inclusive upper bound on `strike_date`, UTC. |

Both bounds are inclusive (§2.2). Unlike the equity routes these accept a full
timestamp, not only a date.

**Response**

| Field | Type | Null? | Unit / time base | Meaning |
|---|---|---|---|---|
| `series_ticker` | string | no | — | Echoed back. |
| `count` | integer | no | rows | Events returned. |
| `events[].event_ticker` | string | no | — | Event ticker. |
| `events[].series_ticker` | string | no | — | Parent series. |
| `events[].title` | string | yes | — | Human-readable question. |
| `events[].sub_title` | string | yes | — | Qualifier, usually the settlement time. |
| `events[].category` | string | yes | — | Kalshi's category. |
| `events[].mutually_exclusive` | boolean | yes | — | Whether at most one market can resolve yes. |
| `events[].strike_date` | string | yes | UTC | When the event's outcome is determined. |
| `events[].strike_period` | string | yes | — | Kalshi's period label; may be empty. |
| `events[].collateral_return_type` | string | yes | — | Kalshi's collateral model. |
| `events[].available_on_brokers` | boolean | yes | — | Kalshi's broker availability flag. |
| `events[].settlement_sources` | array of object | yes | — | `{url, name}` per source. |
| `events[].product_metadata` | object | yes | — | Kalshi's free-form metadata. |
| `events[].last_updated_ts` | string | yes | UTC | When Kalshi last changed the record. |

```sh
curl 'http://127.0.0.1:8137/api/v1/kalshi/series/KXBTCD/events?strike_from=2026-09-10T00:00:00Z&strike_to=2026-09-11T00:00:00Z'
```

```json
{
  "series_ticker": "KXBTCD",
  "count": 21,
  "events": [
    {"event_ticker":"KXBTCD-26SEP0920","series_ticker":"KXBTCD","title":"BTC price on Sep 9, 2026 at 8pm EDT?","sub_title":"On Sep 9, 2026 at 8pm EDT","category":"Crypto","mutually_exclusive":false,"strike_date":"2026-09-10T00:00:00Z","strike_period":"","collateral_return_type":"DIRECNET","available_on_brokers":false}
  ]
}
```

*Captured 2026-09-16. Truncated: 21 events returned, 1 shown with its later
fields elided.*

**Errors**: `422` over the row ceiling or on a reversed strike range; `504` on
statement timeout.

### 4.5 `GET /api/v1/kalshi/events/{event_ticker}`

<!-- endpoint: /api/v1/kalshi/events/{event_ticker}
     params: -
     errors: 422, 504 -->

One event. Fields are those of §4.4.

```sh
curl 'http://127.0.0.1:8137/api/v1/kalshi/events/KXBTCD-26SEP0920'
```

*Captured 2026-09-16. Returns the single event object shown in §4.4.*

**Errors**: `404` if the event ticker is unknown (undeclared, see §2.7); `504`
on statement timeout.

### 4.6 `GET /api/v1/kalshi/events/{event_ticker}/markets`

<!-- endpoint: /api/v1/kalshi/events/{event_ticker}/markets
     params: status:string
     errors: 422, 504 -->

That event's markets, optionally filtered by status.

| Name | Type | Required | Meaning |
|---|---|---|---|
| `status` | string | no | Comma-separated list of market status tokens. |

Valid tokens are the `MarketStatus` set listed at the top of §4. An invalid
token is a hand-written `422` that names both what you sent and what is
accepted:

```sh
curl 'http://127.0.0.1:8137/api/v1/kalshi/events/KXBTCD-26SEP0920/markets?status=settled'
```

```json
{"error":"Invalid status values: settled. Valid: active, amended, closed, determined, finalized, inactive, initialized"}
```

*Captured 2026-09-16, HTTP 422. Note the shape is `{"error": …}`, not the
declared `{"detail": [...]}` — §2.7.*

Market fields are those of §4.7.

**Errors**: `422` for an invalid status token or over the row ceiling; `504` on
statement timeout.

### 4.7 `GET /api/v1/kalshi/markets/{ticker}`

<!-- endpoint: /api/v1/kalshi/markets/{ticker}
     params: -
     errors: 422, 504 -->

One market, with its lifecycle, settlement and economics fields grouped. The
grouping is for readability; the field names inside each group are Kalshi's
verbatim.

**Top-level**

| Field | Type | Null? | Unit / time base | Meaning |
|---|---|---|---|---|
| `ticker` | string | no | — | Market ticker. |
| `event_ticker` | string | no | — | Parent event. |
| `market_type` | string | yes | — | e.g. `binary`. |
| `status` | string | no | — | A `MarketStatus` token (top of §4). |
| `title` / `subtitle` | string | yes | — | Human-readable question and qualifier. |
| `yes_sub_title` / `no_sub_title` | string | yes | — | Side labels. |
| `rules_primary` / `rules_secondary` | string | yes | — | Kalshi's settlement rules, verbatim. |
| `strike_type` | string | yes | — | Kalshi's strike comparison, e.g. `greater`. |
| `price_level_structure` | string | yes | — | Kalshi's tick structure. |
| `is_provisional` | boolean | yes | — | Kalshi's provisional flag. |
| `mve_collection_ticker` | string | yes | — | Kalshi's multivariate collection, when any. |
| `first_seen_at` | string | yes | UTC | When this system first stored it. |
| `last_synced_at` | string | yes | UTC | When this system last refreshed it. |

**`lifecycle`** — all UTC timestamps, all nullable: `created_time`,
`open_time`, `close_time`, `expiration_time`, `expected_expiration_time`,
`latest_expiration_time`, `updated_time`.

**`settlement`** — how the market resolved. This object is present on **every**
market, with all five fields `null` until it settles: an absent object and an
unsettled market would otherwise be indistinguishable from a client's point of
view
([`models/kalshi_catalog.py:147-153`](../../src/manta_trading/api_server/models/kalshi_catalog.py)).

| Field | Type | Null? | Unit | Meaning |
|---|---|---|---|---|
| `result` | string | yes | — | `yes`, `no`, or null while unsettled. |
| `expiration_value` | string | yes | decimal string | The measured value that decided it. |
| `can_close_early` | boolean | yes | — | Whether early close was permitted. |
| `settlement_ts` | string | yes | UTC | When it settled. |
| `settlement_value_dollars` | string | yes | decimal string | Payout per contract. |

**`economics`** — every field a decimal string (§2.3):
`notional_value_dollars`, `last_price_dollars`, `previous_price_dollars`,
`yes_bid_dollars`, `yes_ask_dollars`, `no_bid_dollars`, `no_ask_dollars`,
`liquidity_dollars`, `volume_fp`, `volume_24h_fp`, `open_interest_fp`.

```sh
curl 'http://127.0.0.1:8137/api/v1/kalshi/markets/KXBTCD-26SEP0920-T68599.99'
```

```json
{
  "ticker": "KXBTCD-26SEP0920-T68599.99",
  "event_ticker": "KXBTCD-26SEP0920",
  "market_type": "binary",
  "status": "finalized",
  "subtitle": "$68,600 or above",
  "strike_type": "greater",
  "lifecycle": {"created_time":"2026-09-08T09:01:18.308632Z","open_time":"2026-09-09T23:00:00Z","close_time":"2026-09-10T00:00:00Z","expiration_time":"2026-09-17T00:00:00Z"},
  "settlement": {"result":"yes","expiration_value":"78276.74","can_close_early":true,"settlement_ts":"2026-09-10T00:02:25.563430Z","settlement_value_dollars":"1.0000"},
  "economics": {"notional_value_dollars":"1.0000","last_price_dollars":"0.0000","yes_bid_dollars":"0.0000","yes_ask_dollars":"1.0000","open_interest_fp":"0.00"}
}
```

*Captured 2026-09-16, truncated. A **settled** market is used deliberately: it
is terminal, so re-running this example returns the same shape rather than a
`404` as a live market ages out.*

**Errors**: `404` if the market ticker is unknown (undeclared, see §2.7); `504`
on statement timeout.

### 4.8 `GET /api/v1/kalshi/search`

<!-- endpoint: /api/v1/kalshi/search
     params: q:string, limit:integer
     errors: 422, 504 -->

Plain-language search over series, events and open markets: "fed rate",
"bitcoin", "CPI".

**Use this to find a ticker from words.** Each group is ranked, best first,
and every hit names its parents, so a client can go straight to §4.4 or §4.7.
Matching is stemmed ("rates" finds "rate"), needs every word, tolerates a
misspelling ("bitcon"), and finds a series by the start of its ticker. It reads
titles, subtitles and side labels; settlement rules are not searched. Only
**open** markets are searched (status `active`); a closed or settled market is
reached through its event (§4.6) instead. Series and events are searched in
full.

**It does not understand synonyms.** "rate cut" will not find "funds rate", and
"100k" will not find "$100,000". Search the words the title uses.

| Name | Type | Required | Meaning |
|---|---|---|---|
| `q` | string | yes | The words to find. Not empty or blank. |
| `limit` | integer | no | Most hits per group; 1 to 50, default 10. |

The ceiling on `limit` is 50.
<!-- from: manta_trading.data.kalshi.constants.SEARCH_MAX_LIMIT -->

This is a top-N, not a listing: there is no row ceiling and no `422` for a broad
query. Narrow the words, or follow a hit down the hierarchy, to see more.

**Response**

| Field | Type | Null? | Meaning |
|---|---|---|---|
| `query` | string | no | The `q` that was searched. |
| `series[].ticker` / `title` / `category` | string | `title`, `category` yes | A matching series. |
| `events[].event_ticker` / `series_ticker` | string | no | A matching event and its series. |
| `events[].title` / `sub_title` | string | yes | Event text. |
| `events[].strike_date` | string | yes | UTC. |
| `markets[].ticker` / `event_ticker` / `series_ticker` | string | no | A matching open market and its parents. |
| `markets[].title` / `yes_sub_title` / `no_sub_title` | string | yes | Market text. |
| `markets[].close_time` | string | no | UTC. |

Each group is an empty list, never absent, when nothing at that level matched.
Among equally good text matches, events rank the nearest upcoming strike date
first and markets the soonest close.

```sh
curl 'http://127.0.0.1:8137/api/v1/kalshi/search?q=bitcoin&limit=2'
```

**Errors**: `422` for a missing or blank `q` or a `limit` outside 1 to 50; `504`
on statement timeout.

## 5. Endpoints: Kalshi time series and operations

### 5.1 `GET /api/v1/kalshi/markets/{ticker}/candlesticks`

<!-- endpoint: /api/v1/kalshi/markets/{ticker}/candlesticks
     params: period_minutes:CandlePeriod, start:date|date-time, end:date|date-time, format:json|msgpack
     errors: 422, 504 -->

Candlesticks for one market over a window.

| Name | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `period_minutes` | integer | no | `1` | Candle period: `1`, `60` or `1440`; anything else is a `422`. Echoed in the response. |
| `start` | date or date-time | no | — | Window start, UTC, inclusive. |
| `end` | date or date-time | no | — | Window end, UTC, **exclusive**. A bare date means the end of that day (`end=D` reads through the last candle of day D). |
| `format` | `json` \| `msgpack` | no | `json` | Response encoding (§7). |

**The window is half-open and holds the candles that open inside it.** A
candle covers `[end_period_ts - period, end_period_ts)`; it is returned when its
open is in `[start, end)`. `start=D&end=D` is the whole UTC day D, and the
23:59–00:00 candle (ending D+1 00:00) is in it. This differs from §2.2; the trades
route (§5.2) uses the same `[start, end)` window over `created_time`.

**Hourly and daily candles are derived on read** from the stored minute
candles, grouped by UTC-aligned period (hours on the hour, days at 00:00 UTC);
`end_period_ts` is the period end and the bar starts at `end - period`.

- `open` / `previous`: the first minute's. `close` / `open_interest_fp`: the last
  minute's. `high` / `low`: max / min. `volume_fp`: the sum.
- Traded `price` open/close skip minutes with no trade. `mean_dollars` is the
  volume-weighted mean of the minute means (exact: Kalshi's minute mean is the
  volume-weighted price).
- Same sparseness as 1m: a period with no stored minute has no row; a period
  with book movement but no trades has null traded OHLC with `previous`, bid and
  ask filled.
- The 75,000-row ceiling counts the candles returned, not the minutes read.
- The newest hour or day is served from the minutes stored so far, with no
  partial flag. `complete_through` stays the minute-collection instant, never
  rounded: **any period ending after it is partial.**

**Response**

The four described fields below carry the descriptions published in the schema.

| Field | Type | Null? | Unit / time base | Meaning |
|---|---|---|---|---|
| `market_ticker` | string | no | — | Echoed back. |
| `period_minutes` | integer | no | minutes | Candle period: the request's `period_minutes`, echoed back. |
| `collected` | boolean | no | — | Whether this market is in the candle collection set. **`false` means no candles are stored for it — not that the window was empty.** |
| `coverage_from` | string | yes | UTC | Oldest period this market's candles have been collected from. Null when the market is not collected. |
| `complete_through` | string | yes | UTC | Requested and stored through this instant — **not "the newest stored candle"**. A quiet market produces no candle for a period it was nonetheless asked for, so the two differ. |
| `count` | integer | no | rows | Candles returned. |
| `candlesticks[].end_period_ts` | string | no | UTC | Closing instant of the period. |
| `candlesticks[].yes_bid` | object | no | decimal strings | `{open,high,low,close}_dollars` for the yes bid. |
| `candlesticks[].yes_ask` | object | no | decimal strings | Same four fields for the yes ask. |
| `candlesticks[].price` | object | no | decimal strings | `{open,high,low,close,previous,mean}_dollars`; all but `previous` are null in a period with no trade. |
| `candlesticks[].volume_fp` | string | no | decimal string | Contracts traded in the period. |
| `candlesticks[].open_interest_fp` | string | no | decimal string | Open interest at period close. |

```sh
curl 'http://127.0.0.1:8137/api/v1/kalshi/markets/KXGREENLAND-29-26MAY/candlesticks?start=2026-05-01T05:00:00Z&end=2026-05-01T14:05:00Z'
```

```json
{
  "market_ticker": "KXGREENLAND-29-26MAY",
  "period_minutes": 1,
  "collected": true,
  "coverage_from": "2026-01-10T00:00:00Z",
  "complete_through": "2026-05-01T14:01:00Z",
  "count": 3,
  "candlesticks": [
    {"end_period_ts":"2026-05-01T05:26:00Z",
     "yes_bid":{"open_dollars":"0.0000","high_dollars":"0.0000","low_dollars":"0.0000","close_dollars":"0.0000"},
     "yes_ask":{"open_dollars":"0.0010","high_dollars":"0.0010","low_dollars":"0.0010","close_dollars":"0.0010"},
     "price":{"open_dollars":null,"high_dollars":null,"low_dollars":null,"close_dollars":null,"previous_dollars":"0.0010","mean_dollars":null},
     "volume_fp":"0.00","open_interest_fp":"661247.92"}
  ]
}
```

*Captured 2026-09-16. Truncated: 3 candles returned, 1 shown. Note the null
`price` fields — the period had a book but no trade — beside a non-null
`previous_dollars`.*

An uncollected market returns `collected: false` with everything null, which is
a different fact from an empty window (§2.4):

```json
{"market_ticker":"KXBTCD-26SEP0920-T68599.99","period_minutes":1,"collected":false,"coverage_from":null,"complete_through":null,"count":0,"candlesticks":[]}
```

*Captured 2026-09-16.*

**Errors**: `422` over the row ceiling (exact `count(*)`, §2.5) or on a
reversed range; `504` on statement timeout.

### 5.2 `GET /api/v1/kalshi/markets/{ticker}/trades`

<!-- endpoint: /api/v1/kalshi/markets/{ticker}/trades
     params: start:date|date-time, end:date|date-time, format:json|msgpack
     errors: 422, 504 -->

The trade tape for one market over a window. Parameters are those of §5.1
(without `period_minutes`). A trade is returned when `created_time` is in
`[start, end)`; a bare `end` date means through the end of that day.

**Response**

The three described fields carry the descriptions published in the schema.

| Field | Type | Null? | Unit / time base | Meaning |
|---|---|---|---|---|
| `market_ticker` | string | no | — | Echoed back. |
| `coverage_from` | string | yes | UTC | Oldest instant the trades tape reaches, across the live and historical surfaces. Null when the trades phase has never run. |
| `tape_complete_through` | string | yes | UTC | Created time of the newest stored trade. **Null means the trades phase has never run — not that the tape is empty today.** |
| `tape_filtered` | boolean | no | — | Whether this market's category is excluded from trade collection. **`true` means no trades are stored for it by policy**, so an empty window is expected rather than a gap. |
| `count` | integer | no | rows | Trades returned. |
| `trades[].created_time` | string | no | UTC | When the trade executed. |
| `trades[].trade_id` | string | no | UUID text | Kalshi's trade identifier. Stored as a UUID; the wire carries its text form. |
| `trades[].count_fp` | string | no | decimal string | Contracts traded. |
| `trades[].yes_price_dollars` | string | no | decimal string | Price paid for the yes side. |
| `trades[].no_price_dollars` | string | no | decimal string | Price paid for the no side. |
| `trades[].taker_outcome_side` | string | yes | — | `yes` or `no`: which side the taker bought. |
| `trades[].taker_book_side` | string | yes | — | `bid` or `ask`: which side of the book was hit. |
| `trades[].is_block_trade` | boolean | no | — | Whether Kalshi marked it a block trade. |

```sh
curl 'http://127.0.0.1:8137/api/v1/kalshi/markets/KXPRESNOMD-28-ZMAM/trades?start=2026-09-15T00:00:00Z&end=2026-09-15T00:10:00Z'
```

```json
{
  "market_ticker": "KXPRESNOMD-28-ZMAM",
  "coverage_from": "2026-01-01T00:00:00Z",
  "tape_complete_through": "2026-09-16T14:19:01.149477Z",
  "tape_filtered": false,
  "count": 300,
  "trades": [
    {"created_time":"2026-09-15T00:00:01.953793Z","trade_id":"0278e9b1-f521-ab17-c502-623049e92e35","count_fp":"1.00","yes_price_dollars":"0.0010","no_price_dollars":"0.9990","taker_outcome_side":"no","taker_book_side":"ask","is_block_trade":false}
  ]
}
```

*Captured 2026-09-16. Truncated: 300 trades returned, 1 shown.*

**Errors**: `422` over the row ceiling or on a reversed range; `504` on
statement timeout.

### 5.3 `GET /api/v1/overview`

<!-- endpoint: /api/v1/overview
     params: -
     errors: 504 -->

Operational state: what each acquisition pass is doing, the newest row in each
source table, the health verdict, and the universe accounting line. No
parameters. **This is a pure database read and safe to poll.**

Pass kinds are `accounting` `daily` `health` `kalshi` `minute` `tick`.
<!-- from: manta_trading.data.acquisition.pass_runs.PassKind -->

Run outcomes are `COMPLETE` `COMPLETE_QUOTA` `FAILED` `INCOMPLETE`
`PROVIDER_UNAVAILABLE`.
<!-- from: manta_trading.data.acquisition.pass_runs.PassRunOutcome -->

**The `pass` field is serialized by alias.** `pass` is a Python keyword, so the
model declares the attribute `pass_` and publishes it under the alias `pass`
([`models/operations.py:81,87`](../../src/manta_trading/api_server/models/operations.py)).
The wire name and the schema both say `pass`. A Python consumer that generates
a client from the schema cannot use it as an attribute name and must read it
with `getattr(obj, "pass_")` or by key.

| Field | Type | Null? | Unit / time base | Meaning |
|---|---|---|---|---|
| `now` | string | no | UTC | Server time when the snapshot was taken. |
| `passes[].pass` | string | no | — | A `PassKind` token. Aliased — see above. |
| `passes[].cadence` | string | no | — | Human-readable schedule, e.g. `hourly :20`. |
| `passes[].running` | array of object | no | — | Still-open runs; empty when idle. |
| `passes[].running[].phase` | string | yes | — | Which phase the run is in. |
| `passes[].last_run` | object | yes | — | The last completed run; null if never run. |
| `passes[].last_run.started_at` | string | no | UTC | When it began. |
| `passes[].last_run.ended_at` | string | yes | UTC | When it finished. |
| `passes[].last_run.outcome` | string | no | — | A `PassRunOutcome` token. |
| `passes[].last_run.exit_code` | integer | yes | — | Process exit code. |
| `passes[].last_run.detail` | string | yes | — | Free-text summary of what it did. |
| `passes[].next_firing` | string | yes | UTC | Next scheduled run. Null for a pass with no timer (`tick`, whose cadence reads `manual (no timer)`). |
| `sources[].name` | string | no | — | Source table label, e.g. `minute bars`. |
| `sources[].newest` | string | yes | UTC | Newest row in that table. |
| `sources[].unavailable` | string | yes | — | Why the source could not be read; `newest` is then null. |
| `health.verdict` | string | no | — | Whole-system health verdict. |
| `health.at` | string | no | UTC | When the verdict was computed. |
| `universe.summary` | string | no | — | Universe accounting line. |
| `universe.at` | string | no | UTC | When it was computed. |

**`running` rows carry no `abandoned` field, deliberately.** The CLI screen
(`mt data overview`) marks a running row abandoned by testing its recorded pid
against the *local* process table. That is sound for a CLI run beside the pass
and meaningless over HTTP, where the pid may have been recorded on another
host — every row would be structurally `false`, and publishing a field that is
always false is worse than publishing none
([`models/operations.py:37-48`](../../src/manta_trading/api_server/models/operations.py)).
Use `mt data overview` on the host that owns the run.

**Credits are not here either**, for a different reason: see §5.4.

**The last source is `futures ticks`**, the newest stored tick, read from the
tick database's ledger. When that database is not configured or down,
`newest` is null and `unavailable` carries the reason
(`not configured (MT_TICK_DB_URL unset)`, `unreachable: …`); the route still
answers `200`. `unavailable` is null for every other source.

```sh
curl 'http://127.0.0.1:8137/api/v1/overview'
```

```json
{
  "now": "2026-09-16T15:02:16.633499Z",
  "passes": [
    {"pass":"minute","cadence":"13:05","running":[],
     "last_run":{"started_at":"2026-09-16T13:05:13.494622Z","ended_at":"2026-09-16T14:32:33.033501Z","outcome":"COMPLETE_QUOTA","exit_code":0,"detail":"trailing skipped (not a firing day) · backfill 3592 symbols"},
     "next_firing":"2026-09-17T13:05:00Z"}
  ],
  "sources": [
    {"name":"minute bars","newest":"2026-09-12T00:00:00Z"},
    {"name":"kalshi trades","newest":"2026-09-16T14:19:00.903841Z"}
  ],
  "health": {"verdict":"healthy","at":"2026-09-16T14:51:04.185908Z"},
  "universe": {"summary":"minute universe: 11,612,382/13,661,872 symbol-sessions covered (85.0%); 439,726 untraded; 1,609,764 fillable (hole 1,087,470, unknown 291,415, untracked 230,844, exhausted 35)","at":"2026-09-15T16:31:32.848107Z"}
}
```

*Captured 2026-09-16, before `unavailable` and the `futures ticks` source
existed. Truncated: five passes and four sources returned, one and two shown.*
Since 2026-10-13 each source also carries `"unavailable":null`, and a fifth
follows: `{"name":"futures ticks","newest":"2024-12-31T21:59:59.257498Z","unavailable":null}`.

**Errors**: `504` on statement timeout.

### 5.4 `GET /api/v1/credits`

<!-- endpoint: /api/v1/credits
     params: -
     errors: - -->

The day's EODHD credit position. No parameters.

**This route always returns `200`.** It declares no other status, and that is
deliberate: an unset API key or an unreachable provider arrives as `error` text
with `credits: null`, because a provider that will not answer is a condition
this endpoint *reports* rather than a fault of this server. **There is no
`504`** — it issues no statement about provider reachability beyond the `error`
field.

| Field | Type | Null? | Unit | Meaning |
|---|---|---|---|---|
| `credits` | object | yes | — | Null when the position could not be read. |
| `credits.used` | integer | no | credits | Consumed today. |
| `credits.daily_limit` | integer | no | credits | The plan's daily allowance. |
| `credits.extra` | integer | no | credits | Additional purchased credits. |
| `credits.remaining` | integer | no | credits | Allowance left. |
| `error` | string | yes | — | Why the position is unavailable; null on success. |

```sh
curl 'http://127.0.0.1:8137/api/v1/credits'
```

```json
{"credits":{"used":99998,"daily_limit":100000,"extra":0,"remaining":2},"error":null}
```

*Captured 2026-09-16.*

**Unlike `/api/v1/overview`, this route makes an outbound HTTPS call**, so it
is slower and can fail in ways a database read cannot. That separation is what
keeps `/api/v1/overview` pure database and safe to poll.

**Errors**: none. Failures are reported in the body.

## 6. Endpoints: futures

Futures tick data: the configured products, their contract definitions and roll
schedules, ticks, time bars and tick bars, by contract or by product under a
roll rule. Time bars are the provider's one-minute bars, rolled up per session;
ticks never make time bars. Tick bars (bars of N trades, §6.9) are cut from the
stored ticks. Served from the tick database (`MT_TICK_DB_URL`), not the equity
store. When that database is not configured, unreachable, or missing a
migration, every route here answers `503` (§6.1) and the rest of the API is
unaffected.

### 6.1 Futures conventions

These differ from §2 on purpose. Read them before calling any route below.

**Windows are session dates, and `end` is exclusive.** `start=2024-09-16&end=2024-09-21`
reads the sessions dated 09-16 through 09-20. A CME session opens the evening
before its date (the session dated 09-18 runs 22:00 UTC 09-17 → 21:00 UTC
09-18), so a UTC-day window would cut every session in two. This is the
opposite of §2.2's inclusive `end`; it is the tick tier's convention, shared
with `mt data tick get`. An omitted `start` or `end` defaults to the first held
session, or the day after the last one. `end` at or before `start` is a `422`.

**Ticks and tick bars also take `from` and `until`**, UTC instants (naive input
is UTC, as in §2.1), half-open: `[from, until)`. For ticks they clip each
session to the instants between them, which is how a client reads a session
larger than the row ceiling. To page, pass the previous request's `until` as the
next `from`; pages neither overlap nor skip. For tick bars they select the bars
whose `open_time` is in `[from, until)`; a bar's content and index never depend
on them (below). `from` must be before `until` (`422` otherwise). Time bars take
no instant bounds: a bar starts at its session-anchored bucket, so clipping
would move the first bar's stamp. Bound a bars request by sessions.

**Time bars come from the provider's one-minute bars, and from nothing else.** Every
size is built from Databento `ohlcv-1m` bars: `1m` as stored, `5m` `15m` `1h`
`4h` and `1d` rolled up from them (open of the first, high, low, close of the
last, summed volume). Ticks are never read for bars, even where a session has
ticks and no provider bars. Every bars response names this in `source`
(`"ohlcv-1m"`), so a backtest can tell which tier built what it received.

- **Buckets are anchored at the session open.** `1m`, `5m`, `15m` and `1h` fall
  on the same instants as a UTC grid, because the CME open is on the hour. `4h`
  starts at the open and every four hours after it: 17:00, 21:00, 01:00, 05:00,
  09:00 and 13:00 Chicago time, whichever side of daylight saving the session
  is on. The last `4h` bar is short: it runs from 13:00 to the close, and an
  early close cuts it there. `1d` is the whole session, stamped with its open.
  Empty buckets are omitted, and a bar never crosses a session.
- **No provider bars, no bars.** A session with ticks and no provider bars
  returns no rows and is listed in `incomplete` with its provider-bar status
  (`missing`, `awaiting_ingest`, …). It is never filled from ticks.
- **`incomplete` carries the load verdict only.** A session whose provider bars
  are loaded completely but fail the cross-tier volume check (traded against
  CME's cleared volume) is served and not listed. That finding belongs to
  `mt data tick coverage`.
- **A one-minute stub bar closes a regular session at `5m`, `15m` and `1h`.** A
  provider bar can be stamped exactly at the session close, and the close is
  inside the session. When the session length is a multiple of the bar width (a
  regular 23-hour session), that bar lands alone in a bucket stamped at the
  close. It is a real bar, not a data fault. Whether the closed-interval rule is
  right is issue #70, measured by `mt data tick parity`.
- **Statistics are not served** (open interest, settlement, cleared volume).
  Operators read them with `mt data tick coverage --schema statistics`.

**Tick bars are bars of `ticks_per_bar` trades**, any positive integer, cut by
one rule (the same code the live stream will run):

- **One trade record is one tick.** Records sharing `ts_event` and `sequence`
  (about 2% of ES) are separate trades, told apart by `sequence_ordinal`.
- **Each session counts from its open.** `index` is the bar's 0-based position
  in its session and restarts at 0 at every session open, and at a roll, which
  falls on a session boundary. `(session_date, index)` is unique and strictly
  ascending across a response.
- `open_time` and `close_time` are the `ts_event` of the bar's first and last
  trade; two bars can share a nanosecond, and `index` still orders them.
- `open`, `high`, `low`, `close` are the first, highest, lowest and last trade
  price in key order (`ts_event`, `sequence`, `sequence_ordinal`).
- `volume` is the summed size; `trade_count` the trades in the bar (equal to
  `ticks_per_bar` unless `partial`).
- `buy_volume` and `sell_volume` sum the trades whose aggressor side is `B` and
  `A`. A trade with side `N` counts in `volume` only, so `buy_volume +
  sell_volume` can be below `volume`.
- `bid_px` and `ask_px` are the quote at the bar's last trade; null when that
  trade's record is trades-tier.
- **`partial` is true only for a session's last bar holding fewer than
  `ticks_per_bar` trades.** A session whose trade count is a multiple of
  `ticks_per_bar` ends with a full bar and has no partial one. A
  `ticks_per_bar` above a session's trade count gives that session one partial
  bar.
- **Bars do not depend on `from`/`until`.** Cutting always starts at the session
  open, and a bar opened before `until` is completed with the trades after it.
- **An incomplete session is cut from what is held** and listed in
  `incomplete`, as every futures read does. Its bars, and their indices, can
  change when the session is completed.

**Tick bars are admitted on the trades they scan, then bounded by the bars they
return.** Before any tick is read, the server sums the ingest ledger's record
count over every session the request overlaps (whole sessions: cutting starts at
the open) and refuses with a `422` when the sum is over the trade limit:
`the request scans 2,431,907 trades, over the 2,000,000 trade limit; narrow
start/end`.
<!-- from: manta_trading.constants.API_MAX_TICK_BAR_TRADES -->
The limit is configurable as `MT_API_MAX_TICK_BAR_TRADES`, beside
`MT_API_MAX_BARS_PER_REQUEST`. The bars returned then count against the row
ceiling (below), with the remedy `raise ticks_per_bar, or narrow start/end or
from/until`. A median ES session (about 393,000 trades) fits at `ticks_per_bar`
of 6 or more.

**Ticks are columnar.** A `/ticks` body holds one block per session window
(session × contract), in order, with the session, symbol and instrument once
per block and every other field a column of `count` values:

- `ts_base` is the block's first `ts_event`, epoch nanoseconds as **text**.
  `ts_event[i]` is the delta in nanoseconds from tick `i − 1`, and
  `ts_event[0]` is 0, so tick `i`'s time is `ts_base` plus the sum of
  `ts_event[0..i]`. Deltas are never negative. Add them as 64-bit integers (a
  JavaScript `BigInt`), never as floats.
- Prices (`price`, `bid_px`, `ask_px`) are integers in DBN fixed point: the
  price is the integer × 10^−`price_scale` (`price_scale` is 9). An undefined
  price is `null`. ES and GC prices are below 2^53, so they decode exactly as
  JSON numbers.
- `side` is one string, one character per tick (`B`, `A`, `N`).
- `sequence` and `sequence_ordinal` with `ts_event` are the stream key: unique
  per instrument, and the order ticks come in.
- `bid_px`, `ask_px`, `bid_sz`, `ask_sz` hold `null` for a trades-tier tick.
  They are never left out, so a block always has one shape. Order counts
  (`bid_ct`, `ask_ct`) are not served.

Decoding one block:

```json
{"session_date":"2024-12-03","raw_symbol":"ESZ4","instrument_id":183748,"count":3,
 "ts_base":"1733180400000000000","ts_event":[0,1250,0],
 "sequence":[7,8,8],"sequence_ordinal":[0,0,1],
 "price":[6050250000000,6050500000000,6050500000000],"size":[1,2,1],"side":"BAN",
 "bid_px":[6050000000000,6050250000000,null],"ask_px":[6050250000000,6050500000000,null],
 "bid_sz":[17,9,null],"ask_sz":[18,4,null]}
```

```python
ts = int(block["ts_base"])
for i in range(block["count"]):
    ts += block["ts_event"][i]          # 1733180400000000000, …001250, …001250
    price = Decimal(block["price"][i]).scaleb(-body["price_scale"])  # 6050.25, …
    side = block["side"][i]
```

*Shape only: values chosen to show the decoding, not captured.*

**Roll rules** name the contract a product read uses for each session:
`{method}.{rank}[+{offset}s]`, case-insensitive. The default is `v.0`, the
front contract by volume.
Roll methods are `v` `c` `n`.
<!-- from: manta_trading.data.tick.constants.RollMethod -->
`v` ranks contracts by the volume of an earlier session, so it never looks
ahead; `n` ranks them by the open interest of an earlier session, equally
never looking ahead; `c` takes the nearest expiration, and `c.0+2s` rolls two
sessions before it. An offset is allowed only on `c`. A bad rule is a `422`
naming what is wrong.

**What a time-series response says about itself.** Every array is present,
possibly empty:

- `segments`: runs of consecutive sessions served by one contract, in order.
  A product read lists the roll here; a contract read lists one run. Each
  segment's `basis` names the tier its roll was decided from (`tick_tier`,
  `bar_1m`, or `statistics` for `n`), so a run never splices two tiers; it is
  null for a contract read and for `c`, which no ranking decides.
- `unresolved`: sessions the rule could not answer, each with a `reason`.
  They have **no rows**, never rows from a guessed contract. The volume rule
  cannot answer the first sessions held, because it ranks by an earlier
  session that is not held. Always empty for a contract read.
- `incomplete`: served sessions whose data is not complete, each with its
  `status`. Their rows are returned; they may be partial.

Both arrays come with HTTP `200`: the rows returned are correct, and the
arrays say what is missing. `mt data tick get` exits 3 in the same situation.
Session statuses are `retry_exhausted` `failed` `provider_hole` `in_flight`
`awaiting_ingest` `missing` `pending` `edge_unknown` `complete`.
<!-- from: manta_trading.data.tick.tick_status.TickSessionStatus -->

**Prices and times are strings, except in the columnar ticks.** Every bar and
tick-bar price is exact decimal text (`"5950.25"`), `null` when undefined. Every
bar or tick-bar time is RFC 3339 with nine fraction digits
(`"2024-12-18T14:30:00.001440845Z"`). Epoch nanoseconds exceed 2^53 and a
JavaScript number would round them, so none is sent as a number: the columnar
ticks send `ts_base` as text and small deltas, and their prices as integers
below 2^53 (above).

**The row ceiling is enforced while reading.** A futures request that would
return more than 75,000 rows is refused with a `422`.
<!-- from: manta_trading.constants.API_MAX_BARS_PER_REQUEST -->
The read stops at the ceiling, so the refusal quotes the limit but no count:
`the request matches more than the 75,000 row limit; narrow start/end or
from/until` (bars say `narrow start/end`). A single ES session holds up to
about 690,000 ticks, so ticks over a whole busy session are always refused;
use `from`/`until`.

**Product reads span at most 1,300 sessions** of roll schedule; a longer range
is a `422` naming the cap.
<!-- from: manta_trading.data.tick.constants.TICK_ROLL_MAX_SESSIONS -->

**`503` means the tick tier is down, not the server.** The body is the
standard error shape, and the message says which:

| Message starts | Meaning | What to do |
|---|---|---|
| `not configured (MT_TICK_DB_URL unset)` | The server has no tick database. | Nothing to retry; an operator sets it. |
| `unreachable: …` | The tick database, or the calendar the roll path reads, did not answer within the timeout. | Retry later. |
| `migrations pending (mt data migrate apply --track tick)` | The tick database is behind the code. | An operator runs the command. |

`/api/v1/health`'s `tick_db` field reports the same state without a request
failing (§3.1).

**`404`** is an unknown product (`Product 'ZZ' not found`) or a contract with
no definition (`no definition held for contract ZZZ9`). Like every `404` in
this API it is not declared in `openapi.json` (§9).

### 6.2 `GET /api/v1/futures/products`

<!-- endpoint: /api/v1/futures/products
     params: -
     errors: 503, 504 -->

Every configured product, its display name, its trading calendar, and the contract each status
rule names for the latest complete session held, with the next roll when it is
known. No parameters.

| Field | Type | Null? | Meaning |
|---|---|---|---|
| `products[].product` | string | no | Product code, e.g. `ES`. |
| `products[].name` | string | no | Display name, e.g. `E-mini S&P 500`; search on it, not the code. |
| `products[].calendar` | string | no | Trading calendar id the sessions come from. |
| `products[].active[]` | array of object | no | One line per status rule (`v.0`, `c.0`). |
| `active[].rule`, `is_default` | string, boolean | no | The rule; whether it is the default. |
| `active[].session_date` | date | yes | The latest complete session held. |
| `active[].raw_symbol`, `instrument_id`, `expiration` | string, integer, string | yes | The contract the rule names for it. |
| `active[].basis_session`, `definitions_as_of`, `definition_unit_id`, `basis_unit_ids`, `basis_family` | | yes | What the answer was decided from (`basis_family` is the tier: `tick_tier`, `bar_1m` or `statistics`; null for `c`); a change means the inputs changed. |
| `active[].unresolved[]` | array of object | no | The session, when the rule could not answer it. |
| `active[].next_roll` | object | yes | The next session served by a different contract, when known. |
| `active[].known_through` | date | yes | The last session the answer is known for. |
| `active[].roll_no_later_than` | date | yes | The latest session the current contract can serve. |
| `active[].reason` | string | yes | Why there is no answer (nothing held). Not a failure. |
| `active[].error` | string | yes | A refusal or broken invariant for this rule. HTTP stays `200`. |

```sh
curl 'http://127.0.0.1:8137/api/v1/futures/products'
```

```json
{"products":[{"product":"ES","name":"E-mini S&P 500","calendar":"CME_EQUITY","active":[
  {"product":"ES","rule":"v.0","is_default":true,"session_date":"2024-12-31","instrument_id":5002,"raw_symbol":"ESH5","expiration":"2025-03-21T13:30:00+00:00",…,"next_roll":null,"known_through":"2025-01-03","roll_no_later_than":"2025-03-24","reason":null,"error":null},
  {"product":"ES","rule":"c.0","is_default":false,"session_date":"2024-12-31","raw_symbol":"ESH5",…,"next_roll":{"session_date":"2025-03-24","raw_symbol":"ESM5",…},"known_through":"2027-12-31",…}]}]}
```

*Captured 2026-10-13 against the dev tick database. Abridged.*

**Errors**: `503` only when the tick or calendar database is down; a product
whose rules cannot be answered keeps HTTP `200` with `error` on its lines.

### 6.3 `GET /api/v1/futures/products/{product}/contracts`

<!-- endpoint: /api/v1/futures/products/{product}/contracts
     params: -
     errors: 503, 504 -->

Every instrument defined for the product, calendar spreads included, with the
first and last session holding its records. Ordered by expiration, then
instrument id.

| Field | Type | Null? | Meaning |
|---|---|---|---|
| `product` | string | no | Echoed. |
| `contracts[].instrument_id` | integer | no | Exchange instrument id. |
| `contracts[].raw_symbol` | string | yes | Exchange symbol, e.g. `ESZ4`, or `ESZ4-ESH5` for a spread. |
| `contracts[].instrument_class` | string | yes | `F` outright, `S` spread. |
| `contracts[].activation`, `expiration` | string | no | Validity window, UTC. |
| `contracts[].exchange`, `currency` | string | yes | e.g. `XCME`, `USD`. |
| `contracts[].min_price_increment` | string | yes | Tick size, exact decimal text. |
| `contracts[].contract_multiplier` | integer | yes | As defined; often null. |
| `contracts[].unit_of_measure`, `unit_of_measure_qty` | string | yes | e.g. `IPNT` and `"50"` (decimal text). |
| `contracts[].held` | object | yes | `{first_session, last_session}`; null when nothing is held. |

```sh
curl 'http://127.0.0.1:8137/api/v1/futures/products/ES/contracts'
```

```json
{"product":"ES","contracts":[
  {"instrument_id":118,"raw_symbol":"ESU4","instrument_class":"F","activation":"2022-06-17T13:30:00+00:00","expiration":"2024-09-20T13:30:00+00:00","exchange":"XCME","currency":"USD","min_price_increment":"0.25","contract_multiplier":null,"unit_of_measure":"IPNT","unit_of_measure_qty":"50","held":{"first_session":"2024-08-30","last_session":"2024-09-20"}},
  …]}
```

*Captured 2026-10-13: 51 contracts, one shown.*

**Errors**: `404` for an unknown product; `503`.

### 6.4 `GET /api/v1/futures/products/{product}/schedule`

<!-- endpoint: /api/v1/futures/products/{product}/schedule
     params: rule:string, start:date, end:date
     errors: 422, 503, 504 -->

The contract the rule names for each session in `[start, end)`: as runs
(`segments`), per session (`answers`), and the sessions it could not answer
(`unresolved`). The same body as `mt data tick active --product … --start …
--end … --json`'s schedule. With nothing held and a date omitted, every array
is empty. The default range comes from the tick ledger, so for a range held
only as provider bars, pass `start` and `end`.

| Name | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `rule` | string | no | `v.0` | Roll rule (§6.1). |
| `start` | date | no | first held session | First session date. |
| `end` | date | no | after the last held | End session date, **exclusive**. |

```sh
curl 'http://127.0.0.1:8137/api/v1/futures/products/ES/schedule?start=2024-09-16&end=2024-09-21'
```

```json
{"product":"ES","rule":"v.0","is_default":true,
 "segments":[{"first_session":"2024-09-16","last_session":"2024-09-17","instrument_id":118,"raw_symbol":"ESU4","expiration":"2024-09-20T13:30:00+00:00","basis":"tick_tier"},
             {"first_session":"2024-09-18","last_session":"2024-09-20","instrument_id":183748,"raw_symbol":"ESZ4","expiration":"2024-12-20T14:30:00+00:00","basis":"tick_tier"}],
 "answers":[{"session_date":"2024-09-16","instrument_id":118,"raw_symbol":"ESU4","expiration":"2024-09-20T13:30:00+00:00","basis_session":"2024-09-12","definitions_as_of":"2024-12-31","definition_unit_id":131,"basis_unit_ids":[11,12],"basis_family":"tick_tier"},…],
 "unresolved":[]}
```

*Captured 2026-10-13. Four of five answers elided.*

**Errors**: `404` unknown product; `422` bad rule, empty range, over 1,300
sessions, or a range outside the calendar; `503`.

### 6.5 `GET /api/v1/futures/contracts/{symbol}/ticks`

<!-- endpoint: /api/v1/futures/contracts/{symbol}/ticks
     params: start:date, end:date, from:date-time, until:date-time,
             format:json|msgpack
     errors: 422, 503, 504 -->

One contract's ticks, in key order (`ts_event`, `sequence`, `sequence_ordinal`),
as columnar blocks (§6.1).

| Name | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `start`, `end` | date | no | the contract's sessions held as provider bars | Session dates; `end` **exclusive**. |
| `from` | date-time | no | — | First instant, inclusive. |
| `until` | date-time | no | — | Instant bound, **exclusive**. |
| `format` | `json` \| `msgpack` | no | `json` | Response encoding (§7). |

| Field | Type | Null? | Meaning |
|---|---|---|---|
| `target` | object | no | `{kind: "contract", contract, instrument_id}`; `product`, `rule`, `is_default` null. |
| `start`, `end` | date | yes | The window read (null only when nothing is held). |
| `from`, `until` | string | yes | The instant bounds, echoed in UTC. |
| `definitions_as_of` | date | yes | Product reads only. |
| `segments`, `unresolved`, `incomplete` | array | no | §6.1. |
| `count` | integer | no | Ticks returned, over all blocks. |
| `price_scale` | integer | no | A price is its integer × 10^−`price_scale` (9). |
| `blocks[]` | array of object | no | One per non-empty session window, in order. |
| `blocks[].session_date`, `raw_symbol`, `instrument_id` | | no | Which session and contract. |
| `blocks[].count` | integer | no | Ticks in the block; every column has this many values. |
| `blocks[].ts_base` | string | no | First `ts_event`, epoch ns as text. |
| `blocks[].ts_event[]` | integer | no | Delta in ns from the previous tick; the first is 0. |
| `blocks[].sequence[]`, `sequence_ordinal[]` | integer | no | Venue sequence and the ordinal among records sharing `(ts_event, sequence)`. |
| `blocks[].price[]` | integer | yes | Trade price, fixed point. |
| `blocks[].size[]` | integer | no | Trade size. |
| `blocks[].side` | string | no | Aggressor sides, one character per tick (`A`/`B`/`N`). |
| `blocks[].bid_px[]`, `ask_px[]` | integer | yes | Best bid and offer at the trade, fixed point; null on trades-tier data. |
| `blocks[].bid_sz[]`, `ask_sz[]` | integer | yes | Their sizes. |

```sh
curl 'http://127.0.0.1:8137/api/v1/futures/contracts/ESZ4/ticks?start=2024-12-18&end=2024-12-19&from=2024-12-18T14:30:00Z&until=2024-12-18T14:35:00Z'
```

```json
{"target":{"kind":"contract","product":null,"contract":"ESZ4","instrument_id":183748,"rule":null,"is_default":null},
 "start":"2024-12-18","end":"2024-12-19","from":"2024-12-18T14:30:00Z","until":"2024-12-18T14:35:00Z",
 "definitions_as_of":null,"segments":[{"first_session":"2024-12-18","last_session":"2024-12-18","raw_symbol":"ESZ4","instrument_id":183748,"basis":null}],
 "unresolved":[],"incomplete":[],"count":1792,"price_scale":9,
 "blocks":[{"session_date":"2024-12-18","raw_symbol":"ESZ4","instrument_id":183748,"count":1792,
   "ts_base":"1734532200001440845","ts_event":[0,…],"sequence":[29838835,…],"sequence_ordinal":[0,…],
   "price":[6050250000000,…],"size":[1,…],"side":"B…",
   "bid_px":[6050000000000,…],"ask_px":[6050250000000,…],"bid_sz":[17,…],"ask_sz":[18,…]}]}
```

*Shape of the 2026-10-13 capture (first tick shown) re-encoded as blocks; the
columns are elided after their first value.*

The whole session is refused:

```sh
curl 'http://127.0.0.1:8137/api/v1/futures/contracts/ESZ4/ticks?start=2024-12-18&end=2024-12-19'
```

```json
{"error":"the request matches more than the 75,000 row limit; narrow start/end or from/until"}
```

*Captured 2026-10-13: HTTP 422.*

**Errors**: `404` no definition for the symbol; `422` an ambiguous symbol
(two instruments share it; the message lists both, and a narrower
`start`/`end` separates them), an empty range, `from` not before `until`, or
over the row ceiling; `503`.

### 6.6 `GET /api/v1/futures/contracts/{symbol}/bars`

<!-- endpoint: /api/v1/futures/contracts/{symbol}/bars
     params: granularity:TickBarGranularity, start:date, end:date,
             format:json|msgpack
     errors: 422, 503, 504 -->

One contract's provider bars (§6.1): `1m` as stored, coarser sizes rolled up per
session from them, buckets anchored at the session open. Empty buckets are
omitted, and `1d` is the whole session, stamped with its open. A session with no
provider bars returns no rows and is listed in `incomplete`.

Futures bar sizes are `1m` `5m` `15m` `1h` `4h` `1d`.
<!-- from: manta_trading.api_server.routes.futures_params.TickBarGranularity -->

| Name | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `granularity` | bar size | **yes** | — | One of the sizes above; others are a `422`. |
| `start`, `end` | date | no | the contract's held sessions | Session dates; `end` **exclusive**. |
| `format` | `json` \| `msgpack` | no | `json` | Response encoding (§7). |

The body is the ticks envelope (§6.5) without `from`/`until`, plus
`granularity` and `source` (the provider schema the bars were built from, today
always `"ohlcv-1m"`), with `bars[]` records in place of `price_scale` and
`blocks[]`:

| Field | Type | Null? | Meaning |
|---|---|---|---|
| `bars[].time` | string | no | Bucket start (session-anchored), nine fraction digits. |
| `bars[].session_date`, `raw_symbol`, `instrument_id` | | no | Which session and contract. |
| `bars[].open`, `high`, `low`, `close` | string | yes | Exact decimal text. |
| `bars[].volume` | integer | no | Contracts traded. |

**Errors**: as §6.5, plus `422` for a size outside the list.

```json
{"target":{"kind":"contract","product":null,"contract":"ESZ4","instrument_id":0,"rule":null,"is_default":null},
 "granularity":"4h","source":"ohlcv-1m","start":"2024-12-03","end":"2024-12-04","definitions_as_of":null,
 "segments":[{"first_session":"2024-12-03","last_session":"2024-12-03","raw_symbol":"ESZ4","instrument_id":0,"basis":null}],
 "unresolved":[],"incomplete":[],"count":6,
 "bars":[{"time":"2024-12-02T23:00:00.000000000Z","session_date":"2024-12-03","raw_symbol":"ESZ4","instrument_id":0,"open":"…","high":"…","low":"…","close":"…","volume":0},…]}
```

*Shape only: values are elided with `…` and zeros.*

### 6.7 `GET /api/v1/futures/products/{product}/ticks`

<!-- endpoint: /api/v1/futures/products/{product}/ticks
     params: rule:string, start:date, end:date, from:date-time,
             until:date-time, format:json|msgpack
     errors: 422, 503, 504 -->

A product's ticks under a roll rule: each session from the contract the rule
names for it, never merged across contracts. The parameters and body are
§6.5's, plus `rule` (default `v.0`); `target` is `{kind: "product", product,
rule, is_default}`, and `segments` shows the roll. Each session is its own
block, so a roll starts a new block with the new contract. Sessions in
`unresolved` have no block.

**Errors**: `404` unknown product; `422` as §6.5, plus a bad rule or a range
over 1,300 sessions; `503`, including when the calendar database is down.

### 6.8 `GET /api/v1/futures/products/{product}/bars`

<!-- endpoint: /api/v1/futures/products/{product}/bars
     params: granularity:TickBarGranularity, rule:string, start:date,
             end:date, format:json|msgpack
     errors: 422, 503, 504 -->

A product's bars under a roll rule: §6.6's parameters and body, plus `rule`.
Each bar comes from one contract; a roll shows in `segments` and in each bar's
`raw_symbol`. `segments[].basis` is the tier the *roll* was decided from; the
bars themselves always come from the provider's one-minute bars (`source`).

For `GET /api/v1/futures/products/ES/bars?granularity=1d&start=2024-09-16&end=2024-09-21`:

```json
{"target":{"kind":"product","product":"ES","contract":null,"instrument_id":null,"rule":"v.0","is_default":true},
 "granularity":"1d","source":"ohlcv-1m","start":"2024-09-16","end":"2024-09-21","definitions_as_of":"2024-12-31",
 "segments":[{"first_session":"2024-09-16","last_session":"2024-09-17","raw_symbol":"ESU4","instrument_id":118,"basis":"tick_tier"},
             {"first_session":"2024-09-18","last_session":"2024-09-20","raw_symbol":"ESZ4","instrument_id":183748,"basis":"tick_tier"}],
 "unresolved":[],"incomplete":[],"count":5,
 "bars":[{"time":"2024-09-15T22:00:00.000000000Z","session_date":"2024-09-16","raw_symbol":"ESU4","instrument_id":118,"open":"…","high":"…","low":"…","close":"…","volume":0},…]}
```

*Shape only: values are elided with `…` and zeros; four of five bars elided.*

At `1m`, a session yields about 1,380 bars, so one request covers about 54
sessions before the row ceiling.

**Errors**: as §6.7, plus `422` for a size outside the list.

### 6.9 `GET /api/v1/futures/contracts/{symbol}/tick-bars`

<!-- endpoint: /api/v1/futures/contracts/{symbol}/tick-bars
     params: ticks_per_bar:integer, start:date, end:date, from:date-time,
             until:date-time, format:json|msgpack
     errors: 422, 503, 504 -->

One contract's bars of `ticks_per_bar` trades over `[start, end)` sessions, cut
by §6.1's rule from the stored ticks. Admitted on the trades scanned, then
bounded by the row ceiling (§6.1).

| Name | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `ticks_per_bar` | integer | **yes** | — | Trades per bar, `1` or more; `0`, negatives and non-integers are a `422`. No upper bound. |
| `start`, `end` | date | no | the contract's sessions held as ticks | Session dates; `end` **exclusive**. |
| `from` | date-time | no | — | First `open_time`, inclusive. |
| `until` | date-time | no | — | `open_time` bound, **exclusive**. |
| `format` | `json` \| `msgpack` | no | `json` | Response encoding (§7). |

The envelope is §6.5's (`target`, `start`, `end`, `from`, `until`,
`definitions_as_of`, `segments`, `unresolved`, `incomplete`, `count`) plus
`ticks_per_bar`, with `bars[]` records. No `source`: tick bars have one source,
the stored trades.

| Field | Type | Null? | Meaning |
|---|---|---|---|
| `ticks_per_bar` | integer | no | Echoed. |
| `bars[].session_date`, `index` | date, integer | no | The session and the bar's 0-based position in it. |
| `bars[].open_time`, `close_time` | string | no | `ts_event` of the first and last trade, nine fraction digits. |
| `bars[].raw_symbol`, `instrument_id` | string, integer | no | The contract. |
| `bars[].open`, `high`, `low`, `close` | string | yes | Exact decimal text. |
| `bars[].volume`, `trade_count` | integer | no | Summed size; trades in the bar. |
| `bars[].buy_volume`, `sell_volume` | integer | no | Size of trades with aggressor side `B`, `A`. |
| `bars[].bid_px`, `ask_px` | string | yes | Quote at the last trade; null on trades-tier data. |
| `bars[].partial` | boolean | no | The session's last bar, with fewer than `ticks_per_bar` trades. |

```json
{"target":{"kind":"contract","product":null,"contract":"ESZ4","instrument_id":0,"rule":null,"is_default":null},
 "ticks_per_bar":1000,"start":"2024-12-03","end":"2024-12-04","from":null,"until":null,"definitions_as_of":null,
 "segments":[{"first_session":"2024-12-03","last_session":"2024-12-03","raw_symbol":"ESZ4","instrument_id":0,"basis":null}],
 "unresolved":[],"incomplete":[],"count":2,
 "bars":[{"session_date":"2024-12-03","index":0,"open_time":"2024-12-02T23:00:00.000000000Z","close_time":"…",
          "raw_symbol":"ESZ4","instrument_id":0,"open":"…","high":"…","low":"…","close":"…",
          "volume":0,"trade_count":1000,"buy_volume":0,"sell_volume":0,"bid_px":"…","ask_px":"…","partial":false},…]}
```

*Shape only: values are elided with `…` and zeros.*

Over the trade limit:

```json
{"error":"the request scans 2,431,907 trades, over the 2,000,000 trade limit; narrow start/end"}
```

**Errors**: `404` no definition for the symbol; `422` a bad `ticks_per_bar`, an
ambiguous symbol, an empty range, `from` not before `until`, over the trade
limit, or over the row ceiling; `500` when stored ticks break the cutting rule
(`stored ticks for session … of … cannot be cut into bars; see the server
log`: a data fault for an operator, not a client error); `503`. A client that
disconnects mid-read gets nothing: the read stops and the route answers a
bodiless `499`, which it does not log as an error.

### 6.10 `GET /api/v1/futures/products/{product}/tick-bars`

<!-- endpoint: /api/v1/futures/products/{product}/tick-bars
     params: ticks_per_bar:integer, rule:string, start:date, end:date,
             from:date-time, until:date-time, format:json|msgpack
     errors: 422, 503, 504 -->

A product's tick bars under a roll rule: §6.9's parameters and body, plus
`rule` (default `v.0`). Each session is cut from the contract the rule names for
it, from the same schedule as `/ticks`, `/bars` and `/schedule`, and its count
restarts at `index` 0; a roll falls on a session boundary, so it shows in
`segments` and in each bar's `raw_symbol` and never splits a bar.

**Errors**: `404` unknown product; `422` as §6.9, plus a bad rule or a range over
1,300 sessions; `500` and `503` as §6.9, `503` also when the calendar database
is down.

## 7. msgpack: what it actually saves

Every time-series route accepts `format=msgpack` — equity bars (§3.2), the
futures time series (§6), Kalshi candlesticks (§5.1) and Kalshi trades (§5.2).
The response body is
`application/x-msgpack`; the field names, types and structure are identical to
the JSON form. Both media types are declared on the `200` of each of them,
so a generated client must expect either.

**Measured savings, captured 2026-09-16:**

| Route | Payload reduction vs JSON |
|---|---|
| Equity bars | **35.5%** — flat from four days to eight weeks |
| Kalshi candlesticks | **20.1%** |
| Kalshi trades | **14.2%** |

Two things follow that are easy to get wrong:

- **A longer window does not improve the ratio.** Bars measured flat across
  windows from four days to eight weeks. If 35% is not enough, a bigger request
  will not help.
- **The Kalshi routes save least, and it is structural.** `mode="json"`
  stringifies their fixed-point `Decimal` prices *before* either encoder runs
  (§2.3), and msgpack cannot pack a string more tightly than JSON can. Bars cap
  at ~36% for the same reason applied to timestamps, which are 25-character
  ISO-8601 strings rather than packed integers. What msgpack saves here is key
  and delimiter overhead, not the values.

Use it when bandwidth is the constraint and you already have a decoder. It is
not a substitute for narrowing a window.

## 8. Field glossary

Terms that mean something specific here, gathered in one place. Each links to
where it is specified.

| Term | Where | Means |
|---|---|---|
| `is_stale` | §3.2 | The continuous aggregate serving this grain is behind its source. **Always `false` for `1m` and `1d`**, which are raw — by construction, not by luck (§2.6). |
| `coverage` | §3.1, §3.5 | On `/health`, a coarse whole-system verdict. On `/status`, the per-view freshness verdicts. |
| `gap_count` | §3.5 | **Open** gaps only (`UNKNOWN`, `FAILED_RETRYABLE`). Terminal statuses are excluded so the number can reach zero. |
| `lag_seconds` | §3.5 | Seconds a view is behind, as a float. **`null` is not `0.0`** — it means "could not be measured". |
| `pass` | §5.3 | A `PassKind` token. Serialized by **alias**; the Python attribute is `pass_` because `pass` is a keyword. |
| `count` | everywhere | Rows **in this response after filtering** — never a total. On `/status` a `count: 0` means nothing is wrong (§3.5). |
| `available` | §3.4 | Per-granularity `{start, end}`. `end` is exact; `start` is as of the last coverage materialization and can be later than the true first bar, never earlier. |
| `tape_filtered` | §5.2 | This market's category is excluded from trade collection **by policy**, so an empty window is expected rather than a gap. |
| `collected` | §5.1 | This market is in the candle collection set. `false` means no candles are stored at all. |
| `complete_through` | §5.1 | Requested and stored through this instant — not "the newest stored candle". |
| `coverage_from` | §5.1, §5.2 | The oldest instant the stored data reaches. |

## 9. Keeping this accurate

This document is hand-written, and checked mechanically by
`scripts/check_api_docs.py`:

```sh
uv run python scripts/check_api_docs.py            # report
uv run python scripts/check_api_docs.py --check    # exit 1 on any failure
```

It runs in the unit tier as `test/unit/api_server/test_api_docs.py`, needs no
database, and reads the committed [`openapi.json`](openapi.json) rather than a
live server.

### What makes a path *documented*

An explicit marker, never a mention in prose. Each endpoint section carries one:

```
<!-- endpoint: /api/v1/bars/{symbol}
     params: granularity:Granularity, start:date, end:date,
             adjusted:boolean, prints:PrintMode, format:json|msgpack
     errors: 422, 504 -->
```

- `params` and `errors` may wrap across lines. Write `-` to state "none"
  explicitly, so a section that forgot the line and one that means none are
  distinguishable.
- Parameter types use the canonical spelling the gate derives from the schema
  (`Granularity`, `date|date-time`, `json|msgpack`, `daily|minute`). A mismatch
  prints both spellings.
- `errors` must be a **subset** of the statuses the artifact declares for that
  path. Staying silent about a status is allowed; promising one the route
  cannot send is not.

Prose inference was rejected deliberately: a regex over English cannot tell a
section that documents `/api/v1/status` from a sentence that mentions it.

### What makes a value *checked*

A marker naming the symbol it must track:

```
Granularity tokens are `1m` `5m` `15m` `1h` `4h` `1d` `1w` `1mo` `1q`.
<!-- from: manta_trading.constants.Granularity -->
```

The gate imports the symbol and compares. Scalars compare by value; enums
compare as **token sets** — the backticked words in the sentence the marker
closes — so a new member fails rather than quietly extending a documented list.
The sentence may wrap across lines. Keep incidental backticked words
(a parameter name, say) in a *different* sentence from a token set, or they
will be read as members of it.

### What the gate cannot check

**Whether a sentence is true.** It knows a section claims to document a path
that exists, with the parameters that exist, promising only statuses that are
declared. It does not know whether "raw grains are never stale by construction"
is correct, whether an example's output was really produced by the command
above it, or whether a field's stated meaning matches what the code does.

Prose accuracy is the reviewer's job and the verification walkthrough's. When
you change a route, re-run the examples — the gate will not notice that they
are stale.

### Adding a route

1. Add an endpoint section with its marker, field table, errors and an executed
   `curl` example with a capture date.
2. Add a capability entry in [`agents.md`](agents.md) — the gate requires every
   path in **both** documents.
3. Run `uv run python scripts/check_api_docs.py --check`.

### Known schema gaps

Two mismatches between what the API sends and what `openapi.json` declares.
Both are documented rather than fixed here, because fixing either changes a
published schema — a client-visible contract change. Both are recorded as
Future Work in
[`180-slices.data-serving-api.md`](../../project-documents/user/architecture/180-slices.data-serving-api.md):

- **The `422` shape** (§2.7): the schema declares `HTTPValidationError` for
  every `422`, but hand-written refusals send `{"error": "…"}`.
- **`404` is never declared.** Four route modules raise a `404` for an unknown
  symbol, series, event or market, and **no path in the artifact declares one**.
  This reference documents the `404` in each section's prose where it is real;
  the endpoint markers do not list it, because a marker asserts what the
  artifact declares.
