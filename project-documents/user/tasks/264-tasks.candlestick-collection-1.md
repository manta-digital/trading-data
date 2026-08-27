---
docType: tasks
slice: candlestick-collection
project: trading-data
lld: user/slices/264-slice.candlestick-collection.md
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [261, 262, 263]
interfaces: [265, 266]
projectState: >
  Slice 263 is complete and cut over on manta9000 at v0.9.0:
  mt-kalshi-pass.timer fires hourly at :20 UTC, mt data kalshi pass runs
  CollectionPass over PASS_PHASES = (CatalogPhase(),), and catalog plus
  settlement data accumulate unattended. The kalshi migration track is
  applied to production through kalshi_004. data/kalshi/ holds
  run_context.py (KalshiRun, open_kalshi_run), collection_pass.py
  (PassPhaseName, PhaseReport, PassPhase, PassResult, classify_pass,
  CollectionPass, CatalogPhase, PASS_PHASES), sync.py, repository.py,
  status.py, client.py, models.py, constants.py, db.py, events.py. No
  candle table, no candle phase, no collection rule exists yet.
  kalshi.market_candle_state exists (kalshi_003) with watermark_ts carrying
  a comment this slice corrects. Design 264 reviewed CONCERNS (passes the
  gate); Decisions 2, 4, 5 PM-ratified 20260826.
reviewVerdictsAddressed:
  - 264-review.tasks.candlestick-collection.part-1 (claude-opus-5, CONCERNS, F001-F003/F006 addressed here; F004/F005/F007 in part 2)
  - 264-review.tasks.candlestick-collection.part-2 (claude-opus-5, CONCERNS, dispositioned in part 2)
dateCreated: 20260826
dateUpdated: 20260826
status: in_progress
---

## Context Summary

- Working on **264 Candlestick Collection** — the second phase of the Kalshi
  collection pass. It appends `CandlesPhase` to `PASS_PHASES`, collects
  1-minute candles for the markets a **configurable collection rule**
  selects, writes them into a new `kalshi.candlesticks` hypertable under a
  per-market watermark, and gives `mt data kalshi status` a candle block
  that makes both coverage and the rule's exclusions visible.
- Source of truth: the slice design at
  `user/slices/264-slice.candlestick-collection.md`. Its **Discovery
  Findings**, **Technical Decisions 1–11**, **Implementation Details**,
  **Tests**, **Success Criteria 1–14**, and **Verification Walkthrough** are
  referenced by number below rather than restated. Read the design before
  starting any section — in particular Discovery Findings, because the
  endpoint caps and the sparseness of candles are what the planner and the
  rule exist for.
- **No unit, timer, installer, `mt-run`, or `mt data kalshi pass` surface
  change.** The timer that already runs picks the phase up. The only deploy
  artifacts touched are `deploy/manta-trading.env.example` (five commented
  lines) and runbook 100's Kalshi subsection.
- Code to reuse, not reinvent: `collection_pass.py::CatalogPhase` (the phase
  shape, its try/except and `classify`), `repository.py::CatalogRepository`
  (`transaction()`, the chunked `_upsert` under the bind-parameter ceiling,
  `psycopg.sql` composition), `sync.py::CatalogSync` (core structure, event
  emission, progress logging), `status.py::read_catalog_status`,
  `db.py::open_sync_connection`, `test/kalshi_support/`
  (`fake_source.py`, `fake_repository.py`, `sync_harness.py`, `samples.py`),
  `test/integration/kalshi_helpers.py` and the `kalshi_db` fixture in
  `test/integration/conftest.py`, `scripts/record_kalshi_fixtures.py`.
- Hard rules for this slice:
  - **Every comparison value is a named constant** (CLAUDE.md). The caps,
    the lookback, the per-pass cap, the chunk interval, the compression
    horizon, the period — all in `constants.py`, each cited to its decision.
  - **The rule is rendered in exactly one place** —
    `CandleRepository.selection_sql(rule, form)`. No pending query, no
    status query, and no test may re-spell the predicate.
  - **CHECK constraints are rendered from enums, never hand-listed.**
    `migrations/kalshi.py` already has `_period_check_sql()` for exactly
    this; `kalshi_005` reuses it. The design's SQL sketch shows a literal
    `period IN (1, 60, 1440)` — that is illustrative only, do not copy it.
  - `status.py` imports neither the client nor the transport (Criterion 12).
  - No new catch-all: the phase catches exactly `ProviderError` and
    `psycopg.OperationalError`, as `CatalogPhase` does.
  - Nothing references `public` (261 extraction discipline).
  - Exit codes are 262's `EXIT_BY_OUTCOME` verbatim; no new exit constants.
- Tests: unit tier `uv run pytest test/unit -q`; the integration tier only
  through `uv run python scripts/run_tests.py integration -- -k kalshi -q`
  (never with the production URL). Gates as 263: `uv run ruff check` and
  `uv run ruff format --check` **scoped to the files touched**, `uv run
  --extra dev mypy` and `npx --yes pyright` on the kalshi source paths plus
  the new tests.
- Branch per CLAUDE.md git rules: `264-slice.candlestick-collection`, forked
  from `main` (`git.integration_branch` is unset — verified 20260826).
  Commit checkpoints are marked at the end of each section with a semantic
  message. Merge and release tagging follow runbook 100's update procedure
  after PM approval and are not tasks here.
- Host boundary as 263: tasks marked **[PM]** are executed by the Project
  Manager on manta9000 (no passwordless sudo there); tasks marked
  **[agent]** need no elevation. No task waits on a wall-clock event —
  the timer's behavior is proven by starting the unit the timer activates.
- **This file is part 1 of 2.** Sections 1–4 below build the vocabulary, the
  schema, the planner, and the repository. The core, the phase, the `status`
  block, the rehearsal, the documentation, and the host steps are in
  `user/tasks/264-tasks.candlestick-collection-2.md`, which starts at Section
  5 and depends on everything here.
- Next slice: 265 (trades) copies this phase's shape; 266 (historical
  backfill) consumes `behind_cutoff_uncollected` and must pause this
  hypertable's compression policy during its drain.

## Section 1: Constants, the collection rule, client and fixtures

Design *Constants*, *Settings — the collection rule* (Decision 2), *Client
and models*, *Fixtures and recorder*. This section adds no behavior — it
adds the vocabulary every later section depends on.

- [x] **Task 1.1: Constants** (effort: 1)
  - [x] Add to `data/kalshi/constants.py`, each with a comment naming its
        decision or its Discovery Findings evidence:
        `MARKETS_CANDLESTICKS_PATH`, `COLLECTED_CANDLE_PERIOD`
        (`CandlePeriod.MINUTE`, Decision 1), `CANDLE_BATCH_MAX_TICKERS`,
        `CANDLE_BATCH_MAX_CANDLES`, `CANDLE_SINGLE_MAX_CANDLES`,
        `CANDLE_FIRST_SIGHT_LOOKBACK`, `CANDLE_BACKLOG_REQUESTS_PER_PASS`,
        `CANDLE_PROGRESS_EVERY_REQUESTS`, `CANDLE_LAG_STALE_AFTER`,
        `KALSHI_CANDLE_CHUNK_INTERVAL`, `KALSHI_CANDLE_COMPRESS_AFTER`.
        Values are in the design's *Constants* block.
  - [x] Note in the comment on `CANDLE_BATCH_MAX_CANDLES` that the cap is on
        `len(tickers) × periods_in_window` of the **request**, not the
        response — this is the fact the planner is built around.
  - [x] `CANDLE_SINGLE_MAX_CANDLES` is recorded but unused by the phase (the
        batch path is the only one used); say so in its comment so a reader
        does not hunt for its call site.
  - [x] Success: `test/unit/data/kalshi/test_constants.py` extended — every
        new constant has the design's value and the two `timedelta`
        constants are `timedelta` instances; existing assertions unchanged.

- [x] **Task 1.2: `CandleRule` and the `Settings` fields** (effort: 3)
  - [x] New `data/kalshi/candle_types.py` with the frozen dataclass
        `CandleRule(traded_only, categories, excluded_categories,
        excluded_series_pattern, excluded_title_pattern)` and
        `CandleRule.describe() -> str` rendering the one-line human form the
        design shows (`candles rule: traded 24h · categories all · excluding
        Sports, Mentions · patterns 2`). `describe()` must be deterministic:
        sort the category sets.
  - [x] Five fields on `Settings` (`config/__init__.py`) with the design's
        names, types, and defaults — rule C is the default. Follow the
        file's existing comment style: say *why* the values are data rather
        than an enum (the category vocabulary is Kalshi's, not ours).
  - [x] One `field_validator(mode="before")` splitting the comma-separated
        forms for the two category sets: split on `,`, strip whitespace,
        drop empties, build a `frozenset`. Empty string → empty frozenset.
        Reason in a comment: pydantic-settings' default JSON-list parsing
        for a set-typed field is not what a `.env` author writes.
  - [x] The two pattern fields treat empty string as `None` (clause
        disabled) — one validator, or `str | None` with a normalizer.
  - [x] `Settings.candle_rule() -> CandleRule` — the single parse point.
        Docstring states the evaluation order (allow-list if non-empty →
        exclude-list → patterns → traded) and that exclude wins when a
        category appears in both.
  - [x] Success: `Settings()` with no environment yields exactly rule C.

- [x] **Task 1.3: Settings and rule unit tests** (effort: 2)
  - [x] New `test/unit/test_candle_rule_settings.py` (or extend the existing
        settings test module if one covers `Settings`): defaults equal rule
        C; `MT_KALSHI_CANDLE_CATEGORIES=Sports, Politics` parses to a
        two-member frozenset with whitespace trimmed; empty value → empty
        frozenset; `MT_KALSHI_CANDLE_TRADED_ONLY=false` parses as a bool;
        an empty pattern disables the clause (`None`, not `""`);
        `candle_rule()` returns the expected `CandleRule`; `describe()` is
        stable across two calls and reflects a changed rule.
  - [x] Use monkeypatched environment variables, not a written `.env`.
  - [x] Success: tests pass; no test re-spells the rule's SQL.

- [x] **Task 1.4: Batch client method and models** (effort: 2)
  - [x] `models.py`: `MarketCandlesticks(market_ticker: str, candlesticks:
        list[Candlestick])` and `BatchCandlesticksResponse(markets:
        list[MarketCandlesticks])`, following the existing model style.
  - [x] `client.py`: `get_markets_candlesticks(tickers, *, start_ts, end_ts,
        period_interval) -> list[MarketCandlesticks]` against
        `MARKETS_CANDLESTICKS_PATH`, in the `# ---` banner style the
        candlestick section already uses. **The transport's `ParamValue` is
        `str | int | bool | None` — it has no list member, and
        `_clean_params` would render a list as `"['A', 'B']"`.** Join the
        tickers into a comma-separated string in the client, as
        `MarketsQuery.tickers` and the recorder already do; do not widen the
        transport for this.
  - [x] Pass `int(period_interval)` for the period, matching
        `get_market_candlesticks`.
  - [x] The client passes the request through and enforces no cap — the
        planner owns the cap (Decision 7); say so in the docstring.
  - [x] Success: the method issues exactly one request; unknown tickers are
        simply absent from the returned list (no error raised at this
        layer).

- [x] **Task 1.5: Fixtures and the recorder** (effort: 2)
  - [x] `scripts/record_kalshi_fixtures.py` gains two `--only` targets
        following the existing target declaration pattern:
        `candlesticks_batch` — a real batch response over at least three
        selected tickers for the last hour at `period_interval=1`, chosen so
        that **at least one entry is empty** and at least one candle has
        `price: {}`; and `candlesticks_batch_over_cap` — the HTTP 400 body
        provoked by 100 tickers × 360 minutes, saved as
        `error_400_candles_cap.json`.
  - [x] Record both against the live public API and commit the JSON under
        `test/fixtures/kalshi/`. The existing `candlesticks.json` stays
        (261's single-market client test still uses it).
  - [x] Success: both fixture files exist and are valid JSON; the empty
        entry and the `price: {}` candle are actually present — check, do
        not assume, since the recorder's window choice decides it.

- [x] **Task 1.6: Client, model, and fixture unit tests** (effort: 2)
  - [x] Extend `test/unit/data/kalshi/test_client_endpoints.py`: the request
        `get_markets_candlesticks` issues carries the expected path and
        query parameters (`market_tickers`, `start_ts`, `end_ts`,
        `period_interval`), and the batch fixture parses into
        `MarketCandlesticks` objects **including the empty-list entry** and
        the `price: {}` candle.
  - [x] Extend `test/unit/data/kalshi/test_fixtures.py`: the over-cap
        fixture raises `ProviderPermanentError` through the transport's
        error mapping (a 400 is permanent, not retried).
  - [x] Extend `test/unit/data/kalshi/test_models.py` for the two new models.
  - [x] Success: all pass; no network access in the unit tier.
  - [x] **Commit**: `feat: add kalshi batch candlestick client, rule config, and fixtures`.

## Section 2: Migration `kalshi_005` and the ledger preflight

Design *Migration `kalshi_005_candlesticks`* (Decision 4), *Preflight*
(Decision 8). The design's SQL block is a sketch; the rules below govern
where it and the existing code disagree.

- [x] **Task 2.1: `kalshi_005_candlesticks`** (effort: 3)
  - [x] Append one entry to `KALSHI_MIGRATIONS` in
        `market/schema/migrations/kalshi.py` with `id`,
        `description`, and `sql`, matching the shape of the four entries
        already there. Additive and idempotent (`IF NOT EXISTS`); no
        down-migration.
  - [x] `CREATE TABLE kalshi.candlesticks` with the design's columns:
        `market_ticker` (FK to `kalshi.markets (ticker)`), `period`,
        `end_period_ts`, the sixteen nullable NUMERIC OHLC columns,
        `volume_fp NUMERIC NOT NULL`, `open_interest_fp NUMERIC`, primary
        key `(market_ticker, period, end_period_ts)`.
  - [x] The period CHECK constraint is rendered by the existing
        **`_period_check_sql()`** helper, not hand-listed — the module
        docstring makes this a standing rule and `market_candle_state`
        already follows it. (The design's illustrative SQL shows a literal
        list; do not copy it.)
  - [x] `create_hypertable('kalshi.candlesticks', 'end_period_ts',
        chunk_time_interval => …, if_not_exists => TRUE)` with the interval
        rendered from `KALSHI_CANDLE_CHUNK_INTERVAL` — the constant is the
        single definition, so import it rather than writing `7 days`.
  - [x] `ALTER TABLE … SET (timescaledb.compress, compress_segmentby =
        'market_ticker', compress_orderby = 'end_period_ts DESC')` then
        `add_compression_policy(… compress_after => …, if_not_exists =>
        TRUE)` with the horizon rendered from `KALSHI_CANDLE_COMPRESS_AFTER`.
  - [x] `ALTER TABLE kalshi.market_candle_state ADD COLUMN IF NOT EXISTS
        coverage_from_ts TIMESTAMPTZ` and its comment (Decision 5).
  - [x] Rewrite the `market_candle_state.watermark_ts` comment to Decision
        3's semantics (the window end fetched through, **not** the newest
        stored candle — kalshi_003's text is wrong for sparse data).
  - [x] Rewrite the `kalshi.sync_state.watermark_ts` comment. Note that
        `kalshi_004` already rewrote this comment for the catalog and trades
        surfaces; `COMMENT ON` replaces the whole string, so the new text
        must **carry the catalog and trades clauses forward** and change
        only the candlesticks clause (Decision 11: the historical cutoff
        observed by the last candle phase). Do not shorten the others away.
  - [x] `GRANT SELECT, INSERT, UPDATE, DELETE ON kalshi.candlesticks TO`
        the `APP_ROLE` constant.
  - [x] Success: `mt data migrate apply --track kalshi` applies it to a
        throwaway database and re-applying is a no-op.

- [x] **Task 2.2: Ledger preflight** (effort: 2)
  - [x] In `data/kalshi/db.py`, replace the `to_regclass('kalshi.sync_state')`
        probe in `open_sync_connection` with a check that every migration id
        in `TRACKS["kalshi"]` is present in `schema_migrations`
        (one query, ids bound as a parameter).
  - [x] Missing ids → `PreflightError` naming them and the remedy, in the
        design's wording (`kalshi track has pending migrations:
        kalshi_005_candlesticks — mt data migrate apply --track kalshi`).
        An **absent `schema_migrations` table** must produce the same error,
        not an unhandled `psycopg` error — the bare-database case.
  - [x] Update the `TRACK_NOT_APPLIED` wording to match. The advisory-lock
        step is unchanged and still runs after this check.
  - [x] Success: the check names *all* missing ids, not just the first.

- [x] **Task 2.3: Migration and preflight integration tests** (effort: 3)
  - [x] Extend `test/integration/test_kalshi_migrations.py`: `kalshi_005`
        applies and re-applies cleanly; `kalshi.candlesticks` appears in
        `timescaledb_information.hypertables` with the configured chunk
        interval; its compression settings are the design's segmentby and
        orderby; a compression policy exists on it whose `compress_after`
        equals `KALSHI_CANDLE_COMPRESS_AFTER`, read back from
        `timescaledb_information.jobs` **by hypertable name and
        `proc_name`, never by job ID** (job ids regenerate).
  - [x] `market_candle_state.coverage_from_ts` exists; the two rewritten
        comments contain their new semantics; the `sync_state.watermark_ts`
        comment still carries its catalog and trades clauses (guards against
        Task 2.1 dropping them).
  - [x] Preflight: with `kalshi_005` deleted from `schema_migrations`,
        `open_sync_connection` raises `PreflightError` naming
        `kalshi_005_candlesticks`; restoring it lets the connection open.
  - [x] Success: `uv run python scripts/run_tests.py integration -- -k
        kalshi -q` passes. Never point the tier at the production URL.
  - [x] **Commit**: `feat: add kalshi_005 candlestick hypertable and ledger preflight`.

## Section 3: The batch planner (pure)

Design *Planner* (Decision 3, and the caps from Discovery Findings). No I/O,
no SQL, no clock — every input is an argument.

- [ ] **Task 3.1: `candle_plan.py` types and window arithmetic** (effort: 3)
  - [ ] New `data/kalshi/candle_plan.py` with frozen `CandleTarget(ticker,
        start, end, close_end)` and `CandleBatch(tickers, start, end)`.
  - [ ] `last_complete_period(now, period) -> datetime` — floor to the
        period then subtract one period (Decision 3's one-period guard for a
        still-settling candle in a conflict-ignore table).
  - [ ] `target_window(market, state, *, phase_start, period, lookback) ->
        CandleTarget | None`: `start = watermark` when a state row exists,
        else `max(open_time, min(close_time, phase_start) − lookback)`
        (Decision 5); `end = min(close_time + period,
        last_complete_period(phase_start, period))`; return `None` when
        `start >= end` (nothing to fetch).
  - [ ] Success: pure functions; the module imports nothing from the client,
        the repository, or `psycopg`.

- [ ] **Task 3.2: `plan_batches` packing** (effort: 3)
  - [ ] `plan_batches(targets, *, period, max_tickers, max_candles) ->
        list[CandleBatch]`: sort targets by `start`; **first split any single
        target whose own window exceeds `max_candles` periods** into
        consecutive windows; then pack greedily, admitting a target to the
        current batch only while both `len(tickers) + 1 <= max_tickers` and
        `(len(tickers) + 1) × periods(union_window) <= max_candles` hold —
        the union window is what the request asks for, which is why adding a
        distant target can overflow the cap even when the ticker count is
        small.
  - [ ] Assert both caps on every batch before returning it. A violation is
        a bug here, not a provider condition (Decision 7).
  - [ ] Deterministic: the same targets in any input order produce the same
        batches.
  - [ ] Success: every target's full window is covered by the returned
        batches — packing may widen a request, never drop a period.

- [ ] **Task 3.3: Planner unit tests** (effort: 3)
  - [ ] New `test/unit/data/kalshi/test_candle_plan.py`:
        `last_complete_period` at an exact boundary and mid-period;
        `target_window` for each case the design lists — market first seen
        young (start is `open_time`), first seen old (start is
        `phase_start − lookback`), past close (end clamped to `close_time +
        period`), already complete (returns `None`), and with an existing
        watermark (start is the watermark).
  - [ ] Packing: never exceeds either cap; an over-long single target splits
        into consecutive windows that tile its range; targets with distant
        starts do not share a batch when the union would breach the candle
        cap; output is deterministic.
  - [ ] A randomized invariant test over generated target sets: for every
        batch both caps hold, and every target's `[start, end)` is fully
        covered by the batches containing its ticker (Criterion 5). Seed the
        generator so failures reproduce.
  - [ ] Success: tests pass; the cap constants come from `constants.py`, not
        literals in the test.
  - [ ] **Commit**: `feat: add kalshi candle batch planner`.

## Section 4: `CandleRepository` — the rule, the pending queries, the writes

Design *Repository* and *Data Flow* step 2. `CatalogRepository` is the model
to follow: it takes an open connection, never opens one, holds no exception
handling, and binds every status value as a parameter rather than
interpolating it.

- [ ] **Task 4.1: `selection_sql` — the one place the rule is rendered** (effort: 3)
  - [ ] New `data/kalshi/candle_repository.py` with
        `selection_sql(rule: CandleRule, form: Literal["recent", "ever"]) ->
        sql.Composed`, composing the Decision 2 predicate over the aliases
        `m` (markets) and `s` (series).
  - [ ] Clause by clause, each **omitted entirely when its setting is empty**
        so an unset value costs nothing: allow-list when `categories` is
        non-empty; exclude-list when `excluded_categories` is non-empty; the
        ticker and title patterns when set; and the traded clause when
        `traded_only` — `m.volume_24h_fp > 0` for `form="recent"`,
        `m.volume_fp > 0` for `form="ever"`.
  - [ ] **NULL category and NULL title must not silently drop a market.**
        `kalshi.series.category` and `.title` are nullable TEXT (kalshi_002)
        and Kalshi serves series with neither — the slice's own universe
        table counts a 588-market "Companies / Social / World / unknown"
        cohort. The obvious spellings all evaluate to **NULL** on a NULL
        column, and NULL in a `WHERE` is not TRUE, so such a row would be
        excluded with no report of the exclusion. Measured on the test
        cluster 20260826:

        | expression, NULL left operand | result |
        |---|---|
        | `NOT (s.category = ANY(%s))` | NULL → row dropped |
        | `s.ticker !~ %s` | NULL → row dropped |
        | `s.title !~* %s` | NULL → row dropped |
        | `COALESCE(s.category, '') <> ALL(%s)` | **true** → row kept |
        | `COALESCE(s.title, '') !~* %s` | **true** → row kept |

        Use the `COALESCE` forms for the exclusion clauses, so an
        uncategorised or untitled series is **kept**: the rule excludes
        Sports and Mentions by name, and a series that is neither is not one
        of them. (`IS DISTINCT FROM ALL` is not valid PostgreSQL syntax —
        verified; do not reach for it.)
  - [ ] The **allow-list is the deliberate exception**: `s.category =
        ANY(%s)` on a NULL category is NULL, and that is correct — an
        operator naming the categories they want has not named the
        uncategorised ones. Comment the asymmetry so it reads as intent.
  - [ ] Every value is a **bound parameter**, never interpolated — the
        patterns are operator-supplied strings and must not reach the SQL
        text (repository.py's standing rule).
  - [ ] With every setting empty and `traded_only` false, the predicate must
        be a valid always-true expression, not an empty string.
  - [ ] Module docstring states that this function is the only renderer of
        the rule and that the pending queries and `status` both call it.
  - [ ] Success: a unit-level call returns a `Composed` whose parameter list
        matches the clauses present.

- [ ] **Task 4.1b: `selection_sql` clause-omission unit tests** (effort: 2)
  - [ ] New `test/unit/data/kalshi/test_selection_sql.py`. The five settings
        are each independently omittable, which is combinatorial and cheap
        to prove without a database: assert the rendered parameter list per
        configuration — every setting empty (no parameters, always-true
        predicate), each setting alone, and the rule C default.
  - [ ] Assert the `COALESCE` forms are used for the two exclusion clauses
        and that the allow-list clause is **not** wrapped in `COALESCE` —
        the asymmetry Task 4.1 makes deliberate.
  - [ ] Assert on the `Composed` sequence or render with `.as_string(conn)`;
        do not string-match the whole statement, which breaks on whitespace.
  - [ ] Success: semantic row outcomes stay in Task 4.4's integration test;
        this task proves clause structure only.

- [ ] **Task 4.2: Pending queries** (effort: 3)
  - [ ] `pending_live(period, phase_start)`, `pending_finishing(period)`,
        `pending_backlog(period, cutoff, limit)` on `CandleRepository`, each
        joining `kalshi.markets m JOIN kalshi.events e ON … JOIN
        kalshi.series s ON … LEFT JOIN kalshi.market_candle_state st ON …`
        at `period = COLLECTED_CANDLE_PERIOD`, each embedding
        `selection_sql` with the form the design's Data Flow step 2 names
        (`recent` for live, `ever` for the two finalized sets).
  - [ ] Pending condition per Decision 3: `open_time < phase_start` and
        (`st.watermark_ts IS NULL` or below the target end). Each returns
        `(ticker, open_time, close_time, watermark_ts)`.
  - [ ] `pending_backlog` orders by `settlement_ts` ascending and applies
        `limit` (Decision 6); the other two are unbounded — a live market
        must never queue behind history.
  - [ ] Status values are bound from `MarketStatus`, never literal strings.
  - [ ] **Two count methods the core cannot do without:**
        `count_backlog_remaining(period, cutoff)` and
        `count_behind_cutoff(period, cutoff)`, both over
        `selection_sql(rule, "ever")`. `backlog_remaining` is **not**
        derivable from `pending_backlog`'s rows — that query is capped at
        `CANDLE_BACKLOG_REQUESTS_PER_PASS × CANDLE_BATCH_MAX_TICKERS`, so
        `len(rows)` equals the cap on every pass until the backlog drains,
        reporting a flat line where the criterion asks for a falling one.
        The core issues no SQL of its own, so without these the count has
        nowhere to live.
  - [ ] Success: the three pending queries differ only in the
        status/settlement conditions, the form passed to `selection_sql`,
        and the ordering; the two count methods share the same predicate.

- [ ] **Task 4.3: Writes and state** (effort: 3)
  - [ ] `CANDLE_COLUMNS` — the flattening map from `Candlestick`'s nested
        `yes_bid`/`yes_ask`/`price` `PriceOhlc` objects to the table's
        sixteen column names (Decision 10). Defined once here; the parity
        test checks it against the live table.
  - [ ] `insert_candles(rows) -> int` — multi-row `INSERT … ON CONFLICT DO
        NOTHING`, chunked under `_MAX_BIND_PARAMS` exactly as
        `CatalogRepository._upsert` does. No `raw` column (261 Decision 6),
        and never `DO UPDATE`.
  - [ ] `advance_state(period, advances)` — one multi-row upsert into
        `market_candle_state` setting `watermark_ts = EXCLUDED.watermark_ts`,
        `coverage_from_ts = COALESCE(state.coverage_from_ts,
        EXCLUDED.coverage_from_ts)` (so a re-run can never move it later),
        `updated_at = now()`.
  - [ ] `set_sync_state(phase_start, cutoff)` writing
        `Surface.CANDLESTICKS`'s `last_full_sync_at` and `watermark_ts`
        (Decision 11) — reuse `CatalogRepository`'s `_set_state_column`
        pattern rather than a new spelling.
  - [ ] `transaction()` delegating to the connection, as
        `CatalogRepository.transaction()` does — the caller owns granularity
        (one transaction per batch).
  - [ ] Storage failure taxonomy as 262: an `IntegrityError` on a batch is
        retried per market so offenders become item errors;
        `OperationalError` propagates (storage abort); any other
        `psycopg.Error` propagates as a bug.
  - [ ] Success: the module stays under the ~300-line guideline.

- [ ] **Task 4.4a: Let the test helper write real series** (effort: 2)
  - [ ] `kalshi_helpers.write_catalog(repo, markets)` synthesizes its series
        through `parent_series`, which builds `km.Series(ticker=t)` — ticker
        only, so **every series it writes has `category IS NULL` and `title
        IS NULL`**. The predicate fixture set below needs both, so the
        helper must accept them.
  - [ ] Add an optional parameter: `write_catalog(repo, markets,
        series=None)` uses caller-supplied `km.Series` rows when given and
        falls back to today's `parent_series` behavior when not, leaving
        every existing caller unaffected.
  - [ ] Success: the existing kalshi integration tests pass unchanged, and a
        caller can write a series carrying a category and a title.

- [ ] **Task 4.4: Repository and predicate integration tests** (effort: 3)
  - [ ] Extend `test/integration/test_kalshi_repository.py` (or a new
        `test_kalshi_candles.py` in the same tier) using the `kalshi_db`
        fixture and the `write_catalog` of Task 4.4a.
  - [ ] **The predicate fixture set** — six markets with explicit series: a
        Sports market, a `Mentions`-category market, a mention-titled market
        in another category, a never-traded market, a traded-24 h Politics
        market, and **a market whose series has a NULL category and a NULL
        title**. Assertions (Criterion 2): under the default rule
        `pending_live` returns the traded Politics market **and the
        NULL-category one** (Task 4.1's NULL rule — an uncategorised series
        is neither Sports nor Mentions, so it is kept); under an allow-list
        of `Sports` with the exclusions cleared, only the Sports market is
        (the allow-list deliberately does not match NULL); with
        `traded_only=false` the never-traded market joins; with every
        setting empty all six are returned.
  - [ ] Assert the NULL case **by row identity, not by count** — a count
        assertion passes for the wrong reason if the NULL market is dropped
        while another is wrongly kept.
  - [ ] The same set under the `ever` form for finalized rows.
  - [ ] An invalid regex surfaces the database's own error (a
        `ProgrammingError`) rather than being swallowed — this is a
        configuration bug and must be loud.
  - [ ] `CANDLE_COLUMNS` parity: every mapped column exists on
        `kalshi.candlesticks` and every non-key column of the table is
        mapped — so adding a column without mapping it fails here.
  - [ ] Conflict-ignore: inserting the same batch twice leaves one row per
        key and reports the second insert as writing nothing (Criterion 4).
  - [ ] `advance_state` sets `coverage_from_ts` on first write and leaves it
        unchanged on a later write with a different start (Criterion 6).
  - [ ] A market whose `close_time` moved later becomes pending again.
  - [ ] A market finalized before the cutoff is never returned by
        `pending_backlog` (Criterion 9).
  - [ ] **The two count methods** (Task 4.2): with more selected finalized
        markets than the cap admits, `count_backlog_remaining` reports the
        **full** remainder while `pending_backlog` returns at most the cap,
        and the remainder **falls** once a batch of them gains state rows
        (Criterion 8 — the number must move, not sit at the cap);
        `count_behind_cutoff` counts a market finalized before the cutoff
        and excludes one finalized after it (Criterion 9).
  - [ ] Success: the kalshi integration set passes.
  - [ ] **Commit**: `feat: add kalshi candle repository and selection predicate`.

## Task review disposition (20260826)

Review: `user/reviews/264-review.tasks.candlestick-collection.part-1.md`,
claude-opus-5, verdict **CONCERNS** against `1abefcd` (five concerns, three
notes, two passes). CONCERNS passes the gate. All five concerns and both
actionable notes are fixed in place. The tasks they name now live across
this file and part 2; each fix is described here with the part it landed
in.

- **F001 (concern) — no repository method could produce `backlog_remaining`
  or `behind_cutoff`.** Valid, and the failure mode was specific: the core
  is forbidden from issuing SQL, and `pending_backlog` is capped, so
  `len(rows)` sits at the cap on every pass until the backlog drains —
  Criterion 8 asks for a *falling* number and would have reported a flat
  one. Fixed: Task 4.2 gains `count_backlog_remaining` and
  `count_behind_cutoff` over the same predicate, Task 4.4 asserts the
  remainder exceeds the capped row count and then falls, and part 2's Task
  5.2a sources both counts from these methods.
- **F002 (concern) — `kalshi_helpers.write_catalog` cannot build the
  predicate fixture set.** Verified against the helper: `parent_series`
  builds `km.Series(ticker=t)`, so every series it writes has a NULL
  category and NULL title, and Task 4.4's assertions would have passed for
  the wrong reason. Fixed: new Task 4.4a adds an optional `series`
  parameter with today's behavior as the fallback.
- **F003 (concern) — the predicate's NULL behavior was unspecified.**
  Valid and the most consequential of the five. Measured on the test cluster
  rather than reasoned about: `NOT (category = ANY(...))`, `ticker !~ ...`,
  and `title !~* ...` all evaluate to **NULL** on a NULL column, and NULL in
  a `WHERE` is not TRUE — an uncategorised series would have been dropped
  from collection *and* from `closed_excluded_by_rule`, losing coverage with
  no report of the loss. Fixed: Task 4.1 mandates the `COALESCE` forms with
  the measured truth table, keeps the allow-list deliberately NULL-strict,
  and Task 4.4 gains a sixth fixture market asserted by row identity. The
  review's suggested `IS DISTINCT FROM ALL` is not valid PostgreSQL syntax —
  checked, and the task says so.
- **F004 (concern) — Criterion 1's candle-abort clause had no task.** Valid.
  Fixed in part 2's Task 5.5, which now asserts that a candle-phase abort
  leaves the catalog phase's outcome and `sync_state['catalog']` intact.
- **F005 (concern) — Task 5.2 was the size outlier.** Valid; both reviews
  raised it. Fixed in part 2 by splitting at the batch boundary into 5.2a /
  5.2b and 5.3a / 5.3b, with an added checkpoint commit at 5.3a. No task in
  either file now exceeds effort 3.
- **F006 (note) — `selection_sql`'s clause-omission matrix has no unit
  test.** Fixed: new Task 4.1b covers the combinatorics without a database
  and asserts the `COALESCE`/allow-list asymmetry F003 introduced.
- **F007 (note) — the rehearsal did not record the pending queries' wall
  time.** Fixed in part 2's Task 7.4 (a `\timing on` bullet), distinguished
  from Task 8.2's phase wall time, which is the Decision 9 evidence.
- **F008 (note) — no load test is required.** Agreed, no action: the slice
  states no NFR, and its workload numbers are measurements and derived
  estimates, not thresholds. Adding a `test/load/` task would invent a bound
  the design declined to set.
- F009, F010 (pass): no action.
