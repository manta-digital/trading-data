---
docType: devlog
project: trading
dateCreated: 20260411
dateUpdated: 20260503
---

# Development Log

A lightweight, append-only record of development activity. Newest entries first.
Format: `## YYYYMMDD` followed by brief notes (1-3 lines per session). Written from implementor perspective (class names, design decisions, test counts). For user-visible changes see CHANGELOG.md.

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
