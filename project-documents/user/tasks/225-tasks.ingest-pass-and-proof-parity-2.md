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

# Tasks: Ingest Pass and Proof Parity (part 2 of 2)

## Context Summary

- Part 2 of slice 225's tasks: Sections 5–8 (the pass and its verb, status
  and coverage, standing checks and documents, validation and walkthrough).
- Part 1, `225-tasks.ingest-pass-and-proof-parity-1.md`, holds Sections 0–4
  and the full context: the TD shorthand, the no-money rule, the test
  environment, fixtures, file-size rule and effort scale. All of it applies
  here unchanged.
- Start this part only when every Section 0–4 item in part 1 is checked.

---

## Section 5: The pass and its verb

- [x] **5.1 Create `data/tick/ingest_pass.py`**
  - [x] `IngestPhase` (a `PassPhase[TickStore]`) and `run_ingest`: select,
        then plan each unit on the loop, then run workers through
        `asyncio.to_thread`, at most `TICK_INGEST_WORKERS` at once
  - [x] A failed unit: `record_failure(deterministic=True)` on the run
        connection, with the reason from 2.1 → outcome `partial`
  - [x] Abort: stop starting units, await every in-flight future
        (`gather(..., return_exceptions=True)`), then re-raise the first
        exception into `run_phase`. Units committed while waiting are in
        the summary
  - [x] Summary: `ingested`, `failed`, `records`, `skipped` (by
        `awaiting_definitions`, `outranked`, `changed_during_ingest`),
        `superseded`, and `units` (per unit: `unit_id`, `unit_date`,
        `schema`, `outcome`, `records`, `reason`, durations)
  - [x] Success: imports; under ~300 lines
  - [x] Effort: 3
  - Note: `run_ingest(store, inputs, summary)` and `IngestPhase(inputs)`; `IngestInputs(reader, worker_settings, calendar_url, workers, unit_ids)` is built by the CLI from constants. The summary's `skipped` also carries `not_selectable` (named units that fail a rule), and `units` lists skipped and unselectable units with their reason. During an abort, only units that committed are reported; nothing is recorded for a unit that did not commit, so it stays *verified* and open and the next run retries it. A `ManifestTransitionError` from `record_failure` itself (the unit changed after its worker returned) is tallied as changed during ingest. 200 lines.

- [x] **5.2 Pass tests (FR1, FR2, FR7)**
  - [x] Integration:
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
    6. a row lock held past the lock timeout (the pass built with a short
       one) ends the run as `storage_abort`, with no attempt counted
    7. a unit reset from another connection between plan and commit is
       tallied in `skipped.changed_during_ingest`, the outcome is `ok`, and
       no failure is recorded
    8. an unreachable production calendar ends the run as `storage_abort`
       before any unit starts
    9. a `raw_symbol` unit alongside a good one: the good one ingests, the
       outcome is `partial`, and the bad one is `RETRY_EXHAUSTED` with a
       `shape:` reason
  - [x] Case 1 uses the 0.2 fixture slices. The full real adopted day end
        to end is covered by the load test in 5.4 and by walkthrough step 4
  - [x] Success: passes
  - [x] Effort: 3
  - [x] Commit: `feat(tick): add the ingest pass`
  - Note: the tests are in `test/integration/data/test_tick_ingest_pass.py`, with helpers (`HookedReader`, `ingest_inputs`, `run_ingest_phase`) in `test/tick_support/ingest.py`. `HookedReader` runs a hook on the worker thread after a file's first batch, which injects mid-unit failures (terminating backends, a worker raising while the other is held mid-unit by events, an operator's status change). The concurrent-acquisition case runs the real acquisition pass (fake provider, no wants) alongside ingest. Stable over 3 runs.

- [x] **5.3 `mt data tick ingest` verb**
  - [x] Build the ingest pass: `TickPass[TickStore]` with the one
        `IngestPhase`, run on `open_tick_store(settings,
        lock_key=TICK_INGEST_LOCK_KEY)`, and the `WorkerConnectionSettings`
        built from the 0.3 constants
  - [x] `ingest [--unit-id N]... [--json]`. `tick.py` is at 279 lines, so
        put this verb, and later status and coverage, in a new
        `cli/commands/tick_store_cmds.py` and register them on `tick_app` in
        `tick.py`. The exit comes from `EXIT_BY_OUTCOME`
  - [x] The report reuses `tick_pass_render.py`'s phase rendering and adds
        the per-unit lines and skip tally shown in the LLD's API Contracts.
        `--json` emits `{**PassResult.to_dict(), "exit_code": n}`
  - [x] CLI tests: the exit code per outcome, the `--json` shape, repeated
        `--unit-id`, an unselectable `--unit-id` whose reason appears in the
        report and in `--json`, and running with `MT_DATABENTO_API_KEY`
        unset (FR11, ingest part)
  - [x] Success: passes; each touched CLI file is under ~300 lines
  - [x] Effort: 2
  - [x] Commit: `feat(tick): add mt data tick ingest verb`
  - Note: the new files are `cli/commands/tick_store_cmds.py` (the verb, and `run_mapped`, the one place run failures become exit codes; `tick.py`'s `_run_writer` now uses it) and `cli/commands/tick_ingest_render.py`. `tick.py` is 282 lines. Unit tests are in `test/unit/cli/commands/test_data_tick_ingest.py`; the end-to-end test with no Databento key is in `test/integration/data/test_tick_ingest_cli.py`. `test_data_tick.py`'s verb list gains `ingest`. Found in passing: Rich fixes its console width at the first print of the process, so `test_data_tick.py`'s long-line assertions depend on test order. The new test modules pin `COLUMNS=200` on their `CliRunner`.

- [x] **5.4 Ingest load test (TD2 throughput targets)**
  - [x] `test/load/test_225_tick_ingest_nfr.py`, following
        `test_224_tick_pass_nfr.py`'s pattern: gated by
        `MT_RUN_LOAD_TESTS=1`, test cluster only, never reading the
        production URL. CI runs no test job (slice 907), so this gate is
        the load tier's gate, as for every other load test. The docstring
        states that
  - [x] Scale: the largest real adopted day, 2024-09-03 `trades` (511,965
        records), from `/data/tick-archive`, ingested through `run_ingest`
        into a fixture database. If the archive file is absent, the test
        fails naming the path. It does not skip
  - [x] Bounds, derived from the targets and recorded in the docstring:
    - the unit's ingest takes at most 120 s, 1/720 of the 24 h of market
      time it covers ("far faster than a day of market time")
    - a heartbeat task shows no event-loop gap above 250 ms. The worker
      runs in a thread, so a long gap means blocking work ran on the loop
  - [x] Success: passes with the gate set; the measured time is noted
  - [x] Effort: 2
  - [x] Commit: `test(tick): add ingest load test`
  - Note: **measured** on manta9000, 3 runs. The 511,965-record day ingests in 1.19–1.23 s (decode 0.04 s, write about 1.17 s) against a 120 s budget, with a longest loop gap of 28–38 ms. The first run failed the gap bound at 336 ms: a 2-D `np.unique` in `LedgerAccumulator.add` held the GIL on the worker thread for 342 ms. It was replaced by a packed 1-D key, one stable argsort and `reduceat` (30 ms). `ingest_records.py` is now 305 lines. `seed_tier_unit` gained `tier_file`/`definition_file` overrides for the archived files.

---

## Section 6: Status and coverage (TD9, TD10, TD11)

- [x] **6.1 Create `data/tick/tick_status.py`: classification (pure)**
  - [x] `TickSessionStatus(StrEnum)` and one precedence tuple, worst first,
        exactly as TD9's table (with `awaiting_ingest`)
  - [x] A day's unit is the highest-ranked current tier unit (TD5's rule).
        A session's condition is the worst of its days (`missing` >
        `degraded` > `pending` > `available`, `unknown` if a day lacks a
        row), with Saturdays exempt
  - [x] "Caught up": every session in scope is `complete`, or
        `provider_hole` on a provider-`missing` day. It is "n/a" with no
        wanted range
  - [x] Success: imports; no I/O
  - [x] Effort: 2
  - Note: `tick_status.py` exports `TickSessionStatus` (declared worst first), `STATUS_PRECEDENCE`, `CONDITION_PRECEDENCE`, `DayUnit`, `DayFacts`, `day_unit`, `classify_day`, `classify_session`, `session_condition`, `SessionVerdict`/`verdict` and `caught_up`. A tie between two current units of the same tier on one day picks the newest `unit_id`.

- [x] **6.2 Classification tests (FR9, part)**
  - [x] One test per bucket; precedence when two rules match; the worst
        condition across a session's two days; the Saturday exemption; a
        `complete` session with a `degraded` day counted as complete and
        tallied as degraded; caught up with and without a wanted range
  - [x] Success: passes
  - [x] Effort: 2
  - [x] Commit: `feat(tick): classify tick sessions for status`
  - Note: the tests are in `test/unit/data/tick/test_tick_status.py` (22 tests).

- [x] **6.3 Create `data/tick/status_reads.py`**
  - [x] Reads: units per shape and day (state, fetch status, current,
        schema); ledger sums per (instrument, session) over current units;
        conditions; the edge with `observed_at`; per-contract lines (ledger
        joined to `tick_definition` for `raw_symbol`, `instrument_class`,
        `expiration_ns`)
  - [x] Raw counts for coverage: one grouped count over `tick_trade`
        bounded by the range's first open and last close, with rows
        assigned to sessions by a join against session arrays passed as
        parameters
  - [x] No read scans `tick_trade` except the coverage count
  - [x] Success: imports
  - [x] Effort: 2
  - Note: the reads are `shape_units`, `ledger_totals`, `conditions`, `dataset_edge`, `contract_lines` (latest definition via `DISTINCT ON`) and `raw_counts` (the one `tick_trade` scan, joining `unnest` session arrays). `Shape(dataset, symbols, stype_in)` filters to the product's own units. The tests are in `test/integration/data/test_tick_status_reads.py`.

- [x] **6.4 Status read tests**
  - [x] Integration on `migrated_tick_db` with seeded units, ledger rows
        and a few `tick_trade` rows:
    - [x] each read returns the seeded values
    - [x] superseded units' ledger rows are excluded from the sums
    - [x] the raw count assigns rows on both sides of 00:00 UTC to the right
      session, and ignores rows outside the range
  - [x] Success: passes
  - [x] Effort: 2
  - [x] Commit: `feat(tick): add tick status reads`

- [x] **6.5 `build_status` and `build_coverage`**
  - [x] In `tick_status.py` (split if it passes ~300 lines): scope per TD9
        (the wanted range if a tier is set, plus sessions touched by current
        tier units), sessions from the production calendar, and frozen
        dataclasses with `to_dict()`. The status result carries
        `complete_basis: "units"` (TD10)
  - [x] Coverage marks a session `mismatch` when the raw count differs from
        the ledger sum, naming the instruments and both counts
  - [x] A production or tick outage raises (→ exit 4). There is no degraded
        output
  - [x] Success: imports
  - [x] Effort: 3
  - Note: split along its concern. `tick_status_build.py` (245 lines) holds `build_status` and the helpers coverage shares (`calendar_sessions`, `session_verdicts`, `shape_of`, `jsonable`); `tick_coverage.py` (115 lines) holds `build_coverage`, `TickCoverage`, `CoverageSession` and `Mismatch`. `session_days.py` now exposes `days_touched(session)` and `utc_midnight(day)`, which `session_days`, `ingest_plan` and the builders all use. `build_status` takes `all_instruments`; spreads (`instrument_class = 'S'`) are hidden and counted in `spreads_hidden`.

- [x] **6.6 Status and coverage integration tests (FR9, FR10)**
  - [x] After ingesting fixture days: status buckets match expectations,
        spreads are hidden in the per-contract list, and `to_dict()`
        round-trips through JSON
  - [x] Coverage reports `ok`. After deleting one `tick_trade` row it
        reports `mismatch` naming the instrument, with the ledger one more
        than raw
  - [x] Success: passes
  - [x] Effort: 2
  - [x] Commit: `feat(tick): build tick status and coverage`
  - Note: the tests are in `test/integration/data/test_tick_status_build.py`. The fixture's extra 2024-09-02 unit brings session 09-02 (it opens Sunday 09-01) into scope, so 5 sessions are held. Day 09-03 belongs to both sessions 09-03 and 09-04, so both count as degraded.

- [x] **6.7 `status` and `coverage` verbs**
  - [x] In `tick_store_cmds.py`: `status [--product] [--all-instruments]
        [--json]` and `coverage --start --end [--product] [--json]`.
        `--start` and `--end` are required. They connect through the plain
        helper from 1.1: no lock, no archive directory
  - [x] Rendering goes in new `cli/commands/tick_status_render.py`,
        matching the LLD's API Contracts layout, including the line
        "complete = every unit ingested; raw-count proof: mt data tick
        coverage"
  - [x] Exits: 0; 1 preflight; 3 when coverage finds a mismatch; 4 for an
        unreachable tick or production database
  - [x] CLI tests: exit codes, `--json` equals `to_dict()` plus
        `exit_code`, and both verbs run with `MT_DATABENTO_API_KEY` and
        `MT_TICK_ARCHIVE_DIR` unset (FR11)
  - [x] Success: passes; each file under ~300 lines
  - [x] Effort: 2
  - [x] Commit: `feat(tick): add mt data tick status and coverage verbs`
  - Note: the verbs are in `tick_store_cmds.py` (233 lines) and are registered in `tick.py` (288 lines); rendering is in `tick_status_render.py`. A new `TickDatabaseUnreachable(TickPreflightError)` in `store_context.py` keeps the writers' exit 1, and `run_mapped(..., storage=...)` maps it to exit 4 for status and coverage only. Coverage `--json` is `{"products": [to_dict()...], "exit_code": n}` because a run can cover several products. The tests are in `test/integration/data/test_tick_status_cli.py` (8 tests), run with no key and no archive directory.

---

## Section 7: Standing checks and documents

- [ ] **7.1 Kalshi contract diff**
  - [ ] Compare `pass_contract.py` with `data/kalshi/collection_pass.py`.
        Every difference must be a declared divergence (224's list plus the
        generic run type)
  - [ ] Write the result as a `Note:` line under this task, as 224's task
        1.3 did. If a difference is not declared, STOP and report it to the
        PM before 7.3
  - [ ] Success: the note lists each difference and its disposition
  - [ ] Effort: 1

- [ ] **7.2 Realtime paths and the API answer**
  - [ ] Confirm the LLD's "Realtime paths" points still hold against the
        code as built (bounded unit transaction, rank-based supersession,
        per-unit ledger). Note any change
  - [ ] Confirm `build_status`/`build_coverage` return `to_dict()`
        dataclasses usable by 230 unchanged (TD11)
  - [ ] Write both results as a `Note:` line under this task. If a
        realtime point no longer holds, STOP and report it to the PM
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
        Check both throughput targets from TD2 against fixed ceilings:
    - every unit ≤ 120 s (the 5.4 bound: 1/720 of the day it covers)
    - the whole run ≤ 2 h. The run covers about 3.5 months of sessions, so
      this puts one month well inside a working session

    A miss on either fails the walkthrough
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
  - [ ] Step 8: name the FR6 test (part 1, 4.4 case 1) and paste its passing
        output into the LLD walkthrough
  - [ ] Step 9: drop `mt_scratch_tick_225` and confirm a `pg_database`
        count of 0; the archive stays
  - [ ] Success: recorded; scratch database gone
  - [ ] Effort: 1
  - [ ] Commit: `docs: record slice 225 verification walkthrough`
