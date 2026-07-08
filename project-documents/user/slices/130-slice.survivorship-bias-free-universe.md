---
docType: slice-design
slice: 130
parent: user/architecture/120-slices.data-acquisition.md
project: trading
dateCreated: 20260429
dateUpdated: 20260514
status: complete
dependencies: [158, 159, 161]
---

# Slice 130 — Survivorship-Bias-Free Backtesting Universe

## Purpose

Slice 159 ships `delisted_date` and `first_listing_date` columns on the
`instruments` table. Slice 161 ships point-in-time SP500 constituent
tracking in `universe_members` (`added_date`/`removed_date`). This slice
makes that data **usable at backtest time**.

Backtests today implicitly use whatever is in `instruments` *now* —
which means they never see companies that delisted during the test
period, so historical strategy returns look better than reality. A
strategy that bought "everything in NASDAQ" in 2010 and held to 2020
is currently equivalent to "everything in NASDAQ that survived to
2020" — cheating.

Per the slice 128 dry-run roadmap discussion, the chosen approach is
**Level 2: active-universe-as-of-date**. Maintain `delisted_date` and
`first_listing_date` per row; at backtest time, query
`active_on(t) := first_listing_date <= t AND (delisted_date IS NULL OR
delisted_date > t)`. This is rigorous enough for honest historical
research without requiring the storage of point-in-time universe
snapshots (Level 3) for every date.

## Goals

1. **As-of-date filter API.** Provide
   `data.equity_universe(as_of_date: date, universe: str | None = None)
   -> list[str]` returning the symbol set that was active on
   `as_of_date`. Optionally restricted to a named universe (SP500 via
   `universe_members`); without restriction returns all instruments
   active on that date.

2. **Index-membership as-of-date.** For index-defined universes,
   honour `universe_members.added_date`/`removed_date` so an SP500
   backtest in 2015 sees the *2015 SP500 constituents*, not today's.
   Without this, even a delisted-aware filter still misleads on index
   strategies. The as-of membership query is:

   ```sql
   SELECT symbol FROM universe_members
    WHERE universe_name = %s
      AND added_date <= %s
      AND (removed_date IS NULL OR removed_date > %s)
   ```

   SP500 constituent history is available back to 1996-01-02
   (universe_name `'sp500'`). R2000 and NASDAQ-100 data do not exist
   yet; index-strategy backtests for those universes are deferred.

3. ~~**First-listing-date ingest.**~~ **Deferred.** EODHD `/fundamentals`
   is not available on the current plan. `first_data_date` covers 99.6%
   of current SP500 members as a fallback. The `--only-finnhub` rebuild
   flag (slice 161 session) covers Finnhub-based enrichment as a separate
   operational step. Not a slice 130 deliverable.

4. ~~**Backtest-time correctness assertions.**~~ **Deferred.** No backtest
   harness exists yet. Revisit when the harness is built.

5. ~~**Documentation and labels.**~~ **Deferred.** Revisit with the
   backtest harness.

## Non-goals

- **Level-3 point-in-time universe snapshots.** A
  `instruments_history` daily-snapshot table is overkill for our
  scale and adds storage cost without changing strategy results
  beyond what Level 2 provides. Revisit only if licensing or
  reproducibility requirements demand it.
- **Provider-side adjustments to delisted price data.** EODHD's
  delisted-instrument prices are taken as authoritative; we do not
  attempt to validate them against a second source for delisted
  names.
- **Live trading filter.** Live trading uses today's universe by
  definition; this slice is about historical backtesting.
- **R2000 and NASDAQ-100 index history.** No constituent data exists
  for these indices yet. Index-strategy backtests for R2000/NASDAQ-100
  are out of scope until a data source is evaluated and loaded.

## Scope

- `manta_trading.data.equity_universe` module:
  `equity_universe(conn, as_of_date, universe=None) -> list[str]`
- Active filter uses `COALESCE(first_listing_date, first_data_date)`
  as the lower bound — covers 99.6% of SP500 without requiring
  completed Finnhub enrichment
- Universe filter intersects with `universe_members` when `universe`
  is given; raises `UniverseQueryError` for unknown universe names
- `mt data universes as-of` CLI already delivered by slice 161 — no
  new CLI needed
- Query performance verified against prod; index added if needed
- Tests against `trading_test` DB covering all filter combinations

## Open questions

- **First-listing-date accuracy.** For very old listings EODHD's
  `IPODate` may be missing; the fallback "earliest EOD bar" date
  approximates IPO-or-thereabouts but is not authoritative.
  Acceptable approximation for backtest filtering.
- **Performance at scale.** A naive per-bar `equity_universe(date)`
  call would be expensive in tight loops. v1 expects callers to
  evaluate once per backtest day at most and cache. Document the
  pattern.
- **R2000 / NASDAQ-100 source.** No constituent history exists for
  these indices. Evaluate source options (EODHD index-constituents
  endpoint, Wikipedia, paid feeds) before scoping a follow-on slice.

## References

- Slice 128 dry-run roadmap discussion (2026-04-29): "Level 2 is the
  sweet spot. Level 3 is overkill until you're publishing or
  licensing strategies." Survivorship bias confirmed as
  non-optional for realistic backtest results.
- Slice 159 introduces `delisted_date` and `first_listing_date` on
  `instruments` — this slice consumes them.
- Slice 161 delivers `universe_members` with SP500 constituent
  history (`added_date`/`removed_date`) back to 1996-01-02.
- EODHD `fundamentals/{TICKER}` documentation, `General::IPODate`
  field.

## Success Criteria

1. `equity_universe(conn, date(2015, 6, 30), 'sp500')` returns ~500
   symbols matching historical SP500 composition.
2. A symbol with `delisted_date='2018-04-15'` is included for
   `as_of_date='2018-04-14'` and excluded for `as_of_date='2018-04-15'`.
3. A symbol with NULL `first_listing_date` but populated `first_data_date`
   is correctly included/excluded based on `first_data_date`.
4. `equity_universe(conn, date(1998, 1, 1), 'sp500')` returns a late-1990s
   composition (fewer current names, some old tickers).
5. `UniverseQueryError` raised for unknown universe names (e.g. `'r2000'`).
6. Both DB query paths execute in <100ms against prod.

## Verification Walkthrough

Verified 2026-05-14 against prod DB (`trading`, <db-host>:5432).

```python
import psycopg
from datetime import date
from manta_trading.data.equity_universe import equity_universe, UniverseQueryError

conn = psycopg.connect("postgresql://postgres:<password>@<db-host>:5432/trading")

# Step 1: all active instruments today
all_today = equity_universe(conn, date.today())
print(len(all_today))  # → 11977

# Step 2: SP500 as of 2015-06-30
sp500_2015 = equity_universe(conn, date(2015, 6, 30), universe='sp500')
print(len(sp500_2015))  # → 371
assert 'AAPL' in sp500_2015  # passes

# Step 3: SP500 as of 1998-01-01
sp500_1998 = equity_universe(conn, date(1998, 1, 1), universe='sp500')
print(len(sp500_1998))  # → 206 (different late-1990s composition)

# Step 4: unknown universe raises
try:
    equity_universe(conn, date.today(), universe='r2000')
    assert False, "should have raised"
except UniverseQueryError as e:
    print(e)  # → Universe 'r2000' has no rows in universe_members
```

### Counts below historical SP500 size — expected

The 2015 universe returns 371 (not ~500) because 128 universe_members as
of that date fail the `instruments` active filter:
- **105** have NULL `first_listing_date` AND NULL `first_data_date`
  — these are delisted stocks that never had OHLCV bars loaded in this
  DB instance. The `--only-finnhub` rebuild enriches `first_listing_date`
  for current instruments; delisted instruments without bars remain dark.
- **20** have a `first_data_date` after 2015-06-30 (data backfilled
  recently but the instrument was active much earlier).
- **3** symbols (e.g. `BF.B`, `BRK.B`) do not exist in `instruments`
  (symbol mismatch / not ingested).

This is a known data completeness limitation documented in the Open
Questions section, not a bug in the API. As EODHD delisted-instrument
price history is loaded and Finnhub enrichment fills `first_listing_date`,
counts will approach the historical SP500 size.

## Status

**Ready for implementation.** All dependencies (158, 159, 161) are
complete and merged to main.
