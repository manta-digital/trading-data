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
dateUpdated: 20260816
status: in_progress
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

- [ ] **G.13 Observe the policy advance the head unaided (walkthrough step
      8a, criterion 18 — the only check the original defect could not pass)**
  - [ ] Record `MAX(last_bucket)` on both views and `last_successful_finish`
        for both policy jobs. Wait for at least one policy tick (schedule
        interval 1 h) with **no manual refresh issued**. Confirm the job ran
        (`last_successful_finish` advanced) **and** `MAX(last_bucket)`
        advanced with it, while raw also advanced over the same interval.
  - Success: policy-driven advance confirmed without manual intervention —
    the defect's own signature (a job that runs while the head stands still)
    is absent.
  - Effort: 2

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

- [ ] **G.15 Re-run slice 187's walkthrough step 4 (criterion 9)**
  - [ ] Confirm no discrepancy against 187's original result.
  - Success: 187's check re-passes with the narrowed buckets in place.
  - Effort: 1

---

## Task H — Close-out

- [ ] **H.1 Quality gates**
  - [ ] `ruff` clean, `mypy`/`pyright` zero errors on all touched files.
  - [ ] Full unit and integration suites pass (per-subpackage, per the
        project's known whole-`test/` collection issue).
  - Effort: 1

- [ ] **H.2 Success-criteria audit**
  - [ ] Walk all 19 success criteria from the slice design in order; record
        pass/fail for each with the evidence (test name, prod measurement,
        or architecture line) that satisfies it. Any criterion not met is
        reported, not silently dropped.
  - [ ] Criterion 12 specifically: cite G.9a's prod timing, not Part 1's B.4
        prediction — B.4 is supporting evidence for the width choice, G.9a is
        what actually closes the criterion.
  - Success: 19/19 criteria addressed with recorded evidence.
  - Effort: 2

- [ ] **H.3 Record final measured values in the slice design's Verification
      Walkthrough section**
  - [ ] Replace the "(draft)" marker and fill in the actual measured
        sub-window span, wall-clock times, and row counts from Task G.
  - Effort: 1

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
