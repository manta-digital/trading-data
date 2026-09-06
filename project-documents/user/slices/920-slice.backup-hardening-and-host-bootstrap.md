---
docType: slice-design
slice: backup-hardening-and-host-bootstrap
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [915, 916]
interfaces: [917, 919]
effort: 3
dateCreated: 20260905
dateUpdated: 20260905
status: not_started
---

# Slice Design: Backup Hardening and Host Bootstrap (920)

## Overview

Slice 915 gave production a real backup regime — WAL archiving, weekly
verified base backups, nightly metadata dumps, checksum-verified B2 offsite,
and a drilled restore path. It was sized against pre-Kalshi write rates
(0.2–8 GiB/day of WAL). The Kalshi historical drain then produced ~100 GB/day,
`/data` filled on 2026-09-02, and the incident exposed two defects that had
been present since day one but never exercised:

1. **The weekly prune could never delete from the WAL archive.** The archive
   directory is `postgres`-owned; the cron user (`manta`) had `r-x` only. The
   prune's `pg_archivecleanup` failed silently every week. Nothing noticed
   until the disk was full.
2. **A disk-full event wedged the archiver with no distinct alarm.** The
   failed `cp` left a partial 3.4 MB file at the target name; from then on
   `test ! -f %f` in `archive_command` was true for that segment forever, so
   the archiver retried the same segment indefinitely. The health check
   reported `archiver_failing` — accurate, but indistinguishable from the
   permission failure it was designed for, and the remedy (move the partial
   aside) is nowhere in the runbook.

Recovery was a day of hand-applied host state. **None of it is in the repo.**
A replacement host built from repo plus runbook today would come up without
the ACL, without WAL compression, with 21-day retention that cannot fit, and
with timeshift copying the live PGDATA into its snapshots.

Measured on `manta9000` 2026-09-05 (this design's baseline):

| Item | Measured | Note |
|---|---|---|
| `archive_command` | `test ! -f … && cp … && chmod 644 …` (hand-set in `postgresql.conf`) | not in repo |
| `wal_compression` | `zstd` (via `ALTER SYSTEM`, 2026-09-03) | not in repo |
| `/data/backup/wal` ACL | `user:manta:rwx` + default ACL applied | not in repo; the day-one defect, now fixed by hand |
| Quarantined partial | `/data/backup/wal-partial-0083.disk-full`, 3,457,024 bytes | `pg_stat_archiver.last_failed_wal` still names segment `…1217…83`, `failed_count` 13,124 |
| WAL rate | 1,032 segments in the last 24 h (16 GiB/day); 784/day 7-day mean | catch-up drain still running with Crypto filtered |
| Archive size | 86 GB local, 5,493 files; **0 bytes offsite** | WAL is not offsite |
| Base backups | local: 20260830 (81 G), 20260903 (98 G). B2: 20260816, 20260817, 20260830, 20260903 | **B2 has no prune** — two superseded bases (79 GiB each) persist; `base/20260823` is already absent |
| Crontab | three 915 lines, `--keep-days 7` (was 21) | hand-edited user crontab |
| Timeshift | rsync mode, weekly, `count_weekly 3`, excludes `/home/manta/**`, `/var/lib/postgresql/**`, `/var/lib/libvirt/**`, `/root/**`; snapshots on `/data` | hand-edited `/etc/timeshift/timeshift.json` |
| `/home/manta` | 337 GB on-device; of which Trash 107 G, Steam 46 G, `.cache` 12 G | **no backup layer at all** |
| Tools | rclone 1.75.0, `pg_archivecleanup`, `zstd`, `jq`, `setfacl` present; **restic not installed** (apt candidate 0.18.1) | |

## Value

- **Recoverability that survives the host.** After this slice, WAL is offsite,
  so a B2-only restore reaches any point in the retained window rather than a
  weekly snapshot moment. `/home/manta` and OS config gain a backup layer for
  the first time.
- **The two silent failures become named, tested alarms**, and the wedge
  class is removed at its root by an atomic archive command.
- **Reproducibility.** Every hand-applied item becomes an idempotent script
  step with a drift check, and a bootstrap runbook whose acceptance test is
  a replacement host brought up from repo + runbook alone.

## Technical Scope

**In scope**

1. `deploy/setup-backup.sh` — idempotent, root-run, explicit-argument
   provisioning of all backup host state, with a `--check` drift mode.
2. Archived-segment compression (`zstd`) — `archive_command`,
   `restore_command`, prune, health check, and runbook change together.
3. WAL offsite to B2, continuous, with offsite retention that mirrors local.
4. Health-check additions: six new named failures, each demonstrated firing.
5. restic system-backup layer for `/etc`, `/root`, crontabs, and
   `/home/manta` (with excludes) to B2, layered with two local timeshift
   snapshots.
6. Host-bootstrap runbook (`210-host-bootstrap.md`) and updates to
   `200-backup-and-restore.md`.
7. Cleanup: superseded offsite bases purged by the new offsite prune, and the
   archiver's stale `last_failed_wal` explained in the runbook.

Why host-OS state lands in a maintenance slice: no initiative owns the
host. Host provisioning already lives in this band (916), the 2026-09 incident
was a host-state failure, and the 900 plan entry authorizes items 5 and 6
explicitly. The restic and timeshift work is corrective in the sense that
matters: the host's recovery story is specified (a replacement host from repo
plus runbook) and is currently wrong.

The backup tier is deliberately outside the `mt` surface, extending 915 D5:
it runs as root or with the maintenance credential, reads `/data/backup`
that the service account cannot see under `ProtectSystem`, and orchestrates
external binaries. `mt` is the product's verification surface; the host's is
`setup-backup.sh --check` plus `check_archive_health.sh`.

**Out of scope**

- Replication/standby (915 D7 stands).
- Moving the backup cron to systemd timers (916 decision stands: cron).
- Folding the archive health check into `mt data health` (slice 919). The
  service account cannot read `/data/backup` under `ProtectSystem`, and the
  check needs the maintenance credential. Noted as future work.
- Fixing `install-production.sh --ref <branch>` resolving `/opt`'s stale
  local branch instead of `origin/` (found during the 268 cutover). A
  separate one-line fix; this slice does not touch that script.
- The `@reboot rclone mount google-drive:` crontab line (916: PM-owned,
  excluded).

## Dependencies

### Prerequisites
- 915: the scripts, archive layout, B2 remote, and drill procedure this slice
  hardens. All present.
- 916: the `install-production.sh` mould (check-then-act steps, explicit
  `--ref`, enables nothing) and `/etc/cron.d`-style root-installed files.
- Host packages: `restic` (apt), everything else already installed.

### Interfaces Required
- `MT_TIMESCALE_MAINTENANCE_URL` and the four `MT_BACKUP_S3_*` keys in the
  dev checkout's `.env` (already present). The maintenance credential is
  deliberately absent from `/etc/manta-trading.env` (913), so the backup
  tier keeps running from the dev checkout. That amends decision 4 of the
  2026-08-23 process-journal ADR ("the dev checkout retains exactly two
  operational roles: migrations and the deploy script") to three roles; the
  amendment lands as a dated journal entry (Implementation Notes), not as a
  silent citation. The alternative — scripts run from the pinned `/opt`
  checkout with the maintenance credential in a root-only env file outside
  home — is cleaner and is recorded as future work; it is not taken here
  because it reverses 913's decision to keep that credential out of `/etc`
  files, which is the PM's call, not a maintenance slice's.
- One new key: `MT_BACKUP_RESTIC_PASSWORD` (D9).

---

## Technical Decisions

### D1 — `deploy/setup-backup.sh`: one root-run script, explicit arguments, check-then-act, `--check` mode

Shape follows `install-production.sh`: `set -euo pipefail`, numbered steps,
every step idempotent, recovery is "fix the cause, re-run". Differences
forced by the domain:

- **Arguments, all required, no defaults**: `--checkout <path>` (the dev
  checkout the cron lines point at), `--env-file <path>`, `--backup-root
  <dir>` (`/data/backup`), `--cluster <ver/name>` (`17/main`). The script
  refuses ambient guesses the same way the 915 tools refuse an ambient DB
  URL.
- **`--check`**: report every item as `OK` / `DRIFT <expected> <actual>` /
  `MISSING`, change nothing, exit non-zero on any drift or missing item.
  Step 8's leftover crontab lines are `DRIFT`, not a warning, so success
  criterion 1 cannot pass until the cutover step is done. This is the
  acceptance instrument for D10 and the periodic drift audit.
- **Never restarts PostgreSQL.** Settings that need a restart are applied to
  `postgresql.auto.conf` and reported as `PENDING RESTART`; the operator
  restarts (runbook step). Same discipline as "enables nothing".

Items the script owns, in step order:

| Step | Item | Mechanism | Idempotence test |
|---|---|---|---|
| 1 | Packages | `apt-get install restic` if absent | `dpkg -s` |
| 2 | Archive directories | `mkdir -p` `base/ wal/ metadata/ system/` under `--backup-root`; `wal/` owner `postgres:postgres` 0755 | stat |
| 3 | WAL archive ACL | `setfacl -m u:<cron-user>:rwx -m d:u:<cron-user>:rwx wal/` | `getfacl` contains both entries |
| 4 | PostgreSQL settings | `ALTER SYSTEM SET` as `postgres` for `archive_mode`, `archive_command` (D3), `wal_compression`, then `pg_ctl reload` | `pg_settings.setting` equals expected; `pending_restart` reported |
| 5 | Cron schedule | install `/etc/cron.d/manta-trading-backup` from `deploy/cron.d/manta-trading-backup` with paths substituted (D7) | file content equals rendered template |
| 6 | Timeshift | `jq` merge of managed keys into `/etc/timeshift/timeshift.json` (D8) | each managed key equals expected |
| 7 | restic repository | `restic init` if `restic cat config` fails (D9) | `restic cat config` succeeds |
| 7a | Reconcile arm file | report `<backup-root>/RECONCILE-ARMED` (`OK`/`MISSING`); never created by the script — the watched first reconcile creates it (amended at task breakdown: this is how "the first run is watched, not scheduled" is enforced rather than hoped) | stat |
| 8 | Leftover user-crontab lines | `DRIFT` if `crontab -l -u <cron-user>` still contains any 915 script name (D7); the script never edits the user crontab | grep |

Step 4 supersedes the hand-edited `archive_command` in `postgresql.conf`
because `postgresql.auto.conf` takes precedence. The runbook tells the
operator to delete the hand-set line once `--check` is green, so the file
stops lying about the effective value.

### D2 — Atomic archive command; the wedge class is removed, not just detected

The wedge exists because `archive_command` writes directly to the final
name. Any interruption (disk full, crash, kill) leaves a partial file that
`test ! -f` then honours as "already archived". The fix is the standard
atomic shape: write to a temporary name, `mv` into place. A partial can then
only ever exist under the temporary name, and `test ! -f` on the final name
stays correct.

Combined with compression (D3), the effective command is:

```
archive_command = 'test ! -f /data/backup/wal/%f.zst && zstd -q -T2 %p -o /data/backup/wal/%f.zst.tmp && mv /data/backup/wal/%f.zst.tmp /data/backup/wal/%f.zst && chmod 644 /data/backup/wal/%f.zst'
```

The script writes this as one string from one variable — the same value is
what `--check` compares and what the runbook prints. `%f.zst.tmp` files are
the only partials that can now exist; the health check names them (D5).

The existing quarantined partial (`wal-partial-0083.disk-full`) stays where
it is: the archiver moved past it, and the runbook records why
`pg_stat_archiver.last_failed_wal` will keep naming segment `…83` until the
next real failure (the view is cumulative; it is not a current-state flag —
the health check already compares `last_failed_time` against
`last_archived_time` for that reason).

### D3 — Compress archived segments with `zstd`, decided by measurement, with a mixed-archive transition

`wal_compression = zstd` already compresses full-page images inside WAL.
External compression of the archived segment still gains on everything that
is not an FPI and on the zero-filled tails of switched segments, but the
ratio is not predictable from first principles on this workload. **Go/no-go
is measured before the change is made:** compress a 200-segment sample from
the current archive with `zstd -T2`, and adopt archive compression only if
the ratio is at least 1.5×. Below that, items 3's four coupled changes are
not worth their restore-path complexity, and the design falls back to
uncompressed atomic archiving (D2 without `zstd`), which still delivers the
wedge fix and offsite WAL. The measured ratio goes into the runbook either
way.

If adopted, four things change together, in one commit, because a restore
that finds `.zst` files with a `cp`-shaped `restore_command` fails at the
worst moment:

| Piece | Change |
|---|---|
| `archive_command` | D2 form |
| `restore_command` (runbook, PITR section) | `restore_command = 'test -f /data/backup/wal/%f.zst && zstd -dq /data/backup/wal/%f.zst -o %p \|\| cp /data/backup/wal/%f %p'` — handles both shapes, because the archive holds raw segments from before the cutover and `.zst` after |
| `prune_wal_archive.sh` | `pg_archivecleanup -x .zst`; the extension strip is a no-op on raw names, so a mixed directory prunes correctly. Verified in a scratch directory during task work, not assumed |
| `check_archive_health.sh` | segment-size expectations become "raw = 16 MiB, `.zst` = anything, `.tmp` = partial" |

The archive is **mixed** from cutover until the last raw segment ages past
retention (7 days at current rates). The runbook states this and the
`restore_command` above tolerates it. No conversion of existing segments —
it would rewrite the chain a restore might need this week.

### D4 — WAL offsite: continuous `rclone copy`, weekly `rclone sync`, one retention knob

Today WAL exists only on `/data`. A B2-only restore can reach exactly the
weekly base's moment and nothing after. Offsite WAL closes that.

- **Periodic push**: `scripts/sync_wal_offsite.sh --wal-dir --remote
  --stamp --timeout`, run **hourly** from cron.d as `manta`, doing `rclone
  copy --min-age 2m --exclude '*.tmp'` (never uploads a file the archiver
  may still be writing — with D2 that is only `.tmp`, and `--min-age` is
  the second guard). On success it touches the `--stamp` file; the health
  check reads its age (D5).
  - **The interval is one constant**, `WAL_OFFSITE_INTERVAL_MIN=60` at the
    top of `setup-backup.sh`. It renders the cron.d schedule, the health
    check's `--stale-after` (3 × interval), and the push's `--timeout`
    (interval minus one minute). Changing the cadence is one edit and a
    re-run of the script; nothing else knows the number.
  - **Overlap and hangs**: `flock -n` — if the previous run still holds the
    lock, this run exits 0 immediately and logs "skipped: previous run
    active". The rclone call runs under `timeout`, so a hung B2 endpoint
    can never queue waiting processes. `--bwlimit` is a named constant,
    default off; the operator sets it if uplink contention shows.
  - **Failure**: non-zero exit, stamp not touched, one line to the log and
    to `logger -t manta-backup`. No retry inside the script; the next hourly
    run is the retry. After 3 × interval without a touched stamp,
    `offsite_wal_stale` fires (D5).
  - **Loss window**: at most one interval plus one segment switch. Costs at
    the measured 16 GiB/day: ~40 minutes/day of the 40 Mbps uplink, about
    $0.70/month of B2 storage for 7 days of WAL (less compressed), list
    calls in the cents. At the incident's ~100 GB/day drain rate the uplink
    (~430 GB/day) still keeps up with ~5.5 hours/day busy — the reason the
    push is decoupled from the archiver rather than in `archive_command`,
    which would block on the network and refill `pg_wal`.
- **Weekly reconcile**, in `cron_weekly_base.sh`, ordered so a lagging
  push can never let the local prune delete a segment that was never
  uploaded: (1) catch-up `rclone copy` of the WAL directory, (2) `rclone
  check --one-way` — abort the run if it reports differences, (3) local
  prune, (4) `rclone sync` of the WAL directory and deletion of any
  `base/<date>` prefix offsite that no longer exists locally. Offsite
  therefore holds exactly what local holds, and nothing older, with no
  operator action. As a server-side backstop needing no host at all, the
  runbook records a B2 bucket lifecycle rule (delete versions older than
  30 days) on the `wal/` and `base/` prefixes; it is set once in the B2
  console and catches the case where the host itself is gone for weeks.
- **The reconcile's `rclone sync` is guarded, every run, not just the
  first.** A sync mirrors its source unconditionally, so an unmounted
  `/data`, an emptied archive after an operator recovery, or a wrong
  rendered path would delete the offsite chain. Before the sync step the
  job asserts, and aborts loudly on any failure: `mountpoint -q` on the
  backup root; the WAL directory is non-empty; the segment named by
  `pg_stat_archiver.last_archived_wal` exists locally (proves the directory
  is the live archive, not a stale copy); and the oldest retained
  manifest's start segment exists locally (proves the prune kept what it
  said). The sync itself runs with `--max-delete N`, where N is the count
  the prune just reported plus a fixed margin constant, so a discrepancy
  larger than the prune explains fails the run instead of deleting.
- **The destructive step is armed by hand, once.** The weekly job runs
  steps 1–4 unconditionally but performs the sync/delete step only while
  `<backup-root>/RECONCILE-ARMED` exists; otherwise it logs
  `reconcile skipped: not armed` and exits 0. The operator creates the file
  after watching the first reconcile (walkthrough step 7); a rebuilt host
  starts unarmed. `setup-backup.sh --check` reports the file's state.
- **Offsite retention equals local retention.** One `--keep-days` governs
  both. The alternative — a longer offsite window — costs little in B2 but
  needs a second retention computation with its own oldest-segment logic,
  and the chain it would keep is one nobody has drilled. Rejected for now;
  the weekly reconcile is the single place to revisit it.

This is also what purges the superseded offsite bases (`20260816`,
`20260817`): the first weekly reconcile deletes them because they are not
retained locally. No hand purge. `base/20260823` was measured absent
2026-09-05 — that cleanup item is already closed.

### D4a — Retention: `--keep-days 7` derived from capacity, not inherited from the emergency edit

`/data` is 1.8 TB and also holds timeshift's two snapshots (~35 GB each).
Retention must fit at the incident rate, not the steady state:

| Rate | 7 days: 2 bases + WAL | 21 days: 4 bases + WAL | Fits 1.8 TB? |
|---|---|---|---|
| 16 GiB/day (measured now) | ~200 + 112 = ~310 GB | ~400 + 336 = ~740 GB | both |
| ~100 GB/day (2026-09 drain) | ~200 + 700 = ~900 GB | ~400 + 2,100 = ~2.5 TB | **7 only** |

Seven days is the longest window that survives the drain rate with the
`wal_disk_low` 15 % floor intact; 21 days was never viable once Kalshi
landed. Compression (D3) widens the margin but the number is set without
it. The runbook keeps this table so the next rate change is a re-derivation,
not a guess.

### D5 — Health check: six new named failures, each demonstrated firing

`check_archive_health.sh` keeps its four checks. **Amended at task
breakdown (2026-09-05):** the six new checks live in a new wrapper,
`check_backup_health.sh`, which runs the 915 script for the database checks
and appends its own, and the cron.d glue is likewise new
(`backup_health_cron.sh`, `cron_weekly_backup.sh`). Reason: the live user
crontab invokes the 915 scripts from the host checkout, which is also the
development checkout tracking `main`; changing their required arguments
would break the half-hourly check silently the moment the branch merged.
Wrapping leaves them untouched until cron.d is observed firing, after which
they are deleted. The six checks:

| Name | Detects | How |
|---|---|---|
| `prune_permission` | the day-one ACL defect | create and delete `.prune-canary.tmp` in `--wal-dir` as the invoking user; failure of either is FAIL. Runs as the cron user, so it tests exactly the identity the prune runs as. The `.tmp` suffix means every other reader ignores it: the push excludes `*.tmp`, `pg_archivecleanup` ignores non-segment names, `rclone check` excludes it, and a canary orphaned by a crash is caught by `archive_tmp_leftover`. A `trap EXIT` removes it on any exit path |
| `archive_wedged` | the disk-full wedge (legacy raw shape) | derive the next segment name from `pg_stat_archiver.last_archived_wal` (or `last_failed_wal` when failing); FAIL if a file of that name exists in the archive with size ≠ 16 MiB and no `.zst` sibling |
| `archive_tmp_leftover` | an interrupted atomic write (D2 shape) | FAIL if any `*.tmp` in `--wal-dir` is older than 10 minutes |
| `offsite_wal_stale` | the hourly push has stopped | FAIL if the `--stamp` file is missing or older than `--stale-after` (3 × interval: three missed runs) |
| `system_backup_stale` | the restic tier has stopped (D9) | FAIL if the restic success stamp is missing or older than 2 days (one missed nightly run plus margin) |
| `weekly_base_stale` | the weekly job aborted or stopped (its new guards are abort paths) | FAIL if the newest dated `base/<date>` directory is older than 9 days |

Each threshold is a named constant at the top of the script, as the existing
two are. Following 915's success criterion 2: **an alarm is not delivered
until it has been observed firing** — each of the six is triggered
deliberately in the walkthrough (revoke the ACL, plant a short file at the
next segment name, plant a stale `.tmp`, age the push stamp, age the restic
stamp, point `--base-dir` at a scratch directory holding one old dated
directory) and restored.

The remedy for each named failure goes into the runbook's "If archiving
breaks" section in the same table shape: name → cause → fix. The wedge fix is
`mv` the partial aside (never delete — it may be the only copy of that
segment's first bytes if the source `pg_wal` has since recycled; the runbook
says how to confirm the source still has it).

### D6 — Two flags: archive integrity gates the base backup; offsite health does not

The half-hourly check, the `ARCHIVE-BROKEN` flag, and `cron_weekly_base.sh`'s
refusal while the flag exists all stay, and the three new local-integrity
failures (`prune_permission`, `archive_wedged`, `archive_tmp_leftover`) feed
that flag: each means the chain on disk is suspect, and a base backup taken
on a suspect chain is what the gate exists to prevent.

`offsite_wal_stale`, `system_backup_stale`, and `weekly_base_stale` are
**not** archive-integrity conditions — a B2 outage says nothing about the
local chain, and a stale base must not block the job that would fix it — so
they write a second flag, `/data/backup/BACKUP-STALE`, which does not gate
the weekly base backup. Blocking a healthy local tier because a remote tier is degraded
would trade a backup for an alarm; the alarm alone is the right response.
Both flags have the same delivery, which is honestly stated: a file the
runbook's quick reference tells the operator to `ls` (`/data/backup/*-STALE
/data/backup/*-BROKEN`), plus one `logger -t manta-backup` line per
transition so `journalctl -t manta-backup` shows the history. Nothing pushes
either flag to a person; this host has no mail transport (919) and choosing
a notification channel is a separate decision this slice does not make.
Both clear on the next healthy check.

### D7 — Cron schedule becomes a root-installed `/etc/cron.d` file

916 declared the user crontab PM-owned host config. This slice's plan entry
overrides that for the backup lines: they are now managed by
`setup-backup.sh`. The clean way to manage them is not to edit the user
crontab at all but to install `/etc/cron.d/manta-trading-backup` (root-owned,
0644, user field per line), rendered from `deploy/cron.d/manta-trading-backup`
with the `--checkout`, `--env-file`, and `--backup-root` values substituted.
Entries:

| When | User | Job |
|---|---|---|
| `*/30` | manta | `archive_health_cron.sh` (existing; gains `--wal-dir`, `--stamp`, `--stale-after`, `--system-stamp`, `--offsite-flag`) |
| hourly (rendered from `WAL_OFFSITE_INTERVAL_MIN`) | manta | `sync_wal_offsite.sh` (new, D4) |
| `0 2 * * *` | manta | `cron_nightly_metadata.sh` (existing) |
| `0 3 * * 0` | manta | `cron_weekly_base.sh --keep-days 7` (existing + offsite reconcile) |
| `0 4 * * *` | root | `cron_system_backup.sh` (new, D9) |
| `0 5 1 * *` | root | restic `check --read-data-subset=5%` (new, D9) |

The three user-crontab lines must be removed once — by the PM, as a cutover
step the runbook names — or the jobs run twice. Step 8 of the script warns
while they remain. Keep the `@reboot rclone mount` line untouched.

### D8 — Timeshift: managed keys merged with `jq`, two weekly snapshots, on `/data`

`timeshift.json` mixes operator intent (schedule, counts, excludes) with
host identity (`backup_device_uuid`) and runtime state (`snapshot_size`,
`snapshot_count`). A file copy would clobber the latter, so the script merges
only the managed keys with `jq` and writes back: `schedule_weekly=true`,
every other schedule `false`, `count_weekly=2` (PM: "1–2 local snapshots";
today 3), and the exclude list exactly as measured
(`/home/manta/**`, `/var/lib/postgresql/**`, `/var/lib/libvirt/**`,
`/root/**`). The device UUID is read, not set; `--check` reports it so a
replacement host's runbook step can point it at the right disk.

Two facts the runbook states: snapshots live on `/data`, the same device as
the archive, so their count is a disk-space term (~35 GB each measured);
and timeshift is the OS rollback layer only — everything it excludes is
restic's job (D9). The `/home/manta/**` exclude was an *include* until
2026-09-03 and made snapshots ~570 GB; the runbook keeps that history so
nobody "fixes" it back.

### D9 — restic to B2 for `/etc`, `/root`, crontabs, and `/home/manta`; excludes decided from measurement

- **Tool**: restic 0.18.1 from apt. Standard, single binary, deduplicating,
  encrypted at rest, `forget --prune` retention, `check --read-data-subset`
  for verifiable integrity.
- **Repository**: `s3:https://<MT_BACKUP_S3_ENDPOINT>/<MT_BACKUP_S3_BUCKET>/system`
  — the existing bucket-scoped key, same four env keys, a `system/` prefix
  beside `base/`, `wal/`, `metadata/`. No second credential to manage.
- **Repository password**: new `MT_BACKUP_RESTIC_PASSWORD` in the dev
  checkout's `.env`, grep'd never sourced, exactly like the S3 keys. Losing
  it loses every system backup, so the runbook's bootstrap step 1 is "fetch
  the four S3 values and the restic password from the PM's password
  manager" — that is the one input a replacement host cannot derive.
- **Runs as root** (reads `/etc`, `/root`, `/var/spool/cron/crontabs`, all
  of home), daily at 04:00 via cron.d, through
  `scripts/cron_system_backup.sh --env-file --repo-prefix --exclude-file
  --stamp --log`: `restic backup` then `restic forget --keep-daily 7
  --keep-weekly 4 --keep-monthly 3 --prune`. Monthly `restic check
  --read-data-subset=5%`.
- **Failure handling, same discipline as the WAL tier.** The job runs under
  `flock -n` (one restic process at a time), so it may safely run `restic
  unlock` first — a stale lock from an interrupted run is restic's most
  common recurring failure and would otherwise fail every later backup. A
  missing `MT_BACKUP_RESTIC_PASSWORD`, an unreachable repository, or a
  non-zero `backup`/`forget` exit ends the run non-zero, appends the reason
  to `--log`, emits one `logger -t manta-backup` line, and leaves the
  `--stamp` untouched; the health check's `system_backup_stale` (D5) then
  raises `BACKUP-STALE` (D6) within two days. Success touches the stamp.
  Nothing relies on cron mail: this host has none (919).
- **Encryption is the control that makes include-by-default safe.** Home
  contains `~/.ssh`, the dev checkout's `.env` (maintenance URL, B2 keys,
  provider keys), and browser stores; all of it goes to B2 daily. restic
  encrypts every blob client-side with a key derived from the repository
  password, and B2 never sees plaintext or the password — this is why the
  include set is not trimmed for secrets. Corollary: the password is inside
  the backup it protects, so a copy outside the host (the PM's password
  manager) is a bootstrap precondition, not a convenience.
- **Include set**: `/etc`, `/root`, `/var/spool/cron/crontabs`,
  `/home/manta`. **Excludes** (`deploy/restic-excludes.txt`), from the
  2026-09-05 measurement:

| Path | Size | Exclude? | Why |
|---|---|---|---|
| `/home/manta/.local/share/Trash` | 107 G | yes | it is the bin |
| `/home/manta/.local/share/Steam` | 46 G | yes | re-downloadable |
| `/home/manta/.cache`, `.npm`, `.local/share/uv`, `.vscode` | ~21 G | yes | caches |
| `/home/manta/pCloudDrive`, `GoogleDrive` | FUSE mounts | yes (`--one-file-system`) | remote already |
| `**/.venv`, `**/node_modules` | — | yes | rebuildable |
| `/home/manta/Pictures` (67 G), `/home/manta/ai` (60 G) | 127 G | **PM decides** at task time | first-run size is the deciding measurement |

Everything else in home is included by default: the rule is *exclude what
is derivable, keep what is not*, the same split as 915's metadata tier.
Estimated first snapshot ~150 GB before the PM decision; B2 at ~$6/TB-month
makes the choice a convenience question, not a cost one.

- **Restore proof**: the walkthrough restores `/etc/postgresql`,
  `/etc/timeshift`, the crontab spool, and one home subtree into a scratch
  directory and diffs them against live. A restic repo that has never been
  restored from is a hypothesis, as with 915 D6.

### D10 — Host-bootstrap runbook, with a real acceptance test

`project-documents/user/runbooks/210-host-bootstrap.md`: the ordered
procedure from a bare Ubuntu 26.04 with PostgreSQL 17 + TimescaleDB to a host
that is production, backed up, and drift-free. Order: packages → clone dev
checkout → `.env` from the password manager → `install-production.sh --ref`
→ restore metadata/base as needed → `setup-backup.sh` → restart PostgreSQL
→ remove the user-crontab lines → `setup-backup.sh --check` green → first
restic snapshot → `check_archive_health.sh` PASS. Every sudo, every ACL,
every restart is a step; there is no "then fix permissions".

**Acceptance test, two parts, both required:**

1. `setup-backup.sh --check` on `manta9000` exits 0 with every item `OK`
   after the script has been run once. This proves the script captures the
   hand-applied state completely — any item it does not manage cannot be
   `OK`.
2. A full run from clean state on **hammerhead** (192.168.1.143: same
   PostgreSQL 17 + TimescaleDB, the dedicated test cluster from 917, no
   production data, `/` 1.7 TB free) with a throwaway `--backup-root`,
   followed by a segment switch landing a `.zst` in its archive, a passing
   health check, and a `--check` green — then teardown (archive dir removed,
   settings reset, cron.d file removed). This needs one restart of the test
   cluster; PM go required because it interrupts any integration run in
   flight. A fresh VM is the fallback if the PM prefers hammerhead untouched.

### D11 — Every step keeps a restorable chain

The plan's risk statement is the ordering constraint. Implementation order
(also "Development Approach"):

1. Health-check additions first (read-only; alarms exist before anything
   they guard changes).
2. Measurement task for D3's ratio.
3. Atomic + compressed `archive_command` via `setup-backup.sh` step 4 with
   `restore_command`, prune `-x`, and runbook in the same commit; PITR drill
   across the mixed archive (walkthrough step 5) before proceeding.
4. Offsite WAL push and weekly reconcile; verify by checksum; B2-sourced
   PITR drill (walkthrough step 6).
5. cron.d cutover (PM removes user lines).
6. Timeshift merge, restic init and first snapshot, restic restore drill.
7. Bootstrap runbook and the hammerhead acceptance run.

Nothing in steps 1–4 deletes a segment or a base outside the retention the
prune already enforces; the first offsite reconcile is the first destructive
offsite action and runs only after the local chain has been drilled.

---

## Data Flow (delta from 915)

```
postgresql@17-main
   │ archive_command (atomic: zstd → %f.zst.tmp → mv %f.zst)
   ▼
/data/backup/wal/  ── hourly: rclone copy --min-age 2m (flock -n, timeout) ──▶  b2:$BUCKET/wal/
   │                                                                  ▲
   │ weekly: copy → check → prune (-x .zst) → guarded sync --max-delete ─┘
   │                            (deletes offsite what local pruned; also base/<date>)
   ▼
every 30 min: check_archive_health.sh
   archive_mode_off | archiver_failing | unarchived_backlog | wal_disk_low
   + prune_permission | archive_wedged | archive_tmp_leftover
   → /data/backup/ARCHIVE-BROKEN (weekly base refuses while present)
   offsite_wal_stale | system_backup_stale | weekly_base_stale
   → /data/backup/BACKUP-STALE (alarm only; never gates the base backup)

/etc /root crontabs /home/manta ── daily restic (root) ──▶ b2:$BUCKET/system/
/ (minus home, PGDATA, libvirt, root) ── weekly timeshift ──▶ /data (2 kept)
```

Recovery paths gained:

| Scenario | Before | After |
|---|---|---|
| Local disk gone, need a point in time | base only, weekly granularity | base + offsite WAL replay to any retained moment |
| Host rebuilt | archaeology | `210-host-bootstrap.md` + restic restore of `/etc`, crontabs, home |
| Bad OS update | timeshift snapshot (which used to contain a torn PGDATA) | timeshift snapshot, PGDATA excluded, DB from its own tier |

---

## Success Criteria

1. `deploy/setup-backup.sh` runs to completion on `manta9000` and a second
   run changes nothing (every step reports "already"); `--check` exits 0
   with every item `OK`.
2. `pg_settings` shows `archive_command` in the D2 form, `wal_compression =
   zstd`, `archive_mode = on`, all sourced from `postgresql.auto.conf`; the
   hand-set `postgresql.conf` line is gone.
3. After a `pg_switch_wal()`, a `.zst` segment appears in the archive, no
   `.tmp` remains, and `pg_stat_archiver.last_archived_wal` advances.
   (If D3's measurement said no-go: a raw segment via the atomic form, and
   the design records the ratio and the decision.)
4. `getfacl /data/backup/wal` shows `user:manta:rwx` and the default entry,
   and the `prune_permission` canary passes.
5. A PITR restore across the mixed raw/`.zst` archive reaches a target after
   the cutover (sentinel proof as in 915), using the runbook's
   `restore_command` verbatim.
6. `rclone check --one-way` of the WAL directory against `b2:$BUCKET/wal`
   reports zero differences; the offsite stamp is younger than one interval
   during normal operation, and the interval appears exactly once in the
   repo (`WAL_OFFSITE_INTERVAL_MIN`).
7. A PITR restore whose `restore_command` reads segments pulled **from B2**
   (not the local archive) succeeds — the offsite chain is proven, not
   assumed.
8. Each of the six new named failures has been observed firing on a
   deliberate fault and clearing on repair, with the fault, the FAIL line,
   the flag it raised (`ARCHIVE-BROKEN` or `BACKUP-STALE`), and the fix
   recorded in the runbook. With only `BACKUP-STALE` present,
   `cron_weekly_base.sh` still runs; the weekly reconcile's guards have
   been observed refusing (empty scratch `--wal-dir`) before any `rclone
   sync`, deleting nothing offsite.
9. After the first weekly reconcile, `rclone lsd b2:$BUCKET/base/` lists
   exactly the locally retained dates (`20260816`, `20260817` gone).
10. `/etc/cron.d/manta-trading-backup` exists with the six entries and the
    user crontab contains none of the 915 script names.
11. `/etc/timeshift/timeshift.json` has `count_weekly = 2` and the four
    excludes; the device UUID is unchanged.
12. restic: `restic snapshots` lists a completed daily snapshot;
    `restic check` passes; a restore of `/etc/postgresql`, `/etc/timeshift`,
    `/var/spool/cron/crontabs`, and one home subtree into a scratch dir diffs
    clean against live.
13. `210-host-bootstrap.md` exists, and the hammerhead (or VM) acceptance
    run from D10 is recorded in it with measured durations.

---

## Verification Walkthrough

Draft; refined after Phase 6. All steps on `manta9000` unless stated. `MAINT`
and `BUCKET` as in `200-backup-and-restore.md`.

### 1. Provision and prove idempotence

```bash
sudo -v
sudo deploy/setup-backup.sh --checkout ~/source/repos/manta/trading-data \
  --env-file ~/source/repos/manta/trading-data/.env \
  --backup-root /data/backup --cluster 17/main
sudo deploy/setup-backup.sh <same args>            # expect every step "already"
sudo deploy/setup-backup.sh <same args> --check     # expect all OK, exit 0
```

Expect `PENDING RESTART` only if `archive_mode` changed (it is already on;
none expected). `archive_command` is reload-only.

### 2. The archive command is atomic and compressed

```bash
psql "$MAINT" -c "SELECT pg_switch_wal();"; sleep 5
ls -la /data/backup/wal | tail -3          # newest is *.zst, no *.tmp
psql "$MAINT" -c "SELECT last_archived_wal, last_archived_time FROM pg_stat_archiver;"
```

### 3. Each new alarm fires and clears

Run `./scripts/check_archive_health.sh --db-url "$MAINT" --pgdata … --wal-dir /data/backup/wal --stamp /data/backup/wal-offsite.stamp` after each fault; expect the named `FAIL` line, then `PASS` after repair.

| Fault | Command | Expect |
|---|---|---|
| ACL revoked | `sudo setfacl -x u:manta /data/backup/wal` | `FAIL prune_permission` |
| wedge planted | `sudo -u postgres head -c 1000 /dev/zero > /data/backup/wal/<next-segment>` | `FAIL archive_wedged` |
| stale tmp | `sudo -u postgres touch -d '-20 min' /data/backup/wal/X.zst.tmp` | `FAIL archive_tmp_leftover` |
| push stopped | `touch -d '-4 hours' /data/backup/wal-offsite.stamp` | `FAIL offsite_wal_stale` → `BACKUP-STALE` only |
| restic stopped | `sudo touch -d '-3 days' /data/backup/system-backup.stamp` | `FAIL system_backup_stale` → `BACKUP-STALE` only |
| weekly job stopped | `--base-dir` at a scratch dir containing only `20260801/` | `FAIL weekly_base_stale` → `BACKUP-STALE` only |

Repair each (re-run the setup script for the ACL; `mv` the planted files
aside) and confirm `PASS`, and that both flags clear. While only
`BACKUP-STALE` is present, run `cron_weekly_base.sh` by hand and confirm it
does not refuse. Then point the weekly job at an empty scratch `--wal-dir`
and confirm it aborts before the sync with the guard's message.

### 4. Offsite WAL is present and checksum-verified

```bash
./scripts/sync_wal_offsite.sh --wal-dir /data/backup/wal --remote b2:$BUCKET/wal --stamp /data/backup/wal-offsite.stamp
rclone check /data/backup/wal b2:$BUCKET/wal --one-way --exclude '*.tmp'   # 0 differences
```

### 5. PITR across the mixed archive (local)

Runbook 200 Step 6 + PITR section with the new `restore_command`. Sentinel
committed after the cutover; target before and after; absent/present.

### 6. PITR from B2 only

Pull the needed range into a scratch directory and point `restore_command`
at it:

```bash
rclone copy b2:$BUCKET/wal /data/restore-test/wal-b2 --min-size 1 --include '<range glob>'
# restore_command = '… /data/restore-test/wal-b2/%f.zst …'
```

Recovery reaches the target using only B2-sourced segments.

### 7. Offsite reconcile purges the superseded bases

Run the weekly job by hand (or wait for Sunday), then:

```bash
rclone lsd b2:$BUCKET/base/     # only locally retained dates
tail -5 /data/backup/base.log   # "offsite reconcile: removed base/20260816 base/20260817", check 0 differences
```

### 8. Cron, timeshift, restic

```bash
cat /etc/cron.d/manta-trading-backup; crontab -l | grep -c cron_weekly_base   # 0
jq '.count_weekly, .exclude' /etc/timeshift/timeshift.json
sudo scripts/cron_system_backup.sh --env-file … --repo-prefix system --exclude-file deploy/restic-excludes.txt
sudo restic -r <repo> snapshots; sudo restic -r <repo> check
sudo restic -r <repo> restore latest --target /data/restore-test/restic --include /etc/postgresql --include /var/spool/cron/crontabs
diff -r /etc/postgresql /data/restore-test/restic/etc/postgresql   # clean
```

### 9. Bootstrap acceptance

On hammerhead, from a clean state, follow `210-host-bootstrap.md` verbatim
for the backup sections; end with `setup-backup.sh --check` green and
`check_archive_health.sh` PASS; tear down. Record durations in the runbook.

---

## Risks

- **A `restore_command` that does not match the archive shape** is the one
  way this slice can make a restore fail. Mitigated by D3's single-commit
  rule and walkthrough steps 5 and 6, which use the runbook's command
  verbatim.
- **Offsite reconcile deleting the wrong thing.** It deletes only what local
  retention already deleted, runs after the local chain is drilled (D11),
  and logs every removed prefix. The first run is watched, not scheduled.
- **Compression CPU on the archiver.** `zstd -T2` at 16 GiB/day is ~1% of
  a core-day; measured during the ratio task, not assumed.
- **Restic password loss.** Documented as the bootstrap's first step; the
  runbook says where it lives, never what it is.

## Implementation Notes

- New files: `deploy/setup-backup.sh`, `deploy/cron.d/manta-trading-backup`,
  `deploy/restic-excludes.txt`, `deploy/lib/timeshift_merge.sh`,
  `scripts/wal_segment_name.py` (segment-name arithmetic, shared with the
  prune), `scripts/check_backup_health.sh`, `scripts/backup_health_cron.sh`,
  `scripts/reconcile_guards.sh`, `scripts/cron_weekly_backup.sh`,
  `scripts/sync_wal_offsite.sh`, `scripts/cron_system_backup.sh`,
  `runbooks/210-host-bootstrap.md`. Deleted after cutover:
  `scripts/archive_health_cron.sh`, `scripts/cron_weekly_base.sh`.
- Changed (argument-compatible only): `scripts/prune_wal_archive.sh`
  (`-x .zst`, prints the deletion count the reconcile consumes, segment
  arithmetic moved to `wal_segment_name.py`),
  `runbooks/200-backup-and-restore.md` (archive shape, restore_command,
  two-flag alarm table, cron.d replacing Step 7's user-crontab text,
  mixed-archive note), `runbooks/__readme.md` (210 row), `README`/env
  example (restic key).
- Amendments recorded where the amended decisions live: a dated
  `000-process-journal.md` entry (dev checkout's third role: the backup
  tier; backup cron lines become script-managed via cron.d), and a one-line
  "amended by 920" pointer in the 916 design's cron decision.
- Every PM host step is a script invocation with a printed report, per the
  standing rule; the only checklist items are the ones the script cannot
  do: the PostgreSQL restart (if any), removing the user-crontab lines,
  placing `MT_BACKUP_RESTIC_PASSWORD` in `.env` (D9), and the B2 console
  lifecycle rule (D4).
- Shell scripts get the same discipline as the 915 set: `set -euo pipefail`,
  explicit arguments, named constants at the top, no ambient credentials.
  Unit coverage for the health check's segment-name arithmetic via a bats-
  style fixture directory is worth one task; the rest is verified live.

## Effort

3/5. Six coupled scripts and two runbooks, three drills (mixed-archive PITR,
B2-sourced PITR, restic restore), and a bootstrap run on a second host. The
work is in the drills and the transition ordering, not in the scripts.
