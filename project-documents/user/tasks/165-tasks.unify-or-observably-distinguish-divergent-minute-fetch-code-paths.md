---
docType: tasks
slice: unify-or-observably-distinguish-divergent-minute-fetch-code-paths
project: trading-data
lld: user/slices/165-slice.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md
dependencies: [162]
projectState: Slice 162 (coverage-aware minute gap-seeding), 163 (cagg re-chunking), 166 (minute_ohlcv rechunk), 167 (cagg-backed data_status), 168 (cagg freshness assertion) all complete and merged to main. run_minute_refetch (mt data pull 1m) still uses the pre-162 legacy full-window single-span seed; run_minute_cycle (mt data daemon run --minute) uses slice 162's coverage-aware seeding. No via marker exists in minute/daily daemon logs today.
dateCreated: 20260727
dateUpdated: 20260728
status: not_started
reviewFindings: [F001, F002, F003, F004, F005]
---

# Tasks: Unify or Observably Distinguish Divergent Minute-Fetch Code Paths

## Context

Working on slice 165 (140 initiative band, Data Quality and Operations). Two
independently-implemented "fetch minute data for a symbol" code paths exist:
`mt data pull 1m` (`run_minute_refetch`) and `mt data daemon run --minute
--symbols X` (`run_minute_cycle`). Only the daemon path is coverage-aware
(slice 162) — `run_minute_refetch` always falls back to a legacy
`[history_start, target_end]` full-window seed. Nothing in the CLI, logs, or
help text distinguishes which algorithm ran, which caused two independent
production-verification mistakes during slice 162.

This slice unifies `run_minute_refetch` onto coverage-aware seeding
(`build_minute_coverage_index` / `compute_missing_minute_sessions`), makes
`force_reset_terminal` a fully orthogonal flag, adds a `via=refetch|cycle`
log marker to both minute and daily daemon paths for observability, corrects
slice 162's Verification Walkthrough, and supersedes the interim reference
doc. An audit of the daily/CLI/gaps modules for the same divergent-pair
pattern was already performed during design (see the slice design's Audit
Findings section) — no further code investigation is needed for that item,
only the documentation and log-marker follow-through captured in Phase 1
below.

**Dependencies**: Slice 162 — complete.
**Delivers**: `mt data pull 1m` seeds identically to `mt data daemon run
--minute`; every minute/daily daemon log line carries an unambiguous `via`
marker; slice 162's walkthrough and the interim reference doc are corrected.
**Next slice**: none currently scheduled directly after 165 in the 140 band
(166/167/168 already shipped ahead of it; check the slice plan for the next
unstarted entry).

## Tasks

### Phase 1: `via` Log Marker Threading

- [ ] **1.1 Add `via` parameter to minute daemon functions**
  - [ ] In `src/manta_trading/data/acquisition/daemon/minute.py`, add a
        keyword-only `via: str` parameter to `_do_minute_symbol` and
        `_process_minute_symbol`
  - [ ] Thread `via` into every existing `_logger.info` / `_logger.warning` /
        `_logger.error` / `_logger.exception` call currently inside these two
        functions (append `via=%s` to the format string and `via` to the
        args tuple — do not change existing message wording beyond adding
        the field)
  - [ ] `_process_minute_symbol`'s internal call to `_do_minute_symbol`
        (minute.py:215-221) must forward `via=via` — `via` has no default,
        so an unforwarded call breaks immediately with a missing required
        argument
  - [ ] `run_minute_cycle` passes `via="cycle"` when calling
        `_process_minute_symbol`
  - [ ] Success: `grep -n "via" src/manta_trading/data/acquisition/daemon/minute.py`
        shows the parameter declared on both functions and present in every
        log call site that existed before this task; `via` is not used in
        any conditional or branch

- [ ] **1.2 Add `via` parameter to daily daemon functions**
  - [ ] In `src/manta_trading/data/acquisition/daemon/daily.py`, add the
        same keyword-only `via: str` parameter to `_do_daily_symbol` and
        `_process_daily_symbol`, threaded into their existing log calls
        the same way as 1.1
  - [ ] `_process_daily_symbol`'s internal call to `_do_daily_symbol`
        (daily.py:306) must forward `via=via` — same missing-argument
        failure mode as the minute side if left unmodified
  - [ ] `run_daily_cycle` passes `via="cycle"` when calling
        `_process_daily_symbol`
  - [ ] `run_daily_refetch` calls `_do_daily_symbol` directly (not through
        `_process_daily_symbol`, mirroring how `run_minute_refetch` calls
        `_do_minute_symbol` directly) — update this call site to pass
        `via="refetch"`. `via` has no default, so leaving this call site
        unmodified breaks every `mt data pull 1d` invocation with a missing
        required argument
  - [ ] Success: `grep -n "via" src/manta_trading/data/acquisition/daemon/daily.py`
        mirrors the pattern established in 1.1, and shows `via="refetch"` at
        the `run_daily_refetch` → `_do_daily_symbol` call site specifically;
        no behavior change beyond the added log field

- [ ] **1.3 Test `via` marker presence**
  - [ ] In `test/unit/data/acquisition/daemon/test_minute.py`, add or extend
        a test asserting `_do_minute_symbol` accepts and forwards `via` (e.g.
        capture the mocked `_logger` calls and assert `via="cycle"` or
        `via="refetch"` appears in at least one call's args, per the existing
        test style in that file — see the `TestDoMinuteSymbolExtensions`
        class at line 261 for the mocking pattern used for `_do_minute_symbol`
        callers)
  - [ ] In `test/unit/data/acquisition/daemon/test_daily.py`, add the
        equivalent assertion for `_do_daily_symbol`, plus a test on
        `TestRunDailyRefetch` (mocks `_do_daily_symbol` per its existing
        pattern) asserting `run_daily_refetch` passes `via="refetch"` in its
        call — this is the assertion that catches the missing-argument
        defect a mock-based `_do_daily_symbol` test alone cannot catch
  - [ ] Success: `uv run pytest test/unit/data/acquisition/daemon/test_minute.py test/unit/data/acquisition/daemon/test_daily.py -q`
        passes, including the new `via` assertions

**Commit**: `refactor: add via log marker to minute/daily daemon paths`

### Phase 2: Unify `run_minute_refetch` onto Coverage-Aware Seeding

- [ ] **2.1 Build and pass coverage index in `run_minute_refetch`**
  - [ ] In `src/manta_trading/data/acquisition/daemon/minute.py`, inside
        `run_minute_refetch`, add a `pool.connection()` block that calls
        `build_minute_coverage_index(conn)` — same call shape as the one
        already present in `run_minute_cycle` (around line 148-149)
  - [ ] Pass the resulting `coverage_index` into the existing
        `_do_minute_symbol(...)` call in `run_minute_refetch`, alongside the
        unchanged `force_reset_terminal=True` and `window=window` arguments
  - [ ] Pass `via="refetch"` in the same call
  - [ ] Do not change `run_minute_refetch`'s public signature
        (`symbol`, `from_date`, `to_date`) — no caller update required
  - [ ] Success: `run_minute_refetch` builds a coverage index exactly once
        per invocation and forwards it to `_do_minute_symbol`; `ruff` and
        `mypy`/`pyright` clean on `minute.py`

- [ ] **2.2 Update `TestRunMinuteRefetch` unit tests for coverage-aware seeding**
  - [ ] In `test/unit/data/acquisition/daemon/test_minute.py`, locate the
        `TestRunMinuteRefetch` class (T9, around line 478)
  - [ ] Update or add a test asserting `run_minute_refetch` builds a coverage
        index (mock `build_minute_coverage_index` and assert it was called)
        and that the built index is forwarded to `_do_minute_symbol` as
        `coverage_index` — mirror the equivalent assertion pattern already
        used for `run_minute_cycle` in the T7/T8 classes (see
        `test_coverage_index_present_passes_precomputed_ranges_not_span`
        around line 428 for the pattern to mirror at the `_do_minute_symbol`
        level; at the `run_minute_refetch` level, assert the index-building
        call itself happens and its result is forwarded)
  - [ ] Confirm `test_force_reset_terminal_always_true` (line 551) is
        unchanged and still passes — `force_reset_terminal=True` remains the
        default for `run_minute_refetch`
  - [ ] Remove or update any test that previously asserted (implicitly, by
        omission) `coverage_index=None` was passed from `run_minute_refetch`
  - [ ] Success: `uv run pytest test/unit/data/acquisition/daemon/test_minute.py -q`
        passes; test coverage confirms `run_minute_refetch` now seeds
        coverage-aware, not full-window

- [ ] **2.3 Integration verification against `trading_test`**
  - [ ] Using `MT_TIMESCALE_TEST_URL` (exported manually per the standing
        environment trap — `uv run` does not auto-export `.env`), seed a
        test symbol on `trading_test` with a partially-covered minute
        history (bars present for some sessions in a window, missing for
        others)
  - [ ] Run `uv run mt data pull 1m --symbol <TEST_SYMBOL> -v` against
        `trading_test` and inspect the resulting `data_gaps` rows for that
        symbol
  - [ ] Confirm the seeded gap rows match only the genuinely-missing
        sessions in the window — not a single `[history_start, target_end]`
        span
  - [ ] Success: gap-row shape after the fix matches what
        `mt data daemon run --minute --symbols <TEST_SYMBOL> -v` would
        produce from the same starting state (per slice design Verification
        Walkthrough step 1)

- [ ] **2.4 Integration verification of `force_reset_terminal` DB-level reset**
  - [ ] Using `MT_TIMESCALE_TEST_URL`, seed a `data_gaps` row for a test
        symbol with `status=RETRY_EXHAUSTED`
  - [ ] Run `uv run mt data pull 1m --symbol <TEST_SYMBOL> -v` against
        `trading_test`
  - [ ] Confirm the seeded row is reset and re-attempted (not skipped) —
        this is the DB-level check for slice design Verification Walkthrough
        step 2; task 2.2's `test_force_reset_terminal_always_true` only
        confirms the boolean is passed through at the unit level, not that
        the reset actually happens against real data
  - [ ] Success: post-run `data_gaps` state for the seeded row shows it was
        reset and re-attempted, matching Verification Walkthrough step 2

**Commit**: `fix: unify run_minute_refetch onto coverage-aware seeding`

### Phase 3: Documentation Corrections

- [ ] **3.1 Correct slice 162's Verification Walkthrough**
  - [ ] In `project-documents/user/slices/162-slice.coverage-aware-minute-gap-seeding.md`,
        locate the "CLI correction (found during Phase 6, 2026-07-17)" note
        in the Verification Walkthrough section
  - [ ] Replace the note with the settled, permanent form: state plainly
        that `mt data daemon run --minute --symbols <SYM>` is the correct
        invocation, and remove the caveat about `mt data pull 1m` "silently
        routing through a different, non-coverage-aware code path" — this is
        no longer true after slice 165's fix (Phase 2 above)
  - [ ] Add a one-line cross-reference to slice 165 noting the divergence
        was resolved there
  - [ ] Bump `dateUpdated` in the 162 design's frontmatter to the date this
        task is completed
  - [ ] Success: the 162 walkthrough reads correctly for a reader with no
        knowledge of the historical defect; no stale claims remain

- [ ] **3.2 Supersede the interim reference doc**
  - [ ] In `project-documents/user/reference/minute-fetch-code-paths.md`,
        change frontmatter `status: draft` to `status: superseded`
  - [ ] Add a note at the top of the document (below frontmatter) stating
        the two paths were unified by slice 165 and this document is
        retained only as the historical record of the defect — do not
        delete the file or its content otherwise
  - [ ] Success: `grep -n "status:" project-documents/user/reference/minute-fetch-code-paths.md`
        shows `status: superseded`; document content otherwise unchanged

- [ ] **3.3 Update slice plan entry 25 (165) if needed**
  - [ ] Re-read `project-documents/user/architecture/140-slices.data-quality-operations.md`
        entry 25 (slice 165) — confirm the existing scope description still
        matches what was actually delivered; no rewrite needed unless
        implementation diverged from the design during Phase 1/2 above
  - [ ] Success: plan entry accurately reflects delivered behavior (checkbox
        update happens in Phase 4 close-out, not this task)

**Commit**: `docs: correct slice 162 walkthrough, supersede minute-fetch-code-paths reference`

### Phase 4: Full Verification and Close-Out

- [ ] **4.1 Full unit test pass**
  - [ ] Run `uv run pytest test/unit/data/acquisition/daemon/test_minute.py test/unit/data/acquisition/daemon/test_daily.py -q`
  - [ ] Success: all tests pass, including the new/updated tests from
        Phase 1 and Phase 2

- [ ] **4.2 Static analysis**
  - [ ] Run `ruff check` and `uv run --extra dev mypy` (pyright is not
        installed in this environment per standing note) against
        `src/manta_trading/data/acquisition/daemon/minute.py` and
        `src/manta_trading/data/acquisition/daemon/daily.py`
  - [ ] Success: zero errors on both touched files, at or below `main`
        baseline for pre-existing warnings

- [ ] **4.3 End-to-end log marker confirmation**
  - [ ] Run `mt data pull 1m --symbol <TEST_SYMBOL> -v` and
        `mt data daemon run --minute --symbols <TEST_SYMBOL> -v` against
        `trading_test` with verbose/JSON logging enabled
  - [ ] Also run `mt data pull 1d --symbol <TEST_SYMBOL> -v` against
        `trading_test` — this exercises `run_daily_refetch` → `_do_daily_symbol`
        directly and is the only task in this file that actually invokes that
        call path end-to-end (Phase 1's tests mock `_do_daily_symbol`, so a
        missing/misordered argument there would not surface until this step)
  - [ ] Confirm log output contains `via=refetch` for both `pull` invocations
        (minute and daily) and `via=cycle` for the `daemon run` invocation
  - [ ] Success: all three markers observed exactly as specified in the
        slice design's Verification Walkthrough step 3; `mt data pull 1d`
        completes without a `TypeError` or other exception

- [ ] **4.4 Delegate task/design checklist close-out**
  - [ ] Delegate to the `task-checker` agent: confirm all checkboxes in this
        task file are checked and set frontmatter `status: complete`
  - [ ] Update `project-documents/user/slices/165-slice.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md`
        frontmatter `status: complete`
  - [ ] Update slice plan entry 25 in `140-slices.data-quality-operations.md`
        to `[x]`
  - [ ] Success: task file, slice design, and plan entry all reflect
        completion; `git log` shows all four Phase commits present on the
        branch before merge

**Commit**: `docs: close out slice 165`
