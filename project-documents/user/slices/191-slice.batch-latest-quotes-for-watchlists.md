---
docType: slice-design
slice: batch-latest-quotes-for-watchlists
project: trading-data
parent: user/architecture/180-slices.data-serving-api.md
dependencies: [190]
interfaces: []
dateCreated: 20261118
dateUpdated: 20261118
status: not_started
---

# Slice Design: batch-latest-quotes-for-watchlists

## Overview

mt-ui is building a watchlist: a list of symbols, each showing a last price and the day's change. The API has no call for that. A client has to make one `/api/v1/bars` request per symbol and work out the change itself, so 50 symbols means 50 requests.

This slice adds `GET /api/v1/quotes?symbols=AAPL,SPY,...`. One call returns one row per symbol with the last close, the previous close, the change, the volume and the session date. Each row also carries a status, so one unknown symbol never fails the whole call.

There is no realtime data today. Every row is the last settled daily close, and the row says so. The PM's rule (2026-11-18): when realtime data exists we send it. When a caller asks for realtime and we don't have it, we say so and still send the best price we hold.

trading-ui also asked for an expected session date, so each market's watchlist can show which session is current and mark rows behind it. That is D8.

Tracked as [issue #80](https://github.com/manta-digital/trading-data/issues/80).

## Value

- **mt-ui**: one request fills the watchlist. No per-symbol fan-out, no client-side change arithmetic, no guessing whether a price is live.
- **trading-engine and any later client**: the same call answers "what did these symbols last close at?", which today is also a per-symbol bars request.
- **Later realtime work**: the response already says what kind of price each row is, so adding live prices later adds a value to a field instead of changing the shape.

## Technical Scope

Included:

- `GET /api/v1/quotes` with a `symbols` list and a `realtime` flag (shape under API Contracts).
- Stocks, ETFs and indexes, which are everything in `instruments` today.
- Last two daily bars per symbol from `daily_ohlcv`, read in one statement for the whole list.
- `expected_session_date`: the latest NYSE session whose close should be stored by now (D8), read from `trading_sessions` with one bounded query.
- A previous close adjusted for any split between the two sessions, so a split day doesn't show as a 75% crash.
- A per-row status: served, realtime not available, unknown symbol, no recent close.
- A published 200 response schema in `docs/api/openapi.json`, and entries in `docs/api/reference.md` and `docs/api/agents.md` that pass `scripts/check_api_docs.py --check`.
- One load test for a 100-symbol request.

Excluded:

- Futures quotes. Futures prices come from held tick ranges (ES and GC through 2024), so a "latest" futures price is years old. Futures join when there is a current futures price to serve.
- Kalshi quotes. The Kalshi market record's last-price fields have not been checked yet. A later step.
- Realtime prices. None exist. This slice only adds the request flag and the honest answer.
- Intraday prices from minute bars. On production, minute bars land on Saturdays only, so they are almost always older than the daily close. When they aren't, the gain is a few hours on a Saturday. Not worth a second read path.
- MessagePack output. A 100-row quote response is a few kilobytes of JSON.
- An environment override for the symbol cap. One constant. An override can be added when someone needs one.

## Dependencies

### Prerequisites

- Slice 190 (API reference documentation): the human and agent references and the doc gate this slice extends. Complete.
- `daily_ohlcv`, `instruments` and `splits` on the market database. All exist; no migration.
- `trading_sessions` for the NYSE calendar (D8). Complete through 2028, but 2027 market holidays are wrong until slice 174 ships (#26); see D8.

### Interfaces Required

- The API connection pool (`deps.get_db_pool`) and the app-level handlers: a statement timeout becomes a `504`, any other database error a sanitized `500`.
- `manta_trading.data.adjustment.k_factor.Split` and its price multiplier (`ratio_from / ratio_to`), so the split arithmetic lives in one place.

## Architecture

### Component Structure

| Piece | Where | What it does |
|---|---|---|
| Route | `api_server/routes/quotes.py` | Parses and admits the request, runs the read in the executor, returns the response. |
| Read | `api_server/quotes_read.py` | Three SQL statements and one pure function that builds the rows. No FastAPI imports. |
| Models | `api_server/models/quotes.py` | `Quote` and `QuotesResponse` (Pydantic). |
| Constants | `constants.py` | `QuoteStatus`, `QuoteBasis`, `API_MAX_QUOTE_SYMBOLS`, `QUOTE_LOOKBACK_DAYS`. |

The route is registered in `app.py` beside the others.

### Data Flow

1. Parse `symbols`: split on commas, trim spaces, drop empties, drop duplicates while keeping the caller's order. Reject an empty list or more than `API_MAX_QUOTE_SYMBOLS` with a `422`, before any database work.
2. In one executor call with one pooled connection:
   - **Last two closes**: one statement over `daily_ohlcv` for the whole list, bounded to the last `QUOTE_LOOKBACK_DAYS` days.
   - **Splits**: one statement over `splits` for the same symbols and window. Skipped if no symbol has two bars.
   - **Known symbols**: one statement over `instruments`, only for symbols that came back with no bars. It tells "unknown symbol" from "no recent close".
3. The pure builder turns those results into one `Quote` per requested symbol, in request order.
4. One bounded read of `trading_sessions` for the expected session (D8), on the same connection.
5. The route returns `QuotesResponse` as JSON with a `200`.

### State Management

None. Every request reads the database. Nothing is cached.

## Technical Decisions

### D1 — One read over daily bars, bounded by a time window
Status: decided

The last two bars per symbol come from one statement:

```sql
SELECT symbol, time, close, volume
FROM (
    SELECT symbol, time, close, volume,
           row_number() OVER (PARTITION BY symbol ORDER BY time DESC) AS rn
    FROM daily_ohlcv
    WHERE symbol = ANY(%s) AND time > %s
) t
WHERE rn <= 2
```

The time bound is bound as an aware UTC `datetime`, never a `date`. A `date` bound makes the planner consider every chunk of `daily_ohlcv`, which is the multi-second planning cost slice 187 measured and removed (see the `_SYMBOL_HEAD_SQL` notes in `api_server/queries.py`).

Measured on production 2026-11-18 with 100 real symbols and a 31-day bound: 3.4 ms planning, 2.6 ms execution, 200 rows.

`QUOTE_LOOKBACK_DAYS` is 31. That covers every holiday cluster with room to spare. A symbol with no bar in 31 days is delisted or halted; it gets the "no recent close" status rather than a price that could be months old.

Rejected: a per-symbol `ORDER BY time DESC LIMIT 2` loop (100 round trips), and calling `TimescaleDailyDataDB.get_daily_data` per symbol (100 statements plus adjustment work per symbol).

### D2 — The close is the real traded price; the previous close is split-adjusted only
Status: decided

`close` is the latest session's close as held. No future corporate action exists for the newest bar, so this is the price a trader would see.

`previous_close` is put on the same share basis as `close`. If a split's ex-date falls after the previous session and on or before the latest session, the previous close is multiplied by that split's factor (`ratio_from / ratio_to`, taken from `k_factor.Split`, not rewritten). Dividends are not applied. A stock going ex-dividend really does open lower, and every quote screen shows that drop as part of the day's change.

`change = close - previous_close` and `change_pct = change / previous_close * 100`, rounded to 4 decimal places. Both are `null` when there is no previous close in the window, for example a symbol's first trading day.

Rejected: running the full adjustment (`data.adjustment.adjusted`) over the two bars. It applies dividends as well, so an ex-dividend day would show a smaller change than the screen a trader compares against. It also costs one adjustment read per symbol.

### D3 — A per-row status, and a 200 for every valid request
Status: decided

Each row has a `status` (`QuoteStatus`):

| Value | Meaning | Price fields |
|---|---|---|
| `ok` | Served, and the caller did not ask for realtime. | filled |
| `realtime_unavailable` | The caller asked for realtime. We have none, so this is the best price held. | filled |
| `unknown_symbol` | Not in `instruments`. | `null` |
| `no_recent_close` | Known symbol, no daily bar in the last 31 days. | `null` |

The HTTP status is `200` whenever the request itself is valid, even when every row is an error. This is how the PM's "send a status code, and still send the best data" lands. A non-2xx status carrying data is something most HTTP clients treat as a failure and throw away, so the signal goes on the row, where the price is.

`422` is only for a bad request: no symbols, or more than 100.

Rejected: a `206` or `203` when realtime was asked for. Clients handle those inconsistently, and the condition belongs to each row, not the whole call. Once some symbols have realtime and others don't, one top-level status can't describe that.

### D4 — Say what kind of price each row is
Status: decided

Each filled row carries `basis` (`QuoteBasis`). Today the only value is `daily_close`. When realtime data exists, a `realtime` value is added and rows that have it use it. The shape does not change, and a client reading `basis` already knows how to tell the two apart.

`session_date` is the trading day the close belongs to, so the client can show "as of Nov 16" without guessing.

The top-level `realtime_requested` echoes the flag, so a logged response is readable on its own.

### D5 — Symbols are a comma list, matched as given, capped at 100
Status: decided

`symbols=AAPL,SPY,QQQ`. Repeated parameters (`symbols=AAPL&symbols=SPY`) are not accepted; one spelling keeps the docs and the gate simple. Whitespace around each symbol is trimmed. Case is not changed: `aapl` is an unknown symbol, the same as on `/api/v1/bars`. Duplicates are collapsed to the first occurrence, and rows come back in the order requested.

100 symbols is about 600 characters of query string, far under any URL limit, and 200 rows of database work (6 ms measured). `API_MAX_QUOTE_SYMBOLS = 100` is one constant, used by the check and by the `422` message.

### D6 — Publish the 200 schema
Status: decided

The route declares `response_model=QuotesResponse` and returns the model directly rather than a raw `Response`. The bars and Kalshi time-series routes return a raw `Response`, and their 200 body is missing from the published schema. A UI generating types from `openapi.json` needs the schema, and this route has no MessagePack path that would need a raw `Response`.

### D7 — No freshness probe
Status: decided

`daily_ohlcv` is a raw hypertable, so there is no materialized view to fall behind, and `/api/v1/bars` already serves `1d` with no probe. Each row's `session_date` is the freshness answer. A client that wants the system-wide verdict already has `/api/v1/status`.

### D8 — Expected session date
Status: decided

`expected_session_date` is one top-level `date`, never null: the latest NYSE session whose daily close the store should hold at the moment the response is built. Stocks and ETFs are the only instruments served, and they share the NYSE calendar. NYSE and NASDAQ sessions are identical for every date through 2028, so one calendar is correct. If a later slice adds a market with its own calendar, the field becomes per-row or per-calendar then; it does not change now.

Rule: the latest `session_date` in `trading_sessions` where `calendar_id = 'NYSE'` and `session_close_utc <= now - DAILY_CLOSE_PUBLICATION_ALLOWANCE`. The read binds the cutoff as an aware `timestamptz`, the planning-cost lesson from slice 187, and uses `idx_trading_sessions_close`.

`DAILY_CLOSE_PUBLICATION_ALLOWANCE` is 9 hours, one constant in `constants.py`, beside `MINUTE_PROVIDER_PUBLICATION_LAG`. Evidence, production read-only, 2026-11-19, twelve sessions from 2026-11-02 to 2026-11-17 (dates in UTC, not the session timezone, which is `America/Denver` and shifts a `::date` by a day):

- The daily pass at 00:35 UTC the day after a session writes that session's rows. First row 3.6 hours after a 21:00 UTC close, every session checked, with about 11,400 rows each.
- The calendar's early closes are at 18:00 UTC, so the same pass lands 6.6 hours after close. Rows are complete by about 01:35 UTC, which is 7.6 hours after an 18:00 close.
- A 9-hour allowance flips at 03:00 UTC after an early close (about 1.4 hours after the rows are complete) and at 06:00 UTC after a normal close.

The allowance is what makes an unpublished session read as behind instead of current. A short allowance would mark every row behind for hours each evening; a long one delays the signal about a stalled pass. A unit test guards the relation: for every NYSE session in the calendar, `close + DAILY_CLOSE_PUBLICATION_ALLOWANCE` falls at or after 01:35 UTC on the next calendar day.

Failure is explicit. If no NYSE session closed before the cutoff within 14 days, the route answers `503` with `error` naming the calendar and `mt data extend`. It never returns a guessed date, and it never returns `null`.

Known gap: until slice 174 ships (migration 067 and the 2027 holiday fix, #26), 2027 market holidays are missing from `trading_sessions` and count as sessions. For each 2027 holiday the expected session lands on the holiday, so rows read as behind until the next session closes. This gap is recorded in the release notes. The PM decides whether 191 ships before or after 174.

### Technology Choices

FastAPI, Pydantic and psycopg, as the rest of the API. Nothing new.

### Patterns and Conventions

- SQL lives in `quotes_read.py`, as `queries.py` and `futures_read.py` do. The route stays thin.
- The builder is a pure function over plain tuples, so every status case is a unit test with no database.
- Status and basis values are `StrEnum` members in `constants.py`. Nothing compares against a string literal.
- No `try/except` in the route or the read. Database errors go to the app-level handlers.

## Implementation Details

### API Contracts

`GET /api/v1/quotes`

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `symbols` | string, required | — | Comma-separated, 1 to 100 after trimming and de-duplication. |
| `realtime` | bool | `false` | Ask for realtime prices. Rows we can't serve live are marked `realtime_unavailable` and still carry the best price held. |

`200`:

```json
{
  "realtime_requested": false,
  "expected_session_date": "2026-11-17",
  "count": 3,
  "quotes": [
    {
      "symbol": "AAPL",
      "status": "ok",
      "basis": "daily_close",
      "session_date": "2026-11-16",
      "close": 228.41,
      "previous_close": 226.9,
      "change": 1.51,
      "change_pct": 0.6655,
      "volume": 41233118
    },
    {
      "symbol": "NOPE",
      "status": "unknown_symbol",
      "basis": null,
      "session_date": null,
      "close": null,
      "previous_close": null,
      "change": null,
      "change_pct": null,
      "volume": null
    },
    {
      "symbol": "XYZ",
      "status": "no_recent_close",
      "basis": null,
      "session_date": null,
      "close": null,
      "previous_close": null,
      "change": null,
      "change_pct": null,
      "volume": null
    }
  ]
}
```

Prices are illustrative, not real values.

`expected_session_date` is the D8 value: the latest NYSE session whose close should be stored by now. A row whose `session_date` is earlier than it is behind.

`count` is the number of rows, which equals the number of distinct symbols requested.

`422` (`{"error": "..."}` through the existing handler): no symbols after trimming; more than 100 distinct symbols. The over-cap message names the cap and the count received.

`503`: no NYSE session closed before the cutoff within 14 days (D8). The body names `mt data extend`.

`504`: statement timeout, through the existing handler.

## Integration Points

### Provides to Other Slices

- A quote surface that realtime work extends by adding a `QuoteBasis` value and a second source. No shape change.
- Futures and Kalshi quotes can join later by widening which symbols resolve. The status enum already covers "unknown" for anything not served yet.

### Consumes from Other Slices

- Slice 190's doc gate: the new route must be marked up in both references or `check_api_docs.py --check` fails.
- The 186 handlers: a `422` body is `{"error": ...}`, a statement timeout is a `504`.
- Slice 174 (#26): the NYSE calendar's 2027 holidays. Until it ships, D8 has a known gap on those dates.

## Success Criteria

### Functional Requirements

- A request for known, active symbols returns one `ok` row each, in request order, with close, previous close, change, change percent, volume and session date matching `daily_ohlcv`.
- `realtime=true` returns the same prices with status `realtime_unavailable` and `realtime_requested: true`.
- An unknown symbol is an `unknown_symbol` row inside a `200`. A known symbol with no bar in 31 days is a `no_recent_close` row.
- A symbol with a split between its last two sessions shows a previous close on the post-split basis.
- A symbol with only one bar in the window has `previous_close`, `change` and `change_pct` of `null` and status `ok`.
- `expected_session_date` is the latest NYSE session closed at least `DAILY_CLOSE_PUBLICATION_ALLOWANCE` before the request time, on every response, whatever the symbols.
- A row older than `expected_session_date` is behind; the route does not decide that, the client does.
- An empty calendar window is a `503` naming `mt data extend`, never a guessed date.
- Zero symbols, or 101 distinct symbols, is a `422` before any database work. 101 entries that collapse to 100 distinct symbols is accepted.

### Technical Requirements

- Unit tests: symbol parsing (trimming, empties, duplicates, order, both `422` cases); the builder for every status, the split case, the one-bar case and the `realtime` flag; the time bound bound as an aware `datetime`.
- Integration test against the test cluster: seeded `daily_ohlcv`, `instruments` and `splits` rows produce the expected rows end to end through `TestClient`.
- Load test in `test/load/`: a 100-symbol request against the test database stays under a latency bound set from a measured run.
- ruff format and check clean on touched files; no mypy error not already on main.

### Integration Requirements

- `docs/api/openapi.json` regenerated and committed, with the `QuotesResponse` 200 schema present.
- `docs/api/reference.md` and `docs/api/agents.md` describe the route, every status and the realtime rule. `uv run python scripts/check_api_docs.py --check` exits 0.

### Verification Walkthrough

On a dev checkout against the test database:

```bash
uv run pytest test/unit/api_server -k quotes -q
uv run pytest test/integration -k quotes -q
uv run python scripts/check_api_docs.py --check
```

All pass.

On production after release, with `mt-serve` running:

```bash
curl -s 'http://localhost:8100/api/v1/quotes?symbols=AAPL,SPY,QQQ,NOPE' | jq
```

Expect three `ok` rows with `session_date` equal to the latest daily session and one `unknown_symbol` row, HTTP 200.

```bash
curl -s 'http://localhost:8100/api/v1/quotes?symbols=AAPL,SPY&realtime=true' | jq '.quotes[].status'
```

Expect `"realtime_unavailable"` twice, with the same prices as the first call.

```bash
curl -s -o /dev/null -w '%{http_code} %{time_total}\n' \
  "http://localhost:8100/api/v1/quotes?symbols=$(psql "$MT_TIMESCALE_DB_URL" -XAtc \
  "SELECT string_agg(symbol, ',') FROM (SELECT symbol FROM instruments ORDER BY symbol LIMIT 100) s")"
```

Expect `200`, well under a second.

```bash
curl -s -o /dev/null -w '%{http_code}\n' "http://localhost:8100/api/v1/quotes?symbols="
```

Expect `422`.

```bash
curl -s 'http://localhost:8100/api/v1/quotes?symbols=SPY' | jq '.expected_session_date'
```

Expect the latest NYSE session whose close is at least nine hours past. Before 06:00 UTC on a normal day that is the session before the last close.

## Implementation Notes

### Development Approach

1. Constants and models.
2. `quotes_read.py`: the pure builder and its unit tests first, then the three statements.
3. Route, registration, unit tests through `TestClient` with the read mocked.
4. Integration test on the test cluster.
5. Load test.
6. Regenerate `openapi.json`, write both reference entries, run the doc gate.

### Special Considerations

- Keep the time bound a `timestamptz`. The unit test that pins it is the guard against a multi-second planning regression.
- The route is async and the read is synchronous psycopg, so the read runs in the executor. Parsing 100 symbols in the route is microseconds and stays on the loop.
- Tell mt-ui when the route is released, with the shape and a pointer to the reference section.
