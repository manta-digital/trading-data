---
docType: slice-design
slice: 142-schema-migration-and-cold-start
project: trading
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [141-slice.universe-rebuild-from-eodhd-instruments-schema-migration]
interfaces: [143-slice.compute-k-factor-single-source, 144-slice.daemon-refactor, 145-slice.data-status, 146-slice.data-refetch, 147-slice.data-audit]
dateCreated: 20260501
dateUpdated: 20260501
reviewVerdictsAddressed:
  - 142-review.slice (minimax/minimax-m2.7, CONCERNS, F001/F002/F003/F004)
status: complete
---

# Slice Design: 142 — Schema Migration and Cold-Start

## Overview

The destructive, schema-pivot slice of the data-quality initiative. Lands the new
control-plane schema (`data_gaps`, slimmed `acquisition_state`, the `data_status`
view, and a centralized `manta_trading.constants` module), drops the now-orphaned
`coverage_gaps` table, and TRUNCATEs the bar tables and acquisition state so the
next slice's daemon refactor (slice 144) can cold-start backfill against an
explicitly-empty store.

The rebuilt universe from slice 141 is the precondition. Slice 144 is the
consumer that actually drives population back. This slice writes no rows other
than schema and view definitions; its purpose is to leave the database in a
clean, consistent shape that the rest of the initiative can reason about
without inheriting AV-era noise.

## Value

Three concrete deliverables:

1. **`data_gaps` table** lands with the four-state `fetch_status` enum
   (`UNKNOWN`, `PROVIDER_HOLE`, `FAILED_RETRYABLE`, `RETRY_EXHAUSTED`) and
   session-normalized timestamp ranges. This is the table the daemon (slice
   144) writes to and the operator commands (slices 145–147) read from. With
   it in place, every later slice has a single, well-typed, view-queryable
   gap-state surface.

2. **Slimmed `acquisition_state`** removes the columns that broke status
   during the slice 128 dry-run (`last_success_ts` provider-tag conflation,
   double-bookkeeping `retry_count`) and adds
   `last_adjusted_ca_snapshot_id` for slice 144's CA-detection mechanism.
   `last_attempt_outcome` becomes a typed enum so the daemon's outcome →
   `data_gaps.fetch_status` mapping is mechanically enforced.

3. **`data_status` view** materializes the per-(symbol, granularity) health
   answer the operator commands consume. The view is correct for an empty DB
   from the moment it lands (LEFT JOIN means every newly-added symbol with no
   acquisition row is `STALE`, not absent), and uses an
   exchange-keyed CTE for `target_end` so the query stays sub-second at the
   ~33k-row universe slice 141 produced.

Plus the cold-start: TRUNCATE `minute_ohlcv`, `daily_ohlcv`, the old
`acquisition_state`, and the orphaned `coverage_gaps`. This is the
honest-re-derivation choice from the architecture: AV-era bars carried
correctness issues (mistagged providers, slice 127 known bugs) and we prefer
empty + refetch over inherited noise.

## Technical Scope

In scope:
- New migrations on the **minute** track (this is the TimescaleDB instance
  that owns instruments, acquisition_state, coverage_gaps, and the bar
  hypertables).
- New `data_gaps` table with primary key, indexes, and CHECK on
  `fetch_status`.
- Slimmed `acquisition_state` schema: drop `last_success_ts` and
  `retry_count`; add `last_adjusted_ca_snapshot_id` (TEXT) and
  `last_attempt_outcome` (TEXT with CHECK constraint).
- Drop `coverage_gaps` table and the slice 128 NVDA seed row that lived in it.
- `data_status` view per arch §"One status view".
- New `manta_trading.constants` module exposing every constant the arch
  enumerates.
- Pre-flight verification module that gates the destructive step on slice 141
  having actually populated `instruments`.
- TRUNCATE step for `minute_ohlcv`, `daily_ohlcv`, `acquisition_state`, and
  (drop, not truncate) `coverage_gaps`.
- A new CLI subcommand `mt data migrate-cold-start` that runs the migrations,
  pre-flight, and TRUNCATE in a single operator-confirmed flow.
- Removal of every code path that reads `last_success_ts` or
  `acquisition_state.retry_count` (these are gone from the schema; the
  removal is mandatory, not aesthetic).

Out of scope (explicit; deferred to named slices):
- The daemon's gap-driven backfill loop, CA detection, and band-based
  adjustment UPDATEs → slice 144.
- `compute_k_factor` consolidation and `compute_snapshot_id` → slice 143.
- Operator-facing `mt data status / refetch / audit` commands → slices
  145–147. The `data_status` view lands here; readers come later.
- `update_data_gaps`, `coalesce_data_gaps`, `compute_missing_ranges`,
  advisory-locking discipline → slice 144 (architecturally specified, but
  this slice does not implement them).
- Repopulating bars. The DB is left empty after this slice. Slice 144's
  daemon refills it.

## Dependencies

### Prerequisites

- **Slice 141 — Universe rebuild from EODHD + instruments schema migration**.
  Hard-blocking. Pre-flight check (D1) verifies that 141's column adds and
  bulk upsert ran successfully before this slice TRUNCATEs anything. If 141
  has not been run, this slice halts before any destructive action.
- 141's migrations 015–017 must have applied (`first_listing_date`,
  `first_data_date`, `delisted_date`, `eodhd_type NOT NULL`,
  `eodhd_exchange NOT NULL`, `delisted_at_eodhd`, `active` dropped).
  These migrations and the `eodhd_type` populated rows are the pre-flight's
  trigger.

### Interfaces Required

- `manta_trading.market.schema.runner` — the existing migration runner
  applies dict entries from `MINUTE_MIGRATIONS`. New entries are appended
  there.
- `manta_trading.market.schema.migrations.minute` — module list extended
  with migrations 018–022 (one per logical change; see Migration Plan).
- `psycopg`-based connection pool already wired through
  `manta_trading.cli.commands.data` for the existing rebuild command;
  reused for the cold-start CLI.
- Existing `Granularity` StrEnum in
  `src/manta_trading/data/acquisition/state.py` — referenced by view DDL
  and the slimmed `acquisition_state` DTO.
- The trading_calendar table from slice 102 / arch §"Performance pattern" —
  the view's `exchange_completed_close` CTE reads it.

## Architecture

### Component Structure

```
src/manta_trading/
├── constants.py                              [NEW]
│       Centralized constants per arch §Constants.
│
├── market/schema/migrations/minute.py        [EXTENDED]
│       Migrations 018 (data_gaps),
│       019 (slim acquisition_state),
│       020 (drop coverage_gaps),
│       021 (data_status view),
│       022 (last_attempt_outcome enum check).
│
├── data/acquisition/state.py                 [MODIFIED]
│       Drop last_success_ts and retry_count from
│       AcquisitionStateRow. Add last_adjusted_ca_snapshot_id.
│       Add LastAttemptOutcome StrEnum and rewire
│       last_attempt_outcome typing.
│
├── data/quality/                             [NEW package]
│   ├── __init__.py
│   ├── fetch_status.py
│   │       FetchStatus StrEnum: UNKNOWN, PROVIDER_HOLE,
│   │       FAILED_RETRYABLE, RETRY_EXHAUSTED.
│   │
│   └── data_gaps.py
│           DataGap dataclass (read DTO only this slice;
│           writers land in slice 144).
│
├── data/coverage/                            [DELETED package]
│       Removed entirely. CoverageGapStatus enum,
│       persist_coverage_gaps, scanner — all gone.
│
└── cli/commands/data.py                      [MODIFIED]
        New subcommand `migrate-cold-start`.
        Removed: every reference to last_success_ts and
        retry_count (functions reduced to gap_days /
        last_attempt_ts only).
```

### Data Flow

The cold-start flow is a single linear orchestration. No background tasks,
no resumability mid-flow — if it fails it fails atomically.

```
operator
  │
  │  mt data migrate-cold-start [--yes]
  ▼
┌─────────────────────────────────────────┐
│  Step 1 — Pre-flight                    │
│  ├ verify schema_migrations contains    │
│  │  015, 016, 017 (slice 141 applied)   │
│  ├ verify instruments.eodhd_type        │
│  │  NOT NULL is populated, row count    │
│  │  in [30_000, 80_000]                 │
│  ├ verify EODHD daily probe (optional   │
│  │  small sample; halt on failure;      │
│  │  --skip-probe to omit)               │
│  └ HALT on any failure                  │
└────────────────┬────────────────────────┘
                 │  pre-flight OK
                 ▼
┌─────────────────────────────────────────┐
│  Step 2 — Operator confirmation gate    │
│  Print row counts that will be          │
│  truncated (minute_ohlcv,               │
│  daily_ohlcv, acquisition_state) and    │
│  the table that will be dropped         │
│  (coverage_gaps). 5-second wait then    │
│  prompt. Skipped under --yes.           │
└────────────────┬────────────────────────┘
                 │  confirmed
                 ▼
┌─────────────────────────────────────────┐
│  Step 3 — Migrations + TRUNCATE         │
│  Single transaction (DO $$):            │
│  ├ apply migrations 018–022             │
│  ├ TRUNCATE minute_ohlcv,               │
│  │  daily_ohlcv, acquisition_state      │
│  ├ DROP TABLE coverage_gaps             │
│  └ COMMIT                               │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│  Step 4 — Post-flight                   │
│  ├ SELECT count(*) FROM data_gaps       │
│  │  → 0 (table exists, is empty)        │
│  ├ SELECT count(*) FROM data_status     │
│  │  → instruments × 2 (every symbol     │
│  │    yields one daily + one minute     │
│  │    row; all health = STALE)          │
│  ├ EXPLAIN data_status query →          │
│  │  no per-row function calls           │
│  │  (CTE-driven plan)                   │
│  └ print summary table                  │
└─────────────────────────────────────────┘
```

### State Management

After this slice, the persisted-state surface is:

| Table / View                | Owner / Writer                  | Reader                                                |
|-----------------------------|---------------------------------|-------------------------------------------------------|
| `instruments`               | slice 141 rebuild + slice 144 backfill (first_data_date / delisted_date side effects) | data_status, daemon, all operator commands |
| `data_gaps`                 | slice 144 daemon, slice 146 refetch | data_status view, slice 145 detail listing             |
| `acquisition_state` (slim)  | slice 144 daemon                | data_status (last_attempt_ts, last_attempt_outcome)    |
| `data_status` (view)        | (no writer — derived)           | slice 145 status command, ad-hoc operator queries      |
| `minute_ohlcv` / `daily_ohlcv` (TRUNCATEd) | slice 144 daemon | backtest, audit, status's bars_stored count          |
| `coverage_gaps`             | (DROPPED — does not exist)      | (none)                                                 |

This slice writes to none of the data-bearing tables. It only reshapes
schema and zeroes content.

## Technical Decisions

### D1. Pre-flight is mandatory, not opt-in

The TRUNCATE step is gated on a verifying read of `instruments` and
`schema_migrations`, not on the operator's word that slice 141 ran.

**Rationale.** Running this slice against a database where slice 141 has
not actually applied wipes the bar tables but leaves the AV-era ~8k
universe in `instruments`. The daemon (slice 144) would then backfill
against the wrong universe, and the only signal would be a
silently-too-small target_end column count. Pre-flight catches this
before the destructive transaction opens.

**Pre-flight rules (all must pass):**

1. `schema_migrations` contains rows for `015_instruments_lifecycle_columns`,
   `016_instruments_eodhd_type_not_null`, and `017_instruments_drop_active`.
2. `SELECT count(*) FROM instruments WHERE eodhd_type IS NULL` returns 0.
3. `SELECT count(*) FROM instruments` returns a value in `[30_000, 80_000]`
   (the slice 141 range; tighter than `> 0` to catch a partially-rolled-back
   141 run, looser than the literal observed 32_875 to absorb day-over-day
   universe drift before this slice runs). The 80k ceiling is a **sanity
   bound**, not a precise threshold: slice 141 with current filters
   (`Common Stock | ETF | Preferred Stock | INDEX`, OTC tiers excluded,
   non-US ADRs dropped) produces ~33k rows, and EODHD's USA-relevant
   universe does not realistically grow into the 80k+ range. Tripping the
   upper bound means something changed upstream — a filter regression, OTC
   tiers re-included, EODHD redefining `Country = 'USA'` — and a human
   should look before TRUNCATEing 60M+ bars on the assumption that the new
   universe is correct. Halting here costs an operator a re-run; not
   halting could destroy data we'd then refill from a wrong universe.
4. `information_schema.columns` shows `instruments.active` is **gone**.
5. (Optional, default on; `--skip-probe` to omit) An EODHD `/eod` probe of
   a small sample (3 symbols: AAPL, MSFT, SPY) returns rows. This is a
   liveness check on the provider before destroying local data; it does not
   write anything.

   **Probe failure modes** (each halts pre-flight with a typed message; no
   automatic retries — this is a liveness probe before a destructive
   operation, not a production fetch):

   | Condition                         | Behavior                                                            |
   |-----------------------------------|---------------------------------------------------------------------|
   | Network timeout (default 10s)     | halt; message names the host and timeout value                      |
   | HTTP 401 / 403 (auth)             | halt; message points the operator at `EODHD_API_KEY`                |
   | HTTP 4xx (other)                  | halt; message includes status + response body excerpt               |
   | HTTP 5xx                          | halt; "EODHD reports server error — retry the cold-start later"     |
   | HTTP 200, empty array / null body | halt; "EODHD returned empty for AAPL — schema or scope changed"     |
   | HTTP 200, malformed JSON          | halt; message includes parser error                                 |

   No retry on 5xx: a transient provider blip is a reason to **wait and
   re-run the operator command**, not to silently push past it into a
   destructive transaction. The whole point of the probe is "is the
   provider healthy enough that I'm comfortable wiping local data and
   relying on it for the refill." If the answer isn't an unambiguous yes,
   halt.

A failure anywhere halts the slice **before** the migration transaction
opens. No rows have been mutated.

**Why not assert on `instruments.eodhd_exchange NOT NULL` too?** Migration
016 already enforces that constraint at DDL level; if the column exists
and is NOT NULL, the DB has already verified it. Asking again here would be
checking the DB against itself.

### D2. The cold-start CLI is a single command, not a sequence

`mt data migrate-cold-start` runs pre-flight + confirmation + migrations +
TRUNCATE + post-flight in one process. No `mt data migrate` followed by
`mt data truncate`.

**Rationale.** Splitting these is a foot-gun. An operator who runs the
migrations but skips the TRUNCATE leaves the database in an inconsistent
state: the new `data_status` view sees stale bars, the slimmed
`acquisition_state` rejects rows the old daemon code (if still running)
tries to write. The two operations belong to one transaction at the
operator-intent level. The `--yes` flag lets automation skip the
confirmation gate; everything else is non-skippable.

### D3. Migrations and TRUNCATE share **one** database transaction

The five new migrations (018–022) plus the TRUNCATE plus the DROP TABLE
all run in one BEGIN / COMMIT. If any DDL fails, the bar tables are not
truncated; if the TRUNCATE fails (e.g., FK violation we missed), the DDL
is rolled back.

**Rationale.** `TRUNCATE` in PostgreSQL is transactional. There is no
defensible reason to half-apply a migration set. The architecture spec
says explicitly: "Wipe (single transaction): … New schema lands … then
TRUNCATE … then COMMIT."

**Caveat.** The schema_migrations rows for 018–022 must be inserted
**inside** the transaction (the existing migration runner already does
this). If the transaction rolls back, the rows roll back too — the
migrations remain unrun, and re-running the CLI is an idempotent retry.

### D4. `coverage_gaps` is DROPPED, not preserved as deprecated

Migration 020 is `DROP TABLE coverage_gaps`. Not `ALTER TABLE …
RENAME TO coverage_gaps_deprecated`. Not retained-but-unused.

**Rationale.** `coverage_gaps` was a slice 128 table whose semantics
are fully replaced by `data_gaps`'s `PROVIDER_HOLE` state. Keeping it
around is database clutter and a future-reader trap ("which one is
current?"). The architecture lists it under "Out of scope (designed
out, not deferred)." The seed row for NVDA (migration 014) is lost
along with it; that row will reappear naturally as a `PROVIDER_HOLE`
in `data_gaps` after slice 144's first NVDA fetch attempt. No
information is lost in the long term.

The associated Python module `src/manta_trading/data/coverage/` is
deleted in the same change; `persist_coverage_gaps` and
`CoverageGapStatus` are removed everywhere they appear.

### D5. `data_gaps.fetch_status` is a CHECK constraint, not a Postgres ENUM type

```sql
fetch_status TEXT NOT NULL,
CONSTRAINT data_gaps_fetch_status_check
  CHECK (fetch_status IN ('UNKNOWN', 'PROVIDER_HOLE',
                          'FAILED_RETRYABLE', 'RETRY_EXHAUSTED'))
```

The values are derived in the migration SQL from the
`manta_trading.data.quality.fetch_status.FetchStatus` StrEnum, the same
pattern slice 141 uses for `EodhdType` (see migration 016's
`_eodhd_type_check_sql()` helper).

**Rationale.** Three considerations agree:

1. PostgreSQL ENUM types are awkward to extend (requires
   `ALTER TYPE … ADD VALUE`, cannot be removed without recreating
   the type and rewriting consumers). A CHECK constraint is one
   `ALTER TABLE … DROP CONSTRAINT … ADD CONSTRAINT` operation.
2. Slice 141 already established the StrEnum-as-source-of-truth +
   CHECK-derived-from-enum pattern. Consistency.
3. Project rule: "Never scatter comparison values across code." The
   StrEnum is the single source; the migration helper renders the CHECK
   from it; any place that compares against status values imports the
   enum.

### D6. `last_attempt_outcome` is similarly StrEnum + CHECK

```python
class LastAttemptOutcome(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    EMPTY = "empty"
    TRANSIENT_FAILURE = "transient_failure"
```

Migration 022 adds the CHECK derived from this enum. Migration 019 sets
the column type to `TEXT NOT NULL`. The split (column add in 019, CHECK
in 022) lets 019 backfill any pre-existing rows to a default of
`'success'` before the CHECK lands; in practice the column is added on
an empty (truncated) table, so this is theoretical correctness, not
operational concern.

The mapping table from arch §"Mapping last_attempt_outcome to
data_gaps.fetch_status" is **specification only** for this slice — it
is implemented by slice 144's `update_data_gaps`. We document it here
in the success criteria so the enum is not treated as a free-text
column.

### D7. `data_status` is a view, not a materialized view

Per arch §Performance pattern: at ~33k–60k symbols × 2 granularities
the view's CTE-based plan stays sub-second on commodity hardware. We do
not materialize and we do not add a refresh-trigger surface.

**Rationale.** Materialization adds: (a) refresh discipline (when?
on every gap write?), (b) a staleness window the operator must
remember, (c) a write-amplification surface during cold-start when
the daemon is hammering `data_gaps`. Skipping it removes all three.
A future "FW" item triggers materialization only if measurement
shows the operator command running too slow.

The view DDL **lands here** even though no production caller exists
yet (slice 145 is the first reader). This is intentional: the view
is part of the schema migration. Landing it means slice 145 is a
pure code change, not a code-plus-migration change.

### D8. The view's `exchange_completed_close` CTE reads `trading_calendar`, not a function

```sql
WITH exchange_completed_close AS (
  SELECT calendar_id,
         MAX(session_close_utc) AS completed_close_ts
  FROM trading_calendar
  WHERE session_close_utc + INTERVAL '30 minutes' < NOW()
  GROUP BY calendar_id
)
```

Per arch's performance pattern. The 30-minute literal is
`LATE_BAR_GRACE_PERIOD` from `manta_trading.constants` — this slice
stores it as a constant in Python, but in DDL we materialize the
literal. Reason: making the view depend on a `current_setting()` GUC
or a SQL function bound at view-creation is a brittle
substitution-vector for a value we already know at migration time.
If the constant changes, that's a schema migration (drop view + recreate)
— honest, visible churn, not silent drift.

(This is the only place a magic-looking literal appears in DDL. The
project rule against magic strings is met by the migration runner
rendering it from `LATE_BAR_GRACE_PERIOD`; see Migration Plan §5.)

**Divergence from arch's example DDL.** Arch §"Performance pattern"
shows an inner `JOIN exchange_completed_close` against the symbols
side. We use **`LEFT JOIN`** so a symbol whose `trading_calendar_id`
has no completed-close row yet (e.g., a calendar with no in-range
sessions, or a future-dated calendar fixture) still appears in
`data_status` with `target_end_ts = NULL` — falling through the
`last_attempt_ts IS NULL` health branch to `STALE` rather than being
silently filtered out of the operator's view. Inner-join would lose
those rows entirely; the operator would not see them as STALE, they
would simply be absent. For a status view whose central promise is
"every symbol the operator owns appears here," silent absence is
the worse failure mode. The arch's example is preserved in spirit
(CTE-driven `target_end`, not per-row function call); the join
flavor changes for honesty, not performance.

### D9. The view's join key is `instruments.trading_calendar_id`, not `venue`

Per arch §Performance pattern. An ETF on `NYSE_ARCA` follows the
`NYSE` calendar; an unknown-venue symbol with `venue='US'` falls back
to `trading_calendar_id='NYSE'`. Joining on `venue` would exclude the
`'US'`-placeholder rows entirely from `data_status`, which is exactly
backwards (those rows are the ones the operator most needs to see as
STALE).

We assume `instruments.trading_calendar_id` is populated for every row
after slice 141. The pre-flight check D1.4 verifies this implicitly
(the column was set to NOT NULL via migration 015's chain, and
populated by slice 141's orchestrator).

### D10. Constants live in **one** module, even though they apply at multiple layers

`src/manta_trading/constants.py` exposes:

```python
from datetime import timedelta
from decimal import Decimal

ADJUSTMENT_DRIFT_EPSILON: Decimal = Decimal("1e-6")
MAX_RETRY_COUNT: int = 5

DAILY_STALENESS_THRESHOLD: timedelta = timedelta(days=2)
MINUTE_STALENESS_THRESHOLD: timedelta = timedelta(days=1)

DAILY_HISTORY_MONTHS: int | None = None    # None = unbounded
MINUTE_HISTORY_MONTHS: int = 24

LATE_BAR_GRACE_PERIOD: timedelta = timedelta(minutes=30)
MAX_GAP_STALENESS: timedelta = timedelta(minutes=5)
```

**Rationale.** The arch lists these together for a reason: changing one
forces re-evaluation of the others (e.g., raising `MAX_RETRY_COUNT`
without considering `MINUTE_STALENESS_THRESHOLD` produces stuck-stale
symbols). Co-location forces that re-evaluation by surfacing the
trade-off whenever someone opens the file.

The existing `HISTORY_MONTHS = 24` constant in
`src/manta_trading/data/acquisition/minute/freshness.py` is **moved**
to this module and re-exported with the rename `MINUTE_HISTORY_MONTHS`.
The freshness module imports the new symbol; no behavior change. The
move is non-trivial because slice 144's daemon refactor will subsume
that whole module — this slice does the import surgery so 144 doesn't
have to.

### D11. The cold-start CLI prints what it's about to destroy, then waits

The 5-second confirmation gate from slice 141's D10 (AV-orphan delete)
is reused here for the TRUNCATE. The mechanism is the same: print
counts, sleep 5s, prompt; `--yes` skips. This is the project's
established pattern for destructive operator actions; using it here
maintains uniformity (operator muscle memory is a feature).

```
$ mt data migrate-cold-start
Pre-flight … OK
About to TRUNCATE / DROP:
  minute_ohlcv         ~ 61_800_000 rows
  daily_ohlcv          ~  2_400_000 rows
  acquisition_state    ~     12_400 rows
  coverage_gaps        DROP TABLE (1 row)
This action is irreversible. Continuing in 5 s …
Type 'truncate' to proceed: _
```

`--yes` collapses the wait + prompt into auto-confirm. Same shape as
slice 141.

### D12. Removed code paths are deleted, not feature-flagged

Every reference to `last_success_ts` or `retry_count` in
`src/manta_trading/cli/commands/data.py` and elsewhere is **removed**.
No `if ENABLE_NEW_STATE_SHAPE:` branches, no parallel old/new code
paths.

**Rationale.** The columns are gone from the schema after this slice
runs. Code that references them does not compile (typed) / raises
KeyError at runtime (untyped). Half-removing is worse than fully
removing — it leaves dead branches that confuse future readers. The
project rule "Avoid backwards-compatibility hacks" applies directly.

The functions affected (`_render_acquisition_table`, `_acquisition_summary`,
`_is_fresh`, `_gap_days`, etc.) shrink to the columns that remain
(`last_attempt_ts`, `last_attempt_outcome`). Where they previously
computed freshness from `last_success_ts`, they now compute it from
`last_attempt_ts` (the daemon's heartbeat) plus the
appropriate `STALENESS_THRESHOLD`.

### Patterns and Conventions

- **Migration helpers.** New StrEnum-derived CHECK clauses use the same
  `_render_check_sql(enum)` pattern as slice 141's `_eodhd_type_check_sql`.
  One helper per enum, lives in `migrations/minute.py`, sorted-values
  for deterministic SQL text.
- **Idempotent DDL.** `IF NOT EXISTS`, `DO $$ … IF NOT EXISTS … END $$`,
  and `DROP TABLE IF EXISTS` are used consistently. Re-running the
  migration runner against an already-migrated DB is a no-op.
- **No silent fallbacks.** The pre-flight check raises typed exceptions
  (`PreflightFailed` with a message naming the failed assertion). The
  CLI catches it and exits non-zero with the message printed verbatim.
- **Dataclass + StrEnum DTOs.** `DataGap` is a dataclass; `FetchStatus`
  and `LastAttemptOutcome` are StrEnums per project Python rules.

## Implementation Details

### Migration Plan

Five new migrations on the `minute` track (same track as 015–017 from
slice 141). Daily track is unchanged.

#### Migration 018 — Create `data_gaps`

```sql
CREATE TABLE IF NOT EXISTS data_gaps (
    symbol           TEXT          NOT NULL,
    granularity      TEXT          NOT NULL,
    gap_start        TIMESTAMPTZ   NOT NULL,
    gap_end          TIMESTAMPTZ   NOT NULL,
    fetch_status     TEXT          NOT NULL,
    last_attempt_ts  TIMESTAMPTZ,
    attempt_count    INTEGER       NOT NULL DEFAULT 0,
    CONSTRAINT data_gaps_pkey
        PRIMARY KEY (symbol, granularity, gap_start, gap_end),
    CONSTRAINT data_gaps_fetch_status_check
        CHECK ({_fetch_status_check_sql()}),
    CONSTRAINT data_gaps_granularity_check
        CHECK (granularity IN ('daily', 'minute')),
    CONSTRAINT data_gaps_range_check
        CHECK (gap_end >= gap_start),
    CONSTRAINT data_gaps_attempt_count_check
        CHECK (attempt_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_data_gaps_symbol_granularity
    ON data_gaps (symbol, granularity);
CREATE INDEX IF NOT EXISTS idx_data_gaps_fetch_status
    ON data_gaps (fetch_status);
```

Notes:
- The PK includes `gap_start` and `gap_end` to allow multiple disjoint
  gaps per (symbol, granularity). Slice 144's `coalesce_data_gaps`
  merges adjacent rows with matching `fetch_status`; the PK does not
  prevent adjacency.
- No FK to `instruments(symbol)`. `data_gaps` is keyed on `symbol` for
  query speed; instruments may be deleted in future universe rebuilds
  (a hard FK would block them). Slice 144 reconciles by writing only
  for symbols still in `instruments`.
- `idx_data_gaps_fetch_status` supports the health rule's
  `EXISTS data_gaps row WHERE fetch_status = 'RETRY_EXHAUSTED'` lookup.
- Granularity is `TEXT` with CHECK rather than referencing the
  `Granularity` StrEnum's `tick` value: `data_gaps` does not yet
  represent ticks (arch §Future work). Adding `'tick'` to the CHECK
  later is a one-line migration.

#### Migration 019 — Slim `acquisition_state`

```sql
ALTER TABLE acquisition_state
    DROP COLUMN IF EXISTS last_success_ts,
    DROP COLUMN IF EXISTS retry_count,
    DROP COLUMN IF EXISTS error_message,
    DROP COLUMN IF EXISTS run_id,
    DROP COLUMN IF EXISTS status,
    ADD COLUMN IF NOT EXISTS last_attempt_outcome  TEXT,
    ADD COLUMN IF NOT EXISTS last_adjusted_ca_snapshot_id TEXT;
```

Notes:
- `status`, `error_message`, `run_id` are dropped because the new
  health model derives them from `data_gaps.fetch_status` and
  `last_attempt_outcome`. The arch's slimmed shape is just
  `(symbol, granularity, provider, last_attempt_ts,
  last_attempt_outcome, last_adjusted_ca_snapshot_id)`.
- The TRUNCATE (in migration 023's transaction body, not as a separate
  migration; see below) wipes any rows before the CHECK on
  `last_attempt_outcome` lands in 022. So the CHECK does not need a
  pre-existing-row backfill.

#### Migration 020 — Drop `coverage_gaps`

```sql
DROP TABLE IF EXISTS coverage_gaps;
```

The seed NVDA row from migration 014 goes with the table. Re-deriving it
post-slice-144 is automatic: the first NVDA fetch attempt over the
2024-06-07 → 2024-07-25 window returns empty, slice 144's
`update_data_gaps` writes a `PROVIDER_HOLE` row.

#### Migration 021 — `data_status` view

```sql
CREATE OR REPLACE VIEW data_status AS
WITH exchange_completed_close AS (
    SELECT calendar_id,
           MAX(session_close_utc) AS completed_close_ts
    FROM trading_calendar
    WHERE session_close_utc + INTERVAL '{LATE_BAR_GRACE_LITERAL}' < NOW()
    GROUP BY calendar_id
),
symbols_x_granularity AS (
    SELECT i.symbol,
           i.trading_calendar_id,
           i.first_listing_date,
           i.first_data_date,
           g.granularity
    FROM instruments i
    CROSS JOIN (VALUES ('daily'), ('minute')) AS g(granularity)
    WHERE i.delisted_at_eodhd = FALSE
       OR i.delisted_date IS NULL OR i.delisted_date >= (NOW() - INTERVAL '{HISTORY_HORIZON}')
),
bars_summary AS (
    SELECT 'daily'::TEXT  AS granularity, symbol,
           MIN(time) AS first_bar_ts, MAX(time) AS last_bar_ts,
           COUNT(*) AS bars_stored
    FROM daily_ohlcv GROUP BY symbol
    UNION ALL
    SELECT 'minute'::TEXT AS granularity, symbol,
           MIN(time) AS first_bar_ts, MAX(time) AS last_bar_ts,
           COUNT(*) AS bars_stored
    FROM minute_ohlcv GROUP BY symbol
),
gap_counts AS (
    SELECT symbol, granularity,
           COUNT(*) AS gap_count,
           BOOL_OR(fetch_status = 'RETRY_EXHAUSTED') AS has_retry_exhausted
    FROM data_gaps
    GROUP BY symbol, granularity
)
SELECT
    s.symbol,
    s.granularity,
    s.trading_calendar_id,
    bs.first_bar_ts,
    bs.last_bar_ts,
    COALESCE(bs.bars_stored, 0)            AS bars_stored,
    ec.completed_close_ts                  AS target_end_ts,
    COALESCE(s.first_listing_date, s.first_data_date) AS effective_start,
    COALESCE(gc.gap_count, 0)              AS gap_count,
    COALESCE(gc.has_retry_exhausted, FALSE) AS has_retry_exhausted,
    ast.last_attempt_ts,
    ast.last_attempt_outcome,
    CASE
        WHEN COALESCE(gc.has_retry_exhausted, FALSE) THEN 'FAILED'
        WHEN ast.last_attempt_ts IS NULL
             OR ast.last_attempt_ts <
                NOW() - CASE s.granularity
                    WHEN 'daily'  THEN INTERVAL '{DAILY_STALENESS_LITERAL}'
                    WHEN 'minute' THEN INTERVAL '{MINUTE_STALENESS_LITERAL}'
                END THEN 'STALE'
        WHEN COALESCE(gc.gap_count, 0) > 0 THEN 'GAPS'
        ELSE 'OK'
    END AS health
FROM symbols_x_granularity s
LEFT JOIN exchange_completed_close ec
       ON ec.calendar_id = s.trading_calendar_id
LEFT JOIN bars_summary bs
       ON bs.symbol = s.symbol AND bs.granularity = s.granularity
LEFT JOIN gap_counts gc
       ON gc.symbol = s.symbol AND gc.granularity = s.granularity
LEFT JOIN acquisition_state ast
       ON ast.symbol = s.symbol AND ast.granularity = s.granularity;
```

Notes:
- `{LATE_BAR_GRACE_LITERAL}`, `{DAILY_STALENESS_LITERAL}`,
  `{MINUTE_STALENESS_LITERAL}`, `{HISTORY_HORIZON}` are rendered from
  `manta_trading.constants` at migration build time. The migration
  helper formats e.g. `timedelta(minutes=30)` as `'30 minutes'`. The
  helper lives next to the existing CHECK helpers in
  `migrations/minute.py`.
- The `WHERE i.delisted_at_eodhd = FALSE OR i.delisted_date IS NULL OR
  delisted_date >= NOW() - HISTORY_HORIZON` filter excludes long-dead
  delistings from the active health view (they are still rows in
  `instruments` for backtest historical queries; they are just not
  part of the operator's daily monitor scope). `HISTORY_HORIZON`
  defaults to the daily history window; if `DAILY_HISTORY_MONTHS` is
  unbounded (None), the migration emits no upper bound (the third
  disjunct degenerates to TRUE).
- `LEFT JOIN acquisition_state` is the critical correctness step: a
  symbol with no acquisition row yields `last_attempt_ts = NULL` →
  the CASE expression falls into the STALE branch, not into a
  silent absent-row.
- The view does not project `bars_expected`. Computing expected
  sessions per symbol per window is a per-row operation against
  `trading_calendar` that doesn't fit the CTE pattern at scale.
  Slice 145's status command computes `bars_expected` on the
  client side per symbol when the operator passes `--symbol X`.
  The view's `gap_count` is the architectural answer at universe
  scope.

##### Designed-out columns

The architecture (§"One status view") lists `bars_expected` as a
projected column of `data_status`. **This slice deliberately omits
it from the view DDL.** The omission is a designed-out decision, not
an oversight, and is documented here on the same footing as
`coverage_gaps` being designed out (D4):

- **`bars_expected`** — expected sessions in the target window per
  (symbol, granularity). Computing this in-view requires either
  per-row evaluation against `trading_calendar` (defeats the CTE
  pattern that keeps the view sub-second at universe scope) or a
  second large CTE that pre-aggregates expected sessions per
  (calendar_id, granularity, target_window) tuple. The latter is
  feasible but adds materialization cost the operator does not pay
  for at universe scope — `mt data status` (slice 145) shows
  `gap_count` and `last_attempt_ts` for the all-symbols sweep, and
  computes `bars_expected` only when the operator narrows scope to
  `--symbol X`. The arch's invariant — "do we have everything we
  should have" — is preserved: `gap_count > 0` is the in-view
  signal that some expected sessions are missing.

  Re-adding `bars_expected` to the view is a one-migration change
  if measurement justifies it later (FW2 covers a related view
  refactor). Until then, this is a deliberate scope reduction
  matching the arch's broader principle of preferring
  "operator-scale, not enterprise-scale" view complexity.

#### Migration 022 — `last_attempt_outcome` CHECK

```sql
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'acquisition_state_last_attempt_outcome_check'
    ) THEN
        ALTER TABLE acquisition_state
            ADD CONSTRAINT acquisition_state_last_attempt_outcome_check
            CHECK (last_attempt_outcome IS NULL
                OR last_attempt_outcome IN ({_outcome_check_sql()}));
    END IF;
END $$;
```

`NULL` is permitted because a freshly-inserted row from the daemon
may set the outcome on a later UPDATE.

#### TRUNCATE / DROP step

Not its own migration row (the existing migration runner is for
non-destructive DDL). It runs **inside the same transaction** as
018–022, after the migrations apply, before commit:

```sql
TRUNCATE TABLE minute_ohlcv, daily_ohlcv, acquisition_state RESTART IDENTITY;
-- coverage_gaps already dropped by migration 020
```

Implemented by `migrate_cold_start.py`, not by an entry in
`MINUTE_MIGRATIONS`. The migration runner applies 018–022 in the
same connection, then the orchestrator issues the TRUNCATE on the
same connection before `commit()`. The `schema_migrations` rows for
018–022 are inserted by the runner; if the TRUNCATE fails, the
whole transaction rolls back including those rows.

**Slice-143 caveat (post-implementation correction).** The
`daily_ohlcv` hypertable does not yet exist on the timescale DB at
slice-142 time — it is created in slice 143 (peer to `minute_ohlcv`).
Until then, the cold-start TRUNCATE skips `daily_ohlcv` silently
(check via `to_regclass('public.daily_ohlcv')`) and the `data_status`
view's `bars_summary` CTE is installed without the daily branch
(migration 021 is a `DO $$` block that picks the variant based on
the same `to_regclass` test). The view's `symbols_x_granularity`
CROSS JOIN still emits a row per `(symbol, 'daily')`; those rows
show `bars_stored = 0` until slice 143 lands the table and slice 144
fills it. Re-running the cold-start (or any subsequent migration
re-apply) after slice 143 picks up the daily-included view variant
automatically.

**`target_end_ts` deferral (post-implementation correction).** The
arch describes the view's `exchange_completed_close` CTE as reading
a `trading_calendar` table that materializes per-session
`(calendar_id, session_open_utc, session_close_utc)` rows. **That
table does not exist in the implementation.** Sessions are computed
in Python by `TradingCalendar` from `trading_calendars.market_close`
+ `trading_calendars.timezone` + `trading_holidays.market_status` /
`early_close_time`. Reimplementing that logic in SQL is non-trivial
(timezone math, weekend skipping, holiday + early-close handling)
and risks drifting from the Python source of truth.

The slice-142 view therefore returns `target_end_ts = NULL`. Health
rules do not depend on `target_end_ts` (the staleness branch keys on
`last_attempt_ts` against the `*_STALENESS_THRESHOLD` constants), so
this is a display-only deferral — operator output will show "—" for
target_end until slice 144 chooses one of:

- **(A, leading candidate)** Materialize a `trading_sessions` table
  populated by a maintenance job (calendar + holiday → per-session
  open/close UTC rows). The CTE then becomes
  `MAX(session_close_utc) WHERE session_close_utc + grace < NOW()`
  — trivial SQL, sub-second at universe scope, single source of
  truth shared with the Python `TradingCalendar` (which can also
  read this table instead of recomputing). Slice 144's daemon
  needs `target_end` per symbol anyway; computing it once at
  table-write time is cheaper than recomputing in every view query.
- **(B, fallback)** A fuller in-view SQL replacement covering
  weekends, holidays, and early-close days. Correct but parallel to
  Python; risk of drift over time.

`mt data status --symbol X` (slice 145) computes target_end
client-side via the existing `TradingCalendar` for the
`--symbol` case, so per-symbol operator inspection works today
even with `target_end_ts = NULL` in the universe view.

#### Consumer updates for removed columns

Files modified:

- `src/manta_trading/data/acquisition/state.py` —
  `AcquisitionStateRow`: drop `last_success_ts`, `retry_count`,
  `error_message`, `run_id`, `status`. Add `last_attempt_outcome:
  LastAttemptOutcome | None` and `last_adjusted_ca_snapshot_id:
  str | None`. The repository's INSERT / UPDATE SQL is rewritten to
  match the slimmed columns.
- `src/manta_trading/cli/commands/data.py` — every reference to the
  removed columns is deleted. The acquisition status table
  (~lines 140–190 and ~lines 700–760, 1860–2040) collapses to:
  `symbol, granularity, provider, last_attempt_ts,
  last_attempt_outcome, gap_count_from_view, health_from_view`.
  Where the table was joining the in-memory rows with view
  output, the join becomes a single `SELECT * FROM data_status
  WHERE …` query.
- `src/manta_trading/data/acquisition/minute/freshness.py` —
  `HISTORY_MONTHS` is replaced by an import of
  `MINUTE_HISTORY_MONTHS` from `manta_trading.constants`. The
  module is otherwise untouched (slice 144 will rewrite it).
- `src/manta_trading/data/acquisition/orchestrator.py` and
  `daily/orchestrator.py` and `minute/orchestrator.py` — searches for
  `last_success_ts`, `retry_count`, `error_message`, `run_id`, and
  `status` in the AcquisitionState DTO are all removed. The
  orchestrators continue to work end-to-end against the slimmed
  shape until slice 144 rewrites them; they emit the new outcome
  enum on success/failure.
- `src/manta_trading/data/coverage/` — the package is deleted.
  Any caller is rewritten or also deleted. Slice 128's coverage
  scanner code is gone; its NVDA seed knowledge is recorded in
  this slice's docs and recovered by slice 144's first fetch.

#### Verification that behavior is preserved

This is a destructive slice; "behavior preserved" is bounded. The
verifications:

1. **Migration runner is idempotent.** Running `mt data migrate-cold-start
   --yes` against a freshly-migrated DB applies no migrations the second
   time (the `schema_migrations` table records 018–022). The TRUNCATE
   step still runs (TRUNCATE on empty tables is a no-op). The
   confirmation gate still gates on `--yes`.
2. **Pre-flight gates correctly.** With slice 141 not applied (e.g., on
   a fresh DB without 141's migrations), `mt data migrate-cold-start`
   exits non-zero with the failed assertion in stderr. No DDL ran. No
   tables were truncated.
3. **`data_status` view returns rows for every (symbol, granularity)
   in `instruments`.** Empty bars + empty gaps + empty acquisition_state
   means every row's health is STALE. View-side correctness verified.
4. **EXPLAIN of `SELECT * FROM data_status`.** Plan shows the CTE
   evaluations once (not per row), three LEFT JOINs, no per-row
   function calls. Query planner cost stays sub-second on the slice
   141 universe.
5. **Acquisition-state CLI commands run without column errors.** The
   modified `mt data` subcommands that previously read
   `last_success_ts` produce empty / N/A output instead of crashing.

### CLI Specification

#### `mt data migrate-cold-start`

```
Usage: mt data migrate-cold-start [OPTIONS]

Apply the data-quality schema migration (data_gaps, slimmed
acquisition_state, data_status view) and TRUNCATE bar + acquisition
state tables. This is the cold-start step of the data-quality
initiative; it is irreversible.

Slice 141 (universe rebuild) must have completed first.

Options:
  --yes                Skip the 5-second confirmation gate. Use only
                       in scripts.
  --skip-probe         Skip the EODHD liveness probe in pre-flight.
                       Pre-flight still verifies slice 141 ran.
  --dry-run            Run pre-flight only; print what would be
                       truncated; no DDL or DML.
  --json               Emit summary as JSON. (default: rich table)
  -v, --verbose        Verbose logging.
  --help               Show this help.

Exit codes:
  0   success
  1   pre-flight failed (slice 141 not applied, instruments
      shape wrong, EODHD probe failed)
  2   operator declined confirmation
  3   migration / TRUNCATE failed (transaction rolled back)
```

The output is a table:

```
Step                Result    Detail
─────────────────────────────────────────────────────────────
pre-flight          OK        slice 141 applied, instruments=32_875
confirmation        OK        --yes
migrations applied  OK        018, 019, 020, 021, 022
TRUNCATE            OK        minute_ohlcv (61.8M), daily_ohlcv
                              (2.4M), acquisition_state (12_400)
DROP coverage_gaps  OK        was 1 row
post-flight         OK        data_gaps=0 rows; data_status=
                              65_750 rows; all STALE
```

The migration runner already prints per-migration success on its own
log line (the runner is unchanged); the CLI's table summarizes.

#### Removed flags from `mt data` family

Subcommands in `data.py` that previously accepted `--retry-count`,
`--last-success-since`, etc., have those flags removed. The slimmed
schema does not expose those fields. The CLI rejects the flags with
typer's standard "no such option" error — there is no compat shim.

### Database / Storage Schema

After this slice, the relevant TimescaleDB schema state is:

| Object                  | State          | Notes                                                                    |
|-------------------------|----------------|--------------------------------------------------------------------------|
| `instruments`           | unchanged from 141 (~33k rows) | Pre-flight verifies                                  |
| `minute_ohlcv`          | empty          | TRUNCATEd                                                                |
| `daily_ohlcv`           | empty          | TRUNCATEd                                                                |
| `acquisition_state`     | empty + slimmed schema | Drop `last_success_ts`, `retry_count`, etc.; add `last_attempt_outcome`, `last_adjusted_ca_snapshot_id` |
| `data_gaps`             | new, empty     | PK `(symbol, granularity, gap_start, gap_end)`                           |
| `data_status`           | new, view      | Returns `instruments × {daily, minute}` rows; all STALE post-slice       |
| `coverage_gaps`         | DROPPED        | Migration 020                                                            |
| `schema_migrations`     | +5 rows        | Tracks 018–022                                                           |
| `trading_calendar`      | unchanged      | Read by `data_status` CTE                                                |
| `splits`, `dividends`   | unchanged (daily DB) | Slice 143 / 144 territory; not in this slice's scope             |

The daily-track DB (TimescaleDB instance for splits/dividends from
slice 122) is **not touched** by this slice. Its migrations track is
separate; this slice's changes are all on the minute-track DB.

### API Contracts

This slice exposes one public Python API change beyond the schema:

```python
# manta_trading/constants.py
ADJUSTMENT_DRIFT_EPSILON: Decimal      # 1e-6, audit tolerance
MAX_RETRY_COUNT: int                   # 5
DAILY_STALENESS_THRESHOLD: timedelta   # 2 days
MINUTE_STALENESS_THRESHOLD: timedelta  # 1 day
DAILY_HISTORY_MONTHS: int | None       # None (unbounded)
MINUTE_HISTORY_MONTHS: int             # 24
LATE_BAR_GRACE_PERIOD: timedelta       # 30 minutes
MAX_GAP_STALENESS: timedelta           # 5 minutes

# manta_trading/data/quality/fetch_status.py
class FetchStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    PROVIDER_HOLE = "PROVIDER_HOLE"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"

# manta_trading/data/quality/data_gaps.py
@dataclass
class DataGap:
    symbol: str
    granularity: Granularity
    gap_start: datetime
    gap_end: datetime
    fetch_status: FetchStatus
    last_attempt_ts: datetime | None
    attempt_count: int

# manta_trading/data/acquisition/state.py
class LastAttemptOutcome(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    EMPTY = "empty"
    TRANSIENT_FAILURE = "transient_failure"

@dataclass
class AcquisitionStateRow:
    symbol: str
    granularity: Granularity
    provider: str
    last_attempt_ts: datetime | None
    last_attempt_outcome: LastAttemptOutcome | None
    last_adjusted_ca_snapshot_id: str | None
```

`DataGap` is a read DTO only in this slice; the writer functions
(`update_data_gaps`, `coalesce_data_gaps`, `compute_missing_ranges`)
are slice 144's work. Defining the DTO here lets slice 144 land
purely in implementation files, not schema + DTO + impl.

## Integration Points

### Provides to Other Slices

- **Slice 143 (`compute_k_factor`)** consumes `ADJUSTMENT_DRIFT_EPSILON`
  and `compute_snapshot_id`'s storage location
  (`acquisition_state.last_adjusted_ca_snapshot_id`).
- **Slice 144 (daemon refactor)** consumes the entire schema surface:
  `data_gaps` (writes), slimmed `acquisition_state` (writes), `FetchStatus`
  + `LastAttemptOutcome` enums (logic), every constant in
  `manta_trading.constants` (rate limits, retry caps, grace period,
  staleness thresholds). Also consumes the empty bar tables (cold-start
  starting condition).
- **Slice 145 (`mt data status`)** consumes the `data_status` view
  (default scope) and `data_gaps` (when `--symbol X` listing requested).
- **Slice 146 (`mt data refetch`)** consumes `data_gaps` (its
  `force_reset_terminal=True` path mutates rows that this slice's
  schema defines).
- **Slice 147 (`mt data audit`)** consumes
  `ADJUSTMENT_DRIFT_EPSILON` for tolerance defaults.

### Consumes from Other Slices

- **Slice 141** — the rebuilt `instruments` table is the precondition.
  Pre-flight verifies presence; the `data_status` view joins on
  `instruments.trading_calendar_id` and reads `first_listing_date`.
- **Slice 102 / arch §trading_calendar** — the view's
  `exchange_completed_close` CTE reads `trading_calendar`. The columns
  (`calendar_id`, `session_close_utc`) are pre-existing.
- **Slice 100 / arch §data-storage** — the bar tables (`minute_ohlcv`,
  `daily_ohlcv`) being truncatable as TimescaleDB hypertables. TRUNCATE
  on a hypertable cascades to all chunks; this is documented Timescale
  behavior, not slice work.

## Success Criteria

### Functional Requirements

1. **Pre-flight halts on missing slice 141.** Running
   `mt data migrate-cold-start --yes` on a DB where
   `schema_migrations` lacks rows for 015, 016, 017 exits 1 with
   stderr message naming the missing migration. No DDL ran.
2. **Pre-flight halts on `instruments.eodhd_type` not populated.**
   Synthetic test: insert one row with `eodhd_type = NULL` into
   `instruments`; CLI exits 1 with stderr message naming the count.
3. **Pre-flight halts on instruments row count out of band.**
   With `instruments` count of 1000 (below the 30k floor), CLI
   exits 1.
4. **EODHD probe failure halts pre-flight (when not `--skip-probe`).**
   Synthetic test: set EODHD API key to invalid; CLI exits 1.
   Pass `--skip-probe` and the same DB state passes pre-flight.
5. **Operator declines confirmation: nothing destroyed.** CLI sleeps
   5s, prompts for `truncate`, operator types `no`. CLI exits 2.
   `minute_ohlcv` row count unchanged.
6. **`--yes` skips confirmation, `--dry-run` skips destruction.**
   `--dry-run` runs pre-flight, prints the would-be-truncated
   counts, exits 0. No DDL ran.
7. **Migrations 018–022 are idempotent.** Running
   `mt data migrate-cold-start --yes` twice in succession: first
   run applies five migrations and TRUNCATEs; second run applies
   zero migrations (already in `schema_migrations`) and TRUNCATEs
   already-empty tables (no-op). Second run still exits 0.
8. **`data_gaps` table exists and is empty after slice.**
   `SELECT count(*) FROM data_gaps` returns 0. PK and CHECK
   constraints exist (`information_schema.table_constraints`).
9. **`acquisition_state` is slimmed.** Columns `last_success_ts`,
   `retry_count`, `error_message`, `run_id`, `status` are gone
   (`information_schema.columns` shows none of them on the table).
   Columns `last_attempt_outcome`, `last_adjusted_ca_snapshot_id`
   exist.
10. **`coverage_gaps` is dropped.** `information_schema.tables`
    contains no row with `table_name = 'coverage_gaps'`.
11. **`data_status` view returns one row per (symbol, granularity)
    in `instruments`.** With ~33k symbols active, the view returns
    ~66k rows. All have `health = 'STALE'` (acquisition_state is
    empty).
12. **`data_status` health rules are correct.** Synthetic seeds in a
    test DB:
    - one `acquisition_state` row with recent `last_attempt_ts` and
      no `data_gaps` rows for that (symbol, granularity) → health = OK
    - same row but with one `data_gaps` row in target window
      `fetch_status = UNKNOWN` → health = GAPS
    - same with `fetch_status = RETRY_EXHAUSTED` → health = FAILED
    - no `acquisition_state` row at all → health = STALE
13. **The `manta_trading.constants` module exposes every constant
    in arch §Constants** with the listed values and types. Every
    other module that previously hard-coded one of these
    (`HISTORY_MONTHS`) imports the constant from here.
14. **Removed code paths do not exist.** `grep -r "last_success_ts"
    src/manta_trading/` returns no hits. Same for `retry_count`,
    `error_message`, `run_id`. Same for `from manta_trading.data.coverage`.

### Technical Requirements

15. **Single transaction.** Migrations 018–022, the TRUNCATE, and
    the DROP TABLE all run in one BEGIN/COMMIT. Inducing failure on
    migration 022 (e.g., transient DB error injected via mock) leaves
    `data_gaps` not created, `coverage_gaps` not dropped,
    `minute_ohlcv` not truncated.
16. **`data_status` query plan.** `EXPLAIN (ANALYZE, FORMAT TEXT)
    SELECT * FROM data_status` shows: one Hash Aggregate for the
    CTE (not Seq Scan over `trading_calendar` per row); LEFT JOINs
    have hash-or-merge plans; no `Function Scan` per row. Total
    runtime under 1000ms on the post-slice instruments universe.
17. **Migration runner correctness.** The runner inserts
    `(migration_id, applied_at, description)` rows for 018–022 in
    `schema_migrations`. Rolling back the migration transaction
    rolls back those rows (verified by injecting a TRUNCATE failure
    and observing the rows are absent post-rollback).
18. **CHECK constraints are enforced.** Inserting a `data_gaps`
    row with `fetch_status = 'BANANA'` raises a CHECK constraint
    error. Same for `granularity = 'tick'` and for
    `gap_end < gap_start`.
19. **No magic strings.** Every comparison against a `fetch_status`,
    `last_attempt_outcome`, or `granularity` value in Python code
    references the StrEnum; no string literals like `"UNKNOWN"`
    appear in conditional dispatch outside the enum definition and
    the migration helpers (which derive their text from the enum).
20. **Type-checking passes.** `pyright --strict` over the changed
    modules emits zero errors. The slimmed `AcquisitionStateRow`
    has no fields removed-but-still-typed; the `LastAttemptOutcome`
    is exhaustively handled wherever it appears.

### Integration Requirements

21. **Slice 141 → 142 sequencing.** Running 142 immediately after
    141 on the same DB succeeds end-to-end. Running 142 on a
    no-141 DB halts at pre-flight. (Tested in integration suite.)
22. **Old freshness module imports the new constant.**
    `src/manta_trading/data/acquisition/minute/freshness.py`'s
    `HISTORY_MONTHS` is replaced by an import of
    `MINUTE_HISTORY_MONTHS` from `manta_trading.constants`.
    Existing freshness-related tests pass without modification.
23. **`data_status` view is queryable from psycopg.** A test reads
    the view with `dict_row` factory and gets back the expected
    column names (`symbol, granularity, trading_calendar_id,
    first_bar_ts, last_bar_ts, bars_stored, target_end_ts,
    effective_start, gap_count, has_retry_exhausted,
    last_attempt_ts, last_attempt_outcome, health`).

### Verification Walkthrough

This walkthrough is the demo script — what an operator (or reviewer)
runs end-to-end after this slice ships. It assumes a development DB
where slice 141 has already been applied (per its own walkthrough).

**Step 1. Confirm starting state.**

```bash
psql "$MT_TIMESCALE_DB_URL" -c \
    "SELECT count(*) AS instruments,
            count(*) FILTER (WHERE eodhd_type IS NOT NULL) AS typed
     FROM instruments;"
# expected: instruments ≈ 32_875, typed = same number

psql "$MT_TIMESCALE_DB_URL" -c \
    "SELECT count(*) AS minute_bars FROM minute_ohlcv;"
# expected: ~61_800_000 (pre-slice; will become 0)

psql "$MT_TIMESCALE_DB_URL" -c \
    "SELECT migration_id FROM schema_migrations
     WHERE migration_id LIKE '01%' ORDER BY migration_id;"
# expected: 015_, 016_, 017_ present; 018_ … 022_ absent
```

**Step 2. Dry-run the cold-start.**

```bash
mt data migrate-cold-start --dry-run
# expected output: pre-flight OK, then a printed table of what would
# be destroyed. No DDL applied, no rows mutated.

# Verify no schema change:
psql "$MT_TIMESCALE_DB_URL" -c \
    "SELECT migration_id FROM schema_migrations
     WHERE migration_id LIKE '018%' OR migration_id LIKE '019%';"
# expected: 0 rows
```

**Step 3. Apply the cold-start.**

```bash
mt data migrate-cold-start --yes
# expected output: pre-flight OK; migrations 018..022 OK; TRUNCATE OK;
# DROP coverage_gaps OK; post-flight OK with data_status row count
# matching ~2 × instruments. Total wall time under 60 s.
```

**Step 4. Confirm the new shape.**

```bash
psql "$MT_TIMESCALE_DB_URL" -c "\\d data_gaps"
# expected: table with columns symbol, granularity, gap_start, gap_end,
# fetch_status, last_attempt_ts, attempt_count; PK on first four;
# CHECK constraint listing all four FetchStatus values

psql "$MT_TIMESCALE_DB_URL" -c "\\d acquisition_state"
# expected: columns symbol, granularity, provider, last_attempt_ts,
# last_attempt_outcome, last_adjusted_ca_snapshot_id, updated_at;
# NO last_success_ts, retry_count, error_message, run_id, status

psql "$MT_TIMESCALE_DB_URL" -c \
    "SELECT count(*) FROM coverage_gaps;"
# expected: ERROR — relation "coverage_gaps" does not exist

psql "$MT_TIMESCALE_DB_URL" -c \
    "SELECT count(*) FROM data_gaps;"
# expected: 0
```

**Step 5. Confirm `data_status` works end-to-end.**

```bash
psql "$MT_TIMESCALE_DB_URL" -c "
    SELECT health, count(*)
    FROM data_status
    GROUP BY health
    ORDER BY health;
"
# expected: STALE | ~65_750 (every (symbol, granularity) is STALE
# because acquisition_state is empty)

psql "$MT_TIMESCALE_DB_URL" -c "
    SELECT * FROM data_status WHERE symbol = 'AAPL' ORDER BY granularity;
"
# expected: 2 rows; bars_stored = 0 each; gap_count = 0; health = STALE;
# target_end_ts populated; effective_start = first_listing_date
# (Finnhub-provided) or NULL if Finnhub did not enrich this row

psql "$MT_TIMESCALE_DB_URL" -c "
    EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
    SELECT * FROM data_status;
" | head -40
# expected: Hash Aggregate over trading_calendar runs once (CTE);
# total time under 1000ms; no per-row Function Scan
```

**Step 6. Confirm constants module is wired.**

```bash
python -c "
from manta_trading.constants import (
    ADJUSTMENT_DRIFT_EPSILON, MAX_RETRY_COUNT,
    DAILY_STALENESS_THRESHOLD, MINUTE_STALENESS_THRESHOLD,
    DAILY_HISTORY_MONTHS, MINUTE_HISTORY_MONTHS,
    LATE_BAR_GRACE_PERIOD, MAX_GAP_STALENESS,
)
print('OK', DAILY_STALENESS_THRESHOLD, MINUTE_HISTORY_MONTHS)
"
# expected: OK 2 days, 0:00:00  24

# Confirm freshness module no longer defines HISTORY_MONTHS locally:
grep -n "^HISTORY_MONTHS" src/manta_trading/data/acquisition/minute/freshness.py
# expected: 0 hits
```

**Step 7. Confirm the destructive code paths don't exist.**

```bash
grep -rn "last_success_ts\|coverage_gaps" src/manta_trading/ \
  --include="*.py"
# expected: 0 hits (the column / table / module names are gone)

grep -rn "from manta_trading.data.coverage" src/manta_trading/ tests/ \
  --include="*.py"
# expected: 0 hits
```

**Step 8. Idempotent re-run.**

```bash
mt data migrate-cold-start --yes
# expected: pre-flight OK, migrations 018..022 already applied
# (skipped); TRUNCATE on empty tables (no-op); post-flight OK
# Exit code: 0; total wall time < 5 s.
```

After this walkthrough, the DB is staged for slice 144's daemon
refactor. Slice 144 starts the daemon and watches `data_gaps` /
bar tables fill back up.

## Risk Assessment

### Technical Risks

**R1. The TRUNCATE transaction takes longer than expected, locks bar
tables, and impacts a concurrent backtest read.**

Mitigation: this is a destructive operator command, run during a
maintenance window. Document in the CLI help and in the operator
runbook (slice 145+ era) that backtests should not be running during
the cold-start. Not blocking; runtime is single-digit seconds for
TRUNCATE on tables of this size in TimescaleDB (chunk drop is fast).

**R2. The `data_status` view's CTE plan degrades at universe scale we
haven't tested.**

Mitigation: verification step 5 includes EXPLAIN ANALYZE. If the plan
shows per-row function calls, the migration is iterated before merge.
Risk is bounded — we've already specified the CTE pattern in arch.

**R3. Hidden consumer of `last_success_ts` we missed in code search.**

Mitigation: pyright --strict catches removed-attribute access at type
check; existing tests catch runtime accesses. Project convention is
"delete, don't deprecate" so the removal is total — anything we miss
fails loud at first run, not silently.

**R4. `coverage_gaps` DROP loses the inaugural NVDA seed row.**

Mitigation: documented (D4). Slice 144's first NVDA fetch attempt
reproduces the row in `data_gaps` automatically. The NVDA gap window
is 2024-06-07 → 2024-07-25 — well within slice 144's daily-history
target window — so the recovery is automatic, not best-effort.

## Implementation Notes

### Development Approach

Order of work:

1. Land `manta_trading.constants` module + tests (small, no DB
   surface).
2. Land `FetchStatus` and `LastAttemptOutcome` enums + DTO
   skeletons.
3. Land migrations 018–022 in `migrations/minute.py` with the
   StrEnum-derived CHECK helpers.
4. Land `migrate_cold_start.py` (orchestrator + pre-flight + CLI).
5. Surgical removal of `last_success_ts`, `retry_count`,
   `error_message`, `run_id`, `status` from `state.py`,
   `cli/commands/data.py`, and orchestrators.
6. Delete `src/manta_trading/data/coverage/` package and update
   imports.
7. Migrate `HISTORY_MONTHS` → `MINUTE_HISTORY_MONTHS` import.
8. Run pyright --strict; fix until clean.
9. Run unit + integration test suites; expect deletions to break
   tests that exercised removed columns; rewrite those tests
   against the slimmed shape (or delete the test if the surface
   it covered is gone).
10. Run the verification walkthrough end-to-end against a dev DB
    that has slice 141 applied.

### Special Considerations

- **The migration transaction is large.** Five DDLs + two TRUNCATEs
  + one DROP. Test this against the actual DB shape (33k instruments,
  61M minute bars). Test that pgbouncer / connection pool settings
  don't time out the long transaction.
- **`data_status` view depends on `trading_calendar`.** If
  `trading_calendar` lacks rows for a calendar that some
  `instruments` row references, those instrument rows are silently
  excluded from `data_status` (LEFT JOIN drops would-be-NULL rows
  due to the `JOIN exchange_completed_close` not being a LEFT).
  Decision: make the join `LEFT JOIN exchange_completed_close ec
  ON ec.calendar_id = s.trading_calendar_id` so unknown-calendar
  rows still appear with `target_end_ts = NULL` and health =
  STALE. This is more honest than dropping them silently.
- **Test-DB seeding.** Tests for `data_status` health rules need
  a fixture that seeds a small `instruments` + `trading_calendar`
  + `acquisition_state` + `data_gaps` set. Reuse the integration
  test harness from slice 141.

## Future Work

### FW1: `data_status` materialization

If `mt data status` (slice 145) shows operator-perceptible latency
at full universe scope, materialize the view. Adds a refresh
strategy decision (on every gap write? periodic?) — defer until
measurement justifies. Per arch.

### FW2: View constants as a `current_setting()` GUC

Currently the staleness thresholds, late-bar grace period, and
history horizons are baked into the view's DDL as literals at
migration time. Changing one requires a migration to
`CREATE OR REPLACE VIEW`. A future refinement: store the values in
a small `constants` table; the view reads from it; updating a
constant is a row update. Decision deferred — the current scheme
is honest about what changed (a migration), and constants change
rarely.

### FW3: Tick granularity in `data_gaps`

Per arch §Future work, ticks have a different gap semantic
(sequence ranges, not session timestamps). Slice 200 (initiative)
introduces a separate `tick_gaps` table; `data_gaps` is not
extended. The CHECK on `granularity` excludes `'tick'` for now.

## References

- [140-arch.data-quality-operations.md](../architecture/140-arch.data-quality-operations.md) — initiative architecture; this slice implements §"One control table", §"One status view", §"Slimmed acquisition_state", §"Constants", §"Migration from current state — Step 2".
- [140-slices.data-quality-operations.md](../architecture/140-slices.data-quality-operations.md) — slice plan entry that scopes this slice.
- [141-slice.universe-rebuild-from-eodhd-instruments-schema-migration.md](141-slice.universe-rebuild-from-eodhd-instruments-schema-migration.md) — predecessor slice; this slice's pre-flight verifies its application.
- [data-correctness-architecture.md](../reference/data-correctness-architecture.md) — invariants this initiative closes; this slice does not directly close any invariant but lands the schema that slice 144 uses to close I2 and I3.
