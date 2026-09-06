---
docType: runbook
project: trading-data
parent: user/slices/920-slice.backup-hardening-and-host-bootstrap.md
relatedSlices: [913, 915, 916, 917, 919, 920]
host: <prod_host>
dateCreated: 20260906
dateUpdated: 20260906
status: in_progress
---

# Runbook — Host Bootstrap (slice 920)

From a bare Ubuntu 26.04 with PostgreSQL 17 + TimescaleDB to a host that is
production, backed up, and drift-free. Steps are ordered; each has a command
and the output that means it worked. Every `sudo` is its own step. No step
ends in an unspecified repair: if a step's output differs, stop and read the
script's report — the provisioning is check-then-act and re-runnable.

Layout assumed (manta9000's): login user `manta`; data disk mounted at
`/data`; backup root `/data/backup`; dev checkout
`~/source/repos/manta/trading-data` (the backup tier runs from it — see the
process-journal entry of 2026-09-06); production at `/opt/manta-trading`;
cluster `17/main`.

Set once per shell, used below:

```bash
CHECKOUT=~/source/repos/manta/trading-data
ENV=$CHECKOUT/.env
```

## Inputs a replacement host cannot derive

Fetch from the PM's password manager before starting. **Names only here,
never values.**

| Key | Goes in | Password-manager entry |
|---|---|---|
| `MT_BACKUP_S3_ENDPOINT`, `MT_BACKUP_S3_KEY_ID`, `MT_BACKUP_S3_APPLICATION_KEY`, `MT_BACKUP_S3_BUCKET` | `$ENV` | the B2 bucket-scoped key (also configured into rclone's `b2:` remote, Step 4) |
| `MT_BACKUP_RESTIC_PASSWORD` | `$ENV` — **not** `/etc/manta-trading.env` | "restic manta9000 system backup". Losing it loses every system backup; the copy inside the backup is useless without it |
| `MT_TIMESCALE_MAINTENANCE_URL` | `$ENV` | the DDL credential (913: never in `/etc`) |
| `MT_TIMESCALE_DB_URL`, `MT_EODHD_API_KEY`, Kalshi key id + PEM | `/etc/manta-trading.env`, `/etc/manta-trading/` | per runbook 100 and the README env table |

## Step 0 — Preconditions

```bash
lsb_release -ds                     # Ubuntu 26.04 …
pg_lsclusters                       # 17 main 5432 online postgres /var/lib/postgresql/17/main
findmnt -n -o SOURCE,FSTYPE /data   # a device line — /data is a mount, not a directory on /
```

PostgreSQL 17 and TimescaleDB at the pinned versions: runbook 400 Steps 1–3
(package repositories, pinned install, `apt-mark hold`). `pg_lsclusters`
must show the cluster **online** before Step 7; the reconcile guard refuses
a backup root that lives on the root filesystem, which is what an unmounted
`/data` looks like.

## Step 1 — Packages (sudo)

```bash
sudo apt install -y git jq zstd acl rclone restic postgresql-client-17
restic version        # restic 0.18.1 …
rclone version | head -1   # rclone v1.75 or newer — v1.60 hangs on large B2 objects (runbook 200)
jq --version; zstd --version | head -1; getfacl --version | head -1
curl -LsSf https://astral.sh/uv/install.sh | sh && uv --version
```

`setup-backup.sh` step 1 installs restic itself if absent; installing it
here just makes that item `OK` on the first run.

## Step 2 — Dev checkout

```bash
git clone https://github.com/manta-digital/trading-data.git "$CHECKOUT"
cd "$CHECKOUT" && uv sync --extra dev && .venv/bin/mt --version   # prints the version
```

## Step 3 — `.env` (values from the password manager)

```bash
cp "$CHECKOUT/.env_sample" "$ENV" && chmod 600 "$ENV"
# edit $ENV: fill the keys from the table above
grep -c '^MT_BACKUP_' "$ENV"                                    # 5
grep -o '^MT_BACKUP_RESTIC_PASSWORD=' "$ENV"                     # the name prints; the value never does
MAINT=$(grep '^MT_TIMESCALE_MAINTENANCE_URL' "$ENV" | sed 's/^[^=]*=//' | tr -d '"')
psql "$MAINT" -Atc "select 1"                                    # 1
```

Never `source .env` (a `$` in a password gets shell-expanded); every script
greps its keys out, as above.

## Step 4 — rclone remote `b2:`

```bash
rclone config      # new remote, name: b2, type: b2, the bucket-scoped key id + application key
BUCKET=$(grep '^MT_BACKUP_S3_BUCKET' "$ENV" | sed 's/^[^=]*=//' | tr -d '"')
rclone lsf "b2:$BUCKET/"     # base/  metadata/  wal/
```

## Step 5 — Production install (sudo; runbook 100)

```bash
sudo "$CHECKOUT/deploy/install-production.sh" --ref <release-tag>
# … "Install complete. NOTHING HAS BEEN ENABLED …"
sudoedit /etc/manta-trading.env      # fill per runbook 100 (no maintenance URL, no backup keys here)
```

Cutover of the acquisition units is runbook 100's, and separate. Use a tag
(GitHub issue #21: a branch name resolves the stale local branch).

## Step 6 — Restore the database (rebuild only)

Runbook 200: Step 6 for the newest base backup from B2 plus WAL replay, or
its PITR section for a point in time; the "Local disk gone" row of its quick
reference is the order. Skip on a host that keeps its cluster. Expected at
the end: `pg_lsclusters` online, and `psql "$MAINT" -Atc "select count(*) from
pg_stat_user_tables"` is non-zero.

## Step 7 — Provision the backup tier (sudo; the one script)

```bash
sudo -v
sudo "$CHECKOUT/deploy/setup-backup.sh" --checkout "$CHECKOUT" --env-file "$ENV" --backup-root /data/backup --cluster 17/main
```

Read the report. First run on a bare host: `APPLIED` for `dir-base`,
`dir-wal`, `dir-metadata`, `dir-system`, `wal-dir-owner-mode`,
`wal-acl-manta`, `archive_mode`, `archive_command`, `wal_compression`,
`cron.d`, the timeshift keys (if timeshift is installed), `restic-repo`;
then `SUMMARY applied=<n> not-ok=<m>` where the not-OK lines are exactly:

| Line | Meaning |
|---|---|
| `PENDING RESTART archive_mode` | archiving was off; Step 8 |
| `MISSING arm-file /data/backup/RECONCILE-ARMED …` | expected until Step 14 |
| `MISSING timeshift-config /etc/timeshift/timeshift.json` | only on a host without timeshift (see the Timeshift section) |
| `DRIFT user-crontab still runs: …` | only if 915-era crontab lines exist; Step 9 |

Run it a second time: **`SUMMARY applied=0`** — nothing changes on a
re-run. Anything else in either report is investigated before continuing.

## Step 8 — Restart PostgreSQL, only if reported (sudo)

Only when Step 7 printed `PENDING RESTART archive_mode`:

```bash
sudo systemctl stop mt-daily-pass.timer mt-minute-pass.timer mt-kalshi-pass.timer   # runbook 100: pause acquisition
sudo systemctl restart postgresql@17-main
sudo -u postgres psql -Atc "select setting from pg_settings where name='archive_mode'"   # on
sudo systemctl start mt-daily-pass.timer mt-minute-pass.timer mt-kalshi-pass.timer
```

`archive_command` and `wal_compression` are reload-only; the script reloaded.

## Step 9 — Remove what the script must not edit (sudo / PM)

Only when Step 7 reported them:

```bash
crontab -e     # delete the lines naming archive_health_cron.sh, cron_nightly_metadata.sh, cron_weekly_base.sh; keep "@reboot rclone mount"
crontab -l | grep -c 'archive_health_cron\|cron_nightly_metadata\|cron_weekly_base'   # 0
sudo rm /etc/postgresql/17/main/conf.d/915-archiving.conf     # the hand-set archive lines; postgresql.auto.conf now holds them
sudo systemctl reload postgresql@17-main
```

## Step 10 — `--check` green

```bash
sudo "$CHECKOUT/deploy/setup-backup.sh" --checkout "$CHECKOUT" --env-file "$ENV" --backup-root /data/backup --cluster 17/main --check
```

Every line `OK` except `MISSING arm-file` (and `MISSING timeshift-config` on
a host without timeshift). Exit code 1 for those alone is the expected
pre-arm state; anything else is drift to fix and re-check.

## Step 11 — Prove archiving lands compressed and atomic

```bash
psql "$MAINT" -c "SELECT pg_switch_wal();"; sleep 5
ls -t /data/backup/wal | head -2          # newest name ends in .zst; no .tmp
getfacl -p --omit-header /data/backup/wal | grep manta   # user:manta:rwx and default:user:manta:rwx
```

## Step 12 — First restic snapshot (sudo)

Copy the `0 4 * * *` line's arguments from `cat /etc/cron.d/manta-trading-backup`:

```bash
sudo "$CHECKOUT/scripts/cron_system_backup.sh" --env-file "$ENV" --repo-prefix system --exclude-file "$CHECKOUT/deploy/restic-excludes.txt" --stamp /data/backup/system-backup.stamp --log /data/backup/system-backup.log --lock /data/backup/system-backup.lock
# === system backup done: <timestamp> ===
sudo "$CHECKOUT/deploy/lib/restic_repo.sh" --env-file "$ENV" --prefix system run -- snapshots    # one snapshot row
ls -la /data/backup/system-backup.stamp
```

First snapshot on manta9000: ~108 GB of home plus `/etc`, `/root`, crontabs
(measured 2026-09-06, `du` with the exclude file; the true `--dry-run`
number is in runbook 200's drill record once taken).

## Step 13 — Health check PASS

Copy the `*/30` line's arguments from `cat /etc/cron.d/manta-trading-backup`
and run it as `manta`, or wait for the next half hour:

```bash
tail -1 /data/backup/backup-health.log        # … PASS archive healthy … FLAGS archive=0 stale=0
ls /data/backup/*-BROKEN /data/backup/*-STALE 2>/dev/null    # nothing
```

On a fresh host two stale checks need their first success first:
`offsite_wal_stale` clears after the first hourly push (run the `0 * * * *`
line by hand to hurry it), and `weekly_base_stale` needs a `base/<date>`
younger than 9 days (run the `0 3 * * 0` line by hand — it runs unarmed and
takes a base backup, ~2.5 h on manta9000).

## Step 14 — Arm the offsite reconcile (after watching one)

Runbook 200, Step 7, "The reconcile arm file": watch an unarmed weekly run
(`reconcile guards passed`, `reconcile skipped: not armed`), then
`touch /data/backup/RECONCILE-ARMED` and watch the armed run's sync. Then
Step 10's `--check` exits 0 with every item `OK`. A rebuilt host starts
unarmed on purpose.

## Timeshift (the OS rollback layer only)

- Snapshots live on `/data` (`backup_device_uuid` in
  `/etc/timeshift/timeshift.json`), the same device as the archive: each is
  ~35 GB, so the count is a disk-space term. `count_weekly` is 2 (PM:
  "1–2 local snapshots"; it was 3).
- Excludes, exactly as measured on manta9000: `/home/manta/**`,
  `/var/lib/postgresql/**`, `/var/lib/libvirt/**`, `/root/**`. Everything
  timeshift excludes is restic's or PostgreSQL's job. **History:**
  `/home/manta/**` was an *include* until 2026-09-03 and made snapshots
  ~570 GB; do not "fix" it back.
- `setup-backup.sh` step 6 merges only the managed keys (`schedule_*`,
  `count_weekly`, `exclude`) with `jq`; it never touches the device UUID,
  `snapshot_size`, or `snapshot_count`, and prints
  `INFO timeshift-device-uuid <uuid>` so a replacement host's operator can
  confirm it points at the right disk.
- **A host without timeshift** (hammerhead, measured 2026-09-05): step 6
  reports `MISSING timeshift-config /etc/timeshift/timeshift.json` and never
  creates the file — the script cannot know the device UUID. To add
  timeshift: `sudo apt install timeshift`, run `sudo timeshift --list` once
  (it writes the file with the device it picks; select `/data`'s device in
  `timeshift-gtk` or with `--snapshot-device`), then re-run Step 7 to merge
  the managed keys.

## What restic holds, and what it leaves out

Include set (constants in `scripts/cron_system_backup.sh`): `/etc`, `/root`,
`/var/spool/cron/crontabs`, `/home/manta`. Excludes:
`deploy/restic-excludes.txt` (Trash, Steam, caches, uv, `.vscode`, the FUSE
drive mounts, `**/.venv`, `**/node_modules`, and `/home/manta/ai` — PM
decision 2026-09-06; `/home/manta/Pictures` is **included** by the same
decision). Encryption is what makes include-by-default safe: home holds
`~/.ssh` and `.env`, and B2 only ever sees ciphertext.

## Acceptance test record (design D10; Task 10.1)

Filled in by the run from clean state on hammerhead (PM go) or a fresh VM,
following the backup sections of this runbook verbatim with a throwaway
`--backup-root` and a scratch restic prefix, then torn down. Every deviation
found is a runbook bug, fixed here.

| Field | Value |
|---|---|
| Host, date | (pending) |
| Step 7 first run: `APPLIED` items, `SUMMARY` line, duration | (pending) |
| Step 7 second run: `SUMMARY applied=0` | (pending) |
| Expected `MISSING` on this host | `timeshift-config` (no timeshift), `arm-file` |
| Step 11: `.zst` landed, no `.tmp` | (pending) |
| Step 13: `FLAGS archive=0 stale=…` observed | (pending) |
| Restart of the test cluster needed? | (pending) |
| Runbook fixes made during the run | (pending) |
| Teardown verified (`pg_lsclusters`, settings reset, cron.d gone, archive root gone, restic prefix deleted) | (pending) |
