---
docType: tasks
slice: minute-acquisition-correctness
project: trading-data
lld: user/slices/921-slice.minute-acquisition-correctness.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [919]
interfaces: [162, 165, 912]
projectState: >
  Design 921 committed at e57e1c6 (round-2 review CONCERNS, passes the gate);
  archived at affecb2. Measured read-only on production 2026-09-07: the minute
  universe collects ~45k bars/day for ~10.8k symbols against ~2.0M/day through
  2026-08-27; every minute gap row seeded since 2026-08-31 ends at a session
  open (70,298 UNKNOWN, 42,775 zero-width); `mt data health` still reports
  minute data OK. Issue #20 (caggs) is fixed in production and needs only
  closeout. Version 0.13.0 on main.
dateCreated: 20260908
dateUpdated: 20260908
status: not_started
---

## Context Summary

- Working on **921 minute-acquisition-correctness**, file 1 of 2: the range-end
  fix, the provider window, attempt accounting, the provider-failure policy,
  and the two-phase minute cycle. File 2 covers the health check, the repair
  and cutover scripts, and issue closeout. Task numbers referenced from file 2
  continue this numbering.
- Source of truth: `user/slices/921-slice.minute-acquisition-correctness.md`.
  Tasks cite its numbered Technical Scope items (Scope 1–5), Technical
  Decisions (Decision 1–7), and Success Criteria (SC1–SC8). Read the design
  section before starting each task section.
- **Root cause in one line:** `group_sessions_into_ranges` ends every range at
  the last missing session's `session_open_utc`, `_do_minute_symbol` passes
  that value to EODHD as `to`, and EODHD honors `to` exactly — so a nightly
  trailing session is fetched as a one-minute window and returns one bar.
- Phase 6 slice work: branch `921-slice.minute-acquisition-correctness`, forked
  from the integration target (`cf config get git.integration_branch`, `main`
  if empty). Verify the branch before Task 1.1.
- Code the tasks touch, all read during breakdown:
  - `data/gaps/minute_coverage.py` — `compute_missing_minute_sessions` calls
    `fetch_sessions` + `group_sessions_into_ranges`.
  - `data/gaps/compute_missing_ranges.py` — `fetch_sessions` currently selects
    **only** `session_open_utc`; the close is not fetched anywhere today.
    `group_sessions_into_ranges` is shared with the daily path and is not
    modified (Scope 1).
  - `data/acquisition/daemon/minute.py` — `run_minute_cycle` (~line 107),
    `_process_minute_symbol` (~226), `_do_minute_symbol` and its chunk loop
    (~285–520), `_advance_minute_gap` (~636).
  - `data/gaps/update_data_gaps.py` — the seed path at Step 5 increments
    `attempt_count` and promotes to `RETRY_EXHAUSTED` at `MAX_RETRY_COUNT`
    **with no provider response involved**. This is the measured 2026-09-07
    promotion path; Task 3.1 confirms it with a test before changing it.
  - `data/acquisition/outcomes.py` — `classify_outcome` already **raises**
    `ProviderResponseError` on 402 before any accounting, and
    `_process_minute_symbol` catches it and returns `TRANSIENT_FAILURE` for
    that symbol. So 402 already writes no gap row; what is missing is the
    **abort** (today the cycle continues to the next symbol).
  - `data/gaps/actionable_gap_selector.py` — `pick_most_recent_actionable_gap`.
- Test layout: `test/unit/data/gaps/`, `test/unit/data/acquisition/daemon/`
  (with `conftest.py` and `_harness.py`), `test/unit/cli/commands/`,
  `test/unit/deploy/test_units.py`. Unit tests here mock the psycopg
  connection (see `test_minute_coverage.py` for the established pattern); no
  database is required. Integration-tier tests that do need one read
  `MT_TIMESCALE_TEST_URL` (export from `.env`, strip quotes).
- Rules in force for this slice: no magic values — every threshold, window,
  and firing time is one named constant in `constants.py`; no silent
  fallbacks; every `except` re-raises, handles a named exception with a
  reason, or is a process boundary. Scope `ruff format` to files touched by
  the current change (`git diff --name-only <target>`), never to `src`.
- Commit checkpoint at the end of each section. Run
  `uv run pytest test/unit -q` plus `uv run mypy` over the touched source and
  test paths in one invocation before each checkpoint.
- Delivers (file 1): minute ranges that end at the session close, a provider
  window that reaches the end of the day, accounting that moves only on a
  provider answer, a pass that stops on 402 or a failure storm, and a trailing
  phase that guarantees the current session before any backfill.
- Next planned slice after 921: none queued; PM decides.

## Section 1: Range end conforms to the 140 contract

Design *Scope 1*, *Decision 1*, *SC1*. The minute range must end at
`session_close_utc` of its last missing session. `group_sessions_into_ranges`
and the daily path are **not** modified — the change lives in
`compute_missing_minute_sessions`, the minute caller.

- [ ] **Task 1.1: Fetch session closes alongside opens** (effort: 2)
  - [ ] `fetch_sessions` in `data/gaps/compute_missing_ranges.py` returns a
        list of `session_open_utc` values only. Add a sibling that returns the
        open→close mapping for the same window and calendar join, so the
        minute caller can resolve a close without a second query per range.
  - [ ] Keep `fetch_sessions` and its return type unchanged — the daily path
        and `compute_missing_ranges` call it and must not move.
  - [ ] The new function selects `session_open_utc, session_close_utc` from
        `trading_sessions` joined to `instruments` on `trading_calendar_id`,
        ordered by open, with the same bind-parameter treatment as
        `fetch_sessions` (no interpolation).
  - [ ] Success: the new function exists with a docstring stating that the
        minute range end is the close per 140 step 6; `fetch_sessions`'s
        signature and body are byte-identical to before.
- [ ] **Task 1.2: `compute_missing_minute_sessions` ends ranges at the close**
      (effort: 2)
  - [ ] After `group_sessions_into_ranges` returns, rewrite each `GapRange`'s
        `gap_end_utc` from the last missing session's open to that session's
        `session_close_utc`, using the mapping from Task 1.1.
  - [ ] A session whose close is missing from the mapping (a calendar row with
        a null close) must fail explicitly — log at ERROR and drop that range,
        never fall back to the open. Silently keeping the open reintroduces
        the defect this slice exists to fix.
  - [ ] `gap_start_utc` stays the first missing session's `session_open_utc`.
  - [ ] Success: for one missing session the returned range is
        `[open, close]` of that session; for a contiguous run it is
        `[open(first), close(last)]`; the grouping/contiguity behavior is
        otherwise unchanged.
- [ ] **Task 1.3: Tests for the range end** (effort: 2)
  - [ ] Extend `test/unit/data/gaps/test_minute_coverage.py` following its
        existing mocked-connection pattern.
  - [ ] Fixture rows must be real `trading_sessions` values in both DST
        regimes: 13:30/20:00 UTC in summer and 14:30/21:00 UTC in winter
        (SC1). A fixture that only uses one regime does not prove the close
        is read rather than computed as open + a fixed offset.
  - [ ] Cases: single missing session; a contiguous run of three; two runs
        separated by a covered day; an early-close session (a shorter
        open→close span in the fixture) ending at its real close; a null
        close logging at ERROR and dropping the range.
  - [ ] Success: `uv run pytest test/unit/data/gaps -q` passes; a test asserts
        the exact `gap_end_utc` value against the fixture's close, not against
        an offset from the open.
- [ ] **Task 1.4: Provider window reaches the end of the day** (effort: 2)
  - [ ] In `_do_minute_symbol`'s chunk loop (`data/acquisition/daemon/minute.py`),
        the EODHD URL's `to` becomes the UTC midnight **after** `chunk_end`'s
        date, so extended-hours bars keep landing (AAPL 2026-08-27 traded
        08:00–23:59 UTC). Scope 1: this is a fetch-layer mapping.
  - [ ] `chunk_end` itself, `_advance_minute_gap`'s arguments, the range passed
        to `classify_outcome`, and the trailing-tolerance comparison are
        unchanged — only the value serialized into the URL moves.
  - [ ] The day-end helper is a named module-level function with a docstring
        explaining why the request window exceeds the gap window; do not
        inline the arithmetic at the URL.
  - [ ] Success: for `chunk_end = 2026-09-03 20:00 UTC` the URL's `to` is the
        epoch of `2026-09-04 00:00 UTC`; `from` is still `chunk_start`'s epoch.
- [ ] **Task 1.5: Tests for the provider window** (effort: 2)
  - [ ] Add to `test/unit/data/acquisition/daemon/test_minute.py`, using the
        harness already in that directory.
  - [ ] Capture the built URL and assert both epoch parameters (SC1). Cases:
        a trailing single-session gap; a multi-day chunk; a chunk whose
        `chunk_end` is already at midnight (the day-end helper must not add a
        second day).
  - [ ] Assert `classify_outcome` receives the un-extended `[chunk_start,
        chunk_end]` range, so classification semantics do not shift with the
        request window.
  - [ ] Success: `uv run pytest test/unit/data/acquisition/daemon -q` passes.
- [ ] **Task 1.6: Section 1 checkpoint** (effort: 1)
  - [ ] Run `uv run pytest test/unit -q` and `uv run mypy` over the touched
        source and test paths in a single invocation.
  - [ ] `ruff format` the files this section touched only.
  - [ ] Commit: `fix: end minute gap ranges at the session close (921)`.
  - [ ] Success: unit tier green, working tree clean.

## Section 2: Consumers of the moved `gap_end`

Design *"Consumers of minute `gap_end`"* table. The row's end moves from 13:30
to 20:00 UTC on the same date. The design's analysis says every consumer is
unaffected; this section proves it rather than assuming it.

- [ ] **Task 2.1: Coalescer adjacency regression test** (effort: 1)
  - [ ] `coalesce_data_gaps._are_adjacent` compares
        `next_trading_session_after(prev.gap_end.date())` with
        `current.gap_start.date()` — dates only.
  - [ ] Add a case to `test/unit/data/gaps/test_coalesce_data_gaps.py` with
        two consecutive-session minute rows ending at session closes, and
        assert they still coalesce.
  - [ ] Success: the new case passes; it fails if `gap_end` is moved to the
        next UTC midnight (record that in the test's docstring — it is why the
        range end is the close and not midnight, Decision 1).
- [ ] **Task 2.2: Selector and frontier-gate regression tests** (effort: 1)
  - [ ] `pick_most_recent_actionable_gap` filters `gap_end <= to_ts` with
        `to_ts = now_midnight`; a 20:00 end must still be selected.
  - [ ] `_do_minute_symbol`'s frontier gate compares
        `MAX(gap_end) < target_end`; a 20:00 frontier must still trigger the
        trailing seed.
  - [ ] Add both cases to the existing selector and minute daemon test files.
  - [ ] Success: both pass without changing the production code under test.

## Section 3: No response, no accounting

Design *Scope 3* (first rule), *Decision 4*, *SC4*. `attempt_count` and
`fetch_status` move only on a classified provider response (HTTP 200 or 404).
Confirm the measured promotion path with a failing test **before** changing
anything.

- [ ] **Task 3.1: Reproduce the 2026-09-07 promotion path** (effort: 3)
  - [ ] Read `update_data_gaps.py` Step 5: `attempt_count = prior_count + 1`
        and promotion to `RETRY_EXHAUSTED` at `MAX_RETRY_COUNT` happen on the
        **seed**, with no provider call in the transaction.
  - [ ] Write a test in `test/unit/data/gaps/test_update_data_gaps.py` that
        seeds the same range four times with no fetch between seeds and
        asserts the current (wrong) behavior: the row reaches
        `RETRY_EXHAUSTED` with `attempt_count == MAX_RETRY_COUNT`. Mark it
        with a docstring naming the 7,755 rows promoted on 2026-09-07.
  - [ ] Do not fix anything in this task. Its output is the evidence that the
        seed path — not `_advance_minute_gap` — is the accounting defect.
  - [ ] Success: the test passes against unmodified code, documenting the
        defect; the design's open question ("the seed carry-forward or
        `_advance_minute_gap`'s promotion") is answered in the test docstring.
- [ ] **Task 3.2: Seeding no longer consumes retries** (effort: 3)
  - [ ] Change the seed path so re-seeding a range that was never answered by
        the provider carries the prior `attempt_count` forward **unchanged**
        instead of incrementing it, and therefore cannot promote to
        `RETRY_EXHAUSTED`.
  - [ ] Promotion to `RETRY_EXHAUSTED` on attempt exhaustion stays where a
        provider answer is in hand — `_advance_minute_gap`'s non-success
        branch, which already runs only after `classify_outcome` on a real
        response.
  - [ ] `update_data_gaps`'s signature, `UpdateResult` fields, and the daily
        path's use of it must keep working; if the daily path depends on the
        increment, gate the new behavior on granularity explicitly with a
        comment rather than changing daily silently.
  - [ ] Success: the Task 3.1 test is inverted (four seeds leave the row
        UNKNOWN at its original count) and every existing
        `test_update_data_gaps.py` case still passes.
- [ ] **Task 3.3: Tests for accounting on each failure mode** (effort: 3)
  - [ ] In `test/unit/data/acquisition/daemon/test_minute.py`, one test per
        failure mode — HTTP 402, HTTP 500, a read timeout, and a connection
        reset — each asserting that **no** `data_gaps` row's `attempt_count`,
        `fetch_status`, or `last_attempt_ts` changed, and that
        `acquisition_state.last_attempt_outcome` / `last_attempt_ts` are
        likewise untouched for the un-answered symbol (SC4, Scope 3's 912
        clause).
  - [ ] One test asserts the converse: a classified 200 and a 404 **do** move
        `attempt_count` exactly as today, so the rule did not disable
        accounting.
  - [ ] Success: `uv run pytest test/unit/data/acquisition/daemon -q` passes;
        SC4 is demonstrable from the test names.
- [ ] **Task 3.4: Section 3 checkpoint** (effort: 1)
  - [ ] Unit tier and mypy green; `ruff format` scoped to touched files.
  - [ ] Commit: `fix: move minute gap accounting only on a provider answer (921)`.

## Section 4: 402 abort, failure breaker, and the pass outcome

Design *Scope 3*, *Decision 4*, *Decision 7*, *SC5*. Today a 402 raises
`ProviderResponseError`, is caught per symbol, and the cycle walks the
remaining 13,000 symbols one at a time. Nothing useful happens after the first
402.

- [ ] **Task 4.1: `MinutePassOutcome` enum** (effort: 1)
  - [ ] Add `MinutePassOutcome` as a `StrEnum` beside `LastAttemptOutcome` in
        `data/acquisition/state.py`, with members `COMPLETE`,
        `QUOTA_EXHAUSTED`, `PROVIDER_UNAVAILABLE`.
  - [ ] No string literal for these values may appear anywhere else — journal
        lines, exit mapping, and tests all reference the enum (Decision 7,
        the F007 finding).
  - [ ] Success: `test/unit/data/acquisition/test_state.py` covers the members
        and their string values; `grep` for the literal strings outside
        `state.py` returns only the enum definition.
- [ ] **Task 4.2: Constants for the failure policy** (effort: 1)
  - [ ] Add to `constants.py`, each with a docstring giving its measurement or
        precedent: `MINUTE_TRAILING_PRIORITY_WINDOW` (7 days),
        `MINUTE_PASS_MAX_CONSECUTIVE_PROVIDER_FAILURES` (5, following
        `PULL_MAX_CONSECUTIVE_PROVIDER_ERRORS`).
  - [ ] Success: `test/unit/test_constants.py` asserts the values and that
        they are the types stated (timedelta, int).
- [ ] **Task 4.3: 402 aborts the pass** (effort: 2)
  - [ ] `classify_outcome` raises `ProviderResponseError` for 402 with a
        distinct message. Give the quota case a distinguishable exception (a
        subclass, or a typed attribute — not a message-substring check, which
        is a fragile label) so `run_minute_cycle` can tell quota exhaustion
        from a vendor-contract 4xx.
  - [ ] `_process_minute_symbol` must let that exception reach
        `run_minute_cycle` rather than converting it to
        `TRANSIENT_FAILURE`; the cycle ends the pass with
        `MinutePassOutcome.QUOTA_EXHAUSTED`.
  - [ ] Other non-404 4xx keep today's per-symbol skip behavior.
  - [ ] Success: a 402 on the first symbol ends the pass immediately with no
        further provider calls (SC5).
- [ ] **Task 4.4: Consecutive-failure breaker** (effort: 2)
  - [ ] `run_minute_cycle` counts consecutive symbols whose outcome was a
        provider failure (5xx, timeout, connection reset, 429 exhaustion) and
        aborts with `MinutePassOutcome.PROVIDER_UNAVAILABLE` at
        `MINUTE_PASS_MAX_CONSECUTIVE_PROVIDER_FAILURES`.
  - [ ] The counter resets on any symbol that reaches a classified provider
        response, so scattered failures across a long pass never trip it.
  - [ ] Database-side failures (pool timeout, advisory-lock timeout) are not
        provider failures and must not increment the counter.
  - [ ] Success: five consecutive 5xx symbols end the pass; four failures
        followed by a success and then four more do not.
- [ ] **Task 4.5: Journal line and exit mapping** (effort: 2)
  - [ ] On abort, log one line naming the outcome, the phase it aborted in,
        and the count of symbols not attempted (Scope 3).
  - [ ] Exit mapping in the pass entry point: `COMPLETE` → 0;
        `QUOTA_EXHAUSTED` after the trailing phase completed → 0 (spending the
        allowance on backfill is the designed steady state);
        `QUOTA_EXHAUSTED` inside the trailing phase → 3;
        `PROVIDER_UNAVAILABLE` → 3 in either phase.
  - [ ] Exit 3 is the project's partial-pass convention (the Kalshi pass and
        runbook 100); reuse the existing exit-code constant rather than a new
        literal.
  - [ ] An aborted pass stamps `RunnerState.last_minute_cycle_end_utc` exactly
        as a completed pass does — it is an in-process busy-loop guard only,
        and slice 912 derives remaining work from `acquisition_state`
        (Scope 3's 912 clause). Add a comment citing 912 at the stamp.
  - [ ] Success: the mapping is a single function or table, not scattered
        conditionals.
- [ ] **Task 4.6: Tests for abort, breaker, and exit codes** (effort: 3)
  - [ ] Cases: first-response 402 → `QUOTA_EXHAUSTED`, no second provider
        call; five consecutive 5xx → `PROVIDER_UNAVAILABLE`; four-fail /
        success / four-fail → pass completes; trailing-phase 402 → exit 3;
        post-trailing 402 → exit 0; `PROVIDER_UNAVAILABLE` in either phase →
        exit 3 (SC5).
  - [ ] One test asserts the aborted pass stamped the cycle end and left
        `acquisition_state` rows for un-attempted symbols untouched.
  - [ ] Success: `uv run pytest test/unit/data/acquisition -q` passes.
- [ ] **Task 4.7: Section 4 checkpoint** (effort: 1)
  - [ ] Unit tier and mypy green; `ruff format` scoped to touched files.
  - [ ] Commit: `feat: abort the minute pass on quota exhaustion or a failure storm (921)`.

## Section 5: Two-phase minute cycle

Design *Scope 3*, *SC6*. The trailing phase guarantees the current session
lands before any backfill chunk is requested.

- [ ] **Task 5.1: `min_gap_end` filter on the gap selector** (effort: 1)
  - [ ] `pick_most_recent_actionable_gap` gains an optional `min_gap_end`
        parameter that adds a `gap_end >= %s` predicate. Existing callers pass
        nothing and behave identically (an additive, backward-compatible
        signature change, recorded in the design's Interfaces Required).
  - [ ] Success: `test/unit/data/gaps/test_actionable_gap_selector.py` covers
        the filtered and unfiltered forms.
- [ ] **Task 5.2: Trailing phase** (effort: 3)
  - [ ] `run_minute_cycle` walks the active universe once with
        `min_gap_end = now - MINUTE_TRAILING_PRIORITY_WINDOW` and **one chunk
        per symbol**, then walks it again for backfill with today's
        `most_stale_first` ordering and no `min_gap_end`.
  - [ ] The one-chunk bound belongs to the trailing phase, not to
        `_do_minute_symbol` generally — pass it explicitly so the backfill
        phase and `run_minute_refetch` are unchanged.
  - [ ] The coverage index is built once and shared by both phases.
  - [ ] `should_continue` (the SIGTERM hook) is honored between symbols in
        both phases and between the phases.
  - [ ] Log `trailing phase complete: N symbols` before the first backfill
        line (SC6).
  - [ ] Success: no backfill chunk is requested until every active symbol's
        trailing gap has been attempted.
- [ ] **Task 5.3: Tests for phase order** (effort: 2)
  - [ ] A test with a symbol set holding both trailing and old gaps asserts
        the request order: every trailing request precedes every backfill
        request (SC6).
  - [ ] A test asserts the trailing phase issues at most one chunk per symbol
        even when a symbol has several actionable trailing gaps.
  - [ ] A test asserts a 402 during the trailing phase aborts before the
        backfill phase starts, and that the outcome carries the phase.
  - [ ] Success: `uv run pytest test/unit/data/acquisition/daemon -q` passes.
- [ ] **Task 5.4: Section 5 checkpoint** (effort: 1)
  - [ ] Unit tier and mypy green; `ruff format` scoped to touched files.
  - [ ] Commit: `feat: run the minute cycle in trailing then backfill phases (921)`.
  - [ ] Continue with file 2, Section 6 (health check).
