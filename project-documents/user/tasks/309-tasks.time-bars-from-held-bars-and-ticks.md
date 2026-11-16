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
status: in_progress
turns: 71
promptTokens: 7681211
cachedTokens: 7389751
cacheWriteTokens: 291344
completionTokens: 88834
durationSeconds: 651.6
costUsd: 2.793463
usageRounds: 4
pricedRounds: 4
usageByModel:
  claude-sonnet-5-5:
    rounds: 4
    costUsd: 2.7934637999999987
    completionTokens: 88834
revision_number: 4
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
- Every commit marker below leaves the tree green. The trade limit is measured (section 3)
  before anything reads it, so no later commit carries an unmeasured default.
- Merge, push, release, deploy and the production checks are not tasks; the closing section
  "Phase 7 handoff" names who does them and what. Nothing here merges or pushes.
- Next planned slice: the equity aggregates work (issue #38).

## 1. Bar size grammar and the provider-width rule

- [x] **1.1 `BarSize` and its parser**
  - [x] New module `src/manta_trading/data/tick/time_bar_size.py`. A frozen `BarSize`
    holds `token` (normalized text, e.g. `90m`), `width_ns` (`None` for the session day)
    and `session_day: bool`.
  - [x] `parse_bar_size(text) -> BarSize`: strip and lowercase, then accept `<n>s`,
    `<n>m`, `<n>h` (whole `n >= 1`) and exactly `1d`. The unit table (suffix to
    nanoseconds) and the 24-hour ceiling are named constants in this module, each defined
    once.
  - [x] Raise `ValueError` for zero, a negative or fractional `n`, an unknown unit, an
    empty string, or a width over 24 hours (`25h`, `2d`, `1441m`). The message names the
    accepted form ("a whole number of seconds, minutes or hours up to 24h, or 1d").
    `24h` is accepted.
  - [x] The six sizes served before (`1m 5m 15m 1h 4h 1d`) parse with their spelling
    unchanged.
- [x] **1.2 Provider-width rule in `bar_constants.py`**
  - [x] Replace nothing yet. Add `PROVIDER_BAR_WIDTH_NS: Mapping[TickSchema, int]` with the
    one entry `OHLCV_1M: 60 s`, and `TICKS_SOURCE = "ticks"` (the response's source label
    for tick-cut bars; a provider source is its `TickSchema.value`).
  - [x] `provider_schema_for(size: BarSize) -> TickSchema | None` in `time_bar_size.py`
    (or beside the table): the schema whose width divides `size.width_ns`
    (`size.session_day` is divisible by every provider width); with several, the widest;
    `None` when none divides (any size in seconds that is not a whole minute).
- [x] **1.3 Tests** (`test/unit/data/tick/test_time_bar_size.py`)
  - [x] Parametrized accepted forms: `1s 15s 1m 5m 90m 4h 24h 1d`, and `" 5M "` normalizes
    to `5m`. Parametrized refusals: `0s -1m 1.5h 5x "" 25h 2d 1441m 86401s`, each asserting
    the accepted-form text appears in the message.
  - [x] `provider_schema_for`: `1m 5m 90m 4h 1d 120s` give `ohlcv-1m`; `1s 15s 90s` give
    `None`.

**Commit**: `feat: add the time-bar size grammar and the provider-width rule`

## 2. The tick-cut statement in its own module

- [x] **2.1 Move the statement and give it an interval**
  - [x] New module `src/manta_trading/data/tick/time_bar_cut.py` with
    `cut_time_bars(conn, window, size: BarSize, on_batch) -> int`: the statement of
    `bar_parity._TICK_MINUTES` with the interval a parameter. Bucket by the same
    `SESSION_BUCKET` and `scan_params` (imported from `bar_reads`; make the private
    `_DAY_BUCKET` public as `DAY_BUCKET` and use it for `session_day`). First price and
    last price by the full tick order (`ts_event, sequence, sequence_ordinal`), `max`,
    `min`, `sum(size)::bigint`; empty buckets produce no row.
  - [x] Output columns are `TIME_BAR_CUT_COLUMNS = BAR_COLUMNS + ("trade_count",)`:
    `count(*)` per bucket is the new last column (task 5.3 checks it against the ledger).
    `1d` is one row stamped at the session open.
  - [x] `bar_parity.read_tick_minute_bars` calls `cut_time_bars` at one minute and drops
    the trailing `trade_count` before `on_batch`, so its rows and the classify code are
    unchanged. Delete `_TICK_MINUTES` from `bar_parity.py`. One implementation remains.
  - [x] Update the `bar_parity.py` module docstring: it no longer says "the only
    surviving use of bars built from `tick_trade`".

  Note: bounds are applied by scan_params: buckets anchored at the session open, a bar selected by its start and kept whole (routes in 6.3 pass windows unclipped, as 308 does).
- [x] **2.2 Tests against real held ticks** (`test/integration/data/test_time_bar_cut.py`)
  - [x] Design criterion 2 says "summed by hand". The oracle is that sum: `tick_minutes`
    buckets the tick rows in Python, independently of the SQL, so no manual sum is made.
  - [x] Use the real ingested `tbbo` day (`TBBO_DAY`), seeded as `test/tick_support/parity_seed.py`
    does. Reuse its Python bucketing oracle `tick_minutes(url, window)`: give it a
    `width_ns` parameter (default one minute, so its current callers are unchanged) rather
    than writing a second oracle. For `1d` pass a width longer than the session.
  - [x] Design criterion 2: at `1m`, the same count of non-empty minutes and the same
    open, high, low, close, volume per minute as the oracle.
  - [x] Two more sizes, each for a path `1m` does not reach: `4h` (buckets start at the
    session open, 17:00 Chicago, not on a UTC multiple) and `1d` (the day bucket, one row).
  - [x] `1s` (issue #40): on a five-minute window inside the day (pass `from`/`until`
    bounds to the window), the bars equal the oracle at a width of one second: one bar per
    second that has a trade, none for an empty second.
  - [x] `trade_count` summed over rows equals `SELECT count(*)` for the day, at each size.
  - [x] A bar never crosses a session (the design's "A bar never crosses a session"): at
    `90m` (a size that does not divide the session evenly) every bucket start is at or after
    the session open and before the session close, and no row includes a trade from outside
    the window (seed one trade just before the open and one just after the close; neither
    changes any bar, and `trade_count` summed over rows still equals the window's count).
  - [x] The existing `test/integration/data/test_bar_parity_sql.py` (ties at the close,
    volume, other instrument, empty window) passes unchanged: it now exercises the moved
    statement through `read_tick_minute_bars`.

**Commit**: `refactor: move the tick-cut statement to its own module`

## 3. The trade limit, measured on production

Done before anything reads the limit, so no later commit carries an unmeasured default.

- [x] **3.1 Measure and set the default**
  - [x] The number is a one-off, so the measuring script is not committed: write it outside
    the repo (not inside a directory you run from), run it with `uv run python` from the dev
    checkout, URLs from `Settings`, never `os.environ`. Model it on `scripts/measure_tick_bars.py`
    (read it first); it may import `pick_range` from there. It opens a read-only transaction
    on the production tick database with `TICK_JIT_OPTION`, builds the product plan the route
    builds (`product_plan` over `TICK_TIER`), and times `cut_time_bars` at `1m`. It writes
    nothing to any database.
  - [x] Measure (a) one median ES session (the session whose ledger count is the median of
    the held sessions), and (b) the widest run of consecutive sessions the limit lets through
    (design criterion 5): among all runs whose ledger counts sum to 95-100% of 2,000,000,
    the one with the largest sum (the earliest on a tie), cut session by session in the
    route's order. `pick_range` returns the first such run, so the script enumerates the
    runs itself (a sliding window over the ledger counts) and takes the largest. If no run
    reaches 95%, measure the largest run under the limit and say so in the note. Per
    session, `sum(trade_count)` must equal the ledger count.
  - [x] Rule for the default: 2,000,000 when (b) completes within 10 s (the bound slice 308
    used). If it does not, lower the limit to `floor(2,000,000 × 10 / seconds)` rounded down
    to a round number and re-measure (b) at that limit, re-picking the 95-100% run for the new limit each time;
    repeat until it holds. Never raise it. Stop condition: the limit must admit at least one
    median ES session. If the median session (a) alone takes more than 10 s, or the limit
    would fall below the median session's trade count, write `BLOCKED:` under this task
    with the measured times and stop before task 5.2.
  - [x] Add `API_MAX_TIME_BAR_TRADES` to `constants.py` beside `API_MAX_TICK_BAR_TRADES`, set
    to the result, its docstring recording the date, range and seconds and naming
    `MT_API_MAX_TIME_BAR_TRADES`.
  - [x] Write `user/notes/309-time-bar-measurement.md` with front matter as the 308 note has
    (`docType: notes`, `project`, `dateCreated`, `dateUpdated`, `status: complete`): the
    procedure (what the script read and timed), the ranges, ledger sums, timings, the checks
    and the default chosen. This is design criterion 5's record.
  - [x] (not needed: the database was reachable) If the tick database is unreachable from this checkout, write a line starting with
    `BLOCKED:` and the reason here and leave this task unchecked. Sections 4 and task 5.1 do
    not read the constant and proceed; stop before task 5.2, the first that does.

**Commit**: `feat: set the time-bar trade limit from a production measurement`

## 4. Source choice per session

- [x] **4.1 `choose_sources`** (new `src/manta_trading/data/tick/time_bar_source.py`)
  - [x] `choose_sources(conn, calendar_url, plan, size) -> dict[date, str | None]`, keyed
    by the plan's session dates in plan order (a product plan has one contract per
    session, so the date is unique). Look the planned `Session` objects up with
    `sessions_dated`, filtered to the planned windows, and get the bar verdicts with
    `verdicts_for(SchemaFamily.BAR_1M)`. Do not import from `parity_plan.py` (task 8.2
    deletes it): the lookup is a few lines here. Take the universe entry from
    `plan.target.product` (the lookup in `tick_bar_reads._entry`; make it public and reuse it).
  - [x] Rule per session: provider schema if `provider_schema_for(size)` is not `None`
    and the bar verdict is `complete`; else `TICKS_SOURCE` unless the tick verdict is
    `missing` or the session has no row in the tick ingest ledger, in which case `None`
    (nothing is read; it is already in `incomplete`, and it adds nothing to admission, as
    the design says).
    A tick-sourced session in any other non-complete tick state is still read (design:
    "served from the ticks that are loaded").
  - [x] `served_plan(plan, sources) -> GetPlan`: the plan with `incomplete` reduced to the
    sessions not served from a provider schema (a provider-served session is held, so it
    is not incomplete). Everything else is the original plan. Both the API and the CLI use
    it, so the lists agree.
- [x] **4.2 `source_runs`** (same module, pure)
  - [x] `SourceRun(first_session, last_session, source)` and `source_runs(plan, sources,
    returned_sessions) -> list[SourceRun]`: walk the plan's windows in order; a run is
    consecutive planned sessions that returned at least one bar and share a source; a
    planned session that returned no bars ends the run. The entries cover exactly the
    sessions that returned bars.
- [x] **4.3 Tests**
  - [x] Unit (`test/unit/data/tick/test_time_bar_source.py`) for `source_runs` and
    `served_plan` with hand-built plans: one source across the range gives one run; a
    switch from `ohlcv-1m` to `ticks` gives two runs; a session with no bars splits runs
    of the same source; a provider-served session leaves `incomplete`, a tick-served
    partial one stays.
  - [x] Integration (`test/integration/data/test_time_bar_source.py`) on
    `test/tick_support/parity_seed.py`'s fixture (the real tick day complete in both
    families): `5m` chooses `ohlcv-1m`, `15s` chooses `ticks`; with the bar day made
    partial (an unverified or missing unit) `5m` falls through to `ticks`; a session in
    neither family chooses `None` and remains in `incomplete` as `missing`; ticks partly
    loaded with no bars chooses `ticks` and keeps its tick status.
  Note: Integration file is test_time_bar_source_choice.py (basename clash with the unit file).
  Note: The neither-family session (12-05) has tick verdict edge_unknown in the fixture (past the dataset edge), not missing; it has no ledger row, so it is not read and stays in incomplete with that status.

**Commit**: `feat: choose the source of each session's time bars`

## 5. The reader and its admission

- [x] **5.1 Provider reader takes a `BarSize`**
  - [x] `bar_reads.read_provider_bars(conn, window, size: BarSize, on_batch)`: width equal
    to the provider width reads the stored rows, `session_day` uses `DAY_BUCKET`, any other
    divisible width uses `SESSION_BUCKET`. It no longer takes a `Granularity` or imports
    `GRANULARITY_BAR_MINUTES`. Rewrite the module docstring: it drops "ticks are never read
    here: no provider bars, no bars".
  - [x] Keep the tree green: its three current callers (`futures_series._bars_reader`,
    `tick_get_cmds._stream`, `tick_parity_cmds`) pass `parse_bar_size(<their token>)`.
    Tasks 6.3 and 7.1 replace the first two; task 8.2 deletes the third.
  - [x] Update `test/integration/data/test_bar_reads.py` and `test_bar_reads_sessions.py`
    for the new argument; their expectations (rollups, `1d` stamped at the open) stay.
    Add `90m` (a size that was not served before) rolled from the stored minutes.

**Commit**: `refactor: read provider bars at any size`

- [x] **5.2 Trade admission for tick-cut sessions**
  - [x] In `tick_bar_reads.py` split `trades_to_scan` so a helper
    `trades_in_windows(conn, plan, windows)` returns the ledger's record count for the
    given windows; `trades_to_scan` calls it with the overlapping windows, unchanged
    behaviour. The tick-bar tests pass untouched.
  - [x] Setting `api_max_time_bar_trades` (`MT_API_MAX_TIME_BAR_TRADES`, default
    `API_MAX_TIME_BAR_TRADES` from task 3.1) beside `api_max_tick_bar_trades` in
    `config/__init__.py`, a `get_max_time_bar_trades` dependency in `api_server/deps.py` and
    `app.state.max_time_bar_trades` in `app.py`, all following the 308 limit exactly.
  - [x] `TimeBarScanExceeded(scanned, limit)` with the message "the request reads
    {n:,} trades to cut {size} bars, over the {limit:,} trade limit; narrow start/end".
  - [x] Tests: unit for the setting default and env override (as `test_tick_bar_settings.py`);
    integration that the count excludes provider-served and `None` sessions and that a
    session with no ledger row adds nothing.
  Note: TimeBarScanExceeded also takes the size token, which its message names.

**Commit**: `feat: add the time-bar trade limit setting and window admission count`

- [x] **5.3 The per-request reader** (new `src/manta_trading/data/tick/time_bar_reads.py`)
  - [x] One object per request, `TimeBarRead(size, calendar_url, max_trades, stop)`
    (`max_trades=None` means no admission, for the CLI), with `async admit(conn, plan)`
    (same shape as `Admit`): runs `choose_sources` in the read's snapshot, stores the
    result, raises `TimeBarScanExceeded` when the trades of the tick-sourced windows exceed
    the limit, before any tick is read. And `async read(conn, window, on_batch) -> int` (the
    `ReadWindow` shape): awaits `stop()` before each window and raises the existing
    `ReadAbandoned` when the client has gone; reads `None` windows as nothing; provider
    windows through `read_provider_bars`; tick windows through `cut_time_bars`. The stored
    choices are exposed as `sources` for `served_plan` and `source_runs`.
  - [x] Ledger guard: for a tick-cut window, sum the cut's `trade_count` column, strip it
    before passing rows on, and compare with that session's ledger count
    (`status_reads.ledger_totals` for the one session). More trades than the ledger says
    raises `LedgerCountExceeded(session_date, raw_symbol, instrument_id, ledger, read)`.
    A session with no ledger row is never read (task 4.1), so the check only ever applies to
    a session that has a ledger count; the `500` is for a count that is wrong, not for a
    session that is not loaded. A smaller
    count is not an error (a partial load).
  Note: TimeBarRead also takes bounds (from/until), applied by bar start; the route passes read_series none, as 308 does.
- [x] **5.4 Tests** (`test/integration/data/test_time_bar_reads.py`)
  - [x] Through `read_series(..., admit=reader.admit)` on the seeded fixture: `5m` returns
    provider rows and `15s` returns tick-cut rows for the same session (design criterion 3);
    a mixed range (one session with bars held, one without) returns both, each from its own
    source.
  - [x] A limit below the scan raises `TimeBarScanExceeded` with no rows collected and
    `time_bar_reads.cut_time_bars` (monkeypatched to a recording stub) never called.
  - [x] `stop` returning true before the second window raises `ReadAbandoned` after the
    first was read.
  - [x] Ledger guard: lower the fixture's ledger count by one, and separately delete its
    ledger row (in the throwaway test database only); each expects `LedgerCountExceeded`
    naming the session.
  Note: The deleted-ledger-row case deletes the row between admit and read (read committed), since admission never plans a session with no ledger row.

**Commit**: `feat: read time bars per session from provider bars or ticks`

## 6. Routes and response shape

- [x] **6.1 Default range over ticks and bars** (`data/tick/get_plan.py`)
  - [x] One function returns everything held in either kind of data: from the earliest held
    session to the day after the latest, taken over the tick ledger AND the bar ledger (call
    the existing held-bounds read once per family and take the min and max). The bars routes
    and `mt data tick get <size>` use it; ticks and tick bars keep the tick-only default.
    A session held only in provider bars must be planned by a default request.
  - [x] Integration test: bounds with ticks only, bars only (a session with no ticks) and
    both; the result spans both sessions in the last case and is empty-handled as the
    existing read is when nothing is held.
  Note: Implemented as a range_over argument (TIME_BAR_RANGE) on the existing plan builders, so one function serves both plans.

**Commit**: `feat: default the bars range over held ticks and bars`

- [x] **6.2 Response models** (`api_server/models/futures.py`)
  - [x] `FuturesBarsResponse`: remove `source: str`; add `sources: list[SourceRunRecord]`
    (`first_session`, `last_session`, `source`) and `applied: AppliedBarParams`
    (`granularity`, `rule` only for a product request and omitted for a contract, `start`,
    `end`, `format`). `applied` carries what the server used, including defaulted values:
    the parsed rule text when none was given, the plan's resolved `start` and `end`
    (null only when nothing is held and no range was given), the normalized size token,
    the response format.
  - [x] `from_plan` takes the served plan (task 4.1), the `BarSize`, the runs, the applied
    values and the rows. The `BAR_COLUMNS` mapping and the bar record are unchanged.
- [x] **6.3 Routes** (`api_server/routes/futures_series.py`, `futures_params.py`)
  - [x] Both bars routes take `from` and `until` (the `FromParam`/`UntilParam` the ticks and
    tick-bar routes already use; update their descriptions, which say "Ticks and tick bars
    only"). They select bars by bar start time, half-open, and are echoed in `applied`. The
    plan's windows already carry instant bounds (`TickWindow.clipped`); a bar whose bucket
    starts inside the bounds is returned whole, never cut. Without them a busy session at
    `1s` cannot be fetched under the row ceiling.
  - [x] `granularity` becomes a plain string query parameter whose description gives the
    grammar. Parse with `parse_bar_size` before any read; a `ValueError` is an
    `HTTPException` 422 carrying its message (as `parse_rule` does). Remove the
    `TickBarGranularity` enum and its use.
  - [x] Both bars routes plan with `SchemaFamily.TICK_TIER` and, when `start` or `end` is
    omitted, take the default range from task 6.1.
  - [x] They build one `TimeBarRead` per request, call `read_series(..., admit=reader.admit)`,
    and map: `TimeBarScanExceeded` to 422, `RowCeilingExceeded` to the existing 422,
    `ReadAbandoned` to the bodiless 499 (reuse `CLIENT_CLOSED_REQUEST` and the 308
    handling; share it only if `test_futures_tick_bars.py` then passes with no edit, otherwise
    leave the 308 route alone and repeat the small mapping here), `LedgerCountExceeded` to a logged `500`
    naming the session ("see the server log", as `_uncuttable` does), a cancelled statement
    to the existing 504 through `tick_errors`.
  - [x] Build the response model in the executor (`run_in_executor`), as the ticks and
    tick-bar routes do: validating tens of thousands of rows blocks the loop. Add the new
    limit dependency to the routes. Update `_BARS_RESPONSES` and the route docstrings:
    "provider bars" becomes "time bars from held bars or ticks".
  Note: The bars routes moved to a new module routes/futures_time_bars.py (futures_series.py keeps ticks and the shared plan builders, which gained range_over).
- [x] **6.4 Model and parameter unit tests**
  - [x] `test/unit/api_server/test_futures_models.py`: `sources` replaces `source` (include a
    two-run list); `applied` includes defaulted parameters; a contract response has no `rule`
    key; the parametrized sizes (line 201) come from a literal list of sizes, not
    `TICK_BAR_GRANULARITIES`.
  - [x] `test/unit/api_server/test_tick_access.py`: delete the test pinning `TickBarGranularity`
    to `TICK_BAR_GRANULARITIES` and both imports (lines 18, 21).
- [x] **6.5 Existing tests**
  - [x] Update `test_futures_series.py` and `test/integration/test_operations_serving.py`
    where they assert provider-only bars.
  - [x] Design criterion 6: `test_futures_tick_bars.py`, `test_futures_ticks_columnar.py`,
    `test_tick_bar_reads.py` and `test/unit/data/tick/test_tick_bars*.py` pass unchanged.
  Note: test/integration/api_server/test_futures_bars_provider.py was deleted in this commit (its provider-only assertions fail now); task 6.6 adds its replacement.

- [x] **6.5b Keep the document gates green in this commit**
  - [x] Two unit tests read the committed documents and fail the moment the routes change:
    `test/unit/api_server/test_api_docs.py` (runs `scripts/check_api_docs.py`, which
    resolves the `<!-- from: ...futures_params.TickBarGranularity -->` markers and the
    `granularity:TickBarGranularity` parameter lines in `docs/api/reference.md` and
    `docs/api/agents.md`) and `test/unit/api_server/test_openapi_artifact.py` (the committed
    `docs/api/openapi.json` must equal the generated one).
  - [x] So in this commit: regenerate `docs/api/openapi.json` (the generator is named in
    `test_openapi_artifact.py`), and change only the markers and parameter lines those two
    tests check (`grep -n "TickBarGranularity\|<!-- from:\|granularity:" docs/api/reference.md
    docs/api/agents.md`). The prose rewrite stays in section 9. Both tests pass.

**Commit** (tasks 6.2 to 6.5b: the response shape, routes, the tests of the old shape and the document gates change together, so the tree is green): `feat: serve futures time bars from held bars or ticks`

- [x] **6.6 Route success and refusal tests**
  - [x] Replace `test/integration/api_server/test_futures_bars_provider.py` with
    `test_futures_time_bars.py` (delete the old file: its "no provider bars, no bars" tests
    are the ones removed by this slice). Reader behaviour is covered by task 5.4; this file
    tests the route's own work only.
  - [x] Two fixtures. **Ticks only**: the real tick day with no provider bars (production's
    state today). `seed_parity` always seeds a bar day, so split its first part (the `tbbo`
    unit and its ingest) into a `seed_ticks_only` helper in `test/tick_support/parity_seed.py`
    that `seed_parity` calls first. **Both**: `seed_parity` as it is (the same day complete in
    ticks and in provider one-minute bars).
  - [x] Ticks-only fixture, product route: `1h`, `1m`, `15s` and `1d` return bars with
    `sources` naming `ticks` and `applied` listing every parameter (criterion 1 at test
    scale). Contract route: `1h` only, since the two routes differ only in how the plan is
    built.
  - [x] Both fixture, product route: `5m` and `1d` return `sources` naming `ohlcv-1m` (a
    provider width divides them and the bar verdict is complete, task 4.1's rule), and `15s`
    returns `sources` naming `ticks` (criterion 3 at the route).
  - [x] `0s`, `2d` and `5x` return 422 naming the accepted form.
  - [x] A session with neither ticks nor bars returns no bars and is in `incomplete`
    (criterion 4).
  - [x] Default range: seed provider one-minute bars for a session that has no ticks, next
    to the tick day. A request with no `start` and no `end` returns that session's bars at
    `5m` with `sources` naming `ohlcv-1m`, and the `applied` range covers both sessions.
  Note: Product-route tests pass rule=c.0 (the calendar rule needs only definitions, so the result does not hang on the fixture's volumes).

**Commit**: `test: add route success and refusal tests for futures time bars`

- [x] **6.6b 1-second bars at the route** (same file, ticks-only fixture)
  - [x] `1s` with `from`/`until` five minutes apart returns bars equal to the task 2.2 oracle
    at one second for that window, `sources` naming `ticks`, and `applied` echoing `from`
    and `until`.

- [x] **6.7 Route error-mapping tests** (same file), one test per mapping, with the
  technique of the 308 tests. Each reads through the tick cut, so each requests `15s` (the
  size no provider width makes) on the ticks-only fixture; a size a provider serves would
  never reach the cut.
  - [x] A trade limit below the scan gives 422 with the count.
  - [x] A disconnect gives 499 with no body (`monkeypatch.setattr(Request, "is_disconnected",
    ...)`, as `test_futures_tick_bars.py` does).
  - [x] A cancelled statement gives 504 (raise `psycopg.errors.QueryCanceled` as
    `test_composed_lines.py` does).
  - [x] The ledger guard gives 500 and an ERROR log record naming the session (`caplog`, as
    `test_futures_tick_bars.py` does).

**Commit**: `test: add route error-mapping tests for futures time bars`

## 7. `mt data tick get` and the load check

- [x] **7.1 `mt data tick get <size>`** (`cli/commands/tick_get_cmds.py`, `tick_get_writers.py`)
  - [x] `WHAT` is `ticks` or a size accepted by `parse_bar_size`; anything else refuses with
    the accepted-form message. Delete `TICK_GET_CHOICES`, `family_for` and `bar_source`:
    ticks and sizes both plan `TICK_TIER`; the `WHAT` help text gives the grammar.
  - [x] For a size, `_stream` takes its default range from task 6.1 and builds the same `TimeBarRead` with `max_trades=None` (the
    design exempts the command: "What changes for a consumer" says the limit protects the
    shared API server and the command has never limited a tick read), runs its `admit` once for the choices, reads
    each window through it, prints the `served_plan` lists, and tracks the sessions that
    returned rows.
  - [x] Writers: `source` is replaced by `sources`, printed after the rows (text footer,
    CSV on stderr, JSON tail member) from `source_runs`; `partial` prints the runs for the
    rows written. Update `GET_EPILOG` ("Bars are cut from ticks unless provider bars that
    can make the size are held; the output names the source of each run of sessions").
    Exit codes and the partial handling are unchanged. `LedgerCountExceeded` in the command
    prints the session and the two counts to stderr and exits with the database-failure
    code the command already uses.
  Note: Fixed in this commit: the tick cut handed on an empty batch, which made the CLI writer's header print twice; TimeBarRead now never hands on an empty batch.
- [x] **7.2 Tests** (`test/integration/data/test_tick_get_cli.py`,
  `test/unit/cli/commands/test_tick_get_writers.py`)
  - [x] Replace the tests asserting provider-only output. New: `mt data tick get 15s` on the
    fixture prints rows and `ticks` as its source in text, CSV and JSON; `5m` prints
    `ohlcv-1m`; an invalid size exits with the preflight code; a session with no data exits
    with the partial code and lists it.
  - [x] Writers unit test: the sources footer for text, CSV and JSON, including a partial
    run; remove the uses of `BAR_SOURCE`.

**Commit**: `feat: serve mt data tick get time bars from held bars or ticks`

- [x] **7.3 Load check** (`test/load/test_309_time_bars_nfr.py`)
  - [x] Follow `test_308_tick_bars_nfr.py` (same helpers, same session and `rule=c.1`,
    test-cluster databases only): a product `15s` request over that session (about 5,500
    bars cut from about 670,000 trades) completes under a bound set from five timed runs
    (record the five timings, the median and the date in the docstring, bound about twice the
    median), and a probe of `GET /api/v1/futures/products` during the request stays within
    the 308 starvation margin of its idle median. This is the check that the model build
    runs off the loop. A `1s` request cannot serve here: a full session is about 82,800 bars,
    over the 75,000 row ceiling.
  - [x] The file carries the gate line of the 308 test
    (`MT_RUN_LOAD_TESTS=1 uv run python scripts/run_tests.py load -- <file>`). CI has no test
    job, so nothing gates this file automatically. That is deferred to issue #32 ("Run the
    integration and load tiers on a schedule against the test cluster", open), which covers
    every load test and not this one alone; slice 907 (CI pipeline and load-test gating, not
    started) owns the gate. Until it lands the gate is manual: task 10.1 runs this file.
  Note: Measured 4,919 bars at 15s, not the estimated 5,500.

**Commit**: `test: add the time-bar load test`

## 8. Remove the provider-only rule, the parity command and the history wants

Each removal follows a green verification and has its own marker, so any one can be undone.

- [x] **8.1 Remove the provider-only machinery**
  - [x] Verification first: `grep -rn` for `BAR_SOURCE`, `TICK_BAR_GRANULARITIES`,
    `TICK_GET_CHOICES`, `family_for`, `bar_source` and `TickBarGranularity` across `src`,
    `scripts`, `test`. Each remaining user is a miss in sections 5 to 7: fix it, do not keep
    the symbol.
  - [x] Delete `BAR_SOURCE` (`bar_constants.py`), `TICK_BAR_GRANULARITIES` and
    `test/unit/data/tick/test_bar_source.py` (it asserts the fixed table). Delete any
    remaining test asserting that bars are never built from ticks.
  - [x] Remove the 306 wording from docstrings and comments in `src` that still say
    provider-only ("no provider bars, no bars", "never built from ticks", 306 D2).
  - [x] Kept as is: `SESSION_BUCKET`, the source label, `read_provider_bars`, the
    `TICK_TIER`/`BAR_1M` plumbing that slice 308 uses.
  - [x] Run the unit tier for `data/tick`, `api_server` and `cli`, then the integration
    files of sections 2 to 7; all pass.

**Commit**: `refactor: remove the provider-only bar table and size menu`

- [x] **8.2 Remove `mt data tick parity`** (comparison code stays)
  - [x] Delete `cli/commands/tick_parity_cmds.py` and `tick_parity_render.py`, the `parity`
    registration and name in `cli/commands/tick.py` (including the command list in its help
    text), `data/tick/parity_plan.py`, and the command's tests
    (`test/integration/data/test_tick_parity.py`, `test_tick_parity_exit.py`, the parity
    lines of `test/unit/cli/commands/test_data_tick.py`).
  - [x] `test/tick_support/parity_seed.py` stays (tasks 2.2, 4.3 and 5.4 use it); remove its
    `run_parity` and anything else left with no user.
  - [x] Keep `bar_parity.py`, `bar_parity_report.py` and their tests
    (`test_bar_parity_classify.py`, `test_bar_parity_report.py`, `test_bar_parity_sql.py`);
    their docstrings say the command was removed in 309 and the automatic comparison is
    Future Work ("Automatic minute comparison", slice plan 300).
  - [x] `mt data tick --help` no longer lists `parity`; the tests of the help text pass.

**Commit**: `refactor: remove mt data tick parity`

- [ ] **8.3 Production wants no history**
  - [ ] `data/tick/universe.py`: delete `_HISTORY_START`, `_HISTORY_END`, `_HISTORY_WANTS`
    and the `history=` argument of both entries, and rewrite the comment block (ES and GC
    want the tick tier and its definitions; the history mechanism, `HistoryWant` and its
    validation stay for the day provider bars are bought). Do not touch the planner, the
    purchase code or the 304 scripts: they iterate `entry.history`, which is now empty.
  - [ ] Fix every test that reads the production universe's history (grep `TICK_UNIVERSE`
    with `history` in `test/`; known: `test_universe.py`, `test_availability_span.py`,
    `test_purchase_verdicts.py`, `test_cutover_304_*`). Tests of the history mechanism build
    their own `TickUniverseEntry` (as `test_planner.py` does); change the ones that took
    production's wants as a fixture to do the same. A test asserting production's wants
    becomes one asserting `history == ()` for every entry.
  - [ ] `BAR_SOURCE_NAME` and `STATISTICS_SOURCE_NAME` (`constants.py`) stay: `overview_futures.py`
    prints a source row per wanted family, so with no history wants the rows are absent and
    the constants have no production user but remain the mechanism's names.
  - [ ] Run the unit and integration tests of `mt data health`, `accounting`, `status` and the
    overview (`test_health_tick.py`, `test_tick_overview.py`, `test_family_report.py`,
    `test_overview_gather.py`, `test_operations_serving.py`) and fix expectations that
    counted bar or statistics rows: those rows are printed per want, so they are gone.
  - [ ] Design criterion 7 at test scale: `mt data health` on a test database that holds
    ticks shows no bar or statistics row and no new failure.

**Commit**: `refactor: remove the production history wants`

## 9. API documents, OpenAPI and changelog

- [ ] **9.1 `docs/api/reference.md`**
  - [ ] Find every statement about futures bars (`grep -n -iE "provider bars|no provider
    bars|ohlcv-1m|six sizes|sources?"`) and list them; each is replaced by one of the
    items below, so none is left.
  - [ ] The rule (held provider bars where they can make the size, else ticks), the size
    grammar (any whole number of seconds, minutes or hours up to 24h, and `1d`) and the 422
    for other sizes.
  - [ ] `sources` (a list; example with two runs) and `applied` (with the
    defaulted-parameters example). Say in the changed text why `source` became `sources`
    without a version change (the routes returned nothing in production and no consumer had
    coded against them).
  - [ ] The cost note, the new `MT_API_MAX_TIME_BAR_TRADES` row beside the 308 one, and the
    422/499/500/504 behaviour of tick-cut reads.
  - [ ] The removal of `mt data tick parity`, and every statement about the families a
    product wants (tick tier, `ohlcv-1m`, `statistics`): production wants the tick tier only.
- [ ] **9.2 `docs/api/agents.md` and `README.md`**
  - [ ] The same grep and the same replacements, kept short as those files are.
- [ ] **9.3 OpenAPI**
  - [ ] `docs/api/openapi.json` was regenerated in task 6.5b. Regenerate it again here only
    if a description changed since; the artifact test passes.
- [ ] **9.4 Example tests**
  - [ ] Run `test/unit/api_server/test_api_docs.py` and `test/integration/test_api_docs_examples.py`;
    update the examples they check.
- [ ] **9.5 Changelog**
  - [ ] `CHANGELOG.md` under `[Unreleased]`: a **Changed** entry (futures time bars at any
    whole second, minute or hour size and `1d`, from held provider bars or ticks, with
    `sources` and `applied`; `source` replaced by `sources`), a **Fixed** entry (the bars
    routes and `mt data tick get` returned nothing in production since 0.27.0), and a
    **Removed** entry (`mt data tick parity`; production history wants). Written from the
    user's side, as the existing entries are. Released entries are not edited.

**Commit**: `docs: document time bars from held bars or ticks; regenerate OpenAPI`

## 10. Full validation

A failure found in this section that needs a code change is committed on the slice branch as `fix: {summary}`, one commit per fix.

- [ ] **10.1 Tiers and checks**
  - [ ] `ruff format --check` and `ruff check` on the touched files; `mypy` on the touched
    subpackages (`data/tick`, `api_server`, `cli`, `config`) and `test/` helpers, zero errors.
  - [ ] Unit tier green. Integration tier green apart from known failures that exist on the
    slice base (`test_policy_advances_head.py`'s two unaided tests, the migration 051/052
    chain tests, a priority1 list mismatch, 'tuple concurrently deleted' races); re-run any
    other failure in isolation, and compare against the slice base, before investigating.
    Load tier: `MT_RUN_LOAD_TESTS=1 uv run python scripts/run_tests.py load -- <file>` for
    `test_309_time_bars_nfr.py`, `test_308_tick_bars_nfr.py` and `test_231_futures_api_nfr.py`
    pass (the full tier takes about 77 minutes; do not cap it under 90).
  - [ ] Design criterion 6 confirmed: the tick-bar, raw-tick and `ticks` CLI tests pass with
    no edits to those files (`git diff main --stat` on them is empty).
  - [ ] `git diff main` has no unintended deletions in files `ruff` touched.
  - [ ] Criteria 2, 3, 4, 5, 6 and 7 (at test scale) each name their test in the commits
    above.

**Commit**: none of its own: validation changes no file. Each fix it needs is its own
`fix:` commit (above); this section's check-offs ride in the section 11 commit.

## 11. Cleanup of other slices' documents (its own commit)

- [ ] **11.1 Slice and task files**
  - [ ] In `user/slices/` and `user/tasks/` for 302, 303, 304, 305 and 308, find sentences
    that say time bars are provider-only, that ticks never make time bars, or that history to
    2010 is to be bought (`grep -n -iE "provider-only|never built from ticks|no provider
    bars|2010|ohlcv-1m"`). Reword each to what is now true or mark it as superseded by 309.
    Facts about what was built stay. Do not edit 309's own files or any `## User-Provided
    Concept` section.
  - [ ] Working tree clean after the commit; the work is committed on the slice branch.

**Commit** (with the check-offs of section 10): `docs: reword 302-308 documents for the ticks-primary rule`

## Phase 7 handoff (not tasks)

These need the merged slice, so they belong to Phase 7 and run as agent work, with no merge
or push in this file. After the code review passes and the slice is merged:

- Release through the `/release` skill (deploy included).
- Check production against design criterion 1 (`/products/ES/bars` and `/products/GC/bars`
  for 2024-12-02 at `1h`, `1m`, `15s`, `1d`: bars, `sources` naming `ticks`, `applied`
  listing every parameter) and criterion 7 (`sudo mt-run data health`: no bar or statistics
  rows, no new failure).
- Criterion 8: find trading-ui and trading-engine with `ListAgents` and `SendMessage` each
  that the bars shape is published (`sources`, `applied`, any whole-second size), pointing at
  the futures bars section of `docs/api/reference.md`.
- Record the evidence in `user/notes/309-production-check.md` (front matter as the other
  notes): the requests and their answers, the health output, and the two messages sent.
- After the release is deployed, close #69, #74 and #40 with `gh issue close <n> --comment`,
  each naming the released version. #69 and #74: the production history wants are gone, so
  the invoice audit of a purchase that never ran and the bounds at 2010-purchase scale no
  longer apply. #40: 1-second bars are served from ticks. Issues #70 and #71 stay open: they
  apply when provider bars are held.
