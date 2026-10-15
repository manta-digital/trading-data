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
part: 2
---

# Tasks: Tick Pass Production Wiring (part 2 of 2)

## Context Summary

- Continues `234-tasks.tick-pass-production-wiring-1.md` (Tasks 1–4: pass kind
  and manual schedule, migration 060, `mt data tick run`, health checks). Read
  its Context Summary first; its constraints, known pre-existing failures and
  effort scale apply here unchanged.
- This part covers the deploy files (5), `add-tick-env.sh` (6), the cutover
  script (7), documentation and standing 220 obligations (8), full validation
  (9), and cutover execution with closeout (10).

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
  - [ ] Add a test that the track parameter reaches the `mt data migrate apply
    --track` call for both `kalshi` and `minute`.
  - [ ] Success: that test and the existing cutover tests (`test_cutover_267.py`,
    `test_cutover_921.py`, `test_cutover_227.py`) pass.
  - [ ] Commit — `refactor: make cutover_common.migrate take the track`

- [ ] **7.2 Cutover scaffolding and steps 1–5**
  - [ ] Create `scripts/cutover_234_tick_wiring.py` (split helpers into a
    `cutover_234_*.py` sibling to stay near 300 lines each), following the 227
    pattern in `cutover_227_tick_backup.py` and its helpers: `STEPS` of title,
    expected, action; stops at the first failure; always writes the report to
    `project-documents/user/notes/<date>-234-cutover.md`; exit 0 only if every
    step passes; check-then-act so a re-run changes nothing already done.
  - [ ] Holds the acquisition timers and waits out any running pass; releases in
    `finally` (use `cutover_common` hold/release).
  - [ ] Takes a required `--pm-designation <reference>` argument, recorded in
    the report. Step 3 refuses to run without it (CLAUDE.md: migration 060
    drops and re-adds a constraint on the production database, which only a
    Project Manager designation permits).
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
  - [ ] Success: the module imports; a dry listing shows steps 1–5.

- [ ] **7.3 Test: scaffolding and steps 1–5**
  - [ ] New `test/unit/test_cutover_234.py` modelled on `test_cutover_227.py`:
    step list and order; stop at first failure and still write the report;
    timers released in `finally` on failure; a missing env key stops at step 1
    naming the add-tick-env command and printing no value; step 3 refuses
    without `--pm-designation` and the report records it when given; step 2's
    check fails on an entry outside the group.
  - [ ] Success: tests pass; ruff and mypy clean.
  - [ ] Commit — `feat: add 234 cutover scaffolding and steps 1-5`

- [ ] **7.4 Steps 6–9**
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
    While the unit is active, read the slice cgroup `memory.max` and compare it
    to the byte value parsed from the installed `manta-acquisition.slice`
    `MemoryMax`; no literal byte value in the script. Check: exit 0, newest
    `TICK` row `COMPLETE`, a `memory peak` journal line; the report records the
    purchase count (expected 0). After: `memory.events` `oom_kill` is 0.
  - [ ] Step 9 surfaces: `mt-run status` has a `tick` row; `mt-run data
    overview` shows a tick pass line and `tick manual`; `mt-run data health`
    shows `tick database` OK and `tick retention` OK.
  - [ ] Success: all nine steps defined; the report names no secret values.

- [ ] **7.5 Test: steps 6–9**
  - [ ] Extend `test/unit/test_cutover_234.py`: the purchase guard refuses when
    anything is wanted; step 7 is adjacent to step 8; the memory-bound
    comparison parses a G-suffixed `MemoryMax` into bytes (1024-based) and
    fails on a mismatch; connection arithmetic passes and fails at the
    boundary; `--workers` present fails step 6; the report omits secret values.
  - [ ] Success: tests pass; ruff, mypy and shellcheck (if shell is invoked)
    clean.
  - [ ] Commit — `feat: add 234 cutover steps 6-9`

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

- [ ] **8.5 Commit** — `docs: record 234 runbook, contract diff, and architecture revisions`

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
  - [ ] Success: no new failures. The known failures seen are listed in the body
    of the Task 9.3 commit if one is made, otherwise in the Task 10.4 commit.

- [ ] **9.3 Commit any fixes** — `fix: resolve 234 validation findings`. If
  Tasks 9.1–9.2 needed no fixes, make no commit.

---

## 10. Cutover execution and closeout (LLD Integration Requirements) — effort 3

- [ ] **10.1 Release ref and Project Manager designation**
  - [ ] The agent pushes the slice branch to origin (`git push -u origin
    234-slice.tick-pass-production-wiring`) after asking the Project Manager to
    confirm the push; Step 1 needs the ref on origin. Use the branch tip or the
    ref the Project Manager names; create no tag unless the Project Manager
    confirms.
  - [ ] Obtain the Project Manager's explicit designation that the cutover may
    apply migration 060 to the production database; its reference is the
    cutover's `--pm-designation` value. With no designation, stop and ask.
  - [ ] Success: `git rev-parse` of the ref matches `git ls-remote origin`, and
    a designation reference is in hand.

- [ ] **10.2 Run the cutover**
  - [ ] From the checkout root as `manta`: `uv run python
    scripts/cutover_234_tick_wiring.py <ref>
    --pm-designation <reference>`.
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
  - [ ] Immediately before each manual `sudo mt-run tick` below, run `mt-run
    data tick pass --estimate-only --json` and proceed only if nothing is
    wanted and cost is 0; otherwise stop and report what is wanted.
  - [ ] Run the LLD Verification Walkthrough steps 2–3 as far as the agent can
    (`mt-run` is covered by the narrow sudo list). Confirm `mt-run status`, `mt-run data overview`,
    `mt-run data health`, and the `/api/v1/overview` tick entry (confirm the
    JSON path and port against the installed `mt-serve`).
  - [ ] TD2 verification (one guard run covers both starts): start the unit
    twice concurrently and assert exactly
    one new `TICK` row and that the second `mt-run tick` returns after the
    first ends.
  - [ ] `curl` an `/api/v1/futures/*` route on production `mt-serve` and assert
    it responds (Integration Requirement 2).
  - [ ] Success: all outputs match the walkthrough's expected results; any
    difference is recorded.

- [ ] **10.4 Refine the walkthrough and close out**
  - [ ] Update the LLD's Verification Walkthrough with the confirmed commands
    and paths (it is marked "Draft; refined after implementation").
  - [ ] Draft a comment for issue #33 (memory bound in force) citing the
    report, and one for issue #34 saying that re-deriving the bound with tick
    remains open. Post them, and close #33, only after the Project Manager
    confirms. Do not close #34.
  - [ ] Commit the report and doc updates —
    `docs: record 234 cutover report and verified walkthrough`
  - [ ] Success: working tree clean; all earlier checklist items checked via
    the task-checker agent. The slice ends at a commit on the slice branch; the
    merge belongs to Phase 7 after review.
