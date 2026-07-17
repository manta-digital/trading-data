---
docType: slice-design
slice: coverage-aware-minute-gap-seeding
project: trading-data
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [145, 146]
interfaces: [163, 164, 182]
dateCreated: 20260716
dateUpdated: 20260717
status: not_started
---

# Slice Design: Coverage-Aware Minute Gap-Seeding

## Overview

The minute-data daemon is currently **stopped** because its gap-seeding path
re-fetches full history for symbols that already have most of their data,
burning EODHD credits. This slice makes minute gap-seeding **coverage-aware** —
it seeds `data_gaps` rows only for trading sessions genuinely missing from
`minute_ohlcv`, matching the correctness the daily path already has, but doing
so at 12k-symbol scale via one batch query instead of the per-symbol scan the
daily path uses.

The slice also adds **seed-phase progress output** (the daemon runs silent for a
long stretch during the seed today) and performs a **full re-audit of the four
operational fixes** already in the tree, confirming the `_has_any_gaps` re-seed
trigger becomes correct once seeding is coverage-aware.

## Value

**Operational / cost:** Restarting the minute daemon on a mostly-complete
universe currently produces tens of chunks per symbol (~69 chunks for a
long-lived symbol re-seeded from 2004), re-fetching data already present. Each
chunk is a paid EODHD intraday call. After this slice, a restart produces
**near-zero chunks** for already-covered symbols — the daemon fetches only what
is actually missing. This is what unblocks turning the production minute fetch
back on.

**Correctness:** A partially-backfilled symbol whose hole is in the *past*
(not the trailing window) is currently at risk of never being revisited under
naive "seed forward from latest bar" schemes. The coverage-aware diff seeds the
real hole regardless of where it sits in time.

**Developer-facing:** Seed-phase progress output turns a silent 12k-symbol seed
into an observable `seeded N/<total> symbols, M gaps` stream.

## Technical Scope

**Included:**

1. A new **batch coverage index** builder: one grouped query against the coarsest
   minute cagg `minute_4hour_ohlcv` producing `{symbol: set[covered_day]}` for
   the whole universe in a single scan.
2. A new **coverage-aware minute seeder** that, per symbol, diffs covered days
   against the trading-session calendar over `[history_start, today]` and seeds
   `data_gaps` rows only for genuinely-missing sessions — reusing the contiguous-
   run grouping already implemented in `compute_missing_ranges._group_into_ranges`.
3. Rewiring the minute branch of `update_data_gaps`
   ([`update_data_gaps.py:131-141`](../../../src/manta_trading/data/gaps/update_data_gaps.py))
   and the seed path in `_do_minute_symbol`
   ([`daemon/minute.py:257-269`](../../../src/manta_trading/data/acquisition/daemon/minute.py))
   to use the coverage-aware seeder instead of emitting a single
   `[history_start, target_end]` gap row.
4. **Seed-phase progress output** during the universe-wide seed.
5. **Regression test** pinning the `_has_any_gaps` re-fire interaction: after gap
   rows are deleted for a symbol that still has bars, the re-seed must recreate
   only genuinely-missing sessions, not a 2004→today span.
6. **Re-audit** of the four operational fixes (documented in this design's
   *Operational-Fix Re-Audit* section; no code change expected for three of
   them, a confirming test for the fourth).
7. **Fail-safe handling** for the batch coverage query (statement timeout +
   skip-this-cycle on failure; see *Failure handling for the batch query*).
8. **Doc-only:** remove the obsolete `MINUTE_HISTORY_MONTHS = 24` NFR from the
   140 architecture doc (dead AlphaVantage workaround; see *History window*
   decision). No code change to `_resolve_minute_history_start`.

**Excluded:**

- **Minute-cagg chunk re-sizing** — the caggs are over-chunked ~40× (slice 163).
  This slice's batch coverage query reads each chunk once in a parallel scan and
  is *not* materially affected by the over-chunking (measured ~3s full-DB), so
  163 is a separate, non-blocking slice.
- **Bounded-time hot-path enforcement** (slice 164) — a separate discipline for
  per-symbol reads. The batch coverage query here is deliberately universe-wide
  and grouped, not per-symbol, so it does not participate in the hot path 164
  governs.
- No change to the chunk-fetch loop, `_advance_minute_gap`, `coalesce_data_gaps`,
  or the daily path.
- No schema/migration change — `data_gaps` structure is unchanged.

## Dependencies

### Prerequisites

- **Slice 145** — `data_gaps` / `acquisition_state` model and the
  `update_data_gaps` / `compute_missing_ranges` machinery this slice extends.
- **Slice 146** — long-running daemon cycle (`run_minute_cycle`) and the
  `_do_minute_symbol` seed path this slice modifies.

### Interfaces Required

- `minute_4hour_ohlcv` continuous aggregate (bucket column `time_bucket`),
  named via `GRANULARITY_SOURCE[Granularity.H4]` in
  [`constants.py:127`](../../../src/manta_trading/constants.py) — **not** a
  literal string in the new code.
- `trading_sessions` (column `session_open_utc`) joined to `instruments` via
  `instruments.trading_calendar_id = trading_sessions.calendar_id` — the session
  calendar, exactly as `compute_missing_ranges._fetch_sessions` uses it.
- `instruments` lifecycle columns `first_listing_date`, `first_data_date`,
  `delisted_date` — the per-symbol history floor and clamp.
- `EODHD_INTRADAY_HORIZON = date(2004, 1, 1)` — the absolute provider backstop.

## Architecture

### The Bug (current behavior)

`_do_minute_symbol` decides a symbol needs seeding
([`minute.py:255`](../../../src/manta_trading/data/acquisition/daemon/minute.py)):

```python
_needs_seed = force_reset_terminal or not _has_bars or not _has_any_gaps or _has_unknown_gaps
```

When `_needs_seed` is true it calls `update_data_gaps(... history_start,
target_end ...)`. For minute granularity, `update_data_gaps` short-circuits the
coverage computation and emits **one** gap row spanning the entire window
([`update_data_gaps.py:131-141`](../../../src/manta_trading/data/gaps/update_data_gaps.py)):

```python
if granularity == "minute":
    if fetch_status_for_unfilled is not None:
        gap_ranges = [GapRange(symbol, granularity, from_ts, to_ts)]   # ← one huge row
    else:
        gap_ranges = []
else:
    gap_ranges = compute_missing_ranges(conn, symbol, granularity, from_ts, to_ts)
```

`from_ts` is `history_start`, resolved by `_resolve_minute_history_start` to
`max(EODHD_INTRADAY_HORIZON, operator_floor, first_listing/first_data_date)` —
for a long-lived symbol this is **2004-01-01**. `to_ts` is today. So the seed
produces a single `[2004, today]` gap. The chunk loop
(`pick_most_recent_actionable_gap` → 120-day chunks, newest-first) then walks
this in **~69 chunks**, re-fetching windows already fully present in
`minute_ohlcv` (bar insert is `ON CONFLICT DO NOTHING`, so it is not corrupting —
just wasteful and slow, and every chunk is a paid EODHD call).

The comment at `update_data_gaps.py:126-130` explains *why* the daily
`compute_missing_ranges` path was not reused for minute: it "fetches all stored
minute timestamps," which is "prohibitively expensive" per symbol at minute
resolution. That reasoning is correct for a per-symbol call — **this slice
replaces it with a batch coverage index at day granularity**, which is cheap.

### The Fix — Batch Coverage Index + Day-Granularity Diff

**Design decision: diff at day granularity, not minute granularity.** The daily
path compares *stored bar timestamps* to *sessions*. For minute we compare
*days that have any minute bars* to *trading sessions*. A trading session either
has minute bars or it does not; we do not need per-minute presence to decide
whether to fetch a session's intraday window. This collapses the per-symbol
minute-timestamp scan (millions of rows) into a per-symbol day-set diff (a few
thousand days), and lets us source coverage from the coarse `minute_4hour_ohlcv`
cagg instead of raw `minute_ohlcv`.

#### Component 1 — `build_minute_coverage_index` (new)

One grouped query over the coarsest minute cagg, run **once per cycle** before
the per-symbol loop:

```sql
SELECT symbol, date_trunc('day', time_bucket) AS covered_day
FROM minute_4hour_ohlcv
GROUP BY symbol, date_trunc('day', time_bucket)
```

Returns `{symbol: set[date]}`. The cagg name is read from
`GRANULARITY_SOURCE[Granularity.H4]`, never hard-coded.

Measured on the production DB (EXPLAIN + timed run, slice-162 prep):
- **~3.05s total**, 2,425,433 rows, Finalize HashAggregate over Gather with 13
  parallel workers, Buffers `hit=14932 read=49833` (~500 MB, ~5× the single-
  symbol scan — **not** 12000×). One shot for the whole universe.
- The rejected per-symbol variant
  (`... WHERE symbol = 'AAPL' GROUP BY date_trunc('day', time_bucket)`) is
  ~2.0s **each** because it opens ~140 tiny chunks (per-chunk open overhead
  dominates). 2s × 12k symbols ≈ 7 hours — **rejected**.

**Why the cagg and not raw `minute_ohlcv`:** the cagg holds one row per
4-hour bucket, so the grouped scan touches ~orders of magnitude fewer rows than
raw. The cagg can only *lag* raw (a continuous aggregate structurally cannot
report a bar that raw does not have; refresh policy ≤1h). The worst case is
therefore that the cagg has not yet materialized *today's* partial session, so
we re-seed today — harmless and self-correcting on the next cycle. There is no
case where the cagg claims coverage raw lacks, so we never *skip* a genuinely-
missing session.

**Deliberate departure from `compute_missing_ranges` step-3.** The architecture's
gap function reads coverage from the *raw data table* (arch §"Gap function"
step 3: "From the data table, get the set of `date(time)` for stored bars").
This slice sources coverage from the `minute_4hour_ohlcv` cagg instead. This is
an intentional deviation, sound because (a) the arch defines caggs as first-class
schema objects projecting raw OHLCV, so a cagg is a legitimate coverage proxy;
(b) the cagg-lags-raw property above means the deviation can only ever cause a
harmless re-seed of today, never a skipped gap; and (c) it is the specific
mechanism that makes the universe-wide scan cheap enough to run per cycle (the
raw per-symbol scan the arch step-3 implies is the ~7hr path we reject). The
daily path is unchanged and continues to read raw per its step-3.

**Failure handling for the batch query.** The coverage-index query is a new
per-cycle I/O path and must fail safe. The build runs under an explicit
statement timeout (a small multiple of the measured ~3s — e.g. `SET LOCAL
statement_timeout` = 30s, centralized as a constant, not a literal at the call
site). Failure modes and responses:

- **Timeout / operational error / connection drop mid-scan** — catch the specific
  psycopg exception, log at ERROR via `logger.exception`, and **skip
  coverage-aware seeding for this cycle**: `_do_minute_symbol` proceeds using
  the gap rows already present and does **not** fall back to the old
  single-`[history_start, today]`-span seed. Rationale: the failure mode we are
  eliminating is the credit-burning full-window re-seed, so on any coverage-index
  failure the safe action is to seed *nothing new* (never re-introduce the very
  behavior this slice removes). A symbol with genuinely no gap rows and no
  coverage simply waits for the next cycle whose index build succeeds.
- **Never halt the daemon** on a coverage-index failure — it degrades to
  "attempt only already-known gaps this cycle," which is a safe, self-correcting
  state.

The skip decision is represented explicitly (e.g. `coverage_index is None`
signals "index unavailable this cycle") so the seed path can branch on it
without a silent empty-dict ambiguity (an *empty* index — every symbol
uncovered — is a distinct, valid state from *no* index).

#### Component 2 — `compute_missing_minute_sessions` (new)

Per symbol, given the coverage index:

1. Resolve `history_start` via the existing `_resolve_minute_history_start`
   (unchanged) and clamp to lifecycle dates exactly as
   `compute_missing_ranges._clamp_to_lifecycle` does (`first_listing_date` /
   `first_data_date` lower bound, `delisted_date` upper bound).
2. Fetch the symbol's trading sessions over `[history_start, today]` — the same
   `trading_sessions ⨝ instruments` query `_fetch_sessions` uses, projected to
   **session day** (`session_open_utc::date`).
3. `covered_days = coverage_index.get(symbol, set())`.
4. `missing_sessions = [s for s in sessions if s.date() not in covered_days]`.
5. Group contiguous missing sessions into `GapRange` spans, reusing
   `_group_into_ranges` (promoted from private to a shared helper — see
   *Patterns* below).

Returns `list[GapRange]`. Empty list ⇒ nothing to seed (fully covered).

#### Component 3 — rewired minute seed

`update_data_gaps`'s minute branch changes from emitting one span to calling
the coverage-aware computation. Because `update_data_gaps` runs *inside a
transaction with only a per-symbol connection* and must not itself run the
universe-wide scan, the coverage index is **built once by the caller**
(`run_minute_cycle`) and threaded down. Two viable wirings:

- **(chosen) Seed outside `update_data_gaps`.** `_do_minute_symbol` computes
  `missing_ranges = compute_missing_minute_sessions(conn, symbol, coverage_index,
  history_start, target_end)` and, when non-empty, calls `update_data_gaps` with
  the **precomputed ranges** rather than letting it recompute. This requires
  `update_data_gaps` to accept an optional `precomputed_ranges` parameter for
  the minute path; when provided, it skips the single-span short-circuit and
  inserts exactly those ranges (carry-forward logic unchanged). When *not*
  provided (daily path, and any caller that doesn't pass an index), behavior is
  unchanged.
- (rejected) Pushing the coverage index into `update_data_gaps` as a required
  arg — churns the daily call sites and the `mt data refetch` path for no
  benefit.

`_needs_seed` (the trigger) is **unchanged**. What changes is *what gets seeded*
when it fires: real holes, not the whole window.

### Data Flow

```
run_minute_cycle
  │
  ├─ build_minute_coverage_index(conn)         ← ONE grouped cagg query (~3s)
  │     → {symbol: set[covered_day]}
  │
  └─ for sym in symbol_list:                    ← existing most_stale_first loop
        _do_minute_symbol(sym, ..., coverage_index)
          │
          ├─ _needs_seed?  (unchanged trigger)
          │     └─ compute_missing_minute_sessions(conn, sym, coverage_index, ...)
          │           1. clamp to lifecycle
          │           2. sessions[]  (trading_sessions ⨝ instruments)
          │           3. missing = sessions − covered_days   ← day-granularity diff
          │           4. group into GapRange[]
          │     └─ update_data_gaps(..., precomputed_ranges=missing_ranges)
          │           └─ INSERT only the real holes    ← was: one [2004, today] row
          │
          └─ chunk loop (UNCHANGED)              ← now near-empty for covered symbols
```

### State Management

No new persistent state. The coverage index is an in-memory
`dict[str, set[date]]` built once per cycle and discarded when the cycle ends.
Its staleness bound is the cagg refresh policy (≤1h) plus one cycle's runtime;
the only consequence of staleness is re-seeding *today's* session, which the
chunk loop resolves to zero or one paid call and which self-corrects next cycle.

## Technical Decisions

### Diff at day granularity via the coarse cagg

Rationale covered in *Architecture*. In short: sessions are daily; a day either
has intraday bars or it does not; the coarse cagg answers "which days have any
bars" for the whole universe in one ~3s scan; the per-symbol raw-minute scan the
daily comment warns against is avoided entirely.

### Reject seed-forward-from-`MAX(time)`

A tempting cheaper alternative is "seed from the symbol's latest stored bar
forward." **Rejected — it silently loses past data.** The chunk loop walks
gaps **most-recent-first** (`pick_most_recent_actionable_gap`). A symbol whose
hole is in the *past* but whose `MAX(time)` is near today would be declared
complete and the past hole would never be revisited. The day-granularity diff
against the full session calendar catches holes wherever they sit.

### History window: keep full history to 2004 — the 24-month cap is obsolete

The 140 architecture specifies a minute target window of
`target_start = max(first_trade_date, today - MINUTE_HISTORY_MONTHS)` with
`MINUTE_HISTORY_MONTHS = 24` ([140-arch:157](../architecture/140-arch.data-quality-operations.md),
[:1029](../architecture/140-arch.data-quality-operations.md)). The slice-162
review (F003) correctly flagged that the shipped code does **not** implement
this: `constants.py` has no `MINUTE_HISTORY_MONTHS`, and
`_resolve_minute_history_start` resolves to `EODHD_INTRADAY_HORIZON` (2004) for a
long-lived symbol when `MT_MINUTE_HISTORY_START` is unset (it is unset in
production `.env`). The effective minute window is therefore full history to
2004, not 2 years.

**Decision: this is correct as-is; do NOT implement the 24-month cap.** The
24-month figure was never a product requirement — it was a workaround for
**AlphaVantage**, whose intraday API only served ~24 months. AlphaVantage was
removed from the codebase entirely; EODHD serves full 1-minute history back to
`EODHD_INTRADAY_HORIZON = 2004-01-01`. There is no reason to cap minute history
at 2 years, and doing so would discard data we specifically switched providers
to obtain. The shipped operator-floor model
(`max(EODHD_INTRADAY_HORIZON, MT_MINUTE_HISTORY_START, per-symbol first date)`)
is the intended mechanism: full history by default, narrowable per-deployment via
the operator env var (never via a hard-coded month clamp, which would violate the
project's no-magic-defaults rule).

**Action (in-scope for this slice's planning, doc-only):** remove the obsolete
`MINUTE_HISTORY_MONTHS = 24` NFR from the 140 architecture doc so the spec stops
contradicting the (correct) implementation. `_resolve_minute_history_start` is
**unchanged** — 162 threads its existing full-history result into the
coverage-aware seeder. Coverage-aware seeding makes the wide window cheap anyway:
a fully-covered long-lived symbol seeds **zero** rows regardless of window width;
only genuinely-missing sessions are fetched.

### Promote `_group_into_ranges` to a shared helper

`compute_missing_ranges._group_into_ranges` already implements exactly the
contiguous-run grouping we need. Rather than duplicate it (DRY), promote it to a
module-level shared function (e.g. `group_sessions_into_ranges`) that both
`compute_missing_ranges` and the new `compute_missing_minute_sessions` import.
No behavior change to the daily path.

### Patterns and Conventions

- Cagg / table names from `GRANULARITY_SOURCE`, never literals.
- Session-calendar join identical to `compute_missing_ranges` (single source of
  truth for the join shape).
- Parameterized SQL only (per project SQL rule).
- Progress output via the existing `get_logger`/`_logger` at INFO, consistent
  with slice 160's backfill progress style (`seeded N/<total> symbols, M gaps`).

## Implementation Details

### New / changed source

| File | Change |
|------|--------|
| `data/gaps/minute_coverage.py` (new) | `build_minute_coverage_index(conn) -> dict[str, set[date]]` and `compute_missing_minute_sessions(conn, symbol, coverage_index, from_ts, to_ts) -> list[GapRange]` |
| `data/gaps/compute_missing_ranges.py` | Promote `_group_into_ranges` → shared `group_sessions_into_ranges`; keep daily behavior identical |
| `data/gaps/update_data_gaps.py` | Minute branch accepts optional `precomputed_ranges`; when provided, insert those ranges instead of the single-span short-circuit |
| `data/acquisition/daemon/minute.py` | `run_minute_cycle` builds the coverage index once and threads it in; `_do_minute_symbol` computes missing ranges and passes them to `update_data_gaps`; seed-phase progress logging |
| `constants.py` | Centralize the coverage-index statement-timeout as a named constant (no literal at the call site) |
| `project-documents/user/architecture/140-arch.data-quality-operations.md` (doc-only) | Remove the obsolete `MINUTE_HISTORY_MONTHS = 24` NFR and the `history_months` minute default (dead AlphaVantage workaround; see *History window* decision) |

### Seed-phase progress output

During the seed pass the daemon emits an INFO line periodically, e.g. every 250
symbols and at completion:

```
minute seed: 2500/12043 symbols scanned, 18342 gap rows seeded
minute seed: complete — 12043 symbols, 21107 gap rows seeded
```

Counts are accumulated in `run_minute_cycle`; no new persistence.

### Carry-forward / re-fire correctness

The existing `update_data_gaps` carry-forward (prior `attempt_count` for the
same `fetch_status`) is preserved: seeding real holes still routes through the
same INSERT-with-carry-forward, so a re-seed after a partial attempt does not
reset a symbol's retry history. The `_has_any_gaps` re-fire (gap rows deleted but
bars present) now re-seeds **only genuinely-missing sessions** — the interaction
the regression test pins.

## Integration Points

### Provides to Other Slices

- A universe-scale coverage-index primitive (`build_minute_coverage_index`) that
  slice 163's before/after verification and any future coverage reporting can
  reuse.

### Consumes from Other Slices

- The `minute_4hour_ohlcv` cagg (slice 152 consolidation) and the
  `trading_sessions` calendar (slice 145). Both are present in production.

## Success Criteria

### Functional Requirements

1. **Partially-covered symbol with a past hole** — seeding produces gap rows
   covering only the missing sessions, **not** a `[2004, today]` span.
2. **Fully-covered symbol** — seeding produces **zero** gap rows.
3. **Empty symbol** (no bars) — seeding produces gap rows covering full history
   `[history_start, today]` (as today, but now expressed as session-contiguous
   ranges).
4. **`_has_any_gaps` re-fire** — after deleting a covered symbol's gap rows,
   the next seed recreates **only** genuinely-missing sessions.
5. **Restart on mostly-complete universe** — the chunk loop attempts near-zero
   chunks for already-covered symbols (the operational goal).
6. **Seed-phase progress** — the daemon emits periodic `minute seed: N/<total>`
   INFO lines during the seed pass.

### Technical Requirements

- New code carries unit tests: coverage-index builder (grouping/shape),
  session-diff (past-hole, fully-covered, empty, delisted-clamp cases), and the
  `precomputed_ranges` path of `update_data_gaps`.
- The `_has_any_gaps` regression test lives under
  `test/unit/data/acquisition/daemon/` and pins the re-fire → real-holes-only
  interaction.
- All existing gap/daemon tests pass unmodified (daily path behavior unchanged).
- `ruff` + strict `pyright` clean.

### Verification Walkthrough

> **Note:** all raw-`minute_ohlcv` / cagg data reads below must be run by the
> operator via `psql`/DataGrip as `postgres`/owner — the MCP `trading_app` role
> cannot `SELECT` OHLCV/gap tables. Production DB is `<db-host>:5432/trading`.
> **Do not restart the production minute daemon until this slice lands.**
>
> **CLI correction (found during Phase 6, 2026-07-17):** the commands below
> use `mt data daemon run --minute --symbols <SYM>`, **not**
> `mt data pull --granularity minute --symbols <SYM>` (an earlier draft of
> this walkthrough specified the latter, which does not exist as written —
> `pull` takes a positional `1d`/`1m` argument, not a `--granularity` flag —
> and even corrected to `mt data pull 1m --symbol <SYM>` it silently routes
> through `run_minute_refetch`, a different, non-coverage-aware code path,
> rather than `run_minute_cycle` — see
> `user/reference/minute-fetch-code-paths.md` and slice 165, filed to fix
> this divergence).

**1. Unit tests pass:**
```bash
uv run pytest test/unit/data/gaps/ test/unit/data/acquisition/daemon/ -q
```
Expected: all green, including the new
`test_minute_coverage.py` and the `_has_any_gaps` regression test.

**2. Coverage index is correct and fast (production, operator via psql):**
```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT symbol, date_trunc('day', time_bucket)
FROM minute_4hour_ohlcv
GROUP BY symbol, date_trunc('day', time_bucket);
```
Expected: single Finalize HashAggregate over a parallel Gather, ~3s total (not
minutes). This is the query `build_minute_coverage_index` issues.

Captured (2026-07-17, production `trading`): Finalize HashAggregate,
`rows=2425433`, Gather with 13 workers launched, `Buffers: shared hit=64245`
(all cache hits this run), Planning Time 1269ms, Execution Time 2956ms
(~4.2s total including planning). Universe cardinality:
`count(DISTINCT symbol)` / `count(*)` over `minute_4hour_ohlcv` — record the
two numbers precisely (an earlier capture attempt returned an ambiguous
pasted value; re-run and label each column explicitly before trusting it).

**3. Fully-covered symbol seeds nothing.** Pick a symbol known to be fully
backfilled (operator confirms via a bounded count). Delete its minute gap rows,
run one scoped cycle, and confirm no `[2004, today]` row appears:
```bash
# operator: DELETE FROM data_gaps WHERE symbol='<covered>' AND granularity='minute';
mt data daemon run --minute --symbols <covered> -v
# operator: SELECT gap_start, gap_end, fetch_status FROM data_gaps
#           WHERE symbol='<covered>' AND granularity='minute' ORDER BY gap_start;
```
Expected: zero rows seeded (or only today's partial session), and the `-v`
chunk output shows near-zero chunks attempted — **not** ~69.

Captured (2026-07-17, AAPL): `minute seed: complete — 1 symbols, 0 gap rows
seeded` (via the corrected `daemon run` command); `SELECT count(*) FROM
data_gaps WHERE symbol='AAPL'` → 0. **Pass.**

**4. Partially-covered symbol with a past hole seeds only the hole.** Using a
test fixture (or an operator-prepared symbol) with a known interior gap,
confirm the seeded ranges cover only the missing sessions.

Captured (2026-07-17, TSLA, known real interior holes 2023–2025):
first attempt (pre-fix, via the wrong `run_minute_refetch` path) produced
one row spanning `20200418–20260716` (23 chunks) — this surfaced a real bug,
**not** expected coverage-aware behavior; see Bugs Found below. After fixing
the bug and re-running via the corrected `daemon run --minute --symbols
TSLA` command: `minute seed: complete — 1 symbols, 5 gap rows seeded`,
7 chunks fetched (`20230917–20260715`), final `data_gaps` rows are 6 tight
`PROVIDER_HOLE` ranges (5 seeded + coalescing), e.g.
`2023-07-19→2023-09-17`, `2023-09-17→2024-01-15`, `2024-01-15→2024-05-14`,
plus three single-day holes — all confirmed-empty-by-provider after the
chunk loop ran, not a `[2004, today]` span. **Pass** (post-fix).

**5. Empty symbol seeds full history.** A symbol with no minute bars seeds
session-contiguous ranges spanning `[history_start, today]` — verify the chunk
loop then backfills it normally.

Not yet captured against production — no empty (never-fetched) symbol was
exercised in this pass. Deferred to next operator session or slice 165.

**6. Seed-phase progress is visible:**
```bash
mt data daemon run --minute --symbols <SYM> -v 2>&1 | grep 'minute seed:'
```
Expected: periodic `minute seed: N/<total> symbols scanned, M gap rows seeded`
lines, ending with a `complete` line — no long silent stretch.

Captured (2026-07-17): the `complete` line appears reliably
(`minute seed: complete — N symbols, M gap rows seeded`); for a single-symbol
scope the periodic (every-250-symbols) line never fires since N=1, which is
expected, not a bug. **Pass.**

**Bugs found and fixed during this walkthrough:**
1. **`date_trunc` type mismatch** (fixed in this slice, commit `ea5ac83`):
   `build_minute_coverage_index` stored `date_trunc('day', time_bucket)`
   results (a `timestamptz`/`datetime`) directly as dict keys, while
   `compute_missing_minute_sessions` compared against `session.date()` (a
   plain `date`). The two never matched, so every symbol appeared fully
   uncovered and seeded one full-history span — reproducing the exact bug
   this slice exists to fix. Caught only because production verification
   used real DB rows; the unit tests' mocked fixtures used plain `date(...)`
   objects and never exercised the real psycopg return type. Fixed by
   normalizing to `.date()` in `build_minute_coverage_index`; regression
   tests added in `test_minute_coverage.py`.
2. **Wrong CLI command in this walkthrough** — see the correction note above
   and slice 165.

## Operational-Fix Re-Audit

The four operational fixes already committed to the tree are re-audited against
the new seeder:

| Fix | Location | Verdict |
|-----|----------|---------|
| `_has_any_gaps` seed trigger | `daemon/minute.py:255` | **Root-cause-adjacent.** The trigger itself is correct; it was firing the *wrong seed*. Once seeding is coverage-aware, the re-fire becomes correct (re-seeds only real holes). **Regression test required** (Success Criterion 4). |
| EODHD 404 → EMPTY | `data/acquisition/outcomes.py` (classify) | **Clean / orthogonal.** 404 on an intraday window means "no data for window," not a contract violation; unaffected by seeding changes. No change. |
| `httpx.TimeoutException` retry | inside `eodhd_get` | **Clean.** Below the gap layer; independent of seeding. No change. |
| `PoolTimeout` one-line WARNING | `_process_minute_symbol` (`minute.py:185-189`) | **Clean.** Process-boundary handler; independent of seeding. No change. |

Only the first requires a code-adjacent action, and that action is a test, not a
behavior change.

**Phase 6 re-audit outcome (T14):** confirmed as designed. `_has_any_gaps`
trigger is unchanged at `daemon/minute.py:290`; the T11 regression test
(`test_refire_seeds_only_real_holes_not_full_history_span`) pins that a
re-fire now recreates only genuinely-missing sessions via
`compute_missing_minute_sessions`, not a `[2004, today]` span. The other three
fixes (EODHD 404→EMPTY in `outcomes.py`, `httpx.TimeoutException` retry in
`eodhd_sync.py`, `PoolTimeout` WARNING at `minute.py:218`) are untouched by
this slice's diff — verified via `git diff` against the pre-slice tree. Three
no-change verdicts, one test-covered verdict, as designed.

## Risk Assessment

### Technical Risks

- **Cagg staleness re-seeding today's session.** Bounded by the ≤1h refresh
  policy; worst case is one extra paid call for today, self-correcting next
  cycle. Accepted (documented above).
- **Memory footprint of the coverage index.** `{symbol: set[date]}` for ~12k
  symbols × ~6k days ≈ tens of millions of small date entries. This must be
  measured during implementation; if it proves heavy, the index can be built
  per-batch of symbols rather than universe-wide (the grouped query already
  supports a `WHERE symbol = ANY(%s)` restriction). Note in tasks.

### Mitigation Strategies

- Measure coverage-index memory on the production universe during Phase 6 and
  record it in the walkthrough. If it exceeds a comfortable bound, switch to
  batched index construction (query already supports it) — no design change.

## Implementation Notes

### Development Approach

1. Promote `_group_into_ranges` → `group_sessions_into_ranges`; keep daily tests
   green (pure refactor, verify first).
2. Add `data/gaps/minute_coverage.py` with the two functions + unit tests
   (past-hole, fully-covered, empty, delisted-clamp).
3. Add the `precomputed_ranges` path to `update_data_gaps` + unit test.
4. Wire `run_minute_cycle` / `_do_minute_symbol`; add seed-phase progress.
5. Add the `_has_any_gaps` regression test.
6. Run the verification walkthrough against production (operator-assisted psql),
   fill in concrete symbols/counts/EXPLAIN, then hand back for the daemon
   restart decision.

### Effort

3/5 (per slice plan — new module + two rewirings + regression coverage; no
schema/migration change).
