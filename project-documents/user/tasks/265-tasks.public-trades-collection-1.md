---
docType: tasks
slice: public-trades-collection
project: trading-data
lld: user/slices/265-slice.public-trades-collection.md
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [261, 262, 263, 264]
interfaces: [266]
projectState: >
  Slice 264 is complete and cut over on manta9000 at v0.10.0: the hourly
  mt-kalshi-pass.timer runs CollectionPass over
  PASS_PHASES = (CatalogPhase(), CandlesPhase()), and catalog, settlement,
  and 1-minute candle data accumulate unattended. The kalshi migration
  track is applied to production through kalshi_005. data/kalshi/ holds
  run_context.py, collection_pass.py (PassPhaseName with CATALOG and
  CANDLES only), sync.py, repository.py, status.py, client.py, models.py
  (Trade, TradesPage, HistoricalCutoff already present), constants.py
  (Surface.TRADES already present), db.py, events.py, candle_plan.py,
  candle_repository.py, candle_selection.py, candle_sync.py,
  candle_types.py. No trades table, no trades phase exists. The collection
  rule is candle-named throughout (MT_KALSHI_CANDLE_*, Settings.
  candle_rule(), CandleRule, candle_selection.selection_sql) and this
  slice renames it. Design 265 has Decisions 2, 3, 4 PM-ratified 20260828.
dateCreated: 20260829
dateUpdated: 20260829
status: not_started
---

## Context Summary

- Working on **265 Public Trades Collection** — the third and last phase of
  the Kalshi collection pass. It appends `TradesPhase` to `PASS_PHASES`,
  walks the exchange-wide public trade tape in one-hour windows oldest-first
  under a single watermark, stores every trade whose market the catalog
  knows and the collection rule selects into a new `kalshi.trades`
  hypertable, and gives `mt data kalshi status` a trades block.
- Source of truth: the slice design at
  `user/slices/265-slice.public-trades-collection.md`. Its **Discovery
  Findings**, **Technical Decisions 1–11**, **Implementation Details**,
  **Tests**, **Success Criteria 1–13**, and **Verification Walkthrough** are
  referenced by number below rather than restated. **Read the design before
  starting any section** — in particular Discovery Findings, because the
  measured tape volume (~420 k trades/hour across ~26 k markets) is why the
  phase walks one global tape in windows and never fetches per market, and
  the measured storage cost is what the PM ratified.
- **No unit, timer, installer, `mt-run`, `mt data kalshi pass` surface, or
  client change.** 261's `KalshiClient.get_trades(cursor, min_ts, max_ts,
  limit)` and `get_historical_cutoff()` already suffice; the timer that
  already runs picks the phase up. The only deploy artifacts touched are
  `deploy/manta-trading.env.example` (five commented lines, renamed) and
  runbook 100's Kalshi subsection.
- Code to reuse, not reinvent: `collection_pass.py::CandlesPhase` (the phase
  shape, its `try`/`except`/`classify`), `candle_sync.py::CandleSync` (core
  structure, event emission, per-unit INFO progress logging),
  `sync.py::CatalogSync` (the windowed settled-stream drain — 262's
  `WINDOW_OVERLAP` and watermark-per-window shape is what this phase copies),
  `repository.py::CatalogRepository` (`transaction()`, the `sync_state`
  statements, `psycopg.sql` composition), `candle_repository.py`
  (`CANDLE_COLUMNS` parity pattern, the bind-parameter discipline),
  `status.py::read_candle_status`, `test/kalshi_support/`
  (`fake_candle_source.py`, `fake_candle_repository.py`, `sync_harness.py`,
  `samples.py`), `test/integration/kalshi_helpers.py` and the `kalshi_db`
  fixture in `test/integration/conftest.py`,
  `scripts/record_kalshi_fixtures.py`.
- Hard rules for this slice:
  - **Every comparison value is a named constant** (CLAUDE.md). The page
    limit, the window, the guard, the per-pass cap, the staleness horizon,
    the chunk interval, the compression horizon — all in `constants.py`,
    each cited to its decision.
  - **The rule is rendered in exactly one place** — `selection.py::
    selection_sql(rule, form)`. `write_page`, the pending queries, the
    status queries, and no test may re-spell the predicate.
  - **Ticker text is never logic** (CLAUDE.md). The unknown-prefix tally is
    a display-only log line; no code branches on `KXMVE…` or any other
    ticker substring.
  - `status.py` imports neither the client nor the transport (Criterion 11;
    the existing `test_status_imports.py` guards this).
  - No new catch-all: the phase catches exactly `ProviderError` and
    `psycopg.OperationalError`, as `CandlesPhase` does. Any other
    `psycopg.Error` propagates as a bug (design *Repository*).
  - Nothing references `public` (261 extraction discipline).
  - Exit codes are 262's `EXIT_BY_OUTCOME` verbatim; no new exit constants.
  - Keep every new source file under the ~300-line guideline. `status.py` is
    **already 309 lines** — Task 5.1 extracts the trades reader into its own
    module rather than growing it further.
- Tests: unit tier `uv run pytest test/unit -q`; the integration tier only
  through `uv run python scripts/run_tests.py integration -- -k kalshi -q`
  (never with the production URL). Gates as 264: `uv run ruff check` and
  `uv run ruff format --check` **scoped to the files touched**, `uv run
  --extra dev mypy` and `npx --yes pyright` on the kalshi source paths plus
  the new tests, in a single invocation (kalshi_support path artifact).
- Branch per CLAUDE.md git rules: `265-slice.public-trades-collection`,
  forked from `main` (`git.integration_branch` unset — re-verify with
  `cf config get git.integration_branch` before branching). Commit
  checkpoints are marked at the end of each section with a semantic message.
  Merge and release tagging follow runbook 100's update procedure after PM
  approval and are not tasks here.
- Host boundary as 263/264: tasks marked **[PM]** are executed by the
  Project Manager on manta9000 (no passwordless sudo there); tasks marked
  **[agent]** need no elevation. No task waits on a wall-clock event.
- **This file is part 1 of 2.** Sections 1–4 below do the rename, the
  migration, the repository, and the core plus the phase. The `status`
  block, the rehearsal, the documentation, and the host steps are in
  `user/tasks/265-tasks.public-trades-collection-2.md`, which starts at
  Section 5 and depends on everything here.
- Next slice: 266 (historical backfill) consumes `coverage_from_ts` and
  `before_coverage`, reuses `write_page` against `/historical/trades`, and
  must pause this hypertable's compression policy during its drain.

## Section 1: The rename — one collection rule, surface-neutral

Design *Technical Decision 3* (PM-ratified 20260828), *Settings — the
rename*, *`selection.py`*. This section adds **no behavior**: it is a pure
rename plus one new guard and one new `SelectionForm` member. The candle
suite must be green at every step, and the candle phase must behave exactly
as before (Criterion 5, last clause).

- [ ] **Task 1.1: Create `selection.py` from `candle_selection.py`** (effort: 2)
  - [ ] `git mv src/manta_trading/data/kalshi/candle_selection.py
        src/manta_trading/data/kalshi/selection.py`, then recreate
        `candle_selection.py` holding only the candle-specific pieces.
  - [ ] `selection.py` keeps `Selection`, `SelectionForm`, `_TRADED_COLUMN`,
        `selection_sql`, and gains `CATALOG_JOIN` — the three-table join
        (`kalshi.markets m JOIN kalshi.events e JOIN kalshi.series s`)
        extracted from the current `MARKET_JOIN`.
  - [ ] `candle_selection.py` keeps `MARKET_JOIN` — **composed** as
        `CATALOG_JOIN` + the `LEFT JOIN kalshi.market_candle_state` clause,
        not re-spelled — plus `BACKLOG_CONDITION` and
        `BEHIND_CUTOFF_CONDITION`.
  - [ ] Update the module docstrings: `selection.py` says the rule governs
        candles **and** trades (Decision 3) and is rendered here only.
  - [ ] Success: `candle_selection.MARKET_JOIN` renders byte-identical SQL to
        the pre-rename version (assert in Task 1.5); no module imports
        `candle_selection` for `selection_sql` any more.

- [ ] **Task 1.2: Rename `CandleRule` → `CollectionRule`** (effort: 2)
  - [ ] Move the dataclass out of `candle_types.py` into `selection.py` as
        `CollectionRule` (design *Component Structure*). Fields, defaults,
        and `describe()` are unchanged.
  - [ ] Update every importer found by
        `grep -rn "CandleRule" src test` — `candle_repository.py`,
        `candle_sync.py`, `status.py`, `config/__init__.py`, and the tests
        listed in Task 1.5. `candle_types.py` re-exports nothing; imports
        move to `selection`.
  - [ ] Rename the bound-parameter names inside `selection_sql` from
        `candle_*` to `collection_*` (`collection_categories`,
        `collection_excluded_categories`, `collection_excluded_series_pattern`,
        `collection_excluded_title_pattern`) — a parameter named *candle* in
        a statement that classifies trades is the same trap as a setting
        named *candle*. Every caller binds `Selection.params` as a mapping,
        so no call site changes.
  - [ ] Success: `uv run pytest test/unit -q` passes after mechanical test
        updates; no occurrence of `CandleRule` remains outside a CHANGELOG or
        design document.

- [ ] **Task 1.3: Add the `"any"` selection form** (effort: 1)
  - [ ] Extend `SelectionForm` to `Literal["recent", "ever", "any"]`.
        `"any"` **omits the traded clause entirely** (Decision 3: a trade is
        proof of trading) — it is not a third `_TRADED_COLUMN` entry.
  - [ ] Implement by guarding the `rule.traded_only` clause on
        `form != "any"`, and say why in a comment citing Decision 3.
  - [ ] Document in the `SelectionForm` comment that `"recent"` is the live
        24 h window, `"ever"` is lifetime volume (used by `status` for both
        surfaces), and `"any"` is the trades write path.
  - [ ] Success: `selection_sql(rule, "any")` contains no `volume` reference
        for a rule with `traded_only=True`; `"recent"` and `"ever"` render
        exactly as before.

- [ ] **Task 1.4: Rename the settings and add the loud guard** (effort: 3)
  - [ ] In `config/__init__.py` rename the five fields
        `kalshi_candle_*` → `kalshi_collection_*` and
        `Settings.candle_rule()` → `Settings.collection_rule()`, returning
        `CollectionRule`. Defaults and validators are unchanged.
  - [ ] The environment form becomes `MT_KALSHI_COLLECTION_*`. Define the
        **old prefix and the new prefix each once** as module constants
        beside each other — the guard message and the fields both read them,
        so the pair is changed in one place.
  - [ ] Add a `model_validator(mode="after")` that scans `os.environ` for any
        key starting with the old prefix and raises, naming the offending
        variable and its new name. Rationale in the comment: pydantic-settings
        would otherwise **ignore** the old variable and silently fall back to
        the defaults, which CLAUDE.md forbids.
  - [ ] Update the two call sites: `cli/commands/kalshi.py:234` and
        `collection_pass.py:250` (`settings.candle_rule()` →
        `settings.collection_rule()`), and the renderer's literal at
        `cli/commands/kalshi_render.py:223` (`(MT_KALSHI_CANDLE_*)` →
        `(MT_KALSHI_COLLECTION_*)`).
  - [ ] Rename the five commented lines in `deploy/manta-trading.env.example`
        (lines 25–29), text otherwise unchanged.
  - [ ] Success: `MT_KALSHI_COLLECTION_EXCLUDED_CATEGORIES=Sports mt data
        kalshi status` behaves as the old variable did;
        `MT_KALSHI_CANDLE_CATEGORIES=Sports mt data kalshi status` exits
        nonzero with a message naming `MT_KALSHI_COLLECTION_CATEGORIES`
        (Criterion 5, walkthrough step 3).

- [ ] **Task 1.5: Rename tests, and prove the rename changed nothing** (effort: 2)
  - [ ] Mechanically update the test files the rename touches:
        `test/unit/test_candle_rule_settings.py` (rename the file to
        `test_collection_rule_settings.py`),
        `test/unit/data/kalshi/test_selection_sql.py`,
        `test/unit/data/kalshi/test_candle_sync.py`,
        `test/unit/data/kalshi/test_collection_pass.py`,
        `test/unit/cli/commands/test_data_kalshi.py` (the
        `(MT_KALSHI_COLLECTION_*)` literal at line 347),
        `test/unit/deploy/test_units.py` (the five env names at lines
        145–149), `test/integration/test_kalshi_sync.py`,
        `test/integration/test_kalshi_pass.py`,
        `test/integration/test_kalshi_candles.py`.
  - [ ] **New test — the guard is loud:** every one of the five
        `MT_KALSHI_CANDLE_*` names, set alone, raises at `Settings`
        construction with a message containing the new name. Parametrize
        over the five so a later sixth setting cannot be forgotten.
  - [ ] **New test — `"any"` drops the traded clause:** as Task 1.3's
        success criterion, alongside the existing `"recent"`/`"ever"` cases.
  - [ ] **New test — `MARKET_JOIN` is composed, not re-spelled:** assert
        `CATALOG_JOIN`'s rendered text is a prefix of `MARKET_JOIN`'s.
  - [ ] Success: `uv run pytest test/unit -q` green; the candle integration
        tests pass unchanged in behavior
        (`uv run python scripts/run_tests.py integration -- -k kalshi_candles -q`).

- [ ] **Task 1.6: Section 1 gates and checkpoint commit** (effort: 1)
  - [ ] `uv run ruff check` and `uv run ruff format --check` scoped to the
        files touched; `uv run --extra dev mypy` and `npx --yes pyright` over
        the kalshi source paths plus the touched tests in one invocation.
  - [ ] Commit: `refactor: rename the candle collection rule to
        MT_KALSHI_COLLECTION_* (slice 265)`.

## Section 2: Constants and migration `kalshi_006_trades`

Design *Constants*, *Migration `kalshi_006_trades`*, *Technical Decision 4*
(PM-ratified 20260828).

- [ ] **Task 2.1: Constants** (effort: 1)
  - [ ] Add to `data/kalshi/constants.py`, each with a comment naming its
        decision or its Discovery Findings evidence: `TRADE_PAGE_LIMIT`,
        `TRADE_WINDOW`, `TRADE_LATE_ARRIVAL_GUARD`,
        `TRADE_REQUESTS_PER_PASS`, `TRADE_LAG_STALE_AFTER`,
        `KALSHI_TRADE_CHUNK_INTERVAL`, `KALSHI_TRADE_COMPRESS_AFTER`. Values
        are in the design's *Constants* block.
  - [ ] Note on `TRADE_PAGE_LIMIT` that 1,001 is a verified HTTP 400, and on
        `TRADE_WINDOW` that one window is ~300–550 pages at the measured
        volume and is the unit a phase abort loses (Decision 1).
  - [ ] `WINDOW_OVERLAP` (262) is **reused**, not redefined — confirm it
        exists in `constants.py` and reference it from the design's Decision
        1 comment.
  - [ ] `PassPhaseName.TRADES = "trades"` is added in Section 4 (Task 4.4),
        not here; `Surface.TRADES` already exists — do not add it again.
  - [ ] Success: `test/unit/data/kalshi/test_constants.py` extended with the
        seven new values, following its existing assertion style.

- [ ] **Task 2.2: Migration `kalshi_006_trades`** (effort: 3)
  - [ ] Append the migration dict to
        `src/manta_trading/market/schema/migrations/kalshi.py` following
        `kalshi_005_candlesticks`'s shape exactly: `id`, `description`, a
        comment block citing the slice and decisions, and an f-string `sql`.
  - [ ] Table, hypertable, compression, policy, and the `sync_state` column
        and comments are in the design's *Migration* block. Both intervals
        render through the existing `_interval_sql()` from the Task 2.1
        constants — **no literal `INTERVAL '7 days'`** in the SQL.
  - [ ] `is_block_trade` is `NOT NULL` in the table while 261's `Trade` model
        types it `bool | None`. Resolve **in the repository**, not by
        loosening the column: `write_page` coalesces a missing value to
        `FALSE` and a comment records that the recorded fixture and the
        352,000-trade sample carry it on every row.
  - [ ] The three `COMMENT ON COLUMN kalshi.sync_state.*` statements replace
        the whole comment string, so carry the catalog and candlesticks
        clauses of `kalshi_004`/`kalshi_005` forward verbatim and change only
        the trades clause (the `kalshi_005` block is the model).
  - [ ] Additive and idempotent (`IF NOT EXISTS`, `if_not_exists => TRUE`);
        no down-migration.
  - [ ] Success: the migration appears in `TRACKS["kalshi"]` by construction
        (it is a list entry) and the ledger preflight covers it for free.

- [ ] **Task 2.3: Migration integration tests** (effort: 3)
  - [ ] Extend `test/integration/test_kalshi_migrations.py`, following the
        `kalshi_005` cases:
  - [ ] `kalshi_006_trades` applies to a fresh database and **re-applies**
        with no error.
  - [ ] `kalshi.trades` is a hypertable; its `chunk_time_interval` equals
        `KALSHI_TRADE_CHUNK_INTERVAL`; `compress_segmentby` is
        `market_ticker` and `compress_orderby` is `created_time DESC`.
  - [ ] A compression policy exists on it whose `compress_after` equals
        `KALSHI_TRADE_COMPRESS_AFTER`, **resolved by hypertable name**, never
        by a recorded job id (job ids regenerate).
  - [ ] The primary key is `(market_ticker, created_time, trade_id)` and
        `trade_id` is type `uuid`; the foreign key to `kalshi.markets` exists.
  - [ ] `kalshi.sync_state.coverage_from_ts` exists and is NULL for existing
        rows; the `watermark_ts` comment still contains the catalog and
        candlesticks clauses as well as the new trades clause.
  - [ ] **Compression proves out on a real chunk (Criterion 12):** insert
        rows dated older than the horizon, run the policy job by the id
        resolved from the view (two statements — a subquery is not a valid
        `CALL` argument), and assert the chunk is compressed and the rows
        read back identical.
  - [ ] Success: `uv run python scripts/run_tests.py integration -- -k
        kalshi_migrations -q` green.

- [ ] **Task 2.4: Section 2 gates and checkpoint commit** (effort: 1)
  - [ ] Gates as Task 1.6, scoped to the files touched.
  - [ ] Commit: `feat: add kalshi_006_trades migration and trade constants`.

## Section 3: `TradeRepository` — classify and write, in SQL

Design *Repository (`trade_repository.py`)*, *Technical Decision 5*,
*Technical Decision 11*.

- [ ] **Task 3.1: `TRADE_COLUMNS` and the state readers** (effort: 2)
  - [ ] New `data/kalshi/trade_repository.py`. `TRADE_COLUMNS` is the
        model→column map (261's `Trade.ticker` → `market_ticker`;
        `taker_side` is **not** stored, Decision 11), following
        `candle_repository.CANDLE_COLUMNS`'s shape so the parity test can be
        written the same way.
  - [ ] `read_state() -> TradeState | None` reading `watermark_ts` and
        `coverage_from_ts` from `sync_state` where `surface = Surface.TRADES`.
  - [ ] `init_state(cutoff)` inserts the row with **both** `watermark_ts` and
        `coverage_from_ts` set to the cutoff — first run only (Decision 2).
  - [ ] `advance_watermark(end)` and `set_last_full_sync(phase_start)` reuse
        `CatalogRepository`'s `sync_state` statements rather than re-spelling
        them; `transaction()` is the same context manager.
  - [ ] Success: the module has no httpx, no typer import; `TradeState` is a
        frozen dataclass.

- [ ] **Task 3.2: `write_page` — one statement, one transaction** (effort: 4)
  - [ ] Implement `write_page(rows) -> PageCounts` as the single
        data-modifying CTE in the design's *Repository* block: `unnest` of
        the page's column arrays → `LEFT JOIN` the catalog three-table join →
        `selected = COALESCE(<rule "any" predicate>, FALSE)` → insert the
        selected rows `ON CONFLICT DO NOTHING` → return
        `unknown`, `excluded`, `selected`, `written` in one round trip.
  - [ ] The predicate comes from `selection_sql(rule, "any").predicate` —
        the **only** rendering of the rule on this path. Its params merge
        into the statement's parameter mapping.
  - [ ] Arrays are bound parameters, one array per column (nine arrays, not
        9,000 placeholders) — this is what keeps a 1,000-row page under the
        bind-parameter ceiling. Say so in a comment.
  - [ ] `PageCounts` is a frozen dataclass carrying `fetched`, `written`,
        `unknown_market`, `excluded_by_rule`; `duplicates` is derived as
        `selected − written` and the identity
        `fetched = written + unknown + excluded + duplicates` is asserted in
        the dataclass's `__post_init__` or a module function — Criterion 2
        is an exact accounting, so make it structural.
  - [ ] Error taxonomy (design *Repository*): `psycopg.OperationalError`
        propagates as a storage abort; **any other** `psycopg.Error`
        propagates as a bug. No `try`/`except` swallows anything here.
  - [ ] Success: a page of 1,000 rows issues exactly one statement (assert
        via a counting cursor or the connection's query log in the
        integration test).

- [ ] **Task 3.3: `write_page` integration tests — the predicate fixture set** (effort: 4)
  - [ ] New `test/integration/test_kalshi_trades.py` using the `kalshi_db`
        fixture and `kalshi_helpers.py`, modelled on
        `test_kalshi_candles.py`'s `with_rule` helper.
  - [ ] Seed fixture markets with synthesized series covering the cases, then
        assert each (design *Tests — Integration*):
    1. A **Sports** trade is excluded and counted in `excluded_by_rule`.
    2. A `KXMVE…`-style ticker with **no market row** is counted in
       `unknown_market`, is not stored, and is not an error.
    3. A **Politics** trade is written.
    4. A **second write of the same page** writes 0 and reports the rows as
       `duplicates` (Criterion 3).
    5. Under `MT_KALSHI_COLLECTION_CATEGORIES=Sports` with the exclusions
       cleared, the Sports trade is the one written (Criterion 5).
    6. A page carrying a **non-UUID** `trade_id` fails the write loudly (a
       `psycopg.DataError` propagates; it is not swallowed and not counted).
  - [ ] For every case assert the full identity
        `fetched = written + unknown + excluded + duplicates`.
  - [ ] `TRADE_COLUMNS` parity against the real table (every mapped column
        exists, every non-defaulted column is mapped), following the
        `CANDLE_COLUMNS` parity test.
  - [ ] `advance_watermark` moves `watermark_ts` and leaves
        `coverage_from_ts` untouched; `init_state` sets both and is a no-op
        on a second call.
  - [ ] Success: `uv run python scripts/run_tests.py integration -- -k
        kalshi_trades -q` green.

- [ ] **Task 3.4: Section 3 gates and checkpoint commit** (effort: 1)
  - [ ] Gates as Task 1.6, scoped to the files touched.
  - [ ] Commit: `feat: add TradeRepository with per-page classify-and-write`.

## Section 4: `TradeSync`, `TradesPhase`, fixtures, rendering

Design *Core (`trade_sync.py`) and types (`trade_types.py`)*, *Data Flow*,
*`collection_pass.py`*, *`TradeResult.to_dict()`*, *Fixtures and recorder*,
*Technical Decisions 1, 2, 6, 7, 8, 9*.

- [ ] **Task 4.1: `trade_types.py`** (effort: 2)
  - [ ] `TradeSource` Protocol (`get_trades`, `get_historical_cutoff`) — the
        core depends on this, never on `KalshiClient`.
  - [ ] `TradeResult` dataclass with the fields in the design's *Core* block,
        and `to_dict()` producing exactly the design's *`TradeResult.
        to_dict()`* payload shape.
  - [ ] `TradesBehindCutoffError` (Decision 6) — its message names the
        uncovered range and slice 266 as the remedy.
  - [ ] `classify_trades(result, exc) -> SyncOutcome` delegating to
        `classify_outcome(False, exc)`: this phase has no per-item failure
        and therefore **never** reports `PARTIAL` (Decision 9). Say so in the
        docstring.
  - [ ] Success: unit test asserts `classify_trades` returns `OK`,
        `PROVIDER_ABORT`, `STORAGE_ABORT` and never `PARTIAL` for any input.

- [ ] **Task 4.2: `TradeSync` core** (effort: 5)
  - [ ] New `data/kalshi/trade_sync.py` implementing Data Flow steps 1–6.
        No httpx, no typer, no SQL.
  - [ ] Step 1 — cutoff and state: `get_historical_cutoff().trades_created_ts`
        once, logged at INFO every run; on no state row, `init_state(cutoff)`
        with `coverage_from_ts = watermark_ts = cutoff` (Decision 2); on
        `watermark_ts < cutoff`, raise `TradesBehindCutoffError`
        (Decision 6).
  - [ ] Step 2 — window end: `sync_state['catalog'].last_full_sync_at −
        TRADE_LATE_ARRIVAL_GUARD` (Decision 5). **No catalog row → the phase
        fetches nothing and says so** in its result and its log line.
  - [ ] Step 3 — windows oldest-first in `TRADE_WINDOW` steps from
        `watermark_ts`, the last clamped to `end`. The cap check
        (`requests >= TRADE_REQUESTS_PER_PASS`) is **before each window**, so
        a pass may exceed the cap by at most one window; on stopping, set
        `capped = True` (Decision 8).
  - [ ] Step 4 — one window: page through
        `get_trades(min_ts=start − WINDOW_OVERLAP, max_ts=end,
        limit=TRADE_PAGE_LIMIT, cursor)` until the cursor is empty; each page
        is one `write_page` call in its own transaction; accumulate the
        counts. The watermark does not move inside a window.
  - [ ] Step 5 — window done: `advance_watermark(end)` in one transaction,
        then one INFO line per window in the design's format
        (`trades window {start}→{end} pages N fetched F written W unknown U
        excluded X`).
  - [ ] Step 6 — finish: `set_last_full_sync(phase_start)`; emit
        `phase_finished` with `phase="trades"` through the existing sink and
        `emit_in_thread` — **no new event type**.
  - [ ] The unknown-prefix tally: count by the ticker text before the first
        `-`, kept in memory, emitted as **one INFO line per phase**. A
        comment states this is display only and nothing branches on it
        (CLAUDE.md).
  - [ ] Success: the module is under ~300 lines and imports no client.

- [ ] **Task 4.3: `TradeSync` unit tests with fakes** (effort: 5)
  - [ ] Add `test/kalshi_support/fake_trade_source.py` (scripted pages,
        recorded `min_ts`/`max_ts`/`cursor` per call) and
        `fake_trade_repository.py` (in-memory state and page counts),
        modelled on the candle fakes.
  - [ ] New `test/unit/data/kalshi/test_trade_sync.py` covering the design's
        *Tests — Unit — core* list, one test per behavior:
    1. First run with no state initialises `coverage_from_ts` and
       `watermark_ts` at the cutoff (Criterion 6).
    2. The window sequence starts at the watermark, steps by `TRADE_WINDOW`,
       and the last window is clamped to the catalog walk start minus the
       guard (Criterion 7).
    3. No catalog row → nothing fetched, and the result says so.
    4. Per-page counts aggregate into the result, and the identity
       `fetched = written + unknown + excluded + duplicates` holds
       (Criterion 2).
    5. The watermark advances only after a window's **last** page.
    6. A `ProviderError` mid-window leaves the watermark where it was
       (Criterion 4) and the result classifies as `PROVIDER_ABORT`.
    7. The cap stops **before** a window, sets `capped`, and the next run
       continues from the watermark (Criterion 8).
    8. `watermark_ts < cutoff` raises `TradesBehindCutoffError` naming the
       range (Criterion 6).
    9. `phase_finished` is emitted once with `phase="trades"`.
    10. The unknown-prefix tally groups by prefix and logs once per phase
        (Criterion 9).
    11. The lower bound of each request is `start − WINDOW_OVERLAP`
        (Decision 1's boundary handling).
  - [ ] Success: `uv run pytest test/unit/data/kalshi/test_trade_sync.py -q`
        green.

- [ ] **Task 4.4: `TradesPhase` and `PASS_PHASES`** (effort: 2)
  - [ ] Add `TRADES = "trades"` to `PassPhaseName` and update its docstring
        (it currently reads "265 adds `TRADES`").
  - [ ] Add `TradesPhase` to `collection_pass.py` with the same
        `try`/`except ProviderError` / `except psycopg.OperationalError` /
        `classify` shape as `CandlesPhase`, taking the rule from
        `run.settings.collection_rule()`.
  - [ ] Append it: `PASS_PHASES = (CatalogPhase(), CandlesPhase(),
        TradesPhase())`, and update the registration comment.
  - [ ] `TradesBehindCutoffError` is **not** caught here — it propagates out
        of the pass (Decision 6), and the earlier phases' reports stand.
  - [ ] Extend `test/unit/data/kalshi/test_collection_pass.py`:
        `PASS_PHASES` is exactly the three phases in order (Criterion 1); a
        catalog or candle abort reports trades `skipped`; a trades abort
        leaves the earlier phases' outcomes intact;
        `TradesBehindCutoffError` propagates out of `CollectionPass.run`.
  - [ ] Success: `uv run pytest test/unit -q` green.

- [ ] **Task 4.5: Fixtures and the recorder** (effort: 2)
  - [ ] Add three recorders to `scripts/record_kalshi_fixtures.py` alongside
        `record_trades`: `trades_window` (a windowed page, `min_ts`/`max_ts`
        one minute apart, `limit=100`, with a non-empty cursor),
        `trades_window_last` (the same window's final page, `cursor: ""`),
        `trades_empty` (a future window, no trades, `cursor: ""`). Register
        them in the recorder map. Existing `trades_page1`/`trades_page2` stay
        for the 261 client test.
  - [ ] Record the three fixtures against the live public endpoint and commit
        the JSON under `test/fixtures/kalshi/`.
  - [ ] Extend `test/unit/data/kalshi/test_fixtures.py`: all three parse into
        `TradesPage`; an **empty cursor terminates** the walk (this is the
        fact the core's page loop depends on — assert it explicitly, not by
        implication).
  - [ ] Success: `uv run pytest test/unit/data/kalshi/test_fixtures.py -q`
        green; the fixtures' `min_ts`/`max_ts` bounds are visible in the
        recorded file or its recorder so a future reader can re-record.

- [ ] **Task 4.6: Phase renderer** (effort: 2)
  - [ ] Add `print_trade_summary(summary)` to
        `cli/commands/kalshi_render.py` and register it in
        `PHASE_RENDERERS[PassPhaseName.TRADES]`. It prints windows, requests
        (with `capped` when set), watermark before → after, and
        fetched / written / unknown / excluded / duplicates.
  - [ ] Extend `test/unit/cli/commands/test_data_kalshi.py`: the renderer
        dispatches on the trades phase name; the `TradeResult.to_dict()`
        payload round-trips through `json.dumps`/`loads` unchanged.
  - [ ] Success: `mt data kalshi pass --json | jq '.phases[2].name'` would
        print `"trades"` (asserted against a fabricated `PassResult` in the
        unit test — no network).

- [ ] **Task 4.7: Section 4 gates and checkpoint commit** (effort: 1)
  - [ ] Gates as Task 1.6, scoped to the files touched.
  - [ ] Commit: `feat: add the trades phase to the Kalshi collection pass`.

**Continue in `user/tasks/265-tasks.public-trades-collection-2.md`** — Section
5 (`status` block), Section 6 (end-to-end integration), Section 7 (rehearsal
on the test cluster), Section 8 (documentation), Section 9 (production
deploy, **[PM]**).
