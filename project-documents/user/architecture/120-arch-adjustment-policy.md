---
docType: architecture-decision
component: data-acquisition
project: trading
parent: user/architecture/120-arch.data-acquisition.md
relatedSlices: [127, 128]
archIndex: 120
dateCreated: 20260427
dateUpdated: 20260427
status: accepted
---

# ADR 120-arch-adjustment-policy: Split/dividend adjustment policy

## Context

Slice 127 retired AlphaVantage as the minute data provider and replaced
it with EODHD, while simultaneously introducing the split/dividend
adjustment layer that consumers of minute OHLCV need for backtesting
and strategy development. Two architectural decisions had to be made
together:

1. **Where adjusted prices come from** — provider-mutated history (the
   provider returns prices already rebased), query-time computation
   (raw stored, adjusted derived on read), or stored-side-by-side
   (raw and adjusted both persisted at write time).
2. **Where corporate-action ground truth lives** — pulled from the
   minute provider, the daily provider, or stored as a first-class
   table in the daily database.

These choices matter because they shape the data shape every consumer
sees, the storage budget, the recompute cost when corporate actions
revise, and the operator's confidence signal that adjustments are
correct.

Two probes against the paid EODHD API verified the foundations before
implementation began:

* **Chunk-size probe** — `scripts/probe_eodhd_chunk_size.py`. EODHD
  `/intraday?interval=1m` delivers 76,083 bars in a single 120-day
  request (1.2s, 10.6 MB JSON). Server-enforced 120-day cap returns
  clean HTTP 422 on overshoot. Raw artefacts:
  `project-documents/user/research/eodhd-chunk-size-probe/`.
* **Adjustment-formula probe** — `scripts/probe_eodhd_adjustment.py`.
  EODHD's documented formula `k = adjusted_close / close` round-trips
  exactly (0.000000% error) on every daily bar; AAPL 4:1 split on
  2020-08-31 produces a clean `k_after / k_before = 4.000000`
  transition. Raw artefacts:
  `project-documents/user/research/eodhd-adjustment-probe/`.

The probes confirmed: adjustment is a normal piece of code, not a
research project. The k-factor formula is exact; EODHD's per-day k is
internally consistent; the corporate-action data is well-defined.

## Decision

Slice 127 ships the following policy.

### Storage: raw + adjusted side-by-side on a single hypertable

The `minute_ohlcv` hypertable carries both raw OHLCV columns
(unchanged from prior slices) and adjusted OHLCV columns added by
migration `010_adjusted_columns`:

```
adj_open    NUMERIC(20, 8)
adj_high    NUMERIC(20, 8)
adj_low     NUMERIC(20, 8)
adj_close   NUMERIC(20, 8)
k_factor    NUMERIC(20, 12)
adjusted_at TIMESTAMPTZ
```

All adjusted columns are NULLABLE so existing rows from before the
adjustment layer landed continue to query cleanly. The adjusted
columns are populated atomically with the raw columns: the writer
uses a single transaction that COPYs into a TEMP staging table
(carrying both raw and adjusted columns) and then INSERTs into the
hypertable with `ON CONFLICT (symbol, time) DO NOTHING`. A crash
between raw and adj writes is impossible — they land together or not
at all.

Adjusted column precision (`NUMERIC(20, 8)`) is intentionally higher
than raw (`NUMERIC(12, 4)`) because `adj = close × k_factor` where
`k_factor` is `NUMERIC(20, 12)`; the product needs the headroom.

### Corporate-action ground truth: dedicated tables on the daily DB

Migrations `003_splits` and `004_dividends` add two dedicated tables
to the daily database, both with PK `(symbol, ex_date)`:

```
splits     (symbol, ex_date, ratio_to, ratio_from, source, fetched_at)
dividends  (symbol, ex_date, amount, currency, source, fetched_at)
```

EODHD is the single source of truth: `mt data adjustment ingest
--symbol SYMBOL` calls `/splits/{ticker}` and `/div/{ticker}` and
upserts via `ON CONFLICT (symbol, ex_date) DO UPDATE`. The dividend
`amount` stored is EODHD's `unadjustedValue` (the cash actually paid
on the ex-date), not `value` (post-split-rebased) — the k-factor
formula needs the as-paid amount.

### k-factor: deterministic, recomputable on demand

The k-factor for `(symbol, target_date)` is the cumulative multiplier
that converts raw close on `target_date` to EODHD-style adjusted
close:

```
adjusted_close = raw_close × k_factor(symbol, target_date)

k_factor = ∏ (split.ratio_from / split.ratio_to)   for splits with ex_date > target_date
        × ∏ (prev_close - amount) / prev_close      for dividends with ex_date > target_date
```

`prev_close` is the close on the most recent trading day strictly
before each dividend's ex-date, read from `dailyohlcvadjusted.close`
on the daily DB. (See project memory
`project_av_daily_close_semantics.md`: AV's `close` column is raw
historical close, not adjusted.)

The function is pure: `Decimal` throughout, no I/O, no caching. The
writer pre-loads splits, dividends, and prev_closes per-symbol once
at the start of an update job and broadcasts the per-NY-trading-day
k across the chunk's rows.

### Continuous verification: Stage A always-on, Stage B deferred

`mt data adjustment verify --symbol --from --to --tolerance` is the
operator's confidence signal. **Stage A** (shipped) recomputes the
expected k-factor against the *current* corporate-actions tables and
compares to the stored `adj_close`. Per-day rollup, exit code 1 if
any day exceeds tolerance. Default tolerance is `0.0001` absolute
price units — well above Decimal-vs-float roundoff (~5e-9 in
practice) and well below any real divergence.

**Stage B** (deferred) would fetch EODHD's published `adjusted_close`
per date via `/eod` and cross-check end-to-end. The per-symbol-per-date
call volume should be served by EODHD's bulk-EOD API (1 call covers a
full exchange) when that is wired in. Stage A catches the most
common drift cause (corporate-action ingestion lagging the stored
rows); Stage B would catch errors in the underlying corporate-actions
tables themselves. Stage A is the operator's primary signal today.

## Consequences

### Storage cost (acceptable)

Adjusted columns roughly double the per-row size on `minute_ohlcv`
(raw OHLCV in `NUMERIC(12,4)`, adjusted OHLCV in `NUMERIC(20,8)`,
plus `k_factor` and `adjusted_at`). Day-one impact on the test DB is
minimal; slice 128 will measure cost at full backfill scale before
the production deployment.

### Recompute cost (manageable)

When a new dividend or revised split lands for a symbol, every prior
adjusted row for that symbol becomes stale. Stage A's verifier
detects the drift; the recompute is "delete and re-fetch the affected
rows" today (clean and idempotent thanks to migration 011's UNIQUE
(symbol, time)). Bulk recompute as a first-class feature is recorded
as future work — needed at production scale, not at slice-127 scale.

### Operational invariant: no NULL-adj rows on writer-written ranges

The writer pairs raw + adjusted in a single transaction. Rows from
*before* the adjustment layer existed (pre-migration 010) carry NULL
adj columns by design. The verifier scopes itself to rows with
`adjusted_at IS NOT NULL` so legacy rows don't generate noise. A
back-fill job to populate adj columns on legacy rows is not in
scope; it's a slice-128-or-later concern.

### `MT_ALPHAVANTAGE_API_KEY` retained

The AV minute provider is dormant
(`src/manta_trading/data/historical_minute/providers/alphavantage.py`,
unwired from runtime but kept on disk). The daily AV provider
(`src/manta_trading/data/acquisition/daily/providers/alphavantage.py`)
remains active — it sources the `dailyohlcvadjusted.close` values the
adjustment layer relies on for `prev_close` lookups. The settings
field remains required.

### CLI surfaces

* `mt data adjustment ingest --symbol [--since DATE]` — pulls EODHD
  splits and dividends.
* `mt data adjustment verify --symbol [--from] [--to] [--tolerance]
  [--json]` — Stage-A verifier.
* `mt data minute update SYMBOL [--from] [--to]` — `--from/--to` is
  ad-hoc backfill mode, doesn't touch `acquisition_state`.

(Originally drafted as `mt data quality verify-adjustment`; placed
under `mt data adjustment` so all corporate-action operator commands
sit together.)

## Alternatives considered

### A. Provider-mutated history

EODHD already publishes `adjusted_close` on its daily endpoint, so we
could simply consume those values without recomputing. This was
rejected because:

* Provider revisions silently rewrite history with no audit trail.
* Backtests would change retroactively when EODHD pushes a revision.
* No way to verify correctness — we'd be trusting one black box.

The chosen design stores both raw (immutable, what actually traded)
and adjusted (derived, recomputable, verifiable) so consumers can
trace exactly which corporate actions produced any given adjusted
value.

### B. Query-time view (compute adjusted on read)

Store only raw + corporate-actions; compute `adj_close` on every
read via a SQL view or materialized view. Rejected because:

* Read-heavy workloads (backtests scanning years of minute bars) pay
  the recompute cost on every query rather than once at write.
* The daily-DB join required for `prev_close` lookups becomes a
  per-query cost on a hot path.
* Storage savings (~2× columns on `minute_ohlcv`) are not material
  at our scale.

The chosen design pays the cost once at write time and amortizes
across all reads.

### C. Deferral (don't adjust until consumer asks)

Ship slice 127 as raw-only and add adjustment in slice 128 or later.
Rejected because:

* Backtests are blocked without adjusted prices.
* Adjustment correctness is the bottleneck for downstream confidence;
  delaying it just delays the verification signal.
* The k-factor probe demonstrated the math works — there's no
  research overhang to defer.

### D. Adjustment metadata in a separate table

`minute_ohlcv` stays raw-only; new `minute_ohlcv_adj` table joins on
`(symbol, time)`. Rejected because:

* Doubles the row count and forces a join on every adjusted-price
  query.
* Defeats the atomic-write invariant — separate tables can't be
  written in a single COPY.
* Storage doesn't materially differ; the join cost dominates.

The chosen design keeps everything on one hypertable.

## Provider compatibility contract (slice 128)

The minimum a provider stack must supply for the slice 127 + 128
adjustment + verification pipeline to function:

| Capability | Required | Why |
|---|---|---|
| Raw intraday OHLCV | Yes | The bars we store; must be unadjusted so `adj = raw × k` is meaningful |
| Raw daily close | Yes | `prev_close` for the dividend factor in `k_factor()` |
| Splits, complete | Yes | Multiplicative factor in `k` |
| Dividends, complete with `amount` and `ex_date` | Yes | Dividend factor in `k` |
| Daily `adjusted_close` | Optional | Enables Stage B cross-check; not load-bearing |
| Bulk endpoints | Optional | Backfill optimization; not load-bearing |

Providers known to satisfy: **EODHD (✓ — production minute, daily, and
corporate-actions source as of slice 128)**, AlphaVantage daily (✓ in
principle but the AV account was cancelled 2026-04-27; the
`AlphaVantageDailyProvider` class remains on disk and is selectable via
`MT_DAILY_PROVIDER=alphavantage` if a future need arises), Polygon
(✓ via `adjusted=false` selectability — would be a parallel slice if
adopted), Finnhub (✓ if daily-raw confirmed). Providers known to fail:
Yahoo (intraday is adjusted-only).

A practical consequence of the slice 128 close: minute and daily share
**one** provider (EODHD) by default. Splitting them is supported by the
`build_minute_provider` / `build_daily_provider` seams but is not the
recommended path absent a strong reason.

A provider that returns only adjusted prices on either endpoint cannot
be used as the primary source. Such a provider can still serve as a
cross-check input to a parallel Stage-B-style verifier — but never as
the source of stored bars.

## Outlier handling — Non-Goal

The platform performs **no outlier removal, smoothing, or forward-fill
at storage time. Ever.**

Reasoning:

1. Outlier-ness is strategy-defined and time-varying. A "bad print" to
   a tick-level mean-reversion strategy is a routine event to a
   30-minute momentum strategy. Storing a smoothed value would
   pre-decide that question for every downstream reader.
2. Raw data must be immutable for reproducibility. A backtest run on
   data that was silently cleaned tomorrow produces a different
   answer than the same backtest run on raw data today; that drift
   is uncatchable from the strategy code.
3. Standard cleaning techniques actively destroy real signal. Z-score
   filters flag every legitimate corporate event (split, special
   dividend, halt-resume), and forward-fill fabricates trades that
   never happened. The validation-flags column (deferred until a
   validator populates it) is the right home for advisory annotations
   downstream.

The Stage-A verifier and the Stage-B `verify-against-eodhd-eod` checker
flag inconsistencies; they do not mutate stored data.

## Stage B is provider-coupled by design (slice 128)

The Stage B verifier ships in slice 128 as
`mt data adjustment verify-against-eodhd-eod`. Its name records its
source of truth.

This is intentional. Stage B's value *is* the cross-check against a
specific authoritative external source. A future Polygon equivalent
would be a parallel command (`verify-against-polygon-eod`), not a
generalised `verify-against-any-eod`. A provider-agnostic interface
would hide which source the operator was trusting on a given run, and
that ambiguity is exactly what the cross-check is meant to remove.

A consequence: switching providers means writing a parallel verifier
or accepting Stage A only. The provider compatibility contract above
documents which providers can serve as Stage B sources at all.

## Out of scope (recorded as future work)

* **(slice 128 closes the original Stage B item)** Stage B verifier
  shipped in slice 128 as `mt data adjustment verify-against-eodhd-eod`.
* **Bulk recompute** — when a new dividend lands, sweep all prior
  adjusted rows for that symbol and recompute. Today's path is
  delete + re-fetch.
* **Pre-engineering for spinoffs, return-of-capital, special non-cash
  dividends** — verifier would flag divergence; address case-by-case
  rather than pre-engineer. Initial coverage: regular splits + cash
  dividends only.
* **Back-fill adjustment columns on legacy rows** — pre-migration-010
  rows have NULL adj columns by design; a sweep job to populate them
  is recorded for slice 128 or later.

## Evidence

* Adjustment-formula probe — `scripts/probe_eodhd_adjustment.py`,
  artefacts in
  `project-documents/user/research/eodhd-adjustment-probe/`.
* Chunk-size probe —
  `project-documents/user/research/eodhd-chunk-size-probe/`.
* End-to-end walkthrough captured in slice 127 doc's "Verification
  walkthrough" section: `mt data adjustment ingest`, ad-hoc backfill
  across the 2020-08-31 split, SQL spot-check showing
  `k_factor = 0.242717` pre-split / `0.970867` post-split, Stage-A
  verifier passing all 9 days with ~5e-9 worst drift, synthetic
  drift demo (corrupted ratio → FAIL exit 1, restored → PASS exit 0).
* Integration tests:
  `test/integration/test_eodhd_integration.py::TestWriterAdjustment`
  and `::TestVerifierCatchesDrift` regression-protect the round-trip
  and the drift-detection properties.
