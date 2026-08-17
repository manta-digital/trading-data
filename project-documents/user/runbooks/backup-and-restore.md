---
docType: runbook
project: trading-data
parent: user/slices/915-slice.backup-and-restore-procedures.md
relatedSlices: [913, 915]
host: <prod_host>
dateCreated: 20260816
dateUpdated: 20260816
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
psql "$MAINT" -v ON_ERROR_STOP=1 -c "SET ROLE postgres;" -f scripts/provision_roles.sql
psql "$MAINT" -c "SELECT rolname, rolreplication FROM pg_roles WHERE rolname='trading_migrate';"
```

Expect `rolreplication | t`. Idempotent — safe to re-run.

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
sudo -u postgres mkdir -p /data/backup/wal
sudo chown postgres:postgres /data/backup/wal
```

Edit `/etc/postgresql/17/main/postgresql.conf`:

```
archive_mode = on
archive_command = 'test ! -f /data/backup/wal/%f && cp %p /data/backup/wal/%f'
```

Leave `wal_level = replica`. **Never set it to `minimal`** — that silently
breaks archiving and `pg_basebackup` both.

The `test ! -f` is what stops an existing segment being overwritten. A command
that overwrites can corrupt the archive.

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
trusting any of the above, and repeat it periodically.

Restore to a **second cluster on a distinct port**. Never over `trading`.

```bash
sudo -u postgres mkdir -p /data/restore-test
cd /data/restore-test
sudo -u postgres tar xzf /data/backup/base/<date>/base.tar.gz -C /data/restore-test
sudo -u postgres tar xzf /data/backup/base/<date>/pg_wal.tar.gz -C /data/restore-test/pg_wal
```

Set `port = 5433` in the restored `postgresql.conf`, then start it:

```bash
sudo -u postgres /usr/lib/postgresql/17/bin/pg_ctl -D /data/restore-test -o "-p 5433" start
```

Compare content against source. **Exact counts only** —
`pg_stat_user_tables.n_live_tup` and `approximate_row_count` are both badly
wrong on this database:

```bash
psql -p 5433 -U postgres -d trading -c "SELECT count(*) FROM daily_ohlcv;"
psql -p 5433 -U postgres -d trading -c "SELECT count(*) FROM acquisition_state;"
psql -p 5433 -U postgres -d trading -c "SELECT count(*) FROM instruments;"
```

Against source (expect small deltas from writes after the backup started):

```bash
psql "$MAINT" -c "SELECT count(*) FROM daily_ohlcv;"
psql "$MAINT" -c "SELECT count(*) FROM acquisition_state;"
psql "$MAINT" -c "SELECT count(*) FROM instruments;"
```

Reference figures measured 2026-08-16: `daily_ohlcv` 65,652,505;
`acquisition_state` 45,537; `instruments` 32,075; `minute_ohlcv`
4,414,650,928.

Check a cagg by **content**, not catalog presence — an interrupted derived
object is presumed damaged:

```bash
psql -p 5433 -U postgres -d trading -c "SELECT count(*) FROM daily_coverage;"
psql "$MAINT" -c "SELECT count(*) FROM daily_coverage;"
```

Tear down and reclaim:

```bash
sudo -u postgres /usr/lib/postgresql/17/bin/pg_ctl -D /data/restore-test stop
sudo rm -rf /data/restore-test
```

**Once this passes and B2 holds a verified copy, the 2026-08-10 cloned directory
copy can be deleted.** It is torn, unverified, and superseded.

---

## Step 7 — Schedule it

Cron, not systemd — this host has no units installed. Add to the `manta`
crontab (`crontab -e`); cron does not load your shell profile, so use absolute
paths:

```cron
# metadata dump, nightly
0 2 * * * cd /home/manta/source/repos/manta/trading-data && ./scripts/backup_metadata.sh >> /data/backup/metadata.log 2>&1

# base backup, weekly
0 3 * * 0 cd /home/manta/source/repos/manta/trading-data && ./scripts/backup_prod.sh >> /data/backup/base.log 2>&1
```

Verify by watching a real scheduled run, not by reading the crontab.

Nothing currently re-invokes acquisition on this host, so there is no collision
to schedule around.

**Retention:** WAL older than the oldest base backup you would restore from is
useless; WAL newer than the newest base backup is mandatory. With weekly base
backups, keep at least 2–3 weeks of WAL. Prune `/data/backup/wal` on that basis
and watch `df -h /data`.

---

## Recovery quick reference

| Situation | Do this |
|---|---|
| Metadata tables truncated/lost | `pg_restore` the newest `/data/backup/metadata/*.dump`. Does not touch the 141 GB tier |
| Whole cluster lost | Restore newest base backup, replay WAL from `/data/backup/wal` |
| Need a specific point in time | Restore base backup, set `recovery_target_time` in `postgresql.conf`, start |
| Local disk gone | Pull from B2 first, then as above |
