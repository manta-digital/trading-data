---
docType: slice-design
slice: tick-pass-production-wiring
project: trading-data
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [225-slice.ingest-pass-and-proof-parity, 230-slice.operator-surface-full-parity, 231-slice.api-surface-coverage-futures-tick, 233-slice.cross-source-acquisition-arbitration]
interfaces: []
dateCreated: 20261014
dateUpdated: 20261014
status: not_started
---

# Slice Design: Tick Pass Production Wiring

## Overview

The tick passes (`mt data tick pass` buys, `mt data tick ingest` loads) run today only from the dev checkout as user `manta`. Production has none of the tick settings, no unit, and no `pass_runs` row for a tick run. This slice puts the tick passes into the slice-916 production form:
- one oneshot unit under `manta-acquisition.slice`;
- an `mt-run tick` kind;
- a `PassKind.TICK` run record;
- the tick settings in `/etc/manta-trading.env`;
- a tick section in the health check.

One cutover script installs all of it and verifies it.

**Cadence answer: manual only, no timer.** The slice plan defers this slice "until the cadence decision", and PM direction 2026-09-27 stands: every tick pass runs by hand until realtime work makes a cadence decidable. The realtime initiative has not started. The architecture ("Cadence") requires a manual-only pass to give `schedule_for` an explicit "no timer" answer, not an omission. This design gives that answer: `FiringSchedule.manual()`. Adding a timer later means one `schedule_for` branch, one timer file, and one drift test. Nothing built here would have to be undone.

## Value

- **The operator runs tick work the way they run every other source.** `sudo mt-run tick` starts it, `mt-run follow tick` tails it, and `mt-run status`, `mt data overview` and `/api/v1/overview` show what ran and how it ended. The dev checkout and its `.env` stop being the production path.
- **Unattended surfaces see tick.** The hourly health check reports the tick database's state and any paid delivery near its 30-day download deadline. Before this slice, an expiring job was visible only to someone who ran `mt data tick status`.
- **233's memory bound goes into force.** It is declared in the repo but inert in production: `memory.max` reads `max` today. The bound is applied and verified (issue #33).
- **The API's tick endpoints work in production.** `mt-serve` gains `MT_TICK_DB_URL`. The cutover checks connection capacity on both clusters before it restarts `mt-serve`.

## Technical Scope

**Included**
1. A minute-track migration `060_pass_kind_tick` that re-renders `pass_runs_pass_check` from `PassKind` after `TICK` is added.
2. `PassKind.TICK`, and `FiringSchedule.manual()` with the `schedule_for` branch that returns it. Every caller of `schedule_for` handles a manual schedule explicitly.
3. A new verb, `mt data tick run`: the acquisition pass, then the ingest pass, recorded as one `pass_runs` row of kind `TICK`.
4. `deploy/systemd/mt-tick-pass.service` (oneshot, `Slice=manta-acquisition.slice`, `UMask=0002`, `TimeoutStartSec=infinity`, no `TimeoutStopSec`), with no timer. It is added to the installer's `UNITS`. Timeout and kill handling: TD2.
5. `tick` in `mt-run`'s `KINDS`, which gives `mt-run tick`, `mt-run follow tick`, and the status row.
6. `deploy/add-tick-env.sh`, a root script run once. It adds the five tick keys to `/etc/manta-trading.env` from the checkout's `.env`.
7. The tick archive directory made group-writable for `manta-trading`.
8. Health-check tick checks: `tick database` (reachable, migrations current) and `tick retention` (an in-flight unit whose deadline is within `TICK_RETENTION_WARN_DAYS`, or already past).
9. `scripts/cutover_234_tick_wiring.py`, which covers:
   - 233's `MemoryMax`, verified in force (issue #33);
   - the connection-budget check on both clusters;
   - one supervised firing that buys nothing.
10. Runbook 100: the tick unit row, both "Adding a source" checklists merged into one, the tick keys in "Environment file", and "Running passes by hand" pointed at `mt-run`.
11. The regenerated `docs/api/openapi.json`. The `pass` enum gains `tick`.
12. Standing 220 obligations:
    - diff the tick pass contract against the Kalshi original;
    - add rows to the data-correctness contract's slice-mapping table;
    - the realtime-paths check;
    - the API question (both answered under Technical Decisions).

**Excluded**
- Any timer or `OnCalendar` for tick, and any cadence constant.
- A `pass_runs` row for by-hand `mt data tick pass` / `ingest`. These stay operator tools; the production entry point is `run`.
- Changes to `env_add_keys.sh`, the tick pass logic, the spend guards, or the universe.
- Any purchase. The cutover refuses to fire if the universe wants anything.
- Open interest (Future Work item 2). See Special Considerations: the trigger question is asked again here.

## Dependencies

### Prerequisites
- **225 ingest pass and 224 acquisition pass:** the two passes `run` composes.
- **230:** `data/tick/line_state.py` (`TickLineState`, `TickUnavailable`, `unavailable_text`) and `cli/tick_composed.read_line`, which the health check reuses.
- **231:** the API tick pool (`TICK_API_POOL_MAX = 4`, two pools per worker) and its recorded connection budget.
- **233:** `MemoryMax=14G` on `manta-acquisition.slice` and `scripts/acquisition_memory_bound.py`.
- **227:** `/data/tick-archive` is already in the nightly restic set. Moving the writer to `manta-trading` does not change the path.

### Interfaces Required
- **`pass_run_recorder(settings)`:** the recorder's best-effort contract (`PASS_RUN_DB_CONNECT_TIMEOUT_SECONDS`), as used by `mt data kalshi pass`.
- **`TickOutcome`** (`pass_contract.py`) and **`EXIT_BY_OUTCOME`** (`tick_exit.py`).
- **`tick_request.download_deadline`**, and the unit states in `data/tick/constants.py`.
- **`scripts/cutover_common.py`:** install, hold and release timers, journal reads, `fire_unit`. Its `migrate()` is hard-wired to the Kalshi track. This slice generalises it to take the track.

## Architecture

### Component Structure

```
mt-run tick ─► systemctl start mt-tick-pass.service   (manta-acquisition.slice, oneshot)
                 └─ mt data tick run
                      ├─ recorder.open(PassKind.TICK)          → pass_runs (primary DB)
                      ├─ acquisition pass  (lock 220_000_001)   → tick DB + /data/tick-archive
                      ├─ ingest pass       (lock 220_000_002)   → tick DB
                      └─ recorder.close(worst outcome, exit)    → pass_runs

mt data health (hourly) ─► primary checks (unchanged)
                         └─ gather_tick(settings) via tick_composed.read_line
                              ├─ "tick database"   OK / not configured / unreachable / migrations pending
                              └─ "tick retention"  in-flight units with deadline < now + TICK_RETENTION_WARN_DAYS
```

`run` lives beside the other tick verbs (`cli/commands/tick_store_cmds.py` or a sibling module, kept under 300 lines). It calls the existing pass entry points and copies no pass logic.

### Data Flow

1. `mt-run tick` → the unit runs `mt data tick run`, with environment from `/etc/manta-trading.env` as `manta-trading`.
2. `run` opens a `TICK` row. Phase progress goes through `recorder.progress`: the acquisition phases (`reconcile`, `availability`, `purchase`, `await`, `definitions`), then `ingest`.

   **No per-batch heartbeat.** 225 handed this slice a per-batch progress heartbeat, "because its systemd timeouts are what the heartbeat serves". This design declines it:
   - The unit sets `TimeoutStartSec=infinity` and no `WatchdogSec` (TD2), so no systemd timeout consumes a heartbeat.
   - 226 measured the slowest unit at 3.9 s, so phase-level progress plus the journal's per-unit lines already keep a run observable.

   The architecture's "progress stays observable during a multi-gigabyte ingest" is met by phase progress. If a watchdog is ever adopted, the heartbeat comes with it.
3. The acquisition pass runs to its own outcome. **Ingest always follows.** Ingest loads only *verified* units, and a failed purchase does not make a verified unit unsafe to load. A preflight or tick-database failure makes ingest fail fast with the same cause.
4. The combined outcome is the worse of the two, by a severity order defined once next to `EXIT_BY_OUTCOME`:

   `STORAGE_ABORT` > `PROVIDER_ABORT` > `REFUSED` > `IN_FLIGHT` > `PARTIAL` > `OK`

   The exit code is `EXIT_BY_OUTCOME[combined]`. No exit code is added. The precedence is new: no earlier slice ordered two tick outcomes. It must agree with the tick exit-code vocabulary in `tick_exit.py` (225, used by 230's verbs). The order above follows severity in that vocabulary, and the fold's unit test asserts it against `EXIT_BY_OUTCOME`. The `pass_runs` outcome uses `PASS_RUN_OUTCOME_BY_TICK_OUTCOME`, with an exhaustiveness assert like Kalshi's:

   | Tick outcome | `pass_runs` outcome |
   |---|---|
   | `OK` | `COMPLETE` |
   | `PARTIAL`, `IN_FLIGHT`, `REFUSED` | `INCOMPLETE` |
   | `PROVIDER_ABORT` | `PROVIDER_UNAVAILABLE` |
   | `STORAGE_ABORT` | `FAILED` |

   The detail names both passes' outcomes, for example `acquire=refused ingest=ok`.

   An unexpected exception closes the row `FAILED` with the exception type, then re-raises.
5. The health check reads the tick database through `read_line`. It never opens its own pool, and it shares nothing with the primary connection. A tick failure changes only the tick rows.

### State Management
- **`pass_runs`** (primary DB, minute track) holds a `TICK` row per `run`. This is the only new state.
- **The manifest and ledger** stay the record of what was bought and loaded. If the primary DB is down, a run loses its `pass_runs` row and nothing else (architecture, "Operational state has one home").
- **The archive directory** is shared by two writers: `manta-trading` (the unit, and `mt-run data tick …`) and `manta` (by hand from the checkout). `manta` is already in group `manta-trading`. The setgid group bit plus `UMask=0002` keep new files writable by both users.

## Technical Decisions

### TD1 — Manual-only cadence, stated in code
- **The schedule.** `FiringSchedule.manual()` returns a schedule with no times. `is_manual` is true for it, and `describe()` returns `manual (no timer)`.
- **Callers.** `next_firing_at` keeps raising on a schedule with no times. Every caller checks `is_manual` first:
  - The overview's `_pass_line` stops catching `RuntimeError` for this purpose.
  - `render_next_firings` prints `tick manual`.
- **Tests.** The three `test_firing_schedule.py` tests that iterate `PassKind` assert that a firing exists only for kinds that are not manual. A new drift guard asserts that `schedule_for(kind).is_manual` holds if and only if `deploy/systemd/mt-{kind}-pass.timer` is absent, for every pass kind that has a unit.
- **Rejected:** an empty schedule that callers catch as an error. That is the "omission" the architecture forbids.

### TD2 — One verb, one unit, one row
- **A deliberate extension of the plan.** The plan entry names one pass unit running the existing passes. The architecture's pass form is "`mt` command → `mt-{source}-pass.service`". The tick work is two commands (`pass`, `ingest`), so one unit needs one command that runs both. This slice adds the verb `mt data tick run`, plus the outcome fold and `PASS_RUN_OUTCOME_BY_TICK_OUTCOME` (Data Flow, step 4). It adds nothing else: no options and no `--json` shape.
- **Why one unit.** The plan names one unit and one `mt-run` kind, and the operator's question is "did tick run". So `mt-tick-pass.service` runs `mt data tick run`.
- **Rejected: two `ExecStart=` lines.** systemd stops at the first non-zero exit, so a refused purchase would skip ingest. It would also leave two `pass_runs` rows or none.
- **Rejected: a second unit for ingest.** It doubles the `mt-run` surface for no operator benefit.
- **Timeouts, as on the other pass units.**
  - `TimeoutStartSec=infinity`: a run can wait up to `TICK_WAIT_BUDGET_SECONDS` (1800 s) in acquisition and then ingest multi-gigabyte units. The 90 s default would kill it mid-run.
  - No `TimeoutStopSec` override: the pass has no clean-stop protocol.
- **A killed run (SIGTERM, SIGKILL, OOM) loses nothing.**
  - Ingest commits a unit's rows, ledger and *ingested* transition in one transaction, so a killed unit stays *verified* and reloads next run.
  - Acquisition advances the manifest by compare-and-set per transition. The next run's reconcile phase resumes submitted jobs from the account's job list and never re-buys them.
  - `SUBMITTED` or `DELIVERED` units left behind are what the `tick retention` health check watches (TD5).
- **The orphaned `TICK` row** is closed by 922's existing abandoned-run closer, when the next `run` calls `recorder.open(PassKind.TICK)`: this host's rows whose pid is gone close as abandoned. It uses no threshold, so tick needs none. Until then the overview shows the row open, and `mt-run status` shows the unit's real systemd result.

### TD3 — Settings enter production by a one-time root script
- **What it adds.** `deploy/add-tick-env.sh --from <checkout .env>` adds these keys to `/etc/manta-trading.env`:
  - `MT_TICK_DB_URL`
  - `MT_DATABENTO_API_KEY`
  - `MT_TICK_SPEND_CEILING_USD`
  - `MT_TICK_SPEND_30D_CEILING_USD`
  - `MT_TICK_ARCHIVE_DIR`
- **Why five keys, not three.** The plan entry names three. The pass refuses to buy without the 30-day ceiling, and the preflight refuses to run without the archive directory, so five are needed.
- **How it works.**
  - Check-then-act: it refuses if any of the five is missing from the source, and it never copies `MT_TICK_MAINTENANCE_URL` (913 credential separation).
  - It calls `env_add_keys`, so a present key is never changed and values are never printed. It then re-asserts `0640 root:manta-trading`, because `env_add_keys` leaves the file at 0600.
  - It logs to a file.
- **Values.** The ceilings and the tick credential are the dev `.env`'s. Both sides use the same account, the same tick database and the same manifest, so the rolling 30-day cap is one sum whichever side buys.
- **The PM step.** Writing `/etc` needs root, and no sudo rule covers it. The narrow grant has no general env writer, and a sudo rule for a script that runs once is not worth adding. So this is the slice's single PM host act: one command, run before the cutover. The cutover verifies the five keys by name (`MT_READ`) and stops, naming that command, if any is absent.

### TD4 — The cutover script owns verification
`scripts/cutover_234_tick_wiring.py` follows the 227 step pattern (`STEPS` of title, expected, action; it stops at the first failure; it writes its report to `user/notes/<date>-234-cutover.md`; it exits 0 only if every step passes).

**Acquisition timers.** The script holds them and waits out any running pass. It releases them in `finally`.

**Steps:**
1. **Preconditions.** Runs from the checkout root as `manta`. The ref exists locally and on origin. The five env keys are present by name.
2. **Archive directory.**
   - Action, run as `manta`, the owner, so no root is needed: `chgrp -R manta-trading`, `chmod -R g+w`, and `g+s` on directories.
   - Check: every entry has group `manta-trading` and is group-writable.
3. **Migrate.** Runs minute-track `060_pass_kind_tick`, after `verify_migrate_target` matches the checkout and production targets. Check: `pass_runs_pass_check` accepts `tick`.
4. **Install** the ref (installer, `daemon-reload`). Check: `/opt` HEAD and `mt --version`.
5. **Restart `mt-serve` if active.** Check: `/health` reports the tick pool reachable.
6. **Connection budget.**
   - Read `SHOW max_connections` and `superuser_reserved_connections` on both clusters.
   - Read the `mt-serve` worker count from the installed unit's `ExecStart`. With no `--workers` it is 1.
   - Required connections on the tick cluster: workers × 2 × `TICK_API_POOL_MAX`, plus 1 + `TICK_INGEST_WORKERS` for the pass.
   - Required connections on the primary: workers × (existing API pools + 231's 4), plus the pass's 1.
   - Check: each required total ≤ `max_connections` − reserved − the live `pg_stat_activity` count from roles other than the API's and the pass's.
   - The arithmetic is a pure function in the script, with a unit test. It reads the pool sizes from the `src` constants, not literals.
7. **Purchase guard.** Run `mt-run data tick pass --estimate-only --json`. Check: nothing wanted, cost 0. If anything is wanted, stop and report it: a cutover never buys.
8. **Supervised firing.** Run `fire_unit("mt-tick-pass.service", ["tick"])`.
   - While the unit is active, read the slice cgroup: `memory.max` must equal the declared value parsed from the installed `manta-acquisition.slice` (issue #33). Reading while a member runs guarantees the slice cgroup exists.
   - Check: exit 0, the newest `TICK` `pass_runs` row is `COMPLETE`, and the journal has a `memory peak` line.
   - After the firing: `memory.events` `oom_kill` is 0.
9. **Surfaces.**
   - `mt-run status` has a `tick` row.
   - `mt-run data overview` shows a `tick` pass line and `tick manual`.
   - `mt-run data health` shows `tick database` OK and `tick retention` OK.
**Not in the cutover: re-deriving the bound with tick included.** The plan entry asks only to install 233's bound and verify it is in force. Re-deriving it from the tick unit's own peak is deferred to issue #34. It needs a representative peak, from a run that ingests real units, and the cutover's no-purchase firing does not give one. The 14G interim is safe:
- Declared 14G against a 6.93 GiB sum leaves about 7 GiB of headroom.
- 226 measured an ingest worker's peak RSS rise at 656 MiB (32 MiB budget) and 1025 MiB (128 MiB budget).
- Tick runs only when an operator starts it.

### TD5 — Retention finding with a manual pass
- **Window.** With no next firing, the window is a fixed `TICK_RETENTION_WARN_DAYS = 7` in `data/tick/constants.py`. A week leaves an operator who looks weekly one full look to act before the deadline.
- **Query.** One new read in `manifest_request_reads.py`, `units_near_deadline(conn, now, window)`. It selects units in `SUBMITTED` or `DELIVERED`, not `PROVIDER_HOLE`, with `reopened_at IS NULL` and `download_deadline < now + window`, including deadlines already past.
- **Output.** The check fails and names each job id and deadline, with the action: `mt-run tick`.
- **When a timer arrives.** The window becomes "before the next firing" in that same `schedule_for` change. The check reads `schedule_for(TICK)` and uses the constant only when it `is_manual`.

### TD6 — Health semantics for an absent or unreachable tick database
- **Each `TickLineState` other than OK is a failing `tick database` row**, with `unavailable_text` as its detail. That makes health exit 1, the "loud" line.
- **`tick retention` is not emitted** when the database row failed: it would only repeat the cause.
- **Primary checks still run and print.** A primary outage keeps its exit 2 ("could not run") exactly as today, and the tick checks run first, so they are still printed.
- **Why "not configured" fails:** after this slice every production process has the setting, so its absence is a regression to report, not an expected state.

### Patterns and Conventions
- **Constants:** `TICK_RETENTION_WARN_DAYS`, the outcome severity order, and `PASS_RUN_OUTCOME_BY_TICK_OUTCOME` are each defined once.
- **Kinds:** `mt-run` names come from `KINDS`, and the unit name is `mt-tick-pass.service` per `unit_for`.
- **The migration** follows `kalshi_007`: `DROP CONSTRAINT IF EXISTS`, then `ADD CONSTRAINT … CHECK ({_pass_kind_check_sql()})`. It is a no-op in effect on a fresh database and required on production, whose constraint dates from `055`.
- **Exceptions** follow the project rule. `run` re-raises after closing its row.

### Realtime paths check
Nothing here forecloses either path:
- **Path A, assemble from realtime:** a future streaming unit is `Type=simple` under its own name. `PassKind.TICK` stays the historical pass.
- **Path B, historical with delay:** it needs a timer, which is the TD1 extension point.

### Does this belong in the API?
- **Served:** the `pass` enum on `/api/v1/overview` gains `tick` automatically, and `openapi.json` is regenerated (the schema test enforces it).
- **Not served:** the retention finding, because the API has no health-findings endpoint for any source. Its `/health` already carries `tick_db` from 231. A findings endpoint would be its own API slice, not a tick-only addition.

### Architecture statements superseded
These go in the architecture's Revision Log, with the Cadence text updated to match (Development Approach, step 7):
1. **Cadence: "no `schedule_for` branch, no `PassKind.TICK` firing calendar".** `PassKind.TICK` and its `schedule_for` branch land, and that branch returns an explicit manual schedule (TD1). The same paragraph's requirement of an explicit "no timer" answer is what holds.
2. **The multi-source surfaces print `tick: not configured` "and carry on".** In the health check, an absent or unreachable tick database is a failing row, so health exits 1. The other checks still run and print (TD6).
3. **Escalation: "deadline before the next scheduled firing".** With a manual pass, the window is a fixed `TICK_RETENTION_WARN_DAYS`. The firing-based test returns when a timer exists (TD5).
4. **Plan, "only progress returns to the loop" / the heartbeat handed over by 225.** Progress is phase-level, and the per-batch heartbeat is declined (Data Flow, step 2).

## Implementation Details

### Database / Storage Schema
- **Primary DB, minute track:** `060_pass_kind_tick` re-renders `pass_runs_pass_check`. No tick-track migration.
- **Seeded rows:** the load test `test_922_overview_nfr.py` seeds a row for every kind, so after `060` the test cluster accepts `tick`.

### API Contracts
- `docs/api/openapi.json`: the `PassKind` enum adds `"tick"`. Regenerate with `scripts/dump_openapi.py`, and pass `scripts/check_api_docs.py`.

### CLI Contract: `mt data tick run`
- **Options:** none. It always runs the full universe.
- **Narrowing:** `--start/--end` stay on `pass` for by-hand narrowing. A unit never narrows.
- **Exit codes:** `tick_exit.py`'s, using the combined outcome.

## Integration Points

### Provides to Other Slices
- **The realtime initiative:** a production tick kind, and the TD1 seam for a future timer.
- **Future Work item 2 (open interest):** the unattended form its `statistics` units would run under.
- **Every future slice-member unit:** a working `memory.max` check, and runbook 100's merged "Adding a source or pass" checklist. That checklist now includes `Slice=`, the env keys, the sudoers line, and the `PassKind`/`schedule_for` branch.

### Consumes from Other Slices
- **230's line-state vocabulary.** A change there changes health's tick text and nothing else.
- **231's pool sizes,** read from constants. If 231's budget changes, the cutover check follows it with no edit.
- **233's bound script.** It exits 2 until tick has a journal peak. This slice does not run it: re-deriving with tick is issue #34.

## Success Criteria

### Functional Requirements
1. **The production run:**
   - `sudo mt-run tick` runs `mt data tick run` in `manta-acquisition.slice` as `manta-trading`, and exits with the combined outcome's code.
   - `mt-run follow tick` tails it.
   - `mt-run status` shows a `tick` row.
2. **The `pass_runs` row:**
   - A `run` writes one `TICK` row.
   - Its phases appear in `mt data overview` while it runs.
   - It closes with the mapped outcome and an `acquire=… ingest=…` detail.
3. **Ingest runs after any acquisition outcome.** A test covers `REFUSED`, `PROVIDER_ABORT` and `IN_FLIGHT`.
4. **Overview:** `mt data overview` and `/api/v1/overview` list `tick`, and its next firing reads `manual`.
5. **The `tick database` health row:**
   - FAIL with "not configured (MT_TICK_DB_URL unset)" when the setting is absent.
   - FAIL with "unreachable: …" when the database is down.
   - FAIL when migrations are pending.
   - The minute, daily and Kalshi rows still print in each case.
6. **The `tick retention` health row** fails for a unit with `download_deadline` within 7 days or already past. It passes when none qualifies, and holed or reopened units never count.
7. **`add-tick-env.sh`:**
   - Adds only absent keys and never prints values.
   - Refuses a source missing any of the five keys.
   - Never copies `MT_TICK_MAINTENANCE_URL`.
   - Leaves the file `0640 root:manta-trading`.

### Technical Requirements
1. **`060_pass_kind_tick`:** applies on a database created at `055` and makes it accept `tick`. A migration test covers this.
2. **Firing-schedule tests and the drift guard (TD1):** they pass, and no `mt-tick-pass.timer` exists.
3. **Deploy tests (`test_units.py`)** cover:
   - the tick service: `ExecStart`, `Slice=`, oneshot, `UMask=0002`, `TimeoutStartSec=infinity`, no `[Install]`;
   - its place in `UNITS`;
   - `tick` in `KINDS`;
   - `test_members_from_real_unit_files` including `mt-tick-pass.service`.
4. **Unit tests** cover:
   - the outcome-severity fold and `PASS_RUN_OUTCOME_BY_TICK_OUTCOME` exhaustiveness;
   - the cutover's connection-budget function, at 1 worker (fits) and 4 workers (fails on the tick cluster: 32 + 3 > 30).
5. **`openapi.json`** is regenerated, and the schema test passes.
6. **Ruff, mypy and shellcheck are clean** on touched files. The full unit and integration tiers pass, apart from known pre-existing failures.
7. **Runbook 100** has:
   - the tick unit row;
   - one merged "Adding a source or pass" checklist;
   - the five tick keys under "Environment file";
   - "Running passes by hand" pointing to `mt-run tick` / `mt-run data tick …`.
8. **The standing 220 obligations are done:** the Kalshi contract diff and the data-correctness slice-mapping rows.

### Integration Requirements
1. **The cutover report** shows every step PASS, including:
   - `memory.max` equal to the declared `MemoryMax`, with `oom_kill` 0 (closes issue #33);
   - both clusters within their connection budget;
   - one `COMPLETE` `TICK` row from a firing that bought nothing.
2. **`mt-serve`** serves `/api/v1/futures/*` in production after the cutover.

### Verification Walkthrough

Draft; refined after implementation. The commands run on manta9000.

**0. PM, once:** add the tick settings to the service environment.
```
sudo /home/manta/source/repos/manta/trading-data/deploy/add-tick-env.sh \
     --from /home/manta/source/repos/manta/trading-data/.env
```
Expected: a count of keys added (5 on first run, 0 after), and no values printed.

**1. Agent: run the cutover** from the release ref.
```
uv run python scripts/cutover_234_tick_wiring.py <ref>
```
Expected: exit 0, and `project-documents/user/notes/<date>-234-cutover.md` shows every step PASS:
- `memory.max` is 15032385536 bytes for 14G;
- `oom_kill` is 0;
- the connection budget fits on the tick and primary clusters;
- estimate-only found nothing to buy;
- the `TICK` row is COMPLETE.

**2. Run tick by hand**, the way the operator will from now on.
```
sudo mt-run tick
```
Expected: the journal streams the acquisition phases, then ingest, and the run exits 0. With nothing wanted and nothing pending, both passes report zero units.

**3. See it from the operator surfaces.**
```
mt-run status            # a "tick" row: last run Result=success, exit=0
mt-run data overview     # pass line "tick … complete", next line includes "tick manual"
mt-run data health       # "OK   tick database" and "OK   tick retention"
curl -s localhost:8000/api/v1/overview | jq '.passes[] | select(.pass=="tick")'
```
The JSON path and port are confirmed against the installed `mt-serve` at implementation.

**4. Prove the failure lines** in the dev checkout, not production.
```
env -u MT_TICK_DB_URL uv run mt data health
```
Expected: `FAIL tick database: not configured (MT_TICK_DB_URL unset)`, the minute, daily and Kalshi rows still printed, and exit 1. The unreachable case is covered by an integration test that points the URL at a closed port.

**5. Prove the retention finding** in an integration test, not production. Seed a `SUBMITTED` unit with a deadline 3 days out. Health reports `FAIL tick retention: GLBX-… deadline <date> (run: mt-run tick)`.

## Risk Assessment

### Technical Risks
- **A purchase during cutover.** The supervised firing runs the whole universe with production spend ceilings set.
- **The archive's ownership change racing a by-hand run.** A `manta` run writing during step 2 could leave a file outside the group.

### Mitigation Strategies
- **Purchase:** step 7's estimate-only guard stops the cutover before the firing if anything is wanted. The spend ceilings remain the second guard.
- **Ownership race:** tick has no timer, so the only writers during the cutover are runs the agent starts itself. Step 2's check covers every entry, so a race shows as a failed step, never as a silent gap.

## Implementation Notes

### Development Approach
Suggested order. Commits are checkpointed per section.
1. `PassKind.TICK`, `FiringSchedule.manual()`, the `schedule_for` branch, the callers (overview, renderer), and the schedule tests with the drift guard.
2. Migration `060`, its test, and the regenerated `openapi.json`.
3. `mt data tick run`: the severity fold, the outcome map, and recorder wiring. Tests use mocked passes, plus one integration run on the test cluster.
4. Health: `units_near_deadline`, `gather_tick`, and the TD6 semantics.
5. Deploy: the unit file, `UNITS`, `KINDS`, `add-tick-env.sh`, and deploy tests.
6. The cutover script, with the generalised `cutover_common.migrate(track, id)` and the connection-budget function with tests.
7. Runbook 100, the Kalshi contract diff, and the data-correctness mapping rows. Also the architecture's Revision Log entry and its Cadence text, per "Architecture statements superseded".
8. Cutover execution and the slice closeout.

Effort 3/5. The cutover sequencing is the main work.

### Special Considerations
- **Open interest (Future Work item 2) — question for the PM.** The plan says that if no Standard-plan subscription date is set when 234 is designed, 234's design asks again. No date is recorded, so the question is asked here:
  - Is a subscription date set?
  - If not, does open interest still wait for the first subscription month?
- **Security:**
  - Values are never printed by `add-tick-env.sh` or the cutover report, which checks keys by name only.
  - `MT_TICK_MAINTENANCE_URL` never enters the service environment.
  - The `mt-serve` restart is the only service restart, and production PostgreSQL is never restarted.
- **By-hand runs from the checkout** keep working: `manta` is in group `manta-trading`. Runbook 100 nonetheless names `mt-run` as the production path. To adopt a source held under `/home`, stage it under `/data` first, because `manta-trading` cannot read `/home/manta`.
