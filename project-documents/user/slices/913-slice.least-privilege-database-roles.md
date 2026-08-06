---
docType: slice-design
slice: least-privilege-database-roles
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: []
interfaces: [912]
dateCreated: 20260806
dateUpdated: 20260806
status: not-started
---

# Slice Design: Least-Privilege Database Roles — Make Credential Leaks Non-Destructive

## Overview

On 2026-08-04 a test fixture received the production URL and ran
`TRUNCATE ... CASCADE`, destroying six production metadata tables
([incident notes](../notes/2026-08-04-prod-metadata-truncation-incident.md)).
Every control added since is *procedural*: the static ratchet guards
([_prod_url_guard.py](../../../test/_prod_url_guard.py)), the runtime scrub in
[test/conftest.py](../../../test/conftest.py), and the additive-allowlist runner
in [scripts/run_tests.py](../../../scripts/run_tests.py). Each stops a *class* of
caller from obtaining the URL. None of them limits what the URL can do once
obtained, so each depends on code or a caller behaving correctly.

Production connects as `postgres`, a superuser. That is the whole problem: the
credential that the daemon uses to `INSERT` bars is the same credential that can
`DROP` the database. This slice makes the blast radius structural rather than
procedural. It is the one `sql.md` "Production Database Protection" bullet
(["Split connection roles"](../../ai-project-guide/project-guides/rules/sql.md))
not yet enforced by the server.

### Verified premise

The design premise was tested against production (`trading` on .144,
PostgreSQL 17.7 / TimescaleDB 2.23.0) rather than assumed. A `trading_app` login
role **already exists and holds zero grants** — it is an unusable shell, so this
slice provisions grants onto an existing role rather than creating one. Under
`SET ROLE trading_app`, the three statements that constitute the incident all
fail:

| Statement | Result as `trading_app` |
|---|---|
| `TRUNCATE instruments` | `permission denied for table instruments` |
| `DELETE FROM schema_migrations` | `permission denied for table schema_migrations` |
| `DROP TABLE daemon_heartbeat` | `must be owner of table daemon_heartbeat` |

The same leak, under the application role, destroys nothing.

### A risk that turned out not to exist

The plan entry and the initial code survey both flagged that
`timescaledb_information.*` views row-filter by ownership, which would silently
empty the `cagg_freshness` read path under a non-owner role. **Measured on prod,
this is false for TimescaleDB 2.23.** With zero grants, `trading_app` reads:

```
timescaledb_information.continuous_aggregates -> 9
timescaledb_information.jobs                  -> 17
timescaledb_information.job_stats             -> 17
timescaledb_information.chunks                -> 6077
timescaledb_information.hypertables           -> 2
_timescaledb_catalog.continuous_agg           -> 9
```

Catalog *metadata* is visible without ownership; only *data* access is gated
(`SELECT count(*) FROM daily_coverage` → `permission denied for view
daily_coverage`, correctable with a plain `SELECT` grant). `cagg_watermark` is
likewise gated by `SELECT` on the cagg, not by ownership. This removes the
slice's largest anticipated risk and shrinks scope: no ownership transfer, and no
special handling for `mt data caggs status` or `cagg_freshness` beyond ordinary
`SELECT` grants.

## Technical Decisions

### D1 — Two roles, ownership unchanged

`postgres` remains the owner of all 15 tables and 9 caggs. Ownership transfer
would be a large, risky change that the measured catalog behavior shows is
unnecessary.

- **`trading_app`** (exists): `SELECT, INSERT, UPDATE, DELETE` on application
  tables; `SELECT` only on `schema_migrations`; `TEMPORARY` on the database; no
  `TRUNCATE`, no DDL, no ownership. `TRUNCATE` is a separately grantable
  privilege in PostgreSQL, so withholding it is sufficient — this is what makes
  the incident statement fail.
- **`trading_migrate`** (new): owner-adjacent role for DDL and Timescale
  management. Simplest correct implementation is `GRANT postgres TO
  trading_migrate` so it inherits ownership rights, avoiding per-object `ALTER
  ... OWNER`. It is used only when doing migration or maintenance work.

Rationale for not creating a third read-only role: the API server issues no
writes, but it shares `TimescaleMinuteDataDB` with the daemon, and splitting it
would add a role without removing a destructive capability. Out of scope.

### D2 — `TEMPORARY` is mandatory for the application role

The COPY bulk-write hot path creates a temp staging table
([timescale_minute_db.py:203](../../../src/manta_trading/market/timescale_minute_db.py)).
Revoking `TEMPORARY` breaks all minute ingestion. This is called out explicitly
because it is the least obvious grant in the set.

### D3 — Write surface is enumerated, not inferred

The application role's DML grants cover exactly the tables production code
writes: `minute_ohlcv`, `daily_ohlcv`, `data_gaps`, `acquisition_state`,
`daemon_heartbeat`, `trading_sessions`, `instruments`,
`provider_symbol_mapping`, `universe_members`, `splits`, `dividends`.
`schema_migrations` is `SELECT`-only. `backfill_state`, `trading_calendars`, and
`trading_holidays` receive DML as well — they are application tables even where
current write paths are CLI-driven.

**Two commands are mislabeled as read-only and must be classified as writers:**

- `mt data status` writes `trading_sessions` through the auto-extend hook
  ([data.py:883](../../../src/manta_trading/cli/commands/data.py)). It runs as
  the application role and needs its DML grant — this is not a defect to fix
  here.
- `mt data caggs status` reads `_timescaledb_catalog` and `cagg_watermark`;
  covered by `SELECT` grants per the measurement above.

### D4 — Settings gains a maintenance key; resolution stays in the caller

Add `timescale_maintenance_url` (`MT_TIMESCALE_MAINTENANCE_URL`) to
[Settings](../../../src/manta_trading/config/__init__.py) alongside
`timescale_db_url`. No library module changes: every module below the CLI already
takes `conninfo`/`pool` as a parameter, and
[restore_metadata.py:249](../../../src/manta_trading/data/quality/restore_metadata.py)
documents the invariant that the caller resolves the URL.

DDL commands take the maintenance URL from an explicit resolution helper that
**fails loudly** when the key is unset — never a silent fallback to
`timescale_db_url`, which would restore exactly the coupling this slice removes.
Commands requiring the maintenance URL: `mt data init` (default path),
`mt data migrate apply`, `mt data restore run`, `mt data rechunk` (real run),
`mt data caggs repair` (real run), `mt data caggs refresh`.

### D5 — `runner.py` autocommit path inherits the same URL

[runner.py:81](../../../src/manta_trading/market/schema/runner.py) re-connects
raw from `pool.conninfo` for `requires_autocommit` migrations, bypassing the
pool's configure hook. This needs no code change — it inherits whichever URL
built the pool — but it must be verified under the maintenance role, since 6 of
the 52 minute migrations take that path.

### D6 — Grants as an idempotent, re-runnable artifact

A single reviewable SQL artifact (`scripts/provision_roles.sql`) that is safe
to run repeatedly and covers **future** tables via `ALTER DEFAULT PRIVILEGES FOR
ROLE postgres IN SCHEMA public`. Without default privileges, the next migration
that creates a table silently produces one the application role cannot read.
Role creation uses a `DO` block guard so a second run does not error on the
existing `trading_app`.

### D7 — `migrate_cold_start.py` gets an explicit deny by construction

[migrate_cold_start.py:280](../../../src/manta_trading/data/quality/migrate_cold_start.py)
issues `TRUNCATE TABLE ... RESTART IDENTITY`, has no CLI entry point, and is
reachable only from tests. It is the highest-danger statement in the tree and
precisely the incident's shape. Withholding `TRUNCATE` from the application role
neutralizes it without deleting code.

### D8 — Privilege tests run against an ephemeral database, never `trading`

Added 20260806, after a first implementation attempt. The negative-case tests
originally targeted production. `DROP TABLE` requires an `ACCESS EXCLUSIVE`
lock, so even inside a rolled-back transaction it queues behind live readers and
then blocks every reader and writer of that table. The run hung in that lock
queue and was killed. **Rolling back protects data; it does not protect
availability** — and the original design conflated the two.

It also violated the `sql.md` rule this slice exists to enforce: a fixture
issuing TRUNCATE/DROP/ALTER/DELETE may only target a database it created.

Two consequences shape the test design:

- **`provision_roles.sql` is parameterized** on database name (`:DBNAME`) and
  role names, so the *same* artifact the production run applies is the one the
  test exercises. A fixture with its own derived grant set could pass while the
  real artifact is wrong.
- **Test roles must be uniquely named.** `pg_authid` is a shared catalog
  (verified), and the ephemeral database lives on the same cluster as prod, so
  role-level statements reach across into the roles prod depends on while table
  grants do not. The fixture provisions throwaway role names and drops them on
  teardown.

The ephemeral suite proves the artifact is correct; it cannot prove prod's live
state matches. That confirmation is a **read-only, non-blocking manual check**
(recorded in the walkthrough), deliberately not part of CI.

## Migration Plan

Cutover is staged so the old credential remains available for rollback until the
new one is proven. The privilege split will surface any read path quietly relying
on superuser, and the whole point of the staging is to find those before the
superuser URL is retired.

1. **Provision** — run `provision_roles.sql` against `trading`. Purely additive;
   changes nothing about how running processes connect.
2. **Verify negatively** — assert the application role cannot TRUNCATE, DROP, or
   write the ledger (D8 test).
3. **Verify positively, offline** — point `MT_TIMESCALE_DB_URL` at `trading_app`
   in a scratch environment and run a full daemon daily + minute cycle and the
   API endpoint surface. This is the step that surfaces missing grants.
4. **Cut over** — switch the daemon, API server, and CLI read paths to the
   application credential; set `MT_TIMESCALE_MAINTENANCE_URL` for DDL work.
5. **Retire** — remove the superuser URL from ambient configuration only after
   step 3 and 4 are green. Keep it recorded for rollback.

Rollback at any stage is a one-line credential revert, since ownership and schema
are untouched.

## Success Criteria

- [ ] `provision_roles.sql` runs cleanly against `trading`, and a second
      consecutive run also succeeds with no error (idempotent).
- [ ] Under the application role on an **ephemeral** database (D8): `TRUNCATE`,
      `DROP`, and `DELETE FROM schema_migrations` each raise `permission denied`
      (or `must be owner`), asserted by test. No test issues these against
      `trading`.
- [ ] Under the application role on the ephemeral database: `SELECT` succeeds on
      all application tables; `INSERT`/`UPDATE`/`DELETE` succeed on the D3 write
      surface; temp table creation succeeds.
- [ ] On `trading`, a **read-only** manual check confirms the same privilege
      surface — including all 9 caggs readable — using only non-blocking
      statements (`UPDATE ... WHERE false` takes no exclusive lock). Recorded as
      evidence in the walkthrough, not run by CI.
- [ ] `mt data migrate apply` succeeds under the maintenance URL and fails with
      a permission error under the application URL.
- [ ] A DDL command invoked with `MT_TIMESCALE_MAINTENANCE_URL` unset fails with
      an explicit error naming the missing key — never a silent fallback.
- [ ] The daemon completes a full daily cycle and a full minute cycle under
      application-role credentials, including the COPY temp-table path.
- [ ] All API endpoints serve correctly under application-role credentials,
      including `/api/v1/status` and the cagg-backed symbols range path.
- [ ] `mt data status` and `mt data caggs status` work under the application
      role (auto-extend write and catalog reads respectively).
- [ ] New tables created by a future migration are readable by the application
      role without a manual grant (default privileges verified).

## Verification Walkthrough

Steps 1, 2, and 2a are **verified as run** (20260806); the rest remain draft
until their sections land. Run from the repo root.

**1. Provision and prove idempotency.** Verified on prod `trading` (.144):

```bash
psql "$MT_TIMESCALE_MAINTENANCE_URL" -v ON_ERROR_STOP=1 -f scripts/provision_roles.sql
psql "$MT_TIMESCALE_MAINTENANCE_URL" -v ON_ERROR_STOP=1 -f scripts/provision_roles.sql
```

Both runs exit 0; the second proves re-runnability. Expected output ends with:

```
Provisioning least-privilege roles on database: trading
  application role: trading_app
  maintenance role: trading_migrate
Done. Passwords, if not already set, must be applied out-of-band.
```

The second run additionally emits `NOTICE: role "trading_migrate" has already
been granted membership in role "postgres"`. A `NOTICE` is informational — the
run still exits 0 under `ON_ERROR_STOP=1`.

The artifact takes no extra arguments for production because `app_role` /
`migrate_role` default to the production names. A test target overrides them:

```bash
psql "$URL" -v app_role=t913_app_x -v migrate_role=t913_mig_x \
     -v ON_ERROR_STOP=1 -f scripts/provision_roles.sql
```

**2. Prove the incident cannot recur — automated, on an ephemeral database.**

```bash
python - <<'EOF'
import sys, subprocess; sys.path.insert(0, "scripts")
from pathlib import Path
from run_tests import build_env, load_dotenv_values
env = build_env("integration", load_dotenv_values(Path(".env")))
sys.exit(subprocess.run([sys.executable, "-m", "pytest",
    "test/integration/data/test_role_privileges.py", "-v", "--no-header"],
    env=env).returncode)
EOF
```

Expected: `30 passed` (~5 min — each test rebuilds a database through 52
migrations). Includes the three incident statements (`TRUNCATE instruments`,
`DROP TABLE daemon_heartbeat`, `DELETE FROM schema_migrations`) all denied.

> **Do not run these statements against `trading`.** `DROP TABLE` requires an
> `ACCESS EXCLUSIVE` lock, so even inside a rolled-back transaction it queues
> behind live readers and then blocks every reader and writer of that table.
> An earlier revision of this walkthrough did exactly that and hung (D8).
>
> Invoking pytest directly rather than through `scripts/run_tests.py` is
> deliberate: that runner always passes the tier directory, so a file path
> becomes an *additional* target and the whole tier runs. The snippet above
> reuses the runner's `build_env`, which is what carries the safety property —
> the prod URL is dropped from the child environment and only
> `MT_TIMESCALE_TEST_URL` is allowlisted in.

**2a. Confirm prod's live privilege surface — manual, read-only.** The suite in
step 2 proves the *artifact* is correct; it never connects to production, so it
cannot prove prod's live state matches. This check closes that gap and uses only
non-blocking statements — `UPDATE ... WHERE false` takes no exclusive lock, and
PostgreSQL checks privileges before column validity, so a role holding UPDATE
fails on `cannot assign to system column "ctid"` while one without it fails on
`permission denied`.

Verified 20260806 as `trading_app` against `trading`:

| Check | Result |
|---|---|
| `TRUNCATE instruments` | `permission denied for table instruments` |
| `DROP TABLE daemon_heartbeat` | `must be owner of table daemon_heartbeat` |
| `DELETE FROM schema_migrations` | `permission denied for table schema_migrations` |
| `SELECT` on all 9 continuous aggregates | 9/9 readable |
| `UPDATE` privilege on the 14 write tables | held on all |
| `CREATE TEMP TABLE` | permitted (D2 — the COPY hot path) |
| Grant rows for `trading_app` | 38,691 |

This is a manual step, deliberately **not** part of CI: CI must never hold
production credentials, which is the rule whose violation caused the incident.

**3. Prove the application role still works.** There is no `--url` CLI option —
every command resolves `settings.timescale_db_url`, so credentials are switched
by overriding the environment variable for the invocation:

```bash
export MT_TIMESCALE_DB_URL="$APP_URL"
mt data status
mt data caggs status
mt data get SPY --start 2026-07-01 --end 2026-08-01
```

Bars return, coverage renders, and cagg status shows all 9 aggregates with
watermarks.

**4. Prove the daemon's hot path.** Run a bounded minute cycle under the
application credential and confirm rows land — this exercises the temp-table
COPY path that a missing `TEMPORARY` grant would break:

```bash
mt data pull SPY --timeframe minute --start <recent> --end <recent>
```

**5. Prove the split.** Migration under each credential:

```bash
mt data migrate apply          # maintenance URL set -> succeeds / no-ops cleanly
MT_TIMESCALE_MAINTENANCE_URL="$APP_URL" mt data migrate apply   # -> permission denied
```

The second form deliberately points the *maintenance* key at the application
credential: it proves the DDL path fails on privilege rather than on which key
it read, which is the property that matters after cutover.

**6. Prove no silent fallback.**

```bash
env -u MT_TIMESCALE_MAINTENANCE_URL mt data migrate apply
```

Fails with an explicit message naming `MT_TIMESCALE_MAINTENANCE_URL`. It must not
quietly proceed on `MT_TIMESCALE_DB_URL`.

**7. Prove the API.** Start `mt serve` against the application credential and
exercise the endpoint surface:

```bash
curl -s localhost:8000/api/v1/health
curl -s localhost:8000/api/v1/symbols/SPY
curl -s "localhost:8000/api/v1/bars/SPY?timeframe=daily&start=2026-07-01&end=2026-08-01"
```

All return 200 with correct payloads.

## Risks

**A missing grant breaks a production read or write path.** This is the real
risk, and it is why migration step 3 runs a full daemon + API pass under the new
credential *before* the superuser URL is retired. Mitigation is the staged
cutover plus a retained rollback credential; the failure mode is a loud
`permission denied`, not silent corruption.

**Default privileges are scoped to the creating role.** `ALTER DEFAULT
PRIVILEGES FOR ROLE postgres` only affects objects `postgres` creates. If
migrations run as `trading_migrate`, default privileges must be declared for that
role too — otherwise new tables are invisible to the application role. Both roles
are covered in the artifact.

## Effort

3 / 5
