---
docType: tasks
slice: tick-restore-drill
project: trading-data
lld: user/slices/228-slice.tick-restore-drill.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [227]
projectState: >
  Slice 227 is merged: tick base backups, WAL and nightly metadata dumps sit
  under /data/backup/17-tick, and deploy/backup-clusters.conf has the tick row.
  Slices 223-226 provide the archive, adopt and the rebuild commands. Only one
  archive file has ever been restored. No tick database restore has been run.
  Slice design committed and re-reviewed.
dateCreated: 20261006
dateUpdated: 20261006
status: not_started
---

# Tasks: Tick Restore Drill

## Context Summary

- Working on slice 228. It adds `scripts/drill_tick_restore.py`, a reusable
  drill that proves, in one run: (1) the tick archive restores from restic,
  (2) `trading_tick` restores from base + WAL and matches production,
  (3) a database rebuilt from the *restored* archive matches the restored
  database row for row. It also writes both restore procedures and the drill
  into runbook 200.
- Delivers: the drill script and its helper modules, one `tick_trade`
  fingerprint SQL (TD3), one bookkeeping comparison with its expected-difference
  set (TD4), runbook 200 tick sections, and one recorded run on manta9000 with a
  report in `user/notes`.
- The design (`user/slices/228-slice.tick-restore-drill.md`) holds every
  decision. Read TD1-TD6, Technical Requirements and Special Considerations
  before starting. Tasks cite them rather than repeat them.
- **Production safety.** Production clusters are only read. The one write is
  step 2's `pg_switch_wal`. The scratch server starts only after its
  `postgresql.auto.conf` is emptied and checked. Destructive actions
  (`rm -rf`, `pg_ctl stop`) target only a marked `228-drill-*` directory under
  `/data/restore-test` (TD5, CLAUDE.md).
- **Module layout** (keeps each file near 300 lines). All under `scripts/`:
  `drill_228_fingerprint.py` (TD3), `drill_228_bookkeeping.py` (TD4),
  `drill_228_lifecycle.py` (lock, marker, leftovers, cleanup, TD6 bounds),
  `drill_228_host.py` (sudo, restic, pg_ctl, scratch server),
  `drill_tick_restore.py` (steps 0-9 and the report). Tests under
  `test/unit/` and `test/integration/data/`.
- **Reuse, do not rewrite:** `scripts/backup_clusters.py` (`load_clusters`),
  `scripts/cutover_common.py` (`run`, `say`, `out`, report pattern),
  `scripts/cutover_227_helpers.py` (`Step`, `StepFailed`),
  `scripts/cutover_227_host.py` (`psql()`: postgres over the local socket),
  `scripts/provision_tick_roles.sql`, `storage_columns.py`.
- **Test environment.** Export `MT_TIMESCALE_TEST_URL` from `.env` with quotes
  stripped. Run unit and integration tiers separately. Run mypy on the src
  kalshi paths and the tests in one invocation. Known pre-existing failures are
  not regressions: re-run in isolation before investigating.
- Next slice: none planned after this one in 220's tick backup chain.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 1 - Preflight facts

- [ ] **1.1 Confirm the code facts the design relies on** (effort 2)
  - [ ] Read the tick migration (`src/manta_trading/market/schema/migrations/tick.py`).
        Record the exact columns of the six bookkeeping tables
        (`tick_request`, `tick_archive_unit`, `tick_definition`,
        `tick_ingest_ledger`, `tick_dataset_edge`, `tick_day_condition`) and
        each one's primary key, natural key, and id-valued columns
        (`request_id`, `unit_id`, `superseded_by_unit_id`, `repurchase_of_unit_id`).
  - [ ] Read `cutover_common.run` and `cutover_227_host.psql`. Both use plain
        `sudo`; TD1 requires `sudo -n` after step 0. Decide how `drill_228_host`
        wraps them (a `sudo_n` argument or its own helper) without editing 227's
        scripts' behaviour.
  - [ ] Confirm the settings validation and `mt data migrate apply --track tick`
        accept a socket-style URL (`postgresql://user@/db?host=/path`). Run
        against the test cluster if needed. If either rejects it, stop and ask
        the Project Manager (TD1 step 6 assumes they accept).
  - [ ] Confirm the `restic_repo.sh` call shape used by runbook 200 for restore
        (`--env-file .env --prefix system run -- restore ...`).
  - [ ] Success: a short findings block is added to the top of the
        `drill_228_bookkeeping.py` docstring (or the 1.1 commit message) listing
        columns, keys and the socket-URL result. No code behaviour changes.

---

## Section 2 - The tick_trade fingerprint (TD3)

- [ ] **2.1 Implement the fingerprint SQL** (effort 3)
  - [ ] Create `scripts/drill_228_fingerprint.py` with one SQL constant, built
        from `storage_columns.py` (`TICK_TRADE_COLUMNS`, the BBO columns,
        `sequence_ordinal`; every `tick_trade` column except `unit_id`). Do not
        type the column list.
  - [ ] Group by `(instrument_id, UTC day of ts_event)`; per group return row
        count and the md5 of row text ordered by
        `(ts_event, sequence, sequence_ordinal)`.
  - [ ] Add `fingerprint(conn) -> dict[group, (count, md5)]` and a
        `diff_fingerprints(a, b) -> list[str]` naming each differing or missing
        group.
  - [ ] Success: importable; the column list contains no literal column name;
        `unit_id` is absent from the hashed list.
- [ ] **2.2 Integration test for the fingerprint** (effort 3)
  - [ ] `test/integration/data/test_drill_228_fingerprint.py`, on the test
        cluster's migrated tick DB (`migrated_tick_db`,
        `second_migrated_tick_db` fixtures), rows from `test/tick_support/rows.py`.
  - [ ] Cases: identical data in two DBs → equal; only `unit_id` differs →
        equal; one changed price → different; one missing row → different.
  - [ ] Add a unit test that a column added to `TICK_TRADE_COLUMNS` appears in
        the generated SQL (the "new column is hashed automatically" claim).
  - [ ] Success: new tests pass; ruff and mypy clean on touched files.
- [ ] **2.3 Commit checkpoint** - `feat: add tick_trade fingerprint for the restore drill`

---

## Section 3 - The bookkeeping comparison (TD4)

- [ ] **3.1 Define the table list and the expected-difference constants** (effort 3)
  - [ ] Create `scripts/drill_228_bookkeeping.py`. One constant lists the six
        bookkeeping tables. One constant is the set of allowed-to-differ
        `(table, column)` pairs, using the exact column names from 1.1:
        `tick_request` (`is_adopted`, estimate fields, `download_deadline`),
        `tick_archive_unit` (links, `reopened_at`, `state_changed_at`,
        `last_attempt_at`, `attempt_count`, `failure_reason`),
        `tick_dataset_edge` and `tick_day_condition` (every value column).
  - [ ] Define the "may be missing" rules as named predicates or constants:
        requests without `provider_job_id`; rows for paid jobs with no archived
        files; units with no archived file in any state.
  - [ ] Success: each value defined once; no `(table, column)` literal appears
        outside the constant.
- [ ] **3.2 Implement natural-key matching and the comparison** (effort 5)
  - [ ] Match requests by `provider_job_id`, units by
        `(provider_job_id, unit_date)`; never by `request_id` or `unit_id`.
  - [ ] Compare every id-valued column through the restored→rebuilt mapping:
        definition's unit, ledger row's unit, unit supersession and repurchase
        links.
  - [ ] A rebuilt request without a job id fails. Missing rows outside the
        allowed rules fail; a unit with a file whose state differs fails.
  - [ ] `tick_dataset_edge` / `tick_day_condition`: values may differ, but every
        restored `(dataset, condition_date)` must exist in the rebuild.
  - [ ] Return a result object: failures (list of strings) plus the allowed
        differences found (counted per table/column) for the report, and the
        count of allowed-missing requests.
  - [ ] Success: function takes two connections; no SQL writes.
- [ ] **3.3 Add the primary-path md5 for restored-vs-production** (effort 2)
  - [ ] Add `table_md5(conn, table)`: md5 of the table's rows in primary-key
        order, for step 5 (exact, ids kept). Reuse the table list from 3.1.
  - [ ] Success: one function serves all six tables; the list comes from 3.1.
- [ ] **3.4 Integration tests for the bookkeeping comparison** (effort 4)
  - [ ] `test/integration/data/test_drill_228_bookkeeping.py`, two migrated tick
        DBs.
  - [ ] Cases: an allowed column differing passes; any other column differing
        fails; renumbered `request_id`/`unit_id` with equal natural keys pass;
        a ledger row moved to a different unit fails; a missing row whose unit
        has an archived file fails; a request with no job id missing from the
        rebuild passes and is counted; a rebuilt request with no job id fails;
        a restored condition row absent from the rebuild fails.
  - [ ] Success: all pass; `table_md5` equal for identical tables and different
        after one changed value.
- [ ] **3.5 Unit test: the table list equals the migration's tables** (effort 2)
  - [ ] Test that the 3.1 list plus `tick_trade` equals the set of tables the
        tick migration creates (read from the migration module or the migrated
        test DB's `public` tables). A new table must fail this test.
  - [ ] Success: passes now; fails when a table is added to the migration
        without the list (verify once by hand, do not commit the breakage).
- [ ] **3.6 Commit checkpoint** - `feat: add bookkeeping comparison for the restore drill`

---

## Section 4 - Drill lifecycle: lock, marker, leftovers, cleanup, bounds (TD1 step 0/8, TD5, TD6)

- [ ] **4.1 Time bounds and shared constants** (effort 1)
  - [ ] Create `scripts/drill_228_lifecycle.py`. Define the TD6 table once as
        named constants (statement timeouts, WAL wait 120 s, restic 30 min,
        base extraction and `pg_verifybackup` 30 min each, recovery 15 min,
        each `mt data` command 60 min, `pg_ctl stop -t 60`).
  - [ ] Define the drill root (`/data/restore-test`), directory prefix
        (`228-drill-`), marker name (`drill-228.json`), lock path
        (`/data/restore-test/.228-drill.lock`).
  - [ ] Success: no bound or path literal appears anywhere else in the slice's
        code (grep to confirm at the end of Section 6).
- [ ] **4.2 Implement the drill flock and the marker** (effort 2)
  - [ ] `acquire_drill_lock()`: `flock` held for the whole run; refuse if held
        by another drill, naming it.
  - [ ] `create_drill_dir(stamp)`: makes the directory and writes the marker
        (stamp, pid) *first*, before anything else is placed in it.
  - [ ] Success: a second acquire in the same test refuses.
- [ ] **4.3 Implement the path check and cleanup** (effort 3)
  - [ ] `assert_drill_path(path)`: path must be a direct child of
        `/data/restore-test`, named `228-drill-*`, and contain the marker.
        Resolve symlinks first. Anything else raises by name.
  - [ ] `remove_drill_dir(path)`: runs the check, then `sudo -n rm -rf`. If
        sudo is unavailable, fails by name and leaves the path for the report.
  - [ ] `stop_scratch_server(path)`: `pg_ctl stop -m fast -t 60` as manta when a
        server is running from that data directory.
  - [ ] Success: unmarked path, outside-root path, and a symlink escaping the
        root all raise before any `rm` call.
- [ ] **4.4 Implement the startup leftover sweep** (effort 3)
  - [ ] For each `/data/restore-test/228-drill-*`: marked → stop its server if
        running, then `remove_drill_dir`; unmarked → refuse, naming the path.
  - [ ] Runs only under the drill flock.
  - [ ] Success: matches TD5 exactly.
- [ ] **4.5 Unit tests for lifecycle** (effort 3)
  - [ ] `test/unit/test_drill_228_lifecycle.py`, with `pg_ctl` and `sudo`
        stubbed (record calls, no real `rm`). Use `tmp_path` for the root.
  - [ ] Cases: marked leftover → stopped then removed; unmarked → refuse, no
        `rm`; flock held → refuse; `assert_drill_path` rejects unmarked,
        outside-root, and symlink-escape paths with no `rm` issued.
  - [ ] Success: all pass; ruff and mypy clean.
- [ ] **4.6 Commit checkpoint** - `feat: add restore drill lifecycle with marked cleanup`

---

## Section 5 - Host primitives: sudo, restic, scratch server (TD1 steps 2-4, 6)

- [ ] **5.1 Implement the sudo wrapper and the `sudo -v` prompt** (effort 2)
  - [ ] Create `scripts/drill_228_host.py`. `prime_sudo()` runs `sudo -v` once;
        every later root call uses `sudo -n` and, on failure, raises a named
        "sudo timestamp expired" error instead of waiting on a prompt.
  - [ ] Reuse `cutover_common.run` / `cutover_227_host.psql` per the 1.1 decision.
  - [ ] Success: a stubbed `sudo -n` returning exit 1 yields the named error.
- [ ] **5.2 Implement the archive-against-snapshot check** (effort 3)
  - [ ] Pure function comparing the live archive (file count, total bytes,
        newest mtime) with `restic ls latest` output for `/data/tick-archive`
        (count, bytes, snapshot time). Refuse with
        "archive changed since snapshot <time>; run the restic backup first"
        when count or bytes differ or any live file is newer than the snapshot.
        Parse `restic ls` leniently (JSON output if available, else tolerate
        whitespace variation).
  - [ ] Success: stubbed listings cover equal, count differs, bytes differ,
        newer file; only equal passes.
- [ ] **5.3 Unit test for 5.2 and the sudo wrapper** (effort 2)
  - [ ] `test/unit/test_drill_228_host.py`. Include a fixture built from real
        `restic ls` output captured on the host (record a few lines from
        `deploy/lib/restic_repo.sh --env-file .env --prefix system run -- ls
        latest --json /data/tick-archive` into `test/fixtures`), per CLAUDE.md's
        parser-fixture rule.
  - [ ] Success: tests pass using the real-format fixture.
- [ ] **5.4 Implement the `postgresql.auto.conf` guard** (effort 2)
  - [ ] `empty_auto_conf(datadir)`: truncate the file, then verify no line
        contains `archive` (Step 6's `grep -c archive` = 0 check). If the
        truncate or the check fails, raise and never start the server.
  - [ ] Success: a file containing `archive_command` is truncated and passes;
        a stub that leaves a line behind raises.
- [ ] **5.5 Implement the scratch server config writer** (effort 4)
  - [ ] `write_scratch_conf(datadir, sockdir, prod_settings, wal_dir)`:
        writes `postgresql.conf` and `pg_hba.conf` per TD1 step 4:
        `listen_addresses=''`, socket dir in the drill dir (mode 0700),
        `local all all trust`, `archive_mode=off`,
        `shared_preload_libraries=timescaledb`,
        `timescaledb.max_background_workers=0`, the two-shape `restore_command`
        from runbook 200 against the tick WAL dir.
  - [ ] `max_worker_processes`, `max_locks_per_transaction`, `max_connections`
        come from `prod_settings` (read from `17/tick` in step 1), never typed.
        A missing setting raises.
  - [ ] Create `recovery.signal`.
  - [ ] Success: output contains every item above; no numeric default for the
        three production settings exists in the code.
- [ ] **5.6 Unit tests for 5.4 and 5.5** (effort 2)
  - [ ] Add to `test_drill_228_host.py`: auto.conf guard cases; generated
        config contains the required lines and the supplied production values;
        a missing production setting raises; `recovery.signal` exists;
        the `restore_command` handles `.zst` and raw.
  - [ ] Success: pass; ruff and mypy clean.
- [ ] **5.7 Implement scratch server start/wait/stop and log checks** (effort 4)
  - [ ] `start_scratch(datadir)`: `pg_ctl start` as manta; wait up to the
        recovery bound for `pg_is_in_recovery()` false; if the server process
        exits during the wait, fail with the log tail.
  - [ ] `last_restored_segment(logfile)`: parse the server log leniently (any
        whitespace, quoting variation). `check_reached(seen, needed)` fails with
        "recovery stopped early at <segment>" when seen < needed (WAL names
        compare lexically within a timeline; use `wal_segment_name.py` helpers
        if they apply).
  - [ ] `create_drill_database()`: runs `provision_tick_roles.sql -v
        tick_db=trading_tick_drill` on the scratch server.
  - [ ] Success: unit tests cover the log parser on a real recovery log excerpt
        (captured during Section 7) and the segment comparison.
- [ ] **5.8 Commit checkpoint** - `feat: add restore drill host primitives`

---

## Section 6 - The drill entry point, steps 0-5 (primary path)

- [ ] **6.1 Skeleton: arguments, step runner, report model** (effort 3)
  - [ ] Create `scripts/drill_tick_restore.py` following the
        `cutover_common` / `cutover_227_tick_backup.py` pattern: `Step` records
        with expected vs seen, narration via `say`, report accumulated in
        memory, a top-level handler at the process boundary that writes the
        report even on failure, exit 0 only when every check passes.
  - [ ] Read paths from `load_clusters()` (tick row): backup root, WAL dir, base
        dir. No tick path literal.
  - [ ] Success: `--help` works; running with a stubbed step list produces a
        report file and the right exit status.
- [ ] **6.2 Unit tests: report and exit status; URL environments** (effort 3)
  - [ ] `test/unit/test_drill_tick_restore.py`: exit status 0 only when all
        steps pass, non-zero (and a report written) on any failure.
  - [ ] Add `rebuild_env()`: returns the subprocess environment for step 6:
        tick URLs pointing at the scratch socket and drill database,
        `MT_TICK_ARCHIVE_DIR` at the restored archive, and `MT_TIMESCALE_DB_URL`
        = `.env`'s value plus `options=-c default_transaction_read_only=on`.
        Never edits `.env`.
  - [ ] Test: the calendar URL carries `default_transaction_read_only=on`; the
        tick URLs do not.
  - [ ] Success: tests pass.
- [ ] **6.3 Steps 0-1: prepare and hold** (effort 4)
  - [ ] Step 0: drill flock, `sudo -v`, leftover sweep, free-space check sized
        from the live archive and the latest base backup (about 3x the tick
        footprint; fail by name with expected and seen bytes).
  - [ ] Step 1: connect with `MT_TICK_MAINTENANCE_URL` read-only
        (`default_transaction_read_only=on`, TD6 statement timeout); take the
        tick advisory lock (refuse if a tick run holds it; keys in
        `data/tick/constants.py`); `has_table_privilege(..., 'SELECT')` on every
        public table; read `max_worker_processes`,
        `max_locks_per_transaction`, `max_connections`; run the 5.2 archive vs
        snapshot check.
  - [ ] Success: each check prints expected against seen.
- [ ] **6.4 Steps 2-3: switch WAL, restore the archive** (effort 4)
  - [ ] Step 2: `SELECT pg_walfile_name(pg_switch_wal())` through
        `cutover_227_host.psql` (postgres over the socket); wait up to the TD6
        bound for that segment's `.zst` or raw file in the tick WAL dir.
  - [ ] Step 3: restic `restore latest --include /data/tick-archive` into the
        drill dir (bounded), `assert_drill_path` then `sudo -n chown -R
        manta:manta` on the restored directory only; compare file count and
        bytes with the live archive.
  - [ ] Success: counts and bytes equal the live archive.
- [ ] **6.5 Step 4: restore the database** (effort 4)
  - [ ] Unpack the latest tick base backup into the drill dir; run
        `pg_verifybackup` (both bounded); `empty_auto_conf`; `write_scratch_conf`;
        `start_scratch`; `check_reached` against step 2's segment.
  - [ ] Success: scratch server is out of recovery and reached the target.
- [ ] **6.6 Step 5: compare restored with production** (effort 3)
  - [ ] Exact row counts for every public table; `fingerprint` (2.1) on both
        sides with `diff_fingerprints`; `table_md5` (3.3) for each bookkeeping
        table.
  - [ ] Then confirm the advisory lock is still held on the lock connection's
        own backend (`pg_locks`); any error on that connection or a missing lock
        fails the step. Release the lock; production is not touched afterwards.
  - [ ] Success: all equal, or the step fails naming the first difference.
- [ ] **6.7 Run steps 0-5 on manta9000** (effort 3)
  - [ ] Run the script with steps 6-9 not yet wired (a `--through-step 5` style
        option is acceptable if it is removed or kept as documented in
        `--help`). Cleanup (4.3) must still run.
  - [ ] Success: steps 0-5 pass on the host; no `228-drill-*` directory and no
        scratch server left; the log excerpt from recovery is saved as the
        fixture for 5.7's parser test, which is completed now.
  - [ ] If a step fails, get the actual error text before any fix (CLAUDE.md);
        ask the Project Manager if it cannot be obtained.
- [ ] **6.8 Commit checkpoint** - `feat: add tick restore drill steps 0-5`

---

## Section 7 - Steps 6-9 (fallback path, cleanup, report)

- [ ] **7.1 Step 6: rebuild into `trading_tick_drill`** (effort 4)
  - [ ] `create_drill_database()` (5.7), then with `rebuild_env()` run, each
        bounded: `mt data migrate apply --track tick`; `mt data tick adopt
        --job-id <dir> --source <restored dir>` once per restored job
        directory; `mt data tick pass --estimate-only`; `mt data tick ingest`.
        Use `uv run mt ...` from the checkout root.
  - [ ] Record the rebuild time in the report, not gated.
  - [ ] If production DB or the Databento API is unreachable, this step fails by
        name; steps 3-5 results stay in the report.
  - [ ] Success: all four commands exit 0 against the scratch server.
- [ ] **7.2 Step 7: compare the rebuild with the restore** (effort 3)
  - [ ] Both on the scratch server (statement timeout per TD6): `fingerprint`
        equality, then the TD4 comparison. List allowed differences and the
        allowed-missing request count in the report.
  - [ ] Success: failures are named by table, column and natural key.
- [ ] **7.3 Steps 8-9: cleanup in `finally`, report** (effort 3)
  - [ ] Cleanup always runs (stop server, `remove_drill_dir`). A cleanup failure
        puts the path in the report and fails the run.
  - [ ] Report to `project-documents/user/notes/<date>-228-tick-restore-drill.md`
        with front matter per `file-naming-conventions.md`: every step's
        expected and seen values, timings, bookkeeping differences. Final line
        `PASS: archive, database, fallback` only when every check passed.
  - [ ] Success: unit test (extend 6.2's file) proves the report is written on a
        failing step and cleanup runs when a step raises.
- [ ] **7.4 Run the full drill on manta9000** (effort 3)
  - [ ] Run `uv run python scripts/drill_tick_restore.py`. Keep the printed
        output with the report.
  - [ ] Success: exit 0; report shows every Functional Requirement 2 item
        (archive equal, all table counts and fingerprint equal, rebuilt
        fingerprint equal, allowed differences only, rebuild time, no leftovers).
  - [ ] On any unexpected difference, diagnose from the actual values before
        changing the allowed set. Widening TD4's set is a design change: ask the
        Project Manager.
- [ ] **7.5 Run it a second time** (effort 1)
  - [ ] Success: exit 0 straight after the first run (no carried state);
        `ls /data/restore-test/` shows no `228-drill-*`; `pgrep -u manta -a
        postgres` shows no scratch server.
- [ ] **7.6 Commit checkpoint** - `feat: complete tick restore drill steps 6-9`

---

## Section 8 - Runbook 200

- [ ] **8.1 Primary restore procedure** (effort 3)
  - [ ] In runbook 200's tick section (`### The tick cluster (slice 227)` area),
        add: base + WAL into a fresh data directory (the steps the drill
        automates, including emptying `postgresql.auto.conf` and the settings
        that must match the primary), and putting a restored cluster back into
        service as `17/tick`.
  - [ ] Verify the "back into service" procedure against the system's facts
        (Debian cluster layout under `/etc/postgresql`, `postgresql@17-tick`
        unit). If it cannot be verified without touching production, say so in
        the runbook and in the task notes; do not invent steps.
  - [ ] Success: a reader can follow it without the drill script.
- [ ] **8.2 Fallback procedure** (effort 2)
  - [ ] Add the restic archive restore and the four rebuild commands (as in
        TD1 step 6), with what a rebuild loses (link to the 224 design's list).
        Replace runbook 200's line "The full archive-and-database drill is
        slice 227's" with the correct slice (228).
  - [ ] Success: both procedures carry commands that match the drill's.
- [ ] **8.3 Drill section and record row** (effort 2)
  - [ ] Add the drill command, what it proves (archive, database, fallback),
        its sudo prompt, and the quarterly repeat alongside production's Step 6
        (next due 2026-11-17).
  - [ ] Add a Drill record row with this run's date, duration, and outcome from
        7.4/7.5, citing the report path.
  - [ ] Success: runbook front matter `dateUpdated` bumped; links resolve.
- [ ] **8.4 Commit checkpoint** - `docs: add tick restore procedures and drill to runbook 200`

---

## Section 9 - Final validation

- [ ] **9.1 Quality gates** (effort 2)
  - [ ] Run ruff (scoped to touched files; `git diff main` shows no unrelated
        deletions after format) and mypy (src kalshi paths and tests in one
        invocation) on touched files.
  - [ ] Run the new unit and integration tests, tiers separately.
  - [ ] Grep to confirm: no TD6 bound or drill path literal outside
        `drill_228_lifecycle.py`; no cluster path literal outside
        `backup-clusters.conf`; no credential in any new file.
  - [ ] Success: all clean; any pre-existing failure matches the known list.
- [ ] **9.2 Walk the design's Success Criteria** (effort 2)
  - [ ] Check each Functional and Technical Requirement against the report and
        tests; list any gap.
  - [ ] Update the slice design's `status` and `dateUpdated`; add real output to
        its Verification Walkthrough where Phase 6 refined it.
  - [ ] Success: every criterion maps to a passing test or the recorded run.
- [ ] **9.3 Commit checkpoint** - `docs: update slice 228 verification and status`
