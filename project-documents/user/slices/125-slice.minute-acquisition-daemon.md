---
docType: slice-design
slice: minute-acquisition-daemon
project: trading
parent: user/architecture/120-slices.data-acquisition.md
dependencies: [100, 121, 123, 124, 900]
interfaces: []
dateCreated: 20260414
dateUpdated: 20260414
status: complete
---

# Slice Design: Minute Acquisition Daemon

## Overview

Wraps the slice 124 `MinuteAcquisitionOrchestrator` in a long-running daemon process. This is the second daemon in the system; it replicates the pattern proven by slice 123 (daily daemon) and applies it to the harder minute-data case: larger per-symbol work (up to 24 monthly chunks), tighter rate-limit pressure, and a richer set of terminal states (OK / FAILED / UNFILLABLE).

The daemon:

1. Cycles through every active instrument from the registry, calling `MinuteAcquisitionOrchestrator.update_symbol()` per symbol.
2. Detects "caught up" — every active symbol's last success within `MIN_DAYS=3` calendar days, no retryable failures pending, no retryable gaps — and sleeps for `poll_interval` seconds.
3. Shuts down gracefully on SIGTERM/SIGINT: finishes the in-flight month, checkpoint is written by the orchestrator, heartbeat flips to `STOPPED`, exit 0.
4. Writes heartbeat rows to the existing `daemon_heartbeat` table (added by slice 123) under the `daemon_id="minute-acquisition"` key.
5. Excludes permanently unfillable symbols (AV history older than 24 months, or persistent API errors past `max_retries`) from the work queue so rate-limit budget is spent only on achievable work.

A new `mt data minute status` CLI command reads `acquisition_state` (granularity=MINUTE) plus the heartbeat row and renders daemon health, work-queue summary, and per-symbol freshness.

**Manual rate-limit sequencing:** This slice does NOT coordinate rate limits with the daily daemon. The operator starts daily first; once it reports `IDLE` (typically within ~20 minutes), the minute daemon can be started. A `--requests-per-minute` flag lets the operator cap the minute daemon below the 30 rpm account limit to leave headroom for daily's hourly polls. Shared rate-limit coordination is called out in Out of Scope.

**Out of scope:** daemon framework extraction (deferred per architecture doc until a third daemon exists), shared rate-limit coordination between daily and minute daemons (future slice), concurrent per-symbol fetching (sequential at 30 rpm is correct for a single AV API key — see Technical Decisions), trading-calendar-aware gap detection (Initiative 140).

## Value

**Operator-facing:** Minute data stays current without intervention. The operator starts the minute daemon after daily is IDLE and walks away. `mt data minute status` answers "how current is minute data?" without SSH/psql. Restarts resume at the next unfetched month — no wasted API quota.

**Developer-facing:** Second concrete daemon instance. Validates that the patterns introduced in slice 123 (loop, heartbeat, interruptible sleep, work queue, retry backoff) generalize beyond the trivial one-request-per-symbol case. This is the final datapoint needed before a future extraction initiative can confidently lift a shared `DaemonBase`.

**Architectural:** Completes the equities + AlphaVantage acquisition vertical (slices 121–125). After this slice, both daily and minute pipelines run unattended, resume from failure, and expose their state via CLI. The system satisfies the "zero human intervention for steady-state operation" criterion from the architecture document.

## Technical Scope

### In Scope

- New `MinuteAcquisitionDaemon` class — async main loop, signal handling, caught-up detection, interruptible sleep, heartbeat writes. Mirrors `DailyAcquisitionDaemon` structurally.
- New `build_minute_work_queue` pure function — variant of slice 123's `build_work_queue` tuned for minute semantics (see Technical Decisions).
- Extension of `manta_trading.data.acquisition.daemon.types`: add `MINUTE_DAEMON_ID` constant; reuse existing `SymbolSource` protocol and `DaemonConfig` dataclass.
- Reuse slice 123's `HeartbeatRepository`, `DaemonHeartbeat`, `DaemonStatus`, `daemon_heartbeat` table — no migration needed.
- `InstrumentRegistrySymbolSource` adapter — a `SymbolSource` implementation that calls `InstrumentRegistry.list_instruments(active_only=True)` and returns symbol strings. This replaces the daily daemon's `_MarketDBSymbolSource` inline adapter usage for minute (and is reusable by any future daemon sourcing symbols from the instrument registry).
- `mt data minute daemon` CLI command — foreground process, passes `--poll-interval`, `--max-retries`, `--requests-per-minute` flags.
- `mt data minute status` CLI command — table + JSON output, `--verbose` for per-symbol detail.
- Wire the `--requests-per-minute` flag through to `AlphaVantageMinuteProvider` construction so the operator can cap the minute daemon below 30 rpm.
- Unit tests: daemon loop with fakes, minute work-queue builder (including UNFILLABLE exclusion), status CLI rendering.
- Integration test: daemon runs N cycles against the test TimescaleDB with a stub minute provider, handles simulated failures and shutdowns, survives restart.

### Out of Scope

- Daemon framework extraction / shared base class (deferred until a third daemon exists, per architecture).
- **Shared rate-limit coordination between daily and minute daemons.** Manual operator sequencing (start daily first, then minute) is the interim solution. A future slice may introduce a DB-backed token bucket or a single "acquisition supervisor" process; either is a design effort on the order of slice 121, not a daemon wrapper concern.
- Concurrent per-symbol fetching within the minute daemon. The AV rate limit is per-API-key; any concurrency inside one daemon only requires coordinating requests against the same RateLimiter, which the existing serial loop already does correctly. Concurrency is a response to a different problem (multi-provider, multi-key) and is not needed today.
- Trading-calendar-aware gap detection (weekends/holidays). `MIN_DAYS=3` is a deliberately loose interim threshold that absorbs weekends without special-casing. A `TradingCalendar` class already exists (slice 102) with `is_trading_day(date)` backed by the `trading_calendars`/`trading_holidays` tables — tightening `_is_fresh` to "1 trading day" is a focused follow-up slice rather than this slice's concern (see Future Work).
- Priority tiers / symbol-universe segmentation (e.g. S&P 500 fetched every trading day, Russell 2000 fetched weekly or during off-hours). Independent from calendar-aware freshness; likely a separate follow-up slice (see Future Work).
- Retry backoff tuning beyond the existing exponential schedule (1m, 2m, 4m, 8m, 16m, 32m, 60m capped). Acceptable as-is.
- Daemonization (double-fork, PID files). Foreground process; systemd/supervisord owns lifecycle, per slice 123.
- HTTP health endpoint or Unix socket IPC. Heartbeat table remains the sole liveness channel.

## Architecture

### Component Structure

```
MinuteAcquisitionDaemon
  ├── MinuteAcquisitionOrchestrator (slice 124, injected)
  │     ├── AlphaVantageMinuteProvider
  │     ├── TimescaleMinuteWriter
  │     ├── DataProcessor
  │     └── run_acquisition_unit (slice 121)
  ├── AcquisitionStateRepository (slice 121, injected)
  ├── HeartbeatRepository (slice 123, injected)
  ├── InstrumentRegistrySymbolSource (new adapter around InstrumentRegistry)
  └── EventSink (slice 121, injected)
```

The daemon owns the outer loop; the orchestrator owns per-symbol work. The daemon never calls `run_acquisition_unit` directly — always through `orchestrator.update_symbol()`. This preserves the single-code-path guarantee from slice 124's smoke-test fixes.

### Data Flow

```
MinuteAcquisitionDaemon.run()
  │
  ├─ on startup:
  │    register SIGTERM/SIGINT → set _shutdown_requested + _shutdown_event
  │    heartbeat.upsert(status=STARTING, daemon_id=MINUTE_DAEMON_ID)
  │
  ├─ main loop (while not _shutdown_requested):
  │    │
  │    ├─ build_minute_work_queue():
  │    │    state_rows = state_repo.list(granularity=MINUTE)
  │    │    symbols   = symbol_source.get_symbols()  # from InstrumentRegistry
  │    │    apply inclusion rules (see Technical Decisions § work queue)
  │    │
  │    ├─ if queue is empty:
  │    │    heartbeat.upsert(status=IDLE, cycle_count++)
  │    │    log "caught up — sleeping {poll_interval}s"
  │    │    await _interruptible_sleep(poll_interval)
  │    │    continue
  │    │
  │    ├─ for symbol in queue:
  │    │    if _shutdown_requested: break
  │    │    heartbeat.upsert(status=WORKING, current_symbol=symbol)
  │    │    try:
  │    │      result = await orchestrator.update_symbol(symbol, run_id=uuid4())
  │    │    except Exception:
  │    │      log.exception; continue  # orchestrator already checkpointed;
  │    │                                 # unhandled exceptions must not kill the daemon
  │    │    log result (chunks_written / status / failed_chunks)
  │    │
  │    └─ heartbeat.upsert(status=CYCLE_COMPLETE, cycle_count++)
  │
  └─ on shutdown:
       heartbeat.upsert(status=STOPPED)
       close resources (provider http client, pools, event sink)
       exit 0
```

### State Management

- **`acquisition_state`** (slice 121) — read by the work-queue builder, written by the orchestrator. No schema change; the minute daemon filters rows on `granularity=MINUTE`.
- **`daemon_heartbeat`** (slice 123) — reused. The minute daemon writes rows under `daemon_id="minute-acquisition"`. The table already supports multiple daemon identities via its `daemon_id` primary key.
- **Events** (slice 121) — orchestrator already emits `run_started`, `chunk_ok`, `chunk_failed`, `run_finished`. The daemon does not emit its own events in this slice; daemon-level lifecycle events (`daemon_started`, `daemon_stopped`, `cycle_complete`) are a future concern if the event log grows a visualization layer.

## Technical Decisions

### Reuse vs. Fork: Work Queue Builder

Slice 123 ships `build_work_queue` under `daemon/work_queue.py`. Minute semantics differ in two load-bearing ways:

1. **UNFILLABLE is a terminal state for minute**, not a retryable one. AV's 24-month intraday window makes "gap older than 24 months" permanent. Slice 123's rule 6 includes UNFILLABLE in the queue (treating it as "unknown / try again"). Minute must exclude UNFILLABLE.
2. **Freshness threshold differs.** Daily uses `MIN_DAYS=2`, minute uses `MIN_DAYS=3` (slice 124, `minute/freshness.py`). The daily `_is_fresh` is not applicable — the minute variant must be used.

Two options:
- **(a)** Parameterize `build_work_queue` on `(is_fresh_fn, exclude_unfillable: bool)` and reuse one function.
- **(b)** Fork: add `build_minute_work_queue` alongside.

**Decision: (b) — fork.** The shared function is 30 lines of straight-line logic; parameterization introduces a callable injection for a 1-line call site and a boolean flag that acts as logic dispatch (a smell per CLAUDE.md "no user-accessible labels as logical structure" — though here it's a boolean, the same principle applies: branching behavior on a flag obscures intent). Two focused functions read more clearly and match the architecture doc's guidance that shared-code extraction waits for the third daemon. When the tick daemon arrives, all three variants (daily, minute, tick) become extraction inputs simultaneously — better signal for the right abstraction than doing it with N=2.

### Minute Work-Queue Rules

`build_minute_work_queue(symbol_source, state_rows, max_retries, now=None)`:

1. No state row → include (new symbol).
2. `status=OK` and `_is_fresh(last_success_ts, min_days=MIN_DAYS)` → exclude (fresh).
3. `status=OK` and stale → include.
4. `status=FAILED` and `retry_count >= max_retries` → exclude (permanently failed until manual reset).
5. `status=FAILED` and backoff not elapsed → exclude (wait).
6. `status=FAILED` and backoff elapsed → include (retry).
7. `status=UNFILLABLE` → **exclude** (do not retry; permanent).
8. `status=PENDING` or `status=IN_PROGRESS` → include (recovery: daemon crashed mid-write, orchestrator will resume at the last checkpoint).

Freshness uses `manta_trading.data.acquisition.minute.freshness._is_fresh` (MIN_DAYS=3). Backoff reuses slice 123's `_should_retry` verbatim — the exponential schedule is orchestrator-agnostic.

### Rate Limiting: Manual Operator Sequencing

The minute daemon does not coordinate rate limits with the daily daemon. Rationale:

- **Daily is near-idle by design.** With 30 rpm and one request per daily symbol, a full cycle completes in <20 minutes and the daemon sleeps for the configured `poll_interval` (default 1 hour). During steady state, daily consumes a tiny fraction of the budget.
- **Shared coordination is an initiative.** A correct cross-process rate limit requires shared state (DB-backed token bucket, Redis, or a supervisor process). Each design has meaningful trade-offs (latency overhead, durability, failure modes). Building that inside slice 125 would balloon its scope.
- **Manual sequencing is safe and observable.** Operator workflow: start daily → wait for `status=IDLE` (visible via `mt data daily status`) → start minute. The minute daemon exposes `--requests-per-minute` to cap itself below 30; setting `--requests-per-minute=25` leaves 5 rpm headroom for daily's hourly polling cycles. Measured during slice 124 smoke tests, natural HTTP round-trip latency (~1.2–1.5s/req) already spaces requests below the 30 rpm ceiling in practice; the RateLimiter is a safety net rather than an active throttle.

This is documented in Out of Scope with a pointer to the future coordination slice.

### `--requests-per-minute` Flag Wiring

The flag on `mt data minute daemon` (default: 30) is passed through the CLI `_create_minute_orchestrator` helper (added in slice 124) into `AlphaVantageMinuteProvider(requests_per_minute=...)`. That constructor already accepts the parameter and builds its own `RateLimiter(max_calls=rpm, period=60)`. No orchestrator or core changes needed.

### Symbol Source: InstrumentRegistry (not MarketDB)

Slice 124 moved `minute update-all` from `MarketDB.readLRUSymbolList` to `InstrumentRegistry.list_instruments(active_only=True)` after a production bug (preferred-share symbols valid in daily DB but not in AV minute API burned quota). The minute daemon uses the same source. A new `InstrumentRegistrySymbolSource` adapter implements the existing `SymbolSource` protocol:

```python
class InstrumentRegistrySymbolSource:
    def __init__(self, registry: InstrumentRegistry) -> None:
        self._registry = registry

    def get_symbols(self) -> list[str]:
        return [i.symbol for i in self._registry.list_instruments(active_only=True)]
```

This adapter lives in `daemon/symbol_sources.py` (new leaf module) so any future daemon (e.g. tick) can share it.

### Graceful Shutdown Latency

Minute `update_symbol()` fetches up to 24 months sequentially; per-month checkpoint is already implemented in slice 124. Worst-case shutdown latency = one month fetch + one DB write + one state update ≈ 2–4 seconds. This is acceptable — much better than the naive "cancel mid-chunk" path which would leave the checkpoint unwritten.

The daemon checks `_shutdown_requested` between symbols AND does not interrupt the in-flight `update_symbol()` call. If a user needs faster shutdown mid-symbol, SIGKILL is the escape hatch; the orchestrator's per-month checkpointing bounds the data loss to at most one unfinished month.

### Caught-up Detection

A symbol contributes to "caught up" iff:
- `status=OK` and `_is_fresh(last_success_ts)` (gap < 3 days), OR
- `status=UNFILLABLE` (terminal, not counted as work), OR
- `status=FAILED` and `retry_count >= max_retries` (terminal until manual reset).

The daemon is caught up iff `build_minute_work_queue()` returns an empty list. No separate caught-up predicate is needed.

### Unhandled Exception Policy

Slice 123's daemon logs `exception` and continues the loop — an unhandled exception for one symbol must not kill the daemon. Slice 125 follows the same policy. Slice 124's smoke test revealed that the chief risk here is a provider method silently swallowing API errors (fixed in slice 124 by making `_fetch_month` raise). The orchestrator surfaces real failures as `AcquisitionResult.final_status=FAILED`, not as exceptions, so the exception handler exists purely as a backstop for unexpected defects.

## CLI Commands

### `mt data minute daemon`

```
Usage: mt data minute daemon [OPTIONS]

  Run the minute acquisition daemon (foreground, long-running).

Options:
  --poll-interval         INTEGER  Seconds to sleep when caught up [default: 3600]
  --max-retries           INTEGER  Max consecutive failures before skipping symbol [default: 5]
  --requests-per-minute   INTEGER  AV API rate-limit cap [default: 30]
  --help                           Show this message and exit.
```

Behavior:
- Constructs orchestrator via the existing `_create_minute_orchestrator` helper (slice 124), passing `requests_per_minute`.
- Constructs `InstrumentRegistrySymbolSource`, `HeartbeatRepository`, `DaemonConfig(daemon_id=MINUTE_DAEMON_ID, ...)`.
- Constructs `MinuteAcquisitionDaemon`, calls `asyncio.run(daemon.run())`.
- Exits 0 on graceful shutdown, non-zero on unhandled exception during startup (not in loop — loop exceptions are logged and absorbed).

### `mt data minute status`

```
Usage: mt data minute status [OPTIONS]

  Report minute acquisition daemon health and per-symbol freshness.

Options:
  --json      Output as JSON instead of table
  --verbose   Show per-symbol detail (default: summary only)
  --help      Show this message and exit.
```

Default output (summary):

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

With `--verbose`: adds a per-symbol table with columns `symbol, status, last_success, gap_days, watermark, retry_count, error`.

Implementation: reads `acquisition_state` rows where `granularity=MINUTE` via `state_repo.list()`, reads `daemon_heartbeat` where `daemon_id=MINUTE_DAEMON_ID`, renders with Rich (existing pattern from `mt data daily status`). JSON output mirrors the Rich summary structure for scripting.

## Cross-Slice Dependencies and Interfaces

**Depends on:**

- Slice 121 — `AcquisitionStateRepository`, `AcquisitionStatus`, `Granularity`, `EventSink`, `AcquisitionEvent*`, `run_acquisition_unit`.
- Slice 123 — `HeartbeatRepository`, `DaemonHeartbeat`, `DaemonStatus`, `daemon_heartbeat` table, `DaemonConfig`, `SymbolSource` protocol, `_should_retry` (may be imported directly or duplicated — see Technical Decisions on work-queue forking).
- Slice 124 — `MinuteAcquisitionOrchestrator`, `MIN_DAYS=3`, `_is_fresh` (minute variant), `_create_minute_orchestrator` CLI helper.
- Slice 100 — `InstrumentRegistry.list_instruments` for the symbol source.
- Slice 900 — `Settings`, CLI framework (Typer), structured logging, Rich output.

**Provides for:**

- No downstream slices in the 120 band depend on this slice. Slices 126+ (reserved for futures, Databento, tick acquisition) are separate verticals.
- A future "daemon framework extraction" slice will use `DailyAcquisitionDaemon` (slice 123) and `MinuteAcquisitionDaemon` (this slice) together with a third daemon (likely tick) as the extraction inputs.
- A future "rate-limit coordination" slice will replace the manual-sequencing contract with a coordinated budget allocator. Both `mt data daily daemon` and `mt data minute daemon` would accept a shared rate-limit source in that design.

**Interface stability:** The daemon has no public API beyond its CLI entry points. Internal classes (`MinuteAcquisitionDaemon`, `InstrumentRegistrySymbolSource`, `build_minute_work_queue`) may be refactored freely during framework extraction.

## File Layout

New files:

- `src/manta_trading/data/acquisition/daemon/minute.py` — `MinuteAcquisitionDaemon` class
- `src/manta_trading/data/acquisition/daemon/minute_work_queue.py` — `build_minute_work_queue` + minute-specific helpers
- `src/manta_trading/data/acquisition/daemon/symbol_sources.py` — `InstrumentRegistrySymbolSource` adapter
- `test/unit/data/acquisition/daemon/test_minute_daemon.py` — daemon loop unit tests
- `test/unit/data/acquisition/daemon/test_minute_work_queue.py` — queue rules, UNFILLABLE exclusion, freshness
- `test/unit/data/acquisition/daemon/test_symbol_sources.py` — registry adapter
- `test/integration/data/acquisition/daemon/test_minute_daemon_integration.py` — multi-cycle integration test with stub provider and real TimescaleDB

Modified files:

- `src/manta_trading/data/acquisition/daemon/types.py` — add `MINUTE_DAEMON_ID = "minute-acquisition"` constant alongside `DAILY_DAEMON_ID`.
- `src/manta_trading/cli/commands/data.py` — add `minute_daemon` and `minute_status` commands. Extend `_create_minute_orchestrator` (already exists) to accept `requests_per_minute` and pass it through to the AV provider constructor.
- `CHANGELOG.md` — new slice 125 entry.

No migrations: the `daemon_heartbeat` table already exists from slice 123.

## Testing Strategy

### Unit: `test_minute_daemon.py`

Uses fakes for all dependencies (no real DB, no real provider, no real HTTP). `FakeOrchestrator`, `InMemoryStateRepository`, `FakeHeartbeatRepository`, `ListSymbolSource`, `NullEventSink`.

- `test_single_cycle_processes_stale_and_new_symbols` — seed: 1 fresh, 1 stale, 1 FAILED within backoff, 1 FAILED past backoff, 1 UNFILLABLE, 1 no-state. Assert: orchestrator called for stale + past-backoff + no-state only; UNFILLABLE not called; heartbeat entered WORKING for each.
- `test_caught_up_triggers_sleep` — seed: all symbols fresh. Assert: no orchestrator calls, heartbeat=IDLE, interruptible sleep entered.
- `test_shutdown_signal_interrupts_loop` — start daemon, send shutdown after first symbol. Assert: second symbol not fetched, heartbeat=STOPPED.
- `test_shutdown_signal_interrupts_sleep` — daemon in IDLE sleep. Send shutdown. Assert: wakes quickly, exits.
- `test_unhandled_orchestrator_exception_absorbs_and_continues` — orchestrator raises on symbol 1. Assert: symbol 2 is still attempted, log.exception was emitted, daemon did not crash.
- `test_unfillable_symbols_excluded_from_queue` — seed: 1 OK-stale, 1 UNFILLABLE. Assert: orchestrator called once (only the stale symbol).
- `test_cycle_count_increments_per_cycle` — run two cycles. Assert: heartbeat.cycle_count = 2.
- `test_daemon_id_is_minute_acquisition` — assert all heartbeat writes use `MINUTE_DAEMON_ID`.

### Unit: `test_minute_work_queue.py`

Pure-function tests (injectable `now`).

- Parametrized freshness: gap=0,1,2 → exclude (fresh under MIN_DAYS=3); gap=3,4 → include.
- `test_failed_within_backoff_excluded` / `test_failed_past_backoff_included` — schedule 1m, 2m, 4m, 8m, 16m, 32m, 60m.
- `test_retry_count_at_max_excluded`.
- `test_unfillable_status_always_excluded` — critical divergence from daily. Parametrize over retry_count values; always excluded.
- `test_pending_status_included` — orchestrator crashed mid-write; daemon must retry.
- `test_in_progress_status_included` — same case; treated as recovery.
- `test_no_state_row_included`.
- `test_empty_symbol_source_returns_empty`.

### Unit: `test_symbol_sources.py`

- `test_instrument_registry_symbol_source_returns_symbols` — fake registry with 3 instruments, `active_only=True` is forwarded.
- `test_instrument_registry_symbol_source_excludes_inactive` — fake registry with active=False for some; verify the `active_only` kwarg is passed.

### Integration: `test_minute_daemon_integration.py`

Skipped unless `MT_TIMESCALE_DB_URL` is set. Uses a stub minute provider (returns deterministic canned OHLCV for each month).

- `test_daemon_runs_cycles_and_shuts_down` — 3 symbols, `poll_interval=1`. Let daemon run until IDLE; send SIGTERM via `os.kill(pid, signal.SIGTERM)`. Assert: all 3 symbols `status=OK` with watermarks set; heartbeat `status=STOPPED`; cycle_count ≥ 1; events table has `run_started/chunk_ok/run_finished` per symbol (24 `chunk_ok` each, capped by stub).
- `test_daemon_resumes_after_restart` — run daemon for 1 cycle, kill, restart. Assert: restarted daemon detects freshness from prior run and reaches IDLE quickly without re-fetching completed symbols.
- `test_daemon_survives_provider_failure` — stub provider raises `RuntimeError` for symbol B. Assert: symbol A and C succeed; symbol B recorded as `status=FAILED` with `retry_count=1`; daemon still alive.

### Regression

- All slice 121/122/123/124 tests pass unchanged.
- `mt data minute update SYMBOL` still works (slice 124 path unchanged).
- `mt data minute update-all` still works.
- `mt data daily daemon` and `mt data daily status` still work (the minute daemon must not break the daily daemon's heartbeat writes — they share the `daemon_heartbeat` table but under different `daemon_id` PKs).

## Success Criteria

- `MinuteAcquisitionDaemon` runs as a foreground process via `mt data minute daemon`, continuously cycling through active instruments from the registry.
- Daemon detects "caught up" (every active symbol `OK & fresh`, or terminal `UNFILLABLE`, or terminal `FAILED ≥ max_retries`) and enters IDLE with `poll_interval` sleep.
- Daemon shuts down gracefully on SIGTERM/SIGINT: finishes the in-flight `update_symbol()` (which itself already checkpoints per-month), writes `STOPPED` heartbeat, exits 0. Shutdown latency bounded by one-month fetch + one DB write (~2–4 s worst case).
- Daemon survives restart: restarted daemon skips fresh symbols, retries eligible failures, honors terminal states.
- `UNFILLABLE` symbols are permanently excluded from the work queue (this is the key divergence from slice 123's queue).
- `FAILED` symbols are retried with the shared exponential backoff (1m/2m/4m/8m/16m/32m/60m); symbols exceeding `max_retries` are excluded until manually reset.
- `mt data minute status` accurately reports: daemon alive/dead + current symbol, symbol counts (fresh / stale / failed / unfillable), work-queue size, stalest symbols, failed symbols with retry context, unfillable symbols. `--verbose` adds per-symbol detail.
- `--requests-per-minute` flag caps the AV provider's rate limiter at the specified value.
- The minute daemon and daily daemon coexist under distinct `daemon_id` PKs in the shared `daemon_heartbeat` table.
- No magic strings: `MINUTE_DAEMON_ID` constant, `DaemonStatus` enum, `AcquisitionStatus` enum used consistently.
- Source files ≤ ~300 lines; the daemon main loop ≤ ~200 lines (slice 123 achieved this; 125 should track the same budget).
- All slice 121/122/123/124 tests pass unchanged. New daemon tests pass.

## Verification Walkthrough

Draft — will be refined during Phase 6 with actual commands, observed output, and any corrections discovered during implementation.

**Setup**

1. `git checkout -b 125-slice.minute-acquisition-daemon` (from `main`).
2. Confirm test databases reachable:
   ```bash
   echo $MT_TIMESCALE_DB_URL
   echo $MT_ALPHAVANTAGE_API_KEY
   ```
3. Confirm `daemon_heartbeat` table exists (from slice 123):
   ```bash
   psql $MT_TIMESCALE_DB_URL -c '\d daemon_heartbeat'
   ```
   Expected: table exists with `daemon_id` PK. No migrations needed for this slice.
4. Confirm instrument registry has test symbols:
   ```bash
   mt data instruments list
   ```
   Expected: list of active equities (e.g. AAPL, MSFT, NVDA). Active-only
   is the default; pass `--inactive` to include inactive instruments.

**Tests (no external calls)**

5. Run the new test suite:
   ```bash
   pytest test/unit/data/acquisition/daemon/ -v
   ```
   Expected: all slice 123 + slice 125 daemon unit tests pass.

6. Run the full acquisition suite:
   ```bash
   pytest test/unit/data/acquisition/ -v
   ```
   Expected: slices 121/122/123/124 unchanged + new 125 tests pass.

7. Run integration tests (requires DB):
   ```bash
   pytest test/integration/data/acquisition/daemon/ -v
   ```
   Expected: `test_minute_daemon_integration` passes; daily integration still passes.

**Daily + minute coexistence**

8. Start the daily daemon first (terminal 1):
   ```bash
   mt data daily daemon --poll-interval 60
   ```
   Expected: cycles, reaches IDLE within a few minutes.

9. Check daily status (terminal 3):
   ```bash
   mt data daily status
   ```
   Expected: `Daemon: alive`, status=IDLE.

10. Start the minute daemon (terminal 2) with a conservative rate cap:
    ```bash
    mt data minute daemon --poll-interval 60 --requests-per-minute 25
    ```
    Expected: daemon starts, logs the rate cap, begins cycling through symbols. Log lines show per-month progress (`Symbol NVDA: OK (24 chunk(s))` after a full history fetch).

**Status during operation**

11. Check minute status while the daemon is working:
    ```bash
    mt data minute status
    ```
    Expected: `Daemon: alive`, current_symbol populated, symbol counts changing across repeated invocations.

12. Check minute status with verbose:
    ```bash
    mt data minute status --verbose
    ```
    Expected: per-symbol table with status, watermark, gap days, retry_count.

13. JSON output:
    ```bash
    mt data minute status --json | jq '.symbol_counts'
    ```
    Expected: a JSON object with `total`, `fresh`, `stale`, `failed`, `unfillable`
    integer counts. Other top-level keys in the full payload: `daemon`,
    `work_queue_size`, `stalest`, `failed`, `unfillable` (and `detail` when
    `--verbose` is passed).

**Graceful shutdown**

14. In terminal 2, send SIGTERM (Ctrl-C):
    Expected: daemon logs "Shutdown requested — finishing current symbol and stopping.", completes the in-flight month fetch and checkpoint, writes STOPPED heartbeat, exits 0.

15. Check status after shutdown:
    ```bash
    mt data minute status
    ```
    Expected: `Daemon: not running (last seen: Xs ago, stopped cleanly)`. Symbol freshness still accurate.

**Restart and resume**

16. Restart the minute daemon:
    ```bash
    mt data minute daemon --poll-interval 60 --requests-per-minute 25
    ```
    Expected: reaches IDLE quickly — recently-fetched symbols are skipped via freshness; only stale or eligible-failed symbols are processed.

**Failure handling**

17. Manually mark one symbol as FAILED via `mt data state` (or simulate via an invalid symbol in the registry):
    ```bash
    # example — exact syntax may differ
    mt data state set BADSYM --granularity minute --status failed --retry-count 0
    ```
    Observe the daemon retries with exponential backoff. After `max_retries` consecutive failures, symbol is excluded from the queue.

18. Observe UNFILLABLE handling (requires a symbol with history only beyond 24 months — or a test fixture):
    Expected: once the orchestrator marks a symbol UNFILLABLE, the daemon never attempts it again. Visible in `mt data minute status` under the Unfillable section.

**Regression check**

19. Run all existing commands:
    ```bash
    mt data daily update AAPL
    mt data daily status
    mt data minute update AAPL
    mt data minute update-all
    mt data state
    ```
    Expected: all succeed. No regressions.

20. Full test suite:
    ```bash
    pytest test/ -v
    ```
    Expected: all tests pass. Test count increases by the new daemon + status + work-queue + symbol-source tests.

**Rate-limit sequencing sanity check**

21. With both daemons running, observe request pacing over a 5-minute window (tail daemon logs or a Grafana panel if available). Expected: total AV requests ≤ 30/min aggregate, with the minute daemon capped at 25 rpm and daily contributing bursty low-volume traffic during its occasional cycles.

## Future Work

Explicitly out of scope for slice 125, but recorded here so the architectural direction is visible to reviewers and task authors:

### Calendar-Aware Freshness (follow-up slice)

The architecture document defines minute "caught up" as "watermark within 1 trading day of now." Slice 124 chose `MIN_DAYS=3` (calendar days) as an interim value that absorbs weekends without requiring calendar awareness. `TradingCalendar` already exists ([trading_calendar.py:142](src/manta_trading/data/base/trading_calendar.py#L142)) with `is_trading_day(date)` backed by `trading_calendars`/`trading_holidays` tables (slice 102). The follow-up slice would:

- Replace the calendar-day gap in `_is_fresh` (both `daily/freshness.py` and `minute/freshness.py`) with a "number of trading days since `last_success_ts`" computation that consults `TradingCalendar`.
- Drop `MIN_DAYS` to 1 trading day once the conversion is in place.
- Inject `TradingCalendar` through the orchestrator and daemon constructors (minimal plumbing — the calendar is read-only and thread-safe).
- Handle the early-close edge case explicitly (a symbol fetched after a 1 PM early close on Thursday should be fresh on Friday).

This is a focused refactor — likely a single small slice that touches both daily and minute orchestrators + daemons in one pass for consistency.

### Priority Tiers / Symbol-Universe Segmentation (follow-up slice)

To support patterns like "keep the S&P 500 fresh every trading day, fetch the Russell 2000 over weekends," the system needs a priority model on the instrument universe:

- Either a `priority` column on `instruments` or explicit membership tables (`sp500_members`, `russell2000_members`) on the instrument registry.
- Work-queue builder gains tier-aware ordering and tier-aware freshness thresholds (e.g. `MIN_TRADING_DAYS_FRESH=1` for tier-1, `=5` for tier-2).
- Daemon's "caught up" becomes scoped: the daemon may be caught up on tier-1 while still working tier-2. CLI status reflects per-tier state.
- Complements calendar-aware freshness but is an independent concern; prioritize after calendar-aware freshness lands so the tier logic uses the right gap metric.

This is likely a larger effort than the freshness slice — it introduces a new domain concept (tier membership) with its own data model, admin CLI, and observability surface.

### Framework Extraction (deferred per architecture)

When a third daemon appears (likely tick acquisition), the shared patterns from slices 123 and 125 (main loop, signal handling, heartbeat, interruptible sleep, work queue, retry backoff) become extraction candidates. The two existing work-queue builders (`build_work_queue` daily, `build_minute_work_queue`) become concrete inputs for designing a principled `WorkQueuePolicy` abstraction.

## Notes

- The daemon process is designed to run on .144 against the test DB initially. Production deployment follows once the PM confirms the external backup gate (per architecture doc line 22 — "irreplaceable historical minute data on .95/.144 backed up"). This gate is outside the slice's technical scope but is blocking for production rollout.
- `poll_interval` defaults to 3600 s (1 hour) to match the daily daemon. For the minute use case the tradeoff is different (markets close once; minute updates stream continuously during market hours), but the daemon's "caught up" behavior already means short polls are cheap — it just re-confirms freshness in milliseconds when no work is pending. Operators can tune down during testing.
- `MIN_DAYS=3` (from slice 124's `minute/freshness.py`) is an **interim** value. The architecture calls for freshness within 1 trading day; the calendar-aware follow-up slice (see Future Work) will tighten this. Until then, up to ~3 calendar days of staleness during the trading week is an accepted trade-off for avoiding weekend re-fetches.
- Manual rate-limit sequencing is a deliberate contract, not a workaround. It is documented here and in the CLI help text for `mt data minute daemon`. The operator is expected to: (1) start daily, (2) wait for daily to report IDLE, (3) start minute with `--requests-per-minute ≤ 25`. If daily is not yet IDLE when minute starts, the worst case is a brief burst that may hit the AV 30 rpm ceiling and surface as transient failures — the retry backoff handles these.
- The decision to fork the work-queue builder (rather than parameterize slice 123's) is intentional per the "defer abstraction until N=3" principle from the architecture doc. When a third daemon appears, the three work-queue variants become extraction inputs together — at that point a principled `WorkQueuePolicy` abstraction can be designed from evidence rather than guesswork.
- The `InstrumentRegistrySymbolSource` adapter is designed to be reusable; the daily daemon may adopt it in a future cleanup slice once the daily pipeline's symbol-list source is reviewed (currently uses `MarketDB.readLRUSymbolList`, which is fine for daily but philosophically misaligned with the instrument registry as source-of-truth).
