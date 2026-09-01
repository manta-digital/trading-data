---
docType: slice-design
slice: health-check
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [916]
interfaces: [265, 267]
effort: 1
dateCreated: 20260831
dateUpdated: 20260831
status: complete
review: none
---

# Slice Design: `mt data health` — one automated answer to "does anything need a human?"

## Overview

On 2026-08-31 two silent production failures were found by hand: ~7,300
active symbols whose minute data had frozen for up to 24 days while the nightly
pass logged a fetch line for each (issue #19), and the 5m/15m continuous
aggregates unmaterialized for 24 days while their refresh policies reported
Success (issue #20). Both were visible in numbers nobody was obliged to read.
The Project Manager's requirement: the human maintenance load drops by 80%,
now — no more glancing at `data status`, `caggs status`, `kalshi status`, and
the EODHD account page.

This slice ships one read-only command, `mt data health`, and one hourly
systemd timer that runs it. Every check judges a measured value against a
named threshold in `constants.py` and prints one line; any failure exits
non-zero, so under the timer a breach is a **failed unit** — shown by
`mt-run status` as `== health: FAILING …` and by `systemctl --failed`. Passing
means there is nothing to look at.

## Checks (each pure; `gather()` is the only I/O)

| check | measured | threshold | would have caught |
|---|---|---|---|
| minute data / daily data | newest raw bar in `minute_ohlcv` / `daily_ohlcv` | `HEALTH_*_RAW_STALE_AFTER` (4 d / 5 d) | #19 (raw edge, not attempt stamps) |
| cagg *view* ×7 + 2 coverage | slice-168 `FreshnessVerdict` | slice-168 thresholds | #20 on day one |
| eodhd quota | `/api/user` remaining incl. extra calls | `HEALTH_EODHD_QUOTA_HEADROOM_MIN` (20k) | the 2026-08-31 402 starvation |
| kalshi catalog / candles / trades | last phase completion | `HEALTH_KALSHI_PHASE_STALE_AFTER` (3 h) | a stalled pass within two firings |

Exit codes: 0 healthy, 1 unhealthy, 2 could not run (no URL/key, DB or provider
unreachable) — 2 also fails the unit, deliberately.

## Decisions

1. **Failure is a failed unit, not a message.** The host has no mail transport;
   inventing one is a separate decision. `mt-run status` gains one `== health:`
   line, which is the operator's whole surface.
2. **Thresholds are the loosest values that catch each failure within a
   working day**, defined once in `constants.py`; `data status` health semantics
   (attempt-based) are deliberately not reused — they hid #19.
3. **Cagg checks reuse slice 168's verdicts** rather than a second lag
   computation; coverage caggs go through `check_coverage_freshness` because
   their source is not in `GRANULARITY_SOURCE`.
4. **Hourly at :50 UTC**, after the :05/:20/:35 firings have settled.

## Success Criteria

1. `mt data health` exits 0 on a healthy production and prints one line per
   check plus `healthy`; with any check failing it exits 1 and the last line
   names the failing count. (Unit-tested rules; live smoke run recorded below.)
2. `mt-health.service` / `.timer` are installed by `install-production.sh`,
   the timer fires hourly, and `mt-run status` shows `== health: OK|FAILING`.
3. Pure rules are unit-tested for pass/fail/never on each threshold.

## Verification

Live smoke run on manta9000 2026-08-31 (recorded in the CHANGELOG entry and
DEVLOG). Host: `install-production.sh --ref v0.11.3` twice (new units), then
`systemctl enable --now mt-health.timer`; `mt-run status` shows the health
line after the first :50 firing.
