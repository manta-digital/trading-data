---
docType: tasks
slice: backup-and-restore-procedures
project: trading-data
lldReference: project-documents/user/slices/915-slice.backup-and-restore-procedures.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [913]
interfaces: []
projectState: >
  Slice 915 design committed 20260811. Dependency 913 (least-privilege roles) is
  complete and cut over: prod connects as `trading_app`, with
  `MT_TIMESCALE_MAINTENANCE_URL` holding the `trading_migrate` credential. Slice
  914 is complete. Production is `192.168.1.144`, native Debian
  `postgresql@17-main`, running from a git checkout with **no systemd units and
  no process manager** — the daemon is started manually (production-deploy
  runbook). Re-measured against prod 2026-08-16 (see "Measured state" below):
  `archive_mode` is still `off`, `wal_level` still `replica`, `trading` is
  141 GB, and there is still no tested restore path; the only "backup" is a torn
  `cp` of the data directory taken against a running server on 2026-08-10. Slice
  169's coverage-cagg rebuild completed on prod 2026-08-16; its deferred
  criterion-18 check falls on Monday 2026-08-24 and depends on acquisition
  running continuously through that week — which constrains when this slice's
  PostgreSQL restart may be taken (see Sequencing).
dateCreated: 20260816
dateUpdated: 20260816
status: not_started
---

# Tasks: Backup and Restore Procedures — Part 1 (Mechanisms)

> Sections 7–10 (restore drill, PITR proof, scheduling, runbook) are in
> [915-tasks.backup-and-restore-procedures-2.md](915-tasks.backup-and-restore-procedures-2.md).

## Context summary

The project has no backup procedure and no tested restore path. This slice is
the outstanding action item from the 2026-08-04 truncation incident and the
sibling of 913: that slice made a credential leak non-destructive, this one
makes destruction survivable.

The deliverable is **not the tooling** — it is a restore drill that has actually
been executed and documented (D6). Tooling that has never restored anything is a
hypothesis.

All decisions referenced below (D1–D7) are in the LLD.

### Measured state — prod `trading` on .144, 2026-08-16

Queried directly during task breakdown, superseding the design's 2026-08-11
figures. These are recorded so Section 1 confirms rather than discovers.

| Fact | Measured value |
|---|---|
| `archive_mode` / `archive_command` | **`off`** / disabled — unchanged, PITR still does not exist |
| `wal_level` | `replica` — sufficient for archiving, no change needed |
| `max_wal_senders` | 10 — `pg_basebackup -Xs` has senders available |
| `archive_timeout` / `max_wal_size` | 0 / 1024 MB |
| `trading` size | **141 GB** (design said 150 GB) |
| Other databases | `trading_test` 7,018 MB, `mt_169_b1` 2,904 MB, 2 small leftovers, `postgres` 11 MB |
| `pg_wal` | 560 MB across 35 segments |
| `trading_migrate` attributes | `rolsuper=f`, **`rolreplication=f`**, `rolcreatedb=f` |
| Restore-target disk | `nvme1n1`, **~800 GB free** (PM-reported 2026-08-16; not verified from here) |

**The metadata tier is populated and actively advancing** — the tables are
live, not empty:

| Table | Rows | Table | Rows |
|---|---|---|---|
| `acquisition_state` | **45,537** | `dividends` | 327,534 |
| `data_gaps` | 105,774 | `instruments` | 32,075 |
| `splits` | 6,568 | `trading_sessions` | 4,560 |
| `universe_members` | 1,127 | `trading_holidays` | 168 |
| `schema_migrations` | 55 | `trading_calendars` | 2 |
| `backfill_state` | 0 | `provider_symbol_mapping` | 0 |

`acquisition_state` shows 12,191 rows touched in the last 24 hours and 18,848 in
the last 7 days, newest attempt 2026-08-16 — the re-run daemons rebuilt it. The
oldest attempt is 2026-01-15. **This is exactly the state that has no external
source and is therefore worth protecting**; it is no longer hypothetical.

Two cautions this measurement produced, both folded into tasks below:

- **`pg_stat_user_tables.n_live_tup` is badly stale on this database** — it
  reported 0 for `instruments`, `dividends`, `splits` and 2 for
  `schema_migrations`, all wrong. Any verification in this slice must use exact
  `count(*)`, never planner estimates. This is the same class of error as
  `approximate_row_count` being +68% wrong on `minute_ohlcv`.
- **`backfill_state` and `provider_symbol_mapping` are genuinely 0.** Confirm
  with the PM whether that is expected before treating a restored 0 as a
  successful restore — a table that is empty on both sides proves nothing.

### Non-negotiables from the design

- **`pg_basebackup -Ft -z -Xs`, never `cp`** (D1). The `-Xs` WAL stream during
  the copy is what makes a live-server backup consistent; a file copy has no
  such guarantee.
- **`wal_level` must stay `replica`** (D2). Lowering it to `minimal` silently
  breaks both archiving and `pg_basebackup`.
- **Archive-failure monitoring is in scope, not follow-up** (D2). A silently
  failing `archive_command` fills `pg_wal` and takes the server down — this
  slice's one way to *cause* an outage rather than prevent one.
- **`archive_command` must refuse to overwrite** an existing segment (D2).
- **The metadata table list is derived from the catalog, never hardcoded** (D4).
  A hardcoded list silently omits tables future migrations add — the same
  by-name drift class as the slice-167 defect.
- **Explicit target, never ambient** (D5). The wrapper takes its DB URL and
  destination as arguments and refuses to run without them, per `sql.md`.
- **Verification is by content, never by exit code or catalog presence** (D6).
  `rclone check` by checksum; cagg parity by row content against source
  hypertables.
- **Script under `scripts/`, not an `mt` subcommand** (D5). Operator tooling,
  not product surface; `mt data` is already 3,371 lines and slice 906 is queued
  to decompose it.

### Sequencing

Sections 1–3 are read-only measurement and local tooling — safe to run at any
time against prod, since nothing writes or restarts.

**Section 4 contains the one disruptive step in the slice**: `archive_mode`
requires a PostgreSQL restart (D2). Two constraints bound when it may be taken:

1. Slice 169's criterion-18 check falls on **Monday 2026-08-24** and requires
   the week's acquisition data to have landed continuously. A restart that
   stalls acquisition could spuriously fail it. **Take the restart after
   2026-08-24**, or treat "acquisition confirmed running afterward" as part of
   the restart task itself.
2. `.144` has two known restart hazards recorded in the host topology notes:
   PostgreSQL does not auto-start reliably, and there is a `listen_addresses`
   boot race. The restart task must verify the server came back and is
   reachable, not assume it.

Section 7 (the drill) is gated on a target with sufficient free disk, which
Section 1 measures and the PM chooses. Do not begin Section 7 until that target
is confirmed.

### Git topology (9xx slices)

Slice 915 is a 9xx maintenance slice: the work branch forks from and merges into
**`trading-data-maintenance`**, never `main`. `cf config` cannot express this —
it is manual discipline at branch creation. Planning documents remain on `main`.

---

## Section 1 — Measure the ground truth before changing anything

The design's prod figures were measured 2026-08-11, before an OS update. Every
number the later sections depend on is re-confirmed here, so no task acts on a
stale measurement.

- [x] **1.1 Re-confirm archive and WAL settings on prod**
  - [x] Query `pg_settings` for `wal_level`, `archive_mode`, `archive_command`,
        `archive_timeout`, `max_wal_size`, `max_wal_senders`
  - [x] Confirm `wal_level` is still `replica` and `archive_mode` still `off`
  - [x] Record the current values verbatim — they are the rollback state for
        Section 4
  - [x] Use `statement_timeout` on every prod query per project rule
  - [x] Success: current settings recorded; any deviation from the design's
        2026-08-11 table is flagged before proceeding
  - [x] Effort: 1
  - [x] Done: measured 2026-08-16 during task breakdown. `archive_mode=off`,
        `archive_command` disabled, `wal_level=replica`, `archive_timeout=0`,
        `max_wal_size=1024`, `max_wal_senders=10`. No deviation from the design's
        table — the premise holds and PITR still does not exist. Rollback state
        for Section 4 is `archive_mode=off` with `archive_command` unset

- [ ] **1.2 Measure filesystem sizes on the host**
  - [x] `pg_database_size` for every non-template database — done 2026-08-16:
        `trading` **141 GB** (not the design's 150 GB), `trading_test` 7,018 MB,
        `mt_169_b1` 2,904 MB, `dbg_sweep_bd61c93e` and `mt_test_0e0e2e2bc659`
        13 MB each, `postgres` 11 MB. `pg_wal` is 560 MB / 35 segments
  - [x] Note the leftover-database count is now **4**, not the design's 9 — some
        were cleaned up since 2026-08-11. `mt_169_b1` (2.9 GB) is slice 169
        residue and is the largest; still out of scope per D7, but worth a
        mention to the PM since it is no longer trivially small
  - [ ] Remaining, and requires host access this breakdown did not have: `df -h`
        on `.144` for the filesystem holding the data directory and for the
        archive/staging destination
  - [ ] Confirm the PM-reported **~800 GB free on `nvme1n1`** and record which
        mount point that corresponds to, and whether it is the same filesystem
        as `PGDATA`. If it is the same device, the restore drill and the archive
        share fate with production — record that explicitly rather than
        discovering it during an incident
  - [ ] Success: free space known in absolute GB per mount point, with `PGDATA`,
        archive destination, and restore target each mapped to a device
  - [ ] Effort: 1

- [ ] **1.3 Measure WAL generation rate**
  - [ ] Sample `pg_current_wal_lsn()` twice, separated by a period with the
        daemon actively writing, and compute bytes/hour from the LSN delta
  - [ ] Sample both during a minute-acquisition pass and during idle, since the
        rates differ substantially
  - [ ] Success: a defensible WAL bytes/day figure exists. This is the input to
        both retention (4.5) and the `pg_wal`-fills-the-disk risk — without it,
        retention is a guess
  - [ ] Effort: 2

- [ ] **1.4 Determine whether the maintenance role can run `pg_basebackup`**
  - [x] Query `pg_roles` for `rolreplication` and `rolsuper` — measured
        2026-08-16: `trading_migrate` has **`rolsuper=f` and `rolreplication=f`**.
        `trading_app` likewise both false. Only `postgres` has either
  - [x] **Confirmed: the anticipated gap is real.** `trading_migrate` holds
        `GRANT postgres TO trading_migrate` (913), but role *membership* confers
        privileges, not role *attributes* — and `pg_basebackup` checks the
        attribute. As it stands, the maintenance credential **cannot** take a
        base backup. Task 1.5 is therefore required, not conditional
  - [x] `max_wal_senders` is 10, so `-Xs` has senders available
  - [ ] Remaining (needs host access): check `pg_hba.conf` admits a
        `replication` connection for `trading_migrate` from the backup host.
        A `replication` connection is matched by its own `pg_hba.conf` line —
        an existing entry for the `trading` database does **not** cover it,
        which is the usual first failure of a `pg_basebackup` attempt
  - [ ] Success: a definite answer to "which credential takes the base backup,
        and what does it need." Do not work around a missing attribute by
        reaching for `postgres`
  - [ ] Effort: 2

- [ ] **1.5 Add the required replication grant to `provision_roles.sql`**
  - [ ] **Required, per the 1.4 measurement** — `trading_migrate` lacks
        `rolreplication`. Add `ALTER ROLE trading_migrate REPLICATION` to
        [scripts/provision_roles.sql](../../../scripts/provision_roles.sql), the
        same reviewed artifact 913 established, guarded for idempotency
  - [ ] Do **not** grant superuser as a shortcut. 913's whole result was that no
        application-reachable credential is destructive; `REPLICATION` alone is
        the minimum `pg_basebackup` needs and does not confer DML or DDL rights
  - [ ] Add the `pg_hba.conf` `replication` line if 1.4 shows it missing, and
        record it in the runbook — it is a host file, not part of the SQL
        artifact, so the artifact alone will not reproduce it on a rebuild
  - [ ] Re-run the artifact twice consecutively under `psql -v ON_ERROR_STOP=1`
        and confirm both exit 0 (913's idempotency contract)
  - [ ] Success: the maintenance credential can open a replication connection;
        the artifact remains idempotent; no new superuser credential exists
  - [ ] Effort: 2

- [ ] **1.6 Confirm host tooling and scheduling mechanism**
  - [ ] Check which of `pg_basebackup`, `pg_verifybackup`, `pg_dump`, `rclone`
        are installed on `.144` and record versions. `pg_verifybackup` requires
        PostgreSQL 13+ — confirm it is present, since D6's per-backup
        verification depends on it
  - [ ] Confirm `cron` is available and note whether a crontab already exists
        for the operator account. The production-deploy runbook records **no
        systemd units installed and no process manager** — so cron is the
        mechanism (D4), and a timer is not available to hang off
  - [ ] Record what currently re-invokes the acquisition passes, if anything —
        the deploy runbook lists this as an open question, and a backup cron
        entry must not collide with it
  - [ ] Success: every binary the later sections invoke is confirmed present, or
        an install step is recorded. No task later discovers a missing tool
  - [ ] Effort: 1

---

## Section 2 — Metadata tier (the direct answer to 2026-08-04)

Built first because it is small, cheap, independently useful, and needs no
restart. It restores the exact tables the incident destroyed without touching
the 150 GB tier.

- [ ] **2.1 Implement catalog-derived metadata table enumeration**
  - [ ] Write the query that enumerates metadata tables **by exclusion**:
        everything in `public` that is not a hypertable and not a continuous
        aggregate (D4)
  - [ ] Source the exclusions from `timescaledb_information.hypertables` and
        `timescaledb_information.continuous_aggregates`, plus the internal
        `_timescaledb_internal` chunk tables
  - [ ] Do **not** hardcode the table list from the design's D4 table — that
        list is the expected *output*, not the input
  - [ ] Success: the query returns the D4 set against prod's current schema,
        derived rather than typed. Expected output as measured 2026-08-16 —
        `acquisition_state`, `data_gaps`, `dividends`, `instruments`, `splits`,
        `trading_sessions`, `universe_members`, `trading_holidays`,
        `schema_migrations`, `trading_calendars`, `backfill_state`,
        `provider_symbol_mapping`. Note `universe_members` is **not** in the
        design's D4 list but is a real metadata table with 1,127 rows; a derived
        query picks it up automatically, which is the point of deriving
  - [ ] Also confirm `daemon_heartbeat` is classified deliberately rather than
        by accident — it is genuinely empty and is runtime state, so excluding
        it is defensible, but it should not be excluded merely because it
        happened to be empty on the day the query was written
  - [ ] Effort: 2

- [ ] **2.2 Implement the metadata dump script**
  - [ ] Create `scripts/backup_metadata.sh` (or `.py` if the enumeration is
        easier in Python — either is acceptable; it orchestrates external
        binaries, so shell is the lower-ceremony choice)
  - [ ] Take the DB URL and destination directory as **required explicit
        arguments**; exit non-zero with a usage message if either is absent
        (D5). Do not read `MT_TIMESCALE_*` from the environment inside the tool
  - [ ] Run the 2.1 enumeration, then `pg_dump -Fc` scoped to those tables via
        repeated `-t` arguments
  - [ ] Write to a timestamped path; exit non-zero on any `pg_dump` failure and
        do not leave a partial file presented as complete (D5)
  - [ ] Success: a single invocation produces a restorable custom-format dump;
        omitting either argument produces a usage error and no dump
  - [ ] Effort: 3

- [ ] **2.3 Test the metadata dump against an ephemeral database**
  - [ ] Test against a throwaway database the fixture creates itself, via
        `MT_TIMESCALE_TEST_URL` — never the production URL (project rule; the
        2026-08-04 incident was a fixture pointed at prod)
  - [ ] Assert: the dump completes; `pg_restore -l` lists the expected tables;
        no hypertable or cagg appears in the dump
  - [ ] Assert the derived-list property directly: create a scratch table in the
        ephemeral database, re-run, and assert it appears in the dump **with no
        script edit**. This is the test that distinguishes a derived list from a
        hardcoded one (success criterion 4)
  - [ ] Assert the tool exits non-zero with no arguments and with a DB URL but
        no destination
  - [ ] Success: all assertions pass; the test reads no production environment
        variable and both static prod-URL ratchet guards still pass
  - [ ] Effort: 3

- [ ] **2.4 Run the metadata dump against prod and record timing**
  - [ ] Run against `trading` using the maintenance credential
  - [ ] Record wall-clock duration and resulting file size
  - [ ] Success: completes in seconds (D4's stated expectation) and the dump is
        non-trivial in size. If it takes materially longer, record why before
        proceeding — the nightly cadence assumes it is cheap
  - [ ] Effort: 1

- [ ] **2.5 Prove the metadata dump actually restores**
  - [ ] `pg_restore` the dump into a throwaway database and compare row counts
        for every dumped table against the source
  - [ ] Use exact `count(*)`, **never** `pg_stat_user_tables.n_live_tup` — it was
        measured badly stale on this database (reported 0 for `instruments` and
        `dividends`, 2 for `schema_migrations`, all wrong). A comparison built on
        estimates would pass against an empty restore
  - [ ] Expected source counts as of 2026-08-16 are in the Measured state table;
        they will have moved by execution time, so re-read source at compare time
        rather than asserting against these figures
  - [ ] The two genuinely-empty tables (`backfill_state`,
        `provider_symbol_mapping`) prove nothing on either side — do not count
        them as evidence of a successful restore
  - [ ] This is a content check, not a "pg_restore exited 0" check (D6)
  - [ ] Success: every non-empty table's row count matches source. A dump that
        has never been restored does not count as a backup
  - [ ] Effort: 2

---

## Section 3 — Base backup tooling (no restart required)

`pg_basebackup` works today without `archive_mode`. Building and proving it here
means the restart in Section 4 changes one setting rather than introducing
untested tooling at the same time.

- [ ] **3.1 Implement the base-backup wrapper script**
  - [ ] Create `scripts/backup_prod.sh` per D5 — `scripts/`, not an `mt`
        subcommand
  - [ ] Required explicit arguments: `--db-url` and `--dest`. Refuse to run if
        either is missing, and never fall back to an environment variable
        (success criterion 8)
  - [ ] Invoke `pg_basebackup -Ft -z -Xs` with the checkpoint mode made explicit
        rather than left to default
  - [ ] Exit non-zero on failure; never leave a partial destination presented as
        a complete backup
  - [ ] Success: the script runs a base backup with explicit arguments and
        refuses every invocation missing one
  - [ ] Effort: 3

- [ ] **3.2 Add `pg_verifybackup` to the wrapper**
  - [ ] After the backup completes, run `pg_verifybackup` against the result and
        fail the whole invocation if it does not verify (D6 level 1)
  - [ ] Note in a comment that `-Ft` archives may need extraction before
        verification depending on server version — determine the working
        invocation empirically in 3.4 rather than assuming
  - [ ] Success: a corrupted or truncated backup causes non-zero exit;
        verification is not skippable by a flag
  - [ ] Effort: 2

- [ ] **3.3 Test the wrapper's refusal behavior**
  - [ ] Unit-level test (no database required): invoke with no arguments, with
        only `--db-url`, and with only `--dest`; assert non-zero exit and a
        message naming the missing argument in each case
  - [ ] Assert the script contains no read of `MT_TIMESCALE_DB_URL` or
        `MT_TIMESCALE_MAINTENANCE_URL` — a static assertion, so a later edge
        toward ambient configuration is caught
  - [ ] Success: all cases fail loudly; success criterion 8 is covered by an
        automated test rather than by memory
  - [ ] Effort: 2

- [ ] **3.4 Take a full base backup against the live prod server**
  - [ ] Run with the daemon **running** — the whole point of D1 is that no
        maintenance window is needed (success criterion 3)
  - [ ] Run off-hours: this is a **141 GB** read against the live box (measured
        2026-08-16; the design's 150 GB was approximate)
  - [ ] Record wall-clock duration, compressed size, and observed impact on
        acquisition (does the daemon keep advancing `acquisition_state`?)
  - [ ] Confirm `pg_verifybackup` reports success on the result
  - [ ] Success: a verified base backup exists, taken live, with duration and
        compressed size recorded. The design deliberately predicts neither — this
        task measures them
  - [ ] Effort: 3

---

## Section 4 — Enable WAL archiving (contains the restart)

**Read the Sequencing note above before starting.** The restart is the one
genuinely disruptive step in the slice and is constrained by the 2026-08-24
criterion-18 check.

- [ ] **4.1 Choose and prepare the archive destination**
  - [ ] Select a local archive directory on `.144` with headroom measured
        against 1.3's WAL rate and 1.2's free space
  - [ ] Ensure it is writable by the `postgres` OS user (the archiver runs as the
        server's OS user, not as a database role — a permissions mistake here is
        the classic cause of the `pg_wal` filling failure)
  - [ ] Confirm it is **not** on the same filesystem as `pg_wal` if that is
        achievable; if it is not, record the shared-fate risk explicitly
  - [ ] Success: destination exists, is writable by `postgres`, and has recorded
        free space sufficient for the retention chosen in 4.5
  - [ ] Effort: 2

- [ ] **4.2 Write the non-overwriting `archive_command`**
  - [ ] Use the conventional refuse-to-overwrite shape (`test ! -f <target> && cp
        ...`) — a command that silently overwrites can corrupt the archive (D2)
  - [ ] Verify the command in isolation before configuring it: run it by hand
        against a real segment file, then run it a second time against the same
        target and confirm it **fails** rather than overwriting
  - [ ] Success: the command copies a new segment and refuses an existing one,
        proven by hand before PostgreSQL ever runs it
  - [ ] Effort: 2

- [ ] **4.3 Configure and restart PostgreSQL**
  - [ ] Set `archive_mode = on` and the 4.2 `archive_command`; leave `wal_level`
        at `replica` — **do not lower it to `minimal`** (D2)
  - [ ] Record the exact config file and lines changed, so rollback is a
        one-line revert to the 1.1 recorded state
  - [ ] Confirm the daemon's state before the restart and stop it cleanly if it
        is running; sequence into a window when it is already stopped (D2/risk)
  - [ ] After restart, verify the server came back **and is reachable from
        `.102`** — the host has a known `listen_addresses` boot race and does not
        auto-start reliably
  - [ ] Restart the daemon and confirm acquisition resumes (`acquisition_state`
        advancing), so the 2026-08-24 check is not compromised
  - [ ] Success: `archive_mode = on` in `pg_settings`, server reachable
        remotely, acquisition observably advancing again after the restart
  - [ ] Effort: 3

- [ ] **4.4 Verify segments actually land in the archive**
  - [ ] Query `pg_stat_archiver`: expect `archived_count` > 0 and
        `last_failed_wal` NULL
  - [ ] Force a segment switch with `SELECT pg_switch_wal()` and confirm
        `archived_count` increments **and the segment file appears at the
        destination** — the catalog counter alone is not evidence the file is
        where you think it is
  - [ ] Success: success criterion 1 satisfied, with the destination directory
        listing as evidence alongside the catalog figures
  - [ ] Effort: 1

- [ ] **4.5 Settle and implement WAL retention**
  - [ ] Choose retention from measured inputs: at least the base-backup interval
        plus margin. WAL older than the oldest base backup you intend to restore
        from is useless; WAL newer than your newest base backup is mandatory
        (D2)
  - [ ] Compute the resulting steady-state archive size from 1.3's WAL rate and
        confirm it fits 4.1's free space with margin
  - [ ] Implement expiry (a dated-directory prune, or `pg_archivecleanup` keyed
        to the oldest retained base backup)
  - [ ] Success: retention is a written, justified number tied to the base-backup
        cadence — not "until the disk fills." The fourth row of the LLD's
        recovery-path table is the failure this task exists to prevent
  - [ ] Effort: 3

---

## Section 5 — Archive-failure monitoring (in scope, per D2)

Success criterion 2 requires the alarm be **demonstrated firing**, not merely
written. An unfired alarm is untested.

- [ ] **5.1 Implement the archive-health check**
  - [ ] Check `pg_stat_archiver`: `last_failed_wal` non-null, `last_failed_time`
        recent, `failed_count` increasing, and the gap between the last archived
        WAL and the current WAL position
  - [ ] Also check `pg_wal` directory size against a threshold — the archiver can
        fall behind without recording a hard failure, and disk exhaustion is the
        actual outage mode
  - [ ] Take the DB URL as an explicit argument, consistent with D5
  - [ ] Success: the check returns a clear pass/fail and names which condition
        tripped; it does not require reading logs to interpret
  - [ ] Effort: 3

- [ ] **5.2 Surface the check where an operator actually looks**
  - [ ] Wire it to a mechanism the operator sees — cron output to mail, a status
        line the existing tooling prints, or a file the deploy runbook says to
        check. Decide with the PM if the destination is unclear
  - [ ] Avoid inventing a notification subsystem; this slice needs the signal to
        reach a human, not a monitoring platform
  - [ ] Success: a failing archive produces a signal in a place named in the
        runbook, not only an exit code in a cron log nobody reads
  - [ ] Effort: 2

- [ ] **5.3 Demonstrate the alarm firing, then recovering**
  - [ ] Deliberately break the archive destination in a controlled way (revoke
        write permission — reversible and less disruptive than an unmount)
  - [ ] Force segment switches; confirm the monitor alarms and that `pg_wal`
        growth is visible **before** it becomes dangerous
  - [ ] Restore the destination and confirm the archiver **drains its backlog**
        and `last_failed_wal` clears
  - [ ] Watch free space throughout; abort and restore immediately if headroom
        drops below the 4.1 margin. This task deliberately induces the slice's
        one outage mode — do not run it unattended
  - [ ] Success: success criterion 2 satisfied — the alarm fired in a controlled
        test, and the archiver recovered without intervention beyond fixing the
        destination
  - [ ] Effort: 3

---

## Section 6 — Offsite copy

- [ ] **6.1 Set up the Backblaze B2 account and bucket**
  - [x] Target decided: **B2**, confirmed by the PM 2026-08-16. Chosen for no
        minimum storage duration, immediate restore, and free egress (which
        removes the disincentive to repeat the drill). Deep Archive's 180-day
        floor and 12–48 h restore were the disqualifiers, not its headline price
  - [ ] PM action: create the B2 account (not yet held as of 2026-08-16)
  - [ ] Create a bucket for this project; keep it **private**, and note that a
        bucket holding a full database copy must never be public
  - [ ] Create an **application key scoped to that bucket**, not the master key.
        A master key can delete every bucket in the account; a scoped key limits
        what a leaked credential reaches — the same reasoning as slice 913
  - [ ] Expected cost at 141 GB: roughly $1/month at B2's current rate
  - [ ] Success: bucket exists, is private, and a bucket-scoped application key
        authenticates
  - [ ] Effort: 1

- [ ] **6.2 Configure credentials without committing them**
  - [ ] Object-store credentials come from the environment or an
        operator-provided config path — **never** committed; `.env` stays
        gitignored (project rule and D3)
  - [ ] Document the required variables in `.env_sample` as commented keys, with
        no values
  - [ ] Success: the sync authenticates; `git status` is clean after
        configuration; no credential appears in any tracked file
  - [ ] Effort: 1

- [ ] **6.3 Add checksum-verified sync to the wrapper**
  - [ ] Extend `scripts/backup_prod.sh` with the offsite sync step, taking the
        remote as another explicit argument
  - [ ] After sync, run `rclone check <local> <remote> --one-way` and fail the
        invocation on any difference
  - [ ] A clean exit from the sync itself is **not** sufficient evidence and does
        not satisfy success criterion 5 (D6)
  - [ ] Success: the wrapper is one invocation — base backup, verify, sync,
        checksum-check — and any failing stage fails the whole run non-zero
  - [ ] Effort: 3

- [ ] **6.4 Test the sync failure path**
  - [ ] Point the sync at an invalid remote and confirm the wrapper exits
        non-zero and does not report success
  - [ ] Corrupt or truncate a local file after backup but before check, and
        confirm `rclone check` catches it
  - [ ] Success: both failures are caught and loud. This is the test that proves
        the checksum step is load-bearing rather than decorative
  - [ ] Effort: 2

- [ ] **6.5 Perform the first real offsite push**
  - [ ] Sync the 3.4 base backup and a 2.4 metadata dump to the chosen bucket
  - [ ] Record upload duration and observed throughput — this bounds how long a
        real restore's download will take
  - [ ] Success: success criterion 5 satisfied — objects present in the bucket
        and verified by checksum, with timings recorded
  - [ ] Effort: 2

---

## Continued in Part 2

Sections 7–10 are in
[915-tasks.backup-and-restore-procedures-2.md](915-tasks.backup-and-restore-procedures-2.md):
the restore drill, the point-in-time recovery proof, scheduling, the runbook,
and close-out.

Part 1 builds and proves the mechanisms. Part 2 proves recovery — which is the
only thing this slice is actually for. Do not treat Part 1's completion as the
slice being delivered.

