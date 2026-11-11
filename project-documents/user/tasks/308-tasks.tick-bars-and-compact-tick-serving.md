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
dateUpdated: 20261111
status: not_started
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
  - a load-tier test for the 10 s and 100 ms bounds, and the measurement script
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

- [ ] **1.1 Confirm 306 is merged**
  - [ ] `git log main --oneline` shows the 306 merge, and
    `grep -n "def verdicts_for" src/manta_trading/data/tick/*.py` finds it.
  - [ ] `contract_plan` and `product_plan` accept a `family` argument
    (`src/manta_trading/data/tick/get_plan.py`).
  - [ ] `tick_reads.stream_rows` is public and `read_ticks` calls it (the design's
    `_stream` rename is already done).
  - [ ] If any item fails, write a line starting `BLOCKED:` under this task,
    leave it unchecked and stop.

- [ ] **1.2 Create `data/tick/dbn_values.py`**
  - [ ] Standard library only. It defines `UNDEF_INT64` (`2**63 - 1`),
    `TRADE_SIDE_BUY = "B"` and `TRADE_SIDE_SELL = "A"`.
  - [ ] Docstring says why it exists: `constants.py` imports
    `manta_trading.constants` and calendar seeds, so the cutter cannot import it.

- [ ] **1.3 Move `UNDEF_INT64` out of `constants.py`**
  - [ ] `data/tick/constants.py` imports it from `dbn_values`; its own uses (near
    lines 302-303) still work.
  - [ ] `tick_format.py` imports it from `dbn_values`; `constants.py` imports it
    for its own uses.
  - [ ] `test/integration/data/test_tick_definitions.py:40` defines its own
    `UNDEF_INT64`; replace it with an import from `dbn_values`. Then `grep -rn
    "UNDEF_INT64 =" src test` finds exactly one definition.
  - [ ] Existing `test/unit/data/tick` tests (constants, tick_format) pass.

---

## 2. The rollup module (D1, D2)

- [ ] **2.1 Test support: the oracle and the real fixture trades**
  - [ ] Add `test/unit/data/tick/tick_bar_oracle.py` (helper, not a test file).
    It decodes `test/fixtures/databento/real/glbx-mdp3-20241203.tbbo.dbn.zst`
    and `glbx-mdp3-20241203.trades.dbn.zst` with `DbnFileReader`
    (`data/tick/databento/dbn_file.py`; see `test/unit/data/tick/test_dbn_file.py`
    for how it is called) and returns `Trade` tuples for the busiest instrument: the
    `instrument_id` with the most records in the file, all of whose records are
    taken in key order. `session_date` comes from the CME session containing
    each `ts_event`, as the ingest does. The design says this instrument has
    1,000 contiguous trades each side of the daily break (the end of session
    2024-12-03 and the start of 2024-12-04).
  - [ ] The oracle is independent of the cutter: sort trades by
    `(ts_event, sequence, sequence_ordinal)`, split per `session_date`, chunk by
    N with list slicing, summarise each chunk into the D2 fields.
  - [ ] The helper asserts its own premise (1,000 trades each side of the break),
    so a changed fixture fails loudly.

- [ ] **2.2 Implement `TickBarInputError`, `Trade`, `TickBar`**
  - [ ] `data/tick/tick_bars.py`, imports only the standard library and
    `dbn_values`.
  - [ ] `TickBarInputError(ValueError)` with a `keys` attribute holding the
    offending key tuples (previous and current), also named in the message.
  - [ ] `Trade` is a `NamedTuple` in D1's field order: `ts_event, sequence,
    sequence_ordinal, session_date, price, size, side, bid_px, ask_px`.
  - [ ] `TickBar` is a frozen dataclass or NamedTuple with the D2 fields:
    `session_date, index, open_time, close_time, open, high, low, close, volume,
    trade_count, buy_volume, sell_volume, bid_px, ask_px, partial`. Times and
    prices are integers; formatting is the model's job.

- [ ] **2.3 Implement `TickBarCutter`**
  - [ ] `__init__(ticks_per_bar)` raises `ValueError` unless `>= 1`.
  - [ ] `push(trade)` returns 0, 1 or 2 bars: the bar the Nth trade closes; or,
    when `trade.session_date` differs from the open session's, the open bar first
    emitted with `partial=True` and the count restarted at index 0 (the new trade
    then starts or closes a bar).
  - [ ] A bar closes at its Nth trade; no bar is open after it.
  - [ ] `current()` returns the open bar with `partial=True` without closing it,
    or `None`. `close_session()` returns the open bar as final with
    `partial=True`, or `None` (a session whose count is a multiple of N ends full).
  - [ ] Raises `TickBarInputError` when a trade's key `(ts_event, sequence,
    sequence_ordinal)` is not greater than the previous one, its session goes
    backwards, or its price is `UNDEF_INT64`.
  - [ ] Side `B` adds to `buy_volume`, `A` to `sell_volume`, `N` to `volume`
    only. `bid_px`/`ask_px` are the last trade's values (may be `None`).
  - [ ] One trade record is one trade: records sharing `(ts_event, sequence)`
    with different `sequence_ordinal` are separate.

- [ ] **2.4 Test: cutter against the oracle**
  - [ ] `test/unit/data/tick/test_tick_bars.py`, over the `tbbo` cut and its
    `trades` twin where noted.
  - [ ] N = 1; a prime N leaving a partial bar; N equal to a session's count
    (one full, non-partial bar); N greater than it (one partial bar). Every
    field is compared with the oracle.
  - [ ] Count restarts at index 0 at the 2024-12-04 open; the last bar of
    2024-12-03 is partial exactly when its count is not a multiple of N.
  - [ ] Repeated `(ts_event, sequence)` records count as separate trades.
  - [ ] `buy_volume + sell_volume + side-N volume == volume` for every bar.
  - [ ] `trades` twin: OHLCV, counts and sides equal the `tbbo` result;
    `bid_px`/`ask_px` are `None`.
  - [ ] `current()` mid-stream does not change the final output;
    `close_session()` returns `None` after a full bar and on a second call.
  - [ ] Equal key, lower key, session going backwards and `UNDEF_INT64` price
    each raise `TickBarInputError` with both keys in `keys`;
    `TickBarCutter(0)` and `(-3)` raise `ValueError`.

- [ ] **2.5 Test: module isolation**
  - [ ] `test_tick_bars_isolation.py`: a fresh interpreter (`subprocess`,
    `python -I -c`) imports `manta_trading.data.tick.tick_bars` and reports
    whether `psycopg` and `manta_trading.data.tick.constants` are in
    `sys.modules`. Assert neither is.

**Commit**: `feat: add the tick-bar rollup module and move UNDEF_INT64 to dbn_values`

---

## 3. History reader, admit hook and setting (D3, D5, D10)

- [ ] **3.1 Extract `iter_batches` in `tick_reads.py`**
  - [ ] `iter_batches(conn, query, params)`: an async generator holding the
    named-cursor loop that `stream_rows` has today (cursor in `async with`,
    `fetchmany(TICK_GET_FETCH_ROWS)`, yield each batch).
  - [ ] `stream_rows` becomes a loop over `iter_batches` under
    `contextlib.aclosing`, with the same signature, behavior and return value.
  - [ ] Existing tests that exercise `read_ticks` and its CLI pass unchanged
    (`test_tick_get_cli.py`, `test_futures_series.py`).

- [ ] **3.2 Implement `read_tick_bars`**
  - [ ] First, in `data/tick/tick_bar_reads.py`, define the trade SELECT once:
    columns in `Trade` order (`ts_event, sequence, sequence_ordinal`, the
    window's `session_date` as a parameter, `price, size, side, bid_px_00,
    ask_px_00`), `WHERE` from `tick_reads.WINDOW_WHERE`, `ORDER BY ts_event,
    sequence, sequence_ordinal`.
  - [ ] Signature `read_tick_bars(conn, window, ticks_per_bar, bounds, on_batch,
    stop)`, `stop: Callable[[], Awaitable[bool]]`, `bounds` = `(from_ns,
    until_ns)`. Binding the last four with `functools.partial` gives a
    `ReadWindow`, so `read_series` drives it like `read_ticks`.
  - [ ] Iterates `iter_batches` under `aclosing`; `read_series` already holds the
    transaction, so no second one is opened. Scans from the session open; `from`
    never clips the scan.
  - [ ] Awaits `stop()` before pulling each batch, the first included. If true,
    raises `ReadAbandoned`; no further batch is fetched or pushed.
  - [ ] Pushes each batch through a fresh `TickBarCutter` in
    `loop.run_in_executor(None, ...)`, awaited before the next fetch. The
    executor function builds trades with `Trade._make` and returns the closed
    bars and a `done` flag.
  - [ ] The `until` stop uses a plain counter, not `current()`: the window is one
    session, so the trade at position `i` (0-based) opens a bar exactly when
    `i % ticks_per_bar == 0`. If that trade's `ts_event >= until_ns`, set `done`
    and stop pushing. A bar opened before `until` is completed with later trades.
    On `done`, leave the loop; `aclosing` closes the cursor.
  - [ ] Keeps bars whose `open_time` is in `[from, until)` and passes them to
    `on_batch` with `raw_symbol` and `instrument_id` from the window. After the
    loop, `close_session()`'s bar goes through the same rule.
  - [ ] Defines `TickBarWindowError(Exception)` carrying `session_date`,
    `raw_symbol`, `instrument_id`. The executor call catches `TickBarInputError`
    and raises `TickBarWindowError ... from` it, so the route logs the window and
    the original `keys` without parsing messages. No other exception is caught.

- [ ] **3.3 Test: reader (integration, tick test cluster)**
  - [ ] `test/integration/api_server/test_tick_bar_reads.py`. Seed with
    `seed_ticks` as `test_futures_series.py` does, plus a session with enough
    trades for several bars. Call `read_tick_bars` directly in a snapshot.
  - [ ] Bars over one window equal the cutter run on the same stored trades read
    with `read_ticks`.
  - [ ] Window independence: for several random `from`/`until` pairs (fixed
    seed), the bars equal the full read's bars filtered by `open_time` in
    `[from, until)`, including indices.
  - [ ] A read stopped at `until` fetches fewer batches than the full read:
    monkeypatch `tick_reads.TICK_GET_FETCH_ROWS` small and count `fetchmany`
    calls. Every pool connection is available afterwards.
  - [ ] A `stop` that returns true after the first batch raises `ReadAbandoned`,
    pushes no further batch, and leaves every pool connection available.
  - [ ] A sentinel-price trade raises `TickBarWindowError` whose `__cause__` is a
    `TickBarInputError`, and leaves the pool intact.

- [ ] **3.4 Add the `admit` hook to `read_series`**
  - [ ] `admit: Callable[[Conn, GetPlan], Awaitable[None]] | None = None`, called
    after `plan_for` and before the first window, inside the snapshot. `/ticks`
    and `/bars` pass none. One-line docstring entry.

- [ ] **3.5 Implement the trade-scan admission check**
  - [ ] In `tick_bar_reads.py`: sums `status_reads.ledger_totals` record counts
    over the windows `clip` selects for `[from, until)`. Read the real signature
    of `ledger_totals` and the call at `tick_coverage.py:137` first.
  - [ ] Raises an admission error carrying the sum and the limit; the route maps
    it to 422 with "the request scans {n:,} trades, over the {limit:,} trade
    limit; narrow start/end" (D5).

- [ ] **3.6 Add the setting and constant**
  - [ ] `API_MAX_TICK_BAR_TRADES: Final[int] = 2_000_000` in
    `manta_trading/constants.py` beside `API_MAX_BARS_PER_REQUEST`, with a comment
    that the default is lowered if the Phase 7 measurement (issue #75) misses 10 s.
  - [ ] `config/__init__.py`: `api_max_tick_bar_trades: int = Field(default=
    API_MAX_TICK_BAR_TRADES, gt=0)` (env `MT_API_MAX_TICK_BAR_TRADES`).
  - [ ] `app.py` lifespan sets `app.state.max_tick_bar_trades`; `deps.py` gains
    `get_max_tick_bar_trades` in the shape of `get_max_bars`.

- [ ] **3.7 Test: hook, admission and setting**
  - [ ] `test/unit/api_server/test_tick_bar_settings.py`: default equals the
    constant; `0` and a negative value fail validation; a positive override is read.
  - [ ] In `test_futures_read.py`: `admit` runs once, after the plan and before
    any window read; with `None` behavior is unchanged; an exception from `admit`
    reads no window.
  - [ ] In `test_tick_bar_reads.py`: the admission sum equals the rows
    `read_ticks` returns for the same windows, and windows outside `[from,
    until)` are not counted; over the limit it raises before any cursor opens.

**Commit**: `feat: add the tick-bar history reader, admit hook and trade limit setting`

---

## 4. Tick-bar models, parameter and routes (D1 route error, D4, D5, D7, D10)

- [ ] **4.1 Add the tick-bar models and parameter**
  - [ ] In `routes/futures_params.py` add `TicksPerBarParam`: required integer,
    `ge=1`, no upper bound, declared like the other params there.
  - [ ] In `models/futures.py`, rename the private `_envelope(plan)` to
    `plan_envelope` (it returns `target, start, end, definitions_as_of,
    segments, unresolved, incomplete`) and update its callers. New response
    models call it as `FuturesBarsResponse.from_plan` does; none re-builds those
    fields. `futures.py` is 342 lines: add nothing else to it.
  - [ ] New `models/futures_tick_bars.py`: `FuturesTickBarRecord` (fields as in
    the design's API contract; prices through `price_text`, times through
    `ts_text`) and `FuturesTickBarsResponse` (`target, ticks_per_bar, start, end,
    from, until, definitions_as_of, segments, unresolved, incomplete, count,
    bars`). `from_` with alias `from`, `populate_by_name` and `serialize_by_alias`
    as `FuturesTicksResponse` has. No `source` field.
  - [ ] The build runs in the executor like the other model builds.

- [ ] **4.2 Test: model**
  - [ ] `test/unit/api_server/test_futures_tick_bar_models.py`: a `TickBar` with
    known integers yields the expected decimal strings and nine-digit times;
    `None` quotes stay `None`; `from` serialises under that name.

- [ ] **4.3 Implement `routes/futures_tick_bars.py`**
  - [ ] `GET /api/v1/futures/contracts/{symbol}/tick-bars` and
    `GET /api/v1/futures/products/{product}/tick-bars`; parameters
    `ticks_per_bar, start, end, from, until, rule` (product), `format`, typed as
    the `/ticks` routes in `futures_series.py`.
  - [ ] Plans with `family=TICK_TIER` through the plan helpers
    `futures_series.py` already has; import them, do not copy them.
  - [ ] Calls `read_series` with `admit` bound to the admission check, the limit
    from `get_max_tick_bar_trades`, the bar ceiling from `get_max_bars` with the
    remedy "raise ticks_per_bar, or narrow start/end or from/until", the bounds,
    and `stop = request.is_disconnected`.
  - [ ] `RowCeilingExceeded` maps through `admission.over_ceiling`; the admission
    error is 422; `tick_errors` maps 404/422/503/504 as on every futures route.
  - [ ] Catches `TickBarWindowError` only: `logger.exception` at ERROR with the
    error's session, symbol, instrument and its cause's `keys`, then 500 with
    "stored ticks for session {date} of {symbol} cannot be cut into bars; see the
    server log".
  - [ ] Catches `ReadAbandoned`: returns a bodiless `Response(status_code=499)`
    and logs nothing at ERROR (the client has already gone).
  - [ ] Response via `send_timeseries` / `timeseries_200`, with the 200 schema
    published in OpenAPI (do not hide it with `response_class=Response`).
  - [ ] Include the router in `app.py` beside `futures_series`.

- [ ] **4.4 Test: routes (integration)**
  - [ ] `test/integration/api_server/test_futures_tick_bars.py`, seeded as
    `test_futures_series.py`.
  - [ ] Contract route: 200 with every D2 field; `(session_date, index)` strictly
    ascending; `count == len(bars)`; non-partial bars have `trade_count ==
    ticks_per_bar`; N above the session's trades gives one partial bar; N equal
    to it gives one full bar.
  - [ ] `ticks_per_bar` 0, -1, `abc` and missing: 422.
  - [ ] `from`/`until` bars equal the unrestricted bars filtered by `open_time`.
  - [ ] Unknown contract 404; bad range 422; tick database down 503 (reuse the
    `/ticks` tests' fixtures).
  - [ ] Product route over `seed_september_roll`: one contract per session and
    the count restarts at index 0 after the roll; unknown product 404; bad `rule`
    422; a session not load-complete is in `incomplete` and its bars are returned.
  - [ ] Admission: with a small trade limit a larger request is 422 before any
    tick is read, quoting count and limit. With a small
    `MT_API_MAX_BARS_PER_REQUEST` and `ticks_per_bar=1` the request is 422 with the
    remedy text.
  - [ ] A sentinel-price trade: 500 with the stable detail and one ERROR record in
    `caplog` holding session, symbol, instrument and both keys.
  - [ ] A `stop` that turns true after the first batch (patch what the route
    binds) answers 499 and logs no ERROR.

**Commit**: `feat: add the tick-bar models and the contract and product tick-bar routes`

---

## 5. Columnar ticks (D6)

- [ ] **5.1 Return `sequence_ordinal` from the tick read**
  - [ ] Add `sequence_ordinal` to `tick_reads._TICKS`' select list and to
    `TICK_COLUMNS`. `ORDER BY` unchanged.
  - [ ] `mt data tick get ticks` keeps its row output; update its tests only if
    the extra column shows there.

- [ ] **5.2 Implement `models/futures_ticks.py`**
  - [ ] `TickBlock`: `session_date, raw_symbol, instrument_id, count, ts_base`
    (decimal string), `ts_event` (each the delta from the previous tick, first 0), `sequence`,
    `sequence_ordinal`, `price`, `size`, `side` (one character per tick),
    `bid_px`, `ask_px`, `bid_sz`, `ask_sz` (`None` where absent).
  - [ ] `TickBlocks.from_rows(rows)`: consecutive rows of one (session,
    instrument) make one block; columns pivoted; `ts_base` is the block's first
    `ts_event`, and `ts_event[i]` is the delta from tick `i - 1` (D6), so a prefix
    sum from `ts_base` restores every timestamp; an undefined price becomes `None`; `bid_ct`/`ask_ct` dropped.
  - [ ] `FuturesTicksResponse` (columnar, moved here from `futures.py`): built
    with `plan_envelope`, plus `price_scale` (from `TICK_PRICE_SCALE`, not
    retyped) and `blocks`, one per non-empty window in plan order. The pivot runs
    in the executor.

- [ ] **5.3 Test: pivot and round trip (unit)**
  - [ ] `test/unit/api_server/test_futures_ticks_models.py`: two windows make two
    blocks, two rows of one window make one; `ts_base` is a string, deltas are
    never negative, `ts_event[0]` is 0; a `ts_event` above 2**53 survives; the
    side string's length equals `count`; bid/ask columns are `None` for
    `trades`-tier rows and a block never drops a column; an undefined price is
    `None`.
  - [ ] A test-side decoder (prefix-sum from `ts_base`, scale prices) reproduces
    the input rows exactly, `sequence_ordinal` included, for a `tbbo` and a
    `trades` block.

- [ ] **5.4 Switch both `/ticks` routes and remove the row model**
  - [ ] `routes/futures_series.py` builds the columnar response; parameters,
    plan, row ceiling, paging and error mapping unchanged.
  - [ ] Remove `TickRecord`, its `_records` use and dead helpers from
    `models/futures.py`. `grep -rn TickRecord src test docs` finds nothing.

- [ ] **5.5 Test: `/ticks` routes (integration) and the 231 load test**
  - [ ] Update the `/ticks` cases in `test_futures_series.py` (and any test
    reading `ticks[...]`) to the columnar body; segments, unresolved, incomplete,
    `count`, `from`/`until` paging, the row ceiling and error mapping still pass.
    New columnar cases go in `test_futures_ticks_columnar.py` if the file would
    pass ~450 lines.
  - [ ] Both routes: decoding the blocks through JSON and msgpack reproduces
    `read_ticks`' rows exactly. The `mt data tick get --json` comparisons still
    pass.
  - [ ] `test/load/test_231_futures_api_nfr.py` builds `FuturesTicksResponse` rows
    in `_constructed` and `test_model_construct_versus_validation`: update them
    to the columnar model (the load tier is not run here; `ruff` and mypy must
    pass on it).

**Commit**: `feat: serve /ticks as columnar blocks, drop bid_ct and ask_ct from the wire`

---

## 6. Docs, OpenAPI and CHANGELOG

- [ ] **6.1 `docs/api/reference.md`**
  - [ ] §6.1 gains: the tick-bar rule in prose (index, open/close time, OHLC by
    key order, volume, `trade_count`, buy/sell volume with side `N` in `volume`
    only, bid/ask of the last trade, `partial` only for a session's last bar with
    fewer than N trades), the incomplete-session note, admission (trades scanned,
    then bars returned; the setting `MT_API_MAX_TICK_BAR_TRADES` beside
    `MT_API_MAX_BARS_PER_REQUEST`), and the columnar decoding rule with a short
    example.
  - [ ] §6.5 and §6.7 rewritten for the columnar body.
  - [ ] New sections for the two tick-bar routes appended after §6.8; no
    renumbering.

- [ ] **6.2 `docs/api/agents.md`**
  - [ ] Rewrite "Give me futures ticks" and add "Give me futures tick bars",
    each with `<!-- endpoint: -->` markers as the file's other sections have.

- [ ] **6.3 README settings tables**
  - [ ] Add a `MT_API_MAX_TICK_BAR_TRADES` row (default `2000000`, what it
    bounds) beside the `MT_API_MAX_BARS_PER_REQUEST` row in both `README.md`
    tables (near lines 167 and 818).

- [ ] **6.4 Regenerate OpenAPI**
  - [ ] `uv run python scripts/dump_openapi.py`; `docs/api/openapi.json` shows
    `FuturesTickBarsResponse` and the columnar `FuturesTicksResponse`, with the
    200 schemas of all four tick routes.

- [ ] **6.5 CHANGELOG**
  - [ ] Unreleased entry listing the two tick-bar routes, the columnar `/ticks`
    body (marked breaking), the dropped `bid_ct`/`ask_ct`, the new setting.

- [ ] **6.6 Test: docs gates and full pass**
  - [ ] `uv run python scripts/check_api_docs.py` passes; `test_api_docs.py`,
    `test_openapi_artifact.py` and `test_api_docs_examples.py` pass (fix any
    example the old row body broke).
  - [ ] The unit tier and the `test/integration/api_server` and
    `test/integration/data` tick tests pass.
  - [ ] `grep -rnEi "row_number|ntile|width_bucket" src/manta_trading/data/tick
    src/manta_trading/api_server` finds nothing. Read every `grep -rni "group by"`
    hit in the same directories: none may select from `tick_trade` (success
    criterion 8). Every new source file is under about 300 lines.
  - [ ] The compact-size claim (about 10 B/tick gzipped) is not gated by a test.
    It is checked by hand in the design's Verification Walkthrough step 6, in
    Phase 7 with the measurement in #75.

**Commit**: `docs: document tick bars and columnar ticks, regenerate OpenAPI, update changelog`

---

## 7. Load test and measurement script

- [ ] **7.1 `test/load/test_308_tick_bars_nfr.py`**
  - [ ] Follows `test_231_futures_api_nfr.py`: the archived 2024-12-18 `tbbo` day
    (889,373 records, 687,287-tick ESZ4 session) ingested through the real pass,
    the app built with `create_app(db_url=…, tick_db_url=…)` on test-cluster
    databases only, and a gate command in the docstring. CI runs no test job, so,
    as for every other load test, the gate is the manual
    `MT_RUN_LOAD_TESTS=1` command; no CI wiring is added.
  - [ ] The `busiest` fixture is module-local to test_231. Move it to
    `test/load/conftest.py` and use it from both files; do not copy it.
  - [ ] Test 1: product ES `tick-bars` at `ticks_per_bar=1000` over that day, with
    the app's trade limit set above the day's count, completes under the
    bound (never above 10 s). This scans 889,373 records, about 44% of the
    2,000,000 default limit, so it is a proxy: the limit-level bound is checked
    only by the Phase 7 measurement (#75). `Σ trade_count` equals the ledger record count and
    `count` equals `Σ ceil(nᵢ / 1000)` per session.
  - [ ] Test 2: while that request runs, `GET /api/v1/futures/products` answers
    within 100 ms of its idle median (several probes).
  - [ ] Run it five times (`MT_RUN_LOAD_TESTS=1 uv run python
    scripts/run_tests.py load -- test/load/test_308_tick_bars_nfr.py`). Set the
    bound to about twice the median, and record the five timings, the median and
    the date in the docstring beside it. If the median is already over 10 s, the
    scan is too slow for the default: lower `API_MAX_TICK_BAR_TRADES` and re-run.

- [ ] **7.2 `scripts/measure_tick_bars.py`, with its tests**
  - [ ] A small script for the Phase 7 step of success criterion 12 (run against
    production after release, tracked by #75; not run in Phase 6). Inputs: base
    URL, product, `ticks_per_bar`, the trade limit, and the JSON of `mt data tick
    coverage --product P --start … --end … --json` (read `tick_coverage.py` for
    its keys first).
  - [ ] `pick_range(rows, limit)`: the run of consecutive sessions whose record
    counts sum to 95-100% of `limit`; raises when none exists.
  - [ ] `check_totals(bars, rows)`: per session `Σ trade_count` equals the ledger's
    record count, `Σ volume` its volume, and the bar count equals `Σ ceil(nᵢ / N)`.
  - [ ] Times the request, probes `GET /api/v1/futures/products` idle and during
    it, and writes `user/notes/308-tick-bar-measurement.md` (range, ledger sum,
    timings, checks).
  - [ ] `test/unit/test_measure_tick_bars.py` with synthetic ledger rows:
    `pick_range` returns a run in the band and raises when none exists;
    `check_totals` accepts matching totals and names the session on a mismatch.

**Commit**: `test: add the tick-bar load test and measurement script`
