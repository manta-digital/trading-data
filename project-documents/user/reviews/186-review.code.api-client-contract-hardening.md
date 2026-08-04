---
docType: review
layer: project
reviewType: code
slice: api-client-contract-hardening
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/186-slice.api-client-contract-hardening.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260803
dateUpdated: 20260803
findings:
  - id: F001
    severity: concern
    category: uncategorized
    summary: "Dead dependency helper duplicates state already read directly"
    location: src/manta_trading/api_server/deps.py#get_statement_timeout
  - id: F002
    severity: concern
    category: error-handling
    summary: "`get_daily_data` exception handling is asymmetric with `get_minute_data`"
    location: src/manta_trading/market/timescale_daily_db.py
  - id: F003
    severity: pass
    category: design
    summary: "Admission cap is enforced before any DB work"
    location: src/manta_trading/api_server/routes/bars.py
  - id: F004
    severity: pass
    category: design
    summary: "Single source of truth for the distribution version"
    location: src/manta_trading/version.py
  - id: F005
    severity: pass
    category: design
    summary: "Constants derive the span inputs from one measurement"
    location: src/manta_trading/constants.py
---

# Review: code — slice 186

**Verdict:** CONCERNS
**Model:** minimax/minimax-m3

## Findings

### [CONCERN] Dead dependency helper duplicates state already read directly

`get_statement_timeout` is added with a docstring that says "Used by the 504 handler so its message quotes the budget actually in force rather than the default", but the actual handler in `app.py` reads `request.app.state.statement_timeout` directly. No code path consumes the new dependency. Either the handler should be refactored to use it (and the docstring becomes accurate) or the helper should be removed. As written, the project has two ways to reach the same value, which is exactly the DRY violation the slice is otherwise disciplined about.

### [CONCERN] `get_daily_data` exception handling is asymmetric with `get_minute_data`

The diff adds a `psycopg.errors.QueryCanceled` handler to `TimescaleMinuteDataDB.get_minute_data` that re-raises after a WARNING log, so a cancelled minute-grain query propagates and the app-level handler turns it into a 504. `TimescaleDailyDataDB.get_daily_data` is not touched. If it keeps the previous generic `except Exception` pattern (which the slice does not modify or comment on), a cancelled daily-grain query — used for `1d`/`1w`/`1mo`/`1q` — would be swallowed and surface as a 200 with an empty frame, contradicting the handler's explicit claim that "a ``504`` always means a *data* query was cancelled." The test `test_cancelled_bars_query_returns_504` only exercises the minute path (`minute_db.get_minute_data.side_effect = _cancelled()`), so the asymmetry is not currently caught. Either `get_daily_data` needs the same narrow `QueryCanceled` re-raise (preferred — the 20 s budget is generous but not impossible to exceed under load), or the handler's docstring needs to narrow its claim and the test should pin the daily path's behavior either way.

### [PASS] Admission cap is enforced before any DB work

`_admit_range` computes the bar estimate from the request alone and rejects with 422 before any pool checkout or executor dispatch. The `test_rejected_request_checks_out_no_connection` test asserts `_exploding_pool()` is never touched on a rejected request, which is the load-bearing claim. The error message derives both the ceiling number and the max-day number from the live `app.state.max_bars_per_request`, so an operator override cannot produce a message that contradicts the enforced limit — and `test_message_quotes_the_configured_ceiling` pins that.

### [PASS] Single source of truth for the distribution version

Both the CLI's `--version` callback and the OpenAPI `info.version` (now read in `create_app()`) go through `package_version()`. The pre-slice drift (OpenAPI hardcoded `0.1.0` while the distribution was at a different version) is asserted against by `test_openapi_version_comes_from_package_metadata` and `test_generated_version_is_the_package_version`, and the missing-metadata fallback is `dev` with an explicit WARNING log — the "obvious placeholder, not a silent fallback" pattern the project guide requires.

### [PASS] Constants derive the span inputs from one measurement

`BARS_PER_TRADING_DAY` is built as `INTRADAY_MINUTES_PER_TRADING_DAY / GRANULARITY_BAR_MINUTES[granularity]` plus explicit daily/coarser ratios, so correcting the 960-minute measurement moves every intraday limit together. `test_intraday_bars_per_day_are_derived_not_literal` pins the derivation (not the numbers), which is the right test: it survives a measurement update without rewriting. `test_bars_per_trading_day_covers_every_granularity` ensures FastAPI's validated granularity input cannot trigger a `KeyError` in the admission path.

---

## Resolutions (2026-08-04)

### F001 — accepted, helper removed

Correct as written. `get_statement_timeout` was added alongside `get_max_bars`
by symmetry and never wired up: the `504` handler is a Starlette exception
handler, not a route, so it cannot take a `Depends(...)` — it can only read
`request.app.state` directly, which is what it does. The helper was therefore
never reachable, and its docstring described a call site that does not exist.

Removed. `get_max_bars` stays: `get_bars` is a route and does consume it.

### F002 — premise rejected on evidence; test gap accepted and closed

The finding is conditional ("**If** it keeps the previous generic
`except Exception` pattern"), and the condition is false. `get_daily_data` has
**no** `try/except` at all — the only `except Exception` in
`timescale_daily_db.py` is in `_init_pool`, which logs and re-raises. A
cancelled daily query has always propagated. That is also what the Phase 6
walkthrough measured directly on prod: step 7b3, with
`MT_API_STATEMENT_TIMEOUT=100ms`, a `1d` request over 2004–2026 returned
`HTTP 504` with the configured budget quoted, from
`TimescaleDailyDataDB._pool`. So the described failure mode — a swallowed daily
cancellation surfacing as an empty `200` — cannot occur, and D10's claim holds
for all nine granularities.

The reviewer's fallback recommendation is nonetheless right, and is the real
value of this finding: nothing *pinned* the daily behavior. The asymmetry is in
the implementation (one class needed an explicit re-raise, the other needed
nothing), which makes it invisible from the route and easy to break later — a
`try/except` added to the daily class for an unrelated reason would silently
falsify D10 for `1d`/`1w`/`1mo`/`1q` with no test failing.

**One** test added:
`test_minute_db_error_semantics.py::test_daily_class_does_not_swallow_a_cancellation`,
which pins the guarantee at the class level — where the asymmetry actually
lives, and where a future blanket handler would be introduced.

A route-level mirror of the minute-path test was written first and then
deleted: it mocks `daily_db`, so it exercises the app's `504` handler rather
than the daily class, and `test_cancelled_bars_query_returns_504` already covers
that handler. It would have been a second test of already-tested wiring, not
coverage of the gap this finding identified.

No production change. Suite: 1,804 passing (was 1,803).
