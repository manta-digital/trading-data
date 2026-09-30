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

# Tasks: Ingest Pass and Proof Parity (part 1 of 2)

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
- Sections 0–4 are here; Sections 5–8 are in
  `225-tasks.ingest-pass-and-proof-parity-2.md`.
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
  - [ ] Commit: `feat(tick): add ingest check names and reasons`

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
  - [ ] Commit: `feat(tick): resolve tick records to contracts`

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
  - [ ] Commit: `feat(tick): assign tick records to sessions`

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
  - [ ] Commit: `feat(tick): compute sequence ordinals across batches`

- [ ] **2.8 Ledger accumulation (TD6)**
  - [ ] Accumulate per (instrument, session): count, volume (sum of
        `size`), first and last `ts_event`
  - [ ] The ledger's instrument set: every definition with `asset =
        product` whose window meets the session. Missing instruments become
        zero-record rows with NULL times. `calendar_id` comes from
        `FUTURES_PRODUCT_CALENDAR`; `session_date` comes from the session
  - [ ] Success: imports
  - [ ] Effort: 2

- [ ] **2.9 Ledger tests**
  - [ ] Over the real slice:
    - the ledger's `record_count` sums to the decoded count
    - zero-record rows exist for valid instruments with no records
    - the instrument set equals the definitions valid in each session
  - [ ] Success: passes
  - [ ] Effort: 2
  - [ ] Commit: `feat(tick): accumulate the ingest ledger`

- [ ] **2.10 COPY row building (TD2)**
  - [ ] In its own module (for example `ingest_rows.py`), since this is
        the seam 226 may replace with a NumPy binary encoder: one function
        building rows in `TICK_TRADE_COLUMNS` order plus `sequence_ordinal`
        and `unit_id`, plus the matching COPY column list and types
  - [ ] Test: row tuples match the column list; values round-trip for one
        real record
  - [ ] Success: passes; `ingest_records.py` under ~300 lines
  - [ ] Effort: 1
  - [ ] Commit: `feat(tick): build tick_trade COPY rows`

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
  - [ ] Integration:
    - a plan for a real day holds two sessions and 61 definitions
    - a `raw_symbol` unit fails as `shape`
    - a unit whose day lies past the populated span fails as
      `session_boundary`
    - an unreachable calendar URL raises `TickCalendarError`
    - with an ingested `trades` unit and a verified `tbbo` unit on the same
      day, the `tbbo` plan's superseded list holds the `trades` unit with
      the min first and max last event times from its ledger
  - [ ] Success: passes
  - [ ] Effort: 2
  - [ ] Commit: `feat(tick): build per-unit ingest plans`

---

## Section 4: The worker (TD2, TD5, TD8)

- [ ] **4.1 Supersession step (TD5)**
  - [ ] In `ingest_worker.py`, a function that runs on the worker's open
        cursor, before the COPY, for each superseded unit in the plan: call
        `mark_superseded` through the sync executor. If that unit was
        *ingested*, delete its rows `WHERE unit_id = O AND ts_event BETWEEN`
        its ledger min first and max last event time. Its ledger rows stay
  - [ ] Success: imports
  - [ ] Effort: 2

- [ ] **4.2 The worker function**
  - [ ] A plain synchronous function
        `ingest_unit(plan, archive_root, conn_settings) -> UnitOutcome`.
        `UnitOutcome` holds the unit id, counts, check failure or none,
        duration, and the decode and write time split
  - [ ] `conn_settings` is a frozen `WorkerConnectionSettings` (tick URL,
        connect timeout, keepalive idle, interval and count, lock timeout).
        The pass builds it from the 0.3 constants. The worker has no
        defaults of its own, so tests pass a short lock timeout directly
  - [ ] Opens its own `psycopg.Connection` with those settings. One
        transaction:
    1. supersession (4.1)
    2. binary `COPY` with `set_types` and `write_row`, fed batch by batch
       from `iter_batches`
    3. counts check: provider = decoded = `count(*) WHERE unit_id = N`,
       bounded by the unit's `ts_event` range
    4. ledger insert
    5. `mark_ingested` through the sync executor
    6. `COMMIT`
  - [ ] A check failure rolls back and returns the failure
  - [ ] Catch only these:
    - `UniqueViolation` → `overlap`, naming the key and the other current
      units on the day
    - the named decode and file errors → `decode`
    - `ManifestTransitionError` → skip "changed during ingest"

    Everything else propagates
  - [ ] Write the thread state review from TD2 as the module docstring
  - [ ] Success: imports; under ~300 lines
  - [ ] Effort: 4

- [ ] **4.3 Worker tests (FR3, FR5, FR8)**
  - [ ] Integration on `migrated_tick_db` with the 0.2 fixtures:
    1. a real day slice ingests: the unit is *ingested*,
       `decoded_record_count` is set, and the ledger is complete with
       zero-record rows (FR3)
    2. these checks fail the unit with the check named and leave no rows,
       no ledger rows and no transition (FR5):
       - counts (provider count raised by one)
       - resolution
       - session boundary (break, and outside the span)
    3. decode: the archive file missing, and a truncated copy of a real
       file; each fails as `decode` naming the path
    4. "changed during ingest": build the plan, then reset or reopen the
       unit from another connection, then call the worker. `mark_ingested`
       matches no row; the worker rolls back and returns the skip. This
       needs no hook
    5. lock timeout: another connection holds `SELECT … FOR UPDATE` on the
       unit's row. The worker, called with a lock timeout of about one
       second, raises `OperationalError`, and nothing is written
    6. re-ingesting the same file into a fresh database gives identical rows
       (FR8)
  - [ ] Success: passes
  - [ ] Effort: 3
  - [ ] Commit: `feat(tick): add the ingest worker`

- [ ] **4.4 Supersession and overlap tests (FR5, FR6)**
  - [ ] Integration, with the 0.2 supersession fixture:
    1. `tbbo` over an ingested `trades` unit of the same day replaces its
       rows in one transaction: the link is set, the `trades` rows are
       gone, its ledger rows stay, and session totals equal the `tbbo`
       ledger (FR6)
    2. a forced failure after supersession (the counts check) rolls back:
       the `trades` rows and unit are untouched
    3. overlap: a second current unit whose rows collide with loaded rows
       fails as `overlap`, naming the key and the other unit (FR5)
  - [ ] Success: passes
  - [ ] Effort: 2
  - [ ] Commit: `test(tick): cover supersession and overlap in the worker`
