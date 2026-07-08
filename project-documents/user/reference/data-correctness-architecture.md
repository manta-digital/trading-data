---
docType: reference
project: trading
dateCreated: 20260429
dateUpdated: 20260430
status: draft
---

# Data Correctness Architecture

## Purpose

This document is the cross-slice acceptance contract for the manta-trading
data layer. It states what the system must guarantee, what it explicitly
does not guarantee, and which tools every operator (human or program)
must be able to invoke. Every slice in initiatives 100, 120, 140, 200,
220 must close one or more invariants from this document. Audit
convergence is checked against this document, not against the initiative
plan or any single slice's claims.

If a slice ships and a guarantee here is not yet covered by some slice,
the guarantee is broken. If a slice ships that contradicts a guarantee
here, the slice is wrong, not the document.

This is a target-state document. It describes the system as it must be
when foundation work is complete. Today's system does not yet meet most
of these guarantees; that is what the slices are for.

## Vocabulary

These terms have one meaning across the system. Code, comments,
documents, CLI help text use them consistently or are wrong.

- **Bar** — an aggregated OHLCV record at a fixed granularity (daily,
  minute, hourly). Has a single `time` (start of period) and a single
  trading-date.
- **Tick** — an individual market event (trade or quote). Has nanosecond
  timestamp and sequence number.
- **Granularity** — `daily`, `minute`, `tick`, plus aggregations derived
  from these. Stored in storage rows, used in CLI flags, used in
  acquisition state.
- **Provider** — an external data source (EODHD, AlphaVantage, Databento,
  Yahoo). Each is identified by a `ProviderType` enum value, never a
  string.
- **Acquisition state** — per `(symbol, granularity, provider)` tuple,
  records `last_attempt_ts`, `last_success_ts`, `status`,
  `error_message`. (Per-gap retry counts live in `data_gaps`, not on
  this row.) The provider field identifies the
  *responsible-going-forward* provider for that row, not the
  historical-source-of-bars-in-storage.
- **Bar provenance** — for any given bar in storage, the provider that
  ingested *that bar*. This is *not* tracked at the row level today and
  is not required to be — bars are provider-agnostic OHLCV. Provenance
  is tracked at the ingest-event level via the event-sourced ingest log
  (initiative 180).
- **Corporate action snapshot** — the set of (splits, dividends) for a
  symbol as of a particular `fetched_at` timestamp. Adjustments are
  computed against a snapshot. Re-adjustments occur when the snapshot
  changes.
- **k_factor as-of (S, D, CA)** — a deterministic function:
  `compute_k_factor_as_of(symbol=S, date=D, ca_snapshot=CA) -> float`.
  Given the same inputs, returns the same output. The single source of
  truth for adjustment math.
- **Coverage** — for a `(symbol, granularity, date_range)`, the set of
  trading-days (or trading-minutes for minute granularity) where the
  expected bar count from the trading calendar matches the stored bar
  count within tolerance.
- **Gap** — a contiguous date-range within scope where coverage is below
  threshold. Classified by reason: `HOLIDAY`, `PRE_HISTORY`,
  `POST_DELISTING`, `FILLABLE`, `UNFILLABLE`, `PENDING`.
- **Trading day / trading minute** — a date or minute-timestamp that the
  trading calendar marks as in-session for the relevant exchange.
  Non-trading dates are SKIP, never FAIL.
- **Audit** — a comparison between two independent sources of truth that
  both claim to describe the same reality. Cross-vendor audit compares
  our stored data against a different vendor's data for the same symbol
  and date.
- **Verification** — a comparison between our stored data and the same
  vendor's later representation of the same data (e.g., minute bars we
  stored vs that vendor's daily EOD that should reconcile). Verification
  catches our ingestion bugs; audit catches vendor errors.

## Invariants

These are the guarantees the foundation must provide. Each is named
(I1, I2, ...) so slices can reference them.

### I1 — Adjustment correctness

For every stored `(symbol, time)` row in `minute_ohlcv` or `daily_ohlcv`
where `adj_close` is populated:

`adj_close == close * compute_k_factor_as_of(symbol, trading_date(time), ca_snapshot=current)`

within numerical tolerance (1e-6 absolute on price, configurable). The
function is deterministic for fixed inputs. There is exactly one
implementation in the codebase. Ingestion writes `adj_close` using it.
Re-adjustment recomputes `adj_close` using it. Stage A verifies stored
rows against it. Stage B compares the same function's output against
EODHD's published k_factor; disagreement is reported per-day and never
treated as numerical noise above tolerance.

When a corporate action (split or dividend) is added to the
`splits`/`dividends` tables for a symbol, all rows with `time` after
`min(ex_date)` for that symbol must be re-adjusted before being
returned by query APIs. Stale `adj_close` is a defect, not an
acceptable lag.

### I2 — Coverage truthfulness

For every `(symbol, granularity)` we have ingested any data for, the
system can answer:

- For any date range, what trading-days are present, partial, missing.
- For each missing day, why (HOLIDAY, PRE_HISTORY, POST_DELISTING,
  FILLABLE, UNFILLABLE, PENDING).
- The most recent successful ingest timestamp.
- The most recent re-adjustment timestamp.
- The corporate action snapshot timestamp current bars were adjusted
  against.

The status command and the coverage command and the daemon's
work-queue-readiness check must use the same underlying coverage
computation. They must not derive different answers from the same DB
state.

### I3 — Provider tagging clarity

`acquisition_state.provider` identifies the provider responsible for
keeping that `(symbol, granularity)` fresh going forward. It does not
identify the historical source of stored bars. Code that filters by
this field for status display, work-queue admission, or daemon
scheduling is asking "who is keeping this fresh?" not "who ingested
these bars?"

When the configured provider for a granularity changes (cutover),
either: (a) a one-time re-tag of `acquisition_state.provider` happens
as a managed migration, or (b) status and work-queue queries handle
the transition explicitly. Silent disagreement between configured
provider and tagged rows is a defect. The dry-run incident on
2026-04-29 (status reported zero rows because of this exact
mismatch) must not be possible after foundation work lands.

Bars in `minute_ohlcv` and `daily_ohlcv` carry no provider column.
Bar provenance is recorded at the ingest-event level (initiative 180).

### I4 — Single source of truth for trading sessions

For any `(exchange, date)` pair, the trading calendar answers:
`is_session?`, `rth_open`, `rth_close`, `eth_open`, `eth_close`,
`is_half_day?`. There is exactly one such function in the codebase.
The daemon, the gap detector, the coverage scanner, the verifier, the
report builder all call it. Hard-coded session-hour assumptions
anywhere in code are a defect.

DST transitions, half-days, and exchange holidays are handled by data
in the calendar table, not by special-case code paths.

### I5 — Idempotent rebuild

For any in-scope `(symbol, granularity, date_range)`, the operator can
run a single command that:

1. Re-fetches bars from the configured provider for that range.
2. Re-applies the current corporate action snapshot.
3. Recomputes `k_factor`, `adj_open`, `adj_high`, `adj_low`,
   `adj_close`.
4. Updates the coverage and freshness map.

The command is idempotent — running it twice produces the same final
state. It is safe — running it does not corrupt unrelated data. It is
cheap enough to be used routinely, not heroically. When a vendor bug
is reported and fixed, "rebuild the affected window" is a single
command, not a script-writing exercise.

### I6 — Calendar-correct verification

Every verification and audit operation iterates trading sessions, not
calendar dates. Saturday, Sunday, holidays, and half-day-after-close
periods are SKIP, not FAIL. The Stage B incident on 2026-04-29
(non-trading days marked FAIL) must not be possible after foundation
work lands.

When verification asks an external endpoint for data on a non-trading
date, the absence of a response is expected behavior, not a failure
signal.

### I7 — Cross-vendor audit available

For every `(symbol, granularity)` the system supports, at least one
*independent* (different vendor, different ingestion path) audit can
be run. The audit reports per-day agreement between our stored data
and the secondary source. Disagreements above tolerance are loud,
classified, and persisted to the report artifact.

Single-vendor verification (Stage B) is necessary but not sufficient.
A system that only cross-checks one vendor against itself (the same
vendor's `/eod` endpoint vs the same vendor's `/intraday` rolled up)
cannot detect vendor-side errors and is not audit-complete.

### I8 — Debug primacy

The state of any subsystem is queryable through a `mt data debug`
command that reports the underlying DB state directly. When the
status command and the storage table disagree, the debug command
shows the storage table, and the status command is the bug.

Operators (human or AI) reproducing a problem do so by running
`mt data debug ...`, not by typing SQL. Queries we re-derive
manually more than twice become debug commands.

### I9 — Loud failure

Silent fallback values are forbidden. Silent stale data is
forbidden. Silent provider misconfiguration is forbidden.

When a guarantee here cannot be met for some scope (symbol gone
delisted at provider, vendor returned partial data, coverage
threshold not satisfied), the system surfaces the failure
prominently and persistently — in the report artifact, in the
status command, in the daemon log, in the CLI exit code.

A backtest that runs against silently-stale or silently-incomplete
data and produces a result is the worst possible outcome. The
system must refuse, warn, or annotate, not silently produce.

### I10 — Tooling consistency

Every granularity (`daily`, `minute`, `tick`) supports the same
operator-facing surface:

- `mt data <gran> status` — fresh/stale/failed counts, daemon health.
- `mt data <gran> coverage` — per-symbol coverage rollup, gap
  classification.
- `mt data <gran> update / fetch / ingest` — ad-hoc range fetch.
- `mt data <gran> daemon` — scheduled freshness operation.
- `mt data <gran> backfill` — bulk historical fetch.
- `mt data quality {validate|report|fix|verify} --granularity <gran>`
  — quality operations.
- `mt data adjustment {ingest|verify|reapply}` — for granularities
  with corporate actions (equities, not futures).
- `mt data debug ...` — direct state queries.

A new granularity (tick) plugs into this surface; it does not invent
a new shape.

## Operator-facing surface

### Required CLI commands

These commands must exist and behave per their invariants when
foundation work is complete. Italicized commands are net-new from this
document; non-italicized exist today in some form and may need
revision.

- `mt data daily {status, update, daemon, coverage, backfill}`
- `mt data minute {status, update, daemon, coverage, backfill}`
- _`mt data tick {status, ingest, daemon, coverage, backfill}`_ —
  initiative 200; deferred until tick infrastructure lands.
- `mt data quality {coverage, gaps, validate, report, fix, verify}`
- _`mt data quality audit --vendor <secondary>`_ — cross-vendor
  audit; new.
- `mt data adjustment {ingest, verify, verify-against-eodhd-eod}`
- _`mt data adjustment reapply --symbol X [--from Y] [--to Z]`_ —
  forces re-adjustment from current corporate-action snapshot.
- _`mt data debug {acquisition-state, coverage-shape, k-factor-history,
  provider-tag-distribution, ingest-events}`_ — direct state inspection.

### Required artifacts

- **Quality report JSON** (initiative 140, slice 146) — versioned,
  round-trippable, includes scope, gap classification, validator
  results, audit results, generated_at, ca_snapshot_id.
- **Coverage map** (table or materialized view) — per
  `(symbol, granularity)`, shows date ranges with present/partial/
  missing classification, last-ingested-at, last-adjusted-at,
  ca-snapshot-as-of.
- **Ingest-event log** (initiative 180) — append-only record of every
  ingest action: `(provider, symbol, granularity, range, row_count,
  outcome, fetched_at, run_id)`.

## What is explicitly out of scope

- **Equity tick data.** Universe too large; not on the roadmap.
- **Order book depth (L2, L3, MBO) for tick.** Initiative 200 is L1
  only.
- **Cross-exchange consolidation for tick.** One vendor, one
  consolidation, deliberately.
- **Sub-millisecond live latency.** Not an HFT system.
- **Strategy logic, regime detection, backtest framework.** This
  document covers the data layer only.
- **Multi-tenant or shared deployment.** Single-operator system.

## Convergence audit

The system has converged when, for every invariant I1-I10:

1. There is a slice (or set of slices) closed (`status: complete`)
   that explicitly contributes to closing the invariant.
2. There is a verification command (CLI, test, or both) that an
   operator can invoke to confirm the invariant holds against current
   DB state.
3. The verification command, when invoked against current DB state,
   passes.

If any invariant has no closing slice, that is a planning gap.
If any invariant has slices but no verification command, that is a
tooling gap (close it with a debug-CLI slice).
If any invariant's verification command fails, that is a defect to
fix before further slice work proceeds against that invariant.

## Slice-to-invariant mapping

This section is the live audit table. Updated as slices are added or
revised. A slice closes an invariant when its acceptance criteria
include verifying the invariant holds.

| Invariant | Closing slices | Verification command | Status |
|-----------|----------------|----------------------|--------|
| I1 — Adjustment correctness | 143, 147 | `mt data audit --symbol X` | Designed. Issue #10 reproduces current violation; slice 143's `compute_k_factor` resolves it. |
| I2 — Coverage truthfulness | 142, 144, 145 | `mt data status` | Designed. `data_gaps` table + `data_status` view + daemon refactor. |
| I3 — Provider tagging clarity | 142 | `mt data status` | Designed. `acquisition_state` slimmed; status reads from data tables, not provider tags. |
| I4 — Single source of truth for trading sessions | 142, 144 | tests + `mt data status` | Designed. `compute_missing_ranges` reads `trading_calendar` exclusively. |
| I5 — Idempotent rebuild | 146 | `mt data refetch` | Designed. Single command rebuilds bars + adjustments + gap state for a window; daemon CA-detection handles re-adjustment automatically (no separate `--reapply-only` flag needed). |
| I6 — Calendar-correct verification | 147 | `mt data audit` | Designed. Trading-calendar-aware; non-trading days SKIP. Issue #9 resolved by this slice. |
| I7 — Cross-vendor audit | Future work (Yahoo extension to 147) | `mt data audit --vendor X` | Deferred. Single-provider audit ships in 147; second-vendor extension is future work. |
| I8 — Debug primacy | 145 (`mt data status` is the primary debug surface) | `mt data status` | Designed. Status command surfaces all per-symbol state; ad-hoc SQL not required for normal operation. |
| I9 — Loud failure | Cross-cutting; every slice has a loud-failure obligation | n/a (review) | Ongoing discipline; not a single-slice deliverable. |
| I10 — Tooling consistency | All daily/minute/tick slices must follow shape | review against this document | Ongoing discipline. Initiative 200 (tick) inherits slice 141-147 shape. |

## Notes

- This document supersedes any earlier verbal or in-conversation
  agreements about what "the data layer should look like." If you find
  yourself relying on remembered context, update this document
  instead.
- Adding an invariant means: name it (I11+), describe it in the
  Invariants section, add a row to the slice-to-invariant mapping,
  identify (or create) closing slices.
- Removing an invariant requires explicit justification in this
  document's history. We do not silently relax guarantees.
- Initiative 200 (futures tick) inherits I2, I3, I4, I5, I6, I7, I8,
  I9, I10. It does not inherit I1 (futures have no corporate
  actions). It adds tick-specific invariants (sequence-gap detection,
  continuous-contract correctness) that will be added here when
  initiative 200 lands.
