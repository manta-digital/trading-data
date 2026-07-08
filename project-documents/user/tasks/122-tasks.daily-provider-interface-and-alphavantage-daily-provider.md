---
docType: slice-tasks
slice: daily-provider-interface-and-alphavantage-daily-provider
project: trading
parent: user/slices/122-slice.daily-provider-interface-and-alphavantage-daily-provider.md
dependencies: [100, 121, 900]
dateCreated: 20260411
dateUpdated: 20260411
status: complete
---

# Tasks: Daily Provider Interface and AlphaVantage Daily Provider

## Context

First real use of the slice 121 orchestrator core. This slice defines `IDailyDataProvider`, implements `AlphaVantageDailyProvider` against `TIME_SERIES_DAILY_ADJUSTED`, builds a `MarketDB`-backed `ChunkWriter`, and composes them into `DailyAcquisitionOrchestrator`. The existing `mt data daily update`, `update-all`, and `update-file` CLI commands are rewired onto the new orchestrator so every invocation updates `acquisition_state` and resumes correctly on failure.

Daily is the simplest case — one fetch per symbol, no pagination — so it is the right place to prove the orchestrator pattern end-to-end before slice 124 introduces minute pagination. `marketservice.py` stays on disk until a later slice removes it; this slice only stops the CLI daily commands from calling its OHLCV methods. `daily_symbols` still uses `MarketService.updateSymbolList` and is untouched.

See parent slice design for full rationale, interface contracts, and the verification walkthrough.

## Task Order Rationale

Protocol first (the contract provider and writer both implement), then the provider (independent, mockable), then the writer adapter (independent, mockable with fake MarketDB), then the orchestrator that composes all three via slice 121's `run_acquisition_unit`, then CLI rewiring (the user-visible change), then the integration test that pins the resume property end-to-end. Each implementation task is immediately followed by its test task.

---

## 1. Module Skeleton and Shared Types

- [x] **1.1** Create directory `src/manta_trading/data/acquisition/daily/` with `__init__.py`
  - Export nothing yet; just establish the module
  - Effort: 1

- [x] **1.2** Create `src/manta_trading/data/acquisition/daily/provider.py`
  - Define `IDailyDataProvider` Protocol with three methods:
    - `async def fetch_daily_ohlcv(self, symbol: str, *, output_size: str) -> pd.DataFrame` — returns DataFrame indexed by trading date with canonical columns (`open, high, low, close, adjusted_close, volume, dividend_amount, split_coefficient`), sorted ascending, duplicate-free. Raises on transport/validation errors.
    - `def validate_response(self, raw_data: dict) -> ValidationResult`
    - `def get_rate_limits(self) -> RateLimitInfo`
  - Re-export `ValidationResult` and `RateLimitInfo` from `manta_trading.data.historical_minute.provider` — do not duplicate the dataclasses. Document in the module header that these are shared with the minute provider.
  - Effort: 2

- [x] **1.3** Create `src/manta_trading/data/acquisition/daily/freshness.py`
  - Define `MIN_DAYS = 2` and `RECENT_DAYS = 100` module-level constants (single source of truth — referenced from the orchestrator and tests, never duplicated)
  - Define `_resolve_output_size(last_success_ts: datetime | None, *, recent_days: int = RECENT_DAYS) -> str`:
    - `None` → `"full"`
    - gap ≤ `recent_days` (date diff in days) → `"compact"`
    - otherwise → `"full"`
  - Define `_is_fresh(last_success_ts: datetime | None, *, min_days: int = MIN_DAYS) -> bool` — True iff `last_success_ts` is within `min_days` days of today
  - Pure functions, no I/O. Use `datetime.date.today()` for "today" (safe to monkey-patch in tests if needed, or pass in `today=` override)
  - Effort: 2

- [x] **1.4** **Test** — `test/unit/data/acquisition/daily/test_freshness.py`
  - `_resolve_output_size` parametrized: None → "full"; gap 0/1/100 → "compact"; gap 101 → "full"; gap 365 → "full"
  - `_is_fresh` parametrized: None → False; today → True; yesterday → True; 2 days ago → False (boundary — default `min_days=2` means "strictly less than 2")
  - Confirm both functions are pure (no DB access, no network)
  - Confirm `MIN_DAYS` and `RECENT_DAYS` are the only magic numbers in the module
  - Effort: 1

---

## 2. AlphaVantage Daily Provider

- [x] **2.1** Create `src/manta_trading/data/acquisition/daily/providers/` with `__init__.py`
  - Effort: 1

- [x] **2.2** Implement `AlphaVantageDailyProvider` in `src/manta_trading/data/acquisition/daily/providers/alphavantage.py`
  - Constructor: `__init__(self, api_key: str, *, requests_per_minute: int = 30, rate_limiter: RateLimiter | None = None, client: httpx.AsyncClient | None = None)`
    - If `rate_limiter` is None, construct a new `RateLimiter(requests_per_minute, 60)`
    - Store the injected or lazy `client` (create on first use in `aclose()`-safe way, mirror `AlphaVantageMinuteProvider`)
    - Raise `ValueError` if `api_key` is empty — no fallback to env vars here (the CLI layer reads settings)
  - `async def fetch_daily_ohlcv(self, symbol: str, *, output_size: str = "compact") -> pd.DataFrame`:
    - Validate `symbol` non-empty; raise `ValueError` if empty
    - Validate `output_size` in `{"compact", "full"}`; raise `ValueError` otherwise
    - Acquire rate limiter via `async with self._rate_limiter:`
    - Build params dict: `function=TIME_SERIES_DAILY_ADJUSTED`, `symbol`, `outputsize=output_size`, `apikey=self._api_key`
    - GET `https://www.alphavantage.co/query` with 30s timeout
    - On non-200: raise `RuntimeError` with status code and symbol
    - Parse JSON; run `validate_response` — raise `RuntimeError` with the joined error messages on invalid
    - Convert `"Time Series (Daily)"` dict to a DataFrame indexed by date:
      - Columns (in order): `open, high, low, close, adjusted_close, volume, dividend_amount, split_coefficient`
      - Values: float for all (mirrors legacy `_getDailyOHLCV` at `src/manta_trading/api/alphavantage/alphavantageapi.py:206`)
      - Sort ascending by index; confirm duplicate-free
    - Return the DataFrame
  - `validate_response(self, raw_data: dict) -> ValidationResult`:
    - Mirror `AlphaVantageMinuteProvider.validate_response` — check `"Error Message"`, `"Note"` (rate limit), `"Information"`
    - Empty `"Time Series (Daily)"` is an **error**, not a warning
    - Completely empty response is an error
  - `get_rate_limits(self) -> RateLimitInfo`:
    - Return `RateLimitInfo(requests_per_minute=self._rpm, requests_per_day=None, current_usage=self._current_usage, reset_time=None)`
    - Tracking `current_usage` precisely is out of scope; return 0 or a simple counter
  - `async def aclose(self) -> None` — close the httpx client if this provider owns it
  - **Do not** depend on `AlphavantageAPI` or `MarketService`. This is a clean replacement for the daily fetch path only.
  - Effort: 3

- [x] **2.3** **Test** — `test/unit/data/acquisition/daily/test_provider_alphavantage.py`
  - Use `httpx.MockTransport` to stub responses (or inject a stub `AsyncClient`) — no real network in any test
  - `test_fetch_happy_path` — mock returns a valid `TIME_SERIES_DAILY_ADJUSTED` payload → returns DataFrame with expected columns, ascending dates, float dtypes, correct row count
  - `test_fetch_rate_limit_response` — `{"Note": "Thank you for using Alpha Vantage..."}` → raises `RuntimeError`, no partial DataFrame returned
  - `test_fetch_error_message_response` — `{"Error Message": "Invalid API call"}` → raises `RuntimeError`
  - `test_fetch_information_response` — `{"Information": "..."}` → raises `RuntimeError`
  - `test_fetch_empty_time_series` — `{"Time Series (Daily)": {}}` → raises (no silent empty DataFrame)
  - `test_fetch_http_error` — mock returns 500 → raises `RuntimeError` with status info
  - `test_fetch_output_size_passthrough` — call with `output_size="full"` → captured request has `outputsize=full`
  - `test_fetch_output_size_compact_default` — default value is `"compact"`; captured request has `outputsize=compact`
  - `test_fetch_invalid_output_size` — `output_size="huge"` → raises `ValueError` before any HTTP call
  - `test_fetch_empty_symbol` — `symbol=""` → raises `ValueError`
  - `test_constructor_requires_api_key` — `api_key=""` → raises `ValueError`
  - `test_constructor_accepts_injected_rate_limiter` — pass a shared `RateLimiter` → provider uses it (confirm by checking the instance identity or that the limiter's acquire/release was called)
  - `test_validate_response_variants` parametrized — happy / error-message / note / information / empty / missing-series, each returns the expected `ValidationResult.is_valid` and errors
  - `test_aclose_closes_owned_client` — confirm the client is closed and a subsequent call would create a new one (or raises cleanly)
  - Effort: 3

---

## 3. MarketDB Daily Writer (ChunkWriter Adapter)

- [x] **3.1** Create `src/manta_trading/data/acquisition/daily/writer.py`
  - Define `MarketDBDailyWriter` as a dataclass (or regular class) with fields `db: MarketDB` and `symbol: str`
  - One instance per symbol per orchestrator call (cheap, no pooling needed)
  - Effort: 1

- [x] **3.2** Implement `write(chunk: FetchedChunk) -> ChunkResult`
  - Interpret `chunk.rows` as a pandas DataFrame with the canonical daily columns
  - Empty or None DataFrame → return `ChunkResult(last_written_ts=None, rows_written=0)` without calling `MarketDB`
  - Call `self.db.writeDailyOHLCVAdjusted(self.symbol, df)` — note this returns `bool`
  - On `False` return: raise `RuntimeError(f"MarketDB write failed for {self.symbol}")` so the orchestrator records it as `CHUNK_FAILED` (never silently marking the row `ok`)
  - On `True`: compute `last_ts` as the max date in the DataFrame index, normalized to midnight UTC (daily data has day resolution; no sub-day timestamp to preserve)
  - Return `ChunkResult(last_written_ts=last_ts, rows_written=len(df))`
  - Use `asyncio.to_thread`? **No** — `write` is already sync; the orchestrator handles `asyncio.to_thread` wrapping
  - Effort: 2

- [x] **3.3** **Test** — `test/unit/data/acquisition/daily/test_writer.py`
  - Use a `FakeMarketDB` with a scripted `writeDailyOHLCVAdjusted(symbol, df) -> bool`
  - `test_write_happy_path` — fake returns True, DataFrame with 5 rows → `ChunkResult(last_written_ts=<max date utc midnight>, rows_written=5)`, fake called once with the expected symbol and DataFrame
  - `test_write_empty_df` — empty DataFrame → `ChunkResult(last_written_ts=None, rows_written=0)`, fake **not** called
  - `test_write_none_rows` — `chunk.rows=None` → same as empty, fake not called
  - `test_write_db_failure_raises` — fake returns False → writer raises `RuntimeError` mentioning the symbol; the `last_written_ts` is not computed
  - `test_last_ts_is_max_date_utc_midnight` — DataFrame with mixed date ordering (not pre-sorted) → `last_written_ts` equals the latest trading date at `00:00:00+00:00`
  - `test_last_ts_preserves_timezone` — indexed by tz-aware dates → result is still tz-aware UTC
  - Effort: 2

---

## 4. Daily Acquisition Orchestrator

- [x] **4.1** Create `src/manta_trading/data/acquisition/daily/orchestrator.py`
  - Effort: 1

- [x] **4.2** Define `BatchResult` dataclass
  - Fields: `succeeded: int`, `failed: int`, `skipped: int`, `no_data: int`, `failed_symbols: list[str]`
  - Effort: 1

- [x] **4.3** Define `_DailyChunkProviderAdapter` (internal, same module)
  - Wraps an `IDailyDataProvider` + a resolved `output_size` into a slice 121 `ChunkProvider` that yields exactly one `FetchedChunk` per call
  - `async def fetch_chunks(work_item: WorkItem) -> AsyncIterator[FetchedChunk]`:
    - Calls `provider.fetch_daily_ohlcv(work_item.symbol, output_size=self._output_size)`
    - Yields a single `FetchedChunk(rows=df, chunk_start=work_item.time_range_start, chunk_end=work_item.time_range_end)`
    - Does not swallow exceptions — they propagate to the orchestrator core which converts them to `CHUNK_FAILED` events
  - This is an implementation detail; not exported from the package
  - Effort: 2

- [x] **4.4** Implement `DailyAcquisitionOrchestrator` class
  - Constructor: `__init__(self, provider: IDailyDataProvider, db: MarketDB, state_repo: AcquisitionStateRepository, event_sink: EventSink, *, recent_days_threshold: int = RECENT_DAYS, min_days_fresh: int = MIN_DAYS, provider_id: str = ProviderType.ALPHA_VANTAGE.value)`
  - Store all dependencies; do not open any connections in the constructor
  - `provider_id` default references the enum so callers don't pass magic strings
  - Effort: 2

- [x] **4.5** Implement `async def update_symbol(self, symbol: str, *, output_size: str | None = None, run_id: UUID) -> AcquisitionResult`
  - Resolve `output_size` if None:
    - Prefer `state_repo.get(symbol, Granularity.DAILY, provider_id).last_success_ts`
    - Fall back to `self._db.readLastUpdatedDay(symbol)` as a bridge for symbols with data but no state row yet (converted to a datetime at midnight UTC)
    - Pass through `_resolve_output_size(...)` from `freshness.py`
  - Build `WorkItem(symbol, Granularity.DAILY, self._provider_id, time_range_start=epoch, time_range_end=utcnow())` where `epoch` = `datetime(1970, 1, 1, tzinfo=utc)` and `utcnow()` uses `datetime.now(tz=timezone.utc)`
  - Build `_DailyChunkProviderAdapter(self._provider, output_size)`
  - Build `MarketDBDailyWriter(db=self._db, symbol=symbol)`
  - Call `await run_acquisition_unit(work_item, chunk_provider, writer, self._state_repo, self._event_sink, run_id)`
  - Return the `AcquisitionResult` unchanged
  - Effort: 3

- [x] **4.6** Implement `async def update_symbols(self, symbols: list[str], *, output_size: str | None = None, run_id: UUID, skip_recent: bool = True) -> BatchResult`
  - Initialize counters: `succeeded=0, failed=0, skipped=0, no_data=0, failed_symbols=[]`
  - Iterate `symbols` sequentially (no concurrency in this slice)
  - For each symbol:
    - If `skip_recent`:
      - Fetch existing state via `state_repo.get(symbol, Granularity.DAILY, provider_id)`
      - If `existing is not None` and `existing.status == AcquisitionStatus.OK` and `_is_fresh(existing.last_success_ts, min_days=self._min_days_fresh)`:
        - Increment `skipped`; log info; continue
    - Call `await self.update_symbol(symbol, output_size=output_size, run_id=run_id)`
    - Tally:
      - `final_status == OK` and `chunks_written > 0` → `succeeded += 1`
      - `final_status == OK` and `chunks_written == 0` → `no_data += 1` (empty DataFrame returned)
      - `final_status == FAILED` → `failed += 1`; `failed_symbols.append(symbol)`
  - Return `BatchResult`
  - **No retry.** A failed symbol is failed; the next invocation retries it because its state row is `FAILED`, not `OK`.
  - Effort: 3

- [x] **4.7** **Test** — `test/unit/data/acquisition/daily/test_orchestrator.py`
  - Use fakes: `FakeDailyProvider` (configurable per-symbol responses), `FakeMarketDB` (records writes), `FakeStateRepo` (in-memory dict keyed by PK tuple), `NullEventSink` from slice 121
  - `test_update_symbol_happy_path` — provider yields a DataFrame with 5 rows, max date 2026-04-10 → `AcquisitionResult.final_status == OK`, `chunks_written == 1`; state row for (sym, daily, alphavantage) has `status=OK`, `last_success_ts == 2026-04-10T00:00:00Z`; `FakeMarketDB.writeDailyOHLCVAdjusted` called once
  - `test_update_symbol_fetch_failure` — provider raises `RuntimeError("rate limit")` → `final_status == FAILED`, state `status=FAILED`, `error_message` contains `"rate limit"`, `retry_count == 1`, `writeDailyOHLCVAdjusted` **not** called
  - `test_update_symbol_write_failure` — provider succeeds, fake db returns False → `final_status == FAILED`, state shows `FAILED`, `last_success_ts` unchanged from prior value (seed state with a prior `last_success_ts` and confirm it is preserved)
  - `test_update_symbol_empty_df` — provider returns empty DataFrame → orchestrator handles via writer's empty-df branch (`ChunkResult(None, 0)`); state transitions to `OK` with `last_success_ts=None` (or prior value — confirm the writer/orchestrator behavior and pick the right assertion)
  - `test_update_symbol_resolves_output_size_from_state` — seed state with `last_success_ts = today - 50 days`, call with `output_size=None` → provider is called with `output_size="compact"` (captured from the fake)
  - `test_update_symbol_resolves_output_size_no_state` — no state row, `readLastUpdatedDay` returns None → provider called with `output_size="full"`
  - `test_update_symbol_resolves_output_size_from_db_fallback` — no state row, `readLastUpdatedDay` returns a date 200 days old → provider called with `output_size="full"`
  - `test_update_symbol_explicit_output_size_wins` — pass `output_size="full"` even with fresh state → provider called with `"full"` (explicit override beats resolution)
  - `test_update_symbols_skips_fresh` — seed state with `AAPL: status=OK, last_success_ts=now` → `update_symbols(["AAPL", "MSFT"])` calls provider only for MSFT; `BatchResult.skipped == 1`, `succeeded == 1`
  - `test_update_symbols_retries_failed` — seed state with `AAPL: status=FAILED, last_success_ts=now` → provider is called for AAPL regardless of freshness
  - `test_update_symbols_skip_recent_false` — same seed as above but `skip_recent=False` → provider is called for AAPL even with fresh OK state
  - **`test_update_symbols_resume_after_crash`** (critical) — run `update_symbols(["A","B","C"])` with a provider that succeeds on A, raises on B, is never reached for C. First call: A→OK, B→FAILED, C→(never called). Assert `BatchResult.succeeded==1, failed==1, skipped==0`; state has A=OK, B=FAILED, C=none. Second call with a fresh provider that succeeds for all three, same state repo. Assert A is skipped (state is OK+fresh), B is retried (state is FAILED), C is fetched (no state). Final state: A=OK, B=OK, C=OK. `BatchResult: succeeded=2, skipped=1, failed=0`. **This is the most important test in the slice.**
  - `test_update_symbols_sequential_rate_limit_respected` — confirm the provider's rate limiter is acquired per symbol (inspect the fake limiter's call count or sequence)
  - Effort: 4

---

## 5. CLI Rewiring

- [x] **5.1** Read `src/manta_trading/cli/commands/data.py` to confirm current structure of `daily_update`, `daily_update_all`, `daily_update_file`, and the helper `_create_market_service`
  - No edits yet; orientation
  - Effort: 1

- [x] **5.2** Add a private helper `_create_daily_orchestrator(ctx) -> tuple[DailyAcquisitionOrchestrator, ConnectionPool, MarketDB, JsonlEventSink | NullEventSink]`
  - Resolve settings from `ctx.obj["settings"]`
  - Validate credentials using the existing `_validate_credentials(ctx, json_output)` pattern
  - Build `MarketDB(conninfo=settings.market_db_url)` and enter its context manager (the helper returns the entered db; the caller is responsible for `__exit__`)
  - Build `ConnectionPool(settings.timescale_db_url, min_size=1, max_size=2)` → `AcquisitionStateRepository(pool)`
  - Build `AlphaVantageDailyProvider(api_key=settings.alphavantage_api_key)`
  - Resolve the event sink path:
    - Prefer `settings.data_dir / "events" / "acquisition.jsonl"` if `settings.data_dir` is set
    - Fallback to `Path.home() / ".local/share/manta-trading/events/acquisition.jsonl"`
    - If neither parent directory exists and cannot be created, swap in `NullEventSink` and log a warning once
    - Create parent dirs via `Path.mkdir(parents=True, exist_ok=True)`
  - Build `DailyAcquisitionOrchestrator(provider, db, state_repo, event_sink)`
  - Return the orchestrator plus the handles the caller must close (`pool`, `db`, `event_sink`, `provider`)
  - **Cleanup is the caller's responsibility** — the CLI command uses try/finally to close everything
  - Effort: 3

- [x] **5.3** Rewrite `daily_update` command body
  - Replace the existing `service = _create_market_service(api_key, ctx)` and `asyncio.run(service.updateDailyOHLCVSimpleGap(...))` path
  - Build orchestrator via `_create_daily_orchestrator`
  - Generate `run_id = uuid4()`
  - Call `asyncio.run(orchestrator.update_symbol(symbol, output_size=output_size, run_id=run_id))`
  - Print a concise result line: `"{symbol}: {final_status} — {chunks_written} chunk(s) written"` (or via the existing Rich output module if one is more consistent)
  - On `AcquisitionStatus.FAILED`: exit code 1
  - Wrap in try/finally to close pool, provider (`aclose`), marketdb, event sink
  - Effort: 2

- [x] **5.4** **Test** — manual smoke test for `daily_update` (document in slice walkthrough)
  - `mt data daily update AAPL` → success, state row visible via `mt data state --symbol AAPL`
  - `mt data daily update AAPL` again → succeeds, output mentions `compact` fetch
  - `mt data daily update NOTASYMBOL` → fails cleanly with exit code 1, no stack trace
  - Effort: 1

- [x] **5.5** Rewrite `daily_update_all` command body
  - Remove the `_create_market_service` path
  - Build orchestrator via `_create_daily_orchestrator`
  - Fetch symbol list from `db.readLRUSymbolList(batchSize=...)` — note: this slice preserves the existing batching/pagination strategy from `MarketService.updateDailyOHLCVAll`; the simplest port is to unwind one loop so `update_symbols` is called per batch, or to fetch *all* symbols once and hand them to `update_symbols` as a single list. Pick the simplest port that preserves the LRU ordering and delisted-symbol handling.
  - `asyncio.run(orchestrator.update_symbols(symbols, output_size=output_size, run_id=uuid4()))`
  - Print the summary line: `"{succeeded} succeeded, {failed} failed, {skipped} skipped, {no_data} no-data"`
  - If `failed > 0`: also list `failed_symbols` (first 20) and exit code 1
  - Wrap in try/finally to close all resources
  - Note: the legacy `age` flag is preserved. `--age N` is passed through to `readLRUSymbolList` — it filters the symbol list upstream, before the orchestrator sees it. The orchestrator's own freshness skip is additive.
  - Effort: 3

- [x] **5.6** **Test** — manual smoke test for `daily_update_all` (document in slice walkthrough)
  - Against a small symbol universe: run, observe summary line, confirm state rows exist for every touched symbol
  - Run again: most symbols should be `skipped` (because they are fresh); summary line reflects this
  - Effort: 1

- [x] **5.7** Rewrite `daily_update_file` command body
  - Read symbols from the provided file (preserve the legacy `inputFile` parsing)
  - Call `asyncio.run(orchestrator.update_symbols(symbols, ...))` the same way as `daily_update_all`
  - Same summary line and exit code rules
  - Effort: 2

- [x] **5.8** **Test** — manual smoke test for `daily_update_file` (document in slice walkthrough)
  - Create a 3-line file, run the command, confirm all three symbols appear in `mt data state` and the summary line is correct
  - Effort: 1

- [x] **5.9** Remove `_create_market_service` helper **if and only if** `daily_symbols` can live without it
  - If `daily_symbols` still needs `MarketService.updateSymbolList`, keep the helper but narrow its responsibility (only used by `daily_symbols`)
  - Do **not** remove `marketservice.py` — that is a later slice's job
  - Do **not** touch `daily_symbols`, `daily_migrate`, or `daily_coverage`
  - Effort: 1

- [x] **5.10** **Test** — confirm no regressions in untouched commands
  - `mt data daily symbols`, `mt data daily coverage`, `mt data daily migrate` — all still run
  - `mt data minute coverage`, `mt data state`, `mt data instruments list`, `mt data calendars list` — all still run
  - Effort: 1

---

## 6. Integration Test: Resume After Crash

- [x] **6.1** Create `test/integration/data/acquisition/daily/__init__.py`
  - Effort: 1

- [x] **6.2** Implement `test/integration/data/acquisition/daily/test_daily_update_resume.py`
  - Skip unless both `MT_TIMESCALE_DB_URL` and `MT_MARKET_DB_URL` are set (existing pattern in other integration tests)
  - Use a stub `IDailyDataProvider` — NOT a real AlphaVantage client. CI must not require the real API.
  - Fixture: apply migration 770 (or assume it is applied), clean `acquisition_state` and `dailyohlcvadjusted` of the test symbols
  - `test_cli_update_all_resume`:
    - Seed `symbol_list` with 3 test symbols (e.g. `TST1`, `TST2`, `TST3`) via direct SQL or the existing MarketDB helper if it exists
    - Construct `DailyAcquisitionOrchestrator` directly (not via CLI subprocess) with a stub provider that succeeds for `TST1`, raises for `TST2`, is never called for `TST3`
    - Run 1: `await orchestrator.update_symbols(["TST1","TST2","TST3"], run_id=uuid4())`
    - Assert: `BatchResult.succeeded == 1`, `failed == 1`, `skipped == 0`, `no_data == 0`
    - Assert: state row for `TST1` is `OK`, `TST2` is `FAILED` with an error message, `TST3` has no state row
    - Assert: row count in `dailyohlcvadjusted` for `TST1` == N (the stub's seed count)
    - Run 2: same orchestrator, fresh stub provider that succeeds for all three, same state repo
    - Assert: `BatchResult.succeeded == 2`, `failed == 0`, `skipped == 1` (TST1 is fresh), `no_data == 0`
    - Assert: state rows for all three are `OK`
    - Assert: row count in `dailyohlcvadjusted` for `TST1` is **unchanged** (the existing data was not re-written; `INSERT ... ON CONFLICT DO NOTHING` is a no-op because the stub returns the same rows)
    - Teardown: remove test symbols and acquisition_state rows
  - Effort: 4

- [x] **6.3** **Test** — run the integration test locally with real databases and confirm green
  - `pytest test/integration/data/acquisition/daily/ -v`
  - Effort: 1

---

## 7. End-to-End Verification

- [x] **7.1** Run the full slice walkthrough from the slice design's "Verification Walkthrough" section against a test DB
  - Single symbol (AAPL) → state row visible → re-run → compact fetch
  - Batch with a small universe → skip fresh, retry failed, summary line prints
  - Regression commands still work
  - Event JSONL file is populated with `run_started`, `chunk_ok`, `run_finished` entries
  - Effort: 2

- [x] **7.2** Run the full unit test suite
  - `pytest test/unit/` — all tests pass
  - Slice 121 tests still green (no regressions)
  - New daily tests included in the count
  - Effort: 1

- [x] **7.3** Run the full integration test suite (where DBs available)
  - `pytest test/integration/` — new daily resume test green; existing integration tests still green
  - Effort: 1

---

## 8. Wrap-up

- [x] **8.1** Verify file size budgets — each new module ≤ ~300 lines, functions ≤ ~50 lines
  - `provider.py`, `providers/alphavantage.py`, `writer.py`, `orchestrator.py`, `freshness.py` all within budget
  - If `orchestrator.py` approaches 200 lines, consider splitting `_DailyChunkProviderAdapter` or the batch helper into a separate file — already planned as an escape hatch in the slice design
  - Effort: 1

- [x] **8.2** Lint / type check per project standards
  - `ruff check`, `ruff format --check`, mypy/pyright strict mode on the new files — zero errors
  - Effort: 1

- [x] **8.3** Confirm no magic strings — every `"daily"`, `"ok"`, `"failed"`, `"compact"`, `"full"`, `"alphavantage"` comes from an enum or named constant
  - `Granularity.DAILY`, `AcquisitionStatus.OK/FAILED`, `"compact"/"full"` from `freshness.py` constants (or a small `OutputSize` StrEnum if the review prefers), `ProviderType.ALPHA_VANTAGE`
  - `MIN_DAYS` and `RECENT_DAYS` are the only numeric magic values and live in `freshness.py`
  - Effort: 1

- [x] **8.4** Self-review against slice design success criteria — every bullet checked off
  - Effort: 1

- [x] **8.5** Update the slice design document status to `complete` and set `dateUpdated` to today
  - Effort: 1

- [x] **8.6** Commit on slice branch with semantic message: `feat: add daily provider interface and alphavantage daily provider`
  - Effort: 1

---

## Notes for the Implementer

- **Slice 121 is the contract.** Do not modify `run_acquisition_unit`, `ChunkProvider`, `ChunkWriter`, or the state/event types. If you feel the urge to bend them, that is a design signal worth surfacing before you start coding.
- **One chunk per symbol.** Daily has no pagination. The `_DailyChunkProviderAdapter` exists solely to fit the slice 121 `ChunkProvider` protocol; it always yields exactly one `FetchedChunk`.
- **Watermark = max trading date at midnight UTC.** Daily data has day resolution. Do not try to preserve a sub-day timestamp.
- **Empty DataFrame is not an error.** The provider raises on empty `"Time Series (Daily)"` responses (an actual API error). But a write of an already-up-to-date symbol may produce an empty DataFrame after dedup; the writer handles that by returning `ChunkResult(None, 0)` without calling the DB.
- **`writeDailyOHLCVAdjusted` is idempotent via `ON CONFLICT DO NOTHING`.** Re-running the fetch for a symbol that already has data is a no-op at the DB layer. The integration test's "row count unchanged" assertion depends on this.
- **No retry logic.** A failed CLI invocation prints the error and exits non-zero. Re-running the command is the retry mechanism. Retry policy is a slice 123 concern.
- **Share the rate limiter.** `AlphaVantageDailyProvider` accepts an injected `RateLimiter` specifically so slice 123 can wire a single 30 req/min budget shared with the minute provider without refactoring this interface.
- **`marketservice.py` stays on disk.** Do not delete it in this slice. `daily_symbols` still imports `MarketService` for `updateSymbolList`. Deletion is a later slice's problem.
- **No new CLI flags.** An `--no-skip`/`--force` override would be speculative here. Add it in slice 123 when the daemon's operational context makes it necessary.
- **Resume property is the slice's reason for existing.** `test_update_symbols_resume_after_crash` and `test_cli_update_all_resume` together pin the property. If they do not pass, the slice does not pass.
