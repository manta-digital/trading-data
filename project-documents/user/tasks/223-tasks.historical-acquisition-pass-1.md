---
docType: tasks
slice: historical-acquisition-pass
project: trading-data
lld: user/slices/223-slice.historical-acquisition-pass.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [923, 220, 221, 222]
interfaces: [224, 225, 226, 227, 228, 230, 232]
projectState: >
  Slice design committed and reviewed (CONCERNS; F002–F009 addressed in
  f389027, unknown MT_TICK_ key preflight in f777626). 222 is merged: the tick
  track holds tick_001–tick_005 and the grant artifact enumerates five tables.
  No code writes the manifest yet. No production tick database exists; this
  slice runs on scratch databases on the test cluster. The PM has set both
  spend ceilings in the dev .env (0.50 per pass, 5 per 30 days).
dateCreated: 20260928
dateUpdated: 20260928
status: not_started
---

# Tasks: Historical Acquisition Pass — Part 1 (foundation, adoption, reset, backup)

## Context Summary

- Working on slice 223. The task list is in two files:
  - **Part 1 (this file):** settings and constants, migration `tick_006`, the
    run context, the manifest repository, verification, `mt data tick adopt`,
    `mt data tick reset`, and archive backup enrolment. It spends no money.
    At its end both free-credit ES jobs are archived, verified and backed up.
  - **Part 2** (`223-tasks.historical-acquisition-pass-2.md`): the pass
    contract, universe, planner, spend guard, availability, delivery,
    definitions, `mt data tick pass`, documents and the walkthrough.
- Tasks cite the LLD's Technical Decisions as "TD n (short name)". The eleven:
  TD1 pass contract copy, TD2 run context and lock, TD3 universe constant,
  TD4 wanted days and monthly grouping, TD5 definitions, TD6 calendar
  dependency, TD7 spend guard, TD8 reopened units, TD9 submit and reconcile,
  TD10 adoption, TD11 archive and backup. Read the named TD before each task.
- FR n below means the LLD's Functional Requirement n (Success Criteria).
- Every destructive statement targets a database a fixture or the
  walkthrough created (`sql.md`). No test holds a real client with a paid
  method reachable.
- Next slice: 224 (ingest).

**Test environment.** Export `MT_TIMESCALE_TEST_URL` from `.env` with the
quotes stripped. Run the unit and integration tiers as separate pytest
invocations (`test/unit/data` and `test/integration/data` both import as
`data`). Run mypy on the src kalshi paths, the touched src paths and the
tests in one invocation. **Before every commit step**, run ruff check and
ruff format on that commit's touched files only, then grep `git diff main`
for swept pre-existing lines. Known pre-existing failures are not
regressions; re-run a failure in isolation before investigating.

**Fakes.** Provider tests drive the real `DatabentoTickProvider` over
`test/tick_support/fake_historical.py` with `batch_responses.py` and
`metadata_responses.py`, as `test/unit/data/tick/test_adapter_acquisition.py`
does. The calendar in integration tests comes from `session_migrated_db`
(it holds `CME_EQUITY`, seeded by 221).

**File size.** Source files stay under about 300 lines. If one would pass
that, split along the phase or concern it holds and note the split.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 0 — Baseline and test support

- [ ] **0.1 Confirm the SDK and fixtures have not moved since 222**
  - [ ] `uv.lock` still pins `databento` 0.87.0; `git log -- uv.lock
        test/fixtures/databento` shows no fixture change since 220's merge
  - [ ] If either moved, STOP and report to the PM
  - [ ] Success: stated in the task note
  - [ ] Effort: 1

- [ ] **0.2 Record the pre-change test baseline**
  - [ ] On the slice branch before any change, run the unit tier, then the
        integration tier; save failing ids to `/tmp/223-baseline-unit.txt`
        and `/tmp/223-baseline-integration.txt`
  - [ ] Success: both files exist; each failure matches the known lists in
        project memory or is re-run in isolation and noted
  - [ ] Effort: 1

- [ ] **0.3 Test helper: DBN files with a day-aligned header**
  - [ ] Background: the fixtures' headers do not span one UTC day (trades and
        tbbo: 2020-12-28 13:00 → 2020-12-29 00:00 UTC; definition: XNAS over
        months). Verification requires a header spanning exactly the unit's
        UTC day (TD9, Verification)
  - [ ] Create `test/tick_support/dbn_files.py` with
        `write_day_file(dest, fixture, dataset, day, stype_in)`: decode the
        fixture's records, encode a new header with `databento_dbn.Metadata`
        (`start = day 00:00Z`, `end = day+1 00:00Z`, the fixture's mappings
        and schema), append the unchanged record bytes, zstd-compress, return
        `(path, size, sha256)`
  - [ ] Add `write_job_dir(root, job_id, files)` that writes the day files
        plus `manifest.json` (`job_id`, and per file `filename`, `size`,
        `hash` as `sha256:<hex>`; `manifest.json` does not list itself) and
        `condition.json`/`metadata.json`/`symbology.json` placeholders, and
        `zip_job_dir(dir) -> Path` that zips it the way the provider's zip
        is laid out
  - [ ] If `Metadata` cannot encode a header with the needed fields, STOP and
        report; do not commit real purchased data as a fixture without the
        PM's decision
  - [ ] Test `test/unit/data/tick/test_dbn_files_helper.py`: a written file
        reads back through `DbnFileReader` with the new dataset, start and end
        and the fixture's record count
  - [ ] Success: the helper test passes
  - [ ] Effort: 3

- [ ] **0.4 Batch response helpers for acquisition tests**
  - [ ] Extend `test/tick_support/batch_responses.py`: `job_record` takes
        `dataset`, `symbols`, `stype_in`, `cost_usd`, `record_count`,
        `ts_received` and `ts_expiration` overrides; add `job_list(*records)`
        for `batch_jobs_since`
  - [ ] Existing 220 adapter tests still pass unchanged
  - [ ] Success: `uv run pytest test/unit/data/tick -q` passes
  - [ ] Effort: 1
  - [ ] Commit: `test(tick): add day-file and batch job helpers for 223`

---

## Section 1 — Constants, settings, exit codes

- [ ] **1.1 Add 223's constants to `data/tick/constants.py`**
  - [ ] `TICK_WAIT_BUDGET_SECONDS = 1800`, `TICK_POLL_INTERVAL_SECONDS = 15`
        (both noted as 225 re-sets them from measurement),
        `TICK_JOB_MATCH_SKEW = timedelta(minutes=5)`,
        `TICK_SUBMIT_RESOLVE_AGE = timedelta(hours=1)`,
        `TICK_SPEND_WINDOW = timedelta(days=30)`,
        `TICK_ACQUISITION_LOCK_KEY` (an int distinct from Kalshi's
        `262_000_001`; grep the repo for every `pg_try_advisory_lock` key and
        choose one no other caller uses), `TICK_DB_CONNECT_TIMEOUT_SECONDS =
        10`, `TICK_SPEND_30D_CEILING_ENV = "MT_TICK_SPEND_30D_CEILING_USD"`,
        `TICK_ARCHIVE_DIR_ENV = "MT_TICK_ARCHIVE_DIR"`,
        `TICK_ENV_PREFIX = "MT_TICK_"`
  - [ ] One-line comment per constant giving its TD
  - [ ] Success: imports cleanly; import-boundary test passes
  - [ ] Effort: 1

- [ ] **1.2 Add the two settings**
  - [ ] In `config/__init__.py` beside `tick_spend_ceiling_usd`:
        `tick_spend_30d_ceiling_usd: Decimal | None = Field(default=None,
        gt=0)` and `tick_archive_dir: Path | None = None`
  - [ ] Success: `Settings()` loads with and without both variables
  - [ ] Effort: 1

- [ ] **1.3 Unit tests: constants and settings**
  - [ ] Extend `test/unit/data/tick/test_constants.py`: each new constant's
        value; the lock key differs from Kalshi's constant (import it)
  - [ ] Settings test beside the existing `tick_spend_ceiling_usd` tests:
        `0.50` parses as `Decimal("0.50")`; `0` and a negative are rejected;
        unset is `None`; the archive dir parses as `Path`. Build `Settings`
        from explicit values, never the developer's `.env`
  - [ ] Success: both files pass
  - [ ] Effort: 1

- [ ] **1.4 Exit codes for `adopt` and `reset`**
  - [ ] In `cli/commands/tick.py`, add `EXIT_STORAGE = 4` beside `EXIT_OK`,
        `EXIT_PREFLIGHT`, `EXIT_PROVIDER` (the rest arrive in Part 2 with
        `TickOutcome`)
  - [ ] Success: existing CLI tick tests pass
  - [ ] Effort: 1
  - [ ] Commit: `feat(tick): add acquisition constants and settings`

---

## Section 2 — Migration `tick_006_availability`

- [ ] **2.1 Append `tick_006_availability` to `TICK_MIGRATIONS`**
  - [ ] `tick_dataset_edge` and `tick_day_condition` exactly as the LLD's
        "Database / Storage Schema" tables; `condition` CHECK rendered with
        `_check_in` from `DatasetCondition`
  - [ ] `ALTER TABLE tick_archive_unit ADD COLUMN IF NOT EXISTS reopened_at
        TIMESTAMPTZ` and the named CHECK `tick_archive_unit_reopened_check`:
        `reopened_at IS NULL OR state NOT IN (...)`, rendered from
        `UNIT_STATES_WITH_FILE`; add the constraint idempotently (guard on
        `pg_constraint`, as earlier tick migrations do)
  - [ ] No index beyond the primary keys; no `GRANT`; comment cites TD8
        (reopened units)
  - [ ] Success: `migrated_tick_db` builds; applying the track twice applies
        nothing
  - [ ] Effort: 2

- [ ] **2.2 Integration tests for `tick_006`**
  - [ ] In `test/integration/data/test_tick_storage_track.py`, extend the
        re-apply test's expected ids (derived from `TRACKS["tick"]`)
  - [ ] New `test/integration/data/test_tick_availability_schema.py`:
        every `DatasetCondition` member inserts, a non-member is rejected;
        duplicate `(dataset, condition_date)` is rejected; `reopened_at` set
        on a `delivered` unit inserts; on each of `downloaded`, `verified`,
        `ingested` it is rejected
  - [ ] 923's CLI init test still reaches head
  - [ ] Success: both files pass
  - [ ] Effort: 2

- [ ] **2.3 Grant the two tables to the app role**
  - [ ] Add `tick_dataset_edge` and `tick_day_condition` to the enumerated
        `GRANT SELECT, INSERT, UPDATE, DELETE` list in
        `scripts/provision_tick_roles.sql`; update the header comment
  - [ ] Success: 222's artifact-equals-tables test in
        `test_tick_role_privileges.py` passes, and the parametrized DML test
        covers the new tables
  - [ ] Effort: 1
  - [ ] Commit: `feat(tick): add tick_006 availability migration`

---

## Section 3 — Session days

- [ ] **3.1 Create `data/tick/session_days.py`**
  - [ ] `session_days(calendar, start, end) -> list[date]`: one
        `sessions_between` call; for each session every UTC date from
        `open_utc` to `close_utc − 1 ns`, clipped to `[start, end)`, sorted,
        unique (TD4, Days)
  - [ ] `OutOfPopulatedRangeError` propagates; it is not caught here
  - [ ] Success: imports; no I/O besides the calendar call
  - [ ] Effort: 1

- [ ] **3.2 Tests for session days**
  - [ ] Unit: a stub calendar returning hand-built sessions: a Sunday-evening
        open yields Sunday and Monday; Saturday never appears; clipping at
        both ends; end exclusive
  - [ ] Integration on `session_migrated_db` with the real `CME_EQUITY`
        calendar: 2024-12-25 is included (the 12-26 session opens 23:00 UTC);
        the job-1 range 2024-08-30 → 2024-09-30 yields 26 days and the job-2
        range 2024-11-01 → 2025-01-01 yields 52 (the counts adoption must
        produce, FR2). If either count differs, STOP and report: the design's
        adoption expectation depends on it
  - [ ] Success: both pass
  - [ ] Effort: 2
  - [ ] Commit: `feat(tick): add session-touched UTC day computation`

---

## Section 4 — Run context

- [ ] **4.1 Create `data/tick/run_context.py`**
  - [ ] `TickPreflightError(message)`; `TickRun` dataclass (`settings`,
        `provider`, `conn`, `archive_root`, `run_id`, `clock`)
  - [ ] `open_tick_run(settings, clock=...)`: async context manager. Refusals
        in TD2's order (run context), each naming the variable or command:
    1. unknown `MT_TICK_*` key in the process environment or the `.env` file:
       known names are derived from `Settings.model_fields` starting `tick_`
       (with the settings' env prefix), never a second list; the message
       names the closest known name (`difflib.get_close_matches`)
    2. API key: `DatabentoTickProvider.from_settings` → `ProviderAuthError`
    3. `MT_TICK_DB_URL` via `resolve_database_url(..., Database.TICK,
       Credential.APPLICATION)`; `DatabaseNotConfiguredError.env_var` shown
    4. `MT_TICK_ARCHIVE_DIR` unset, not an existing directory, or not
       writable; never created
    5. connect within `TICK_DB_CONNECT_TIMEOUT_SECONDS`, autocommit
    6. pending tick migrations via `list_migration_state`; a missing ledger
       table counts as all pending; message ends `mt data migrate apply
       --track tick`
    7. `pg_try_advisory_lock(TICK_ACQUISITION_LOCK_KEY)` false → "another
       tick acquisition run holds the lock"
  - [ ] Copy the shape of Kalshi's `data/kalshi/db.py:open_sync_connection`;
        do not import it
  - [ ] Success: imports; no `data.kalshi` import under `data/tick`
  - [ ] Effort: 3

- [ ] **4.2 Tests for the run context (FR1, preflight)**
  - [ ] Unit (`test/unit/data/tick/test_run_context.py`): each of refusals
        1–4 raises `TickPreflightError` naming its variable; the misspelt
        `MT_TICK_DATA_SPEND_CEILING_USD` names `MT_TICK_SPEND_CEILING_USD`
        both when set in the process environment and when present only in a
        temporary `.env` file (the 2026-09-28 case); a missing archive
        directory is not created; an existing but read-only archive directory
        (`chmod 0555` on a `tmp_path` directory) is refused as not writable
  - [ ] Integration (`test/integration/data/test_tick_run_context.py`) on
        `ephemeral_tick_db`/`migrated_tick_db`: a bare database refuses with
        the migrate command; a migrated one yields; a second concurrent
        `open_tick_run` refuses on the lock; after the first closes, a new
        one succeeds; a URL to a closed local port refuses as unreachable
        within the connect timeout, naming `MT_TICK_DB_URL`
  - [ ] Success: both pass
  - [ ] Effort: 2
  - [ ] Commit: `feat(tick): add tick run context and preflight`

---

## Section 5 — Manifest repository

- [ ] **5.1 Create `data/tick/manifest_repo.py`**
  - [ ] Every SQL statement on `tick_request` and `tick_archive_unit`, as named
        async functions over the run's connection. Parameterized only
  - [ ] Every unit `UPDATE` is compare-and-set: its `WHERE` names the expected
        `state` and `fetch_status` (and `reopened_at IS NULL` where TD8 says);
        a zero-row match raises `ManifestTransitionError` naming the unit and
        the expected state (TD2, compare-and-set)
  - [ ] Functions needed by Part 1: insert adopted request with units;
        request-by-job-id lookup; mark downloaded (file columns); mark
        verified (`provider_record_count`); record failure (transient: attempt
        + 1, exhaust at `MAX_RETRY_COUNT`; deterministic: straight to
        `RETRY_EXHAUSTED` with reason); mark `PROVIDER_HOLE`; reset one
        exhausted unit (→ `UNKNOWN`, attempt 0, reason NULL); reopen one hole
        (`reopened_at = now`); read units by id
  - [ ] These are per-unit compare-and-set primitives only. Deciding which
        primitive applies to a unit (the reset classification) is 8.1's, not
        this module's
  - [ ] The coverage predicate (`superseded_by_unit_id IS NULL AND
        reopened_at IS NULL`) is one SQL fragment constant, used everywhere
  - [ ] Part 2 adds its functions here (submit, reconcile, sweep, trailing
        spend, supersession). If the file passes ~300 lines, split reads
        from transitions
  - [ ] Success: imports; no SQL outside this module and `availability.py`
  - [ ] Effort: 3

- [ ] **5.2 Integration tests for every Part 1 transition**
  - [ ] `test/integration/data/test_tick_manifest_repo.py` on
        `migrated_tick_db`, rows from `test/tick_support/rows.py`
  - [ ] Each transition succeeds from its expected state and raises
        `ManifestTransitionError` from any other (parametrized)
  - [ ] Transient failure five times ends `RETRY_EXHAUSTED` with attempt
        count 5; a deterministic failure exhausts at attempt 1
  - [ ] Reset-one and reopen-one raise on a unit that is already reopened
  - [ ] Success: passes
  - [ ] Effort: 3
  - [ ] Commit: `feat(tick): add compare-and-set manifest repository`

---

## Section 6 — Verification

- [ ] **6.1 Create `data/tick/verify.py`**
  - [ ] `check(run, unit)`, run in `asyncio.to_thread` for hashing and the
        header read: file exists under its final name; size and SHA-256
        re-hashed equal the recorded values; header dataset, schema,
        `stype_in` equal the request's; header start/end equal the unit's
        UTC day; then the free `record_count` for `[day, day+1)` with the
        request's symbols is stored via `mark verified` (TD9, Verification)
  - [ ] Header or hash mismatch → deterministic failure naming the field;
        `ProviderError` propagates to the phase (run-level)
  - [ ] Success: imports
  - [ ] Effort: 2

- [ ] **6.2 Tests for verification**
  - [ ] Unit over files from `write_day_file`: a matching file verifies and
        stores the fake's record count; a flipped byte, a wrong day, a wrong
        schema and a wrong dataset each fail naming the field; a record-count
        call failure raises `ProviderError`
  - [ ] Success: passes
  - [ ] Effort: 2
  - [ ] Commit: `feat(tick): add archive unit verification`

---

## Section 7 — Adoption

Adoption is split in two modules at a natural seam (review F003): file
handling in `adopt_files.py`, orchestration and rows in `adopt.py`. Both
raise `TickAdoptionRefused(message)` (defined in `adopt_files.py`) for a
refusal that writes no row; the verb maps it to exit 1 (TD10, all or
nothing).

- [ ] **7.1 Create `data/tick/adopt_files.py` (file handling)**
  - [ ] Read `manifest.json` from a directory or a zip; its `job_id` must
        equal `--job-id`; any listed name containing `/`, `\` or `..` is
        refused; zip members are read only by listed names
  - [ ] Free space on the archive volume ≥ Σ listed sizes, else refuse
        naming the shortfall (free-bytes function injectable for tests)
  - [ ] Each listed file → `<archive>/<ID>/<name>.partial`, hashed while
        written, renamed on size+SHA-256 match; an existing final file with a
        matching hash is skipped; any mismatch or missing member refuses,
        leaving `.partial` files; blocking work in `asyncio.to_thread`
  - [ ] `OSError` on write (for example `ENOSPC`) is not a refusal: it raises
        `TickArchiveWriteError` naming path and errno, which the verb maps to
        exit 4 (storage)
  - [ ] Returns the copied files with size and hash
  - [ ] Success: imports; under ~300 lines
  - [ ] Effort: 2

- [ ] **7.2 Unit tests for adoption file handling**
  - [ ] Over `write_job_dir`/`zip_job_dir`: directory and zip both copy every
        listed file; a flipped byte in one file refuses with its name and
        leaves no final file; a `../x` name and a name not in the zip are
        refused; a job-id mismatch is refused; insufficient free space is
        refused before any copy; an injected `OSError(ENOSPC)` on write
        raises `TickArchiveWriteError` naming path and errno
  - [ ] Success: passes
  - [ ] Effort: 2

- [ ] **7.3 Create `data/tick/adopt.py` (orchestration and rows)**
  - [ ] Follow TD10 (adoption) and the LLD's Adoption data flow:
    1. job id already in `tick_request` → result "already adopted", nothing
       written
    2. `batch_job(ID)` state must be `done` or `expired`, else
       `TickAdoptionRefused` naming the state
    3. `adopt_files` copies and verifies the files
    4. calendar: `calendar_for_product` for the job's product, opened as
       `TradingCalendar(id, str(settings.timescale_db_url))` (the pattern in
       `cli/commands/calendar_sessions.py`; the production database, TD6);
       `session_days` of the job's range. An unreachable calendar database or
       `OutOfPopulatedRangeError` raises before any row is written and maps to
       exit 4 naming the calendar
    5. one transaction: adopted request (`estimated = actual = cost_usd`,
       `committed_at = ts_received`, no deadline) and one unit per session
       day: file present (matched by header) → downloaded; none → delivered
       + `PROVIDER_HOLE`; a file on a non-session day → reported by name, no
       unit
    6. `verify.check` each downloaded unit
  - [ ] Returns an `AdoptResult` (job, cost, files, units by state, holes,
        strays) for rendering
  - [ ] Success: imports; under ~300 lines
  - [ ] Effort: 3

- [ ] **7.4 Integration tests: adoption end to end (FR2)**
  - [ ] `test/integration/data/test_tick_adopt.py`: `migrated_tick_db` plus
        `session_migrated_db` calendar, fake provider job record, a job
        directory of day files over two UTC days
  - [ ] Every session day with a file ends *verified* with
        `provider_record_count`; a session day without a file is
        `PROVIDER_HOLE`; a stray non-session file is reported; the request
        row matches the job's cost and `ts_received`
  - [ ] A second adoption writes nothing and returns "already adopted";
        re-adopting from `<archive>/<ID>` into a fresh database copies
        nothing and rebuilds identical rows (TD10, rebuilding the manifest)
  - [ ] Refusals write no rows: a corrupted file; a job state `queued`
        (`TickAdoptionRefused` naming it); a calendar URL to a closed port and
        a range before 2020-01-01 (outside the CME span) each raise the
        storage error with no row
  - [ ] Success: passes
  - [ ] Effort: 3
  - [ ] Commit: `feat(tick): add batch job adoption`

---

## Section 8 — `adopt` and `reset` verbs

- [ ] **8.1 Reset classification, `data/tick/reset.py`**
  - [ ] `reset_units(run, unit_ids | ALL) -> list[ResetChange]` owns the
        classification and calls 5.1's per-unit primitives: exhausted and not
        reopened → reset-one; `PROVIDER_HOLE` and not reopened → reopen-one;
        anything else unchanged and listed (TD8, reset). An unknown unit id
        is listed as not found
  - [ ] Integration test (`test/integration/data/test_tick_reset.py`): each
        branch, including reopened and non-exhausted units listed unchanged
        and an unknown id
  - [ ] Success: passes
  - [ ] Effort: 1

- [ ] **8.2 CLI verbs and rendering**
  - [ ] `mt data tick adopt --job-id ID --source PATH [--json]` and `mt data
        tick reset (--unit-id N ... | --all) [--yes] [--json]` in
        `cli/commands/tick.py`; both go through `open_tick_run`
  - [ ] Reset prompts for the word `reset` unless `--yes` or `--json`
  - [ ] Rendering in new `cli/commands/tick_pass_render.py` (Rich and `--json`
        via `cli.output`): adopt shows job, cost, files, units by state,
        holes and strays; reset shows each unit before and after
  - [ ] Exit: `TickPreflightError` or `TickAdoptionRefused` → 1;
        `ProviderError` → 2; `TickArchiveWriteError`, the calendar errors of
        7.3 and `psycopg.OperationalError` → 4; else 0
  - [ ] Success: `mt data tick --help` lists both; both files under ~300 lines
  - [ ] Effort: 2

- [ ] **8.3 CLI tests**
  - [ ] Extend `test/unit/cli/commands/test_data_tick.py` with the run context
        and cores patched: each exception class of 8.2 gives its exit code;
        `--json` shapes; reset refuses without the typed word; `--unit-id` and
        `--all` are mutually exclusive
  - [ ] Success: passes
  - [ ] Effort: 2
  - [ ] Commit: `feat(tick): add adopt and reset verbs`

---

## Section 9 — Archive backup enrolment

- [ ] **9.1 Include and exclude the archive**
  - [ ] `scripts/cron_system_backup.sh`: add `/data/tick-archive` to
        `INCLUDE_PATHS` with a comment (separate device under
        `--one-file-system`, TD11)
  - [ ] `deploy/restic-excludes.txt`: add `/data/tick-archive/**/*.partial`
  - [ ] Success: `~/.local/bin/shellcheck scripts/cron_system_backup.sh` clean
  - [ ] Effort: 1

- [ ] **9.2 Create `scripts/verify_tick_archive_backup.sh`**
  - [ ] Root script, same arguments as the cron line plus `--log`; check
        before act; prints expected vs observed at each step; logs to the
        given file. Steps per TD11 (proof, now): fail if
        `MT_TICK_ARCHIVE_DIR` from the env file is not in `INCLUDE_PATHS`;
        run the backup once; `restic ls latest` count vs the archive's count
        excluding `.partial`; restore one data file into
        `/data/restore-test/tick-archive/` (created by the script, refuse if
        it exists); compare SHA-256; remove only that directory
  - [ ] Success: shellcheck clean; `--help` runs without root
  - [ ] Effort: 2

- [ ] **9.3 Runbook 200**
  - [ ] `project-documents/user/runbooks/200-backup-and-restore.md`: add the
        path to the D9 include set, the `.partial` exclusion, and a restore
        subsection naming the verify script
  - [ ] Success: `dateUpdated` bumped
  - [ ] Effort: 1
  - [ ] Commit: `feat(backup): enrol the tick archive in the nightly backup`

---

## Section 10 — Part 1 checkpoint

- [ ] **10.1 Lint, types and tiers**
  - [ ] ruff and mypy per the test environment note; unit then integration
        tier; compare with the Section 0 baseline
  - [ ] Success: no failure outside the baseline
  - [ ] Effort: 2

- [ ] **10.2 Walkthrough steps 1–4, 8 and 9 on the real archive**
  - [ ] Run LLD walkthrough steps 1–4 (tests, scratch database
        `mt_scratch_tick_223`, the pre-init refusal, adopting both
        free-credit jobs, the manifest query), step 8 (reset) and step 9
        (backup verify under sudo). Add `MT_TICK_ARCHIVE_DIR=/data/tick-archive`
        to the dev `.env` first (not committed)
  - [ ] Run step 9 with the Bash sandbox disabled (the sandbox sets "no new
        privileges", which blocks sudo). Only if sudo is still refused, STOP
        and give the PM the single step 9 command and the log path
  - [ ] Keep `mt_scratch_tick_223`: Part 2 continues in it
  - [ ] Record actual outputs in the LLD walkthrough; correct any command or
        expected value that differed
  - [ ] Success: 26 and 52 units *verified*, provider records equal job
        records (10,049,172 and 17,642,240), re-adopt exits 0, backup log
        shows equal counts and hashes
  - [ ] Effort: 2
  - [ ] Commit: `docs: record slice 223 part 1 walkthrough`
