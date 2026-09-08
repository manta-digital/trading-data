---
docType: tasks
slice: minute-acquisition-correctness
project: trading-data
lld: user/slices/921-slice.minute-acquisition-correctness.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [919]
interfaces: [162, 165, 912]
projectState: >
  Continuation of 921 file 1 (range end, provider window, accounting, failure
  policy, two-phase cycle). This file covers the health check that measures
  session mass, the repair of six weeks of truncated days through the single
  writer, the cutover script, and closing issues #19 and #20. Assumes
  Sections 1-5 are complete and committed on the slice branch.
dateCreated: 20260908
dateUpdated: 20260908
status: not_started
---

## Context Summary

- Working on **921 minute-acquisition-correctness**, file 2 of 2. File 1
  covers Sections 1–5; this file continues at Section 6. Task numbering is
  continuous across both files.
- Source of truth: `user/slices/921-slice.minute-acquisition-correctness.md`.
  Tasks cite its Technical Scope items (Scope 1–5), Technical Decisions
  (Decision 1–7), Success Criteria (SC1–SC8), and Verification Walkthrough.
- **Do not start Section 7 (repair) before Sections 1–5 are merged-ready.**
  The repair reseeds gap rows that the fixed code must then fetch correctly;
  running it against the old range-end code re-creates the same truncation.
- Existing patterns to follow:
  - Health checks: `cli/commands/health.py` — pure rule functions returning
    `HealthCheck`, one `gather()` doing all I/O, `render()` formatting. Tests
    in `test/unit/cli/commands/test_data_health.py`.
  - Cutover scripts: `scripts/cutover_common.py` (`say`, `run`, `unit_active`,
    `preflight`, `install`, `fire`, `read_journal`) with
    `scripts/cutover_267_historical.py` and `cutover_268_trades_filter.py` as
    worked examples; tests in `test/unit/test_cutover_267.py`.
  - Systemd unit tests: `test/unit/deploy/test_units.py`.
- **PM host steps are a script invocation and a printed report, never a chat
  checklist.** The cutover is one command that checks then acts and prints
  what it did.
- Measured production values the tasks depend on (read-only, 2026-09-07/08):
  healthy session 2026-08-27 held 1,991,311 bars across 10,859 symbols with
  7,259 at ≥30 bars; broken sessions hold ~45k bars; one session is ~41k rows
  in `minute_4hour_ohlcv`; NYSE covers 11,692 of 13,081 active instruments;
  2026 NYSE early closes are 07-02 17:00 UTC and 11-27 / 12-24 18:00 UTC.
- `mt-minute-pass.timer` currently fires at 01:05 and 13:05 UTC. The 01:05
  firing is the collecting one for the prior session.
- Commit checkpoint at the end of each section; unit tier plus mypy over the
  touched paths before each; `ruff format` scoped to touched files.
- Delivers (file 2): a health check that fails within one firing when session
  mass collapses, a repair that goes through `update_data_gaps` under the
  daemon's own lock, a one-command cutover, and #19/#20 closed with
  measurements.

## Section 6: Health check — minute session mass

Design *Scope 4*, *Decision 5*, *Decision 6*, *SC7*. The failure this slice
fixes was invisible because the check judged the newest bar's age, not how
much data the session holds.

- [ ] **Task 6.1: Health constants** (effort: 1)
  - [ ] Add to `constants.py`, each with a docstring stating the measurement
        behind the value: `HEALTH_MINUTE_SESSION_CALENDAR` ("NYSE"),
        `HEALTH_MINUTE_SESSION_COLLECTION_LAG` (3 h),
        `HEALTH_MINUTE_SESSION_MIN_BARS_PER_MINUTE` (2,500 — half the measured
        healthy 5,100/min on 2026-08-27),
        `HEALTH_MINUTE_SESSION_MIN_SYMBOLS` (5,000 — against 7,259 measured),
        `HEALTH_MINUTE_SESSION_SYMBOL_MIN_BARS` (30),
        `HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT` ("30s", sibling of
        `CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT`).
  - [ ] Add `MINUTE_PASS_FIRING_TIMES_UTC` holding both firing times currently
        written in `deploy/systemd/mt-minute-pass.timer` (01:05, 13:05), as
        the single source both the timer and the health check read
        (Scope 4 — the check and the timer cannot be allowed to disagree).
  - [ ] Success: `test/unit/test_constants.py` asserts every value and type;
        no health threshold appears as a literal in `health.py`.
- [ ] **Task 6.2: Judged-session selection** (effort: 3)
  - [ ] A pure function selects the session to judge: the newest
        `trading_sessions` row for `HEALTH_MINUTE_SESSION_CALENDAR` whose
        collecting firing has finished — the first `MINUTE_PASS_FIRING_TIMES_UTC`
        entry after its `session_close_utc`, plus
        `HEALTH_MINUTE_SESSION_COLLECTION_LAG`, is in the past.
  - [ ] This resolves to 04:05 UTC the next day for a regular close and for an
        early close alike. Weekends and holidays fall out because they are not
        calendar rows.
  - [ ] The function takes `now` and the candidate sessions as arguments (no
        I/O), so the boundary is directly testable.
  - [ ] Success: given a fixture calendar, the judged session is yesterday's
        from 04:05 UTC onward and the day before that earlier — asserted at
        04:04 and 04:05 for both a 20:00 and a 17:00 close (SC7).
- [ ] **Task 6.3: Mass measurement query** (effort: 2)
  - [ ] Read both quantities from `minute_4hour_ohlcv` — `SUM(minute_count)`
        for total bars, and the count of symbols whose per-symbol sum is
        `≥ HEALTH_MINUTE_SESSION_SYMBOL_MIN_BARS` — over buckets whose
        `time_bucket` falls in `[session_open_utc, session_close_utc)`.
  - [ ] Never read raw `minute_ohlcv` (Scope 4, the 140-slices §166/§167
        latency cliff). Run under
        `HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT`; a timeout exits 2 like
        every other 919 check, it does not report a false mass.
  - [ ] The cagg's own freshness is judged by the existing
        `cagg minute_4hour_ohlcv` line; do not duplicate that check here.
  - [ ] Success: the query is a single grouped read; the table name comes from
        the existing granularity-source constant, not a literal.
- [ ] **Task 6.4: `check_minute_session_mass` rule and rendering** (effort: 2)
  - [ ] A pure rule function in `health.py` takes the judged session, its
        length in minutes, the measured bars and symbol count, and returns a
        `HealthCheck` named `minute session mass`.
  - [ ] Floors scale with the session's real length:
        `HEALTH_MINUTE_SESSION_MIN_BARS_PER_MINUTE × (close − open) in minutes`
        (390 regular, 210 early close). The symbol floor does not scale.
  - [ ] The detail line names the session date, its length, both measured
        values, and both floors — the design's example line is the format.
  - [ ] Wire it into `gather()` alongside the existing checks.
  - [ ] Success: a 390-minute session is judged against 975,000 bars, a
        210-minute session against 525,000.
- [ ] **Task 6.5: Remove the quota floor check and fix the UTC label**
      (effort: 1)
  - [ ] Delete `check_quota`, its `gather()` call, `fetch_quota` if it has no
        other caller, and `HEALTH_EODHD_QUOTA_HEADROOM_MIN`. Its consequence
        is what Task 6.4 now measures, and no floor is right when normal
        operation is designed to consume the whole allowance (Decision 6).
  - [ ] `check_raw_freshness` prints the timestamp in local time with a `UTC`
        suffix; convert to UTC before formatting.
  - [ ] If removing the quota check leaves `data_health` no longer needing the
        HTTP client or `eodhd_api_key`, remove those requirements too rather
        than leaving a dead precondition that can fail the command.
  - [ ] Success: `mt data health --json` no longer carries an `eodhd quota`
        entry; existing tests referencing it are updated, not deleted
        wholesale.
- [ ] **Task 6.6: Tests for the health check** (effort: 3)
  - [ ] In `test/unit/cli/commands/test_data_health.py`: FAIL on a fixture
        session holding 45k bars; PASS on 1.99M with 7,259 symbols; PASS on a
        210-minute early-close fixture holding 1.05M (SC7).
  - [ ] A test captures the executed query and asserts it targets
        `minute_4hour_ohlcv` and not `minute_ohlcv` (SC7).
  - [ ] A test asserts a statement timeout exits 2 rather than reporting a
        low mass.
  - [ ] Boundary tests on judged-session selection at 04:04 and 04:05 UTC for
        a regular and an early close.
  - [ ] A test asserts no check named `eodhd quota` is produced.
  - [ ] Success: `uv run pytest test/unit/cli -q` passes.
- [ ] **Task 6.7: Timer rendered from the constant** (effort: 2)
  - [ ] `deploy/install-production.sh` renders `mt-minute-pass.timer`'s
        `OnCalendar` lines from `MINUTE_PASS_FIRING_TIMES_UTC` rather than the
        unit file carrying hand-written times.
  - [ ] The rendering must be explicit and verifiable: if the constant cannot
        be read, the install fails loudly — never install a unit with a
        default schedule.
  - [ ] Success: `test/unit/deploy/test_units.py` asserts the installed unit's
        `OnCalendar` lines match the constant, so the health check's judged
        session and the timer cannot drift apart.
- [ ] **Task 6.8: Section 6 checkpoint** (effort: 1)
  - [ ] Unit tier and mypy green; `ruff format` scoped to touched files.
  - [ ] Commit: `feat: judge minute session mass in mt data health (921)`.

## Section 7: Repair through the single writer

Design *Scope 2*, *Decision 3*, *Decision 7*, *SC2*. Six weeks of truncated
days must be re-seeded. The repair writes **no SQL of its own** against
`data_gaps` — it calls `update_data_gaps` under the daemon's advisory lock.

- [ ] **Task 7.1: Repair window constant and truncation signature** (effort: 1)
  - [ ] Add `REPAIR_921_WINDOW_START` (2026-07-16, the day the coverage seeder
        shipped) to `constants.py` with a docstring saying why that date and
        that rows before it are never touched.
  - [ ] Define the truncation signature in one place: a symbol-day is
        truncated when `max(time)` for that session date is
        `≤ session_open_utc`. Both the `--check` report and the coverage
        adjustment must use that one definition.
  - [ ] Success: the predicate exists once and is referenced, not restated.
- [ ] **Task 7.2: `compute_missing_minute_sessions` accepts uncovered days**
      (effort: 2)
  - [ ] Add an optional parameter taking a per-symbol set of days to treat as
        uncovered, subtracted from the coverage set before the diff. Existing
        callers pass nothing and are unaffected (additive and
        backward-compatible, per the design's Interfaces Required).
  - [ ] Success: with a day passed as uncovered, that session appears in the
        returned ranges even though the coverage index contains it; tests
        added to `test/unit/data/gaps/test_minute_coverage.py`.
- [ ] **Task 7.3: `scripts/repair_921_minute_sessions.py --check`** (effort: 3)
  - [ ] Follow the `cutover_common` script pattern: explicit arguments, named
        constants at the top, database URL taken from the configured setting
        and never from ambient environment.
  - [ ] `--check` is read-only and reports, over the active universe since
        `REPAIR_921_WINDOW_START`: truncated symbol-days, zero-width minute
        gap rows, session-open-ended terminal rows (`PROVIDER_HOLE` and
        `RETRY_EXHAUSTED`), midnight-ended legacy rows inside the window, and
        whether either pass unit is active.
  - [ ] `--check` must be safe to run against production from any host with
        the URL (Verification Walkthrough).
  - [ ] Success: running `--check` prints the counts and exits 0 without
        writing anything.
- [ ] **Task 7.4: `--apply` through `update_data_gaps`** (effort: 3)
  - [ ] Per symbol, one transaction, under
        `advisory_lock(conn, symbol, "minute", timeout=DAEMON_LOCK_TIMEOUT)` —
        the daemon's own helper, so an overlapping pass blocks and then the
        symbol is skipped and reported, never written concurrently.
  - [ ] Call `update_data_gaps(conn, symbol, "minute", REPAIR_921_WINDOW_START,
        now_midnight, fetch_status_for_unfilled=UNKNOWN, outcome=PARTIAL,
        force_reset_terminal=True, precomputed_ranges=…)` where the ranges
        come from `compute_missing_minute_sessions` with the symbol's
        truncated days passed as uncovered (Task 7.2).
  - [ ] Commit per symbol so a run that dies partway is resumed by rerunning.
  - [ ] Refuse to start while `mt-minute-pass.service` or
        `mt-daily-pass.service` is active.
  - [ ] The script never calls the provider.
  - [ ] Success: a second `--apply` reports zero net change (carry-forward
        keeps attempt counts); rows before the window are untouched (SC2).
- [ ] **Task 7.5: Tests for the repair script** (effort: 3)
  - [ ] Unit tests in `test/unit/test_repair_921.py`, following
        `test_cutover_267.py`'s style (subprocess or direct invocation with a
        fake writer; no live database).
  - [ ] A test with a fake `update_data_gaps` asserts it is called with
        `force_reset_terminal=True` and inside the advisory lock, and that the
        script issues no other write against `data_gaps` (SC2).
  - [ ] A test asserts the script exits non-zero and writes nothing when a
        pass unit reports active.
  - [ ] A test asserts idempotency: the same input twice yields the same
        computed ranges and a zero-net-change report.
  - [ ] A test asserts the truncated-day predicate matches the signature from
        Task 7.1 against fixture rows on both sides of the boundary.
  - [ ] Success: `uv run pytest test/unit/test_repair_921.py -q` passes.
- [ ] **Task 7.6: Run `--check` against production and record the baseline**
      (effort: 1)
  - [ ] Run `uv run python scripts/repair_921_minute_sessions.py --check`
        read-only against production and record the output in the slice's
        notes. This is the before-image for SC3 and the issue closeout.
  - [ ] Success: counts recorded; nothing written.
- [ ] **Task 7.7: Section 7 checkpoint** (effort: 1)
  - [ ] Unit tier and mypy green; `ruff format` scoped to touched files.
  - [ ] Commit: `feat: add the 921 minute-session repair script (921)`.

## Section 8: Cutover, release, and closeout

Design *Scope 5*, Verification Walkthrough, *SC3*, *SC7*, *SC8*. One command
for the PM, then a report.

- [ ] **Task 8.1: `scripts/cutover_921_minute_sessions.py`** (effort: 3)
  - [ ] Built on `cutover_common`: preflight the ref, install via
        `install-production.sh` (twice if units changed), verify no pass unit
        is active, verify the EODHD allowance reset recently (the repair must
        run just after 00:00 UTC so the following firings have budget), then
        run the repair `--apply` printing before/after counts per predicate.
  - [ ] Print what the next firings will do (00:35 UTC daily, 01:05 UTC minute
        trailing phase) so the report stands alone.
  - [ ] Check-then-act throughout, and log every action it takes — the script
        runs with root privileges and must be auditable afterward.
  - [ ] Success: the whole cutover is one command producing one report; no
        step asks the PM to run something by hand.
- [ ] **Task 8.2: Tests for the cutover script** (effort: 2)
  - [ ] Following `test_cutover_267.py`: each precondition failure aborts
        before any write; the happy path calls the repair with `--apply`
        exactly once.
  - [ ] Success: `uv run pytest test/unit -q -k cutover_921` passes.
- [ ] **Task 8.3: Runbook and CHANGELOG** (effort: 2)
  - [ ] Runbook 100 gains the truncation-signature query and
        `repair_921_minute_sessions.py --check` as the standing diagnostic for
        this failure class, and the `minute session mass` check's meaning and
        floors.
  - [ ] CHANGELOG entry for the release covering the range-end fix, the
        accounting rule, the two-phase cycle, the health check, and the
        removal of the quota floor check.
  - [ ] Success: a reader who has never seen this slice can run the diagnostic
        from the runbook alone.
- [ ] **Task 8.4: Release and cutover** (effort: 2)
  - [ ] Full test tiers green, version bumped, tagged, released.
  - [ ] Run the cutover script just after 00:00 UTC; capture its report.
  - [ ] Success: the report shows the repair applied and the counts moved as
        `--check` predicted.
- [ ] **Task 8.5: Verify against production after two firings** (effort: 2)
  - [ ] After two nightly firings, confirm: truncated symbol-days over the
        last five NYSE sessions across active symbols = 0; bars for the judged
        session ≥ 1,000,000 (SC3); `mt data health` shows `OK minute session
        mass` with the measured line; the journal shows `trailing phase
        complete: N symbols` before any backfill line (SC6); at least one
        `healthy` run after 23:00 UTC (SC7).
  - [ ] `repair_921_minute_sessions.py --check` reports zero pending.
  - [ ] If any criterion misses, record the measurement and stop — do not
        apply a speculative fix without the actual evidence.
  - [ ] Success: every measurement recorded in the slice notes.
- [ ] **Task 8.6: Close issues #19 and #20** (effort: 1)
  - [ ] Close #19 with the root cause (session-open range end), the fix, and
        the before/after measurements from Tasks 7.6 and 8.5.
  - [ ] Close #20 with the cagg-freshness verification already measured
        (every cagg fresh, migrations 053/054 applied), noting the optional
        recompression as deferred (design Out of scope).
  - [ ] Success: both issues closed with measurements, not assertions (SC8).
- [ ] **Task 8.7: Section 8 checkpoint** (effort: 1)
  - [ ] Commit: `docs: record the 921 cutover measurements and close #19/#20`.
  - [ ] Success: slice branch ready to merge into the integration target.
