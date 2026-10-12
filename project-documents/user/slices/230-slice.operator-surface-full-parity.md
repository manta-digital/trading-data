---
docType: slice-design
slice: operator-surface-full-parity
project: trading-data
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [221, 225, 229]
interfaces: [231, 232, 234]
dateCreated: 20261012
dateUpdated: 20261012
status: not_started
---

# Slice Design: operator-surface-full-parity

## Overview

229 left the tick tier able to say what it holds (`status`, `coverage`)
and which contract is active (`active`). It still cannot hand an operator
the data itself, and the project's composed screens do not know it exists.
This slice closes the gap with the minute tier on the CLI:

- **`mt data tick get`**: ticks, or bars derived from ticks, for one
  contract (`--contract ESZ4`) or for a product under a roll rule
  (`--product ES --rule v.0`). The product read follows 229's
  `build_schedule` session by session and never merges two contracts.
- **`mt data tick debug {requests, units, definitions}`**: plain reads of
  the manifest and the contract definitions, so no investigation needs
  ad-hoc SQL.
- **A tick line in `mt data accounting`** and a **`futures ticks` line in
  `mt data overview`**, which is also what `mt-run data overview` prints in
  production. Both lines say `not configured` or `unreachable` when they
  cannot read the tick database. They are never left out.
- **The runbook's tick operator procedures** and the **data-correctness
  contract rows** for these surfaces.

`health` is not here. Its tick line, with the second connection pool and
the retention-deadline finding, is 234's, as the slice plan places it.

## Value

- An operator, or a research notebook driven from a shell, can pull the ES
  ticks or 1-minute bars for a contract or for the continuous front month
  in one command, as they already can for an equity with `mt data get`.
- The continuous read is honest about its inputs. It names the contract
  serving each segment, lists sessions the rule cannot resolve and
  sessions not fully held, and exits 3 when either list is non-empty.
- Investigating a failed or stuck unit, a cost question or a missing
  contract needs one `debug` command instead of a `psql` session against
  the tick cluster.
- `mt data overview` and `mt data accounting`, the two screens an operator
  reads first, show the tick tier beside minute, daily and Kalshi. A tick
  outage shows up there as a line that says so.
- 231 gets a tested read path (ticks and bars, per session, per contract)
  to put behind `/api/v1/futures/*`.

## Technical Scope

**Included**

1. `mt data tick get WHAT (--contract SYMBOL | --product P [--rule RULE])
   [--start D] [--end D] [--csv | --json]`, where `WHAT` is `ticks` or a
   bar granularity (`1m 5m 15m 1h 4h 1d`; `1d` is the session bar). The
   output streams in text, CSV or JSON.
2. `mt data tick debug requests`, `debug units` and `debug definitions`,
   with filters (TD6).
3. A tick section in `mt data accounting` (text and JSON), one line per
   universe product, built from the same session verdicts `status` uses.
4. A `futures ticks` source line in `mt data overview` (text and JSON).
5. The `not configured` / `unreachable` / `migrations pending` states on
   both composed surfaces (TD8).
6. One fixed-point price formatter and one nanosecond timestamp formatter,
   shared by `get` and `debug` (TD4).
7. Runbook: a "Operating the tick tier" section in
   `runbooks/100-production-operations.md` and rows in its Quick reference.
8. Data-correctness contract: the I8, I10 and I14 rows, and the Required
   CLI commands list.
9. The standing 220 obligations: the Kalshi pass-contract diff task, the
   realtime-paths check, and "does this belong in the API?" for each new
   surface.

**Excluded**

- `health` (234), `PassKind.TICK`, `mt-run tick`, `mt-run follow tick`
  and the `mt-run status` row (234). Overview gets no tick PASSES row until
  `PassKind.TICK` exists.
- Adding `MT_TICK_DB_URL` to `/etc/manta-trading.env`. It is not there
  today. 234 owns the service environment files. Until then
  `mt-run data overview` prints `futures ticks  not configured`, which is
  the correct and explicit answer (TD8).
- Any API route or change to `/api/v1/overview` (231). The tick overview
  line is gathered on the CLI side only (TD7).
- A separate `backfill` verb (TD9).
- Weekly, monthly and quarterly tick bars, back-adjusted or stitched
  series, and VWAP or trade-count columns. None is asked for, and the
  architecture rules out stored or adjusted continuous series.
- Any change to the tick schema. This slice adds no migration.

## Dependencies

### Prerequisites

- **229 (complete):** `build_schedule`, `RollRule.parse`,
  `DEFAULT_ROLL_RULE`, `roll_conninfo`, `held_session_bounds`, the TD4
  roll-day default and the `RollRangeTooLong` cap.
- **225 (complete):** `session_verdicts`, `ledger_totals`, the `status`
  bucket vocabulary, `connect_migrated`, `TickDatabaseUnreachable` and the
  read-verb exit mapping (`run_mapped`, `read_storage`).
- **221 (complete):** the `CME_EQUITY` calendar and session bounds
  (`open_calendar`, `calendar_sessions`).
- **922 (complete):** `gather` / `build_overview` / `render_overview`,
  `SourceFreshness`, `overview_payload`.
- The ES data already held: the trades job (2024-08-30 → 09-29, with the
  September roll) and the tbbo job (2024-11-02 → 2025-01-01, with the
  December roll).

### Interfaces Required

- `tick_trade` with its key `(instrument_id, ts_event, sequence,
  sequence_ordinal)`, nanosecond `BIGINT` times and raw fixed-point prices.
- `tick_definition`, `tick_request`, `tick_archive_unit`,
  `tick_ingest_ledger` as 222–225 left them, and `COVERAGE_PREDICATE` for
  "current units".
- `Granularity` in `constants.py` for the bar tokens.

## Architecture

### Component Structure

New modules, each kept near 300 lines:

| Module | Role |
|---|---|
| `data/tick/tick_reads.py` | Per-session reads of `tick_trade`: raw ticks and bucketed bars for one `(instrument_id, open_ns, close_ns)` window, over a server-side cursor. Pure SQL plus row shaping. |
| `data/tick/get_plan.py` | Builds a `GetPlan` (the ordered list of `(session_date, instrument_id, raw_symbol, open_ns, close_ns)` to read, plus `unresolved` and `incomplete`) for a contract read or a product read. Calls `build_schedule` for products. |
| `data/tick/tick_format.py` | `price_text(raw)` (fixed-point → exact decimal string, `UNDEF_INT64` → `None`) and `ts_text(ns)` (ISO 8601 UTC with nine fraction digits). |
| `data/tick/debug_reads.py` | The three debug queries with their filters. |
| `data/tick/tick_accounting.py` | One `TickAccountingLine` per universe product from the session verdicts, and the unavailable states. |
| `cli/commands/tick_get_cmds.py` | `get`: option parsing, the plan, streaming output in three formats, exit code. |
| `cli/commands/tick_debug_cmds.py` | The `debug` sub-typer and its three verbs. |
| `data/tick/availability.py` | The line-state vocabulary: `TickAvailability` (`ok`, `not_configured`, `unreachable`, `migrations_pending`) and `TickUnavailable(state, detail)`. No I/O, no CLI import; 231 reuses it (TD8). |
| `cli/tick_composed.py` | The one place a CLI composed surface (accounting, overview) opens the tick database and classifies each failure into a `TickUnavailable` (TD8). |

Changed modules: `cli/commands/tick.py` (registration),
`cli/commands/accounting.py` (tick section), `cli/commands/overview.py`
(`gather` adds the tick fact), `cli/overview_types.py`
(`SourceFreshness.unavailable`), `cli/rendering/overview.py` (render the
state), `data/tick/constants.py` (TD10 constants).

### Data Flow

**`get --product ES --rule v.0 1m --start 2024-09-16 --end 2024-09-20`**

1. Parse `WHAT` against `TICK_GET_CHOICES`, the rule with `RollRule.parse`
   (default `DEFAULT_ROLL_RULE`), and the product against `TICK_UNIVERSE`.
2. `connect_migrated` checks the tick track (exit 1 if migrations are
   pending). No lock, no archive directory, no provider key.
3. `build_schedule(tick_url, calendar_url, entry, rule, start, end)` returns
   the answers for every session in `[start, end)`. Its snapshot, cap and
   failure mapping are 229's (TD9 there).
4. `session_verdicts` for the same sessions gives the sessions whose
   verdict is not `complete`. A resolved session that is not complete is
   still read, and is listed in `incomplete` with its verdict.
5. Session bounds come from the calendar. The plan is the resolved
   sessions in date order, each with its contract and `[open_ns, close_ns)`.
6. One `REPEATABLE READ READ ONLY` transaction on the tick database runs
   one query per planned session, in order, each over a server-side cursor
   fetched in batches of `TICK_GET_FETCH_ROWS`. Rows go to the writer as
   they arrive. Nothing is collected in memory.
7. The writer prints, and the exit code is 3 if `unresolved` or
   `incomplete` is non-empty, else 0.

**`get --contract ESZ4 ticks`**

1. `raw_symbol` → `instrument_id` from `tick_definition`. Candidates are
   definitions whose validity window overlaps the requested range (or any
   window, when no range is given). No match exits 1 naming the symbol.
   More than one match exits 1 listing each `instrument_id` with its
   expiration and asking for a range that separates them. Exchange symbols
   repeat every decade, and a guess here would serve the wrong contract.
2. The default range is the contract's held sessions (first to last ledger
   session of that instrument under current units). With a range, the
   sessions are the calendar sessions in `[start, end)` inside the
   contract's validity window.
3. Steps 4–7 as above, with no schedule. TD4 of 229 applies: a contract
   read serves every held session of that contract, roll or not.

**Composed surfaces**

```
mt data accounting ──▶ minute accounting (unchanged, primary DB)
                  └─▶ tick_composed.open() ─▶ tick_accounting.lines()
mt data overview  ──▶ gather_db_facts (unchanged, primary DB)
                  ├─▶ EODHD credits (unchanged)
                  └─▶ tick_composed.open() ─▶ newest event from ledger
```

Each composed read opens a read-only connection with
`TICK_COMPOSED_CONNECT_TIMEOUT_SECONDS` and
`TICK_COMPOSED_STATEMENT_TIMEOUT_SECONDS` (TD10), so a hung tick cluster
cannot stall the screen. A statement that hits the timeout gives the
`unreachable` line, naming the timeout.

### State Management

The slice is read-only on every database. It adds no table, migration,
setting or `pass_runs` row. The accounting command still records its
`PassKind.ACCOUNTING` row with the minute summary as its detail, unchanged.
The overview's universe line therefore still means the minute universe.

## Technical Decisions

### TD1. `get` is one verb; `WHAT` is a required positional, as in `mt data get`

```
mt data tick get WHAT (--contract SYMBOL | --product P [--rule RULE])
                 [--start D] [--end D] [--csv | --json]
```

- `WHAT` is `ticks` or a bar token. `mt data get SYMBOL GRANULARITY` has no
  default granularity, so `WHAT` has none either. Two verbs (`ticks`,
  `bars`) would duplicate every option and the plan logic.
- The target is an option, not a positional. `ES` and `ESZ4` are both
  plausible strings, and telling them apart by spelling is guessing. Exactly
  one of `--contract` and `--product` is required (exit 1 otherwise).
- `--rule` is accepted only with `--product`. Without it the rule is
  `DEFAULT_ROLL_RULE`, and the output marks it `(default)` as `active`
  does.
- `--csv` and `--json` are mutually exclusive (exit 1).

### TD2. Defaults follow the minute tier; dates follow the tick subgroup

- **Range:** `mt data get` defaults to everything it holds (1970 →
  today). `tick get` does the same: with no `--start`/`--end`, the range is
  the held sessions (`held_session_bounds` for a product, the contract's
  ledger sessions for a contract). `--start` alone runs to the last held
  session, and `--end` alone starts at the first.
- **End date:** exclusive, in session dates, as in every tick verb that
  takes a range (`coverage`, `active`, `pass`). `mt data get` treats its
  end as inclusive in UTC days, so the same `--end` means different ranges
  on the two tiers. This departs from the architecture's "same questions,
  same defaults" constraint, on purpose: a tick subgroup with two date
  conventions would be worse. The departure is recorded, not left implied:
  - an architecture Revision Log entry (a required task, not optional);
  - the `get` help text and the runbook both state "end exclusive, unlike
    `mt data get`";
  - the I10 contract row names the difference.
- **The cap:** a product read is bounded by 229's
  `TICK_ROLL_MAX_SESSIONS`, because `build_schedule` enforces it. When the
  held range is longer (the 2020+ universe will be), the default read
  exits 1 with `RollRangeTooLong`, naming the cap and asking for
  `--start`/`--end`. A contract read has no schedule and no cap, since a
  contract is never live for more than a few hundred sessions.
- **No adjustment:** futures have no corporate actions, so there is no
  `--raw`.
- **No row ceiling:** `mt data get` has none. `tick get` streams, so its
  memory use does not grow with the range. The operator chose the range.

### TD3. Read per session, in order, over a server-side cursor

- One query per `(session, instrument)` window instead of one query over
  an `unnest` of windows. Each window is a range scan on the primary key
  prefix `(instrument_id, ts_event)`, already in output order, so there is
  no sort over the whole result.
- Each fetch is one short statement, so the 30 s statement timeout from
  `roll_conninfo`'s bounds holds even for a multi-month read.
- **Ticks:** `ORDER BY ts_event, sequence, sequence_ordinal`, the table's
  key order.
- **Bars:** grouped by bucket within the session window. The bucket start
  is `GREATEST(time_bucket(width, ts_event), open_ns)`, so a bar never
  crosses a session boundary, and the first bar of a session starts at the
  session open. A bucket that straddles the daily break becomes two bars,
  one per session. On a roll they belong to different contracts. `1d` is
  the whole session: one bar stamped with the session open and labelled
  with the session date.
  - Open and close are the first and last trade in key order
    `(ts_event, sequence, sequence_ordinal)`, not just `ts_event`. Several
    trades can share a nanosecond. A test seeds such ties.
  - Volume is `sum(size)`. Empty buckets produce no row, as in the minute
    tier.
- **Two snapshots, stated:** `build_schedule` owns its connection and
  snapshot (229 TD9), and the data read is a second snapshot. An ingest
  that commits between them can change a later session's ledger, and so
  the answer. The output carries each segment's contract and the
  schedule's `definitions_as_of`, so a re-run is comparable. Sharing one
  snapshot would mean taking connection ownership away from the roll path,
  which 229 rejected.

### TD4. Exact prices, nanosecond times

- `tick_trade.price`, the BBO prices and `tick_definition`'s
  `min_price_increment` are DBN fixed-point integers. One constant,
  `TICK_PRICE_SCALE = 10**9` (DBN `FIXED_PRICE_SCALE`), and one function,
  `price_text`, turn them into exact decimal strings (`5950.25`) through
  `Decimal`, never `float`. `UNDEF_INT64` becomes `null` / empty. Text, CSV
  and JSON all use the string. JSON carries it as a string, because a JSON
  float cannot guarantee exactness.
- Times are ISO 8601 UTC with nine fraction digits
  (`2024-09-16T13:30:00.123456789Z`), formatted from the integer by
  `ts_text`, because `datetime` stops at microseconds.

### TD5. Output shapes

**Tick row:** `ts_event, session_date, raw_symbol, instrument_id, price,
size, side, bid_px, ask_px, bid_sz, ask_sz, bid_ct, ask_ct, sequence`.
The BBO columns are always present: null on trades-tier rows and set on
tbbo rows. A read that crosses tiers keeps one column set.

**Bar row:** `time, session_date, raw_symbol, instrument_id, open, high,
low, close, volume`.

- **Text:** a header naming the target, the rule and each segment
  (`2024-09-16 → 2024-09-17  ESU4 (118)`), then one fixed-width line per
  row printed as it arrives, then `N row(s)`, then the `unresolved` and
  `incomplete` lists. Rows are not drawn as a Rich table, because Rich
  buffers the whole table.
- **CSV:** a header row and data rows only, on stdout. The segments and
  the two lists go to stderr, so a redirected file stays clean.
- **JSON:** one object, written incrementally:
  `{target, what, start, end, segments, unresolved, incomplete, rows,
  row_count, complete, error, exit_code}`. `complete` is `false` and
  `error` is set only when the read stops partway (TD11).
  - `target` is `{product, rule, is_default}` or
    `{contract, instrument_id}`.
  - `unresolved` is `[{session_date, reason}]` (229's shape). `incomplete`
    is `[{session_date, status}]`.
  - Every key except `rows`, `row_count` and `exit_code` is known before
    the first row is read.
- **Empty result:** exit 0 with zero rows and the header, as `mt data get`
  does. It exits 3 instead when a list explains the emptiness.

**Exit codes** (all from `tick_exit.py`; no new code):

| Code | When |
|---|---|
| 0 | Every session resolved and complete |
| 3 | Output printed, and `unresolved` or `incomplete` is non-empty |
| 1 | Bad `WHAT`, rule, date or option combination; unknown product; unknown or ambiguous contract; range over the cap; range outside the calendar; pending tick migrations; `MT_TICK_DB_URL` unset |
| 4 | Tick database or calendar unreachable, timed out, or dropped |

### TD6. `debug`: three reads, one table each, no interpretation

```
mt data tick debug requests    [--product P] [--start D --end D] [--json]
mt data tick debug units       [--product P] [--start D --end D]
                               [--state S] [--fetch-status F] [--json]
mt data tick debug definitions [--product P] [--symbol RAW]
                               [--as-of D] [--spreads] [--json]
```

- **requests:** `tick_request` rows: id, schema, symbols, stype, range,
  delivery mode, adopted, job id, estimated and actual cost, record count,
  billed bytes, requested, committed and deadline times. `--start/--end`
  filters on range overlap.
- **units:** `tick_archive_unit` rows joined to their request's schema:
  id, request, date, schema, state, fetch status, failure reason,
  attempts, last attempt, file path and size, provider and decoded record
  counts, superseded-by, repurchase-of, reopened-at. `--state` and
  `--fetch-status` take the `UnitState` / `FetchStatus` values (choices
  rendered from the enums). `--start/--end` filters on `unit_date`,
  end exclusive. These are the filters an investigation starts from
  (which units failed, which are stuck, which days). Narrowing to one
  unit or request is `--json | jq`, so there are no id filters.
- **definitions:** `tick_definition` rows: instrument id, raw symbol,
  class, activation and expiration (`ts_text`), tick size (`price_text`),
  multiplier, currency, unit id. Outrights only unless `--spreads`, the
  same default as `status`'s `spreads_hidden`. `--as-of D` keeps the
  definitions valid at session D's open.
- `--product` maps to the universe entry's request symbols for the
  manifest reads and to `asset` for definitions. An unknown product exits 1.
- No row cap. The whole 2020+ manifest is a few thousand rows.
- Exit codes: 0 (including no rows), 1 (bad input, unset URL, pending
  migrations), 4 (unreachable). `debug` prints facts and asserts nothing,
  so it never exits 3.
- Text output is a Rich table (these results are small); JSON is
  `{rows, exit_code}`.

### TD7. Composed lines: what each says

**Accounting**, under the minute section:

```
futures tick universe:
  ES  41/42 sessions complete (97.6%); 1 pending; 0 failed; 0 holed  · 27.6 M records
```

- The figures come from the same session verdicts and buckets that
  `status` reports for the product's wanted range, through one function.
  If `build_status` does not expose the bucket step on its own, it is
  extracted, not copied.
- Records are the ledger sum over current units.
- JSON gains `tick: {state, products: [{product, sessions_wanted,
  sessions_complete, buckets, records}]}`. `state` is `ok`,
  `not_configured`, `unreachable` or `migrations_pending`. `products` is
  empty unless `state` is `ok`.
- The `PassKind.ACCOUNTING` detail stays the minute summary. The overview
  universe line keeps its meaning.

**Overview**, as a fifth source line:

```
SOURCES
  minute bars      2026-10-10 20:59 UTC
  ...
  futures ticks    2025-01-01 21:59 UTC
```

- `newest` is the latest `last_event_ns` in `tick_ingest_ledger` under
  current units. That is the newest stored tick, read from the ledger
  rather than a `max()` over the hypertable.
- It is gathered in `gather` (CLI) next to the credits call, not in
  `gather_db_facts`. `/api/v1/overview` calls `gather_db_facts` and maps
  sources explicitly into `SourceRecord`, so the API response does not
  change. 231 decides the API's tick freshness.
- `SourceFreshness` gains `unavailable: str | None`. It is `None` for every
  existing source. JSON sources gain the `unavailable` key, so existing
  keys are unchanged.

### TD8. One function opens the tick database for composed surfaces

The vocabulary and the opener are split by layer:

- `data/tick/availability.py` holds `TickAvailability` and
  `TickUnavailable(state, detail)`. It has no I/O and no CLI import, so
  231 can reuse the states without depending on `cli/`.
- `cli/tick_composed.open(settings)` connects, checks the tick track, and
  returns either a connection or a `TickUnavailable`. It serves the CLI's
  one-shot commands only. The API keeps its own startup-opened,
  self-reconnecting pool (architecture, "Serving-layer failure posture")
  and maps that pool's failures onto the same states.

Accounting and overview render a `TickUnavailable` as the line. They never
skip the line or fail the command:

| Condition | Line |
|---|---|
| `MT_TICK_DB_URL` unset | `futures ticks    not configured (MT_TICK_DB_URL unset)` |
| connect timeout / refused / `OperationalError` / `QueryCanceled` | `futures ticks    unreachable: <first line of the error>` |
| tick track has pending migrations | `futures ticks    migrations pending (mt data migrate apply --track tick)` |

- The command's exit code does not change for a tick-line state. Minute
  accounting and the overview keep their own exit rules (2 when the
  primary database fails). A tick outage must not turn the minute
  operator screen red. The alarm surface is `health`, in 234, which is
  where the architecture puts it. The line is the report.
- This posture is the 234 "loud line, continue" rule applied early, with
  the same three states. 234's health line uses `tick_composed.open`, so
  the states are defined once. This makes 230, not 234, the first slice
  with a tick composing surface. The slice plan's 234 entry is updated to
  say so.
- No credential appears in a line. Errors are reduced to their first line,
  and psycopg's connection errors name the host, not the password. A test
  asserts that the URL's password never appears.

### TD9. No `backfill` verb

The contract's I10 vocabulary lists `backfill`. In the tick tier the wanted
range lives in the universe, and `pass` acquires whatever the universe
wants and the manifest lacks. Backfilling an older range is a universe edit
followed by `mt data tick pass`. `pass --start/--end` only narrows within
the universe. An alias verb would be a second name for the same operation.
I10's row is updated to say `pass` is the tick tier's backfill. The minute
tier has no `backfill` verb either; `pull` plays that role.

### TD10. Constants, defined once in `data/tick/constants.py`

- `TICK_GET_TICKS = "ticks"`
- `TICK_BAR_GRANULARITIES`: a tuple of `Granularity` members
  (`1m 5m 15m 1h 4h 1d`), never re-spelled strings
- `TICK_GET_CHOICES = (TICK_GET_TICKS, *TICK_BAR_GRANULARITIES)`
- `TICK_PRICE_SCALE = 10**9`
- `TICK_GET_FETCH_ROWS`: the server-side cursor batch size
- `TICK_COMPOSED_CONNECT_TIMEOUT_SECONDS = 5`: the same bound the overview
  uses for the primary database, defined separately because accounting
  uses it too and should not borrow an overview-named constant
- `TICK_COMPOSED_STATEMENT_TIMEOUT_SECONDS = 10`: the bound for any one
  statement on the accounting or overview tick read. The accounting read
  is `status`'s verdict step over each product's wanted range, which
  finishes far inside it on today's data. The walkthrough times it (step
  6), and the 2020+ universe re-checks it in 232.

Bucket widths come from an existing `Granularity` → duration map if one
exists, and from one new map otherwise.

### TD11. A streamed `get` that stops partway

Rows are already on stdout when a failure happens, so the output has to
say it is partial. Exit 0 must never mean that.

**The database drops or a fetch times out** (`OperationalError`, which
includes `QueryCanceled`, after the first row). The writer stops, rolls
back the transaction, and finishes the output:

| Format | stdout | stderr | Exit |
|---|---|---|---|
| JSON | The object is closed and stays valid: the rows so far, `row_count`, `"complete": false`, `"error": "<first line>"`, `"exit_code": 4` | — | 4 |
| CSV | The rows so far. No in-band marker, which would corrupt the file | `partial output: N rows written, then: <first line>` | 4 |
| Text | The rows so far, then `PARTIAL: N row(s), then: <first line>` in place of the `N row(s)` footer | the error | 4 |

A failure before the first row takes the ordinary error path (TD5's exit
table). On success, JSON carries `"complete": true` and `"error": null`.

**The reader goes away** (`get … | head`, a closed terminal). Python raises
`BrokenPipeError` on write:

- The writer catches it, closes the cursor, rolls back the transaction and
  closes the connection.
- It points stdout at `/dev/null`, so the interpreter's final flush does
  not raise a second error.
- It exits 0 with nothing on stderr.

The reader chose to stop, so this is not a failure of `get`. With
`pipefail`, the pipeline's status is the reader's.

Tests: an integration test terminates the backend
(`pg_terminate_backend` from a second connection) after the first batch,
in each format, and checks the output above. A test closes the read end
of a pipe after the first rows and checks for exit 0, an empty stderr,
and no open transaction left on the server.

### Realtime paths

Neither path is ruled out. `get` reads stored ticks per session, whatever
wrote them. Path A (assembling from realtime) would add rows to the same
table. Path B (historical with delay) is what is stored now. Session-bounded
bars do not assume a finished session. A session still being written reads
as `incomplete`, which is the honest answer under either path.

### Does this belong in the API?

- **`get`:** yes. 231's ticks and bars endpoints by contract and by
  product plus rule, over `get_plan` and `tick_reads`.
- **`debug`:** no. These are operator reads of internal bookkeeping (file
  paths, job ids, costs). 231's contract catalog serves the client-facing
  part of definitions.
- **Accounting line:** the same figures belong in 231's `/api/v1/status`
  tick coverage, from the same function.
- **Overview line:** yes, in 231's `/api/v1/overview`, from the same ledger
  read.

### Patterns and Conventions

- Every new verb goes through `run_mapped` with `read_storage()`, so
  unreachable means exit 4, everywhere.
- Shared options come from `tick_store_cmds` (`JSON_OPTION`,
  `PRODUCT_OPTION`, `DATE_FORMAT`). No option is re-declared.
- Each command module has an `epilog` with examples, following the
  existing tick verbs.

## Implementation Details

### API Contracts

CLI surfaces only. TD1, TD5 and TD6 give the commands, their shapes and
their exit codes. TD7 gives the composed lines. Changed JSON contracts:

- `mt data accounting --json` gains a top-level `tick` object. Existing keys
  are unchanged.
- `mt data overview --json`: each `sources[]` item gains `unavailable`, and
  a `futures ticks` item is appended. Existing items keep their shape.

## Integration Points

### Provides to Other Slices

- **231:**
  - `get_plan` and `tick_reads` serve the ticks and bars endpoints.
    `unresolved` and `incomplete` are the arrays 229 obliges 231 to carry.
  - `price_text` and `ts_text` keep exact prices and nanosecond times
    identical between the CLI and the API.
  - The accounting function feeds `/api/v1/status`.
  - The ledger freshness read feeds `/api/v1/overview`.
  - `data/tick/availability.py`'s states are the vocabulary for the API's
    503 and health field. 231 reuses the states, not the CLI opener, and
    maps its own pool's failures onto them.
- **232:** GC appears in `get`, `debug`, accounting and overview with no
  code change once it is in `TICK_UNIVERSE`.
- **234:** `tick_composed.open` is the health line's opener, and 230 is
  now the first tick composing surface (the slice plan's 234 entry is
  updated). 234 adds
  `MT_TICK_DB_URL` to `/etc/manta-trading.env`, which turns production's
  `not configured` overview line into a real one.

### Consumes from Other Slices

- **229:** `build_schedule`'s answers, cap and failures, unchanged. A
  schedule error maps through `tick_exit` as it does for `active`.
- **225:** session verdicts. If verdict semantics change, `incomplete` and
  the accounting line follow, because they call the same function.
- **221:** session bounds. A range outside the populated calendar is 229's
  refusal naming `mt data extend`.

## Success Criteria

### Functional Requirements

1. `get ticks --contract ESZ4 --start 2024-09-16 --end 2024-09-18` prints
   ESZ4's ticks for the two sessions in key order. The row count equals
   the ledger `record_count` sum for ESZ4 over those sessions.
2. `get 1m --product ES --start 2024-09-16 --end 2024-09-20` (default
   `v.0`) serves ESU4 through session 09-17 and ESZ4 from 09-18, as
   `active` shows for the same range. No bar mixes the two contracts.
   Each bar's volume equals the sum of that contract's tick sizes in the
   bucket.
3. A bar never spans a session boundary. A `4h` read across the daily
   break yields two bars, and the second starts at the session open.
4. `1d` gives one bar per session whose open, high, low, close and volume
   match the session's ticks, with ties at one nanosecond broken by
   `(sequence, sequence_ordinal)`.
5. A product read whose range begins at the first held session lists the
   first two sessions as unresolved with 229's reasons, serves the rest,
   and exits 3. It never fills them from another rule or contract.
6. A resolved session whose verdict is not `complete` is served, listed in
   `incomplete` with its verdict, and the exit code is 3.
7. An ambiguous `--contract` exits 1 listing every candidate
   `instrument_id` with its expiration. An unknown one exits 1 naming the
   symbol.
8. With no range, a product read covers the held sessions or refuses with
   the cap message. A contract read covers the contract's held sessions.
9. Prices are exact decimals in all three formats. An undefined BBO price
   is null or empty, never `9223372036.854775807`.
10. `--csv` writes only the header and rows to stdout. Segments and lists
    go to stderr.
10a. A tick connection killed after the first batch gives TD11's partial
    output in each format and exits 4. JSON stays parseable with
    `"complete": false`. A reader that closes the pipe early gets exit 0,
    an empty stderr, and no transaction is left open.
11. `debug requests`, `debug units` and `debug definitions` return the
    rows their filters select. `--state`/`--fetch-status` reject values
    outside the enums. Definitions hide spreads unless `--spreads`.
12. `mt data accounting` prints a tick line per product whose figures equal
    `status`'s buckets for that product, and `--json` carries the `tick`
    object.
13. `mt data overview` prints a `futures ticks` line whose time is the
    newest ingested event. `/api/v1/overview`'s response is byte-identical
    to before for the same facts.
14. With `MT_TICK_DB_URL` unset, with the tick cluster stopped or
    unreachable, and with a pending tick migration, accounting and overview
    each print the matching line from TD8, and their exit codes are what
    they would be without the tick line.

### Technical Requirements

- Unit tests:
  - `price_text`, including negative prices, `UNDEF_INT64` and whole
    numbers;
  - `ts_text`;
  - bar bucketing and session clipping;
  - the `get_plan` paths: contract, product, ambiguous contract,
    unresolved sessions, incomplete sessions;
  - the `tick_composed` states, including the password-never-printed test;
  - the overview rendering of each state.
- Integration tests on the test cluster with seeded ticks (`roll_seed` /
  `roll_synthetic`):
  - tick and bar reads across a seeded roll;
  - same-nanosecond ties;
  - a streamed read checking that rows arrive before the read finishes
    (the writer is called per batch);
  - each `debug` verb;
  - the accounting and overview lines against a reachable and an
    unreachable tick database.
- `mypy` and `ruff` clean on touched files (with the known pre-existing
  exceptions).
- New modules near 300 lines, functions near 50.
- Runbook: `100-production-operations.md` gains "Operating the tick tier":
  - reading state (`status`, `coverage`, `active`, `accounting`,
    `overview`);
  - reading data (`get`);
  - investigating (`debug`);
  - running passes by hand (`estimate`, `pass`, `ingest`, `adopt`,
    `reset`);
  - the exit-code table.

  Its Quick reference gains the tick rows.
- Contract (`reference/data-correctness-architecture.md`):
  - I8 gains `mt data tick debug` as the tick tier's no-SQL investigation
    surface;
  - I10 gains `get`, `debug`, and `pass` as the backfill (TD9), and
    states that tick `--end` is exclusive in session dates while
    `mt data get`'s is inclusive (TD2);
  - I14 gains 230's rule: a product-level `get` serves one contract per
    session, lists unresolved and incomplete sessions, and exits 3;
  - the Required CLI commands list gains the new verbs.
- The standing pass-contract diff against Kalshi is a named task, with no
  change expected because this slice does not touch the pass.

### Integration Requirements

- 231 can build its tick endpoints by calling `get_plan`, `tick_reads`,
  the accounting function and the freshness read, with no CLI import.
- Existing minute, daily and Kalshi output from `accounting` and
  `overview` is unchanged apart from the added tick line and the
  `unavailable` key.

### Verification Walkthrough

Run from the repo with `.env` loaded (it points `MT_TICK_DB_URL` at the
tick cluster). Instrument ids are the ones 229 recorded (ESU4 = 118,
ESZ4 = 183748).

1. **Continuous 1-minute bars over the September roll.**

   ```
   mt data tick get 1m --product ES --start 2024-09-16 --end 2024-09-20
   ```

   The header shows `v.0 (default)` and two segments:
   `2024-09-16 → 2024-09-17 ESU4 (118)` and
   `2024-09-18 → 2024-09-19 ESZ4 (183748)`. These match
   `mt data tick active --product ES --start 2024-09-16 --end 2024-09-20`.
   Bars before 09-18's open carry ESU4, and later bars carry ESZ4. Exit 0.

2. **Ticks for a contract, checked against the ledger.**

   ```
   mt data tick get ticks --contract ESZ4 --start 2024-09-18 --end 2024-09-19 --csv > /tmp/esz4.csv
   wc -l /tmp/esz4.csv
   mt data tick coverage --product ES --start 2024-09-18 --end 2024-09-19
   ```

   The CSV line count, minus the header, equals the ledger records for
   ESZ4 on 09-18. Prices read like `5650.25`.

3. **Session bars and the unresolved head of a held range.**

   ```
   mt data tick get 1d --product ES --start 2024-11-04 --end 2024-11-08 --json | jq '{unresolved, n: .row_count, exit_code}'
   ```

   The first held tbbo sessions have no ranking session held, so the
   sessions within two of 2024-11-04 appear in `unresolved` with 229's
   reason, and `exit_code` is 3. The remaining sessions have one bar each.

4. **Ambiguous and unknown contracts.**

   ```
   mt data tick get ticks --contract ESZ9; echo $?
   ```

   This exits 1 naming the symbol, because no definition is held for it.
   The ambiguous case (one raw symbol, two held contracts a decade apart)
   cannot be shown on today's data. Its integration test seeds two
   definitions for the same raw symbol.

5. **Debug reads.**

   ```
   mt data tick debug requests
   mt data tick debug units --state verified --json | jq '.rows | length'
   mt data tick debug definitions --product ES --as-of 2024-09-18
   ```

   The requests show both adopted jobs with their costs. The units count
   matches `status`'s awaiting-ingest bucket (0 today). The definitions
   list ESU4, ESZ4, ESH5 and the rest with exact tick sizes (`0.25`), and
   no spreads.

6. **Accounting and overview, healthy.**

   ```
   time mt data accounting
   mt data overview
   ```

   The accounting run finishes well inside
   `TICK_COMPOSED_STATEMENT_TIMEOUT_SECONDS`. Record the time in the
   walkthrough notes.

   Accounting ends with the `futures tick universe` line, whose session
   counts match `mt data tick status --product ES`. The overview SOURCES
   block has `futures ticks` with the newest ingested event, around
   2025-01-01 21:59 UTC.

7. **The three degraded lines.**

   ```
   MT_TICK_DB_URL= mt data overview; echo $?
   MT_TICK_DB_URL=postgresql://nobody@127.0.0.1:1/x mt data accounting; echo $?
   ```

   The first prints `futures ticks    not configured (MT_TICK_DB_URL unset)`
   and exits as it would have otherwise (0). The second prints the tick
   line as `unreachable: …` after the minute section and exits 0. The
   pending-migrations line is proved by its integration test, which uses
   the test cluster rather than production.

8. **Production.**

   ```
   mt-run data overview
   ```

   It prints `futures ticks    not configured (MT_TICK_DB_URL unset)`,
   because the production env file does not carry the tick URL until 234.
   That proves the line is present and explicit on the production path.

## Implementation Notes

### Development Approach

1. Constants and the two formatters, with unit tests.
2. `tick_reads` (ticks and bars per window), with integration tests on
   seeded ticks, including ties and session clipping.
3. `get_plan` for contracts, then products, with the
   unresolved/incomplete lists.
4. The `get` command and its three writers; the walkthrough steps 1–4.
5. `debug_reads` and `tick_debug_cmds`; step 5.
6. `tick_composed`, then the accounting and overview lines; steps 6–8.
7. Runbook, contract rows, the pass-contract diff task, the architecture
   Revision Log entry for TD2's end-exclusive date (required), and the
   slice plan's 234 entry (230 is the first tick composing surface).

Commit after each of these groups.

### Special Considerations

- `get` and `debug` read the production tick cluster from a developer
  shell. Both are read-only (`READ ONLY` transaction, application role).
  No statement in this slice writes to the tick database.
- A multi-month tick read is millions of rows. Streaming keeps memory
  flat, but the terminal is the bottleneck. The runbook points bulk reads
  at `--csv > file`.
