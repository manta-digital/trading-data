---
docType: tasks
slice: candlestick-collection
project: trading-data
lld: user/slices/264-slice.candlestick-collection.md
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [261, 262, 263]
interfaces: [265, 266]
projectState: >
  Part 2 of 3. Part 1 (264-tasks.candlestick-collection-1.md) delivers the
  constants and the configurable collection rule, the batch client method and
  its fixtures, migration kalshi_005 with the candlesticks hypertable and the
  ledger preflight, and the pure batch planner. This file builds what consumes
  them: CandleRepository (the single rendering of the selection rule, the
  pending queries, the writes) and the CandleSync core with its phase.
reviewVerdictsAddressed:
  - 264-review.tasks.candlestick-collection.part-1 (claude-opus-5, CONCERNS, F001-F003/F006 addressed)
  - 264-review.tasks.candlestick-collection.part-2 (claude-opus-5, CONCERNS, F001-F003 addressed)
dateCreated: 20260826
dateUpdated: 20260826
status: not_started
---

## Context Summary

- Working on **264 Candlestick Collection**, part 2 of 3 — the repository and
  the core. **Complete part 1's Sections 1–3 first**: Section 4 below renders
  the rule defined in part 1 Task 1.2 against the schema created in part 1
  Task 2.1, and Section 5 drives the planner built in part 1 Section 3.
- The context, hard rules, gates, branch, and host boundary in **part 1's
  Context Summary** apply unchanged to every task here. The two that bite
  hardest in this half: the rule is rendered in exactly **one** place
  (`selection_sql`), and the core issues **no SQL of its own** — every count
  it reports comes from a repository method.
- Source of truth remains `user/slices/264-slice.candlestick-collection.md`,
  referenced by decision and criterion number.
- Part 3 (`264-tasks.candlestick-collection-3.md`) has the `status` block,
  the end-to-end and compression tests, the rehearsal, the documentation, and
  the host steps.
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

## Section 5: `CandleSync` core, `CandlesPhase`, renderer dispatch

Design *Data Flow*, *`collection_pass.py`*, *`CandleResult.to_dict()`*,
Decisions 6, 7, 9. The core has no httpx, no typer, and no SQL: it depends
on the `CandleSource` Protocol and `CandleRepository`.

- [ ] **Task 5.1: Result and source types** (effort: 2)
  - [ ] In `data/kalshi/candle_types.py` (created in Task 1.2), add
        `CandleResult` with `to_dict()` producing exactly the design's JSON
        shape (`run_id, started_at, period, cutoff, pending{live,
        finishing, backlog, backlog_remaining}, requests,
        markets_requested, markets_advanced, candles_fetched,
        candles_written, item_errors, duration_ms, error`), and
        `classify_candles(result, exc) -> SyncOutcome` mirroring
        `sync_types.classify` (storage first, then provider, `TypeError` for
        anything else, `PARTIAL` when item errors exist, else `OK`).
  - [ ] `CandleSource` Protocol: `get_markets_candlesticks(...)` and
        `get_historical_cutoff()` — the two methods the core calls.
  - [ ] Success: `to_dict()` round-trips through `json.dumps` (every value is
        a JSON scalar, list, or dict — datetimes rendered as ISO strings).

- [ ] **Task 5.2a: `CandleSync` skeleton — cutoff, pending sets, plan** (effort: 3)
  - [ ] New `data/kalshi/candle_sync.py` implementing Data Flow steps 1–4
        and 6, with the batch loop left as a single call site Task 5.2b
        fills in: read the cutoff once; build the three pending sets through
        the repository; map them through `target_window`, dropping targets
        with `start >= end`; plan batches with `plan_batches`.
  - [ ] Only the backlog set is capped (`CANDLE_BACKLOG_REQUESTS_PER_PASS ×
        CANDLE_BATCH_MAX_TICKERS` rows); live and finishing are never
        capped, so a market that closed since the last pass never queues
        behind history.
  - [ ] One INFO line at phase start carrying the cutoff and
        `CandleRule.describe()` — the cutoff line is the signal that 266 has
        become urgent, so log it every run whether or not anything is
        pending.
  - [ ] After the batch loop returns:
        `sync_state['candlesticks'].last_full_sync_at = phase_start`,
        `.watermark_ts = cutoff`; emit `phase_finished` with the counts plus
        `backlog_remaining` and `behind_cutoff` **from the Task 4.2 count
        methods**, never from `len(backlog_rows)` — that equals the cap on
        every pass until the backlog drains.
  - [ ] Sequential work on the run's single connection (Decision 9) — no
        concurrency, no connection of its own.
  - [ ] Success: with an empty pending set the phase completes, writes
        `sync_state`, and emits `phase_finished` with zero counts.

- [ ] **Task 5.2b: The batch loop — fetch, write, item errors** (effort: 3)
  - [ ] **One transaction per batch** (Data Flow step 5): within it, insert
        every served candle with conflict-ignore, and upsert state for every
        requested ticker **present in the response — with or without
        candles** — at `watermark_ts = min(batch end, close_time + period)`
        and `coverage_from_ts = coalesce(existing, target start)`. This is
        the sparseness rule: a market that served nothing still advances, or
        an idle market would be re-requested forever (Decision 3).
  - [ ] A requested ticker **absent** from the response is the one per-market
        failure the API signals: emit `item_error` with `phase="candles"`
        and the reason `"not served by the batch endpoint"`, leave its state
        untouched, and continue (Decision 7).
  - [ ] A `ProviderError` on a batch **aborts the phase** — the planner
        guarantees the caps, so a 400 here is our bug or an API change and
        must be visible (Decision 7). Do not catch it inside the batch loop.
  - [ ] One INFO line per `CANDLE_PROGRESS_EVERY_REQUESTS` requests.
  - [ ] Success: the module keeps to the ~300-line guideline; no `psycopg`
        import beyond the exception types it must catch, no client import.

- [ ] **Task 5.3a: Candle test doubles** (effort: 2)
  - [ ] Extend `test/kalshi_support/fake_source.py` with candlestick
        support: a `FakeCandleSource` (or candle methods on the existing
        fake) serving scripted candles per ticker and **recording every
        query it receives** — the recorded queries are what proves the rule
        selects exactly what it should at the unit level (Criterion 2).
  - [ ] Add candle methods to `test/kalshi_support/fake_repository.py`
        mirroring the real repository's method set — **including the two
        count methods** — and following its existing `fail_on(method, exc,
        at=)` pattern so a storage abort can be scripted.
  - [ ] Success: the doubles import cleanly and existing kalshi unit tests
        still pass.
  - [ ] **Commit**: `test: add kalshi candle test doubles`.

- [ ] **Task 5.3b: Core unit tests** (effort: 3)
  - [ ] New `test/unit/data/kalshi/test_candle_sync.py`: the three pending
        sets are requested and only the backlog is capped; a ticker present
        with **zero candles still advances** its watermark; an omitted
        ticker produces one item error and no state advance; a
        `ProviderError` on a batch aborts and later batches are not
        requested; `coverage_from_ts` is set once and not moved by a second
        pass; `sync_state` is written after the last batch with the observed
        cutoff; `backlog_remaining` comes from the count method and
        **differs from the capped row count** when the backlog exceeds the
        cap; `classify_candles` returns each outcome for its condition;
        events carry `phase="candles"` and the run's `run_id`; the progress
        line appears at the configured cadence.
  - [ ] Success: unit tier passes; no network and no database.

- [ ] **Task 5.4: `CandlesPhase`, `PASS_PHASES`, renderer dispatch** (effort: 3)
  - [ ] `collection_pass.py`: `PassPhaseName.CANDLES = "candles"`;
        `CandlesPhase` with the **same body shape as `CatalogPhase`** — lazy
        in-method imports, `time.monotonic()` around the run, exactly two
        `except` clauses (`ProviderError`, `psycopg.OperationalError` with
        `logger.exception`), and a `PhaseReport` built from
        `classify_candles`. Append it: `PASS_PHASES = (CatalogPhase(),
        CandlesPhase())`.
  - [ ] **`kalshi_render.py` renderer dispatch — this is a required fix, not
        an enhancement.** `print_pass_summary` today calls
        `print_phase_summary` for every report with a non-empty summary, and
        that function indexes catalog-only keys (`phases`, `transitions`,
        `awaiting`, `windows_completed`, `settled_captured`), so a candles
        summary would raise `KeyError` the first time a pass runs both
        phases. Replace the unconditional call with a lookup by
        `PassPhaseName` → renderer, and add
        `print_candle_summary(summary)` printing requests, markets
        requested/advanced, candles fetched/written, pending
        live/finishing/backlog (+ remaining), and item errors.
  - [ ] A phase name with no registered renderer must fail loudly (a named
        error), not print nothing — silently skipping a phase's summary is
        how 265 would ship invisible.
  - [ ] No change to `kalshi.py`'s `run_pass`, the `pass` command surface,
        the exit-code map, or any unit file.
  - [ ] Success: `mt data kalshi pass --json` reports two phases in order.

- [ ] **Task 5.5: Pass and rendering unit tests** (effort: 2)
  - [ ] Extend `test/unit/data/kalshi/test_collection_pass.py`:
        `PASS_PHASES` is exactly `(CatalogPhase(), CandlesPhase())` by name
        and order (Criterion 1); a catalog abort leaves the candle phase
        `skipped`.
  - [ ] **Criterion 1's third clause, which nothing else asserts:** a pass
        whose *candle* phase aborts still reports the catalog phase's
        original outcome and leaves `sync_state['catalog']` unchanged. 263's
        `CollectionPass` very likely already guarantees this, but the
        criterion is restated in this slice and 265 copies the contract, so
        assert it rather than inherit it.
  - [ ] **`CandleResult.to_dict()` survives `json.dumps`** with the design's
        exact key set — including a **non-empty `item_errors`** and a
        **non-null `cutoff`**, the two places a `datetime` most easily
        leaks. Follow the existing precedent
        (`test_collection_pass.py::test_to_dict_round_trips_through_json`).
        Task 5.1 states this as a success condition; without a committed
        test it can be satisfied by a one-off check and the phase summary
        then reaches `--json` unguarded.
  - [ ] New or extended CLI unit test: the renderer dispatch selects
        `print_candle_summary` for a candles report and
        `print_phase_summary` for a catalog report; **a pass result carrying
        both summaries renders without raising** (the regression this
        section fixes); an unregistered phase name raises the named error.
  - [ ] Success: `uv run pytest test/unit -q` passes.
  - [ ] **Commit**: `feat: add kalshi candle phase and per-phase summary rendering`.
