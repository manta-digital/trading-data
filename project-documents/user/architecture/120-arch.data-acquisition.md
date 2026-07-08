---
docType: architecture
component: data-acquisition
project: trading
parent: user/project-guides/001-initiative-plan.trading.md
dependencies:
  - 100-arch.data-storage
relatedSlices: []
riskLevel: high
archIndex: 120
dateCreated: 20260404
dateUpdated: 20260427
status: complete
---

# Data Acquisition Architecture

## Overview

Data Acquisition is the system that fetches market data from external providers, validates it, and writes it to the storage layer established in Initiative 100. It covers daily OHLCV, minute OHLCV, and (in later slices) tick-level data across multiple providers and instrument types.

**Scope:** Provider clients, acquisition daemon processes, ingestion orchestration, resume/recovery logic, rate limiting, and the CLI/daemon interfaces for controlling acquisition. **Includes (per slice 128, 2026-04-27)** an operational baseline of coverage detection and EOD cross-validation, scoped to single-pass single-provider operator-driven checks; analytical extensions (trending, dashboards, multi-provider) remain Initiative 140's responsibility. Does not include serving APIs (Initiative 160) or formalized event audit trails (Initiative 180) — though the daemon emits structured events that 140/180 can consume.

**Motivation:** The project has working but fragile acquisition code. The daily pipeline (`mt data daily update`) works for single symbols but batch runs break partway through and don't resume cleanly. The minute pipeline has an orchestration service (`HistoricalMinuteService`) with placeholder quality metrics and basic gap detection that doesn't use trading calendars. Past experience shows that `update-all` often fails mid-run due to provider timeouts, rate limit hits, or connection resets — and recovery requires manual intervention because the system doesn't track where it left off. Meanwhile, ~45M rows of irreplaceable minute data (beyond AlphaVantage's 2-year history window) sit without backup. This initiative makes acquisition reliable, resumable, and observable — starting with equities and AlphaVantage, the simplest case, and building toward futures and additional providers.

## Design Goals

- **Reliable unattended operation** — Acquisition runs as a long-running daemon on the database host, pulling data continuously without human intervention. When a provider returns an error, times out, or resets the connection, the daemon logs it, backs off, and resumes from where it left off. A run that fetches 500 symbols should not lose progress when symbol 347 fails.

- **Resumable by design** — Every acquisition run tracks its progress at the symbol+time-range granularity. If a daemon restarts (crash, host reboot, deployment), it picks up where it left off without re-fetching completed work. This is the single most important capability — it's what's broken today.

- **Observable and communicable** — The daemon exposes its state: what it's currently fetching, what's queued, what failed, how far behind it is. The CLI (`mt data`) can query this state. Structured event logs capture every fetch attempt, success, failure, and retry for downstream consumption by Initiatives 140 and 180.

- **Vertical-first: equities daily, then equities minute** — Rather than building horizontal infrastructure for all providers and granularities at once, get one complete workflow running well before adding complexity. Daily OHLCV from AlphaVantage is the simplest case (full history in one request per symbol, simple gap model). Minute OHLCV from AlphaVantage is the second (month-based pagination, 2-year history limit, intraday cutoff boundaries). Each must be solid before moving to the next.

- **Extensible to futures and new providers without redesign** — The daemon architecture and provider interface support adding Databento (futures ticks), flat file import, and other providers as new service instances. But the initial implementation is AlphaVantage equities only — futures-specific concerns (contract rolls, expiration, continuous contracts) inform the interface design but don't drive the initial build.

## Architectural Principles

- **Service-per-concern, not service-per-provider** — Separate daemon processes by workload characteristics: daily OHLCV acquisition is low-frequency batch work; minute OHLCV is higher-volume with pagination and gap-filling; tick ingestion (future) is high-throughput streaming. Each runs as an independent daemon process. A single provider (AlphaVantage) may be consumed by multiple services (daily service and minute service). This gives operational independence — the tick service can be scaled, restarted, or debugged without affecting daily/minute acquisition.

- **Progress is persisted, not inferred** — Don't reconstruct "where was I?" from the data itself. Maintain explicit acquisition state: per-symbol watermarks (last successfully fetched timestamp), run status (pending/in-progress/failed/complete), and failure context (error message, retry count, last attempt time). This state lives in the acquisition state tables on prod host. Resumption reads this state, not the data tables. Critically, **the write unit matches the checkpoint unit**: for minute data, each month-chunk is fetched → validated → written → watermark updated in sequence. If the daemon crashes after writing month 18 of 24, it resumes at month 19 — not month 1. The current `HistoricalMinuteService` gathers all months then writes once; this must change to per-chunk write-and-checkpoint.

- **Async fetch, sync store** — Provider communication (HTTP requests, WebSocket streams) is async. Data processing (validation, normalization) and storage writes (COPY, INSERT) are sync. The daemon's event loop manages concurrency for fetches; writes are sequential per symbol. This matches the existing codebase pattern and avoids async-in-sync antipatterns. Tick data may eventually need async writes, but that's a future concern.

- **Fail loud, retry smart** — Provider errors are logged with full context (symbol, time range, HTTP status, response body) and surfaced in daemon status. Retries use exponential backoff with jitter, capped at a configurable maximum. After max retries, the symbol is marked failed with context and the daemon moves to the next item. No silent fallbacks, no swallowed exceptions, no "try again in 30 minutes" loops without visibility.

- **CLI is the baseline, daemon is the target** — Every acquisition operation must work as a one-shot CLI command (`mt data daily update SYMBOL`). The daemon composes these operations with scheduling, progress tracking, and communication. Critically, **CLI and daemon share the same orchestrator and state**: `mt data daily update SYMBOL` calls the same fetch→validate→write→update-watermark code path the daemon uses. Both update acquisition state. The daemon is just "run the orchestrator in a loop"; the CLI is "run it once and exit." This means one code path to test, no state divergence between CLI and daemon, and the daemon always sees what a manual CLI run did.

- **Reuse what 900/100 built, rewrite what they didn't touch** — `MarketDB`, `TimescaleMinuteDataDB`, `AlphaVantageMinuteProvider`, `DataProcessor`, the provider protocol, chunking strategies, and rate limiting are modern (psycopg3, httpx, structured logging). `marketservice.py` (camelCase, sync/async violations) and `HistoricalMinuteService` (placeholder metrics) are legacy wrappers that need replacement, not patching.

## Current State

**What works today:**

- `mt data daily update SYMBOL` fetches daily OHLCV from AlphaVantage and writes to MarketDB. Single-symbol operation is reliable. `update-all` iterates the symbol list but has no progress tracking — if it fails at symbol N, restarting begins from symbol 1.

- `mt data daily symbols` updates the master symbol list from AlphaVantage. Functional.

- `mt data daily coverage` reports per-symbol date ranges and staleness. Functional.

- `AlphaVantageMinuteProvider` fetches minute data with month-based pagination and rate limiting (30 req/min premium tier). Modernized to httpx in Initiative 900.

- `TimescaleMinuteDataDB` writes minute data via COPY protocol at high throughput. Gap detection exists (calendar-day gaps > 3 days, per-day bar counts). Modernized to psycopg3 in Initiative 100.

- `DataProcessor` validates OHLCV consistency and classifies sessions (RTH/ETH). Modern code.

- Rate limiting (`RateLimiter`) uses async token bucket, precise enforcement. Modern code.

- `ChunkingStrategy` framework with intelligent gap-based fetch planning. Modern code.

**What's broken or missing:**

- **No resume capability.** Neither daily nor minute batch runs track per-symbol progress. A failed run restarts from scratch. This is the primary operational pain point.

- **`marketservice.py` is legacy.** CamelCase naming, async methods wrapping sync DB calls, ad-hoc error handling with string matching, print statements. This is the daily acquisition orchestrator and needs replacement.

- **`HistoricalMinuteService` has placeholder metrics.** Completeness score is hardcoded `min(1.0, total_rows / 10000)`. Gap detection doesn't use trading calendars for expected bar counts. The service structure (provider + storage + registry + calendar injection) is sound; the implementation is incomplete.

- **No daemon capability.** All acquisition is CLI-invoked, foreground, single-run. No way to run continuously, no state exposure, no external communication.

- **No structured acquisition events.** Fetch attempts, successes, and failures are logged but not captured as queryable events. No way to answer "what did the system do last night?" without reading log files.

- **AlphaVantage minute data has a 2-year history limit.** Data older than 2 years cannot be re-fetched. ~45M rows of existing minute data on the production databases are irreplaceable. Backup is a prerequisite before any minute pipeline changes. (PM is handling backup separately.)

- **`AlphaVantageMinuteProvider` month pagination is scaffolded but not wired.** The provider calculates month ranges and iterates them, but `_fetch_month()` never passes the `month=YYYY-MM` parameter to the API. Every request returns the same most-recent trailing data regardless of which month was requested. This is a fix (add the `month` parameter to request params, explicitly set `extended_hours=true`, correct docstrings that reference CSV format when the code uses JSON), not a full rewrite — the provider structure, rate limiting, and response parsing are sound.

- **AlphaVantage minute data volume per request is limited and variable.** With `outputsize=full` and `month` specified, the API nominally returns "the full intraday data for a specific month" but in practice returns approximately 10 trading days (~1000 data points with extended hours), not a full calendar month. The exact cutoff is unspecified by AlphaVantage and should not be relied upon. This means a single month may require multiple requests if we need complete coverage, and gap detection must account for partial-month responses with intraday cutoff boundaries.

- **No daily OHLCV provider abstraction.** `marketservice.py` calls AlphaVantage directly with no provider interface. The minute pipeline has `IMinuteDataProvider`; the daily pipeline has nothing equivalent.

## Envisioned State

A set of independent daemon processes, one per acquisition concern, running on the database host:

**Daily Acquisition Daemon** — Continuously cycles through the equity symbol universe, fetching daily OHLCV from AlphaVantage. Tracks per-symbol watermarks (last fetched date). On each cycle: identifies stale symbols, fetches updates, writes to MarketDB, records success/failure. Respects rate limits. Responds to CLI queries about status and progress. Initial provider: AlphaVantage. Low resource usage — this is a slow, steady background process. **Caught-up definition:** every active (non-delisted) symbol has data within 2 trading days of today. Once caught up, the daemon sleeps with a configurable poll interval (e.g., 1 hour) and wakes to check for newly stale symbols. Delisted symbols are excluded from staleness checks.

**Minute Acquisition Daemon** — Continuously fills gaps in minute OHLCV data. Maintains per-symbol acquisition state: last successful fetch timestamp, pending time ranges, retry state. On each cycle: identifies symbols with gaps or missing recent data, plans fetch operations (using chunking strategies), executes with rate limiting and retry, writes via COPY per month-chunk, updates watermarks after each chunk. Handles AlphaVantage's partial-response behavior (intraday cutoffs). Higher resource usage than daily due to volume and pagination. Initial provider: AlphaVantage. **Caught-up definition:** every active symbol's watermark is within 1 trading day of now, and no known fillable gaps exist older than the watermark. Gaps older than AlphaVantage's ~2-year history window are logged as permanently unfillable and excluded from the work queue — the daemon does not retry them. Once caught up, the daemon sleeps with a configurable poll interval and wakes to check for new data availability.

**Tick Acquisition Daemon (future)** — Separate process for tick-level data from Databento or similar providers. Different operational profile: streaming rather than polling, higher throughput, potentially different host. Architecture accommodates this as a third daemon instance using the same patterns (progress tracking, event emission, CLI queryability) but with streaming-specific internals. Not built in the initial slices.

**Shared infrastructure across daemons:**

- **Acquisition state tables** — Database tables on prod tracking per-symbol, per-granularity watermarks and run state. Schema: `(symbol, granularity, provider, last_success_ts, last_attempt_ts, status, error_message, retry_count, run_id)`. Primary key: `(symbol, granularity, provider)` — one row per symbol-granularity-provider triplet. State writes use UPSERT (`ON CONFLICT ... DO UPDATE`) so that both daemon cycles and CLI runs update the same row. This is what makes resumption work.

- **Provider interface layer** — `IDailyDataProvider` (new, modeled on existing `IMinuteDataProvider`), plus the existing minute and future tick provider interfaces. Provider implementations handle auth, rate limiting, pagination, and format conversion. The daemon doesn't know provider internals.

- **Daemon framework** — Shared patterns for: main loop with graceful shutdown (SIGTERM/SIGINT), progress state management, rate limit coordination, structured event emission, health endpoint or status file for CLI queries. Not a heavyweight framework — a small set of base classes or mixins that each daemon composes.

- **CLI integration** — `mt data daily status`, `mt data minute status` query daemon state. `mt data daily update SYMBOL` works as before (one-shot, no daemon required). `mt data daemon start daily` / `mt data daemon stop daily` manage daemon lifecycle if we go that route, or daemons run as systemd services managed externally.

- **Structured acquisition events** — Every fetch attempt produces a structured event (JSON or database row): `{run_id, symbol, granularity, provider, action, status, rows_fetched, time_range, duration_ms, error, timestamp}`. Written to a local event store (database table or append-only file). Initiative 180 may formalize this further, but the daemon starts emitting events from day one.

## Technical Considerations

- **Daemon communication pattern** — The daemon needs to be queryable (status, progress, current work) and controllable (pause, resume, re-prioritize). Options: Unix socket with a simple protocol, HTTP endpoint (lightweight, e.g. a single-route ASGI app), shared database state that CLI reads, or a status file (simplest). The database-state approach is most natural since we already have acquisition state tables — the CLI reads them directly. Daemon writes state; CLI reads it. No IPC protocol needed initially. A health/heartbeat row or file confirms the daemon is alive.

- **Concurrency within a daemon** — A single daemon may want to fetch multiple symbols concurrently (especially minute data where each symbol requires multiple paginated requests). The async fetch layer supports this naturally with `asyncio.gather` or a semaphore-bounded task pool. Writes remain sequential per symbol. The concurrency level is configurable and should default conservatively (e.g., 3 concurrent symbols for minute data) since the rate limiter is the true bottleneck.

- **AlphaVantage rate limit as the binding constraint** — At 30 requests/minute (premium tier) and ~1000 data points per request for minute data, catching up a single symbol's 2-year history takes many requests. For a universe of 500+ symbols, initial catch-up is measured in days. The daemon must handle this gracefully: prioritize symbols by staleness, make steady progress, and not thrash between symbols. The daily endpoint is simpler (full history in one request) but the same rate limit applies across all AlphaVantage endpoints — the daemons must coordinate or share a rate limit budget.

- **Shared rate limit across daemons** — If the daily and minute daemons both hit AlphaVantage, they share the same API key and rate limit. Practical approach: let the daily daemon run first with the full rate budget — it catches up fast (one request = full history per symbol). Once daily is current, it needs very few requests (one per symbol per day, and AlphaVantage may support multi-symbol daily requests — to be verified). At that point, the minute daemon gets most of the budget. This is simpler than a shared rate limiter and matches the operational reality: daily catch-up is hours, minute catch-up is days. Note: the AlphaVantage account is a legacy plan with favorable pricing that is no longer offered — avoid any account changes that might trigger a plan migration.

- **Acquisition state lives on prod server (TimescaleDB host)** — All acquisition state (watermarks, run status, error tracking) is centralized on the TimescaleDB host, regardless of where the data itself is written. This gives the daemon and CLI a single source of truth. For minute data (also on prod), state and data updates can share a transaction. For daily data (currently on temp-test), the write to temp-test completes first, then the state update on prod-minute follows — if the daemon crashes between these, the next run re-fetches and the idempotent write (`ON CONFLICT DO NOTHING`) is harmless. Daily data may move to prod server eventually, which would eliminate this gap entirely.

- **Graceful shutdown and resume** — On SIGTERM, the daemon must finish its current fetch (or abandon it cleanly), persist state, and exit. On restart, it reads persisted state and resumes. For minute data, state updates happen per-month-chunk within the same transaction as the data write. For daily data, the at-most-once-extra-fetch pattern (described above) provides safe recovery without cross-host transactions.

- **`marketservice.py` replacement** — This module is the daily acquisition orchestrator and is legacy (camelCase, sync/async violations, string-based error matching). It should be replaced, not patched. The replacement is effectively the daily acquisition daemon's core loop: iterate symbols, fetch via provider, write via `MarketDB`, track progress. The existing `MarketDB` read/write methods are solid and stay.

- **Provider error taxonomy** — AlphaVantage returns errors in several forms: HTTP errors, JSON `"Error Message"` field, `"Note"` field (rate limit), `"Information"` field, empty responses, and connection timeouts. The existing `AlphaVantageMinuteProvider.validate_response()` handles these. The retry strategy should distinguish transient errors (timeout, rate limit, 5xx) from permanent errors (invalid symbol, API key issue) — transient errors retry with backoff, permanent errors mark the symbol as failed immediately.

- **Existing data at risk** — ~45M rows of minute OHLCV on production databases represent data beyond AlphaVantage's 2-year retrieval window. PM is handling backup separately. No minute pipeline changes should be deployed to production until backup is confirmed. This is an operational constraint, not an architectural one, but the architecture should note it.

- **Testing strategy** — Unit tests mock the provider HTTP layer (httpx mock transport) and use in-memory state. Integration tests run against a real database (skip when unavailable, per existing pattern). The daemon loop itself should be testable with a fake provider and fake storage — the "run one cycle" operation is a pure function of (current state, provider results) → (new state, writes).

## Anticipated Slices

- **Daily provider interface and orchestrator** — Create `IDailyDataProvider`, implement `AlphaVantageDailyProvider`, replace `marketservice.py` with a clean daily acquisition orchestrator. Wire into existing CLI commands. Add acquisition state tracking (per-symbol watermarks). This is the simplest end-to-end path: one request per symbol, straightforward gap model.

- **Daily acquisition daemon** — Wrap the daily orchestrator in a daemon process. Continuous symbol cycling, progress persistence, graceful shutdown, structured event emission, CLI status query. This is where "just works unattended" gets proven on the easy case.

- **Minute orchestrator hardening** — Replace `HistoricalMinuteService` placeholder logic with real gap detection (calendar-aware), proper progress tracking, and resume capability. Harden `AlphaVantageMinuteProvider` for unattended operation (intraday cutoff handling, partial-response recovery). Wire acquisition state tracking.

- **Minute acquisition daemon** — Wrap the minute orchestrator in a daemon process. Same patterns as daily daemon but with pagination, higher concurrency, and more complex gap model. This is the most operationally demanding equities slice.

- **Daemon framework extraction (deferred)** — Not built as part of the initial equities slices. Once a third daemon is added (tick acquisition), extract shared patterns from the daily and minute daemons into reusable components. Refactor candidates will live in the daily and minute daemon modules created in slices 123 and 125 — main loop, graceful shutdown handling, acquisition state management, structured event emission, and CLI status query support. Until then, accept some duplication between the two daemons rather than build a framework speculatively.

- **Futures and additional providers (later slices)** — Databento tick provider, flat file import provider, futures instrument support. These slices extend the provider interface and add a tick acquisition daemon. Sequenced after equities daily+minute are solid.

## Related Work

- **100-arch.data-storage** (complete) — Provides the storage layer this initiative writes to: `MarketDB` (daily OHLCV), `TimescaleMinuteDataDB` (minute OHLCV), `tick_events` hypertable (tick data, future), instrument registry, trading calendars.
- **900-arch.foundation-cleanup** (complete) — Provides CLI framework (Typer), config system (`Settings`), structured logging, provider registry with enums.
- **Archived 050-arch.data-storage-and-acquisition** — Contains prior design for provider abstraction, data flow (async orchestrator → provider → processor → storage), service internal architecture with explicit async/sync boundaries. Informs this architecture.
- **Existing modules (reuse):**
  - `src/manta_trading/market/marketdb.py` — Daily OHLCV storage, psycopg3, solid
  - `src/manta_trading/market/timescale_minute_db.py` — Minute OHLCV storage, psycopg3 + COPY, solid
  - `src/manta_trading/data/historical_minute/providers/alphavantage.py` — Minute provider, httpx, structure sound but month pagination not wired (fix, not rewrite)
  - `src/manta_trading/data/historical_minute/processor.py` — OHLCV validation + session classification, modern
  - `src/manta_trading/data/historical_minute/provider.py` — `IMinuteDataProvider` protocol, modern
  - `src/manta_trading/market/chunking_strategy.py` — Fetch planning strategies, modern
  - `src/manta_trading/util/ratelimiter.py` — Async token bucket, holds lock during sleep (fix needed for concurrent symbol fetches)
- **Existing modules (replace):**
  - `src/manta_trading/market/marketservice.py` — Legacy daily orchestrator (camelCase, sync/async violations)
  - `src/manta_trading/data/historical_minute/service.py` — Minute orchestrator with placeholder metrics
- **Initiative 140 (Data Quality)** — Will consume acquisition events and provide cross-validation, calendar-aware gap analysis, and coverage reporting. Boundary: 120 does operational gap handling (fetch what's needed); 140 does analytical gap handling (is our data correct). **Boundary refinement (slice 128, 2026-04-27):** the original boundary placed all coverage reporting and cross-validation in 140. Slice 128 ships an *operational baseline* of these capabilities — the `mt data minute coverage` CLI, the `coverage_gaps` table, and the `verify-against-eodhd-eod` Stage B verifier — because production cutover cannot proceed without them (Stage A is structurally blind to provider data gaps; the EODHD NVDA-2024 gap discovery proved this). 120 now owns: minimum-viable detection (single-pass, single-provider, single-symbol-at-a-time CLI commands and the persisted gap log). 140 still owns: analytical extensions (historical gap-rate trending, cross-provider validation, dashboards, automated triage workflows, multi-source ground-truth comparison). The 120 artifacts are designed for 140 absorption — schema and CLI shape do not need to change at handover. See [user/slices/128-slice.eodhd-catchup-and-production-cutover.md](../slices/128-slice.eodhd-catchup-and-production-cutover.md) Decision 15 for the handoff plan.
- **Initiative 180 (Event Infrastructure)** — Will formalize the structured event logs that acquisition daemons emit. The daemon starts emitting events in a simple format; 180 may standardize schema and add persistence/querying.
git sta