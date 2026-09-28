---
docType: slice-design
slice: multi-database-migration-and-credential-plumbing
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [913, 917]
interfaces: [222]
dateCreated: 20260927
dateUpdated: 20260927
status: complete
---

# Slice Design: Multi-Database Migration and Credential Plumbing

## Overview

The migration and credential code assumes a single database. Every track in
`TRACKS` ([migrations/\_\_init\_\_.py:13](../../../src/manta_trading/market/schema/migrations/__init__.py))
is applied through one maintenance URL (`_get_maintenance_url`,
[data.py:444](../../../src/manta_trading/cli/commands/data.py)). The `--track`
option's own comment says "both tracks target the same database, so no new
connection plumbing". There is one migration ledger, one grant artifact
(`scripts/provision_roles.sql`), and one throwaway database per test.
`MT_TICK_DB_URL` (`Settings.tick_db_url`) exists, but nothing reads it.

Initiative 220 puts tick data in a separate database (220-arch, "Database
placement and roles"), and its storage track (slice 222) cannot start until the
code can reach a second database. This slice adds that, placement-agnostic. The
PM has not yet chosen whether the tick database is a second database on the
production cluster, a second cluster, or a second host, and the design works
unchanged for all three.

The slice delivers:

- **Track routing.** Each migration track declares which database it belongs
  to. `mt data migrate apply|status` and `mt data init` connect to that
  database.
- **A credential pair per database.** The new `MT_TICK_MAINTENANCE_URL` sits
  beside the existing, unused `MT_TICK_DB_URL`. The same fail-loud,
  no-fallback rule that 913 set for the primary database applies.
- **A tick grant artifact.** `scripts/provision_tick_roles.sql` gives the tick
  database its own application and maintenance roles, so a leaked credential
  for one database cannot touch the other.
- **A misroute guard.** Migrations refuse to run against a database whose
  ledger belongs to the other database. If `MT_TICK_MAINTENANCE_URL` is
  mistakenly set to the production URL, nothing gets created in `trading`.
- **A registered `tick` track** holding only the ledger bootstrap. Slice 222
  appends its tables to it.
- **Test fixtures (917)** that create a throwaway tick database beside the
  throwaway primary on the test cluster. The production-URL guards are
  extended to the tick variables.

The primary database's migration path behaves exactly as before.

## Value

- **Architectural enablement.** Slice 222 and every slice after it in
  initiative 220 are gated on this one, with no fallback (220 slice plan,
  "923 is the one external gate"). Once it lands, 222 only has to append its
  migrations to a track that already reaches the right database.
- **The isolation goal holds under misconfiguration, not just in the design.**
  Putting tick tables in the production database is the placement 220 rules
  out. A copy-paste error in `.env` would have produced exactly that, silently.
  The misroute guard turns it into an error that names the offending migration
  ids.
- **Least privilege extends to the second database.** The tick roles have no
  grants on `trading`, and the primary roles cannot connect to the tick
  database. A leaked tick credential cannot damage minute data, and a leaked
  primary credential cannot damage tick data.
- **Reusable foundation.** Any later second database (a dedicated Kalshi
  instance, an archive database) adds one `Database` member, one pair of
  settings, and a grant artifact. No migration-runner or CLI code changes.

## Technical Scope

**Included**

1. A `Database` enum (`PRIMARY`, `TICK`) and a `Credential` enum
   (`APPLICATION`, `MAINTENANCE`), plus one table mapping each
   (database, credential) pair to its `Settings` field.
2. A track registry that pairs every track with its database. `TRACKS` stays
   as a derived view, so no existing consumer changes.
3. A shared URL resolver that is not in the CLI. It raises a typed error naming
   the environment variable, and there is no cross-database or
   cross-credential fallback. `_get_maintenance_url(ctx)` becomes a thin
   wrapper around it.
4. `mt data migrate apply` and `status` route by `--track`. `mt data init`
   gains `--database` (default `primary`, so the no-argument invocation is
   unchanged).
5. A ledger-ownership guard that runs before any migration is applied.
6. The new setting `Settings.tick_maintenance_url` (`MT_TICK_MAINTENANCE_URL`).
   `tick_db_url` gets its first consumer.
7. A new `tick` track, `migrations/tick.py`, containing only the
   `001_schema_migrations` bootstrap. Tick ids after it use the `tick_NNN_*`
   prefix.
8. `scripts/provision_tick_roles.sql`: roles `tick_app` and `tick_migrate`,
   database creation owned by `tick_migrate`, and cross-database isolation.
9. Test fixtures: a shared throwaway-database helper, `ephemeral_tick_db`, a
   tick privilege suite, and a two-database routing suite.
10. The production-URL guards (runtime scrub, static ratchet, and
    `run_tests.py`) extended to `MT_TICK_DB_URL` and `MT_TICK_MAINTENANCE_URL`.
11. Removal of `test/integration/test_tick_schema_integration.py`, a slice 105
    leftover. It reads `MT_TICK_DB_URL` and applies DDL from a
    `database/migrations/` directory that was deleted in slice 150.
12. Documentation: the migrations README (its track table is stale), the
    `.env_sample` entries, and a runbook section on provisioning a tick
    database.

**Excluded**

- **Creating a production tick database.** Placement is the PM's decision
  (220-arch), made after the proof's size measurements. This slice ships the
  artifact and proves it on the test cluster. Running it in production belongs
  to the placement decision and needs no code.
- **Tick tables, the TimescaleDB extension on the tick database, and the tick
  write-surface grants.** These are slice 222.
- **Second connection pools for overview, health, status, accounting, and the
  API server** (220-arch, "composition is cross-database"). Each process gains
  one when it takes on a tick duty (223 onward). None has one yet.
- **Backup of the tick database.** Slice 227.
- **Changing which primary tracks `mt data init` applies.** See Special
  Considerations: it applies only `minute`, not `kalshi`.

## Dependencies

### Prerequisites

- **913 Least-Privilege Database Roles (complete).** It supplies the
  application/maintenance split, the fail-loud maintenance resolver, the
  parameterized grant artifact, `trading_test_admin`, and the PostgreSQL 16
  `WITH SET TRUE` fixture pattern ([test_role_privileges.py:99](../../../test/integration/data/test_role_privileges.py)).
- **917 Dedicated Test Database Cluster (complete).** It supplies the
  hammerhead cluster, `MT_TIMESCALE_TEST_URL`, `require_usable_test_endpoint`,
  and the `ephemeral_db` pattern.
- No new packages. psycopg, psycopg_pool, Typer, and psql are already in use.

### Interfaces Required

- `market/schema/runner.py`: `apply_migrations(pool, migrations)` and
  `list_migration_state(pool, migrations)`, unchanged. Both already take a pool
  and a track, and bootstrap a ledger on whatever database the pool reaches.
- `TimescaleMinuteDataDB(conninfo)` is used, as today, as the pool wrapper that
  migrate commands run under. Its `DB_BULK_SESSION` settings are what every
  migration currently runs with, and 222's hypertable DDL wants the same.

## Architecture

### Component Structure

```
config/__init__.py              Settings: + tick_maintenance_url (tick_db_url exists)

market/schema/databases.py      NEW — the one definition of database identity
  Database(StrEnum)             PRIMARY, TICK
  Credential(StrEnum)           APPLICATION, MAINTENANCE
  DATABASE_URL_FIELDS           {(Database, Credential): settings field name}
  env_var_for(db, cred)         "MT_" + field.upper()   (Settings env_prefix)
  DatabaseNotConfiguredError    carries env_var; message names it
  resolve_database_url(settings, db, cred) -> str
  assert_ledger_belongs(pool, db)          misroute guard (D5)

market/schema/migrations/
  __init__.py                   TRACK_REGISTRY: {name: TrackSpec(database, migrations)}
                                TRACKS (derived, unchanged shape), DEFAULT_TRACK,
                                DEFAULT_TRACK_FOR: {Database: track name}
  tick.py                       NEW — TICK_MIGRATIONS = [001_schema_migrations]

cli/commands/data.py            migrate apply/status: route by track
                                init: --database option
                                _get_maintenance_url(ctx) → wrapper over resolver

scripts/provision_tick_roles.sql  NEW — tick roles, database, isolation

test/conftest.py                _throwaway_database(prefix) helper (dedupes the two
                                existing fixtures), ephemeral_tick_db, tick vars scrubbed
test/_prod_url_guard.py         needle list: + MT_TICK_DB_URL, MT_TICK_MAINTENANCE_URL
scripts/run_tests.py            build_env pops the tick vars
```

`databases.py` sits below the CLI because the API server and the pass modules
will need `resolve_database_url(settings, Database.TICK, Credential.APPLICATION)`
when they gain their second pool (223 onward). Putting it in `data.py`, which is
already 3,929 lines, would force them to import from the CLI.

### Data Flow

`mt data migrate apply --track tick`:

```
--track tick
  → TRACK_REGISTRY["tick"].database                      = Database.TICK
  → resolve_database_url(settings, TICK, MAINTENANCE)
       MT_TICK_MAINTENANCE_URL unset → DatabaseNotConfiguredError
         → CLI prints "MT_TICK_MAINTENANCE_URL not configured …", exit 1
           (never MT_TICK_DB_URL, never MT_TIMESCALE_*)
  → TimescaleMinuteDataDB(conninfo=<tick maintenance URL>)
  → assert_ledger_belongs(pool, TICK)
       ledger holds ids of tracks routed to PRIMARY → LedgerMisrouteError, exit 1
  → apply_migrations(pool, TRACKS["tick"])               ledger lives in the tick DB
```

`migrate status --track T` follows the same path with `Credential.APPLICATION`
and does not run the guard, since it is read-only. `mt data init --database D`
applies `DEFAULT_TRACK_FOR[D]`: `minute` for `PRIMARY`, `tick` for `TICK`.

**Factory contract.** `_create_timescale_db(ctx, conninfo=None)` falls back to
`settings.timescale_db_url` when `conninfo` is omitted. Today `migrate status`
and `init --validate-only` both rely on that fallback, and both check
`settings.timescale_db_url` directly. Every track-routed command (`migrate
apply`, `migrate status`, and `init` with or without `--validate-only`) now
resolves its URL through `resolve_database_url` and always passes it as
`conninfo`. Status's direct `timescale_db_url` check is replaced by the
resolver. The no-`conninfo` default remains only for the non-migration primary
read paths (instruments, bars, caggs, and similar), which never route by track.

The primary path, `--track minute` or no option, resolves
`MT_TIMESCALE_MAINTENANCE_URL` / `MT_TIMESCALE_DB_URL`. Those are the same
variables as today, with the same error text and the same output. The only
addition is one guard query, and on a correctly configured primary it returns
no rows.

### State Management

- **One ledger per database.** Each database's `schema_migrations` records only
  its own tracks. The primary's ledger stays shared by `minute`, `daily`, and
  `kalshi`, unchanged. The tick database's ledger holds the bootstrap plus
  `tick_*` ids. The runner already creates the ledger on first apply, so no
  runner change is needed.
- **No new persistent state beyond the tick ledger.** Routing is code, and
  credentials are environment.

## Technical Decisions

### D1 — Routing lives on the track, defined once

`TRACK_REGISTRY` pairs each track name with a frozen `TrackSpec(database,
migrations)`. `TRACKS` becomes `{name: spec.migrations for ...}`: the same type
and the same keys plus `tick`. The nine existing `TRACKS[...]` consumers
(kalshi preflight, universe rebuild, the DB wrapper, the CLI) are untouched,
and a track's database is stated in exactly one place.

*Rejected:* a parallel `TRACK_DATABASE` dict kept in sync by a test (two
definitions of one fact), and changing `TRACKS` values to `TrackSpec` (it
touches every consumer for no gain).

### D2 — One resolver, no fallback in any direction

`resolve_database_url(settings, database, credential)` reads the field named in
`DATABASE_URL_FIELDS`. Unset or empty raises `DatabaseNotConfiguredError(env_var)`.
There is no fallback in any direction:

- **Tick maintenance never falls back to tick application.** This is 913 D4's
  reasoning repeated for the second database.
- **Tick never falls back to primary.** That fallback is precisely the
  placement 220 rules out ("hard gate, no fallback").

The environment variable name is derived from the field name using `Settings`'
`env_prefix` (`MT_`). The mapping table is the only place a database's
variables are named.

`_get_maintenance_url(ctx)` keeps its signature and its exact message for the
primary. The existing tests in `test_maintenance_url_resolver.py`,
`test_ddl_command_url_routing.py`, `test_data_init.py`, and `test_cli_data.py`
pass unmodified, and that is how "the primary path is unchanged" is proven.
Its six call sites (init, migrate apply, restore run, rechunk, caggs repair,
caggs refresh) are primary-only and stay as they are.

### D3 — Separate roles for the tick database

The tick database gets `tick_app` (DML, `TEMPORARY`, `SELECT` only on its
ledger) and `tick_migrate` (DDL). It does not reuse `trading_app` and
`trading_migrate`:

- **Placement independence.** On a second cluster or host the primary roles do
  not exist, so reuse would work only for one of the three placements.
- **Isolation.** On a shared cluster, roles are cluster-wide. Reuse would make
  one leaked credential reach both databases. The isolation goal is exactly
  that a failure in the tick tier cannot reach production.

### D4 — The tick artifact creates the database, owned by `tick_migrate`

On the primary, `trading_migrate` gets DDL rights through `GRANT postgres TO
trading_migrate`, because `postgres` owns everything (913 D1). Copying that for
the tick database would give `tick_migrate` membership in `postgres`, and with
it reach into `trading` on a shared cluster.

Instead, `provision_tick_roles.sql`:

1. Creates the roles, with the same `\gexec` idempotency guards as
   `provision_roles.sql`.
2. Creates the database `OWNER tick_migrate`, guarded on `pg_database`, outside
   a transaction.
3. Runs `\connect :tick_db`, then in one transaction:
   - `REVOKE CONNECT … FROM PUBLIC`, and `GRANT CONNECT` to `tick_app`.
   - `TEMPORARY` for `tick_app`, needed for 224's COPY staging.
   - Ledger `SELECT`-only for `tick_app`.
   - Default privileges for tables created by `tick_migrate`.

`tick_migrate` owns what it creates, and as database owner it can install the
TimescaleDB extension, which 222's first migration needs. 913 D9 measured this
for the test-admin role as database owner. This slice re-asserts it for
`tick_migrate` in the privilege suite.

**Parameters.** `tick_db` is **required**, with no default name, because the
production name is part of the undecided placement. `app_role` and
`migrate_role` default to `tick_app` and `tick_migrate`, following 913's
precedent, so the tested file is the applied file.

The artifact **enumerates no write tables yet**; 222 adds them. Ownership model
and write surface are the two things that differ from the primary, so this is
a second artifact rather than a `\if` branch inside the first. The shared
portions (the role-creation guard, the ledger revoke, default privileges) are
four short statements. Branching the audited primary file to share them would
put its behaviour at risk for no reduction in logic.

### D5 — Misroute guard: a database's ledger may not hold another database's migrations

Before applying any track, `assert_ledger_belongs(pool, database)` selects the
ledger ids that belong to tracks routed to a *different* database. The shared
`001_schema_migrations` bootstrap id is excluded. Any hit raises
`LedgerMisrouteError`, which names the target track, up to five foreign ids,
and the variable to check. The guard runs before any DDL.

- **It checks the ledger, not the URLs.** Comparing URL endpoints misses host
  aliases (`localhost` vs `127.0.0.1`, a hostname vs its IP). The ledger is what
  the database *is*.
- **Unknown ids are ignored.** Production's ledger can hold ids no longer in
  any track, and those must not trip the guard.
- **A missing ledger passes.** This is a bare database, so there is nothing to
  misroute into yet.
- **Worst remaining case.** Tick migrations applied to a bare primary *before*
  its own chain: the next primary apply then refuses, because the ledger now
  holds `tick_*` ids. In `mt data init` order (primary first) this case cannot
  arise.

### D6 — The `tick` track is registered here, with only the bootstrap

The runner requires `001_schema_migrations` as the first entry of any track
applied to a bare database. `TICK_MIGRATIONS = [<bootstrap>]` gives this slice
a real second-database track to route, apply, and guard, and gives 222 a track
to append to. Ids after the bootstrap are `tick_NNN_*`, following the Kalshi
precedent. A registry-level unit test asserts that non-bootstrap ids are unique
across all tracks sharing a database, which the guard relies on.

### D7 — `mt data init --database`, default `primary`

`init` today applies `DEFAULT_TRACK` (`minute`) to the primary. It gains
`--database {primary,tick}`, with choices taken from the enum, default
`primary`, applying `DEFAULT_TRACK_FOR[database]`. The no-argument invocation
and its table and JSON output are unchanged. `--validate-only` works per
database with the application credential, as today.

*Rejected:* initialising every configured database by default and skipping an
unconfigured tick with a notice. That changes the default output, and a
forgotten variable would read as "done".

### D8 — Test fixtures (917)

- **Shared helper.** `_throwaway_database(prefix)` is a context manager that
  replaces the create, terminate, and drop block currently duplicated in
  `ephemeral_db` and `session_ephemeral_db`.
- **`ephemeral_tick_db`.** Uses the helper with prefix `mt_test_t`, on the same
  test cluster, owned by the test-admin role. `migrated_tick_db` applies
  `TRACKS["tick"]` to it.
- **`provisioned_tick_db`** (privilege suite).
  - Runs `provision_tick_roles.sql` as the test admin with per-run names
    (`tick_db=mt_test_tp<hex>`, `t923_app_<hex>`, `t923_mig_<hex>`), so the
    artifact creates the database.
  - Teardown drops the database, then `DROP OWNED` / `REVOKE` / `DROP ROLE`, in
    913's order. The database is one this process created, so the `sql.md`
    rule is satisfied.
  - Under PostgreSQL 16, `CREATE DATABASE … OWNER r` requires `SET` on `r`,
    which a CREATEROLE creator lacks. The artifact therefore grants
    `r TO current_user WITH SET TRUE` only when the applier is not a
    superuser. That is the same fix the 913 fixture applies, moved into the
    artifact because the artifact now creates the database. It is not emitted
    in production, which is applied as superuser.
- **Two-database settings.** A helper builds a `Settings(_env_file=None)`
  pointing primary at `migrated_db` and tick at `ephemeral_tick_db`. This keeps
  a developer's real `.env` out of routing tests, which is the trap
  `test_ddl_command_url_routing.py` documents.

### D9 — Tick URLs are production URLs to the test suite

Once a tick database exists, `MT_TICK_DB_URL` and `MT_TICK_MAINTENANCE_URL` in
`.env` name a production database. The runtime scrub in `pytest_configure`, the
static ratchet regex in `_prod_url_guard.py`, and `run_tests.build_env` each
move from a single needle to a tuple that includes both tick variables. The
ratchet's allowlist for the tick needles is empty.

The one current reader is `test_tick_schema_integration.py`. It is deleted
here, not left to 222, because it would apply DDL to whatever `MT_TICK_DB_URL`
names, and after this slice that can be a real database. Slice 222 keeps the
other slice 105 cleanup (`TickEventType` and its unit test).

### Error Handling

- Errors are typed in `databases.py` and mapped once, at the CLI, to
  `print_error` plus exit 1. New code adds no `except Exception`.
- `migrate status`'s JSON error shape (`{"connected": false, "error": …}`) is
  kept for both databases, with the tick variable named in `error`. Its
  existing process-boundary handler already covers connection failures.
- `LedgerMisrouteError` and `DatabaseNotConfiguredError` never fall through to
  a psycopg error mid-migration. Both are raised before the first statement.
- **Connection failures in `apply` and `init`.** The pool opens in the
  background, so an unreachable or refusing peer shows up at the first
  `getconn` as `psycopg_pool.PoolTimeout`, after the pool's existing 30 s wait.
  No new timeout setting is added. That first `getconn` is the guard's `SELECT`,
  and the guard runs before any DDL. A peer that drops during the guard raises
  `psycopg.OperationalError`, also before any DDL. The CLI catches these two
  types around the whole routed command (a process-boundary handler, which the
  project's exception rule permits), prints
  `could not connect to the <database> database (<env var>): <error>`, and
  exits 1. No DDL runs in either case.
- **A disconnect mid-apply** needs no new handling. Each migration runs in its
  own transaction (the existing runner contract), so the migration in flight
  rolls back, earlier ones stay committed and recorded, and a re-run resumes.
  The CLI maps the error the same way and exits 1.
- This mapping is one code path for both databases. On the primary, the only
  observable change is that a connection failure prints one line instead of a
  traceback. The unchanged-primary criteria below cover the success paths and
  the configuration-error paths, and those are unaffected.

## Implementation Details

### Migration Plan

This is a restructuring slice. Consumers and how each stays working:

| Consumer | Change | How behaviour is preserved |
|---|---|---|
| `TRACKS` readers (kalshi `db.py`, `universe/rebuild.py`, `TimescaleMinuteDataDB`, `data.py`) | none | `TRACKS` is derived, with the same type and keys, plus `tick` |
| `--track` option | choices gain `tick`; the stale comment is replaced | default is still `minute` |
| `migrate apply` / `status` | URL from the track's database | primary resolves the same variables with the same messages |
| `data init` | `--database`, default `primary` | the no-argument path is unchanged |
| `_get_maintenance_url(ctx)` and its 6 callers | body delegates to the resolver | signature and message are unchanged |
| `ephemeral_db`, `session_ephemeral_db` | share `_throwaway_database` | same names, prefixes, and teardown |
| `migrated_db`, `session_migrated_db` | none | — |
| `provision_roles.sql`, 913 privilege suite | none | the primary artifact is not edited |
| `cutover_common.verify_migrate_target` | none | primary-only by design; a tick cutover is 222's |

No data moves. The primary ledger is not written by this slice, and on
production the guard is a single `SELECT`.

### Database / Storage Schema

**Tick ledger.** This is `schema_migrations` with the same DDL as the other
tracks' bootstrap, created in the tick database on first apply.

**Roles and privileges** after `provision_tick_roles.sql`:

| Object | `tick_app` | `tick_migrate` | primary roles |
|---|---|---|---|
| tick database | CONNECT, TEMPORARY | owner | no CONNECT (PUBLIC revoked) |
| `schema_migrations` | SELECT | owner (created by migrate) | — |
| future tables created by `tick_migrate` | SELECT, INSERT, UPDATE, DELETE (default privileges) | owner | — |
| `trading` database | nothing beyond PUBLIC defaults | nothing | unchanged |

## Integration Points

### Provides to Other Slices

- **222 Tick Storage Track.**
  - A routed `tick` track to append `tick_001_…` migrations to, starting with
    the TimescaleDB extension.
  - A grant artifact whose write-surface list 222 fills.
  - `migrated_tick_db` and `provisioned_tick_db` fixtures.
  - The guarantee that `migrate apply --track tick` cannot write to `trading`.
- **223 onward.**
  - `resolve_database_url(settings, Database.TICK, Credential.APPLICATION)`
    for every process that gains a tick pool.
  - The variable names the service environment file will carry once a process
    has a tick duty.
- **A future second database** (Kalshi, archive): one `Database` member, one
  settings pair, and one artifact.

### Consumes from Other Slices

- **913:** the role split, the no-fallback rule, and the artifact
  conventions. If 913's artifact changes, this one does not inherit the change;
  the two are reviewed side by side (Special Considerations).
- **917:** the test cluster. A missing `MT_TIMESCALE_TEST_URL` fails the tier
  rather than skipping it, as today.

### Slice plan updates made with this design

- **220 slice plan, entry 222.**
  - "Creates the `tick` migration track" becomes "appends to the `tick` track
    923 registers".
  - "adds tick roles per 913" becomes "adds its tables to 923's tick grant
    artifact".
  - The `test_tick_schema_integration.py` removal moves to this slice.

## Success Criteria

### Functional Requirements

1. `mt data migrate apply --track tick` applies `TRACKS["tick"]` to the
   database at `MT_TICK_MAINTENANCE_URL`, and the ledger rows appear there and
   nowhere else.
2. `mt data migrate status --track tick` reads the tick ledger through
   `MT_TICK_DB_URL`. With `MT_TICK_DB_URL` unset and `MT_TIMESCALE_DB_URL`
   set, it exits 1 naming `MT_TICK_DB_URL` and opens no connection. The same
   holds for `mt data init --database tick --validate-only`. Together these
   prove that no track-routed path reaches the factory's primary default.
3. With `MT_TICK_MAINTENANCE_URL` unset, `apply --track tick` exits 1 naming
   that variable. This holds even when `MT_TICK_DB_URL`,
   `MT_TIMESCALE_DB_URL`, and `MT_TIMESCALE_MAINTENANCE_URL` are all set, and
   no connection is opened.
4. With `MT_TICK_MAINTENANCE_URL` pointing at a database whose ledger holds
   minute ids, `apply --track tick` exits 1 naming the foreign ids, and no
   statement runs. The mirror case (a minute apply against a ledger holding
   `tick_*` ids) also exits 1.
5. `mt data init --database tick` brings a bare tick database to the head of
   the tick track. `mt data init`, with no option, is unchanged.
6. With `MT_TICK_MAINTENANCE_URL` pointing at an unreachable port on the test
   cluster, `apply --track tick` exits 1 with the one-line connection error
   naming that variable, prints no traceback, and runs no statement.
7. Primary unchanged:
   - The existing unit tests for the maintenance resolver, DDL URL routing,
     `data init`, and `cli data` pass with no edits.
   - The 913 privilege suite passes with no edits.
   - `mt data migrate status --json` against production is identical before
     and after (Walkthrough, step 1).
8. `provision_tick_roles.sql` applied to the test cluster produces:
   - `tick_app` cannot `TRUNCATE`, `DROP`, or write the ledger.
   - `tick_app` can create a temp table.
   - `tick_migrate` owns the database and can `CREATE EXTENSION timescaledb`.
   - The primary throwaway application role has no `CONNECT` on the tick
     database.
   - The tick roles have no privileges in the primary throwaway database.
   - Re-running the artifact is a no-op.

### Technical Requirements

- `databases.py` stays under 300 lines, and the routing and guard functions
  under 50 lines each.
- Unit tests:
  - The resolver: every (database, credential) pair, the empty string, and no
    fallback.
  - The registry: every track has a database, every database has a default
    track, and non-bootstrap ids are unique per database.
  - The guard: foreign ids, unknown ids, a missing ledger, and the bootstrap
    exemption.
  - The CLI: routing, error messages, and the connection-failure mapping
    (`PoolTimeout` and `OperationalError` become exit 1 before any DDL), run
    under `Settings(_env_file=None)`.
- Integration tests:
  - The two-database routing suite, including misroute.
  - The tick privilege suite.
  - The cross-database isolation assertions, read from the catalog
    (`has_database_privilege`, `information_schema.table_privileges`), per 913
    D8.
- Guard tests: the unit and integration prod-URL guard tests cover the tick
  needles with an empty allowlist.
- ruff, and mypy run per the kalshi_support path note, are clean on the
  touched files.

### Integration Requirements

- Slice 222 can add `tick_001_timescaledb_extension` and its tables by editing
  `migrations/tick.py` and the artifact's write list only. No CLI, runner, or
  fixture code changes.
- The full unit and integration tiers stay green, apart from the known
  pre-existing failures recorded in memory.

### Verification Walkthrough

Verified 20260927 at Phase 6 completion (PostgreSQL 17.11, TimescaleDB
2.29.1); outputs below are the actual ones. Everything destructive runs on the test cluster (hammerhead) against databases this
walkthrough creates. Production is only *read*, in step 1, through the
application credential.

**0. Environment.** Export the test-cluster admin URL from `.env`, stripping
the quotes:

```bash
export MT_TIMESCALE_TEST_URL=$(grep '^MT_TIMESCALE_TEST_URL=' .env | cut -d= -f2- | tr -d "\"'")
```

Every `mt` command below also reads `.env`, whose primary pair names
production. Only the tick variables are overridden per command, and the tick
track never resolves a primary variable, so nothing below touches production.

**1. Primary unchanged.** Capture migration status on the target (`main`
while `git.integration_branch` is unset) before the branch's first commit, and
again on the branch at the end:

```bash
# on the target, before implementation starts
uv run mt data migrate status --json > /tmp/923-before.json
# on the slice branch, after implementation
uv run mt data migrate status --json > /tmp/923-after.json
diff /tmp/923-before.json /tmp/923-after.json && echo "primary status identical"
#   primary status identical
```

**2. Unit tier.** The new files plus the untouched 913 files:

```bash
uv run pytest test/unit/market/schema/test_databases.py \
  test/unit/market/schema/test_track_registry.py \
  test/unit/cli/test_migrate_track_routing.py \
  test/unit/cli/test_maintenance_url_resolver.py \
  test/unit/cli/test_ddl_command_url_routing.py \
  test/unit/cli/commands/test_data_init.py test/unit/test_cli_data.py \
  test/unit/test_unit_prod_url_guard.py test/unit/test_conftest_scrub.py \
  test/unit/test_run_tests_env.py -q
```

Expected: all pass. `git diff main --stat -- test/unit/cli/test_maintenance_url_resolver.py test/unit/cli/test_ddl_command_url_routing.py test/unit/cli/commands/test_data_init.py test/unit/test_cli_data.py`
prints nothing.

**3. Integration tier on the test cluster.**

```bash
uv run pytest test/integration/data/test_two_database_migrate.py \
  test/integration/data/test_tick_role_privileges.py \
  test/integration/data/test_role_privileges.py \
  test/integration/test_integration_prod_url_guard.py -q
```

Expected: all pass, and no `mt_test_*` databases or `t923_*` roles remain:

```bash
psql "$MT_TIMESCALE_TEST_URL" -Atc "select count(*) from pg_database where datname like 'mt_test_%'"
psql "$MT_TIMESCALE_TEST_URL" -Atc "select count(*) from pg_roles where rolname like 't923_%'"
```

Both print `0` if no other test run is active. The test cluster is shared, so
in practice compare the `mt_test_%` list before and after the run rather than
expecting `0`: on 20260927 fourteen databases from other sessions were
present, several with live connections, and none were this slice's. The
integration tests need `MT_TIMESCALE_TEST_URL` exported (step 0).

**4. The CLI against a throwaway tick database**, by hand:

```bash
T=mt_test_walk923_$(date +%s)
psql "$MT_TIMESCALE_TEST_URL" -c "CREATE DATABASE $T"
TICK_URL=$(python -c "import sys,urllib.parse as u;p=u.urlparse(sys.argv[1]);print(u.urlunparse(p._replace(path='/'+sys.argv[2])))" "$MT_TIMESCALE_TEST_URL" "$T")

# unset → names the variable, exit 1
env -u MT_TICK_MAINTENANCE_URL uv run mt data migrate apply --track tick; echo "exit $?"
#   Error: MT_TICK_MAINTENANCE_URL not configured. This command performs schema or
#   maintenance work and requires the migration credential; it will not fall back to
#   MT_TICK_DB_URL. Set the environment variable or add it to your .env file.
#   exit 1

MT_TICK_MAINTENANCE_URL=$TICK_URL uv run mt data init --database tick
#   Applied this run 0 · Total applied 1 · Pending remaining 0
MT_TICK_DB_URL=$TICK_URL uv run mt data migrate status --track tick
#   001_schema_migrations  applied … "1 applied, 0 pending"
```

"Applied this run" is `0`, not `1`: the runner records the bootstrap on a bare
database before its apply loop and does not count it as newly applied. That is
the runner's existing behaviour, unchanged here.

**5. Misroute refused.** Point the tick maintenance variable at a throwaway
database that carries minute ids. The quickest source is the integration
suite's `migrated_db`; by hand, apply the minute chain with the runner using an
explicit URL (no `Settings`, so `.env` cannot redirect it):

```bash
P=mt_test_walk923p_$(date +%s); psql "$MT_TIMESCALE_TEST_URL" -c "CREATE DATABASE $P"
PRIM_URL=${TICK_URL%/*}/$P
uv run python -c "import sys; from psycopg_pool import ConnectionPool; \
from manta_trading.market.schema.migrations import TRACKS; \
from manta_trading.market.schema.runner import apply_migrations; \
p=ConnectionPool(sys.argv[1]); apply_migrations(p, TRACKS['minute']); p.close()" "$PRIM_URL"
MT_TICK_MAINTENANCE_URL=$PRIM_URL uv run mt data migrate apply --track tick; echo "exit $?"
#   Error: refusing: this database's ledger holds migrations of track(s) routed to
#   primary (001a_create_timescaledb_extension, 001b_create_minute_ohlcv,
#   001c_create_minute_ohlcv_hypertable, 001d_create_minute_ohlcv_indexes,
#   002_instruments, … 53 more); check MT_TICK_MAINTENANCE_URL (track 'tick' targets
#   the tick database)
#   exit 1
psql "$PRIM_URL" -Atc "select count(*) from schema_migrations where migration_id like 'tick_%'"   # 0
```

**6. Teardown.** These are databases this walkthrough created:

```bash
psql "$MT_TIMESCALE_TEST_URL" -c "DROP DATABASE $T" -c "DROP DATABASE $P"
```

### Implementation Findings (20260927)

- **Ledger ordering (resolved in the runbook, not the design).**
  `tick_migrate`'s default privileges grant `tick_app` DML on every table it
  creates, and `init` creates the ledger *after* provisioning. Measured: after
  provision → init, `tick_app` held INSERT/UPDATE/DELETE on
  `schema_migrations`. Re-running the idempotent artifact applies the guarded
  ledger revoke and closes it. The runbook sequence is therefore provision →
  `init --database tick` → provision again, and `provisioned_tick_db` builds
  in that order. Slice 222 should know that any table it creates also gets
  full DML through default privileges, so its enumerated write list is
  documentation plus a no-op rather than the grant that makes writes possible.
- **Extension as database owner: works.** `tick_migrate` with `LOGIN` only
  (no CREATEDB, CREATEROLE, SUPERUSER, or `postgres` membership) runs
  `CREATE EXTENSION timescaledb` as owner of the tick database. The Risk
  mitigation below was not needed; 222's first migration can create the
  extension itself.
- **Role attributes under the non-superuser test admin.** The artifact's
  `GRANT migrate_role TO current_user WITH SET TRUE` is sufficient for
  `CREATE DATABASE … OWNER`, `REVOKE CONNECT FROM PUBLIC` and the default
  privileges. Teardown additionally needs membership in the *app* role before
  `DROP OWNED BY`, so `drop_tick_db` grants it first; a fixture that fails
  before its own grant still cleans up.
- **Resolver message.** `DatabaseNotConfiguredError` words a maintenance
  credential's error with the "will not fall back to <application var>"
  clause, which is byte for byte 913's primary message. `_get_maintenance_url`
  now prints the resolver's text, so the message has one definition.
- **`migrate status` JSON.** The primary keeps `"URL not configured"`; the
  tick track names its variable. That is the one branch on database identity
  in the CLI, kept for the byte-for-byte primary criterion.
- **Guard call site.** The DB wrapper exposes the guard as
  `check_ledger_belongs`, not `assert_*`: the 913 unit tests stub the wrapper
  with `MagicMock`, which rejects attributes named `assert*`.
- **Bootstrap id constant.** `runner.BOOTSTRAP_MIGRATION_ID` replaces the
  literal in the runner; the tick track reuses the minute bootstrap dict.
- **Test support.** `ProvisionedTickDb` and its helpers live in
  `test/tick_support/database.py`, not the integration `conftest.py`, because
  the unit tier already imports `test/conftest.py` as `conftest`.
- **Unit tier needs the test URL.** `run_tests.py unit` passes no DB variable;
  its 40 DB-backed tests error unless `MT_TIMESCALE_TEST_URL` is exported.
  Pre-existing.
- **Full tiers.** Unit 3915 passed. Integration 513 passed, 6 failed, all
  known and pre-existing: `test_cli_lists` priority1 (2),
  `test_migration_051_052` (2), `test_policy_advances_head` unaided (2).

## Risk Assessment

### Technical Risks

- **Role-attribute behaviour on the test cluster under PostgreSQL 16/17.**
  `CREATE DATABASE … OWNER`, `REVOKE CONNECT FROM PUBLIC`, and teardown by a
  non-superuser test admin depend on `SET` and `INHERIT` membership in the
  throwaway roles. 913 hit two such surprises (`MEMBER` vs `USAGE`, and the
  CREATEROLE `SET` change).
- **The extension as database owner.** 913 D9 measured it for the test admin,
  not for a database owned by a role other than the connecting one.

### Mitigation Strategies

- Build the privilege fixture first (Development Approach, step 3). Record each
  measured attribute requirement as a comment in the artifact, in 913's style,
  before the rest of the slice depends on it.
- If owner-installed TimescaleDB fails for `tick_migrate`: the artifact
  installs the extension at provisioning (it runs as superuser in production),
  and 222's first migration becomes `CREATE EXTENSION IF NOT EXISTS`, a no-op.
  Record the measurement in this design's findings.

## Implementation Notes

### Development Approach

1. `databases.py`, the settings field, and the registry refactor, with unit
   tests. Confirm the 913 CLI unit tests still pass with no edits.
2. CLI routing (`migrate`, `init --database`) and the misroute guard, with unit
   tests.
3. `provision_tick_roles.sql` and the tick privilege suite on the test cluster.
   This is where the measurements happen.
4. The fixtures refactor (`_throwaway_database`), `ephemeral_tick_db`, and the
   two-database integration suite.
5. The production-URL guard extension; delete `test_tick_schema_integration.py`.
6. Documentation: the migrations README (a track → database table replacing
   the stale MarketDB row and `--db` flag), `.env_sample` (both tick
   variables, commented, with the no-fallback note), and the runbook section
   on provisioning a tick database. The runbook section is one command for the
   operator, run when placement is decided.

Checkpoint commits after each step.

### Special Considerations

- **Security.** `MT_TICK_MAINTENANCE_URL` stays out of
  `deploy/manta-trading.env.example`, the same rule as the primary maintenance
  URL. No service runs migrations. `MT_TICK_DB_URL` is not added to the service
  environment until a service has a tick duty (223 onward).
- **Review side by side.** `provision_tick_roles.sql` intentionally shares
  idioms with `provision_roles.sql`. A future change to either artifact's
  role-creation or default-privilege blocks should be checked against the
  other.
- **Two pre-existing gaps are out of scope.** Both are recorded for the PM:
  - `mt data init` applies only the `minute` track, so a cold-started primary
    lacks the `kalshi` schema until `migrate apply --track kalshi` runs.
  - The test suite's runtime scrub does not remove
    `MT_TIMESCALE_MAINTENANCE_URL`. No test reads it from the environment
    today, and the static ratchet does not cover it.
