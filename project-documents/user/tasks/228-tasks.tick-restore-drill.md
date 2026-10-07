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
dateUpdated: 20261007
status: in_progress
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
  stripped. Run unit and integration tiers separately. Known pre-existing
  failures are not regressions: re-run in isolation before investigating.
- **Scope.** The drill is a manual quarterly script: no load test and no CI
  gating task is needed. Its checks are the unit and integration tests below
  plus the recorded host run.
- Next slice: none planned after this one in 220's tick backup chain.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 1 - Preflight facts

- [x] **1.1 Confirm the code facts the design relies on** (effort 2)
  - [x] Read the tick migration (`src/manta_trading/market/schema/migrations/tick.py`).
        Record the exact columns of the six bookkeeping tables
        (`tick_request`, `tick_archive_unit`, `tick_definition`,
        `tick_ingest_ledger`, `tick_dataset_edge`, `tick_day_condition`) and
        each one's primary key, natural key, and id-valued columns
        (`request_id`, `unit_id`, `superseded_by_unit_id`, `repurchase_of_unit_id`).
  - [x] Read `cutover_common.run` and `cutover_227_host.psql`. Both use plain
        `sudo`; TD1 requires `sudo -n` after step 0. Decide how `drill_228_host`
        wraps them (a `sudo_n` argument or its own helper) without editing 227's
        scripts' behaviour.
  - [x] Confirm the settings validation and `mt data migrate apply --track tick`
        accept a socket-style URL (`postgresql://user@/db?host=/path`). Run
        against the test cluster if needed. If either rejects it, stop and ask
        the Project Manager (TD1 step 6 assumes they accept).
  - [x] Confirm where the live archive path comes from: `MT_TICK_ARCHIVE_DIR`
        (`TICK_ARCHIVE_DIR_ENV` in `data/tick/constants.py`) read from `.env`
        the way `cutover_227_host.env_value` and
        `verify_tick_archive_backup.sh` do. `backup-clusters.conf` holds only
        the backup root, WAL and base directories, not the archive.
  - [x] Confirm the tick locks match TD1 step 1: `TICK_ACQUISITION_LOCK_KEY`
        (adopt, reset, pass) and `TICK_INGEST_LOCK_KEY` (ingest), both taken
        through `cutover_227_host.TICK_LOCK_KEYS` (6.3).
  - [x] Confirm the restic call shape used by runbook 200 for restore
        (`--env-file .env --prefix system run -- restore ...`).
  - [x] Write the findings into this task file as a `Findings (1.1)` block
        directly under this task: columns, keys, id-valued columns, the sudo
        wrapper decision, the socket-URL result. Section 3 reads it from here.
  - [x] Success: the findings block exists in this file; no code changes.

  **Findings (1.1)** (2026-10-07; source `migrations/tick.py` tick_002-tick_007)

  | Table | Primary key | Natural key | Id-valued columns |
  |---|---|---|---|
  | `tick_request` | `request_id` (identity) | `provider_job_id` (UNIQUE, nullable) | `request_id` |
  | `tick_archive_unit` | `unit_id` (identity) | `(provider_job_id via request_id, unit_date)`; UNIQUE `(request_id, unit_date)` | `unit_id`, `request_id`, `superseded_by_unit_id`, `repurchase_of_unit_id` |
  | `tick_definition` | `(instrument_id, activation_ns)` | same as PK | `unit_id` |
  | `tick_ingest_ledger` | `(unit_id, instrument_id, session_date)` | `(unit natural key, instrument_id, session_date)` | `unit_id` |
  | `tick_dataset_edge` | `dataset` | same as PK | none |
  | `tick_day_condition` | `(dataset, condition_date)` | same as PK | none |

  Columns:
  - `tick_request`: request_id, dataset, schema, symbols, stype_in, range_start,
    range_end, delivery_mode, is_adopted, provider_job_id, estimated_cost_usd,
    actual_cost_usd, provider_record_count, billed_size_bytes, requested_at,
    committed_at, download_deadline.
  - `tick_archive_unit`: unit_id, request_id, unit_date, state, state_changed_at,
    fetch_status, failure_reason, attempt_count, last_attempt_at, file_path,
    file_size_bytes, file_sha256, provider_record_count, decoded_record_count,
    superseded_by_unit_id, repurchase_of_unit_id, reopened_at (tick_006).
  - `tick_definition`: instrument_id, activation_ns, expiration_ns, raw_symbol,
    asset, exchange, instrument_class, security_type, cfi, currency,
    min_price_increment, display_factor, unit_of_measure, unit_of_measure_qty,
    contract_multiplier, ts_recv_ns, unit_id.
  - `tick_ingest_ledger`: unit_id, instrument_id, calendar_id, session_date,
    record_count, volume, first_event_ns, last_event_ns.
  - `tick_dataset_edge`: dataset, available_start, available_end, observed_at.
  - `tick_day_condition`: dataset, condition_date, condition,
    last_modified_date, observed_at.
  - `public` tables after migrating: the six above, `tick_trade`, and the
    runner's `schema_migrations` (8 total, confirmed on a scratch server).

  Request columns, adopt vs pass (`manifest_repo.py`, `manifest_pass.py`):
  adopt writes `estimated = actual = cost`, `requested_at = committed_at =
  ts_received`, no `download_deadline`. The pass writes `requested_at =
  run.clock()` at planning and `committed_at = ts_received` at submit. So
  `requested_at` differs and `committed_at` matches. **PM decision 2026-10-07:**
  `requested_at` joins TD4's allowed set (slice design updated);
  `committed_at` does not. Production today: 6 requests, all adopted; 156
  units, all `ingested`/`UNKNOWN`, none reopened, superseded or repurchased.

  Sudo wrapper: `cutover_common.run(sudo=True)` and `cutover_227_host.psql`
  prepend plain `sudo`. `drill_228_host` gets its own `sudo_n(args)` helper
  (prepends `sudo -n`, maps exit 1 + "a password is required" to the named
  "sudo timestamp expired" error) and its own `postgres_value(sql)`, built
  the same way as `psql()` but through `sudo_n`. 227's scripts are not edited.

  Socket URL: proven on a throwaway socket-only PG 17.11 + TimescaleDB
  server. `mt data migrate apply --track tick` with
  `MT_TICK_MAINTENANCE_URL=postgresql://manta@/<db>?host=<sockdir>` applied
  all 7 tick migrations. Settings take tick URLs as plain `str` (no host
  validation); env vars override `.env`. `options=-c%20default_transaction_read_only%3Don`
  in a URL gives `default_transaction_read_only=on`, and a write fails with
  `ReadOnlySqlTransaction`. **Unix socket paths cap at 107 bytes**:
  `<sockdir>/.s.PGSQL.5432` must fit, and `/data/restore-test/228-drill-<stamp>/sock`
  does.

  Paths and backup layout:
  - Archive: `MT_TICK_ARCHIVE_DIR` (`TICK_ARCHIVE_DIR_ENV`), read with
    `cutover_227_host.env_value(.env, key)`. Today `/data/tick-archive`, 500 MB.
  - From the cluster row's `backup_root` (`/data/backup/17-tick`): WAL is
    `<root>/wal` and base backups are `<root>/base/<YYYYMMDD>/` holding
    `base.tar.gz`, `pg_wal.tar.gz` and `backup_manifest` (setup-backup.sh
    `WAL_DIR="$ROOT/wal"`, `CLUSTER_SUBDIRS=(base metadata)`).
  - Restoring the base: extract `base.tar.gz` into pgdata and `pg_wal.tar.gz`
    into `pgdata/pg_wal`, then `pg_verifybackup -m <base>/backup_manifest
    <pgdata>` (PG 17 verifies plain format only; `backup_prod.sh` does the same).
  - `/data` is manta-owned. `/data/restore-test` does not exist yet, so step 0
    creates it.

  Locks: `TICK_ACQUISITION_LOCK_KEY = 220_000_001`, `TICK_INGEST_LOCK_KEY =
  220_000_002` (`data/tick/constants.py`), the tuple is
  `cutover_227_host.TICK_LOCK_KEYS`. The `pg_locks` probe shape
  (`classid = 0 AND objsubid = 1 AND objid IN (...)`) is in `tick_locks_held()`.

  Restic: `deploy/lib/restic_repo.sh --env-file .env --prefix system run --
  <restic args>`, run as root. Listing: `ls --json --recursive latest <archive>`
  (without `--recursive` a directory filter lists only direct children).
  Restore: `restore latest --target <dir> --include <archive>`.

- [x] **1.2 Commit checkpoint** - `docs: record slice 228 preflight findings`

---

## Section 2 - The tick_trade fingerprint (TD3)

- [x] **2.1 Implement the fingerprint SQL** (effort 3)
  - [x] Create `scripts/drill_228_fingerprint.py` with one SQL constant, built
        from `storage_columns.py`: every key of `TICK_TRADE_COLUMNS` (it already
        includes the BBO columns) plus `sequence_ordinal`; every `tick_trade`
        column except `unit_id`. Do not type the column list.
  - [x] Group by `(instrument_id, UTC day of ts_event)`; per group return row
        count and the md5 of row text ordered by
        `(ts_event, sequence, sequence_ordinal)`.
  - [x] Add `fingerprint(conn) -> dict[group, (count, md5)]` and a
        `diff_fingerprints(a, b) -> list[str]` naming each differing or missing
        group.
  - [x] Success: importable; the column list contains no literal column name;
        `unit_id` is absent from the hashed list.
- [x] **2.2 Integration test for the fingerprint** (effort 3)
  - [x] `test/integration/data/test_drill_228_fingerprint.py`, on the test
        cluster's migrated tick DB (`migrated_tick_db`,
        `second_migrated_tick_db` fixtures), rows from `test/tick_support/rows.py`.
  - [x] Cases: identical data in two DBs → equal; only `unit_id` differs →
        equal; one changed price → different; one missing row → different.
  - [x] Add a unit test that a column added to `TICK_TRADE_COLUMNS` appears in
        the generated SQL (the "new column is hashed automatically" claim).
  - [x] Success: new tests pass; ruff and mypy clean on touched files.
- [x] **2.3 Commit checkpoint** - `feat: add tick_trade fingerprint for the restore drill`

---

## Section 3 - The bookkeeping comparison (TD4)

- [x] **3.1 Define the table list and the expected-difference constants** (effort 3)
  - [x] Create `scripts/drill_228_bookkeeping.py`. One constant lists the six
        bookkeeping tables. One constant is the set of allowed-to-differ
        `(table, column)` pairs, using the exact column names from 1.1:
        `tick_request` (`is_adopted`, estimate fields, `download_deadline`),
        `tick_archive_unit` (links, `reopened_at`, `state_changed_at`,
        `last_attempt_at`, `attempt_count`, `failure_reason`),
        `tick_dataset_edge` and `tick_day_condition` (every value column).
  - [x] Define the "may be missing" rules as named predicates or constants:
        requests without `provider_job_id`; rows for paid jobs with no archived
        files; units with no archived file in any state.
  - [x] Success: each value defined once; no `(table, column)` literal appears
        outside the constant.
- [x] **3.2 Natural-key id mapping and row comparison** (effort 4)
  - [x] Match requests by `provider_job_id`, units by
        `(provider_job_id, unit_date)`; never by `request_id` or `unit_id`.
        Build the restored→rebuilt `request_id` and `unit_id` maps; requests
        without a job id are returned separately, not mapped.
  - [x] Compare each bookkeeping table's rows, translating every id-valued
        column through the maps: definition's unit, ledger row's unit, unit
        supersession and repurchase links. Columns in the allowed set (3.1) are
        skipped; any other differing column is a failure.
  - [x] Success: failures are strings naming table, column and natural key; no
        SQL writes.
- [x] **3.3 Integration tests for 3.2** (effort 3)
  - [x] `test/integration/data/test_drill_228_bookkeeping.py`, two migrated tick
        DBs (`migrated_tick_db`, `second_migrated_tick_db`).
  - [x] Cases: an allowed column differing passes; any other column differing
        fails; renumbered `request_id`/`unit_id` with equal natural keys pass;
        a ledger row moved to a different unit fails.
  - [x] Success: all pass; ruff and mypy clean on touched files.
- [x] **3.4 Missing-row rules, condition coverage, result object** (effort 4)
  - [x] A rebuilt request without a job id fails. Rows missing from the rebuild
        fail unless an allowed-missing rule from 3.1 applies, by name; a unit
        with a file whose state differs fails.
  - [x] `tick_dataset_edge` / `tick_day_condition`: values may differ, but every
        restored `(dataset, condition_date)` must exist in the rebuild.
  - [x] Return a result object: failures, the allowed differences found
        (counted per table/column), and the count of allowed-missing requests,
        for the report. `compare_bookkeeping(restored_conn, rebuilt_conn)` is
        the one entry point, combining 3.2 and this task.
  - [x] Success: each rule from 3.1 is applied by name, none re-typed here.
- [x] **3.5 Integration tests for 3.4** (effort 3)
  - [x] Add to `test_drill_228_bookkeeping.py`: a missing row whose unit has an
        archived file fails; a request with no job id missing from the rebuild
        passes and is counted; a rebuilt request with no job id fails; a
        restored condition row absent from the rebuild fails; a missing
        no-file unit (e.g. `RETRY_EXHAUSTED`) passes.
  - [x] Success: all pass.
- [x] **3.6 Primary-path table md5** (effort 2)
  - [x] Add `table_md5(conn, table)`: md5 of the table's rows in primary-key
        order, for step 5 (exact, ids kept). Reuse the table list from 3.1.
  - [x] Success: one function serves all six tables; the list comes from 3.1.
- [x] **3.7 Integration test for 3.6** (effort 1)
  - [x] `table_md5` equal for identical tables; different after one changed
        value; different after one missing row.
  - [x] Success: pass.
- [x] **3.8 Pin tests against the migration** (effort 3)
  - [x] Table list: the 3.1 list plus `tick_trade` equals the set of `public`
        tables in the migrated test DB (read from `information_schema.tables`),
        **excluding the migration runner's tracking table `schema_migrations`**
        (name defined once in the test; the runner also creates it). The
        migration module holds raw SQL strings, so it can't be read for table
        names. A new table must fail this test.
  - [x] Allowed set: every `(table, column)` in the 3.1 allowed-difference set
        exists in the migrated DB's `information_schema.columns`, and every
        value column of `tick_dataset_edge` and `tick_day_condition` is in the
        set. A renamed or added column must fail.
  - [x] Both need the test cluster, so they live under `test/integration/data/`.
  - [x] Success: pass now; each fails when a table or column is added to the
        migration without the constants (verify once by hand, do not commit the
        breakage).
- [ ] **3.9 Commit checkpoint** - `feat: add bookkeeping comparison for the restore drill`

---

## Section 4 - Drill lifecycle: lock, marker, leftovers, cleanup, bounds (TD1 step 0/8, TD5, TD6)

- [ ] **4.1 Time bounds and shared constants** (effort 1)
  - [ ] Create `scripts/drill_228_lifecycle.py`. Define the TD6 table once as
        named constants (statement timeouts, WAL wait 120 s, restic 30 min,
        base extraction and `pg_verifybackup` 30 min each, recovery 15 min,
        each `mt data` command 60 min, `pg_ctl stop -t 60`).
  - [ ] Define the drill root (`/data/restore-test`), directory prefix
        (`228-drill-`), marker name (`drill-228.json`), lock path
        (`/data/restore-test/.228-drill.lock`), the PostgreSQL binary directory
        (`/usr/lib/postgresql/17/bin`, for `pg_ctl` and `pg_verifybackup`), and
        the report directory (`project-documents/user/notes`).
  - [ ] The live archive path is **not** a constant: it is `MT_TICK_ARCHIVE_DIR`
        from `.env` (1.1), and every later task uses that value, including for
        restic's `--include` and `ls` path.
  - [ ] Success: no bound, drill path, binary directory or report directory
        literal appears anywhere else in the slice's code (grep in 9.1).
- [ ] **4.2 Implement the drill flock and the marker** (effort 2)
  - [ ] `acquire_drill_lock()`: `flock` held for the whole run; refuse if held
        by another drill, naming it.
  - [ ] `create_drill_dir(stamp)`: makes the directory and writes the marker
        (stamp, pid) *first*, before anything else is placed in it.
  - [ ] Success: importable; the marker is the first file written.
- [ ] **4.3 Unit tests for 4.2** (effort 2)
  - [ ] `test/unit/test_drill_228_lifecycle.py`, `tmp_path` as the root. Cases:
        a second acquire refuses; the marker exists with stamp and pid.
  - [ ] Success: pass.
- [ ] **4.4 Implement the path check and cleanup** (effort 3)
  - [ ] `assert_drill_path(path)`: resolve symlinks first. The resolved path
        must be a drill directory (a direct child of the drill root, named with
        the drill prefix, containing the marker) **or lie inside one**. Anything
        else raises by name. This is the check for `chown` of the restored
        archive, which sits inside the drill directory.
  - [ ] `remove_drill_dir(path)`: requires `path` to be the drill directory
        itself (stricter than the check above: a subdirectory is refused), then
        `sudo -n rm -rf`. If sudo is unavailable, fails by name and leaves the
        path for the report.
  - [ ] `stop_scratch_server(path)`: `pg_ctl stop -m fast -t 60` as manta when a
        server is running from that data directory.
  - [ ] Success: unmarked path, outside-root path, a `..` escape and a symlink
        escaping the root all raise before any `rm` call; a path inside a
        marked drill directory passes the check but is refused by removal.
- [ ] **4.5 Unit tests for 4.4** (effort 2)
  - [ ] Add to `test_drill_228_lifecycle.py`, with `pg_ctl` and `sudo` stubbed
        (record calls, no real `rm`): `assert_drill_path` rejects unmarked,
        outside-root, `..`-escape and symlink-escape paths with no `rm`
        issued, and accepts a subdirectory of a marked drill directory;
        `remove_drill_dir` refuses that subdirectory; a marked drill directory
        is stopped then removed.
  - [ ] Success: pass.
- [ ] **4.6 Implement the startup leftover sweep** (effort 3)
  - [ ] For each `/data/restore-test/228-drill-*`: marked → stop its server if
        running, then `remove_drill_dir`; unmarked → refuse, naming the path.
  - [ ] Runs only under the drill flock. Matches TD5 exactly.
  - [ ] Success: importable; uses 4.4's functions only.
- [ ] **4.7 Unit tests for 4.6** (effort 2)
  - [ ] Cases: marked leftover → stopped then removed; unmarked → refuse, no
        `rm`; sweep without the flock held → refuse.
  - [ ] Success: pass; ruff and mypy clean on touched files.
- [ ] **4.8 Commit checkpoint** - `feat: add restore drill lifecycle with marked cleanup`

---

## Section 5 - Host primitives: sudo, restic, scratch server (TD1 steps 2-4, 6)

- [ ] **5.1 Implement the sudo wrapper and the `sudo -v` prompt** (effort 2)
  - [ ] Create `scripts/drill_228_host.py`. `prime_sudo()` runs `sudo -v` once;
        every later root call uses `sudo -n` and, on failure, raises a named
        "sudo timestamp expired" error instead of waiting on a prompt.
  - [ ] Reuse `cutover_common.run` / `cutover_227_host.psql` per the 1.1 decision.
  - [ ] Success: a stubbed `sudo -n` returning exit 1 yields the named error.
- [ ] **5.2 Implement the archive-against-snapshot check** (effort 3)
  - [ ] Pure function comparing the live archive's backed-up set (TD1 step 3:
        the live archive minus the patterns read from `deploy/restic-excludes.txt`,
        today `**/*.partial`; file count, total bytes, newest mtime) with `restic ls latest` output for the live archive path from `.env`
        (count, bytes, snapshot time). Refuse with
        "archive changed since snapshot <time>; run the restic backup first"
        when count or bytes differ or any live file is newer than the snapshot.
        Parse `restic ls` leniently (JSON output if available, else tolerate
        whitespace variation).
  - [ ] Success: stubbed listings cover equal, count differs, bytes differ,
        newer file; only equal passes. A live `.partial` file absent from the
        listing still passes.
- [ ] **5.3 Unit test for 5.2 and the sudo wrapper** (effort 2)
  - [ ] `test/unit/test_drill_228_host.py`. Use a hand-written fixture in
        `restic ls --json` form (one snapshot line, then one node line per file
        with `type`, `path`, `size`, `mtime`). Parse leniently. The real-output
        fixture is captured in 6.8, where step 1 runs `restic ls` itself.
  - [ ] Success: tests pass on the hand-written fixture.
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
- [ ] **5.7 Implement scratch server start and wait** (effort 3)
  - [ ] `start_scratch(datadir)`: `pg_ctl start` (4.1 binary directory) as
        manta; wait up to the recovery bound for `pg_is_in_recovery()` false; if
        the server process exits during the wait, fail with the log tail.
        Stopping reuses 4.4's `stop_scratch_server`.
  - [ ] Success: importable; no timeout or path literal outside 4.1.
- [ ] **5.8 Unit tests for 5.7** (effort 2)
  - [ ] Add to `test_drill_228_host.py` with `pg_ctl` and the recovery probe
        stubbed: a server that exits during the wait fails with the log tail; a
        wait past the bound fails by name; recovery finishing returns.
  - [ ] Success: pass.
- [ ] **5.9 Implement the recovery-target check** (effort 3)
  - [ ] `last_restored_segment(logfile)`: parse the server log leniently (any
        whitespace, quoting variation). `check_reached(seen, needed)` fails with
        "recovery stopped early at <segment>" when seen < needed (WAL names
        compare lexically within a timeline; use `wal_segment_name.py` helpers
        if they apply).
  - [ ] Success: importable.
- [ ] **5.10 Unit tests for 5.9** (effort 2)
  - [ ] Segment comparison cases (equal, later, earlier) and the log parser on a
        hand-written excerpt in PostgreSQL 17's `restored log file "<seg>"
        from archive` form, with whitespace variations. A real-log test is
        added in 6.8.
  - [ ] Success: pass; ruff and mypy clean on touched files.
- [ ] **5.11 Commit checkpoint** - `feat: add restore drill host primitives`

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
  - [ ] Add `rebuild_env()`: returns the subprocess environment for step 6:
        tick URLs pointing at the scratch socket and drill database,
        `MT_TICK_ARCHIVE_DIR` at the restored archive, and `MT_TIMESCALE_DB_URL`
        = `.env`'s value plus `options=-c default_transaction_read_only=on`.
        Never edits `.env`.
  - [ ] Success: `--help` works; running with a stubbed step list produces a
        report file and the right exit status.
- [ ] **6.2 Unit tests: report and exit status; rebuild environment** (effort 3)
  - [ ] `test/unit/test_drill_tick_restore.py`: exit status 0 only when all
        steps pass, non-zero (and a report written) on any failure.
  - [ ] Test `rebuild_env()` (6.1): the calendar URL carries `default_transaction_read_only=on`; the
        tick URLs do not.
  - [ ] Success: tests pass.
- [ ] **6.3 Steps 0-1: prepare and hold** (effort 4)
  - [ ] Step 0: drill flock, `sudo -v`, leftover sweep, free-space check sized
        from the live archive (`MT_TICK_ARCHIVE_DIR` from `.env`, per 1.1) and
        the latest base backup (about 3x the tick
        footprint; fail by name with expected and seen bytes).
  - [ ] Step 1: connect with `MT_TICK_MAINTENANCE_URL` read-only
        (`default_transaction_read_only=on`, TD6 statement timeout); take
        **both** tick advisory locks on that connection, acquisition then
        ingest (`TICK_ACQUISITION_LOCK_KEY`, `TICK_INGEST_LOCK_KEY`), refusing
        if either is held; `has_table_privilege(..., 'SELECT')` on every
        public table; read `max_worker_processes`,
        `max_locks_per_transaction`, `max_connections`; run the 5.2 archive vs
        snapshot check.
  - [ ] Success: each check prints expected against seen.
- [ ] **6.4 Steps 2-3: switch WAL, restore the archive** (effort 4)
  - [ ] Step 2: `SELECT pg_walfile_name(pg_switch_wal())` through
        `cutover_227_host.psql` (postgres over the socket); wait up to the TD6
        bound for that segment's `.zst` or raw file in the tick WAL dir.
  - [ ] Step 3: restic `restore latest --include <live archive path from .env>`
        into the drill dir (bounded), `assert_drill_path` on the restored
        archive directory (inside the drill directory, per 4.4), then
        `sudo -n chown -R manta:manta` on that directory only; compare file count and
        bytes with the live archive's backed-up set (5.2's function, one
        definition of the set).
  - [ ] Success: counts and bytes equal the live archive's backed-up set.
- [ ] **6.5 Step 4: restore the database** (effort 4)
  - [ ] Unpack the latest tick base backup into the drill dir; run
        `pg_verifybackup` from the 4.1 binary directory (there is no `/usr/bin`
        wrapper) (both bounded); `empty_auto_conf`; `write_scratch_conf`;
        `start_scratch`; `check_reached` against step 2's segment.
  - [ ] Success: scratch server is out of recovery and reached the target.
- [ ] **6.6 Step 5: compare restored with production** (effort 3)
  - [ ] Exact row counts for every public table; `fingerprint` (2.1) on both
        sides with `diff_fingerprints`; `table_md5` (3.6) for each bookkeeping
        table.
  - [ ] Then confirm both advisory locks are still held on the lock
        connection's own backend (`pg_locks`); any error on that connection or
        a missing lock fails the step. Release the locks; production is not touched afterwards.
  - [ ] Success: all equal, or the step fails naming the first difference.
- [ ] **6.7 Run steps 0-5 on manta9000** (effort 3)
  - [ ] Steps 6-9 are not wired yet. Run the step functions through step 5 from
        a one-off shell call (`run_steps(through=5)`, a function parameter, not
        a CLI option, so the finished script's interface and FR1's "exits 0"
        meaning are unchanged). Cleanup (4.4) must still run.
  - [ ] Success: steps 0-5 pass on the host; no `228-drill-*` directory and no
        scratch server left.
  - [ ] If a step fails, get the actual error text before any fix (CLAUDE.md);
        ask the Project Manager if it cannot be obtained.
- [ ] **6.8 Capture real-format fixtures and test the parsers** (effort 3)
  - [ ] From 6.7's run, save a short excerpt of the real recovery log and a few
        real `restic ls --json` lines as fixtures under `test/fixtures`.
  - [ ] Add tests that 5.9's log parser and 5.2's listing parser read them
        (CLAUDE.md's real-format rule). If either parser fails on real input,
        fix the parser, not the fixture.
  - [ ] Success: both tests pass on the real excerpts.
- [ ] **6.9 Commit checkpoint** - `feat: add tick restore drill steps 0-5`

---

## Section 7 - Steps 6-9 (fallback path, cleanup, report)

- [ ] **7.1 Step 6: rebuild into `trading_tick_drill`** (effort 4)
  - [ ] Implement `create_drill_database()`: runs `provision_tick_roles.sql -v
        tick_db=trading_tick_drill` on the scratch server over its socket.
        Unit test with `psql` stubbed: the command carries that variable and the
        scratch socket, never a production URL.
  - [ ] Then with `rebuild_env()` run, each
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
  - [ ] Report to `<report directory from 4.1>/<date>-228-tick-restore-drill.md`
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
        (next due 2026-11-17). Update the runbook's "Repeat expectation"
        paragraph so the quarterly drill is production's Step 6 **plus** this
        script. That wording is the slice's Integration Requirement; running it
        on 2026-11-17 is the PM's future quarterly drill, not a task here.
  - [ ] Add a Drill record row with this run's date, duration, and outcome from
        7.4/7.5, citing the report path.
  - [ ] Success: runbook front matter `dateUpdated` bumped; links resolve.
- [ ] **8.4 Commit checkpoint** - `docs: add tick restore procedures and drill to runbook 200`

---

## Section 9 - Final validation

- [ ] **9.1 Quality gates** (effort 2)
  - [ ] Run ruff format and check on touched files only; `git diff <target>`
        (target from `cf config get git.integration_branch`, else `main`) shows
        no unrelated deletions.
  - [ ] Run mypy on the new `scripts/drill_*` files and the new tests in one
        invocation (the project's `files` setting covers only `src/`). If the
        scripts' sibling imports don't resolve, set `MYPYPATH=scripts`.
  - [ ] Run the new unit and integration tests, tiers separately.
  - [ ] Grep to confirm: no TD6 bound, drill root/prefix/marker/lock,
        PostgreSQL binary directory or report directory literal outside
        `drill_228_lifecycle.py`; no `/data/tick-archive` literal in the
        new code; no cluster path literal outside `backup-clusters.conf`; no
        credential in any new file.
  - [ ] Success: all clean; any pre-existing failure matches the known list.
- [ ] **9.2 Walk the design's Success Criteria** (effort 2)
  - [ ] Check each Functional and Technical Requirement against the report and
        tests; list any gap.
  - [ ] Update the slice design's `status` and `dateUpdated`; add real output to
        its Verification Walkthrough where Phase 6 refined it.
  - [ ] Success: every criterion maps to a passing test or the recorded run.
- [ ] **9.3 Commit checkpoint** - `docs: update slice 228 verification and status`
