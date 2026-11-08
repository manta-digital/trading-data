---
docType: slice-design
slice: serving-the-new-tiers
project: trading-data
parent: user/architecture/300-slices.futures-historical-bars-statistics-and-tick-aggregation.md
dependencies:
- 302
- 303
interfaces:
- 307
- 308
dateCreated: 20261108
dateUpdated: 20261108
status: not_started
turns: 49
promptTokens: 4864185
cachedTokens: 4709281
cacheWriteTokens: 154812
completionTokens: 44849
durationSeconds: 504.1
costUsd: 3.078037
usageRounds: 1
pricedRounds: 1
usageByModel:
  claude-opus-5-5:
    rounds: 1
    costUsd: 3.0780371
    completionTokens: 44849
---

# Slice Design: Serving the New Tiers

## Overview

`/api/v1/futures/*` serves time bars by running `time_bucket` over `tick_trade` at read time (`tick_reads.read_bars`). So bars exist only where ticks do: ES from 2024-08-30 and GC from 2024-11-04. Slices 302 and 303 stored provider `ohlcv-1m` bars and `statistics`, and nothing serves either.

This slice applies the architecture's bar-source rule (journal 20261101 ADR) to every bars read, in the API and in `mt data tick get`:

- Provider `ohlcv-1m` is the only source of time bars. 1m is served as stored. 5m, 15m, 1h, 4h and 1d are rolled up from it on the CME session.
- Ticks never produce served time bars, including where no provider bar is held. The read-time tick bars are removed. Their one-minute SQL survives only as a new parity check, `mt data tick parity`, which compares tick-derived minutes with provider minutes where both are held. Its first run also answers issue #70 (do close-minute bars hide trades after the close?).
- Every bars response names its source schema.
- Open interest, settlement and cleared volume are not served yet. That is a recorded decision (D6), and the routes are drafted in issue #73.

The human and agent API docs and the OpenAPI file are updated. Provider `ohlcv-1h` and `1d` bucketing was checked for this design (D1). Neither is served.

## Value

- **Consumers (trading-engine, trading-ui):** futures bars reach back as far as provider bars are bought (2010 once 307 runs), not just the few months of held ticks. A bar means the same thing in every era.
- **Correctness:** one bars source per interval, named in the response, so a backtest never mixes tick-built and provider-built bars without knowing it ("never splice tiers silently").
- **Operators:** `mt data tick parity` measures the provider-against-tick agreement the architecture says must be measured, not assumed, and settles issue #70.
- **Architectural:** 308 (tick bars and compact ticks) starts from a bars path that no longer reads `tick_trade`, so "tick bars" can only mean N-trade bars.

## Technical Scope

**Included**

- A provider bar reader over `tick_ohlcv_1m`: 1m as stored, coarser sizes rolled up per session window, with intraday buckets anchored at the session open (issue #47).
- The bars routes (`/contracts/{symbol}/bars`, `/products/{product}/bars`) and `mt data tick get <size>` switched to that reader, with the plan's default range and `incomplete` list taken from the `BAR_1M` family.
- `source` on the bars response (and in `get`'s JSON and header output).
- Removal of the tick-derived bar SQL from every serving path. The one-minute variant moves to the parity module.
- `mt data tick parity`: tick-derived against provider one-minute bars per session, with close-stamped minutes marked (issue #70).
- The recorded decision not to serve `statistics` yet (D6).
- `docs/api/reference.md`, `docs/api/agents.md`, `docs/api/openapi.json`, the CHANGELOG and the operator runbook's `get`/`parity` lines.

**Excluded**

- The tick routes (`/ticks`) are unchanged. Their compact encoding and tick bars at any N are 308.
- `1s` bars (issue #40): they need `ohlcv-1s` (issue #71, future work). The source map (D2) is where it will join.
- Explicit bar period ends (issue #49): that is a contract across equities, futures and Kalshi, and it stays its own work. This slice adds no `end` field.
- `/api/v1/status` and health/accounting surfaces (305).
- Statistics routes, contract and product (D6, issue #73). Operators already read statistics through `mt data tick coverage --schema statistics`.
- Archiving or serving provider `ohlcv-1h`/`1d` (D1).
- Any purchase. Tests use the existing fixtures, and the first parity measurement uses the ES 2024 bars that 304 buys (D9).

## Dependencies

### Prerequisites

- **302 (complete):** `tick_ohlcv_1m`, `tick_bar_ledger`, `SchemaFamily.BAR_1M`, `FAMILY_LEDGER`, the bar family's session verdicts, and the derived 2024-12-03 bar fixture.
- **303 (complete):** the `basis` on segments and the `n` roll rule.
- **301 (complete):** CME sessions back to 2010-06-06. Session windows are the rollup frame.

### Interfaces Required

- `get_plan.contract_plan` / `product_plan` and `GetPlan` (230). They gain a family parameter (D3).
- `tick_status_scope.VerdictReader`, `status_reads.session_verdicts`.
- `roll_inputs.held_session_bounds` (gains a family parameter).
- `futures_read.read_series` (snapshot read under the row ceiling), `tick_access.tick_errors`, `serialization.send_timeseries` / `timeseries_200`.
- `tick_format.price_text`, `cell_value`.
- `scripts/cut_tick_fixtures.py` and `test/fixtures/databento/SOURCES.md`.

## Architecture

### Component Structure

```
api_server/routes/futures_series.py      bars routes → provider reader (D2, D3)
api_server/models/futures.py             FuturesBarsResponse gains `source`

data/tick/get_plan.py         plans take a family; verdicts_for(family) (D3)
data/tick/bar_reads.py        NEW: provider bar reader + session-anchored rollup (D2)
data/tick/bar_constants.py    BAR_SOURCE: granularity → provider schema (D2)
data/tick/tick_reads.py       read_ticks only; read_bars removed
data/tick/bar_parity.py       NEW: tick-derived 1m SQL + minute comparison (D7)

cli/commands/tick_get_cmds.py     bar sizes read provider bars (D3)
cli/commands/tick_parity_cmds.py  NEW: mt data tick parity (D7)
```

### Data Flow

**Bars (API and `get`):**

```
request(granularity, contract | product+rule, start, end)
  → plan(family=BAR_1M): sessions, one window per (session, contract),
      default range = held bar sessions, incomplete = BAR_1M verdicts
  → per window: bar_reads.read_provider_bars(window, granularity)
      1m:      SELECT … FROM tick_ohlcv_1m WHERE instrument_id AND ts_event ∈ [open, close]
      coarser: GROUP BY session-anchored bucket; first open, max high, min low,
               last close, sum volume; 1d = one bucket stamped at the open
  → rows (BAR_COLUMNS, unchanged) under the row ceiling, one REPEATABLE READ snapshot
  → FuturesBarsResponse{…, source: BAR_SOURCE[granularity] = "ohlcv-1m"}
```

A session with no provider bars yields no rows and appears in `incomplete` with its `BAR_1M` status. Ticks are never read. `incomplete` carries the load verdict only (`session_verdicts`). A session that is load-complete but fails 303's cross-tier volume check is served normally and is not listed: that finding belongs to `mt data tick coverage` and 305's surfaces. `reference.md` says so. Responses go through `send_timeseries` unchanged, so they inherit its gzip (level 1, encoded off the event loop).

**Parity:**

```
mt data tick parity (contract | product+rule, start, end)
  → plan(family=TICK_TIER) gives windows + TICK_TIER verdicts;
      verdicts_for(BAR_1M)(plan sessions) in the same snapshot, joined by session;
      compared sessions = complete in both
  → per window: tick-derived 1m (bar_parity SQL over tick_trade) ⋈ provider 1m (bar_reads)
      by bucket → agree | differ(fields) | provider_only | tick_only
  → per-session counts (incl. close-stamped minutes) → text; + minute list → --json; exit 0 or 3
```

### State Management

No new tables, columns or migrations. Every read is a pure function of the stored archive projection, inside one read-only snapshot. Provider bars are read only from `tick_ohlcv_1m`, whose rows are the current units' rows: supersession and reopen delete a unit's rows (302 D3), as for `tick_trade`.

## Technical Decisions

### D1 — Provider `ohlcv-1h` and `ohlcv-1d`: bucketing checked, rule unchanged
Status: decided

The slice plan asks for this check at design. Recorded 2026-11-08:

- **`ohlcv-1d` is a UTC-date bar.** The DBN schema definition (`databento/dbn`, `rust/dbn/src/enums.rs`) reads "Open, high, low, close, and volume at a daily interval based on the UTC date." A CME session runs from the previous evening (22:00 or 23:00 UTC) to the next afternoon, so one provider daily bar holds the end of one session and the start of the next. Serving it as `1d` would silently change what a daily bar means.
- **`ohlcv-1h` is an hourly bar on whole hours.** CME's open and close are on the hour in CT, and CT differs from UTC by whole hours, so its boundaries coincide with our session-anchored 1h buckets. It adds nothing that rolling up `ohlcv-1m` does not give.
- **`ohlcv-eod`** ("daily cadence based on the end of the trading session") exists in DBN, but `metadata.list_schemas("GLBX.MDP3")` does not offer it as of 2026-11-08. If it becomes available, the architecture's "revisited if we choose to use them" applies.

The architecture's rule stands: neither is archived or served.

### D2 — One provider bar reader, rolled up per session window
Status: decided

`bar_reads.read_provider_bars(conn, window, granularity, on_batch)` replaces `tick_reads.read_bars` everywhere. It has the same signature and returns rows in the same `BAR_COLUMNS` order, so `read_series`, the models and `get`'s writers do not change shape.

- **Window.** `instrument_id = :id AND ts_event BETWEEN :open_ns AND :close_ns`, the closed session interval ingest used to place each bar (`SessionIndex.locate_ns`). The served session of a bar is therefore always the session the ledger counted it in.
- **1m.** The stored rows, in `ts_event` order. The read is an index-prefix scan on `(instrument_id, ts_event)`.
- **Coarser sizes.** `bucket = open_ns + ((ts_event − open_ns) / width_ns) * width_ns` (integer division), anchored at the session open (D5). Open is the first bar's open by `ts_event`, close the last bar's close, high the max, low the min, volume the sum. `1d` is a single bucket stamped at the open. Empty buckets are omitted, as today.
- **Source map.** `bar_constants.BAR_SOURCE: Mapping[Granularity, TickSchema]` maps every served size to `OHLCV_1M`. The response's `source` field is `BAR_SOURCE[granularity]`. `ohlcv-1s` will add `1s → OHLCV_1S` there and route the read to its own table, and nothing else will name the source.
- **One statement per window,** as ticks are read today. A product read over `TICK_ROLL_MAX_SESSIONS` (1,300) sessions issues 1,300 short statements. D10 measures a long read in Phase 7.

Rejected:

- *Continuous aggregates over `tick_ohlcv_1m`.* A cagg buckets on a fixed clock, not per session. It would need its own refresh policy on an integer-time hypertable. A full 1m session is about 1,380 rows, so per-request rollup is cheap.
- *Falling back to ticks where no provider bar is held.* The ADR forbids it (PM, 2026-11-01).

### D3 — Plans take the family they serve
Status: decided

`contract_plan` and `product_plan` gain one parameter, `family: SchemaFamily`. The verdict reader is derived from the family inside `get_plan`, so a family and a reader can never be passed in a mismatched pair:

- `_held` (contract default range) reads `FAMILY_LEDGER[family]` instead of always `tick_ingest_ledger`.
- `roll_inputs.held_session_bounds(conn, shape, family)` takes the family instead of hard-coding `TICK_TIER`. The schedule route and every existing caller pass `TICK_TIER`, so their behaviour is unchanged.
- `verdicts_for(family) -> VerdictReader` is the one place the reader is chosen: `session_verdicts` for `TICK_TIER` and `BAR_1M`, and a `ValueError` naming the family for any other (statistics plans arrive with issue #73). `_incomplete` calls it with the plan's family. Any other code that needs a family's verdicts (parity, D7) calls it too, so no caller pairs a family with a reader by hand.

| Caller | `family` |
|---|---|
| ticks routes, `get ticks`, parity | `TICK_TIER` |
| bars routes, `get <size>` | `BAR_1M` |

**Roll basis: bars and `/schedule` agree.** `family` changes only two things: the default range when `start`/`end` are omitted, and which verdicts fill `incomplete`. It never changes which contract a rule picks. `/bars`, `/ticks` and `/schedule` all call the same `build_schedule`, and inside it the basis of each session is fixed by 303's per-session rule (the tick tier when its session is complete, else `BAR_1M`, else unresolved; `statistics` for `n`). So for the same rule and session, `/bars` and `/schedule` name the same contract and the same `basis`, on mixed ranges too. Over a bar-only range, `/schedule` with explicit `start`/`end` resolves `v.0` on bar volume and `n.0` on open interest. Only its default range still comes from the tick ledger (`TICK_TIER`, unchanged), so a bar-only range must be asked for explicitly. A test asserts that `/schedule` and `/bars` give identical segments (contract and `basis`) for `v.0` over a bar-only window and over a mixed window.

- *Rejected: a separate bar plan module.* The contract resolution, schedule and windows are identical; only the ledger and the verdict differ.

### D4 — Granularities: same sizes, same names
Status: decided

The served sizes stay `1m, 5m, 15m, 1h, 4h, 1d`, under their current names `TICK_BAR_GRANULARITIES` and `TickBarGranularity`, so the published OpenAPI component does not change. "Tick bar" only becomes ambiguous when 308 adds N-trade bars, so the rename is deferred to 308 (issue #72).

### D5 — Intraday buckets anchored at the session open (issue #47)
Status: decided

Today's buckets are UTC-epoch-aligned, with the first bucket of a session relabelled to the open. Anchoring at `open_ns` (D2's formula) is the CME session model the architecture names for rollups, and it is what trading-engine asked for (issue #47, and the 4h decision recorded on issue #38).

- 1m, 5m, 15m and 1h are unchanged, because the 17:00 CT open falls on those boundaries in both DST states.
- 4h moves to 17:00, 21:00, 01:00, 05:00, 09:00 and 13:00 CT. The last bar runs from 13:00 to the close and is short. An early close cuts its bucket at the close (the window ends there).
- Tests cover a DST change and an early-close session from the calendar (Thanksgiving 2024's Friday, in the held range).

This changes 4h output for any consumer, and the CHANGELOG says so. No consumer calls the futures routes yet (architecture, Current State). Issue #47 is closed by this slice's commit.

### D6 — Statistics are not served in this slice
Status: decided

The plan entry allows "routes, or an explicit recorded decision not to serve them". This is the recorded decision: `statistics` (settlement, cleared volume, open interest) gets no route in 306. No consumer has asked for open interest or settlement over the API. A route would add response models, a pivot module, a route file and a filter in 303's `resolved` for no current reader. The values stay readable to operators through `mt data tick coverage --schema statistics`, and the roll rule `n.0` already uses them. The contract and product route designs drafted for this slice are recorded in issue #73, to be built when a consumer asks.

### D7 — `mt data tick parity`: tick-derived against provider minutes
Status: decided

This is the only surviving use of tick-derived time bars (ADR point 1).

- **Arguments:** as `mt data tick get`: `--contract SYM` or `--product P [--rule R]`, `--start`, `--end` (session dates, end exclusive), `--json`.
- **Sessions compared:** those whose `TICK_TIER` and `BAR_1M` verdicts are both `complete`. The plan (`family=TICK_TIER`) supplies the tick verdicts. A second call, `verdicts_for(BAR_1M, calendar_url)` over the plan's sessions in the same snapshot, supplies the bar verdicts, joined by session date. Every other planned session is listed as skipped, with both statuses. That way a partial load is never reported as disagreement.
- **Per window:** `bar_parity.read_tick_minute_bars` (today's `_BARS` SQL at width 60 s with a session-anchored bucket; open and close by full key order `ts_event, sequence, sequence_ordinal`) and `read_provider_bars(…, M1)`, joined by bucket. Each minute is classed as `agree` (all of OHLCV equal), `differ` (the fields that differ are named), `provider_only` or `tick_only`.
- **Close-stamped minutes (issue #70):** these get no report section of their own. Each minute record carries `at_close` (bucket = the session's `close_ns`), the `--json` minute list includes every close-stamped minute whatever its class, and the per-session counts include close-stamped minutes and how many agree. The tick side of such a minute holds only trades stamped exactly at the close. A held tick unit in which a trade fell after the close would have failed ingest with `session_boundary`, so any volume the provider bar has beyond the ticks is the evidence that the closed-interval rule moves trades out of their minute.
- **Output:** the text form gives per-session minute counts by class, the close-stamped count and volume totals for both sides. `--json` adds the minute list: every non-agreeing minute plus every close-stamped minute. The text form lists no minutes, so it has no truncation rule.
- **Exit:** 0 when every compared minute agrees, 3 when any does not (a finding, as `coverage` uses 3). Nothing is written.
- **Failures:** the whole run reads in one `REPEATABLE READ READ ONLY` snapshot, as `get` does, and the report is built in memory (per-session counts and the listed minutes are small) and printed only after the last session has been compared. A failed run therefore prints no report, so exit 3 always means a finished comparison with findings, never an aborted one. The mapping goes through `tick_exit.exit_code_for`:
  - an unknown or ambiguous contract, a bad range or a calendar refusal: exit 1 (`EXIT_PREFLIGHT`);
  - an unreachable tick database, a dropped connection or a statement timeout (`QueryCanceled` is an `OperationalError`) at any point: exit 4 (`EXIT_STORAGE`), with the error's first line on stderr.
  The command is read-only, so any failed run can be re-run as it is. The parity connection uses the CLI's normal tick session settings, and no API statement timeout applies.

Rejected:

- *Parity at every size.* Coarser sizes are rollups of the same minutes by the same rule. A minute that agrees makes every bucket containing it agree.
- *A scheduled parity run.* This is a measurement for the windows where both tiers are held, not a standing check. 305 owns standing checks.

### D8 — `mt data tick get` follows the API
Status: decided

`get <size>` plans with `family=BAR_1M` and reads `read_provider_bars`, sharing the API's read path. Its JSON output gains `"source"`, and its text/CSV header line names the source. `get ticks` is unchanged. Exit 2 (partial) still means unresolved or incomplete sessions. For bars, "incomplete" now means the bar family, so a range with ticks and no provider bars exits 2 with every session listed, and prints no rows.

### D9 — Fixtures and the first parity measurement: no purchase
Status: decided

This slice buys nothing. The architecture's spend statement and the slice plan entry stay as they are.

- **Reader and rollup tests** use the DBN project's `ohlcv-1m` samples (decoding, price scale, bar-open `ts_event`) and the derived 2024-12-03 bar day. That day has real instruments, both sides of the daily break and a full session of minutes. Rollup expectations are computed in the test from the fixture's own 1m rows.
- **Parity tests** ingest the cut 2024-12-03 `tbbo` fixture and the derived bar day. The test computes each minute's expected class with an independent Python bucketing of the fixture's trades, set against the derived bars. Minutes the cut kept whole come out `agree`, the rest `differ` or `provider_only`, so every class the command reports is exercised on real records. `at_close` is tested on the session close inside that day.
- **First measurement (Phase 7, after release):** once 304's ES 2024 bars are ingested, `sudo mt-run data tick parity --product ES --start 2024-08-30 --end 2025-01-01 --json` runs over the whole held ES tick window. The result (minute counts by class and the `--json` minute list) goes in `user/notes/306-bar-parity.md`, and issue #70 is closed with it. This is the only provider-against-tick measurement, so Phase 6 does not depend on 304.

### D10 — Admission bounds stay as they are, and one is measured
Status: decided

- Bars and ticks share `MT_API_MAX_BARS_PER_REQUEST` (75,000 rows). A full ES 1m session is about 1,380 bars, so one request holds about 54 sessions of 1m and up to 1,300 sessions of 1d (the roll schedule's cap).
- Phase 7 measures one long coarse read on real data, with no generated test data: product `1d` bars over the whole held ES 2024 year (about 259 sessions), through the API in production. The bound is that the whole read completes in under 10 s, half of the API's per-statement timeout (`API_SERVING_SESSION.statement_timeout`, 20 s, operator setting `MT_API_STATEMENT_TIMEOUT`). One statement per session is far below 20 s, so the bound is on the whole request, which is what a client waits for. The result is recorded in the parity note, with the per-session time extrapolated to 1,300 sessions. Fallback if it fails: batch the windows into one statement.

## Implementation Details

### API Contracts

**Bars (changed):** `GET /api/v1/futures/{contracts/{symbol} | products/{product}}/bars`

- Parameters unchanged: `granularity` (`1m|5m|15m|1h|4h|1d`), `start`, `end` (exclusive), `rule` (product), `format`.
- Response gains `source` (string, the provider schema the bars came from, today always `"ohlcv-1m"`). `incomplete[]` now carries `BAR_1M` verdicts. `bars[]` fields are unchanged: `time` is the bucket start (session-anchored, D5).
- Behaviour change: a session with ticks and no provider bars returns no bars and is listed in `incomplete`.

```json
{
  "target": {"kind": "product", "product": "ES", "rule": "v.0", "is_default": true, "...": "..."},
  "granularity": "4h", "source": "ohlcv-1m",
  "start": "2024-12-02", "end": "2024-12-04", "definitions_as_of": "2024-12-03",
  "segments": [{"first_session": "2024-12-02", "last_session": "2024-12-03",
                "raw_symbol": "ESZ4", "instrument_id": 0, "basis": "bar_1m"}],
  "unresolved": [], "incomplete": [], "count": 12,
  "bars": [{"time": "2024-12-01T23:00:00.000000000Z", "session_date": "2024-12-02",
            "raw_symbol": "ESZ4", "instrument_id": 0,
            "open": "…", "high": "…", "low": "…", "close": "…", "volume": 0}]
}
```

(Values are shape only, elided with `…` and zeros.)

**Docs:** `reference.md` §6.1 (conventions: the bar-source rule, `source`, session-anchored buckets, "no provider bars, no bars", and the close-stamped stub bucket), §6.6/§6.8 rewritten. `agents.md` gets "Give me futures bars" (rewritten), with its `<!-- endpoint: -->` markers. `openapi.json` is regenerated by `scripts/dump_openapi.py`. `scripts/check_api_docs.py` and `test_openapi_artifact.py` pass.

### Migration Plan

The tick-derived bars are retired in one slice, with every consumer updated:

| Consumer of `read_bars` / `TICK_BAR_GRANULARITIES` | After |
|---|---|
| `routes/futures_series._bars_reader` | `read_provider_bars`, plan `family=BAR_1M` |
| `cli/commands/tick_get_cmds._stream` (bar sizes) | `read_provider_bars`, plan `family=BAR_1M` |
| tests of `read_bars` bucketing | rewritten against `read_provider_bars`. Tick-minute SQL tests move to `bar_parity` |

`tick_reads.py` keeps `read_ticks`, `TICK_COLUMNS`, `BAR_COLUMNS` and `TickWindow`. `_BARS`, `_INTRADAY_BUCKET`, `_SESSION_BUCKET` and `read_bars` are deleted. A grep for `read_bars` and `TICK_BAR_GRANULARITIES` returns nothing at the end of the slice.

Behaviour verification: response field names and the `BAR_COLUMNS` order are pinned by the existing model test. 1m/5m/15m/1h buckets on a regular session are unchanged (tested). The intentional changes are listed in the CHANGELOG: source, 4h anchoring, no bars without provider bars.

## Integration Points

### Provides to Other Slices

- **307:** whatever it buys is served at once. Bars need no further serving work, and `parity` can be run over any newly overlapping window.
- **308:** a bars path free of `tick_trade`, the session-window plan with a family parameter (tick bars plan with `TICK_TIER`), and the `source` field, whose values are provider schema names. 308 defines what its N-trade bars put there.
- **`ohlcv-1s` (future):** `BAR_SOURCE` is the one place `1s` joins. The reader routes `OHLCV_1S` to its table.
- **trading-engine / trading-ui:** provider bars under `/api/v1/futures/*`. Issues #47 (closed) and #62 (unchanged, 308).

### Consumes from Other Slices

- **302/303:** tables, ledgers, verdict readers, `resolved`. A session whose family is not complete is served with what is held and listed in `incomplete`. Nothing is filled from another family.
- **304 (Phase 7 only):** its ES 2024 bars, ingested in production, are what the first parity measurement (D9) runs on. Phase 6 does not depend on them.
- **305 (independent):** both slices edit `reference.md` and `openapi.json`. Whichever merges second regenerates the OpenAPI file.

## Success Criteria

### Functional Requirements

1. `GET …/bars` at every size reads only `tick_ohlcv_1m`. A test with ticks and no provider bars for a session returns `count: 0` and lists the session in `incomplete` with its `BAR_1M` status.
2. 1m bars equal the stored provider rows for the window. 5m–1d equal the rollup of those rows: first open, max high, min low, last close, summed volume. This is proven on the derived 2024-12-03 bar day and the DBN samples.
3. 4h buckets start at 17:00, 21:00, 01:00, 05:00, 09:00 and 13:00 CT across a DST change. The last bucket of an early-close session holds no bar after the early close. 1m/5m/15m/1h buckets on a regular session match today's.
4. Every bars response carries `source: "ohlcv-1m"`, taken from `BAR_SOURCE`.
5. `mt data tick get 1h --product ES …` returns the same rows as the API for the same arguments, with `source` in the JSON output.
6. D6's decision not to serve statistics is recorded in the design and in issue #73.
7. `mt data tick parity` compares only sessions complete in both tiers, classes every minute, marks close-stamped minutes and exits 0/3 as D7 states. Proven on the cut `tbbo` fixture against the derived bar day (D9).
8. `mt data tick parity` and the note template `user/notes/306-bar-parity.md` exist. The production measurement fills the note in Phase 7 (D9).
9. Provider 1h/1d bucketing is recorded (D1, and carried into the note).

### Technical Requirements

10. `read_bars`, `_BARS`, `_INTRADAY_BUCKET`, `_SESSION_BUCKET` and `TICK_BAR_GRANULARITIES` no longer exist (grep is empty).
11. ruff format/lint (scoped to touched files), mypy and the unit and integration tiers for the touched areas pass. New files stay under about 300 lines.

### Integration Requirements

12. `docs/api/reference.md`, `docs/api/agents.md` and `docs/api/openapi.json` describe the bar-source rule, `source`, and session-anchored buckets. `check_api_docs.py`, `test_api_docs.py` and `test_openapi_artifact.py` pass. The 200 schemas of both bars routes are published.
13. The CHANGELOG lists the behaviour changes (source, 4h anchoring, no tick-built bars, and the close-stamped one-minute stub bucket below) and the new command. `reference.md` §6.1 states the stub bucket rule.

### Verification Walkthrough

The commands exist after Phase 6. They run in production after the release (Phase 7), once 304's purchase is ingested: ES bars for 2024. Before then, the integration tests run the same paths on the fixtures.

1. **Provider bars, as stored and rolled up.**
   ```bash
   sudo mt-run data tick get 1m --product ES --start 2024-12-03 --end 2024-12-04 --json | jq '.source, .count'
   # "ohlcv-1m", ~1,380 (one full session of the front month)
   sudo mt-run data tick get 4h --product ES --start 2024-12-03 --end 2024-12-04 --json | jq -r '.bars[].time'
   # six bucket starts: 23:00Z, 03:00Z, 07:00Z, 11:00Z, 15:00Z, 19:00Z (17:00 … 13:00 CT, standard time)
   ```
2. **No provider bars means no bars.** GC has ticks for 2024-11-04 onward and no bars until 307:
   ```bash
   sudo mt-run data tick get 1h --product GC --start 2024-11-04 --end 2024-11-06; echo "exit $?"
   # no rows, both sessions listed incomplete (bar_1m status), exit 2
   ```
3. **The API says the same.**
   ```bash
   curl -s 'localhost:8100/api/v1/futures/products/ES/bars?granularity=1d&start=2024-12-03&end=2024-12-04' | jq '{source, count, bars: [.bars[] | {time, volume}]}'
   curl -s 'localhost:8100/api/v1/futures/products/GC/bars?granularity=1d&start=2024-11-04&end=2024-11-06' | jq '.count, .incomplete'
   # GC: count 0, both sessions incomplete
   time curl -s -o /dev/null 'localhost:8100/api/v1/futures/products/ES/bars?granularity=1d&start=2024-01-02&end=2025-01-01'
   # D10: under 10 s; recorded in the parity note
   ```
4. **Parity over the held ES tick window (fills the note, closes #70).**
   ```bash
   sudo mt-run data tick parity --product ES --start 2024-08-30 --end 2025-01-01 --json > ~/306-parity-prod.json; echo "exit $?"
   # per session: agree / differ / provider_only / tick_only counts, close-stamped counts; exit 0 if all agree, 3 otherwise
   ```
   The result goes in `user/notes/306-bar-parity.md`, and issue #70 is closed with it.
5. **Docs.** `uv run python scripts/check_api_docs.py` passes. `docs/api/openapi.json` shows `source` on `FuturesBarsResponse`.

## Risk Assessment

### Technical Risks

- **Provider and tick minutes may not agree.** Databento's bar construction (which trade records count, spread handling) is not documented in a form we can read. Disagreement is not a defect of this slice. It is the measurement the architecture asks for.
- **Long coarse reads.** One statement per session over 1,300 sessions is the first read of that length on this path.

### Mitigation Strategies

- Parity findings are recorded in the note and never patched in either tier. If provider bars systematically differ, the PM decides from the note whether the source rule needs revisiting.
- D10's measurement runs on the first real year of bars.

## Implementation Notes

### Development Approach

Suggested order:

1. **Plan family parameter (D3)** with `held_session_bounds`. Existing tick tests must stay green unchanged.
2. **`bar_reads` and `BAR_SOURCE` (D2, D5).**
3. **Switch the bars routes and `get` (D8),** and delete the tick-derived bar code (Migration Plan).
4. **`bar_parity` and `mt data tick parity` (D7),** and the note template.
5. **Docs, OpenAPI regeneration, CHANGELOG.**

Commit at each step's end. Testing: unit tests for the bucket formula and minute classification. Integration tests (tick test cluster) for the bar reader and the parity command on the real fixtures. API tests through the app for status codes, `source` and the published schemas.

### Special Considerations

- **Close-stamped minutes.** A provider bar stamped exactly at the session close is served in that session, as ingest placed it. When the session length is a multiple of the bucket width (a regular 23-hour session at 5m, 15m and 1h), it lands alone in a bucket stamped at the close: a one-minute stub bar. The CHANGELOG and `reference.md` §6.1 state this, so consumers do not read the stub as a data fault. Whether the closed-interval rule is right is issue #70's question, answered by D7. A rule change would be a fix to `locate_ns` that moves ingest and serving together, not part of this slice.
- **`TICK_ROLL_MAX_SESSIONS` (1,300)** still bounds product reads. Sixteen years of 1d bars need several requests. It is unchanged here.
- **Production database.** Phase 6 uses only the test cluster's fixture databases. Production is read only, through `sudo mt-run` and the served API, in Phase 7.
