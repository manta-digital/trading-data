---
docType: tasks
slice: universe-expansion-gc
project: trading-data
lld: user/slices/232-slice.universe-expansion-gc.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [231, 226, 221, 229]
interfaces: [234]
projectState: >
  Slice design committed and reviewed twice (CONCERNS, addressed). ES is the
  only product in TICK_UNIVERSE; CME_EQUITY is the only CME calendar
  (migration 058). Slices 221, 226, 229 and 231 are complete. No GC data has
  been bought. The Standard plan is not subscribed.
dateCreated: 20261014
dateUpdated: 20261014
status: not_started
---

# Tasks: Universe Expansion — GC

## Context Summary

- Working on slice 232. It adds COMEX gold (GC) as the second futures product
  by configuration: a `CME_METALS` calendar, one `FUTURES_PRODUCT_CALENDAR`
  entry and one `TICK_UNIVERSE` entry.
- It delivers:
  - a data-driven CME seed module (`CmeCalendarSeed`) shared by equity and
    metals, so 058 and 059 do not duplicate SQL;
  - `seed_cme_metals_calendar.py` (row plus every dated exception from 2020,
    each with its CME source) and migration `059_seed_cme_metals_calendar`;
  - GC bought at tbbo for 2024-11-02 → 2025-01-01 (about $8.43, PM-authorised)
    and ingested through the existing pass and ingest;
  - session-model verification in both directions, roll verification against
    Databento, the open-interest decision, and documentation.
- **No product surface code changes.** Pass, ingest, status, overview, `get`,
  `active` and API code stay untouched. If one needs a change, stop and report
  it to the PM as a finding. Migration 058 and its tests are not edited.
- **Spend is the PM's.** No agent raises a ceiling. Section 7 ends at the free
  estimate; Section 8 begins only when the PM has set both ceilings.
- All decisions (TD1–TD10) and success criteria are in the LLD. Read TD1–TD10
  before starting.
- Never write `review:` into front matter. Never merge: the last task ends at a
  commit on the slice branch (Phase 7 merges).
- Next slice: 233 (cross-source acquisition arbitration); 234 (production
  wiring) is deferred.

**Test environment.** Export `MT_TIMESCALE_TEST_URL` from `.env` with the quotes
stripped. Run mypy on the src kalshi paths and the tests in one invocation.
Run unit and integration tiers separately. Known pre-existing failures are not
regressions: re-run in isolation before investigating (see memory notes on
integration flakes, migration 051/052, load tier).

**Effort scale:** 1 (trivial) to 5 (hard). Slice effort 3/5.

---

## Section 0 — Branch and Baseline

- [ ] **0.1 Create the slice branch and capture the baseline** (effort 1)
  - [ ] `cf config get git.integration_branch` is empty, so the target is `main`
  - [ ] From `main`: `git checkout -b 232-slice.universe-expansion-gc main`
  - [ ] Run the unit tier and `ruff check`; record failures that already exist
  - [ ] Run `test/unit/data/tick/test_pass_contract_parity.py`; record the result
  - [ ] Append an empty `## Implementation Findings` section at the end of
        `user/slices/232-slice.universe-expansion-gc.md`; later tasks write to it
  - [ ] Success: branch exists; the section exists and holds the baseline failure
        list

---

## Section 1 — Data-Driven CME Seed Shape (TD3)

- [ ] **1.1 Add `CmeCalendarSeed` and the seed-taking builders** (effort 2)
  - [ ] In `src/manta_trading/market/schema/seed_cme_calendar.py` add a frozen
        dataclass `CmeCalendarSeed` with `calendar_id`, `calendar` (row mapping),
        `seed_start`, `holidays_seeded_through`, `exceptions`
  - [ ] Add `CME_EQUITY_SEED`, an instance built from the existing equity
        constants. Keep every existing constant name exported
  - [ ] Add `cme_calendar_insert(seed)` and `cme_holidays_insert(seed)` holding
        the SQL once (the logic currently in the two equity builders)
  - [ ] Turn `cme_equity_calendar_insert()` and `cme_equity_holidays_insert()`
        into one-line calls with the equity seed. Same names, same signatures
        (they exist so 058 and its tests stay untouched)
  - [ ] Success: no SQL string or column list is duplicated; module still
        under about 460 lines

- [ ] **1.2 Test the seed shape** (effort 1)
  - [ ] In `test/unit/test_seed_cme_calendar.py` add tests: the equity wrappers
        return exactly what the seed-taking builders return for `CME_EQUITY_SEED`;
        the seed dataclass is frozen
  - [ ] Run `test_seed_cme_calendar.py`, `test_cme_equity_boundaries.py`,
        `test_migration_057_058_cme_calendar.py`, `test_schema_migrations.py`
        **without editing any existing assertion**
  - [ ] Success: all pass; `git diff main` shows no edit to migration 058 or
        its existing tests

- [ ] **1.3 Commit checkpoint** — `refactor: make CME calendar seed data-driven`

---

## Section 2 — CME_METALS Exceptions (TD1, TD2)

- [ ] **2.1 Gather CME sources** (effort 3)
  - [ ] Follow the 221 method recorded in `seed_cme_calendar.py`'s comments
        (Wayback snapshots of CME's yearly Globex holiday spreadsheets and the
        trading-hours JSON; the live site refuses automated reads)
  - [ ] For each year 2020 → 2027, read the **Metals** row (GC). Never
        extrapolate from another year or from the equity table
  - [ ] Confirm GC hours are 17:00 → 16:00 CT in every year read. A change
        inside the range stops the work and goes to the PM
  - [ ] Expected patterns to confirm per year (not to assume): US holidays halt
        at 13:30 CT from 2022; day after Thanksgiving closes 12:45 CT; Good
        Friday is closed in years equities trade an abbreviated session
  - [ ] Success: a scratch list of (date, name, closed/early_close, time, source
        URL) for every weekday GC did not trade the regular session. Any year
        that cannot be sourced is reported to the PM, not guessed

- [ ] **2.2 Create `seed_cme_metals_calendar.py`** (effort 3)
  - [ ] New module `src/manta_trading/market/schema/seed_cme_metals_calendar.py`
        with `CME_METALS_CALENDAR_ID = "CME_METALS"`, the TD1 calendar row
        (`CME Globex Metals`, `America/Chicago`, 17:00/16:00, no extended hours),
        `CME_METALS_SEED_START`, `CME_METALS_HOLIDAYS_SEEDED_THROUGH = 2027-12-31`
  - [ ] Add `CME_METALS_EXCEPTIONS` using the existing `_closed` / `_early_close`
        helpers (import them; do not copy them). Every row has a `# source:` comment
  - [ ] Add `CME_METALS_SEED`, an instance of `CmeCalendarSeed`
  - [ ] If an early year was unsourceable, set `CME_METALS_SEED_START` to the first
        fully sourced year and file a GitHub issue to widen backward (TD2)
  - [ ] Success: module imports without a cycle; the module carries the metals
        table only, with no logic beyond the helpers

- [ ] **2.3 Unit-test the metals seed** (effort 2)
  - [ ] New `test/unit/test_seed_cme_metals_calendar.py`: every row has a
        source comment (parse the module text); every date is a weekday inside
        `[seed_start, holidays_seeded_through]`; statuses are `closed` or
        `early_close`; early closes have a time and closures have none; no
        duplicate dates; row values match TD1
  - [ ] Add explicit assertions for 2024-11-28 (13:30), 2024-11-29 (12:45),
        2024-12-25 and 2025-01-01 (closed)
  - [ ] Success: tests pass; a mutation (delete a source comment) fails the
        source test

- [ ] **2.4 Run the `pandas_market_calendars` cross-check** (effort 2)
  - [ ] From a scratch directory outside the repo, with
        `uv run --no-project --with pandas_market_calendars`, compare
        `CMEGlobex_GC` sessions for 2020–2027 against `CME_METALS_EXCEPTIONS`.
        Never add the library to the project
  - [ ] Resolve each difference from a CME document (Christmas Eve 2024 is a known
        case: settle from CME's 2024 spreadsheet, not the library)
  - [ ] Record every difference and its resolution in the LLD's Implementation
        Findings
  - [ ] Success: zero unresolved differences; `git status` shows no dependency
        change

- [ ] **2.5 Commit checkpoint** — `feat: add CME_METALS calendar seed with sourced exceptions`

---

## Section 3 — Migration 059 (Database / Storage Schema)

- [ ] **3.1 Add the 059 callable and entry** (effort 2)
  - [ ] In `src/manta_trading/market/schema/migrations/minute.py` add
        `_seed_cme_metals_calendar(conn)` beside `_seed_cme_equity_calendar`, of
        the same three steps: insert the calendar row, `executemany` the
        exceptions, `extend_calendar_sessions(conn, CME_METALS_CALENDAR_ID,
        start=CME_METALS_SEED_START, end=Dec 31 of current_year +
        TRADING_SESSIONS_EXTENSION_YEARS)`. Use the seed-taking builders
  - [ ] Append `059_seed_cme_metals_calendar` after 058, with a `description`
        in the style of 058's. Do not edit 058
  - [ ] Success: callable has a docstring stating idempotence and that NYSE,
        NASDAQ and CME_EQUITY are untouched

- [ ] **3.2 Integration test for 059** (effort 3)
  - [ ] New `test/integration/test_migration_059_cme_metals_calendar.py`, modelled
        on `test_migration_057_058_cme_calendar.py`, on a throwaway database
  - [ ] Assert: the `CME_METALS` row and `holidays_seeded_through = 2027-12-31`;
        every exception row present; sessions populated from seed start to the
        bound; 11-28 closes 13:30 CT and 11-29 closes 12:45 CT in the sessions
        table; no session dated 2024-12-25 or 2025-01-01
  - [ ] Assert byte-identical `NYSE`, `NASDAQ`, `CME_EQUITY` rows, holidays and
        sessions before and after 059 (snapshot, apply, compare)
  - [ ] Assert idempotence: running the callable twice changes nothing
  - [ ] Success: all pass on the test cluster

- [ ] **3.3 Move the migration-chain tail test** (effort 1)
  - [ ] In `test/unit/test_schema_migrations.py` rename `test_chain_ends_at_058`
        to `..._059` and expect `059_seed_cme_metals_calendar`; update the
        stubbed-session-extension helper's docstring/stub if it names only 058
        (line ~102) so 059's callable is also stubbed
  - [ ] Success: `test_schema_migrations.py` passes; the other 058 assertions
        are unedited

- [ ] **3.4 Commit checkpoint** — `feat: add migration 059 seeding the CME_METALS calendar`

---

## Section 4 — Product Mapping and Universe (TD4, TD10)

- [ ] **4.1 Add the GC calendar mapping** (effort 1)
  - [ ] In `src/manta_trading/data/tick/constants.py` import
        `CME_METALS_CALENDAR_ID` from the metals module and add
        `"GC": CME_METALS_CALENDAR_ID` to `FUTURES_PRODUCT_CALENDAR`
  - [ ] Success: `calendar_for_product("GC")` returns `CME_METALS`; the error
        message for an unknown product still lists known products

- [ ] **4.2 Add the GC universe entry** (effort 1)
  - [ ] In `src/manta_trading/data/tick/universe.py` add
        `TickUniverseEntry("GC", ("GC.FUT",), SType.PARENT, tier=TickSchema.TBBO,
        start=date(2024, 11, 2), end=date(2025, 1, 1))` after ES
  - [ ] Success: universe validation passes with ES and GC

- [ ] **4.3 Update `test_universe.py`** (effort 1)
  - [ ] In `test/unit/data/tick/test_universe.py` change the product-list assertion
        to `["ES", "GC"]` and add GC field assertions (symbols, stype, tier tbbo,
        range) plus one assertion that GC maps to `CME_METALS`
  - [ ] Add a constants test for `calendar_for_product("GC")` if the existing
        constants tests list products
  - [ ] Success: tests pass

- [ ] **4.4 Fix the API fixture's synthetic second product** (effort 2)
  - [ ] In `test/integration/api_server/conftest.py` the `two_products` fixture adds
        a synthetic `SECOND` named GC mapped onto `CME_EQUITY`. Rename the root to
        one no CME product uses, together with its symbols in `seed_second_product`
        and every test reference (`grep -rn SECOND test/`)
  - [ ] Keep the fixture's monkeypatch. Correct the "232 needs no change" comment
  - [ ] Success: API integration tests pass, none lists GC twice

- [ ] **4.5 Sweep for other ES-only assumptions** (effort 2)
  - [ ] Run the unit and integration tiers separately. For each failure that
        counts products or assumes `["ES"]` (including tests reading `UNTIERED`
        from `test/tick_support/universe.py`), update it in this task and list it
        by file name in Implementation Findings
  - [ ] Any failure that needs a **src** change outside the two constants is a
        leaked product assumption: stop and report to the PM
  - [ ] Success: both tiers match the baseline from 0.1; `git diff main --stat`
        src changes are limited to seed modules, minute.py, constants.py, universe.py

- [ ] **4.6 Commit checkpoint** — `feat: add GC to the product mapping and tick universe`

---

## Section 5 — Roll Fixture Recorder and Equivalence Test (TD8, code part)

- [ ] **5.1 Add `--product` to the recorder** (effort 2)
  - [ ] In `scripts/record_229_roll_fixture.py` replace the module constant
        `PRODUCT = "ES"` with a required `--product` option; change the docstring
        usage line to match
  - [ ] Resolve `{product}.c.0/.c.1/.v.0/.v.1` and read the product's calendar
        through `calendar_for_product`. Write `{product.lower()}_*.json`
        (replace the hard-coded `es_*` keys at lines ~121–176)
  - [ ] Do not rename or re-read ES's fixtures
  - [ ] Success: `--help` shows `--product` as required; running without it errors

- [ ] **5.2 Parametrize the equivalence test over products** (effort 2)
  - [ ] In `test/unit/data/tick/test_roll_provider_equivalence.py` parametrize over
        the products that have fixtures under `test/fixtures/tick/roll/`; replace the
        hard-coded `ES.{rule}` and `es_*` references. Keep the module docstring
        accurate
  - [ ] GC's fixtures do not exist yet (recorded in Section 9), so GC is added to
        the product list only in task 9.2; at this task ES alone must pass unchanged
  - [ ] Success: the ES cases pass with the same assertions as before

- [ ] **5.3 Commit checkpoint** — `refactor: parametrize roll fixture recorder and equivalence test by product`

---

## Section 6 — Quality Gate Before Production

- [ ] **6.1 Full local gate** (effort 1)
  - [ ] `ruff check` and `ruff format --check` on touched files only. Grep
        `git diff main` for unintended deletions before formatting anything
  - [ ] mypy in one invocation over src kalshi paths and tests
  - [ ] Unit, integration and load tiers separately; compare with the 0.1 baseline
  - [ ] Run `test_pass_contract_parity.py` and record the result (Kalshi contract diff)
  - [ ] Success: no new failures versus baseline; migration 058 and its tests absent
        from the diff

---

## Section 7 — Production Calendar and Free Estimate

- [ ] **7.1 Apply 059 to production** (effort 2)
  - [ ] `uv run mt data migrate status` shows `059_seed_cme_metals_calendar`
        pending, nothing else. Then `uv run mt data migrate apply`
  - [ ] Snapshot `CME_EQUITY`, `NYSE`, `NASDAQ` session counts before and after;
        they must match
  - [ ] Success: `uv run mt data calendars list` shows `CME_METALS` with
        `holidays seeded through 2027-12-31`; walkthrough step 1 sessions output
        shows 13:30 CT on 11-28 and 12:45 CT on 11-29 for `CME_METALS`, and 12:00
        and 12:15 for `CME_EQUITY`; no 12-25 or 2025-01-01 session on either

- [ ] **7.2 Run the free estimate and report** (effort 1)
  - [ ] `uv run mt data tick pass --estimate-only`. Expected: ES plans nothing
        (caught up); GC plans definition and tbbo days 2024-11-03 → 12-31; the
        bundle cost is printed and is refused at the current $0.50 ceiling
  - [ ] Record the printed figure (the number of record), and report it to the PM
        with the two variables to set: `MT_TICK_SPEND_CEILING_USD` and
        `MT_TICK_SPEND_30D_CEILING_USD`
  - [ ] Also ask the PM to confirm TD6's decision point (this purchase counts as one
        of the architecture's rare purchases outside the plan) and TD9's (open
        interest stays deferred)
  - [ ] Success: the figure is in Implementation Findings; the report to the PM is
        sent. **This is where the agent's work on Sections 8 onward is gated by
        the PM's answer.** Do not edit the ceilings

- [ ] **7.3 Commit checkpoint** — `docs: record GC free estimate and 059 production apply`

---

## Section 8 — Purchase and Ingest (PM ceilings set)

*Precondition: the PM has set both ceilings at or above the figure from 7.2 and
confirmed TD6. If not, stop at 7.2.*

- [ ] **8.1 Run the pass** (effort 2)
  - [ ] Re-run `uv run mt data tick pass --estimate-only` and confirm it is within
        the ceilings; then `uv run mt data tick pass`
  - [ ] If the 1,800 s wait ends with a job in flight, the pass prints it with its
        deadline. Re-run `uv run mt data tick pass`, which reconciles and
        downloads before buying anything
  - [ ] Success: GC definition and tbbo units downloaded and verified; no failed
        units; the figure charged matches the estimate

- [ ] **8.2 Run ingest and record timings** (effort 2)
  - [ ] `uv run mt data tick ingest`
  - [ ] Record total wall-clock and the slowest unit
  - [ ] Success: every GC unit is *ingested* with all three checks (counts,
        resolution, session-boundary) passing. Slowest unit ≤ 120 s and the whole
        range ≤ 2 h; a miss fails the slice and is reported to the PM
  - [ ] If a boundary check fails, record the unit and check name and stop: it
        is a calendar-table finding, not a reason to loosen the check

- [ ] **8.3 Ask the PM to restore the per-pass ceiling** (effort 1)
  - [ ] Report that the purchase is complete so the PM can restore the per-pass
        ceiling to its prior value
  - [ ] Success: the report is sent and the ceilings are not edited by the agent

- [ ] **8.4 Commit checkpoint** — `docs: record GC purchase and ingest timings`

---

## Section 9 — Proof Surfaces, Session Model and Roll

- [ ] **9.1 Check the proof surfaces** (effort 2)
  - [ ] `mt data tick status`, `mt data tick coverage --start 2024-11-02 --end
        2025-01-01`, `mt data overview`
  - [ ] Success: a GC line at tier tbbo, caught up; coverage ok on every GC session;
        the overview tick line names ES and GC
  - [ ] Record the post-ingest GC spread share and compare with TD5's 6.6%
        estimate in Implementation Findings

- [ ] **9.2 Session model, too-wide direction (TD7)** (effort 3)
  - [ ] For the session dated 2024-11-28 and the session dated 2024-11-29, run
        `mt data tick get 1m --product GC` with the start/end dates in LLD
        walkthrough step 5 (dates are session dates)
  - [ ] Check each metals exception session in the range, including the Christmas
        and New Year closures, which must have no bars
  - [ ] Success: the 11-28 last bar is 13:29 CT (19:29 UTC); the 11-29 last bar is
        12:44 CT (18:44 UTC); the next bar of each is at or after the following 17:00
        CT open. Record every exception session's last-bar time in Implementation
        Findings. A miss means the seed is too wide: stop and report

- [ ] **9.3 Verify the roll and the API** (effort 2)
  - [ ] `uv run mt data tick active --product GC --start 2024-11-04 --end 2025-01-01`
  - [ ] Start the API (see the runbook) and run the two curl commands from LLD
        walkthrough step 7
  - [ ] Success: under `v.0` GCZ4 serves until 2024-11-29 and GCG5 after; the first
        two sessions are unresolved; the API lists GC on `CME_METALS` and its
        schedule segments match the `active` output. No surface code changed

- [ ] **9.4 Record the GC fixtures and extend the equivalence test** (effort 3)
  - [ ] `uv run python scripts/record_229_roll_fixture.py --product GC` (free
        metadata and `symbology.resolve` calls only)
  - [ ] Add GC to the product list in `test_roll_provider_equivalence.py`
  - [ ] Success: the test passes for ES and GC at `VOLUME_RANK_LAG_SESSIONS = 2`. If
        GC's `v.0` disagrees with the ledger, **stop and report to the PM**: do not
        add a per-product lag

- [ ] **9.5 Commit checkpoint** — `test: verify GC session model and roll against Databento`

---

## Section 10 — Documentation (Technical Requirements)

- [ ] **10.1 Update the data-correctness contract** (effort 1)
  - [ ] In `user/reference/data-correctness-architecture.md` add to the I4 row
        "232: `CME_METALS` behind the same surface"; to the I13 row "232: GC units
        pass the session-boundary check; too-wide check in the walkthrough"; and
        232's rows to the slice-mapping table
  - [ ] Success: the three edits are present, with front matter untouched

- [ ] **10.2 Add "Adding a futures product" to the runbook** (effort 2)
  - [ ] In `user/runbooks/100-production-operations.md`, under "Operating the tick
        tier", add the section: seed and migration, mapping, universe entry, free
        estimate, PM ceilings, pass, ingest, and the two-direction session check
  - [ ] Do not hard-code job IDs. Refer to commands, not remembered output
  - [ ] Success: a reader can follow it for a third product without the LLD

- [ ] **10.3 Update the slice plan** (effort 1)
  - [ ] In `user/architecture/220-slices.*.md`: set slice 232's effort to 3/5; update
        Future Work item 2 with the GC evidence (`GC.n.0` rolled 2024-11-21 against
        `GC.v.0` 2024-11-29) and the trigger (first subscription month, beside the
        `ohlcv` history pull, or earlier if a consumer asks; 234's design asks again
        if no subscription date is set); record the PM's TD9 answer
  - [ ] Success: Future Work item 2 states the trigger; the plan matches TD9

- [ ] **10.4 Add the architecture Revision Log entry** (effort 1)
  - [ ] In `user/architecture/220-arch.*.md` Revision Log add the 232 entry stating:
        separate calendars supersede the "Session model" statement that one row
        serves ES and GC; the 232 open-interest decision against "Active contract
        and roll methods"; GC's measured spread share
  - [ ] Success: the entry is dated and names all three

- [ ] **10.5 Final gate** (effort 1)
  - [ ] Re-run ruff, mypy (one invocation), the three test tiers separately and the
        parity test; compare with baseline
  - [ ] Confirm `git diff main` src changes are only: the two constants, seed modules,
        the migration list, and the recorder script
  - [ ] Success: no new failures versus baseline; diff scope as stated

- [ ] **10.6 Finalize the LLD and commit** (effort 1)
  - [ ] Replace the draft walkthrough with the as-run commands and results; complete
        Implementation Findings (baseline, cross-check differences, ES-only test
        updates, timings, spread share, last-bar times, parity result)
  - [ ] Set the LLD and this task file `status: complete` only when every box above
        is checked
  - [ ] Success: every Functional Requirement 1–9 in the LLD has a recorded result
  - [ ] Commit: `docs: complete slice 232 documentation and findings`. The work ends
        at this commit on the slice branch
