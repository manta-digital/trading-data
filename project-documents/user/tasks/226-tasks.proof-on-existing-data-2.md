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

Part 1 (Sections 0–7) holds the fix, the cluster, the harness and the uncompressed measurements. This part holds the layout, `tick_007`, the rebuild and the go/no-go. Part 1's reports and constants are inputs here.

---

## Section 8 — `tick_007_trade_columnstore` (TD 4, TD 5)

- [ ] **8.1 Record the layout decision (effort 1)**
  - [ ] Read the `layouts` report. Set `TICK_TRADE_SEGMENT_BY` and
        `TICK_TRADE_ORDER_BY` to the winner. Docstrings cite the report file.
  - [ ] Success: constants hold the chosen layout; no provisional wording.

- [ ] **8.2 Write the migration (effort 3)**
  - [ ] Append `tick_007_trade_columnstore` to `migrations/tick.py` (tick
        track, maintenance credential), idempotent like its siblings.
  - [ ] Step 1: `tick_now_ns()` (`BIGINT`, `STABLE`, nanoseconds since the
        epoch) and `set_integer_now_func('tick_trade', 'tick_now_ns')`.
  - [ ] Step 2: `ALTER TABLE tick_trade SET (timescaledb.enable_columnstore,
        segmentby, orderby)` rendered from the constants, as the chunk interval
        is today.
  - [ ] Step 3: `add_columnstore_policy('tick_trade', after => <ns from
        TICK_TRADE_COMPRESS_AFTER>)`, `if_not_exists => TRUE`.
  - [ ] Check the TimescaleDB 2.29 syntax with the tool guide
        (`tool-guides/timescaledb/`) and context7 before writing it.
  - [ ] Success: the migration applies to an empty tick test database twice
        without error.

- [ ] **8.3 Migration tests (effort 3)**
  - [ ] Integration test (TD 5, Migration row): `tick_007` on an empty database
        and on a populated uncompressed one. Settings read back from the
        TimescaleDB information views match the constants; the policy exists;
        `tick_now_ns()` is within a second of `now()`.
  - [ ] Update the migration-chain test that pins the last migration id.
  - [ ] Add the `tick_007` row to the migrations README.
  - [ ] Success: tests pass; no other integration test newly fails.

- [ ] **8.4 Compressed-chunk tests: supersession and overlap (effort 4)**
  - [ ] Create `test/integration/data/test_tick_compressed.py`. Compress with
        `compress_chunk` explicitly; the test cluster has no scheduler.
  - [ ] Supersession: ingest a `trades` unit, compress its chunk, ingest the
        `tbbo` unit for the same day. One transaction deletes the `trades` rows
        and loads the `tbbo` rows; counts check passes; `coverage` reads `ok`.
  - [ ] Overlap: a compressed chunk already holds a unit's rows; a second
        current unit with the same rows raises `UniqueViolation` and the unit
        fails with `overlap:`.
  - [ ] Success: both pass. If either fails, fix 225's code in this slice (a
        failure is never fixed by dropping compression); if no fix works, stop
        and report, because the design then falls back to compressing only past
        a settled age.

- [ ] **8.5 Compressed-chunk tests: `tick_app` and coverage (effort 4)**
  - [ ] Ingest as `tick_app`: the application role inserts through ingest into
        a compressed chunk. If it fails on internal compressed tables, extend
        `provision_tick_roles.sql` with the minimal grant and add a test for it.
  - [ ] Coverage on a partial chunk: rows inserted into a compressed chunk
        before recompression give a raw count equal to the ledger; after
        `compress_chunk` the count still equals the ledger.
  - [ ] Success: all five TD 5 rows pass.

- [ ] **8.6 Section checkpoint**
  - [ ] Run unit tier, integration tier, mypy and ruff on touched files.
  - [ ] Commit: `feat: add tick_007 columnstore migration and compressed-chunk
        tests`.
  - [ ] Success: only the known baseline failures remain.

---

## Section 9 — Constants re-set from measurement (TD 6)

- [ ] **9.1 Decide each constant by its rule (effort 2)**
  - [ ] `TICK_INGEST_WORKERS`: `workers` report, plus the contention verdict
        (4 must stay within the bound).
  - [ ] `TICK_DECODE_BATCH_BYTES`: `batch` report.
  - [ ] `TICK_WAIT_BUDGET_SECONDS`: max(1,800, 2 x slowest account job).
  - [ ] `TICK_POLL_INTERVAL_SECONDS`: kept unless the fastest job finishes
        under 15 s.
  - [ ] `TICK_INGEST_LOCK_TIMEOUT_SECONDS`: at least 4 x the slowest
        compressed-chunk supersession delete, never below 30.
  - [ ] `TICK_TRADE_CHUNK_INTERVAL`: the interval verdict in the `queries`
        report. If it changes, add the migration and plan the rebuild.
  - [ ] Connect timeout, keepalives, `TICK_SUBMIT_RESOLVE_AGE`: kept.
  - [ ] Success: a short table in a scratch note: constant, old, new, rule,
        source report.

- [ ] **9.2 Apply and re-document (effort 2)**
  - [ ] Edit each value that changes. Rewrite **every** listed docstring,
        including the unchanged ones, to name its measurement and report file.
  - [ ] Run the unit tier; fix any test that pins an old value by reference,
        not by a copy of the number.
  - [ ] Commit: `refactor: re-set tick constants from the 226 proof`.
  - [ ] Success: no docstring says "conservative until 226 measures".

---

## Section 10 — Production rebuild on `trading_tick` (TD 4, walkthrough 6)

- [ ] **10.1 Implement the `final` step (effort 3)**
  - [ ] On `trading_tick`: compress every eligible chunk (`compress_chunk` over
        `show_chunks(older_than => …)`, `if_not_compressed`); run the query set
        on the final layout; run `coverage` over both ranges; report table
        sizes. Read-only apart from the compression.
  - [ ] Refuse below the `/data` free-space floor.
  - [ ] The compression sits behind its own check that the connection's
        database is the one named in `MT_TICK_DB_URL`.
  - [ ] Success: step implemented; a re-run finishes any chunks left over.

- [ ] **10.2 Migrate and load `trading_tick` (effort 3)**
  - [ ] `uv run mt data migrate apply --track tick` (maintenance URL of
        `trading_tick`); confirm it applies through `tick_007`.
  - [ ] `adopt` each of the six job directories under `/data/tick-archive`.
  - [ ] `uv run mt data tick pass --estimate-only`. If it plans anything, stop
        and report it to the PM.
  - [ ] `time uv run mt data tick ingest`.
  - [ ] Success: migration through `tick_007`; pass exit 0 with nothing
        planned; ingest exit 0 with 78 ingested and 0 failed. Record the wall
        time of each command, because the total is 227's input.

- [ ] **10.3 Run `final` (effort 2)**
  - [ ] Success: every eligible chunk compressed, Q1–Q4 at or under 1 s,
        coverage `ok` on both ranges, 27,691,412 rows in `tick_trade`. A
        Q1–Q4 miss gets an index or read-side aggregate in `tick_007` and is
        re-measured. Commit the report: `docs: add 226 final proof report`.

---

## Section 11 — Re-verify and tear down the proof database (TD 2)

- [ ] **11.1 Implement `drop-proof` (effort 2)**
  - [ ] Connect to `trading_tick` with the maintenance URL (a database cannot
        drop itself). The target is the constant `TICK_PROOF_DB_NAME`, never a
        parameter or a value read from a URL. Refuse if that name equals the
        database named in `MT_TICK_DB_URL`.
  - [ ] Success: step implemented.

- [ ] **11.2 `drop-proof` tests (effort 2)**
  - [ ] Unit test both refusals: a non-constant target, and a name equal to
        the production URL's database.
  - [ ] Success: tests pass.

- [ ] **11.3 Hold the drop until the go/no-go has its numbers (effort 1)**
  - [ ] Do not run `drop-proof` yet. Task 13.2 runs it, after the go/no-go
        cites every report. Nothing waits on a clock: it is a reorder only.
  - [ ] Success: this task is closed by 13.2. Commit the step and its tests:
        `feat: add proof harness drop-proof step`.

---

## Section 12 — `TICK_UNIVERSE`, status and the Kalshi contract diff (TD 9, TD 10)

- [ ] **12.1 Decide the tier from the reports (effort 2)**
  - [ ] Read the `size` and `layouts` reports. Choose `trades` or `tbbo` for
        ES by the go/no-go criteria (bytes stored, and the value of the quote
        at each trade). The PM can change it at review; it is one edit.
  - [ ] Success: the choice and reason are in a scratch note for 13.1.

- [ ] **12.2 Edit `TICK_UNIVERSE` for ES (effort 2)**
  - [ ] Set the tier and `start`/`end` to the range already held at that tier.
        For `tbbo` that is 2024-11-01 to 2025-01-01.
  - [ ] Update any test that pins the universe, by reference to the entry.
  - [ ] Success: `uv run mt data tick pass --estimate-only` plans nothing to
        buy; `uv run mt data tick status` reads caught up for ES with the tier
        named; the other tier's sessions are listed `complete`.

- [ ] **12.3 Coverage over both ranges (effort 1)**
  - [ ] `mt data tick coverage --start 2024-08-30 --end 2025-01-01`.
  - [ ] Success: `ok` on every session of both ranges.

- [ ] **12.4 Kalshi contract diff (effort 2)**
  - [ ] Re-run the Kalshi parity test.
  - [ ] Diff the tick and Kalshi contract files. Absorb or record each
        difference; the only pass-code change is verify's decode-error mapping.
  - [ ] Success: parity test passes; the diff result is written in the
        go/no-go (13.1).
  - [ ] Commit: `feat: set ES tick tier and range in TICK_UNIVERSE`.

---

## Section 13 — The go/no-go document (TD 9)

- [ ] **13.1 Write `user/analysis/226-analysis.tick-proof-go-no-go.md` (effort 4)**
  - [ ] `docType: analysis` front matter per `file-naming-conventions.md`
        (create `user/analysis/`).
  - [ ] The measured table: every row of TD 3 with its value, pass or decision,
        and the report file it came from.
  - [ ] One section per decision with a recommendation and evidence:
        tier (`trades` or `tbbo`); spreads (parent including spreads, or
        outrights only, from the measured spread share in both tiers); GC
        (from the free estimate, run `mt data tick estimate` for `ES.FUT` and
        `GC.FUT`, times the measured compressed bytes per row); Standard-plan
        subscription (a technical go or no-go; timing stays the PM's).
  - [ ] Settled by measurement: chunk interval, layout, constants, intra-unit
        checkpoints (Future Work item 3, closed with the number), tick cluster
        memory (buffer hit ratio and resident memory; confirm or lower
        `shared_buffers`), contention, the space-partitioning skew evidence, and
        the rebuild cost handed to 227.
  - [ ] Record the Kalshi contract diff result and the capacity projection,
        labelled as a projection.
  - [ ] Success: every number names its report; each of the four decisions has a
        recommendation.

- [ ] **13.2 Drop the proof database and verify the archive (effort 1)**
  - [ ] Run `uv run python scripts/proof_226_tick.py drop-proof`.
  - [ ] `psql "$MT_TICK_MAINTENANCE_URL" -Atc "SELECT datname FROM pg_database
        ORDER BY 1"`.
  - [ ] `sha256sum -c` against each job's `manifest.json`.
  - [ ] Success: `trading_tick_proof` is gone; only `trading_tick` and the
        cluster's own databases remain; every archive file verifies.
  - [ ] Commit the go/no-go: `docs: add 226 tick proof go/no-go`.

---

## Section 14 — Documents and final validation

- [ ] **14.1 Contract, plan and architecture docs (effort 3)**
  - [ ] Data-correctness contract: 226's part in the I11 row (coverage proved
        on both ranges in production), I12 (supersession and overlap verified on
        compressed chunks) and I13 (session check on every unit).
  - [ ] Slice plan Notes: the statements this design supersedes, per the
        design's list. The 232 entry gains the minute-pass overlap measurement.
  - [ ] Architecture Revision Log, naming the paragraphs it amends:
        "Cross-source arbitration" and "Proof on existing data" (contention
        moves to the Kalshi pass); "Pass form" and "Delivery mode and
        retention" (job timing from account records); "Storage" (space
        partitioning decided from the disk layout); and the harness's one-off
        production reads, which add no product edge.
  - [ ] Success: each paragraph named is amended; no stale statement remains.

- [ ] **14.2 CHANGELOG and README (effort 2)**
  - [ ] CHANGELOG entry for the slice.
  - [ ] README storage paragraph naming the tick cluster and its layout.
  - [ ] Confirm the `17/main` configuration checksums equal those recorded in
        3.6.
  - [ ] Success: both docs updated; checksums match.
  - [ ] Commit: `docs: record slice 226 in contract, plan, architecture and
        changelog`.

- [ ] **14.3 Full validation (effort 2)**
  - [ ] Unit tier clean. Integration tier: only the known baseline failures,
        with the chain test updated for `tick_007`.
  - [ ] mypy (src kalshi paths and tests in one invocation) and ruff clean on
        touched files; shellcheck clean on the provisioning script.
  - [ ] `git grep` the diff for credentials and `postgresql://` URLs carrying a
        password. None.
  - [ ] Walk the Verification Walkthrough (design, steps 1–9) and tick each
        expected result against Success Criteria 1–9.
  - [ ] Success: every criterion holds; all work is committed on the slice
        branch.
