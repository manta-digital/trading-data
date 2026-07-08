---
title: "154 — CLI surface: get, pull, caggs; old command deletion; daemon bulk-EOD"
slice: 154
initiative: 140
status: complete
phase: 4
type: feat
effort: 2
tags: [cli, adjusted-on-read, daemon, bulk-eod]
created: 20260505
dateUpdated: 20260506
author: pm+claude
docType: slice-design
project: trading
dateCreated: 20260505
dependsOn: [153]
---

# Slice 154 — CLI surface

## What this slice delivers

Two new top-level `mt data` verbs (`get`, `pull`), one new subgroup
(`caggs`), deletion of the old `daily *`, `minute *`, and `refetch`
commands, and the daemon's bulk-EOD daily steady-state path. Depends on
slice 153's `TimescaleDailyDataDB` and `adjusted()` being in place.

For command specs see the "Command surface" section in
`152-slice.consolidation.md`. This slice implements exactly that spec.

## mt data get

```
mt data get <symbol> <granularity> [--start <date>] [--end <date>] [--raw] [--json|--csv]
```

- Both positional args required.
- Validates `granularity` against `Granularity` enum; errors clearly on
  unknown token.
- Routes to `TimescaleDailyDataDB` for `1d/1w/1mo/1q`, to
  `TimescaleMinuteDataDB` for minute-grain tokens.
- `--raw` → `adjusted=False`; default is `adjusted=True`.
- Output: Rich table by default; `--json`; `--csv`.
- Pure read — no writes, no network calls, no recomputes.
- `KeyError` from missing `prev_close` surfaces as a named error message
  (symbol + date) and non-zero exit.

## mt data pull

```
mt data pull <granularity> [--symbol|--symbols|--list|--universe]
             [--start] [--end] [--verify] [--reset] [--dry-run] [--yes] [--json]
```

- Granularity positional, required. Only `1d` and `1m` accepted; cagg
  tokens error clearly.
- Symbol selection mutually exclusive; no default — omitting all four
  errors with a clear message.
- Default mode: fetch gaps in window, skip terminal gaps
  (`PROVIDER_HOLE`, `RETRY_EXHAUSTED`).
- `--verify`: report gaps, fetch nothing.
- `--reset`: reset terminal gap markers to `UNKNOWN` before fetching.
  Requires confirmation prompt unless `--yes` or `--json`.
- `--dry-run`: preview actions (gaps to fetch, rows to reset), no changes.
- `--verify` and `--dry-run` are mutually exclusive; error if both set.

Migration mapping from old commands:

| Old command | New command |
|---|---|
| `mt data daily update --symbol AAPL` | `mt data pull 1d --symbol AAPL` |
| `mt data daily update-all` | `mt data pull 1d --universe` |
| `mt data daily update-file lists/spy.txt` | `mt data pull 1d --list spy` |
| `mt data daily verify` | `mt data pull 1d --universe --verify` |
| `mt data minute backfill --symbol AAPL --start 2020-01-01` | `mt data pull 1m --symbol AAPL --start 2020-01-01` |
| `mt data refetch --symbol AAPL --daily --from 2024-01-01` | `mt data pull 1d --symbol AAPL --start 2024-01-01 --reset` |

## mt data caggs

```
mt data caggs refresh [--granularity 5m,15m,...] [--start <date>] [--end <date>]
mt data caggs status
```

- `refresh`: calls `CALL refresh_continuous_aggregate(...)` for each named
  cagg (or all 7 if omitted) over the window (or `NULL/NULL` for full).
- `status`: queries `timescaledb_information.jobs` and
  `continuous_aggregates`; renders last refresh time, policy schedule, and
  materialized row count per cagg.

## What deletes

No alias period — 152 is already a breaking slice.

- `mt data daily {update, update-all, update-file, verify, coverage, migrate, symbols}`
- `mt data minute {update, update-all, backfill, status, metrics}`
- `mt data refetch` (replaced by `pull --reset`)
- All tests for the deleted commands.
- `daily_app` and `minute_app` subgroups if now empty; their `add_typer`
  registrations.

## Daemon: stop-when-done default for scoped invocations

`mt data daemon run --symbols X` or `--list NAME` should exit when the scope
is drained — no `--stop-when-done` flag required. Bare `mt data daemon run`
(full universe, no scope flag) keeps the current forever-loop default.

Implementation: in the runner's mode-selection logic, set
`terminate_when_drained = True` by default when `--symbols` or `--list` is
supplied, unless `--forever` is explicitly passed. The flags `--stop-when-done`
and `--forever` remain as explicit overrides in both directions.

## Daemon: bulk-EOD daily steady-state

In the daemon's daily cycle, replace the per-symbol `/eod` loop with a
single `/eod-bulk-last-day` call for the full exchange once all scope
members are caught up. Per-symbol `/eod` is retained for the backfill case
and for `pull --reset`.

The original slice 152 (old numbering) in the slice plan described a
`DailyMode` enum (`BACKFILL` vs `STEADY_STATE`) for mode selection. That
logic belongs here.

## What this slice does not do

- No schema changes.
- No changes to `adjustment.py` or `TimescaleDailyDataDB` (slice 153).
- No new ingest providers.

Effort score: **2**
