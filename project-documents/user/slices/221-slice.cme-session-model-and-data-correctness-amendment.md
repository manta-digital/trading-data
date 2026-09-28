---
docType: slice-design
slice: cme-session-model-and-data-correctness-amendment
project: trading
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: []
interfaces: [222-slice.tick-storage-track, 224-slice.ingest-pass-and-proof-parity, 228-slice.active-contract-and-roll-methods, 231-slice.universe-expansion-gc]
dateCreated: 20260928
dateUpdated: 20260928
status: not_started
---

# Slice Design: cme-session-model-and-data-correctness-amendment

## Overview

This slice adds the CME futures session model to the one calendar surface that invariant I4 governs, and writes the frame of the data-correctness contract's tick amendment.

The session model is data, not code. It adds:

- a `CME_EQUITY` row in `trading_calendars`;
- its holidays, early closes and irregular sessions as `trading_holidays` rows;
- its sessions in `trading_sessions`, from 2020-01-01 forward.

The code changes are small:

- one population rule for every calendar: a session whose open time is later than its close time opens on the previous calendar day;
- a single shared extension routine, replacing three copies;
- a session lookup on `TradingCalendar` that answers which session contains a timestamp, and fails explicitly outside the populated range.

The contract amendment adds:

- the tick invariants;
- the I7 exception for tick, with its reason;
- the vocabulary rules for tick completeness and `granularity = 'tick'`;
- the record that sequence-gap detection moves to the realtime initiative.

No purchase is needed and no tick database is touched. Everything lands on the production database's minute migration track, the carve-out the architecture names ("Session model").

## Value

- **Unblocks ingest (224).** Every tick gets its session from this lookup. The ingest ledger's grain is (instrument, session, unit), and the session-boundary check needs session bounds. Without this slice, 224 has nothing to assign against.
- **Proves the session model on real trades before any tick table exists.** The two ES batch jobs already on disk are free-credit purchases, and they cover Labor Day 2024, Thanksgiving, Black Friday, Christmas Eve and Christmas. A verification script runs every trade in them through the new lookup. A wrong boundary shows up now, as a named session, not later as failed ingest units.
- **Makes the calendar horizon honest.** A calendar's sessions can no longer be extended past the last date its holidays are known. A session table without holiday rows reports every holiday as a trading day. That defect already exists for NYSE in 2027–2028; see Special Considerations. This slice prevents it for CME and makes the limit visible for every calendar.
- **Brings the governing contract in line with the initiative.** The contract currently promises sequence-gap detection and a cross-vendor audit for tick, and this initiative delivers neither. It also has no tick invariants. After this slice, it says what tick guarantees and where each guarantee will be closed.

## Technical Scope

**Included**

1. **Holiday-coverage bound.** A new column, `trading_calendars.holidays_seeded_through DATE NOT NULL`, stored per calendar. `NYSE` and `NASDAQ` get `2026-12-31`, the true end of what migrations 007 and 008 seeded.
2. **Open-after-close population rule** in one shared helper, used by both `populate_trading_sessions` and `TradingCalendar._build_trading_hours`, so every session path applies it.
3. **One shared extension routine.** `extend_calendar_sessions` replaces the three copies that exist today: `mt data extend`, `maybe_extend_trading_sessions`, and the new seed migration's population. It never populates past a calendar's `holidays_seeded_through`.
4. **The `CME_EQUITY` calendar.** Its row, its exception rows from 2020-01-01 through the last year CME has published, and its sessions over the same range, in a minute-track migration.
5. **Session lookup on `TradingCalendar`:** `session_containing(ts)` and `sessions_between(start, end)`, both backed by a pure `SessionIndex` that also offers vectorized assignment for 224.
6. **Product → calendar mapping** for the tick package, as one constant. `ES` maps to `CME_EQUITY`.
7. **Operator surface.** A new `mt data calendars sessions` debug read (I8). `mt data calendars list` is fixed; it queries columns that do not exist today. Both `calendars list` and `mt data extend` now show the holiday bound.
8. **Verification script** `scripts/verify_cme_sessions.py`. It runs every record in local DBN files through the lookup and reports trades outside any session, plus first and last trade per session.
9. **Contract amendment frame** in `user/reference/data-correctness-architecture.md`.
10. **Slice-plan and architecture follow-ups** found at design (Integration Points).

**Excluded**

- The `CME_METALS` calendar for GC. GC's holiday schedule differs from ES's (Technical Decisions, D1), so GC needs its own calendar row. It is seeded by 231, where GC data is acquired and the calendar can be checked against real trades.
- Fixing NYSE and NASDAQ holiday data. This slice changes no NYSE or NASDAQ `trading_sessions` rows. The defects found at design go to a GitHub issue (Special Considerations).
- Any tick table, ingest code, or ledger. The vectorized assignment is provided here; 224 uses it.
- Changing migration 026's callable. It is applied history, and it runs before the new column exists.
- An API surface. See "Does this belong in the API?" below.

## Dependencies

### Prerequisites

- None within the 220 plan. 923 (multi-database plumbing) is complete. The migration lands on the minute track, which 923 routes to the production database.
- Slice 220 (complete, v0.18.0) supplies the DBN file reader (`ITickFileReader`, `ITickFile.iter_batches`) that the verification script uses.
- The two free-credit ES jobs on disk under `/data/market-data/databento`:
  - `GLBX-20240930-USM7UXXJBA`: `trades`, 2024-08-30 → 09-29;
  - `GLBX-20250123-XT4GD5UM6C`: `tbbo`, 2024-11-01 → 12-31, as a zip.
  They are read locally. Nothing is bought.

### Interfaces Required

- The existing tables `trading_calendars`, `trading_holidays` and `trading_sessions` (migrations 004, 005 and 025/026). `trading_sessions` is keyed `(calendar_id, session_date)` with UTC open and close timestamps, and needs no schema change.
- `TRADING_SESSIONS_EXTENSION_YEARS` and `TRADING_SESSIONS_HORIZON_WARN_DAYS` in `constants.py`, unchanged.
- The minute migration track in `market/schema/migrations/minute.py`. The chain currently ends at `056_data_status_open_gaps_and_walk_anchor`.

## Architecture

### Component Structure

| Component | Location | Change |
|---|---|---|
| Session interval rule | `data/base/session_population.py` | New `session_interval(session_date, open_t, close_t, tz) -> (open_utc, close_utc)`. It holds the open-after-close rule, and `populate_trading_sessions` calls it. |
| Shared extension | `data/base/session_extension.py` (new) | `extend_calendar_sessions(conn, calendar_id, *, start, end) -> CalendarExtension`. It reads the calendar row and its holidays, clamps `end` to `holidays_seeded_through`, calls `populate_trading_sessions`, and upserts. |
| Session index | `data/base/session_index.py` (new) | `Session` dataclass and `SessionIndex`, a pure lookup over sorted sessions. It has scalar and NumPy vectorized forms. |
| `TradingCalendar` | `data/base/trading_calendar.py` | Adds `session_containing`, `sessions_between`, and `OutOfPopulatedRangeError`. `_build_trading_hours` uses `session_interval` on its ETH and ALL paths. |
| CME seed | `market/schema/seed_cme_calendar.py` (new) | `CME_EQUITY` calendar metadata and an explicit, dated exception table with a source per row. |
| Migrations | `market/schema/migrations/minute.py` | `057_calendar_holidays_seeded_through` and `058_seed_cme_equity_calendar`. |
| Callers moved to the shared extension | `cli/commands/data.py` (`data_extend`) and `data/maintenance/auto_extend.py` | Their inline populate-and-upsert loops are replaced by calls to `extend_calendar_sessions`. |
| Product → calendar | `data/tick/constants.py` | `CME_EQUITY_CALENDAR_ID` and `FUTURES_PRODUCT_CALENDAR = {"ES": CME_EQUITY_CALENDAR_ID}`. |
| Calendar CLI | `cli/commands/calendar_sessions.py` (new), registered on `calendars_app` | `mt data calendars sessions`. `calendars list` is fixed in place. |
| Verification script | `scripts/verify_cme_sessions.py` (new) | Runs DBN records through `SessionIndex`. |
| Contract | `user/reference/data-correctness-architecture.md` | The amendment frame (D10). |

The new CLI command goes in its own module, following the `minute_session_mass.py` precedent, so `data.py` gains only one registration line. That file is already 4,006 lines, and issue #25 tracks splitting it.

### Data Flow

**Seed (migration 058, once):**

```
seed_cme_calendar.CME_EQUITY_CALENDAR ──INSERT──▶ trading_calendars (holidays_seeded_through = table end)
seed_cme_calendar.CME_EQUITY_EXCEPTIONS ─INSERT─▶ trading_holidays (calendar_id = 'CME_EQUITY')
extend_calendar_sessions(conn, 'CME_EQUITY', start=2020-01-01,
                         end=Dec 31 of current_year + TRADING_SESSIONS_EXTENSION_YEARS)
   └─ end clamped to holidays_seeded_through ──UPSERT──▶ trading_sessions
```

**Forward extension (every existing trigger):** `mt data extend [--calendar X]`, `mt data status` (auto-extend), the daemon idle hook, and, from 224, the ingest pass. Each one calls `extend_calendar_sessions` per calendar with `start = MAX(session_date) + 1`. The routine writes nothing past the holiday bound and reports when the bound is what stopped it.

**Lookup (224 and the verification script):**

```
TradingCalendar('CME_EQUITY').sessions_between(unit_start_utc, unit_end_utc)
   └─ one SELECT over trading_sessions ──▶ SessionIndex
SessionIndex.locate_ns(ts_event: np.ndarray[int64 ns]) ──▶ np.ndarray[int] (session position, -1 = in no session)
```

`session_containing(ts)` is the scalar form. It raises when `ts` is outside the populated range, returns `None` for a break or closed day inside that range, and otherwise returns the `Session`.

### State Management

All state is rows in the three calendar tables on the production database. The only new persistent state is the column `holidays_seeded_through`. `TradingCalendar` keeps its existing per-instance cache. A `SessionIndex` is immutable, built per call to `sessions_between`, and never cached across units.

## Technical Decisions

### Technology Choices

**D1 — One calendar per distinct CME schedule, and ES and GC do not share one.** The architecture expected ES and GC to share a row and asked for verification per product. They do not share one. Regular hours are the same: Sunday–Friday, 17:00–16:00 America/Chicago. Holiday handling differs:

- On US holidays, equity index futures halt at 12:00 CT, while metals and energy halt at 13:30 CT (from 2022).
- The day after Thanksgiving closes at 12:15 CT for equities and 12:45 CT for metals.
- In some years equities trade an abbreviated Good Friday session that ends at 08:15 CT, while metals stay closed.

These differences come from the CME holiday calendar and are corroborated by `pandas_market_calendars` (its `CMEGlobexEquitiesExchangeCalendar` and `CMEGlobexEnergyAndMetalsExchangeCalendar`, which cite cmegroup.com). The calendar row is therefore `CME_EQUITY`, named for the schedule rather than the exchange. GC gets `CME_METALS` in 231.

**D2 — Calendar row values.**

- `calendar_id`: `CME_EQUITY`
- `exchange_name`: `CME Globex Equity Index`
- `timezone`: `America/Chicago`
- `market_open`: `17:00`
- `market_close`: `16:00`
- `has_extended_hours`: `FALSE`; the extended columns are `NULL`

For futures, the Globex session is the whole session. It has no RTH/ETH split, so `get_trading_hours(ETH)` returns `None`, as it does for any calendar without extended hours.

A calendar row holds one open and close with no history of changes. The seed range must therefore fall inside one regular-hours regime. The 17:00–16:00 CT hours must be confirmed over 2020-onward while compiling the seed. A change inside the range stops the work and goes to the PM. It is not modelled here.

The daily 15:15–15:30 CT equity halt that CME removed in 2021 is a gap inside a session. It needs no modelling: the session-boundary check asserts only that trades fall inside sessions, and a halt contains no trades.

**D3 — The open-after-close rule lives in one function.** `session_interval(session_date, open_t, close_t, tz)` combines `close_t` with `session_date`. It combines `open_t` with `session_date` when `open_t < close_t`, and with `session_date − 1 day` when `open_t > close_t`. It raises `ValueError` when they are equal. It runs after the holiday overrides are applied, so a CME early close at 12:00 still opens the previous evening at 17:00. Both conversions happen in the calendar's time zone before UTC, so a DST switch between the two calendar days is handled by `zoneinfo`.

`populate_trading_sessions` keeps its Monday–Friday loop. The architecture notes that this loop is already right for CME, because the Sunday-evening open belongs to Monday's session.

NYSE and NASDAQ open before they close, so their output is byte-identical. A regression test proves it (Success Criteria).

`_build_trading_hours` currently combines the open and close for ETH and ALL with the same date. Routing those paths through `session_interval` removes the last place a calendar could get an inverted session.

**D4 — A session is a contiguous open-to-close interval, identified by the date it closes.** This is the architecture's definition, applied without exception. It means one deliberate departure from CME clearing: on a holiday with an abbreviated session, for example MLK Day, CME clears the Sunday-evening-to-noon trading into Tuesday's trade date. Here that interval is its own session, dated the holiday, with an `early_close` row. Tuesday's session opens Monday 17:00.

The reason is that every consumer in this initiative needs contiguous intervals:

- the session-boundary check;
- the ingest ledger's per-session record counts;
- the day-condition edge rule, where a session touches two calendar days.

A two-part session would bring back the intra-session halt the architecture ruled out. No 220 slice needs CME's clearing trade date. If 228's volume-based roll rule ever needs volume per clearing date, it folds a holiday session into the next session at read time. That note goes into the contract vocabulary so 228 inherits it.

**D5 — The CME seed is an explicit dated table, not a rule generator.** `seed_cme_calendar.CME_EQUITY_EXCEPTIONS` lists every exception date from 2020-01-01 through `CME_EQUITY_HOLIDAYS_SEEDED_THROUGH`. Each row carries its `market_status` (`closed` or `early_close`, plus `late_open` if one ever occurs), its time, and a source comment.

The rule-generator approach used for NYSE is rejected for two reasons, both found at design:

- CME's holiday handling changed regime several times inside the seed range (2021, 2022 and later), and ad hoc closures such as national days of mourning cannot be derived from a rule.
- The existing NYSE generator is wrong on real dates. It closes 2021-12-31, a Friday on which NYSE traded, by shifting a Saturday New Year's Day onto it. It also has no row for the 2025-01-09 closure.

The table is compiled during implementation from:

1. **CME's holiday calendar** (cmegroup.com/tools-information/holiday-calendar.html) for the current and next year;
2. **CME's holiday trading-hours notices** for past years;
3. a **one-off cross-check** against `pandas_market_calendars`' CME Globex Equity calendar, run from the scratchpad with `uv run --with pandas_market_calendars`. It is never a project dependency, because a second session implementation in the tree would itself breach I4. Each difference is resolved from a CME notice and recorded in this document's Implementation Findings.

A past year that cannot be sourced from CME is reported to the PM, not guessed.

The seed starts on 2020-01-01. That matches the equities seed and covers the adopted files, the plan's included year, and the COVID-era purchase range the architecture mentions. A range before 2020 is a later migration that adds rows.

**D6 — The holiday bound is enforced by the extension.** `holidays_seeded_through` means that the calendar's exceptions are complete through this date. `extend_calendar_sessions` clamps its `end` to that date and returns `clamped_by_holiday_bound = True` when it did.

Three surfaces report the bound when it is what limits the horizon:

- `mt data extend`;
- the auto-extend summary in `mt data status`;
- `--strict`, which already exits 4 when a horizon is under 90 days away.

The report reads, for example, `CME_EQUITY: horizon 2027-12-31 — holidays seeded through 2027-12-31; seed the next year's CME schedule`. That turns "CME's next year is not yet seeded" into a loud warning 90 days ahead, instead of silently wrong sessions.

`TradingCalendar`'s horizon stays `MAX(session_date)`. The extension never writes past the bound, so for CME the two agree. For NYSE, whose existing rows run to 2028, nothing about lookups changes in this slice.

The migration sets the bound for every calendar explicitly and then applies `SET NOT NULL`. A calendar the migration does not know fails the migration loudly. It never gets a guessed value.

**D7 — Lookup semantics.**

- `Session(calendar_id, session_date, open_utc, close_utc)` is frozen, and the interval is closed: `open_utc ≤ ts ≤ close_utc`. Sessions never touch, because a break is at least one hour and holiday gaps are longer, so a closed interval is lenient without being ambiguous.
- `sessions_between(start_utc, end_utc)` returns every session that intersects `[start_utc, end_utc)`. It raises `OutOfPopulatedRangeError(calendar_id, ts, first_open, last_close)` when `start_utc` is before the first populated open, or when `end_utc` is after the last populated close. The populated range is 2020-01-01 17:00 CT for CME.
- `session_containing(ts)` raises the same error outside the populated range, returns `None` in a break or on a closed day, and otherwise returns the `Session`.
- A naive datetime raises `ValueError`.
- `None` is a real answer ("no session"), not a fallback. 224's session-boundary check turns it into a unit failure.
- `SessionIndex.locate_ns` uses `np.searchsorted` over the open times, then compares against the close times, with `-1` meaning "in no session". Its callers handle out-of-range timestamps before assignment. `sessions_between` has already raised for any unit range outside the populated span.

**D8 — Product → calendar mapping is one constant.** `FUTURES_PRODUCT_CALENDAR` in `data/tick/constants.py` is the only place a futures product meets a calendar id. A product with no entry raises `KeyError` naming the product; there is no default. 223's universe configuration and 224's ingest read it. 231 adds `"GC": CME_METALS_CALENDAR_ID` there. Calendar ids stay strings because they are database keys, as `NYSE` is today (`HEALTH_MINUTE_SESSION_CALENDAR`). Each id is defined once as a constant.

**D9 — Does this belong in the API? No.** The API does not serve calendars today, and the session lookup is an internal input to ingest. The futures namespace (230) exposes sessions where a client meets them, in tick coverage and status per session. The operator need is served by the `mt data calendars sessions` debug read (I8).

**D10 — The contract amendment frame.** These edits go to `user/reference/data-correctness-architecture.md`. The contract numbers the initiative 200, the concept's old number, so each edited passage renames it to 220 and records the renumbering once.

- **Vocabulary**, added terms:
  - *Session (futures)*: a contiguous open-to-close interval in a CME calendar, identified by `(calendar_id, session_date)` where `session_date` is the date it closes. It is not CME's clearing trade date (D4).
  - *Archive unit*: one purchased or adopted provider file, tracked in the manifest.
  - *Ingest ledger*: one row per (instrument, session, unit).
  - *Instrument-session complete*: the architecture's completeness definitions, restated.
- **Vocabulary rules for tick.**
  - Tick completeness is answered from the manifest, the ingest ledger and raw-table counts. It never reads `acquisition_state`, `data_gaps` or `data_status`, and never any aggregate.
  - `Granularity.TICK` (in `data/acquisition/state.py`) names a granularity. `PassKind.TICK` (deferred with the cadence decision, 225) names a run.
  - `granularity = 'tick'` enters a minute-track enumeration (`data_gaps`, `data_status`) only where a surface reads it. The contract keeps that list, and it is empty as of 221.
- **New invariants.** Each names its planned closing slices in the mapping table, with status "Framed (221)":
  - **I11 — Tick completeness from the manifest.** Completeness per archive unit, instrument-session and universe, under the architecture's definitions, answered only from the manifest, the ledger and raw counts. Closing: 222 and 224.
  - **I12 — Tick provenance and supersession.** Every tick row carries its archive unit's id. Replacing data is an explicit supersession recorded in the manifest, never a natural-key conflict deciding between two units. Closing: 222 and 224.
  - **I13 — Session-assigned ticks.** Every ingested tick falls inside a session of its product's calendar. A tick in a break, on a closed day, or outside the populated range fails its unit. Closing: 221 (the model and lookup) and 224 (the check).
  - **I14 — Futures identity is explicit.** Every surface that answers for a product names the contract and the roll rule that chose it. No continuous or back-adjusted series is stored. Closing: 228, 229 and 230.
- **I7 exception.** I7 does not apply to tick in initiative 220. The scope has one provider and no cross-venue consolidation, and no independent price source is bought. The ingest checks (counts, resolution, session boundaries) are *verification* in this contract's vocabulary, not audit. A future second tick source reopens it.
- **Sequence-gap detection** moves out of this initiative's inherited list and is reserved for the realtime initiative. The reason: within a purchased historical range, the provider's delivery is complete by contract, so the completeness question is which sessions were purchased and ingested, not which sequence numbers are missing. The Notes paragraph that promised it is rewritten to say so.
- **Mapping table.**
  - The I4 row gains "221: `CME_EQUITY` calendar and session lookup behind the same surface; verification: tests + `mt data calendars sessions`".
  - The I7 row gains the tick exception.
  - Rows are added for I11–I14.
- **Required CLI commands.** The tick line is updated to 220's decided vocabulary (the I10 row already records it).

### Patterns and Conventions

- Migration 058 follows 026's form: a Python callable run by the migration runner on the migration connection. Its SQL inserts use the seed module's own INSERT builder. `generate_calendar_insert_sql` is not changed, because migrations 007 and 008 render through it at import time, and adding the new column to its output would break a cold start before 057 runs.
- Seed upserts use `ON CONFLICT DO NOTHING` for calendar and holiday rows, and `ON CONFLICT (calendar_id, session_date) DO UPDATE` for sessions, matching existing practice.
- Every error names the calendar and the offending date or timestamp, with the command that fixes it where one exists (`mt data extend`, or "seed the next year's schedule").
- **Realtime paths check.** Nothing here forecloses path A (history assembled from realtime capture) or path B (historical API with a lag).
  - The model is delivery-agnostic: a session is a session whether its ticks arrive from a batch file or a live feed.
  - Realtime capture past the seeded year fails session assignment loudly until the next year is seeded. That is the intended behaviour, and the 90-day horizon warning gives it notice.

## Implementation Details

### Database / Storage Schema

**`057_calendar_holidays_seeded_through`** (minute track)

```sql
ALTER TABLE trading_calendars ADD COLUMN IF NOT EXISTS holidays_seeded_through DATE;
UPDATE trading_calendars SET holidays_seeded_through = DATE '2026-12-31'
 WHERE calendar_id IN ('NYSE', 'NASDAQ') AND holidays_seeded_through IS NULL;
ALTER TABLE trading_calendars ALTER COLUMN holidays_seeded_through SET NOT NULL;
```

The date literal comes from the end year that migrations 007 and 008 seeded (`_SEED_END_YEAR`). It is rendered from that constant, not typed a second time.

**`058_seed_cme_equity_calendar`** (minute track, Python callable)

1. INSERT the `CME_EQUITY` row, with `holidays_seeded_through = CME_EQUITY_HOLIDAYS_SEEDED_THROUGH`.
2. INSERT every `CME_EQUITY_EXCEPTIONS` row into `trading_holidays`.
3. Call `extend_calendar_sessions(conn, CME_EQUITY_CALENDAR_ID, start=CME_EQUITY_SEED_START, end=Dec 31 of current_year + TRADING_SESSIONS_EXTENSION_YEARS)`.

The migration does not touch the `NYSE` or `NASDAQ` rows. The unit test that asserts the chain ends at 056 moves to 058.

### API Contracts

Internal Python interfaces, which 224 consumes:

```python
@dataclass(frozen=True)
class Session:
    calendar_id: str
    session_date: date
    open_utc: datetime   # tz-aware UTC
    close_utc: datetime  # tz-aware UTC

class TradingCalendar:
    def sessions_between(self, start_utc: datetime, end_utc: datetime) -> list[Session]: ...
    def session_containing(self, ts: datetime) -> Session | None: ...

class SessionIndex:
    def __init__(self, sessions: Sequence[Session]) -> None: ...     # sorted, non-overlapping; raises otherwise
    def locate(self, ts: datetime) -> Session | None: ...
    def locate_ns(self, ts_ns: np.ndarray) -> np.ndarray: ...         # int64 ns UTC → position, -1 = no session

def extend_calendar_sessions(conn, calendar_id: str, *, start: date, end: date) -> CalendarExtension: ...
# CalendarExtension: calendar_id, rows_upserted, horizon_after, holidays_seeded_through, clamped_by_holiday_bound
```

**CLI**

- `mt data calendars sessions --calendar CME_EQUITY --from 2024-11-27 --to 2024-12-02 [--json]` prints one line per session: session date, open and close in the calendar's time zone and in UTC, duration, and the exception name if one applies.
- `mt data calendars list` is fixed. It selects `exchange_name` and `market_open`/`market_close` under the aliases `TradingCalendar._ensure_loaded` already uses, and it gains a `holidays seeded through` column.
- `mt data extend` gains the bound in its per-calendar line.

## Integration Points

### Provides to Other Slices

- **224 (ingest):** `TradingCalendar.sessions_between`, `SessionIndex.locate_ns`, `extend_calendar_sessions` (224 runs it for `CME_EQUITY` when a unit's range passes the horizon), `OutOfPopulatedRangeError`, and `FUTURES_PRODUCT_CALENDAR`. The ledger's session key is `(calendar_id, session_date)`.
- **222 (storage):** a session is identified by `(calendar_id, session_date)`. With two calendars in play from 231 on, the ledger must carry `calendar_id` or derive it from the product. 222's design decides which.
- **228 (roll methods):** the D4 note that a holiday session is dated by the day it closes, not by CME's clearing trade date.
- **231 (GC):** the pattern for a second futures calendar: a seed module table, a minute-track migration, one mapping entry, and verification against GC files.
- **All later 220 slices:** the contract amendment frame, which each slice extends with its own mapping rows.

### Consumes from Other Slices

- **220:** the DBN reader, used only by the verification script. If a file fails to decode, the script fails loudly.
- **923:** the minute track's routing to the production database. Nothing else.

### Follow-ups written during this phase

- **Slice plan, entry 231:** add "seeds the `CME_METALS` calendar (221 found GC's holiday schedule differs from ES's) and adds `GC` to `FUTURES_PRODUCT_CALENDAR`".
- **Slice plan, "Architecture statements superseded":** add that ES and GC do not share a calendar row.
- **GitHub issue #26:** NYSE and NASDAQ holiday data (Special Considerations).

## Success Criteria

### Functional Requirements

1. After `mt data migrate apply`, `trading_calendars` has a `CME_EQUITY` row with the D2 values and a non-null `holidays_seeded_through`. `NYSE` and `NASDAQ` carry `2026-12-31`.
2. `trading_sessions` for `CME_EQUITY` covers every weekday from 2020-01-02 through `holidays_seeded_through`, minus `closed` exceptions. Every session opens 17:00 CT on the previous calendar day, except where a `late_open` exception says otherwise, and closes at 16:00 CT or at its exception's early-close time.
3. Checked on real dates, verified against the adopted files:
   - Labor Day 2024-09-02 is a session that opens 2024-09-01 17:00 CT and closes at the holiday halt.
   - 2024-09-03 opens 2024-09-02 17:00 CT.
   - 2024-12-25 has no session.
   - 2024-12-26 opens 2024-12-25 17:00 CT.
4. `NYSE` and `NASDAQ` `trading_sessions` rows are byte-identical before and after the migrations and after a full `mt data extend`.
5. `session_containing` returns the containing session for a timestamp inside one. It returns `None` for a timestamp in the daily break and on a closed day. It raises `OutOfPopulatedRangeError` before the first populated open and after the last populated close. It raises `ValueError` for a naive datetime.
6. `extend_calendar_sessions` never writes a session dated after `holidays_seeded_through`. When the bound limits the horizon, `mt data extend` and the `mt data status` auto-extend line say so and name the fix. `--strict` exits 4 when the bound is under 90 days away.
7. `mt data calendars list` runs without error and shows `CME_EQUITY`, `NYSE` and `NASDAQ` with their bounds.
8. `scripts/verify_cme_sessions.py` over both adopted jobs reports **zero records outside a session**, and lists first and last trade per session. The first and last trades at Labor Day, Thanksgiving, Black Friday and Christmas Eve are recorded in Implementation Findings as evidence that the early closes are right.

### Technical Requirements

- **Unit tests:**
  - `session_interval`: normal, inverted, equal (raises), and DST weekends in March and November for CME, plus the NYSE identity.
  - `populate_trading_sessions` for CME over a range with each exception kind.
  - `SessionIndex`: boundaries at open and at close, break, closed day, the vectorized form matching the scalar form, and unsorted or overlapping input raising.
  - The seed table: every date is a weekday, dates are unique, every row is on or before the bound, every early close is before 16:00, and every row has a source comment (a test reads the module source).
  - The extension clamp.
  - The `calendars list` query against the real column names.
- **The NYSE regression test**, run in the integration tier against a throwaway database: populate NYSE over 2020–2028 with the old function (a frozen copy of the pre-change output is held as a fixture file) and with the new one, and require equality.
- **Boundary fixture from real data:** a small JSON of real `ts_event` values (first and last trade of the sessions in criterion 3), extracted from the adopted files with provenance noted. It drives a unit test of `SessionIndex` against the seeded sessions, so the real format is in the test suite without committing large DBN files.
- **Integration test for migrations 057 and 058** on a throwaway database: the column, the CME rows, the session count, and a re-run as a no-op.
- `ruff` and `mypy` clean on touched files. Run mypy with src and tests in one invocation.

### Integration Requirements

- 224 can assign sessions to every record in a unit with one `sessions_between` call and one `locate_ns` call, and needs no other calendar code.
- The contract document carries:
  - the tick invariants (I11–I14);
  - the I7 tick exception with its reason;
  - the vocabulary rules, including the list of minute-track enumerations where `granularity = 'tick'` appears, which is empty as of 221;
  - the sequence-gap move;
  - a 221 entry in the I4 row, and rows for I11–I14 naming their planned closing slices.

### Verification Walkthrough

Run against the dev database after `mt data migrate apply`. Commands marked *new* do not exist before this slice.

1. **The calendar exists, with its bound.**
   ```bash
   mt data calendars list
   ```
   Expected: three rows. `CME_EQUITY` shows America/Chicago, 17:00–16:00, and its holidays-seeded-through date. NYSE and NASDAQ show `2026-12-31`. This command fails before this slice because of a column-name error.

2. **Sessions around Thanksgiving and Christmas 2024** (*new*).
   ```bash
   mt data calendars sessions --calendar CME_EQUITY --from 2024-11-27 --to 2024-12-02
   mt data calendars sessions --calendar CME_EQUITY --from 2024-12-23 --to 2024-12-27
   ```
   Expected:
   - 11-28 opens 11-27 17:00 CT and closes at the Thanksgiving halt.
   - 11-29 opens 11-28 17:00 CT and closes at the Black Friday early close.
   - 12-02 opens Sunday 12-01 17:00 CT.
   - There is no 12-25 row, and 12-26 opens 12-25 17:00 CT.

3. **Holiday exceptions.**
   ```bash
   mt data calendars holidays --calendar CME_EQUITY --year 2024
   ```
   Expected: the 2024 rows from the seed table, each with its status and time.

4. **The lookup on real trades** (*new*). Unzip the `tbbo` job to the scratchpad first.
   ```bash
   uv run python scripts/verify_cme_sessions.py --calendar CME_EQUITY \
       /data/market-data/databento/GLBX-20240930-USM7UXXJBA <scratch>/GLBX-20250123-XT4GD5UM6C
   ```
   Expected: record totals per job matching each job's `manifest.json` record count, `outside any session: 0`, and a per-session table of first and last trade against open and close. Exit code 0. Any record outside a session prints its timestamp and the nearest session, and exits 1.

5. **The holiday bound in action.**
   ```bash
   mt data extend --calendar CME_EQUITY
   mt data extend --calendar CME_EQUITY --strict; echo "exit=$?"
   ```
   Expected: 0 rows upserted, and a line naming the horizon and the holiday bound. `--strict` exits 0 while the bound is more than 90 days away. To see exit 4, use a throwaway database with the bound set 30 days out; the integration test does this.

6. **NYSE unchanged.** Run `pytest test/integration -k nyse_sessions_unchanged`. Expected: pass.

7. **The contract.** Open `user/reference/data-correctness-architecture.md`. I11–I14 are present, I7 names the tick exception, the Notes no longer promise sequence-gap detection for tick, and the mapping table has the 221 entries.

## Risk Assessment

### Technical Risks

- **Historical CME exceptions may be incomplete.** CME's live page covers only the current and next year, and past years are spread across per-holiday notices. A missed irregular session in, say, 2021 would put trades there outside any session, or leave a false session.
- **The proof data covers only 2024.** The verification script can check only the adopted ranges.

### Mitigation Strategies

- The dated table plus the one-off cross-check against an independent derivation catches omissions on either side, and every disagreement is resolved from a CME notice and recorded.
- The ingest session-boundary check (224) re-runs the same test on every unit ever ingested, so a wrong past-year row surfaces as a named failed unit, never as silently misattributed data. The fix is an additive migration plus re-ingest of the affected units.
- A year with no CME source goes to the PM before the table claims it.

## Implementation Notes

### Development Approach

1. `session_interval` and the D3 rule, with its unit tests. Capture the NYSE pre-change fixture **before** changing any code.
2. `SessionIndex` and its tests.
3. Migration 057 and `extend_calendar_sessions`. Move `mt data extend` and auto-extend onto the shared routine, and delete their inline loops. Add the bound reporting.
4. Compile the CME exception table (D5 sources, cross-check, findings recorded) and write `seed_cme_calendar.py` and its tests.
5. Migration 058, and update the chain-end unit test.
6. `TradingCalendar.sessions_between` and `session_containing`, the ETH/ALL path through `session_interval`, and the product mapping constant.
7. CLI: fix `calendars list`, then add `calendars sessions`.
8. `verify_cme_sessions.py`, run over both jobs, the real-data boundary fixture, and the findings recorded.
9. The contract amendment. (The slice-plan follow-ups and issue #26 were done at design.)

### Special Considerations

- **NYSE and NASDAQ holiday data is wrong today (found at design; filed as issue #26, not fixed here).**
  - Holidays are seeded only for 2020–2026, but sessions were extended through 2028. From 2027-01-01, every NYSE holiday is a trading day in `trading_sessions`.
  - Inside 2020–2026, the generator closes 2021-12-31, a day NYSE traded.
  - The generator has no row for the 2025-01-09 closure.
  - Gap detection reads these rows, so minute and daily coverage are affected.
  - This slice leaves NYSE rows unchanged, as the plan requires, and stores the true bound (2026-12-31) so the defect is visible in `mt data calendars list`. The fix, explicit dated rows for NYSE and NASDAQ and a re-population, belongs to its own change before 2027-01-01.
- **`session_date` for a holiday session differs from CME's clearing trade date (D4).** This is intentional. It is stated in the contract vocabulary so no later slice treats `session_date` as a settlement date.
- **The production migration is one small insert** (roughly 250 session rows per seeded year). It needs no special cutover. `scripts/upgrade_production.py` blocks on newly pending minute-track migrations, so this release goes out through the normal migration path, not the code-only upgrade.
