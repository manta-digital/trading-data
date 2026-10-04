---
docType: tasks
slice: tick-storage-track
project: trading-data
lld: user/slices/222-slice.tick-storage-track.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [923, 220, 221]
interfaces: [223, 224, 225, 226, 227, 229, 230, 231]
projectState: >
  Slice design committed and reviewed (CONCERNS; F004 addressed in abfb5f1).
  923 (tick track, fixtures, grant artifact), 220 (tick enums, DBN reader,
  fixtures) and 221 (CME session model) are complete; 0.20.0 released. The
  tick track holds only the ledger bootstrap and the artifact's write list is
  empty. No production tick database exists.
dateCreated: 20260928
dateUpdated: 20260928
status: complete
---

# Tasks: Tick Storage Track

## Context Summary

- Working on slice 222. It adds five tables to the `tick` migration track:
  `tick_request`, `tick_archive_unit`, `tick_definition`, `tick_trade` (a
  hypertable on integer nanosecond time) and `tick_ingest_ledger`.
- It also delivers:
  - the tick vocabulary (`UnitState`, `UNIT_STATES_WITH_FILE`,
    `ARCHIVED_SCHEMAS`, `TICK_TRADE_CHUNK_INTERVAL`);
  - the column contract `data/tick/storage_columns.py` (DBN field → column);
  - the grant artifact's write surface;
  - removal of slice 105's `TickEventType`;
  - contract, slice plan and README updates.
- **It writes no market data** and adds no pass, CLI verb or API route. 223
  and 224 write requests and units, 224 writes definitions; 225 writes ticks and ledger rows.
- Every schema decision is in the LLD. Read Technical Decisions 1–9 and
  "Database / Storage Schema" before starting. Tasks cite them as "TD n".
- Everything is proven on the test cluster. Every destructive statement
  targets a database a fixture or the walkthrough created (`sql.md`).
- Next slice: 223 (tick archive adoption).

**Test environment.** Export `MT_TIMESCALE_TEST_URL` from `.env` with the
quotes stripped. Run mypy on the src kalshi paths, the touched src paths and
the tests in one invocation (a narrower run reports false errors). **Before
every commit step** below, run ruff check and ruff format on that commit's
touched files only, then check `git diff main` for swept pre-existing lines
(review F004); 7.1 is the final sweep, not the first. Run
the unit and integration tiers separately; known pre-existing failures are not
regressions, so re-run a failure in isolation before investigating.

**Import boundary.** Only `data/tick/databento/adapter.py` and `dbn_file.py`
may import `databento` (`test/unit/data/tick/test_import_boundary.py`).
`storage_columns.py` holds field names as strings. Tests read fixtures through
`DbnFileReader`, whose batches are numpy structured arrays.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 0 — Baseline

- [x] **0.1 Confirm the SDK and fixtures have not moved since 220**
  - [x] The slice plan says to re-record fixtures and re-run the preflight if
        the SDK moved. Check `uv.lock`: `databento` is still `0.87.0` and
        `git log -- uv.lock test/fixtures/databento` shows no change after
        220's merge
  - [x] If either moved, STOP and report to the PM; do not re-record here
  - [x] Success: pin and fixtures unchanged, stated in the task notes
  - [x] Effort: 1
  - [x] Note: databento 0.87.0 in uv.lock; uv.lock changes since 220's merge are only the package's own version bumps (0.18.0–0.20.0); no fixture commits since 220.

- [x] **0.2 Record the pre-change test baseline**
  - [x] On the slice branch before any change, run the unit tier, then the
        integration tier, and save the failing test ids to
        `/tmp/222-baseline-unit.txt` and `/tmp/222-baseline-integration.txt`
  - [x] Success: both files exist; every listed failure matches the known
        pre-existing lists in project memory, or is re-run in isolation and
        noted
  - [x] Effort: 1
  - [x] Note: unit tier 0 failures; integration tier 6 failures, all known pre-existing (test_cli_lists priority1 ×2, test_migration_051_052 ×2, test_policy_advances_head unaided ×2).

---

## Section 1 — Vocabulary

- [x] **1.1 Add the storage vocabulary to `data/tick/constants.py`**
  - [x] `UnitState(StrEnum)`, members in lifecycle order: `requested`,
        `submitted`, `delivered`, `downloaded`, `verified`, `ingested`.
        Docstring: furthest step reached, never moves backward; failure is
        `fetch_status`, not a state (LLD State Management, TD4)
  - [x] `UNIT_STATES_WITH_FILE: frozenset[UnitState]` = `downloaded`,
        `verified`, `ingested`
  - [x] `ARCHIVED_SCHEMAS: frozenset[TickSchema] = STORED_TIERS |
        COMPANION_SCHEMAS`. Comment: `mbp-1` stays estimate-only (TD9)
  - [x] `TICK_TRADE_CHUNK_INTERVAL = timedelta(days=7)` with a docstring
        giving TD8's rule (wall-clock span ÷ 1,000–2,000 chunks, journal
        20260719) and that 226 validates it
  - [x] Update the module docstring to name slice 222's additions
  - [x] Success: module imports cleanly; no new import of `databento`
  - [x] Effort: 1

- [x] **1.2 Unit tests for the vocabulary**
  - [x] Extend `test/unit/data/tick/test_constants.py`:
    1. `[s.value for s in UnitState]` equals the six values in order
    2. `UNIT_STATES_WITH_FILE` equals the three members, and each is later in
       `UnitState` order than `delivered`
    3. `ARCHIVED_SCHEMAS` equals `{trades, tbbo, definition}` and excludes
       `mbp-1`
    4. `TICK_TRADE_CHUNK_INTERVAL == timedelta(days=7)`
  - [x] Success: `uv run pytest test/unit/data/tick/test_constants.py -q`
        passes
  - [x] Effort: 1
  - [x] Commit: `feat(tick): add storage vocabulary constants`

---

## Section 2 — Column contract

- [x] **2.1 Create `data/tick/storage_columns.py`**
  - [x] Before writing the maps, print the dtype field names of the `trades`
        v3, `tbbo` v3 and `definition` v3 fixtures (via `DbnFileReader`). If
        any field the LLD names is absent, STOP and report
  - [x] `TICK_TRADE_COLUMNS: Mapping[str, str]`, ordered DBN field → column,
        for every `tick_trade` source field in the LLD table (trades body plus
        the six `tbbo` fields). `length` and `rtype` are not mapped (TD3)
  - [x] `TICK_TRADE_KEY: tuple[str, ...]` = `instrument_id`, `ts_event`,
        `sequence`, `sequence_ordinal` (TD1)
  - [x] `TICK_TRADE_BBO_COLUMNS: tuple[str, ...]`: the six `tbbo` columns, so
        the BBO rule and its tests name them once
  - [x] `TICK_TRADE_DERIVED_COLUMNS` = `sequence_ordinal`, `unit_id`: columns
        with no DBN source (TD1, TD6)
  - [x] `TICK_DEFINITION_COLUMNS: Mapping[str, str]`, DBN field → column, for
        the fields TD5 keeps: `instrument_id`, `activation` → `activation_ns`,
        `expiration` → `expiration_ns`, `raw_symbol`, `asset`, `exchange`,
        `instrument_class`, `security_type`, `cfi`, `currency`,
        `min_price_increment`, `display_factor`, `unit_of_measure`,
        `unit_of_measure_qty`, `contract_multiplier` (the architecture's
        "multiplier"), `ts_recv` → `ts_recv_ns`
  - [x] `TICK_DEFINITION_DERIVED_COLUMNS` = `unit_id`
  - [x] Module docstring: 224 and 225 write through these maps; sentinels are
        kept in `tick_trade` and become `NULL` in `tick_definition` (TD3); the
        conversion itself is the writer's (224/225), not this module's
  - [x] Success: imports cleanly; import-boundary test still passes
  - [x] Effort: 2

- [x] **2.2 Unit tests: column contract against the real DBN layouts**
  - [x] Create `test/unit/data/tick/test_storage_columns.py`
  - [x] Every `TICK_TRADE_COLUMNS` key except the six BBO fields exists in the
        `trades` v2 and v3 fixture dtypes; every key exists in the `tbbo` v3
        dtype
  - [x] Every `TICK_DEFINITION_COLUMNS` key exists in the `definition` v3
        dtype
  - [x] Every trades-body dtype field except `length` and `rtype` is mapped
        (TD3: every other body field is stored)
  - [x] No column name appears twice across a map and its derived tuple
  - [x] Success: the new file passes
  - [x] Effort: 2
  - [x] Commit: `feat(tick): add DBN field to column contract`

---

## Section 3 — Migrations

Each migration is appended to `TICK_MIGRATIONS` in
`market/schema/migrations/tick.py` as a list-of-dicts entry with id
`tick_NNN_<name>`. SQL is idempotent (`IF NOT EXISTS`, `if_not_exists =>
TRUE`, constraints named inside the CREATE). Each migration's comment cites
its TD by number and name. No `GRANT` in any migration (LLD Patterns).
Integration tests go in `test/integration/data/test_tick_storage_track.py`
and use `migrated_tick_db`; if the file passes ~300 lines, split the
constraint cases into `test_tick_storage_constraints.py` and add it to the
walkthrough's step 1 command.

- [x] **3.1 Add the rendering helpers to `tick.py`**
  - [x] A tick-local `_in_list(members)` over an iterable of `StrEnum`
        members, sorted by value, rendered as quoted SQL literals. Copy the
        Kalshi idiom; do not import `migrations/kalshi.py` (LLD Interfaces
        Required)
  - [x] `_check_in(column, members) -> str` returning `CHECK (<column> IN
        (...))`; used for every enum CHECK in TD9's table, `FetchStatus`
        included
  - [x] `_interval_ns(span: timedelta) -> int`, exact integer nanoseconds
        (no float arithmetic)
  - [x] Success: module imports; nothing is appended yet
  - [x] Effort: 1

- [x] **3.2 Unit tests for the rendering helpers**
  - [x] Create `test/unit/market/schema/test_tick_migrations.py`
  - [x] Parametrize over TD9's sources (`ARCHIVED_SCHEMAS`, `SType`,
        `DeliveryMode`, `UnitState`, `FetchStatus`, `UNIT_STATES_WITH_FILE`):
        the rendered list parsed back equals the set of member values
  - [x] `_interval_ns(TICK_TRADE_CHUNK_INTERVAL) == 604_800_000_000_000`
  - [x] Every id after the bootstrap matches `tick_\d{3}_`, and ids are
        unique and ascending (holds vacuously now; guards each later entry)
  - [x] Success: the new file passes
  - [x] Effort: 1

- [x] **3.3 `tick_001_extensions`**
  - [x] `CREATE EXTENSION IF NOT EXISTS timescaledb;` and `CREATE EXTENSION
        IF NOT EXISTS btree_gist;`
  - [x] Success: `migrated_tick_db` builds with the new entry
  - [x] Effort: 1

- [x] **3.4 Integration tests: track shape and `tick_001`**
  - [x] Create `test/integration/data/test_tick_storage_track.py`
  - [x] Both extensions are present in `pg_extension`
  - [x] Applying `TRACKS["tick"]` a second time applies nothing and the
        ledger holds exactly the track's ids (FR1). Extend this assertion as
        each later migration lands
  - [x] Fix 923's tests in `test/integration/data/test_two_database_migrate.py`
        that assume the track is the bootstrap alone:
        `test_apply_tick_writes_only_the_tick_ledger` and
        `test_status_tick_reads_the_tick_ledger` assert
        `[BOOTSTRAP_MIGRATION_ID]`. Derive the expected ids from
        `TRACKS["tick"]` instead, as `test_init_tick_brings_a_bare_database_to_head`
        already does
  - [x] FR1 through the CLI: in `test_init_tick_brings_a_bare_database_to_head`,
        invoke `init --database tick` a second time and assert it applies
        nothing. With the fix above, 923's CLI tests now cover this slice's
        migrations with no manual step
  - [x] Success: both files pass
  - [x] Effort: 1
  - [x] Commit: `feat(tick): add tick_001 extensions migration`

- [x] **3.5 `tick_002_manifest`: `tick_request` and `tick_archive_unit`**
  - [x] Columns, types, keys and notes exactly as the LLD's two tables
  - [x] `tick_request` CHECKs: `schema` from `ARCHIVED_SCHEMAS`, `stype_in`
        from `SType`, `delivery_mode` from `DeliveryMode`,
        `cardinality(symbols) > 0`, `range_end > range_start`, both cost
        columns `>= 0`. `is_adopted` has no default
  - [x] `tick_archive_unit` CHECKs: `state` from `UnitState`, `fetch_status`
        from `FetchStatus`, `(fetch_status = 'UNKNOWN') = (failure_reason IS
        NULL)` with `'UNKNOWN'` rendered from `FetchStatus`, `attempt_count
        >= 0`, `superseded_by_unit_id <> unit_id`, and the file rule: `state`
        not in `UNIT_STATES_WITH_FILE` or all three file columns non-NULL
  - [x] `UNIQUE (request_id, unit_date)`, `repurchase_of_unit_id` UNIQUE, both
        self-FKs, and the FK to `tick_request`
  - [x] Comment cites TD4 and TD9
  - [x] Success: `migrated_tick_db` builds
  - [x] Effort: 3

- [x] **3.6 Test row helpers**
  - [x] Create `test/tick_support/rows.py` with small insert helpers that
        return generated ids: `insert_request(conn, **overrides)` and
        `insert_unit(conn, request_id, **overrides)`, each with a valid
        default row built from the enums (no hand-typed enum strings)
  - [x] Later tasks add `insert_definition` and `insert_ledger_row` here
  - [x] Success: importable from tests
  - [x] Effort: 1

- [x] **3.7 Integration tests: manifest constraints (FR6)**
  - [x] Parametrized per enum CHECK column: every member inserts; one
        non-member is rejected with `CheckViolation`
  - [x] Each of `downloaded`, `verified`, `ingested` without file columns is
        rejected; `delivered` without them inserts
  - [x] `fetch_status = 'UNKNOWN'` with a `failure_reason` is rejected; a
        failed status without one is rejected
  - [x] Empty `symbols`, `range_end <= range_start`, negative cost, a
        negative `attempt_count`, a
        duplicate `(request_id, unit_date)`, a unit superseded by itself and
        a second unit repurchasing the same unit are each rejected
  - [x] Success: the file passes
  - [x] Effort: 3
  - [x] Commit: `feat(tick): add tick_002 manifest migration`
  - [x] Note: at ~394 lines the constraint tests were split: manifest cases in test_tick_manifest_constraints.py, definition/trade/ledger cases in test_tick_storage_constraints.py.

- [x] **3.8 `tick_003_definitions`: `tick_definition`**
  - [x] One column per `TICK_DEFINITION_COLUMNS` value plus `unit_id`, typed
        by TD3's rule from the v3 fixture dtype
  - [x] `NOT NULL`: `instrument_id`, `activation_ns`, `expiration_ns`,
        `ts_recv_ns`, `unit_id`. Every other model field is nullable
        (provider undefined → `NULL`, TD3)
  - [x] PK `(instrument_id, activation_ns)`; `CHECK (expiration_ns >=
        activation_ns)`; the TD5 `EXCLUDE USING gist` constraint; FK
        `unit_id` → `tick_archive_unit`
  - [x] Comment cites TD5
  - [x] Success: `migrated_tick_db` builds
  - [x] Effort: 2

- [x] **3.9 Integration tests: definition windows (FR5)**
  - [x] Add `insert_definition` to `rows.py`
  - [x] Two overlapping windows for one `instrument_id` are rejected
        (`ExclusionViolation`), including windows that touch at one endpoint
        (the range is `'[]'`)
  - [x] Disjoint windows for the same id insert; the same window for two
        different ids inserts
  - [x] `expiration_ns < activation_ns` is rejected; a NULL activation is
        rejected
  - [x] Success: the file passes
  - [x] Effort: 2
  - [x] Commit: `feat(tick): add tick_003 definitions migration`

- [x] **3.10 `tick_004_trades`: `tick_trade` hypertable**
  - [x] Columns and types exactly as the LLD table, in `TICK_TRADE_COLUMNS`
        order followed by `sequence_ordinal` and `unit_id`
  - [x] PK from `TICK_TRADE_KEY`; `sequence_ordinal >= 0`; the BBO CHECK
        rendered from `TICK_TRADE_BBO_COLUMNS` as pairwise `(x IS NULL) =
        (bid_px_00 IS NULL)`
  - [x] No FK on `unit_id` (TD6)
  - [x] `create_hypertable` per the LLD, interval from
        `_interval_ns(TICK_TRADE_CHUNK_INTERVAL)`, `create_default_indexes =>
        FALSE`, `if_not_exists => TRUE`. No compression, no space dimension,
        no other index
  - [x] Comment cites TD1, TD2, TD6 and TD8
  - [x] Success: `migrated_tick_db` builds
  - [x] Effort: 2

- [x] **3.11 Integration tests: trade key, BBO rule, geometry (FR2–FR4)**
  - [x] Two rows equal in every field except `sequence_ordinal` both insert;
        repeating an ordinal raises `UniqueViolation`; a negative ordinal is
        rejected
  - [x] No foreign key exists on `tick_trade` (TD6): `pg_constraint` has no
        `contype = 'f'` row for it, and a row with a `unit_id` matching no
        unit inserts
  - [x] Parametrized over the six BBO columns: a row with only that one NULL
        is rejected; all six NULL and all six set both insert
  - [x] `timescaledb_information.dimensions`: one row, `ts_event`, `bigint`,
        `integer_interval` equal to the rendered constant (read
        `integer_interval`, never `time_interval`)
  - [x] `timescaledb_information.hypertables.compression_enabled` is false;
        `pg_indexes` on `tick_trade` lists only the primary key
  - [x] Success: the file passes
  - [x] Effort: 2
  - [x] Commit: `feat(tick): add tick_004 trades hypertable migration`

- [x] **3.12 `tick_005_ingest_ledger`: `tick_ingest_ledger`**
  - [x] Columns as the LLD table; PK `(unit_id, instrument_id,
        session_date)`; FK `unit_id` → `tick_archive_unit`
  - [x] CHECKs: `record_count >= 0`, `volume >= 0`, each event-time column
        `IS NULL` exactly when `record_count = 0`, `first_event_ns <=
        last_event_ns`. No CHECK on `calendar_id` (TD7)
  - [x] Comment cites TD7
  - [x] Success: `migrated_tick_db` builds
  - [x] Effort: 1

- [x] **3.13 Integration tests: ledger (FR7)**
  - [x] Add `insert_ledger_row` to `rows.py`
  - [x] `record_count = 0` with non-NULL times is rejected; with NULL times it
        inserts
  - [x] `record_count > 0` with a NULL time, `first > last`, a negative count
        or volume, and an unknown `unit_id` are each rejected
  - [x] Success: the file passes; the FR1 re-apply test lists all six ids
  - [x] Effort: 1
  - [x] Commit: `feat(tick): add tick_005 ingest ledger migration`

- [x] **3.14 Column contract parity against the migrated tables**
  - [x] In `test_tick_storage_track.py`, read each table's columns from
        `information_schema.columns`:
    1. `tick_trade` columns equal `TICK_TRADE_COLUMNS` values plus
       `TICK_TRADE_DERIVED_COLUMNS`, in order
    2. `tick_definition` columns equal `TICK_DEFINITION_COLUMNS` values plus
       `TICK_DEFINITION_DERIVED_COLUMNS`
  - [x] Success: passes
  - [x] Effort: 1

- [x] **3.15 Exactness round trip on real records (FR8)**
  - [x] Read every record of the `trades` v3 and `tbbo` v3 fixtures through
        `DbnFileReader`; build rows through `TICK_TRADE_COLUMNS`
  - [x] The test computes `sequence_ordinal` itself (count of earlier records
        with the same triple, keyed by a counter, not by adjacency) and uses
        one real unit's `unit_id`
  - [x] Add one derived row per tier copied from a fixture record with a new
        `sequence`: `size = 4294967295`, and for `tbbo` `bid_px_00 =
        INT64_MAX` (the sentinel is stored as delivered, TD3)
  - [x] Read back ordered by the key and compare every field for equality
        with the source values (one-byte chars decoded to `str`)
  - [x] Success: passes; the test fails if any column narrows a value
  - [x] Effort: 3
  - [x] Commit: `test(tick): add column parity and exactness round trip`

---

## Section 4 — Grant artifact write surface

- [x] **4.1 Enumerate the five tables in `provision_tick_roles.sql`**
  - [x] Replace `-- (none yet)` with one `GRANT SELECT, INSERT, UPDATE,
        DELETE ON <table> TO app_role` per table, generated with `\gexec`
        from `pg_tables` filtered on `schemaname = 'public'` and the five
        names, so the file still applies before the tables exist
  - [x] Update the header comment ("empty until slice 222") and the section
        comment to name the five tables. No TRUNCATE, no sequence grant
  - [x] Success: `psql -f` of the artifact still succeeds on a fresh name
        (covered by the existing privilege fixture)
  - [x] Effort: 1

- [x] **4.2 Privilege tests on `provisioned_tick_db`**
  - [x] Extend `test/integration/data/test_tick_role_privileges.py`. Every
        write rolls back (the fixture is session-scoped)
  - [x] Parametrized over the five tables, as the app role: SELECT, INSERT,
        UPDATE and DELETE succeed; inserts into `tick_request` and
        `tick_archive_unit` draw identity values with no sequence grant
  - [x] As the app role, `TRUNCATE` and one DDL statement per table are
        denied
  - [x] `btree_gist`'s `extowner` is the migrate role, and that role still
        has no attribute beyond LOGIN
  - [x] The artifact's enumerated table names equal the tick database's
        tables minus `schema_migrations` (parse the names from the file;
        read the tables from `pg_tables`), so a new table cannot be left out
  - [x] The existing re-apply test still shows no catalog change
  - [x] Success: the file passes
  - [x] Effort: 2
  - [x] Commit: `feat(tick): grant tick_app DML on the tick tables`
  - [x] Note: 923's test_migrate_role_owns_database_and_installs_timescaledb could no longer CREATE EXTENSION (tick_001 installs it), so it became test_migrate_role_owns_database_and_the_extensions, which reads extowner for timescaledb and btree_gist. The re-apply snapshot now covers the five tick tables' ACLs as well as the ledger.

---

## Section 5 — Remove `TickEventType`

- [x] **5.1 Delete slice 105's leftovers**
  - [x] Delete `src/manta_trading/data/base/tick_schema.py` and
        `test/unit/data/base/test_tick_schema.py`
  - [x] `grep -rn "TickEventType\|data.base.tick_schema" src test docs
        README.md` returns nothing (`_tick_schema` in `dbn_file.py` is a
        different name and stays)
  - [x] Success: the unit tier collects with no import error
  - [x] Effort: 1
  - [x] Commit: `refactor: remove unused TickEventType`

---

## Section 6 — Documents

- [x] **6.1 Data-correctness contract**
  - [x] In `project-documents/user/reference/data-correctness-architecture.md`,
        rewrite the *Archive unit* entry: one UTC day of a request, normally
        one provider file; a day with no file (a provider hole) is still a
        unit (LLD TD4)
  - [x] In the I11 and I12 slice-mapping rows, name 222's part: I11 — the
        manifest tables and the ledger; I12 — `unit_id` on every tick row,
        `superseded_by_unit_id`, and the key that makes overlapping loads
        conflict (TD1)
  - [x] Success: `dateUpdated` bumped; no other entry changed
  - [x] Effort: 1

- [x] **6.2 Slice plan Notes**
  - [x] In `user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md`
        Notes, add "Architecture statements superseded by 222's slice
        design" listing the five items of the LLD's section of that name,
        one line each, with the TD number
  - [x] Success: the five items appear; the `(222)` entry is unchanged
  - [x] Effort: 1

- [x] **6.3 Migrations README, README, CHANGELOG**
  - [x] `src/manta_trading/market/schema/migrations/README.md`: the tick
        track's description names `tick_001`–`tick_005` and the five tables
  - [x] `README.md` "Futures tick data": a short storage paragraph (five
        tables, nanosecond integer time, no data written until 224/225) and
        update the sentence saying the tick database arrives later
  - [x] `CHANGELOG.md` `[Unreleased]`: an Added entry for the tick storage
        schema and a Removed entry for `TickEventType`, user-facing wording
  - [x] Success: all three updated
  - [x] Effort: 1
  - [x] Commit: `docs: record tick storage track in contract, plan and readmes`

---

## Section 7 — Validation

- [x] **7.1 Lint and type check**
  - [x] ruff check and ruff format on touched files only; `git diff main`
        shows no swept pre-existing lines
  - [x] mypy per the test environment note, over every touched src and test
        file
  - [x] Success: both clean
  - [x] Effort: 1

- [x] **7.2 Full tiers**
  - [x] Run the unit tier, then the integration tier
  - [x] Compare failures with the Section 0 baseline files; any new failure is
        re-run in isolation, then fixed
  - [x] Success: no failure outside the baseline
  - [x] Effort: 2
  - [x] Note: unit 3988 passed, 0 failed; integration 613 passed, 6 failed — failure list identical to the Section 0 baseline.

- [x] **7.3 Run the verification walkthrough**
  - [x] Run LLD Verification Walkthrough steps 1–5, including the scratch
        database teardown in step 5
  - [x] Record actual outputs in the LLD's walkthrough, correcting any
        command or expected value that differed
  - [x] Success: every step matches its expected result; the scratch
        database no longer exists
  - [x] Effort: 2
  - [x] Commit: `docs: record slice 222 verification walkthrough`
  - [x] Note: step 1's single pytest command fails at collection (test/unit/data and test/integration/data both import as package 'data'); the LLD walkthrough now uses two invocations and lists both constraint files. Steps 2–5 matched as written.
