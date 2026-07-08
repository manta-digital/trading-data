---
docType: slice-design
slice: 143-compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable
project: trading
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [142-slice.schema-migration-and-cold-start]
interfaces:
  - 144-slice.daemon-refactor          # consumes current_ca_snapshot + compute_snapshot_id for CA-detection; writes daily_ohlcv
  - 147-slice.data-audit               # Stage A + Stage B both call compute_k_factor
relatedReference: user/reference/data-correctness-architecture.md
dateCreated: 20260501
dateUpdated: 20260501
reviewVerdictsAddressed:
  - 143-review.slice (z-ai/glm-5.1, CONCERNS, F001/F002/F003/F004/F005)
  - 143-review.slice (z-ai/glm-5.1, CONCERNS, F001/F002) — second pass
status: complete
---

# Slice Design: 143 — `compute_k_factor` Single Source of Truth + `daily_ohlcv` Hypertable

## Overview

Two related deliverables that together close invariant **I1 — Adjustment
correctness** from `data-correctness-architecture.md`:

1. Promote the existing pure `k_factor(symbol, target_date, splits,
   dividends, prev_closes) -> Decimal` in
   `src/manta_trading/data/adjustment/k_factor.py` to a true single source of
   truth: rename to `compute_k_factor`, accept the canonical `ca_snapshot`
   shape from arch §"`ca_snapshot` shape", add the `compute_snapshot_id`
   stable-hash function and a `current_ca_snapshot(symbol)` loader, and
   migrate every existing in-tree call site to the new entry point.

2. Create the `daily_ohlcv` hypertable on the timescale DB as a peer to
   `minute_ohlcv` (same column shape including `adj_*` and `k_factor`,
   `chunk_time_interval => INTERVAL '7 days'`), deferred from slice 142
   per the slice 142 design's "Defect 1" note. Once it exists, slice 142's
   `data_status` view picks up the daily branch automatically on the next
   `mt data migrate-cold-start` (migration 021's DO-block branches on
   `to_regclass('daily_ohlcv')`).

Order matters within the slice: **the hypertable lands first** (it's a
prerequisite for slice 144 backfill) but the adjustment-function work is
the load-bearing change. Both are landed in one slice because (a) slice
144's daemon needs both — `compute_k_factor` to write `adj_*`, the
hypertable to write into — and (b) splitting them would yield a slice
that ships zero operator-visible behaviour.

## Value

Three concrete deliverables:

1. **One adjustment function.** After this slice, every code path that
   needs an adjustment multiplier — minute writer, daily writer (slice
   144), Stage A audit, Stage B audit, daemon CA-detection recompute
   (slice 144) — calls `compute_k_factor` with the same `ca_snapshot`
   shape. Today's `minute/writer._attach_adjustment_columns` is the only
   correct caller and it speaks the old positional-arg interface; the
   `verify` and `verify_eod` modules each derive k_factor against
   slightly different inputs. After this slice they all share one
   implementation, so Stage A and Stage B comparisons are meaningful
   instead of comparing apples to oranges.

2. **Stable `snapshot_id`.** A SHA256 hex digest over a canonicalized
   serialization of `(splits, dividends)` that is identical across
   processes and Python restarts. Slice 144 stores this on
   `acquisition_state.last_adjusted_ca_snapshot_id` (column already
   added by migration 019); the daemon detects when adjustments are
   stale by comparing the recomputed snapshot_id against the stored
   one. Without a stable hash this entire mechanism is undefined —
   Python's built-in `hash()` randomizes per process and would silently
   trigger spurious recomputes on every daemon restart.

3. **`daily_ohlcv` hypertable.** Slice 144 fetches the full ~22-year
   EODHD daily history into this table from scratch. Once it exists,
   `data_status` reports daily symbols with real `bars_stored` counts
   and the operator commands work for daily granularity. Closes the
   "table-existence" deferral logged in slice 142's commit `6153da6`.

## Technical Scope

### In scope

- New `current_ca_snapshot(symbol, *, settings: Settings) -> CaSnapshot` helper
  that loads `splits`, `dividends`, and `prev_closes` from the market DB and
  returns a frozen `CaSnapshot` dataclass with `snapshot_id` already computed.
  The function opens and closes its own connection (matching the existing
  `load_adjustment_context` pattern). Failure modes and handling:
  (1) **Market DB unreachable / connection timeout** — `psycopg` raises
  `OperationalError`; the function lets it propagate. Callers (daemon, audit
  command) must handle at the ingest-loop boundary, not inside the loader.
  (2) **Query timeout** — same: propagate. The per-dividend `prev_close`
  round-trip (D3) is bounded at ~90 queries for the longest-tenured symbols;
  each is a single-row lookup on a small table. A blanket per-query timeout
  (set via `options="-c statement_timeout=5s"` in the connection string) is
  an appropriate caller-side guard but is not set by this function.
  (3) **Connection drop mid-query** — `psycopg` raises; propagate.
  (4) **Missing `prev_close` for a dividend ex_date** — log a WARNING (matching
  today's `load_adjustment_context` behaviour) and omit the key from
  `prev_closes`. `compute_k_factor` will raise `KeyError` when a caller tries
  to evaluate k for a target_date where that dividend matters; this surfaces the
  data gap to the caller explicitly rather than silently returning a wrong k.
- New `compute_snapshot_id(splits, dividends) -> str` per arch
  §"`snapshot_id` computation" — canonicalized JSON + SHA256 hex.
- Rename `k_factor` → `compute_k_factor` and accept either the
  positional-arg form (existing) or a `CaSnapshot` instance. The new
  preferred call is `compute_k_factor(symbol, target_date,
  ca_snapshot=...)`; the positional form is retained for the small
  number of test fixtures that pass synthetic split/dividend lists
  directly. Both go through the same internal implementation; behaviour
  is unchanged.
- Replace each in-tree call site with the new shape (see "Migration
  plan" below).
- New migration `023_daily_ohlcv` on the minute (timescale) track that
  creates `daily_ohlcv` with the full minute-peer shape and converts it
  to a hypertable with `chunk_time_interval => INTERVAL '7 days'`. After
  the table exists, the migration also creates the unique
  `(symbol, time)` index that mirrors `minute_ohlcv`'s migration 011.
- After 023, re-run migration 021 (`data_status_view`) so the view
  picks up the with-daily branch. Implementation choice: add a small
  follow-on migration `024_data_status_view_refresh` whose body is
  exactly the same `DO $$ ... to_regclass('daily_ohlcv') ...` block as
  021. Re-running 021 in place would require its `id` to change, which
  the migration runner forbids; a separate `024` is the cleanest path.
- Update `src/manta_trading/data/adjustment/__init__.py` to export
  `CaSnapshot`, `compute_k_factor`, `compute_snapshot_id`,
  `current_ca_snapshot`. Keep the legacy `k_factor` name as an alias
  re-export for one slice (deprecated, removed in slice 144 after the
  daemon refactor lands).

### Out of scope (explicit non-goals)

- Daemon CA-detection logic — that is slice 144 and depends on this
  slice's outputs.
- Band-based UPDATE writes for `adj_*` columns — slice 144.
- Refetching daily history into `daily_ohlcv` — slice 144's daily
  backfill path.
- Migrating `dailyOHLCVAdjusted` rows into `daily_ohlcv`. Per project
  memory `project_av_daily_close_semantics.md` and the slice 142
  context note, the legacy table is left in place as backtest history;
  slice 144 refetches the full EODHD history into the new table from
  scratch.
- Any modification to the `splits` / `dividends` schema on the market DB.

## Cross-slice Dependencies and Interfaces

### Inputs

- Slice 142 must be applied (migrations 018–022 present, slimmed
  `acquisition_state` with `last_adjusted_ca_snapshot_id` column). Confirmed
  applied on the dev DB per the session context restore.
- `splits` and `dividends` tables on the market DB (slice 127) — read by
  `current_ca_snapshot` and unchanged by this slice.
- `dailyohlcvadjusted` on the market DB — read by `current_ca_snapshot`
  for `prev_close` lookup. **Not** written; the legacy table is
  untouched.

### Outputs consumed by later slices

- **Slice 144** calls `current_ca_snapshot(symbol, settings=settings)`
  once per daemon cycle per symbol, hashes via `compute_snapshot_id`,
  compares against `acquisition_state.last_adjusted_ca_snapshot_id`,
  and on mismatch runs the band-based UPDATE algorithm using
  `compute_k_factor` per band. Slice 144 also writes daily bars into
  `daily_ohlcv` using a COPY+staging pattern that mirrors the existing
  minute writer.
- **Slice 147** (`mt data audit`) Stage A and Stage B both call
  `compute_k_factor` with
  `ca_snapshot = current_ca_snapshot(symbol, settings=settings)`.
  Stage A verifies stored `adj_close` against it; Stage B compares its
  output to EODHD's published `adjusted_close / close`.

### Interface contract: `CaSnapshot`

```python
@dataclass(frozen=True)
class CaSnapshot:
    symbol: str
    splits: tuple[Split, ...]              # tuple, not list — prevents accidental mutation
    dividends: tuple[Dividend, ...]
    prev_closes: dict[date, Decimal]       # mutable dict; see note below
    snapshot_id: str                       # SHA256 hex, never empty
```

Frozen dataclass; `tuple` not `list` for the CA collections so
accidental mutation of the canonical inputs fails loudly. `prev_closes`
stays a plain `dict` because it's looked up by date inside the k_factor
inner loop and the lookup pattern `prev_closes[ex_date]` is idiomatic.
**`CaSnapshot` instances are not hashable** — the `dict` field prevents
it. No caller uses `CaSnapshot` as a dict key or set member; the
`frozen=True` is present to prevent field reassignment, not to enable
hashing. `snapshot_id` is computed at construction time by
`compute_snapshot_id`, never `None`, never recomputed.

## Technical Decisions

### D1 — Rename `k_factor` → `compute_k_factor`

Arch §"One adjustment function" names the canonical entry point
`compute_k_factor`. The existing module-level function is named
`k_factor`. We rename to match the arch (and to make every call site a
verbatim grep target). The legacy name is re-exported from
`adjustment/__init__.py` as a deprecated alias for one slice; the
alias is deleted in slice 144 once daemon code is rewritten. Module
filename stays `k_factor.py` — renaming the file would churn imports
across the codebase for no semantic gain; the function name is what
the arch contract is about.

### D2 — `CaSnapshot` is loaded once per ingest pass per symbol

This matches the existing `AdjustmentContext` ergonomics in
`adjustment/context.py`: load once, reuse across every chunk for that
symbol. The new `current_ca_snapshot(symbol)` is a thin wrapper that
returns a `CaSnapshot` (the new public name) by reading the same three
sources as today plus computing `snapshot_id` at construction time.
`AdjustmentContext` is renamed to `CaSnapshot` in place; the old name
is re-exported as an alias for one slice and removed in slice 144.

### D3 — `prev_closes` lookup keeps the per-dividend round-trip

The current `load_adjustment_context` issues one `SELECT close FROM
dailyohlcvadjusted WHERE date < ex_date ORDER BY date DESC LIMIT 1`
per dividend. For a 40-year-old stock with ~90 dividends, that's 90
trips. We keep this pattern: it's correct, it's small per symbol, and
batching it (e.g. one query with all ex_dates and a window function)
is a measurement-driven follow-up, not a correctness requirement.
Slice 147's audit will exercise the cumulative cost across the
universe; if it's a bottleneck there, optimize then. **Don't preempt.**

### D4 — `snapshot_id` algorithm — `fetched_at` excluded from canonicalization

The arch §"`snapshot_id` computation" includes `fetched_at` in the
canonical tuple. **This slice deviates from that spec** and the arch
document is updated accordingly (see References). Reason: pre-P5
inspection of `src/manta_trading/data/adjustment/ingest.py` confirms
that both `upsert_splits` and `upsert_dividends` set `fetched_at =
NOW()` in the `ON CONFLICT DO UPDATE` clause — meaning every CA ingest
cycle bumps `fetched_at` regardless of whether the underlying
ratio/amount changed. Including `fetched_at` in the hash would cause
`snapshot_id` to change on every ingest run, triggering spurious
band-based recomputes in slice 144's daemon even when no corporate
action actually changed. That defeats the purpose of the mechanism.

The canonical algorithm omits `fetched_at` and keys on CA identity only:

```python
def compute_snapshot_id(
    splits: Iterable[Split], dividends: Iterable[Dividend]
) -> str:
    splits_canon = sorted(
        (s.ex_date.isoformat(), str(s.ratio_to), str(s.ratio_from))
        for s in splits
    )
    dividends_canon = sorted(
        (d.ex_date.isoformat(), str(d.amount))
        for d in dividends
    )
    payload = json.dumps(
        {"splits": splits_canon, "dividends": dividends_canon},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

The `(ex_date, ratio_to, ratio_from)` tuple is a sufficient identity
key for splits: two splits for the same symbol on the same ex_date with
different ratios is a data-quality defect upstream, not a case we design
for. Same for dividends: `(ex_date, amount)` uniquely identifies a cash
dividend. The PK on `splits (symbol, ex_date)` and `dividends (symbol,
ex_date)` enforces this at the DB level.

`Split` and `Dividend` dataclasses do **not** need a `fetched_at` field
added. The `current_ca_snapshot` SELECT does not need to include it.
This simplifies both the data model and the canonicalization.

### D5 — Migration order

```
021 (data_status_view, daily-branch DO-block)   ← slice 142 (applied)
022 (acquisition_state outcome CHECK)           ← slice 142 (applied)
023 (daily_ohlcv hypertable + UNIQUE index)     ← this slice
024 (re-execute data_status_view DO-block)      ← this slice
```

Migration 024's body is bit-identical to 021's. We do NOT modify 021
(applied migrations are immutable per the runner's invariant). 024
exists solely to re-execute the `to_regclass`-branched `CREATE OR
REPLACE VIEW`; on existing dev DBs that ran the cold-start before
slice 143, 024 flips the view to the with-daily variant. On fresh DBs
that run all migrations 018–024 in one go, 024 is a no-op redo of
021's effect — `CREATE OR REPLACE` makes that safe.

Importing `_DATA_STATUS_VIEW_WITH_DAILY` and
`_DATA_STATUS_VIEW_WITHOUT_DAILY` from
`market/schema/migrations/minute.py` for migration 024's body keeps
the SQL definition in exactly one place.

**Latency NFR.** The architecture specifies that `data_status` view
latency stays sub-second at full-universe scope. Migration 024 activates
the with-daily branch, adding a `UNION ALL` against `daily_ohlcv` for
~32k daily symbols. This slice requires that the view remains sub-second
after 024, verified by the step in the walkthrough below. If measurement
shows regression, the fix before merging is a `CREATE INDEX
CONCURRENTLY` on `daily_ohlcv (symbol)` to support the `GROUP BY symbol`
aggregation in `bars_summary`; that index is preferable to
materializing the view, which is explicitly deferred to measurement.

### D6 — `daily_ohlcv` schema mirrors `minute_ohlcv`

Including the post-migration shape (after migrations 010 and 011 on
minute). One CREATE TABLE that goes straight to the final shape:

```sql
CREATE TABLE IF NOT EXISTS daily_ohlcv (
    time         TIMESTAMPTZ     NOT NULL,
    symbol       TEXT            NOT NULL,
    open         NUMERIC(12, 4)  NOT NULL,
    high         NUMERIC(12, 4)  NOT NULL,
    low          NUMERIC(12, 4)  NOT NULL,
    close        NUMERIC(12, 4)  NOT NULL,
    volume       BIGINT          NOT NULL,
    adj_open     NUMERIC(20, 8),
    adj_high     NUMERIC(20, 8),
    adj_low      NUMERIC(20, 8),
    adj_close    NUMERIC(20, 8),
    k_factor     NUMERIC(20, 12),
    adjusted_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

SELECT create_hypertable('daily_ohlcv', 'time',
                         chunk_time_interval => INTERVAL '7 days',
                         if_not_exists => TRUE);

CREATE UNIQUE INDEX IF NOT EXISTS ux_daily_ohlcv_symbol_time
    ON daily_ohlcv (symbol, time);
CREATE INDEX IF NOT EXISTS ix_daily_ohlcv_symbol_time
    ON daily_ohlcv (symbol, time DESC);
CREATE INDEX IF NOT EXISTS ix_daily_ohlcv_time_symbol
    ON daily_ohlcv (time DESC, symbol);
```

`time` for daily bars is the session-open timestamp in UTC (the
arch's convention from §"Schema invariants" — daily bars use the
exchange's official session-open as the canonical timestamp). Slice
144 enforces this on write. `chunk_time_interval => INTERVAL '7 days'`
matches the arch's expected daily chunk granularity (vs. minute's 4
hours): far fewer chunks per symbol-year, cheap maintenance,
sub-second time-range scans for typical read patterns.

`instrument_id` column is **not** included. `minute_ohlcv` has one
(migration 006) but it's nullable and unused — slice 142 didn't add
it to the slim plan. We hold the same line: don't add columns nothing
reads. If slice 144 wants it, slice 144 adds it.

## Migration Plan (Code)

### Files to modify

| File | Change |
|---|---|
| `src/manta_trading/data/adjustment/k_factor.py` | Rename `k_factor` → `compute_k_factor`. Add `compute_snapshot_id(splits, dividends) -> str` (keyed on `ex_date` + ratio/amount only — no `fetched_at`). Add `CaSnapshot` dataclass. Provide a `compute_k_factor(symbol, target_date, *, ca_snapshot)` keyword-arg overload that delegates to the existing positional implementation. `Split` and `Dividend` dataclasses are unchanged — no `fetched_at` field addition needed. |
| `src/manta_trading/data/adjustment/context.py` | Rename `AdjustmentContext` → `CaSnapshot` (re-export old name for one slice). Rename `load_adjustment_context` → `current_ca_snapshot(symbol, *, settings: Settings) -> CaSnapshot`. Extend the SELECTs to include `fetched_at`. Compute and attach `snapshot_id` before returning. |
| `src/manta_trading/data/adjustment/__init__.py` | Export new names; re-export old names with `# deprecated — removed in slice 144` comment. |
| `src/manta_trading/data/adjustment/verify.py` | Replace `k_factor(sym, d, ctx.splits, ctx.dividends, ctx.prev_closes)` call with `compute_k_factor(sym, d, ca_snapshot=ctx)`. |
| `src/manta_trading/data/adjustment/verify_eod.py` | Same shape: caller switches to `compute_k_factor(... ca_snapshot=...)`. The Stage B comparison logic itself does not change in this slice. |
| `src/manta_trading/data/acquisition/minute/writer.py` | `_attach_adjustment_columns`: switch from `k_factor(ctx.symbol, d, ctx.splits, ctx.dividends, ctx.prev_closes)` to `compute_k_factor(ctx.symbol, d, ca_snapshot=ctx)`. No behaviour change — the function still returns the same `Decimal`. |
| `src/manta_trading/market/schema/migrations/minute.py` | Append migrations 023 (`daily_ohlcv`) and 024 (`data_status_view_refresh`) to `MINUTE_MIGRATIONS`. Both use SQL that's idempotent on re-run. |
| `tests/unit/data/adjustment/test_k_factor.py` (and any sibling test files) | Update imports; add tests for `compute_snapshot_id` (cross-process determinism, ordering invariance, `None`-fetched_at stability, Decimal-string stability) and `CaSnapshot` (snapshot_id pre-computed at construction; field reassignment raises `FrozenInstanceError`; `hash()` raises `TypeError` — asserted explicitly so the non-hashable contract is pinned). |
| `tests/unit/market/schema/test_migrations.py` (or equivalent) | Add tests asserting migration 023 produces a hypertable with the expected column list and chunk interval; assert migration 024 selects the with-daily view variant after 023 runs. |
| `tests/integration/data/adjustment/test_current_ca_snapshot.py` | New file. Boots the market DB pool, calls `current_ca_snapshot('AAPL')`, asserts shape + non-empty `snapshot_id`. Skips cleanly without `MT_MARKET_DB_URL`. |

### Behaviour verification before merge

The `_attach_adjustment_columns` migration is the one with non-trivial
risk: it's the only production caller and it must keep producing
identical numeric output. The unit-test surgery for it should be
**add tests, then refactor**: add a test that pins the current output
shape (k-factor decimals per trading-day for a fixture context with
known splits + dividends), then change the call. Diff between
before/after must be zero.

## Verification Walkthrough

This is the demo script the user can run to confirm the slice
delivers what it claims. Run from repo root with the project's
standard env vars (`MT_TIMESCALE_URL`, `MT_MARKET_DB_URL`) pointing at
the dev DB.

### Step 1 — Apply migrations

```
mt data migrate-cold-start --skip-probe --yes
# expect: migrations 023, 024 applied (018-022 already applied per
# slice 142's prior run); destroy-counts loop reports daily_ohlcv: 0
# (newly created, empty); minute_ohlcv: 0 (already empty);
# acquisition_state: 0 (already empty)
```

After this, confirm the hypertable exists and has the expected shape:

```
psql $MT_TIMESCALE_URL -c "\d daily_ohlcv"
psql $MT_TIMESCALE_URL -c "
  SELECT chunk_time_interval
    FROM timescaledb_information.dimensions
   WHERE hypertable_name = 'daily_ohlcv';"
# expect: '7 days'
```

### Step 2 — Confirm `data_status` view picked up the daily branch and meets latency NFR

```
psql $MT_TIMESCALE_URL -c "
  EXPLAIN SELECT * FROM data_status WHERE symbol = 'AAPL';"
# expect: query plan references both daily_ohlcv and minute_ohlcv
# (vs. the slice-142-only plan that referenced only minute_ohlcv)
```

Latency NFR check (full-universe scope, sub-second required):

```
psql $MT_TIMESCALE_URL -c "\timing" -c "SELECT COUNT(*) FROM data_status;"
# expect: << 1000ms even with daily_ohlcv empty (the UNION ALL aggregation
# over an empty table is cheap; the test is that the plan doesn't degrade
# once daily_ohlcv is populated in slice 144).
```

If timing exceeds 1s on the empty table, investigate before merging —
a missing index on `daily_ohlcv (symbol)` is the likely fix (the
`bars_summary` GROUP BY symbol needs it).

### Step 3 — `compute_snapshot_id` is stable across processes

```
python -c "
from manta_trading.data.adjustment import current_ca_snapshot
from manta_trading.config import Settings
s = current_ca_snapshot('AAPL', settings=Settings())
print(s.snapshot_id)
"
# Run the same command in a second shell. Both prints must be identical.
```

Then in pytest:

```
pytest tests/unit/data/adjustment/test_compute_snapshot_id.py -v
# expect: test_stable_across_runs passes (uses subprocess to invoke
# Python again and compares hex digests)
```

### Step 4 — `compute_k_factor` matches EODHD published k

For a small sample (AAPL, MSFT, GOOGL) over the slice 128 dry-run
sample window, fetch EODHD `/eod` for each, compute
`published_k = adjusted_close / close` per session, and assert
`abs(compute_k_factor(sym, d, ca_snapshot=current_ca_snapshot(sym)) -
published_k) < ADJUSTMENT_DRIFT_EPSILON`.

```
pytest tests/integration/data/adjustment/test_eodhd_parity.py -v
# expect: AAPL pass, GOOGL pass, MSFT pass — the MSFT case is the
# regression check for issue #10 (k_factor staleness).
```

### Step 5 — Existing minute writer behaviour is unchanged

```
pytest tests/unit/data/acquisition/minute/test_writer_adj.py -v
# expect: every test that previously passed still passes (the
# refactor is call-site-only; numeric output is identical)
```

### Step 6 — Issue #10 reproduction + resolution

This is the slice's headline regression test. It demonstrates that
the new code reproduces the bug under an artificial stale-snapshot,
then resolves it under the current snapshot:

```
pytest tests/integration/data/adjustment/test_issue_10_msft_staleness.py -v
# Test does:
#   1. Construct an artificial ca_snapshot for MSFT excluding the
#      most recent dividend (simulates pre-MSFT-dividend daemon
#      state). compute_k_factor against this snapshot reproduces the
#      slice 128 dry-run constant 2.09e-3 drift vs. EODHD published k.
#   2. Then call compute_k_factor with the current snapshot.
#      Drift is now < ADJUSTMENT_DRIFT_EPSILON.
# expect: both assertions pass.
```

## Success Criteria

Specific enough to drive the task breakdown.

1. `compute_k_factor`, `compute_snapshot_id`, `current_ca_snapshot`,
   and `CaSnapshot` are exported from `manta_trading.data.adjustment`
   with the signatures specified in this design.
2. Every in-tree call site that previously called `k_factor` (writer,
   verify, verify_eod) now calls `compute_k_factor`. `grep -rn '^from
   .* import k_factor\|^k_factor(' src/` returns only the alias
   re-export line in `__init__.py`.
3. `compute_snapshot_id` returns identical hex digests for the same
   input across separate Python processes (verified by subprocess
   test).
4. `compute_snapshot_id` returns identical hex digests when input
   `splits` / `dividends` iterables are passed in different orders
   (canonicalization invariance). Re-running `compute_snapshot_id` on
   the same CA set after a CA ingest cycle (which bumps `fetched_at`)
   returns the same digest — confirmed by a test that constructs two
   snapshots with different `fetched_at` values on otherwise-identical
   inputs.
5. `compute_k_factor` output for AAPL/MSFT/GOOGL over the slice 128
   dry-run window matches `EODHD_published_k = adjusted_close / close`
   within `ADJUSTMENT_DRIFT_EPSILON`.
6. With an artificially-stale snapshot for MSFT (last dividend
   removed), `compute_k_factor` reproduces the issue #10 ~2.09e-3
   drift; with `current_ca_snapshot('MSFT')` the drift is
   `< ADJUSTMENT_DRIFT_EPSILON`. Both verified in one test.
7. Migration 023 creates `daily_ohlcv` as a TimescaleDB hypertable
   with `chunk_time_interval = '7 days'`, the column list specified
   in D6, and the unique `(symbol, time)` index.
8. Migration 024 re-applies the `data_status` view; on a DB where
   `daily_ohlcv` exists, the view's plan references both
   `minute_ohlcv` and `daily_ohlcv`.
9. The minute writer's `_attach_adjustment_columns` produces
   numerically identical `adj_*` columns before and after the
   refactor for the test fixtures (zero-diff guard test).
10. All pre-existing unit tests still pass (1162 from the slice 142
    baseline, plus the new tests added by this slice).
11. Invariant **I1** from `data-correctness-architecture.md` is now
    closeable: the codebase has exactly one implementation of the
    adjustment-multiplier function, and every writer/verifier path
    routes through it. Final verification of I1 across the live
    universe is slice 147's audit; this slice creates the conditions
    that make that audit meaningful.
12. `data_status` view returns in under 1 second for `SELECT COUNT(*) FROM
    data_status` against the post-migration dev DB (verified by `\timing`
    in Step 2 of the walkthrough). This holds even after the with-daily
    `UNION ALL` branch is activated by migration 024.

## Risks

Two worth recording.

- **R1 — Numeric drift in the rename.** Risk that the
  positional-args-vs-`ca_snapshot` overload accidentally picks up
  different values somewhere (e.g. a stale `prev_closes` dict) and
  silently changes `adj_close` outputs. Mitigation: zero-diff guard
  test (criterion 9) gates the writer change. If the guard test
  passes, behaviour is unchanged by definition.

- **R2 — `fetched_at` semantics — RESOLVED.** Pre-P5 inspection of
  `ingest.py` confirmed that `fetched_at = NOW()` fires on every
  `ON CONFLICT DO UPDATE`, not only on genuine CA changes. The risk
  was real. Resolution: `fetched_at` is excluded from the
  canonicalization (see D4). The arch doc is updated to match.

## Future Work (out of scope, but worth recording)

- **Batch `prev_closes` lookup.** If slice 147's audit shows
  per-symbol audit time dominated by the per-dividend round-trip, swap
  `current_ca_snapshot` to a single window-function query. Don't
  preempt — measure first.
- **Drop the `k_factor` and `AdjustmentContext` aliases.** Slice 144's
  daemon refactor is the natural place to delete them since it
  rewrites every remaining caller anyway.

## References

- `project-documents/user/architecture/140-arch.data-quality-operations.md`
  §"One adjustment function", §"`ca_snapshot` shape",
  §"`snapshot_id` computation (stable, cross-process)",
  §"Band-based adjustment writes"
- `project-documents/user/reference/data-correctness-architecture.md`
  §"I1 — Adjustment correctness"
- `project-documents/user/slices/142-slice.schema-migration-and-cold-start.md`
  — for the `daily_ohlcv` deferral context (commit `6153da6`)
- `project-documents/user/tasks/128-tasks.eodhd-catchup-and-production-cutover.md`
  line 395 — issue #10 (MSFT k_factor staleness) original observation
- `src/manta_trading/data/adjustment/k_factor.py` — current
  implementation (already correct math; this slice promotes it to SSOT)
- `src/manta_trading/data/adjustment/context.py` — current loader
  (renamed in this slice)
- `src/manta_trading/market/schema/migrations/minute.py` — where
  migrations 023 and 024 are appended
