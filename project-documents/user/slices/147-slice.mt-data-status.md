---
docType: slice-design
slice: 147-mt-data-status
project: trading
parent: user/architecture/140-slices.data-quality-operations.md
dependencies:
  - 144-slice.trading-sessions-materialization-data-status-view-rewrite
  - 146-slice.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute
interfaces:
  - 148-slice.mt-data-refetch    # operator reads `status` to decide what to refetch
  - 149-slice.mt-data-audit      # operator reads `status` to scope an audit
relatedReference: user/architecture/140-arch.data-quality-operations.md
dateCreated: 20260503
dateUpdated: 20260504
reviewedBy: moonshotai/kimi-k2.6 (2026-05-04, round 2 — concerns resolved)
status: complete
---

# Slice Design: 147 — `mt data status` + `trading_sessions` Auto-Extension

## Overview

Slice 142 created the `data_status` view; slice 144 finished it (rewrote
the CTE to project `target_end_ts` from `trading_sessions`); slice 145/146
made the daemon write into the tables that view reads. This slice
delivers the **operator-facing read surface** for that pipeline:

1. **`mt data status [--symbol X] [--json]`** — single command, no
   subcommands. Default: a Rich table summarizing every (symbol,
   granularity) row in `data_status`. With `--symbol`, prints one
   detail block plus the symbol's full `data_gaps` listing
   (including `fetch_status` per row). `--json` emits the same data
   as JSON for machine consumption.
2. **Automated `trading_sessions` horizon extension.** Slice 144 ships
   `mt data --extend` as a manual command and warns at 90 days. This
   slice adds an automatic call equivalent to `mt data --extend` that
   fires when `mt data status` runs (and from the long-running
   daemon's idle tick). Operators stop having to remember the
   maintenance command.

After this slice the operator's "what's the state of the world" loop
collapses to one read command, and the materialized session horizon
that `data_status` depends on stays current without manual
intervention.

## Value

1. **One-glance situational awareness.** `mt data status` prints, in
   a single Rich table, the health of every (symbol, granularity)
   pair that the registry tracks. Operator sees `OK / GAPS / STALE /
   FAILED` counts immediately; `--symbol X` drills into a specific
   row and lists its raw `data_gaps`.
2. **Machine-consumable output.** `--json` makes the same data
   trivially scriptable — alerting, dashboarding, or piping into
   `jq` — without parsing Rich tables.
3. **Self-maintaining horizon.** `data_status.target_end_ts` is
   sourced from `trading_sessions`; if the horizon lapses, status
   silently degrades (target_end becomes stale; `bars_expected`
   under-counts). Auto-extension turns the maintenance step from
   "operator must remember" into "happens on first read of the day."

## Non-Goals

- **No `mt data status --refetch` shortcut.** Refetch is slice 148.
  Status does not mutate bars, `data_gaps`, or `acquisition_state`
  (the pipeline's data tables). The auto-extension side effect writes
  only to `trading_sessions` (the calendar infrastructure table), as
  explicitly scoped in the slice plan entry — this is a maintenance
  write that keeps the target window valid, not a data-pipeline write.
- **No materialized `data_status` table.** The view is fast enough
  at current scale (slice 142 verified sub-second at ~57k rows;
  slice 144's CTE rewrite preserved that). Materialize only if
  measurement says so — left for future work.
- **No history of past status snapshots.** Status reflects the live
  view. Time-series of past health is deferred.
- **No new health states.** The view already classifies
  `OK / GAPS / STALE / FAILED`; status renders what the view says.
- **Auto-extension does not replace `mt data --extend`.** The
  manual command stays for explicit operator control, CI hooks, and
  the `--strict` exit code. Auto-extension is opportunistic, not
  authoritative.

## Inputs

### From slice 144
- `trading_sessions(calendar_id, session_date, session_open_utc,
  session_close_utc)` — read by the auto-extension probe and by the
  `data_status` view.
- `mt data --extend` CLI command (`@data_app.command("extend")` in
  `cli/commands/data.py:2281`) — auto-extension invokes the same
  `populate_trading_sessions` code path the manual command uses (not
  by spawning a subprocess; by calling the underlying function).
- `TRADING_SESSIONS_HORIZON_WARN_DAYS = 90` — threshold at which
  auto-extension triggers (constants module).
- `TRADING_SESSIONS_EXTENSION_YEARS = 2` — extension target year span.

### From slice 142/146 (already in place)
- `data_status` view (returns one row per (symbol, granularity)
  pair, columns per `140-arch §"One status view"`).
- `data_gaps` table — read for `--symbol X` detail listings.
- `acquisition_state` — read transitively via the view's LEFT JOIN.

### From slice 146
- Long-running daemon's idle-tick hook — auto-extension also runs
  here so a daemon that's been up for weeks doesn't drift out of
  horizon between status invocations. Implementation detail in
  Approach §Auto-extension trigger points.

## Outputs

### Code
- `src/manta_trading/cli/commands/data.py` — new `@data_app.command("status")`
  (Typer command, not a sub-app; arch §"`mt data status [--symbol X]`"
  specifies "single command, no subcommands").
- `src/manta_trading/data/maintenance/auto_extend.py` (new module) —
  `maybe_extend_trading_sessions(conn_factory) -> AutoExtendResult`
  pure helper; reused by status command and daemon idle tick.
  `AutoExtendResult` dataclass: `(triggered: bool, calendars_extended:
  list[str], rows_inserted: int, horizon_after: dict[str, date])`.
- `src/manta_trading/cli/rendering/status_table.py` (new module) —
  Rich rendering helpers (default summary table; `--symbol` detail
  block; gap listing). Pure functions; take dataclasses, return
  Rich renderables. JSON encoder uses the same dataclasses with
  `dataclasses.asdict` + a date/datetime serializer.
- `src/manta_trading/data/runner.py` — expose a narrow
  `register_idle_hook(fn: Callable[[], None]) -> None` on the
  `Runner` class. Slice 147 registers `maybe_extend_trading_sessions`
  via this hook at construction time (in `cli/commands/data.py`'s
  `daemon_run` command). Runner invokes registered hooks between
  cycles; hooks are called at most once per 24h per hook (gate
  implemented inside the hook, not the runner). The runner itself
  does not import or reference `auto_extend.py` — the dependency
  arrow stays downstream-to-upstream, not upstream-to-downstream.

### Config / Constants
- `TRADING_SESSIONS_AUTO_EXTEND_THRESHOLD_DAYS: int = 90` (alias /
  reuse of `TRADING_SESSIONS_HORIZON_WARN_DAYS`; if they should
  diverge later, split then). Decision: reuse the 90-day constant.
  Rationale: the warn threshold and the auto-extend threshold answer
  the same question ("is the horizon close enough to today to
  matter?").

### Deletions
- None. `mt data --extend` stays as the explicit operator command.

### Documentation
- CHANGELOG: user-facing summary of `mt data status` + auto-extension.
- DEVLOG: implementation notes (module list, idle-hook design, gating approach).
- Walkthrough section in this slice file (refined post-implementation).

## Approach

### Decision A: Single command, not a sub-app

Architecture explicitly says "single command, no subcommands."
Operator invokes:

```
mt data status                 # universe summary table
mt data status --symbol AAPL   # detail + gap listing for AAPL
mt data status --json          # JSON output of summary
mt data status --symbol AAPL --json   # JSON output of detail
```

No `mt data status list`, `mt data status detail`, or similar. Flags
shape behavior; the command is one Typer entry point.

### Decision B: Output shape

**Default summary (no `--symbol`)** — Rich table, one row per
`data_status` row. Columns:

| Column | Source | Notes |
|---|---|---|
| symbol | `data_status.symbol` | |
| granularity | `data_status.granularity` | `daily` or `minute` |
| health | `data_status.health` | colored: OK=green, GAPS=yellow, STALE=blue, FAILED=red |
| bars | `data_status.bars_stored` | thousand-separated |
| first_bar | `data_status.first_bar_ts` | date only (drop time) |
| last_bar | `data_status.last_bar_ts` | date only |
| gaps | `data_status.gap_count` | from view's `gap_counts` CTE |
| last_attempt | `data_status.last_attempt_ts` | "2h ago" / "3d ago" / "never" |
| outcome | `data_status.last_attempt_outcome` | shortened: ok/partial/empty/fail |

Footer line: aggregate counts by health (`OK: 12,341  GAPS: 84
STALE: 9  FAILED: 2`). At ~57k rows the default table is too long
to dump unfiltered; see Decision C for filtering.

**`--symbol X` detail** — two-section Rich render:

1. Detail panel: every column from the view's row for X (both
   granularities side-by-side if both present), formatted vertically.
2. Gap table: every `data_gaps` row for X, columns
   `granularity / gap_start / gap_end / fetch_status /
   attempt_count / last_attempt_ts`. Rows ordered by
   `gap_start ASC`. `fetch_status` is colored (UNKNOWN=dim,
   PROVIDER_HOLE=blue, FAILED_RETRYABLE=yellow,
   RETRY_EXHAUSTED=red).

**`--json` mode** — replaces Rich rendering with `json.dumps` of a
`StatusReport` dataclass. Schema:

```jsonc
{
  "scope": "all" | "symbol",            // reflects --symbol presence
  "symbol": "AAPL" | null,              // when scope=="symbol"
  "rows": [                             // one entry per data_status row
    {"symbol": "...", "granularity": "...", "health": "...", "bars_stored": 0,
     "first_bar_ts": "...", "last_bar_ts": "...", "gap_count": 0,
     "last_attempt_ts": "...", "last_attempt_outcome": "...",
     "target_end_ts": "...", "effective_start": "..."}
  ],
  "gaps": [                             // populated only when scope=="symbol"
    {"symbol": "...", "granularity": "...", "gap_start": "...",
     "gap_end": "...", "fetch_status": "...", "attempt_count": 0,
     "last_attempt_ts": "..."}
  ],
  "auto_extend": {                      // present when triggered
     "triggered": true,
     "calendars_extended": ["NYSE"],
     "rows_inserted": 504,
     "horizon_after": {"NYSE": "2028-12-31"}
  },
  "summary": {"OK": 12341, "GAPS": 84, "STALE": 9, "FAILED": 2}
}
```

Dates and timestamps as ISO-8601 strings; `null` for SQL NULLs.
`last_attempt_ts` is the raw ISO timestamp in JSON, not a relative
"2h ago" string — that humanization is for the Rich table only.

### Decision C: Default-summary scope filters

At ~57k rows the unfiltered table is unusable. Default behavior
filters out OK rows:

- Without `--symbol`: print only rows where `health != 'OK'`.
- `--all` flag: print every row including OK. Footer warns "57,234
  rows printed; use `--health` or `--symbol` to filter."
- `--health LIST` flag: comma-separated subset of `OK,GAPS,STALE,FAILED`.
  Default with no flag is `GAPS,STALE,FAILED`.
- `--granularity {daily,minute}` flag: limit by granularity.

The footer aggregate counts (OK/GAPS/STALE/FAILED tallies) reflect
the **unfiltered** universe, not the filtered table — operator
always sees the true counts even when filtered.

JSON mode applies the same filters as the table. `--all --json`
emits every row.

### Decision D: Auto-extension trigger points and gating

**Where it fires:**

1. Inside `mt data status` (any invocation) — once the connection
   pool is open, before reading the view.
2. Inside the long-running daemon's main loop (slice 146) — at the
   top of each idle tick, gated by a once-per-24h sentinel.

**Gating logic** (shared by both call sites, in `auto_extend.py`):

```text
maybe_extend_trading_sessions(conn_factory):
  for cal in trading_calendars:
    max_session = SELECT MAX(session_date) FROM trading_sessions
                  WHERE calendar_id = cal
    if max_session IS NULL OR max_session < today + 90 days:
      call populate_trading_sessions(...) for [max_session+1, ...]
      log INFO with calendar_id and rows_inserted
      record AutoExtendResult.calendars_extended.append(cal)
  return AutoExtendResult(...)
```

Every status invocation runs this scan (cheap: one indexed `MAX`
per calendar; ~5 calendars). The actual extension only runs when
the horizon is short. `mt data status --json` includes the
`AutoExtendResult` so scripts can detect the action.

**Daemon-side gating.** `maybe_extend_trading_sessions` carries its
own 24h gate using an in-process timestamp (`_last_extend_at:
datetime | None` module-level variable, initialized to `None`).
When the runner's idle-tick fires the hook, the function checks
`datetime.now() - _last_extend_at < timedelta(hours=24)` and
returns immediately if within the window. On the first call (or
after 24h), it runs the MAX probe and extends if needed, then
updates `_last_extend_at`. No sentinel row is written to any DB
table — `acquisition_state` remains strictly per-symbol run-state
as defined by the architecture.

**Status-side sentinel: not used.** Status is invoked rarely
(operator command), so the cost of one MAX query per calendar per
invocation is negligible. No gating needed.

### Decision E: Failure modes for auto-extension

If `populate_trading_sessions` raises (network-free; pure Python
calendar arithmetic — failure here means a programming error or a
malformed `trading_calendars`/`trading_holidays` row):

- **In `mt data status`**: log the error with `logger.exception`
  and continue. Print a warning footer: "Auto-extend failed for
  NYSE; run `mt data --extend --calendar NYSE` manually." Status
  output is still produced. Exit code stays 0 — the read succeeded.
- **In the daemon idle tick**: log and continue. Daemon does not
  exit. Sentinel is **not** advanced (so the next idle tick will
  retry). If failure persists for >7 days the daemon emits a
  WARNING per attempt; alerting is the operator's responsibility.

If the database connection itself fails (timescale_db_url misconfigured
or DB down), both call sites raise — that's not auto-extension's
problem to swallow.

### Decision F: Health-state computation lives in the view, not Python

`data_status.health` is computed in SQL (slice 142's view). Status
command renders what the view returns; it does not re-derive
health from the constituent columns. This keeps one source of
truth. Status's only computation is:

- Humanizing timestamps for the Rich table (`"2h ago"` etc.);
- Aggregate footer counts (`Counter(row.health for row in rows)`).

### Decision G: Connection / rendering boundary

`data.py` command stays thin: parse Typer args → open pool → call
two pure functions (`fetch_status_rows`, `fetch_symbol_gaps`) → pass
results to `render_status_summary` / `render_status_detail`.
Everything below the command is testable without Typer or a live
DB (with fixture rows). Command-level integration test is
subprocess-based, follows the slice 146 pattern.

## Failure Modes

### Stale view caches
None — `data_status` is a non-materialized view. Every read
recomputes against current data. No staleness possible.

### Horizon end inside target window
If `trading_sessions` somehow has a max date in the past relative
to "today minus the grace period," the view's
`exchange_completed_close` CTE projects `target_end_ts` as the last
populated session, not as today. `bars_expected` under-counts; live
symbols look healthier than they are. Auto-extension prevents this
in the steady state. If auto-extension is wedged
(see Decision E), status's footer warning is the alerting
mechanism.

### Symbol with no `acquisition_state` row
Already handled by slice 142's LEFT JOIN. Renders with
`last_attempt_ts = null`, `last_attempt_outcome = null`, `health =
STALE` (per the view's CASE), and the table prints "never" / "—".

### Symbol absent from `instruments` table
Cannot appear in `data_status` (the view CROSS JOINs from
`instruments`). `mt data status --symbol UNKNOWN` returns zero rows;
command prints "No data_status row for UNKNOWN. Is the symbol in
the instruments registry?" and exits 0 (or exit 4 in `--strict`-style
contexts; for now exit 0, this is operator-not-finding-input, not
a system failure).

### Empty universe
If `instruments` is empty (cold-start hasn't run), `data_status`
returns zero rows. Status prints "No instruments found. Run
`mt data instruments rebuild` to populate the registry." Exit 0.

### DB I/O path failures
All DB calls in this slice (pool open, `data_status` view query,
`data_gaps` query, `MAX(session_date)` scans, `populate_trading_sessions`
INSERT batch) are subject to the standard psycopg timeout stack:

- **Pool open timeout**: `ConnectionPool` raises `PoolTimeout` if
  no connection is acquired within `pool_timeout` (default 30s).
  The command propagates this exception; Typer prints the error and
  exits non-zero. No partial output is emitted.
- **Statement timeout**: `data_status` view queries and gap queries
  run under the default server-side statement timeout (configured via
  `statement_timeout` on the timescale DB). If the view query exceeds
  this limit, `psycopg` raises `QueryCanceled`; the command exits
  non-zero with the error text. No partial output.
- **Peer disconnect mid-query**: `psycopg` raises `OperationalError`.
  Command exits non-zero. Same behavior as pool open failure.
- **Auto-extension INSERT batch failure**: if the `executemany` raises,
  the transaction is rolled back (psycopg's default rollback on
  exception). `maybe_extend_trading_sessions` catches the exception,
  logs at ERROR via `logger.exception`, and returns an
  `AutoExtendResult(triggered=False, error=str(exc))`. The status
  command continues to the view query and prints the warning footer
  (per Decision E). Idempotency is guaranteed by `ON CONFLICT DO UPDATE`.

No retry logic is introduced in this slice. Transient DB failures
produce a non-zero exit (for status) or a logged warning (for
auto-extend in daemon context); operators re-run or investigate.

### Daemon idle-hook hang or exception
`Runner.register_idle_hook` wraps every hook invocation in a
`try/except Exception`:

- **Exception raised**: logged at ERROR via `logger.exception`;
  the daemon loop continues. The hook's in-process `_last_extend_at`
  is **not** updated on failure, so the next idle tick retries.
- **Hang / long runtime**: `maybe_extend_trading_sessions` uses the
  same `ConnectionPool` as the rest of the daemon, which inherits
  the timescale DB's `statement_timeout`. Pure Python calendar
  arithmetic inside `populate_trading_sessions` is CPU-only and
  bounded (generating at most ~500 rows/year × 2 years = ~1000 rows
  per calendar; negligible). No additional timeout wrapping is
  needed. If a future hook performs unbounded I/O, the hook itself
  is responsible for adding a deadline; the runner does not impose
  one globally.
- **Hook does not block the daemon's main acquisition loop.** Hooks
  run synchronously between cycles (not concurrently), so a slow
  hook delays the next cycle start. The 24h in-process gate ensures
  hooks run at most once per process lifetime per 24h period, keeping
  the overhead negligible in steady state.

## Non-Functional Targets

- **View query latency**: `data_status` view query completes in <1s
  at ~57k instruments (per arch §"Performance pattern" NFR, verified
  by slice 142). This slice does not change the view; the NFR carries
  forward unchanged.
- **Latency**: `mt data status` (default-filtered, summary) returns
  in <2s end-to-end at ~57k instruments × 2 granularities = ~114k
  view rows, filtered to non-OK. The view query is sub-second;
  rendering Rich for hundreds-to-low-thousands of non-OK rows adds
  negligible overhead.
- **Latency (JSON)**: `mt data status --json` returns in <2s for
  the unfiltered universe (114k rows serialized). Profile if it
  exceeds.
- **Auto-extend overhead**: <100ms per status invocation in the
  steady state (when the horizon is fine). One MAX query per
  calendar (~5), all indexed.
- **No additional DB connections held open across the rendering
  step**: fetch all rows, close the connection, then render. (Rich
  rendering on tens of thousands of rows can take longer than the
  view query; we don't hold the connection during it.)

## Cross-Slice Dependencies

### Hard prerequisites
- **Slice 144** — `trading_sessions` table, `mt data --extend` CLI,
  `populate_trading_sessions` helper, `data_status` view's
  `target_end_ts` projection. Without 144 the view returns
  `target_end_ts = NULL` and `bars_expected` cannot be computed
  correctly.
- **Slice 146** — long-running daemon. Auto-extension's daemon-side
  hook plugs into slice 146's idle tick. (Status-side hook works
  without the daemon, so partial functionality is available even
  if 146 is rolled back; the daemon hook is the second call site.)

### Soft consumers
- **Slice 148** (`mt data refetch`) — operator workflow: read
  status, see GAPS/FAILED for symbol X, run refetch X. No code
  dependency.
- **Slice 149** (`mt data audit`) — operator workflow: read
  status to scope an audit. No code dependency.

## Migration Plan

### Step 1 — Build the auto-extension helper
Write `auto_extend.py` and unit-test it in isolation against a
fixture DB whose `trading_sessions` is artificially short-horizon
and whose `trading_calendars` is the real ~5-row set. Verify it's a
no-op when horizon is healthy.

### Step 2 — Build the rendering layer
Write `status_table.py` against fixture row-dataclasses. No DB,
no Typer. Unit tests verify Rich output structure (column count,
row count, color codes via Rich's `Console.capture`).

### Step 3 — Wire the Typer command
Add `@data_app.command("status")` to `cli/commands/data.py`. Command
opens pool, calls `maybe_extend_trading_sessions`, fetches view rows,
optionally fetches gap rows for `--symbol`, calls renderer.
Integration test via subprocess (pattern from slice 146 §
`test_daemon_run.py`).

### Step 4 — Wire the daemon idle-tick hook
Add `register_idle_hook` to the `Runner` class. In
`cli/commands/data.py`'s `daemon_run` command, register a
`maybe_extend_trading_sessions` closure. Integration test seeds a
near-horizon-end state, runs the daemon for one tick
(`--stop-when-done`), asserts rows were inserted into
`trading_sessions` and that a second tick is a no-op (in-process
24h gate holds).

### Step 5 — Documentation + CHANGELOG/DEVLOG
Walkthrough below verified end-to-end against `trading_test`.

## Data Flows

### Default `mt data status` invocation

```
operator
  └─ mt data status
       ├─ open ConnectionPool to MT_TIMESCALE_URL
       ├─ maybe_extend_trading_sessions(conn_factory)
       │    ├─ for each calendar in trading_calendars:
       │    │    └─ MAX(session_date) — if < today+90d, populate forward
       │    └─ return AutoExtendResult
       ├─ fetch_status_rows(conn, filter=health_filter)  # the view
       ├─ Counter(r.health for r in rows)  # full universe footer
       ├─ render_status_summary(rows, summary, auto_extend)
       └─ stdout: Rich table or JSON
```

### `mt data status --symbol AAPL`

```
operator
  └─ mt data status --symbol AAPL
       ├─ open pool, maybe_extend_trading_sessions(...)
       ├─ fetch_status_rows(conn, symbol="AAPL")  # both granularities
       ├─ fetch_symbol_gaps(conn, symbol="AAPL")  # all data_gaps rows for AAPL
       ├─ render_status_detail(rows, gaps, auto_extend)
       └─ stdout
```

### Auto-extend (status path, near-horizon-end)

```
maybe_extend_trading_sessions(conn_factory)
  ├─ for cal in [NYSE, NASDAQ, NYSE_ARCA, BATS, NYSE_MKT, INDX]:
  │    ├─ max_date := SELECT MAX(session_date) ...
  │    ├─ if max_date < today + 90 days:
  │    │    ├─ load (timezone, market_open, market_close) from trading_calendars
  │    │    ├─ load holidays for cal_id
  │    │    ├─ rows := populate_trading_sessions(cal_id, max_date+1, today + 2y)
  │    │    └─ executemany(INSERT ... ON CONFLICT DO UPDATE)
  │    └─ append cal to AutoExtendResult.calendars_extended
  └─ return AutoExtendResult
```

This is the same code path as `mt data --extend` (Step 3 in slice
144's command), called as a Python function — not a subprocess.

## Risks

### `--all` over the full universe is too slow
Risk: 114k Rich-rendered rows takes >30s and produces unreadable
scrolling output. Mitigation: Decision C filters non-OK by default;
`--all` is opt-in with a footer warning; JSON mode is the
recommended path for "I want everything." Measure during
implementation; if `--all` is genuinely unusable at scale, consider
falling back to JSON-only output for `--all` (with a stderr notice)
rather than spending render cycles on tens of thousands of Rich
rows that won't fit a terminal.

### Auto-extend silently masks an operator forgetting `--strict`
Risk: operator stops noticing horizon health because auto-extend
keeps it healthy; a deeper bug (calendar metadata corrupt,
holiday table outdated) gets papered over. Mitigation: status's
JSON includes `auto_extend.triggered`; Rich output prints a notice
when triggered. The `mt data --extend --strict` path stays
authoritative for CI / cron alerting. Auto-extend is convenience,
not the safety net.

### Concurrent status + daemon both trigger auto-extend
Risk: a status invocation and a daemon idle-tick both call
`maybe_extend_trading_sessions` within the same 24h window.
Mitigation: the INSERT uses `ON CONFLICT DO UPDATE` — concurrent
writers are idempotent. Worst case: both run back-to-back; the
second is a no-op. The in-process 24h gate in the daemon prevents
unnecessary DB traffic within a single daemon process; it does not
coordinate across process boundaries (status and daemon are separate
processes). Acceptable.

## Success Criteria

### Functional
1. `mt data status` (no flags) prints a Rich summary table filtered
   to non-OK rows, with a footer reporting `OK / GAPS / STALE /
   FAILED` counts over the full universe.
2. `mt data status --health OK,GAPS,STALE,FAILED` prints every row
   (equivalent to `--all`).
3. `mt data status --symbol AAPL` prints AAPL's detail block plus
   its full `data_gaps` listing.
4. `mt data status --json` prints valid JSON conforming to the
   schema in Decision B; includes `auto_extend` block when
   triggered.
5. `mt data status --symbol UNKNOWN` (symbol not in registry)
   prints a clear "not found" message and exits 0.
6. `mt data status` against an empty `instruments` table prints
   the cold-start hint and exits 0.
7. Auto-extension fires when any calendar's `MAX(session_date) <
   today + 90 days`; running status with a healthy horizon is a
   no-op (no INSERTs).
8. Auto-extension failure (e.g., a malformed `trading_holidays`
   row) prints a warning footer in status output but does not
   abort the read; status exit 0.
9. Health classifications match the view's logic exactly:
    - All sessions covered, no gaps, recent attempt → `OK`
    - One missing session inside target window → `GAPS`
    - No `acquisition_state` row, or last attempt > staleness
      threshold → `STALE`
    - At least one `data_gaps` row in target window with
      `fetch_status = RETRY_EXHAUSTED` → `FAILED`
10. Target-window correctness during an active session: today's
    not-yet-closed session is **not** counted in `bars_expected`;
    a fully covered symbol stays `OK` mid-session. After
    `session_close + LATE_BAR_GRACE_PERIOD`, today enters the
    target window and missing bars register.
11. Daemon idle-tick auto-extends idempotently, gated by an
    in-process 24h timestamp (`_last_extend_at`); no sentinel row
    is written to any DB table. A second daemon pass within the 24h
    window does not re-run the MAX probe or any INSERTs.

### Technical
12. Every code path is covered by a unit test (renderer takes
    fixture rows, auto-extend helper takes fixture DB state).
13. Integration test runs `mt data status` against `trading_test`
    end-to-end and asserts table content + exit code.
14. Integration test seeds an artificially-stale horizon and
    asserts auto-extension runs.
15. `--json` output round-trips through `json.loads` cleanly; all
    timestamps are ISO-8601 strings; SQL NULLs map to JSON `null`.
16. No hard-coded magic strings — health labels and `fetch_status`
    values use the existing enums (per project rule "no magic
    strings").

### Integration
17. Slice 148 (refetch) can read status output (JSON) to drive its
    own scope-selection workflow.
18. Slice 149 (audit) ditto.

## Verification Walkthrough

This is the demo script. Each step lists the command, the data
state it assumes, and the expected behavior. The walkthrough is
designed to be runnable end-to-end against `trading_test` after the
slice is implemented.

### 0. Prerequisites
- Slice 146 merged (long-running daemon; `data_status` view
  populated).
- `trading_test` DB has a non-empty `instruments` registry and at
  least one symbol with stored bars (e.g., SPY daily backfilled
  via slice 146's daemon).
- `MT_TIMESCALE_URL` and other DB env vars set.

### 1. Default summary
```
mt data status
```
Expect: Rich table of non-OK rows. If the daemon is fully caught
up, the table may be empty; the footer line still prints
`OK: <N>  GAPS: 0  STALE: 0  FAILED: 0`. Exit 0.

### 2. Force a non-OK row, verify it appears
Insert a synthetic gap:
```sql
INSERT INTO data_gaps (symbol, granularity, gap_start, gap_end,
                       fetch_status, attempt_count)
VALUES ('SPY', 'daily', '2024-01-02 14:30+00', '2024-01-02 21:00+00',
        'RETRY_EXHAUSTED', 5);
```
Run:
```
mt data status
```
Expect: SPY/daily appears with `health = FAILED` (red), `gaps = 1`.
Footer count for `FAILED` increments by 1.

Cleanup the synthetic row before continuing:
```sql
DELETE FROM data_gaps WHERE symbol='SPY' AND attempt_count = 5
                       AND fetch_status='RETRY_EXHAUSTED';
```

### 3. Filter flags
```
mt data status --health OK
```
Expect: only OK rows print; footer unchanged (counts reflect full
universe).

```
mt data status --granularity daily
```
Expect: only daily rows.

### 4. Symbol detail
```
mt data status --symbol SPY
```
Expect: detail panel for SPY/daily (and SPY/minute if minute is
populated), followed by SPY's `data_gaps` listing (likely empty
if backfill is complete). Exit 0.

```
mt data status --symbol DOES_NOT_EXIST
```
Expect: "No data_status row for DOES_NOT_EXIST. Is the symbol in
the instruments registry?" Exit 0.

### 5. JSON mode
```
mt data status --json | jq '.summary'
```
Expect: object with OK/GAPS/STALE/FAILED integer counts.

```
mt data status --symbol SPY --json | jq '.gaps | length'
```
Expect: integer (0 or higher).

```
mt data status --json | jq '.rows[0]'
```
Expect: row object with all schema fields populated; timestamps
ISO-8601 strings.

### 6. Auto-extension fires (manual horizon shrink)
Truncate horizon:
```sql
DELETE FROM trading_sessions
WHERE calendar_id='NYSE' AND session_date > current_date + 30;
```
Verify:
```sql
SELECT MAX(session_date) FROM trading_sessions WHERE calendar_id='NYSE';
-- expect: today + 30 days (approx)
```
Run:
```
mt data status
```
Expect: stderr / footer notice "Auto-extended trading_sessions for
NYSE: <N> rows inserted (horizon now <YYYY-MM-DD>)". Verify:
```sql
SELECT MAX(session_date) FROM trading_sessions WHERE calendar_id='NYSE';
-- expect: 2028-12-31 (current_year + TRADING_SESSIONS_EXTENSION_YEARS)
```

### 7. Auto-extension is a no-op on healthy horizon
Run again immediately:
```
mt data status
```
Expect: no auto-extend notice; horizon unchanged.

### 8. Auto-extension via daemon idle tick
Shrink the horizon again (as in step 6), then run the daemon:
```
mt data daemon run --symbols SPY --daily --stop-when-done
```
Verify the horizon was extended:
```sql
SELECT MAX(session_date) FROM trading_sessions WHERE calendar_id='NYSE';
-- expect: 2028-12-31 (current_year + TRADING_SESSIONS_EXTENSION_YEARS)
```
Run a second one-cycle pass immediately:
```
mt data daemon run --symbols SPY --daily --stop-when-done
```
Verify the `MAX(session_date)` is unchanged (in-process 24h gate
held; no duplicate INSERTs). Log should not contain "Auto-extended"
for this second run.

### 9. Auto-extension failure path (negative test)
Temporarily insert a malformed holiday row:
```sql
INSERT INTO trading_holidays
  (calendar_id, holiday_date, market_status, early_close_time, late_open_time)
VALUES ('NYSE', '9999-01-01', 'INVALID_STATUS', NULL, NULL);
```
Then re-shrink the horizon (step 6) and run:
```
mt data status
```
Expect: status output prints normally (table or JSON), with a
warning footer "Auto-extend failed for NYSE; run
`mt data --extend --calendar NYSE` manually." Exit 0.

Cleanup:
```sql
DELETE FROM trading_holidays
WHERE calendar_id='NYSE' AND holiday_date='9999-01-01';
```

### 10. Empty registry path
On a scratch DB or with `instruments` cleared:
```
mt data status
```
Expect: "No instruments found. Run `mt data instruments rebuild`
to populate the registry." Exit 0.

### 11. Latency check
```
time mt data status --json > /dev/null
```
Expect: <2s at full-universe scope.

```
time mt data status --health OK,GAPS,STALE,FAILED > /dev/null
```
Expect: <5s for unfiltered Rich render at ~114k rows. If much
slower, decide whether to surface a stderr warning or fall back
to JSON-only for `--all` (per Risks §1).

## Resolved Decisions

### CLI surface — single command, flag-driven
`mt data status` is one Typer command, not a sub-app (per arch).
`--symbol`, `--json`, `--health`, `--granularity`, `--all` shape
behavior.

### Filter default — non-OK only
At ~114k row universe scope, defaulting to all rows is
unreadable. `--health GAPS,STALE,FAILED` is the implicit default;
`--all` opts in to OK rows.

### Auto-extend reuses the warn threshold
`TRADING_SESSIONS_HORIZON_WARN_DAYS = 90` is the trigger. Splitting
into a separate auto-extend threshold is unnecessary complexity
right now.

### Auto-extend is opportunistic, not authoritative
`mt data --extend --strict` stays as the explicit operator /
CI command. Auto-extension is convenience.

### Auto-extend scope is plan-specified, not scope creep (F007 response)
Reviewer F007 flagged auto-extension as scope not defined in the parent
architecture. The auto-extension scope is explicitly in the slice plan
entry: "Also adds automated horizon extension for `trading_sessions`: on
status invocation (or a scheduled daemon tick)…" The arch doc predates
this slice; the plan entry is the authoritative working input.

### Daemon hook via `register_idle_hook`, not direct edit (F005 response)
Slice 147 does not directly modify `runner.py`'s internals. Instead it
adds a minimal `register_idle_hook` extension point to the `Runner`
class and registers the `auto_extend` callable from the `daemon_run`
command. The runner does not import `auto_extend.py`. Dependency arrow
stays downstream-to-upstream.

### No sentinel row in `acquisition_state` (F006 response)
The daemon-side 24h gate uses an in-process `_last_extend_at` module
variable, not a row in `acquisition_state`. `acquisition_state` remains
strictly per-symbol run-state as defined by the architecture.

## Effort

3/5 — the rendering layer is straightforward but has many
sub-cases (default summary, symbol detail, JSON, filters, color
coding); auto-extension is mostly reuse of slice 144's helper. The
daemon-side sentinel hook is a small, well-contained addition.
