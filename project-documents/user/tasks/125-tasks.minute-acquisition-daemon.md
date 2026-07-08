---
docType: tasks
slice: minute-acquisition-daemon
project: trading
lld: user/slices/125-slice.minute-acquisition-daemon.md
dependencies: [100, 121, 123, 124, 900]
projectState: Slices 121–124 complete and merged to main. MinuteAcquisitionOrchestrator (124) fetches per-month chunks with checkpointing; AlphaVantageMinuteProvider now raises on API errors; `mt data minute update-all` uses InstrumentRegistry. Slice 123's DailyAcquisitionDaemon, HeartbeatRepository, daemon_heartbeat table, DaemonConfig, SymbolSource protocol, and build_work_queue are in place. No new DB migrations required for slice 125.
dateCreated: 20260414
dateUpdated: 20260414
status: complete
---

# Tasks: Minute Acquisition Daemon

## Context Summary

- Working on the `minute-acquisition-daemon` slice (125)
- Wraps slice 124's `MinuteAcquisitionOrchestrator` in a long-running daemon process
- Delivers: `MinuteAcquisitionDaemon`, `build_minute_work_queue`, `InstrumentRegistrySymbolSource`, `mt data minute daemon` CLI command, `mt data minute status` CLI command, `--requests-per-minute` rate-limit cap
- Reuses slice 123 infra unchanged: `HeartbeatRepository`, `DaemonHeartbeat`, `DaemonStatus`, `daemon_heartbeat` table, `DaemonConfig`, `SymbolSource` protocol, `_should_retry` helper
- Key minute-specific divergences from daily daemon:
  - `UNFILLABLE` status is terminal — must be excluded from the work queue (daily's builder treats it as "retry")
  - Freshness uses `minute/freshness.py:_is_fresh` (MIN_DAYS=3), not daily's
  - Symbol source is `InstrumentRegistry.list_instruments(active_only=True)`, not `MarketDB.readLRUSymbolList`
  - Shutdown latency bounded by one-month fetch + one DB write (~2–4 s) — orchestrator already checkpoints per month
  - `--requests-per-minute` flag caps AV provider below 30 rpm so operator can leave headroom for the daily daemon
- No DB migration: `daemon_heartbeat` table already exists (slice 123)
- Manual rate-limit sequencing: operator starts daily first (wait for IDLE), then starts minute — documented in slice design
- Dependencies: 121 (state/orchestrator core), 123 (daemon infra), 124 (minute orchestrator + provider fixes), 100 (InstrumentRegistry), 900 (CLI/settings)
- Next slices (future): calendar-aware freshness (drop MIN_DAYS to 1 trading day using existing TradingCalendar); priority tiers (S&P 500 daily, Russell 2000 weekly); daemon framework extraction after a third daemon lands

## Task Order Rationale

Shared types extension first (one constant added), then symbol-source adapter (standalone, own tests), then work-queue builder (pure function, own tests), then daemon loop (composes the above), then CLI commands, then integration test, then end-to-end verification. Each implementation task is immediately followed by its test task (test-with pattern).

---

## 1. Shared Types Extension

- [x] **1.1** Extend `src/manta_trading/data/acquisition/daemon/types.py`
  - Add module-level constant `MINUTE_DAEMON_ID: str = "minute-acquisition"` alongside existing `DAILY_DAEMON_ID`
  - Do not modify existing `SymbolSource` protocol, `DaemonConfig` dataclass, `DAILY_DAEMON_ID`, or `HEARTBEAT_ALIVE_THRESHOLD_SECONDS` — they are shared between daemons as-is
  - No I/O, no new imports from slice 121/122/124 — leaf module
  - Effort: 1

- [x] **1.2** **Test** — extend `test/unit/data/acquisition/daemon/test_types.py` (create if absent)
  - `test_minute_daemon_id_distinct_from_daily` — assert `MINUTE_DAEMON_ID != DAILY_DAEMON_ID`
  - `test_minute_daemon_id_value` — assert value is the string `"minute-acquisition"` (this is the contract with the shared heartbeat table)
  - Effort: 1

---

## 2. Symbol Source Adapter

- [x] **2.1** Create `src/manta_trading/data/acquisition/daemon/symbol_sources.py`
  - Implement `InstrumentRegistrySymbolSource` — adapter that implements the existing `SymbolSource` protocol
  - Constructor: `__init__(self, registry: InstrumentRegistry) -> None`
  - Method: `def get_symbols(self) -> list[str]` returning `[i.symbol for i in self._registry.list_instruments(active_only=True)]`
  - The `active_only=True` kwarg is the load-bearing filter — inactive instruments must not appear in the work queue
  - Keep the module leaf-level; no imports from slice 123's daemon internals
  - Effort: 1

- [x] **2.2** **Test** — `test/unit/data/acquisition/daemon/test_symbol_sources.py`
  - Use a `FakeInstrumentRegistry` with a configurable list of `Instrument` objects (match the real dataclass shape; minimum required fields)
  - `test_returns_symbols_from_registry` — fake with 3 active instruments → `get_symbols()` returns those 3 symbol strings in registry order
  - `test_forwards_active_only_true` — spy on the fake; assert `list_instruments` was called with `active_only=True`
  - `test_empty_registry_returns_empty_list` — fake with zero instruments → returns `[]`
  - Effort: 2

---

## 3. Minute Work Queue Builder

- [x] **3.1** Create `src/manta_trading/data/acquisition/daemon/minute_work_queue.py`
  - Implement `build_minute_work_queue(symbol_source: SymbolSource, state_rows: dict[str, AcquisitionStateRow], *, max_retries: int, now: datetime | None = None) -> list[str]`
    - `now` defaults to `datetime.now(UTC)` (injectable for tests)
    - Import `_is_fresh` from `manta_trading.data.acquisition.minute.freshness` — NOT the daily variant
    - Import `_should_retry` from `manta_trading.data.acquisition.daemon.work_queue` (slice 123) — reuse verbatim; the exponential-backoff formula is orchestrator-agnostic
    - Iterate `symbol_source.get_symbols()` preserving order
    - For each symbol, apply rules in order (first match wins):
      1. No state row → include (new symbol)
      2. `status == OK` and `_is_fresh(row.last_success_ts, today=now.date())` → exclude
      3. `status == OK` and stale → include
      4. `status == FAILED` and `retry_count >= max_retries` → exclude
      5. `status == FAILED` and `not _should_retry(row, max_retries, now=now)` → exclude
      6. `status == FAILED` and `_should_retry(...)` → include
      7. `status == UNFILLABLE` → **exclude** (terminal; this is the key divergence from daily)
      8. `status == PENDING` → include (orchestrator crashed mid-write; resume)
      9. `status == IN_PROGRESS` → include (same recovery case)
    - Returns symbols in source order
  - All status comparisons reference `AcquisitionStatus` enum — no bare strings
  - Effort: 3

- [x] **3.2** **Test** — `test/unit/data/acquisition/daemon/test_minute_work_queue.py`
  - Inject `now` for all time-dependent tests
  - `test_no_state_row_included` — symbol in source, absent from state dict → included
  - `test_fresh_ok_excluded` — parametrized gap: 0, 1, 2 days → excluded (fresh under MIN_DAYS=3); 3, 4 days → included
  - `test_failed_within_backoff_excluded` — FAILED 30s ago, retry_count=1 (backoff=2m) → excluded
  - `test_failed_past_backoff_included` — FAILED 5m ago, retry_count=1 (backoff=2m) → included
  - `test_retry_count_at_max_excluded` — retry_count >= max_retries → excluded regardless of elapsed time
  - `test_unfillable_always_excluded` — parametrize over retry_count values (0, 3, max); UNFILLABLE always excluded
  - `test_pending_status_included` — status=PENDING → included (recovery)
  - `test_in_progress_status_included` — status=IN_PROGRESS → included (recovery)
  - `test_all_symbols_fresh_empty_queue` — every symbol fresh OK → returns `[]`
  - `test_ordering_preserved` — symbols returned in source order, not sorted
  - Effort: 3

---

## 4. Minute Acquisition Daemon

- [x] **4.1** Create `src/manta_trading/data/acquisition/daemon/minute.py`
  - Implement `MinuteAcquisitionDaemon` class
  - Constructor: `__init__(self, orchestrator: MinuteAcquisitionOrchestrator, state_repo: AcquisitionStateRepository, heartbeat_repo: HeartbeatRepository, symbol_source: SymbolSource, event_sink: EventSink, *, config: DaemonConfig)`
  - Attributes: `_shutdown_requested: bool = False`, `_shutdown_event: asyncio.Event`
  - `_request_shutdown(self) -> None` — sets `_shutdown_requested = True` and `_shutdown_event.set()`
  - Do not open any connections in the constructor
  - Effort: 2

- [x] **4.2** Implement `async def run(self) -> None`
  - Structurally mirror `DailyAcquisitionDaemon.run()` (slice 123). Key adjustments:
    - Filter state rows via `state_repo.list(granularity=Granularity.MINUTE)` (NOT DAILY)
    - Build work queue via `build_minute_work_queue(self._symbol_source, state_rows, max_retries=config.max_retries)`
    - Heartbeat writes use `self._config.daemon_id` (will be `MINUTE_DAEMON_ID` when constructed from the CLI)
  - Register SIGTERM and SIGINT handlers via `loop.add_signal_handler(sig, self._request_shutdown)`
  - Write heartbeat `status=STARTING` with `started_at=now`, `pid=os.getpid()`, `hostname=socket.gethostname()`
  - Main loop `while not self._shutdown_requested`:
    - Build state-row dict keyed by symbol
    - Call `build_minute_work_queue(...)`
    - Empty queue: heartbeat `status=IDLE, cycle_count++`; log caught-up; `_interruptible_sleep(config.poll_interval)`; continue
    - Non-empty queue: for each symbol: check `_shutdown_requested` first; heartbeat `status=WORKING, current_symbol=symbol`; `await orchestrator.update_symbol(symbol, run_id=uuid4())`; log result (chunks_written + final_status)
    - End of queue: heartbeat `status=CYCLE_COMPLETE, cycle_count++`
  - Wrap each `update_symbol` call in a try/except that logs `exception` and continues — unhandled exceptions must never kill the daemon (same policy as slice 123)
  - On exit (finally block): heartbeat `status=STOPPED` with current `cycle_count`; return
  - Effort: 4

- [x] **4.3** Implement `async def _interruptible_sleep(self, seconds: int) -> None`
  - `asyncio.wait_for(self._shutdown_event.wait(), timeout=seconds)` — wakes immediately if event set
  - Catch `asyncio.TimeoutError` silently (normal expiry)
  - Do NOT reset the event — shutdown stays set if triggered during sleep
  - This is structurally identical to slice 123's helper; copy verbatim. (Do not extract to a shared base yet — defer abstraction per slice design Future Work.)
  - Effort: 1

- [x] **4.4** **Test** — `test/unit/data/acquisition/daemon/test_minute_daemon.py`
  - Use fakes: `FakeOrchestrator` (records `update_symbol` calls, returns configurable `AcquisitionResult`, supports raising on configured symbols), `InMemoryStateRepo` (reuse slice 123 test helper or adapt), `FakeHeartbeatRepo`, `ListSymbolSource` (returns a fixed list), `NullEventSink`
  - `test_single_cycle_processes_eligible_symbols` — seed: 1 fresh OK, 1 stale OK, 1 no-state, 1 UNFILLABLE, 1 FAILED within backoff, 1 FAILED past backoff. Run one cycle. Assert `update_symbol` called exactly for stale + no-state + past-backoff (3 total); UNFILLABLE and fresh and within-backoff symbols NOT called; heartbeat ends at `CYCLE_COMPLETE`
  - `test_unfillable_never_processed` — seed only UNFILLABLE symbols plus a caught-up-fresh symbol → daemon reaches IDLE without calling orchestrator
  - `test_caught_up_triggers_sleep` — all fresh. One cycle. Assert no orchestrator calls; heartbeat `IDLE`; sleep entered (mock sleep to avoid delay)
  - `test_shutdown_signal_interrupts_loop` — send shutdown after first symbol; assert second symbol not fetched; final heartbeat `STOPPED`
  - `test_shutdown_signal_interrupts_sleep` — daemon in IDLE sleep; trigger `_request_shutdown()`; assert daemon exits promptly (wakes via event, not timeout)
  - `test_orchestrator_exception_does_not_crash_loop` — orchestrator raises on symbol 2; assert symbol 3 is still fetched; daemon completes the cycle; log.exception was emitted
  - `test_cycle_count_increments` — run two cycles; assert heartbeat `cycle_count` progresses 0 → 1 → 2
  - `test_daemon_id_written_to_heartbeat` — use `DaemonConfig(daemon_id=MINUTE_DAEMON_ID, ...)`; assert every heartbeat upsert uses `MINUTE_DAEMON_ID` (spy on `FakeHeartbeatRepo.upsert`)
  - `test_state_repo_filtered_by_minute_granularity` — seed state rows for both DAILY and MINUTE granularities; assert the daemon only considers MINUTE rows (spy on `state_repo.list` calls)
  - Effort: 4

---

## 5. CLI: `_create_minute_orchestrator` Extension

- [x] **5.1** Read `src/manta_trading/cli/commands/data.py` `_create_minute_orchestrator` helper to confirm current signature and return tuple
  - Orientation only — no edits. Slice 124 already returns a 6-tuple including the `InstrumentRegistry`
  - Confirm whether a `requests_per_minute` parameter already flows through; this task adds it if not present
  - Effort: 1

- [x] **5.2** Extend `_create_minute_orchestrator` to accept `requests_per_minute: int` (keyword-only)
  - Pass `requests_per_minute` through to `AlphaVantageMinuteProvider(..., requests_per_minute=requests_per_minute)`
  - Preserve default (30) for existing callers — do not break `mt data minute update` or `mt data minute update-all`
  - Return signature unchanged otherwise
  - Effort: 2

- [x] **5.3** **Test** — `test/unit/cli/commands/test_minute_create_orchestrator.py` (create if absent, or add to the existing file covering this helper)
  - `test_requests_per_minute_forwarded_to_provider` — mock `AlphaVantageMinuteProvider`; call `_create_minute_orchestrator(..., requests_per_minute=25)`; assert constructor received `requests_per_minute=25`
  - `test_default_requests_per_minute_preserved` — call without the kwarg; assert provider received the default (30)
  - Effort: 2

---

## 6. CLI: `mt data minute daemon` Command

- [x] **6.1** Add `_create_minute_daemon(ctx, config: DaemonConfig, requests_per_minute: int) -> tuple[MinuteAcquisitionDaemon, ...]` helper in `data.py`
  - Call extended `_create_minute_orchestrator(ctx, api_key, requests_per_minute=requests_per_minute)` — returns orchestrator, pool, event_sink, provider, registry, (any other handles)
  - Wrap the returned `InstrumentRegistry` in `InstrumentRegistrySymbolSource`
  - Construct `HeartbeatRepository(pool)` — reuse the existing TimescaleDB pool
  - Construct `AcquisitionStateRepository(pool)` — reuse the pool
  - Construct `MinuteAcquisitionDaemon(orchestrator, state_repo, heartbeat_repo, symbol_source, event_sink, config=config)`
  - Return daemon plus all handles that need closing (pool, event_sink, provider) so the command's finally block can close them
  - Effort: 2

- [x] **6.2** Add `minute_daemon` CLI command
  - `@minute_app.command("daemon")`
  - Options:
    - `--poll-interval INT` (default 3600; help: "Seconds to sleep when caught up")
    - `--max-retries INT` (default 5; help: "Max consecutive failures before skipping symbol")
    - `--requests-per-minute INT` (default 30; help: "AV API rate-limit cap. Set below 30 when running alongside the daily daemon to leave headroom.")
  - Build `DaemonConfig(poll_interval, max_retries, daemon_id=MINUTE_DAEMON_ID)`
  - Call `_create_minute_daemon(ctx, config, requests_per_minute)` then `asyncio.run(daemon.run())`
  - Wrap in try/finally to close all returned handles
  - Exit 0 on clean return; non-zero on unhandled exception during startup/construction (loop exceptions are absorbed per policy)
  - Log to structured logger
  - Effort: 2

- [x] **6.3** **Test** — manual smoke test for `mt data minute daemon`
  - Start daily daemon in terminal 1 with `--poll-interval 60`; wait for `IDLE` via `mt data daily status`
  - In terminal 2: `mt data minute daemon --poll-interval 60 --requests-per-minute 25`
  - Confirm logs: STARTING, per-symbol WORKING, CYCLE_COMPLETE; rate cap logged at startup
  - Let daemon reach IDLE (may take significant time for full-history fetch — can test with a small active-instrument set)
  - Send Ctrl-C; confirm orchestrator finishes the in-flight month, STOPPED log, exit code 0
  - Effort: 2

---

## 7. CLI: `mt data minute status` Command

- [x] **7.1** Implement `mt data minute status` command in `data.py`
  - `@minute_app.command("status")`
  - Options: `--json` (output JSON), `--verbose` (per-symbol table)
  - Read `acquisition_state` rows where `granularity=MINUTE` via `state_repo.list(granularity=Granularity.MINUTE)`
  - Read `daemon_heartbeat` row via `heartbeat_repo.get(MINUTE_DAEMON_ID)`
  - Determine alive: `heartbeat_repo.is_alive(heartbeat_row)`
  - Compute counts: total symbols (from registry via `InstrumentRegistrySymbolSource.get_symbols()`), fresh count (`_is_fresh(row.last_success_ts)` — MINUTE variant), stale count, failed count (with/without retry headroom), unfillable count
  - Compute work-queue size by re-running `build_minute_work_queue(...)` against current state (read-only)
  - Find top-3 stalest symbols (largest gap to today; exclude FAILED and UNFILLABLE from stalest list)
  - Render summary table via Rich (match slice 123 `mt data daily status` style):
    ```
    Minute Acquisition Status
    ─────────────────────────
    Daemon:      alive (last heartbeat: 14s ago, cycle 12, working on NVDA)
    Symbols:     487 total │ 412 fresh │ 58 stale │ 12 failed │ 5 unfillable
    Work queue:  70 symbols eligible
    Stalest:     BRK-B (11 days), TSLA (8 days), NVDA (working), AAPL (5 days)
    Failed:      DELISTED1 (Invalid API call, retries: 5/5)
                 NEWIPO2   (No data, retries: 3/5, next try in 6m)
    Unfillable:  PENNY1 (AV 24-month cutoff), PENNY2 (AV 24-month cutoff)
    ```
  - `--verbose`: add per-symbol table with columns: symbol, status, last_success, gap_days, watermark, retry_count, error
  - `--json`: output structured dict with the same fields (daemon, symbol_counts, work_queue_size, stalest, failed, unfillable)
  - All status values reference `AcquisitionStatus` and `DaemonStatus` enums — no bare strings
  - Effort: 3

- [x] **7.2** **Test** — manual smoke test for `mt data minute status`
  - Run `mt data minute status` with minute daemon not running → shows "not running" / last seen timestamp
  - Start minute daemon; run status from second terminal → shows "alive", current symbol, fresh/stale counts
  - Run `mt data minute status --verbose` → per-symbol table including `watermark` and `retry_count` columns
  - Run `mt data minute status --json | jq '.symbol_counts'` → valid JSON with counts object
  - Seed (or wait for) one UNFILLABLE row in `acquisition_state`; confirm it appears in the Unfillable section and is NOT counted as stale
  - Effort: 2

---

## 8. Integration Test: Daemon Multi-Cycle

- [x] **8.1** Create `test/integration/data/acquisition/daemon/test_minute_daemon_integration.py`
  - Skip unless `MT_TIMESCALE_DB_URL` is set (use the existing skip pattern from slice 123 integration test)
  - Use a stub `IMinuteDataProvider` — NO real AlphaVantage; CI must not require the real API
  - Fixture: clean `acquisition_state` rows for granularity=MINUTE and `daemon_heartbeat` rows for `daemon_id=MINUTE_DAEMON_ID` after each test; seed 3 test instruments in the `instruments` table (or reuse an existing fixture)
  - `test_daemon_runs_cycles_and_shuts_down`:
    - Stub provider returns canned per-month OHLCV for each symbol (limited month count to keep test fast)
    - Construct `MinuteAcquisitionDaemon` with stub provider, `poll_interval=1`, `max_retries=5`, `daemon_id=MINUTE_DAEMON_ID`
    - Run daemon in a background task via `asyncio.create_task(daemon.run())`
    - Poll `acquisition_state` until all 3 symbols have `status=OK` (bounded timeout e.g. 60 s)
    - Call `daemon._request_shutdown()` to trigger graceful stop
    - Await the task with a timeout (e.g. 30 s)
    - Assert: all 3 symbols have `status=OK` with non-null watermarks
    - Assert: heartbeat row has `status=STOPPED`, `daemon_id=MINUTE_DAEMON_ID`
    - Assert: `cycle_count >= 1`
    - Assert: event log has `run_started`/`chunk_ok`/`run_finished` entries per symbol
  - `test_daemon_resumes_after_restart`:
    - Run daemon for one cycle (as above), shut down
    - Start a fresh `MinuteAcquisitionDaemon` instance against the same DB
    - Assert it reaches IDLE quickly (no re-fetch of already-fresh symbols — count orchestrator calls via a wrapping spy)
  - `test_daemon_survives_provider_failure`:
    - Stub provider raises `RuntimeError` for symbol B; returns canned data for A and C
    - Run daemon until cycle complete; shut down
    - Assert: A and C have `status=OK`; B has `status=FAILED` with `retry_count=1`; daemon reached STOPPED heartbeat cleanly
  - Effort: 5

- [x] **8.2** **Test** — run integration test locally with real test TimescaleDB
  - `pytest test/integration/data/acquisition/daemon/test_minute_daemon_integration.py -v` — green
  - `pytest test/integration/data/acquisition/daemon/ -v` — confirm slice 123's daily integration test still passes (shared heartbeat table must not regress)
  - Effort: 1

---

## 9. Coexistence Check: Daily + Minute Daemons

- [x] **9.1** Confirm both daemons can run concurrently
  - Start `mt data daily daemon --poll-interval 60` in terminal 1; wait for IDLE
  - Start `mt data minute daemon --poll-interval 60 --requests-per-minute 25` in terminal 2
  - Run `mt data daily status` and `mt data minute status` in a third terminal
  - Confirm: both daemons show `alive` under their respective `daemon_id` keys; no heartbeat collision (the `daemon_heartbeat` table has distinct rows per `daemon_id`)
  - Confirm: aggregate AV request rate stays below 30 rpm (observe via daemon logs over a 5-minute window)
  - Effort: 2

- [x] **9.2** Graceful shutdown of both daemons
  - Ctrl-C the minute daemon first; confirm STOPPED heartbeat in `mt data minute status`
  - Ctrl-C the daily daemon; confirm STOPPED heartbeat in `mt data daily status`
  - Run `psql $MT_TIMESCALE_DB_URL -c 'SELECT daemon_id, status FROM daemon_heartbeat;'` → both rows have `status='STOPPED'`
  - Effort: 1

---

## 10. End-to-End Verification

- [x] **10.1** Run the full slice verification walkthrough from the slice design (§ Verification Walkthrough steps 1–21)
  - Note any deviations or surprises; record actual command output for the "Verification Walkthrough" section refinement during wrap-up
  - Effort: 3

- [x] **10.2** Run the full unit test suite
  - `pytest test/unit/ -v` — all tests pass; new minute daemon tests included
  - Slice 121/122/123/124 tests still green
  - Effort: 1

- [x] **10.3** Run the full integration test suite
  - `pytest test/integration/ -v` — new minute daemon integration test green; existing integrations pass
  - Effort: 1

---

## 11. Wrap-up

- [x] **11.1** Verify file size budgets
  - Each new module ≤ ~300 lines. `minute.py` daemon loop ≤ ~200 lines (matches slice 123 budget)
  - `minute_work_queue.py` and `symbol_sources.py` are small by construction
  - If `minute.py` exceeds 200 lines, factor heartbeat-write helpers or logging helpers into a small helper module in the same package
  - Effort: 1

- [x] **11.2** Lint and type check
  - `ruff check` and `mypy` (or pyright in strict mode) on all new and modified files — zero errors
  - Effort: 1

- [x] **11.3** Confirm no magic strings
  - `DaemonStatus` enum for all status values; `MINUTE_DAEMON_ID` constant for daemon identity; `Granularity.MINUTE` and `AcquisitionStatus` enums throughout
  - No bare `"minute-acquisition"`, `"WORKING"`, `"STOPPED"`, `"OK"`, `"FAILED"`, `"UNFILLABLE"` literals in code
  - Effort: 1

- [x] **11.4** Self-review against slice design § Success Criteria — every bullet checked
  - Effort: 1

- [x] **11.5** Refine slice design § Verification Walkthrough with actual observed output and any corrections from task 10.1
  - Goal: a human (or external AI) could follow the walkthrough and reproduce the demo
  - Effort: 1

- [x] **11.6** Update slice design `125-slice.minute-acquisition-daemon.md` YAML frontmatter:
  - `status: complete`
  - `dateUpdated: <today>`
  - Effort: 1

- [x] **11.7** Update task file `125-tasks.minute-acquisition-daemon.md` YAML frontmatter:
  - `status: complete`
  - `dateUpdated: <today>`
  - Effort: 1

- [x] **11.8** Update `CHANGELOG.md` with a slice 125 entry (follow existing format; one-line summary + link to slice design)
  - Effort: 1

- [x] **11.9** Update slice plan `project-documents/user/architecture/120-slices.data-acquisition.md` entry 5 checkbox from `[ ]` to `[x]`
  - Effort: 1

- [x] **11.10** Commit on slice branch: `feat: add minute acquisition daemon and status CLI`
  - Stage all new and modified files from project root
  - **Commit**: `feat: add minute acquisition daemon and status CLI`
  - Effort: 1

---

## Notes for the Implementer

- **`MinuteAcquisitionOrchestrator` is the only path into per-symbol work.** The daemon never calls `run_acquisition_unit` directly.
- **Shutdown latency is bounded by one-month fetch + one DB write (~2–4 s).** The orchestrator already checkpoints per-month (slice 124) so SIGTERM in the middle of a 24-month fetch loses at most one unfinished month.
- **`_interruptible_sleep` must not use `asyncio.sleep`.** It uses `asyncio.wait_for(event.wait(), timeout=N)` so a shutdown signal wakes it immediately.
- **UNFILLABLE is terminal.** This is the single most important divergence from slice 123's work-queue builder. Symbols marked UNFILLABLE (AV 24-month cutoff exceeded, or permanent API errors past max_retries) must NEVER be retried by the daemon.
- **Symbol source uses the InstrumentRegistry, not MarketDB.** Slice 124 post-merge smoke testing established this — preferred shares and test artifacts valid in the daily DB are not valid for AV minute API and will burn quota.
- **`--requests-per-minute` is the manual rate-limit handle.** Operator workflow: start daily daemon → wait for IDLE → start minute daemon with `--requests-per-minute 25`. No shared coordination primitive is built in this slice; the future cross-daemon coordination slice will introduce it.
- **`MINUTE_DAEMON_ID` must be a constant, not a literal.** Both the daemon (writer) and the status CLI (reader) reference the same constant.
- **Integration tests use a stub provider.** The real AlphaVantage API must never be called from CI or automated tests.
- **Reuse, don't re-implement, slice 123 infra.** `HeartbeatRepository`, `DaemonHeartbeat`, `DaemonStatus`, `DaemonConfig`, `SymbolSource`, `_should_retry`, `_interruptible_sleep` pattern, `daemon_heartbeat` table — all are used as-is. The only types addition is the `MINUTE_DAEMON_ID` constant.
- **Defer framework extraction.** With N=2 daemons and a known third daemon (tick) coming, the right abstraction is not yet evident. Resist the urge to extract a `DaemonBase` in this slice; copy the shutdown/sleep/heartbeat-write patterns from slice 123 and move on. The Future Work section of the slice design captures the deferred extraction.
