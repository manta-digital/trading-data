"""Schema migration definitions for the daily (PostgreSQL MarketDB) track.

Starts with two entries that bring an existing daily DB under management:
  001 — creates the tracking table (idempotent)
  002 — reconciliation marker recording the pre-migration baseline as applied
"""

from __future__ import annotations

DAILY_MIGRATIONS: list[dict[str, str]] = [
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
        "id": "002_reconcile_existing_schema",
        "description": "Reconciliation marker: schema inherited from pre-migration state",
        "sql": "SELECT 1;",
    },
    {
        "id": "003_splits",
        "description": "Create splits table for corporate-action history",
        "sql": """
            CREATE TABLE IF NOT EXISTS splits (
                symbol     TEXT NOT NULL,
                ex_date    DATE NOT NULL,
                ratio_to   NUMERIC(20, 8) NOT NULL,
                ratio_from NUMERIC(20, 8) NOT NULL,
                source     TEXT NOT NULL DEFAULT 'eodhd',
                fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (symbol, ex_date)
            );
        """,
    },
    {
        "id": "004_dividends",
        "description": "Create dividends table for cash-distribution history",
        "sql": """
            CREATE TABLE IF NOT EXISTS dividends (
                symbol     TEXT NOT NULL,
                ex_date    DATE NOT NULL,
                amount     NUMERIC(20, 8) NOT NULL,
                currency   TEXT NOT NULL DEFAULT 'USD',
                source     TEXT NOT NULL DEFAULT 'eodhd',
                fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (symbol, ex_date)
            );
        """,
    },
]
