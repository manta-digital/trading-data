---
docType: slice-design
slice: daily-provider-interface-and-alphavantage-daily-provider
project: trading
parent: user/architecture/120-slices.data-acquisition.md
dependencies: [100, 121, 900]
interfaces: [IDailyDataProvider]
dateCreated: 20260411
dateUpdated: 20260411
status: complete
---

# Slice Design: Daily Provider Interface and AlphaVantage Daily Provider

## Overview

First end-to-end use of the slice 121 orchestrator core. This slice:

1. Defines `IDailyDataProvider` — the daily analog of the existing `IMinuteDataProvider` protocol.
2. Implements `AlphaVantageDailyProvider` against that interface, calling `TIME_SERIES_DAILY_ADJUSTED` with the same `httpx` + `RateLimiter` patterns used by the minute provider.
3. Replaces the legacy `marketservice.py` daily orchestrator with a new `DailyAcquisitionOrchestrator` module that composes slice 121's `run_acquisition_unit` with a `MarketDB`-backed `ChunkWriter` and the new provider.
4. Rewires the existing `mt data daily update`, `mt data daily update-all`, and `mt data daily update-file` CLI commands onto the new orchestrator so every invocation updates `acquisition_state` and resumes correctly on failure.

Daily is the simplest case — a single fetch covers the full history for a symbol, the gap model is "any gap > threshold ⇒ full; otherwise compact", and a daily "chunk" is just a single fetch per symbol. This makes it the right place to prove the orchestrator pattern end-to-end before minute data (slice 124) introduces pagination complications.

**Out of scope:** any daemon loop (slice 123), any change to minute acquisition (slice 124/125), any new storage schema changes (slice 100/121 already provide what is needed), and `marketservice.py` itself beyond the call sites the CLI touches — the file may remain on disk alongside the new orchestrator until slice 123 removes it.

## Value

**Operator-facing:** `mt data daily update-all` becomes resumable. A run that fails at symbol N leaves `acquisition_state` with N-1 rows marked `ok` plus symbol N marked `failed` with context. The next invocation skips the `ok` rows (based on last-success staleness, same gap logic as today) and retries `failed`/`pending`. Symbol-level progress survives crashes, Ctrl-C, and host reboots — today it does not.

**Developer-facing:** The daily fetch path becomes a provider + a writer plus ~20 lines of orchestration glue. Adding a second daily provider (e.g. Polygon) in a later slice is "implement `IDailyDataProvider`, register a profile, pass it to the orchestrator" — no new loop.

**Architectural:** Proves slice 121's `ChunkProvider`/`ChunkWriter` interfaces are actually usable. If the daily implementation needs to bend the protocols, that is a design signal to fix here rather than discover under pressure in slice 124.

## Technical Scope

### In Scope

- New `IDailyDataProvider` Protocol (matches the shape of `IMinuteDataProvider`, but narrower — daily has no month pagination).
- New `AlphaVantageDailyProvider` implementation calling `TIME_SERIES_DAILY_ADJUSTED`.
- New `MarketDBDailyWriter` implementing slice 121's `ChunkWriter` protocol on top of `MarketDB.writeDailyOHLCVAdjusted`.
- New `DailyAcquisitionOrchestrator` module/class that composes provider + writer + `run_acquisition_unit` for one symbol, and a thin `run_daily_update_all` helper that iterates a symbol list and invokes the single-symbol path per symbol (bypassing full orchestrator re-entry overhead only if measurement shows it matters).
- Rewire `mt data daily update`, `update-all`, and `update-file` CLI commands to the new orchestrator. Remove the `MarketService` construction from these command bodies; the new orchestrator is created directly.
- `update-all` and `update-file` are resumable across invocations: before fetching, skip symbols whose `acquisition_state` row is `ok` and whose `last_success_ts` places them inside the existing staleness threshold (same thresholds as the current gap logic).
- Integration test that simulates a failure mid-run and verifies that the next invocation picks up at the failed symbol.
- Unit tests for `AlphaVantageDailyProvider` using `httpx.MockTransport` or a stub client (no real network calls).
- Unit tests for `MarketDBDailyWriter` using a fake/mocked `MarketDB` (the real DB is exercised by the integration test only).
- Unit tests for `DailyAcquisitionOrchestrator` confirming it correctly assembles `run_acquisition_unit` (one chunk per symbol, watermark advances to the last row date, failures propagate via `AcquisitionStatus`).

### Out of Scope

- Any modification to the minute pipeline.
- Any daemon loop or long-running process.
- New acquisition-state columns or migrations (slice 121 schema is sufficient).
- Replacing `MarketDB.writeDailyOHLCVAdjusted` — the existing method already performs an idempotent `INSERT ... ON CONFLICT DO NOTHING`, which is exactly what the writer needs.
- Deleting `marketservice.py` itself — this slice stops the CLI from using it but leaves the file in place. Removal happens alongside the daemon slice (123) or in a later cleanup pass so the two changes don't get tangled.
- Any provider registry changes — `ProviderType.ALPHA_VANTAGE` already exists and is the canonical provider identifier.
- Any changes to `mt data daily symbols` or `mt data daily coverage` — these commands stay as-is.

## Interface Design

### `IDailyDataProvider` Protocol

```python
# src/manta_trading/data/acquisition/daily/provider.py

from datetime import date
from typing import Protocol
import pandas as pd

class IDailyDataProvider(Protocol):
    """Fetch daily OHLCV bars for a single symbol from an external source."""

    async def fetch_daily_ohlcv(
        self,
        symbol: str,
        *,
        output_size: str,  # "compact" | "full", provider-specific meaning
    ) -> pd.DataFrame:
        """Return a DataFrame indexed by trading date.

        Columns (canonical, required):
            open, high, low, close, adjusted_close, volume,
            dividend_amount, split_coefficient

        Must be sorted ascending by date, duplicate-free. Raises on transport
        or validation errors — the orchestrator catches and records them.
        """
        ...

    def validate_response(self, raw_data: dict) -> ValidationResult:
        """Inspect a raw provider payload for rate-limit / error messages."""
        ...

    def get_rate_limits(self) -> RateLimitInfo:
        """Static rate-limit description for logging and daemon planning."""
        ...
```

Notes:

- Only `fetch_daily_ohlcv` is required by the orchestrator path. `validate_response` and `get_rate_limits` mirror `IMinuteDataProvider` so the two protocols feel the same and a future daemon can introspect them identically.
- The canonical DataFrame schema matches what `MarketDB.writeDailyOHLCVAdjusted` already expects, so the writer does not have to reshape anything.
- Output size is a keyword argument because AlphaVantage is the only current implementation and the semantics ("compact" = 100 days, "full" = 20+ years) are provider-specific. A future provider that does not honor those strings will ignore the parameter.
- `ValidationResult` and `RateLimitInfo` are reused from `manta_trading.data.historical_minute.provider` — there is no reason to duplicate them. If the module placement feels wrong during implementation, lift both dataclasses into `manta_trading.data.acquisition.types` and re-export from the minute provider module.

### `AlphaVantageDailyProvider`

```python
# src/manta_trading/data/acquisition/daily/providers/alphavantage.py

class AlphaVantageDailyProvider:
    """IDailyDataProvider implementation backed by AlphaVantage TIME_SERIES_DAILY_ADJUSTED."""

    def __init__(
        self,
        api_key: str,
        *,
        requests_per_minute: int = 30,
        rate_limiter: RateLimiter | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None: ...

    async def fetch_daily_ohlcv(
        self, symbol: str, *, output_size: str = "compact"
    ) -> pd.DataFrame: ...

    def validate_response(self, raw_data: dict) -> ValidationResult: ...

    def get_rate_limits(self) -> RateLimitInfo: ...

    async def aclose(self) -> None: ...
```

Implementation notes:

- Uses `httpx.AsyncClient` directly, lazy-initialized on first call and closed via `aclose()`. Pattern mirrors `AlphaVantageMinuteProvider` for consistency.
- Takes an optional injected `RateLimiter` so the daily orchestrator and minute orchestrator can share a single 30 req/min budget when both run in the same process (important for slice 123/125 coexistence, but needed here so the interface does not get refactored twice).
- Shape of the request params is identical to the existing `AlphavantageAPI._getDailyOHLCV` call (see [alphavantageapi.py:206](src/manta_trading/api/alphavantage/alphavantageapi.py#L206)) — `function=TIME_SERIES_DAILY_ADJUSTED`, `symbol`, `outputsize`, `apikey`. Column renaming (`['open', 'high', 'low', 'close', 'adjusted_close', 'volume', 'dividend_amount', 'split_coefficient']`) is lifted verbatim.
- `validate_response` mirrors `AlphaVantageMinuteProvider.validate_response` — checks `Error Message`, `Note` (rate limit), `Information`, and "Time Series (Daily)" key presence. Empty series is treated as an error, not a warning: we do not want to silently write nothing and mark the symbol `ok`.
- Does **not** depend on `AlphavantageAPI` or `MarketService`. The existing `AlphavantageAPI` class is reserved for legacy callers (news, symbol listing) until slice 123 or later — the daily provider is a clean replacement for just the OHLCV path.

### `MarketDBDailyWriter`

```python
# src/manta_trading/data/acquisition/daily/writer.py

@dataclass
class MarketDBDailyWriter:
    """ChunkWriter adapter over MarketDB.writeDailyOHLCVAdjusted."""
    db: MarketDB
    symbol: str  # bound per-work-item; orchestrator constructs one per symbol

    def write(self, chunk: FetchedChunk) -> ChunkResult:
        # chunk.rows is a pandas DataFrame with the canonical daily columns
        df = chunk.rows
        if df is None or df.empty:
            return ChunkResult(last_written_ts=None, rows_written=0)
        ok = self.db.writeDailyOHLCVAdjusted(self.symbol, df)
        if not ok:
            raise RuntimeError(f"MarketDB write failed for {self.symbol}")
        last_ts = _max_trading_date(df)  # datetime at 00:00 UTC for PK consistency
        return ChunkResult(last_written_ts=last_ts, rows_written=len(df))
```

Notes:

- `writeDailyOHLCVAdjusted` already returns `bool` and the orchestrator protocol expects the writer to raise on failure. The wrapper converts `False` into an exception so `run_acquisition_unit` records it as a `CHUNK_FAILED` event rather than silently marking the symbol `ok`.
- `last_written_ts` is the maximum trading date in the DataFrame, normalized to midnight UTC. Daily data has day resolution; there is no sub-day timestamp to preserve. The orchestrator stores this as the watermark, and subsequent gap checks (`last_updated_day`) read from it.
- One writer instance per symbol per orchestration call — it is a cheap dataclass, nothing to pool.

### `DailyAcquisitionOrchestrator`

```python
# src/manta_trading/data/acquisition/daily/orchestrator.py

class DailyAcquisitionOrchestrator:
    """Composes IDailyDataProvider + MarketDBDailyWriter + run_acquisition_unit.

    One instance per CLI invocation. Reuses the state repo, event sink, and
    httpx client across many symbols.
    """

    def __init__(
        self,
        provider: IDailyDataProvider,
        db: MarketDB,
        state_repo: AcquisitionStateRepository,
        event_sink: EventSink,
        *,
        recent_days_threshold: int = 100,
        provider_id: str = ProviderType.ALPHA_VANTAGE.value,
    ) -> None: ...

    async def update_symbol(
        self, symbol: str, *, output_size: str | None = None, run_id: UUID
    ) -> AcquisitionResult:
        """Update one symbol through the orchestrator core."""
        ...

    async def update_symbols(
        self,
        symbols: list[str],
        *,
        output_size: str | None = None,
        run_id: UUID,
        skip_recent: bool = True,
    ) -> BatchResult:
        """Update many symbols, honoring acquisition_state for resume."""
        ...
```

#### Per-symbol flow

1. Determine `output_size` if not supplied: read `last_success_ts` from `acquisition_state` (preferred) or fall back to `MarketDB.readLastUpdatedDay` for compatibility with symbols that have data but no state row yet.
2. Build a `WorkItem(symbol, Granularity.DAILY, provider_id, time_range_start=epoch, time_range_end=utcnow())`.
3. Build a single-chunk `AlphaVantageDailyChunkProvider` adapter — a thin `ChunkProvider` wrapping the daily fetch into an async generator that yields exactly one `FetchedChunk`. (The adapter is an implementation detail; it lives next to the orchestrator and is not part of the public interface.)
4. Build a `MarketDBDailyWriter(db, symbol)`.
5. Call `run_acquisition_unit(...)` and return the result.

This is the key composition: one symbol = one chunk. The orchestrator core handles the state lifecycle (pending → in_progress → ok/failed), event emission, and checkpointing. The daily code above is the glue.

#### Batch flow (`update_symbols`)

```
for symbol in symbols:
    if skip_recent:
        existing = state_repo.get(symbol, Granularity.DAILY, provider_id)
        if existing and existing.status == AcquisitionStatus.OK:
            if _is_fresh(existing.last_success_ts, recent_days_threshold=MIN_DAYS):
                skipped += 1; continue
    result = await update_symbol(symbol, run_id=run_id)
    # tally success/failure/no-data
await asyncio.sleep_per_symbol(0.0)  # rate limiter handles pacing
return BatchResult(success=..., failed=..., skipped=..., no_data=...)
```

Critical resume property: because `acquisition_state` is persisted per chunk (which for daily is per symbol), a crashed `update-all` can be restarted and it will skip everything it successfully finished. Failed and untouched symbols retry. This is the first slice where that property is user-visible, and the integration test pins it.

#### Freshness / gap logic

Pulled out of `marketservice.py` into a tiny helper:

```python
def _resolve_output_size(
    last_success_ts: datetime | None,
    *,
    recent_days: int = 100,
) -> str:
    if last_success_ts is None:
        return "full"
    gap = (date.today() - last_success_ts.date()).days
    return "compact" if gap <= recent_days else "full"
```

The `MIN_DAYS=2` "skip if fresher than 2 days" shortcut from legacy `getOutputSizeFromLastUpdatedDay` is preserved as the skip rule in the batch path — the orchestrator simply does not call `update_symbol` for symbols inside that window. Do not move this into the per-symbol path; the core orchestrator stays unaware of freshness.

## Data Flow

```
mt data daily update SYMBOL
            │
            ▼
CLI command handler (data.py)
  • resolve credentials / settings
  • build MarketDB, psycopg pool (ConnectionPool)
  • build AcquisitionStateRepository, JsonlEventSink
  • build AlphaVantageDailyProvider
  • build DailyAcquisitionOrchestrator
  • call orchestrator.update_symbol(symbol, run_id=uuid4())
            │
            ▼
DailyAcquisitionOrchestrator.update_symbol
  • resolve output_size
  • build WorkItem, chunk provider adapter, writer
  • await run_acquisition_unit(...)          ← slice 121
            │
            ▼
run_acquisition_unit (slice 121 core)
  • mark acquisition_state IN_PROGRESS
  • emit RUN_STARTED
  • for each chunk in provider.fetch_chunks(work_item):
      - writer.write(chunk) in asyncio.to_thread
      - on success: upsert OK + last_success_ts, emit CHUNK_OK
      - on failure: upsert FAILED + error, emit CHUNK_FAILED, break
  • emit RUN_FINISHED
  • return AcquisitionResult
            │
            ▼
Back to CLI: render summary table (symbol, chunks_written, status, error)
```

For `update-all`:

- The batch loop is sequential (one symbol at a time). The `RateLimiter` inside the provider handles pacing at 30 req/min. Concurrency is deliberately deferred — daily catches up fast enough that a concurrent loop is not needed until the daemon slice.
- Before each symbol the loop consults `acquisition_state`. Skips and retries are logged, not silent.
- The CLI prints a final summary: N succeeded, N failed, N skipped, N no-data.

## Cross-Slice Dependencies and Interfaces

**Depends on:**

- Slice 121 — `run_acquisition_unit`, `WorkItem`, `FetchedChunk`, `ChunkResult`, `ChunkProvider`, `ChunkWriter`, `AcquisitionStateRepository`, `Granularity.DAILY`, `AcquisitionStatus`, `JsonlEventSink`, `NullEventSink`. This slice is the first real consumer.
- Slice 100 — `MarketDB` (daily OHLCV storage, untouched).
- Slice 900 — `ProviderType.ALPHA_VANTAGE`, `Settings.alphavantage_api_key`, CLI scaffold, Rich output, structured logging.

**Provides for:**

- Slice 123 (daily daemon) — imports `DailyAcquisitionOrchestrator.update_symbol` / `update_symbols` and calls it in a loop.
- Slice 124 (minute orchestrator hardening) — reuses `IDailyDataProvider` as a pattern, not directly. It will need the same `ChunkProvider` adapter trick but for multi-chunk (per-month) fetches.
- Future provider slices (Polygon, etc.) — implement `IDailyDataProvider`, plug into the same orchestrator.

**Interface stability:** `IDailyDataProvider.fetch_daily_ohlcv` is the primary contract. Slice 123 may add a cancellation token or a structured context argument when daemon needs arise, but the shape is expected to stay.

## File Layout

New files:

- `src/manta_trading/data/acquisition/daily/__init__.py`
- `src/manta_trading/data/acquisition/daily/provider.py` — `IDailyDataProvider` Protocol, re-exports of `RateLimitInfo`/`ValidationResult`
- `src/manta_trading/data/acquisition/daily/providers/__init__.py`
- `src/manta_trading/data/acquisition/daily/providers/alphavantage.py` — `AlphaVantageDailyProvider`
- `src/manta_trading/data/acquisition/daily/writer.py` — `MarketDBDailyWriter`
- `src/manta_trading/data/acquisition/daily/orchestrator.py` — `DailyAcquisitionOrchestrator`, `BatchResult`, internal `_DailyChunkProviderAdapter`
- `src/manta_trading/data/acquisition/daily/freshness.py` — `_resolve_output_size`, `_is_fresh`, `MIN_DAYS`, `RECENT_DAYS` constants (single source of truth, referenced by orchestrator and tests)
- `test/unit/data/acquisition/daily/__init__.py`
- `test/unit/data/acquisition/daily/test_provider_alphavantage.py`
- `test/unit/data/acquisition/daily/test_writer.py`
- `test/unit/data/acquisition/daily/test_orchestrator.py`
- `test/integration/data/acquisition/daily/test_daily_update_resume.py` — requires `MT_TIMESCALE_DB_URL` and `MT_MARKET_DB_URL`, skipped otherwise

Modified files:

- `src/manta_trading/cli/commands/data.py` — `daily_update`, `daily_update_all`, `daily_update_file` command bodies rewritten to construct and invoke `DailyAcquisitionOrchestrator`. `_create_market_service` helper removed (or kept only for `daily_symbols`, which still calls `MarketService.updateSymbolList`).

Not touched:

- `src/manta_trading/market/marketservice.py` — still imported by `daily_symbols` for `updateSymbolList`. Left in place until a later slice removes it.
- `src/manta_trading/api/alphavantage/alphavantageapi.py` — still used by `marketservice.py` for symbol listing and news. Untouched.
- `src/manta_trading/data/historical_minute/*` — untouched.

## CLI Behavior Changes

User-visible behavior after this slice:

| Command | Before | After |
|---|---|---|
| `mt data daily update SYMBOL` | Fetches via `MarketService.updateDailyOHLCVSimpleGap`, no state row written | Fetches via `DailyAcquisitionOrchestrator`, `acquisition_state` row upserted (`in_progress` → `ok`/`failed`) |
| `mt data daily update-all` | Iterates `readLRUSymbolList`, resumes from symbol 1 on restart | Iterates `readLRUSymbolList`, **skips symbols with fresh `ok` state**, retries `failed`/`pending`, per-symbol rows in `acquisition_state` |
| `mt data daily update-file PATH` | Reads file, iterates same as `update-all` | Same — plus state tracking |
| `mt data daily symbols` | Unchanged | Unchanged (still `MarketService.updateSymbolList`) |
| `mt data daily coverage` | Unchanged | Unchanged |
| `mt data daily migrate` | Unchanged | Unchanged |
| `mt data state --granularity daily` | Returned empty | Returns the rows this slice writes |

New output: a short summary line at the end of `update-all` — `"{ok} succeeded, {failed} failed, {skipped} skipped, {no_data} no-data"`.

No new command flags are added in this slice. An `--no-skip` or `--force` flag to override the freshness skip may be added in slice 123 when daemon behavior makes it meaningful; adding it here would be speculative.

## Event Emission

The orchestrator already emits `RUN_STARTED`, `CHUNK_OK`, `CHUNK_FAILED`, `RUN_FINISHED` via slice 121's `JsonlEventSink`. This slice just provides the sink from the CLI:

- CLI constructs a `JsonlEventSink` pointed at `$MT_DATA_DIR/events/acquisition.jsonl` (or `~/.local/share/manta-trading/events/acquisition.jsonl` as fallback).
- If `MT_DATA_DIR` is not set and the fallback parent is not writable, CLI swaps in `NullEventSink` and logs a warning once. Do not crash because a log file is missing — the acquisition work is the point.

The event file path is *not* made configurable via a new CLI flag. A settings field `events_path` can be added later if operators ask for it.

## Testing Strategy

### Unit: `test_provider_alphavantage.py`

- Stubs `httpx.AsyncClient` or uses `httpx.MockTransport` with canned responses.
- `test_fetch_happy_path` — mock returns a valid `TIME_SERIES_DAILY_ADJUSTED` payload → `fetch_daily_ohlcv` returns a DataFrame with expected columns, ascending dates, correct dtypes.
- `test_fetch_rate_limit_response` — mock returns `{"Note": "Thank you for using Alpha Vantage..."}` → raises a specific provider error, no partial DataFrame.
- `test_fetch_error_message_response` — `{"Error Message": "Invalid API call"}` → raises.
- `test_fetch_empty_time_series` — `{"Time Series (Daily)": {}}` → raises (no silent empty).
- `test_validate_response_variants` — parametrized cases for the four error shapes + the happy shape.
- `test_output_size_passthrough` — `fetch_daily_ohlcv(..., output_size="full")` sends `outputsize=full` in the query params.

### Unit: `test_writer.py`

- `test_write_happy_path` — fake `MarketDB.writeDailyOHLCVAdjusted` returns True → writer returns `ChunkResult(last_written_ts=last_date_in_df, rows_written=len(df))`.
- `test_write_empty_df` — empty DataFrame → `ChunkResult(last_written_ts=None, rows_written=0)`, no `writeDailyOHLCVAdjusted` call.
- `test_write_db_failure_raises` — fake db returns False → writer raises `RuntimeError`.
- `test_last_ts_is_max_date_utc` — DataFrame with mixed ordering → `last_written_ts` equals the latest trading date in UTC midnight.

### Unit: `test_orchestrator.py`

- Uses fake `ChunkProvider`, fake `ChunkWriter`, in-memory `AcquisitionStateRepository` fake, `NullEventSink`.
- `test_update_symbol_happy_path` — provider yields one chunk of 5 rows → state transitions to `ok`, `last_success_ts` matches the max date, `AcquisitionResult.final_status == OK`.
- `test_update_symbol_fetch_failure` — provider raises → state is `failed`, error message captured, no write attempted.
- `test_update_symbol_write_failure` — writer raises → state is `failed`, event is `CHUNK_FAILED`, `last_success_ts` unchanged from prior value.
- `test_update_symbols_skips_fresh` — seed state repo with `AAPL` status=`ok`, last_success=yesterday → `update_symbols(["AAPL", "MSFT"])` calls the provider only for MSFT.
- `test_update_symbols_retries_failed` — seed state with `AAPL` status=`failed` → provider is called for `AAPL` regardless of staleness.
- `test_update_symbols_resume_after_crash` — run `update_symbols(["A","B","C"])` with provider raising on "B". First call: A→ok, B→failed, C→(not reached). Second call (same state repo, fresh provider that succeeds): A is skipped, B is retried, C is fetched. Final state: all three ok. **This is the resumability property and the most important test in the slice.**
- `test_resolve_output_size_boundary` — no last_success → `full`; gap ≤ 100 → `compact`; gap > 100 → `full`.

### Integration: `test_daily_update_resume.py`

Skipped unless both `MT_TIMESCALE_DB_URL` and `MT_MARKET_DB_URL` are set. Uses real databases (test instances).

- `test_cli_update_all_resume` — seed symbol_list with 3 symbols. First run uses a stub provider that succeeds for #1, raises for #2, is never called for #3. Verify `acquisition_state`: sym1=ok, sym2=failed, sym3 absent or pending. Run again with a provider that succeeds for all. Verify `acquisition_state`: all three ok, and the DataFrame written for sym1 was **not** rewritten (check timestamp or row hash).
- Harness constructs the orchestrator directly (not via CLI subprocess) and uses dependency injection for the stub provider, so the CLI code path is exercised without needing real AlphaVantage credentials.

### Regression: existing commands

- `mt data daily symbols` — still works.
- `mt data daily coverage` — still works.
- `mt data state --granularity daily` — now returns rows after a daily update.
- `mt data daily update SYMBOL` against a real test DB with real credentials is verified manually in the walkthrough below, not in automated tests (we do not hit the real API in CI).

## Success Criteria

- `IDailyDataProvider` Protocol exists and is referenced by `AlphaVantageDailyProvider` via `isinstance` duck typing / static type checking.
- `AlphaVantageDailyProvider` passes its unit test suite with mocked HTTP transport. No real network calls in tests.
- `MarketDBDailyWriter` passes its unit tests. `chunks_written == 0` when the input DataFrame is empty; raises on underlying failure.
- `DailyAcquisitionOrchestrator` passes its unit tests. The resume property test is green.
- `mt data daily update SYMBOL` writes an `acquisition_state` row (visible via `mt data state`).
- `mt data daily update-all` skips already-fresh symbols, retries failed symbols, and produces a final summary line.
- Integration test resumes correctly after injected failure. Sym1's data is not rewritten (verified via `INSERT ... ON CONFLICT DO NOTHING` semantics — the write would succeed but change nothing; the test asserts row count unchanged).
- `mt data daily symbols`, `mt data daily coverage`, `mt data daily migrate`, `mt data state`, and all minute/instrument/calendar commands are unchanged (no regressions).
- No magic strings: `"daily"`, `"ok"`, `"failed"`, `"compact"`, `"full"`, `"alphavantage"` each come from an enum or a named constant. The `MIN_DAYS=2` / `RECENT_DAYS=100` thresholds live in `freshness.py` as named constants.
- Source files are ≤ ~300 lines. The orchestrator module is ≤ ~150 lines; if it grows beyond that, freshness and writer helpers get split out (already planned).
- Pre-slice test suite passes with identical counts (slice 121 tests still green).

## Verification Walkthrough

Draft — will be refined during Phase 6 with actual commands, output, and any surprises.

**Setup**

1. `git checkout -b 122-slice.daily-provider-interface-and-alphavantage-daily-provider`
2. Confirm test databases reachable:
   ```bash
   echo $MT_TIMESCALE_DB_URL
   echo $MT_MARKET_DB_URL
   echo $MT_ALPHAVANTAGE_API_KEY  # or $ALPHAVANTAGE_API_KEY
   ```
3. Confirm slice 121 migration applied:
   ```bash
   psql $MT_TIMESCALE_DB_URL -c '\d acquisition_state'
   ```
   Expected: table exists with PK on `(symbol, granularity, provider)`.

**Tests (no external calls)**

4. Run the new test suite:
   ```bash
   pytest test/unit/data/acquisition/daily/ -v
   ```
   Expected: all new tests pass, including `test_update_symbols_resume_after_crash`.

5. Run the full acquisition suite:
   ```bash
   pytest test/unit/data/acquisition/ -v
   ```
   Expected: slice 121 tests still pass, new daily tests included.

**Single symbol — real API**

6. Fetch one symbol and verify state row appears:
   ```bash
   mt data daily update AAPL
   mt data state --symbol AAPL --granularity daily
   ```
   Expected: the second command returns one row with `status=ok`, `last_success_ts` on the most recent trading day, `retry_count=0`.

7. Run again immediately:
   ```bash
   mt data daily update AAPL
   ```
   Expected: succeeds again; output mentions "compact" fetch; no duplicate rows written (verified by querying `SELECT COUNT(*) FROM dailyohlcvadjusted WHERE symbol='AAPL'` before and after — same count modulo the single trading day of new bars).

**Batch — skip-on-fresh**

8. Pick three symbols and run:
   ```bash
   mt data daily update MSFT
   mt data daily update-all --age 0
   ```
   Expected: the `update-all` output shows MSFT skipped (or at minimum, the log notes it was already fresh). Summary line prints.

**Batch — resume after induced failure (integration test, not manual)**

9. See `test_cli_update_all_resume`. This is the automated integration test; no manual walkthrough step.

**Regression check**

10. Run the existing commands to confirm they still work:
    ```bash
    mt data daily coverage
    mt data daily symbols   # if you want to exercise it; hits the real API
    mt data minute coverage
    mt data state
    ```
    Expected: all succeed. `mt data state` now lists daily rows from the steps above.

11. Run the full test suite:
    ```bash
    pytest test/ -v
    ```
    Expected: same number of non-acquisition tests pass as before. Acquisition test count increases by the new daily tests.

**Inspect the event log**

12. Locate and tail:
    ```bash
    ls -la $MT_DATA_DIR/events/acquisition.jsonl 2>/dev/null || \
        ls -la ~/.local/share/manta-trading/events/acquisition.jsonl
    tail -n 10 $MT_DATA_DIR/events/acquisition.jsonl
    ```
    Expected: one `run_started`, one `chunk_ok`, one `run_finished` per successful `mt data daily update` invocation. Failures add a `chunk_failed` line.

## Notes

- The `marketservice.py` legacy file remains on disk. This slice stops the CLI daily commands from calling it directly; a later slice (likely 123) removes the file entirely once `daily_symbols` is also migrated. Avoid the temptation to delete it here — the surface area of this slice is already adequate.
- The shared rate limiter between daily and minute becomes important in slice 123/125 when both are running concurrently. This slice's `AlphaVantageDailyProvider` accepts an injected `RateLimiter` specifically so slice 123 can wire it without another refactor.
- The per-symbol concurrency question is intentionally deferred. Daily fetches are fast (1 request each) and the 30 req/min cap means a sequential loop of 500 symbols takes ~17 minutes. That is fine for the CLI case. The daemon slice can revisit concurrency if the bottleneck moves.
- If `MarketDB.writeDailyOHLCVAdjusted` turns out to have a write-path bug surfaced by the new writer tests (e.g. the `INSERT ... ON CONFLICT DO NOTHING` silently drops real updates like split/dividend adjustments), fix that here — it is a direct dependency and fixing it elsewhere would split the context.
- The `update-file` command's "read symbols from a text file" behavior is preserved exactly. Only the orchestrator underneath changes.
- This slice does not add retry logic. Retry policy is a daemon concern (slice 123). A failed CLI invocation prints the error and exits non-zero; re-running the command is the retry mechanism.
