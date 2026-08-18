---
docType: slice-design
slice: backup-and-restore-procedures
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [913]
interfaces: []
dateCreated: 20260811
dateUpdated: 20260818
status: complete
review: none
---

# Slice Design: Backup and Restore Procedures

## Overview

The project has **no backup procedure and no tested restore path**. What
exists is an operator manually copying the PostgreSQL data directory to an
external drive. On 2026-08-10 that copy was taken against a *running* server,
which produces a torn image: `cp` walks a 150 GB directory over many minutes
while PostgreSQL writes 8 KB pages and WAL continuously, so the result
corresponds to no single instant and carries no crash-recovery guarantee. Such
a copy usually restores anyway — which is the hazard, because the one that
fails is the one where an index page was mid-split, and that is discovered
during an emergency.

Measured on prod (`192.168.1.144`, 2026-08-11):

| Setting | Value | Consequence |
|---|---|---|
| `wal_level` | `replica` | sufficient for archiving; no change needed |
| `archive_mode` | **`off`** | **no point-in-time recovery exists** |
| `archive_command` | disabled | — |
| Tablespaces | `pg_default`, `pg_global` only | one data directory; no external dirs |
| `trading` size | 150 GB | `trading_test` a further 7 GB |
| Leftover test DBs | 9 (`mt_test_*`, `mt_diag_*`, ~100 MB) | fixture-teardown orphans |

With archiving off, recovery granularity is "whenever someone last remembered
to copy." Everything written between the last copy and a failure is
unrecoverable by construction — not by accident of procedure, but because the
information required to replay it is never retained.

This is the outstanding action item from the 2026-08-04 truncation incident
([scoping notes](../notes/2026-08-04-prod-db-guardrails-scoping.md)). It is the
sibling of slice 913: that slice made a credential leak **non-destructive**;
this one makes destruction **survivable**. 913 is the last line of defence
against a specific mistake, 915 against every other one — hardware failure,
filesystem corruption, a mistaken `DROP` under the maintenance role, or a
migration that mangles data in a way no privilege model can prevent.

### What backup currency is driven by

The scoping notes' priority table is this slice's requirements input, and it
overturns the intuitive "OHLCV is re-derivable, so it's low priority" reading:

| Table | Re-derivable? | Priority |
|---|---|---|
| `minute_ohlcv` (4.4 B rows) | in principle from EODHD; **in practice no** — quota plus weeks of pulling | **1** |
| `daily_ohlcv` (34.7 M rows) | same, smaller | 2 |
| `acquisition_state` | **no external source**; losing it forces a mass re-pull | 3 |
| `instruments`, `splits`, `dividends`, calendars | yes (EODHD / GitHub CSV / code) | low |
| `schema_migrations` | from repo | low |

Two distinct shapes fall out of this, and conflating them is the main design
error to avoid. The priority-1/2 tables are **enormous and change slowly** —
they want an infrequent full physical backup. The priority-3 metadata is
**small and is what actually got destroyed on 2026-08-04** — it wants a
frequent, cheap logical dump. A single cadence serving both is either too
expensive to run often or too stale to be useful.

---

## Technical Decisions

### D1 — Physical backup mechanism: `pg_basebackup`, not file copy

Adopt `pg_basebackup -Ft -z -Xs` as the base-backup mechanism, replacing the
manual directory copy.

The mechanism matters more than the cadence. `pg_basebackup` issues a
checkpoint, tracks the backup start LSN, and (with `-Xs`) streams the WAL
generated *during* the copy on a second connection. The resulting archive
therefore contains everything needed to bring the cluster to a consistent
state on restore, even though the underlying files were read over a long
interval from a live server. That is precisely the guarantee `cp` cannot make.

Consequences that make this strictly better than the status quo:

- **No maintenance window.** It runs against a live server with the daemon
  writing. The 2026-08-10 procedure required stopping PostgreSQL.
- **`-Ft -z`** produces a handful of large tar archives instead of hundreds of
  thousands of loose files, which is what makes offsite sync tractable (D3).
- **Self-verifying on restore** — a torn copy has no such property.

Compression expectation should be set honestly in the runbook: the bulk of the
data is already in TimescaleDB's columnstore, so gzip's marginal benefit is
much smaller than the raw 150 GB figure suggests. The design does not commit
to a compressed size; the task run measures it.

Explicitly **not** chosen: `pg_dump` for the full cluster. A logical dump of
4.4 B rows is impractical to take and far worse to restore. `pg_dump` has a
role in this slice, but only for the small metadata tier (D4).

### D2 — Enable WAL archiving, with archive-failure monitoring as a first-class concern

Set `archive_mode = on` with an `archive_command` and a retention policy, so
point-in-time recovery exists at all. `wal_level` is already `replica`, which
is sufficient; **it must not be lowered to `minimal`**, which would silently
break both archiving and `pg_basebackup`.

Two operational facts drive the design here, and both belong in the task
breakdown rather than being discovered on the host:

1. **`archive_mode` requires a server restart.** It is not `SIGHUP`-reloadable.
   This is the one genuinely disruptive step in the slice and should be
   sequenced against a window when the daemon is already stopped — the same
   discipline slice 170's Phase C uses.
2. **A silently failing `archive_command` fills `pg_wal` and can take the
   server down.** PostgreSQL retains every WAL segment the archiver has not
   confirmed. If the command fails — destination unmounted, permissions wrong,
   disk full — segments accumulate indefinitely until the filesystem fills and
   the server halts. This turns a backup feature into an availability risk, so
   **archive-failure monitoring is in scope, not a follow-up.** The minimum is
   a check on `pg_stat_archiver` (`last_failed_wal`, `last_failed_time`,
   and the gap between `archived_count` and current WAL position) surfaced
   somewhere an operator actually looks.

The `archive_command` must be idempotent and must **refuse to overwrite an
existing segment** (the conventional `test ! -f target && cp ...` shape),
because a command that silently overwrites can corrupt the archive.

Retention is a real decision the task work must settle: WAL accumulates
continuously, so an archive with no expiry grows without bound. Retention must
be at least as long as the interval between base backups plus a margin — WAL
older than the oldest base backup you intend to restore from is useless, and
WAL younger than your newest base backup is mandatory.

### D3 — Offsite target: decided by tradeoff table

The overview names Backblaze B2 as the front-runner; this design records the
comparison and leaves the decision to the PM rather than presuming it.

Costs are monthly, at ~150 GB, ignoring API-call charges (negligible at this
object count with `-Ft`):

| Target | Storage /mo | Restore latency | Retrieval + egress for a full 150 GB restore | Minimum storage duration |
|---|---|---|---|---|
| **Backblaze B2** | ~$0.90 | immediate | **$0** (free egress up to 3× stored) | **none** |
| S3 Glacier Deep Archive | ~$0.15 | **12–48 h** | ~$13–18 (retrieval + $0.09/GB egress) | **180 days** |
| S3 Glacier Instant Retrieval | ~$0.60 | immediate | ~$16 (retrieval + egress) | 90 days |
| GCS Archive | ~$0.18 | immediate | ~$18 (retrieval + egress) | **365 days** |

Three factors decide this, and raw storage price is the least important:

- **Minimum storage duration is the term that quietly dominates.** Deleting or
  replacing an object before the minimum bills the remainder anyway. This slice
  exists in a project that re-pushes backups after large migrations (170 alone
  rewrote every daily chunk), so a 180- or 365-day floor means paying for
  several overlapping copies rather than one. GCS Archive's 365-day floor is
  the harshest of the four despite its attractive headline price.
- **Retrieval cost and latency are paid exactly when things are worst.** Deep
  Archive's 12–48 hour restore *is* the outage length during a real incident,
  and it charges for the privilege.
- **Egress-free restore removes a disincentive to drill.** A restore drill
  (D6) that costs $15 in egress each time will not be repeated. One that costs
  nothing will be.

**Recommendation: B2** for the working offsite copy — immediate restore, no
retrieval fee, no minimum duration, S3-compatible so `rclone`/`aws s3` tooling
is unchanged. Deep Archive remains reasonable as an additional yearly cold
vault, where the 180-day floor is irrelevant; that is optional and should not
block the slice.

Credential handling follows the project rule: the object-store credential is
supplied from the environment or an operator-provided config path, **never**
committed, and `.env` remains gitignored. Per `sql.md`, the tooling takes its
targets from explicit arguments rather than reading ambient environment inside
the tool.

### D4 — Two-tier cadence, split by what cannot be re-derived

Two independent backup tiers, because the priority table describes two
different problems:

| Tier | Contents | Mechanism | Cadence | Size |
|---|---|---|---|---|
| **Metadata** | `instruments`, `provider_symbol_mapping`, `acquisition_state`, `backfill_state`, `data_gaps`, `trading_calendars`, `trading_holidays`, `trading_sessions`, `splits`, `dividends`, `schema_migrations` | `pg_dump -Fc` table-scoped | frequent (nightly) | megabytes |
| **Full cluster** | everything, including the 4.4 B-row hypertable | `pg_basebackup -Ft -z -Xs` | infrequent | ~150 GB |

The metadata tier is the direct answer to the 2026-08-04 incident: those are
exactly the tables that were truncated, they are small enough that a nightly
dump is free, and they are the ones with **no external source**. Restoring
them does not require touching the 150 GB tier at all.

The exact table list must be **derived from the schema, not hand-maintained** —
a hardcoded list silently omits any table a future migration adds, which is the
same by-name drift class that produced the slice-167 defect and the
`restore_metadata.py` `minute_1hour_ohlcv` bug. The design's constraint is:
enumerate from the catalog by exclusion (everything that is not a hypertable or
continuous aggregate), so a new metadata table is included automatically.

Cadence figures above are the design's starting proposal; the task work
confirms them against measured runtime and the chosen retention.

**Scheduling has no process manager to hang off.** Per the production-deploy
runbook, `.144` runs from a git checkout with **no systemd units installed**
and no process manager — the daemon is started manually. So this slice cannot
assume a `systemd.timer`. The task work chooses between installing a timer unit
(clean, but expands scope into the deployment work that Future Work explicitly
defers) and a `cron` entry (unglamorous, but matches how the host is actually
operated today). **Recommendation: cron**, with a note that it migrates to a
timer if and when the `/opt` + systemd deployment lands.

### D5 — One command, not remembered steps

Wrap base-backup → verify → offsite sync in a single invocation, so the
procedure cannot be half-performed.

Placement: a script under `scripts/` rather than an `mt` subcommand. Three
reasons — it is operator tooling rather than product surface; `mt data` is
already 3,371 lines and slice 906 is queued to decompose it, so adding a
sub-app now works against that; and the work is orchestration of external
binaries (`pg_basebackup`, `rclone`) rather than anything needing the
application's data layer. If it later earns a place in the CLI, promoting a
working script is easy; the reverse is not.

Non-negotiable properties, all inherited from existing project rules:

- **Explicit target, never ambient.** DB URL and destination are caller
  arguments (`sql.md`: "destructive and maintenance tooling takes its DB URL
  from an explicit caller argument"). A backup aimed by an unset variable is
  the failure mode the tool exists to prevent.
- **Uses the 913 maintenance credential**, not a superuser URL and not the
  application role. `pg_basebackup` needs `REPLICATION` or superuser; the task
  work confirms which grant the maintenance role needs and adds it to
  `provision_roles.sql` rather than reaching for `postgres`.
- **Fails loudly, never silently partially.** A failed sync must exit non-zero
  and must not leave a truncated object presented as a complete backup.
- **Verifies rather than assumes.** See D6.

### D6 — Verification is by content, and the restore drill is the deliverable

A backup that has never been restored is a hypothesis. This slice's central
deliverable is a **restore drill executed and documented**, not the tooling.

Two levels of verification:

1. **Per-backup, automatic.** `pg_verifybackup` against the produced archive
   (it validates the backup manifest PostgreSQL 13+ writes), plus a checksum
   comparison after offsite sync. The sync is verified by checksum, **never
   inferred from a clean exit code** — `rclone` provides this natively.
2. **Periodic, manual — the drill.** Restore into a throwaway target, start it,
   and verify **content**: row counts for the priority-1/2 tables match the
   source, and the continuous aggregates are checked by content parity against
   their source hypertables rather than catalog presence. This last point is a
   direct lesson from the 2026-08-04 restore, recorded in `sql.md`: an object
   created or interrupted mid-incident is *presumed damaged*, and catalog
   presence proves nothing about whether it holds the right rows.

The drill's result is written into a runbook alongside
`cagg-maintenance-pausing.md`, with the actual commands and observed timings,
so the next person executing it under pressure is following a tested script.

### D7 — Explicitly out of scope

- **Replication / hot standby.** A different problem — availability, not
  recoverability. A standby replicates a `DROP TABLE` instantly and is not a
  backup.
- **Changes to `restore_metadata.py`'s ledger-replay logic.** Its known
  boundary (objects dropped while their creating migration is still ledgered
  are invisible to replay) is documented in the scoping notes. This slice may
  *use* that tool for the metadata tier; redesigning it is separate.
- **Cleaning up the 9 leftover test databases.** Noted here because they will
  appear in the backup; they are ~100 MB and harmless. Worth a separate
  chore, and worth fixing the fixture teardown that orphans them.
- **Encryption at rest in the object store.** Worth doing, but the credential
  and key-management design is its own decision; note it as follow-up rather
  than bolting it on.

---

## Data Flow

```
                    ┌────────────────────────────────────────┐
                    │  .144  postgresql@17-main  (trading)   │
                    └───┬───────────────┬────────────────┬───┘
                        │               │                │
        archive_command │   pg_basebackup│      pg_dump -Fc│ (metadata tables,
        (continuous)    │   -Ft -z -Xs   │      catalog-derived list)
                        ▼               ▼                ▼
                 ┌────────────┐  ┌────────────┐   ┌────────────┐
                 │ WAL archive│  │ base backup│   │ metadata   │
                 │ (segments) │  │ (tar.gz)   │   │ dump (MB)  │
                 └──────┬─────┘  └─────┬──────┘   └─────┬──────┘
                        │              │                │
                        │        pg_verifybackup        │
                        │              │                │
                        └──────────────┼────────────────┘
                                       ▼
                            ┌──────────────────────┐
                            │ rclone sync +        │
                            │ checksum verify      │
                            └──────────┬───────────┘
                                       ▼
                            ┌──────────────────────┐
                            │  offsite bucket (D3) │
                            └──────────┬───────────┘
                                       │
                            ┌──────────▼───────────┐
                            │ RESTORE DRILL (D6)   │
                            │ throwaway target →   │
                            │ content parity check │
                            └──────────────────────┘
```

Recovery paths this produces, by scenario:

| Scenario | Path | Data lost |
|---|---|---|
| Metadata truncation (the 2026-08-04 case) | metadata dump, restore those tables only | ≤ one day |
| Whole-cluster loss | base backup + WAL replay | ≤ archive lag (minutes) |
| Corruption discovered late | base backup + WAL replay to a chosen LSN/time | none, if within retention |
| Base backup older than WAL retention | base backup only | everything since that backup |

The fourth row is the one to design against: retention (D2) and base-backup
cadence (D4) are coupled, and getting them wrong reintroduces exactly the gap
this slice exists to close.

---

## Success Criteria

1. `archive_mode = on` on `.144` with a non-empty `archive_command`, confirmed
   from `pg_settings`, and WAL segments observably landing in the archive
   destination (`pg_stat_archiver.archived_count` increasing,
   `last_failed_wal` null).
2. Archive-failure monitoring exists and is demonstrated to fire — verified by
   deliberately breaking the archive destination in a controlled test and
   observing the alarm, then restoring it. An unfired alarm is untested.
3. A full base backup completes against the **live** server with the daemon
   running, and `pg_verifybackup` passes on the result.
4. The metadata dump tier runs, its table list is derived from the catalog
   (adding a table to the schema causes it to be included without editing the
   script), and it completes in seconds.
5. The offsite copy is present in the chosen bucket and verified **by
   checksum**, not by exit code.
6. **The restore drill has been executed**: a throwaway target brought up from
   the backup, with row counts for `minute_ohlcv`, `daily_ohlcv`, and
   `acquisition_state` matching the source, and cagg content parity confirmed
   against source hypertables.
7. A point-in-time recovery is demonstrated at least once — restore to a
   timestamp *before* a deliberate test change and confirm the change is
   absent. This is what proves archiving works end to end, as distinct from
   proving segments are being copied.
8. The wrapper script refuses to run without an explicit target and does not
   read the DB URL from ambient environment.
9. A runbook exists alongside `cagg-maintenance-pausing.md` with the real
   commands and observed timings from the drill.

---

## Verification Walkthrough

*Executed during Phase 6; measured values below. The canonical as-executed
reference is [the backup-and-restore runbook](../runbooks/backup-and-restore.md),
which carries the full command set, the drill record and the next-due date.*

Measured on production (`manta9000`, PostgreSQL 17.11, `trading` at 141 GB):

| What | Measured |
|---|---|
| Base backup, live server | 2 h 03 m idle (20260816); 2 h 09 m with the daemon acquiring (20260817) — 79 GB compressed |
| Verification | extract + `pg_verifybackup`, 15 m, "backup successfully verified" |
| Offsite upload / download | 4 h 44 m–5 h 06 m up (~40 Mbps); 2 h 05 m down (~90 Mbps, needs rclone ≥ 1.75) |
| Offsite integrity | `rclone check --one-way`: 0 differences, including the 84.5 GB multipart object |
| Metadata dump | 12 tables, 4.6 MB, 1.3 s |
| Restore drill (2026-08-17) | extract 13 m 47 s, replay 42 s; exact `count(*)` parity on all 12 tables (`minute_ohlcv` 4,464,471,566) and all 9 continuous aggregates |
| Point-in-time recovery (2026-08-18) | proven both directions against a sentinel table — absent before the target, present after; ~14 m + ~1 m per direction |
| Archive-failure alarm | fired on a deliberate permission break (failed count 0→6) and the archiver drained unaided in under 20 s once restored |
| WAL generation | 0.18 GiB/day idle, ~3.1 GiB/day active blend, ~8 GiB/day peak → 21-day retention ≤ 170 GB against 756 GB free |

### 1. Confirm archiving is live

```bash
psql "$MT_TIMESCALE_MAINTENANCE_URL" -c \
  "SELECT name, setting FROM pg_settings
    WHERE name IN ('archive_mode','archive_command','wal_level');"
```
Expect `archive_mode=on`, a non-empty command, `wal_level=replica`.

```bash
psql "$MT_TIMESCALE_MAINTENANCE_URL" -c \
  "SELECT archived_count, last_archived_wal, last_archived_time,
          failed_count, last_failed_wal, last_failed_time
     FROM pg_stat_archiver;"
```
Expect `archived_count` > 0 and `last_failed_wal` NULL. Force a segment
switch with `SELECT pg_switch_wal();` and confirm `archived_count` increments
and the segment appears at the destination.

### 2. Take and verify a base backup, live

```bash
scripts/backup_prod.sh --db-url "$MT_TIMESCALE_MAINTENANCE_URL" \
                       --dest /path/to/staging
```
Runs with the daemon running. Then:

```bash
pg_verifybackup /path/to/staging
```
Expect `backup successfully verified`. Record wall-clock duration and the
compressed size — the design deliberately does not predict these.

### 3. Metadata tier, and prove the list is catalog-derived

Run the metadata dump; confirm it completes in seconds and the dump contains
the expected tables. Then create a scratch table, re-run, and confirm it is
picked up **without editing the script** — this is what distinguishes a derived
list from a hardcoded one. Drop the scratch table afterward.

### 4. Offsite sync, verified by checksum

```bash
rclone check /path/to/staging <remote>:<bucket>/<date>/ --one-way
```
Expect zero differences. A passing exit from the sync itself is not sufficient
evidence and does not satisfy this step.

### 5. Restore drill — the real test

Restore into a throwaway target (a separate host, or a second cluster on a
distinct port — **never** over `trading`). Start it, then compare against
source:

```sql
SELECT count(*) FROM minute_ohlcv;      -- expect the 4,414,650,928-class figure
SELECT count(*) FROM daily_ohlcv;
SELECT count(*) FROM acquisition_state;
```

Then cagg content parity — presence in the catalog is **not** acceptable
evidence:

```sql
-- per rollup cagg, compare against its source hypertable over a closed window
SELECT sum(volume) FROM daily_weekly_ohlcv WHERE ...;
```

Record every figure in the runbook.

### 6. Point-in-time recovery

Note a timestamp. Make a deliberate, reversible change on prod (insert a
sentinel row into a scratch table). Restore a copy to just before that
timestamp and confirm the sentinel is **absent**, then to just after and
confirm it is **present**. This is the only step that proves WAL replay works
rather than merely that segments are being copied.

### 7. Failure modes fire correctly

- Break the archive destination (unmount / revoke write); confirm the monitor
  alarms and that `pg_wal` growth is visible before it becomes dangerous.
  Restore the destination and confirm the archiver drains its backlog.
- Invoke the wrapper with no explicit target; confirm it refuses rather than
  defaulting.

---

## Risks

- **`archive_command` failure filling `pg_wal`.** The one way this slice can
  *cause* an outage rather than prevent one. Mitigated by D2's monitoring
  requirement and Success Criterion 2, which demands the alarm be demonstrated
  rather than assumed. Disk headroom on `.144` relative to WAL growth rate
  should be measured during task work.
- **`archive_mode` needing a restart.** Sequence it into a window when the
  daemon is already stopped; not risky, but not reloadable either.
- **A drill that is never repeated.** The realistic failure of backup work is
  that it is verified once and rots. The egress-free target recommendation (D3)
  and a runbook with real timings (D6) both exist to lower the cost of
  repeating it.

## Effort

3/5. The mechanisms are standard PostgreSQL tooling rather than novel work;
the effort is in the restore drill, the archive-failure monitoring, and
settling retention/cadence against measured sizes — not in writing the
wrapper.
