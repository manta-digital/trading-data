---
docType: reference
project: trading
dateCreated: 20260717
dateUpdated: 20260717
status: draft
---

# Minute Fetch: Two Divergent Code Paths (Operator Reference)

## Purpose

There are currently **two independently-implemented code paths** for
fetching minute data for a symbol. They accept overlapping arguments,
touch the same tables, and produce similarly-shaped log output — but
they seed gap rows differently, and only one of them is coverage-aware
(slice 162). This document exists so an operator can tell them apart
*today*, before slice 165 resolves the underlying design defect.

**If you need to fetch/backfill minute data for one or more symbols
right now, use `mt data daemon run --minute --symbols <SYM[,SYM2,...]>`,
not `mt data pull 1m`.** See the comparison below for why.

## The two paths

### Path A — `mt data pull 1m --symbol X` / `--symbols X,Y`

- CLI: `mt data pull <granularity> ...` (`data.py::data_pull` →
  `_pull_fetch` → `_pull_fetch_inner`, minute branch).
- Underlying function: **`run_minute_refetch`**
  (`data/acquisition/daemon/minute.py`).
- Always calls `_do_minute_symbol(..., force_reset_terminal=True,
  coverage_index=None)`.
- `force_reset_terminal=True` unconditionally resets `PROVIDER_HOLE` /
  `RETRY_EXHAUSTED` rows before re-seeding.
- `coverage_index` is **never built or passed** — the seed always falls
  back to the legacy single `[history_start, target_end]` span (now
  gated behind `precomputed_ranges=None` post-slice-162, but still the
  full-window shape, not a coverage-aware diff).
- Originally added in slice 148 as an **operator escape valve** for
  forcing a full re-verify of one symbol (e.g. after a suspected bad
  fetch) — it was never intended as a general "backfill this symbol"
  tool, but nothing in the CLI surface communicates that distinction.

### Path B — `mt data daemon run --minute --symbols X`

- CLI: `mt data daemon run` (`data.py::daemon_run` → `Runner` →
  `run_minute_cycle`).
- Underlying function: **`run_minute_cycle`** →
  `_process_minute_symbol` → `_do_minute_symbol`.
- Builds `coverage_index` once via `build_minute_coverage_index` before
  the per-symbol loop, and threads it into `_do_minute_symbol`.
- When `_needs_seed` fires, computes `precomputed_ranges` via
  `compute_missing_minute_sessions` — seeds only genuinely-missing
  sessions (slice 162's coverage-aware behavior).
- `--symbols` scopes the daemon's normal cycle to a symbol subset and
  implies `--stop-when-done` by default — it is the **same code path**
  the unscoped daemon uses, just bounded to fewer symbols. This is the
  correct tool for "fetch/verify minute data for symbol(s) X."

## Why this is dangerous, not just confusing

Both paths:
- Accept a symbol argument and a `-v`/`--verbose` flag.
- Touch `data_gaps` and `minute_ohlcv`.
- Emit plausible `INFO`-level log lines describing progress and a
  final outcome ("success", chunk counts).

Neither path's output tells you which one you ran. Nothing errors,
warns, or logs a distinguishing marker. Two independent verification
attempts against production (slice 162, 2026-07-17) used Path A by
mistake — following the slice design's own walkthrough, which itself
had the same error — before a chunk-count anomaly (23 chunks for a
"coverage-aware" seed) surfaced the mismatch. This violates the
project rule against using user-accessible labels as logical structure:
the command name (`pull` vs `daemon run`) is a label, and it silently
selects between materially different seeding algorithms.

## Current guidance (until slice 165 lands)

| I want to... | Use |
|---|---|
| Backfill or verify coverage for specific symbol(s) right now | `mt data daemon run --minute --symbols X,Y,Z -v` |
| Force a full re-verify of one symbol after a suspected bad/incomplete fetch (operator escape valve, resets terminal gap rows) | `mt data pull 1m --symbol X` (Path A — understand it resets `PROVIDER_HOLE`/`RETRY_EXHAUSTED` and does NOT do a coverage-aware seed) |
| Run the standing production daemon | `mt data daemon run --minute` (no `--symbols`, runs forever / until credit budget) |

If you are unsure which you need, prefer Path B (`daemon run --minute
--symbols ...`) — it is coverage-aware and will not burn credits
re-fetching data already present.

## Follow-up

Tracked as slice 165 (140 initiative band) — see
`140-slices.data-quality-operations.md` for scope: unify or make these
two paths observably distinct, and sweep the daemon/CLI surface for
similar cases where a user-facing label silently selects between
divergent implementations.
