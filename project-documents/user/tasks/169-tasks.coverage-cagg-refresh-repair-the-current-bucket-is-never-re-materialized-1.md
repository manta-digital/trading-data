---
docType: tasks
slice: coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized
project: trading-data
lld: project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [167, 168, 170, 187]
interfaces: [187]
projectState: >
  Part 1 of 2 — design and implementation (width measurement, constants,
  migrations 051/052, doc-comment fix, architecture amendment). Part 2
  (169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md)
  covers the prod rebuild execution and close-out. 140-arch already amended
  (D6a, commit 7849757) for the working 30-day assumption. Slice 170 shipped
  2026-08-11 and its exit force-refresh already rewrote daily_coverage
  against the current (365-day) width. Both coverage refresh policies (jobs
  1107/1108) have been successful no-ops since creation — the defect this
  slice repairs.
dateCreated: 20260813
dateUpdated: 20260813
status: not_started
---

# Tasks: Coverage-Cagg Refresh Repair — Part 1 (Design and Implementation)

## Context summary

This is **part 1 of 2**. Part 1 covers width measurement, constants,
migrations 051/052, the doc-comment fix, and the architecture amendment —
everything short of touching production. Part 2
(`169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md`)
covers the prod rebuild execution (Task G) and close-out (Task H); it cannot
start until this file's tasks are merged.

`minute_coverage` and `daily_coverage` bucket at `COVERAGE_BUCKET_INTERVAL`
(365 days). A refresh policy's window is truncated to whole buckets and only
re-materializes buckets *fully contained* in it, so the open (current) bucket
is never written by the policy — materialized once at cagg creation, then
never again until the bucket closes. Both hourly policies have run 205 times
each as silent no-ops.

The repair is **narrow the bucket width** (not a custom head-refresh job),
paired with re-deriving every threshold that assumed the old width. Full
reasoning: `169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md`
D1–D7. This file does not restate the design's rationale — read D2/D3/D3a/D3b
before Task B, and **The Rebuild Window** section before Task C.

**Width is not fixed yet.** Task B measures it; the design's working
assumption is 30 days but Task B can select a different value in the 7–30 day
range. Every later task renders from `COVERAGE_BUCKET_INTERVAL` — no task
below hardcodes a width.

### What already exists (verified in tree)

- `src/manta_trading/constants.py`: `COVERAGE_BUCKET_INTERVAL` (:333),
  `COVERAGE_SOURCE_TABLE` (:351), `COVERAGE_REFRESH_MIN_WINDOW_BUCKETS` (:376),
  `COVERAGE_CONTENT_STALENESS` (:401), `MAX_COVERAGE_SOURCE_STALENESS` (:78),
  `CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT` (:94),
  `MINUTE_COVERAGE_REFRESH_START_OFFSET`/`END_OFFSET`/`SCHEDULE_INTERVAL`,
  `DAILY_COVERAGE_REFRESH_START_OFFSET`/`END_OFFSET`/`SCHEDULE_INTERVAL`.
- `src/manta_trading/market/schema/migrations/minute.py`: all migrations live
  in one `MINUTE_MIGRATIONS` list ending at `050_daily_chunk_interval_70d`
  (:2039). `046_create_coverage_caggs` (:1851) creates both caggs;
  `047_coverage_cagg_refresh_policies` (:1913) installs their policies;
  `048_data_status_cagg_backed` (:1972) installs `data_status` and attaches
  `_data_status_doc_comment()` (:355); `049` renamed the bucket column to
  `time_bucket`.
- `src/manta_trading/market/maintenance/cagg_freshness.py`: `_resolve_threshold`
  (:457) computes `min(start_offset, MAX_COVERAGE_SOURCE_STALENESS) +
  end_offset`, called from `_evaluate` (:652). `_raw_max` (:337)'s docstring
  already states the vacuous-for-wide-buckets caveat this slice acts on.
- `src/manta_trading/data/maintenance/status_coverage.py`:
  `check_coverage_freshness` (:223) calls `assert_cagg_fresh(conn, view_name,
  source_table=COVERAGE_SOURCE_TABLE[view_name], augment=_apply_content_edge_check,
  **kwargs)` per view — the content-edge check (187 D6) rides the same TTL
  cache via the `augment` hook.
- `src/manta_trading/market/maintenance/cagg_repair.py`: `_resolve_cagg_jobs`
  (:131) resolves jobs from the catalog by `hypertable_name` + `proc_name` —
  reuse this, never a hardcoded job ID. `_REFRESH_SUBWINDOW = timedelta(days=14)`
  (:108) and `_rebuild_window`'s sub-window loop (:355-363) are the precedent
  for bounded materialization. `_check_coverage_index_available` (:180) refuses
  a pause of `minute_4hour_ohlcv` for a different cagg's repair.
- `src/manta_trading/data/quality/restore_metadata.py` `expected_caggs` (:185)
  already lists `minute_coverage`/`daily_coverage` by name; the migration
  ledger is consulted dynamically via `MINUTE_MIGRATIONS` (:166), so **no
  migration-number literal needs updating there** — verify only.
- `test/integration/test_migrations_046_047.py`,
  `test/integration/test_migration_050.py` — precedent for new migration
  integration tests.
- `test/unit/market/test_constants.py`:
  `test_coverage_refresh_window_meets_engine_minimum` already asserts
  `COVERAGE_REFRESH_MIN_WINDOW_BUCKETS * COVERAGE_BUCKET_INTERVAL` — must pass
  unedited at the new width (criterion 4).

### Non-negotiables from the design

- No width literal in SQL, tests, or migration descriptions — everything
  renders from `COVERAGE_BUCKET_INTERVAL` (D5).
- `DROP MATERIALIZED VIEW` on either coverage cagg **without** first dropping
  `data_status`, or **with** `CASCADE`, are both forbidden (D4, Migration Plan
  ordering ①②③).
- Every job paused for the rebuild is resolved from the catalog by name via
  `_resolve_cagg_jobs`, never a hardcoded job ID (D4, 170 lesson).
  `minute_4hour_ohlcv`'s refresh must never be paused (D4,
  `_check_coverage_index_available`).
- Materialization is issued as bounded sub-windows, never one full-span call
  (The Rebuild Window). A statement timeout is set, sized to one sub-window.
- 051 runs `requires_autocommit` with no transactional rollback and must be
  idempotent on re-run from any point in ①②③.
- Exact row counts only for verification (`count(*)`) — `approximate_row_count`
  is excluded (D7).

---

## Task A — Amend the row-count basis before measuring (prerequisite for Task B)

- [ ] **A.1 Confirm 140-arch's D6a amendment is current**
  - [ ] Read the four amendment blocks 140-arch already carries (commit
        `7849757`): `COVERAGE_BUCKET_INTERVAL`'s constants block, the
        slice-167 bounded-consistency paragraph, the refresh-policy block, the
        `MAX_COVERAGE_SOURCE_STALENESS` block.
  - [ ] Confirm each still states the width as the **working assumption**
        (30 days), not yet the measured value — Task B closes this out (D6a's
        checklist).
  - Success: no drift between the design's D6a section and the live
    140-arch text; discrepancies (if any) are reported before Task B runs.
  - Effort: 1

---

## Task B — Measure the width tradeoff (Task B1 from the design)

> Every prod-adjacent step in this task runs against an **ephemeral or test
> database seeded to a representative shape**, never against prod. Nothing in
> Task B issues DDL against prod.

- [ ] **B.1 Seed a representative measurement database**
  - [ ] Use or build a database whose `daily_ohlcv`/`minute_ohlcv` span and
        symbol count approximate slice 170's measured prod spans (daily
        1962–2026 / 12,040 symbols; minute 2004–2026 / 5,871 symbols) —
        exact replication is not required, but the row-count arithmetic in
        design D2 must be checkable against it.
  - [ ] Document the seed's actual span and symbol count in the task notes —
        Task B's numbers are only meaningful relative to what was measured.
  - Success: a database exists that can materialize `minute_coverage` and
    `daily_coverage` at candidate widths without touching prod.
  - Effort: 3

- [ ] **B.2 Materialize both caggs at each candidate width (7 / 30 / 90 days)**
  - [ ] For each candidate, create both coverage caggs at that
        `time_bucket` width (ad hoc SQL against the measurement DB, not a
        migration) and materialize over the seeded history.
  - [ ] Record rows materialized (actual, not worst-case) per view per width.
  - Success: three actual row counts per view, comparable against design D2's
    worst-case table.
  - Effort: 3

- [ ] **B.3 Measure the `bars_summary` grouping cost (diagnostic only)**
  - [ ] Run `EXPLAIN (ANALYZE, BUFFERS) SELECT symbol, MIN(first_bucket),
        MAX(last_bucket), SUM(bars)::BIGINT FROM daily_coverage GROUP BY
        symbol;` (and the minute equivalent) at each width.
  - [ ] Record timings. This is a diagnostic for *where* cost lands, not the
        gate — criterion 12 is the full-view read (B.4).
  - Success: three timing pairs recorded.
  - Effort: 2

- [ ] **B.4 Measure the full-universe `data_status` NFR (the actual gate,
      criterion 12)**
  - [ ] Run `EXPLAIN (ANALYZE, BUFFERS) SELECT count(*) FROM data_status;` at
        each candidate width.
  - [ ] This is the shape slice 167 took from 7.8 s to sub-second — record
        against that NFR, not against B.3's CTE-only number.
  - Success: three full-view timings recorded; any width failing the
    sub-second NFR is flagged.
  - Effort: 2

- [ ] **B.5 Measure the content-edge probe cost against the 10 s budget (D3b,
      criterion 17)**
  - [ ] Run `SELECT max(last_bucket) FROM daily_coverage;` and the minute
        equivalent at each width.
  - [ ] Record against `CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT` (10 s). A
        width that approaches or exceeds it is rejected on NFR grounds per
        D3b — raising the timeout is not an available fix.
  - Success: three probe timings recorded per view (criterion 17); any width
    failing the margin check is flagged.
  - Effort: 2

- [ ] **B.6 Measure the refresh policy's per-run cost at candidate
      `start_offset` values (D4a)**
  - [ ] For each candidate width, derive the corresponding floor
        (`COVERAGE_REFRESH_MIN_WINDOW_BUCKETS × width`) and measure a
        representative policy run (`refresh_continuous_aggregate` over a
        `start_offset`-sized window) on the seeded database.
  - [ ] Record wall-clock against the 1-hour schedule interval — it must fit
        comfortably with margin, not merely complete.
  - Success: one measured run time per candidate width, compared against its
    schedule interval.
  - Effort: 3

- [ ] **B.7 Select the width and record the decision**
  - [ ] Choose the smallest candidate width that holds **all** of: the
        sub-second `data_status` NFR (B.4), the 10 s probe budget with margin
        (B.5), and a policy run comfortably inside its schedule interval
        (B.6).
  - [ ] Record the selected width, the selected `start_offset`, and every
        measurement from B.2–B.6 in the task notes — this is the source data
        Task E's architecture amendment renders from.
  - Success: one width and one `start_offset` selected, with the measurements
    that justify the choice recorded, not just the conclusion.
  - Effort: 1

- [ ] **Commit**: `docs: record slice 169 width-selection measurements (Task B)`

---

## Task C — Constants and derived values (D2, D3, D3a, D4a, D5)

- [ ] **C.1 Update `COVERAGE_BUCKET_INTERVAL` to the selected width**
  - [ ] `src/manta_trading/constants.py:333` — new value from Task B.7, with
        the docstring's row-count rationale updated to match (no longer "~15k
        rows"; use the measured actual from B.2).
  - Success: single source of truth updated; no other literal introduced
    (criterion 1).
  - Effort: 1

- [ ] **C.2 Re-derive `COVERAGE_CONTENT_STALENESS` (D3, criterion 2)**
  - [ ] `constants.py:401` — value becomes `COVERAGE_BUCKET_INTERVAL +
        max(end_offset over both coverage policies)`, computed from the
        constants (not a literal), with the docstring's derivation block
        updated to show the new arithmetic.
  - Success: value changes automatically if `COVERAGE_BUCKET_INTERVAL`
    changes again; docstring states the new bound in prose (criterion 2).
  - Effort: 2

- [ ] **C.3 Unit-test `COVERAGE_CONTENT_STALENESS` derivation**
  - [ ] Assert `COVERAGE_CONTENT_STALENESS == COVERAGE_BUCKET_INTERVAL +
        max(MINUTE_COVERAGE_REFRESH_END_OFFSET, DAILY_COVERAGE_REFRESH_END_OFFSET)`
        directly from the constants, so the test fails if the derivation is
        ever hand-edited back to a literal.
  - Success: test passes; fails if the constant is replaced with a literal.
  - Effort: 1

- [ ] **C.4 Add the per-view bucket-lag budget override (D3a)**
  - [ ] Add a mapping in `constants.py`, resolved alongside
        `COVERAGE_SOURCE_TABLE` (:351), keyed by view name, giving the
        coverage bucket-lag budget: `COVERAGE_BUCKET_INTERVAL +
        min(start_offset, MAX_COVERAGE_SOURCE_STALENESS) + end_offset` per
        view. Follow the design's rejection of a boolean "tolerant" flag —
        the map carries the value, not a mode switch.
  - [ ] Docstring cites D3a and states explicitly that the seven pre-167
        caggs are untouched because they have no entry, falling back to
        `cagg_freshness._resolve_threshold`'s existing formula.
  - Success: importable map with two entries (`minute_coverage`,
    `daily_coverage`); no width literal.
  - Effort: 2

- [ ] **C.5 Wire the override into `cagg_freshness._resolve_threshold` /
      `_evaluate`**
  - [ ] In `src/manta_trading/market/maintenance/cagg_freshness.py`, extend
        `_evaluate` (:589) or `_resolve_threshold` (:457) to consult the new
        C.4 map by `view_name` when present, falling back to the existing
        `min(start_offset, MAX_COVERAGE_SOURCE_STALENESS) + end_offset`
        formula for every view without an entry.
  - [ ] Preserve the public `assert_cagg_fresh` signature — `status_coverage.py`'s
        call site (`source_table=..., augment=..., **kwargs`) must not change
        (167/187 contract).
  - Success: `minute_coverage`/`daily_coverage` resolve the D3a budget; every
    other view's resolved threshold is byte-identical to before this task.
  - Effort: 3

- [ ] **C.6 Unit-test the per-view override, both directions**
  - [ ] `minute_coverage`/`daily_coverage` resolve to the C.4 budget, not the
        generic formula.
  - [ ] A pre-167 cagg (e.g. `daily_monthly_ohlcv`) resolves to the
        **unchanged** generic formula — regression guard against the override
        leaking to views without an entry.
  - [ ] A view whose lag pins at exactly one bucket width no longer trips
        `LAG_EXCEEDS_THRESHOLD` under the new budget (the D3a scenario
        directly).
  - Success: three tests pass; the second one fails if the override is
    applied unconditionally.
  - Effort: 2

- [ ] **C.7 Re-examine `start_offset` for both coverage policies (D4a,
      criterion 19)**
  - [ ] Update `MINUTE_COVERAGE_REFRESH_START_OFFSET` and
        `DAILY_COVERAGE_REFRESH_START_OFFSET` to the value Task B.7 selected,
        with docstrings recording the three constraints: engine floor
        (`COVERAGE_REFRESH_MIN_WINDOW_BUCKETS × width`), the minute side's
        parent-window constraint (unchanged from 167 D4), and the new
        measured-runtime-fits-schedule-interval constraint from B.6.
  - Success: both `start_offset`s updated; docstrings cite the B.6
    measurement showing margin against the schedule interval, not an
    assumption (criterion 19).
  - Effort: 2

- [ ] **C.8 Confirm `test_coverage_refresh_window_meets_engine_minimum`
      passes unedited (criterion 4)**
  - [ ] Run the existing test in `test/unit/market/test_constants.py` after
        C.1/C.7. It must pass with **no edits to the assertion itself** — it
        was written to fail loudly at test time if the constants drift out of
        the engine's constraint.
  - Success: test passes with zero diff to the assertion.
  - Effort: 1

- [ ] **C.9 Fix `_data_status_doc_comment()`'s CAGG LAG formula (D5)**
  - [ ] `src/manta_trading/market/schema/migrations/minute.py:355-397` — the
        current formula (`MINUTE_CAGG_REFRESH_SCHEDULE_INTERVAL +
        MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL`, "2 hours total") is wrong
        independent of this slice's width change: it never accounted for the
        open-bucket lag. Replace with `COVERAGE_BUCKET_INTERVAL + refresh
        schedule interval(s)` per D5, rendered from constants via
        `_interval_literal`, matching the CAGG LAG clause's existing style.
  - [ ] Keep the BUCKET TRUNCATION clause and the "FRESHNESS IS NOT ASSERTED
        IN SQL" clause unchanged — only the CAGG LAG derivation changes.
  - [ ] Done here, **before** Task D's migrations, so 051/052 can re-execute
        this function directly with no placeholder/supersede step.
  - Success: the string "2 hours total" (or any two-hop-only phrasing) no
    longer appears anywhere in the function's output.
  - Effort: 2

- [ ] **C.10 Unit-test the corrected doc comment (criterion 14)**
  - [ ] Assert the rendered comment text includes the bucket-width term (i.e.
        contains a rendering of `COVERAGE_BUCKET_INTERVAL`), not just the
        schedule intervals.
  - [ ] Assert the old two-hop-only phrasing is absent — regression guard
        pinned to criterion 14.
  - Success: tests pass; fail if the formula reverts to schedule-intervals
    only (criterion 14).
  - Effort: 2

- [ ] **Commit**: `feat: re-derive coverage cagg constants at the measured width`

---

## Task D — Migrations 051/052 (D4, Migration Plan)

- [ ] **D.1 Write migration `051_coverage_cagg_bucket_narrowing`**
  - [ ] In `MINUTE_MIGRATIONS` (`src/manta_trading/market/schema/migrations/minute.py`),
        append after `050`. `requires_autocommit: True`.
  - [ ] Step ① `DROP VIEW IF EXISTS data_status` — **before** touching either
        cagg (mandatory ordering; `data_status` depends on both).
  - [ ] Step ② `DROP MATERIALIZED VIEW IF EXISTS minute_coverage` /
        `daily_coverage` — **no `CASCADE`** — then recreate both at the new
        `COVERAGE_BUCKET_INTERVAL` width using `_interval_seconds_sql`,
        mirroring 046's structure (one `CREATE MATERIALIZED VIEW IF NOT
        EXISTS` per `execute()` call, same column names/types/aliases as 046
        so downstream queries bind unchanged).
  - [ ] Step ③ re-install `data_status` by re-executing 048's existing
        `_build_data_status_view_sql(...)` output unchanged (not a rewrite),
        and re-attach the corrected `_data_status_doc_comment()` from Task
        C.9 — the function is already fixed by this point, so 051 calls it
        directly with no placeholder text.
  - [ ] Description text states the new width and the new worst-case row
        counts from Task B (no stale "1 year"/"~15k rows" text carried
        forward from 046).
  - [ ] Idempotent: every statement uses `IF EXISTS`/`IF NOT EXISTS`, so
        re-running 051 from any point in ①②③ converges.
  - Success: migration defined; matches the mandatory ①②③ ordering; no
    `CASCADE` anywhere in the migration.
  - Effort: 4

- [ ] **D.2 Write migration `052_coverage_cagg_refresh_policies_narrowed`**
  - [ ] Reinstall both coverage caggs' refresh policies at the new
        `start_offset` (Task C.7), unchanged `end_offset`/`schedule_interval`,
        using the idempotent `DO $$ ... IF NOT EXISTS ... $$` pattern from
        047 (:1925).
  - [ ] Re-render `COMMENT ON VIEW data_status` from the corrected doc-comment
        function (Task C.9) so the migration-052-installed comment is already
        correct — no follow-up migration needed for the comment text.
  - Success: migration defined; idempotent on re-run; comment text sourced
    from the single doc-comment function, no duplicated string.
  - Effort: 2

- [ ] **D.3 Integration test: 051/052 apply cleanly on a cold-start database
      (criterion 3)**
  - [ ] New `test/integration/test_migration_051_052.py`, following
        `test_migrations_046_047.py`'s pattern: fresh throwaway database, run
        the full migration chain, assert it ends at `052` with no errors.
  - [ ] Assert both coverage caggs exist with the new `time_bucket` width
        (query `timescaledb_information.continuous_aggregates` /
        `dimensions`, not a hardcoded literal — compare against
        `COVERAGE_BUCKET_INTERVAL`).
  - [ ] Assert `data_status` exists and returns zero rows without error on
        the empty cold-start database (criterion 13, cold-start case).
  - Success: test passes on a throwaway database; fails if 051/052 regress.
  - Effort: 3

- [ ] **D.4 Integration test: idempotent re-run from a partial 051 state**
  - [ ] Simulate a failure between steps ① and ③ (e.g. run only step ① and
        ②'s DDL on a scratch database, matching "Window A" in the design's
        Rebuild Window section), then re-run 051 in full and assert it
        converges without error.
  - Success: test passes; demonstrates the recovery path the design's Window
    A handling describes (re-run 051, not a manual recovery script).
  - Effort: 3

- [ ] **D.5 Integration test: `DROP` ordering is enforced (regression guard
      for F002)**
  - [ ] Assert that attempting to drop either coverage cagg **without**
        first dropping `data_status` raises the expected dependency error on
        a scratch database with 048's `data_status` installed — this pins
        the reason step ① exists, so a future edit that reorders 051
        breaks a test immediately rather than failing silently on prod.
  - Success: test passes; fails if steps ①/② are reordered.
  - Effort: 2

- [ ] **D.6 Update `test_migrations_046_047.py`/`test_migration_050.py`
      fixtures that assume the old width, if any (verify, do not assume)**
  - [ ] Per D5: `test_coverage_content_edge.py`, `test_migrations_046_047.py`,
        `test_symbol_ranges_sql.py`, `test_data_status_equivalence.py`
        already scale with `COVERAGE_BUCKET_INTERVAL` and should need no
        arithmetic changes. Run each and confirm — do not edit unless a
        failure surfaces a hardcoded literal.
  - Success: all four suites pass unedited, or the specific hardcoded
    literal found is fixed and noted.
  - Effort: 2

- [ ] **Commit**: `feat: add migrations 051/052 narrowing coverage cagg buckets`

---

## Task E — Architecture close-out (D6, D6a)

- [ ] **E.1 Close out the 140-arch D6a checklist with measured values
      (criterion 15)**
  - [ ] Update `COVERAGE_BUCKET_INTERVAL` in 140-arch to the **measured**
        width from Task B.7 (confirm or correct the existing 30-day working
        assumption).
  - [ ] Update `COVERAGE_CONTENT_STALENESS` and the per-view bucket-lag
        budget text to match Task C.2/C.4.
  - [ ] Replace the worst-case row-count table with **measured actuals**
        from Task B.2.
  - [ ] Record the measured probe cost (B.5) against the 10 s budget.
  - [ ] Record the selected `start_offset` (C.7) and its measured per-run
        cost (B.6).
  - [ ] Use the established amendment convention (`*(Architecture amendment,
        {date} — slice 169.)*`) for each edit, consistent with the four
        blocks already amended under D6a.
  - Success: 140-arch states measured values throughout, with no remaining
    "working assumption" language for anything Task B measured.
  - Effort: 2

- [ ] **Commit**: `docs: close 140-arch D6a with measured coverage-cagg values`

---

## Task F — `restore_metadata.py` verification (D-Consumers)

- [ ] **F.1 Verify `restore_metadata.py` needs no change**
  - [ ] Confirm `expected_caggs` (:185-195) already lists `minute_coverage`
        and `daily_coverage` by name (no migration-number literal to update —
        the ledger check at :166 consults `MINUTE_MIGRATIONS` dynamically).
  - [ ] Run any existing `restore_metadata` tests against a database that has
        051/052 applied and confirm no missing-migration or missing-view
        false positive.
  - Success: confirmed no code change needed, or a genuine gap found and
    fixed. Either outcome recorded — do not skip silently.
  - Effort: 1

---

## Notes

- **Effort 3/5 overall** (design estimate) — the migrations and constants
  changes here are small; the width measurement (Task B) is the weight in
  this file. The prod rebuild (part 2, Task G) carries the rest.
- **Commit per task group**, not batched at the end — checkpoints follow
  Tasks B, C, D, E.
- **Continue to part 2**
  (`169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md`)
  once Tasks A–F are merged. Part 2's Task G (prod rebuild) requires the
  design's PM Decisions (30-day provisional lag, widened
  `COVERAGE_CONTENT_STALENESS`) to remain the operative decisions — if the PM
  revisits either before Task G runs, stop and confirm before proceeding.
- Out of scope, per D7: head-refresh machinery, `bars_summary` floor-plus-
  head-probe reshape (GitHub issue #14), `mt data caggs repair` extension to
  coverage caggs, the minute rollup parity shortfall, `approximate_row_count`
  accuracy.
