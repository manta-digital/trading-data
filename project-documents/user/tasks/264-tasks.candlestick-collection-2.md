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
  CandleRepository with its selection predicate and count methods) are
  prerequisites for everything here: this file starts at the CandleSync core,
  which consumes all of them.
reviewVerdictsAddressed:
  - 264-review.tasks.candlestick-collection.part-1 (claude-opus-5, CONCERNS, F004/F005/F007 addressed)
  - 264-review.tasks.candlestick-collection.part-2 (claude-opus-5, CONCERNS, F001-F004 addressed)
dateCreated: 20260826
dateUpdated: 20260827
status: in_progress
---

## Context Summary

- Working on **264 Candlestick Collection**, part 2 of 2. Part 1 is
  `user/tasks/264-tasks.candlestick-collection-1.md`; **complete its
  Sections 1–4 first** — the core built here depends on the planner, the
  repository, the rule, the client method, and the migration.
- The context, hard rules, gates, branch, and host boundary stated in part
  1's Context Summary apply unchanged to every task below. The three that
  bite hardest in this half: the rule is rendered in exactly **one** place
  (`selection_sql`), the core issues **no SQL of its own** — every count it
  reports comes from a repository method — and `status.py` may import
  neither the client nor the transport.
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

- [x] **Task 5.1: Result and source types** (effort: 2)
  - [x] In `data/kalshi/candle_types.py` (created in Task 1.2), add
        `CandleResult` with `to_dict()` producing exactly the design's JSON
        shape (`run_id, started_at, period, cutoff, pending{live,
        finishing, backlog, backlog_remaining}, requests,
        markets_requested, markets_advanced, candles_fetched,
        candles_written, item_errors, duration_ms, error`), and
        `classify_candles(result, exc) -> SyncOutcome` mirroring
        `sync_types.classify` (storage first, then provider, `TypeError` for
        anything else, `PARTIAL` when item errors exist, else `OK`).
  - [x] `CandleSource` Protocol: `get_markets_candlesticks(...)` and
        `get_historical_cutoff()` — the two methods the core calls.
  - [x] Success: `to_dict()` round-trips through `json.dumps` (every value is
        a JSON scalar, list, or dict — datetimes rendered as ISO strings).

- [x] **Task 5.2a: `CandleSync` skeleton — cutoff, pending sets, plan** (effort: 3)
  - [x] New `data/kalshi/candle_sync.py` implementing Data Flow steps 1–4
        and 6, with the batch loop left as a single call site Task 5.2b
        fills in: read the cutoff once; build the three pending sets through
        the repository; map them through `target_window`, dropping targets
        with `start >= end`; plan batches with `plan_batches`.
  - [x] Only the backlog set is capped (`CANDLE_BACKLOG_REQUESTS_PER_PASS ×
        CANDLE_BATCH_MAX_TICKERS` rows); live and finishing are never
        capped, so a market that closed since the last pass never queues
        behind history.
  - [x] One INFO line at phase start carrying the cutoff and
        `CandleRule.describe()` — the cutoff line is the signal that 266 has
        become urgent, so log it every run whether or not anything is
        pending.
  - [x] After the batch loop returns:
        `sync_state['candlesticks'].last_full_sync_at = phase_start`,
        `.watermark_ts = cutoff`; emit `phase_finished` with the counts plus
        `backlog_remaining` and `behind_cutoff` **from the Task 4.2 count
        methods**, never from `len(backlog_rows)` — that equals the cap on
        every pass until the backlog drains.
  - [x] Sequential work on the run's single connection (Decision 9) — no
        concurrency, no connection of its own.
  - [x] Success: with an empty pending set the phase completes, writes
        `sync_state`, and emits `phase_finished` with zero counts.

- [x] **Task 5.2b: The batch loop — fetch, write, item errors** (effort: 3)
  - [x] **One transaction per batch** (Data Flow step 5): within it, insert
        every served candle with conflict-ignore, and upsert state for every
        requested ticker **present in the response — with or without
        candles** — at `watermark_ts = min(batch end, close_time + period)`
        and `coverage_from_ts = coalesce(existing, target start)`. This is
        the sparseness rule: a market that served nothing still advances, or
        an idle market would be re-requested forever (Decision 3).
  - [x] A requested ticker **absent** from the response is the one per-market
        failure the API signals: emit `item_error` with `phase="candles"`
        and the reason `"not served by the batch endpoint"`, leave its state
        untouched, and continue (Decision 7).
  - [x] A `ProviderError` on a batch **aborts the phase** — the planner
        guarantees the caps, so a 400 here is our bug or an API change and
        must be visible (Decision 7). Do not catch it inside the batch loop.
  - [x] One INFO line per `CANDLE_PROGRESS_EVERY_REQUESTS` requests.
  - [x] Success: the module keeps to the ~300-line guideline; no `psycopg`
        import beyond the exception types it must catch, no client import.

- [x] **Task 5.3a: Candle test doubles** (effort: 2)
  - [x] Extend `test/kalshi_support/fake_source.py` with candlestick
        support: a `FakeCandleSource` (or candle methods on the existing
        fake) serving scripted candles per ticker and **recording every
        query it receives** — the recorded queries are what proves the rule
        selects exactly what it should at the unit level (Criterion 2).
  - [x] Add candle methods to `test/kalshi_support/fake_repository.py`
        mirroring the real repository's method set — **including the two
        count methods** — and following its existing `fail_on(method, exc,
        at=)` pattern so a storage abort can be scripted.
  - [x] Success: the doubles import cleanly and existing kalshi unit tests
        still pass.
  - [x] **Commit**: `test: add kalshi candle test doubles`.

- [x] **Task 5.3b: Core unit tests** (effort: 3)
  - [x] New `test/unit/data/kalshi/test_candle_sync.py`: the three pending
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
  - [x] Success: unit tier passes; no network and no database.

- [x] **Task 5.4: `CandlesPhase`, `PASS_PHASES`, renderer dispatch** (effort: 3)
  - [x] `collection_pass.py`: `PassPhaseName.CANDLES = "candles"`;
        `CandlesPhase` with the **same body shape as `CatalogPhase`** — lazy
        in-method imports, `time.monotonic()` around the run, exactly two
        `except` clauses (`ProviderError`, `psycopg.OperationalError` with
        `logger.exception`), and a `PhaseReport` built from
        `classify_candles`. Append it: `PASS_PHASES = (CatalogPhase(),
        CandlesPhase())`.
  - [x] **`kalshi_render.py` renderer dispatch — this is a required fix, not
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
  - [x] A phase name with no registered renderer must fail loudly (a named
        error), not print nothing — silently skipping a phase's summary is
        how 265 would ship invisible.
  - [x] No change to `kalshi.py`'s `run_pass`, the `pass` command surface,
        the exit-code map, or any unit file.
  - [x] Success: `mt data kalshi pass --json` reports two phases in order.

- [x] **Task 5.5: Pass and rendering unit tests** (effort: 2)
  - [x] Extend `test/unit/data/kalshi/test_collection_pass.py`:
        `PASS_PHASES` is exactly `(CatalogPhase(), CandlesPhase())` by name
        and order (Criterion 1); a catalog abort leaves the candle phase
        `skipped`.
  - [x] **Criterion 1's third clause, which nothing else asserts:** a pass
        whose *candle* phase aborts still reports the catalog phase's
        original outcome and leaves `sync_state['catalog']` unchanged. 263's
        `CollectionPass` very likely already guarantees this, but the
        criterion is restated in this slice and 265 copies the contract, so
        assert it rather than inherit it.
  - [x] **`CandleResult.to_dict()` survives `json.dumps`** with the design's
        exact key set — including a **non-empty `item_errors`** and a
        **non-null `cutoff`**, the two places a `datetime` most easily
        leaks. Follow the existing precedent
        (`test_collection_pass.py::test_to_dict_round_trips_through_json`).
        Task 5.1 states this as a success condition; without a committed
        test it can be satisfied by a one-off check and the phase summary
        then reaches `--json` unguarded.
  - [x] New or extended CLI unit test: the renderer dispatch selects
        `print_candle_summary` for a candles report and
        `print_phase_summary` for a catalog report; **a pass result carrying
        both summaries renders without raising** (the regression this
        section fixes); an unregistered phase name raises the named error.
  - [x] Success: `uv run pytest test/unit -q` passes.
  - [x] **Commit**: `feat: add kalshi candle phase and per-phase summary rendering`.

## Section 6: `status` — the candle block

Design *CLI and rendering* (Decision 11), and Criterion 12 — `status`
answers the candle clause from the database alone. Every field is a
persisted fact; nothing here counts rows in `kalshi.candlesticks`.

- [x] **Task 6.1: `CandleStatus` and `read_candle_status`** (effort: 3)
  - [x] In `data/kalshi/status.py`, add the frozen `CandleStatus` with the
        design's fields (`period_minutes`, `last_phase_at`,
        `cutoff_observed`, `rule`, `selected_open`, `markets_tracked`,
        `open_lagging`, `open_oldest_watermark`, `complete_through_close`,
        `closed_short_of_close`, `backlog_remaining`,
        `behind_cutoff_uncollected`, `closed_excluded_by_rule`,
        `partial_history`) and its `to_dict()`.
  - [x] `read_candle_status(conn, rule) -> CandleStatus | None` — synchronous
        psycopg like `read_catalog_status`; returns `None` until the phase
        has run once (no `sync_state` row for `Surface.CANDLESTICKS`).
  - [x] The cutoff comes from `sync_state['candlesticks'].watermark_ts`, not
        from the API — `status` must make no network call (Decision 11).
  - [x] Every rule-dependent count calls `selection_sql` — do not re-spell
        the predicate here (Criterion 2's "collection and reporting cannot
        disagree"). `open_lagging` counts only markets the rule **still
        selects** and whose watermark is older than `now −
        CANDLE_LAG_STALE_AFTER`: a deselected market is idle, not lagging.
  - [x] Success: `status.py` imports neither the client nor the transport
        (Criterion 12).

- [x] **Task 6.2: Wire the block into the CLI** (effort: 2)
  - [x] `cli/commands/kalshi.py`'s `status` command reads the candle status
        alongside the catalog one, passing `settings.candle_rule()` so the
        printed rule is the one in force.
  - [x] `kalshi_render.py::print_status` gains the candle block in the
        design's layout; `None` prints `Candlesticks: never collected`.
  - [x] `--json` nests the block under `candles`, `null` when never
        collected. The catalog keys keep their current position and spelling
        so existing consumers are unaffected.
  - [x] Success: both output modes render on a database where the phase has
        never run and on one where it has.

- [x] **Task 6.3: Status tests** (effort: 3)
  - [x] Unit: an import-boundary test asserting `status.py`'s module
        imports exclude the client and transport modules (Criterion 12) —
        assert on the imported module graph, not on source text.
  - [x] Extend `test/integration/test_kalshi_status.py` on `kalshi_db`:
        `read_candle_status` is `None` before the first phase run; after
        seeding `sync_state`, `market_candle_state`, and the predicate
        fixture set, **every field** in the design's list has the expected
        value — including `closed_excluded_by_rule` non-zero and
        `behind_cutoff_uncollected` counting a market finalized before the
        cutoff; changing the rule changes `selected_open` and
        `closed_excluded_by_rule` without any collection happening.
  - [x] Success: kalshi integration set passes.
  - [x] **Commit**: `feat: add candle block to mt data kalshi status`.

## Section 7: End-to-end integration, docs, and the rehearsal

- [x] **Task 7.1: End-to-end pass integration test** (effort: 3)
  - [x] Extend `test/integration/test_kalshi_pass.py` on `kalshi_db` with a
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
  - [x] Success: the kalshi integration set passes end to end.

- [x] **Task 7.2: Compression proven on a real chunk** (effort: 3)
  - [x] Integration test (Criterion 13): insert candles old enough to fall
        outside `KALSHI_CANDLE_COMPRESS_AFTER`, run the compression job by
        hand (`CALL run_job(...)`), and assert
        `chunk_compression_stats('kalshi.candlesticks')` reports the chunk
        `Compressed` and that a per-market query over it returns **the same
        rows as before compression**.
  - [x] Resolve the job by hypertable name and `proc_name` at use time,
        never by a job ID — job IDs regenerate and a recorded one goes
        stale.
  - [x] Success: the test is self-contained (it creates the old rows it
        needs) and leaves no policy disabled.

- [x] **Task 7.3: Full gate pass** (effort: 1)
  - [x] `uv run pytest test/unit -q`; `uv run python scripts/run_tests.py
        integration -- -k kalshi -q`; `uv run ruff check` and `uv run ruff
        format --check` **scoped to the files touched** (263 process note);
        `uv run --extra dev mypy` and `npx --yes pyright` on the kalshi
        source paths plus the new tests.
  - [x] Success: all clean; no new dependency in `pyproject.toml`.
  - [x] **Commit**: `test: add kalshi candle end-to-end and compression coverage`.

- [x] **Task 7.4: Rehearsal on a throwaway database** (effort: 3)
  - [x] Walkthrough steps 1–5 exactly, on a **throwaway database on the test
        cluster** — the shell's `MT_TIMESCALE_DB_URL` points at it and the
        production URL never enters the shell. Use `sync --settled-since` at
        about six hours to keep the catalog small.
  - [x] Record: the migrate/hypertable check (step 1); the preflight
        failure naming `kalshi_005_candlesticks` and its recovery (step 2,
        Criterion 11); the rule inspection **including the full list of
        series the exclusion patterns match** — read that list, it is the
        check that the patterns neither over- nor under-reach (step 3); the
        env-override showing `selected_open` moving with no collection
        (Criterion 2); the first pass with its phase lines and counts (step
        4); the second pass, the duplicate check, the `status` block in both
        modes, and the compression job run (step 5).
  - [x] Also record from step 4 the **partial/complete counts** — the
        `count(*) filter (where coverage_from_ts > open_time)` and
        `watermark_ts >= close_time + interval` query — which is where
        first-sight semantics are proven on real data (Criterion 6).
  - [x] Record the **pending queries' wall time** with `\timing on`. The
        design's *Special Considerations* asks for it: each pass joins
        `markets` (3.5 M+ rows) to `events` and `series` once per pending
        set, and if the backlog query dominates, a partial index on
        `markets (settlement_ts) WHERE status = 'finalized'` is the named
        first lever. Task 8.2's phase wall time is the Decision 9 evidence
        about concurrency; this is the separate per-query evidence.
  - [x] Write it all into `user/notes/2026-MM-DD-264-rehearsal.md` (the date
        run), with the matched-series listing pasted in full.
  - [x] Success: two passes exit 0; zero HTTP 400 on `/markets/candlesticks`
        (Criterion 5); the duplicate query returns 0 rows (Criterion 4).
  - [x] **Commit**: `docs: record 264 throwaway-database rehearsal`.

- [x] **Task 7.5: Documentation** (effort: 2)
  - [x] `deploy/manta-trading.env.example`: five commented `MT_KALSHI_CANDLE_*`
        lines under the existing optional-tuning block, showing the
        defaults, in the file's established comment style.
  - [x] Runbook 100, Kalshi subsection: one paragraph covering the pass now
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
  - [x] `CHANGELOG.md` under `[Unreleased]`: the candle phase and the rule,
        the status block, the migration (hypertable + compression policy),
        and the preflight change.
  - [x] Success: the runbook paragraph names no job ID and no wall-clock
        promise the collector does not make.
  - [x] **Commit**: `docs: document the kalshi candle phase and collection rule`.

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

## Task review disposition (20260826)

Review: `user/reviews/264-review.tasks.candlestick-collection.part-2.md`,
claude-opus-5, verdict **CONCERNS** against `1abefcd` (two concerns, two
notes, four passes). CONCERNS passes the gate. Both concerns and both notes
are fixed in place. Part 1's F004 and F005 also landed here, since the tasks
they name live in this file.

- **F001 (concern) — Section 5 carried 17 effort points to one commit, and
  Tasks 5.2/5.3 were out of family.** Valid: they were the only effort-5
  tasks in either file, and 5.2 encoded eight behaviors behind a single
  checkbox, so partial completion was indistinguishable from completion.
  Fixed by the split both reviews proposed, at the batch boundary: **5.2a**
  (cutoff, pending sets, target mapping, planning, terminal state and
  events) and **5.2b** (the batch loop: transaction, conflict-ignore insert,
  advance-on-empty, omitted-ticker item error, provider abort, progress
  cadence); **5.3a** (test doubles, with its own commit) and **5.3b** (the
  assertions). Section 5 now has an interim checkpoint, and no task in
  either file exceeds effort 3.
- **F002 (concern) — the `CandleResult` JSON round-trip had no owning
  task.** Valid: it appeared only as a Success bullet on an implementation
  task, which a one-off REPL check satisfies while leaving nothing
  committed. Fixed: Task 5.5 now requires the assertion with the design's
  exact key set, a non-empty `item_errors`, and a non-null `cutoff` — the
  two places a `datetime` most easily leaks into `--json`.
- **F003 (note) / part 1 F004 (concern) — Criterion 1's third clause was
  never asserted.** The two reviews rated this differently; treated as the
  concern. Fixed: Task 5.5 asserts that a pass whose *candle* phase aborts
  still reports the catalog phase's original outcome and leaves
  `sync_state['catalog']` unchanged. 263's `CollectionPass` very likely
  already guarantees it, but the criterion is restated in this slice and 265
  copies the contract, so it is asserted rather than inherited.
- **F004 (note) — Task 7.4 did not name the step-4 query proving Criterion
  6.** Fixed: the rehearsal record list now names the partial/complete
  counts explicitly.
- F005 (pass) — the renderer-dispatch fix was independently verified against
  `kalshi_render.py`; no action.
- F006, F007, F008 (pass): no action.

### Not adopted

- The reviews suggested `IS DISTINCT FROM ALL` for part 1's NULL fix. It is
  not valid PostgreSQL syntax (checked on the test cluster); the tasks
  specify `COALESCE(...) <> ALL(...)`, which was measured to return `true`
  on a NULL operand.
- **The ~450-line file guideline, deliberately.** The fixes pushed the two
  files to 584 and 437 lines. Splitting into three was tried and reverted on
  PM direction (20260826): the task files are reviewed one-to-one against
  `264-review.tasks.…part-1` and `…part-2`, and a third file would break
  that correspondence — a reviewer would have no source document to hold it
  against, and every finding location in the two existing reviews would stop
  resolving. The 1:1 between a task file and its review outranks the length
  guideline here. Section numbering and task ids are unchanged from the
  reviewed revision `1abefcd`.
