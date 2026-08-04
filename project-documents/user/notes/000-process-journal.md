---
docType: notes
layer: project
project: trading-data
audience: [human, ai]
description: Append-only log of process decisions and design reasoning that has no home in other document types
dateCreated: 20260719
dateUpdated: 20260804
status: in_progress
---

# Overview

Each entry is an h2 heading `## YYYYMMDD — Title`, newest first, followed by
**Context** (what prompted it), **Decision** (what was settled), **Rationale**
(why), and optionally **Follow-ups** (issues/slices/docs affected). Entries are
written in timeless decision language — no session transcripts, no line numbers
that drift. When the file exceeds the standard size limit, split per
file-naming-conventions (`-1`, `-2`, …).

# Entries

## 20260804 — A partially-applied filter returns zero, and zero is a plausible answer: three window-bound defects on the serving API, none of which any test could see

**Context:** Slice 186 hardened the bars contract, including changing an empty window
from `404` to `200 count: 0` (D5). Running the verification walkthrough against prod
surfaced that a single-day minute request returned `count: 0`. Investigating that
one-line oddity uncovered three defects across two endpoints, all in how an optional or
converted date bound reaches SQL, and all invisible to a green test suite:

1. **`bars`: `end` was inclusive at daily grain and exclusive at minute grain.** The
   daily path passes dates to a closed predicate; the minute path converted `end` to
   *midnight*, so every minute request silently dropped its last day. A Mon–Fri `1m`
   request returned Mon–Thu — 2,975 bars ending 06-13 23:59 for a window ending 06-14.
2. **`gaps`: a one-sided window returned zero gaps, always.** `has_window` was true when
   *either* bound was supplied, so a one-sided request ran the two-sided query with the
   other bound as `NULL`; `gap_start < NULL` is `NULL`, never true. `?start=1990-01-01`
   on a symbol with 31 known gaps returned `count: 0`.
3. **`gaps`: the same `end`-at-midnight boundary**, excluding any gap beginning on the
   last requested day.

**Decision:**

1. **A filter that cannot be applied must fail, not narrow.** Defect 2's shape is an
   optional parameter reaching SQL as `NULL` inside a comparison, where SQL's three-valued
   logic converts "unknown bound" into "matches nothing". Either express an absent bound as
   an explicitly unbounded value (`COALESCE(%s, 'infinity')`) or refuse the request — never
   let it pass through a comparison operator. The gaps route now uses one statement whose
   every optional filter is null-tolerant, replacing four hand-written variants selected by
   an `if` ladder. The ladder *was* the bug: four query shapes and three optional inputs
   is eight combinations, of which it handled six.
2. **An endpoint whose job is reporting problems must never report "none" on a failed or
   partial query.** A silent empty list from `gaps` is worse than an error: gaps are the
   data-integrity signal, so `count: 0` reads as "healthy" to exactly the monitoring client
   most likely to call it with a one-sided window. Rank the severity of a wrong answer by
   what the caller will *do* with it, not by how wrong it is.
3. **Boundary semantics belong to the parameter, not to the code path.** `start`/`end`
   now mean the same thing on every granularity and every endpoint — inclusive of the whole
   day — even though the mechanism differs (`time.max` against a closed predicate in `bars`,
   next-midnight against a half-open overlap in `gaps`). Where two paths implement one
   documented parameter, pin both with tests in the same file so they cannot drift.

**Rationale:** All three defects share the property this journal keeps rediscovering —
*silent, self-hiding, invisible to its own checks* — but with a new twist worth naming:
**the tests were green and would have stayed green forever.** `test_gaps.py` mocks the
cursor and asserts on the *SQL text* (`assert "gap_start <" in sql`), so Postgres never
evaluated the predicate and the `NULL` annihilation could not appear. This is precisely
the 20260725 rule ("test rendered output, not inputs") applied to a `WHERE` clause: the
rendered artifact is what the *database* does with the SQL, and asserting the string is
asserting one side of the transformation. The corrective is an integration test that
seeds real rows and executes the real statement; it was checked against the pre-fix query
shapes to confirm it fails on them, because a regression test never seen failing is a
guess.

The second-order lesson is about *how the defects were found*, and it is a process
failure worth recording. Defect 1 was noticed and fixed. The `gaps` route shares the same
`_date_to_utc_datetime` helper, and it was inspected and set aside with the reasoning
"the predicate is an overlap test, so the bound means something different there" — which
is **true, and was the wrong conclusion**. The boundary behavior did differ; that
difference was treated as evidence of no problem rather than as a reason to check. Sitting
beside it was the far more serious defect 2, which had nothing to do with the shared
helper and would have been found by any direct look. **A plausible reason why a sibling
differs is not evidence that the sibling is correct.** When a defect is found in one
consumer of a shared helper, every other consumer gets looked at — the mechanism may
differ, and the second defect is often not the one being searched for.

**Follow-ups:** Fixed on `main` post-186-merge; recorded as D13 in the 186 slice design
(bars) and in this entry (gaps). New integration test
`test/integration/test_gaps_window_sql.py` executes the real predicate over all
filter combinations, including granularity-plus-one-bound. The unit tests in
`test_gaps.py` that assert SQL *text* were left in place — they catch route-wiring
regressions cheaply — but they are explicitly not the guarantee.

**Audit of the remaining endpoints (2026-08-04, closing this entry's open item).**
`symbols.py` and `status.py` were checked for the defect-2 pattern — both by reading and
by probing prod with every filter combination and reconciling counts against direct SQL.
**Neither has it**, and the reason each is safe is worth recording, because the two use
different correct patterns:

- `symbols.py` branches on `search is not None` and selects a query with no bound at all
  when absent. An absent filter contributes no predicate — safe by construction.
- `status_queries.fetch_status_rows_with_freshness` builds its `WHERE` dynamically,
  appending a condition *only* for filters that are present. Same property, reached by
  composition rather than branching, and it scales to N filters where the `if` ladder that
  broke `gaps` does not. This is the pattern to copy.

Counts reconciled exactly in every case (`/symbols` 31,612 = `count(*) FROM instruments`;
`?search=AAP` 12 = `ILIKE 'AAP%'`; SPY's status rows daily/OK + minute/FAILED matching the
view row-for-row under every filter combination), and `summary` stayed full-universe
(63,224) regardless of `rows` filtering, as documented.

Two contract observations, neither a defect:

- **`/status` defaults to unhealthy rows only.** A healthy symbol returns `count: 0`,
  which reads as "no such symbol" to a caller who does not know the default. Correct and
  intentional (it mirrors `mt data status`), but it was undocumented — README now states
  it. Same *shape* of surprise as the gaps bug — a filter combination returning zero for a
  reason invisible in the response — which is why it is recorded next to it.
- **Empty-string filters behave differently across endpoints, and both are correct.**
  `?health=` is a deliberate `422`; `?search=` matches everything. That looks inconsistent
  and was first recorded here as such — wrongly, from comparing surface behavior without
  checking whether the underlying cases were analogous. **The rule that covers both:
  reject an empty value only when it would mean something different from omitting it.**
  For `search`, omitted and empty both mean "no prefix restriction" and return the
  identical set (verified: 31,612 either way), so there is nothing to disambiguate and an
  error would reject an unambiguous request. For `health`, omitted means the non-OK
  default while empty means neither that nor "everything" — and an empty `ANY()` array
  matches zero rows — so empty is ambiguous between two wrong answers and must be refused.

  The second test is **how the wrong answer fails.** The worry about `?search=` was an
  unset `?search={q}` template returning the whole registry; but 31,612 rows is
  self-announcing, and a caller notices immediately. `?health=`'s bad outcomes — zero rows,
  or a silently substituted default — are plausible-looking, which is what lets them
  survive, exactly as the gaps defect did. **A filter that fails loudly does not need a
  guard; one that fails quietly does.** Decision: no change to either endpoint.

## 20260727 — Slice 163 close-out: minute-cagg re-chunking + repair complete, standing verify/repair rule now live

**Context:** Slice 166's raw-table rechunk (7-day chunks) invalidated all four minute
continuous aggregates, which were also independently over-chunked ~40x (1.67-day
mat-hypertable intervals inherited from the pre-166 10x-source default). The caggs are
the serving path (`materialized_only=true`), so both defects were live-serving corruption,
not latent debt. Slice 163 fixed both with one windowed drop→refresh→compress sweep per
cagg, in fixed order (4h → 1h → 15m → 5m), driven by parity (`SUM(minute_count)` vs raw
`COUNT(*)`) rather than a bookkeeping table.

**Decision:** All eight design success criteria are met (audit table in the slice 163
design, evidence in `163-baseline-verify-20260724.md`):

- All four minute caggs at 70-day chunk intervals, ~4,239 chunks → 119 each (36x).
- Full parity every year/granularity within the trailing refresh-lag bound — verified
  twice: immediately post-repair (2026-07-25) and after a full trading day
  (2026-07-27), confirming the lag heals and does not accumulate.
- Single-symbol 4h query: ~5.2 s → ~95 ms (plan nodes 12,721 → 238).
- All 119 chunks/cagg compressed; total footprint 41 GB (not the ~300 GB uncompressed
  full-materialization would have cost).
- Repair resumability proven twice — once deliberately (kill mid-4h-sweep), once by an
  unplanned `statement_timeout` failure at window 103/119 of the 5m sweep: both resumed
  with zero operator input via parity re-derivation, no orphaned backend either time.
- All eight jobs (4 refresh + 4 columnstore) resumed and confirmed `Success` both
  immediately and after a full trading day.
- Cold start (migrations alone, repair tool never invoked) proven correct by mutation
  test.
- Slice 162's coverage query returns correct, complete results post-repair.

The standing rule is now live: **after any raw `minute_ohlcv` chunk restructuring, run
`mt data caggs verify`; on parity failure, run `mt data caggs repair`** (rebuilds only
invalidated windows, resumable, safe to interrupt). This is documented in both
subcommands' `--help` text, not just here.

**Rationale:** The closed-window-parity-sum-must-be-exactly-0 gate (runbook R5) is what
makes "verify exits non-zero" and "data is actually missing" distinguishable — `verify`
cannot tell trailing refresh lag from real loss on its own, by design, so the operator
must check whether *only* the single currently-open window fails. Every verify run this
slice produced (D5, D6) followed that pattern: closed windows exact, open window short
by an amount that shrinks and disappears as the trailing refresh policy catches up.

**Follow-ups:** Unblocks slice 167 (already complete, merged). Filed but unindexed in the
163 design (no-standalone-slices rule): coverage caggs missing from `caggs
refresh`/`verify` lag column (LOW); `daily_ohlcv` is now the next table showing 166/163's
disease (over-chunked at 3,364 chunks, `max(time)` planning 1.1-2.1s vs 7ms execution) and
needs a 166-style rechunk slice with a plan entry; `preflight()` in `cagg_repair.py` has no
guard against pausing job 1003 (the 4h refresh the daemon's coverage index depends on)
while a *different* granularity's sweep is running — discovered as an incident mid-slice
(D3-D4), worked around by runbook R1 (leave 1003 scheduled), never hardened in code.

## 20260726 — A guard is not deployed until it has been *observed firing*: slice 167 shipped a freshness check to prod that could never pass, plus three constraints TimescaleDB imposes on wide-bucket caggs

**Context:** Slice 167 replaces `data_status.bars_summary`'s per-symbol scan of raw
`minute_ohlcv` with two coverage caggs, and its central safety property (D3a, inherited
from the 163 incident) is that every read asserts cagg freshness via slice 168's
`assert_cagg_fresh`. Migrations 046–048 were applied to production after the migration
tests passed. The guard itself had never been executed end-to-end against a real database.

It could not have worked. `assert_cagg_fresh` probes a cagg's leading edge with
`max(time_bucket)` — **by column name**. The new coverage caggs named their bucket column
`yr_bucket`, so every probe raised `UndefinedColumn`, which the helper correctly maps to
`PROBE_FAILED`, which is by design a *stale* verdict. The result on production: a guard
that reported the caggs permanently stale, on caggs that were in fact perfectly healthy —
the precise inversion of the property the slice exists to deliver. It surfaced only when a
cold-start test exercised the guard for real rather than against a stub, and was fixed by
migration 049 (a catalog-only column rename, ~0.1 s, no re-materialization).

A second defect of the same family was found in the same pass: `minute_coverage`'s
freshness source had been set to its *parent cagg* `minute_4hour_ohlcv`. That is wrong
twice — `_raw_max` probes `max(time)`, which only a raw hypertable has, and measuring
against an intermediate would let a stalled parent leave the coverage cagg looking fresh
while `data_status` served months-old coverage.

**Decision:**

1. **A guard, assertion, or refusal path is not "delivered" until a test has watched it
   both pass and fire against a real database.** Migrations applying cleanly is evidence
   about migrations, not about the invariant they exist to enable. This extends the
   20260725 "test rendered output, not inputs" rule from values to *control flow*: the
   asserted-input analogue for a guard is "the code that calls it ran without raising."
2. **Deploy order for a slice whose value is a safety property: exercise the property
   locally, then deploy.** Prod application is not the cheap step it appears to be when
   the thing being validated is the guard rather than the schema.
3. **Any consumer of a by-name probe inherits a naming contract.** Where a helper resolves
   columns, tables, or jobs by literal name, a new object that helper will inspect must
   adopt the existing convention. All seven pre-167 caggs used `time_bucket`; the two new
   ones diverged for local readability and broke the contract silently. Prefer the
   convention over the clearer local name.
4. **Freshness is measured against raw, not against an intermediate**, for any derived
   object in a multi-hop chain. The verdict must cover the whole chain to reality, which
   is also the bound the consumer documents.

**Rationale:** The failure mode is the one this journal keeps rediscovering under
different disguises — *silent, self-hiding, and invisible to its own checks* (20260719
background jobs, 20260725 re-pull loop, 20260725 rendered SQL). What makes this instance
worth its own entry is that the broken thing **was the detector**. Every prior entry
assumed a guard, once written, would report honestly; here the guard's failure mode was
indistinguishable from the condition it watches for, and it defaulted to the safe-looking
answer ("stale"), which on an operator-facing read is a permanent false alarm rather than
an outage. A guard that cries wolf constantly is functionally equivalent to no guard,
because it trains the operator to ignore it.

The near-miss is worth naming: had the coverage caggs been genuinely stale at any point
before the defect was found, the guard would have reported it correctly *by accident*, and
the bug would have survived indefinitely behind a plausible-looking verdict.

### Three TimescaleDB constraints on wide-bucket caggs (measured, 2.21.3 / 2.23.0)

Recorded because all three were discovered at implementation, not design, and each
invalidated a decision the slice design had already fixed:

- **A refresh policy's window must span ≥ 2 bucket widths.**
  `add_continuous_aggregate_policy` rejects anything narrower with
  `InvalidParameterValue: policy refresh window too small`. A refresh only re-materializes
  buckets *fully contained* in `[now − start_offset, now − end_offset]`; a narrower window
  can slide into a position containing no whole bucket and silently refresh nothing.
  With a 1-year bucket the floor is `2 × 365 d + end_offset` — measured exactly: **730 days
  rejected, 731 accepted**. This never bound the pre-167 caggs because each has a bucket far
  *smaller* than its offsets (4 h bucket / 1 day offset; 3-month bucket / 270-day offset).
  A wide bucket inverts that ratio, and the D4 reasoning — "cover the parent's refresh
  window plus margin," which suggested ~30 days — is simply unreachable.
- **The same two-bucket rule applies to manual `refresh_continuous_aggregate`**, and
  calendar-aligned windows do not satisfy it: a one-calendar-year window does not contain a
  whole 365-day bucket, because fixed-width buckets do not align to calendar years.
- **`CREATE MATERIALIZED VIEW … WITH (timescaledb.continuous)` materializes on creation**
  unless `WITH NO DATA` is given. Migration 046's 40 seconds was not "creating two caggs" —
  it was backfilling 22 years of coverage for 11,625 symbols. Cheap here only because the
  hierarchical source is the small 4h cagg; the same statement over a raw source would be a
  very different operation applied unannounced to production.

**Corollary on scale claims (reinforces 20260720):** the 4h cagg's row count was asserted
in-session as ~1.2 B, a figure carried over from the discredited set the 20260720 entry
explicitly retired. Simple arithmetic refutes it — a 4-hour bucket admits at most ~2–3 rows
per symbol per trading day, so the cagg is orders of magnitude below raw. The real leverage
is smaller still and only known by measuring: **`minute_coverage` is 102,770 rows**
(11,625 symbols × ~9 year-buckets), which is why grouping it is sub-millisecond. A number
recorded as "wrong" in this journal is not thereby unavailable to a future reader — it must
be re-measured, never recalled.

**Outcome on production:** guard verified firing correctly post-049 (`fresh=True`,
`lag=0:00:00` against real thresholds on both caggs); `data_status` full-universe read
**7.8 s → 198 ms**; coverage parity exact on both branches (`minute_coverage`
`SUM(bars)` = 4,412,419,648 matching the 4h cagg; `daily_coverage` = 34,223,492 matching raw
`daily_ohlcv`).

**Follow-ups:**

- Migration 049 is the corrective rename; 046 carries an inline comment stating that the
  `time_bucket` name is load-bearing, not cosmetic, so it is not "tidied" later.
- `COVERAGE_SOURCE_TABLE` (constants.py) documents why both coverage caggs measure against
  raw hypertables despite one being hierarchical.
- `COVERAGE_REFRESH_MIN_WINDOW_BUCKETS` encodes the two-bucket rule as a unit-tested
  constraint, so a future change to `COVERAGE_BUCKET_INTERVAL` fails at test time rather
  than at migration time against a live database.
- **Open:** slice 167's task 9.5 (prove the guard fires by *inducing* staleness on a
  throwaway DB) is now the load-bearing verification for this entry's rule 1 — reading code
  or asserting the helper is merely *called* does not satisfy it.
- Slice 182 must read through `data/maintenance/status_coverage.py`; the enforcement is a
  grep asserting no `FROM data_status` survives outside that module.

## 20260725 — ADR: design rules for the next storage tier (tick), derived from what minute cost us

**Context:** The storage tiers were deliberately built easiest-first — daily, then minute,
then tick. Minute was the *fail-case* for tick: if minute could not be structured well,
tick could not succeed at all, since tick carries the same failure modes at one to three
orders of magnitude more volume. Slice 163 surfaced four defects during minute's repair.
Three of them were **design-time** decisions that only became expensive later, and all
four scale *worse* with tick. This entry exists so the tick slice's design phase inherits
them instead of rediscovering them.

**Decision:** The following are design-phase requirements for any new storage tier, and
for slice 167 (the nearest instance — it adds a cagg-backed read path).

### 1. Set every cagg's `chunk_time_interval` explicitly at creation

TimescaleDB sizes a new cagg's materialization hypertable at **10x the source
hypertable's interval, frozen at creation time**. It never revisits this. Every cagg
therefore inherits whatever the source interval happened to be on the day it was created.

Minute's caggs were created while `minute_ohlcv` was on a 4-hour interval → 1.67-day
cagg chunks → ~4,239 chunks each. Slice 166 later fixed the *source* to 7 days; the
existing caggs kept the old geometry, and slice 163 was the cost of that.

Tick makes this sharper, because volume pressure pushes the source interval *down*:

| Source interval | Cagg interval (10x) | Chunks / 22.5 yr |
|---|---|---|
| 1 hour | 10 hours | ~19,700 |
| 1 day | 10 days | ~820 |
| 7 days | 70 days | ~117 |

A 1-hour tick source yields ~19,700 chunks per cagg — worse than the state 163 repaired,
and past the planning-time wall slice 166 hit (10m47s for a single-symbol `MIN/MAX`).

**Rule:** in the same migration that creates a cagg, set its mat hypertable interval from
the wall-clock rule — *interval = wall-clock span ÷ target chunk count, never data
volume*. Never accept the 10x default. This is one line per cagg at creation and makes
migration-044-equivalents unnecessary.

Note the corollary discovered while verifying criterion 7: post-043, `minute_ohlcv` is
7 days, so 70-day cagg chunks now arise *automatically* and migration 044 is a verified
no-op on cold start. 044 matters only for databases that already existed. Getting the
source interval right first makes the cagg geometry correct for free.

### 2. Decide deliberately whether derived data may inform acquisition

The expensive defect was not the chunk interval — that was known, planned, and scoped.
It was that **a cagg fed an acquisition decision**. The minute daemon's coverage index
reads `minute_4hour_ohlcv`, so a paused refresh policy silently changed what the daemon
believed existed, and it re-pulled data indefinitely with no error surfaced anywhere.

Tick makes this *more* likely, not less: tick volumes make answering "what do I already
have?" from a rollup far more attractive than scanning raw.

**Rule:** completeness and coverage questions are answered from the raw table, or from a
source carrying an explicit freshness contract that **the reader asserts** rather than
assumes. A derived object that informs acquisition is a production input, not an
optimization, and pausing its refresh is a change to acquisition behavior.

### 3. Refresh policies have a horizon; long pauses are not self-healing

A refresh policy only reconsiders the last `start_offset` of data. Resuming a policy
paused for longer than that heals the most recent window and **strands everything older
permanently** — the scheduled job never revisits it. Current values across the family:
2 h (pre-043 5m/15m), 1 day (minute caggs), 21/90/270 days (daily caggs). The daily
tier's 270-day offset means a stalled policy there could go unnoticed for months.

**Rule:** treat `start_offset` as a maintenance budget. Any interruption longer than it —
deliberate pause, crashed job, failed policy, restart — requires an explicit catch-up
`refresh_continuous_aggregate` over the affected span. Verify with a per-entity,
per-period coverage diff; a universe-wide `max(time)` comparison hides it (ours differed
by one bucket while 349 symbols were invisible for four days).

**Corollary — `start_offset` alone is the wrong staleness threshold.** It is set for
refresh *efficiency*, not for how stale a consumer can tolerate its input, and the two
diverge badly: the daily caggs use 21/90/**270**-day offsets, so a policy stalled for
three months is "within `start_offset`" and passes any check written against it.
Simulation of a 100-day daily-cagg stall confirmed the false negative. A consumer's
freshness bound must be `min(start_offset, <absolute ceiling the consumer requires>)`.
The looser the policy's offset, the longer staleness hides — so the tiers with the
weakest natural signal need the tightest explicit bound.

### 4. Silent-and-harmless is the hardest failure to find

The re-pull loop produced no errors and no corruption — `ON CONFLICT DO NOTHING`
discarded the redundant rows — so the only symptom was wasted provider quota. It was
found by the PM noticing unusual chunk counts, not by any check we had. Failures that are
individually harmless have no natural discovery pressure and persist indefinitely.

**Rule:** where a subsystem can do redundant work harmlessly, add a cheap explicit signal
(counter, log line, periodic assertion). Correctness alone is not observability.

### Two operational rules that generalize

- **Size budgets from the worst case, not the first case measured.** The 300 s
  `statement_timeout` was derived from the 4h cagg (17–62 s/window), survived 1h and 15m,
  and killed the 5m sweep at window 103/119. Cost scaled with raw volume *and* bucket
  density; the binding case was the finest granularity over the densest period, never
  measured before the constant was fixed. Check the projected worst-case **single unit**
  against the ceiling, not the projected total against the clock.
- **Test rendered output, not inputs.** Migration 045 passed unit tests while emitting
  invalid SQL (asserted the constant, not the rendered statement). The cold-start test
  verified caggs *existed* but not that they were configured correctly — a false green on
  the slice's central property. A chaining script aborted a healthy sweep by parsing
  `psql`'s `SET` echo. Same class each time: one side of a transformation was asserted.
  For anything schema-shaped, execute against a real database.

**Rationale:** Rules 1, 2, and 3 are cheap at design time and expensive afterward. Rule 1
cost a full slice to retrofit. Rule 2 cost an incident plus a code guard. Rule 3 is
*still* only partially addressed (see Follow-ups) — the hardest to retrofit, because it
requires detecting a condition nothing currently watches for.

**Follow-ups:**

- **Open gap — rule 3 is not enforced in code.** `preflight()` refuses to repair one cagg
  while the coverage-index cagg's refresh is paused, which blocks the exact path taken in
  163. Nothing detects a pause *exceeding* `start_offset`, performs or verifies the
  catch-up refresh, or notices staleness from any other cause — a crashed job, a failed
  policy, an `alter_job` issued outside our tooling, a restart mid-maintenance. Runbook
  R2 covers it by human discipline only, and this failure's defining property is that
  nobody goes looking. A freshness assertion in the coverage-index reader is the
  candidate fix.
- Slice 167 adds a hierarchical coverage cagg for `data_status` — a cagg-backed read path
  informing operational decisions. Rules 1–3 apply directly; it is the first place to
  enforce them.
- Operational half recorded in `user/runbooks/cagg-maintenance-pausing.md` (R1–R5).

## 20260725 — Derived-data staleness leaks into acquisition: a paused cagg refresh job silently drove a perpetual re-pull, and resuming it was not the fix

**Context:** During slice 163's repair sweeps the minute daemon began re-pulling
many chunks on symbols that were already complete. The 4h cagg refresh job had been
paused for the 4h sweep and left paused after that sweep finished.

**Decision:** Treat any cagg that feeds an acquisition decision as a **production
input**, not merely derived output. Pausing its refresh policy is a change to
acquisition behavior and must be scoped to the shortest possible window. Recorded as
`user/runbooks/cagg-maintenance-pausing.md` (R1–R5) and enforced in code by a new
pre-flight check that refuses to repair one cagg while the coverage-index cagg's
refresh policy is paused.

**Rationale:** The daemon's coverage index reads `minute_4hour_ohlcv`. With that cagg
frozen while raw kept growing, the missing-session diff reported recent sessions as
absent and re-seeded gap rows every cycle. The loop was silent — no errors, and
`ON CONFLICT DO NOTHING` meant no corruption — so the only symptom was wasted provider
calls, which is exactly the kind of failure that persists indefinitely because nothing
alarms.

Two second-order lessons carried more weight than the original bug:

- **Resuming the job does not repair the gap.** All four minute refresh policies use
  `start_offset => '1 day'`, so a resumed job heals only the most recent day and
  strands everything older *permanently*. Any pause longer than `start_offset`
  requires an explicit catch-up `refresh_continuous_aggregate` over the pause window.
- **A universe-wide `max(time)` comparison hides the problem.** Raw and cagg maxima
  differed by one bucket while 349 symbols were invisible for four days. Per-symbol,
  per-day coverage diffs are the only trustworthy check.

**Follow-ups:** Slice 167 adds another cagg-backed read path (hierarchical coverage for
`data_status`) and inherits this coupling — the runbook applies there unchanged.

## 20260725 — Code that parses real output must be tested against real output

**Context:** Two failures in one slice shared a root cause. Migration 045 rendered an
untyped `7 days` into `add_columnstore_policy` and raised a syntax error on prod, having
passed unit tests that asserted the *constant* rather than the *rendered SQL*. Later, a
sweep-chaining script aborted a healthy run because `psql -tAc "SET ...; SELECT ..."`
echoes `SET` on stdout, so `tr -d '[:space:]'` produced `SET0` instead of `0` — the
underlying SQL had been validated, but the shell parsing around it had not.

**Decision:** Where a value crosses a formatting boundary — SQL rendered from a
constant, shell parsing of command output, a regex over a file — the test fixture must
be the *real* artifact, not a reconstruction of it. Assertions on inputs do not
substitute for assertions on rendered output.

**Rationale:** Both bugs were invisible to otherwise-reasonable tests because the tests
asserted one side of a transformation. Only execution against the real consumer catches
this class. The cold-start integration test was extended for the same reason: it
verified caggs *existed* but never that migrations 044/045 took effect, so it would have
passed on a database with the wrong chunk interval and no compression — a false green on
the slice's central property.

**Follow-ups:** Guard added to the cold-start test (mat `chunk_time_interval`,
`compression_enabled`, columnstore policy count per cagg). Runbook R5 documents the
`psql` `SET`-echo trap and the `PGOPTIONS` alternative.

## 20260720 — Row-scale claims require exact counts; `approximate_row_count` on compressed hypertables is unreliable even post-ANALYZE; ad-hoc prod aggregates require a statement_timeout

**Context:** `minute_ohlcv`'s row count bounced across four figures in two days:
~7.27 B (`approximate_row_count` post-ANALYZE, recorded by slice 166 as fact),
~918 M ("corrected" during slice 167 design from `SUM(minute_count)` over a
cagg that was, unknown at the time, ~79% under-materialized), ~1.2 B (operator
estimate extrapolated from the 5-min cagg — poisoned by the same
under-materialization; older SP500-era planning anchors land nearby), and finally
**4,405,379,285 — the exact `count(*)`**, which post-166 runs in ~1.3 s using
compressed-batch metadata. Each wrong figure came from trusting a derived or
estimated source because the exact count *used to be* prohibitively slow.
Verifying the exact figure with a full-table `GROUP BY extract(year ...)`
then caused a production incident: the expression GROUP BY cannot use batch
metadata, so it decompressed the entire table server-side; the client timed
out (which does not cancel the backend), follow-up queries stacked behind it,
a backend died ("server closed the connection unexpectedly"), and the server
required a reboot.

**Decision:** (1) The only authoritative row-scale source is an exact
`count(*)` — cheap now on a healthy chunk layout; `approximate_row_count` is
treated as order-of-magnitude only on compressed hypertables (it was still
~66% high *after* ANALYZE: 7.31 B vs 4.41 B), and a cagg-derived count is
valid only after cagg-vs-raw parity is verified. (2) Capacity planning uses
the corrected compressed floor: 75 GB TOAST ÷ 4.405 B rows ≈ **17 bytes/row**
(supersedes the ~10 bytes/row figure in the 20260719 chunk-interval entry,
which divided by the estimate). (3) Ad-hoc analytical queries against prod
run under `SET statement_timeout` sized to intent (seconds for probes,
minutes only deliberately); after any client-side timeout the server-side
backend is explicitly cancelled (`pg_cancel_backend`) before anything else is
run. (4) Full-table aggregates over expressions (anything the columnstore
cannot answer from batch metadata) are treated as decompress-everything
operations and bounded or windowed accordingly.

**Rationale:** Three independent readers (slice 166's docs, slice 167's
design, the PM's recollection) each held a different number for the same
table, and each source *looked* authoritative. Estimates and derived tables
drift silently; only the source table counts. The incident half of the lesson
is the operational mirror of the same error — treating a query as cheap
because a superficially similar one (metadata-assisted plain `count(*)`) was
cheap.

**Follow-ups:** Corrected figures in slices 163/167 designs and 140-plan
entries 23/27 (raw = 4.405 B exact; caggs ~79% under-materialized overall —
per-year 9.5–21% in 2019+, higher pre-2019; full uncompressed cagg
materialization ≈ 300 GB, not 500 GB). Tick-capacity math must use
17 bytes/row. `mt data cagg verify` (slice 163) makes the parity
precondition checkable. Daemon must be verified running after the
2026-07-20 server reboot.

## 20260719 — Stage-then-drop rewrite cycles must take the exclusive lock BEFORE the snapshot, and safety must come from the transaction, not from operational circumstance

**Context:** The slice 166 rechunk driver's window cycle was: snapshot the
window's rows into a temp table, `drop_chunks()`, reinsert, all in one
transaction, with a staged==reinserted rowcount guard. Code review (166
code-review F001) identified a silent-loss race the guard cannot catch: a
concurrent application writer (daemon, `mt data pull`, gap seeding) that
commits rows into the window *after* the stage snapshot but *before*
`drop_chunks` acquires its exclusive lock has those rows destroyed by the
drop and never reinserted — and both sides of the rowcount guard equally
exclude them, so the check passes. The production run happened to be safe
because the daemon was stopped and jobs were paused, but nothing in the tool
enforced that safety, and the tool is reusable.

**Decision:** Any stage-then-drop rewrite takes `LOCK TABLE <hypertable> IN
EXCLUSIVE MODE` as the **first statement of the window transaction, before
the stage snapshot**. EXCLUSIVE blocks all writers for the window's duration
(seconds) while leaving readers unaffected; a writer therefore lands either
wholly before the snapshot (and is staged) or wholly after the commit (and
writes into the new chunk) — never in between. Row-count guards are kept as
invariant checks but are **not** accepted as concurrency protection: a guard
that compares two views taken under the same snapshot cannot detect rows
that neither view saw. Operational preconditions ("daemon is stopped")
remain documented operator guidance but never substitute for the lock.
The lock's presence is proven by a deterministic test using an
`after_stage` injection seam that attempts a concurrent write inside the
critical span and asserts it is refused — concurrency claims get
deterministic tests via seams, not sleep-based races.

**Rationale:** This is the second silent-loss mechanism found in one slice
(after the cagg-refresh collision), and both share a shape: the danger
window is invisible to the code's own consistency checks, and the loss
leaves no error. Correctness against concurrent writers must be structural
(a lock ordering guarantee) rather than circumstantial (a process someone
remembered to stop), because the circumstance is exactly what erodes first
when a tool is reused months later by an operator who didn't read the
original slice.

**Follow-ups:** Implementation and test:
`market/maintenance/rechunk.py` (`_rewrite_window`), integration test
`test_concurrent_writer_blocked_during_window`; review record in
`166-review.code.*.md` (F001, Resolution). Related journal entry: the
background-job pause (cagg refresh corruption) — the two together define the
concurrency preconditions for any future chunk-restructuring tool,
including slice 163's cagg re-chunk.

## 20260719 — Chunk intervals must be sized against wall-clock span, not data volume; the archived tick schema's 1-hour interval would reproduce the minute_ohlcv pathology

**Context:** Slice 166's root-cause work established that `minute_ohlcv`'s
disease was purely *chunk count*: 25,256 four-hour chunks over 22.5 years made
query **planning** — not execution — the dominant cost (846 s planning vs
4.2 s execution on a trivial single-symbol MIN/MAX; ~7 catalog locks per chunk,
176k total). Per-chunk data volume was irrelevant to the failure: chunks
averaged only ~5 MB. Separately, the storage post-mortem corrected an earlier
belief: after rebuilding compression batches at proper size, `minute_ohlcv`'s
7.27 B rows still occupy ~75 GB of TOAST — ~10 bytes/row is the *true*
compressed size of six numeric/bigint OHLCV columns under
`segmentby=symbol, orderby=time DESC`, not batch overhead. Both facts bear
directly on the future tick initiative: the archived tick outline
(100-arch.data-storage.md, "Tick schema design") specifies **1-hour chunks**,
and tick volume is ~28× minute volume at modest scale.

**Decision:** Chunk-interval choices for any future hypertable (tick included)
are made from **wall-clock span ÷ target chunk count (~1,000–2,000 chunks over
the table's lifetime)**, never from per-chunk data-volume guidance alone. The
tick schema's 1-hour interval is flagged as **invalid as written** — over one
decade it implies ~87,000 chunks (3.5× the interval that made minute_ohlcv
unusable) before space partitioning multiplies it further. Capacity planning
for tick storage uses the measured ~10 bytes/row compressed floor, not
optimistic compression-ratio projections.

**Rationale:** The planner and lock manager pay per *chunk*, and that cost is
paid on every unpruned query regardless of how small the chunks are. Data
volume determines how large a chunk gets; wall-clock span determines how many
chunks exist. A table that lives for decades needs its interval derived from
the decade, and minute_ohlcv is the measured proof (14-minute plan time on a
trivial query at 25k chunks; 0.7 s at 1.2k). The tick schema was outlined
before this was learned — without this entry the 1-hour figure would be
implemented as designed and fail at a scale where the rewrite is far more
expensive than minute_ohlcv's 13-hour repair.

**Follow-ups:** Slice 166 design doc (Root-Cause Record + Phase D storage
note) carries the full evidence. The tick schema slice, when scoped, must
revisit `chunk_time_interval` and cite this entry. `MINUTE_OHLCV_CHUNK_INTERVAL`
(constants.py) is the single source of truth for the minute table's interval.

## 20260719 — Re-chunking a live gap-structured hypertable: merge_chunks cannot cross empty ranges; the working mechanism is atomic per-window drop-and-reinsert

**Context:** Slice 166 planned to consolidate minute_ohlcv's chunks with
TimescaleDB's `merge_chunks` (Option A) or `compress_chunk_time_interval`
roll-up (Option B). Rehearsal on a gap-faithful scratch table surfaced a
documented but easily-missed restriction: chunks separated by an **empty
range** cannot be merged ("cannot create new chunk partition boundaries").
Market-hours data guarantees empty ranges — no chunks exist overnight or on
weekends — so prod's 25,256 chunks form 5,671 per-trading-day contiguous runs,
capping both mechanisms at ~4.4× improvement when ~21× was required.

**Decision:** The standard mechanism for re-chunking a gap-structured
hypertable in this project is **in-place per-window rewrite** (slice 166's
`mt data rechunk`): for each target-interval window on TimescaleDB's epoch
grid (1970-01-01 + k×interval — 7-day windows start on Thursdays), one
transaction stages the window's rows to a temp table, `drop_chunks()` removes
the old chunks *and their dimension slices*, and reinsert routes tuples into
one fresh full-width chunk; compression follows outside the transaction.
Windows must be **grid-aligned** (a straddling window yields two chunks).
Interrupted runs are safe by construction: the window either committed or
rolled back, and window state is re-derived from the catalog on every run.

**Rationale:** The per-window transaction gives resumability and a
never-broken intermediate state (proven on prod by killing the run mid-window:
clean rollback, exact row-count match on completed windows, seamless resume).
It preserves the hypertable's identity, so all attached continuous aggregates
survive untouched — the copy-and-swap alternative destroys them. It also fixes
compression-batch fragmentation for free, because compression happens on
freshly inserted full-window data. Cost is a full rewrite of the table
(~7.27 B rows in ~13 h), which is acceptable for a one-shot repair and
irrelevant for correctly-chunked tables going forward.

**Follow-ups:** `mt data rechunk` (market/maintenance/rechunk.py) is the
reusable implementation. Slice 163 (minute-cagg re-chunking) faces the same
constraints on the caggs' materialized hypertables and must not re-derive
them. Re-run `mt data rechunk` periodically (jobs paused) to fold in trailing
windows that were inside `compress_after` during the main run.

## 20260719 — Background jobs must be paused during any chunk restructuring: a concurrent cagg refresh silently and permanently loses materialized rows

**Context:** Slice 166's Phase A rehearsal tested what happens when a cagg
refresh policy fires against a chunk mid-restructure. Expected: blocking
(performance nuisance). Observed: the refresh blocked behind the
restructuring transaction, then completed against a source snapshot taken
before the restructure committed — it **consumed the invalidation log but
materialized nothing** for the affected range. The cagg was left permanently
missing rows while reporting "already up-to-date"; only
`refresh_continuous_aggregate(..., force => true)` over the range repairs it.

**Decision:** Any operation that restructures hypertable chunks (merge,
rechunk, decompress/recompress sweeps) **must pause the full family of
background jobs first** — the table's columnstore policy plus every attached
cagg's refresh policy — with job IDs resolved from
`timescaledb_information.jobs` at runtime, never hardcoded. Maintenance
tooling enforces this in pre-flight (refuse to run, don't warn). Job pause is
classified as a **correctness** control, not a performance courtesy. After
resume, verify `last_run_status = 'Success'` and zero unscheduled jobs.

**Rationale:** Silent, permanent, self-hiding data loss is the worst failure
class: the cagg's own bookkeeping says nothing is wrong, so no later refresh
heals it and only a direct cagg-vs-source comparison can detect it. The
rehearsal methodology that caught this is itself part of the decision: 
rehearse destructive operations on a **gap-faithful scratch hypertable**
(weekday/market-hours synthetic data with an attached cagg and live policy) —
a 24/7-continuous scratch table hides both the adjacency restriction and
realistic policy collision windows.

**Follow-ups:** Slice 166 design doc §Root-Cause Record (A5 rehearsal, Q3)
carries the reproduction. `mt data rechunk` pre-flight implements the refusal.
Slice 163 must adopt the same pause/verify discipline for the cagg
materialized hypertables' own jobs.
