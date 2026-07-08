---
docType: slice-design
slice: 145-daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes
project: trading
parent: user/architecture/140-slices.data-quality-operations.md
dependencies:
  - 142-slice.schema-migration-and-cold-start
  - 143-slice.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable
  - 144-slice.trading-sessions-materialization-data-status-view-rewrite
interfaces:
  - 146-slice.ca-detection-bulk-eod                # consumes the daemon cycle this slice introduces
  - 147-slice.mt-data-status                       # benefits from accurate gap rows
  - 148-slice.mt-data-refetch                      # consumes update_data_gaps(force_reset_terminal)
  - 150-slice.rebuild-minute-caggs-adjusted-prices # caggs read adj_* columns this slice writes
relatedReference: user/architecture/140-arch.data-quality-operations.md
dateCreated: 20260502
dateUpdated: 20260503
status: complete
---

# Slice Design: 145 — Daemon Refactor: `data_gaps`-Driven Backfill + Advisory Locking + Band-Based `adj_*` Writes

## Overview

This is the **load-bearing slice** of the 140 initiative. It reopens
the 120-era daemon code and replaces its freshness-heuristic work-queue
with the `data_gaps`-driven backfill loop the architecture specifies.
On the way it lands three concrete invariants:

1. **`data_gaps` is the source of truth** for what's missing. Daemon
   reads from it, updates it via the transactional `update_data_gaps`
   writer, coalesces with `coalesce_data_gaps`. Freshness heuristics
   are gone.
2. **PostgreSQL advisory locks** serialize concurrent writers
   (daemon, refetch, backtest) on `(symbol, granularity)`. Daemon
   holds at most one lock at a time — deadlock-free by construction.
3. **Band-based `adj_*` writes** populate adjusted columns at ingest
   via SQL UPDATEs scoped to ex-date bands. No per-bar Python in the
   hot path. Slice 143's `compute_k_factor` runs once per band, not
   once per bar.

Daily backfill side-effects populate `instruments.first_data_date`
(MIN of returned bars, one-time) and `instruments.delisted_date`
(MAX of returned bars when `delisted_at_eodhd = true`). Slice 141
left these columns NULL with the explicit promise that "slice 145
populates them" — that promise is kept here.

CA-detection drift handling and the bulk-EOD steady-state are
**explicitly deferred** to slice 146. This slice writes correct
`adj_*` on the **initial fetch** of any bar via `current_ca_snapshot`,
but does not detect when a stored `last_adjusted_ca_snapshot_id`
has gone stale relative to the current snapshot. That's a separable
mechanism that layers cleanly on this slice's daemon cycle.

## Value

1. **Health rules become true.** Slice 142's `data_status` view reads
   `data_gaps`. Today the table is empty (no writer). After this slice,
   `data_gaps` reflects reality and `health` (OK / STALE / GAPS /
   FAILED) is meaningful for the first time.
2. **Adjusted prices are correct on day one.** Today, freshly-fetched
   bars have NULL `adj_*` columns until a CA-detection sweep recomputes
   them. After this slice, every bar lands with `adj_*` already
   populated against the current `ca_snapshot`. Backtests don't need
   to know about a "second sweep."
3. **`first_data_date` / `delisted_date` populate.** Universe-at-time-T
   queries (arch §"Universe at time T") become honest: backtests can
   filter their candidate universe through `instruments` lifecycle
   columns and get a survivorship-bias-free answer.
4. **Concurrent operator + daemon work is safe.** Backtest reads,
   daemon writes, and `mt data refetch` (slice 148) writes can all
   run against overlapping or disjoint scopes. Disjoint scopes proceed
   in parallel; overlapping scopes serialize via advisory lock — no
   deadlock, no torn writes, no lock-table contention.

## Non-Goals

- **CA-detection drift** — slice 146 detects when stored
  `last_adjusted_ca_snapshot_id` mismatches current `compute_snapshot_id`
  and re-runs band-based UPDATEs. This slice writes correct `adj_*` on
  initial fetch but does not detect drift.
- **Bulk EOD steady-state** — slice 146 switches the daily daemon to
  `/eod-bulk-last-day/US` once caught up. This slice uses per-symbol
  `/eod` for daily.
- **`mt data status` consumer-side polish** — slice 147. This slice
  ensures the *data* is right; surfacing it is a separate slice.
- **`mt data refetch` operator command** — slice 148. The
  `update_data_gaps(force_reset_terminal=True)` flag is implemented
  here but exercised only by tests until 148 lands.
- **Replacing the existing `freshness.py` modules wholesale.** The
  freshness modules are *consulted* during the migration (see
  Migration Plan below) so we don't lose any operational policy
  hidden in them, but they do not survive into the new daemon. The
  daemon's only "is this stale?" question is now "does `data_gaps`
  have an actionable row in the target window?"
- **Backtest contract enforcement.** Arch §"Backtest contract" describes
  policies (`strict` / `skip-and-mark` / `proceed`); the lock primitives
  this slice ships are sufficient for backtests to use, but no
  backtest-side code lands here.
- **New tables or schema migrations.** Everything this slice needs is
  already on the DB after slices 142–144.

## Inputs

- `data_gaps` table (slice 142): destination for gap rows. Currently
  empty. PK `(symbol, granularity, gap_start, gap_end)`. CHECK
  constraints on `fetch_status`, `granularity`, `gap_end >= gap_start`.
- `acquisition_state` table (slice 142): destination for
  `last_attempt_ts`, `last_attempt_outcome`,
  `last_adjusted_ca_snapshot_id`. Slice 145 writes the first two on
  every fetch attempt. The third is set only when this slice writes
  `adj_*` for a chunk (i.e. always on initial fetch, since the daemon
  always has a snapshot when it ingests).
- `trading_sessions` table (slice 144): single source of truth for
  session boundaries. `compute_missing_ranges` reads it.
- `instruments` table (slices 141 + this slice): `first_data_date`,
  `delisted_date` populated as side-effects of daily backfill.
  `first_listing_date` is read but never written here.
- `daily_ohlcv` and `minute_ohlcv` hypertables (slices 143 + 120):
  destination for bar rows + `adj_*` columns.
- `compute_k_factor`, `current_ca_snapshot`, `compute_snapshot_id`
  (slice 143): the adjustment trio. Daemon calls
  `current_ca_snapshot(symbol)` once per symbol-update cycle and
  passes the result to `compute_k_factor` per band.
- `populate_trading_sessions` and `TradingCalendar` (slice 144):
  session-boundary lookups.
- `LATE_BAR_GRACE_PERIOD`, `MAX_RETRY_COUNT`, `MINUTE_HISTORY_MONTHS`,
  `DAILY_HISTORY_MONTHS`, `MAX_GAP_STALENESS` (slice 142 constants):
  no new constants introduced by this slice.

## Outputs

- New module `manta_trading.data.gaps` exposing:
  - `compute_missing_ranges(symbol, granularity, from_ts, to_ts) -> list[GapRange]`
  - `update_data_gaps(symbol, granularity, from_ts, to_ts, fetch_status_for_unfilled, *, force_reset_terminal=False, outcome) -> UpdateResult`
  - `coalesce_data_gaps(symbol, granularity) -> int`
  - `next_trading_session_after(calendar_id, after_date) -> date | None`

  *Note on signature divergence from arch §"Gap function":* the arch's
  `update_data_gaps` signature does not include the `outcome` parameter,
  but step 7 of the algorithm requires writing
  `acquisition_state.last_attempt_outcome` to "the caller's outcome."
  Adding `outcome` to the call surface is the only way to honor that
  step without back-channeling state through globals. Slice 148's
  refetch consumer must pass `outcome` like any other caller; this is
  a strict superset of the arch's documented interface.
- New module `manta_trading.data.locking` exposing:
  - `advisory_lock(conn, symbol, granularity)` — context manager
    wrapping `pg_advisory_xact_lock`. Used by all three gap functions
    and by daemon's per-symbol cycle.
- New module `manta_trading.data.adjustment.band_writer` exposing:
  - `apply_band_updates(conn, table, symbol, range_start, range_end, ca_snapshot)` — given a chunk's range and a snapshot, computes
    band boundaries and issues one UPDATE per band against
    `daily_ohlcv` or `minute_ohlcv`.
- Refactored daemon entry-points:
  - `manta_trading.data.acquisition.daemon.daily.run_daily_cycle()` —
    drives one daily-cycle pass over the universe.
  - `manta_trading.data.acquisition.daemon.minute.run_minute_cycle()` —
    drives one minute-cycle pass.
  - Both consume `data_gaps` directly; the existing
    `minute_work_queue.py` / `work_queue.py` logic is removed.
- `instruments.first_data_date` populated for every symbol on first
  successful daily backfill.
- `instruments.delisted_date` populated for every symbol with
  `delisted_at_eodhd = true` on first successful daily backfill.
- `acquisition_state.last_adjusted_ca_snapshot_id` advanced on every
  ingest chunk's band-write.
- Tests:
  - Unit tests for `compute_missing_ranges`, `update_data_gaps`,
    `coalesce_data_gaps`, band-writer (algorithmic correctness on
    fixtures, no DB).
  - Integration tests for the lock + transactional behavior of
    `update_data_gaps` and `coalesce_data_gaps`.
  - Integration tests driving a full daemon cycle (daily + minute) on
    a small fixture symbol set.
  - Concurrency test: two daemon-like callers on disjoint and
    overlapping scopes; assert serialization on overlapping, parallel
    on disjoint, no deadlock either way.

## Approach

The slice is large but not architecturally controversial — the arch
document specifies most of the algorithms in concrete pseudocode (see
references). The work is **disciplined implementation**, not novel
design. The decisions left to this slice are:

### Decision A: New gap-function module location

`compute_missing_ranges` reads `trading_sessions`, `instruments`, and
the data table. `update_data_gaps` writes `data_gaps` +
`acquisition_state`. `coalesce_data_gaps` rewrites `data_gaps`. None
of them belong inside the daemon — they're called by the daemon, by
`mt data refetch` (slice 148), and by the backtest read-path.

**Decision: new package `manta_trading.data.gaps`** with one file per
function. The package has no dependency on the daemon. The daemon
imports from it.

Rejected alternatives:
- Putting them in `manta_trading.data.acquisition.daemon.gaps` —
  couples to daemon, makes the backtest path import from `daemon`
  which is upside-down.
- Inlining them in `data_gaps.py` next to the table-DDL helpers —
  the DDL lives in `manta_trading.market.schema.migrations.minute`
  (a migrations module), and pulling in business logic there
  conflates two concerns.

### Decision B: Band-writer scope

Slice 143 specifies `compute_k_factor` and `current_ca_snapshot`. The
band-writer is the consumer that turns those into SQL UPDATEs. Two
candidate locations:

- **Inside the `acquisition.minute.writer` / `acquisition.daily.writer`** —
  same module that does the COPY+INSERT for the bars themselves. The
  band-write is a follow-on UPDATE in the same transaction.
- **A new `manta_trading.data.adjustment.band_writer`** — pure
  function over (conn, table, range, snapshot), called by both
  daily and minute writers post-insert.

**Decision: new `band_writer` module.** Rationale:

- The algorithm is identical for daily and minute; one
  implementation, two callers.
- Slice 146's CA-detection re-run path also calls the band-writer
  (over `[min(changed_ca.ex_date), now()]`), with a different bar
  range but the same band-iteration logic. Centralizing it now
  prevents 146 from duplicating it.
- Keeps the writers focused on raw-bar persistence; `adj_*` is a
  separate concern with its own test surface.

### Decision C: Lock-acquisition idiom

Three options for the advisory-lock primitive:

- `pg_advisory_xact_lock(key)` — auto-released at txn end. Forces
  every locked region to be a transaction. Simplest semantics.
- `pg_advisory_lock(key)` + explicit `pg_advisory_unlock(key)` —
  caller controls release. Lock can span transactions.
- `pg_try_advisory_xact_lock(key)` — non-blocking; returns false
  if held. Lets callers decide their wait policy.

**Decision: `pg_advisory_xact_lock` (transaction-scoped) for daemon
and refetch; `pg_try_advisory_xact_lock` for the backtest read path
when it wants to fail fast on contention.** Rationale:

- Auto-release on commit/rollback eliminates a category of bugs
  ("forgot to release on exception path"). The arch's
  `update_data_gaps` is already specified as a single transaction.
- Backtests typically want a deterministic "either I get the lock
  immediately or I report contention to the operator" behavior;
  the `try_` variant gives them that without timing assumptions.
- The 64-bit lock key is computed as `hashtextextended(symbol ||
  '|' || granularity, 0)` — deterministic, collision-tolerant at
  our scale (≤ 100k unique scopes), and matches PG's documented
  pattern for app-level keys.

### Decision D: Daemon main-loop shape

The arch's pseudocode (lines 660–676) specifies the loop:

```
for (symbol, granularity) in scheduled_work:
    acquire_lock(symbol, granularity)
    try:
        do_work(symbol, granularity)
    finally:
        release_lock(symbol, granularity)
```

Open question: how is `scheduled_work` ordered? Two candidate
policies:

- **Most-stale-first**: order by `acquisition_state.last_attempt_ts
  ASC NULLS FIRST`. Symbols that haven't been touched in longest
  go first. Fair.
- **Universe-traversal**: stable order over `instruments` (e.g.
  alphabetical). Predictable, easy to checkpoint.

**Decision: `ORDER BY last_attempt_ts ASC NULLS FIRST, symbol ASC`.**

This single clause covers three regimes correctly:

1. **Cold start (clean DB):** every row has `last_attempt_ts = NULL`,
   so the secondary sort `symbol ASC` drives the order. Predictable,
   alphabetical, easy to checkpoint mentally.
2. **Cold start interrupted and resumed:** symbols already touched
   have `last_attempt_ts != NULL`; untouched symbols still have NULL.
   `NULLS FIRST` ensures the daemon **finishes the universe walk
   before revisiting any symbol**. This is what we want — get every
   symbol seen at least once before re-fetching anyone, even if some
   of the touched ones had transient failures.
3. **Steady state (universe fully touched):** every row has a real
   timestamp. `last_attempt_ts ASC` puts the staleest first. Fair.

The trade-off worth naming: in regime 2, transient-failure rows from
the prior partial cycle are not retried until the cold-start completes.
At single-operator scale and with `MAX_RETRY_COUNT` bounded, this is
acceptable; the operator can run `mt data refetch` if they want to
prioritize a known-failing symbol. The alternative (NULLS LAST) would
favor retrying recent failures over completing the cold start, which
delays the universe becoming whole and makes `data_status` health
results harder to reason about.

### Decision E: Per-cycle batching vs. per-symbol commit

Each (symbol, granularity) update is one transaction (per arch).
But a daemon cycle covers thousands of symbols. Two options:

- One transaction per symbol — N small transactions per cycle.
- One transaction per cycle — one big transaction wrapping everything.

**Decision: one transaction per (symbol, granularity).** The arch
specifies it (line 536: "Runs in a single transaction. Acquires
PostgreSQL advisory lock"). Reason: a long-running transaction holds
locks for the duration, blocks other writers, and rollback on a
single-symbol failure would unwind successful symbols. Per-symbol
transactions confine failure to one symbol.

### Decision F: Outcome classification

`last_attempt_outcome` is the enum `'success' | 'partial' | 'empty'
| 'transient_failure'`. Daemon must classify each fetch attempt.
Rules (codified once in `manta_trading.data.acquisition.outcomes`):

- HTTP 5xx, network timeout, rate-limit 429 → `transient_failure`.
- HTTP 4xx (other than 429) → caller's bug or vendor change. Raise,
  do not classify. (Crash the daemon worker; orchestrator restarts.)
- HTTP 200, response empty list / no bars in range → `empty`.
- HTTP 200, response covers part of requested range → `partial`.
- HTTP 200, response covers full requested range → `success`.

The mapping `last_attempt_outcome → fetch_status_for_unfilled` is
fixed by the arch (lines 591–599 of arch). Daemon never invents a
mapping — it computes the outcome and passes it to
`update_data_gaps`, which applies the table.

## Failure Modes

Concrete handling for every new I/O path. Anything not enumerated
here is a bug if it surfaces in production.

### EODHD HTTP

| Failure | Detection | Classification | Action |
|--|--|--|--|
| Connect timeout (>10s to establish TCP) | `httpx.ConnectTimeout` | `transient_failure` | record outcome; daemon proceeds to next symbol |
| Read timeout (>60s for `/eod`, >30s for minute chunk) | `httpx.ReadTimeout` | `transient_failure` | same |
| Connection reset / mid-stream disconnect | `httpx.ReadError`, `httpx.RemoteProtocolError` | `transient_failure` | same |
| HTTP 5xx | response status | `transient_failure` | same |
| HTTP 429 (rate-limit) | response status | `transient_failure` | same; supervisor backs off the cycle (separate slice) |
| HTTP 4xx (other than 429) | response status | **uncaught — raise** | crash the symbol-update; orchestrator logs and proceeds. Indicates vendor schema change or our bug. |
| HTTP 200, body fails `response.json()` | `ValueError` from psycopg/orjson decoder | `transient_failure` | record outcome; the body shape is unusable, retry next cycle |
| HTTP 200, body is `{"error": "..."}` (EODHD quirk) | body-shape check in `classify_outcome` | `transient_failure` | record outcome; behavior matches body-shape spec |
| HTTP 200, valid JSON, empty list | body-shape check | `empty` → `PROVIDER_HOLE` | record per arch outcome→fetch_status mapping |
| HTTP 200, valid JSON, partial range coverage | body-shape check | `partial` → `UNKNOWN` | record; daemon retries unfilled ranges |
| HTTP 200, valid JSON, full range coverage | body-shape check | `success` | record; range covered |

Timeouts are configured on the `httpx.Client` instance: `httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0)`. The
read timeout is overridden to 30s for minute-chunk fetches. These
values are not new constants — they live with the EODHD client
construction in `manta_trading.data.acquisition.providers` (or
wherever the existing client lives, per the migration walk in
Step 1).

### PostgreSQL advisory lock

Two callers, two policies:

- **Daemon**: `pg_advisory_xact_lock(key)` is wrapped by setting
  `lock_timeout = '30s'` at the start of the transaction. If the
  lock isn't acquired in 30 seconds, PG raises `lock_not_available`
  (SQLSTATE `55P03`); the daemon classifies this as
  `transient_failure` and proceeds to the next symbol. The locked-out
  symbol returns to the queue on the next cycle.
- **Backtest read-path** (future, slice not numbered): uses
  `pg_try_advisory_xact_lock(key)` for fail-fast — returns immediately
  if contended. The backtest decides whether to wait or surface the
  contention to the operator.
- **`mt data refetch`** (slice 148): operator-initiated and operator-
  scoped; uses plain `pg_advisory_xact_lock` with no timeout. The
  operator wants the answer and will wait or Ctrl-C.

The 30s daemon timeout is a constant
`DAEMON_LOCK_TIMEOUT = '30 seconds'` in
`manta_trading.constants` (new constant — added by this slice).

### PostgreSQL transactional writes

`update_data_gaps` and `apply_band_updates` each run in a single
transaction (decision E). Failure modes:

| Failure | Detection | Behavior |
|--|--|--|
| Connection drop mid-txn | psycopg raises `OperationalError` | PG rolls back the entire transaction atomically. No partial state. Daemon's outcome classifier records `transient_failure` on the cycle. Next cycle's `compute_missing_ranges` sees the unchanged DB state and re-attempts. |
| Constraint violation (e.g. CHECK on `fetch_status`) | psycopg raises `IntegrityError` | **uncaught — raise**. Indicates a code bug or schema drift. Crash the symbol-update. |
| `lock_timeout` exceeded waiting for advisory lock | psycopg raises `OperationalError` SQLSTATE `55P03` | Caught at the cycle boundary; classified as `transient_failure`. Per the lock section above. |
| Statement-level deadlock with internal PG operation | psycopg raises `DeadlockDetected` SQLSTATE `40P01` | Rare; PG has chosen a victim. Caught at the cycle boundary; classified as `transient_failure`. |
| Long-running migrations / vacuum holding conflicting locks | n/a — we don't run DDL concurrent with daemon | Operational policy: pause daemon before running `mt data migrate-cold-start`. Slice 142's command is a one-shot anyway. |

Atomicity guarantee — explicit: the daemon's per-symbol transaction
is the unit of recovery. Either the entire write set commits
(`data_gaps` rows updated, bars adjusted, `acquisition_state` row
advanced) or none of it does. There is no partially-adjusted state
to recover from. Slice 146's CA-detection drift loop will re-cover
any range whose `last_adjusted_ca_snapshot_id` doesn't match
current — that's the cleanup path for any "I crashed before the
band-write committed" case anyway.

### Daemon supervisor (out of scope)

A daemon supervisor that catches per-symbol crashes and continues
with the next symbol is **out of scope** for this slice. The
single-cycle command (`mt data daemon daily --once`) crashes the
process on any uncaught exception. Continuous-daemon mode (with
supervisor + restart-on-crash) is a follow-up slice.

In practice, the only path that raises uncaught from this slice
is "HTTP 4xx other than 429" or "constraint violation" — both
indicate either a vendor schema change or a bug we want to know
about, not transient noise to swallow.

## Non-Functional Targets

These are inherited from the parent architecture (§"Refetch" and
EODHD-quota expectations) and restated here so the slice is
self-contained for verification.

**EODHD rate limits (relevant constants):**

- **1,000 requests / minute** — instantaneous rate ceiling.
- **100,000 requests / day** — rolling 24-hour cap.

A common confusion worth heading off: "100,000 / day ÷ 1,440 minutes
= ~70 req/min" (or "~14 req/min" if dividing only over a single
8-hour trading window) is the *average* sustainable rate if you
spread the daily budget evenly. It is **not** a per-minute ceiling.
We can burst at 1,000/min for the duration of a backfill, then idle.

**Slice-level targets:**

- **Daily backfill end-to-end (cold start, full universe ~57k
  symbols, single cycle)**: ≤ 75 minutes wall-clock. At EODHD's
  1,000/min ceiling, 57k calls is a 57-minute floor; 75 minutes gives
  ~30% headroom for backoff on transient failures and per-symbol
  processing overhead (k_factor compute + band-write + gap update).
  Arch's published estimate is ~57 minutes against the same ceiling.
  Verifiable on a TRUNCATEd `daily_ohlcv` with
  `mt data daemon daily --once` against the active universe.
- **Minute backfill end-to-end (per-symbol, single symbol with
  full `MINUTE_HISTORY_MONTHS` window)**: ≤ 5 minutes wall-clock for
  a typical symbol (matches arch's ~4-minute estimate with modest
  headroom). Per-symbol minute history at 120-day chunks ≈ 6 chunks
  for `MINUTE_HISTORY_MONTHS = 24`; six per-symbol calls is well
  inside any rate window. Verifiable with a single-symbol cycle on
  TRUNCATEd `minute_ohlcv`.
- **EODHD daily-quota footprint**: cold-start daily costs one `/eod`
  per symbol (~57k calls). At 100k/day this leaves ~43k for the same
  day's minute work — enough for ~7,000 symbols' minute history at
  6 chunks each. Operator policy for full cold-start: do daily
  cold-start one day, minute cold-start another, or accept that
  minute cold-start spans 2–3 days at 100k/day. Steady-state is
  bulk-EOD (slice 146) for daily + ~6 chunks per active minute
  symbol per cycle, well inside quota.
- **`update_data_gaps` p99 wall-clock per call**: ≤ 200ms at
  current scale. The slowest path is the snapshot read +
  `compute_missing_ranges` for a multi-year minute window;
  measurable via `pg_stat_statements`. If exceeded in production,
  investigate the snapshot read query plan first.
- **Lock contention**: at single-operator scale (one daemon,
  occasional refetch, occasional backtest), expected contention
  rate is < 1% of daemon transactions. The 30s `lock_timeout`
  gives plenty of room before symbols start cycling out as
  transient failures.

These are planning targets, not strict SLAs — the arch frames them
the same way. They exist so a regression that doubles backfill time
gets caught at verification, not in production.

## Cross-Slice Dependencies

- **Slice 142** (complete): `data_gaps`, `acquisition_state`,
  `data_status` view all in place. `MAX_RETRY_COUNT`,
  `LATE_BAR_GRACE_PERIOD`, `MAX_GAP_STALENESS` constants in
  `manta_trading.constants`.
- **Slice 143** (complete): `compute_k_factor`, `current_ca_snapshot`,
  `compute_snapshot_id`. Daily hypertable `daily_ohlcv` exists with
  `adj_*` and `k_factor` columns.
- **Slice 144** (complete): `trading_sessions` populated. `TradingCalendar`
  reads from it. `populate_trading_sessions` is shared with the
  refetch CLI but not called from this slice's daemon.
- **Slice 146** (downstream, depends on this): adds CA-detection
  drift loop and bulk-EOD steady-state. Both layer on this slice's
  per-symbol cycle plumbing — adding ~20 lines to the daemon's
  cycle entry-point and a new SQL helper.
- **Slice 147** (downstream): `mt data status` reads `data_status`,
  which finally has meaningful `health` because `data_gaps` is
  populated.
- **Slice 148** (downstream): `mt data refetch` calls
  `update_data_gaps(force_reset_terminal=True)`. The flag is
  shipped here; the consumer is 148.
- **120-arch dependency**: this slice reopens the 120-era daemon
  code in `manta_trading.data.acquisition.daemon.{daily,minute}`. The
  existing `freshness.py`, `work_queue.py`, and `minute_work_queue.py`
  modules in `acquisition.daemon` are deleted. `acquisition.minute.freshness`
  and `acquisition.daily.freshness` (the per-granularity freshness
  helpers in those subpackages) are also removed.

## Migration Plan

The 120-era daemon has accumulated logic that needs auditing before
deletion. The migration is in three steps within this slice:

### Step 1 — Reconnaissance (no code change)

Walk every module in `acquisition/daemon/`, `acquisition/daily/`,
and `acquisition/minute/` that this slice will touch or delete.
For each, list:

1. Module's purpose in 120-arch.
2. What it reads (DB tables, APIs, files).
3. What it writes (DB tables, side effects).
4. Public callers (anything outside `acquisition/`).
5. Whether the new daemon path covers the same behavior (yes/no/partial).

Record the audit as a comment block at the top of T1's task file (not
a doc — internal scratch). If any module has a "no/partial" answer,
stop and confer with the project manager before proceeding.

### Step 2 — Build the new daemon path alongside the old

New code lands in:
- `manta_trading.data.gaps.{compute_missing_ranges, update_data_gaps, coalesce_data_gaps, next_trading_session_after}`
- `manta_trading.data.locking.advisory_lock`
- `manta_trading.data.adjustment.band_writer.apply_band_updates`
- `manta_trading.data.acquisition.outcomes.classify_outcome`
- `manta_trading.data.acquisition.daemon.daily.run_daily_cycle` (new
  function, not the existing `daily.py`)
- `manta_trading.data.acquisition.daemon.minute.run_minute_cycle` (new
  function, not the existing `minute.py`)

The existing `daily.py` and `minute.py` daemon entry-points are not
yet wired to the new functions. Tests run against `run_daily_cycle`
and `run_minute_cycle` directly.

### Step 3 — Switch the daemon orchestrator + delete the old path

`manta_trading.data.acquisition.orchestrator` (or wherever the daemon
is started) is repointed to call `run_daily_cycle` /
`run_minute_cycle`. The previously-listed modules are deleted in one
commit:

- `acquisition/daemon/work_queue.py`
- `acquisition/daemon/minute_work_queue.py`
- `acquisition/daemon/symbol_sources.py` (if no remaining caller)
- `acquisition/daily/freshness.py`
- `acquisition/minute/freshness.py`
- The 120-era `acquisition/daemon/daily.py` and `daemon/minute.py`
  bodies are replaced (file paths kept; bodies become thin shims that
  call the new `run_*_cycle`).

Tests that imported from deleted modules are removed (the new tests
cover the same behaviors via the new functions).

### Behavior verification at the migration boundary

A short comparison run:

1. On a small fixture symbol set, drive the **old** daemon path one
   cycle. Capture `data_gaps`, `acquisition_state`, and bar counts.
2. TRUNCATE everything, drive the **new** daemon path one cycle on
   the same fixture.
3. Diff the resulting state. Expectation: the new path produces
   `data_gaps` rows where the old path produced nothing (the old
   path didn't write `data_gaps`), `acquisition_state` shape is
   different (no `last_success_ts` etc. — expected per slice 142),
   and bar counts are within ±1 of each other (the new path's
   chunk-boundary handling may differ slightly on edge cases).

This is a sanity check, not a regression suite. Codified
divergences are noted in T-level tasks.

## Data Flows

### Daily cycle (new daemon path)

```
for symbol in instruments_active(ordered_by_staleness):
  with advisory_lock(symbol, 'daily'):
    snap = current_ca_snapshot(symbol)
    target_end = trading_sessions.most_recent_completed_session_close_utc(calendar_id)
    target_start = max(first_listing_date or earliest_seeded, target_end - DAILY_HISTORY_MONTHS)

    response = eodhd_eod(symbol, output_size='full')
    bars = parse(response)
    outcome = classify_outcome(bars, response, target_start, target_end)

    with txn:
      copy_into(daily_ohlcv, bars)
      apply_band_updates(daily_ohlcv, symbol, target_start, target_end, snap)

      if instruments.first_data_date IS NULL:
        UPDATE instruments SET first_data_date = MIN(date) WHERE symbol = ?
      if instruments.delisted_at_eodhd AND instruments.delisted_date IS NULL:
        UPDATE instruments SET delisted_date = MAX(date) WHERE symbol = ?

      update_data_gaps(symbol, 'daily', target_start, target_end,
                        fetch_status_for_unfilled=map[outcome],
                        outcome=outcome)
      acquisition_state.last_adjusted_ca_snapshot_id = snap.snapshot_id
```

### Minute cycle (new daemon path)

```
for symbol in instruments_active(ordered_by_staleness):
  with advisory_lock(symbol, 'minute'):
    snap = current_ca_snapshot(symbol)
    target_end = trading_sessions.most_recent_completed_session_close_utc(calendar_id)
    target_start = max(first_data_date, today - MINUTE_HISTORY_MONTHS)

    # Initial recompute (idempotent — re-running on caught-up state is a no-op)
    update_data_gaps(symbol, 'minute', target_start, target_end,
                      fetch_status_for_unfilled='UNKNOWN', outcome='partial')

    while True:
      gap = pick_most_recent_actionable_gap(symbol, 'minute', target_start, target_end)
      if gap is None: break
      chunk_range = (max(gap.gap_start, gap.gap_end - PROVIDER_MAX_CHUNK), gap.gap_end)
      response = eodhd_minute(symbol, chunk_range)
      bars = parse(response)
      outcome = classify_outcome(bars, response, *chunk_range)
      with txn:
        copy_into(minute_ohlcv, bars)
        apply_band_updates(minute_ohlcv, symbol, chunk_range[0], chunk_range[1], snap)
        update_data_gaps(symbol, 'minute', *chunk_range,
                          fetch_status_for_unfilled=map[outcome], outcome=outcome)
        acquisition_state.last_adjusted_ca_snapshot_id = snap.snapshot_id

    coalesce_data_gaps(symbol, 'minute')
```

### Gap-function call graph

```
daemon ──┬──> update_data_gaps ──> compute_missing_ranges
         ├──> coalesce_data_gaps ──> next_trading_session_after
         └──> apply_band_updates ──> compute_k_factor (slice 143)

mt data refetch (slice 148) ──> update_data_gaps(force_reset_terminal=True)
                            └──> coalesce_data_gaps

backtest read-path ──> update_data_gaps (only when MAX_GAP_STALENESS exceeded)
                   └──> read data_gaps
```

## Risks

- **Concurrency bugs at the lock boundary.** The arch's discipline
  ("daemon holds at most one lock at a time") is correct only if
  enforced. Mitigation: a runtime assertion in the daemon's lock
  acquisition path that checks no other lock is currently held by
  this connection. Remove the assertion when stable; keep it as a
  debug-mode toggle.
- **Band-writer correctness on edge cases.** Ex-dates that fall on
  non-trading days, or chunks whose range starts exactly on an
  ex-date, or chunks containing zero ex-dates — each is a different
  band-iteration path. Mitigation: parameterized unit tests across
  all four edge cases (zero / one / many bands, leading-edge ex-date,
  trailing-edge ex-date) using fixtures, not the live DB. The arch's
  pseudocode (lines 386–426) is the source of truth.
- **Migration leaves orphaned dead code.** Easy to delete the daemon
  modules but miss their callers. Mitigation: Step 1's reconnaissance
  walk produces an explicit caller list; the deletion commit must
  account for every caller.
- **`update_data_gaps` performance under contention.** A long
  ingest chunk plus a backtest's `update_data_gaps` recompute on the
  same scope serialize. At single-operator scale this is rare and
  acceptable. Mitigation: `pg_try_advisory_xact_lock` on the backtest
  side returns immediately on contention, letting backtests fail fast
  rather than block.
- **Outcome classification ambiguity.** EODHD sometimes returns 200
  with a JSON error payload (`{"error": "..."}`) instead of a 4xx.
  Mitigation: `classify_outcome` checks the body shape, not just the
  HTTP status. Unit tests pin known EODHD quirks.
- **`first_data_date` write race**. Two concurrent daily backfills of
  the same symbol both want to UPDATE `first_data_date`. The advisory
  lock prevents this, but only if both callers hold the lock. The
  daemon does. Mitigation: assertion in the
  `update_first_data_date` helper that the calling connection holds
  the symbol's daily lock.
- **Existing 120-era state on disk**. The `acquisition_state` table
  has rows from 120-era code. Slice 142 already TRUNCATEd it. If a
  test or operator re-populated it via the old code path between 142
  and this slice, the new daemon's first cycle may surface stale
  outcomes. Mitigation: the migration boundary's TRUNCATE-and-rerun
  step (above) covers this in the test environment; production has
  not been touched by the old daemon since 142.

## Success Criteria

1. **Cold-DB convergence.** Starting from a TRUNCATEd `data_gaps` /
   `acquisition_state` and a populated universe, one daily-cycle pass
   over a fixture set populates `data_gaps` rows for every missing
   session range and `acquisition_state` rows with
   `last_attempt_outcome` set per the classification table.
2. **`first_data_date` populated.** After the first daily-cycle pass
   that includes a successful fetch, `instruments.first_data_date`
   for every fetched symbol equals `MIN(date)` of the bars returned
   for that symbol. The column never goes backwards on subsequent
   cycles.
3. **`delisted_date` populated.** For every symbol with
   `delisted_at_eodhd = true` whose first daily-cycle pass succeeds,
   `instruments.delisted_date` equals `MAX(date)` of the returned
   bars.
4. **Adjusted prices correct on initial fetch.** For a sample symbol
   set (AAPL, MSFT, GOOGL), after the first daily-cycle pass,
   `abs(stored adj_close - close * stored_k_factor) <
   ADJUSTMENT_DRIFT_EPSILON` on every session.
5. **Stage B audit holds.** For the same sample, `abs(stored_k_factor
   - (response.adjusted_close / response.close)) <
   ADJUSTMENT_DRIFT_EPSILON` on every session — the daemon's
   adjustment matches the vendor's published one.
6. **Band-write count is correct.** Minute-chunk insert with zero
   ex-dates in range produces exactly one UPDATE against
   `minute_ohlcv` for `adj_*` (verified by capturing
   `pg_stat_statements` or a connection-level statement counter
   during the test). With N ex-dates in range, exactly N+1 UPDATEs.
7. **Retry promotion to RETRY_EXHAUSTED.** Injecting transient
   failure responses on a chosen symbol's chunk fetch
   `MAX_RETRY_COUNT` times produces a `data_gaps` row with
   `fetch_status = RETRY_EXHAUSTED`. The next daemon cycle excludes
   that row from its actionable list (no further fetches against it).
8. **Daemon never deadlocks.** Two daemon-like callers operating on
   disjoint scopes complete in parallel; on overlapping scopes they
   serialize without deadlock; on the same scope they serialize
   strictly. Verified by integration test using two `psycopg`
   connections.
9. **`coalesce_data_gaps` is idempotent.** Running coalesce twice
   in a row produces zero writes on the second run (assertable via
   the function's return value or `pg_stat_statements`).
10. **`update_data_gaps(force_reset_terminal=True)` resets terminal
    rows.** A `PROVIDER_HOLE` or `RETRY_EXHAUSTED` row in scope
    becomes `UNKNOWN, attempt_count = 0` after one
    force-reset-terminal call. (Consumed by slice 148; tested here.)
11. **Old daemon code is gone.** No remaining import of
    `acquisition.daemon.work_queue`, `minute_work_queue`,
    `daemon.symbol_sources`, `daily.freshness`, or `minute.freshness`
    in the source tree. Search returns zero hits.
12. **`data_status` health is meaningful.** After a daemon cycle on
    the fixture set with an intentionally-missing session for one
    symbol, `data_status` shows `health = GAPS` for that symbol and
    `health = OK` for the others.

## Verification Walkthrough

Run from project root with `MT_TIMESCALE_DB_URL` and
`MT_EODHD_API_KEY` set. Uses the `trading_test` DB.

### 1. Pre-state snapshot

```bash
psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT COUNT(*) AS gaps FROM data_gaps;
  SELECT COUNT(*) FILTER (WHERE last_attempt_ts IS NOT NULL) AS state_rows
    FROM acquisition_state;
  SELECT COUNT(*) FILTER (WHERE first_data_date IS NOT NULL) AS instruments_with_fdd
    FROM instruments;
"
```

Expect (post-cold-start, pre-145): `gaps = 0`, `state_rows = 0`,
`instruments_with_fdd = 0`.

### 2. Drive a daily cycle on a fixture symbol set

```bash
mt data daemon daily --symbols AAPL,MSFT,GOOGL
```

Expect: log lines per symbol with `outcome=success` (or
`outcome=transient_failure` if EODHD is hiccupping); the command
exits with status 0 if all symbols completed (regardless of
per-symbol outcomes); status 1 if any symbol crashed (e.g. an
unclassified HTTP 4xx).

### 3. Verify `data_gaps` populated

```bash
psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT symbol, granularity, fetch_status, COUNT(*) AS rows,
         MIN(gap_start) AS earliest, MAX(gap_end) AS latest
    FROM data_gaps
   WHERE symbol IN ('AAPL','MSFT','GOOGL')
   GROUP BY symbol, granularity, fetch_status
   ORDER BY symbol, granularity, fetch_status;
"
```

Expect: rows present (the daily history goes back ~22 years, with
`PROVIDER_HOLE` rows for any pre-listing range and `success`-resolved
ranges for the populated portion).

### 4. Verify `instruments.first_data_date`

```bash
psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT symbol, first_data_date, delisted_at_eodhd, delisted_date
    FROM instruments
   WHERE symbol IN ('AAPL','MSFT','GOOGL');
"
```

Expect: `first_data_date` populated (typically 1980-12-12 for AAPL,
1986-03-13 for MSFT, 2004-08-19 for GOOGL given EODHD's coverage).
`delisted_date` NULL (these are active symbols).

### 5. Verify `adj_*` columns populated

```bash
psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT symbol, COUNT(*) AS bars,
         COUNT(*) FILTER (WHERE adj_close IS NOT NULL) AS bars_adjusted,
         COUNT(*) FILTER (WHERE k_factor IS NOT NULL) AS bars_with_k
    FROM daily_ohlcv
   WHERE symbol IN ('AAPL','MSFT','GOOGL')
   GROUP BY symbol;
"
```

Expect: `bars_adjusted = bars` and `bars_with_k = bars` for every row
(every bar lands with `adj_*` and `k_factor` populated).

### 6. Stage A consistency check

```bash
psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT symbol, COUNT(*) AS drift_violations
    FROM daily_ohlcv
   WHERE symbol IN ('AAPL','MSFT','GOOGL')
     AND ABS(adj_close - close * k_factor) > 1e-6
   GROUP BY symbol;
"
```

Expect: zero rows (no symbol violates Stage A's tolerance).

### 7. Inject a transient failure and observe retry promotion

Run a short Python harness that monkey-patches the EODHD client to
raise `httpx.TimeoutException` on every minute-chunk fetch for a
chosen symbol. Drive `run_minute_cycle` `MAX_RETRY_COUNT` times.
Then:

```bash
psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT symbol, gap_start, gap_end, fetch_status, attempt_count
    FROM data_gaps
   WHERE symbol = '<test_symbol>'
   ORDER BY gap_end DESC LIMIT 5;
"
```

Expect: at least one row with `fetch_status = RETRY_EXHAUSTED` and
`attempt_count = MAX_RETRY_COUNT`. The next cycle does not fetch
that row.

### 8. Concurrency verification

Two-process integration test (lives in `test/integration/`):

- Process A: holds `advisory_lock('AAPL', 'daily')` and sleeps 5s.
- Process B: attempts `advisory_lock('AAPL', 'daily')`. Records the
  acquisition wall-clock.
- Process B: attempts `advisory_lock('MSFT', 'daily')` (disjoint).
  Records the acquisition wall-clock.

Expect: Process B's AAPL acquisition takes ~5s; its MSFT acquisition
returns < 100ms. No deadlock under any combination.

### 9. `coalesce_data_gaps` idempotency

After step 3, run:

```bash
psql "$MT_TIMESCALE_DB_URL" -c "
  WITH before AS (SELECT COUNT(*) AS n FROM data_gaps WHERE symbol='AAPL')
  SELECT 'before' AS phase, n FROM before;
"
mt data debug coalesce --symbol AAPL --granularity daily   # or: python -c '...'
psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT COUNT(*) FROM data_gaps WHERE symbol='AAPL';
"
```

Expect: row count unchanged (the daemon already coalesced post-loop).
Re-running coalesce is a no-op.

### 10. `data_status.health` reflects reality

```bash
psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT symbol, granularity, health, gap_count, has_retry_exhausted
    FROM data_status
   WHERE symbol IN ('AAPL','MSFT','GOOGL')
   ORDER BY symbol, granularity;
"
```

Expect: `health = OK` (or `GAPS` if vendor-side holes exist in the
range, with `gap_count > 0` and `has_retry_exhausted = false`).

### 11. Old code is gone

```bash
grep -rn "from manta_trading.data.acquisition.daemon.work_queue\|from manta_trading.data.acquisition.daemon.minute_work_queue\|from manta_trading.data.acquisition.daily.freshness\|from manta_trading.data.acquisition.minute.freshness" src/ test/
```

Expect: zero matches.

## Resolved Decisions (formerly Open Questions)

### Daemon CLI surface — `mt data daemon`, not `mt data status`

The single-cycle daemon entry-point ships in this slice as
`mt data daemon daily --once` and `mt data daemon minute --once`.
Slice 147 (`mt data status`) is a status *reader*; the daemon driver
is a separate concern and belongs with the daemon code.

Future continuous-daemon mode (without `--once`) is out of scope here
— this slice ships the single-cycle command and the underlying
`run_*_cycle()` functions; a future slice can add a long-running
supervisor.

CLI surface (final):

```
mt data daemon daily  --once [--symbols X,Y,Z]
mt data daemon minute --once [--symbols X,Y,Z]
```

`--symbols` is for testing and incident response; default is "all
active instruments per the symbol-selector below."

### Backtest `MAX_GAP_STALENESS` wiring — primitives only, defer consumer

Arch §"Backtest contract" describes a backtest read-path that
acquires advisory locks on its declared scope set, checks
`MAX_GAP_STALENESS` against `last_attempt_ts`, refreshes via
`update_data_gaps` if stale, then reads. **All primitives needed
for that path ship in this slice** (`advisory_lock`,
`update_data_gaps`, `coalesce_data_gaps`, the
`pg_try_advisory_xact_lock` variant for fail-fast behavior).

The backtest *consumer* of these primitives — the read-path that
actually invokes them — is deferred. Reasons:

- No backtest framework currently exists in the repo. Wiring the
  consumer requires designing the backtest entry-point first, which
  is its own scope.
- Slice 145 is already 4/5 effort. Adding backtest read-path code
  pushes it past usefully-reviewable size.
- The primitives are testable here via a two-process integration
  test (one process simulating a backtest, one simulating the
  daemon — both real PG connections, real locks, real
  `update_data_gaps` calls). Concurrency invariants are pinned
  without a real backtest.

When the backtest slice lands (in a future 100-band initiative —
not numbered here), it imports from `manta_trading.data.gaps` and
`manta_trading.data.locking` and uses the primitives unchanged.

### Symbol scope per cycle — include delisted-active for one final pass

The cycle's symbol-selector is:

```sql
SELECT symbol FROM instruments
 WHERE (delisted_at_eodhd = false AND delisted_date IS NULL)
    OR (delisted_at_eodhd = true  AND delisted_date IS NULL)
```

i.e. **active OR newly-delisted-without-final-pass**. Once
`delisted_date` populates (this slice's daily-cycle side-effect for
the second branch), the symbol drops out of subsequent cycles. The
selector is one query, no special-case code.

This handles the transition: a symbol marked `delisted_at_eodhd =
true` by the most recent universe rebuild gets exactly one more
daemon pass to populate `delisted_date = MAX(date)` of the bars
EODHD still returns, then is excluded thereafter. Without this,
`delisted_date` would never populate for symbols delisted between
universe rebuilds and the slice's promise to populate it would
silently fail.

## Effort

Relative effort: **4 / 5**. The largest slice in the 140 initiative.
Six new modules (gaps, locking, band_writer, outcomes, daily-cycle,
minute-cycle), one significant refactor (deletion of the 120-era
daemon path), and a meaningful concurrency/integration test surface.
The arch document constrains the design, so most of the work is
disciplined implementation, not novel decision-making — but there is
a lot of it.
