---
docType: slice-design
slice: coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized
project: trading-data
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [167, 168, 170, 187]
interfaces: [187]
dateCreated: 20260813
dateUpdated: 20260813
status: not_started
---

# Slice Design: Coverage-Cagg Refresh Repair — the Current Bucket Is Never Re-materialized

## Overview

`minute_coverage` and `daily_coverage` (slice 167, migration 046) bucket at
`COVERAGE_BUCKET_INTERVAL` = 365 days. A TimescaleDB refresh policy's window is
`[now - start_offset, now - end_offset]` **truncated to whole buckets**, and a
refresh only re-materializes buckets *fully contained* in that window. With a
365-day bucket and an `end_offset` of 1–4 hours, `now - end_offset` always falls
*inside* the current bucket, so truncation drops it.

**The current bucket is materialized once, at cagg creation, and then never
again until the year rolls over.** Both hourly policies have been successful
no-ops since creation.

This is not a "policy is behind" problem that a wider `start_offset` fixes —
`start_offset` is already 750 days. It is structural: the head bucket is
unreachable by the policy mechanism for as long as it stays open.

Measured on prod 2026-08-04 (slice 187 D3), and again in the summary of the
slice 170 session (2026-08-11), where both caggs had drifted further:

| Signal | 2026-08-04 | 2026-08-11 |
|---|---|---|
| `daily_coverage` `MAX(last_bucket)` | 2026-06-12 | 2025-12-26 |
| `minute_coverage` `MAX(last_bucket)` | 2026-07-24 12:00 | 2025-12-26 |
| Raw `daily_ohlcv` edge | 2026-08-03 | 2026-08-05 |
| Raw `minute_ohlcv` edge | 2026-07-28 13:30 | 2026-08-07 |
| Invisible daily rows | 390,884 | not re-counted |
| Jobs 1107 / 1108 | `scheduled`, hourly, 205 × `Success` | unchanged |

The 2026-08-11 reading is the important one and it is *worse* than a simple
drift: both watermarks had fallen back to **2025-12-26**, and a forced full-span
`refresh_continuous_aggregate` on `daily_coverage` **did not move the newest
bucket**. That is the signature of a bucket the engine will not write while it
is open, not of a lookback shortfall.

Observable consequence today: `data_status` reports `SPY / daily / OK /
last_bar_ts = 2025-12-26`, and `mt data status` presents it as current coverage.
Slice 187 D6 made this visible rather than silent (both views now report STALE
via the content-edge check) — **this slice is what clears it.**

### Why slice 187 is both a dependency and an interface

187 appears in **both** frontmatter lists, deliberately. It is a *dependency*
because this slice consumes and then **modifies** what 187 built: D3 relies on
187 D6's content-edge check already existing in `status_coverage.py` as its
truth-telling mechanism, and it edits `COVERAGE_CONTENT_STALENESS`, the
constant 187 introduced. That is consume-and-modify, not adjacency. It remains
an *interface* because 187's D3 residual window is what this slice closes and
its walkthrough step 4 is a success criterion here. 187 is shipped, so the
dependency costs nothing operationally — it is recorded so the ordering is
legible from the frontmatter alone.

### Why this is filed after 170

Slice 170's plan entry carries the ordering note: rematerializing the coverage
caggs *before* the `daily_ohlcv` rechunk pays the full cost twice, since the
rechunk invalidates every cagg sourced from that table. 170 shipped 2026-08-11
and its exit force-refresh already rewrote `daily_coverage` (+148 k rows). This
slice runs against that repaired substrate.

---

## Decisions

### D1 — Repair mechanism: narrow `COVERAGE_BUCKET_INTERVAL` (not a custom head-refresh job)

**Decision:** reduce `COVERAGE_BUCKET_INTERVAL` so the refresh policy's window
can contain whole buckets near the head, and rematerialize both caggs.

The alternative considered and **rejected** was an explicit scheduled refresh
covering the head bucket — machinery outside `add_continuous_aggregate_policy`,
which cannot express "include the open bucket." Rejected on four grounds:

1. **It leaves a lying job in the catalog.** The 365-day bucket, and therefore
   the vacuous policy, would remain. The system would carry *two* refresh paths
   per cagg — a stock policy handling closed history and a custom job handling
   the head — with the policy still reporting `Success` for work it does not do.
   An operator reading `timescaledb_information.jobs` would draw exactly the
   wrong conclusion. That misreading is what hid this bug for months.
2. **The job body has nowhere good to live.** Either a PL/pgSQL procedure
   registered via `add_job()` — putting executable logic in the schema, outside
   Python's type-checking and the normal test tiers, with no precedent in this
   repo (every job today is a stock policy) — or a cron entry on `.144`, which
   slice 915 measured as having **no process manager or systemd units
   installed**, and which would be invisible to `timescaledb_information.jobs`
   and therefore to `mt data caggs status`.
3. **It needs a bespoke health signal or it fails the same silent way.** The
   lesson of this defect is that a green `last_run_status` proved nothing.
4. **It is permanent.** Every future change to the coverage caggs would have to
   reason about two refresh paths.

Narrowing uses the engine's own mechanism. It ends with **one** refresh path and
no catalog lie. Its cost is a one-time rematerialization plus a permanent
increase in cagg row count — the row-count trade slice 167 D1 made deliberately,
now partially traded back with eyes open (see D2).

**What narrowing does and does not fix.** It does *not* make the engine refresh
an open bucket — nothing does. It bounds how much data that limitation can hide.
The head bucket remains unmaterialized while open; narrowing shrinks that window
from up to a year to one bucket width. This distinction drives D3.

### D2 — Bucket width: derived from the visibility bound, not chosen by taste

The width is not a free parameter to be picked for a pleasing row count. It is
determined by the **worst-case invisible window** it permits, because the open
bucket is never materialized:

```
worst-case coverage lag  =  COVERAGE_BUCKET_INTERVAL  +  end_offset
```

A symbol whose bars all land early in a bucket stays invisible until that bucket
closes. This is the quantity an operator actually cares about, and it is the
quantity `COVERAGE_CONTENT_STALENESS` (currently 1 day 4 h) is checked against.

Two constraints bound the choice from the other side:

1. **Engine floor.** `start_offset - end_offset >= COVERAGE_REFRESH_MIN_WINDOW_BUCKETS
   × bucket` (currently 2 × 365 d = 730 d, with `start_offset` 750 d). Narrowing
   *relaxes* this — it is the one constraint that gets easier. The constants test
   `test_coverage_refresh_window_meets_engine_minimum` asserts it and will keep
   asserting it at the new width.
2. **Grouping cost.** `bars_summary` groups the cagg per symbol; slice 167 D1
   sized the year bucket so that grouping stayed sub-millisecond (~15 k minute
   rows). Row count scales linearly with 1/width.

Row-count arithmetic at candidate widths, using slice 170's **measured** spans
(daily 1962→2026, 64.6 years, 12,040 symbols; minute 2004→2026, ~22 years,
5,871 symbols). These are worst cases — they assume every symbol spans the full
history, which is false for nearly all of them:

| Width | Minute rows (≤) | Daily rows (≤) | Worst-case invisible window |
|---|---|---|---|
| 365 d (today) | ~15 k | ~780 k | **up to 1 year** |
| 90 d | ~530 k | ~3.1 M | ~90 d + 4 h |
| 30 d | ~1.6 M | ~9.5 M | ~30 d + 4 h |
| 7 d | ~6.7 M | ~40 M | ~7 d + 4 h |

**None of these widths brings the worst case under `COVERAGE_CONTENT_STALENESS`
(1 d 4 h).** That is the crux of this slice and the reason D3 exists: narrowing
alone cannot satisfy the freshness threshold the system already checks. Reaching
a 1-day bound would require a ~1-day bucket, which puts the daily cagg near
280 M rows and destroys the whole point of slice 167.

**Decision: adopt a width in the 7–30 day range, chosen by measurement (Task
B1), with 30 days as the design's working assumption**, and pair it with D3.
30 days keeps the daily cagg around 9.5 M worst-case rows — two orders of
magnitude below the 4.4 B raw scan slice 167 escaped, and within reach of a
sub-second `GROUP BY symbol` — while cutting the invisible window from a year to
a month. The final number is fixed after Task B1 measures `bars_summary` at
candidate widths on a representative database. The design deliberately does not
hardcode it here; it is rendered from the constant everywhere (D5).

**Do not choose a width that is not a divisor-friendly fit with the parent.**
`minute_coverage` is hierarchical over `minute_4hour_ohlcv` (4 h buckets), so
any width in whole days nests cleanly. `daily_coverage` reads raw. No nesting
hazard of the slice 166/170 kind arises here, because these are cagg bucket
widths, not chunk intervals — but the *materialization* hypertables underneath
them do have chunk intervals, which is D6.

### D3 — Close the residual head window with `end_offset`, not with a custom job

Narrowing bounds the invisible window but leaves the open bucket unwritten. The
remaining question is whether `data_status` can be *correct* about it.

It already is, and that is slice 187's doing. D6 of that slice added the
**content-edge check** — `max(last_bucket)` on the cagg against `max(time)` on
the raw source, with no bucket alignment — precisely because the generic
bucket-lag guard has a one-bucket detection floor and was returning
`is_fresh=True, lag=0` over a 52-day staleness. That check is in
`status_coverage.py` today and is what currently reports prod stale.

**Decision:** this slice does **not** add head-refresh machinery. It:

1. Narrows the bucket (D2), shrinking the invisible window by ~12–52×.
2. Re-derives `COVERAGE_CONTENT_STALENESS` from the new width so the threshold
   describes the system's actual guarantee instead of an unreachable one.
3. Re-derives the **generic bucket-lag budget** for these two views as well
   (D3a) — without which narrowing makes that check fire permanently.
4. Leaves the content-edge check as the mechanism that tells the truth when the
   head bucket is behind.

Point 2 is the honest part and needs stating plainly: **the current 1 d 4 h
threshold is not achievable by any bucket width compatible with slice 167's
purpose.** Its derivation (`MAX_COVERAGE_SOURCE_STALENESS` + max `end_offset`)
was written for the bucket-lag check and does not account for the open-bucket
gap. After this slice the threshold becomes:

```
COVERAGE_CONTENT_STALENESS  =  COVERAGE_BUCKET_INTERVAL
                            +  max(end_offset over both coverage policies)
```

At a 30-day width that is 30 d 4 h. This is a **widening** of a staleness
threshold, which normally deserves suspicion — so the justification must be
explicit: the threshold is being corrected to describe a bound the architecture
actually provides, rather than left at a value the architecture can never meet
and which therefore fires permanently. A permanently-firing staleness signal is
indistinguishable from a broken one, and an operator learns to ignore it.

**Consequence to accept and document:** `mt data status` can show a
`last_bar_ts` up to one bucket width behind reality for a symbol, without the
staleness banner firing. For the API this is already handled — slice 187 D2's
per-symbol head probe (`_SYMBOL_HEAD_SQL`) reads `minute_5min_ohlcv` and raw
`daily_ohlcv` *past the coverage horizon*, so `/symbols` ranges stay exact
regardless of coverage lag. The residual window slice 187 D3 recorded closes to
one bucket width rather than to zero.

### D3a — The generic bucket-lag guard needs a bucket-width term, or it fires forever

Slice 168's `assert_cagg_fresh` computes

```
lag = time_bucket(width, max(time) on raw)  -  max(time_bucket) on cagg
```

against a budget of `min(start_offset, MAX_COVERAGE_SOURCE_STALENESS) + end_offset`
= **1 day 4 h** (`cagg_freshness.py:392, :481`). The architecture justifies
omitting bucket width from that budget: *"The raw edge is bucketed to the cagg's
own grid before comparison, so the structural offset cancels exactly."*

**That cancellation depends on the head bucket being materialized**, and D1's
central premise is that it never is. Once the cagg's newest materialized bucket
is the most recently *closed* one while the bucketed raw edge is the *open* one,
the generic lag pins at **exactly one bucket width, permanently**.

Today this does not fire only by accident: the 365-day head bucket was written
once at cagg creation, so `max(time_bucket)` still equals the bucketed raw edge
and the check reports `lag=0`. That is the false negative slice 187 D6 was built
to work around. **Narrowing the bucket removes the accident**, and without a
matching budget change `LAG_EXCEEDS_THRESHOLD` would fire on every read of both
views forever — converting a silent false negative into a permanently-firing
true positive, which is the outcome D3 argues against two paragraphs earlier.

**Decision:** the bucket-lag budget for the **coverage caggs specifically**
gains a bucket-width term, parallel to the content-edge threshold:

```
coverage bucket-lag budget  =  COVERAGE_BUCKET_INTERVAL
                            +  min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)
                            +  end_offset
```

Applied **per view, not globally**: the seven pre-167 caggs keep the existing
budget unchanged. Their buckets are small relative to their offsets, so the open
bucket is always inside the refresh window and the cancellation genuinely holds
for them — this is the same point `COVERAGE_REFRESH_MIN_WINDOW_BUCKETS`'
docstring makes ("a 1-year bucket is the first one large relative to any sane
refresh window"). Widening the budget globally would blunt the guard on seven
healthy caggs to accommodate two exceptional ones.

**Mechanism (decided here, not at task level):** a per-view budget override
resolved alongside `COVERAGE_SOURCE_TABLE` in `constants.py`. That map already
exists, already keys on view name, and already holds per-view coverage
metadata — adding the budget beside it keeps one lookup and introduces no new
concept. `assert_cagg_fresh` consults it and falls back to the existing
`min(start_offset, MAX_COVERAGE_SOURCE_STALENESS) + end_offset` for any view
without an entry, so the seven pre-167 caggs are untouched by construction.

Rejected: an "open-bucket-tolerant" boolean flag on the freshness spec. It
encodes *why* rather than *what*, so the width term would still have to be
derived somewhere else, and a second view needing a different budget for a
different reason would need a second flag.

**What must not happen** is
suppressing the bucket-lag signal for these views — that would remove a real
guard (a genuinely stalled or unscheduled policy) to silence a structural
offset, and the content-edge check does not subsume it: they detect different
failures (168 D1's `NOT_SCHEDULED` / `LAST_RUN_FAILED` signals ride the same
path).

**Consequence for the success criteria:** criteria 6 and 8 are achievable only
with this change. Without it they are unachievable in steady state, since both
views would report stale on every read the moment the rebuild completes.

If the PM judges a one-bucket display lag on `mt data status` unacceptable, the
follow-on is to extend the 187 D2 head-probe pattern to the `data_status` view's
`bars_summary` — a coverage floor plus a bounded head read. That is a larger
change to a view with a strict column contract (167 D2) and is **out of scope
here**; noted in Open Questions.

### D4 — Rematerialization: drop and rebuild, do not refresh in place

Changing a cagg's `time_bucket` width is **not** an `ALTER`. TimescaleDB has no
operation to re-bucket an existing continuous aggregate; the view definition
itself changes, so both caggs must be **dropped and recreated** at the new
width, then materialized over full history.

This is a new migration pair (051/052 — the chain currently ends at 050), not an
edit to 046/047. Editing an applied migration would leave already-migrated
databases at the old width with no record of the change.

**The slice 163 corruption lesson applies directly.** From the journal and
`project_merge_chunks_adjacency_lesson`: a cagg refresh running concurrently
with restructuring silently loses rows. Therefore:

- Both coverage caggs' refresh **and** columnstore policy jobs are paused before
  any DDL, resolved from the catalog by name (`_resolve_cagg_jobs` in
  `cagg_repair.py` already does exactly this) — **never** by hardcoded job ID.
  The 170 execution proved why: the runbook's job table was stale, job 1003 no
  longer exists, and the 4 h minute refresh is now job 1124.
- `minute_coverage`'s parent `minute_4hour_ohlcv` must stay **scheduled**, not
  paused. `_check_coverage_index_available` enforces this and will refuse
  otherwise — pausing it makes the daemon re-seed and re-pull recent sessions
  every cycle (prod incident 2026-07-25, and
  `project_cagg_pause_reseed_loop`).
- Every paused job is resumed afterward, verified by re-reading the catalog
  (runbook R4).

**Materialization must cover full history explicitly.** The scheduled policy
looks back only `start_offset` (750 d) and cannot heal 64 years — the 163 lesson
restated, and the same trap slice 170 found when its exit force-refresh revealed
the daily rollup caggs were roughly half-materialized. The rebuild issues an
explicit `refresh_continuous_aggregate(view, <full-span start>, <now>)`.

### D5 — Everything renders from the constant; no width literal anywhere

`COVERAGE_BUCKET_INTERVAL` is already the single source of truth, rendered into
migrations via `_interval_seconds_sql`. The new migrations do the same. No
literal width appears in SQL, tests, or docs.

Five derived values move with it and must be re-derived rather than restated:

- `COVERAGE_CONTENT_STALENESS` — per D3, now a function of the width.
- The coverage bucket-lag budget — per D3a.
- The engine-minimum assertion in `test_constants.py` — already written against
  `COVERAGE_REFRESH_MIN_WINDOW_BUCKETS * COVERAGE_BUCKET_INTERVAL`, so it
  follows automatically. This is the constants test doing its job: it was
  written specifically so a width change fails at test time, not migration time.
- Migration 046's description text, which names "1 year" **and "~15k rows"** in
  prose. Both numbers change; 046 is already applied everywhere, so an existing
  database keeps the obsolete text unless 051's description supersedes it.
- **`_data_status_doc_comment()`** (`minute.py:355-397`), which migration 048
  attaches via `COMMENT ON VIEW data_status` and which 140-arch designates as
  the in-database statement of these bounds.

**The doc comment is already wrong today, before this slice touches it.** It
renders CAGG LAG from the *schedule intervals* only —
`MINUTE_CAGG_REFRESH_SCHEDULE_INTERVAL + MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL`,
i.e. **2 hours total** — a derivation that never accounted for the open bucket.
The true bound on prod right now is the 365-day open-bucket lag. An operator
running `\d+ data_status` reads a promise of hours against a reality of months.

So this is not merely "re-render with new constants": the **formula** is wrong
and must gain the bucket-width term, matching D3 and D3a:

```
coverage lag bound  =  COVERAGE_BUCKET_INTERVAL
                    +  refresh schedule interval(s)
```

Re-attaching the corrected comment belongs to 052 (the migration that owns the
policies the comment describes). This is the artifact the architecture points
operators at, so it is precisely the one that must not lie — the same
operator-visibility principle D1 used to reject a cron job invisible to
`timescaledb_information.jobs`.

Test fixtures that construct spans as multiples of `COVERAGE_BUCKET_INTERVAL`
(`test_coverage_content_edge.py`, `test_migrations_046_047.py`,
`test_symbol_ranges_sql.py`, `test_data_status_equivalence.py`) already scale
with the constant and should need no arithmetic changes — **verify, do not
assume**; a fixture that hardcoded a matching literal elsewhere will surface as
a failure.

### D6 — Materialization-hypertable chunk interval

A cagg's materialization hypertable has its own `chunk_time_interval`, which
TimescaleDB defaults to **10× the bucket width**. At 365 days that default is
absurd but harmless (the cagg is tiny). At 30 days it becomes 300 days, which is
fine — but the row count is now ~12× larger, so this deserves a check rather
than a shrug.

**Decision:** let the default stand, and **verify** the resulting chunk count in
the walkthrough. The 166/163 disease is *over*-chunking; a 300-day interval over
a 64-year span yields ~79 chunks for `daily_coverage` — comfortably in the
healthy band the wall-clock rule targets (span ÷ 1,000–2,000 is the *upper*
bound guidance; low hundreds is the proven-good range from 166/170). No explicit
`set_chunk_time_interval` unless Task B1's measurement contradicts this.

### D6a — Amend the parent architecture

140-arch is normative about both things this slice changes, so shipping without
amending it leaves the architecture stating values the system no longer has:

- **`140-arch#Constants` (line 1109)** specifies `COVERAGE_BUCKET_INTERVAL =
  365 days` with the load-bearing rationale *"groups ~15k rows rather than
  scanning 4.4 billion raw rows"*. Both the constant and the row count change.
- **The slice-167 amendment (line 100)** states that `data_status` *"is
  consistent with the underlying data only within a documented and asserted
  staleness bound."* This slice widens that asserted bound from 1 d 4 h to
  ~30 d 4 h — a change to what the architecture **promises the operator**,
  against its Purpose question 1 ("For symbol X, what data do we have, at what
  granularity, when?").

**Decision:** amending 140-arch is a **deliverable of this slice**, not a
follow-up, using the established convention (`*(Architecture amendment,
{date} — slice 169.)*`, as at lines 100 and 667).

**Done 2026-08-13**, three amendments: `COVERAGE_BUCKET_INTERVAL`'s constants
block, the slice-167 bounded-consistency paragraph, and the refresh-policy
block — the last because it carried the same wrong two-hop formula as the doc
comment. The width is written as 30 days, the design's working assumption;
**Task B1 must update it if measurement selects a different width**, along with
the derived thresholds.

The reasoning is D1's own: a stale architecture is the same failure as a lying
job catalog. A later slice designing against coverage would read 365 days and
a sub-1-day bound, and size its work wrong. Tie this to the D3/D3a threshold
values, so the amendment records the final measured width rather than the
design's working assumption.

### D7 — Scope exclusions

Out of scope, explicitly:

- **Head-refresh machinery of any kind** (D1, D3).
- **Changing `bars_summary` to a floor-plus-head-probe shape** (D3, Open
  Questions).
- **`mt data caggs repair` extension to the coverage caggs.** The rebuild here
  is a one-time migration-driven operation, not a recurring sweep.
- **The minute rollup caggs' ~0.018 % parity shortfall.** Known, unrelated
  (identical 548,889,887 volume units over 2024, a late-arrival source pattern),
  and healed by `mt data caggs repair`.
- **`approximate_row_count` accuracy.** Known TimescaleDB estimator defect
  (+2,099 % on `daily_ohlcv`, +68 % on `minute_ohlcv`). Never used for
  verification here — exact counts only.

---

## Data Flow

Unchanged in shape; only the bucket width and the refresh reachability change.

```
raw minute_ohlcv ──► minute_4hour_ohlcv ──► minute_coverage ──┐
   (raw hypertable)     (4 h cagg,            (W-day cagg)     │
                         stays scheduled)                      ├──► bars_summary
                                                               │      (data_status)
raw daily_ohlcv ─────────────────────────► daily_coverage ────┘      └──► mt data status
   (70-day chunks, slice 170)                (W-day cagg)             └──► /api/v1/status
                                                                      └──► /api/v1/health

                       ┌── check_coverage_freshness (187 D6 content-edge check)
                       │      max(last_bucket) vs max(time) on raw source
                       └──► STALE verdict when lag > COVERAGE_CONTENT_STALENESS
```

`/api/v1/symbols` ranges do **not** flow through coverage alone: slice 187 D2
combines a coverage floor with a bounded head probe against `minute_5min_ohlcv`
and raw `daily_ohlcv`, so they stay exact across this change (D3).

---

## Migration Plan

| Step | Object | Action |
|---|---|---|
| 051 | `COVERAGE_BUCKET_INTERVAL` | New width, rendered into DDL |
| 051 ① | `data_status` | `DROP VIEW data_status` **first** — it depends on both caggs (see below) |
| 051 ② | `minute_coverage`, `daily_coverage` | `DROP MATERIALIZED VIEW` (no `CASCADE`) + recreate at new width; one `CREATE` per `execute()` (046's constraint), `requires_autocommit` |
| 051 ③ | `data_status` | Re-install from 048's branch-on-`to_regclass` definition and re-attach its doc comment |
| 052 | refresh policies | Reinstall for both views at unchanged offsets; idempotent `DO` block, matching 047 |
| 052 | `COMMENT ON VIEW data_status` | Re-render from the new constants (D5) |
| — | full-history materialization | Explicit `refresh_continuous_aggregate` over the full span; **operational step, not a migration** (see below) |

**Ordering ①②③ is mandatory, and `CASCADE` is forbidden.** Migration 048
installs `data_status` as a plain `CREATE OR REPLACE VIEW` whose `bars_summary`
CTE selects from `minute_coverage` and `daily_coverage`
(`minute.py:297`, `:401-407`), so PostgreSQL records a hard relation dependency.
Column compatibility — which the original draft of this plan reasoned about —
is irrelevant to a `DROP`.

Two failure modes if this is not explicit:

- **Without the pre-drop:** `DROP MATERIALIZED VIEW minute_coverage` raises
  `cannot drop ... because other objects depend on it`, aborting partway
  through a two-view drop/recreate that runs under `requires_autocommit` — so
  there is **no transactional rollback**, and the database is left with one
  cagg dropped and one intact.
- **With `CASCADE`** (the reflex, and the spelling used elsewhere in this
  module): `data_status` is silently dropped, and `mt data status`,
  `/api/v1/status`, and `/api/v1/health` all fail against a missing relation
  until someone notices.

Because 051 is not transactional, it must be **idempotent on re-run** from any
point in ①②③ — `DROP ... IF EXISTS`, `CREATE ... IF NOT EXISTS`, matching the
convention 046 already uses.

**Why the full-history refresh is not in the migration.** It is a long,
resource-heavy write against 64 years of daily and 22 years of minute data. Two
reasons to keep it out of the migration chain: a cold-start database has no
history to materialize and should not pay for the machinery, and on prod the
operator needs it under the pausing runbook with the ability to stop and resume.
It belongs in the execution phase (Task C), driven by an explicit command.

**Consumers requiring no change:** `api_server/queries.py`,
`status_coverage.py` structure. The caggs are recreated with identical column
names and types, so every *query* against them binds unchanged — the payoff of
167 D2's column contract. The width is invisible above the cagg boundary for
readers; it is **not** invisible to `DROP`, which is F002's point above.

**Consumers requiring change:**

- **`data_status`** — dropped and re-installed by 051 as an ordered step
  (above). Not a rewrite: 048's existing definition is re-executed unchanged.
- **`cagg_freshness.py`** — the per-view bucket-lag budget (D3a).
- **`restore_metadata.py`** — lists both coverage views among recreatable
  objects (lines 190–191) and references 046 as their creating migration. That
  reference must move to 051, or the restore tool recreates them at the old
  width. Note this file is also the incident-recovery path, so a stale
  reference here fails exactly when it is least affordable.
- **`_data_status_doc_comment()`** — see D5.

---

## Success Criteria

1. `COVERAGE_BUCKET_INTERVAL` is the new measured width; **no width literal**
   appears in any SQL, test, or migration description.
2. `COVERAGE_CONTENT_STALENESS` is derived from `COVERAGE_BUCKET_INTERVAL` plus
   the larger coverage `end_offset`, with the derivation in its docstring.
3. Migrations 051/052 apply cleanly on a **cold-start** database, and the
   migration chain ends at 052 with the count tripwire updated.
4. `test_coverage_refresh_window_meets_engine_minimum` passes at the new width
   without editing the assertion — it is already written against the constants.
5. On a database with history, both coverage caggs materialize over their full
   span, and `MAX(last_bucket)` on each is within one bucket width plus
   `end_offset` of `MAX(time)` on its raw source.
6. `check_coverage_freshness` reports **fresh** for both views against prod.
7. `data_status` returns the same columns, in the same order, with the same
   types as before (167 D2 contract); a spot-check of `SPY / daily` shows
   `last_bar_ts` tracking raw within the bound from criterion 5, not
   2025-12-26.
8. `mt data status`, `/api/v1/health`, and `/api/v1/status` no longer report
   coverage staleness.
9. Slice 187's walkthrough step 4 re-runs with no discrepancy.
10. Exact row counts on both raw sources are **unchanged** across the operation
    (`count(*)`, not `approximate_row_count`).
11. Every job paused during the rebuild is `scheduled = true` afterward, and
    `minute_4hour_ohlcv`'s refresh was never paused (R1/R4).
12. **The full-universe `data_status` read** meets the sub-second NFR that slice
    167 exists to hold — measured as `SELECT count(*) FROM data_status` (the
    shape 167 took from 7.8 s to sub-second, and the form 140-arch states the
    NFR in), with the number recorded. The isolated `bars_summary` `GROUP BY`
    is a **diagnostic, not the gate**: at 30 days `daily_coverage` grows ~12×,
    and the full view additionally joins that CTE against `symbols`,
    `acquisition_state`, and the exchange-close CTE over a larger intermediate.
    A fast CTE with a regressed view read would pass a CTE-only criterion while
    breaking the actual architectural NFR.
13. `data_status` exists and returns rows after 051 — verified directly, not
    inferred from column compatibility (F002).
14. `COMMENT ON VIEW data_status`, read back via `obj_description`, states a
    lag bound including the bucket-width term and matching the constants —
    i.e. no longer the current "2 hours total" (D5).
15. 140-arch is amended for the new width, the new row-count rationale, and the
    widened staleness bound, using the established amendment convention (D6a).
16. Both coverage views report **fresh** on the *generic* bucket-lag check as
    well as the content-edge check, with the seven pre-167 caggs' budgets
    unchanged (D3a).

---

## Verification Walkthrough (draft)

To be refined with measured values at Phase 6 completion. **Every prod step
runs only when prod is clear of other work**, with `statement_timeout` set on
each session, and any client-side timeout followed by
`pg_cancel_backend` on the server side (`feedback_prod_query_discipline`).

### 1. Measure the width tradeoff before fixing it (Task B1)

On an ephemeral or test database seeded to a representative shape, materialize
both caggs at candidate widths (7 / 30 / 90 days) and time the grouping:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT symbol, MIN(first_bucket), MAX(last_bucket), SUM(bars)::BIGINT
FROM daily_coverage GROUP BY symbol;
```

Then time the **full-universe view read**, which is the actual NFR (criterion
12) — the CTE timing above is only a diagnostic for *where* any cost lands:

```sql
EXPLAIN (ANALYZE, BUFFERS) SELECT count(*) FROM data_status;
```

Record rows materialized, CTE time, and full-view time per width. Choose the
smallest width whose **full `data_status` read** holds the sub-second NFR with
margin.

### 2. Confirm the defect is still live before repairing

```sql
SET statement_timeout = '30s';
SELECT 'daily'  AS fam, MAX(last_bucket) FROM daily_coverage
UNION ALL
SELECT 'minute',        MAX(last_bucket) FROM minute_coverage;
```

Expect both near 2025-12-26 (the 2026-08-11 reading). Compare against raw:

```sql
SELECT MAX(time) FROM daily_ohlcv;    -- expect ~2026-08-05 or later
SELECT MAX(time) FROM minute_ohlcv;   -- expect ~2026-08-07 or later
```

### 3. Pause the right jobs, resolved from the catalog

```sql
SELECT job_id, proc_name, hypertable_name, scheduled
FROM timescaledb_information.jobs
WHERE hypertable_name IN ('minute_coverage', 'daily_coverage', 'minute_4hour_ohlcv');
```

Pause the coverage views' refresh and columnstore jobs by the IDs **this query
returns**. Confirm `minute_4hour_ohlcv`'s refresh remains `scheduled = true`.

### 4. Apply the migrations

```bash
mt data migrate --status      # chain ends at 050 before
mt data migrate
mt data migrate --status      # chain ends at 052 after
```

### 5. Materialize full history

Explicit full-span refresh per view, outside a transaction. Expect a long run on
the daily side (64.6-year span). Record wall-clock and rows written.

### 6. Verify the leading edge tracks raw

Re-run step 2's queries. Each cagg's `MAX(last_bucket)` must now be within one
bucket width plus `end_offset` of its raw source's `MAX(time)`.

### 7. Verify freshness clears end to end — **both** checks

```bash
mt data status --json    # coverage staleness absent; SPY/daily last_bar_ts current
curl -s localhost:8000/api/v1/health | jq .
curl -s localhost:8000/api/v1/status | jq .
```

The generic bucket-lag check and the content-edge check are distinct signals
(D3a). Confirm **neither** fires — a verdict carrying `LAG_EXCEEDS_THRESHOLD`
means the D3a budget change is missing or wrong, even if the content-edge
check passes. Confirm too that the seven pre-167 caggs still report against
their unchanged budgets.

### 7a. Verify the in-database doc comment no longer lies

```sql
SELECT obj_description('data_status'::regclass, 'pg_class');
```

The CAGG LAG clause must include the bucket-width term and match the constants.
Before this slice it reads "2 hours total" against a real bound of months
(D5) — that specific string must be gone.

### 8. Resume every paused job and confirm

Re-run step 3's catalog query; every row must read `scheduled = true`.

### 9. Confirm no raw data moved

```sql
SELECT count(*) FROM daily_ohlcv;   -- expect exactly 65,652,505 (slice 170 measured)
SELECT count(*) FROM minute_ohlcv;  -- expect exactly 4,414,650,928 (slice 163 measured)
```

Exact counts only — `approximate_row_count` is off by +2,099 % on `daily_ohlcv`
and is not a verification tool (D7).

---

## The Rebuild Window

Splitting the drop/recreate (051) from the full-history materialization (Task C)
opens an interval in which both caggs exist but are **empty or partial**. That
interval is the riskiest part of this slice and needs stated handling rather
than the word "resumable."

**What readers report while the caggs are empty.** `assert_cagg_fresh`'s
`cagg_max is None` path yields maximal lag → stale, so every symbol reads as
no-coverage: `data_status` returns rows with `bars_stored = 0` and null
timestamps, and `mt data status`, `/api/v1/status`, and `/api/v1/health` all
report coverage stale. **This is correct reporting of a true state** (167 D3a:
report, don't refuse) and nothing crashes — but it is operator-visible and
lasts for the whole materialization. Announce the window; do not run it
alongside anything that reads coverage for decisions.

**The daemon stays stopped for the entire window**, not merely during DDL. Not
for the caggs' sake — the daemon's coverage index reads `minute_4hour_ohlcv`,
which is untouched here — but because a running daemon writes to
`minute_ohlcv`/`daily_ohlcv` while a full-span refresh is reading them, which
moves the target mid-rebuild and leaves the trailing edge indeterminate.

**Interruption handling.** `refresh_continuous_aggregate` over a full span is
**not** chunk-committed the way the minute-fetch pattern is
(`feedback_minute_txn_pattern`); it is one long operation. A statement timeout,
client disconnect, or Ctrl-C leaves partial materialization. Three consequences
to plan for:

- Set **no** statement timeout on the refresh session specifically (unlike
  every read session, which must have one). A timeout here converts a slow
  success into a partial failure.
- After any client-side interruption, `pg_cancel_backend` the server side
  before doing anything else — client disconnect does not cancel the backend
  (`feedback_prod_query_discipline`), and a still-running refresh racing a
  retry is the 163 collision shape.
- **Detect partial materialization by content, not catalog presence** (the
  sql.md rule, and the 2026-08-04 incident's lesson): compare per-symbol
  coverage against raw for a sample, and check that `MIN(first_bucket)` reaches
  the known history floor. A cagg that is present and non-empty may still be
  half-materialized — exactly what slice 170's exit refresh discovered about
  the daily rollups.

**Recovery is re-run, not rollback.** A full-span
`refresh_continuous_aggregate` is idempotent: re-running it over the same span
rewrites the same buckets. So the recovery path for any interruption is to
re-issue it, optionally narrowed to the unmaterialized span once detection has
identified it.

**If the width proves wrong after 051 applies**, the path back is another
migration pair at a corrected width, not a revert — the same drop/recreate
cost paid again. This is the argument for spending the effort on Task B1's
measurement *before* 051 rather than discovering it on prod.

## Risks

| Risk | Mitigation |
|---|---|
| Cagg refresh racing the rebuild silently loses rows (163 lesson) | Pause both views' refresh + columnstore jobs from the catalog before any DDL; resume and verify after (D4) |
| Pausing `minute_4hour_ohlcv` triggers the daemon re-seed loop | Never paused; `_check_coverage_index_available` refuses it (D4) |
| Full-history materialization is heavy on the daily side | See **The Rebuild Window** above — no statement timeout on the refresh session, `pg_cancel_backend` after any client interruption, content-based partial-materialization detection, re-run (not rollback) as recovery |
| `DROP MATERIALIZED VIEW` fails or `CASCADE` silently removes `data_status` | 051 drops and re-installs `data_status` as ordered steps ①②③; `CASCADE` forbidden; 051 idempotent on re-run since it is non-transactional |
| Generic bucket-lag guard fires permanently after narrowing | Per-view budget gains a bucket-width term (D3a); pre-167 caggs unchanged |
| New width regresses the 167 sub-second NFR | Width chosen by measurement (Task B1), NFR re-measured as criterion 12 |
| Widening `COVERAGE_CONTENT_STALENESS` masks a real future staleness | Threshold is *derived* from the width, not chosen; a genuine stall still exceeds one bucket width and fires (D3) |

---

## PM Decisions

Both questions this design raised are resolved (PM, 2026-08-13); neither
blocks task breakdown. Both are recorded with their reasoning because both are
provisional — accepted as the best available under the current structure, not
as settled ceilings.

1. **A 30-day display lag on `mt data status` is accepted for this slice**
   (PM, 2026-08-13) — **provisionally, not permanently.** After this slice,
   `last_bar_ts` for a symbol can trail reality by up to one bucket width
   without the staleness banner firing (D3). The API's `/symbols` ranges are
   unaffected (187 D2's head probe keeps them exact). Build to the 30-day
   width; do not treat it as a settled ceiling.

   **Follow-on, not scheduled:** extending slice 187 D2's floor-plus-head-probe
   shape to `bars_summary` — a coverage floor for the bulk plus a bounded head
   read for the edge — would bring `mt data status` to roughly the underlying
   data's own freshness (~8 h), matching what a 4h-cagg read already provides.
   That is a change to a view with a strict column contract (167 D2) and stays
   out of scope here. Nothing in this slice forecloses it.

   Weighing it later should account for what `mt data status` is actually
   *for*: it duplicates `/api/v1/status` almost exactly (same backend —
   `fetch_status_rows_with_freshness` over the `data_status` view, same
   filters, same health summary), and its one substantive advantage is that it
   needs **no running server**. That makes it the incident tool — reached for
   precisely when the API may be what is down. In that role a stale reading
   costs more than in a convenience tool, which argues for the head-probe
   follow-on over deprecating the command.

2. **The widened `COVERAGE_CONTENT_STALENESS` is accepted** (PM, 2026-08-13).
   D3 changes it from a value the architecture cannot meet to one it can.

   The reasoning, so a later reader does not mistake this for a threshold
   quietly loosened to silence an alarm: at 1 d 4 h the banner fires
   permanently, because no bucket width compatible with slice 167's purpose
   delivers 1-day-fresh coverage. A permanently-firing signal is
   indistinguishable from a broken one and trains operators to ignore it.
   Widening it to `COVERAGE_BUCKET_INTERVAL + max(end_offset)` makes it
   describe the guarantee the system actually provides. A genuine stall — a
   dead refresh policy, a cagg that stops materializing — still exceeds one
   bucket width and still fires.

   Accepting the 30-day display lag (item 1) while keeping a threshold that
   treats that same lag as a fault would be incoherent; the two decisions
   stand or fall together.

   **PM's standing reservation, recorded deliberately:** a 30-day lag is not
   considered *useful*, only the best available under the current structure.
   This is an argument for the item-1 follow-on (floor-plus-head-probe on
   `bars_summary`, which would reach ~8 h), not an objection to this slice.
   Treat both the width and this threshold as provisional ceilings rather
   than settled design.

---

## Effort

**3 / 5.** Two migrations and a constant change are small; the full-history
rematerialization on prod under the pausing runbook, plus the width measurement,
carry the weight.
