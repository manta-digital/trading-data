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
  - 264-review.tasks.candlestick-collection.part-1 (claude-opus-5, CONCERNS, F001-F003/F006-F007 addressed)
  - 264-review.tasks.candlestick-collection.part-2 (claude-opus-5, CONCERNS, dispositioned in parts 2-3)
dateCreated: 20260826
dateUpdated: 20260826
status: not_started
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
- **This file is part 1 of 3.** Sections 1–3 below build the vocabulary, the
  schema, and the planner — everything the later parts consume.
  `264-tasks.candlestick-collection-2.md` has the repository and the core
  (Sections 4–5); `264-tasks.candlestick-collection-3.md` has the `status`
  block, verification, documentation, and the host steps (Sections 6–8).
  Both depend on everything here.
- Next slice: 265 (trades) copies this phase's shape; 266 (historical
  backfill) consumes `behind_cutoff_uncollected` and must pause this
  hypertable's compression policy during its drain.

## Section 1: Constants, the collection rule, client and fixtures

Design *Constants*, *Settings — the collection rule* (Decision 2), *Client
and models*, *Fixtures and recorder*. This section adds no behavior — it
adds the vocabulary every later section depends on.

- [ ] **Task 1.1: Constants** (effort: 1)
  - [ ] Add to `data/kalshi/constants.py`, each with a comment naming its
        decision or its Discovery Findings evidence:
        `MARKETS_CANDLESTICKS_PATH`, `COLLECTED_CANDLE_PERIOD`
        (`CandlePeriod.MINUTE`, Decision 1), `CANDLE_BATCH_MAX_TICKERS`,
        `CANDLE_BATCH_MAX_CANDLES`, `CANDLE_SINGLE_MAX_CANDLES`,
        `CANDLE_FIRST_SIGHT_LOOKBACK`, `CANDLE_BACKLOG_REQUESTS_PER_PASS`,
        `CANDLE_PROGRESS_EVERY_REQUESTS`, `CANDLE_LAG_STALE_AFTER`,
        `KALSHI_CANDLE_CHUNK_INTERVAL`, `KALSHI_CANDLE_COMPRESS_AFTER`.
        Values are in the design's *Constants* block.
  - [ ] Note in the comment on `CANDLE_BATCH_MAX_CANDLES` that the cap is on
        `len(tickers) × periods_in_window` of the **request**, not the
        response — this is the fact the planner is built around.
  - [ ] `CANDLE_SINGLE_MAX_CANDLES` is recorded but unused by the phase (the
        batch path is the only one used); say so in its comment so a reader
        does not hunt for its call site.
  - [ ] Success: `test/unit/data/kalshi/test_constants.py` extended — every
        new constant has the design's value and the two `timedelta`
        constants are `timedelta` instances; existing assertions unchanged.

- [ ] **Task 1.2: `CandleRule` and the `Settings` fields** (effort: 3)
  - [ ] New `data/kalshi/candle_types.py` with the frozen dataclass
        `CandleRule(traded_only, categories, excluded_categories,
        excluded_series_pattern, excluded_title_pattern)` and
        `CandleRule.describe() -> str` rendering the one-line human form the
        design shows (`candles rule: traded 24h · categories all · excluding
        Sports, Mentions · patterns 2`). `describe()` must be deterministic:
        sort the category sets.
  - [ ] Five fields on `Settings` (`config/__init__.py`) with the design's
        names, types, and defaults — rule C is the default. Follow the
        file's existing comment style: say *why* the values are data rather
        than an enum (the category vocabulary is Kalshi's, not ours).
  - [ ] One `field_validator(mode="before")` splitting the comma-separated
        forms for the two category sets: split on `,`, strip whitespace,
        drop empties, build a `frozenset`. Empty string → empty frozenset.
        Reason in a comment: pydantic-settings' default JSON-list parsing
        for a set-typed field is not what a `.env` author writes.
  - [ ] The two pattern fields treat empty string as `None` (clause
        disabled) — one validator, or `str | None` with a normalizer.
  - [ ] `Settings.candle_rule() -> CandleRule` — the single parse point.
        Docstring states the evaluation order (allow-list if non-empty →
        exclude-list → patterns → traded) and that exclude wins when a
        category appears in both.
  - [ ] Success: `Settings()` with no environment yields exactly rule C.

- [ ] **Task 1.3: Settings and rule unit tests** (effort: 2)
  - [ ] New `test/unit/test_candle_rule_settings.py` (or extend the existing
        settings test module if one covers `Settings`): defaults equal rule
        C; `MT_KALSHI_CANDLE_CATEGORIES=Sports, Politics` parses to a
        two-member frozenset with whitespace trimmed; empty value → empty
        frozenset; `MT_KALSHI_CANDLE_TRADED_ONLY=false` parses as a bool;
        an empty pattern disables the clause (`None`, not `""`);
        `candle_rule()` returns the expected `CandleRule`; `describe()` is
        stable across two calls and reflects a changed rule.
  - [ ] Use monkeypatched environment variables, not a written `.env`.
  - [ ] Success: tests pass; no test re-spells the rule's SQL.

- [ ] **Task 1.4: Batch client method and models** (effort: 2)
  - [ ] `models.py`: `MarketCandlesticks(market_ticker: str, candlesticks:
        list[Candlestick])` and `BatchCandlesticksResponse(markets:
        list[MarketCandlesticks])`, following the existing model style.
  - [ ] `client.py`: `get_markets_candlesticks(tickers, *, start_ts, end_ts,
        period_interval) -> list[MarketCandlesticks]` against
        `MARKETS_CANDLESTICKS_PATH`, in the `# ---` banner style the
        candlestick section already uses. **The transport's `ParamValue` is
        `str | int | bool | None` — it has no list member, and
        `_clean_params` would render a list as `"['A', 'B']"`.** Join the
        tickers into a comma-separated string in the client, as
        `MarketsQuery.tickers` and the recorder already do; do not widen the
        transport for this.
  - [ ] Pass `int(period_interval)` for the period, matching
        `get_market_candlesticks`.
  - [ ] The client passes the request through and enforces no cap — the
        planner owns the cap (Decision 7); say so in the docstring.
  - [ ] Success: the method issues exactly one request; unknown tickers are
        simply absent from the returned list (no error raised at this
        layer).

- [ ] **Task 1.5: Fixtures and the recorder** (effort: 2)
  - [ ] `scripts/record_kalshi_fixtures.py` gains two `--only` targets
        following the existing target declaration pattern:
        `candlesticks_batch` — a real batch response over at least three
        selected tickers for the last hour at `period_interval=1`, chosen so
        that **at least one entry is empty** and at least one candle has
        `price: {}`; and `candlesticks_batch_over_cap` — the HTTP 400 body
        provoked by 100 tickers × 360 minutes, saved as
        `error_400_candles_cap.json`.
  - [ ] Record both against the live public API and commit the JSON under
        `test/fixtures/kalshi/`. The existing `candlesticks.json` stays
        (261's single-market client test still uses it).
  - [ ] Success: both fixture files exist and are valid JSON; the empty
        entry and the `price: {}` candle are actually present — check, do
        not assume, since the recorder's window choice decides it.

- [ ] **Task 1.6: Client, model, and fixture unit tests** (effort: 2)
  - [ ] Extend `test/unit/data/kalshi/test_client_endpoints.py`: the request
        `get_markets_candlesticks` issues carries the expected path and
        query parameters (`market_tickers`, `start_ts`, `end_ts`,
        `period_interval`), and the batch fixture parses into
        `MarketCandlesticks` objects **including the empty-list entry** and
        the `price: {}` candle.
  - [ ] Extend `test/unit/data/kalshi/test_fixtures.py`: the over-cap
        fixture raises `ProviderPermanentError` through the transport's
        error mapping (a 400 is permanent, not retried).
  - [ ] Extend `test/unit/data/kalshi/test_models.py` for the two new models.
  - [ ] Success: all pass; no network access in the unit tier.
  - [ ] **Commit**: `feat: add kalshi batch candlestick client, rule config, and fixtures`.

## Section 2: Migration `kalshi_005` and the ledger preflight

Design *Migration `kalshi_005_candlesticks`* (Decision 4), *Preflight*
(Decision 8). The design's SQL block is a sketch; the rules below govern
where it and the existing code disagree.

- [ ] **Task 2.1: `kalshi_005_candlesticks`** (effort: 3)
  - [ ] Append one entry to `KALSHI_MIGRATIONS` in
        `market/schema/migrations/kalshi.py` with `id`,
        `description`, and `sql`, matching the shape of the four entries
        already there. Additive and idempotent (`IF NOT EXISTS`); no
        down-migration.
  - [ ] `CREATE TABLE kalshi.candlesticks` with the design's columns:
        `market_ticker` (FK to `kalshi.markets (ticker)`), `period`,
        `end_period_ts`, the sixteen nullable NUMERIC OHLC columns,
        `volume_fp NUMERIC NOT NULL`, `open_interest_fp NUMERIC`, primary
        key `(market_ticker, period, end_period_ts)`.
  - [ ] The period CHECK constraint is rendered by the existing
        **`_period_check_sql()`** helper, not hand-listed — the module
        docstring makes this a standing rule and `market_candle_state`
        already follows it. (The design's illustrative SQL shows a literal
        list; do not copy it.)
  - [ ] `create_hypertable('kalshi.candlesticks', 'end_period_ts',
        chunk_time_interval => …, if_not_exists => TRUE)` with the interval
        rendered from `KALSHI_CANDLE_CHUNK_INTERVAL` — the constant is the
        single definition, so import it rather than writing `7 days`.
  - [ ] `ALTER TABLE … SET (timescaledb.compress, compress_segmentby =
        'market_ticker', compress_orderby = 'end_period_ts DESC')` then
        `add_compression_policy(… compress_after => …, if_not_exists =>
        TRUE)` with the horizon rendered from `KALSHI_CANDLE_COMPRESS_AFTER`.
  - [ ] `ALTER TABLE kalshi.market_candle_state ADD COLUMN IF NOT EXISTS
        coverage_from_ts TIMESTAMPTZ` and its comment (Decision 5).
  - [ ] Rewrite the `market_candle_state.watermark_ts` comment to Decision
        3's semantics (the window end fetched through, **not** the newest
        stored candle — kalshi_003's text is wrong for sparse data).
  - [ ] Rewrite the `kalshi.sync_state.watermark_ts` comment. Note that
        `kalshi_004` already rewrote this comment for the catalog and trades
        surfaces; `COMMENT ON` replaces the whole string, so the new text
        must **carry the catalog and trades clauses forward** and change
        only the candlesticks clause (Decision 11: the historical cutoff
        observed by the last candle phase). Do not shorten the others away.
  - [ ] `GRANT SELECT, INSERT, UPDATE, DELETE ON kalshi.candlesticks TO`
        the `APP_ROLE` constant.
  - [ ] Success: `mt data migrate apply --track kalshi` applies it to a
        throwaway database and re-applying is a no-op.

- [ ] **Task 2.2: Ledger preflight** (effort: 2)
  - [ ] In `data/kalshi/db.py`, replace the `to_regclass('kalshi.sync_state')`
        probe in `open_sync_connection` with a check that every migration id
        in `TRACKS["kalshi"]` is present in `schema_migrations`
        (one query, ids bound as a parameter).
  - [ ] Missing ids → `PreflightError` naming them and the remedy, in the
        design's wording (`kalshi track has pending migrations:
        kalshi_005_candlesticks — mt data migrate apply --track kalshi`).
        An **absent `schema_migrations` table** must produce the same error,
        not an unhandled `psycopg` error — the bare-database case.
  - [ ] Update the `TRACK_NOT_APPLIED` wording to match. The advisory-lock
        step is unchanged and still runs after this check.
  - [ ] Success: the check names *all* missing ids, not just the first.

- [ ] **Task 2.3: Migration and preflight integration tests** (effort: 3)
  - [ ] Extend `test/integration/test_kalshi_migrations.py`: `kalshi_005`
        applies and re-applies cleanly; `kalshi.candlesticks` appears in
        `timescaledb_information.hypertables` with the configured chunk
        interval; its compression settings are the design's segmentby and
        orderby; a compression policy exists on it whose `compress_after`
        equals `KALSHI_CANDLE_COMPRESS_AFTER`, read back from
        `timescaledb_information.jobs` **by hypertable name and
        `proc_name`, never by job ID** (job ids regenerate).
  - [ ] `market_candle_state.coverage_from_ts` exists; the two rewritten
        comments contain their new semantics; the `sync_state.watermark_ts`
        comment still carries its catalog and trades clauses (guards against
        Task 2.1 dropping them).
  - [ ] Preflight: with `kalshi_005` deleted from `schema_migrations`,
        `open_sync_connection` raises `PreflightError` naming
        `kalshi_005_candlesticks`; restoring it lets the connection open.
  - [ ] Success: `uv run python scripts/run_tests.py integration -- -k
        kalshi -q` passes. Never point the tier at the production URL.
  - [ ] **Commit**: `feat: add kalshi_005 candlestick hypertable and ledger preflight`.

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

## Task review disposition (20260826)

Review: `user/reviews/264-review.tasks.candlestick-collection.part-1.md`,
claude-opus-5, verdict **CONCERNS** against `1abefcd` (five concerns, three
notes, two passes). CONCERNS passes the gate. All five concerns and both
actionable notes are fixed in place. The tasks they name now live across
three files (see *Not adopted* in part 3 for why); each fix is described
here with the part it landed in.

- **F001 (concern) — no repository method could produce `backlog_remaining`
  or `behind_cutoff`.** Valid, and the failure mode was specific: the core
  is forbidden from issuing SQL, and `pending_backlog` is capped, so
  `len(rows)` sits at the cap on every pass until the backlog drains —
  Criterion 8 asks for a *falling* number and would have reported a flat
  one. Fixed in part 2: Task 4.2 gains `count_backlog_remaining` and
  `count_behind_cutoff` over the same predicate, Task 4.4 asserts the
  remainder exceeds the capped row count and then falls, and Task 5.2a
  sources both counts from these methods.
- **F002 (concern) — `kalshi_helpers.write_catalog` cannot build the
  predicate fixture set.** Verified against the helper: `parent_series`
  builds `km.Series(ticker=t)`, so every series it writes has a NULL
  category and NULL title, and Task 4.4's assertions would have passed for
  the wrong reason. Fixed in part 2: new Task 4.4a adds an optional `series`
  parameter with today's behavior as the fallback.
- **F003 (concern) — the predicate's NULL behavior was unspecified.**
  Valid and the most consequential of the five. Measured on the test cluster
  rather than reasoned about: `NOT (category = ANY(...))`, `ticker !~ ...`,
  and `title !~* ...` all evaluate to **NULL** on a NULL column, and NULL in
  a `WHERE` is not TRUE — an uncategorised series would have been dropped
  from collection *and* from `closed_excluded_by_rule`, losing coverage with
  no report of the loss. Fixed in part 2: Task 4.1 mandates the `COALESCE` forms with
  the measured truth table, keeps the allow-list deliberately NULL-strict,
  and Task 4.4 gains a sixth fixture market asserted by row identity. The
  review's suggested `IS DISTINCT FROM ALL` is not valid PostgreSQL syntax —
  checked, and the task says so.
- **F006 (note) — `selection_sql`'s clause-omission matrix has no unit
  test.** Fixed in part 2: new Task 4.1b covers the combinatorics without a
  database and asserts the `COALESCE`/allow-list asymmetry F003 introduced.
- **F007 (note) — the rehearsal did not record the pending queries' wall
  time.** Fixed in part 3's Task 7.4 (a `\timing on` bullet), distinguished
  from Task 8.2's phase wall time, which is the Decision 9 evidence.
- **F004 (concern) — Criterion 1's candle-abort clause had no task.** Valid.
  Fixed in part 2's Task 5.5, which now asserts that a candle-phase abort
  leaves the catalog phase's outcome and `sync_state['catalog']` intact.
- **F005 (concern) — Task 5.2 was the size outlier.** Valid; both reviews
  raised it. Fixed in part 2 by splitting at the batch boundary into 5.2a /
  5.2b and 5.3a / 5.3b, with an added checkpoint commit at 5.3a. No task in
  any part now exceeds effort 3.
- **F008 (note) — no load test is required.** Agreed, no action: the slice
  states no NFR, and its workload numbers are measurements and derived
  estimates, not thresholds. Adding a `test/load/` task would invent a bound
  the design declined to set.
- F009, F010 (pass): no action.
