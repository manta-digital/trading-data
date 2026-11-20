---
docType: notes
project: trading-data
dateCreated: 20261119
dateUpdated: 20261119
status: complete
---

# 174: what the equity close-stamp bar holds (#78)

The bar #78 describes is a 1m bar stamped exactly at the extended close: 20:00 ET, or
17:00 ET on an early-close day. Slice 174 puts it outside every session bar (design D4).
This note records what it holds, so the API references can tell clients.

## Procedure

- A one-off script, not committed, run with `uv run python` from the dev checkout against
  production (`Settings().timescale_db_url`), in one read-only transaction
  (`default_transaction_read_only=on`), session time zone UTC. Nothing was written.
- Sample: 20 NYSE/NASDAQ-calendar symbols (indices excluded). The 10 largest by minute rows
  over 2025-11-01..2026-11-01 (from `minute_coverage`: SPY, QQQ, SOXL, GOOGL, PLTR, TQQQ,
  VOO, IONQ, TSLA, NVDA) and 10 spread evenly down that ranking (RKLB, BLDR, PHAT, MERC,
  FOTO, AMBR, INDL, BUFT, HSPT, AMUB).
- Sessions: every NYSE session 2026-08-03..2026-10-30 (64), plus every held early close in
  `trading_holidays` (2025-07-03, 2025-11-28, 2025-12-24, 2026-07-02). 1,360 symbol-sessions.
- Per symbol-session: the bar at the extended close instant (present, single-price, off-tape
  mask), the `daily_ohlcv` close and volume, the summed volume of 04:00-20:00 ET minutes and
  of regular minutes, the 15:59 and 16:00 ET minutes' volume against the median regular
  minute, and the `minute_hourly_ohlcv` row that holds the stamp.
- Follow-ups by hand: listing venue (`instruments.eodhd_exchange`), the 16:00 and 15:59
  closes against the daily close, the 2025-11-28 early close for IONQ/BLDR/NVDA, and SPY
  2026-01-14 for the winter rollup buckets.

## Results

Regular days (64 sessions per symbol), by listing venue:

| Listing venue | Symbols | Stamp present | Median volume vs median regular minute: 15:59 / 16:00 / stamp |
|---|---|---|---|
| NYSE Arca ETFs | SPY, VOO, SOXL, INDL, AMUB | 320 of 320 (AMUB 63) | SPY 25 / 52 / 23; VOO 21 / 56 / 26; SOXL 8 / 26 / 13 |
| Cboe BZX ETF | BUFT | 64 of 64 | 1.0 / 0.9 / 0.2 |
| ETF listed as NASDAQ by the provider | FOTO | 64 of 64 | 7.3 / 1.9 / 0.6 |
| NASDAQ (stocks and ETFs) | QQQ, TQQQ, GOOGL, NVDA, TSLA, PLTR, RKLB, PHAT, MERC, AMBR, HSPT | 0 of 704 | GOOGL 15 / 259; NVDA 11 / 206; QQQ 14 / 32 |
| NYSE stocks | IONQ, BLDR | 0 of 128 | IONQ 13 / 126; BLDR 29 / 256 |

Every stamp found (483 symbol-sessions, early closes included):

| Measure | Result |
|---|---|
| Single price (O = H = L = C) | 471 of 483 (the 12 others are ordinary minutes on 2026-07-02, below) |
| Price equals the `daily_ohlcv` close | 454 of 483 |
| Price equals the last minute before it | 4 of 483 |
| Volume / daily volume | median 1.8% (quartiles 0.6%, 3.5%); SPY 2.9%, VOO 2.7%, SOXL 1.8% |
| 04:00-20:00 minute volume / daily volume | median 1.045 without the stamp, 1.059 with it |
| (daily volume minus regular-minute volume) / stamp volume | median 8.4 |
| Served by `prints=eligible` (not fully marked off-tape) | 414 of 483; SOXL 23 of 68, SPY and VOO 61 of 68 |

Early closes (2025-07-03, 2025-11-28, 2025-12-24): the stamp is at 17:00 ET, single-price,
at the daily close, for the Arca and BZX ETFs **and** for the NYSE stocks IONQ and BLDR
(IONQ 2025-11-28: 983,672 shares at 49.30, daily close 49.30), which carry no stamp on
regular days. NASDAQ symbols have none.

2026-07-02 is not an early close. Every symbol traded the full day: SPY's closing volume is
at 15:59-16:00 ET (2.95M, 4.08M), minutes continue past 17:00, and the 20:00 stamp is
present (1,650,128 at 744.78). 2020-07-02 is the same (SPY 15:59 1.62M, 16:00 3.06M, nothing
at 13:00). The rule generator wrongly makes both 13:00 early closes (July 4 on a Saturday);
this is the #26 defect class, handled in section 2 of the 174 tasks.

Rollup buckets (`session=utc`, #78 work item 4):

- In summer the stamp is 00:00Z and is alone in its 1h, 4h, 15m and 5m buckets (no other
  minute in 00:00-04:00Z).
- In winter it is 01:00Z: alone in its 1h bucket (SPY 2026-01-15 01:00Z, minute_count 1,
  volume 1,149,722), but in the 00:00Z 4h bucket with the 19:00-19:59 ET minutes
  (minute_count 61, volume 1,197,594).
- The `utc` 1h and 4h rollups therefore carry it whenever `prints=eligible` keeps it, and a
  1h or 4h bar dated the next UTC day can be nothing but this print.

## Finding

**Established:**

- The bar is a single print at the official closing price (454 of 483 equal the daily
  close; only 4 equal the preceding minute's close).
- It appears on regular days only for exchange-traded products whose listing venue runs its
  own closing auction outside NASDAQ: every NYSE Arca and Cboe BZX ETF sampled, every day.
  It never appears for NASDAQ-listed symbols, and not for NYSE-listed stocks on regular
  days. On early closes, NYSE-listed stocks carry it too, at 17:00 ET.
- For NASDAQ- and NYSE-listed stocks the closing auction's volume is in the **16:00 ET
  minute** (100-260 times the median regular minute), the first minute of the post-market
  phase. For NYSE stocks that minute's close is the official close (BLDR 61 of 64); for
  NASDAQ symbols the official close is in no minute bar (3-8 of 64).

**Not established:** what the stamp's volume counts. It is 1-3% of daily volume, and the
minute sums reconcile with the daily total neither with nor without it (the daily total is
below the 04:00-20:00 sum, and exceeds the regular sum by about 8 stamps). The likeliest
reading, the listing venue's closing-auction print, cannot be confirmed: we hold no
equity tape and the provider documents nothing. Whether the same shares are also inside the
16:00 minute is not known.

## What it means for regular-session volume

Phases are half-open and stamped at their start, so the regular phase is 09:30-15:59:59 ET.
The closing auction executes at 16:00:00 and lands in the 16:00 minute: **regular-session
volume summed from session bars excludes the closing auction for every symbol**, and the
regular phase's last close is the 15:59 minute's close, not the official close. The
16:00 minute is the first post-market bar, and `extended` session bars include it. The
close-stamp bar is in no session bar. Clients who want closing-auction volume take the
16:00 minute (and, for Arca/BZX products, note the stamp separately), never the 15:30 bar.
A `closing_print` flag (#78 work item 3) remains follow-up work.
