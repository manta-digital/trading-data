---
docType: review
layer: project
reviewType: code
slice: daemon-cycle-correctness
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/912-slice.daemon-cycle-correctness.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260803
dateUpdated: 20260803
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "`unactionable_no_calendar` is silently discarded on the STEADY_STATE path"
    location: src/manta_trading/data/acquisition/daemon/daily.py:328
  - id: F002
    severity: concern
    category: correctness
    summary: "A symbol that never gets stamped makes the work list non-terminating — at up to 100 credits per 15-minute tick"
    location: src/manta_trading/data/acquisition/daemon/daily.py:102-147
  - id: F003
    severity: concern
    category: design
    summary: "`getattr(..., \"nothing_actionable\", False) is True` shapes production code around a test double"
    location: src/manta_trading/data/acquisition/daemon/runner.py:525-527
  - id: F004
    severity: concern
    category: dry
    summary: "\"Today's UTC midnight + offset\" is now computed in five places"
    location: src/manta_trading/data/acquisition/daemon/daily.py:209-220
  - id: F005
    severity: concern
    category: maintainability
    summary: "`CycleGranularity` is read through the enum but still written as a bare literal"
    location: src/manta_trading/constants.py:69-85
  - id: F006
    severity: concern
    category: style
    summary: "The change introduces a ruff `I001` failure in a previously clean file"
    location: src/manta_trading/data/acquisition/daemon/runner.py:33
  - id: F007
    severity: note
    category: efficiency
    summary: "The work-list query aggregates over the whole `instruments` table regardless of scope"
    location: src/manta_trading/data/acquisition/daemon/daily.py:106-116
  - id: F008
    severity: note
    category: naming
    summary: "Symbols absent from `instruments` are reported as \"no trading calendar\""
    location: src/manta_trading/data/acquisition/daemon/daily.py:294-300
  - id: F009
    severity: note
    category: test-coverage
    summary: "No load-tier coverage for the new cadence, despite `test/load/` covering the 146 runner"
    location: test/load
  - id: F010
    severity: pass
    category: correctness
    summary: "The resume-correctness fix is well-designed and well-proven"
    location: src/manta_trading/data/acquisition/daemon/runner.py:139-193
  - id: F011
    severity: pass
    category: test-coverage
    summary: "Test design goes after the failure modes a mock cannot reach"
    location: test/integration/test_pending_daily_symbols_sql.py
---

# Review: code — slice 912

**Verdict:** CONCERNS
**Model:** claude-opus-5

## Findings

### [CONCERN] `unactionable_no_calendar` is silently discarded on the STEADY_STATE path

`run_daily_cycle` sets `report.unactionable_no_calendar` at line 289, then at line 328 rebinds `report` to the value returned by `_run_steady_state_cycle`, which constructs a **fresh** `CycleReport()` (line 419). The count set moments earlier is thrown away. The BACKFILL branch mutates the existing `report` in place, so it preserves the field — the two modes disagree, and the mode that loses the data is the dominant production path.

Verified directly:

```
patch pending_daily_symbols -> pending=["AAPL"], unactionable=["NOCAL1","NOCAL2"]
patch _select_daily_mode    -> STEADY_STATE
=> report.unactionable_no_calendar == 0   (expected 2)
```

`nothing_actionable` is unaffected (it is only set on the early-return path). The existing test `test_daily_cycle_work_gating.py::test_partial_scope_narrows_to_pending_only` exercises exactly this code path with a `MagicMock` return value but asserts only on the arguments passed downstream, so it cannot see the loss. `test_unactionable_symbols_warn_once_not_per_symbol` does assert the field, but only on the drained path where the rebind never happens.

No production consumer reads the field today (only `runner.py:526` reads `nothing_actionable`), so the operator-facing warning at line 294 still fires — but the DTO field documented as "counted so the daemon can say what it could not act on" reports zero on the normal path, and the first consumer added will silently get wrong numbers. Fix by having `_run_steady_state_cycle` accept/populate the caller's report, or by copying the field across after the call, and add the missing assertion to the STEADY_STATE test.

### [CONCERN] A symbol that never gets stamped makes the work list non-terminating — at up to 100 credits per 15-minute tick

D6 correctly identifies that a permanently-unstampable symbol turns the derived work list into a non-terminating one that "re-trigger[s] the billable bulk EOD call on every cadence tick" — and closes that hole for the *no-calendar* bucket only. The same hole remains open for every other way a symbol can complete a pass without `update_data_gaps` running:

- `_run_steady_state_cycle` line 468: `target_end is None` → `continue` before any stamp.
- `_run_steady_state_cycle` lines 500/506: `LockNotAvailable` and the generic handler → no stamp.
- BACKFILL: any exception inside `_do_daily_symbol` before line 621 → `_process_daily_symbol` returns TRANSIENT_FAILURE, no stamp.

The `target_end is None` case is reachable *despite* `has_calendar` being true, because the two predicates are not the same test. `calendars_with_sessions` is `SELECT DISTINCT calendar_id FROM trading_sessions` with no time bound, while `_last_completed_session` (line 698) additionally requires `ts.session_open_utc < NOW()`. A calendar holding only future sessions satisfies the first and fails the second. The SQL docstring at lines 141-143 asserts the opposite — "the two must agree, or the work list would hand the cycle a symbol it then refuses to fetch" — which is precisely what happens. Adding `AND session_open_utc < NOW()` to the CTE would make the claim true.

Failure scenario: one stuck symbol with bars and no UNKNOWN gaps. It stays in `pending` forever. `_select_daily_mode` now sees only the *pending* set (line 316), so a scope narrowed to that single caught-up symbol selects STEADY_STATE, and every 15-minute tick issues a fresh full-exchange `/eod-bulk-last-day` call at `EODHD_BULK_EOD_BASE_COST = 100` credits — ~9,400 credits/day against a 100,000-credit quota, versus at most 100/day under the previous once-per-UTC-day gate. `DAILY_CYCLE_RETRY_INTERVAL`'s docstring ("~94 no-op ticks per day, each one a small-table read with no provider call") describes only the fully-drained case and does not hold here. Consider an attempt cap or per-symbol backoff so a symbol that cannot be stamped drops out of `pending` the way the no-calendar bucket does.

### [CONCERN] `getattr(..., "nothing_actionable", False) is True` shapes production code around a test double

The inline comment states the reason outright: "an injected MagicMock auto-creates the attribute as a truthy mock, which would spuriously read as a drained scope." `_run_daily_cycle`'s contract is that it returns a `CycleReport`, which always has the attribute — so both the `getattr` default and the `is True` narrowing exist solely to survive under-specified mocks. This is the "cheap hack" CLAUDE.md forbids, and it costs real robustness: if the attribute is ever renamed, `getattr` silently returns `False` and the drained signal disappears with no error. The correct fix belongs in the tests (`MagicMock(spec=CycleReport)` or a real `CycleReport` return value — most of the new tests already do this) with `drained = daily_report.nothing_actionable` in production.

### [CONCERN] "Today's UTC midnight + offset" is now computed in five places

`daily_pass_boundary` is introduced with a docstring promising it is "the same expression the runner's cadence gate uses, so 'the pass has started' and 'attempted in this pass' can never disagree" — but it is a *copy* of that expression, not a shared one. The identical three-line `datetime(today.year, today.month, today.day, tzinfo=_UTC) + DAILY_CYCLE_START_OFFSET` now appears in `daily_cycle_due` (runner.py:187-189), `ca_update_due` (runner.py:223-224), `sleep_until_next_due_event` (runner.py:260-262), `_next_due_description` (runner.py:477-479), and `daily_pass_boundary`. Five copies can diverge silently, and the docstring's guarantee is only as strong as the next person noticing all five. Export `daily_pass_boundary` (or an equivalent helper) and call it from the runner's four sites.

### [CONCERN] `CycleGranularity` is read through the enum but still written as a bare literal

The enum's docstring says it exists "because these values were previously bare literals at every comparison site, which the project's single-definition-site rule forbids" — but only the read sites in `runner.py` and the new SQL were converted. The producers were not:

- `RunnerConfig.granularities` default (`runner.py:101`) is `frozenset({"daily", "minute"})`, and the field is typed `frozenset[str]`, not `frozenset[CycleGranularity]`.
- The CLI builds the set from literals (`cli/commands/data.py:1193-1199`).
- `CA_UPDATE_SENTINEL_GRANULARITY = "daily"` (`runner.py:56`).
- Every `update_data_gaps(conn, sym, "daily", ...)` and `advisory_lock(conn, sym, "daily", ...)` call (`daily.py:484, 490, 615, 622, 692`) — these are the **writes** whose values `_PENDING_DAILY_SYMBOLS_SQL` then reads back through `CycleGranularity.DAILY`.
- `_select_daily_mode`'s SQL literal `granularity = 'daily'` (`daily.py:386`).

It works today only because `StrEnum` compares equal to `str`. But the new work-list query's correctness depends on the write side and read side agreeing on the token, and the enum's own docstring warns that a mismatch "produces a silent no-match rather than an error" — an empty `pending` list, i.e. a daemon that quietly does nothing. Converting the write sites and typing the config field as `frozenset[CycleGranularity]` is what makes the enum actually protective rather than decorative.

### [CONCERN] The change introduces a ruff `I001` failure in a previously clean file

`from enum import StrEnum` was added at line 33, after the `typing` import and separated by a blank line, splitting the stdlib group. `ruff check --select I` on the base version of `runner.py` reports no import errors; on the branch version it reports `I001`. `I` is in the project's mandatory `[tool.ruff.lint] select` list, so this is a lint regression, auto-fixable with `ruff check --fix`. (`daily.py` also has `I001`, but that one pre-dates the slice — the new `from manta_trading.providers.types import ProviderType` at line 24 lands mid-block and adds to it rather than causing it.)

### [NOTE] The work-list query aggregates over the whole `instruments` table regardless of scope

`calendars_with_sessions` does `SELECT DISTINCT calendar_id FROM trading_sessions` (unbounded), and `symbol_calendar` groups over *all* of `instruments` — the scope restriction is applied only afterwards, via `LEFT JOIN scope`, which Postgres cannot push into the aggregate. So a `--symbols AAPL` invocation still scans and groups the full instrument universe. Under the new 15-minute cadence this runs ~94×/day instead of once, and `DAILY_CYCLE_RETRY_INTERVAL`'s docstring characterises each tick as "a small-table read". Adding `WHERE i.symbol = ANY(%(symbols)s::text[])` to `symbol_calendar` would bound both the group-by and the join without changing semantics. Worth an `EXPLAIN ANALYZE` against prod before dismissing, given the 2026-07-20 incident history.

### [NOTE] Symbols absent from `instruments` are reported as "no trading calendar"

A scope member with no `instruments` row gets `COALESCE(sc.has_calendar, false)` → `false` → the `unactionable_no_calendar` bucket. Counting it rather than dropping it is right (and `test_unknown_symbol_is_unactionable_not_dropped` pins it deliberately), but the warning text — "have no trading calendar and cannot be fetched … see issue #4" — misattributes a typo'd or unseeded `--symbols` argument to issue #4's calendar problem. An operator debugging `mt data daemon run --symbols AAPLL` gets pointed at the wrong issue. Distinguishing "unknown symbol" from "known symbol, no calendar" in the message would cost one extra column.

### [NOTE] No load-tier coverage for the new cadence, despite `test/load/` covering the 146 runner

The python rules require at least one load test for code on the network/concurrency paths, and `test/load/test_146_part1_nfrs.py` / `part2` already hold the daemon runner's NFRs. This slice changes the runner's cycle cadence from once-per-UTC-day to every 15 minutes and adds a per-tick database query, which is exactly the kind of change a load-tier assertion (ticks/day, credits/day, work-list query latency at prod scope size) would pin. The unit-tier simulation in `test_daily_resume_behavior.py::test_no_busy_poll_when_nothing_is_actionable` bounds the *tick count* well, but it cannot bound the query cost or credit spend. The existing 146 load tests skip without a database, so no regression was observable here either way.

### [PASS] The resume-correctness fix is well-designed and well-proven

The start→end stamp inversion is the actual defect, and the fix is the right shape: `RunnerState` is demoted to a pure busy-loop guard with the correctness question moved into `run_daily_cycle` where it can be derived from durable state, `daily_cycle_due` drops the UTC-day comparison entirely, and `sleep_until_next_due_event` is updated in lockstep so the sleep horizon cannot outrun the gate (including the non-obvious empty-`upcoming` case, where falling back to `cap_seconds` would have slept straight past a due cycle). Stamping the end on the exception path is correct and correctly justified. The `_awaiting_first_cycle` qualifier on the D5 wait is the subtle part — without it `--minute --list X` would never terminate — and it is both explained at the point of definition and pinned by an exhaustive parametrisation plus a `--minute --list` regression test with a real timeout.

### [PASS] Test design goes after the failure modes a mock cannot reach

Three things stand out. `_ExplodingHTTPClient` asserts on `__getattr__` rather than on `get()`, so it catches `post`/`stream`/`request` too — the assertion matches the actual invariant ("no billable call") instead of one spelling of it. The integration tests assert structural invariants (partition, ordering, no fan-out) rather than specific symbols, with an explicit note that the test database is documented as unrepresentative, and they set `statement_timeout` per the project's prod-query discipline. And `test_no_fan_out_on_duplicate_instrument_rows` targets the exact non-uniqueness of `instruments.symbol` that the SQL docstring reasons about — a class of bug that the mock-cursor unit tests structurally cannot detect, and that the docstring alone would not have prevented.
