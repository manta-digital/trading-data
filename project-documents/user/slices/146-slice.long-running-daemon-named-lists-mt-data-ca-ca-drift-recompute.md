---
docType: slice-design
slice: 146-long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute
project: trading
parent: user/architecture/140-slices.data-quality-operations.md
dependencies:
  - 145-slice.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes
interfaces:
  - 147-slice.mt-data-status        # consumes daemon's CA-drift-corrected adj_*
  - 148-slice.mt-data-refetch       # operator escape valve, parallel to daemon's automatic recompute
  - 149-slice.mt-data-audit         # Stage A/B audits ride on top of CA-drift recompute
  - 150-slice.rebuild-minute-caggs-adjusted-prices  # caggs assume adj_* stays correct under CA drift
  - 152-slice.bulk-eod-steady-state # daily steady-state path layered on this slice's runner
relatedReference: user/architecture/140-arch.data-quality-operations.md
dateCreated: 20260503
dateUpdated: 20260503
status: complete
---

# Slice Design: 146 — Long-Running Daemon + Named Lists + `mt data ca` + CA-Drift Recompute

## Overview

Slice 145 shipped the daemon **primitives**: a one-shot `mt data daemon
daily` / `mt data daemon minute` cycle that drains `data_gaps`, writes
correct `adj_*` on initial fetch via band-based UPDATEs, and serializes
concurrent writers via PostgreSQL advisory locks. This slice turns those
primitives into the **operational surface** the architecture specifies.

Four components, all layered on slice 145's cycle functions
(`run_daily_cycle`, `run_minute_cycle`) without changing them:

1. **Long-running daemon** (`mt data daemon run`) — continuous loop
   over the cycle functions with token-bucket throttling against
   `EODHD_PER_MINUTE_BURST` (1000 credits/min) and `EODHD_DAILY_QUOTA`
   (100k credits/day rolling); SIGTERM finishes current symbol then
   exits; scope-aware termination defaults (`--symbols` / `--list`
   exit when scope drains; bare invocation runs forever).
2. **Named symbol lists** — `config/symbol-lists.yaml` plus
   `mt data lists ls | show NAME | refresh-sp500`; `--list NAME` on
   `daemon run` and `ca update` resolves to a symbol set that filters
   `iter_active_instruments`. Lists are operator config, not
   instrument state.
3. **`mt data ca` command group** — replaces `mt data adjustment
   ingest`. `ca update` defaults to bulk-fetching yesterday's splits
   + dividends across the full exchange (200 credits). `--since N`,
   `--symbol X`, `--list NAME` shape the path. Deletes the
   `adjustment` Typer sub-app entirely.
4. **CA-drift detection + band recompute** — the original slice 146
   scope, retained verbatim. Each cycle, before fetching new bars,
   compares current `compute_snapshot_id` to
   `acquisition_state.last_adjusted_ca_snapshot_id`. On mismatch:
   recompute affected ex-date bands via the same band-based UPDATE
   writer slice 145 introduced, refresh affected cagg ranges, advance
   `last_adjusted_ca_snapshot_id`, then proceed with the cycle.

The slice 145 one-shot CLI commands (`mt data daemon daily`,
`mt data daemon minute`) are deleted; their cycle functions remain and
are now invoked exclusively by the long-running loop.

**Bulk-EOD steady-state is deferred to slice 152.** This slice ships
per-symbol `/eod` for the daily path (slice 145's existing behavior).
At ~13k symbols × 1 credit/call = ~13k credits/day, daily steady-state
is well under the 100k/day quota; bulk-EOD is a quota optimization,
not a correctness requirement, and its mode-selection edge cases
(newly-added symbols, mixed-mode cycles, bulk-response routing into
the per-symbol band-write path) deserve their own design pass.

## Value

1. **One unattended process replaces a cron jungle.** Today (post-145)
   an operator must orchestrate `daemon daily`, `daemon minute`,
   `adjustment ingest`, and refetch by hand or with cron. After this
   slice, `mt data daemon run` is the single supervised process.
2. **Quota awareness becomes structural, not aspirational.** EODHD's
   1000/min burst and 100k/day rolling caps are enforced inside the
   loop. Naive cycling against ~13k symbols would blow the burst cap
   in seconds; the token bucket prevents that without operator
   thought.
3. **CA correctness is self-healing.** A new split or dividend lands
   in `splits`/`dividends`; the next daemon cycle detects the
   `snapshot_id` mismatch and recomputes affected `adj_*` ranges.
   Operators stop having to remember "and now re-adjust." Backtests
   stop reading stale adjusted prices.
4. **Named lists make priority ordering trivial.**
   `mt data daemon run --list priority1` finishes a hand-picked
   ten-symbol set in minutes and exits. The same daemon binary is
   the playground for `priority1` and the production sweep for the
   full universe — no separate "test daemon."
5. **`mt data ca` collapses the CA surface.** Today: `mt data
   adjustment ingest --symbol X --type splits` and the dual for
   dividends, run twice. After: `mt data ca update --symbol X` does
   both in one call; the no-flags default is the daily steady-state
   call the production loop already needs.

## Non-Goals

- **Bulk EOD steady-state for the daily path** — deferred to slice
  152. This slice keeps slice 145's per-symbol `/eod` daily fetch.
  At ~13k symbols × 1 credit, full-universe daily costs ~13k
  credits/day — comfortably under the 100k/day quota. Switching to
  `/eod-bulk-last-day/US` is a quota optimization with non-trivial
  edge cases (caught-up detection across newly-added symbols,
  routing the bulk response into the per-symbol band-write path,
  mixed-mode cycles when only some scope members are caught up); it
  earns its own slice once we've watched per-symbol `/eod` run in
  steady-state and know what the operational pain actually looks
  like.
- **Bulk CA ingestion.** EODHD exposes
  `/eod-bulk-last-day/US?type=splits` (100 credits flat, full
  exchange). The architecture's "Future work" section flags this as
  a follow-up. This slice ships per-symbol CA fetches plus the bulk
  EOD-bars endpoint; bulk CA stays per-symbol-keyed for now because
  per-symbol CA polling is not measurable quota pressure.
- **Cross-vendor audit.** Slice 149's `mt data audit` is the
  single-vendor (EODHD) consistency check. Multi-vendor compare is
  deferred per the slice plan.
- **Persistent quota accounting across restarts.** The token bucket
  lives in process memory; on restart it begins refilled. Rationale:
  (a) the daily 100k cap is rolling and the EODHD response carries
  current spent-credits headers we can re-sync from on first call;
  (b) any in-process state lost across restart is bounded by
  `EODHD_PER_MINUTE_BURST` worth of credits — within tolerance.
- **Distributed daemon coordination.** Single process per host. Multi-
  host deployment is out of scope; if it ever lands, the advisory
  locks slice 145 introduced are already the right serialization
  primitive.
- **Backtest contract enforcement.** The CA-drift recompute makes
  `adj_*` correct at *cycle* boundaries; backtests that read between
  cycles still use slice 145's `MAX_GAP_STALENESS` recompute trigger
  (slice 145 ships the primitives; consumer wiring is its own slice).
- **Materialized `data_status`.** Status performance is not in scope;
  measurement-driven follow-up if needed.
- **Schema migrations.** No new tables. `symbol_lists` lives in YAML
  (architecture explicitly notes "or a `symbol_lists` DB table" — we
  pick YAML; trivially migratable later if DB persistence becomes
  desirable).

## Inputs

- **Slice 145 cycle functions** —
  `manta_trading.data.acquisition.daemon.daily.run_daily_cycle(symbols)`
  and `manta_trading.data.acquisition.daemon.minute.run_minute_cycle(symbols)`.
  Both return a `CycleReport`. Both already drive the
  `data_gaps`-driven loop, advisory locking, and band-based `adj_*`
  writes. This slice wraps them; it does not modify them except to
  add the snapshot-drift check at cycle entry.
- **Slice 143 adjustment primitives** — `compute_k_factor`,
  `current_ca_snapshot`, `compute_snapshot_id`. Used by the
  CA-drift detection step at the top of each cycle.
- **Slice 145 band-based UPDATE writer** — same SQL pattern reused
  by the CA-drift recompute for the
  `[min(changed_ca.ex_date), now()]` range.
- **`acquisition_state.last_adjusted_ca_snapshot_id`** — slice 142
  schema column. Slice 145 writes it on initial fetch. This slice
  reads it for drift detection and writes it on recompute completion.
- **`instruments` universe** — used by `iter_active_instruments` plus
  list-filter overlay.
- **`data_gaps`** — actionable-row selector (already in place).
- **EODHD endpoints** — `/eod/{ticker}` (daily, full history on
  backfill or trailing window on steady-state),
  `/intraday/{ticker}` (minute), `/splits/{ticker}` + `/div/{ticker}`
  (per-symbol CA backfill), `/eod-bulk-last-day/US?type=splits` and
  `?type=dividends` (bulk CA daily for `mt data ca update`),
  `/fundamentals/GSPC.INDX` (S&P 500 component refresh).
- **EODHD cost constants** — already in
  `manta_trading.constants` (added 2026-05-03):
  `EODHD_DAILY_QUOTA`, `EODHD_PER_MINUTE_BURST`,
  `EODHD_INTRADAY_CALL_COST`, `EODHD_EOD_CALL_COST`,
  `EODHD_BULK_EOD_BASE_COST`.

## Outputs

### Code

- New CLI commands under `mt data daemon`:
  - `mt data daemon run [--minute] [--daily] [--symbols X,Y,Z]
    [--list NAME] [--max-credits N] [--stop-when-done | --forever]`
- New CLI sub-app `mt data lists`:
  - `mt data lists ls`
  - `mt data lists show NAME`
  - `mt data lists refresh-sp500`
- New CLI sub-app `mt data ca`:
  - `mt data ca update [--since DAYS_OR_DATE]
    [--symbol SYMBOL | --list NAME]`
  - `mt data ca show --symbol SYMBOL [--from DATE] [--to DATE]`
  - `mt data ca list [--from DATE] [--to DATE]`
- New module `manta_trading.data.acquisition.daemon.runner` —
  long-running loop, token bucket, SIGTERM handling, per-cycle
  progress logging.
- New module `manta_trading.data.acquisition.quota` — token-bucket
  implementation, two-window (per-minute burst + per-day rolling),
  pure-Python (no external lib), `cost_for(call_type)` lookup keyed
  on a `CallType` `StrEnum`.
- New module `manta_trading.data.lists` — list resolution from
  `config/symbol-lists.yaml`, S&P 500 refresh, file-source resolution
  (`source: file:...`).
- New module `manta_trading.data.acquisition.daemon.ca_drift` —
  per-symbol drift detection (compares stored
  `last_adjusted_ca_snapshot_id` to current `compute_snapshot_id`),
  band-recompute orchestrator (delegates the SQL UPDATE writer to
  the slice 145 module), affected-cagg refresh trigger.

### Config

- `config/symbol-lists.yaml` — see architecture spec; ships with
  `priority1` (≤10 hand-picked symbols), `priority2` (sourced from
  `config/lists/sp500-snapshot.txt`).
- `config/lists/sp500-snapshot.txt` — initially empty / placeholder;
  populated by `mt data lists refresh-sp500`.

### Deletions

- `mt data daemon daily` and `mt data daemon minute` Typer commands
  (slice 145's one-shot wrappers) — removed; replaced by
  `mt data daemon run` with explicit scope/mode flags.
- `adjustment_app` Typer sub-app entirely (`mt data adjustment
  ingest`, `mt data adjustment verify`,
  `mt data adjustment verify-against-eodhd-eod`) — replaced by
  `mt data ca update` (and the verify commands move to `mt data
  audit`, slice 149; the verify commands stay in this slice as
  `mt data ca verify` only if needed pre-149 — see Decision E).

### Documentation

- CHANGELOG entry per slice closeout.
- Inline docstrings on each new module per project rules.
- No standalone README; CLI help text is the authoritative reference.

## Approach

### Decision A: Token-bucket scope (per-process, two-window)

**Decision:** A single process-local `QuotaBucket` with two
sub-buckets — `minute_window` (capacity 1000, refill 1000/60s) and
`day_window` (capacity 100k, refill 100k/86400s, rolling). Every
provider call passes a `CallType` enum to `bucket.consume(call_type)`
which blocks until both windows have capacity. No persistence across
restarts.

**Rejected alternatives:**
- *Per-window-aware HTTP middleware that reads EODHD's
  `X-RateLimit-*` headers.* Would be more accurate but couples the
  bucket to EODHD's response schema and requires a successful call
  before knowing the budget. The static constants in
  `manta_trading.constants` are authoritative per the architecture
  decision; the runtime should match them, not learn them.
- *Distributed bucket via Redis / DB row.* Over-engineered for a
  single-process daemon. Re-evaluate only if multi-host deployment
  becomes a requirement.

**Rationale:** The 100k/day cap with rolling-window accounting is
the load-bearing constraint. A simple two-bucket model captures both
the burst ceiling and the rolling daily cap with one data structure
per process. Restart loss is bounded by `EODHD_PER_MINUTE_BURST` and
acceptable.

### Decision B: Long-running loop shape

**Decision:** Single thread, single asyncio event loop, alternating
between daily and minute cycles based on a small priority policy:

```
while not should_exit():
    if ca_update_due():         # once per UTC day, first call after 00:00 + grace
        run_ca_update_bulk()    # 200 credits — splits + dividends bulk fetch
    if daily_cycle_due():       # once per UTC day at first call after 00:00 UTC
        run_daily_cycle_with_drift_check(scope)
    if minute_cycle_due():      # back-to-back during market hours
        run_minute_cycle_with_drift_check(scope)
    else:
        sleep_until_next_due_event()
```

The `ca_update_due()` step is intentional and load-bearing — see
Decision G. Without it, no new CAs land in `splits`/`dividends`, so
the per-symbol drift check at the top of each cycle has nothing to
detect. The "self-healing CA correctness" value claim depends on
this step.

`scope` is the resolved symbol set (universe, `--symbols`, or
`--list`). `should_exit()` evaluates the termination policy
(`--stop-when-done`, `--forever`, default-by-scope, `--max-credits`,
SIGTERM flag).

**Rejected alternatives:**
- *Two threads (one per granularity).* Would require coordinating
  the token bucket across threads and complicate SIGTERM handling.
  The cycles are already sequential per-symbol; alternating them in
  one thread is simpler and equivalent.
- *Cron-style scheduler library.* This is one process running in a
  loop, not a scheduler with multiple jobs. APScheduler / Celery
  add operational surface for no win.

**Rationale:** Simplicity wins. The cycle functions already serialize
per-symbol via advisory lock; we don't gain throughput from
parallelism within one process — provider calls are the bottleneck,
and the token bucket already serializes those.

### Decision C: CA-drift detection placement

**Decision:** Per-symbol, at the top of each symbol's iteration in
both `run_daily_cycle` and `run_minute_cycle`. The cycle functions
gain one new step before fetch:

```python
snapshot = current_ca_snapshot(symbol)
stored_id = acquisition_state.get(symbol).last_adjusted_ca_snapshot_id
if stored_id is not None and stored_id != snapshot.snapshot_id:
    recompute_adj_bands(symbol, granularity, snapshot)
    refresh_affected_caggs(symbol, granularity, snapshot)
    acquisition_state.set_last_adjusted_ca_snapshot_id(
        symbol, granularity, snapshot.snapshot_id
    )
# proceed with normal fetch path (which itself sets last_adjusted_ca_snapshot_id
# on initial-fetch bars to the same value)
```

The recompute uses the slice 145 band-based UPDATE writer with the
range `[min(changed_ca.ex_date), now()]` per the architecture spec.

**Rejected alternatives:**
- *Drift detection at cycle entry, not per symbol.* Cheaper, but
  misses the case where a CA lands mid-cycle for a symbol the cycle
  has not yet reached. Per-symbol detection is naturally correct.
- *Drift detection as a separate cron task outside the daemon.*
  Re-introduces the cron jungle this slice is trying to eliminate.

**Rationale:** The `compute_snapshot_id` call is cheap (it hashes a
small in-memory snapshot), and `last_adjusted_ca_snapshot_id` is a
single column read. The marginal cost per symbol is negligible
relative to the fetch cost; correctness wins.

### Decision D: Migration of `mt data adjustment verify*`

**Decision:** Delete the entire `adjustment` Typer sub-app in this
slice. The `verify` and `verify-against-eodhd-eod` commands are
re-homed in slice 149's `mt data audit` (Stage A and Stage B,
respectively). Until 149 lands, the verify capability is exercised
only via integration tests; operators have no production need for
it in the 146-only window because slice 146's CA-drift recompute
keeps `adj_*` correct automatically.

**Rejected alternatives:**
- *Keep `mt data adjustment verify` until 149 lands.* Leaves a
  vestigial Typer sub-app with one command. Operators learn it,
  then it disappears two slices later.
- *Land 146 and 149 together.* Inflates this slice unnecessarily;
  149's audit scope (Stage A + B + `--all` + tolerance + JSON) is
  its own design problem.

**Rationale:** Clean break. The audit commands belong in
`mt data audit`, not `mt data ca`; CA management and audit are
distinct concerns even though they touch the same data.

### Decision E: List config format (YAML, not DB)

**Decision:** YAML at `config/symbol-lists.yaml`, file-sourced
references via `source: file:config/lists/sp500-snapshot.txt`. No
`symbol_lists` DB table.

**Rejected alternatives:**
- *DB table.* Would need migration, CRUD CLI, ORM model. YAML is
  human-editable, source-controllable, version-trackable in git, and
  diff-reviewable.
- *Inline in `pyproject.toml` or `mt-config.yaml`.* Over-loading the
  main config; lists deserve their own file.

**Rationale:** Lists are operator config, not application state.
Git is the right backing store. The architecture spec leaves it as
"YAML or DB table"; we pick YAML for low operational surface.

### Decision F: SIGTERM behavior

**Decision:** SIGTERM sets a process-level `should_exit` flag. The
runner finishes the current symbol (so neither the cycle's per-symbol
advisory lock nor any in-flight HTTP call is interrupted), then exits
0. SIGINT is treated identically. SIGKILL is operator's
responsibility — it leaks the advisory lock until the connection
times out (psycopg session-scoped lock), which is acceptable.

**Rationale:** Per-symbol granularity is the natural unit; partial-
symbol writes are not atomic across multiple chunks but each chunk
is a single transaction (slice 145 invariant). Shutting down between
symbols leaves the system in a clean state.

### Decision G: `mt data ca update` placement (inline in daemon, not external cron)

**Decision:** The bare `mt data ca update` (bulk yesterday's
splits + dividends, 200 credits) runs **inline inside the daemon
loop**, gated by a once-per-UTC-day `ca_update_due()` check at the
top of each iteration. The CLI command remains for operator manual
use and one-shot scripts; the daemon does not shell out to it but
calls the same underlying ingest function.

State for the once-per-day gate: a single timestamp on
`acquisition_state` keyed by a sentinel symbol (e.g.
`('__bulk_ca__', 'daily')` row's `last_attempt_ts`), or a small
process-local "last_ca_update_utc_date" memo that re-checks the DB
on startup. Pick the DB-backed approach so a daemon restart on the
same day doesn't double-spend the 200 credits.

**Rejected alternatives:**
- *External cron / systemd timer.* Re-introduces the cron-jungle
  this slice exists to eliminate. Operator must remember to schedule
  it; missed runs silently break CA-drift detection.
- *Drift detection without CA ingestion.* The drift check would have
  nothing to detect — `compute_snapshot_id` reads
  `splits`/`dividends`, which only change when ingest runs. The
  self-healing claim collapses.
- *Inline on every cycle (not gated).* Wastes 200 credits per cycle
  for no benefit; CAs change daily at most.

**Rationale:** The architecture says "either is fine; implementation
chooses the one with lower operational surface." Inline-in-daemon
wins on operational surface (one process, one config) and on the
self-healing value claim (no external dependency to keep the
mechanism live).

## Failure Modes

### EODHD HTTP

- **429 Too Many Requests.** Should not happen if the token bucket
  is correct. If it does, treat as transient: log at WARNING, sleep
  the response's `Retry-After` (or 60s default), retry. After
  `MAX_RETRY_COUNT` 429s on the same call, escalate to ERROR and
  exit nonzero — the bucket is misconfigured.
- **5xx.** Standard transient: backoff retry per the slice 145
  pattern. Cycle marks symbol's outcome `transient_failure`.
- **4xx other than 429.** Non-retryable. Logged at ERROR;
  `update_data_gaps` writes `RETRY_EXHAUSTED` for the affected
  range; cycle proceeds with next symbol.
- **Network timeout.** Retried per slice 145's existing transient
  policy.
- **Peer disconnect mid-send.** TCP connection drops after partial
  response received (distinct from timeout — bytes have arrived but
  the body is truncated, often producing a JSON parse error rather
  than an HTTP error). The HTTP client's response-body read raises
  `httpx.RemoteProtocolError` / `httpx.ReadError` /
  `json.JSONDecodeError` depending on where in the stream the drop
  occurred. Handling: discard the partial response (do **not** parse
  or persist), classify as transient_failure, and retry per the
  slice 145 backoff/`MAX_RETRY_COUNT` policy — same path as 5xx and
  timeout. The token bucket is **not** refunded for the dropped
  call; EODHD likely charged the credit. Acceptable: rare event,
  bounded by `MAX_RETRY_COUNT` retries per chunk.

### CA-drift recompute

- **Snapshot computed mid-write.** A new CA could land between
  `current_ca_snapshot()` and the band UPDATEs. Acceptable: the
  next cycle re-detects drift and recomputes again. No corruption.
- **Cagg refresh fails.** Logged at ERROR;
  `last_adjusted_ca_snapshot_id` is **not** advanced (so next cycle
  retries). The bars themselves are correct (UPDATEs committed); the
  cagg is stale until the next refresh succeeds.
- **Empty `last_adjusted_ca_snapshot_id`** (slice 145 had not
  populated it yet for some symbol). Treat as "no drift to detect";
  the slice 145 fetch path will populate it on the next bar write.

### Token bucket

- **Clock jump backwards** (NTP correction). Bucket may briefly
  over-grant. Bounded by `EODHD_PER_MINUTE_BURST`; recovers within
  one window.
- **Day boundary at 00:00 UTC.** Day window resets atomically; no
  special handling required because rolling windows naturally
  decay.

### List config

- **`config/symbol-lists.yaml` missing.** `mt data daemon run` and
  `mt data ca update` succeed without `--list`. With `--list`,
  exit nonzero with explicit error (no silent fallback to the full
  universe).
- **Named list references unknown symbol.** Filter ignores the
  unknown symbol but logs at WARNING. Cycle proceeds with the
  intersection of the list and the active instrument registry.
- **`refresh-sp500` returns malformed payload.** Exit nonzero
  without writing the snapshot file; preserve the existing snapshot
  for the next attempt.

### `mt data ca update`

- **Bulk endpoint returns nonzero error count for some symbols.**
  Logged at WARNING with per-symbol error list; non-failing rows
  upsert; exit code reflects whether *any* upsert succeeded (0) or
  none did (1).
- **Per-symbol path called with `--symbol UNKNOWN`.** Exit nonzero
  with explicit error; do not silently no-op.

## Non-Functional Targets

- **Throughput, single-symbol fast path:** `mt data daemon run
  --symbols SPY` completes a 22-year SPY backfill in **~90s of API
  time** (the architecture spec's target metric — wall-clock summed
  over outbound HTTP calls). Wall-clock at the CLI may be modestly
  higher (token-bucket waits, DB-write latency, the per-symbol
  drift-check round-trip); we expect under 2 minutes wall clock as
  a soft ceiling, but the API-time figure is the load-bearing one
  because it isolates the daemon's contribution from
  environment/throttling overhead. Verification (success criteria
  #2) measures both: `time` for wall clock, instrumented HTTP-call
  duration sum for API time.
- **Steady-state daily cycle cost:** ~13k credits/cycle at full
  universe (per-symbol `/eod`, 1 credit each); slice 152 will reduce
  this to ~100 credits/cycle by switching to bulk EOD.
- **Steady-state minute cycle cost:** ~63k credits/day at full
  universe (verified figure from session prep).
- **Memory:** runner process steady-state RSS < 500 MB at full
  universe (no per-symbol accumulation; all state in DB or
  bounded-size in-process structures).
- **SIGTERM-to-exit latency:** ≤ one symbol's processing time
  (typically < 30s for daily, < 60s for minute backfill chunks).
- **Token bucket overhead:** < 1ms per `consume()` call.
- **List resolution latency:** < 100ms for any defined list (file
  read + YAML parse + intersect with active universe).

## Cross-Slice Dependencies

- **Slice 145** — hard dependency. This slice does not change the
  cycle internals; it wraps them. If slice 145 invariants are not
  in place (advisory lock, band-write, `data_gaps` source-of-truth,
  `last_adjusted_ca_snapshot_id` written on initial fetch), this
  slice's CA-drift recompute and token-budgeted runner will not
  produce correct results.
- **Slice 143** — `compute_k_factor`, `current_ca_snapshot`,
  `compute_snapshot_id`. CA-drift detection calls all three.
- **Slice 142** — `acquisition_state.last_adjusted_ca_snapshot_id`
  column; `data_gaps` table; constants module.
- **Slice 144** — `trading_sessions` for cycle-due predicates
  (e.g. "has yesterday's session closed + grace?" gates the daily
  cycle's run-once-per-UTC-day check).
- **Provides to slice 147 (`mt data status`)** — the long-running
  daemon means `last_attempt_ts` rows stay current; status' STALE
  classification will fire only when the daemon is genuinely down.
  The CA-drift recompute means stored `adj_*` is trustworthy when
  status (or any consumer) reads it.
- **Provides to slice 148 (`mt data refetch`)** — coexistence under
  advisory lock works because slice 145 already established the
  pattern; this slice's runner does not introduce new lock
  acquisitions.
- **Provides to slice 149 (`mt data audit`)** — Stage A is more
  meaningful when `adj_*` is current (CA-drift recompute landed);
  Stage B's published-k comparison is the audit consumer of the
  invariant this slice maintains.
- **Provides to slice 150 (rebuild caggs)** — caggs read `adj_*`;
  CA-drift recompute keeps `adj_*` correct, which keeps caggs
  consistent across CA events.

## Migration Plan

### Step 1 — Build the new daemon runner alongside slice 145's commands

- Add `mt data daemon run` as a *new* Typer command. Slice 145's
  `mt data daemon daily` and `mt data daemon minute` continue to
  work during development and integration testing.
- Add the token bucket, the runner loop, and the CA-drift module.

### Step 2 — Land lists and `mt data ca`

- Add `mt data lists` Typer sub-app and `config/symbol-lists.yaml`
  with `priority1` and `priority2` entries.
- Add `mt data ca` Typer sub-app. The `ca update --symbol X` path
  reuses the existing `manta_trading.data.adjustment.ingest`
  module's per-symbol write logic. The bulk-yesterday path is new;
  it calls the EODHD bulk CA endpoints and upserts via the same
  splits/dividends repository.

### Step 3 — Switch the runner over and remove slice 145's command wrappers

- Verify `mt data daemon run --symbols SPY` produces the same
  per-symbol DB state as `mt data daemon daily --symbols SPY` for
  several sample symbols (AAPL, MSFT, GOOGL, SPY).
- Verify `mt data daemon run --list priority1` drains the list and
  exits.
- Delete `mt data daemon daily` and `mt data daemon minute` Typer
  commands (the underlying `run_daily_cycle` /
  `run_minute_cycle` functions stay).
- Delete the `adjustment_app` Typer sub-app (the underlying
  ingest/verify functions stay; verify is used by slice 149).

### Step 4 — Behavior verification at the migration boundary

- Diff `daily_ohlcv` content for the SPY backfill produced by the
  old vs. new code path. Bit-identical (same provider, same
  k_factor function, same band writer).
- Diff `data_gaps` rows for AAPL/MSFT/GOOGL across old vs. new.
  Identical except for the new CA-drift recompute path's effect on
  `last_adjusted_ca_snapshot_id` (which the old path also wrote on
  initial fetch — only difference is on second cycles when CAs
  changed).
- Run two parallel `mt data daemon run --symbols X,Y` processes on
  disjoint scopes (separate hosts or separate API keys); verify no
  deadlock and no double-credit-spend (each process has its own
  bucket; combined spend is bounded by 2 × per-process budget,
  which is fine because the operator chose to run two processes).

## Data Flows

### Long-running loop (steady-state, full universe)

Drift detection is **per-symbol, integrated into each cycle's
per-symbol step** (Decision C) — not a separate top-level sweep.
The cycle functions (`run_daily_cycle`, `run_minute_cycle`) run a
drift check at the top of each symbol's iteration before fetching
new bars; the runner does not loop over symbols itself for drift.

```
runner.start(scope=ALL_ACTIVE)
  loop:
    if ca_update_due():
      bucket.consume(BULK_EOD); fetch /eod-bulk-last-day/US?type=splits
      bucket.consume(BULK_EOD); fetch /eod-bulk-last-day/US?type=dividends
      upsert splits + dividends                       # 200 credits total
    if daily_cycle_due():                              # once per UTC day after 00:00 + grace
      → run_daily_cycle(scope):
          for each symbol in scope:
              # drift check (Decision C) — runs first, inside the symbol's iteration
              snapshot = current_ca_snapshot(symbol)
              if snapshot.id != stored_id:
                  recompute_adj_bands(symbol, snapshot)
                  refresh_caggs(symbol, snapshot)
                  advance(stored_id := snapshot.id)
              # then the normal fetch
              bucket.consume(EOD)                      # 1 credit
              fetch /eod/{ticker}
              insert daily_ohlcv
              band_write_adj
              update_data_gaps
    if minute_cycle_due():                             # back-to-back during/after market hours
      → run_minute_cycle(scope):
          for each symbol in scope:
              # same drift-check-then-fetch pattern as daily
              snapshot = current_ca_snapshot(symbol)
              if snapshot.id != stored_id:
                  recompute_adj_bands(symbol, snapshot)
                  refresh_caggs(symbol, snapshot)
                  advance(stored_id := snapshot.id)
              for each actionable gap (most-recent first):
                  bucket.consume(INTRADAY)             # 5 credits
                  fetch /intraday/{ticker}
                  insert minute_ohlcv
                  band_write_adj
                  update_data_gaps
                  coalesce_data_gaps
    sleep_until_next_due_event()
```

### Backfill loop (`--symbols SPY`)

```
runner.start(scope={SPY}, terminate_when_drained=True)
  loop:
    daily backfill:
      bucket.consume(EOD)               # 1 credit
      fetch /eod/SPY?output_size=full   # ~22 years in one response
      insert daily_ohlcv (~5500 bars)
      band_write_adj
      update_data_gaps                  # marks SPY daily fully-covered
    minute backfill:
      while gaps actionable:
        bucket.consume(INTRADAY)         # 5 credits per chunk
        fetch /intraday/SPY (120-day chunk, most-recent first)
        insert minute_ohlcv
        band_write_adj
        update_data_gaps
        coalesce_data_gaps
    scope drained → exit 0
```

### CA-drift recompute (single symbol)

```
detect:
  current = current_ca_snapshot(symbol)        # in-memory, ~ms
  stored  = state.last_adjusted_ca_snapshot_id
  if current.snapshot_id == stored: return     # no-op fast path

recompute:
  changed = diff(current, stored)
  range = [min(changed.ex_date), now()]
  for band in ex_date_bands(range, current):
      k = compute_k_factor(symbol, band.start - 1d, current)
      UPDATE <data_table> SET k_factor=k, adj_*= * k
        WHERE symbol=:s AND time >= :band.start AND time < :band.end
  refresh_caggs_in_range(symbol, range)
  state.last_adjusted_ca_snapshot_id = current.snapshot_id
```

### `mt data ca update` (no flags)

```
bucket.consume(BULK_EOD)                         # 100 credits
fetch /eod-bulk-last-day/US?type=splits&date=YYYY-MM-DD
upsert splits
bucket.consume(BULK_EOD)                         # 100 credits
fetch /eod-bulk-last-day/US?type=dividends&date=YYYY-MM-DD
upsert dividends
                                                  # total: 200 credits
                                                  # affected symbols' adj_* recomputed
                                                  # by next daemon cycle's drift check
```

## Risks

- **Token bucket undercounts credits.** EODHD's true cost may differ
  from the static constants (e.g. a future endpoint change). Mitigation:
  log accumulated daily spend on each cycle boundary; if it diverges
  from EODHD's response-header spend by > 10% in a 24h window, ERROR
  and exit. Operator re-syncs constants from current EODHD docs.
- **CA-drift recompute thrashes caggs.** A symbol with many CAs in
  rapid succession could trigger long band-recomputes that contend
  with cagg refresh policies. Mitigation: per-symbol advisory lock
  (already held by slice 145 invariant) serializes recompute against
  cycle work; cagg refresh runs out-of-band on the timescale-managed
  cadence and absorbs the changes naturally.
- **YAML config drift between hosts.** Two hosts with different
  `config/symbol-lists.yaml` will resolve `--list priority1`
  differently. Mitigation: doc that lists are config; deploy-tooling
  responsibility (out of slice scope).
## Success Criteria

1. **Long-running daemon runs forever.** `mt data daemon run` (no
   args) starts and continues running without exiting until SIGTERM
   or `--max-credits` is hit. Manually verified by running for at
   least one full day and observing continuous activity in the log.
2. **Scoped invocation exits cleanly.** `mt data daemon run --symbols
   SPY` finishes a 22-year SPY backfill (daily + minute target
   window) and exits with status 0. API time (sum of outbound HTTP
   call durations) ≤ ~90s per the architecture spec; wall clock ≤
   2 minutes as a soft ceiling.
3. **`--list NAME` exits when scope drains.** `mt data daemon run
   --list priority1` finishes the priority1 set and exits 0.
4. **Token bucket honors caps.** Synthetic test that issues 1500
   `bucket.consume(EOD)` calls in 60 seconds takes ≥ 60 seconds
   (the second 500 wait for the per-minute window). Synthetic test
   that issues 100,001 `bucket.consume(EOD)` calls in 24 hours
   takes ≥ 24 hours (the last call waits for the day window).
5. **SIGTERM completes current symbol then exits.** Send SIGTERM
   mid-cycle; observe the current symbol's processing finish, then
   `runner.start` returns and the process exits 0.
6. **`mt data ca update` (no flags) costs 200 credits.** Verified
   by an instrumented test that intercepts EODHD calls and asserts
   exactly two calls (one splits, one dividends) for the prior UTC
   day with no symbol filter.
6a. **Daemon runs `ca update` once per UTC day inline.** A daemon
    started fresh on a new UTC day performs the bulk splits +
    dividends fetch on its first iteration (200 credits), then does
    not repeat for the rest of the day. A daemon restarted later
    the same UTC day does **not** re-issue the bulk calls
    (DB-backed gate). Verified by HTTP-log capture across a
    same-day restart.
7. **`mt data ca update --symbol AAPL` matches legacy ingest.**
   Row-for-row diff against the rows produced by `mt data adjustment
   ingest --symbol AAPL` (run before deletion of the legacy command
   in step 3 of the migration plan) returns zero diffs on splits
   and dividends tables for AAPL.
8. **CA-drift recompute fires.** Seed `acquisition_state` with an
   intentionally-wrong `last_adjusted_ca_snapshot_id` for AAPL.
   Run `mt data daemon run --symbols AAPL --stop-when-done`.
   Verify (a) the cycle issues band-based UPDATEs against
   `daily_ohlcv` for AAPL (count via `pg_stat_statements` or query
   logging); (b) `last_adjusted_ca_snapshot_id` advances to the
   current snapshot id; (c) Stage A (`abs(adj_close - close *
   k_factor)`) holds across the recomputed range.
9. **CA-drift no-op when snapshot matches.** Run `mt data daemon
   run --symbols AAPL --stop-when-done` twice. Second run issues
   zero band-UPDATEs from the drift-check path (verified via
   query-logging delta) — drift detection is a single
   `compute_snapshot_id` call and a comparison.
10. **Named lists resolve correctly.** `mt data lists show priority1`
    prints exactly the symbols listed in `config/symbol-lists.yaml`
    under `priority1`. `mt data lists show priority2` prints the
    contents of `config/lists/sp500-snapshot.txt`.
11. **`mt data lists refresh-sp500` writes the snapshot.** Run the
    command; observe `config/lists/sp500-snapshot.txt` rewritten
    with ~500 tickers; observe one EODHD call to
    `/fundamentals/GSPC.INDX` (10 credits).
12. **Old commands gone.** `mt data daemon daily` and
    `mt data daemon minute` exit with "no such command" or are
    absent from `mt data daemon --help`. `mt data adjustment`
    sub-app is absent from `mt data --help`.
13. **No deadlock under co-execution.** `mt data daemon run` and
    `mt data refetch --symbol AAPL --from D1 --to D2` (slice 148
    or its test stand-in) running simultaneously serialize on AAPL
    via advisory lock; both eventually complete; no deadlock
    observed in 30 minutes of co-execution.

## Verification Walkthrough

Run from project root with `MT_TIMESCALE_DB_URL` and
`MT_EODHD_API_KEY` set, against `trading_test`. Pre-state assumes
slice 145 has run at least once (so `daily_ohlcv` has AAPL/MSFT/GOOGL).

**Verified: 2026-05-03. All 10 steps passed.**

### 1. Pre-state snapshot

```bash
psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT symbol, granularity, last_adjusted_ca_snapshot_id
    FROM acquisition_state
   WHERE symbol IN ('AAPL','MSFT','GOOGL','SPY')
   ORDER BY symbol, granularity;
"
```

Expect: rows for AAPL/MSFT/GOOGL daily with `last_adjusted_ca_snapshot_id`
populated; SPY not present yet (or NULL).

**Result:** All four symbols present with snapshot IDs populated (SPY
was populated by the earlier test run in this session).

### 2. Backfill SPY end-to-end with the new runner

```bash
time mt data daemon run --symbols SPY --stop-when-done
```

Expect: log lines reporting daily backfill start/end; exit status 0.

```bash
psql "$MT_TIMESCALE_DB_URL" -c "SELECT COUNT(*) AS daily_bars FROM daily_ohlcv WHERE symbol='SPY';"
psql "$MT_TIMESCALE_DB_URL" -c "SELECT health FROM data_status WHERE symbol='SPY';"
```

**Result:** `daily_bars = 8371` (1993-01-28 through 2026-04-30); `health = OK`.

### 3. Verify `--list` filtering

```bash
mt data lists show priority1
```

Expect: prints the 10 symbols configured in `config/symbol-lists.yaml`,
one per line.

**Result:** SPY QQQ AAPL MSFT NVDA GOOGL META TSLA AMZN BRK-B ✓

### 4. CA-drift recompute fires on stale snapshot

Force-stale AAPL, run daemon, verify drift fired:

```bash
psql "$MT_TIMESCALE_DB_URL" -c "
  UPDATE acquisition_state
     SET last_adjusted_ca_snapshot_id = 'force-stale-' || md5(random()::text)
   WHERE symbol = 'AAPL' AND granularity = 'daily';"

mt data daemon run --symbols AAPL --stop-when-done --daily --no-minute

psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT last_adjusted_ca_snapshot_id FROM acquisition_state WHERE symbol='AAPL' AND granularity='daily';
  SELECT COUNT(*) FILTER (WHERE ABS(adj_close - close * k_factor) > 1e-6) AS drift_violations FROM daily_ohlcv WHERE symbol='AAPL';"
```

**Result:** Log: `ca_drift[AAPL/daily]: recomputed 96 band(s) over [1987-05-11, 2026-05-04]`.
Snapshot advanced to current id. `drift_violations = 0`. ✓

### 5. CA-drift no-op on second pass

```bash
mt data daemon run --symbols AAPL --stop-when-done --daily --no-minute
```

**Result:** No drift log line on second run — only `scope drained` exit message. ✓

### 6. `mt data ca update` defaults to bulk

```bash
mt data ca update 2>&1 | grep -E "bulk|credits"
```

**Result:**
```
fetch_bulk_splits(US, 2026-05-02): 0 records
fetch_bulk_dividends(US, 2026-05-02): 0 records
2026-05-02: splits +0/~0  dividends +0/~0 [credits used: 200]
```
Exactly two bulk calls, 200 credits consumed. ✓

### 7. `mt data ca update --symbol AAPL` matches legacy ingest

```bash
mt data ca update --symbol AAPL
psql "$MT_MARKET_DB_URL" -c "
  SELECT 'splits' AS kind, COUNT(*) FROM splits WHERE symbol='AAPL'
  UNION ALL
  SELECT 'dividends', COUNT(*) FROM dividends WHERE symbol='AAPL';"
```

**Result:** `splits = 5`, `dividends = 91` — matches pre-deletion snapshot exactly. ✓

### 8. Token bucket unit tests

```bash
pytest test/unit/data/acquisition/test_quota.py -q
```

**Result:** 7 passed. ✓

### 9. SIGTERM clean shutdown

Covered by `test/integration/test_runner_sigterm.py` (T20, Part 1).
Manual verification: daemon exits 0 on SIGTERM with no stuck advisory locks. ✓

### 10. Old commands removed

```bash
mt data daemon --help  # must not list daily or minute
mt data --help         # must not list adjustment
grep -rn 'daemon_app.command("daily")\|daemon_app.command("minute")\|adjustment_app' src/manta_trading/cli/
```

**Result:** `daily`/`minute` absent from daemon help; `adjustment` absent from data help;
grep returns zero matches. ✓

## Resolved Decisions

### CLI surface — `mt data daemon run` (single command)

Considered `mt data daemon run-daily` and `mt data daemon run-minute`
as separate commands. Rejected: the architecture's surface is one
process running both granularities by design; separating them at
the CLI re-introduces the cron jungle. `--minute` and `--daily`
flags are toggles within the one command (default: both).

### `--max-credits` semantics

Counts credits spent by **this process** since its start, not
remaining EODHD daily quota. Operator wanting "stop at 50% of
today's quota" computes 50000 themselves. Rationale: the daemon
cannot reliably know how many credits other processes (operator
running `mt data ca update` manually, second daemon on another host)
have spent today. Per-process budget is honest about what it
controls.

### List file location

Picked `config/symbol-lists.yaml` over `~/.config/mt/lists.yaml`.
Lists are project state, not user state; they should be in git
alongside the code that consumes them.

## Effort

3/5. The runner loop, token bucket, and CA-drift module are each
straightforward but new; the `mt data ca` and `mt data lists`
sub-apps are mostly Typer plumbing over existing primitives. The
risk is in the integration: making sure SIGTERM, bucket throttling,
and CA-drift detection all compose correctly under load. The
verification walkthrough catches that risk.
