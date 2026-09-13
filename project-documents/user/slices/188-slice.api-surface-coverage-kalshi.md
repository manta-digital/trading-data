---
docType: slice-design
slice: api-surface-coverage-kalshi
project: trading-data
parent: user/architecture/180-slices.data-serving-api.md
dependencies: [186, 187, 262, 264, 265, 268]
interfaces: [189, 190, 907]
dateCreated: 20260912
dateUpdated: 20260913
status: not_started
effort: 3
---

# Slice Design: API Surface Coverage — Kalshi (188)

## Overview

Initiative 260 collects Kalshi event-contract data continuously and none of it
is reachable over HTTP. `src/manta_trading/api_server/` has zero references to
Kalshi; the README says so in one line ("The API serves equity data only;
Kalshi data is read via `mt data kalshi status` or SQL for now"). Measured on
production 2026-09-12: `kalshi.trades` 345,142,063 rows, `kalshi.candlesticks`
239,848,780, `kalshi.markets` 11,055,712, `kalshi.events` 595,789,
`kalshi.series` 14,013.

This slice adds a `/api/v1/kalshi/*` surface: catalog reads that follow
Kalshi's own hierarchy (series → events → markets, settlement outcome on the
market), and two per-market time-series endpoints (candlesticks, trades) shaped
like `/api/v1/bars/{symbol}` where the semantics allow. It reuses the pool, the
error handlers, the `504`/`422`/`404` contracts, and the rows-per-response
ceiling that slices 184–187 established. No new serving machinery, no new
tables, no migration.

Every decision below that rests on a number cites a measurement taken on
production during this design (section *Measurements*), following 187's
precedent — the range-cap policy in particular is sized from what is stored,
not from what the catalog says a market traded.

## Value

- **Consumer-facing**: 585M+ rows of collected Kalshi data become readable by a
  non-Python consumer (trading-ui, an agent, a notebook on another host) with
  the same contracts the equities endpoints already honor.
- **Architectural**: closes the gap 180's 2026-09-12 amendment names — an
  unserved surface is a sequencing note, not a boundary — and gives 189
  (operations/freshness) and 190 (documentation) a Kalshi surface to build on.
- **Contract hygiene**: the unresolved "range cap for a 345M-row table" question
  from the slice-plan entry is settled by measurement rather than left to the
  first client to trip over.

## Technical Scope

**In scope**

| Area | Deliverable |
|---|---|
| `data/kalshi/serve_catalog.py` | New — sync read functions for series, events, markets (seeks, scoped lists, count guards) |
| `data/kalshi/serve_timeseries.py` | New — sync read functions for candles and trades per market, plus the completeness facts each response carries |
| `api_server/routes/kalshi_catalog.py` | New — seven catalog routes: categories, the series/events/markets lists, and the three seeks (D2, D3) |
| `api_server/routes/kalshi_timeseries.py` | New — candlesticks and trades routes (D4, D5, D6) |
| `api_server/models/kalshi.py` | New — Pydantic response models (responses.py is at 291 lines; a second module rather than a 600-line one) |
| `api_server/serialization.py` | New — the json/msgpack `Response` builder extracted from `bars.py`, used by bars, candles, and trades (D7) |
| `api_server/app.py` | Register the two routers; `description` string updated; `app.state.kalshi_trades_excluded` resolved once in lifespan (D6) |
| `api_server/deps.py` | `get_kalshi_trades_excluded` accessor, same shape as `get_max_bars` |
| `constants.py` / README | Rows-per-response ceiling re-described as the ceiling for every time-series and list response, not only bars (D8) |
| `docs/api/openapi.json` | Regenerated; the drift test enforces it |
| `test/unit/api_server/test_kalshi_*.py` | Route tests with faked readers, one per contract in *API Specification* |
| `test/integration/test_kalshi_serving.py` | Reader tests against a throwaway DB with the kalshi track applied |
| `test/load/test_188_kalshi_api_nfr.py` | Load tier per the Python rules (D11) |

**Explicitly excluded**

- **A cross-cutting markets list** (`GET /kalshi/markets?status=active` with no
  event scope). 122,240 markets are `active` today; at ~1 KB per full market row
  that is a 120 MB response, above every ceiling this API has. It needs either
  pagination or a slimmer projection with a bounded time window — a design
  question of its own, recorded under *Future work* in the slice plan, not
  smuggled in here.
- **Derived candle periods** (`period=60`, `1440`). Only 1-minute candles are
  collected (264 Decision 1: coarser bars are derived locally — but nothing
  derives them today). Serving hour/day candles means a `time_bucket`
  aggregation over fourteen nullable OHLC columns with open/close/mean/previous
  semantics to get right. Not this slice; `period_minutes` is reported on every
  response so a later slice can widen it without a shape change.
- **Operational/freshness verdicts** (`behind`, `lag`, awaiting-settlement
  ages, candle backlog). That is `mt data kalshi status`, and it belongs to 189
  with the other slice-922 surfaces. This slice exposes only the
  *per-response* completeness facts (D5) a data consumer needs to interpret a
  count of zero.
- Consumer documentation beyond the README delta (190).
- Any change to the bars contract, the equities symbol vocabulary, or pool
  sizing.

## Dependencies

### Prerequisites

- 186 (error-body shape, `404`/`200` split, `504` handler, rows ceiling setting,
  committed `openapi.json`) and 187 (`create_app(db_url=)` seam, load tier
  conventions, scoped-checkout pattern) — both complete.
- 262/264/265/268 — the Kalshi schema and the write-side semantics this slice
  reads: `market_candle_state.watermark_ts` ("requested through", not "newest
  candle"), `sync_state['trades'].watermark_ts` ("tape complete through"),
  `coverage_from_ts`, and `selection.trades_filter_sql`. All complete.

### Interfaces required

- `app.state.db_pool` (shared `ConnectionPool`, max 8) via `get_db` /
  `get_db_pool`.
- `Settings.kalshi_trades_excluded_categories` and
  `selection.trades_filter_sql` — the one rendering of the tape filter (268
  Decision 3). Never re-spelled.
- `data/kalshi/trade_status._effective_floor` — the lower of the live tape
  floor and the historical walk's watermark (267 Decision 8). It is private
  today; this slice promotes it to a public `effective_tape_floor` in the same
  module and calls it from both places, so the CLI and the API cannot report
  two different floors.
- `manta_trading.data.kalshi.constants.MarketStatus`, `CandlePeriod`,
  `COLLECTED_CANDLE_PERIOD` — filter vocabulary and the served period.

### Interfaces provided

- To **189**: the `/api/v1/kalshi/` namespace and the `models/kalshi.py`
  module; 189 adds status/freshness there rather than inventing a second
  prefix.
- To **190**: nine routes — seven catalog, two time series — with docstrings
  written as public descriptions (the
  187 rule: FastAPI publishes the docstring; decision references stay in
  comments), and the regenerated `openapi.json`.

## Measurements (production `trading`, 2026-09-12, read-only)

All timestamps UTC. Each query was bounded by `statement_timeout = 150s`; none
came close.

| Fact | Value | Bears on |
|---|---|---|
| Series / events / markets | 14,013 / 595,789 / 11,055,712 | D2 list scoping |
| Distinct series categories (2026-09-13) | 20, none null; largest Sports 3,638, smallest Education / Business 1 each | D2 categories route |
| Whole series list, served projection vs `GROUP BY category` (2026-09-13) | 6.2 MB / 14,013 rows vs 20 rows in 15 ms | D2 — why the categories route exists |
| Markets by status | finalized 10,869,710 · active 122,240 · initialized 48,195 · closed 15,347 · inactive 2,155 · determined 466 | cross-cutting list exclusion |
| Events per series: max / p99 | 24,382 (`KXETH15M`) / 1,536 | D2, D8 ceiling |
| Markets per event: max / p99 / mean | 440 / 300 / 18.6 | D2 |
| `markets WHERE event_ticker = ?` | index scan, 0.065 ms execution | D2 |
| Hypertable chunks: trades / candles | 37 (34 compressed) / 46 (43 compressed), `segmentby market_ticker` | D4 plan cost |
| Per-market, time-bounded read plan | 3.8 ms planning (trades), 1.6 ms (candles); execution 0.05 ms | D4 — no 187-D1 planning cliff here |
| Largest stored market: trades / candles | `KXMAYORLA-26-SPRA` 132,552 trades, 68,663 candles over 5 months | D4 cap, D8 ceiling |
| Estimated vs actual candles for that market | span ÷ 1 min ≈ 218,000 estimated; 68,663 stored | D4 — a window estimate over-rejects by 3× |
| Busiest market, last 24 h: trades / candles | 41,320 / 1,379 (1,440 ceiling) | D4 |
| Counting 357k trades across 5 markets | 205 ms (≈1.7M rows/s) | D4 count-first guard cost |
| Exchange-wide tape, busiest hour of last 24 h | 14,888 trades | context: Crypto filter (268) is in force |
| `market_candle_state` rows | 1,151,400 | D5 — "collected" is a state-row fact |
| `sync_state` trades: coverage_from / watermark | 2026-07-01 00:00 / 2026-09-13 03:19 | D5 |
| `sync_state` historical watermark (floor reached) | 2026-01-01 00:00 | D5 effective floor |
| Top catalog-volume markets (`KXMENWORLDCUP-26-*`) | zero stored trades and candles | D4 — size from stored data, not catalog volume |

## Technical Decisions

### D1 — Kalshi lives under `/api/v1/kalshi/*`, not in the symbol/bars vocabulary

A Kalshi market is not an instrument in `instruments`, its ticker is not a
symbol, its prices are yes/no contract prices in dollars with no adjustment
concept, and its lifecycle (open → close → determined → finalized, with a
settlement outcome) has no equities analogue. Folding it into `/symbols` and
`/bars` would make `adjusted`, `granularity`, and `available` meaningless on
half the surface and force a discriminator into every equities response.

**Decision:** a namespace prefix, `/api/v1/kalshi/`, with resources named in
Kalshi's own hierarchy. The equities routes are untouched. Slice 189 extends the
same prefix.

### D2 — Catalog resources follow the hierarchy; every list is scoped by its parent

The catalog is read through the tree, never flat:

| Route | Scope | Measured worst case |
|---|---|---|
| `GET /kalshi/categories` | `GROUP BY category` over the series table | 20 rows |
| `GET /kalshi/series` | whole table, optional `category=` (exact) and `search=` (ticker prefix, `ILIKE 'X%'` as `symbols` does) | 14,013 rows |
| `GET /kalshi/series/{ticker}` | primary-key seek | 1 |
| `GET /kalshi/series/{ticker}/events` | `events_series_ticker_idx`; optional `strike_from`/`strike_to` (dates, inclusive, on `strike_date`) | 24,382 |
| `GET /kalshi/events/{event_ticker}` | primary-key seek | 1 |
| `GET /kalshi/events/{event_ticker}/markets` | `markets_event_ticker_idx`; optional `status=` | 440 |
| `GET /kalshi/markets/{ticker}` | primary-key seek | 1 |

There is no unscoped events or markets list (see *Explicitly excluded*). The
scoped lists are all inside the rows-per-response ceiling today (D8), and each
carries the count guard so that a series that outgrows the ceiling fails
explicitly rather than by a 40 MB response.

`GET /kalshi/categories` is the entry point to that tree, and it exists because
without it the tree has no root a client can find. `category=` is the only
filter that makes the 14,013-row series list navigable, and `category` is free
text Kalshi assigns — it is not an enum anywhere in this codebase, so unlike
`status=` its accepted values cannot be derived from a type. Measured on
production 2026-09-13: 20 distinct categories, none null; the whole-table
series response in its *served* projection (no `raw`) is 6.2 MB, and the
`GROUP BY` that replaces it is 20 rows in 15 ms. Without this route the only
way to learn what to pass to `category=` is to download the 6.2 MB list and
reduce it client-side — the exact request the filter exists to avoid.

The response carries the series count per category, which costs nothing over
the same scan and turns the route into the catalog's size map:

```
GET /kalshi/categories
→ {"count": 20, "categories": [{"category": "Sports", "series_count": 3638}, …]}
```

Ordered by `category` so the response is stable between calls. No count guard:
the row count is bounded by the number of distinct categories, which is 20.

`status=` accepts `MarketStatus` values (the *served* vocabulary — `active`,
`finalized`, … — not Kalshi's filter vocabulary), validated the way
`status.py::_resolve_health_filter` validates `health`: a comma-separated list,
each token checked against the enum, `422` naming the valid set. The valid set
is derived from the enum, never restated.

Every row is projected from the typed columns only. `raw` is never served: it is
the capture-before-it-disappears column (261), not a contract, and at 11M rows
of JSONB it is the bulk of the table.

### D3 — Settlement outcome is on the market resource, not its own endpoint

`kalshi.markets` already carries `result`, `expiration_value`,
`settlement_ts`, `settlement_value_dollars`, and `status`. A
`/markets/{ticker}/settlement` endpoint would be a second seek of the same row
returning five of its columns. **Decision:** the market response includes a
nested `settlement` object built from those columns, present on every market
(fields `null` until settled), so a client reads outcome and lifecycle in one
call. "Which markets in this event settled YES" is
`GET /kalshi/events/{event_ticker}/markets?status=finalized` plus a client-side
filter on `settlement.result` — 440 rows at most.

`awaiting_settlement` (the collector's work queue) is not a consumer resource;
its ages and stuck counts are 189's.

### D4 — Time-series range policy: count-first admission against the shared ceiling, `422` above it, no pagination, no truncation

The bars cap (186 D4) estimates rows from the window alone because equities
bars have a known density. Kalshi rows do not: candles are sparse by
construction (Kalshi serves a candle only for a period with activity — the
largest stored market has 68,663 candles where its span predicts 218,000), and
trades have no rate at all (41,320 in one day for one market, 132,552 over five
months for another). A window estimate would reject valid requests by 3× on
candles and cannot be written for trades.

**Decision:** for both time-series endpoints, the reader issues
`SELECT count(*) … WHERE market_ticker = %s [AND time bounds]` first, then the
row fetch only if the count is at or under the ceiling. Above it, `422` carrying
the actual count, the ceiling, and the advice to narrow `start`/`end`. Measured
cost of the count: ~1.7M rows/s over compressed, segment-indexed chunks, so a
count at the ceiling is under 100 ms and a count that rejects a multi-million
row request is the only work that request does.

The mechanism differs from bars' estimate on purpose and for a measured reason;
the *policy* is identical: reject before serialization, never silently truncate,
never paginate (186 D4's rejection of pagination stands — a client that needs a
whole 345M-row tape should read the database). The fetch carries no `LIMIT`: a
row inserted between the count and the fetch makes the response one row over
the ceiling, never one row short, which is the direction the project's rules
care about.

`start`/`end` are **optional**. Omitted means the market's whole stored tape or
candle history, bounded by the count guard — the natural request for the
15-minute and one-day markets that are most of the catalog. When present they
are ISO-8601 datetimes; a bare date is accepted and means midnight UTC (`start`)
or the last instant of that day (`end`), exactly the bars convention; a naive
datetime is read as UTC, stated in the parameter description and the OpenAPI
schema. `start > end` is `422` (bars precedent). Bounds are bound as
`timestamptz` parameters, never dates — 187 D8's 3,100 ms lesson applies to any
hypertable predicate.

Candle windows filter on `end_period_ts` (the row's time column). The response
says so; the field is not relabelled `timestamp`, because a Kalshi candle is
identified by the period it *closes*.

### D5 — Per-response completeness facts replace `is_stale`

Bars carry `is_stale` because a cagg can silently lag its source. Kalshi has no
caggs; the equivalent trap is a `200` with `count: 0` that means one of four
things: no activity, not collected for this market, the window is past what the
collector has reached, or the category is tape-filtered. The response must let
a client tell these apart without consulting the CLI.

**Candlesticks** carry, from `market_candle_state` for this market and
`COLLECTED_CANDLE_PERIOD`:

- `coverage_from` — `coverage_from_ts`; `null` when no state row exists.
- `complete_through` — `watermark_ts`: candles were requested and stored
  through this instant (264 Decision 3 — **not** the newest stored candle).
  `null` when no state row exists.
- `collected` — `true` iff a state row exists. `false` means the market is
  outside the collection rule or has not been reached yet; a zero count is then
  not evidence of no activity.

**Trades** carry, from `sync_state` and the market's series category:

- `coverage_from` — the effective tape floor (`effective_tape_floor`, D-list
  *Interfaces required*): the lower of the live floor and the historical walk's
  watermark. Today 2026-01-01 00:00 UTC.
- `tape_complete_through` — `sync_state['trades'].watermark_ts` (265
  Decision 1: complete through the end of the last fully walked window).
- `tape_filtered` — `true` iff the market's category is in
  `MT_KALSHI_TRADES_EXCLUDED_CATEGORIES`, evaluated with the same
  `trades_filter_sql` predicate the write path uses, against the same
  `Settings` value (resolved once in lifespan onto `app.state`, the 186 D9
  pattern). A filtered market's trades are classified and not stored (268);
  `count: 0, tape_filtered: true` is the honest answer.

No `behind`/lag verdict here — that is a freshness judgement (189). These are
facts read from state rows, not computed thresholds.

### D6 — `404` means "unknown ticker"; the market row is read up front on the time-series routes

The 186 D5 contract applies unchanged: `404` only when the ticker does not
exist; a known market with nothing in the window is `200, count: 0` with the D5
facts populated.

Unlike bars, which look the symbol up only on the empty path, the time-series
routes seek the market row **first**, every request: the response needs its
series category (D5 `tape_filtered` — a markets → events → series join, three
index seeks in one statement) and the state rows keyed by ticker. One
sub-millisecond seek per request is the price of a response that can explain
itself, and it settles the `404` before any count runs.

### D7 — One serialization helper for every time-series response

`bars.py` builds its json/msgpack `Response` inline (`orjson.dumps` /
`msgpack.packb(default=str)`). Two more routes need the same branch.
**Decision:** extract it to `api_server/serialization.py::timeseries_response(model, fmt)`
and call it from all three. `format=json|msgpack` is offered on candles and
trades with the bars semantics and media types.

**Decimal fields serialize as strings** (`"0.4900"`), on the wire exactly as
Kalshi serves them and as `NUMERIC` stores them. The alternative — `float` —
is what bars do for equity prices, but Kalshi prices are fixed-point dollars
whose exactness the storage layer went to some trouble to preserve (261
Decision 5); a JSON `0.49` that round-trips to `0.48999999` would undo that at
the last hop. Pydantic v2 already emits `Decimal` as a string in JSON mode; the
helper dumps with `mode="json"` so orjson and msgpack see strings, not
`Decimal` objects — this is also what makes `default=str` unnecessary on the
msgpack path. Timestamps are aware UTC datetimes rendered ISO-8601, as
everywhere else in the API.

### D8 — One rows-per-response ceiling for the whole API

`MT_API_MAX_BARS_PER_REQUEST` (default 75,000, 186 D4/D9) bounds one thing: the
rows a single response carries. That is exactly the bound the Kalshi lists and
time-series need, and the measured worst cases sit under it today (24,382
events; 68,663 candles; 440 markets; 14,013 series). A second knob with the same
number would be two things to keep in step.

**Decision:** reuse the existing setting and constant for every Kalshi list and
time-series response. The env-var name keeps its historical `BARS`; the README
and the constant's docstring say what it now bounds: *rows per response —
equity bars, Kalshi candles and trades, and scoped Kalshi catalog lists*. No
rename, no second setting. A candle row is wider than a bar (sixteen numeric
columns), so a ceiling-sized candle response is roughly 2× a ceiling-sized bars
response; Phase 6 measures it and records the figure next to 186's 11.58 MB.

The catalog lists apply the ceiling via the same count-first guard as D4
(`count(*)` on the scoping index — sub-millisecond for events-per-series,
0.6 s for the whole-table markets-per-event distribution, which no route
issues).

### D9 — Reads live in `data/kalshi/`, routes stay thin

The arch's rule: a new query capability lands in the data layer, testable
without HTTP, then a route exposes it. The Kalshi package already keeps its
sync readers there (`status.py`, `trade_status.py`, `historical_status.py`),
consumed by the CLI. **Decision:** two new sync modules, `serve_catalog.py` and
`serve_timeseries.py`, each a set of pure functions over a
`psycopg.Connection` returning frozen dataclasses; the route modules map
dataclasses onto Pydantic models and raise `HTTPException`. Neither module
imports the client, the transport, or the config layer (the `status.py`
discipline), so a future `mt data kalshi markets …` can call them unchanged.

Statements within one route run sequentially on one connection inside one
executor call (187 D7: psycopg serializes on the connection lock; gathering
buys nothing). Catalog routes use `get_db` (whole-request checkout — the whole
request is a few milliseconds). Time-series routes use `get_db_pool` and scope
the checkout to the seek + count + fetch, releasing before serialization (185
D8a): a ceiling-sized response serializes for hundreds of milliseconds, and
eight of those holding connections would stall `/health`.

### D10 — Failure modes on the new paths

Enumerated per 185 D9 / 186 D5, because these paths decide status codes:

- Any statement cancelled by `statement_timeout` → `QueryCanceled` → `504`
  (186 D10). The 20 s budget is generous: the slowest statement in this design
  is a ceiling-sized count at ~100 ms.
- Any other `psycopg.Error` → global handler → sanitized `500`. Never caught in
  the readers, never defaulted: a failed existence seek must not become a `404`
  and a failed count must not become an admitted fetch.
- `sync_state['trades']` absent (trades phase never ran) → the trades route
  answers `200` with `tape_complete_through: null`, `coverage_from: null` — a
  fact about the collector, not an error, and the state a fresh install is in.
  Recorded in the field descriptions.
- A `status=` token outside `MarketStatus`, an empty `status=`, `start > end`
  → `422` with the message naming the fix, via the app-level `{"error": …}`
  handler (186 D6). FastAPI's own type failures keep their native `detail`
  body, as documented.

### D11 — Load tier: what only a load test can assert

Per the Python rules, code on the network/concurrency path needs a load test
with latency or resource bounds. Manual gate and fixture conventions inherited
from 187 D9 (`MT_RUN_LOAD_TESTS=1`, ephemeral DB, the prod-URL guard;
`create_app(db_url=)` is the seam). The Kalshi track is applied with
`test/integration/kalshi_helpers.apply_kalshi_track`, which moves to
`test/kalshi_support/` so both tiers import it from one place.

**CI gating stays with 907, as it did for 167 and 187.** The repository's only
workflow is publish-on-tag with no test job, so this tier runs manually until
slice 907 (CI Pipeline and Load-Test Gating) lands; 907 already names the load
tier in its scope. The module docstring records the manual command and that
deferral, and claims no enforcement that does not exist.

Fixture `kalshi_dense_db`: one series with 25,000 events (above the measured
24,382 maximum), one event with 450 markets, and one market with
`ceiling + 1,000` trades and `ceiling + 1,000` candles seeded by `COPY`, plus
the `sync_state`/`market_candle_state` rows the D5 facts read.

Assertions, each with a provisional bound re-derived from a Phase 6 measurement
before it is committed:

1. **Trades at the ceiling**: a windowed request admitting exactly the ceiling
   completes end to end (count + fetch + serialize) — provisional **< 15 s**,
   bars' bound, expected far lower since there is no 3,371-chunk planning cost.
2. **Rejection costs only the count**: the whole-tape request over
   `ceiling + 1,000` rows returns `422` — provisional **< 500 ms** — and the
   fake-free assertion that no row fetch ran (statement log or a counting
   `Connection` wrapper, decided in tasks).
3. **Events list at the measured maximum**: 25,000-row response — provisional
   **< 2 s**.
4. **Concurrency against the pool**: 16 concurrent market-detail requests with
   `max_size=8` all complete within the queueing factor of the single-request
   bound (187 D10 assertion 3, applied to the new routes).

### D12 — Documents touched by this slice

| Document | Change |
|---|---|
| `README.md` | Remove "serves equity data only"; add the nine routes and the D5 field meanings in the endpoint list; re-describe `MT_API_MAX_BARS_PER_REQUEST` (D8) |
| `app.py` `description` | Names Kalshi; 190 replaces it with the real reference |
| `docs/api/openapi.json` | Regenerated at slice close |
| `180-arch.data-serving.md` | Endpoints section gains a Kalshi subsection pointing here; Range Policy notes the count-first mechanism |
| `180-slices.data-serving-api.md` | Entry 8 linked; *Future work* gains the cross-cutting markets list and derived candle periods |

## API Specification

All routes: `GET`, JSON by default, errors as `{"error": "…"}`, `504`
declared via `GATEWAY_TIMEOUT_RESPONSE`. Prefix `/api/v1/kalshi`.

### Catalog

```
GET /categories
→ {"count": 20, "categories": [{"category": "AI", "series_count": 5}, …]}

GET /series?category=Politics&search=KXFED
→ {"count": 12, "series": [SeriesRecord, …]}

GET /series/{ticker}
→ SeriesRecord | 404

GET /series/{ticker}/events?strike_from=2026-09-01&strike_to=2026-09-30
→ {"series_ticker": "KXFEDDECISION", "count": 3, "events": [EventRecord, …]} | 404 | 422 (over ceiling)

GET /events/{event_ticker}
→ EventRecord | 404

GET /events/{event_ticker}/markets?status=active,finalized
→ {"event_ticker": "…", "count": 2, "markets": [MarketRecord, …]} | 404 | 422 (bad status, over ceiling)

GET /markets/{ticker}
→ MarketRecord | 404
```

`SeriesRecord`: `ticker, frequency, title, category, tags, settlement_sources,
fee_type, fee_multiplier, contract_url, contract_terms_url, product_metadata,
last_updated_ts, first_seen_at, last_synced_at`.

`EventRecord`: `event_ticker, series_ticker, title, sub_title, category,
mutually_exclusive, strike_date, strike_period, collateral_return_type,
available_on_brokers, settlement_sources, product_metadata, last_updated_ts,
first_seen_at, last_synced_at`.

`MarketRecord`: every typed `kalshi.markets` column except `raw`, grouped:
identity and text (`ticker, event_ticker, market_type, status, title,
subtitle, yes_sub_title, no_sub_title, rules_primary, rules_secondary`),
`lifecycle {created_time, open_time, close_time, expiration_time,
expected_expiration_time, latest_expiration_time, updated_time}`,
`settlement {result, expiration_value, settlement_ts, settlement_value_dollars,
can_close_early}`, `economics {notional_value_dollars, last_price_dollars,
previous_price_dollars, yes_bid_dollars, yes_ask_dollars, no_bid_dollars,
no_ask_dollars, previous_yes_bid_dollars, previous_yes_ask_dollars,
liquidity_dollars, volume_fp, volume_24h_fp, open_interest_fp,
yes_bid_size_fp, yes_ask_size_fp}`, classification (`strike_type,
price_level_structure, is_provisional, mve_collection_ticker`), and
`first_seen_at, last_synced_at`. The grouping is a response-model choice for
readability; the column names inside each group are the DB/Kalshi names
verbatim, so 190's reference can point at Kalshi's own field documentation.

### Time series

```
GET /markets/{ticker}/candlesticks?start=2026-06-01T00:00:00Z&end=2026-06-08&format=json
→ {
    "market_ticker": "KXMAYORLA-26-SPRA",
    "period_minutes": 1,
    "collected": true,
    "coverage_from": "2026-01-08T01:00:00Z",
    "complete_through": "2026-06-09T00:00:49Z",
    "count": 4123,
    "candlesticks": [
      {"end_period_ts": "2026-06-01T00:01:00Z",
       "yes_bid": {"open_dollars": "0.4800", "high_dollars": …, "low_dollars": …, "close_dollars": …},
       "yes_ask": {…},
       "price":   {"open_dollars": …, "high_dollars": …, "low_dollars": …, "close_dollars": …,
                   "previous_dollars": …, "mean_dollars": …},
       "volume_fp": "12.00", "open_interest_fp": "48211.00"}
    ]
  }
| 404 (unknown ticker) | 422 (start > end; count over ceiling)

GET /markets/{ticker}/trades?start=…&end=…&format=msgpack
→ {
    "market_ticker": "…",
    "coverage_from": "2026-01-01T00:00:00Z",
    "tape_complete_through": "2026-09-13T03:19:13Z",
    "tape_filtered": false,
    "count": 9870,
    "trades": [
      {"trade_id": "…", "created_time": "…", "count_fp": "5.00",
       "yes_price_dollars": "0.4900", "no_price_dollars": "0.5100",
       "taker_outcome_side": "yes", "taker_book_side": "yes", "is_block_trade": false}
    ]
  }
| 404 | 422
```

The candle's nested `yes_bid`/`yes_ask`/`price` objects are the wire shape
Kalshi serves and `Candlestick` parses; the flat fourteen columns
(`candle_repository.CANDLE_COLUMNS`) are re-nested on the way out so the API
row and the Kalshi row are the same shape. Nulls are preserved (a period with
no trades carries `price.previous_dollars` only).

### Error bodies (new messages)

- Over ceiling: `"the request matches 132,552 rows, over the 75,000 row limit;
  narrow start/end"` — both numbers from the count and the live setting, never
  literals.
- Bad status: `"Invalid status values: open. Valid: active, amended, closed,
  determined, finalized, inactive, initialized"`.

## Data Flow

```
client ── GET /kalshi/markets/{t}/trades?start&end ──▶ route (kalshi_timeseries.py)
   validate window (422) ──▶ run_in_executor:
        pool.connection():
           serve_timeseries.market_context(conn, t)      # seek + category + state rows  → None ⇒ 404
           serve_timeseries.count_trades(conn, t, lo, hi) # > ceiling ⇒ 422 (raised after checkout ends)
           serve_timeseries.fetch_trades(conn, t, lo, hi)
        ⟵ dataclasses
   models.kalshi.TradesResponse(...)  ──▶ serialization.timeseries_response(model, fmt) ──▶ 200
```

Catalog routes are the same shape minus the window: seek parent (404) → count
(422) → fetch → model → JSON. `GET /categories` is the one exception: it has no
parent to seek and no count to guard, so it is a single aggregate → model →
JSON.

## Testing Strategy

- **Unit** (`test/unit/api_server/`): `TestClient` against `create_app()` with
  the reader functions monkeypatched (the `test_symbols.py` pattern: sentinel
  pool on `app.state`). One test per contract line above: 404 vs 200-empty,
  every 422, D5 facts in all four zero-count situations, Decimal-as-string,
  msgpack media type, naive-datetime-as-UTC, bare-date expansion.
- **Integration** (`test/integration/test_kalshi_serving.py`, `kalshi_db`
  fixture): readers against real tables — the count guard at the boundary
  (ceiling, ceiling + 1), `tape_filtered` through the real `trades_filter_sql`
  join, `effective_tape_floor` with and without a historical row, the nested
  candle re-assembly against the recorded `candlesticks.json` fixture written
  through `CandleRepository` (the fixture data is the real served shape — a
  parser test that only passes on invented rows is the trap the project rules
  name).
- **Load** (D11).
- **Artifact**: `test_openapi_artifact.py` fails until `openapi.json` is
  regenerated.

## Success Criteria

1. Nine routes under `/api/v1/kalshi/` appear in `docs/api/openapi.json`; the
   drift test passes; the equities routes' schema entries are byte-identical to
   before.
2. Every scoped list and both time-series routes answer `422` above the
   configured ceiling with the actual row count in the message, and issue no
   row fetch when rejecting (asserted in the load tier).
3. `404` only for an unknown ticker; a known market with an empty window is
   `200, count: 0` with D5 facts populated.
4. `tape_filtered` agrees with `mt data kalshi status --json`'s
   `filter.excluded_categories` for a market in an excluded category, and
   `coverage_from` on trades equals that command's `coverage_from`.
5. `complete_through` on candles equals the market's
   `market_candle_state.watermark_ts` and is `null` with `collected: false`
   when no state row exists.
6. Decimal fields are strings on both json and msgpack; timestamps are UTC.
7. `MT_RUN_LOAD_TESTS=1 uv run pytest test/load/test_188_kalshi_api_nfr.py`
   passes with bounds recorded from Phase 6 measurements.
8. `bars.py` uses the extracted serialization helper and its unit tests are
   unchanged.
9. README, `app.py` description, and the two 180 documents updated per D12.

## Verification Walkthrough

Against production (read-only), `mt serve` on the host, `$API` =
`http://localhost:8100/api/v1/kalshi`. Tickers below were chosen from the
measurements; substitute any current ones.

**1 — Catalog hierarchy, top down.**
```sh
curl -s "$API/categories" | jq '.count, [.categories[] | select(.category=="Politics")]'
curl -s "$API/series?category=Politics&search=KXFED" | jq '.count, .series[0].ticker'
curl -s "$API/series/KXFEDDECISION" | jq '.title, .category'
curl -s "$API/series/KXFEDDECISION/events?strike_from=2026-09-01" | jq '.count, .events[0].event_ticker'
curl -s "$API/events/KXFEDDECISION-26SEP/markets" | jq '.count, [.markets[] | {ticker, status}]'
```
Expect 20 categories with `Politics` carrying ~2,307 series, then a handful of
rows at each level below; the markets list shows the H0/H25/… ladder. Step one
is the discovery path: every value `category=` accepts came from the first
call.

**2 — Settlement on the market resource.**
```sh
curl -s "$API/markets/KXGOVTSHUTDOWN-26FEB14" | jq '.status, .settlement'
```
Expect `"finalized"` and a populated `settlement.result` / `settlement_ts`.

**3 — The cap, from stored data.**
```sh
curl -s -o /dev/null -w '%{http_code}\n' "$API/markets/KXMAYORLA-26-SPRA/trades"
curl -s "$API/markets/KXMAYORLA-26-SPRA/trades" | jq .error
```
Expect `422` and a message quoting **132,552** rows against the 75,000 limit.
Then a window that fits:
```sh
curl -s "$API/markets/KXMAYORLA-26-SPRA/trades?start=2026-06-01&end=2026-06-08" | jq '.count, .tape_complete_through, .coverage_from'
```
Expect a `200`, a count in the thousands, `coverage_from` = `2026-01-01T00:00:00Z`.

**4 — Whole-market candles under the ceiling.**
```sh
curl -s "$API/markets/KXMAYORLA-26-SPRA/candlesticks" | jq '.count, .collected, .complete_through'
```
Expect `68663` (or the current figure), `true`, and a `complete_through` just
after the market's close.

**5 — The four meanings of zero.**
```sh
# window past the tape watermark
curl -s "$API/markets/KXFEDDECISION-26SEP-H0/trades?start=2030-01-01" | jq '.count, .tape_complete_through'
# a Crypto market (tape-filtered on prod)
curl -s "$API/markets/$(curl -s "$API/series/KXBTC15M/events?strike_from=$(date -u +%F)" | jq -r '.events[0].event_ticker')/…"  # pick one market ticker from its markets list, then:
curl -s "$API/markets/<that-ticker>/trades" | jq '.count, .tape_filtered'
# a market outside the candle rule
curl -s "$API/markets/<same-ticker>/candlesticks" | jq '.count, .collected'
```
Expect `0` with a watermark before the window; `0, true`; and either candles
(Crypto candles are collected) or `0, false` for a market outside the rule.

**6 — 404 versus empty.**
```sh
curl -s -w '%{http_code}\n' "$API/markets/NOSUCH-TICKER/trades"
curl -s -w '%{http_code}\n' "$API/series/NOSUCH"
```
Expect `404` with `{"error": "Market 'NOSUCH-TICKER' not found"}` and likewise
for the series.

**7 — msgpack and Decimal exactness.**
```sh
curl -s "$API/markets/KXGOVTSHUTDOWN-26FEB14/trades?start=2026-02-14&format=msgpack" -o /tmp/t.msgpack -w '%{content_type}\n'
uv run python -c "import msgpack;d=msgpack.unpackb(open('/tmp/t.msgpack','rb').read());print(d['count'], d['trades'][0]['yes_price_dollars'])"
```
Expect `application/x-msgpack` and a price like `0.9900` as a string.

**8 — Bad input.**
```sh
curl -s "$API/events/KXFEDDECISION-26SEP/markets?status=open" | jq .error
curl -s "$API/markets/KXGOVTSHUTDOWN-26FEB14/trades?start=2026-03-01&end=2026-02-01" | jq .error
```
Expect the valid-status list and the reversed-range message.

**9 — Schema and tests.**
```sh
uv run python scripts/dump_openapi.py --check
uv run pytest test/unit/api_server test/integration/test_kalshi_serving.py
MT_RUN_LOAD_TESTS=1 uv run pytest test/load/test_188_kalshi_api_nfr.py
```

## Risks

- **Ceiling-sized candle responses are wider than bars.** Sixteen numeric
  strings per row; a 75,000-row candle response may approach 20 MB. Mitigated
  by measurement at Phase 6 (D8); if it is unreasonable, the fix is a
  Kalshi-specific ceiling as a *separate* decision, not a silent change to the
  shared one.
- **Count-then-fetch is two statements.** A pathological market whose count
  sits at the ceiling pays ~100 ms twice. Accepted: it is the cost of an exact
  guard on data with no density model, and it is measured, not guessed.

## Implementation Notes

Suggested order: (1) `serialization.py` extraction with bars tests green;
(2) `serve_catalog.py` + integration tests; (3) catalog routes + models + unit
tests; (4) `effective_tape_floor` promotion, `serve_timeseries.py` +
integration tests; (5) time-series routes; (6) load tier and its fixture;
(7) README / description / `openapi.json` / 180 documents. Commit at each
section boundary.

`kalshi_helpers.apply_kalshi_track` moves to `test/kalshi_support/` in step
(6); the integration conftest import path changes with it.
