---
docType: tasks
slice: candlestick-collection
project: trading-data
lld: user/slices/264-slice.candlestick-collection.md
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [261, 262, 263]
interfaces: [265, 266]
projectState: >
  Continuation of 264-tasks.candlestick-collection-1.md. Sections 1-4 of that
  file (constants and the collection rule, the batch client and fixtures,
  migration kalshi_005 and the ledger preflight, the pure batch planner, and
  CandleRepository with its selection predicate) are prerequisites for
  everything here: this file starts at the CandleSync core, which consumes
  all of them.
dateCreated: 20260826
dateUpdated: 20260826
status: not_started
---

## Context Summary

- Working on **264 Candlestick Collection**, part 2 of 2. Part 1 is
  `user/tasks/264-tasks.candlestick-collection-1.md`; **complete its
  Sections 1–4 first** — the core built here depends on the planner, the
  repository, the rule, the client method, and the migration.
- The context, hard rules, gates, branch, and host boundary stated in part
  1's Context Summary apply unchanged to every task below. Read them there
  rather than assuming; the two that bite hardest in this half are that the
  rule is rendered in exactly one place (`selection_sql`) and that
  `status.py` may import neither the client nor the transport.
- Source of truth remains the slice design at
  `user/slices/264-slice.candlestick-collection.md`, referenced by decision
  and criterion number.
- What is left after part 1: the `CandleSync` core and `CandlesPhase`, the
  per-phase renderer dispatch (a required fix — see Task 5.4), the `status`
  candle block, end-to-end and compression integration tests, the throwaway
  rehearsal, the documentation, and the host steps.

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

- [ ] **Task 5.2: `CandleSync.run()`** (effort: 5)
  - [ ] New `data/kalshi/candle_sync.py` implementing the design's Data Flow
        steps 1–6 in order: read the cutoff once; build the three pending
        sets; map them through `target_window`; plan batches; fetch and
        write each batch; then write `sync_state` and emit events.
  - [ ] **One transaction per batch** (Data Flow step 5): within it, insert
        every served candle with conflict-ignore, and upsert state for every
        requested ticker **present in the response — with or without
        candles** — at `watermark_ts = min(batch end, close_time + period)`
        and `coverage_from_ts = coalesce(existing, target start)`. This is
        the sparseness rule: a market that served nothing still advances,
        or an idle market would be re-requested forever (Decision 3).
  - [ ] A requested ticker **absent** from the response is the one per-market
        failure the API signals: emit `item_error` with `phase="candles"`
        and the reason `"not served by the batch endpoint"`, leave its state
        untouched, and continue (Decision 7).
  - [ ] A `ProviderError` on a batch **aborts the phase** — the planner
        guarantees the caps, so a 400 here is our bug or an API change and
        must be visible (Decision 7). Do not catch it inside the batch loop.
  - [ ] Sequential fetch and write on the run's single connection
        (Decision 9) — no concurrency, no connection of its own.
  - [ ] One INFO line per `CANDLE_PROGRESS_EVERY_REQUESTS` requests, and one
        INFO line at phase start carrying the cutoff and
        `CandleRule.describe()` (the cutoff line is the signal that 266 has
        become urgent).
  - [ ] After the last batch: `sync_state['candlesticks'].last_full_sync_at
        = phase_start`, `.watermark_ts = cutoff`; emit `phase_finished`
        with the counts plus `backlog_remaining` and `behind_cutoff`.
  - [ ] Only the backlog set is capped (`CANDLE_BACKLOG_REQUESTS_PER_PASS ×
        CANDLE_BATCH_MAX_TICKERS` rows); live and finishing are never capped.
  - [ ] Success: the module keeps to the ~300-line guideline; no `psycopg`
        import beyond the exception types it must catch, no client import.

- [ ] **Task 5.3: Core unit tests with fakes** (effort: 5)
  - [ ] Extend `test/kalshi_support/fake_source.py` with candlestick
        support: a `FakeCandleSource` (or candle methods on the existing
        fake) that serves scripted candles per ticker and **records every
        query it receives** — the recorded queries are what proves Criterion
        2 at the unit level. Add candle methods to
        `test/kalshi_support/fake_repository.py` mirroring the real
        repository's method set, following its existing `fail_on` pattern.
  - [ ] New `test/unit/data/kalshi/test_candle_sync.py`: the three pending
        sets are requested and only the backlog is capped; a ticker present
        with **zero candles still advances** its watermark; an omitted
        ticker produces one item error and no state advance; a
        `ProviderError` on a batch aborts and later batches are not
        requested; `coverage_from_ts` is set once and not moved by a second
        pass; `sync_state` is written after the last batch with the observed
        cutoff; `classify_candles` returns each outcome for the matching
        condition; events carry `phase="candles"` and the run's `run_id`;
        the progress line appears at the configured cadence.
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
  - [ ] New or extended CLI unit test: the renderer dispatch selects
        `print_candle_summary` for a candles report and
        `print_phase_summary` for a catalog report; **a pass result carrying
        both summaries renders without raising** (the regression this
        section fixes); an unregistered phase name raises the named error.
  - [ ] Success: `uv run pytest test/unit -q` passes.
  - [ ] **Commit**: `feat: add kalshi candle phase and per-phase summary rendering`.

## Section 6: `status` — the candle block

Design *CLI and rendering* (Decision 11), and Criterion 12 — `status`
answers the candle clause from the database alone. Every field is a
persisted fact; nothing here counts rows in `kalshi.candlesticks`.

- [ ] **Task 6.1: `CandleStatus` and `read_candle_status`** (effort: 3)
  - [ ] In `data/kalshi/status.py`, add the frozen `CandleStatus` with the
        design's fields (`period_minutes`, `last_phase_at`,
        `cutoff_observed`, `rule`, `selected_open`, `markets_tracked`,
        `open_lagging`, `open_oldest_watermark`, `complete_through_close`,
        `closed_short_of_close`, `backlog_remaining`,
        `behind_cutoff_uncollected`, `closed_excluded_by_rule`,
        `partial_history`) and its `to_dict()`.
  - [ ] `read_candle_status(conn, rule) -> CandleStatus | None` — synchronous
        psycopg like `read_catalog_status`; returns `None` until the phase
        has run once (no `sync_state` row for `Surface.CANDLESTICKS`).
  - [ ] The cutoff comes from `sync_state['candlesticks'].watermark_ts`, not
        from the API — `status` must make no network call (Decision 11).
  - [ ] Every rule-dependent count calls `selection_sql` — do not re-spell
        the predicate here (Criterion 2's "collection and reporting cannot
        disagree"). `open_lagging` counts only markets the rule **still
        selects** and whose watermark is older than `now −
        CANDLE_LAG_STALE_AFTER`: a deselected market is idle, not lagging.
  - [ ] Success: `status.py` imports neither the client nor the transport
        (Criterion 12).

- [ ] **Task 6.2: Wire the block into the CLI** (effort: 2)
  - [ ] `cli/commands/kalshi.py`'s `status` command reads the candle status
        alongside the catalog one, passing `settings.candle_rule()` so the
        printed rule is the one in force.
  - [ ] `kalshi_render.py::print_status` gains the candle block in the
        design's layout; `None` prints `Candlesticks: never collected`.
  - [ ] `--json` nests the block under `candles`, `null` when never
        collected. The catalog keys keep their current position and spelling
        so existing consumers are unaffected.
  - [ ] Success: both output modes render on a database where the phase has
        never run and on one where it has.

- [ ] **Task 6.3: Status tests** (effort: 3)
  - [ ] Unit: an import-boundary test asserting `status.py`'s module
        imports exclude the client and transport modules (Criterion 12) —
        assert on the imported module graph, not on source text.
  - [ ] Extend `test/integration/test_kalshi_status.py` on `kalshi_db`:
        `read_candle_status` is `None` before the first phase run; after
        seeding `sync_state`, `market_candle_state`, and the predicate
        fixture set, **every field** in the design's list has the expected
        value — including `closed_excluded_by_rule` non-zero and
        `behind_cutoff_uncollected` counting a market finalized before the
        cutoff; changing the rule changes `selected_open` and
        `closed_excluded_by_rule` without any collection happening.
  - [ ] Success: kalshi integration set passes.
  - [ ] **Commit**: `feat: add candle block to mt data kalshi status`.

## Section 7: End-to-end integration, docs, and the rehearsal

- [ ] **Task 7.1: End-to-end pass integration test** (effort: 3)
  - [ ] Extend `test/integration/test_kalshi_pass.py` on `kalshi_db` with a
        fake candle source: a `pass` runs **both** phases in order and
        reports `["catalog", "candles"]` (Criterion 1); candles land under
        the natural key and `market_candle_state` rows exist for every
        requested market including those that served nothing (Criterion 3);
        a **second pass writes only what is new** and no duplicate row
        exists (Criterion 4); a closed market reaches `watermark_ts >=
        close_time + period`, is counted in `complete_through_close`, and is
        not requested again (Criterion 7); the backlog is capped per pass and
        drains oldest-settlement-first across two passes (Criterion 8); an
        omitted ticker yields exit 3 with one item error and no state row
        (Criterion 10).
  - [ ] Success: the kalshi integration set passes end to end.

- [ ] **Task 7.2: Compression proven on a real chunk** (effort: 3)
  - [ ] Integration test (Criterion 13): insert candles old enough to fall
        outside `KALSHI_CANDLE_COMPRESS_AFTER`, run the compression job by
        hand (`CALL run_job(...)`), and assert
        `chunk_compression_stats('kalshi.candlesticks')` reports the chunk
        `Compressed` and that a per-market query over it returns **the same
        rows as before compression**.
  - [ ] Resolve the job by hypertable name and `proc_name` at use time,
        never by a job ID — job IDs regenerate and a recorded one goes
        stale.
  - [ ] Success: the test is self-contained (it creates the old rows it
        needs) and leaves no policy disabled.

- [ ] **Task 7.3: Full gate pass** (effort: 1)
  - [ ] `uv run pytest test/unit -q`; `uv run python scripts/run_tests.py
        integration -- -k kalshi -q`; `uv run ruff check` and `uv run ruff
        format --check` **scoped to the files touched** (263 process note);
        `uv run --extra dev mypy` and `npx --yes pyright` on the kalshi
        source paths plus the new tests.
  - [ ] Success: all clean; no new dependency in `pyproject.toml`.
  - [ ] **Commit**: `test: add kalshi candle end-to-end and compression coverage`.

- [ ] **Task 7.4: Rehearsal on a throwaway database** (effort: 3)
  - [ ] Walkthrough steps 1–5 exactly, on a **throwaway database on the test
        cluster** — the shell's `MT_TIMESCALE_DB_URL` points at it and the
        production URL never enters the shell. Use `sync --settled-since` at
        about six hours to keep the catalog small.
  - [ ] Record: the migrate/hypertable check (step 1); the preflight
        failure naming `kalshi_005_candlesticks` and its recovery (step 2,
        Criterion 11); the rule inspection **including the full list of
        series the exclusion patterns match** — read that list, it is the
        check that the patterns neither over- nor under-reach (step 3); the
        env-override showing `selected_open` moving with no collection
        (Criterion 2); the first pass with its phase lines and counts (step
        4); the second pass, the duplicate check, the `status` block in both
        modes, and the compression job run (step 5).
  - [ ] Write it all into `user/notes/2026-MM-DD-264-rehearsal.md` (the date
        run), with the matched-series listing pasted in full.
  - [ ] Success: two passes exit 0; zero HTTP 400 on `/markets/candlesticks`
        (Criterion 5); the duplicate query returns 0 rows (Criterion 4).
  - [ ] **Commit**: `docs: record 264 throwaway-database rehearsal`.

- [ ] **Task 7.5: Documentation** (effort: 2)
  - [ ] `deploy/manta-trading.env.example`: five commented `MT_KALSHI_CANDLE_*`
        lines under the existing optional-tuning block, showing the
        defaults, in the file's established comment style.
  - [ ] Runbook 100, Kalshi subsection: one paragraph covering the pass now
        having two phases; what the collection rule is and that it is set by
        the `MT_KALSHI_CANDLE_*` lines; that `status` shows the rule in
        force and the excluded count; that `kalshi_005` must be applied
        during the update and a firing before that exits 1 naming the
        migration (expected, not a defect); that the first firing after the
        release runs a few minutes longer and the backlog drains over about
        six firings; and that chunks older than 14 days compress
        automatically — how to see the policy and its last run **by
        hypertable name and `proc_name`, never by job ID** — and that a
        historical backfill must pause it.
  - [ ] `CHANGELOG.md` under `[Unreleased]`: the candle phase and the rule,
        the status block, the migration (hypertable + compression policy),
        and the preflight change.
  - [ ] Success: the runbook paragraph names no job ID and no wall-clock
        promise the collector does not make.
  - [ ] **Commit**: `docs: document the kalshi candle phase and collection rule`.

## Section 8: Host steps and close

Walkthrough steps 6–8. **[PM]** steps run on manta9000. The release must be
merged and tagged per runbook 100 before 8.1 (not a task here).

- [ ] **Task 8.1 [PM]: Deploy and apply the migration** (effort: 2)
  - [ ] Runbook 100 *Update procedure*: install the tag (once — this release
        adds **no** new unit, so the two-run installer dance 263 needed does
        not apply), then from the dev checkout `uv run mt data migrate
        status --track kalshi` (1 pending) → `uv run mt data migrate apply
        --track kalshi` with the maintenance credential → `status` shows 0
        pending. Record each output.
  - [ ] A firing between install and apply shows `Result=exit-code`, exit 1,
        naming `kalshi_005_candlesticks` — expected (Decision 8). Record it
        if it happens; it is Criterion 11 on the host.

- [ ] **Task 8.2 [PM]: First supervised firing** (effort: 2)
  - [ ] `sudo mt-run kalshi` (or wait for `:20`); `mt-run follow kalshi`.
        Record the `kalshi pass finished` line showing `phases:
        catalog=ok candles=ok`, `systemctl show mt-kalshi-pass.service -p
        Result -p ExecMainStatus` (`success`, `0`), and
        `journalctl … --since -2h | grep -c 'HTTP 4'` → 0 (Criterion 14,
        and Criterion 5 on real traffic).
  - [ ] Record the phase's wall time from the journal — Decision 9 says a
        fetch pool stays a follow-up **with evidence**, and this is that
        evidence.

- [ ] **Task 8.3 [PM]: Second pass and the steady-state counts** (effort: 1)
  - [ ] Record `mt-run data kalshi status` immediately after 8.2 (the rule
        line and every candle count), then run a **second pass on demand**
        with `sudo mt-run kalshi` and record `status` again. Do not wait for
        the timer: starting the unit the timer activates proves the same
        thing and is measurable now.
  - [ ] Success (Criterion 14): between the two readings `open_lagging` is 0
        and `backlog_remaining` has fallen. Both numbers come from the two
        recorded outputs, not from a projection.
  - [ ] Record `behind_cutoff_uncollected` — it is 266's input and the
        honest "known-lost until then" number.

- [ ] **Task 8.4 [agent]: Walkthrough refresh and close** (effort: 2)
  - [ ] Replace the design's draft walkthrough expectations with the output
        actually observed in 7.4 and 8.1–8.3 (the 263 pattern), and fill the
        *Success criteria — where each is proven* table with what was seen.
  - [ ] Add a `user/notes/000-process-journal.md` entry for anything found
        that outlives this slice — in particular the measured phase wall
        time and whether Kalshi was observed revising a completed candle
        (the Risk Assessment's open question; conflict-ignore keeps the
        first version, so a revision would show as a diff on re-fetch).
  - [ ] Set `dateUpdated` on the design, the runbook, and this task file;
        set the design's `status: complete`.
  - [ ] Delegate checklist updates for this file to the `task-checker` agent.
  - [ ] **Commit**: `docs: refresh 264 walkthrough with observed output`.
