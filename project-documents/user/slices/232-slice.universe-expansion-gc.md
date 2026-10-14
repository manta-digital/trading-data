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
- On the day after Thanksgiving, metals close at 12:45 CT, where equities close at 12:15.
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
- **Metals-specific exceptions inside the range.** Thanksgiving (13:30) and the day after Thanksgiving (12:45) both differ from equity. Christmas Day and New Year's Day are closures. TD7 depends on these.
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
- **The calendar is not too wide.** The boundary check cannot see an early close seeded too late. The walkthrough reads 1-minute bars for the GC front contract over each exception session in the range, using the shipped `mt data tick get 1m`. The last bar must fall just before the seeded close: 13:29 CT on 11-28 and 12:44 CT on 11-29. The next bar must be at or after the following 17:00 CT open. Each result is recorded in the walkthrough notes.

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
3. `mt data calendars sessions --calendar CME_METALS --from 2024-11-27 --to 2024-12-02` shows the 11-28 session closing 13:30 CT and the 11-29 session closing 12:45 CT. `--calendar CME_EQUITY` over the same dates still shows 12:00 and 12:15.
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
   Expected: metals closes at 13:30 CT on 11-28 and 12:45 CT on 11-29. Equity is unchanged at 12:00 and 12:15. There is no session dated 12-25 or 2025-01-01 on either calendar.

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
   The dates are session dates, so `--start 2024-11-28` is the session that opens on 11-27 at 17:00 CT. Expected: the last bar of the 11-28 session is at 13:29 CT (19:29 UTC), and the last bar of the 11-29 session is at 12:44 CT (18:44 UTC). Record each exception session's last-bar time in the walkthrough notes.

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
