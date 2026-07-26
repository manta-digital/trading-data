---
docType: slice-design
slice: cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr
project: trading-data
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [166, 163, 168]
interfaces: [147, 182]
dateCreated: 20260720
dateUpdated: 20260726
status: in_progress
---

# Slice Design: Cagg-backed `data_status` bars summary — reach the sub-second NFR

## Overview

Slice 166 re-chunked `minute_ohlcv` and brought a single-symbol MIN/MAX from
10m47s to 0.68s, but the full-universe `data_status` read only improved
117.2s → 7.8s — still ~8× over the 140-arch NFR ("View latency stays
sub-second at full-universe scope"). The residual cost is **structural**: the
view's `bars_summary` CTE scans and groups the entire raw `minute_ohlcv`
hypertable (plus `daily_ohlcv`) on every read. No amount of raw-table
chunk tuning removes a full per-symbol aggregate over ~4.4B minute rows.

This slice rewrites `bars_summary` to derive `first_bar_ts / last_bar_ts /
bars_stored` per symbol from **continuous aggregates** instead of the raw
tables, preserving the view's exact column contract so `mt data status` and
every other consumer is unchanged, and documents the resulting cagg-lag
staleness bound.

> **Inherited requirement from slice 163 — documenting the staleness bound is not
> sufficient; this slice must *assert* it.**
>
> 163 established that a cagg informing an operational decision is a production
> input, not an optimization. Its refresh policy can stop — deliberate pause,
> crashed job, failed policy, out-of-band `alter_job`, restart mid-maintenance —
> and **resuming it does not heal the gap**, because a policy only reconsiders the
> last `start_offset` of data. On prod this silently drove a perpetual minute
> re-pull across ~349 symbols with no error surfaced anywhere; it was caught by the
> PM noticing chunk counts, not by any check.
>
> This slice creates the second cagg-backed read path, so it inherits that failure
> mode. `bars_summary` must call the shared `assert_cagg_fresh(conn, view_name)`
> helper (owned by slice 168, a hard dependency of this slice) and surface staleness
> rather than silently reporting stale coverage as fact. 168 lands first, so the
> helper exists when this slice starts — 167 consumes it and must never ship a
> second unguarded consumer.
>
> **`start_offset` alone is the wrong threshold.** It is set for refresh efficiency,
> not consumer tolerance. `daily_ohlcv`'s caggs — which `bars_summary` also reads —
> use 21/90/**270**-day offsets, so a policy stalled for three months would pass any
> check written against `start_offset`. Use
> `min(start_offset, <absolute ceiling this consumer requires>)`.
>
> Full reasoning: journal `20260725` ADR rules 2–4; operational half in
> `user/runbooks/cagg-maintenance-pausing.md`.

## Value

**Operational:** `mt data status` at full-universe scope becomes usable
interactively (sub-second, not ~8s). Every operator status check and
verification walkthrough benefits.

**Architectural:** closes the last leg of the 140-arch `data_status` NFR that
has been open since slice 142. Makes the view's latency **structurally
independent** of how the underlying bar tables are chunked — a durable fix,
not a tuning that can silently regress.

## Measured Baseline (captured 2026-07-20, prod `trading` DB, design phase)

Environment: PostgreSQL 17.7, TimescaleDB 2.23.0.

| Fact | Value |
|---|---|
| `data_status` full-universe read (post-166) | 7.8 s |
| NFR target | sub-second |
| Raw `minute_ohlcv` authoritative row count | **4,405,379,285** exact (see note) |
| Raw `daily_ohlcv` row count | 34,223,492 |
| `minute_4hour_ohlcv` cagg row count | 7,761,587 (5,871 symbols) |
| Minute `bars_summary` via 4h cagg group-by | 5.7 s |
| Daily `bars_summary` raw group-by | 3.8 s |

**Row-count note (settled by exact count, 2026-07-20):** the raw count is
**4,405,379,285** — exact `SELECT count(*)`, metadata-assisted, ~1.3 s
post-166. Three earlier figures were all wrong, each from a source that
looked authoritative: ~7.27B was `approximate_row_count` post-ANALYZE (still
~66% high on this compressed hypertable); ~918M was `SUM(minute_count)` over
the corrupted cagg (the ~21% materialization artifact, see §Critical
prerequisite); ~1.2B was an operator estimate extrapolated from the 5-min
cagg — poisoned by the same under-materialization.
Corrected compressed floor: 78 GB ÷ 4.405B ≈ 17 bytes/row. Standing rule
(journal 20260720): exact `count(*)` is the only authoritative row-scale
source; once slice 163 repairs the caggs and parity is verified,
`SUM(minute_count)` becomes a valid fast cross-check.

## Critical prerequisite (discovered 2026-07-20): minute caggs are ~79% under-materialized

> **This finding was discovered during 167's design phase and materially
> changes the slice. It is documented here per PM instruction; the slice will
> return to it. The cagg *repair* itself is being folded into slice 163 (cagg
> re-chunking), which must run before 167.**

While measuring the cagg-backed approach, the design phase found that **all four
minute continuous aggregates** (`minute_5min_ohlcv`, `minute_15min_ohlcv`,
`minute_hourly_ohlcv`, `minute_4hour_ohlcv`) are materialized with only ~21%
of the raw bars they should contain (9.5–21% across measured 2019+ years,
~28% pre-2019 by subtraction), spanning the entire 2004–2026 range:

| Year | Raw `minute_ohlcv` | Cagg `SUM(minute_count)` | Coverage |
|---|---|---|---|
| 2019 | 208,673,609 | 43,440,140 | 20.8% |
| 2021 | 280,079,556 | 46,267,456 | 16.5% |
| 2024 | 362,186,695 | 55,358,082 | 15.3% |
| 2025 | 442,655,155 | 59,833,368 | 13.5% |
| 2026 | 247,389,640 | 23,483,264 | 9.5% |

All four caggs report the *identical* materialized count per period, confirming
a common cause.

**Root cause:** slice 166's rechunk (`drop_chunks` + reinsert of the entire raw
`minute_ohlcv`) invalidated every materialized cagg region. The refresh
policies use `start_offset => INTERVAL '1 day'` (trailing), so on each run they
only re-materialize the last day — they **cannot self-heal history**. This is
the exact failure mode recorded in the merge-chunks adjacency lesson: *cagg
refresh during restructuring silently loses materialized rows; repair only via
`refresh_continuous_aggregate(..., force => true)` over the full range.*
The refresh jobs are running successfully (job 1003, 1708 runs, 0 failures) —
they are simply not scoped to repair history.

**Impact:** this is a live production integrity issue **independent of 167** —
any consumer reading the 4h / hourly / 15m / 5m rollups today gets aggregates
computed over ~21% of the data. No data is being lost or mis-written (the *raw*
table is intact and is what the daemon and the current `data_status` view
read), but the caggs are silently wrong.

**Decision (PM, 2026-07-20): fold the repair into slice 163.**
Re-chunking a cagg invalidates and re-materializes it regardless, so a
standalone repair slice would run the full re-materialization now and slice 163 would
re-refresh them again during its re-chunk — paying the full materialization
twice. Folding the `refresh_continuous_aggregate(..., force => true)` repair
into 163 does it once, as an intrinsic part of correctly restructuring the
caggs. **167 therefore depends on [166, 163]:** 167 cannot back `bars_summary`
with the 4h cagg (directly or hierarchically) until 163 has repaired it to full
materialization. Slice 163's plan entry should be treated as now-urgent for
this reason.

## Technical decisions

### D1 — Structure: hierarchical coverage cagg (Option 1)

Pointing `bars_summary` at the existing `minute_4hour_ohlcv` and grouping by
symbol is **5.7 s** — still over the NFR — because that cagg is itself
over-chunked (4,235 chunks; the same proliferation slice 163 addresses). A
per-symbol `GROUP BY` must Append-scan all 4,235 chunks with a partial
HashAggregate each. Even `SELECT DISTINCT symbol` over it is 1.4 s.

**Decision:** introduce a **hierarchical continuous aggregate**
`minute_coverage` built *over* `minute_4hour_ohlcv` with a wide (1-year) time
bucket, plus an analogous `daily_coverage` over `daily_ohlcv`:

```
minute_coverage  (cagg over minute_4hour_ohlcv):
  time_bucket('1 year', time_bucket) AS yr_bucket,
  symbol,
  SUM(minute_count) AS bars,
  MIN(time_bucket)  AS first_bucket,
  MAX(time_bucket)  AS last_bucket
  GROUP BY yr_bucket, symbol

daily_coverage   (cagg over daily_ohlcv, analogous, SUM(day_count) / COUNT)
```

`minute_coverage` materializes to **~15,195 rows** (5,871 symbols × ~22
years). `bars_summary` then groups *that*:

```
bars_summary AS (
  SELECT 'minute' AS granularity, symbol,
         MIN(first_bucket) AS first_bar_ts,
         MAX(last_bucket)  AS last_bar_ts,
         SUM(bars)         AS bars_stored
  FROM minute_coverage GROUP BY symbol
  UNION ALL
  <analogous daily branch over daily_coverage>
)
```

Grouping ~15k rows is sub-millisecond, **regardless of the 4h cagg's chunk
count**. This is the durability argument: the NFR holds structurally, not
contingent on chunk-health tuning that can regress.

**Rejected — Option 2 (group `minute_4hour_ohlcv` directly):** smaller surface
(no new cagg, just a view CTE rewrite) but only sub-second *after* 163
re-chunks the 4h cagg, and re-couples the NFR to the exact chunk-proliferation
problem slice 166 spent its effort breaking. If the 4h cagg ever drifts back
toward over-chunking, `data_status` silently regresses past the NFR again.
Option 1 is immune to that.

### D2 — Column contract preserved exactly

The view's output columns are unchanged: `first_bar_ts`, `last_bar_ts`,
`bars_stored` keep their names, types, and positions. Only the *source* of the
three `bars_summary` columns changes (cagg-derived instead of raw-scan). All
consumers — `mt data status` (`status_queries.py`, `status_table.py`),
`migrate_cold_start.py`'s verification, and any external reader — see identical
shape. `status_table.py` renders `first_bar_ts`/`last_bar_ts` via `_fmt_date`
(**date-only**, `%Y-%m-%d`) and `bars_stored` via `_fmt_int`; nothing in the
health logic (`gap_count`, `has_retry_exhausted`, `last_attempt_ts`) touches
`bars_summary`, so the rewrite cannot alter any `health` classification.

### D3 — Timestamp fidelity and staleness semantics (to document in the view)

**Bucket truncation:** `first_bar_ts`/`last_bar_ts` derive from `MIN`/`MAX` of
the 4h cagg's `time_bucket`, so they are truncated to the **4-hour bucket
start**, not the true first/last raw bar timestamp. The 1-year coverage bucket
does *not* coarsen this — `MIN/MAX(time_bucket)` are carried through as
aggregates, so fidelity is identical to reading the 4h cagg directly. For US
equities the earliest session bar (~09:30 ET = 13:30/14:30 UTC) buckets to
12:00 UTC (same UTC date), so the **date-only display is unaffected** in
practice. This bound must be stated in the view's doc comment.

**Cagg lag:** a cagg can only *lag* raw, never lead it. Coverage may understate
the very latest bars by at most:
`(minute_4hour_ohlcv refresh interval) + (minute_coverage refresh interval)` —
a two-hop bound because the coverage cagg is hierarchical. With the 4h cagg
refreshing hourly and the coverage cagg on a daily (or hourly) policy, this
bounds understatement of `bars_stored` / `last_bar_ts` to well under a day for
the trailing edge only; all settled history is exact. **The exact numeric bound
and the refresh-policy intervals chosen must be documented in the view's doc
comment** so operators reading `mt data status` understand that a just-fetched
symbol may show slightly-stale coverage until the next refresh tick.

### D3a — Freshness must be **asserted**, not assumed (inherited from slice 163)

D3's lag bound is `(parent refresh interval) + (coverage refresh interval)`. That
arithmetic holds **only while the policies actually run**. When a policy stops, the
bound is silently unbounded — and slice 163 proved on prod that this is not
hypothetical.

A refresh policy stops for many reasons beyond a deliberate pause: a crashed job, a
policy erroring on every fire, an `alter_job` issued outside our tooling, a restart
during maintenance. **Resuming it does not heal the gap**, because a policy only
reconsiders the last `start_offset` of data; everything older is stranded permanently
and no scheduled run ever revisits it. In 163 this drove a perpetual minute re-pull
across ~349 symbols with no error anywhere — found by the PM noticing chunk counts,
not by any check.

This slice creates the **second** cagg-backed read path and inherits that failure mode
directly. Documenting the bound (D3) is what 163 already did; it did not prevent the
incident.

**Decision:** `bars_summary` calls a shared
`assert_cagg_fresh(conn, view_name) -> FreshnessVerdict` before trusting cagg-derived
coverage. **Slice 168 owns the helper** (promoted from 140-plan future work on
2026-07-26) and is a **hard dependency** of this slice, so this slice *consumes* it
unchanged and adds only the `bars_summary` call site and the operator-facing surfacing
below — **167 must not ship a second unguarded consumer.** 168's D6 TTL verdict cache
is what keeps the guard inside this slice's sub-second NFR; no amortization scheme is
needed here.

Four independent signals, OR'd (none is sufficient alone), from one catalog read of
`timescaledb_information.jobs` + `job_stats`:

| Signal | Catches |
|---|---|
| `raw_max - cagg_max > threshold` | the 163 incident shape |
| `NOT scheduled` | any pause, including out-of-band `alter_job` |
| `now() - last_successful_finish > threshold` | crashed job still marked `scheduled` |
| `last_run_status <> 'Success'` | policy failing on every fire |

**Threshold is `min(start_offset, <absolute ceiling this consumer requires>)` — not
`start_offset` alone.** `start_offset` is set for refresh efficiency, not consumer
tolerance, and the two diverge badly. The divergence is structural here, not incidental:
per D4 both coverage caggs are **hierarchical or wide-bucketed**, so their
`start_offset` must be deliberately generous — wide enough to re-materialize
recently-changed parent buckets, since a trailing-1-day offset is precisely what caused
the prerequisite corruption. `daily_coverage`'s own `start_offset` (chosen in task 2.2)
is therefore far looser than any tolerance an operator reading `data_status` would
accept. A coverage cagg stalled for weeks still passes an `start_offset`-relative check
while reporting stale coverage as fact. This false negative was found by simulating the
detector before writing it.

*(Correction, 2026-07-26 — review F004.)* An earlier draft of this paragraph cited
"the daily caggs, whose offsets run to 21/90/**270** days" as the example. That
misattributed the hazard: per D1 the daily branch reads the new `daily_coverage` cagg,
not `daily_weekly`/`daily_monthly`/`daily_quarterly`, so those offsets are not on this
read path. The conclusion is unchanged and independently codified as
`MAX_COVERAGE_SOURCE_STALENESS`.

**Cost is not a concern** (measured on prod 2026-07-25): ~0.19 s for the cagg
leading-edge probe, ~0.75 s for the raw probe, both planning-dominated — negligible
against a view whose whole purpose is sub-second reads, and the probes are per-cagg,
not per-symbol.

**On trip:** surface staleness rather than silently reporting stale coverage as fact.
Unlike the daemon's coverage index — which fails safe by skipping work — `data_status`
is an operator-facing read, so the verdict should be *reported* (a stale-coverage
indicator plus an ERROR log naming the cagg, measured lag, and which signals fired),
not silently suppressed. Exact surfacing is a task-level decision; the constraint is
that a stale cagg must never be presented as current coverage.

**Deliberately not auto-remediating:** an automatic catch-up
`refresh_continuous_aggregate` inside a read path makes a heavy write a side effect of
a status query. Detect and report; catch-up stays with runbook R2.

Full reasoning: journal `20260725` ADR rules 2–4;
`user/runbooks/cagg-maintenance-pausing.md` (R1–R5).

### D4 — Refresh policy for the coverage caggs

`minute_coverage` and `daily_coverage` each get an
`add_continuous_aggregate_policy`. Because `minute_coverage` is hierarchical
(built over another cagg), its `start_offset` must be wide enough to
re-materialize recently-changed parent buckets — the trailing-1-day policy is
what caused the prerequisite corruption — and **must be re-verified against the
merge-chunks/cagg-invalidation lesson** before any future restructuring of the
parent.

**Offsets fixed at task 2.2 (2026-07-26), measured from prod, not estimated.**
Parent values read from `timescaledb_information.jobs`:

| Parent | Job | `schedule_interval` | `start_offset` | `end_offset` |
|---|---|---|---|---|
| `minute_4hour_ohlcv` | 1003 | 1 h | **1 day** | 4 h |
| `daily_ohlcv` (raw) | — | *no refresh policy* | — | — |
| | | | | |
| `daily_ohlcv` compression | 1010 | 12 h | `compress_after` 7 days | |

Chosen values, as constants in `constants.py` — never restated as literals:

| Coverage cagg | `start_offset` | `end_offset` | `schedule_interval` |
|---|---|---|---|
| `minute_coverage` | **30 days** | 4 h | 1 h |
| `daily_coverage` | **30 days** | 1 h | 1 h |

Reasoning, per side:

- **`minute_coverage`** — `start_offset` must exceed the parent's *entire*
  refresh window (1 day), not merely equal it, with margin for a parent backfill
  or repair that rewrites recent history. 30 days is that window plus a wide
  margin, deliberately generous: the asymmetry of the failure modes decides it.
  Too-wide costs one 1-year bucket per symbol per refresh; too-narrow strands
  history permanently, because no scheduled run ever revisits data older than
  `start_offset` — the exact shape of the ~79% under-materialization 163 had to
  repair. `end_offset` (4 h) matches the parent's, since refreshing closer to now
  than the parent has materialized would undercount the trailing edge.
  `schedule_interval` (1 h) matches the parent's cadence.
- **`daily_coverage`** — its source is the **raw** `daily_ohlcv`, which has no
  refresh policy at all, so the binding constraint is not a parent window but
  late-arriving and revised daily bars (provider restatements, adjustment
  rebasing). 30 days matches the minute side — one operator-visible number rather
  than two — and comfortably covers the 7-day compression horizon after which
  rows are no longer expected to change.

The D4 constraint is encoded mechanically as a unit test (`start_offset` ≥ parent
refresh window + margin), so a later edit cannot silently reintroduce the 1-day
bug.

### D5 — Load-test tier (revisiting slice 166 D2's deferral)

Slice 166 recorded the deferral of the NFR load-test tier to whichever slice
lands the rewrite — this one. The NFR ("sub-second at full-universe scope") is
a latency assertion on a full-universe read, which the python rules place in
`tests/load/` (latency/throughput/resource bounds, not functional
correctness). **Decision to confirm with PM at task-breakdown:** add one load
test asserting full-universe `data_status` read latency < 1 s against a
realistic-scale fixture (or a gated prod-shaped tier), so the NFR has
regression coverage. Functional equivalence (output identical to raw-scan
modulo the documented lag bound) is covered by integration tests, not the load
tier.

### D6 — Guard placement: a single guarded accessor (PM ruling, 2026-07-26)

*(Resolves design-review F002, which found "the guard lives in `bars_summary`"
under-specified.)* `assert_cagg_fresh` is a Python helper; `bars_summary` is a SQL
CTE inside a view definition, so the guard cannot literally live "in" it. Placing
the call at each read site instead would make D3a aspirational — the next reader
added (notably slice 182's serving API) silently becomes an unguarded consumer,
which is exactly what this slice promises not to ship.

**Decision:** a new module `data/maintenance/status_coverage.py` is the **single
guarded door** to `data_status`. It asserts freshness on both coverage caggs, then
returns rows **plus** the verdicts so callers can surface staleness; it neither
swallows a stale verdict nor raises, per D3a's report-don't-skip behavior. Both
existing readers — `status_queries.py` and `migrate_cold_start.py` — are migrated
onto it **in this slice**, and the constraint is made enforceable rather than
documented: no `FROM data_status` may remain outside that module, proven by grep.
Slice 182 is contractually required to use it (see Cross-slice dependencies).

### D7 — Equivalence is date-normalized, not literal (PM ruling, 2026-07-26)

*(Resolves design-review F003, which found criterion 2's "identical" in direct
contradiction with D3's documented bucket truncation.)* Minute-side bucket
truncation is a **permanent** delta across all history, not a trailing-edge
effect, so a literal row-by-row equality assertion would fail on every symbol —
the criterion as originally written was unsatisfiable.

**Decision:** equivalence means date-normalized timestamps equal, `bars_stored`
**exactly** equal, and raw−cagg timestamp delta < 4 h on the minute branch. The
daily branch reads raw `daily_ohlcv`, so its timestamps must match **exactly** —
asserted separately, or a real daily regression would hide inside the minute-side
tolerance. This tests the actual user-visible contract, since the CLI renders
date-only (`_fmt_date`, `%Y-%m-%d`). Criterion 2 is restated accordingly, and the
equivalence test carries a comment stating why literal equality is *not* used.

## Data flow

```
raw minute_ohlcv ──(4h refresh policy)──▶ minute_4hour_ohlcv
                                                │
                              (coverage refresh policy)
                                                ▼
                                         minute_coverage  (~15k rows)
                                                │
raw daily_ohlcv ──▶ daily_coverage             │
                          │                     │
                          └────────┬────────────┘
                                   ▼
                       data_status.bars_summary (groups ~15k+ rows)
                                   ▼
                        mt data status  (unchanged output shape)
```

## Migration plan

- **Source of truth:** `MINUTE_OHLCV_CHUNK_INTERVAL` pattern from slice 166 —
  any interval/offset constants centralized in `constants.py`, not inlined.
- **New migration (044+):** create `minute_coverage` and `daily_coverage`
  continuous aggregates + their refresh policies (each `CREATE MATERIALIZED
  VIEW` its own `execute()`, `requires_autocommit: True`, following the
  established `034_create_daily_caggs` / policy-add pattern in `minute.py`).
- **View rewrite migration (045+):** `CREATE OR REPLACE VIEW data_status` with
  the cagg-backed `bars_summary`, via the existing
  `_build_data_status_view_sql(...)` builder (add a variant/flag rather than
  duplicating the SQL string), preserving all other CTEs and columns verbatim.
  Re-uses the migration-021 DO-block / `to_regclass` branching convention so
  cold-start and existing DBs converge.
- **Consumer updates:** none — the column contract is preserved (D2).
- **Behavior verification:** before/after full-universe read timing; row-by-row
  equivalence of `data_status` output (raw-scan vs cagg-backed) modulo the
  documented lag bound (D3); cold-start applies cleanly to the new migration
  count.

## Cross-slice dependencies and interfaces

- **Depends on [166]** — the raw-table re-chunk that this builds on.
- **Depends on [163]** — two distinct dependencies, both binding:
  - *Data:* 163 repairs (force-refresh) and re-chunks the minute caggs; 167 cannot
    back `bars_summary` with a corrupted 4h cagg (§Critical prerequisite).
  - *Design:* 163 established that a cagg informing an operational decision is a
    production input requiring an asserted freshness contract, and that
    `start_offset` alone is the wrong threshold. 167 is the second such consumer
    and inherits both constraints (D3a). This is a design dependency, not just a
    sequencing one — a reviewer should confirm D3a is satisfied, not merely that
    163 ran.
  - *Shared artifact:* `assert_cagg_fresh` — delivered by slice 168, a hard
    dependency of this slice. It lives in a shared maintenance module, not
    inlined in the view path, because the minute daemon's coverage index is the
    other caller. 167 consumes it; it does not reimplement it.
- **Interfaces [147]** — `mt data status` reads `data_status`; contract
  preserved.
- **Interfaces [182]** — serving API's available-ranges / status surfaces read
  the same view; contract preserved. **Contractually required (D6):** 182 must
  read through `data/maintenance/status_coverage.py`, not `FROM data_status`
  directly, so the freshness guard is not bypassed. Note also that the API may
  expose timestamps at full precision, where the up-to-4 h minute-side
  coarsening (D3/D7) becomes visible — unlike the CLI's date-only rendering.
  How to present that is 182's decision; 167 only documents it.

## Success criteria

1. Full-universe `data_status` read is **sub-second** on prod `trading` DB.
2. Output is **equivalent** to the raw-scan version under the date-normalized
   definition (review F003, ruled below): for settled history,
   `date(first_bar_ts)` and `date(last_bar_ts)` equal, `bars_stored` **exactly**
   equal, minute-side timestamp delta < 4 h, daily-side timestamps exact; the
   trailing edge differs only within the documented cagg-lag bound.
3. `mt data status` output shape (columns, formatting) is **unchanged**.
4. The view carries a doc comment stating the bucket-truncation and cagg-lag
   bounds and the chosen refresh intervals (D3).
5. Cold-start applies the new migrations cleanly and yields a sub-second view
   on a freshly-built DB.
6. A load test asserts full-universe read latency < 1 s, gated on
   `MT_RUN_LOAD_TESTS=1` per the existing `test/load/` convention, and is
   runnable via the documented invocation
   (`MT_RUN_LOAD_TESTS=1 uv run pytest test/load/`). **This repo has no CI**
   (review F002 — `.github/workflows` does not exist); standing one up is out of
   scope for 167 and filed as **slice 907** (CI Pipeline and Load-Test Gating),
   which will also retire slice 146's stale "CI must enable" docstrings. This
   criterion is met by the documented manual invocation, stated honestly rather
   than claimed as automated (PM, 2026-07-26).
7. **`assert_cagg_fresh` is called on the coverage caggs via the guarded
   accessor, and is proven to fire** (D3a). Verified by *inducing* staleness,
   not by reading code: pause a coverage cagg's refresh policy on a throwaway
   DB, advance raw past the threshold, and confirm the read reports stale
   coverage rather than presenting it as current. Each of the four signals is
   covered independently — including the case a naive implementation misses: a
   coverage cagg whose policy carries a deliberately loose `start_offset` (per
   D4) but which is stalled far beyond what this consumer tolerates. A test that
   only asserts the helper is *called* does not satisfy this criterion.
8. Every Python reader of `data_status` goes through `status_coverage`; no
   second unguarded consumer ships (review F002).

## Verification walkthrough (draft — refined at Phase 6)

1. **Prove the prerequisite is met** (163 ran): confirm the 4h cagg is fully
   materialized — `SELECT SUM(minute_count) FROM minute_4hour_ohlcv` matches
   the raw count within the lag bound (not ~21%).
2. **Timing before/after:** `\timing on`; `SELECT count(*) FROM data_status;`
   — record sub-second vs the 7.8 s baseline.
3. **Equivalence:** diff a snapshot of `data_status` (all columns) taken
   against the raw-scan view vs the cagg-backed view for a sample of covered,
   partially-covered, and empty symbols; assert equality modulo trailing-edge
   lag.
4. **Contract unchanged:** `mt data status` and `mt data status --symbol AAPL`
   render identical column layout to pre-slice output.
5. **Cold-start:** throwaway DB → run all migrations → `data_status` returns
   rows and is sub-second.
6. **Lag bound honesty:** fetch new bars for a symbol, immediately read
   `data_status`, confirm coverage understates by at most the documented bound
   and converges after the next refresh tick.
