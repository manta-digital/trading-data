---
docType: tasks
slice: multi-database-migration-and-credential-plumbing
project: trading-data
lld: user/slices/923-slice.multi-database-migration-and-credential-plumbing.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [913, 917]
interfaces: [222]
projectState: >
  Slice design committed and reviewed (CONCERNS, addressed in df5b3e5).
  Prerequisites 913 (least-privilege roles, maintenance URL) and 917 (test
  cluster) are complete. Every migration track targets the primary database
  today; `Settings.tick_db_url` exists with no consumer. 220 is released as
  0.18.0; its slice 222 is hard-gated on this slice.
dateCreated: 20260927
dateUpdated: 20260927
status: in_progress
---

# Tasks: Multi-Database Migration and Credential Plumbing

## Context Summary

- Working on slice 923. It teaches the migration tooling that a track belongs
  to a database, so tick migrations (slice 222 onward) can never land in the
  primary `trading` database.
- It delivers:
  - `market/schema/databases.py` (database identity, URL resolver, misroute
    guard);
  - a track registry that routes each track to its database;
  - `mt data migrate apply|status` routed by track, and
    `mt data init --database`;
  - `scripts/provision_tick_roles.sql`;
  - tick test fixtures;
  - the production-URL guards extended to the two tick variables.
- **Nothing in production changes.** No production tick database is created.
  The primary path must be unchanged: the same variables, messages and output.
  The proof is that the 913 unit tests pass with **no edits**.
- **No fallback in any direction.** Tick maintenance never falls back to tick
  application, and tick never falls back to primary (LLD D2).
- All decisions (D1–D9) and the numbered Functional Requirements are in the LLD.
  Read D1–D9 and Error Handling before starting.
- Every destructive statement targets a database or role that a fixture or this
  walkthrough created on the test cluster (`sql.md`).
- Next slice: 222 (tick storage track). It appends to the `tick` track and the
  artifact's write list.

**Test environment.** Export `MT_TIMESCALE_TEST_URL` from `.env` with the quotes
stripped (LLD Walkthrough, step 0). Run mypy on the src kalshi paths and the
tests in one invocation (a narrower run reports false errors). Run the unit and
integration tiers separately. Known pre-existing failures are not regressions:
re-run in isolation before investigating.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 0 — Baseline

- [x] **0.1 Capture the primary migration status before any change**
  - [x] On the target (`cf config get git.integration_branch`, or `main` when
        empty; this is the slice branch's fork point), before the slice
        branch's first commit, run
        `uv run mt data migrate status --json > /tmp/923-before.json` (LLD
        Walkthrough, step 1). This reads production through the application
        credential only.
  - [x] Success: the file exists and holds `"connected": true`
  - [x] Effort: 1

---

## Section 1 — Database identity and track registry

- [x] **1.1 Add `Settings.tick_maintenance_url`**
  - [x] In `src/manta_trading/config/__init__.py`, add
        `tick_maintenance_url: str | None = None` next to `tick_db_url`
  - [x] Replace the `# Tick data database (separate instance)` comment with one
        stating that the tick pair mirrors the primary pair, and that callers
        must never fall back between them (mirror the 913 comment above it)
  - [x] Add tests to `test/unit/test_settings.py`: the default is `None`, and
        `MT_TICK_MAINTENANCE_URL` overrides it (follow the existing
        `tick_db_url` tests)
  - [x] Success: `uv run pytest test/unit/test_settings.py -q` passes
  - [x] Effort: 1

- [x] **1.2 Create `market/schema/databases.py`: enums, URL table, resolver**
  - [x] `Database(StrEnum)`: `PRIMARY`, `TICK`. `Credential(StrEnum)`:
        `APPLICATION`, `MAINTENANCE`
  - [x] `DATABASE_URL_FIELDS: dict[tuple[Database, Credential], str]` maps the
        four pairs to `timescale_db_url`, `timescale_maintenance_url`,
        `tick_db_url` and `tick_maintenance_url`. This is the only place a
        database's settings fields are named.
  - [x] `env_var_for(db, cred)` returns `"MT_" + field.upper()`. Read the
        prefix from `Settings.model_config["env_prefix"]` rather than repeating
        `"MT_"`.
  - [x] `DatabaseNotConfiguredError(Exception)` carries `env_var`. Its message
        is `"<ENV_VAR> not configured. Set the environment variable or add it to
        your .env file."`, which is the existing primary wording from
        `_get_maintenance_url`. Copy the exact text from `data.py`.
  - [x] `resolve_database_url(settings, db, cred) -> str`: raises on `None` or
        `""`. It never consults another field.
  - [x] Module docstring: why this sits below the CLI (LLD Component Structure)
  - [x] Success: the module imports cleanly and has no dependency on `cli`
  - [x] Effort: 2

- [x] **1.3 Unit tests for `databases.py`**
  - [x] Create `test/unit/market/schema/__init__.py` (empty) and
        `test/unit/market/schema/test_databases.py`
  - [x] Build every `Settings` with `Settings(_env_file=None, ...)` so a
        developer's `.env` cannot leak in
  - [x] Parametrize over all four (database, credential) pairs: set → returned;
        `None` → error naming the right variable; `""` → error
  - [x] No-fallback cases:
    1. tick maintenance unset, tick application set → error names
       `MT_TICK_MAINTENANCE_URL`
    2. tick application unset, both primary URLs set → error names
       `MT_TICK_DB_URL`
  - [x] `env_var_for` returns exactly `MT_TIMESCALE_DB_URL`,
        `MT_TIMESCALE_MAINTENANCE_URL`, `MT_TICK_DB_URL` and
        `MT_TICK_MAINTENANCE_URL`
  - [x] Success: the new file passes
  - [x] Effort: 2

- [x] **1.4 Register the `tick` track and the track registry (D1, D6)**
  - [x] Create `src/manta_trading/market/schema/migrations/tick.py` with
        `TICK_MIGRATIONS` holding only the `001_schema_migrations` bootstrap
        entry. Reuse the existing bootstrap dict; do not copy its SQL.
        A module comment states that later ids use `tick_NNN_*`.
  - [x] In `migrations/__init__.py`:
    1. Add a frozen `TrackSpec(database: Database, migrations: list[...])`.
    2. Add `TRACK_REGISTRY`, mapping `minute`, `daily` and `kalshi` →
       `PRIMARY` and `tick` → `TICK`.
    3. Derive `TRACKS = {name: spec.migrations ...}`, with the same type as
       today.
    4. Add `DEFAULT_TRACK_FOR = {PRIMARY: "minute", TICK: "tick"}`, and set
       `DEFAULT_TRACK = DEFAULT_TRACK_FOR[Database.PRIMARY]`.
    5. Export the new names in `__all__`.
  - [x] Avoid an import cycle: `migrations` imports `Database` from
        `databases.py`, so `databases.py` must not import `migrations` at module
        level
  - [x] Success: `grep -rn "TRACKS\[" src` consumers are unchanged, and
        `uv run python -c "from manta_trading.market.schema.migrations import
        TRACKS; print(sorted(TRACKS))"` prints the four tracks
  - [x] Effort: 2

- [x] **1.5 Unit tests for the registry**
  - [x] Create `test/unit/market/schema/test_track_registry.py`
  - [x] Every track has a `Database`, and every `Database` member has a
        `DEFAULT_TRACK_FOR` entry that names a registered track
  - [x] `TRACKS[name] is TRACK_REGISTRY[name].migrations` for every name
  - [x] Non-bootstrap migration ids are unique across all tracks sharing a
        database (the guard relies on this)
  - [x] Every id in `TRACKS["tick"]` after the bootstrap starts with `tick_`
        (vacuous today; binds 222)
  - [x] Success: the new file passes
  - [x] Effort: 1

- [x] **1.6 Make `_get_maintenance_url(ctx)` a wrapper over the resolver (D2)**
  - [x] In `cli/commands/data.py`, the body calls
        `resolve_database_url(settings, PRIMARY, MAINTENANCE)` and maps
        `DatabaseNotConfiguredError` to `print_error` + `typer.Exit(1)`. The
        signature and exact message stay the same.
  - [x] Leave its six call sites untouched
  - [x] Success: `uv run pytest test/unit/cli/test_maintenance_url_resolver.py
        test/unit/cli/test_ddl_command_url_routing.py
        test/unit/cli/commands/test_data_init.py test/unit/test_cli_data.py -q`
        passes, and `git diff --stat` on those four files is empty
  - [x] Effort: 1

- [x] **1.7 Checkpoint**
  - [x] ruff check + ruff format on touched files only, then check for swept
        pre-existing lines: `git diff` shows no unrelated changes
  - [x] Commit: `feat: add database identity and track-to-database registry`
  - [x] Effort: 1

---

## Section 2 — CLI routing and the misroute guard

- [x] **2.1 Implement the misroute guard (D5)**
  - [x] In `databases.py`, add `LedgerMisrouteError(Exception)`. It carries the
        target database, the target track, up to five foreign ids, and the
        variable to check. Its message follows the LLD Walkthrough step 5 text:
        "refusing: this database's ledger holds migrations of track(s) routed to
        <db> (<ids>); check <ENV_VAR>"
  - [x] Split it into a pure function and a thin query:
    1. `foreign_ledger_ids(ledger_ids, database) -> list[str]`: ids belonging to
       tracks routed to another database. It excludes `001_schema_migrations`,
       ignores ids in no track, and returns them sorted. It reads
       `TRACK_REGISTRY` through a function-local import (task 1.4 cycle note).
    2. `assert_ledger_belongs(pool, database, track, env_var)`. It checks
       `to_regclass('schema_migrations')`: a missing ledger returns. Otherwise
       it selects the ids, calls (1), and raises on any hit. It issues only
       `SELECT`s.
  - [x] Success: under 50 lines each; `databases.py` stays under 300 lines
  - [x] Effort: 2

- [x] **2.2 Unit tests for the guard**
  - [x] In `test_databases.py`, test `foreign_ledger_ids` for:
    1. minute ids against `TICK` → returned
    2. `tick_*`-shaped ids against `PRIMARY` (monkeypatch a registry with one
       fake `tick_001_x` id) → returned
    3. the bootstrap id → never returned
    4. unknown ids → ignored
    5. the target's own ids → empty
  - [x] Test `assert_ledger_belongs` with a stub pool/cursor: a missing ledger
        returns without the id query; a hit raises with at most five ids and
        names the variable
  - [x] Success: tests pass
  - [x] Effort: 2

- [x] **2.3 Add one routed-connection helper in `data.py`**
  - [x] Add `_routed_db(ctx, database, credential)`. It resolves the URL, maps
        `DatabaseNotConfiguredError` to `print_error` + exit 1, and returns
        `(_create_timescale_db(ctx, conninfo=url), env_var)`. It always passes
        `conninfo` (the LLD's "Factory contract" paragraph under Data Flow).
  - [x] Add a context manager or wrapper that maps
        `psycopg_pool.PoolTimeout` and `psycopg.OperationalError` to
        `print_error("could not connect to the <database> database (<ENV_VAR>):
        <error>")` + exit 1 (LLD Error Handling). Catch exactly these two types.
        Add a comment saying it is a process-boundary handler.
  - [x] Map `LedgerMisrouteError` to `print_error` + exit 1 in the same place
  - [x] Success: one definition of each mapping, used by all three commands
        below
  - [x] Effort: 2

- [x] **2.4 Route `mt data migrate apply` by track**
  - [x] Resolve via `TRACK_REGISTRY[track].database` and `MAINTENANCE`, then run
        `assert_ledger_belongs` and then `apply_schema_migrations(TRACKS[track])`
  - [x] Replace the stale `_TRACK_OPTION` comment ("Both tracks target the same
        database…") with one saying the choices come from the registry and each
        track routes to its own database
  - [x] Success: `--track minute` and no option resolve
        `MT_TIMESCALE_MAINTENANCE_URL` with the unchanged message
  - [x] Effort: 2

- [x] **2.5 Route `mt data migrate status` by track**
  - [x] Replace the direct `settings.timescale_db_url` check and the argumentless
        `_create_timescale_db(ctx)` with the resolver: the track's database and
        `APPLICATION`. No guard (read-only).
  - [x] Keep the JSON error shape `{"connected": false, "error": …, "applied":
        [], "pending": []}`. When unconfigured, `error` names the resolved
        variable. For the primary, keep the existing `"URL not configured"` JSON
        text and `"MT_TIMESCALE_DB_URL not configured."` human text byte for
        byte.
  - [x] Keep the existing connection handler (it already covers connect
        failures)
  - [x] Success: `test_cli_data.py` passes unedited
  - [x] Effort: 2

- [x] **2.6 Add `mt data init --database` (D7)**
  - [x] `--database`: choices from `Database`, default `primary`. The track is
        `DEFAULT_TRACK_FOR[database]`.
  - [x] The apply path uses `MAINTENANCE`, runs the guard, then applies
        `TRACKS[track]`. `--validate-only` uses `APPLICATION` and reads
        `list_migration_state(TRACKS[track])`. Both go through `_routed_db`, so
        neither reaches the factory's primary default.
  - [x] The table and JSON output are unchanged for the no-argument path
  - [x] Success: `test_data_init.py` passes unedited
  - [x] Effort: 2

- [x] **2.7 Unit tests for CLI routing**
  - [x] Create `test/unit/cli/test_migrate_track_routing.py`, with Typer's
        `CliRunner` and `Settings(_env_file=None)`. Stub `_create_timescale_db`
        so it records the `conninfo` it received and opens nothing.
  - [x] `apply --track tick` receives `MT_TICK_MAINTENANCE_URL`, and
        `apply --track minute` receives `MT_TIMESCALE_MAINTENANCE_URL`
  - [x] `status --track tick` receives `MT_TICK_DB_URL`, and
        `init --database tick` / `init --database tick --validate-only` receive
        the tick maintenance / application URL
  - [x] Functional Requirement 3: tick maintenance unset while all three other
        URLs are set → exit 1 naming `MT_TICK_MAINTENANCE_URL`, factory never
        called
  - [x] Functional Requirement 2: `MT_TICK_DB_URL` unset and
        `MT_TIMESCALE_DB_URL` set → `status --track tick` and
        `init --database tick --validate-only` both exit 1 naming
        `MT_TICK_DB_URL`, factory never called
  - [x] Connection mapping: the stub raises `PoolTimeout`, then
        `OperationalError` → exit 1, one-line message naming the variable, no
        traceback in the output, and apply is never called
  - [x] Misroute mapping: the guard raises → exit 1, and apply is never called
  - [x] `--track` choices include `tick`; `--database` choices are exactly
        `primary` and `tick`
  - [x] Success: the new file passes, plus the task 1.6 command, with the same
        empty `git diff --stat`
  - [x] Effort: 3

- [x] **2.8 Checkpoint**
  - [x] ruff (touched files, check for swept lines), then mypy per the test
        environment note
  - [x] Commit: `feat: route migrate and init by track database with misroute guard`
  - [x] Effort: 1

---

## Section 3 — Tick provisioning artifact and privilege suite

Measure here first (LLD Risk Assessment). Record each measured role-attribute
requirement as a comment in the artifact, in `provision_roles.sql`'s style.

- [x] **3.1 Write `scripts/provision_tick_roles.sql` (D3, D4)**
  - [x] Header comment:
    - purpose;
    - required `-v tick_db=<name>` (no default);
    - optional `app_role` / `migrate_role` (defaults `tick_app` /
      `tick_migrate`);
    - passwords are set out of band and never committed;
    - it must be reviewed side by side with `provision_roles.sql`.
  - [x] Fail with a clear message when `tick_db` is not supplied (psql `\if`
        on `:{?tick_db}`)
  - [x] Create both roles with `\gexec` guards on `pg_roles`, mirroring
        `provision_roles.sql`
  - [x] Only when `current_user` is not a superuser, grant
        `migrate_role TO current_user WITH SET TRUE`. This is the PG16
        CREATEROLE rule; see LLD D8.
  - [x] `CREATE DATABASE :tick_db OWNER migrate_role`, guarded on
        `pg_database` via `\gexec`, outside a transaction
  - [x] Then `\connect :tick_db`, and in one transaction:
    1. `REVOKE CONNECT ON DATABASE … FROM PUBLIC`
    2. `GRANT CONNECT, TEMPORARY` to the app role
    3. `REVOKE ALL` / `GRANT SELECT` on `schema_migrations` to the app role,
       guarded, because the ledger may not exist yet
    4. `ALTER DEFAULT PRIVILEGES FOR ROLE migrate_role` granting `SELECT,
       INSERT, UPDATE, DELETE` on tables to the app role
  - [x] An empty, commented write-surface list marks where 222 adds tables
  - [x] Success: no credentials, no `GRANT postgres`, no `ALTER … OWNER`, and it
        runs twice without error (proved in 3.3)
  - [x] Effort: 3

- [x] **3.2 Add the `provisioned_tick_db` fixture (D8)**
  - [x] In `test/integration/conftest.py`, so 222 can reuse it. Follow
        `provisioned_roles` in `test/integration/data/test_role_privileges.py`.
  - [x] Per-run names: `tick_db=mt_test_tp<hex>`, `t923_app_<hex>` and
        `t923_mig_<hex>`. Apply the artifact with `psql -v …` as the test admin
        (`MT_TIMESCALE_TEST_URL`), so the artifact creates the database.
  - [x] Apply `TRACKS["tick"]` as the migrate role, so the ledger exists and is
        owned by it
  - [x] Teardown: terminate backends, `DROP DATABASE`, then `DROP OWNED` /
        `REVOKE` / `DROP ROLE` in 913's order. Only names this fixture created.
  - [x] Yields the database name and both role names
  - [x] Success: after a run, `pg_database` has no `mt_test_tp%` rows and
        `pg_roles` has no `t923_%` rows
  - [x] Effort: 3

- [x] **3.3 Tick privilege suite (Functional Requirement 8)**
  - [x] Create `test/integration/data/test_tick_role_privileges.py`, with one
        test per bullet:
    1. As the app role: `TRUNCATE`, `DROP` and ledger `INSERT`/`DELETE` are
       denied, using the `_assert_denied` pattern from the 913 suite.
    2. As the app role: a temp table can be created.
    3. The migrate role owns the database, and `CREATE EXTENSION timescaledb`
       succeeds as it. Drop the extension afterwards.
    4. The primary throwaway app role (from `provisioned_roles`, or one created
       here) has no `CONNECT` on the tick database, per
       `has_database_privilege`.
    5. The tick roles have no privileges in the primary throwaway database (read
       from the catalog, 913 D8).
    6. Re-applying the artifact exits 0 and changes no catalog row counted in
       (1)–(5).
  - [x] If (3) fails: **stop**. Record the exact error and the role
        attributes in this task's notes, and report to the PM. Do not change the
        artifact, the LLD, or Functional Requirement 8. The LLD mitigation needs
        a PM decision first, because it changes 222's first migration.
  - [x] Success: the suite passes; the 913 suite
        `test/integration/data/test_role_privileges.py` passes unedited
  - [x] Effort: 3

- [x] **3.4 Checkpoint**
  - [x] Commit: `feat: add tick database provisioning artifact and privilege suite`
  - [x] Effort: 1

---

## Section 4 — Fixtures and the two-database suite

- [x] **4.1 Extract `_throwaway_database(prefix)` (D8)**
  - [x] In `test/conftest.py`, a context manager holding the create /
        terminate / drop block now duplicated in `ephemeral_db` and
        `session_ephemeral_db`. Both fixtures keep their names, prefixes, scopes
        and teardown.
  - [x] Success: `test/integration/data/test_role_privileges.py` and one
        `migrated_db` consumer (for example the kalshi integration tests) pass,
        and there is no leftover `mt_test_%` database
  - [x] Commit: `refactor(test): share throwaway database helper`
  - [x] Effort: 2

- [x] **4.2 Add `ephemeral_tick_db`, `migrated_tick_db` and a two-database
      settings helper**
  - [x] `ephemeral_tick_db`: `_throwaway_database("mt_test_t")`, owned by the
        test admin. `migrated_tick_db` applies `TRACKS["tick"]`.
  - [x] The helper builds `Settings(_env_file=None)` with the primary pair →
        `migrated_db` and the tick pair → `ephemeral_tick_db`
  - [x] Success: fixtures importable; no test yet
  - [x] Effort: 1

- [x] **4.3 Two-database routing suite**
  - [x] Create `test/integration/data/test_two_database_migrate.py`. Drive the
        CLI with `CliRunner` and the task 4.2 settings.
  - [x] Functional Requirement 1: `apply --track tick` → tick ledger rows in
        the tick database, and the primary ledger has no `tick_*` or new rows
  - [x] Functional Requirement 2: `status --track tick` lists the bootstrap as
        applied
  - [x] Functional Requirement 4, both directions:
    1. tick maintenance → a minute-migrated database: exit 1 naming minute ids,
       and the ledger row count is unchanged
    2. a minute apply against a ledger holding a synthetic `tick_*` id, inserted
       into a throwaway database with a monkeypatched registry: exit 1
  - [x] Functional Requirement 5: `init --database tick` on a bare
        `ephemeral_tick_db` reaches the head of the track; `init` with no option
        still reports the minute track
  - [x] Functional Requirement 6: tick maintenance URL → an unused port on the
        test host: exit 1, one-line message naming the variable, no traceback.
        Shorten the wait by patching the pool timeout in the test, not in
        production code.
  - [x] Success: the suite passes, with no leftover `mt_test_%` databases
  - [x] Effort: 3

- [x] **4.4 Checkpoint**
  - [x] Commit: `test: add tick fixtures and two-database migrate suite`
  - [x] Effort: 1

---

## Section 5 — Production-URL guards (D9)

- [ ] **5.1 Delete `test/integration/test_tick_schema_integration.py`**
  - [ ] It reads `MT_TICK_DB_URL` and applies DDL from the deleted
        `database/migrations/` directory (LLD D9). Delete it first, so the
        ratchet in 5.3 starts with an empty allowlist.
  - [ ] Success: the file is gone; `grep -rn "MT_TICK_DB_URL" test` shows only
        guard code and task 1.1 tests
  - [ ] Effort: 1

- [ ] **5.2 Extend the runtime scrub in `test/conftest.py`**
  - [ ] `pytest_configure` pops a tuple: the existing variable plus
        `MT_TICK_DB_URL` and `MT_TICK_MAINTENANCE_URL`. The report header names
        whichever were scrubbed. `MT_ALLOW_PROD_READS` behaviour is unchanged
        (it covers only the primary read variable; say so in the docstring).
  - [ ] Success: a unit test (in the existing unit guard file, or a new
        `test/unit/test_conftest_scrub.py`) sets all three, calls the scrub, and
        asserts all three are absent
  - [ ] Effort: 2

- [ ] **5.3 Extend the static ratchet in `test/_prod_url_guard.py`**
  - [ ] Needles become a tuple, each concatenated so the module cannot trip
        itself. `prod_url_readers` matches any needle.
  - [ ] Fix the docstring's stale module names: the real files are
        `test/unit/test_unit_prod_url_guard.py` and
        `test/integration/test_integration_prod_url_guard.py`
  - [ ] Update both guard test files so the tick needles have an **empty**
        allowlist. Add a case proving a synthetic multi-line
        `os.environ.get(\n "MT_TICK_DB_URL")` is detected.
  - [ ] Success: both guard tests pass
  - [ ] Effort: 2

- [ ] **5.4 Extend `scripts/run_tests.py` `build_env`**
  - [ ] Pop `MT_TICK_DB_URL` and `MT_TICK_MAINTENANCE_URL`, using the same
        concatenation style
  - [ ] Add `test/unit/test_run_tests_env.py`: with all prod-shaped variables in
        `os.environ`, `build_env("integration", {})` contains none of them
  - [ ] Success: the new test passes
  - [ ] Effort: 1

- [ ] **5.5 Checkpoint**
  - [ ] Commit: `test: guard tick database URLs like production URLs`
  - [ ] Effort: 1

---

## Section 6 — Documentation

- [ ] **6.1 Update the migrations README**
  - [ ] In `src/manta_trading/market/schema/migrations/README.md`, replace the
        stale track table (MarketDB row, `--db` flag) with one row per track:
        module, database, application variable, maintenance variable
  - [ ] State: one ledger per database; the misroute guard; `init --database`;
        tick ids are `tick_NNN_*`; add a track by editing `TRACK_REGISTRY` only
  - [ ] Bump `dateUpdated`
  - [ ] Effort: 1

- [ ] **6.2 Add the tick variables to `.env_sample`**
  - [ ] Add commented `MT_TICK_DB_URL` and `MT_TICK_MAINTENANCE_URL` entries,
        with placeholder values only, and a note: no fallback to each other or
        to the primary pair
  - [ ] Do **not** add either variable to `deploy/manta-trading.env.example`
  - [ ] Effort: 1

- [ ] **6.3 Add the runbook section "Provisioning a tick database"**
  - [ ] Add it to `project-documents/user/runbooks/100-production-operations.md`.
        It is run when the PM decides placement.
    - one command applies the artifact as superuser with `-v tick_db=<name>`;
    - passwords are set out of band;
    - then `mt data init --database tick` runs with `MT_TICK_MAINTENANCE_URL`
      set for that invocation only;
    - `mt data migrate status --track tick` verifies it.
  - [ ] Do not use a real database name or host. The name is the PM's
        placement decision; the section says so.
  - [ ] Effort: 1

- [ ] **6.4 Checkpoint**
  - [ ] Commit: `docs: document tick track routing and tick database provisioning`
  - [ ] Effort: 1

---

## Section 7 — Validation

- [ ] **7.1 Static checks**
  - [ ] ruff on all touched files; mypy per the test environment note
  - [ ] `databases.py` < 300 lines; new functions < 50 lines
  - [ ] `git diff <target> -- src` shows no unrelated reformatting
  - [ ] Effort: 1

- [ ] **7.2 Unit tier, then integration tier**
  - [ ] `uv run python scripts/run_tests.py unit`, then
        `uv run python scripts/run_tests.py integration`, run separately
  - [ ] Any failure not in the known pre-existing list must be fixed. Re-run a
        suspected flake in isolation before investigating.
  - [ ] Success: green, apart from the known pre-existing failures; each one
        listed in the commit message or task notes
  - [ ] Effort: 2

- [ ] **7.3 Verification Walkthrough (LLD steps 1–6)**
  - [ ] Step 1: capture `/tmp/923-after.json` and `diff` it against task 0.1's
        file; it prints nothing
  - [ ] Steps 2–3: the listed unit and integration files pass. The four 913
        unit files show an empty `git diff <target> --stat`. There are no
        leftover `mt_test_%` databases or `t923_%` roles.
  - [ ] Steps 4–5: by hand on throwaway test-cluster databases. The unset
        variable, `init --database tick`, `status --track tick` and the misroute
        refusal each match the expected output. Record the actual output in the
        LLD walkthrough if it differs.
  - [ ] Step 6: drop the walkthrough's two databases
  - [ ] Effort: 2

- [ ] **7.4 Close out the design**
  - [ ] Update the LLD with measured findings (role attributes, extension
        ownership result) and any walkthrough corrections. Set the LLD and this
        file to `status: complete` and bump `dateUpdated`.
  - [ ] Commit: `docs: record 923 implementation findings`
  - [ ] Effort: 1
