---
docType: tasks
slice: tick-pass-production-wiring
project: trading-data
lld: user/slices/234-slice.tick-pass-production-wiring.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [225-slice.ingest-pass-and-proof-parity, 230-slice.operator-surface-full-parity, 231-slice.api-surface-coverage-futures-tick, 233-slice.cross-source-acquisition-arbitration]
projectState: >
  Slice design committed; design review rounds 1 and 2 settled (CONCERNS, no
  open findings). The minute migration chain ends at 059. PassKind has
  MINUTE, DAILY, KALSHI, HEALTH, ACCOUNTING; no tick unit, no tick timer, no
  tick keys in /etc/manta-trading.env. The tick passes run only from the dev
  checkout. 233's MemoryMax=14G is declared in the repo and not in force.
dateCreated: 20261014
dateUpdated: 20261014
status: not_started
---

# Tasks: Tick Pass Production Wiring

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
  adds both ends.
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

- [ ] **1.1 Add `PassKind.TICK`**
  - [ ] In `src/manta_trading/data/acquisition/pass_runs.py`, add `TICK = "tick"`
    to `PassKind` with a one-line docstring in the style of its siblings.
  - [ ] Grep the repo for every `match`/`if` on `PassKind` and for every
    `for kind in PassKind` loop (`src`, `scripts`, `test`). List each in the
    commit message body and handle tick explicitly in each (Tasks 1.2–1.4 cover
    the schedule callers). No site may fall through to a default for tick.
  - [ ] Success: `PassKind("tick")` resolves; mypy is clean on the touched files.

- [ ] **1.2 Add `FiringSchedule.manual()` and the `schedule_for` branch**
  - [ ] In `src/manta_trading/firing_schedule.py`, add a classmethod `manual()`
    returning a schedule with no times, and an `is_manual` property that is true
    only for it (not for any other empty schedule).
  - [ ] `describe()` returns `manual (no timer)` for a manual schedule.
  - [ ] `schedule_for(PassKind.TICK, ...)` returns `FiringSchedule.manual()`.
    The unreachable-branch `AssertionError` at the end stays.
  - [ ] `next_firing_at` keeps raising on a schedule with no times. Do not make
    it return `None` or a sentinel.
  - [ ] Success: `schedule_for(PassKind.TICK).is_manual` is true and every other
    kind's is false.

- [ ] **1.3 Make every `schedule_for` caller handle a manual schedule**
  - [ ] `_pass_line` in `src/manta_trading/cli/commands/overview.py`: check
    `is_manual` first and produce a pass line whose next firing reads `manual`.
    Remove the `RuntimeError` catch that exists to absorb an empty schedule; if
    other behaviour depends on it, keep the catch only for that and say why in a
    comment.
  - [ ] `render_next_firings` in `src/manta_trading/cli/rendering/overview.py`:
    print `tick manual` for a manual kind instead of a time.
  - [ ] The API model `_pass_line` in `api_server/models/operations.py`: confirm
    the next-firing field can carry "manual" (or its absence) without a schema
    break; if it cannot, stop and raise it with the Project Manager.
  - [ ] Grep for any other caller of `schedule_for` or `next_firing_at` and
    handle each.
  - [ ] Success: `mt data overview` with a tick row renders without exception.

- [ ] **1.4 Test: schedule and callers**
  - [ ] In `test/unit/test_firing_schedule.py`, update the three tests that
    iterate `PassKind` so a firing is asserted only for kinds that are not
    manual.
  - [ ] Add one test (same file, no new guard module) asserting that
    `schedule_for(kind).is_manual` holds if and only if
    `deploy/systemd/mt-{kind}-pass.timer` is absent, for every pass kind that
    has a `mt-{kind}-pass.service` file. Tick has its service after Task 5.1;
    until then the test covers the kinds that already have units, and Task 5.2
    confirms tick is included.
  - [ ] Add tests: `describe()` text; `next_firing_at` still raises on a manual
    schedule; overview `_pass_line` and `render_next_firings` for tick.
  - [ ] Success: `uv run pytest test/unit/test_firing_schedule.py` and the
    overview unit tests pass; ruff and mypy clean on touched files.

- [ ] **1.5 Commit** — `feat: add PassKind.TICK and manual firing schedule`

---

## 2. Migration 060 and the API enum (LLD Database/Storage, API Contracts) — effort 2

- [ ] **2.1 Write migration `060_pass_kind_tick`**
  - [ ] In `src/manta_trading/market/schema/migrations/minute.py`, append the
    migration after `059_seed_cme_metals_calendar`. Follow the `kalshi_007`
    pattern: `DROP CONSTRAINT IF EXISTS pass_runs_pass_check`, then
    `ADD CONSTRAINT … CHECK ({_pass_kind_check_sql()})`.
  - [ ] Check the migration registry for any position or count assertion that a
    new entry affects (migration-order notes near `055`).
  - [ ] Success: the migration is listed by `mt data migrate status --track
    minute` as pending against a database at 059.

- [ ] **2.2 Test: migration 060**
  - [ ] New `test/integration/test_migration_060_pass_kind_tick.py`, modelled on
    `test_migration_055_pass_runs.py`. Create a throwaway database at `055`,
    apply through `060`, and assert an insert with pass `tick` is accepted
    while an unknown pass is still rejected.
  - [ ] Add the 060 id to any chain-end assertion that lists the expected last
    migration, except the known stale `chain_ends_at_052` test.
  - [ ] Success: the new test passes; no new failure in
    `test_schema_migrations.py`.

- [ ] **2.3 Regenerate the API docs**
  - [ ] Run `scripts/dump_openapi.py` to rewrite `docs/api/openapi.json`; the
    `pass` enum gains `tick`. Run `scripts/check_api_docs.py`.
  - [ ] Success: the diff of `openapi.json` is the enum addition only (plus any
    next-firing description change from Task 1.3); the schema test passes.

- [ ] **2.4 Commit** — `feat: add migration 060 pass_kind_tick and regenerate openapi`

---

## 3. The `mt data tick run` verb (LLD TD2, Data Flow) — effort 4

- [ ] **3.1 Outcome severity fold and run-outcome map**
  - [ ] In `src/manta_trading/cli/commands/tick_exit.py`, next to
    `EXIT_BY_OUTCOME`, define once the severity order
    (`STORAGE_ABORT` > `PROVIDER_ABORT` > `REFUSED` > `IN_FLIGHT` > `PARTIAL` >
    `OK`) and a function folding two `TickOutcome`s to the worse.
  - [ ] Define `PASS_RUN_OUTCOME_BY_TICK_OUTCOME` (table in LLD Data Flow
    step 4) with an exhaustiveness assert modelled on
    `PASS_RUN_OUTCOME_BY_SYNC_OUTCOME` in `cli/commands/kalshi.py`. If
    `tick_exit.py` would pass ~300 lines, put the map in a sibling module.
  - [ ] Add an exhaustiveness assert that the severity order lists every
    `TickOutcome`.
  - [ ] Success: importing the module fails loudly if a `TickOutcome` member is
    added to neither structure.

- [ ] **3.2 Test: fold and map**
  - [ ] Unit tests: the fold is symmetric; every ordered pair resolves to the
    higher-severity member; `OK` is the identity; the map covers every member
    with the LLD's values.
  - [ ] A test asserting the severity order agrees with `EXIT_BY_OUTCOME`
    severity as the LLD describes: derive the expectation from the exit
    vocabulary (storage and provider exits ranked above refused, in-flight,
    partial), not from a copy of the same list.
  - [ ] Success: tests pass.

- [ ] **3.3 Ingest per-batch progress callback**
  - [ ] Add an optional thread-safe callback field to `IngestInputs`
    (`data/tick/ingest_pass.py`) that receives the row count of each `COPY`
    batch a worker commits. Default is "none"; the existing `mt data tick
    ingest` path must behave exactly as today.
  - [ ] Thread it through to `_copy` in `data/tick/ingest_worker.py` and call it
    once per batch with the batch's row count. Only a count crosses the worker
    boundary (LLD Data Flow step 2).
  - [ ] Decide and implement how the callback is carried to worker processes or
    threads by reading `ingest_pass.py` and the worker launch path; use the
    existing worker model, and add no new concurrency primitive beyond a
    thread-safe counter. If workers are processes and a callback cannot cross,
    stop and raise it with the Project Manager rather than polling the database.
  - [ ] Success: with a counting callback, a unit fixture load reports batch
    counts that sum to the unit's loaded row count; with no callback, behaviour
    and existing tests are unchanged.

- [ ] **3.4 Test: callback**
  - [ ] Unit/integration tests alongside the existing ingest worker tests: sum
    of reported counts equals rows loaded; no callback is a no-op; a failing
    unit reports only what it committed or nothing, as the worker's
    transaction semantics dictate (assert what the code does and document it).
  - [ ] Success: tests pass; existing ingest tests unchanged.

- [ ] **3.5 Heartbeat throttle**
  - [ ] Add `TICK_HEARTBEAT_SECONDS = 30` to `data/tick/constants.py`
    (and `TICK_RETENTION_WARN_DAYS = 7`, used by Task 4.1).
  - [ ] New small module (beside the `run` verb) holding a throttled reporter:
    it accumulates counts from the callback and calls
    `recorder.progress(run_id, phase="ingest", done=<rows loaded>,
    total=<rows expected>)` at most once per `TICK_HEARTBEAT_SECONDS`, using an
    injectable clock. `total` is the sum of `provider_record_count` over the
    pass's units; if any selected unit's count is `None`, pass `total=None`
    rather than a partial sum. It is thread-safe.
  - [ ] Success: calls arrive at most once per interval with a fake clock; the
    final count is flushed once when ingest ends.

- [ ] **3.6 Test: heartbeat**
  - [ ] Unit tests with a fake clock and a fake recorder: N rapid batches yield
    one progress call per interval; `total` is `None` when any unit lacks a
    count; the closing flush happens exactly once; concurrent callers do not
    lose counts.
  - [ ] Success: tests pass.

- [ ] **3.7 Implement `mt data tick run`**
  - [ ] Add the verb beside the other tick verbs (a sibling module of
    `tick_store_cmds.py` if that file would exceed ~300 lines) and register it
    in `cli/commands/tick.py`. No options (LLD CLI Contract).
  - [ ] Flow, per LLD Data Flow: `pass_run_recorder(settings)`; `recorder.open(
    PassKind.TICK)`; run the acquisition pass over the full universe with the
    same entry the `pass` verb uses; run the ingest pass with the heartbeat
    reporter; fold the two outcomes; close the row with the mapped outcome, the
    exit code `EXIT_BY_OUTCOME[combined]`, and the detail `acquire=<o>
    ingest=<o>`. Report phase progress through `recorder.progress` for the five
    acquisition phases and `ingest`.
  - [ ] Ingest always runs after the acquisition pass, whatever its outcome. A
    preflight refusal or lock-held refusal of either pass still attempts the
    other, then exits `EXIT_PREFLIGHT` (1) and closes the row `FAILED` with the
    refusal text.
  - [ ] An unexpected exception closes the row `FAILED` with the exception type
    and re-raises (logged with `logger.exception`). The recorder is best-effort:
    a down primary database loses the row and nothing else.
  - [ ] Reuse the existing pass entry points. Copy no pass logic; no new spend
    path; no new exit code.
  - [ ] Print one concise summary line per pass using the existing renderers;
    no new `--json` shape.
  - [ ] Success: `uv run mt data tick run --help` shows the verb with no
    options.

- [ ] **3.8 Test: `run` with mocked passes**
  - [ ] Unit tests with fake acquisition and ingest passes and a fake recorder:
    ingest runs after each of `REFUSED`, `PROVIDER_ABORT` and `IN_FLIGHT`
    (Success Criteria Functional 3); exit code equals
    `EXIT_BY_OUTCOME[combined]`; row outcome follows the map; detail names both
    outcomes; a preflight refusal in either pass yields exit 1, `FAILED`, and
    the other pass attempted; an unexpected exception closes `FAILED` and
    re-raises; a recorder returning no run id (primary down) does not change
    the exit code.
  - [ ] Success: tests pass.

- [ ] **3.9 Test: `run` integration, and the orphaned row**
  - [ ] One integration run on the test cluster (throwaway primary + tick
    databases, empty universe or fixture units, no provider calls): the verb
    exits 0 and one `TICK` row closes `COMPLETE`.
  - [ ] Orphan test (LLD TD2): seed an open `TICK` row for this host with a
    dead pid; call `recorder.open(PassKind.TICK)`; assert the old row closed as
    `abandoned: pid … gone` and the new row is open. This pins
    `PassRunRecorder._close_stale`.
  - [ ] Success: both tests pass on the test cluster. If the run test hits a
    known integration flake, re-run it in isolation first.

- [ ] **3.10 Commit** — `feat: add mt data tick run with outcome fold and ingest heartbeat`

---

## 4. Health: tick database and tick retention (LLD TD5, TD6) — effort 3

- [ ] **4.1 `units_near_deadline` read**
  - [ ] In `data/tick/manifest_request_reads.py`, add
    `units_near_deadline(conn, now, window)` selecting units in `SUBMITTED` or
    `DELIVERED`, not `PROVIDER_HOLE`, with `reopened_at IS NULL` and
    `download_deadline < now + window`, deadlines already past included.
    Take state names from `data/tick/constants.py`; do not write them as
    literals. Return job id and deadline.
  - [ ] Success: the function is typed and uses parameters, not string-built
    SQL.

- [ ] **4.2 Test: `units_near_deadline`**
  - [ ] Integration test on the tick test database: seed units at 3 days out,
    8 days out, already past, `PROVIDER_HOLE`, reopened, and a non-in-flight
    state; assert only the in-window in-flight, non-holed, non-reopened units
    return, including the past one.
  - [ ] Success: test passes.

- [ ] **4.3 `gather_tick` and the informational line**
  - [ ] Add a tick health gatherer (new module under `cli/commands/`, keeping
    `health.py` under ~300 lines) that reads through
    `cli/tick_composed.read_line` and `data/tick/line_state.py`. It never opens
    its own pool and shares nothing with the primary connection.
  - [ ] `tick database`: for every `TickLineState` other than OK, emit an
    informational line using `unavailable_text` as the detail (for example
    `INFO tick database: not configured (MT_TICK_DB_URL unset)`). It must not
    count toward the verdict. OK emits an OK line.
  - [ ] `tick retention`: emitted only when the database line is OK. It fails
    when `units_near_deadline` returns any unit, naming each job id and
    deadline and the action `mt-run tick`; it passes otherwise. When
    `schedule_for(PassKind.TICK)` is manual use `TICK_RETENTION_WARN_DAYS`;
    that is the only case this slice implements.
  - [ ] Extend `HealthCheck` minimally so a check can be informational (a
    severity field, not a boolean-and-string convention), and update `render`,
    `verdict_line` and the `--json` output so informational checks print and
    are excluded from the verdict.
  - [ ] Success: a stub tick reader in each `TickLineState` produces the right
    lines and verdict contribution.

- [ ] **4.4 Wire into `data health`**
  - [ ] In `data_health` (`cli/commands/health.py`), run the tick gather before
    the primary connection so tick lines still print when the primary is down.
    A primary outage keeps exit 2 ("could not run") as today. A tick failure
    changes only the tick rows.
  - [ ] Success: `env -u MT_TICK_DB_URL uv run mt data health` against the dev
    database prints the informational tick line, the other rows, and the exit
    code the primary checks alone give.

- [ ] **4.5 Test: health tick semantics**
  - [ ] Unit tests for each non-OK `TickLineState` (not configured,
    unreachable, migrations pending): informational line, exit code unchanged,
    minute/daily/Kalshi rows still print, no `tick retention` row.
  - [ ] Integration test: unreachable by pointing `MT_TICK_DB_URL` at a closed
    port; retention failure by seeding a `SUBMITTED` unit with a deadline 3 days
    out, asserting `FAIL tick retention` naming job id, deadline and
    `mt-run tick`, and exit 1; holed and reopened units never fail it.
  - [ ] Test that a primary outage still exits 2 with the tick lines printed.
  - [ ] Success: tests pass; existing health tests unchanged.

- [ ] **4.6 Commit** — `feat: add tick database and tick retention health checks`

---

## 5. Deploy: unit, kinds, installer (LLD scope 4, 5) — effort 2

- [ ] **5.1 `mt-tick-pass.service`**
  - [ ] Create `deploy/systemd/mt-tick-pass.service` modelled on
    `mt-kalshi-pass.service`: oneshot, `Slice=manta-acquisition.slice`,
    `UMask=0002`, `TimeoutStartSec=4h`, no `TimeoutStopSec`, no `[Install]`,
    `ExecStart` running `mt data tick run` the same way the sibling units
    invoke `mt`. No timer file.
  - [ ] Add a comment block giving the reason for each non-default setting
    (LLD TD2), briefly.
  - [ ] Success: `systemd-analyze verify` on the file reports no errors, or the
    same warnings the sibling units produce.

- [ ] **5.2 Register in the installer and `mt-run`**
  - [ ] Add `mt-tick-pass.service` to `UNITS` in `deploy/install-production.sh`
    (service only; no timer entry).
  - [ ] Add `tick` to `KINDS` in `deploy/mt-run`. Check the usage text and any
    help listing of kinds, and update them from the same list rather than a
    second literal.
  - [ ] Check how the installer enables timers: tick must not be enabled or
    started at install.
  - [ ] Success: `shellcheck` clean on both scripts.

- [ ] **5.3 Test: deploy tests**
  - [ ] In `test/unit/deploy/test_units.py`, add assertions for the tick
    service (`ExecStart`, `Slice=`, oneshot, `UMask=0002`,
    `TimeoutStartSec=4h`, no `[Install]`, no `TimeoutStopSec`), its presence in
    `UNITS`, `tick` in `KINDS`, and include `mt-tick-pass.service` in
    `test_members_from_real_unit_files`.
  - [ ] Confirm the manual-iff-no-timer test from Task 1.4 now includes tick
    (service present, timer absent, `is_manual` true).
  - [ ] Success: `uv run pytest test/unit/deploy test/unit/test_firing_schedule.py`
    passes.

- [ ] **5.4 Commit** — `feat: add mt-tick-pass unit and mt-run tick kind`

---

## 6. `add-tick-env.sh` (LLD TD3) — effort 2

- [ ] **6.1 Write the script**
  - [ ] Create `deploy/add-tick-env.sh --from <checkout .env>`, modelled on how
    existing deploy scripts call `deploy/lib/env_add_keys.sh`. Do not change
    `env_add_keys.sh`.
  - [ ] Keys, defined once in the script: `MT_TICK_DB_URL`,
    `MT_DATABENTO_API_KEY`, `MT_TICK_SPEND_CEILING_USD`,
    `MT_TICK_SPEND_30D_CEILING_USD`, `MT_TICK_ARCHIVE_DIR`.
  - [ ] Check-then-act: refuse, naming the missing key names only, if any of the
    five is missing from the source. Never copy `MT_TICK_MAINTENANCE_URL`.
    A key already present in the target is never changed. Values are never
    printed or logged; print a count of keys added.
  - [ ] After the call, re-assert mode `0640` and owner `root:manta-trading`
    (`env_add_keys` leaves the file at 0600). Log to a file.
  - [ ] Add `# shellcheck`-clean quoting and `set -euo pipefail`.
  - [ ] Success: `shellcheck` is clean (`~/.local/bin/shellcheck`).

- [ ] **6.2 Test: the script**
  - [ ] Add a test following the existing deploy-script tests' pattern (look
    for `env_add_keys` tests under `test/`): run the script against a temp
    target file and temp source with the root-only operations stubbed or made
    parameters. Cover: adds absent keys only; a present key is unchanged; a
    source missing one key is refused and writes nothing; the maintenance key in
    the source is not copied; no value appears in stdout/stderr/log; second run
    adds 0.
  - [ ] If the chown/chmod cannot be exercised without root, the test asserts
    the script issues them (via an injectable command prefix) rather than
    skipping the check.
  - [ ] Success: tests pass.

- [ ] **6.3 Commit** — `feat: add add-tick-env.sh for production tick settings`

---

## 7. Cutover script (LLD TD4) — effort 4

- [ ] **7.1 Generalise `cutover_common.migrate` to take the track**
  - [ ] In `scripts/cutover_common.py`, change `migrate(migration_id)` (hard
    wired to the Kalshi track) to take the track name, and update every caller
    (`cutover_922_overview.py`, `cutover_265_trades.py`, others found by grep)
    to pass `kalshi` explicitly. Check how `verify_migrate_target` and
    `_migrate_status` pick the track and parameterise them too.
  - [ ] Success: existing cutover tests (`test_cutover_267.py`,
    `test_cutover_921.py`, `test_cutover_227.py`) pass unchanged in behaviour.

- [ ] **7.2 Cutover step scaffolding and steps 1–5**
  - [ ] Create `scripts/cutover_234_tick_wiring.py` (split helpers into a
    `cutover_234_*.py` sibling to stay near 300 lines each), following the 227
    pattern in `cutover_227_tick_backup.py` and its helpers: `STEPS` of title,
    expected, action; stops at the first failure; always writes the report to
    `project-documents/user/notes/<date>-234-cutover.md`; exit 0 only if every
    step passes; check-then-act so a re-run changes nothing already done.
  - [ ] Holds the acquisition timers and waits out any running pass; releases in
    `finally` (use `cutover_common` hold/release).
  - [ ] Step 1 preconditions: runs from the checkout root as `manta`; the ref
    exists locally and on origin; the five env keys are present **by name**
    (read `/etc/manta-trading.env` the way existing cutovers do; never print
    values). If any key is absent, stop and print the exact
    `sudo …/deploy/add-tick-env.sh --from …/.env` command.
  - [ ] Step 2 archive directory: as `manta`, `chgrp -R manta-trading`,
    `chmod -R g+w`, `g+s` on directories under `MT_TICK_ARCHIVE_DIR`. Check:
    every entry has group `manta-trading` and is group-writable.
  - [ ] Step 3 migrate: `cutover_common.migrate` for minute track
    `060_pass_kind_tick` after `verify_migrate_target`. Check:
    `pass_runs_pass_check` accepts `tick`.
  - [ ] Step 4 install the ref (installer, `daemon-reload`). Check: `/opt` HEAD
    equals the ref and `mt --version`.
  - [ ] Step 5 restart `mt-serve` if active. Check: `/health` reports the tick
    pool reachable.
  - [ ] Success: the module imports; `--help` or a dry listing shows steps 1–5.

- [ ] **7.3 Steps 6–9**
  - [ ] Step 6 connections: the installed `mt-serve` `ExecStart` has no
    `--workers` (fail otherwise). Tick cluster: 231 API budget (pool constants
    in `src`, `TICK_API_POOL_MAX` ×2 pools per worker) + 1 +
    `TICK_INGEST_WORKERS` fits in `max_connections` −
    `superuser_reserved_connections` − live `pg_stat_activity`. Primary: 231's
    recorded extra + 1 under the same check. Read pool sizes from constants; no
    new worker arithmetic.
  - [ ] Step 7 purchase guard: `mt-run data tick pass --estimate-only --json`;
    pass only if nothing wanted and cost 0, else stop and report what is
    wanted. It runs immediately before step 8's firing with no step between.
  - [ ] Step 8 supervised firing: `fire_unit("mt-tick-pass.service", ["tick"])`.
    While the unit is active, read the slice cgroup `memory.max` and compare to
    the value parsed from the installed `manta-acquisition.slice` `MemoryMax`
    (14G = 15032385536 bytes). Check: exit 0, newest `TICK` row `COMPLETE`, a
    `memory peak` journal line; the report records the purchase count (expected
    0). After: `memory.events` `oom_kill` is 0.
  - [ ] Step 9 surfaces: `mt-run status` has a `tick` row; `mt-run data
    overview` shows a tick pass line and `tick manual`; `mt-run data health`
    shows `tick database` OK and `tick retention` OK.
  - [ ] Success: all nine steps defined; the report names no secret values.

- [ ] **7.4 Test: cutover script**
  - [ ] New `test/unit/test_cutover_234.py` modelled on `test_cutover_227.py`:
    step list and order; stop at first failure and still write the report;
    missing env key stops at step 1 naming the add-tick-env command and no
    value; purchase guard refuses when anything is wanted; step 7 is adjacent
    to step 8; memory-bound comparison parses `14G` correctly and fails on a
    mismatch; connection arithmetic passes and fails at the boundary; `--workers`
    present fails step 6; timers released in `finally` on failure.
  - [ ] Test the generalised `migrate` track parameter (Task 7.1).
  - [ ] Success: tests pass; ruff, mypy and shellcheck (if shell is invoked)
    clean.

- [ ] **7.5 Commit** — `feat: add 234 tick wiring cutover script`

---

## 8. Documentation and standing 220 obligations — effort 2

- [ ] **8.1 Runbook 100**
  - [ ] In `project-documents/user/runbooks/100-production-operations.md`: add
    the tick unit row to "The units" (oneshot, no timer, manual via `sudo
    mt-run tick`, `TimeoutStartSec=4h`), the five tick keys to "Environment
    file" (and a note that `MT_TICK_MAINTENANCE_URL` is never present), the
    `add-tick-env.sh` one-time step, the retention health row and its action,
    and the "stage a source under `/data`" note from the LLD Special
    Considerations. Add a Revision Log line.
  - [ ] Success: every statement matches the unit file, `KINDS`, and script.

- [ ] **8.2 Kalshi contract diff (standing 220 obligation)**
  - [ ] Diff the tick pass contract (`data/tick/pass_contract.py`, the new
    `run` verb) against the Kalshi original (`cli/commands/kalshi.py` pass:
    recorder use, outcome map, exit codes, best-effort contract). Record the
    differences and why each is justified in the slice design's section or a
    short note in `project-documents/user/notes/`, wherever the other 220
    slices record theirs (look at 225/230 for precedent).
  - [ ] Success: a written diff exists with no unexplained difference.

- [ ] **8.3 Data-correctness contract mapping rows**
  - [ ] Add the 234 rows to the slice-mapping table of the data-correctness
    contract (find it with `grep -rl "slice-mapping"` under `project-documents`;
    the table is referenced from the 220 architecture and slice plan). Rows
    state which invariants 234 touches (the pass-run record and health lines;
    no data invariant changes).
  - [ ] Success: table rows present in the format of the neighbouring rows.

- [ ] **8.4 Architecture Revision Log and Cadence text**
  - [ ] In `220-arch.data-acquisition-futures-tick-primary-focus.md`, reword the
    Cadence sentence "no `schedule_for` branch, no `PassKind.TICK` firing
    calendar" and the Escalation sentence "deadline before the next scheduled
    firing" to match the slice (LLD "Architecture statements superseded"), and
    add the two Revision Log entries. Preserve any "User-Provided Concept"
    sections exactly.
  - [ ] Realtime-paths and API answers: confirm the LLD's two answers are
    recorded in the slice plan entry for 234 (update it if the plan entry still
    says three env keys or omits `run`).
  - [ ] Success: the architecture text and the LLD no longer contradict.

- [ ] **8.5 Changelog and commit**
  - [ ] Add a CHANGELOG entry for 234 in the file's existing format. Do not bump
    a version or tag (release timing is the Project Manager's call).
  - [ ] Commit — `docs: record 234 runbook, contract diff, and architecture revisions`

---

## 9. Full validation (LLD Technical Requirements 6) — effort 2

- [ ] **9.1 Static checks**
  - [ ] `ruff check` and `ruff format --check` on touched files only; `mypy`
    run in one invocation over the src paths plus `kalshi_support` and tests as
    the project memory describes; `shellcheck` on `deploy/mt-run`,
    `deploy/install-production.sh`, `deploy/add-tick-env.sh`.
  - [ ] `git diff main` shows no deletions of pre-existing lines caused by
    formatting.
  - [ ] Success: all clean.

- [ ] **9.2 Test tiers**
  - [ ] Run the unit tier, then the integration tier separately, with
    `MT_TIMESCALE_TEST_URL` exported from `.env`. Failures limited to the known
    pre-existing list in the Context Summary are acceptable; re-run any other
    failure in isolation before investigating, and fix real ones.
  - [ ] Success: no new failures; the list of known failures seen is recorded in
    the commit message body.

- [ ] **9.3 Commit** — `chore: validate 234 static checks and test tiers` (only if
  fixes were needed; otherwise note the clean result in the next commit body).

---

## 10. Cutover execution and closeout (LLD Integration Requirements) — effort 3

- [ ] **10.1 Tag-free release ref**
  - [ ] Confirm the slice commits are pushed to origin on the slice branch so
    Step 1's "ref exists locally and on origin" holds. Use the branch tip or
    the ref the Project Manager names; create no tag unless the Project Manager
    confirms.
  - [ ] Success: `git rev-parse` of the ref matches `git ls-remote origin`.

- [ ] **10.2 Run the cutover**
  - [ ] From the checkout root as `manta`: `uv run python
    scripts/cutover_234_tick_wiring.py <ref>`.
  - [ ] If Step 1 stops because the five tick keys are absent from
    `/etc/manta-trading.env`, that is the single Project Manager host act: the
    script prints the one `sudo …/deploy/add-tick-env.sh --from …/.env`
    command. Report it to the Project Manager, and re-run the whole script
    after the keys exist. Every other step needs no Project Manager action.
  - [ ] Success: exit 0 and `project-documents/user/notes/<date>-234-cutover.md`
    shows every step PASS, including `memory.max` equal to the declared
    `MemoryMax`, `oom_kill` 0, both clusters within their connection budget,
    estimate-only finding nothing to buy, and one `COMPLETE` `TICK` row with
    purchase count 0.

- [ ] **10.3 Verify the operator surfaces and the double start**
  - [ ] Run the LLD Verification Walkthrough steps 2–3 as far as the agent can
    (`sudo mt-run tick` is permitted only if the narrow sudo list covers
    `mt-run`; it does). Confirm `mt-run status`, `mt-run data overview`,
    `mt-run data health`, and the `/api/v1/overview` tick entry (confirm the
    JSON path and port against the installed `mt-serve`).
  - [ ] TD2 verification: start the unit twice concurrently and assert exactly
    one new `TICK` row and that the second `mt-run tick` returns after the
    first ends.
  - [ ] `curl` an `/api/v1/futures/*` route on production `mt-serve` and assert
    it responds (Integration Requirement 2).
  - [ ] Success: all outputs match the walkthrough's expected results; any
    difference is recorded.

- [ ] **10.4 Refine the walkthrough and close out**
  - [ ] Update the LLD's Verification Walkthrough with the confirmed commands
    and paths (it is marked "Draft; refined after implementation").
  - [ ] Close issue #33 (memory bound in force) with a comment citing the
    report; comment on issue #34 that re-deriving the bound with tick remains
    open. Do not close #34.
  - [ ] Commit the report and doc updates —
    `docs: record 234 cutover report and verified walkthrough`
  - [ ] Success: working tree clean; all earlier checklist items checked via
    the task-checker agent. The slice ends at a commit on the slice branch; the
    merge belongs to Phase 7 after review.
