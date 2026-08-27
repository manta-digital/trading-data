"""Schema migration definitions for the ``kalshi`` track (slice 261).

The track targets the shared ``trading`` database (TimescaleDB host) and
creates PostgreSQL schema ``kalshi``: catalog tables (series, events,
markets), collection-state tables (sync_state, awaiting_settlement,
market_candle_state), and — from ``kalshi_005`` (slice 264) — the
``candlesticks`` hypertable. The track assumes the TimescaleDB extension is
already installed in the target database (the minute track's ``001a``
created it on ``trading``); ``create_hypertable`` fails loudly otherwise.
The trades data table arrives with its collector in slice 265.

Migration IDs are prefixed ``kalshi_NNN_*`` because the ledger
(``schema_migrations``) is shared with the minute track. The first entry is
the standard ``001_schema_migrations`` bootstrap: already recorded on
production (a no-op there), and what lets the unchanged runner bootstrap a
bare throwaway database in tests.

Extraction discipline (design 261, Technical Decision 1): nothing in this
schema references a ``public`` object — no foreign keys, joins, or views —
so the eventual move to a dedicated database is a dump/restore.

SQL is idempotent (IF NOT EXISTS). CHECK constraints for lifecycle and
collection enums are rendered from the enums in ``data/kalshi/constants.py``
(the ``acquisition_state`` precedent), never hand-listed. Grants target the
``trading_app`` role by name: a missing role fails loudly by design.
"""

from __future__ import annotations

from datetime import timedelta
from enum import Enum

from manta_trading.data.kalshi.constants import (
    KALSHI_CANDLE_CHUNK_INTERVAL,
    KALSHI_CANDLE_COMPRESS_AFTER,
    CandlePeriod,
    MarketStatus,
    Surface,
)

#: DML-only application role (slice 913 role split). Never TRUNCATE/DDL.
APP_ROLE = "trading_app"


def _in_list(members: type[Enum]) -> str:
    """Render an enum's values as a SQL ``IN (...)`` list, sorted by value."""
    return ", ".join(
        repr(m.value) if isinstance(m.value, str) else str(m.value)
        for m in sorted(members, key=lambda m: m.value)
    )


def _status_check_sql() -> str:
    return f"CHECK (status IN ({_in_list(MarketStatus)}))"


def _surface_check_sql() -> str:
    return f"CHECK (surface IN ({_in_list(Surface)}))"


def _period_check_sql() -> str:
    return f"CHECK (period IN ({_in_list(CandlePeriod)}))"


def _interval_sql(span: timedelta) -> str:
    """Render a timedelta as a SQL INTERVAL literal, seconds-based so any
    value renders exactly (the minute track's ``_minute_chunk_interval_sql``
    idiom) — the constant stays the single definition of the number."""
    return f"INTERVAL '{int(span.total_seconds())} seconds'"


KALSHI_MIGRATIONS: list[dict[str, str]] = [
    {
        "id": "001_schema_migrations",
        "description": "Create schema_migrations tracking table",
        "sql": """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id VARCHAR(64) PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                description TEXT
            );
        """,
    },
    {
        "id": "kalshi_001_schema",
        "description": "Create kalshi schema and grant usage to the application role",
        "sql": f"""
            CREATE SCHEMA IF NOT EXISTS kalshi;
            GRANT USAGE ON SCHEMA kalshi TO {APP_ROLE};
        """,
    },
    {
        "id": "kalshi_002_catalog",
        "description": "Create kalshi catalog tables: series, events, markets",
        # Column names follow the API field names verbatim (``*_dollars`` /
        # ``*_fp`` included), and each table's column set equals its model's
        # field set plus our three bookkeeping columns — enforced by
        # ``test_kalshi_migrations.py::TestModelColumnParity`` — so 262's
        # upsert can map model fields to columns one-to-one. Optional columns
        # are the keys observed in the recorded fixtures (design: Recording
        # cross-check); everything else stays in ``raw``. ``first_seen_at`` /
        # ``last_synced_at`` are ours; ``last_updated_ts`` / ``updated_time``
        # are Kalshi's and are the raw material for incremental sync.
        "sql": f"""
            CREATE TABLE IF NOT EXISTS kalshi.series (
                ticker              TEXT PRIMARY KEY,
                frequency           TEXT,
                title               TEXT,
                category            TEXT,
                tags                JSONB,
                settlement_sources  JSONB,
                fee_type            TEXT,
                fee_multiplier      NUMERIC,
                contract_url        TEXT,
                contract_terms_url  TEXT,
                product_metadata    JSONB,
                last_updated_ts     TIMESTAMPTZ,
                raw                 JSONB NOT NULL,
                first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_synced_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS kalshi.events (
                event_ticker            TEXT PRIMARY KEY,
                series_ticker           TEXT NOT NULL REFERENCES kalshi.series (ticker),
                title                   TEXT,
                sub_title               TEXT,
                category                TEXT,
                mutually_exclusive      BOOLEAN,
                strike_date             TIMESTAMPTZ,
                strike_period           TEXT,
                collateral_return_type  TEXT,
                available_on_brokers    BOOLEAN,
                settlement_sources      JSONB,
                product_metadata        JSONB,
                last_updated_ts         TIMESTAMPTZ,
                raw                     JSONB NOT NULL,
                first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_synced_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS events_series_ticker_idx
                ON kalshi.events (series_ticker);

            CREATE TABLE IF NOT EXISTS kalshi.markets (
                ticker                    TEXT PRIMARY KEY,
                event_ticker              TEXT NOT NULL
                    REFERENCES kalshi.events (event_ticker),
                market_type               TEXT,
                status                    TEXT NOT NULL,
                title                     TEXT,
                subtitle                  TEXT,
                yes_sub_title             TEXT,
                no_sub_title              TEXT,
                rules_primary             TEXT,
                rules_secondary           TEXT,
                -- lifecycle (Kalshi's timestamps)
                created_time              TIMESTAMPTZ,
                open_time                 TIMESTAMPTZ,
                close_time                TIMESTAMPTZ NOT NULL,
                expiration_time           TIMESTAMPTZ,
                expected_expiration_time  TIMESTAMPTZ,
                latest_expiration_time    TIMESTAMPTZ,
                updated_time              TIMESTAMPTZ,
                -- settlement
                result                    TEXT,
                expiration_value          TEXT,
                can_close_early           BOOLEAN,
                settlement_ts             TIMESTAMPTZ,
                settlement_value_dollars  NUMERIC,
                -- economics (fixed-point strings on the wire)
                notional_value_dollars    NUMERIC,
                last_price_dollars        NUMERIC,
                previous_price_dollars    NUMERIC,
                yes_bid_dollars           NUMERIC,
                yes_ask_dollars           NUMERIC,
                no_bid_dollars            NUMERIC,
                no_ask_dollars            NUMERIC,
                liquidity_dollars         NUMERIC,
                volume_fp                 NUMERIC,
                volume_24h_fp             NUMERIC,
                open_interest_fp          NUMERIC,
                yes_bid_size_fp           NUMERIC,
                yes_ask_size_fp           NUMERIC,
                previous_yes_bid_dollars  NUMERIC,
                previous_yes_ask_dollars  NUMERIC,
                -- classification
                strike_type               TEXT,
                price_level_structure     TEXT,
                is_provisional            BOOLEAN,
                mve_collection_ticker     TEXT,
                raw                       JSONB NOT NULL,
                first_seen_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_synced_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT markets_status_check {_status_check_sql()}
            );
            CREATE INDEX IF NOT EXISTS markets_event_ticker_idx
                ON kalshi.markets (event_ticker);
            CREATE INDEX IF NOT EXISTS markets_status_idx
                ON kalshi.markets (status);
            CREATE INDEX IF NOT EXISTS markets_close_time_idx
                ON kalshi.markets (close_time);

            GRANT SELECT, INSERT, UPDATE, DELETE
                ON kalshi.series, kalshi.events, kalshi.markets TO {APP_ROLE};
        """,
    },
    {
        "id": "kalshi_003_collection_state",
        "description": "Create kalshi collection-state tables",
        # Row semantics are finalized operationally by the consuming slices
        # (262 catalog sync, 264 candles); the columns are fixed here so they
        # write into a stable schema.
        "sql": f"""
            CREATE TABLE IF NOT EXISTS kalshi.sync_state (
                surface            TEXT PRIMARY KEY,
                last_full_sync_at  TIMESTAMPTZ,
                watermark_ts       TIMESTAMPTZ,
                cursor             TEXT,
                updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT sync_state_surface_check {_surface_check_sql()}
            );
            COMMENT ON TABLE kalshi.sync_state IS
                'One row per collection surface (catalog, candlesticks, trades).';
            COMMENT ON COLUMN kalshi.sync_state.last_full_sync_at IS
                'catalog: end of the last complete walk of series/events/markets; '
                'candlesticks/trades: end of the last full pass over their '
                'market sets.';
            COMMENT ON COLUMN kalshi.sync_state.watermark_ts IS
                'catalog: the min_updated_ts to pass on the next incremental sync; '
                'trades: created_time of the newest stored trade; '
                'candlesticks: unused at surface level (see market_candle_state).';
            COMMENT ON COLUMN kalshi.sync_state.cursor IS
                'Resume cursor of an interrupted page walk; NULL when none is '
                'in progress.';

            CREATE TABLE IF NOT EXISTS kalshi.awaiting_settlement (
                market_ticker    TEXT PRIMARY KEY REFERENCES kalshi.markets (ticker),
                close_time       TIMESTAMPTZ NOT NULL,
                entered_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_checked_at  TIMESTAMPTZ
            );
            COMMENT ON TABLE kalshi.awaiting_settlement IS
                'Closed markets whose settlement has not been captured yet; '
                'age is now() - close_time; the stuck threshold is slice 262''s '
                'decision.';

            CREATE TABLE IF NOT EXISTS kalshi.market_candle_state (
                market_ticker  TEXT NOT NULL REFERENCES kalshi.markets (ticker),
                period         SMALLINT NOT NULL,
                watermark_ts   TIMESTAMPTZ,
                updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (market_ticker, period),
                CONSTRAINT market_candle_state_period_check {_period_check_sql()}
            );
            COMMENT ON COLUMN kalshi.market_candle_state.watermark_ts IS
                'end_period_ts of the newest stored candle for this market and '
                'period (slice 264).';

            GRANT SELECT, INSERT, UPDATE, DELETE
                ON kalshi.sync_state, kalshi.awaiting_settlement,
                   kalshi.market_candle_state
                TO {APP_ROLE};
        """,
    },
    {
        "id": "kalshi_004_catalog_sync_semantics",
        "description": "Fix kalshi.sync_state column comments to slice 262 semantics",
        # Comments only, no shape change (design 262: State Management).
        "sql": """
            COMMENT ON COLUMN kalshi.sync_state.last_full_sync_at IS
                'catalog: start time of the last run whose full walk '
                '(series, markets, events) completed; also the min_updated_ts '
                'floor for the events refresh. NULL until the first '
                'successful walk. candlesticks/trades: end of the last full '
                'pass over their market sets.';
            COMMENT ON COLUMN kalshi.sync_state.watermark_ts IS
                'catalog: settlement_ts upper bound of the last completed '
                'settled window - every non-MVE market with settlement_ts '
                'before this has been captured; NULL until the first window '
                'completes. trades: created_time of the newest stored trade; '
                'candlesticks: unused at surface level (see '
                'market_candle_state).';
            COMMENT ON COLUMN kalshi.sync_state.cursor IS
                'catalog: unused - windows replace cursor resume. trades: '
                'resume cursor of an interrupted page walk; NULL when none '
                'is in progress.';
        """,
    },
    {
        "id": "kalshi_005_candlesticks",
        "description": (
            "Create the kalshi.candlesticks hypertable with compression, add "
            "market_candle_state.coverage_from_ts, fix watermark comments"
        ),
        # Slice 264. The nested ``yes_bid`` / ``yes_ask`` / ``price`` OHLC
        # objects flatten to sixteen nullable NUMERIC columns (Decision 10;
        # the map is ``candle_repository.CANDLE_COLUMNS``, parity-tested
        # against this table). Hypertable from creation, chunked and
        # compressed per Decision 4 — both horizons render from the constants.
        # ``watermark_ts`` semantics per Decision 3 replace kalshi_003's text,
        # which was wrong for sparse data. ``COMMENT ON`` replaces the whole
        # string, so the sync_state comment carries kalshi_004's catalog and
        # trades clauses forward and changes only the candlesticks clause
        # (Decision 11).
        "sql": f"""
            CREATE TABLE IF NOT EXISTS kalshi.candlesticks (
                market_ticker           TEXT        NOT NULL
                    REFERENCES kalshi.markets (ticker),
                period                  SMALLINT    NOT NULL,
                end_period_ts           TIMESTAMPTZ NOT NULL,
                yes_bid_open_dollars    NUMERIC,
                yes_bid_high_dollars    NUMERIC,
                yes_bid_low_dollars     NUMERIC,
                yes_bid_close_dollars   NUMERIC,
                yes_ask_open_dollars    NUMERIC,
                yes_ask_high_dollars    NUMERIC,
                yes_ask_low_dollars     NUMERIC,
                yes_ask_close_dollars   NUMERIC,
                price_open_dollars      NUMERIC,
                price_high_dollars      NUMERIC,
                price_low_dollars       NUMERIC,
                price_close_dollars     NUMERIC,
                price_previous_dollars  NUMERIC,
                price_mean_dollars      NUMERIC,
                volume_fp               NUMERIC     NOT NULL,
                open_interest_fp        NUMERIC,
                PRIMARY KEY (market_ticker, period, end_period_ts),
                CONSTRAINT candlesticks_period_check {_period_check_sql()}
            );
            SELECT create_hypertable(
                'kalshi.candlesticks',
                'end_period_ts',
                chunk_time_interval => {_interval_sql(KALSHI_CANDLE_CHUNK_INTERVAL)},
                if_not_exists       => TRUE
            );
            ALTER TABLE kalshi.candlesticks SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'market_ticker',
                timescaledb.compress_orderby   = 'end_period_ts DESC'
            );
            SELECT add_compression_policy(
                'kalshi.candlesticks',
                compress_after => {_interval_sql(KALSHI_CANDLE_COMPRESS_AFTER)},
                if_not_exists  => TRUE
            );

            ALTER TABLE kalshi.market_candle_state
                ADD COLUMN IF NOT EXISTS coverage_from_ts TIMESTAMPTZ;
            COMMENT ON COLUMN kalshi.market_candle_state.watermark_ts IS
                'candles requested and stored through this instant (window '
                'end, clamped to close_time + period) - NOT the newest stored '
                'candle: Kalshi serves no candle for an idle period (slice '
                '264, Decision 3)';
            COMMENT ON COLUMN kalshi.market_candle_state.coverage_from_ts IS
                'start of the first window ever requested; equals open_time '
                'only when the market was first seen young (slice 264, '
                'Decision 5)';
            COMMENT ON COLUMN kalshi.sync_state.watermark_ts IS
                'catalog: settlement_ts upper bound of the last completed '
                'settled window - every non-MVE market with settlement_ts '
                'before this has been captured; NULL until the first window '
                'completes. trades: created_time of the newest stored trade. '
                'candlesticks: market_settled_ts of the historical cutoff '
                'observed by the last candle phase (slice 264, Decision 11).';

            GRANT SELECT, INSERT, UPDATE, DELETE ON kalshi.candlesticks TO {APP_ROLE};
        """,
    },
]
