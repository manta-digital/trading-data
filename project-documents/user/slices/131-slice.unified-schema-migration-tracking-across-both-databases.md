---
docType: slice-design
slice: 131
parent: user/architecture/120-slices.data-acquisition.md
project: trading
dateCreated: 20260418
dateUpdated: 20260502
status: complete
renumberedFrom: 150
---

# Slice 131 — Unified Schema Migration Tracking Across Both Databases

## Purpose

Bring both databases in the trading project — the daily-OHLCV PostgreSQL DB (`MarketDB`) and the TimescaleDB minute DB (`TimescaleMinuteDataDB`) — under one uniform schema-migration framework that already exists for the minute DB, and make the framework's state visible via CLI. Retire the orphaned on-disk SQL migration files that are not part of any tracking system and have been a recurring source of confusion.

This slice is a **prerequisite for slice 141** (which wants to add a `010_trading_sessions` migration to the minute-DB track) and for any future daily-DB schema work.

## Motivation / Problem

Today's state is asymmetric and partially unmanaged:

- **Minute DB:** has a `schema_migrations` tracking table and a Python-defined migration list at [src/manta_trading/market/schema/migrations.py](src/manta_trading/market/schema/migrations.py). Migrations 001–009 applied against `trading_test`. Runner lives on `TimescaleMinuteDataDB.apply_schema_migrations()`. Exposed as `mt data migrate`.
- **Daily DB:** no `schema_migrations` table, no migration list, no runner. `mt data daily migrate` exists but only calls `MarketDB.verifyDatabase()` — it is a verifier, not a migrator. Schema drifted in ad-hoc.
- **Orphaned on-disk SQL files:** [database/migrations/*.sql](database/migrations/) (025, 750, 760, 770, 780) and [sql/01_setup_database.sql](sql/01_setup_database.sql) are not read by any runner and are not tracked anywhere. They are remnants of archived work (Slice 750, etc.) and have actively misled recent sessions into believing `trading_sessions`-class tables were already defined or applied when they were not.
- **No status visibility:** there is no command that answers "what migrations are applied on which DB?" Each session has had to re-derive this from live SQL queries.

The result: schema state drifts, archived artifacts masquerade as live design, and slice planning has to litigate ground truth on every turn. This slice ends that class of problem.

## Scope

**In scope:**

1. Extend the existing Python-defined migration framework to operate on **multiple named database tracks** — initially `minute` and `daily` — each with its own migration list and its own `schema_migrations` table on that DB.
2. Add a daily-DB migration track with an initial set of tracking-only migrations that record the current daily schema as already-applied (no-op SQL), so the live daily DB joins the managed regime without any behavior change.
3. Extend the CLI:
   - `mt data migrate` gains `--db {minute|daily|all}` (default `all`).
   - New subcommand `mt data migrate status` showing, per DB: applied migration IDs with descriptions and applied-at timestamps, and pending migration IDs with descriptions.
   - Retire the misleading `mt data daily migrate` (verifier) — or repurpose it to call the new unified migrator. Decision: **repurpose**, so muscle memory doesn't break.
4. Delete or archive the orphaned on-disk SQL files.
5. Short `README.md` in the migrations module stating the single-source-of-truth rule.

**Explicitly out of scope:**

- No new schema changes. No `trading_sessions` table. No 140 application logic. Anything that looks like schema design belongs to slice 141 or later.
- No production-DB migrations. All work is against the `_test` DBs. Production runs are an operator task tracked separately.
- No new migration formats (YAML, SQL files discovered via glob, etc.). The Python-dict format in `migrations.py` is kept as-is.
- No rollback / "down" migrations. The existing framework has none; adding them is out of scope.

## Technical Decisions

### D1. Track identity and module layout

Today's migrations module is `src/manta_trading/market/schema/migrations.py` — singular, implicitly "the minute DB migrations." Promote this to a package:

```
src/manta_trading/market/schema/
    __init__.py
    seed_calendar.py            (unchanged)
    migrations/
        __init__.py             (re-exports tracks)
        minute.py               (existing MIGRATIONS list, renamed)
        daily.py                (new)
        README.md               (single-source-of-truth note)
```

Each track module exposes a module-level constant `MIGRATIONS: list[dict[str, str]]` with the same shape as today. A top-level `TRACKS: dict[str, list[dict[str, str]]]` in `migrations/__init__.py` maps track name → migration list.

**Why:** minimal disruption. The existing shape of each migration entry is unchanged. Consumers import `TRACKS["minute"]` instead of `MIGRATIONS`, but no entry structure churn.

### D2. Runner location

The existing runner is a method on `TimescaleMinuteDataDB`. That coupling made sense when there was only one track, but it hard-codes "the minute DB" into the runner. Extract the runner into a plain function:

```python
# src/manta_trading/market/schema/runner.py
def apply_migrations(pool: ConnectionPool, migrations: list[dict]) -> list[str]: ...
def list_migration_state(pool: ConnectionPool, migrations: list[dict]) -> dict: ...
```

Both DB classes expose thin wrappers that call the runner with the right `(pool, migrations)` pair. `TimescaleMinuteDataDB.apply_schema_migrations()` keeps its signature and becomes a one-liner delegating to the runner — no caller-visible change.

`MarketDB` gains an analogous `apply_schema_migrations()` method with the same signature and contract.

**Why:** keeps the "runner knows nothing about which DB" property that makes this scalable; keeps existing `TimescaleMinuteDataDB` callers working; lets `MarketDB` join without duplication.

### D3. Bootstrap handling

The minute runner today does a conditional "does `schema_migrations` exist? if not, run `001_schema_migrations` and record it" bootstrap. This behavior is preserved in the extracted runner. The daily track's first migration is also named `001_schema_migrations` (identical SQL) and the runner bootstraps it the same way.

**Why:** the bootstrap-before-record asymmetry is a necessary consequence of the tracking table being self-creating. Preserving the existing implementation avoids re-validating a subtle piece of SQL.

### D4. Daily-track initial migrations: reconcile, don't rewrite

The daily DB already has its tables (symbol_list, daily OHLCV tables, whatever MarketDB creates). We do **not** drop and recreate to match a Python-defined schema. Instead:

- `001_schema_migrations` — create the tracking table (first-run bootstraps, existing DBs idempotent).
- `002_reconcile_existing_schema` — empty/no-op SQL (`SELECT 1;` or equivalent). Description: `Reconciliation marker: schema inherited from pre-migration state`. Its only job is to exist as a tracking row so the baseline is recorded. Future daily migrations start at `003_*`.

A one-time reconciliation step (documented in the README and not automated) tells the operator: "Run `mt data migrate --db daily` against each live daily DB exactly once. It applies 001 and 002, both no-ops against the tracking layer, and from then on the DB is managed." No live data is touched.

**Why:** the daily schema's current state is what MarketDB's code assumes. Expressing it as a Python-defined reset would risk introducing a diff we don't want. Reconciliation-by-declaration is honest: the baseline is "whatever is there today"; everything additive goes through the framework from this point forward.

**Deliberately deferred:** writing Python migrations that describe the current daily schema declaratively. If the daily DB ever needs to be stood up from empty, that's a separate slice — and at that point the reconciliation entry would be retro-filled with real SQL. For today's needs (both live DBs are populated) it is not required.

### D5. CLI surface

Add:

- `mt data migrate [--db minute|daily|all] [--json]` — apply pending migrations. Default `--db all`. Prints per-DB applied lists and a summary line.
- `mt data migrate status [--db minute|daily|all] [--json]` — show, per DB, applied migrations (id, description, applied_at) and pending migrations (id, description). Rich table for humans; structured JSON with the same content for machines.

Change:

- `mt data daily migrate` (which currently runs `verifyDatabase`): repurpose to call the new unified migrator for `--db daily`. Document it as an alias. The `verifyDatabase()` path becomes `mt data daily verify` (new, one-liner), preserving behavior under a clearer name. If that rename feels out of scope, defer the rename and leave `daily migrate` with its old semantics flagged as deprecated — but the `mt data migrate --db daily` path is authoritative.

**Decision:** repurpose, rename verify. Small, contained, avoids leaving a trap.

### D6. What to do with orphaned SQL files

The files in `database/migrations/*.sql` and `sql/01_setup_database.sql` are not referenced by any code path in the current codebase (verified via grep during slice prep). They are safe to remove.

Choice:
- **Delete outright.** Git history preserves them. Cleanest, fewest future stumbles.
- **Move to `archive/`.** Keeps a browsable breadcrumb but risks re-confusing someone who finds them.

**Decision: delete**, with the commit message making archive-in-git explicit. A short note in the new `migrations/README.md` mentions they existed and were retired, pointing readers at the commit hash if they need context.

### D7. Status output shape

`mt data migrate status --json` emits:

```json
{
  "tracks": {
    "minute": {
      "connected": true,
      "applied": [
        {"id": "001_schema_migrations", "description": "...", "applied_at": "2026-04-03T05:57:11.606618+00:00"},
        ...
      ],
      "pending": [
        {"id": "010_trading_sessions", "description": "..."}
      ]
    },
    "daily": { ... }
  }
}
```

`connected: false` plus an `error` string if the DB URL is not configured or the connection fails — does not fail the whole command.

Rich rendering: one table per track, columns `ID | Status | Description | Applied At`.

### D8. Error handling and idempotency

Unchanged from existing framework:
- All migration SQL must be idempotent (`IF NOT EXISTS`, `ON CONFLICT DO NOTHING`, `DO $$ ... END $$` guards). Enforced by convention and review, not statically.
- Each migration runs in its own transaction (existing behavior in `apply_schema_migrations`).
- Any SQL error aborts the run and surfaces the failing migration ID.

### D9. Config and connection plumbing

No config changes. `Settings.market_db_url` and `Settings.timescale_db_url` already exist. The unified `mt data migrate` resolves both from the current `Settings` instance. If one URL is unset, the corresponding track is skipped with a clear message (not an error when `--db all`; an error when that specific `--db` was requested).

## Data Flows

### Flow A: Operator runs `mt data migrate` (default all)

1. CLI resolves `Settings` → `market_db_url`, `timescale_db_url`.
2. For each configured URL:
   a. Construct the appropriate DB class.
   b. Call `db.apply_schema_migrations()` which delegates to `runner.apply_migrations(pool, TRACKS[name])`.
   c. Runner: ensure `schema_migrations` table exists (bootstrap `001_` if needed). Read applied IDs. For each migration not in applied, run SQL + insert tracking row in same transaction. Collect newly-applied IDs.
3. CLI prints per-track results.

### Flow B: Operator runs `mt data migrate status`

1. CLI resolves both URLs.
2. For each: if `schema_migrations` table exists, SELECT it (id, description, applied_at). Diff against track's migration list to compute `pending`. If table does not exist, all track migrations are `pending`.
3. CLI renders per-track table (or emits JSON).

### Flow C: New migration added (post-150)

Author adds a dict entry to the relevant track file. Commits. Next `mt data migrate --db <track>` picks it up and applies it. Next `mt data migrate status` shows it as applied.

## Migration Plan (this slice's own refactoring)

| Step | Source | Destination | Notes |
|---|---|---|---|
| 1 | `market/schema/migrations.py` | `market/schema/migrations/minute.py` | Move file; no content change except module docstring. |
| 2 | (new) | `market/schema/migrations/daily.py` | Contains `001_schema_migrations` and `002_reconcile_existing_schema` only. |
| 3 | (new) | `market/schema/migrations/__init__.py` | Exports `TRACKS` mapping and re-exports `MIGRATIONS` from minute track as backward-compat alias. |
| 4 | `TimescaleMinuteDataDB.apply_schema_migrations()` body | `market/schema/runner.apply_migrations()` | Extract as free function taking `(pool, migrations)`. |
| 5 | (new method) | `MarketDB.apply_schema_migrations()` | Thin wrapper calling the runner with the daily track. |
| 6 | `cli/commands/data.py::data_migrate` | same | Expand to accept `--db` and dispatch. |
| 7 | `cli/commands/data.py` | same | Add `data_migrate_status` Typer subcommand. |
| 8 | `cli/commands/data.py::daily_migrate` | same | Repurpose to unified migrator path for daily; add `daily_verify` for the old behavior. |
| 9 | `database/migrations/*.sql`, `sql/01_setup_database.sql` | (deleted) | Deleted in the same commit that introduces the README. |
| 10 | (new) | `market/schema/migrations/README.md` | Single-source-of-truth note. |

### Consumer update audit

- `mt data migrate` — direct consumer, updated.
- `mt data daily migrate` — direct consumer, updated.
- `apply_schema_migrations()` callers elsewhere — grep-verified to be only the CLI at slice time. Any new callers get the extracted runner for free.
- Tests in `test/unit/test_schema_migrations.py` and `test/integration/test_schema_integration.py` — audit and update to import from the new package path. Backward-compat re-export (`from manta_trading.market.schema.migrations import MIGRATIONS`) keeps any stragglers working.

### Behavior verification

For each track:
- State before: `SELECT migration_id, applied_at FROM schema_migrations ORDER BY migration_id;`
- Run `mt data migrate --db <track>`.
- State after: same query.
- Assert: applied set is superset of before; every member of `TRACKS[track]` is represented; applied_at is monotonic.

For the daily track specifically:
- Before: no `schema_migrations` table. `verifyDatabase()` passes.
- After first run: `schema_migrations` has `001_schema_migrations` and `002_reconcile_existing_schema`. `verifyDatabase()` still passes (it was never schema-dependent in a way migrations touch). No `symbol_list` or OHLCV table touched. Row counts in those tables unchanged.

## CLI Specification

```
mt data migrate [--db minute|daily|all] [--json]

  Apply pending schema migrations.

  --db    Which database track to target. Default: all.
          If 'all' and one URL is unset, that track is skipped with a warning.
          If a specific track is requested and its URL is unset, exits non-zero.
  --json  Machine-readable output.


mt data migrate status [--db minute|daily|all] [--json]

  Show applied and pending migrations per database track.

  (Same --db / --json semantics.)
```

Human output of `mt data migrate status`:

```
┌────────────────────────────────────┬─────────┬─────────────────────────────────┬──────────────────────────┐
│ ID                                 │ Status  │ Description                     │ Applied At               │
├────────────────────────────────────┼─────────┼─────────────────────────────────┼──────────────────────────┤
│ 001_schema_migrations              │ applied │ Create schema_migrations …      │ 2026-04-03 05:57:11 UTC  │
│ 002_instruments                    │ applied │ Create instruments table        │ 2026-04-03 05:57:11 UTC  │
│ ...                                │         │                                 │                          │
│ 010_trading_sessions               │ pending │ Create trading_sessions table   │ —                        │
└────────────────────────────────────┴─────────┴─────────────────────────────────┴──────────────────────────┘

minute: 9 applied, 1 pending
daily:  2 applied, 0 pending
```

## Cross-Slice Dependencies and Interfaces

**Unblocks:** slice 141 (needs a place to add `010_trading_sessions` migration).

**Depends on:** nothing new. Uses existing `Settings`, `MarketDB`, `TimescaleMinuteDataDB`, and the existing migration-dict format.

**Interface to future slices:** adding a new migration to either track is "append a dict to the track's list." Adding a new track (e.g. `tick` for a future tick DB) is "new module under `migrations/`, add to `TRACKS`, add URL to `Settings`, add DB class wrapper."

## Success Criteria

Concrete enough for tasks:

1. `src/manta_trading/market/schema/migrations/` package exists with `minute.py`, `daily.py`, `__init__.py`, and `README.md`. Old `migrations.py` is gone or is a deprecated shim.
2. `src/manta_trading/market/schema/runner.py` exposes `apply_migrations(pool, migrations) -> list[str]` and `list_migration_state(pool, migrations) -> dict`.
3. `TimescaleMinuteDataDB.apply_schema_migrations()` delegates to the runner; external behavior unchanged; existing tests pass without change.
4. `MarketDB.apply_schema_migrations()` exists with the same contract and calls the runner with the daily track.
5. Running `mt data migrate status` against a fresh daily DB (before first migrate) prints all daily-track migrations as pending; running it after `mt data migrate --db daily` prints them all as applied with timestamps.
6. Running `mt data migrate` with no flags applies pending migrations to both DBs and reports which ones were applied per track.
7. `mt data migrate` with one URL unset in `Settings` and `--db all` succeeds for the configured track and emits a warning for the missing one; `mt data migrate --db <unconfigured>` exits non-zero with a clear message.
8. `database/migrations/*.sql` and `sql/01_setup_database.sql` no longer exist in the working tree. Commit message references the archival.
9. `migrations/README.md` exists and states the single-source-of-truth rule.
10. Test coverage:
    - Unit tests for the extracted runner (bootstrap-when-missing, idempotent re-run, partial-apply then resume).
    - Integration test that runs the full minute track against a real test DB and asserts `schema_migrations` rows match `TRACKS["minute"]`.
    - Integration test that runs the daily track and asserts `002_reconcile_existing_schema` is a no-op (row counts in pre-existing tables unchanged).
    - CLI test for `mt data migrate status --json` shape.

## Verification Walkthrough (demo script)

Prerequisites: both `MT_MARKET_DB_URL` and `MT_TIMESCALE_DB_URL` set to test DBs in `.env`.

**Note:** CLI command is now `mt data migrate apply` (not `mt data migrate`) since `migrate`
is a sub-app. Use `mt data migrate apply [--db ...]` to apply and `mt data migrate status` to
inspect. `mt data daily migrate` is a convenience alias for `mt data migrate apply --db daily`.

```bash
# 1. Starting state: minute has 001-009 applied; daily has 2 applied (already reconciled).
mt data migrate status
# minute migrations table renders with 9 rows, all "applied"
# daily migrations table renders with 2 rows, all "applied"
# → minute: 9 applied, 0 pending
# → daily:  2 applied, 0 pending

# 2. Bring daily under management (idempotent on already-managed DB).
mt data migrate apply --db daily
# → daily: 0 applied

# 3. Confirm daily tables untouched.
psql "$MT_MARKET_DB_URL" -c "SELECT COUNT(*) FROM symbol_list;"
# → same row count as before

# 4. Confirm both tracks green.
mt data migrate status
# → minute: 9 applied, 0 pending
# → daily:  2 applied, 0 pending

# 5. Verify JSON output shape for scripting.
mt data migrate status --json | python3 -c "import sys,json; d=json.load(sys.stdin); print(list(d['tracks'].keys()))"
# → ['minute', 'daily']
mt data migrate status --json | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['tracks']['daily']['applied']))"
# → 2

# 6. Idempotency: running again does nothing.
mt data migrate apply
# → minute: 0 applied, daily: 0 applied

# 7. Confirm orphans are gone.
ls database/migrations/ 2>&1
# → No such file or directory
ls sql/01_setup_database.sql 2>&1
# → No such file or directory

# 8. Confirm daily verify still works under its new name.
mt data daily verify
# → {'status': 'ok', 'message': 'Database verified'}

# 9. Slice 141 readiness: adding a new migration is now trivial.
#    Append a dict to TRACKS["minute"] (in minute.py) and run:
#    mt data migrate apply --db minute
#    The new migration appears as "applied" in mt data migrate status.
```

Demonstrated outcome: both DBs are under the same managed framework, state is inspectable
from the CLI in human and JSON forms, no schema drift was introduced, and the orphaned SQL
files that caused prior confusion are gone.

**Caveats discovered during implementation:**
- The `migrate` command is a Typer sub-app, so the apply subcommand is
  `mt data migrate apply` not `mt data migrate`. The alias `mt data daily migrate` still works.
- `mt data migrate status --json` output has `connected: true` key per track to distinguish
  connection failure from an empty-but-reachable DB.

## Risks

- **Backward-compat import paths.** Downstream modules may import `MIGRATIONS` from `manta_trading.market.schema.migrations` (the old module). The `migrations/__init__.py` re-export covers this; if any other imports surface during implementation, they're straightforward fixes.
- **Reconciliation migration feels like a hack.** It is — deliberately. The alternative (writing a Python-declared version of the live daily schema) is more code for no immediate benefit and risks diffing against reality. Documented in the slice design so future-us knows why.

## Effort

2/5. Extends an existing framework; new code is mechanical; scope is deliberately bounded.
