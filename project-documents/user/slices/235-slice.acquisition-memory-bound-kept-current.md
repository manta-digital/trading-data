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
- **Per-run memory is visible.** `/api/v1/overview` shows each pass's last
  memory peak, which today exists only in the journal.

## Technical Scope

**Included**

1. Migration `061_pass_runs_memory_peak` on the minute track (primary
   database): two nullable columns on `pass_runs`, `memory_peak_bytes` and
   `systemd_unit`, with a CHECK that both are null or both are set.
2. A cgroup reader, `manta_trading/data/acquisition/cgroup_peak.py`: this
   process's service unit name and its `memory.peak`.
3. `PassRunRecorder.close` and `PassRunRepository.close` record the peak.
   Every pass kind gets it, because all six close through the same recorder.
4. The rule module, `manta_trading/data/acquisition/memory_bound.py`. It holds
   the headroom and window constants, member discovery from installed unit
   files, the derivation from `pass_runs`, the loaded-value read, and the
   drop-in text. It is the only place the rule exists.
5. `scripts/acquisition_memory_bound.py` is rewritten as a thin entry point
   over the module. It reads `pass_runs` instead of the journal and gains
   `--dropin`.
6. The health check `acquisition memory bound`, in
   `cli/commands/health_memory_bound.py`.
7. `deploy/lib/memory_bound_dropin.sh`, sourced by `install-production.sh`:
   it runs the derivation as the service user and writes the drop-in.
8. `last_run.memory_peak_bytes` on `/api/v1/overview` and in
   `mt data overview --json`.
9. `scripts/cutover_235_memory_bound.py`. A pending migration makes
   `upgrade_production.py` refuse, so this release needs its own cutover.
10. Documentation updates: runbook 100's "Adding a source or pass" (the
    hand re-derive step that 233 added is removed), the slice unit's comment,
    and the CHANGELOG.

**Excluded**

- **Re-deriving between releases.** Writing the drop-in needs root. The
  health unit runs as `manta-trading`. Between releases a drift shows as a
  health FAIL, and any re-run of the installer fixes it.
- **Backfilling peaks from the journal.** It would not close the gap. The
  tick unit's journal peak is from 234's no-purchase firing, which is not
  representative, and the timed members record a peak within a day of the
  cutover.
- **A peak on the text overview screen.** The plan entry places it on the API.
  The JSON output gets it because it shares the API's shape. The fixed-width
  screen is unchanged.
- **Recording the peak of a pass the OOM killer kills.** See Special
  Considerations.
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
  (`cli/commands/overview.py`), the JSON rendering in
  `cli/rendering/overview.py`, and `LastRunRecord`
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
scripts/acquisition_memory_bound.py    CLI over memory_bound (print | --dropin)
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
pass_runs: max(memory_peak_bytes) per systemd_unit, ended_at in last 30 d ──► peaks
memory_bound.derive(members, peaks) ──► ceil_GiB(2 × Σ peaks)  | MemoryBoundError(member)

        ┌─ health:   systemctl show manta-acquisition.slice → LoadState, MemoryMax
        │            FAIL unless loaded, finite and ≥ derived
derive ─┤
        └─ install:  systemd-run --uid=manta-trading … acquisition_memory_bound.py --dropin
                     exit 0 → write manta-acquisition.slice.d/memory-bound.conf → daemon-reload
                     exit 2 → loud "NOT RE-DERIVED: <reason>", value in force unchanged
```

### State Management

- **`pass_runs`** gains the per-run peak. It is written once, at close, by the
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
- **All kinds, not only members.** The recorder does not know about slices.
  `mt-health` and `mt-accounting` rows get a peak too. That costs nothing
  and makes the overview uniform. The derivation selects members by unit
  name (TD2).
- **Outside a service there is no peak.** A pass run from a shell is in a
  `session-N.scope` or `user@….service/app.slice` cgroup. That counter
  covers unrelated processes, so it is not recorded. Both columns stay
  NULL and the recorder logs one WARNING:
  `pass_runs: not in a systemd service cgroup (<path>); no memory peak
  recorded`. NULL here means "not measured", and the derivation treats a
  member with no measured run as a failure. Nothing stands in for it.
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

A row's `systemd_unit` is the key. There is no `PassKind`-to-unit table to
keep in step with the unit files. A unit that joins the slice in a later
release is a member as soon as its file is installed, and it has a peak as
soon as it runs once.

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
| `MEMORY_BOUND_DROPIN` | `"memory-bound.conf"` | Drop-in file name under `<slice>.d/` |

Functions:

- `members(unit_dir)`: sorted unit names.
- `fetch_peaks(conn, units, now)`: one query that returns `{unit: max bytes}`
  over the window, for rows with a non-null peak, of any outcome.
- `derive(members, peaks)`: returns `(peaks, gib)`. It raises
  `MemoryBoundError` naming every member with no peak, not only the first:
  `mt-tick-pass.service: no recorded memory peak in the last 30 days`. With
  no members it raises `no unit declares Slice=manta-acquisition.slice`.
- `loaded_memory_max(run)`: runs `systemctl show manta-acquisition.slice
  --property=LoadState --property=MemoryMax`. It parses `key=value` lines in
  any order and returns `LoadedBound(load_state, max_bytes | None)`, where
  `None` means `infinity`. The command runner is injected for tests.
- `render_dropin(peaks, gib, derived_at)`: the drop-in text.

Any outcome counts toward the peak. A run that ended FAILED still used that
memory.

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
| A member has no peak in the window | FAIL | the error's text, naming each such member |
| `LoadState` ≠ `loaded` | FAIL | `manta-acquisition.slice is <state>` |
| `MemoryMax` is `infinity` | FAIL | `manta-acquisition.slice has no MemoryMax` |
| loaded < derived | FAIL | `MemoryMax 14 GiB < derived 16 GiB (peaks: …); the next install writes 16G` |
| loaded ≥ derived | OK | `MemoryMax 14 GiB ≥ derived 14 GiB (4 members, 30-day peaks)` |

A loaded value above the derived one passes. That matches the plan entry,
which fails only when the bound is too tight. The check reads `pass_runs` on
health's existing primary connection inside `gather`. If the primary is down,
health already exits 2.

**Consequence the PM should know about.** The tick pass is manual. Until it
completes one run that records a peak, and again whenever it has not run for
30 days, this check FAILs. That makes `mt-health.service` a failed unit
hourly. The plan entry asks for exactly this, and it is what keeps
issue #34 visible. See the open question in Special Considerations.

### TD5 — The install writes a drop-in; it never edits the shipped unit

`install-production.sh` step 5 copies the units. It then calls
`write_memory_bound_dropin` from `deploy/lib/memory_bound_dropin.sh`,
before step 6's `daemon-reload`. The function runs:

```
systemd-run --quiet --wait --pipe --collect \
  --uid=manta-trading --gid=manta-trading \
  -p EnvironmentFile=/etc/manta-trading.env -p WorkingDirectory=/opt/manta-trading \
  /opt/manta-trading/.venv/bin/python scripts/acquisition_memory_bound.py --dropin
```

`systemd-run` parses the environment file exactly as the pass units do, so
the derivation sees the same `MT_TIMESCALE_DB_URL` and the same database role.
Exit codes:

- **0:** stdout is the drop-in text. The function writes it to a temp file in
  `manta-acquisition.slice.d/` and moves it over `memory-bound.conf`
  (mode 0644, root), then prints `memory bound: MemoryMax=<N>G written`.
- **2** (the script's "cannot derive": a member with no peak, an unreachable
  database, or a missing column): prints a banner on stderr,
  `MEMORY BOUND NOT RE-DERIVED: <reason>`, followed by the value that stays
  in force. The install continues. Failing the install here would block
  every release until a paid tick run. The health check keeps failing until
  the cause is fixed, so this path is loud, not silent.
- **Anything else** is a bug. The function calls `die` with the output,
  before the reload, so the half-done install stays inert. This is the
  installer's existing failure posture.

**Why a drop-in.** The shipped file stays identical to the repo, and the
installer keeps copying it unconditionally. The derived value has its own
file with its own comment, and `systemctl cat manta-acquisition.slice` shows
both. Rewriting the installed unit's `MemoryMax=` line was rejected: the
installed file would differ from the release it came from.

**Order with the migration.** A cutover migrates before it installs (234's
order), so the column exists when the derivation runs. A release with no
migration runs `upgrade_production.py`, which calls the same installer.

### TD6 — API: the peak goes on `last_run`

`LastRun`, `LastRunRecord` and the JSON rendering gain
`memory_peak_bytes: int | None`. It is null when the run was not measured
(TD1) and for rows from before migration 061. Bytes, not GiB: the API carries
exact values and the UI formats them. This is an additive, nullable field,
so the patch version applies. `check_api_docs.py` and the published OpenAPI
schema update with it. `systemd_unit` is not served. It is the derivation's
join key, and the overview already keys lines by `pass`.

### Patterns and Conventions

- The pure rules (cgroup-line parse, `systemctl show` parse, derive, verdict,
  drop-in text) take values in and return values out. The I/O is three thin
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
- **API:** the per-run peak is served (TD6). The bound verdict is not, for
  the reason in the plan entry.

### Architecture statements superseded

- 233 design, "the result is copied by hand into
  `deploy/systemd/manta-acquisition.slice`", and the
  `scripts/acquisition_memory_bound.py` docstring: replaced by TD5.
- 233 design, "peaks come from the journal": replaced by TD1. The rule
  itself (2 × Σ 30-day peaks, rounded up to whole GiB) is unchanged.

## Implementation Details

### Database / Storage Schema

Migration `061_pass_runs_memory_peak`, minute track, idempotent:

```sql
ALTER TABLE pass_runs
    ADD COLUMN IF NOT EXISTS memory_peak_bytes BIGINT,
    ADD COLUMN IF NOT EXISTS systemd_unit      TEXT;
ALTER TABLE pass_runs DROP CONSTRAINT IF EXISTS pass_runs_memory_peak_check;
ALTER TABLE pass_runs ADD CONSTRAINT pass_runs_memory_peak_check CHECK (
    (memory_peak_bytes IS NULL AND systemd_unit IS NULL)
 OR (memory_peak_bytes > 0     AND systemd_unit IS NOT NULL));
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
| `uv run python scripts/acquisition_memory_bound.py` | members, their 30-day peaks, `sum … x 2 -> MemoryMax=NG`, and the loaded value | 0 / 2 cannot derive |
| `… --dropin` | the drop-in text on stdout, nothing else | 0 / 2 cannot derive |

Drop-in text:

```ini
# Written by install-production.sh from pass_runs (slice 235); do not edit.
# Rule: MemoryMax = ceil_GiB(2 x sum of each member's max 30-day memory peak).
# Derived 2026-10-20T14:03Z: mt-daily-pass.service 426.1 MiB,
#   mt-kalshi-pass.service 318.1 MiB, mt-minute-pass.service 6348.8 MiB,
#   mt-tick-pass.service 1210.0 MiB; sum 8.11 GiB -> 17G.
[Slice]
MemoryMax=17G
```

(The numbers above show the format only. The real ones come from `pass_runs`.)

## Integration Points

### Provides to Other Slices

- A per-run `memory_peak_bytes` on every `pass_runs` row written under a
  service. A future pass or source gets it with no code change.
- A slice bound that follows the measured load. A future acquisition unit
  needs only `Slice=manta-acquisition.slice`. The runbook's add-a-pass
  procedure loses its re-derive step.
- `last_run.memory_peak_bytes` for the trading-ui overview.

### Consumes from Other Slices

- 233's rule and member regex (moved, unchanged).
- 234's tick unit as a member. Its missing peak is the expected first FAIL.
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
   every member with no peak in the window and exits 2. It no longer calls
   `journalctl`.
4. `mt data health` prints an `acquisition memory bound` row with the
   verdicts in TD4's table. A FAIL makes the command exit 1.
5. `install-production.sh` writes
   `/etc/systemd/system/manta-acquisition.slice.d/memory-bound.conf` when the
   derivation succeeds. When it exits 2, the installer prints the
   `MEMORY BOUND NOT RE-DERIVED` banner and completes. On any other exit,
   the installer stops before `daemon-reload`.
6. `/api/v1/overview` and `mt data overview --json` carry
   `last_run.memory_peak_bytes`.

### Technical Requirements

1. Unit tests:
   - The cgroup parse uses the real `/proc/self/cgroup` lines captured from
     `mt-kalshi-pass.service` and from a login shell on manta9000, plus a
     cgroup v1 multi-line file, which yields no unit.
   - The `systemctl show` parse uses real output for the loaded slice, for a
     not-found unit, and for `MemoryMax=infinity`.
   - Members are discovered from copies of the real `deploy/systemd` units.
   - Derive and verdict are covered for each table row in TD4.
   - The drop-in text is checked.
2. A unit test of `memory_bound_dropin.sh` with a stub deriver for exits 0,
   2 and 1, writing to a temp directory, in the style of
   `test/unit/test_setup_backup.py`.
3. Integration tests against the test cluster:
   - Migration 061 applies twice cleanly, and the CHECK rejects a peak
     without a unit.
   - `PassRunRepository.close` writes both columns.
   - `fetch_peaks` honors the window and ignores NULL rows.
   - The overview API returns the field.
4. `ruff check`, scoped `ruff format`, mypy (one invocation over the source
   and test paths) and `shellcheck` on the new lib and the installer: all
   clean.
5. Runbook 100, the slice file comment and the CHANGELOG are updated.
   Runbook 100's add-a-pass step no longer contains a hand re-derive.

### Integration Requirements

1. The existing health, overview and pass-run tests pass unchanged, apart
   from the added field and row.
2. A release installed after this slice ships its bound with no edit to
   `deploy/systemd/manta-acquisition.slice`.

### Verification Walkthrough

**Dev (before the cutover)**

1. `uv run pytest test/unit -k "cgroup_peak or memory_bound or health"`:
   all pass.
2. Run a pass from a shell against the test database. Its row has NULL peak
   columns and the log shows the "not in a systemd service cgroup" WARNING:

   ```
   psql "$MT_TIMESCALE_TEST_URL" -c "SELECT pass, systemd_unit, memory_peak_bytes FROM pass_runs ORDER BY started_at DESC LIMIT 1"
   ```

3. `uv run python scripts/acquisition_memory_bound.py` against production
   before the cutover exits 2 and names the missing column. This proves the
   cannot-derive path is not a traceback.

**Cutover** (one command, operator account; tag first)

```
uv run python scripts/cutover_235_memory_bound.py v<release>
```

Steps, each check-then-act, with a report in `project-documents/user/notes/`:

1. **Preconditions:** the ref exists here and on origin, and 061 is the only
   pending primary migration.
2. **Hold timers:** stop each acquisition timer and wait out its pass.
3. **Migrate:** apply 061, then confirm both columns and the CHECK exist.
4. **Install:** the output shows `MEMORY BOUND NOT RE-DERIVED: …` naming
   every member, because no row has a peak yet. `systemctl show
   manta-acquisition.slice -p MemoryMax` still reports the 14G bootstrap
   value.
5. **Restart serve.**
6. **Fire a member:** `sudo mt-run kalshi`. The newest KALSHI row has
   `systemd_unit = 'mt-kalshi-pass.service'` and `memory_peak_bytes`
   within 10% of the journal's `memory peak` for that invocation.
7. **Health:** `/opt/manta-trading/.venv/bin/mt data health` shows
   `FAIL acquisition memory bound  mt-daily-pass.service, mt-minute-pass.service,
   mt-tick-pass.service: no recorded memory peak in the last 30 days`. This
   is the expected state; Kalshi is no longer listed.
8. **API:** `curl -s localhost:<port>/api/v1/overview | jq '.passes[] |
   select(.pass=="kalshi") | .last_run.memory_peak_bytes'` is non-null.
9. **Release timers:** start the timers again, as they were found.

**After the cutover (outside the slice's tasks)**

- Within a day the daily and minute timers fill their peaks, and health
  then names only `mt-tick-pass.service`.
- After a real tick run, the next release install prints
  `memory bound: MemoryMax=<N>G written`.
  `systemctl cat manta-acquisition.slice` shows the drop-in, and health
  reports OK. Issue #34 closes then.

## Implementation Notes

### Development Approach

1. Migration 061, the `PassRun` fields and `close`. Integration tests.
2. `cgroup_peak.py` with fixtures captured on this host, then the recorder
   change.
3. `memory_bound.py`, then the script rewritten over it (233's tests move
   from journal lines to `pass_runs` peaks).
4. The health row.
5. The installer lib, its test, and the hook in `install-production.sh`.
6. The overview field: dataclass, API model, JSON, OpenAPI docs check.
7. The cutover script, then the docs.

Commit after each numbered step.

### Special Considerations

- **A pass killed by the slice's OOM killer records no peak.** It never
  reaches close. The next run's abandoned-run sweep closes its row as FAILED
  with NULL peak columns. The bound therefore cannot learn from the run that
  exceeded it. Such a run is still visible: systemd marks the unit failed
  with `Result=oom-kill` (`mt-run status`, `systemctl --failed`), and the
  journal keeps its peak. Recording kills is out of scope. If it becomes
  needed, it is a health check on the unit's `Result`.
- **Open question for the PM: a manual member and the 30-day window.** Per
  the plan entry, health fails whenever `mt-tick-pass.service` has no
  recorded run in the last 30 days, both before its first real run and
  during any later idle month. The alternative is: for a member with no run
  in the window, use its newest recorded peak of any age, shown with its
  date, and FAIL only when it has never recorded one. That keeps the
  failure for "never measured" and drops the recurring one for "not run
  lately". The design follows the plan entry unless the PM chooses the
  alternative.
- **Security:** the installer runs the derivation as `manta-trading`, never
  as root, and root only writes the file the derivation printed. The drop-in
  content is one integer and comments rendered by our own code. The function
  rejects output that lacks a single `MemoryMax=<N>G` line before writing it.
