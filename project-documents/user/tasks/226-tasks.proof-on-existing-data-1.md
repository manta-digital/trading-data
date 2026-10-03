---
docType: tasks
slice: proof-on-existing-data
project: trading-data
lld: user/slices/226-slice.proof-on-existing-data.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [223, 224, 225, 923]
interfaces: [227, 228, 229, 230, 231, 232, 233]
projectState: >
  Slice design committed and reviewed twice (findings addressed). 225 is
  merged: ingest, status, coverage, supersession and overlap paths work on
  uncompressed chunks. Six jobs sit under /data/tick-archive. No production
  tick cluster exists yet; MT_TICK_DB_URL points nowhere. tick_007 does not
  exist.
dateCreated: 20261003
dateUpdated: 20261003
status: not_started
---

# Tasks: Proof on Existing Data

## Context Summary

- Working on slice 226. It measures the 225 pipeline on the production host,
  turns the numbers into decisions, and builds the production tick database.
- It delivers:
  - the bad-header fix (`DbnFile` raises `TickFileDecodeError`; verify and
    ingest fail the unit, not the pass);
  - `scripts/provision_tick_cluster.sh`: a second PostgreSQL cluster
    (`17/tick`, port 5433, data on `/data`) with `trading_tick` and
    `trading_tick_proof`;
  - `scripts/proof_226_tick.py`: one measurement per step, one report per run
    under `user/notes/`;
  - migration `tick_007_trade_columnstore` (integer-time function,
    columnstore layout, compression policy);
  - five compressed-chunk integration tests;
  - constants re-set from measurement;
  - `trading_tick` rebuilt from the archive;
  - `user/analysis/226-analysis.tick-proof-go-no-go.md`, the `TICK_UNIVERSE`
    edit for ES, and docs.
- **Nothing is bought.** Every pass runs `--estimate-only`. If an estimate
  plans anything, stop and report it to the Project Manager.
- **Destructive SQL** targets only `trading_tick_proof` (created by the
  provisioning script for this slice) or a test fixture's database. Tests
  never read the production URL variable.
- **The production cluster `17/main` is untouched.** No restart, no config
  change, no timer started or stopped.
- Design: read Technical Decisions 1–10 and Success Criteria before starting.
  Tasks cite them as "TD n".
- Next slice: 227 (backup), which consumes the rebuild cost measured here.

**Test environment.** Export `MT_TIMESCALE_TEST_URL` from `.env` with the
quotes stripped. Run mypy on the src kalshi paths and the tests in one
invocation. Run the unit and integration tiers separately. Known baseline
failures are not regressions (`test_cli_lists` priority1 x2,
`test_migration_051_052` x2, `test_policy_advances_head` unaided x2); re-run
in isolation before investigating anything else. Scope `ruff format` to
touched files and check `git diff` for unrelated deletions before each commit.

**Reports.** Every harness run writes a report under `user/notes/`. Commit
each one. Every number in the go/no-go names its report file.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 0 — Baseline

- [ ] **0.1 Record the unit and integration baselines (effort 1)**
  - [ ] Confirm the working directory is the project root and the current
        branch is the slice branch.
  - [ ] Run the unit tier and the integration tier separately. Note every
        failure by test id in a scratch note.
  - [ ] Success: unit tier clean; integration failures are only the known
        baseline list above.

---

## Section 1 — The bad-header fix (TD 8)

- [ ] **1.1 Convert header raises in `dbn_file.py` (effort 2)**
  - [ ] Read `src/manta_trading/data/tick/databento/dbn_file.py` and list every
        raise that comes from header content: mixed record types, unsupported
        schema, unsupported `stype_in`, wrong `stype_out`, `ts_out` records,
        and the mapping date of the wrong type.
  - [ ] Change each to `TickFileDecodeError`, keeping the message text.
  - [ ] Leave `iter_batches`'s byte-budget `ValueError` as it is. It is a
        configuration error and must stop the run.
  - [ ] Success: no header-content raise in the file is a bare `ValueError` or
        `TypeError`; the budget error is unchanged.

- [ ] **1.2 Unit tests for each header case (effort 2)**
  - [ ] One test per case in 1.1. Each builds its file by rewriting the
        header bytes of a real fixture (never an invented format).
  - [ ] Each asserts `TickFileDecodeError` with the original message.
  - [ ] One test asserts the byte-budget `ValueError` is still raised.
  - [ ] Success: new unit tests pass; existing `DbnFile` tests pass unedited.

- [ ] **1.3 Verify maps the decode error to a unit failure (effort 2)**
  - [ ] In `verify._file_mismatch`, catch `TickFileDecodeError` from
        `open_file` and return `header: <message>`.
  - [ ] Confirm the ingest worker already maps `TickFileDecodeError` to a
        `decode:` failure. Change nothing there.
  - [ ] Success: a bad-header unit yields a mismatch string, not an exception.

- [ ] **1.4 Integration tests: both passes continue past a bad header (effort 3)**
  - [ ] Create `test/integration/data/test_tick_bad_header.py`.
  - [ ] Verify: a pass over two units, one with a bad header, reports the bad
        unit failed with `header:` and verifies the other.
  - [ ] Ingest: the same shape; the bad unit fails with `decode:` and the
        other is ingested.
  - [ ] Success: both tests pass; no `storage_abort` in either.

- [ ] **1.5 Section checkpoint**
  - [ ] Run unit tier, mypy and ruff on touched files.
  - [ ] Commit: `fix: fail the unit on a bad DBN header, not the pass`.
  - [ ] Success: tiers clean; commit exists.

---

## Section 2 — Proof constants and the database guard (TD 2, TD 4)

- [ ] **2.1 Add the new constants (effort 1)**
  - [ ] In `constants.py` add `TICK_PROOF_DB_NAME` (`trading_tick_proof`),
        `TICK_TRADE_COMPRESS_AFTER` (`timedelta(days=14)`),
        `TICK_TRADE_SEGMENT_BY` and `TICK_TRADE_ORDER_BY`. The last two take
        layout A as a placeholder until Section 8 decides.
  - [ ] Docstrings state the value is provisional where it is.
  - [ ] Success: constants import; no other module hard-codes these values.

- [ ] **2.2 Harness skeleton and destructive-statement guard (effort 3)**
  - [ ] Create `scripts/proof_226_tick.py` with a step dispatcher (one
        positional step name; unknown name exits non-zero) and the report
        writer. Reuse `scripts/cutover_common.py` report helpers where they
        fit; extract nothing generic.
  - [ ] Add the guard function: every destructive harness function first checks
        `current_database() = TICK_PROOF_DB_NAME` and raises otherwise.
        Destructive means TRUNCATE, DROP, DELETE, ALTER, decompress.
  - [ ] Add the proof-database reset: `TRUNCATE tick_trade,
        tick_ingest_ledger`, then set every ingested tier unit back to
        *verified*. It sits behind the guard.
  - [ ] Add the free-space check: refuse to load data when `/data` has less
        than 50 GB free (named constant).
  - [ ] Success: the dispatcher lists the ten steps from TD 2; the guard and the
        space check are importable functions.

- [ ] **2.3 Guard tests (effort 2)**
  - [ ] Unit test: every destructive harness function raises on a connection
        whose database name is not `TICK_PROOF_DB_NAME`.
  - [ ] Test (against a throwaway database from a fixture): the reset empties
        both tables and returns tier units to *verified*.
  - [ ] Success: tests pass; a deliberately wrong name makes them fail.

---

## Section 3 — The provisioning script (TD 1)

- [ ] **3.1 Script skeleton with checks and `--check` mode (effort 3)**
  - [ ] Create `scripts/provision_tick_cluster.sh` following the host-script
        convention: check-then-act, expected against seen, logged to
        `/var/log/manta-tick-provision-<timestamp>.log`, safe to re-run,
        exit 0 only when every check passes. Study
        `scripts/cutover_265_trades.py` for the report style.
  - [ ] Memory settings in one block at the top, with the values from TD 1's
        table.
  - [ ] Pre-checks: port 5433 free; `/data` has at least 100 GB free; the
        TimescaleDB version matches the production cluster's.
  - [ ] `--check` runs without root, prints what it would do, and changes
        nothing. It never touches `17/main` beyond reading its version and port.
  - [ ] Success: `--check` runs as `manta` and prints every step with expected
        against seen.

- [ ] **3.2 Cluster creation and configuration steps (effort 4)**
  - [ ] `/data/postgresql` owned by `postgres`, mode 0700.
  - [ ] `pg_createcluster 17 tick --port 5433 --datadir
        /data/postgresql/17/tick`, only if absent.
  - [ ] Settings: `shared_preload_libraries = 'timescaledb'`;
        `listen_addresses = '127.0.1.1'`; the memory block; `pg_hba.conf`
        `scram-sha-256` from `127.0.1.1/32` for `tick_app` and `tick_migrate`
        only, peer access kept for `postgres`.
  - [ ] Reconcile settings on re-run and restart only `postgresql@17-tick`,
        and only when a setting changed.
  - [ ] Success: each step reads state first and acts only if different.

- [ ] **3.3 Databases, roles and credentials (effort 4)**
  - [ ] Apply `provision_tick_roles.sql` to both `trading_tick` and
        `trading_tick_proof`. Read the SQL first; extend the script, not
        the SQL, unless a grant is missing.
  - [ ] Generate both passwords, set them with `ALTER ROLE`, and never print
        them.
  - [ ] Write `MT_TICK_DB_URL`, `MT_TICK_MAINTENANCE_URL` and the two proof
        URLs into the checkout's `.env`: add only absent keys, write to a 0600
        temp file in the same directory, then `mv` over `.env`. Keep the file
        owned by `manta`, mode 0600.
  - [ ] Final line: `PASS: tick cluster 17/tick on 5433; 2 databases; .env
        updated (N keys added)`.
  - [ ] Success: a second run prints `0 keys added` and exits 0.

- [ ] **3.4 Log scrub (effort 2)**
  - [ ] Before copying the log to `user/notes/`, grep it for each generated
        password and for any `postgresql://` URL carrying a password. A match
        fails the run and no copy is made. `--check` runs the same scan over
        any log already copied.
  - [ ] Success: the scan is a callable unit that tests can run in isolation.

- [ ] **3.5 Tests for the `.env` writer and the log scrub (effort 3)**
  - [ ] Test the `.env` writer on a temp directory: adds only absent keys,
        leaves existing values alone, mode ends 0600, a simulated failure
        part-way leaves the old file whole.
  - [ ] Test the scrub with a log that contains a planted password. The test
        must fail the scan on it, and pass it on a clean log.
  - [ ] Success: tests pass; no real credential appears in any fixture.

- [ ] **3.6 shellcheck, dry run, commit (effort 1)**
  - [ ] `~/.local/bin/shellcheck scripts/provision_tick_cluster.sh` clean.
  - [ ] Run `--check` and read the output for anything that would touch
        `17/main`.
  - [ ] Record the checksums of `17/main`'s configuration files for the
        before/after comparison in 14.2.
  - [ ] Commit: `feat: add tick cluster provisioning script`.
  - [ ] Success: shellcheck clean; `--check` exits 0; commit exists.

- [ ] **3.7 [PM] Run the provisioning script once (effort 1)**
  - [ ] Hand the PM one command: `sudo scripts/provision_tick_cluster.sh`.
        Nothing else for the PM to do.
  - [ ] Read the report: exit 0, the PASS line, `pg_lsclusters` shows `main`
        5432 and `tick` 5433 online, TimescaleDB `2.29.1`.
  - [ ] Re-run `--check` and confirm `0 keys added`.
  - [ ] Commit the log copy under `user/notes/`. Commit:
        `docs: add tick cluster provisioning log`.
  - [ ] Success: Success Criterion 1 holds; the log is committed; `.env`
        holds the four tick URLs and none is in `git status`.

---

## Section 4 — Rebuild: the baseline on the production host (TD 2, TD 3)

- [ ] **4.1 Implement the `rebuild` step (effort 4)**
  - [ ] In the proof database from empty: migrate (tick track), `adopt` each
        of the six job directories, `pass --estimate-only`, `ingest`. Time
        each. Then `coverage` over both ranges.
  - [ ] Drive the shipped CLI or pass function; take unit timing from the
        ingest report, not wrappers.
  - [ ] If the estimate plans anything, stop and report the plan. Do not buy.
  - [ ] Record connect time (for the connect-timeout rule).
  - [ ] Refuse below the `/data` free-space floor.
  - [ ] Success: the step writes `user/notes/<date>-226-proof-rebuild.md` or
        exits non-zero with a reason and no report.

- [ ] **4.2 Run `rebuild` and check it (effort 2)**
  - [ ] Run `uv run python scripts/proof_226_tick.py rebuild`.
  - [ ] Success: 78 tier units ingested, 0 failed, 27,691,412 rows, coverage
        `ok` on every session, slowest unit at or under 120 s, whole set at or
        under 2 h. Any miss is reported to the PM before continuing.
  - [ ] Commit the code and the report: `feat: add proof harness rebuild
        step`.

---

## Section 5 — Uncompressed measurements (TD 3)

- [ ] **5.1 `mapping` step (effort 3)**
  - [ ] Decode every tier file with `DbnFileReader.open_file`. For every
        record, check that some interval in the file's `mappings` names its
        `instrument_id` and covers the file's day.
  - [ ] Report the count checked and the miss count.
  - [ ] Success: the report states a percentage over exactly 27,691,412
        records. A miss makes the report say NO-GO and name the raw-symbol
        fallback (`stype_in=raw_symbol`).

- [ ] **5.2 Run `mapping` (effort 1)**
  - [ ] Success: `100.00 %`, or the NO-GO is flagged to the PM at once.

- [ ] **5.3 `size` step (effort 3)**
  - [ ] Per tier: archive bytes, DBN record size read from
        `DbnFile.record_size` (never assumed), and uncompressed table bytes,
        each divided by an exact `count(*)`. Per-chunk sizes give per-tier
        figures, since the tiers occupy separate chunks.
  - [ ] Record rows per instrument per chunk, as the skew evidence for the
        space-partitioning rejection.
  - [ ] Success: the report holds bytes per record for both tiers and a skew
        table.

- [ ] **5.4 `jobs` step (effort 2)**
  - [ ] Read every account job record through `ITickMetadataProvider`
        (`batch_jobs_since`; free). Tabulate submit → done time per job.
  - [ ] Success: the report lists every job with its duration and names the
        slowest and fastest.

- [ ] **5.5 Run `size` and `jobs` (effort 1)**
  - [ ] Success: both reports exist and are committed. Commit:
        `feat: add proof harness mapping, size and jobs steps`.

- [ ] **5.6 `workers` step (effort 3)**
  - [ ] Reset the proof database, then re-ingest with `TICK_INGEST_WORKERS`
        patched in process to 1, 2 and 4. Record wall time, per-unit decode
        and write sums, and peak host CPU.
  - [ ] Apply the TD 3 rule text in the report: keep 2 unless 4 is at least
        1.5x faster and the contention run (Section 7) stays within its bound.
  - [ ] Success: the report holds three runs and a stated verdict, marked
        provisional until contention is measured.

- [ ] **5.7 `batch` step (effort 3)**
  - [ ] Re-ingest the three largest units with `TICK_DECODE_BATCH_BYTES` at
        8, 32 and 128 MiB. Record peak worker RSS and batch count.
  - [ ] Success: the report states keep-or-change by the rule (keep 32 MiB
        unless peak RSS exceeds 4x the budget, or a smaller budget moves unit
        time by more than 10 %).

- [ ] **5.8 Run `workers` and `batch` (effort 2)**
  - [ ] Run each; confirm free space and `MemAvailable` before each.
  - [ ] Success: both reports exist; the proof database is reset after each
        run; commit: `feat: add proof harness workers and batch steps`.

---

## Section 6 — Compression layouts and queries (TD 3, TD 4)

- [ ] **6.1 Query set (effort 3)**
  - [ ] Pick, from the ledger, the instrument-session with the most records in
        each range. Implement Q1–Q5 as TD 3 defines them.
  - [ ] Run each under `SET statement_timeout` and `EXPLAIN (ANALYZE,
        BUFFERS)`. Record planning and execution times separately, and the
        buffer hit ratio.
  - [ ] Success: a function takes a database connection and returns one result
        row per query.

- [ ] **6.2 `queries` step, uncompressed baseline (effort 2)**
  - [ ] Run the query set three times, warm. Also compute the chunk-count
        projection over the table's 20-year span for the chunk interval rule
        (1,000–2,000 chunks; planning at most 50 ms for Q1–Q4).
  - [ ] Success: report written with the interval verdict.

- [ ] **6.3 `layouts` step (effort 5)**
  - [ ] Begin by decompressing every compressed chunk in the proof database.
  - [ ] Layout A: `segmentby = instrument_id`, `orderby = ts_event, sequence,
        sequence_ordinal`. Layout B: no segmentby, `orderby = instrument_id,
        ts_event, sequence, sequence_ordinal`. For each: set it, compress
        every chunk, record compressed bytes per row by tier, run the query
        set three times, then decompress.
  - [ ] On a compressed chunk under each layout, time the supersession delete
        (bounded by ledger times). That number feeds the lock-timeout rule.
  - [ ] Verify TimescaleDB 2.29's rule that every unique-key column must be in
        segmentby or orderby; if either layout is rejected, the report says so.
  - [ ] Apply TD 4's rule: lower compressed bytes per row wins, unless it
        misses a Q1–Q4 bound the other meets; within 10 % on both, choose A.
  - [ ] Success: the report names A or B by the rule and states the numbers.

- [ ] **6.4 Run `queries` and `layouts` (effort 2)**
  - [ ] Success: both reports exist. Commit: `feat: add proof harness queries
        and layouts steps`.

---

## Section 7 — Contention against the Kalshi pass (TD 7)

- [ ] **7.1 Sampler (effort 3)**
  - [ ] Every 5 s record: host CPU busy and iowait (`/proc/stat`);
        `MemAvailable`, `Committed_AS` and `CommitLimit` (`/proc/meminfo`);
        reads and writes per device (`nvme0n1`, `nvme1n1` from
        `/proc/diskstats`); the production cluster's `pg_stat_database` commit
        and tuple counters; the tick loop's rows per second.
  - [ ] The production connection uses `MT_TIMESCALE_DB_URL` with
        `default_transaction_read_only = on` and a 5 s `statement_timeout`.
        Its only two reads are `pass_runs` and `pg_stat_database`.
  - [ ] Success: the sampler runs standalone for 30 s and returns a series.

- [ ] **7.2 Guards as named constants (effort 3)**
  - [ ] `MemAvailable` below 16 GiB stops the loop and exits non-zero; a first
        sample already below the floor starts nothing.
  - [ ] `/data` below 50 GB stops the loop and exits non-zero.
  - [ ] A Kalshi pass still running at 2 x 324 s stops the loop and records the
        trip. The pass is left alone.
  - [ ] The Kalshi timer not fired within 75 minutes, or no next-elapse time,
        exits non-zero with no load started.
  - [ ] Production unreachable before the start exits non-zero.
  - [ ] A `try/finally` stops the loop, awaits the in-flight workers and
        closes every connection on interrupt or error.
  - [ ] No commit-limit guard (TD 7 explains why).
  - [ ] Success: each guard is one named constant plus one check function.

- [ ] **7.3 Guard tests (effort 3)**
  - [ ] Unit tests with fake samples and fake clocks: each trip condition stops
        the loop, sets the non-zero exit and writes the report line.
  - [ ] A first-sample-below-floor test asserts no load starts.
  - [ ] Success: tests pass without touching a real database or timer.

- [ ] **7.4 `contention` step (effort 4)**
  - [ ] Read the Kalshi timer's next elapse (read only; never start or stop a
        unit). Two minutes before it, start the loop in the proof database:
        reset, ingest the whole set, repeat. Stop one minute after the Kalshi
        pass ends. Two overlapped firings, then one solo firing.
  - [ ] Report: each overlapped duration against the week's 133–324 s; median
        and 95th percentile of each series, overlapped against solo; tick
        ingest rate under overlap against `rebuild`'s solo rate.
  - [ ] Verdict: above 324 s is `measurable contention`; otherwise `none
        measured at two ingest workers`, with the sentence that this is not a
        clearance for the minute pass (232 measures that).
  - [ ] Success: step written; verdict logic unit tested on synthetic numbers.

- [ ] **7.5 Run `contention` (effort 2)**
  - [ ] Start it in the background. State the current UTC and local time and
        the next Kalshi firing in the message to the PM. Proceed to Section 8
        while it runs.
  - [ ] When it finishes, confirm the report, commit it, and record the
        verdict. Commit: `feat: add proof harness contention step`.
  - [ ] Success: a report with two overlapped and one solo Kalshi firing, and
        a verdict.

---
