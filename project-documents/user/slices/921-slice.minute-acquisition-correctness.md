---
docType: slice-design
slice: minute-acquisition-correctness
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [919]
interfaces: [162, 165, 912]
effort: 3
dateCreated: 20260907
dateUpdated: 20260909
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

The 140 architecture's gap function (§"Gap function", step 6) specifies the
range as `(session_open_utc(T_first), session_close_utc(T_last))`. The code
has never implemented the second half; every minute range ends at an open.

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
- The two remaining #19 mechanisms (session-open parking, retries consumed
  without a provider answer) are removed at the root, not worked around.
- The health check measures the quantity that failed (bars per session),
  so this class of failure fails a unit within one firing.
- #19 and #20 close with recorded measurements.

## Technical Scope

**In scope**

1. **Range end conforms to the 140 contract** — a minute session range ends
   at `session_close_utc` of its last missing session, as 140 step 6
   specifies. Applied in `compute_missing_minute_sessions` (the minute
   caller); `group_sessions_into_ranges` and the daily path are unchanged
   (the daily provider takes dates, so the daily deviation has no effect and
   is left for initiative 140). **The provider window is a fetch-layer
   mapping**: `_do_minute_symbol` requests `[chunk_start, day_end(chunk_end)]`
   where `day_end` is the UTC midnight after `chunk_end`'s date, so
   extended-hours bars (AAPL 2026-08-27: 08:00–23:59 UTC) keep landing as
   they did under the midnight-anchored legacy rows. `chunk_end` itself, the
   gap accounting, and the classified range stay as they are.
2. **Repair through the single writer** — `scripts/repair_921_minute_sessions.py`
   (`--check` / `--apply`, `cutover_common` pattern) walks the active
   universe and, per symbol, in one transaction under
   `advisory_lock(conn, symbol, "minute", timeout=DAEMON_LOCK_TIMEOUT)` —
   the daemon's own helper — calls
   `update_data_gaps(conn, symbol, "minute", REPAIR_921_WINDOW_START,
   now_midnight, fetch_status_for_unfilled=UNKNOWN, outcome=PARTIAL,
   force_reset_terminal=True, precomputed_ranges=…)` — the published reset
   path `mt data pull --reset` already uses. `precomputed_ranges` comes from
   `compute_missing_minute_sessions` with the symbol's **truncated days**
   (signature: `max(time)` for the session date `≤ session_open_utc`)
   removed from its coverage set, so they are seeded as missing. The window
   starts at `REPAIR_921_WINDOW_START = 2026-07-16` (the day the seeder
   shipped). Rows before the window are untouched. *(Measured 20260909:
   there are **no** midnight-ended legacy rows inside the window — all 1,321
   fall before 2026-07-16, so nothing legacy is reset.)* The
   script writes no SQL of its own against `data_gaps`.
   Serialization: the advisory lock is the same one the daemon takes, so an
   overlap blocks for `DAEMON_LOCK_TIMEOUT` then skips and reports the
   symbol; the script additionally refuses to start while
   `mt-minute-pass.service` or `mt-daily-pass.service` is active.
   Idempotent: a second `--apply` recomputes the same rows (carry-forward
   keeps attempt counts) and reports zero net change; a run that dies
   partway has committed per symbol and is resumed by rerunning. The script
   never calls the provider.
3. **Quota priority and a stated failure policy** — the minute cycle runs
   two phases over the active universe: *trailing* (every symbol's gaps
   whose `gap_end ≥ now − MINUTE_TRAILING_PRIORITY_WINDOW` (7 days), one
   chunk each) then *backfill* (everything else, `most_stale_first` as
   today). Rules, each unit-tested:
   - **No response, no accounting.** `attempt_count` and `fetch_status`
     move only on a classified provider response (200 or 404). 402, 429
     exhaustion, 5xx, timeout, and connection reset touch no gap row. This
     is the general rule that closes the "7,755 rows exhausted without an
     answer" path, whatever the exact accounting turns out to be.
   - **402 aborts the pass immediately** (deterministic: the allowance is
     gone until 00:00 UTC).
   - **Any other provider failure** keeps today's per-symbol handling
     (skip, `TRANSIENT_FAILURE`), and `MINUTE_PASS_MAX_CONSECUTIVE_PROVIDER_FAILURES`
     (5, the `PULL_MAX_CONSECUTIVE_PROVIDER_ERRORS` pattern) consecutive
     symbol failures abort the pass — a 5xx storm or timeout cascade ends in
     five symbols, not 13,000.
   - The pass outcome is `MinutePassOutcome` (`StrEnum` beside
     `LastAttemptOutcome` in `state.py`): `COMPLETE`, `QUOTA_EXHAUSTED`,
     `PROVIDER_UNAVAILABLE`. The journal line names the phase and the count
     of symbols not attempted. Exit code follows the project's partial-pass
     convention (exit 3 fails the unit on purpose, as the Kalshi pass and
     runbook 100 do): `COMPLETE` → 0; `QUOTA_EXHAUSTED` after the trailing
     phase completed → 0, because spending the allowance on backfill is the
     designed steady state and a green unit is the truthful report;
     `QUOTA_EXHAUSTED` inside the trailing phase → 3;
     `PROVIDER_UNAVAILABLE` → 3 in either phase, so a provider outage that
     stalls the backfill drain fails the unit rather than hiding in a
     journal line.
   - **Slice 912's cycle stamp.** `RunnerState.last_minute_cycle_end_utc`
     is an in-process busy-loop guard; 912 states that whether work remains
     is derived from `acquisition_state`, never from that timestamp. An
     aborted pass stamps the cycle end exactly as a completed one does, so
     `--stop-when-done` exits instead of re-running into the same 402, and
     nothing is derived from the stamp. `acquisition_state` rows for the
     un-attempted tail are untouched — "no response, no accounting" covers
     `last_attempt_outcome` and `last_attempt_ts` as well as the gap rows —
     so a later hand run finds the work still pending.
   Cross-firing budget, stated: the 00:35 UTC daily pass spends ~12k
   requests first; the trailing phase needs ~13k (one chunk per active
   symbol); against a 100k allowance the current session lands every night
   by construction, and the backfill takes whatever the three later firings
   have left.
4. **Health measures mass, with absolute thresholds** (919's style: the
   loosest value that catches the failure within a working day; no
   baseline, so no poisoned window). New check `minute session mass`:
   - **Judged session:** the newest `trading_sessions` row for
     `HEALTH_MINUTE_SESSION_CALENDAR = "NYSE"` (11,692 of 13,081 active
     instruments) whose **collecting firing has finished**: the pass that
     lands a session is the first 01:05 UTC minute firing after its close,
     so the session is judged once
     `next_0105_utc_after(session_close_utc) + HEALTH_MINUTE_SESSION_COLLECTION_LAG`
     (`3 h`, the trailing phase measured at ~35 min plus margin) is in the
     past — 04:05 UTC the next day for a regular close and for an early
     close alike (NYSE 2026 early closes: 07-02 17:00 UTC, 11-27 and 12-24
     18:00 UTC; the 01:05 firing is the collecting one either way).
     Weekends and holidays fall out of the calendar. The firing time is the
     `mt-minute-pass.timer` `OnCalendar` value, held once in `constants.py`
     and rendered into the unit, not duplicated.
   - **Thresholds, scaled to the session's length:** the floors are
     per-minute rates applied to the judged session's
     `session_close_utc − session_open_utc` (390 min regular, 210 min at
     an early close), so a half-day is judged against half the mass:
     `HEALTH_MINUTE_SESSION_MIN_BARS_PER_MINUTE = 2,500` (measured healthy
     1.99M / 390 ≈ 5,100 per minute on 2026-08-27; broken 45k / 390 ≈ 115)
     and symbols with `≥ HEALTH_MINUTE_SESSION_SYMBOL_MIN_BARS = 30` bars
     `≥ HEALTH_MINUTE_SESSION_MIN_SYMBOLS = 5,000` (measured 7,259 on
     2026-08-27, ~0 since 2026-08-31; the symbol count does not scale with
     session length, and 30 bars is reachable in a 210-minute session by
     every symbol that reaches it in 390).
   - **Measurement source and read bound:** both quantities are read from
     `minute_4hour_ohlcv` — `SUM(minute_count)` and
     `COUNT(*) FILTER (WHERE symbol-sum ≥ 30)` over the buckets that OVERLAP
     `[session_open_utc, session_close_utc)`, grouped by symbol — never from
     raw `minute_ohlcv` (140-slices §166/§167
     recorded the raw-table latency cliff). One session is ~41k cagg rows
     (measured 2026-08-27), a sub-second read; the check runs under
     `CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT`'s sibling
     `HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT = "30s"` and exits 2 on
     timeout like every 919 check. The cagg's own freshness is already
     judged by the `cagg minute_4hour_ohlcv` line, so a stale cagg fails
     there, not as a false mass reading.
   - One line: `OK   minute session mass   2026-09-08 (390 min): 1,991,311 bars, 7,259 symbols ≥30 bars; floors 975,000 / 5,000`.
   The `eodhd quota` floor check is removed — its consequence is what this
   check measures. `check_raw_freshness` prints the timestamp in UTC (today
   it prints local time with a `UTC` suffix).
5. **Closeout** — #19 and #20 closed with the measurements above and below;
   runbook 100 gains the truncation-signature query and
   `repair_921_minute_sessions.py --check`; `CHANGELOG` entry.

**Out of scope**

- Recompression of the mat chunks touched by #20's catch-up refresh
  (optional per the issue; recorded there as deferred).
- Delisting-aware `data status` health and exact minute `last_bar` (#14).
- The Kalshi pass's `partial` exit 3 failing its unit hourly (separate).
- The daily path's range end (returns `session_open_utc` for both ends,
  harmless with a date-based provider) — noted to initiative 140, not fixed
  here.

## Dependencies

### Prerequisites

- Slice 919 (`mt data health`, `mt-health.timer`) — the check is added to
  its `gather()`/`render()` structure.
- Slice 145's `update_data_gaps` single-writer contract and slice 162/165
  seeding code paths — consumed and corrected, not replaced.

### Interfaces Required

- `trading_sessions(session_date, session_open_utc, session_close_utc)` per
  calendar; `instruments.trading_calendar_id`.
- `minute_4hour_ohlcv` as the coverage source (unchanged).
- `data/locking.advisory_lock` and `DAEMON_LOCK_TIMEOUT`.
- EODHD intraday `from`/`to` semantics as probed above (exact).
- Two 140-owned signatures gain optional, backward-compatible parameters
  (`compute_missing_minute_sessions` takes a per-symbol set of days to
  treat as uncovered; `pick_most_recent_actionable_gap` takes
  `min_gap_end`). Existing callers are unchanged and no published contract
  moves, so this is within the maintenance band's latitude (900-arch:27)
  and is recorded here rather than escalated; the daily-path range end,
  which would change a contract, is escalated (Out of scope).

## Architecture

### Component Structure

| Component | Change |
|---|---|
| `data/gaps/minute_coverage.py` | `compute_missing_minute_sessions` returns ranges ending at `session_close_utc` of the last missing session (140 step 6); accepts a per-symbol set of days to treat as uncovered (used by the repair). |
| `data/gaps/compute_missing_ranges.py` | unchanged. |
| `data/acquisition/daemon/minute.py` | provider window `day_end(chunk_end)`; two-phase cycle; no-response-no-accounting; 402 abort; consecutive-failure breaker; `MinutePassOutcome`. |
| `data/gaps/actionable_gap_selector.py` | optional `min_gap_end` filter for the trailing phase. |
| `data/acquisition/state.py` | `MinutePassOutcome`. |
| `cli/commands/health.py` | `check_minute_session_mass`; quota check removed; UTC label fix. |
| `constants.py` | `MINUTE_TRAILING_PRIORITY_WINDOW`, `MINUTE_PASS_MAX_CONSECUTIVE_PROVIDER_FAILURES`, `MINUTE_PASS_FIRING_TIMES_UTC` (rendered into `mt-minute-pass.timer`), `HEALTH_MINUTE_SESSION_*` (calendar, collection lag, bars-per-minute floor, symbol floor, per-symbol bar floor, statement timeout), `REPAIR_921_WINDOW_START`. |
| `deploy/systemd/mt-minute-pass.timer` | `OnCalendar` and `MINUTE_PASS_FIRING_TIMES_UTC` are held in agreement by a unit test that parses the timer, so the health check and the timer cannot disagree. (Task-breakdown revision: the unit file stays authoritative rather than being rendered by `install-production.sh`, which installs units verbatim from a bash loop and has no venv at that point; a `configparser` guard in `test/unit/deploy/test_units.py` catches the same drift at the same place the Kalshi units are already asserted.) |
| `scripts/repair_921_minute_sessions.py` | one-time repair via the single writer, `--check` / `--apply` / `--verify`. (Task-breakdown revision: `--verify` is a read-only mode printing the SC3/SC7 acceptance measurements on demand — reusing the health check's own selector, query, and rule function — so verification is an action the cutover performs rather than a wait for the next morning.) |
| `scripts/cutover_921_minute_sessions.py` | preconditions (installed version, no pass active, quota just reset), repair `--apply`, next-morning report. |

### Consumers of minute `gap_end` (every reader, and what changes)

The row's end moves from 13:30 UTC to 20:00 UTC on the same date. Each
consumer, checked in code:

| Consumer | Effect |
|---|---|
| `coalesce_data_gaps._are_adjacent` | compares `next_trading_session_after(prev.gap_end.date())` with `current.gap_start.date()` — dates only; same date as before, so adjacency is unchanged. (A midnight end would have broken this; that is why the range end is the session close, not midnight.) |
| `actionable_gap_selector` | `gap_end <= to_ts` with `to_ts = now_midnight`; 20:00 ≤ 00:00 next day, unchanged. Gains the trailing-phase `min_gap_end` filter. |
| `update_data_gaps` (`_delete_intersecting`, carry-forward by `gap_start`) | window intersection and carry-forward keyed on `gap_start`; unchanged. |
| `_do_minute_symbol` chunk loop, `_advance_minute_gap`, trailing tolerance | `chunk_end == gap_end` by construction is kept; the provider `to` is `day_end(chunk_end)`; the tolerance compares dates — unchanged. `classify_outcome` receives the same requested range it does today. |
| `_do_minute_symbol` frontier gate (`MAX(gap_end) < target_end`) | 20:00 of the last session < next midnight; fires as before. |
| `api_server/routes/gaps.py`, `models/responses.py` (slices 184/185/187) | rows are returned as stored; minute windows now read `[open, close]` per the 140 contract instead of `[open, open]`. No code change; the response shape is unchanged. |
| `cli/commands/data.py` (`mt data gaps`, `gap_end <= %s` filters), `rendering/status_table.py`, `maintenance/status_queries.py`, `quality/data_gaps.py` | display and filter on the stored value; no code change. |
| `market/schema/migrations/minute.py` | schema only; no change. |

### Data Flow

Nightly firing after this slice:

1. Seed: for each active symbol, missing sessions → ranges
   `[open(T_first), close(T_last)]` → UNKNOWN rows.
2. Trailing phase: for each active symbol, the newest actionable gap with
   `gap_end ≥ now − 7 d`, one chunk, requested to `day_end`, full day
   returned by the provider, row deleted on success.
3. Backfill phase: remaining actionable gaps, `most_stale_first`, until the
   first 402 or five consecutive provider failures.
4. On abort: outcome `QUOTA_EXHAUSTED` / `PROVIDER_UNAVAILABLE`; journal
   names the phase and the untouched symbol count; unanswered rows are
   untouched.
5. Health at :50: the judged session's bar and symbol mass against the
   floors.

## Technical Decisions

1. **The range end is the 140 contract, not a third semantics.** The code
   deviates from 140 today (open for both ends); this slice conforms the
   minute path. Extended-hours bars are a provider-window concern and are
   handled where the request is built, so the shared `data_gaps` contract
   is not touched and the coalescer's date-based adjacency keeps working.
2. **Coverage stays day-granular.** A "day covered only if the afternoon
   bucket exists" rule would re-seed illiquid symbols every pass and spend
   quota forever (the 22-year re-seed slice 162 exists to prevent). The fix
   is at the range end; the repair handles history once.
3. **The repair uses the single writer and its lock.** No bespoke SQL
   against `data_gaps`; the reset is `force_reset_terminal=True` over a
   dated window, the seed is the seeder with truncated days marked
   uncovered. *(Measured 20260909: no legacy rows fall inside the window, so
   this price is not paid at all.)*
4. **No response, no accounting** is the rule; 402 and the breaker are
   applications of it. Skipping 13,000 symbols one by one after the quota
   is gone costs 35 minutes, produces 7,000 ERROR lines, and (as measured)
   consumed retries. Nothing useful happens after the first 402.
5. **Absolute floors, not a baseline.** Every session since 2026-08-31
   holds ~45k bars; any trailing median would certify the broken state.
   Floors at half the measured healthy mass catch a 2% day on the first
   run and survive an illiquid Friday.
6. **Quota floor check removed rather than retuned.** No floor is right when
   normal operation is designed to use the whole allowance.
7. **Repair is a script, not a migration.** It is data, it runs once, and it
   must be scheduled against the quota reset; migrations are neither.

## Success Criteria

1. `compute_missing_minute_sessions` returns ranges whose `gap_end_utc` is
   `session_close_utc` of the last missing session, proven by a unit test
   whose fixture is real `trading_sessions` rows (13:30/20:00 UTC in
   summer, 14:30/21:00 in winter). The chunk loop's provider `to` is the
   UTC midnight after `chunk_end` (unit test on the built URL).
2. `repair_921_minute_sessions.py --check` reports truncated symbol-days,
   zero-width rows, and session-open-ended terminal rows; `--apply` goes
   through `update_data_gaps` under the advisory lock (asserted by a test
   with a fake writer); a second `--apply` reports zero net change; the
   script refuses to start while a pass unit is active.
3. After the repair and two nightly firings: truncated symbol-days
   (`max(time) ≤ session_open_utc`) for the last five NYSE sessions across
   active symbols = 0; bars for the judged session ≥ 1,000,000.
4. Unanswered requests change nothing: unit tests for 402, 5xx, timeout,
   and connection reset each assert no `attempt_count` or `fetch_status`
   change; a test reproduces the 2026-09-07 promotion path and proves it
   no longer fires without a response.
5. A first-response 402 ends the pass with `QUOTA_EXHAUSTED`; five
   consecutive symbol failures end it with `PROVIDER_UNAVAILABLE`; the exit
   code is 3 only when the trailing phase was incomplete (unit tests on
   the outcome and the exit mapping).
6. Every active symbol's trailing gap is requested before any backfill
   chunk (unit test on the phase order; the journal shows
   `trailing phase complete: N symbols` before the first backfill line).
7. `mt data health` fails `minute session mass` on a fixture where the
   judged session holds 45k bars and passes on one with 1.99M and on a
   210-minute early-close fixture with 1.05M; the judged session is
   yesterday's from 04:05 UTC and the day before earlier, for regular and
   early closes alike; the read is against `minute_4hour_ohlcv` (asserted
   by the test's query capture); the quota floor line is gone; production
   records at least one `healthy` run after 23:00 UTC.
8. Issues #19 and #20 are closed with the measurements; runbook 100 carries
   the signature query.

### Verification Walkthrough

Before install (baseline, read-only, any host with the DB URL):

```
uv run python scripts/repair_921_minute_sessions.py --check
# truncated symbol-days since 2026-07-16: N
# zero-width minute gap rows: N   session-open-ended terminal rows: N
# pass units active: none
```

Cutover, run by the automation identity just after 00:00 UTC:

```
sudo scripts/cutover_921_minute_sessions.py
# 1. install-production.sh --ref v0.14.0 (twice if units changed)
# 2. preconditions: no pass active, quota reset in the last 30 min
# 3. repair --apply (prints before/after counts per predicate)
# 4. next firings: 00:35 UTC daily, 01:05 UTC minute — trailing phase
```

Next morning:

```
mt-run status                      # == health: OK
journalctl -u mt-minute-pass.service --since "6 hours ago" | grep -E "trailing phase|QUOTA_EXHAUSTED|PROVIDER_UNAVAILABLE|backfill phase"
uv run mt data health              # OK  minute session mass  <date>: <bars> bars, <symbols> symbols ≥30 bars; floors 1,000,000 / 5,000
uv run python scripts/repair_921_minute_sessions.py --check   # zero pending
```

Then close #19 and #20 with the two `--check` outputs and the health line.

## Risk Assessment

- **Quota.** The repair seeds roughly 24k rows plus the existing 70k
  UNKNOWN backlog; the trailing phase guarantees the current session lands
  each night, and the backfill drains behind it at whatever the allowance
  leaves. A slow drain is visible in the journal's abort line, not silent.
- **A wrong reset window re-fetches genuine holes.** Bounded by the dated
  window and reviewed in `--check` output before `--apply`; rows before
  2026-07-16 are never touched. *(Measured 20260909: the window contains no
  legacy rows, and the integration test pins that a pre-window
  `PROVIDER_HOLE` keeps its status and attempt count.)*

## Implementation Notes

Order: (1) range-end conformance and provider-window mapping with tests;
(2) no-response-no-accounting with the reproducing test, then the 402
abort and the breaker; (3) two-phase cycle and `MinutePassOutcome`;
(4) health check; (5) repair and cutover scripts, `--check` against
production; (6) release, cutover, measure, close the issues. Commit at each
section.

## Implementation Correction (2026-09-09, Task 6.8 — production baseline)

The read-only `--check` against production corrects two of this design's
figures and exposed one defect in the repair script itself.

**No legacy rows fall inside the repair window.** This document said the 1,321
midnight-ended legacy rows are "inside" `REPAIR_921_WINDOW_START` and are
reset as "the price of using the published path unchanged". Measured: zero.
They all predate 2026-07-16. The three places that stated otherwise are
corrected inline; the repair neither resets them nor can be judged on it.

**Straddling rows affect 1,147 symbols, not a corner case.** Task 6.3 called
them "plausible in production". At 8.8% of the universe, the decision to widen
the repair window rather than skip the symbol is load-bearing — skipping would
have left 1,147 symbols un-repaired behind a hand-worked exception list.

**`--check` could not finish before this task measured it.** The per-symbol
truncation probe costs 0.5–0.9 s (a lateral join into `minute_ohlcv`), so the
first production run timed out at ~5,750 of 13,083 symbols and a full walk
projected to ~2.5 hours. Replaced with one grouped universe-wide query
(`build_truncated_day_index`, the shape `build_minute_coverage_index` already
uses): 258 s. A pre-cutover gate nobody can afford to run is not a gate.

**The baseline itself** (SC3 before-image, exit 0, nothing written):
13,083 symbols scanned; 10,119 needing repair; 52,700 truncated symbol-days;
4,418 zero-width rows; 348 session-open-ended terminal rows; 0 midnight-ended
legacy rows; 1,147 rows straddling the window start.

## Implementation Correction (2026-09-09, Task 5.8)

Two defects in the health check were found by the Section 5 load test and are
recorded here because one of them was in this design's own wording.

**The bucket filter was specified wrongly.** This document said the mass is
read "over the buckets whose `time_bucket` falls in `[session_open_utc,
session_close_utc)`". Implemented literally, that is wrong: TimescaleDB's
4-hour buckets are aligned to the **day**, not to the session, so a regular
13:30–20:00 UTC session opens partway through the 12:00 bucket, whose
`time_bucket` (12:00) is before the open. The filter dropped that bucket
whole — the first ~2.5 hours of every regular session, ~38% of its bars.
Measured on the load fixture: 990,000 of 1,980,000 seeded bars counted.

In production this would not have failed loudly. A healthy ~5,100 bars/min
would have been reported as ~3,100/min, still above the 2,500 floor — so the
check would have run permanently near its threshold, turning ordinary
variation into false FAILs and masking a genuine partial degradation. The
scope wording above is corrected to "the buckets that OVERLAP" the session,
and the query now reaches back one bucket width (from
`GRANULARITY_BAR_MINUTES`, never a literal). Two unit tests pin it, one for
the opening bucket and one confirming the bucket starting at the close stays
excluded.

**The candidate read had to exclude sessions that have not closed.**
`trading_sessions` is populated ~2 years ahead
(`TRADING_SESSIONS_EXTENSION_YEARS`, kept current by
`maybe_extend_trading_sessions`), so reading "the newest rows" returns only
future-dated sessions, none of which can ever be judged. The check would have
reported `no completed session to judge` on every run in production — a
permanent FAIL carrying no information, which is the same silence this slice
exists to end. The candidate query now bounds on `session_close_utc <= now`.

Neither defect was reachable from the unit tier, which hand-builds candidate
lists and mocks the cursor. Both required a fixture with a real calendar and
real cagg buckets, which is what Task 5.8 exists for.

## Review Response (2026-09-08)

| Finding | Change |
|---|---|
| F001 repair bypasses the single writer | Scope 2 and Decision 3: the repair calls `update_data_gaps(..., force_reset_terminal=True)` per symbol under the daemon's advisory lock; refuses to run while a pass is active; per-symbol commits; idempotent. |
| F002 range end deviates from 140 | Scope 1 and Decision 1: the minute range now ends at `session_close_utc` per 140 step 6 (the code was the deviation); the full-day provider window is a fetch-layer mapping, not a contract change. |
| F003 consumers unanalyzed | New "Consumers of minute `gap_end`" table covering the coalescer, selector, writer, chunk loop, gate, API, and CLI readers. |
| F004 mass check under-specified, poisoned baseline | Scope 4: absolute floors with values and their measurements, a calendar-based judged session with an 8 h collection lag, no baseline. |
| F005 only 402 has a policy | Scope 3: no-response-no-accounting for every provider failure, 402 abort, consecutive-failure breaker, repair partial-failure behavior. |
| F006 intra-pass only | Scope 3: cross-firing budget stated with numbers. |
| F007 magic string | `MinutePassOutcome` `StrEnum` in `state.py`, exit mapping defined. |

Round 2 (CONCERNS, reviewed b3ab989):

| Finding | Change |
|---|---|
| F001 early closes | Scope 4: the judged session is keyed to the collecting firing (04:05 UTC next day for every close), and the bar floor is a per-minute rate applied to the session's real length; the firing time is one constant rendered into the timer. |
| F002 measurement source | Scope 4: read from `minute_4hour_ohlcv` (`SUM(minute_count)`, ~41k rows per session, sub-second), never raw `minute_ohlcv`; own statement timeout; exit 2 on timeout. |
| F003 exit 0 hides an outage | Scope 3: `PROVIDER_UNAVAILABLE` exits 3 in either phase; only a post-trailing `QUOTA_EXHAUSTED` exits 0, with the reason stated. |
| F004 912 stamping | Scope 3: an abort stamps the in-process cycle end like a completed pass (busy-loop guard only, per 912); `acquisition_state` for the un-attempted tail is untouched. |
| F005 plan entry drift | 900 plan entry 22 reconciled to the design (session close + fetch-layer day window; absolute floors). |
| F006 signature extensions | Interfaces Required: recorded as additive, backward-compatible, within the band's latitude. |
