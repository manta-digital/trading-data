---
docType: slice-design
slice: acquisition-memory-bound-kept-current
project: trading-data
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [233-slice.cross-source-acquisition-arbitration, 234-slice.tick-pass-production-wiring]
interfaces: []
dateCreated: 20261015
dateUpdated: 20261015
status: not_started
---

# Slice Design: Acquisition Memory Bound Kept Current

## Overview

Slice 233 put `MemoryMax=14G` on `manta-acquisition.slice`, the systemd slice
that groups the minute, daily, Kalshi and tick passes. Its rule is
`ceil_GiB(2 × Σ each member's 30-day peak)`. Today a person applies that rule:
they run `scripts/acquisition_memory_bound.py`, which reads peaks from the
journal, and copy the result into `deploy/systemd/manta-acquisition.slice`.
The tick unit joined the slice in 234 with no representative peak, so the
value in force does not account for it (issue #34).

This slice takes the person out of that loop:

1. **Every pass records its own peak.** At close, the pass-run recorder reads
   the `memory.peak` of the process's own systemd service cgroup and writes
   it, with the unit name, to the run's `pass_runs` row.
2. **Health checks the bound.** `mt data health` gains an
   `acquisition memory bound` check. It derives the bound from `pass_runs` and
   compares it with the value systemd has loaded for the slice.
3. **The install writes the bound.** `deploy/install-production.sh` re-derives
   the bound from `pass_runs` and writes it to a systemd drop-in for the slice.
   Every release installs the current value with no hand step.

## Value

- **No hand step.** The bound changes when the passes' measured memory
  changes, not when someone remembers to run a script. This applies the
  standing rule that a deferred re-derive becomes a health check plus an
  automatic re-derivation.
- **A too-tight bound is caught.** A slice bound below what the passes
  actually use means the slice's OOM killer will kill a healthy pass. Today
  nothing watches for that. After this slice the hourly health timer fails
  when it happens.
- **The source works for the service user.** The journal is readable by the
  operator, not by `manta-trading`. The database and the cgroup file are
  readable by both, so the health unit and the installer can derive the bound.
- **Pass memory is visible.** `/api/v1/overview` shows each pass's process
  memory peak at its last close, which today exists only in the journal.

## Technical Scope

**Included**

1. Migration `061_pass_runs_memory_peak` on the minute track (primary
   database): two nullable columns on `pass_runs`, `memory_peak_bytes` and
   `systemd_unit`.
2. A cgroup reader, `manta_trading/data/acquisition/cgroup_peak.py`: this
   process's service unit name and its `memory.peak`.
3. `PassRunRecorder.close` and `PassRunRepository.close` record the peak.
   Every pass kind gets it, because all six close through the same recorder.
4. The rule module, `manta_trading/data/acquisition/memory_bound.py`. It holds
   the headroom and window constants, member discovery from installed unit
   files, the derivation from `pass_runs`, and the loaded-value read. It is
   the only place the rule exists.
5. `scripts/acquisition_memory_bound.py` is rewritten as a thin entry point
   over the module. It reads `pass_runs` instead of the journal and prints
   `MemoryMax=<N>G` as its only stdout line.
6. The health check `acquisition memory bound`, in
   `cli/commands/health_memory_bound.py`.
7. `deploy/lib/memory_bound_dropin.sh`, sourced by `install-production.sh`:
   it runs the derivation as the service user and writes the drop-in.
8. `last_run.memory_peak_bytes` on `/api/v1/overview`.
9. `scripts/cutover_235_memory_bound.py`. A pending migration makes
   `upgrade_production.py` refuse, so this release needs its own cutover.
   It is a step list over `cutover_common` (preconditions, timer hold,
   `migrate`, `install`, report). The journal seed (TD7), the member firing
   and the checks in the walkthrough are new.
10. Documentation updates: runbook 100's "Adding a source or pass" (the
    hand re-derive step that 233 added is removed), the slice unit's comment,
    the CHANGELOG, a dated slice-235 entry in the 220 architecture document,
    and the 220 slice plan's entry 16 (see "Architecture statements
    superseded"). The plan entry also moves from Effort 2/5 to 3/5, which is
    what the migration, the installer step and the cutover add up to.

**Excluded**

- **Re-deriving between releases.** Writing the drop-in needs root. The
  health unit runs as `manta-trading`. Between releases a drift shows as a
  health FAIL, and any re-run of the installer fixes it.
- **Backfilling peaks from the journal beyond the cutover's one-time 30-day
  seed (TD7).** After the seed, `pass_runs` is the only source.
- **A peak on `mt data overview`, text or `--json`.** The plan entry places
  it on the API only.
- **Recording the peak of a pass the OOM killer kills.** Such a run never
  reaches close. Health FAILs on it instead (TD4), and Special
  Considerations covers the rest.
- **Serving the bound check over the API.** No source has a health-findings
  endpoint.

## Dependencies

### Prerequisites

- **233 (complete):** the rule, `MemoryMax=14G` on the slice, and the journal
  version of `scripts/acquisition_memory_bound.py` with its tests.
- **234 (complete):** `mt-tick-pass.service` in the slice, migration 060
  (the last minute-track migration), `PassKind.TICK`, the `CheckSeverity`
  split in health, and the cutover script pattern
  (`cutover_234_tick_wiring.py`, `cutover_common.py`).
- **Host:** cgroup v2 with `memory.peak` (kernel 6.14 on manta9000; the file
  exists since 5.19). `systemd-run` on the host, used by the installer.

### Interfaces Required

- `PassRunRecorder` / `PassRunRepository`
  (`data/acquisition/daemon/pass_run_recorder.py`,
  `data/acquisition/pass_runs.py`): the single close path for every pass.
- `HealthCheck`, `CheckSeverity` and `gather` in `cli/commands/health.py`.
- `LastRun` (`cli/overview_types.py`), `_last_run`
  (`cli/commands/overview.py`) and `LastRunRecord`
  (`api_server/models/operations.py`).
- `install()`, `migrate()`, `hold_timers` / `release_timers` and the report
  helpers in `scripts/cutover_common.py`.

## Architecture

### Component Structure

```
data/acquisition/cgroup_peak.py        own unit + memory.peak (pure parse + one read)
data/acquisition/pass_runs.py          close() writes memory_peak_bytes, systemd_unit
data/acquisition/daemon/pass_run_recorder.py   close() reads the peak, never raises
data/acquisition/memory_bound.py       the rule: constants, members, derive, loaded value, drop-in text
market/schema/migrations/minute.py     061_pass_runs_memory_peak
cli/commands/health_memory_bound.py    the health row
scripts/acquisition_memory_bound.py    CLI over memory_bound: report on stderr, MemoryMax=<N>G on stdout
deploy/lib/memory_bound_dropin.sh      install step: derive as service user, write drop-in
deploy/install-production.sh           sources the lib, calls it before daemon-reload
deploy/systemd/manta-acquisition.slice comment rewritten; MemoryMax=14G stays as the bootstrap value
scripts/cutover_235_memory_bound.py    migrate, install, prove recording, report
```

### Data Flow

```
pass process (in mt-*-pass.service cgroup)
  └─ recorder.close(run_id, …)
       ├─ /proc/self/cgroup  → "0::/manta.slice/manta-acquisition.slice/mt-kalshi-pass.service"
       ├─ /sys/fs/cgroup/<path>/memory.peak → bytes
       └─ UPDATE pass_runs SET …, memory_peak_bytes, systemd_unit

/etc/systemd/system/*.service with Slice=manta-acquisition.slice ──► members
pass_runs: max(memory_peak_bytes) per systemd_unit, ended_at in last 30 d (else newest) ──► peaks
memory_bound.derive(members, peaks) ──► ceil_GiB(2 × Σ peaks)  | MemoryBoundError(never-measured member)

        ┌─ health:   systemctl show manta-acquisition.slice → LoadState, MemoryMax
        │            FAIL unless loaded, finite and ≥ derived
derive ─┤
        └─ install:  systemd-run --uid=manta-trading … acquisition_memory_bound.py
                     exit 0 → write manta-acquisition.slice.d/memory-bound.conf → daemon-reload
                     exit 2 → loud "NOT RE-DERIVED: <reason>", value in force unchanged
```

### State Management

- **`pass_runs`** gains the process peak at close. It is written once, at close, by the
  process that ran the pass, which keeps 922's rule that rows are written by
  the pass and never by an observer.
- **The drop-in** `/etc/systemd/system/manta-acquisition.slice.d/memory-bound.conf`
  holds the derived value and a comment with its inputs and date. Only the
  installer writes it.
- **The repo's slice file** keeps `MemoryMax=14G` as the bootstrap value. It
  is in force only on a host where no derivation has succeeded yet. The
  drop-in overrides it once one has. Health judges whichever value systemd
  has loaded, so a stale bootstrap value cannot hide.

## Technical Decisions

### TD1 — The pass measures itself: own cgroup `memory.peak` at close

The recorder reads `/proc/self/cgroup`. On cgroup v2 this is one line,
`0::<path>`, and the parser takes the path after the last `::` on the `0::`
line. When the path's last component ends in `.service`, that component is
the unit name. The peak is `/sys/fs/cgroup<path>/memory.peak`, parsed as an
integer.

- **Same counter as 233's journal value.** systemd's `Consumed … memory peak`
  line is this same cgroup's `memory.peak`, read at unit stop. The service
  cgroup is created for each start of a oneshot, so the counter covers this
  run alone. It also covers worker subprocesses (tick ingest workers),
  which an RSS figure would miss.
- **Read at close, not at exit.** The close is the last thing a pass writes,
  and teardown after it allocates nothing of note.
- **No slice logic in the recorder.** Any row closed under a service gets a
  peak; the derivation selects members by unit name (TD2).
- **Outside a service there is no peak.** A pass run from a shell is in a
  `session-N.scope` or `user@….service/app.slice` cgroup. That counter
  covers unrelated processes, so it is not recorded. Both columns stay
  NULL and the recorder logs one WARNING:
  `pass_runs: not in a systemd service cgroup (<path>); no memory peak
  recorded`. NULL here means "not measured", and the derivation treats a
  member that was never measured as a failure (TD3). Nothing stands in for
  it.
- **Recording never aborts a pass** (922's recorder contract). An `OSError`
  or `ValueError` from the read is logged with `logger.exception`, and the
  row closes with NULL peak columns.
- **Recording one run, not daemon cycles.** The minute and daily daemons run
  `--stop-when-done` under their units, and each cycle closes its own row.
  The cgroup counter is the process's peak so far. For the maximum over a
  window, this gives the same answer.

The reader takes the two paths as parameters, defaulting to `/proc/self/cgroup`
and `/sys/fs/cgroup`, so tests can point it at fixture files. Those fixtures
hold the real line from a production unit and from an interactive shell on
manta9000.

### TD2 — Members come from installed unit files; peaks join by unit name

Members are the `*.service` files in `/etc/systemd/system` that declare
`Slice=manta-acquisition.slice`. 233's regex is kept and moved into the
module. 233 read `deploy/systemd` in the checkout. The installed directory
is the truth of the host, and it does not depend on how the package was
installed. The installer copies the release's units there before it derives
(TD5), so at install time it holds the new release's members.

A row's `systemd_unit` is the key. The alternative is a `PassKind`-to-unit
table in code, which would be a second list of members to keep in step with
the unit files. The column costs one nullable field and makes members come
from one place. A unit that joins the slice in a later release is a member
as soon as its file is installed, and it has a peak as soon as it runs once.

The installer copies units in and never removes them. A unit retired or
renamed out of the slice stays on disk as a never-measured member. The FAIL
names it, and the operator removes the file; the release that retires a unit
lists that removal in its cutover.

`SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")` is defined once in the
module. It matches `UNIT_DIR` in `install-production.sh`, the same
cross-language pairing `cutover_common.py` already documents.

### TD3 — The rule lives in one module

`memory_bound.py` owns:

| Name | Value | Meaning |
|---|---|---|
| `MEMORY_BOUND_HEADROOM` | `2` | Multiplier over the summed peaks (233 Decision 2) |
| `MEMORY_PEAK_WINDOW_DAYS` | `30` | Window on `pass_runs.ended_at`. It replaces `JOURNAL_WINDOW_DAYS` |
| `ACQUISITION_SLICE` | `"manta-acquisition.slice"` | The slice bounded |
| `SYSTEMCTL_SHOW_TIMEOUT_SECONDS` | `10` | Bound on the `systemctl show` call (TD4) |

Functions:

- `members(unit_dir)`: sorted unit names. An `OSError` reading the
  directory or a unit file is raised as `MemoryBoundError`, naming the path.
- `fetch_peaks(conn, units, now)`: one query that returns, per unit, the
  max peak over the window. For a unit with no run in the window it returns
  that unit's newest recorded peak of any age, with its date. Rows with a
  NULL peak are ignored; any outcome counts.
- `derive(members, peaks)`: returns `(peaks, gib)`. It raises
  `MemoryBoundError` naming every member that has never recorded a peak, not
  only the first: `mt-tick-pass.service: no memory peak ever recorded`. With
  no members it raises `no unit declares Slice=manta-acquisition.slice`.
- `loaded_memory_max(run)`: runs `systemctl show manta-acquisition.slice
  --property=LoadState --property=MemoryMax`. It parses `key=value` lines in
  any order and returns `LoadedBound(load_state, max_bytes | None)`, where
  `None` means `infinity`. It raises `MemoryBoundError` on a non-zero exit, a
  missing binary, or a timeout. The command runner is injected for tests.
- `oom_killed(members, run)`: one `systemctl show <member…>
  --property=Id --property=Result` call. It returns the members whose
  `Result` is `oom-kill`, with the same error handling as
  `loaded_memory_max`.

**Decision: a member idle for the window keeps its newest peak.** The 220
architecture assumes no tick cadence, so an idle month is the tick unit's
normal state. Under the plan entry's wording ("no recorded peak in the
window") health would fail hourly after every idle month. That alarm would
hide real regressions in the other checks. The newest peak of any age is that
member's last measured use, not a placeholder. It is reported with its date.
The failure that matters stays: a member that has never been measured fails
derivation, and that FAILs health until the first tick run (TD4). Timed
members always have a run in the window, so for them the rule is unchanged.
Any outcome counts toward the peak, because a run that ended FAILED still
used that memory.

### TD4 — Health judges the value systemd has loaded

The plan entry says "the live slice cgroup's `memory.max`". That cgroup
exists only while a member runs: an idle slice has no
`/sys/fs/cgroup/manta.slice/manta-acquisition.slice`. An hourly check would
usually find nothing. `systemctl show` returns the value systemd applies
when the slice starts, including drop-ins, and it re-applies that value to
an active slice on `daemon-reload`. 234 verified that this value matches
the kernel's `memory.max` (issue #33). The health unit can run
`systemctl show` unprivileged, under its existing hardening
(`ProtectSystem=full` affects writes only).

Verdicts, in this order. Every one is `CheckSeverity.VERDICT`:

| Condition | Result | Detail |
|---|---|---|
| `pass_runs` has no `memory_peak_bytes` (`psycopg.errors.UndefinedColumn`) | FAIL | `pass_runs has no memory_peak_bytes: migration 061 not applied` |
| Unit files unreadable (`MemoryBoundError` from `members`) | FAIL | the error's text, naming the path |
| A member has never recorded a peak | FAIL | the error's text, naming each such member |
| A member's last run was OOM-killed (`Result=oom-kill`) | FAIL | `mt-minute-pass.service was OOM-killed; its peak is unrecorded, so the bound cannot learn from it` |
| `systemctl show` exits non-zero, is missing, or exceeds `SYSTEMCTL_SHOW_TIMEOUT_SECONDS` | FAIL | `cannot read manta-acquisition.slice: <error>` |
| `LoadState` ≠ `loaded` | FAIL | `manta-acquisition.slice is <state>` |
| `MemoryMax` is `infinity` | FAIL | `manta-acquisition.slice has no MemoryMax` |
| loaded < derived | FAIL | `MemoryMax 14 GiB < derived 16 GiB (peaks: …); the next install writes 16G` |
| loaded ≥ derived | OK | `MemoryMax 14 GiB ≥ derived 14 GiB (4 members, 30-day peaks)` |

A loaded value above the derived one passes. That matches the plan entry,
which fails only when the bound is too tight. The check reads `pass_runs` on
health's existing primary connection inside `gather`, in its own
`conn.transaction()` so that a missing column fails this row and not the
checks after it. If the primary is down, health already exits 2. Any other
exception (a database error other than the missing column, an unexpected
`OSError`) propagates out of `gather` and health exits 2 with the traceback.
That is how every existing check behaves, because `gather` catches nothing.

The OOM-kill row exists because a killed run cannot fix itself. It never
reaches close, so its peak is never recorded, the derived bound stays where
it was, and the next run is killed the same way. Without this row, a bound
that is too tight would be invisible to the check meant to catch it.

The cutover seeds 30 days of peaks (TD7), so the check is green from the
cutover on. A member added later FAILs until its first run. An idle tick
month does not fail it (TD3).

**What the gate does not check.** Any tick run clears "never measured",
including a run with nothing to buy or ingest, whose peak (about 100 MB in
234's firing) says nothing about ingest memory. The design does not try to
tell the two apart. Issue #34 closes when the operator confirms that the tick
row behind the derived bound ingested units (its `progress_done` > 0 in the
`ingest` phase); the check alone does not close it. Until then a bound derived
from a small tick peak is no weaker than the 14G interim: the other members
sum to 6.93 GiB, so the bound is still at least 14G, and the 7 GiB of
headroom is several times 226's measured ingest-worker rise of 656–1025 MiB.

### TD5 — The install writes a drop-in; it never edits the shipped unit

`install-production.sh` step 5 copies the units. It then calls
`write_memory_bound_dropin` from `deploy/lib/memory_bound_dropin.sh`,
before step 6's `daemon-reload`. The function runs:

```
systemd-run --quiet --wait --pipe --collect \
  --uid=manta-trading --gid=manta-trading \
  -p EnvironmentFile=/etc/manta-trading.env -p WorkingDirectory=/opt/manta-trading \
  -p RuntimeMaxSec=${MEMORY_BOUND_DERIVE_TIMEOUT_SECONDS} \
  /opt/manta-trading/.venv/bin/python scripts/acquisition_memory_bound.py
```

`systemd-run` parses the environment file exactly as the pass units do, so
the derivation sees the same `MT_TIMESCALE_DB_URL` and the same database role.
`runuser` with the file sourced by bash was rejected: it is a different
parser for the same file, and quoting that systemd accepts can read
differently in a shell.
The script connects with `PASS_RUN_DB_CONNECT_TIMEOUT_SECONDS`.
`MEMORY_BOUND_DERIVE_TIMEOUT_SECONDS=120`, defined once in the lib, bounds the
whole run: a stalled query is killed by systemd, never left hanging the
installer. Exit codes:

- **0:** stdout must be exactly one line matching `^MemoryMax=[0-9]+G$`;
  anything else is treated as a bug (below). The function writes a temp file
  in `manta-acquisition.slice.d/` holding a one-line comment ("written by
  install-production.sh from pass_runs, slice 235; inputs are in the install
  log; do not edit"), `[Slice]` and that line. It moves the temp file over
  `memory-bound.conf` (mode 0644, root) and prints
  `memory bound: MemoryMax=<N>G written`. The script's report of members and
  peaks goes to stderr, which lands in the install log.
- **2** (the script's "cannot derive": a member never measured, an
  unreachable database, or a missing column): prints a banner on stderr,
  `MEMORY BOUND NOT RE-DERIVED: <reason>`, followed by the value that stays
  in force. The install continues. Failing the install here would block
  every release until a paid tick run. The health check keeps failing until
  the cause is fixed, so this path is loud, not silent.
- **Anything else**, including a timeout kill and malformed stdout, calls
  `die` with the output, before the reload, so the half-done install stays
  inert. A failed `mkdir`, temp write or `mv` also calls `die` at the same
  point; the old drop-in, if any, is untouched because the `mv` is the only
  step that replaces it. This is the installer's existing failure posture.

**Why a drop-in.** The shipped file stays identical to the repo, and the
installer keeps copying it unconditionally. The derived value has its own
file, and `systemctl cat manta-acquisition.slice` shows both. Rewriting the installed unit's `MemoryMax=` line was rejected: the
installed file would differ from the release it came from.

**Order with the migration.** A cutover migrates before it installs (234's
order), so the column exists when the derivation runs. A release with no
migration runs `upgrade_production.py`, which calls the same installer.

### TD6 — API: the peak goes on `last_run`

`LastRun` and `LastRunRecord` gain `memory_peak_bytes: int | None`: the
pass process's cgroup peak at close. For a `--stop-when-done` daemon that
closes several rows, that is the process's peak so far, not the last cycle's
alone (TD1). It is null when the run was not measured
(TD1) and for rows from before migration 061. Bytes, not GiB: the API carries
exact values and the UI formats them. This is an additive, nullable field,
so the patch version applies. `check_api_docs.py` and the published OpenAPI
schema update with it. `systemd_unit` is not served. It is the derivation's
join key, and the overview already keys lines by `pass`.

### TD7 — The cutover seeds 30 days of peaks from the journal

An empty window is not a neutral starting point. It is a wrong bound. The
minute pass peaks at about 6.1 GiB on Saturdays and about 100 MiB on every
other day (journal, 2026-09-15 to 10-15). With a few weekday runs recorded,
the derivation gives roughly `ceil_GiB(2 × (0.1 + 0.43 + 0.32 + 0.1)) = 2G`.
Any install before the next Saturday would write that value, the Saturday
minute pass would be OOM-killed, and the killed run would record nothing to
raise the bound (TD4). A day-one bound has to come from a full window.

The cutover reads the journal for the past `MEMORY_PEAK_WINDOW_DAYS` as the
operator, using 233's `Consumed … memory peak` parser, which moves into the
cutover module with its tests. Each invocation's peak is written to the
`pass_runs` rows of that unit's pass kind whose `ended_at` falls inside the
invocation, meaning after the previous stop line and at or before this one.
A `--stop-when-done` daemon's several rows all get its process peak, as TD1
records them. Rows that already have a peak are left alone, so a re-run
changes nothing.

- **The kind-to-unit map is cutover-only.** It lives in the cutover script,
  is used once, and TD2's single source of members is unaffected. Writing a
  column on rows the passes already wrote is a data fix, not an observer
  writing rows.
- **An invocation with no matching row** (for example a pass that died
  before opening its row) is reported and skipped.
- **The seed runs between `migrate` and `install`**, so the cutover's install
  derives from a full window and writes the drop-in at once.

The tick unit's journal peak (about 100 MB, from 234's firings) clears "never
measured". TD4's "What the gate does not check" still applies to it.

### Patterns and Conventions

- The pure rules (cgroup-line parse, `systemctl show` parse, derive, verdict)
  take values in and return values out. The I/O is three thin
  functions, matching health's existing style.
- Errors name the unit. `MemoryBoundError` is raised only for "cannot
  derive". Everything else propagates with its traceback.
- No bare string for the slice name, window or headroom outside
  `memory_bound.py`. The health row name is a module constant, like
  `health_tick.DATABASE_CHECK`.

### Realtime paths and the API (standing 220 checks)

- **Realtime:** a streaming unit (`Type=simple`) would close its row only
  when it stops. Its peak would then be recorded once per process life,
  which still bounds it. Nothing here forecloses that.
- **API:** the peak at close is served (TD6). The bound verdict is not, for
  the reason in the plan entry.

### Architecture statements superseded

- The 220 architecture's 2026-10-14 slice-233 entry ("derived from journal
  peaks by `scripts/acquisition_memory_bound.py`") and 233's design (hand
  copy, journal peaks): replaced by TD1, TD3 and TD5. The implementation
  adds a dated slice-235 entry to the architecture document saying so. The
  2 × Σ peaks, whole-GiB rule is unchanged; the idle-member case is TD3's.
- The 220 slice plan's entry 16 (this slice): the implementation rewrites it
  to match three deliberate deviations. Health reads the loaded value through
  `systemctl show`, not the live cgroup's `memory.max` (TD4). An idle member
  keeps its newest peak instead of failing for "no recorded peak in the
  window" (TD3). The bound goes into a drop-in, not the slice unit (TD5).
  The entry's effort becomes 3/5.

## Implementation Details

### Database / Storage Schema

Migration `061_pass_runs_memory_peak`, minute track, idempotent:

```sql
ALTER TABLE pass_runs
    ADD COLUMN IF NOT EXISTS memory_peak_bytes BIGINT,
    ADD COLUMN IF NOT EXISTS systemd_unit      TEXT;
```

No index. The derivation scans at most 30 days of rows for a handful of
units, a few thousand rows at most, and `idx_pass_runs_pass_started` already
exists. Existing rows stay NULL. `PassRun`, `_COLS` and `_row_to_pass_run`
gain both fields. The migration-chain test that pins the newest id
(`chain_ends_at_*`) is updated to 061.

### API Contracts

`GET /api/v1/overview` → `passes[].last_run`:

```json
{ "started_at": "…", "ended_at": "…", "outcome": "COMPLETE",
  "exit_code": 0, "detail": null, "memory_peak_bytes": 333545472 }
```

### Script contract: `scripts/acquisition_memory_bound.py`

| Invocation | Output | Exit |
|---|---|---|
| `uv run python scripts/acquisition_memory_bound.py` | stderr: members, their peaks (with dates for idle members), `sum … x 2`; stdout: `MemoryMax=<N>G` only | 0 / 2 cannot derive |

## Integration Points

### Provides to Other Slices

- A `memory_peak_bytes` (process peak at close) on every `pass_runs` row written under a
  service. A future pass or source gets it with no code change.
- A slice bound that follows the measured load. A future acquisition unit
  needs only `Slice=manta-acquisition.slice`. The runbook's add-a-pass
  procedure loses its re-derive step.
- `last_run.memory_peak_bytes` for the trading-ui overview.

### Consumes from Other Slices

- 233's rule and member regex (moved, unchanged).
- 234's tick unit as a member. The cutover seeds its peak (TD7).
- 922's recorder contract. If the database is unreachable at close, the peak
  is lost with the rest of the close, and the abandoned-run sweep later
  closes the row with NULL peak columns.

## Success Criteria

### Functional Requirements

1. A pass run under its systemd unit closes its `pass_runs` row with
   `systemd_unit` equal to the unit's name and `memory_peak_bytes > 0`. A pass
   run from a shell closes with both NULL and logs the WARNING in TD1.
2. A failure to read the cgroup files never changes a pass's outcome or exit
   code. It logs at ERROR and the row closes with NULL peak columns.
3. `scripts/acquisition_memory_bound.py` derives from `pass_runs`. It names
   every member that has never recorded a peak and exits 2. It no longer calls
   `journalctl`.
4. `mt data health` prints an `acquisition memory bound` row with the
   verdicts in TD4's table. A FAIL makes the command exit 1.
5. `install-production.sh` writes
   `/etc/systemd/system/manta-acquisition.slice.d/memory-bound.conf` when the
   derivation succeeds. When it exits 2, the installer prints the
   `MEMORY BOUND NOT RE-DERIVED` banner and completes. On any other exit,
   including a timeout or malformed output, the installer stops before
   `daemon-reload`.
6. `/api/v1/overview` carries `last_run.memory_peak_bytes`.

### Technical Requirements

1. Unit tests:
   - The cgroup parse uses the real `/proc/self/cgroup` lines captured from
     `mt-kalshi-pass.service` and from a login shell on manta9000, plus a
     cgroup v1 multi-line file, which yields no unit.
   - The `systemctl show` parse uses real output for the loaded slice, for a
     not-found unit, and for `MemoryMax=infinity`.
   - Members are discovered from copies of the real `deploy/systemd` units.
   - Derive covers an idle member using its newest peak and a never-measured
     member failing; the verdict covers each row in TD4's table, including
     the OOM-kill and unreadable-unit-dir rows.
   - The journal seed's matching (TD7) uses real `Consumed … memory peak`
     lines from manta9000, including a Saturday minute invocation. It covers
     a daemon invocation that closed several rows, an invocation with no
     row, and a re-run that changes nothing.
2. A unit test of `memory_bound_dropin.sh` with a stub deriver for exit 0,
   exit 0 with malformed stdout, exit 2 and exit 1, writing to a temp
   directory, in the style of
   `test/unit/test_setup_backup.py`.
3. Integration tests against the test cluster:
   - Migration 061 applies twice cleanly.
   - `PassRunRepository.close` writes both columns.
   - `fetch_peaks` honors the window, falls back to the newest peak for an
     idle unit, and ignores NULL rows.
   - The overview API returns the field.
4. `ruff check`, scoped `ruff format`, mypy (one invocation over the source
   and test paths) and `shellcheck` on the new lib and the installer: all
   clean.
5. Runbook 100, the slice file comment, the CHANGELOG and the 220
   architecture document are updated.
   Runbook 100's add-a-pass step no longer contains a hand re-derive.

### Integration Requirements

1. The existing health, overview and pass-run tests pass unchanged, apart
   from the added field and row.
2. A release installed after this slice ships its bound with no edit to
   `deploy/systemd/manta-acquisition.slice`.

### Verification Walkthrough

**Cutover** (one command, operator account; tag first)

```
uv run python scripts/cutover_235_memory_bound.py v<release>
```

Steps, each check-then-act, with a report in `project-documents/user/notes/`:

1. **Preconditions, hold timers, migrate 061** (234's pattern).
2. **Seed peaks (TD7):** the report lists, per member, the invocations
   matched and skipped and the max seeded peak. The minute max is within 10%
   of 233's journal figure (6348.8 MiB on 2026-10-14). All four members have
   a peak.
3. **Install:** the output shows `memory bound: MemoryMax=14G written`.
   `systemctl show manta-acquisition.slice -p MemoryMax` reports
   15032385536, and `systemctl cat` shows the drop-in.
4. **Restart serve.**
5. **Fire a member:** `sudo mt-run kalshi`. The newest KALSHI row has
   `systemd_unit = 'mt-kalshi-pass.service'` and `memory_peak_bytes`
   within 10% of the journal's `memory peak` for that invocation.
6. **Health:** `/opt/manta-trading/.venv/bin/mt data health` shows
   `ok acquisition memory bound` with 4 members.
7. **API:** `curl -s localhost:<port>/api/v1/overview | jq '.passes[] |
   select(.pass=="kalshi") | .last_run.memory_peak_bytes'` is non-null.
8. **Release timers.**

Issue #34 stays open after the cutover. It closes on the operator's
confirmation in TD4, once a tick run that ingested units is behind the
bound.

## Implementation Notes

### Development Approach

1. Migration 061, the `PassRun` fields and `close`. Integration tests.
2. `cgroup_peak.py` with fixtures captured on this host, then the recorder
   change.
3. `memory_bound.py`, then the script rewritten over it (233's tests move
   from journal lines to `pass_runs` peaks).
4. The health row.
5. The installer lib, its test, and the hook in `install-production.sh`.
6. The overview field: dataclass, API model, OpenAPI docs check.
7. The cutover script, then the docs.

Commit after each numbered step.

### Special Considerations

- **A pass killed by the slice's OOM killer records no peak.** It never
  reaches close. The next run's abandoned-run sweep closes its row as FAILED
  with NULL peak columns. The bound therefore cannot learn from the run that
  exceeded it. Such a run is still visible: systemd marks the unit failed
  with `Result=oom-kill` (`mt-run status`, `systemctl --failed`), and the
  journal keeps its peak. Health FAILs on it (TD4). The operator's fix is
  to raise the bound in the drop-in by hand and re-install once a
  successful run records the larger peak. The FAIL names the unit, and the
  runbook 100 entry says what to do.
- **Security:** the installer runs the derivation as `manta-trading`, never
  as root. Root writes only a fixed comment, `[Slice]`, and the one stdout
  line, after checking it against `^MemoryMax=[0-9]+G$`.
