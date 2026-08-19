---
docType: runbook
project: trading-data
parent: user/slices/915-slice.backup-and-restore-procedures.md
relatedSlices: [913, 915]
host: <prod_host>
dateCreated: 20260816
dateUpdated: 20260818
status: in_progress
---

# Runbook — Backup and Restore (slice 915)

Run every step **on the prod host, from the prod checkout**
(`~/source/repos/manta/trading-data`). Steps are ordered; each is standalone.

Set this once per shell — every step below uses it:

```bash
cd ~/source/repos/manta/trading-data
MAINT=$(grep '^MT_TIMESCALE_MAINTENANCE_URL' .env | sed 's/^[^=]*=//' | tr -d '"')
```

**Never `source .env`** — a `$` in a password gets shell-expanded and silently
mangles the credential. Always `grep` it out like the line above.

## Where things live

| Thing | Path / value |
|---|---|
| `PGDATA` | `/var/lib/postgresql/17/main` on `/dev/nvme0n1p2` (`/`) |
| Backup + archive target | `/data` on `/dev/nvme1n1p1` — **separate physical device** |
| `pg_verifybackup` | `/usr/lib/postgresql/17/bin/pg_verifybackup` — **no `/usr/bin` wrapper** |
| Base backups | `/data/backup/base/<date>` |
| WAL archive | `/data/backup/wal` |
| Metadata dumps | `/data/backup/metadata` |
| Replication | admitted from **localhost only** — backups run on this host |
| Backup scripts | `scripts/backup_prod.sh`, `scripts/backup_metadata.sh`, `scripts/check_archive_health.sh` — each requires explicit `--db-url`/`--dest`, none reads the environment |

`/data` is owned by `manta`, so backup directories and the backups themselves
need **no sudo** — everything below except Step 4 runs as the operator.

**The `$MAINT` URL points at `192.168.1.144`, but replication connections are
only admitted from localhost** — anything invoking `pg_basebackup` must swap
the host: `"${MAINT/@192.168.1.144:/@127.0.0.1:}"`. Verified 2026-08-16:
`IDENTIFY_SYSTEM` succeeds via `127.0.0.1` and is refused via the LAN address.

---

## Step 1 — Grant REPLICATION (DONE 2026-08-16; no restart)

`pg_basebackup` needs the REPLICATION role attribute. `GRANT postgres TO
trading_migrate` does not confer it.

The artifact must be applied **as a superuser** (its own header says so):
altering role attributes is beyond the maintenance role. The maintenance
credential's `postgres` membership covers it via `SET ROLE` — no sudo, no
superuser URL:

```bash
git pull
psql "$MAINT" -v ON_ERROR_STOP=1 -v with_replication=1 \
  -c "SET ROLE postgres;" -f scripts/provision_roles.sql
psql "$MAINT" -c "SELECT rolname, rolreplication FROM pg_roles WHERE rolname='trading_migrate';"
```

Expect `rolreplication | t`. Idempotent — safe to re-run.

`-v with_replication=1` is required: the grant is opt-in, because only a role
holding REPLICATION may set it (so an unguarded ALTER aborts the artifact for
every non-superuser executor), and because roles are cluster-wide — a throwaway
test role given REPLICATION could stream production WAL. Without the flag the
artifact prints that it skipped the grant and `rolreplication` stays `f`.

**Applied 2026-08-16**: ran twice, both exit 0, second run emitted no `ALTER
ROLE` (guard held). `trading_migrate` is `rolreplication=t`, still
`rolsuper=f`. Replication connection tested both ways (see above).

---

## Step 2 — Take a base backup (live; daemon stays up)

Use the wrapper — it refuses missing arguments, streams WAL during the copy
(`-Xs`, what makes a live backup consistent), and verifies with
`pg_verifybackup` by **extracting to a sibling scratch dir first** — measured
2026-08-16: PG17's `pg_verifybackup` handles plain format only (tar support
is PG18). The destination only appears once verification passes. A failed
copy removes its partial; a completed copy that fails verification is
preserved at `<dest>.failed` (the copy alone is ~2 h — never discard it to a
verification problem):

```bash
mkdir -p /data/backup/base
./scripts/backup_prod.sh \
  --db-url "${MAINT/@192.168.1.144:/@127.0.0.1:}" \
  --dest /data/backup/base/$(date +%Y%m%d)
```

Expect a long run — 141 GB source. The script prints start/end timestamps and
the verified compressed size; record them. A directory named `*.inprogress`
is a crashed or running backup, never a restorable one.

---

## Step 3 — Metadata dump (small, fast, run nightly later)

These are the tables with no external source — losing them forces a mass
re-pull. This is what the 2026-08-04 incident destroyed.

```bash
./scripts/backup_metadata.sh --db-url "$MAINT" --dest /data/backup/metadata
```

The table list is derived from the catalog at run time, so a table added by a
future migration is picked up without editing anything; `daemon_heartbeat` is
deliberately excluded as runtime state. The script refuses to produce an
empty dump (wrong-database guard).

**First prod run 2026-08-16**: 12 tables, 1.3 s, 4.6 MB
(`meta-20260816T213743.dump`). Restore-proven the same day: `pg_restore` into
a throwaway database, exact `count(*)` matched source on all 12 tables.

---

## Step 4 — Enable WAL archiving (REQUIRES RESTART)

Stop the daemon before restarting. If this restart interrupts acquisition during
the week of the slice-169 criterion-18 check, that check simply gets re-run the
following Monday — it is one query, not a blocker.

Prepare the destination first:

```bash
mkdir -p /data/backup/wal          # /data is manta-owned
sudo chown postgres:postgres /data/backup/wal
sudo chmod 755 /data/backup/wal    # operator needs traversal for PITR; only
                                   # postgres can write. NOT 700 — see below
```

Edit `/etc/postgresql/17/main/postgresql.conf`:

```
archive_mode = on
archive_command = 'test ! -f /data/backup/wal/%f && cp %p /data/backup/wal/%f && chmod 644 /data/backup/wal/%f'
```

Leave `wal_level = replica`. **Never set it to `minimal`** — that silently
breaks archiving and `pg_basebackup` both.

The `test ! -f` is what stops an existing segment being overwritten. A command
that overwrites can corrupt the archive.

The `chmod 644` makes each segment operator-readable, so a PITR restore runs
as `manta` without root. Learned the hard way 2026-08-18: a default ACL on
the directory does NOT survive — `cp` creates segments mode 600 and POSIX ACL
masking strips the named-user entry on every new file. WAL is no more
sensitive than the base backups, which are already operator-readable.
`archive_command` is reload-only (`systemctl reload`), no restart.

Stop the daemon, restart Postgres, bring it back:

```bash
sudo systemctl restart postgresql@17-main
sudo systemctl status postgresql@17-main --no-pager | head -5
psql "$MAINT" -c "SELECT name, setting FROM pg_settings WHERE name IN ('archive_mode','archive_command','wal_level');"
```

**This host does not auto-start Postgres reliably and has a `listen_addresses`
boot race.** Confirm it is reachable from `.102`, not just locally, before
walking away. Then restart the daemon and confirm acquisition resumes.

Verify segments actually land:

```bash
psql "$MAINT" -c "SELECT pg_switch_wal();"
sleep 5
psql "$MAINT" -c "SELECT archived_count, last_archived_wal, failed_count, last_failed_wal FROM pg_stat_archiver;"
ls -la /data/backup/wal/ | tail -5
```

Expect `archived_count` climbing, `last_failed_wal` NULL, and files present in
the directory. The counter alone is not evidence — check the directory.

### If archiving breaks

Postgres retains every unarchived segment. If `archive_command` fails, `pg_wal`
grows until the filesystem fills and **the server halts**. This is the one way
this work can cause an outage.

Check with the health script — it names the failing condition and exits
non-zero (run it ad hoc, and Step 7 schedules it):

```bash
./scripts/check_archive_health.sh --db-url "$MAINT" --pgdata /var/lib/postgresql/17/main
```

Or by hand:

```bash
psql "$MAINT" -c "SELECT last_failed_wal, last_failed_time, failed_count FROM pg_stat_archiver;"
df -h /data
```

If `last_failed_wal` is non-null: fix the destination (permissions, space), and
the archiver drains its backlog on its own. Do not delete from `pg_wal` by hand.

---

## Step 5 — Offsite to B2

Credentials go in `.env` on this host as `MT_BACKUP_S3_*` — **not yet
present on `.144` as of 2026-08-16** (the PM populated the workstation copy
only; measured zero `MT_BACKUP_S3` lines here). PM action before this step:
copy the four values into this host's `.env`. Use a **bucket-scoped**
application key, never the account master key.

`rclone` here is v1.60.1-DEV (2022 build) — test it against B2 before relying
on it.

Configure the remote once (`rclone config`, or write `~/.config/rclone/rclone.conf`):

```
[b2]
type = s3
provider = Other
access_key_id = <MT_BACKUP_S3_KEY_ID>
secret_access_key = <MT_BACKUP_S3_APPLICATION_KEY>
endpoint = <MT_BACKUP_S3_ENDPOINT>
```

Push and verify **by checksum** — a clean exit from `sync` is not evidence:

```bash
BUCKET=$(grep '^MT_BACKUP_S3_BUCKET' .env | sed 's/^[^=]*=//' | tr -d '"')
rclone sync /data/backup/base/<date> b2:$BUCKET/base/<date> --progress
rclone check /data/backup/base/<date> b2:$BUCKET/base/<date> --one-way
```

Expect zero differences. Same for `metadata/`.

---

## Step 6 — Restore drill (the actual deliverable)

A backup that has never been restored is a hypothesis. Do this once before
trusting any of the above, and repeat it periodically. Executed successfully
2026-08-17; the procedure below is as-run (no sudo anywhere — `/data` is
manta-owned and the drill cluster runs as the operator).

**Separation from production, verbatim** (this is where an operator under
pressure can do damage): distinct PGDATA (`/data/restore-test`, owned by
`manta`, not `postgres`); **no TCP at all** (`listen_addresses=''`, socket
only, in a directory only `manta` can reach); distinct invocation (`pg_ctl`
as `manta` — never systemd, never the `postgres` user); `archive_mode = off`
so it cannot write into the production WAL archive. The restored data dir is
self-contained: Debian keeps prod's configs in `/etc/postgresql`, which the
backup deliberately does not carry, so the drill cluster inherits nothing.

```bash
mkdir -p /data/restore-test && chmod 700 /data/restore-test
tar -xzf /data/backup/base/<date>/base.tar.gz -C /data/restore-test
mkdir -p /data/restore-test/pg_wal /data/restore-test/sock
tar -xzf /data/backup/base/<date>/pg_wal.tar.gz -C /data/restore-test/pg_wal
```

(Measured 2026-08-17: extraction of the 152 GB tree took 13m47s.)

Write `/data/restore-test/postgresql.conf`:

```
listen_addresses = ''
port = 5433
unix_socket_directories = '/data/restore-test/sock'
shared_preload_libraries = 'timescaledb'
timescaledb.max_background_workers = 0   # keep policy jobs off the evidence
shared_buffers = 2GB
max_wal_size = 4GB
archive_mode = off
hba_file = '/data/restore-test/pg_hba.conf'
ident_file = '/data/restore-test/pg_ident.conf'
```

Write `/data/restore-test/pg_hba.conf` (socket dir is 0700 manta — only the
operator can connect):

```
local all all trust
```

Then `touch /data/restore-test/pg_ident.conf` and start:

```bash
/usr/lib/postgresql/17/bin/pg_ctl -D /data/restore-test \
  -l /data/restore-test/startup.log -w -t 600 start
```

Recovery replays from `backup_label` + the streamed `pg_wal` (42 s measured).
Connect with `psql -h /data/restore-test/sock -p 5433 -U postgres -d trading`.

Compare content against source. **Exact counts only** —
`pg_stat_user_tables.n_live_tup` and `approximate_row_count` are both badly
wrong on this database:

```bash
psql -h /data/restore-test/sock -p 5433 -U postgres -d trading \
  -c "SELECT count(*) FROM minute_ohlcv;"
psql "$MAINT" -c "SET statement_timeout=0;" -c "SELECT count(*) FROM minute_ohlcv;"
```

(Exact counts are cheap here — columnstore batch metadata; 12 s for 4.46 B
rows.) Repeat for `daily_ohlcv`, `acquisition_state`, `instruments`,
`data_gaps`, `universe_members`. Deltas are explainable writes after backup
start, or failures — 2026-08-17 all six matched exactly (daemon was down).

Check every cagg by **content**, not catalog presence — an interrupted
derived object is presumed damaged. Two comparisons over a closed historical
window (2026-08-17 used Q2-2026), both of which must match:

1. **Carried state**: the same windowed signature (`count`, `sum(volume)`,
   `sum(close)`, bucket extrema) on restored vs prod, for all nine caggs.
2. **Source recomputation**: the cagg's window recomputed from its source
   hypertable on the restored cluster (per its own `view_definition`),
   including the coverage caggs (`minute_coverage` sources
   `minute_4hour_ohlcv`, not the hypertable).

Tear down and reclaim (no sudo — everything is manta-owned):

```bash
/usr/lib/postgresql/17/bin/pg_ctl -D /data/restore-test stop
rm -rf /data/restore-test
```

**Once this passes and B2 holds a verified copy, the 2026-08-10 cloned directory
copy can be deleted.** It is torn, unverified, and superseded.

---

## Step 7 — Schedule it (INSTALLED 2026-08-18)

Cron, not systemd — this host has no units installed and no process manager.
Whether these migrate to systemd timers is an explicit decision inside **slice
916** (supervised production services); the current recommendation recorded there
is to leave them on cron, since cron is installed and proven.
Installed in the `manta` crontab (absolute paths — cron loads no shell
profile). Three entries, all thin glue scripts that grep credentials from the
named `.env` and call the explicit-argument tools:

```cron
*/30 * * * * .../scripts/archive_health_cron.sh --env-file .../.env --pgdata /var/lib/postgresql/17/main --flag /data/backup/ARCHIVE-BROKEN --log /data/backup/archive-health.log >/dev/null 2>&1
0 2 * * *    .../scripts/cron_nightly_metadata.sh --env-file .../.env --dest /data/backup/metadata >> /data/backup/metadata.log 2>&1
0 3 * * 0    .../scripts/cron_weekly_base.sh --env-file .../.env --base-dir /data/backup/base --wal-dir /data/backup/wal --keep-days 21 --health-flag /data/backup/ARCHIVE-BROKEN >> /data/backup/base.log 2>&1
```

(`...` = `/home/manta/source/repos/manta/trading-data`; run `crontab -l` for
the literal lines.) Nothing re-invokes acquisition on this host (measured),
so there is no collision to schedule around.

**How a failure surfaces (the alarm):** the half-hourly health check writes
the flag file **`/data/backup/ARCHIVE-BROKEN`** naming the failed condition,
and `cron_weekly_base.sh` **refuses to take a base backup while the flag
exists** — so a broken archive turns into a missing weekly backup and a loud
file, not a silent gap. The flag clears itself on the first healthy check.
If you see the flag: read it, fix the destination (permissions or space),
and the archiver drains its own backlog — demonstrated 2026-08-18: broke the
directory, alarm fired within one check, restore of permissions drained the
2-segment backlog unaided in under 20 seconds.

**Retention (implemented):** `prune_wal_archive.sh` runs after each weekly
backup: keeps 21 days of dated base backups (never fewer than the newest,
whatever its age), then `pg_archivecleanup`s WAL older than the oldest
*retained* backup's manifest start. Sized against measured WAL rates (idle
0.18 GiB/day, active ~3–8 GiB/day): three weeks is at most ~170 GB against
759 GB free. Watch `df -h /data` on the monthly pass anyway.

---

## Drill record

| Proven | Date | Duration / outcome |
|---|---|---|
| Full restore drill (Step 6): counts + all 9 caggs | 2026-08-17 | 13m47s extract, 42s replay; all content exact; found+fixed a real pre-existing cagg staleness (SPMA 2026-06-18) |
| PITR both directions (sentinel) | 2026-08-18 | ~14m extract + ~1m replay per direction; absent-before / present-after |
| Offsite round trip | 2026-08-17/18 | up 4h44m–5h06m, down 2h05m, checksums 0 differences (rclone ≥ 1.75 required) |
| Alarm fire + self-recovery | 2026-08-18 | FAIL within one check; backlog drained unaided in <20 s |

**Repeat expectation: re-run the restore drill (Step 6, at least the count
checks and one cagg signature) and one PITR direction every quarter, or
after any PostgreSQL/TimescaleDB major upgrade — whichever comes first.
Next due: 2026-11-17.** A backup regime verified once and never again is the
LLD's named rot risk.

---

---

## Point-in-time recovery (tested 2026-08-18, both directions)

Exactly the Step 6 drill procedure, plus three lines in the restored
`postgresql.conf` and one signal file:

```
restore_command = 'cp /data/backup/wal/%f %p'
recovery_target_time = '<target>'   # compares against COMMIT timestamps
recovery_target_action = 'promote'
max_worker_processes = 64   # archive recovery refuses to start below the
                            # primary's setting (51 measured); crash recovery
                            # (Step 6) does not check this
```

Then `touch <datadir>/recovery.signal` and start. The startup log shows
`recovery stopping before commit of transaction ...` at the target and the
cluster promotes read-write. Proven with a sentinel: a row committed at
21:40:57 was absent when targeting 21:40:55 and present when targeting
21:40:59 — replay both stops where instructed and reaches what it should.

Constraint that gates all PITR: **the base backup must start inside archived
WAL coverage.** A backup taken before archiving was enabled cannot replay
across the unarchived gap (the 20260816 backup has this defect; 20260817
onward do not). The weekly cadence keeps this true automatically.

## Recovery quick reference

| Situation | Do this |
|---|---|
| Metadata tables truncated/lost | `pg_restore` the newest `/data/backup/metadata/*.dump`. Does not touch the 141 GB tier |
| Whole cluster lost | Restore newest base backup (Step 6), replay WAL from `/data/backup/wal` |
| Need a specific point in time | Step 6 + the PITR section above |
| Local disk gone | Pull from B2 first (measured: 84.5 GB in 2h05m with rclone ≥1.75; v1.60 hangs on large objects), then as above |
