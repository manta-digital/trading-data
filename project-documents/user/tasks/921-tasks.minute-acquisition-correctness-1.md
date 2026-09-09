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
dateUpdated: 20260909
status: in_progress
---

## Context Summary

- Working on **921 minute-acquisition-correctness**, file 1 of 2, in four
  sections: (1) the range end and provider window, with the `gap_end` consumer
  regressions folded in; (2) no-response-no-accounting; (3) the two-phase
  cycle; (4) the pass outcome, abort, and exit codes. File 2 continues at
  Section 5 with the health check, the repair and cutover scripts, and issue
  closeout.
- **Section order matters.** The two-phase cycle (Section 3) precedes the
  outcome and exit work (Section 4) because the exit mapping is defined by
  which phase aborted — there is nothing to condition on until the phases
  exist.
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
    promotion path; Task 2.1 confirms it with a test before changing it.
  - `data/acquisition/outcomes.py` — two different behaviors, and the
    difference drives Section 2. `classify_outcome` **raises**
    `ProviderResponseError` on 402 (and other non-404 4xx) before any
    accounting, so 402 already writes no gap row — what is missing there is
    the abort. But it **returns** `TRANSIENT_FAILURE` for 429, any 5xx, an
    unparseable body, and EODHD's 200-with-`{"error": …}` quirk, and the
    chunk loop then writes accounting for it. That second path is a live
    defect, not a design assumption.
  - `data/gaps/actionable_gap_selector.py` — `pick_most_recent_actionable_gap`.
  - `cli/commands/kalshi.py:48-58` — `EXIT_OK`/`EXIT_SYNC_PARTIAL`/… and
    `EXIT_BY_OUTCOME`, the precedent for Section 4's exit mapping. There is
    **no** project-wide exit-code constant; `cli/commands/data.py` uses
    per-command private ints.
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

- [x] **Task 1.1: Fetch session closes alongside opens** (effort: 2)
  - [x] `fetch_sessions` in `data/gaps/compute_missing_ranges.py` returns a
        list of `session_open_utc` values only. Add a sibling that returns the
        open→close mapping for the same window and calendar join, so the
        minute caller can resolve a close without a second query per range.
  - [x] Keep `fetch_sessions` and its return type unchanged — the daily path
        and `compute_missing_ranges` call it and must not move.
  - [x] The new function selects `session_open_utc, session_close_utc` from
        `trading_sessions` joined to `instruments` on `trading_calendar_id`,
        ordered by open, with the same bind-parameter treatment as
        `fetch_sessions` (no interpolation).
  - [x] Success: the new function exists with a docstring stating that the
        minute range end is the close per 140 step 6; `fetch_sessions`'s
        signature and body are byte-identical to before.
- [x] **Task 1.2: `compute_missing_minute_sessions` ends ranges at the close**
      (effort: 2)
  - [x] After `group_sessions_into_ranges` returns, rewrite each `GapRange`'s
        `gap_end_utc` from the last missing session's open to that session's
        `session_close_utc`, using the mapping from Task 1.1.
  - [x] A session whose close is missing from the mapping (a calendar row with
        a null close) must fail explicitly — log at ERROR and drop that range,
        never fall back to the open. Silently keeping the open reintroduces
        the defect this slice exists to fix.
  - [x] `gap_start_utc` stays the first missing session's `session_open_utc`.
  - [x] Success: for one missing session the returned range is
        `[open, close]` of that session; for a contiguous run it is
        `[open(first), close(last)]`; the grouping/contiguity behavior is
        otherwise unchanged.
- [x] **Task 1.3: Tests for the range end** (effort: 2)
  - [x] Extend `test/unit/data/gaps/test_minute_coverage.py` following its
        existing mocked-connection pattern.
  - [x] Fixture rows must be real `trading_sessions` values in both DST
        regimes: 13:30/20:00 UTC in summer and 14:30/21:00 UTC in winter
        (SC1). A fixture that only uses one regime does not prove the close
        is read rather than computed as open + a fixed offset.
  - [x] Cases: single missing session; a contiguous run of three; two runs
        separated by a covered day; an early-close session (a shorter
        open→close span in the fixture) ending at its real close; a null
        close logging at ERROR and dropping the range.
  - [x] Success: `uv run pytest test/unit/data/gaps -q` passes; a test asserts
        the exact `gap_end_utc` value against the fixture's close, not against
        an offset from the open.
- [x] **Task 1.4: Provider window reaches the end of the day** (effort: 2)
  - [x] In `_do_minute_symbol`'s chunk loop (`data/acquisition/daemon/minute.py`),
        the EODHD URL's `to` becomes the UTC midnight **after** `chunk_end`'s
        date, so extended-hours bars keep landing (AAPL 2026-08-27 traded
        08:00–23:59 UTC). Scope 1: this is a fetch-layer mapping.
  - [x] `chunk_end` itself, `_advance_minute_gap`'s arguments, the range passed
        to `classify_outcome`, and the trailing-tolerance comparison are
        unchanged — only the value serialized into the URL moves.
  - [x] The day-end helper is a named module-level function with a docstring
        explaining why the request window exceeds the gap window; do not
        inline the arithmetic at the URL.
  - [x] Success: for `chunk_end = 2026-09-03 20:00 UTC` the URL's `to` is the
        epoch of `2026-09-04 00:00 UTC`; `from` is still `chunk_start`'s epoch.
- [x] **Task 1.5: Tests for the provider window** (effort: 2)
  - [x] Add to `test/unit/data/acquisition/daemon/test_minute.py`, using the
        harness already in that directory.
  - [x] Capture the built URL and assert both epoch parameters (SC1). Cases:
        a trailing single-session gap; a multi-day chunk; a chunk whose
        `chunk_end` is already at midnight (the day-end helper must not add a
        second day).
  - [x] Assert `classify_outcome` receives the un-extended `[chunk_start,
        chunk_end]` range, so classification semantics do not shift with the
        request window.
  - [x] Success: `uv run pytest test/unit/data/acquisition/daemon -q` passes.

### Consumers of the moved `gap_end`

Design *"Consumers of minute `gap_end`"* table. The row's end moves from
13:30 to 20:00 UTC on the same date. The design's analysis says every
consumer is unaffected; these two tasks prove it rather than assuming it,
and ride into the same checkpoint.

- [x] **Task 1.6: Coalescer adjacency regression test** (effort: 1)
  - [x] `coalesce_data_gaps._are_adjacent` compares
        `next_trading_session_after(prev.gap_end.date())` with
        `current.gap_start.date()` — dates only.
  - [x] Add a case to `test/unit/data/gaps/test_coalesce_data_gaps.py` with
        two consecutive-session minute rows ending at session closes, and
        assert they still coalesce.
  - [x] Success: the new case passes; it fails if `gap_end` is moved to the
        next UTC midnight (record that in the test's docstring — it is why the
        range end is the close and not midnight, Decision 1).
- [x] **Task 1.7: Selector and frontier-gate regression tests** (effort: 1)
  - [x] `pick_most_recent_actionable_gap` filters `gap_end <= to_ts` with
        `to_ts = now_midnight`; a 20:00 end must still be selected.
  - [x] `_do_minute_symbol`'s frontier gate compares
        `MAX(gap_end) < target_end`; a 20:00 frontier must still trigger the
        trailing seed.
  - [x] Add both cases to the existing selector and minute daemon test files.
  - [x] Success: both pass without changing the production code under test.
- [x] **Task 1.8: Section 1 checkpoint** (effort: 1)
  - [x] Run `uv run pytest test/unit -q` and `uv run mypy` over the touched
        source and test paths in a single invocation.
  - [x] `ruff format` the files this section touched only.
  - [x] Commit: `fix: end minute gap ranges at the session close (921)`.
  - [x] Success: unit tier green, working tree clean.

## Section 2: No response, no accounting

Design *Scope 3* (first rule), *Decision 4*, *SC4*. `attempt_count` and
`fetch_status` move only on a classified provider response (HTTP 200 or 404).
There are **two** paths that violate this and each needs its own fix: the seed
in `update_data_gaps`, and the chunk loop's handling of a `TRANSIENT_FAILURE`
that `classify_outcome` returned rather than raised.

- [x] **Task 2.1: Reproduce the 2026-09-07 seed promotion path** (effort: 3)
  - [x] Read `update_data_gaps.py` Step 5: `attempt_count = prior_count + 1`
        and promotion to `RETRY_EXHAUSTED` at `MAX_RETRY_COUNT` happen on the
        **seed**, with no provider call in the transaction.
  - [x] Write a test in `test/unit/data/gaps/test_update_data_gaps.py` that
        seeds the same range four times with no fetch between seeds and
        asserts the current (wrong) behavior: the row reaches
        `RETRY_EXHAUSTED` with `attempt_count == MAX_RETRY_COUNT`. Mark it
        with a docstring naming the 7,755 rows promoted on 2026-09-07.
  - [x] Do not fix anything in this task. Its output is the evidence that the
        seed path is one of the two accounting defects.
  - [x] Success: the test passes against unmodified code, documenting the
        defect.
- [x] **Task 2.2: Seeding no longer consumes retries** (effort: 3)
  - [x] Change the seed path so re-seeding a range that was never answered by
        the provider carries the prior `attempt_count` forward **unchanged**
        instead of incrementing it, and therefore cannot promote to
        `RETRY_EXHAUSTED`.
  - [x] `update_data_gaps`'s signature, `UpdateResult` fields, and the daily
        path's use of it must keep working; if the daily path depends on the
        increment, gate the new behavior on granularity explicitly with a
        comment rather than changing daily silently.
  - [x] Success: the Task 2.1 test is inverted (four seeds leave the row
        UNKNOWN at its original count) and every existing
        `test_update_data_gaps.py` case still passes.
- [x] **Task 2.3: A response-less `TRANSIENT_FAILURE` writes nothing**
      (effort: 3)
  - [x] **The chunk loop is the second defect.** `classify_outcome` *returns*
        `TRANSIENT_FAILURE` (it does not raise) for HTTP 429, any 5xx, an
        unparseable body, and EODHD's 200-with-`{"error": …}` quirk
        (`outcomes.py:71-76, 100-113`). The chunk loop then calls
        `_advance_minute_gap(outcome=TRANSIENT_FAILURE,
        fetch_status=FAILED_RETRYABLE)` and `_record_minute_attempt`, which
        increments `attempt_count`, can promote to `RETRY_EXHAUSTED`, and
        writes `acquisition_state`. That is accounting without an answer.
  - [x] Distinguish the two kinds of `TRANSIENT_FAILURE` at the point of
        classification — a transport/status failure (no usable answer) versus
        a 200 whose body was classified. Carry the distinction as a value, not
        by re-inspecting `response.status_code` at the call site.
  - [x] **Fence the daily path.** `classify_outcome` is shared: `minute.py:456`
        and `daily.py:737` both call it. State which mechanism carries the
        distinction — a new return value, a sidecar, or a new enum member —
        and keep `daily.py:737`'s behavior unmoved. Widening the return type
        in place would break or silently change the daily acquisition path,
        which this slice's Out of scope leaves alone.
  - [x] In the chunk loop, a no-answer failure skips both `_advance_minute_gap`
        and `_record_minute_attempt` and breaks out of the symbol's chunk loop
        (there is no point requesting the next chunk from a provider that just
        failed). The row is left exactly as it was for the next pass.
  - [x] A classified 200 (SUCCESS / PARTIAL / EMPTY) and a 404 keep today's
        behavior unchanged.
  - [x] Success: `_advance_minute_gap` is unreachable without a classified
        provider answer; the change is in the chunk loop and the
        classification, not in `_advance_minute_gap`'s own branches.
- [x] **Task 2.4: Tests for accounting on each failure mode** (effort: 3)
  - [x] In `test/unit/data/acquisition/daemon/test_minute.py`, one test per
        failure mode — HTTP 402, HTTP 500, HTTP 429 after retry exhaustion, a
        read timeout, and a connection reset — each asserting that **no**
        `data_gaps` row's `attempt_count`, `fetch_status`, or
        `last_attempt_ts` changed, and that
        `acquisition_state.last_attempt_outcome` / `last_attempt_ts` are
        likewise untouched for the un-answered symbol (SC4, and Scope 3's 912
        clause).
  - [x] One test asserts the converse: a classified 200 and a 404 **do** move
        `attempt_count` exactly as today, so the rule did not disable
        accounting.
  - [x] One test covers the 200-with-`{"error": …}` body: it is a classified
        response but carries no data — assert the behavior chosen in Task 2.3
        and state the reasoning in the test docstring.
  - [x] Success: `uv run pytest test/unit/data/acquisition/daemon -q` passes;
        SC4 is demonstrable from the test names.
- [x] **Task 2.5: Section 2 checkpoint** (effort: 1)
  - [x] Unit tier and mypy green; `ruff format` scoped to touched files.
  - [x] Commit: `fix: move minute gap accounting only on a provider answer (921)`.

## Section 3: Two-phase minute cycle

Design *Scope 3*, *SC6*. The trailing phase guarantees the current session
lands before any backfill chunk is requested. **This section comes before the
outcome and exit-code work** because the exit mapping is defined in terms of
which phase aborted — there is nothing to condition on until the phases exist.

- [ ] **Task 3.1: Constants for the failure policy** (effort: 1)
  - [ ] Add to `constants.py`, each with a docstring giving its measurement or
        precedent: `MINUTE_TRAILING_PRIORITY_WINDOW` (7 days),
        `MINUTE_PASS_MAX_CONSECUTIVE_PROVIDER_FAILURES` (5, following
        `PULL_MAX_CONSECUTIVE_PROVIDER_ERRORS`).
  - [ ] Success: `test/unit/test_constants.py` asserts the values and types.
- [ ] **Task 3.2: `min_gap_end` filter on the gap selector** (effort: 1)
  - [ ] `pick_most_recent_actionable_gap` gains an optional `min_gap_end`
        parameter that adds a `gap_end >= %s` predicate. Existing callers pass
        nothing and behave identically (an additive, backward-compatible
        signature change, recorded in the design's Interfaces Required).
  - [ ] Success: `test/unit/data/gaps/test_actionable_gap_selector.py` covers
        the filtered and unfiltered forms.
- [ ] **Task 3.3: Decide and record how the backfill phase seeds** (effort: 2)
  - [ ] **The problem, measured from the code.** `_do_minute_symbol`'s seed
        gate is `_needs_full_seed = force_reset_terminal or not _has_bars or
        not _has_any_gaps or _has_unknown_gaps`. With a ~70k-row UNKNOWN
        backlog, `_has_unknown_gaps` is true for nearly every symbol, so a
        second walk over the same universe re-enters the seed. The re-seed
        calls `compute_missing_minute_sessions` with the **cycle-start**
        coverage index, which still reports the session the trailing phase
        just fetched as uncovered, and re-inserts it as UNKNOWN. The backfill
        phase's selector (no `min_gap_end`, `ORDER BY gap_end DESC`) then
        picks exactly that row first.
  - [ ] Left unaddressed this costs ~13,000 redundant `/intraday` calls at 5
        credits each — roughly 65k of the 100k daily allowance spent
        re-fetching the day just fetched, which is the resource this slice
        exists to protect.
  - [ ] Choose one mechanism and record the reasoning in the task file and in
        a code comment: (a) the backfill phase does not seed at all — seeding
        belongs to the trailing walk, and the backfill walk consumes only
        existing actionable rows; or (b) the coverage index is refreshed
        between the phases so the just-fetched day reads as covered. Option
        (a) costs no extra query and matches "seed once per cycle"; option (b)
        pays a universe-wide cagg scan a second time per cycle.
  - [ ] Whichever is chosen, `run_minute_refetch` and the single-symbol path
        must be unaffected.
  - [ ] Success: the mechanism is written down with its cost before Task 3.4
        implements it; no symbol can be fetched twice in one cycle by
        construction, not by luck of ordering.

  **Decision (recorded 20260909): option (a) — the backfill phase does not
  seed.** Seeding belongs to the trailing walk, which runs first; the backfill
  walk consumes only gap rows that already exist.

  *Why (a).* The double-fetch becomes impossible by construction rather than
  by ordering: with no second seed there is no re-inserted row for the
  backfill selector to pick, so correctness does not depend on the coverage
  index being fresh, on `ORDER BY gap_end DESC`, or on the trailing phase
  having succeeded. Option (b) — refreshing the coverage index between phases
  — reaches the same guarantee only while the refresh itself succeeds: the
  index builder fails safe to `None` on a stale cagg or a statement timeout
  (`build_minute_coverage_index`, slice 168), and on that path the backfill
  phase would fall back to the cycle-start index and re-seed exactly the day
  just fetched. A correctness property that degrades when a query times out
  is the wrong shape for the resource this slice exists to protect.

  *Cost.* Option (a) costs nothing: one universe-wide cagg scan per cycle,
  unchanged from today. Option (b) pays a second scan per cycle (measured ~3 s
  for the universe query, per `MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT`'s
  note) for a weaker guarantee.

  *What (a) gives up.* A session that becomes missing *between* the two phases
  is not seeded until the next cycle. That is acceptable and in fact correct:
  the trailing phase has already attempted every symbol's current session, so
  a newly-missing row inside the trailing window is one the pass just handled,
  and a newly-missing older row is backfill work by definition. It waits one
  cycle, and the cycles fire twice daily (01:05 and 13:05 UTC).

  *Scope of the change.* The no-seed behavior is a property of the backfill
  **phase**, passed explicitly into `_do_minute_symbol` — not a change to
  `_do_minute_symbol`'s own gate. `run_minute_refetch` and the single-symbol
  operator path call `_do_minute_symbol` directly and never set it, so they
  seed exactly as they do today.
- [ ] **Task 3.4: Trailing and backfill phases** (effort: 3)
  - [ ] `run_minute_cycle` walks the active universe once with
        `min_gap_end = now - MINUTE_TRAILING_PRIORITY_WINDOW` and **one chunk
        per symbol**, then walks it again for backfill with today's
        `most_stale_first` ordering and no `min_gap_end`, seeding per the
        Task 3.3 decision.
  - [ ] The one-chunk bound belongs to the trailing phase, not to
        `_do_minute_symbol` generally — pass it explicitly so the backfill
        phase and `run_minute_refetch` are unchanged.
  - [ ] `should_continue` (the SIGTERM hook) is honored between symbols in
        both phases and between the phases.
  - [ ] The current phase is tracked as a value the cycle can report, since
        Section 4's exit mapping and journal line both depend on it.
  - [ ] Log `trailing phase complete: N symbols` before the first backfill
        line (SC6).
  - [ ] Success: no backfill chunk is requested until every active symbol's
        trailing gap has been attempted.
- [ ] **Task 3.5: Tests for phase order and no double-fetch** (effort: 3)
  - [ ] A test with a symbol set holding both trailing and old gaps asserts
        the request order: every trailing request precedes every backfill
        request (SC6).
  - [ ] A test asserts the trailing phase issues at most one chunk per symbol
        even when a symbol has several actionable trailing gaps.
  - [ ] **A test asserts no symbol-day is requested twice in one cycle** —
        the direct regression for Task 3.3. Fixture: a symbol with a
        successful trailing fetch and a stale coverage index; assert the
        backfill phase does not request that day again.
  - [ ] A `caplog` assertion covers SC6's second half: the
        `trailing phase complete: N symbols` line is emitted before the first
        backfill line. It is the operator's only in-production evidence that
        the trailing phase ran to completion, and it is what the design's
        Verification Walkthrough greps for.
  - [ ] Success: `uv run pytest test/unit/data/acquisition/daemon -q` passes.
- [ ] **Task 3.6: Section 3 checkpoint** (effort: 1)
  - [ ] Unit tier and mypy green; `ruff format` scoped to touched files.
  - [ ] Commit: `feat: run the minute cycle in trailing then backfill phases (921)`.

## Section 4: Pass outcome, abort, and exit codes

Design *Scope 3*, *Decision 4*, *Decision 7*, *SC5*. Today a 402 raises
`ProviderResponseError`, is caught per symbol by `_process_minute_symbol`, and
the cycle walks the remaining 13,000 symbols one at a time. The accounting is
already correct for 402 (the raise happens before any write) — what is missing
is the **abort**, the outcome, and a path from that outcome to the process
exit code.

- [ ] **Task 4.1: `MinutePassOutcome` enum** (effort: 1)
  - [ ] Add `MinutePassOutcome` as a `StrEnum` beside `LastAttemptOutcome` in
        `data/acquisition/state.py`, with members `COMPLETE`,
        `QUOTA_EXHAUSTED`, `PROVIDER_UNAVAILABLE`.
  - [ ] No string literal for these values may appear anywhere else — journal
        lines, exit mapping, and tests all reference the enum (Decision 7).
  - [ ] Success: `test/unit/data/acquisition/test_state.py` covers the members
        and their string values; `grep` for the literal strings returns
        matches in
        `state.py`'s enum definition and nowhere else.
- [ ] **Task 4.2: Failure kind survives `_process_minute_symbol`** (effort: 2)
  - [ ] **The problem, measured from the code.** `_process_minute_symbol`
        collapses five distinct `except` handlers —
        `ProviderResponseError`, `psycopg.errors.LockNotAvailable`,
        `PoolTimeout`, `httpx.HTTPError`/`TimeoutException`, and a bare
        `Exception` — to the same return value,
        `(TRANSIENT_FAILURE, None, None, 0, 0)`. `run_minute_cycle` therefore
        cannot tell a provider outage from a database one, and a breaker built
        on that value would abort with `PROVIDER_UNAVAILABLE` on a Postgres
        pool exhaustion.
  - [ ] **The handlers are only half of it.** 5xx and 429 never raise —
        `classify_outcome` returns `TRANSIENT_FAILURE` for them, so
        `_do_minute_symbol` returns *normally* through the `try`, not through
        any handler. The breaker's headline trigger therefore does not reach
        the cycle at all unless the Task 2.3 distinction is threaded up the
        **normal return path**: `_do_minute_symbol`'s return →
        `_process_minute_symbol`'s return → `run_minute_cycle`.
  - [ ] Extend both paths — what `_process_minute_symbol` returns on a normal
        return, and what its five `except` handlers return (or let a typed
        exception reach the cycle) — so the failure **kind** is explicit:
        provider quota, provider failure, database failure, or a classified
        response. Do not infer the kind from the outcome enum or a log
        message.
  - [ ] Every existing caller and test of `_process_minute_symbol` must be
        updated in this task, not left to fail in the next one. **Re-green the
        daemon test module before starting Task 4.3** — this task changes a
        signature across every caller, and the abort and breaker layer on top
        of it.
  - [ ] Success: a `PoolTimeout`, an `httpx.ReadTimeout`, and a **returned**
        HTTP 500 are all distinguishable by the cycle without inspecting an
        exception message — the 500 case is the one the handler-only reading
        of this task would miss.
- [ ] **Task 4.3: 402 aborts the pass** (effort: 2)
  - [ ] `classify_outcome` raises `ProviderResponseError` for 402 with a
        distinct message. Give the quota case a distinguishable exception (a
        subclass, or a typed attribute — not a message-substring check, which
        is a fragile label) so the cycle can tell quota exhaustion from a
        vendor-contract 4xx.
  - [ ] The cycle ends the pass immediately with
        `MinutePassOutcome.QUOTA_EXHAUSTED`, recording which phase it was in.
  - [ ] Other non-404 4xx keep today's per-symbol skip behavior.
  - [ ] Success: a 402 on the first symbol ends the pass with no further
        provider calls (SC5).
- [ ] **Task 4.4: Consecutive-failure breaker** (effort: 2)
  - [ ] `run_minute_cycle` counts consecutive symbols whose failure kind
        (Task 4.2) is *provider failure* — 5xx, timeout, connection reset,
        429 exhaustion — and aborts with
        `MinutePassOutcome.PROVIDER_UNAVAILABLE` at
        `MINUTE_PASS_MAX_CONSECUTIVE_PROVIDER_FAILURES`.
  - [ ] The counter resets on any symbol that reaches a classified provider
        response, so scattered failures across a long pass never trip it.
  - [ ] Database failure kinds never increment the counter.
  - [ ] Success: five consecutive 5xx symbols end the pass; five consecutive
        `PoolTimeout` symbols do not; four failures, a success, then four more
        do not.
- [ ] **Task 4.5: Tests for abort and breaker** (effort: 2)
  - [ ] Cases: first-response 402 → `QUOTA_EXHAUSTED` with no second provider
        call; five consecutive 5xx → `PROVIDER_UNAVAILABLE`; five consecutive
        `PoolTimeout` → pass continues (the misattribution regression);
        four-fail / success / four-fail → pass completes.
  - [ ] A test asserts the outcome carries the phase it aborted in.
  - [ ] Success: `uv run pytest test/unit/data/acquisition -q` passes.
- [ ] **Task 4.6: Outcome-to-exit plumbing** (effort: 3)
  - [ ] **Name the path, because none exists today.** `run_minute_cycle`
        returns a `CycleReport` (defined in `daemon/daily.py:69` and shared
        with the daily path); `Runner.start()` returns its own int;
        `daemon_run` ends at `sys.exit(runner.start())` (`data.py:1470`).
        Carry `MinutePassOutcome` from the cycle through the runner to that
        exit without adding a minute-only field to the shared `CycleReport` —
        if the report must carry it, say so and state what the daily path puts
        there.
  - [ ] State what a `--forever` runner that ran several minute cycles exits
        with: the last cycle's outcome, or the worst seen. Pick one and write
        the reason in a comment. **This is a low-stakes choice** —
        `mt-minute-pass.service`'s `ExecStart` runs
        `mt data daemon run --minute --stop-when-done`, so `--forever` is a
        hand-run/dev path and neither choice affects the unit's exit code in
        production. Do not treat it as blocking.
  - [ ] **There is no shared exit-code constant in this project.** The nearest
        precedent is `EXIT_BY_OUTCOME`, a dict local to
        `cli/commands/kalshi.py:54` over `EXIT_OK`/`EXIT_SYNC_PARTIAL`/…;
        `cli/commands/data.py` uses per-command private ints. Follow the
        Kalshi precedent: define the minute pass's exit codes as named
        constants and a single `dict[MinutePassOutcome, int]` mapping, not
        scattered conditionals and not a bare `3`.
  - [ ] Mapping: `COMPLETE` → 0; `QUOTA_EXHAUSTED` after the trailing phase
        completed → 0 (spending the allowance on backfill is the designed
        steady state); `QUOTA_EXHAUSTED` inside the trailing phase → 3;
        `PROVIDER_UNAVAILABLE` → 3 in either phase.
  - [ ] On abort, log one line naming the outcome, the phase, and the count of
        symbols not attempted (Scope 3).
  - [ ] An aborted pass stamps `RunnerState.last_minute_cycle_end_utc` exactly
        as a completed pass does — it is an in-process busy-loop guard only,
        and slice 912 derives remaining work from `acquisition_state`
        (Scope 3's 912 clause). Add a comment citing 912 at the stamp.
  - [ ] Success: the exit code is produced by one lookup table; `grep` finds
        no bare `3` in the minute pass path.
- [ ] **Task 4.7: Tests for the exit mapping** (effort: 2)
  - [ ] Cases: trailing-phase 402 → exit 3; post-trailing 402 → exit 0;
        `PROVIDER_UNAVAILABLE` in the trailing phase → exit 3; in the backfill
        phase → exit 3; `COMPLETE` → exit 0 (SC5).
  - [ ] A test asserts the aborted pass stamped the cycle end and left
        `acquisition_state` rows for un-attempted symbols untouched.
  - [ ] A test covers the `--forever` multi-cycle rule chosen in Task 4.6.
  - [ ] Success: `uv run pytest test/unit/data/acquisition -q` passes.
- [ ] **Task 4.8: Section 4 checkpoint** (effort: 1)
  - [ ] Unit tier and mypy green; `ruff format` scoped to touched files.
  - [ ] Commit: `feat: abort the minute pass on quota exhaustion or a failure storm (921)`.
  - [ ] Continue with file 2, Section 5 (health check).

## Review Response (2026-09-09, tasks review part 1 — FAIL)

| Finding | Change |
|---|---|
| F001 no task makes a 5xx stop moving accounting | New Task 2.3: a `TRANSIENT_FAILURE` that `classify_outcome` **returned** (429, 5xx, unparseable body, 200-with-`error`) skips `_advance_minute_gap` and `_record_minute_attempt` and breaks the chunk loop. The wrong claim in the old Task 3.2 is deleted; the Context Summary now states both accounting paths. Task 2.4 adds the 429 and 200-with-`error` cases. |
| F002 backfill re-seeds from the stale coverage index | New Task 3.3: the double-fetch is stated with its measured cost (~13k redundant calls, ~65k credits) and the mechanism must be chosen and recorded before Task 3.4 implements it. Task 3.5 adds the no-double-fetch regression test. |
| F003 exit mapping depends on phases introduced later | Sections reordered: the two-phase cycle is now Section 3 and the outcome/exit work Section 4. The Context Summary states why. |
| F004 breaker cannot distinguish provider from DB failures | New Task 4.2: the failure kind must survive `_process_minute_symbol`, which today collapses five `except` handlers to one return value. Task 4.5 adds the five-consecutive-`PoolTimeout` regression. |
| F005 no such exit-code constant, and no path to the exit | Task 4.6 names the plumbing (cycle to runner to `sys.exit`), requires a decision on the `--forever` multi-cycle rule, and points at `kalshi.py:48-58`'s `EXIT_BY_OUTCOME` as the precedent, stating that no project-wide constant exists. |
| F006 no load test for the health read NFR | Addressed in file 2, Task 5.8, with the CI gap stated explicitly. |
| F007 Task 8.5 wait-blocked on two nightly firings | Addressed in file 2: the cutover script fires both passes itself (Task 7.3) and a new `--verify` mode (Task 7.1) prints the measurements on demand. |
| F008 Section 2 had no commit checkpoint | Its two regression tests folded into Section 1 as Tasks 1.6/1.7, riding the Task 1.8 checkpoint. |
| F009 floor stated as both 975,000 and 1,000,000 | Distinguished in file 2, Tasks 5.5 and 7.1: 975,000 is the computed health floor, 1,000,000 the stricter one-time cutover acceptance bar. |
| F010 tests batched in Sections 4 and 6 | Section 4 now tests after the abort/breaker (Task 4.5) and again after the exit mapping (Task 4.7); file 2's Section 5 tests after the selector (Task 5.3) and after the rule (Task 5.7). |

## Review Response (2026-09-09, tasks re-review part 1 — CONCERNS)

Both FAIL findings from the first round are resolved. Remaining concerns and
notes applied:

| Finding | Change |
|---|---|
| F001 breaker's 5xx/429 trigger never reaches the exception path | Task 4.2 now states that 5xx and 429 return *normally* through `_do_minute_symbol` and requires the failure kind to be threaded up the normal return path as well as the handlers; its success criterion adds a returned-500 case. |
| F002 Task 2.3 changes a shared classifier without fencing daily | Task 2.3 gains the fence: `classify_outcome` is called by `minute.py:456` and `daily.py:737`; the mechanism must be named and `daily.py:737`'s behavior must not move. |
| F003 SC6's journal line untested | Task 3.5 adds a `caplog` assertion that `trailing phase complete: N symbols` precedes the first backfill line. |
| F004 grep criterion self-contradictory | Task 4.1 reworded: grep returns matches in `state.py`'s enum definition and nowhere else. |
| F005 `--forever` decision overstated | Task 4.6 records that `ExecStart` uses `--stop-when-done`, so `--forever` is a hand-run path and the choice is not blocking. |
| F006 Section 4 stacks three tasks before a test | Task 4.2 now requires re-greening the daemon test module before Task 4.3 layers the abort on top of the signature change. |
