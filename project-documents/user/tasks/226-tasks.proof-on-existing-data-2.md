---
docType: tasks
slice: proof-on-existing-data
project: trading-data
lld: user/slices/226-slice.proof-on-existing-data.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [223, 224, 225, 923]
interfaces: [227, 229, 230, 231, 232, 233, 234]
projectState: >
  Slice design committed and reviewed twice (findings addressed). 225 is
  merged: ingest, status, coverage, supersession and overlap paths work on
  uncompressed chunks. Six jobs sit under /data/tick-archive. No production
  tick cluster exists yet; MT_TICK_DB_URL points nowhere. tick_007 does not
  exist.
dateCreated: 20261003
dateUpdated: 20261004
status: complete
---

# Tasks: Proof on Existing Data (part 2 of 2)

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

**Test environment.** Run every tier through the reviewed runner:
`uv run python scripts/run_tests.py unit | integration | load [-- <path>]`.
It passes each tier an explicit environment allowlist and strips the
production and tick URLs; never export `.env` into a test run. Run mypy on
the src kalshi paths and the tests in one invocation. Run the tiers
separately. **CI runs no test job (slice 907)**: the load tests are gated by
the runner's `load` tier alone, and this slice adds no CI wiring. Known baseline
failures are not regressions (`test_cli_lists` priority1 x2,
`test_migration_051_052` x2, `test_policy_advances_head` unaided x2); re-run
in isolation before investigating anything else. Scope `ruff format` to
touched files and check `git diff` for unrelated deletions before each commit.

**Reports.** Every harness run writes a report under `user/notes/`. Commit
each one. Every number in the go/no-go names its report file.

**Effort scale:** 1 (trivial) to 5 (hard).

---

Part 1 (Sections 0–7) holds the fix, the cluster, the harness and the uncompressed measurements. This part holds the layout, `tick_007`, the rebuild and the go/no-go. Part 1's reports and constants are inputs here.

---

## Section 8 — `tick_007_trade_columnstore` (TD 4, TD 5)

- [x] **8.1 Record the layout decision (effort 1)**
  - [x] Read the `layouts` report. Set `TICK_TRADE_SEGMENT_BY` and
        `TICK_TRADE_ORDER_BY` to the winner. Docstrings cite the report file.
  - [x] Success: constants hold the chosen layout; no provisional wording.

- [x] **8.2 Write the migration (effort 3)**
  - [x] Append `tick_007_trade_columnstore` to `migrations/tick.py` (tick
        track, maintenance credential), idempotent like its siblings.
  - [x] Step 1: `tick_now_ns()` (`BIGINT`, `STABLE`, nanoseconds since the
        epoch) and `set_integer_now_func('tick_trade', 'tick_now_ns')`.
  - [x] Step 2: `ALTER TABLE tick_trade SET (timescaledb.enable_columnstore,
        segmentby, orderby)` rendered from the constants, as the chunk interval
        is today.
  - [x] Step 3: `add_columnstore_policy('tick_trade', after => <ns from
        TICK_TRADE_COMPRESS_AFTER>)`, `if_not_exists => TRUE`.
  - [x] Check the TimescaleDB 2.29 syntax with the tool guide
        (`tool-guides/timescaledb/`) and context7 before writing it.
  - [x] Success: the migration applies to an empty tick test database twice
        without error.

- [x] **8.3 Migration tests (effort 3)**
  - [x] Wait for the contention report (7.5) before running any tier.
  - [x] Integration test (TD 5, Migration row): `tick_007` on an empty database
        and on a populated uncompressed one. Settings read back from the
        TimescaleDB information views match the constants; the policy exists;
        `tick_now_ns()` is within a second of `now()`.
  - [x] Find every test that pins the tick track's newest migration id or
        its count (start with `git grep -n "tick_006"` under `test/`, and
        `test/integration/data/test_tick_storage_track.py`). Update each to
        include `tick_007`. If none exists, say so in the commit message; do
        not invent one. The 051/052 chain test belongs to the primary track
        and is not touched.
  - [x] Flip the assertions that say `tick_trade` is uncompressed:
        `test_trade_hypertable_has_no_compression` in
        `test/integration/data/test_tick_storage_track.py` now expects
        compression enabled (rename it to say so). `git grep -n
        "compression_enabled\|has_no_compression" test/` for any other.
  - [x] Add the `tick_007` row to the migrations README.
  - [x] Success: tests pass; no other integration test newly fails.

- [x] **8.4 Apply `tick_007` to the proof database (effort 2)**
  - [x] Precondition: the `contention` report exists (7.5); the loop uses this
        database.
  - [x] Decompress every compressed chunk in `trading_tick_proof` (behind the
        guard), then run `mt data migrate apply --track tick` with the proof
        maintenance URL.
  - [x] Success: settings read back from the information views match the
        constants; the policy exists. The proof database now holds the chosen
        layout. (`final` runs on `trading_tick`, and the load test, 10.5,
        builds its own fixture database; neither reads the proof database.)

- [x] **8.5 Compressed-chunk tests: supersession and overlap (effort 4)**
  - [x] Create `test/integration/data/test_tick_compressed.py`. Compress with
        `compress_chunk` explicitly; the test cluster has no scheduler.
  - [x] Supersession: ingest a `trades` unit, compress its chunk, ingest the
        `tbbo` unit for the same day. One transaction deletes the `trades` rows
        and loads the `tbbo` rows; counts check passes; `coverage` reads `ok`.
  - [x] Overlap: a compressed chunk already holds a unit's rows; a second
        current unit with the same rows raises `UniqueViolation` and the unit
        fails with `overlap:`.
  - [x] Success: both pass. If either fails, fix 225's code in this slice (a
        failure is never fixed by dropping compression). If no fix works, stop
        and report to the PM; 8.8 then applies.

- [x] **8.6 Compressed-chunk tests: `tick_app` and coverage (effort 4)**
  - [x] Ingest as `tick_app`: the application role inserts through ingest into
        a compressed chunk. Record whether it passes as it stands.
  - [x] Coverage on a partial chunk: rows inserted into a compressed chunk
        before recompression give a raw count equal to the ledger; after
        `compress_chunk` the count still equals the ledger.
  - [x] Success: the coverage test passes, and the `tick_app` test either
        passes or fails only on privileges on TimescaleDB's internal
        compressed tables (then 8.7 applies).

- [x] **8.7 Grant for `tick_app`, only if 8.6 needs one (effort 3)** (no grant needed)
  - [x] Extend `provision_tick_roles.sql` with the minimal grant and add a
        privilege test for it.
  - [x] The existing databases do not pick it up on their own. Give the PM the
        one command to re-run the provisioning script, which applies that SQL
        to both databases. Confirm the grant on `trading_tick` and
        `trading_tick_proof` with the privilege test's query. Do this before
        10.3.
  - [x] If 8.6 passed as it stood, tick this task with "no grant needed".
  - [x] Success: the `tick_app` test passes, so all five TD 5 rows pass; or
        "no grant needed" is recorded.

- [x] **8.8 Fallback, only if a TD 5 test cannot be made to pass (effort 3)** (not needed)
  - [x] Set `TICK_TRADE_COMPRESS_AFTER` to a settled age beyond which no
        supersession or overlap can reach (the PM names it), re-run 8.5 and
        8.6 under that policy, and record the failing path and the fallback for
        the go/no-go (13.1).
  - [x] If all five tests passed, tick this task with "not needed".
  - [x] Success: either "not needed" is recorded, or the five tests pass under
        the fallback and the go/no-go input is written.

- [x] **8.9 Section checkpoint**
  - [x] Run unit tier, integration tier, mypy and ruff on touched files.
  - [x] Commit: `feat: add tick_007 columnstore migration and compressed-chunk
        tests`.
  - [x] Success: only the known baseline failures remain.

---

## Section 9 — Constants re-set from measurement (TD 6)

- [x] **9.1 Decide each constant by its rule (effort 2)** (all constants kept; 225 load test passes)
  - [x] `TICK_INGEST_WORKERS`: `workers` report, plus the contention verdict
        (4 must stay within the bound).
  - [x] `TICK_DECODE_BATCH_BYTES`: `batch` report.
  - [x] `TICK_WAIT_BUDGET_SECONDS`: max(1,800, 2 x slowest account job).
  - [x] `TICK_POLL_INTERVAL_SECONDS`: kept unless the fastest job finishes
        under 15 s.
  - [x] `TICK_INGEST_LOCK_TIMEOUT_SECONDS`: at least 4 x the slowest
        compressed-chunk supersession delete, never below 30. Source: the
        `layouts` report's delete times (6.5), taking the chosen layout's.
        If 8.8's fallback applied, supersession no longer reaches a compressed
        chunk. The input is then the uncompressed delete time, which 6.5
        also records, and the 30 s floor will almost certainly hold.
  - [x] `TICK_TRADE_CHUNK_INTERVAL`: the interval verdict in the `queries`
        report. If it changes, 9.4 applies.
  - [x] Connect timeout, keepalives, `TICK_SUBMIT_RESOLVE_AGE`: kept.
  - [x] Success: a short table in a scratch note: constant, old, new, rule,
        source report.

- [x] **9.2 Apply and re-document (effort 2)**
  - [x] Edit each value that changes. Rewrite **every** listed docstring,
        including the unchanged ones, to name its measurement and report file.
  - [x] Run the unit tier; fix any test that pins an old value by reference,
        not by a copy of the number.
  - [x] Commit: `refactor: re-set tick constants from the 226 proof`.
  - [x] Success: no docstring says "conservative until 226 measures".

- [x] **9.3 Re-gate ingest throughput under the new constants (effort 2)** (225 load test passes)
  - [x] Run the 225 load test:
        `uv run python scripts/run_tests.py load --
        test/load/test_225_tick_ingest_nfr.py`.
  - [x] Success: it passes (unit time at most 120 s, event-loop gap at most
        250 ms) with the re-set worker count and batch budget. A failure goes
        to the PM before Section 10.

- [x] **9.4 Apply a changed chunk interval (effort 3)** (not needed)
  - [x] Only if 9.1 changed `TICK_TRADE_CHUNK_INTERVAL`. Migrations are
        append-only, so add `tick_008_trade_chunk_interval`, which calls
        `set_chunk_time_interval('tick_trade', <new interval>)` rendered from the
        constant. It applies to `trading_tick` in 10.3 while that table is
        still empty, so the rebuild there is the load itself.
  - [x] In this task, add `tick_008` to the migration test file 8.3 created,
        and update the tests that pin the newest tick migration.
  - [x] Commit the migration with its tests: `feat: add tick_008 chunk
        interval migration`.
  - [x] If the interval is unchanged, tick this task with "not needed".
  - [x] Success: either "not needed", or `tick_008` applies twice without error
        and the information view shows the new interval.

---

## Section 10 — Production rebuild on `trading_tick` (TD 4, walkthrough 6)

- [x] **10.1 Implement the `final` step (effort 3)**
  - [x] On `trading_tick`: compress every eligible chunk (`compress_chunk` over
        `show_chunks(older_than => …)`, `if_not_compressed`); run the query set
        on the final layout; run `coverage` over both ranges; report table
        sizes. Read-only apart from the compression.
  - [x] Refuse below the `/data` free-space floor.
  - [x] The compression sits behind its own check that the connection's
        database is the one named in `MT_TICK_DB_URL`.
  - [x] Success: step implemented; a re-run finishes any chunks left over.

- [x] **10.2 Test `final`'s production write guard (effort 2)**
  - [x] Unit test: `final` refuses to compress when the connection's database
        is not the one named in `MT_TICK_DB_URL`, and refuses below the
        free-space floor. A throwaway-database fixture stands in; the test
        never reads the production URL variable.
  - [x] Success: tests pass; a wrong database name makes them fail.

- [x] **10.3 Migrate and load `trading_tick` (effort 3)**
  - [x] `uv run mt data migrate apply --track tick` (maintenance URL of
        `trading_tick`); confirm it applies through the newest tick migration (`tick_007`, or
        `tick_008` if 9.4 added it).
  - [x] `adopt` each of the six job directories under `/data/tick-archive`.
  - [x] `uv run mt data tick pass --estimate-only`. If it plans anything, stop
        and report it to the PM.
  - [x] `time uv run mt data tick ingest`.
  - [x] Success: migration through the newest tick migration; pass exit 0 with nothing
        planned; ingest exit 0 with 78 ingested and 0 failed. Record the wall
        time of each command, because the total is 227's input.

- [x] **10.4 Run `final` (effort 2)**
  - [x] Run `uv run python scripts/proof_226_tick.py final`.
  - [x] Only if a Q1–Q4 bound is missed: write a new migration (the next free
        `tick_00N`; `tick_007` and `tick_008` are already applied and never
        edited) adding the index or read-side aggregate that fixes it. Add it
        to the migration test file and the newest-migration pins, run the
        integration tier, commit it (`feat: add tick_00N <what> for query
        latency`), apply it to `trading_tick`, and re-run `final`.
  - [x] Success: every eligible chunk compressed, Q1–Q4 at or under 1 s,
        coverage `ok` on both ranges, 27,691,412 rows in `tick_trade`. Commit
        the report: `docs: add 226 final proof report`.

- [x] **10.5 Load test for query latency on compressed data (effort 3)**
  - [x] Create `test/load/test_226_tick_query_nfr.py` in the style of
        `test_225_tick_ingest_nfr.py`: for each range (the largest real
        adopted `trades` day and the largest `tbbo` day from
        `/data/tick-archive`) ingest it into a fixture database migrated
        through the newest tick migration, compress its chunk, then run Q1–Q4. It fails, never skips,
        if the archive file is absent, and never reads the production URL
        variable.
  - [x] Bound: each of Q1–Q4 at or under 1 s warm. Docstring names the gate
        (`MT_RUN_LOAD_TESTS=1`) and cites the `final` report.
  - [x] Run it: `uv run python scripts/run_tests.py load --
        test/load/test_226_tick_query_nfr.py`. It is not wired into CI (CI has
        no test job; slice 907). Commit: `test: add compressed-chunk query
        latency load test`.
  - [x] Success: the load test passes; the load tier's
        `test_load_tier_never_references_prod_db_url` still passes.

---

## Section 11 — Re-verify and tear down the proof database (TD 2)

- [x] **11.1 Implement `drop-proof` (effort 2)**
  - [x] Connect to `trading_tick` with the maintenance URL (a database cannot
        drop itself). The target is the constant `TICK_PROOF_DB_NAME`, never a
        parameter or a value read from a URL. Refuse if that name equals the
        database named in `MT_TICK_DB_URL`.
  - [x] Success: step implemented.

- [x] **11.2 `drop-proof` tests (effort 2)**
  - [x] Unit test both refusals: a non-constant target, and a name equal to
        the production URL's database.
  - [x] Success: tests pass.

- [x] **11.3 `archive-check` step and test (effort 2)**
  - [x] Add a read-only harness step `archive-check` (an addition to TD 2's
        table). For each job directory it reads `manifest.json` (a JSON file
        listing each file's name and `sha256:`-prefixed hash; `sha256sum -c`
        cannot parse it) and compares each listed file's hash from
        `tick.hashing.sha256_file`. `adopt_files._read_manifest` is private, so
        `archive-check` parses `manifest.json` itself (job id, file names,
        hashes) and leaves `adopt_files.py` unchanged. It opens nothing for
        writing.
  - [x] Add a unit test: a temp directory with a manifest and a file whose hash
        is correct passes; one byte changed fails. The manifest in the test copies
        the shape of a real one under `/data/tick-archive` (`files` entries with
        `filename`, `size`, `hash`, `urls`).
  - [x] Success: tests pass. Commit `drop-proof`, `archive-check` and their
        tests: `feat: add proof harness drop-proof and archive-check steps`.
        Neither step is run until 13.2.

---

## Section 12 — `TICK_UNIVERSE`, status and the Kalshi contract diff (TD 9, TD 10)

- [x] **12.1 Decide the tier from the reports (effort 2)**
  - [x] Read the `size` and `layouts` reports. Choose `trades` or `tbbo` for
        ES by the go/no-go criteria (bytes stored, and the value of the quote
        at each trade). The PM can change it at review; it is one edit.
  - [x] Success: the choice and reason are in a scratch note for 13.1.

- [x] **12.2 Edit `TICK_UNIVERSE` for ES (effort 2)** (wanted start is 2024-11-02, not 11-01, recorded in the go/no-go)
  - [x] Set the tier and `start`/`end` to the range already held at that tier.
        For `tbbo` that is 2024-11-01 to 2025-01-01.
  - [x] Update any test that pins the universe, by reference to the entry.
  - [x] Success: `uv run mt data tick pass --estimate-only` plans nothing to
        buy; `uv run mt data tick status` reads caught up for ES with the tier
        named; the other tier's sessions are listed `complete`.

- [x] **12.3 Coverage over both ranges (effort 1)**
  - [x] `mt data tick coverage --start 2024-08-30 --end 2025-01-01`.
  - [x] Success: `ok` on every session of both ranges.

- [x] **12.4 Kalshi contract diff (effort 2)**
  - [x] Re-run the Kalshi parity test.
  - [x] Diff the tick and Kalshi contract files. Absorb or record each
        difference; the only pass-code change is verify's decode-error mapping.
  - [x] Success: parity test passes; the diff result is written in the
        go/no-go (13.1).
  - [x] Commit: `feat: set ES tick tier and range in TICK_UNIVERSE`.

---

## Section 13 — The go/no-go document (TD 9)

- [x] **13.1 Write `user/analysis/226-analysis.tick-proof-go-no-go.md` (effort 4)**
  - [x] `docType: analysis` front matter per `file-naming-conventions.md`
        (create `user/analysis/`).
  - [x] The measured table: every row of TD 3 with its value, pass or decision,
        and the report file it came from.
  - [x] One section per decision with a recommendation and evidence:
        tier (`trades` or `tbbo`); spreads (parent including spreads, or
        outrights only, from the measured spread share in both tiers); GC
        (from the free estimate, run `mt data tick estimate` for `ES.FUT` and
        `GC.FUT`, times the measured compressed bytes per row); Standard-plan
        subscription (a technical go or no-go; timing stays the PM's).
  - [x] Settled by measurement: chunk interval, layout, constants, intra-unit
        checkpoints (Future Work item 3, closed with the number), tick cluster
        memory (buffer hit ratio and resident memory; confirm or lower
        `shared_buffers`), contention, the space-partitioning skew evidence, and
        the rebuild cost handed to 227.
  - [x] Record the Kalshi contract diff result and the capacity projection,
        labelled as a projection.
  - [x] Realtime paths check (TD 10): state that neither path A (assemble from
        realtime) nor path B (historical with delay) is ruled out, naming the
        decisions that keep each open, or name any decision that rules one out.
  - [x] API question (TD 10): "Does this belong in the API?" answered: no new
        surface, the go/no-go is a document and the harness a one-off script.
  - [x] If the compressed-chunk fallback (8.8) was used, record it here.
  - [x] Success: every number names its report; each of the four decisions has a
        recommendation.

- [x] **13.2 Drop the proof database, verify the archive (effort 1)**
  - [x] Run `drop-proof` only now, after the go/no-go cites every report.
  - [x] `psql "$MT_TICK_MAINTENANCE_URL" -Atc "SELECT datname FROM pg_database
        ORDER BY 1"`, then run `archive-check`.
  - [x] Success: `trading_tick_proof` is gone; only `trading_tick` and the
        cluster's own databases remain; every listed archive file verifies.
  - [x] Commit the go/no-go: `docs: add 226 tick proof go/no-go`.

- [x] **13.3 Apply any memory-setting change (effort 2)** (memory settings confirmed)
  - [x] If the go/no-go confirmed `shared_buffers` and the memory block as they
        are, record "unchanged" and tick this task.
  - [x] If it changed any value: edit the memory block at the top of
        `scripts/provision_tick_cluster.sh`, run `--check`, and give the PM the
        one command to re-run it (it restarts only `postgresql@17-tick`).
        Confirm the new values with `SHOW`.
  - [x] Success: the cluster's live settings equal the go/no-go's, or
        "unchanged" is recorded.

---

## Section 14 — Documents and final validation

- [x] **14.1 Contract, plan and architecture docs (effort 3)**
  - [x] Data-correctness contract: 226's part in the I11 row (coverage proved
        on both ranges in production), I12 (supersession and overlap verified on
        compressed chunks) and I13 (session check on every unit).
  - [x] Slice plan Notes: the statements this design supersedes, per the
        design's list. The 233 entry gains the minute-pass overlap measurement.
  - [x] Architecture Revision Log, naming the paragraphs it amends:
        "Cross-source arbitration" and "Proof on existing data" (contention
        moves to the Kalshi pass); "Pass form" and "Delivery mode and
        retention" (job timing from account records); "Storage" (space
        partitioning decided from the disk layout); and the harness's one-off
        production reads, which add no product edge.
  - [x] Amend the slice design where these tasks differ from it, so the
        design stays the record: TD 2's step table gains `archive-check`; its
        archive re-check uses the manifest's `sha256:` hashes, not
        `sha256sum -c`; the proof URL variables are `MT_PROOF_226_DB_URL` and
        `MT_PROOF_226_MAINTENANCE_URL` (TD 1 and the component diagram); the
        walkthrough's `.env` export step.
  - [x] Success: each paragraph named is amended; no stale statement remains.

- [x] **14.2 CHANGELOG and README (effort 2)**
  - [x] CHANGELOG entry for the slice.
  - [x] README storage paragraph naming the tick cluster and its layout.
  - [x] Confirm the `17/main` configuration checksums equal those recorded in
        3.6.
  - [x] Success: both docs updated; checksums match.
  - [x] Commit: `docs: record slice 226 in contract, plan, architecture and
        changelog`.

- [x] **14.3 Full validation (effort 2)**
  - [x] Unit tier clean (all tiers through `scripts/run_tests.py`). Integration tier: only the known baseline failures,
        with the tick-track tests that pin the newest migration updated for
        `tick_007`, and `tick_008` if it exists (8.3, 9.4).
  - [x] mypy (src kalshi paths and tests in one invocation) and ruff clean on
        touched files; shellcheck clean on the provisioning script.
  - [x] `git grep` the diff for credentials and `postgresql://` URLs carrying a
        password. None.
  - [x] Re-run the load tier gate for both tick load tests (9.3, 10.5).
  - [x] Check the Technical and Integration Requirements: the guard test and
        the `.env` writer test exist and pass; the `17/main` configuration
        checksums match 3.6's; `systemctl list-timers` (read only) shows the
        Kalshi, minute, daily and health timers scheduled and none stopped;
        every document listed under Docs in the design is updated.
  - [x] Walk the Verification Walkthrough (design, steps 1–9) and tick each
        expected result against Success Criteria 1–9. The walkthrough's
        `set -a; . ./.env` is replaced by the harness's own dotenv loading
        (2.2); use `scripts/run_tests.py` for tests.
  - [x] Success: every criterion holds; all work is committed on the slice
        branch.
