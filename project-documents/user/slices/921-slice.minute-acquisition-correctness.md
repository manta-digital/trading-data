---
docType: slice-design
slice: minute-acquisition-correctness
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [919]
interfaces: [162, 165, 912]
effort: 3
dateCreated: 20260907
dateUpdated: 20260907
status: not_started
---

# Slice Design: Minute Acquisition Correctness (921)

## Overview

Issues #19 and #20 were opened on 2026-08-31 as two silent production
failures. Their code fixes shipped the same day (v0.11.1–v0.11.3) and their
operator steps ran. Measured read-only on 2026-09-07, the state is:

| Item | Measured | Verdict |
|---|---|---|
| #20 — caggs | every cagg `fresh`, 5m/15m lag 0 (health run 2026-09-08 00:50 UTC); migrations 053/054 in the ledger | fixed; needs closeout |
| #19 — frontier gate | 9,279 of 9,354 gap-bearing active symbols have `MAX(gap_end)` in the current week; 75 older | gate works |
| #19 — data actually collected | ~45k minute bars/day for ~10.8k symbols since 2026-08-31, against ~2.0M/day through 2026-08-27; 8,300–9,600 of ~10,800 symbol-days per session hold ≤3 bars; AAPL: one bar per day | **worse than before the fix** |
| health | minute data `OK` (newest bar 3.4 d), every cagg `OK`, `eodhd quota` FAIL in 65 of 71 runs over three days | the check cannot see it |

The minute universe is collecting about 2% of its bars, and the instrument
built to catch exactly this class of failure reports healthy. This slice
fixes the mechanism, repairs the data, reorders quota use so the fix can
land, and replaces the health check that judged the wrong quantity.

### Root cause: the range end is the session open

`compute_missing_minute_sessions` (slice 162) diffs the trading calendar
against a day-granular coverage index and hands the missing sessions to
`group_sessions_into_ranges`, which sets each range's `gap_end_utc` to the
**last missing session's `session_open_utc`** (13:30 UTC). The chunk loop in
`_do_minute_symbol` passes `gap_end` straight to EODHD as `to`.

EODHD honors `to` exactly. Probed 2026-09-08 00:05 UTC against `AAPL.US`:

| request | bars |
|---|---|
| `from=to=2026-09-03 13:30` | 1 (13:30 only) |
| `2026-09-03 13:30 → 20:00` | 391 |
| `2026-09-02 13:30 → 2026-09-03 13:30` | 960 — the second day truncated at 331 |

Consequences, all observed in production:

1. **The trailing session of every coverage-seeded range is cut at its opening
   bar.** A lone trailing session (the normal nightly case) yields one bar.
   Since 2026-08-31 every UNKNOWN minute gap row (70,298) ends at a session
   open; 42,775 are zero-width.
2. **A symbol with no bar at the exact opening minute gets an empty
   response**, classified `EMPTY` → `PROVIDER_HOLE` → terminal, and the
   symbol parks. This is issue #19's original parking mechanism (the
   "trailing-day EMPTY → PROVIDER_HOLE" follow-up) and the likely cause of
   the 6–7 Aug mass `empty` event. 7,672 zero-width `PROVIDER_HOLE` rows
   exist today, all written 2026-09-06.
3. **The thin day counts as covered.** The coverage index is built from the
   4-hour cagg at day granularity ("regardless of per-minute bar presence",
   by design), so a day with one bar is never re-seeded. The truncation is
   permanent and silent.
4. **Why now.** The defect has been latent since 2026-07-16. Until
   2026-08-31 most symbols were served by legacy rows anchored at UTC
   midnight (1,321 survive, all terminal), which fetched whole days. The
   0.11.1 frontier gate plus the reset routed every active symbol through
   coverage-seeded trailing sessions, making the truncation universal.

### Compounding cause: quota order and attempt accounting

The 100k/day EODHD allowance is spent by the 00:35/01:05 UTC firings on the
deep backfill (57,663 UNKNOWN rows at attempt 4 covering 2022–2025 sessions)
before trailing sessions are reached. The 13:05 UTC pass on 2026-09-07
received HTTP 402 for 7,179 of 13,083 symbols and still ran 35 minutes,
skipping one symbol at a time. In that same pass 7,755 zero-width
trailing-session rows were promoted to `RETRY_EXHAUSTED` at attempt 5 —
retries consumed without a provider answer. The exact accounting path (the
seed carry-forward or `_advance_minute_gap`'s promotion) is the first task's
job to pin down with a test, not this design's.

The health quota check fails at its 20k floor every evening by construction,
because normal operation consumes the whole allowance. A unit that is always
failed is not an alarm.

## Value

- Minute data is collected again: full sessions for every active symbol,
  and the six weeks of truncated days are refetched.
- The two remaining #19 mechanisms (session-open parking, quota-starved
  retry exhaustion) are removed at the root, not worked around.
- The health check measures the quantity that failed (bars per session),
  so this class of failure fails a unit within one firing.
- #19 and #20 close with recorded measurements.

## Technical Scope

**In scope**

1. **Range end** — a minute session range ends at the UTC midnight after the
   last missing session's `session_date`. Applied in
   `compute_missing_minute_sessions` (minute path only; the daily path's
   `compute_missing_ranges` is unchanged, its provider takes dates). The
   trailing-tolerance comment in `_do_minute_symbol` already assumes this
   anchoring.
2. **Repair** — one script, `scripts/repair_921_minute_sessions.py`, run
   once with the maintenance credential, that:
   - resets every session-open-ended terminal row (15,785 `PROVIDER_HOLE`,
     8,267 `RETRY_EXHAUSTED`) to UNKNOWN with the corrected end;
     midnight-ended legacy rows (1,321) are genuine holes and stay;
   - seeds every truncated symbol-day since 2026-07-16 — signature:
     `max(time)` for the (symbol, session_date) `≤ session_open_utc` — as
     UNKNOWN rows with corrected ends, grouped into contiguous ranges;
   - prints the counts before and after and refuses to run twice (the
     second run finds nothing to do and says so).
   The nightly firings then do the fetching; the script does not call the
   provider.
3. **Quota priority** — the minute cycle runs two phases over the active
   universe: *trailing* (every symbol's gaps whose `gap_end` falls within
   `MINUTE_TRAILING_PRIORITY_WINDOW`, one chunk each) and then *backfill*
   (everything else, `most_stale_first` as today). The first HTTP 402 aborts
   the pass with outcome `quota_exhausted` and a journal line naming the
   phase and the count of symbols not attempted; no gap row is touched for
   an unanswered request. Both constants live in `constants.py`.
4. **Health** — `mt data health` gains `minute session mass`: the bar count
   and the active-symbol count for the last completed session, judged
   against the median of the previous `HEALTH_MINUTE_MASS_BASELINE_SESSIONS`
   sessions with floor `HEALTH_MINUTE_MASS_MIN_RATIO`. The `eodhd quota`
   floor check is removed — its consequence is what the mass check
   measures. `check_raw_freshness` labels the timestamp it prints correctly
   (today it prints local time with a `UTC` suffix).
5. **Closeout** — #19 and #20 closed with the measurements above and below;
   runbook 100 gains the truncation-signature query and the repair script's
   `--check` invocation; `CHANGELOG` entry.

**Out of scope**

- Recompression of the mat chunks touched by #20's catch-up refresh
  (optional per the issue; recorded there as deferred).
- Delisting-aware `data status` health and exact minute `last_bar` (#14).
- The Kalshi pass's `partial` exit 3 failing its unit hourly (separate).

## Dependencies

### Prerequisites

- Slice 919 (`mt data health`, `mt-health.timer`) — the check is added to
  its `gather()`/`render()` structure.
- Slice 162/165 seeding code paths — modified, not replaced.

### Interfaces Required

- `trading_sessions(session_date, session_open_utc, session_close_utc)` per
  calendar; `instruments.trading_calendar_id`.
- `minute_4hour_ohlcv` as the coverage source (unchanged).
- EODHD intraday `from`/`to` semantics as probed above (exact).

## Architecture

### Component Structure

| Component | Change |
|---|---|
| `data/gaps/minute_coverage.py` | `compute_missing_minute_sessions` maps each range's end to the next UTC midnight after the last session's `session_date` (one helper, `session_range_end`, used by the repair script too). |
| `data/gaps/compute_missing_ranges.py` | unchanged; `group_sessions_into_ranges` keeps returning session opens — the minute caller post-processes. |
| `data/acquisition/daemon/minute.py` | two-phase cycle; 402 abort; attempt accounting fix; `_do_minute_symbol` gains a `phase` bound on which gaps `pick_most_recent_actionable_gap` may return. |
| `data/gaps/actionable_gap_selector.py` | optional `min_gap_end` filter for the trailing phase. |
| `cli/commands/health.py` | `check_minute_session_mass`; quota check removed; timestamp label fix. |
| `constants.py` | `MINUTE_TRAILING_PRIORITY_WINDOW`, `HEALTH_MINUTE_MASS_BASELINE_SESSIONS`, `HEALTH_MINUTE_MASS_MIN_RATIO`. |
| `scripts/repair_921_minute_sessions.py` | one-time repair with `--check` and `--apply`, `cutover_common` pattern. |
| `scripts/cutover_921_minute_sessions.py` | the cutover: preconditions (installed version, quota just reset), repair `--apply`, and the next-morning report query. |

### Data Flow

Nightly firing after this slice:

1. Seed: for each active symbol, missing sessions → ranges ending at the
   next UTC midnight → UNKNOWN rows.
2. Trailing phase: for each active symbol, the newest actionable gap with
   `gap_end ≥ now − MINUTE_TRAILING_PRIORITY_WINDOW`, one chunk, full day
   returned by the provider, row deleted on success.
3. Backfill phase: remaining actionable gaps, `most_stale_first`, until the
   pass's cycle budget or the first 402.
4. On 402: pass ends with `quota_exhausted`; journal names the phase and the
   untouched symbol count; no `attempt_count` changes for unanswered rows.
5. Health at :50: the last completed session's mass against the baseline.

## Technical Decisions

1. **Range end is the next UTC midnight, not `session_close_utc`.** Extended
   hours bars run to 23:59 UTC for liquid symbols (AAPL 2026-08-27: 08:00 to
   23:59) and the legacy rows that worked were midnight-anchored. Closing at
   20:00 would silently drop after-hours bars the store already holds.
2. **Coverage stays day-granular.** A "day covered only if the afternoon
   bucket exists" rule would re-seed illiquid symbols every pass and spend
   quota forever (the 22-year re-seed slice 162 exists to prevent). The fix
   is at the range end; the repair handles history once.
3. **Repair resets by shape, not by symbol list.** Every session-open-ended
   terminal row was produced by a truncated request and is suspect; every
   midnight-ended one predates the defect's exposure. The shape is the
   evidence, so the rule is one predicate and is auditable in SQL.
4. **402 aborts the pass.** Skipping 13,000 symbols one by one after the
   quota is gone costs 35 minutes, produces 7,000 ERROR lines, and (as
   measured) consumed retries. Nothing useful happens after the first 402.
5. **Quota floor check removed rather than retuned.** No floor is right when
   normal operation is designed to use the whole allowance; the mass check
   measures the consequence that matters.
6. **Repair is a script, not a migration.** It is data, it runs once, and it
   must be scheduled against the quota reset; migrations are neither.

## Success Criteria

1. `compute_missing_minute_sessions` returns ranges whose `gap_end_utc` is
   the UTC midnight after the last missing session's date, proven by a unit
   test whose fixture is real `trading_sessions` rows (13:30/20:00 UTC opens
   and closes, 14:30 in winter).
2. The repair script's `--check` reports the truncated symbol-days,
   zero-width rows, and session-open-ended terminal rows; after `--apply`
   the same `--check` reports zero of each pending and refuses to apply
   again.
3. After the repair and two nightly firings: truncated symbol-days
   (`max(time) ≤ session_open_utc`) for the last five sessions across
   active symbols = 0; bars for the last completed session ≥ 1.5M.
4. A pass whose first provider response is 402 ends with
   `quota_exhausted`, touches no gap row, and increments no
   `attempt_count` (unit test with a fake client; a second test proves the
   promotion path that exhausted 7,755 rows on 2026-09-07 no longer fires
   without a response).
5. Every active symbol's trailing gap is requested before any backfill
   chunk (unit test on the phase order; the journal shows
   `trailing phase complete: N symbols` before the first backfill line).
6. `mt data health` fails `minute session mass` on a fixture where the last
   session holds 2% of baseline bars and passes on production after the
   repair; the quota floor line is gone; the health unit records at least
   one `healthy` run after 23:00 UTC.
7. Issues #19 and #20 are closed with the measurements; runbook 100 carries
   the signature query.

### Verification Walkthrough

Before install (baseline, read-only, any host with the DB URL):

```
uv run python scripts/repair_921_minute_sessions.py --check
# truncated symbol-days since 2026-07-16: N
# zero-width minute gap rows: N   session-open-ended terminal rows: N
```

Cutover, run by the automation identity just after 00:00 UTC:

```
sudo scripts/cutover_921_minute_sessions.py
# 1. install-production.sh --ref v0.14.0 (twice if units changed)
# 2. repair --apply (prints before/after counts)
# 3. next firing is the 00:35 UTC daily, then 01:05 UTC minute — trailing phase
```

Next morning:

```
mt-run status                      # == health: OK
journalctl -u mt-minute-pass.service --since "6 hours ago" | grep -E "trailing phase|quota_exhausted|backfill phase"
uv run mt data health              # OK  minute session mass  <bars> bars / <symbols> symbols (baseline …)
uv run python scripts/repair_921_minute_sessions.py --check   # zero pending
```

Then close #19 and #20 with the two `--check` outputs and the health line.

## Risk Assessment

- **Quota.** The repair seeds roughly 24k rows plus the existing 70k
  UNKNOWN backlog; the trailing phase guarantees the current session lands
  each night, and the backfill drains behind it at whatever the allowance
  leaves. Mitigation: the trailing window and the abort-on-402 mean a slow
  drain is visible, not silent.
- **A wrong reset predicate re-fetches genuine holes.** Mitigation: the
  predicate is by row shape and the `--check` output is reviewed before
  `--apply`; midnight-ended rows are never touched.

## Implementation Notes

Order: (1) range-end fix and its test; (2) attempt accounting under 402 with
the reproducing test, then the abort; (3) two-phase cycle; (4) health check;
(5) repair and cutover scripts, dry-run against production `--check`;
(6) release, cutover, measure, close the issues. Commit at each section.
