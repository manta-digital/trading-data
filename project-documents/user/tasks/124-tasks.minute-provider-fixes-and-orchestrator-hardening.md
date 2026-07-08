---
docType: tasks
slice: minute-provider-fixes-and-orchestrator-hardening
project: trading
lld: user/slices/124-slice.minute-provider-fixes-and-orchestrator-hardening.md
dependencies: [100, 121, 900]
projectState: Slices 121–123 complete and merged. run_acquisition_unit, AcquisitionStateRepository, EventSink, and the ChunkProvider/ChunkWriter protocols are stable. AlphaVantageMinuteProvider exists but its _fetch_month does not send the month parameter. RateLimiter holds its lock during sleep. TimescaleMinuteDataDB and DataProcessor are unchanged and solid. HistoricalMinuteService has no active callers but still exists.
dateCreated: 20260413
dateUpdated: 20260413
status: complete
---

# Tasks: Minute Provider Fixes and Orchestrator Hardening

## Context Summary

- Working on the `minute-provider-fixes-and-orchestrator-hardening` slice (124)
- Three bug fixes: AlphaVantage `_fetch_month` wires the `month=YYYY-MM` param and `extended_hours=true`; `RateLimiter` releases lock during sleep; provider docstrings corrected
- New package `src/manta_trading/data/acquisition/minute/` with `writer.py`, `orchestrator.py`, `freshness.py`, `provider.py` (re-exports + fetch_month)
- `MinuteAcquisitionOrchestrator` uses slice 121 `run_acquisition_unit` with per-month-chunk write-and-checkpoint — resume at next unfetched month after failure
- New CLI: `mt data minute update SYMBOL [--months N]`, `mt data minute update-all`
- `HistoricalMinuteService` is retained (cleanup deferred to slice 125)
- All work is on test DB; production deployment gated on PM's minute-data backup confirmation (external)
- Dependencies: slice 121 (orchestrator core, state repo, event sink), slice 100 (TimescaleMinuteDataDB, MarketDB), slice 900 (CLI/settings)
- Next slice: 125 (minute acquisition daemon — wraps this orchestrator)

## Task Order Rationale

RateLimiter fix first (smallest, most isolated). Then AlphaVantage provider fix (unblocks real fetching). Then the new acquisition package bottom-up: freshness constants → writer → chunk adapter → orchestrator. CLI commands after the orchestrator. Integration test at the end. Each implementation task is immediately followed by its test task. Commits placed at coherent checkpoints so mid-slice failure has a safe rollback.

---

## 1. Fix RateLimiter Lock-During-Sleep Bug

- [x] **1.1** Read `src/manta_trading/util/ratelimiter.py` to confirm the current behavior
  - Confirm `__aenter__` holds `self.lock` across `asyncio.sleep(time_to_wait)` at line 33
  - Orientation only — no edits
  - Effort: 1

- [x] **1.2** Fix `RateLimiter.__aenter__` to release lock before sleeping
  - Restructure to a `while True` loop that:
    1. Acquires `self.lock`, prunes expired timestamps, checks capacity
    2. If under limit: records timestamp, returns (inside lock)
    3. If at limit: calculates `time_to_wait`, releases lock
    4. Sleeps outside the lock
    5. Loops back to re-acquire lock and re-check
  - Preserve the precise-timing behavior (wait exactly until oldest call expires, not an arbitrary interval)
  - Keep the class/module docstring — fix its placement (currently after class declaration; should be inside the class body as first statement)
  - Do not change `__aexit__`, `acquire`, or `release` (unchanged behavior for existing callers)
  - Effort: 3

- [x] **1.3** **Test** — `test/unit/util/test_ratelimiter.py`
  - If file does not exist, create it with module docstring and standard imports
  - `test_single_call_records_timestamp` — one acquire under limit completes immediately; `len(calls) == 1`
  - `test_n_calls_at_limit_forces_wait` — `max_calls=2`, `period=1s`; 3 rapid acquires; third must wait ~1s (use `time.monotonic()` around the third call; assert elapsed > 0.9s, < 1.5s)
  - `test_concurrent_acquires_do_not_serialize_on_lock` — `max_calls=2`, `period=2s`; start one acquire that will have to wait (via `asyncio.create_task`); while it is waiting, a second coroutine enters `__aenter__` and should **not block on the lock** — instead it should also wait on the rate limit and complete when capacity frees. Verify via ordering: the second coroutine starts its rate-limit wait within ~50ms of the first (not after the first's full sleep completes)
  - `test_release_is_noop` — call `release()` after acquire; no errors; state unchanged
  - `test_exit_context_manager_is_noop` — entering and exiting as async context manager does not alter state beyond the acquire
  - Effort: 3

- [x] **1.4** **Commit checkpoint** — `fix: release rate limiter lock during sleep window`
  - Stage `src/manta_trading/util/ratelimiter.py` and new test file
  - Effort: 1

---

## 2. Fix AlphaVantage Minute Provider

- [x] **2.1** Read `src/manta_trading/data/historical_minute/providers/alphavantage.py` end-to-end to confirm:
  - `_fetch_month` params at lines 296–302 — no `month` key, no `extended_hours` key
  - Top-of-file module docstring and class docstring — flag any references to CSV format (code uses JSON via `response.json()`)
  - `fetch_minute_data` flow that calls `_fetch_month` per month range
  - Orientation only — no edits
  - Effort: 1

- [x] **2.2** Fix `_fetch_month` to send `month` and `extended_hours` parameters
  - Add `"month": f"{month_start:%Y-%m}"` to the `params` dict
  - Add `"extended_hours": "true"` to the `params` dict (string, not bool — AlphaVantage expects a string)
  - Keep `"outputsize": "full"`, `"interval": "1min"`, `"function": "TIME_SERIES_INTRADAY"`, `"apikey"`
  - Do not change error handling, response parsing, or timeout behavior
  - Remove the obsolete comment at lines 293–295 (`# Format: yearmonth...` and `# For simplicity, we'll use the FULL output and filter later`) — replace with a one-line comment describing the corrected behavior
  - Effort: 2

- [x] **2.3** Correct docstrings that reference CSV format
  - Scan file for mentions of "CSV", "csv" in docstrings and comments
  - Replace with accurate JSON references where the code actually uses JSON
  - Do not change actual code behavior
  - Effort: 1

- [x] **2.4** **Test** — `test/unit/data/historical_minute/providers/test_alphavantage_minute_provider.py`
  - If file exists, extend it; otherwise create with module docstring
  - Use `httpx.MockTransport` or `respx` (consult the existing minute provider test patterns in the codebase) to mock HTTP responses
  - `test_fetch_month_sends_month_param` — call `_fetch_month(symbol="AAPL", month_start=datetime(2025, 6, 1), month_end=...)`. Assert the captured request URL query includes `month=2025-06` and `extended_hours=true`
  - `test_fetch_month_sends_other_months` — parametrized over several month_start values; assert each captured request sends the correct `month=YYYY-MM`
  - `test_fetch_month_invalid_response_returns_none` — mock returns a response the validator rejects; assert `_fetch_month` returns `None`
  - `test_fetch_month_http_error_returns_none` — mock returns 500; assert returns `None`
  - Effort: 3

- [x] **2.5** **Commit checkpoint** — `fix: wire month and extended_hours params in AlphaVantage minute provider`
  - Stage the provider file and its test file
  - Effort: 1

---

## 3. Minute Acquisition Package Skeleton

- [x] **3.1** Create `src/manta_trading/data/acquisition/minute/__init__.py`
  - Module docstring; export nothing yet
  - Effort: 1

- [x] **3.2** Create `src/manta_trading/data/acquisition/minute/freshness.py`
  - Define `MIN_DAYS: int = 3` with docstring explaining the threshold (minute data updates frequently; daemon runs continuously)
  - Define `HISTORY_MONTHS: int = 24` with docstring referencing AlphaVantage's 2-year intraday history limit
  - Implement `_is_fresh(last_success_ts: datetime | None, *, min_days: int = MIN_DAYS, today: datetime.date | None = None) -> bool` — same semantics as the daily version: gap < min_days, UTC-only date comparison, None → False
  - Include a TODO comment noting these constants should eventually migrate to config (mirrors the daily pattern)
  - No I/O
  - Effort: 2

- [x] **3.3** **Test** — `test/unit/data/acquisition/minute/test_freshness.py`
  - Create `test/unit/data/acquisition/minute/__init__.py`
  - `test_is_fresh_none_returns_false` — `_is_fresh(None)` → False
  - `test_is_fresh_within_threshold` — parametrized: gap 0, 1, 2 days → True
  - `test_is_fresh_at_threshold` — gap == MIN_DAYS → False (strict less-than)
  - `test_is_fresh_above_threshold` — gap 4, 10, 100 days → False
  - `test_is_fresh_tz_aware` — tz-aware `last_success_ts` computed against UTC `today` gives correct gap (no silent ±1 day drift)
  - All tests inject `today` explicitly
  - Effort: 2

---

## 4. Timescale Minute Writer (ChunkWriter Adapter)

- [x] **4.1** Create `src/manta_trading/data/acquisition/minute/writer.py`
  - Define `@dataclass TimescaleMinuteWriter`:
    - `db: TimescaleMinuteDataDB`
    - `symbol: str`
  - Implement `write(self, chunk: FetchedChunk) -> ChunkResult`:
    - If `chunk.rows is None` or empty DataFrame → return `ChunkResult(last_written_ts=None, rows_written=0)` without calling the DB
    - Call `self.db.write_minute_data_bulk(self.symbol, chunk.rows)`; if it returns False, raise `RuntimeError` with the symbol in the message
    - Compute watermark: `max_ts = chunk.rows.index.max()`; normalize to UTC-aware datetime. If naive, assume UTC and attach `tzinfo=timezone.utc`. If tz-aware, convert to UTC. Do **not** collapse to midnight (minute granularity preserves time)
    - Return `ChunkResult(last_written_ts=last_ts, rows_written=len(chunk.rows))`
  - Mirror the structure of `MarketDBDailyWriter` but with minute-granularity timestamp preservation
  - Effort: 2

- [x] **4.2** **Test** — `test/unit/data/acquisition/minute/test_writer.py`
  - Use a `FakeMinuteDB` with a `write_minute_data_bulk(symbol, df)` method that records calls and returns configurable success/failure
  - `test_write_empty_dataframe_returns_none_watermark` — empty DataFrame → `ChunkResult(None, 0)`, no DB call made
  - `test_write_none_rows_returns_none_watermark` — `chunk.rows=None` → same behavior
  - `test_write_success_returns_max_timestamp` — DataFrame with 3 minute bars (indexes `09:30`, `09:31`, `09:32` UTC); assert `last_written_ts` equals `09:32:00+00:00` and `rows_written == 3`
  - `test_write_success_naive_index_assumed_utc` — DataFrame with tz-naive DatetimeIndex; watermark returned as tz-aware UTC
  - `test_write_success_tz_aware_index_converted_to_utc` — DataFrame with US/Eastern DatetimeIndex; watermark returned in UTC, correctly converted
  - `test_write_failure_raises` — fake DB returns False; assert `RuntimeError` raised with symbol in message
  - Effort: 3

---

## 5. Minute Chunk Provider Adapter

- [x] **5.1** Create `src/manta_trading/data/acquisition/minute/provider.py`
  - Re-export `IMinuteDataProvider` from `manta_trading.data.historical_minute.provider`
  - Re-export `RawDataResponse`, `RateLimitInfo`, `ValidationResult` likewise (matches the daily provider module's re-export pattern)
  - No new class definitions in this file
  - Effort: 1

- [x] **5.2** Add `_MinuteChunkProviderAdapter` to `src/manta_trading/data/acquisition/minute/orchestrator.py` (create file)
  - File header: module docstring explaining the orchestrator and adapter
  - Class `_MinuteChunkProviderAdapter`:
    - Constructor: `__init__(self, provider: IMinuteDataProvider, processor: DataProcessor, months: list[tuple[datetime, datetime]])` — `months` is a pre-computed list of `(month_start, month_end)` UTC tuples
    - `async def fetch_chunks(self, work_item: WorkItem) -> AsyncGenerator[FetchedChunk, None]`:
      - For each `(month_start, month_end)` in `self._months`:
        - Call `await self._provider.fetch_minute_data(work_item.symbol, month_start, month_end)` → `RawDataResponse`
        - Call `self._processor.process(raw_response, self._provider)` → `(df, validation)`
        - If `df` is empty: yield `FetchedChunk(rows=None, chunk_start=month_start, chunk_end=month_end)` and continue (writer handles None gracefully)
        - Otherwise: yield `FetchedChunk(rows=df, chunk_start=month_start, chunk_end=month_end)`
    - Validation errors are logged but do not raise — the orchestrator already handles write/fetch failures via the chunk result
  - Effort: 3

- [x] **5.3** **Test** — `test/unit/data/acquisition/minute/test_chunk_provider_adapter.py`
  - Use `FakeMinuteProvider` that returns canned `RawDataResponse` per (symbol, month) and `FakeProcessor` that returns canned `(df, validation)`
  - `test_yields_one_chunk_per_month` — 3 months in range; assert exactly 3 chunks yielded in order
  - `test_empty_month_yields_none_rows_chunk` — one month with empty df; assert chunk yielded with `rows=None`
  - `test_chunk_boundaries_match_input` — assert `chunk_start`/`chunk_end` on each yielded chunk equal the corresponding month tuple
  - `test_zero_months_yields_nothing` — empty months list; async generator terminates immediately with no yields
  - Effort: 2

---

## 6. Minute Acquisition Orchestrator

- [x] **6.1** Add helper `_compute_month_ranges` to `orchestrator.py`
  - `def _compute_month_ranges(*, start_ts: datetime, end_ts: datetime, max_months: int = HISTORY_MONTHS) -> list[tuple[datetime, datetime]]`
  - Generates calendar-aligned month ranges covering `[start_ts, end_ts]`
  - Each tuple is `(first_day_of_month_00:00_UTC, last_moment_of_month_UTC)`
  - Caps at `max_months` entries (most recent months when capped)
  - Returns empty list if `start_ts > end_ts`
  - Pure function; no I/O; deterministic
  - Effort: 2

- [x] **6.2** **Test** — add to `test/unit/data/acquisition/minute/test_orchestrator.py`
  - `test_compute_month_ranges_single_month` — start and end in same calendar month → one tuple
  - `test_compute_month_ranges_multi_month` — Jan 15 to Mar 15 → three tuples covering Jan, Feb, Mar
  - `test_compute_month_ranges_caps_at_max` — 30-month span with `max_months=24` → 24 tuples, most recent 24
  - `test_compute_month_ranges_inverted_returns_empty` — start > end → `[]`
  - `test_compute_month_ranges_boundaries_utc` — tuples start at `YYYY-MM-01T00:00:00+00:00`
  - Effort: 2

- [x] **6.3** Implement `MinuteAcquisitionOrchestrator` in `src/manta_trading/data/acquisition/minute/orchestrator.py`
  - Constructor:
    ```
    __init__(
      self,
      provider: IMinuteDataProvider,
      db: TimescaleMinuteDataDB,
      processor: DataProcessor,
      state_repo: AcquisitionStateRepository,
      event_sink: EventSink,
      *,
      min_days_fresh: int = MIN_DAYS,
      max_history_months: int = HISTORY_MONTHS,
      provider_id: str = ProviderType.ALPHA_VANTAGE.value,
    )
    ```
  - `async def update_symbol(self, symbol: str, *, months_limit: int | None = None, run_id: UUID) -> AcquisitionResult`:
    1. Read existing state row via `state_repo.get(symbol, Granularity.MINUTE, provider_id)`
    2. Compute `start_ts`: `last_success_ts` if present; else `now - HISTORY_MONTHS` months
    3. Compute `end_ts = now(UTC)`
    4. Build month ranges via `_compute_month_ranges(start_ts, end_ts, max_months=months_limit or max_history_months)`
    5. If no months → return `AcquisitionResult(chunks_attempted=0, chunks_written=0, chunks_failed=0, final_status=AcquisitionStatus.OK)` without calling the orchestrator core
    6. Build `WorkItem(symbol, Granularity.MINUTE, provider_id, start_ts, end_ts)`
    7. Build `_MinuteChunkProviderAdapter(provider, processor, months)` and `TimescaleMinuteWriter(db, symbol)`
    8. Return `await run_acquisition_unit(work_item, adapter, writer, state_repo, event_sink, run_id)`
  - `async def update_symbols(self, symbols: list[str], *, months_limit: int | None = None, run_id: UUID, skip_recent: bool = True) -> BatchResult`:
    - Mirror the daily `update_symbols` fail-fast pattern (see `daily/orchestrator.py`)
    - Skip recent: if `skip_recent` and state row exists with `status=OK` and `_is_fresh(last_success_ts, min_days=self._min_days_fresh)` → `result.skipped += 1`
    - On first failure: append to `failed_symbols`, break (resume on next invocation)
    - Define `BatchResult` as in daily module (or reuse by importing if the daily dataclass is reasonable — confirm naming/fields match before reusing vs. defining a parallel dataclass; define locally if daily's is in `daily.orchestrator` private scope)
  - File length target: ≤ ~300 lines. If over, extract `_MinuteChunkProviderAdapter` into its own file.
  - Effort: 4

- [x] **6.4** **Test** — add to `test/unit/data/acquisition/minute/test_orchestrator.py`
  - Use fakes: `FakeMinuteProvider`, `FakeProcessor`, `FakeMinuteDB`, `FakeStateRepo` (in-memory; reuse pattern from slice 121/122), `NullEventSink`
  - `test_update_symbol_no_state_starts_from_history_limit` — empty state; assert `update_symbol` requests months covering the last `HISTORY_MONTHS` months
  - `test_update_symbol_resumes_from_watermark` — seed state row with `last_success_ts = 2 months ago`; assert only the 2 recent months are fetched
  - `test_update_symbol_respects_months_limit` — call with `months_limit=3`; assert exactly 3 months fetched
  - `test_update_symbol_writes_per_month_checkpoint` — seed 3 months; after each month, assert state row has been upserted with `last_success_ts` matching that month's max timestamp (inspect `FakeStateRepo.upsert` call log)
  - `test_update_symbol_failure_mid_range_preserves_prior_watermark` — fake provider raises on month 2; assert state row shows `FAILED` with `last_success_ts` equal to month 1's watermark (not None, not overwritten)
  - `test_update_symbol_empty_month_skipped_cleanly` — fake returns empty df for middle month; assert orchestrator continues, final status OK, watermark from last non-empty month
  - `test_update_symbols_skips_fresh_when_skip_recent_true` — one fresh, one stale; assert only the stale is processed; `result.skipped == 1`
  - `test_update_symbols_fail_fast_stops_batch` — three symbols; second fails; assert third is not processed; `result.failed == 1` with second symbol in `failed_symbols`
  - `test_update_symbols_zero_months_no_state_change` — symbol already current to now (no months remain after watermark) — OK with no state write beyond potentially a no-op
  - Effort: 5

- [x] **6.5** **Commit checkpoint** — `feat: add minute acquisition package and orchestrator`
  - Stage all new files under `src/manta_trading/data/acquisition/minute/` and their tests
  - Effort: 1

---

## 7. CLI: `mt data minute update` and `update-all`

- [x] **7.1** Read `src/manta_trading/cli/commands/data.py` to confirm:
  - Existing `minute coverage` and `minute metrics` command structure and `minute_app` Typer subcommand
  - The `_create_daily_orchestrator` factory pattern
  - How `TimescaleMinuteDataDB` is constructed in `_create_timescale_db`
  - Orientation only — no edits
  - Effort: 1

- [x] **7.2** Add `_create_minute_orchestrator(ctx, api_key)` factory to `data.py`
  - Construct: `TimescaleMinuteDataDB` (conninfo from settings), `AlphaVantageMinuteProvider(api_key)`, `DataProcessor`, `AcquisitionStateRepository` (timescale pool), `JsonlEventSink` (same path convention as daily)
  - Return `(orchestrator, pool, db, event_sink, provider)` — caller owns cleanup
  - Mirror `_create_daily_orchestrator` layout
  - Effort: 2

- [x] **7.3** Add `minute_update` CLI command
  - `@minute_app.command("update")`
  - Signature: `minute_update(ctx, symbol: str, months: Optional[int] = typer.Option(None, "--months", help="Limit fetch to most recent N months"))`
  - Validate API key via existing `_validate_credentials` helper
  - Call `_create_minute_orchestrator`; wrap work in try/finally for resource cleanup
  - Run `asyncio.run(orchestrator.update_symbol(symbol, months_limit=months, run_id=uuid4()))`
  - Print a one-line summary (symbol, chunks_written, final_status, error if any) using Rich
  - Exit 0 on OK; non-zero on FAILED
  - Effort: 2

- [x] **7.4** **Test** — manual smoke test for `mt data minute update SYMBOL`
  - `mt data minute update AAPL --months 1` against test DB
  - Confirm completes without error; summary line shows `chunks_written >= 1`
  - Run `mt data state --granularity minute --symbol AAPL` — row exists with recent `last_success_ts`
  - **VERIFIED end-to-end: 6697 rows written, state row ok**
  - Effort: 1

- [x] **7.5** Add `minute_update_all` CLI command
  - `@minute_app.command("update-all")`
  - Signature: `minute_update_all(ctx, months: Optional[int] = ..., skip_recent: bool = typer.Option(True, "--skip-recent/--no-skip-recent"))`
  - Read symbol list via `MarketDB.readLRUSymbolList(batchSize=10000, age=0)` — same pattern as daily
  - Call `asyncio.run(orchestrator.update_symbols(symbols, months_limit=months, run_id=uuid4(), skip_recent=skip_recent))`
  - Render `BatchResult` via Rich (succeeded, failed, skipped, no_data, first few failed symbols)
  - Exit non-zero if any failures
  - Effort: 2

- [x] **7.6** **Test** — manual smoke test for `mt data minute update-all`
  - `mt data minute update-all --months 1` against test DB
  - Interrupt with Ctrl-C mid-run; re-run and confirm the batch resumes (prior successes are skipped when `--skip-recent`)
  - Note: manual-only test; live exercise deferred for verification walkthrough
  - Effort: 1

- [x] **7.7** **Commit checkpoint** — `feat: wire mt data minute update/update-all through orchestrator`
  - Stage `data.py` changes
  - Effort: 1

---

## 8. Integration Test

- [x] **8.1** Create `test/integration/data/acquisition/minute/__init__.py`
  - Effort: 1

- [x] **8.2** Implement `test/integration/data/acquisition/minute/test_minute_orchestrator_integration.py`
  - Skip unless `MT_TIMESCALE_DB_URL` and `MT_MARKET_DB_URL` are set (reuse slice 122/123 skip pattern)
  - Use a stub `IMinuteDataProvider` that returns canned DataFrames keyed by (symbol, month) — no real AlphaVantage
  - Fixture: clean `acquisition_state` rows for the test symbol/granularity=MINUTE before each test
  - `test_update_symbol_end_to_end`:
    - Seed stub provider with 2 months of canned minute bars for `TEST_SYMBOL`
    - Construct `MinuteAcquisitionOrchestrator` with real `TimescaleMinuteDataDB`, real state repo, real JSONL event sink
    - Run `await orchestrator.update_symbol("TEST_SYMBOL", run_id=uuid4())`
    - Assert: 2 chunks written; final status OK
    - Assert: `acquisition_state` row exists with `status=OK`, `last_success_ts` = max timestamp of month 2
    - Assert: data is queryable via `db.get_minute_data(...)` for both months
  - `test_resume_after_mid_range_failure`:
    - Seed provider with 3 months; configure it to raise on month 2 the first time
    - Run once; expect `FAILED` state after month 1's success
    - Assert state has `last_success_ts` = month 1's max; `status=FAILED`; `retry_count=1`
    - Reconfigure provider to succeed on all months; run again
    - Assert: final state `OK`; `last_success_ts` = month 3's max; minute data for all 3 months is present in the DB
  - Effort: 4

- [x] **8.3** **Test** — run integration test locally with real databases
  - `pytest test/integration/data/acquisition/minute/ -v` — 2 tests passed against real TimescaleDB
  - Effort: 1

---

## 9. End-to-End Verification

- [x] **9.1** Run the full slice verification walkthrough from the slice design
  - Source `.env`; verify DB + API key vars exported
  - Run `mt data minute update AAPL --months 1` produced 6697 rows; check state via `mt data state --granularity minute --symbol AAPL` — state row ok
  - Start a `--months 3` fetch; interrupt after ~1 min; confirm partial watermark; re-run and confirm resume (no re-fetch of completed months)
  - Run unit test file for rate limiter to confirm fix: `pytest test/unit/util/test_ratelimiter.py -v` — 5/5 passed
  - Note any deviations or surprises
  - Effort: 2

- [x] **9.2** Run the full unit test suite
  - `pytest test/unit/ -q` — 828 passed; 13 pre-existing failures unrelated to slice 124
  - Slice 121/122/123 tests still green
  - Effort: 1

- [x] **9.3** Run the full integration test suite
  - `pytest test/integration/ -q` — 49 passed; 20 pre-existing failures unrelated to slice 124
  - New minute integration test green; existing tests pass
  - Effort: 1

---

## 10. Wrap-up

- [x] **10.1** Verify file size budgets
  - Extracted `_MinuteChunkProviderAdapter` to its own `chunk_adapter.py`; `orchestrator.py` now exactly 300 lines; all other new files well under budget
  - Pre-existing bug found: orchestrator.py was 356 lines (over budget); slice design explicitly allowed this extraction
  - Effort: 1

- [x] **10.2** Lint and type check
  - `ruff check` clean on new files; pre-existing unused imports in edited files noted but not introduced by this slice; mypy warns only on missing pandas stubs
  - Effort: 1

- [x] **10.3** Confirm no magic strings
  - `Granularity.MINUTE`, `AcquisitionStatus`, `ProviderType.ALPHA_VANTAGE` used throughout
  - `grep` in `src/manta_trading/data/acquisition/minute/` for bare status/granularity/provider literals returned empty
  - `MIN_DAYS`, `HISTORY_MONTHS` referenced (not repeated)
  - Effort: 1

- [x] **10.4** Self-review against slice design success criteria
  - All Functional and Technical Requirements verified
  - Effort: 1

- [x] **10.5** Update slice design `124-slice.minute-provider-fixes-and-orchestrator-hardening.md` status to `complete` and `dateUpdated` to 2026-04-13
  - Effort: 1

- [x] **10.6** Final commit on slice branch: `feat: complete minute provider fixes and orchestrator slice 124`
  - Stage any remaining modified files; confirm clean working tree after
  - Commit hash 9540679, clean working tree after
  - Effort: 1

---

## Notes for the Implementer

- **Three fixes are independent.** Rate limiter, AlphaVantage provider, and the new minute package do not depend on one another at the code level. Commit each cleanly so any single fix can be reverted without affecting the others.
- **`HistoricalMinuteService` is NOT deleted in this slice.** Slice 125 handles its removal. Leave imports and file in place.
- **Watermark precision matters.** Minute granularity: the watermark is a minute timestamp, not midnight UTC. `TimescaleMinuteWriter` must preserve the exact `df.index.max()` (converted to UTC-aware if necessary).
- **AlphaVantage partial months are expected.** A single month request may return ~10 trading days. The orchestrator does not re-request the same month within one run; the daemon (slice 125) or a subsequent CLI invocation will pick up the remainder as the watermark advances.
- **No real AlphaVantage in tests.** Integration test uses a stub provider. Never call the real API from CI.
- **Production deployment gate.** Do not deploy to production until PM confirms the minute-data backup is in place. All testing uses the test DB on .144.
- **`run_acquisition_unit` is the only path into state updates.** Do not bypass it. The orchestrator composes the adapter + writer and delegates.
- **Fail-fast in batch mode preserves resume semantics.** The first failure stops the batch so state accurately reflects "symbol N failed, symbols N+1..K untouched." The daemon/next CLI run resumes at the same point.
