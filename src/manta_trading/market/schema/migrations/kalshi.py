"""Schema migration definitions for the ``kalshi`` track (slice 261).

The track targets the shared ``trading`` database (TimescaleDB host) and
creates PostgreSQL schema ``kalshi``: catalog tables (series, events,
markets) and collection-state tables (sync_state, awaiting_settlement,
market_candle_state). No hypertables and no candle/trade *data* tables —
those arrive with their collectors in slices 264/265.

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

from enum import Enum

from manta_trading.data.kalshi.constants import CandlePeriod, MarketStatus, Surface

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
]
