---
docType: slice-design
slice: daily-acquisition-daemon
project: trading
parent: user/architecture/120-slices.data-acquisition.md
dependencies: [100, 121, 122, 900]
interfaces: [125]
dateCreated: 20260412
dateUpdated: 20260412
status: complete
---

# Slice Design: Daily Acquisition Daemon

## Overview

Wraps the slice 122 `DailyAcquisitionOrchestrator` in a long-running daemon process that runs unattended on .144, continuously cycling through the equity symbol universe and keeping daily OHLCV data current. This is the first daemon in the system and proves the pattern on the simplest case (low request volume, one-request-per-symbol gap model) before slice 125 applies it to minute data.

The daemon:

1. Cycles through all active symbols, calling `DailyAcquisitionOrchestrator.update_symbol()` for each.
2. Detects "caught up" (every active symbol within 2 trading days of today) and sleeps with a configurable poll interval.
3. Shuts down gracefully on SIGTERM/SIGINT — finishes the current symbol, persists state, exits.
4. Writes a heartbeat row so the CLI can report daemon health without IPC.

A new `mt data daily status` CLI command reads `acquisition_state` and the heartbeat to report daemon health, current work queue, and per-symbol freshness.

**Out of scope:** minute acquisition (slice 125), daemon framework extraction (deferred per architecture doc until a third daemon exists), shared rate limit coordination between daily and minute daemons (slice 125 concern — daily catches up fast enough that simple sequencing works), retry backoff tuning beyond what the architecture specifies.

## Value

**Operator-facing:** Daily data stays current without human intervention. The operator starts the daemon, walks away, and checks `mt data daily status` periodically. Restarts (planned or crash) resume from where the daemon left off — no re-fetching of already-current symbols.

**Developer-facing:** Proves the daemon pattern (main loop, signal handling, caught-up detection, heartbeat) in a low-complexity context. Slice 125 reuses these patterns for the minute daemon. Patterns live in the daily daemon module initially; extraction into shared code happens when the third daemon (tick) is added.

**Architectural:** Completes the daily acquisition vertical: provider (122) → orchestrator (122) → daemon (this slice) → status CLI (this slice). After this slice, the daily pipeline requires zero human intervention for steady-state operation.

## Technical Scope

### In Scope

- New `DailyAcquisitionDaemon` class: async main loop, signal handling, caught-up detection, sleep-and-poll, heartbeat writes.
- New `DaemonHeartbeat` mechanism: a row in a `daemon_heartbeat` table (or reuse of a status file) that records daemon identity, last-active timestamp, current symbol, and cycle count. The CLI reads this to determine if the daemon is alive.
- New `mt data daily daemon` CLI command to run the daemon in the foreground (the process; systemd or supervisord manages lifecycle externally).
- New `mt data daily status` CLI command that reads `acquisition_state` rows for `granularity=daily` plus the heartbeat, and renders a summary: daemon alive/dead, last heartbeat, symbols total/fresh/stale/failed, most-stale symbols.
- Retry policy for failed symbols: exponential backoff using the `retry_count` already in `acquisition_state`. A symbol with `retry_count=N` is skipped for `min(2^N, 60)` minutes after `last_attempt_ts`. After `max_retries` (configurable, default 5), the symbol is left in `FAILED` status and excluded from the work queue until manually reset or the next full cycle.
- Database migration for `daemon_heartbeat` table.
- Unit tests for the daemon loop (using fakes for orchestrator, state repo, heartbeat).
- Integration test: daemon runs multiple cycles against a test DB with a stub provider, handles simulated failures, shuts down on signal.
- Removal of `marketservice.py` (slice 122 stopped the CLI from using it; this slice removes the file and migrates `daily_symbols` to use the provider registry or AlphaVantage API directly).

### Out of Scope

- Minute acquisition daemon (slice 125).
- Shared daemon framework or base class extraction (deferred until tick daemon).
- HTTP health endpoint or Unix socket IPC — the heartbeat table is sufficient for CLI queries.
- Concurrent symbol fetching within the daily daemon — sequential is fine at 30 req/min with one request per symbol (~17 min for 500 symbols).
- Daemonization (double-fork, PID files) — the process runs in the foreground; the host's service manager handles backgrounding.
- Trading calendar integration for "caught up" detection — using a simple calendar-day gap (MIN_DAYS=2) is sufficient for daily data. Calendar-aware gap analysis is Initiative 140's concern.
- Rate limit coordination with the minute daemon — daily catches up in minutes/hours; minute catch-up takes days. Simple operational sequencing (start daily first, add minute after daily is current) suffices. Slice 125 addresses this if needed.

## Architecture

### Component Structure

```
DailyAcquisitionDaemon
  ├── DailyAcquisitionOrchestrator (slice 122, injected)
  │     ├── AlphaVantageDailyProvider
  │     ├── MarketDBDailyWriter
  │     └── run_acquisition_unit (slice 121)
  ├── AcquisitionStateRepository (slice 121, injected)
  ├── DaemonHeartbeat (new, injected)
  ├── SymbolSource (new protocol — wraps symbol list retrieval)
  └── EventSink (slice 121, injected)
```

The daemon owns the loop; the orchestrator owns the per-symbol work. The daemon never calls `run_acquisition_unit` directly — it goes through the orchestrator's `update_symbol()`, preserving the single code path principle from the architecture doc.

### Data Flow

```
DailyAcquisitionDaemon.run()
  │
  ├─ on startup:
  │    register SIGTERM/SIGINT handlers → set shutdown_requested flag
  │    write heartbeat (status=STARTING)
  │
  ├─ main loop (while not shutdown_requested):
  │    │
  │    ├─ build_work_queue():
  │    │    read all acquisition_state rows (granularity=DAILY)
  │    │    read symbol list from MarketDB (LRU list or configured source)
  │    │    for each symbol:
  │    │      if state is OK and _is_fresh(last_success_ts): skip
  │    │      if state is FAILED and retry backoff not elapsed: skip
  │    │      if state is FAILED and retry_count >= max_retries: skip
  │    │      else: add to work queue
  │    │    symbols with no state row → add (new symbols get fetched)
  │    │
  │    ├─ if work_queue is empty:
  │    │    write heartbeat (status=IDLE, cycle_count++)
  │    │    log "caught up, sleeping {poll_interval}s"
  │    │    await interruptible_sleep(poll_interval)
  │    │    continue
  │    │
  │    ├─ for symbol in work_queue:
  │    │    if shutdown_requested: break
  │    │    write heartbeat (status=WORKING, current_symbol=symbol)
  │    │    result = await orchestrator.update_symbol(symbol, run_id=uuid4())
  │    │    log result (success/failure/no-data)
  │    │
  │    └─ write heartbeat (status=CYCLE_COMPLETE, cycle_count++)
  │
  └─ on shutdown:
       write heartbeat (status=STOPPED)
       close resources (provider, pools, event sink)
       exit 0
```

### State Management

**Acquisition state** — owned by slices 121/122, unchanged. The daemon reads it to build the work queue and the orchestrator writes it per-symbol. No new columns needed.

**Daemon heartbeat** — new table, written by the daemon, read by the CLI `status` command:

```sql
CREATE TABLE IF NOT EXISTS daemon_heartbeat (
    daemon_id    TEXT PRIMARY KEY,       -- e.g. "daily-acquisition"
    status       TEXT NOT NULL,          -- STARTING, WORKING, IDLE, CYCLE_COMPLETE, STOPPED
    started_at   TIMESTAMPTZ NOT NULL,
    last_beat_at TIMESTAMPTZ NOT NULL,
    current_symbol TEXT,                 -- NULL when idle
    cycle_count  INTEGER NOT NULL DEFAULT 0,
    pid          INTEGER,               -- OS pid for debugging
    hostname     TEXT                    -- for multi-host identification
);
```

The daemon upserts this row on every status transition. The CLI considers the daemon "alive" if `last_beat_at` is within a configurable threshold (default: 5 minutes). This is simple, requires no IPC, and leverages the existing TimescaleDB connection.

**Heartbeat status values** — defined as a `DaemonStatus` StrEnum: `STARTING`, `WORKING`, `IDLE`, `CYCLE_COMPLETE`, `STOPPED`.

## Technical Decisions

### Daemon Lifecycle: Foreground Process

The daemon runs as a foreground async process (`asyncio.run(daemon.run())`). No double-fork, no PID file management, no custom daemonization. The host's service manager (systemd on .144) handles backgrounding, restart-on-failure, and log capture. This matches standard modern practice and avoids reinventing process management.

The `mt data daily daemon` command is the entry point:

```python
@daily_app.command("daemon")
def daily_daemon(
    ctx: typer.Context,
    poll_interval: int = typer.Option(3600, help="Seconds to sleep when caught up"),
    max_retries: int = typer.Option(5, help="Max retries before skipping a failed symbol"),
) -> None:
    """Run the daily acquisition daemon (foreground, long-running)."""
```

### Graceful Shutdown via Signal Handlers

```python
async def run(self) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, self._request_shutdown)
    # ... main loop checks self._shutdown_requested before each symbol
```

On signal receipt, the daemon sets `_shutdown_requested = True`. The main loop checks this flag before starting each symbol. The current in-flight `update_symbol()` call completes naturally (it is a single HTTP request + DB write — fast). This means shutdown latency is bounded by one provider request (~2 seconds worst case).

### Interruptible Sleep

When caught up, the daemon sleeps for `poll_interval` seconds. This sleep must be interruptible by shutdown signals. Implementation: `asyncio.wait_for(self._shutdown_event.wait(), timeout=poll_interval)`. The signal handler sets the event, waking the sleep immediately.

### Retry Backoff for Failed Symbols

The `retry_count` and `last_attempt_ts` fields in `acquisition_state` already exist (slice 121). The daemon uses them to implement exponential backoff:

```python
def _should_retry(self, row: AcquisitionStateRow) -> bool:
    if row.status != AcquisitionStatus.FAILED:
        return False
    if row.retry_count >= self._max_retries:
        return False
    backoff_minutes = min(2 ** row.retry_count, 60)
    earliest_retry = row.last_attempt_ts + timedelta(minutes=backoff_minutes)
    return datetime.now(UTC) >= earliest_retry
```

Backoff schedule: 1m, 2m, 4m, 8m, 16m, 32m, 60m (capped). After `max_retries` (default 5), the symbol stays `FAILED` until manually reset via `mt data state` or the next daemon restart resets the cycle. The daemon does not auto-reset — persistent failures (delisted symbol, API key issue) should not retry indefinitely.

### Symbol Source

The daemon needs a list of "all symbols that should have daily data." Today, `readLRUSymbolList` provides this from MarketDB. The daemon uses the same source, wrapped in a simple protocol so testing doesn't require a real database:

```python
class SymbolSource(Protocol):
    def get_symbols(self) -> list[str]: ...
```

The production implementation calls `MarketDB.readLRUSymbolList(batchSize=10000, age=0)` and extracts symbol strings. The test implementation returns a fixed list.

### `marketservice.py` Removal

Slice 122 stopped the daily update CLI commands from using `MarketService`. The remaining caller is `daily_symbols` (which calls `MarketService.updateSymbolList`). This slice migrates `daily_symbols` to call `AlphavantageAPI.getSymbolList()` directly (the underlying method `MarketService` wraps) and deletes `marketservice.py`. This is cleanup — the file is dead code after this migration.

## CLI Commands

### `mt data daily daemon`

```
Usage: mt data daily daemon [OPTIONS]

  Run the daily acquisition daemon (foreground, long-running).

Options:
  --poll-interval  INTEGER  Seconds to sleep when caught up [default: 3600]
  --max-retries    INTEGER  Max consecutive failures before skipping symbol [default: 5]
  --help                    Show this message and exit.
```

Behavior:
- Constructs `DailyAcquisitionDaemon` with injected orchestrator, state repo, heartbeat, symbol source, event sink.
- Calls `asyncio.run(daemon.run())`.
- Logs to structured logger (stdout/stderr, captured by systemd).
- Exits 0 on graceful shutdown, non-zero on unhandled exception.

### `mt data daily status`

```
Usage: mt data daily status [OPTIONS]

  Report daily acquisition daemon health and per-symbol freshness.

Options:
  --json     Output as JSON instead of table
  --verbose  Show per-symbol detail (default: summary only)
  --help     Show this message and exit.
```

Default output (summary):

```
Daily Acquisition Status
────────────────────────
Daemon:    alive (last heartbeat: 12s ago, cycle 47, working on MSFT)
Symbols:   487 total │ 482 fresh │ 3 stale │ 2 failed
Stalest:   GOOG (3 days), IBM (2 days), WMT (2 days)
Failed:    DELISTED1 (Invalid API call, retries: 5), DELISTED2 (No data, retries: 3)
```

With `--verbose`: adds a table of all symbols with columns: symbol, status, last_success, gap_days, retry_count, error.

Implementation: reads `acquisition_state` rows where `granularity=DAILY` via `state_repo.list()`, reads `daemon_heartbeat` row where `daemon_id="daily-acquisition"`, renders with Rich tables (existing pattern from slice 122 CLI output).

## Cross-Slice Dependencies and Interfaces

**Depends on:**

- Slice 121 — `AcquisitionStateRepository`, `AcquisitionStatus`, `Granularity`, `EventSink`, `JsonlEventSink`, `NullEventSink`, `AcquisitionEvent`, `AcquisitionEventType`.
- Slice 122 — `DailyAcquisitionOrchestrator`, `AlphaVantageDailyProvider`, `_is_fresh`, `MIN_DAYS`, `BatchResult`, `AcquisitionResult`. The daemon calls `orchestrator.update_symbol()` — this is the primary integration point.
- Slice 100 — `MarketDB` (for symbol list retrieval via `readLRUSymbolList`).
- Slice 900 — `Settings`, `ProviderType`, CLI framework (Typer), structured logging, Rich output.

**Provides for:**

- Slice 125 (minute daemon) — proves the daemon pattern. Slice 125 will replicate the loop structure, signal handling, heartbeat, and status CLI for minute data. No shared code is exported yet — 125 copies and adapts.
- Future daemon framework extraction — the patterns in this slice's `DailyAcquisitionDaemon` (main loop, `_request_shutdown`, interruptible sleep, heartbeat writes, work queue building) become extraction candidates when the tick daemon is added.

**Interface stability:** The daemon has no public API beyond the CLI. Internal classes (`DailyAcquisitionDaemon`, `DaemonHeartbeat`) may be refactored freely when framework extraction happens.

## File Layout

New files:

- `src/manta_trading/data/acquisition/daemon/__init__.py`
- `src/manta_trading/data/acquisition/daemon/daily.py` — `DailyAcquisitionDaemon` class
- `src/manta_trading/data/acquisition/daemon/heartbeat.py` — `DaemonHeartbeat`, `DaemonStatus` enum, heartbeat table operations
- `src/manta_trading/data/acquisition/daemon/types.py` — `SymbolSource` protocol, `DaemonConfig` dataclass (poll_interval, max_retries, daemon_id constants)
- `migrations/NNN_create_daemon_heartbeat.sql` — heartbeat table DDL
- `test/unit/data/acquisition/daemon/__init__.py`
- `test/unit/data/acquisition/daemon/test_daily_daemon.py` — daemon loop unit tests
- `test/unit/data/acquisition/daemon/test_heartbeat.py` — heartbeat read/write tests
- `test/unit/data/acquisition/daemon/test_work_queue.py` — work queue building and retry logic tests
- `test/integration/data/acquisition/daemon/test_daily_daemon_integration.py` — multi-cycle integration test

Modified files:

- `src/manta_trading/cli/commands/data.py` — add `daily_daemon` and `daily_status` commands, migrate `daily_symbols` away from `MarketService`.
- `src/manta_trading/data/acquisition/state.py` — no schema changes; may add a convenience method like `list_stale(granularity, min_days)` if it simplifies the work queue builder, but this is an implementation-time decision.

Removed files:

- `src/manta_trading/market/marketservice.py` — fully replaced. `daily_symbols` is migrated; all other callers were removed in slice 122.

## Testing Strategy

### Unit: `test_daily_daemon.py`

Uses fakes for all dependencies: `FakeOrchestrator` (records calls, returns configurable results), `InMemoryStateRepository` (from slice 121 tests), `FakeHeartbeat`, `FakeSymbolSource`, `NullEventSink`.

- `test_single_cycle_processes_stale_symbols` — seed state with 3 symbols: 1 fresh, 1 stale, 1 no-state-row. Run one cycle. Assert orchestrator called for stale + no-state symbols only, heartbeat updated.
- `test_caught_up_triggers_sleep` — seed all symbols as fresh. Run one cycle. Assert no orchestrator calls, heartbeat status is IDLE, sleep was entered.
- `test_shutdown_signal_interrupts_loop` — start daemon, send shutdown after first symbol completes. Assert second symbol was not fetched, heartbeat status is STOPPED.
- `test_shutdown_signal_interrupts_sleep` — start daemon in caught-up state (sleeping). Send shutdown. Assert daemon exits promptly (not after full poll_interval).
- `test_failed_symbol_backoff` — seed state with a FAILED symbol, `retry_count=2`, `last_attempt_ts=1 minute ago`. Assert symbol is skipped (backoff is 4 minutes). Advance time past backoff. Assert symbol is now in work queue.
- `test_max_retries_excludes_symbol` — seed state with `retry_count=max_retries`. Assert symbol excluded from work queue entirely.
- `test_new_symbol_gets_fetched` — symbol in symbol source but not in acquisition_state. Assert it appears in work queue.
- `test_cycle_count_increments` — run two cycles. Assert heartbeat cycle_count goes from 0 to 2.

### Unit: `test_heartbeat.py`

Uses a real (test) database connection or an in-memory fake.

- `test_upsert_creates_row` — write heartbeat, read back, verify fields.
- `test_upsert_updates_existing` — write twice with different status, verify row updated (not duplicated).
- `test_is_alive_within_threshold` — heartbeat 10s ago, threshold 300s → alive.
- `test_is_alive_expired` — heartbeat 600s ago, threshold 300s → not alive.
- `test_is_alive_no_row` — no heartbeat row → not alive.

### Unit: `test_work_queue.py`

- `test_fresh_symbols_excluded` — parametrized: gap=0,1 → excluded; gap=2,3 → included.
- `test_failed_within_backoff_excluded` — symbol FAILED 30s ago with retry_count=1 (backoff=2m) → excluded.
- `test_failed_past_backoff_included` — symbol FAILED 5m ago with retry_count=1 (backoff=2m) → included.
- `test_symbols_not_in_state_included` — symbol in source list but absent from acquisition_state → included.
- `test_retry_count_at_max_excluded` — retry_count >= max_retries → excluded regardless of time.

### Integration: `test_daily_daemon_integration.py`

Skipped unless `MT_TIMESCALE_DB_URL` and `MT_MARKET_DB_URL` are set.

- `test_daemon_runs_three_cycles_and_shuts_down` — start daemon with a stub provider (returns canned data), 3 symbols, `poll_interval=1`. Let it run until all symbols are fresh (cycle 1 fetches all, cycle 2 detects caught-up and sleeps). Send SIGTERM via `os.kill`. Assert: all 3 symbols have `status=OK` in acquisition_state, heartbeat row has `status=STOPPED`, cycle_count >= 2, event log has run_started/chunk_ok/run_finished entries for each symbol.

### Regression

- All slice 121/122 tests pass unchanged.
- `mt data daily update SYMBOL` still works (not broken by daemon code).
- `mt data daily coverage`, `mt data daily symbols`, `mt data state` still work.
- `mt data daily symbols` works after `marketservice.py` removal.

## Success Criteria

- `DailyAcquisitionDaemon` runs as a foreground process via `mt data daily daemon`, continuously cycling through symbols.
- Daemon detects "caught up" (all active symbols fresh per `_is_fresh` / `MIN_DAYS=2`) and sleeps for `poll_interval` seconds.
- Daemon shuts down gracefully on SIGTERM/SIGINT: finishes current symbol, writes STOPPED heartbeat, exits 0.
- Daemon survives restart with state intact: restarted daemon skips already-fresh symbols and retries failed ones. No re-fetching of completed work.
- Failed symbols are retried with exponential backoff; symbols exceeding `max_retries` are excluded until manual intervention.
- `mt data daily status` accurately reports: daemon alive/dead (from heartbeat), symbol counts (fresh/stale/failed), most-stale symbols, failed symbols with error context.
- Heartbeat table tracks daemon identity, status, last beat timestamp, current symbol, and cycle count.
- `marketservice.py` is deleted; `daily_symbols` CLI command works via direct API call.
- No magic strings: daemon statuses use `DaemonStatus` enum; daemon_id is a named constant; all thresholds are named constants or config parameters.
- Source files are ≤ ~300 lines. The daemon main loop module should be ≤ ~200 lines.
- All slice 121/122 tests pass unchanged. New daemon tests pass.

## Verification Walkthrough

Draft — will be refined during Phase 6 with actual commands, output, and any surprises.

**Setup**

1. `git checkout 123-slice.daily-acquisition-daemon` (or create branch from main).
2. Confirm test databases reachable:
   ```bash
   echo $MT_TIMESCALE_DB_URL
   echo $MT_MARKET_DB_URL
   echo $MT_ALPHAVANTAGE_API_KEY
   ```
3. Apply heartbeat migration:
   ```bash
   psql $MT_TIMESCALE_DB_URL -f database/migrations/780_create_daemon_heartbeat.sql
   psql $MT_TIMESCALE_DB_URL -c '\d daemon_heartbeat'
   ```
   Expected: table exists with `daemon_id` PK.

**Tests (no external calls)**

4. Run the new test suite:
   ```bash
   pytest test/unit/data/acquisition/daemon/ -v
   ```
   Expected: all daemon tests pass.

5. Run the full acquisition suite:
   ```bash
   pytest test/unit/data/acquisition/ -v
   ```
   Expected: slices 121/122 tests still pass, new daemon tests included.

**Daemon — real API**

6. Start the daemon with a short poll interval for testing:
   ```bash
   mt data daily daemon --poll-interval 60
   ```
   Expected: daemon starts, logs "Starting daily acquisition daemon", begins cycling through symbols. Log lines show per-symbol fetch results.

7. In a second terminal, check status:
   ```bash
   mt data daily status
   ```
   Expected: shows daemon as alive, current symbol being processed, symbol counts updating as daemon makes progress.

8. Let the daemon run until it logs "caught up, sleeping 60s" (may take several minutes depending on how many symbols are stale).

9. Check status again:
   ```bash
   mt data daily status
   ```
   Expected: daemon status is IDLE, all symbols show as fresh or failed (with error context for failures).

**Graceful shutdown**

10. Send SIGTERM to the daemon (Ctrl-C in terminal 1, or `kill <pid>`).
    Expected: daemon logs "Shutdown requested, finishing current symbol...", then "Daemon stopped." Exits with code 0.

11. Check status after shutdown:
    ```bash
    mt data daily status
    ```
    Expected: "Daemon: not running (last seen: Xs ago, stopped cleanly)". Symbol freshness data still accurate.

**Restart and resume**

12. Restart the daemon:
    ```bash
    mt data daily daemon --poll-interval 60
    ```
    Expected: daemon starts, immediately detects most symbols are fresh (from the prior run), reaches "caught up" state quickly without re-fetching everything.

**Status command — verbose mode**

13. ```bash
    mt data daily status --verbose
    ```
    Expected: per-symbol table with columns: symbol, status, last_success, gap_days, retry_count, error.

**`marketservice.py` removal verification**

14. Confirm the file is gone:
    ```bash
    ls src/manta_trading/market/marketservice.py
    ```
    Expected: file not found.

15. Confirm `daily_symbols` still works:
    ```bash
    mt data daily symbols
    ```
    Expected: symbol list updates successfully.

**Regression check**

16. Run all existing commands:
    ```bash
    mt data daily update AAPL
    mt data daily coverage
    mt data state
    mt data minute coverage
    ```
    Expected: all succeed. No regressions.

17. Full test suite:
    ```bash
    pytest test/ -v
    ```
    Expected: all tests pass. Test count increases by the new daemon tests.

## Notes

- The daemon process is designed to run on .144 against the test DB initially. Production deployment follows once the operator is satisfied with stability. No environment-specific configuration is baked in — the daemon reads `Settings` from environment variables like all other commands.
- The `poll_interval` default of 3600 seconds (1 hour) is conservative. For the daily use case (markets close once, new data appears once per day), hourly polling is more than sufficient. Operators can tune this down for testing.
- The heartbeat table is intentionally simple (one row per daemon). It is not an event log — that's what `JsonlEventSink` is for. The heartbeat answers one question: "is the daemon running right now?"
- Daemon framework extraction is explicitly deferred. When slice 125 builds the minute daemon, it will replicate the patterns from this slice (loop, signal handling, heartbeat, interruptible sleep, work queue). When a third daemon appears, the shared patterns get extracted. This avoids premature abstraction.
- The `marketservice.py` removal is included in this slice because it's the natural cleanup point — slice 122 stopped using it for updates, and this slice is the last touch before the daily pipeline is fully self-contained. The migration of `daily_symbols` is a small, well-bounded change.
- If the symbol list source needs to change (e.g., from `readLRUSymbolList` to a dedicated symbols table or config file), the `SymbolSource` protocol makes that a one-line swap in the daemon constructor. But the default implementation uses the existing mechanism.
