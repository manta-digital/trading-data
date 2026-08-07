---
docType: tasks
slice: least-privilege-database-roles
project: trading-data
lldReference: project-documents/user/slices/913-slice.least-privilege-database-roles.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: []
interfaces: [912]
projectState: >
  Slice 913 design committed 20260806 (381ea74). Premise verified against prod
  `trading` on .144 (PostgreSQL 17.7 / TimescaleDB 2.23.0): a `trading_app`
  login role already exists holding ZERO grants, and TRUNCATE / DROP /
  DELETE-from-ledger all already fail under it. Measurement also overturned the
  plan's largest anticipated risk — `timescaledb_information.*` does NOT
  row-filter by ownership on 2.23 — so no ownership transfer is in scope.
  Production currently connects as superuser `postgres`; the PM has confirmed
  that retiring that credential does not have to happen in this slice. Sections
  1-4 below deliver the protection without changing how any live process
  connects; Section 5 (cutover) is deliberately separated and requires explicit
  PM approval before starting.
dateCreated: 20260806
dateUpdated: 20260807
status: in_progress
---

# Tasks: Least-Privilege Database Roles — Make Credential Leaks Non-Destructive

## Context summary

The 2026-08-04 incident destroyed six production tables because one credential
carries every privilege. Every control added since is procedural — it stops a
*class of caller* from obtaining the URL, but does not limit what the URL can do
once obtained. This slice makes the blast radius structural: with DML-only
application credentials, the same leak dies on `permission denied`.

All decisions referenced below (D1–D7) are in the LLD.

### Non-negotiables from the design

- **`postgres` stays the owner** of all 15 tables and 9 caggs (D1). No `ALTER
  ... OWNER` anywhere in this slice. The measured catalog behavior makes
  ownership transfer unnecessary.
- **`TEMPORARY` must be granted to the application role** (D2). The COPY
  bulk-write hot path creates a temp staging table
  ([timescale_minute_db.py:203](../../../src/manta_trading/market/timescale_minute_db.py));
  withholding this grant breaks all minute ingestion.
- **`TRUNCATE` must never be granted to the application role** (D1). It is a
  separately grantable PostgreSQL privilege, and withholding it is the specific
  thing that makes the incident statement fail.
- **No silent fallback** from the maintenance URL to `timescale_db_url` (D4). An
  unset maintenance key fails loudly. A fallback restores exactly the coupling
  this slice removes.
- **There is no `--url` CLI option.** Every command resolves
  `settings.timescale_db_url`. Credentials are switched by overriding the
  environment variable for an invocation, not by a flag.

### Sequencing

Sections 1–4 are additive and safe: they provision roles, prove the protection,
and add the maintenance key, while every live process keeps connecting exactly
as it does today. Section 5 switches live credentials and is gated on explicit PM
approval — do not begin it as a continuation of Section 4.

---

## Section 1 — Provisioning artifact

- [x] **1.1 Create the role-provisioning SQL artifact**
  - [x] Create `scripts/provision_roles.sql`
  - [x] Guard role creation in a `DO` block testing `pg_roles` so a second run
        does not error on the already-existing `trading_app`
  - [x] Create `trading_migrate` if absent; `GRANT postgres TO trading_migrate`
        so it inherits ownership rights without per-object `ALTER ... OWNER`
  - [x] Do NOT set passwords in the artifact. Add a header comment stating that
        passwords are set out-of-band and never committed
  - [x] Success: file exists, is pure SQL, contains no credentials, and contains
        no `ALTER ... OWNER` statement
  - [x] Effort: 2

- [x] **1.2 Grant the application role its read surface**
  - [x] `GRANT USAGE ON SCHEMA public TO trading_app`
  - [x] `GRANT SELECT ON ALL TABLES IN SCHEMA public TO trading_app`
  - [x] Grant `SELECT` on all 9 continuous aggregates by name: `daily_coverage`,
        `minute_coverage`, `daily_weekly_ohlcv`, `daily_monthly_ohlcv`,
        `daily_quarterly_ohlcv`, `minute_5min_ohlcv`, `minute_15min_ohlcv`,
        `minute_hourly_ohlcv`, `minute_4hour_ohlcv`
  - [x] Note in a comment: caggs are views and are NOT covered by `ALL TABLES IN
        SCHEMA public`, which is why they are enumerated
  - [x] Success: as `trading_app`, `SELECT count(*)` succeeds on every
        application table and on all 9 caggs
  - [x] Effort: 2

- [x] **1.3 Grant the application role its write surface**
  - [x] `GRANT INSERT, UPDATE, DELETE` on exactly: `minute_ohlcv`, `daily_ohlcv`,
        `data_gaps`, `acquisition_state`, `daemon_heartbeat`, `trading_sessions`,
        `instruments`, `provider_symbol_mapping`, `universe_members`, `splits`,
        `dividends`, `backfill_state`, `trading_calendars`, `trading_holidays`
  - [x] Grant `SELECT` only on `schema_migrations` — no INSERT/UPDATE/DELETE
  - [x] `GRANT TEMPORARY ON DATABASE trading TO trading_app` (D2 — required by
        the COPY path)
  - [x] Grant `USAGE` on all sequences in `public` (identity/serial columns need
        it for INSERT)
  - [x] Do NOT grant `TRUNCATE` on any table
  - [x] Success: the 14 write tables accept DML as `trading_app`;
        `schema_migrations` rejects it
  - [x] Effort: 2

- [x] **1.4 Add default privileges for future tables**
  - [x] `ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT
        SELECT, INSERT, UPDATE, DELETE ON TABLES TO trading_app`
  - [x] Repeat the same for `FOR ROLE trading_migrate` — default privileges are
        scoped to the creating role, so a migration run as `trading_migrate`
        would otherwise produce tables the app role cannot read
  - [x] Add matching default privileges for sequences (`USAGE`)
  - [x] Success: a table created by either role in `public` is immediately
        readable and writable by `trading_app` with no manual grant
  - [x] Effort: 2

- [x] **1.5 Apply the artifact to production and prove idempotency**
  - [x] Run against `trading` on .144 using the superuser credential
  - [x] Run it a **second** consecutive time
  - [x] Success: both runs exit 0 under `psql -v ON_ERROR_STOP=1`; the second run
        produces no error
  - [x] Effort: 1

---

## Section 2 — Negative-case tests (the incident cannot recur)

These are the tests that make the protection non-regressible. They must exist
before any credential is switched.

### Redesigned 20260806 — tests target an ephemeral database, never `trading`

The original Section 2 pointed these tests at the production `trading`
database. That was wrong, and a first implementation attempt proved it
concretely: `DROP TABLE daemon_heartbeat` requires an `ACCESS EXCLUSIVE` lock,
so even inside a rolled-back transaction it queues behind live readers and
blocks every subsequent reader and writer of that table until it resolves. The
run hung in that lock queue and had to be killed. Rolling back protects data;
it does not protect availability.

This also violated the `sql.md` rule the slice exists to enforce: *"A fixture
that issues TRUNCATE/DROP/ALTER/DELETE may only target a database it created."*

**Constraint that shapes the redesign — roles are cluster-wide, grants are
per-database.** `pg_authid` is a shared catalog (verified), and the ephemeral
test database lives on the *same cluster* as prod. So `CREATE ROLE`,
`GRANT postgres TO trading_migrate`, and any `ALTER ROLE` reach across into the
role prod depends on, while `GRANT ... ON TABLE` does not. The test must
therefore provision **test-local role names**, never `trading_app` /
`trading_migrate` themselves.

- [x] **2.1 Parameterize `provision_roles.sql` on database and role names**
  - [x] Replace the two hardcoded `GRANT ... ON DATABASE trading` references
        (currently lines 62 and 114) with `:DBNAME`, which psql already
        substitutes — it printed `trading` correctly on both prod runs, so no
        new argument is needed for the production invocation
  - [x] Parameterize the two role names via psql variables with defaults, so
        production applies unchanged (`trading_app` / `trading_migrate`) while
        a test run can pass throwaway names
  - [x] Rationale: one artifact stays the single source of truth. A fixture
        that applied its *own* derived grant set could pass while the real
        artifact is wrong — precisely the class of false confidence this slice
        exists to remove
  - [x] Re-apply to prod and re-confirm idempotency after the edit (two
        consecutive runs, `ON_ERROR_STOP=1`, both exit 0)
  - [x] Success: the artifact applies unchanged to `trading`; the same file
        applies to an ephemeral database under different role names
  - [x] Effort: 2

- [x] **2.2 Add the ephemeral role-privilege fixture**
  - [x] Build on the existing `migrated_db` fixture
        ([test/conftest.py](../../../test/conftest.py)) so the test database
        has real schema and is one the fixture created itself
  - [x] Apply the parameterized artifact to that database using **uniquely
        named** roles (e.g. suffixed with the same UUID fragment as the
        database). Never `trading_app` / `trading_migrate` — those are shared
        cluster objects that prod is using
  - [x] Drop the test roles on teardown; `DROP ROLE` fails while grants remain,
        so revoke or drop the owned objects first
  - [x] Must NOT read `MT_TIMESCALE_DB_URL` — derive everything from
        `MT_TIMESCALE_TEST_URL`, keeping the file outside the ratchet allowlist
  - [x] Skip only when the database is **not configured**; never swallow an
        exception from the connection, from role provisioning, or from
        `SET ROLE`. A broad except-to-skip would turn the whole suite green
        while asserting nothing — the one outcome this slice cannot tolerate
  - [x] Success: fixture skips (not errors) with no DB configured; creates and
        cleans up its own roles; leaves no residue in `pg_roles`; both static
        ratchet guards still pass
  - [x] Effort: 3

- [x] **2.3 Assert the three incident statements are denied**
  - [x] Assert `TRUNCATE instruments` raises `InsufficientPrivilege`
  - [x] Assert `DROP TABLE daemon_heartbeat` raises an error (`must be owner`)
  - [x] Assert `DELETE FROM schema_migrations` raises `InsufficientPrivilege`
  - [x] Safe to assert DROP here precisely because the target is a database the
        fixture created — no live reader can be blocked
  - [x] Set `lock_timeout` on the session anyway, so a future change that
        reintroduces contention fails fast instead of hanging a suite
  - [x] Success: all three pass against the ephemeral database; each failure
        message names the statement that was wrongly permitted
  - [x] Effort: 2

- [x] **2.4 Assert the positive surface still works**
  - [x] Assert `SELECT` succeeds on every application table
  - [x] Assert `INSERT`/`UPDATE`/`DELETE` succeed on a representative write
        table (rolled back)
  - [x] Assert temp-table creation succeeds (D2 — guards the COPY hot path)
  - [x] Cagg assertions depend on which aggregates the migration chain creates
        in a fresh database. Assert against the caggs actually present rather
        than the prod list of 9 — a hardcoded count would be brittle. If none
        are materialized, note it and rely on the prod verification in 2.5
  - [x] Success: all assertions pass; a missing grant fails with a message
        naming the object
  - [x] Effort: 2

- [x] **2.5 Record the one-time production privilege verification**
  - [x] The ephemeral suite proves the *artifact* is correct. It cannot prove
        prod's live state matches, since it never connects there — a `REVOKE`
        on prod tomorrow would leave every test green
  - [x] **Revised 20260807 — read the catalog, attempt nothing.** This task
        originally called for confirming denials on prod by *running*
        `TRUNCATE` / `DROP` / ledger-`DELETE`. The PM rejected that, correctly:
        a check whose failure mode is executing the statement it verifies is
        not a check. `DROP` additionally takes an `ACCESS EXCLUSIVE` lock and
        blocks live readers even when rolled back — the same hazard that forced
        the Section 2 redesign, left in the manual path by oversight
  - [x] Verify instead by reading privilege state as data:
        `information_schema.table_privileges` (TRUNCATE absent, ledger
        SELECT-only, cagg SELECT grants, write-table UPDATE grants),
        `pg_tables.tableowner` (owns nothing), `pg_roles` (not superuser, no
        createdb/createrole/bypassrls)
  - [x] Success: walkthrough step 2a carries the queries and the evidence, and
        states plainly that prod confirmation is manual, not covered by CI
  - [x] Effort: 1
  - [x] Done: commit `15cd1c6`. Verified against `trading` 20260806 — TRUNCATE
        0 rows, ledger SELECT only, 0 tables owned, all four role attributes
        false, 9/9 caggs granted SELECT

---

## Section 3 — Maintenance URL settings key

- [x] **3.1 Add the maintenance URL setting**
  - [x] Add `timescale_maintenance_url: str | None = None` to
        [Settings](../../../src/manta_trading/config/__init__.py) beside
        `timescale_db_url` (resolves `MT_TIMESCALE_MAINTENANCE_URL`)
  - [x] Add the key, commented, to `.env_sample` with a note that it holds the
        migration/maintenance credential and is only needed for DDL commands
  - [x] Success: `Settings()` exposes the field; unset yields `None`
  - [x] Effort: 1

- [x] **3.2 Add an explicit maintenance-URL resolver**
  - [x] Add a helper beside `_get_timescale_url`
        ([data.py:389](../../../src/manta_trading/cli/commands/data.py)) that
        returns `settings.timescale_maintenance_url`
  - [x] Raise a `typer.BadParameter` (or the module's existing failure idiom)
        naming `MT_TIMESCALE_MAINTENANCE_URL` when unset
  - [x] **Must not** fall back to `timescale_db_url` under any condition (D4)
  - [x] Success: unset key produces an error message containing the variable
        name; the function has no fallback branch
  - [x] Effort: 1

- [x] **3.3 Test the resolver's fail-loud behavior**
  - [x] Test: maintenance key set → returns that value
  - [x] Test: maintenance key unset, `timescale_db_url` set → **raises**, and the
        raised message names `MT_TIMESCALE_MAINTENANCE_URL`
  - [x] The second test is the regression guard against a fallback being added
        later; add a comment saying so
  - [x] Success: both tests pass
  - [x] Effort: 1

- [x] **3.4 Route DDL commands through the maintenance resolver**
  - [x] Update each to resolve the maintenance URL instead of
        `settings.timescale_db_url`: `mt data init` (default path only —
        `--validate-only` stays on the read credential), `mt data migrate apply`,
        `mt data restore run`, `mt data rechunk` (real run only, not
        `--dry-run`), `mt data caggs repair` (real run only), `mt data caggs
        refresh`
  - [x] Leave `mt data migrate status` and `mt data restore assess` on the read
        credential — they are genuinely read-only
  - [x] No library-module changes: every module below the CLI already accepts
        `conninfo`/`pool` as a parameter
  - [x] Note: [runner.py:81](../../../src/manta_trading/market/schema/runner.py)
        re-connects raw from `pool.conninfo` for the 6 `requires_autocommit`
        migrations. It needs no change — it inherits whichever URL built the
        pool — but is verified in 4.3
  - [x] Success: each listed command resolves the maintenance key; the four
        read-only commands are untouched
  - [x] Effort: 3

- [x] **3.5 Test DDL command URL routing**
  - [x] For each command in 3.4, assert it fails with the maintenance-key error
        when that key is unset, without attempting a connection
  - [x] Assert `mt data migrate status` and `mt data restore assess` still work
        with only `timescale_db_url` set
  - [x] Success: tests pass; no test reads `MT_TIMESCALE_DB_URL`
  - [x] Effort: 2

### Pre-existing tests updated

Two tests asserted the old contract and were updated:
- `test/unit/cli/commands/test_data_init.py::test_missing_url_exits_with_error`
- `test/unit/test_cli_data.py::TestMigrateApply::test_migrate_apply_missing_url_exits_nonzero`

Their `_settings` MagicMock helpers now set `timescale_maintenance_url` explicitly because a bare MagicMock auto-creates a truthy attribute. Both tests remain green.

---

## Section 4 — Offline verification under application credentials

This is the section that finds missing grants. It runs against a scratch
environment or an explicitly-overridden invocation — it does **not** change any
running process.

- [ ] **4.1 Verify the CLI read surface**
  - [ ] With `MT_TIMESCALE_DB_URL` overridden to the application credential for
        the invocation, run: `mt data status`, `mt data caggs status`, `mt data
        get SPY --start 2026-07-01 --end 2026-08-01`
  - [ ] `mt data status` exercises the auto-extend write to `trading_sessions`
        ([data.py:883](../../../src/manta_trading/cli/commands/data.py)) — it is
        a writer despite appearing read-only (D3)
  - [ ] `mt data caggs status` exercises `_timescaledb_catalog` and
        `cagg_watermark`
  - [ ] Success: all three complete without `permission denied`; bars return and
        cagg status lists all 9 aggregates with watermarks
  - [ ] Effort: 2

- [ ] **4.2 Verify the daemon hot path**
  - [ ] Under the application credential, run a bounded minute pull for one
        symbol over a recent window
  - [ ] Confirm rows land, exercising the temp-table COPY path (D2)
  - [ ] Run a bounded daily cycle
  - [ ] Success: both complete; row counts increase; no `permission denied` in
        logs
  - [ ] Effort: 3

- [ ] **4.3 Verify migrations under the maintenance role**
  - [ ] Run `mt data migrate apply` with the maintenance credential against a
        scratch database (not prod) that is behind on migrations, so real DDL
        executes rather than a no-op
  - [ ] Confirm at least one `requires_autocommit` migration applies, exercising
        the `runner.py:81` raw-reconnect path
  - [ ] Run `MT_TIMESCALE_MAINTENANCE_URL="$APP_URL" mt data migrate apply` and
        confirm it fails on privilege — proving the DDL path fails on the role's
        rights, not merely on which key it read
  - [ ] Success: migrations apply under maintenance; denied under application
  - [ ] Effort: 3

- [ ] **4.4 Verify the API surface**
  - [ ] Start `mt serve` with the application credential
  - [ ] Exercise `/api/v1/health`, `/api/v1/status`, `/api/v1/symbols/SPY`, and a
        bars request
  - [ ] Success: all return 200 with correct payloads; no `permission denied`
  - [ ] Effort: 2

- [ ] **4.5 Verify default privileges on a new table**
  - [ ] On a scratch database, create a table as the maintenance role
  - [ ] Confirm `trading_app` can immediately `SELECT` and `INSERT` with no
        manual grant
  - [ ] Success: both succeed — proves task 1.4 covers future migrations
  - [ ] Effort: 1

- [ ] **4.6 Record findings and update the walkthrough**
  - [ ] Record any grant discovered missing during 4.1–4.5, and add it to
        `provision_roles.sql` (then re-run 1.5 idempotency)
  - [ ] Refine the LLD Verification Walkthrough with the commands as actually run
  - [ ] Success: walkthrough reflects reality; artifact covers every grant the
        offline pass required
  - [ ] Effort: 2

---

## Section 5 — Live cutover (GATED — requires explicit PM approval)

**Do not begin this section as a continuation of Section 4.** Sections 1–4
deliver the incident protection: once `MT_TIMESCALE_DB_URL` holds a non-superuser
credential, a leaked URL is non-destructive regardless of what the daemon
connects as. Switching live processes is a separate operational decision the PM
has explicitly deferred.

- [ ] **5.1 Confirm PM approval to cut over**
  - [ ] Success: approval recorded; Section 4 fully green
  - [ ] Effort: 1

- [ ] **5.2 Switch live credentials**
  - [ ] Point `MT_TIMESCALE_DB_URL` at the application credential on .144
  - [ ] Set `MT_TIMESCALE_MAINTENANCE_URL` to the maintenance credential
  - [ ] Restart the daemon and API server
  - [ ] Keep the superuser URL recorded out-of-band for rollback
  - [ ] Success: both processes start and serve; rollback is a one-line revert
  - [ ] Effort: 2

- [ ] **5.3 Observe a full production cycle**
  - [ ] Watch a complete daily cycle and several minute cycles under the new
        credential
  - [ ] Check `acquisition_state` and `daemon_heartbeat` advance; scan logs for
        `permission denied`
  - [ ] Success: a full cycle completes with no privilege errors
  - [ ] Effort: 2

- [ ] **5.4 Retire the superuser credential from ambient configuration**
  - [ ] Remove the superuser URL from `.env` on .144 once 5.3 is green
  - [ ] Retain it in operator records for rollback
  - [ ] Success: no ambient superuser credential; daemon and API keep running
  - [ ] Effort: 1

---

## Completion

- [ ] **6.1 Full validation pass**
  - [ ] Run unit and integration tiers via `scripts/run_tests.py`, separately per
        tier
  - [ ] `ruff check` and `ruff format --check` clean
  - [ ] Both static prod-URL ratchet guards still pass
  - [ ] Success: tiers green apart from the known pre-existing failures
        (`test_cli_lists.py` operator-config assertions; `testNewsIntegration.py`
        requiring `NEWS_DB`)
  - [ ] Effort: 2

- [ ] **6.2 Update slice status and commit**
  - [ ] Set slice design `status` to `complete`; check off the 913 entry in
        [900-slices.foundation-cleanup.md](../architecture/900-slices.foundation-cleanup.md)
  - [ ] Note explicitly in the slice whether Section 5 was completed or deferred
  - [ ] Success: documents reflect actual delivered state
  - [ ] Effort: 1
