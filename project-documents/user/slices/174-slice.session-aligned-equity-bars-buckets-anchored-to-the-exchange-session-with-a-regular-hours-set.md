---
docType: slice-design
slice: session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set
project: trading-data
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [144, 171, 173]
interfaces: [182, 186, 190]
dateCreated: 20261119
dateUpdated: 20261119
status: not_started
---

# Slice Design: Session-Aligned Equity Bars

## Overview

Equity bars wider than a minute come from the four minute caggs (`minute_5min_ohlcv`, `minute_15min_ohlcv`, `minute_hourly_ohlcv`, `minute_4hour_ohlcv`). Since slice 173 they hold tape-eligible values. They bucket on UTC with TimescaleDB's default origins. 5m and 15m happen to line up with the 09:30 ET open. 1h and 4h do not: the 1h bucket that holds 09:30 ET runs 09:00–10:00 ET, so half of it is pre-market. Stored minutes run from 04:00 to 20:00 ET (pre-market, regular hours, post-market) and nothing in the response says which is which.

This slice adds a `session` parameter to `GET /api/v1/bars/{symbol}`. With `session=regular` or `session=extended`, intraday bars are built at read time from the eligible 1m bars, bucketed from the start of each part of the trading day: pre-market from 04:00 ET, regular hours from 09:30 ET, post-market from the close. Opens, closes, early closes and holidays come from the trading calendar, with DST handled. With no `session` parameter nothing changes.

The slice also does three things the new bars depend on:

- it fixes the NYSE/NASDAQ holiday data ([#26](https://github.com/manta-digital/trading-data/issues/26)) and stores the extended-hours bounds per session;
- it applies split and dividend adjustment by session date, not UTC date (the intraday half of [#28](https://github.com/manta-digital/trading-data/issues/28));
- it establishes what the 1m bar stamped at the extended close holds ([#78](https://github.com/manta-digital/trading-data/issues/78)) and keeps it out of every session bar.

Issue [#38](https://github.com/manta-digital/trading-data/issues/38) is the request. trading-engine's slice 144 waits on it.

## Value

- **trading-engine** can request regular-hours 1h bars that start at 09:30 ET, and extended-hours bars where no bar mixes pre-market, regular and post-market minutes. Today it cannot build a correct regular-hours 1h bar from anything we serve. It also gets one curl request that proves the feature is live, which its Task 1 check needs.
- **mt-ui** can draw regular-hours charts without pre-market and post-market noise, and hourly charts that line up with the open.
- **Correctness:** adjusted session bars are right on the evening before an ex-date. Today a winter post-market minute after 19:00 ET falls on the next UTC date and gets the next day's adjustment factor. The holiday fix stops 2027 holidays from turning into trading days on 2027-01-01. That would otherwise break gap detection, not just this slice.

## Technical Scope

**Included**

1. **Session bounds in the calendar.** Migration 067 adds `extended_open_utc` and `extended_close_utc` to `trading_sessions`. NYSE/NASDAQ holidays become explicit dated rows, in the pattern of `cme_calendar_history.py`. 2027–2028 are added, 2021-12-31 opens, and 2025-01-09 closes. `holidays_seeded_through` is raised and the affected session rows are re-populated (#26).
2. **Session bar reader.** One SQL statement per request reads eligible 1m bars (or raw bars with `prints=all`) joined to the symbol's sessions. It assigns each minute a phase (pre, regular, post) and a bucket anchored at that phase's start, then aggregates first open, max high, min low, last close and summed volume per bucket.
3. **Adjustment by session date.** `adjusted()` accepts the session date of each bar instead of deriving it from the bar's UTC timestamp.
4. **API.** `session` on `GET /api/v1/bars/{symbol}` takes `utc` (the default, today's behavior), `regular` or `extended`. Bars served with `regular` or `extended` carry `session_date` and `phase`. The value is echoed in `applied`, and admission counts the minutes it will read. The OpenAPI schema and both API references are updated.
5. **The close-stamp bar (#78).** An investigation across many symbols and days, early closes included, written up in a note and in both API references. Session bars exclude the bar by construction.
6. **140 architecture note.** The equity caggs section says what session reads are, and that they bypass the caggs.

**Excluded**

- **Daily and wider granularities with `session`.** `1d`, `1w`, `1mo` and `1q` keep serving EODHD's daily data and the daily caggs. Adding `session` to them is a `422`. A regular-hours daily bar built from minutes is future work. Its close would be the last regular minute's close, not the official close, which is exactly what #78 has to settle first.
- **The daily-cagg half of #28** (`1w`/`1mo`/`1q` buckets that span an ex-date). #28 stays open for it.
- **A `closing_print` field or flag** (#78 item 3). The investigation documents the bar. Whether the API should mark it is decided after the finding.
- **New stored aggregates.** No session caggs (D1).
- **New granularity sizes.** The equity menu stays `1m`, `5m`, `15m`, `1h`, `4h` (D7).
- **Changing the default.** `session=utc` stays the default (D3).

## Dependencies

### Prerequisites

- **Slice 144** (`trading_sessions`): the per-session regular open and close in UTC, early closes included.
- **Slices 171 and 173:** the eligible 1m values (`eligible_sql`) and the eligible default.
- **Migration 066** is the latest. 067 follows it on the minute track.

### Interfaces Required

- `instruments.trading_calendar_id`, which says which calendar a symbol trades on (NYSE or NASDAQ, which hold identical hours and holidays).
- `eligible_sql.eligible_value` / `has_eligible_price`: the one definition of a tape-eligible minute.
- `adjusted()` in `src/manta_trading/data/adjustment/_adjusted.py`, extended by D5.
- `bars_admission.check_request` and the `limit` reader (`latest_bars.read_latest`), which already work through the route's `fetch(start, end)` closure.

## Architecture

### Component Structure

| Component | Where | Change |
|---|---|---|
| `SessionMode` | `constants.py` | New `StrEnum`: `utc`, `regular`, `extended`. Defined once and used by the route, admission and the reader. |
| Migration 067 | `market/schema/migrations/minute.py` | Extended bounds columns, explicit NYSE/NASDAQ holiday rows, re-population. |
| NYSE/NASDAQ holiday table | new `market/schema/nyse_calendar_history.py` | Explicit dated rows (closures and early closes), on the pattern of `cme_calendar_history.py`. Replaces the rule generator for these two calendars. |
| Session population | `data/base/session_population.py` | Computes the extended bounds as well. The early-close extended close comes from the holiday row. |
| Session bar reader | new `market/session_bars.py` | The SQL in D2 and a `read_session_bars(...)` function returning a frame indexed by bar start, with `session_date` and `phase` columns. |
| Adjustment | `data/adjustment/_adjusted.py` | Optional `bar_dates` series (D5). |
| Route | `api_server/routes/bars.py` | `session` query param. `_fetcher` dispatches to the session reader when it is not `utc`. |
| Admission | `api_server/routes/bars_admission.py` | Session-mode checks (D6). |
| Response | `api_server/models/responses.py` | `BarRecord` gains nullable `session_date` and `phase`. `AppliedEquityBarParams` gains `session`. |

### Data Flow

```
GET /api/v1/bars/SPY?granularity=1h&session=regular&start=2026-11-18&end=2026-11-18
  → check_request: session valid for 1h; minutes to read ≤ ceiling (D6)
  → fetch(start, end) → read_session_bars(symbol, 1h, regular, prints, start, end)
      SQL: sessions for SPY's calendar with session_date in [start, end]
           ⋈ eligible minute_ohlcv rows in [extended_open_utc, extended_close_utc)
           → phase, bucket → GROUP BY session_date, phase, bucket
  → adjusted(frame, symbol, conn, bar_dates=frame.session_date)   (when adjusted=true)
  → BarsResponse (bars with session_date + phase; applied.session = "regular")
```

`start` and `end` stay inclusive dates. In session mode they are session dates, not UTC dates. A session's post-market can end after midnight UTC, and it belongs to the session it closes.

### State Management

The only stored state that changes is calendar data: the two new `trading_sessions` columns and the NYSE/NASDAQ holiday rows. Session bars are never stored. The freshness probe does not apply, because session reads read `minute_ohlcv` directly, the same as `1m` (`is_stale` is always `false`).

## Technical Decisions

### D1 — Build session bars at read time from 1m bars, not in new caggs
Status: decided

- Each part of the session is bucketed from its own start, and buckets end at the session's own close, which differs on early-close days. That is a per-row origin taken from `trading_sessions`. A cagg can approximate it with `time_bucket(..., 'America/New_York', origin => ...)` but cannot end a bucket at a calendar-driven early close. It also cannot apply #28's rule that a bucket takes its session's adjustment factor, since caggs store raw prices and adjustment happens on read.
- Read-time cost is small. A year of regular-hours minutes for one symbol is about 98,000 rows (252 sessions × 390 minutes) aggregated in SQL. Admission bounds the cost (D6).
- **Rejected:** four or eight new session caggs (regular and extended × 4 widths). They would need storage, refresh policies, `caggs verify`/`repair` coverage and a health check, all for less correct output. This is the same choice #28 recommends for the daily caggs: aggregate from adjusted fine-grained bars.
- If reads turn out too slow for real use, stored aggregates are the next step, the same position slice 309 took for tick-cut bars.

### D2 — A bar never crosses a phase; each phase counts from its own start
Status: decided

- Phases, half-open, per session: **pre** `[extended_open, open)`, **regular** `[open, close)`, **post** `[close, extended_close)`. `session=regular` reads the regular phase only. `session=extended` reads all three.
- Each bucket starts at `anchor + floor((time − anchor) / width) × width`, where the anchor is the phase's start. So regular 1h bars start at 09:30, 10:30 … 15:30 ET, and the 15:30 bar is half an hour. Extended 1h bars are pre 04:00 … 08:00 plus a short 09:00–09:30, then regular as above, then post 16:00 … 19:00.
- Anchoring at the phase start keeps bars from crossing the open or the close. A single grid anchored at 09:30 would make the 15:30 bar mix the last regular half hour with the first post-market half hour.
- The last bar of a phase may be short. Its `time` is its start. Its end is the next bar's start or the phase end. This is documented, not flagged per bar.
- **Rejected:** one grid for the whole extended day (it crosses the close), and anchoring pre-market backwards from 09:30 (it gives a pre-market bar starting at 03:30 that holds only 04:00 onwards).

### D3 — `session` is opt-in; `utc` stays the default
Status: decided

- mt-ui and trading-engine read the current UTC buckets today. Changing the default would silently change every chart and every stored engine result. `session=utc` names today's behavior so `applied` can echo it.
- Making session-aligned bars the default is a later, separate decision, once both clients have moved.

### D4 — The close-stamp bar is outside every session bar
Status: decided

- The bar #78 describes is stamped at the extended close instant: 20:00 ET, or 17:00 ET on early-close days. Phases are half-open, so `[close, extended_close)` never contains it. Session bars exclude it by construction, whatever it turns out to be.
- The slice still investigates it (#78 work item 1). Across a sample of symbols and days, early closes included, it compares the bar's price with the daily close and its volume with the daily volume and with the 16:00 ET minute. The finding goes to `user/notes/174-closing-print-investigation.md` and into both API references, with how a client counts closing volume.
- The `1m` route with `session=utc` keeps serving the bar unchanged. `1m` with `session=extended` does not serve it.
- **Rejected:** folding its volume into the last regular bar. That has to wait until we know whether the volume is already counted in the minutes (#78 shows the sums don't reconcile).

### D5 — Adjustment uses the session date
Status: decided

- `adjusted()` derives the factor from `ts.date()` (UTC). In winter, post-market minutes from 19:00 to 19:59 EST are on the next UTC date. On the evening before a Tuesday-to-Friday ex-date they get the ex-date's factor, so the last post-market hour is adjusted differently from the rest of that session.
- `adjusted()` gains an optional `bar_dates` argument, the session date of each row. The session reader passes `session_date`. Every minute in a session bar shares one session date, so one factor per bar is correct by construction. That rule is #28's, applied to intraday buckets.
- Existing callers pass nothing and are unchanged.

### D6 — Admission counts minutes read; its ceiling is measured
Status: decided

- The row ceiling (`MT_API_MAX_BARS_PER_REQUEST`) still bounds the bars returned. A session read also costs the minutes it scans: a 4h extended request over five years returns about 5,000 bars but reads about 1.2M minutes.
- So a session request is also admitted on an estimate of the minutes it reads: sessions in range × minutes per phase read (390 regular, 960 extended), computed from `trading_sessions` before any minute is read. It's checked against a new setting, `MT_API_MAX_SESSION_MINUTES`. Its default is set from a production measurement in this slice, not guessed. Over the ceiling is a structured `422` with `reason=minute_ceiling`, `limit` and `estimate`, the same shape as the other refusals (#23).
- Also refused with a `422`: `session` other than `utc` with `1d`, `1w`, `1mo` or `1q`.
- `prints=all` is allowed at every intraday size in session mode, because session bars are built from 1m rows, which hold raw values. With `session=utc`, `prints=all` stays `1m` only (slice 173).
- `limit` works unchanged. `read_latest` calls the same `fetch` closure, and its windows are session dates.

### D7 — The equity granularity menu is unchanged
Status: decided

- The reader takes a bucket width, so any whole number of minutes would work. Opening the menu (as 309 did for futures) changes the published `Granularity` enum on the equity route. Nobody has asked for it. Out of scope.

### D8 — Fix the holiday data in this slice
Status: decided

- Session bars filter by `trading_sessions`. A wrongly closed day (2021-12-31) drops a real session's bars, and from 2027-01-01 every holiday becomes a trading day in the table. Since this slice already adds columns to `trading_sessions` and re-populates it, fixing #26 in the same migration costs little and touches the table once.
- Holidays and early closes for 2020–2028 become explicit dated rows from NYSE's published schedule. Early-close rows also carry the extended close (17:00 ET; the post-market runs four hours after an early close, which #78's 17:00 stamps confirm). NASDAQ uses the same rows.

### Technology Choices

- Plain SQL over `minute_ohlcv` with a join to `trading_sessions`, in the style of `_ELIGIBLE_MINUTE_QUERY`. First and last values by `array_agg(... ORDER BY time)`, as `bar_reads` does. No new dependency.

### Patterns and Conventions

- `SessionMode` and the phase names (`pre`, `regular`, `post`) are `StrEnum` members defined once in `constants.py`.
- The minutes-per-phase figures used by admission come from `trading_sessions` bounds, not from literals.
- Refusals follow the #23 structured shape. A cancelled statement is the existing `504`.

## Implementation Details

### API Contracts

`GET /api/v1/bars/{symbol}`, new parameter:

| Param | Values | Default | Meaning |
|---|---|---|---|
| `session` | `utc`, `regular`, `extended` | `utc` | `utc`: today's bars (UTC-anchored caggs, every stored minute). `regular`: regular-hours minutes only, bucketed from the open. `extended`: pre, regular and post, each bucketed from its own start. |

In session mode `start` and `end` are session dates. Each bar gains:

```json
{"time": "2026-11-18T14:30:00Z", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0,
 "session_date": "2026-11-18", "phase": "regular"}
```

Both fields are `null` with `session=utc`. `applied` gains `"session": "regular"`.

New `422` reasons: `minute_ceiling` (with `limit`, `estimate`), and `session` with a daily-or-wider granularity (prose names the accepted granularities; `reason=session_granularity`).

### Database / Storage Schema

Migration `067_trading_sessions_extended_bounds_and_nyse_holidays`:

- `ALTER TABLE trading_sessions ADD COLUMN extended_open_utc TIMESTAMPTZ, ADD COLUMN extended_close_utc TIMESTAMPTZ`. Both are nullable because calendars without extended hours (CME) leave them null.
- `ALTER TABLE trading_holidays ADD COLUMN extended_close_time TIME`. It's set on early-close rows.
- Replaces the NYSE and NASDAQ holiday rows with the explicit dated set and raises `holidays_seeded_through` to 2028-12-31.
- Re-populates NYSE and NASDAQ `trading_sessions` rows (open, close and the extended bounds) through `populate_trading_sessions`, as an idempotent upsert. It deletes the session row for any date the corrected data closes.

The migration only changes calendar tables. A dry check, run first, lists every date whose status changes (2021-12-31 opens, 2025-01-09 closes, the 2027–2028 holidays close). A test pins that list.

## Integration Points

### Provides to Other Slices

- **trading-engine slice 144:** session-aligned bars, plus the closing comment on #38 naming one curl request that returns rows (see the walkthrough).
- **mt-ui:** `session=regular` for charts. It regenerates types from `openapi.json` at release.
- **Gap detection and the daily daemon:** correct NYSE/NASDAQ sessions through 2028.
- **Future work:** a regular-hours daily bar and any later `closing_print` field build on the session reader and the #78 note.

### Consumes from Other Slices

- **Eligible values (171/173).** If a minute is all-marked it's absent from eligible reads, and an empty bucket has no bar, as everywhere else.
- **`trading_sessions` (144).** A session date with no row returns no bars. That is correct for a holiday, and is why D8 fixes the data first.

## Success Criteria

### Functional Requirements

1. `GET /api/v1/bars/SPY?granularity=1h&session=regular` for a recent full session returns 7 bars starting at 09:30 ET, each with `phase=regular` and that `session_date`.
2. The same request for an early-close session (2025-11-28) returns 4 bars, the last starting 12:30 ET.
3. `session=extended` 1h for a full session returns 6 pre bars (04:00 … 08:00 plus 09:00), 7 regular and 4 post (16:00 … 19:00). No bar crosses 09:30 or the close.
4. Bars anchor at 09:30 ET across the 2025-11-02 and 2026-03-08 DST changes (the sessions either side of each).
5. Each session bar equals the aggregate of the eligible 1m bars it covers: open, high, low, close and volume. This holds for a sample of symbols, sizes and both modes.
6. No session bar includes a minute stamped at or after the session's extended close (the #78 bar).
7. Adjusted session bars on the evening before a winter Tuesday-to-Friday ex-date use that session's factor for every bar, the last post-market hour included. The case is picked from `corporate_actions` during implementation.
8. With no `session` parameter every response is byte-identical to before, apart from the two new `null` fields and `applied.session`.
9. Refusals: `session=regular` with `1d` is a `422` with `reason=session_granularity`, and an over-ceiling session read is a `422` with `reason=minute_ceiling`.
10. After migration 067, 2021-12-31 is a session, 2025-01-09 is not, 2027 and 2028 holidays are not, and early-close sessions carry a 17:00 ET extended close.
11. The #78 note states what the close-stamp bar holds, with the evidence, and both API references describe it.

### Technical Requirements

- Unit tests: phase and bucket assignment (full day, early close, DST), `SessionMode` admission, and `adjusted()` with `bar_dates`.
- Integration tests on a throwaway database with real calendar rows: criteria 1–7 and 10.
- One load test for a one-year `1h` regular read, holding a latency bound and checking that `/health` stays responsive meanwhile.
- `ruff`, format and mypy add no errors over main.
- `openapi.json` regenerated, and `scripts/check_api_docs.py` passes with `session`, the two fields and the new reasons documented.

### Integration Requirements

- trading-engine can run the curl request named on #38 and get rows.
- mt-ui's current requests are unaffected (criterion 8).

### Verification Walkthrough

Production, after release and migration 067 (cutover script per the release skill; the migration touches calendar tables only).

1. **Regular hours, one session:**
   `curl -s 'http://127.0.0.1:8100/api/v1/bars/SPY?granularity=1h&session=regular&start=2026-11-18&end=2026-11-18' | jq '.count, [.bars[] | .time]'`
   → `7`, and times from `2026-11-18T14:30:00Z` to `2026-11-18T20:30:00Z` (09:30 to 15:30 ET).
2. **Early close:** the same with `start=end=2025-11-28` → `4`, the last bar at `2025-11-28T17:30:00Z` (12:30 ET).
3. **Extended:** `session=extended` for 2026-11-18 → 17 bars. `jq '[.bars[] | .phase] | group_by(.) | map({(.[0]): length}) | add'` → `{"post":4,"pre":6,"regular":7}`.
4. **DST:** `session=regular&start=2026-03-06&end=2026-03-09` → the first bar on 03-06 is `14:30Z` and on 03-09 is `13:30Z`. Both are 09:30 ET.
5. **Bar equals its minutes:** fetch `1m&session=regular` for the same session, sum the 10:30–11:29 ET minutes' volume and compare with the 10:30 bar's volume. Do the same for its open, high, low and close.
6. **Close-stamp bar excluded:** `1m&session=utc` for 2026-11-18 includes a bar at the extended close instant. `1m&session=extended` does not.
7. **Default unchanged:** `granularity=1h&start=2026-11-18&end=2026-11-18` with no `session` returns the same bars as before the release, with `applied.session = "utc"`.
8. **Refusal:** `granularity=1d&session=regular` → `422`, `jq .reason` → `"session_granularity"`.
9. **Calendar:** `uv run mt data ...` has no session listing today, so read it with psql. On `trading_sessions`, confirm 2021-12-31 is present, and 2025-01-09 and 2027-01-01 are absent.
10. **Tell trading-engine:** post on #38 the request from step 1, then close the issue.

## Risk Assessment

### Technical Risks

- **Read cost on a shared server.** Session reads scan 1m rows and do not use the caggs.
- **Calendar re-population.** It rewrites session rows that gap detection reads.

### Mitigation Strategies

- Admission on minutes read, with a ceiling measured on production (D6), the serving statement timeout, and the load test.
- Migration 067's dry check lists every changed date, a test pins the list, and the upsert is idempotent and safe to re-run. Only the three known defects plus 2027–2028 may change.

## Implementation Notes

### Development Approach

1. **The #78 investigation first.** It's read-only and produces the note. D4 does not depend on its answer, but the docs do.
2. Migration 067 and the explicit holiday rows, with the dry check and its pinned list.
3. `session_bars.py` and its unit and integration tests (criteria 1–6).
4. `adjusted()` with `bar_dates` (criterion 7).
5. Route, admission and response changes. Measure on production for the `MT_API_MAX_SESSION_MINUTES` default. Load test.
6. Docs: both API references, `openapi.json`, the 140 architecture note and the #78 note. The closing comment for #38 is written at release.

### Special Considerations

- In session mode `start`/`end` are session dates. Say so in the parameter description, because with `session=utc` they are UTC dates.
- The production database session timezone is America/Denver, so `time::date` shifts dates back a day. Measurements and SQL that need a UTC date must use `(time AT TIME ZONE 'UTC')::date`, and session dates come from `trading_sessions`, never from casting a timestamp.
- The #26 deadline is 2027-01-01. This slice carries that fix, so it should not sit unbuilt into December.
