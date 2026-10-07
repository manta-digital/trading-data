---
docType: slice-design
slice: tick-restore-drill
project: trading-data
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [227]
interfaces: []
dateCreated: 20261004
dateUpdated: 20261007
status: in_progress
---

# Slice Design: tick-restore-drill

## Overview

After slice 227, the tick tier is backed up in two pieces: the archive (`/data/tick-archive`) in the nightly restic backup, and the tick database (`17/tick`, `trading_tick`) as weekly base backups plus WAL plus a nightly bookkeeping dump. The architecture requires that "the restore drill covers both". So far only one archive file has ever been restored (slice 223).

This slice adds `scripts/drill_tick_restore.py`, a reusable drill that proves three things in one run:
1. **Archive:** the archive restores from restic.
2. **Database:** the database restores from base + WAL and matches production.
3. **Fallback:** a database rebuilt from the *restored* archive matches the restored database row for row.

It also writes both restore procedures and the drill into runbook 200 (`user/runbooks/200-backup-and-restore.md`, the backup runbook). This was split out of 227's design on 2026-10-04; 227's design records the policy decision (full weight, rebuild as fallback) that this drill proves.

## Value

- **Backups proven to restore.** A backup nobody has restored is a hope. After this slice, the tick tier's primary restore path and its fallback are both run end to end, from real backup media, with exact comparisons.
- **The fallback stays honest.** Rebuild-from-archive is the answer when the base/WAL chain is broken. Running it every quarter from the restored archive, not the live one, proves the archive backup alone is enough to recover the data and the money.
- **A quarterly job, not a one-off.** The drill joins runbook 200's quarterly drill (next due 2026-11-17) as one command with a written report.

## Technical Scope

**Included**
1. `scripts/drill_tick_restore.py`: the drill (TD1–TD6). It reads paths from 227's `deploy/backup-clusters.conf`.
2. One fingerprint SQL definition for `tick_trade`, used for every comparison (TD3).
3. The bookkeeping comparison and its expected-difference set, defined once (TD4).
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
- **227:** tick base backups, WAL and metadata dumps under `/data/backup/17-tick`, readable by manta; the cluster table. (227's `trading_tick_drill` entry in the tick `pg_hba` is no longer used: the drill database lives on the scratch server, TD5.)
- **226:** the rebuild commands and the measured rebuild cost (212.5 s plus compression).
- **223/224:** `mt data tick adopt` re-hashes every file against its job's `manifest.json`. The 224 slice design lists what an adopt-based rebuild loses.

### Interfaces Required
- The production database (`MT_TIMESCALE_DB_URL`), read only: adopt and the pass read the CME calendar from it (`tick_calendar.py`). No tick command writes to it, and tick runs record no `pass_runs` rows. The tick code never extends the calendar: a range past its populated horizon fails with `TickCalendarError` (exit 4) and writes nothing.
- `MT_DATABENTO_API_KEY`: adopt's free `batch_job` reads and the pass's free estimate and metadata reads.
- The restic repository (root, through `deploy/lib/restic_repo.sh`).

## Architecture

### Component Structure

```
scripts/drill_tick_restore.py
  ├─ reads deploy/backup-clusters.conf (227's parser)  → tick backup root, WAL dir, base dir
  ├─ psql on 17/tick (read only) → advisory locks, pg_switch_wal, comparison reads
  ├─ sudo restic restore  → /data/restore-test/228-drill-<stamp>/tick-archive
  ├─ pg_ctl (as manta)    → /data/restore-test/228-drill-<stamp>/pgdata   (socket-only scratch server)
  │     ├─ trading_tick        restored from base + WAL
  │     └─ trading_tick_drill  created here, rebuilt from the restored archive
  ├─ uv run mt data …     → rebuild into trading_tick_drill on the scratch server
  └─ report               → project-documents/user/notes/<date>-228-tick-restore-drill.md
```

### Data Flow

```
restic system/ ──restore──► drill/tick-archive ──adopt+ingest──► scratch: trading_tick_drill ─┐
                                                                                               ├─ compare (fingerprint, bookkeeping)
/data/backup/17-tick/{base,wal} ──restore──► scratch: trading_tick ────────────────────────────┤
                                                                                               └─ compare (counts, fingerprint)
                                                                    production trading_tick ───┘
```

### State Management

The drill owns only what it creates, and removes all of it:
- `/data/restore-test/228-drill-<stamp>/`, marked by a `drill-228.json` file written first (stamp, pid);
- the scratch server inside it, which holds both the restored `trading_tick` and `trading_tick_drill`.

Production is only read: `17/tick` for the lock and the comparison reads, `17/main` for the calendar. The one exception is step 2's `pg_switch_wal`, which closes the current tick WAL segment early. That segment is archived and pushed to B2 like any other, which is harmless. No table data is created, written or dropped on either production cluster, so the rebuild adds no WAL to the tick archive and no I/O to the production tick cluster beyond step 5's reads.

## Technical Decisions

### TD1. Steps

The drill runs as manta from the checkout root and uses `sudo` only for restic and for the `chown` after it. Each step is check-then-act and prints expected against seen.

0. **Prepare.**
   - Take the drill's own lock, `flock` on `/data/restore-test/.228-drill.lock`, held for the whole run. If another drill holds it, refuse.
   - Run `sudo -v`, so the one password prompt comes now, before any production lock. Every later `sudo` call uses `sudo -n`, so an expired timestamp fails by name instead of waiting on a prompt.
   - Clear leftovers (TD5), then check free space (Special Considerations).
1. **Hold the tick cluster.**
   - The drill's own connection to `17/tick` is `MT_TICK_MAINTENANCE_URL` from `.env`: `tick_migrate`, TCP by host name, database `trading_tick`. It is opened read-only (`default_transaction_read_only=on`) with TD6's statement timeout.
   - Take both tick advisory locks on it, acquisition then ingest (`cutover_227_host.TICK_LOCK_KEYS`). They are distinct so ingest can run alongside acquisition, so holding one excludes only half the tick runs. If either is held, refuse.
   - Check `has_table_privilege(…, 'SELECT')` for every public table. A missing grant fails by name.
   - Read the recovery settings step 4 copies.
   - **Archive and snapshot agree.** The restored archive comes from restic's latest snapshot, while the restored database is brought up to now. If a tick run added or changed archive files since that snapshot, steps 3 and 7 would fail even though both backups are fine. So, under the lock, the drill compares the live archive's backed-up set (step 3) with the snapshot's listing of `/data/tick-archive` (`restic ls latest`, through `sudo -n`): file count, total bytes, and no live file newer than the snapshot time. If any differ, it refuses with "archive changed since snapshot <time>; run the restic backup first". Ingest writes only the database, so a run after the snapshot that only ingested doesn't trip this, and doesn't need to: the rebuild ingests every file anyway.
2. **Make the restore point current.** Run `SELECT pg_walfile_name(pg_switch_wal())` on `17/tick` as postgres over the local socket, through 227's `cutover_227_host.psql` helper (`sudo -n -u postgres`): `pg_switch_wal` needs superuser. That names the segment the restore must reach, whether or not the switch had anything to close: with no WAL activity since the last switch, it names the last completed segment, which is already archived, and the wait ends at once. Wait until that segment's `.zst` or raw file is in the tick WAL directory.
3. **Restore the archive.** Run restic `restore latest --include /data/tick-archive` into the drill directory, then `sudo chown -R manta:manta` on that restored directory only (its path is checked to sit under the marked drill directory first). Compare file count and total bytes with the live archive's **backed-up set**: the live archive minus the paths `deploy/restic-excludes.txt` excludes (today `**/*.partial`, an unfinished download or adoption copy, which is not the record). The exclusion is read from that file, not typed here, and step 1's snapshot check uses the same set.
4. **Restore the database.**
   - Unpack the latest tick base backup into the drill directory and run `pg_verifybackup` on it.
   - **Empty `postgresql.auto.conf`.** The base backup carries it, and it holds production's `ALTER SYSTEM` settings, including `archive_mode=on` and the `archive_command` into the live tick WAL directory. It overrides `postgresql.conf`, so a scratch server started with it would archive its new timeline into production's archive and on to B2. The drill truncates it and checks that no `archive` setting is left (runbook 200 Step 6's `grep -c archive` = 0). If either fails, it refuses to start the server.
   - Write the scratch server's own `postgresql.conf` and `pg_hba.conf` into the data directory (the Debian layout keeps production's under `/etc`, so the base backup has none). They set:
     - `listen_addresses=''`, with the socket directory in the drill directory, mode 0700;
     - `local all all trust`, so only manta can reach the socket;
     - `archive_mode=off`;
     - `shared_preload_libraries=timescaledb`, with `timescaledb.max_background_workers=0`, so no policy job runs during the comparison;
     - `max_worker_processes`, `max_locks_per_transaction` and `max_connections` at production's values, read from `17/tick` in step 1 rather than typed in (archive recovery refuses to start below the primary's, and emptying `postgresql.auto.conf` drops them; runbook 200's 2026-09-07 PITR drill hit this);
     - runbook 200's two-shape `restore_command` (`.zst` and raw segments) against the tick WAL directory.
   - Create `recovery.signal`. Without it the server runs crash recovery only and never calls `restore_command`.
   - Start it with `pg_ctl` as manta and wait for recovery to finish (`pg_is_in_recovery()` false). If the server process exits during the wait, the step fails with the tail of its log.
   - **Recovery reached the target.** The server log's last restored segment must be the one named in step 2, or later. If it isn't, the step fails as "recovery stopped early at <segment>", which names a WAL-chain gap directly instead of leaving it to show up as a step-5 mismatch.
5. **Compare the restored database with production:**
   - exact row counts for every public table;
   - the `tick_trade` fingerprint (TD3);
   - for each bookkeeping table (TD4's list), the md5 of its rows in primary-key order. A physical restore keeps the ids, so these must match exactly. Bookkeeping is the part that can't be rebuilt, so the primary path proves it value for value.

   All must be equal. Then confirm both locks are still held: `pg_locks` shows them on the lock connection's own backend. Any error on that connection, or a missing lock, fails the step, since production could have changed under the comparison. Release the locks. Production is not touched after this point.
6. **Rebuild.** Create `trading_tick_drill` on the scratch server (TD5), then run against it:
   ```
   mt data migrate apply --track tick
   mt data tick adopt --job-id <dir> --source <restored dir>     # once per restored job directory
   mt data tick pass --estimate-only
   mt data tick ingest
   ```
   `MT_TICK_DB_URL`, `MT_TICK_MAINTENANCE_URL` and `MT_TICK_ARCHIVE_DIR` point at the scratch server's socket, the drill database and the restored archive. They are set in the subprocess environment only, never in `.env`. The same environment overrides `MT_TIMESCALE_DB_URL` with `.env`'s value plus `options=-c default_transaction_read_only=on`, so the calendar connection is read-only by enforcement, not just by the tick code's behaviour. A write attempt fails by name. (`PGOPTIONS` can't be used: it would make the drill database read-only too.) Socket URLs are a drill-only exception to the architecture's TCP-by-host-name rule for the production URL; implementation step 2 confirms that settings validation and the migrate CLI accept them. Adopt re-hashes every restored file against its job's `manifest.json`, so this step is also the full archive verification. The rebuild time is recorded in the report, not gated.
7. **Compare the rebuild with the restore**, both on the scratch server.
   - The `tick_trade` fingerprints must be equal: the architecture's "row-for-row the same projection".
   - The bookkeeping comparison (TD4). Any difference outside the expected set fails.
8. **Clean up** (always, in `finally`): stop the scratch server, then remove the drill directory with `sudo -n rm -rf`, after checking that the path is a marked `228-drill-*` directory under `/data/restore-test`. Using sudo covers root-owned files a failed or killed restic restore or `chown` left behind. If sudo isn't available, cleanup fails by name and leaves the path in the report.
9. **Report** to `user/notes/<date>-228-tick-restore-drill.md`: every step's expected and seen values, the timings, and the bookkeeping differences. Exit 0 only when every check passes.

### TD2. Why the rebuild uses the restored archive

Rebuilding from the live archive would prove only what 226 already measured. Rebuilding from the restic copy proves the backed-up archive is complete and byte-correct, because adopt's per-file SHA-256 check would fail on any missing or damaged file. It also proves the fallback works at today's archive size, not just at 226's.

### TD3. One `tick_trade` fingerprint

The fingerprint is per `(instrument_id, UTC day)`: row count, plus the md5 of the rows' text in `(ts_event, sequence, sequence_ordinal)` order (the natural key's order). It is defined once as a SQL constant in the drill module and run against all three databases.

- **Hashed columns:** every `tick_trade` column except `unit_id`. The list is built from `storage_columns.py` (`TICK_TRADE_COLUMNS`, the BBO columns, `sequence_ordinal`), not typed out, so a new column is hashed automatically.
- **Why `unit_id` is left out:** it is an identity value from `tick_archive_unit`, and a rebuild numbers units in adopt order, not production's purchase order. It is not part of the natural key. Which unit wrote each row is checked by TD4 through the ledger, by natural key.
- Two equal fingerprint sets mean the same rows in every group. Compression state is not part of the claim (architecture: "physical layout, including compression state, is not part of the claim").
- The cost is one ordered scan per day group. At 27.7 M rows today that's a full read of a 0.65 GiB table per database.

### TD4. The bookkeeping comparison, defined once

Every tick bookkeeping table is compared, restored against rebuilt: `tick_request`, `tick_archive_unit`, `tick_definition`, `tick_ingest_ledger`, `tick_dataset_edge`, `tick_day_condition`. The table list is checked against the tick migration's tables by a test, so a new table can't be skipped silently.

- **Rows are matched by natural key, never by identity ids.** `request_id` and `unit_id` are renumbered by a rebuild. A request is keyed by `provider_job_id`, a unit by `(provider_job_id, unit_date)`.
  - A production request with no `provider_job_id` was never accepted by the provider (written at *requested*, then exhausted or still pending). It has no job and so no files, and a rebuild can't recreate it. Such requests and their units are allowed to be missing from the rebuild and are listed in the report by count. A rebuilt request without a job id fails, since adopt only creates requests from job directories. Every id-valued column (`request_id`, `unit_id`, `superseded_by_unit_id`, `repurchase_of_unit_id`) is compared through that mapping:
  - a definition's unit;
  - a ledger row's unit, so the ledger also proves which unit wrote each session part;
  - a unit's supersession and repurchase links.
- **Columns that may differ** (the 224 design's adopt-path losses, plus the times a rebuild sets anew):
  - `tick_request`: `is_adopted`, the estimate fields (`estimated_cost_usd`), `download_deadline`, `requested_at` (adopt writes the provider's `ts_received`; a pass purchase writes its own clock at planning. `committed_at` is `ts_received` on both paths, so it must match; PM decision 2026-10-07);
  - `tick_archive_unit`: the repurchase and supersession links, `reopened_at`, `state_changed_at`, `last_attempt_at`, `attempt_count`, `failure_reason` (a rebuild adopts each file once, with none of production's retries);
  - `tick_dataset_edge` and `tick_day_condition`: every value column. The pass re-observes them from the provider at drill time. The rebuild must still have a condition row for every `(dataset, condition_date)` the restored database has.
- **Rows that may be missing:**
  - requests with no job id (above);
  - rows for paid jobs with no archived files;
  - units with no archived file, whatever their state: retention-expired failures, `RETRY_EXHAUSTED`, `PROVIDER_HOLE`. A rebuild has only units it adopted from files.
- **Everything else must be equal:** any other differing column, a unit with a file whose state differs, or a missing row whose unit *does* have a file, fails.

The set is one constant of `(table, column)` pairs. The exact column names are taken from the tick migrations during implementation and pinned by a test.

### TD5. Everything the drill creates lives in its marked directory

`trading_tick_drill` is created on the scratch server through `provision_tick_roles.sql -v tick_db=trading_tick_drill`. The script is idempotent and the restored cluster already has the tick roles, so it creates only the database: owner `tick_migrate`, same grants as production. Dropping it is never needed: it goes with the scratch server.

At startup, under the drill's `flock` (step 0), so no other drill is running, for each `/data/restore-test/228-drill-*` directory:
- **with the marker file:** if its scratch server is running, stop it with `pg_ctl stop -m fast`, then remove the directory as step 8 does, with the same path check and `sudo -n rm -rf` (a run that was killed, ran out of memory, or lost the host, possibly leaving root-owned restic files);
- **without the marker:** refuse, naming the path.

This meets CLAUDE.md's rule for destructive actions: only on what this process (or an earlier run of it, by its marker) created. No destructive statement is ever sent to a production cluster.

### TD6. Time bounds, defined once

Each wait or subprocess has a bound, defined once as a constant in the drill module. Past its bound the step fails by name and cleanup still runs.

| Step | Bound |
|---|---|
| statement timeout on production reads (1, 2, 5) | 10 min per statement |
| WAL segment archived (2) | 120 s |
| restic restore (3) | 30 min |
| base backup extraction and `pg_verifybackup` (4) | 30 min each |
| recovery finished (4) | 15 min |
| each `mt data` rebuild command (6) | 60 min |
| statement timeout on scratch-server reads (5, 7) | 10 min per statement |
| `pg_ctl stop -m fast` (8, leftover sweep) | 60 s (`-t 60`) |

Adopt's and the pass's Databento calls use their existing client timeouts. The per-command bound covers anything above them.

The production advisory locks are held for steps 1–5 only (switch, restores, comparison reads). The rebuild in step 6 runs after they are released. Tick runs are manual today, so nothing is blocked.

### Realtime paths

The drill only reads, restores and rebuilds what slices 223–227 already defined: the per-unit row id, the supersession rule and the per-unit ledger grain are used as they are, not changed. Nothing here forecloses Path A or Path B.

### Patterns and Conventions
- **Script pattern:** follows `cutover_common.py` for steps, sudo, the report and exit status.
- **Reuse:** the cluster-table parser is 227's; the restore command is the runbook's two-shape form, not a new one; the fingerprint's column list comes from `storage_columns.py`.
- **Errors:** every failure names the step and the expected-against-seen values. No step is skipped silently: an unreachable production database or Databento API fails step 6 by name.

## Integration Points

### Provides to Other Slices
- **Operators:** the quarterly tick drill command and its report, next to production's drill in runbook 200.
- **The later hammerhead move:** the base + WAL restore step, run against a cluster row, is the same procedure the move uses to bring the copy up.

### Consumes from Other Slices
- 227's backups and cluster table; 226's rebuild commands; 224's list of what a rebuild loses.
- **If the production DB or the Databento API is unreachable:** step 6 fails by name. Steps 3–5 still report, so the drill shows whether the primary path works.

## Success Criteria

### Functional Requirements
1. `scripts/drill_tick_restore.py` runs on manta9000 and exits 0.
2. Its report shows:
   - the archive restored, with file count and bytes equal to the live archive's backed-up set;
   - the database restored from base + WAL, with every public table's row count and the `tick_trade` fingerprint equal to production's;
   - the database rebuilt from the restored archive, with a `tick_trade` fingerprint equal to the restored database's, and bookkeeping differences only from the expected set;
   - the rebuild time (recorded, not gated);
   - no leftovers: the drill directory and scratch server are gone.
3. A second run straight after the first also exits 0 (no state carried between runs).
4. Runbook 200's tick section has the primary restore procedure, the fallback procedure, the drill command and a drill-record row with this run's date.

### Technical Requirements
- Integration tests on the test cluster's tick database:
  - the fingerprint SQL: equal for identical data, and equal when only `unit_id` differs; different for one changed price and for one missing row;
  - the bookkeeping comparison:
    - an allowed column passes, any other column fails;
    - renumbered `request_id`/`unit_id` with the same natural keys pass;
    - a ledger row moved to a different unit fails;
    - a missing row with archived files fails.
- Unit tests:
  - leftover handling (marked directory → stopped and removed; unmarked → refuse; drill `flock` held → refuse), with stubbed `pg_ctl`;
  - the `postgresql.auto.conf` guard (an `archive` line left → refuse to start);
  - the archive-against-snapshot precondition (count, bytes or a newer file differ → refuse), with stubbed `restic ls`;
  - cleanup's path check (an unmarked path or one outside `/data/restore-test` → no `rm`), with stubbed sudo;
  - the calendar URL override carries `default_transaction_read_only=on`, and the tick URLs don't;
  - the bookkeeping table list equals the tick migration's tables;
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
   - archive file count and bytes equal to the live archive's backed-up set;
   - all table counts equal to production;
   - fingerprints equal;
   - the bookkeeping differences listed, all in the expected set;
   - the rebuild time.

3. **Confirm cleanup:**
   ```
   ls /data/restore-test/          # no 228-drill-* directory
   pgrep -u manta -a postgres      # no server with a 228-drill-* data directory
   ```

4. **Run it again:** the same command exits 0 a second time.

## Implementation Notes

### Development Approach
1. The fingerprint SQL and the bookkeeping comparison with its expected-difference constant, with their integration tests.
2. The drill directory lifecycle (flock, marker, leftover handling, cleanup), with unit tests. Confirm the socket-path URLs pass settings validation and `mt data migrate apply`.
3. Steps 0–5 (leftovers, hold, WAL switch, archive restore, base + WAL restore, compare with production), run on the host.
4. Steps 6–9 (rebuild, compare, cleanup, report), run on the host.
5. The runbook 200 sections, then the recorded drill run.

### Special Considerations
- **Disk:** the drill needs room for the archive copy and the scratch server holding both databases: about three times the tick footprint (roughly 2.5 GB today), against 1.2 TB free on `/data`. The drill checks free space before step 3, sized from the live archive and the latest base backup.
- **Production safety:** both production clusters are only read, over read-only connections (the drill's own and, by URL option, the rebuild's calendar connection). The one write is step 2's segment switch. The scratch server starts only after its `postgresql.auto.conf` is emptied and checked, and runs socket-only with `archive_mode=off`. So it can't write into the tick WAL archive or accept network connections, and the rebuild's load lands on it, not on `17/tick`.
- **Ordering:** the drill holds both tick advisory locks for steps 1–5, so no tick run can change production during the comparison.
