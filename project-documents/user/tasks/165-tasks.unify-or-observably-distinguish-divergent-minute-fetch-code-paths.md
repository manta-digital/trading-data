---
docType: tasks
slice: unify-or-observably-distinguish-divergent-minute-fetch-code-paths
project: trading-data
lld: user/slices/165-slice.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md
dependencies: [162]
projectState: Slice 162 (coverage-aware minute gap-seeding), 163 (cagg re-chunking), 166 (minute_ohlcv rechunk), 167 (cagg-backed data_status), 168 (cagg freshness assertion) all complete and merged to main. run_minute_refetch (mt data pull 1m) still uses the pre-162 legacy full-window single-span seed; run_minute_cycle (mt data daemon run --minute) uses slice 162's coverage-aware seeding. No via marker exists in minute/daily daemon logs today. [Slice 165 delivered 2026-07-28: paths unified.]
dateCreated: 20260727
dateUpdated: 20260728
status: complete
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

- [x] **1.1 Add `via` parameter to minute daemon functions**
  - [x] In `src/manta_trading/data/acquisition/daemon/minute.py`, add a
        keyword-only `via: str` parameter to `_do_minute_symbol` and
        `_process_minute_symbol`
  - [x] Thread `via` into every existing `_logger.info` / `_logger.warning` /
        `_logger.error` / `_logger.exception` call currently inside these two
        functions (append `via=%s` to the format string and `via` to the
        args tuple — do not change existing message wording beyond adding
        the field)
  - [x] `_process_minute_symbol`'s internal call to `_do_minute_symbol`
        (minute.py:215-221) must forward `via=via` — `via` has no default,
        so an unforwarded call breaks immediately with a missing required
        argument
  - [x] `run_minute_cycle` passes `via="cycle"` when calling
        `_process_minute_symbol`
  - [x] Success: `grep -n "via" src/manta_trading/data/acquisition/daemon/minute.py`
        shows the parameter declared on both functions and present in every
        log call site that existed before this task; `via` is not used in
        any conditional or branch

- [x] **1.2 Add `via` parameter to daily daemon functions**
  - [x] In `src/manta_trading/data/acquisition/daemon/daily.py`, add the
        same keyword-only `via: str` parameter to `_do_daily_symbol` and
        `_process_daily_symbol`, threaded into their existing log calls
        the same way as 1.1
  - [x] `_process_daily_symbol`'s internal call to `_do_daily_symbol`
        (daily.py:306) must forward `via=via` — same missing-argument
        failure mode as the minute side if left unmodified
  - [x] `run_daily_cycle` passes `via="cycle"` when calling
        `_process_daily_symbol`
  - [x] `run_daily_refetch` calls `_do_daily_symbol` directly (not through
        `_process_daily_symbol`, mirroring how `run_minute_refetch` calls
        `_do_minute_symbol` directly) — update this call site to pass
        `via="refetch"`. `via` has no default, so leaving this call site
        unmodified breaks every `mt data pull 1d` invocation with a missing
        required argument
  - [x] Success: `grep -n "via" src/manta_trading/data/acquisition/daemon/daily.py`
        mirrors the pattern established in 1.1, and shows `via="refetch"` at
        the `run_daily_refetch` → `_do_daily_symbol` call site specifically;
        no behavior change beyond the added log field

- [x] **1.3 Test `via` marker presence**
  - [x] In `test/unit/data/acquisition/daemon/test_minute.py`, add or extend
        a test asserting `_do_minute_symbol` accepts and forwards `via` (e.g.
        capture the mocked `_logger` calls and assert `via="cycle"` or
        `via="refetch"` appears in at least one call's args, per the existing
        test style in that file — see the `TestDoMinuteSymbolExtensions`
        class at line 261 for the mocking pattern used for `_do_minute_symbol`
        callers)
  - [x] In `test/unit/data/acquisition/daemon/test_daily.py`, add the
        equivalent assertion for `_do_daily_symbol`, plus a test on
        `TestRunDailyRefetch` (mocks `_do_daily_symbol` per its existing
        pattern) asserting `run_daily_refetch` passes `via="refetch"` in its
        call — this is the assertion that catches the missing-argument
        defect a mock-based `_do_daily_symbol` test alone cannot catch
  - [x] Success: `uv run pytest test/unit/data/acquisition/daemon/test_minute.py test/unit/data/acquisition/daemon/test_daily.py -q`
        passes, including the new `via` assertions

**Commit**: `refactor: add via log marker to minute/daily daemon paths`

### Phase 2: Unify `run_minute_refetch` onto Coverage-Aware Seeding

- [x] **2.1 Per-symbol coverage builder + wire into `run_minute_refetch`**
      *(Amended 2026-07-28 per design §Amendment — was: reuse the
      universe-wide `build_minute_coverage_index`.)*
  - [x] In `src/manta_trading/data/gaps/minute_coverage.py`, add
        `build_symbol_minute_coverage(conn, symbol) -> dict[str, set[date]] | None`:
        same `assert_cagg_fresh` guard, same
        `SET LOCAL statement_timeout = MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT`,
        same catch-log-return-`None` fail-safe as
        `build_minute_coverage_index`, query filtered `WHERE symbol = %s`
        (parameterized — symbol must never be interpolated into SQL)
  - [x] In `constants.py`, change `MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT`
        from `"30s"` to `"300s"` and update its docstring rationale to cite
        `user/reference/prod-scale-and-coverage-scan-baseline.md`
        *(corrected from an initial 90s during Phase 6: statement_timeout
        also counts row streaming; measured end-to-end universe build is
        152.2s — aggregation ~18s + 22.7M-row transfer/parse — so 90s
        failed in prod at 91.2s; 300s is measured cost + plateau headroom)*
  - [x] In `run_minute_refetch` (`minute.py`), add a `pool.connection()`
        block calling `build_symbol_minute_coverage(conn, symbol)` and pass
        the result as `coverage_index` into the existing
        `_do_minute_symbol(...)` call, alongside the unchanged
        `force_reset_terminal=True`, `window=window`, and `via="refetch"`
  - [x] Do not change `run_minute_refetch`'s public signature
        (`symbol`, `from_date`, `to_date`) — no caller update required
  - [x] Success: `run_minute_refetch` builds a single-symbol coverage index
        exactly once per invocation and forwards it to `_do_minute_symbol`;
        it never calls the universe-wide `build_minute_coverage_index`;
        `ruff` and `mypy` clean on all touched files

- [x] **2.2 Unit tests for `build_symbol_minute_coverage` and `TestRunMinuteRefetch`**
      *(Amended 2026-07-28 to match design §Amendment.)*
  - [x] In `test/unit/data/gaps/test_minute_coverage.py`, add tests for
        `build_symbol_minute_coverage` mirroring the existing
        `build_minute_coverage_index` tests: returns `{symbol: set[date]}`
        (datetime rows normalized to `date`), returns `None` on
        `QueryCanceled`/operational error, returns `None` when
        `assert_cagg_fresh` reports stale, and the SQL is parameterized
        (symbol passed as a query parameter, not interpolated)
  - [x] In `test/unit/data/acquisition/daemon/test_minute.py`
        (`TestRunMinuteRefetch`, T9), mock `build_symbol_minute_coverage`
        and assert it is called exactly once with the requested symbol, and
        that its result is forwarded to `_do_minute_symbol` as
        `coverage_index`
  - [x] Confirm `test_force_reset_terminal_always_true` is unchanged and
        still passes — `force_reset_terminal=True` remains the default for
        `run_minute_refetch`
  - [x] Success: `uv run pytest test/unit/data/acquisition/daemon/test_minute.py test/unit/data/gaps/test_minute_coverage.py -q`
        passes; coverage confirms `run_minute_refetch` seeds coverage-aware
        via the per-symbol builder, not full-window and not the universe scan

- [x] **2.3 Integration verification against production `trading`**
      *(Re-targeted 2026-07-28: `trading_test` is unrepresentative — plain
      views instead of caggs, so every coverage path falls back; PM ruling
      recorded in `user/reference/prod-scale-and-coverage-scan-baseline.md`.
      `pull 1m` writes are additive-only and PM-approved for this check.)*
  - [x] Pick a prod symbol with genuinely-partial minute coverage (backfill
        in progress makes these plentiful — inspect `data_gaps` for a
        symbol with UNKNOWN gaps, or use `build_symbol_minute_coverage`'s
        query manually to find missing sessions)
  - [x] Run `uv run mt data pull 1m --symbol <SYMBOL> -v` with
        `MT_TIMESCALE_DB_URL` (prod) and inspect the resulting `data_gaps`
        rows for that symbol
  - [x] Confirm seeded gap rows match only the genuinely-missing sessions
        in the window — not a single `[history_start, target_end]` span —
        and the log shows no coverage-index ERROR/fallback
  - [x] Success: gap-row shape matches what the daemon path would produce
        from the same starting state (design Verification Walkthrough
        step 1); `via=refetch` present in log output

- [x] **2.4 Integration verification of `force_reset_terminal` DB-level reset**
      *(Re-targeted 2026-07-28 to prod; use an EXISTING terminal row — do
      not seed synthetic rows into production `data_gaps`.)*
  - [x] Find a prod symbol that already has a `RETRY_EXHAUSTED` or
        `PROVIDER_HOLE` `data_gaps` row within a bounded window (SELECT
        with `statement_timeout` per prod query discipline)
  - [x] Run `uv run mt data pull 1m --symbol <SYMBOL> -v` (optionally with
        `--from/--to` bounding the terminal row's window)
  - [x] Confirm the terminal row is reset and re-attempted (not skipped) —
        the DB-level check for design Verification Walkthrough step 2;
        task 2.2's `test_force_reset_terminal_always_true` only confirms
        the boolean at the unit level
  - [x] Success: post-run `data_gaps` state shows the terminal row was
        reset and re-attempted, matching Verification Walkthrough step 2

**Commit**: `fix: unify run_minute_refetch onto coverage-aware seeding`

### Phase 3: Documentation Corrections

- [x] **3.1 Correct slice 162's Verification Walkthrough**
  - [x] In `project-documents/user/slices/162-slice.coverage-aware-minute-gap-seeding.md`,
        locate the "CLI correction (found during Phase 6, 2026-07-17)" note
        in the Verification Walkthrough section
  - [x] Replace the note with the settled, permanent form: state plainly
        that `mt data daemon run --minute --symbols <SYM>` is the correct
        invocation, and remove the caveat about `mt data pull 1m` "silently
        routing through a different, non-coverage-aware code path" — this is
        no longer true after slice 165's fix (Phase 2 above)
  - [x] Add a one-line cross-reference to slice 165 noting the divergence
        was resolved there
  - [x] Bump `dateUpdated` in the 162 design's frontmatter to the date this
        task is completed
  - [x] Success: the 162 walkthrough reads correctly for a reader with no
        knowledge of the historical defect; no stale claims remain

- [x] **3.2 Supersede the interim reference doc**
  - [x] In `project-documents/user/reference/minute-fetch-code-paths.md`,
        change frontmatter `status: draft` to `status: superseded`
  - [x] Add a note at the top of the document (below frontmatter) stating
        the two paths were unified by slice 165 and this document is
        retained only as the historical record of the defect — do not
        delete the file or its content otherwise
  - [x] Success: `grep -n "status:" project-documents/user/reference/minute-fetch-code-paths.md`
        shows `status: superseded`; document content otherwise unchanged

- [x] **3.3 Update slice plan entry 25 (165) if needed**
  - [x] Re-read `project-documents/user/architecture/140-slices.data-quality-operations.md`
        entry 25 (slice 165) — confirm the existing scope description still
        matches what was actually delivered; no rewrite needed unless
        implementation diverged from the design during Phase 1/2 above
  - [x] Success: plan entry accurately reflects delivered behavior (checkbox
        update happens in Phase 4 close-out, not this task)

**Commit**: `docs: correct slice 162 walkthrough, supersede minute-fetch-code-paths reference`

### Phase 4: Full Verification and Close-Out

- [x] **4.1 Full unit test pass**
  - [x] Run `uv run pytest test/unit/data/acquisition/daemon/test_minute.py test/unit/data/acquisition/daemon/test_daily.py -q`
  - [x] Success: all tests pass, including the new/updated tests from
        Phase 1 and Phase 2

- [x] **4.2 Static analysis**
  - [x] Run `ruff check` and `uv run --extra dev mypy` (pyright is not
        installed in this environment per standing note) against
        `src/manta_trading/data/acquisition/daemon/minute.py` and
        `src/manta_trading/data/acquisition/daemon/daily.py`
  - [x] Success: zero errors on both touched files, at or below `main`
        baseline for pre-existing warnings

- [x] **4.3 End-to-end log marker confirmation**
  - [x] Run `mt data pull 1m --symbol <TEST_SYMBOL> -v` and
        `mt data daemon run --minute --symbols <TEST_SYMBOL> -v` against
        `trading_test` with verbose/JSON logging enabled
  - [x] Also run `mt data pull 1d --symbol <TEST_SYMBOL> -v` against
        `trading_test` — this exercises `run_daily_refetch` → `_do_daily_symbol`
        directly and is the only task in this file that actually invokes that
        call path end-to-end (Phase 1's tests mock `_do_daily_symbol`, so a
        missing/misordered argument there would not surface until this step)
  - [x] Confirm log output contains `via=refetch` for both `pull` invocations
        (minute and daily) and `via=cycle` for the `daemon run` invocation
  - [x] Success: all three markers observed exactly as specified in the
        slice design's Verification Walkthrough step 3; `mt data pull 1d`
        completes without a `TypeError` or other exception

- [x] **4.4 Delegate task/design checklist close-out**
  - [x] Delegate to the `task-checker` agent: confirm all checkboxes in this
        task file are checked and set frontmatter `status: complete`
  - [x] Update `project-documents/user/slices/165-slice.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md`
        frontmatter `status: complete`
  - [x] Update slice plan entry 25 in `140-slices.data-quality-operations.md`
        to `[x]`
  - [x] Success: task file, slice design, and plan entry all reflect
        completion; `git log` shows all four Phase commits present on the
        branch before merge

**Commit**: `docs: close out slice 165`
