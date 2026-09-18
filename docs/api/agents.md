---
docType: reference
title: Data Serving API — agent reference
project: trading-data
dateCreated: 20260916
dateUpdated: 20260916
status: complete
---

# Data Serving API — agent reference

For an LLM client choosing a route by the question it is trying to answer.

This document is **capability-indexed**: §2 lists questions, not routes. Each
entry names the route that answers it, the shape that comes back, and — the
part that matters most — the routes that look like they answer it and do not.
Every near-miss below returns `200` with a confidently wrong answer.

The **route-indexed** companion, with full field tables, units and executed
examples, is [`reference.md`](reference.md). This file does not repeat those
tables; it tells you which route to call and how to read what happens.

## 1. Orientation

- **Read-only.** Every route is `GET`. Nothing in this API mutates anything.
- **No authentication.** No API key, no token, no header.
- **Base URL**: all routes are prefixed `/api/v1`. The server default is
  `0.0.0.0:8100`.
- **Every response is JSON** unless you ask for msgpack with `format=msgpack`,
  which three routes accept. It saves 14–36% (see
  [`reference.md` §6](reference.md#6-msgpack-what-it-actually-saves)); prefer
  JSON unless bandwidth is the constraint.
- **All timestamps are UTC**, RFC 3339 with a `Z` suffix. Naive input is read
  as UTC.
- **Fields ending `_dollars` or `_fp` are decimal strings**, not numbers:
  `"0.4900"`. Parse into a decimal type, never a float.
- **Date windows are inclusive at both ends**, with one exception
  (`/api/v1/gaps`, whose day end is exclusive at the next midnight).
- **No pagination.** A request is answered completely or refused with `422`.

## 2. Capability index

### "Is the server up?"

`GET /api/v1/health` → `{status, db, coverage}`.

<!-- endpoint: /api/v1/health
     params: -
     errors: 200 -->

**Not this:** `/api/v1/overview` will also tell you the server is up, but it
does real database work to do it. Use `/health` for liveness.

### "Is anything running right now, and how far along?"

`GET /api/v1/overview` → `{now, passes[], sources[], health, universe}`.

<!-- endpoint: /api/v1/overview
     params: -
     errors: 504 -->

Each entry in `passes[]` has `running[]` (empty when idle), `last_run` with its
`outcome`, `next_firing`, and `cadence`.

**Not this: `/api/v1/status`.** This is the most common wrong turn in this API.
`/api/v1/status` reports **per-symbol data health** — whether stored bars are
complete — not whether a process is running. It will answer `200` with rows
about symbols and tell you nothing about what is executing. "Is it running" is
`/api/v1/overview`.

**Note:** `running[]` rows carry **no `abandoned` field**. A run whose process
died still appears in `running[]`. The API cannot tell — the recorded pid may
belong to another host. Do not infer liveness from `running[]` alone; check
`next_firing` and `last_run` too, or run `mt data overview` on the owning host.

### "Is my data fresh / complete for this symbol?"

`GET /api/v1/status?symbol=SPY&all=true` → per-symbol, per-granularity health
rows plus a whole-registry `summary` and a `coverage` verdict.

<!-- endpoint: /api/v1/status
     params: symbol:string, health:string, granularity:daily|minute,
             all:boolean
     errors: 422, 504 -->

**Pass `all=true`** unless you specifically want unhealthy rows. The default
filter returns only `GAPS`, `STALE` and `FAILED` entries, so a healthy symbol
answers `count: 0` — see §4.

**Not this: `/api/v1/health`.** Its `coverage` field is a coarse whole-system
verdict. It says nothing about your symbol, and it will say `ok` while the
symbol you care about has a year missing.

**Note:** this route's `granularity` takes `daily` or `minute` — the storage
family — **not** a granularity token like `1m`. See §7.

### "What bars do you have for this symbol?"

`GET /api/v1/bars/{symbol}?granularity=1d&start=…&end=…` →
`{symbol, granularity, adjusted, is_stale, count, bars[]}`.

<!-- endpoint: /api/v1/bars/{symbol}
     params: granularity:Granularity, start:date, end:date,
             adjusted:boolean, format:json|msgpack
     errors: 422, 504 -->

`start` and `end` are inclusive dates. Check `is_stale`: `true` means the
aggregate serving this grain is behind its source and the bars may be
incomplete. It is always `false` for `1m` and `1d`, which are raw — by
construction, not by luck.

### "Does this symbol exist? What range do you have?"

`GET /api/v1/symbols/{symbol}` → instrument metadata plus an `available` map of
granularity to `{start, end}`.

<!-- endpoint: /api/v1/symbols/{symbol}
     params: -
     errors: 422, 504 -->

This is the **existence check**: an unknown symbol is a `404` here. An empty
`available` means "known symbol, no bars".

`end` is exact. `start` is as of the last coverage materialization and can be
later than the true first bar, never earlier — do not treat it as a hard floor
when deciding what to request.

**Not this: `/api/v1/gaps/{symbol}`.** A symbol with no recorded gaps and a
symbol with perfect data both answer `count: 0`. It cannot tell you whether a
symbol exists.

### "What symbols are there?"

`GET /api/v1/symbols?search=SPY` → `{symbols[]}`.

<!-- endpoint: /api/v1/symbols
     params: search:string
     errors: 422, 504 -->

`search` is a ticker prefix. An unfiltered call may exceed the row ceiling —
if it does, you get a `422` telling you to narrow the filter.

### "Where is data missing for this symbol?"

`GET /api/v1/gaps/{symbol}?granularity=1m&start=…&end=…` → `{symbol, count, gaps[]}`.

<!-- endpoint: /api/v1/gaps/{symbol}
     params: granularity:Granularity, start:date, end:date
     errors: 422, 504 -->

Each gap carries `fetch_status`: `UNKNOWN` and `FAILED_RETRYABLE` are still
being retried; `PROVIDER_HOLE` (the provider says there is nothing there) and
`RETRY_EXHAUSTED` are terminal. A `PROVIDER_HOLE` is not a defect to wait out.

**Note:** this route's `end` is exclusive at the next midnight — the one
window-semantics exception in the API — and the response echoes the storage
**family** (`minute`), not the granularity you sent (`1m`).

### "What prediction markets exist?"

Four routes, in containment order. Start at the top unless you already have a
ticker.

`GET /api/v1/kalshi/categories` → every category with its series count.

<!-- endpoint: /api/v1/kalshi/categories
     params: -
     errors: 504 -->

**Start here.** Categories are free text Kalshi assigns, not a fixed
vocabulary, so this is the only way to learn what `category=` accepts below.
The counts double as a size map: a category with 390 series needs a `search`
filter, not a bare listing.

`GET /api/v1/kalshi/series?category=…&search=…` → series matching the filter.

<!-- endpoint: /api/v1/kalshi/series
     params: category:string, search:string
     errors: 422, 504 -->

14,092 series exist. An unfiltered call is close to the row ceiling; filter.

`GET /api/v1/kalshi/series/{ticker}` → one series.

<!-- endpoint: /api/v1/kalshi/series/{ticker}
     params: -
     errors: 422, 504 -->

`GET /api/v1/kalshi/series/{ticker}/events?strike_from=…&strike_to=…` → that
series' events, optionally bounded by strike date (both bounds inclusive,
full timestamps accepted).

<!-- endpoint: /api/v1/kalshi/series/{ticker}/events
     params: strike_from:date-time, strike_to:date-time
     errors: 422, 504 -->

`GET /api/v1/kalshi/events/{event_ticker}` → one event.

<!-- endpoint: /api/v1/kalshi/events/{event_ticker}
     params: -
     errors: 422, 504 -->

`GET /api/v1/kalshi/events/{event_ticker}/markets?status=…` → that event's
markets, optionally filtered by a comma-separated list of status tokens (§7).

<!-- endpoint: /api/v1/kalshi/events/{event_ticker}/markets
     params: status:string
     errors: 422, 504 -->

`GET /api/v1/kalshi/markets/{ticker}` → one market, with `lifecycle`,
`settlement` and `economics` groups.

<!-- endpoint: /api/v1/kalshi/markets/{ticker}
     params: -
     errors: 422, 504 -->

`settlement` is present on **every** market, with all five fields `null` until
it settles. An absent object and an unsettled market would otherwise be
indistinguishable. Check `settlement.result` for `yes`/`no`/`null`, not for the
presence of the object.

### "How did this market trade?"

`GET /api/v1/kalshi/markets/{ticker}/candlesticks?start=…&end=…` → periodic
bid/ask/price candles.

<!-- endpoint: /api/v1/kalshi/markets/{ticker}/candlesticks
     params: start:date|date-time, end:date|date-time, format:json|msgpack
     errors: 422, 504 -->

**Check `collected` before reading `count`.** `collected: false` means this
market is not in the candle collection set at all — no candles are stored for
it, ever. That is a different fact from an empty window.

`GET /api/v1/kalshi/markets/{ticker}/trades?start=…&end=…` → the trade tape.

<!-- endpoint: /api/v1/kalshi/markets/{ticker}/trades
     params: start:date|date-time, end:date|date-time, format:json|msgpack
     errors: 422, 504 -->

**Check `tape_filtered` before reading `count`.** `tape_filtered: true` means
this market's category is excluded from trade collection by policy, so an empty
window is expected rather than a gap.

### "How many provider credits are left?"

`GET /api/v1/credits` → `{credits: {used, daily_limit, extra, remaining}, error}`.

<!-- endpoint: /api/v1/credits
     params: -
     errors: - -->

**Always `200`.** An unset key or an unreachable provider arrives as `error`
text with `credits: null` — check `error` before reading `credits`. There is no
`504` and no error status: a provider that will not answer is a condition this
route reports, not a fault of the server.

**This is the one route that makes an outbound network call.** It is slower
than the rest and fails in ways a database read cannot. Do not poll it the way
you would poll `/api/v1/overview`.

## 3. Choosing a window

The ceiling is **75,000 rows**, shared by every list and time-series route.
<!-- from: manta_trading.constants.API_MAX_BARS_PER_REQUEST -->

It counts **rows, not bytes**, and there is no pagination: a request is
answered completely or refused with `422`. Exceeding it is not an error to
retry — it is an instruction to ask for less.

Maximum span per bars request, at the default ceiling:

| Granularity | Max span |
|---|---|
| `1m` | ~113 days |
| `5m` | ~565 days |
| `15m` | ~1,697 days |
| `1h` and coarser | effectively unbounded |

**Splitting a too-large request.** The `422` message names the estimate, the
ceiling and the maximum span for that granularity, so you can compute the split
without a second probe:

```
requested range spans about 3,167,495 1m bars, over the 75,000 bar limit;
at 1m request at most 113 days per call, or use a coarser granularity
```

Two valid responses: issue ⌈span ÷ 113 days⌉ sequential requests, or move to a
coarser granularity if you do not need minute resolution. A third — retrying
the same request — is never correct.

Equity bars are refused from an **estimate** computed before any database work.
Kalshi routes take an exact `count(*)` first and quote the real number.

## 4. Reading a result

**A `200` with `count: 0` is the normal case, not an error.** Weekends,
holidays, pre-listing dates and quiet markets all produce it. Never retry a
`count: 0`; nothing about it will change on a second identical call.

**Telling "no data" from "wrong identifier":**

| Observation | Means |
|---|---|
| `404` | The identifier is unknown. **Only** that. |
| `200`, `count: 0`, on `/api/v1/status` | **Nothing is wrong.** The default filter returns only unhealthy rows. Pass `all=true` to see healthy ones. |
| `200`, `count: 0`, `collected: false` | This market has no candles stored, ever. Not an empty window. |
| `200`, `count: 0`, `tape_filtered: true` | Trades for this category are excluded by policy. Expected, not a gap. |
| `200`, `count: 0`, window outside `coverage_from` … `complete_through` | You asked outside the stored range. |
| `200`, `count: 0`, none of the above | Genuinely no activity in that window. |
| `200`, empty `available` on `/api/v1/symbols/{symbol}` | Known symbol, no bars. |

`complete_through` is what was **requested and stored through**, not the newest
stored row — a quiet market produces no candle for a period it was nonetheless
asked for, so the two differ. `tape_complete_through: null` means the trades
phase has never run, not that the tape is empty.

## 5. Failure modes and retry verdicts

Every status this API can return, with an explicit verdict and the parameter
change that resolves it.

| Status | Retry? | What to do |
|---|---|---|
| `200` with `count: 0` | **No.** Nothing is wrong. | Read §4 — the response tells you which of the five meanings applies. Do not re-request. |
| `404` | **No.** The identifier is unknown. | Check spelling; use `/api/v1/symbols/{symbol}` or the Kalshi catalog routes to find a valid one. Retrying will never succeed. |
| `422` | **No. Retrying the same request cannot help.** | The request is malformed or asks for too much. **Narrow the window**, filter the list, fix the reversed range, or correct the invalid token. The message names what to change. |
| `500` | **Once, then stop.** | An unexpected server fault; the body is sanitized. If it repeats, the server needs attention — a client cannot fix it. |
| `504` | **Yes, retry may help.** | The database cancelled the query at the statement timeout. The same request may succeed under lighter load, but **narrowing the window is more reliable** than retrying unchanged. |

### What a `504` does not mean

`statement_timeout` bounds **a single statement**, not a request. A route that
issues several statements can run far past the timeout budget without any one
statement being cancelled: slice 186 measured a **95-second request under a
20-second budget**
([`180-arch.data-serving.md:291`](../../project-documents/user/architecture/180-arch.data-serving.md)).
Request latency is not enforced anywhere in this API.

Two consequences, stated so you do not have to infer them:

1. **A slow request is not a hung one.** It may still return. Set your own
   client timeout deliberately; do not assume the server will cut it off.
2. **The absence of a `504` is no evidence a request was fast.** A response
   that arrives is not a response that arrived within any budget.

### The two `422` shapes — parse defensively

**This will break a naively generated client.** `openapi.json` declares
`HTTPValidationError` for `422` on every route that has one:

```json
{"detail":[{"type":"enum","loc":["query","granularity"],"msg":"Input should be '1m', '5m', …","input":"2d"}]}
```

But **eight hand-written refusals send a different shape**:

```json
{"error":"requested range spans about 3,167,495 1m bars, over the 75,000 bar limit; at 1m request at most 113 days per call, or use a coarser granularity"}
```

Both come from the same route with the same status. Which you get:

| Condition | Shape |
|---|---|
| Unparseable date, unknown enum token, wrong scalar type | `{"detail": [...]}` — FastAPI's own validation |
| Reversed range, over the row ceiling, invalid status filter | `{"error": "…"}` — a hand-written refusal |

**Check for `error` first, then `detail`.** A client that parses only the
declared shape will fail to read the most actionable messages the API
produces — the ones that tell you exactly how to narrow your request.

This mismatch is documented rather than fixed, because fixing it changes a
published schema. The same is true of `404`: four route modules raise one and
**no path in the schema declares it**, so a generated client may not model it
at all. Both are recorded as Future Work.

## 6. Determinism and freshness

**Safe to poll:**

- `/api/v1/health` — trivial.
- `/api/v1/overview` — a **pure database read**. No outbound calls. This is why
  credits live on their own route.
- `/api/v1/status`, `/api/v1/bars`, `/api/v1/symbols`, `/api/v1/gaps`, and the
  Kalshi catalog and time-series routes — all database reads, though the wider
  ones cost real work. Poll on the timescale the data actually changes.

**Not safe to poll:** `/api/v1/credits` makes an outbound HTTPS call to the
provider. Treat it as expensive and cache what it tells you.

**What can be stale, and how you are told:**

| Route | Signal | Meaning |
|---|---|---|
| `/api/v1/bars/{symbol}` | `is_stale` | The continuous aggregate for this grain is behind its source; bars may be incomplete. Always `false` for `1m` and `1d` — they are raw, so no aggregate exists that could lag. |
| `/api/v1/health` | `coverage` | Coarse whole-system verdict. Not about your symbol. |
| `/api/v1/status` | `coverage.verdicts[]` | Per-view freshness with `lag_seconds`. **`null` is not `0.0`** — it means the lag could not be measured. |
| `/api/v1/kalshi/…/candlesticks` | `complete_through` | Requested and stored through this instant. |
| `/api/v1/kalshi/…/trades` | `tape_complete_through` | Created time of the newest stored trade; `null` means the phase never ran. |
| `/api/v1/overview` | `sources[].newest` | Newest row in each source table — the most direct freshness signal in the API. |

Nothing in this API is cached at the HTTP layer. Two identical requests one
second apart can legitimately differ if a pass wrote rows between them.

## 7. Vocabularies

Five closed token sets. Each is checked against the code that defines it, so
these lists cannot silently fall behind.

**Bar granularity** — `/api/v1/bars` and `/api/v1/gaps`:
`1m` `5m` `15m` `1h` `4h` `1d` `1w` `1mo` `1q`.
<!-- from: manta_trading.constants.Granularity -->

**Data health** — `/api/v1/status` `health` filter and `rows[].health`:
`OK` `GAPS` `STALE` `FAILED`.
<!-- from: manta_trading.cli.rendering.status_table.HealthStatus -->

`STALE` means "not attempted in the last recorded universe walk". `gap_count`
on the same row counts **open** gaps only — terminal ones are excluded, so the
number can reach zero.

**Market status** — the `/api/v1/kalshi/events/{event_ticker}/markets` filter
and `status` on every market record:
`active` `amended` `closed` `determined` `finalized` `inactive` `initialized`.
<!-- from: manta_trading.data.kalshi.constants.MarketStatus -->

An invalid token is a `422` naming both what you sent and the valid set.

**Pass kind** — `passes[].pass` on `/api/v1/overview`:
`accounting` `daily` `health` `kalshi` `minute`.
<!-- from: manta_trading.data.acquisition.pass_runs.PassKind -->

The field is serialized under the **alias** `pass` because `pass` is a Python
keyword. A generated Python client cannot expose it as an attribute; read it by
key.

**Run outcome** — `passes[].last_run.outcome` on `/api/v1/overview`:
`COMPLETE` `COMPLETE_QUOTA` `FAILED` `INCOMPLETE` `PROVIDER_UNAVAILABLE`.
<!-- from: manta_trading.data.acquisition.pass_runs.PassRunOutcome -->

`COMPLETE_QUOTA` means the pass finished by exhausting its provider quota, not
that it failed. `PROVIDER_UNAVAILABLE` means the provider did not answer.

**Storage family** — the `/api/v1/status` `granularity` parameter and the
`granularity` echoed by `/api/v1/gaps`: `daily` or `minute`. This is **not**
the bar granularity vocabulary above, and the two are not interchangeable:

| Route | Takes | Note |
|---|---|---|
| `/api/v1/bars/{symbol}` | a bar granularity | used directly |
| `/api/v1/gaps/{symbol}` | a bar granularity | mapped to its family; the response echoes the **family** |
| `/api/v1/status` | `daily` or `minute` | the family directly |

## 8. Keeping this accurate

This document is hand-written and checked by `scripts/check_api_docs.py`, which
verifies that every path here exists in the schema, that every documented
parameter and status matches it, and that each `<!-- from: -->` marker still
agrees with the symbol it names.

```sh
uv run python scripts/check_api_docs.py --check
```

The conventions — `<!-- endpoint: -->` blocks and `<!-- from: -->` markers —
are specified in
[`reference.md` §8](reference.md#8-keeping-this-accurate). Every one of the 17
paths must appear in **both** documents; the gate enforces it, because two
descriptions of one surface that can disagree is the condition these files
exist to end.

**What the gate cannot check is whether any sentence here is true** — including
every retry verdict in §5. Those are the reviewer's responsibility and the
verification walkthrough's.
