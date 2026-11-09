---
docType: tasks
slice: serving-the-new-tiers
project: trading-data
lld: user/slices/306-slice.serving-the-new-tiers.md
dependencies:
- 301
- 302
- 303
projectState: 302 (tick_ohlcv_1m, BAR_1M family) and 303 (statistics, basis on segments)
  complete. /api/v1/futures bars and `mt data tick get <size>` still build time bars
  from tick_trade at read time (tick_reads.read_bars). No consumer calls the futures
  routes yet.
dateCreated: 20261108
dateUpdated: 20261109
status: in_progress
turns: 29
promptTokens: 2531122
cachedTokens: 2393647
cacheWriteTokens: 137423
completionTokens: 28570
durationSeconds: 209.4
costUsd: 1.075159
usageRounds: 3
pricedRounds: 3
usageByModel:
  claude-sonnet-5-5:
    rounds: 3
    costUsd: 1.0751586000000002
    completionTokens: 28570
revision_number: 2
---

## Context Summary

- Working on slice 306, serving the new tiers. Design: `user/slices/306-slice.serving-the-new-tiers.md`. Cite decisions as D1–D10 there.
- Goal: provider `ohlcv-1m` is the only source of served time bars, in the API and in `mt data tick get`. 1m is served as stored. 5m, 15m, 1h, 4h and 1d are rolled up from it per CME session, anchored at the session open. Every bars response names its `source`.
- Ticks never produce served time bars. The tick-derived one-minute SQL moves to a new parity check, `mt data tick parity`.
- Statistics are not served (D6, issue #73). No new tables, columns or migrations.
- All tests run on the tick test cluster and the committed fixtures (the derived 2024-12-03 bar day, the cut `tbbo` day, the DBN `ohlcv-1m` samples). Phase 6 reads no production database and buys nothing.
- The production measurements (D9 parity run, D10 long read) and closing issue #70 are Phase 7. They are not tasks here.
- Test conventions: export `MT_TIMESCALE_TEST_URL` from `.env` before pytest. Run mypy with the src paths and tests in one invocation. Run `ruff format` and `ruff check` on touched files only, and check `git diff` for swept pre-existing lines before each commit.
- Next slice: 307 (historical bar purchase). 308 (tick bars) follows.

**Constants, not numbers:** D4 keeps `TICK_BAR_GRANULARITIES` and `TickBarGranularity`, so the final grep (6.3) covers `read_bars`, `_BARS`, `_INTRADAY_BUCKET`, `_SESSION_BUCKET` only. Use the exit-code constants in `tick_exit.py`: `get` keeps `EXIT_PARTIAL`, and parity exits `EXIT_OK` or `EXIT_PARTIAL`.

---

## 1. Plans take the family they serve (D3)

- [x] **1.1 `held_session_bounds` takes a family**
  - [x] In `data/tick/roll_inputs.py`, change `held_session_bounds(conn, shape)` to `held_session_bounds(conn, shape, family)`. Replace both hard-coded `SchemaFamily.TICK_TIER` uses (the `ledger_units` call and `shape.params`) with `family`.
  - [x] Update the two callers to pass `SchemaFamily.TICK_TIER`: `get_plan.resolve_range` and `active_contract` (around line 290).
  - [x] Update `test/integration/data/test_tick_roll_inputs.py` (two calls, lines ~123 and ~126) to pass `TICK_TIER`.
  - [x] Add one test: with a bar ledger row and no tick ledger row for a shape, `held_session_bounds(..., BAR_1M)` returns the bar session and `(..., TICK_TIER)` returns `None`.
  - [x] Success: the new test passes and `test_tick_roll_inputs.py` passes with only the argument added.

- [x] **1.2 `verdicts_for(family)` in `tick_status_scope.py`**
  - [x] Add `verdicts_for(family: SchemaFamily) -> VerdictReader`: returns `session_verdicts` for `TICK_TIER` and `BAR_1M`, and raises `ValueError` naming the family for any other. Check how `VerdictReader` and `session_verdicts` are declared in that module and in `status_reads.py`, and reuse their types. Do not duplicate them.
  - [x] Unit test (`test/unit/data/tick/`): both supported families return the reader. `STATISTICS` raises `ValueError` and the message contains the family value.
  - [x] Success: unit test passes. The module stays under about 300 lines.

- [x] **1.3 `contract_plan` and `product_plan` take `family`**
  - [x] In `get_plan.py`, add a required `family: SchemaFamily` parameter to `contract_plan` and `product_plan`. Thread it to `_held` (read `FAMILY_LEDGER[family]` instead of the literal `tick_ingest_ledger`; keep the `COVERAGE_PREDICATE` join), to `resolve_range` (which passes it to `held_session_bounds`), and to `_incomplete`.
  - [x] `_incomplete` gets its reader from `verdicts_for(family)` and passes `family` to it. Remove the hard-coded `TICK_TIER` there.
  - [x] Update every caller of the two plan functions to pass `SchemaFamily.TICK_TIER` for now: `routes/futures_series.py` (both plan helpers), `cli/commands/tick_get_cmds.py` (`_plan`), `test/integration/data/test_tick_get_plan.py`, `test/integration/api_server/test_futures_series.py`, `test/load/test_231_futures_api_nfr.py`. Behaviour is unchanged at this step.
  - [x] Check `_held` is only called from `contract_plan` (grep) so no caller is missed.
  - [x] Success: `ruff`, `mypy` and the existing tests in `test_tick_get_plan.py`, `test_tick_get_cli.py`, `test_futures_series.py` pass unchanged apart from the added argument.

- [x] **1.4 Plan tests for `BAR_1M`**
  - [x] In `test_tick_get_plan.py`, add tests with `family=BAR_1M`: (a) a contract with ticks and no bars: default range is empty (`start`/`end` `None`) and no segments; (b) a contract with bars: default range is the held bar sessions, and a session with ticks but no bars is listed in `incomplete` with its `BAR_1M` status; (c) `contract_plan` with `TICK_TIER` on the same data still reads the tick ledger.
  - [x] Seed bars with hand-built `ohlcv-1m` units (`seed_tier_days(..., OHLCV_1M)` plus `insert_bar_ledger_row`) beside the existing September roll ticks. The plan reads only units and ledgers, so this is the faithful seed; the cut 2024-12-03 day (`seed_bar_units`) is for the reader tests in section 2.
  - [x] Success: tests pass on the test cluster.

**Commit**: `refactor: plans take the schema family they serve`

---

## 2. Provider bar reader (D2, D5)

- [x] **2.1 `BAR_SOURCE` in `bar_constants.py`**
  - [x] Add `BAR_SOURCE: Mapping[Granularity, TickSchema]` mapping every size in `TICK_BAR_GRANULARITIES` to `TickSchema.OHLCV_1M`. Derive the keys from `TICK_BAR_GRANULARITIES` so a new served size cannot be forgotten. Import `TickSchema` from `data/tick/constants.py`; check there is no import cycle with `constants.py` importing `bar_constants`.
  - [x] Unit test: the keys equal `TICK_BAR_GRANULARITIES` and every value is `OHLCV_1M`.
  - [x] Success: unit test passes.

- [x] **2.2 `read_provider_bars`**
  - [x] Create `data/tick/bar_reads.py`. Reuse `TickWindow`, `BAR_COLUMNS`, `OnBatch`, `Row` and the `_stream`/window-parameter helpers from `tick_reads.py`; if they are private there, rename them without the underscore rather than copying.
  - [x] Define the session-anchored bucket once, as a SQL fragment that `bar_parity` (5.1) imports: `bucket = open_ns + ((ts_event - open_ns) / width_ns) * width_ns` (integer division). Width is `GRANULARITY_BAR_MINUTES[granularity]` times ns per minute. There is no Python copy of the formula. Its boundaries are tested through the database in 2.3.
  - [x] Implement `read_provider_bars(conn, window, granularity, on_batch) -> int` with the same signature and `BAR_COLUMNS` row order as `read_bars`. Window predicate: `instrument_id = :id AND ts_event BETWEEN :open_ns AND :close_ns` on `tick_ohlcv_1m` (use `BAR_1M_TABLE`).
  - [x] `M1`: select the stored rows in `ts_event` order, with the window's `session_date`, `raw_symbol` and `instrument_id` as parameters, as the tick reads do.
  - [x] Coarser: group by the session-anchored bucket. First open and last close by `ts_event` order, max high, min low, `sum(volume)::bigint`. Use `array_agg(... ORDER BY ts_event)` as `_BARS` does. Empty buckets are omitted. `D1` is one bucket stamped at `open_ns`.
  - [x] The one-minute bar's `time` is the stored `ts_event`, unchanged.
  - [x] Success: module under about 300 lines and mypy clean.

- [x] **2.3 Integration tests for the reader**
  - [x] Create `test/integration/data/test_bar_reads.py` on a scratch tick database. Seed `tick_ohlcv_1m` from the derived 2024-12-03 bar day using `seed_bar_units` plus the ingest path (see `test_bar_ingest_e2e.py` for how bars reach the table). Build `TickWindow`s from the calendar session for 2024-12-03.
  - [x] Tests: (a) `M1` rows equal the stored rows exactly (count, prices, volume); (b) for each of 5m, 15m, 1h, 4h, 1d the result equals a rollup computed in the test from the same 1m rows (first open, max high, min low, last close, summed volume); (c) the bucket formula's boundaries: 4h bucket starts on the 2024-12-03 session are 23:00Z, 03:00Z, 07:00Z, 11:00Z, 15:00Z, 19:00Z; (d) a window with no bars returns 0 rows; (e) 5m, 15m and 1h bucket starts equal the session open plus whole multiples of the width (the pre-306 boundaries).
  - [x] DBN sample check: decode `test_data.ohlcv-1m.v1.dbn.zst` (2 bars, ESH1, 2020-12-28) through the existing DBN test helper and assert the reader returns its price scale and `ts_event` unchanged.
  - [x] Success: all pass on the test cluster.

- [x] **2.4 DST and early-close bucket tests (criterion 3)**
  - [x] Add a DST test: one session on each side of the 2024-11-03 US change, 4h bucket starts are at 17:00 CT (22:00Z/23:00Z open) offsets. Add an early-close session, Friday 2024-11-29 (day after Thanksgiving; confirm its close in the calendar), asserting the last 4h bucket holds no bar after the early close. Windows come from the production calendar (`tick_support.reads.session_window`). The minute bars are inserted straight into `tick_ohlcv_1m`, because the reader reads only that table and ingest placement is 302's, tested there.
  - [x] Success: all pass on the test cluster.

**Commit**: `feat: add provider bar reader with session-anchored rollup`

---

## 3. Serve provider bars: API (D2, D3, D4)

- [x] **3.1 `source` on `FuturesBarsResponse`**
  - [x] In `api_server/models/futures.py`, add `source: str` to `FuturesBarsResponse` (placed after `granularity`, as the design example shows). `from_plan` takes the source from `BAR_SOURCE[granularity]` and writes its `.value`; the model never invents it. Look at how `granularity` is passed in today and mirror it.
  - [x] Update `test/unit/api_server/test_futures_models.py`: `source` appears in the model and equals `"ohlcv-1m"` for every served size. The existing field-name and `BAR_COLUMNS` order pins still pass.
  - [x] Success: unit tests pass.

- [x] **3.2 Bars routes read provider bars**
  - [x] In `routes/futures_series.py`: `_bars_reader` calls `read_provider_bars`. The bars handlers build their plans with `SchemaFamily.BAR_1M`; the ticks handlers keep `TICK_TIER`. Thread the family through `_contract_plan` and `_product_plan` as a parameter, not a module constant. Remove the `read_bars` import.
  - [x] `futures_read.py` comment (line ~25) mentions `read_bars`: update it.
  - [x] Success: mypy clean; the module stays under about 300 lines (it is 244 now).

- [x] **3.3 API tests rewritten for provider bars**
  - [x] Rework the bars tests in `test/integration/api_server/test_futures_series.py` (contract bars, product bars across a roll, second product, row ceiling) so the fixtures hold `tick_ohlcv_1m` bars, not only ticks. The two CLI-equality tests (`test_contract_bars_equal_the_cli`, `test_product_bars_across_the_roll_equal_the_cli`) need task 4, so they are reworked in 4.3, not here.
  - [x] Add: (a) a session with ticks and no provider bars returns `count: 0` and lists the session in `incomplete` with its `BAR_1M` status (criterion 1; also assert no `tick_trade` statement runs for bars, for example by seeding ticks whose prices differ from the bars and asserting the bars are the provider's); (b) every size returns `source == "ohlcv-1m"`; (c) `/schedule` and `/bars` give identical segments (contract and `basis`) for `v.0` over a bar-only window and over a mixed window (D3); (d) a load-complete session that fails the cross-tier volume check is served and not listed in `incomplete` (use the 303 fixture or helper for that check, see `test_family_surface_agreement.py`).
  - [x] Success: the bars tests in `test_futures_series.py` other than the two CLI-equality tests, plus `test/unit/api_server/test_bars.py` and `test_tick_access.py`, pass.

- [x] **3.4 Rewire the 231 load test to provider bars**
  - [x] `test/load/test_231_futures_api_nfr.py` seeds ticks only (`long_roll` via `seed_minute_roll`), so `test_a_product_1m_bars_read_across_the_roll` would return no bars once the route reads `tick_ohlcv_1m`. Extend `long_roll` (or a sibling fixture in `test/tick_support/bulk_ticks.py`) to also seed `tick_ohlcv_1m` rows and bar-ledger rows for the same sessions and instruments, using the same bulk COPY approach `seed_minute_roll` uses. Seed enough minutes per session that the product 1m read keeps its current shape (`READ_SESSIONS` sessions, same segments). Other users of `long_roll` (`test_the_tick_block_adds_little_to_status`) must still pass.
  - [x] Keep `PRODUCT_BARS_BOUND_S` and the assertions as they are. If the bound fails on provider bars, do not raise it. Write a line starting `BLOCKED:` with the measured median under this task, leave the task unchecked, and stop.
  - [x] The load tier takes about 77 minutes in full. Run only this file (`test/load/test_231_futures_api_nfr.py`).
  - [x] The load tier is run by hand, not in CI, so no wiring is needed. D10's 10 s bound is the one-off Phase 7 production measurement the design sets, not a Phase 6 test.
  - [x] Success: the rewired load test passes with its original bound.

(No commit here: the API-versus-CLI equality tests pass only once task 4 lands. Sections 3 and 4 commit together.)

---

## 4. Serve provider bars: `mt data tick get` (D8)

- [x] **4.1 `get <size>` reads provider bars**
  - [x] In `cli/commands/tick_get_cmds.py`: `_plan` takes the family. Bar sizes plan with `BAR_1M`, `ticks` with `TICK_TIER`. `_stream` calls `read_provider_bars` for sizes and `read_ticks` for `ticks`. Remove the `read_bars` import. Exit-code logic is unchanged: `EXIT_PARTIAL` when unresolved or incomplete sessions exist. For bars "incomplete" is the bar family, so ticks with no bars exits `EXIT_PARTIAL` with no rows.
  - [x] Update the `tick_get` docstring and `GET_EPILOG`: "bars built from ticks" becomes "provider bars" (check the epilog text).
  - [x] Success: mypy clean.

- [x] **4.2 `source` in `get` output**
  - [x] In `tick_get_writers.py`: JSON output gains `"source"` for bar sizes (value from `BAR_SOURCE[Granularity(what)]`). The text and CSV header line names the source. `ticks` output is unchanged and has no `source`. Pass the source through the writer factory (`writer_for`), not a string literal in the writers.
  - [x] Success: mypy clean; `tick_get_writers.py` under about 300 lines.

- [x] **4.3 `get` tests**
  - [x] Rework the bar tests in `test/integration/data/test_tick_get_cli.py` (`test_product_bars_follow_the_schedule`, `test_product_bars_as_json`, the unresolved test) to seed provider bars. Tick-only expectations (`N_TICKS`) become bar values.
  - [x] Add: (a) `get 1h --product ES` returns the same rows as the API for the same arguments, with `source` in the JSON. This includes the two reworked API tests `test_contract_bars_equal_the_cli` and `test_product_bars_across_the_roll_equal_the_cli` in `test_futures_series.py`; (b) a range with ticks and no provider bars prints no rows, lists every session as incomplete and exits `EXIT_PARTIAL`; (c) the text and CSV header names the source; (d) `get ticks` output is unchanged.
  - [x] Success: `test_tick_get_cli.py`, all of `test_futures_series.py` and `test/unit/cli/commands` pass.

**Commit**: `feat: serve futures bars and get from provider ohlcv-1m with source (Fixes #47)`

---

## 5. Parity module and command (D7, D9)

- [ ] **5.1 `bar_parity.py`: tick-derived one-minute SQL**
  - [ ] Create `data/tick/bar_parity.py` with `read_tick_minute_bars(conn, window, on_batch)`: the former `_BARS` SQL over `tick_trade` at width 60 s with the session-anchored bucket (the bucket SQL fragment from `bar_reads` (2.2), so buckets equal the provider's). Open and close by full key order `ts_event, sequence, sequence_ordinal`, as `_BARS` does today. Rows come back in `BAR_COLUMNS` order.
  - [ ] Reuse the window and stream helpers from `tick_reads.py`. Do not copy them. Do not import `read_bars` (it is removed in 6.1).
  - [ ] No unit test: its behaviour is covered by the parity integration tests (5.7).
  - [ ] Success: module under about 300 lines and mypy clean.

- [ ] **5.2 Minute classification**
  - [ ] In `bar_parity.py` (or `bar_parity_classify.py` if it would pass 300 lines), add a pure function that joins tick minutes and provider minutes by bucket and classes each: `agree` (open, high, low, close and volume all equal), `differ` (record the field names that differ), `provider_only`, `tick_only`. A minute record carries `bucket`, `class`, `fields`, both sides' values and `at_close` (bucket equals the window's `close_ns`).
  - [ ] Define the class names once, as a `StrEnum`.
  - [ ] Unit tests (`test/unit/data/tick/`): each of the four classes, a minute differing in two fields names both, `at_close` set only at `close_ns`, and the empty-on-both-sides case returns nothing.
  - [ ] Success: unit tests pass.

- [ ] **5.3 Session report built in memory**
  - [ ] Add a per-session report type: counts by class, close-stamped minute count and how many of those agree, and volume totals for both sides. Add a function that builds the full report from classified minutes of each compared session, plus a list of skipped sessions each carrying its `TICK_TIER` and `BAR_1M` status.
  - [ ] `minutes_listed` rule (for `--json`): every non-agreeing minute plus every close-stamped minute whatever its class.
  - [ ] `exit_code`: `EXIT_OK` if every compared minute agrees, else `EXIT_PARTIAL`.
  - [ ] Unit tests for counts, close-stamped count, the listed-minutes rule and the exit rule.
  - [ ] Success: unit tests pass.

- [ ] **5.4 Parity plan, shared helpers and session join**
  - [ ] If `_plan`, the target and rule parsing and the option definitions are private in `tick_get_cmds.py`, promote them (drop the underscore) so parity imports them. Do not copy them. Follow `tick_get_cmds.py` for connection setup (`connect_migrated`, `roll_conninfo`).
  - [ ] Create `cli/commands/tick_parity_cmds.py`. Plan with `family=TICK_TIER` (windows and tick verdicts). In the same `REPEATABLE READ READ ONLY` snapshot call `verdicts_for(BAR_1M)` over the plan's sessions and join by session date. Compared sessions are `complete` in both. Every other planned session goes in a skipped list with both statuses.
  - [ ] Success: mypy clean; the existing `get` tests still pass after the promotion.

- [ ] **5.5 Parity compare loop and failure mapping**
  - [ ] Per compared window read both sides (`read_tick_minute_bars` and `read_provider_bars(..., M1)`) into memory, classify, and add to the report. Print only after the last session is compared.
  - [ ] Failures go through `tick_exit.exit_code_for` as `get` does (`run_mapped`): unknown or ambiguous contract, bad range or calendar refusal exit `EXIT_PREFLIGHT`; an `OperationalError` at any point, including a statement timeout, exits `EXIT_STORAGE` with the first line of the error on stderr and no report. Do not catch and continue.
  - [ ] Success: mypy clean, and neither file passes about 300 lines.

- [ ] **5.6 Output and registration**
  - [ ] Text output: per-session minute counts by class, the close-stamped count, volume totals for both sides, and the skipped sessions with their statuses. It lists no minutes. `--json` output adds `minutes_listed` and the same session counts. Arguments are those of `get`: `--contract` or `--product [--rule]`, `--start`, `--end` (session dates, end exclusive), `--json`.
  - [ ] Register `parity` in `tick.py` next to `get` (`tick_app.command("parity", ...)`) and update the `tick` group help line that lists the verbs (line ~69).
  - [ ] Success: `mt data tick parity --help` runs and lists the options.

- [ ] **5.7 Parity classes on the real fixtures**
  - [ ] Create `test/integration/data/test_tick_parity.py`. Ingest the cut 2024-12-03 `tbbo` fixture and the derived bar day (the `tick_support` helpers used by `test_bar_ingest_e2e.py`).
  - [ ] Compute each minute's expected class in the test by an independent Python bucketing of the fixture's trades, set against the derived bars (D9). Assert the command's `--json` output agrees minute for minute. Minutes the cut kept whole come out `agree`; the rest `differ` or `provider_only`.
  - [ ] `tick_only`: a separate test ingests the same `tbbo` day with a hand-built bar day (`write_bar_file`) that omits one minute holding trades, and asserts that minute is `tick_only`. State this in the test docstring.
  - [ ] `at_close`: assert the minute stamped at the 2024-12-03 session close is flagged and listed in the `--json` minutes.
  - [ ] Skipped sessions: seed a day with ticks and no bars; assert it is listed as skipped with both statuses and does not change the exit code.
  - [ ] Success: all pass on the test cluster.

- [ ] **5.8 Parity exit codes and failures**
  - [ ] In the same file or `test_tick_parity_exit.py`: `EXIT_OK` for an all-agree window (hand-built matching bars and trades); `EXIT_PARTIAL` when any minute differs; `EXIT_PREFLIGHT` for an unknown contract; `EXIT_STORAGE` with nothing on stdout when the connection fails. Use the patterns in `test_tick_get_cli.py` to force the failures.
  - [ ] Success: all pass on the test cluster.

- [ ] **5.9 Note template**
  - [ ] Create `user/notes/306-bar-parity.md` (with the front matter `file-naming-conventions.md` requires for notes) holding headings only: the ES 2024 parity result (minute counts by class, the `--json` minute list reference, the close-stamped finding for issue #70), the D10 long-read timing with the 1,300-session extrapolation, and the D1 provider `ohlcv-1d`/`1h`/`eod` bucketing finding carried from the design. Mark each result section `Not yet measured (Phase 7)`. Copy the D1 text into the D1 section now (it is already decided).
  - [ ] Success: the file exists and holds no invented numbers.

**Commit**: `feat: add mt data tick parity comparing tick and provider minutes`

---

## 6. Retire the tick-derived bars (Migration Plan)

- [ ] **6.1 Delete `read_bars` and its SQL**
  - [ ] In `tick_reads.py`, delete `_BARS`, `_INTRADAY_BUCKET`, `_SESSION_BUCKET`, `read_bars` and the imports only they used (`Granularity`, `GRANULARITY_BAR_MINUTES`, `_NS_PER_MINUTE` if nothing else uses it). Update the module docstring: it no longer mentions bars. Keep `read_ticks`, `TICK_COLUMNS`, `BAR_COLUMNS`, `TickWindow` and the helpers `bar_reads`/`bar_parity` import.
  - [ ] Success: the module imports cleanly and mypy passes.

- [ ] **6.2 Move or delete the old tick-bar tests**
  - [ ] `test/integration/data/test_tick_reads.py`: remove the `read_bars` tests (lines ~99–149); the equivalent bucketing and window cases are covered by `test_bar_reads.py` (2.3) and the tick-minute SQL by `test_tick_parity.py` (5.7). If any case there has no counterpart (for example the two-instrument isolation test or the empty window), port it to the matching new file first. Update the module docstring.
  - [ ] `test/integration/data/test_tick_coverage_bars.py` and `test_tick_status_build.py` matched the earlier grep for `_BARS`; check each match. If it is only an unrelated name, leave the file alone.
  - [ ] Success: `test_tick_reads.py` covers `read_ticks` only and passes.

- [ ] **6.3 Verify nothing references the removed code**
  - [ ] Run `grep -rn "read_bars\|_INTRADAY_BUCKET\|_SESSION_BUCKET" src test scripts docs`. Expected: no matches. Also run `grep -rnw "_BARS" src test scripts`; the only allowed matches are the tick-minute SQL now in `bar_parity.py`, if you kept the name there. Substring hits such as `MIN_BARS` are unrelated.
  - [ ] Run `scripts/cut_tick_fixtures.py` only by reading it: if it imports anything removed, update it. Do not re-cut fixtures.
  - [ ] Confirm `TICK_BAR_GRANULARITIES` and `TickBarGranularity` still exist (D4; see Context Summary).
  - [ ] Success: grep output as expected; `uv run mypy` (src paths and tests in one invocation) passes.

- [ ] **6.4 Touched-area test run**
  - [ ] Run the unit tier for `test/unit/api_server`, `test/unit/cli`, `test/unit/data/tick`, and the integration tests `test/integration/data` (tick files) and `test/integration/api_server`. Run the integration tiers separately and re-run any failure in isolation before investigating.
  - [ ] Run `ruff format` and `ruff check` on the touched files only. Check `git diff` for swept pre-existing lines.
  - [ ] Success: all touched-area tests pass. Any failure is explained as pre-existing by running it on the slice base.

**Commit**: `refactor: remove read-time tick bars from every serving path`

---

## 7. Docs, OpenAPI, CHANGELOG (D4–D7, criteria 12, 13)

- [ ] **7.1 `docs/api/reference.md`**
  - [ ] §6.1 (conventions) states: the bar-source rule (provider `ohlcv-1m` only, ticks never serve bars); `source` on every bars response; session-anchored buckets (4h at 17:00, 21:00, 01:00, 05:00, 09:00, 13:00 CT, short last bar, early-close cut); "no provider bars, no bars" (session listed in `incomplete` with its `BAR_1M` status); `incomplete` carries the load verdict only, and a load-complete session that fails the cross-tier volume check is served and not listed (it belongs to `mt data tick coverage`); the close-stamped one-minute stub bucket at 5m/15m/1h on a regular session, tied to issue #70; statistics are not served.
  - [ ] Rewrite §6.6 and §6.8 for the new behaviour and `source`, with a response example in the design's shape (values elided). Fix the §6.1 line that says `mt data tick get` exits 3 when sessions are incomplete if that text now misdescribes bars. Check the §6.4 note that `/schedule`'s default range still comes from the tick ledger and a bar-only range must be given explicitly (D3).
  - [ ] Success: the sections read consistently with the design; no mention of bars built from ticks remains (grep `built from ticks`, `tick bars`).

- [ ] **7.2 `docs/api/agents.md`**
  - [ ] Rewrite "Give me futures bars" for provider bars, with `source`, the session anchoring and "no provider bars, no bars". Keep the `<!-- endpoint: -->` markers for the two bars routes in the format `scripts/check_api_docs.py` expects (read an existing marker block first).
  - [ ] Success: `uv run python scripts/check_api_docs.py` passes.

- [ ] **7.3 Regenerate OpenAPI**
  - [ ] Run `scripts/dump_openapi.py` to regenerate `docs/api/openapi.json`. Confirm in the diff that only `FuturesBarsResponse` gains `source` and nothing else changes (the `TickBarGranularity` component is unchanged).
  - [ ] Add an assertion to `test_openapi_artifact.py` that `FuturesBarsResponse` has `source` and that the 200 response of both bars routes references it (both are published today).
  - [ ] Success: `test_openapi_artifact.py`, `test_api_docs.py`, `test/integration/test_api_docs_examples.py` and `scripts/check_api_docs.py` pass.

- [ ] **7.4 CHANGELOG, README and runbook lines**
  - [ ] Add an Unreleased entry to `CHANGELOG.md` listing: bars now come from provider `ohlcv-1m` only; `source` on bars responses and `get` JSON; 4h buckets anchored at the session open (closes #47); no bars where no provider bars are held; the close-stamped one-minute stub bucket; the new `mt data tick parity` command; statistics not served (issue #73).
  - [ ] Update the operator `get` and `parity` lines wherever the runbook or README documents `mt data tick get`/`coverage` (grep `mt data tick get` in `README.md` and `deploy/`; the earlier grep found `README.md` around line 755). Add one `parity` line with its options and exit codes.
  - [ ] Verify D6 is recorded: confirm issue #73's body states the statistics routes are deferred from 306 (read-only `gh issue view 73`); the design already holds the decision. Do not edit the issue unless the body lacks the route drafts.
  - [ ] Success: `check_api_docs.py` passes. The CHANGELOG entry names every behaviour change in success criterion 13.

**Commit**: `docs: document provider bar serving, source, and mt data tick parity`
