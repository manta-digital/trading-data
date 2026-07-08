---
docType: slice-design
slice: 129
parent: user/architecture/120-slices.data-acquisition.md
project: trading
dateCreated: 20260429
dateUpdated: 20260429
status: complete
dependencies: [127, 128]
dateUpdated: 20260512
closureNote: |
  Closed 20260512. Scope absorbed by slices 141–157 via a different
  (better) architecture: symbol_list replaced by instruments table
  (slice 141); named universes delivered as YAML-file symbol lists
  (slice 146, config/symbol-lists.yaml + mt data lists); daemon scoping
  via --list flag (slice 146); HISTORY_MONTHS replaced by per-symbol
  MT_MINUTE_HISTORY_START floor (slice 154/P08); symbol normalisation
  exists in providers (_normalise_symbol); first_listing_date on
  instruments (slice 141). Remaining items (--include-delisted,
  data_source tagging) planned for slices 158/159. No formal
  protocol seam (IUniverseProvider) was built — instruments rebuild
  from EODHD is the universe source and no seam was needed.
---

# Slice 129 — Named Universes, EODHD Universe Source, and Symbol Hygiene

## Purpose

Slice 128 cuts production over to EODHD for daily, minute, and corporate
actions. The `symbol_list` table is still derived from AlphaVantage's
`LISTING_STATUS` endpoint, which (a) excludes ~6,000 US-listed
instruments EODHD covers, (b) carries its own survivorship bias, and (c)
contains malformed entries that EODHD rejects with HTTP 422.

Equally important: today the minute daemon defaults to `HISTORY_MONTHS=24`
(an AV-era constant) when fetching history for a fresh symbol, and there
is no way to say "fetch only this universe (e.g. SP500), with this much
history." Either you get the daemon's whole-universe slow march, or you
hand-call `mt data minute backfill --universe X --since DATE` with no
real "X" to point at because we don't have named universes.

This slice replaces the AV-derived universe with an EODHD-derived
universe **defined as named, configurable subsets** (SP500, R2000, NYSE,
"watchlist:erik-core", etc.) with **per-universe history-depth config**
and **on-demand backfill** when a backtest references something we
don't have. We do not blanket-fetch 22 years of 13,000 symbols.

## Goals

1. **Named universes.** Introduce a `universes` table (or YAML config —
   decide in scope expansion) with rows like
   `(name, description, member_query, history_months, last_refreshed)`.
   Members defined either statically (list of symbols) or dynamically
   (SQL/filter expression evaluated against the current `symbol_list`,
   e.g. "all NYSE common-stock with market_cap >= 1B"). Examples to
   ship: `sp500`, `r2000`, `nasdaq100`, `manual:default`. SP500/R2000
   memberships seeded from EODHD's index-constituents endpoint or a
   static CSV checked into the repo for v1.

2. **Per-universe history-depth.** Each universe carries a
   `history_months` value. SP500 might be 240 months (20 years), a
   short-term-trading watchlist might be 24 months, etc. This becomes
   the lookback when the daemon or backfill command initializes a
   fresh symbol in that universe. Replaces the global `HISTORY_MONTHS`
   default.

3. **Daemon scoped to a universe set.** `mt data minute daemon
   --universes sp500,r2000` (or via env `MT_ACTIVE_UNIVERSES`) limits
   the daemon's work queue to the union of those universes' members
   instead of every active instrument. Avoids the daemon spending
   weeks of quota on instruments nobody will backtest.

4. **EODHD as universe source.** New `IUniverseProvider` seam with
   `EODHDUniverseProvider` first implementation pulling
   `exchange-symbol-list/US` and (optionally) `?delisted=1`. Replaces
   `mt data daily symbols` (which was AV-specific).

5. **On-demand backfill.** New CLI:
   `mt data minute fetch --symbol SYM --since DATE [--until DATE]`
   for backtest-driven gap-filling. When a strategy references a
   symbol we don't have full history for, the operator runs this once
   and the data lands. Eventually the strategy code itself can call
   the same path, but for v1 it's an operator command.

6. **Per-row provider tagging in `symbol_list`.** Add a `data_source`
   column (`eodhd`, `alphavantage`, `manual`, `unknown`) so the
   daemons skip instruments whose data_source can't serve them.
   1,054 AV-only SPAC variants stay in the table tagged
   `data_source='alphavantage'` and never hit EODHD's 404 path.

7. **Symbol normalisation.** Single-source mapping for slash-separated
   tickers (`BC/PA → BC-PA` or `BC.PA`) and quarantine for
   structurally malformed entries (`NXT(EXP20091224)`, `TEST_ERROR`).
   Daemons consult the normaliser before calling out to providers.

## Non-goals

- **Survivorship-bias filtering at backtest time.** That's slice 130.
  This slice ships the data needed (`delisted_date`, named universes
  with as-of memberships) but does not implement the backtest-time
  `as_of_date` query API.
- **Backfilling 22 years × every active US instrument.** Daemon
  default lookback is per-universe-configured; deeper history is
  on-demand via `mt data minute fetch`.
- **Polygon/Finnhub universe sources.** Same seam pattern would host
  them; not implemented this slice.
- **Pruning `dailyohlcvadjusted` / `minute_ohlcv`.** Existing data
  stays.
- **Strategy-side `data.minute_bars(...)` API.** That's the scope
  boundary toward backtesting infrastructure (separate initiative).

## Scope outline (to be expanded into tasks before implementation)

### Schema

- Add to `symbol_list`:
  - `data_source TEXT NOT NULL DEFAULT 'unknown'`
  - `delisted_date DATE NULL`
  - `first_trade_date DATE NULL` (populated where EODHD provides it
    via fundamentals; required by slice 130 for as-of filtering)
- Backfill `data_source` for existing rows: everything pre-129
  becomes `'alphavantage'`.
- New table `universes` with columns:
  `(name TEXT PRIMARY KEY, description TEXT, definition_type TEXT,
    definition JSONB, history_months INT NOT NULL, created_at, updated_at)`.
  `definition_type ∈ {'static', 'index', 'filter'}`; `definition` is
  shape-dependent (member list, EODHD index code, or SQL filter).
- New table `universe_members` with columns:
  `(universe_name FK, symbol TEXT, added_on DATE, removed_on DATE NULL)`.
  Captures point-in-time membership for index-tracking universes
  (SP500 reconstitutions etc.). Used by slice 130 for as-of filtering.

### Provider seams

- `manta_trading.data.universe.providers.IUniverseProvider` with
  `fetch_active() -> list[InstrumentMetadata]` and
  `fetch_delisted() -> list[InstrumentMetadata]`.
- `EODHDUniverseProvider` implements both via
  `exchange-symbol-list/US` and `?delisted=1`. Optional
  `fetch_index_constituents(index_code)` for SP500/R2000/etc.

### CLI

- New: `mt data symbols refresh [--provider eodhd]
  [--include-delisted] [--dry-run]` — replaces `mt data daily symbols`.
- New: `mt data universes list|show|create|update|delete` — manage
  named universes and their member sets.
- New: `mt data universes refresh-members [--name NAME]` — re-pull
  index constituents from EODHD for index-defined universes.
- New: `mt data minute fetch --symbol SYM --since DATE [--until DATE]`
  — on-demand single-symbol gap fill. Reuses the existing
  `update_symbol` orchestrator path.
- Modify: `mt data minute daemon --universes name1,name2,...` — work
  queue restricted to union of named-universe members.
- Modify: `mt data minute backfill` — `--universe NAME` now resolves
  through the named-universe table, not the daemon's symbol-source.

### Daemon and orchestrator changes

- Work-queue builder reads `universes` + `universe_members` to define
  the eligible symbol set when `--universes` flag/env is set.
- Per-symbol `HISTORY_MONTHS` lookup: when initializing a fresh
  symbol, use the `history_months` of the universe(s) the symbol
  belongs to (max if multiple). Falls back to a conservative default
  (60 months suggested) if symbol has no universe membership.
- Symbol normalisation: orchestrator calls
  `normalize_symbol_for_provider(symbol, provider)` before request.
  Quarantined symbols never get fetched (logged once, skipped).

### Bootstrapping universes for v1

- Static CSV checked into repo: `data/universes/sp500.csv`,
  `r2000.csv`, `nasdaq100.csv`. One-time seed via
  `mt data universes create --name sp500 --from-csv data/universes/sp500.csv`.
- `manual:default` universe: a small operator-curated list (~20–50
  symbols of personal interest) for low-noise daily testing.
- Migration step seeds these on first apply.

## Open questions

- **Index-constituent licensing.** EODHD's index-constituents endpoint
  may have separate licensing terms; verify before relying on it
  programmatically. Static CSV from public sources (Wikipedia, S&P
  press releases) is a fallback.
- **Filter-defined universes.** SQL filters in `definition` are
  flexible but a query-injection risk if exposed via API later. v1
  defines the filter language as a small whitelist (allowed columns,
  operators) rather than raw SQL.
- **AV-only SPAC retention.** Confirm with PM whether to keep at all.
  Default: keep, tagged `alphavantage`, daemon skips them.
- **EODHD-only filter by Type.** 6,162 EODHD-only tickers include
  many mutual funds. Default v1 import gates on
  `Type IN ('Common Stock', 'ETF', 'Preferred Stock')` — adds ~5,000
  rather than ~6,000. Funds reachable by explicit
  `--include-types fund,mutual_fund` later.

## References

- Slice 128 §10.2 dry-run findings: AV-derived universe at 8,010
  active symbols; EODHD active US-listed = 13,118; intersection =
  6,956; AV-only = 1,054 (mostly SPAC warrants/units); EODHD-only =
  6,162. Daemon `HISTORY_MONTHS=24` default → fresh symbols only get
  2 years of history without explicit backfill.
- EODHD documentation: `exchange-symbol-list/{EXCHANGE}`,
  `?delisted=1` parameter, `index-symbols/{INDEX}` endpoint.
- Verified live 2026-04-29: EODHD `?delisted=1` returns 54,119 US
  delisted tickers including 29,048 delisted common stocks.

## Status

**Scoped only — not yet planned for implementation.** Wait for slice
128 production cutover to complete and stabilise before starting
tasks.
