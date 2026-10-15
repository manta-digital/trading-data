---
docType: tasks
slice: tick-pass-production-wiring
project: trading-data
lld: user/slices/234-slice.tick-pass-production-wiring.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [225-slice.ingest-pass-and-proof-parity, 230-slice.operator-surface-full-parity, 231-slice.api-surface-coverage-futures-tick, 233-slice.cross-source-acquisition-arbitration]
projectState: >
  Tasks 1–4 implemented on branch 234-slice.tick-pass-production-wiring
  (PassKind.TICK, manual schedule, migration 060, `mt data tick run`, ingest
  heartbeat, tick health rows); Tasks 5–10 in part 2 remain.
dateCreated: 20261014
dateUpdated: 20261015
status: complete
part: 1
---

# Tasks: Tick Pass Production Wiring (part 1 of 2)

## Context Summary

- Working on slice 234. It puts the tick passes into the slice-916 production
  form: one oneshot unit, an `mt-run tick` kind, a `PassKind.TICK` run record,
  the tick settings in `/etc/manta-trading.env`, and a tick section in the
  health check. One cutover script installs and verifies all of it.
- **Cadence is manual only.** No timer and no cadence constant. The "no timer"
  answer is `FiringSchedule.manual()` (LLD TD1).
- It delivers:
  - `PassKind.TICK`, `FiringSchedule.manual()`, migration `060_pass_kind_tick`;
  - the verb `mt data tick run` (acquisition pass, then ingest pass, one
    `pass_runs` row) with an outcome-severity fold and an ingest heartbeat;
  - health rows `tick database` (informational) and `tick retention`;
  - `mt-tick-pass.service`, `tick` in `mt-run` `KINDS`, `deploy/add-tick-env.sh`;
  - `scripts/cutover_234_tick_wiring.py`, runbook 100 rows, the regenerated
    `openapi.json`, and the standing 220 obligations.
- Read LLD decisions TD1–TD6 before starting. Every rule referenced below is
  stated there; tasks cite them rather than restate them.
- **The ingest heartbeat has no callback to reuse.** The LLD says the ingest
  pass "accepts" a thread-safe per-batch callback, handed over by 225. In the
  code it does not: `IngestInputs` (`data/tick/ingest_pass.py`) has no such
  field and `_copy` in `ingest_worker.py` reports nothing per batch. Task 3.3
  adds both ends. The LLD's Excluded list bars changes to "the tick pass
  logic"; the callback is an optional, additive reporting hook that LLD Data
  Flow step 2 requires, so Task 3.3 may not change what is loaded, checked or
  committed. If the work shows it cannot be done without such a change, stop
  and raise it then.
- Constraints that bind every task:
  - Nothing is bought. No test or script may call the provider's purchase path.
  - Destructive SQL only against a database the process created (CLAUDE.md).
    Tests never read the production URL variable; export `MT_TIMESCALE_TEST_URL`
    from `.env` as the project memory describes.
  - `MT_TICK_MAINTENANCE_URL` never enters the service environment.
  - No timer file, `OnCalendar`, or cadence constant for tick.
  - Ruff-format only the files a task touches, and check `git diff` for swept
    pre-existing lines before each commit.
- Known pre-existing failures to ignore (verified at the slice base): the
  `test_migration_051_052` chain-end and cagg-budget failures,
  `test_policy_advances_head` (2 tests, no TimescaleDB scheduler on the test
  cluster), and the integration-tier flakes (priority1 list mismatch, "tuple
  concurrently deleted", setup errors under concurrent DB churn). Re-run in
  isolation before investigating any failure in those areas.
- Work happens on branch `234-slice.tick-pass-production-wiring`, created from
  the target by the implementer before Task 1. No task creates, switches or
  merges branches.
- Next slice: none planned in 220 after this one. Open interest (Future Work
  item 2) stays deferred; 234 builds nothing for it.

Effort scale: 1 (trivial) to 5 (hardest).

---

## 1. Pass kind and manual schedule (LLD TD1) — effort 2

- [x] **1.1 Add `PassKind.TICK`**
  - [x] In `src/manta_trading/data/acquisition/pass_runs.py`, add `TICK = "tick"`
    to `PassKind` with a one-line docstring in the style of its siblings.
  - [x] Grep the repo for every `match`/`if` on `PassKind` and for every
    `for kind in PassKind` loop (`src`, `scripts`, `test`). List each in the
    commit message body and handle tick explicitly in each (Tasks 1.2–1.4 cover
    the schedule callers). No site may fall through to a default for tick.
  - [x] Success: `PassKind("tick")` resolves; mypy is clean on the touched files.

- [x] **1.2 Add `FiringSchedule.manual()` and the `schedule_for` branch**
  - [x] In `src/manta_trading/firing_schedule.py`, add a classmethod `manual()`
    returning a schedule with no times, and an `is_manual` property that is true
    only for it (not for any other empty schedule).
  - [x] `describe()` returns `manual (no timer)` for a manual schedule.
  - [x] `schedule_for(PassKind.TICK, ...)` returns `FiringSchedule.manual()`.
    The unreachable-branch `AssertionError` at the end stays.
  - [x] `next_firing_at` keeps raising on a schedule with no times. Do not make
    it return `None` or a sentinel.
  - [x] Success: `schedule_for(PassKind.TICK).is_manual` is true and every other
    kind's is false.

- [x] **1.3 Make every `schedule_for` caller handle a manual schedule**
  - [x] `_pass_line` in `src/manta_trading/cli/commands/overview.py`: check
    `is_manual` first and produce a pass line whose next firing reads `manual`.
    Remove the `RuntimeError` catch that exists to absorb an empty schedule; if
    other behaviour depends on it, keep the catch only for that and say why in a
    comment.
  - [x] `render_next_firings` in `src/manta_trading/cli/rendering/overview.py`:
    print `tick manual` for a manual kind instead of a time.
  - [x] The API model `_pass_line` in `api_server/models/operations.py`: confirm
    the next-firing field can carry "manual" (or its absence) without a schema
    break; if it cannot, stop and raise it with the Project Manager.
  - [x] Grep for any other caller of `schedule_for` or `next_firing_at` and
    handle each.
  - [x] Success: `mt data overview` with a tick row renders without exception.

- [x] **1.4 Test: schedule and callers**
  - [x] In `test/unit/test_firing_schedule.py`, update the three tests that
    iterate `PassKind` so a firing is asserted only for kinds that are not
    manual.
  - [x] Add one test (same file, no new guard module) asserting that
    `schedule_for(kind).is_manual` holds if and only if
    `deploy/systemd/mt-{kind}-pass.timer` is absent, for every pass kind that
    has a `mt-{kind}-pass.service` file. Tick has its service after Task 5.1 (part 2);
    until then the test covers the kinds that already have units, and Task 5.3
    (part 2) confirms tick is included.
  - [x] Add tests: `describe()` text; `next_firing_at` still raises on a manual
    schedule; overview `_pass_line` and `render_next_firings` for tick.
  - [x] Add a test that the API overview response (find the existing test with
    `grep -rn "api/v1/overview" test/`) lists a `tick` pass whose next firing
    carries the manual value chosen in Task 1.3.
  - [x] Success: `uv run pytest test/unit/test_firing_schedule.py` and the
    overview unit and API tests pass; ruff and mypy clean on touched files.
    Tests that seed a `pass_runs` row for every kind against the test cluster
    wait for Task 2.2, because the cluster rejects `tick` until migration 060
    applies.

(No commit here: `PassKind.TICK` without migration 060 breaks every-kind
seeding tests, so Tasks 1 and 2 commit together at 2.4.)

---

## 2. Migration 060 and the API enum (LLD Database/Storage, API Contracts) — effort 2

- [x] **2.1 Write migration `060_pass_kind_tick`**
  - [x] In `src/manta_trading/market/schema/migrations/minute.py`, append the
    migration after `059_seed_cme_metals_calendar`. Follow the `kalshi_007`
    pattern: `DROP CONSTRAINT IF EXISTS pass_runs_pass_check`, then
    `ADD CONSTRAINT … CHECK ({_pass_kind_check_sql()})`.
  - [x] Check the migration registry for any position or count assertion that a
    new entry affects (migration-order notes near `055`).
  - [x] Success: the migration is listed by `mt data migrate status --track
    minute` as pending against a database at 059.

- [x] **2.2 Test: migration 060**
  - [x] New `test/integration/test_migration_060_pass_kind_tick.py`, modelled on
    `test_migration_055_pass_runs.py`. Create a throwaway database at `055`,
    apply through `060`, and assert an insert with pass `tick` is accepted
    while an unknown pass is still rejected.
  - [x] Add the 060 id to any chain-end assertion that lists the expected last
    migration, except the known stale `chain_ends_at_052` test.
  - [x] Run the tests that seed a `pass_runs` row for every kind
    (`test_922_overview_nfr.py` and any found by grep for `for kind in
    PassKind` under `test/integration`) now that 060 applies.
  - [x] Success: the new test passes; no new failure in
    `test_schema_migrations.py` or the every-kind seeding tests.

- [x] **2.3 Regenerate the API docs**
  - [x] Run `scripts/dump_openapi.py` to rewrite `docs/api/openapi.json`; the
    `pass` enum gains `tick`. Run `scripts/check_api_docs.py`.
  - [x] Success: the diff of `openapi.json` is the enum addition only (plus any
    next-firing description change from Task 1.3); the schema test passes.

- [x] **2.4 Commit (Tasks 1 and 2 together)** —
  `feat: add PassKind.TICK, manual schedule and migration 060`

---

## 3. The `mt data tick run` verb (LLD TD2, Data Flow) — effort 4

- [x] **3.1 Outcome severity fold and run-outcome map**
  - [x] In `src/manta_trading/cli/commands/tick_exit.py`, next to
    `EXIT_BY_OUTCOME`, define once the severity order
    (`STORAGE_ABORT` > `PROVIDER_ABORT` > `REFUSED` > `IN_FLIGHT` > `PARTIAL` >
    `OK`) and a function folding two `TickOutcome`s to the worse.
  - [x] Define `PASS_RUN_OUTCOME_BY_TICK_OUTCOME` (table in LLD Data Flow
    step 4) with an exhaustiveness assert modelled on
    `PASS_RUN_OUTCOME_BY_SYNC_OUTCOME` in `cli/commands/kalshi.py`. If
    `tick_exit.py` would pass ~300 lines, put the map in a sibling module.
  - [x] Add an exhaustiveness assert that the severity order lists every
    `TickOutcome`.
  - [x] Success: importing the module fails loudly if a `TickOutcome` member is
    added to neither structure.

- [x] **3.2 Test: fold and map**
  - [x] Unit tests: the fold is symmetric; every ordered pair resolves to the
    higher-severity member; `OK` is the identity; the map covers every member
    with the LLD's values.
  - [x] A test asserting the severity order agrees with `EXIT_BY_OUTCOME`
    severity as the LLD describes: derive the expectation from the exit
    vocabulary (storage and provider exits ranked above refused, in-flight,
    partial), not from a copy of the same list.
  - [x] Success: tests pass.
  - [x] Commit — `feat: add tick outcome severity fold and pass-run outcome map`

- [x] **3.3 Ingest per-batch progress callback**
  - [x] Add an optional thread-safe callback field to `IngestInputs`
    (`data/tick/ingest_pass.py`) that receives the row count of each `COPY`
    batch a worker commits. Default is "none"; the existing `mt data tick
    ingest` path must behave exactly as today.
  - [x] Thread it through to `_copy` in `data/tick/ingest_worker.py` and call it
    once per batch with the batch's row count. Only a count crosses the worker
    boundary (LLD Data Flow step 2).
  - [x] Workers are threads: `ingest_pass.py` runs `ingest_unit` through
    `asyncio.to_thread` (confirmed at design time). The callback is passed
    straight through; the reporter it feeds (3.5) is the only shared state and
    is thread-safe. Add no other concurrency primitive.
  - [x] Counts are rows `COPY`'d inside the unit's open transaction, not yet
    committed. They are a liveness signal only; a unit that rolls back still
    reported its rows. Document this on the field.
  - [x] Success: with a counting callback, a unit fixture load reports batch
    counts that sum to the unit's loaded row count; with no callback, behaviour
    and existing tests are unchanged.

- [x] **3.4 Test: callback**
  - [x] Unit/integration tests alongside the existing ingest worker tests: sum
    of reported counts equals rows loaded; no callback is a no-op; a failing
    unit reports only what it committed or nothing, as the worker's
    transaction semantics dictate (assert what the code does and document it).
  - [x] Success: tests pass; existing ingest tests unchanged.
  - [x] Commit — `feat: add optional per-batch progress hook to tick ingest`

- [x] **3.5 Heartbeat throttle**
  - [x] Add `TICK_HEARTBEAT_SECONDS = 30` to `data/tick/constants.py`.
  - [x] New small module (beside the `run` verb) holding a throttled reporter:
    it accumulates counts from the callback and calls
    `recorder.progress(run_id, phase="ingest", done=<rows loaded>,
    total=<rows expected>)` at most once per `TICK_HEARTBEAT_SECONDS`, using an
    injectable clock. `total` is the sum of `provider_record_count` over the
    pass's units; if any selected unit's count is `None`, pass `total=None`
    rather than a partial sum. It is thread-safe.
  - [x] Success: calls arrive at most once per interval with a fake clock; the
    final count is flushed once when ingest ends.

- [x] **3.6 Test: heartbeat**
  - [x] Unit tests with a fake clock and a fake recorder: N rapid batches yield
    one progress call per interval; `total` is `None` when any unit lacks a
    count; the closing flush happens exactly once; concurrent callers do not
    lose counts.
  - [x] Success: tests pass.
  - [x] Commit — `feat: add throttled ingest heartbeat reporter`

- [x] **3.7 `mt data tick run`: core flow**
  - [x] Add the verb beside the other tick verbs (a sibling module of
    `tick_store_cmds.py` if that file would exceed ~300 lines) and register it
    in `cli/commands/tick.py`. No options (LLD CLI Contract).
  - [x] Flow, per LLD Data Flow: `pass_run_recorder(settings)`; `recorder.open(
    PassKind.TICK)`; run the acquisition pass over the full universe with the
    same entry the `pass` verb uses; then run the ingest pass, always, whatever
    the acquisition outcome; fold the two outcomes; close the row with the
    mapped outcome, the exit code `EXIT_BY_OUTCOME[combined]`, and the detail
    `acquire=<o> ingest=<o>`.
  - [x] Reuse the existing pass entry points. Copy no pass logic; no new spend
    path; no new exit code. Print one concise summary line per pass using the
    existing renderers; no new `--json` shape.
  - [x] Success: `uv run mt data tick run --help` shows the verb with no
    options.

- [x] **3.8 `mt data tick run`: failure paths and progress**
  - [x] A preflight refusal or lock-held refusal of either pass still attempts
    the other, then exits `EXIT_PREFLIGHT` (1) and closes the row `FAILED` with
    the refusal text.
  - [x] An unexpected exception closes the row `FAILED` with the exception type
    and re-raises (logged with `logger.exception`). The recorder is best-effort:
    a down primary database loses the row and nothing else.
  - [x] Report phase progress through `recorder.progress` for the five
    acquisition phases and `ingest`, and attach the Task 3.5 heartbeat reporter
    to the ingest pass.
  - [x] Success: each path above is reachable from a fake-pass harness (the
    tests in Task 3.9 exercise them).

- [x] **3.9 Test: `run` with mocked passes**
  - [x] Unit tests with fake acquisition and ingest passes and a fake recorder:
    ingest runs after each of `REFUSED`, `PROVIDER_ABORT` and `IN_FLIGHT`
    (Success Criteria Functional 3); exit code equals
    `EXIT_BY_OUTCOME[combined]`; row outcome follows the map; detail names both
    outcomes; a preflight refusal in either pass yields exit 1, `FAILED`, and
    the other pass attempted; an unexpected exception closes `FAILED` and
    re-raises; a recorder returning no run id (primary down) does not change
    the exit code.
  - [x] Success: tests pass.

- [x] **3.10 Test: `run` integration, and the orphaned row**
  - [x] One integration run on the test cluster (throwaway primary + tick
    databases, empty universe or fixture units, no provider calls): the verb
    exits 0 and one `TICK` row closes `COMPLETE`.
  - [x] Orphan test (LLD TD2): seed an open `TICK` row for this host with a
    dead pid; call `recorder.open(PassKind.TICK)`; assert the old row closed as
    `abandoned: pid … gone` and the new row is open. This pins
    `PassRunRecorder._close_stale`.
  - [x] Success: both tests pass on the test cluster. If the run test hits a
    known integration flake, re-run it in isolation first.

- [x] **3.11 Commit** — `feat: add mt data tick run verb`

---

## 4. Health: tick database and tick retention (LLD TD5, TD6) — effort 3

- [x] **4.1 `units_near_deadline` read**
  - [x] In `data/tick/manifest_request_reads.py`, add
    `units_near_deadline(conn, now, window)` selecting units in `SUBMITTED` or
    `DELIVERED`, not `PROVIDER_HOLE`, with `reopened_at IS NULL` and
    `download_deadline < now + window`, deadlines already past included.
    Take state names from `data/tick/constants.py`; do not write them as
    literals. Return job id and deadline.
  - [x] Add `TICK_RETENTION_WARN_DAYS = 7` to `data/tick/constants.py`; Task
    4.3 passes it as the window.
  - [x] Success: the function is typed and uses parameters, not string-built
    SQL.

- [x] **4.2 Test: `units_near_deadline`**
  - [x] Integration test on the tick test database: seed units at 3 days out,
    8 days out, already past, `PROVIDER_HOLE`, reopened, and a non-in-flight
    state; assert only the in-window in-flight, non-holed, non-reopened units
    return, including the past one.
  - [x] Success: test passes.

- [x] **4.3 `gather_tick` and the informational line**
  - [x] Add a tick health gatherer (new module under `cli/commands/`, keeping
    `health.py` under ~300 lines) that reads through
    `cli/tick_composed.read_line` and `data/tick/line_state.py`. It never opens
    its own pool and shares nothing with the primary connection.
  - [x] `tick database`: for every `TickLineState` other than OK, emit an
    informational line using `unavailable_text` as the detail (for example
    `INFO tick database: not configured (MT_TICK_DB_URL unset)`). It must not
    count toward the verdict. OK emits an OK line.
  - [x] `tick retention`: emitted only when the database line is OK. It fails
    when `units_near_deadline` returns any unit, naming each job id and
    deadline and the action `mt-run tick`; it passes otherwise. When
    `schedule_for(PassKind.TICK)` is manual use `TICK_RETENTION_WARN_DAYS`;
    that is the only case this slice implements.
  - [x] Extend `HealthCheck` minimally so a check can be informational (a
    severity field, not a boolean-and-string convention), and update `render`,
    `verdict_line` and the `--json` output so informational checks print and
    are excluded from the verdict.
  - [x] Success: a stub tick reader in each `TickLineState` produces the right
    lines and verdict contribution.

- [x] **4.4 Wire into `data health`**
  - [x] In `data_health` (`cli/commands/health.py`), run the tick gather before
    the primary connection so tick lines still print when the primary is down.
    A primary outage keeps exit 2 ("could not run") as today. A tick failure
    changes only the tick rows.
  - [x] Success: `env -u MT_TICK_DB_URL uv run mt data health` against the dev
    database prints the informational tick line, the other rows, and the exit
    code the primary checks alone give.

- [x] **4.5 Test: health tick semantics**
  - [x] Unit tests for each non-OK `TickLineState` (not configured,
    unreachable, migrations pending): informational line, exit code unchanged,
    minute/daily/Kalshi rows still print, no `tick retention` row.
  - [x] Integration test: unreachable by pointing `MT_TICK_DB_URL` at a closed
    port; retention failure by seeding a `SUBMITTED` unit with a deadline 3 days
    out, asserting `FAIL tick retention` naming job id, deadline and
    `mt-run tick`, and exit 1; holed and reopened units never fail it.
  - [x] Test that a primary outage still exits 2 with the tick lines printed.
  - [x] Assert the shared health output for an informational check: the
    `--json` shape (the check present, marked informational, not counted) and
    the `verdict_line` text (unchanged by an informational check).
  - [x] Success: tests pass; existing health tests unchanged.

- [x] **4.6 Commit** — `feat: add tick database and tick retention health checks`

---

Continued in `234-tasks.tick-pass-production-wiring-2.md` (Tasks 5–10).
