---
docType: review
layer: project
reviewType: code
slice: unify-or-observably-distinguish-divergent-minute-fetch-code-paths
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/165-slice.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260728
dateUpdated: 20260728
findings:
  - id: F001
    severity: concern
    category: dry-violation
    summary: "`build_symbol_minute_coverage` duplicates `build_minute_coverage_index` almost verbatim"
    location: src/manta_trading/data/gaps/minute_coverage.py:113-182
  - id: F002
    severity: concern
    category: typing
    summary: "`via` is a bare `str` instead of an enum, despite an established in-file precedent"
    location: src/manta_trading/data/acquisition/daemon/minute.py:214
  - id: F003
    severity: pass
    category: uncategorized
    summary: "`via` threading and happy-path logging is correct and well-tested"
    location: src/manta_trading/data/acquisition/daemon/minute.py:293-302
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Per-symbol coverage query uses parameterized SQL correctly"
    location: src/manta_trading/data/gaps/minute_coverage.py:153-164
  - id: F005
    severity: note
    category: uncategorized
    summary: "Single-symbol query reuses the universe-scaled 300s timeout"
    location: src/manta_trading/constants.py:119-143
---

# Review: code — slice 165

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Disposition (20260728)

- **F001 — fixed.** Shared envelope extracted: `_run_coverage_query`
  (staleness guard, statement timeout, fail-safe error handling) and
  `_normalize_covered_day` now serve both builders; each public function
  keeps only its SQL text and result-shape assembly. The slice-168 guard
  rationale comment moved into the helper so it, too, lives once.
- **F002 — fixed.** `FetchEntryPoint(StrEnum)` added to `constants.py`
  (`CYCLE`/`REFETCH`, same pattern as `DailyMode`); `via` is typed
  `FetchEntryPoint` on all four daemon functions and every call site and
  test passes the enum. The design doc's original "plain str is fine"
  rationale is superseded — see the design's Patterns and Conventions.
- **F005 — no change needed.** The reviewer notes the constant's docstring
  already documents the intentional timeout reuse; flagged for visibility
  only.

Verified after fixes: 134 unit tests pass (sole failure is the pre-existing
`test_4xx_non_429_propagates`, present on `main`); mypy clean; ruff at
baseline on all touched files.

## Findings

### [CONCERN] `build_symbol_minute_coverage` duplicates `build_minute_coverage_index` almost verbatim

The new per-symbol function repeats ~90% of `build_minute_coverage_index` (lines 35-110) line-for-line: the same `assert_cagg_fresh` staleness-guard block (including the identical log-message scaffolding and runbook reference), the same `SET LOCAL statement_timeout` setup, the same `try/except psycopg.OperationalError` envelope, and the same `covered_day.date() if isinstance(covered_day, datetime) else covered_day` normalization. Only the SQL's `WHERE`/`GROUP BY` clause and the return shape genuinely differ. Project guidelines are explicit here — "Do not duplicate logic. Respect DRY" and "Never scatter comparison values/logic across code... changing a value should require editing exactly one place." As written, the staleness-guard message, the timeout envelope, and the date-normalization fix all now live in two places; a future change to any of them (e.g. adjusting the runbook reference, or fixing a normalization edge case) requires remembering to touch both functions. Recommend factoring the shared staleness-check + timeout + fetch/normalize logic into a single private helper (e.g. `_query_covered_days(conn, cagg, sql, params, log_context)` or a small class) that both public functions call with just the SQL text and log label as parameters.

### [CONCERN] `via` is a bare `str` instead of an enum, despite an established in-file precedent

`via: str` (mirrored in `daily.py`) accepts any string, with `"cycle"`/`"refetch"` as the only two values ever passed, scattered as string literals across four call sites (`minute.py` cycle/refetch, `daily.py` cycle/refetch) and asserted against as string literals in the new tests. `constants.py` already establishes the project's own convention for exactly this shape of value — `DailyMode(StrEnum)` with `BACKFILL`/`STEADY_STATE` — and the Python rules doc requires `Enum`/`StrEnum` "for constants/choices." A `FetchEntryPoint(StrEnum)` with `CYCLE = "cycle"` / `REFETCH = "refetch"` would let the type checker catch a typo'd value at every call site (a bare `str` cannot), and centralizes the two valid values in one place rather than four. This doesn't block correctness today (mypy passes, and there are only two producers), but it's the same class of magic-string-as-parameter issue the project guidelines call out, and the codebase already demonstrates the fix pattern one struct away in the same file family.

### [PASS] `via` threading and happy-path logging is correct and well-tested

Every error branch in `_process_minute_symbol`/`_process_daily_symbol` and the happy-path log line in `_do_minute_symbol`/`_do_daily_symbol` carry `via=`, closing the exact ambiguity the slice targeted. Tests assert both the happy-path marker (`test_happy_path_logs_via_marker`) and the error-path marker, and a dedicated test (`test_via_refetch_passed_to_do_daily_symbol`/`..._do_minute_symbol`) catches the specific defect class of a caller forgetting to forward `via` to the real call site rather than only unit-testing the callee in isolation — good test-with, not test-after, coverage.

### [PASS] Per-symbol coverage query uses parameterized SQL correctly

`symbol` is passed as a bind parameter (`%s`, `(symbol,)`), not interpolated into the SQL string — the table name (`cagg`) is a resolved constant from `GRANULARITY_SOURCE`, not user input, so the f-string use there is consistent with the existing `build_minute_coverage_index` pattern. Test `test_symbol_passed_as_parameter_not_interpolated` explicitly guards this.

### [NOTE] Single-symbol query reuses the universe-scaled 300s timeout

`MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT` was resized to 300s based on an end-to-end measurement of the *universe-wide* 22.7M-row scan; `build_symbol_minute_coverage` (a single-symbol query) now reuses the same constant. This is safe (fail-safe on timeout either way, and a single-symbol query will complete far under 300s in practice) but means a genuinely-hung single-symbol query in `run_minute_refetch` — an interactive, single-shot operator command — could block for up to 5 minutes before failing safe, versus a tighter bound sized for the per-symbol case. Not a defect, just worth a comment noting the reuse is intentional (the constant's docstring already does this, so this is effectively already addressed — flagging only for visibility).
