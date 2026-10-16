---
docType: tasks
slice: off-tape-prints
project: trading-data
lld: user/slices/171-slice.off-tape-prints.md
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [163, 921]
interfaces: [172, 182, 186, 190]
projectState: >
  Slice design committed and reviewed (CONCERNS, all findings addressed).
  Production runs v0.23.2 with migration 061 applied; the minute migration
  chain ends at 061. minute_ohlcv is a compressed hypertable (1,215 compressed
  chunks, 82 GB, 7-day chunks, segmentby symbol). A 40-symbol prototype of the
  detector exists outside the repository. Nothing in this slice is built.
dateCreated: 20261016
dateUpdated: 20261016
status: not_started
---

# Tasks: Off-Tape Prints

## Context Summary

- Working on slice 171. EODHD 1m bars include tape-ineligible trades
  (late-reported, average-price, prior-reference-price). They appear as tall
  isolated wicks, mostly in extended hours, and survive into every rollup.
- This slice:
  - adds a nullable `off_tape smallint` bitmask to `minute_ohlcv` (migration
    062). `NULL` = not evaluated, `0` = clean. Raw values are never modified;
  - adds a pure detector `mark_day` and one marking routine `mark_days`, which
    both minute insert paths and the history sweep call;
  - adds `mt data off-tape sweep` to mark all history, chunk by chunk;
  - adds `prints=all|eligible` at 1m only to `get_minute_data` and
    `/api/v1/bars` (default `all`: no client sees a change);
  - adds two `mt data health` checks and the documentation amendments.
- **The design is the specification.** Detector rule, eligible-value rule,
  sweep steps and failure table: see the slice design's Technical Decisions.
  Tasks reference it rather than restating it.
- **Key invariant:** a bar's mask is a function of its symbol's whole ET day
  (04:00–20:00 ET) and nothing else. The insert path re-marks every ET day a
  frame touches; the sweep marks whole ET days. Both produce identical masks.
- Not in this slice: 5m+ eligible rollups and the default flip (slice 172),
  per-bar marks in API responses, any change to stored raw values.
- **After merge (Phase 7, not a task here):** the release applies migration
  062, and the production sweep runs per runbook 100. Success criterion 6
  (no `NULL` after the sweep, both health checks pass) is verified then.
- Next planned slice: 172 (Eligible Rollups).

**Conventions for every task below**

- Run from the repository root. Confirm with `pwd` before file or shell work.
- **Target branch:** read `cf config get git.integration_branch`; empty means
  `main`. Work on `171-slice.off-tape-prints`, forked from the target per the
  Git Rules.
- **Tests never read the production URL.** Fixture cutting and the universe
  measurement are developer-run scripts under `scripts/`, read-only.
- Integration tests need `MT_TIMESCALE_TEST_URL` exported from `.env` (strip
  the quotes). Run the unit, integration and load tiers separately.
- `ruff format` only the files the task touched. Before each commit, run
  `git diff <target> --stat` and look for deletions in files you did not mean
  to change.
- mypy: one invocation covering the src kalshi paths plus tests, never a
  narrower run (narrower runs report false errors).
- Every comparison value (bits, modes, thresholds, bounds, lock timeout, the
  422 message) is defined once in `src/manta_trading/constants.py`.
- Commit messages use the project's semantic prefixes and end with the
  attribution lines in force for the session.

---

## Section 1 — Fixtures, detector, calibration (effort 3/5)

- [ ] **1.1 Cut detector fixtures from production bars**
  - [ ] Write `scripts/cut_off_tape_fixtures.py` (developer-run, read-only;
        `scripts/cut_tick_fixtures.py` is the docstring/format model). It reads
        whole ET days of `minute_ohlcv` (`time, symbol, open, high, low, close,
        volume`) and writes one CSV per symbol-day to
        `test/fixtures/off_tape/{SYMBOL}_{YYYY-MM-DD}.csv`.
  - [ ] Cut every fixture listed in the design's Technical Requirements:
    1. SPY 2026-09-08, SPY 2026-09-11, AVGO 2026-06-03, MSFT 2026-08-05;
    2. the earnings set: MSFT 07-29, AAPL 07-30, SMCI 07-21, PLTR 08-03,
       TSLA 07-22, MU 06-24, LULU 09-03, AMZN 07-30, CRM 08-26, AMD 08-04,
       NFLX 07-16, UPST 08-04, META 07-29 (all 2026; AVGO 06-03 is in item 1);
    3. dense-print session MSFT 2026-06-01;
    4. a winter day whose after-hours tail crosses UTC midnight (any liquid
       symbol, a date between November and March where bars exist after
       00:00 UTC);
    5. a quiet regular-hours day for SPY. Choose the day by running the
       prototype rule (design "The detector rule") and taking a day with zero
       marks between 09:30 and 16:00 ET; record the choice in the script
       docstring.
  - [ ] Add `test/fixtures/off_tape/README.md` naming each file, its source
        query date and why it exists. No synthetic-only fixtures.
  - [ ] Success: every listed file exists, holds the whole ET day, and the
        script reruns to byte-identical output.

- [ ] **1.2 Define constants**
  - [ ] In `constants.py`: `OffTape` (`IntFlag`: `OPEN=1, HIGH=2, LOW=4,
        CLOSE=8, ALL=15`), `PrintMode` (`StrEnum`: `ALL="all"`,
        `ELIGIBLE="eligible"`), `OFF_TAPE_WINDOW_BARS = 10`,
        `OFF_TAPE_SCALE_MULTIPLE = 10`, `OFF_TAPE_MIN_FRACTION = 0.003`,
        and the ET day bounds (04:00, 20:00, `America/New_York`) if no
        existing constant already holds them (search first; reuse if found).
  - [ ] Each constant has a one-line comment naming slice 171.
  - [ ] Success: `ruff` and `mypy` clean.

- [ ] **1.3 Implement the detector**
  - [ ] Create `src/manta_trading/data/quality/off_tape.py` with
        `mark_day(bars: pd.DataFrame) -> pd.Series` (int masks, same index):
        one symbol, one ET day, rows ordered by time. Pure, no I/O.
  - [ ] Implement exactly the rule in the design ("The detector rule"):
        by-row before/after medians of `K` closes, one-sided at the day's
        edges, larger side's median |Δclose| as scale, `threshold =
        max(M × scale, R × close)`, two-sided envelope, each of o/h/l/c
        marked independently. The prototype `detect4.py` (session scratchpad,
        may be gone) matches this rule; the design is authoritative.
  - [ ] Add a helper `split_et_days(bars) -> Iterator[(date, frame)]` in the
        same module, so every caller assigns days identically.
  - [ ] Raise `ValueError` on more than one symbol or more than one ET day.
  - [ ] Success: module under ~300 lines; `ruff`, `mypy` clean.

- [ ] **1.4 Detector tests**
  - [ ] `test/unit/data/quality/test_off_tape.py`, driven by the 1.1 fixtures:
    1. SPY 09-08: 07:35–07:38 ET values at 770.19 marked; 16:01–16:04 ET highs
       770.3516 marked HIGH; 16:52 and 17:26 ET lows 724.228 marked LOW;
    2. SPY 09-11 17:33 ET high 787.5169 marked; AVGO 06-03 16:15 ET low
       295.4665 marked; MSFT 08-05 16:13 ET low 384.28 marked;
    3. earnings set: nothing marked within three minutes of each jump, except
       AVGO 06-03's 295.47;
    4. quiet day: zero marks in regular hours;
    5. dense session MSFT 06-01 16:40–17:12 ET: marks present (the scale did
       not collapse);
    6. winter fixture: `split_et_days` puts the post-00:00 UTC bars in the
       previous ET day;
    7. a bar whose open or close is marked also has HIGH or LOW marked
       (the design's 8,171-bar observation) on every fixture;
    8. `ValueError` on two symbols and on two ET days.
  - [ ] Success: all pass. **If any fixture assertion fails at the starting
        calibration, do not change the assertion: go to 1.5.**

- [ ] **1.5 Calibrate and record**
  - [ ] Write `scripts/measure_off_tape_share.py` (read-only): runs the
        detector over every symbol for the most recent complete trading week
        and reports marked share (bars with mask ≠ 0), regular-hours marks,
        and the prior-close match rate (marked values equal to one of the
        previous 30 official daily closes, against unmarked values).
  - [ ] Run it. If 1.4 passes at `K=10, M=10, R=0.003`, keep them. If not,
        adjust only `M`/`R`, rerun 1.4 and this script, and record each tried
        set and its result. **If no set passes 1.4, stop and tell the
        Project Manager.**
  - [ ] Set `OFF_TAPE_SHARE_FLOOR` and `OFF_TAPE_SHARE_CEILING` in
        `constants.py` from the measured weekly share: floor = half the lowest
        daily share in the week, ceiling = twice the highest. Comment cites
        the measurement date.
  - [ ] Write `project-documents/user/notes/{YYYY-MM-DD}-171-calibration.md`
        (front matter per `file-naming-conventions.md`): chosen K/M/R, weekly
        and per-day share, regular-hours count, prior-close match rates, the
        earnings-set result, detector throughput (bars/s).
  - [ ] Success: 1.4 passes; constants and note committed together.

- [ ] **1.6 Commit:** `feat: add off-tape print detector with production fixtures`

## Section 2 — Eligible-value SQL (effort 2/5)

- [ ] **2.1 Implement the renderer**
  - [ ] Create `src/manta_trading/market/schema/eligible_sql.py` rendering,
        from `OffTape` only (no literal bits in the module):
        `eligible_low()`/`eligible_high()` (`LEAST`/`GREATEST` over
        `CASE WHEN COALESCE(off_tape,0) & <bit> = 0 THEN <col> END`),
        `eligible_value(col)` (`LEAST(GREATEST(col, lo), hi)`) for each of
        open/high/low/close, and `has_eligible_price()`
        (`COALESCE(off_tape,0) <> 15`). An optional table alias parameter
        lets 172 use it inside a cagg.
  - [ ] Add a Python mirror `eligible_bar(o, h, l, c, mask)` returning the
        clamped values or `None` for an all-marked bar, in `off_tape.py`,
        for tests and reasoning only.
  - [ ] Success: `ruff`, `mypy` clean; no literal `15` or bit values outside
        `constants.py`.

- [ ] **2.2 Renderer tests**
  - [ ] Unit test: rendered text uses `OffTape` values (patch-free check by
        building expected strings from the enum).
  - [ ] Integration test (`test/integration/test_eligible_sql.py`, throwaway
        DB): insert one bar per case — each single bit, HIGH|LOW, OPEN|CLOSE,
        ALL, 0 and `NULL` — and assert the rendered SQL's output equals
        `eligible_bar` for every case; ALL is excluded by the predicate;
        `NULL` behaves as clean.
  - [ ] Success: tests pass.

- [ ] **2.3 Commit:** `feat: add eligible-value SQL renderer`

## Section 3 — Migration 062 (effort 1/5)

- [ ] **3.1 Add the migration**
  - [ ] Append `062_minute_ohlcv_off_tape` to `MINUTE_MIGRATIONS` in
        `market/schema/migrations/minute.py`:
        `ALTER TABLE minute_ohlcv ADD COLUMN IF NOT EXISTS off_tape smallint`
        (nullable, no default, no index). Follow 061's entry shape.
  - [ ] Update `test_chain_ends_at_061` in `test/unit/test_schema_migrations.py`
        to end at 062 and any count comment beside it.
  - [ ] Update `migrations/README.md` if it lists migrations.

- [ ] **3.2 Migration test**
  - [ ] `test/integration/test_migration_062_off_tape.py`: on a throwaway DB
        with at least one compressed chunk, the migration applies, is
        idempotent on rerun, the column is nullable `smallint`, existing rows
        read `NULL`, and an `UPDATE` of `off_tape` on a compressed chunk
        succeeds without changing raw columns.
  - [ ] Success: test passes; unit migration tests pass.

- [ ] **3.3 Commit:** `feat: add migration 062 off_tape column`

## Section 4 — `mark_days` and the insert paths (effort 3/5)

- [ ] **4.1 Implement `mark_days`**
  - [ ] Create `src/manta_trading/data/quality/off_tape_store.py` with
        `mark_days(conn, symbol, et_days) -> int` (rows updated). It reads the
        whole ET days in one `(symbol, time)` range read, runs `mark_day` per
        day, and updates rows whose stored mask differs (`NULL` always
        differs) from a temp table in one statement. Uses the caller's
        connection and transaction; never commits.
  - [ ] Add `et_days_of(times) -> set[date]` beside `split_et_days`, used by
        both insert paths.
  - [ ] Success: `ruff`, `mypy` clean.

- [ ] **4.2 Wire the acquisition insert path**
  - [ ] In `_insert_minute_bars` (`data/acquisition/daemon/minute.py`), after
        the `INSERT ... ON CONFLICT DO NOTHING`, call `mark_days(conn, symbol,
        et_days_of(<frame times>))` inside the same transaction.
  - [ ] On exception: `logger.exception`, re-raise. Trace what the pass does
        with that symbol's write failure and its gap rows today; record it in
        the calibration note under "Insert failure path". It must match the
        design's failure table row 1 (no unevaluated rows committed). **If it
        does not, stop and tell the Project Manager.**

- [ ] **4.3 Wire `write_minute_data_bulk`**
  - [ ] Same call in `TimescaleMinuteDB.write_minute_data_bulk`
        (`market/timescale_minute_db.py`), same transaction, same failure
        handling. Return-value semantics unchanged except that a marking
        failure is a failed write.

- [ ] **4.4 Insert path and agreement tests**
  - [ ] `test/integration/test_off_tape_marking.py`, throwaway DB, real
        fixtures loaded through each path:
    1. every inserted row has non-`NULL` `off_tape`; masks equal `mark_day`
       on the fixture;
    2. raw `open/high/low/close/volume` read back identical to the fixture
       (checksum), including a re-insert of an overlapping frame;
    3. **agreement:** the winter fixture inserted as two frames split at UTC
       midnight equals the same day inserted as one frame, equals the day
       after setting `off_tape = NULL` and calling `mark_days` (the sweep's
       routine);
    4. a later frame adding bars to a day re-marks that whole day;
    5. a detector that raises (monkeypatched) rolls the symbol's insert back:
       zero rows committed.
  - [ ] Success: tests pass; existing daemon and minute-db tests pass.

- [ ] **4.5 Commit:** `feat: mark off-tape prints in both minute insert paths`

## Section 5 — Health checks (effort 2/5)

- [ ] **5.1 Implement the checks**
  - [ ] In `cli/commands/health.py` `gather`, add two `HealthCheck`s over the
        last collected minute session (reuse `select_judged_session` /
        `fetch_candidate_sessions` from `minute_session_mass.py`; do not
        redefine the session):
    1. **off-tape unevaluated:** FAIL with the count when any row in the
       session has `NULL` `off_tape`;
    2. **off-tape share:** FAIL when the share of rows with a non-zero mask
       lies outside `[OFF_TAPE_SHARE_FLOOR, OFF_TAPE_SHARE_CEILING]`,
       naming the share and bounds.
  - [ ] Keep `health.py` near its current size; if it passes ~300 lines, put
        the two checks in `cli/commands/health_off_tape.py` (the
        `health_tick.py` pattern).

- [ ] **5.2 Health tests**
  - [ ] Unit tests: each check's PASS, both FAIL states (NULL present; share
        below floor; share above ceiling), and rendered lines.
  - [ ] Success: tests pass; existing health tests pass.

- [ ] **5.3 Commit:** `feat: add off-tape health checks`

## Section 6 — History sweep (effort 4/5)

- [ ] **6.1 Implement chunk selection and dry run**
  - [ ] Create `market/maintenance/off_tape_sweep.py` (`rechunk.py` and
        `cagg_repair.py` are the structure models). List `minute_ohlcv`
        chunks oldest first with compressed state, and select those holding
        any `NULL` `off_tape` (resume = data state, no progress table).
  - [ ] Defer the newest chunk while `pass_runs` shows a running minute
        pass (`PassRunRepository`, `PassKind`).
  - [ ] `--dry-run` prints chunks remaining, compressed/uncompressed counts
        and whether the newest chunk is deferred; writes nothing.

- [ ] **6.2 Implement the per-chunk rewrite**
  - [ ] Per the design's Sweep steps: read the chunk's rows widened to whole
        ET days at both edges, compute masks with `mark_day` per symbol-day
        (via `split_et_days`), and write only rows inside the chunk.
  - [ ] Compressed chunk: one transaction with
        `SET LOCAL lock_timeout = OFF_TAPE_SWEEP_LOCK_TIMEOUT` (new constant):
        raw checksum → `decompress_chunk` → `UPDATE` every row from a temp
        table → checksum → `compress_chunk`. Uncompressed: the `UPDATE` only.
  - [ ] Share the mask-write SQL with `mark_days` (extract a helper in
        `off_tape_store.py`); do not duplicate it.
  - [ ] Failure table (design "Failure handling"): lock timeout → skip,
        retry once at end of run, exit non-zero naming unlocked chunks;
        checksum mismatch → roll back, stop, exit non-zero naming the chunk;
        any other exception → `logger.exception`, roll back, re-raise.

- [ ] **6.3 Add the CLI**
  - [ ] Create `cli/commands/off_tape.py` with a Typer app registered in
        `data.py` as `mt data off-tape` (the `health` registration is the
        model). Command `sweep` with `--maintenance-url` (required, never read
        from the environment inside the tool) and `--dry-run`. Logs one line
        per chunk: rows, marked share, elapsed.

- [ ] **6.4 Sweep integration tests**
  - [ ] `test/integration/test_off_tape_sweep.py`, throwaway DB with fixture
        bars across at least three chunks, two compressed:
    1. after the sweep: no `NULL`, chunks recompressed, raw checksums
       unchanged, masks equal the insert-path masks for the same days;
    2. resume: an exception injected after the first chunk's `UPDATE` rolls
       that chunk back; a rerun completes it and skips done chunks;
    3. lock timeout: a second connection holding a lock on one chunk makes
       the run skip it, retry it, and exit non-zero naming it;
    4. deferred newest chunk: a fake running minute `pass_runs` row leaves
       the newest chunk `NULL` and the rest done;
    5. checksum mismatch (monkeypatched checksum) rolls back and exits
       non-zero naming the chunk;
    6. `mt data accounting` counts, `data_status` and the coverage index read
       the same before and after the sweep;
    7. `--dry-run` writes nothing.
  - [ ] Success: tests pass.

- [ ] **6.5 Measure one production-sized chunk**
  - [ ] On the test cluster, load one full production chunk's worth of bars
        (copy one 7-day chunk's rows from production read-only via a
        developer-run script, or the largest available), compress it, and
        time the sweep of that chunk. Record elapsed, rows/s and the
        projected full-history duration in the calibration note.
  - [ ] **If the projection exceeds the gap between weekly minute passes,
        stop and tell the Project Manager** (workers are a design change).
  - [ ] Success: measurement recorded.

- [ ] **6.6 Commit:** `feat: add resumable off-tape history sweep`

## Section 7 — `prints` at 1m (effort 3/5)

- [ ] **7.1 Read path**
  - [ ] `get_minute_data(..., prints: PrintMode = PrintMode.ALL)` in
        `market/timescale_minute_db.py`. `ELIGIBLE` selects the
        `eligible_sql` expressions and filters by `has_eligible_price()`;
        adjusted-on-read multiplies after the eligible values. `ALL` SQL is
        unchanged. `ELIGIBLE` with a non-1m request raises `ValueError`.
  - [ ] Unit/integration tests: `ALL` output identical to today's on a
        fixture day; `ELIGIBLE` equals `eligible_bar` row by row; all-marked
        minutes absent (price and volume); adjusted values clamp first.

- [ ] **7.2 API route**
  - [ ] `api_server/routes/bars.py`: query parameter `prints: PrintMode =
        PrintMode.ALL`. `ELIGIBLE` with granularity ≠ 1m → 422 with the
        message constant from the design ("prints=eligible is served at 1m
        only until the eligible rollups are built").
  - [ ] `BarsResponse` (`api_server/models/responses.py`) gains `prints`
        echoing the mode served.
  - [ ] Admission, staleness and 404 behaviour unchanged.
  - [ ] Tests in `test/integration/api_server/`: default response has
        `prints: "all"` and today's bars exactly; `prints=eligible` 1m drops
        the SPY 09-08 770.x highs (fixture); `prints=eligible` at 5m → 422
        with the message; invalid value → 422.

- [ ] **7.3 CLI `mt data get` and CSV export**
  - [ ] Add `--prints all|eligible` (default `all`) passed to
        `get_minute_data`; non-1m with `eligible` exits with the same message.
  - [ ] Test: option parses and reaches `get_minute_data`.

- [ ] **7.4 Load test case**
  - [ ] In `test/load/test_187_api_nfr.py`, add a `prints=eligible` 1m case
        at the admission ceiling under the same bound as the existing
        `prints=all` case. Run the bars load tests (allow the tier its full
        time; do not cap under 90 minutes if running the whole tier).
  - [ ] Success: passes; elapsed recorded in the calibration note.

- [ ] **7.5 Commit:** `feat: serve tape-eligible 1m bars via prints parameter`

## Section 8 — Documentation (effort 2/5)

- [ ] **8.1 API references**
  - [ ] Regenerate `docs/api/openapi.json` with `scripts/dump_openapi.py`;
        `scripts/check_api_docs.py` passes.
  - [ ] `docs/api/reference.md` and `docs/api/agents.md`: the `prints`
        parameter, 1m-only rule and 422, the `prints` response field, the
        all-marked-minute absence, and that the default stays `all` until
        slice 172. Add a worked example; `test_api_docs_examples.py` passes.

- [ ] **8.2 Architecture amendments**
  - [ ] `project-documents/user/architecture/140-arch.data-quality-operations.md`:
        dated amendment (2026-10-16 or the implementation date) for the
        `off_tape` column and the read API's `prints`.
  - [ ] `project-documents/user/architecture/120-arch-adjustment-policy.md`
        "Outlier handling — Non-Goal": amend per the design's Special
        Considerations (storage-time removal still forbidden, raw immutable;
        marks are annotations; serving may choose eligible; why this is the
        exchanges' rule, not outlier cleaning). Mark it "pending PM sign-off
        at review".

- [ ] **8.3 Runbook 100 and CHANGELOG**
  - [ ] `project-documents/user/runbooks/100-production-operations.md`: the
        sweep (dry run, run, resume, exit codes and what each failure line
        means), and the two health lines with what a FAIL means and what to
        do.
  - [ ] `CHANGELOG.md` `[Unreleased]`: `prints` at 1m, the `off_tape`
        column and migration 062, `mt data off-tape sweep`, the health checks.
        No version bump in this slice.

- [ ] **8.4 Commit:** `docs: document off-tape marks, prints, and sweep`

## Section 9 — Final validation (effort 1/5)

- [ ] **9.1 Run the gates**
  - [ ] `ruff check` and `ruff format --check` on touched files; mypy per
        conventions — clean.
  - [ ] Unit tier, integration tier, then the new load case, run
        separately. Known pre-existing failures (see project memory) are
        listed, not fixed; any new failure is fixed or reported.
  - [ ] Design verification walkthrough step 1 passes.

- [ ] **9.2 Record results in the design**
  - [ ] Add a "Calibration record" to the slice design's Verification
        Walkthrough: K/M/R, weekly share and bounds, prior-close match rate,
        earnings-set result, sweep chunk measurement and projection, eligible
        1m load-test elapsed (from the calibration note).
  - [ ] Set the slice design's `status` to `complete` and this file's
        `status` to `complete`.

- [ ] **9.3 Commit:** `docs: record 171 calibration and complete slice`
  - [ ] Success: working tree clean on `171-slice.off-tape-prints`.
