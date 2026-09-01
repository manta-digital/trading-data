---
docType: devlog
project: trading
dateCreated: 20260411
dateUpdated: 20260831
---

# Development Log

A lightweight, append-only record of development activity. Newest entries first.
Format: `## YYYYMMDD` followed by brief notes (1-3 lines per session). Written from implementor perspective (class names, design decisions, test counts). For user-visible changes see CHANGELOG.md.

---

## 20260831

**0.11.2 — cagg refresh offsets + pull robustness (issues #20, #19).** 5m/15m caggs found frozen since 2026-08-07 with policies reporting Success: 2-hour `start_offset` never overlapped the nightly, hours-old bars; 037 had widened it but the 2026-08-04 restore replay of 035 recreated 2h policies and the forward-only ledger never re-fired 037. Migration `053` sets all four minute caggs from `MINUTE_CAGG_REFRESH_START_OFFSET` (3 days — tolerates a quota-deferred night), guarded and idempotent; 035 renders the constant. `_pull_fetch_inner` collapsed to one loop for daily/minute with per-symbol `ProviderResponseError` skip and a `PULL_MAX_CONSECUTIVE_PROVIDER_ERRORS` abort; 402 message names the quota. Unpark pull (issue #19) completed: 13,081 symbols, 11,886 ok / 1,195 empty / 0 errors, ~94.6k requests; every frozen symbol has bars through 2026-08-28.

---

**0.11.1 — minute seed-gate fix (issue #19).** Post-265-cutover review of `mt data status` found ~7,300 of 13,083 active symbols with frozen minute data: `_do_minute_symbol`'s `_needs_seed` gate (no bars / no gap rows / has UNKNOWN) never fires for a symbol whose rows are all terminal, so one empty trailing-day fetch (`EMPTY` → `PROVIDER_HOLE`) parked a symbol permanently — mass event 2026-08-06/07 (6,384 symbols), then 166–748/week. Fix: the preflight SELECT also reads the gap frontier (`MAX(gap_end)`); when the full-seed gate doesn't fire and frontier < target_end, seed exactly `[frontier, target_end]` — never `history_start`, because `update_data_gaps` deletes rows contained in its window and would resurrect every genuine provider hole. Five regression tests (`TestTrailingSeedAfterTerminalGaps`). Daily is unaffected (no such gate). Recovery of already-parked spans: one `mt data pull 1m --universe --reset --start 2026-06-01` after the release lands (dry-run measured: 1,719 terminal rows in window). Also observed, still open: `minute_5min/15min_ohlcv` caggs materialized only to 2026-08-07 with policies reporting Success.

---

## 20260830

**Slice 265 — Kalshi public trades collection (0.11.0).** Third pass phase: `TradesPhase` appended to `PASS_PHASES`, `TradeSync` walking the exchange-wide tape in one-hour windows under one watermark (`kalshi.sync_state['trades']`, floor at the cutoff on the first run, `TradesBehindCutoffError` if the watermark falls behind it), `TradeRepository.write_page` classifying and writing each 1,000-trade page in one CTE with per-page transactions, `TRADE_REQUESTS_PER_PASS = 3,000` cap checked before each window. Migration `kalshi_006_trades`: hypertable keyed `(market_ticker, created_time, trade_id UUID)`, 7-day chunks, compression at 14 days. The candle rule became the collection rule — `MT_KALSHI_CANDLE_*` → `MT_KALSHI_COLLECTION_*` with a loud `RenamedSettingError` guard over the environment and the env file; `CandleRule` → `CollectionRule` in `data/kalshi/selection.py`. `status` trades block from persisted state plus the catalog join (four closed-market counts under a documented precedence; never counts `kalshi.trades`). Gates: unit tier 2,523 passed; `integration -k kalshi` 120. Rehearsal on the test cluster: 857,954 fetched = 453,406 written + 83,362 unknown + 321,124 excluded + 62 duplicates; 0.21 s/page insert path, no measurable penalty re-walking a compressed chunk (8.4× compression). Host cutover is one command, `scripts/cutover_265_trades.py`, run by the PM after tagging.

---

## 20260827

**Slice 264 — Kalshi candlestick collection (0.10.0).** Second pass phase: `CandlesPhase` appended to `PASS_PHASES`, `CandleSync` over `CandleRepository` (three pending queries — live / finishing / backlog — sharing one `selection_sql` predicate with `status`), pure planner `candle_plan.py` (`target_window`, `plan_batches` under the 100-ticker / 10,000-candle batch caps, `AssertionError` guard not `assert`), `CandleRule` as five `MT_KALSHI_CANDLE_*` settings with rule C defaults. Migration `kalshi_005_candlesticks`: hypertable (7-day chunks), compression policy at 14 days, `coverage_from_ts`; preflight now checks the whole kalshi ledger. `status` candle block from persisted state only. Shared helpers extracted rather than duplicated (`events.emit_in_thread`, `sync_types.classify_outcome`); the kalshi track never created the TimescaleDB extension, so `kalshi_bare_db` + `ensure_timescaledb` in the test tier. Gates: 409 kalshi unit / 87 kalshi integration; unit tier 2,439 passed. Rehearsal (two runs on the test cluster) found `open_lagging` counting markets already complete through close — fixed with a regression test — and that `CALL run_job((select …))` is invalid SQL (two-statement form). Host steps (Section 8) pending.

---

## 20260825

**Slice 263 — collection pass and supervised install (0.9.0).** `mt data kalshi pass`: `PASS_PHASES` registry run over one client/connection/sink with 262's exit codes; `mt-kalshi-pass.service` + `.timer` (hourly :20 UTC, `Persistent=true`, `TimeoutStartSec=infinity`) installed by the same script, enabling nothing; `mt-run kalshi`/`follow kalshi`; a `settled window …` INFO line per completed window. Fixed `mt-run` under sudo forwarding only two `MT_*` variables. Cold throwaway rehearsal: 3.5 M settlements in 244 windows, 45.7 min; steady state 97 s. Host cutover done, all 12 criteria proven.

---

## 20260824

**Slice 262 — catalog sync with settlement capture.** `mt data kalshi sync`: series, live non-MVE markets with per-page parent resolution, events, the settled stream in 6-hour windows from a persisted watermark (`--settled-since` for replay), the awaiting-settlement guarantee; write-on-change upserts; exit codes 0/1/2/3/4 by `SyncOutcome`; `--events-file` JSONL sink; `mt data kalshi status`. Migration `kalshi_004` (comments). Discovery: served market status vocabulary is `initialized/active/inactive/closed/determined/finalized`, not the documented filter names.

**Slice 261 — Kalshi provider foundation.** Package `manta_trading.data.kalshi`: async `trade-api/v2` client (cursor iterators, one rate budget, bounded retry, complete transient/permanent error taxonomy over httpx), Pydantic models with `Decimal` money, optional RSA-PSS authenticated mode (`MT_KALSHI_API_KEY_ID` + `MT_KALSHI_PRIVATE_KEY_PATH`, both or neither). `kalshi` migration track (`--track`), recorded fixtures under `test/fixtures/kalshi/` with `scripts/record_kalshi_fixtures.py`.

---

## 20260822–23

**Slice 916 — supervised production (0.8.0).** systemd timers for the daily/minute passes and a supervised `mt serve`; pinned checkout at `/opt/manta-trading` under a `nologin` account; `mt-run` front door (`daily|minute|status|follow|<mt args>`); idempotent `deploy/install-production.sh --ref <tag>`; runbooks renumbered and indexed. Redacted the EODHD token from retry/error log sites.

**Slice 917 — dedicated test database cluster.** Test tier moved to its own host (runbook 400, `hammerhead`), `trading_test_admin` role; the suite fails rather than skips when `MT_TIMESCALE_TEST_URL` is unset, points at production, or the server version drifts.

---

## 20260816–18

**Slice 169 — coverage-cagg bucket narrowing.** Part 1: 7-day buckets and a reachable staleness threshold (schema + thresholds); part 2: the rebuild driver, sweep tests, runbook. Closed 20260818, 19/19 criteria. **Slice 915** — backup and restore runbook and task breakdown from the measured prod host survey; `REPLICATION` granted to the maintenance role for `pg_basebackup`.

---

## 20260811

**Slice 170** — generalized the 166 rechunk driver to a `RechunkTarget` StrEnum + `RECHUNK_TARGETS` registry (table, interval, cagg views, interval-migration id); `run_rechunk` takes `target=`, keeping `table`/`cagg_views` as test seams. Added `DAILY_OHLCV_CHUNK_INTERVAL` (70 d), migration `050_daily_chunk_interval_70d`, and made creation migration 023 render the constant instead of `INTERVAL '7 days'`. CLI `mt data rechunk --table minute|daily`. Tests: 24 unit (registry coverage vs enum, 70-day grid nesting, pre-flight naming 043 vs 050), 9 CLI, 5 migration-050 integration on an ephemeral DB, 7 daily-shaped driver integration. Unit tier 1,898 passed; integration baseline unchanged (2 known `test_cli_lists.py`).

**Prod run** — `mt data rechunk --table daily` exit 0, ~16 min: 337/338 windows rewritten, every one collapsing to exactly 1 chunk (336 from 10, 1 from 8). Chunks 3,372 → 341; `MAX(time)` 4.92 s → 0.157 s; 31k-symbol EXPLAIN >120 s → 7.70 s; `count(*)` identical at 65,652,505. **C2.3 stop condition fired**: dry run reported 338 windows, not ~118 — the design's span input was wrong (data starts 1962, not 2004; 64.6 years), grid arithmetic was correct. Exit force-refresh revealed the daily caggs were ~half-materialized (+6.6 M / +1.5 M / +530 k / +148 k rows); R5 closed-window parity now 0 for all three rollups. Runbook job 1003 no longer exists (4h minute refresh is 1124). `approximate_row_count` off by +2,099% post-`ANALYZE` — known estimator defect, do not use for verification.

**Slice 915** — added overview + Phase 4 design for backup and restore procedures (no backup procedure or tested restore path exists; prod `archive_mode=off`, so no PITR). Dependencies [913].

---

## 20260809

**Slice 914 + 0.7.7 release** — removed the AlphaVantage-era news subsystem: `news/` (7 files), `agents/newsagent.py`, 5 unit + 3 integration test files, and the `pymongo`/`motor` dependencies (`uv lock` also dropped `dnspython`). Unit 1,892 → 1,868 passed (0 failed); integration failures 6 → 2, both the pre-existing `test_cli_lists.py` pair; `mt --help` diff empty. Cut CHANGELOG sections retroactively for 0.7.5/0.7.6 (tagged without changelog) and released 0.7.7. Filed API enhancement issues #11 (expose all stored instrument metadata), #12 (batch bars), #13 (last-N-bars).

---

## 20260808

**Slice 913** — least-privilege database roles. Prod now connects as `trading_app` (DML-only: no TRUNCATE/DDL/ownership, SELECT-only on the migration ledger); schema/maintenance commands (`migrate apply`, `init`, `restore run`, `rechunk`, `caggs repair/refresh`) resolve `MT_TIMESCALE_MAINTENANCE_URL` and fail loudly when unset; test tier moved off superuser onto `trading_test_admin` (CREATEDB/CREATEROLE/pg_signal_backend only — 80,108 prod table grants → 0). Provisioning via idempotent `scripts/provision_roles.sql`, superuser-applied. Also: daemon clean-exit fix (`_do_minute_symbol` polls `should_continue` per chunk; `QuotaBucket.stop_requested` raises `QuotaWaitAborted` with 1s-sliced sleeps; cycle loops re-raise it past the transient-failure handlers) — 20260807 had shown five ignored signals and a required SIGKILL.

---

## 20260805–06

**0.7.5 / 0.7.6 ship + daemon wedge fix** — 0.7.5 shipped slices 186 (API contract hardening) and 187 (symbols ranges via coverage caggs) plus the post-186 window-bound fixes below. 0.7.6 shipped the daily-mode wedge fix: `_select_daily_mode`'s cold-symbol probe (`COUNT(DISTINCT symbol)` with ~31k-element `ANY` over 3,371-chunk `daily_ohlcv`) rewritten as a bounded anti-join on `acquisition_state` (`_WARM_OUTCOMES`), and `make_configure_connection` hoisted from `api_server` to `market/db_session` so all four daemon pools get `DB_BULK_SESSION` (UTC, work_mem, 300s statement_timeout). Incident restore + test-tier prod-URL ratchet work journaled in `000-process-journal.md` 20260805–06.

---

## 20260804

**Post-186 window-bound fixes** — `bars` and `gaps` date semantics.
`bars.py`: split `_date_to_utc_datetime` into `_window_start_utc` (`time.min`) and `_window_end_utc` (`time.max`), so `end` is inclusive at minute grain as it already was at daily grain — the minute path fed `time <= midnight(end)` and silently dropped the last day of every request (SPY Mon–Fri `1m`: 2,975 bars ending 06-13 23:59 → 3,764 ending 06-14 23:59). `gaps.py`: collapsed `_ALL_GAPS_SQL`/`_GRAN_GAPS_SQL`/`_WINDOWED_GAPS_SQL`/`_WINDOWED_GRAN_GAPS_SQL` and the `has_window` `if` ladder into one `_GAPS_SQL` whose optional filters are null-tolerant (`%s::text IS NULL OR granularity = %s`, `COALESCE(%s::timestamptz, ±'infinity')`); the ladder routed a one-sided window into the two-sided query with the other bound as `NULL`, and `gap_start < NULL` never matches, so `?start=` or `?end=` alone returned `count: 0` for every symbol. `_window_end_utc` there is next-midnight rather than `time.max` because the predicate is half-open (`gap_start < %s`) rather than closed. New `test/integration/test_gaps_window_sql.py` (7 tests) seeds real `data_gaps` rows and executes the real statement across all filter combinations — the existing unit tests mock the cursor and assert SQL *text*, so they could not observe the `NULL` annihilation; the new tests were replayed against the pre-fix query shapes to confirm they fail on them. `data_gaps.fetch_status` accepts only `FAILED_RETRYABLE`/`PROVIDER_HOLE`/`RETRY_EXHAUSTED`/`UNKNOWN` (not `PENDING`). 7 unit tests added for the bars bound. Suite 1,804 → 1,811. Reasoning recorded in `000-process-journal.md` 20260804.

---

## 20260504

**Slice 148** — `mt data refetch` operator escape valve.
Extended `_do_daily_symbol`/`_do_minute_symbol` with `force_reset_terminal: bool = False` and `window: tuple[date, date] | None = None`. Added `run_daily_refetch`/`run_minute_refetch` entry points: resolve window defaults (`first_data_date` / last completed session / MINUTE_HISTORY_MONTHS clamp), call `_do_*_symbol` with `force_reset_terminal=True`, call `coalesce_data_gaps` after (daily: in `run_daily_refetch` after `_do_*` returns; minute: already in `_do_minute_symbol` chunk loop). Added `_first_data_date` helper to `daily.py`; imported into `minute.py` to avoid duplication. `mt data refetch` Typer command: validates symbol against instruments table, resolves granularities from `--daily`/`--minute` flags (both False → both), parses `--from`/`--to` to `date`, dry-run via `SELECT` on `data_gaps`, sets up `QuotaBucket` + `QUOTA_BUCKET_VAR` for `eodhd_get` before calling refetch functions, emits Rich preview table + confirmation prompt or JSON output. 28 unit tests (5 `_do_daily_symbol`, 6 `run_daily_refetch`, 7 `_do_minute_symbol`, 5 `run_minute_refetch`); 10 integration tests (skip without DB). Verification walkthrough passed against trading_test using AAPL.

---

**Slice 147** — `mt data status` + `trading_sessions` auto-extension.
New packages: `data/maintenance/` (`auto_extend.py`, `status_queries.py`) and `cli/rendering/` (`status_table.py`). `AutoExtendResult` dataclass; module-level `_last_extend_at` 24h gate (daemon use). `maybe_extend_trading_sessions`: per-calendar MAX probe, calls `populate_trading_sessions` on short horizon, catches INSERT errors per calendar (no re-raise), advances gate only on full success. `fetch_status_rows`/`fetch_symbol_gaps`/`fetch_all_health_counts`: psycopg `dict_row` factory, single GROUP BY for health counts (avoids materializing 114k rows in Python). `HealthStatus` StrEnum; `_health_color`/`_fetch_status_color` via dict lookups. `render_status_summary`: 9-column Rich table. `render_status_footer`: `OK/GAPS/STALE/FAILED` aggregate line; `all_rows=True` appends advisory. `render_status_detail`: Panel per row + sorted gap table. `render_auto_extend_notice`: returns notice string on trigger/error, None on no-op. `status_report_to_json`: `dataclasses.asdict` + `_json_default` encoder (ISO-8601 for dates/datetimes, null for None). `Runner.register_idle_hook`: appends to `_idle_hooks`, called between cycles via `_run_idle_hooks` with `try/except Exception` per hook. Daemon hook injected in `daemon_run` via lambda closure; runner does not import `auto_extend`. Status command: thin Typer entry point — parse flags, open psycopg connections, call `maybe_extend_trading_sessions(bypass_gate=True)`, fetch rows, close connections, then render. 81 unit tests total (7 auto_extend, 16 status_table, 3 idle-hook + 55 existing); 3 integration test files (skipped without DB).

---

## 20260503

**Slice 146** — long-running daemon, named symbol lists, `mt data ca` CLI, CA-drift recompute.
`QuotaBucket`: two `_Window` rolling buckets (1000/60s, 100k/86400s), injectable clock/sleep, `QUOTA_BUCKET_VAR` contextvar for daemon-scoped resolution. `eodhd_get` wraps all outbound calls: 429/Retry-After handling, peer-disconnect retry, raises `QuotaBucketUnsetError` on missing context. Named lists: `load_lists`/`resolve_list` from YAML (`file:` sources relative to config dir); `intersect_with_active` against instruments. `ca_drift.check_and_recompute`: reads `last_adjusted_ca_snapshot_id` from timescale, compares to `current_ca_snapshot` from market DB, recomputes bands + refreshes caggs on mismatch — caller holds advisory lock. Integrated into both cycle functions via `should_continue` callback. `Runner`: `RunnerConfig`/`RunnerState`, cycle-due predicates (`daily_cycle_due`, `minute_cycle_due`, `ca_update_due` sentinel-row-based), `SIGTERM`/`SIGINT` handler restores prior handlers on exit. `make_ca_update_fn(settings)` factory closes over settings for the once-per-UTC-day bulk CA path; `_advance_ca_sentinel` stamps the row on success. `bulk_ca.py`: `fetch_bulk_splits`/`fetch_bulk_dividends` via `/eod-bulk-last-day`. `ca_app` Typer sub-app: `update` (bulk/per-symbol/--list), `show`, `list`. `daemon run` CLI wires `Runner` with all scope/termination flags. Deleted: `daemon daily`, `daemon minute`, entire `adjustment_app`. T29 STOP-GATE: legacy commands already broken post-T16 (QuotaBucketUnsetError), required-identical OHLCV columns unchanged. 10/10 walkthrough steps passed against trading_test. 10 new integration tests (T28–T28e), 2 load tests (T28a), 23 unit tests (T24).

---

## 20260411

**Slice 121** — acquisition state schema and orchestrator core.
Migration 770 creates `acquisition_state` (regular PG table, PK `symbol/granularity/provider`). New `manta_trading.data.acquisition` module: `Granularity`/`AcquisitionStatus` StrEnums, `AcquisitionStateRepository` (upsert/get/list), `AcquisitionEvent` scaffold (`NullEventSink`, `JsonlEventSink`), `run_acquisition_unit` async orchestrator. Key implementation note: plain `async for` cannot catch fetch-side generator exceptions — iterated via explicit `__anext__()` so both fetch and write failures are caught and turned into `CHUNK_FAILED` events with watermark preserved. `mt data state` CLI added to `data.py`. 63 unit tests, 6 integration (skip when DB unavailable).

---

## 20260404 (approx)

**Slice 105** — tick events hypertable schema.
Migration 760: `tick_events` hypertable on separate TimescaleDB instance (`MT_TICK_DB_URL`). Single-table discriminator (`event_type IN ('trade','quote')`), 1-hour chunks, space-partitioned by `instrument_id` (4 partitions), natural key unique index for idempotent ingestion, compression policy 7-day delay. `TickEventType` StrEnum added to `data/base/tick_schema.py`. `Settings.tick_db_url` wired. 16 integration tests.

**Slice 104** — trading calendar rewrite.
`TradingCalendar` rewritten: psycopg2 → psycopg3 `ConnectionPool`, `@lru_cache` → per-instance dict (fixed cross-instance pollution bug), `pytz` → `zoneinfo.ZoneInfo`. Added `is_trading_day()`, `get_holidays()` → `list[Holiday]` with `MarketStatus` StrEnum, `get_trading_hours()` with holiday overrides, `get_expected_bar_count()` with DST handling. `mt data calendars list/holidays` CLI added. 52 tests (40 unit, 6 CLI, 12 integration).

**Slice 103** — instrument registry.
`InstrumentRegistry` rewritten from `NotImplementedError` stubs to full psycopg3 implementation with `ConnectionPool` and per-instance dict cache. `instrument_seed.py` seeds from MarketDB `symbol_list` with `VENUE_MAP`/`CALENDAR_MAP`/`ASSET_CLASS_MAP` constants. `mt data instruments list/seed` CLI added. 46 tests (21 registry unit, 19 seed unit, 10 CLI).

**Slice 102** — foundation tables migration.
Migration 750: `schema_migrations` tracking table, `instruments`, `provider_symbol_mapping`, `trading_calendars`, `trading_holidays`, NYSE/NASDAQ seed data (2020–2026), nullable `instrument_id` FK column on `minute_ohlcv`. `TimescaleMinuteDataDB.apply_schema_migrations()` runner. `mt data migrate` CLI. 48 tests.

**Slice 101** — coverage analysis commands.
`MinuteCoverageAnalyzer` (sync, psycopg3) replaced broken async `TimescaleMinuteDataCoverage`. `mt data minute coverage/metrics`, `mt data daily coverage` CLI commands. `MarketDB.get_daily_coverage()` added. 63 tests.

**Slice 100** — DB layer migration.
`MarketDB` psycopg2 → psycopg3, `TimescaleMinuteDataDB` SQLAlchemy → psycopg3, all with `ConnectionPool`. `market_db_url`/`timescale_db_url` Settings fields. Shared DB skip fixtures in `test/conftest.py`. Removed `psycopg2-binary` and `sqlalchemy` deps.

**Slices 900–903** — foundation.
Typer CLI (`mt`), pydantic-settings `Settings`, `ConfigManager` (TOML, three-level precedence), provider registry (`ProviderType`/`AuthType` StrEnums, `ApiKeyAuthStrategy`), `mt status`, stdlib logging replacing loguru, httpx replacing aiohttp, deprecated CLI entry points and ~2,600 lines removed.
