---
docType: tasks
slice: 147-mt-data-status
project: trading
lld: user/slices/147-slice.mt-data-status.md
dependencies:
  - 144-slice.trading-sessions-materialization-data-status-view-rewrite
  - 146-slice.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute
dateCreated: 20260504
dateUpdated: 20260504
reviewedBy: z-ai/glm-5.1 (2026-05-04, concerns resolved)
status: complete
---

## Context Summary

- Slice 146 is merged to main. `data_status` view, `data_gaps`, `trading_sessions`,
  and the long-running daemon (`mt data daemon run`) are all in place.
- This slice adds `mt data status` (read + filter + JSON) and automated
  `trading_sessions` horizon extension (triggered by the status command and the
  daemon's idle tick via a new `register_idle_hook` extension point).
- New code: `auto_extend.py`, `status_table.py`, `@data_app.command("status")`,
  `Runner.register_idle_hook`.
- New directories: `src/manta_trading/data/maintenance/`,
  `src/manta_trading/cli/rendering/`.
- Next slice: 148 (`mt data refetch`).
- Runner is at `src/manta_trading/data/acquisition/daemon/runner.py`.
- Slice design: `user/slices/147-slice.mt-data-status.md`.

---

## Tasks

### T1 — Create branch

- [x] Verify current branch is `main`. Create and switch to `147-slice.mt-data-status`.
  - [x] `git checkout main && git pull` (confirm clean)
  - [x] `git checkout -b 147-slice.mt-data-status`

---

### T2 — Create `data/maintenance/` package and `auto_extend.py`

- [x] Create `src/manta_trading/data/maintenance/__init__.py` (empty).
- [x] Create `src/manta_trading/data/maintenance/auto_extend.py` with:
  - [x] `AutoExtendResult` dataclass: fields `triggered: bool`,
        `calendars_extended: list[str]`, `rows_inserted: int`,
        `horizon_after: dict[str, date]`, `error: str | None = None`.
  - [x] Module-level `_last_extend_at: datetime | None = None` (in-process
        24h gate for daemon use).
  - [x] `maybe_extend_trading_sessions(conn_factory, *, bypass_gate: bool = False)
        -> AutoExtendResult`:
    - Checks `_last_extend_at` gate (skip if within 24h and not `bypass_gate`);
      returns `AutoExtendResult(triggered=False, ...)` immediately if gated.
    - For each calendar in `trading_calendars`, queries
      `MAX(session_date) FROM trading_sessions WHERE calendar_id = cal`.
    - If `max_date IS NULL` or `max_date < today + TRADING_SESSIONS_HORIZON_WARN_DAYS`:
      loads calendar + holiday rows, calls `populate_trading_sessions`, upserts
      via `ON CONFLICT DO UPDATE`, records rows inserted.
    - On `Exception` from the INSERT batch: logs at ERROR via
      `logger.exception`; sets `AutoExtendResult.error`; continues to next
      calendar (does not re-raise).
    - Updates `_last_extend_at = datetime.now()` only on success (no error
      for any calendar).
    - Returns populated `AutoExtendResult`.
  - [x] All DB calls use parameterized queries (no f-string SQL).
  - [x] Uses `TRADING_SESSIONS_HORIZON_WARN_DAYS` and
        `TRADING_SESSIONS_EXTENSION_YEARS` from `manta_trading.constants`.
  - [x] Imports `populate_trading_sessions` from
        `manta_trading.data.base.session_population` (same as `mt data --extend`).

**Commit:** `feat(147): add auto_extend helper (maybe_extend_trading_sessions)`

---

### T3 — Unit tests for `auto_extend.py`

- [x] Create `test/unit/data/maintenance/` directory with `__init__.py`.
- [x] Create `test/unit/data/maintenance/test_auto_extend.py`.
  - [x] Fixture: fake `conn_factory` returning a mock connection with
        controllable `fetchone` results (no real DB needed).
  - [x] `test_no_op_when_horizon_healthy`: MAX returns `today + 120 days` → no
        INSERT called, `AutoExtendResult.triggered = False`.
  - [x] `test_extends_when_horizon_short`: MAX returns `today + 30 days` →
        `populate_trading_sessions` called, rows inserted, `triggered = True`,
        `calendars_extended` non-empty.
  - [x] `test_no_op_when_null_horizon`: MAX returns `NULL` (empty table) →
        extend runs (treats as horizon = None → extends from beginning).
  - [x] `test_gate_blocks_second_call`: Call once (extends), call again
        immediately → second call is a no-op (gated); use `bypass_gate=False`.
  - [x] `test_bypass_gate_ignores_timestamp`: `bypass_gate=True` skips gate
        check regardless of `_last_extend_at`.
  - [x] `test_insert_error_continues`: INSERT raises → `AutoExtendResult.error`
        is set, `triggered = False`, no re-raise.
  - [x] `test_last_extend_at_not_updated_on_error`: After an error, `_last_extend_at`
        remains `None` so the next call retries.
  - [x] All tests pass; `pyright` reports no errors on the module.

---

### T4 — Create `cli/rendering/` package and `status_table.py`

- [x] Create `src/manta_trading/cli/rendering/__init__.py` (empty).
- [x] Create `src/manta_trading/cli/rendering/status_table.py` with:
  - [x] `StatusRow` dataclass: all columns from `data_status` view
        (`symbol`, `granularity`, `health`, `bars_stored`, `first_bar_ts`,
        `last_bar_ts`, `gap_count`, `last_attempt_ts`, `last_attempt_outcome`,
        `target_end_ts`, `effective_start`). Use `date | None` / `datetime | None`
        types; do not use strings.
  - [x] `GapRow` dataclass: columns from `data_gaps`
        (`symbol`, `granularity`, `gap_start`, `gap_end`, `fetch_status`,
        `attempt_count`, `last_attempt_ts`).
  - [x] `StatusReport` dataclass: `scope: str`, `symbol: str | None`,
        `rows: list[StatusRow]`, `gaps: list[GapRow]`,
        `auto_extend: AutoExtendResult | None`, `summary: dict[str, int]`.
  - [x] `_humanize_ts(ts: datetime | None) -> str`: returns `"never"` for
        `None`; `"Xm ago"` / `"Xh ago"` / `"Xd ago"` for past timestamps.
  - [x] `_health_color(health: str) -> str`: returns Rich markup color string.
        Uses a `dict` lookup keyed on health value constants (no inline
        magic strings).
  - [x] `render_status_summary(report: StatusReport) -> Table`: Rich `Table`
        with columns per slice design §Decision B. Footer panel printed
        separately (caller prints both).
  - [x] `render_status_footer(report: StatusReport, *, all_rows: bool = False) -> str`:
        formats the `OK: N  GAPS: N  STALE: N  FAILED: N` summary line. When
        `all_rows=True`, appends a second line: "N rows printed; use `--health`
        or `--symbol` to filter." (Decision C `--all` advisory).
  - [x] `render_status_detail(report: StatusReport) -> list[Renderable]`:
        returns list of Rich renderables (detail panel + gap table).
  - [x] `render_auto_extend_notice(result: AutoExtendResult) -> str | None`:
        returns a notice string when triggered or error is set; `None` if no-op.
  - [x] `status_report_to_json(report: StatusReport) -> str`: serializes via
        `dataclasses.asdict` + custom encoder for `date`/`datetime` → ISO-8601
        string, `None` → JSON `null`. The `auto_extend` block includes `error`
        as an optional field (present and non-null when auto-extension failed).
        Matches schema in slice design §Decision B plus the `error` field.
  - [x] Health constants (`OK`, `GAPS`, `STALE`, `FAILED`) reference an enum
        or the project's existing `HealthStatus` if one exists; no bare strings.

**Commit:** `feat(147): add status_table rendering module`

---

### T5 — Unit tests for `status_table.py`

- [x] Create `test/unit/cli/rendering/` directory with `__init__.py`.
- [x] Create `test/unit/cli/rendering/test_status_table.py`.
  - [x] Fixture factory: `make_status_row(**overrides)` builds a valid `StatusRow`
        with sensible defaults (health=`OK`, bars_stored=1000, etc.).
  - [x] Fixture factory: `make_gap_row(**overrides)` builds a valid `GapRow`.
  - [x] `test_summary_table_columns`: `render_status_summary` returns a `Table`
        with the correct column count and headers.
  - [x] `test_summary_table_rows`: with 3 `StatusRow` fixtures, table has 3 rows.
  - [x] `test_health_coloring`: FAILED row renders with red markup;
        OK renders with green.
  - [x] `test_footer_counts`: `render_status_footer` returns string with all four
        health labels and correct integer counts.
  - [x] `test_footer_all_rows_advisory`: `render_status_footer(..., all_rows=True)`
        includes the row-count advisory line; `all_rows=False` does not.
  - [x] `test_detail_renders_both_granularities`: two rows (daily + minute) for
        same symbol → both appear in detail output.
  - [x] `test_gap_table_ordering`: gaps with out-of-order `gap_start` → rendered
        in ascending order.
  - [x] `test_json_schema_fields`: `status_report_to_json` output parses with
        `json.loads`; all required top-level keys present; timestamps are strings.
  - [x] `test_json_null_for_none`: `None` datetimes serialize as JSON `null`.
  - [x] `test_humanize_ts_never`: `None` → `"never"`.
  - [x] `test_humanize_ts_relative`: past datetimes → `"Xm ago"` / `"Xh ago"`
        format.
  - [x] `test_auto_extend_notice_triggered`: `render_auto_extend_notice` with
        `triggered=True` returns a non-None string containing the calendar name
        and row count.
  - [x] `test_auto_extend_notice_error`: `render_auto_extend_notice` with
        `error="some error"` returns a warning string containing "failed" and
        the manual-command hint.
  - [x] `test_auto_extend_notice_noop`: `render_auto_extend_notice` with
        `triggered=False, error=None` returns `None`.
  - [x] `test_json_auto_extend_error_field`: `status_report_to_json` with an
        `AutoExtendResult(error="oops")` includes `"error": "oops"` in the
        `auto_extend` JSON block.
  - [x] All tests pass; no pyright errors.

---

### T6 — DB fetch helpers

- [x] In a new `src/manta_trading/data/maintenance/status_queries.py`:
  - [x] `fetch_status_rows(conn, *, symbol: str | None, health_filter: list[str] | None,
        granularity: str | None = None) -> list[StatusRow]`: queries `data_status`
        view; applies `WHERE health = ANY(%s)` filter when provided; applies
        `WHERE symbol = %s` filter when symbol given; applies
        `WHERE granularity = %s` filter when granularity given. All filters
        composed as `AND` clauses in a single parameterized query.
        Returns `StatusRow` dataclasses.
  - [x] `fetch_symbol_gaps(conn, symbol: str) -> list[GapRow]`:
        `SELECT * FROM data_gaps WHERE symbol = %s ORDER BY gap_start ASC`.
        Returns `GapRow` dataclasses.
  - [x] `fetch_all_health_counts(conn) -> dict[str, int]`: a single pass over
        `data_status` counting all rows (unfiltered) by health; used for the
        footer aggregate.  May share the same query as `fetch_status_rows`
        if a full fetch is already done, or run a separate lightweight
        `SELECT health, COUNT(*) FROM data_status GROUP BY health` query.
        Implementation chooses the cheaper option; document the choice.
  - [x] All queries use `psycopg.rows.dict_row` or `dataclasses` row factory;
        no manual column indexing.
  - [x] No magic string column names — use the `StatusRow` / `GapRow` field
        names as the single reference.

**Commit:** `feat(147): add status_queries DB fetch helpers`

---

### T7 — Integration tests for `status_queries.py`

- [x] Create `test/integration/test_status_queries.py` (requires real DB;
      consistent with project convention — DB-dependent tests live under
      `test/integration/`, not `test/unit/`).
  - [x] Skip if `MT_TIMESCALE_URL` not set.
  - [x] Seed minimal fixture rows (one `instruments` row, one `data_gaps`
        row with known values) in a setup fixture; clean up after.
  - [x] `test_fetch_status_rows_no_filter`: returns rows for both granularities.
  - [x] `test_fetch_status_rows_health_filter`: `health_filter=["FAILED"]` returns
        only FAILED rows.
  - [x] `test_fetch_status_rows_symbol_filter`: `symbol="SPY"` returns only SPY rows.
  - [x] `test_fetch_status_rows_granularity_filter`: `granularity="daily"` returns
        only daily rows.
  - [x] `test_fetch_symbol_gaps_ordered`: gaps returned in ascending `gap_start`.
  - [x] `test_fetch_symbol_gaps_empty`: unknown symbol → empty list, no exception.
  - [x] `test_fetch_all_health_counts_sums`: total count across all health values
        equals total `data_status` row count.
  - [x] All tests pass.

---

### T8 — `@data_app.command("status")` Typer command

- [x] Add `@data_app.command("status")` to
      `src/manta_trading/cli/commands/data.py`:
  - [x] Flags:
    - `--symbol TEXT` (optional, single symbol for detail view)
    - `--json` (boolean flag, plain `Option(False)`)
    - `--health TEXT` (comma-separated, default `"GAPS,STALE,FAILED"`;
      validated against allowed values)
    - `--granularity TEXT` (optional, `"daily"` or `"minute"`)
    - `--all` (boolean flag; when set, overrides `--health` to all four values)
  - [x] Logic (per slice design §Decision G — command stays thin):
    1. Parse and validate flags; raise `typer.BadParameter` for unknown
       health values.
    2. Open `ConnectionPool` against `MT_TIMESCALE_URL`; exit preflight
       (`_EXIT_PREFLIGHT_FAILED`) if URL not configured.
    3. Call `maybe_extend_trading_sessions(conn_factory)`.
    4. Call `fetch_status_rows` and `fetch_all_health_counts`.
    5. If `--symbol`: call `fetch_symbol_gaps` too.
    6. Build `StatusReport`.
    7. If `--json`: call `status_report_to_json`, print to stdout.
    8. Else: use `rich.Console` to print table, footer (passing `all_rows=True`
       when `--all` is set), and auto-extend notice.
  - [x] Empty `instruments` path: no rows returned → print cold-start hint,
        exit 0.
  - [x] `--symbol UNKNOWN` path: zero rows for symbol → print "not found"
        message, exit 0.
  - [x] Connection pool closed before rendering (Decision G).
  - [x] Follows existing command style in `data.py` (uses `ctx.obj["settings"]`,
        `print_error`, `_EXIT_PREFLIGHT_FAILED` etc.).

**Commit:** `feat(147): add mt data status Typer command`

---

### T9 — Integration test: `mt data status` CLI

- [x] Create `test/integration/test_data_status.py` (subprocess pattern from
      `test_daemon_run.py`).
  - [x] Skip if `MT_TIMESCALE_URL` not set.
  - [x] `test_status_exits_zero`: `mt data status` exits 0.
  - [x] `test_status_json_schema`: `mt data status --json` output parses with
        `json.loads`; has keys `scope`, `rows`, `summary`, `gaps`.
  - [x] `test_status_summary_contains_spy`: with SPY backfilled (from slice 146
        fixture), SPY appears in `--json` output `rows`.
  - [x] `test_status_symbol_detail`: `mt data status --symbol SPY --json` has
        `scope == "symbol"` and `symbol == "SPY"`.
  - [x] `test_status_symbol_unknown`: `mt data status --symbol DOES_NOT_EXIST`
        exits 0 and prints "not found" text.
  - [x] `test_status_health_filter`: seed a `RETRY_EXHAUSTED` gap for SPY,
        run `mt data status --health FAILED --json`, assert SPY appears.
        Cleanup the gap after.
  - [x] `test_status_granularity_filter`: `--granularity daily` → only daily
        rows in JSON output.
  - [x] `test_status_all_flag`: `--all --json` → `rows` contains at least one
        `health == "OK"` entry (SC2).
  - [x] `test_status_empty_registry`: run against a DB where `instruments` has
        zero rows (or use a symbol scope with no matches); assert exit 0 and
        output contains the cold-start hint (SC6). Use `--symbol` with an
        unknown symbol against a populated DB as a proxy if truncating
        `instruments` is not safe in the test environment.
  - [x] `test_status_default_excludes_ok`: `mt data status --json` (no flags)
        returns JSON where every entry in `rows` has `health != "OK"`.
        Seed at least one non-OK row (RETRY_EXHAUSTED gap) to ensure the
        view is non-empty; verify OK rows are absent from output.
  - [x] `test_status_invalid_health_flag`: `mt data status --health INVALID`
        exits non-zero and stderr/stdout contains an error message.
  - [x] All tests pass.

---

### T10 — Integration test: auto-extension via `mt data status`

- [x] Add to `test/integration/test_data_status.py` (or a new
      `test_auto_extend_status.py` if the file exceeds ~150 lines):
  - [x] `test_auto_extend_fires_on_short_horizon`:
    1. `DELETE FROM trading_sessions WHERE calendar_id='NYSE' AND session_date > current_date + 30`
    2. Run `mt data status --json`.
    3. Assert `auto_extend.triggered == True` in JSON output.
    4. Assert `SELECT MAX(session_date) FROM trading_sessions WHERE calendar_id='NYSE'`
       is beyond `current_date + 90 days`.
  - [x] `test_auto_extend_noop_on_healthy_horizon`:
    1. Ensure horizon is healthy (no prior truncation).
    2. Run `mt data status --json`.
    3. Assert `auto_extend.triggered == False` (or key absent).
  - [x] All tests pass.

**Commit:** `test(147): add integration tests for status CLI and auto-extension`

---

### T11 — `Runner.register_idle_hook` extension point

- [x] In `src/manta_trading/data/acquisition/daemon/runner.py`:
  - [x] Add `self._idle_hooks: list[Callable[[], None]] = []` to `Runner.__init__`.
  - [x] Add `def register_idle_hook(self, fn: Callable[[], None]) -> None`
        that appends `fn` to `self._idle_hooks`.
  - [x] In the main loop (between cycles), call each hook wrapped in
        `try/except Exception`: log at ERROR via `logger.exception` on failure;
        loop continues regardless.
  - [x] Runner does not import or reference `auto_extend.py`. The hook
        callable is injected by the caller.
  - [x] Existing `Runner` tests continue to pass (no behavioral change when
        no hooks registered).

**Commit:** `feat(147): add Runner.register_idle_hook extension point`

---

### T12 — Unit tests for `register_idle_hook`

- [x] Add to existing runner unit tests (or create
      `test/unit/data/acquisition/daemon/test_runner_idle_hook.py`):
  - [x] `test_hook_called_between_cycles`: register a hook that increments a
        counter; run 2 cycles → hook called at least once.
  - [x] `test_hook_exception_does_not_crash_runner`: hook raises `RuntimeError`;
        runner loop continues; exit 0.
  - [x] `test_no_hooks_no_op`: runner with no hooks registered completes normally.
  - [x] All tests pass; existing runner tests still pass.

---

### T13 — Wire auto-extension into `daemon_run` command

- [x] In `src/manta_trading/cli/commands/data.py`'s `daemon_run` function:
  - [x] After constructing the `Runner`, register the auto-extend hook:
    ```python
    from manta_trading.data.maintenance.auto_extend import maybe_extend_trading_sessions
    runner.register_idle_hook(
        lambda: maybe_extend_trading_sessions(conn_factory)
    )
    ```
  - [x] The lambda uses `conn_factory` already in scope; no new imports at
        module level (import inside function per existing pattern in `data.py`).
  - [x] `Runner` itself has no import of `auto_extend`.

**Commit:** `feat(147): wire auto-extend hook into daemon_run`

---

### T14 — Integration test: auto-extension via daemon idle tick

- [x] Create `test/integration/test_auto_extend_daemon.py`.
  - [x] Skip if `MT_TIMESCALE_URL` or `MT_EODHD_API_KEY` not set.
  - [x] `test_daemon_extends_short_horizon`:
    1. Truncate NYSE horizon to `today + 30 days`.
    2. Run `mt data daemon run --symbols SPY --daily --stop-when-done` (subprocess).
    3. Assert exit 0.
    4. Assert `MAX(session_date) > current_date + 90` for NYSE.
  - [x] `test_daemon_noop_hook_on_healthy_horizon`:
    1. Ensure healthy horizon.
    2. Run daemon for one tick.
    3. Assert `MAX(session_date)` unchanged.
  - [x] All tests pass.

**Commit:** `test(147): add daemon idle-hook integration test`

---

### T15 — Verification walkthrough

- [x] Run the Verification Walkthrough from the slice design end-to-end
      against `trading_test`. See §Verification Walkthrough in
      `user/slices/147-slice.mt-data-status.md` for all 11 steps.
  - [x] Steps 1–5 (summary, FAILED seed, filters, symbol detail, JSON) pass.
  - [x] Steps 6–7 (auto-extend fires, no-op on healthy horizon) pass.
  - [x] Step 8 (daemon idle tick extends) passes.
  - [x] Step 9 (failure path / warning footer) passes.
  - [x] Step 10 (empty registry hint) passes.
  - [x] Step 11 (latency: `--json` < 2s) passes. **No load test added**: the
        Python rules load-test tier applies to "simulation, network, concurrency,
        or environment-layer paths." `mt data status` is a single sequential
        DB view read followed by serialization — no concurrency, no quota
        budget, no event-loop path. The <2s NFR is bounded by the DB view query
        (already measured sub-second in slice 142) plus constant-time render.
        The `time` subprocess assertion in T15 provides the appropriate CI-gated
        regression check for this command type. A `tests/load/` test would add
        noise without catching a new failure class.
  - [x] Update walkthrough section in slice design with actual output and any
        caveats discovered. Update `dateUpdated` in slice frontmatter.

---

### T16 — `cf check` / `workflow_check` and closeout

- [x] Run `cf check --fix` (or `workflow_check` with fix parameter). Resolve
      any reported issues. Run again until clean.
- [x] Update slice frontmatter: `status: complete`, `dateUpdated: 20260504`.
- [x] Update CHANGELOG.md (user-facing language per slice 146 convention):
  - New entry under `## [Unreleased]`: "`mt data status` command — see the
    health of every symbol in one table; drill into a specific symbol with
    `--symbol`; get machine-readable output with `--json`."
  - Auto-extension entry: "Trading calendar horizon is now kept current
    automatically — running `mt data status` or the daemon extends it when
    needed so you don't have to remember `mt data --extend`."
- [x] Update DEVLOG with 20260504 entry (module names, idle-hook design,
      gating approach, test counts).

**Commit:** `docs(147): mark slice complete; update CHANGELOG, DEVLOG, walkthrough`
