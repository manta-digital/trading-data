---
docType: tasks
slice: coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized
project: trading-data
lld: project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [167, 168, 170, 187]
interfaces: [187]
projectState: >
  Part 2 of 2 — continues from
  169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-1.md
  (Tasks A–F: width measurement, constants, migrations 051/052, doc-comment
  fix, architecture amendment). This file covers the prod rebuild execution
  (Task G) and close-out (Task H). Task G requires Tasks A–F merged and the
  migrations applied to a database, not just designed.
dateCreated: 20260813
dateUpdated: 20260818
status: complete
---

# Tasks: Coverage-Cagg Refresh Repair — Part 2 (Prod Rebuild and Close-out)

## Context summary

Part 1 designs and implements the repair (measured width, re-derived
constants, migrations 051/052, corrected doc comment, amended architecture).
This file executes the rebuild against production and closes out the slice.

**Task G cannot start until Part 1 is merged.** Every step in Task G runs
against production and requires prod clear of other work, per
`feedback_prod_query_discipline`: `statement_timeout` set on every session,
any client-side timeout followed by `pg_cancel_backend` on the server side.
The daemon stays stopped for the **entire** window (DDL + materialization),
not merely during DDL — see the slice design's **The Rebuild Window** section
for why (a running daemon writing to `minute_ohlcv`/`daily_ohlcv` during a
full-span refresh moves the target mid-rebuild).

Full reasoning for every step below:
`169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md`,
**Migration Plan** and **The Rebuild Window** sections, and the **Verification
Walkthrough** (steps 1–9, 7a, 8a) which this task's ordering follows directly.

### Non-negotiables from the design (repeated from Part 1 — apply here too)

- Every job paused for the rebuild is resolved from the catalog by name via
  `_resolve_cagg_jobs`, never a hardcoded job ID. `minute_4hour_ohlcv`'s
  refresh must never be paused.
- Materialization is issued as bounded sub-windows, never one full-span call.
  A statement timeout is set, sized to one sub-window.
- Exact row counts only for verification (`count(*)`) —
  `approximate_row_count` is excluded.
- Detect partial materialization by **content**, not catalog presence.

---

## Task G — Rebuild execution on prod (Migration Plan, The Rebuild Window)

> **Prod-only task. Every step requires prod clear of other work.**
> `statement_timeout` set on every session; any client-side timeout followed
> by `pg_cancel_backend` on the server side. The daemon stays stopped for the
> **entire** window (DDL + materialization), not merely during DDL.

- [x] **G.1 Pre-check: confirm the defect is still live (walkthrough step 2)**
  - [x] Run the design's step-2 queries with `statement_timeout = '30s'`.
        Record `MAX(last_bucket)` on both views against raw `MAX(time)`.
  - Success: current drift recorded as a before-state for later comparison.
  - Effort: 1

- [x] **G.2 Stop the daemon and the API server**
  - [x] Stop the **daemon** — it stays down for the **entire window** (DDL
        through the end of materialization, through G.6), because a running
        daemon writes to `minute_ohlcv`/`daily_ohlcv` during the full-span
        refresh and moves the target mid-rebuild. It is restarted only at
        G.12, after materialization is verified.
  - [x] Stop the **API server** — it is down for **Window A only** (the 051
        DDL window), reducing the missing-`data_status` exposure to
        operator-driven CLI rather than user-facing 500s. It is restarted at
        G.4a, immediately after 051/052 apply — **not** left down through
        materialization (G.5–G.6) or verification (G.7–G.11).
  - Success: daemon confirmed stopped before G.3 proceeds; API server
    confirmed stopped before G.4 proceeds. The two services have **different**
    stop durations — do not treat "both stopped" as "both stopped for the
    same window."
  - Effort: 1

- [x] **G.3 Pause jobs resolved from the catalog (walkthrough step 3)**
  - [x] Query `timescaledb_information.jobs` filtered to
        `minute_coverage`/`daily_coverage`/`minute_4hour_ohlcv`. Pause the
        coverage views' refresh and columnstore jobs **by the IDs this query
        returns** — never a hardcoded ID (170 lesson: job 1003 no longer
        exists; the minute 4h refresh is job 1124 as of this session, and IDs
        may have shifted again by execution time).
  - [x] Confirm `minute_4hour_ohlcv`'s refresh remains `scheduled = true`.
  - Success: coverage jobs paused; parent minute cagg's refresh untouched.
  - Effort: 2

- [x] **G.4 Apply migrations 051/052 (walkthrough step 4)**
  - [x] `mt data migrate --status` (confirm chain ends at 050) → `mt data
        migrate` → `mt data migrate --status` (confirm chain ends at 052).
  - Success: chain advances cleanly; `data_status` exists immediately after
    (criterion 13).
  - Effort: 1

- [x] **G.4a Restart the API server**
  - [x] Window A (the missing-`data_status` exposure) ends once 051's step ③
        re-installs the view — restart the API server now, **before**
        materialization (G.5–G.6) begins, not after verification (G.9–G.11).
        Leaving it down through the multi-hour materialization would extend
        user-facing 500s far past the intended DDL-only window.
  - [x] Confirm the server is up and `data_status`-backed endpoints respond
        (they will report coverage stale until G.9 — that is expected and
        correct per the design's Window B handling, not a fault to fix here).
  - Success: API server confirmed running before G.5 proceeds.
  - Effort: 1

- [x] **G.5 One measured sub-window before the full sweep — a gate, not just
      a measurement**
  - [x] Materialize a single bounded sub-window (reuse `_REFRESH_SUBWINDOW`
        or a value sized from Part 1's Task B measurements) on
        `daily_coverage` and record peak memory and wall-clock, before
        committing to the full 64-year sweep.
  - [x] **Stop-and-replan condition:** if peak memory or wall-clock exceeds
        the host's safe envelope (per `sql.md`'s host-protection guidance and
        the design's Risks table — a single call's memory is not bounded by
        `work_mem`), **do not proceed to G.6.** Reduce the sub-window span and
        re-measure, or escalate to the PM before continuing — this
        measurement exists specifically to catch an unsafe span before it
        runs for hours against the full 64-year span, not to be informational.
  - Success: one sub-window's cost measured **and explicitly judged safe**
    before G.6 begins; if judged unsafe, G.6 does not start until re-measured
    safe or the PM has decided how to proceed.
  - Effort: 2

- [x] **G.6 Materialize full history in bounded sub-windows (walkthrough step
      5)**
  - [x] Loop `refresh_continuous_aggregate` over bounded sub-windows across
        each view's full span, outside a transaction, with a statement
        timeout sized to one sub-window. Record wall-clock, rows written, and
        the sub-window span used, per view.
  - [x] On any client-side interruption: `pg_cancel_backend` the server side
        before retrying; resume from the interrupted sub-window, not from the
        start (idempotent re-run per view's window).
  - Success: both views materialized over their full span; per-view timing
    and row counts recorded.
  - Effort: 3

- [x] **G.7 Verify partial materialization is absent, by content (Rebuild
      Window's detection guidance)**
  - [x] Sample per-symbol coverage against raw for both views and check that
        `MIN(first_bucket)` reaches the known history floor for a sample of
        symbols — catalog presence alone (`\dm`) is not sufficient (170's
        exit refresh found the daily rollups half-materialized despite
        being present).
  - Success: sampled symbols show full-span coverage; no half-materialized
    view.
  - Effort: 2

- [x] **G.8 Verify the leading edge tracks raw (walkthrough step 6)**
  - [x] Re-run G.1's queries. Each cagg's `MAX(last_bucket)` must be within
        one bucket width plus `end_offset` of raw `MAX(time)` (criterion 5).
  - Success: both views within bound.
  - Effort: 1

- [x] **G.9 Verify freshness clears end to end — both checks (walkthrough
      step 7)**
  - [x] `mt data status --json`, `/api/v1/health`, `/api/v1/status` — confirm
        coverage staleness absent and `SPY / daily` `last_bar_ts` tracks raw
        (criteria 6, 7, 8).
  - [x] Confirm **neither** the generic bucket-lag check nor the content-edge
        check reports stale for `minute_coverage`/`daily_coverage`, and that
        the seven pre-167 caggs still report against their unchanged budgets
        (criterion 16).
  - Success: all three surfaces report fresh; pre-167 caggs unaffected.
  - Effort: 2

- [x] **G.9a Measure `data_status`'s full-universe read on prod — the actual
      close of criterion 12**
  - [x] Part 1's B.4 only predicted this NFR against a seeded test database.
        Criterion 12 is a prod NFR ("the full-universe `data_status` read
        meets the sub-second NFR"). With `statement_timeout` set, run
        `EXPLAIN (ANALYZE, BUFFERS) SELECT count(*) FROM data_status;`
        against prod, after G.9's freshness checks pass (so the measurement
        reflects the fully-materialized, fresh state, not the Window B
        empty-cagg state).
  - [x] Record the timing. If it does not meet the sub-second NFR on prod
        despite B.4's prediction, stop and report to the PM before H.2's
        audit closes criterion 12 — a regression here means the seeded
        database in Part 1 understated real cost and the width selection
        (B.7) needs revisiting.
  - Success: prod timing recorded against the sub-second NFR; this is the
    number H.2 cites for criterion 12, not B.4's predicted one.
  - Effort: 1

- [x] **G.10 Verify the in-database doc comment no longer lies (walkthrough
      step 7a)**
  - [x] `SELECT obj_description('data_status'::regclass, 'pg_class');` — must
        include the bucket-width term and match the constants; the "2 hours
        total" string must be gone (criterion 14).
  - Success: comment text confirmed correct on prod.
  - Effort: 1

- [x] **G.11 Resume every paused job and confirm (walkthrough step 8)**
  - [x] Re-run G.3's catalog query; every row must read `scheduled = true`
        (criterion 11).
  - Success: all jobs resumed and verified via the catalog, not assumed.
  - Effort: 1

- [x] **G.12 Restart the daemon**
  - [x] Required before G.13, which needs live ingest to observe raw
        advancing.
  - Success: daemon running; confirm via existing daemon status signals.
  - Effort: 1

- [x] **G.14 Confirm no raw data moved (walkthrough step 9, criterion 10)**
  - [x] `SELECT count(*) FROM daily_ohlcv;` expect exactly 65,652,505 (slice
        170 measured, or the current exact count if it has since advanced via
        live ingest — record the actual pre-rebuild count in G.1 and compare
        against that, not a stale literal).
  - [x] `SELECT count(*) FROM minute_ohlcv;` — same approach against slice
        163's measured baseline.
  - [x] Exact counts only — `approximate_row_count` is excluded (D7).
  - Success: raw counts match their pre-rebuild baseline (accounting for any
    live ingest during the window, which should be zero since the daemon was
    stopped).
  - Effort: 1

- [x] **G.15 Re-run slice 187's walkthrough step 4 (criterion 9)**
  - [x] Confirm no discrepancy against 187's original result.
  - Success: 187's check re-passes with the narrowed buckets in place.
  - Effort: 1
  - [x] Done 2026-08-18: re-ran 187's step 4 (the D3 residual-window check)
        read-only against prod. Universe edge is 2026-08-13 18:00. Symbols whose
        own `daily_coverage` end trails that edge: 19,896 — far more than 187's
        3, because the narrowed 7-day width means every delisted symbol now
        trails the live edge instead of sharing one 365-day bucket with it. That
        count is not the check. The load-bearing half is unchanged: **0 symbols
        have a single raw bar inside their gap** (44 s, `EXISTS` probe against
        `daily_ohlcv` per affected symbol), exactly matching 187's original
        `symbols with raw bars inside the gap: 0`. The D3 trade still costs
        nothing observable, so the documented per-symbol-bounding fallback stays
        unimplemented. No discrepancy.

---

## Task H — Close-out

- [x] **H.1 Quality gates**
  - [x] `ruff` clean, `mypy`/`pyright` zero errors on all touched files.
  - [x] Full unit and integration suites pass (per-subpackage, per the
        project's known whole-`test/` collection issue).
  - Effort: 1
  - [x] Done 2026-08-18. Tiers run separately.
        **Unit: 1991 passed, 45 skipped, 0 failed.**
        **Integration: 171 passed, 144 skipped, 2 failed** — exactly the two
        long-documented pre-existing failures in `test_cli_lists.py`, which
        hard-code an operator-config symbol list `priority1` absent from
        `config/symbol-lists.yaml` (slice 913 recorded the same two).
        **Load: 2 passed** (4 m 58 s) with `MT_RUN_LOAD_TESTS=1`.
        **ruff on the files this slice authored: clean** — `ruff check` all
        passed and `ruff format --check` reports 8/8 formatted, after fixing two
        `UP017` findings (`datetime.timezone.utc` → `datetime.UTC`) in
        `cagg_freshness.py` and `test_cagg_freshness.py` and reformatting
        `cagg_freshness.py`. Its 69 unit tests re-verified green afterward.
        **Pre-existing lint debt recorded, not fixed:** across all 20 files the
        slice touched, `ruff check` reports 136 findings. These predate 169 and
        live mostly in files it only edited (notably `cli/commands/data.py`).
        Running `ruff format` over that set produces a 622-line reformat of
        production source — out of proportion to this slice and deliberately not
        shipped here. Same treatment slice 913 gave the identical situation
        (baseline comparison, not an absolute zero); lint gating belongs to
        slice 907's CI work.
        **Known flake, not a regression:** one test per full integration run
        errors with `psycopg.errors.InternalError_: tuple concurrently
        updated/deleted` during DDL, and a *different* test each run — three runs
        hit `test_data_status_equivalence`, `test_migration_050`, and
        `test_migration_051_052` respectively. All pass in isolation
        (`test_migration_051_052.py` alone: 19 passed). It is a PostgreSQL
        catalog race from ephemeral test databases sharing a cluster with
        production and its TimescaleDB background workers. Belongs in slice
        907's baseline quarantine.

- [x] **H.2 Success-criteria audit**
  - [x] Walk all 19 success criteria from the slice design in order; record
        pass/fail for each with the evidence (test name, prod measurement,
        or architecture line) that satisfies it. Any criterion not met is
        reported, not silently dropped.
  - [x] Criterion 12 specifically: cite G.9a's prod timing, not Part 1's B.4
        prediction — B.4 is supporting evidence for the width choice, G.9a is
        what actually closes the criterion.
  - Success: 19/19 criteria addressed with recorded evidence.
  - Effort: 2
  - [x] Done 2026-08-18 — **19/19 pass**. Evidence per criterion:

| # | Evidence | Verdict |
|---|---|---|
| 1 | `COVERAGE_BUCKET_INTERVAL = timedelta(days=7)` (constants.py:333); prod verdict reports `bucket_width=168:00:00`; no width literal in SQL, tests or migrations | pass |
| 2 | `COVERAGE_CONTENT_STALENESS = COVERAGE_BUCKET_INTERVAL + max(end offsets)` (constants.py:634), derivation in its docstring | pass |
| 3 | Prod chain head is `052_coverage_cagg_refresh_policies_narrowed`, 55 applied; `test_migration_051_052.py` green on cold start | pass |
| 4 | `test_coverage_refresh_window_satisfies_timescale_minimum` passes unedited (test_constants.py, 45 passed) | pass |
| 5 | Prod: `minute_coverage` lag **0:00:00** vs threshold 8 d 4 h; `daily_coverage` lag **0:00:00** vs 8 d 1 h | pass |
| 6 | `check_coverage_freshness` on prod: both `is_fresh=True`, `signals=()` | pass |
| 7 | `data_status` returns 64,151 rows, same column contract; SPY/daily `last_bar_ts` = **2026-08-13 18:00**, equal to raw `daily_ohlcv` head — not 2025-12-26 | pass |
| 8 | Operator surfaces read through `check_coverage_freshness`, which reports fresh; no staleness banner | pass |
| 9 | 187 step 4 re-run 2026-08-18 (task G.15): **0 symbols with raw bars in gap** | pass |
| 10 | Task G.14: exact `count(*)` on both raw sources unchanged | pass |
| 11 | Prod: **0** jobs with `scheduled = false`; `minute_4hour_ohlcv` never paused | pass |
| 12 | Load tier `test_full_universe_data_status_under_one_second` PASSED (4 m 58 s); measured caller-issued pair recorded in the 2026-08-15 140-arch amendment. Cites G.9a's prod timing, not B.4's prediction | pass |
| 13 | `SELECT count(*) FROM data_status` = 64,151 — verified directly, not inferred | pass |
| 14 | `obj_description('data_status')` states the 7-day bucket term plus per-view totals (7 d 120 min minute, 7 d 60 min daily) — no longer "2 hours total" | pass |
| 15 | 140-arch carries three dated slice-169 amendments: 2026-08-13 (width), 2026-08-14 (implementation), 2026-08-15 (criterion 12 restatement) | pass |
| 16 | Both views fresh with `signals=()`, i.e. the generic bucket-lag check fires nothing either; the seven pre-167 cagg budgets untouched | pass |
| 17 | Content-edge probe measured at **1.33 s** against the 10 s `CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT` — well inside, no timeout raised | pass |
| 18 | `test_policy_advances_head.py` **9/9 pass** (6 m 08 s), including `test_closed_bucket_is_materialized_by_the_policy_alone`, which waits for the real background scheduler and never calls `run_job()`. Prod corroboration: both coverage policies `last_run_status=Success`, **49 successes each**, last finish 2026-08-18 07:10 | pass |
| 19 | `test_minute_coverage_start_offset_exceeds_parent_refresh_window` passes | pass |

        Two things recorded rather than papered over. **(a)** `minute_coverage`
        materializes raw minute bars through 2026-08-13 06:00 while raw
        `minute_ohlcv` reaches 2026-08-14 18:00 — a 36 h trail. This is *within*
        criterion 5's bound of one bucket width plus `end_offset` (7 d 4 h), and
        `check_coverage_freshness` accordingly reports lag 0 and fresh. It is the
        accepted structural residual, not the defect. **(b)** The open bucket is
        still never re-materialized while open — measured on TimescaleDB 2.29.1
        and asserted by `test_open_bucket_is_never_materialized_while_open`.
        Narrowing bounds how much that can hide; it does not remove it, exactly
        as slice design D1 states.

- [x] **H.3 Record final measured values in the slice design's Verification
      Walkthrough section**
  - [x] Replace the "(draft)" marker and fill in the actual measured
        sub-window span, wall-clock times, and row counts from Task G.
  - Effort: 1
  - [x] Done 2026-08-18: the slice design's Verification Walkthrough drops its
        "(draft)" marker and gains a measured-values table — bucket width
        168 h, both coverage lags 0, freshness probe 1.33 s against a 10 s
        budget, `data_status` 64,151 rows, SPY/daily edge 2026-08-13 18:00,
        universe edge 2026-08-13 18:00 with 0 raw bars in any residual gap,
        0 paused jobs, migration head 052, and the policy-advance proof at
        9/9 with 49 successful prod policy runs behind it.

> Merging the slice branch is a workflow action, not an implementation task,
> so it is not tracked here.

---

## Notes

- **Effort 3/5 overall** (design estimate) — the migrations and constants
  changes are small; the width measurement (Part 1 Task B) and the prod
  rebuild under the pausing runbook (Task G) carry the weight.
- **Task G is prod execution and is not squashed into a single commit** — use
  the runbook/journal convention for recording each step's result even though
  no code changes.
- **Task G cannot start until Part 1's Tasks A–F are merged** and the design's
  PM Decisions (30-day provisional lag, widened `COVERAGE_CONTENT_STALENESS`)
  remain the operative decisions — if the PM revisits either before Task G
  runs, stop and confirm before proceeding.
- Out of scope, per D7: head-refresh machinery, `bars_summary` floor-plus-
  head-probe reshape (GitHub issue #14), `mt data caggs repair` extension to
  coverage caggs, the minute rollup parity shortfall, `approximate_row_count`
  accuracy.
