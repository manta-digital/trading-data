---
docType: tasks
slice: cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr
project: trading-data
lldReference: user/slices/167-slice.cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr.md
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [166, 163, 168]
interfaces: [147, 182]
dateCreated: 20260726
dateUpdated: 20260726
status: not_started
---

# Tasks: Cagg-backed `data_status` bars summary — reach the sub-second NFR

## Context Summary

`data_status` full-universe read is **7.8 s** post-slice-166 against a
**sub-second** NFR. The residual cost is structural: the view's `bars_summary`
CTE does a per-symbol `MIN(time)/MAX(time)/COUNT(*)` over the entire raw
`minute_ohlcv` (4,405,379,285 rows) and `daily_ohlcv` (34,223,492 rows) on every
read. No chunk tuning removes a full per-symbol aggregate at that scale.

This slice introduces two **hierarchical continuous aggregates** —
`minute_coverage` (over `minute_4hour_ohlcv`) and `daily_coverage` (over
`daily_ohlcv`) — with 1-year buckets, so `bars_summary` groups ~15k rows instead
of billions. Grouping is then sub-millisecond *regardless of the parent cagg's
chunk count*, which is the durability argument for D1 Option 1 over grouping the
4h cagg directly (5.7 s, and re-couples the NFR to chunk health).

**Prerequisites are satisfied:** 166 (raw rechunk) and 163 (cagg repair +
re-chunk) are complete, and 168 delivered `assert_cagg_fresh`. This slice
**consumes** that helper unchanged — it must not reimplement it, and must not
ship a second unguarded cagg consumer.

### Key inputs (verified against the tree, 2026-07-26)

| Fact | Value |
|---|---|
| View builder | `_build_data_status_view_sql(...)` at [minute.py:171](src/manta_trading/market/schema/migrations/minute.py#L171) |
| Highest existing migration | `045_minute_cagg_columnstore` → **new migrations are 046, 047** |
| Cagg-creation pattern | migration `033_create_minute_caggs` / `034_create_daily_caggs`: `requires_autocommit: True`, one `execute()` per `CREATE MATERIALIZED VIEW` |
| Policy pattern | `035_cagg_refresh_policies`, `037_widen_minute_cagg_refresh_offsets` |
| Primary read path | `fetch_status_rows` / `fetch_all_health_counts` in [status_queries.py](src/manta_trading/data/maintenance/status_queries.py) |
| Other reader | [migrate_cold_start.py:300](src/manta_trading/data/quality/migrate_cold_start.py#L300) (`SELECT COUNT(*) FROM data_status`) |
| Renderer | [status_table.py](src/manta_trading/cli/rendering/status_table.py) — `_fmt_date` is **date-only** (`%Y-%m-%d`) |
| Freshness helper | `assert_cagg_fresh(conn, view_name, *, now=..., source_table=None) -> FreshnessVerdict` in `market/maintenance/cagg_freshness.py` |

### PM decisions taken at task breakdown (resolving review concerns)

- **F002 (guard placement) → guarded accessor module.** `assert_cagg_fresh` is a
  Python helper; `bars_summary` is a SQL CTE, so the guard cannot live "in" it.
  A new `data/maintenance/status_coverage.py` becomes the **single guarded door**
  to `data_status`. `status_queries.py` and `migrate_cold_start.py` are migrated
  onto it in this slice; slice 182 is contractually required to use it. This is
  what makes "no second unguarded consumer" true rather than aspirational.
- **F003 (equivalence definition) → date-normalized comparison.** Bucket
  truncation is a permanent delta for *all* history, not a trailing-edge effect,
  so literal row-by-row equality would fail on every symbol. Equivalence is:
  date-normalized timestamps equal, `bars_stored` exactly equal, raw−cagg
  timestamp delta < 4 h. This tests the actual user-visible contract, since the
  CLI renders date-only.
- **F001 (arch amendment) → in scope**, task 1.1.
- **F004 (misattributed example) → in scope**, task 1.2 (doc correction only).
- **D5 load test → confirmed in scope** (criterion 6), section 8.

### Task-review findings folded in (167-review.tasks, 2026-07-26)

- **F001 (no commit checkpoints) → fixed.** `**Commit**` checkpoints added after
  every section, matching the 168 precedent. Commit per task, not batched.
- **F002 (load test "CI-gated" was hand-waving) → fixed.** The real gate is
  `MT_RUN_LOAD_TESTS=1` (`test/load/test_146_part1_nfrs.py`); `.github/workflows`
  does not exist. Task 8.1.4 names the actual mechanism and 8.3 forces an
  explicit PM call rather than repeating 146's unwired claim.
- **F003 (doc-comment test asserted but not tasked) → fixed.** Promoted to task
  4.4; section 7 covers `bars_summary` output, not `COMMENT ON VIEW` content.

### Constraints carried in

- **No magic strings.** Cagg view names, bucket widths, and refresh offsets are
  constants in `constants.py` (slice 166 `MINUTE_OHLCV_CHUNK_INTERVAL` pattern),
  referenced everywhere — never inlined in SQL or tests.
- **Hierarchical policy offsets must not repeat the 1-day trailing mistake**
  (D4). A too-narrow `start_offset` on a hierarchical cagg is exactly what
  caused the ~79% under-materialization this slice's prerequisite had to repair.
- **Prod query discipline.** Every prod query under an explicit
  `statement_timeout`; `export PGCONNECT_TIMEOUT=10`; `.env` values are
  double-quoted (`tr -d '"'`). `psql -c` with multiple statements runs in ONE
  transaction — `refresh_continuous_aggregate` must be its own `-c`.
- **Column contract preserved exactly** (D2) — no new/renamed/reordered columns.

---

## 1. Documentation corrections (review findings)

- [ ] **1.1** Amend `140-arch.data-quality-operations.md` for the `data_status`
      source change (review F001). Effort: 1/5
  - [ ] 1.1.1 Locate the "One status view" section defining `bars_summary` as
        `MIN(time)`/`MAX(time)`/`COUNT(*)` **from the data table**, and the claim
        "A view, not a table. Always consistent with the underlying data."
  - [ ] 1.1.2 Add an amendment following the established convention (the
        2026-07-20 `mt data caggs` amendment for slices 154/163 — match its
        heading style and dated format).
  - [ ] 1.1.3 Amendment states: `bars_summary` derives from coverage caggs as of
        slice 167; the view is consistent **within a documented and asserted
        staleness bound**, not unconditionally; timestamps are bucket-truncated.
  - [ ] 1.1.4 Success: arch no longer describes a view that does not exist; the
        original text is amended, not deleted.
- [ ] **1.2** Correct D3a's misattributed example in the 167 slice design
      (review F004). Effort: 1/5
  - [ ] 1.2.1 D3a cites "the daily caggs, whose offsets run to 21/90/270 days" —
        but per D1 the daily branch reads the **new `daily_coverage` cagg**, not
        `daily_weekly`/`daily_monthly`/`daily_quarterly`.
  - [ ] 1.2.2 Rewrite the example to reference `daily_coverage`'s own offset
        (chosen in task 2.2). Keep the `min(start_offset, ceiling)` conclusion —
        it is correct and independently codified as
        `MAX_COVERAGE_SOURCE_STALENESS`.
  - [ ] 1.2.3 Record in the design's decisions the two PM rulings above (F002
        accessor module, F003 date-normalized equivalence), and restate success
        criterion 2 per F003 so the criterion and D3 no longer contradict.

- [ ] **Commit**: `docs: amend 140-arch data_status spec for cagg-backed bars_summary`

---

## 2. Constants and refresh-policy parameters

- [ ] **2.1** Add coverage-cagg constants to `constants.py`. Effort: 1/5
  - [ ] 2.1.1 View names as constants (e.g. `MINUTE_COVERAGE_VIEW`,
        `DAILY_COVERAGE_VIEW`) — these are passed to `assert_cagg_fresh` and used
        in tests, so they must exist in exactly one place.
  - [ ] 2.1.2 `COVERAGE_BUCKET_INTERVAL` (1 year) for both coverage caggs.
  - [ ] 2.1.3 Follow the existing `MINUTE_OHLCV_CHUNK_INTERVAL` declaration style
        and place them with the related cagg constants, not at file end.
  - [ ] 2.1.4 Success: no coverage view name or bucket width appears as a literal
        anywhere in `src/`.
- [ ] **2.2** Choose and justify the coverage refresh-policy offsets (D4).
      Effort: 2/5
  - [ ] 2.2.1 Read the parent policies on prod first — `minute_4hour_ohlcv`
        (`start_offset` 1 day per 168's verified catalog facts) and `daily_ohlcv`'s
        relevant policy. Record actual values; do not assume.
  - [ ] 2.2.2 `start_offset` for each coverage cagg must be **at least the
        parent's refresh window plus margin**, so a parent bucket that changes
        after the coverage cagg last ran is still re-materialized. A trailing
        1-day offset on a hierarchical cagg is the exact D4 hazard.
  - [ ] 2.2.3 Add as constants (`MINUTE_COVERAGE_REFRESH_*`,
        `DAILY_COVERAGE_REFRESH_*`: start_offset, end_offset, schedule_interval).
  - [ ] 2.2.4 Write the chosen values and the reasoning into the design's D4,
        replacing "exact offsets are a task-level decision".
  - [ ] 2.2.5 Success: each offset traceable to a measured parent value, not a
        round number picked by feel.
- [ ] **2.3** Unit-test the constants block. Effort: 1/5
  - [ ] 2.3.1 Assert coverage `start_offset` ≥ parent refresh interval + margin
        (encodes the D4 constraint mechanically so a later edit can't silently
        reintroduce the 1-day bug).
  - [ ] 2.3.2 Assert bucket interval and view names are non-empty and typed as
        the module's other interval constants are.

- [ ] **Commit**: `feat(constants): add coverage cagg names, bucket, and refresh offsets`

---

## 3. Migration 046 — coverage continuous aggregates

- [ ] **3.1** Add migration `046_create_coverage_caggs`. Effort: 3/5
  - [ ] 3.1.1 Follow `033_create_minute_caggs` exactly: `requires_autocommit:
        True`, `python_fn` issuing **one `execute()` per `CREATE MATERIALIZED
        VIEW`** (Timescale rejects multiple cagg DDL statements per call).
  - [ ] 3.1.2 `minute_coverage` over **`minute_4hour_ohlcv`** (hierarchical):
        `time_bucket(<COVERAGE_BUCKET_INTERVAL>, time_bucket) AS yr_bucket,
        symbol, SUM(minute_count) AS bars, MIN(time_bucket) AS first_bucket,
        MAX(time_bucket) AS last_bucket GROUP BY yr_bucket, symbol`.
  - [ ] 3.1.3 `daily_coverage` over **`daily_ohlcv`** (raw, not a cagg):
        analogous, with `COUNT(*) AS bars` and `MIN(time)`/`MAX(time)` — note the
        daily branch reads raw, so its timestamps are **exact, not truncated**.
        Record that asymmetry in the migration description.
  - [ ] 3.1.4 `CREATE MATERIALIZED VIEW IF NOT EXISTS` for idempotency, matching
        033/034.
  - [ ] 3.1.5 Success: `mt data migrate apply` creates both; both appear in
        `timescaledb_information.continuous_aggregates`;
        `minute_coverage` is confirmed hierarchical (parent = `minute_4hour_ohlcv`).
- [ ] **3.2** Add migration `047_coverage_cagg_refresh_policies`. Effort: 2/5
  - [ ] 3.2.1 `add_continuous_aggregate_policy` for each coverage cagg using the
        2.2 constants. Follow `035_cagg_refresh_policies`.
  - [ ] 3.2.2 Idempotent on re-apply (existing policy must not raise) — match how
        035/037 handle it.
  - [ ] 3.2.3 Success: both jobs present in `timescaledb_information.jobs`,
        `scheduled = true`, offsets equal to the constants.
- [ ] **3.3** Integration-test migrations 046/047 on a scratch DB. Effort: 2/5
  - [ ] 3.3.1 Apply on a throwaway DB; assert both caggs and both policies exist
        with the expected offsets.
  - [ ] 3.3.2 Re-apply; assert idempotent (no error, no duplicate policy).
  - [ ] 3.3.3 Seed a small known raw fixture, refresh through the hierarchy, and
        assert `SUM(bars)` from `minute_coverage` equals the raw bar count for the
        seeded range — proves the hierarchical rollup arithmetic, catching a wrong
        `SUM(minute_count)` vs `COUNT(*)` choice.
  - [ ] 3.3.4 Never touch a production job from a test (168's precedent).

- [ ] **Commit**: `feat(schema): add coverage continuous aggregates and refresh policies`

---

## 4. View rewrite — cagg-backed `bars_summary`

- [ ] **4.1** Extend `_build_data_status_view_sql` with a cagg-backed variant.
      Effort: 3/5
  - [ ] 4.1.1 Add a flag (e.g. `cagg_backed_bars_summary: bool = False`) rather
        than duplicating the SQL string — the builder already carries
        `include_daily_branch` / `include_trading_sessions_cte` this way.
  - [ ] 4.1.2 Minute branch: `SELECT 'minute', symbol, MIN(first_bucket),
        MAX(last_bucket), SUM(bars) FROM minute_coverage GROUP BY symbol`.
  - [ ] 4.1.3 Daily branch: analogous over `daily_coverage`.
  - [ ] 4.1.4 **Everything else verbatim** — `symbols_x_granularity`,
        `gap_counts`, `exchange_completed_close`, the `health` CASE, all joins,
        all output columns in the same order. The doubled-quote (`''`) escaping
        convention of the existing builder must be preserved.
  - [ ] 4.1.5 Add module-level rendered constants for the new variant alongside
        `_DATA_STATUS_VIEW_WITH_DAILY_TS` etc.
  - [ ] 4.1.6 Success: generated SQL differs from the current variant **only**
        inside the `bars_summary` CTE (diff the two strings to prove it).
- [ ] **4.2** Unit-test the builder. Effort: 2/5
  - [ ] 4.2.1 Assert the cagg-backed variant references `minute_coverage` /
        `daily_coverage` and does **not** reference `minute_ohlcv` /
        `daily_ohlcv` in its `bars_summary`.
  - [ ] 4.2.2 Assert the output column list and order are byte-identical between
        raw and cagg-backed variants (the D2 contract, mechanically pinned).
  - [ ] 4.2.3 Assert the non-`bars_summary` CTEs are unchanged.
  - [ ] 4.2.4 Assert view names come from constants, not literals.
- [ ] **4.3** Add migration `048_data_status_cagg_backed`. Effort: 2/5
  - [ ] 4.3.1 `CREATE OR REPLACE VIEW data_status` with the new variant.
  - [ ] 4.3.2 Re-use the migration-021 `to_regclass` DO-block branching so
        cold-start and existing DBs converge on the same definition.
  - [ ] 4.3.3 Add the D3 doc comment via `COMMENT ON VIEW data_status`: bucket
        truncation (minute timestamps truncated to 4 h bucket start; daily exact),
        the two-hop cagg-lag bound with the **chosen numeric intervals** from 2.2,
        and that freshness is asserted at the accessor, not in SQL (criterion 4).
  - [ ] 4.3.4 Success: `COMMENT ON VIEW` is retrievable via `obj_description`.
- [ ] **4.4** Integration-test the view doc comment (criterion 4). Effort: 1/5
  - [ ] 4.4.1 After applying 048 on a scratch DB, read the comment via
        `obj_description('data_status'::regclass)`.
  - [ ] 4.4.2 Assert it is non-empty and mentions **both** documented bounds —
        bucket truncation and cagg lag — and the chosen refresh intervals from
        2.2. Criterion 4 is otherwise unverifiable; section 7 tests
        `bars_summary` output, not comment content.
  - [ ] 4.4.3 Assert against the 2.2 constants, not hard-coded interval literals.

- [ ] **Commit**: `feat(schema): back data_status bars_summary with coverage caggs`

---

## 5. Guarded accessor — the single door to `data_status` (review F002)

- [ ] **5.1** Create `data/maintenance/status_coverage.py`. Effort: 3/5
  - [ ] 5.1.1 Public accessor(s) wrapping reads of `data_status`; every Python
        reader goes through this module.
  - [ ] 5.1.2 Call `assert_cagg_fresh(conn, MINUTE_COVERAGE_VIEW)` and
        `assert_cagg_fresh(conn, DAILY_COVERAGE_VIEW)` before returning rows.
        **Consume 168's helper unchanged** — do not reimplement, do not copy its
        signal logic.
  - [ ] 5.1.3 Return the rows **plus** the freshness verdicts, so callers can
        surface staleness. Do not swallow a stale verdict and do not raise —
        `data_status` is operator-facing, so it **reports** (D3a on-trip
        behavior), unlike the daemon's coverage index which skips work.
  - [ ] 5.1.4 On trip, log at ERROR naming the cagg, measured lag, and which
        signals fired (D3a).
  - [ ] 5.1.5 Threshold is `min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)` —
        supplied by 168's helper; verify this slice passes whatever ceiling
        argument the helper expects rather than re-deriving it.
  - [ ] 5.1.6 **Do not auto-remediate.** No `refresh_continuous_aggregate` in a
        read path; catch-up stays with runbook R2.
  - [ ] 5.1.7 Success: 168's TTL verdict cache keeps repeat reads inside the NFR;
        no amortization scheme is added here.
- [ ] **5.2** Unit-test the accessor. Effort: 3/5
  - [ ] 5.2.1 Fresh verdict → rows returned, no ERROR log.
  - [ ] 5.2.2 Each of the four D3a signals independently produces a stale verdict
        that reaches the caller (fake/stub the helper; the signals themselves are
        168's tests — here assert **propagation**, not re-derivation).
  - [ ] 5.2.3 Explicitly cover the case a naive implementation misses: a cagg with
        a loose `start_offset` that is stalled far beyond this consumer's ceiling
        must still trip (criterion 7).
  - [ ] 5.2.4 Stale verdict → rows still returned **and** marked stale; assert it
        is never silently presented as current.
  - [ ] 5.2.5 Assert no `refresh_continuous_aggregate` is issued on any path.

- [ ] **Commit**: `feat(maintenance): add freshness-guarded data_status accessor`

- [ ] **5.3** Migrate `status_queries.py` onto the accessor. Effort: 2/5
  - [ ] 5.3.1 `fetch_status_rows` and `fetch_all_health_counts` read via
        `status_coverage`, not direct `FROM data_status` SQL.
  - [ ] 5.3.2 Preserve the existing filter behavior (symbol / health /
        granularity as AND conditions, single parameterized query) and the
        `StatusRow`/`GapRow` row-factory contract — no manual column indexing.
  - [ ] 5.3.3 Success: no `FROM data_status` string remains outside
        `status_coverage.py`; grep proves it.
- [ ] **5.4** Migrate `migrate_cold_start.py` verification onto the accessor.
      Effort: 1/5
  - [ ] 5.4.1 Replace the direct `SELECT COUNT(*) FROM data_status` at
        [migrate_cold_start.py:300](src/manta_trading/data/quality/migrate_cold_start.py#L300).
  - [ ] 5.4.2 Cold-start runs against a freshly-built DB where coverage caggs are
        legitimately empty/never-refreshed — confirm 168's cold-start semantics
        (never-run ≠ stale) make this pass, and test it. This is the most likely
        false-positive site in the slice.
  - [ ] 5.4.3 Leave the `EXPLAIN` plan capture intact.
- [ ] **5.5** Test the migrated consumers. Effort: 2/5
  - [ ] 5.5.1 `status_queries` tests pass unchanged in behavior (filters, shape).
  - [ ] 5.5.2 Cold-start verification passes on an empty-cagg DB without a false
        stale report.
- [ ] **5.6** Record the slice-182 contract. Effort: 1/5
  - [ ] 5.6.1 State in the 167 design (and the accessor's module docstring) that
        **any** reader of `data_status` — including 182's serving API — must go
        through `status_coverage`, and why (F002; no second unguarded consumer).
  - [ ] 5.6.2 Note for 182: the API may expose timestamps at full precision,
        where the up-to-4 h minute-side coarsening is visible, unlike the CLI's
        date-only rendering. 182 must decide how to present that; 167 only
        documents it.

- [ ] **Commit**: `refactor(maintenance): route all data_status readers through guarded accessor`

---

## 6. Staleness surfacing in `mt data status`

- [ ] **6.1** Surface a stale-coverage indicator in the CLI. Effort: 2/5
  - [ ] 6.1.1 Render an operator-visible indicator when a verdict is stale,
        naming the cagg and measured lag.
  - [ ] 6.1.2 **Must not change the column contract** (D2 / criterion 3) — no new
        column in the table. Use a footer/banner alongside the existing health
        counts.
  - [ ] 6.1.3 Honor `--json`: the staleness signal must be machine-readable
        there, as a sibling of the rows, not injected into row objects.
  - [ ] 6.1.4 Health classification is untouched — nothing in `gap_count` /
        `has_retry_exhausted` / `last_attempt_ts` logic reads `bars_summary`.
- [ ] **6.2** Test the surfacing. Effort: 2/5
  - [ ] 6.2.1 Fresh → no indicator; table output byte-identical to pre-slice.
  - [ ] 6.2.2 Stale → indicator present in both table and `--json`.
  - [ ] 6.2.3 Column layout unchanged in both cases (criterion 3).

- [ ] **Commit**: `feat(cli): surface stale coverage indicator in mt data status`

---

## 7. Equivalence verification (review F003 — date-normalized)

- [ ] **7.1** Integration test: cagg-backed vs raw-scan equivalence. Effort: 3/5
  - [ ] 7.1.1 Build both view variants against the same seeded DB.
  - [ ] 7.1.2 Assert per symbol/granularity, for settled history:
        `date(first_bar_ts)` and `date(last_bar_ts)` equal; `bars_stored`
        **exactly** equal; raw−cagg timestamp delta < 4 h.
  - [ ] 7.1.3 Cover covered, partially-covered, and **empty** symbols — the empty
        case must still yield `bars_stored = 0` via the LEFT JOIN + COALESCE, not
        a dropped row.
  - [ ] 7.1.4 Daily branch reads raw `daily_ohlcv`, so its timestamps must match
        **exactly** (no truncation allowance) — assert the stricter condition
        there, or a real daily regression hides behind the minute-side tolerance.
  - [ ] 7.1.5 Success: the test encodes the F003 definition; a literal-equality
        assertion is explicitly *not* used, with a comment saying why.
- [ ] **7.2** Test trailing-edge lag honesty. Effort: 2/5
  - [ ] 7.2.1 Insert new raw bars, read before refresh: coverage understates by at
        most the documented bound.
  - [ ] 7.2.2 After a refresh tick, values converge to exact (modulo truncation).

- [ ] **Commit**: `test(schema): date-normalized equivalence for cagg-backed data_status`

---

## 8. Load test — the NFR (D5, criterion 6)

- [ ] **8.1** Add `test/load/` NFR test for full-universe `data_status`.
      Effort: 3/5
  - [ ] 8.1.1 Assert full-universe read latency **< 1 s**. Latency assertion, not
        functional correctness (python rules: load tier).
  - [ ] 8.1.2 Realistic-scale fixture or a gated prod-shaped tier — a 10-symbol
        fixture proves nothing about a 5,871-symbol read. If full scale is
        impractical in CI, gate on a prod-shaped tier and **say so in the test
        docstring**; do not silently shrink the fixture and keep the assertion.
  - [ ] 8.1.3 Include the accessor's freshness guard in the measured path — the
        guard is part of the read now, so excluding it measures a path that
        doesn't exist.
  - [ ] 8.1.4 Gate on `MT_RUN_LOAD_TESTS=1` via
        `@pytest.mark.skipif(os.environ.get("MT_RUN_LOAD_TESTS") != "1", ...)`,
        matching the existing convention in
        [test_146_part1_nfrs.py:29](test/load/test_146_part1_nfrs.py#L29). Do
        not invent a second gating mechanism.
  - [ ] 8.1.5 Success: test fails if `data_status` regresses past 1 s.
- [ ] **8.2** Confirm the load tier does not run against prod by default.
      Effort: 1/5
- [ ] **8.3** Make "CI-gated" concrete rather than aspirational (criterion 6,
      review F002). Effort: 1/5
  - [ ] 8.3.1 **The repo has no CI config** — `.github/workflows` does not exist.
        Slice 146's load tests assert "CI must enable" in a docstring and it was
        never mechanically wired; repeating that leaves criterion 6 as implicit
        as 146 left it.
  - [ ] 8.3.2 Document the concrete invocation that satisfies the gate
        (`MT_RUN_LOAD_TESTS=1 uv run pytest test/load/`) in the test docstring
        and the slice's verification section, so the NFR has a runnable check
        even without CI.
  - [ ] 8.3.3 **Settled (PM, 2026-07-26): CI is out of scope for 167** and is
        filed as slice **907** (CI Pipeline and Load-Test Gating) in the 900
        band, which will also retire 146's stale "CI must enable" docstrings.
        Record criterion 6 as satisfied by the documented manual invocation, and
        state that plainly in the design — do **not** claim CI gating that does
        not exist. Reference 907 so the gap is tracked, not forgotten.

- [ ] **Commit**: `test(load): assert sub-second full-universe data_status read`

---

## 9. Production verification

> Run under prod query discipline: `export PGCONNECT_TIMEOUT=10`, explicit
> `statement_timeout` on every query, `.env` value de-quoted with `tr -d '"'`.
> Long runs need `run_in_background` (Bash tool caps at 2 min; macOS has no
> `timeout`). One statement per `psql -c` where autocommit is required.

- [ ] **9.1** Confirm the 163 prerequisite still holds. Effort: 1/5
  - [ ] 9.1.1 `SUM(minute_count)` over `minute_4hour_ohlcv` matches the raw count
        within the lag bound — **not ~21%**. Raw authoritative count is
        4,405,379,285.
  - [ ] 9.1.2 If it does not match, STOP and report — the slice's premise is void.
- [ ] **9.2** Apply migrations 046–048 to prod and materialize. Effort: 3/5
  - [ ] 9.2.1 Apply; expect the initial coverage materialization to be the long
        step — background it.
  - [ ] 9.2.2 Verify `minute_coverage` row count is in the expected ~15k range
        (~5,871 symbols × ~22 years). An order-of-magnitude miss means the bucket
        or GROUP BY is wrong.
  - [ ] 9.2.3 Verify `SUM(bars)` parity against raw within the lag bound.
- [ ] **9.3** Measure the NFR on prod. Effort: 2/5
  - [ ] 9.3.1 `\timing on`; `SELECT count(*) FROM data_status;` and a full
        `SELECT *`. Record vs the 7.8 s baseline (criterion 1).
  - [ ] 9.3.2 Measure through `mt data status` (accessor + guard included), not
        raw SQL alone — that is the operator-facing path.
  - [ ] 9.3.3 If not sub-second, capture `EXPLAIN (ANALYZE, BUFFERS)` before any
        fix. Diagnose from the plan; do not stack speculative changes.
- [ ] **9.4** Contract-unchanged check. Effort: 1/5
  - [ ] 9.4.1 `mt data status` and `mt data status --symbol AAPL` render an
        identical column layout to a pre-slice capture (diff captures).
- [ ] **9.5** Prove the guard fires by **inducing** staleness (criterion 7).
      Effort: 3/5
  - [ ] 9.5.1 On a **throwaway DB, never prod**: pause a coverage cagg's refresh
        policy, advance raw past the threshold, read, and confirm stale coverage
        is reported rather than presented as current.
  - [ ] 9.5.2 Reading code or asserting the helper is merely *called* does **not**
        satisfy criterion 7.
  - [ ] 9.5.3 Record the induced-staleness walkthrough in a note under
        `user/notes/`.
- [ ] **9.6** Cold-start verification (criterion 5). Effort: 2/5
  - [ ] 9.6.1 Throwaway DB → all migrations → `data_status` returns rows, is
        sub-second, and reports no false staleness on never-refreshed caggs.

- [ ] **Commit**: `docs: record slice 167 prod verification and induced-staleness walkthrough`

---

## 10. Close-out

- [ ] **10.1** Full test suite per-subpackage (whole-`test/` collection is broken
      by a missing `__init__.py`). Baseline on `main` is 2 pre-existing failures
      (`test_daily.py`, `test_outcomes.py`) + 12 live-DB errors
      (`test_equity_universe.py`) — this slice must not add to it. Effort: 1/5
- [ ] **10.2** `ruff` clean on touched files only — never lint
      `test/integration/` wholesale (~865 pre-existing errors). Effort: 1/5
- [ ] **10.3** CHANGELOG entry. Effort: 1/5
- [ ] **10.4** Update the 140 arch Constants section with the new coverage
      constants (matching how 168 amended it). Effort: 1/5
- [ ] **10.5** Record verification results (timings, row counts, induced-staleness
      outcome) in the slice design; set `status: complete`. Effort: 1/5
- [ ] **10.6** Check the slice's plan entry in
      `140-slices.data-quality-operations.md`. Effort: 1/5

- [ ] **Commit**: `docs: mark slice 167 complete`

> **Commit per task, not batched at the end** (project convention; 168
> precedent). The `**Commit**` checkpoints above are the intended granularity —
> each lands a coherent, buildable unit. Merging the slice branch is a workflow
> action, not a checklist item, and is deliberately not listed (PM ruling,
> slice 168).

---

## Success Criteria (from the slice design, as amended)

1. Full-universe `data_status` read is **sub-second** on prod `trading` DB.
2. Output is equivalent to the raw-scan version under the **date-normalized**
   definition (F003): dates equal for settled history, `bars_stored` exactly
   equal, minute timestamp delta < 4 h, daily timestamps exact; trailing edge
   within the documented lag bound.
3. `mt data status` output shape (columns, formatting) is **unchanged**.
4. The view carries a doc comment stating bucket-truncation and cagg-lag bounds
   and the chosen refresh intervals.
5. Cold-start applies the new migrations cleanly and yields a sub-second view.
6. A load test asserts full-universe read latency < 1 s, gated on
   `MT_RUN_LOAD_TESTS=1` per the existing `test/load/` convention, and is
   runnable via the documented invocation. No CI exists in this repo (review
   F002); standing it up is **out of scope for 167 and filed as slice 907**
   (PM, 2026-07-26). This criterion is met by the documented manual invocation —
   stated honestly, not claimed as automated.
7. `assert_cagg_fresh` is called on the coverage caggs via the guarded accessor
   and is **proven to fire by inducing staleness**, including the loose-offset /
   badly-stalled case.
8. Every Python reader of `data_status` goes through `status_coverage`; no
   second unguarded consumer ships (F002).
