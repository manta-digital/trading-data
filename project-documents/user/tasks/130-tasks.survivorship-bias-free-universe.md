---
docType: tasks
slice: survivorship-bias-free-universe
project: trading
lld: user/slices/130-slice.survivorship-bias-free-universe.md
dependencies: [158, 159, 161]
projectState: >
  Slices 158, 159, and 161 are complete and merged to main. The instruments
  table has delisted_date (18,741 populated) and first_listing_date (379
  populated; first_data_date covers 99.6% of SP500 as fallback). The
  universe_members table has SP500 constituent history back to 1996-01-02
  (503 current active members). mt data universes as-of already exists
  (slice 161). Goals 3 (EODHD fundamentals ingest), 4 (harness assertions),
  and 5 (report labels) are deferred — out of scope for this slice. The
  --only-finnhub rebuild flag (added this session) covers first_listing_date
  enrichment as a separate operational step, not a slice 130 deliverable.
dateCreated: 20260514
dateUpdated: 20260514
status: complete
---

## Context Summary

- Core deliverable: `manta_trading.data.equity_universe` module with
  `equity_universe(conn, as_of_date, universe=None) -> list[str]`
- Active filter: `first_listing_date <= as_of_date AND (delisted_date IS NULL OR
  delisted_date > as_of_date)`, using `first_data_date` as fallback when
  `first_listing_date IS NULL`
- Index filter: when `universe` is given, intersect with `universe_members`
  using `added_date <= as_of_date AND (removed_date IS NULL OR removed_date > as_of_date)`
- `mt data universes as-of` CLI already exists — not duplicated here
- Key files to create:
  - `src/manta_trading/data/equity_universe.py` — NEW: core API
  - `test/unit/data/test_equity_universe.py` — NEW
- No new migrations needed

---

## Tasks

- [x] **T01 — Branch setup**
  - [x] Confirm on `main`; create branch
    `130-slice.survivorship-bias-free-universe`
  - [x] Confirm clean working tree

- [x] **T02 — Create `equity_universe.py` module**
  - [x] Create `src/manta_trading/data/equity_universe.py` with:
    - `equity_universe(conn, as_of_date: date, universe: str | None = None) -> list[str]`
    - Active-on-date filter using `instruments`:
      ```sql
      SELECT symbol FROM instruments
      WHERE COALESCE(first_listing_date, first_data_date) <= %(d)s
        AND (delisted_date IS NULL OR delisted_date > %(d)s)
      ORDER BY symbol
      ```
    - When `universe` is provided, intersect with `universe_members`:
      ```sql
      SELECT symbol FROM universe_members
      WHERE universe_name = %(u)s
        AND added_date <= %(d)s
        AND (removed_date IS NULL OR removed_date > %(d)s)
      ORDER BY symbol
      ```
      Then return the intersection: symbols in both result sets.
    - `UniverseQueryError(RuntimeError)` — raised if `universe` is given
      but has no rows in `universe_members` at all (unknown universe name)
  - [x] Module imports cleanly; `equity_universe` is the sole public name
  - [x] Success: function returns a non-empty `list[str]` for a known date

- [x] **T03 — Tests: `equity_universe` core logic**
  - [x] Create `test/unit/data/test_equity_universe.py` using psycopg
    against `trading_test` DB with fixture data inserted/cleaned per test:
    - Active filter — symbol with `first_listing_date <= date` and no
      `delisted_date` is included
    - Delisted filter — symbol with `delisted_date <= date` is excluded;
      symbol with `delisted_date > date` is included
    - `first_data_date` fallback — symbol with NULL `first_listing_date`
      but `first_data_date <= date` is included
    - Both NULL — symbol with neither date populated is excluded
    - Universe filter — `universe='sp500'` returns only symbols in
      `universe_members` that are also active in `instruments`
    - Universe as-of — a symbol added to sp500 after `as_of_date` is
      excluded; a symbol removed before `as_of_date` is excluded
    - Unknown universe — `UniverseQueryError` raised
    - No-universe — all active instruments returned regardless of index
      membership
  - [x] Run `pytest test/unit/data/test_equity_universe.py` — all pass
  - [x] **Commit checkpoint:**
    `feat: add equity_universe API (slice 130)`

- [x] **T04 — Query performance check**
  - [x] Against prod DB (`trading`, <db-host>:5432), run EXPLAIN ANALYZE
    on both query paths:
    ```sql
    EXPLAIN ANALYZE
    SELECT symbol FROM instruments
    WHERE COALESCE(first_listing_date, first_data_date) <= '2015-06-30'
      AND (delisted_date IS NULL OR delisted_date > '2015-06-30')
    ORDER BY symbol;
    ```
    ```sql
    EXPLAIN ANALYZE
    SELECT symbol FROM universe_members
    WHERE universe_name = 'sp500'
      AND added_date <= '2015-06-30'
      AND (removed_date IS NULL OR removed_date > '2015-06-30')
    ORDER BY symbol;
    ```
  - [x] If either query uses a sequential scan on a large table and planning
    time + execution time exceeds 100ms, add a targeted index and document
    it as a new migration
  - [x] Success: both queries return in <100ms on prod

- [x] **T05 — Full test suite + commit**
  - [x] Run `pytest test/unit/` — all existing and new tests pass; no
    regressions
  - [x] Commit:
    `feat: add survivorship-bias-free equity_universe API (slice 130)`
    Stage all modified/new files under `src/` and `test/`

- [x] **T06 — Update slice design and tasks frontmatter**
  - [x] Update `130-slice.survivorship-bias-free-universe.md`:
    - Remove Goals 3, 4, 5 (deferred) and update Scope outline to
      reflect actual implementation
    - Note that `mt data universes as-of` CLI is already delivered by
      slice 161 — no new CLI needed
    - Add note that `--only-finnhub` rebuild covers first_listing_date
      enrichment as an operational step outside this slice
    - Set `status: complete` and `dateUpdated: today`
  - [x] Update `130-tasks.survivorship-bias-free-universe.md`
    `status: complete`

- [x] **T07 — Verification walkthrough**
  - [x] Follow the verification steps in `130-slice.md` §Verification
    Walkthrough against the `trading` DB:
    1. Python REPL: import `equity_universe`, call with a historical date
       and verify symbol count is reasonable
    2. Call with `universe='sp500'` and `as_of_date='2015-06-30'` —
       confirm ~500 symbols, all matching historical SP500 composition
    3. Call with `universe='sp500'` and `as_of_date='1998-01-01'` —
       confirm count reflects late-1990s SP500 (fewer current names)
    4. Confirm a known delisted symbol (e.g. `ENRNQ`) is absent for
       dates after its delisting and present for dates before
    5. Confirm `UniverseQueryError` for `universe='r2000'` (no data)
  - [x] Record actual output in the slice's Verification Walkthrough
    section
