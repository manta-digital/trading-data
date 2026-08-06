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
dateUpdated: 20260806
status: not_started
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

- [ ] **1.1 Create the role-provisioning SQL artifact**
  - [ ] Create `scripts/sql/provision_roles.sql`
  - [ ] Guard role creation in a `DO` block testing `pg_roles` so a second run
        does not error on the already-existing `trading_app`
  - [ ] Create `trading_migrate` if absent; `GRANT postgres TO trading_migrate`
        so it inherits ownership rights without per-object `ALTER ... OWNER`
  - [ ] Do NOT set passwords in the artifact. Add a header comment stating that
        passwords are set out-of-band and never committed
  - [ ] Success: file exists, is pure SQL, contains no credentials, and contains
        no `ALTER ... OWNER` statement
  - [ ] Effort: 2

- [ ] **1.2 Grant the application role its read surface**
  - [ ] `GRANT USAGE ON SCHEMA public TO trading_app`
  - [ ] `GRANT SELECT ON ALL TABLES IN SCHEMA public TO trading_app`
  - [ ] Grant `SELECT` on all 9 continuous aggregates by name: `daily_coverage`,
        `minute_coverage`, `daily_weekly_ohlcv`, `daily_monthly_ohlcv`,
        `daily_quarterly_ohlcv`, `minute_5min_ohlcv`, `minute_15min_ohlcv`,
        `minute_hourly_ohlcv`, `minute_4hour_ohlcv`
  - [ ] Note in a comment: caggs are views and are NOT covered by `ALL TABLES IN
        SCHEMA public`, which is why they are enumerated
  - [ ] Success: as `trading_app`, `SELECT count(*)` succeeds on every
        application table and on all 9 caggs
  - [ ] Effort: 2

- [ ] **1.3 Grant the application role its write surface**
  - [ ] `GRANT INSERT, UPDATE, DELETE` on exactly: `minute_ohlcv`, `daily_ohlcv`,
        `data_gaps`, `acquisition_state`, `daemon_heartbeat`, `trading_sessions`,
        `instruments`, `provider_symbol_mapping`, `universe_members`, `splits`,
        `dividends`, `backfill_state`, `trading_calendars`, `trading_holidays`
  - [ ] Grant `SELECT` only on `schema_migrations` — no INSERT/UPDATE/DELETE
  - [ ] `GRANT TEMPORARY ON DATABASE trading TO trading_app` (D2 — required by
        the COPY path)
  - [ ] Grant `USAGE` on all sequences in `public` (identity/serial columns need
        it for INSERT)
  - [ ] Do NOT grant `TRUNCATE` on any table
  - [ ] Success: the 14 write tables accept DML as `trading_app`;
        `schema_migrations` rejects it
  - [ ] Effort: 2

- [ ] **1.4 Add default privileges for future tables**
  - [ ] `ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT
        SELECT, INSERT, UPDATE, DELETE ON TABLES TO trading_app`
  - [ ] Repeat the same for `FOR ROLE trading_migrate` — default privileges are
        scoped to the creating role, so a migration run as `trading_migrate`
        would otherwise produce tables the app role cannot read
  - [ ] Add matching default privileges for sequences (`USAGE`)
  - [ ] Success: a table created by either role in `public` is immediately
        readable and writable by `trading_app` with no manual grant
  - [ ] Effort: 2

- [ ] **1.5 Apply the artifact to production and prove idempotency**
  - [ ] Run against `trading` on .144 using the superuser credential
  - [ ] Run it a **second** consecutive time
  - [ ] Success: both runs exit 0 under `psql -v ON_ERROR_STOP=1`; the second run
        produces no error
  - [ ] Effort: 1

---

## Section 2 — Negative-case tests (the incident cannot recur)

These are the tests that make the protection non-regressible. They must exist
before any credential is switched.

- [ ] **2.1 Add test infrastructure for role-privilege assertions**
  - [ ] Add a fixture supplying an application-role connection. Derive it by
        connecting as the existing test/admin credential and issuing `SET ROLE
        trading_app` — this needs no new password and no new env var
  - [ ] The fixture must respect the existing prod-URL guards: it must NOT read
        `MT_TIMESCALE_DB_URL`. Follow the pattern in
        [test/conftest.py](../../../test/conftest.py)
  - [ ] Skip cleanly when the database is **not configured** — skip on absent
        configuration only, never on an exception from the connection or from
        `SET ROLE` itself
  - [ ] `SET ROLE trading_app` is authorized against `session_user`, so it
        succeeds only while the test credential is `postgres` or a member of
        `trading_app`. A non-member raises `InsufficientPrivilege: permission
        denied to set role` (measured). That error must **propagate as a
        failure** — a broad except-to-skip here would turn the entire negative
        suite green while asserting nothing, which is the one outcome this
        slice cannot tolerate
  - [ ] If the test tier ever runs as a non-superuser, fix it by granting
        membership (`GRANT trading_app TO <test_role>`), not by widening the
        skip
  - [ ] Success: fixture skips (not errors) with no DB configured; with a DB
        configured but role membership missing, the suite **fails loudly**
        rather than skipping; both static ratchet guards still pass
  - [ ] Effort: 3

- [ ] **2.2 Assert the three incident statements are denied**
  - [ ] Assert `TRUNCATE instruments` raises `InsufficientPrivilege`
  - [ ] Assert `DROP TABLE daemon_heartbeat` raises an error (`must be owner`)
  - [ ] Assert `DELETE FROM schema_migrations` raises `InsufficientPrivilege`
  - [ ] Wrap each in a transaction that is rolled back, so a regression that
        *permits* the statement still cannot destroy anything
  - [ ] Success: all three assertions pass against `trading`; each failure
        message names the statement that was wrongly permitted
  - [ ] Effort: 2

- [ ] **2.3 Assert the positive surface still works**
  - [ ] Assert `SELECT` succeeds on every application table and all 9 caggs
  - [ ] Assert `INSERT`/`UPDATE`/`DELETE` succeed on a representative write table
        (rolled back)
  - [ ] Assert temp-table creation succeeds (D2 — guards the COPY hot path)
  - [ ] Assert `_timescaledb_functions.cagg_watermark(mat_hypertable_id)` returns
        a value, confirming `SELECT` grants are sufficient for
        `mt data caggs status`
  - [ ] Success: all assertions pass; a missing grant fails with a message naming
        the object
  - [ ] Effort: 2

---

## Section 3 — Maintenance URL settings key

- [ ] **3.1 Add the maintenance URL setting**
  - [ ] Add `timescale_maintenance_url: str | None = None` to
        [Settings](../../../src/manta_trading/config/__init__.py) beside
        `timescale_db_url` (resolves `MT_TIMESCALE_MAINTENANCE_URL`)
  - [ ] Add the key, commented, to `.env_sample` with a note that it holds the
        migration/maintenance credential and is only needed for DDL commands
  - [ ] Success: `Settings()` exposes the field; unset yields `None`
  - [ ] Effort: 1

- [ ] **3.2 Add an explicit maintenance-URL resolver**
  - [ ] Add a helper beside `_get_timescale_url`
        ([data.py:389](../../../src/manta_trading/cli/commands/data.py)) that
        returns `settings.timescale_maintenance_url`
  - [ ] Raise a `typer.BadParameter` (or the module's existing failure idiom)
        naming `MT_TIMESCALE_MAINTENANCE_URL` when unset
  - [ ] **Must not** fall back to `timescale_db_url` under any condition (D4)
  - [ ] Success: unset key produces an error message containing the variable
        name; the function has no fallback branch
  - [ ] Effort: 1

- [ ] **3.3 Test the resolver's fail-loud behavior**
  - [ ] Test: maintenance key set → returns that value
  - [ ] Test: maintenance key unset, `timescale_db_url` set → **raises**, and the
        raised message names `MT_TIMESCALE_MAINTENANCE_URL`
  - [ ] The second test is the regression guard against a fallback being added
        later; add a comment saying so
  - [ ] Success: both tests pass
  - [ ] Effort: 1

- [ ] **3.4 Route DDL commands through the maintenance resolver**
  - [ ] Update each to resolve the maintenance URL instead of
        `settings.timescale_db_url`: `mt data init` (default path only —
        `--validate-only` stays on the read credential), `mt data migrate apply`,
        `mt data restore run`, `mt data rechunk` (real run only, not
        `--dry-run`), `mt data caggs repair` (real run only), `mt data caggs
        refresh`
  - [ ] Leave `mt data migrate status` and `mt data restore assess` on the read
        credential — they are genuinely read-only
  - [ ] No library-module changes: every module below the CLI already accepts
        `conninfo`/`pool` as a parameter
  - [ ] Note: [runner.py:81](../../../src/manta_trading/market/schema/runner.py)
        re-connects raw from `pool.conninfo` for the 6 `requires_autocommit`
        migrations. It needs no change — it inherits whichever URL built the
        pool — but is verified in 4.3
  - [ ] Success: each listed command resolves the maintenance key; the four
        read-only commands are untouched
  - [ ] Effort: 3

- [ ] **3.5 Test DDL command URL routing**
  - [ ] For each command in 3.4, assert it fails with the maintenance-key error
        when that key is unset, without attempting a connection
  - [ ] Assert `mt data migrate status` and `mt data restore assess` still work
        with only `timescale_db_url` set
  - [ ] Success: tests pass; no test reads `MT_TIMESCALE_DB_URL`
  - [ ] Effort: 2

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
