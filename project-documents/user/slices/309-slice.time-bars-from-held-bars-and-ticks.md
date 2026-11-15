---
docType: slice-design
slice: time-bars-from-held-bars-and-ticks
project: trading-data
parent: user/architecture/300-slices.futures-historical-bars-statistics-and-tick-aggregation.md
dependencies:
- 302
- 308
interfaces: []
dateCreated: 20261115
dateUpdated: 20261115
status: not_started
---

# Slice Design: Time Bars from Held Bars and Ticks

## Overview

Futures time bars are broken in production. Since release 0.27.0 the routes `/api/v1/futures/contracts/{symbol}/bars` and `/api/v1/futures/products/{product}/bars` serve bars only from Databento's one-minute bars. Production holds none of those, so both routes return zero bars for ES and for GC, although production holds 27.7 million ES and 4.0 million GC tick records.

This slice applies the rule the PM set on 2026-11-15 (journal 20261115):

1. Raw ticks and bars of N trades always come from ticks. Nothing changes there.
2. Time bars come from stored provider bars where we hold ones that can make the size asked for.
3. Everywhere else, time bars are cut from ticks. That is slower.
4. The response says which source it used.

Today rule 2 never applies, because no provider bars are held. So after this slice every futures time bar is cut from ticks, and the trading-ui and trading-engine projects can build and test on the ticks we hold: ES for 2024-08-30 to 09-29 and 2024-11-01 to 12-31, GC for 2024-11-04 to 12-31.

## Value

- **trading-ui** can draw futures time-bar charts. This is the one thing blocking its futures display work.
- **trading-engine** gets time bars at any size, including the 1-second bars it asked for (issue #40), and can see every parameter the server applied (issue #77).
- **Later, with the Databento Standard plan:** the same routes serve provider bars back to 2010 with no change for the consumer, because the rule already says to use them where held.

## What changes for a consumer

### The request

`granularity` takes any bar size, not a menu of six:

- a whole number of seconds, minutes or hours: `1s`, `15s`, `1m`, `5m`, `90m`, `4h`;
- `1d`, which is one bar per CME session (not 24 hours).

The six sizes served today (`1m`, `5m`, `15m`, `1h`, `4h`, `1d`) keep their spelling, so an existing request does not change. A size longer than 24 hours, a zero, or an unknown unit is refused with a `422` that names the accepted form. If performance, storage or bandwidth later give a reason, the sizes may be limited to a predefined set (journal 20261115, decision 3).

Everything else in the request is unchanged: `start`, `end`, `rule`, `format`.

### The response

Two changes to the body:

- **`source` (one string) becomes `sources` (a list).** One request can cross sessions served from different sources, so one string cannot be right. Each entry is `{first_session, last_session, source}` and the entries cover every session that returned bars. `source` is `ticks` or the provider schema used (`ohlcv-1m`, later `ohlcv-1s`).
- **`applied` is added** (issue #77): every parameter the server used, with the value used, including the ones the caller left out and the server defaulted. For example `{"granularity": "15s", "rule": "v.0", "start": "2024-12-02", "end": "2024-12-03", "format": "json"}`.

`source` becoming `sources` breaks any reader of the old field. No version change is needed: the futures bars routes return nothing in production today, trading-ui has confirmed it has not coded against them, and both projects are told the new shape when it is published (success criterion 8).

Bars keep their fields (`time`, session, contract, open, high, low, close, volume). Intraday bars still start at the session open and count from there, so a 4-hour bar starts at 17:00, 21:00, 01:00 Chicago time and so on (issue #47, kept from slice 306). A bar never crosses a session. An empty interval has no bar.

A session with neither ticks nor usable provider bars returns no bars and is listed in `incomplete` with the reason, as today.

`mt data tick get <size>` follows the same rule and prints the same sources.

## How the source is chosen

The read already works one session and one contract at a time. For each of those:

1. **Provider bars**, if that session is held complete in a provider bar size that divides the size asked for. With one-minute bars held, that is any whole number of minutes or hours, and `1d`. A size in seconds cannot be made from one-minute bars.
2. **Ticks**, if rule 1 does not apply and the session's ticks are held.
3. **Nothing**, and the session is listed in `incomplete`.

**What "held" means.** A session is held in a kind of data when its status for that kind is `complete`: every archive file covering the session (a session spans two UTC days, so usually two files) is verified and loaded, and the loaded counts agree with the ledger. This is the session status the project already computes for ticks and for provider bars; nothing new is defined. It is the load check, not a judgement of the market data's content.

- A session only partly loaded in provider bars is not held there, and falls through to ticks.
- A session only partly loaded in ticks, with no usable provider bars, is still served from the ticks that are loaded and is listed in `incomplete` with its tick status (`in_flight`, `failed`, `provider_hole` and so on), as the tick routes do today. The consumer is told it is not whole, not left guessing.
- A session with no ticks loaded and no usable provider bars returns no bars and is listed in `incomplete` with status `missing`.

Provider bars are chosen first only because reading them is cheaper. Where both are held their agreement is a separate check (below), not something the read decides.

## Cutting time bars from ticks

One SQL statement per session and contract over the tick table: bucket each trade by `session open + whole intervals`, then first price, highest, lowest, last price and summed size per bucket. The first and last trade are picked by the full tick order (event time, sequence, position within the sequence), so ties are not ambiguous.

This statement already exists at one size: the minute comparison built in slice 306 computes tick-built minutes exactly this way. It is moved to its own module with the interval as a parameter, and the comparison uses it at one minute. So there is one implementation.

**Cost.** Cutting from ticks reads every trade in the range. A median ES session is about 393,000 trades. So a tick-cut request is admitted on the number of trades it would read, known from the ingest ledger before any tick is read, the same way bars of N trades are admitted. It has its own limit setting. The limit's default is set from a measurement on production in this slice, not guessed. A request over the limit is a `422` saying how many trades it would read and to narrow the range.

**When a tick-cut read goes wrong after it was admitted.** Admission bounds the expected cost, not the actual cost. The handling reuses what the futures routes already have:

- **A statement runs past the time budget.** Every API read runs under the serving statement timeout. A cancelled statement is the existing `504` with the "narrow the range" message. No partial body is sent.
- **The client disconnects mid-read.** The read checks for a disconnect between sessions and stops; the remaining sessions are not read. This is the bodiless `499` that bars of N trades already return.
- **The ledger has no trade count for a planned session, or the count is wrong.** A session with no ledger row is not `complete`, so it is listed in `incomplete` and adds nothing to the admission count. If a session returns more trades than its ledger says, the read fails with a logged `500` naming the session, because admission trusted a number that was false.

Stored aggregates (for example precomputed 30-second or 1-minute bars from ticks) are not built here. They are the next step if these reads are too slow for real use (journal 20261115, decision 7).

## What is removed

From slice 306, which is dead:

- The rule "no provider bars, no bars", in the reader, the routes and `mt data tick get`.
- The fixed table that says every size is built from `ohlcv-1m`.
- The tests that assert bars are never built from ticks.
- The same statements in `docs/api/reference.md`, `docs/api/agents.md`, `README.md` and the OpenAPI file. The OpenAPI file is regenerated.
- The command `mt data tick parity`. The comparison code and its tests stay (see below).

From the 2010 history work:

- The production configuration's history wants. After this slice production wants ticks and the definitions that go with them, and nothing else. The health, accounting and overview rows for bars and statistics disappear with the wants, because those rows are printed per want.

Kept from slice 306: bars anchored at the session open, the source label (now per session run), and the plumbing that tells a read which kind of data it is planning over. Slice 308's tick bars use that plumbing.

## The minute comparison

Comparing tick-built minutes with provider minutes is useful where both are held, and nobody should have to run it by hand. In this slice the command is removed and the comparison code stays, tested as now.

Running it automatically is not built in this slice. It is listed in the slice plan's Future Work as "Automatic minute comparison": the tick pass compares each session once when it becomes complete in both kinds of data, stores the counts, and `mt data health` reports them. It is taken up when provider bars are first held in production, which is when it has something to report. Until then the architecture's rule that agreement is measured by an automatic check is not met, and after this slice the comparison cannot be run by hand either; with no provider bars held, there is nothing it could compare.

## What this slice does not do

- It buys nothing and changes no spend limit.
- It does not add stored aggregates.
- It does not serve statistics (open interest, settlement). Those routes stay drafted in issue #73.
- It does not change the equity bars routes. Echoing applied parameters there (the rest of issue #77) goes with the equity aggregates work (issue #38), which is next.
- It does not touch bars of N trades or raw ticks.

## Documents and issues cleaned up in this slice

- Slice and task files for 302, 303, 304, 305 and 308: sentences that say time bars are provider-only, that ticks never make time bars, or that history to 2010 is to be bought. Facts about what was built stay.
- `CHANGELOG.md`: a new entry for this change. Released entries are history and are not rewritten.
- Issues closed: #69 (invoice audit of a purchase that never ran), #74 (bounds missed only at the scale of the 2010 purchase), #40 (1-second bars, answered here). Issues #70 and #71 stay open; they apply when provider bars are held.

## Success criteria

1. On production, `/products/ES/bars` and `/products/GC/bars` for 2024-12-02 return bars at `1h`, `1m`, `15s` and `1d`, with `sources` naming `ticks` and `applied` listing every parameter.
2. For one ES session, the one-minute bars cut from ticks equal the same session's trades summed by hand: same count of non-empty minutes, same open, high, low, close and volume. Checked in a test against real held ticks, not an invented fixture.
3. A test database that holds provider one-minute bars for a session serves `5m` from them (`sources` says `ohlcv-1m`) and `15s` for the same session from ticks.
4. A session with neither returns no bars and is listed in `incomplete`.
5. A tick-cut request over the trade limit is refused before any tick is read. The limit's default and the measured times (one ES session at `1m`; the widest range the default admits) are recorded in a note.
6. Bars of N trades and raw ticks return exactly what they returned before; their tests pass unchanged.
7. `mt data health` on production shows no bar or statistics rows and no new failure.
8. trading-ui and trading-engine are told the shape is published, with the document to read.

## Order of work

The cleanup of other slices' documents and the unrelated issues (step 5's last two items) goes in its own `docs:` commit, so the code change stays focused.


1. The tick-cut statement in its own module, any interval, with the test against real ticks.
2. The size parser and the source choice per session; `sources` and `applied` in the response.
3. Routes and `mt data tick get` on the new read; admission for tick-cut reads; the production measurement.
4. Remove the provider-only rule, its tests, the parity command and the history wants.
5. API documents, OpenAPI file, changelog, the other slice files, issues.
6. Release, deploy, check production against criteria 1 and 7, tell the two projects.
