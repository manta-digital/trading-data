---
docType: notes
slice: serving-the-new-tiers
project: trading-data
dateCreated: 20261109
dateUpdated: 20261109
status: in_progress
---

# Bar parity and long-read measurements (slice 306)

The production results fill this note in Phase 7, after the release. Nothing below
is measured yet.

## ES 2024 parity result (issue #70)

Not yet measured (Phase 7).

To record here:

- Minute counts by class (`agree`, `differ`, `provider_only`, `tick_only`) over the
  held ES tick window, from
  `sudo mt-run data tick parity --product ES --start 2024-08-30 --end 2025-01-01 --json`.
- A reference to the saved `--json` minute list.
- The close-stamped finding for issue #70: how many close-stamped minutes there
  are, how many agree, and whether provider volume beyond the ticks shows the
  closed-interval rule moving trades out of their minute.

## D10 long coarse read

Not yet measured (Phase 7).

To record here: product `1d` bars over the whole held ES 2024 year (about 259
sessions) through the API in production, the wall time (bound: under 10 s), and the
per-session time extrapolated to the 1,300-session roll cap.

## D1: provider `ohlcv-1h`, `ohlcv-1d` and `ohlcv-eod` bucketing

Decided at design, recorded 2026-11-08. Neither is archived or served.

- **`ohlcv-1d` is a UTC-date bar.** The DBN schema definition (`databento/dbn`,
  `rust/dbn/src/enums.rs`) reads "Open, high, low, close, and volume at a daily
  interval based on the UTC date." A CME session runs from the previous evening
  (22:00 or 23:00 UTC) to the next afternoon, so one provider daily bar holds the
  end of one session and the start of the next. Serving it as `1d` would silently
  change what a daily bar means.
- **`ohlcv-1h` is an hourly bar on whole hours.** CME's open and close are on the
  hour in CT, and CT differs from UTC by whole hours, so its boundaries coincide
  with our session-anchored 1h buckets. It adds nothing that rolling up
  `ohlcv-1m` does not give.
- **`ohlcv-eod`** ("daily cadence based on the end of the trading session") exists
  in DBN, but `metadata.list_schemas("GLBX.MDP3")` does not offer it as of
  2026-11-08. If it becomes available, the architecture's "revisited if we choose
  to use them" applies.

The architecture's rule stands: neither is archived or served.
