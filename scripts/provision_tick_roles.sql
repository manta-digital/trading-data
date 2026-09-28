-- provision_tick_roles.sql — the tick database, its two roles, and its isolation.
--
-- Slice 923 (D3, D4). The tick database (initiative 220) is kept apart from the
-- primary `trading` database so that a failure in the tick tier cannot reach
-- production. REVIEW THIS FILE SIDE BY SIDE WITH provision_roles.sql: the role
-- guards and the ledger rule are the same, and the ownership model and write
-- surface deliberately differ.
--
-- Two roles, never the primary ones (D3). Roles are cluster-wide, so reusing
-- trading_app / trading_migrate would let one leaked credential reach both
-- databases on a shared cluster, and on a separate cluster they do not exist:
--   tick_app      DML on the enumerated write surface (empty until slice 222),
--                 TEMPORARY for COPY staging, SELECT-only on the ledger.
--   tick_migrate  Owns the tick database and everything its migrations create.
--
-- Ownership differs from the primary on purpose (D4). The primary gives its
-- maintenance role DDL rights through `GRANT postgres TO trading_migrate`.
-- Copying that here would put tick_migrate inside `postgres`, and so inside
-- `trading` on a shared cluster. Instead this file creates the database OWNER
-- tick_migrate: as owner it can run DDL and install the TimescaleDB extension
-- (measured, see test/integration/data/test_tick_role_privileges.py) without
-- any membership in `postgres`. There is no GRANT postgres and no ALTER ...
-- OWNER in this file, and adding either would be a scope error.
--
-- PASSWORDS ARE NOT SET HERE. This file is committed, so it must never contain
-- credentials. Set passwords out of band:
--     ALTER ROLE tick_app     WITH PASSWORD '...';
--     ALTER ROLE tick_migrate WITH PASSWORD '...';
--
-- Parameters:
--   tick_db       REQUIRED, no default. The production name is part of the
--                 placement decision (PM), so the file never guesses one.
--   app_role      optional, default tick_app.
--   migrate_role  optional, default tick_migrate. Tests pass per-run names so
--                 the tested file is the applied file without touching real
--                 roles (roles are cluster-wide).
--
-- Apply as a superuser, connected to any existing database (e.g. `postgres`);
-- the file creates the tick database and then connects to it:
--     psql "$SUPERUSER_URL" -v tick_db=<name> -f scripts/provision_tick_roles.sql
--
-- Idempotent: every CREATE is guarded, and re-running it re-applies the grants.
-- Re-run it after `mt data init --database tick` creates the ledger, so the
-- ledger rule below applies (see "Migration ledger").

\set ON_ERROR_STOP on

\if :{?tick_db}
\else
  \echo 'provision_tick_roles.sql: -v tick_db=<name> is required (no default: the name is a placement decision).'
  DO $$ BEGIN RAISE EXCEPTION 'tick_db not supplied'; END $$;
\endif

\if :{?app_role}
\else
  \set app_role tick_app
\endif

\if :{?migrate_role}
\else
  \set migrate_role tick_migrate
\endif

\echo 'Provisioning tick database:' :tick_db
\echo '  application role:' :app_role
\echo '  maintenance role:' :migrate_role

-- ---------------------------------------------------------------------------
-- Roles (idempotent: CREATE ROLE has no IF NOT EXISTS, so guard on pg_roles).
-- \gexec, not a DO block: psql does not interpolate inside a dollar-quoted body.
-- ---------------------------------------------------------------------------

BEGIN;

SELECT format('CREATE ROLE %I LOGIN', :'app_role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_role')
\gexec

-- LOGIN only, no other attribute. Measured 20260927 on PostgreSQL 17.11 with
-- TimescaleDB 2.29.1: as owner of the tick database this role runs the
-- migrations and `CREATE EXTENSION timescaledb` without CREATEDB, CREATEROLE,
-- SUPERUSER, or membership in `postgres`.
SELECT format('CREATE ROLE %I LOGIN', :'migrate_role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'migrate_role')
\gexec

-- PostgreSQL 16 changed CREATEROLE: a non-superuser creator gets ADMIN on the
-- roles it makes but not SET, and `CREATE DATABASE ... OWNER r` requires SET
-- on r ("must be able to SET ROLE"). This is the fix the 913 test fixture
-- applies, moved here because this file now creates the database (LLD D8).
-- Emitted only for a non-superuser applier, so production (superuser) never
-- gains a membership from it.
SELECT format('GRANT %I TO %I WITH SET TRUE', :'migrate_role', current_user)
WHERE NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)
  AND NOT pg_has_role(current_user, :'migrate_role', 'SET')
\gexec

COMMIT;

-- ---------------------------------------------------------------------------
-- Database. CREATE DATABASE cannot run inside a transaction block.
-- ---------------------------------------------------------------------------

SELECT format('CREATE DATABASE %I OWNER %I', :'tick_db', :'migrate_role')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'tick_db')
\gexec

\connect :"tick_db"

BEGIN;

-- ---------------------------------------------------------------------------
-- Isolation. PUBLIC's default CONNECT would let every role on a shared cluster
-- (trading_app included) open the tick database.
-- ---------------------------------------------------------------------------

SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', :'tick_db')
\gexec
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I', :'tick_db', :'app_role')
\gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'app_role')
\gexec

-- ---------------------------------------------------------------------------
-- Default privileges: tables tick_migrate creates are usable by tick_app.
-- Scoped to the creating role, which is always tick_migrate here because the
-- migrations run under the maintenance credential.
-- ---------------------------------------------------------------------------

SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
    'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
    :'migrate_role', :'app_role')
\gexec

-- ---------------------------------------------------------------------------
-- Write surface — enumerated, not inferred. Empty until slice 222 adds the
-- tick tables; filtered on pg_tables so the file applies before they exist.
-- ---------------------------------------------------------------------------

-- (none yet)

-- ---------------------------------------------------------------------------
-- Migration ledger: readable, never writable, by the application role.
-- Guarded on existence, because on a first run the ledger does not exist yet;
-- `mt data init --database tick` creates it.
-- ---------------------------------------------------------------------------

SELECT format(
    'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON schema_migrations FROM %I',
    :'app_role')
FROM pg_tables WHERE schemaname = 'public' AND tablename = 'schema_migrations'
\gexec
SELECT format('GRANT SELECT ON schema_migrations TO %I', :'app_role')
FROM pg_tables WHERE schemaname = 'public' AND tablename = 'schema_migrations'
\gexec

COMMIT;

\echo 'Done. Passwords, if not already set, must be applied out of band.'
