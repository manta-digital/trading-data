---
docType: tasks
slice: daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease
project: trading-data
lld: user/slices/170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md
dependencies: [166]
projectState: >
  Release 0.7.7 is live on PyPI; main is clean. Slice 170's design passed
  review (all findings PASS, verdict PASS). daily_ohlcv on prod is a ~4.4 GB
  compressed hypertable with 3,371 seven-day chunks over ~34.7 M rows;
  SELECT MAX(time) exceeds 30 s and a 31k-symbol ANY aggregate cannot finish
  planning in 120 s — the same pathology slice 166 fixed on minute_ohlcv.
  The `mt data rechunk` Option-D driver exists and is production-proven at
  7.27 B rows, but is hardcoded to the minute family. TimescaleDB 2.23.0 /
  PostgreSQL 17.7. Latest migration id is 049; this slice adds 050.
  Slice 169 (coverage-cagg refresh repair) is sequenced immediately after.
dateCreated: 20260809
dateUpdated: 20260811
status: complete
---

## Context Summary

- Working on the **170 — `daily_ohlcv` rechunk** slice: the last table
  carrying the over-chunking pathology fixed for `minute_ohlcv` (166) and
  the minute caggs (163).
- **The mechanism is already built and proven.** Option D (per-window stage →
  `drop_chunks` → reinsert → compress, one transaction per window) lives in
  `src/manta_trading/market/maintenance/rechunk.py` and rewrote 7.27 B rows
  on prod with zero errors. This slice does **not** redesign or re-rehearse
  it — it parameterizes the driver by target table and runs it once more, at
  ~210× smaller scale.
- **Phase B is pure code and is safe to do without prod access.** Only
  Phase C touches production, behind a PM gate.
- Key constraints (design + standing project guidance):
  - Chunk interval defined **once** as `DAILY_OHLCV_CHUNK_INTERVAL`; no
    scattered `'70 days'` literals (project rule; 166/163 precedent).
  - **No magic strings in dispatch** — the CLI `--table` value routes through
    a `RechunkTarget` StrEnum and a registry, never a bare string compare.
  - The minute path must be **bit-identical** after the refactor
    (Success Criterion 7); its default invocation `mt data rechunk` is
    unchanged.
  - **Pause only the daily family.** Runbook R1 holds: minute jobs — job 1003
    especially — stay running, or the daemon re-seed loop reopens.
  - A concurrent cagg refresh during chunk restructuring **silently and
    permanently loses materialized rows** (166 A5-Q3). The pre-flight
    refusing to run while daily-family jobs are scheduled is
    correctness-critical, not a nicety.
  - Prod query discipline: always `SET statement_timeout`; never run an
    unbounded expression aggregate over a compressed hypertable.
  - Destructive statements target prod only under the PM-gated maintenance
    window; cold-start checks use a throwaway database that this process
    creates and drops.
- **Grid nesting is the property that makes this safe:** 70 = 10 × 7, so
  existing 7-day chunks nest exactly inside the epoch-aligned 70-day grid.
  Every target window contains only whole chunks and yields exactly one chunk.
- Next slice after 170: **169** (coverage-cagg refresh repair), which must run
  after this rechunk.

---

## Phase B — Code, Constants, and Migration (no production access)

### B1. Add the `DAILY_OHLCV_CHUNK_INTERVAL` constant

- [x] B1.1 Add `DAILY_OHLCV_CHUNK_INTERVAL: timedelta = timedelta(days=70)`
      to `src/manta_trading/constants.py`, placed beside
      `MINUTE_OHLCV_CHUNK_INTERVAL`.
- [x] B1.2 Write its docstring to record *why* 70 days, not just what: the
      wall-clock rule (span ÷ target chunk count, never data volume;
      22.6 years ÷ 70 days ≈ 118 chunks), the 70 = 10 × 7 grid-nesting
      property, and the note that migration 001c (creation), migration 050,
      and the rechunk registry all derive from it — never restate the value.
- [x] B1.3 Reword the `MINUTE_OHLCV_CHUNK_INTERVAL` and
      `MINUTE_CAGG_CHUNK_INTERVAL` docstrings where they cite `daily_ohlcv`
      or the `daily_*` caggs at their pre-170 intervals as reference points,
      so the constants file stops teaching superseded values.
- **Success:** `DAILY_OHLCV_CHUNK_INTERVAL` exists with a rationale docstring;
  `grep -rn "70 days\|days=70" src/` shows no new literal outside the
  constant's own definition; no minute docstring cites a stale daily interval.
- **Effort:** 1

### B2. Generalize the rechunk driver to a target registry

- [x] B2.1 Add a `RechunkTarget` `StrEnum` (`MINUTE = "minute"`,
      `DAILY = "daily"`) to `market/maintenance/rechunk.py`.
- [x] B2.2 Add a frozen dataclass describing one target — hypertable name,
      chunk interval, dependent cagg views, and the migration id the
      pre-flight names in its error message — plus a registry dict keyed by
      `RechunkTarget`. Populate `MINUTE` from the existing values
      (`MINUTE_OHLCV_TABLE`, `MINUTE_OHLCV_CHUNK_INTERVAL`, the four minute
      cagg views via `GRANULARITY_SOURCE`, migration id `043...`) and `DAILY`
      from `daily_ohlcv`, `DAILY_OHLCV_CHUNK_INTERVAL`, the four daily cagg
      views, and migration id `050...`.
- [x] B2.3 Change `run_rechunk` to accept `target: RechunkTarget =
      RechunkTarget.MINUTE` and read table/interval/cagg views from the
      registry instead of the module-level `RECHUNK_TABLE` constant and the
      hardcoded `interval = MINUTE_OHLCV_CHUNK_INTERVAL`. Keep the existing
      `table` / `cagg_views` / `max_windows` / `after_stage` parameters as
      test seams (the integration tests depend on them), with the registry
      supplying their defaults.
- [x] B2.4 Thread the target's migration id into
      `_assert_dimension_interval`'s `PreflightError` message so a daily
      pre-flight failure names migration 050, not 043.
- [x] B2.5 Leave window classification, the EXCLUSIVE-before-stage lock, the
      staged==reinserted guard, `SKIP_UNCOMPRESSED` handling, and
      resumability **untouched**. If a change to any of these seems
      necessary, stop and raise it — it means the refactor has exceeded its
      scope.
- [x] B2.6 Commit: the constant (B1) and the registry refactor land together
      — the registry references `DAILY_OHLCV_CHUNK_INTERVAL`, so they cannot
      be split without leaving a broken commit. This lands before the
      migration and CLI work so a bisect can isolate the refactor.
- **Success:** the driver is table-agnostic; no `MINUTE_*` constant is
  referenced outside the registry's `MINUTE` entry; dispatch is by enum, not
  string comparison.
- **Effort:** 2

### B3. Test the registry and the preserved minute behavior

- [x] B3.1 In `test/unit/market/test_rechunk.py`, add unit tests: each
      `RechunkTarget` resolves to the expected table, interval, and cagg-view
      tuple; the registry covers every enum member (guards against adding an
      enum value without a registry entry).
- [x] B3.2 Add a unit test that `_load_windows` groups on a 70-day grid
      correctly, including the nesting property — a set of 7-day chunk rows
      spanning one 70-day window groups into exactly one window, and a window
      boundary lands where `_window_start` says it does.
- [x] B3.3 Add a test asserting the daily pre-flight error message names
      migration 050 and the minute one still names 043.
- [x] B3.4 Confirm the existing minute-path unit tests still pass unmodified.
      **If any test required editing to pass, HALT and escalate to the PM
      before proceeding to B4** — a required edit is evidence the refactor
      changed minute behavior, which Success Criterion 7 forbids. Do not
      "fix" the test to match new behavior; that converts the regression
      guard into a rubber stamp. Note precisely which test and why.
- **Success:** `uv run pytest test/unit/market/test_rechunk.py` passes; the
  pre-existing minute assertions are untouched, with zero edits to them.
- **Effort:** 2

### B4. Add migration 050 and update the creation migration

- [x] B4.1 Add migration `050_daily_chunk_interval_70d` to
      `src/manta_trading/market/schema/migrations/minute.py`, following the
      shape of `043_minute_chunk_interval_7d`: a single
      `set_chunk_time_interval('daily_ohlcv', ...)` rendering
      `DAILY_OHLCV_CHUNK_INTERVAL` through the existing
      `_interval_seconds_sql` helper.
- [x] B4.2 Write the migration description to state that it affects **future
      chunks only**, that existing 7-day chunks are rewritten by
      `mt data rechunk --table daily`, that it is idempotent, and how to
      revert manually — matching 043's description convention.
- [x] B4.3 Update the slice-143 creation migration (the `create_hypertable`
      call for `daily_ohlcv`, currently `chunk_time_interval => INTERVAL
      '7 days'`) to render `DAILY_OHLCV_CHUNK_INTERVAL`, so a cold start
      creates 70-day chunks directly.
- [x] B4.4 Update the same migration's description text, which currently
      states `chunk_time_interval = 7 days`.
- [x] B4.5 Commit: migration 050 plus the creation-migration update land as
      the schema-definition commit.
- **Success:** migration list ends at 050; both call sites derive from the
  constant; no `INTERVAL '7 days'` literal remains for `daily_ohlcv`.
- **Effort:** 1

### B5. Test the migration and cold-start interval

- [x] B5.1 Add or extend a migration test asserting 050 is present, is
      ordered last, and renders the interval from the constant (not a
      literal) — following whatever pattern the existing migration tests use
      for 043/044.
- [x] B5.2 Add a cold-start assertion covering Success Criterion 6: after
      applying the full chain to a throwaway database,
      `timescaledb_information.dimensions` reports 70 days for `daily_ohlcv`.
      Use the existing throwaway-DB fixture; **verify that fixture contains
      no TRUNCATE/DELETE against a configured production URL before running
      it** (2026-08-04 incident).
- [x] B5.3 Re-apply the chain twice against the throwaway DB to confirm 050
      is idempotent.
- **Success:** tests pass; the throwaway database is created and dropped by
  the test itself and no production URL is read.
- **Effort:** 2

### B6. Expose `--table` on the CLI

- [x] B6.1 Add a `--table` option to `data_rechunk` in
      `src/manta_trading/cli/commands/data.py`, typed as `RechunkTarget` so
      Typer validates the value and rejects anything else, defaulting to
      `RechunkTarget.MINUTE`.
- [x] B6.2 Pass the target through to `run_rechunk`. Leave the existing
      exit-code contract (0 / 1 / 2) and the dry-run vs maintenance-role
      connection split unchanged.
- [x] B6.3 Update the command's docstring: it currently describes only the
      minute rewrite. State both targets, keep the operator warning about
      stopping the daemon, and make the ~118-window daily expectation
      explicit alongside the minute ~1,175.
- **Success:** `mt data rechunk --help` lists `--table` with both choices;
  `mt data rechunk --dry-run` (no `--table`) still plans the minute table.
- **Effort:** 1

### B7. Test the CLI surface

- [x] B7.1 Add CLI tests: default invocation targets minute; `--table daily`
      targets daily; an invalid `--table` value exits non-zero without
      touching the database.
- [x] B7.2 Assert the pre-flight and rechunk failure exit codes are unchanged
      for both targets.
- **Success:** CLI tests pass; no existing rechunk CLI test needed edits.
- **Effort:** 1

### B8. Integration test on a daily-shaped scratch hypertable

- [x] B8.1 Extend `test/integration/test_rechunk_driver.py` with a
      daily-shaped scratch table: 7-day chunks over a span covering at least
      two 70-day grid windows, weekday-only rows so empty ranges are faithful,
      everything compressed except a trailing region that exercises
      `SKIP_UNCOMPRESSED`, and one attached cagg with a refresh policy.
- [x] B8.2 Assert the full cycle on that table: dry run mutates nothing; a
      real run collapses each grid window to exactly one chunk with zero row
      loss; the attached cagg's contents are unchanged; a re-run is a no-op.
- [x] B8.3 Assert the pre-flight refuses while the scratch cagg's refresh
      policy is still scheduled.
- [x] B8.4 Keep using the driver's table/cagg parameters as seams — the test
      must never touch real `daily_ohlcv` or `minute_ohlcv`.
- [x] B8.5 Commit: the integration test lands once it passes, so the proven
      daily-shaped cycle is its own point in history.
- **Success:** integration tests pass against a TimescaleDB instance and are
  skipped cleanly when `MT_TIMESCALE_DB_URL` is unset.
- **Effort:** 3

### B9. Phase B close-out

- [x] B9.1 Run the full unit + integration tiers; confirm no regression
      beyond the two known-unrelated `test_cli_lists.py` failures.
- [x] B9.2 Run `ruff` and `mypy` on the touched files; do not increase the
      existing baseline counts.
- [x] B9.3 Commit any remaining work (B6/B7 CLI, docstring sweep) and confirm
      the branch history reads as distinct units — registry refactor,
      schema definition, integration test, CLI — rather than one bulk commit.
      All work is on the slice branch
      `170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease`,
      forked from `main` (`git.integration_branch` is unset).
- **Success:** branch builds clean; Phase C can proceed on a PM go.
- **Effort:** 1

---

## Phase C — Production Execution (PM-gated maintenance window)

> **STOP — do not begin Phase C without explicit PM authorization.** Every
> task below mutates production. The daemon must be stopped and a backup
> point confirmed first.

### C1. Pre-flight gate

- [x] C1.1 Confirm with the PM: backup/snapshot point taken, and go-ahead for
      the maintenance window.
- [x] C1.2 Stop the data daemon. Verify it is actually stopped —
      `acquisition_state` quiescent and no fresh `daemon_heartbeat` — rather
      than assuming; there is no `status` subcommand.
- [x] C1.3 Confirm no `mt data pull` or gap-seeding process is running.
- **Success:** the PM has authorized the window and no writer is active.
- **Effort:** 1

### C2. Apply migration 050 and inspect the plan

- [x] C2.1 Apply the migration to prod (`mt data migrate apply`).
- [x] C2.2 Verify `timescaledb_information.dimensions` reports 70 days for
      `daily_ohlcv`.
- [x] C2.3 Run `mt data rechunk --table daily --dry-run` and read the plan:
      expect ~118 windows, with trailing uncompressed windows listed as
      skips. If the window count is wildly off, **stop** — it means the grid
      assumption is wrong, and that is evidence to bring back to the design,
      not something to push through.
- **Success:** dimension shows 70 days; the dry-run plan is consistent with
  the design's ~118-window expectation.
- **Effort:** 1

### C3. Capture pre-rewrite baselines

> **Every query in this task runs against production.** Set
> `statement_timeout` on each one — not just the first — and cancel the
> backend if a client-side timeout fires, or the query keeps running on the
> server. An unbounded expression aggregate over a compressed hypertable
> decompresses everything and has crashed this server before (2026-07-20).

- [x] C3.1 With `statement_timeout` set, capture exact
      `count(*) FROM daily_ohlcv`.
- [x] C3.2 Capture per-cagg totals for `daily_weekly_ohlcv`,
      `daily_monthly_ohlcv`, `daily_quarterly_ohlcv`, and `daily_coverage`.
- [x] C3.3 Capture bounded `count(*)` / `MIN(time)` / `MAX(time)` for at least
      3 sampled symbols over fixed windows — bind timestamps as `timestamptz`,
      not dates, or chunk exclusion is defeated.
- [x] C3.4 Record the current chunk count (3,371 expected) and the "before"
      timings for `SELECT MAX(time)` and the 31k-symbol `ANY` EXPLAIN.
- [x] C3.5 Write all baselines to a notes file under `user/notes/` so the
      after-comparison is against a recorded artifact, not memory.
- **Success:** every Phase D comparison has a captured "before" value.
- **Effort:** 2

### C4. Pause daily-family jobs

- [x] C4.1 Resolve job IDs from the catalog at runtime — never trust the
      runbook's table. Target `daily_ohlcv`'s columnstore policy, the four
      dependent caggs' refresh policies, and any columnstore policies on
      those caggs' mat hypertables.
- [x] C4.2 Pause them via `alter_job(..., scheduled => false)`.
      `daily_coverage` is not hierarchical, so plain `alter_job` works — the
      R2b catalog-update workaround applies only to `minute_coverage`.
- [x] C4.3 **Verify the minute-family jobs are still scheduled**, job 1003
      especially (runbook R1). Record the pause start time; R2's catch-up
      depends on it.
- **Success:** exactly the daily-family jobs are unscheduled; no minute job
  was touched.
- **Effort:** 1

### C5. Run the rechunk

- [x] C5.1 Run `mt data rechunk --table daily` with output captured to a log.
- [x] C5.2 Monitor progress per window. The run is expected to be short
      relative to 166's; if a window fails, the driver stops with that window
      identified and the table is left valid — report the failing window
      rather than retrying blindly.
- [x] C5.3 On completion, confirm exit 0 and that every window reported the
      staged==reinserted guard passing.
- [x] C5.4 Checkpoint before verification: commit the run log and the actual
      window/chunk counts into the C3.5 notes file. The rechunk itself mutates
      the database, not the repository — this checkpoint exists so the
      execution record survives independently of whatever Phase D finds.
- **Success:** exit 0; chunk count drops to low hundreds; log retained and
  committed.
- **Effort:** 2

### C6. Resume jobs and force-refresh the caggs

- [x] C6.1 Resume every paused job via `alter_job(..., scheduled => true)`.
- [x] C6.2 Force-refresh the three rollup caggs over their full span with
      `force => true` — their policies look back at most 270 days and a
      scheduled run can never heal history (the 163 lesson).
- [x] C6.3 Force-refresh `daily_coverage` using the R2a form: NULL bounds
      (365-day buckets reject narrow windows) with `force => true`.
- [x] C6.4 Run `ANALYZE daily_ohlcv` and re-check
      `approximate_row_count('daily_ohlcv')` for sanity against the exact
      count from C3.1.
- [x] C6.5 Confirm zero jobs left unscheduled (runbook R4) and that resumed
      jobs report `last_run_status = 'Success'` on their next runs.
- **Success:** all caggs re-materialized; no job left paused.
- **Effort:** 2

---

## Phase D — Verification and Close-out

### D1. Verify performance and chunk health

> **Prod query discipline applies to every measurement below**, same as C3:
> `statement_timeout` on each query, cancel the backend on a client timeout.
> Compare each result against the C3.4 baseline rather than against the
> design's expected value — the baseline is what proves the change.

- [x] D1.1 `SELECT MAX(time) FROM daily_ohlcv` returns sub-second
      (Criterion 2).
- [x] D1.2 Chunk count is low hundreds, ~120 plus trailing skips
      (Criterion 1).
- [x] D1.3 The 31k-symbol `ANY` aggregate `EXPLAIN` (plan-only) completes in
      seconds, not minutes (Criterion 3).
- **Success:** all three measured and recorded against their C3 baselines.
- **Effort:** 1

### D2. Verify data integrity

- [x] D2.1 Total `count(*)` is identical to the C3.1 baseline (Criterion 4).
- [x] D2.2 Each sampled symbol's bounded `count(*)` / `MIN` / `MAX` matches
      its C3.3 baseline exactly.
- [x] D2.3 If any comparison differs, **stop and escalate** — a mismatch is a
      data-loss event, not a rounding difference.
- **Success:** every integrity comparison is identical.
- **Effort:** 1

### D3. Verify cagg parity

- [x] D3.1 Run `mt data caggs verify`.
- [x] D3.2 Apply the R5 discriminator per rollup cagg: sum parity strictly
      before the newest window boundary must be exactly 0. Do not read exit 2
      as corruption, and do not read it as safe (Criterion 5).
- [x] D3.3 Beware the `SET` echo when scripting the R5 query — a piped
      `psql -tAc "SET ...; SELECT ..."` yields `SET0`, not `0`. Log the
      parsed value and fail closed on anything that is not a bare integer.
- [x] D3.4 If a closed window is short, that is real under-materialization —
      re-run the repair path for that cagg before proceeding.
- **Success:** every rollup cagg returns exactly 0 for closed-window parity.
- **Effort:** 2

### D4. Verify the minute path did not regress

- [x] D4.1 Run `mt data rechunk --dry-run` with no `--table` and confirm it
      plans the minute table with unchanged semantics (Criterion 7).
- [x] D4.2 Confirm `minute_ohlcv`'s chunk count and dimension interval are
      untouched by this slice.
- **Success:** the minute target behaves exactly as before the refactor.
- **Effort:** 1

### D5. Documentation and close-out

- [x] D5.1 Record the execution results — before/after table, actual window
      count, run duration, and any surprises — in the slice design document
      as its execution record, following the 166 precedent.
- [x] D5.2 Note explicitly that `daily_coverage`'s content staleness was
      healed as a side effect, but the policy defect remains and staleness
      re-accrues, so **slice 169 is still required**.
- [x] D5.3 Update `100-arch.data-storage.md` and any other doc restating
      `daily_ohlcv`'s 7-day chunk interval, so no document keeps teaching the
      superseded value.
- [x] D5.4 Add a CHANGELOG entry under `[Unreleased]`.
- [x] D5.5 Add a DEVLOG entry for the maintenance run.
- [x] D5.6 Refine the design's Verification Walkthrough from draft into the
      verified form, with actual measured values substituted.
- [x] D5.7 Check off this task file and merge the slice branch to `main`.
- **Success:** documents reflect what actually happened, with measured values
  rather than expectations.
- **Effort:** 2

---

## Relevant Risks

- **Registry refactor silently changing the minute path.** The minute run is
  finished, so a regression would surface only on a future re-run for
  trailing windows. Mitigated by B3.4's untouched-tests evidence and D4.
- **A daily-family job firing mid-rewrite.** The catastrophic mode from
  166 A5-Q3: a concurrent refresh consumes the invalidation log and
  materializes nothing, leaving the cagg reporting "up-to-date" while
  permanently missing rows. Mitigated by the pre-flight (C4) and the
  force-refresh on the way out (C6).
- **Window count far from ~118 in the C2 dry run.** Would mean the grid
  assumption is wrong. C2.3 makes this a stop-and-report condition rather
  than something to push past.
