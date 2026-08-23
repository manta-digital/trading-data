---
docType: index
project: trading-data
dateCreated: 20260823
dateUpdated: 20260823
status: current
---

# Runbooks — index

Read this first. One line per runbook: what it covers and when to open it.

| Runbook | What it covers | Open it when… |
|---|---|---|
| [100-production-operations.md](100-production-operations.md) | **Start here.** Operating production: quick-reference command table, systemd units, install/update, run/pause/resume passes, rollback | you want to run, check, update, or pause anything in production |
| [200-backup-and-restore.md](200-backup-and-restore.md) | PostgreSQL backups: WAL archiving, weekly base, B2 offsite, restore drill, the ARCHIVE-BROKEN alarm | restoring data, or the ARCHIVE-BROKEN flag appeared |
| [300-cagg-maintenance-pausing.md](300-cagg-maintenance-pausing.md) | Pausing/resuming TimescaleDB cagg refresh jobs safely during repairs | you are about to pause a refresh job, or just resumed one |
| [310-coverage-cagg-rebuild.md](310-coverage-cagg-rebuild.md) | Rebuilding the slice-167 coverage caggs from scratch | a coverage cagg is wrong/empty and refresh alone cannot fix it |
| [400-test-database-cluster.md](400-test-database-cluster.md) | The dedicated test PG cluster: setup, credentials, MT_TIMESCALE_TEST_URL | tests fail wanting a test database, or the test cluster needs work |

## "cagg is STALE" in pass logs — do you actually need to act?

The minute pass logs an ERROR when `minute_4hour_ohlcv` lags more than ~1 day.
**If acquisition is catching up (backlog, outage recovery): do nothing.** The
lag is the *data* being behind; it closes by itself as passes drain the
backlog and the hourly refresh jobs (which run regardless) materialize the new
bars. The pass meanwhile degrades gracefully to recorded gap rows.

Act only if the lag persists **after** acquisition is current — that means the
refresh machinery itself is stuck: check `timescaledb_information.job_stats`
for the refresh job (resolve by view name, never a hard-coded job id), and see
[300-cagg-maintenance-pausing.md](300-cagg-maintenance-pausing.md) R2/R2a for the
catch-up refresh.

## Naming

`{index}-{name}.md`, grouped by hundreds: 1xx operating production, 2xx backup,
3xx TimescaleDB cagg maintenance, 4xx test infrastructure. Renamed 2026-08-23 —
older documents referencing `production-deploy.md`, `backup-and-restore.md`,
etc. mean the files above.
