---
docType: slice-design
slice: backup-coverage-for-tick-archive-and-database
project: trading-data
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [226]
interfaces: [229, 230, 231, 233]
dateCreated: 20261004
dateUpdated: 20261004
status: not_started
---

# Slice Design: backup-coverage-for-tick-archive-and-database

## Overview

The tick cluster `17/tick` (port 5433, data at `/data/postgresql/17/tick`, database `trading_tick`) has no backup of any kind today. Its archive, `/data/tick-archive`, has been in the nightly restic backup since slice 223, but only a one-file restore has been proven.

This slice:
- chooses the tick database's backup policy from the rebuild cost slice 226 measured;
- extends the existing 915/920 backup tooling (weekly `pg_basebackup`, WAL archiving, keep-days pruning, B2 offsite copy, health flags) to a second cluster by making it per-cluster, rather than copying it;
- extends the tick role set so the tick cluster can be base-backed-up;
- fixes a live health-check bug that the weekly base backup triggers;
- adds a restore drill script that proves both the archive and the database come back;
- records retention, exclusions and placement in runbook 200.

**Policy decision (TD1): the tick database gets the same weight as production: a weekly base backup, continuous WAL archiving, and a nightly bookkeeping dump. Rebuilding from the archive stays the documented fallback, and the drill proves it.**

## Value

- **Real purchased data stops depending on one disk.** Slices 229–231 spend real money on the Standard plan. After this slice, both the files and the bookkeeping that describes them (which jobs were bought, what they cost, what superseded what) survive a disk loss.
- **Restore is independent of outside systems.** Restoring from a base backup plus WAL needs only `/data/backup` or B2. A rebuild from the archive also needs the production database (for the CME calendar) and the Databento API (for `batch_job` records).
- **One backup system for two clusters.** The same scripts, flags and runbook cover both clusters. When the tick cluster later moves to hammerhead, it is enrolled the same way (one more row in the table), and `pg_basebackup` is already the move mechanism the architecture names.
- **Production's weekly backup stops blinding the health check.** Each Sunday's base backup currently makes the check fail with `cannot_check` for about an hour (FAIL lines at 05:30 and 06:00 on 2026-10-04).

## Technical Scope

**Included**
1. **Cluster table.** A checked-in table, `deploy/backup-clusters.conf`, defines which clusters are backed up, where each one's backups go locally and offsite, and when its jobs run. Rows: `17/main` (current values, unchanged) and `17/tick`.
2. **`deploy/setup-backup.sh` made per-cluster.**
   - It loops over the table.
   - Per-cluster steps: directories, the WAL directory ACL, and the `archive_mode`/`archive_command`/`wal_compression` settings.
   - Host-wide steps run once: restic, timeshift, the cron file render, the leftover-crontab report.
   - The cluster's data directory is read with `pg_lsclusters` rather than built from `/var/lib/postgresql`.
   - `--cluster` is removed. The table is the membership.
3. **Cron template split** into a host-wide part (the two restic lines) and a per-cluster block (health check, WAL push, nightly metadata dump, weekly base backup), rendered once per row into the single `/etc/cron.d/manta-trading-backup`.
4. **Cron wrappers take the URL key as an argument.** `cron_weekly_backup.sh`, `backup_health_cron.sh` and `cron_nightly_metadata.sh` gain `--url-key <ENV_KEY>` in place of the hard-wired `MT_TIMESCALE_MAINTENANCE_URL`.
   - `cron_weekly_backup.sh` gains an explicit `--replication-host <host>` in place of its hard-coded `@192.168.1.144:` → `127.0.0.1` rewrite.
   - `cron_nightly_metadata.sh` gains `--remote` in place of reading `MT_BACKUP_S3_BUCKET`.
5. **Tick roles and access for base backups.**
   - `scripts/provision_tick_roles.sql` gains `-v with_replication=1`, which grants REPLICATION to `tick_migrate`, matching `provision_roles.sql` on production.
   - `scripts/provision_tick_cluster.sh` adds a `host replication tick_migrate <src>/32 scram-sha-256` line to the tick `pg_hba.conf`.
   - It also splits its database list: `trading_tick` is created; `trading_tick` and `trading_tick_drill` are allowed in `pg_hba`. The proof database is gone.
6. **Health-check fix.** `scripts/wal_segment_name.py` (called by `check_backup_health.sh`) accepts every file name PostgreSQL archives. Today it rejects the backup-history file (`<segment>.<offset>.backup`) that a base backup leaves as `last_archived_wal`.
7. **Restore drill script**, `scripts/drill_tick_restore.py`, a reusable quarterly drill that proves three things:
   - **Archive:** the archive restores from restic.
   - **Database:** the database restores from base + WAL and matches production.
   - **Fallback:** a database rebuilt from the restored archive matches the restored one row for row.
8. **Cutover script**, `scripts/cutover_227_tick_backup.py`. The PM runs one command. It enrols the tick cluster, takes its first base backup, arms its offsite reconcile, runs the drill and writes the report.
9. **Runbooks.**
   - Runbook 200 gains a tick-cluster section covering placement, retention, exclusions and the drill.
   - Runbook 210's `setup-backup.sh` invocation and its stale restic include list are corrected.

**Excluded**
- Moving the tick cluster to hammerhead. That move adds a table row and its own host bootstrap.
- Automating production's restore drill. Runbook 200 Step 6 stays manual for `17/main`. The drill's restore step is written per-cluster so it could serve main later, but switching main is not this slice.
- Showing backup flags in `mt data health` or the overview. Production's flags aren't surfaced either, so parity holds. This is a candidate for the API and operator-surface question, not this slice.
- Archive-size growth from future `ohlcv-1s` purchases (architecture Future Work 7). Restic deduplicates immutable files; the size gets revisited when that purchase is planned.
- Removing the stale `MT_PROOF_226_*` lines from `.env`. They name a dropped database, and nothing reads them.

## Dependencies

### Prerequisites
- **226 (complete):** the postgres-owned cluster `17/tick` exists, and the rebuild cost is measured at 212.5 s from an empty `trading_tick` to 78 ingested units, plus compression.
- **223 (complete):** `/data/tick-archive` is in `cron_system_backup.sh`'s `INCLUDE_PATHS`, and `.partial` files are excluded.
- **915/920 (complete):** the backup scripts this slice extends. They are already parameterized by explicit arguments under 915's rule that every backup tool takes its targets as explicit, required arguments.
- **923 (complete):** `provision_tick_roles.sql`, the `MT_TICK_DB_URL` and `MT_TICK_MAINTENANCE_URL` settings, and the tick preflight. The preflight refuses unknown `MT_TICK_*` keys; this slice adds none.

### Interfaces Required
- Production database reachable via `MT_TIMESCALE_DB_URL`. The drill's rebuild step reads the CME calendar from it.
- `DATABENTO_API_KEY` in `.env`. Adopt's free `batch_job` reads need it during the drill's rebuild step.
- The B2 bucket (`MT_BACKUP_S3_*`) and the restic repository, as configured by 920.
- One PM-run `sudo` session for the cutover. The tick cluster's restart for `archive_mode` happens inside it, and running the script is the PM's go for that restart.

## Architecture

### Component Structure

```
deploy/backup-clusters.conf ──read by──► deploy/setup-backup.sh ──renders──► /etc/cron.d/manta-trading-backup
        │                                     │                                ├─ host block   (restic nightly, restic check)
        │                                     ├─ per row: dirs, WAL ACL,       ├─ 17/main block (unchanged lines)
        │                                     │   archive settings (pg_settings.sh)
        │                                     └─ host once: restic, timeshift  └─ 17/tick block
        │
        └──read by──► scripts/drill_tick_restore.py  ◄── called by ── scripts/cutover_227_tick_backup.py
                      scripts/cutover_227_tick_backup.py
```

**Per-cluster jobs.** Each cluster block calls the existing scripts with that row's arguments:
- `backup_health_cron.sh` → `check_backup_health.sh` / `check_archive_health.sh`
- `sync_wal_offsite.sh`
- `cron_nightly_metadata.sh` → `backup_metadata.sh`
- `cron_weekly_backup.sh` → `backup_prod.sh`, `prune_wal_archive.sh`, `reconcile_guards.sh`, `rclone sync`

None of these scripts gains cluster knowledge. They already take explicit directories and URLs. Only the three wrappers that hard-wire production's URL key change.

### Data Flow

**Backup, steady state (tick row):**
1. Every WAL segment from `17/tick` is compressed with zstd by `archive_command` into `/data/backup/17-tick/wal/`. The hourly push copies it to `b2:<bucket>/17-tick/wal`.
2. The nightly metadata dump runs `pg_dump -Fc` of every `trading_tick` table that isn't a hypertable:
   - `tick_request`, `tick_archive_unit`, `tick_ingest_ledger`, `tick_definition`, `tick_day_condition`, `tick_dataset_edge`, `schema_migrations`;
   - under 1 MB today;
   - written to `/data/backup/17-tick/metadata/` and offsite.
3. The weekly run does a `pg_basebackup` of `17/tick` (677 MB today) to `/data/backup/17-tick/base/<YYYYMMDD>`, verified, then pushed offsite. WAL older than the oldest kept base backup is pruned (keep-days 7). After the reconcile guards pass, the offsite copy is synced.
4. Nightly restic (unchanged) carries `/data/tick-archive` to `system/`.

**Restore, primary path:** restore the latest base + WAL into a fresh data directory and start it. The archive comes back separately via restic. Nothing outside `/data/backup`, B2 and restic is needed.

**Restore, fallback path:**
1. Restore the archive from restic.
2. Run `mt data migrate apply --track tick` on an empty database.
3. Run `mt data tick adopt` once per job directory. This verifies every file's size and SHA-256 against the job's `manifest.json`.
4. Run `mt data tick pass --estimate-only`, then `mt data tick ingest`.

This needs the production database and the Databento API. It loses three things (224 slice design, the decision on adopting existing batch files): pass-purchase provenance (units return as adopted, without estimate or repurchase/supersession links), `reopened_at` marks, and any paid jobs whose files were never archived.

### State Management

| State | Where | Owned by |
|---|---|---|
| Backup membership, per-cluster paths, schedules | `deploy/backup-clusters.conf` | this slice |
| Tick base backups, WAL, metadata dumps | `/data/backup/17-tick/{base,wal,metadata}` | per-cluster cron block |
| Tick offsite copy | `b2:<bucket>/17-tick/{base,wal,metadata}` | per-cluster cron block |
| Tick health flags and stamps | `/data/backup/17-tick/{ARCHIVE-BROKEN,BACKUP-STALE,wal-offsite.stamp,wal-offsite.lock,RECONCILE-ARMED}` | per-cluster cron block |
| Production (unchanged paths) | `/data/backup/{base,wal,metadata,…}`, `b2:<bucket>/{base,wal,metadata}` | per-cluster cron block |
| Drill reports | `project-documents/user/notes/<date>-227-tick-restore-drill.md`, plus the runbook 200 drill record row | drill script |

The tick root sits inside production's root, but no production job touches it. Production's jobs only ever name `base/`, `wal/` and `metadata/` explicitly, and restic does not include `/data/backup`. A unit test asserts that the main block's rendered lines never name `17-tick` (TD3).

## Technical Decisions

### TD1. Policy: full weight (base + WAL + metadata dump); rebuild-from-archive as the proven fallback

**Option 1: rebuild from the archive on restore (rejected).** Its cost is small (212.5 s plus compression for 27.7 M rows; about 6 minutes per year of ES+GC at tbbo). But it gives back the data and the money, not the history:
- **Lost history.** Slice 224's design for adopting existing batch files says: "Provenance is kept only by a durable tick database." Once 229–231 buy through the pass, that provenance is real.
- **Outside dependencies.** A rebuild also needs the production database and the Databento API, so a host-wide loss would chain restores.

**Option 2: WAL-only (rejected).** WAL replay needs a base backup to replay onto, so this isn't really an option.

**Option 3: bookkeeping dump plus rebuilding trades (rejected).** This means restoring the small tables, then re-ingesting `tick_trade` from the archive. It sounds cheap but needs new code: units restore as `ingested` with no rows, so the restore path would need a reset that moves `ingested` back to `verified` and clears the ingest ledger. That's a restore-only code path with no other user, and it is exactly the kind that rots.

**Option 4: full weight (chosen).**
- **Weight:** 677 MB per weekly base today against production's roughly 98 GB, and about 5 GB/yr stored at the ES+GC tbbo projection.
- **No new code:** the tooling exists, and the one-row-per-cluster table is the extension the architecture asks for.
- **Same restore:** the tick cluster restores exactly like production.
- **Why the dump stays:** it keeps the bookkeeping recoverable even if a base/WAL chain is broken. The bookkeeping is under 1 MB, and the dump tool needs no change, because `backup_metadata.sh` already picks "every table that is not a hypertable" from the catalog.

The measured rebuild cost still sets one thing: the fallback is cheap enough to run in every drill, so the drill proves it every quarter instead of trusting a two-week-old measurement.

### TD2. One checked-in cluster table, not a repeatable `--cluster` flag

`deploy/backup-clusters.conf` is whitespace-separated, has `#` comments, and holds one row per cluster:

```
# cluster  url_key                        backup_root          remote_subpath  replication_host  metadata_cron  weekly_base_cron
17/main    MT_TIMESCALE_MAINTENANCE_URL   /data/backup         -               127.0.0.1         0 2 * * *      0 3 * * 0
17/tick    MT_TICK_MAINTENANCE_URL        /data/backup/17-tick 17-tick         -                 15 2 * * *     30 2 * * 0
```

- **`-` means "no override".** Production's remote is the bucket root (its existing layout). Tick connects with its URL's host as written (`manta9000`, which only listens on 127.0.1.1).
- **Why a table and not a flag:** a repeatable flag would let a run that names only `17/main` drop tick's cron block without anyone noticing. With the table, membership is defined once.
- **Readers:** bash (`setup-backup.sh`) and Python (`drill_tick_restore.py`, the cutover) both read this one file through one small parser per language. Each parser has a unit test against the real file, per CLAUDE.md's parsing rule.
- **Malformed rows:** a malformed row (wrong field count, unknown cluster in `pg_lsclusters`, root not under `/data`) is a hard error before any change.
- **Schedules:** tick's jobs run before production's, so the tick base backup (seconds today) never overlaps production's three-hour one.

### TD3. Production's layout stays exactly where it is

Moving production's backups to `/data/backup/17-main/` would move roughly 300 GB of live backups, the B2 prefixes, the B2 lifecycle rules and the arm file, all for symmetry. Not worth it.

Production keeps its root and prefix. Tick nests under `/data/backup/17-tick` and `b2:<bucket>/17-tick`.

**What protects production:**
- **Same cron lines:** a test renders the cron file from the real table and asserts that production's block is byte-identical to today's rendered production lines.
- **Unchanged config:** the cutover checks production's `postgresql.conf`/`postgresql.auto.conf` SHA-256 before and after, the way `provision_tick_cluster.sh` already does.

### TD4. Data directory from `pg_lsclusters`

`setup-backup.sh` now builds `PGDATA=/var/lib/postgresql/$CLUSTER`, which is wrong for tick. It changes to read the data directory and config path from `pg_lsclusters` for the row's cluster. If the cluster isn't listed, that's a hard error. This removes the `PG_DATA_ROOT` constant.

### TD5. Tick base backups use `tick_migrate` with REPLICATION, matching production

On production, the maintenance role (`trading_migrate`) holds REPLICATION, granted by `provision_roles.sql -v with_replication=1`. Tick follows the same design:
- `provision_tick_roles.sql` gets the same variable.
- `provision_tick_cluster.sh` passes it and writes one replication line to `pg_hba` for that role, from the source address it already computes (`ip route get 127.0.1.1`; the 2026-10-03 finding that loopback connections arrive from 127.0.0.1).

A dedicated backup role was considered and rejected: it means one more credential in `.env` and one more `MT_TICK_*` key for the preflight, with no gain over production's design.

### TD6. Archive settings are `setup-backup.sh`'s job; the restart is the cutover's

`archive_mode`, `archive_command` (with that row's WAL directory) and `wal_compression=zstd` are applied per row through `pg_settings.sh`, exactly as for production. `setup-backup.sh` keeps its rule of never restarting; it reports `PENDING RESTART`.

The cutover restarts only the `postgresql@17-tick` unit, and only when:
- the setting is pending;
- the tick advisory lock is free, meaning no tick run is active.

Production is never restarted. A pending-restart report for `17/main` makes the cutover stop.

### TD7. The health check accepts every archived file name

`wal_segment_name.py next` parses the name's 24-hex segment prefix:
- a plain segment, `<segment>.partial`, and `<segment>.<offset>.backup` all give that segment;
- a timeline history file (`<timeline>.history`) has no segment. `check_backup_health.sh` then reports the check as `n/a: last archived file is a timeline history file`. It still emits its FLAGS line, so the wrapper's `cannot_check` never fires on a normal archive state.

Any other name is still an error. The test fixtures are the real file names seen on this host (`00000001000012A5000000B4.00000028.backup` from 2026-10-04).

### TD8. Drill script design

`scripts/drill_tick_restore.py` runs as manta from the checkout root and uses `sudo` for the restic steps (the same model as the cutover scripts). Each step is check-then-act, and the script writes only under `/data/restore-test/227-drill-<stamp>/` plus a drill database it creates.

1. **Hold the tick cluster.** Take the tick advisory lock for the whole run, so production `trading_tick` can't change while it is compared. If a tick run holds the lock, refuse.
2. **Make the database restore point current.** Run `pg_switch_wal()` on `17/tick` and wait (bounded) until that segment's `.zst` is in the tick WAL directory.
3. **Restore the archive.** Use restic `restore latest --include /data/tick-archive` into the drill directory. Compare file count and total bytes with the live archive.
4. **Restore the database.** Unpack the latest tick base backup into the drill directory. Set a `restore_command` that reads the tick WAL directory (runbook 200's two-shape command for `.zst` and raw segments). Start it as manta with `pg_ctl`, socket-only in the drill directory, `archive_mode=off`. Wait for recovery to finish.
5. **Compare the restored database with production.** Exact row counts for every public table, plus a `tick_trade` fingerprint, must be equal.
   - The fingerprint is per `(instrument_id, UTC day)`: row count, plus the md5 of the rows' text in `(ts_event, sequence, sequence_ordinal)` order. One SQL definition is used for every comparison.
6. **Rebuild the fallback.** Create `trading_tick_drill` on `17/tick` through `provision_tick_roles.sql`, with a database comment marking it as created by the drill. Then run the four rebuild commands against it, with `MT_TICK_DB_URL`/`MT_TICK_MAINTENANCE_URL` pointed at the drill database and `MT_TICK_ARCHIVE_DIR` at the restored archive, set in the subprocess environment only.
   - Adopt re-hashes every restored file against its job's `manifest.json`, so this step is also the full archive verification.
   - Record the rebuild time.
7. **Compare the rebuild with the restore.** `tick_trade` fingerprints must be equal, which is the architecture's "row-for-row the same projection" claim. Differences in the bookkeeping tables are listed in the report and checked against the expected set from 224's design (`is_adopted`, estimate fields, `download_deadline`, links, `reopened_at`). A difference outside that set fails.
8. **Clean up what the script created:**
   - Stop the scratch server.
   - Drop `trading_tick_drill`, only when its comment marks it as the drill's.
   - Remove the drill directory.
   - At startup, a leftover drill database with the drill's comment is dropped. One without it is a refusal.
9. **Report** to `user/notes/<date>-227-tick-restore-drill.md`: each step's expected and seen values, and the timings. Exit 0 only when every check passes.

### TD9. Exclusions are recorded, not changed

| Path | In restic? | Why |
|---|---|---|
| `/data/tick-archive` | yes (223) | the record; immutable, so stored once thanks to deduplication |
| `/data/tick-archive/**/*.partial` | excluded (223) | unfinished copies are not the record |
| `/data/postgresql/17/tick` | no | a file copy of a live data directory can't be restored; covered by base + WAL |
| `/data/backup` (incl. `17-tick/`) | no | goes offsite by rclone to B2 |
| `/data/market-data/databento` | no | the PM's read-only originals; every job there was adopted, so verified copies are in the archive |
| `/data/restore-test` | no | scratch space for drills |

For timeshift, the implementation checks whether its configured includes reach `/data` at all, and records the answer in runbook 200. It adds an exclude for `/data/postgresql/**` only if they do. Production's equivalent, `/var/lib/postgresql/**`, is already excluded.

### TD10. B2 lifecycle for the tick prefix

Production's `wal/` and `base/` prefixes have a 30-day "delete after hidden" rule. Without one, the files tick's syncs delete would stay as hidden versions forever. That's a slow storage-cost leak, not a data risk.

- **Detect:** `setup-backup.sh --check` reads the bucket's rules (`rclone backend lifecycle b2:<bucket>`) and reports MISSING for any row prefix not covered.
- **Apply:** applying a per-prefix rule without replacing the bucket's existing rules is checked against rclone's B2 backend source during implementation.
- **Fallback:** if rclone can't do that, the cutover report names the one rule to add in the B2 console. That would be the slice's only non-script PM step.

### Patterns and Conventions
- **Bash and rendering:** check-then-act bash with explicit required arguments, as in 915/920. `--check` reports drift and changes nothing. Cron render tokens are `@NAME@`.
- **Cutover script:** follows the `cutover_265_trades.py` / `cutover_common.py` pattern (step list in the docstring, report in `user/notes`, exit status from the checks).
- **Errors:** no silent defaults anywhere. A missing table field, URL key, data directory or WAL segment is a named error.

## Implementation Details

### Migration Plan

The production cron file and `setup-backup.sh` invocation change shape. Production's behaviour must not.

- **Source:**
  - `setup-backup.sh --cluster 17/main …`;
  - one template with six lines;
  - wrappers hard-wired to `MT_TIMESCALE_MAINTENANCE_URL`.
- **Destination:**
  - `setup-backup.sh` without `--cluster`, reading the table;
  - a host block plus per-cluster blocks;
  - wrappers taking `--url-key`, `--replication-host` and `--remote`.
- **Consumers to update in this slice:**
  - the cron template;
  - runbook 210 Step 7 (the bootstrap command);
  - runbook 200 Step 7 (the cron table);
  - `test/unit/test_setup_backup.py`, `test_system_backup.py`, `test_wal_offsite.py`.
- **Proof that behaviour is preserved:**
  - Production's rendered lines are byte-identical to the current `/etc/cron.d/manta-trading-backup` production lines (unit test, plus `setup-backup.sh --check` reporting no drift for the main block before the cutover applies).
  - Production's config hash is unchanged across the cutover.
  - The next production health firing after the cutover prints `PASS … FLAGS archive=0 stale=0`.

### Database / Storage Schema

There are no migrations. Storage added:

| Item | Size now | Growth |
|---|---|---|
| Tick base backup | 677 MB per weekly copy, 1–2 kept locally and offsite under keep-days 7 | about 5 GB/yr at the ES+GC tbbo projection |
| Tick WAL | bursts during ingest, pruned weekly | bounded by one week of ingest |
| Tick metadata dump | < 1 MB nightly | small |

## Integration Points

### Provides to Other Slices
- **229–231 (Standard plan purchases, GC):** the tick database, including pass-purchase provenance and spend records, is durable from the first paid job.
- **233 (service wiring):** a backed-up tick cluster to point the service env at. The cluster table is where any future cluster gets enrolled.
- **The later hammerhead move:** that host's cluster joins the backup set as one more table row, and a weekly `pg_basebackup` is already producing the copy the move starts from.
- **Operators:** `scripts/drill_tick_restore.py` as the quarterly tick drill, recorded next to production's in runbook 200.

### Consumes from Other Slices
- 226's cluster and rebuild commands; 223's archive enrolment; 915/920's scripts; 923's tick credentials and preflight.
- **If the production DB or the Databento API is unreachable:** the drill's fallback step fails and the report says which one. The primary restore and its comparison stand on their own, so the report still shows whether the primary path works.

## Success Criteria

### Functional Requirements
1. `deploy/backup-clusters.conf` lists `17/main` and `17/tick`. `setup-backup.sh` (and its `--check`) reads it, and any malformed row is a hard error before any change.
2. `17/tick` runs with `archive_mode=on`, `wal_compression=zstd`, and an `archive_command` that writes to `/data/backup/17-tick/wal`. A forced WAL switch produces a `.zst` segment there.
3. `/etc/cron.d/manta-trading-backup` holds the host block, production's block (byte-identical to before), and a tick block.
4. After the cutover, `/data/backup/17-tick/base/<date>` holds a verified base backup, and `b2:<bucket>/17-tick/{base,wal,metadata}` hold matching copies (`rclone check --one-way` clean).
5. `/data/backup/17-tick/RECONCILE-ARMED` exists only after the first tick weekly run passed its final check.
6. The tick health check logs `PASS … FLAGS archive=0 stale=0`.
7. Production's health check no longer logs `cannot_check` when the last archived file is a `.backup` history file.
8. The drill script exits 0, and its report shows:
   - the archive restored, with file count and bytes equal to the live archive;
   - the database restored from base + WAL, with every table's row count and the `tick_trade` fingerprint equal to production's;
   - the database rebuilt from the restored archive, with a `tick_trade` fingerprint equal to the restored database's, and bookkeeping differences only from the expected set;
   - the rebuild time;
   - no leftovers (drill directory and drill database gone).
9. Runbook 200 has a tick section: placement, retention, the exclusions table, both restore paths, the drill command and the drill record row. Runbook 210's bootstrap command and restic include list are correct.
10. Production is untouched: config hash unchanged, never restarted, and its backup paths and prefixes unchanged.

### Technical Requirements
- Unit tests:
  - table parsing (bash and Python) against the real file and a malformed row;
  - the cron render (production block byte-identical to today's; tick block content; no production line names `17-tick`);
  - `wal_segment_name.py` with the real names seen on the host;
  - the wrappers' new required arguments (missing → exit 2);
  - the `pg_lsclusters` data-directory read.
- Integration test: the drill's fingerprint SQL and the expected-difference check, run on the test cluster's tick database. The full drill runs only in the cutover.
- ruff, mypy and shellcheck (`-x`) clean on touched files.

### Integration Requirements
- 229 can start buying with the tick database already in the weekly backup and the hourly WAL push.
- The next quarterly drill (due 2026-11-17 per runbook 200) runs production's manual Step 6 plus `drill_tick_restore.py`.

### Verification Walkthrough

These are the commands as designed; Phase 6 refines them with real output.

1. **Check, change nothing** (agent, before the cutover):
   ```
   sudo deploy/setup-backup.sh --check --checkout "$PWD" --env-file "$PWD/.env" --backup-root /data/backup
   ```
   Expect: `17/main` all OK. `17/tick` reports MISSING for its directories, archive settings and cron block. The lifecycle check reports the tick prefix.

2. **Cutover** (PM, one command, after tagging the release):
   ```
   uv run python scripts/cutover_227_tick_backup.py
   ```
   Steps the script prints, each with expected and seen values:
   1. Production config hash.
   2. Tick advisory lock free.
   3. `provision_tick_cluster.sh` adds the replication grant and `pg_hba` line.
   4. `setup-backup.sh` applies the change (production: no change; tick: dirs, settings, cron).
   5. Restart `postgresql@17-tick` (pending `archive_mode`).
   6. `pg_switch_wal` gives a segment in `/data/backup/17-tick/wal`.
   7. First tick weekly run, with the base backup verified and pushed.
   8. Arm the tick reconcile.
   9. Drill.
   10. Production config hash again, unchanged.

   The report lands in `user/notes/2026-10-xx-227-cutover.md`. Exit 0 means every check passed.

3. **Read the results:**
   ```
   ls /data/backup/17-tick/base/
   tail -2 /data/backup/17-tick/backup-health.log      # PASS … FLAGS archive=0 stale=0
   tail -2 /data/backup/backup-health.log              # production still PASS
   rclone check --one-way /data/backup/17-tick/base b2:<bucket>/17-tick/base
   ```

4. **Quarterly drill, standalone** (any later time):
   ```
   uv run python scripts/drill_tick_restore.py
   ```
   Expect the archive, database and fallback sections all PASS, the rebuild time near 226's 212.5 s plus compression for the current archive, and a report in `user/notes/<date>-227-tick-restore-drill.md`.

5. **Health-fix spot check:**
   ```
   uv run python scripts/wal_segment_name.py next 00000001000012A5000000B4.00000028.backup   # prints 00000001000012A5000000B5
   ```

## Risk Assessment

### Technical Risks
- **Reworking `setup-backup.sh` touches production's backup schedule.** A render error could silently drop or alter a production cron line.
- **The tick restart for `archive_mode`** briefly stops the tick cluster.

### Mitigation Strategies
- **For the render:**
  - the byte-identical test for production's block;
  - `--check` before applying;
  - the cutover's before/after config hash;
  - the cutover fails if the main block shows any drift.
- **For the restart:** it is the tick unit only, only with the tick lock free, and inside the PM's single cutover run. No tick timers exist yet (the tick schedule is still manual), so no scheduled work is interrupted.

## Implementation Notes

### Development Approach
1. **Health-check fix** (`wal_segment_name.py` plus its tests). It's independent and fixes a live production problem, so it lands first.
2. **The cluster table and its parsers**, with tests against the real file.
3. **Wrapper arguments** (`--url-key`, `--replication-host`, `--remote`), plus the template split and render. The production-block test must pass before going further.
4. **`setup-backup.sh` per-row loop and `pg_lsclusters` data directory**, with `--check` run against the live host (read-only).
5. **Tick role and `pg_hba` changes** in `provision_tick_roles.sql` and `provision_tick_cluster.sh` (`--check` run).
6. **`drill_tick_restore.py`**, with the fingerprint SQL integration test.
7. **`cutover_227_tick_backup.py`**, then the runbook 200/210 updates.
8. **PM:** tag, then run the cutover. Agent: close the slice from the report.

This sits at the upper end of one session: about two task files, with the setup-backup rework and the drill as the two heavy parts. If task breakdown shows it won't fit, the clean split is "per-cluster tooling + tick enrolment" and then "drill script + runbooks".

### Special Considerations
- **Production safety:** never restart `17/main`, and never move its backup paths. Destructive SQL only touches `trading_tick_drill`, which the drill itself created (marked by its comment).
- **Preflight:** no new `MT_TICK_*` keys, so the tick preflight is unaffected. The drill's URL overrides live in a subprocess environment only, never in `.env`.
- **Credentials:** the replication grant reuses `tick_migrate`'s existing scram password, so no credential is written anywhere new.
