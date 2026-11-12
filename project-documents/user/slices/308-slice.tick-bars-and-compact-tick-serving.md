---
docType: slice-design
slice: tick-bars-and-compact-tick-serving
project: trading-data
parent: user/architecture/300-slices.futures-historical-bars-statistics-and-tick-aggregation.md
dependencies:
- 306
interfaces: []
dateCreated: 20261108
dateUpdated: 20261108
status: not_started
turns: 27
promptTokens: 2148565
cachedTokens: 2036166
cacheWriteTokens: 112347
completionTokens: 31168
durationSeconds: 345.7
costUsd: 1.929915
usageRounds: 1
pricedRounds: 1
usageByModel:
  claude-opus-5-5:
    rounds: 1
    costUsd: 1.9299148000000002
    completionTokens: 31168
---

# Slice Design: Tick Bars and Compact Tick Serving

## Overview

After 306, time bars come only from provider `ohlcv-1m`, and ticks are served only as raw rows: about 200 bytes per tick, 14 columns, with the session, symbol and instrument repeated on every row. Ticks are held for two things bars cannot give (journal 20261101 ADR): bars of N trades at any N, and event-level views. This slice serves both.

- **One rollup module.** `data/tick/tick_bars.py` takes one trade at a time and returns closed bars plus the current partial bar. One tick is one trade record. The count restarts at each session open. The last bar of a session is kept and marked partial. History feeds it stored trades in a loop. There is no SQL version.
- **A tick-bar route** under `/api/v1/futures/{contracts/{symbol} | products/{product}}/tick-bars` with `ticks_per_bar` = any positive integer.
- **Compact raw ticks.** The two `/ticks` routes switch to a columnar encoding: one block per session window, with the session, symbol and instrument once per block, timestamps as integer nanosecond deltas, integer prices, and the stream key (`ts_event`, `sequence`, `sequence_ordinal`) on every tick.
- **Admission.** A tick-bar request is admitted on the trades it must scan, which the ingest ledger gives exactly before anything is read. The bars it returns are bounded while cutting, as tick rows are today (D5).

trading-ui's requirements (issue #62) are met as accepted there. The human and agent docs and the OpenAPI file are updated.

## Value

- **trading-ui:** futures charts at any N, with the open and close time, a unique ascending identity and the partial flag that position-spaced charts need. Time-and-sales, trade markers and bid/ask lines come from the compact ticks, at about a tenth of today's bytes gzipped (ADR: 18.5 B/tick gzipped rows against 9.5 B/tick gzipped columnar).
- **trading-engine:** the same bars the chart shows, cut by one implementation. Buy and sell volume by aggressor side, which time bars cannot carry.
- **Architectural:** the rollup module is pure and incremental, so the live stream (280) and a later per-client engine run the same code, and the compact encoding and stream key are the shape 280's messages reuse.

## Technical Scope

**Included**

- `data/tick/tick_bars.py`: the rollup module (D1, D2). Standard library only.
- `data/tick/tick_bar_reads.py`: feeds each planned window's trades into the module and hands bars on (D3).
- Tick-bar routes, contract and product, with `ticks_per_bar`, `start`, `end`, `from`, `until`, `rule`, `format` (D4).
- Admission on trades scanned, a new operator setting `MT_API_MAX_TICK_BAR_TRADES` (D5).
- The columnar tick response replacing today's row response on both `/ticks` routes (D6, D7).
- `docs/api/reference.md` §6, `docs/api/agents.md`, `docs/api/openapi.json`, the CHANGELOG.

**Excluded**

- Live streaming and any live cutting (280). The module's partial-bar output is built and tested here, and history uses it only for a session's last bar.
- A CLI form of tick bars. `mt data tick get ticks` keeps its row output (D8).
- Time bars from ticks: the ADR forbids them. 306's parity check is the only tick-derived time bar.
- Volume, range or dollar bars. The ADR names trade-count bars only.
- `mbp-1`/`bbo` quotes between trades: not bought (PM, 2026-11-01). Bid/ask is the quote at each trade.
- Any purchase, migration or new table.
- The rename of the time-bar size names off "tick" (issue #72). The slice plan entry does not assign it and it changes a published OpenAPI component, so it ships on its own as a `refactor:` commit under that issue.

## Dependencies

### Prerequisites

- **306 (merged before Phase 6 starts):** the bars routes read provider bars only, `get_plan`'s plans take a `family` and `verdicts_for(family)` exists (306 D3), and `source` is on time-bar responses.
- **302/300:** `tick_trade` and the tick ledger, `SchemaFamily.TICK_TIER`, `status_reads.ledger_totals` (record count per instrument and session over current units).
- **301/221:** the CME sessions that define each window's open and close.

### Interfaces Required

- `get_plan.contract_plan` / `product_plan` with `family=TICK_TIER`, and `GetPlan.segments` (`TickWindow`s, one per session and contract).
- `futures_read.read_series`, `clip`, `RowCeilingExceeded`; `tick_access.tick_errors`; `serialization.send_timeseries` / `timeseries_200`; `admission.over_ceiling`.
- `tick_reads._stream` (the named-cursor batch loop), already the public `tick_reads.stream_rows`, with its caller `read_ticks` updated. The named-cursor loop is extracted once more into `tick_reads.iter_batches`, which `stream_rows` and `read_tick_bars` both consume, because `read_tick_bars` must await a disconnect check before each fetch and push each batch off the loop. `TICK_GET_FETCH_ROWS` (10,000).
- `tick_format.price_text`, `ts_text`.
- `UNDEF_INT64`, moved from `data/tick/constants.py` to `data/tick/dbn_values.py` (D1).
- `test/fixtures/databento/real/glbx-mdp3-20241203.tbbo.dbn.zst` and its derived `trades` twin.

## Architecture

### Component Structure

```
data/tick/tick_bars.py           NEW  Trade, TickBar, TickBarCutter: the one rule (D1, D2)
data/tick/tick_bar_reads.py      NEW  read_tick_bars: window trades → cutter → bars (D3)
data/tick/dbn_values.py         NEW  UNDEF_INT64 (moved), TRADE_SIDE_BUY/SELL: standard library only (D1)
data/tick/constants.py                imports UNDEF_INT64 from dbn_values
api_server/futures_read.py            read_series gains an `admit` hook (D5)
api_server/routes/futures_tick_bars.py NEW  the two tick-bar routes (D4)
api_server/routes/futures_series.py   /ticks build the columnar model (D6)
api_server/routes/futures_params.py   TicksPerBarParam
api_server/models/futures_ticks.py    NEW  columnar tick models + pivot (D6)
api_server/models/futures_tick_bars.py NEW  FuturesTickBarRecord, FuturesTickBarsResponse (D7)
api_server/models/futures.py          `_envelope` made public as `plan_envelope`; TickRecord removed
config/__init__.py, constants.py      MT_API_MAX_TICK_BAR_TRADES (D5)
```

`futures.py` is at 342 lines, so the new models get their own files.

### Data Flow

**Tick bars:**

```
GET …/tick-bars?ticks_per_bar=N&start&end[&from&until][&rule]
  → plan(family=TICK_TIER): windows, segments, unresolved, incomplete      ┐ one REPEATABLE READ
  → admit: Σ ledger_totals over windows overlapping [from, until) ≤ trade limit │ READ ONLY snapshot
  → per overlapping window, read from the session open (never clipped):     │
      SELECT trade columns FROM tick_trade WHERE instrument_id, ts_event ∈ [open, close]
      ORDER BY ts_event, sequence, sequence_ordinal                        │
      → TickBarCutter(N).push(trade) → closed bars; close_session() → last bar, partial
      → keep bars with open_time ∈ [from, until) → collector (bar ceiling)  ┘
  → FuturesTickBarsResponse → send_timeseries (JSON | msgpack, gzip)
```

A bar's content and index never depend on `from`/`until`: cutting always starts at the session open and a bar opened before `until` is completed with trades after it. A window's scan stops at the first trade that would open a bar at or after `until`.

**Raw ticks:**

```
GET …/ticks (unchanged parameters)
  → read_series(read_ticks, clip(bounds)) as today → rows in window order
  → TickBlocks.from_rows: consecutive rows of one (session, instrument) → one block,
      columns pivoted, ts_event delta-encoded from the block's ts_base
  → FuturesTicksResponse (columnar) → send_timeseries
```

### State Management

None. No table, column or migration. Each request is a pure function of the stored projection, read in one snapshot. The cutter's state lives only for the duration of one window's read.

## Technical Decisions

### D1 — The rollup module: one trade in, bars out
Status: decided

`data/tick/tick_bars.py` holds the whole rule. It imports only the standard library and `data/tick/dbn_values.py`, so trading-engine or 280's live process can run it without the database stack.

`dbn_values.py` is a new standard-library-only module holding the two DBN values the cutter needs: `UNDEF_INT64` (moved from `data/tick/constants.py`, which imports it from there, so it is still defined once) and the aggressor side codes (D2). `data/tick/constants.py` cannot be the home, because it imports `manta_trading.constants` and the calendar seed modules. A test imports `tick_bars` in a fresh interpreter and asserts that neither `psycopg` nor `manta_trading.data.tick.constants` was loaded.

```python
class Trade(NamedTuple):          # field order = tick_bar_reads' SELECT order
    ts_event: int; sequence: int; sequence_ordinal: int
    session_date: date; price: int; size: int; side: str
    bid_px: int | None; ask_px: int | None

class TickBarCutter:
    def __init__(self, ticks_per_bar: int) -> None: ...      # ValueError unless ≥ 1
    def push(self, trade: Trade) -> list[TickBar]: ...       # 0, 1 or 2 bars
    def current(self) -> TickBar | None: ...                 # the open bar, partial=True; None if none open
    def close_session(self) -> TickBar | None: ...           # the open bar, final, partial=True; None if none open
```

- `push` returns the bar the Nth trade closes. When a trade's `session_date` differs from the open session's, the open bar is first emitted as that session's last bar (partial) and the count restarts at index 0. So the session rule lives in the module, not in its callers.
- A bar closes at its Nth trade, so no bar is open after it. `close_session()` then returns `None`, and a session whose trade count is a multiple of N ends with a full, non-partial bar (D2).
- `current` is for live: it shows the bar in progress without closing it. History never calls it.
- A trade whose key `(ts_event, sequence, sequence_ordinal)` is not greater than the previous trade's, or whose session goes backwards, raises `TickBarInputError` (a `ValueError` subclass defined in `tick_bars.py`) naming both keys (kept on the error as `keys`). Out-of-order input is a caller bug and is never reordered silently.
- A trade with an undefined price (`UNDEF_INT64`) raises `TickBarInputError`. `tick_trade.price` is `NOT NULL` but the column can hold the sentinel, so in history this means a bad stored row.
- **In the routes**, a `TickBarInputError` means the stored ticks break the cutter's contract. `read_tick_bars` wraps it in `TickBarWindowError` (defined in `tick_bar_reads.py`, carrying the window's session, symbol and instrument, with the original as `__cause__`), because the cutter does not know the window. The tick-bar route catches only that wrapper, logs it at ERROR with `logger.exception` (window session, symbol, instrument and the cause's keys), and answers 500 with the stable detail "stored ticks for session {date} of {symbol} cannot be cut into bars; see the server log". No bars are returned: the error aborts the snapshot read as any read failure does. An integration test inserts a sentinel-price trade into the fixture database and asserts the 500 and the ERROR log line.

Rejected:

- *A SQL implementation for history* (window functions over `row_number() / N`). The ADR forbids a second implementation, and identical bars for chart and engine depend on there being one.
- *A batch function over a session's trades.* Live needs one trade at a time. A batch function would be the second implementation the ADR rules out.
- *One cutter per instrument stream with an instrument check.* The reader already uses one cutter per window (one contract, one session). Live keeps one cutter per contract. The rule does not need the instrument.

### D2 — What a tick bar is
Status: decided

The ADR's rule, made exact:

| Field | Value |
|---|---|
| `session_date`, `index` | The session and the bar's 0-based position in it. Unique and strictly ascending in (session_date, index) across a response. |
| `open_time`, `close_time` | `ts_event` of the bar's first and last trade. Two bars may share a nanosecond; `index` still orders them. |
| `open`, `high`, `low`, `close` | First, max, min, last trade price, by key order. |
| `volume`, `trade_count` | Sum of sizes; number of trade records (= N unless partial). |
| `buy_volume`, `sell_volume` | Sizes of trades with aggressor side `B` and `A`. Side `N` counts in `volume` only, so `buy + sell ≤ volume`. |
| `bid_px`, `ask_px` | The last trade's bid and ask (`tbbo`'s quote at that trade). Null when that trade's record is `trades` tier. |
| `partial` | True only for a session's last bar holding fewer than N trades. A session whose trade count is a multiple of N has no partial bar. The architecture's "Tick bars and raw ticks" paragraph and the slice plan entry state this rule (amended with this design), so 280 starts from the same definition. `reference.md` states it. |

- One tick is one record. Records that share `(ts_event, sequence)` and differ in `sequence_ordinal` (about 2% of ES) are separate ticks.
- The side values `B`/`A` are defined once in `data/tick/dbn_values.py` (`TRADE_SIDE_BUY`, `TRADE_SIDE_SELL`) and read from there.
- Bid and ask sizes at close are not carried. The ADR names bid/ask at close, and no consumer asked for sizes on bars. They stay available per tick (D6).
- A session that is not load-complete is cut from what is held and listed in `incomplete`, as every futures read does. Its indices can change when the session is completed. `reference.md` says so.

### D3 — History runs the module in a loop
Status: decided

`tick_bar_reads.read_tick_bars(conn, window, ticks_per_bar, bounds, on_batch)` has the `ReadWindow` shape once `ticks_per_bar` and `bounds` are bound, so `read_series` drives it like `read_ticks`.

- One statement per window over the full session `[open_ns, close_ns]`, on the key prefix `(instrument_id, ts_event)`, through `tick_reads.stream_rows`. It selects only the trade columns in `Trade` order (the window's session date as a parameter), so each fetched row becomes a `Trade` with `Trade._make`.
- A fresh `TickBarCutter` per window. Every fetched batch is pushed through; bars whose `open_time` is in `[from, until)` go to `on_batch`. `close_session()` ends the window.
- Bars get `raw_symbol` and `instrument_id` from the window, as tick rows do.
- Windows that do not overlap `[from, until)` are neither scanned nor counted (`clip` selects them). Overlapping windows are scanned from their unclipped open.

### D4 — The tick-bar routes
Status: decided

`GET /api/v1/futures/contracts/{symbol}/tick-bars` and `GET /api/v1/futures/products/{product}/tick-bars`.

- `ticks_per_bar`: required integer, `≥ 1`, no upper bound. A value larger than a session's trade count gives that session one partial bar. FastAPI answers 422 for 0, negatives and non-integers.
- `start`, `end` (session dates, end exclusive), `from`, `until` (half-open instants, filtering by `open_time`), `rule` (product), `format`: the same parameter types the `/ticks` routes use.
- Plans with `family=TICK_TIER`, so the default range is held tick sessions and `incomplete` carries tick verdicts. Product reads take one contract per session from the same `build_schedule` as `/ticks`, `/bars` and `/schedule`; a roll falls on a session boundary, where the count restarts anyway.
- Errors map through `tick_errors` as on every futures route: 404 unknown product or contract, 422 bad rule or range, 503 tick database unavailable, 504 statement timeout. A cutter input error is a 500 (D1).
- No `source` field. 306's `source` says which provider bar schema a time bar came from. A tick bar has one possible source, the stored trades, and the route names it.

Rejected: *`ticks_per_bar` from a fixed menu.* The ADR makes any N non-negotiable.

### D5 — Admission: trades scanned, known before reading
Status: decided

The plan asks how tick bars are admitted when the bar count is not a function of the window. The answer is to admit on the work, which is a function of the window, and to bound the output while cutting.

- **Trades scanned (checked first).** `status_reads.ledger_totals` gives the record count of every (instrument, session) over current units. That is exactly the rows a window read returns, which `mt data tick coverage` proves. Before any tick is read, in the same snapshot, the route sums it over the windows it will scan (D3) and refuses with 422 when the sum is over `MT_API_MAX_TICK_BAR_TRADES`: "the request scans 2,431,907 trades, over the 2,000,000 trade limit; narrow start/end". The check is a hook on `read_series` (`admit: Callable[[Conn, GetPlan], Awaitable[None]] | None`), run after the plan and before the first window. The `/ticks` and `/bars` routes pass none.
- **Bars returned (while cutting).** The existing collector refuses past `MT_API_MAX_BARS_PER_REQUEST` (75,000), as tick rows are refused today (231 TD4), with the remedy "raise ticks_per_bar, or narrow start/end or from/until". A median ES session (about 393k trades) fits at N ≥ 6. Smaller N over a dense session pages with `from`/`until`, and the scan bound still counts whole sessions.
- **The default trade limit** is `API_MAX_TICK_BAR_TRADES = 2_000_000` in `constants.py`, read through settings like `MT_API_MAX_BARS_PER_REQUEST`. That is about five median ES sessions, or about 24 GC sessions. Phase 7 measures a request at the limit (D9). If it misses the bound, the default is lowered to a value that meets it, with the measurement recorded.

Rejected:

- *Counting scanned trades against the 75,000-row ceiling.* One ES session is five times that, so no session could be cut at any N.
- *A pre-count of bars as `Σ ceil(nᵢ / N)`.* It is exact without `from`/`until` and wrong with them, so the refusal message would change meaning with the parameters. The collector already bounds the output.

### D6 — Columnar raw ticks
Status: decided

Both `/ticks` routes return the columnar shape instead of row records. Parameters, plan, row ceiling, `from`/`until` paging and error mapping are unchanged. No consumer calls the futures routes yet (300 architecture, Current State), so the old shape is replaced, not versioned.

```json
{
  "target": {"…": "…"}, "start": "2024-12-03", "end": "2024-12-04",
  "from": null, "until": null, "definitions_as_of": "2024-12-03",
  "segments": [ … ], "unresolved": [], "incomplete": [], "count": 3,
  "price_scale": 9,
  "blocks": [{
    "session_date": "2024-12-03", "raw_symbol": "ESZ4", "instrument_id": 0, "count": 3,
    "ts_base": "1733180400000000000",
    "ts_event": [0, 1250, 0],
    "sequence": [0, 0, 0], "sequence_ordinal": [0, 0, 1],
    "price": [0, 0, 0], "size": [0, 0, 0], "side": "BAA",
    "bid_px": [0, 0, null], "ask_px": [0, 0, null], "bid_sz": [0, 0, null], "ask_sz": [0, 0, null]
  }]
}
```

(Values are shape only, zeroed.)

- **One block per non-empty window** (session × contract), in plan order. A product read that rolls has one block per session.
- **Timestamps.** `ts_base` is the block's first `ts_event`, as a decimal string, because epoch nanoseconds exceed 2⁵³ and would lose precision in JavaScript and in many msgpack decoders. `ts_event[i]` is the delta from tick `i − 1` (`ts_event[0]` is 0), so `ts_event` of tick `i` is `ts_base` plus the sum of `ts_event[0..i]`. Deltas are never negative (key order).
- **Prices** are DBN fixed-point integers, value × 10⁻⁹, stated once as `price_scale: 9`. ES and GC prices are below 2⁵³. An undefined price is `null`.
- **Side** is one string, one character per tick (`B`, `A`, `N`).
- **Stream key.** `ts_event`, `sequence` and `sequence_ordinal` are on every tick. 280's live messages carry the same key, so a client stitches history to live with no gap or duplicate. `sequence_ordinal` is added to the read's select list; today it is ordered on but not returned.
- **Bid/ask** columns hold `null` for a `trades`-tier tick. They are never dropped as a whole, so a block has one shape.
- **Dropped from the wire:** `bid_ct`, `ask_ct` (not a display field, issue #62, and trading-engine has not asked for them; they stay stored), and the per-row `session_date`, `raw_symbol`, `instrument_id` (now per block).

The pivot is `TickBlocks.from_rows` in `models/futures_ticks.py`, over the rows `read_ticks` already returns. The reader and `mt data tick get ticks` are unchanged apart from the extra column.

Rejected:

- *A second `encoding=` parameter keeping row records.* Two shapes of one resource, for no consumer.
- *Delta-encoding prices and sequences.* The ADR names timestamp deltas only, and gzip takes most of what price deltas would.
- *Arrow or Parquet.* Issue #44 is the bulk-export path. This is the interactive read, which must stay JSON- and msgpack-readable.

### D7 — Tick-bar records stay rows
Status: decided

Tick bars use row records like time bars: prices as decimal strings (`price_text`), times as ISO-8601 with nine fraction digits (`ts_text`). A response holds at most 75,000 bars, so a columnar form buys little, and the chart reads bars as records. `FuturesTickBarsResponse` carries `target`, `ticks_per_bar`, `start`, `end`, `from`, `until`, `definitions_as_of`, `segments`, `unresolved`, `incomplete`, `count` and `bars`.

### D8 — No CLI tick bars
Status: decided

`mt data tick get` mirrors the time-bar API (306 D8) because operators read bars there. No operator task needs tick bars, and the walkthrough checks them through the API. `get ticks` keeps its row output: the compact encoding is a wire format for API clients, not an operator view.

### D9 — Fixtures, oracle and the production check
Status: decided

Nothing is bought.

- **Module tests** run the cutter on the real 2024-12-03 `tbbo` cut, which holds 1,000 records of all instruments on each side of the daily break. Its busiest instrument (ESZ4, 183748) has 982 trades before the 2024-12-03 close and 997 after the 2024-12-04 open, and 1,968 and 1,491 trades in those two sessions overall (corrected in Phase 6; the oracle asserts these counts). Expected bars come from an independent oracle in the test: the trades sorted by key, split per session, chunked by N with list slicing, each chunk summarised. The tests cover N = 1, a prime N that leaves a partial bar, N equal to and greater than a session's count, the restart at the session open, repeated `(ts_event, sequence)` records, and `buy + sell + side-N volume = volume`.
- **`trades` tier:** the derived `trades` twin of the same day gives identical OHLCV, counts and sides, with null bid/ask.
- **Window independence:** for random `from`/`until` cuts, the returned bars equal the full read's bars filtered by `open_time`.
- **Integration (tick test cluster):** both routes over the ingested fixture, admission refusals at a small configured trade limit, and the bar ceiling.
- **Columnar round trip:** decoding a block (prefix-summing `ts_event` from `ts_base`, scaling prices) reproduces `read_ticks`' rows exactly, for a `tbbo` and a `trades` block, through JSON and msgpack.
- **Production (Phase 7, after release):** one ES request at the trade limit at N = 1000. Its range is chosen from the ledger, not from a session count: the run of consecutive ES 2024 sessions whose record counts (`mt data tick coverage --json`) sum to between 95% and 100% of the configured limit. The sum is recorded with the timing. The request completes in under 10 s, the bound 306 D10 uses. For every session, `Σ trade_count` equals the ledger's record count and `Σ volume` its volume; `count` equals `Σ ceil(nᵢ / 1000)`. While that request runs, a concurrent `GET /api/v1/futures/products` answers within 100 ms of its time measured idle, which shows the loop is not starved. The result is recorded in `user/notes/308-tick-bar-measurement.md`.

### D10 — Abandoned, concurrent and early-stopped reads
Status: decided

- **Client disconnects.** `read_tick_bars` takes a `stop: Callable[[], Awaitable[bool]]` and awaits it before each fetch. The route binds it to `request.is_disconnected`, so the data layer never sees the HTTP request. On a disconnect the reader raises `ReadAbandoned` (defined in `tick_bar_reads.py`), and the route answers with a bodiless status 499 and no ERROR log: the client is gone, so nothing is delivered. At most one batch already in the executor finishes (milliseconds, Special Considerations, "Event loop"), and no further batch is fetched or pushed.
- **Release on every exit.** The cursor is opened with `async with conn.cursor(...)` and the read runs inside `async with conn.transaction()`, so a window stopped early at `until`, a cutter error, a disconnect or a cancellation all close the cursor, roll back the snapshot and return the connection to the pool through the `tick_connection` dependency. No path needs its own cleanup.
- **Concurrent limit-sized requests.** Each request holds one tick pool connection for its whole read. When the pool is exhausted, checkout waits `TICK_API_CHECKOUT_TIMEOUT_SECONDS` and the existing mapping answers 503 (`PoolTimeout` is an `OperationalError`), never a hang. Executor threads are held only for one batch at a time, so the default executor is not tied up for the length of a request. No extra cap is added. The accepted limit is the architecture's point 9.
- **Test (integration):** a read stopped early at `until`, and a read whose disconnect check returns true after the first batch, both leave the pool with every connection available again, and the disconnected read pushes no further batch.

## Implementation Details

### API Contracts

**New:** `GET /api/v1/futures/contracts/{symbol}/tick-bars`, `GET /api/v1/futures/products/{product}/tick-bars`

```json
{
  "target": {"kind": "product", "product": "ES", "rule": "v.0", "…": "…"},
  "ticks_per_bar": 1000, "start": "2024-12-03", "end": "2024-12-04",
  "from": null, "until": null, "definitions_as_of": "2024-12-03",
  "segments": [ … ], "unresolved": [], "incomplete": [], "count": 2,
  "bars": [{
    "session_date": "2024-12-03", "index": 0,
    "open_time": "2024-12-02T23:00:00.000000000Z", "close_time": "…",
    "raw_symbol": "ESZ4", "instrument_id": 0,
    "open": "…", "high": "…", "low": "…", "close": "…",
    "volume": 0, "trade_count": 1000, "buy_volume": 0, "sell_volume": 0,
    "bid_px": "…", "ask_px": "…", "partial": false
  }]
}
```

(Values are shape only.)

**Changed:** both `/ticks` routes, columnar body (D6).

**Docs:** `reference.md` §6.1 gains the tick-bar rule (D2), admission (D5) and the columnar decoding rule with a short decoding example. §6.5/§6.7 are rewritten for the columnar body. New sections cover the two tick-bar routes. `agents.md` rewrites "Give me futures ticks" and adds "Give me futures tick bars", with `<!-- endpoint: -->` markers. `openapi.json` is regenerated by `scripts/dump_openapi.py`, and `check_api_docs.py` and `test_openapi_artifact.py` pass.

**Settings:** `MT_API_MAX_TICK_BAR_TRADES` (positive integer, default `API_MAX_TICK_BAR_TRADES`), documented beside `MT_API_MAX_BARS_PER_REQUEST` in the reference and the README settings tables (the operator runbook has no API settings section).

## Integration Points

### Provides to Other Slices

- **280 (live):** `TickBarCutter` with `push`/`current`/`close_session` for live cutting, the columnar block as the message body, and the stream key on every tick.
- **trading-ui / trading-engine:** tick bars at any N and compact ticks (issue #62, closed by this slice's release). The engine can import `tick_bars.py` when it runs its own feed.

### Consumes from Other Slices

- **306:** the family-parameterised plan, `verdicts_for`, and the provider-only bars routes. 308 does not start Phase 6 until 306 is merged.
- **302/300:** `tick_trade`, `ledger_totals`. Admission is exact only while ledger and rows agree. A disagreement is a `coverage` finding (exit 3). If it ever happens, the bar ceiling and the statement timeout still bound the read.

## Success Criteria

### Functional Requirements

1. `TickBarCutter` produces the oracle's bars for every case in D9, on the real `tbbo` cut and its `trades` twin.
2. A session's last bar is marked partial exactly when its trade count is not a multiple of N. The count restarts at index 0 at each session open, both within one cutter and across product windows.
3. `current()` returns the open bar without changing later output. `close_session()` ends a session. Out-of-order or undefined-price input raises `TickBarInputError`, and the route answers it with a logged 500 (D1).
4. Both tick-bar routes return D2's fields, with (session_date, index) strictly ascending, and accept any `ticks_per_bar ≥ 1`. 0 or a negative value is a 422.
5. Bars returned with `from`/`until` equal the full read's bars filtered by `open_time ∈ [from, until)`.
6. A request whose windows hold more trades than `MT_API_MAX_TICK_BAR_TRADES` is refused with 422 before any tick is read, quoting the count and the limit. A request over the bar ceiling is refused with the D5 remedy.
7. Both `/ticks` routes return D6's columnar body, and decoding it reproduces `read_ticks`' rows, including `sequence_ordinal`, through JSON and msgpack.

### Technical Requirements

8. `tick_bars.py` imports only the standard library and `dbn_values.py`, and the isolation test (D1) passes. Tick bars are produced nowhere else: no SQL statement groups trades by count.
9. ruff format and lint (scoped to touched files), mypy, and the unit and integration tiers for the touched areas pass. New files stay under about 300 lines.

### Integration Requirements

10. `reference.md`, `agents.md` and `openapi.json` describe the tick-bar routes, the rule, admission and the columnar encoding. `check_api_docs.py`, `test_api_docs.py` and `test_openapi_artifact.py` pass, and the 200 schemas of all four routes are published.
11. The CHANGELOG lists the new routes, the columnar `/ticks` body (a breaking change), the dropped `bid_ct`/`ask_ct` and the new setting.
12. The Phase 7 measurement, at a range whose ledger sum is 95–100% of the trade limit, is recorded with that sum in `user/notes/308-tick-bar-measurement.md` (D9).

### Verification Walkthrough

The routes exist after Phase 6. The production steps run after the release, against the held ES 2024 ticks. Before then, the integration tests run the same paths on the fixtures.

1. **Tick bars at an arbitrary N.**
   ```bash
   curl -s 'localhost:8100/api/v1/futures/products/ES/tick-bars?ticks_per_bar=997&start=2024-12-03&end=2024-12-04' \
     | jq '.count, .bars[0] | {session_date, index, open_time, trade_count, partial}'
   curl -s '…same…' | jq '[.bars[] | select(.partial)] | length'
   # one partial bar at most per session; index 0 at the session open
   ```
2. **Partial bar and restart across sessions.**
   ```bash
   curl -s 'localhost:8100/api/v1/futures/products/ES/tick-bars?ticks_per_bar=1000&start=2024-12-02&end=2024-12-04' \
     | jq '[.bars[] | select(.index == 0 or .partial) | {session_date, index, trade_count, partial}]'
   ```
3. **Totals agree with the ledger.**
   ```bash
   sudo mt-run data tick coverage --product ES --start 2024-12-02 --end 2024-12-04 --json
   # compare each session's record count and volume with Σ trade_count and Σ volume from step 2
   ```
4. **Admission.**
   ```bash
   curl -s -w '%{http_code}\n' 'localhost:8100/api/v1/futures/products/ES/tick-bars?ticks_per_bar=1000&start=2024-09-03&end=2024-12-31' | tail -c 200
   # 422: "the request scans … trades, over the 2,000,000 trade limit; narrow start/end"
   curl -s -w '%{http_code}\n' 'localhost:8100/api/v1/futures/products/ES/tick-bars?ticks_per_bar=1&start=2024-12-03&end=2024-12-04' | tail -c 200
   # 422: over the 75,000 row limit; raise ticks_per_bar, or narrow start/end or from/until
   ```
5. **Measurement at the trade limit (D9).**
   ```bash
   sudo mt-run data tick coverage --product ES --start 2024-09-03 --end 2025-01-01 --json
   # pick START/END: consecutive sessions whose record counts sum to 95–100% of the trade limit
   time curl -s --compressed -o /tmp/tb.json "localhost:8100/api/v1/futures/products/ES/tick-bars?ticks_per_bar=1000&start=$START&end=$END"
   # meanwhile, in a second shell: time curl -s -o /dev/null localhost:8100/api/v1/futures/products  (within 100 ms of idle)
   # under 10 s; the ledger sum and the timing are recorded in user/notes/308-tick-bar-measurement.md
   ```
6. **Compact ticks.**
   ```bash
   curl -s --compressed -H 'Accept-Encoding: gzip' -o /tmp/ticks.json -w '%{size_download}\n' \
     'localhost:8100/api/v1/futures/products/ES/ticks?start=2024-12-03&end=2024-12-04&until=2024-12-03T00:30:00Z'
   jq '.price_scale, .blocks[0] | {session_date, raw_symbol, count, ts_base, side: .side[0:10]}' /tmp/ticks.json
   # bytes per tick ≈ size_download / count; expect about 10 gzipped (ADR: 9.5)
   ```
7. **Docs.** `uv run python scripts/check_api_docs.py` passes. `docs/api/openapi.json` shows `FuturesTickBarsResponse` and the columnar `FuturesTicksResponse`.

## Implementation Notes

### Development Approach

Suggested order, committing at each step's end:

1. **`tick_bars.py` and its oracle tests (D1, D2, D9).** No database.
2. **`tick_bar_reads.py`, the `admit` hook and the setting (D3, D5),** with integration tests on the fixture.
3. **Tick-bar routes and models (D4, D7).**
4. **Columnar ticks (D6)** with the round-trip tests.
5. **Docs, OpenAPI regeneration, CHANGELOG.**

### Special Considerations

- **Throughput.** History pushes every trade through Python. The trade limit (D5) is what keeps a request inside the 10 s bound, and D9 measures it on production data. `Trade._make` over fetched tuples avoids per-field work. The loop stays simple unless the measurement says otherwise.
- **Event loop.** Pushing one fetched batch (`TICK_GET_FETCH_ROWS`, 10,000 trades) takes milliseconds, far over the 1 ms the Python rules allow on an async path. So `read_tick_bars` awaits each batch from the cursor on the loop, then pushes it through the cutter with `loop.run_in_executor(None, …)`, as `send_timeseries` encodes off the loop. The columnar pivot (`TickBlocks.from_rows`) and the tick-bar model build run in the executor the same way. Shared state: the cutter and the batch belong to one window of one request, and each executor call is awaited before the next fetch, so only one thread touches them at a time and nothing is shared between requests. The production check (D9) adds a concurrency test.
- **Scaling.** Server-side cutting is known not to scale past a handful of clients (ADR point 9). That is accepted. The module's purity is what lets a per-client engine take the work over later.
- **Production database.** Phase 6 uses only the test cluster's fixture databases. Production is read only, through the served API and `sudo mt-run`, in Phase 7.
