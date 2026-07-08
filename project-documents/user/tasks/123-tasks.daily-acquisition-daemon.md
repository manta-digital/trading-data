---
docType: tasks
slice: daily-acquisition-daemon
project: trading
lld: user/slices/123-slice.daily-acquisition-daemon.md
dependencies: [100, 121, 122, 900]
projectState: Slice 122 complete and merged to main. DailyAcquisitionOrchestrator, AlphaVantageDailyProvider, and MarketDBDailyWriter are all implemented and tested. AcquisitionStateRepository and run_acquisition_unit from slice 121 are stable.
dateCreated: 20260412
dateUpdated: 20260412
status: complete
---

# Tasks: Daily Acquisition Daemon

## Context Summary

- Working on the `daily-acquisition-daemon` slice (123)
- Wraps slice 122's `DailyAcquisitionOrchestrator` in a long-running daemon process
- Delivers: `DailyAcquisitionDaemon`, `DaemonHeartbeat`, `mt data daily daemon` CLI command, `mt data daily status` CLI command, heartbeat DB migration, and removal of `marketservice.py`
- Key daemon behaviors: continuous cycling, caught-up detection and sleep, graceful SIGTERM/SIGINT shutdown, exponential backoff for failed symbols
- Dependencies: slice 121 (state/orchestrator core), slice 122 (daily orchestrator), slice 100 (MarketDB), slice 900 (CLI/settings)
- Next slice: 125 (minute acquisition daemon — replicates this pattern)

## Task Order Rationale

Types and protocol first (shared by all components), then the heartbeat mechanism (independent, has own tests), then work queue logic (pure functions, easily testable), then the daemon loop (composes the above), then CLI commands, then integration test, then `marketservice.py` removal (last because it touches existing code). Each implementation task is immediately followed by its test task.

---

## 1. Module Skeleton and Shared Types

- [x] **1.1** Create `src/manta_trading/data/acquisition/daemon/__init__.py`
  - Export nothing yet; establish the package
  - Effort: 1

- [x] **1.2** Create `src/manta_trading/data/acquisition/daemon/types.py`
  - Define `SymbolSource` Protocol: `def get_symbols(self) -> list[str]`
  - Define `DaemonConfig` dataclass: `poll_interval: int`, `max_retries: int`, `daemon_id: str`
  - Define module-level constant `DAILY_DAEMON_ID: str = "daily-acquisition"` (single source; referenced by daemon and CLI status command)
  - Define `HEARTBEAT_ALIVE_THRESHOLD_SECONDS: int = 300` (5 min; CLI uses this to determine alive/dead)
  - No I/O, no imports from slice 121/122 — keep this module leaf-level
  - Effort: 2

- [x] **1.3** Create `src/manta_trading/data/acquisition/daemon/heartbeat.py`
  - Define `DaemonStatus` StrEnum: `STARTING`, `WORKING`, `IDLE`, `CYCLE_COMPLETE`, `STOPPED`
  - Define `DaemonHeartbeat` dataclass: `daemon_id`, `status: DaemonStatus`, `started_at: datetime`, `last_beat_at: datetime`, `current_symbol: str | None`, `cycle_count: int`, `pid: int | None`, `hostname: str | None`
  - Implement `HeartbeatRepository` class with a psycopg `Connection` or `ConnectionPool` dependency:
    - `async def upsert(self, heartbeat: DaemonHeartbeat) -> None` — upsert into `daemon_heartbeat` on `daemon_id`
    - `async def get(self, daemon_id: str) -> DaemonHeartbeat | None`
    - `def is_alive(self, heartbeat: DaemonHeartbeat | None, *, threshold_seconds: int = HEARTBEAT_ALIVE_THRESHOLD_SECONDS) -> bool` — True iff `heartbeat is not None` and `last_beat_at` is within `threshold_seconds` of `datetime.now(UTC)`
  - All `DaemonStatus` values must reference the enum — no bare strings
  - Effort: 3

- [x] **1.4** **Test** — `test/unit/data/acquisition/daemon/test_heartbeat.py`
  - Create `test/unit/data/acquisition/daemon/__init__.py`
  - Use an in-memory fake or a `FakeHeartbeatRepo` that stores in a dict — no real DB for unit tests
  - `test_upsert_creates_row` — upsert, get back, verify all fields match
  - `test_upsert_updates_existing` — upsert twice with different status; get returns the second; only one row
  - `test_is_alive_within_threshold` — `last_beat_at = now - 10s`, threshold 300 → True
  - `test_is_alive_expired` — `last_beat_at = now - 600s`, threshold 300 → False
  - `test_is_alive_no_row` — `heartbeat=None` → False
  - Confirm `DaemonStatus` enum covers all values used; no bare string comparisons in the implementation
  - Effort: 2

---

## 2. Database Migration

- [x] **2.1** Create `migrations/NNN_create_daemon_heartbeat.sql`
  - Find the next available migration number by listing `migrations/` directory
  - DDL matches the schema in the slice design exactly:
    - `daemon_id TEXT PRIMARY KEY`
    - `status TEXT NOT NULL`
    - `started_at TIMESTAMPTZ NOT NULL`
    - `last_beat_at TIMESTAMPTZ NOT NULL`
    - `current_symbol TEXT`
    - `cycle_count INTEGER NOT NULL DEFAULT 0`
    - `pid INTEGER`
    - `hostname TEXT`
  - Wrap in `CREATE TABLE IF NOT EXISTS daemon_heartbeat (...)`
  - Effort: 1

- [x] **2.2** **Test** — verify migration applies cleanly
  - Apply via `psql $MT_TIMESCALE_DB_URL -f migrations/NNN_create_daemon_heartbeat.sql`
  - Confirm table exists: `\d daemon_heartbeat`
  - Apply again (idempotence): confirm no error
  - Effort: 1

---

## 3. Work Queue Builder

- [x] **3.1** Create `src/manta_trading/data/acquisition/daemon/work_queue.py`
  - Implement `build_work_queue(symbol_source: SymbolSource, state_rows: dict[str, AcquisitionStateRow], *, max_retries: int, now: datetime | None = None) -> list[str]`
    - `now` defaults to `datetime.now(UTC)` (injectable for tests)
    - For each symbol from `symbol_source.get_symbols()`:
      - No state row → include
      - State row with `status=OK` and `_is_fresh(last_success_ts)` (from `freshness.py`) → exclude
      - State row with `status=FAILED` and `retry_count >= max_retries` → exclude
      - State row with `status=FAILED` and backoff not yet elapsed → exclude (see `_should_retry` logic in slice design)
      - State row with `status=FAILED` and backoff elapsed → include
      - Any other status → include
    - Returns symbols in the order they appear from `symbol_source`
  - Implement `_should_retry(row: AcquisitionStateRow, max_retries: int, *, now: datetime) -> bool` — pure function, matches the formula in the slice design: `backoff_minutes = min(2 ** row.retry_count, 60)`; `earliest_retry = row.last_attempt_ts + timedelta(minutes=backoff_minutes)`
  - All status comparisons use `AcquisitionStatus` enum values
  - Effort: 3

- [x] **3.2** **Test** — `test/unit/data/acquisition/daemon/test_work_queue.py`
  - `test_fresh_ok_symbol_excluded` — parametrized: gap 0, 1 days → excluded; gap 2, 3 days → included
  - `test_no_state_row_included` — symbol in source list but absent from state dict → included
  - `test_failed_within_backoff_excluded` — FAILED 30s ago with `retry_count=1` (backoff=2m) → excluded
  - `test_failed_past_backoff_included` — FAILED 5m ago with `retry_count=1` (backoff=2m) → included
  - `test_retry_count_at_max_excluded` — `retry_count >= max_retries` → excluded regardless of time elapsed
  - `test_all_symbols_fresh_empty_queue` — all symbols fresh → returns empty list
  - `test_ordering_preserved` — symbols returned in source order (not sorted or shuffled)
  - Inject `now` in all tests that check time-dependent logic
  - Effort: 2

---

## 4. Daily Acquisition Daemon

- [x] **4.1** Create `src/manta_trading/data/acquisition/daemon/daily.py`
  - Implement `DailyAcquisitionDaemon` class
  - Constructor: `__init__(self, orchestrator: DailyAcquisitionOrchestrator, state_repo: AcquisitionStateRepository, heartbeat_repo: HeartbeatRepository, symbol_source: SymbolSource, event_sink: EventSink, *, config: DaemonConfig)`
  - Attributes: `_shutdown_requested: bool = False`, `_shutdown_event: asyncio.Event`
  - `_request_shutdown(self) -> None` — sets `_shutdown_requested = True` and `_shutdown_event.set()`
  - Do not open any connections in the constructor
  - Effort: 2

- [x] **4.2** Implement `async def run(self) -> None`
  - Register SIGTERM and SIGINT handlers via `loop.add_signal_handler(sig, self._request_shutdown)`
  - Write heartbeat `status=STARTING` with `started_at=now`, `pid=os.getpid()`, `hostname=socket.gethostname()`
  - Enter main loop: `while not self._shutdown_requested`
    - Read all state rows via `state_repo.list(granularity=Granularity.DAILY)`; build dict keyed by symbol
    - Call `build_work_queue(self._symbol_source, state_rows, max_retries=config.max_retries)`
    - If queue is empty: write heartbeat `status=IDLE, cycle_count++`; log caught-up message; call `_interruptible_sleep(config.poll_interval)`; continue
    - For each symbol in queue: check `_shutdown_requested` first; write heartbeat `status=WORKING, current_symbol=symbol`; call `await orchestrator.update_symbol(symbol, run_id=uuid4())`; log result
    - Write heartbeat `status=CYCLE_COMPLETE, cycle_count++`
  - On exit: write heartbeat `status=STOPPED`; close resources (provider aclose, pool close, event sink close if applicable); return
  - All exceptions from `update_symbol` are caught, logged, and counted — never propagate to crash the loop
  - Effort: 4

- [x] **4.3** Implement `async def _interruptible_sleep(self, seconds: int) -> None`
  - `asyncio.wait_for(self._shutdown_event.wait(), timeout=seconds)` — returns immediately if shutdown event is set
  - Catches `asyncio.TimeoutError` (normal expiry) silently
  - Does **not** reset the event (shutdown stays set if triggered during sleep)
  - Effort: 1

- [x] **4.4** **Test** — `test/unit/data/acquisition/daemon/test_daily_daemon.py`
  - Use fakes: `FakeOrchestrator` (records `update_symbol` calls, returns configurable `AcquisitionResult`), `InMemoryStateRepo` (in-memory dict, re-use or adapt from slice 121 tests), `FakeHeartbeatRepo`, `FakeSymbolSource`, `NullEventSink`
  - `test_single_cycle_processes_stale_symbols` — seed 3 symbols: 1 fresh/OK, 1 stale/OK, 1 no-state. Run one cycle. Assert orchestrator called exactly for stale + no-state symbols; fresh symbol skipped; heartbeat ends at `CYCLE_COMPLETE`
  - `test_caught_up_triggers_sleep` — all symbols fresh. Run one cycle. Assert no orchestrator calls; heartbeat shows `IDLE`; sleep was entered (mock the sleep to avoid actual delay)
  - `test_shutdown_signal_interrupts_loop` — start daemon; send shutdown after first symbol completes (set `_shutdown_requested` and `_shutdown_event` between symbols via a side-effect in `FakeOrchestrator`). Assert second symbol not fetched; final heartbeat `STOPPED`
  - `test_shutdown_signal_interrupts_sleep` — daemon in caught-up state; trigger `_request_shutdown()` after sleep begins. Assert daemon exits promptly (sleep resolves via event, not timeout)
  - `test_failed_symbol_backoff_respected` — FAILED symbol with `retry_count=2`, `last_attempt_ts=1m ago` (backoff=4m). Assert symbol excluded from queue. Advance `now` past 4m. Assert symbol included.
  - `test_max_retries_excludes_symbol` — `retry_count=max_retries`. Assert excluded from queue in all cycles
  - `test_new_symbol_gets_fetched` — symbol in source but absent from state → appears in queue and is fetched
  - `test_cycle_count_increments` — run two cycles with work each time; assert heartbeat `cycle_count` goes 0 → 1 → 2
  - `test_orchestrator_exception_does_not_crash_loop` — `FakeOrchestrator` raises on second symbol; assert third symbol is still fetched; daemon completes normally
  - Effort: 4

---

## 5. CLI: `mt data daily daemon` Command

- [x] **5.1** Read `src/manta_trading/cli/commands/data.py` to confirm the existing `daily_*` command structure, `_create_daily_orchestrator` helper, and resource cleanup patterns
  - Orientation only — no edits
  - Effort: 1

- [x] **5.2** Add `_create_daily_daemon(ctx, config: DaemonConfig) -> tuple[DailyAcquisitionDaemon, ...]`
  - Reuse `_create_daily_orchestrator` (from slice 122) for the orchestrator construction
  - Wrap `MarketDB.readLRUSymbolList(batchSize=10000, age=0)` in a `MarketDBSymbolSource` (local adapter implementing `SymbolSource` protocol)
  - Construct `HeartbeatRepository` with the timescale connection pool
  - Construct `DailyAcquisitionDaemon(orchestrator, state_repo, heartbeat_repo, symbol_source, event_sink, config=config)`
  - Return daemon plus all handles to close (pool, db, provider, event_sink)
  - Effort: 2

- [x] **5.3** Add `daily_daemon` CLI command
  - `@daily_app.command("daemon")`
  - Options: `--poll-interval INT` (default 3600), `--max-retries INT` (default 5)
  - Build `DaemonConfig(poll_interval, max_retries, daemon_id=DAILY_DAEMON_ID)`
  - Call `_create_daily_daemon(ctx, config)` then `asyncio.run(daemon.run())`
  - Wrap in try/finally to close all returned handles
  - Exit 0 on clean return; non-zero on unhandled exception
  - Log to structured logger (stdout/stderr)
  - Effort: 2

- [x] **5.4** **Test** — manual smoke test for `mt data daily daemon`
  - Start with `--poll-interval 10` in one terminal
  - Confirm logs appear (STARTING, WORKING per symbol, CYCLE_COMPLETE)
  - Let it reach IDLE (caught-up), confirm "caught up, sleeping" log line
  - Send Ctrl-C; confirm STOPPED log and exit code 0
  - Effort: 1

---

## 6. CLI: `mt data daily status` Command

- [x] **6.1** Implement `mt data daily status` command in `data.py`
  - `@daily_app.command("status")`
  - Options: `--json` (output JSON), `--verbose` (per-symbol table)
  - Read `acquisition_state` rows where `granularity=DAILY` via `state_repo.list(granularity=Granularity.DAILY)`
  - Read `daemon_heartbeat` row via `heartbeat_repo.get(DAILY_DAEMON_ID)`
  - Determine alive: `heartbeat_repo.is_alive(heartbeat_row)`
  - Compute counts: total symbols, fresh count (`_is_fresh(row.last_success_ts)`), stale count, failed count
  - Find top-3 stalest symbols (largest gap to today; exclude `FAILED` from stalest list)
  - Render summary table via Rich (match existing CLI output style):
    ```
    Daily Acquisition Status
    ────────────────────────
    Daemon:    alive (last heartbeat: 12s ago, cycle 47, working on MSFT)
    Symbols:   487 total │ 482 fresh │ 3 stale │ 2 failed
    Stalest:   GOOG (3 days), IBM (2 days), WMT (2 days)
    Failed:    SYM1 (error text, retries: 5), SYM2 (error text, retries: 3)
    ```
  - `--verbose`: add per-symbol table with columns: symbol, status, last_success, gap_days, retry_count, error
  - `--json`: output structured dict with same fields
  - Effort: 3

- [x] **6.2** **Test** — manual smoke test for `mt data daily status`
  - Run `mt data daily status` with daemon not running → shows "not running"
  - Start daemon; run `mt data daily status` in second terminal → shows "alive", current symbol
  - Run `mt data daily status --verbose` → per-symbol table shown
  - Run `mt data daily status --json` → valid JSON output
  - Effort: 1

---

## 7. `marketservice.py` Removal

- [x] **7.1** Read `src/manta_trading/market/marketservice.py` and `src/manta_trading/cli/commands/data.py` to confirm the only remaining caller is `daily_symbols` and identify the exact method used (`MarketService.updateSymbolList`)
  - Grep for all other `marketservice` and `MarketService` imports in the codebase — confirm no other callers
  - Orientation only — no edits yet
  - Effort: 1

- [x] **7.2** Find the underlying AlphaVantage method that `MarketService.updateSymbolList` wraps
  - Read `marketservice.py` `updateSymbolList` implementation; identify the `AlphavantageAPI` or direct call it delegates to
  - This is the method `daily_symbols` will call directly after the migration
  - Effort: 1

- [x] **7.3** Migrate `daily_symbols` command to call the underlying API method directly
  - Remove `_create_market_service` helper (or narrow to no callers)
  - Import and call the underlying method identified in 7.2 directly
  - Preserve the `daily_symbols` command signature and output exactly — no behavior changes
  - Effort: 2

- [x] **7.4** **Test** — confirm `daily_symbols` still works after migration
  - `mt data daily symbols` runs without error
  - Symbol list output is identical in format to before
  - Effort: 1

- [x] **7.5** Delete `src/manta_trading/market/marketservice.py`
  - Confirm all tests pass after deletion
  - Effort: 1

- [x] **7.6** **Test** — run full unit suite to confirm no regressions from removal
  - `pytest test/unit/ -v` — all tests pass; no import errors
  - Effort: 1

---

## 8. Integration Test: Daemon Multi-Cycle

- [x] **8.1** Create `test/integration/data/acquisition/daemon/__init__.py`
  - Effort: 1

- [x] **8.2** Implement `test/integration/data/acquisition/daemon/test_daily_daemon_integration.py`
  - Skip unless `MT_TIMESCALE_DB_URL` and `MT_MARKET_DB_URL` are set (use the existing skip pattern from slice 122 integration tests)
  - Use a stub `IDailyDataProvider` — no real AlphaVantage; CI must not require the real API
  - Fixture: apply heartbeat migration (idempotent), seed 3 test symbols in `symbol_list`, clean `acquisition_state` and `daemon_heartbeat` rows for test daemon_id after each test
  - `test_daemon_runs_cycles_and_shuts_down`:
    - Construct `DailyAcquisitionDaemon` with stub provider (returns canned data for each symbol), `poll_interval=1`, `max_retries=5`
    - Run the daemon in a background task via `asyncio.create_task(daemon.run())`
    - Let it process until all 3 symbols have `status=OK` in acquisition_state (poll state repo)
    - Call `daemon._request_shutdown()` to trigger graceful stop
    - Await the task with a timeout (e.g. 30s)
    - Assert: all 3 symbols have `status=OK` in acquisition_state
    - Assert: heartbeat row has `status=STOPPED`
    - Assert: `cycle_count >= 1`
    - Assert: event log (JSONL sink) has `run_started`/`chunk_ok`/`run_finished` entries for each symbol
  - Effort: 4

- [x] **8.3** **Test** — run integration test locally with real databases
  - `pytest test/integration/data/acquisition/daemon/ -v` — green
  - Effort: 1

---

## 9. End-to-End Verification

- [x] **9.1** Run the full slice verification walkthrough from the slice design (steps 1–17)
  - Start daemon, observe logs, check status, test graceful shutdown, restart-and-resume, verbose status, marketservice removal, regression commands
  - Note any deviations or surprises for the slice design "Verification Walkthrough" section
  - Effort: 2

- [x] **9.2** Run the full unit test suite
  - `pytest test/unit/ -v` — all tests pass; new daemon tests included
  - Slice 121/122 tests still green
  - Effort: 1

- [x] **9.3** Run the full integration test suite
  - `pytest test/integration/ -v` — new daemon integration test green; existing tests pass
  - Effort: 1

---

## 10. Wrap-up

- [x] **10.1** Verify file size budgets
  - Each new module ≤ ~300 lines, daemon main loop (`daily.py`) ≤ ~200 lines
  - If `daily.py` exceeds 200 lines, split `_interruptible_sleep` or heartbeat update calls into a helper module
  - Effort: 1

- [x] **10.2** Lint and type check
  - `ruff check` and `mypy` on all new daemon files — zero errors
  - Effort: 1

- [x] **10.3** Confirm no magic strings
  - `DaemonStatus` enum for all status values; `DAILY_DAEMON_ID` constant for daemon identity; `HEARTBEAT_ALIVE_THRESHOLD_SECONDS` for threshold; `Granularity.DAILY` and `AcquisitionStatus` enums throughout
  - No bare `"daily-acquisition"`, `"WORKING"`, `"STOPPED"`, etc. in code
  - Effort: 1

- [x] **10.4** Self-review against slice design success criteria — every bullet in the "Success Criteria" section checked
  - Effort: 1

- [x] **10.5** Update slice design `123-slice.daily-acquisition-daemon.md` status to `complete` and `dateUpdated` to today
  - Effort: 1

- [x] **10.6** Commit on slice branch: `feat: add daily acquisition daemon and status CLI`
  - Stage all new and modified files from project root
  - Effort: 1

---

## Notes for the Implementer

- **`DailyAcquisitionOrchestrator` is the only path into per-symbol work.** The daemon never calls `run_acquisition_unit` directly.
- **Shutdown latency is bounded.** `update_symbol` is one HTTP request + DB write. Signal receipt sets the flag; the current call completes naturally before the loop checks.
- **`_interruptible_sleep` must not use `asyncio.sleep`.** It uses `asyncio.wait_for(event.wait(), timeout=N)` so a shutdown signal wakes it immediately.
- **`HeartbeatRepository` upserts, never inserts.** One row per `daemon_id` — the daemon is either running or not; no history in this table (the event JSONL is the history).
- **`marketservice.py` removal is safe because slice 122 already migrated all OHLCV callers.** Verify with grep before deleting.
- **Integration tests use a stub provider.** The real AlphaVantage API must never be called from CI or automated tests.
- **`DAILY_DAEMON_ID` must be a constant, not a literal.** Both the daemon (writes heartbeat) and the status CLI (reads heartbeat) must reference the same constant.
