---
docType: slice-design
slice: minute-provider-fixes-and-orchestrator-hardening
project: trading
parent: user/architecture/120-slices.data-acquisition.md
dependencies: [100, 121, 900]
interfaces: [125]
dateCreated: 20260413
dateUpdated: 20260413
status: complete
---

# Slice Design: Minute Provider Fixes and Orchestrator Hardening

## Overview

Fix the `AlphaVantageMinuteProvider` so that month-based pagination actually works (the `month=YYYY-MM` parameter is calculated but never sent to the API), fix the `RateLimiter` so it releases its lock during the sleep window, and replace `HistoricalMinuteService` with a new `MinuteAcquisitionOrchestrator` that uses the slice 121 orchestrator core with per-month-chunk write-and-checkpoint. Wire `mt data minute update SYMBOL` through the new path with full state tracking and resume.

This is the highest-risk slice in the data acquisition initiative: it replaces the minute orchestration layer for a pipeline that manages ~45M rows of irreplaceable historical data. The risk is mitigated by: (a) the replacement uses the same proven orchestrator core from slice 121, (b) all changes are on the test DB until the PM confirms the backup gate, and (c) the underlying storage (`TimescaleMinuteDataDB`) and data processing (`DataProcessor`) remain unchanged.

**Out of scope:** Minute acquisition daemon (slice 125), concurrent symbol fetching (slice 125), shared rate limit coordination between daily and minute daemons (slice 125 concern), trading-calendar-aware gap detection (Initiative 140), quality metrics beyond basic OHLCV validation (Initiative 140).

## Value

**Operator-facing:** `mt data minute update SYMBOL` becomes resumable. A fetch that fails on month 12 of 24 resumes at month 13 on the next invocation instead of re-fetching from month 1. `mt data minute update SYMBOL --months 3` fetches only the most recent 3 months (quick refresh). State is visible via the existing `mt data state --granularity minute` command.

**Developer-facing:** The minute provider actually sends the `month` parameter, so each request returns the requested month's data instead of always returning the most recent trailing data. The `RateLimiter` no longer serializes concurrent coroutines while sleeping. These are correctness fixes that unblock slice 125's daemon.

**Architectural:** Completes the minute acquisition vertical through the orchestrator layer: provider (this slice, fixed) → orchestrator (this slice, new) → state tracking (slice 121, reused). After this slice, slice 125 only needs to wrap the orchestrator in a daemon loop — the same pattern as slice 123 wrapping slice 122.

## Dependencies

### Prerequisites

- **Slice 121** (complete): Provides `run_acquisition_unit`, `AcquisitionStateRepository`, `AcquisitionStateRow`, `EventSink`, and the `ChunkProvider`/`ChunkWriter` protocols.
- **Slice 100** (complete): Provides `TimescaleMinuteDataDB` (psycopg3, COPY protocol), `MarketDB`, and the instrument registry.
- **Slice 900** (complete): Provides CLI framework (Typer), `Settings`, structured logging, provider registry with enums.

### Interfaces Required

- `run_acquisition_unit(WorkItem, ChunkProvider, ChunkWriter, AcquisitionStateRepository, EventSink, run_id)` — core orchestrator (unchanged).
- `TimescaleMinuteDataDB.write_minute_data_bulk(symbol, df)` — COPY-based minute writer (unchanged).
- `AlphaVantageMinuteProvider.fetch_minute_data(symbol, start, end)` — fixed to actually send the `month` parameter.
- `DataProcessor.process(raw_response, provider)` — OHLCV validation + session classification (unchanged).
- `AcquisitionStateRepository` — state reads/writes (unchanged).

## Architecture

### Component Structure

```
src/manta_trading/
├── data/acquisition/
│   ├── orchestrator.py              # slice 121 — unchanged
│   ├── state.py                     # slice 121 — unchanged
│   ├── events.py                    # slice 121 — unchanged
│   ├── minute/                      # NEW package (this slice)
│   │   ├── __init__.py
│   │   ├── provider.py              # Re-export IMinuteDataProvider + add fetch_month protocol
│   │   ├── writer.py                # TimescaleMinuteWriter (ChunkWriter adapter)
│   │   ├── orchestrator.py          # MinuteAcquisitionOrchestrator
│   │   └── freshness.py             # Minute-specific freshness constants
│   └── daily/                       # slice 122 — unchanged
├── data/historical_minute/
│   ├── providers/
│   │   └── alphavantage.py          # FIXED: month param, extended_hours, docstrings
│   ├── provider.py                  # Existing IMinuteDataProvider — unchanged
│   ├── processor.py                 # Existing DataProcessor — unchanged
│   └── service.py                   # HistoricalMinuteService — NOT deleted (slice 125)
└── util/
    └── ratelimiter.py               # FIXED: release lock during sleep
```

**Key decision:** `HistoricalMinuteService` is **not deleted** in this slice. It has no direct callers today (minute acquisition is CLI-only and the CLI will be rewired to the new orchestrator), but removing it is deferred to slice 125 which replaces its daemon/batch role. If inspection reveals zero callers, it can be deleted here — but verify first.

### Data Flow

```
CLI: mt data minute update AAPL
  │
  ▼
MinuteAcquisitionOrchestrator.update_symbol("AAPL", run_id=...)
  │
  ├─ Read AcquisitionStateRow for (AAPL, MINUTE, alphavantage)
  │   └─ Determine: start month = last_success_ts or 24 months ago
  │
  ├─ Build _MinuteChunkProviderAdapter(provider, months_to_fetch)
  │   └─ fetch_chunks() yields one FetchedChunk per month:
  │       ├─ provider._fetch_month(symbol, "2024-06") → raw JSON
  │       ├─ provider.validate_response(raw) → ValidationResult
  │       ├─ provider.convert_to_standard_format(raw) → DataFrame
  │       └─ processor.process(raw_response, provider) → (df, validation)
  │           └─ yield FetchedChunk(rows=df, chunk_start, chunk_end)
  │
  ├─ For each chunk: run_acquisition_unit core loop
  │   ├─ writer.write(chunk) → ChunkResult
  │   │   └─ TimescaleMinuteDataDB.write_minute_data_bulk(symbol, df)
  │   ├─ state_repo.upsert(last_success_ts = actual max timestamp in chunk)
  │   └─ event_sink.emit(CHUNK_OK)
  │
  └─ Return AcquisitionResult
```

**Per-month-chunk checkpoint:** This is the critical difference from the current `HistoricalMinuteService`, which gathers all months into one DataFrame and writes once. The new orchestrator writes and checkpoints after each month. If the daemon crashes after writing month 12 of 24, it resumes at month 13.

### State Management

Uses the existing `acquisition_state` table from slice 121 with `granularity=MINUTE`:

- **Primary key:** `(symbol, 'minute', 'alphavantage')`
- **Watermark:** `last_success_ts` = max timestamp in the most recently written month-chunk. This is the actual data extent, not the requested month boundary.
- **Resume:** On next invocation, start fetching from the month containing `last_success_ts + 1 day` (or 24 months ago if no prior state).
- **Partial months:** AlphaVantage returns ~10 trading days per request with `month` specified. The watermark reflects what was actually returned, not the full calendar month. The next fetch requests the same month — AlphaVantage returns the next ~10 days. This continues until the full month is covered, at which point the orchestrator advances to the next month.

## Technical Decisions

### Fix 1: AlphaVantage `_fetch_month` — Wire the `month` Parameter

**Current bug:** `_fetch_month()` receives `month_start`/`month_end` but never includes `month=YYYY-MM` in the API request params. Every request returns the same most-recent trailing data regardless of which month was requested.

**Fix:** Add `"month": f"{month_start:%Y-%m}"` and `"extended_hours": "true"` to the request params. The `datatype` param defaults to JSON (correct behavior), so no change needed there. Update the docstring that references CSV format.

```python
params = {
    "function": "TIME_SERIES_INTRADAY",
    "symbol": symbol,
    "interval": "1min",
    "month": f"{month_start:%Y-%m}",
    "extended_hours": "true",
    "outputsize": "full",
    "apikey": self.api_key,
}
```

**Verification:** Fetch the same symbol with two different months; confirm the returned timestamps fall within the requested months.

### Fix 2: RateLimiter — Release Lock During Sleep

**Current bug:** The `RateLimiter.__aenter__` holds `self.lock` during `asyncio.sleep(time_to_wait)`. Any concurrent coroutine trying to acquire the rate limiter blocks on the lock — not on the rate limit itself — serializing all concurrent work unnecessarily.

**Fix:** Release the lock before sleeping, then re-acquire and re-check:

```python
async def __aenter__(self):
    while True:
        async with self.lock:
            now = time.time()
            self.calls = [t for t in self.calls if now - t < self.period]

            if len(self.calls) < self.max_calls:
                self.calls.append(now)
                return self

            # Calculate wait time but release lock before sleeping
            oldest_call = self.calls[0]
            time_to_wait = (oldest_call + self.period) - now

        # Sleep WITHOUT holding the lock
        if time_to_wait > 0:
            await asyncio.sleep(time_to_wait)
        # Loop back to re-acquire lock and re-check
```

This is a correctness fix for concurrent usage. The daily daemon (sequential symbols) is unaffected. The minute daemon (slice 125, concurrent symbols) requires this.

### Fix 3: Docstring Corrections in `AlphaVantageMinuteProvider`

Several docstrings reference CSV format when the code uses JSON. The class docstring at the top of the file should be reviewed and corrected. This is a documentation-only change in the provider file.

### New: `_MinuteChunkProviderAdapter`

Adapts `IMinuteDataProvider` into the slice 121 `ChunkProvider` protocol. Unlike the daily adapter (which yields exactly one chunk), the minute adapter yields **one chunk per month** in the requested range.

The adapter:
1. Calculates month ranges from `work_item.time_range_start` to `work_item.time_range_end`.
2. For each month: calls `provider.fetch_minute_data()` (which internally calls `_fetch_month()`), validates, converts to DataFrame via `DataProcessor.process()`.
3. Yields one `FetchedChunk` per month with `chunk_start`/`chunk_end` set to the actual data extent (not the requested month boundary).

If a month returns no data (empty DataFrame), the adapter yields a chunk with `rows=None` — the writer handles this by returning `ChunkResult(None, 0)` and the orchestrator records an OK with no watermark advance.

### New: `TimescaleMinuteWriter`

Adapts `TimescaleMinuteDataDB.write_minute_data_bulk()` as a slice 121 `ChunkWriter`:

```python
@dataclass
class TimescaleMinuteWriter:
    db: TimescaleMinuteDataDB
    symbol: str

    def write(self, chunk: FetchedChunk) -> ChunkResult:
        df: pd.DataFrame | None = chunk.rows
        if df is None or (isinstance(df, pd.DataFrame) and df.empty):
            return ChunkResult(last_written_ts=None, rows_written=0)
        success = self.db.write_minute_data_bulk(self.symbol, df)
        if not success:
            raise RuntimeError(f"TimescaleDB write failed for {self.symbol}")
        max_ts = df.index.max()
        # ... normalize to UTC
        return ChunkResult(last_written_ts=last_ts, rows_written=len(df))
```

Follows the same pattern as `MarketDBDailyWriter` from slice 122.

### New: `MinuteAcquisitionOrchestrator`

Parallel to `DailyAcquisitionOrchestrator` from slice 122. Key differences:

- `update_symbol()` calculates month ranges based on `last_success_ts` watermark (not output_size).
- Uses `_MinuteChunkProviderAdapter` which yields multiple chunks (one per month).
- Each chunk is written and checkpointed independently by the orchestrator core.
- `update_symbols()` follows the same fail-fast pattern as daily (first failure stops batch).

Constructor parameters:
- `provider: IMinuteDataProvider` — the (fixed) AlphaVantage minute provider.
- `db: TimescaleMinuteDataDB` — minute data storage.
- `processor: DataProcessor` — OHLCV validation + session classification.
- `state_repo: AcquisitionStateRepository` — shared state (same table, `granularity=MINUTE`).
- `event_sink: EventSink` — structured events.
- `provider_id: str` — defaults to `ProviderType.ALPHA_VANTAGE.value`.
- `max_history_months: int` — defaults to 24 (AlphaVantage 2-year limit).

### Minute Freshness Constants

New file `src/manta_trading/data/acquisition/minute/freshness.py`:

```python
MIN_DAYS: int = 3
"""A minute-granularity symbol is "fresh" if its last success is < 3 calendar
days old. Tighter than daily (5) because minute data updates more frequently
and the daemon (slice 125) will run continuously."""

HISTORY_MONTHS: int = 24
"""AlphaVantage intraday history limit. Do not attempt to fetch data older
than this many months — it won't exist and the request is wasted."""
```

### CLI Additions

**`mt data minute update SYMBOL`** — Single-symbol minute acquisition with resume:
- `--months INT` — Limit fetch to most recent N months (default: all missing months up to 24).
- Calls `MinuteAcquisitionOrchestrator.update_symbol()`.
- Reads/writes `acquisition_state` with `granularity=MINUTE`.

**`mt data minute update-all`** — Batch update for all symbols:
- Calls `MinuteAcquisitionOrchestrator.update_symbols()`.
- Same fail-fast + resume semantics as daily.

Factory function `_create_minute_orchestrator(ctx, api_key)` parallels `_create_daily_orchestrator`.

### Migration Plan

| Component | Action | Detail |
|-----------|--------|--------|
| `AlphaVantageMinuteProvider._fetch_month` | Fix | Add `month` param, `extended_hours`, fix docstrings |
| `RateLimiter.__aenter__` | Fix | Release lock before sleeping |
| `HistoricalMinuteService` | Retain | No callers removed in this slice; cleanup deferred to 125 |
| New `data/acquisition/minute/` | Create | writer, orchestrator, freshness, provider re-exports |
| CLI `data.py` | Extend | Add `minute update`, `minute update-all`, factory |
| Existing `minute coverage`, `minute metrics` | Unchanged | Read-only commands, no migration needed |

## Integration Points

### Provides to Other Slices

- **Slice 125 (Minute Daemon):** `MinuteAcquisitionOrchestrator` with `update_symbol()` — the daemon wraps this in a loop, exactly as slice 123 wraps the daily orchestrator.
- **Slice 125:** Fixed `RateLimiter` that supports concurrent coroutines.
- **Slice 125:** Minute freshness constants (`MIN_DAYS`, `HISTORY_MONTHS`).

### Consumes from Other Slices

- **Slice 121:** `run_acquisition_unit`, `AcquisitionStateRepository`, `EventSink`, protocols.
- **Slice 100:** `TimescaleMinuteDataDB`, `MarketDB` (for symbol lists).
- **Slice 900:** CLI framework, `Settings`, provider registry.

## Success Criteria

### Functional Requirements

- `AlphaVantageMinuteProvider._fetch_month("AAPL", month_start=datetime(2025, 6, 1))` sends `month=2025-06` and `extended_hours=true` in the API request.
- Two consecutive requests for different months return data with timestamps in their respective months.
- `RateLimiter` does not hold the lock during sleep — a second coroutine can check the rate limit while the first is waiting.
- `mt data minute update AAPL` fetches minute data, writes per-month via COPY, and updates `acquisition_state` with `granularity=MINUTE` after each month-chunk.
- Resume: `mt data minute update AAPL` (interrupted after month 12) + `mt data minute update AAPL` (re-run) resumes at month 13, not month 1.
- `mt data minute update AAPL --months 3` fetches only the 3 most recent months.
- Watermarks reflect actual data extent (max timestamp in written chunk), not the requested month boundary.
- `mt data state --granularity minute --symbol AAPL` shows the acquisition state row.

### Technical Requirements

- Unit tests for `_MinuteChunkProviderAdapter` (multi-month yield, empty month, partial month).
- Unit tests for `TimescaleMinuteWriter` (success, empty, write failure).
- Unit tests for `MinuteAcquisitionOrchestrator.update_symbol()` (full range, resume from watermark, failure mid-range).
- Unit tests for fixed `RateLimiter` (concurrent acquire does not block on lock during sleep).
- Integration test: fetch 2–3 months of real minute data for one symbol, write to test DB, verify per-month state checkpoints, simulate mid-fetch failure and verify resume. Skips if `MT_TIMESCALE_DB_URL` not set.
- All existing unit tests continue to pass.

### Verification Walkthrough

**Prerequisites:**
```bash
# Source environment
set -a && source .env && set +a

# Verify DB connectivity
echo $MT_TIMESCALE_DB_URL
echo $MT_MARKET_DB_URL
echo $MT_ALPHAVANTAGE_API_KEY
```

**1. Verify provider fix — month parameter is sent:**
```bash
# Fetch a specific symbol's minute data (limited to 1 month for speed)
mt data minute update AAPL --months 1
```

Expected: request URL contains `month=YYYY-MM&extended_hours=true`. Actual run (2026-04-13) produced:
```
GET https://www.alphavantage.co/query?function=TIME_SERIES_INTRADAY&symbol=AAPL&interval=1min&month=2026-04&extended_hours=true&outputsize=full&apikey=***
Converted 6697 rows for AAPL
TimescaleDB bulk write: 0.153s (6697 rows, 43658 rows/s)
AAPL: ok — 1 chunk(s) written
```

```bash
# Check acquisition state — should show granularity=minute with a recent watermark
mt data state --granularity minute --symbol AAPL
```
Expected: one row with `status=ok`, `last_success_ts` within the last few minutes.

**2. Verify per-month checkpoint and resume:**

Covered by automated integration test `test/integration/data/acquisition/minute/test_minute_orchestrator_integration.py::test_resume_after_mid_range_failure` — uses a stub provider that fails on month 2, then re-runs; asserts state row `last_success_ts` = month 1 watermark after failure and all 3 distinct months present in `minute_ohlcv` after resume.

Live exercise (optional, manual):
```bash
mt data minute update AAPL --months 3
# Interrupt with Ctrl+C after ~1 minute
mt data state --granularity minute --symbol AAPL
# Re-run — resumes from watermark
mt data minute update AAPL --months 3
```

Caveat: `minute_ohlcv` currently lacks a `UNIQUE(symbol, time)` constraint, so re-fetching a month that has already been written WILL duplicate rows. The orchestrator resume logic is correct; DB-level idempotence is a pre-existing schema gap tracked for a future slice.

**3. Verify rate limiter fix:**
```bash
pytest test/unit/util/test_ratelimiter.py -v
```
Expected: 5 tests pass. Key test: `test_concurrent_acquires_do_not_serialize_on_lock` — verifies the second coroutine begins its rate-limit wait within 50 ms of the first, not staggered by the first's full sleep.

**4. Run full test suite:**
```bash
pytest test/unit/ -q
# Expected: 828+ passed. 12 pre-existing failures in daily freshness tests,
# CLI tests, and market DB tests are unrelated to slice 124 — caused by:
#   - slice 123's MIN_DAYS 2→5 change (daily/freshness tests not updated)
#   - unrelated CLI/settings test drift
# No failures introduced by slice 124.

pytest test/integration/data/acquisition/minute/ -v
# Expected: 2 tests pass against real TimescaleDB.
```

## Implementation Notes

### Development Approach

Suggested implementation order:

1. **RateLimiter fix** — smallest, most isolated change. Unit test the concurrent behavior.
2. **AlphaVantage provider fix** — wire `month` param, `extended_hours`, fix docstrings. Unit test with mock HTTP.
3. **`TimescaleMinuteWriter`** — ChunkWriter adapter. Unit test with fake DB.
4. **`_MinuteChunkProviderAdapter`** — ChunkProvider adapter. Unit test with fake provider.
5. **`MinuteAcquisitionOrchestrator`** — composes provider adapter + writer + orchestrator core. Unit test with fakes.
6. **CLI commands** — `minute update`, `minute update-all`, factory function.
7. **Integration test** — end-to-end against test DB.

### Special Considerations

- **Irreplaceable data:** ~45M rows of minute data beyond AlphaVantage's 2-year window cannot be re-fetched. All development and testing uses the test DB. Production deployment is gated on PM's backup confirmation (external to this slice).
- **AlphaVantage partial months:** With `month=YYYY-MM` and `outputsize=full`, the API returns ~10 trading days, not a full calendar month. The adapter must handle this: the watermark advances only to the actual data extent, and the next request for the same month may return the next ~10 days. The adapter should detect when a month is fully covered (returned data extends to month boundary or next request returns no new data) and advance to the next month.
- **Rate limit budget:** At 30 req/min, catching up a single symbol's 24-month history requires many requests (~2-3 per month × 24 months ≈ 48-72 requests ≈ 2-3 minutes). A universe of 500+ symbols takes days. This is acceptable — the daemon (slice 125) manages prioritization and steady progress.
- **AlphaVantage legacy account:** The account has a legacy plan with favorable pricing that is no longer offered. Do not make any account changes. The 30 req/min limit is correct for this account.
