-- provision_roles.sql — least-privilege database roles for the `trading` database.
--
-- Slice 913. Enforces the `sql.md` "Production Database Protection" rule
-- "Split connection roles", the one bullet not previously enforced by the
-- server. Motivated by the 2026-08-04 incident, in which a test fixture
-- received the production URL and ran TRUNCATE ... CASCADE against six
-- metadata tables. Production connects as superuser `postgres`, so the
-- credential that inserts bars can also drop the database.
--
-- Two roles:
--   trading_app      DML only. No TRUNCATE, no DDL, no ownership, SELECT-only
--                    on the migration ledger. This is the credential the
--                    daemon, API server, and CLI read paths use. A leak of
--                    this credential dies on `permission denied`.
--   trading_migrate  DDL and Timescale management, supplied only when doing
--                    that work (migrations, rechunk, cagg repair, restore).
--
-- PASSWORDS ARE NOT SET HERE. This file is committed to the repository, so it
-- must never contain credentials. Set passwords out-of-band, e.g.:
--     ALTER ROLE trading_app     WITH PASSWORD '...';
--     ALTER ROLE trading_migrate WITH PASSWORD '...';
--
-- Ownership is deliberately NOT transferred. `postgres` remains the owner of
-- all tables and continuous aggregates. Measured on TimescaleDB 2.23, the
-- `timescaledb_information.*` views do NOT row-filter by ownership, so a
-- non-owner role reads catalog metadata normally; only data access is gated,
-- which plain SELECT grants cover. There is therefore no ALTER ... OWNER in
-- this file, and adding one would be a scope error.
--
-- Idempotent and re-runnable. Apply with:
--     psql "$MT_TIMESCALE_MAINTENANCE_URL" -v ON_ERROR_STOP=1 -f scripts/provision_roles.sql

\echo 'Provisioning least-privilege roles for database:' :DBNAME

BEGIN;

-- ---------------------------------------------------------------------------
-- Roles (idempotent: CREATE ROLE has no IF NOT EXISTS, so guard on pg_roles)
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trading_app') THEN
        CREATE ROLE trading_app LOGIN;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trading_migrate') THEN
        CREATE ROLE trading_migrate LOGIN;
    END IF;
END
$$;

-- trading_migrate inherits ownership rights via role membership rather than
-- per-object ALTER ... OWNER. This keeps the ownership contract of the
-- originating initiative intact while still allowing DDL.
GRANT postgres TO trading_migrate;

-- ---------------------------------------------------------------------------
-- Application role — read surface
-- ---------------------------------------------------------------------------

GRANT CONNECT ON DATABASE trading TO trading_app;
GRANT USAGE ON SCHEMA public TO trading_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO trading_app;

-- Continuous aggregates are views, and ALL TABLES IN SCHEMA public does NOT
-- cover them. They must be enumerated or the coverage/rollup read paths fail
-- with `permission denied for view ...`. Enumerated deliberately so that a new
-- cagg is a visible omission here rather than a silent runtime failure.
GRANT SELECT ON
    daily_coverage,
    daily_weekly_ohlcv,
    daily_monthly_ohlcv,
    daily_quarterly_ohlcv,
    minute_coverage,
    minute_5min_ohlcv,
    minute_15min_ohlcv,
    minute_hourly_ohlcv,
    minute_4hour_ohlcv
TO trading_app;

-- ---------------------------------------------------------------------------
-- Application role — write surface
--
-- Enumerated, not inferred: exactly the tables production code writes. TRUNCATE
-- is a separately grantable PostgreSQL privilege and is deliberately absent —
-- withholding it is precisely what makes the 2026-08-04 statement fail.
-- ---------------------------------------------------------------------------

GRANT INSERT, UPDATE, DELETE ON
    minute_ohlcv,
    daily_ohlcv,
    data_gaps,
    acquisition_state,
    daemon_heartbeat,
    trading_sessions,
    instruments,
    provider_symbol_mapping,
    universe_members,
    splits,
    dividends,
    backfill_state,
    trading_calendars,
    trading_holidays
TO trading_app;

-- The migration ledger is readable but never writable by the application role.
-- The incident deleted from this table; SELECT-only makes that impossible.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON schema_migrations FROM trading_app;
GRANT SELECT ON schema_migrations TO trading_app;

-- Required by the COPY bulk-write hot path, which creates a temp staging table
-- (staging_minute_ohlcv). Without this, all minute ingestion fails.
GRANT TEMPORARY ON DATABASE trading TO trading_app;

-- Identity/serial columns need sequence USAGE for INSERT.
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO trading_app;

-- ---------------------------------------------------------------------------
-- Default privileges — future tables
--
-- Default privileges are scoped to the role that CREATES the object, so both
-- roles must be declared. Without the trading_migrate row, the next migration
-- run under the maintenance credential would silently produce a table the
-- application role cannot read.
-- ---------------------------------------------------------------------------

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO trading_app;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT USAGE ON SEQUENCES TO trading_app;

ALTER DEFAULT PRIVILEGES FOR ROLE trading_migrate IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO trading_app;
ALTER DEFAULT PRIVILEGES FOR ROLE trading_migrate IN SCHEMA public
    GRANT USAGE ON SEQUENCES TO trading_app;

COMMIT;

\echo 'Done. Passwords, if not already set, must be applied out-of-band.'
