---
docType: slice-design
slice: point-in-time-universe-populate-lifecycle-dates-for-delisted-symbols
project: trading
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [158]
interfaces: []
dateCreated: 20260514
dateUpdated: 20260514
status: complete
---

# Slice Design: Point-in-Time Universe — Populate Lifecycle Dates for Delisted Symbols

## Overview

As of slice 158, the `instruments` table contains 31,688 rows: ~12,946 active
and ~18,742 delisted (`delisted_at_eodhd = true`). All 18,742 delisted rows
have `delisted_date IS NULL`. Without `delisted_date` populated, the
point-in-time universe query

```sql
SELECT symbol FROM instruments
WHERE COALESCE(first_listing_date, first_data_date) <= :window_end
  AND (delisted_date IS NULL OR delisted_date >= :window_start)
```

is incorrect: every delisted symbol matches the `delisted_date IS NULL` branch
and bleeds into every historical window, regardless of when it stopped trading.

This slice closes that gap with two operator steps:

1. **New command `mt data instruments populate-delisted-dates`** — fetches a
   single bar per delisted symbol from EODHD (`/eod/{SYM}?limit=1&order=d`)
   at 1 credit each, sets `instruments.delisted_date` to that bar's date.
   Costs ~18.7k credits for a full run; completes within a single quota day.

2. **Finnhub enrichment pass** — re-run `mt data instruments rebuild` (no
   `--skip-finnhub`) to populate `first_listing_date` for delisted symbols
   that are still NULL. No code change; the orchestrator already handles this
   path resumably.

After both steps, the point-in-time query is correct for any window within
EODHD's history. Survivorship bias is eliminated for backtests that filter
their candidate universe through `symbols_active_on(date D)`.

## Value

**Backtest correctness**: a strategy that entered SPY on 2010-01-01 and exited
2023-12-31 should only consider symbols that were actually trading in that
window. Without `delisted_date`, symbols that delisted in 2008 appear as
candidates for a 2010 backtest. This is the survivorship bias the architecture
was designed to prevent.

After this slice, the operator has a correct point-in-time universe. Full bar
history for delisted symbols is a separate pull (`mt data pull --universe
--include-delisted`); this slice only sets the lifecycle boundary dates.

## Technical Scope

**Included:**
- New function `populate_delisted_dates(conn, *, api_key, dry_run, on_progress)`
  in `src/manta_trading/data/universe/populate_delisted_dates.py`
- New CLI command `mt data instruments populate-delisted-dates [--dry-run] [-v]`
  added to `instruments_app` in `data.py`
- Unit tests for the core function (mocked EODHD responses) and CLI command

**Excluded:**
- No migration — `delisted_date date` already exists (migration 015, slice 141)
- No bar storage — this is a date-metadata fetch only; bars for delisted symbols
  are pulled separately via `mt data pull --universe --include-delisted`
- No change to `iter_active_instruments` or daemon paths
- No change to `data_gaps` / `acquisition_state` — these are not gap rows
- No change to `instruments rebuild` — Finnhub enrichment already handles
  symbols with NULL `first_listing_date` resumably

## Dependencies

### Prerequisites
- Slice 158 — `--universe` delisted filter and `--include-delisted` flag.
  The active/delisted count assumptions are based on the post-157 universe
  (~12,946 active, ~18,742 delisted with `delisted_at_eodhd = true`).

### Interfaces Required
- `eodhd_get` from `manta_trading.api.eodhd_sync` — sync EODHD HTTP helper
  with `QuotaBucket` consumption; `CallType.EOD` costs 1 credit
- `QuotaBucket` (via `eodhd_get`) for quota enforcement
- `instruments` table: `symbol`, `delisted_at_eodhd`, `delisted_date` columns
- Settings (`MT_TIMESCALE_DB_URL`, `MT_EODHD_API_KEY`) via env

## Architecture

### Component Structure

```
src/manta_trading/data/universe/populate_delisted_dates.py
  populate_delisted_dates(conn, *, api_key, dry_run, on_progress)
  PopulateDelistedDatesReport

src/manta_trading/cli/commands/data.py
  instruments_app.command("populate-delisted-dates")
  → calls populate_delisted_dates

test/unit/data/universe/test_populate_delisted_dates.py
```

### Data Flow

```
instruments WHERE delisted_at_eodhd = true AND delisted_date IS NULL
  → ordered list of symbols
  → for each symbol:
      GET /eod/{SYM}.US?limit=1&order=d&api_token=...&fmt=json
      response[0]["date"]  (or skip if empty)
      UPDATE instruments SET delisted_date = :date WHERE symbol = :sym
  → PopulateDelistedDatesReport(updated, skipped_empty, error_count)
```

### Implementation Details

#### Core Function

`populate_delisted_dates` in `populate_delisted_dates.py`:

```python
@dataclass(frozen=True)
class PopulateDelistedDatesReport:
    total: int
    updated: int
    skipped_empty: int   # EODHD returned no bars — symbol has no history
    error_count: int


def populate_delisted_dates(
    conn: psycopg.Connection,
    *,
    api_key: str,
    dry_run: bool = False,
    on_progress: Callable[[int, int, str, date | None], None] | None = None,
) -> PopulateDelistedDatesReport:
```

Algorithm:
1. Query `SELECT symbol FROM instruments WHERE delisted_at_eodhd = true AND delisted_date IS NULL ORDER BY symbol ASC`.
2. For each symbol: call `eodhd_get(url, ...)` with `CallType.EOD`, where URL is
   `/eod/{SYM}.US?limit=1&order=d&api_token=...&fmt=json`.
3. Parse JSON response. If list is empty → `skipped_empty++`, continue.
4. Extract `response[0]["date"]` → `last_bar_date: date`.
5. If not `dry_run`: `UPDATE instruments SET delisted_date = :date WHERE symbol = :sym`.
6. Call `on_progress(processed, total, symbol, last_bar_date)` if supplied.
7. Return `PopulateDelistedDatesReport`.

Error handling:
- HTTP 4xx (non-429): log at ERROR and increment `error_count`; continue to
  next symbol. Do not halt the entire batch on a single bad symbol.
- `KeyError` on `response[0]["date"]`: log at ERROR, `error_count++`, continue.
- `eodhd_get` handles 429 / Retry-After internally (existing behavior).

Each UPDATE is a standalone statement (no batch transaction). Resumability: on
re-run, `delisted_date IS NULL` filter naturally skips already-updated symbols.

#### EODHD Endpoint

`GET https://eodhd.com/api/eod/{SYMBOL}.US?api_token=...&fmt=json&order=d&limit=1`

- Returns a JSON array of at most 1 element (most recent bar).
- Empty array = EODHD has no bar data for this symbol (graceful skip).
- `limit=1&order=d` is consistent with the EODHD API's documented `order` and
  `limit` query parameters for the `/eod` endpoint.
- Symbol normalization: append `.US` suffix for bare US equity tickers
  (same rule as `EODHDDailyProvider._normalise_symbol`).
- Cost: 1 credit per call (`CallType.EOD`).

#### CLI Command

```
mt data instruments populate-delisted-dates [--dry-run] [-v / --verbose]
```

- Reads `MT_TIMESCALE_DB_URL` and `MT_EODHD_API_KEY` from environment.
- `--dry-run`: queries the symbol list and logs what would be done; no DB
  writes; still consumes quota (the API calls happen to get the dates).
- `-v / --verbose`: print one line per symbol (symbol, resolved date or
  EMPTY). Without `-v`, prints only the final summary.
- Exits 0 on completion; exits 1 if `error_count > 0`.

Output (default, non-verbose):
```
Populating delisted_date for 18742 symbols...
Done. updated=18210 skipped_empty=532 errors=0
```

## Integration Points

### Consumes from Other Slices
- Slice 158 cleaned the instruments table of preferred stock and established
  the `delisted_at_eodhd` / `delisted_date` semantics this slice depends on.
- `eodhd_sync.eodhd_get` + `QuotaBucket` from slices 145/146 — no change.

### Provides to Other Slices
- After this slice, the point-in-time universe query in the arch spec
  (`symbols_active_on(date D)`) is correct. Slice 161 (index constituent
  tracking) can depend on a correct active universe. Any backtest scaffold
  that gates on `symbols_active_on` also depends on this.

## Success Criteria

### Functional Requirements

1. `SELECT COUNT(*) FROM instruments WHERE delisted_at_eodhd = true AND delisted_date IS NULL`
   drops from 18,742 to a small residual (only symbols for which EODHD returned
   no bars at all). Practical target: < 100 remaining NULL after a full run.

2. For a known delisted symbol (e.g. `AAAB`, confirmed delisted before 2003 and
   verifiable in EODHD), `delisted_date` is populated with the correct last bar
   date after the command runs.

3. Point-in-time query is correct:
   ```sql
   SELECT COUNT(*) FROM instruments
   WHERE COALESCE(first_listing_date, first_data_date) <= '2000-01-01'
     AND (delisted_date IS NULL OR delisted_date >= '2000-01-01');
   ```
   Returns a plausible 1990s–2000 universe count (not the full 31k).

4. `--dry-run` produces a count report without modifying any `delisted_date`
   values; re-running `SELECT COUNT(*) WHERE delisted_date IS NULL` after a
   dry run returns the same pre-run count.

5. Running the command twice is a no-op on the second run (zero rows in the
   `WHERE delisted_at_eodhd = true AND delisted_date IS NULL` query).

6. After the Finnhub enrichment step (`mt data instruments rebuild` without
   `--skip-finnhub`), `SELECT COUNT(*) FROM instruments WHERE first_listing_date IS NULL`
   decreases meaningfully compared to pre-run (Finnhub enriches what it can;
   some symbols remain NULL if Finnhub has no data).

### Technical Requirements

- Pyright strict: zero new errors.
- All existing unit tests continue to pass.
- New unit tests cover: happy-path (bars returned, date written), empty-response
  (skip), `--dry-run` mode, and progress callback invocation.

## Risk Assessment

### Quota consumption

18,742 symbols × 1 credit = 18,742 credits. EODHD daily quota is 100,000.
The command uses ~19% of daily quota; leaves the remaining ~81% for the daemon.
Operators should run this once on a low-traffic day and allow the daemon
to continue normal operation alongside.

If the operator needs to preserve quota, the command is naturally resumable:
any symbols updated in a partial run are excluded from the next run.

### Symbols with no EODHD bar data

Some delisted symbols may have no bars in EODHD (thinly-traded names, very
old listings, foreign crosslistings that made it into the US bulk list). The
command gracefully skips them (`skipped_empty` count) and leaves their
`delisted_date = NULL`. The point-in-time query's `delisted_date IS NULL`
branch means they remain visible in all historical windows, which slightly
overstates the universe. The count will be small (estimated < 1% of total).

## Implementation Notes

### Development Approach

Suggested order:
1. `populate_delisted_dates.py` — core function + report dataclass
2. Unit tests (mocked `eodhd_get`)
3. CLI command in `data.py`
4. CLI integration test (mocked DB + mocked HTTP)
5. Manual verification on `trading_test`, then prod

### Testing Strategy

Unit tests mock `eodhd_get` (not actual HTTP). Three key fixtures:
- `[{"date": "2003-07-15", "close": 1.23, ...}]` — happy path
- `[]` — empty response (skip)
- HTTP exception — error path

The CLI test invokes the Typer runner with mocked DB + `eodhd_get`; asserts
on exit code and printed output.

### Operator Sequence for Full Lifecycle-Date Population

Both steps are required for a complete point-in-time universe:

```bash
# Step 1 — populate delisted_date for all delisted symbols
MT_TIMESCALE_DB_URL="..." MT_EODHD_API_KEY="..." \
  mt data instruments populate-delisted-dates

# Step 2 — populate first_listing_date via Finnhub (resumable; ~8-9h at 60/min)
MT_TIMESCALE_DB_URL="..." MT_FINNHUB_API_KEY="..." \
  mt data instruments rebuild
```

Step 2 uses the existing `instruments rebuild` command with its built-in
Finnhub rate limiter (60/min). The operator does not need to wait for step 2
to complete before using the universe for `delisted_date`-based filtering —
step 1 already closes the survivorship bias for the `delisted_date` bound.
Step 2 improves `first_listing_date` coverage (the lower bound) and is
independently useful.

## Verification Walkthrough

**Prerequisites:** `MT_EODHD_API_KEY` set in environment (or `.env`); prod DB at `<db-host>:5432/trading`.

**1. Confirm baseline — all delisted symbols missing `delisted_date`:**
```bash
psql "postgresql://postgres:<password>@<db-host>:5432/trading" -c \
  "SELECT COUNT(*) AS missing FROM instruments WHERE delisted_at_eodhd = true AND delisted_date IS NULL;"
```
Expected: `18742`

**2. Dry-run — reports counts, no DB writes:**
```bash
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading" \
  mt data instruments populate-delisted-dates --dry-run
```
Expected output (actual from 2026-05-14 run):
```
DRY RUN — would update=18742 skipped_empty=0 errors=0
```

**3. Confirm dry-run did not write:**
```bash
psql "postgresql://postgres:<password>@<db-host>:5432/trading" -c \
  "SELECT COUNT(*) FROM instruments WHERE delisted_at_eodhd = true AND delisted_date IS NULL;"
```
Expected: still `18742` ✓

**4. Real run (operator step — takes several hours at ~1 credit/symbol):**
```bash
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading" \
  mt data instruments populate-delisted-dates -v
```
Expected: lines like `AAAB: 2003-01-29`, then `Done. updated=N skipped_empty=N errors=0`

**5. Confirm symbols now have `delisted_date`:**
```bash
psql "postgresql://postgres:<password>@<db-host>:5432/trading" -c \
  "SELECT COUNT(*) FROM instruments WHERE delisted_at_eodhd = true AND delisted_date IS NULL;"
```
Expected: ≤ skipped_empty count from step 4 ✓

**6. Spot-check a known delisted symbol:**
```bash
psql "postgresql://postgres:<password>@<db-host>:5432/trading" -c \
  "SELECT symbol, delisted_at_eodhd, delisted_date FROM instruments WHERE symbol = 'AAAB';"
```
Expected: `delisted_date` is a non-NULL date (last trading day for AAAB)

**7. Point-in-time universe query sanity check:**
```bash
psql "postgresql://postgres:<password>@<db-host>:5432/trading" -c \
  "SELECT COUNT(*) AS universe_2000 FROM instruments
   WHERE COALESCE(first_listing_date, first_data_date) <= '2000-01-01'
     AND (delisted_date IS NULL OR delisted_date >= '2000-01-01');"
```
Expected: plausible non-zero count (not 31k, not 0) ✓

**8. Second run is a no-op:**
```bash
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading" \
  mt data instruments populate-delisted-dates
```
Expected: `Done. updated=0 skipped_empty=0 errors=0`

**9. Unit tests:**
```bash
uv run pytest test/unit/universe/test_populate_delisted_dates.py -v
```
Expected: 5 tests pass ✓

**10. Full test suite:**
```bash
uv run pytest test/unit -q
```
Expected: all tests pass, no regressions ✓
