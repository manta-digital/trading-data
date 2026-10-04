---
docType: slice-design
slice: tick-restore-drill
project: trading-data
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [227]
interfaces: []
dateCreated: 20261004
dateUpdated: 20261004
status: not_started
---

# Slice Design: tick-restore-drill

## Overview

After slice 227, the tick tier is backed up in two pieces: the archive (`/data/tick-archive`) in the nightly restic backup, and the tick database (`17/tick`, `trading_tick`) as weekly base backups plus WAL plus a nightly bookkeeping dump. The architecture requires that "the restore drill covers both". So far only one archive file has ever been restored (slice 223).

This slice adds `scripts/drill_tick_restore.py`, a reusable drill that proves three things in one run:
1. **Archive:** the archive restores from restic.
2. **Database:** the database restores from base + WAL and matches production.
3. **Fallback:** a database rebuilt from the *restored* archive matches the restored database row for row.

It also writes both restore procedures and the drill into runbook 200. This was split out of 227's design on 2026-10-04; 227's design records the policy decision (full weight, rebuild as fallback) that this drill proves.

## Value

- **Backups proven to restore.** A backup nobody has restored is a hope. After this slice, the tick tier's primary restore path and its fallback are both run end to end, from real backup media, with exact comparisons.
- **The fallback stays honest.** Rebuild-from-archive is the answer when the base/WAL chain is broken. Running it every quarter from the restored archive, not the live one, proves the archive backup alone is enough to recover the data and the money.
- **A quarterly job, not a one-off.** The drill joins runbook 200's quarterly drill (next due 2026-11-17) as one command with a written report.

## Technical Scope

**Included**
1. `scripts/drill_tick_restore.py`: the drill (TD1–TD5). It reads paths from 227's `deploy/backup-clusters.conf`.
2. One fingerprint SQL definition for `tick_trade`, used for every comparison (TD3).
3. The expected-difference set for a rebuilt database's bookkeeping, defined once (TD4).
4. Runbook 200, in its tick section:
   - **Primary restore procedure:** base + WAL into a fresh data directory, and putting a restored cluster back into service as `17/tick`.
   - **Fallback procedure:** restic archive restore, then the four rebuild commands.
   - **Drill:** the drill command, what it proves, and a drill-record row.
5. Running the drill once on manta9000, with its report in `user/notes`.

**Excluded**
- Automating production's (`17/main`) drill. Runbook 200 Step 6 stays manual. The drill's base + WAL restore step takes a cluster row, so it can serve main later; switching main is not this slice.
- Scheduling the drill. It runs quarterly, by hand, like production's.
- Point-in-time recovery to a chosen timestamp. The drill restores to the end of the archived WAL; runbook 200's existing PITR section covers targets.

## Dependencies

### Prerequisites
- **227:** tick base backups, WAL and metadata dumps under `/data/backup/17-tick`; the cluster table; `trading_tick_drill` allowed in the tick `pg_hba`.
- **226:** the rebuild commands and the measured rebuild cost (212.5 s plus compression).
- **223/224:** `mt data tick adopt` re-hashes every file against its job's `manifest.json`. The 224 slice design lists what an adopt-based rebuild loses.

### Interfaces Required
- The production database (`MT_TIMESCALE_DB_URL`): adopt, the pass and ingest read the CME calendar from it.
- `DATABENTO_API_KEY`: adopt's free `batch_job` reads.
- The restic repository (root, through `deploy/lib/restic_repo.sh`).

## Architecture

### Component Structure

```
scripts/drill_tick_restore.py
  ├─ reads deploy/backup-clusters.conf (227's parser)  → tick backup root, WAL dir, base dir
  ├─ sudo restic restore  → /data/restore-test/228-drill-<stamp>/tick-archive
  ├─ pg_ctl (as manta)    → /data/restore-test/228-drill-<stamp>/pgdata   (socket-only scratch server)
  ├─ psql on 17/tick      → CREATE DATABASE trading_tick_drill (provision_tick_roles.sql)
  ├─ uv run mt data …     → rebuild into trading_tick_drill from the restored archive
  └─ report               → project-documents/user/notes/<date>-228-tick-restore-drill.md
```

### Data Flow

```
restic system/ ──restore──► drill/tick-archive ──adopt+ingest──► trading_tick_drill ─┐
                                                                                    ├─ compare (fingerprint, bookkeeping)
/data/backup/17-tick/{base,wal} ──restore──► drill/pgdata (scratch server) ─────────┤
                                                                                    └─ compare (counts, fingerprint)
                                                         production trading_tick ───┘
```

### State Management

The drill owns only what it creates, and removes all of it:
- `/data/restore-test/228-drill-<stamp>/`;
- the scratch server, which is stopped;
- `trading_tick_drill`, marked by a database comment naming the drill.

Production `trading_tick` is only read. While it is compared, the drill holds the tick advisory lock so it can't change.

## Technical Decisions

### TD1. Steps

The drill runs as manta from the checkout root and uses `sudo` only for restic. Each step is check-then-act and prints expected against seen.

1. **Hold the tick cluster.** Take the tick advisory lock for the whole run. If a tick run holds it, refuse.
2. **Make the restore point current.** Run `pg_switch_wal()` on `17/tick` and wait (bounded) until that segment's `.zst` is in the tick WAL directory.
3. **Restore the archive.** Run restic `restore latest --include /data/tick-archive` into the drill directory. Compare file count and total bytes with the live archive.
4. **Restore the database.**
   - Unpack the latest tick base backup into the drill directory and run `pg_verifybackup` on it.
   - Set runbook 200's two-shape `restore_command` (`.zst` and raw segments) against the tick WAL directory.
   - Start it with `pg_ctl` as manta, socket-only in the drill directory, `archive_mode=off`. Wait for recovery to finish.
5. **Compare the restored database with production:**
   - exact row counts for every public table;
   - the `tick_trade` fingerprint (TD3).

   All must be equal.
6. **Rebuild.** Create `trading_tick_drill` (TD5), then run against it:
   ```
   mt data migrate apply --track tick
   mt data tick adopt --job-id <dir> --source <restored dir>     # once per restored job directory
   mt data tick pass --estimate-only
   mt data tick ingest
   ```
   `MT_TICK_DB_URL`, `MT_TICK_MAINTENANCE_URL` and `MT_TICK_ARCHIVE_DIR` point at the drill database and the restored archive, set in the subprocess environment only, never in `.env`. Adopt re-hashes every restored file against its job's `manifest.json`, so this step is also the full archive verification. The rebuild time is recorded.
7. **Compare the rebuild with the restore.**
   - The `tick_trade` fingerprints must be equal: the architecture's "row-for-row the same projection".
   - Bookkeeping differences are listed and checked against the expected set (TD4). Any difference outside it fails.
8. **Clean up** (always, in `finally`): stop the scratch server, drop `trading_tick_drill`, remove the drill directory.
9. **Report** to `user/notes/<date>-228-tick-restore-drill.md`: every step's expected and seen values, the timings, and the bookkeeping differences. Exit 0 only when every check passes.

### TD2. Why the rebuild uses the restored archive

Rebuilding from the live archive would prove only what 226 already measured. Rebuilding from the restic copy proves the backed-up archive is complete and byte-correct, because adopt's per-file SHA-256 check would fail on any missing or damaged file. It also proves the fallback works at today's archive size, not just at 226's.

### TD3. One `tick_trade` fingerprint

The fingerprint is per `(instrument_id, UTC day)`: row count, plus the md5 of the rows' text in `(ts_event, sequence, sequence_ordinal)` order (the natural key's order). It is defined once as a SQL constant in the drill module and run against all three databases.

- Two equal fingerprint sets mean the same rows in every group. Compression state is not part of the claim (architecture: "physical layout, including compression state, is not part of the claim").
- The cost is one ordered scan per day group. At 27.7 M rows today that's a full read of a 0.65 GiB table per database.

### TD4. Expected bookkeeping differences, defined once

The 224 slice design, in its decision on adopting existing batch files, lists what a rebuild loses. The drill encodes it as one constant: a set of `(table, column)` pairs that may differ between the restored and rebuilt databases.

- **Columns that may differ:**
  - `tick_request`: `is_adopted`, the estimate fields, `download_deadline`;
  - `tick_archive_unit`: the repurchase and supersession links, `reopened_at`.
- **Rows that may be missing:** rows for paid jobs with no archived files may be missing from the rebuild.
- **Everything else must be equal:** any other differing column, or a missing row whose job *does* have files, fails.

The exact column names are taken from the tick migrations during implementation and are pinned by a test.

### TD5. The drill database is the drill's alone

`trading_tick_drill` is created through `provision_tick_roles.sql -v tick_db=trading_tick_drill` (owner `tick_migrate`, same grants as production), with `COMMENT ON DATABASE` naming the drill and its run stamp.

At startup:
- a leftover `trading_tick_drill` with the drill's comment is dropped (a crashed previous run);
- one without the comment is a refusal.

This is CLAUDE.md's rule for destructive statements: only on a database this process created.

### Patterns and Conventions
- **Script pattern:** follows `cutover_common.py` for steps, sudo, the report and exit status.
- **Reuse:** the cluster-table parser is 227's; the restore command is the runbook's two-shape form, not a new one.
- **Errors:** every failure names the step and the expected-against-seen values. No step is skipped silently: an unreachable production database or Databento API fails step 6 by name.

## Integration Points

### Provides to Other Slices
- **Operators:** the quarterly tick drill command and its report, next to production's drill in runbook 200.
- **The later hammerhead move:** the base + WAL restore step, run against a cluster row, is the same procedure the move uses to bring the copy up.

### Consumes from Other Slices
- 227's backups, cluster table and `pg_hba` entry; 226's rebuild commands; 224's list of what a rebuild loses.
- **If the production DB or the Databento API is unreachable:** step 6 fails by name. Steps 3–5 still report, so the drill shows whether the primary path works.

## Success Criteria

### Functional Requirements
1. `scripts/drill_tick_restore.py` runs on manta9000 and exits 0.
2. Its report shows:
   - the archive restored, with file count and bytes equal to the live archive;
   - the database restored from base + WAL, with every public table's row count and the `tick_trade` fingerprint equal to production's;
   - the database rebuilt from the restored archive, with a `tick_trade` fingerprint equal to the restored database's, and bookkeeping differences only from the expected set;
   - the rebuild time;
   - no leftovers: the drill directory, scratch server and drill database are gone.
3. A second run straight after the first also exits 0 (no state carried between runs).
4. Runbook 200's tick section has the primary restore procedure, the fallback procedure, the drill command and a drill-record row with this run's date.

### Technical Requirements
- Integration tests on the test cluster's tick database:
  - the fingerprint SQL (equal for identical data; different for one changed price and for one missing row);
  - the expected-difference check (allowed column passes; any other column fails; missing row with archived files fails).
- Unit tests:
  - leftover drill-database handling (with comment → drop; without → refuse), using a stubbed `psql`;
  - the report's exit status.
- The full drill runs only on the host, as the slice's verification.
- ruff and mypy clean on touched files.

### Integration Requirements
- The next quarterly drill (due 2026-11-17) runs production's Step 6 plus this script.

### Verification Walkthrough

These are the commands as designed; Phase 6 refines them with real output.

1. **Run the drill** (PM or agent; prompts once for `sudo`):
   ```
   uv run python scripts/drill_tick_restore.py
   ```
   Expect each step printed with expected and seen values, ending with `PASS: archive, database, fallback`, and a report path under `user/notes/`.

2. **Read the report:**
   ```
   less project-documents/user/notes/<date>-228-tick-restore-drill.md
   ```
   Expect:
   - archive file count and bytes equal to the live archive;
   - all table counts equal to production;
   - fingerprints equal;
   - the bookkeeping differences listed, all in the expected set;
   - a rebuild time of the order of 226's 212.5 s plus compression for the current archive.

3. **Confirm cleanup:**
   ```
   ls /data/restore-test/                                   # no 228-drill-* directory
   psql "$MT_TICK_MAINTENANCE_URL" -Atc "select datname from pg_database where datname='trading_tick_drill'"   # no rows
   ```

4. **Run it again:** the same command exits 0 a second time.

## Implementation Notes

### Development Approach
1. The fingerprint SQL and the expected-difference constant, with their integration tests.
2. The drill-database lifecycle (create, comment, leftover handling, drop), with unit tests.
3. Steps 1–5 (hold, WAL switch, archive restore, base + WAL restore, compare with production), run on the host.
4. Steps 6–9 (rebuild, compare, cleanup, report), run on the host.
5. The runbook 200 sections, then the recorded drill run.

### Special Considerations
- **Disk:** the drill needs room for the archive copy, the restored data directory and the drill database: about three times the tick footprint (roughly 2.5 GB today), against 1.2 TB free on `/data`. The drill checks free space before step 3, sized from the live archive and the latest base backup.
- **Production safety:** production `trading_tick` is only read. The scratch server is socket-only with `archive_mode=off`, so it can't write into the tick WAL archive or accept network connections.
- **Ordering:** the drill holds the tick advisory lock throughout, so no tick pass can run during it. The tick schedule is manual today, so nothing is blocked.
