---
docType: review
layer: project
reviewType: tasks
slice: minute-acquisition-correctness
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260909
dateUpdated: 20260909
reviewedSha: 0110652f6cedbde18062c2518ad6f56ebfe12fd1
findings:
  - id: F001
    severity: concern
    category: test-coverage
    summary: "Load-tier test names a fixture that cannot exercise the session-mass read"
    location: "test/load/conftest.py:89-112"
  - id: F002
    severity: concern
    category: correctness
    summary: "SC3 is defined after two nightly firings; the cutover fires once and Task 7.8 treats the result as final"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:361-424"
  - id: F003
    severity: concern
    category: duplication
    summary: "`--verify` re-measures session mass without being told to reuse the health check's code"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:345-357"
  - id: F004
    severity: note
    category: scope
    summary: "`--verify` is an addition beyond the design's script specification"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:196-200"
  - id: F005
    severity: note
    category: traceability
    summary: "Timer-rendering deviation is recorded in the tasks but not in the design"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:243"
  - id: F006
    severity: note
    category: acceptance
    summary: "SC7's production-observation clause has no owning task"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:420-422"
  - id: F007
    severity: note
    category: completability
    summary: "Task 6.7 does not name its test file or fixture"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:309-328"
  - id: F008
    severity: pass
    category: coverage
    summary: "Success-criteria coverage is complete and traceable across both files"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:60-70"
  - id: F009
    severity: pass
    category: correctness
    summary: "Task 6.3's code claim is accurate and the risk is real"
    location: "src/manta_trading/data/gaps/update_data_gaps.py:295-313"
  - id: F010
    severity: pass
    category: ci
    summary: "Load-test CI gating is explicit, not assumed"
    location: ".github/workflows/ci.yml:1-8"
  - id: F011
    severity: pass
    category: sequencing
    summary: "Commit checkpoints are distributed and the pre-tag ordering is correct"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:399-405"
---

# Review: tasks — slice 921

**Verdict:** CONCERNS
**Model:** claude-opus-5

## Findings

### [CONCERN] Load-tier test names a fixture that cannot exercise the session-mass read

Task 5.8 (tasks file line 183) specifies `test/load/test_921_minute_session_mass_nfr.py` over `prod_shaped_db`, asserting the mass read "completes well inside `HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT`". `_seed_prod_shape` seeds minute bars at `datetime(FIRST_YEAR + y, 1, 2, 14, 31) + COVERAGE_BUCKET_INTERVAL * b` — one bar per symbol per 7-day bucket, `FIRST_YEAR = 2010`, `YEAR_COUNT = 10` (conftest.py:56-64, 100-112), with `COVERAGE_BUCKET_INTERVAL = timedelta(days=7)` (constants.py:366). No load-tier fixture seeds `trading_sessions` at all (grep over `test/load/*.py` returns nothing).

Failure scenario: the mass query filters `minute_4hour_ohlcv` buckets to `[session_open_utc, session_close_utc)` of a judged session. Against `prod_shaped_db` no recent session exists in the calendar, and even a 2010-era seeded date yields at most one bar per symbol in the window — nowhere near the ~41k cagg rows per session the slice's read bound is about. The test passes in milliseconds over an effectively empty window, the NFR is never measured, and a real regression in the grouped read ships green. The task must name what the fixture needs (a dense recent session across ~11k symbols plus NYSE `trading_sessions` rows — a new fixture or an extension to `prod_shaped_db`) and state an explicit budget number, as `test_169_coverage_freshness_probe_nfr.py` does.

### [CONCERN] SC3 is defined after two nightly firings; the cutover fires once and Task 7.8 treats the result as final

The slice states SC3 as "After the repair and two nightly firings: truncated symbol-days … for the last five NYSE sessions = 0; bars for the judged session ≥ 1,000,000." Task 7.3 fires the daily pass and then the minute pass once each; Task 7.8 confirms SC3 from that single report and says to "stop" if a criterion misses. File 1's Task 3.4 bounds the trailing phase to **one chunk per symbol per cycle**, and the trailing selector takes the *newest* actionable gap.

Failure scenario: the repair re-seeds truncated days back to 2026-07-16. Where a non-truncated day interleaves the last five sessions, a symbol ends up with two or more separate in-window gap ranges; one fired minute pass fetches only the newest, so "truncated symbol-days over the last five sessions = 0" is unreachable in one firing. Compounding it, the cutover runs just after 00:00 UTC, at which time the health check's judged session is D-2 (D-1's collecting firing, 01:05 + 3 h, has not passed) — a session the just-fired trailing pass may not have reached, so `--verify` reports a sub-1,000,000 mass and Task 7.8 stops on a false failure. Either have the cutover fire the minute pass until `--verify` shows no pending trailing work (bounded by quota/`QUOTA_EXHAUSTED`), or state in Task 7.8 which residual is expected after one firing and how it is distinguished from a broken fix.

### [CONCERN] `--verify` re-measures session mass without being told to reuse the health check's code

Task 7.1 has `--verify` print "bars and symbols-with-≥30-bars for the judged session" and "the `minute session mass` health line", but nowhere says these must come from Task 5.4's query and Task 5.5's rule function. Task 7.2's test only asserts the right *bar* (1,000,000 vs the computed 975,000 floor), not the shared implementation.

Failure scenario: a junior implementer writes a second `SUM(minute_count)` query in `scripts/repair_921_minute_sessions.py` with its own judged-session logic. The two drift — e.g. the script's window is inclusive of `session_close_utc` while `health.py` uses `[open, close)`, or the script keeps a NYSE literal after the calendar constant changes — and the cutover report certifies SC3/SC7 with a number `mt data health` does not reproduce. This is the DRY rule in CLAUDE.md and exactly the "two implementations of one measurement" trap. Task 7.1 should require importing the health check's judged-session selector, query, and rule function, and Task 7.2 should assert the reuse.

### [NOTE] `--verify` is an addition beyond the design's script specification

The design's component table specifies `repair_921_minute_sessions.py` with `--check` / `--apply` only, and the Verification Walkthrough verifies the next morning with `--check` plus `mt data health`. Task 7.1 adds a third mode. The addition is well-motivated (it converts a wait into an action, per the no-wait-blocked-tasks rule) and the Review Response table records it under F004, but the design's script contract still reads two modes. Reconcile the design so the two documents do not diverge — the same class of drift the design's own round-2 F005 called out.

### [NOTE] Timer-rendering deviation is recorded in the tasks but not in the design

Design Scope 4 and the component table say `mt-minute-pass.timer`'s `OnCalendar` is "rendered from the constant by `install-production.sh`". Task 5.9 deliberately keeps the unit file authoritative and substitutes a `configparser` guard test, with a well-argued rationale (verified: `install-production.sh` installs units verbatim in a bash loop at lines 172-175, and `test/unit/deploy/test_units.py` parses the repo's unit files). The task says to "note the deviation", but the design text is unchanged. The timer's current values (01:05, 13:05 UTC) match Task 5.1's constant, so the guard is implementable as written; only the design needs the reconciliation.

### [NOTE] SC7's production-observation clause has no owning task

SC7 ends with "production records at least one `healthy` run after 23:00 UTC". Task 7.8 records this as a follow-up note against the issue rather than a blocking task. Given the no-wait-blocked-tasks rule this is the right call, and the cutover does exercise `mt data health` in production via `--verify` — but it means the slice can be marked complete with one SC clause outstanding. Make the follow-up an explicit named artifact (a line on the issue with a check-back instruction) so it is not lost at closeout.

### [NOTE] Task 6.7 does not name its test file or fixture

Task 6.6 names `test/unit/test_repair_921.py` and its style precedent; Task 6.7 says only "the repo's `MT_TIMESCALE_TEST_URL` fixture convention". The tier exists with usable fixtures (`test/integration/conftest.py` provides `ephemeral_db` / `migrated_db`, and `test_gaps_window_sql.py` is a `data_gaps` precedent), so nothing blocks the work — but a junior implementer has to discover both the path and the fixture name. Name them, as every other test task in the file does.

### [PASS] Success-criteria coverage is complete and traceable across both files

SC2 → Tasks 6.1–6.7 (each `--check` predicate, the advisory-lock and `force_reset_terminal` assertions, the idempotency proof); SC3 → 7.1/7.8; SC7 → 5.1–5.7 (45k FAIL, 1.99M PASS, 210-minute early-close PASS, the query-capture assertion against `minute_4hour_ohlcv`, the removed quota line); SC8 → 7.5/7.9. No task lacks a criterion: 5.6's quota removal is Decision 6, 5.9 is Scope 4's timer/check agreement, 6.3 is a prerequisite for SC2's idempotency claim. Verified against the codebase: `check_quota`, `fetch_quota`, and `HEALTH_EODHD_QUOTA_HEADROOM_MIN` occur only in `health.py:37,108,145,188-190` and `constants.py:96,101` plus tests and history docs, so Task 5.6's "removal is unconditional" claim holds.

### [PASS] Task 6.3's code claim is accurate and the risk is real

`_delete_intersecting`'s docstring says "Delete all data_gaps rows whose [gap_start, gap_end] intersects [from_ts, to_ts]" while its SQL is `gap_start >= %s AND gap_end <= %s` — containment, not intersection. A coalesced UNKNOWN row starting before `REPAIR_921_WINDOW_START` and ending inside the window survives the reset while the seeder re-inserts the same in-window sessions, producing overlapping rows and double-fetching against the rationed quota. Task 6.3 correctly makes this a decide-and-record task before 6.5 implements it, has `--check` count the rows (6.4), and explicitly forbids changing the shared helper's semantics in this slice.

### [PASS] Load-test CI gating is explicit, not assumed

Task 5.8 states that CI runs no test job and that the gate is a documented manual invocation recorded in the test module docstring and runbook 100 (Task 7.5). Verified: `.github/workflows/ci.yml` is `on: push: tags: ["v*"]` with a single `publish` job — no test execution anywhere in `.github/workflows/`. Adding a CI wiring task here would be scope creep against a repo-wide gap already tracked as slice 907; naming the gap and the manual invocation is the correct handling.

### [PASS] Commit checkpoints are distributed and the pre-tag ordering is correct

Checkpoints land at 5.10, 6.9, 7.6, and 7.10 — one per section, matching the project's confirmed per-section granularity, with each gated on unit tier + mypy + scoped `ruff format`. Task 7.6 correctly commits the cutover script, its tests, and the CHANGELOG *before* Task 7.7 tags, since `install-production.sh --ref v0.14.0` installs from the tagged checkout — the ordering defect from the previous round is fixed, and 7.6's success criterion ("nothing from Sections 5–7 is uncommitted when 7.7 runs") makes it checkable.
