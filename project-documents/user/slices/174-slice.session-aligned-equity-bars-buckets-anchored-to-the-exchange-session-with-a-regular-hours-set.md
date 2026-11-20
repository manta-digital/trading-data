---
docType: slice-design
slice: session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set
project: trading-data
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [144, 171, 173, 182, 186, 221, 301]
interfaces: [190]
dateCreated: 20261119
dateUpdated: 20261119
status: not_started
---

# Slice Design: Session-Aligned Equity Bars

## Overview

Equity bars wider than a minute come from the four minute caggs (`minute_5min_ohlcv`, `minute_15min_ohlcv`, `minute_hourly_ohlcv`, `minute_4hour_ohlcv`). Since slice 173 they hold tape-eligible values. They bucket on UTC with TimescaleDB's default origins. 5m and 15m happen to line up with the 09:30 ET open. 1h and 4h do not: the 1h bucket that holds 09:30 ET runs 09:00–10:00 ET, so half of it is pre-market. Stored minutes run from 04:00 to 20:00 ET (pre-market, regular hours, post-market) and nothing in the response says which is which.

This slice adds a `session` parameter to `GET /api/v1/bars/{symbol}`. With `session=regular` or `session=extended`, intraday bars are built at read time from the eligible 1m bars, bucketed from the start of each part of the trading day: pre-market from 04:00 ET, regular hours from 09:30 ET, post-market from the close. Opens, closes, early closes and holidays come from the trading calendar, with DST handled. With no `session` parameter the bars are the same as today.

The slice also does three things the new bars depend on:

- it fixes the NYSE/NASDAQ holiday data ([#26](https://github.com/manta-digital/trading-data/issues/26)), stores the extended-hours bounds per session, and reconciles the gap rows the corrected dates affect;
- it applies split and dividend adjustment by the exchange-local date instead of the UTC date on **every** intraday read, `session=utc` included. This is the intraday half of [#28](https://github.com/manta-digital/trading-data/issues/28);
- it establishes what the 1m bar stamped at the extended close holds ([#78](https://github.com/manta-digital/trading-data/issues/78)) and keeps it out of every session bar.

Issue [#38](https://github.com/manta-digital/trading-data/issues/38) is the request. trading-engine's slice 144 waits on it.

## Value

- **trading-engine** gets regular-hours 1h bars that start at 09:30 ET, and extended-hours bars where no bar mixes pre-market, regular and post-market minutes. Today it cannot build a correct regular-hours 1h bar from anything we serve. #38 also asks for extended hours "if cheap", and the cost case is under D6. It gets one curl request that proves the feature is live, which its Task 1 check needs.
- **mt-ui** can draw regular-hours charts without pre-market and post-market noise, and hourly charts that line up with the open.
- **Correctness for every client:**
  - Adjusted intraday bars are right on the evening before an ex-date. Today a winter post-market minute after 19:00 ET falls on the next UTC date and takes the next day's adjustment factor.
  - The holiday fix stops 2027 holidays from turning into trading days on 2027-01-01. That would break gap detection, not just this slice.

## Technical Scope

**Included**

1. **Session bounds in the calendar.** A minute-track migration (the next free id when the slice is implemented; see the Database section):
   - adds `extended_open_utc` and `extended_close_utc` to `trading_sessions`;
   - turns the NYSE/NASDAQ holidays into explicit dated rows, on the pattern of slices 221 and 301 ("nothing is generated from a holiday rule");
   - adds 2027–2028, opens 2021-12-31, closes 2025-01-09, and raises `holidays_seeded_through`;
   - re-populates the affected session rows;
   - reconciles the gap rows those dates affect (#26).
2. **Session read method.** `TimescaleMinuteDataDB.get_session_bars(...)`, beside `get_minute_data`, with the same `adjusted` and `prints` arguments and adjustment applied inside it. One SQL statement reads eligible 1m bars (or raw bars with `prints=all`) joined to the symbol's sessions. It assigns each minute a phase (pre, regular, post) and a bucket anchored at that phase's start, then aggregates per bucket.
3. **Adjustment by exchange-local date.** `adjusted()` derives each bar's factor from its date in the calendar's timezone, not from its UTC date. This applies to every intraday read, `session=utc` included.
4. **API.**
   - `session` on `GET /api/v1/bars/{symbol}`: `utc` (the default, today's bars), `regular` or `extended`.
   - Session responses carry `session_date` and `phase` on each bar. Default responses are unchanged in shape (D9).
   - The value is echoed in `applied`.
   - Session admission estimates bars per phase and minutes read.
   - Dates past the calendar's horizon are a structured `422`.
   - The OpenAPI schema and both API references are updated.
5. **The close-stamp bar (#78).** An investigation across many symbols and days, early closes included. It's written up in a note and in both API references. Session bars exclude the bar by construction.
6. **Architecture amendments.** In 140:
   - the equity caggs section: session reads bypass the caggs;
   - the `adjusted()` date rule: exchange-local date;
   - the `prints=all` rule: allowed at every intraday size in session mode;
   - the calendar contract: new `trading_sessions` columns and explicit NYSE/NASDAQ rows.

   In 180: the bars contract (`session`, `session_date`, `phase`, the new refusals).

**Excluded**

- **Daily and wider granularities with `session`.** `1d`, `1w`, `1mo` and `1q` keep serving EODHD's daily data and the daily caggs. Adding `session` to them is a `422`. A regular-hours daily bar built from minutes is future work. Its close would be the last regular minute's close, not the official close, and #78 settles what the official close is first.
- **The daily-cagg half of #28** (`1w`/`1mo`/`1q` buckets that span an ex-date). #28 stays open for it.
- **A `closing_print` field or flag** (#78 item 3). The investigation documents the bar. Whether the API marks it is decided after the finding (D4).
- **New stored aggregates.** No session caggs (D1).
- **New granularity sizes.** The equity menu stays `1m`, `5m`, `15m`, `1h`, `4h` (D7).
- **Changing the default.** `session=utc` stays the default (D3).

## Dependencies

### Prerequisites

- **Slice 144** (`trading_sessions`, `OutOfHorizonError`): the per-session regular open and close in UTC, early closes included, and the fail-loud rule for dates past the horizon.
- **Slice 221** (`holidays_seeded_through`, `extend_calendar_sessions`): the bound the migration raises, and the clamp on writes past it.
- **Slice 301** (migration 066, `cme_calendar_history`): the explicit-dated-rows pattern the NYSE/NASDAQ rows follow.
- **Slices 171 and 173:** the eligible 1m values (`eligible_sql`) and the eligible default.
- **Slice 182** (the bars response, `BarsResponse`/`BarRecord`) and **slice 186** (admission: the row ceiling, `INTRADAY_MINUTES_PER_TRADING_DAY`, the 20 s serving statement timeout, ceilings as constants with a `Settings` field). Later work on the same route added `applied` (#77, `AppliedEquityBarParams`) and `limit` (#13, `latest_bars.read_latest`).

### Interfaces Required

- `instruments.trading_calendar_id`, which says which calendar a symbol trades on.
- `eligible_sql.eligible_value` / `has_eligible_price`: the one definition of a tape-eligible minute.
- `adjusted()` in `src/manta_trading/data/adjustment/_adjusted.py`, changed by D5.
- The gap writer (`update_data_gaps`), for reconciliation (D8).

## Architecture

### Component Structure

| Component | Where | Change |
|---|---|---|
| `SessionMode`, `SessionPhase` | `constants.py` | New `StrEnum`s: `utc`/`regular`/`extended` and `pre`/`regular`/`post`. Defined once. |
| Session ceiling | `constants.py` + `Settings` | `MAX_SESSION_MINUTES_PER_REQUEST` with an `MT_API_MAX_SESSION_MINUTES` override, following 186 D9. |
| Calendar migration | `market/schema/migrations/minute.py` | Extended bounds columns, explicit NYSE/NASDAQ holiday rows, re-population, gap reconciliation. |
| NYSE/NASDAQ holiday table | new `market/schema/nyse_calendar_history.py` | Explicit dated rows (closures and early closes with their extended close), on the pattern of `cme_calendar_history.py`. Replaces the rule generator for these two calendars. |
| Session population | `data/base/session_population.py` | Computes the extended bounds as well. |
| Session SQL | new `market/session_bars_sql.py` | The statement in D2, rendered from `eligible_sql` and the same first/last expressions the eligible rollups use. |
| Session read | `market/timescale_minute_db.py` | `get_session_bars(...)` beside `get_minute_data`. Adjustment inside. Raises `OutOfHorizonError` past the calendar's horizon. |
| Adjustment | `data/adjustment/_adjusted.py` | Factor by exchange-local date (D5). |
| Route | `api_server/routes/bars.py` | `session` query param. `_fetcher` calls `get_session_bars` when it is not `utc`. Still a thin wrapper. |
| Admission | `api_server/routes/bars_admission.py` | Session-mode estimates and refusals (D6). |
| Response | `api_server/models/responses.py` | `SessionBarRecord` (a `BarRecord` plus `session_date`, `phase`) in a session response model. `AppliedEquityBarParams` gains `session`. |

### Data Flow

```
GET /api/v1/bars/SPY?granularity=1h&session=regular&start=2026-11-18&end=2026-11-18
  → check_request: session valid for 1h; range inside the calendar horizon;
                   estimated bars ≤ row ceiling; estimated minutes ≤ session ceiling (D6)
  → fetch(start, end) → minute_db.get_session_bars(symbol, 1h, regular, prints, start, end, adjusted)
      SQL: sessions for SPY's calendar with session_date in [start, end]
           ⋈ eligible minute_ohlcv rows in [extended_open_utc, extended_close_utc)
           → phase, bucket → GROUP BY session_date, phase, bucket
      → adjusted() (exchange-local date = session_date for every session bar)
  → session BarsResponse (bars with session_date + phase; applied.session = "regular")
```

`start` and `end` stay inclusive dates. In session mode they are session dates.

### State Management

The only stored state that changes is calendar data (the two new `trading_sessions` columns and the NYSE/NASDAQ holiday rows) and the gap rows the corrected dates affect. Session bars are never stored. The freshness probe does not apply, because session reads read `minute_ohlcv` directly, the same as `1m`.

## Technical Decisions

### D1 — Build session bars at read time from 1m bars, not in new caggs
Status: decided

- Each part of the session is bucketed from its own start, and buckets end at the session's own close, which differs on early-close days. That is a per-row origin taken from `trading_sessions`. A cagg can approximate it with a timezone-aware `time_bucket` but cannot end a bucket at a calendar-driven early close.
- Read-time cost is bounded by admission (D6). A year of regular-hours minutes for one symbol is about 98,000 rows aggregated in SQL.
- **Rejected:** four or eight new session caggs. They would need storage, refresh policies, `caggs verify`/`repair` coverage and a health check, for less correct output.
- **No standing parity check.** The 140 `value parity` check exists because a stored rollup can drift from its definition after it is built. Session bars are computed per request from the 1m rows with expressions rendered from `eligible_sql` and the eligible rollups' first/last forms. There is nothing stored to drift. The tests in criterion 5 pin the definition.
- If reads turn out too slow for real use, stored aggregates are the next step.

### D2 — A bar never crosses a phase; each phase counts from its own start
Status: decided

- Phases, half-open, per session: **pre** `[extended_open, open)`, **regular** `[open, close)`, **post** `[close, extended_close)`. `session=regular` reads the regular phase only. `session=extended` reads all three.
- Each bucket starts at `anchor + floor((time − anchor) / width) × width`, where the anchor is the phase's start. Regular 1h bars start at 09:30, 10:30 … 15:30 ET, and the 15:30 bar is half an hour. Extended 1h bars are pre 04:00 … 08:00 plus a short 09:00–09:30, then regular as above, then post 16:00 … 19:00.
- Anchoring at the phase start keeps bars from crossing the open or the close.
- The last bar of a phase may be short. Its `timestamp` is its start. Its end is the next bar's start or the phase end. This is documented, not flagged per bar.
- **Rejected:** one grid for the whole extended day (it crosses the close), and anchoring pre-market backwards from 09:30 (it gives a pre-market bar starting at 03:30).

### D3 — `session` is opt-in; `utc` stays the default
Status: decided

- mt-ui and trading-engine read the current UTC buckets today. Changing the default would silently change every chart and every stored engine result. `session=utc` names today's behavior so `applied` can echo it.
- Making session-aligned bars the default is a later decision, once both clients have moved.

### D4 — The close-stamp bar is outside every session bar
Status: decided

- The bar #78 describes is stamped at the extended close instant: 20:00 ET, or 17:00 ET on early-close days. Phases are half-open, so `[close, extended_close)` never contains it. Session bars exclude it by construction.
- **Why the session bars don't wait on #78.** Every session bar, the regular 15:30 bar included, is the aggregate of the provider's own minutes inside it. The close-stamp bar is stamped four hours after the regular close and is in no phase. Whatever #78 finds, the 15:30 bar's values don't change. What #78 can change is the advice to clients. If it finds the closing-auction volume is reported **only** in the close-stamp bar and not in the 15:59 minute, then regular-session volume summed from session bars misses the auction. The API references will say so, and a `closing_print` field becomes the follow-up. The investigation runs first (Development Approach step 1), so the docs ship with the answer.
- The `1m` route with `session=utc` keeps serving the bar unchanged. `1m` with `session=extended` does not serve it.
- **Rejected:** folding its volume into the last regular bar before we know whether the minutes already count it (#78 shows the sums don't reconcile).

### D5 — Adjustment by exchange-local date, on every intraday read
Status: decided

- `adjusted()` derives the factor from `ts.date()` in UTC. In winter, post-market minutes from 19:00 to 19:59 EST are on the next UTC date. On the evening before a Tuesday-to-Friday ex-date they take the ex-date's factor. So the last post-market hour is adjusted differently from the rest of its session, in `1m` and in the `utc` caggs.
- Every stored minute, 04:00 to 20:00 ET plus the close stamp at 20:00, has the same date in the calendar's timezone (America/New_York) as its session. So the rule is: a bar's factor comes from its date in the calendar's timezone. That is one change inside `adjusted()`, and it applies to every intraday read, `session=utc` included. A session bar's exchange-local date is its `session_date`, so session bars need nothing extra.
- Daily bars are already stamped by session date and are unchanged.
- **Client-visible change on the default path:** adjusted prices of winter post-market minutes after 19:00 ET on the evening before an ex-date, and the `utc` 1h/4h buckets that hold them. It's a correctness fix, listed in the CHANGELOG.

### D6 — Admission: bars per phase, minutes read, and the horizon
Status: decided

- **Horizon.** A session request whose range goes past the calendar's `holidays_seeded_through` is a `422` (`reason=calendar_horizon`, with the bound). That keeps "closed holiday" (no session row, no bars, `200`) distinct from "beyond the calendar" (we don't know). An extended read over a session row with a NULL extended bound fails loudly with a logged `500`. Every NYSE/NASDAQ row has one after the migration, so a NULL means broken data, not an empty answer.
- **Row estimate.** In session mode the estimate is per session and per phase: `ceil(phase_minutes / width)` summed over the phases read, using each session's bounds from `trading_sessions`. So regular 1h is 7 per full session, extended 1h is 17, extended 4h is 5, and early closes count fewer. It is checked against the existing row ceiling (`MT_API_MAX_BARS_PER_REQUEST`). The 186 estimator (`INTRADAY_MINUTES_PER_TRADING_DAY / width`) stays for `session=utc`.
- **Minute estimate.** The same pass sums the phase lengths: the minutes the read scans. It's checked against `MAX_SESSION_MINUTES_PER_REQUEST` (constant plus `Settings` override, 186 D9). Over it is a `422` with `reason=minute_ceiling`, `limit` and `estimate`.
- **How the ceiling's default is set.** Measured on production. The default is the largest minute count whose read completes at p95 within 5 s, a quarter of the 20 s serving statement timeout, on the largest regular-hours symbol. The measurement and the chosen value go in the slice's production note.
- **Extended hours cost.** An extended read scans 960 minutes per full session against 390 regular, about 2.5×. The same minute ceiling bounds both modes, so an extended request simply admits fewer sessions. No second path, no second ceiling.
- **Also refused:** `session` other than `utc` with `1d`, `1w`, `1mo` or `1q` (`422`, `reason=session_granularity`).
- `prints=all` is allowed at every intraday size in session mode, because session bars are built from 1m rows, which hold raw values. With `session=utc`, `prints=all` stays `1m` only (slice 173).
- `limit` works unchanged: `read_latest` calls the same `fetch` closure, and its windows are session dates.

### D7 — The equity granularity menu is unchanged
Status: decided

- The reader takes a bucket width, so any whole number of minutes would work. Opening the menu changes the published `Granularity` enum on the equity route. Nobody has asked for it.

### D8 — Fix the holiday data in this slice, and reconcile the gaps it moves
Status: decided

- Session bars filter by `trading_sessions`. A wrongly closed day (2021-12-31) drops a real session's bars, and from 2027-01-01 every holiday becomes a trading day in the table. This slice already adds columns to `trading_sessions` and re-populates it, so fixing #26 in the same migration touches the table once.
- Holidays and early closes for 2020–2028 become explicit dated rows from NYSE's published schedule. Early-close rows also carry the extended close (17:00 ET, which #78's 17:00 stamps confirm). NASDAQ uses the same rows.
- **Amended in implementation (PM, 2026-11-19):** the rule generator also made 2020-07-02 and 2026-07-02 13:00 early closes. With July 4 on a Saturday, NYSE closes Friday the 3rd and does not shorten Thursday; NYSE's schedule and production minutes (closing spike at 16:00, minutes past 17:00) agree. Both become full sessions in the same migration, listed as `retimed`.
- **Gap rows.** `data_gaps` never stores holiday ranges, and gap rows change only when a range is recomputed. After the migration, the cutover recomputes gaps through the existing gap writer over every date whose status changed, for the minute and daily granularities:
  - a date that stops being a session (2025-01-09, the 2027–2028 holidays) loses any gap row that covered it;
  - a date that becomes a session (2021-12-31) gains a gap row if bars are missing.

  No hand-written gap SQL. The dry check lists the gap rows over the changed dates before and after.

### D9 — Session fields only on session responses
Status: decided

- `session_date` and `phase` on every default bar would add about 33 bytes per bar, roughly 20% at the row ceiling (180 records an 11.58 MB ceiling-sized response). So a session request returns a session response model whose bars are `SessionBarRecord`, and a default request returns today's `BarsResponse` unchanged apart from `applied.session`. The OpenAPI schema declares both bodies for the route.

### Technology Choices

- Plain SQL over `minute_ohlcv` with a join to `trading_sessions`, rendered from `eligible_sql` and the eligible rollups' first/last expressions. No new dependency.

### Patterns and Conventions

- `SessionMode`, `SessionPhase` and the ceiling are defined once in `constants.py`. Phase lengths come from `trading_sessions`, never from literals.
- Refusals follow the #23 structured shape. A cancelled statement is the existing `504`.
- Production SQL that needs a date uses `trading_sessions` or `(time AT TIME ZONE ...)::date`, never a bare cast (see Special Considerations).

## Implementation Details

### API Contracts

`GET /api/v1/bars/{symbol}`, new parameter:

| Param | Values | Default | Meaning |
|---|---|---|---|
| `session` | `utc`, `regular`, `extended` | `utc` | `utc`: today's bars (UTC-anchored caggs, every stored minute). `regular`: regular-hours minutes only, bucketed from the open. `extended`: pre, regular and post, each bucketed from its own start. |

In session mode `start` and `end` are session dates. A session bar:

```json
{"timestamp": "2026-11-18T14:30:00Z", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0,
 "session_date": "2026-11-18", "phase": "regular"}
```

Default responses are unchanged apart from `applied.session = "utc"`.

New `422` reasons: `calendar_horizon` (with the bound), `minute_ceiling` (with `limit`, `estimate`) and `session_granularity`.

### Database / Storage Schema

A minute-track migration, the next free id in `MINUTE_MIGRATIONS` when the slice is implemented (read the list, don't trust a number here):

- `ALTER TABLE trading_sessions ADD COLUMN extended_open_utc TIMESTAMPTZ, ADD COLUMN extended_close_utc TIMESTAMPTZ`. Nullable, because calendars without extended hours (CME) leave them null.
- `ALTER TABLE trading_holidays ADD COLUMN extended_close_time TIME`, set on early-close rows.
- Replaces the NYSE and NASDAQ holiday rows with the explicit dated set and raises `holidays_seeded_through` to 2028-12-31.
- Re-populates NYSE and NASDAQ `trading_sessions` rows (open, close and the extended bounds) through `populate_trading_sessions`, as an idempotent upsert. It removes the session row for any date the corrected data closes.

The cutover script runs the dry check first. It lists every date whose status changes (2021-12-31 opens, 2025-01-09 closes, the 2027–2028 holidays close) and the gap rows over those dates, and a test pins the date list. Then it applies the migration and recomputes gaps (D8).

## Integration Points

### Provides to Other Slices

- **trading-engine slice 144:** session-aligned bars, plus the closing comment on #38 naming one curl request that returns rows.
- **mt-ui:** `session=regular` for charts. It regenerates types from `openapi.json` at release.
- **Every adjusted intraday read:** the exchange-local date rule (D5).
- **Gap detection and the daily daemon:** correct NYSE/NASDAQ sessions through 2028.
- **Future work:** a regular-hours daily bar and any later `closing_print` field build on `get_session_bars` and the #78 note.

### Consumes from Other Slices

- **Eligible values (171/173).** An all-marked minute is absent from eligible reads, and an empty bucket has no bar, as everywhere else.
- **`trading_sessions` (144, 221).** A date with no row inside the horizon is a holiday and returns no bars. A date past the horizon is a `422` (D6).

## Success Criteria

### Functional Requirements

1. `GET /api/v1/bars/SPY?granularity=1h&session=regular` for a recent full session returns 7 bars starting at 09:30 ET, each with `phase=regular` and that `session_date`.
2. The same request for an early-close session (2025-11-28) returns 4 bars, the last starting 12:30 ET.
3. `session=extended` 1h for a full session returns 6 pre bars (04:00 … 08:00 plus 09:00), 7 regular and 4 post (16:00 … 19:00). No bar crosses 09:30 or the close.
4. Bars anchor at 09:30 ET across the 2025-11-02 and 2026-03-08 DST changes (the sessions either side of each).
5. Each session bar equals the aggregate of the eligible 1m bars it covers: open, high, low, close and volume. This holds for a sample of symbols, sizes and both modes.
6. No session bar includes a minute stamped at or after the session's extended close (the #78 bar).
7. On the evening before a winter Tuesday-to-Friday ex-date (picked from `corporate_actions` during implementation), every adjusted bar of that session uses that session's factor, the last post-market hour included. The same minute gets the same factor in `1m` with `session=utc` and with `session=extended`.
8. With no `session` parameter, the response shape is unchanged apart from `applied.session`. The values are unchanged except the D5 correction.
9. Refusals: `1d` with `session=regular` → `reason=session_granularity`; an over-ceiling session read → `reason=minute_ceiling`; a range past 2028-12-31 → `reason=calendar_horizon`. All three are `422`s.
10. After the migration, 2021-12-31 is a session, 2025-01-09 is not, and 2027 and 2028 holidays are not. Early-close sessions carry a 17:00 ET extended close. Gap rows over the changed dates match a fresh recompute.
11. The #78 note states what the close-stamp bar holds, with the evidence, and both API references describe it and what it means for regular-session volume.

### Technical Requirements

- Unit tests: phase and bucket assignment (full day, early close, DST), the session row and minute estimates, the horizon refusal, and `adjusted()` by exchange-local date.
- Integration tests on a throwaway database with real calendar rows: criteria 1–7, 9 and 10.
- One load test, a one-year `1h` regular read for SPY:
  - it completes at p95 under 2 s, with the 20 s serving timeout as the hard limit;
  - `/api/v1/health` answers at p95 under 0.5 s while it runs.
- `ruff`, format and mypy add no errors over main.
- `openapi.json` regenerated, and `scripts/check_api_docs.py` passes with `session`, the session fields and the three new reasons documented.
- The 140 and 180 amendments listed in Included item 6 are written.

### Integration Requirements

- trading-engine can run the curl request named on #38 and get rows.
- mt-ui's current requests are unaffected (criterion 8).

### Verification Walkthrough

Production, after release and the cutover (the migration touches calendar tables and gap rows only).

1. **Regular hours, one session:**
   `curl -s 'http://127.0.0.1:8100/api/v1/bars/SPY?granularity=1h&session=regular&start=2026-11-18&end=2026-11-18' | jq '.count, [.bars[] | .timestamp]'`
   → `7`, and times from `2026-11-18T14:30:00Z` to `2026-11-18T20:30:00Z` (09:30 to 15:30 ET).
2. **Early close:** the same with `start=end=2025-11-28` → `4`, the last bar at `2025-11-28T17:30:00Z` (12:30 ET).
3. **Extended:** `session=extended` for 2026-11-18 → 17 bars, and `jq '[.bars[] | .phase] | group_by(.) | map({(.[0]): length}) | add'` → `{"post":4,"pre":6,"regular":7}`.
4. **DST:** `session=regular&start=2026-03-06&end=2026-03-09` → the first bar on 03-06 is `14:30Z` and on 03-09 is `13:30Z`. Both are 09:30 ET.
5. **Bar equals its minutes:** fetch `1m&session=regular` for the same session. Sum the 10:30–11:29 ET minutes' volume and compare it with the 10:30 bar's volume. Do the same for its open, high, low and close.
6. **Close-stamp bar excluded:** `1m&session=utc` for 2026-11-18 includes a bar at the extended close instant. `1m&session=extended` does not.
7. **Default unchanged:** `granularity=1h&start=2026-11-18&end=2026-11-18` with no `session` returns the same bars as before the release, with `applied.session = "utc"` and no `session_date`/`phase` keys.
8. **Refusals:** `granularity=1d&session=regular` → `422` with `jq .reason` → `"session_granularity"`. `session=regular&start=2029-01-02&end=2029-01-02` → `"calendar_horizon"`.
9. **Calendar:** `uv run mt data ...` has no session listing today, so read it with psql. On `trading_sessions`, confirm 2021-12-31 is present, and 2025-01-09 and 2027-01-01 are absent. The cutover report shows the gap rows over the changed dates.
10. **Tell trading-engine:** post on #38 the request from step 1, then close the issue.

## Risk Assessment

### Technical Risks

- **Read cost on a shared server.** Session reads scan 1m rows and do not use the caggs.
- **Calendar re-population.** It rewrites session rows that gap detection reads.

### Mitigation Strategies

- Admission on bars and minutes read, a ceiling measured on production against the serving timeout (D6), and the load test with stated bounds.
- The dry check lists every changed date and its gap rows, a test pins the date list, and the upsert is idempotent and safe to re-run. Only the three known defects plus 2027–2028 may change. Gaps are recomputed through the existing gap writer.

## Implementation Notes

### Development Approach

1. **The #78 investigation first.** It's read-only and produces the note. The docs need its answer (D4).
2. The calendar migration and the explicit holiday rows, with the dry check, its pinned list and the gap recompute.
3. `adjusted()` by exchange-local date (criterion 7, and the default-path correction).
4. The session SQL and `get_session_bars`, with unit and integration tests (criteria 1–6).
5. Route, admission and response changes. Measure on production for the minute ceiling's default. Load test.
6. Docs:
   - both API references and `openapi.json`;
   - the 140 and 180 amendments;
   - the #78 note;
   - the CHANGELOG, with the D5 correction called out.

   The closing comment for #38 is written at release.

### Special Considerations

- In session mode `start`/`end` are session dates. Say so in the parameter description, because with `session=utc` they are UTC dates.
- The production database session timezone is America/Denver, so `time::date` shifts dates back a day. Measurements and SQL that need a UTC date must use `(time AT TIME ZONE 'UTC')::date`. Session dates come from `trading_sessions`, never from casting a timestamp.
- The #26 deadline is 2027-01-01. This slice carries that fix, so it should not sit unbuilt into December.
