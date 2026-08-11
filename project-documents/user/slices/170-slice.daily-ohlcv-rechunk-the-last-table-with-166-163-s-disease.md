---
docType: slice-design
slice: daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease
project: trading-data
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [166]
interfaces: [169]
dateCreated: 20260809
dateUpdated: 20260811
status: complete
---

# Slice Design: `daily_ohlcv` Rechunk — the Last Table with 166/163's Disease

## Overview

`daily_ohlcv` is the last remaining hypertable with the over-chunking
pathology diagnosed and fixed for `minute_ohlcv` (slice 166) and the minute
caggs (slice 163): **3,371 chunks** (3,369 compressed) for only **34.7 M
rows**, from the 7-day `chunk_time_interval` set at creation (slice 143) and
never revisited. Measured on prod (2026-08-06):

- `EXPLAIN` (plan-only) of a 31k-symbol `ANY` aggregate exceeds **120 s** —
  planning, not execution, is the cost, exactly as in 166's root-cause record.
- A bare `SELECT MAX(time) FROM daily_ohlcv` exceeds **30 s**.
- The 2026-08-05 daemon hang (journal 20260806): a daily-mode selection query
  against this table could not finish planning and wedged the daemon for
  15+ hours. 0.7.6 rewrote that probe as an anti-join on `acquisition_state`
  (treating the symptom); this slice removes the root hazard.
- Any `assert_cagg_fresh` raw-edge probe (slice 168) against a
  `daily_ohlcv`-sourced cagg risks a spurious `PROBE_FAILED` stale verdict
  until the `MAX(time)` probe is fast again.

The fix reuses the **proven Option-D mechanism** from slice 166 (per-window
stage → `drop_chunks` → reinsert → compress, one transaction per window,
resumable and idempotent), already productized as `mt data rechunk`. This
slice generalizes that tool from its hardcoded minute target to a small
per-table target registry and runs it against `daily_ohlcv`.

At ~34.7 M rows (~210× fewer than the minute run's 7.27 B) this is a short
maintenance window, not a marathon.

## Value

- **Operational:** removes the last known trigger for the daemon-wedge class
  of failure; `MAX(time)` and universe-scale planning return to normal.
- **Correctness:** `assert_cagg_fresh` probes against `daily_weekly/monthly/
  quarterly_ohlcv` and `daily_coverage` stop risking `PROBE_FAILED` verdicts
  caused purely by planning latency.
- **Sequencing:** unblocks slice 169 (coverage-cagg refresh repair), whose
  plan entry mandates running *after* this rechunk so its `daily_coverage`
  rematerialization is paid once, on the healthy table.

## Measured Baseline (prod, 2026-08-05/06)

| Fact | `daily_ohlcv` | Corrected at execution (2026-08-11) |
|---|---|---|
| `chunk_time_interval` | 7 days (slice 143, literal in creation migration) | — |
| Chunk count | 3,371 (3,369 compressed) | 3,372 / 3,370 |
| Rows | ~34.7 M | **65,652,505** (~1.9× the estimate) |
| Total size | ~4.4 GB (166 baseline table) | — |
| Data span | ~2004 → present (~22.6 years) | **1962 → present (64.6 years)** |
| 31k-symbol `ANY` aggregate | EXPLAIN plan-only > 120 s | confirmed; 7.70 s after |
| `SELECT MAX(time)` | > 30 s | 4.92 s re-measured; 0.157 s after |

> **Two inputs in this table were wrong, and one changed the plan.** The data
> span was carried over from `minute_ohlcv`'s 2004 EODHD horizon; daily history
> actually begins in **1962**. That made the derived window count 338 rather
> than the ~118 predicted below — see the Execution Record. The grid arithmetic
> was never at fault. (`MAX(time)` re-measured at 4.92 s rather than >30 s on
> execution day; the original was taken under daemon load.)

Dependent caggs (all must be paused during the run, per the 166 A5-Q3
lesson — a concurrent refresh during chunk restructuring silently and
permanently loses materialized rows):

| Cagg | Source | Refresh policy (`start_offset` / `end_offset` / cadence) |
|---|---|---|
| `daily_weekly_ohlcv` | `daily_ohlcv` | 21 d / 7 d / 1 d |
| `daily_monthly_ohlcv` | `daily_ohlcv` | 90 d / 30 d / 1 d |
| `daily_quarterly_ohlcv` | `daily_ohlcv` | 270 d / 90 d / 1 d |
| `daily_coverage` | `daily_ohlcv` (direct, not hierarchical) | 365-day buckets; see runbook R2a |

Job IDs are environment-specific — always catalog-resolved at runtime, never
hardcoded (166 pattern; runbook job-reference rule).

## Technical Decisions

### D1 — Target chunk interval: 70 days (`DAILY_OHLCV_CHUNK_INTERVAL`)

New constant in `constants.py`: `DAILY_OHLCV_CHUNK_INTERVAL = timedelta(days=70)`.

- Wall-clock rule (journal 20260719): interval = span ÷ target chunk count,
  never data volume. 22.6 years ÷ 70 days ≈ **118 chunks** — low hundreds,
  matching the healthy minute caggs (`MINUTE_CAGG_CHUNK_INTERVAL`, same value,
  same reasoning) and the daily caggs' proven-healthy mat-hypertable interval.
- **Grid nesting:** 70 = 10 × 7, so the existing 7-day chunks nest exactly
  inside the epoch-aligned 70-day grid (1970-01-01 + k×70d). Every target
  window contains only whole existing chunks — the 166 grid-alignment caveat
  is satisfied by construction, and each rewritten window yields exactly one
  chunk.
- Defined once, referenced by the creation migration, the new
  `set_chunk_time_interval` migration, and the rechunk target registry — no
  scattered literals (project rule; 166 precedent).

### D2 — Generalize `mt data rechunk` via a target registry

`run_rechunk` currently hardcodes `interval = MINUTE_OHLCV_CHUNK_INTERVAL`
and defaults `table`/`cagg_views` to the minute family
(`market/maintenance/rechunk.py`). Generalization, kept minimal:

- A `RechunkTarget` `StrEnum` (`MINUTE`, `DAILY`) keying a registry that
  supplies, per target: hypertable name, interval constant, dependent cagg
  views, and the migration id the pre-flight names in its error message.
  No magic strings in dispatch (project rule).
- CLI: `mt data rechunk [--table minute|daily]`, defaulting to `minute` so
  the existing invocation and its documented re-run semantics are unchanged.
  Still one operator flag beyond that: `--dry-run`.
- The daily target's cagg view list: `daily_weekly_ohlcv`,
  `daily_monthly_ohlcv`, `daily_quarterly_ohlcv`, `daily_coverage`. The
  existing pre-flight (refuses to run while the table's columnstore policy or
  any listed cagg's refresh policy is scheduled) then covers the daily family
  with no logic change.
- Everything else in the driver (window classification, EXCLUSIVE lock before
  staging, staged==reinserted guard, `SKIP_UNCOMPRESSED` trailing windows,
  resumability) is table-agnostic already and is reused untouched.

### D3 — Migration pair (166 Phase-B pattern)

1. New migration: `set_chunk_time_interval('daily_ohlcv',
   DAILY_OHLCV_CHUNK_INTERVAL)` — affects future chunks only; safe
   regardless of when the rewrite runs. The rechunk pre-flight asserts this
   dimension value before mutating (existing `_assert_dimension_interval`,
   pointed at the new migration id in its message).
2. Update the slice-143 creation migration's literal
   `chunk_time_interval => INTERVAL '7 days'` (`migrations/minute.py:1236`)
   to render `DAILY_OHLCV_CHUNK_INTERVAL`, so a cold start creates 70-day
   chunks directly and the migration chain stays the single schema source of
   truth (slice 156 contract).

### D4 — Cold-start cagg default: accepted divergence, recorded

After D3, a cold-start DB creates `daily_ohlcv` at 70 days, so TimescaleDB's
10×-source default gives newly created daily caggs 700-day mat-hypertable
chunks (~12 chunks over the span) instead of prod's existing 70. This is
**accepted**: the pathology class is over-chunking, and a dozen chunks on
megabyte-scale caggs is harmless. No extra `set_chunk_time_interval` for the
daily caggs; prod's existing cagg mat hypertables are untouched by this slice.

### D5 — Job pause/resume scope (runbook `cagg-maintenance-pausing.md`)

- Pause (catalog-resolved): `daily_ohlcv`'s columnstore policy, the four
  dependent caggs' refresh policies, and any columnstore policies on those
  caggs' mat hypertables. `daily_coverage` is **not** hierarchical, so plain
  `alter_job` works on it (R2b applies only to `minute_coverage`).
- **R1 holds: minute-family jobs stay running.** Job 1003 (4h refresh) feeds
  the daemon's coverage index; there is no reason to touch anything outside
  the daily family.
- The daemon is stopped for the window (PM go/no-go), and no `mt data pull`
  runs — the EXCLUSIVE per-window lock makes concurrent writers safe but
  stalled, and stopping them is the clean choice for a short run.
- Resume + catch-up per R2: the three rollup caggs get explicit full-span
  `refresh_continuous_aggregate(..., force => true)` (their policies look
  back at most 270 days — a scheduled run can never heal history; the
  163 lesson). `daily_coverage` gets the R2a form — NULL bounds (365-day
  buckets reject any narrow window), with `force => true` for the same
  reason: `CALL refresh_continuous_aggregate('daily_coverage', NULL, NULL, force => true)`.
- R4 close-out: zero jobs left unscheduled.

### D6 — Verification gate: R5 discriminator, not exit codes alone

`mt data caggs verify` exits 2 on benign trailing lag as well as real
corruption. Acceptance uses the R5 closed-window parity query (sum parity
strictly before the newest window boundary must be exactly 0) for each
rollup cagg, alongside the verify run.

## Execution Flow

Phases compress relative to 166 because the mechanism is rehearsed and
production-proven; no scratch rehearsal is repeated.

**Phase B — code + migration (no prod access needed):**
1. `DAILY_OHLCV_CHUNK_INTERVAL` constant; target registry + `--table` flag;
   new migration + creation-migration update (D2/D3).
2. Unit tests: registry dispatch, pre-flight message per target, minute
   default unchanged. The driver's existing seam-based tests already cover
   the mechanism; extend the throwaway-DB fixture coverage to a daily-shaped
   table only where behavior differs (nothing is expected to).
3. Doc touches: `MINUTE_OHLCV_CHUNK_INTERVAL` and `MINUTE_CAGG_CHUNK_INTERVAL`
   docstrings currently cite `daily_ohlcv`/`daily_*` at their old intervals as
   reference points — reword so they don't teach superseded values.

**Phase C — execute on prod (maintenance window):**
4. PM gate: backup/snapshot point confirmed; daemon stopped
   (`acquisition_state` quiescent, no heartbeat).
5. Apply migration (`mt data migrate apply`); `mt data rechunk --table daily
   --dry-run` and inspect the plan (~118 windows expected, trailing
   uncompressed windows listed as skips).
6. Capture pre-rewrite baselines: total row count, per-cagg totals, 3 sampled
   symbols' bounded `count(*)`/`MIN`/`MAX` (statement_timeout set, per prod
   query discipline).
7. Pause daily-family jobs (D5); run `mt data rechunk --table daily`.
8. Resume jobs; force-refresh the four caggs (D5); `ANALYZE daily_ohlcv`.
9. Later idempotent re-run picks up trailing windows once the columnstore
   policy has compressed them (same standing procedure as the minute table).

**Phase D — verify:** success criteria below; results recorded in this
document as the execution record.

## Integration Points

- **169 (coverage-cagg refresh repair):** must run after this slice (plan
  ordering). Note: step 8's `daily_coverage` full refresh heals the *content*
  staleness (stuck at 2026-06-12, ~390k invisible rows) as a side effect —
  but the *policy* defect (current 365-day bucket never refreshed) remains,
  so staleness re-accrues from day one. 169 is still required; the status
  surfaces will legitimately report fresh only until the bucket drifts again.
- **168 (`assert_cagg_fresh`):** consumes nothing new; its raw-edge probe
  simply becomes reliable for daily-sourced caggs.
- **166:** provides the mechanism, driver, runbook, and re-run procedure —
  all reused; the minute target's behavior must be bit-identical after the
  registry refactor.
- **Daemon (0.7.6 wedge fix):** the anti-join probe stays; this slice removes
  the underlying planning hazard for every other query shape.

## Success Criteria

1. `daily_ohlcv` chunk count drops from 3,371 to **low hundreds** (~120,
   plus trailing uncompressed skips until the standing re-run).
2. `SELECT MAX(time) FROM daily_ohlcv` returns **sub-second**.
3. The 31k-symbol `ANY` aggregate `EXPLAIN` (plan-only) completes in normal
   time (seconds, not minutes).
4. **No data loss:** total row count identical before vs after; for ≥3
   sampled symbols, bounded `count(*)`/`MIN(time)`/`MAX(time)` identical.
5. All four dependent caggs pass the R5 closed-window parity check (exactly
   0) and `mt data caggs verify` reports parity, with any exit-2 explained as
   trailing lag per R5.
6. Cold start creates 70-day `daily_ohlcv` chunks from the first migration
   run (throwaway DB, `mt data init`, check
   `timescaledb_information.dimensions`).
7. `mt data rechunk` with no `--table` still targets `minute_ohlcv` with
   unchanged behavior (regression-guarded by existing tests).
8. No background job left unscheduled (R4); resumed jobs report
   `last_run_status = 'Success'` on their next runs.
9. `ANALYZE` run; `approximate_row_count('daily_ohlcv')` sane against the
   exact count.

## Verification Walkthrough (verified 2026-08-11)

Expected values below are the **measured** post-run results, not predictions.
An external agent re-running these against prod should reproduce them, allowing
for growth in the trailing (uncompressed) region.

```sql
-- Prod psql; always: SET statement_timeout (prod query discipline). After a
-- client-side timeout, pg_cancel_backend before running anything else.
SET statement_timeout = '120s';
\timing on

-- 1. The probe that was slow before the rewrite (4.92 s measured pre-run):
SELECT MAX(time) FROM daily_ohlcv;
-- measured after: 0.157 s, 2026-08-05 18:00:00-06:00

-- 2. Chunk health:
SELECT num_chunks FROM timescaledb_information.hypertables
WHERE hypertable_name = 'daily_ohlcv';
-- measured after: 341 total / 339 compressed (was 3,372 / 3,370).
-- NOT ~120: the data spans 64.6 years (from 1962), so 23,604 days / 70
-- = 338 windows. See the Execution Record.

-- 3. Future-chunk interval:
SELECT time_interval FROM timescaledb_information.dimensions
WHERE hypertable_name = 'daily_ohlcv';          -- expect 70 days

-- 4. Integrity (vs. captured baselines). NOTE the ::timestamptz casts —
--    binding a bare date defeats chunk exclusion (measured 3,100 ms vs 7 ms).
SELECT count(*) FROM daily_ohlcv;
-- measured: 65,652,505 both before and after — identical

SELECT count(*), sum(volume) FROM daily_ohlcv
WHERE symbol = 'AAPL'
  AND time >= '2015-01-01'::timestamptz AND time < '2026-01-01'::timestamptz;
-- measured: 2766, 308130610600  (MSFT 2766/78239020262;
--           SPY 2766/238100213300; IBM 2766/13257218779)

-- 5. R5 closed-window parity, per rollup cagg (must be exactly 0). Compare
--    sums strictly BEFORE the newest materialized bucket; trailing lag beyond
--    that edge is benign and is why exit codes alone are not the gate.
WITH edge AS (SELECT max(time_bucket) AS b FROM daily_weekly_ohlcv),
     m AS (SELECT coalesce(sum(volume),0) v FROM daily_weekly_ohlcv, edge
            WHERE time_bucket < edge.b),
     s AS (SELECT coalesce(sum(volume),0) v FROM daily_ohlcv, edge
            WHERE time >= (SELECT min(time_bucket) FROM daily_weekly_ohlcv)
              AND time < edge.b)
SELECT m.v - s.v FROM m, s;
-- measured: 0 for daily_weekly_ohlcv, daily_monthly_ohlcv,
--           daily_quarterly_ohlcv (repeat with each view substituted)

-- 6. No job left paused:
SELECT job_id, proc_name, hypertable_name
FROM timescaledb_information.jobs WHERE NOT scheduled;
-- measured: zero rows (R4 satisfied)
```

```bash
# 7. CLI plan and run (operator):
mt data rechunk --table daily --dry-run   # window plan, no mutation
                                          # measured: 338 windows, 337 to
                                          # rewrite, 1 skipped (trailing)
mt data rechunk --table daily             # after jobs paused; resumable
                                          # measured: exit 0, ~16 min

mt data rechunk --dry-run                 # regression guard: with no --table
                                          # this still plans minute_ohlcv
                                          # measured: 1,180 windows, 1,176 done

mt data caggs verify   # NOTE: checks the MINUTE family, not daily. It reports
                       # FAIL from a pre-existing ~0.018% minute shortfall that
                       # is slice 169 / `mt data caggs repair` territory — it is
                       # NOT a daily-parity signal. Use step 5 for this slice.

mt data status         # coverage still reports data ending 2025-12-26: the
                       # 365-day coverage bucket never refreshes while open.
                       # Not fixed by this slice — see 169.

# 8. Cold-start check (throwaway DB only, per DB-protection rules).
#    Covered automatically by test/integration/test_migration_050.py and
#    test_cold_start.py against an ephemeral database:
MT_TIMESCALE_TEST_URL=... uv run pytest test/integration/test_migration_050.py -q
# measured: 5 passed (interval = 70 days, 050 idempotent)
```

## Execution Record (prod, 2026-08-11)

Executed on `192.168.1.144` after a PM-authorized window with the daemon
stopped and a cold backup taken (PostgreSQL stopped for the copy). Full detail,
including every baseline value, is in
[2026-08-11-slice-170-daily-rechunk-execution.md](../notes/2026-08-11-slice-170-daily-rechunk-execution.md);
the run log is beside it.

**Result: all nine success criteria met, one with a caveat.** `mt data rechunk
--table daily` exited 0 in ~16 minutes. 337 of 338 windows rewritten, 1 skipped
(trailing uncompressed), 0 failures. **Every window collapsed to exactly one
chunk** — 336 from 10 chunks, one from 8.

| Measure | Before | After | Criterion |
|---|---|---|---|
| Chunks (total / compressed) | 3,372 / 3,370 | **341 / 339** | 1 ✅ |
| `SELECT MAX(time)` | 4.92 s | **0.157 s** | 2 ✅ |
| 31k-symbol `ANY` EXPLAIN (plan only) | >120 s, never finished | **7.70 s** | 3 ✅ |
| `count(*)` | 65,652,505 | **65,652,505** | 4 ✅ |
| R5 closed-window parity (3 rollups) | — | **0, 0, 0** | 5 ✅ |

Per-symbol integrity (AAPL, MSFT, SPY, IBM) matched **exactly** on both count
and `sum(volume)`. Criterion 6 was verified pre-flight by the cold-start
integration test; 7 by `mt data rechunk --dry-run` still planning
`minute_ohlcv` (1,180 windows, 1,176 done) with its interval unchanged at
7 days; 8 by zero jobs left unscheduled.

**Criterion 9 — caveat, and the criterion was mis-specified.** `ANALYZE` ran
in 3.1 s, but `approximate_row_count('daily_ohlcv')` then returned
**1,443,446,308** against an exact 65,652,505 — **+2,099% wrong**. This is a
known TimescaleDB estimator defect already recorded for `minute_ohlcv` (+68%
there), not damage from this rewrite: the exact count is correct and matches
baseline. The criterion should never have asked for a "sane"
`approximate_row_count`; **do not use that function for verification**.

### The C2.3 stop condition fired — and was right to

The dry run reported **338 windows**, 2.9× the design's ~118. Per C2.3 the run
was halted and diagnosed before any mutation:

- Real span is **1961-12-27 .. 2026-08-12 = 23,604 days = 64.6 years**, not the
  22.6 years this design assumed by borrowing `minute_ohlcv`'s 2004 horizon.
- 23,604 ÷ 70 = 337.2 → **338 windows.** The arithmetic was correct throughout.
- All 3,372 source chunks were exactly 7 days wide, so **the 70 = 10 × 7
  nesting property held perfectly** — which the run then demonstrated by
  collapsing every window to a single chunk.

The PM approved proceeding at 70 days. Re-deriving to ~200 days to hit the
original ~120-chunk target would **break nesting** (200 = 28.57 × 7) and
reintroduce the grid-alignment hazard 166 warned about — a bad trade for a
marginal planner gain. **The design's derivation method (wall-clock span ÷
target count) is sound; only its span input was wrong.**

### Other findings

- **The runbook's job table is stale.** There is no job 1003; the 4h minute
  refresh is now **job 1124**. D5's instruction to resolve IDs from the catalog
  at runtime is what prevented acting on a wrong ID.
- **The daily caggs were roughly half-materialized.** The exit force-refresh
  added +6.6 M rows to `daily_weekly_ohlcv`, +1.5 M to monthly, +530 k to
  quarterly, +148 k to `daily_coverage`. Their policies look back ≤270 days, so
  a scheduled run could never have healed 64 years of history — D6 and the 163
  lesson vindicated. Post-refresh parity is exactly 0.
- **`mt data caggs verify` reports FAIL, on the *minute* family**, which this
  slice never touched (`minute_ohlcv` unchanged at 7 days / 1,207 chunks, its
  jobs running throughout). Closed-window parity there is 99.982% — an
  identical shortfall across all four minute rollups, i.e. late-arriving source
  rows rather than per-cagg corruption. Pre-existing; `mt data caggs repair`
  clears it. This is exactly the ambiguity D6 anticipated in refusing to gate
  on that command's exit code.
- **`daily_coverage` content staleness was healed as a side effect, but the
  policy defect remains** — the current 365-day bucket is never refreshed, so
  its newest bucket is still 2025-12-26 even immediately after a forced
  full-span refresh, and staleness re-accrues. **Slice 169 is still required**,
  and this is the project's live API-facing defect.

## Risk Assessment

**Risk: Med.** The mechanism is the same code that rewrote 7.27 B rows on
prod with zero errors and byte-identical integrity checks (166); this run is
~210× smaller. Residual risks:

- **Registry refactor regressing the minute path** — mitigated by keeping the
  driver logic untouched (parameters only) and by Success Criterion 7's
  regression guard.
- **Cagg corruption via a job firing mid-rewrite** — the known catastrophic
  mode (166 A5-Q3); mitigated by the existing pre-flight (refuses to run
  while daily-family jobs are scheduled) now covering the daily cagg list,
  plus the D5 force-refresh on the way out.
- **Bulk mutation of prod** — PM-confirmed backup point before Phase C
  (gate in step 4), and per-window transactions mean an interrupted run
  leaves a valid, partially-improved table.

Effort: 2/5.
