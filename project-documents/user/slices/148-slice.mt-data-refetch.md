---
title: "148 — mt data refetch"
slice: 148
initiative: 140
status: complete
phase: 6
type: feature
effort: 3
tags: [cli, data-quality, data-gaps, operator-tools]
dateCreated: 2026-05-04
dateUpdated: 2026-05-04
author: architect
docType: slice-design
project: trading
---

# Slice 148 — `mt data refetch`

## Overview

Adds `mt data refetch` as an operator escape valve for reprocessing a specific symbol's
data window. It fetches bars from the configured provider in provider-sized chunks, using
`update_data_gaps(..., force_reset_terminal=True)` so that any `PROVIDER_HOLE` or
`RETRY_EXHAUSTED` rows in scope are reset to `UNKNOWN, attempt_count=0` before each
chunk is re-attempted. After all chunks are processed, `coalesce_data_gaps` runs to merge
contiguous gap rows.

This follows the architecture's specification verbatim:
> "Fetches the requested window from the configured provider in provider-sized chunks.
> Per-chunk processing uses `update_data_gaps` with `force_reset_terminal=True`…
> After all chunks process, runs `coalesce_data_gaps`."
> — 140-arch.data-quality-operations.md §`mt data refetch`

## Value

`mt data status` exposes terminal gaps (`PROVIDER_HOLE`, `RETRY_EXHAUSTED`) that the
daemon will never retry. `mt data refetch` gives the operator a direct repair path:
re-fetch the window, let `update_data_gaps` reset the terminal rows under the advisory
lock, and return control to the daemon. No direct SQL required.

## Technical Scope

### What this slice adds

- `mt data refetch` CLI command in `src/manta_trading/cli/commands/data.py`
- `run_daily_refetch` function in `src/manta_trading/data/acquisition/daemon/daily.py`
- `run_minute_refetch` function in `src/manta_trading/data/acquisition/daemon/minute.py`
- Unit and integration tests

### What this slice does NOT add

- New fetch logic — the refetch functions reuse the existing per-symbol fetch/lock/write
  path in `_do_daily_symbol` / `_do_minute_symbol`
- New advisory-lock machinery — `update_data_gaps` already acquires it; refetch just
  passes `force_reset_terminal=True`
- `--reapply-only` — CA re-adjustment is handled automatically by the daemon's
  CA-detection mechanism (slice 146)
- Multi-symbol scope — single symbol per invocation (by design; see D1)

## Architecture

### Command interface

```
mt data refetch --symbol SYMBOL [--daily] [--minute] [--from DATE] [--to DATE] [--dry-run] [--yes] [--json]
```

Flag details:

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--symbol` | TEXT | Yes | Single symbol to refetch |
| `--daily` | flag | No | Refetch daily granularity |
| `--minute` | flag | No | Refetch minute granularity |
| `--from` | DATE (YYYY-MM-DD) | No | Start of fetch window; default: symbol's `first_data_date` |
| `--to` | DATE (YYYY-MM-DD) | No | End of fetch window; default: last completed trading session |
| `--dry-run` | flag | No | Show current terminal gaps in scope; make no changes |
| `--yes` / `-y` | flag | No | Skip confirmation prompt |
| `--json` | flag | No | Emit JSON; skip prompt |

`--daily` and `--minute` follow the same convention as `mt data status`: both set = both
granularities. Neither set = both granularities. One set = that granularity only.

No `--all` flag. This is a single-symbol operator action.

### New functions: `run_daily_refetch` / `run_minute_refetch`

These are thin wrappers around the existing `_do_daily_symbol` / `_do_minute_symbol`
logic. They accept an explicit `[from_date, to_date]` window and set
`force_reset_terminal=True` on every `update_data_gaps` call within that window.

**`run_daily_refetch`** location: `src/manta_trading/data/acquisition/daemon/daily.py`

```python
def run_daily_refetch(
    symbol: str,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
) -> CycleReport:
```

Behavior:
1. Resolve `from_date` → symbol's `first_data_date` if `None`; resolve `to_date` →
   last completed trading session if `None`.
2. Acquire advisory lock on `(symbol, 'daily')`.
3. Run CA drift check (same as normal daily cycle).
4. Fetch full EOD history from EODHD for the window.
5. Insert bars, apply band adjustments.
6. Call `update_data_gaps(symbol, 'daily', from_ts, to_ts, fetch_status_for_unfilled,
   force_reset_terminal=True)`.
7. Update `instruments.first_data_date` / `delisted_date` as normal.
8. Release lock.
9. Call `coalesce_data_gaps(symbol, 'daily')` — called by `run_daily_refetch` after
   `_do_daily_symbol` returns, not inside it. This keeps the normal daemon cycle
   (which calls `_do_daily_symbol` directly) unchanged.
10. Return `CycleReport`.

**`run_minute_refetch`** location: `src/manta_trading/data/acquisition/daemon/minute.py`

```python
def run_minute_refetch(
    symbol: str,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
) -> CycleReport:
```

Behavior:
1. Resolve window: clamp `from_date` to `max(first_data_date, today - MINUTE_HISTORY_MONTHS)`;
   clamp `to_date` to last completed trading session close UTC.
2. Acquire advisory lock on `(symbol, 'minute')`.
3. Run CA drift check (load-bearing for minute; same as normal cycle).
4. Chunk-loop over `[from_date, to_date]` in `provider_max_chunk_days` (120-day) slices,
   most-recent-first:
   - Fetch EODHD intraday for the chunk.
   - Insert bars, apply band adjustments.
   - Call `update_data_gaps(..., force_reset_terminal=True)` for the chunk range.
5. After loop: `coalesce_data_gaps(symbol, 'minute')`.
6. Update `last_adjusted_ca_snapshot_id`.
7. Release lock. Return `CycleReport`.

### Implementation approach: extend `_do_*_symbol`, not duplicate

Rather than duplicating the per-symbol logic, the existing `_do_daily_symbol` and
`_do_minute_symbol` functions gain an optional `force_reset_terminal: bool = False`
parameter and an optional `window: tuple[date, date] | None = None` parameter. The
new `run_daily_refetch` / `run_minute_refetch` entry points call these with the flag
set and the window constrained. Normal daemon cycles call them with defaults (no change
to existing behavior).

This keeps the fetch, lock, insert, and gap-update logic in one place.

### Advisory lock discipline

The advisory lock on `(symbol, granularity)` is acquired once — inside `_do_daily_symbol` /
`_do_minute_symbol` at the start of the per-symbol work, exactly as the normal daemon path
does today. It is held for the duration of the fetch, insert, `update_data_gaps`, and
`coalesce_data_gaps` calls, then released. `update_data_gaps` and `coalesce_data_gaps` run
within that already-held session lock; they do not re-acquire it independently. PostgreSQL
advisory lock reentrance (same session, same lock key) is not relied upon — the lock is held
at the `_do_*_symbol` level for the full operation, and `update_data_gaps` / `coalesce_data_gaps`
are called while that lock is held.

The dependencies table note "Inherited — `update_data_gaps` acquires it" describes where the
locking call lives in the existing code, not that it acquires a new outer lock. No new locking
code is added by this slice.

### Failure modes

| Scenario | Behavior |
|----------|----------|
| `pg_try_advisory_lock` returns false (daemon holds lock) | `_do_*_symbol` raises immediately; CLI surfaces "Symbol SPY is locked by another process — is the daemon running?" and exits non-zero. Operator retries after daemon cycle completes. |
| EODHD API timeout / hang | Inherited from existing per-symbol HTTP path (connection timeout from `httpx`). CLI propagates the exception as a non-zero exit with the error message. No partial DB state — the transaction is not committed if the fetch fails. |
| EODHD disconnect mid-response | Same as above — transaction rolled back on exception. `data_gaps` rows for the window remain at their pre-refetch state (terminal rows are not reset until `update_data_gaps` commits). |
| EODHD HTTP 4xx (non-429) | Propagates and exits non-zero. Same as daemon behavior — 4xx is not retried. |
| Partial minute chunk failure | Already-processed chunks committed. Failed chunk leaves those gap rows in their post-reset state (`UNKNOWN, attempt_count=0` if `force_reset_terminal` fired). Daemon will retry them on next cycle. `CycleReport` reflects `transient_failure_count > 0`; CLI prints this clearly. |
| DB write failure mid-insert | Transaction rolled back; `data_gaps` and `daily_ohlcv` / `minute_ohlcv` remain consistent. |

### Credit consumption

`mt data refetch` runs outside the daemon's `QuotaBucket` — it does not consume from the
daemon's rolling credit budget. Operators should account for this when running refetch
concurrently with the daemon:

| Operation | Credit cost |
|-----------|-------------|
| Daily refetch (any window size) | 1 credit (single `/eod?output_size=full` call) |
| Minute refetch — per chunk | 5 credits (`EODHD_INTRADAY_CALL_COST`) |
| Minute refetch — full 24-month window | ~3 chunks × 5 = 15 credits |
| Minute refetch — full 22-year window (clamped to `MINUTE_HISTORY_MONTHS`) | same as full window |

Running a minute refetch concurrently with a busy daemon cycle is safe but may contribute
to hitting `EODHD_PER_MINUTE_BURST` (1000 req/min) or `EODHD_DAILY_QUOTA` (100k credits/day).
At the credit costs above, a single-symbol refetch is negligible against those limits.

### CLI command flow

```
mt data refetch --symbol SPY
```

1. Validate `--symbol` is in instrument registry; raise `typer.BadParameter` if not found.
2. Validate `--from` ≤ `--to` if both provided.
3. Resolve granularities from `--daily` / `--minute` flags → `['daily']`, `['minute']`,
   or `['daily', 'minute']`.
4. **Dry-run path** (or pre-confirmation preview): query `data_gaps` for terminal rows
   in `(symbol, granularities, from_date, to_date)` scope. Print preview table. If
   `--dry-run`, exit 0.
5. If no terminal gaps and no `--yes`: print "No terminal gaps in scope for {symbol}.
   Refetch anyway? [y/N]" — lets operator proceed even if no terminal gaps exist (they
   may want to refresh bars regardless).
6. If not `--yes` / `--json`: prompt "Refetch {symbol} ({granularities}, {window})? [y/N]".
7. Call `run_daily_refetch` and/or `run_minute_refetch` per resolved granularities.
8. Print summary from `CycleReport`.

### Output (non-JSON)

**Pre-confirmation preview (terminal gaps in scope):**

```
Terminal gaps for SPY in window 2023-01-01 → 2024-12-31 (daily)

  gap_start    gap_end      fetch_status       attempts
  2023-11-15   2023-11-15   RETRY_EXHAUSTED    5
  2023-12-01   2023-12-05   PROVIDER_HOLE      3

2 terminal gap(s) will be reset and re-fetched.
```

**No terminal gaps in scope:**

```
No terminal gaps for SPY in scope.
Refetch anyway? [y/N]:
```

**Success:**

```
Refetch complete for SPY (daily).
  Bars inserted: 253
  Gaps resolved: 2
  Outcome: success
```

**Dry-run:**

```
Dry run — no changes made.
```

### JSON mode output

```json
{
  "symbol": "SPY",
  "granularities": ["daily"],
  "from_date": "2023-01-01",
  "to_date": "2024-12-31",
  "dry_run": false,
  "daily": {"success_count": 1, "transient_failure_count": 0},
  "minute": null
}
```

JSON mode skips the confirmation prompt.

## Data Flow

```
Operator: mt data refetch --symbol SPY --daily

  CLI (data.py)
    │
    ├─ validate symbol, resolve window + granularities
    ├─ preview terminal gaps (SELECT from data_gaps)
    ├─ confirm
    └─ run_daily_refetch("SPY", from_date=..., to_date=...)
         │
         └─ _do_daily_symbol("SPY", window=(...), force_reset_terminal=True)
              │
              ├─ pg_try_advisory_lock(symbol, 'daily')  ← advisory lock
              ├─ CA drift check + optional band recompute
              ├─ EODHD /eod fetch for window
              ├─ INSERT INTO daily_ohlcv ... ON CONFLICT DO UPDATE
              ├─ band-based adj_* UPDATE
              └─ update_data_gaps(..., force_reset_terminal=True)
                   │
                   └─ step 2: reset PROVIDER_HOLE / RETRY_EXHAUSTED in scope
                        → UNKNOWN, attempt_count=0 (in-memory before carry-forward)
                   └─ pg_advisory_unlock(symbol, 'daily')
```

## Dependencies

| Dependency | Direction | Notes |
|------------|-----------|-------|
| `data_gaps` table | Read + Write | Via `update_data_gaps` — no direct writes |
| `instruments` table | Read (symbol validation, window defaults) | Slice 141 |
| `update_data_gaps` | Existing — extend with `force_reset_terminal` | Already implemented (slice 145) |
| `coalesce_data_gaps` | Existing | Already implemented (slice 145) |
| `_do_daily_symbol` / `_do_minute_symbol` | Extend — add `force_reset_terminal`, `window` params | Slice 145/146 |
| Advisory lock | Held by `_do_*_symbol` for full operation; `update_data_gaps` / `coalesce_data_gaps` run within it | No new locking code |
| Slice 147 (`mt data status`) | Upstream — status reveals the terminal gaps refetch addresses | No code dependency |
| Daemon acquisition loop | Downstream — normal retry after reset; no coupling | — |

## Technical Decisions

**D1 — Single-symbol scope only.**
No `--all` or `--list` flag. Terminal gaps warrant operator review before re-fetching.
If bulk is needed, a shell loop over `mt data status --json` output is the right tool
and keeps each refetch visible in logs.

**D2 — Extend `_do_*_symbol`, do not duplicate.**
The fetch/lock/insert/gap-update logic lives in one place. Adding `force_reset_terminal`
and `window` parameters to the existing functions means the refetch path gets every
correctness fix that lands on the normal daemon path for free.

**D3 — `force_reset_terminal=True` is passed to `update_data_gaps`, not handled separately.**
The architecture specifies this flag on `update_data_gaps` for this purpose. It resets
terminal rows in the in-memory snapshot under the advisory lock before the carry-forward
step. This is the correct and only safe way to reset terminal state — outside the lock
is a concurrency hazard.

**D4 — Window defaults match the daemon's full-history behavior.**
`from_date` defaults to `first_data_date`; `to_date` defaults to last completed session.
This matches what a normal daemon cycle would cover, so an unscoped refetch is equivalent
to "redo everything the daemon would have done for this symbol."

**D5 — `--daily` / `--minute` bool flags (not `--granularity TEXT`).**
Consistent with `mt data status` (slice 147). Neither or both = both granularities.

**D6 — Dry-run shows terminal gaps, does not simulate the fetch.**
A full fetch simulation would require provider calls. Dry-run value is confirming which
terminal rows are in scope before the operator commits to a provider call.

**D7 — Confirmation prompt shown even when no terminal gaps.**
The operator may want to re-fetch bars that are present but suspect, without any terminal
gaps existing. The prompt reflects this: no terminal gaps is noted, but the refetch is
not blocked.

## Integration Points

- `src/manta_trading/cli/commands/data.py` — add `@data_app.command("refetch")`
- `src/manta_trading/data/acquisition/daemon/daily.py` — add `run_daily_refetch`;
  extend `_do_daily_symbol` with `force_reset_terminal: bool = False` and
  `window: tuple[date, date] | None = None`
- `src/manta_trading/data/acquisition/daemon/minute.py` — add `run_minute_refetch`;
  extend `_do_minute_symbol` with same parameters
- `src/manta_trading/data/gaps/update_data_gaps.py` — `force_reset_terminal` parameter
  already exists (slice 145); no change needed
- `src/manta_trading/data/gaps/coalesce_data_gaps.py` — already exists; no change needed

## Success Criteria

1. `mt data refetch --symbol SPY --dry-run` prints terminal gaps in scope without
   making any provider calls or DB writes.
2. `mt data refetch --symbol SPY` shows a preview, prompts for confirmation, and
   fetches on "y".
3. `mt data refetch --symbol SPY --yes` fetches without prompting.
4. After a successful refetch, terminal gaps in the window are no longer
   `PROVIDER_HOLE` or `RETRY_EXHAUSTED` in `data_gaps`.
5. `mt data status --symbol SPY` reflects the updated gap state after refetch.
6. `--daily` alone refetches only daily granularity; `--minute` alone refetches only
   minute; both or neither refetches both.
7. `--from` / `--to` constrain the fetch window passed to `_do_*_symbol`; bars and
   gap rows outside the window are unaffected.
8. The advisory lock on `(symbol, granularity)` is held during the refetch (verified
   by concurrent access test: daemon blocked on same symbol during refetch).
9. `force_reset_terminal=True` is passed to `update_data_gaps`; terminal rows in scope
   are reset to `UNKNOWN, attempt_count=0` before the chunk is re-attempted.
10. `coalesce_data_gaps` runs after all chunks are processed for both daily and minute granularities.
11. An unknown symbol exits with a clear error and non-zero status code.
12. A refetch of a symbol with no terminal gaps completes successfully when confirmed.
13. `--json` output is valid JSON, skips prompt, includes `CycleReport` outcomes.
14. Unit tests cover: `force_reset_terminal` flag propagation, window clamping,
    `--daily`/`--minute` flag resolution, dry-run (no mutations), `--yes` skips prompt.
15. The normal daemon cycle (`run_daily_cycle`, `run_minute_cycle`) behavior is
    unchanged — `force_reset_terminal` defaults to `False`.

## Verification Walkthrough

Verified 2026-05-04 against test DB using symbol AAPL (SPY not in registry).

### Setup: seed a terminal gap

```python
import psycopg
with psycopg.connect(MT_TIMESCALE_DB_URL) as conn:
    conn.execute("""
        INSERT INTO data_gaps (symbol, granularity, gap_start, gap_end, fetch_status, attempt_count)
        VALUES ('AAPL', 'daily', '2023-11-15', '2023-11-15', 'RETRY_EXHAUSTED', 5)
        ON CONFLICT DO NOTHING
    """)
    conn.commit()
```

### Step 1 — Confirm gap visible in status

```bash
mt data status --symbol AAPL --health FAILED
```

Actual output:
```
╭──────────────────────────────── AAPL / daily ────────────────────────────────╮
│ health: FAILED  gap_count: 1  ...                                            │
╰──────────────────────────────────────────────────────────────────────────────╯
data_gaps: daily | 2023-11-15 | 2023-11-15 | RETRY_EXHAUSTED | 5
```

### Step 2 — Dry run

```bash
mt data refetch --symbol AAPL --daily --from 2023-11-01 --to 2023-11-30 --dry-run
```

Actual output: preview table shows the 2023-11-15 gap; "Dry run — no changes made."
Gap still RETRY_EXHAUSTED in DB after dry-run (confirmed via psycopg query).

### Step 3 — Refetch with --yes

```bash
mt data refetch --symbol AAPL --daily --from 2023-11-01 --to 2023-11-30 --yes
```

Actual output:
```
1 terminal gap(s) will be reset and re-fetched.
[INFO] CaSnapshot for AAPL: 5 splits, 91 dividends...
Refetch complete for AAPL (daily).
  Outcome: success
```

### Step 4 — Confirm gap resolved

```bash
mt data status --symbol AAPL
```

Actual output: FAILED: 0 — gap row removed (filled by refetch).
`data_gaps` WHERE symbol='AAPL' AND gap_start='2023-11-15' → 0 rows after refetch.

### Step 5 — Verify advisory lock behavior

Manual-only (concurrent process coordination is not automatable in CI).
Start refetch in background, start daemon on same symbol: second invocation blocks.

### Step 6 — Unknown symbol

```bash
mt data refetch --symbol NOTREAL
```

Actual output: `Error: Symbol NOTREAL not found in instrument registry.` exit 1.

### Cleanup

Gap was resolved by the refetch in Step 3 (row gone from data_gaps).
No manual DELETE needed.
