---
docType: runbook
project: trading-data
parent: user/slices/163-slice.minute-cagg-chunk-re-sizing.md
relatedSlices: [162, 163, 166, 167]
host: <db_host>
dateCreated: 20260725
dateUpdated: 20260725
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
