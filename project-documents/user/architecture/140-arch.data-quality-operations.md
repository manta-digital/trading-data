---
docType: architecture
component: data-quality-operations
project: trading
parent: user/project-guides/001-initiative-plan.trading.md
dependencies:
  - 100-arch.data-storage
  - 120-arch.data-acquisition
relatedReference: user/reference/data-correctness-architecture.md
archIndex: 140
dateCreated: 20260429
dateUpdated: 20260717
status: in-progress
---

# Data Quality & Operations Architecture

## Purpose

Make the data layer transparent and trustworthy for one operator. Answer
three questions on demand:

1. For symbol X, what data do we have, at what granularity, when?
2. Where are the holes?
3. Are the stored prices correct?

Everything in this document exists to answer those three questions
without requiring the operator to read code or run ad-hoc SQL.

## Scope

Equities daily and minute. One provider per granularity (EODHD by
default). One operator. Local execution.

Tick data and futures are out of scope here — they re-use the same
mechanisms under initiative 200.

## Design

### One control table

```
data_gaps (
  symbol           text not null,
  granularity      text not null,           -- 'daily' | 'minute'
  gap_start        timestamptz not null,    -- inclusive, normalized to session-open UTC
  gap_end          timestamptz not null,    -- inclusive, normalized to session-close UTC
  fetch_status     text not null,           -- 'UNKNOWN' | 'PROVIDER_HOLE' | 'FAILED_RETRYABLE' | 'RETRY_EXHAUSTED'
  last_attempt_ts  timestamptz,
  attempt_count    int not null default 0,
  primary key (symbol, granularity, gap_start, gap_end)
)
```

`data_gaps` lists only **MISSING** ranges within the in-scope window.
`PRE_LISTING`, `DELISTED`, and `HOLIDAY` ranges are filtered out by the
gap computation — they are never stored.

#### Gap granularity

A "gap" is always **session-granular**, never bar-granular. A session
where the calendar says trading occurred and the data table contains
**zero bars** for the symbol is a gap. A session with any positive bar
count is "present" — light-trading days, sparse symbols, partial
sessions are all considered present. Gap detection asks "is the
session there at all," not "does the session look healthy."

This means a session with 1 bar and a session with 960 bars are both
"present." Quality concerns about under-bar sessions are handled by
audit, not by gap detection. Conflating completeness with quality
produces noise.

#### Timestamp normalization

Both daily and minute gaps express ranges as `[session_open_utc,
session_close_utc]` for the symbol's exchange. The `trading_calendar`
table provides per-(exchange, date) `session_open` and `session_close`
in exchange-local time; these are converted to UTC at gap-computation
time. This makes inclusive-range queries against
`daily_ohlcv.time` and `minute_ohlcv.time` cleanly aligned, with no
off-by-one risk from timezone shifts.

### One status view

```
data_status AS
  per (symbol, granularity):
    first_bar_ts          = MIN(time)  from data table
    last_bar_ts           = MAX(time)  from data table
    bars_stored           = COUNT(*)   from data table
    bars_expected         = expected sessions from trading_calendar within target window
    gap_count             = COUNT(*) from data_gaps within target window
    last_attempt_ts       = LEFT JOIN acquisition_state (NULL if no row)
    last_attempt_outcome  = LEFT JOIN acquisition_state (NULL if no row)
    health                = derived per rules below
```

A view, not a table. Always consistent with the underlying data.

#### Performance pattern

Naive per-row computation of `most_recent_completed_session_close_utc(exchange)`
would invoke a function once per symbol — slow at universe scale
(~57k rows). The view materializes per-exchange completed-session
boundaries into a small lookup CTE before joining:

```
WITH exchange_completed_close AS (
  SELECT exchange,
         MAX(session_close_utc) AS completed_close_ts
  FROM trading_calendar
  WHERE session_close_utc + INTERVAL '30 minutes' < NOW()
  GROUP BY exchange
)
SELECT s.symbol, s.granularity, ...
FROM symbols s
JOIN exchange_completed_close ec
     ON ec.exchange = i.trading_calendar_id
LEFT JOIN acquisition_state ast
     ON ast.symbol = s.symbol AND ast.granularity = s.granularity
...
```

> **Implementation note (slice-142 deferral; slice 144 resolves).** The
> `trading_calendar` table referenced above does **not exist** in the
> implementation today. Sessions are computed in Python by
> `TradingCalendar` from `trading_calendars` + `trading_holidays`.
> Slice 142 ships `data_status` with `target_end_ts = NULL` rather than
> reimplement timezone + holiday + early-close logic in SQL. Slice 144
> lands the materialized `trading_sessions(calendar_id, session_date,
> session_open_utc, session_close_utc)` table that this CTE assumes,
> populated from the calendar + holiday tables by a maintenance job
> with a per-year horizon, and rewrites the view's CTE to project
> `target_end_ts` per arch. Python `TradingCalendar` is refactored at
> the same time to read from `trading_sessions` (single source of
> truth, eliminating Python/SQL drift risk).

The join uses `i.trading_calendar_id`, not `i.venue`. Reason: `venue` is the
trading venue (NASDAQ, NYSE, NYSE_ARCA, BATS, NYSE_MKT, INDX, plus a transient
`'US'` placeholder for symbols whose venue is not yet authoritatively determined),
while `trading_calendar_id` is the authoritative pointer to the session calendar.
An ETF on `NYSE_ARCA` follows the `NYSE` calendar; an unknown-venue symbol with
`venue='US'` falls back to `trading_calendar_id='NYSE'` (the conservative US-equity
calendar, set by slice 141's rebuild). Joining on `trading_calendar_id` makes
`data_status` work for all rows; joining on `venue` would exclude `'US'` rows
entirely.

The CTE returns ~5 rows (one per exchange in our universe).
`target_end` joins from this lookup, avoiding per-row function calls.
View latency stays sub-second at full-universe scope.

#### Target window

For each (symbol, granularity), the **target window** is:

```
target_start = max(first_trade_date, today - history_months)
target_end   = most_recent_completed_session_close_utc(exchange)
```

`history_months` is configured per-granularity — `unbounded` for daily.
Minute history is full-to-`EODHD_INTRADAY_HORIZON` (2004-01-01) by default,
not a month-count; narrowable per-deployment via `MT_MINUTE_HISTORY_START`
(see slice 162 §History window — the earlier `MINUTE_HISTORY_MONTHS = 24`
NFR was a dead AlphaVantage workaround, never implemented, and has been
removed from this doc).

`most_recent_completed_session_close_utc(exchange)` returns the close
timestamp of the most recent session whose
`session_close_utc + LATE_BAR_GRACE_PERIOD < now()`. During an active
trading session, this is the *previous* session's close — today's
session is not yet expected, so today's bars not yet existing does
not produce false gaps. After today's close + grace period, today's
session enters the target window and missing bars are flagged.

`LATE_BAR_GRACE_PERIOD` (default 30 min) absorbs vendors that publish
the last bars of a session shortly after close. Configurable.

Status answers "do we have everything we should have within the period
we care about" — not "is what we have internally complete." A symbol
that the daemon has never attempted shows as `STALE` because its
target window is empty of bars, not OK because the empty interval is
internally consistent.

#### Health rules

```
if EXISTS data_gaps row in target window with fetch_status = RETRY_EXHAUSTED:
    health = FAILED
elif last_attempt_ts IS NULL
     OR last_attempt_ts < (today - STALENESS_THRESHOLD):
    health = STALE
elif gap_count > 0:
    health = GAPS
else:
    health = OK
```

The view uses **LEFT JOIN** against `acquisition_state` so a symbol
with no acquisition_state row at all (newly added, daemon hasn't
attempted) gets `last_attempt_ts = NULL` → falls through to STALE.

`STALENESS_THRESHOLD` is a configured constant (see Constants
section). Daily `STALENESS_THRESHOLD` defaults to 2 days; minute
defaults to 1 day.

`MAX_RETRY_COUNT` lives in `data_gaps` enforcement only —
`update_data_gaps` promotes a row's `fetch_status` to
`RETRY_EXHAUSTED` when its `attempt_count` reaches the cap. Health
checks for the *existence* of any RETRY_EXHAUSTED row in the
target window; one is enough for the symbol to be FAILED.

### Slimmed `acquisition_state`

```
acquisition_state (
  symbol, granularity, provider,
  last_attempt_ts, last_attempt_outcome,
    -- 'success' | 'partial' | 'empty' | 'transient_failure'
  last_adjusted_ca_snapshot_id    -- references most recent CA snapshot
                                  -- adjustments were computed against
)
```

`retry_count` is intentionally absent from `acquisition_state`. The
authoritative retry tracker is `data_gaps.attempt_count` per gap row.
A symbol-level retry counter would conflate distinct gaps and create
double-bookkeeping. Health rule for FAILED references `data_gaps`
directly, not a separate symbol-level column.

`last_success_ts` is removed. It is now `MAX(time) FROM <data table>`,
queryable directly. The provider-tag conflation that broke status
during the slice 128 dry-run is removed because filtering for status
uses the data tables, not provider-tagged acquisition_state rows.

#### Mapping `last_attempt_outcome` to `data_gaps.fetch_status`

When the daemon or refetch processes a chunk, it emits both an
`acquisition_state.last_attempt_outcome` and updates `data_gaps`
rows for the chunk's range. The mapping is:

| `last_attempt_outcome` | Effect on `data_gaps` |
|---|---|
| `success` | gap rows in chunk range removed (recomputed; result is empty) |
| `partial` | gap rows for filled portion removed; gap rows for missing portion remain with `fetch_status = UNKNOWN` (will retry) |
| `empty` | gap rows in chunk range have `fetch_status = PROVIDER_HOLE`, `attempt_count++` |
| `transient_failure` | gap rows in chunk range have `fetch_status = FAILED_RETRYABLE`, `attempt_count++` |

Both states are written in one transaction with the bar inserts for
the chunk. The daemon and refetch never produce a state where the
two disagree.

### One adjustment function

> **Superseded by slice 152.** The adjusted-on-write model described in
> this section and in "Band-based adjustment writes" below is replaced by
> adjusted-on-read. `adj_*` columns, `k_factor`, `adjusted_at`, and
> `last_adjusted_ca_snapshot_id` are dropped from the schema. The
> `compute_k_factor` math is preserved and used by the new `adjusted()`
> function in `src/manta_trading/data/adjustment.py`. See
> [Adjusted-on-read (slice 152)](#adjusted-on-read-slice-152) below.

```
compute_k_factor(symbol, target_date, ca_snapshot) -> Decimal
```

Single source of truth for adjustment math. Used by:
- ingest (writing `adj_*` columns)
- refetch (re-adjusting newly-fetched bars)
- daemon CA-detection recompute (set-based SQL UPDATEs per ex-date band)
- audit Stage A (verifying `abs(stored adj_close - close * stored_k_factor) < EPSILON`)
- audit Stage B (comparing stored k_factor vs vendor's published k)

Deterministic for fixed inputs. Implemented in
`src/manta_trading/data/adjustment/k_factor.py` (existing module from
slice 127). The arch's role here is to spec the math precisely so
every caller agrees on what "k_factor" means.

#### Adjustment model

The k_factor is a **backward-adjustment multiplier** that converts
raw close to EODHD-style `adjusted_close`:

```
adjusted_close = raw_close × k_factor(symbol, target_date)
```

By construction, the most recent close has `k_factor = 1.0` exactly,
and prices on dates earlier than corporate actions are scaled down
to reflect those actions. This matches EODHD's `adjusted_close`
model exactly — that's a hard requirement, not a coincidence. Stage
B audit (`stored_k_factor` vs `vendor's adjusted_close / close`)
only produces meaningful comparisons if the models match.

The factor is the product of contributions from every corporate
action whose `ex_date` is **strictly after** `target_date`:

- **Split** with ratio `ratio_to / ratio_from` (e.g. 4-for-1 has
  ratio_to=4, ratio_from=1) contributes `ratio_from / ratio_to`
  (= 1/4 in this example). A symbol with a 2:1 split tomorrow has
  yesterday's k = 1/2.
- **Cash dividend** of `amount` paid on `ex_date` contributes
  `(prev_close - amount) / prev_close`, where `prev_close` is the
  close on the most recent trading day **strictly before** `ex_date`.

Both contributions are commutative under multiplication; iteration
order doesn't matter. Return value is `Decimal('1')` when no
corporate actions exist after `target_date`.

Decimal arithmetic throughout — no float. `Decimal` is preserved
end-to-end from CA storage (`splits.ratio_to` etc. are NUMERIC) to
the multiplier returned here.

#### `ca_snapshot` shape

```
ca_snapshot = {
  symbol: str,
  splits:    list[{ ex_date, ratio_to, ratio_from, fetched_at }],
  dividends: list[{ ex_date, amount, fetched_at }],
  prev_closes: dict[date, Decimal],   -- close on the most recent
                                      -- trading day before each
                                      -- dividend's ex_date
  snapshot_id: str,    -- stable SHA256 hex of canonicalized snapshot
}
```

`prev_closes` must be populated by the caller for every dividend
ex_date in `dividends`. `compute_k_factor` raises `KeyError` if a
required prev_close is missing. (This matches the existing
`k_factor.py` implementation.) The `current_ca_snapshot(symbol)`
helper assembles all four pieces by querying `splits`, `dividends`,
and (for prev_closes) the daily bar table.

In normal operation, `ca_snapshot` is the **current** snapshot —
all rows in the `splits` and `dividends` tables for the symbol with
their current `fetched_at` values, plus prev_closes derived from
current daily bars. Loaded once per ingest pass for the symbol,
used for every bar in that pass. The `snapshot_id` is recorded on
`acquisition_state.last_adjusted_ca_snapshot_id` so the daemon can
detect when adjustments need to be re-run.

The argument exists in the function signature for **deterministic
replay**: given a historical snapshot (e.g., reconstructed from
audit logs of CA arrivals), recompute what k_factor *would have
been* at ingest time. Used for debugging "why did this row get
k=X." Not exercised in normal operation.

#### `snapshot_id` computation (stable, cross-process)

`snapshot_id` MUST be deterministic across processes and Python
restarts. Python's built-in `hash()` is randomized per process and
MUST NOT be used.

Algorithm:

```
def compute_snapshot_id(splits, dividends):
    splits_canon = sorted(
        [(s.ex_date.isoformat(),
          str(s.ratio_to),         # Decimal serialized as string
          str(s.ratio_from)) for s in splits]
    )
    dividends_canon = sorted(
        [(d.ex_date.isoformat(),
          str(d.amount)) for d in dividends]
    )
    payload = json.dumps(
        {"splits": splits_canon, "dividends": dividends_canon},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

Stable across runs because: SHA256 is deterministic; `Decimal` and
`datetime` are serialized as strings, not floats or epoch ints;
sort orders are explicit; JSON output is canonical.

Note: `fetched_at` is intentionally excluded from the canonical tuple.
The ingest path (`upsert_splits` / `upsert_dividends`) sets
`fetched_at = NOW()` on every `ON CONFLICT DO UPDATE`, including no-op
upserts where the ratio or amount did not change. Including `fetched_at`
would cause `snapshot_id` to change on every CA ingest cycle regardless
of whether actual corporate-action data changed, triggering spurious
band-based recomputes in the daemon. The CA identity key is
`(ex_date, ratio_to, ratio_from)` for splits and `(ex_date, amount)`
for dividends; the DB enforces uniqueness on `(symbol, ex_date)` for
both tables, making these tuples sufficient.

#### Band-based adjustment writes

> **Superseded by slice 152.** See [Adjusted-on-read (slice 152)](#adjusted-on-read-slice-152).

Both **ingest** and **CA-detection recompute** write `adj_*` columns
the same way: a small number of `compute_k_factor` calls (one per
ex-date band) plus a small number of SQL UPDATEs. Never per-bar
Python.

Algorithm (used by ingest after a chunk write, and by daemon
CA-detection when the snapshot_id mismatches):

1. Determine the time range covered by this write. For ingest, that
   is the chunk's `[from_ts, to_ts]`. For CA-detection recompute,
   that is `[min(changed_ca.ex_date), now()]` covering all stored
   bars potentially affected by the changed CAs.
2. From `ca_snapshot`, identify the ex-dates falling within the
   range plus the immediate ex-date before the range start (anchors
   the leading band). Between consecutive ex-dates, `k_factor` is
   constant.
3. For each ex-date band `[band_start, band_end)`, compute
   `k_factor_band = compute_k_factor(symbol, band_start - 1 day, ca_snapshot)`
   **once**. (`band_start - 1 day` because `compute_k_factor` looks
   for CAs strictly *after* the target date; bars on `band_start`
   itself are after that band's anchor ex-date.)
4. Issue one UPDATE per band against the appropriate data table
   (`minute_ohlcv` or `daily_ohlcv`):
   ```
   UPDATE <data_table>
   SET k_factor   = :k,
       adj_open   = open  * :k,
       adj_high   = high  * :k,
       adj_low    = low   * :k,
       adj_close  = close * :k,
       adjusted_at = now()
   WHERE symbol = :sym
     AND time >= :band_start
     AND time <  :band_end
   ```
5. After all bands, set `acquisition_state.last_adjusted_ca_snapshot_id
   = current_snapshot_id`.

Number of UPDATEs per write = number of ex-date bands intersecting
the range (typically ≤ a few for an ingest chunk, < 100 for a
multi-year CA-recompute). Rows affected per UPDATE varies; SQL
handles bulk efficiently. There is no per-bar Python loop in either
hot path.

For an ingest chunk that contains zero ex-dates (common case — most
chunks don't straddle a CA), there is exactly one UPDATE.

This is a daemon behavior. Slice 145 reopens 120's daemon code to
add it.

### Operator commands

The operator interacts with the data layer through five command
groups: the long-running daemon (which drives all unattended fetch
work), corporate-actions management, status, refetch, and audit.

#### `mt data daemon run`

```
mt data daemon run [--minute] [--daily] [--symbols X,Y,Z]
                   [--list NAME] [--max-credits N]
                   [--stop-when-done | --forever]
```

The single long-running process that drives all unattended fetch
work. Replaces the slice-145 one-shot `daemon daily` / `daemon
minute` cycles.

**Termination defaults — scoped invocations exit, bare invocations
run forever:**

| Invocation | Default behavior |
|---|---|
| `mt data daemon run --symbols SPY` | runs until SPY is fully backfilled, then exits |
| `mt data daemon run --list priority1` | runs until priority1 list is fully backfilled, then exits |
| `mt data daemon run` | runs forever (the long-running daemon) |
| `mt data daemon run --max-credits N` | runs until budget exhausted, then exits |

`--forever` and `--stop-when-done` are explicit overrides.

**Behavior:**
- Continuous loop. After one cycle finishes, immediately starts the
  next.
- Token-bucket throttling against `EODHD_PER_MINUTE_BURST` (1000
  credits/min, short-window ceiling) and `EODHD_DAILY_QUOTA` (100k
  credits/day rolling).
- Quota accounting per call type: `EODHD_INTRADAY_CALL_COST = 5`,
  `EODHD_EOD_CALL_COST = 1`, `EODHD_BULK_EOD_BASE_COST = 100`.
- Graceful shutdown on SIGTERM: finishes the current symbol, exits
  cleanly.
- Per-cycle progress logging: symbols processed, credits spent
  today, estimated completion.
- Single-symbol fast path: `mt data daemon run --symbols SPY`
  finishes a 22-year SPY backfill in ~90s of API time (~335
  credits) and exits.

#### Named symbol lists

The operator can define **named lists of symbols** that the daemon
(and `mt data ca update`) can be pointed at via `--list NAME`. Lists
are general-purpose: priority backfill ordering, sector slices,
watchlists, custom test sets — all the same mechanism.

**List config** at `config/symbol-lists.yaml` (or a `symbol_lists`
DB table):

```yaml
lists:
  priority1:
    description: "Hot test set — backfill before anything else"
    symbols: [SPY, QQQ, AAPL, MSFT, NVDA, GOOGL, META, TSLA, AMZN, BRK-B]
  priority2:
    description: "S&P 500 snapshot frozen at refresh time"
    source: file:config/lists/sp500-snapshot.txt
  tech-megacap:
    description: "Top tech names for sector-rotation experiments"
    symbols: [AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA]
```

**List management:**

- `mt data lists ls` — show defined lists with member counts
- `mt data lists show NAME` — print the resolved symbol list
- `mt data lists refresh-sp500` — refresh `config/lists/sp500-snapshot.txt`
  from EODHD's `/fundamentals/GSPC.INDX` `Components` array (10 credits)

Lists are **operator state**, not instrument state — there is no
`priority_tier` column on `instruments`. The daemon resolves
`--list NAME` to a symbol set and filters its iteration accordingly.

#### `mt data ca` — corporate-actions management

```
mt data ca update [--since DAYS_OR_DATE] [--symbol SYMBOL | --list NAME]
mt data ca show --symbol SYMBOL [--from DATE] [--to DATE]
mt data ca list [--from DATE] [--to DATE]
```

Splits and dividends from EODHD. The default action (`mt data ca
update` with no flags) is **bulk-fetch yesterday's splits +
dividends across the entire exchange** — the daily steady-state
call, 200 credits total, full universe coverage.

`ca update` shapes:

| Invocation | What it does | Cost |
|---|---|---|
| `mt data ca update` | Bulk-fetch yesterday's splits + dividends (full exchange) | 200 credits |
| `mt data ca update --since 7` | Bulk-fetch trailing 7 days, per-day | 1400 credits |
| `mt data ca update --since 2026-04-25` | Bulk-fetch from date through yesterday | 200 × N days |
| `mt data ca update --symbol AAPL` | Per-symbol full CA history backfill | 2 credits |
| `mt data ca update --symbol AAPL --since 2024-01-01` | Per-symbol with client-side post-filter | 2 credits |
| `mt data ca update --list priority2` | Per-symbol full backfill across each list member | 2 × N symbols |

**`--symbol` and `--list` are mutually exclusive.** Either modifies
the path from "bulk-by-day" to "per-symbol-by-symbol." `--since`
modifies whichever path is in effect (bulk: per-day range;
per-symbol: client-side post-filter).

**No `--type` flag** — splits and dividends always travel together
(both 100 credits, both immutable, both should be current).

**No `--date YYYY-MM-DD`** for one specific historical date — no
operator workflow needs that; `--since` and per-symbol scopes cover
the real cases.

**Replaces legacy `mt data adjustment ingest`** — the slice-127
per-symbol command becomes `ca update --symbol X [--since DATE]`.
The `adjustment` Typer sub-app has only this one command and is
deleted in slice 146.

**Verified pricing (EODHD docs, 2026-05-03):**
- `/eod-bulk-last-day/US?type=splits` — 100 credits flat, full
  exchange. Symbols filter not supported for splits/dividends.
- `/eod-bulk-last-day/US?type=dividends` — same.
- `/splits/{ticker}` — 1 credit per call. `/div/{ticker}` — 1 credit.

`mt data ca update` (no flags) typically runs once per UTC day —
either via cron/systemd timer, or as an inline once-per-day guarded
action inside the long-running daemon's main loop. Either is fine;
implementation chooses the one with lower operational surface.

When new CAs land in `splits`/`dividends`, the daemon's
CA-drift detection (next cycle) recomputes affected `adj_*` ranges
via band-based UPDATE.

#### `mt data status [--symbol X]`

Reads `data_status` view. Default scope: all symbols in the
instrument registry. With `--symbol`, prints one row of detail plus
the full `data_gaps` listing for that symbol. Rich table by default;
`--json` for machine consumption.

#### `mt data refetch --symbol X --from D1 --to D2`

Fetches the requested window from the configured provider in
provider-sized chunks. Per-chunk processing is specified by
`update_data_gaps` below; refetch passes `force_reset_terminal=True`
so any `PROVIDER_HOLE` or `RETRY_EXHAUSTED` rows in scope are reset
to `UNKNOWN, attempt_count=0` before re-attempting (escape valve
for terminal-state rows). After all chunks process, runs
`coalesce_data_gaps(symbol, granularity)` to merge contiguous gap
rows.

Re-adjustment of stored bars on demand is **not** an operator
command — the daemon's CA-detection mechanism (next cycle) handles
it automatically when corporate actions change.

#### `mt data audit (--symbol X | --symbols-from PATH | --all) [--from D1] [--to D2] [--tolerance EPSILON]`

  Runs Stage A and Stage B over the window:

  - **Stage A** — for each session in window, asserts
    `abs(stored adj_close - close * stored_k_factor) < tolerance`.
    Uses stored k_factor; verifies internal consistency.
  - **Stage B** — for each session in window, fetches vendor's
    `adjusted_close` for that session via the daily endpoint, computes
    `published_k = adjusted_close / close`, asserts
    `abs(stored_k_factor - published_k) < tolerance`. **One vendor
    call per symbol** (a single range-fetch over the audit window
    against the vendor's daily endpoint), not one call per session.
    For `--all` against the active US-equity registry (~13k symbols),
    this is ~13k calls — within EODHD's 100k/day quota with
    headroom but still significant. The interactive confirmation
    prompt (below) reports the symbol count and call estimate before
    proceeding.

  Trading-calendar-aware: non-trading days are SKIP, never FAIL.
  Output: one Rich table, both stages side-by-side per day.

  Scope rules:
  - `--symbol X` — single symbol, runs immediately.
  - `--symbols-from PATH` — one symbol per line, runs immediately.
  - `--all` — full instrument registry. Counts symbols, prints
    "Will audit N symbols, ~N API calls. Continue? [y/N]". Proceeds
    on y. This prevents accidental quota exhaustion.
  - No scope flag — error.

  `--tolerance` overrides the default `ADJUSTMENT_DRIFT_EPSILON`
  per-run.

### Gap function (the core invariant)

The gap state in `data_gaps` is maintained by two pure functions plus
one transactional writer. All three are specified concretely; nothing
hand-waved.

#### `compute_missing_ranges(symbol, granularity, from_ts, to_ts) -> list[GapRange]`

Pure function. Reads `trading_calendar`, `instruments`, and the data
table (`daily_ohlcv` or `minute_ohlcv`). Returns missing session ranges
within `[from_ts, to_ts]`, normalized as `[session_open_utc,
session_close_utc]` per session.

Algorithm:

1. From `instruments`, get the symbol's effective lifecycle dates:
   `effective_start = COALESCE(first_listing_date, first_data_date)`
   and `effective_end = delisted_date` (NULL for active symbols).
   If `from_ts < effective_start`, raise `from_ts` to
   `effective_start`. If `effective_end IS NOT NULL AND
   to_ts > effective_end`, lower `to_ts` to `effective_end`. Return
   empty if range is empty after clamping. (Symbols with both
   `first_listing_date` and `first_data_date` NULL — neither Finnhub
   nor backfill has populated them — cannot have ranges computed
   and return empty; slice 145's status surfaces them as STALE.)
2. From `trading_calendar`, get all sessions in the (clamped) range
   for the symbol's exchange as an **ordered list** of trading dates
   `[T_1, T_2, ..., T_n]`.
3. From the data table, get the set of `date(time)` for stored bars
   in the (clamped) range: `stored_dates`.
4. Walk the ordered list. For each `T_i`, compute
   `is_missing = T_i NOT IN stored_dates`. Start a new range when
   `is_missing` becomes true; close the range when it becomes false
   (or the list ends). The result is a list of contiguous runs of
   missing trading sessions.
5. Friday-missing + weekend + Monday-missing produces a single range
   from Friday to Monday because Friday and Monday are adjacent in
   the ordered trading-session list (the weekend is not in the list
   to begin with).
6. For each range, return `(symbol, granularity,
   session_open_utc(T_first), session_close_utc(T_last))`.

`HOLIDAY`, `PRE_LISTING`, `DELISTED` are excluded by construction —
step 1 handles `PRE_LISTING`/`DELISTED`; step 2 only returns trading
sessions, so `HOLIDAY` cannot appear in the ordered list.

#### `update_data_gaps(symbol, granularity, from_ts, to_ts, fetch_status_for_unfilled)` — transactional writer

Runs in a single transaction. Acquires PostgreSQL advisory lock on
`(symbol, granularity)` for the duration. Concurrent callers serialize
on the same scope; disjoint scopes proceed in parallel.

Function signature:

```
update_data_gaps(symbol, granularity, from_ts, to_ts,
                 fetch_status_for_unfilled,
                 force_reset_terminal=False)
```

`force_reset_terminal` is set by `mt data refetch` (operator-driven)
to clear `PROVIDER_HOLE` and `RETRY_EXHAUSTED` rows in scope before
re-attempting. The daemon never sets this flag.

Algorithm:

1. **Snapshot prior state**. `prior_rows = SELECT gap_start, gap_end,
   fetch_status, attempt_count FROM data_gaps WHERE symbol = X AND
   granularity = G AND gap_start <= to_ts AND gap_end >= from_ts`.
   Read into memory as a list. This survives the delete in step 2.
2. **Force-reset terminal rows (if flagged)**. If
   `force_reset_terminal=True`, set every prior row whose
   `fetch_status IN ('PROVIDER_HOLE', 'RETRY_EXHAUSTED')` to logical
   state `attempt_count = 0, fetch_status = UNKNOWN` in the
   in-memory snapshot before step 4 consults it. Carry-forward at
   step 4 then sees `UNKNOWN` rather than the terminal status,
   preserving the operator's intent to re-attempt.
3. **Delete intersecting rows**. `DELETE FROM data_gaps WHERE symbol
   = X AND granularity = G AND gap_start <= to_ts AND gap_end >=
   from_ts`. Naturally handles partial-fill splitting: a 30-day gap
   whose middle 10 days got filled has its original row deleted, and
   step 5 inserts the head and tail as two new rows.
4. **Compute new ranges**. `new_gaps = compute_missing_ranges(symbol,
   granularity, from_ts, to_ts)`.
5. **Insert with carried-forward attempt_count**. For each `new_gap`:
   - Find any prior row (from the post-reset snapshot) whose
     `[gap_start, gap_end]` overlaps `new_gap`. If the matched
     prior's `fetch_status` matches `fetch_status_for_unfilled`,
     take the maximum `attempt_count` among matching prior rows;
     otherwise treat as a first attempt.
   - INSERT new row with `last_attempt_ts = now()` and
     `attempt_count = max_prior_count + 1` if a matching prior row
     existed, else `1` (this is the first attempt).
6. **Promote to RETRY_EXHAUSTED**. After insert, for any newly-
   inserted row with `fetch_status = FAILED_RETRYABLE` and
   `attempt_count >= MAX_RETRY_COUNT`, update `fetch_status` to
   `RETRY_EXHAUSTED`. The row stays; the operator decides whether to
   retry it via `mt data refetch` (which calls back in with
   `force_reset_terminal=True`).
7. **Update acquisition_state**. Set `last_attempt_ts = now()` and
   `last_attempt_outcome` to the caller's outcome. (No retry_count;
   per-gap counts live in `data_gaps`.)

`fetch_status_for_unfilled` is determined by the caller per the
`last_attempt_outcome` mapping table:

| `last_attempt_outcome` | `fetch_status_for_unfilled` |
|---|---|
| `success` | (no unfilled rows; range is covered) |
| `partial` | `UNKNOWN` (caller will retry the unfilled portion) |
| `empty` | `PROVIDER_HOLE` |
| `transient_failure` | `FAILED_RETRYABLE` |

A prior `PROVIDER_HOLE` row whose range is overlapped by a new fetch
returning bars is *replaced* by `success` — the bars now exist. We do
not preserve `PROVIDER_HOLE` against evidence to the contrary. A prior
`PROVIDER_HOLE` whose range still has no bars after the new fetch
keeps `PROVIDER_HOLE` (attempt_count carries forward unchanged — we
don't reattempt confirmed holes).

#### `coalesce_data_gaps(symbol, granularity)` — post-refetch cleanup

Holds the advisory lock on `(symbol, granularity)` for its duration.

Single-pass, sorted-list, accumulator algorithm. **O(n)** in the
number of gap rows.

Two `data_gaps` rows A (preceding) and B are **adjacent** iff:
- `A.fetch_status == B.fetch_status` (mixed-status rows never merge —
  `PROVIDER_HOLE` next to `UNKNOWN` are semantically different).
- `next_trading_session_after(A.gap_end) == B.gap_start` —
  i.e. B's first session is exactly the next trading session after
  A's last. This is a single function call, not an iteration over
  candidate sessions.

`compute_missing_ranges` produces non-overlapping, calendar-correct
ranges by construction (it walks the ordered trading-session list
and groups consecutive missing ones). After every
`update_data_gaps` write, ranges within a scope are correct. The
only reason adjacent same-status rows can exist is that
`update_data_gaps` operates on bounded sub-ranges (chunks); rows
from successive chunks may abut. `coalesce` merges them.

Algorithm:

1. `rows = SELECT * FROM data_gaps WHERE symbol=:s AND
   granularity=:g ORDER BY gap_start`. Single query.
2. Walk `rows` once with an accumulator `current`:
   - Initialize `current = rows[0]`.
   - For each subsequent `r in rows[1:]`:
     - If `current` is adjacent to `r` (per definition above), set
       `current.gap_end = r.gap_end`, carry forward
       `last_attempt_ts = MIN(current, r)` and
       `attempt_count = MAX(current, r)`.
     - Else, emit `current` to the output list, set `current = r`.
   - After the loop, emit `current`.
3. If the output list is unchanged from `rows`, no writes — early
   return.
4. Otherwise: `DELETE` all rows for the scope, `INSERT` the merged
   set in one statement. Single transaction.

Idempotent: re-running on already-coalesced state walks the list,
finds no merges, and exits without writes. O(n) per call.

#### Concurrency

`update_data_gaps` and `coalesce_data_gaps` both acquire a PostgreSQL
advisory lock keyed on `(symbol, granularity)` for the duration of
their work. Daemon, refetch, and backtest are all callers; the lock
serializes them on the same scope. Disjoint scopes proceed in
parallel.

**Daemon lock discipline (deadlock-free by construction):** The
daemon holds **at most one (symbol, granularity) lock at any time**.
Its main loop is:

```
for (symbol, granularity) in scheduled_work:
    acquire_lock(symbol, granularity)
    try:
        do_work(symbol, granularity)   # update_data_gaps and friends
    finally:
        release_lock(symbol, granularity)
```

The daemon never holds two locks simultaneously, so it cannot block
on its own lock acquisition. This is a design constraint, not an
emergent property — slice 145's daemon implementation must enforce
it.

**Backtest lock discipline:** A backtest may need locks on many
`(symbol, granularity)` scopes simultaneously. It acquires them in
a deterministic sorted order (by `(symbol, granularity)`) so that
two concurrent backtests cannot deadlock each other. Combined with
the daemon's one-lock-at-a-time rule, no daemon-vs-backtest deadlock
is possible: at any instant the daemon holds at most one lock, and
a backtest waiting on that lock will eventually proceed when the
daemon releases.

Backtests trigger an `update_data_gaps` recompute (within their
held lock) if `data_gaps` is older than `MAX_GAP_STALENESS` for the
scope, then read `data_gaps` and release. See Backtest contract
below.

In single-operator practice, contention is rare — daemon processes
symbols one at a time, refetch operates on the operator's named
symbol, backtests operate on a declared scope. The lock is cheap
when uncontended and correct when contended.

### Backtest contract

A backtest declares a window and a symbol set, then verifies its data
scope before reading bars.

Three policies, default **strict**:

- `strict` — any gap row in scope causes the backtest to halt with
  an error listing the missing ranges.
- `skip-and-mark` — proceed without the missing bars; result includes
  metadata enumerating skipped ranges.
- `forbid-symbol` — drop symbols with any gap in window from the
  candidate universe before running.

The backtest uses `read_data_gaps_consistent_for(symbols, window)`,
specified as:

1. Acquire advisory lock on `(symbol, granularity)` for each symbol
   in scope (acquired in sorted order to prevent deadlock).
2. For each symbol, check `acquisition_state.last_attempt_ts`
   against the symbol's window end. If the most recent gap-state
   write is older than `MAX_GAP_STALENESS` (default 5 min), call
   `update_data_gaps(symbol, granularity, window_start, window_end,
   UNKNOWN)` to refresh — the writer reads current bars and
   recomputes gap rows under the same lock.
3. Read all `data_gaps` rows for `(symbol, granularity)` whose
   `[gap_start, gap_end]` intersects `window`.
4. Release locks.
5. Return the gap rows as a structured result.

After reading, the backtest applies its policy:
- `strict` — any gap row in scope causes immediate halt with an
  error listing the missing ranges.
- `skip-and-mark` — proceed without the missing bars; result
  includes metadata enumerating skipped ranges.
- `forbid-symbol` — drop symbols with any gap in window from the
  candidate universe before running.

The shared `data_gaps` table is the source of truth for both daemon
and backtest. There is no separate ephemeral gap representation.
The advisory lock ensures the daemon and backtest never observe a
partially-written gap state. Concurrent backtests over disjoint
scopes proceed in parallel.

The daemon also recomputes after every ingest, but the backtest does
not trust that — its own staleness check and conditional recompute
is part of the contract.

## Backfill behavior

### Daily

Two paths:

**Backfill path** (cold start or `mt data refetch`): one call per
symbol via `/eod` with `output_size=full`. Provider returns full
history. Store everything. Populate
`instruments.first_data_date = MIN(date)` of returned bars (one-time
on first successful backfill). For symbols on the EODHD delisted list
(`instruments.delisted_at_eodhd = true`), populate
`instruments.delisted_date = MAX(date)` of returned bars. Call
`update_data_gaps(symbol, 'daily', first_data_date, target_end,
fetch_status_for_unfilled)` per the outcome mapping. If any sessions
in the range are missing in the store, they become gap rows.

**Steady-state path** (daily daemon cycles after initial backfill is
done): the *target* steady-state is a single
`/eod-bulk-last-day/US` call per cycle (one 100-credit call covers
the whole exchange in a single response, instead of ~13,000
individual `/eod` calls). The bulk response is iterated to write
yesterday's bar (or the most recent completed session's bar) for
every symbol present. Per-symbol `update_data_gaps` runs over the
cycle's range only.

The bulk endpoint returns rows for symbols not in our `instruments`
table; these are silently dropped. It does not return rows for
delisted-after-the-cycle symbols; those naturally accumulate gaps in
`data_gaps` until either the next universe rebuild marks them
`delisted_at_eodhd` or operator manual intervention.

**Implementation note (2026-05-03):** The bulk-EOD steady-state path
was originally bundled into slice 146 but was split out to slice 152
because its mode-selection edge cases (newly-added symbols mid-day,
mixed-mode cycles when only some scope members are caught up,
bulk-response routing into the per-symbol band-write path) deserve
their own design pass. Slice 146 ships the long-running daemon, named
lists, `mt data ca`, and CA-drift recompute on top of slice 145's
per-symbol `/eod` daily path. At ~13k symbols × 1 credit/call =
~13k credits/day, per-symbol steady-state is comfortably under the
100k/day quota — the bulk switch is a quota optimization, not a
correctness requirement. Slice 152 lands the bulk-EOD switch and at
that point per-symbol `/eod` becomes used **only** for backfill and
refetch, matching the target steady-state described above.

### Minute

Most-recent-chunk-first loop, driven by `data_gaps`:

1. Compute target window: `[max(first_trade_date, today - history_months), most_recent_completed_session_close_utc]`.
2. Initial recompute: `update_data_gaps(symbol, 'minute', target_start, target_end, UNKNOWN)`.
   This writes one row per missing session-range in the window.
3. Loop: pick the most recent **actionable** gap (see below),
   determine the provider-sized chunk covering it, fetch.
4. Process per chunk: store bars, call `update_data_gaps` for the
   chunk's range with the appropriate `fetch_status` for the outcome.
5. Continue until no actionable gaps remain.
6. After loop: `coalesce_data_gaps`.

**Actionable gap** = `fetch_status IN ('UNKNOWN', 'FAILED_RETRYABLE')`.
- `UNKNOWN` means we haven't tried this range yet.
- `FAILED_RETRYABLE` means we tried, hit a transient error, and have
  not yet exhausted retries (`attempt_count < MAX_RETRY_COUNT`).
  `update_data_gaps` automatically promotes a row to
  `RETRY_EXHAUSTED` when its retry count hits the cap.
- `PROVIDER_HOLE` is **not** actionable — vendor confirmed empty.
  The daemon does not retry these. (Operator can manually trigger
  retry via `mt data refetch`, which resets the rows to `UNKNOWN`.)
- `RETRY_EXHAUSTED` is **not** actionable — too many transient
  failures. Operator decides whether to manually `mt data refetch`
  (which resets to `UNKNOWN, attempt_count = 0`) or accept the gap.

Provider-sized chunk: `provider_max_chunk_days` for the configured
provider (e.g. 120 for EODHD minute). The fetch range is
`[max(gap_start, gap_end - provider_max_chunk_days),
gap_end]` so we always fetch the most recent chunk-sized slice of
the gap first.

Steady state: `data_gaps` contains only `PROVIDER_HOLE` and
`RETRY_EXHAUSTED` rows (terminal states). Daemon checks once per
cycle whether the most recent expected session is missing and
fetches it. Manual retry via `mt data refetch` is the escape valve
for terminal-state rows.

This is a new daemon behavior introduced by this initiative. Slice 145
reopens 120's daemon code to add it.

## Universe at time T

Derived from `instruments` table lifecycle. No new table.

`instruments` carries three date columns relevant to point-in-time
queries:

- `first_listing_date` — IPO date when known (Finnhub-sourced).
  Authoritative when populated; NULL when Finnhub didn't return one.
- `first_data_date` — earliest date for which we have a stored
  daily bar from EODHD. Practical lower bound on what we can serve
  for a backtest.
- `delisted_date` — last date for which we have a stored daily bar
  for symbols where `delisted_at_eodhd = true`. Practical upper
  bound for delisted symbols. NULL for active symbols.

```
symbols_active_on(date D) =
  SELECT symbol FROM instruments
  WHERE COALESCE(first_listing_date, first_data_date) <= D
    AND (delisted_date IS NULL OR delisted_date > D)
```

`COALESCE(first_listing_date, first_data_date)` falls back to the
data-derived earliest date when Finnhub didn't supply an IPO date.
This is honest about what we know: the IPO date if available, else
"the earliest date we have data for."

Survivorship-bias-free by construction. Backtests filter their
candidate universe through this query before requesting bars.

Index-membership-at-time-T (e.g. "what was in SP500 on 2024-03-15") is
out of scope until needed — that requires vendor index-constituents
data and a separate slice. When it lands, `mt data audit --group SP500`
becomes natural.

## Constants

Defined once, in `manta_trading.constants` or equivalent. Referenced
by every module that needs them.

```
ADJUSTMENT_DRIFT_EPSILON = 1e-6   -- absolute, in price units
                                  -- used by Stage A and Stage B audit

MAX_RETRY_COUNT          = 5      -- transient_failure retries before
                                  -- fetch_status promotes to RETRY_EXHAUSTED

DAILY_STALENESS_THRESHOLD   = 2 days   -- after which health = STALE
MINUTE_STALENESS_THRESHOLD  = 1 day

DAILY_HISTORY_MONTHS  = unbounded  -- full provider history

-- Minute history has no month-count constant. Effective floor =
-- max(EODHD_INTRADAY_HORIZON, MT_MINUTE_HISTORY_START, per-symbol
-- first_listing_date/first_data_date) — full history to 2004 by default,
-- narrowable per-deployment via the MT_MINUTE_HISTORY_START env var
-- (slice 162 §History window).

LATE_BAR_GRACE_PERIOD = 30 minutes  -- absorbs vendors that publish
                                    -- last bars of a session shortly
                                    -- after close; affects target_end
                                    -- of `data_status` view

MAX_GAP_STALENESS     = 5 minutes   -- backtests trigger an
                                    -- update_data_gaps recompute when
                                    -- prior gap-state write is older
                                    -- than this

EODHD_DAILY_QUOTA           = 100_000  -- credits/day rolling cap
EODHD_PER_MINUTE_BURST      = 1_000    -- credits/min short-window ceiling
EODHD_INTRADAY_CALL_COST    = 5        -- credits per /intraday call
EODHD_EOD_CALL_COST         = 1        -- credits per /eod call
EODHD_BULK_EOD_BASE_COST    = 100      -- credits per /eod-bulk-last-day call
                                       -- (full exchange; +1/symbol if symbols
                                       -- filter used; symbols filter NOT
                                       -- supported for splits/dividends)
```

CLI overrides where applicable (`--tolerance`, etc.) are explicit.

## Migration from current state

Current state has:
- 61.8M minute_ohlcv rows for 660 symbols (uneven, partly AV-era)
- 13k daily symbols (varying coverage)
- Old `acquisition_state` with `last_success_ts` and provider tags
  that conflated ingest-source with responsibility-going-forward
- `coverage_gaps` table from slice 128 (orphan after this redesign)
- `instruments` table populated from AV's universe (~8k active
  symbols, no delisted, no listing dates, no type metadata beyond
  `equity`)

Migration is **rebuild universe, then wipe and refetch**, with
explicit pre-flight verification and progress-tracking. We are not
preserving AV-era data because the AV pipeline had its own
correctness issues (slice 127 known bugs, mistagged provider on
minute rows). Honest re-derivation from EODHD is preferred to
inherited noise.

### Sequencing

The migration runs as **two distinct operator-invoked steps**, with
the second strict-blocked on the first completing successfully.

#### Step 1 — Universe rebuild (slice 141)

`mt data instruments rebuild [--dry-run] [--skip-finnhub]`. Adds
schema columns (`first_listing_date`, `first_data_date`,
`delisted_date`, `eodhd_type`, `eodhd_exchange`, `delisted_at_eodhd`),
drops `active` boolean (now derived). Fetches EODHD bulk lists,
filters by Type to `('Common Stock', 'ETF', 'Preferred Stock',
'INDEX')`, upserts into `instruments`. The `eodhd_exchange` column
records the raw EODHD provider field (`'US'` or `'INDX'`) — kept
distinct from `venue` (the authoritative trading venue: NASDAQ,
NYSE, NYSE_ARCA, BATS, NYSE_MKT, INDX). Optionally enriches
`first_listing_date` and authoritative `venue` from Finnhub
`/stock/profile2` (60/min rate limit, ~17 hours for the v1 universe;
resumable; skipped under `--skip-finnhub`).

After upsert, rows in the prior AV-seeded baseline that EODHD does
not know about are deleted (the orchestrator reports the count and
sample first; 5-second confirmation gate). This is the only
destructive action on `instruments` in this step. Slice 141's D10
covers the rationale.

`first_data_date` and `delisted_date` are left NULL by this step;
they are populated by the daily backfill (slice 145) as a side
effect of fetching history.

Bar tables and acquisition state are untouched. Re-running with no
upstream changes is a no-op.

#### Step 2 — Schema migration + TRUNCATE (slice 142)

Pre-flight checks before TRUNCATE:

1. Verify slice 141 has run successfully: `instruments.eodhd_type`
   column populated for all rows; row count is in the expected
   ~50,000–60,000 range (not zero, not the AV-era ~8k).
2. Verify EODHD daily access works for a sample of symbols (small
   probe; fetch, store, gap recompute, status). Halt on failure.

Wipe (single transaction):

1. New schema lands (`data_gaps`, slimmed `acquisition_state`,
   `data_status` view, constants migration).
2. `TRUNCATE minute_ohlcv, daily_ohlcv, acquisition_state,
   coverage_gaps`.

### Refetch

Daemon cold-starts in stages (slice 145):

1. **Daily backfill**: per-symbol `/eod?output_size=full` for every
   symbol in `instruments`. With ~57k symbols (Common Stock + ETF +
   Preferred Stock + USA-relevant INDEX) at EODHD's per-minute rate
   limit of **1000 calls/minute** and 100k/day quota, this completes
   in approximately **57 minutes of wall-clock time** within a
   single quota-day. Populates `first_data_date` and (where
   applicable) `delisted_date` as a side effect.
2. **Minute backfill**: chunked per the loop above. With 660
   currently-tracked minute symbols and ~120-day chunks over 24
   months of history (≈ 730 days / 120 days/chunk = ~6 chunks per
   symbol), this is approximately **3,960 calls** (~4 minutes at
   1000/min). Well within EODHD's 100k/day quota. Configurable; a
   wider universe or deeper history can run over multiple quota
   days.

After backfill, the daily steady-state path *will* use
`/eod-bulk-last-day/US` (single 100-credit call per cycle) — an
order-of-magnitude saving on quota. This is delivered by slice 152;
slices 145 and 146 ship per-symbol `/eod` for the steady-state path
(~13k credits/day, well under quota).

### Known provider-data limitations (EODHD)

These are not migration steps; they are data realities the system
must surface honestly via `data_gaps`:

- **Daily for delisted symbols**: available across the historical
  archive (verified back to 1999 via probe of `AAAB`, delisted 2003).
- **Intraday (minute) for delisted symbols**: only available for
  symbols delisted after 2021. Pre-2021 delisted symbols will return
  empty for minute fetches; their `data_gaps` rows become
  `PROVIDER_HOLE`. This is a known constraint, not a defect.
- **Symbol-specific historical holes**: occasional, per-symbol gaps
  exist (e.g. TSLA Q1 2024, NVDA post-2024-split window). These
  manifest as `PROVIDER_HOLE` in `data_gaps` after a fetch attempt
  returns empty for an expected range.

Backtests under `strict` policy halt loudly when scope intersects
these holes — exactly the right behavior. Nothing in the system
silently substitutes for missing vendor data.

### Progress tracking

`mt data status` is the progress query. Run it during backfill to see
fresh counts of bars stored and remaining gap counts. The same
command serves operations and migration.

### Provider hole acceptance

Some sessions EODHD doesn't have. They become `PROVIDER_HOLE` and
remain so. We do not fall back to AV (deprecated) or any other
secondary in this design — multi-provider hole-filling is future work
and explicitly not in scope here. Honest gaps are preferred to
inherited stale data.

## Out of scope

The following were considered and dropped to keep the system at
operator-scale:

- Quality validator protocol with registry-driven extension. (Use a
  function call. Add a new audit by editing the audit function.)
- Recovery coordinator with report → fix → verify workflow.
  (Replaced by `mt data refetch` directly; operator decides what to
  refetch.)
- Persistent JSON quality reports as artifacts. (Replaced by
  `mt data status` running on demand.)
- `acquisition_gap_targets` table. (Replaced by `data_gaps` driving
  the daemon directly.)
- `coverage_gaps` table. (Replaced by `data_gaps`.)
- Scope resolver with `--symbols-from <table>` (file path is
  supported per audit; tables aren't because we don't have universe
  membership tables yet).
- Scheduled quality runner. (Use cron if needed.)
- Light-trading-day detection beyond gap detection. (Sessions with
  any positive bar count are present; sub-session quality is audit
  territory if needed at all.)
- Multi-provider hole-filling. (Single provider per granularity for
  now. Adding a secondary is a separate slice when justified.)
- Threshold-based session presence. (Replaced by zero-bar test:
  any positive bar count = session present.)

These are not deferred — they are designed out. If a use case for one
of them emerges, the bar to add it back is "name an operator pain
that the current system does not solve."

## Future work

- **Cross-vendor audit option for `mt data audit`** — `--vendor yahoo`
  flag that compares stored adj_close against a second vendor's
  published adjusted close. Single function extension, no protocol.
  Add when a second vendor is integrated.
- **Materialized `data_status`** — only if `mt data status` becomes
  too slow at full-universe scope. Measurement-driven.
- **`mt data refetch --auto`** — daemon-driven gap repair without
  manual symbol selection. Add when manual refetch shows operator
  pain.
- **Tick-granularity extension** — initiative 200. Tick uses its own
  storage (parquet + QuestDB or similar) and its own gap-tracking
  table (`tick_gaps`) keyed by sequence-number ranges, not session
  timestamps — `data_gaps` is **not** reused for tick because the
  semantics differ. What transfers from this initiative is the
  *pattern*: a single gap-tracking table, a status view, a
  `compute_missing_ranges` analog over sequence numbers, the same
  three operator commands shape (`mt data tick status / refetch /
  audit`), and the strict-by-default backtest gap policy. The
  ticks-as-pattern-not-table reuse is intentional; equity ticks are
  out of scope and futures ticks have a different universe and feed
  shape.
- **Multi-provider hole-filling** — auto-fall-through to a secondary
  provider on `PROVIDER_HOLE`. Requires deciding which provider is
  authoritative when they disagree. Separate design conversation.
- **Index-membership-at-time-T** — point-in-time SP500 / NDX / etc.
  membership for backtest scope filtering. Vendor data buy plus
  schema slice.

## Adjusted-on-read (slice 152)

Slice 152 replaces the adjusted-on-write model with adjusted-on-read.
This section is authoritative for the post-152 architecture.

### What changed

- `adj_open`, `adj_high`, `adj_low`, `adj_close`, `k_factor`,
  `adjusted_at` are dropped from `daily_ohlcv` and `minute_ohlcv`.
- `acquisition_state.last_adjusted_ca_snapshot_id` is dropped.
- `ADJUSTMENT_DRIFT_EPSILON` constant is removed.
- The `data/adjustment/` package (band_writer, verify, verify_eod,
  audit, context) is deleted.
- `daemon/ca_drift.py` and its CA-detection recompute cycle are deleted.
- MarketDB is removed. `splits` and `dividends` live in TimescaleDB only.
- AlphaVantage is removed. EODHD is the sole OHLCV provider.
- The `backtest/` directory is deleted.

### What stays

`compute_k_factor` math is preserved. `splits` and `dividends` tables
exist in TimescaleDB with the same shape as before. The daemon still
fetches CA data via `mt data ca update`.

### New adjustment contract

One function in `src/manta_trading/data/adjustment.py`:

```python
def adjusted(bars, symbol, conn, *, ca_snapshot=None) -> bars
```

- Reads `splits` and `dividends` from TimescaleDB for the symbol and
  date range of `bars`.
- Looks up `prev_close` from `daily_ohlcv` for each dividend ex-date.
- Applies `compute_k_factor` per bar's date. Returns bars unchanged if
  no CAs exist for the symbol.
- Raises `KeyError` if `prev_close` is missing for a CA date.
- Pure function. No side effects. Optional `ca_snapshot` for replay.

### Read API

`TimescaleMinuteDataDB.get_minute_data` and `TimescaleDailyDataDB.get_daily_data`
both accept `adjusted: bool = True`. When `True` they call `adjusted()`
before returning. Default is `True` — callers get adjusted bars unless
they explicitly opt out with `adjusted=False`.

### Continuous aggregates

7 caggs in the post-152 schema, all projecting raw OHLCV:

- 4 over `minute_ohlcv`: `minute_5min_ohlcv`, `minute_15min_ohlcv`,
  `minute_hourly_ohlcv`, `minute_4hour_ohlcv`
- 3 over `daily_ohlcv`: `daily_weekly_ohlcv`, `daily_monthly_ohlcv`,
  `daily_quarterly_ohlcv`

Adjustment is applied at read time by the caller via `adjusted()`.
Caggs do not store adjusted prices.

## References

- [data-correctness-architecture.md](../reference/data-correctness-architecture.md) — invariants this initiative closes
- [120-arch.data-acquisition.md](120-arch.data-acquisition.md) — daemon code is reopened by slice 145 to add the gap-driven backfill loop; slice 146 adds the long-running daemon, named lists, `mt data ca`, and CA-detection; slice 152 removes CA-detection recompute and switches to adjusted-on-read
- [100-arch.data-storage.md](100-arch.data-storage.md) — storage tables and trading_calendar (consumed unchanged)
