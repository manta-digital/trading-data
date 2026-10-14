---
docType: slice-design
slice: universe-expansion-gc
project: trading-data
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [231-slice.api-surface-coverage-futures-tick, 226-slice.proof-on-existing-data, 221-slice.cme-session-model-and-data-correctness-amendment, 229-slice.active-contract-and-roll-methods]
interfaces: [234-slice.tick-pass-production-wiring]
dateCreated: 20261014
dateUpdated: 20261014
status: not_started
---

# Slice Design: Universe Expansion — GC

## Overview

This slice adds COMEX gold (GC) as the second futures product. It makes three changes:

- It adds the `CME_METALS` trading calendar: a dated exception table and a minute-track migration, following 221's `CME_EQUITY` pattern.
- It adds one entry to `FUTURES_PRODUCT_CALENDAR`.
- It adds one entry to `TICK_UNIVERSE`.

GC data for 2024-11-02 → 2025-01-01 is then bought at tbbo through 224's manual pass and loaded through 225's ingest. That range contains GC's December → February roll. The session model, the ingest checks and the roll rules are verified on real GC data, and status, overview and the API show GC without any surface code changing.

The 226 go/no-go set the inputs: "GC: follow at tbbo … Slice 232 still decides GC's spreads and calendar" (`user/analysis/226-analysis.tick-proof-go-no-go.md`). 229 TD6 handed over the open-interest question, because GC is the product where open interest and volume disagree.

## Value

- **Research:** a second, differently behaved product (bimonthly active months, serial months, a different holiday schedule) through the same tables, surfaces and roll rules. ES and GC are held over the same sessions, so cross-product reads line up.
- **Architectural:** it proves the claim the whole initiative was built on, that expanding the universe is a configuration change gated on measurements. Product code changes only in the calendar seed and two constants. If any surface needs more than that, a product assumption has leaked, and this slice is where it shows.
- **Operator:** a pattern for adding a futures product on a new CME schedule, written into the runbook.

## Technical Scope

**Included**

- `CME_METALS` calendar: the calendar row, every exception from `CME_METALS_SEED_START` (2020-01-01 unless a year cannot be sourced, TD2) through 2027-12-31 with its CME source, and sessions populated by migration `059_seed_cme_metals_calendar` (minute track, production database).
- The CME seed module made data-driven. The equity and metals tables share one seed shape, one pair of insert builders and one migration callable, so 058 and 059 do not duplicate code (TD3).
- `"GC": CME_METALS_CALENDAR_ID` in `FUTURES_PRODUCT_CALENDAR`.
- A `TICK_UNIVERSE` entry for GC: `GC.FUT`, parent symbology, spreads included, tier tbbo, 2024-11-02 → 2025-01-01.
- One purchase of that range through the existing manual pass, within ceilings the PM sets for the run (TD6), then ingest.
- Session-model verification for GC on the purchased data (TD7).
- Roll verification for GC: 229's provider-equivalence test extended to GC (TD8).
- The open-interest revisit: a recorded decision (TD9).
- Test updates where a fixture or assertion assumed ES is the only product (TD10).
- Documentation: the data-correctness contract's mapping rows, a runbook entry for adding a product, the slice plan's Future Work item 2, and an entry in the architecture's Revision Log. The entry records the separate calendars that supersede the "Session model" statement that one row serves ES and GC, the 232 open-interest decision against "Active contract and roll methods", and GC's measured spread share.

**Excluded**

- Any change to the pass, ingest, status, overview, `get`, `active` or API code. If one turns out to be needed, it is a finding reported to the PM, not quiet scope growth.
- Schedule code, timers and `PassKind.TICK` (234, deferred until the cadence decision).
- Open-interest data (`statistics`) and the `.n` rule (TD9).
- The Standard plan subscription and the plan year's wider range. When the plan is subscribed, widening GC's range is the same one edit as ES's.
- Other products. Each one gets its own calendar decision.

## Dependencies

### Prerequisites

- **231 (complete):** the `/api/v1/futures/*` surfaces, read from `TICK_UNIVERSE`.
- **226 go/no-go (complete):** tier tbbo, layout, constants, "GC: follow at tbbo".
- **221 (complete):** `holidays_seeded_through`, `extend_calendar_sessions`, `session_interval`, the open-after-close rule, and the seed pattern.
- **229 (complete):** roll rules, `VOLUME_RANK_LAG_SESSIONS`, the provider-equivalence test and its fixture recorder.
- **PM spend authorisation (external):** the purchase is outside any plan at a list estimate of **$8.43**. It needs `MT_TICK_SPEND_CEILING_USD` and `MT_TICK_SPEND_30D_CEILING_USD` at or above that for the run. The per-pass ceiling is $0.50 today (TD6).
- **Databento:** free metadata and `symbology.resolve` calls; one paid batch job.

### Interfaces Required

- `extend_calendar_sessions(conn, calendar_id, *, start, end)`, which clamps to `holidays_seeded_through`.
- `calendar_for_product(product)`, the single product → calendar lookup that ingest, status, coverage, the roll reads and the API all go through.
- `TICK_UNIVERSE`, which the pass, status, overview and `/api/v1/futures/products` iterate.
- 224's estimate → guard → manifest path and 225's three ingest checks, unchanged.

## Architecture

### Component Structure

```
market/schema/seed_cme_calendar.py      CmeCalendarSeed shape, seed-taking insert builders;
                                        equity builders keep their names (one-line calls)
market/schema/seed_cme_metals_calendar.py   NEW: CME_METALS row + dated exceptions
market/schema/migrations/minute.py      NEW 059 callable _seed_cme_metals_calendar;
                                        058 untouched
data/tick/constants.py                  FUTURES_PRODUCT_CALENDAR += GC
data/tick/universe.py                   TICK_UNIVERSE += GC entry
scripts/record_229_roll_fixture.py      required --product → {product}_*.json (es_* unchanged)
test/.../test_roll_provider_equivalence.py   parametrized over ES and GC
```

The metals table goes in its own module. The equity module is already about 440 lines, and the metals table is about the same size, because every row carries its source.

### Data Flow

```
migrate apply (minute) ─► 059 ─► trading_calendars/holidays/sessions (CME_METALS, 2020 → bound)

pass --estimate-only ─► plan: universe × range − manifest  (ES caught up → GC days only)
                        └─ sessions from CME_METALS via calendar_for_product("GC")
pass ─► guards (per-pass, 30-day) ─► definition + tbbo batch jobs ─► archive ─► verify
ingest ─► definitions first ─► tbbo units ─► counts / resolution / session-boundary checks
       └─ session index built from CME_METALS
status · coverage · active · get · overview · /api/v1/futures/* ─► iterate TICK_UNIVERSE (GC appears)
```

### State Management

New state is limited to:

- calendar rows on the production database;
- GC manifest, archive, definition, tick and ledger rows on the tick database, written by the existing pass and ingest;
- two recorded GC fixture files under `test/fixtures/tick/roll/`.

The archive directory is already in the backup set (223), and the tick cluster is already backed up (227). New rows are covered without changes.

## Technical Decisions

### Technology Choices

**TD1 — `CME_METALS` calendar row.**

| column | value |
|---|---|
| `calendar_id` | `CME_METALS` (constant `CME_METALS_CALENDAR_ID`) |
| `exchange_name` | `CME Globex Metals` |
| `timezone` | `America/Chicago` |
| `market_open` / `market_close` | `17:00` / `16:00` |
| `has_extended_hours` | `FALSE`, extended columns `NULL` |
| `holidays_seeded_through` | `2027-12-31` |

221 D2's one-regime rule applies. GC's 17:00–16:00 CT hours are confirmed from GC contract specifications over 2020 onward while compiling the seed. A change inside the range stops the work and goes to the PM. The calendar is named for the schedule, as `CME_EQUITY` is. Silver and other metals could share it later only after a per-product check. This slice maps only GC.

**TD2 — Exceptions are an explicit dated table, sourced from CME (221 D5, unchanged).** The table holds every weekday from 2020-01-01 through 2027-12-31 on which GC did not trade the regular session: `closed`, or `early_close` with its time. It is compiled from the "Metals" row of CME's yearly Globex holiday spreadsheets (Wayback snapshots, as for equity), with a source comment on every row.

The 221 findings say what to expect, and each still has to be sourced per year:

- On US holidays, metals halt at 13:30 CT from 2022, where equities halt at 12:00. Pre-2022 years are read from their own spreadsheets, never extrapolated.
- On the day after Thanksgiving, metals close at 12:45 CT through 2023 and 13:45 CT from 2024, where equities close at 12:15. *(Corrected in implementation: the design said 12:45 for every year; CME's 2024–2027 documents and the 2024–2025 trades say 13:45. See Implementation Findings.)*
- Metals are closed on Good Friday in the years equities trade an abbreviated session.

A one-off cross-check is run from the scratchpad against `pandas_market_calendars`' `CMEGlobex_GC`, with `uv run --no-project --with pandas_market_calendars`. It is never a project dependency (221 D5). Each difference is resolved from a CME document and recorded in this design's Implementation Findings. A year that cannot be sourced is reported to the PM, not guessed.

**Effort is re-rated from 2/5 to 3/5** for this sourcing, and the slice plan entry is updated to match. The plan's 2/5 did not count eight years of exceptions.

**Why the seed starts at 2020, not at the purchased range.** The PM's standing direction is that the 2020-onward universe is the target, and that cost is cut, never scope by date. A metals calendar that starts in 2024 would make GC's first backfill before that date fail on the calendar. The sourcing cost is small: 221 read the equity rows from CME's yearly holiday spreadsheets, and each spreadsheet carries one row per product group (221's source comments cite its "Equity Products" row). The metals rows are a re-read of the same documents.

**The slice does not stall on an early year.** If a year's metals row cannot be found, the seed starts on the first fully sourced year. That date becomes `CME_METALS_SEED_START`, a declared lower bound, as 2020-01-01 is for equity. Widening backward is filed as a GitHub issue and named in Implementation Findings. The purchased 2024 range never depends on the early years.

Christmas Eve 2024 is an example of why the table is sourced row by row. The library shows a normal 16:00 close for metals, where equities closed at 12:15. That row is settled from CME's 2024 spreadsheet, not from the library.

**TD3 — One seed shape, two tables.** A frozen `CmeCalendarSeed` holds:

- `calendar_id`
- the calendar-row mapping
- `seed_start`
- `holidays_seeded_through`
- `exceptions`

Both `CME_EQUITY_SEED` and `CME_METALS_SEED` are instances of it. New builders `cme_calendar_insert(seed)` and `cme_holidays_insert(seed)` hold the SQL once. `cme_equity_calendar_insert()` and `cme_equity_holidays_insert()` keep their names and signatures and become one-line calls with the equity seed. These wrappers exist only so that 058 and its tests stay untouched. 059 uses the seed-taking builders directly.

**Migration 058 and its tests are not edited.** An applied migration stays frozen, and the SQL it shares with 059 already lives in the builders. 059 gets its own callable, `_seed_cme_metals_calendar`, of the same three steps (Database / Storage Schema).

**TD4 — GC universe entry and range.**

```python
TickUniverseEntry("GC", ("GC.FUT",), SType.PARENT,
                  tier=TickSchema.TBBO, start=date(2024, 11, 2), end=date(2025, 1, 1))
```

The range is ES's range. The reasons:

- **A roll inside the range.** Databento's `GC.v.0` switched GCZ4 → GCG5 on 2024-11-29, and `GC.n.0` on 2024-11-21 (229 TD6). The calendar rule also switches inside the range.
- **Metals-specific exceptions inside the range.** Thanksgiving (13:30) and the day after Thanksgiving (13:45, corrected from 12:45 in implementation) both differ from equity. Christmas Day and New Year's Day are closures. TD7 depends on these.
- **Same sessions as ES,** so cross-product comparisons need no alignment.
- **The same start reasoning as 226:** the first whole session starts at 17:00 CT on Sunday 11-03.

**TD5 — Spreads: parent including spreads, decided on GC's own measurement.** GC uses `GC.FUT` parent, matching ES. Status already hides spreads by default (`SPREAD_CLASS`), and they are never roll candidates.

GC's share was measured before purchase with two free estimates over 2024-11-03 → 2025-01-01, run 2026-10-14:

| request | tbbo records | tbbo cost |
|---|---|---|
| `GC.FUT` parent (outrights + spreads) | 4,031,298 | $8.41 |
| 25 outright raw symbols (GCX4 … GCZ9) | 3,766,376 | $7.86 |

Spreads are 6.6% of GC's rows (ES: 2.35%) and $0.55 of the purchase. Spreads are dropped for a product only when they exceed a quarter of its rows. Below that, one request shape per product, with no hand-kept outright list to go stale as months list, is worth more than the saving. The post-ingest spread share is recorded in the walkthrough notes as a cross-check on this estimate.

**TD6 — Cost and purchase.** The free estimate, run 2026-10-14:

```
mt data tick estimate --symbols GC.FUT --stype parent --start 2024-11-03 --end 2025-01-01
```

The range starts on 11-03, the first UTC day the pass plans for a 2024-11-02 start (the first whole session opens Sunday 11-03).

| schema | records | size | cost | bundle (tier + definitions) |
|---|---|---|---|---|
| tbbo | 4.03 M | 307.6 MiB | $8.41 | **$8.43** |
| definition | 29,919 | 14.8 MiB | $0.02 | bought with the tier |

The "roll inputs for the range" in the slice plan are covered as follows:

- **Calendar rule:** needs definitions, which are in the bundle.
- **Volume rule:** reads ledger volume for the session two before. That data is inside the bought range, so it costs nothing extra. The first two sessions are unresolved, and status reports them.
- **Open-interest rule:** not bought (TD9).

The pass's own `--estimate-only` figure is the number of record. It is printed in the walkthrough before anything is bought.

**The Standard plan is not subscribed, so this is a purchase outside the plan.** That is the architecture's third stage ("rare purchases outside the plan"). It is not bundled into a subscription decision. **PM decision (2026-10-14): authorised** as one of the architecture's rare purchases. The PM sets both ceilings when the walkthrough reaches the purchase, and restores the per-pass ceiling afterwards. If the plan is subscribed first, the estimate prints $0 and nothing else changes.

**TD7 — Session-model verification for GC.** It runs in two directions, because the boundary check alone proves only one.

- **The calendar is not too narrow.** This is 225's session-boundary check, which runs on every GC unit at ingest: no trade in a break, on a closed day, or outside the populated range. A metals session seeded with equity times (a 12:00 close on Thanksgiving) fails the unit, naming the check.
- **The calendar is not too wide.** The boundary check cannot see an early close seeded too late. The walkthrough reads 1-minute bars for the GC front contract over each exception session in the range, using the shipped `mt data tick get 1m`. The last bar must fall just before the seeded close: 13:29 CT on 11-28 and 13:44 CT on 11-29. The next bar must be at or after the following 17:00 CT open. Each result is recorded in the walkthrough notes.

Every GC unit must reach *ingested*, and `coverage` must return ok over the range.

**TD8 — Roll verification against the provider.**

- `scripts/record_229_roll_fixture.py` gains a required `--product` option. Its only consumer is the developer command line, and the docstring's usage line changes with it. It writes the fixtures as `{product}_*.json` with the product lower-cased, which is the name ES's existing `es_*.json` files already have.
- The read uses the same shipped roll reads. It now resolves `{product}.c.0/.c.1/.v.0/.v.1` and reads the product's calendar through `calendar_for_product`.
- ES's fixtures are neither renamed nor re-read, so the ES test stays pinned to the data it was written against.
- `test_roll_provider_equivalence.py` is parametrized over the products with fixtures.

This puts `VOLUME_RANK_LAG_SESSIONS = 2` to its first test outside ES. The constant's docstring says a disagreement means "revisit it whole, never per roll". If GC's `v.0` disagrees with the ledger at lag 2, the slice stops and reports to the PM. It does not add a per-product lag.

**TD9 — Open interest: revisited, deferred again.** The evidence is 229 TD6: `GC.n.0` rolls 6–12 days before `GC.v.0` (2024-11-21 against 11-29), so for GC the rule would give a distinct answer. It is still deferred:

- **Cost:** `statistics` is a purchase outside the plan until the Standard plan is subscribed, and then it is included. Buying it now pays for what the subscription will include.
- **Size:** shipping it needs a new archived schema, a tick-track migration, a table, a projection and universe entries. That is a slice of its own, larger than this one.
- **Need:** the default rule `v.0` is Databento's own default convention, and no consumer has asked for an open-interest roll.

Slice plan Future Work item 2 gets the GC evidence and a trigger: build it in the first subscription month, beside the `ohlcv` history pull (Future Work item 7), or earlier if a consumer needs it. If no subscription date has been set when 234 is designed, 234's design asks the question again, because 234 is the first slice that runs passes unattended. **PM decision (2026-10-14): deferred.** Open interest does not ship in this slice.

### Patterns and Conventions

- **TD10 — "No code change" is tested, not asserted.** Status, overview, `active`, `get` and every `/api/v1/futures/*` route iterate `TICK_UNIVERSE` or go through `calendar_for_product`. A grep at design time found no `"ES"` literal on any surface. Three test-side assumptions do change:
  - `test/unit/data/tick/test_universe.py` asserts the product list is `["ES"]`. It becomes `["ES", "GC"]` and gains GC's field assertions.
  - The API fixture `two_products` (`test/integration/api_server/conftest.py`) adds a synthetic `SECOND = TickUniverseEntry("GC", …)` and maps it onto `CME_EQUITY`. With GC in the real universe, that would list GC twice and put it on the wrong calendar. The fixture keeps its monkeypatch, so the API tests stay isolated from later universe edits. The synthetic product is renamed to a root no CME product uses, together with its symbols in `seed_second_product`. The fixture's comment, "232 needs no change", is corrected. The real universe's GC on `CME_METALS` is asserted once, in `test_universe.py`.
  - The migration-chain test moves its expected tail from 058 to 059.

  Tests that read `UNTIERED` (`test/tick_support/universe.py`) gain an untiered GC entry automatically. Any test whose expectations count products is updated in the same task and named in the task file.
- **Ids are defined once:** `CME_METALS_CALENDAR_ID` in the metals seed module, imported by the constants module, as `CME_EQUITY_CALENDAR_ID` is.
- **Does this belong in the API?** No new surface. GC reaches the API through the existing routes. Calendars stay unserved (221 D9).
- **Realtime paths check:** nothing here rules out path A (assembled from realtime capture) or path B (historical with the lag). A calendar is delivery-agnostic, and the universe entry is the same shape a realtime subscription would read.
- **Kalshi contract diff:** no pass code changes. The parity test is run and its result recorded, as a named task.
- **Cadence:** manual. The pass and ingest are fired by hand; nothing waits on a timer or a future roll.

## Implementation Details

### Database / Storage Schema

**`059_seed_cme_metals_calendar`** (minute track, Python callable, production database):

1. INSERT the `CME_METALS` row with `holidays_seeded_through = 2027-12-31` (`ON CONFLICT DO NOTHING`).
2. INSERT every `CME_METALS_EXCEPTIONS` row into `trading_holidays` (`ON CONFLICT DO NOTHING`).
3. `extend_calendar_sessions(conn, CME_METALS_CALENDAR_ID, start=CME_METALS_SEED_START, end=Dec 31 of current_year + TRADING_SESSIONS_EXTENSION_YEARS)`, which clamps to the bound.

The migration does not touch `NYSE`, `NASDAQ` or `CME_EQUITY`. Auto-extension, `mt data extend` and the `--strict` horizon warning already loop over every calendar row (`auto_extend.py`), so `CME_METALS` gets its 90-day "seed the next year" warning with no change.

There is no tick-track migration. GC uses the same tables, layout, tier CHECK and grants as ES (226).

## Integration Points

### Provides to Other Slices

- **234 (production wiring):** a two-product universe. Every pass 234 schedules covers GC with no further edit.
- **Future products:** the pattern of a seed instance, a migration, a mapping entry and a universe entry, with the two-direction session verification. It is written into the runbook.
- **Future Work item 2 (open interest):** GC evidence and a trigger.

### Consumes from Other Slices

- 221's calendar machinery, 224's pass and guards, 225's ingest checks and status, 229's roll reads and recorder, 230's `get`/`active`/overview, 231's routes. All are consumed unchanged.
- If the production calendar is down, ingest halts and status exits 4, as for ES (225). Nothing is lost.

## Success Criteria

### Functional Requirements

1. Migration 059 applies on a fresh test database and on production. Afterwards `mt data calendars list` shows `CME_METALS` with `holidays seeded through 2027-12-31`, and `CME_EQUITY`, `NYSE` and `NASDAQ` rows and sessions are byte-identical before and after (test).
2. Every `CME_METALS_EXCEPTIONS` row carries a CME source comment. The `pandas_market_calendars` cross-check differences, each with its resolution, are recorded in Implementation Findings.
3. `mt data calendars sessions --calendar CME_METALS --from 2024-11-27 --to 2024-12-02` shows the 11-28 session closing 13:30 CT and the 11-29 session closing 13:45 CT. `--calendar CME_EQUITY` over the same dates still shows 12:00 and 12:15.
4. `calendar_for_product("GC")` returns `CME_METALS`. The universe validates with ES and GC.
5. `mt data tick pass --estimate-only` plans only GC days and prints the bundle cost. A pass within the PM's ceilings buys them. Every GC unit reaches *ingested* with all three checks passing, and `mt data tick coverage --start 2024-11-02 --end 2025-01-01` is ok for GC.
6. The too-wide check (TD7) is recorded for every metals exception session in the range.
7. `mt data tick status` and `mt data overview` show a GC line caught up at tier tbbo, with the active contract per status rule. `/api/v1/futures/products` lists GC on `CME_METALS`. `/api/v1/futures/products/GC/schedule` over the range shows the switch from GCZ4 to GCG5. None of these needed a surface code change.
8. The provider-equivalence test passes for GC at `VOLUME_RANK_LAG_SESSIONS = 2`, or the slice has stopped and reported the disagreement (TD8).
9. The open-interest decision (TD9) is confirmed by the PM and recorded in the slice plan.

### Technical Requirements

- Seed SQL is shared through the seed-taking builders, not copied (TD3). Migration 058 and its tests are unchanged in the diff.
- ruff, mypy (one invocation over the src and test paths) and the unit, integration and load tiers pass, apart from the known pre-existing failures already recorded for main.
- The Kalshi pass-contract parity test passes.
- The architecture's Revision Log gains the 232 entry (Technical Scope, Documentation).
- The data-correctness contract's I4 row gains "232: `CME_METALS` behind the same surface". The I13 row gains "232: GC units pass the session-boundary check; too-wide check in the walkthrough". The slice-mapping table gains 232's rows.
- The runbook (`100-production-operations.md`, "Operating the tick tier") gains "Adding a futures product": seed and migration, mapping, universe entry, estimate, PM ceilings, pass, ingest, and the two-direction session check.

### Integration Requirements

- 234 can schedule passes over the two-product universe with no universe or calendar edit.
- No product surface changed in this slice's diff: no pass, ingest, status, overview, `get`, `active` or API code. The expected source diff is the two constants, the seed modules (TD3 builders and the metals table), the migration list, and the developer-only recorder script's `--product` option (TD8). Test changes are limited to those named in TD10. Anything else is the leaked product assumption this slice exists to find, and it goes to the PM.

### Verification Walkthrough

This is a draft, refined after implementation. The commands run from the dev checkout (`uv run mt …`) until 234 wires `mt-run data tick`.

1. **Calendar.**
   ```bash
   uv run mt data migrate status            # 059_seed_cme_metals_calendar pending
   uv run mt data migrate apply
   uv run mt data calendars list            # CME_METALS … holidays seeded through 2027-12-31
   uv run mt data calendars sessions --calendar CME_METALS --from 2024-11-27 --to 2024-12-02
   uv run mt data calendars sessions --calendar CME_EQUITY --from 2024-11-27 --to 2024-12-02
   ```
   Expected: metals closes at 13:30 CT on 11-28 and 13:45 CT on 11-29. Equity is unchanged at 12:00 and 12:15. There is no session dated 12-25 or 2025-01-01 on either calendar.

2. **Estimate (free).**
   ```bash
   uv run mt data tick pass --estimate-only
   ```
   Expected: ES plans nothing because it is caught up. GC plans the definition and tbbo days for 2024-11-03 → 12-31. The bundle cost is about $8.43, and the run is refused at the current $0.50 ceiling.

3. **Purchase (PM-authorised spend).** The PM sets `MT_TICK_SPEND_CEILING_USD` and `MT_TICK_SPEND_30D_CEILING_USD` at or above the printed figure, then:
   ```bash
   uv run mt data tick pass                 # submits, waits within 1,800 s, downloads, verifies, projects definitions
   uv run mt data tick ingest
   ```
   Expected: every GC unit ends *ingested*, with no failed units. Record the ingest wall-clock and the slowest unit in the walkthrough notes against the bounds under Testing. If the 1,800 s wait budget ends with a job still in flight, the pass prints it with its deadline. Downloading it before that deadline is then the operator's job (architecture, manual regime): run `uv run mt data tick pass` again, which reconciles and downloads before it buys anything. Afterwards the per-pass ceiling is restored.

4. **Proof surfaces.**
   ```bash
   uv run mt data tick status
   uv run mt data tick coverage --start 2024-11-02 --end 2025-01-01
   uv run mt data overview
   ```
   Expected: a GC line with tier tbbo and caught up, and coverage ok on every GC session. The overview tick line names both products.

5. **Session model, too-wide direction.**
   ```bash
   uv run mt data tick get 1m --product GC --start 2024-11-28 --end 2024-11-29
   uv run mt data tick get 1m --product GC --start 2024-11-29 --end 2024-11-30
   ```
   The dates are session dates, so `--start 2024-11-28` is the session that opens on 11-27 at 17:00 CT. Expected: the last bar of the 11-28 session is at 13:29 CT (19:29 UTC), and the last bar of the 11-29 session is at 13:44 CT (19:44 UTC). Record each exception session's last-bar time in the walkthrough notes.

6. **Roll.**
   ```bash
   uv run mt data tick active --product GC --start 2024-11-04 --end 2025-01-01
   uv run python scripts/record_229_roll_fixture.py --product GC
   uv run pytest test/unit/data/tick/test_roll_provider_equivalence.py
   ```
   Expected: under `v.0`, GCZ4 serves until the session Databento's `GC.v.0` switches on (2024-11-29), and GCG5 serves after it. The first two sessions are listed as unresolved. The test passes for ES and GC.

7. **API.**
   ```bash
   curl -s localhost:8137/api/v1/futures/products | jq '.products[] | {product, calendar}'
   curl -s 'localhost:8137/api/v1/futures/products/GC/schedule?start=2024-11-04&end=2025-01-01' | jq '.segments'
   ```
   Expected: GC is on `CME_METALS`, and its segments match step 6's `active` output.

## Risk Assessment

### Technical Risks

- **The roll-rule lag may not hold for GC.** `VOLUME_RANK_LAG_SESSIONS` was fitted on two ES rolls. GC's statistics publication could differ.
- **Pre-2022 metals schedules may be unsourceable.** Wayback coverage of CME's yearly spreadsheets has gaps.

### Mitigation Strategies

- **Lag:** TD8 tests it on the purchased roll before anything depends on it. A disagreement stops the slice for a PM decision, rather than adding a per-product constant.
- **Sourcing:** a year that cannot be sourced is reported, not guessed. The seed then starts on the first fully sourced year, and widening backward becomes a GitHub issue (TD2). Verification uses 2024, so it does not depend on the early years.

## Implementation Notes

### Development Approach

1. Add the seed shape and the seed-taking builders (TD3). 058's tests pass without being edited.
2. Compile `CME_METALS_EXCEPTIONS` from CME sources, run the cross-check, and record the findings.
3. Add migration 059 with its integration test (CME_METALS rows and sessions; other calendars untouched). Move the chain-tail test.
4. Add the mapping and universe entries, and update the ES-only test assumptions (TD10).
5. Apply 059 on production. Run the free estimate and stop for the PM's ceilings.
6. Run the purchase and ingest, then the proof surfaces and the too-wide check (TD7).
7. Record the GC roll fixture and parametrize the equivalence test (TD8).
8. Update the docs: contract mapping, runbook entry, slice plan Future Work item 2, the architecture's Revision Log entry, and the Kalshi parity result. Commit at each section checkpoint.

**Testing:** unit tests cover the seed shape and the GC rows (sources present, dates are weekdays, statuses valid). Integration tests cover migration 059 on a throwaway database. The provider-equivalence test runs over the recorded GC fixtures. **Ingest target (architecture, "Ingest throughput and the write path"):** a day of sessions ingests far faster than a day of market time, and a month within an operator's working session. GC is checked against 226's bounds for that target: the slowest unit at most 120 s, and the whole range at most 2 h. The measured wall-clock is recorded in walkthrough step 3, and a miss fails the slice. No new load test is added, because the ingest code path is unchanged and step 3 measures it on real GC data.

### Special Considerations

- **Spend is the PM's.** No agent raises a ceiling. The walkthrough stops at step 2 until the PM has set both ceilings.
- **Production migration:** 059 writes only new calendar rows and sessions. It is idempotent, and no other calendar's rows change (criterion 1).

## Implementation Findings

### Baseline (task 0.1, branch 232-slice.universe-expansion-gc from main 9cea3a7)

- Unit tier: 4707 passed, 5 skipped, 0 failed.
- `ruff check src test scripts`: 1479 pre-existing errors on main, so the ruff gate runs on touched files only.
- `test/unit/data/tick/test_pass_contract_parity.py`: 31 passed.

### CME_METALS sources (tasks 2.1–2.3)

Every year from 2020 through 2027 was sourced, so `CME_METALS_SEED_START` stays 2020-01-01 and no backward-widening issue is needed. 89 rows. Each row's full Wayback URL is in its `# source:` comment in `seed_cme_metals_calendar.py`; the table below names the document.

- **Documents read.** 2020–2022 and 2023-01-02: the product-group row that holds metals ("Energy, Metals, Softs & DME Products" and its variants; "Energy, Metal, …" in the 2020 and 2021 MLK sheets) of CME's Globex holiday spreadsheets, the same files 221 cited. 2023: the METALS row of CME's holiday PDFs. From 2023-09: product 437 (Gold Futures) in the trading-hours JSON captures (56 holiday windows, one capture each). The Wayback captures of past windows taken after the fact are empty (all of 2023 before September, and Thanksgiving 2023), so those rows come from the PDFs.
- **Reading the JSON.** The same reading as CME_EQUITY's: a weekday with no events is closed; a `closed` event before 16:00 is an early close; a holiday whose first event is the next session's `preopen` before 16:00 is a halt (early close) on the holiday. Applied to product 133 (ES), the reading reproduces all 35 JSON-sourced CME_EQUITY rows exactly.
- **Hours (TD1).** Every document shows the regular 16:00 CT close and 17:00 CT open around each holiday, 2020–2027. No change inside the range.
- **Ignored rows.** The "Gold" rows at the foot of the 2020–2023 spreadsheets are Bursa Malaysia's gold contract (the partner-exchange table), not COMEX GC. The "Gold TAS early close 11:30 CT" notes concern trade-at-settlement only, not the outright session.
- **One row with no CME document: 2023-01-16 (MLK).** None is archived for any product (221 found the same for equity). It is settled from Databento trade counts, as CME_EQUITY's row was (PM decision, 2026-09-28): 222 GC trades 13:00–13:30 CT, 0 from 13:30–16:59 CT. **PM decision (2026-10-14): the 2026-09-28 decision covers this metals row too.**
- **Independent check.** Every row dated up to 2026-10-13 (74) was checked against Databento GLBX.MDP3 `GC.FUT` trade counts (free `metadata.get_record_count`): trades in the 30 minutes before each early close and none from the close to 16:59 CT; none from 00:00 to 16:59 CT on each closed day. 74 of 74 pass. Equity-only exception days that metals trade normally were also checked: 2023-07-03, 2024-07-03 and 2025-07-03 (3,571 / 6,650 / 4,692 trades 12:15–15:59 CT) and 2025-01-09, the National Day of Mourning (25,788 trades 08:30–15:59 CT; CME's PDF: "CME GROUP METALS NORMAL HOURS").

**Design correction.** TD2 expected the day after Thanksgiving to close at 12:45 CT. That holds through 2023. From 2024 CME publishes 13:45, and the trades agree (2024-11-29: 371 trades 13:30–13:45 CT, 0 after; 2025-11-28: 817 trades in the 30 minutes before 13:45, 0 after). The TD2 bullet, TD4, TD7, criterion 3 and walkthrough steps 1 and 5 now read 13:45 / 13:44 CT (19:44 UTC). The design's "12:45" belongs to Christmas Eve, which metals close at 12:45 in every year of the range.

Other per-year differences from the equity table that the sources show: US holidays halt at 12:00 in 2020–2021 and 13:30 from 2022; a Friday holiday (2020-07-03, 2025-07-04, 2026-06-19, 2026-07-03, 2027-06-18) closes at 12:00, because no session reopens until Sunday; Good Friday 2021 and 2026 is closed for metals where equities traded until 08:15.

| date | name | status | close CT | source | Databento check |
|---|---|---|---|---|---|
| 2020-01-01 | New Year's Day | closed | — | CME holiday spreadsheet `2019-2020-new-years` | verified |
| 2020-01-20 | Martin Luther King Jr. Day | early_close | 12:00 | CME holiday spreadsheet `2020-mlk` | verified |
| 2020-02-17 | Presidents Day | early_close | 12:00 | CME holiday spreadsheet `2020-presidents` | verified |
| 2020-04-10 | Good Friday | closed | — | CME holiday spreadsheet `2020-good-friday` | verified |
| 2020-05-25 | Memorial Day | early_close | 12:00 | CME holiday spreadsheet `2020-memorial` | verified |
| 2020-07-03 | Independence Day (observed) | early_close | 12:00 | CME holiday spreadsheet `2020-independence` | verified |
| 2020-09-07 | Labor Day | early_close | 12:00 | CME holiday spreadsheet `2020-labor` | verified |
| 2020-11-26 | Thanksgiving Day | early_close | 12:00 | CME holiday spreadsheet `2020-thanksgiving` | verified |
| 2020-11-27 | Day after Thanksgiving | early_close | 12:45 | CME holiday spreadsheet `2020-thanksgiving` | verified |
| 2020-12-24 | Christmas Eve | early_close | 12:45 | CME holiday spreadsheet `2020-christmas` | verified |
| 2020-12-25 | Christmas Day | closed | — | CME holiday spreadsheet `2020-christmas` | verified |
| 2021-01-01 | New Year's Day | closed | — | CME holiday spreadsheet `2021-new-years` | verified |
| 2021-01-18 | Martin Luther King Jr. Day | early_close | 12:00 | CME holiday spreadsheet `2021-mlk` | verified |
| 2021-02-15 | Presidents Day | early_close | 12:00 | CME holiday spreadsheet `2021-presidents` | verified |
| 2021-04-02 | Good Friday | closed | — | CME holiday spreadsheet `2021-good-friday` | verified |
| 2021-05-31 | Memorial Day | early_close | 12:00 | CME holiday spreadsheet `2021-memorial` | verified |
| 2021-07-05 | Independence Day (observed) | early_close | 12:00 | CME holiday spreadsheet `2021-independence` | verified |
| 2021-09-06 | Labor Day | early_close | 12:00 | CME holiday spreadsheet `2021-labor` | verified |
| 2021-11-25 | Thanksgiving Day | early_close | 12:00 | CME holiday spreadsheet `2021-thanksgiving` | verified |
| 2021-11-26 | Day after Thanksgiving | early_close | 12:45 | CME holiday spreadsheet `2021-thanksgiving` | verified |
| 2021-12-24 | Christmas Day (observed) | closed | — | CME holiday spreadsheet `2021-christmas` | verified |
| 2022-01-17 | Martin Luther King Jr. Day | early_close | 13:30 | CME holiday spreadsheet `2022-mlk` | verified |
| 2022-02-21 | Presidents Day | early_close | 13:30 | CME holiday spreadsheet `2022-presidents` | verified |
| 2022-04-15 | Good Friday | closed | — | CME holiday spreadsheet `2022-good-friday` | verified |
| 2022-05-30 | Memorial Day | early_close | 13:30 | CME holiday spreadsheet `2022-memorial` | verified |
| 2022-06-20 | Juneteenth (observed) | early_close | 13:30 | CME holiday spreadsheet `2022-juneteenth` | verified |
| 2022-07-04 | Independence Day | early_close | 13:30 | CME holiday spreadsheet `2022-independence` | verified |
| 2022-09-05 | Labor Day | early_close | 13:30 | CME holiday spreadsheet `2022-labor` | verified |
| 2022-11-24 | Thanksgiving Day | early_close | 13:30 | CME holiday spreadsheet `2022-thanksgiving` | verified |
| 2022-11-25 | Day after Thanksgiving | early_close | 12:45 | CME holiday spreadsheet `2022-thanksgiving` | verified |
| 2022-12-26 | Christmas Day (observed) | closed | — | CME holiday spreadsheet `2022-christmas` | verified |
| 2023-01-02 | New Year's Day (observed) | closed | — | CME holiday spreadsheet `2023-new-years` | verified |
| 2023-01-16 | Martin Luther King Jr. Day | early_close | 13:30 | Databento trade counts | verified |
| 2023-02-20 | Presidents Day | early_close | 13:30 | CME holiday PDF `presidents-day` | verified |
| 2023-04-07 | Good Friday | closed | — | CME holiday PDF `good-friday` | verified |
| 2023-05-29 | Memorial Day | early_close | 13:30 | CME holiday PDF `memorial-day-2023` | verified |
| 2023-06-19 | Juneteenth | early_close | 13:30 | CME holiday PDF `juneteenth-2023` | verified |
| 2023-07-04 | Independence Day | early_close | 13:30 | CME holiday PDF `4th-of-july-2023` | verified |
| 2023-09-04 | Labor Day | early_close | 13:30 | CME holiday PDF `labor-day-2023` | verified |
| 2023-11-23 | Thanksgiving Day | early_close | 13:30 | CME holiday PDF `thanksgiving-day-2023` | verified |
| 2023-11-24 | Day after Thanksgiving | early_close | 12:45 | CME holiday PDF `thanksgiving-day-2023` | verified |
| 2023-12-25 | Christmas Day | closed | — | CME holiday PDF `christmas-day-2023` | verified |
| 2024-01-01 | New Year's Day | closed | — | CME trading-hours JSON `2023-12-31_2024-01-02` | verified |
| 2024-01-15 | Martin Luther King Jr. Day | early_close | 13:30 | CME trading-hours JSON `2024-01-14_2024-01-16` | verified |
| 2024-02-19 | Presidents Day | early_close | 13:30 | CME trading-hours JSON `2024-02-18_2024-02-20` | verified |
| 2024-03-29 | Good Friday | closed | — | CME trading-hours JSON `2024-03-28_2024-03-30` | verified |
| 2024-05-27 | Memorial Day | early_close | 13:30 | CME trading-hours JSON `2024-05-26_2024-05-28` | verified |
| 2024-06-19 | Juneteenth | early_close | 13:30 | CME trading-hours JSON `2024-06-18_2024-06-20` | verified |
| 2024-07-04 | Independence Day | early_close | 13:30 | CME trading-hours JSON `2024-07-03_2024-07-05` | verified |
| 2024-09-02 | Labor Day | early_close | 13:30 | CME trading-hours JSON `2024-09-01_2024-09-03` | verified |
| 2024-11-28 | Thanksgiving Day | early_close | 13:30 | CME trading-hours JSON `2024-11-27_2024-11-29` | verified |
| 2024-11-29 | Day after Thanksgiving | early_close | 13:45 | CME trading-hours JSON `2024-11-27_2024-11-29` | verified |
| 2024-12-24 | Christmas Eve | early_close | 12:45 | CME trading-hours JSON `2024-12-24_2024-12-26` | verified |
| 2024-12-25 | Christmas Day | closed | — | CME trading-hours JSON `2024-12-24_2024-12-26` | verified |
| 2025-01-01 | New Year's Day | closed | — | CME trading-hours JSON `2024-12-31_2025-01-02` | verified |
| 2025-01-20 | Martin Luther King Jr. Day | early_close | 13:30 | CME trading-hours JSON `2025-01-19_2025-01-21` | verified |
| 2025-02-17 | Presidents Day | early_close | 13:30 | CME trading-hours JSON `2025-02-16_2025-02-18` | verified |
| 2025-04-18 | Good Friday | closed | — | CME trading-hours JSON `2025-04-17_2025-04-19` | verified |
| 2025-05-26 | Memorial Day | early_close | 13:30 | CME trading-hours JSON `2025-05-25_2025-05-27` | verified |
| 2025-06-19 | Juneteenth | early_close | 13:30 | CME trading-hours JSON `2025-06-18_2025-06-20` | verified |
| 2025-07-04 | Independence Day | early_close | 12:00 | CME trading-hours JSON `2025-07-03_2025-07-05` | verified |
| 2025-09-01 | Labor Day | early_close | 13:30 | CME trading-hours JSON `2025-08-31_2025-09-02` | verified |
| 2025-11-27 | Thanksgiving Day | early_close | 13:30 | CME trading-hours JSON `2025-11-26_2025-11-28` | verified |
| 2025-11-28 | Day after Thanksgiving | early_close | 13:45 | CME trading-hours JSON `2025-11-26_2025-11-28` | verified |
| 2025-12-24 | Christmas Eve | early_close | 12:45 | CME trading-hours JSON `2025-12-24_2025-12-26` | verified |
| 2025-12-25 | Christmas Day | closed | — | CME trading-hours JSON `2025-12-24_2025-12-26` | verified |
| 2026-01-01 | New Year's Day | closed | — | CME trading-hours JSON `2025-12-31_2026-01-02` | verified |
| 2026-01-19 | Martin Luther King Jr. Day | early_close | 13:30 | CME trading-hours JSON `2026-01-18_2026-01-20` | verified |
| 2026-02-16 | Presidents Day | early_close | 13:30 | CME trading-hours JSON `2026-02-15_2026-02-17` | verified |
| 2026-04-03 | Good Friday (jobs report) | closed | — | CME trading-hours JSON `2026-04-01_2026-04-03` | verified |
| 2026-05-25 | Memorial Day | early_close | 13:30 | CME trading-hours JSON `2026-05-24_2026-05-26` | verified |
| 2026-06-19 | Juneteenth | early_close | 12:00 | CME trading-hours JSON `2026-06-18_2026-06-20` | verified |
| 2026-07-03 | Independence Day (observed) | early_close | 12:00 | CME trading-hours JSON `2026-07-03_2026-07-05` | verified |
| 2026-09-07 | Labor Day | early_close | 13:30 | CME trading-hours JSON `2026-09-06_2026-09-08` | verified |
| 2026-11-26 | Thanksgiving Day | early_close | 13:30 | CME trading-hours JSON `2026-11-25_2026-11-27` | future (CME only) |
| 2026-11-27 | Day after Thanksgiving | early_close | 13:45 | CME trading-hours JSON `2026-11-25_2026-11-27` | future (CME only) |
| 2026-12-24 | Christmas Eve | early_close | 12:45 | CME trading-hours JSON `2026-12-24_2026-12-26` | future (CME only) |
| 2026-12-25 | Christmas Day | closed | — | CME trading-hours JSON `2026-12-24_2026-12-26` | future (CME only) |
| 2027-01-01 | New Year's Day | closed | — | CME trading-hours JSON `2026-12-31_2027-01-02` | future (CME only) |
| 2027-01-18 | Martin Luther King Jr. Day | early_close | 13:30 | CME trading-hours JSON `2027-01-17_2027-01-19` | future (CME only) |
| 2027-02-15 | Presidents Day | early_close | 13:30 | CME trading-hours JSON `2027-02-14_2027-02-16` | future (CME only) |
| 2027-03-26 | Good Friday | closed | — | CME trading-hours JSON `2027-03-25_2027-03-27` | future (CME only) |
| 2027-05-31 | Memorial Day | early_close | 13:30 | CME trading-hours JSON `2027-05-30_2027-06-01` | future (CME only) |
| 2027-06-18 | Juneteenth (observed) | early_close | 12:00 | CME trading-hours JSON `2027-06-17_2027-06-19` | future (CME only) |
| 2027-07-05 | Independence Day (observed) | early_close | 13:30 | CME trading-hours JSON `2027-07-04_2027-07-06` | future (CME only) |
| 2027-09-06 | Labor Day | early_close | 13:30 | CME trading-hours JSON `2027-09-05_2027-09-07` | future (CME only) |
| 2027-11-25 | Thanksgiving Day | early_close | 13:30 | CME trading-hours JSON `2027-11-24_2027-11-26` | future (CME only) |
| 2027-11-26 | Day after Thanksgiving | early_close | 13:45 | CME trading-hours JSON `2027-11-24_2027-11-26` | future (CME only) |
| 2027-12-24 | Christmas Day (observed) | closed | — | CME trading-hours JSON `2027-12-22_2027-12-24` | future (CME only) |

### pandas_market_calendars cross-check (task 2.4)

Run from the scratchpad with `uv run --no-project --with pandas_market_calendars` (5.5.0). `CMEGlobex_GC` resolves to `CMEGlobex_EnergyAndMetals`. Compared over every weekday 2020-01-01 → 2027-12-31: 13 differences, each resolved from a CME document; the library is wrong on all 13. `git status` shows no dependency change.

| date | table | library | resolution |
|---|---|---|---|
| 2020-12-24 | 12:45 | regular | CME 2020 Christmas spreadsheet: metals close 12:45. Trades: 1,191 before, 0 after |
| 2022-09-05, 2023-09-04, 2024-09-02, 2025-09-01, 2027-09-06 | 13:30 | 12:00 | CME 2022 Labor Day spreadsheet, 2023 PDF, JSON 2024/2025/2027: 13:30 halt. Trades confirm 2022–2025 (0 after 13:30, 131–461 in the 30 minutes before) |
| 2024-11-29, 2025-11-28, 2027-11-26 | 13:45 | 12:45 | CME JSON 2024, 2025, 2027: 13:45. Trades confirm 2024 and 2025 |
| 2024-12-24, 2025-12-24 | 12:45 | regular | CME JSON: 12:45 close (the design's known case). Trades: 642 / 695 before, 0 after |
| 2025-07-04, 2027-06-18 | 12:00 | 13:30 | CME JSON: Friday holiday, 12:00 close. Trades confirm 2025 (687 before, 0 after) |

### Migration 059 test changes (task 3.1–3.2)

- `test/unit/test_schema_migrations.py`: `test_chain_ends_at_058` → `test_chain_ends_at_059` (planned, TD10), and the migration-count assertion 61 → 62, which counts every migration and so moves with each new one. No other assertion changed.
- `test/integration/test_migration_057_058_cme_calendar.py`: `test_calendars_list_shows_all_three` asserted the database holds exactly NYSE, NASDAQ and CME_EQUITY, so it fails once 059 adds a calendar. It is now `test_calendars_list_shows_every_calendar` and expects `CME_METALS` too: a one-line test-side change of the kind TD10 names (a test that counts calendars), and the only edit to a 058 test file. Migration 058 itself is untouched.
- New `test/integration/test_migration_059_cme_metals_calendar.py`: 8 tests (TD1 row, exceptions equal the seed, sessions from seed start to the bound, the purchased-range sessions 11-28 13:30 / 11-29 13:45 / 12-24 12:45 with no 12-25 or 2025-01-01, other calendars byte-identical across 059, idempotence, `calendars list`, `calendars sessions`). All pass.

### ES-only assumptions found by the sweep (tasks 4.3–4.5)

Unit tier after the GC entry: 4716 passed, 5 skipped, 0 failed. Integration tier: 13 failed, 1023 passed, 128 skipped. Six of the 13 are the known pre-existing failures (`test_cli_lists.py` ×2, `test_migration_051_052.py` ×2, `test_policy_advances_head.py` ×2). The other seven are tick tests that assumed one universe product. Six of them are test-side and were updated:

- `test/unit/data/tick/test_universe.py`: product list `["ES", "GC"]`, GC field assertions, GC → `CME_METALS` (planned, TD10).
- `test/unit/data/tick/test_constants.py`: `calendar_for_product("GC")` is `CME_METALS`; the unknown-product refusal now uses `ZZ`.
- `test/tick_support/api.py`, `test/integration/api_server/conftest.py`, `test_futures_catalog.py`: the synthetic second product is `ZZ` (`ZZZ4`), and `two_products` pins its universe to ES plus `ZZ` (planned, TD10). `test_products_matches_the_cli_active_lines` now reads ES by product and checks one line per universe product.
- `test/integration/data/test_tick_accounting.py`: both tests read the ES line by product; the text output has one line per universe product under the header.
- `test/integration/data/test_tick_status_build.py`: `UNTIERED` now holds GC too (as TD10 foresaw); the test reads ES by product.
- `test/integration/data/test_tick_status_cli.py::test_preflight_refusals_exit_1`: the unknown-product case used `GC`; it now uses `ZZ`.
- `test/integration/data/test_tick_active_cli.py::test_no_product_answers_every_universe_product_and_status_rule`: GC (nothing seeded) answers "no sessions held for GC", and `active` keeps its nothing-held exit 3. The surface behaves as designed; the test assumed every product is seeded.

**Leaked product assumption (src), reported to the PM; the slice is stopped here.** `status_reads.raw_counts` (`src/manta_trading/data/tick/status_reads.py:186`) counts `tick_trade` rows of every instrument inside the session windows, with no product filter. `build_coverage` compares those counts with the product's own ledger, so each product's coverage reports the other product's trades as mismatches and exits 3. Seen in `test_tick_status_cli.py::test_coverage_json_is_to_dict_plus_exit_code`: GC's coverage over 2024-09-03 → 09-05 lists ES instruments 183748 (ESZ4), 5002 (ESH5) and others as `ledger 0, raw N`. Once GC is ingested, the same happens on production in both directions, so FR5 ("coverage ok for GC") cannot pass without a change to `raw_counts`. Every other `tick_trade` / ledger / definition read in src filters by product, unit or instrument; `newest_event_ns` is the tick store's single "newest tick" and global by design.

**PM decision (2026-10-14): fix it in this slice.** `raw_counts(conn, product, sessions)` now counts only instruments defined for the product (`instrument_id IN (SELECT instrument_id FROM tick_definition WHERE asset = product)`); `build_coverage` passes `entry.product`. Ingest stores no trade without its definition (the resolution check), so no stored row falls outside every product. The src diff therefore also holds `status_reads.py` and `tick_coverage.py`, the one leaked assumption this slice found. Tests: `test_tick_status_reads.py` gains `test_raw_counts_count_only_the_products_instruments` (ES, GC and an unknown product over one session), and the existing across-midnight test now seeds its instrument's definition (setup only; its assertions are unchanged). `test_tick_status_cli.py::test_coverage_json_is_to_dict_plus_exit_code` expects one coverage block per universe product and exit 0.

### Production apply and free estimate (tasks 7.1–7.2)

- **059 on production (PM go, 2026-10-14).** `mt data migrate status` showed `059_seed_cme_metals_calendar` as the only pending migration (69 applied); after `mt data migrate apply`, 70 applied, 0 pending. Digests (md5 of the calendar row, the holidays and the sessions) of `CME_EQUITY` (93 holidays, 2,068 sessions), `NASDAQ` and `NYSE` (84 / 2,280 each) are identical before and after. `CME_METALS`: 89 holidays, 2,065 sessions. `mt data calendars list` shows `CME_METALS … 2027-12-31`; `calendars sessions` shows metals 13:30 CT on 11-28 and 13:45 CT on 11-29 (equity 12:00 and 12:15), 12:45 on 12-24, and no session dated 12-25 or 2025-01-01.
- **Estimate (2026-10-14).** The first `mt data tick pass --estimate-only` ended `provider_abort` (exit 2) on a Databento `get_cost` HTTP 504; the immediate re-run succeeded. ES plans nothing. GC plans 51 days of tbbo and 51 of definitions in two requests per schema (2024-11-03 → 11-30, 2024-12-01 → 2025-01-01): tbbo $4.8008 + $3.6091, definitions $0.0114 + $0.0132. **Planned $8.4346** (the figure of record; TD6's list estimate was $8.43). Trailing 30 days $0.0041. Refused at the per-pass ceiling $0.50 and the 30-day cap $5.00. To buy, `MT_TICK_SPEND_CEILING_USD` ≥ 8.4346 and `MT_TICK_SPEND_30D_CEILING_USD` ≥ 8.4387 (trailing plus planned).

### Quality gate (task 6.1)

- ruff on every touched `.py` file: no error that main does not already have (30 pre-existing in `minute.py` and `test_schema_migrations.py`; both files were already unformatted on main, so only new files were formatted).
- mypy, one invocation over `src/manta_trading/data/kalshi`, `data/tick`, `market/schema`, the touched tests, `test/tick_support` and the recorder: 1 error, `market/schema/runner.py:85`, which this branch does not touch and which reproduces on main.
- Unit 4716 passed, 0 failed. Integration 6 failed (the known six), 1031 passed. Load 5 failed, 42 passed: four known (`test_187_api_nfr.py::test_status_endpoint_latency`, `test_921_minute_session_mass_nfr.py` ×3); the fifth, `test_224_tick_pass_nfr.py::test_whole_history_pass_and_steady_state_stay_in_budget`, seeds ES's history alone and then found the pass also buying GC's 51 definition days (2126 against 2075). It now pins the pass's universe to ES through `use_only(monkeypatch, "ES")` (new, beside `use_untiered` in `test/tick_support/universe.py`) and passes. A test-side ES-only assumption.
- `test_pass_contract_parity.py`: 31 passed (Kalshi contract diff: none).

### Purchase and ingest (tasks 8.1–8.2)

- **Ceilings (PM, 2026-10-14):** both set to $10.00 for the run. Re-estimate: planned $8.4346, verdict `allowed` in estimate-only.
- **Pass.** First run: `provider_abort` (exit 2) on a `get_cost` HTTP 504 before any submit. Second run (16:27 UTC): verdict `allowed`; submitted `GLBX-20261014-APNCYMUQH7` (definition, 11-03 → 11-30), `GLBX-20261014-9KWXGRVWGF` (tbbo, 11-03 → 11-30), `GLBX-20261014-456N4PS795` (definition, 12-01 → 2025-01-01), `GLBX-20261014-J58WAUBTJS` (tbbo, 12-01 → 2025-01-01); the wait then ended `provider_abort` on a `get_record_count` HTTP 504 after 1,177 s. Third run: reconcile verified the remaining 10 units, nothing to buy, definitions projected for 51 units (520 rows inserted, 14,955 no-ops, 0 failures). Every Databento failure was a gateway timeout; no unit failed.
- **Charged = estimate.** Actual cost per request equals its estimate: $0.0114 + $4.8008 + $0.0132 + $3.6091 = **$8.4346**. Provider record counts: tbbo 2,301,261 + 1,730,037; definitions 13,855 + 16,064.
- **Trailing spend display.** The third run printed "trailing 30 days $0.0000" because a `nothing_to_buy` plan does not run the guard. The window itself holds $8.4387 (8 requests); the cap still sees it. A report quirk, not a guard fault.
- **Ingest** (`mt data tick ingest`, 16:58 UTC): 51 tbbo units ingested, **4,031,298 records** (equal to TD5's pre-purchase estimate), 0 failed, with all three checks (counts, resolution, session boundary) passing on every unit. **Wall clock 14.4 s** for the whole range (bound 2 h); **slowest unit 1.369 s** (unit 200, 2024-11-25, 223,647 records; bound 120 s).
- The PM restores the per-pass ceiling after the slice's purchase work.

### Proof surfaces, session model and roll (tasks 9.1–9.4)

- **Status** (`mt data tick status`, exit 0): `GC (CME_METALS, GLBX.MDP3, GC.FUT parent) tier: tbbo`, 41 sessions held, 41 complete, caught up; 16 outrights listed, 50 spreads hidden. `v.0` GCG5, `c.0` GCF5 at session 2024-12-31.
- **Coverage** (`mt data tick coverage --start 2024-11-02 --end 2025-01-01`, exit 0): GC 41/41 sessions `ok`, and ES 41/41 `ok` (the `raw_counts` product filter on production, with both products stored).
- **Overview.** `mt data overview` has one store-wide `futures ticks` line (newest tick, `2024-12-31 21:59 UTC`); it has named no product since slice 230 (`newest_event_ns`). The expectation "the overview tick line names ES and GC" was wrong about the shipped surface; nothing changed and nothing is needed.
- **Spread share.** Outrights 3,766,376 records (equal to TD5's outright estimate), spreads 264,922: **6.57%** of 4,031,298 (TD5 estimated 6.6%).
- **Too-wide check (TD7).** The served `v.0` contract is not the right probe on a roll's edge: on 11-28 `v.0` serves GCZ4, whose last bar is 18:59 UTC (12:59 CT) because it had stopped trading, not because the session closed. The check therefore reads the latest bar across the contracts trading (`mt data tick get 1m --contract GCG5 / GCZ4`):

  | session | seeded close | last bar (GCG5) | next bar |
  |---|---|---|---|
  | 2024-11-28 | 13:30 CT | 19:29 UTC = 13:29 CT | 11-28 23:00 UTC = 17:00 CT |
  | 2024-11-29 | 13:45 CT | 19:44 UTC = 13:44 CT | 12-01 23:00 UTC = Sunday 17:00 CT |
  | 2024-12-24 | 12:45 CT | 18:44 UTC = 12:44 CT | 12-25 23:00 UTC = 17:00 CT |
  | 2024-12-25 | closed | no bars | — |

  2025-01-01 (closed) lies past the range end; the last session, 12-31, ends 21:59 UTC (15:59 CT, regular). No miss.
- **Roll** (`mt data tick active --product GC --start 2024-11-04 --end 2025-01-01`, exit 3 for the unresolved sessions): `v.0` GCZ4 2024-11-06 → 11-28, GCG5 11-29 → 12-31 (Databento's `GC.v.0` switches 11-29); 11-04 and 11-05 unresolved ("volume inputs: session 2024-10-31 / 11-01 is missing"). `c.0` GCX4 → 11-26, GCZ4 11-27 → 12-27, GCF5 from 12-30.
- **API** (branch build, `mt serve --port 8137`; the installed `mt-serve` runs the deployed code): `/api/v1/futures/products` lists ES on `CME_EQUITY` and GC on `CME_METALS`; `/products/GC/schedule` returns the same two `v.0` segments and the same two unresolved sessions as `active`. No surface code changed for either.
- **GC fixtures** (`record_229_roll_fixture.py --product GC`, free calls): `gc_definitions.json` 24 rows, `gc_volumes.json` 1,720, `gc_calendar.json` 153, `gc_databento.json` 4 symbols. Databento: `GC.c.0` GCX4 → 11-27 GCZ4 → 12-29 GCF5; `GC.v.0` GCZ4 → 11-29 GCG5; `GC.v.1` → 11-29 GCZ4 → 12-02 GCJ5.
- **Equivalence at `VOLUME_RANK_LAG_SESSIONS = 2`, inside GC's held range (from 2024-11-04):** `v.0` 40/40 sessions agree (TD8's stop condition does not fire); `c.0` and `c.1` 42/42 agree; **`v.1` disagrees on 3 of 40**: 12-02 (ours GCZ4, ranked on Thanksgiving 11-28: GCZ4 1,764 vs GCJ5 379), 12-09 (ours GCF5, 12-05: 1,104 vs 1,086), 12-12 (ours GCF5, 12-10: 1,153 vs 1,140); Databento says GCJ5 on all three. Lag 1 would match all three but moves `v.0`'s switch to 11-28, a session before Databento's; no single lag fits. **PM decision (2026-10-14): keep the lag whole at 2; rely on `v.0` (and `c.0`, `c.1`); note `v.1` for potential later use.** `test_roll_provider_equivalence.py` lists ES and GC; the three GC `v.1` sessions are an explicit `KNOWN_DIVERGENCE` set, and the test fails if that set changes either way.
- **Test-side issues the GC fixtures exposed, now fixed:** the shared resolve span starts 2024-08-30, before GC is held, so `c.*` "resolves" 46 pre-range sessions to contracts whose definitions GC does not hold (all 43 pre-range mismatches); and `assert len(resolved) > 40` was sized for ES's two held ranges (GC resolves exactly 40 under `v.*`). Each product is now compared from its first session with recorded volume (`_held_from`: ES 2024-08-30, the span start, so ES's compared set is unchanged; GC 2024-11-04), and the floor is `>= 40`. 29 tests pass.
