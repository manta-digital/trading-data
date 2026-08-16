---
docType: runbook
project: trading-data
parent: user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md
relatedSlices: [163, 167, 169]
host: <prod_host>
dateCreated: 20260816
dateUpdated: 20260816
status: ready
---

# Runbook — Coverage-Cagg Rebuild (slice 169 Task G)

Migrations 051/052 drop and recreate `minute_coverage` and `daily_coverage` at
the narrowed 7-day bucket width, **empty**. This runbook applies those
migrations and runs the sweep that refills both caggs over full history, then
verifies the repair. Total window ≈ 30–45 minutes.

Run every step **on the prod host, from the prod checkout**
(`~/source/repos/manta/trading-data`), in order. Each step is labeled with the
Task G step(s) it executes from
`169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md`.

**Hard rules for the whole window:**

- `minute_4hour_ohlcv`'s refresh job is **never paused** (it is the daemon's
  coverage index — pausing it causes the endless re-seed loop, incident
  2026-07-25). The sweep's pre-flight enforces this; do not work around it.
- No acquisition pass (`mt data daemon run ...`) runs between steps 2 and 9.
- Any client-side timeout or Ctrl-C during a psql step: `pg_cancel_backend`
  the server side before running anything else.
- The sweep itself is **resumable** — if a rebuild command is interrupted or
  fails partway, the recovery action is to re-run the same command, never to
  roll anything back.

---

## Step 0 — Update the checkout

```bash
cd ~/source/repos/manta/trading-data
git pull
uv sync
uv run mt --version
```

Set the maintenance URL for this terminal session (the caggs are owned by the
maintenance role, so the migrations, the psql steps, and the sweep all run
under it). This reads one named value from `.env` — do **not** `source .env`
(the `$`-in-password trap):

```bash
MAINT_URL="$(grep -m1 '^MT_TIMESCALE_MAINTENANCE_URL=' .env | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//')"
```

## Step 1 — Record the before-state (G.1, baseline for G.14)

```bash
psql "$MAINT_URL" <<'SQL'
SET statement_timeout = '30s';
SELECT 'minute_coverage' AS v, max(time_bucket) FROM minute_coverage
UNION ALL SELECT 'daily_coverage', max(time_bucket) FROM daily_coverage
UNION ALL SELECT 'daily_ohlcv raw', max(time) FROM daily_ohlcv
UNION ALL SELECT 'minute_4hour raw', max(time_bucket) FROM minute_4hour_ohlcv;
SET statement_timeout = '120s';
SELECT count(*) AS daily_ohlcv_rows FROM daily_ohlcv;
SQL
```

Record all five values. The coverage heads should show the known drift
(pinned months behind raw) — that is the defect, still live.

## Step 2 — Stop the daemon and the API server (G.2)

There is no persistent daemon process — acquisition passes are one-shot.
Confirm none is running, and stop the API server:

```bash
pgrep -af "mt data daemon run"   # must print nothing; if it prints, wait for that pass to finish
pgrep -af "mt serve"             # note the PID, then:
pkill -f "mt serve"
```

Do not start an acquisition pass again until step 9.

## Step 3 — Plan the sweep, read-only (G.5 sizing input)

Safe to run at any time; mutates nothing and skips the pause pre-flight:

```bash
uv run mt data caggs rebuild-coverage --family daily --dry-run
uv run mt data caggs rebuild-coverage --family minute --dry-run
```

Expect ≈65 daily windows and ≈23 minute windows. Wildly different counts mean
the span detection is off — stop and report.

## Step 4 — Pause the coverage policies (G.3)

Pauses **every** job on the two coverage caggs (refresh + columnstore),
resolved from the catalog — and proves `minute_4hour_ohlcv` was not touched:

```bash
psql "$MAINT_URL" <<'SQL'
SET statement_timeout = '30s';
SELECT alter_job(job_id, scheduled => false)
  FROM timescaledb_information.jobs
 WHERE hypertable_name IN ('minute_coverage', 'daily_coverage');
SELECT job_id, hypertable_name, proc_name, scheduled
  FROM timescaledb_information.jobs
 WHERE hypertable_name IN ('minute_coverage', 'daily_coverage', 'minute_4hour_ohlcv')
 ORDER BY hypertable_name, job_id;
SQL
```

Check the printed table: every `minute_coverage`/`daily_coverage` row reads
`scheduled = f`; every `minute_4hour_ohlcv` row still reads `scheduled = t`.

## Step 5 — Apply migrations 051/052, then restart the API server (G.4, G.4a)

```bash
uv run mt data migrate status    # chain ends at 050
uv run mt data migrate apply
uv run mt data migrate status    # chain ends at 052
```

Both caggs now exist empty at the 7-day width and `data_status` is
re-installed. Restart the API server now — the DDL exposure window (Window A)
is over; it does **not** stay down during materialization. Start it the way it
is normally run on this host (e.g. `nohup uv run mt serve >> ~/mt-serve.log 2>&1 &`).
Endpoints will report coverage **stale** until step 7 completes — expected,
not a fault.

## Step 6 — Rebuild daily coverage, then verify (G.5–G.7)

```bash
MT_TIMESCALE_DB_URL="$MAINT_URL" uv run mt data caggs rebuild-coverage --family daily
```

The first sub-window is the safety gate (G.5): it should complete in roughly a
second (measured ~0.5 s/yr). If the first window runs for minutes or the host
shows memory pressure, **Ctrl-C** (safe — resumable), and re-run with a
narrower span: `--subwindow-days 90`. If that also misbehaves, stop and report
to the PM.

Then verify by content (never by catalog presence):

```bash
MT_TIMESCALE_DB_URL="$MAINT_URL" uv run mt data caggs rebuild-coverage --family daily --verify
```

Exit 0 with span floor reached and head within one bucket = pass.

## Step 7 — Rebuild minute coverage, then verify (G.6–G.8)

```bash
MT_TIMESCALE_DB_URL="$MAINT_URL" uv run mt data caggs rebuild-coverage --family minute
MT_TIMESCALE_DB_URL="$MAINT_URL" uv run mt data caggs rebuild-coverage --family minute --verify
```

## Step 8 — Resume the paused jobs and confirm (G.11)

```bash
psql "$MAINT_URL" <<'SQL'
SET statement_timeout = '30s';
SELECT alter_job(job_id, scheduled => true)
  FROM timescaledb_information.jobs
 WHERE hypertable_name IN ('minute_coverage', 'daily_coverage');
SELECT job_id, hypertable_name, proc_name, scheduled
  FROM timescaledb_information.jobs
 WHERE hypertable_name IN ('minute_coverage', 'daily_coverage', 'minute_4hour_ohlcv')
 ORDER BY hypertable_name, job_id;
SQL
```

Every row in the printed table must read `scheduled = t`.

## Step 9 — Post-checks (G.9, G.9a, G.10, G.14) and return to normal operation

```bash
time uv run mt data status
```

- Coverage staleness must be **absent** (this is the user-visible fix).
- The `time` reading closes criterion 12 (as amended 2026-08-15): the wall
  time minus ~1 s of CLI startup must stay within the no-regression margin of
  **3.9 s**. Record it. If it regresses past that, report to the PM before
  close-out.

```bash
psql "$MAINT_URL" <<'SQL'
SET statement_timeout = '120s';
SELECT obj_description('data_status'::regclass, 'pg_class');
SELECT count(*) AS daily_ohlcv_rows FROM daily_ohlcv;
SQL
```

- The comment must include the bucket-width term in its CAGG LAG formula and
  must **not** contain the string "2 hours total" (G.10).
- `daily_ohlcv_rows` must equal step 1's count exactly — no raw data moved
  (G.14; the daemon was stopped, so no ingest occurred in between).

Normal operation resumes now: acquisition passes run on their usual schedule
(G.12), and `/api/v1/health` + `/api/v1/status` can be spot-checked (G.9).

## Step 10 — One week later: the criterion-18 check (G.13)

This is the only check the original defect could not pass. The head advances
when a bucket **closes** (buckets are Monday-to-Monday UTC), so it is
observable on the **first Monday at least 7 days after the rebuild, after
06:00 UTC** — not merely one policy tick later:

```bash
psql "$MAINT_URL" <<'SQL'
SET statement_timeout = '30s';
SELECT 'minute_coverage' AS v, max(time_bucket) FROM minute_coverage
UNION ALL SELECT 'daily_coverage', max(time_bucket) FROM daily_coverage;
SELECT j.hypertable_name, s.last_run_status, s.last_successful_finish
  FROM timescaledb_information.jobs j
  JOIN timescaledb_information.job_stats s USING (job_id)
 WHERE j.hypertable_name IN ('minute_coverage', 'daily_coverage');
SQL
```

Pass: both `max(time_bucket)` values have advanced past the step-9 head with
**no manual refresh issued**, and `last_successful_finish` is recent. In the
meantime `mt data status` must never report coverage stale (the budgets,
8 d 4 h / 8 d 1 h, exceed the weekly cycle). If the Monday check shows the
head standing still while the jobs run green — that is the defect's exact
signature; report it immediately.
