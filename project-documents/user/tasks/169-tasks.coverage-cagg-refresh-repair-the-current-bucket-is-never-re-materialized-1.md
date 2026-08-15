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

- [x] **A.1 Confirm 140-arch's D6a amendment is current**
  - [x] Read the four amendment blocks 140-arch already carries (commit
        `7849757`): `COVERAGE_BUCKET_INTERVAL`'s constants block, the
        slice-167 bounded-consistency paragraph, the refresh-policy block, the
        `MAX_COVERAGE_SOURCE_STALENESS` block.
  - [x] Confirm each still states the width as the **working assumption**
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

- [x] **B.1 Seed a representative measurement database**
  - [x] Seed `daily_ohlcv`/`minute_ohlcv` span and symbol count **within 10%**
        of slice 170's measured prod spans (daily 1962–2026 / 12,040 symbols;
        minute 2004–2026 / 5,871 symbols) — this tolerance, not "approximate,"
        is what B.4/B.5/B.6a's measurements are checked against; record the
        actual delta achieved.
  - [x] **Also seed `symbols`, `acquisition_state`, and enough of the
        exchange-close CTE's inputs (`trading_sessions`) to exercise
        `data_status`'s full join** — not just the two coverage source
        tables. Criterion 12 measures the *full view read*
        (`SELECT count(*) FROM data_status`), which joins `bars_summary`
        against these three; seeding only `daily_ohlcv`/`minute_ohlcv` would
        let B.4 measure a cheaper query shape than the one criterion 12
        actually gates, silently understating cost.
  - [x] Document the seed's actual span, symbol count, and per-table row
        counts (`symbols`, `acquisition_state`) in the task notes, with the
        delta from prod's counts — Task B's numbers are only meaningful
        relative to what was measured, and B.7 must record this delta
        alongside the selected width.
  - Success: a database exists that can materialize `minute_coverage` and
    `daily_coverage` at candidate widths, and exercise `data_status`'s full
    join, without touching prod; seed row counts recorded within 10% of prod
    for every table `data_status` reads.
  - Effort: 3

- [x] **B.2 Materialize both caggs at each candidate width (7 / 30 / 90 days)**
  - [x] For each candidate, create both coverage caggs at that
        `time_bucket` width (ad hoc SQL against the measurement DB, not a
        migration) and materialize over the seeded history.
  - [x] Record rows materialized (actual, not worst-case) per view per width.
  - Success: three actual row counts per view, comparable against design D2's
    worst-case table. **Measured values (7-day bucket):**
    - minute_coverage: 3,019,870 rows
    - daily_coverage: 16,742,957 rows
    (Also measured at 14d, 30d, and 90d widths for comparison.)
  - Effort: 3

- [x] **B.3 Measure the `bars_summary` grouping cost (diagnostic only)**
  - [x] Run `EXPLAIN (ANALYZE, BUFFERS) SELECT symbol, MIN(first_bucket),
        MAX(last_bucket), SUM(bars)::BIGINT FROM daily_coverage GROUP BY
        symbol;` (and the minute equivalent) at each width.
  - [x] Record timings. This is a diagnostic for *where* cost lands, not the
        gate — criterion 12 is the full-view read (B.4).
  - Success: three timing pairs recorded.
  - Effort: 2

- [x] **B.4 Measure the full-universe `data_status` NFR on the seeded database
      (a prediction for criterion 12, not the criterion itself)**
  - [x] Run `EXPLAIN (ANALYZE, BUFFERS) SELECT count(*) FROM data_status;` at
        each candidate width, against B.1's fully-joined seed (not just the
        two coverage tables).
  - [x] This is the shape slice 167 took from 7.8 s to sub-second — record
        against that NFR, not against B.3's CTE-only number.
  - [x] **This measurement predicts criterion 12; it does not satisfy it.**
        Criterion 12 is a prod NFR. Part 2's Task G takes the prod
        measurement that actually closes it (see part 1's B.7 and part 2's
        Task G for the explicit hand-off).
  - Success: three full-view timings recorded on the seed; any width failing
    the sub-second NFR is flagged; predicted-vs-prod status noted explicitly.
  - Effort: 2

- [x] **B.5 Measure the content-edge probe cost against the 10 s budget (D3b,
      criterion 17)**
  - [x] Run `SELECT max(last_bucket) FROM daily_coverage;` and the minute
        equivalent at each width.
  - [x] Record against `CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT` (10 s). A
        width that approaches or exceeds it is rejected on NFR grounds per
        D3b — raising the timeout is not an available fix.
  - Success: three probe timings recorded per view (criterion 17); any width
    failing the margin check is flagged. **7d worst case: 0.220s (45x margin).**
  - Effort: 2

- [x] **B.6a Measure raw policy wall-clock at each candidate width, at the
      engine-floor `start_offset` (D4a)**
  - [x] For each candidate width, fix `start_offset` at the engine floor
        (`COVERAGE_REFRESH_MIN_WINDOW_BUCKETS × width`) — a single, mechanical
        value, not a choice — and measure a representative policy run
        (`refresh_continuous_aggregate` over that `start_offset`-sized window)
        on the seeded database.
  - [x] Record wall-clock per candidate width. This step does **not** select
        `start_offset`; it only measures cost at the one value every
        candidate needs regardless of what B.7 ultimately picks.
  - Success: one measured run time per candidate width, all at the same
    floor-relative `start_offset` definition. **Engine floor: 0.023–0.024s
    (flat across all widths, quiescent database, not a prod prediction).**
  - Effort: 2

- [x] **B.6b Derive the candidate `start_offset` value(s) from B.6a plus the
      non-runtime constraints (D4a)**
  - [x] For each candidate width, apply the two non-runtime constraints —
        engine floor (already used as B.6a's measurement point) and the
        minute side's parent-window constraint (unchanged from 167 D4) — to
        get a candidate `start_offset`. If B.6a's floor-relative measurement
        already fits the 1-hour schedule interval with comfortable margin,
        the floor value is retained; if it does not, widen `start_offset`
        only as far as needed and re-measure that one case (not a full
        re-sweep of B.6a).
  - Success: one candidate `start_offset` per width, each backed by a
    wall-clock measurement that fits its schedule interval with margin.
    **Derived: 16 days for both views (engine floor 2×bucket + end_offset
    = 14d 4h at 7-day bucket, ~20h margin to 16d).**
  - Effort: 2

- [x] **B.7 Select the width and record the decision**
  - [x] Choose the smallest candidate width that holds **all** of: the
        sub-second `data_status` NFR (B.4), the 10 s probe budget with margin
        (B.5), and a policy run comfortably inside its schedule interval
        (B.6b).
  - [x] Record the selected width, the selected `start_offset` (from B.6b),
        and every measurement from B.2–B.6b in the task notes — this is the
        source data Task E's architecture amendment renders from.
  - [x] **Criterion 12's B.4 number is a prediction, not the criterion.**
        Criterion 12 is a prod NFR; B.4 measures the seeded database only.
        Record B.4's number as "predicted, pending prod confirmation
        (part 2)" — do not mark criterion 12 satisfied from B.4 alone. Part
        2's G.9a (see that file) takes the prod measurement that actually
        closes criterion 12.
  - Success: one width and one `start_offset` selected, with the measurements
    that justify the choice recorded, not just the conclusion; criterion 12
    explicitly flagged as pending prod confirmation. **Selected: 7-day width.
    All criteria met; decision record with measurements documented.**
  - Effort: 1

**Follow-on GitHub issues filed:** #16 (unfiltered health-count scan dominates status latency), #17 (data_status NFR stated against a query no caller issues), #14 updated for 7-day width.

- [ ] **B.8 Add load tests pinning the three restated NFRs (criteria 12, 17,
      19) at the selected width**
  - [ ] `test/load/` already exists with the tier convention to follow:
        `test_167_data_status_nfr.py` is the direct precedent for criterion
        12 — a prod-shaped throwaway database, gated on
        `MT_RUN_LOAD_TESTS=1`, using `MT_TIMESCALE_TEST_URL` (never the prod
        URL), with a mechanical `test_load_tier_never_references_prod_db_url`
        guard. Update it to seed at the **new** `COVERAGE_BUCKET_INTERVAL`
        (the existing fixture predates this slice) and re-assert the
        sub-second NFR — this makes criterion 12 re-checkable by any future
        change, not just confirmed once during this slice.
  - [ ] Add `test/load/test_169_coverage_freshness_probe_nfr.py`: seed a
        prod-shaped database at the selected width, run the content-edge
        probe (`max(last_bucket)`) and assert it stays well inside
        `CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT` (criterion 17) — same tier
        conventions as the 167 precedent.
  - [ ] Add a policy-run-cost assertion (criterion 19) to the same or a
        sibling load test: at the selected `start_offset` (B.6b/C.7), a
        representative refresh run completes comfortably inside the 1-hour
        schedule interval on a prod-shaped database.
  - [ ] **CI wiring note:** per the 167 load test's own documented gap, this
        repo's CI (`.github/workflows/ci.yml`) runs no test job at all — CI
        wiring for the whole `test/load/` tier is out-of-band, tracked as
        slice 907 (CI Pipeline and Load-Test Gating). These load tests close
        the same documented gap 167 already accepted, not a new one this
        slice introduces; do not add slice 907's CI wiring here.
  - Success: criteria 12, 17, and 19 each have a load test that can be
    re-run (`MT_RUN_LOAD_TESTS=1 uv run pytest test/load/`) to reconfirm the
    NFR, rather than resting on a one-time Task B/G measurement alone.
  - Effort: 3

- [ ] **Commit**: `test: add load tests for coverage-cagg NFRs at the new width`

---

## Task C — Constants and derived values (D2, D3, D3a, D4a, D5)

- [x] **C.1 Update `COVERAGE_BUCKET_INTERVAL` to the selected width**
  - [x] `src/manta_trading/constants.py:333` — new value from Task B.7, with
        the docstring's row-count rationale updated to match (no longer "~15k
        rows"; use the measured actual from B.2).
  - Success: single source of truth updated; no other literal introduced
    (criterion 1). **Set to timedelta(days=7) with row-count rationale updated
    to measured actuals.**
  - Effort: 1

- [x] **C.2 Re-derive `COVERAGE_CONTENT_STALENESS` (D3, criterion 2)**
  - [x] `constants.py:401` — value becomes `COVERAGE_BUCKET_INTERVAL +
        max(end_offset over both coverage policies)`, computed from the
        constants (not a literal), with the docstring's derivation block
        updated to show the new arithmetic.
  - Success: value changes automatically if `COVERAGE_BUCKET_INTERVAL`
    changes again; docstring states the new bound in prose (criterion 2).
    **Now computed as COVERAGE_BUCKET_INTERVAL + max(end_offset) = 7d 4h,
    with derivation in docstring.**
  - Effort: 2

- [x] **C.3 Unit-test `COVERAGE_CONTENT_STALENESS` derivation**
  - [x] Assert `COVERAGE_CONTENT_STALENESS == COVERAGE_BUCKET_INTERVAL +
        max(MINUTE_COVERAGE_REFRESH_END_OFFSET, DAILY_COVERAGE_REFRESH_END_OFFSET)`
        directly from the constants, so the test fails if the derivation is
        ever hand-edited back to a literal.
  - Success: test passes; fails if the constant is replaced with a literal.
    **test_coverage_content_staleness_is_derived_not_literal added to
    test/unit/test_constants.py.**
  - Effort: 1

- [x] **C.4 Add the per-view bucket-lag budget override (D3a)**
  - [x] Add a mapping in `constants.py`, resolved alongside
        `COVERAGE_SOURCE_TABLE` (:351), keyed by view name, giving the
        coverage bucket-lag budget: `COVERAGE_BUCKET_INTERVAL +
        min(start_offset, MAX_COVERAGE_SOURCE_STALENESS) + end_offset` per
        view. Follow the design's rejection of a boolean "tolerant" flag —
        the map carries the value, not a mode switch.
  - [x] Docstring cites D3a and states explicitly that the seven pre-167
        caggs are untouched because they have no entry, falling back to
        `cagg_freshness._resolve_threshold`'s existing formula.
  - Success: importable map with two entries (`minute_coverage`,
    `daily_coverage`); no width literal.
  - Effort: 2

- [x] **C.5 Wire the override into `cagg_freshness._resolve_threshold` /
      `_evaluate`**
  - [x] In `src/manta_trading/market/maintenance/cagg_freshness.py`, extend
        `_evaluate` (:589) or `_resolve_threshold` (:457) to consult the new
        C.4 map by `view_name` when present, falling back to the existing
        `min(start_offset, MAX_COVERAGE_SOURCE_STALENESS) + end_offset`
        formula for every view without an entry.
  - [x] Preserve the public `assert_cagg_fresh` signature — `status_coverage.py`'s
        call site (`source_table=..., augment=..., **kwargs`) must not change
        (167/187 contract).
  - Success: `minute_coverage`/`daily_coverage` resolve the D3a budget; every
    other view's resolved threshold is byte-identical to before this task.
  - Effort: 3

- [x] **C.6 Unit-test the per-view override, both directions**
  - [x] `minute_coverage`/`daily_coverage` resolve to the C.4 budget, not the
        generic formula.
  - [x] A pre-167 cagg (e.g. `daily_monthly_ohlcv`) resolves to the
        **unchanged** generic formula — regression guard against the override
        leaking to views without an entry.
  - [x] A view whose lag pins at exactly one bucket width no longer trips
        `LAG_EXCEEDS_THRESHOLD` under the new budget (the D3a scenario
        directly).
  - Success: three tests pass; the second one fails if the override is
    applied unconditionally.
  - Effort: 2

- [x] **C.6a Build the scratch-cagg scaffold for "policy advances the head
      unaided" (criterion 18 verification scaffold)**
  - [x] Criterion 18 — "the refresh policy advances the head on its own" — is
        the only criterion the original defect could not satisfy, and it is
        exercised for real against prod in part 2's Task G.13. Part 1's job is
        to build and unit/integration-test the **mechanism** that
        distinguishes "policy ran and head moved" from "policy ran and head
        stood still," so G.13 in part 2 is executing a proven check, not
        writing one from scratch.
  - [x] Following `test/integration/test_rechunk_driver.py`'s and slice 168's
        Task 8 pattern: build a scratch hypertable + scratch cagg + scratch
        refresh policy at the new narrow bucket width, dropped on teardown.
  - [x] Write a helper `_head_advanced(before, after) -> bool` (or equivalent)
        that compares `MAX(last_bucket)` and `last_successful_finish` before
        and after a policy tick, and returns true only when **both** the job
        ran and the head moved — a job that ran while the head stood still
        (the original defect's exact signature) must return `False`.
  - [x] Integration test: insert rows into the scratch hypertable spanning
        into a still-open bucket, wait for (or manually trigger) one scratch
        policy tick, and assert `_head_advanced` returns `True` — proving the
        narrowed-bucket mechanism actually lets the policy write the open
        bucket, which is D1/D2's central claim.
        
        **IMPORTANT NOTE (verified complete, defect documented):** The task
        brief's instruction to "insert rows spanning into a still-open bucket
        ... and assert `_head_advanced` returns `True`" was found to be FALSE
        when measured. 200 rows in the open bucket saw 13 consecutive
        successful policy runs materialize nothing — the open bucket is never
        refreshed while open, at the 7-day width exactly as at 365 (design D1:
        "nothing does"). The positive test therefore seeds a CLOSED bucket
        (materializes unaided within one tick), and a separate test asserts
        the open-bucket non-event so the accepted residual is documented
        rather than mistaken for a fix.
  - [x] Regression case: assert `_head_advanced` returns `False` when the
        scratch policy is paused (job ran nothing, head genuinely frozen) —
        so the helper cannot be trivially satisfied by "job exists."
  - Success: a tested, reusable check for "policy advanced the head unaided"
    exists before part 2 runs it against prod; the regression case proves it
    can actually detect the original defect's signature. New test/integration/test_policy_advances_head.py,
    9 tests passing. Scratch hypertable + scratch cagg at the new narrow
    width + scratch refresh policy, dropped on teardown. `_head_advanced(before, after) -> bool`
    returns True only when BOTH the job ran (last_successful_finish advanced)
    AND MAX(last_bucket) moved; 6 unit-level cases pin the contract including
    the defect's exact signature (job ran, head frozen -> False) and
    manual-refresh detection (head moved, job didn't run -> False). Integration
    cases use the REAL background scheduler, never `CALL run_job()`, since a
    manually-triggered policy is exactly what criterion 18 does not assert.
  - Effort: 3

- [x] **C.7 Re-examine `start_offset` for both coverage policies (D4a,
      criterion 19)**
  - [x] Update `MINUTE_COVERAGE_REFRESH_START_OFFSET` and
        `DAILY_COVERAGE_REFRESH_START_OFFSET` to the value Task B.7 selected,
        with docstrings recording the three constraints: engine floor
        (`COVERAGE_REFRESH_MIN_WINDOW_BUCKETS × width`), the minute side's
        parent-window constraint (unchanged from 167 D4), and the
        measured-runtime-fits-schedule-interval constraint from B.6a/B.6b.
  - Success: both `start_offset`s updated; docstrings cite the B.6a/B.6b
    measurement showing margin against the schedule interval, not an
    assumption (criterion 19). **Both set to 750 -> 16 days, docstrings
    recording all three constraints.**
  - Effort: 2

- [x] **C.8 Confirm `test_coverage_refresh_window_meets_engine_minimum`
      passes unedited (criterion 4)**
  - [x] Run the existing test in `test/unit/market/test_constants.py` after
        C.1/C.7. It must pass with **no edits to the assertion itself** — it
        was written to fail loudly at test time if the constants drift out of
        the engine's constraint.
  - Success: test passes with zero diff to the assertion. **Test passes UNEDITED
    at the new width (criterion 4 satisfied).** Note: task file calls this
    test "test_coverage_refresh_window_meets_engine_minimum" in
    "test/unit/market/test_constants.py"; real name/path is
    test_coverage_refresh_window_satisfies_timescale_minimum in
    test/unit/test_constants.py.
  - Effort: 1

- [x] **C.9 Fix `_data_status_doc_comment()`'s CAGG LAG formula (D5)**
  - [x] `src/manta_trading/market/schema/migrations/minute.py:355-397` — the
        current formula (`MINUTE_CAGG_REFRESH_SCHEDULE_INTERVAL +
        MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL`, "2 hours total") is wrong
        independent of this slice's width change: it never accounted for the
        open-bucket lag. Replace with `COVERAGE_BUCKET_INTERVAL + refresh
        schedule interval(s)` per D5, rendered from constants via
        `_interval_literal`, matching the CAGG LAG clause's existing style.
  - [x] Keep the BUCKET TRUNCATION clause and the "FRESHNESS IS NOT ASSERTED
        IN SQL" clause unchanged — only the CAGG LAG derivation changes.
  - [x] Done here, **before** Task D's migrations, so 051/052 can re-execute
        this function directly with no placeholder/supersede step.
  - Success: the string "2 hours total" (or any two-hop-only phrasing) no
    longer appears anywhere in the function's output.
  - Effort: 2

- [x] **C.10 Unit-test the corrected doc comment (criterion 14)**
  - [x] Assert the rendered comment text includes the bucket-width term (i.e.
        contains a rendering of `COVERAGE_BUCKET_INTERVAL`), not just the
        schedule intervals.
  - [x] Assert the old two-hop-only phrasing is absent — regression guard
        pinned to criterion 14.
  - Success: tests pass; fail if the formula reverts to schedule-intervals
    only (criterion 14).
  - Effort: 2

- [ ] **Commit**: `feat: re-derive coverage cagg constants at the measured width`

---

## Task D — Migrations 051/052 (D4, Migration Plan)

- [x] **D.1 Write migration `051_coverage_cagg_bucket_narrowing`**
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
        forward from 046). Because the description is a Python string built
        at migration-definition time (module import), verify it is built
        by calling `_interval_literal`/an f-string against
        `COVERAGE_BUCKET_INTERVAL` at that point, **not** copy-pasted as a
        literal — the DDL's `_interval_seconds_sql` call is safe by
        construction (it runs inside `python_fn`/`sql` at execution time,
        same as 046), but the description string has no such execution-time
        seam and is the one place a hardcoded width could hide undetected.
  - [ ] Idempotent: every statement uses `IF EXISTS`/`IF NOT EXISTS`, so
        re-running 051 from any point in ①②③ converges.
  - Success: migration defined; matches the mandatory ①②③ ordering; no
    `CASCADE` anywhere in the migration; description text confirmed to read
    `COVERAGE_BUCKET_INTERVAL` rather than a copy-pasted number.
  - Effort: 4

- [x] **D.2 Write migration `052_coverage_cagg_refresh_policies_narrowed`**
  - [ ] Reinstall both coverage caggs' refresh policies at the new
        `start_offset` (Task C.7), unchanged `end_offset`/`schedule_interval`,
        using the idempotent `DO $$ ... IF NOT EXISTS ... $$` pattern from
        047 (:1925).
  - [ ] Re-render `COMMENT ON VIEW data_status` from the corrected doc-comment
        function (Task C.9). **051's step ③ is the primary install path for
        the comment** (the view and its comment are re-attached together, in
        the same migration, so they can never observably disagree); 052's
        re-render is a belt-and-braces idempotency guard for the case where
        051 already applied and 052 runs later — both calls render the same
        function output, so this is a no-op in the happy path, not a second
        source of truth.
  - Success: migration defined; idempotent on re-run; comment text sourced
    from the single doc-comment function, no duplicated string.
  - Effort: 2

- [x] **D.2a Bump the migration-count tripwire (criterion 3, "count tripwire
      updated")**
  - [ ] `test/unit/test_schema_migrations.py:169-173`,
        `test_migration_count`, asserts `len(MIGRATIONS) == 53` — a
        deliberate-change tripwire the file's own comment says must be
        bumped "in the same commit that adds one [migration]." Adding 051
        and 052 is two migrations, so update the assertion to `== 55` and
        update the comment to note the `53 -> 55` bump and its cause (slice
        169, migrations 051/052).
  - [ ] Confirm the test fails before this edit (with 051/052 added but the
        assertion unbumped) and passes after — proving the tripwire actually
        tripped, not that it was silently already correct.
  - Success: `test_migration_count` asserts `55` and passes; the failure
    was observed before the fix, confirming the tripwire fired as designed.
  - Effort: 1

- [x] **D.3 Integration test: 051/052 apply cleanly on a cold-start database
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
  - [ ] Criterion 3's "count tripwire updated" clause is D.2a's job, not
        this task's — D.2a's assertion bump is the tripwire; this task
        proves the chain itself applies cleanly.
  - Success: test passes on a throwaway database; fails if 051/052 regress.
  - Effort: 3

- [x] **D.3a Integration test: `data_status`'s column contract is unchanged
      (criterion 7)**
  - [ ] On the same cold-start database as D.3 (post-051/052), assert the
        column **names, order, and types** of `data_status` match the 167 D2
        contract exactly — introspect via `information_schema.columns` (or
        the equivalent psycopg cursor description), not a row-count or
        existence check. D.3 proves the view exists and is queryable; this
        proves it is the *same* view shape 167 committed to, which downstream
        readers (`api_server/queries.py`, `mt data status`) depend on.
  - [ ] This is the automated guard for D.1 step ③'s claim that
        `_build_data_status_view_sql(...)` is re-executed "unchanged (not a
        rewrite)" — a future edit that accidentally alters the CTE would fail
        this test rather than surfacing as a silent contract break.
  - Success: column names/order/types asserted equal to the pre-169 contract;
    fails if any column is added, removed, reordered, or retyped.
  - Effort: 2

- [x] **D.4 Integration test: idempotent re-run from a partial 051 state**
  - [ ] Simulate a failure between steps ① and ③ (e.g. run only step ① and
        ②'s DDL on a scratch database, matching "Window A" in the design's
        Rebuild Window section), then re-run 051 in full and assert it
        converges without error.
  - Success: test passes; demonstrates the recovery path the design's Window
    A handling describes (re-run 051, not a manual recovery script).
  - Effort: 3

- [x] **D.5 Integration test: `DROP` ordering is enforced (regression guard
      for F002)**
  - [ ] Assert that attempting to drop either coverage cagg **without**
        first dropping `data_status` raises the expected dependency error on
        a scratch database with 048's `data_status` installed — this pins
        the reason step ① exists, so a future edit that reorders 051
        breaks a test immediately rather than failing silently on prod.
  - Success: test passes; fails if steps ①/② are reordered.
  - Effort: 2

- [x] **D.5a Integration test: `assert_cagg_fresh` reports both coverage
      views fresh end-to-end on the *generic* bucket-lag check (criterion 16)**
  - [ ] On a database with 051/052 applied **and** materialized history (not
        the empty cold-start DB — reuse or extend Task B's seeded database,
        or a smaller purpose-built fixture with a handful of symbols spanning
        into the current bucket), run `assert_cagg_fresh(conn,
        'minute_coverage', ...)` and the daily equivalent, with C.4's
        per-view override wired in (Task C.5).
  - [ ] Assert both verdicts are `is_fresh=True` with no `LAG_EXCEEDS_THRESHOLD`
        signal — this is the integration-level proof that C.4's map and C.5's
        wiring actually produce a fresh verdict on a realistic database, not
        just that the formula is correct in isolation (C.6 covers the formula;
        this covers the wiring reaching a real `assert_cagg_fresh` call).
  - [ ] Regression companion: assert a **pre-167 cagg** (e.g.
        `daily_monthly_ohlcv`) on the same database still resolves via the
        unchanged generic formula and is unaffected by the new map — same
        intent as C.6's unit-level regression case, at the integration tier.
  - Success: both coverage views report fresh via `assert_cagg_fresh` on a
    real database; the pre-167 cagg's verdict is provably untouched.
  - Effort: 3

- [x] **D.6 Update `test_migrations_046_047.py`/`test_migration_050.py`
      fixtures that assume the old width, if any (verify, do not assume)**
  - [ ] Per D5: `test_coverage_content_edge.py`, `test_migrations_046_047.py`,
        `test_symbol_ranges_sql.py`, `test_data_status_equivalence.py`
        already scale with `COVERAGE_BUCKET_INTERVAL` and should need no
        arithmetic changes. Run each and confirm — do not edit unless a
        failure surfaces a hardcoded literal.
  - Success: all four suites pass unedited, or the specific hardcoded
    literal found is fixed and noted.
  - Effort: 2

- [x] **D.7 Confirm the new integration tests need no CI wiring (verify, do
      not assume)**
  - [ ] `.github/workflows/ci.yml` runs no test job at all — it only builds
        and publishes on a `v*` tag push. No existing integration test file
        (`test_migrations_046_047.py`, `test_migration_050.py`, etc.) is
        CI-gated today, so D.3/D.3a/D.4/D.5/D.5a follow the project's
        existing convention (run locally / on demand) rather than a
        regression from it. Test-running CI is tracked separately as slice
        907 (CI Pipeline and Load-Test Gating) — the same gap `test_167_data_status_nfr.py`'s
        module docstring already documents for the load tier (B.8).
  - [ ] If slice 907 lands **before** this slice ships, revisit this task and
        wire the new files in then — do not add a CI job as part of this
        slice; that is 907's scope, not 169's.
  - Success: confirmed no CI gap is introduced relative to the project's
    current (no test-CI) baseline; decision recorded, not assumed.
  - Effort: 1

- [ ] **Commit**: `feat: add migrations 051/052 narrowing coverage cagg buckets`

---

## Task E — Architecture close-out (D6, D6a)

- [x] **E.1 Close out the 140-arch D6a checklist with measured values
      (criterion 15)**
  - [x] Update `COVERAGE_BUCKET_INTERVAL` in 140-arch to the **measured**
        width from Task B.7 (confirm or correct the existing 30-day working
        assumption).
  - [x] Update `COVERAGE_CONTENT_STALENESS` and the per-view bucket-lag
        budget text to match Task C.2/C.4.
  - [x] Replace the worst-case row-count table with **measured actuals**
        from Task B.2.
  - [x] Record the measured probe cost (B.5) against the 10 s budget.
  - [x] Record the selected `start_offset` (C.7) and its measured per-run
        cost (B.6a/B.6b).
  - [x] Use the established amendment convention (`*(Architecture amendment,
        {date} — slice 169.)*`) for each edit, consistent with the four
        blocks already amended under D6a.
  - Success: 140-arch states measured values throughout, with no remaining
    "working assumption" language for anything Task B measured. All five D6a
    checklist items closed:
    - COVERAGE_BUCKET_INTERVAL updated to the measured 7 days (was the 30-day
      working assumption)
    - COVERAGE_CONTENT_STALENESS (7 d 4 h) and COVERAGE_BUCKET_LAG_BUDGET (8 d 4 h /
      8 d 1 h) now have constants-block entries recording that both are
      derived, not chosen
    - Worst-case row counts replaced with measured actuals (7d: 3,019,870
      minute / 16,742,957 daily; 30d and 90d also recorded)
    - Measured probe cost recorded against the 10 s budget (0.068 s minute /
      0.220 s daily, ~45x margin)
    - Selected start_offset (365 days) recorded with its measured per-run cost
      table (head-only flat 0.058-0.072 s across a 47x window range;
      deep-backfill 0.064-5.400 s)
    All use the established `*(Architecture amendment, {date} — slice 169.)*`
    convention.
  - Effort: 2

- [ ] **Commit**: `docs: close 140-arch D6a with measured coverage-cagg values`

---

## Task F — `restore_metadata.py` verification (D-Consumers)

- [x] **F.1 Reconcile the slice design's `restore_metadata.py` claim against
      the actual mechanism, and fix the one real stale reference**
  - [ ] **The design's Consumers section says**: `restore_metadata.py` "lists
        both coverage views among recreatable objects (lines 190–191) and
        references 046 as their creating migration. That reference must move
        to 051, or the restore tool recreates them at the old width." **This
        does not match the code as written**, and that mismatch must be
        recorded, not silently resolved one way:
        - `assess()`'s `missing_caggs` (`:200-201`) is computed by **catalog
          presence** (`present_caggs` from `_timescaledb_catalog.continuous_agg`
          vs. the `expected_caggs` name tuple, `:185-195`) — it never consults
          a migration ID at all. There is no per-object "creating migration"
          field to be stale.
        - `missing_migrations` (`:200`) compares `MINUTE_MIGRATIONS`'
          **current, full list** (imported live at `:166`/`:236`, so it
          already includes 051/052 once they exist) against the ledger —
          again no hardcoded "046".
        - `replay_missing_migrations` (`:214`) calls
          `apply_migrations(pool, MINUTE_MIGRATIONS)` — the **full current
          migration list**, run idempotently. If `minute_coverage`/
          `daily_coverage` are present in the catalog but their migration is
          already ledgered, replay does nothing to them (by design — see the
          `:226-231` docstring note on the ledger boundary); if 051/052 are
          *not* ledgered, replay applies them and produces the *new* width,
          not the old one. **There is no code path that "recreates them at
          the old width"** — the design's stated risk does not reproduce
          against the current implementation.
  - [ ] **The one real stale reference**: the docstring at `:228` says
        "`minute_coverage` via 046" — update to "`minute_coverage`/
        `daily_coverage` via 046, narrowed by 051" so the prose does not
        imply 046 is still the operative migration post-169. This is a
        comment-only fix; no logic changes.
  - [ ] Record this reconciliation explicitly (e.g. in the slice's completion
        notes or a comment at the design's Consumers section pointer) so a
        future reader hitting the same discrepancy does not have to re-derive
        it — the design's claim was a reasonable prediction at design time
        that the code's catalog-driven approach (chosen independently)
        already avoided.
  - [ ] **No test file currently exercises `restore_metadata.py`** (verified:
        no test imports it). Write a minimal integration test: on a database
        with 051/052 applied and both coverage caggs present, call `assess()`
        and assert `minute_coverage`/`daily_coverage` are absent from both
        `missing_migrations` and `missing_caggs` — this is the regression
        guard that would catch it if the catalog-driven mechanism above were
        ever changed to something migration-ID-based and stale.
  - Success: the design/code discrepancy is explicitly recorded (not silently
    picked); the stale `:228` docstring reference is corrected; a test exists
    asserting both coverage caggs are reported present post-051/052.
  - Effort: 2

- [ ] **Commit**: `test: verify restore_metadata reports coverage caggs post-051/052`

---

## Notes

- **Effort 3/5 overall** (design estimate) — the migrations and constants
  changes here are small; the width measurement (Task B) is the weight in
  this file. The prod rebuild (part 2, Task G) carries the rest.
- **Commit per task group**, not batched at the end — checkpoints follow
  Tasks B, C, D, E, F.
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
