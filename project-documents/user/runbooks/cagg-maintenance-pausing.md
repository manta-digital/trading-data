---
docType: runbook
project: trading-data
parent: user/slices/163-slice.minute-cagg-chunk-re-sizing.md
relatedSlices: [162, 163, 166, 167]
host: <db_host>
dateCreated: 20260725
dateUpdated: 20260726
status: ready
---

# Runbook — Pausing continuous-aggregate jobs for maintenance

Applies to any operation that pauses a TimescaleDB refresh or columnstore policy on the
minute caggs: `mt data caggs repair`, chunk re-sizing, manual re-materialization, or
ad-hoc `alter_job(..., scheduled => false)`.

Derived from a real prod incident on 2026-07-25 (slice 163 Phase D). Full evidence in
`user/notes/163-baseline-verify-20260724.md`.

## Why this needs a runbook

Pausing a cagg refresh job is not a self-contained action. Two properties of this system
make a paused job leak into unrelated subsystems:

1. **The minute daemon's coverage index reads a cagg, not raw.**
   `build_minute_coverage_index` queries `minute_4hour_ohlcv`
   (`src/manta_trading/data/gaps/minute_coverage.py`). A stale 4h cagg makes the daemon
   believe recent sessions are missing and re-seed gap rows for them **every cycle**.

2. **Refresh jobs only look back `start_offset`.** All four minute refresh jobs use
   `start_offset => '1 day'`. Resuming a job that was paused longer than that heals only
   the most recent day and leaves the rest stranded **permanently** — the scheduled job
   will never revisit it.

The failure is silent: no errors, no corruption (bars re-insert via
`ON CONFLICT DO NOTHING`), just an unbounded stream of wasted provider API calls.

## Job reference (minute caggs)

| Granularity | Refresh job | Columnstore job | mat_hypertable_id |
|---|---|---|---|
| 5m  | 1007 | 1018 | 3 |
| 15m | 1008 | 1019 | 4 |
| 1h  | 1002 | 1020 | 5 |
| 4h  | 1003 | 1021 | 6 |

Job IDs are environment-specific. Always re-derive rather than trusting this table:

```sql
SELECT j.job_id, j.application_name, j.scheduled, j.config, s.last_run_status
FROM timescaledb_information.jobs j
LEFT JOIN timescaledb_information.job_stats s ON s.job_id = j.job_id
WHERE j.application_name NOT LIKE '%Telemetry%'
ORDER BY j.job_id;
```

## Rules

### R1 — Never pause the 4h refresh job (1003) to repair a different granularity

The coverage index depends only on the **4h** cagg. Repairing 5m/15m/1h requires pausing
only that granularity's own pair. Pausing 1003 during a long sweep re-opens the daemon
re-seed loop for the sweep's entire duration.

Pause 1003 **only** for a 4h-specific operation, and follow R2 on the way out.

### R2 — After any pause longer than `start_offset`, run a manual catch-up refresh

Resuming the job is **not** sufficient. Cover the whole pause window explicitly, with a
day of margin on each side:

```sql
SELECT alter_job(<refresh_job_id>, scheduled => true);
SELECT alter_job(<columnstore_job_id>, scheduled => true);

CALL refresh_continuous_aggregate('<cagg_view>', '<pause_start - 1d>', '<now + 1d>');
```

#### R2a — Coverage caggs (`minute_coverage`, `daily_coverage`): the template above FAILS

The slice-167 coverage caggs use **365-day buckets**, and TimescaleDB rejects any manual
refresh whose window spans less than two bucket widths (measured on 2.21.3: 730 days
rejected, 731 accepted — same rule that forced the 750-day policy `start_offset`).
The pause-window template above is therefore guaranteed to fail on them with
`refresh window too small`. Calendar-aligned windows do **not** satisfy the rule either.

Use NULL bounds instead — a full refresh of these caggs is cheap (~40 s measured on prod
for the complete 22-year history at creation; incremental re-refresh is faster):

```sql
CALL refresh_continuous_aggregate('minute_coverage', NULL, NULL);
CALL refresh_continuous_aggregate('daily_coverage',  NULL, NULL);
```

**Order matters for `minute_coverage`.** It is hierarchical over `minute_4hour_ohlcv`;
refreshing the child alone rolls up whatever the parent currently holds (measured: with
an unmaterialized parent it yields **0 bars**, silently). If the 4h cagg itself may be
behind — which it is after any pause this runbook covers — refresh it first:

```sql
CALL refresh_continuous_aggregate('minute_4hour_ohlcv', '<pause_start - 1d>', '<now + 1d>');
CALL refresh_continuous_aggregate('minute_coverage', NULL, NULL);
```

`daily_coverage` reads raw `daily_ohlcv` directly; it has no parent-order concern.

#### R2b — `alter_job` cannot pause or resume `minute_coverage`'s refresh policy

`SELECT alter_job(<job>, scheduled => false)` — the pause mechanism used throughout this
runbook — **fails outright on the refresh policy of a hierarchical cagg** with
`multiple refresh policies are not supported for hierarchical continuous aggregates`
(TimescaleDB re-validates policy config on every `alter_job`; measured 2026-07-27 on
2.21.3, literal and bound-parameter forms alike). Of the coverage caggs this affects
only `minute_coverage` (hierarchical over the 4h cagg); `daily_coverage`'s policy
alters normally.

If `minute_coverage`'s policy must be paused or resumed, go through the job catalog —
this is the same column `timescaledb_information.jobs.scheduled` (and therefore the
slice-167 freshness guard) reads:

```sql
UPDATE _timescaledb_config.bgw_job SET scheduled = false WHERE id = <refresh_job_id>;
-- ... maintenance ...
UPDATE _timescaledb_config.bgw_job SET scheduled = true  WHERE id = <refresh_job_id>;
```

Then apply R2a's NULL-bounds catch-up as usual. Verified on a throwaway DB: the catalog
update round-trips cleanly and the guard reports `NOT_SCHEDULED` while paused.

Note: the coverage caggs are **not** addressable via `mt data caggs refresh` (its
granularity tokens cover only the 163-era caggs) — use psql as above. `mt data caggs
verify` does list them (policy state, last run), though its `lag` column is blank for
them; the `mt data status` stale-coverage banner reports their lag directly.

### R3 — Verify the coverage hole is actually closed

The authoritative check is a raw-vs-cagg day-coverage diff. It must return **zero rows**:

```sql
SET statement_timeout = '600s';
WITH raw AS (
  SELECT symbol, time::date d FROM minute_ohlcv
  WHERE time >= '<pause_start - 1d>' GROUP BY 1,2),
cg AS (
  SELECT symbol, date_trunc('day', time_bucket)::date d FROM minute_4hour_ohlcv
  WHERE time_bucket >= '<pause_start - 1d>' GROUP BY 1,2)
SELECT r.d AS day, count(*) AS symbols_invisible_to_coverage_index
FROM raw r LEFT JOIN cg ON cg.symbol = r.symbol AND cg.d = r.d
WHERE cg.d IS NULL
GROUP BY r.d ORDER BY r.d;
```

Do not substitute `max(time)` vs `max(time_bucket)` for this. A universe-wide max hides
per-symbol holes — during the 2026-07-25 incident the two maxima were one bucket apart
while 349 symbols were invisible for four days.

### R4 — Confirm jobs actually resumed

```sql
SELECT job_id, scheduled FROM timescaledb_information.jobs
WHERE job_id IN (1002,1003,1007,1008,1018,1019,1020,1021) ORDER BY job_id;
```

All must be `t`. Then confirm `last_run_status = 'Success'` after the next scheduled run.

### R5 — Distinguish trailing refresh lag from real corruption

`mt data caggs verify` **exits 2 whenever cagg totals fall short of raw**, including the
benign case where the daemon has written bars the cagg's newest bucket has not covered
yet. It cannot tell the two apart — the operator must. Do not read exit 2 as corruption,
and do not read it as safe.

The discriminator: **a lag shortfall is confined to the open trailing window.** Sum
parity over everything *before* the newest window boundary; it must be exactly `0`.

```sql
SET statement_timeout = '300s';
SELECT (SELECT count(*) FROM minute_ohlcv
         WHERE "time" >= '2003-01-01' AND "time" < '<newest_window_start>')
     - (SELECT coalesce(sum(minute_count), 0) FROM <cagg_view>
         WHERE time_bucket >= '2003-01-01' AND time_bucket < '<newest_window_start>');
```

- `0` → every closed window is exact; the shortfall is trailing lag. Benign. It heals
  once the refresh policy is resumed.
- non-zero → a closed window is short. That is real under-materialization. Re-run
  `mt data caggs repair` for that granularity; do **not** proceed to other work.

Verified against the fully-repaired 1h cagg on 2026-07-25: returned `0` while
`verify` was exiting 2 on an 84-bar open-window gap.

**Any automation that chains sweeps must gate on this query, not on the exit code.**
Gating on exit 0 alone stalls forever on benign lag; ignoring the exit code entirely
marches past real corruption.

When scripting it, beware the `SET` echo: `psql -tAc "SET statement_timeout=...; SELECT
..."` prints `SET` on its own stdout line before the result. Piping that into
`tr -d '[:space:]'` yields `SET0`, not `0`, and a `== "0"` compare then aborts a
perfectly healthy sweep (observed 2026-07-25). Either strip it —

```bash
CLOSED=$(psql "$DBURL" -tAc "SET statement_timeout='300s'; SELECT ..." \
  | grep -vx 'SET' | tail -1 | tr -d '[:space:]')
```

— or set the timeout out of band with `PGOPTIONS='-c statement_timeout=300s'` so the
query is the only statement. Always log the parsed value, and fail closed on anything
that is not a bare integer.

## Diagnostic: is the daemon in a re-seed loop?

Symptom — the daemon re-pulls many chunks on symbols that should be complete.

1. Pick an affected symbol. Compare raw days against cagg days:

```sql
SELECT count(DISTINCT time::date) FROM minute_ohlcv WHERE symbol = '<SYM>';
SELECT count(DISTINCT date_trunc('day', time_bucket)) FROM minute_4hour_ohlcv WHERE symbol = '<SYM>';
```

2. If the cagg count is lower, list the specific invisible days (R3 query, scoped to the
   symbol). If they cluster in a contiguous recent block, check whether job 1003 was
   unscheduled over that block.

3. Remediate per R2 + R3.

**Not** a symptom of this bug: the daemon visiting symbols in non-alphabetical order.
`iter_active_instruments` uses `most_stale_first`
(`ORDER BY s.last_attempt_ts ASC NULLS FIRST, i.symbol ASC`) — symbol name is only the
tiebreaker, so wrapping Z→A is correct behavior.

## Known gap

`preflight()` in `src/manta_trading/market/maintenance/cagg_repair.py` refuses to run when
the **target** cagg's jobs are scheduled, but does not check whether job 1003 is
*unscheduled* while a different granularity is being repaired. R1 is currently enforced by
this runbook only, not by code.
