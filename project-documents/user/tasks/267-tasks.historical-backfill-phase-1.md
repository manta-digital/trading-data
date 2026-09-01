---
docType: tasks
slice: historical-backfill-phase
project: trading-data
lld: user/slices/267-slice.historical-backfill-phase.md
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [264, 265]
interfaces: []
projectState: >
  Slices 264 and 265 are complete and cut over on manta9000 (v0.11.0; the
  host runs 0.11.3 with the health check from 919). The hourly
  mt-kalshi-pass.timer runs PASS_PHASES = (CatalogPhase(), CandlesPhase(),
  TradesPhase()); the live trades drain is in progress (tape through
  2026-07-09, coverage_from 2026-07-01, before coverage 20,937,
  behind-cutoff candles 8,394 on 2026-08-31). The kalshi migration track
  is applied through kalshi_006_trades. The PM's Kalshi API key is
  installed on the host (2026-08-31); the dev checkout has no key. The
  client already has get_historical_cutoff() and RSA signing (261); no
  other /historical/* method exists. Design 267 has Decisions 2 and 3
  PM-ratified 20260831; slice review CONCERNS addressed (3bbc3d6, 4cafb25).
reviewVerdictsAddressed:
  - 267-review.tasks.historical-backfill-phase.part-1, first round (claude-sonnet-5, CONCERNS) — F001 the four remaining slice-266 sites assigned (Task 2.1 comments; Task 7.4 the status line); F002 fixtures now precede the client tests (Tasks 3.2/3.3 swapped); F003 Task 1.2 split into [PM] 1.2 and [agent] 1.3; F004–F005 pass
  - 267-review.tasks.historical-backfill-phase.part-2, first round (claude-sonnet-5, CONCERNS) — F001 load-test waiver recorded in the Context Summary with its reasoning; F002–F004 pass
dateCreated: 20260831
dateUpdated: 20260901
status: not_started
---

## Context Summary

- Working on **267 Historical Backfill Phase** — a fourth phase
  (`historical`) of the hourly Kalshi pass, after `trades`. Each firing it
  (1) fetches candles for up to `HISTORICAL_CANDLE_MARKETS_PER_PASS` markets
  of the behind-cutoff set through `/historical/markets/{ticker}/candlesticks`
  and stamps them, then (2) walks `/historical/trades` **backward** in
  one-hour windows from the live tape's floor toward
  `HISTORICAL_TRADES_FLOOR`, under one request cap sized from the client's
  rate budget (`requests_per_minute × HISTORICAL_PHASE_MINUTES`). It
  replaces the retired operator-run slice 266.
- Source of truth: the slice design at
  `user/slices/267-slice.historical-backfill-phase.md`. Its **Architecture**,
  **Technical Decisions 1–10**, **Implementation Details**, **Success
  Criteria 1–10**, **Verification**, and **Risks** are referenced by number
  below rather than restated. Read the design before starting any section.
- **No new unit, timer, command, or operator step.** The only host facts
  this slice depends on already exist: the key (Decision 2) and the timer.
  The deploy artifacts touched are the runbook, the CHANGELOG, and one
  cutover script.
- Code to reuse, not reinvent: `trade_sync.py::TradeSync` (the window loop
  — this slice parameterises its direction, Decision 5), `trade_repository.py`
  (`write_page`, the `sync_state` statements — gains a surface parameter),
  `candle_repository.py` (`pending_backlog`'s shape for the behind-cutoff
  query, `insert_candles`, `advance_state`), `candle_selection.py::
  BEHIND_CUTOFF_CONDITION` (the set, already defined — never re-spelled),
  `collection_pass.py::TradesPhase` (the phase shape and its exact `except`
  pair), `test/kalshi_support/` fakes, `scripts/record_kalshi_fixtures.py`,
  `scripts/cutover_265_trades.py` (the deploy script pattern),
  `user/notes/2026-08-30-265-rehearsal.md` (the rehearsal note shape).
- Hard rules for this slice:
  - **Every comparison value is a named constant** in `constants.py`, cited
    to its decision. The cap is **computed** from the client's budget, never
    a literal (Decision 2).
  - **The collection rule is rendered only by `selection.selection_sql`**;
    the behind-cutoff set is `BEHIND_CUTOFF_CONDITION` composed, not copied.
  - **Ticker text is never logic**; the unknown-prefix tally stays display-only.
  - The phase catches exactly `ProviderError` and `psycopg.OperationalError`
    (Decision 6). No catch-all.
  - `status.py` is 305 lines and `trade_status.py` 137: the historical
    status reader is a **new module**; `read_trade_status` changes by one
    parameter (Decision 8).
  - Nothing references `public`; exit codes are 262's `EXIT_BY_OUTCOME`.
  - **This checkout's `.env` points at the production database** (the dev
    checkout lives on manta9000). No task below points a test or a rehearsal
    at it: the integration tier runs only through
    `uv run python scripts/run_tests.py integration -- -k kalshi -q`, and
    the rehearsal exports `MT_TIMESCALE_DB_URL` for a throwaway database on
    the test cluster (917) in the shell that runs it, then unsets it.
- Tests: unit tier `uv run pytest test/unit -q`; integration as above. Gates
  per section: `uv run ruff check` and `uv run ruff format --check` scoped
  to the files touched; `uv run --extra dev mypy` and `npx --yes pyright`
  over the kalshi source paths plus the touched tests **in one invocation**
  (the kalshi_support path artifact).
- Branch per CLAUDE.md: `267-slice.historical-backfill-phase` from `main`
  (`git.integration_branch` is unset — re-verify with `cf config get
  git.integration_branch` before branching). Commit checkpoints close each
  section. Merge and tagging are not tasks.
- Host boundary as 265: **[PM]** tasks run on manta9000 with elevation;
  **[agent]** tasks need none. **No task waits on a wall-clock event** — the
  ~15-firing drain is a handoff note at the end of part 2.
- **No load test, by decision.** Criterion 6's 45-minute bound is a
  property of Kalshi's live latency, not of this code: the phase bounds
  itself in **requests** (the cap, computed from the budget and asserted at
  the unit tier — Tasks 2.2 and 7.1), and the wall-clock figure follows from
  the provider's response times, which no fixture-driven load test can
  reproduce. A `test/load/` case timing fakes would assert nothing real.
  The bound is measured where it can be — once by the rehearsal (Task 9.2)
  and once by the cutover report (Task 11.1) — and every later firing's
  duration is in the journal and `mt-run status`. This is the 264 precedent
  (`264-tasks.candlestick-collection-1.md`, "no load test is required")
  applied to a threshold that is external, and it is recorded here so the
  omission is a decision, not a gap.
- **Effort ceiling.** Two tasks sit at 4: Task 6.2 (`HistoricalSync.run` is
  the design's *one firing* diagram end to end) and Task 6.4 (its unit
  suite). Everything else is ≤ 3.
- **This file is part 1 of 2.** Sections 1–5 do the endpoint-cost discovery,
  constants and migration, the client methods and fixtures, the repository
  seams, and the direction-parameterised window loop. The core, the phase,
  `status`, integration, rehearsal, documentation, and the host steps are in
  `user/tasks/267-tasks.historical-backfill-phase-2.md`.

## Section 1: Discovery — what `/historical/*` costs

Design *Risks*, first item. The answer changes only the drain's length, but
it is the one unknown the design asked to be read before anything is built.

- [ ] **Task 1.1: `scripts/kalshi_endpoint_costs.py`** **[agent]** (effort: 1)
  - [ ] A read-only script: `Settings(_env_file=<path from --env-file>)` →
        `load_credentials(...)` → `KalshiTransport(credentials=...)` →
        `get_json("/account/endpoint_costs", {})` → print the body as JSON.
        Exit non-zero, naming the variable, when the settings hold no key
        pair — the endpoint is authenticated and a public call would 401.
  - [ ] The path is a constant in the script (`ENDPOINT_COSTS_PATH`) — it is
        read once and nothing under `data/kalshi` uses it, so it does not
        belong in `constants.py`.
  - [ ] Success: `uv run python scripts/kalshi_endpoint_costs.py --env-file
        .env` exits non-zero with the message (no key in the dev `.env`);
        ruff clean.

- [ ] **Task 1.2: Read the costs on the host** **[PM]** (effort: 1)
  - [ ] On manta9000: `sudo uv run python
        scripts/kalshi_endpoint_costs.py --env-file /etc/manta-trading.env`
        (root reads the PEM; the file's mode is 0640 root:manta-trading).
        Paste the body back.
  - [ ] Success: the JSON body is in the conversation.

- [ ] **Task 1.3: Record the costs in the design** **[agent]** (effort: 1)
  - [ ] Record in the design's *Risks* first bullet the cost of
        `/historical/trades`, `/historical/markets/{ticker}/candlesticks`,
        and — for the comparison — `/markets/trades`. If any historical
        cost exceeds the 10-token default, restate Decision 3's firing count
        from the measured cost; the cap constant does not change (it is in
        requests; the 429 backoff absorbs the difference — design *Risks*).
  - [ ] Commit: `docs: record /historical endpoint costs in the 267 design`.
  - [ ] Success: the design names the three costs as numbers with the date.

## Section 2: Constants and migration `kalshi_007`

Design *Implementation Details* (`constants.py`), *Technical Decision 7*.

- [ ] **Task 2.1: Constants** (effort: 1)
  - [ ] In `constants.py`, a new block *Historical backfill (slice 267)*:
        `HISTORICAL_MARKETS_PATH = "/historical/markets"`,
        `HISTORICAL_TRADES_PATH = "/historical/trades"`,
        `HISTORICAL_MARKET_CANDLESTICKS_PATH =
        "/historical/markets/{ticker}/candlesticks"` (no series segment —
        261 Discovery), `HISTORICAL_PHASE_MINUTES = 30` (Decision 2),
        `HISTORICAL_CANDLE_MARKETS_PER_PASS = 1_000`,
        `HISTORICAL_TRADES_FLOOR = datetime(2026, 1, 1, tzinfo=UTC)`
        (Decision 3, PM-ratified 20260831), `HISTORICAL_ARCHIVE_STOP_MARGIN
        = timedelta(days=1)` (Decision 9), `HISTORICAL_SLOW_MARKET_SECONDS
        = 30` (Decision 4). Each cites its decision in its comment.
  - [ ] `Surface.HISTORICAL = "historical"`.
  - [ ] Fix the two stale comments on `KALSHI_CANDLE_COMPRESS_AFTER` and
        `KALSHI_TRADE_COMPRESS_AFTER` ("266's backfill pauses the policy")
        — Decision 4: the policies stay on; the manual lever is runbook 100.
        Fix `TradesBehindCutoffError`'s docstring and message in
        `trade_types.py` (they name slice 266 as the remedy; the remedy is
        now this phase). Fix the three comments that call the behind-cutoff
        set "266's input": `candle_sync.py:111`, `candle_repository.py:194`,
        `candle_selection.py:26` — it is this phase's input. The
        user-facing `before coverage` line in `kalshi_render.py:318` and the
        `trade_status.py` docstring are rewritten by Task 7.4 and Task 7.3
        (part 2), where their meaning changes; `client.py:376` by Task 3.1.
    - [ ] Success: `grep -rn "266" src/` finds only the three sites named
        above as later tasks' (`kalshi_render.py`, `trade_status.py`,
        `client.py`) — and after part 2's Section 7, none; `uv run pytest
        test/unit/data/kalshi/test_constants.py -q`
        is green (Task 2.2 extends it).

- [ ] **Task 2.2: Constants tests** (effort: 1)
  - [ ] Extend `test/unit/data/kalshi/test_constants.py`: the three paths
        match the design (and the candles path has no `series_ticker`
        field); the stop margin is positive; `Surface` has four members with
        `historical` last; the floor is timezone-aware UTC and at a whole
        hour; the per-pass and minutes constants are the design's values;
        `HISTORICAL_PHASE_MINUTES ×
        KALSHI_AUTHENTICATED_RATE_LIMIT.requests_per_minute == 30_000` and
        `× KALSHI_PUBLIC_RATE_LIMIT.requests_per_minute == 9_000` (Decision
        2's two figures, asserted where they are derived).
  - [ ] Success: the new cases pass; the existing `test_surface_values` is
        updated, not duplicated.

- [ ] **Task 2.3: Migration `kalshi_007_historical_surface`** (effort: 2)
  - [ ] Append to `KALSHI_MIGRATIONS` in
        `market/schema/migrations/kalshi.py`. SQL, idempotent: `ALTER TABLE
        kalshi.sync_state DROP CONSTRAINT IF EXISTS
        sync_state_surface_check;` then `ADD CONSTRAINT
        sync_state_surface_check {_surface_check_sql()}` — rendered from
        `Surface`, as kalshi_003 does, so the enum stays the single source.
  - [ ] `COMMENT ON` replaces whole strings: carry the existing catalog,
        candlesticks, and trades clauses of `sync_state.watermark_ts` and
        `sync_state.coverage_from_ts` forward **verbatim** and add the
        historical clause to each — `watermark_ts`: "historical: the oldest
        hour fully walked backward; moves DOWN, never past coverage_from_ts";
        `coverage_from_ts`: "historical: the target floor
        (HISTORICAL_TRADES_FLOOR), recorded so status can show the distance".
        Update the table comment's surface list.
  - [ ] Explain in the migration's comment why a fresh apply makes this a
        no-op (kalshi_003 already renders the widened CHECK) and why
        production still needs it (its constraint was rendered before
        `historical` existed).
  - [ ] Success: `mt data migrate status --track kalshi` against a throwaway
        test-cluster database lists `kalshi_007_historical_surface`; apply
        twice is clean.

- [ ] **Task 2.4: Migration integration tests** (effort: 2)
  - [ ] In `test/integration/test_kalshi_migrations.py`: add the id to the
        expected track list and a `HISTORICAL_ID` constant; a
        `TestHistoricalSurface` class with (a) in-track-and-reapplies, (b)
        **the production path**: after the full apply, replace the constraint
        by hand with one that omits `'historical'`, delete the
        `kalshi_007` ledger row, re-apply the track, and assert an
        `INSERT ... surface = 'historical'` succeeds; (c) the three comments
        contain both the carried clauses and the new one; (d) an unknown
        surface still fails the CHECK.
  - [ ] Success: the class passes in the integration tier; the existing
        `test_check_constraints_derive_from_enums` passes unchanged (it
        iterates `Surface`, so it now covers `historical` for free).

- [ ] **Task 2.5: Section 2 gates and checkpoint commit** (effort: 1)
  - [ ] Gates as the Context Summary, scoped to the files touched.
  - [ ] Commit: `feat: add historical surface constants and kalshi_007
        migration (slice 267)`.

## Section 3: Client methods and fixtures

Design *Implementation Details* (`client.py`), 261 Discovery *Historical
tier*: same shapes, same cursor pagination as the live endpoints.

- [ ] **Task 3.1: The three `/historical/*` client methods** (effort: 2)
  - [ ] `get_historical_markets(*, cursor=None, **query:
        Unpack[MarketsQuery]) -> MarketsPage` — a mirror of `get_markets` on
        `HISTORICAL_MARKETS_PATH`. The endpoint accepts only `tickers`,
        `event_ticker`, `series_ticker`, `mve_filter`, `limit`, `cursor`
        (Decision 9); document on the method that the settlement-window
        keys of `MarketsQuery` are **ignored by the API**, not rejected —
        the same pass-through posture `MarketsQuery`'s docstring already
        takes for `min_updated_ts`.
  - [ ] `get_historical_trades(*, cursor=None, **query: Unpack[TradesQuery])
        -> TradesPage` — a mirror of `get_trades` on
        `HISTORICAL_TRADES_PATH`; the same `TradesQuery` (no new TypedDict —
        the parameters are the same, Decision 5).
  - [ ] `get_historical_market_candlesticks(ticker, *, start_ts, end_ts,
        period_interval) -> list[Candlestick]` — a mirror of
        `get_market_candlesticks` on `HISTORICAL_MARKET_CANDLESTICKS_PATH`,
        parsed through the existing `CandlesticksResponse`; **no
        `series_ticker` argument** and no `include_latest_before_start`.
  - [ ] Update `get_historical_cutoff`'s docstring (it says the other
        historical methods belong to 266).
    - [ ] Success: the three methods type-check; the routed unit tests of
        Task 3.3 pass.

- [ ] **Task 3.2: Recorder and fixtures** (effort: 2)
  - [ ] In `scripts/record_kalshi_fixtures.py` add
        `record_historical_markets_page` — the first archive page at
        `limit=MARKETS_PAGE_LIMIT`, `mve_filter=exclude`, saved as
        `historical_markets_page` (must carry a cursor); print the page's
        first and last `settlement_ts` so the note can record the order.
  - [ ] Add `record_historical_trades_window` — reads the cutoff, takes a
        one-minute window seven days **before** `trades_created_ts` (a busy
        UTC afternoon minute; print the bounds as `record_trades_window`
        does), limit `TRADES_WINDOW_LIMIT`, saves `historical_trades_window`
        (must carry a cursor, else exit with the same retry message) and,
        after following the cursor to the end, `historical_trades_window_last`.
  - [ ] Add `record_historical_candles_market` — takes the first ticker of
        the page just recorded, requests one day of 1-minute candles around
        that trade through the new candles method, saves
        `historical_candles_market`. Reading the ticker from the fixture
        file (the `_recorded_tickers` helper) keeps the pair consistent.
  - [ ] Record them: `uv run python scripts/record_kalshi_fixtures.py --only
        historical_markets_page`, `--only historical_trades_window`, then
        `--only historical_candles_market`
        (public mode suffices — 261 verified the endpoints unauthenticated).
  - [ ] Extend `test/unit/data/kalshi/test_fixtures.py`: the completeness
        list gains the four names; the archive page parses into a
        `MarketsPage` whose every status is in `MarketStatus` and whose
        `settlement_ts` values are all set; each historical trades page parses
        into
        a `TradesPage` (the first with a cursor, the last without); the
        candles fixture parses with OHLC. Include a parity assertion: the
        historical trade's field set equals the live `trades_window`
        fixture's (261's "same shape", proven rather than assumed).
  - [ ] Success: the four files exist under `test/fixtures/kalshi/`; the
        fixture tests pass; `--dry-run` for the three recorders prints
        the expected HTTP 200 lines.

- [ ] **Task 3.3: Client endpoint unit tests** (effort: 2)
  - [ ] In `test/unit/data/kalshi/test_client_endpoints.py`, using the
        existing routed `Harness`: the markets method hits
        `/historical/markets` with `limit`, `mve_filter`, and `cursor` and
        parses a `MarketsPage`; the trades method hits the historical path
        with `min_ts`/`max_ts`/`limit`/`cursor` exactly as `get_trades` does
        (assert the recorded request URL and query); the candles method hits
        `/historical/markets/{ticker}/candlesticks` with the three params and
        nothing else; an unrouted path is still a permanent error.
    - [ ] Success: the cases pass against the fixtures Task 3.2 recorded —
        no hand-rolled bodies.

- [ ] **Task 3.4: Section 3 gates and checkpoint commit** (effort: 1)
  - [ ] Gates as the Context Summary, scoped to the files touched.
    - [ ] Commit: `feat: add the three /historical client methods with
        fixtures`.

## Section 4: Repository seams

Design *State*, *Technical Decision 5* (adapters over 265's abstractions),
*Architecture* step 1 (the behind-cutoff query).

- [ ] **Task 4.1: `TradeRepository` gains a surface** (effort: 2)
  - [ ] `TradeRepository.__init__(conn, rule, *, surface: Surface =
        Surface.TRADES)`; `read_state`, `advance_watermark`,
        `set_last_full_sync` bind `self._surface` instead of
        `Surface.TRADES`. `write_page` is untouched — the tape is one table.
  - [ ] `init_state(watermark, coverage_from)` replaces `init_state(cutoff)`
        (the live caller passes the cutoff twice — one call site in
        `TradeSync._state`). Same `ON CONFLICT DO NOTHING`.
  - [ ] New `read_live_coverage_from() -> datetime | None`: the live
        `trades` row's `coverage_from_ts`, or `None` when the live phase has
        never run (the historical row is seeded from it — Criterion 2).
  - [ ] New `read_cursor() -> str | None` and `set_cursor(cursor: str |
        None)` on `self._surface` — the archive walk's resume point
        (Decision 9; `sync_state.cursor` exists since kalshi_003 and
        `CatalogRepository._set_state_column` types its value as a
        datetime, so this is its own small statement, `ON CONFLICT` like
        the others). `None` clears it.
  - [ ] Success: the existing trades unit and integration suites pass
        unchanged apart from the `init_state` signature.

- [ ] **Task 4.2: `TradeRepository` integration tests** (effort: 2)
  - [ ] In `test/integration/test_kalshi_trades.py`'s state class: a
        repository built with `surface=Surface.HISTORICAL` reads `None`
        before any row, `init_state(w, f)` writes exactly that row and leaves
        the `trades` row absent, `advance_watermark` on the historical row
        moves **only** it; `read_live_coverage_from` returns `None` with no
        live row and the value once the live row exists; `set_cursor`
        round-trips a string
        and `None` clears it without touching the watermark. Both rows can
        coexist (the CHECK from Section 2).
  - [ ] Success: the cases pass in the integration tier.

- [ ] **Task 4.3: `CandleRepository.pending_behind_cutoff`** (effort: 2)
  - [ ] Beside `pending_backlog`: `pending_behind_cutoff(period, cutoff,
        limit) -> list[PendingMarket]` — `MARKET_JOIN` + the `"ever"` form +
        `BEHIND_CUTOFF_CONDITION` (composed, never re-spelled) + `m.open_time
        IS NOT NULL`, ordered `settlement_ts, ticker`, `LIMIT %(limit)s`.
        It does **not** go through `_pending` (that fragment's watermark
        clause is for the live sets) — say so in the docstring.
  - [ ] Its complement, `count_behind_cutoff`, already exists; the phase
        reports the count after its writes (Criterion 5).
  - [ ] Success: an integration test in `test/integration/test_kalshi_candles.py`
        — seed three finalized markets before the cutoff (one already
        stamped, one with a NULL `open_time`) and one after: the query
        returns exactly the one unstamped, open-timed, pre-cutoff market;
        `limit=0` returns nothing; the count excludes the stamped one.

- [ ] **Task 4.4: Section 4 gates and checkpoint commit** (effort: 1)
  - [ ] Gates as the Context Summary, scoped to the files touched.
  - [ ] Commit: `refactor: parameterise TradeRepository by surface and add
        the behind-cutoff query`.

## Section 5: `TradeSync` walks in either direction

Design *Technical Decision 5*, *Implementation Details* (`trade_sync.py`):
the window loop is parameterised by direction rather than duplicated.

- [ ] **Task 5.1: `WindowDirection` and `drain`** (effort: 3)
  - [ ] In `trade_types.py`: `class WindowDirection(StrEnum)`: `FORWARD =
        "forward"`, `BACKWARD = "backward"`.
  - [ ] `TradeSync.__init__` gains keyword-only `direction:
        WindowDirection = FORWARD` and `cap: int = TRADE_REQUESTS_PER_PASS`.
        The cap check in the loop reads `self.cap`; the log line names it.
  - [ ] Extract the loop into a public `async def drain(self, start:
        datetime, bound: datetime) -> None`. Forward: while `start < bound`,
        window `[start, min(start + TRADE_WINDOW, bound))`, watermark →
        `window_end`. Backward: while `start > bound`, window
        `[max(start − TRADE_WINDOW, bound), start)`, watermark →
        `window_start` (the far edge, Decision 5). `_window(lo, hi)` is
        called identically in both — the overlap steps `lo` back, `hi` is
        inclusive; nothing else in it changes.
  - [ ] `run()` keeps the live flow (cutoff, state, catalog bound) and calls
        `drain(state.watermark_ts, phase_end)`; it alone calls `_finish`.
        `drain` emits **no** event — the historical core owns its own
        `phase_finished` (part 2, Task 6.2), so the `trades` event is never
        emitted for a backward walk.
  - [ ] The per-window INFO line reads `{surface} window a→b …` from the
        repository's surface (or a `label` argument), so the journal tells
        the two drains apart.
  - [ ] Success: the whole existing `test_trade_sync.py` passes with no
        change to any expected query, watermark, or event — the forward path
        is a pure refactor.

- [ ] **Task 5.2: Backward-walk unit tests** (effort: 3)
  - [ ] In `test/unit/data/kalshi/test_trade_sync.py`, a `TestBackward`
        class over the existing fakes (`FakeTradeSource`,
        `FakeTradeRepository`) — the fake repository is given `surface` so
        its recorded watermark moves can be asserted per surface:
    1. [ ] windows step **down** from the start by whole hours; the last is
       clamped to the bound (`bound` not a whole hour → the last window is
       short, exactly like the forward clamp).
    2. [ ] the lower bound of every query still steps back by
       `WINDOW_OVERLAP`; `max_ts` is the window's top.
    3. [ ] the watermark moves once per window, to the window's **start**,
       only after the window's last page committed (page fake with
       `page_size` smaller than the window's rows).
    4. [ ] a provider error mid-window leaves the watermark at the previous
       window's start; an `OperationalError` on `write_page` likewise.
    5. [ ] `cap` is honoured before each window: with `cap=2` and four
       windows of work, `requests == 2`, `capped` is true, and a second
       `drain` from the recorded watermark continues from where it stopped.
    6. [ ] `start <= bound` drains nothing and reports `windows_completed
       == 0`.
    7. [ ] `drain` emits no `phase_finished` event (the sink stays empty).
  - [ ] Success: the seven cases pass; the forward suite is unchanged.

- [ ] **Task 5.3: Section 5 gates and checkpoint commit** (effort: 1)
  - [ ] Gates as the Context Summary, scoped to the files touched.
  - [ ] Commit: `refactor: parameterise the trades window loop by direction
        (slice 267)`.
