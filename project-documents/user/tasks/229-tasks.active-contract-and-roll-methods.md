---
docType: tasks
slice: active-contract-and-roll-methods
project: trading-data
lld: user/slices/229-slice.active-contract-and-roll-methods.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [220, 221, 224, 225, 226]
projectState: >
  Slices 220-228 are merged. trading_tick holds both adopted ES jobs, ingested
  and checked (trades 2024-08-30 → 09-29, tbbo 2024-11-01 → 12-31), each with a
  quarterly roll. The ingest ledger carries per-(unit, instrument, session)
  volume. Nothing can yet answer "which ES contract is active". Slice design
  committed and reviewed (round 2 CONCERNS, gate passed).
dateCreated: 20261012
dateUpdated: 20261012
status: not_started
---

# Tasks: Active Contract and Roll Methods

## Context Summary

- Working on slice 229. It adds roll rules (`c` calendar, `v` volume, a rank,
  and a session offset for calendar), a pure resolver, a read-time schedule,
  a new verb `mt data tick active`, and roll lines in `mt data tick status`.
  No stored state, no schema change, nothing bought.
- The design (`user/slices/229-slice.active-contract-and-roll-methods.md`)
  holds every decision. Read TD1-TD10, API Contracts and Success Criteria
  before starting. Tasks cite them rather than repeat them.
- **Paths differ from the design text.** Tests live under `test/`, not
  `tests/`: unit tests in `test/unit/data/tick/`, integration tests in
  `test/integration/data/`, fixtures in `test/fixtures/tick/roll/`. Test
  helpers live in `test/tick_support/`. The data-correctness contract is
  `user/reference/data-correctness-architecture.md`.
- **Two placements the design leaves implicit, decided here:**
  - `DEFAULT_ROLL_RULE` and `STATUS_ROLL_RULES` are `RollRule` instances, so
    they live in `roll_rules.py` beside the class. `constants.py` cannot hold
    them without importing `roll_rules`, which itself reads constants (an
    import cycle). `RollMethod` and every scalar (lag, cap, timeout, classes)
    stay in `constants.py`. "Defined once" still holds.
  - `calendar_sessions` also lives in `tick_status_build.py` today, and the
    roll path needs it. It moves to `status_reads.py` with `session_verdicts`
    and `shape_of` (task 4.1), so no roll module imports `tick_status_build`.
- **Module layout** (each file ≤ ~300 lines): `data/tick/roll_rules.py`,
  `roll_resolve.py`, `roll_inputs.py`, `active_contract.py`, and
  `cli/commands/tick_active_render.py`. If `roll_resolve.py` passes ~300
  lines, move its result dataclasses to `roll_types.py` (still pure).
  `constants.py` is at 300 lines already; the few new scalars are allowed to
  push it slightly over, since it is the single home for comparison values.
- **Test environment.** Export `MT_TIMESCALE_TEST_URL` from `.env` with quotes
  stripped, in the same shell call. Run with
  `uv run python scripts/run_tests.py unit|integration -- <paths> -q`. Run
  tiers separately. Known pre-existing integration failures are not
  regressions: re-run in isolation before investigating.
- **Production safety.** `trading_tick` and the production calendar are only
  read (`SELECT`, plus Databento's free `symbology.resolve`). Tests never read
  `MT_TICK_DB_URL` or call Databento.
- Delivers to 230 (`get --product`), 231 (API) and 232 (GC). Next slice: 230.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 1 - Preflight facts

- [ ] **1.1 Confirm the code facts the design relies on** (effort 1)
  - [ ] `TradingCalendar(calendar_id, url)` accepts a conninfo string with
        `connect_timeout`, keepalives and `options` (what `roll_conninfo`
        returns). Read `data/base/trading_calendar.py` for how the pool is
        built and whether `options` reach each pooled connection.
  - [ ] `calendar_sessions` in `tick_status_build.py` maps
        `OperationalError` (including the pool's `PoolTimeout`) to
        `TickCalendarError` and out-of-range to `TickPreflightError`.
  - [ ] Every caller of `session_verdicts`, `shape_of` and `calendar_sessions`
        (today: `tick_status_build.py`, `tick_coverage.py`, tests).
  - [ ] `ingest_plan._definitions` uses `activation_ns ≤ close` and
        `expiration_ns ≥ open` (the listed-in-session predicate TD3 relies on).
  - [ ] Outright `instrument_class` value in `trading_tick` is `"F"`
        (read-only `SELECT DISTINCT instrument_class FROM tick_definition`).
  - [ ] Which test fixture builds a scratch tick database with definitions,
        units and ledger rows (`test/tick_support/database.py`, `seed.py`,
        `rows.py`), and how `test_tick_status_build.py` uses it.
  - [ ] Write a `Findings (1.1)` block under this task. If `TradingCalendar`
        cannot carry the conninfo bounds, stop and ask the Project Manager
        (TD9 depends on it).
  - [ ] Success: findings block present; no code changes.

---

## Section 2 - Rules and constants

- [ ] **2.1 Add roll constants to `data/tick/constants.py`** (effort 1)
  - [ ] `RollMethod(StrEnum)`: `CALENDAR = "c"`, `VOLUME = "v"` (TD1). Leave
        `n` unassigned.
  - [ ] `OUTRIGHT_CLASS = "F"` and `SPREAD_CLASS = "S"` (moved from
        `tick_status_build.py`; update its import, keep its comment).
  - [ ] `VOLUME_RANK_LAG_SESSIONS = 2`. Its comment must state: why 2 (it
        matches Databento's `.v` switch on both held rolls; CME publishes
        session D's statistics during D + 1), the fixture test that pins it
        (task 4.3's test name), and that a change is a provider convention
        change revisited whole, never per roll (TD3).
  - [ ] `TICK_ROLL_MAX_SESSIONS = 1_300` (TD10) and
        `TICK_ROLL_STATEMENT_TIMEOUT_SECONDS = 30` (TD9), each with a one-line
        reason.
  - [ ] Success: `test_constants.py` and `test_tick_status_build.py` pass;
        no other definition of `"S"` as a class remains in `src`.

- [ ] **2.2 Implement `data/tick/roll_rules.py`** (effort 2)
  - [ ] Frozen dataclass `RollRule(method, rank=0, offset_sessions=0)`.
        Validation in `__post_init__` raises `ValueError` naming the field,
        following `TickUniverseEntry`: negative rank, negative offset, offset
        on `VOLUME`.
  - [ ] `RollRule.parse(text)`: grammar `{method}.{rank}[+{offset}s]`,
        lenient on case and surrounding whitespace. Named refusals (TD1):
        unknown method (`unknown roll method 'n'; known: c, v`, listing from
        `RollMethod`), negative or non-integer rank, suffix on `v`, `+0s`,
        trailing text.
  - [ ] `__str__` renders the canonical form (no suffix for offset 0);
        `symbol(product)` returns `f"{product}.{rule}"`.
  - [ ] `DEFAULT_ROLL_RULE = RollRule(RollMethod.VOLUME, 0)` and
        `STATUS_ROLL_RULES = (DEFAULT_ROLL_RULE, RollRule(RollMethod.CALENDAR))`
        defined here (see Context Summary).
  - [ ] Success: module has no I/O; ruff, mypy and pyright clean.

- [ ] **2.3 Test `roll_rules`** (effort 2)
  - [ ] `test/unit/data/tick/test_roll_rules.py`: round trip
        `parse(str(rule)) == rule` for every method × rank 0-3 × offset 0-10
        that is valid (offset only on `c`).
  - [ ] Canonicalisation: `" V.0 "` → `v.0`; `c.0+5S` → `c.0+5s`.
  - [ ] One test per refusal in 2.2, asserting the message text.
  - [ ] `symbol("ES")` gives `ES.v.0` and `ES.c.0+5s`.
  - [ ] Success: tests pass. Commit: `feat: add roll rule grammar and roll
        constants`.

---

## Section 3 - Pure resolver

- [ ] **3.1 Define resolver input and result types** (effort 2)
  - [ ] In `roll_resolve.py` (or `roll_types.py`, see Context Summary): input
        records for a contract definition (`instrument_id`, `raw_symbol`,
        `activation_ns`, `expiration_ns`, `unit_id`) and a session volume row
        (`session_date`, `instrument_id`, `volume`, `unit_id`).
  - [ ] Result types exactly as API Contracts: `ActiveContract`,
        `Unresolved`, `Segment` (first session, last session, contract),
        `RollSchedule` with `segments()`, and `ActiveLine` (latest complete
        session L and its answer or reason, `next_roll`, `known_through`,
        `roll_no_later_than`, `error`).
  - [ ] `RollInputError` for broken invariants (TD9): message names the
        contract and the session.
  - [ ] Success: types import cleanly; no I/O imports in the module.

- [ ] **3.2 Implement the calendar rule (TD2)** (effort 3)
  - [ ] `resolve_sessions(rule, sessions, contracts, volumes, complete)` for
        `CALENDAR`: candidates are outright definitions with
        `activation_ns ≤ S.open`; E(c) is the last session opening at or
        before `expiration_ns`; eligible when
        `index(S) ≤ index(E(c)) − offset_sessions`; order by `expiration_ns`;
        rank r takes the r-th.
  - [ ] An E(c) beyond the sessions passed in raises `RollInputError` naming
        the contract and `mt data extend` (never "not expiring"). The caller
        (task 5.4) passes sessions through each candidate's expiration.
  - [ ] Rank deeper than eligible candidates → `Unresolved` with the rank and
        candidate count in the reason.
  - [ ] `ActiveContract` fields: `basis_session = S`, `basis_unit_ids = ()`,
        `definition_unit_id` from the chosen row, `definitions_as_of` passed
        in.
  - [ ] Success: ruff, mypy, pyright clean.

- [ ] **3.3 Test the calendar rule** (effort 2)
  - [ ] `test/unit/data/tick/test_roll_resolve_calendar.py`, synthetic
        sessions and contracts.
  - [ ] Offsets 0, 1 and 5; `c.0+5s` rolls exactly five sessions earlier than
        `c.0` (FR5).
  - [ ] Expired contract drops out of rank 1; expiry inside a session keeps
        the contract front through that session (TD2's ESU4 example).
  - [ ] Deep rank (`c.5` with four candidates) → `Unresolved` naming 5 and 4.
  - [ ] Expiration beyond the sessions → `RollInputError`.
  - [ ] Success: tests pass with no database.

- [ ] **3.4 Implement the volume rule (TD3)** (effort 4)
  - [ ] For session S with lag session L = S − `VOLUME_RANK_LAG_SESSIONS`
        (by session index): if L is outside the sessions passed in or not in
        `complete`, return `Unresolved` with reason naming L and its status,
        e.g. `volume inputs: session 2024-11-01 is missing`. Never fall back to
        partial volume, calendar, or the previous answer.
  - [ ] Ranked set: eligible at S (TD2, offset 0) and listed in L
        (`activation_ns ≤ L.close` and `expiration_ns ≥ L.open`). A contract
        listed after L opened is excluded, not ranked as 0.
  - [ ] A ranked contract with no volume row for a complete L raises
        `RollInputError` naming the contract and L (I13).
  - [ ] Order by volume descending, ties by earlier expiration; rank r takes
        the r-th; deeper rank → `Unresolved` as in 3.2.
  - [ ] `basis_session = L`; `basis_unit_ids` = sorted distinct unit ids of
        L's rows that were read.
  - [ ] Success: ruff, mypy, pyright clean.

- [ ] **3.5 Test the volume rule** (effort 3)
  - [ ] `test/unit/data/tick/test_roll_resolve_volume.py`, synthetic inputs.
  - [ ] Volume crosses at L → the switch shows on L + 2 (lag applied).
  - [ ] Tie broken by earlier expiration.
  - [ ] Contract activated between L and S: excluded for S, ranked from the
        first session whose lag session lists it; no error (FR6a).
  - [ ] Missing row for a contract listed in a complete L raises, naming it
        (FR6a).
  - [ ] Incomplete L → `Unresolved` naming L and its status; first two
        sessions of a range are unresolved (FR4).
  - [ ] `v.1` while the old front is still listed (walkthrough step 4 shape).
  - [ ] `basis_unit_ids` lists L's units.
  - [ ] Success: tests pass with no database.

- [ ] **3.6 Implement segments and the next-roll forecast** (effort 3)
  - [ ] `RollSchedule.segments()`: consecutive same-`instrument_id` runs;
        unresolved sessions break segments and are not part of any segment.
  - [ ] `next_roll` for an `ActiveLine` (TD7, API Contracts):
        - calendar: exact first session after L whose answer differs, with
          the incoming contract; `known_through` = last session passed in;
          `roll_no_later_than = None`;
        - volume: resolve L + 1 and L + 2 only (both decided by held volume);
          if either differs from L's answer, set `next_roll` to it; otherwise
          `next_roll = None`; `known_through = L + 2`;
          `roll_no_later_than` = first session after the active contract's
          expiration session.
  - [ ] Success: ruff, mypy, pyright clean; module ≤ ~300 lines (split types
        per Context Summary if not).

- [ ] **3.7 Test segments and next roll** (effort 2)
  - [ ] `test/unit/data/tick/test_roll_next.py`: segments over a run with an
        unresolved gap; volume roll on L + 1, on L + 2, and beyond L + 2 (bound
        only, `next_roll` None); calendar next roll exact.
  - [ ] Success: tests pass. Commit: `feat: add pure roll resolver`.

---

## Section 4 - Recorded provider fixtures and the equivalence test

- [ ] **4.1 Write `scripts/record_229_roll_fixture.py`** (effort 3)
  - [ ] Reads `MT_TICK_DB_URL` and `MT_TIMESCALE_DB_URL` from the environment
        (fails naming the variable if unset). Issues only `SELECT`s.
  - [ ] Writes JSON fixtures to `test/fixtures/tick/roll/`:
        `es_definitions.json` (ES outright definitions),
        `es_volumes.json` (outright volume per session and unit, both held
        ranges, current units only, plus the verdict per session as
        `COMPLETE` or its status), `es_calendar.json` (CME_EQUITY sessions
        2024-08-28 → 2025-03-31 with open/close UTC, enough to cover ESH5's
        expiry), `es_databento.json` (intervals for `ES.c.0`, `ES.c.1`,
        `ES.v.0`, `ES.v.1` over 2024-08-30 → 2025-01-02 from
        `ITickMetadataProvider.resolve_symbols`).
  - [ ] Each file records its source and the UTC time it was read.
  - [ ] Success: `--help` works; ruff and mypy clean (`MYPYPATH=scripts`).

- [ ] **4.2 Run the fixture script on manta9000 and record it** (effort 1)
  - [ ] Run it once against `trading_tick` and the production calendar.
  - [ ] Write `user/notes/229-roll-fixture-record.md` (with frontmatter): the
        command, row counts per file, and the Databento switch dates seen
        (expected `ES.v.0` 2024-09-18 and 2024-12-18; `ES.c.0` 2024-09-22).
  - [ ] Success: four fixture files exist; the note matches them.

- [ ] **4.3 Provider-equivalence test** (effort 3)
  - [ ] A loader in `test/tick_support/roll_fixture.py` turns the JSON into
        resolver inputs.
  - [ ] `test/unit/data/tick/test_roll_provider_equivalence.py`
        (name it in the lag constant's comment, task 2.1): for `c.0`, `c.1`,
        `v.0`, `v.1`, every resolved session in the held ranges equals the
        contract Databento's interval maps on that session date (TD4).
  - [ ] Unresolved sessions asserted exactly: those whose lag session is not
        complete in the recorded verdicts (for tbbo, 11-04 and 11-05; the gap
        between ranges).
  - [ ] FR2 and FR3 dates asserted explicitly (ESU4→ESZ4 and ESZ4→ESH5 under
        both rules).
  - [ ] Success: tests pass with no database or network. Commit:
        `test: add recorded roll fixtures and provider equivalence test`.

---

## Section 5 - Reads, connections and the schedule

- [ ] **5.1 Move shared status reads into `status_reads.py`** (effort 2)
  - [ ] Rename `_SHAPE_WHERE` to `SHAPE_WHERE`; update its uses.
  - [ ] Move `shape_of`, `session_verdicts` and `calendar_sessions` from
        `tick_status_build.py` to `status_reads.py` unchanged. Update imports
        in `tick_status_build.py`, `tick_coverage.py` and tests (findings 1.1).
  - [ ] Run unit and integration tests for status and coverage
        (`test_tick_status*.py`, coverage tests) before committing.
  - [ ] Success: those tests pass unchanged. Commit:
        `refactor: move shared tick status reads into status_reads`.

- [ ] **5.2 Implement `data/tick/roll_inputs.py`** (effort 2)
  - [ ] `outright_definitions(conn, product)`: `asset = product` and
        `instrument_class = OUTRIGHT_CLASS`. No rows → `RollInputError`
        naming the product (Special Considerations), never empty.
  - [ ] `session_volumes(conn, shape, first, last)`: ledger rows of outrights
        over current units of the shape (`SHAPE_WHERE`), returning resolver
        volume rows with `unit_id`.
  - [ ] `definitions_as_of`: latest ingested definition unit day.
  - [ ] Success: ruff, mypy, pyright clean.

- [ ] **5.3 Test `roll_inputs` on a scratch tick database** (effort 3)
  - [ ] `test/integration/data/test_tick_roll_inputs.py`: current units only
        (a superseded unit's rows are not returned); spreads not returned;
        unknown product raises.
  - [ ] Success: tests pass.

- [ ] **5.4 Implement `roll_conninfo` and `build_schedule`** (effort 4)
  - [ ] In `active_contract.py`: `roll_conninfo(url)` via
        `psycopg.conninfo.make_conninfo` adding `connect_timeout`, keepalives
        (`TICK_DB_KEEPALIVES_*`), and
        `options=-c statement_timeout=<…> -c default_transaction_read_only=on`
        (TD9).
  - [ ] `build_schedule(tick_url, calendar_url, entry, rule, start, end)`:
        `RollRangeTooLong` (names `TICK_ROLL_MAX_SESSIONS`, asks for a narrower
        range) before any tick read when `[start, end)` spans more sessions
        than the cap; calendar via `calendar_sessions` with
        `roll_conninfo(calendar_url)` in `asyncio.to_thread`, covering the lag
        sessions before `start` and each candidate's expiration session.
  - [ ] One owned tick connection from `roll_conninfo(tick_url)`, one
        `REPEATABLE READ READ ONLY` transaction holding definitions, volumes
        and `session_verdicts`. Close both connections before returning.
  - [ ] Resolution in `asyncio.to_thread`. No parameter accepts a connection,
        transaction or verdicts.
  - [ ] Success: ruff, mypy, pyright clean.

- [ ] **5.5 Implement `latest_active`** (effort 3)
  - [ ] `latest_active(tick_url, calendar_url, entry, rules)` → one
        `ActiveLine` per rule, same ownership and one snapshot for all rules.
  - [ ] Finds the latest complete session L, resolves L and its forecast
        (task 3.6). A `RollInputError` or `TickPreflightError` for one rule
        sets that line's `error` and leaves other lines intact (TD9).
  - [ ] Success: ruff, mypy, pyright clean; `active_contract.py` ≤ ~300 lines.

- [ ] **5.6 Test the schedule, snapshot and failure modes** (effort 4)
  - [ ] `test/integration/data/test_tick_active_contract.py` on a scratch tick
        database: a seeded roll resolves under `v.0` and `c.0`; missing ledger
        row raises `RollInputError`; `basis_unit_ids` changes after a
        supersession (State Management).
  - [ ] Snapshot: a unit committed during a roll read is wholly visible or
        not at all (FR8b).
  - [ ] Statement timeout: hold a lock on a table the roll read needs and
        assert `QueryCanceled` (`OperationalError`) within the timeout
        (patch the constant low for the test).
  - [ ] `roll_conninfo` output carries the timeout and keepalives, and the
        calendar is opened with it (FR8b).
  - [ ] Range over the cap raises before any tick connection opens.
  - [ ] Success: tests pass.

- [ ] **5.7 Import-direction guard** (effort 1)
  - [ ] Unit test (AST walk, like `test_import_boundary.py`) asserting no
        `roll_*` module and not `active_contract` imports `tick_status_build`,
        and that `roll_resolve` imports nothing that does I/O.
  - [ ] Success: test passes. Commit:
        `feat: add roll inputs and read-time schedule`.

---

## Section 6 - Surfaces

- [ ] **6.1 Renderer `cli/commands/tick_active_render.py`** (effort 3)
  - [ ] Text forms from TD7: the latest-session block (contract, rule,
        `(default)` marker, expiration UTC, session, inputs line for volume,
        next-roll line per method) and the ranged segments form (exclusive
        end, as `coverage`), with unresolved sessions and reasons listed.
  - [ ] JSON with exactly TD7's fields (`product`, `rule`, `is_default`,
        `answers`/`segments`, `unresolved`, `next_roll`, `exit_code`), dates
        ISO, expiration ISO UTC.
  - [ ] Success: ruff, mypy, pyright clean.

- [ ] **6.2 Register `mt data tick active`** (effort 3)
  - [ ] In `tick_store_cmds.py`: options `--product`, `--rule` (parsed by
        `RollRule.parse`, default every rule in `STATUS_ROLL_RULES`),
        `--start`/`--end` (both or neither), `--json`. No API key, archive or
        lock. URLs via `store_context.database_url` (unset → exit 1).
  - [ ] Exit codes from `tick_exit.py` (TD7): 0 all resolved; 3 any
        unresolved; 1 bad rule, unknown product, over the cap, broken
        invariant, calendar out of range; 4 tick DB or calendar unreachable,
        timeout, or dropped connection.
  - [ ] Widen the `EXIT_PARTIAL` comment in `tick_exit.py` to cover
        unresolved `active` sessions and a failed `status` roll line; values
        unchanged.
  - [ ] Success: `uv run mt data tick active --help` lists the options.

- [ ] **6.3 Test `active`** (effort 3)
  - [ ] Unit tests on the renderer (text and JSON shape) and integration CLI
        tests in `test/integration/data/test_tick_active_cli.py`: exit 0 on a
        resolved range, 3 with unresolved sessions (output still printed), 1
        for `n.0`, `v.0+2s` and an over-cap range, 4 for an unreachable tick
        database and for the lock-held timeout.
  - [ ] Success: tests pass. Commit: `feat: add mt data tick active`.

- [ ] **6.4 Add roll lines to `status`** (effort 3)
  - [ ] `ProductStatus.rolls: tuple[ActiveLine, ...]`, filled by
        `latest_active` with `STATUS_ROLL_RULES` after status's own reads.
        The tick and calendar URLs are passed in; `status`'s own connection
        is not reused or changed.
  - [ ] `tick_status_render.py` prints roll lines under the contract lines,
        in `active`'s latest-session form (reuse its line formatter, do not
        copy it); an `error:` line for a rule with `error` set.
  - [ ] JSON `rolls` per product with TD7 walkthrough step 7's fields.
  - [ ] Exit 3 when any roll line has `error`; otherwise exit as today.
  - [ ] Success: ruff, mypy, pyright clean.

- [ ] **6.5 Test `status` roll lines and regressions** (effort 3)
  - [ ] Extend `test_tick_status_cli.py` / `test_tick_status_build.py`:
        `v.0 (default)` and `c.0` lines shown (FR7); a broken invariant in one
        rule prints its `error:` line, keeps the others and the buckets, exits
        3 (FR8a); lock-held timeout → `error:` line, exit 3 (FR8b).
  - [ ] Regressions (FR8c): with no roll error, every existing text line and
        JSON field is unchanged plus `rolls`; exit 0; exit 4 when the tick
        database is unreachable; `coverage` output unchanged.
  - [ ] FR9 check: `grep` finds no rule spelled as a bare `"v"`/`"c"` outside
        `constants.py`, `roll_rules.py` and tests.
  - [ ] Success: tests pass. Commit: `feat: show active contract in tick
        status`.

---

## Section 7 - Walkthrough, docs and closing checks

- [ ] **7.1 Walkthrough and timing on `trading_tick`** (effort 2)
  - [ ] Run the design's Verification Walkthrough steps 1-8 on manta9000 and
        compare each with its expected output.
  - [ ] Re-run TD10's measurement: time a full held-range schedule
        (2024-08-30 → 2025-01-02, `v.0`); it must finish well inside 1 s.
  - [ ] Record outputs, exit codes and the timing in
        `user/notes/229-walkthrough.md` (with frontmatter). Any mismatch with
        an expected value: stop and report to the Project Manager.
  - [ ] Success: note committed: `docs: record slice 229 walkthrough`.

- [ ] **7.2 Data-correctness contract rows** (effort 1)
  - [ ] In `user/reference/data-correctness-architecture.md`: I14 row gets
        229's delivery (rules, default `v.0`, volume next-roll narrowing,
        unresolved sessions reported); I10 row gets the `active` verb.
  - [ ] Success: rows updated.

- [ ] **7.3 Gating check: architecture and slice plan match what shipped**
      (effort 1)
  - [ ] Compare the Revision Log entry 2026-10-09 in
        `user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md`
        and the "superseded by 229" note in
        `user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md`
        against shipped behaviour: volume next roll, offset in sessions, lag
        2, default `v.0`, open interest deferred. Correct either if
        implementation changed anything.
  - [ ] Success: both match, or are corrected in this task.

- [ ] **7.4 Final validation** (effort 2)
  - [ ] Kalshi pass-contract diff test passes
        (`test/unit/data/tick/test_pass_contract_parity.py`).
  - [ ] ruff (check, format scoped to touched files), mypy (one invocation:
        src, kalshi paths and tests, `MYPYPATH=scripts`) and pyright clean on
        touched files.
  - [ ] Unit tier and integration tier pass, except known pre-existing
        failures (re-run each in isolation).
  - [ ] Touched source files ≤ ~300 lines (`wc -l`), `constants.py` excepted
        per Context Summary.
  - [ ] Success: all green. Commit: `docs: update data-correctness contract
        for slice 229`.
