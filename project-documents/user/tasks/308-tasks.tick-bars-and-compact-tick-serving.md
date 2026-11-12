---
docType: tasks
slice: tick-bars-and-compact-tick-serving
project: trading-data
lld: user/slices/308-slice.tick-bars-and-compact-tick-serving.md
parent: user/architecture/300-slices.futures-historical-bars-statistics-and-tick-aggregation.md
dependencies:
- 306
interfaces: []
projectState: Slice design committed and reviewed. After 306 the futures bars routes
  read provider bars only and plans take a `family`. `/ticks` still returns row records
  (`TickRecord`, 14 columns). `tick_reads.stream_rows` is already public. No tick-bar
  code exists.
dateCreated: 20261111
dateUpdated: 20261112
status: in_progress
turns: 26
promptTokens: 2105367
cachedTokens: 1975630
cacheWriteTokens: 129685
completionTokens: 43565
durationSeconds: 301.1
costUsd: 1.152359
usageRounds: 3
pricedRounds: 3
usageByModel:
  claude-sonnet-5-5:
    rounds: 3
    costUsd: 1.1523589
    completionTokens: 43565
revision_number: 2
---

# Tasks: Tick Bars and Compact Tick Serving

## Context Summary

- Working on slice 308. It serves trade-count bars at any N and replaces the
  row-record `/ticks` body with a columnar one.
- Prerequisite: 306 merged (family-parameterised plans, `verdicts_for`). Task 1.1
  checks it before anything is written.
- It delivers:
  - `data/tick/dbn_values.py` and `data/tick/tick_bars.py` (the one rollup rule,
    standard library only);
  - `data/tick/tick_bar_reads.py` (history feeds stored trades to the cutter);
  - the `admit` hook on `read_series` and the `MT_API_MAX_TICK_BAR_TRADES` setting;
  - two tick-bar routes (contract, product) with `FuturesTickBarsResponse`;
  - columnar `/ticks` bodies (`models/futures_ticks.py`), `TickRecord` removed;
  - `reference.md`, `agents.md`, `openapi.json`, CHANGELOG;
  - a load-tier test for the 10 s and 250 ms bounds, and the measurement script
    for success criterion 12.
- Not here: live cutting (280), a CLI form of tick bars, time bars from ticks,
  any purchase or migration, the #72 rename. Running the measurement script
  (task 7.2) against production and writing its note is a named Phase 7 step,
  tracked by issue #75.
- Tests live in `test/unit/data/tick/` (module), `test/unit/api_server/`
  (models, params, settings) and `test/integration/api_server/` (routes, tick
  test cluster). Integration tests need `MT_TIMESCALE_TEST_URL` exported from
  `.env` (strip quotes). Tick fixtures come from `tick_support` (`seed_ticks`,
  `client_for`), as `test_futures_series.py` does.
- Before every `**Commit**:` marker: `ruff format <files>` and `ruff check
  <files>` on touched files only, mypy over `src/manta_trading` and `test` in
  one invocation, and the touched areas' unit and integration tests. A failure
  in a file this slice did not touch is re-run in isolation and compared with
  the base commit before it is set aside.
- Two names the design leaves out are introduced here and recorded in the slice
  (D1, D10): `TickBarWindowError` and `ReadAbandoned`, both in `tick_bar_reads.py`.
- D3 says history reads "through `stream_rows`". D10 needs an awaited step
  before each fetch and an executor push after it, which `stream_rows`'
  synchronous `on_batch` cannot do. So the cursor loop lives once, in a new
  `tick_reads.iter_batches`; `stream_rows` and `read_tick_bars` both consume it.
- New files stay under about 300 lines; functions under about 50.
- Next planned slice: per the architecture's slice list (280 live stream follows).

---

## 1. Preconditions and the shared DBN values (D1)

- [x] **1.1 Confirm 306 is merged**
  - [x] `git log main --oneline` shows the 306 merge, and
    `grep -n "def verdicts_for" src/manta_trading/data/tick/*.py` finds it.
  - [x] `contract_plan` and `product_plan` accept a `family` argument
    (`src/manta_trading/data/tick/get_plan.py`).
  - [x] `tick_reads.stream_rows` is public and `read_ticks` calls it (the design's
    `_stream` rename is already done).
  - [x] If any item fails, write a line starting `BLOCKED:` under this task,
    leave it unchecked and stop.

- [x] **1.2 Create `data/tick/dbn_values.py`**
  - [x] Standard library only. It defines `UNDEF_INT64` (`2**63 - 1`),
    `TRADE_SIDE_BUY = "B"` and `TRADE_SIDE_SELL = "A"`.
  - [x] Docstring says why it exists: `constants.py` imports
    `manta_trading.constants` and calendar seeds, so the cutter cannot import it.

- [x] **1.3 Move `UNDEF_INT64` out of `constants.py`**
  - [x] `data/tick/constants.py` imports it from `dbn_values`; its own uses (near
    lines 302-303) still work.
  - [x] `tick_format.py` imports it from `dbn_values`; `constants.py` imports it
    for its own uses.
  - [x] `test/integration/data/test_tick_definitions.py:40` defines its own
    `UNDEF_INT64`; replace it with an import from `dbn_values`. Then `grep -rn
    "UNDEF_INT64 =" src test` finds exactly one definition.
  - [x] Existing `test/unit/data/tick` tests (constants, tick_format) pass.

---

## 2. The rollup module (D1, D2)

- [x] **2.1 Test support: the oracle and the real fixture trades**
  - [x] Add `test/unit/data/tick/tick_bar_oracle.py` (helper, not a test file).
    It decodes `test/fixtures/databento/real/glbx-mdp3-20241203.tbbo.dbn.zst`
    and `glbx-mdp3-20241203.trades.dbn.zst` with `DbnFileReader`
    (`data/tick/databento/dbn_file.py`; see `test/unit/data/tick/test_dbn_file.py`
    for how it is called) and returns `Trade` tuples for the busiest instrument: the
    `instrument_id` with the most records in the file, all of whose records are
    taken in key order. `session_date` comes from the CME session containing
    each `ts_event`, as the ingest does. The design says this instrument has
    1,000 contiguous trades each side of the daily break (the end of session
    2024-12-03 and the start of 2024-12-04).
  - [x] The oracle is independent of the cutter: sort trades by
    `(ts_event, sequence, sequence_ordinal)`, split per `session_date`, chunk by
    N with list slicing, summarise each chunk into the D2 fields.
  - [x] The helper asserts its own premise (1,000 trades each side of the break),
    so a changed fixture fails loudly.
  - Note: measured premise differs from the design wording: the fixture cut holds 1,000 records of all instruments each side of the break; ESZ4 (183748) has 982 trades before the 12-03 close and 997 after the 12-04 open, 1,968 / 1,491 per session. The oracle asserts these; the slice design D9 is corrected.

- [x] **2.2 Implement `TickBarInputError`, `Trade`, `TickBar`**
  - [x] `data/tick/tick_bars.py`, imports only the standard library and
    `dbn_values`.
  - [x] `TickBarInputError(ValueError)` with a `keys` attribute holding the
    offending key tuples (previous and current), also named in the message.
  - [x] `Trade` is a `NamedTuple` in D1's field order: `ts_event, sequence,
    sequence_ordinal, session_date, price, size, side, bid_px, ask_px`.
  - [x] `TickBar` is a frozen dataclass or NamedTuple with the D2 fields:
    `session_date, index, open_time, close_time, open, high, low, close, volume,
    trade_count, buy_volume, sell_volume, bid_px, ask_px, partial`. Times and
    prices are integers; formatting is the model's job.
  - Note: `TickBar` is a frozen slotted dataclass; as a NamedTuple its `index` field shadows `tuple.index` (mypy error).

- [x] **2.3 Implement `TickBarCutter`**
  - [x] `__init__(ticks_per_bar)` raises `ValueError` unless `>= 1`.
  - [x] `push(trade)` returns 0, 1 or 2 bars: the bar the Nth trade closes; or,
    when `trade.session_date` differs from the open session's, the open bar first
    emitted with `partial=True` and the count restarted at index 0 (the new trade
    then starts or closes a bar).
  - [x] A bar closes at its Nth trade; no bar is open after it.
  - [x] `current()` returns the open bar with `partial=True` without closing it,
    or `None`. `close_session()` returns the open bar as final with
    `partial=True`, or `None` (a session whose count is a multiple of N ends full).
  - [x] Raises `TickBarInputError` when a trade's key `(ts_event, sequence,
    sequence_ordinal)` is not greater than the previous one, its session goes
    backwards, or its price is `UNDEF_INT64`.
  - [x] Side `B` adds to `buy_volume`, `A` to `sell_volume`, `N` to `volume`
    only. `bid_px`/`ask_px` are the last trade's values (may be `None`).
  - [x] One trade record is one trade: records sharing `(ts_event, sequence)`
    with different `sequence_ordinal` are separate.

- [x] **2.4 Test: cutter against the oracle**
  - [x] `test/unit/data/tick/test_tick_bars.py`, over the `tbbo` cut and its
    `trades` twin where noted.
  - [x] N = 1; a prime N leaving a partial bar; N equal to a session's count
    (one full, non-partial bar); N greater than it (one partial bar). Every
    field is compared with the oracle.
  - [x] Count restarts at index 0 at the 2024-12-04 open; the last bar of
    2024-12-03 is partial exactly when its count is not a multiple of N.
  - [x] Repeated `(ts_event, sequence)` records count as separate trades.
  - [x] `buy_volume + sell_volume + side-N volume == volume` for every bar.
  - [x] `trades` twin: OHLCV, counts and sides equal the `tbbo` result;
    `bid_px`/`ask_px` are `None`.
  - [x] `current()` mid-stream does not change the final output;
    `close_session()` returns `None` after a full bar and on a second call.
  - [x] Equal key, lower key, session going backwards and `UNDEF_INT64` price
    each raise `TickBarInputError` with both keys in `keys`;
    `TickBarCutter(0)` and `(-3)` raise `ValueError`.

- [x] **2.5 Test: module isolation**
  - [x] `test_tick_bars_isolation.py`: a fresh interpreter (`subprocess`,
    `python -I -c`) imports `manta_trading.data.tick.tick_bars` and reports
    whether `psycopg` and `manta_trading.data.tick.constants` are in
    `sys.modules`. Assert neither is.

**Commit**: `feat: add the tick-bar rollup module and move UNDEF_INT64 to dbn_values`

---

## 3. History reader, admit hook and setting (D3, D5, D10)

- [x] **3.1 Extract `iter_batches` in `tick_reads.py`**
  - [x] `iter_batches(conn, query, params)`: an async generator holding the
    named-cursor loop that `stream_rows` has today (cursor in `async with`,
    `fetchmany(TICK_GET_FETCH_ROWS)`, yield each batch).
  - [x] `stream_rows` becomes a loop over `iter_batches` under
    `contextlib.aclosing`, with the same signature, behavior and return value.
  - [x] Existing tests that exercise `read_ticks` and its CLI pass unchanged
    (`test_tick_get_cli.py`, `test_futures_series.py`).

- [x] **3.2 Implement `read_tick_bars`**
  - [x] First, in `data/tick/tick_bar_reads.py`, define the trade SELECT once:
    columns in `Trade` order (`ts_event, sequence, sequence_ordinal`, the
    window's `session_date` as a parameter, `price, size, side, bid_px_00,
    ask_px_00`), `WHERE` from `tick_reads.WINDOW_WHERE`, `ORDER BY ts_event,
    sequence, sequence_ordinal`.
  - [x] Signature `read_tick_bars(conn, window, ticks_per_bar, bounds, on_batch,
    stop)`, `stop: Callable[[], Awaitable[bool]]`, `bounds` = `(from_ns,
    until_ns)`. Binding the last four with `functools.partial` gives a
    `ReadWindow`, so `read_series` drives it like `read_ticks`.
  - [x] Iterates `iter_batches` under `aclosing`; `read_series` already holds the
    transaction, so no second one is opened. Scans from the session open; `from`
    never clips the scan.
  - [x] Awaits `stop()` before pulling each batch, the first included. If true,
    raises `ReadAbandoned`; no further batch is fetched or pushed.
  - [x] Pushes each batch through a fresh `TickBarCutter` in
    `loop.run_in_executor(None, ...)`, awaited before the next fetch. The
    executor function builds trades with `Trade._make` and returns the closed
    bars and a `done` flag.
  - [x] The `until` stop uses a plain counter, not `current()`: the window is one
    session, so the trade at position `i` (0-based) opens a bar exactly when
    `i % ticks_per_bar == 0`. If that trade's `ts_event >= until_ns`, set `done`
    and stop pushing. A bar opened before `until` is completed with later trades.
    On `done`, leave the loop; `aclosing` closes the cursor.
  - [x] Keeps bars whose `open_time` is in `[from, until)` and passes them to
    `on_batch` with `raw_symbol` and `instrument_id` from the window. After the
    loop, `close_session()`'s bar goes through the same rule.
  - [x] Defines `TickBarWindowError(Exception)` carrying `session_date`,
    `raw_symbol`, `instrument_id`. The executor call catches `TickBarInputError`
    and raises `TickBarWindowError ... from` it, so the route logs the window and
    the original `keys` without parsing messages. No other exception is caught.
  - Note: signature is `read_tick_bars(conn, window, on_batch, *, ticks_per_bar, bounds, stop)`: `functools.partial` cannot bind `on_batch`, which `read_series` passes positionally, so the three bound parameters are keyword-only. The route calls `read_series` with no bounds so windows arrive unclipped; the reader applies `[from, until)` itself via `overlapping` (built on `clip`, which moved from `futures_read` to `tick_reads` so the data layer can use it; `futures_read` re-imports it).

- [x] **3.3 Test: reader (integration, tick test cluster)**
  - [x] `test/integration/api_server/test_tick_bar_reads.py`. Seed with
    `seed_ticks` as `test_futures_series.py` does, plus a session with enough
    trades for several bars. Call `read_tick_bars` directly in a snapshot.
  - [x] Bars over one window equal the cutter run on the same stored trades read
    with `read_ticks`.
  - [x] Window independence: for several random `from`/`until` pairs (fixed
    seed), the bars equal the full read's bars filtered by `open_time` in
    `[from, until)`, including indices.
  - [x] A read stopped at `until` fetches fewer batches than the full read:
    monkeypatch `tick_reads.TICK_GET_FETCH_ROWS` small and count `fetchmany`
    calls. Every pool connection is available afterwards.
  - [x] A `stop` that returns true after the first batch raises `ReadAbandoned`,
    pushes no further batch, and leaves every pool connection available.
  - [x] A sentinel-price trade raises `TickBarWindowError` whose `__cause__` is a
    `TickBarInputError`, and leaves the pool intact.
  - Note: pool checks check out every connection with a timeout and assert each is idle; `get_stats()` races psycopg_pool's background return worker when a `reset` callback is set.

- [x] **3.4 Add the `admit` hook to `read_series`**
  - [x] `admit: Callable[[Conn, GetPlan], Awaitable[None]] | None = None`, called
    after `plan_for` and before the first window, inside the snapshot. `/ticks`
    and `/bars` pass none. One-line docstring entry.

- [x] **3.5 Implement the trade-scan admission check**
  - [x] In `tick_bar_reads.py`: sums `status_reads.ledger_totals` record counts
    over the windows `clip` selects for `[from, until)`. Read the real signature
    of `ledger_totals` and the call at `tick_coverage.py:137` first.
  - [x] Raises an admission error carrying the sum and the limit; the route maps
    it to 422 with "the request scans {n:,} trades, over the {limit:,} trade
    limit; narrow start/end" (D5).
  - Note: `ledger_totals` needs the product's request shape, and a contract plan carried no product, so `ContractTarget` gained a required `product` field (set by `contract_plan`); the API `target` body is unchanged. The admission is `trade_admission(bounds, limit)` raising `TradeScanExceeded`; a window with no ledger row counts 0.

- [x] **3.6 Add the setting and constant**
  - [x] `API_MAX_TICK_BAR_TRADES: Final[int] = 2_000_000` in
    `manta_trading/constants.py` beside `API_MAX_BARS_PER_REQUEST`, with a comment
    that the default is lowered if the Phase 7 measurement (issue #75) misses 10 s.
  - [x] `config/__init__.py`: `api_max_tick_bar_trades: int = Field(default=
    API_MAX_TICK_BAR_TRADES, gt=0)` (env `MT_API_MAX_TICK_BAR_TRADES`).
  - [x] `app.py` lifespan sets `app.state.max_tick_bar_trades`; `deps.py` gains
    `get_max_tick_bar_trades` in the shape of `get_max_bars`.

- [x] **3.7 Test: hook, admission and setting**
  - [x] `test/unit/api_server/test_tick_bar_settings.py`: default equals the
    constant; `0` and a negative value fail validation; a positive override is read.
  - [x] In `test_futures_read.py`: `admit` runs once, after the plan and before
    any window read; with `None` behavior is unchanged; an exception from `admit`
    reads no window.
  - [x] In `test_tick_bar_reads.py`: the admission sum equals the rows
    `read_ticks` returns for the same windows, and windows outside `[from,
    until)` are not counted; over the limit it raises before any cursor opens.

**Commit**: `feat: add the tick-bar history reader, admit hook and trade limit setting`

---

## 4. Tick-bar models, parameter and routes (D1 route error, D4, D5, D7, D10)

- [x] **4.1 Add the tick-bar models and parameter**
  - [x] In `routes/futures_params.py` add `TicksPerBarParam`: required integer,
    `ge=1`, no upper bound, declared like the other params there.
  - [x] In `models/futures.py`, rename the private `_envelope(plan)` to
    `plan_envelope` (it returns `target, start, end, definitions_as_of,
    segments, unresolved, incomplete`) and update its callers. New response
    models call it as `FuturesBarsResponse.from_plan` does; none re-builds those
    fields. `futures.py` is 342 lines: add nothing else to it.
  - [x] New `models/futures_tick_bars.py`: `FuturesTickBarRecord` (fields as in
    the design's API contract; prices through `price_text`, times through
    `ts_text`) and `FuturesTickBarsResponse` (`target, ticks_per_bar, start, end,
    from, until, definitions_as_of, segments, unresolved, incomplete, count,
    bars`). `from_` with alias `from`, `populate_by_name` and `serialize_by_alias`
    as `FuturesTicksResponse` has. No `source` field.
  - [x] The build runs in the executor like the other model builds.

- [x] **4.2 Test: model**
  - [x] `test/unit/api_server/test_futures_tick_bar_models.py`: a `TickBar` with
    known integers yields the expected decimal strings and nine-digit times;
    `None` quotes stay `None`; `from` serialises under that name.

- [x] **4.3 Implement `routes/futures_tick_bars.py`**
  - [x] `GET /api/v1/futures/contracts/{symbol}/tick-bars` and
    `GET /api/v1/futures/products/{product}/tick-bars`; parameters
    `ticks_per_bar, start, end, from, until, rule` (product), `format`, typed as
    the `/ticks` routes in `futures_series.py`.
  - [x] Plans with `family=TICK_TIER` through the plan helpers
    `futures_series.py` already has; import them, do not copy them.
  - [x] Calls `read_series` with `admit` bound to the admission check, the limit
    from `get_max_tick_bar_trades`, the bar ceiling from `get_max_bars` with the
    remedy "raise ticks_per_bar, or narrow start/end or from/until", the bounds,
    and `stop = request.is_disconnected`.
  - [x] `RowCeilingExceeded` maps through `admission.over_ceiling`; the admission
    error is 422; `tick_errors` maps 404/422/503/504 as on every futures route.
  - [x] Catches `TickBarWindowError` only: `logger.exception` at ERROR with the
    error's session, symbol, instrument and its cause's `keys`, then 500 with
    "stored ticks for session {date} of {symbol} cannot be cut into bars; see the
    server log".
  - [x] Catches `ReadAbandoned`: returns a bodiless `Response(status_code=499)`
    and logs nothing at ERROR (the client has already gone).
  - [x] Response via `send_timeseries` / `timeseries_200`, with the 200 schema
    published in OpenAPI (do not hide it with `response_class=Response`).
  - [x] Include the router in `app.py` beside `futures_series`.
  - Note: the plan helpers in `futures_series.py` are now public (`contract_plan_for`, `product_plan_for`) and imported; the remedy text is `admission.NARROW_TICK_BARS`; the `from`/`until` parameter descriptions now say they apply to tick bars too (by `open_time`). `docs/api/openapi.json` was regenerated here and `test_openapi_artifact.py` updated (27 paths, two more msgpack routes) so that test stays green; `test_api_docs.py::test_gate_passes_on_the_committed_tree` fails until task 6 adds the reference/agents sections for the two new paths.

- [x] **4.4 Test: routes (integration)**
  - [x] `test/integration/api_server/test_futures_tick_bars.py`, seeded as
    `test_futures_series.py`.
  - [x] Contract route: 200 with every D2 field; `(session_date, index)` strictly
    ascending; `count == len(bars)`; non-partial bars have `trade_count ==
    ticks_per_bar`; N above the session's trades gives one partial bar; N equal
    to it gives one full bar.
  - [x] `ticks_per_bar` 0, -1, `abc` and missing: 422.
  - [x] `from`/`until` bars equal the unrestricted bars filtered by `open_time`.
  - [x] Unknown contract 404; bad range 422; tick database down 503 (reuse the
    `/ticks` tests' fixtures).
  - [x] Product route over `seed_september_roll`: one contract per session and
    the count restarts at index 0 after the roll; unknown product 404; bad `rule`
    422; a session not load-complete is in `incomplete` and its bars are returned.
  - [x] Admission: with a small trade limit a larger request is 422 before any
    tick is read, quoting count and limit. With a small
    `MT_API_MAX_BARS_PER_REQUEST` and `ticks_per_bar=1` the request is 422 with the
    remedy text.
  - [x] A sentinel-price trade: 500 with the stable detail and one ERROR record in
    `caplog` holding session, symbol, instrument and both keys.
  - [x] A `stop` that turns true after the first batch (patch what the route
    binds) answers 499 and logs no ERROR.

**Commit**: `feat: add the tick-bar models and the contract and product tick-bar routes`

---

## 5. Columnar ticks (D6)

- [x] **5.1 Return `sequence_ordinal` from the tick read**
  - [x] Add `sequence_ordinal` to `tick_reads._TICKS`' select list and to
    `TICK_COLUMNS`. `ORDER BY` unchanged.
  - [x] `mt data tick get ticks` keeps its row output; update its tests only if
    the extra column shows there.

- [x] **5.2 Implement `models/futures_ticks.py`**
  - [x] `TickBlock`: `session_date, raw_symbol, instrument_id, count, ts_base`
    (decimal string), `ts_event` (each the delta from the previous tick, first 0), `sequence`,
    `sequence_ordinal`, `price`, `size`, `side` (one character per tick),
    `bid_px`, `ask_px`, `bid_sz`, `ask_sz` (`None` where absent).
  - [x] `TickBlocks.from_rows(rows)`: consecutive rows of one (session,
    instrument) make one block; columns pivoted; `ts_base` is the block's first
    `ts_event`, and `ts_event[i]` is the delta from tick `i - 1` (D6), so a prefix
    sum from `ts_base` restores every timestamp; an undefined price becomes `None`; `bid_ct`/`ask_ct` dropped.
  - [x] `FuturesTicksResponse` (columnar, moved here from `futures.py`): built
    with `plan_envelope`, plus `price_scale` (from `TICK_PRICE_SCALE`, not
    retyped) and `blocks`, one per non-empty window in plan order. The pivot runs
    in the executor.
  - Note: the block model is `FuturesTickBlock`, not `TickBlock`: `models/futures.py` already has the status `TickBlock` (an OpenAPI component), and two models of one name would rename both components. `FuturesTickBlock.from_rows` builds the blocks; the pivot is `block_fields(rows)` so the 231 load test's construct-without-validation path reuses it. `price_scale` is `tick_format.PRICE_DIGITS` (derived from `TICK_PRICE_SCALE`).

- [x] **5.3 Test: pivot and round trip (unit)**
  - [x] `test/unit/api_server/test_futures_ticks_models.py`: two windows make two
    blocks, two rows of one window make one; `ts_base` is a string, deltas are
    never negative, `ts_event[0]` is 0; a `ts_event` above 2**53 survives; the
    side string's length equals `count`; bid/ask columns are `None` for
    `trades`-tier rows and a block never drops a column; an undefined price is
    `None`.
  - [x] A test-side decoder (prefix-sum from `ts_base`, scale prices) reproduces
    the input rows exactly, `sequence_ordinal` included, for a `tbbo` and a
    `trades` block.

- [x] **5.4 Switch both `/ticks` routes and remove the row model**
  - [x] `routes/futures_series.py` builds the columnar response; parameters,
    plan, row ceiling, paging and error mapping unchanged.
  - [x] Remove `TickRecord`, its `_records` use and dead helpers from
    `models/futures.py`. `grep -rn TickRecord src test docs` finds nothing.

- [x] **5.5 Test: `/ticks` routes (integration) and the 231 load test**
  - [x] Update the `/ticks` cases in `test_futures_series.py` (and any test
    reading `ticks[...]`) to the columnar body; segments, unresolved, incomplete,
    `count`, `from`/`until` paging, the row ceiling and error mapping still pass.
    New columnar cases go in `test_futures_ticks_columnar.py` if the file would
    pass ~450 lines.
  - [x] Both routes: decoding the blocks through JSON and msgpack reproduces
    `read_ticks`' rows exactly. The `mt data tick get --json` comparisons still
    pass.
  - [x] `test/load/test_231_futures_api_nfr.py` builds `FuturesTicksResponse` rows
    in `_constructed` and `test_model_construct_versus_validation`: update them
    to the columnar model (the load tier is not run here; `ruff` and mypy must
    pass on it).
  - Note: a shared decoder lives in `test/tick_support/columnar.py`. The CLI comparison decodes the body and formats it with `row_cells`, minus `bid_ct`/`ask_ct`. The 231 load test is updated but not run; its `validated < 1.2 * constructed` assertion was measured on row records and may need re-measuring on the columnar body. `docs/api/openapi.json` regenerated; `test_api_docs.py::test_gate_passes_on_the_committed_tree` still fails until task 6.

**Commit**: `feat: serve /ticks as columnar blocks, drop bid_ct and ask_ct from the wire`

---

## 6. Docs, OpenAPI and CHANGELOG

- [x] **6.1 `docs/api/reference.md`**
  - [x] §6.1 gains: the tick-bar rule in prose (index, open/close time, OHLC by
    key order, volume, `trade_count`, buy/sell volume with side `N` in `volume`
    only, bid/ask of the last trade, `partial` only for a session's last bar with
    fewer than N trades), the incomplete-session note, admission (trades scanned,
    then bars returned; the setting `MT_API_MAX_TICK_BAR_TRADES` beside
    `MT_API_MAX_BARS_PER_REQUEST`), and the columnar decoding rule with a short
    example.
  - [x] §6.5 and §6.7 rewritten for the columnar body.
  - [x] New sections for the two tick-bar routes appended after §6.8; no
    renumbering.

- [x] **6.2 `docs/api/agents.md`**
  - [x] Rewrite "Give me futures ticks" and add "Give me futures tick bars",
    each with `<!-- endpoint: -->` markers as the file's other sections have.

- [x] **6.3 README settings tables**
  - [x] Add a `MT_API_MAX_TICK_BAR_TRADES` row (default `2000000`, what it
    bounds) beside the `MT_API_MAX_BARS_PER_REQUEST` row in both `README.md`
    tables (near lines 167 and 818).

- [x] **6.4 Regenerate OpenAPI**
  - [x] `uv run python scripts/dump_openapi.py`; `docs/api/openapi.json` shows
    `FuturesTickBarsResponse` and the columnar `FuturesTicksResponse`, with the
    200 schemas of all four tick routes.

- [x] **6.5 CHANGELOG**
  - [x] Unreleased entry listing the two tick-bar routes, the columnar `/ticks`
    body (marked breaking), the dropped `bid_ct`/`ask_ct`, the new setting.

- [x] **6.6 Test: docs gates and full pass**
  - [x] `uv run python scripts/check_api_docs.py` passes; `test_api_docs.py`,
    `test_openapi_artifact.py` and `test_api_docs_examples.py` pass (fix any
    example the old row body broke).
  - [x] The unit tier and the `test/integration/api_server` and
    `test/integration/data` tick tests pass.
  - [x] `grep -rnEi "row_number|ntile|width_bucket" src/manta_trading/data/tick
    src/manta_trading/api_server` finds nothing. Read every `grep -rni "group by"`
    hit in the same directories: none may select from `tick_trade` (success
    criterion 8). Every new source file is under about 300 lines.
  - [x] The compact-size claim (about 10 B/tick gzipped) is not gated by a test.
    It is checked by hand in the design's Verification Walkthrough step 6, in
    Phase 7 with the measurement in #75.
  - Note: `check_api_docs.py --check` passes; `test_api_docs.py` and `test_openapi_artifact.py` pass. Pre-existing on `main`, not this slice: `test_api_docs_examples.py::test_every_curl_block_is_followed_by_a_capture_date[reference.md]` (the Kalshi search example has no capture date) and `test_tick_status_cli.py::test_an_unreachable_tick_database_exits_4` ×2 (reads the developer `.env`); both re-run on `main` in a temporary worktree with the same result. The only `GROUP BY` over `tick_trade` is `bar_parity`'s time-bucket parity (306), not a count grouping. The CHANGELOG makes no byte-size claim; that is measured in walkthrough step 6 (Phase 7, #75).

**Commit**: `docs: document tick bars and columnar ticks, regenerate OpenAPI, update changelog`

---

## 7. Load test and measurement script

- [x] **7.1 `test/load/test_308_tick_bars_nfr.py`**
  - [x] Follows `test_231_futures_api_nfr.py`: the archived 2024-12-18 `tbbo` day
    (889,373 records, 687,287-tick ESZ4 session) ingested through the real pass,
    the app built with `create_app(db_url=…, tick_db_url=…)` on test-cluster
    databases only, and a gate command in the docstring. CI runs no test job, so,
    as for every other load test, the gate is the manual
    `MT_RUN_LOAD_TESTS=1` command; no CI wiring is added.
  - [x] The `busiest` fixture is module-local to test_231. Move it to
    `test/load/conftest.py` and use it from both files; do not copy it.
  - [x] Test 1: product ES `tick-bars` at `ticks_per_bar=1000` over that day, with
    the app's trade limit set above the day's count, completes under the
    bound (never above 10 s). This scans 889,373 records, about 44% of the
    2,000,000 default limit, so it is a proxy: the limit-level bound is checked
    only by the Phase 7 measurement (#75). `Σ trade_count` equals the ledger record count and
    `count` equals `Σ ceil(nᵢ / 1000)` per session.
  - [x] Test 2: while that request runs, `GET /api/v1/futures/products` answers
    within 250 ms of its idle median (several probes). The slice's 100 ms was
    inside the noise of the idle probe itself (see `STARVATION_MARGIN_S`).
  - [x] Run it five times (`MT_RUN_LOAD_TESTS=1 uv run python
    scripts/run_tests.py load -- test/load/test_308_tick_bars_nfr.py`). Set the
    bound to about twice the median, and record the five timings, the median and
    the date in the docstring beside it. If the median is already over 10 s, the
    scan is too slow for the default: lower `API_MAX_TICK_BAR_TRADES` and re-run.
  - RESOLVED (Test 2): measured, not starvation. Idle `/products` is 43-131 ms and cutting adds 50-90 ms (GIL sharing); a shorter `sys.setswitchinterval` did not help. The margin is 250 ms (`STARVATION_MARGIN_S`, evidence in its comment). Test 1 uses `rule=c.1`: `c.0` serves ESZ4, only 170,970 trades that day. Record the five Test 1 timings from the test's five samples.
  - Note: Test 1 scans 661,950 ESH5 session trades (not the design's 687,287 ESZ4).

- [x] **7.2 `scripts/measure_tick_bars.py`, with its tests**
  - [x] A small script for the Phase 7 step of success criterion 12 (run against
    production after release, tracked by #75; not run in Phase 6). Inputs: base
    URL, product, `ticks_per_bar`, the trade limit, and the JSON of `mt data tick
    coverage --product P --start … --end … --json` (read `tick_coverage.py` for
    its keys first).
  - [x] `pick_range(rows, limit)`: the run of consecutive sessions whose record
    counts sum to 95-100% of `limit`; raises when none exists.
  - [x] `check_totals(bars, rows)`: per session `Σ trade_count` equals the ledger's
    record count, `Σ volume` its volume, and the bar count equals `Σ ceil(nᵢ / N)`.
  - [x] Times the request, probes `GET /api/v1/futures/products` idle and during
    it, and writes `user/notes/308-tick-bar-measurement.md` (range, ledger sum,
    timings, checks).
  - [x] `test/unit/test_measure_tick_bars.py` with synthetic ledger rows:
    `pick_range` returns a run in the band and raises when none exists;
    `check_totals` accepts matching totals and names the session on a mismatch.
  - RESOLVED (inputs): do not use `mt data tick coverage --json`; it sums every instrument of the product and has no tick-tier volume. The script builds the plan the route builds: `product_plan(conn, (tick_url, calendar_url), entry, parsed_rule, start, end, SchemaFamily.TICK_TIER)` (see `futures_series.product_plan_for`) gives the exact (instrument, session) windows; `status_reads.ledger_totals` and `ledger_volumes` give each window's record count and volume. Run it with `sudo mt-run` on the host, URLs from settings (never `os.environ`). `pick_range` and `check_totals` take rows of `(session_date, instrument_id, records, volume)` from those. The base URL, product, rule, `ticks_per_bar` and limit stay arguments.
  - Note: `deploy/mt-run` runs only the `mt` CLI, so the script runs from the dev checkout with `uv run` and reads URLs from `Settings`, as `reconcile_tick_spend.py` does; `--limit` is an argument.

**Commit**: `test: add the tick-bar load test and measurement script`
