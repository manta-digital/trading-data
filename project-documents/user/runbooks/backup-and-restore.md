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

---

## Step 1 — Grant REPLICATION (done via git; no restart)

`pg_basebackup` needs the REPLICATION role attribute. `GRANT postgres TO
trading_migrate` does not confer it.

```bash
git pull
psql "$MAINT" -v ON_ERROR_STOP=1 -f scripts/provision_roles.sql
psql "$MAINT" -c "SELECT rolname, rolreplication FROM pg_roles WHERE rolname='trading_migrate';"
```

Expect `rolreplication | t`. Idempotent — safe to re-run.

---

## Step 2 — Take a base backup (live; daemon stays up)

```bash
sudo -u postgres mkdir -p /data/backup/base
DEST=/data/backup/base/$(date +%Y%m%d)
sudo -u postgres pg_basebackup -h 127.0.0.1 -U trading_migrate \
  -D "$DEST" -Ft -z -Xs -P
```

`-Xs` streams WAL during the copy, which is what makes a live backup
consistent. Expect a long run — 141 GB source. Record the wall-clock time and
the resulting size:

```bash
du -sh "$DEST"
```

Verify it — **this step is not optional**, it is the difference between a backup
and a hope:

```bash
/usr/lib/postgresql/17/bin/pg_verifybackup "$DEST"
```

Expect `backup successfully verified`.

---

## Step 3 — Metadata dump (small, fast, run nightly later)

These are the tables with no external source — losing them forces a mass
re-pull. This is what the 2026-08-04 incident destroyed.

```bash
sudo -u postgres mkdir -p /data/backup/metadata
psql "$MAINT" -At -c "
SELECT string_agg('-t '||quote_ident(tablename), ' ')
  FROM pg_tables
 WHERE schemaname='public'
   AND tablename NOT IN (SELECT hypertable_name FROM timescaledb_information.hypertables)
   AND tablename NOT IN (SELECT view_name FROM timescaledb_information.continuous_aggregates);"
```

That prints the `-t` list. Feed it to `pg_dump`:

```bash
TABLES=$(psql "$MAINT" -At -c "SELECT string_agg('-t '||quote_ident(tablename),' ') FROM pg_tables WHERE schemaname='public' AND tablename NOT IN (SELECT hypertable_name FROM timescaledb_information.hypertables) AND tablename NOT IN (SELECT view_name FROM timescaledb_information.continuous_aggregates);")
eval pg_dump \"\$MAINT\" -Fc $TABLES -f /data/backup/metadata/meta-$(date +%Y%m%d).dump
ls -lh /data/backup/metadata/
```

Derived from the catalog, so a table added by a future migration is picked up
without editing anything. Should finish in seconds.

---

## Step 4 — Enable WAL archiving (REQUIRES RESTART)

**Do this after 2026-08-24** — the slice-169 criterion-18 check needs continuous
acquisition through that week, and a restart can stall it.

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

Check periodically:

```bash
psql "$MAINT" -c "SELECT last_failed_wal, last_failed_time, failed_count FROM pg_stat_archiver;"
du -sh /var/lib/postgresql/17/main/pg_wal
df -h /data
```

If `last_failed_wal` is non-null: fix the destination (permissions, space), and
the archiver drains its backlog on its own. Do not delete from `pg_wal` by hand.

---

## Step 5 — Offsite to B2

Credentials are in `.env` on this host as `MT_BACKUP_S3_*`. Use a
**bucket-scoped** application key, never the account master key.

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
