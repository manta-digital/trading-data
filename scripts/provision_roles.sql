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
--
-- Parameterized on database and role names so the *same* artifact production
-- applies is the one the test suite exercises (D8). A fixture that applied its
-- own derived grant set could pass while this file is wrong. Defaults below
-- mean the production invocation above needs no extra arguments.
--
-- Roles are cluster-wide (`pg_authid` is a shared catalog) while table grants
-- are per-database, so a test run against a throwaway database on the same
-- cluster MUST pass throwaway role names — otherwise it mutates the very roles
-- production depends on:
--     psql "$URL" -v app_role=trading_app_t1 -v migrate_role=trading_migrate_t1 \
--          -v ON_ERROR_STOP=1 -f scripts/provision_roles.sql

\if :{?app_role}
\else
  \set app_role trading_app
\endif

\if :{?migrate_role}
\else
  \set migrate_role trading_migrate
\endif

\echo 'Provisioning least-privilege roles on database:' :DBNAME
\echo '  application role:' :app_role
\echo '  maintenance role:' :migrate_role

BEGIN;

-- ---------------------------------------------------------------------------
-- Roles (idempotent: CREATE ROLE has no IF NOT EXISTS, so guard on pg_roles)
--
-- Uses \gexec rather than a DO block: psql does not interpolate variables
-- inside a dollar-quoted body, so :'app_role' there is a syntax error.
-- \gexec runs the *result* of the query, and the query emits nothing when the
-- role already exists — which is the idempotency guard.
-- ---------------------------------------------------------------------------

SELECT format('CREATE ROLE %I LOGIN', :'app_role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_role')
\gexec

SELECT format('CREATE ROLE %I LOGIN', :'migrate_role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'migrate_role')
\gexec

-- The maintenance role inherits ownership rights via role membership rather
-- than per-object ALTER ... OWNER, keeping the originating initiative's
-- ownership contract intact while still allowing DDL. Granting `current_user`
-- rather than a hardcoded `postgres` keeps this correct on a test database
-- owned by whichever role created it.
SELECT format('GRANT %I TO %I', current_user, :'migrate_role')
\gexec

-- ---------------------------------------------------------------------------
-- Application role — read surface
-- ---------------------------------------------------------------------------

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'DBNAME', :'app_role')
\gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'app_role')
\gexec
SELECT format('GRANT SELECT ON ALL TABLES IN SCHEMA public TO %I', :'app_role')
\gexec

-- Continuous aggregates are views, and ALL TABLES IN SCHEMA public does NOT
-- cover them; without an explicit grant the coverage/rollup read paths fail
-- with `permission denied for view ...`.
--
-- Discovered from the catalog rather than hardcoded, because the set differs by
-- database: production carries 9, while a freshly migrated database carries
-- only what its migration chain has materialized. A static list would make this
-- artifact inapplicable to any database but prod — and the point of
-- parameterizing it is that the tested file is the applied file.
SELECT format('GRANT SELECT ON %I.%I TO %I', view_schema, view_name, :'app_role')
FROM timescaledb_information.continuous_aggregates
\gexec

-- ---------------------------------------------------------------------------
-- Application role — write surface
--
-- Enumerated, not inferred: exactly the tables production code writes. TRUNCATE
-- is a separately grantable PostgreSQL privilege and is deliberately absent —
-- withholding it is precisely what makes the 2026-08-04 statement fail.
-- ---------------------------------------------------------------------------

-- The list is enumerated (D3: write surface is enumerated, not inferred), but
-- filtered against pg_tables so the artifact also applies to a freshly migrated
-- database whose chain has not created every table. A table named here but
-- absent from the target is silently skipped; a table present but *not* named
-- here gets SELECT only, which is the intended default.
SELECT format('GRANT INSERT, UPDATE, DELETE ON %I TO %I', tablename, :'app_role')
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN (
      'minute_ohlcv',
      'daily_ohlcv',
      'data_gaps',
      'acquisition_state',
      'daemon_heartbeat',
      'trading_sessions',
      'instruments',
      'provider_symbol_mapping',
      'universe_members',
      'splits',
      'dividends',
      'backfill_state',
      'trading_calendars',
      'trading_holidays'
  )
\gexec

-- The migration ledger is readable but never writable by the application role.
-- The incident deleted from this table; SELECT-only makes that impossible.
-- Guarded on existence so the artifact also applies to a database whose
-- migration chain has not yet created the ledger.
SELECT format(
    'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON schema_migrations FROM %I',
    :'app_role')
FROM pg_tables WHERE schemaname = 'public' AND tablename = 'schema_migrations'
\gexec
SELECT format('GRANT SELECT ON schema_migrations TO %I', :'app_role')
FROM pg_tables WHERE schemaname = 'public' AND tablename = 'schema_migrations'
\gexec

-- Required by the COPY bulk-write hot path, which creates a temp staging table
-- (staging_minute_ohlcv). Without this, all minute ingestion fails.
SELECT format('GRANT TEMPORARY ON DATABASE %I TO %I', :'DBNAME', :'app_role')
\gexec

-- Identity/serial columns need sequence USAGE for INSERT.
SELECT format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO %I', :'app_role')
\gexec

-- ---------------------------------------------------------------------------
-- Default privileges — future tables
--
-- Default privileges are scoped to the role that CREATES the object, so both
-- roles must be declared. Without the trading_migrate row, the next migration
-- run under the maintenance credential would silently produce a table the
-- application role cannot read.
-- ---------------------------------------------------------------------------

-- `current_user` covers the role that owns the existing schema (postgres on
-- prod, whichever role created the database in a test). The maintenance role is
-- declared separately because a migration run under it creates tables the
-- application role must still be able to read.
SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
    'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
    role_name, :'app_role')
FROM (VALUES (current_user), (:'migrate_role')) AS r(role_name)
\gexec

SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
    'GRANT USAGE ON SEQUENCES TO %I',
    role_name, :'app_role')
FROM (VALUES (current_user), (:'migrate_role')) AS r(role_name)
\gexec

COMMIT;

\echo 'Done. Passwords, if not already set, must be applied out-of-band.'
