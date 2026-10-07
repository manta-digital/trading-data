---
docType: slice-design
slice: backup-coverage-for-tick-archive-and-database
project: trading-data
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [226]
interfaces: [228, 230, 231, 232, 234]
dateCreated: 20261004
dateUpdated: 20261006
status: complete
---

# Slice Design: backup-coverage-for-tick-archive-and-database

## Overview

The tick cluster `17/tick` (port 5433, data at `/data/postgresql/17/tick`, database `trading_tick`) has no backup of any kind today. Its archive, `/data/tick-archive`, has been in the nightly restic backup since slice 223.

This slice:
- chooses the tick database's backup policy from the rebuild cost slice 226 measured;
- extends the existing 915/920 backup tooling (weekly `pg_basebackup`, WAL archiving, keep-days pruning, B2 offsite copy, health flags) to a second cluster by making it per-cluster, rather than copying it;
- extends the tick role set so the tick cluster can be base-backed-up;
- enrols the tick cluster through one PM-run cutover;
- records placement, retention and exclusions in the runbooks.

**Policy decision (TD1): the tick database gets the same weight as production: a weekly base backup, continuous WAL archiving, and a nightly bookkeeping dump. Rebuilding from the archive stays the documented fallback.**

**Split (2026-10-04):** the restore drill that proves both the archive and the database come back, and both restore procedures, are slice 228. This slice delivers backups that exist and verify; 228 proves they restore.

**Done separately:** the health-check fix this design first scoped, which handles the `<segment>.<offset>.backup` name a base backup leaves as `last_archived_wal`, landed on main as a plain fix (5e4a8ac). The tick cluster's weekly backup would have triggered the same failure.

## Value

- **Real purchased data stops depending on one disk.** Slices 230–232 spend real money on the Standard plan. After this slice, the bookkeeping that describes the files (which jobs were bought, what they cost, what superseded what) is in the backup set next to the files.
- **Restore is independent of outside systems.** Restoring from a base backup plus WAL needs only `/data/backup` or B2. A rebuild from the archive also needs the production database (for the CME calendar) and the Databento API (for `batch_job` records).
- **One backup system for two clusters.** The same scripts, flags and runbook cover both clusters. When the tick cluster later moves to hammerhead, it is enrolled the same way (one more row in the table), and `pg_basebackup` is already the move mechanism the architecture names.

## Technical Scope

**Included**
1. **Cluster table.** A checked-in table, `deploy/backup-clusters.conf`, defines which clusters are backed up, where each one's backups go locally and offsite, and when its jobs run. Rows: `17/main` (current values, unchanged) and `17/tick`.
2. **`deploy/setup-backup.sh` made per-cluster.**
   - It loops over the table.
   - Per-cluster steps: directories, the WAL directory ACL, and the `archive_mode`/`archive_command`/`wal_compression` settings.
   - Host-wide steps run once: restic, timeshift, the cron file render, the leftover-crontab report.
   - The cluster's data directory is read with `pg_lsclusters` rather than built from `/var/lib/postgresql`.
   - `--cluster` is removed. The table is the membership.
   - `--backup-root` stays, and now names only the **host root** (`/data/backup`): where the host-wide restic stamp, log and lock live. Each cluster's own root comes from its table row. Every cluster's health check reads the restic stamp from the host root.
3. **Cron template split** into a host-wide part (the two restic lines) and a per-cluster block (health check, WAL push, nightly metadata dump, weekly base backup), rendered once per row into the single `/etc/cron.d/manta-trading-backup`.
4. **Cron wrappers take the URL key as an argument.** `cron_weekly_backup.sh`, `backup_health_cron.sh` and `cron_nightly_metadata.sh` gain `--url-key <ENV_KEY>` in place of the hard-wired `MT_TIMESCALE_MAINTENANCE_URL`.
   - `cron_weekly_backup.sh` gains an explicit `--replication-host <host>` in place of its hard-coded `@192.168.1.144:` → `127.0.0.1` rewrite.
   - `cron_nightly_metadata.sh` gains `--remote` in place of reading `MT_BACKUP_S3_BUCKET`.
5. **Tick roles and access for base backups.**
   - `scripts/provision_tick_roles.sql` gains `-v with_replication=1`, which grants REPLICATION to `tick_migrate`, matching `provision_roles.sql` on production.
   - `scripts/provision_tick_cluster.sh` adds a `host replication tick_migrate <src>/32 scram-sha-256` line to the tick `pg_hba.conf`.
   - It sets `/data/postgresql` and `/data/postgresql/17` to 0755, matching `/var/lib/postgresql`. The data directory itself stays 0700. Today both parents are 0700, so the health check (run as manta) cannot `df` the tick data directory for its free-disk check and would raise ARCHIVE-BROKEN on every firing.
   - It also splits its database list: `trading_tick` is created; `trading_tick` and `trading_tick_drill` are allowed in `pg_hba` (the drill database is 228's). The proof database is gone, so a re-run must not recreate it.
6. **Cutover script**, `scripts/cutover_227_tick_backup.py`. The PM runs one command. It:
   - enrols the tick cluster;
   - restarts the tick unit for `archive_mode`;
   - takes and verifies the first tick base backup;
   - pushes it offsite;
   - arms the tick reconcile;
   - writes the report.
7. **Runbooks.**
   - Runbook 200 gains a tick-cluster placement row, the per-cluster cron table, tick retention, and the exclusions table (TD9).
   - Runbook 210's `setup-backup.sh` invocation and its restic include list (stale since 223) are corrected.

**Excluded**
- The restore drill and both restore procedures. That's slice 228.
- Moving the tick cluster to hammerhead. That move adds a table row and its own host bootstrap.
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
        └──read by──► scripts/cutover_227_tick_backup.py   (and 228's drill)
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

### State Management

| State | Where | Owned by |
|---|---|---|
| Backup membership, per-cluster paths, schedules | `deploy/backup-clusters.conf` | this slice |
| Tick base backups, WAL, metadata dumps | `/data/backup/17-tick/{base,wal,metadata}` | per-cluster cron block |
| Tick offsite copy | `b2:<bucket>/17-tick/{base,wal,metadata}` | per-cluster cron block |
| Tick health flags and stamps | `/data/backup/17-tick/{ARCHIVE-BROKEN,BACKUP-STALE,wal-offsite.stamp,wal-offsite.lock,RECONCILE-ARMED}` | per-cluster cron block |
| Production (unchanged paths) | `/data/backup/{base,wal,metadata,…}`, `b2:<bucket>/{base,wal,metadata}` | per-cluster cron block |

**Shared volume.** Tick backups share the `/data` volume with production's backups and with the tick cluster itself (1.8 TB, 1.2 TB free on 2026-10-04). Production's data and WAL are on the root disk, so a full `/data` cannot stop production's database. It would stop production's WAL archiving and base backups. Two things keep that from happening silently:
- **Size:** a week of tick WAL is bounded by a week of ingest. The largest planned purchase, the Standard plan's included year of ES+GC tbbo, is about 5 GB stored, which is under 0.5 % of the free space.
- **Alarm:** the tick health check's existing `wal_disk_low` check runs `df` on the tick data directory, which is on `/data`. It raises ARCHIVE-BROKEN below 15 % free, long before production's archiving can fail. Production's own check watches the root disk, so this is the first alarm that watches `/data`.

The tick root sits inside production's root, but no production job touches it. Production's jobs only ever name `base/`, `wal/` and `metadata/` explicitly, and restic does not include `/data/backup`. A unit test asserts that the main block's rendered lines never name `17-tick` (TD3).

## Technical Decisions

### TD1. Policy: full weight (base + WAL + metadata dump); rebuild-from-archive as the fallback

**Option 1: rebuild from the archive on restore (rejected).** Its cost is small (212.5 s plus compression for 27.7 M rows; about 6 minutes per year of ES+GC at tbbo). But it gives back the data and the money, not the history:
- **Lost history.** Slice 224's design for adopting existing batch files says: "Provenance is kept only by a durable tick database." Once 230–232 buy through the pass, that provenance is real.
- **Outside dependencies.** A rebuild also needs the production database and the Databento API, so a host-wide loss would chain restores.

**Option 2: WAL-only (rejected).** WAL replay needs a base backup to replay onto, so this isn't really an option.

**Option 3: bookkeeping dump plus rebuilding trades (rejected).** This means restoring the small tables, then re-ingesting `tick_trade` from the archive. It sounds cheap but needs new code: units restore as `ingested` with no rows, so the restore path would need a reset that moves `ingested` back to `verified` and clears the ingest ledger. That's a restore-only code path with no other user, and it is exactly the kind that rots.

**Option 4: full weight (chosen).**
- **Weight:** 677 MB per weekly base today against production's roughly 98 GB, and about 5 GB/yr stored at the ES+GC tbbo projection.
- **No new code:** the tooling exists, and the one-row-per-cluster table is the extension the architecture asks for.
- **Same restore:** the tick cluster restores exactly like production.
- **Why the dump stays:** it keeps the bookkeeping recoverable even if a base/WAL chain is broken. The bookkeeping is under 1 MB, and the dump tool needs no change, because `backup_metadata.sh` already picks "every table that is not a hypertable" from the catalog.

The measured rebuild cost still sets one thing: the fallback is cheap enough for slice 228's drill to run every quarter.

### TD2. One checked-in cluster table, not a repeatable `--cluster` flag

`deploy/backup-clusters.conf` is whitespace-separated, has `#` comments, and holds one row per cluster:

```
# cluster  url_key                        backup_root          remote_subpath  replication_host  metadata_cron  weekly_base_cron
17/main    MT_TIMESCALE_MAINTENANCE_URL   /data/backup         -               127.0.0.1         0 2 * * *      0 3 * * 0
17/tick    MT_TICK_MAINTENANCE_URL        /data/backup/17-tick 17-tick         -                 15 2 * * *     30 2 * * 0
```

- **`-` means "no override".** Production's remote is the bucket root (its existing layout). Tick connects with its URL's host as written (`manta9000`, which only listens on 127.0.1.1).
- **Why a table and not a flag:** a repeatable flag would let a run that names only `17/main` drop tick's cron block without anyone noticing. With the table, membership is defined once.
- **Readers:** bash (`setup-backup.sh`) and Python (the cutover, and 228's drill) both read this one file through one small parser per language. Each parser has a unit test against the real file, per CLAUDE.md's parsing rule.
- **Host root is not a row:** `--backup-root` names the host root for the restic stamp, log and lock (Scope item 2). The table holds only per-cluster roots.
- **Malformed rows:** a malformed row (wrong field count, unknown cluster in `pg_lsclusters`, root not under `/data`) is a hard error before any change.
- **Schedules:** tick's jobs run before production's, so the tick base backup (seconds today) never overlaps production's three-hour one.

### TD3. Production's layout stays exactly where it is

Moving production's backups to `/data/backup/17-main/` would move roughly 300 GB of live backups, the B2 prefixes, the B2 lifecycle rules and the arm file, all for symmetry. Not worth it.

Production keeps its root and prefix. Tick nests under `/data/backup/17-tick` and `b2:<bucket>/17-tick`.

**What protects production:**
- **Same cron jobs:** production's lines gain only the new explicit arguments (`--url-key MT_TIMESCALE_MAINTENANCE_URL`, `--replication-host 127.0.0.1`, `--remote b2:<bucket>`), which carry today's hard-wired values. A test renders the cron file from the real table, strips those argument pairs from production's lines, and asserts the result equals today's installed lines exactly. The cutover applies the same comparison to the file it installs.
- **No gap between merge and cutover:** cron runs the wrappers from this checkout, so the merged wrappers still accept the old lines' missing arguments, with today's values, until the cutover re-renders the file. That compatibility is removed once the cutover has run.
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

The lock is checked again immediately before the restart, not only at step 1. It can't be held across the restart, because the restart ends the session that holds it. No tick timers exist, so the only way a run can start in between is by hand during the cutover.

Production is never restarted. A pending-restart report for `17/main` makes the cutover stop.

### TD7. The first tick weekly run happens in the cutover, and arms itself

The 920 arm file (`RECONCILE-ARMED`) gates the offsite `rclone sync --max-delete` until a person has seen a reconcile run go clean. Tick's offsite prefix starts empty, so the first sync has nothing to wrongly delete.

The cutover:
1. runs the tick weekly job once (base backup, verify, push, prune, guards, final check);
2. creates `/data/backup/17-tick/RECONCILE-ARMED` only if that run's final check passed.

Without this, arming would be a later manual step that waits on a Sunday.

### TD8. Cutover steps

`scripts/cutover_227_tick_backup.py` follows the `cutover_265_trades.py` / `cutover_common.py` pattern. It runs as manta from the checkout root and uses `sudo` for the root steps. Each step prints expected against seen:

1. Production config SHA-256 recorded; tick advisory lock free.
2. `setup-backup.sh --check` shows every `17/main` item OK. `cron.d` reports DRIFT, which is expected (the new arguments); any other non-OK `17/main` item stops the cutover.
3. `sudo scripts/provision_tick_cluster.sh` adds the replication grant, the `pg_hba` line and the parent-directory modes.
4. `sudo deploy/setup-backup.sh` applies: no change for production except the cron arguments (TD3's comparison must pass); directories, settings and the cron block for tick.
5. If `archive_mode` is pending on `17/tick`, check the tick lock again, then restart `postgresql@17-tick` (TD6).
6. `pg_switch_wal()` on tick; its segment appears as `.zst` in `/data/backup/17-tick/wal` within `WAL_SWITCH_WAIT_S` (one named constant, 120 s; `archive_command` normally takes under a second).
7. The tick health job runs once and must log `PASS`. This clears any ARCHIVE-BROKEN or BACKUP-STALE the half-hourly firing set between steps 4 and 6, when the cron block existed but archiving was not on yet. It has to come before step 8, because the weekly job refuses to run while ARCHIVE-BROKEN exists. BACKUP-STALE for the missing base backup is expected here and clears in step 10.
8. The first tick weekly run, then arm (TD7).
9. The tick metadata job runs once, so the first dump exists without waiting for 02:15.
10. The tick health job runs again and must log `PASS … FLAGS archive=0 stale=0`.
11. `rclone check --one-way` of tick `base/`, `wal/` and `metadata/` against `b2:<bucket>/17-tick`.
12. Production config SHA-256 unchanged; production's health log still PASS; `setup-backup.sh --check` (sudo) OK on both rows apart from a lifecycle MISSING, with its full output in the report.
13. The report goes to `user/notes/<date>-227-cutover.md`. Exit 0 only when every check passed.

**On failure.** The script stops at the first failed step, prints what it expected and what it saw, and still writes the report. Recovery is: fix the cause, then re-run the whole script, the same as 265's cutover. Every step is check-then-act, so steps already done report OK and change nothing.

| Step or job | If it fails | Strategy |
|---|---|---|
| Steps 1–2 (lock, production drift) | nothing has changed yet | stop |
| Step 3 (provision) | its own check-then-act; production untouched | stop, re-run |
| Step 4 (setup-backup) | partial apply; each item is idempotent | stop, re-run |
| Step 5 (restart) | tick down or still pending; production untouched | stop; the PM reads `journalctl -u postgresql@17-tick` |
| Step 6 (WAL switch) | no `.zst` within the wait: archiving is broken | stop; tick health will also flag ARCHIVE-BROKEN |
| Step 8 (weekly) | `backup_prod.sh` never leaves a partial at `base/<date>`: a failed copy is removed and a failed verify is moved to `<date>.failed`. A leftover makes the next run refuse until it is inspected. A failed push or B2 outage fails the job's final check, so no arm file. | stop; remove a reported leftover, re-run |
| Step 9 (metadata) | dump or push failed | stop, re-run |
| Step 11 (rclone check) | offsite differs | stop, re-run (the push is idempotent) |
| Steady state: hourly WAL push | B2 unreachable | the stamp ages; BACKUP-STALE after the existing threshold; the next push catches up |
| Steady state: weekly, metadata, health | as for production | the existing flags; nothing new |

A re-run on the same day as a successful step 8 does not take a second base backup: the weekly step is skipped when `base/<today>` exists and the arm file is present.

### TD9. Exclusions are recorded, not changed

| Path | In restic? | Why |
|---|---|---|
| `/data/tick-archive` | yes (223) | the record; immutable, so stored once thanks to deduplication |
| `/data/tick-archive/**/*.partial` | excluded (223) | unfinished copies are not the record |
| `/data/postgresql/17/tick` | no | a file copy of a live data directory can't be restored; covered by base + WAL |
| `/data/backup` (incl. `17-tick/`) | no | goes offsite by rclone to B2 |
| `/data/market-data/databento` | no | the PM's read-only originals; every job there was adopted, so verified copies are in the archive |
| `/data/restore-test` | no | scratch space for drills |

Timeshift does not reach `/data`: its backup device is the `/data` volume, and its 2026-10-01 snapshot holds `/data` as an empty mount point. No exclude is added; runbook 200 records this. Production's data directory, `/var/lib/postgresql/**`, is already excluded.

### TD10. B2 lifecycle for the tick prefix

Production's `wal/` and `base/` prefixes have a 30-day "delete after hidden" rule. Without one, the files tick's syncs delete would stay as hidden versions forever. That's a slow storage-cost leak, not a data risk.

- **Detect:** `setup-backup.sh --check` reads the bucket's rules (`rclone backend lifecycle b2:<bucket>`) and reports MISSING for any row prefix not covered.
- **Apply:** applying a per-prefix rule without replacing the bucket's existing rules is checked against rclone's B2 backend source during implementation.
- **Fallback:** if rclone can't do that, the cutover report names the one rule to add in the B2 console. That would be the slice's only non-script PM step. It does not affect the cutover's exit code, because a missing rule costs storage, not data. `setup-backup.sh --check` keeps reporting MISSING until the rule exists.
- **Phase 6 finding (2026-10-04):** the fallback is the path. The `b2` rclone remote is the S3 backend, which has no `lifecycle` command, so `deploy/lib/b2_lifecycle.sh` reads the rules through rclone's native backend (`:b2:<bucket>`, the same bucket key from `.env`). rclone v1.75.0 `backend/b2/b2.go` `lifecycleCommand` sets `LifecycleRules: []{newRule}` with an empty prefix, which replaces every rule on the bucket, so apply never sets one. Production's rules read back as `base/` and `wal/`, 30 days from hiding to deleting; tick's are `17-tick/base/` and `17-tick/wal/` (two console rules).

### Patterns and Conventions
- **Bash and rendering:** check-then-act bash with explicit required arguments, as in 915/920. `--check` reports drift and changes nothing. Cron render tokens are `@NAME@`.
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
  - Production's rendered lines equal the current `/etc/cron.d/manta-trading-backup` lines once the new arguments are stripped (unit test, and the same comparison in the cutover).
  - Production's config hash is unchanged across the cutover.
  - The next production health firing after the cutover prints `PASS … FLAGS archive=0 stale=0`.

### Database / Storage Schema

There are no migrations. Storage added:

| Item | Size now | Growth |
|---|---|---|
| Tick base backup | 677 MB per weekly copy, 1–2 kept locally and offsite under keep-days 7 | about 5 GB/yr at the ES+GC tbbo projection |
| Tick WAL | bursts during ingest, pruned weekly | bounded by one week of ingest; the ES+GC tbbo year is about 5 GB in total (see Shared volume) |
| Tick metadata dump | < 1 MB nightly | small |

## Integration Points

### Provides to Other Slices
- **228 (restore drill):** tick base backups, WAL and metadata dumps to restore; the cluster table to read paths from; `trading_tick_drill` allowed in the tick `pg_hba`.
- **230–232 (Standard plan purchases, GC):** the tick database, including pass-purchase provenance and spend records, is in the backup set from the first paid job.
- **234 (service wiring):** a backed-up tick cluster to point the service env at.
- **The later hammerhead move:** that host's cluster joins the backup set as one more table row, and a weekly `pg_basebackup` is already producing the copy the move starts from.

### Consumes from Other Slices
- 226's cluster and provisioning script; 223's archive enrolment; 915/920's scripts; 923's tick credentials and preflight.

## Success Criteria

### Functional Requirements
1. `deploy/backup-clusters.conf` lists `17/main` and `17/tick`. `setup-backup.sh` (and its `--check`) reads it, and any malformed row is a hard error before any change.
2. `17/tick` runs with `archive_mode=on`, `wal_compression=zstd`, and an `archive_command` that writes to `/data/backup/17-tick/wal`. A forced WAL switch produces a `.zst` segment there.
3. `/etc/cron.d/manta-trading-backup` holds the host block, production's block (today's lines plus only the new explicit arguments, TD3), and a tick block.
4. After the cutover, `/data/backup/17-tick/base/<date>` holds a base backup that passed `pg_verifybackup`, and `b2:<bucket>/17-tick/{base,wal}` hold matching copies (`rclone check --one-way` clean).
5. The first tick metadata dump exists locally and offsite; the cutover runs the dump job once.
6. `/data/backup/17-tick/RECONCILE-ARMED` exists only after the first tick weekly run passed its final check.
7. The tick health check logs `PASS … FLAGS archive=0 stale=0`.
8. Runbook 200 has the tick placement row, the per-cluster cron table, tick retention and the exclusions table. Runbook 210's bootstrap command and restic include list are correct.
9. Production is untouched: config hash unchanged, never restarted, and its backup paths and prefixes unchanged.
10. The B2 lifecycle rules cover `17-tick/base/` and `17-tick/wal/`, matching production's `base/` and `wal/` rules (`setup-backup.sh --check` reports `OK 17/tick lifecycle …`). rclone cannot add a rule without replacing the bucket's others (Phase 6 finding), so the cutover report names the console rules and the PM adds them (TD10).

### Recovery Targets
Stated as designed, for 228's drill to measure against:
- **Recovery point, local:** the last archived WAL segment. `archive_timeout` is 0 on production and is kept at 0 for tick, so an idle cluster's unfilled segment is the exposure. Ingest fills segments quickly, and an idle cluster has nothing new to lose.
- **Recovery point, offsite:** one hour (the hourly WAL push) plus the same unfilled segment.
- **Recovery time:** not set here. 228 measures it for both procedures (restore from base plus WAL, and the rebuild, which 226 measured at 212.5 s plus compression).

### Technical Requirements
- Unit tests:
  - table parsing (bash and Python) against the real file and a malformed row;
  - the cron render (production lines equal today's once the new arguments are stripped; tick block content; no production line names `17-tick`);
  - the wrappers' new required arguments (missing → exit 2);
  - the `pg_lsclusters` data-directory read;
  - `provision_tick_cluster.sh --check` output with the replication line.
- ruff, mypy and shellcheck (`-x`) clean on touched files.

### Integration Requirements
- 228 can restore the tick cluster from `/data/backup/17-tick` and B2 using paths read from the cluster table.
- 230 can start buying with the tick database already in the weekly backup and the hourly WAL push.

### Verification Walkthrough

Refined in Phase 6 (2026-10-04) with real output. Steps 1 and 1a were run by the agent on manta9000; steps 2–4 are the PM's cutover and its read-back.

1. **Check, change nothing** (agent, before the cutover; ran 2026-10-04 as manta):
   ```
   deploy/setup-backup.sh --check --checkout "$PWD" --env-file "$PWD/.env" --backup-root /data/backup
   ```
   Runs as manta (no root). `--backup-root` is the host root. Seen, and expected until the cutover:
   - `OK` for `package-restic`, `dir-system`, every `17/main dir-*` item with its `-owner-mode` item, `17/main wal-acl-manta`, `17/main arm-file`, `OK 17/main lifecycle base/` and `wal/`, the timeshift keys, `restic-repo`, `user-crontab`.
   - `MISSING 17/main pg-settings psql failed: … role "manta" does not exist` and the same for `17/tick`: PostgreSQL items need root to read. The cutover's steps 2 and 12 run the sudo form.
   - `17/tick`: `MISSING` for `dir-root`, `dir-base`, `dir-metadata`, `dir-wal` (each with a `DRIFT …-owner-mode … 'absent'`), `wal-acl-manta`, `arm-file`.
   - `MISSING 17/tick lifecycle 17-tick/base/ (add in the B2 console: fileNamePrefix=17-tick/base/ daysFromHidingToDeleting=30; …)` and the same for `17-tick/wal/`. These are not counted in `not-ok`.
   - `DRIFT cron.d /etc/cron.d/manta-trading-backup` (the new arguments and the tick block).
   - `SUMMARY applied=0 not-ok=13 mode=check`, exit 1.

   1a. **Provisioning check** (agent; ran 2026-10-04 as manta):
   ```
   scripts/provision_tick_cluster.sh --check
   ```
   Seen: `WOULD set /data/postgresql to postgres 755 (seen: postgres 700)`, `WOULD set /data/postgresql/17 to postgres 755 (seen: unreadable)`, `WOULD apply provision_tick_roles.sql to trading_tick (with_replication=1)`, `SKIP /etc/postgresql/17/tick/pg_hba.conf (needs root to read)`, `OK /etc/postgresql/17/main and postgresql.auto.conf unchanged`, exit 0. The `pg_hba` replication line is only visible to root (the cutover's step 3).

2. **Cutover** (PM, one command, after the code review passes and the slice is merged to main):
   ```
   uv run python scripts/cutover_227_tick_backup.py
   ```
   Runs as manta from the checkout root; it refuses any other user. `sudo` asks for the password at the first root step. Expect twelve `==> Step n: <title>` lines each followed by `PASS`, then `Step 13: report written to project-documents/user/notes/<date>-227-cutover.md — PASS` and exit 0. On the first run, step 4's report legitimately contains `PENDING RESTART 17/tick archive_mode` and `MISSING 17/tick arm-file`; step 5 then restarts `postgresql@17-tick`. If the B2 lifecycle rules are missing, the script ends with `B2 console: MISSING 17/tick lifecycle …` lines and the report has a "B2 console rules to add (TD10)" section; the PM adds those two rules in the console (Buckets → bucket → Lifecycle Settings → custom rules; "days till hide" blank, "days till delete" 30). On a failure: the report names the step and what it saw; fix it and re-run the whole script.

3. **Read the results** (as manta):
   ```
   ls /data/backup/17-tick/base/ /data/backup/17-tick/metadata/   # one <YYYYMMDD>/ base; one meta-<ts>.dump
   ls /data/backup/17-tick/RECONCILE-ARMED                        # present
   tail -1 /data/backup/17-tick/backup-health.log                 # … FLAGS archive=0 stale=0
   tail -1 /data/backup/backup-health.log                         # production: … FLAGS archive=0 stale=0
   rclone check --one-way /data/backup/17-tick/base b2:<bucket>/17-tick/base   # 0 differences
   grep -c 17-tick /etc/cron.d/manta-trading-backup               # 4 (the tick block's four jobs)
   deploy/lib/b2_lifecycle.sh --env-file .env --subpath 17-tick --days 30 base/ wal/   # OK lifecycle 17-tick/base/, OK lifecycle 17-tick/wal/ once the PM has added them
   ```

4. **Production unchanged:** the cutover's step 12 already ran this with sudo and put the full output in the report. To repeat it:
   ```
   sudo deploy/setup-backup.sh --check --checkout "$PWD" --env-file "$PWD/.env" --backup-root /data/backup
   ```
   Expect every item `OK` on both rows and `SUMMARY applied=0 not-ok=0`, exit 0. `MISSING 17/tick lifecycle …` lines may remain until the console rules are added; they do not count.

## Risk Assessment

### Technical Risks
- **Reworking `setup-backup.sh` touches production's backup schedule.** A render error could silently drop or alter a production cron line.
- **The tick restart for `archive_mode`** briefly stops the tick cluster.

### Mitigation Strategies
- **For the render:**
  - the render test comparing production's lines with today's (new arguments stripped);
  - `--check` before applying;
  - the cutover's before/after config hash;
  - the cutover stops if the main block shows any drift.
- **For the restart:** it is the tick unit only, only with the tick lock free, and inside the PM's single cutover run. No tick timers exist yet (the tick schedule is still manual), so no scheduled work is interrupted.

## Implementation Notes

### Development Approach
1. **The cluster table and its parsers**, with tests against the real file.
2. **Wrapper arguments** (`--url-key`, `--replication-host`, `--remote`), plus the template split and render. The production-block test must pass before going further.
3. **`setup-backup.sh` per-row loop and `pg_lsclusters` data directory**, with `--check` run against the live host (read-only).
4. **Tick role and `pg_hba` changes** in `provision_tick_roles.sql` and `provision_tick_cluster.sh` (`--check` run).
5. **`cutover_227_tick_backup.py`**, then the runbook 200/210 updates.
6. **PM:** after review and merge, run the cutover. Agent: close the slice from the report.

### Special Considerations
- **Production safety:** never restart `17/main`, and never move its backup paths.
- **Preflight:** no new `MT_TICK_*` keys, so the tick preflight is unaffected.
- **Credentials:** the replication grant reuses `tick_migrate`'s existing scram password, so no credential is written anywhere new.
