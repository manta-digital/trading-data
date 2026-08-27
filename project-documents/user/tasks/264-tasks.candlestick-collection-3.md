---
docType: tasks
slice: candlestick-collection
project: trading-data
lld: user/slices/264-slice.candlestick-collection.md
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [261, 262, 263]
interfaces: [265, 266]
projectState: >
  Part 3 of 3. Parts 1 and 2 deliver the constants and collection rule, the
  batch client, migration kalshi_005, the planner, CandleRepository, and the
  CandleSync core with CandlesPhase appended to PASS_PHASES. What remains is
  the operator-facing half: the status candle block, end-to-end and
  compression integration tests, the throwaway-database rehearsal, the
  documentation, and the supervised host steps.
reviewVerdictsAddressed:
  - 264-review.tasks.candlestick-collection.part-1 (claude-opus-5, CONCERNS, F007 addressed)
  - 264-review.tasks.candlestick-collection.part-2 (claude-opus-5, CONCERNS, F004 addressed)
dateCreated: 20260826
dateUpdated: 20260826
status: not_started
---

## Context Summary

- Working on **264 Candlestick Collection**, part 3 of 3 — `status`,
  verification, documentation, and deployment. **Complete parts 1 and 2
  first**; every task here reads state the core writes.
- The context, hard rules, gates, branch, and host boundary in **part 1's
  Context Summary** apply unchanged. The one that bites hardest here:
  `status.py` imports neither the client nor the transport, and every candle
  figure comes from persisted state — nothing counts rows in
  `kalshi.candlesticks`.
- Source of truth remains `user/slices/264-slice.candlestick-collection.md`.
  Section 7's rehearsal follows its **Verification Walkthrough** steps 1–5;
  Section 8 is steps 6–8 and is the Project Manager's.
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
  - [ ] Also record from step 4 the **partial/complete counts** — the
        `count(*) filter (where coverage_from_ts > open_time)` and
        `watermark_ts >= close_time + interval` query — which is where
        first-sight semantics are proven on real data (Criterion 6).
  - [ ] Record the **pending queries' wall time** with `\timing on`. The
        design's *Special Considerations* asks for it: each pass joins
        `markets` (3.5 M+ rows) to `events` and `series` once per pending
        set, and if the backlog query dominates, a partial index on
        `markets (settlement_ts) WHERE status = 'finalized'` is the named
        first lever. Task 8.2's phase wall time is the Decision 9 evidence
        about concurrency; this is the separate per-query evidence.
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
- Nothing else. One structural change the reviews did not ask for: the fixes
  pushed the two files to 575 and 438 lines against a ~450 guideline, past
  the ~100-line overrun the guide tolerates, so the breakdown is now **three
  files** — vocabulary/schema/planner, repository/core, then
  status/verification/deploy. Section numbering and task ids are unchanged,
  so the review's finding locations still resolve.
