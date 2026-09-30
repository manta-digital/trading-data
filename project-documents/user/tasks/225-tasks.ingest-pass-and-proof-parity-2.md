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
