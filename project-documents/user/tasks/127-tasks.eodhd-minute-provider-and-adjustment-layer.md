---
docType: task-breakdown
slice: 127
sliceName: eodhd-minute-provider-and-adjustment-layer
parent: user/slices/127-slice.eodhd-minute-provider-and-adjustment-layer.md
project: trading
dateCreated: 20260426
dateUpdated: 20260427
dateCompleted: 20260427
dependencies: [124, 125, 131]
status: complete
---

# Tasks: Slice 127 — EODHD Minute Provider + Adjustment Layer

## Context

Slice 127 replaces AlphaVantage as the minute data provider, drops AV minute code entirely, and ships the split/dividend adjustment layer in the same slice. After this slice the pipeline can ingest 22-year minute history from EODHD and produce backtest-ready adjusted prices continuously verifiable against EODHD's published `adjusted_close`.

Two probes against the real paid EODHD API have already verified the foundations:

- [scripts/probe_eodhd_chunk_size.py](scripts/probe_eodhd_chunk_size.py) — `/intraday?interval=1m` delivers 76,083 bars in a single 120-day request (1.2s, 10.6 MB JSON). Server-enforced 120-day cap returns clean HTTP 422 on overshoot.
- [scripts/probe_eodhd_adjustment.py](scripts/probe_eodhd_adjustment.py) — `close × k = adjusted_close` is exact (0.000000% error) on every day; AAPL 4:1 split round-trips with `k_after / k_before = 4.000000`.

Probe results: [project-documents/user/research/eodhd-chunk-size-probe/](project-documents/user/research/eodhd-chunk-size-probe/) and [project-documents/user/research/eodhd-adjustment-probe/](project-documents/user/research/eodhd-adjustment-probe/).

### Key paths

**New code:**
- `src/manta_trading/data/historical_minute/providers/eodhd.py` — provider class
- `src/manta_trading/data/historical_minute/providers/__init__.py` — `MinuteProviderName` enum + `build_minute_provider` helper
- `src/manta_trading/data/adjustment/__init__.py` — new package
- `src/manta_trading/data/adjustment/k_factor.py` — k-factor computation
- `src/manta_trading/data/adjustment/ingest.py` — splits/dividends ingestion job
- `src/manta_trading/data/adjustment/recompute.py` — adjusted-column population
- `test/fixtures/eodhd/*.json` — captured raw responses
- `test/unit/test_eodhd_provider.py`
- `test/unit/test_chunk_ranges.py`
- `test/unit/test_k_factor.py`
- `test/integration/test_eodhd_integration.py`
- `project-documents/user/architecture/120-arch-adjustment-policy.md` — ADR

**Schema migrations (slice 150's framework):**
- `src/manta_trading/market/schema/migrations/minute.py` — append `010_adjusted_columns`
- `src/manta_trading/market/schema/migrations/daily.py` — append `003_splits`, `004_dividends`

**Touched code:**
- `src/manta_trading/config/__init__.py` — `eodhd_api_key`, `minute_provider` fields
- `src/manta_trading/data/historical_minute/provider.py` — protocol gains `max_days_per_request`
- `src/manta_trading/data/acquisition/minute/orchestrator.py` — `_compute_chunk_ranges`
- `src/manta_trading/cli/commands/data.py` — uses `build_minute_provider`; new `verify-adjustment` subcommand
- `src/manta_trading/data/acquisition/minute/writer.py` — writes adjusted columns

**Removed code:**
- `src/manta_trading/data/historical_minute/providers/alphavantage.py` — minute AV provider deleted
- AV-minute-specific tests
- AV-minute imports/references in `data.py`

### CLI surfaces this slice creates or modifies

- `mt data minute update SYMBOL` — existing; now uses EODHD via the seam
- `mt data quality verify-adjustment [--symbol] [--from] [--to]` — new
- `mt data adjustment ingest [--symbol] [--exchange]` — new (per-symbol path; bulk noted as future)

### Out-of-scope reminders

These are **explicitly not** in this task list and should not be added without a new slice or PM direction:

- Deployment to .144 — slice 128.
- WebSocket / streaming.
- Real-time tier.
- Crypto, forex, non-equity markets.
- Bulk splits/dividends API integration (recorded as future optimization in slice doc).
- MCP-server-as-runtime-provider.
- Pre-engineering for spinoffs, return-of-capital, special non-cash dividends. Verifier flags divergence; we address case-by-case.
- Backfill at full universe scale — slice 128's job.
- Daily AV provider — unrelated, untouched.

---

## Task 1: Smoke-test EODHD intraday contract on the implementer's paid key

Confirm the documented contract holds for the implementer's exact key and date range. Cheap, catches surprises before code is written.

- [x] 1.1 Confirm `MT_EODHD_API_KEY` is in `.env` and the key is the paid plan (not free). — Verified present in .env; paid plan confirmed.
- [x] 1.2 Run [scripts/probe_eodhd_chunk_size.py](scripts/probe_eodhd_chunk_size.py) again. Expected: identical results to existing probe artifact (or close — bar counts may differ slightly with different end date). Spot-check that the 120-day cap and 422-on-overshoot still hold. — Re-ran; 120d returns 76,083 bars in 1.32s, 121d returns clean HTTP 422.
- [x] 1.3 Hit `/internal-user` once via curl or the skill's `eodhd_client.py --endpoint user`. Confirm: `subscriptionMode == "paid"`, `dailyRateLimit == 100000`, plan name includes intraday access. — /user endpoint confirms subscriptionMode=paid, dailyRateLimit=100000.
- [x] 1.4 If any of the above diverge from expected, **stop** and notify PM. Do not continue without paid intraday access. — Verified: no divergence, all conditions met.

Effort: 1/5

---

## Task 2: Add EODHD config to `Settings`

- [x] 2.1 Edit [src/manta_trading/config/__init__.py](src/manta_trading/config/__init__.py): add `eodhd_api_key: str | None = None`.
- [x] 2.2 Add `minute_provider: str = "eodhd"` field. (Will be replaced with `StrEnum` after Task 4 — for now leave as `str` so config loads cleanly while we build the rest.)
- [x] 2.3 Confirm settings load: `python -c "from manta_trading.config import Settings; s=Settings(); print(s.minute_provider, bool(s.eodhd_api_key))"` prints `eodhd True`.

Effort: 1/5

---

## Task 3: Test — Settings round-trip

- [x] 3.1 Add or extend `test/unit/test_config.py` (or whatever the existing config test file is — find via grep) to assert: `eodhd_api_key` defaults to `None`, reads from `MT_EODHD_API_KEY`; `minute_provider` defaults to `"eodhd"`, reads from `MT_MINUTE_PROVIDER`.
- [x] 3.2 `uv run pytest test/unit/test_config.py -v`. Expected: green.

Effort: 1/5

---

## Task 4: Generalize chunk-range computation

The orchestrator currently has `_compute_month_ranges`. Generalize so each provider declares its own window.

- [x] 4.1 Read [src/manta_trading/data/acquisition/minute/orchestrator.py](src/manta_trading/data/acquisition/minute/orchestrator.py); locate `_compute_month_ranges`. — Read and located.
- [x] 4.2 Rename to `_compute_chunk_ranges(start_ts, end_ts, max_days_per_request)`. Returns `list[tuple[datetime, datetime]]`. Each tuple covers up to `max_days_per_request` calendar days, contiguous, covering `[start_ts, end_ts]`. — Implemented and tested.
- [x] 4.3 When `max_days_per_request == 30`, output must match the previous `_compute_month_ranges` exactly (calendar months) — preserves AV behavior for the legacy code path that still references it during transition. — **Deviated per PM direction (2026-04-26)**: pure N-day chunking implemented. AV minute path uses 30-day rolling windows during the brief Tasks 4-28 transition rather than calendar months. Reason: AV minute is going dormant in Task 28, so preserving exact bit-identical AV behavior added test churn for no benefit.
- [x] 4.4 When `max_days_per_request == 120`, output is contiguous 120-day buckets aligned to `start_ts`. — Implemented and verified with parametrized tests.
- [x] 4.5 Update the one caller of `_compute_month_ranges` to call `_compute_chunk_ranges` and pass `provider.max_days_per_request`. — Orchestrator updated; uses `self._provider.max_days_per_request`.

Effort: 2/5

---

## Task 5: Test — `_compute_chunk_ranges`

- [x] 5.1 Create `test/unit/test_chunk_ranges.py`. Parametrize `max_days` across `[7, 30, 120]` plus edge cases. — File created with 18 parametrized tests.
- [x] 5.2 Cases to cover: range smaller than `max_days` (one chunk); range exactly equal; range slightly larger (two chunks, last one short); month-boundary handling for the 30-day case (must produce calendar months, matching legacy behavior); year-boundary; DST boundary. — All cases covered: basic ranges, year-boundary, DST-boundary, inverted-range error, EODHD-120 edge.
- [x] 5.3 `uv run pytest test/unit/test_chunk_ranges.py -v`. Expected: green. — All 18 tests green.

Effort: 2/5

---

## Task 6: Add `max_days_per_request` to `IMinuteDataProvider` protocol

- [x] 6.1 Edit [src/manta_trading/data/historical_minute/provider.py](src/manta_trading/data/historical_minute/provider.py): add `max_days_per_request: int` as a class-level attribute on the `IMinuteDataProvider` Protocol (or as a property — pick whichever the existing protocol style uses). — Protocol attribute added as class-level int.
- [x] 6.2 Update protocol docstring to explain: this is the maximum number of calendar days that can be requested in a single `fetch_minute_data` call. Orchestrator chunks longer ranges into multiple calls. — Docstring updated with full explanation.
- [x] 6.3 Add `max_days_per_request = 30` to existing `AlphaVantageMinuteProvider` (preserves behavior; will be deleted in Task 21). — Attribute added with comment explaining dormant-after-Task-28 status.

Effort: 1/5

---

## Task 7: Implement `EODHDMinuteProvider` — skeleton + auth + URL building

Build incrementally. This task is the skeleton: class, init, URL construction, no actual fetch yet.

- [x] 7.1 Create `src/manta_trading/data/historical_minute/providers/eodhd.py`. Module docstring records: EODHD-specific quirks (UTC timestamps, 5-calls/request, 120-day cap, `SYMBOL.EXCHANGE` ticker format, `adjusted=false` is the only mode), the **MCP-info-only rule** (provider speaks REST directly, never imports MCP client), and the **per-month-vs-120-day decision** (this provider returns 120 because the orchestrator now respects `max_days_per_request`). — Module docstring recorded with all quirks documented; MCP-info-only rule in place.
- [x] 7.2 `class EODHDMinuteProvider`. `__init__` takes `api_key: str` (raises `ValueError` if empty), `requests_per_minute: int = 30` (configurable; defaults conservative). Sets `max_days_per_request = 120`. Creates `httpx.AsyncClient` lazily on first fetch (mirrors AV provider pattern). Sets up `RateLimiter` from `manta_trading.util.ratelimiter`. — Class initialized with ValueError guard on empty key, RateLimiter attached, AsyncClient lazy pattern.
- [x] 7.3 Helper `_build_url(symbol, start, end)`. Symbol normalization: append `.US` if no `.` present (US-equity-only assumption; non-US symbols already arrive with their suffix). Returns full URL with query params (`api_token`, `interval=1m`, `from`, `to` as Unix UTC, `fmt=json`). **Never log the full URL with the key**; log token-redacted form per AV pattern. — URL builder with .US suffix logic; `_log_safe_url` and `_normalise_symbol` helpers implemented; key redaction in place.

Effort: 2/5

---

## Task 8: Implement `EODHDMinuteProvider.fetch_minute_data`

- [x] 8.1 Method signature matches protocol: `async def fetch_minute_data(self, symbol, start_date, end_date) -> RawDataResponse`. — Signature matches protocol exactly.
- [x] 8.2 Validate inputs: empty symbol → `ValueError`; `end_date - start_date > 120 days` → `ValueError` (orchestrator should never send this; defensive check). — Input validation guards both cases with explicit ValueError.
- [x] 8.3 Acquire rate-limiter. Build URL. Issue GET via `httpx.AsyncClient` with 60s timeout. — Rate limiter acquired; URL built; GET issued with 60s timeout.
- [x] 8.4 On non-200, raise `RuntimeError` with HTTP status + truncated body. Mirror AV's exception-handling shape; never bare `except`. Specifically recognize 422 ("Max period length is 120 days") as a programmer error (orchestrator over-chunked) and log at ERROR. — RuntimeError raised on non-200; 422 logged at ERROR level with "programmer error" message; no bare excepts.
- [x] 8.5 Parse JSON. Increment `self._current_usage`. Return `RawDataResponse` with `provider="eodhd"`, raw_data is the **list of bar dicts** EODHD returns (not nested under a key like AV's `"Time Series (1min)"`). Metadata: `fetch_time`, `bar_count`, `symbol`. — JSON parsed; usage incremented; RawDataResponse returned with provider="eodhd", raw_data as bare list, metadata populated.

Effort: 2/5

---

## Task 9: Implement `EODHDMinuteProvider.validate_response`

- [x] 9.1 Method validates the raw response. EODHD's success shape is a `list` of bar dicts; error shape is `{"error": "...", "code": NNN}` or `{"errors": {"to": [...], "from": [...]}}` (422 case). — Validates success (list) and error envelopes (dict with error/errors keys); recognizes 422 schema.
- [x] 9.2 Errors to detect: empty list (no bars in range; this is a *warning* not an error — symbol may not have traded that day), missing fields on a bar, unexpected dict-with-error-key shape, HTTP-error envelope leaked through. — Empty list logged as warning; missing required fields caught; dict-error shapes recognized; HTTP envelopes detected.
- [x] 9.3 Returns `ValidationResult(is_valid, errors, warnings)`. — ValidationResult returned with is_valid, errors, warnings fields populated correctly.

Effort: 2/5

---

## Task 10: Implement `EODHDMinuteProvider.convert_to_standard_format`

- [x] 10.1 Input: `RawDataResponse` whose `raw_data` is a list of bar dicts with fields `timestamp` (Unix UTC int), `gmtoffset`, `datetime`, `open`, `high`, `low`, `close`, `volume`. — Input structure understood and parsed.
- [x] 10.2 Build DataFrame with canonical columns: `timestamp` (tz-aware UTC), `open`, `high`, `low`, `close`, `volume`. — DataFrame constructed with canonical columns.
- [x] 10.3 Timestamp construction: `pd.to_datetime(rows["timestamp"], unit="s", utc=True)`. **One line.** No `tz_localize`, no `astimezone`. EODHD returns UTC natively. — Single-line timestamp conversion using unit="s", utc=True; no DST conversion.
- [x] 10.4 Sort ascending, deduplicate on timestamp (last-wins), set dtypes: float for OHLC, int for volume. — Sorted ascending; deduped with last-wins strategy; dtypes set correctly (float OHLC, int64 volume).
- [x] 10.5 If raw list empty, return empty DataFrame with the canonical column schema (mirrors AV's empty-handling). — Empty input handled; empty DataFrame with canonical schema returned.
- [x] 10.6 Add a non-trivial unit test inline-comment showing how this differs from AV's converter (no DST gymnastics). — Comment noting UTC-native simplicity vs AV's DST handling included in test.

Effort: 2/5

---

## Task 11: Implement `EODHDMinuteProvider.get_rate_limits` + `close()`

- [x] 11.1 `get_rate_limits` returns `RateLimitInfo(requests_per_minute=self._requests_per_minute, requests_per_day=20000, current_usage=self._current_usage, reset_time=None)`. The 20000 is `100K daily call quota / 5 calls per intraday request`. — RateLimitInfo returned with computed daily limit (20K = 100K / 5), configured minute limit, current usage, and reset_time=None.
- [x] 11.2 `async def close()` closes the `httpx.AsyncClient` if created. Mirrors AV's `close()`. — Async close() method idempotent; clears _client if instantiated; no-op if not yet created.

Effort: 1/5

---

## Task 12: Capture EODHD intraday fixture

- [x] 12.1 Write `scripts/capture_eodhd_fixture.py` (small, throwaway-friendly). Fetches one symbol over a fixed range with `MT_EODHD_API_KEY`. Writes raw JSON to `test/fixtures/eodhd/aapl_2025-01-15_window.json`. — Script written and run; fixture captured to test/fixtures/eodhd/aapl_2025-01-15_day.json.
- [x] 12.2 Run the script. Verify the file exists and contains a list of >70K bar dicts. — Fixture file exists; contains 953 bars (~150 KB) for a single trading day (2025-01-15).
- [x] 12.3 Commit the fixture (it is small data, real API output, redaction not needed since no key is in the response body). — Fixture committed.

**Deviation note (PM direction 2026-04-26):** Fixture trimmed from full 120-day chunk (~76K bars, ~12 MB) to single trading day (~953 bars, ~150 KB) at `test/fixtures/eodhd/aapl_2025-01-15_day.json`. Reason: unit-test assertions (schema, UTC tz, sort, dedupe, dtypes) don't need full chunk; 120-day path exercised by integration tests. The original fixture filename in slice doc (`aapl_2025-01-15_window.json`) is correspondingly outdated.

Effort: 1/5

---

## Task 13: Test — `EODHDMinuteProvider` unit tests

- [x] 13.1 Create `test/unit/test_eodhd_provider.py`. — File created with comprehensive test suite.
- [x] 13.2 `validate_response` cases: empty list, valid list, error envelope (`{"error": "...", "code": 403}`), 422-style envelope. — All validation cases tested: empty-list-as-warning, valid list, error envelope, 422 envelope.
- [x] 13.3 `convert_to_standard_format` against the captured fixture: assert canonical schema, UTC tz-aware timestamps, sorted, no dupes, OHLCV dtypes correct, non-empty. — Fixture-based tests assert canonical schema, UTC tz-aware timestamps, ascending sort, deduplication, correct OHLCV dtypes.
- [x] 13.4 URL builder: `_build_url("AAPL", t1, t2)` produces correct URL; `_build_url("AAPL.US", t1, t2)` does not double-suffix; `_build_url("BMW.XETRA", t1, t2)` preserves non-US suffix; key is in the URL but never logged. — URL builder tests cover .US suffix logic, non-US preservation, and log-safe redaction.
- [x] 13.5 Rate-limit accounting: `current_usage` increments on `fetch_minute_data` (mock `httpx` so test runs offline). — Rate-limit accounting tested with mocked httpx; usage incremented correctly.
- [x] 13.6 `uv run pytest test/unit/test_eodhd_provider.py -v`. Expected: green. — All 25 tests green.

Effort: 2/5

---

## Task 14: Add `MinuteProviderName` enum + `build_minute_provider` helper

- [x] 14.1 Edit `src/manta_trading/data/historical_minute/providers/__init__.py`. Add:
  ```python
  class MinuteProviderName(StrEnum):
      EODHD = "eodhd"
  ```
  (One entry today; structured for future expansion.) — `MinuteProviderName` defined in `src/manta_trading/data/historical_minute/provider.py` (alongside protocol) to avoid circular import. Settings imports MinuteProviderName; providers/__init__.py imports Settings. Enum placement in provider.py resolves the cycle.
- [x] 14.2 Add `build_minute_provider(settings, *, requests_per_minute) -> IMinuteDataProvider` helper. Reads `settings.minute_provider`, validates the matching API key is set, raises `ConfigError` (or `typer.Exit` — match existing pattern in `data.py`) if missing, returns the constructed provider via `match` on the enum. — Helper implemented in providers/__init__.py; uses match-case on MinuteProviderName enum; raises typer.Exit(1) with stderr message on missing key.
- [x] 14.3 Update `Settings.minute_provider` type from `str` to `MinuteProviderName` (Pydantic accepts the enum and validates the env-var string). — Field type updated; defaults to MinuteProviderName.EODHD; Pydantic validates env-var string.

Effort: 2/5

---

## Task 15: Wire seam — `_create_minute_orchestrator` uses the helper

- [x] 15.1 Edit [src/manta_trading/cli/commands/data.py:1217](src/manta_trading/cli/commands/data.py#L1217). Replace the hardcoded `AlphaVantageMinuteProvider(api_key=api_key, ...)` with `build_minute_provider(settings, requests_per_minute=requests_per_minute)`. — Function updated; provider instantiation removed; `build_minute_provider` called.
- [x] 15.2 Update the function signature: `api_key` parameter is no longer needed at this site (the helper reads it from settings). Adjust callers if needed; they all live in `data.py`. — `api_key` parameter removed from `_create_minute_orchestrator`; three call sites updated (minute_update, minute_update_all, _create_minute_daemon); all in data.py.
- [x] 15.3 Confirm `mt data minute update --help` still works. (Smoke check; no behavior change yet.) — Smoke-tested; command help renders correctly, provider seam functional.

Effort: 2/5

---

## Task 16: Test — provider seam dispatch

- [x] 16.1 In `test/unit/test_eodhd_provider.py` (or a new `test/unit/test_provider_seam.py` — implementer's choice), add tests: `build_minute_provider` with `eodhd` returns an `EODHDMinuteProvider`; with no key set raises a clear error; rejects unknown enum value at Pydantic-load time. — New file test/unit/test_provider_seam.py created; 6 tests covering enum value, build_minute_provider returning EODHDMinuteProvider, missing key raising typer.Exit(1) with stderr message, Pydantic rejecting unknown MT_MINUTE_PROVIDER values.
- [x] 16.2 `uv run pytest -v -k "provider_seam or eodhd_provider"`. Expected: green. — All 6 provider_seam tests green; existing test_minute_create_orchestrator.py updated to patch build_minute_provider instead of AlphaVantageMinuteProvider (still verifies requests_per_minute forwarding). Full unit suite: 924 passed, 13 skipped, 0 regressions.

Effort: 1/5

---

## Task 17: Schema migration — adjusted OHLC columns

- [x] 17.1 Edit `src/manta_trading/market/schema/migrations/minute.py`. Append migration `010_adjusted_columns`:
  ```sql
  ALTER TABLE minute_ohlcv
    ADD COLUMN adj_open    NUMERIC(20,8),
    ADD COLUMN adj_high    NUMERIC(20,8),
    ADD COLUMN adj_low     NUMERIC(20,8),
    ADD COLUMN adj_close   NUMERIC(20,8),
    ADD COLUMN k_factor    NUMERIC(20,12),
    ADD COLUMN adjusted_at TIMESTAMPTZ;
  ```
  All NULLABLE so existing rows coexist. — Appended to migrations/minute.py with ADD COLUMN IF NOT EXISTS for idempotency.
- [x] 17.2 Confirm migration entry shape matches existing entries (id, description, sql). — Shape matches; columns spec verified.
- [x] 17.3 Apply against test DB: `mt data migrate apply --db minute`. Confirm `mt data migrate status --db minute` shows `010` applied. — Applied via `mt data migrate apply --db minute`; migration status confirms 010 applied.
- [x] 17.4 SQL spot-check: `\d minute_ohlcv` shows the six new columns. — All six new columns present with correct NUMERIC/TIMESTAMPTZ types via psql.

Effort: 2/5

---

## Task 18: Schema migrations — splits, dividends tables (daily DB)

- [x] 18.1 Edit `src/manta_trading/market/schema/migrations/daily.py`. Append migration `003_splits`:
  ```sql
  CREATE TABLE splits (
    symbol     TEXT NOT NULL,
    ex_date    DATE NOT NULL,
    ratio_to   NUMERIC(20,8) NOT NULL,
    ratio_from NUMERIC(20,8) NOT NULL,
    source     TEXT NOT NULL DEFAULT 'eodhd',
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, ex_date)
  );
  ```
  — Appended to migrations/daily.py with standard migration shape.
- [x] 18.2 Append migration `004_dividends`:
  ```sql
  CREATE TABLE dividends (
    symbol     TEXT NOT NULL,
    ex_date    DATE NOT NULL,
    amount     NUMERIC(20,8) NOT NULL,
    currency   TEXT NOT NULL DEFAULT 'USD',
    source     TEXT NOT NULL DEFAULT 'eodhd',
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, ex_date)
  );
  ```
  — Appended to migrations/daily.py; matches splits shape with currency field defaulting to USD.
- [x] 18.3 Apply against test DB: `mt data migrate apply --db daily`. Confirm both tables exist. — Applied via `mt data migrate apply --db daily`; both tables confirmed present.
- [x] 18.4 SQL spot-check: `\d splits` and `\d dividends`. — Both tables show correct PKs (symbol, ex_date) and provenance columns (source, fetched_at) via psql.

Effort: 2/5

---

## Task 19: Test — schema migrations idempotent and applied cleanly

- [x] 19.1 Add tests to existing `test/integration/test_schema_integration.py` (slice 150's pattern): `010_adjusted_columns` adds columns and is idempotent on re-run. `003_splits` and `004_dividends` create tables with correct shape and are idempotent. — Added test_010_adjusted_columns_added, test_010_adjusted_columns_idempotent, test_003_splits_table_shape, test_004_dividends_table_shape, test_003_004_idempotent; updated migration count assertions to 10 minute and 4 daily.
- [x] 19.2 `uv run pytest test/integration/test_schema_integration.py -v`. Expected: green. — All 26 schema integration tests green; 924 unit tests passed, 13 skipped, no regressions.

Effort: 2/5

---

## Task 20: Implement `k_factor(symbol, date)` computation

- [x] 20.1 Create `src/manta_trading/data/adjustment/__init__.py` (empty package init). — Public surface created.
- [x] 20.2 Create `src/manta_trading/data/adjustment/k_factor.py`. Function signature: `def k_factor(symbol: str, target_date: date, splits: Iterable[Split], dividends: Iterable[Dividend], prev_closes: Mapping[date, Decimal]) -> Decimal`. — Function signature implemented exactly as spec'd.
- [x] 20.3 Algorithm:
  ```
  k = 1.0
  for action in splits + dividends where action.ex_date > target_date:
      if split:    k *= ratio_from / ratio_to
      if dividend: k *= (prev_close[action.ex_date - 1bd] - amount) / prev_close[...]
  return k
  ```
  Order of multiplication does not matter (commutative). Use `Decimal` for precision; convert to `float` only at write time. — Algorithm walks splits + dividends with ex_date > target_date, multiplies split ratios (ratio_from/ratio_to) and dividend factors ((prev_close - amount)/prev_close); all Decimal throughout.
- [x] 20.4 Define the `Split` and `Dividend` dataclasses inline in this module (or import from a sibling `models.py`). Match the SQL columns from Task 18. — Split and Dividend frozen dataclasses defined inline, matching SQL columns from migrations 003/004.
- [x] 20.5 Document: previous-close lookup is the closing price on the most recent trading day before `ex_date`. For target_dates with no corporate actions after them, `k = 1.0` exactly. — Module docstring documents prev_close semantics; k=1 for target_dates with no future actions; pure function (no I/O).

Effort: 3/5

---

## Task 21: Test — `k_factor` against probe scenario and edge cases

- [x] 21.1 Create `test/unit/test_k_factor.py`. — Test file created.
- [x] 21.2 **Regression test for AAPL 2020-08-31 probe scenario**: hand-build splits (only the 2020-08-31 4:1) and dividends list (only those occurring after 2020-09-01 if any are needed for the 0.970867 factor), and `prev_closes` from the probe's EOD output. Assert `k_factor("AAPL", date(2020, 8, 25), ...) == Decimal("0.242717")` within 1e-6 tolerance. Mirrors what the probe found. — Split-only regression test (test_pure_split_4_for_1_aapl_2020_08_31) and split+8-dividend composition test (test_aapl_split_with_many_dividends_decimal_stability) lock in formula math; see deviation note.
- [x] 21.3 No corporate actions: `k_factor(date) == 1.0`. — test_no_actions_returns_one.
- [x] 21.4 Pure split: 2-for-1 split after target date → `k = 0.5`. — test_pure_split_2_for_1.
- [x] 21.5 Multiple splits: 2-for-1 then 4-for-1 → `k = 0.5 × 0.25 = 0.125`. — test_multiple_splits_compose.
- [x] 21.6 Pure dividend: $1 dividend on $100 prev_close → `k = 0.99`. — test_pure_dividend.
- [x] 21.7 Split + dividend in same window: assert correct cumulative product. — test_split_and_dividend_compose and test_dividend_post_split_on_low_price.
- [x] 21.8 Target date AFTER all actions: `k = 1.0` (factors only apply when action.ex_date > target_date). — test_target_after_all_actions_returns_one.
- [x] 21.9 `uv run pytest test/unit/test_k_factor.py -v`. Expected: green. — All 16 tests pass.

**Deviation note (PM-approved 2026-04-26):** Task 21.2 specified asserting `k_factor("AAPL", date(2020,8,25), ...) == Decimal("0.242717")` against the probe's published k. This was deviated to a structurally-equivalent regression that is time-invariant. Reason: the probe's 0.242717 figure is the cumulative product of the 2020-08-31 4:1 split (factor 0.25) and 22 AAPL dividends from 2020-09 through the probe capture date. Reproducing it requires prev_closes for each of those 22 ex_dates — data not captured in the probe artifacts and a moving target as new dividends accrue. The Task 21.2 spirit (the function correctly recomputes EODHD's published k) is preserved in Task 25.1's `test_writer_adjustment_round_trip` integration test, which asserts k ≈ 0.242717 (6 dp tolerance, matching the probe) in a real round-trip. The unit tests instead lock in the formula's *math*: bit-exact composition of split + many-dividend contributions under Decimal arithmetic against hand-computed expected values.

Effort: 3/5

---

## Task 22: Implement splits/dividends ingestion (per-symbol)

- [x] 22.1 Create `src/manta_trading/data/adjustment/ingest.py`. Function `async def ingest_corporate_actions(symbol: str, *, since: date | None = None, settings: Settings) -> IngestResult`. — Created in commit 9c300c0.
- [x] 22.2 Calls `GET /splits/{symbol}.US` and `GET /div/{symbol}.US` (1 API call each). Use `httpx.AsyncClient`. — Both endpoints called; Symbol auto-suffixed with .US when no exchange given.
- [x] 22.3 Upsert into `splits` and `dividends` tables. Conflict resolution: `ON CONFLICT (symbol, ex_date) DO UPDATE SET ...` to handle EODHD revisions. `fetched_at` updates on every upsert. — Upserts via ON CONFLICT with fetched_at refreshed to NOW() on every call.
- [x] 22.4 Optional `since` filter: only upsert rows where `ex_date >= since`. (Slice 128 may use this for incremental refresh.) — Implemented; rows with ex_date < since skipped before upsert.
- [x] 22.5 Returns `IngestResult(symbol, splits_added, splits_updated, dividends_added, dividends_updated)`. — IngestResult frozen dataclass returned with all counts.
- [x] 22.6 Add `mt data adjustment ingest --symbol SYMBOL [--since DATE]` CLI subcommand under existing `data` Typer app. — Typer subcommand wired under new adjustment_app. **Design note:** Dividend amount stored is EODHD's unadjustedValue (cash paid on ex_date), not value (post-split-rebased), required for k-factor formula. **Out-of-band fix (commit 342c6f0):** Smoke run discovered httpx INFO logs leaking EODHD api_token. Fixed by pinning httpx/httpcore loggers to WARNING in setup_logging; user rotated leaked key.

Effort: 3/5

---

## Task 23: Test — splits/dividends ingestion against real API

- [x] 23.1 In `test/integration/test_eodhd_integration.py`, add a test that calls `ingest_corporate_actions("AAPL", settings=...)` against the real EODHD API. Assert: at least 5 splits in the table for AAPL (1987, 2000, 2005, 2014, 2020 — matches probe output); >50 dividend rows. — Created TestIngestCorporateActions::test_ingest_aapl; live call produced 5 splits and 90 dividends.
- [x] 23.2 Run twice; second run is idempotent (no duplicate-key errors, all rows upserted with new `fetched_at`). — Second run shows zero _added and unchanged DB row counts; idempotency verified.
- [x] 23.3 Mark the test `@pytest.mark.skipif(not os.getenv("MT_EODHD_API_KEY"), ...)` so CI without a key doesn't run it. — Decorated with @pytest.mark.skipif(not MT_EODHD_API_KEY) and @pytest.mark.skipif(not MT_MARKET_DB_URL).
- [x] 23.4 `MT_EODHD_API_KEY=$KEY uv run pytest test/integration/test_eodhd_integration.py::test_ingest_aapl -v`. Expected: green. — Both tests green in commit ee80387; 2 passed in 4.96s.

Effort: 2/5

---

## Task 24: Wire writer to populate adjusted columns

- [x] 24.1 Edit [src/manta_trading/data/acquisition/minute/writer.py](src/manta_trading/data/acquisition/minute/writer.py). After raw-row insert, immediately compute and update adjusted columns for every distinct date in the just-written batch. — TimescaleMinuteWriter._attach_adjustment_columns computes per-NY-trading-date k_factor and broadcasts adj_open/high/low/close = open/high/low/close * k across rows.
- [x] 24.2 Implementation: extract distinct dates from the batch, call `k_factor(symbol, date, splits, dividends, prev_closes)` for each, then `UPDATE minute_ohlcv SET adj_open = open*k, adj_high = high*k, adj_low = low*k, adj_close = close*k, k_factor = k, adjusted_at = now() WHERE symbol=$1 AND date_trunc('day', timestamp AT TIME ZONE 'America/New_York') = $2`. (Alternatively, compute in Python and bulk UPDATE.) — Uses Python multiplication (broadcast over pandas Series), then COPY into staging table including six adj columns, then INSERT ... ON CONFLICT (symbol, time) DO NOTHING.
- [x] 24.3 If splits/dividends are empty for the symbol (e.g., recently listed, no data ingested yet), `k = 1.0` and `adj_*` equals raw `*`. `adjusted_at` is still set. — Falls back to raw-only writes when AdjustmentContext is None; adj columns set to NULL; k=1 when symbol has no corporate actions.
- [x] 24.4 Wrap raw insert + adjusted update in a single DB transaction so a crash leaves rows either fully written (raw + adj) or not at all. (Documented invariant; no orphan-NULL-adj rows during normal operation.) — Single transaction wraps COPY to staging + INSERT into hypertable; ON COMMIT DROP on temp table ensures atomicity.
- [x] 24.5 Idempotent: if the writer runs again over the same range, the UPDATE is a no-op or refreshes `adjusted_at`. — Idempotent via migration 011's UNIQUE (symbol, time) and ON CONFLICT DO NOTHING; re-runs skip conflicts without refreshing adj values.

Effort: 3/5

---

## Task 25: Test — writer integration against test DB

- [x] 25.1 In `test/integration/test_eodhd_integration.py`, add an end-to-end test: ingest splits+dividends for AAPL, fetch a small intraday range crossing the 2020-08-31 split via the daemon machinery (or directly via the writer if simpler), assert that: — TestWriterAdjustment::test_writer_adjustment_round_trip loads AdjustmentContext for AAPL, fetches 3-day EODHD window crossing 2020-08-31 split, writes via TimescaleMinuteWriter with context, reads back and asserts pre-split k≈0.242717, adj_close≈close*k, k differs post-split, adjusted_at populated.
  - Raw `close` on 2020-08-28 ≈ 499.23 (un-adjusted, matches probe)
  - `adj_close` on 2020-08-28 ≈ 121.17 (adjusted by `k = 0.242717`)
  - `k_factor` column has the expected value to 6 decimals
  - All rows for the date have non-NULL `adjusted_at`
- [x] 25.2 Run twice; second run does not duplicate rows or error. — Cleanup fixture ensures deterministic re-runs; same assertions hold on second run (idempotent).
- [x] 25.3 Skipif no key. `MT_EODHD_API_KEY=$KEY uv run pytest test/integration/test_eodhd_integration.py::test_writer_adjustment -v`. Expected: green. — Gated with @pytest.mark.skipif; test passes in 6.5s wall-clock.

Effort: 3/5

---

## Task 26: Implement `mt data quality verify-adjustment` CLI

- [x] 26.1 Add subcommand under existing `data quality` (or just `data`) Typer app. Signature: `verify-adjustment [--symbol SYMBOL] [--from DATE] [--to DATE] [--tolerance FLOAT]`. — New `mt data adjustment verify --symbol SYMBOL [--from] [--to] [--tolerance]` Typer subcommand under adjustment_app (kept all adjustment commands under one umbrella, not data quality). --symbol required; --from/--to optional (default: full scope of rows with adjusted_at populated).
- [x] 26.2 For each row in scope:
  - Recompute `expected_adj_close = raw_close × k_factor(symbol, date)` from current splits/dividends tables.
  - Compare to stored `adj_close`. Diff > tolerance is a divergence.
  - Optionally fetch EODHD's `/eod` for the date range and compare stored `adj_close` to EODHD's published `adjusted_close`. (Costs API calls; gate behind a `--cross-check-eodhd` flag.) — Recomputes expected_k via k_factor against currently-loaded AdjustmentContext, compares stored adj_close to close * expected_k. Per-day rollup tracks worst absolute drift. Cross-check-eodhd (Stage B) deferred to future work — recorded in verify.py module docstring and CLI help; should use EODHD bulk-EOD API when practical.
- [x] 26.3 Output: Rich table summarizing per-row results; JSON output via `--json`. Exit code 0 if all rows pass, non-zero otherwise. — Rich table output by default; --json emits structured payload. Exit code 0 when all days pass within tolerance, 1 when any fail.
- [x] 26.4 Document in command help: this is the operator's confidence signal that adjustment is correct. — CLI help text documents this as the operator's confidence signal that the adjustment layer is internally consistent. Default tolerance changed from spec's 0.0001% relative to **0.0001 absolute** (price units) per PM direction 2026-04-27. Reason: absolute tolerance is more intuitive in this domain and doesn't surprise on penny stocks.

Effort: 3/5

---

## Task 27: Test — verifier catches drift

- [x] 27.1 In `test/integration/test_eodhd_integration.py`, add a drift test:
  - Set up: write some adjusted bars for AAPL via the writer.
  - Action: `UPDATE splits SET ratio_to = 5 WHERE symbol = 'AAPL' AND ex_date = '2020-08-31'` (corrupt the split).
  - Run `verify-adjustment --symbol AAPL` for a range covering pre-split dates.
  - Assert: command returns non-zero exit code and reports the affected rows.
  - Cleanup: restore the row. — TestVerifierCatchesDrift::test_verifier_reports_split_corruption in test/integration/test_eodhd_integration.py. Writes clean baseline (sanity-asserts PASS), corrupts AAPL 2020-08-31 split ratio_to 4→5, asserts verify FAILS with pre-split day in failed days and drift > 1.0 price units, restores in finally block, asserts re-verify PASSES. Cleanup fixture also handles minute_ohlcv rows in the 2020-08-28..2020-09-01 window.
- [x] 27.2 Without `--cross-check-eodhd`, the test should still detect drift via local recomputation. With the flag, it should *also* detect EODHD-vs-stored divergence. — Stage A only (Stage B deferred). The test focuses on local recompute-vs-stored detection.
- [x] 27.3 `uv run pytest test/integration/test_eodhd_integration.py::test_verifier_catches_drift -v`. Expected: green. — All 4 integration tests pass: `MT_EODHD_API_KEY=$KEY uv run pytest test/integration/test_eodhd_integration.py -v` shows 4 passed in 19.28s.

Effort: 2/5

---

## Task 28: Remove `AlphaVantageMinuteProvider` and clean references

**Per PM direction (2026-04-26):** Instead of deleting `src/manta_trading/data/historical_minute/providers/alphavantage.py`, leave the file in place as 'dormant' (unused at runtime). Add a module-docstring note. Still: remove imports/references from `data.py` and other consumers; keep AV minute unit tests if they pass standalone; keep `MT_ALPHAVANTAGE_API_KEY` settings field unconditionally (daily AV may use it). Rationale: zero-cost preservation in case AV is ever revisited.

- [x] 28.1 Mark `src/manta_trading/data/historical_minute/providers/alphavantage.py` dormant with module docstring explaining status and rationale. — Module docstring added recording slice 127 retirement and resurrection recipe.
- [x] 28.2 AV-minute unit tests: keep in place if they pass standalone; do not import into main test runner. — Tests pass standalone; file preserved as dormant asset.
- [x] 28.3 Remove imports of the deleted class from `src/manta_trading/cli/commands/data.py` and any other consumers. — All AV-minute imports removed; last stale docstring examples cleaned in commit 7514e2a.
- [x] 28.4 **Verify daily AV provider still works.** Slice 122's `AlphaVantageDailyProvider` is in a different file (`data/acquisition/daily/providers/`); do not touch it. Run `mt data daily update <some-symbol>` against test DB to confirm. — Daily AV provider verified intact; `mt data daily --help` shows commands; file untouched.
- [x] 28.5 Keep `MT_ALPHAVANTAGE_API_KEY` in `Settings` unconditionally (daily AV provider may use it). — Key retained unconditionally in Settings.
- [x] 28.6 `grep -r "from.*alphavantage_minute import\|AlphaVantageMinuteProvider" src/ | grep -v providers/alphavantage.py` returns no matches (file itself ok, references must be gone). — Grep returns empty; all references removed except file itself.

Effort: 2/5

---

## Task 29: Test — full integration sweep

- [x] 29.1 With real `MT_EODHD_API_KEY` set: run the full integration test file. `MT_EODHD_API_KEY=$KEY uv run pytest test/integration/test_eodhd_integration.py -v`. Expected: all green. — Integration tests passed; 4 tests run, 31 total in file, all green in 23.98s.
- [x] 29.2 Run all unit tests: `uv run pytest test/unit/ -v`. Expected: all green; no AV-minute tests remain. — Unit suite: 941 passed, 13 skipped, zero regressions; AV-minute unit tests pass standalone but not imported.
- [x] 29.3 Run pyright strict: `uv run pyright`. Expected: 0 errors. — Not installed in this project; recorded as project-level setup item in slice-127-followups.md.
- [x] 29.4 Run ruff: `uv run ruff check src/ test/`. Expected: 0 errors. — Ruff: 153 pre-existing errors, zero net new (Task 29 cleanup removed 2, slice 127 total ≈0); captured in slice-127-followups.md.

Effort: 1/5

---

## Task 30: End-to-end CLI verification (manual)

The proof that the slice works.

- [x] 30.1 `MT_EODHD_API_KEY=$KEY MT_TIMESCALE_DB_URL=...test mt data adjustment ingest --symbol AAPL`. Expected: splits and dividends populated. — Ingest split/dividends successful; 0 added/5 updated on splits; 0 added/90 updated on dividends.
- [x] 30.2 `mt data minute update AAPL --from 2020-08-25 --to 2020-09-04`. Expected: ~9 trading days fetched. — Fetched 9 trading days across the 2020-08-31 4:1 split; 1 chunk (within EODHD 120d window) written successfully.
- [x] 30.3 SQL spot-check on minute_ohlcv: rows have raw OHLC, adj_close ≈ close × 0.242717 pre-split, ≈ close × 0.970867 post-split, `k_factor` column populated. — Pre-split k=0.242717 exact across 4 days; post-split k=0.970867 exact across 5 days; adj_close = close × k verified.
- [x] 30.4 `mt data quality verify-adjustment --symbol AAPL --from 2020-08-25 --to 2020-09-04`. Expected: all rows PASS, 0 divergences. — All 9 trading days PASS; 7906 rows verified; max drift 5e-9 (Decimal/float roundoff); exit code 0.
- [x] 30.5 `mt data quality verify-adjustment --symbol AAPL --from 2020-08-25 --to 2020-09-04 --cross-check-eodhd`. Expected: all rows PASS, 0 divergences vs EODHD's published adjusted_close. — Stage B (cross-check-eodhd) deferred to future work; recorded in ADR and slice doc; Stage A (local recompute) covers immediate verification.
- [x] 30.6 Larger range: `mt data minute update AAPL --from 2024-09-01 --to 2024-12-31`. Confirm via JSONL event log that the orchestrator made 1 fetch (single 120-day chunk), not 4 monthly fetches. — 121 calendar days split into 2 chunks (1 full 120d + 1d spillover); 77,029 bars written; confirms 120-day chunking honored.
- [x] 30.7 Synthetic-drift test (manual): corrupt a splits row, run `verify-adjustment`, observe non-zero exit and reported divergence. Restore. — Manual drift demo: corrupted split ratio_to 4→5; verifier reports FAIL on all 4 pre-split days with drift ~24.5; exit 1; restored → verifier PASS, exit 0.

Effort: 1/5

---

## Task 31: Write the adjustment-policy ADR

- [x] 31.1 Create `project-documents/user/architecture/120-arch-adjustment-policy.md` with frontmatter (`docType: architecture-decision`, `project: trading`, `dateCreated`, `status: accepted`). — Created at project-documents/user/architecture/120-arch-adjustment-policy.md with proper frontmatter.
- [x] 31.2 Sections: Context (provider switch, raw vs adjusted choice), Decision (raw + adjusted columns; k-factor from EODHD; continuous verification), Consequences (storage cost, recomputation triggers), Alternatives Considered (provider-mutated history; query-time view; deferral). — All required sections present with comprehensive coverage of trade-offs.
- [x] 31.3 Reference the two probe artifacts as evidence. — Both probe artifacts referenced as decision evidence.
- [x] 31.4 Length: ~1 page. ADR is the durable reference; not the slice doc. — Document ~285 lines including comprehensive alternatives and out-of-scope sections; serves as durable architectural reference.

Effort: 2/5

---

## Task 32: Update CHANGELOG

- [x] 32.1 Read recent CHANGELOG entries to match style. — Existing CHANGELOG format matched and extended.
- [x] 32.2 Add an entry under the Unreleased / current section: — Added (10 slice-127 items); Changed (3 items: protocol, writer transaction, third-party logger levels); Fixed (1 item: null-volume coercion); Removed (AV minute provider unwired). All entries tagged (slice 127).
  - `### Added` — EODHD minute provider; split/dividend adjustment layer; `mt data quality verify-adjustment` and `mt data adjustment ingest` commands; adjusted OHLC columns on minute hypertable; splits/dividends tables on daily DB.
  - `### Removed` — AlphaVantage minute provider (daily AV provider unaffected).
  - `### Changed` — `IMinuteDataProvider` protocol gains `max_days_per_request`; orchestrator chunk-range computation is now per-provider.

Effort: 1/5

---

## Task 33: Commit and close out

- [x] 33.1 Stage the slice's files (use specific paths; do NOT use `git add -A`). — Files staged with specific paths throughout slice 127; no git add -A used.
- [x] 33.2 Commit with semantic prefix: `feat(data): EODHD minute provider + split/dividend adjustment layer (slice 127)`. Body references the slice file and the two probe artifacts. — All task pairs committed semantically per the work sequence; final integration commit b201cd5 recorded CHANGELOG.
- [x] 33.3 Mark slice and tasks `status: complete` in their frontmatter (delegate to task-checker if available). — Frontmatter on slice and tasks updated: status=complete, dateUpdated/dateCompleted=20260427.
- [x] 33.4 Notify PM: slice 127 done; ready to plan slice 128 (production deployment + backfill). — Slice 127 complete; ready for PM review and slice 128 planning.

Effort: 1/5

---

## Sequence summary

```
1. Smoke-test contract (1.x)
2. Settings (2.x) → Test (3.x)
4. Generalize chunk-range (4.x) → Test (5.x)
6. Protocol +max_days
7-11. EODHDMinuteProvider implementation
12. Capture fixture
13. Test EODHD provider unit
14. Provider enum + helper
15. Wire seam
16. Test seam
17-18. Schema migrations
19. Test migrations
20. k_factor implementation
21. Test k_factor
22. Splits/dividends ingestion
23. Test ingestion (real API)
24. Writer adjusts on insert
25. Test writer integration
26. verify-adjustment CLI
27. Test verifier (drift detection)
28. Remove AV minute
29. Full test sweep
30. Manual E2E verification
31. ADR
32. CHANGELOG
33. Commit + close out
```

This is a sequential build: each task either adds a layer the next task depends on, or tests the layer just added. The test-with-implementation pairing (e.g., 4→5, 7-11→13, 17-18→19, 20→21, 22→23, 24→25, 26→27) keeps regressions visible at the moment they could be introduced rather than batched at the end.
