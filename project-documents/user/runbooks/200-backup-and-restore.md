---
docType: runbook
project: trading-data
parent: user/slices/915-slice.backup-and-restore-procedures.md
relatedSlices: [913, 915, 920]
host: <prod_host>
dateCreated: 20260816
dateUpdated: 20260906
status: in_progress
---

# Runbook — Backup and Restore (slices 915, 920)

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

**Applied by `deploy/setup-backup.sh` step 4 (slice 920, 2026-09).** The
directory, its ownership (`postgres:postgres` 0775 — the group bits are the
ACL mask, see Step 7), and the three settings below are check-then-act items
of that script; run `sudo deploy/setup-backup.sh --check …` to audit them and
without `--check` to apply. The settings go in via `ALTER SYSTEM`
(`postgresql.auto.conf`), which takes precedence over `postgresql.conf` and
its `conf.d`:

```
archive_mode = on
wal_compression = zstd
archive_command = 'test ! -f /data/backup/wal/%f.zst && zstd -q -T2 %p -o /data/backup/wal/%f.zst.tmp && mv /data/backup/wal/%f.zst.tmp /data/backup/wal/%f.zst && chmod 644 /data/backup/wal/%f.zst'
```

(The command string above is copied from the script's `ARCHIVE_COMMAND_TEMPLATE`
constant with the backup root filled in; a unit test asserts the two match.)
Atomic: the segment is written to `%f.zst.tmp` and renamed, so a disk-full or
kill mid-write leaves only a `.tmp` (named by the `archive_tmp_leftover`
check), never a short file under the final name that `test ! -f` would then
honour as "already archived" — the 2026-09-02 wedge. Compressed: measured
1.88× over 200 segments at 56 ms/segment (slice 920 D3).

**Remove the hand-set lines once `--check` is green.** Before slice 920,
`archive_mode` and `archive_command` were set by hand in
`/etc/postgresql/17/main/conf.d/915-archiving.conf`. `postgresql.auto.conf`
overrides them, so they are inert, but the file lies about the effective
value and `--check` reports `DRIFT archive_command conf-line` until they are
gone: delete the two lines (or the file), then `sudo systemctl reload
postgresql@17-main`.

Leave `wal_level = replica`. **Never set it to `minimal`** — that silently
breaks archiving and `pg_basebackup` both.

The `test ! -f` is what stops an existing segment being overwritten. A command
that overwrites can corrupt the archive. (With the atomic form above it tests
the final `.zst` name; the only file that can be partial is the `.tmp`.)

The `chmod 644` makes each segment operator-readable, so a PITR restore runs
as `manta` without root. Learned the hard way 2026-08-18: a default ACL on
the directory does NOT survive — `cp` creates segments mode 600 and POSIX ACL
masking strips the named-user entry on every new file. WAL is no more
sensitive than the base backups, which are already operator-readable.
`archive_command` is reload-only (`systemctl reload`), no restart.

Only a change to `archive_mode` itself needs a restart (`--check` reports
`PENDING RESTART archive_mode`; the script never restarts). On a host where
archiving is already on, `archive_command` and `wal_compression` take effect
on the reload the script performs. First-time enable: stop the daemon,
restart Postgres, bring it back:

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

Expect `archived_count` climbing, `last_failed_wal` NULL (or, after the
2026-09-02 incident, still naming segment `…1217…83` — see the quick
reference), and `.zst` files present in the directory. The counter alone is
not evidence — check the directory.

### If archiving breaks — the alarms (slice 920)

Postgres retains every unarchived segment. If `archive_command` fails, `pg_wal`
grows until the filesystem fills and **the server halts**. This is the one way
this work can cause an outage.

Every half hour cron.d runs `scripts/backup_health_cron.sh`, which runs
`scripts/check_backup_health.sh` (wrapping the 915 `check_archive_health.sh`
for the four database checks and adding six of its own). Each failure is a
named `FAIL` line; the last line is always `FLAGS archive=<n> stale=<m>`.
Run it ad hoc with the cron.d arguments (`cat /etc/cron.d/manta-trading-backup`
for the literal line) to see the current state.

**Two flag files, nothing else.** Neither flag is pushed to a person — this
host has no mail transport. Look for them:

```bash
ls -la /data/backup/*-BROKEN /data/backup/*-STALE 2>/dev/null   # nothing listed = healthy
journalctl -t manta-backup --since '-7 days'                    # one line per flag raise/clear, per failed push/backup
```

- **`/data/backup/ARCHIVE-BROKEN`** — the local chain is suspect. The weekly
  job refuses to take a base backup while it exists (a backup on a suspect
  chain is what the gate prevents). Also written when the check cannot run
  at all (no URL in the env file, checker crashed): unreachable is an alarm.
- **`/data/backup/BACKUP-STALE`** — a tier has stopped succeeding. Alarm only;
  it does **not** gate the weekly base backup (blocking a healthy local tier
  because a remote tier is degraded would trade a backup for an alarm).

Both clear on the next healthy check. The ten named failures:

| Name | Class → flag | Cause | Fix |
|---|---|---|---|
| `archive_mode_off` | archive → `ARCHIVE-BROKEN` | `archive_mode` is not `on` | `sudo deploy/setup-backup.sh …` (step 4) — a restart is reported as `PENDING RESTART`, never performed |
| `archiver_failing` | archive → `ARCHIVE-BROKEN` | the most recent archive attempt failed (`last_failed_time` newer than `last_archived_time`) | fix the destination (space, permissions, a wedge below); the archiver drains its backlog on its own. Do not delete from `pg_wal` by hand |
| `unarchived_backlog` | archive → `ARCHIVE-BROKEN` | more than 4 GiB of WAL awaiting archive | as above; watch `df -h /data` |
| `wal_disk_low` | archive → `ARCHIVE-BROKEN` | the `pg_wal` filesystem is below 15% free | free space; then the archiver catches up |
| `prune_permission` | archive → `ARCHIVE-BROKEN` | the cron user cannot create+delete a file in `wal/` (the ACL is gone or masked) | `sudo deploy/setup-backup.sh …` (step 3 re-applies the ACL; step 2 the 0775 mode the mask needs) |
| `archive_wedged` | archive → `ARCHIVE-BROKEN` | a short raw file sits at the next-to-archive name with no `.zst` sibling (the 2026-09-02 shape) | see the quick reference: confirm `pg_wal` still holds it, `mv` it aside, never delete |
| `archive_tmp_leftover` | archive → `ARCHIVE-BROKEN` | a `*.tmp` in `wal/` older than 10 min — an atomic write that never finished | `df -h /data`; remove the `.tmp`; the archiver rewrites the segment |
| `offsite_wal_stale` | stale → `BACKUP-STALE` | `wal-offsite.stamp` missing or older than 3 × the push interval (three missed hourly pushes) | `tail /data/backup/wal-offsite.log`; run the push line from cron.d by hand; check B2 credentials/reachability |
| `system_backup_stale` | stale → `BACKUP-STALE` | `system-backup.stamp` missing or older than 2 days | `tail /data/backup/system-backup.log`; run the restic line from cron.d by hand as root; a stale restic lock is cleared by the job itself (`restic unlock`) |
| `weekly_base_stale` | stale → `BACKUP-STALE` | newest `base/<YYYYMMDD>` is older than 9 days (the weekly job aborted — its guards are abort paths — or never ran) | `tail /data/backup/base.log` for the refusal reason; fix it; run the weekly line by hand |

Thresholds are named constants at the top of `check_backup_health.sh`
(`WAL_SEGMENT_BYTES`, `TMP_LEFTOVER_MAX_AGE_MIN`, `SYSTEM_BACKUP_STALE_DAYS`,
`WEEKLY_BASE_STALE_DAYS`) and `check_archive_health.sh`
(`MAX_UNARCHIVED_BYTES`, `MIN_FREE_PCT`). The class of each name lives in one
array in `check_backup_health.sh`; a unit test asserts this table names the
same ten.

By hand, the underlying query:

```bash
psql "$MAINT" -c "SELECT last_archived_wal, last_failed_wal, last_failed_time, failed_count FROM pg_stat_archiver;"
df -h /data
```

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

**Since slice 920, empty the restored `postgresql.auto.conf` first.** The
production settings now live in `postgresql.auto.conf` (`ALTER SYSTEM`),
which the base backup carries and which overrides `postgresql.conf` — so a
restored tree started as-is would have `archive_mode = on` and the
production `archive_command`, and could write into the live archive. The
915 drills did not have this hazard because the settings were in `conf.d`,
which the backup does not carry.

```bash
: > /data/restore-test/postgresql.auto.conf     # must be empty before every start
grep -c archive /data/restore-test/postgresql.auto.conf   # 0
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

## Step 7 — Schedule it (cron.d, slice 920)

Backups stay on cron — decided 2026-08-22 (slice 916): cron is installed and
proven, and acquisition's move to systemd timers does not pull the backup
jobs with it. **Amended by slice 920:** the schedule is no longer hand-edited
into the `manta` crontab; it is the root-owned file
`/etc/cron.d/manta-trading-backup`, rendered by `deploy/setup-backup.sh`
(step 5) from `deploy/cron.d/manta-trading-backup` with the checkout, env
file, backup root, and cron user substituted. Change the template or the
script's constants and re-run the script; `--check` reports `DRIFT cron.d`
when the installed file differs from a fresh render. Six entries:

| When | User | Job |
|---|---|---|
| `*/30 * * * *` | manta | `backup_health_cron.sh` — the ten checks, two flags (above) |
| `0 * * * *` (from `WAL_OFFSITE_INTERVAL_MIN=60`) | manta | `sync_wal_offsite.sh` — additive `rclone copy` of `wal/` to `b2:$BUCKET/wal`, touches `wal-offsite.stamp` |
| `0 2 * * *` | manta | `cron_nightly_metadata.sh` (unchanged from 915) |
| `0 3 * * 0` | manta | `cron_weekly_backup.sh --keep-days 7` — base backup, catch-up push, check, prune, guards, and (only while `RECONCILE-ARMED` exists) the offsite mirror |
| `0 4 * * *` | root | `cron_system_backup.sh` — restic snapshot of `/etc`, `/root`, crontabs, `/home/manta` |
| `0 5 1 * *` | root | `cron_system_backup.sh --check` — `restic check --read-data-subset=5%` |

The push interval appears in exactly one place in the repository
(`WAL_OFFSITE_INTERVAL_MIN` in `setup-backup.sh`); it renders the schedule,
the health check's `--stale-after` (3 ×) and the push's `--timeout` (−1 min).
`cat /etc/cron.d/manta-trading-backup` is the literal, argument-complete
form of every job — copy a line from there to run a job by hand.

**One-time removal of the 915 user-crontab lines (done 2026-09-06).** The
three entries the 2026-08-18 install put in `crontab -e` (half-hourly health
check, nightly metadata, weekly base) must be deleted once cron.d is
installed, or the jobs run twice; their two glue scripts were deleted from
the repository after cron.d was observed firing. The script never edits the user
crontab; `--check` reports `DRIFT user-crontab still runs: …` while they
remain. Keep the `@reboot rclone mount google-drive:` line.

**Retention (slice 920 D4a):** `--keep-days 7`, derived from capacity at the
incident rate, not from the 21 days 915 chose at the steady-state rate:

| Rate | 7 days: 2 bases + WAL | 21 days: 4 bases + WAL | Fits 1.8 TB `/data`? |
|---|---|---|---|
| 16 GiB/day (measured 2026-09-05) | ~200 + 112 = ~310 GB | ~400 + 336 = ~740 GB | both |
| ~100 GB/day (the 2026-09 drain) | ~200 + 700 = ~900 GB | ~400 + 2,100 = ~2.5 TB | **7 only** |

`/data` also holds timeshift's two snapshots (~35 GB each). Offsite retention
equals local retention — one knob — because the weekly reconcile mirrors
`wal/` and purges every `base/<date>` prefix that is no longer retained
locally. Watch `df -h /data` on the monthly pass anyway.

**B2 lifecycle rule (server-side backstop, set once by the PM at cutover):**
catches the case where the host itself is gone for weeks and nothing
reconciles. In the B2 console: Buckets → `manta.trading.data` → Lifecycle
Settings → "Use custom lifecycle rules" → add one rule per prefix, `wal/`
and `base/`, preset **"Keep prior versions for this number of days: 30"**
— that is `daysFromHidingToDeleting = 30` with `daysFromUploadingToHiding`
**unset**. It physically deletes versions the reconcile already hid (an
rclone delete on B2 hides, it does not remove) 30 days later, and never
touches the current version of a live object. **Do not set "days till
hide"**: that hides every current object N days after upload — a hard cap
on how long a dead host's last backups survive, the opposite of a backstop.
No rules on `metadata/` or `system/` (rclone sync and restic manage their
own; restic must never have current files hidden from under it). Paste the resulting rule JSON below when set:

```
(rule JSON — filled in at the cutover, Task 8.2)
```

**The reconcile arm file `/data/backup/RECONCILE-ARMED`.** The weekly job
runs its base backup, catch-up push, checksum check, prune, and the four
guards every week regardless; it performs the destructive step — `rclone
sync --max-delete <pruned + 50>` of `wal/` and the purge of offsite
`base/<date>` prefixes absent locally — **only while this file exists**.
Otherwise it logs `reconcile skipped: not armed` and exits 0. Nothing
creates the file but a person: after watching the first reconcile by hand
(unarmed run: guards pass, `skipped`; then `touch` the file and run again
watching the sync — the 920 drill), `touch /data/backup/RECONCILE-ARMED`.
A rebuilt host starts unarmed; `setup-backup.sh --check` reports the file's
state as `MISSING arm-file` until it is created. Remove the file to disarm
at any time.

---

## Drill record

| Proven | Date | Duration / outcome |
|---|---|---|
| Full restore drill (Step 6): counts + all 9 caggs | 2026-08-17 | 13m47s extract, 42s replay; all content exact; found+fixed a real pre-existing cagg staleness (SPMA 2026-06-18) |
| PITR both directions (sentinel) | 2026-08-18 | ~14m extract + ~1m replay per direction; absent-before / present-after |
| Offsite round trip | 2026-08-17/18 | up 4h44m–5h06m, down 2h05m, checksums 0 differences (rclone ≥ 1.75 required) |
| Alarm fire + self-recovery | 2026-08-18 | FAIL within one check; backlog drained unaided in <20 s |
| Offsite reconcile rehearsal (slice 920 Task 5.6) against a scratch prefix `b2:<bucket>/scratch-920/` with a 50-file fake archive under `/data`, the real cluster for the guards, `cron_weekly_backup.sh --skip-base-backup` | 2026-09-06 | Unarmed run: push 50, check 0 differences, prune 0, `reconcile guards passed`, `reconcile skipped: not armed`, exit 0, offsite 50. Armed run after deleting 5 local files: `rclone sync --max-delete 50` left offsite at exactly 45, final check 0 differences. Armed run with the local directory emptied: `reconcile refused: WAL directory … is empty`, exit 1, offsite still 45. Two earlier runs with a mis-built fixture (manifest start segment newer than every file) had the prune remove all files and the guards refuse — offsite untouched both times. Scratch prefix purged; `rclone lsf` of it empty |

| 920 cutover settings evidence (Task 8.3) | 2026-09-06 | `setup-backup.sh` applied 13:17 local (run 1: `APPLIED` restic, `dir-system`, cron.d, `count_weekly`, restic-repo; after the `--as-user` fix, `APPLIED archive_command`; run 2: `SUMMARY applied=0`). `pg_settings`: `archive_mode=on`, `wal_compression=zstd`, `archive_command` = the D2 form; `sourcefile` postgresql.auto.conf for `archive_command` (`archive_mode` still reported from `conf.d/915-archiving.conf` until that file was removed). `pg_switch_wal()` at 13:23: `…1244…F2.zst` landed within 6 s, no `.tmp`, `last_archived_wal` advanced; the three `.zst` segments are 1.4–6.3 MB each (16 MiB raw). `getfacl`: `user:manta:rwx` + `default:user:manta:rwx`. timeshift: UUID `277accd4-…` unchanged, `count_weekly` 2, the four excludes |
| 920 cron-driven evidence — health check and push fired from cron.d (Task 8.4) | 2026-09-06 | `/etc/cron.d/manta-trading-backup` has the six D7 entries (4 × manta, 2 × root), installed 13:17. cron logged no `RELOAD` line at its default log level; the evidence is the firing: 13:30:01 `journalctl -t CRON` shows `(manta) CMD (… backup_health_cron.sh …)`, `backup-health.log` gained its first line, `BACKUP-STALE` appeared, `journalctl -t manta-backup` shows the raise. 14:00:01 push: `journalctl -t CRON` shows `(manta) CMD (… sync_wal_offsite.sh …)` and `wal-offsite.log` gained `skipped: previous run active` — the by-hand full first push (started 10:08) still held the lock, so the overlap guard was observed live from cron. Two distinct cron.d entries executed by cron. First cron-touched stamp mtime: (recorded once the full push completes) |
| 920 alarm drill — six new failures fire and clear, two-flag gate behaviour (Task 9.1) | 2026-09-06 | Real conditions first: the 13:30 cron.d run raised `BACKUP-STALE` for `offsite_wal_stale` + `system_backup_stale` (no push/restic stamp yet), journal `BACKUP-STALE raised: …`. Planted as `manta` via the ACL at 13:30: 1000-byte file at `wal_segment_name.py next` of `last_archived_wal` (…1244…FA) + `drill.zst.tmp` aged 20 min → `FAIL archive_wedged`, `FAIL archive_tmp_leftover`, `ARCHIVE-BROKEN` written by the glue, journal `ARCHIVE-BROKEN raised: …`; `cron_weekly_backup.sh` with the cron.d arguments refused on the flag before reading its env file. `mv` the planted file aside + `rm` the `.tmp` → next glue run removed `ARCHIVE-BROKEN`, journal `ARCHIVE-BROKEN cleared`; with only `BACKUP-STALE` present the weekly job got past the flag and failed on a deliberately nonexistent `--env-file`. Ad hoc: scratch `--base-dir` with only `20260801/` → `FAIL weekly_base_stale` (36 days); push stamp aged 4 h → `FAIL offsite_wal_stale` (240 min); restic stamp aged 3 days → `FAIL system_backup_stale` (4320 min); fresh stamps → `FLAGS archive=0 stale=0`. `prune_permission`: 2026-09-07 06:41 PM ran `sudo setfacl -x u:manta /data/backup/wal` → `FAIL prune_permission: manta cannot create /data/backup/wal/.prune-canary.tmp …`; `sudo deploy/setup-backup.sh …` → `APPLIED wal-acl-manta`, `OK wal-acl-manta`. **All six observed.** Nit found: the glue's `raised:` journal line lists every FAIL name, not only the flag's class |
| 920 PITR across the mixed raw/`.zst` archive, local (Task 9.2) | 2026-09-07 | Base 20260906 (taken 03:00 Sunday by the last 915 cron run; 100 GB `base.tar.gz`), extraction 990 s. Sentinel `pitr_sentinel_920` committed 06:36:31.355 local, bracketed 06:36:31.307 / 06:36:33.402. Drill conf: the two-shape `restore_command` verbatim, `recovery_target_action = 'pause'`, plus `max_locks_per_transaction = 2048` (archive recovery refused without it — the primary sets it via `ALTER SYSTEM`, which the emptied `postgresql.auto.conf` no longer carries). Replay: 833 segments restored in ~90 s (raw before the 13:17 cutover, `.zst` after — both shapes served by one command), `recovery stopping before commit of transaction 74991502`, paused; `to_regclass('pitr_sentinel_920')` NULL, last replayed commit 06:36:30.636. Stop, target → after, start: one more segment, `recovery stopping before commit of transaction 74991504`, row present. Absent-before / present-after |
| 920 PITR from B2-sourced WAL only (Task 9.3) | 2026-09-07 | `rclone copy b2:$BUCKET/wal /data/restore-test-walb2 --files-from <range list>`: 839 files (…1243…9A through …1246…DF, raw and `.zst`), 9.5 GB, ~2 min. Fresh extraction of base 20260906; `restore_command` pointing only at the pulled directory (the config contains no `/data/backup` path); target = after the sentinel. 833 segments restored from B2-sourced files, `recovery stopping before commit of transaction 74991504`, paused, sentinel row present. First attempt failed with `recovery ended before configured recovery target was reached` because the pulled range stopped at the sentinel's own segment …D9 while the after-target lies in …DA — pull past the target segment, not up to it |
| 920 watched first offsite reconcile — unarmed, then armed (Task 9.4) | 2026-09-06/07 | All by hand with the cron.d line plus `--skip-base-backup` (the last 915 cron run had taken base 20260906 at 03:00 Sunday). Unarmed 21:00: push 15 min, check 0 differences over 6,672 files, `PRUNED wal=0 base=0`, then **guard 4 refused**: base 20260830's start segment …11CD…23 was absent locally (only its history file remained) — offsite untouched, exit 1, journal line. Unarmed 06:37: aborted at the pre-prune check on one false "missing" (a segment archived mid-push) — fixed in `cron_weekly_backup.sh` (checks skip everything younger than their push). Unarmed 07:59: 0 differences, `pruning base backup /data/backup/base/20260830 (older than 7 days)`, `reconcile guards passed`, `reconcile skipped: not armed`. `touch /data/backup/RECONCILE-ARMED`; armed 08:18: 0 differences, guards passed, `rclone sync --max-delete 50` 10 min, `removing offsite base … 20260816 / 20260817 / 20260830 (absent locally)`, final check 0 differences, done 08:54. `rclone lsf --dirs-only b2:$BUCKET/base/` → `20260903/ 20260906/`. Non-root `--check` afterwards: every item OK apart from the PostgreSQL item it cannot read; the root `--check` closes success criterion 1 |
| 920 restic first snapshot, size, check, restore diff (Task 9.5) | 2026-09-07 | First snapshot taken **by cron** (04:00 root entry, no hand run): 04:00:01 → 09:09, `system backup OK`, stamp touched, `BACKUP-STALE` cleared on the next health run. Snapshot `34e6a3d1`: 262,541 files, 107.575 GiB restore size (the `du` estimate was ~108 GB; no separate `--dry-run` taken — the real run is the number), restic compression 1.12×. `restic check --read-data-subset=5%`: 263 packs read, no errors, 56 s. Restore of `/etc/postgresql`, `/etc/timeshift`, `/var/spool/cron/crontabs`, and the checkout's `deploy/` into `/data/restore-test/restic` (52 files, instant); `diff -r` clean for `/etc/timeshift`, `deploy/`, `postgresql.conf`, and the `manta` crontab (against `crontab -l`); `pg_hba.conf`/`pg_ident.conf` are root-readable only — diffed by the PM with sudo |

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
restore_command = 'test -f /data/backup/wal/%f.zst && zstd -dq /data/backup/wal/%f.zst -o %p || cp /data/backup/wal/%f %p'
recovery_target_time = '<target>'   # compares against COMMIT timestamps
recovery_target_action = 'promote'
max_worker_processes = 64   # archive recovery refuses to start below the
                            # primary's setting (51 measured); crash recovery
                            # (Step 6) does not check this
max_locks_per_transaction = 2048   # same rule (2026-09-07 drill: primary is 2048,
                            # set via ALTER SYSTEM, so emptying the restored
                            # postgresql.auto.conf drops it — put it here)
```

**The archive is mixed** (slice 920): raw segments archived before the
2026-09 cutover and `.zst` segments after it, until the last raw segment ages
past the 7-day retention window — seven days after the cutover date in the
drill record. The `restore_command` above handles both shapes and stays in
this form permanently; a `cp`-only command fails on the first `.zst` segment
at the worst possible moment. Compression ratio measured 1.88× (D3), so a
week of WAL restores from roughly half the bytes it did before.

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
| `FAIL archive_wedged` (a short raw file sits at the next-to-archive name, no `.zst` sibling — the 2026-09-02 shape) | **Never delete it.** First confirm the source still holds the segment: `ls /var/lib/postgresql/17/main/pg_wal/<name>` (as root). If present, `mv` the partial aside (`/data/backup/wal-partial-<seg>.<reason>`, outside `wal/`) and the archiver re-archives it on its next attempt. If the source has recycled it, the partial is the only copy of that segment's first bytes: keep it, and treat every base backup before that segment as the restore floor |
| `FAIL archive_tmp_leftover` (a `*.tmp` older than 10 min in `wal/`) | An atomic write that never finished (disk full, kill). Check `df -h /data`; remove the `.tmp` — the archiver rewrites the segment from `pg_wal` on its next attempt because the final name never appeared |
| `pg_stat_archiver.last_failed_wal` names `…1217…83` long after recovery | Expected. The view is cumulative, not a current-state flag; it keeps the last failure until a newer one. The health check compares `last_failed_time` against `last_archived_time`, so this alone never fires `archiver_failing`. The quarantined partial from that incident stays at `/data/backup/wal-partial-0083.disk-full` |
