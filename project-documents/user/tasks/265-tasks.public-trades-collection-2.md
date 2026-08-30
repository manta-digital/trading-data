---
docType: tasks
slice: public-trades-collection
project: trading-data
lld: user/slices/265-slice.public-trades-collection.md
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [261, 262, 263, 264]
interfaces: [266]
projectState: >
  Part 2 of the 265 task breakdown. It begins where
  265-tasks.public-trades-collection-1.md ends: the collection rule is
  renamed to MT_KALSHI_COLLECTION_* with a loud guard, kalshi_006_trades
  creates the kalshi.trades hypertable, TradeRepository.write_page
  classifies and writes a page in one statement, and TradeSync /
  TradesPhase run as the third phase of the pass with fakes, fixtures, and
  a renderer. What remains: the status block, the end-to-end integration
  proof, the rehearsal on the test cluster, the documentation, and the
  production deploy.
reviewVerdictsAddressed:
  - 265-review.tasks.public-trades-collection.part-2, first round (claude-opus-5, FAIL) — all findings addressed
  - 265-review.tasks.public-trades-collection.part-2, second round (claude-opus-5, FAIL) — F001 and F008 by merging Tasks 5.1/5.2a into one task with no re-export; F002 the cutoff figure struck from the block (part 1 Task 2.2; Tasks 5.1, 5.4); F003 the integration tests moved to Task 5.3, before rendering; F004 Tasks 7.3–7.5 match the re-walk timings; F005 Task 9.3 names the journal block; F006 Task 8.1; F007 no action — no NFR to gate on; F009–F010 pass
  - 265-review.tasks.public-trades-collection.part-2, third round (claude-opus-5, CONCERNS) — F001 `_iso` moves to `sync_types` as `iso_utc` (Task 5.1); F002 Task 6.1 gains the mid-window abort against a real database; F003 Task 9.3 names the before-watermark source; F004–F005 no action; F006–F009 pass
dateCreated: 20260829
dateUpdated: 20260830
status: not_started
---

## Context Summary

- **This file is part 2 of 2.** Read
  `user/tasks/265-tasks.public-trades-collection-1.md` first — its Context
  Summary carries the hard rules, the gates, the branch, and the host
  boundary for the whole slice, and every section here depends on Sections
  1–4 being complete and committed.
- Source of truth remains the slice design at
  `user/slices/265-slice.public-trades-collection.md`; Success Criteria are
  cited by number.
- Section 7's rehearsal runs against a **throwaway database on the test
  cluster**, created and dropped by its exact generated name, with
  `MT_TIMESCALE_DB_URL` pointed at it for those commands only. The
  production database URL never enters the shell (CLAUDE.md, Production
  Database Protection).
- Section 9 is the Project Manager's: **[PM]** tasks run on manta9000.

## Section 5: The `status` trades block

Design *CLI and rendering*, *Technical Decision 10*, Success Criterion 11
(plus the `status`-shows-the-lag clause of Criterion 8). Criterion 10 is the
ledger preflight and belongs to Tasks 6.2 and 7.2, not here.

- [ ] **Task 5.1: `trade_status.py` — `TradeStatus` and the state fields** (effort: 3)
  - [ ] `data/kalshi/status.py` is **already 309 lines** — over the ~300-line
        guideline before this slice adds anything. Put `TradeStatus` and
        `read_trade_status` in a new `data/kalshi/trade_status.py` rather
        than growing it. **No re-export through `status.py`:** Task 5.4
        wires the one CLI call site directly to `trade_status`; a module
        whose only job is to forward a name is the complexity CLAUDE.md tells
        us to resist.
  - [ ] The new module imports neither the client nor the transport. Extend
        `test/unit/data/kalshi/test_status_imports.py` to probe
        `manta_trading.data.kalshi.trade_status` the same way it probes
        `status` (Criterion 11).
  - [ ] `read_trade_status(conn, rule) -> TradeStatus | None` — `None` until
        the phase has run once (no `sync_state['trades']` row).
  - [ ] Fields from `sync_state['trades']` alone: `last_phase_at`,
        `tape_through` (`watermark_ts`), `lag` (`now − watermark_ts`),
        `behind` (`lag > TRADE_LAG_STALE_AFTER`), `coverage_from`. **No
        `cutoff` field:** the trades cutoff is observed per run and logged,
        never persisted (part 1, Task 2.2), and the design's block has been
        corrected to match.
  - [ ] `TradeStatus.to_dict()` follows `CandleStatus.to_dict()`'s shape.
        Timestamps go through the one-line `_iso` helper that today is
        private to `status.py` (`status.py:81`, five call sites): **move it
        to `sync_types.py` as a public `iso_utc`** and point the five
        `status.py` call sites at it — not a private cross-module import,
        not a copy. `sync_types` is already the shared kalshi types module
        with no client import, so the import guard is unaffected.
  - [ ] **Nothing counts rows in `kalshi.trades`** (Decision 10, journal
        20260720). Every figure is `sync_state` plus the catalog join.
  - [ ] Success: a unit test asserts the rendered SQL text of every statement
        this module issues contains no reference to `kalshi.trades` — that
        one assertion is what enforces Decision 10, so write it here; the
        import guard is green for both modules; both are under ~300 lines.

- [ ] **Task 5.2: The four closed-market counts** (effort: 3)
  - [ ] Four counts over **selected closed markets** (`selection_sql(rule,
        "ever")`, `close_time < now()`), each exactly as the design defines
        it: `complete_through_close` (`open_time >= coverage_from AND
        close_time <= watermark`); `partial_history` (`open_time <
        coverage_from <= close_time`); `short_of_close` (`close_time >
        watermark`); `before_coverage` (`close_time < coverage_from`) —
        266's input.
  - [ ] Three of the four turn on `coverage_from` versus `open_time` /
        `close_time` ordering — this is where the risk in the whole section
        sits, which is why it is its own task and why its integration tests
        (Task 5.3) come **next**, before any rendering.
  - [ ] No `excluded_by_rule` figure here — one rule, one figure, already in
        the candle block.
  - [ ] The rule is rendered only through `selection_sql`; the counts share
        one statement over `CATALOG_JOIN` where practical.
  - [ ] Success: the four counts partition the selected closed markets — no
        market is counted twice and none is missed, asserted as a sum against
        the total in Task 5.3.

- [ ] **Task 5.3: `status` integration tests** (effort: 3)
  - [ ] Extend `test/integration/test_kalshi_status.py` following its
        `read_candle_status` cases:
  - [ ] `read_trade_status` returns `None` with no `sync_state['trades']`
        row.
  - [ ] Every field's value against a seeded state row and a seeded catalog:
        `tape_through`, `lag`, `behind` on either side of
        `TRADE_LAG_STALE_AFTER`, `coverage_from`.
  - [ ] Each of the four counts against markets deliberately straddling the
        boundaries — in particular a market **opening before and closing
        after** `coverage_from` (counts as `partial_history`, not as
        complete) and one **closing before** it (`before_coverage`).
  - [ ] The counts respect the rule: a Sports market that would otherwise be
        `complete_through_close` is in none of the four.
  - [ ] The four counts **partition** the selected closed markets: their sum
        equals the total selected closed market count, over a fixture set
        that populates all four (Task 5.2's success criterion).
  - [ ] Success: `uv run python scripts/run_tests.py integration -- -k
        kalshi_status -q` green.

- [ ] **Task 5.4: Rendering, Rich and JSON** (effort: 2)
  - [ ] `print_status` gains the trades block in the design's *Rich block*
        layout — the header line carries `last phase` only, no `cutoff`
        (Task 5.1); when `read_trade_status` returns `None` it prints
        `Trades: never collected` and the JSON payload carries
        `"trades": null`.
  - [ ] Wire `read_trade_status(conn, settings.collection_rule())` into
        `cli/commands/kalshi.py` beside `read_catalog_status` and
        `read_candle_status`, imported from `trade_status` directly.
  - [ ] Extend `test/unit/cli/commands/test_data_kalshi.py`: the Rich block
        renders every field; the `None` case renders the never-collected
        line; the JSON payload has a `trades` key that is `null` in that
        case.
  - [ ] Success: `uv run pytest test/unit -q` green.

- [ ] **Task 5.5: Section 5 gates and checkpoint commit** (effort: 1)
  - [ ] Gates as part 1's Task 1.6, scoped to the files touched.
  - [ ] Commit: `feat: add the trades block to mt data kalshi status`.

## Section 6: End-to-end integration

Design *Tests — Integration* (the last three items), Success Criteria 1, 3,
and 10.

- [ ] **Task 6.1: Three-phase pass, end to end** (effort: 3)
  - [ ] Extend `test/integration/test_kalshi_pass.py`: a full
        `CollectionPass` over `PASS_PHASES` against a small seeded catalog
        and a fake trade source, asserting the phase names and order are
        `catalog`, `candles`, `trades` (Criterion 1).
  - [ ] A **second pass immediately after** writes no new trade rows and
        reports the re-walked overlap's rows as duplicates (Criterion 3).
  - [ ] A duplicate-key check over `kalshi.trades` returns 0 rows.
  - [ ] **The mid-window abort, against a real database (Criterion 4):** the
        fake trade source raises `ProviderError` after page 2 of the first
        window; afterwards `sync_state['trades'].watermark_ts` is unchanged
        and the rows pages 1–2 wrote are present and committed. Part 1's
        Task 4.3b case 6 proves the call order against an in-memory fake;
        only this proves that the watermark write and the page writes are
        separate transactions on a real connection.
  - [ ] Success: `uv run python scripts/run_tests.py integration -- -k
        kalshi_pass -q` green.

- [ ] **Task 6.2: Preflight names the missing migration** (effort: 1)
  - [ ] Extend the existing ledger-preflight integration test: with
        `kalshi_006_trades` deleted from `schema_migrations`,
        `mt data kalshi pass` exits 1 with a message naming
        `kalshi_006_trades` (Criterion 10). This works for free through
        `TRACKS["kalshi"]` — the test proves it, it adds no code.
  - [ ] Success: the assertion names the migration id as a string the test
        reads from the migration definition, not a hardcoded literal.

- [ ] **Task 6.3: Full-tier run and checkpoint commit** (effort: 1)
  - [ ] `uv run pytest test/unit -q` and
        `uv run python scripts/run_tests.py integration -- -k kalshi -q`,
        both green. Re-run any failure in isolation before investigating
        (the tier has known concurrency flakes).
  - [ ] Gates as part 1's Task 1.6 over every file the slice touched.
  - [ ] Commit: `test: add end-to-end coverage for the three-phase pass`.

## Section 7: Rehearsal on the test cluster

Design *Verification Walkthrough* steps 1–7. Every step is **[agent]** and
runs against a throwaway database. Record observed output as you go — the
design's expected outputs are drafts to be replaced.

- [ ] **Task 7.1: Throwaway database, migrated, with a small catalog** (effort: 2)
  - [ ] Create the throwaway database on the test cluster by generated name;
        point `MT_TIMESCALE_DB_URL` at it for this section's commands only.
  - [ ] `mt data migrate apply --track kalshi` → `kalshi_006_trades` applied;
        `mt data migrate status --track kalshi` → 0 pending.
  - [ ] Confirm both hypertables report `compression_enabled` (design step 1's
        query).
  - [ ] `mt data kalshi sync --settled-since "$(date -u -d '6 hours ago'
        +%FT%TZ)"` to give the trades phase a catalog to join against.
  - [ ] Success: the two migration commands and the hypertable query print
        what step 1 predicts; capture the real output.

- [ ] **Task 7.2: Preflight and the rename, observed** (effort: 1)
  - [ ] Walkthrough step 2: delete the `kalshi_006_trades` ledger row, run
        `mt data kalshi pass`, confirm exit 1 naming the migration, re-apply
        (Criterion 10).
  - [ ] Walkthrough step 3: `MT_KALSHI_CANDLE_CATEGORIES=Sports mt data
        kalshi status` errors naming `MT_KALSHI_COLLECTION_*` and exits
        nonzero; `mt data kalshi status --json | jq .candles.rule.description`
        is unchanged by the rename (Criterion 5).
  - [ ] Success: both outputs captured verbatim for the rehearsal note.

- [ ] **Task 7.3: First pass — three phases and the floor** (effort: 3)
  - [ ] The throwaway catalog is only hours old, so seed
        `sync_state['trades']` by hand at `now − 3 hours` (both
        `watermark_ts` and `coverage_from_ts`) so the drain finishes in a few
        windows. **Record explicitly in the rehearsal note that this
        substitutes for the design's cutoff start**, which the host step
        (Task 9.2) proves instead.
  - [ ] Run `mt data kalshi pass --events-file trades-pass1.jsonl` and
        capture: the phase-start line with the cutoff and coverage floor; the
        per-window INFO lines; the unknown-prefix line; the pass-finished
        line showing `catalog=ok candles=ok trades=ok` (Criteria 1, 9).
  - [ ] Verify by `jq` on the summary that
        `fetched = written + unknown + excluded + duplicates` (Criterion 2).
  - [ ] Verify `watermark_ts` equals the catalog's `last_full_sync_at` minus
        one minute (Criterion 7), and `coverage_from_ts` is unchanged
        (Criterion 6's floor behavior).
  - [ ] Verify no Sports or Mentions trade was stored (design step 4's join
        query returns 0).
  - [ ] **Record the per-window wall time** of this first pass — the
        insert-path, uncompressed figure Task 9.3's first firing is compared
        against. It is **not** Task 7.5's baseline: a first pass inserts
        every row, a re-walk hits `ON CONFLICT DO NOTHING` on every row, and
        a difference between those two would not attribute to compression.
  - [ ] Confirm the summary reports `capped: false`, and record that **the
        cap is not exercised here**: seeding at `now − 3 hours` gives ~3
        windows ≈ 900 requests, well under `TRADE_REQUESTS_PER_PASS = 3,000`.
        Criterion 8 is proven by part 1's Task 4.3b case 7 and observed in
        production by Task 9.3.
  - [ ] Success: every assertion above holds; outputs captured.

- [ ] **Task 7.4: Second pass, duplicates, status** (effort: 2)
  - [ ] Walkthrough step 5: a second pass walks one short window, its
        `duplicates` equal the one-second overlap's rows and nothing else is
        written twice (Criteria 3, 4).
  - [ ] The self-join duplicate check over `kalshi.trades` returns 0.
  - [ ] `mt data kalshi status` prints the trades block with every field
        populated (Criterion 11); capture it.
  - [ ] **Late-arriving trades, the in-session check.** The design's Risk
        Assessment names a day-later re-walk and diff as the check for its
        third risk; that cannot be a task (it would be wait-blocked). Do the
        measurable version now: seed the watermark back over an hour already
        walked earlier in this rehearsal, re-run the pass, and diff the
        stored row count for that window before and after. A non-zero
        difference means trades became visible after their window was walked
        — record the number either way.
  - [ ] **Record that re-walk's per-window wall time.** It is the
        uncompressed **re-walk** baseline Task 7.5 compares against: the same
        conflict-only write path, so the only variable left between the two
        figures is the chunk's compression state.
  - [ ] Success: the block matches the design's layout with real numbers, and
        the re-walk diff and its wall time are recorded as numbers.

- [ ] **Task 7.5: The drain against a compressed chunk** (effort: 3)
  - [ ] Walkthrough step 6, and the measurement Criterion 12's second clause
        names. Resolve the compression job **by hypertable name** from
        `timescaledb_information.jobs`, then run it — two statements, since a
        subquery is not a valid `CALL` argument (journal 20260827).
  - [ ] Force the chunk under the watermark compressed
        (`compress_chunk` over `show_chunks`), seed the watermark back one
        hour, and re-run the pass.
  - [ ] **Record both re-walk per-window wall times** (Task 7.4's
        uncompressed re-walk and this compressed one) in the rehearsal note —
        not Task 7.3's first pass, which measured the insert path. If the
        compressed figure is materially worse, note it and the lever — pause
        the policy by hypertable name for the drain, resume after (runbook,
        never automated; the application role cannot `alter_job`).
  - [ ] Success: the two timings are recorded as numbers, not impressions.

- [ ] **Task 7.6: Write the rehearsal note and drop the database** (effort: 2)
  - [ ] Write `user/notes/2026-MM-DD-265-rehearsal.md` (real date) with every
        captured output, the **unknown-prefix listing** observed (the check
        that the unknown set really is all MVE), and the three per-window
        timings (first pass, uncompressed re-walk, compressed re-walk).
  - [ ] Record the three things the rehearsal deliberately did **not** do,
        each with its reason and where the proof lives instead:
    1. **The cutoff start** — substituted by a hand-seeded watermark at
       `now − 3 h` (Task 7.3); proven on the host by Task 9.2.
    2. **The abort inside a window** (walkthrough step 7) — proven by Task
       6.1's integration case against a real database (and part 1's Task
       4.3b cases 6 and 12 at the unit tier); the manual analogue was not
       re-run by hand.
    3. **The day-later late-arrival diff** named in the design's Risk
       Assessment — not performed, because a task cannot wait a day; the
       in-session re-walk diff (Task 7.4) is the weaker substitute, and the
       residual risk is carried by the PM's drain observation.
  - [ ] Drop the throwaway database by its exact generated name; confirm
        `MT_TIMESCALE_DB_URL` is unset from the shell.
  - [ ] Commit: `docs: record the 265 rehearsal on the test cluster`.
  - [ ] Success: the note is committed and the throwaway database is gone.

## Section 8: Documentation

Design *Runbook 100 and CHANGELOG*.

- [ ] **Task 8.1: Runbook 100, Kalshi subsection** (effort: 2)
  - [ ] Add the paragraph the design specifies: the pass has three phases;
        the collection rule governs candles **and** trades and is set by the
        `MT_KALSHI_COLLECTION_*` lines (renamed — an old `MT_KALSHI_CANDLE_*`
        line fails the pass at start, naming the new name); `kalshi_006` must
        be applied during the update; the first ~10 days after the release
        drain the live tape from the cutoff (each pass ~15 minutes, `status`
        shows the lag falling).
  - [ ] Add how to see the trades compression policy **by hypertable name**
        (never a recorded job id), and that a drain proving slow against
        compressed chunks is paused and resumed the 266 way.
  - [ ] Update the two existing `MT_KALSHI_CANDLE_*` references at runbook
        lines 131 and 415 to the new names.
  - [ ] Success, checked mechanically: `grep -n` on
        `project-documents/user/runbooks/100-production-operations.md` finds
        `kalshi_006_trades`, `MT_KALSHI_COLLECTION_`, the ~10-day drain, and
        the by-hypertable-name policy lookup, each in the Kalshi subsection;
        and every remaining `MT_KALSHI_CANDLE_` hit is inside the one
        sentence that describes the guard's failure message — no instruction
        still uses the old name.

- [ ] **Task 8.2: CHANGELOG** (effort: 1)
  - [ ] Under `[Unreleased]`: the trades phase, the status block, the
        `kalshi_006_trades` migration (a hypertable with compression), and
        the settings rename marked **Breaking** with both the old and new
        prefixes named.
  - [ ] Success: the breaking entry says exactly what an operator must change
        in `/etc/manta-trading.env`.
  - [ ] Checkpoint commit: `docs: document the trades phase and the settings
        rename`. **No ruff/mypy/pyright gates — this section edits markdown
        only**, which is why its shape differs from Tasks 5.5 and 6.3.

## Section 9: Production deploy — Project Manager

Design *Verification Walkthrough* steps 8–10, Success Criterion 13. These
run on manta9000 after PM approval of the slice, and follow runbook 100's
update procedure.

- [ ] **Task 9.1: Migrate the host** **[PM]** (effort: 2)
  - [ ] Release tagging and `install-production.sh --ref` are the PM's
        release steps under runbook 100's update procedure, **not tasks
        here** (part 1's Context Summary). This task starts at the installed
        ref.
  - [ ] **First**, before any command on the new ref: replace any
        `MT_KALSHI_CANDLE_*` lines in `/etc/manta-trading.env` with
        `MT_KALSHI_COLLECTION_*`. Unset (commented) lines need nothing; the
        example file shows the new names. This goes first because the guard
        fires at `Settings` construction for **every** command, and the
        timer may fire in the gap between install and rename.
  - [ ] `uv run mt data migrate status --track kalshi` reports 1 pending →
        `apply` with the maintenance credential → `status` reports 0 pending.
  - [ ] Success: `mt data migrate status --track kalshi` reports 0 pending
        and no `MT_KALSHI_CANDLE_` variable remains in the environment file.

- [ ] **Task 9.2: First supervised firing** **[PM]** (effort: 2)
  - [ ] `sudo mt-run kalshi` and follow it; the journal's
        `kalshi pass finished` line shows
        `phases: catalog=ok candles=ok trades=ok` and `Result=success`
        (Criterion 13, first half).
  - [ ] The `kalshi trades phase started … cutoff=… coverage_from=…
        watermark=…` journal line shows all three equal on this first run,
        the `trades window` lines walk forward from it, and
        `mt-run data kalshi status` shows `coverage from` at that cutoff and
        `tape through` ~7 hours past it with a large lag — this is the
        observation that proves the **first-run floor is the cutoff**
        (Criterion 6), which the rehearsal deliberately did not exercise.
        (`status` does not print the cutoff itself; the journal line is its
        source.)
  - [ ] Success: the two outputs above captured for the slice's completion
        record.

- [ ] **Task 9.3: Measure the first firing's deltas** **[PM]** (effort: 2)
  - [ ] Everything here is read **from the firing that just completed** — no
        bullet waits on a later one. Record each as a number in the slice's
        completion record.
  - [ ] `watermark_ts` advanced by the expected number of windows for a
        capped pass (~7 hours of tape). **Source for "before":** on this
        first run it equals `coverage_from_ts` (still in `sync_state`, never
        moved) and is printed on the `kalshi trades phase started …
        watermark=` journal line Task 9.2 captured; "after" is
        `sync_state['trades'].watermark_ts` now, also on the pass's
        `Kalshi trades` block (`watermark before → after`). The row itself
        holds only the new value once the firing is done.
  - [ ] The pass's `Kalshi trades` summary block reports `requests` at or
        just above `TRADE_REQUESTS_PER_PASS`, marked `(capped)` — the cap's
        only production observation (Criterion 8). **Source:** the unit runs
        `mt data kalshi pass` with no flags under `StandardOutput=journal`,
        so the block `print_trade_summary` writes to stdout (part 1, Task 4.6
        keeps `requests` and `capped` on one line) is in the journal:
        `journalctl -u mt-kalshi-pass.service -n 200 | grep -A4 'Kalshi
        trades'`. Neither the `kalshi pass finished` line nor the
        `trades window` lines carry these two fields.
  - [ ] `journalctl -u mt-kalshi-pass.service … | grep -c 'HTTP 429'` for
        this firing is 0 (retries never left attempt 1).
  - [ ] `before coverage` from `mt-run data kalshi status`, recorded as the
        **baseline number** — it is 266's input and should not move
        thereafter.
  - [ ] The slowest `trades window` line's wall time, compared against the
        rehearsal's first-pass timing (Task 7.3 — the same insert path,
        uncompressed) and its compressed re-walk (Task 7.5). A window taking
        minutes rather
        than seconds means the policy compressed the chunk under the
        watermark — pause it by hypertable name for the remainder of the
        drain (runbook 100), resume after.
  - [ ] Success: all five numbers recorded from a single firing.

**Handoff, not a task — the steady state.** Criterion 13's second half
(`tape through` advancing ~7 hours per firing until `behind` clears at
~10 days, then staying within two hours of now) is an observation of a
running system over days, and **no task in this file may wait on it** (part
1's hard rule; the PM has vetoed wait-blocked task items outright). It is
carried as an explicit follow-up in the slice's completion record and as a
**266 prerequisite** — 266 should not start against a tape still draining.
The mechanism it depends on is already proven without waiting: the cap and
the per-pass advance by part 1's Task 4.3b case 7 and Task 9.3 above, the
window loop by the rehearsal, and the lag figures by Task 5.3.
