---
title: "152 — Consolidation: demolition + migration (one DB, raw schema)"
slice: 152
initiative: 140
status: complete
phase: 6
type: refactor
effort: 3
tags: [refactor, demolition, migration, consolidation, marketdb-removal, caggs]
created: 20260505
dateUpdated: 20260505
dateCompleted: 20260505
author: pm+claude
docType: slice-design
project: trading
dateCreated: 20260505
dependsOn: [145, 146, 147, 148]
supersedes: [149, 150, 151]
---

# Slice 152 — Consolidation: demolition + migration

> Scope note: this slice covers demolition, migration, and cagg rebuild
> only. Adjusted-on-read function and DB read layer → slice 153.
> CLI surface and daemon bulk-EOD → slice 154.

## What's broken and what we're doing about it

The OHLCV pipeline today writes adjusted prices as columns (`adj_open/high/low/close`, `k_factor`, `adjusted_at`) onto every bar at ingest time. When corporate actions change, a daemon recomputes those columns. When the recompute misses an edge case, stored adjusted values silently rot. Slice 149 was designed to detect this rot and on its first run surfaced ~$0.20 per-session drift on AAPL, traceable to a legacy table the writer was reading. The pipeline also straddles two databases (TimescaleDB and a legacy MarketDB), each with its own copy of partial truth. We fix it by:

- Storing only **raw** OHLCV. No `adj_*` columns, no `k_factor` column, no `adjusted_at` column.
- Computing adjusted prices **on read** via a single function. No daemon, no drift detector, no audit, no snapshot id.
- One database. MarketDB is unplugged. `splits` and `dividends` migrate into TimescaleDB. The legacy `dailyohlcvadjusted` table is dropped.
- AlphaVantage is fully removed (config, providers, profiles, agents, news, util, tests). EODHD is the only OHLCV provider.

## What the new architecture looks like

**One database: TimescaleDB.** No second DB, no cross-DB queries, no consistency questions about which copy is right. MarketDB is gone — not "unused-but-still-there"; the URL is unset and the connection class is deleted.

**One ingest path per granularity.** Daily and minute writers each pull from EODHD and upsert raw OHLCV into `daily_ohlcv` / `minute_ohlcv`. Continuous aggregates project raw columns. No band writer, no per-row k_factor.

**Two tables for corporate actions: `splits` and `dividends`** (mirror the architecture's existing schema, just relocated to TimescaleDB). Same columns, same indexes, same semantics — only the host DB changes. Migration is a one-shot copy from MarketDB.

**One adjustment function:** `adjusted(bars, symbol, conn, *, ca_snapshot=None) -> bars`. Reads `splits` and `dividends`, looks up `prev_close` from `daily_ohlcv`, applies k-factor per bar's date, returns adjusted bars. Pure function over its inputs. Optional `ca_snapshot` for replay-against-historical-state if anyone ever wants it; default is read live. ~80 lines.

**Daemon stays as-is** (cycle scheduling, advisory locks, quota bucket, lists, CA fetch). The daemon's per-symbol body drops the band-writer call and the drift-detector call. Bulk-EOD switch (slice 146 plan deferred to here): daily steady-state uses `/eod-bulk-last-day` for the full exchange instead of N per-symbol `/eod` calls. Per-symbol `/eod` stays for backfill and `mt data refetch`.

## Continuous aggregates

**Slice 150 was the previous answer to the cagg problem and is superseded by 152.** Slice 150 proposed dropping the 11 legacy minute caggs (`*_v2` duplicates from a column-order mistake) and recreating 7 caggs that project the *adjusted* columns (`adj_open/high/low/close`). That was the right shape for adjusted-on-write. Under adjusted-on-read it is the wrong shape: there are no adjusted columns to project. The cagg work in slice 150 — drop legacy, recreate seven shapes, install refresh policies, fix `AGGREGATION_VIEWS`, align the bootstrap path — is *re-used* here, but the projection changes.

**What's still in scope under 152.** All of the structural cagg work from 150, with the projection swapped to raw OHLCV:

- Drop all 11 legacy minute caggs and the bootstrap path's duplicate creation in `timescale_init.create_continuous_aggregations`. CASCADE removes the policy jobs.
- Recreate 7 minute caggs over `minute_ohlcv` (5min, 15min, 1h, 4h, 1d, 1w, 1mo) projecting raw columns:
  ```sql
  SELECT
      time_bucket('<interval>', time) AS time_bucket,
      symbol,
      FIRST(open,  time) AS open,
      MAX(high)          AS high,
      MIN(low)           AS low,
      LAST(close,  time) AS close,
      SUM(volume)        AS volume,
      COUNT(*)           AS minute_count
  FROM minute_ohlcv
  GROUP BY time_bucket, symbol;
  ```
  No `WHERE adj_close IS NOT NULL` filter (the adjusted columns no longer exist). No filtering at all — every raw row participates.
- Install one refresh policy per cagg. Cadences from slice 150's table are a good starting point; under adjusted-on-read there are no late-arriving adjustment rewrites, so `start_offset` can be tighter (the only reason to widen it now is late-arriving raw bars from a backfill, which is bounded by the daemon's backfill window).
- Add **3 new daily caggs over `daily_ohlcv`**: `daily_weekly_ohlcv`, `daily_monthly_ohlcv`, `daily_quarterly_ohlcv` (or whichever timeframes we actually want — TBD with PM, but at minimum weekly and monthly so consumers can ask `daily` / `weekly` / `monthly` against the EODHD-sourced daily series the same way they ask the minute-derived series). Same raw projection shape. Refresh policies sized for end-of-day cadence.
- Update `TimescaleMinuteDataDB.AGGREGATION_VIEWS` to drop `_v2` suffixes. Update `timescale_init.create_continuous_aggregations` to match the new projection (or remove and rely on the migration as the only path).

**What's *not* needed under 152.** A few things slice 150 spent time on are no longer concerns:

- **No "consistency for an imaginary reason."** Slice 150 had to keep `_v2` and non-`_v2` views straight while the band writer continued populating `adj_*`. Under 152 the band writer is gone before the migrations run; there's nothing to coordinate with.
- **No drift-detector cagg refresh path.** `ca_drift.refresh_caggs_in_range` and the `MINUTE_CAGGS` constant in `daemon/ca_drift.py` go away with the rest of `ca_drift.py`. Caggs refresh purely on their own policies; nothing rewrites historical bars after a CA, so the daemon never needs to nudge them.
- **No adjusted-column verification.** Slice 150's success criteria #4 ("each view's definition uses `FIRST(adj_open, time) ...`") and #8 ("adjusted prices continuous across split windows") have no analogue here. Verification is "view exists, projects raw, refresh policy installed, sample query returns rows." Continuity across splits is the read-side adjustment function's job, tested separately.
- **No `WHERE adj_close IS NOT NULL` race condition.** The slice 150 filter exists to hide rows the writer hasn't visited yet. Without a writer, every raw row qualifies the moment it lands.

**Backfill / how full the caggs get and when.**

- Caggs are derived state. After migration they start empty and fill on first refresh. A manual `CALL refresh_continuous_aggregate('<view>', NULL, NULL)` per view, run once after migration, materializes the full history. Acceptable cost: bounded by `minute_ohlcv` size (large but finite) and `daily_ohlcv` size (small).
- Minute caggs are **as full as `minute_ohlcv` is**. Today minute history is sparse — the 145/146 daemon writes minute data forward from when it was turned on, not backward. That's not a 152 problem. If we want deep minute history later, that's a separate backfill initiative against the EODHD intraday endpoint (or a Databento ingest under the future tick-data slice). 152 doesn't try to fix it.
- Daily caggs are as full as `daily_ohlcv` is, which is essentially complete for symbols the daemon has touched. So daily / weekly / monthly caggs over `daily_ohlcv` are usable immediately after the post-migration refresh.

**How much can be restarted vs. preserved.** All of it can be restarted — caggs are derived. `DROP MATERIALIZED VIEW ... CASCADE` is safe by construction; the source hypertables (`minute_ohlcv`, `daily_ohlcv`) are not touched. Operator cost is one full refresh per cagg after the migration, which is the only time we pay it.

## Where caggs come from

Slice 150 had caggs only over `minute_ohlcv`. The previous draft of this section proposed adding caggs over `daily_ohlcv` for weekly/monthly. **Withdraw that proposal.** Two competing rollups (a weekly bar derived from minutes vs. a weekly bar derived from EODHD daily) is exactly the kind of "two copies of partial truth" this slice exists to eliminate. One source of truth per timeframe.

**Rule:** every aggregate timeframe is derived from the finest-grained source available *for that symbol's history window*. In practice that means:

- **5min, 15min, 1h, 4h** — caggs over `minute_ohlcv`. Always. The minute-derived bucket is the right shape; aggregating from daily would be wrong (no intra-day truth in daily).
- **Daily** — read directly from `daily_ohlcv`. Not a cagg. EODHD-sourced, dense, authoritative.
- **Weekly, monthly, quarterly** — derived from daily, not from minutes. Two reasons: (1) minute history is sparse, daily isn't, so a minute-derived weekly would have huge holes for symbols where daily is complete; (2) weekly/monthly are inherently daily-grain rollups (last close of the week, sum of the week's volume), and `daily_ohlcv` already has the right inputs.

So caggs in the new schema:
- 4 minute caggs over `minute_ohlcv`: `minute_5min_ohlcv`, `minute_15min_ohlcv`, `minute_hourly_ohlcv`, `minute_4hour_ohlcv`.
- 3 daily caggs over `daily_ohlcv`: `daily_weekly_ohlcv`, `daily_monthly_ohlcv`, `daily_quarterly_ohlcv`.
- No `minute_daily_ohlcv` / `minute_weekly_ohlcv` / `minute_monthly_ohlcv` (slice 150's proposed daily-grain minute caggs). Daily-grain rollups come from daily, not minutes.

Total: 7 caggs in the new schema (4 minute-sourced, 3 daily-sourced), down from 11 today. Each timeframe has exactly one source.

**This changes the cagg-migration scope from the previous section:** drop the 11 legacy minute caggs *and* migrate the bootstrap/init path so it stops creating `minute_daily_ohlcv` etc. Three new caggs to create against `daily_ohlcv`. Same projection shape (raw OHLCV with `FIRST/MAX/MIN/LAST/SUM`), just a different source hypertable.

## Command surface

Today the data CLI has `mt data daily *`, `mt data minute *`, `mt data ca *`, `mt data lists *`, `mt data instruments *`, `mt data calendars *`, `mt data daemon *`, plus top-level `mt data {state, status, migrate-cold-start, extend, refetch}`. Most of those stay. What's missing — and what users actually want — is a unified read command and a unified ensure/verify command. Slice 152 adds two top-level verbs and rationalizes the granularity argument.

### Granularity tokens

One canonical set of tokens, everywhere a granularity is named (CLI args, function kwargs, view-name map keys):

```
1m   5m   15m   1h   4h   1d   1w   1mo   1q
```

Lowercase `m` for minutes; `mo` (not `M`) for months because uppercase invites typos and confusion with minutes. `1q` only makes the list if we ship the quarterly cagg. Drop it if we don't. No aliases (`hour`, `daily`, `60m` — pick one and stick with it). The map from token to source:

| token | source                                  | adjusted-on-read input |
| ----- | --------------------------------------- | ---------------------- |
| `1m`  | `minute_ohlcv` (hypertable, raw)        | yes                    |
| `5m`  | `minute_5min_ohlcv` cagg                | yes                    |
| `15m` | `minute_15min_ohlcv` cagg               | yes                    |
| `1h`  | `minute_hourly_ohlcv` cagg              | yes                    |
| `4h`  | `minute_4hour_ohlcv` cagg               | yes                    |
| `1d`  | `daily_ohlcv` (hypertable, raw)         | yes                    |
| `1w`  | `daily_weekly_ohlcv` cagg               | yes                    |
| `1mo` | `daily_monthly_ohlcv` cagg              | yes                    |
| `1q`  | `daily_quarterly_ohlcv` cagg (if shipped) | yes                  |

### `mt data get` — read

```
mt data get <symbol> <granularity> [--start <date>] [--end <date>] [--raw] [--json|--csv]
```

Positional: `symbol` then `granularity`. Both required. `mt data get AAPL 15m` is the common case. No `--granularity` flag — you'd never want to omit it, and the positional reads cleanly.

- `--start` / `--end` accept ISO date or datetime. Defaults: start = `<symbol>`'s earliest available bar at this granularity; end = now. If both omitted, returns the full history (potentially a lot of rows; that's fine — Timescale handles it, output is paginated by the renderer).
- `--raw` returns unadjusted OHLC straight from the source. Default is adjusted.
- `--json` / `--csv` switch the output format. Default is a Rich table.

Three things this command intentionally does *not* do: write to disk, recompute anything, go to the network. It's a pure read.

### `mt data pull` — fetch / backfill / verify / reset

```
mt data pull <granularity> [--symbol SYM | --symbols a,b,c | --list NAME | --universe] \
                            [--start <date>] [--end <date>] \
                            [--verify] [--reset] [--dry-run] [--yes] [--json]
```

One verb for "get the data into the database for this window." Subsumes today's `mt data daily {update, update-all, update-file, verify, coverage}`, `mt data minute {update, update-all, backfill}`, *and* `mt data refetch`. Same job under one name; the variations are flags.

**Args:**
- Granularity is positional and required (`1d` or `1m` — only the two raw sources accept `pull`; you don't pull a cagg, you refresh it, see `mt data caggs` below).
- Symbol selection is mutually exclusive: `--symbol`, `--symbols` (comma list), `--list` (named list from `lists.yml`), `--universe` (active universe from settings). Exactly one must be given. No default — `mt data pull 1d` alone errors with a clear message; this is loud on purpose.
- `--start` / `--end` define the window. Defaults: start = last covered bar (i.e. fill forward), end = now. Explicit `--start` in the past enables backfill.

**Modes (default behavior, then modifiers):**
- *Default:* fetch anything in `[start, end]` not already present, **skipping ranges the daemon has marked terminal** (`PROVIDER_HOLE`, `RETRY_EXHAUSTED`). Routine "make it current."
- `--verify`: run the coverage check, report gaps, fetch nothing. Answers "is the data there?" Useful for scripts and pre-flight checks.
- `--reset`: before fetching, reset terminal gap markers (`PROVIDER_HOLE`, `RETRY_EXHAUSTED`) in scope to `UNKNOWN`. This is what today's `mt data refetch` does — operator override of the gap machinery's verdict. Terminal markers exist precisely because retrying burns quota for no benefit, so `--reset` requires a confirmation prompt unless `--yes` or `--json` is set.
- `--dry-run`: preview what would be fetched (and, with `--reset`, what gap rows would be cleared) without making changes.

**Why `--reset` is a flag, not a separate command:** the operation is "fetch this window," with one optional modifier ("ignore the don't-retry markers"). Splitting into two verbs implies two different operations; they're the same operation with one extra step at the front. Keeping them merged also means scripts and dashboards have one entry point to learn.

**Migration from today's commands:**
- `mt data daily update --symbol AAPL` → `mt data pull 1d --symbol AAPL`
- `mt data daily update-all` → `mt data pull 1d --universe`
- `mt data daily update-file lists/spy.txt` → `mt data pull 1d --list spy`
- `mt data daily verify` → `mt data pull 1d --universe --verify`
- `mt data daily coverage` → `mt data pull 1d --symbol AAPL --verify` (with detail output)
- `mt data minute backfill --symbol AAPL --start 2020-01-01` → `mt data pull 1m --symbol AAPL --start 2020-01-01`
- `mt data refetch --symbol AAPL --daily --from 2024-01-01` → `mt data pull 1d --symbol AAPL --start 2024-01-01 --reset`

The old commands delete in this slice. No alias period — 152 is a breaking slice already.

### `mt data caggs` — refresh / verify aggregates

```
mt data caggs refresh [--granularity 5m,15m,...] [--start <date>] [--end <date>]
mt data caggs status
```

Caggs aren't "ensured" the way raw data is — their content is a function of the source hypertable plus a refresh policy. Two operations matter:

- `refresh` — manual `CALL refresh_continuous_aggregate(...)` for one or more views over a window. Useful after a large backfill, or when policies are intentionally lagged. Defaults: all 7 caggs, full history.
- `status` — show each cagg's last refresh time, its policy schedule, and the count of materialized rows. Answers "is this thing keeping up?"

No "verify caggs" mode — a cagg is verifiable by sampling: query it for a known window and compare to a `time_bucket()` over the source. That's a debugging task, not a routine operation; it doesn't need a command.

### What stays as-is

- `mt data status` — already does cross-granularity health (gaps, stale, failed) by symbol. Keep. Reflects what `pull --verify` would tell you for the whole registry.
- `mt data extend` — trading-sessions extension. Keep.
- `mt data state` — show acquisition state. Keep.
- `mt data ca *` — splits/dividends inspection. Keep, but the underlying tables are now in TimescaleDB.
- `mt data lists *`, `mt data instruments *`, `mt data calendars *`, `mt data daemon *` — keep.
- `mt data migrate-cold-start`, `mt data migrate *` — keep.

### What deletes

- `mt data daily {update, update-all, update-file, verify, coverage, migrate, symbols}` — folded into `pull` and `status`.
- `mt data minute {update, update-all, backfill, status, metrics}` — folded into `pull` and `status`.
- `mt data refetch` (slice 148) — folded into `pull --reset`.
- `mt data audit` (slice 149) — gone with the rest of the audit machinery.
- Any `mt data adjustment *` command — gone with the adjustment package.

### Programmatic API

The CLI surface above is one face of this; the in-process API is the other. Two small additions:

- `TimescaleMinuteDataDB.get_minute_data(symbol, start, end, granularity, *, adjusted=True)` — already exists; add the `adjusted` kwarg and the new granularity tokens. When `adjusted=True`, runs the result through `adjusted(bars, symbol, conn)` before returning.
- New `TimescaleDailyDataDB.get_daily_data(symbol, start, end, granularity, *, adjusted=True)` — granularity is one of `1d/1w/1mo/1q`, routes to `daily_ohlcv` or the appropriate daily cagg. Same adjusted-by-default contract.

A thin `mt data get` implementation is just: pick the DB based on granularity, call its `get_*_data`, render. ~40 lines.

### One caveat on adjusted-on-read

`adjusted()` looks up `prev_close` from `daily_ohlcv` to compute the k-factor for split/dividend dates. If a symbol has minute data but no corresponding daily history (shouldn't happen under the daemon, but possible during partial backfills), adjustment for that symbol fails with `KeyError` rather than silently returning unadjusted bars. That's the explicit-failure contract from the architecture section; it's the right behavior. `mt data get` surfaces this as an error message naming the symbol and date.

## What deletes vs stays

**Deletes (~3000 lines):**
- `src/manta_trading/data/adjustment/band_writer.py`, `verify.py`, `verify_eod.py`, `audit.py`, `context.py`, `__init__.py` re-exports of these.
- `src/manta_trading/data/acquisition/daemon/ca_drift.py`.
- `src/manta_trading/market/marketdb.py`, `symbol_list_manager.py`, `instrument_seed.py` (the MarketDB-touching parts; any registry-seeding logic worth keeping moves to a Timescale-native module).
- `src/manta_trading/backtest/` — the entire directory. Gone.
- All AlphaVantage code: every match for `alphavantage` / `AlphaVantage` / `ALPHAVANTAGE` under `src/` and `test/`.
- `src/manta_trading/cli/commands/data.py`: the `mt data audit` command, any legacy `mt data daily *` or `mt data minute *` command that wrote to MarketDB or read `dailyohlcvadjusted`, any `mt data adjustment *` command.
- `daily_ohlcv.{adj_open, adj_high, adj_low, adj_close, k_factor, adjusted_at}` columns. Same for `minute_ohlcv`.
- `acquisition_state.last_adjusted_ca_snapshot_id` column.
- `manta_trading.constants.ADJUSTMENT_DRIFT_EPSILON` (orphaned after audit deletion).
- All unit and integration tests for the deleted modules.
- The legacy MarketDB schema: `dailyohlcvadjusted`, `splits`, `dividends`, `symbol_list`, `objects_last_updated`, `schema_migrations` (after migrating splits/dividends out).

**Adds (~150 lines):**
- One migration: copy `splits` and `dividends` rows from MarketDB into TimescaleDB tables of the same shape.
- One module: `src/manta_trading/data/adjustment.py` (single file, replaces the entire `data/adjustment/` package) with `adjusted(bars, symbol, conn, *, ca_snapshot=None)`. Includes prev_close lookup against `daily_ohlcv`, k-factor math from the existing `compute_k_factor` algorithm, and explicit failure modes (missing prev_close → `KeyError`; empty CA result for a symbol with no actions → return bars unchanged).
- Bulk-EOD daily steady-state path: `mt data daemon`'s daily cycle calls `/eod-bulk-last-day` once per day instead of `/eod` per symbol. Per-symbol `/eod` retained for backfill and refetch.
- Architecture amendment: update `140-arch.data-quality-operations.md` to describe adjusted-on-read. Mark adjusted-on-write sections as superseded.

**Stays:**
- Slice 145 daily ingest, minute ingest, advisory locking, data_gaps machinery.
- Slice 146 daemon (cycle scheduling, lists, quota bucket, signal handling, CA fetch).
- Slice 147 status command and trading_sessions auto-extension.
- Slice 148 refetch command.
- All Timescale tables not listed under "deletes": `instruments`, `trading_calendars`, `trading_holidays`, `trading_sessions`, `data_gaps`, `acquisition_state` (minus one column), `daemon_heartbeat`, `backfill_state`, `provider_symbol_mapping`.
- Finnhub (enriches `instruments` with profile data; not on the OHLCV path).
- Databento config fields (real-time tick initiative, separate slice, not now).

Effort score: **4** (per frontmatter). Mostly deletion; new module is small; migration is a one-shot copy of two tables.
