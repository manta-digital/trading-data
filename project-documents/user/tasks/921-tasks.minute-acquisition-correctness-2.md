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
  file 1's Sections 1-4 are complete and committed on the slice branch.
dateCreated: 20260908
dateUpdated: 20260909
status: in_progress
---

## Context Summary

- Working on **921 minute-acquisition-correctness**, file 2 of 2. File 1
  covers Sections 1–4; this file continues at Section 5 (health check),
  Section 6 (repair) and Section 7 (cutover and closeout). Task numbering is
  continuous across both files.
- Source of truth: `user/slices/921-slice.minute-acquisition-correctness.md`.
  Tasks cite its Technical Scope items (Scope 1–5), Technical Decisions
  (Decision 1–7), Success Criteria (SC1–SC8), and Verification Walkthrough.
- **Do not start Section 6 (repair) before file 1's Sections 1–4 are
  merged-ready.** The repair reseeds gap rows that the fixed code must then
  fetch correctly; running it against the old range-end code re-creates the
  same truncation.
- **No task in this file waits on a nightly firing.** The cutover script fires
  the daily and minute passes itself and reads the result back from the
  journal, so every acceptance number is produced by running something, not by
  waiting for tomorrow.
- Existing patterns to follow:
  - Health checks: `cli/commands/health.py` — pure rule functions returning
    `HealthCheck`, one `gather()` doing all I/O, `render()` formatting. Tests
    in `test/unit/cli/commands/test_data_health.py`.
  - Cutover scripts: `scripts/cutover_common.py` (`say`, `run`, `unit_active`,
    `preflight`, `install`, `journal_cursor`, `wait_for_pass_to_end`,
    `read_journal`; `fire` is Kalshi-specific but is the shape to follow) with
    `scripts/cutover_267_historical.py` and `cutover_268_trades_filter.py` as
    worked examples; tests in `test/unit/test_cutover_267.py`.
  - Load-tier NFR tests: `test/load/test_167_data_status_nfr.py` and
    `test/load/test_169_coverage_freshness_probe_nfr.py`, over the
    `prod_shaped_db` fixture in `test/load/conftest.py`.
  - Systemd unit tests: `test/unit/deploy/test_units.py` — parses the repo's
    unit files with `configparser`; no database, no systemd, no network.
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
- Commit checkpoint at the end of each section, and one **before** the release
  tag (Task 7.6) — the cutover script installs from the tagged ref, so the
  script itself must be in that commit. Unit tier plus mypy over the touched
  paths before each; `ruff format` scoped to touched files.
- CI runs no test job (`.github/workflows/ci.yml` is publish-on-tag only, a
  repo-wide gap tracked as slice 907), so any tier outside the default run is
  gated by a documented manual invocation, never by an assumed CI job.
- Delivers (file 2): a health check that fails within one firing when session
  mass collapses, a repair that goes through `update_data_gaps` under the
  daemon's own lock, a one-command cutover, and #19/#20 closed with
  measurements.

## Section 5: Health check — minute session mass

Design *Scope 4*, *Decision 5*, *Decision 6*, *SC7*. The failure this slice
fixes was invisible because the check judged the newest bar's age, not how
much data the session holds.

- [x] **Task 5.1: Health constants** (effort: 1)
  - [x] Add to `constants.py`, each with a docstring stating the measurement
        behind the value: `HEALTH_MINUTE_SESSION_CALENDAR` ("NYSE"),
        `HEALTH_MINUTE_SESSION_COLLECTION_LAG` (3 h),
        `HEALTH_MINUTE_SESSION_MIN_BARS_PER_MINUTE` (2,500 — half the measured
        healthy 5,100/min on 2026-08-27),
        `HEALTH_MINUTE_SESSION_MIN_SYMBOLS` (5,000 — against 7,259 measured),
        `HEALTH_MINUTE_SESSION_SYMBOL_MIN_BARS` (30),
        `HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT` ("30s", sibling of
        `CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT`).
  - [x] Add `MINUTE_PASS_FIRING_TIMES_UTC` holding both firing times currently
        written in `deploy/systemd/mt-minute-pass.timer` (01:05, 13:05), as
        the single source both the timer guard and the health check read
        (Scope 4 — the check and the timer cannot be allowed to disagree).
  - [x] Success: `test/unit/test_constants.py` asserts every value and type;
        no health threshold appears as a literal in `health.py`.
- [x] **Task 5.2: Judged-session selection** (effort: 3)
  - [x] A pure function selects the session to judge: the newest
        `trading_sessions` row for `HEALTH_MINUTE_SESSION_CALENDAR` whose
        collecting firing has finished — the first `MINUTE_PASS_FIRING_TIMES_UTC`
        entry after its `session_close_utc`, plus
        `HEALTH_MINUTE_SESSION_COLLECTION_LAG`, is in the past.
  - [x] This resolves to 04:05 UTC the next day for a regular close and for an
        early close alike. Weekends and holidays fall out because they are not
        calendar rows.
  - [x] The function takes `now` and the candidate sessions as arguments (no
        I/O), so the boundary is directly testable.
  - [x] **State the no-candidate verdict.** If the selection yields nothing —
        an empty `trading_sessions` window, a calendar rename, a long holiday
        stretch — the check must report an explicit non-OK result (or exit 2),
        never `ok=True`. A silent pass here is exactly the silence this slice
        exists to end; the project's no-silent-fallback rule applies.
  - [x] Success: given a fixture calendar, the judged session is yesterday's
        from 04:05 UTC onward and the day before that earlier — asserted at
        04:04 and 04:05 for both a 20:00 and a 17:00 close (SC7); an empty
        candidate list produces the stated non-OK verdict.
- [x] **Task 5.3: Tests for judged-session selection** (effort: 2)
  - [x] Boundary cases at 04:04 and 04:05 UTC for a regular (20:00) and an
        early (17:00) close; a weekend and a holiday gap; the empty-candidate
        verdict from Task 5.2.
  - [x] Success: `uv run pytest test/unit/cli/commands/test_data_health.py -q`
        passes.
- [x] **Task 5.4: Candidate fetch and mass measurement query** (effort: 3)
  - [x] **This task owns the `trading_sessions` read** that produces Task
        5.2's candidates: sessions for `HEALTH_MINUTE_SESSION_CALENDAR`, newest
        first, bounded to a small recent window (enough to cover a holiday
        stretch, not the whole calendar). Nothing else fetches them.
  - [x] Read both mass quantities from `minute_4hour_ohlcv` —
        `SUM(minute_count)` for total bars, and the count of symbols whose
        per-symbol sum is `≥ HEALTH_MINUTE_SESSION_SYMBOL_MIN_BARS` — over
        buckets whose `time_bucket` falls in
        `[session_open_utc, session_close_utc)`.
  - [x] Never read raw `minute_ohlcv` (Scope 4, the 140-slices §166/§167
        latency cliff). Run under
        `HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT`; a timeout exits 2 like
        every other 919 check — it does not report a false mass.
  - [x] The cagg's own freshness is judged by the existing
        `cagg minute_4hour_ohlcv` line; do not duplicate that check here.
  - [x] Success: the mass read is a single grouped query; the table name comes
        from the existing granularity-source constant, not a literal.
- [x] **Task 5.5: `check_minute_session_mass` rule and rendering** (effort: 2)
  - [x] A pure rule function in `health.py` takes the judged session, its
        length in minutes, the measured bars and symbol count, and returns a
        `HealthCheck` named `minute session mass`.
  - [x] Floors scale with the session's real length:
        `HEALTH_MINUTE_SESSION_MIN_BARS_PER_MINUTE × (close − open) in minutes`
        (390 regular → 975,000; 210 early close → 525,000). The symbol floor
        does not scale.
  - [x] **The floor is computed, never hardcoded.** The design's Verification
        Walkthrough and file 2's Task 7.5 use `≥ 1,000,000` as a *stricter
        one-time cutover acceptance bar*; that number is not this check's
        floor and must not be written into `health.py`.
  - [x] The detail line names the session date, its length, both measured
        values, and both floors — the design's example line is the format.
  - [x] Wire it into `gather()` alongside the existing checks.
  - [x] Success: a 390-minute session is judged against 975,000 bars, a
        210-minute session against 525,000.
- [x] **Task 5.6: Remove the quota floor check and fix the UTC label**
      (effort: 1)
  - [x] Delete `check_quota`, its `gather()` call, `fetch_quota`, and
        `HEALTH_EODHD_QUOTA_HEADROOM_MIN`. Its consequence is what Task 5.5
        now measures, and no floor is right when normal operation is designed
        to consume the whole allowance (Decision 6). Verified during review:
        these symbols appear only in `health.py` and `constants.py`, `gather()`
        has one caller, `deploy/mt-run` reads only the service exit code, and
        no runbook references the `eodhd quota` line — so the removal is
        unconditional. Leave 919's design record alone; it is history.
  - [x] The httpx client and the `settings.eodhd_api_key` precondition in
        `data_health` can both be dropped outright.
  - [x] `check_raw_freshness` prints the timestamp in local time with a `UTC`
        suffix; convert to UTC before formatting.
  - [x] Success: `mt data health --json` no longer carries an `eodhd quota`
        entry; existing tests referencing it are updated, not deleted wholesale.
- [x] **Task 5.7: Tests for the mass check** (effort: 3)
  - [x] In `test/unit/cli/commands/test_data_health.py`: FAIL on a fixture
        session holding 45k bars; PASS on 1.99M with 7,259 symbols; PASS on a
        210-minute early-close fixture holding 1.05M (SC7).
  - [x] A test captures the executed query and asserts it targets
        `minute_4hour_ohlcv` and not `minute_ohlcv` (SC7).
  - [x] A test asserts a statement timeout exits 2 rather than reporting a
        low mass.
  - [x] A test asserts no check named `eodhd quota` is produced, and that
        `data_health` runs without an EODHD key configured.
  - [x] Success: `uv run pytest test/unit/cli -q` passes.
- [x] **Task 5.8: Load-tier test for the read bound** (effort: 2)
  - [x] The slice states a read NFR — one session is ~41k cagg rows, a
        sub-second read — and degrades to exit 2 on timeout, which an operator
        cannot distinguish from the silence this slice ends. The project's
        convention for exactly this claim is a load-tier test:
        `test/load/test_167_data_status_nfr.py` and
        `test/load/test_169_coverage_freshness_probe_nfr.py` are the
        precedents, the latter asserting its sibling probe "stays well inside
        its budget".
  - [x] **`prod_shaped_db` as it stands cannot measure this.**
        `_seed_prod_shape` seeds one bar per symbol per 7-day bucket starting
        in 2010, and no load-tier fixture seeds `trading_sessions` at all. The
        mass query filters buckets to a judged session's
        `[open, close)` window, so against that fixture the window is
        effectively empty, the test passes in milliseconds, and the NFR is
        never exercised.
  - [x] Add `test/load/test_921_minute_session_mass_nfr.py` with a fixture
        that can actually load the query: NYSE `trading_sessions` rows for a
        recent session plus a dense cagg population for it — on the order of
        the measured ~41k cagg rows across ~11k symbols. Extend
        `prod_shaped_db` or add a sibling fixture; say which.
  - [x] State an explicit budget number rather than "fast", as
        `test_169_coverage_freshness_probe_nfr.py` does, and assert the read
        completes well inside `HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT`.
  - [x] **CI does not run tests.** `.github/workflows/ci.yml` is publish-on-tag
        only (a repo-wide gap tracked as slice 907), so the gate is a
        documented manual run. State the invocation in the test module
        docstring and in runbook 100, rather than assuming a CI job.
  - [x] Success: the load test passes locally and its invocation is written
        down where an operator will find it.

  **Load test production defects:** The load test exposed two defects the unit tier could not reach: (1) `fetch_candidate_sessions` returned only future-dated sessions because `trading_sessions` is populated ~2 years ahead, which would have caused the check to report "no completed session to judge" on every production run; (2) the mass filter dropped the cagg bucket the session opens inside, because 4-hour buckets align to the day and not the session, uncounting ~38% of every session's bars — this defect originated in the design's own wording and is corrected in the slice document's new "Implementation Correction" section. Both are pinned by unit regression tests.
- [x] **Task 5.9: Timer/constant drift guard** (effort: 2)
  - [x] **Keep the unit file authoritative.** `install-production.sh:173`
        installs each unit verbatim from a bash loop; making it render a
        Python constant would need a template mechanism, a venv at install
        time, and a fail-loud read, and would break
        `test/unit/deploy/test_units.py`'s `configparser` parse of the repo's
        unit files. It also cannot be asserted from the unit tier, which runs
        with no systemd.
  - [x] Instead, add a guard test to `test/unit/deploy/test_units.py` that
        parses `deploy/systemd/mt-minute-pass.timer` and asserts its
        `OnCalendar` values equal `MINUTE_PASS_FIRING_TIMES_UTC`. That is the
        same shape as the existing Kalshi unit assertions and catches the
        drift Scope 4 cares about — the health check and the timer disagreeing.
  - [x] Note the deviation from the design's "rendered into the unit" wording
        and the reason, so the design and the tasks do not silently diverge.
  - [x] Success: editing either the timer or the constant alone fails the test.
- [x] **Task 5.10: Section 5 checkpoint** (effort: 1)
  - [x] Unit tier and mypy green; `ruff format` scoped to touched files.
  - [x] Commit: `feat: judge minute session mass in mt data health (921)`.

## Section 6: Repair through the single writer

Design *Scope 2*, *Decision 3*, *Decision 7*, *SC2*. Six weeks of truncated
days must be re-seeded. The repair writes **no SQL of its own** against
`data_gaps` — it calls `update_data_gaps` under the daemon's advisory lock.

- [ ] **Task 6.1: Repair window constant and truncation signature** (effort: 1)
  - [ ] Add `REPAIR_921_WINDOW_START` (2026-07-16, the day the coverage seeder
        shipped) to `constants.py` with a docstring saying why that date and
        that rows before it are never touched.
  - [ ] Define the truncation signature in one place: a symbol-day is
        truncated when `max(time)` for that session date is
        `≤ session_open_utc`. Both the `--check` report and the coverage
        adjustment must use that one definition.
  - [ ] Success: the predicate exists once and is referenced, not restated.
- [ ] **Task 6.2: `compute_missing_minute_sessions` accepts uncovered days**
      (effort: 2)
  - [ ] Add an optional parameter taking a per-symbol set of days to treat as
        uncovered, subtracted from the coverage set before the diff. Existing
        callers pass nothing and are unaffected (additive and
        backward-compatible, per the design's Interfaces Required).
  - [ ] Success: with a day passed as uncovered, that session appears in the
        returned ranges even though the coverage index contains it; tests
        added to `test/unit/data/gaps/test_minute_coverage.py`.
- [ ] **Task 6.3: Decide what happens to window-straddling rows** (effort: 2)
  - [ ] **The problem, measured from the code.** `_delete_intersecting`
        (`update_data_gaps.py:295`) is *named and documented* as an
        intersection but its SQL is containment:
        `gap_start >= %s AND gap_end <= %s`. A coalesced UNKNOWN row that
        starts before `REPAIR_921_WINDOW_START` and ends inside the window is
        therefore **not** deleted — while the seed, which computes missing
        sessions from the cagg coverage index and knows nothing about that
        row, inserts fresh UNKNOWN rows for the in-window sessions the
        surviving row already covers. The symbol ends with overlapping gap
        rows and those sessions are fetched twice against the rationed quota.
  - [ ] This is plausible in production: the coalescer merges consecutive
        sessions, and the design records 57,663 UNKNOWN backfill rows spanning
        2022–2025 plus 1,321 legacy rows.
  - [ ] Decide and record: skip the symbol and report it, or widen that
        symbol's window to the straddling row's `gap_start`. Do **not** change
        `_delete_intersecting`'s semantics as part of this slice — it is
        shared with the daily path; if its docstring is simply wrong, fix the
        docstring and say so.
  - [ ] Success: the decision and its reasoning are written down before
        Task 6.5 implements it.
- [ ] **Task 6.4: `scripts/repair_921_minute_sessions.py --check`** (effort: 3)
  - [ ] Follow the `cutover_common` script pattern: explicit arguments, named
        constants at the top, database URL taken from the configured setting
        and never from ambient environment.
  - [ ] `--check` is read-only and reports, over the active universe since
        `REPAIR_921_WINDOW_START`: truncated symbol-days, zero-width minute
        gap rows, session-open-ended terminal rows (`PROVIDER_HOLE` and
        `RETRY_EXHAUSTED`), midnight-ended legacy rows inside the window,
        **rows straddling the window start** (Task 6.3), and whether either
        pass unit is active.
  - [ ] `--check` must be safe to run against production from any host with
        the URL (Verification Walkthrough).
  - [ ] Success: running `--check` prints the counts and exits 0 without
        writing anything.
- [ ] **Task 6.5: `--apply` through `update_data_gaps`** (effort: 3)
  - [ ] Per symbol, one transaction, under
        `advisory_lock(conn, symbol, "minute", timeout=DAEMON_LOCK_TIMEOUT)` —
        the daemon's own helper, so an overlapping pass blocks and then the
        symbol is skipped and reported, never written concurrently.
  - [ ] Call `update_data_gaps(conn, symbol, "minute", REPAIR_921_WINDOW_START,
        now_midnight, fetch_status_for_unfilled=UNKNOWN, outcome=PARTIAL,
        force_reset_terminal=True, precomputed_ranges=…)` where the ranges
        come from `compute_missing_minute_sessions` with the symbol's
        truncated days passed as uncovered (Task 6.2).
  - [ ] Apply the Task 6.3 decision for straddling rows.
  - [ ] Commit per symbol so a run that dies partway is resumed by rerunning.
  - [ ] Refuse to start while `mt-minute-pass.service` or
        `mt-daily-pass.service` is active.
  - [ ] The script never calls the provider.
  - [ ] Success: a second `--apply` reports zero net change; rows before the
        window are untouched (SC2).
- [ ] **Task 6.6: Unit tests for the repair script** (effort: 3)
  - [ ] Unit tests in `test/unit/test_repair_921.py`, following
        `test_cutover_267.py`'s style (fake writer; no live database).
  - [ ] A test with a fake `update_data_gaps` asserts it is called with
        `force_reset_terminal=True` and inside the advisory lock, and that the
        script issues no other write against `data_gaps` (SC2).
  - [ ] A test asserts the script exits non-zero and writes nothing when a
        pass unit reports active.
  - [ ] A test asserts the truncated-day predicate matches the signature from
        Task 6.1 against fixture rows on both sides of the boundary.
  - [ ] Success: `uv run pytest test/unit/test_repair_921.py -q` passes.
- [ ] **Task 6.7: Integration test for the repair's database behavior**
      (effort: 3)
  - [ ] **A fake writer cannot reach what SC2 actually claims.** Every SC2
        property is a property of `update_data_gaps` itself: that
        `force_reset_terminal=True` over the window resets the terminal rows
        and only those; that carry-forward (`_best_prior_count`, keyed on
        `gap_start`) preserves `attempt_count` so a second `--apply` is
        genuinely zero net change; that pre-window rows survive.
  - [ ] Add `test/integration/test_repair_921_live.py`, using
        `test/integration/conftest.py`'s `migrated_db` fixture (its
        `ephemeral_db` sibling and `test_gaps_window_sql.py` are the
        `data_gaps` precedents) under `MT_TIMESCALE_TEST_URL`: seed a
        production-shaped `data_gaps` fixture
        spanning the window boundary (pre-window rows, in-window terminal
        rows, a straddling row, truncated symbol-days), run `--apply` twice,
        and assert row-level equality between the two runs and that pre-window
        rows are byte-identical to their seeded values.
  - [ ] Failure this catches: carry-forward keys on a `gap_start` the
        recomputed range no longer matches, so every repaired row restarts at
        `attempt_count = 0` or re-increments toward `RETRY_EXHAUSTED` — and
        the fake-writer suite stays green throughout.
  - [ ] Success: the integration test passes; running it twice in a row is
        idempotent.
- [ ] **Task 6.8: Run `--check` against production and record the baseline**
      (effort: 1)
  - [ ] Run `uv run python scripts/repair_921_minute_sessions.py --check`
        read-only against production and record the output in the slice's
        notes. This is the before-image for SC3 and the issue closeout.
  - [ ] Success: counts recorded; nothing written.
- [ ] **Task 6.9: Section 6 checkpoint** (effort: 1)
  - [ ] Unit tier and mypy green; `ruff format` scoped to touched files.
  - [ ] Commit: `feat: add the 921 minute-session repair script (921)`.

## Section 7: Cutover, release, and closeout

Design *Scope 5*, Verification Walkthrough, *SC3*, *SC7*, *SC8*. One command
for the PM, then a report. **No task here waits on a nightly firing** — the
cutover script fires the passes itself and measures immediately.

- [ ] **Task 7.1: `--verify` mode on the repair script** (effort: 3)
  - [ ] Add a read-only `--verify` mode that prints every acceptance
        measurement on demand, so verification is an action rather than a
        wait: truncated symbol-days over the last five NYSE sessions across
        active symbols (SC3, expected 0); bars and symbols-with-≥30-bars for
        the judged session (SC3's `≥ 1,000,000` acceptance bar — stricter than
        the health check's computed 975,000 floor, see Task 5.5); the
        `minute session mass` health line; and the pending counts `--check`
        reports.
  - [ ] **Reuse, do not re-implement.** The judged-session selector
        (Task 5.2), the mass query (Task 5.4), and the rule function
        (Task 5.5) are imported and called — `--verify` must not carry its own
        `SUM(minute_count)` query or its own judged-session logic. Two
        implementations of one measurement drift (an inclusive vs. exclusive
        close, a stale calendar literal) and the cutover then certifies a
        number `mt data health` does not reproduce.
  - [ ] Success: one invocation prints every SC3/SC7 number with its
        pass/fail against the stated bar, and the mass figures come from the
        health check's own code path.
- [ ] **Task 7.2: Tests for `--verify`** (effort: 2)
  - [ ] Assert `--verify` writes nothing, and that each measurement is
        reported against the right bar (the 1,000,000 acceptance bar, not the
        health floor).
  - [ ] Assert the reuse: the health check's judged-session selector, mass
        query, and rule function are the ones invoked (patch them and observe
        the call), so a second implementation cannot slip in.
  - [ ] Success: `uv run pytest test/unit/test_repair_921.py -q` passes.
- [ ] **Task 7.3: `scripts/cutover_921_minute_sessions.py`** (effort: 3)
  - [ ] Built on `cutover_common`: preflight the ref, install via
        `install-production.sh` (twice if units changed), verify no pass unit
        is active, verify the EODHD allowance reset recently (the repair must
        run just after 00:00 UTC so the following firings have budget), then
        run the repair `--apply` printing before/after counts per predicate.
  - [ ] **Then fire the passes rather than waiting for the timers.**
        `cutover_common` already has the cursor → run-unit → wait → read-journal
        pattern (`fire`, `wait_for_pass_to_end`, `journal_cursor`,
        `read_journal`); use it to run the daily pass and then the minute pass,
        and read back from the journal: the trailing-phase completion line,
        the symbol count, and any `QUOTA_EXHAUSTED` / `PROVIDER_UNAVAILABLE`
        abort (SC6).
  - [ ] **One firing is not enough, and the task must say so.** The trailing
        phase is bounded to one chunk per symbol per cycle (file 1, Task 3.4)
        and takes the newest actionable gap, so a symbol left with two
        in-window ranges by the repair needs more than one pass. Fire the
        minute pass repeatedly until `--verify` reports no pending trailing
        work, bounded by the quota — a `QUOTA_EXHAUSTED` outcome ends the loop
        and is reported, not retried.
  - [ ] Finish by running `repair_921_minute_sessions.py --verify` and
        printing its report, so the cutover's own output carries the SC3
        numbers.
  - [ ] Check-then-act throughout, and log every action it takes — the script
        runs with root privileges and must be auditable afterward.
  - [ ] Success: the whole cutover is one command producing one report,
        including the post-firing measurements; no step asks the PM to run
        something by hand or to come back tomorrow.
- [ ] **Task 7.4: Tests for the cutover script** (effort: 2)
  - [ ] Following `test_cutover_267.py`: each precondition failure aborts
        before any write; the happy path calls the repair with `--apply`
        exactly once, then fires each pass once, then calls `--verify`.
  - [ ] Success: `uv run pytest test/unit -q -k cutover_921` passes.
- [ ] **Task 7.5: Runbook and CHANGELOG** (effort: 2)
  - [ ] Runbook 100 gains the truncation-signature query,
        `repair_921_minute_sessions.py --check` and `--verify` as the standing
        diagnostics for this failure class, the `minute session mass` check's
        meaning and floors, and the manual invocation for the Task 5.8 load
        test.
  - [ ] CHANGELOG entry covering the range-end fix, the accounting rule, the
        two-phase cycle, the health check, and the removal of the quota floor
        check.
  - [ ] Success: a reader who has never seen this slice can run the
        diagnostics from the runbook alone.
- [ ] **Task 7.6: Pre-release checkpoint** (effort: 1)
  - [ ] **Commit before tagging.** The cutover script's first step is
        `install-production.sh --ref v0.14.0`, which installs from a checkout
        at that ref — so the script, its tests, and the CHANGELOG entry must
        be in the commit the tag points at.
  - [ ] Unit tier and mypy green; `ruff format` scoped to touched files.
  - [ ] Commit: `feat: add the 921 cutover script and changelog entry`.
  - [ ] Success: nothing from Sections 5–7 is uncommitted when Task 7.7 runs.
- [ ] **Task 7.7: Release and cutover** (effort: 2)
  - [ ] Full test tiers green, version bumped, tagged, released.
  - [ ] Run the cutover script; it applies the repair and fires both passes,
        so the acceptance numbers are in its report rather than a day away.
        The one timing constraint is the quota reset — run it after 00:00 UTC
        so the firings it triggers have budget.
  - [ ] Success: the report shows the repair applied, both passes fired, and
        the `--verify` measurements.
- [ ] **Task 7.8: Confirm the acceptance criteria from the cutover report**
      (effort: 2)
  - [ ] **Know which judged session is being read.** The cutover runs just
        after 00:00 UTC, when the health check's judged session is still D-2
        (D-1's collecting firing at 01:05 plus the 3 h lag has not passed).
        Read the mass figure for the session the fired passes actually
        collected, and state that in the report — otherwise `--verify` shows a
        sub-1,000,000 mass for an uncollected session and this task stops on a
        false failure.
  - [ ] From the Task 7.7 report, confirm: truncated symbol-days over the last
        five NYSE sessions = 0; bars for the judged session ≥ 1,000,000 (SC3);
        `OK minute session mass` with the measured line; the journal shows
        `trailing phase complete: N symbols` before any backfill line (SC6).
  - [ ] The one genuinely time-bound observation — at least one `healthy`
        production run after 23:00 UTC (SC7) — is recorded as a follow-up
        note against the issue, not as a task blocking the slice. Make it an
        explicit named artifact: a comment on #19 stating the outstanding SC7
        clause and the check-back instruction, so the slice cannot close with
        it silently lost.
  - [ ] If any criterion misses, record the measurement and stop — do not
        apply a speculative fix without the actual evidence.
  - [ ] Success: every measurement recorded in the slice notes.
- [ ] **Task 7.9: Close issues #19 and #20** (effort: 1)
  - [ ] Close #19 with the root cause (session-open range end), the fix, and
        the before/after measurements from Tasks 6.8 and 7.8.
  - [ ] Close #20 with the cagg-freshness verification already measured
        (every cagg fresh, migrations 053/054 applied), noting the optional
        recompression as deferred (design Out of scope).
  - [ ] Success: both issues closed with measurements, not assertions (SC8).
- [ ] **Task 7.10: Section 7 checkpoint** (effort: 1)
  - [ ] Commit: `docs: record the 921 cutover measurements and close #19/#20`.
  - [ ] Success: slice branch ready to merge into the integration target.

## Review Response (2026-09-09, tasks review part 2 — CONCERNS)

| Finding | Change |
|---|---|
| F001 only checkpoint lands after the release | New Task 7.6 commits the cutover script, its tests, and the CHANGELOG **before** Task 7.7 tags — the script installs from the tagged ref. Task 7.10 keeps only the measurements and closeout. |
| F002 repair only ever tested against a fake writer | New Task 6.7: an integration-tier test over `MT_TIMESCALE_TEST_URL` seeds a production-shaped `data_gaps` fixture spanning the window boundary, runs `--apply` twice, and asserts row-level equality plus byte-identical pre-window rows. |
| F003 window-straddling rows neither reset nor excluded | New Task 6.3: `_delete_intersecting`'s SQL is containment despite its name, so a row starting before the window and ending inside survives while the seed re-inserts the same sessions. The behavior must be decided and recorded; `--check` counts them (Task 6.4); `_delete_intersecting`'s shared semantics are not changed. |
| F004 Tasks 8.4/8.5 wait-blocked | New Task 7.1 adds `--verify`; Task 7.3 fires both passes via the `cutover_common` journal pattern; Task 7.8 reads the acceptance numbers from the cutover's own report. Only the "healthy run after 23:00 UTC" observation remains, as a recorded follow-up rather than a blocking task. |
| F005 no load-tier task for the read bound | New Task 5.8: `test/load/test_921_minute_session_mass_nfr.py` over `prod_shaped_db`, with the CI gap (slice 907) stated and a documented manual invocation instead of an assumed CI job. |
| F006 timer rendering unspecified and untestable | Task 5.9 keeps the unit file authoritative and makes the guard a unit test asserting the timer's parsed `OnCalendar` equals `MINUTE_PASS_FIRING_TIMES_UTC`, with the deviation from the design's "rendered into the unit" wording recorded. |
| F007 nothing fetches candidate sessions; no empty verdict | Task 5.4 owns the `trading_sessions` read; Task 5.2 states the no-candidate verdict as an explicit non-OK or exit 2, never a silent pass. |
| F008 floor stated as both values | Task 5.5 states the floor is computed and that 1,000,000 is a separate, stricter cutover bar that must not reach `health.py`; Task 7.1 repeats the distinction. |
| F009 Section 6 batched its tests | Split: Task 5.3 tests the selector, Task 5.7 the rule and rendering; Section 6 keeps unit tests (6.6) and adds the integration test (6.7). |

## Review Response (2026-09-09, tasks re-review part 2 — CONCERNS)

| Finding | Change |
|---|---|
| F001 load fixture cannot exercise the read | Task 5.8 states why `prod_shaped_db` as seeded (one bar per symbol per 7-day bucket from 2010, no `trading_sessions` at all) leaves the window empty, and requires a fixture with NYSE sessions plus a dense recent session (~41k cagg rows across ~11k symbols) and an explicit budget number. |
| F002 SC3 unreachable in one firing | Task 7.3 fires the minute pass until `--verify` reports no pending trailing work, bounded by quota; Task 7.8 states that the cutover's judged session is D-2 just after 00:00 UTC and that the report must name the session the fired passes collected, so a false failure is not mistaken for a broken fix. |
| F003 `--verify` re-measures instead of reusing | Task 7.1 requires importing the health check's judged-session selector, mass query, and rule function; Task 7.2 asserts the reuse by patching them. |
| F004 `--verify` beyond the design's script contract | Design component table updated to `--check` / `--apply` / `--verify` with the reason recorded. |
| F005 timer deviation not in the design | Design component table updated: the unit file stays authoritative and a `configparser` guard test holds it in agreement with the constant, with the reason recorded. |
| F006 SC7 observation has no owning artifact | Task 7.8 makes it an explicit comment on #19 stating the outstanding clause and the check-back instruction. |
| F007 Task 6.7 unnamed file/fixture | Now names `test/integration/test_repair_921_live.py` and `migrated_db`, citing `test_gaps_window_sql.py` as the `data_gaps` precedent. |
