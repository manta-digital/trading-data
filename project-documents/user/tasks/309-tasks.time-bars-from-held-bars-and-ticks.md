---
docType: tasks
slice: time-bars-from-held-bars-and-ticks
project: trading-data
lld: user/slices/309-slice.time-bars-from-held-bars-and-ticks.md
parent: user/architecture/300-slices.futures-historical-bars-statistics-and-tick-aggregation.md
dependencies:
- 302
- 308
interfaces: []
projectState: Slice design committed and its review answered. The futures bars routes
  and `mt data tick get <size>` serve provider `ohlcv-1m` bars only (slice 306), plan
  with the BAR_1M family and take one of six sizes. Production holds no provider bars,
  so both routes return nothing. `bar_parity.py` holds the tick-minute statement at
  one size; `mt data tick parity` runs it. The production universe wants ES and GC
  history (definition, statistics, ohlcv-1m) back to 2010.
dateCreated: 20261115
dateUpdated: 20261115
status: not_started
---

# Tasks: Time Bars from Held Bars and Ticks

## Context Summary

- Working on slice 309. After it, every futures time bar is cut from ticks unless the
  session is held complete as provider bars that can make the size asked for; the
  response says which source each run of sessions used.
- Rule (PM, journal 20261115): ticks and bars of N trades come from ticks (unchanged);
  time bars come from held provider bars where they can make the size, else from ticks.
- Prerequisites: 302 (bar table, ledger, session verdicts) and 308 (tick-bar cutter,
  `read_series` with `admit`, `trade_admission`, the 499 and 500 mappings) are merged.
- Tooling: `uv run --extra dev ruff format <touched files>` and `ruff check <touched
  files>` (never a directory: it rewrites whole files; grep `git diff` for deletions
  before each commit); `mypy` per touched subpackage; integration tests need
  `MT_TIMESCALE_TEST_URL` exported from `.env` (strip the quotes). Run the unit, integration
  and load tiers separately.
- Design vocabulary used below: **held** = the session's status for that data kind is
  `complete` (`verdicts_for(family)`); nothing new is defined. **Tick-cut** = a time bar
  built from `tick_trade`. Names of new modules are suggestions; keep each file under
  about 300 lines.
- Not tasks (Phase 7 and the `/release` skill): merge, push, release, deploy, the
  production check of design criteria 1 and 7, closing issue #40, and telling trading-ui
  and trading-engine (criterion 8). Nothing here merges or pushes.
- Next planned slice: the equity aggregates work (issue #38).

## 1. Bar size grammar and the provider-width rule

- [ ] **1.1 `BarSize` and its parser**
  - [ ] New module `src/manta_trading/data/tick/time_bar_size.py`. A frozen `BarSize`
    holds `token` (normalized text, e.g. `90m`), `width_ns` (`None` for the session day)
    and `session_day: bool`.
  - [ ] `parse_bar_size(text) -> BarSize`: strip and lowercase, then accept `<n>s`,
    `<n>m`, `<n>h` (whole `n >= 1`) and exactly `1d`. The unit table (suffix to
    nanoseconds) and the 24-hour ceiling are named constants in this module, each defined
    once.
  - [ ] Raise `ValueError` for zero, a negative or fractional `n`, an unknown unit, an
    empty string, or a width over 24 hours (`25h`, `2d`, `1441m`). The message names the
    accepted form ("a whole number of seconds, minutes or hours up to 24h, or 1d").
    `24h` is accepted.
  - [ ] The six sizes served before (`1m 5m 15m 1h 4h 1d`) parse with their spelling
    unchanged.
- [ ] **1.2 Provider-width rule in `bar_constants.py`**
  - [ ] Replace nothing yet. Add `PROVIDER_BAR_WIDTH_NS: Mapping[TickSchema, int]` with the
    one entry `OHLCV_1M: 60 s`, and `TICKS_SOURCE = "ticks"` (the response's source label
    for tick-cut bars; a provider source is its `TickSchema.value`).
  - [ ] `provider_schema_for(size: BarSize) -> TickSchema | None` in `time_bar_size.py`
    (or beside the table): the schema whose width divides `size.width_ns`
    (`size.session_day` is divisible by every provider width); with several, the widest;
    `None` when none divides (any size in seconds that is not a whole minute).
- [ ] **1.3 Tests** (`test/unit/data/tick/test_time_bar_size.py`)
  - [ ] Parametrized accepted forms: `1s 15s 1m 5m 90m 4h 24h 1d`, and `" 5M "` normalizes
    to `5m`. Parametrized refusals: `0s -1m 1.5h 5x "" 25h 2d 1441m 86401s`, each asserting
    the accepted-form text appears in the message.
  - [ ] `provider_schema_for`: `1m 5m 90m 4h 1d 120s` give `ohlcv-1m`; `1s 15s 90s` give
    `None`.

**Commit**: `feat: add the time-bar size grammar and the provider-width rule`

## 2. The tick-cut statement in its own module

- [ ] **2.1 Move the statement and give it an interval**
  - [ ] New module `src/manta_trading/data/tick/time_bar_cut.py` with
    `cut_time_bars(conn, window, size: BarSize, on_batch) -> int`: the statement of
    `bar_parity._TICK_MINUTES` with the interval a parameter. Bucket by the same
    `SESSION_BUCKET` and `bucket_params` (imported from `bar_reads`; make the private
    `_DAY_BUCKET` public as `DAY_BUCKET` and use it for `session_day`). First price and
    last price by the full tick order (`ts_event, sequence, sequence_ordinal`), `max`,
    `min`, `sum(size)::bigint`; empty buckets produce no row.
  - [ ] Output columns are `TIME_BAR_CUT_COLUMNS = BAR_COLUMNS + ("trade_count",)`:
    `count(*)` per bucket is the new last column (task 4.2 checks it against the ledger).
    `1d` is one row stamped at the session open.
  - [ ] `bar_parity.read_tick_minute_bars` calls `cut_time_bars` at one minute and drops
    the trailing `trade_count` before `on_batch`, so its rows and the classify code are
    unchanged. Delete `_TICK_MINUTES` from `bar_parity.py`. One implementation remains.
  - [ ] Update the `bar_parity.py` module docstring: it no longer says "the only
    surviving use of bars built from `tick_trade`".
- [ ] **2.2 Tests against real held ticks** (`test/integration/data/test_time_bar_cut.py`)
  - [ ] Use the real ingested `tbbo` day (`TBBO_DAY`, seeded as `test/tick_support/parity_seed.py`
    and `tier_units.seed_tier_unit` do). Compute expectations in the test, in Python,
    from `SELECT ... FROM tick_trade` rows: bucket, first/last by the full key, max, min,
    sum, count. The bucket formula has no Python copy in `src`; the test owns its own.
  - [ ] Design criterion 2: at `1m`, the same count of non-empty minutes and the same
    open, high, low, close, volume per minute as the hand sums.
  - [ ] Same comparison at `15s`, `5m`, `1h` and `4h` (4-hour buckets start at the session
    open, 17:00 Chicago), plus `1d`: one row whose volume equals the day's total size and
    whose open and close are the day's first and last trade.
  - [ ] `trade_count` summed over rows equals the day's trade count at every size.
  - [ ] The existing `test/integration/data/test_bar_parity_sql.py` (ties at the close,
    volume, other instrument, empty window) passes unchanged: it now exercises the moved
    statement through `read_tick_minute_bars`.

**Commit**: `refactor: move the tick-cut statement to its own module with any interval`

## 3. Source choice per session

- [ ] **3.1 `choose_sources`** (new `src/manta_trading/data/tick/time_bar_source.py`)
  - [ ] `choose_sources(conn, calendar_url, plan, size) -> dict[date, str | None]`, keyed
    by the plan's session dates in plan order (a product plan has one contract per
    session, so the date is unique). Build the planned `Session` objects as
    `parity_plan.parity_inputs` does (`sessions_dated`, filtered to the planned windows)
    and get the bar verdicts with `verdicts_for(SchemaFamily.BAR_1M)`. Do not call
    `universe_entry_of`: take the entry from `plan.target.product` (the lookup in
    `tick_bar_reads._entry`; make it public and reuse it).
  - [ ] Rule per session: provider schema if `provider_schema_for(size)` is not `None`
    and the bar verdict is `complete`; else `TICKS_SOURCE` unless the tick verdict is
    `missing`, in which case `None` (nothing is read; it is already in `incomplete`).
    A tick-sourced session in any other non-complete tick state is still read (design:
    "served from the ticks that are loaded").
  - [ ] `served_plan(plan, sources) -> GetPlan`: the plan with `incomplete` reduced to the
    sessions not served from a provider schema (a provider-served session is held, so it
    is not incomplete). Everything else is the original plan. Both the API and the CLI use
    it, so the lists agree.
- [ ] **3.2 `source_runs`** (same module, pure)
  - [ ] `SourceRun(first_session, last_session, source)` and `source_runs(plan, sources,
    returned_sessions) -> list[SourceRun]`: walk the plan's windows in order; a run is
    consecutive planned sessions that returned at least one bar and share a source; a
    planned session that returned no bars ends the run. The entries cover exactly the
    sessions that returned bars.
- [ ] **3.3 Tests**
  - [ ] Unit (`test/unit/data/tick/test_time_bar_source.py`) for `source_runs` and
    `served_plan` with hand-built plans: one source across the range gives one run; a
    switch from `ohlcv-1m` to `ticks` gives two runs; a session with no bars splits runs
    of the same source; a provider-served session leaves `incomplete`, a tick-served
    partial one stays.
  - [ ] Integration (`test/integration/data/test_time_bar_source.py`) on
    `test/tick_support/parity_seed.py`'s fixture (the real tick day complete in both
    families): `5m` chooses `ohlcv-1m`, `15s` chooses `ticks`; with the bar day made
    partial (an unverified or missing unit) `5m` falls through to `ticks`; a session in
    neither family chooses `None` and remains in `incomplete` as `missing`; ticks partly
    loaded with no bars chooses `ticks` and keeps its tick status.

**Commit**: `feat: choose the source of each session's time bars`

## 4. The reader, its admission and the response shape

- [ ] **4.1 Provider reader takes a `BarSize`**
  - [ ] `bar_reads.read_provider_bars(conn, window, size: BarSize, on_batch)`: width equal
    to the provider width reads the stored rows, `session_day` uses `DAY_BUCKET`, any other
    divisible width uses `SESSION_BUCKET`. It no longer takes a `Granularity` or imports
    `GRANULARITY_BAR_MINUTES`. Rewrite the module docstring: it drops "ticks are never read
    here: no provider bars, no bars".
  - [ ] Update `test/integration/data/test_bar_reads.py` and `test_bar_reads_sessions.py`
    for the new argument; their expectations (rollups, `1d` stamped at the open) stay.
    Add `90m` (a size that was not served before) rolled from the stored minutes.
- [ ] **4.2 Trade admission for tick-cut sessions**
  - [ ] In `tick_bar_reads.py` split `trades_to_scan` so a helper
    `trades_in_windows(conn, plan, windows)` returns the ledger's record count for the
    given windows; `trades_to_scan` calls it with the overlapping windows, unchanged
    behaviour. The tick-bar tests pass untouched.
  - [ ] Setting `api_max_time_bar_trades` (`MT_API_MAX_TIME_BAR_TRADES`) beside
    `api_max_tick_bar_trades` in `config/__init__.py`, default constant
    `API_MAX_TIME_BAR_TRADES` in `constants.py`, a `get_max_time_bar_trades` dependency in
    `api_server/deps.py` and `app.state.max_time_bar_trades` in `app.py`, all following the
    308 limit exactly. The default is provisional: set it equal to
    `API_MAX_TICK_BAR_TRADES` and say so in its docstring; task 6.2 replaces it with the
    measured value.
  - [ ] `TimeBarScanExceeded(scanned, limit)` with the message "the request reads
    {n:,} trades to cut {size} bars, over the {limit:,} trade limit; narrow start/end".
  - [ ] Tests: unit for the setting default and env override (as `test_tick_bar_settings.py`);
    integration that the count excludes provider-served and `None` sessions and that a
    session with no ledger row adds nothing.
- [ ] **4.3 The per-request reader** (new `src/manta_trading/data/tick/time_bar_reads.py`)
  - [ ] One object per request, `TimeBarRead(size, calendar_url, max_trades, stop)` (`max_trades=None` means no
    admission, for the CLI), with
    `async admit(conn, plan)` (same shape as `Admit`): runs `choose_sources` in the read's
    snapshot, stores the result, raises `TimeBarScanExceeded` when the trades of the
    tick-sourced windows exceed the limit, before any tick is read. And
    `async read(conn, window, on_batch) -> int` (the `ReadWindow` shape): awaits `stop()`
    before each window and raises the existing `ReadAbandoned` when the client has gone;
    reads `None` windows as nothing; provider windows through `read_provider_bars`;
    tick windows through `cut_time_bars`.
  - [ ] Ledger guard: for a tick-cut window, sum the cut's `trade_count` column, strip it
    before passing rows on, and compare with that session's ledger count
    (`status_reads.ledger_totals` for the one session). More trades than the ledger says
    raises `LedgerCountExceeded(session_date, raw_symbol, instrument_id, ledger, read)`.
    A smaller count is not an error (a partial load).
  - [ ] Expose the stored choices as `sources` after the read, for `served_plan` and
    `source_runs`.
- [ ] **4.4 Tests** (`test/integration/data/test_time_bar_reads.py`)
  - [ ] Through `read_series(..., admit=reader.admit)` on the seeded fixture: `5m` returns
    provider rows and `15s` returns tick-cut rows for the same session (design criterion 3);
    a mixed range (one session with bars held, one without) returns both, each
    from its own source; a limit below the scan raises `TimeBarScanExceeded` and
    `tick_trade` is never queried (assert with a stub `read`/spy on the connection or by
    checking no rows were collected).
  - [ ] `stop` returning true before the second window raises `ReadAbandoned` after the
    first was read.
  - [ ] Ledger guard: edit the fixture's ledger count down by one (in the throwaway test
    database only) and expect `LedgerCountExceeded` naming the session.
- [ ] **4.5 Response models** (`api_server/models/futures.py`)
  - [ ] `FuturesBarsResponse`: remove `source: str`; add `sources: list[SourceRunRecord]`
    (`first_session`, `last_session`, `source`) and `applied: AppliedBarParams`
    (`granularity`, `rule` only for a product request and omitted for a contract, `start`,
    `end`, `format`). `applied` carries what the server used, including defaulted values:
    the parsed rule text when none was given, the plan's resolved `start` and `end`
    (null only when nothing is held and no range was given), the normalized size token,
    the response format.
  - [ ] `from_plan` takes the served plan (task 3.1), the `BarSize`, the runs, the applied
    values and the rows. The `BAR_COLUMNS` mapping and the bar record are unchanged.
  - [ ] Update `test/unit/api_server/test_futures_models.py`: `sources` replaces `source`;
    `applied` includes defaulted parameters; a contract response has no `rule` key.

**Commit**: `feat: read futures time bars from provider bars or ticks per session`

## 5. Routes, CLI and load check

- [ ] **5.1 Routes** (`api_server/routes/futures_series.py`, `futures_params.py`)
  - [ ] `granularity` becomes a plain string query parameter whose description gives the
    grammar. Parse with `parse_bar_size` before any read; a `ValueError` is an
    `HTTPException` 422 carrying its message (as `parse_rule` does). Remove the
    `TickBarGranularity` enum and its use.
  - [ ] Both bars routes plan with `SchemaFamily.TICK_TIER` (their default range is the held
    ticks), build one `TimeBarRead` per request, call `read_series(..., admit=reader.admit)`,
    and map: `TimeBarScanExceeded` to 422, `RowCeilingExceeded` to the existing 422,
    `ReadAbandoned` to the bodiless 499 (reuse `CLIENT_CLOSED_REQUEST` and the 308
    handling, moved to one shared place if needed), `LedgerCountExceeded` to a logged `500`
    naming the session ("see the server log", as `_uncuttable` does), a cancelled statement
    to the existing 504 through `tick_errors`.
  - [ ] Build the response model in the executor (`run_in_executor`), as the ticks and
    tick-bar routes do: a 1-second request returns tens of thousands of rows and
    validating them blocks the loop. Add the new limit parameter dependency to the routes.
  - [ ] Update `_BARS_RESPONSES` and the route docstrings: "provider bars" becomes "time
    bars from held bars or ticks".
- [ ] **5.2 Route tests** (`test/integration/api_server/`)
  - [ ] Replace `test_futures_bars_provider.py` with `test_futures_time_bars.py` (delete the
    old file: its "no provider bars, no bars" tests are the ones removed by this slice).
    Cover on the seeded fixture: `1h`, `1m`, `15s`, `1d` return bars with `sources` naming
    `ticks` and `applied` listing every parameter (design criterion 1 at test scale); `5m`
    with provider bars held names `ohlcv-1m`; a mixed range returns two `sources` runs; a
    session with neither returns no bars and is in `incomplete` (criterion 4); `0s`, `2d`
    and `5x` return 422 naming the accepted form; a trade limit below the scan returns 422
    with the count before any tick is read; a disconnect gives 499 with no body; a forced
    cancelled statement gives 504 (find how `test_futures_series.py` or `test_tick_pool.py`
    forces it and reuse that); the ledger guard gives 500 and a log record naming the
    session.
  - [ ] Both the contract route and the product route are exercised for the success case.
  - [ ] Update `test_futures_series.py` and `test/integration/test_operations_serving.py`
    where they assert provider-only bars.
  - [ ] Design criterion 6: `test_futures_tick_bars.py`, `test_futures_ticks_columnar.py`,
    `test_tick_bar_reads.py` and `test/unit/data/tick/test_tick_bars*.py` pass unchanged.
- [ ] **5.3 `mt data tick get <size>`** (`cli/commands/tick_get_cmds.py`,
  `tick_get_writers.py`)
  - [ ] `WHAT` is `ticks` or a size accepted by `parse_bar_size`; anything else refuses with
    the accepted-form message. `family_for` and `bar_source` go away: ticks and sizes both
    plan `TICK_TIER`.
  - [ ] For a size, `_stream` builds the same `TimeBarRead` with `max_trades=None` (no admission:
    an operator may read what the server refuses), runs its `admit` once for the choices,
    reads each window through it, prints the `served_plan` lists, and tracks the sessions that returned
    rows.
  - [ ] Writers: `source` is replaced by `sources`, printed after the rows (text footer,
    CSV on stderr, JSON tail member) from `source_runs`; `partial` prints the runs for the
    rows written. Update `GET_EPILOG` ("Bars are cut from ticks unless provider bars that
    can make the size are held; the output names the source of each run of sessions").
  - [ ] Exit codes and the partial handling are unchanged.
- [ ] **5.4 CLI tests** (`test/integration/data/test_tick_get_cli.py`,
  `test/unit/cli/commands/test_tick_get_writers.py`)
  - [ ] Replace the tests asserting provider-only output. New: `mt data tick get 15s` on the
    fixture prints rows and `ticks` as its source in text, CSV and JSON; `5m` prints
    `ohlcv-1m`; an invalid size exits with the preflight code; a session with no data exits
    with the partial code and lists it.
  - [ ] Writers unit test: the sources footer for text, CSV and JSON, including a partial
    run.
- [ ] **5.5 Load check** (`test/load/test_309_time_bars_nfr.py`)
  - [ ] Follow `test_308_tick_bars_nfr.py` (same gate line, same helpers, test-cluster
    databases only): on the busiest real session, a product `15s` request (about 82,000
    bars) completes under a bound set from five timed runs (record the five timings, the
    median and the date in the docstring, bound about twice the median), and a probe of
    `GET /api/v1/futures/products` during the request stays within the 308 starvation
    margin of its idle median. This is the check that the model build runs off the loop.

**Commit**: `feat: serve futures time bars and mt data tick get from held bars or ticks`

## 6. The trade limit, measured on production

- [ ] **6.1 `scripts/measure_time_bars.py`, with its tests**
  - [ ] Modelled on `scripts/measure_tick_bars.py` (read it first). Run from the dev checkout
    with `uv run python`; database URLs from `Settings`, never `os.environ`. It opens a
    read-only transaction on the production tick database with `TICK_JIT_OPTION`, builds the
    product plan the route builds (`product_plan` over `TICK_TIER`), and times
    `cut_time_bars` itself (no API, nothing deployed). Writes nothing to any database.
  - [ ] Measures (a) one median ES session at `1m` (the session whose ledger count is the
    median of the held sessions), and (b) the widest run of consecutive sessions whose ledger
    counts sum to 95-100% of `--limit`, cut at `1m`, session by session in the route's order.
    Reuse `pick_range` from `measure_tick_bars.py` (import it, or move it to a shared module
    both scripts import; do not copy it). Check that per session `sum(trade_count)` equals the
    ledger count.
  - [ ] Writes `user/notes/309-time-bar-measurement.md`: range, ledger sum, the timings, the
    checks, the default chosen.
  - [ ] `test/unit/test_measure_time_bars.py`, with synthetic ledger rows, using
    `tick_support/measure_script.py` as `test_measure_tick_bars.py` does: the median session
    is chosen correctly for odd and even counts; the totals check accepts matching counts and
    names the session on a mismatch.
- [ ] **6.2 Run it and set the default**
  - [ ] Run `measure_time_bars.py --product ES --limit 2000000 --start <first held ES session>
    --end <day after the last>` against production. Rule for the default: keep 2,000,000 when
    the measured range at the limit completes within 10 s (the bound slice 308 used). If it
    does not, set the limit to `floor(2,000,000 × 10 / seconds)` rounded down to a round
    number, and re-run at that limit; repeat until it holds. The default is never raised.
  - [ ] Set `API_MAX_TIME_BAR_TRADES` to the result; replace the provisional docstring with
    the measurement (date, range, seconds) and `MT_API_MAX_TIME_BAR_TRADES`. Update the
    default in the settings test.
  - [ ] Design criterion 5 evidence: the note records the default and the two measured times
    (one ES session at `1m`; the widest range the default admits).
  - [ ] If the tick database is unreachable from this checkout, write a line starting with
    `BLOCKED:` and the reason here, leave this task unchecked, and stop.

**Commit**: `feat: set the time-bar trade limit from a production measurement`

## 7. Remove the provider-only rule, the parity command and the history wants

Each removal follows a green verification and has its own marker, so a removal can be undone.

- [ ] **7.1 Remove the provider-only machinery**
  - [ ] Verification first: `grep -rn` for `BAR_SOURCE`, `TICK_BAR_GRANULARITIES`,
    `TICK_GET_CHOICES`, `family_for`, `bar_source`, `TickBarGranularity` and `Granularity` as
    a bar-size type across `src`, `scripts`, `test`. Each remaining user is a task 4-5 miss:
    fix it, do not keep the symbol.
  - [ ] Delete `BAR_SOURCE` (`bar_constants.py`), `TICK_BAR_GRANULARITIES` and
    `TICK_GET_CHOICES` (replace the latter with the `ticks` choice only if the CLI still
    needs the constant), and `test/unit/data/tick/test_bar_source.py` (it asserts the fixed
    table). Delete any remaining test asserting that bars are never built from ticks.
  - [ ] Remove the 306 wording from docstrings and comments still saying provider-only
    ("no provider bars, no bars", "never built from ticks", 306 D2) in `src`.
  - [ ] Kept as is: `SESSION_BUCKET`, the source label, `read_provider_bars`, the
    `TICK_TIER`/`BAR_1M` plumbing that slice 308 uses.
  - [ ] Run the unit tier for `data/tick`, `api_server` and `cli`, then the integration
    files of tasks 2 to 5; all pass.
- [ ] **7.2 Remove `mt data tick parity`** (comparison code stays)
  - [ ] Delete `cli/commands/tick_parity_cmds.py` and `tick_parity_render.py`, the `parity`
    registration and name in `cli/commands/tick.py` (including the list of command names in
    its help text), `data/tick/parity_plan.py`, and the command's tests
    (`test/integration/data/test_tick_parity.py`, `test_tick_parity_exit.py`, the parity
    lines of `test/unit/cli/commands/test_data_tick.py`).
  - [ ] `test/tick_support/parity_seed.py` stays only if task 3 and 4 tests use it; remove
    `run_parity` and anything else with no user left.
  - [ ] Keep `bar_parity.py`, `bar_parity_report.py` and their tests
    (`test_bar_parity_classify.py`, `test_bar_parity_report.py`, `test_bar_parity_sql.py`)
    untouched, and leave their docstrings saying that the command was removed in 309 and the
    automatic comparison is Future Work ("Automatic minute comparison", slice plan 300).
  - [ ] `mt data tick --help` no longer lists `parity`; the tests of the help text pass.
- [ ] **7.3 Production wants no history**
  - [ ] `data/tick/universe.py`: delete `_HISTORY_START`, `_HISTORY_END`, `_HISTORY_WANTS`
    and the `history=` argument of both entries, and rewrite the comment block (ES and GC
    want the tick tier and its definitions; the history mechanism, `HistoryWant` and its
    validation stay for the day provider bars are bought). Do not touch the planner, the
    purchase code or the 304 scripts: they iterate `entry.history`, which is now empty.
  - [ ] Fix every test that reads the production universe's history: grep `TICK_UNIVERSE`
    with `history` in `test/` (known: `test_universe.py`, `test_availability_span.py`,
    `test_purchase_verdicts.py`, `test_cutover_304_*`). Tests of the history mechanism build
    their own `TickUniverseEntry` (as `test_planner.py` does); change the ones that took
    production's wants as a fixture to do the same. A test asserting production's wants
    becomes one asserting `history == ()` for every entry.
  - [ ] Confirm the rows disappear because they are printed per want: run the unit and
    integration tests of `mt data health`, `accounting`, `status` and the overview
    (`test_health_tick.py`, `test_tick_overview.py`, `test_family_report.py`,
    `test_overview_gather.py`, `test_operations_serving.py`) and fix expectations that
    counted bar or statistics rows.
  - [ ] Design criterion 7 at test scale: `mt data health` on a test database that holds
    ticks shows no bar or statistics row and no new failure.

**Commit**: `refactor: remove the provider-only bar rule, mt data tick parity and the history wants`

## 8. API documents, OpenAPI and changelog

- [ ] **8.1 Documents**
  - [ ] `docs/api/reference.md` (about lines 327-373, 427, 729-731, 744-770, 1498-1520,
    1771-1990), `docs/api/agents.md` (about lines 369-373, 427) and `README.md`: replace
    every statement that time bars are provider-only, "No provider bars, no bars", the
    six-size menu, the `source` string and "always `ohlcv-1m`", with the new rule, the
    grammar (any whole number of seconds, minutes or hours up to 24h, and `1d`), the 422 for
    other sizes, `sources` (a list; example with two runs), `applied` (with the
    defaulted-parameters example), the cost note and the new `MT_API_MAX_TIME_BAR_TRADES`
    row beside the 308 one, the 422/499/500/504 behaviour of tick-cut reads, and the removal
    of `mt data tick parity`. Also drop the `ohlcv-1m`/`statistics` family rows that appear
    only in examples of production wants. Say in the changed text why `source` became
    `sources` without a version change (the routes returned nothing in production and no
    consumer had coded against them).
  - [ ] Regenerate `docs/api/openapi.json` the way slice 308 did (find the generator or test
    in `test_openapi_artifact.py` and use it); the artifact test passes.
  - [ ] Run `test/unit/api_server/test_api_docs.py` and `test/integration/test_api_docs_examples.py`;
    update the examples they check.
- [ ] **8.2 Changelog**
  - [ ] `CHANGELOG.md` under `[Unreleased]`: a **Changed** entry (futures time bars at any
    whole second, minute or hour size and `1d`, from held provider bars or ticks, with
    `sources` and `applied`; `source` replaced by `sources`), a **Fixed** entry (the bars
    routes and `mt data tick get` returned nothing in production since 0.27.0), and a
    **Removed** entry (`mt data tick parity`; production history wants). Written from the
    user's side, as the existing entries are. Released entries are not edited.

**Commit**: `docs: document futures time bars from held bars and ticks, regenerate OpenAPI, update changelog`

## 9. Cleanup of other slices' documents and unrelated issues (its own commit)

- [ ] **9.1 Slice and task files**
  - [ ] In `user/slices/` and `user/tasks/` for 302, 303, 304, 305 and 308 (and the 300
    architecture only where it still contradicts the ticks-primary rule), find sentences
    that say time bars are provider-only, that ticks never make time bars, or that history to
    2010 is to be bought (`grep -n -iE` for `provider-only|never built from ticks|no provider
    bars|2010|ohlcv-1m`). Reword each to what is now true or mark it as superseded by 309.
    Facts about what was built stay. Do not edit 309's own files or any `## User-Provided
    Concept` section.
- [ ] **9.2 Issues**
  - [ ] Close #69 and #74 with `gh issue close <n> --comment`, each comment naming the commit
    of task 7.3 (the production wants are gone, so the invoice audit of a purchase that never
    ran and the bounds at 2010-purchase scale no longer apply). Issues #70, #71 and #40 stay
    open here: #70 and #71 apply when provider bars are held; #40 is closed in Phase 7 after
    the release serves 1-second bars.

**Commit**: `docs: reword 302-308 documents for the ticks-primary rule`

## 10. Full validation

- [ ] **10.1 Tiers and checks**
  - [ ] `ruff format --check` and `ruff check` on the touched files; `mypy` on the touched
    subpackages (`data/tick`, `api_server`, `cli`, `config`) and `test/` helpers, zero errors.
  - [ ] Unit tier green. Integration tier green apart from known failures that exist on the slice base
    (`test_policy_advances_head.py`'s two unaided tests, the migration 051/052 chain tests,
    a priority1 list mismatch, 'tuple concurrently deleted' races); re-run any other
    failure in isolation, and compare against the slice base, before investigating.
    Load tier: the new file and `test_308_tick_bars_nfr.py` and `test_231_futures_api_nfr.py`
    pass (the full tier takes about 77 minutes; do not cap it under 90).
  - [ ] Design criterion 6 confirmed: the tick-bar, raw-tick and `ticks` CLI tests pass with
    no edits to those files (`git diff main --stat` on them is empty).
  - [ ] `git diff main` has no unintended deletions in files `ruff` touched.
  - [ ] Every design criterion that Phase 6 can verify (2, 3, 4, 5, 6, and 7 at test scale)
    names its test in the commit history above; criteria 1 (production), 7 (production) and 8
    are the Phase 7 steps listed in the Context Summary.
  - [ ] Working tree clean; the work is committed on the slice branch (no merge, no push).
