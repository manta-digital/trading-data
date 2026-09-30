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

- [x] **0.1 Record the pre-change test baseline**
  - [x] On the slice branch before any change, run the unit tier, then the
        integration tier. Save failing ids to `/tmp/225-baseline-unit.txt`
        and `/tmp/225-baseline-integration.txt`
  - [x] Success: both files exist
  - [x] Effort: 1
  - [x] Commit: (baseline recording, no commit required)
  - Note: unit baseline is 0 failures (4168 passed). Integration baseline has 6 failures, all known pre-existing: test_cli_lists priority1 (2), test_migration_051_052 (2), test_policy_advances_head unaided (2).

- [x] **0.2 Real DBN slice fixtures**
  - [x] Cut small real slices from the adopted files under
        `/data/tick-archive` into `test/fixtures/databento/` (a few thousand
        records each, well under 1 MB committed), written the way
        `dbn_files.py` writes day files:
    1. `trades`, 2024-09-03: records on both sides of 00:00 UTC and around
       the 21:00–22:00 UTC daily break, including at least one spread id
    2. `tbbo`, one day inside the `tbbo` job, the same shape of cut
    3. the definition file for each of those two days, from the matching
       `GLBX-20260930-*` job
  - [x] Keep at least one repeated `(instrument_id, ts_event, sequence)`
        triple in the trades slice. If the real day has none, record that in
        a note; 2.7 then builds the repeat by editing a real batch
  - [x] Supersession fixture (FR6): the adopted jobs share no day. Derive a
        `trades` file for the `tbbo` slice's day from the `tbbo` slice's
        trade fields (the same events on the same day). Record in a note that
        this replaces the LLD's "real trades and tbbo slice of the same day"
  - [x] Add `dbn_files.py` helpers that build a job directory and seed the
        manifest: a tier unit at *verified* with `provider_record_count` set,
        its companion definition unit *ingested*, and its `tick_definition`
        rows projected (reuse 224's definitions projection; no copied SQL)
  - [x] Success: the helpers build a selectable unit on `migrated_tick_db`
        in a smoke test
  - [x] Effort: 3
  - Note: the helpers are in a new module `test/tick_support/tier_units.py` (`seed_tier_unit`, `real_file`, `record_count`, `TRADES_DAY`, `TBBO_DAY`), not `dbn_files.py`. That module re-heads the synthetic fixtures; the real slices keep their provider headers unchanged. The slices live in `test/fixtures/databento/real/` and are cut by `scripts/cut_tick_fixtures.py`. Provenance is in `SOURCES.md`.
  - Note: the trades slice has 73 repeated `(instrument_id, ts_event, sequence)` triples and the tbbo slice has 53, so 2.7 does not need to build a repeat by hand.
  - Note: the supersession fixture `glbx-mdp3-20241203.trades.dbn.zst` is derived from the 2024-12-03 tbbo slice (the same events cut to their trade fields). It replaces the LLD's "real trades and tbbo slice of the same day", because the adopted jobs share no day.
  - Note: the tbbo day is 2024-12-03 (definitions from GLBX-20260930-HVGRLYKHRN) and the trades day is 2024-09-03 (definitions from GLBX-20260930-DLDYL5DM8Q). The smoke test is `test/integration/data/test_tick_tier_units.py`.

- [x] **0.3 Constants (TD2, TD4, TD5, TD8)**
  - [x] `data/tick/constants.py`:
    - `TICK_INGEST_LOCK_KEY = 220_000_002`
    - `TICK_INGEST_WORKERS = 2`, with a note that it is a starting value
      226 re-sets
    - `TICK_TIER_RANK`, derived from `TICK_TIERS` order and never written
      out by hand
    - `TICK_INGEST_LOCK_TIMEOUT_SECONDS`
    - `TICK_DB_KEEPALIVES_IDLE_SECONDS`, `TICK_DB_KEEPALIVES_INTERVAL_SECONDS`
      and `TICK_DB_KEEPALIVES_COUNT`
  - [x] Each constant gets a one-line comment naming its TD. The timeout and
        keepalive values are starting values: pick modest ones and say so
  - [x] Add a helper that renders the tier-rank SQL expression
        (`array_position(ARRAY[...], schema)`) from `TICK_TIERS`
  - [x] Extend `test_constants.py`: lock keys distinct; `TICK_TIER_RANK`
        matches `TICK_TIERS` order; the rendered SQL lists the tiers in the
        same order
  - [x] Success: `uv run pytest test/unit/data/tick -q` passes
  - [x] Effort: 1
  - [x] Commit: `feat(tick): add ingest fixtures and constants`
  - Note: `tier_rank_sql(column)` renders `array_position(ARRAY[...]::text[], column)`. The worker connect timeout reuses the existing `TICK_DB_CONNECT_TIMEOUT_SECONDS`.

---

## Section 1: Refactors with no behaviour change (TD1, TD3)

- [x] **1.1 Split the store context from the run context (TD1)**
  - [x] New `data/tick/store_context.py`: `TickStore` (settings, conn,
        archive_root, run_id, clock) and `open_tick_store(settings, *,
        lock_key)`. The preflight runs in 223's order without the provider:
        unknown `MT_TICK_*` keys, the tick URL, the archive directory,
        connect, migrations, lock
  - [x] `run_context.py`: `TickRun(TickStore)` adds `provider`;
        `open_tick_run` composes `open_tick_store(lock_key=
        TICK_ACQUISITION_LOCK_KEY)` with the provider
  - [x] Expose the connect-and-check-migrations helper publicly, for
        status and coverage (no lock, no archive directory)
  - [x] Success: every 224 call site is unchanged; imports resolve
  - [x] Effort: 2
  - Note: `store_context.py` holds the preflight: `open_tick_store`, `TickStore`, `TickPreflightError`, `check_env_keys`, `database_url`, `connect_migrated` (the public connect-and-check-migrations helper for status and coverage), `lock_held_message(lock_key)` and `LOCK_NAMES`. `run_context.py` re-exports its old names, and `LOCK_HELD` is now `lock_held_message(TICK_ACQUISITION_LOCK_KEY)` with unchanged text. `open_tick_run` checks env keys before building the provider (223's order), then enters `open_tick_store`, which repeats the cheap check.

- [x] **1.2 Store context tests**
  - [x] Every existing unit and integration test for the tick run context
        passes unchanged
  - [x] New: `open_tick_store` succeeds with `MT_DATABENTO_API_KEY` unset;
        it takes the given lock key (a second holder of the same key is
        refused, a holder of the acquisition key is not); the plain
        connection helper needs no archive directory
  - [x] Success: passes
  - [x] Effort: 1
  - [x] Commit: `refactor(tick): split store context from run context`
  - Note: tests are in `test/integration/data/test_tick_store_context.py`.

- [x] **1.3 Make the pass contract generic (TD1)**
  - [x] `pass_contract.py`: `PassPhase` and `TickPass` become generic in
        `RunT` (bound to `TickStore`); add `TickPassPhaseName.INGEST`
  - [x] The acquisition pass is typed `TickPass[TickRun]`
  - [x] Success: mypy is clean on `data/tick` and its tests
  - [x] Effort: 2
  - Note: uses PEP 695 generics (`class TickPass[RunT: TickStore]`). New `ACQUISITION_PHASE_NAMES` in `pass_contract.py` (every phase name but `INGEST`) is used by the acquisition renderer's `_PHASE_LINES` guard in `tick_pass_render.py`, by `test_acquisition_pass.py` and by `test_data_tick.py`. Those tests had iterated all of `TickPassPhaseName`, so they could not pass unchanged once `INGEST` was added. Ingest renders its own report (5.3).

- [x] **1.4 Update the parity test**
  - [x] `test_pass_contract_parity.py`: add the generic run type to the
        declared-divergence list, with a comment naming TD1
  - [x] Success: passes; all acquisition pass tests pass unchanged
  - [x] Effort: 1
  - [x] Commit: `refactor(tick): make the tick pass contract generic in its run`
  - Note: new parity test `test_generic_run_type_is_the_declared_divergence` checks that tick `PassPhase`/`TickPass` have one type parameter bound to `TickStore` and that Kalshi's `PassPhase`/`CollectionPass` have none.

- [x] **1.5 Transition statement builders and a sync executor (TD3)**
  - [x] In `manifest_repo.py`, each compare-and-set transition becomes a
        builder returning `(sql, params, expected)`, plus the existing async
        executor. Every async function keeps its signature
  - [x] Add `execute_transition_sync(cur, statement, unit_id)`. It runs in
        the caller's transaction and raises `ManifestTransitionError` on
        zero rows
  - [x] Add a builder for `mark_superseded(unit_id, by_unit_id)`,
        compare-and-set on "current" (not superseded, not reopened)
  - [x] `manifest_repo.py` is at 295 lines. Put the sync executor and
        `mark_superseded` in a new module (for example
        `manifest_ingest.py`) and note the split
  - [x] Success: imports; no SQL string is duplicated between the async and
        sync paths
  - [x] Effort: 2
  - Note: the new module is `manifest_transitions.py`. It holds `ManifestTransitionError` (still importable from `manifest_repo`), `TransitionStatement` (unit_id, sql, params, expected), `transition_statement(...)`, `execute_transition` (async, own transaction), `execute_transition_sync(cur, statement)` and `mark_superseded_statement(unit_id, by_unit_id)`, which is compare-and-set on `COVERAGE_PREDICATE`. The unit id travels inside the statement, so the sync executor takes `(cur, statement)`. `manifest_repo.py` (298 lines) routes every transition through a statement and adds `mark_ingested_statement`.

- [x] **1.6 Transition tests**
  - [x] All existing manifest tests pass unchanged
  - [x] Integration on `migrated_tick_db`:
    - [x] `mark_ingested` through the sync executor inside an open transaction
      moves *verified* → *ingested*, and a rollback leaves the unit
      *verified*
    - [x] zero matched rows raises `ManifestTransitionError`
    - [x] `mark_superseded` sets the link once, and a second call raises
  - [x] Success: passes
  - [x] Effort: 2
  - [x] Commit: `refactor(tick): split manifest transitions into statements and executors`
  - Note: tests are in `test/integration/data/test_tick_manifest_transitions.py`. The same commit updates `test/integration/data/test_tick_pass.py` to iterate `ACQUISITION_PHASE_NAMES` (a 1.3 follow-up that the integration tier caught).

---

## Section 2: Checks and record processing (TD6, TD8)

`ingest_records.py` is pure NumPy over one `RecordBatch`, with no I/O. Its
unit tests run on the 0.2 real slices. Synthetic data is used only for
failure shapes a real file cannot produce, and it is built by editing a
real batch.

- [x] **2.1 Create `data/tick/ingest_checks.py`**
  - [x] `IngestCheck(StrEnum)`: `counts`, `resolution`, `session_boundary`,
        `overlap`, `shape`, `decode`
  - [x] One reason formatter per check, each reason starting with
        `<check>:`, carrying the evidence TD8's table names (counts P/D/S;
        first id, time and count; UTC and calendar-zone time with
        neighbouring sessions; the conflicting key and other units; the
        path and error; the populated span for a planning failure)
  - [x] Unit test: every formatter's text starts with its check's value
  - [x] Success: passes
  - [x] Effort: 1
  - [x] Commit: `feat(tick): add ingest check names and reasons` (fdf5c24)
  - Note: reasons are formatted by `counts_reason`, `resolution_reason`, `outside_span_reason`, `no_session_reason`, `planning_span_reason`, `overlap_reason`, `shape_reason` and `decode_reason`, plus the helper `utc_of_ns`. The tests are in `test/unit/data/tick/test_ingest_checks.py`.

- [x] **2.2 Contract resolution in `ingest_records.py` (TD6)**
  - [x] Definitions arrive as arrays sorted by `(instrument_id,
        activation_ns)`. Resolve each record by `searchsorted` on the id and
        then the window test, looping only over ids with more than one
        window that day
  - [x] Return the resolved mask, plus the first unresolved id, its time
        and the unresolved count
  - [x] Success: imports
  - [x] Effort: 2

- [x] **2.3 Resolution tests**
  - [x] The real trades slice resolves fully against its day's definitions
  - [x] A record whose id is edited to an unknown id is reported with its
        time and count
  - [x] A record outside its id's window fails
  - [x] An id with two windows on one day resolves each record to the
        window that holds it
  - [x] Success: passes
  - [x] Effort: 2
  - [x] Commit: `feat(tick): resolve tick records to contracts` (01b1771)
  - Note: `ingest_records.py` (299 lines) holds resolution, sessions, ordinals and the ledger, so 2.2–2.9 went in as one commit (01b1771, "resolve, place and order tick records and accumulate the ledger"). The three per-pair commit lines are satisfied by it. A failed check raises `UnitCheckFailed(check, reason)`. All the tests are in `test/unit/data/tick/test_ingest_records.py` (13 tests), with sessions built by hand as CME_EQUITY's 17:00→16:00 America/Chicago.

- [x] **2.4 Session location (TD6)**
  - [x] Test the populated span first: a record outside it is the "outside
        the populated calendar range" failure. Then `locate_ns`, where `-1`
        is the "in no session" failure
  - [x] Return session positions per record
  - [x] Success: imports
  - [x] Effort: 1

- [x] **2.5 Session location tests**
  - [x] The real slice's records on both sides of 00:00 UTC land in the
        right sessions
  - [x] A record edited into the daily break fails as "in no session"
  - [x] A record edited past the populated span fails as "outside",
        not as a break
  - [x] Success: passes
  - [x] Effort: 1
  - [x] Commit: `feat(tick): assign tick records to sessions` (01b1771)
  - Note: `SessionFrame(index, first_open, last_close, zone)` carries the calendar's populated span and time zone; `locate_sessions(frame, ts_event)` returns positions or raises.

- [x] **2.6 `sequence_ordinal` with a cross-batch carry (TD6, 222 TD1)**
  - [x] Within a batch: stable lexsort and run lengths. Across batches: add
        each triple's earlier count from a sorted carry array of distinct
        triples, merged per batch. No contiguity assumption
  - [x] Success: imports
  - [x] Effort: 3

- [x] **2.7 Ordinal tests (FR8, part)**
  - [x] A non-adjacent repeat gets ordinals 0 and 1
  - [x] A repeat that straddles a batch boundary gets 0 and 1 (split one
        real batch in two)
  - [x] Ordinals for the whole real slice equal a brute-force count
        computed in the test
  - [x] Success: passes
  - [x] Effort: 2
  - [x] Commit: `feat(tick): compute sequence ordinals across batches` (01b1771)
  - Note: `OrdinalCarry` packs `instrument_id << 32 | sequence` into one uint64 beside `ts_event`. A test also runs the whole real file through the reader in more than 30 batches (patched `TICK_DECODE_BATCH_BYTES`) and compares against the brute-force count.

- [x] **2.8 Ledger accumulation (TD6)**
  - [x] Accumulate per (instrument, session): count, volume (sum of
        `size`), first and last `ts_event`
  - [x] The ledger's instrument set: every definition with `asset =
        product` whose window meets the session. Missing instruments become
        zero-record rows with NULL times. `calendar_id` comes from
        `FUTURES_PRODUCT_CALENDAR`; `session_date` comes from the session
  - [x] Success: imports
  - [x] Effort: 2

- [x] **2.9 Ledger tests**
  - [x] Over the real slice:
    - [x] the ledger's `record_count` sums to the decoded count
    - [x] zero-record rows exist for valid instruments with no records
    - [x] the instrument set equals the definitions valid in each session
  - [x] Success: passes
  - [x] Effort: 2
  - [x] Commit: `feat(tick): accumulate the ingest ledger` (01b1771)
  - Note: `LedgerAccumulator.add(ids, positions, size, ts_event)` and `.rows(definitions, frame, calendar_id)` return `LedgerRow`s. `rows` asserts that no accumulated (instrument, session) falls outside the instrument set, because that would be a defect.

- [x] **2.10 COPY row building (TD2)**
  - [x] In its own module (for example `ingest_rows.py`), since this is
        the seam 226 may replace with a NumPy binary encoder: one function
        building rows in `TICK_TRADE_COLUMNS` order plus `sequence_ordinal`
        and `unit_id`, plus the matching COPY column list and types
  - [x] Test: row tuples match the column list; values round-trip for one
        real record
  - [x] Success: passes; `ingest_records.py` under ~300 lines
  - [x] Effort: 1
  - [x] Commit: `feat(tick): build tick_trade COPY rows` (2cab9f2)
  - Note: `ingest_rows.py` exports `COPY_COLUMNS`, `COPY_TYPES`, `COPY_SQL` and `copy_rows(records, ordinals, unit_id)`. The unit test is `test/unit/data/tick/test_ingest_rows.py`. The integration test `test/integration/data/test_tick_ingest_rows.py` checks `COPY_TYPES` against the migrated table's column types and round-trips one real record of each tier through `COPY`.

---

## Section 3: Selection and plan (TD4, TD5, TD7)

- [x] **3.1 Create `data/tick/ingest_select.py`**
  - [x] Select units that meet all of these: the schema is in
        `STORED_TIERS`; `state = verified`; `fetch_status` is open; the unit
        is current (`COVERAGE_PREDICATE`); no current higher-tier unit
        exists for the same shape and day (the rank SQL from 0.3); its
        companion definition unit is *ingested*
  - [x] Skip, do not fail, with a reason: "outranked by unit N" or
        "awaiting definitions"
  - [x] `--unit-id` narrows the selection and never overrides it. A named
        unit that is not selectable is reported with its reason
  - [x] Order by `unit_date`, then `unit_id`
  - [x] Success: imports
  - [x] Effort: 2
  - Note: `select_units(conn, unit_ids=()) -> Selection(selected, skipped)`. `Skipped(unit_id, kind, detail)` uses `SkipKind` (`awaiting_definitions`, `outranked`, `changed_during_ingest`, `not_selectable`); a named unit that is not selectable gets `not_selectable` with a reason such as "fetch_status is RETRY_EXHAUSTED; reset it first", "superseded by unit N" or "no such unit". Outranking counts any current higher-tier unit of the shape and day in any state. `manifest_reads.py` now exposes `UNIT_COLUMNS`, `UNIT_FROM` and `unit_row` (renamed from `_unit_row`) so the selection query extends the unit select list without string surgery.

- [x] **3.2 Selection tests (FR4)**
  - [x] Integration on `migrated_tick_db`, one case per rule:
    - [x] a unit is skipped as "awaiting definitions" until its companion is
      *ingested*, and selected after
    - [x] a `trades` unit with a current `tbbo` unit on the same day is
      "outranked"
    - [x] a superseded unit, a reopened unit and an exhausted unit are not
      selected
    - [x] an explicit `--unit-id` for an unselectable unit returns its reason
    - [x] order follows `unit_date`, then `unit_id`
  - [x] Success: passes
  - [x] Effort: 2
  - [x] Commit: `feat(tick): select units for ingest` (045503d)
  - Note: the tests are in `test/integration/data/test_tick_ingest_select.py` (5 cases).

- [x] **3.3 Create `data/tick/ingest_plan.py`**
  - [x] An immutable `UnitIngestPlan` per unit, built on the loop through
        `asyncio.to_thread`: product (`planning_product`), calendar,
        `sessions_between(day 00:00Z, day+1 00:00Z)`, populated span,
        `SessionIndex`, definition arrays (`asset = product`, windows
        meeting the unit's session span), and the superseded current
        lower-tier units with their ledger min/max event times
  - [x] A non-`parent` `stype_in` becomes a `shape` failure
  - [x] `OutOfPopulatedRangeError` becomes a `session_boundary` failure
        naming the day and span (TD7). An unreachable calendar raises
        `TickCalendarError` (→ `storage_abort`)
  - [x] Success: imports
  - [x] Effort: 2
  - Note: `build_plan(conn, calendars, unit) -> UnitIngestPlan(unit, product, calendar_id, frame, definitions, superseded)`. `Calendars(url)` keeps one `TradingCalendar` per product for the run, and its blocking `frame(product, day)` runs through `asyncio.to_thread`. `SupersededUnit(unit_id, state, first_event_ns, last_event_ns)` covers current lower-tier units in any state; only ingested ones have rows to delete. A new public `TradingCalendar.zone()` returns the calendar's time zone. The same commit fixes that file's pre-existing E501 and two mypy errors (asserts in the extended-hours branch), so the touched file is clean.

- [x] **3.4 Plan tests and the horizon test (TD7)**
  - [x] Unit: `session_days` over a calendar whose last close is mid-day X
        returns no day at or after X, and raises for `[.., X+1)`
  - [x] Integration:
    - [x] a plan for a real day holds two sessions and 41 definitions
    - [x] a `raw_symbol` unit fails as `shape`
    - [x] a unit whose day lies past the populated span fails as
      `session_boundary`
    - [x] an unreachable calendar URL raises `TickCalendarError`
    - [x] with an ingested `trades` unit and a verified `tbbo` unit on the same
      day, the `tbbo` plan's superseded list holds the `trades` unit with
      the min first and max last event times from its ledger
  - [x] Success: passes
  - [x] Effort: 2
  - [x] Commit: `feat(tick): build per-unit ingest plans` (ec1f7a2)
  - Note: **design correction.** A real day's definition file holds 61 *records* but **41 instruments**: 21 outrights and 20 spreads, each spread sent twice under one key. The LLD's "61 instruments per day, 21 outrights and 40 spreads" is wrong, so a unit writes about 82 ledger rows, not about 122. Correct the LLD (Prerequisites, TD6 ledger rows, the status sample's "40 spreads hidden") in Section 7. The plan test asserts 41. The horizon test is `test_no_day_at_or_after_a_mid_day_horizon_is_emitted` in `test/unit/data/tick/test_session_days.py`; the plan tests are in `test/integration/data/test_tick_ingest_plan.py`.

---

## Section 4: The worker (TD2, TD5, TD8)

- [x] **4.1 Supersession step (TD5)**
  - [x] In `ingest_worker.py`, a function that runs on the worker's open
        cursor, before the COPY, for each superseded unit in the plan: call
        `mark_superseded` through the sync executor. If that unit was
        *ingested*, delete its rows `WHERE unit_id = O AND ts_event BETWEEN`
        its ledger min first and max last event time. Its ledger rows stay
  - [x] Success: imports
  - [x] Effort: 2

- [x] **4.2 The worker function**
  - [x] A plain synchronous function
        `ingest_unit(plan, archive_root, conn_settings) -> UnitOutcome`.
        `UnitOutcome` holds the unit id, counts, check failure or none,
        duration, and the decode and write time split
  - [x] `conn_settings` is a frozen `WorkerConnectionSettings` (tick URL,
        connect timeout, keepalive idle, interval and count, lock timeout).
        The pass builds it from the 0.3 constants. The worker has no
        defaults of its own, so tests pass a short lock timeout directly
  - [x] Opens its own `psycopg.Connection` with those settings. One
        transaction:
    1. supersession (4.1)
    2. binary `COPY` with `set_types` and `write_row`, fed batch by batch
       from `iter_batches`
    3. counts check: provider = decoded = `count(*) WHERE unit_id = N`,
       bounded by the unit's `ts_event` range
    4. ledger insert
    5. `mark_ingested` through the sync executor
    6. `COMMIT`
  - [x] A check failure rolls back and returns the failure
  - [x] Catch only these:
    - `UniqueViolation` → `overlap`, naming the key and the other current
      units on the day
    - the named decode and file errors → `decode`
    - `ManifestTransitionError` → skip "changed during ingest"

    Everything else propagates
  - [x] Write the thread state review from TD2 as the module docstring
  - [x] Success: imports; under ~300 lines
  - [x] Effort: 4
  - Note: the signature is `ingest_unit(plan, archive_root, settings, reader, clock) -> UnitOutcome`. The reader and clock are passed explicitly because the worker needs `now` for `mark_ingested`. `UnitOutcome` has `unit_id`, `result` (`UnitResult`: ingested, failed, changed_during_ingest), `decoded`, `check`, `reason`, `duration_seconds`, `decode_seconds` and `write_seconds`. The worker is 284 lines. The same commit adds `TickFileDecodeError` to `provider.py`, and `DbnFile` now raises it for the SDK's `DBNError`/`BentoError`, so the worker needs no SDK import. A missing file stays `FileNotFoundError` (`OSError`); the worker catches `(TickFileDecodeError, OSError)` as `decode`.
  - Note: **measured.** A file truncated at a zstd frame boundary (60,000 of 66,000 bytes) decodes 2,661 of 3,774 records with only a `BentoWarning`. The counts check catches it, not decode. A shorter cut (20,000 bytes) raises `DBNError` → `decode`, and that is the "truncated" case the test uses. Turning the warning into an error would need `warnings.catch_warnings`, which is not thread-safe, so it is not done.

- [x] **4.3 Worker tests (FR3, FR5, FR8)**
  - [x] Integration on `migrated_tick_db` with the 0.2 fixtures:
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
  - [x] Success: passes
  - [x] Effort: 3
  - [x] Commit: `feat(tick): add the ingest worker`
  - Note: the tests are in `test/integration/data/test_tick_ingest_worker.py` (10 tests) with helpers in `test/tick_support/ingest.py`. The session-boundary cases shrink the plan's `SessionFrame` (first session closing 10 h early for "no session"; the populated span ending there for "outside"). A unit writes 82 ledger rows (41 instruments × 2 sessions).

- [x] **4.4 Supersession and overlap tests (FR5, FR6)**
  - [x] Integration, with the 0.2 supersession fixture:
    1. `tbbo` over an ingested `trades` unit of the same day replaces its
       rows in one transaction: the link is set, the `trades` rows are
       gone, its ledger rows stay, and session totals equal the `tbbo`
       ledger (FR6)
    2. a forced failure after supersession (the counts check) rolls back:
       the `trades` rows and unit are untouched
    3. overlap: a second current unit whose rows collide with loaded rows
       fails as `overlap`, naming the key and the other unit (FR5)
  - [x] Success: passes
  - [x] Effort: 2
  - [x] Commit: `test(tick): cover supersession and overlap in the worker`
  - Note: the tests are in `test/integration/data/test_tick_ingest_supersession.py`. `overlap_reason(detail, day, others)` passes PostgreSQL's "Key (...)=(...) already exists." through unchanged.
