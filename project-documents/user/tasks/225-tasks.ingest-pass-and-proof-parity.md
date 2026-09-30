---
docType: tasks
slice: ingest-pass-and-proof-parity
project: trading-data
lld: user/slices/225-slice.ingest-pass-and-proof-parity.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [220, 221, 222, 223, 224]
interfaces: [226, 227, 228, 229, 230, 231, 233]
projectState: >
  Slice 224 complete and released (v0.22.0): `mt data tick pass` buys and
  delivers units to *verified* and projects definitions into
  tick_definition. The archive at /data/tick-archive holds the two adopted
  jobs and four definition jobs. tick_trade and tick_ingest_ledger exist but
  are empty. No status command exists. The 225 design passed review
  (CONCERNS, findings addressed in 19b6ae3).
dateCreated: 20260930
dateUpdated: 20260930
status: not_started
---

# Tasks: Ingest Pass and Proof Parity

## Context Summary

- Working on slice 225. It builds `mt data tick ingest`, which loads every
  *verified* `trades`/`tbbo` unit into `tick_trade` with three checks
  (counts, resolution, session boundary) in one transaction per unit. It
  also builds `mt data tick status` and `mt data tick coverage`.
- The LLD is `user/slices/225-slice.ingest-pass-and-proof-parity.md`. Tasks
  cite its Technical Decisions as "TD n (short name)":
  - TD1: store-only run context and the generic pass contract.
  - TD2: one transaction per unit, on the worker's own connection.
  - TD3: statement builders plus two transition executors.
  - TD4: selection and order.
  - TD5: tier supersession.
  - TD6: resolution, sessions and the ledger.
  - TD7: no calendar extension.
  - TD8: failure classification.
  - TD9: status classification.
  - TD10: unit-complete status and raw-proved coverage.
  - TD11: the API answer.

  "FR n" means the LLD's Functional Requirement n.
- **No money in this slice.** Ingest, status and coverage make no provider
  call, and no test builds a provider client. Only walkthrough step 2
  (`adopt`, then `pass` over already-bought definitions) touches the
  provider, and it spends $0.
- Every destructive statement targets a database that a fixture or the
  walkthrough created (`sql.md`). Production is read-only (sessions).
- `tick_app` grants: `scripts/provision_tick_roles.sql` already grants
  SELECT, INSERT, UPDATE and DELETE on all seven tick tables, including
  DELETE on `tick_trade` (checked at breakdown). No grant task is needed.
- Next slice: 226 (proof: measure and tune).

**Test environment.** Export `MT_TIMESCALE_TEST_URL` from `.env` with the
quotes stripped. Run the unit and integration tiers as separate pytest
invocations. Run mypy on the src kalshi paths, the touched src paths and
the tests in one invocation. **Before every commit step**, run ruff check
and ruff format on that commit's touched files only. Then grep
`git diff main` for pre-existing lines the formatter rewrote. Known
pre-existing failures are not regressions. Re-run a failure in isolation
before investigating it.

**Fixtures.** Integration tests use `migrated_tick_db` for the tick
database and `session_migrated_db` for the calendar (it holds
`CME_EQUITY`). DBN day files and job directories come from
`test/tick_support/dbn_files.py`; manifest rows come from `seed.py` and
`rows.py`.

**File size.** Source files stay under about 300 lines. If one would pass
that, split it along the concern it holds and add a note on the task.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 0: Baseline, fixtures and constants

- [ ] **0.1 Record the pre-change test baseline**
  - [ ] On the slice branch before any change, run the unit tier, then the
        integration tier. Save failing ids to `/tmp/225-baseline-unit.txt`
        and `/tmp/225-baseline-integration.txt`
  - [ ] Success: both files exist
  - [ ] Effort: 1

- [ ] **0.2 Real DBN slice fixtures**
  - [ ] Cut small real slices from the adopted files under
        `/data/tick-archive` into `test/fixtures/databento/` (a few thousand
        records each, well under 1 MB committed), written the way
        `dbn_files.py` writes day files:
    1. `trades`, 2024-09-03: records on both sides of 00:00 UTC and around
       the 21:00–22:00 UTC daily break, including at least one spread id
    2. `tbbo`, one day inside the `tbbo` job, the same shape of cut
    3. the definition file for each of those two days, from the matching
       `GLBX-20260930-*` job
  - [ ] Keep at least one repeated `(instrument_id, ts_event, sequence)`
        triple in the trades slice. If the real day has none, record that in
        a note; 2.7 then builds the repeat by editing a real batch
  - [ ] Supersession fixture (FR6): the adopted jobs share no day. Derive a
        `trades` file for the `tbbo` slice's day from the `tbbo` slice's
        trade fields (the same events on the same day). Record in a note that
        this replaces the LLD's "real trades and tbbo slice of the same day"
  - [ ] Add `dbn_files.py` helpers that build a job directory and seed the
        manifest: a tier unit at *verified* with `provider_record_count` set,
        its companion definition unit *ingested*, and its `tick_definition`
        rows projected (reuse 224's definitions projection; no copied SQL)
  - [ ] Success: the helpers build a selectable unit on `migrated_tick_db`
        in a smoke test
  - [ ] Effort: 3

- [ ] **0.3 Constants (TD2, TD4, TD5, TD8)**
  - [ ] `data/tick/constants.py`:
    - `TICK_INGEST_LOCK_KEY = 220_000_002`
    - `TICK_INGEST_WORKERS = 2`, with a note that it is a starting value
      226 re-sets
    - `TICK_TIER_RANK`, derived from `TICK_TIERS` order and never written
      out by hand
    - `TICK_INGEST_LOCK_TIMEOUT_SECONDS`
    - `TICK_DB_KEEPALIVES_IDLE_SECONDS`, `TICK_DB_KEEPALIVES_INTERVAL_SECONDS`
      and `TICK_DB_KEEPALIVES_COUNT`
  - [ ] Each constant gets a one-line comment naming its TD. The timeout and
        keepalive values are starting values: pick modest ones and say so
  - [ ] Add a helper that renders the tier-rank SQL expression
        (`array_position(ARRAY[...], schema)`) from `TICK_TIERS`
  - [ ] Extend `test_constants.py`: lock keys distinct; `TICK_TIER_RANK`
        matches `TICK_TIERS` order; the rendered SQL lists the tiers in the
        same order
  - [ ] Success: `uv run pytest test/unit/data/tick -q` passes
  - [ ] Effort: 1
  - [ ] Commit: `feat(tick): add ingest fixtures and constants`

---

## Section 1: Refactors with no behaviour change (TD1, TD3)

- [ ] **1.1 Split the store context from the run context (TD1)**
  - [ ] New `data/tick/store_context.py`: `TickStore` (settings, conn,
        archive_root, run_id, clock) and `open_tick_store(settings, *,
        lock_key)`. The preflight runs in 223's order without the provider:
        unknown `MT_TICK_*` keys, the tick URL, the archive directory,
        connect, migrations, lock
  - [ ] `run_context.py`: `TickRun(TickStore)` adds `provider`;
        `open_tick_run` composes `open_tick_store(lock_key=
        TICK_ACQUISITION_LOCK_KEY)` with the provider
  - [ ] Expose the connect-and-check-migrations helper publicly, for
        status and coverage (no lock, no archive directory)
  - [ ] Success: every 224 call site is unchanged; imports resolve
  - [ ] Effort: 2

- [ ] **1.2 Store context tests**
  - [ ] Every existing unit and integration test for the tick run context
        passes unchanged
  - [ ] New: `open_tick_store` succeeds with `MT_DATABENTO_API_KEY` unset;
        it takes the given lock key (a second holder of the same key is
        refused, a holder of the acquisition key is not); the plain
        connection helper needs no archive directory
  - [ ] Success: passes
  - [ ] Effort: 1
  - [ ] Commit: `refactor(tick): split store context from run context`

- [ ] **1.3 Make the pass contract generic (TD1)**
  - [ ] `pass_contract.py`: `PassPhase` and `TickPass` become generic in
        `RunT` (bound to `TickStore`); add `TickPassPhaseName.INGEST`
  - [ ] The acquisition pass is typed `TickPass[TickRun]`
  - [ ] Success: mypy is clean on `data/tick` and its tests
  - [ ] Effort: 2

- [ ] **1.4 Update the parity test**
  - [ ] `test_pass_contract_parity.py`: add the generic run type to the
        declared-divergence list, with a comment naming TD1
  - [ ] Success: passes; all acquisition pass tests pass unchanged
  - [ ] Effort: 1
  - [ ] Commit: `refactor(tick): make the tick pass contract generic in its run`

- [ ] **1.5 Transition statement builders and a sync executor (TD3)**
  - [ ] In `manifest_repo.py`, each compare-and-set transition becomes a
        builder returning `(sql, params, expected)`, plus the existing async
        executor. Every async function keeps its signature
  - [ ] Add `execute_transition_sync(cur, statement, unit_id)`. It runs in
        the caller's transaction and raises `ManifestTransitionError` on
        zero rows
  - [ ] Add a builder for `mark_superseded(unit_id, by_unit_id)`,
        compare-and-set on "current" (not superseded, not reopened)
  - [ ] `manifest_repo.py` is at 295 lines. Put the sync executor and
        `mark_superseded` in a new module (for example
        `manifest_ingest.py`) and note the split
  - [ ] Success: imports; no SQL string is duplicated between the async and
        sync paths
  - [ ] Effort: 2

- [ ] **1.6 Transition tests**
  - [ ] All existing manifest tests pass unchanged
  - [ ] Integration on `migrated_tick_db`:
    - `mark_ingested` through the sync executor inside an open transaction
      moves *verified* → *ingested*, and a rollback leaves the unit
      *verified*
    - zero matched rows raises `ManifestTransitionError`
    - `mark_superseded` sets the link once, and a second call raises
  - [ ] Success: passes
  - [ ] Effort: 2
  - [ ] Commit: `refactor(tick): split manifest transitions into statements and executors`

---

## Section 2: Checks and record processing (TD6, TD8)

`ingest_records.py` is pure NumPy over one `RecordBatch`, with no I/O. Its
unit tests run on the 0.2 real slices. Synthetic data is used only for
failure shapes a real file cannot produce, and it is built by editing a
real batch.

- [ ] **2.1 Create `data/tick/ingest_checks.py`**
  - [ ] `IngestCheck(StrEnum)`: `counts`, `resolution`, `session_boundary`,
        `overlap`, `shape`, `decode`
  - [ ] One reason formatter per check, each reason starting with
        `<check>:`, carrying the evidence TD8's table names (counts P/D/S;
        first id, time and count; UTC and calendar-zone time with
        neighbouring sessions; the conflicting key and other units; the
        path and error; the populated span for a planning failure)
  - [ ] Unit test: every formatter's text starts with its check's value
  - [ ] Success: passes
  - [ ] Effort: 1

- [ ] **2.2 Contract resolution in `ingest_records.py` (TD6)**
  - [ ] Definitions arrive as arrays sorted by `(instrument_id,
        activation_ns)`. Resolve each record by `searchsorted` on the id and
        then the window test, looping only over ids with more than one
        window that day
  - [ ] Return the resolved mask, plus the first unresolved id, its time
        and the unresolved count
  - [ ] Success: imports
  - [ ] Effort: 2

- [ ] **2.3 Resolution tests**
  - [ ] The real trades slice resolves fully against its day's definitions
  - [ ] A record whose id is edited to an unknown id is reported with its
        time and count
  - [ ] A record outside its id's window fails
  - [ ] An id with two windows on one day resolves each record to the
        window that holds it
  - [ ] Success: passes
  - [ ] Effort: 2

- [ ] **2.4 Session location (TD6)**
  - [ ] Test the populated span first: a record outside it is the "outside
        the populated calendar range" failure. Then `locate_ns`, where `-1`
        is the "in no session" failure
  - [ ] Return session positions per record
  - [ ] Success: imports
  - [ ] Effort: 1

- [ ] **2.5 Session location tests**
  - [ ] The real slice's records on both sides of 00:00 UTC land in the
        right sessions
  - [ ] A record edited into the daily break fails as "in no session"
  - [ ] A record edited past the populated span fails as "outside",
        not as a break
  - [ ] Success: passes
  - [ ] Effort: 1

- [ ] **2.6 `sequence_ordinal` with a cross-batch carry (TD6, 222 TD1)**
  - [ ] Within a batch: stable lexsort and run lengths. Across batches: add
        each triple's earlier count from a sorted carry array of distinct
        triples, merged per batch. No contiguity assumption
  - [ ] Success: imports
  - [ ] Effort: 3

- [ ] **2.7 Ordinal tests (FR8, part)**
  - [ ] A non-adjacent repeat gets ordinals 0 and 1
  - [ ] A repeat that straddles a batch boundary gets 0 and 1 (split one
        real batch in two)
  - [ ] Ordinals for the whole real slice equal a brute-force count
        computed in the test
  - [ ] Success: passes
  - [ ] Effort: 2

- [ ] **2.8 Ledger accumulation and row building (TD6, TD2)**
  - [ ] Accumulate per (instrument, session): count, volume (sum of
        `size`), first and last `ts_event`
  - [ ] The ledger's instrument set: every definition with `asset =
        product` whose window meets the session. Missing instruments become
        zero-record rows with NULL times. `calendar_id` comes from
        `FUTURES_PRODUCT_CALENDAR`; `session_date` comes from the session
  - [ ] Build COPY rows in `TICK_TRADE_COLUMNS` order plus
        `sequence_ordinal` and `unit_id`, behind one function (the one 226
        may replace)
  - [ ] Success: imports; under ~300 lines (split row building out if not)
  - [ ] Effort: 3

- [ ] **2.9 Ledger and row tests**
  - [ ] Over the real slice: the ledger's `record_count` sums to the decoded
        count; zero-record rows exist for valid instruments with no records;
        the instrument set equals the definitions valid in each session
  - [ ] Row tuples have the column order the COPY statement names
  - [ ] Success: passes
  - [ ] Effort: 2
  - [ ] Commit: `feat(tick): add ingest checks and record processing`

---

## Section 3: Selection and plan (TD4, TD5, TD7)

- [ ] **3.1 Create `data/tick/ingest_select.py`**
  - [ ] Select units that meet all of these: the schema is in
        `STORED_TIERS`; `state = verified`; `fetch_status` is open; the unit
        is current (`COVERAGE_PREDICATE`); no current higher-tier unit
        exists for the same shape and day (the rank SQL from 0.3); its
        companion definition unit is *ingested*
  - [ ] Skip, do not fail, with a reason: "outranked by unit N" or
        "awaiting definitions"
  - [ ] `--unit-id` narrows the selection and never overrides it. A named
        unit that is not selectable is reported with its reason
  - [ ] Order by `unit_date`, then `unit_id`
  - [ ] Success: imports
  - [ ] Effort: 2

- [ ] **3.2 Selection tests (FR4)**
  - [ ] Integration on `migrated_tick_db`, one case per rule:
    - a unit is skipped as "awaiting definitions" until its companion is
      *ingested*, and selected after
    - a `trades` unit with a current `tbbo` unit on the same day is
      "outranked"
    - a superseded unit, a reopened unit and an exhausted unit are not
      selected
    - an explicit `--unit-id` for an unselectable unit returns its reason
    - order follows `unit_date`, then `unit_id`
  - [ ] Success: passes
  - [ ] Effort: 2
  - [ ] Commit: `feat(tick): select units for ingest`

- [ ] **3.3 Create `data/tick/ingest_plan.py`**
  - [ ] An immutable `UnitIngestPlan` per unit, built on the loop through
        `asyncio.to_thread`: product (`planning_product`), calendar,
        `sessions_between(day 00:00Z, day+1 00:00Z)`, populated span,
        `SessionIndex`, definition arrays (`asset = product`, windows
        meeting the unit's session span), and the superseded current
        lower-tier units with their ledger min/max event times
  - [ ] A non-`parent` `stype_in` becomes a `shape` failure
  - [ ] `OutOfPopulatedRangeError` becomes a `session_boundary` failure
        naming the day and span (TD7). An unreachable calendar raises
        `TickCalendarError` (→ `storage_abort`)
  - [ ] Success: imports
  - [ ] Effort: 2

- [ ] **3.4 Plan tests and the horizon test (TD7)**
  - [ ] Unit: `session_days` over a calendar whose last close is mid-day X
        returns no day at or after X, and raises for `[.., X+1)`
  - [ ] Integration: a plan for a real day holds two sessions and 61
        definitions; a `raw_symbol` unit fails as `shape`; a unit whose day
        lies past the populated span fails as `session_boundary`
  - [ ] Success: passes
  - [ ] Effort: 2
  - [ ] Commit: `feat(tick): build per-unit ingest plans`

---

## Section 4: The worker (TD2, TD5, TD8)

- [ ] **4.1 Create `data/tick/ingest_worker.py`**
  - [ ] A plain synchronous function taking the plan and returning a
        `UnitOutcome` (unit id, counts, check failure or none, duration,
        decode and write time split)
  - [ ] Open its own `psycopg.Connection` with `connect_timeout`, the
        keepalive constants and `lock_timeout` from 0.3
  - [ ] One transaction:
    1. supersession (4.2)
    2. binary `COPY` with `set_types` and `write_row`, fed batch by batch
       from `iter_batches`
    3. counts check: provider = decoded = `count(*) WHERE unit_id = N`,
       bounded by the unit's `ts_event` range
    4. ledger insert
    5. `mark_ingested` through the sync executor
    6. `COMMIT`
  - [ ] A check failure rolls back and returns the failure
  - [ ] Catch only: `UniqueViolation` (→ `overlap`, naming the key and the
        other current units on the day), the named decode and file errors
        (→ `decode`), and `ManifestTransitionError` (→ skip "changed during
        ingest"). Everything else propagates
  - [ ] Write the thread state review from TD2 as the module docstring
  - [ ] Success: imports; under ~300 lines
  - [ ] Effort: 4

- [ ] **4.2 Supersession inside the transaction (TD5)**
  - [ ] Before the COPY, for each superseded unit in the plan: call
        `mark_superseded` through the sync executor. If that unit was
        *ingested*, delete its rows `WHERE unit_id = O AND ts_event BETWEEN`
        its ledger min first and max last event time. Its ledger rows stay
  - [ ] Success: imports
  - [ ] Effort: 2

- [ ] **4.3 Worker tests (FR3, FR5, FR6, FR8)**
  - [ ] Integration on `migrated_tick_db` with the 0.2 fixtures:
    1. a real day ingests: unit *ingested*, `decoded_record_count` set,
       ledger complete with zero-record rows (FR3)
    2. each check fails its unit with the check named and leaves no rows,
       no ledger rows and no transition: counts (provider count raised by
       one), resolution, session boundary (break and outside span), overlap
       (a second current unit over loaded rows) (FR5)
    3. supersession: `tbbo` over an ingested `trades` of the same day
       replaces its rows in one transaction, sets the link, and session
       totals equal the `tbbo` ledger (FR6)
    4. a unit's row is changed by another connection before
       `mark_ingested`: the worker rolls back and returns "changed during
       ingest"
    5. another session holds a row lock on the unit past the lock timeout
       (set short in the test): `OperationalError` is raised and nothing is
       written
    6. re-ingesting the same file into a fresh database gives identical rows
       (FR8)
  - [ ] Success: passes
  - [ ] Effort: 4
  - [ ] Commit: `feat(tick): add the ingest worker`

---

## Section 5: The pass and its verb

- [ ] **5.1 Create `data/tick/ingest_pass.py`**
  - [ ] `IngestPhase` (a `PassPhase[TickStore]`) and `run_ingest`: select,
        then plan each unit on the loop, then run workers through
        `asyncio.to_thread`, at most `TICK_INGEST_WORKERS` at once
  - [ ] A failed unit: `record_failure(deterministic=True)` on the run
        connection, with the reason from 2.1 → outcome `partial`
  - [ ] Abort: stop starting units, await every in-flight future
        (`gather(..., return_exceptions=True)`), then re-raise the first
        exception into `run_phase`. Units committed while waiting are in
        the summary
  - [ ] Summary: `ingested`, `failed`, `records`, `skipped` (by
        `awaiting_definitions`, `outranked`, `changed_during_ingest`),
        `superseded`, and `units` (per unit: `unit_id`, `unit_date`,
        `schema`, `outcome`, `records`, `reason`, durations)
  - [ ] Success: imports; under ~300 lines
  - [ ] Effort: 3

- [ ] **5.2 Pass tests (FR1, FR2, FR7)**
  - [ ] Integration:
    1. two real days ingest; a second run selects nothing and exits `ok`
       with the row count unchanged (FR2)
    2. the tick database drops mid-run (terminate the backend): the outcome
       is `storage_abort`, no attempt is counted, committed units stay
       *ingested*, and the in-progress unit leaves nothing (FR7)
    3. one worker raises while the other is mid-unit: the other unit
       settles before the run ends, and the summary shows it
    4. a failed check gives `partial`, the unit is `RETRY_EXHAUSTED` with
       the reason prefixed by its check, and after `reset` it ingests
    5. acquisition (fake provider) and ingest run concurrently on one
       database without interfering
  - [ ] Success: passes
  - [ ] Effort: 3
  - [ ] Commit: `feat(tick): add the ingest pass`

- [ ] **5.3 `mt data tick ingest` verb**
  - [ ] `ingest [--unit-id N]... [--json]`. `tick.py` is at 279 lines, so
        put this verb, and later status and coverage, in a new
        `cli/commands/tick_store_cmds.py` and register them on `tick_app` in
        `tick.py`. The exit comes from `EXIT_BY_OUTCOME`
  - [ ] The report reuses `tick_pass_render.py`'s phase rendering and adds
        the per-unit lines and skip tally shown in the LLD's API Contracts.
        `--json` emits `{**PassResult.to_dict(), "exit_code": n}`
  - [ ] CLI tests: the exit code per outcome, the `--json` shape, repeated
        `--unit-id`, and running with `MT_DATABENTO_API_KEY` unset (FR11,
        ingest part)
  - [ ] Success: passes; each touched CLI file is under ~300 lines
  - [ ] Effort: 2
  - [ ] Commit: `feat(tick): add mt data tick ingest verb`

---

## Section 6: Status and coverage (TD9, TD10, TD11)

- [ ] **6.1 Create `data/tick/tick_status.py`: classification (pure)**
  - [ ] `TickSessionStatus(StrEnum)` and one precedence tuple, worst first,
        exactly as TD9's table (with `awaiting_ingest`)
  - [ ] A day's unit is the highest-ranked current tier unit (TD5's rule).
        A session's condition is the worst of its days (`missing` >
        `degraded` > `pending` > `available`, `unknown` if a day lacks a
        row), with Saturdays exempt
  - [ ] "Caught up": every session in scope is `complete`, or
        `provider_hole` on a provider-`missing` day. It is "n/a" with no
        wanted range
  - [ ] Success: imports; no I/O
  - [ ] Effort: 2

- [ ] **6.2 Classification tests (FR9, part)**
  - [ ] One test per bucket; precedence when two rules match; the worst
        condition across a session's two days; the Saturday exemption; a
        `complete` session with a `degraded` day counted as complete and
        tallied as degraded; caught up with and without a wanted range
  - [ ] Success: passes
  - [ ] Effort: 2
  - [ ] Commit: `feat(tick): classify tick sessions for status`

- [ ] **6.3 Create `data/tick/status_reads.py`**
  - [ ] Reads: units per shape and day (state, fetch status, current,
        schema); ledger sums per (instrument, session) over current units;
        conditions; the edge with `observed_at`; per-contract lines (ledger
        joined to `tick_definition` for `raw_symbol`, `instrument_class`,
        `expiration_ns`)
  - [ ] Raw counts for coverage: one grouped count over `tick_trade`
        bounded by the range's first open and last close, with rows
        assigned to sessions by a join against session arrays passed as
        parameters
  - [ ] No read scans `tick_trade` except the coverage count
  - [ ] Success: imports
  - [ ] Effort: 2

- [ ] **6.4 `build_status` and `build_coverage`**
  - [ ] In `tick_status.py` (split if it passes ~300 lines): scope per TD9
        (the wanted range if a tier is set, plus sessions touched by current
        tier units), sessions from the production calendar, and frozen
        dataclasses with `to_dict()`. The status result carries
        `complete_basis: "units"` (TD10)
  - [ ] Coverage marks a session `mismatch` when the raw count differs from
        the ledger sum, naming the instruments and both counts
  - [ ] A production or tick outage raises (→ exit 4). There is no degraded
        output
  - [ ] Success: imports
  - [ ] Effort: 3

- [ ] **6.5 Status and coverage integration tests (FR9, FR10)**
  - [ ] After ingesting fixture days: status buckets match expectations,
        spreads are hidden in the per-contract list, and `to_dict()`
        round-trips through JSON
  - [ ] Coverage reports `ok`. After deleting one `tick_trade` row it
        reports `mismatch` naming the instrument, with the ledger one more
        than raw
  - [ ] Success: passes
  - [ ] Effort: 2
  - [ ] Commit: `feat(tick): build tick status and coverage`

- [ ] **6.6 `status` and `coverage` verbs**
  - [ ] In `tick_store_cmds.py`: `status [--product] [--all-instruments]
        [--json]` and `coverage --start --end [--product] [--json]`.
        `--start` and `--end` are required. They connect through the plain
        helper from 1.1: no lock, no archive directory
  - [ ] Rendering goes in new `cli/commands/tick_status_render.py`,
        matching the LLD's API Contracts layout, including the line
        "complete = every unit ingested; raw-count proof: mt data tick
        coverage"
  - [ ] Exits: 0; 1 preflight; 3 when coverage finds a mismatch; 4 for an
        unreachable tick or production database
  - [ ] CLI tests: exit codes, `--json` equals `to_dict()` plus
        `exit_code`, and both verbs run with `MT_DATABENTO_API_KEY` and
        `MT_TICK_ARCHIVE_DIR` unset (FR11)
  - [ ] Success: passes; each file under ~300 lines
  - [ ] Effort: 2
  - [ ] Commit: `feat(tick): add mt data tick status and coverage verbs`

---

## Section 7: Standing checks and documents

- [ ] **7.1 Kalshi contract diff**
  - [ ] Compare `pass_contract.py` with `data/kalshi/collection_pass.py`.
        Every difference must be a declared divergence (224's list plus the
        generic run type). Otherwise name it in a note for the PM
  - [ ] Success: note lists each difference and its disposition
  - [ ] Effort: 1

- [ ] **7.2 Realtime paths and the API answer**
  - [ ] Confirm the LLD's "Realtime paths" points still hold against the
        code as built (bounded unit transaction, rank-based supersession,
        per-unit ledger). Note any change
  - [ ] Confirm `build_status`/`build_coverage` return `to_dict()`
        dataclasses usable by 230 unchanged (TD11)
  - [ ] Success: note written
  - [ ] Effort: 1

- [ ] **7.3 Contract rows, plan Notes and architecture flag**
  - [ ] `user/reference/data-correctness-architecture.md`:
    - I10: the three verbs
    - I11: the check and completeness grains (status unit-complete,
      coverage raw-proved)
    - I12: supersession at ingest by tier rank
    - I13: the session check as shipped
  - [ ] 220 slice plan Notes: extend the "superseded by 225" block with LLD
        supersedes items 6–9, one line each
  - [ ] Add to the architecture's Revision Log (as 224 did): the fourth
        tick → production edge (status and coverage read sessions)
  - [ ] Success: `dateUpdated` bumped on each file
  - [ ] Effort: 1

- [ ] **7.4 README and CHANGELOG**
  - [ ] README "Futures tick data": `ingest`, `status` and `coverage`, their
        exit codes, and that none needs the API key
  - [ ] CHANGELOG `[Unreleased]` Added entries, user-facing wording
  - [ ] Success: both updated
  - [ ] Effort: 1
  - [ ] Commit: `docs: record tick ingest, status and coverage`

---

## Section 8: Validation and walkthrough

- [ ] **8.1 Lint, types and tiers**
  - [ ] ruff and mypy per the test environment note; unit tier, then
        integration tier; compare with the 0.1 baseline
  - [ ] Success: no failure outside the baseline
  - [ ] Effort: 2

- [ ] **8.2 Walkthrough steps 2 and 3: scratch database, status before ingest**
  - [ ] Run LLD walkthrough step 2 into `mt_scratch_tick_225`
  - [ ] Record from the manifest the unit count and `provider_record_count`
        sum per schema. Expect 26 `trades` units summing to 10,049,172 and
        52 `tbbo` units, and record the `tbbo` total. A different count
        stops the walkthrough: report it to the PM
  - [ ] Step 3: status runs without the key. Record whether each
        job-boundary session shows `missing` or `edge_unknown`, and why
  - [ ] Success: outputs recorded in the LLD walkthrough
  - [ ] Effort: 1

- [ ] **8.3 Walkthrough steps 4 and 5: ingest and idempotence**
  - [ ] Step 4: 78 units ingested, none failed, provider = decoded per
        schema, `tick_trade` count = their sum
  - [ ] Record the wall time and per-unit decode and write durations.
        Check both throughput targets from TD2: every unit far below 24 h,
        and the whole run (about 3.5 months of sessions) inside an operator's
        working session. A miss fails the walkthrough
  - [ ] Step 5: a second run selects nothing, exits 0, row count unchanged
  - [ ] If a unit fails a check, STOP and report the check and its reason.
        Do not loosen a check
  - [ ] Success: all match; numbers recorded in the LLD walkthrough
  - [ ] Effort: 2

- [ ] **8.4 Walkthrough steps 6 and 7: status, coverage, a loud mismatch**
  - [ ] Step 6: sessions `complete` except the job-boundary ones, each
        listed; ESU4, ESZ4 and ESH5 present, with the September roll
        visible; both coverage ranges `ok`, exit 0
  - [ ] Step 7: the hand delete in the scratch database gives `mismatch`
        on 2024-09-18, naming the instrument, exit 3
  - [ ] Success: outputs recorded
  - [ ] Effort: 1

- [ ] **8.5 Walkthrough steps 8 and 9: supersession evidence, teardown**
  - [ ] Step 8: name the FR6 test (4.3 case 3) and paste its passing
        output into the LLD walkthrough
  - [ ] Step 9: drop `mt_scratch_tick_225` and confirm a `pg_database`
        count of 0; the archive stays
  - [ ] Success: recorded; scratch database gone
  - [ ] Effort: 1
  - [ ] Commit: `docs: record slice 225 verification walkthrough`
