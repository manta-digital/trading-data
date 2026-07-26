---
docType: slice-design
slice: cagg-freshness-assertion-for-derived-data-readers
project: trading-data
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [163]
interfaces: [162, 167]
dateCreated: 20260726
dateUpdated: 20260726
status: not_started
---

# Slice Design: Cagg freshness assertion for derived-data readers

## Overview

Slice 163 uncovered a production defect that its own fix does not close. A
TimescaleDB refresh policy only reconsiders the last `start_offset` of data, so
**any interruption longer than `start_offset` leaves a hole that resuming the
policy never heals.** During 163's Phase D, job 1003 (the `minute_4hour_ohlcv`
refresh) was paused to allow cagg restructuring and left paused. The daemon's
coverage index reads that exact cagg, so its leading edge froze while raw
`minute_ohlcv` kept growing; `compute_missing_minute_sessions` concluded the
trailing days were missing and re-seeded gap rows every cycle across ~349 of
4,198 symbols for four days. Resuming the job was **not** sufficient — the
policy's `start_offset => '1 day'` healed only the most recent day and stranded
the rest permanently, requiring a manual windowed
`refresh_continuous_aggregate` call.

Two properties make this failure class worth its own slice rather than a
defensive one-liner:

1. **It is silent.** Each redundant re-seed is individually harmless — gap rows
   land under `ON CONFLICT DO NOTHING`, no error is raised, no data is
   corrupted. The only cost is wasted EODHD quota. Nothing in the logs looks
   wrong, so nobody goes looking. The incident was caught because the PM
   noticed anomalous chunk counts, not by any check in the system.
2. **A universe-wide `max(time)` comparison hides it.** The raw and cagg maxima
   differed by a single bucket while 349 symbols were invisible for four days.
   Detection requires per-symbol/per-day granularity, which no existing check
   performs.

163 added a `preflight()` guard, but that guard covers exactly one path: the
maintenance tool refusing to repair a cagg while the coverage-index cagg's
refresh is paused. The other causes of the same staleness — a crashed job, a
policy failing on every fire, an out-of-band `alter_job`, a server restart
mid-maintenance — never pass through maintenance tooling at all. Today they are
covered only by runbook R2's human discipline.

This slice implements the fix designed and validated against prod on
2026-07-25 (recorded as rule 3 of the tick-tier ADR, journal 20260725): a
shared `assert_cagg_fresh(conn, view_name)` helper placed in the **reader**
path, wired to its first consumer.

## Value

Removes a silent, recurring quota leak and — more importantly — closes the
failure class before the tick tier arrives. Tick data multiplies both the
volume pressure that motivates aggressive cagg use and the temptation to let
derived data inform acquisition decisions (ADR rule 2). Slice 167 is about to
add a **second** consumer of a cagg-derived coverage read (`bars_summary`); if
this guard does not exist first, 167 ships an unguarded consumer and the class
doubles instead of closing.

Placing the assertion in the reader is deliberate. Maintenance is the path
already guarded; the uncovered causes never run through it.

## Technical decisions carried in from 163

These were settled during 163's Phase D and validated against prod. The design
phase refines placement and naming, not these conclusions.

### D1 — Four OR'd staleness signals; no single one suffices

One catalog read of `timescaledb_information.jobs` + `job_stats` supplies
`start_offset`, `scheduled`, `last_run_status`, and `last_successful_finish`;
two `max()` probes supply the edges.

| Signal | Catches |
|---|---|
| `raw_max - cagg_max > threshold` | the actual 163 incident |
| `NOT scheduled` | any pause, including out-of-band `alter_job` |
| `now() - last_successful_finish > threshold` | crashed / erroring job still marked `scheduled` |
| `last_run_status <> 'Success'` | policy failing on every fire |

### D2 — The threshold needs a ceiling, not just `start_offset`

`min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)`. This is **required**, not
belt-and-braces. Simulation against the four prod policies exposed a false
negative in the naive design: the daily caggs use 21/90/**270**-day offsets, so
a daily cagg stalled 100 days passes every `start_offset`-relative check. The
threshold is uselessly loose exactly where staleness hides longest — and
because 167's `bars_summary` reads the daily caggs, this applies directly to
the next consumer. The ceiling is a small absolute bound (~1 day for the
acquisition path); its value belongs in the constants module, not inline.

### D3 — Fail-safe by refusing, never by falling back

On trip: log ERROR naming the cagg, the measured lag, and which signals fired,
then return `None`. `build_minute_coverage_index` already returns `None` on
failure and its caller already skips coverage-aware seeding, so the guard
reuses an existing fail-safe rather than inventing one.

Never fall back to a full-window seed — that reintroduces the 22-year re-seed
slice 162 exists to prevent. Prefer failing loudly and doing less work over
silently doing redundant work; silent redundancy is what made the original bug
invisible (ADR rule 4).

### D4 — Detect and refuse; do not auto-remediate

An automatic catch-up `refresh_continuous_aggregate` from inside the daemon's
read path would make a heavy write the side effect of a read. Catch-up stays
with runbook R2 as an operator action.

### D5 — Generalized helper, not an inlined check

`assert_cagg_fresh(conn, view_name)` lives in a shared maintenance module and
resolves views through `GRANULARITY_SOURCE` rather than hardcoding names (the
163 `preflight()` precedent). Granularity-agnostic so 167 consumes it
unchanged.

Probe cost measured on prod: **~0.19 s** for the cagg edge, **~0.75 s** for
raw, both planning-dominated — negligible against a once-per-cycle index build
already costing ~23 s.

## Scope

- `assert_cagg_fresh(conn, view_name)` in a shared maintenance module.
- `MAX_COVERAGE_SOURCE_STALENESS` in the constants module.
- Wire the first consumer: `build_minute_coverage_index`
  (`src/manta_trading/data/gaps/minute_coverage.py`).
- Induced-staleness integration test (see success criteria).

Out of scope: auto-remediation (D4); a freshness check inside maintenance
tooling (163's `preflight()` already covers that path); 167's `bars_summary`
wiring, which is 167's own work using this helper.

## Success criteria

1. **Staleness is induced, not mocked.** On a throwaway DB: pause a refresh
   policy, advance raw past `start_offset`, and observe the guard trip, the
   daemon skip seeding, and the ERROR name the cagg and the measured lag. A
   test that only asserts the helper was *called* does **not** satisfy this
   criterion.
2. Each of the four D1 signals trips independently, verified in isolation.
3. The 270-day false negative is pinned by a regression test that **fails**
   when the ceiling is removed.
4. A fresh, healthy cagg passes with no false positive; the daemon's normal
   seeding path is unchanged.
5. Probe overhead stays within the measured envelope (~1 s total) against a
   once-per-cycle index build.
6. The helper is granularity-agnostic — exercised against a minute cagg and a
   daily cagg without signature change — so 167 consumes it as-is.
7. Re-running the 163 incident shape (pause job 1003, let raw advance) produces
   a refusal instead of a re-seed.

## Verification walkthrough (draft — refined at Phase 6)

1. Stand up a throwaway DB via the cold-start path; confirm caggs and policies
   exist.
2. Baseline: run the coverage-index build; confirm it succeeds and seeds
   normally.
3. Induce staleness per criterion 1; confirm refusal, log content, and that no
   gap rows were written.
4. Trip each remaining signal in isolation (criterion 2).
5. Remove the ceiling; confirm the daily-cagg regression test fails
   (criterion 3).
6. Restore; confirm the healthy path is unaffected (criterion 4).
7. Record probe timings on prod against the measured envelope (criterion 5).

## Cross-slice dependencies and interfaces

- **Dependencies: [163]** — design origin and the prod validation this slice
  implements. 163's `preflight()` remains; it guards the maintenance path while
  this guards the reader path.
- **Interfaces: [162]** — `build_minute_coverage_index` is 162's seeding path;
  this slice adds a precondition, not a behavior change.
- **Interfaces: [167]** — 167's D3a requires this guard. Sequencing this slice
  first reduces 167's D3a to "consume the guard; do not ship an unguarded
  consumer." If this slice has not delivered when 167 starts, 167 must build
  the helper itself rather than ship unguarded.

## Notes

- Design rules: journal 20260725 ADR, rule 3 (`start_offset` is a maintenance
  budget; long pauses are not self-healing) and rule 4 (silent-and-harmless is
  the hardest failure to find).
- Runbook `user/runbooks/cagg-maintenance-pausing.md` R2 documents the manual
  catch-up this slice detects the absence of. R2 stays authoritative for
  remediation.
