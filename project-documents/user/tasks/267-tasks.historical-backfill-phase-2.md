---
docType: tasks
slice: historical-backfill-phase
project: trading-data
lld: user/slices/267-slice.historical-backfill-phase.md
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [264, 265]
interfaces: []
projectState: >
  Part 2 of 2. Starts after part 1's Sections 1-5: endpoint costs
    recorded, constants and kalshi_007 in, the three client methods and
  their fixtures recorded, TradeRepository parameterised by surface,
  CandleRepository.pending_behind_cutoff, and TradeSync.drain walking in
  either direction. See part 1's Context Summary for the rules, the gates,
  the branch, and the production-database warning.
reviewVerdictsAddressed:
  - 267-review.tasks.historical-backfill-phase.part-2, first round (claude-sonnet-5, CONCERNS) — F001 load-test waiver recorded in part 1's Context Summary; F002–F004 pass
  - 267-review.tasks.historical-backfill-phase.part-1, first round (claude-sonnet-5, CONCERNS) — F001's user-facing site assigned to Task 7.4 here
dateCreated: 20260831
dateUpdated: 20260901
status: in_progress
---

## Context Summary

- Part 2 of `267-tasks.historical-backfill-phase`. Sections 6–11: the
  `HistoricalSync` core and its tests, the phase and renderer, the `status`
  changes (Decision 8), end-to-end integration, the rehearsal, the
  documentation, and the Project Manager's host steps. Every rule in part
  1's Context Summary applies here unchanged.
- The core's shape follows `TradeSync` and `CandleSync`: no httpx, no
  typer, no SQL; sources and repositories are injected; every count comes
  from a repository method; one `phase_finished` event per phase.

## Section 6: `HistoricalSync` — the core

Design *Architecture* (the phase, one firing; state), *Technical Decisions
1, 2, 4, 6, 9*, *Implementation Details* (`historical_sync.py`).

- [x] **Task 6.1: `historical_types.py`** (effort: 2)
  - [x] `HistoricalSource(Protocol)`: `get_historical_markets(*, cursor,
        limit, mve_filter) -> MarketsPage`, `get_historical_trades(*,
        cursor, min_ts, max_ts, limit) -> TradesPage`,
        `get_historical_market_candlesticks(ticker, *, start_ts, end_ts,
        period_interval) -> list[Candlestick]`, plus the parent calls
        `ingest_markets` needs (`get_events`, `get_event`, `get_series`).
        `KalshiClient` satisfies it structurally.
  - [x] `HistoricalCatalogSource`: the Decision 9 adapter — satisfies
        `sync.CatalogSource` by forwarding `get_markets`/`iter_markets` to
        the archive and the event/series calls unchanged;
        `get_series_list` and `get_historical_cutoff` raise
        `NotImplementedError` naming why (the walk never calls them).
  - [x] `HistoricalTradeSource`: the adapter Decision 5 names — wraps a
        `HistoricalSource` and exposes `TradeSource`'s `get_trades(...)` by
        forwarding to `get_historical_trades` with the same arguments;
        `get_historical_cutoff` is **not** forwarded (raise
        `NotImplementedError` naming why: the backward walk never reads the
        cutoff). Five lines; it is what lets `TradeSync` run unchanged.
  - [x] `HistoricalResult` dataclass with `counts()` and `to_dict()`:
        `run_id`, `started_at`, `cap`, `requests`, `capped`,
        `archive_walked`, `archive_pages`, `archive_markets_fetched`,
        `archive_markets_written`, `archive_restarted`,
        `candle_markets_completed`, `candle_requests`, `candles_written`,
        `candle_markets_remaining`, `slow_markets`, `item_errors:
        list[CandleItemError]` (reuse 264's type), `trades_row_missing`,
        `floor`, `watermark_before`, `watermark_after`, `floor_reached`, the
        trades sub-drain's `windows_completed`, `trades_fetched`,
        `trades_written`, `unknown_market`, `excluded_by_rule`,
        `duplicates`, `unknown_prefixes`, `duration_ms`, `error`. The
        trades figures are copied from the inner `TradeResult` after
        `drain`, not recomputed.
  - [x] `classify_historical(result, exc)` — `classify_outcome(bool(result.
        item_errors), exc)`: `PARTIAL` when a market was skipped on a
        permanent error and nothing aborted (Decision 6), the
        `classify_candles` shape.
  - [x] Success: mypy/pyright clean; `to_dict` round-trips through
        `json.dumps` (unit test in Task 6.4).

- [x] **Task 6.2: `HistoricalSync.run`** (effort: 4)
  - [x] New `historical_sync.py` (≤ ~300 lines; split the candle sub-drain
        into `historical_candles.py` if it does not fit). Constructor:
        `source: HistoricalSource`, `trades: TradeRepository` (built by the
        phase with `surface=Surface.HISTORICAL`), `candles: CandleRepository`,
        `catalog: CatalogRepository`, `sink`,
        `*, rule, run_id, cap: int, clock`. `cap` is **passed in** — the core
        never sees the client; the phase computes it (Task 7.1).
  - [x] `run()`, in order, inside the `TradeSync`-style `try` that records
        `error`, logs `exception`, finishes, and re-raises:
    1. [x] Log the start line with `run_id`, `cap`, the floor, and the rule.
    2. [x] **Archive walk (Decision 9).** Three states of the historical
       row: *not started* (no row, or cursor NULL and watermark NULL), *in
       progress* (cursor set), *done* (cursor NULL, watermark set — the
       trades sub-drain seeds the watermark only after the walk completes,
       so the watermark is the done marker and no new column is needed).
       Done → no request, `archive_walked`. Otherwise build
       `CatalogSync(HistoricalCatalogSource(source), catalog,
       sink=NullSyncEventSink(), run_id=run_id)` once; loop: **check the
       cap**, `get_historical_markets(cursor=…, limit=MARKETS_PAGE_LIMIT,
       mve_filter=KALSHI_MVE_FILTER)`, count the request, `await
       sync.ingest_markets(SyncPhase.MARKETS, page.markets)` (parents
       resolved and upserted by 262's code; its parent requests count too —
       wrap the source to count), then in one transaction
       `set_cursor(page.cursor or None)`. Stop when `page.cursor` is empty
       or every market on the page has `settlement_ts <
       HISTORICAL_TRADES_FLOOR − HISTORICAL_ARCHIVE_STOP_MARGIN`; on stop
       clear the cursor and log `archive walk done pages=n markets=m`. Cap
       hit mid-walk → `capped`, cursor saved, **return before candles and
       trades** (nothing downstream may run on a partial catalog). A
       `ProviderPermanentError` on the resume request → log `archive cursor
       rejected; restarting`, `archive_restarted`, cursor cleared, walk
       again from the first page (idempotent upserts). Item errors 262
       raises for unavailable parents stay 262's (counted in `sync.result`
       and logged; they do not make this phase `PARTIAL` — the market stays
       unknown, as in the live pass).
    3. [x] **Candles sub-drain.** The cutoff is the candles surface's stored
       `watermark_ts` (`CandleRepository`/`CatalogRepository.get_sync_state
       (Surface.CANDLESTICKS)`) — the same instant `status` counts
       `behind cutoff` against, and no request. No candles row → skip the
       sub-drain with an INFO line (the candle phase has never run).
       Otherwise `pending_behind_cutoff(period, cutoff, limit)` where
       `limit` is `HISTORICAL_CANDLE_MARKETS_PER_PASS` while the historical
       row's watermark is above the floor and **unbounded** (`None` → no
       `LIMIT` clause) once `floor_reached` (Decision 9 — the cap alone
       bounds it then); for each market, **check the cap first** (a market may
       exceed it by its own requests — Criterion
       6), then fetch `[open_time, close_time + period)` in chunks of at
       most `CANDLE_SINGLE_MAX_CANDLES` periods (`candle_plan.periods_in`;
       the constant exists from 264 and gains its first reader — update its
       comment), one request per chunk; write all chunks in **one
       transaction per market**: `insert_candles` then `advance_state(period,
       [StateAdvance(ticker, close_time + period, open_time)])`. Time each
       market with the clock; `WARNING` above
       `HISTORICAL_SLOW_MARKET_SECONDS` and count it in `slow_markets`
       (Decision 4). A `ProviderPermanentError` on one market's request →
       `item_errors.append(CandleItemError(ticker, str(exc)))`, `logger.
       error`, continue with the next market; **no state row is written**
       for it (Decision 6). Any other exception propagates. After the loop,
       `candle_markets_remaining = count_behind_cutoff(period, cutoff)`.
    4. [x] **State row.** `trades.read_state()`; when `None`,
       `read_live_coverage_from()` — `None` means the live phase has never
       run: set `trades_row_missing`, log, and skip the trades sub-drain
       (Criterion 2 needs a live floor to seed from). Otherwise
       `init_state(live_floor, HISTORICAL_TRADES_FLOOR)` in a transaction
       and log both instants (Criterion 2). A row whose `watermark_ts` is
       already ≤ the floor → `floor_reached`, no requests (Criterion 3).
    5. [x] **Trades sub-drain.** Build `TradeSync(HistoricalTradeSource
       (source), trades, sink=NullSyncEventSink(), rule=rule,
       run_id=run_id, clock=clock, direction=BACKWARD, cap=cap −
       result.requests)`; `await inner.drain(watermark, HISTORICAL_
       TRADES_FLOOR)`; add its `requests` to the phase's; copy its counts;
       `floor_reached = inner.result.watermark_after <= floor`; log the
       unknown-prefix line through the inner core's method.
    6. [x] `set_last_full_sync(phase_start)` on the historical row.
    7. [x] `_finish`: `duration_ms`, one `phase_finished` event with
       `phase="historical"`, `counts()`, `error`.
  - [x] Success: Task 6.4's cases pass; each file is under the line
        guideline (the archive walk and the candle sub-drain are natural
        modules of their own if `historical_sync.py` runs long); the only
        `except` clauses are the re-raising one and the two
        `ProviderPermanentError` handlers named above, each with its comment.

- [x] **Task 6.3: Test fakes** (effort: 2)
  - [x] `test/kalshi_support/fake_historical_source.py`: `FakeHistoricalSource`
        — a scripted historical tape with `FakeTradeSource`'s window
        semantics and query recording (compose or subclass it; do not copy
        its paging), plus `candles_by_ticker: dict[str, list[Candlestick]]`
        served by `get_historical_market_candlesticks` filtered to the
        requested range, a scripted archive (`archive_pages:
        list[list[Market]]`, served newest page first with an opaque cursor,
        plus `FakeCatalogSource`'s event/series answers for parents), every
        query recorded, and `raise_on` for all three methods.
  - [x] Extend `FakeTradeRepository` with the surface parameter,
        `read_live_coverage_from` (a settable attribute), and the cursor
        pair; the walk's catalog writes go through the existing
        `FakeRepository` (262's), so its recorded upserts prove what the
        archive contributed; extend
        `FakeCandleRepository` with `pending_behind_cutoff` (served from a
        scripted list, honouring `limit`) and a settable candles sync-state
        row. Their self-tests in `test_fakes.py` gain one case each.
  - [x] Success: `test_fakes.py` passes; the fakes satisfy the protocols
        under pyright.

- [x] **Task 6.4: `HistoricalSync` unit tests** (effort: 4)
  - [x] `test/unit/data/kalshi/test_historical_sync.py`, a `Harness` as
        `test_trade_sync.py` has (source, both fake repositories, sink, fixed
        clock, `cap` argument):
    1. [x] first run seeds the row at the live floor with `coverage_from ==
       HISTORICAL_TRADES_FLOOR` and logs both (Criterion 2).
    2. [x] no live row → `trades_row_missing`, no trades query, candles
       still drained.
    3. [x] no candles row → candles skipped, trades still drained.
    4. [x] candles: a market spanning 3 × `CANDLE_SINGLE_MAX_CANDLES`
       periods costs exactly three requests, its rows land, and its state
       row is stamped `watermark = close + period, coverage_from = open`;
       `candle_markets_completed` equals the pending list's length under
       the per-pass limit.
    5. [x] the cap is shared: with `cap` equal to the candle requests plus
       one, the trades sub-drain runs exactly one window and reports
       `capped`; with `cap` smaller than the first market's requests, that
       market completes and no further market starts.
    6. [x] the watermark moves down by whole hours and stops at the floor;
       a run starting at the floor makes no trades request and reports
       `floor_reached` (Criterion 3).
    7. [x] `fetched = written + unknown + excluded + duplicates` on the
       phase's counts (Criterion 4).
    8. [x] a provider error in the candles sub-drain aborts before any
       trades request and leaves the historical row untouched; a provider
       error mid-window leaves the watermark at the previous window's
       start (Decision 6).
    9. [x] the slow-market warning fires above the threshold (clock
       stepped) and is counted.
    10. [x] exactly one `phase_finished` event, `phase == "historical"`, no
        `trades` event.
    11. [x] `to_dict` matches the design's fields and round-trips.
    12. [x] `classify_historical`: `PARTIAL` iff item errors and no abort;
        refuses an unclassified exception.
    13. [x] archive walk: three scripted pages, the third entirely older
        than the floor minus the margin → exactly three archive requests
        (plus the parent lookups the fake answers), every market of the
        first two pages upserted through the catalog fake, the cursor saved
        after pages one and two and NULL at the end, `archive_walked` true;
        a second run makes no archive request (Criterion 9).
    14. [x] archive walk resumes: `cap` smaller than the walk → `capped`,
        cursor saved, **no candles and no trades request**; the next run
        starts from the saved cursor (the fake asserts the cursor it
        received) and finishes.
    15. [x] archive cursor rejected on resume (`ProviderPermanentError` on
        the first request of the run) → `archive_restarted`, the walk
        starts from page one and completes.
    16. [x] a `ProviderPermanentError` on one market's candles → that ticker
        in `item_errors`, no state row for it, the other markets stamped,
        the trades sub-drain run, outcome `PARTIAL` (Criterion 10); a
        `ProviderTransientError` on the same request aborts.
    17. [x] once the watermark is at the floor, the candle sub-drain takes
        every pending market the cap allows (no per-pass ceiling).
  - [x] Success: the seventeen cases pass.

- [x] **Task 6.5: Section 6 gates and checkpoint commit** (effort: 1)
  - [x] Gates as part 1's Context Summary, scoped to the files touched.
  - [x] Commit: `feat: add HistoricalSync — behind-cutoff candles and the
        backward trades drain`.

## Section 7: The phase, the renderer, and `status`

Design *Implementation Details* (`collection_pass.py`, `kalshi_render.py`,
`trade_status.py`/`status.py`), *Technical Decision 8*, Criteria 1, 6, 7.

- [x] **Task 7.1: `HistoricalPhase` and `PASS_PHASES`** (effort: 2)
  - [x] `PassPhaseName.HISTORICAL = "historical"`; `HistoricalPhase` copies
        `TradesPhase`'s shape and its exact `except` pair. It computes `cap
        = run.client.rate_limit.requests_per_minute ×
        HISTORICAL_PHASE_MINUTES` and logs `kalshi historical cap=%d
        (%d/min × %d min, mode=%s)` before constructing the core (Criterion
        6's first clause). Repositories: `TradeRepository(run.conn, rule,
        surface=Surface.HISTORICAL)` and `CandleRepository(run.conn, rule)`,
        `CatalogRepository(run.conn)`.
  - [x] `PASS_PHASES` becomes four entries; the registration comment says
        why historical is last (Decision 1).
  - [x] Tests in `test_collection_pass.py`: the names-and-order case lists
        four; a trades abort leaves historical `skipped`; a historical abort
        leaves the three earlier reports intact and the pass outcome is the
        abort (Criterion 1); the cap is computed from the run's client
        budget (a fake client with `requests_per_minute=10` → `cap == 300`).
  - [x] Success: the cases pass; `mt data kalshi pass --json` on the
        integration database (Task 8.1) lists four phase names.

- [x] **Task 7.2: Phase renderer** (effort: 2)
  - [x] `print_historical_summary` in `kalshi_render.py` from
        `HistoricalResult.to_dict()`: one line `requests n / cap m (capped)`,
        one for the archive (`walked` or `pages · markets · cursor saved`),
        one for candles (`markets completed · requests · candles written ·
        remaining · slow`), one for the watermark (`before → after`, `floor
        reached` when true), one for the trades counts in
        `print_trade_summary`'s order, the item errors one per line as
        `print_candle_summary` prints them, and the `no live trades row`
        note when `trades_row_missing`. Register it in `PHASE_RENDERERS`.
  - [x] Test: every `PassPhaseName` has a renderer (extend the existing
        registry test); the summary renders the Task 6.4 payload without
        error.
  - [x] Success: `render_phase_summary(PassPhaseName.HISTORICAL, …)` prints;
        the registry test passes.

- [x] **Task 7.3: The effective floor and `historical_status.py`** (effort: 3)
  - [x] New `data/kalshi/historical_status.py` (the `trade_status.py`
        pattern; imports neither client nor transport — extend
        `test_status_imports.py`): `HistoricalStatus(last_phase_at,
        archive_walked, archive_in_progress, tape_from, tape_to, floor,
        floor_reached)` with `to_dict()` (`tape_from` is `None` until the
        walk is done and the row is seeded), and
        `read_historical_status(conn) -> HistoricalStatus | None` reading
        the `historical` row and the live row's `coverage_from_ts`
        (`tape_to`). `None` until the phase has run.
  - [x] Decision 8 in `trade_status.py`: `read_trade_status` reads the
        `historical` row's `watermark_ts` (one more short read) and binds
        `coverage_from = min(trades.coverage_from_ts, historical.watermark_ts)`
        to `TRADE_COUNTS`; `TradeStatus.coverage_from` is that effective
        floor. The partition and its sum check are untouched. Update the
        module docstring's `before_coverage` clause ("closed before the
        effective floor").
  - [x] Unit: `test_trade_status.py` and a new `test_historical_status.py`
        over a fake connection — the effective floor is the live floor
        without a historical row and the minimum with one.
  - [x] Integration, `test_kalshi_status.py`: seed a live row and a
        historical row with a lower watermark and markets closed between
        the two; assert `coverage_from` is the historical watermark,
        `before_coverage` counts only markets closed before it, and the four
        buckets still sum (Criterion 7). `read_historical_status` returns
        every field; `None` with no row.
  - [x] Success: unit and integration cases pass; `status.py` is not longer
        than it is today.

- [x] **Task 7.4: Status line, Rich and JSON** (effort: 2)
  - [x] `kalshi.py::kalshi_status` reads `read_historical_status(conn)` in
        the same connection; JSON payload gains `"historical"`
        (`to_dict()` or `null`), with `behind_cutoff_candles_remaining`
        taken from `candles.behind_cutoff_uncollected` — the count is read
        once, in the candle block (design *Implementation Details*).
  - [x] Rewrite the existing `before coverage` line in `print_trade_status`
        (`kalshi_render.py:318`): its caveat "tape predates the collector;
        slice 266" is the one the design's *Value* section retires — it now
        reads `closed before the effective floor <coverage_from>`, and
        `grep -rn "266" src/` finds nothing afterwards (part 1, Task 2.1's
        success check).
  - [x] `print_status` gains `historical`; `print_historical_status` prints
        the design's line: `historical tape <tape_to> → <tape_from> (floor
        <floor>) · behind-cutoff candles remaining n · last phase <when>`,
        with `floor reached` replacing the arrow form when true, `archive
        walk in progress` replacing the tape range while the cursor is set,
        and `Historical: never run` when `None`.
  - [x] Test: in `test/integration/test_kalshi_status.py`, drive
        `kalshi_status` through the CLI runner against `kalshi_db` — JSON
        has the `historical` key in both states; the Rich line renders.
  - [x] Success: `mt data kalshi status --json | jq .historical` on the
        rehearsal database (Section 9) prints the fields.

- [x] **Task 7.5: Section 7 gates and checkpoint commit** (effort: 1)
  - [x] Gates as part 1's Context Summary, scoped to the files touched.
  - [x] Commit: `feat: add the historical pass phase, its renderer, and the
        effective coverage floor in status`.

## Section 8: End-to-end integration

Criteria 1, 4, 5, 7 against a real database.

- [x] **Task 8.1: Four-phase pass, end to end** (effort: 3)
  - [x] In `test/integration/test_kalshi_pass.py`, extend the pass source
        (`_ThreeSurfaceSource` becomes four) with the historical fixtures:
        one archive page whose markets are **not** in the seeded catalog and
        settle before the floor minus the margin, a historical tape covering
        three hours below a seeded live floor that includes trades for those
        archive-only markets, and one behind-cutoff market's candles. Seed
        the catalog with the tape's other markets (`write_catalog`), the
        candles row (cutoff after the market's settlement), and the live
        trades row.
  - [x] First pass: `phases[].name` is the four names, all `ok`; the
        archive page's markets are in `kalshi.markets` with their parents
        and the trades for them were **written** (Criterion 9); the
        historical row exists at the live floor with the floor target with
        a NULL cursor; the
        watermark descended by whole hours; the candle market has rows and a
        state row and `count_behind_cutoff` fell by one (Criterion 5); the
        identity holds on the summary (Criterion 4); `status --json`'s
        `coverage_from` equals the new watermark and `before_coverage`
        moved by the markets closed in the walked hours (Criterion 7).
  - [x] Second pass over the same fixtures writes 0 trade rows and 0
        candles, the live row is byte-identical, and the watermark does not
        move past the floor.
  - [x] Abort: a source that raises `ProviderTransientError` on the first
        historical candles request leaves catalog, candles, and trades
        reports `ok` with their state intact, historical `provider_abort`,
        exit code `EXIT_BY_OUTCOME[PROVIDER_ABORT]`; the same request
        raising `ProviderPermanentError` gives historical `partial`, exit
        `EXIT_BY_OUTCOME[PARTIAL]`, the ticker in the summary's
        `item_errors`, and the trades watermark still descended (Criterion
        10).
  - [x] Success: the four cases pass in the integration tier.

- [x] **Task 8.2: Full-tier run and checkpoint commit** (effort: 1)
  - [x] `uv run pytest test/unit -q` and the integration tier both green;
        gates over every file the slice touched.
  - [x] Commit: `test: cover the four-phase pass end to end`.

## Section 9: Rehearsal on the test cluster

Design *Verification*. Every step is **[agent]** against a throwaway
database on the 917 test cluster; export `MT_TIMESCALE_DB_URL` for it in
the rehearsal shell only. Record observed output as you go.

- [ ] **Task 9.1: Throwaway database, migrated, with a catalog** (effort: 2)
  - [ ] Create the database by generated name; `mt data migrate apply
        --track kalshi` → `kalshi_007_historical_surface` applied, 0
        pending; `mt data kalshi sync --settled-since "$(date -u -d '6
        hours ago' +%FT%TZ)"` for a catalog.
  - [ ] Seed one behind-cutoff market: the ticker in
        `historical_candles_market.json`, its market object fetched with
        `KalshiTransport.get_json("/historical/markets/{ticker}")` and
        written with its parent event and series through
        `CatalogRepository.upsert_*` (a ten-line snippet in the note, not a
        script); then run one `mt data kalshi pass` so the candles row
        holds a cutoff, and confirm `status` shows `behind cutoff,
        uncollected 1`.
  - [ ] Success: outputs captured; the market is in the behind-cutoff set.

- [ ] **Task 9.2: Two passes under a small cap** (effort: 3)
  - [ ] Seed the live `trades` row as production has it: `coverage_from_ts`
        = the cutoff's `trades_created_ts`, `watermark_ts` the same
        (nothing live to drain). Run the passes at the public default (cap
        9,000) — the archive walk needs most of it (Decision 9's estimate),
        and this is the measurement that replaces the estimate.
  - [ ] Pass 1 (expect an hour or more — the walk upserts millions of
        rows): capture the cap line (`cap=9000 (300/min × 30 min,
        mode=public)`), the archive walk's per-page progress and its done
        line **or** its cap-reached line with the cursor saved (then pass 2
        resumes and finishes it — capture the resume line; the trades
        drain starts only after). **Record the archive's page count to the
        stop point, the wall time, and the catalog's market count before
        and after** — the numbers Decision 9 estimated. Then the candle
        market's completion and timing line, the
        historical start line with both instants (Criterion 2), the
        per-window lines walking down, the cap-reached line, and
        `catalog=ok candles=ok trades=ok historical=ok` (Criterion 1).
        Verify the identity on the JSON summary (Criterion 4); verify
        `behind cutoff, uncollected` is 0 and the market has candle rows
        and a state row (Criterion 5); verify the historical watermark is a
        whole hour below the live floor and the live row is unchanged
        (Criteria 3, 7).
  - [ ] Pass 2 (or 3, if the walk spilled): the watermark descends again
        and no archive request is made (Criterion 9); a re-walk (seed it
        back up one hour) writes 0 rows (Criterion 4's second clause).
        Check in the journal's per-window lines that `unknown` is now the
        MVE prefixes only — the check that the archive walk did its job.
  - [ ] `mt data kalshi status` prints the historical line and the trades
        block's `coverage from` equals the historical watermark; capture.
  - [ ] **Record per-window wall time** for comparison with 265's 0.21
        s/page and with the first production firing.
  - [ ] Success: every assertion holds; outputs captured verbatim.

- [ ] **Task 9.3: Rehearsal note and teardown** (effort: 2)
  - [ ] Write `user/notes/2026-MM-DD-267-rehearsal.md` (real date) with the
        captured output, the timings, the archive measurements (replace
        Decision 9's estimate in the design with them — commit that with
        the note), and the two things the rehearsal did **not** do, each with
        where the proof lives: the authenticated cap
        of 30,000 (proven by Task 7.1's computed-cap test; observed on the
        host by Task 11.2) and a multi-firing descent to the floor (the
        handoff below).
  - [ ] Drop the throwaway database by its exact name; confirm
        `MT_TIMESCALE_DB_URL` is unset from the shell.
  - [ ] Commit: `docs: record the 267 rehearsal on the test cluster`.

## Section 10: Documentation and version

- [x] **Task 10.1: Runbook 100, Kalshi subsection** (effort: 2)
  - [x] The paragraph the design specifies: four phases since this release;
        the first firing walks the market archive into the catalog and runs
        hours under the run lock (overlapping firings exit 1, expected);
        the historical phase self-limits to `HISTORICAL_PHASE_MINUTES` of
        the client's budget (30,000 requests authenticated, 9,000 public)
        and stops at `HISTORICAL_TRADES_FLOOR`; how to read the status
        line; extending the floor is one constant edit; `kalshi_007` must
        be applied during the update (the usual exit-1 firing between
        install and apply); an unserved market shows as `historical=partial`
        with its ticker in the journal and retries each firing; the
        slow-market warning and the manual compression-pause lever
        (unchanged, never automated).
  - [x] Success: `grep -n` finds `kalshi_007`, `HISTORICAL_TRADES_FLOOR`,
        and `historical tape` in the Kalshi subsection.

- [x] **Task 10.2: CHANGELOG and version** (effort: 1)
  - [x] `## [0.12.0]` with the date: the historical phase, the archive walk
        into the catalog (the first firing runs hours), the status line and
        the effective floor, `kalshi_007`, the three client methods; note
        that the pass then runs up to ~40 minutes during the drain.
        `pyproject.toml` version `0.12.0`.
  - [x] Commit: `docs: document the historical backfill phase (0.12.0)`.

## Section 11: Production deploy — Project Manager

Design *Verification* (host), Criterion 8. Two PM acts — cutting the
release and reading the report — with one script between them, as 265.

- [x] **Task 11.1: `scripts/cutover_267_historical.py`** **[agent]** (effort: 3)
  - [x] Model it on `cutover_265_trades.py` step for step (hold the timer,
        install the ref, migrate, first firing streamed, report, release the
        timer), minus the settings rename. Its report checks, from the
        journal of that one firing and `status --json`: the client line
        reads `mode=authenticated budget=1000/min`; the cap line reads
        `cap=30000`; the archive walk's done line with its page and market
        counts (or, if the walk was capped, its cursor-saved line — then
        the rest of the checks read from the *next* firing, which the
        script waits for through the unit, as it waits for the first);
        `catalog=ok candles=ok trades=ok historical=ok|partial` (Criterion
        8; `partial` lists the tickers); the historical row was created at
        the live floor with the floor target; the watermark descended ≥ 1
        hour;
        `candle_markets_completed ≤ 1,000` and `behind cutoff, uncollected`
        fell by exactly that number (Criterion 5); 429 count; total pass
        duration < 45 min (Criterion 6) **for the first firing after the
        walk** — the walk's own firing is reported as its duration and the
        catalog's growth; the slowest window and slowest market as numbers.
        Writes `user/notes/<date>-267-cutover.md`.
  - [x] Unit test the report's parsing against a journal excerpt
        (`test/unit/test_cutover_267.py`; the 265 script shipped without
        one — do not repeat that).
  - [x] Commit: `chore: add the 267 cutover script`.

- [ ] **Task 11.2: Cut and run** **[PM]** (effort: 1)
  - [ ] Merge `267-slice.historical-backfill-phase` into `main`, tag
        `v0.12.0`, push both; then from `main`:
        `uv run python scripts/cutover_267_historical.py v0.12.0`.
  - [ ] Success: exit 0 — every check in the report ✅. A ❌ on the mode
        line means the key pair is not in `/etc/manta-trading.env` (the
        collector still ran, at the public cap); fix and let the next firing
        prove it. A ❌ on the slowest market means candle writes into
        compressed chunks are slow — the pause lever in runbook 100.

- [ ] **Task 11.3: Close the slice from the report** **[agent]** (effort: 2)
  - [ ] Fill the design's *Verification* host paragraph with the observed
        output and a *Success criteria — where each is proven* table for
        1–8; journal entry in `user/notes/000-process-journal.md` for what
        outlives the slice (the first firing's per-window and per-market
        timings; the measured endpoint costs).
  - [ ] Set `dateUpdated` on the design, runbook 100, and both task files;
        the design's `status: complete`. Delegate checklist updates to the
        `task-checker` agent.
  - [ ] Commit: `docs: close slice 267 from the cutover report`.

**Handoff, not a task — the descent to the floor.** Criterion 8's second
half (the status line's tape range growing downward over the following
firings, ~15 at the authenticated cap, and `behind cutoff, uncollected`
reaching 0 in the first nine) is an observation over hours and days, and no
task here may wait on it. The mechanisms are proven without waiting: the
per-firing descent and the floor stop by Task 6.4 cases 5–6 and Task 9.2,
the cap by Task 7.1 and the first firing's cap line, the stamping by Task
8.1, the archive walk by Task 6.4 cases 13–15 and the rehearsal's measured
walk. The PM watches `mt-run data kalshi status` at any later hour; a
watermark that has not moved across firings while `floor reached` is absent
is the signal to act on.
