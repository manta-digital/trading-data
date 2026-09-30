---
docType: tasks
slice: historical-acquisition-pass
project: trading-data
lld: user/slices/224-slice.historical-acquisition-pass.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [923, 220, 221, 222, 223]
interfaces: [225, 226, 227, 228, 229, 231, 233]
projectState: >
  Slice 223 (tick archive adoption) complete: tick_006, run context and lock,
  compare-and-set manifest repository, verification, session days, adopt and
  reset verbs, archive backup enrolment; both free-credit jobs adopted into
  the scratch database mt_scratch_tick_223. No purchase code exists yet. The
  PM has set both spend ceilings in the dev .env (0.50 per pass, 5 per 30
  days).
dateCreated: 20260928
dateUpdated: 20260930
status: complete
---

# Tasks: Historical Acquisition Pass

## Context Summary

- Working on slice 224. It builds `mt data tick pass`: five phases
  (reconcile, availability, purchase, await, definitions) on a copy of the
  Kalshi pass contract, with the pure planner and spend guard behind them.
  Its walkthrough makes the initiative's first live purchase: the
  definitions for the two adopted jobs, about $0.004.
- The LLD is `user/slices/224-slice.historical-acquisition-pass.md`. Its
  "Slice Split" section lists what 223 already built; this slice uses those
  modules and does not rebuild them.
- Tasks cite the LLD's Technical Decisions as "TD n (short name)": TD1 pass
  contract copy, TD2 run context and lock, TD3 universe constant, TD4 wanted
  days and monthly grouping, TD5 definitions, TD6 calendar dependency, TD7
  spend guard, TD8 reopened units, TD9 submit and reconcile, TD10 adoption,
  TD11 archive and backup. FR n means the LLD's Functional Requirement n.
- **Money rules for every task here.** The only paid call is
  `submit_batch`, and only after the guard allows it. Nothing calls
  `fetch_range`. No test builds a real client.
- Every destructive statement targets a database a fixture or the
  walkthrough created (`sql.md`).
- Next slice: 225 (ingest pass and proof parity).

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
`metadata_responses.py`. Day files and job directories come from 223's
`test/tick_support/dbn_files.py`. The calendar in integration tests comes
from `session_migrated_db` (it holds `CME_EQUITY`).

**File size.** Source files stay under about 300 lines. If one would pass
that, split along the phase or concern it holds and note the split.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 0 — Baseline, helpers and constants

- [x] **0.1 Record the pre-change test baseline**
  - [x] On the slice branch before any change, run the unit tier, then the
        integration tier; save failing ids to `/tmp/224-baseline-unit.txt`
        and `/tmp/224-baseline-integration.txt`
  - [x] Confirm `mt_scratch_tick_223` exists and holds the two adopted jobs
        (the LLD walkthrough step 4 query). If it is gone, re-create it with
        walkthrough steps 2–3 (re-adopting from `/data/tick-archive`)
  - [x] Success: both files exist; the scratch manifest holds 26 + 52 units
  - [x] Effort: 1
  - Note: baseline on the slice branch before any change: unit tier 0 failures (4056 passed); integration tier 6 failures, all known (test_cli_lists priority1 x2, test_migration_051_052 x2, test_policy_advances_head x2). /tmp/224-baseline-unit.txt is empty; /tmp/224-baseline-integration.txt lists the six. mt_scratch_tick_223 holds 26 + 52 verified units.

- [x] **0.2 Job-list helper and the pass's constants**
  - [x] `test/tick_support/batch_responses.py`: add `job_list(*records)` for
        `batch_jobs_since`
  - [x] `data/tick/constants.py`: `TICK_WAIT_BUDGET_SECONDS = 1800`,
        `TICK_POLL_INTERVAL_SECONDS = 15` (both noted as 226 re-sets them
        from measurement), `TICK_JOB_MATCH_SKEW = timedelta(minutes=5)`,
        `TICK_SUBMIT_RESOLVE_AGE = timedelta(hours=1)`, `TICK_SPEND_WINDOW =
        timedelta(days=30)`, each with a one-line comment giving its TD
  - [x] Extend `test/unit/data/tick/test_constants.py` with their values
  - [x] Success: `uv run pytest test/unit/data/tick -q` passes
  - [x] Effort: 1
  - [x] Commit: `feat(tick): add acquisition pass constants`

---

## Section 1 — Pass contract (TD1)

- [x] **1.1 Create `data/tick/pass_contract.py`**
  - [x] Copy of `data/kalshi/collection_pass.py`'s `PhaseReport`,
        `PassResult` (with `to_dict()`), `PassPhase` protocol, `SKIPPED`,
        `classify_pass` and runner, field for field; no import of
        `data.kalshi`
  - [x] `TickOutcome(StrEnum)`: `ok`, `partial`, `provider_abort`,
        `storage_abort` with Kalshi's values, plus `refused` and `in_flight`;
        precedence worst first: `storage_abort`, `provider_abort`, `partial`,
        `refused`, `in_flight`, `ok`. Only the two aborts skip later phases
  - [x] `TickPassPhaseName(StrEnum)`: the five phase names
  - [x] No event sink or `on_phase`; logs "tick pass started run_id=…
        phases=…" and "tick pass finished outcome=…"
  - [x] Success: imports; under ~300 lines
  - [x] Effort: 2

- [x] **1.2 Parity test (FR9)**
  - [x] `test/unit/data/tick/test_pass_contract_parity.py`, the four checks
        in TD1 (the test): field names, order and annotations of both
        dataclasses (differing only in the outcome type); `set(TickOutcome) −
        set(SyncOutcome) == {refused, in_flight}`; shared members' values
        equal; abort-then-skip behaviour equal on the same report sequence
  - [x] Plus precedence tests for `classify_pass` over every pair of outcomes
  - [x] Success: passes; adding a field to either `PhaseReport` locally makes
        it fail (check, then revert)
  - [x] Effort: 2

- [x] **1.3 Review the rest of the Kalshi contract diff**
  - [x] Read `data/kalshi/collection_pass.py` and `sync_types.py` beside the
        copy. For every Kalshi element not copied (event sink, `on_phase`,
        historical phase, anything else), confirm it is one of TD1's declared
        divergences or name it in a task note for the PM
  - [x] Success: note lists each element and its disposition
  - [x] Effort: 1
  - Note (dispositions of Kalshi contract elements not copied): HistoricalPhase and PassPhaseName.HISTORICAL — TD1 declared (no historical phase). CatalogPhase/CandlesPhase/TradesPhase/HistoricalPhase classes and PASS_PHASES — Kalshi-specific; the tick phases and PASS_PHASES are built in 8.3. CollectionPass on_phase callback, event sink (SyncEvent, _emit, PASS_STARTED/PASS_FINISHED) — TD1 declared. Start-log fields mode= and budget=/min (Kalshi client) — not applicable; the tick log keeps run_id and phases. sync_types.classify/classify_outcome (the shared phase classification, not in collection_pass.py) — NOT a declared divergence: the tick pass needs its own error-to-outcome mapping, built in section 8 (flag for the PM). PASS_RUN_OUTCOME_BY_SYNC_OUTCOME and pass_runs recording — 233's (LLD Excluded: no schedule code). Finished-event error extraction — dropped with the sink; PhaseReport.error is kept. One change to 223 code for field parity: TickRun.run_id is now a UUID (was str) so PassResult.run_id matches Kalshi's annotation.

- [x] **1.4 Remaining exit codes**
  - [x] In `cli/commands/tick.py`: add `EXIT_REFUSED = 5` and
        `EXIT_IN_FLIGHT = 6` (223 already defines `EXIT_PARTIAL = 3` and
        `EXIT_STORAGE = 4`; reuse them, do not redefine), and `EXIT_BY_OUTCOME` over `TickOutcome` with a
        module-level exhaustiveness assert, as Kalshi's
  - [x] Test: every `TickOutcome` maps; the values are 0–6 and unique
  - [x] Success: passes
  - [x] Effort: 1
  - [x] Commit: `feat(tick): add tick pass contract and exit codes`

---

## Section 2 — Universe (TD3)

- [x] **2.1 Create `data/tick/universe.py`**
  - [x] `TickUniverseEntry` and `TICK_UNIVERSE` exactly as TD3's code block:
        ES, `("ES.FUT",)`, `SType.PARENT`, `tier=None`, no range
  - [x] Import-time validation per TD3 (validated at import), each failure
        naming the field: product has a calendar; symbols non-empty and
        sorted; tier in `STORED_TIERS` when set; `start` required with a
        tier; `end > start`; unique products
  - [x] Comment: spreads are included by explicit configuration, 2.35% of
        adopted trades, 226 re-confirms (TD3, review F004)
  - [x] Success: imports
  - [x] Effort: 1

- [x] **2.2 Universe tests**
  - [x] Validation function tested with each bad entry (parametrized); the
        shipped constant validates
  - [x] Success: passes
  - [x] Effort: 1

---

## Section 3 — Planner (TD4, TD5)

- [x] **3.1 Create `data/tick/planner.py` (pure, no I/O)**
  - [x] Inputs: universe, owned tier days from the manifest (dataset, schema,
        symbols, stype, day), covered keys, session days per product, day
        conditions, `--start/--end` window. Output: `PlannedRequest` list plus
        pending and missing day tallies
  - [x] Wants = companion definitions for owned and to-be-bought tier days
        (same symbols, stype, days) ∪ tier wants for entries with a tier,
        each intersected with the window; minus covered
  - [x] Purchasable = condition `available` or `degraded`; `pending` and past
        the edge → pending; `missing` → missing, no request
  - [x] Group per `(schema, symbols, stype_in)` into runs of consecutive
        session days, broken by any non-purchasable session day, never
        crossing a UTC month; a Saturday does not break a run; `TickRequest(
        first, last + 1)`
  - [x] Order by first day; within a day definitions before tiers
  - [x] Success: imports; no psycopg or provider import
  - [x] Effort: 3

- [x] **3.2 Planner unit tests**
  - [x] Parametrized cases: month boundary splits; a covered day splits; a
        Saturday inside a run does not; pending and missing are tallied and
        not planned; `tier=None` yields companion definitions only; the two
        adopted jobs' days yield exactly four definition requests, 2024-08 to
        2024-12 (FR3); a window narrows and never widens; ordering rule
  - [x] Success: passes
  - [x] Effort: 2
  - [x] Commit: `feat(tick): add tick universe and purchase planner`

---

## Section 4 — Spend guard (TD7)

- [x] **4.1 Create `data/tick/spend_guard.py` (pure)**
  - [x] `evaluate_spend(planned, trailing_rows, unheld_jobs, per_pass,
        cap_30d, now, estimate_only) -> SpendVerdict` per TD7: both ceilings
        required; per-pass and 30-day checks in `Decimal`; the 30-day check
        is trailing + unheld + planned; re-submits already in the trailing
        rows not added again; a refusal names each unheld job and reports planned total, each
        ceiling, each overage, absent settings, and the date the plan fits
        (ageing oldest in-window rows out) or "cap must be raised"
  - [x] `evaluate_space(planned_bytes, free_bytes) -> SpaceVerdict` naming
        the shortfall
  - [x] Success: imports; no I/O
  - [x] Effort: 2

- [x] **4.2 Guard unit tests (FR4)**
  - [x] Either ceiling absent with wants → refused naming both variables; a
        plan inside per-pass but over 30-day → refused with overage and fit
        date; planned alone over the cap → "raise"; $0 plan passes; adopted
        and unaccepted rows in window count; rows outside the window do not;
        `estimate_only` never allows; space shortfall refused; an unheld
        job counts (at `cost_usd`, or at the supplied request cost when
        unpriced) and is named in the refusal; a listed job whose id a row
        holds is not counted (total unchanged)
  - [x] Success: passes
  - [x] Effort: 2
  - [x] Commit: `feat(tick): add spend and space guards`

---

## Section 5 — Availability (TD8 hole reopen)

- [x] **5.1 Create `data/tick/availability.py`**
  - [x] First add to `manifest_repo.py` what this phase needs (6.1 builds on
        them): owned tier days, holed units' days, and a compare-and-set
        "reopen a day's holed units" transition (`reopened_at = now` on
        `PROVIDER_HOLE` units not yet reopened)
  - [x] Span per the LLD's "Availability capture": universe tier ranges,
        owned tier days and holed units' days, narrowed by the window and
        clipped to `dataset_range`
  - [x] One `dataset_range` and one `dataset_condition` call per run; upsert
        `tick_dataset_edge`; per day upsert `tick_day_condition`; a changed
        `(condition, last_modified_date)` reopens that day's holed units in
        the same transaction (through `manifest_repo`)
  - [x] Returns the edge and a condition tally
  - [x] Success: imports
  - [x] Effort: 2

- [x] **5.2 Availability integration tests (FR6, reopen on change)**
  - [x] On `migrated_tick_db` with the metadata fakes: first run inserts rows;
        a second identical run changes nothing; a changed condition on a
        holed day reopens that unit and leaves other days alone
  - [x] Success: passes
  - [x] Effort: 2
  - [x] Commit: `feat(tick): capture dataset edge and day conditions`

---

## Section 6 — Delivery and reconcile (TD9)

- [x] **6.1 Manifest functions for the pass**
  - [x] Add to `manifest_repo.py`: insert request and units at *requested*
        with `fetch_status = UNKNOWN`, `attempt_count = 1`, `last_attempt_at
        = now` (TD9: stamped before the paid call; a re-submit after `reset`
        re-stamps the same row) (with repurchase and supersession links for reopened days, one
        transaction, TD8); record submit (job id, `committed_at`, units →
        *submitted*); requests without a job id; mark delivered (actual cost,
        counts, deadline); expiry sweep candidates; trailing spend rows; owned
        tier days; covered keys
  - [x] Integration tests for each, including the supersession pair written
        atomically and the sweep excluding `PROVIDER_HOLE`
  - [x] Success: passes
  - [x] Effort: 3
  - [x] Commit: `feat(tick): add submit and delivery manifest transitions`
  - Note (file split): the pass's new manifest writes are in data/tick/manifest_pass.py and its reads in manifest_reads.py (manifest_repo.py would have passed ~300 lines; manifest_reads.py is ~310). ADVANCE_SET and advance_params were made public in manifest_repo.py for the split. mark_ingested (needed by 7.1) is in manifest_repo.py. Tests: test/integration/data/test_tick_manifest_pass.py.

- [x] **6.2 Create `data/tick/in_flight.py`**
  - [x] `resolve_unsubmitted(run)`: for each request with no job id,
        `batch_jobs_since(requested_at − TICK_JOB_MATCH_SKEW)`; exact
        `TickRequest` match whose id no row holds → record submit; no match
        and attempt younger than `TICK_SUBMIT_RESOLVE_AGE` → stays
        `FAILED_RETRYABLE` ("submit outcome unresolved"); older → exhausted
        ("… `reset` to re-submit"). Never re-submits
  - [x] `sweep_expired(run)`: deadline passed, no file, not a hole →
        exhausted "retention expired at …" plus `reopened_at`
  - [x] `advance(run)`: the LLD's `in_flight.advance()` flow: poll each
        submitted job; done → delivered; expired → exhausted + reopened;
        unknown state → exhausted naming it; download delivered jobs earliest
        deadline first into `<archive>/<job_id>`; match files by header;
        missing day → hole; write `manifest.json` if absent; verify each
        downloaded unit
  - [x] Download failure → transient attempt + provider abort; `OSError` →
        storage abort with no attempt counted; a `ProviderError` from
        `verify.check`'s record-count call → provider abort, remaining units
        left *downloaded* for the next run (LLD failure table, as adopt)
  - [x] Log per job whether the first `batch_jobs_since` after submit listed
        it (listing-lag measurement, LLD Risk Assessment)
  - [x] Success: imports; under ~300 lines (split resolve/sweep from advance
        if not)
  - [x] Effort: 4
  - Note (file split): download-to-units and manifest.json writing are in data/tick/in_flight_files.py; in_flight.py holds resolve_unsubmitted, sweep_expired, advance and the listing-lag log helper. Provider fake for these tests: test/tick_support/fake_provider.py (stateful, records calls); test/tick_support/runs.py builds a TickRun around it.

- [x] **6.3 Delivery tests (FR5, FR6)**
  - [x] Unknown submit then a listed job → units *submitted*, zero submits
  - [x] Crash after the pre-submit insert (before `submit_batch`) and crash
        after an accepted submit (before recording it): both leave
        `attempt_count = 1` with `last_attempt_at` set; the next run matches
        the accepted one, holds the other unresolved, and neither is
        re-submitted
  - [x] No listed job: stays retryable under the age, exhausted after it (a
        fixed clock)
  - [x] Past-deadline unit swept with `reopened_at`; expired and unknown job
        states exhaust naming the state
  - [x] Two delivered jobs download in deadline order (recorded call order)
  - [x] A missing day file → `PROVIDER_HOLE`; transient download failure
        counts one attempt, the fifth exhausts
  - [x] `manifest.json` written when the job lacked one, and it lists every
        other file
  - [x] A `ProviderError` from the record-count call during verify →
        provider abort; the unverified units stay *downloaded*, and the next
        `advance()` verifies them
  - [x] An injected `OSError(ENOSPC)` during download raises the storage
        error naming path and errno, and the unit's `attempt_count` is
        unchanged
  - [x] Success: passes
  - [x] Effort: 3
  - [x] Commit: `feat(tick): add in-flight reconcile, delivery and expiry`

---

## Section 7 — Definitions projection (TD5)

- [x] **7.1 Create `data/tick/definitions.py`**
  - [x] Scope: definition units at *verified*, open status, earliest day
        first; selects only `TickSchema.DEFINITION` requests
  - [x] Per unit, one transaction: decode through `DbnFileReader`, map via
        `TICK_DEFINITION_COLUMNS`, sentinels → `NULL`; undefined activation or
        expiration → exhausted naming `instrument_id` and `raw_symbol`;
        in-file duplicates compared first; existing same key: all kept
        columns equal → no-op, else exhausted naming instrument and fields;
        new → insert; `ExclusionViolation` → exhausted naming both windows;
        success → *ingested* with `decoded_record_count`
  - [x] Never `ON CONFLICT DO NOTHING`
  - [x] Success: imports
  - [x] Effort: 3

- [x] **7.2 Definitions tests (FR7)**
  - [x] Integration on `migrated_tick_db` over day files built from the
        definition fixture with hand-set windows: insert; identical re-send
        no-op; one changed kept field fails naming it; undefined activation
        fails; overlapping window for a reused id fails naming both; success
        is *ingested*
  - [x] Unit: the phase's selection excludes a verified `trades` unit
        (TD5, bounded exception)
  - [x] Success: passes
  - [x] Effort: 3
  - [x] Commit: `feat(tick): project definition units into tick_definition`

---

## Section 8 — The pass and its verb

Error mapping for every phase: `ProviderError` → `provider_abort`,
`psycopg.OperationalError` and the archive write error → `storage_abort`,
calendar `OutOfPopulatedRangeError` or an unreachable calendar database →
`storage_abort` naming it. No catch-all.

- [x] **8.1 Create `data/tick/purchase_phase.py`**
  - [x] Calendar (as 223's `adopt.py` opens it) → `session_days` → planner → free `cost`
        and `billable_size` per request → space guard (free bytes of the
        archive volume) → unheld jobs (`batch_jobs_since(now −
        TICK_SPEND_WINDOW)`, ids no row holds, `cost()` for unpriced ones)
        → spend guard → submit in order: insert at
        *requested*, `submit_batch`, record submit (TD9)
  - [x] TD9 outcome rules: `ProviderOutcomeUnknownError` → units
        `FAILED_RETRYABLE`, stop submitting, `provider_abort`; 429 →
        `FAILED_RETRYABLE`, `provider_abort`; other 4xx → units exhausted,
        continue, `partial`; a jobless row is submitted only at
        `attempt_count = 0` (after `reset`); every other jobless row is
        skipped; the unknown path does not count the attempt again
  - [x] Summary fields per the LLD's API Contracts purchase line
  - [x] Success: imports; under ~300 lines
  - [x] Effort: 3
  - Note (splits): the phase is split along its concern. data/tick/purchase_plan.py builds the plan (calendar sessions, planner inputs, costs, unheld jobs, both guards, summary); purchase_phase.py holds the submit loop and PurchasePhase; phase_support.py holds run_phase (the shared error-to-outcome mapping, the tick equivalent of Kalshi's classify_outcome noted in 1.3). The calendar access shared with adopt was extracted into data/tick/tick_calendar.py (TickCalendarError, product_of_shape, product_session_days); adopt.py now uses it and TickCalendarError is imported from there. Tests: test/integration/data/test_tick_purchase_phase.py (the seven listed cases plus estimate-only, a reset row re-submitted on the same request, and a no-wants case) and test/unit/data/tick/test_pass_money_paths.py (an AST check: nothing calls fetch_range; only purchase_phase.py calls submit_batch).

- [x] **8.2 Purchase phase tests**
  - [x] Integration, fake provider, `migrated_tick_db`, calendar from
        `session_migrated_db`:
    1. a 4xx refusal on the first of two requests: its units exhausted, the
       second submitted, outcome `partial`
    2. a 429: units `FAILED_RETRYABLE`, no further submit, `provider_abort`
    3. an unknown outcome on the first of two requests: exactly one
       `submit_batch` call, `provider_abort`
    4. ceilings unset with wants → `refused`, no submit; `--estimate-only` →
       `ok`, no submit
    5. Σ `billable_size` above an injected free-bytes value → `refused`
       naming the shortfall, no submit
    6. a listed job no row holds pushes the plan over the 30-day cap →
       `refused` naming the job id, no submit; a listed job a row holds is
       filtered out and not counted
    7. the calendar raises `OutOfPopulatedRangeError` (and, separately, is
       unreachable) → `storage_abort` naming it, no submit
  - [x] Unit: the only paid method invoked on the fake is `submit_batch`, and
        `fetch_range` is never called
  - [x] Success: passes
  - [x] Effort: 3
  - [x] Commit: `feat(tick): add tick purchase phase`

- [x] **8.3 Create `data/tick/acquisition_pass.py`**
  - [x] The other four phases and `PASS_PHASES`, following the LLD's pass
        data flow: reconcile (`resolve_unsubmitted`, `sweep_expired`, one
        `advance`); availability; purchase (8.1); await (`advance` every
        poll interval up to the wait budget, then `in_flight` listing jobs
        and deadlines; budget and interval injectable); definitions
  - [x] Success: imports; under ~300 lines
  - [x] Effort: 2
  - Note: PASS_PHASES is the factory pass_phases(window, estimate_only, reader, ...) rather than a module tuple, because the purchase phase hands the await phase the jobs it submitted (PassState) and the await phase needs the run's timing. run_pass() runs it. The await phase skips waiting under --estimate-only; the wait budget counts time slept between polls, never time inside advance().

- [x] **8.4 Whole-pass tests**
  - [x] Integration, same setup as 8.2:
    1. two passes back to back after an unknown submit: exactly one
       `submit_batch` call (FR5)
    2. reset of an unresolved-exhausted row: the next pass searches the list
       again, then re-submits the same row
    3. a delivered job is downloaded before the first `submit_batch` of the
       same pass (the fake's recorded call order) (FR5)
    4. a job still processing at budget end → `in_flight` with its deadline
       (FR8)
    5. a second pass after success plans nothing, `ok` (FR10)
    6. a swept expired day is re-bought with supersession links (FR5)
    7. an `ENOSPC` during reconcile's download → `storage_abort`, later
       phases `SKIPPED`
    8. calendar unreachable with a delivered job in flight: reconcile still
       downloads and verifies it, purchase ends `storage_abort` with no
       submit (TD6)
  - [x] Success: passes
  - [x] Effort: 3
  - [x] Commit: `feat(tick): add tick acquisition pass`

- [x] **8.5 `mt data tick pass` verb and report**
  - [x] `pass [--start] [--end] [--estimate-only] [--json]` in
        `cli/commands/tick.py`; `--end` exclusive; exit from
        `EXIT_BY_OUTCOME`
  - [x] Extend 223's `tick_pass_render.py` with the pass report: phase table, per-phase summaries
        as the LLD's API Contracts list, closing line; `--json` emits
        `{**PassResult.to_dict(), "exit_code": n}`
  - [x] CLI tests: exit code per outcome, `--json` shape, window parsing
  - [x] Success: passes; `tick.py` and `tick_pass_render.py` each under ~300
        lines after both parts' additions
  - [x] Effort: 2
  - [x] Commit: `feat(tick): add mt data tick pass verb`
  - Note (split): the exit-code constants, EXIT_BY_OUTCOME and exit_code_for moved to cli/commands/tick_exit.py so tick.py stays under ~300 lines (279); tick.py re-exports what it uses, so cmd.EXIT_* is unchanged. tick_pass_render.py is 296 lines.

---

## Section 9 — Documents

- [x] **9.1 Contract rows and plan Notes**
  - [x] `user/reference/data-correctness-architecture.md`: 224's part in
        rows I9 (the unknown-outcome reconcile), I10 (the `pass` verb), I11
        (the pass's manifest writes, availability capture), I12
        (repurchase and supersession links), I14 (definitions with enforced
        windows)
  - [x] Slice plan Notes: "Statements superseded by 224's slice design",
        the six items from the LLD section of that name, one line each
  - [x] Success: `dateUpdated` bumped on both
  - [x] Effort: 1

- [x] **9.2 README and CHANGELOG**
  - [x] README "Futures tick data": `mt data tick pass`, its phases, both
        spend ceilings and the exit codes
  - [x] CHANGELOG `[Unreleased]` Added entries, user-facing wording
  - [x] Success: all updated
  - [x] Effort: 1
  - [x] Commit: `docs: record tick acquisition pass in contract, plan and readmes`

---

## Section 10 — Validation

- [x] **10.1 Lint, types and tiers**
  - [x] ruff and mypy per the test environment note; unit then integration tier; compare
        with the Section 0 baseline
  - [x] Success: no failure outside the baseline
  - [x] Effort: 2
  - Note: final tiers vs baseline: unit 4,156 passed, 0 failed; integration 744 passed, the same six known failures as the baseline (diff of the failing ids is empty).

- [x] **10.2 Walkthrough steps 5 and 6: plan, then the live purchase**
  - [x] Confirm both ceilings load from the dev `.env` (`uv run python -c`
        printing the two `Settings` fields). If either is absent, run step 5
        only, record step 6 as deferred to 226 (LLD Dependencies), skip the
        listing-lag and definition-window observations, and in 10.3 re-adopt
        only the two free-credit jobs
  - [x] In `mt_scratch_tick_223`: step 5 (`--estimate-only` shows four
        definition requests ≈ $0.004; with both ceiling variables unset in
        the shell the pass exits 5 naming both). Step 6 (live purchase with
        the `.env` ceilings): four jobs, definitions *ingested*,
        `tick_definition` query, `manifest.json` in each job directory with
        `sha256sum -c` passing
  - [x] If step 6 fails on a spread without a window or a changed definition
        field, STOP and report the named instrument and field (LLD Risk
        Assessment); do not loosen the rule
  - [x] Record outputs in the LLD walkthrough and the listing-lag
        observation in the findings table; if any job was not listed on the
        first poll, re-set `TICK_SUBMIT_RESOLVE_AGE` from the measurement and
        note it
  - [x] Success: steps 5 and 6 match (or step 6 recorded as deferred)
  - [x] Effort: 2
  - [x] Commit: `docs: record slice 224 purchase walkthrough findings`
  - Note: both ceilings loaded from .env (0.50 per pass, 5 per 30 days). Step 5 needed one fix first: Databento omits Saturdays from get_dataset_condition (parse_conditions now accepts an absent Saturday). Step 6 bought four definition jobs, $0.00409504 in total; the first run then hit a second real-provider fact (download_batch does not create the job directory), fixed in deliver_job, after which the next pass delivered everything with no new spend. All four jobs were listed on the first poll after submit, so TICK_SUBMIT_RESOLVE_AGE stays 1 h. 51 definitions (23 outrights, 28 spreads), every window defined, no changed field. Results are recorded in the LLD walkthrough and its findings table.

- [x] **10.3 Walkthrough steps 7 and 10: idempotence, rebuild, teardown**
  - [x] Step 7: a second `pass` plans nothing and exits 0
  - [x] Step 10: rebuild into `mt_scratch_tick_223b` from every job
        directory under the archive (six, or two if step 6 was deferred),
        compare `provider_job_id`, `actual_cost_usd` and `committed_at` with
        `mt_scratch_tick_223`, then drop both scratch databases and confirm
        a `pg_database` count of 0 for each
  - [x] Success: rows match; both scratch databases gone; the archive
        remains
  - [x] Effort: 1
  - [x] Commit: `docs: record slice 224 verification walkthrough`
  - Note: step 7 planned nothing (exit 0). Step 10: all six job directories re-adopted into mt_scratch_tick_223b; provider_job_id, actual_cost_usd and committed_at identical; both scratch databases dropped (pg_database count 0 each); the archive keeps its six job directories.
