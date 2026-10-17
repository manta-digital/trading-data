---
docType: slice-design
slice: eligible-rollups
project: trading
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [171, 163, 169]
interfaces: [173]
dateCreated: 20261016
dateUpdated: 20261016
status: not_started
---

# Slice Design: Eligible Rollups

## Overview

Slice 171 marks off-tape prints on every minute bar and serves eligible 1m bars on request. The 5m, 15m, 1h and 4h rollups still aggregate raw values, so the UI's spikes remain at those granularities. This slice builds eligible replacements beside the current rollups and fills them in production. Nothing a reader sees changes. Slice 173 swaps them in and makes `prints=eligible` the default.

It was split out of 171 after that design review (2026-10-16). It was split again from 173 on 2026-10-16 (PM decision) because the swap and the default switch can only ship after the build has finished in production, and `mt data migrate apply` has no way to stop between two migrations in one release.

## Value

- The eligible rollups are complete in production before anything switches, so 173's cutover is renames only.
- The build runs while the minute passes run, with no change to what charts, `mt data status` or the seeder read.
- In the 171 prototype, SPY 5m bars with a wick over 1% fall from 249 to 1, MSFT from 859 to 95, and NVDA from 835 to 28. That is what 173 will serve.

## Technical Scope

**Included:**

1. Migration 063: `minute_{5min,15min,hourly,4hour}_ohlcv_next` with eligible definitions, and `minute_coverage_next` over the 4h one, all `WITH NO DATA`, with refresh and columnstore policies.
2. `mt data caggs repair`, `verify` and `rebuild-coverage` learn `--target next`; `repair` gains `--order newest-first`.
3. A cutover script for the release: install, apply 063, then build and verify the `_next` objects.
4. Documentation: runbook 100 (the build, and why `_next` rows appear in `caggs status`), CHANGELOG.

**Excluded:**

- **The swap, the eligible default, the value-parity health check and the NFR re-runs:** slice 173.
- **Marking:** the marks, the detector and the sweep belong to slice 171.
- **Daily-family rollups:** weekly, monthly and quarterly come from EOD bars, not from minute data.

## Dependencies

### Prerequisites

- **171, complete in production:** `off_tape` evaluated on every row, plus `eligible_sql.py` and the eligible-volume rule. The production build must not start while the history sweep is running (see Build order below).
- **163:** the windowed refresh-and-compress `repair` loop, and `cagg_parity`.
- **169:** `rebuild-coverage` and the coverage rollup helpers.
- **0.23.2 (migration 061):** the refresh window (10 days) and compression (14 days) constants the new policies reuse.

### Interfaces Required

- `GRANULARITY_SOURCE` / `MINUTE_CAGG_GRANULARITIES` stay the single source for the rollup names. The `_next` names are derived from them with one suffix constant (`CAGG_NEXT_SUFFIX`), never spelled out.
- The existing creation helpers for the minute rollups and `minute_coverage`, parameterized by view name and parent view name.

## Architecture

### Component Structure

| Component | Location | Role |
|---|---|---|
| Rollup definitions | `market/schema/migrations/minute.py` (063) | Render the four eligible rollups from `eligible_sql.py`, plus `minute_coverage_next` over the 4h one |
| Targeting | `market/maintenance/cagg_parity.py`, `cagg_repair.py`, `coverage_rebuild.py`, `cli/commands/data.py` | `--target current|next` resolves view names; `repair --order newest-first` |
| Cutover | `scripts/cutover_172_eligible_rollups.py` | Hold timers → install → apply 063 → restart serve → release timers → build → verify → report |

### Data Flow

```
minute_ohlcv (+ off_tape, from 171)
      │
      ├─► minute_*_ohlcv (raw definitions) ──► minute_coverage ──► readers   (unchanged)
      │
      └─► minute_*_ohlcv_next (eligible definitions; policies track the head)
               ├─ repair --target next --order newest-first, until verify = parity
               └─► minute_coverage_next ─ rebuild-coverage --target next
```

### State Management

- **Materialization progress** is parity-derived, as `repair` is today (no progress table).
- **The cutover script** is check-then-act at each step, so a rerun resumes at the first step that is not done.

## Technical Decisions

### Rollup definition

Rendered from 171's `eligible_sql.py`. `<has eligible>` is `COALESCE(off_tape,0) <> 15`:

```sql
first(<clamp(open)>, time) FILTER (WHERE <has eligible>) AS open,
max(<clamp(high)>)         FILTER (WHERE <has eligible>) AS high,
min(<clamp(low)>)          FILTER (WHERE <has eligible>) AS low,
last(<clamp(close)>, time) FILTER (WHERE <has eligible>) AS close,
SUM(volume)                FILTER (WHERE <has eligible>) AS volume,
COUNT(*)                                                  AS minute_count
```

- **Volume** follows 171's rule. All-marked minutes are left out of eligible price and volume alike, so eligible 1m rolled up equals the rollup exactly, volume included. Nothing reads rollup volume for accounting: `mt data accounting` reads daily volume.
- **`minute_count` keeps counting every bar.** Coverage, accounting and `cagg_parity` read it, and they must not change meaning. It is also why `repair`'s count parity works unchanged on the `_next` views.
- **A bucket made only of all-marked minutes** yields a row with NULL prices and volume and a non-zero `minute_count`. The row stays, for coverage. 173's read path drops it. Fixtures cover a bucket with one all-marked minute and a bucket of only all-marked minutes.

The test cluster probe from 171 (TimescaleDB 2.29.1) confirmed `FILTER` and these inline expressions in a cagg over a compressed hypertable, with columnstore enabled. A cagg rejects a sub-query in `FROM`, which is why the expressions are inline.

### Why beside, not in place

Dropping and recreating in place would empty 5m and up for the whole rematerialization. It would also empty the 4h rollup, the acquisition seeder's coverage source; the slice 163 incident re-seeded about 349 symbols off a frozen 4h rollup. Building `minute_coverage_next` here as well means 173 does not rebuild coverage with the minute timer stopped.

Rejected: keeping raw rollups beside eligible ones permanently. That doubles about 62 GB of rollups and their refresh cost, for a raw 5m view nobody has asked for.

### Targeting the `_next` views

Today `repair`, `verify` and `rebuild-coverage` resolve view names only through `GRANULARITY_SOURCE` (`cagg_parity.cagg_view`), so they cannot reach a `_next` view. A `CaggTarget` StrEnum (`current`, `next`) is passed down to that one resolver, and to the coverage family's view name in `coverage_rebuild.py`. `--target` defaults to `current`, so every existing command and runbook keeps its meaning.

`repair`'s pre-flight still guards the live coverage index (`_COVERAGE_INDEX_VIEW`, the current 4h rollup). Repairing `minute_4hour_ohlcv_next` pauses only its own policies, never the live one's.

`--order newest-first` reverses the window list. Parity-derived resume is unaffected: each window is still checked before it is rebuilt.

### Who sees the `_next` views

- **Unaffected:** `mt data health` (its fixed `OHLCV_CAGG_VIEWS`), `assert_cagg_fresh` (`_VIEW_GRANULARITY`), bars, `data_status` and the seeder. They name the current objects only, so a partly built `_next` view never fails a check or a probe.
- **Listed:** `mt data caggs status` reads every catalog cagg, so it shows the five `_next` rows. Runbook 100 says these are expected until 173.
- **Policy load:** the `_next` refresh policies run beside the current ones until 173 drops the old set, so minute-cagg refresh work roughly doubles for that time. The cutover report records each `_next` refresh job's last run duration against its schedule interval.

### Build order

1. The build starts only when `off-tape unevaluated` is zero. A sweep `UPDATE` after a window is materialized only invalidates it, and the refresh policies look back 10 days. `repair` checks counts, not values, so an older window built from unswept rows would stay wrong. The script refuses with that reason.
2. 4h first (the coverage parent), then 1h, 15m, 5m, each newest-first. One cagg per `repair` run, as `repair` requires today.
3. `rebuild-coverage --family minute --target next`, then its `--verify`.
4. `verify --target next` exits 0.

`repair` and `rebuild-coverage` refuse unless the target's own policy jobs are paused, and leave the pausing to the caller on purpose. The script is that caller. For each `_next` view it pauses the view's jobs (resolved from the catalog by name), runs the step, and resumes them, logging both. A kill in between leaves them paused, and the rerun resumes them before it exits. `minute_coverage_next` is rebuilt after `minute_4hour_ohlcv_next`'s jobs are resumed, because `rebuild-coverage` refuses while its parent's refresh is paused. `repair` also needs attested disk headroom. The script measures free space on the database volume itself and passes it as `--assume-headroom-gb`, failing with a clear message if it cannot read it.

### Failure handling (cutover)

| Trigger | Effect | Handling |
|---|---|---|
| Install or 063 fails | Timers held | Non-zero exit naming the step; rerun after fixing the cause (the existing `cutover_common` behaviour) |
| `off-tape unevaluated` > 0 | Build refused | Non-zero exit with the count; rerun after the sweep finishes |
| `repair` or `rebuild-coverage` fails or is interrupted | Some `_next` windows not materialized | Rerun; parity-derived resume. Nothing user-visible changes |
| `verify --target next` not at parity | Build not complete | Non-zero exit naming the view; rerun |

## Implementation Details

### Database / Storage Schema

- **063:** four `_next` rollups and `minute_coverage_next`, plus refresh and columnstore policies. On a fresh database they are simply empty and track new data.
- **Disk:** about 62 GB beside the current rollups. The host has 1.2 TB free.

### Migration Plan

| Step | Who | What |
|---|---|---|
| 1 | Phase 7, cutover script | Release with 063; `_next` objects created and tracking the head |
| 2 | Same script, build step | Materialize newest-first, coverage, verify; report |

The build runs as the automation identity through the existing NOPASSWD unit commands, so no PM host step is needed.

## Integration Points

### Provides to Other Slices

- **173:** five complete, verified `_next` objects; `--target next` for its pre-swap verification.

### Consumes from Other Slices

- **171:** marks on every row; the renderer; the volume rule.
- **163:** `repair`/`verify`.
- **169:** `rebuild-coverage` and the coverage helpers.

## Success Criteria

### Functional Requirements

1. The SPY, AVGO and MSFT values from slice 171's criteria are absent from the `_next` 5m and 1h rollups.
2. For any window, eligible 1m bars rolled up to 5m equal `minute_5min_ohlcv_next`, volume included.
3. Bars, `mt data status`, `/api/v1/status`, `mt data health` and the seeder return the same results before and after 063 and the build.
4. `--target next` commands never read or write the current objects; without `--target`, every command behaves as before.
5. After the build, `mt data caggs verify --target next` exits 0 and `rebuild-coverage --verify --target next` passes.
6. Rerunning the cutover script after a kill at any step completes it.

### Technical Requirements

- **Migration integration tests:** 063 on a cold start, and 063 on a database holding the current rollups with data.
- **Fixtures:** a bucket with one all-marked minute, and a bucket of only all-marked minutes (row kept with NULL prices and full `minute_count`).
- **Targeting:** unit and integration tests for the resolver, `repair --order newest-first`, and the build-order refusal.
- **Quality bar:** `ruff` and `mypy` clean; runbook 100 and CHANGELOG updated.

### Verification Walkthrough

1. On the test database:
   ```bash
   uv run mt data migrate apply                                            # 063
   uv run mt data caggs repair --target next --granularity 4h --order newest-first
   uv run mt data caggs verify --target next --granularity 4h              # exit 0
   uv run mt data caggs status                                             # _next rows listed
   uv run mt data health                                                   # unchanged
   ```
2. Values: for MSFT 2026-06-01, compare the 5m after-hours high from `minute_5min_ohlcv` (near 487) and `minute_5min_ohlcv_next` (near 460).
3. Production (Phase 7):
   ```bash
   uv run python scripts/cutover_172_eligible_rollups.py vX.Y.Z           # install, 063, build, verify, report
   ```

## Implementation Notes

### Development Approach

1. `CaggTarget` and the resolver; `--target` on the three commands; `--order newest-first`.
2. Migration 063 and its tests.
3. The cutover script and its tests.
4. Documentation.
