---
title: "156 — Cold-start integrity: restore working empty-DB → working-DB path"
slice: 156
initiative: 140
status: complete
phase: 6
type: fix
effort: 3
tags: [migrations, cold-start, cli, ci, prod-cutover]
created: 20260508
dateCreated: 20260508
dateUpdated: 20260509
revision: cold-start-defect-followup-daemon-heartbeat
author: pm+claude
docType: slice-design
project: trading
dependsOn: []
relatedIssues: [16, 17]
---

# Slice 156 — Cold-start integrity

> Scope note: this slice fixes the broken cold-start path so a fresh
> Postgres database can be brought to a working state by a single
> documented command, AND folds `timescale_init.py` into the
> migration chain so the migration list becomes the single source
> of truth for schema. Both pieces are bundled because shipping the
> immediate fix without the fold leaves the foundation sketchy:
> the next deletion of a CREATE migration recreates the bug, and
> the integration test landing alongside this slice is the right
> safety net for the fold's destructive verification. Backup
> posture (slice 157) and prod cutover proper come after.

## Source

- Issue [#16](https://github.com/manta-digital/trading/issues/16) —
  filed 2026-05-08 when prod cutover attempt to the empty `trading` DB
  on 144 hit the failure.
- Slice plan entry: `140-slices.data-quality-operations.md` item 16
  ("(156) Cold-start integrity").

## Problem statement

A fresh empty Postgres database cannot be brought to a working state.
Today's path requires two undocumented commands and still fails:

```bash
# Step 1 — works: extension + minute_ohlcv hypertable
MT_TIMESCALE_DB_URL=postgresql://.../trading_clean \
  python -m manta_trading.market.timescale_init

# Step 2 — fails on migration 019:
#   UndefinedTable: relation "acquisition_state" does not exist
MT_TIMESCALE_DB_URL=postgresql://.../trading_clean \
  mt data migrate apply
```

Three independent defects compound into the bug:

1. **Missing CREATE.** Migrations 019 (`slim_acquisition_state`),
   022 (`acquisition_state_outcome_check`), and 030
   (`drop_adj_columns_daily_ohlcv` — also drops a column from
   `acquisition_state`) all reference `acquisition_state`, but no
   migration in the current chain creates it. The view created in 021
   (`data_status_view`) also reads from it. A grep of `src/` for
   `CREATE TABLE.*acquisition_state` returns zero hits. The original
   CREATE migration was deleted (likely during slice 152's demolition)
   without folding the create into a surviving prerequisite.

2. **No documented entry point.** Slice 154 deleted the
   `mt data migrate-cold-start` CLI command without replacement.
   Today the operator must know about `timescale_init` (a script
   invoked via `python -m`, never advertised in `mt data --help`)
   *and* `mt data migrate apply`, in that order, with environment
   variable carried between them.

3. **No CI gate.** Every dev DB and `trading_test` were created
   incrementally over months and accumulated `acquisition_state` from
   a since-deleted migration. The first attempt at a fresh DB
   (2026-05-08) was the first signal. Test suite never exercised the
   empty-DB → working-DB path.

## Audit results

I audited every `CREATE TABLE` / `CREATE VIEW` /
`CREATE MATERIALIZED VIEW` / `create_hypertable` in
`src/manta_trading/market/`:

**Created in `timescale_init.py`:** `minute_ohlcv` (table +
hypertable).

**Created in MINUTE_MIGRATIONS (in order):**
`schema_migrations` (001), `instruments` (002),
`provider_symbol_mapping` (003), `trading_calendars` (004),
`trading_holidays` (005), `coverage_gaps` (012, dropped in 020),
`backfill_state` (013), `data_gaps` (018), `data_status` view (021),
`daily_ohlcv` (023, table + hypertable), `trading_sessions` (025),
`splits` (029), `dividends` (029), and 7 cagg materialized views
(033–034).

**Referenced but never created:** `acquisition_state`. **One** missing
table. The audit is small because the chain is otherwise self-contained.

## Approach

Four pieces, sequenced so each can land cleanly. The first three
deliver the immediate fix; the fourth folds `timescale_init.py` into
the migration chain. All four ship in this slice — see "Why all four
in one slice" below.

1. **Fixup migration `038_create_acquisition_state`** — restores the
   missing CREATE. Idempotent (`CREATE TABLE IF NOT EXISTS`) with the
   final post-019 / post-022 / post-030 column shape. Existing DBs
   that already have the table get a no-op. Fresh DBs get the table
   before migrations 019 / 021 / 022 / 030 run their ALTERs and
   references.

2. **Restored `mt data init` command** — single documented entry point.
   After piece 4 lands, this command's only job is `apply_schema_migrations`
   plus a friendly status table. (Pre-fold, it would have wrapped both
   `timescale_init.initialize_database` and `apply_schema_migrations`;
   post-fold, all of init's work is in the migration chain itself, so
   there is nothing for `mt data init` to do beyond running migrations
   against a database that has the `timescaledb` extension installable.)

3. **Cold-start integration test** — a pytest fixture that creates an
   ephemeral Postgres database, runs `mt data init` against it,
   asserts the resulting schema matches an expected manifest. Gated
   in CI so any future deletion of a CREATE migration (or
   modification of init-equivalent migrations) fails loudly. This
   test is also the verification mechanism for piece 4.

4. **Fold `timescale_init.py` into the migration chain.** Replace the
   `python -m manta_trading.market.timescale_init` script with three
   to four new migrations at the *front* of the list (placed before
   migration 002, which references `instruments`):
   - `001a_create_timescaledb_extension` — `CREATE EXTENSION IF NOT
     EXISTS timescaledb`. Idempotent.
   - `001b_create_minute_ohlcv` — `CREATE TABLE IF NOT EXISTS
     minute_ohlcv (...)` with the same column shape as today's init
     script.
   - `001c_create_minute_ohlcv_hypertable` — `SELECT
     create_hypertable('minute_ohlcv', 'time', if_not_exists =>
     TRUE, ...)`. The `if_not_exists` argument makes the call
     idempotent against an already-converted table.
   - `001d_create_minute_ohlcv_indexes` — index definitions from
     init's `create_indexes` method, all `CREATE INDEX IF NOT
     EXISTS`.

   Compression and retention policies in init are skipped here; they
   are environment-specific (Timescale community edition rejects
   them) and slice 154's caggs already handle the rollup story for
   minute data. If we want them later, they go in their own
   migration.

   After this lands, `timescale_init.py` is deleted entirely.
   Migration runner becomes the single source of schema truth.

### Why all four in one slice

Pieces 1, 2, 3 are the minimum to unblock prod cutover. Piece 4 is
the structural fix. Bundling them is the right call because:

- The integration test (piece 3) is exactly what's needed to verify
  piece 4 — it asserts the end-state schema matches expectation. It
  does not matter to the test whether the schema was produced by
  init+migrations or by migrations alone; it asserts the *result*.
  Building the test once, for both purposes, costs less than building
  it twice.
- Without piece 4, the cold-start path is "fixed" but the foundation
  stays brittle: any future deletion of a CREATE migration in the
  chain recreates the bug class. Slice 156 was filed precisely
  because that brittleness manifested. Fixing the symptom and not
  the cause is half-work.
- Piece 4 is mechanical once the integration test exists. The risk
  is **not** schema-correctness (the test catches that); the risk is
  applying piece 4's idempotent migrations against `trading_test`
  and seeing a non-zero diff. We mitigate that with a dry-run path:
  after piece 4 is implemented, run `mt data migrate status` against
  `trading_test` first — the new `001a/b/c/d` migrations should show
  as `pending`. Run `mt data migrate apply` and confirm they each
  produce a `0 rows affected` (or equivalent) result and that no
  table data changed. The integration test then proves a *fresh* DB
  also lands at the same end state.

### Why a fixup migration, not editing existing migrations

Editing an existing migration's SQL invalidates it on every DB that
already applied it. `trading_test` is past migration 037; mutating
019's SQL to "create-then-alter" would not re-run 019 there, and the
DB would silently diverge from what the migration list says. Fixup
migrations are append-only and idempotent — the only safe pattern
once a migration is deployed.

### Why not put `acquisition_state` in `timescale_init.py`

`timescale_init.py` is going away in piece 4. Adding more CREATEs to
it would just be more work to undo. The migration chain becomes the
single source of truth.

### Migration 038 column shape

The current `acquisition_state` (verified against `trading_test`):

```sql
CREATE TABLE IF NOT EXISTS acquisition_state (
    symbol               text NOT NULL,
    granularity          text NOT NULL,
    provider             text NOT NULL,
    last_attempt_ts      timestamptz,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    last_attempt_outcome text,
    PRIMARY KEY (symbol, granularity, provider)
);
```

This matches the post-030 state of an existing DB. Migrations 019,
022, and 030 will then run as no-ops on a fresh DB:

- 019's `DROP COLUMN IF EXISTS last_success_ts, retry_count,
  error_message, run_id, status` — all already absent. `ADD COLUMN
  IF NOT EXISTS last_attempt_outcome / last_adjusted_ca_snapshot_id`
  — `last_attempt_outcome` already present from 038, so no-op;
  `last_adjusted_ca_snapshot_id` gets added.
- 022's CHECK constraint addition — runs against the
  `last_attempt_outcome` column from 038. Works.
- 030's `DROP COLUMN IF EXISTS last_adjusted_ca_snapshot_id` — drops
  the column 019 just added. Net: gone. Matches `trading_test`.

The fixup migration is **placed at id 038** (after the most recent
migration `037_widen_minute_cagg_refresh_offsets`). Migration runners
are id-ordered, and 038 must run *before* the slimming/altering
migrations. Since IDs in the existing chain are not numerically
contiguous with timeline (cf. missing 027), placement must be by
content order, not numeric order. **Decision:** the runner already
applies in list-iteration order, not by numeric sort of the id. The
new entry must be inserted into the `MINUTE_MIGRATIONS` list at a
position that comes *before* the first migration referencing
`acquisition_state` (which is `019_slim_acquisition_state`).

Because we cannot rename existing migrations safely, we insert
`038_create_acquisition_state_fixup` immediately before the
`019_slim_acquisition_state` entry in the list. The id `038` is
numerically larger than `019` but the runner iterates the list in
order, so insertion-point placement is what matters. Existing DBs
have already run 019, so the fixup's `IF NOT EXISTS` makes it a
no-op there; fresh DBs run 038 before 019 and 019 finds the table
ready.

A short comment in `MINUTE_MIGRATIONS` documents this convention so
the next maintainer doesn't reorder the list alphabetically by id.

## Components

### `src/manta_trading/market/schema/migrations/minute.py`

Add one entry to `MINUTE_MIGRATIONS`. Position: immediately before
the existing `019_slim_acquisition_state` entry. Id:
`038_create_acquisition_state_fixup`.

### `src/manta_trading/cli/commands/data.py`

New `init` Typer command on the `data_app`. Calls
`db.apply_schema_migrations()` on a fresh `TimescaleMinuteDataDB`
and prints a status row. After piece 4 lands, that single call is
sufficient: extension creation, hypertable creation, and index
creation are all migrations now.

Flags:
- `--validate-only` — run `mt data migrate status` and exit.
- `--yes` — currently a no-op (no destructive operations in the new
  init path). Reserved for future destructive flags.

Output: number of migrations applied and resulting schema-migrations
row count.

### `src/manta_trading/market/timescale_init.py`

**Deleted** in piece 4. Its work moves to migrations 001a/b/c/d.

Tests that imported `TimescaleDBInitializer` (if any) update to
exercise the migration path instead. Audit before deletion to confirm
no external callers remain.

### `test/integration/test_cold_start.py` (new)

Pytest fixture pattern:

1. Skip whole module if `MT_TIMESCALE_TEST_URL` env var is unset (no
   ambient assumption of available Timescale instance).
2. Per-test: create a UUID-named database via a postgres-admin
   connection to the same host. Drop it on teardown.
3. Run `mt data init --yes` against the new DB via `subprocess` (or
   in-process if simpler — verify the CLI codepath end-to-end).
4. Assert a schema manifest:
   - All 13 tables present.
   - All 7 cagg materialized views present.
   - All 7 cagg refresh policies installed (per slice 154 + 155).
   - `data_status` view returns rows (LEFT JOINs work even on empty
     instruments).
   - `schema_migrations` row count equals `len(MINUTE_MIGRATIONS) +
     init.tables_count` — exact accounting.
5. Negative test: a separate parametrized test deletes one CREATE
   migration from a copy of the list, runs init, asserts a clear
   error.

CI integration: `pytest -m integration` with the env var set in CI's
test-database service (a Timescale container). Local runs without
the env skip; this is acceptable because the test is gated by
infrastructure availability, not by code being incomplete.

## Cross-slice dependencies and interfaces

- **Independent of all other slices.** No `dependsOn`. Issue #16 is
  blocking prod cutover (slice 155 deployment) but that is a
  scheduling dependency, not a code dependency.
- **Slice 155 (daemon-as-service)** consumes the working init path
  for production setup. Slice 155's documentation will reference
  `mt data init` as the bootstrap command.
- **Slice 157 (backup, future)** consumes a known-good cold-start
  baseline. Backup-and-restore can only be validated against a
  reproducible empty-to-working transition.

## Success criteria

1. **Fresh DB cold-start works.** `createdb trading_clean &&
   MT_TIMESCALE_DB_URL=...trading_clean mt data init --yes` produces
   a DB where `mt data migrate status` shows all migrations applied
   and `mt data caggs status` shows all 7 caggs with policies
   installed. Zero manual intervention.
2. **Existing DBs unaffected.** Running `mt data migrate apply`
   against `trading_test` after this slice lands applies the new
   piece-4 migrations (001a/b/c/d) idempotently with no table data
   mutated and no schema diff in the existing tables. Re-running it
   reports 0 applied.
3. **Integration test exists and is runnable.** A new
   `test_cold_start.py` integration test exists, runs locally with a
   documented `MT_TIMESCALE_TEST_URL`, and passes against an
   ephemeral DB. Deleting a CREATE migration from the list (in a
   deliberate regression) causes the test to fail with a clear
   assertion. CI gating is intentionally deferred — this repo has no
   CI yet (issue #17 tracks the bootstrap). Once CI exists, wiring this
   test in is a one-step change.
4. **Single source of schema truth.** `timescale_init.py` is deleted.
   `python -m manta_trading.market.timescale_init` no longer works
   (and isn't needed). All schema is reproduced by running the
   `MINUTE_MIGRATIONS` list against an empty DB.
5. **`mt data init` is the documented entry point.** README and any
   prod-cutover runbook reference `mt data init`.

## Verification walkthrough

> **Status:** Executed and verified during slice 156 implementation
> on 2026-05-09 against the trading_test (existing) and trading
> (cold-start) databases on <db-host>. Observed counts and
> commands below reflect the real run; substitute your own DB host
> as appropriate.

### A. The bug, before this slice (reproduction)

This is a historical reproduction; once slice 156 lands, the bug no
longer reproduces because the init-fold migrations 001a/b/c/d run
first and migration 038 restores acquisition_state before 019.

```bash
# On a host that can reach the test Timescale instance
PGPASSWORD=manta createdb -h <db-host> -U postgres trading_repro_before
MT_TIMESCALE_DB_URL=postgresql://postgres:<password>@<db-host>:5432/trading_repro_before \
  uv run mt data migrate apply
# PRE-156 expected output: fails on migration 019 with
#   UndefinedTable: relation "acquisition_state" does not exist
# POST-156 expected output: succeeds; 41 migrations applied.
PGPASSWORD=manta dropdb -h <db-host> -U postgres trading_repro_before
```

### B. After this slice — fresh DB success

Verified end-to-end on the prod `trading` DB on 144 (T22):

```bash
PGPASSWORD=manta dropdb -h <db-host> -U postgres trading --if-exists
PGPASSWORD=manta createdb -h <db-host> -U postgres trading
export MT_TIMESCALE_DB_URL=postgresql://postgres:<password>@<db-host>:5432/trading

uv run mt data init
# Observed:
#   data init
#   ┃ Metric            ┃ Count ┃
#   │ Applied this run  │ 40    │
#   │ Total applied     │ 41    │
#   │ Pending remaining │ 0     │
#
# Note: "Applied this run" is 40 (not 41) because the runner bootstraps
# 001_schema_migrations out-of-band before the apply loop starts and
# does not include it in the returned list. The "Total applied" of 41
# is the source of truth.

uv run mt data migrate status
# Observed: "41 applied, 0 pending"

uv run mt data caggs status
# Observed: 7 caggs, all "policy: yes", all "Status: Success".
# "Mat Latest" and "Lag" both render "—" because the source tables are
# empty — this is the correct behavior for an empty cold-start DB
# (slice 156 also fixed a pre-existing crash on this exact path; see
# bcef420 fix(cli): caggs status — handle BC sentinel).
```

### C. After this slice — existing DB unaffected (T05, T11)

Verified against the in-use `trading_test` DB:

```bash
export MT_TIMESCALE_DB_URL=postgresql://postgres:<password>@<db-host>:5432/trading_test
uv run mt data migrate apply
# Observed (5 new migrations 001a/b/c/d + 038): all reported applied.
uv run mt data migrate apply
# Re-run: "0 migration(s) applied" — idempotent.

# Schema parity:
PGPASSWORD=manta pg_dump -h <db-host> -U postgres --schema-only \
  --no-owner --no-privileges trading_test > before.sql
# (apply slice 156 migrations)
PGPASSWORD=manta pg_dump -h <db-host> -U postgres --schema-only \
  --no-owner --no-privileges trading_test > after.sql
diff before.sql after.sql
# Observed: empty diff.

# Row count on minute_ohlcv:
PGPASSWORD=manta psql -h <db-host> -U postgres -d trading_test \
  -c "SELECT count(*) FROM minute_ohlcv;"
# Observed: 26,864,856 before AND after — unchanged.
```

### D. Integration tests (T20)

```bash
export MT_TIMESCALE_TEST_URL=postgresql://postgres:<password>@<db-host>:5432/postgres
uv run --extra dev pytest test/integration/test_cold_start.py -v
# Observed: 2 passed in 3.07s
#   - test_apply_migrations_brings_schema_to_current
#   - test_removing_038_breaks_019
# Cleanup verified: 0 mt_test_* databases remain after the run.
```

`MT_TIMESCALE_TEST_URL` must point at an *admin* connection (e.g. the
maintenance `postgres` database) because the fixture CREATEs and DROPs
throwaway UUID-named databases per test. Tests skip cleanly when the
variable is unset.

CI wiring is deferred to issue #17 (no CI exists in the repo yet).

### E. Prod cutover (the workflow this slice unblocks)

The prod `trading` DB on 144 is initialized via §B above. The remaining
data-load steps are not part of slice 156's scope:

```bash
# Already done by §B
# uv run mt data init

uv run mt data instruments rebuild
uv run mt data pull 1d --list priority1
uv run mt data caggs refresh
# Expected: prod DB at parity with test DB for priority1 daily data.
```

### F. Schema parity vs trading_test (T23)

13 user tables created by the migration chain were compared
column-by-column between `trading` (post-init) and `trading_test`:

| Result | Tables |
|---|---|
| OK (byte-identical column shapes) | acquisition_state, backfill_state, daily_ohlcv, data_gaps, dividends, instruments, minute_ohlcv, provider_symbol_mapping, schema_migrations, splits, trading_calendars, trading_holidays, trading_sessions |

`trading_test` originally contained four extra tables not produced by
any migration: `daemon_heartbeat`, `minute_collection_events`,
`minute_collection_jobs`, `symbol_lists`. Of these, `daemon_heartbeat`
turned out to be a *real* table referenced by `HeartbeatStore` whose
CREATE had never been folded into the migration chain (cold-start
defect surfaced post-T23). The other three were genuine orphans (zero
references in src/, zero rows).

### G. daemon_heartbeat fold-in (slice 156 follow-up)

Defect surfaced after T23: cold-start would produce a working schema
for the data layer but the daemon would crash on first heartbeat write
because `daemon_heartbeat` was missing. Fix:

- Added migration `039_create_daemon_heartbeat` (idempotent
  `CREATE TABLE IF NOT EXISTS`, column shape matched verbatim against
  trading_test and `HeartbeatStore.upsert()`).
- Extended the cold-start integration test's `EXPECTED_TABLES` manifest
  to include `daemon_heartbeat` so this regression class is caught
  next time.
- Dropped the three real orphans
  (`minute_collection_events`, `minute_collection_jobs`,
  `symbol_lists`) from `trading_test`. trading does not have them.
- Deleted stale `test/integration/test_migrate_cold_start_cli.py`
  (referenced the slice-154-deleted `mt data migrate-cold-start`).
- Re-ran `mt data init` against prod `trading`: 1 migration applied
  (039), 42 total. `\d daemon_heartbeat` confirms shape parity.

### H. Migration 036 psycopg3 fix (second slice 156 follow-up)

A second latent defect surfaced when re-running the cold-start
integration test on a developer host with `MT_MARKET_DB_URL` set in
.env (loaded by `uv run`). Migration 036
(`_copy_splits_dividends_from_marketdb`) called
`Connection.executemany`, which is a psycopg2-only API — psycopg3
raises `AttributeError: 'Connection' object has no attribute
'executemany'`. Never triggered in normal cold-start runs because
prior runs had the env var unset (the migration's no-op fast path).

Fix:
- Migration 036 now uses `with conn.cursor() as cur:
  cur.executemany(...)`. Verified end-to-end against the live
  MarketDB on <prototype-host> (6,070 splits + 294,067 dividends copied
  in 8.26s).
- The cold-start integration test added an autouse fixture
  `_isolate_marketdb_env` that forcibly deletes `MT_MARKET_DB_URL`
  before each test. The test stays hermetic regardless of the
  developer's shell env or .env contents.
- A new opt-in test `TestMigration036WithMarketDB` is gated on
  `MT_MARKET_DB_URL_FOR_COLD_START_TEST` (deliberately a different
  variable name from `MT_MARKET_DB_URL` so it can't be set by
  accident); it exercises the live-MarketDB path so the
  psycopg2-vs-psycopg3 regression class is caught next time.

Operational consequence on prod `trading`: migration 036 had been
recorded as "applied" with 0 splits / 0 dividends because the prior
init run silently took the no-op path. Re-applied 036 explicitly
with `MT_MARKET_DB_URL` set; trading now has 6,070 splits + 294,067
dividends matching MarketDB.

## Risks

- **Insertion order in `MINUTE_MIGRATIONS` list is load-bearing and
  not enforced by code.** A future maintainer who sorts the list
  alphabetically by id breaks the cold-start path again. Mitigated
  by: (a) inline comment marking the fixup's required position,
  (b) the integration test catches reorder-induced regressions
  immediately. Risk does not warrant a runtime check (overengineered
  for a one-time situation).
- **Piece 4 idempotency on `trading_test`.** The new 001a/b/c/d
  migrations run against a DB that already has the extension,
  hypertable, and indexes from the original `timescale_init` run.
  All four use `IF NOT EXISTS` (or Timescale's `if_not_exists =>
  TRUE`), so they should be no-ops. Mitigated by: (a) explicit
  pre-flight `mt data migrate status` and a `pg_dump --schema-only`
  diff before/after on `trading_test`; (b) integration test on a
  fresh DB confirms end-state schema parity.
- **Audit may have missed something not surfaced by 019.** The audit
  was textual (`grep CREATE TABLE`), not by simulated execution. The
  integration test is the actual safety net — if anything else is
  missing, it surfaces there. We accept this trade-off rather than
  designing a more exhaustive static audit.

## Future work

- **Schema-snapshot regression test.** Beyond the existence
  manifest, snapshot the full `pg_dump --schema-only` output of a
  cold-started DB and diff against committed expected snapshot.
  Catches drift between migration intent and resulting schema.
  Current integration test covers the regression class this slice
  was filed to prevent; snapshot diff is a stronger guard for the
  future. Add when measurement justifies it.

## Effort

3/5. Pieces 1 (fixup migration), 2 (`mt data init`), and 3
(integration test) are mechanical and small. Piece 4 (fold
`timescale_init.py` into migrations + delete) is also mechanical
but carries the only real verification work in the slice — running
the new migrations against `trading_test` and confirming a clean
no-op diff. No design ambiguity remains after this document.
