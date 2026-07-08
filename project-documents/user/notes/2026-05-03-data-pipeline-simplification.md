---
docType: notes
project: trading
dateCreated: 20260503
status: draft
---

# Data Pipeline Simplification Proposal

Companion: [2026-05-03-140-slice-impact.md](2026-05-03-140-slice-impact.md)

## Why this exists

A week of work to fetch minute data should have taken a day. The
existing 140 plan is correct in its mechanics but adds layers that
amplify the operator burden rather than reducing it. This doc proposes
a smaller, sharper system that does the same job with less code,
fewer moving parts, and meaningfully less operator attention.

The companion doc walks slice-by-slice through what stays, what
changes, and what should be deleted from the 140 plan.

---

## Current pain (what "useless" actually means)

Today, after slice 145 ships, you have:

- A `mt data daemon minute` command that runs **one cycle and exits**.
  One cycle = one chunk per symbol across the universe.
- A `mt data daemon daily` command, same shape.
- No looping wrapper. No quota awareness. No scheduled CA polling.
- CA ingestion is per-symbol manual (`mt data adjustment ingest --symbol X`).
- A 12,636-symbol minute backfill at 67 chunks/symbol = ~847,000 cycle
  invocations needed if you hand-loop, or ~3–5 weeks of unattended
  daemon if you don't.

What's broken about this is not any individual piece — each slice does
exactly what it claims. What's broken is that **nothing orchestrates
across cycles**. The system has correct primitives and zero glue.

---

## Proposal: four changes, in order of impact

### 1. Long-running daemon with quota-aware throttling (the big one)

Replace one-shot `run_daily_cycle()` / `run_minute_cycle()` with a
single long-running process:

```
mt data daemon run [--minute] [--daily] [--symbols X,Y,Z] [--list NAME]
                   [--max-credits N] [--stop-when-done | --forever]
```

**Termination defaults (the principle: scoped invocations exit; bare
invocations run forever):**

| Invocation | Default behavior |
|---|---|
| `mt data daemon run --symbols SPY` | runs until SPY is fully backfilled, then exits |
| `mt data daemon run --list priority1` | runs until priority1 list is fully backfilled, then exits |
| `mt data daemon run` | runs forever (the actual long-running daemon) |
| `mt data daemon run --max-credits 50000` | runs until budget exhausted, then exits |

Override flags: `--forever` forces continuous mode on a scoped run
(rare — e.g. continuously poll SPY for new bars). `--stop-when-done`
forces exit on a bare run (e.g. overnight universe catchup that
should exit when caught up).

Behaviors:

- **Continuous loop.** After one cycle finishes, immediately starts
  the next. No external scheduler needed.
- **Token-bucket throttling.** Tracks credits spent in the current
  rolling 60-second window against `EODHD_PER_MINUTE_BURST` (1000)
  and the rolling 24-hour window against `EODHD_DAILY_QUOTA` (100k).
  Sleeps to avoid 429s and to avoid blowing the daily ceiling.
- **Quota accounting per call type.** `EODHD_INTRADAY_CALL_COST = 5`,
  `EODHD_EOD_CALL_COST = 1`, `EODHD_BULK_EOD_BASE_COST = 100`. All
  in `manta_trading.constants` already (added 2026-05-03).
- **Graceful shutdown.** `SIGTERM` finishes the current symbol, then
  exits cleanly.
- **Per-cycle progress reporting.** Logs `symbols processed: 8721 /
  12636 — credits spent today: 84,200 / 100,000 — est. completion:
  18 days`.
- **Single-symbol fast path.** `mt data daemon run --minute --symbols
  SPY` finishes a 22-year SPY backfill in ~90s of API time (335
  credits) and exits. This is what makes the system *usable* for
  testing today instead of after a 6-week backfill.

**Implementation cost:** small. The cycle functions already exist; we
need a loop with throttling and a clean shutdown handler. ~200 lines
of new code, ~1 day of work.

**Operator impact:** start the daemon once. Walk away. Come back when
backfill is done. Cron-like scheduling not required.

---

### 1a. Named symbol lists (priority backfill, watchlists, sector slices)

Don't make the operator wait 4 weeks to test against any data. Let
the operator define **named lists of symbols** the daemon can be
pointed at. Run lists in any order; the unscoped daemon grinds
through the rest in the background.

Lists are general-purpose: a "priority1" list for backfill ordering
is one use, but a "tech-megacap" list, a "sector-energy" list, or a
"my-strategy-test-set" list are equally valid. Lists are just named
collections of symbols, queryable by name.

**Suggested initial lists:**

| List | Symbols | Cost (22-yr worst) | Wall clock |
|---|---|---|---|
| **priority1** | ≤10 hand-picked (SPY, QQQ, AAPL, MSFT, NVDA, GOOGL, META, TSLA, AMZN, BRK-B) | ~3,400 credits | ~30 min |
| **priority2** | 500 (today's S&P 500 — frozen membership) | ~167k credits | ~1 day |
| **(unscoped)** | ~12,000 remaining | ~4M credits | ~3–5 weeks |

**Mechanism:**

```bash
mt data lists refresh-sp500               # 10 credits, refreshes config/lists/sp500-snapshot.txt
mt data daemon run --list priority1       # ~30 min, exits when done
mt data daemon run --list priority2       # ~1 day, exits when done
mt data daemon run                        # unscoped universe, runs forever
```

The daemon iterates the list in some order (membership order or
alphabetical — implementation detail) and exits when no actionable
gaps remain for any symbol in the list.

**SP500 source (verified 2026-05-03 against EODHD docs):**
- Endpoint: `GET /fundamentals/GSPC.INDX`
- Returns `Components` array with code, exchange, name, sector,
  industry, weight per member.
- Cost: **10 credits per refresh** (not per symbol).
- `?historical=1` returns historical membership snapshots back to
  the 1960s — useful later for properly-rebalanced backtests.
- Manual override always available: just edit
  `config/lists/sp500-snapshot.txt`.

**List file format:**
```yaml
# config/symbol-lists.yaml
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

**List management commands:**
- `mt data lists ls` — show defined lists with symbol counts
- `mt data lists show NAME` — print the resolved symbol list
- `mt data lists refresh-sp500` — refresh the SP500 source file

**Implementation:** lists live in a config file (or a `symbol_lists`
table). `--list NAME` resolves to a symbol set that filters the
daemon's `iter_active_instruments` query via `WHERE symbol = ANY(...)`.
No `priority_tier` column on `instruments` — that would conflate
"membership in a list the operator named" with a property of the
instrument itself. Lists are operator state, not instrument state.

**With +500k bucket:** priority1 + priority2 both done day 1 with
~330k credits left over (= a 3-day head start on the unscoped run).

---

### 2. Corporate-actions update via bulk endpoint

CAs are rare (a few hundred ex-dates per day across the entire US
exchange) and immutable once published. Polling them per-symbol
per-cycle is structurally wrong; one bulk call per day covers the
whole exchange.

**Commands:**

```
mt data ca update [--since DAYS_OR_DATE] [--symbol SYMBOL | --list NAME]
mt data ca show --symbol SYMBOL [--from DATE] [--to DATE]
mt data ca list [--from DATE] [--to DATE]
```

`ca update` shapes:

| Invocation | What it does | Cost |
|---|---|---|
| `mt data ca update` | **Default:** bulk-fetch yesterday's splits + dividends across the entire exchange. The daily steady-state call. | 200 credits |
| `mt data ca update --since 7` | Bulk-fetch trailing 7 days (catchup after daemon downtime). Per-day API; cost scales with N. | 1400 credits (200 × 7) |
| `mt data ca update --since 2026-04-25` | Bulk-fetch from given date through yesterday, per-day | 200 × N days |
| `mt data ca update --symbol AAPL` | Per-symbol full CA history backfill via `/splits/AAPL` + `/div/AAPL` | 2 credits |
| `mt data ca update --symbol AAPL --since 2024-01-01` | Per-symbol fetch with client-side post-filter on ex_date | 2 credits |
| `mt data ca update --list priority1` | Per-symbol full backfill for each list member | 2 × N symbols |
| `mt data ca update --list priority2` | Per-symbol full backfill for the SP500 list (≈500 symbols) | ~1000 credits |

**The default (no flags) is the right thing.** Daily operator action
= "make sure CAs are current" = bulk yesterday call = 200 credits,
full exchange coverage. No `--symbol` requirement; bare invocation
does the most common safe action.

**`--symbol` and `--list` are mutually exclusive.** Either modifies
the path from "bulk-by-day" to "per-symbol-by-symbol." `--since`
modifies whichever path is in effect (bulk: per-day range;
per-symbol: client-side post-filter on the returned ex_dates).

**No `--type` flag.** Splits and dividends always travel together —
both are 100 credits, both immutable, both should be current. The
implementation always does both calls. Adding the flag would invite
"oops I only updated splits" partial-state bugs.

**No `--date YYYY-MM-DD` for one specific historical date.** No
operator workflow wants a single-day backwards-in-time update.
`--since` covers the real need (catchup ranges); per-symbol covers
single-symbol backfills. Add specific-date if a workflow ever
materializes for it.

**When to use which path:**
- **Bulk (no scope flag, optionally `--since`):** for the entire
  exchange's recent history. Cheapest per symbol when you need broad
  coverage of a recent window.
- **Per-symbol (`--symbol X`):** when you need full CA history for
  one symbol (newly-tracked, suspected-stale, audit follow-up).
- **Per-list (`--list NAME`):** when you've defined a new list and
  need its members' full CA history loaded. SP500 = ~1000 credits,
  cheaper than running per-symbol 500 times by hand.

**Verified pricing (EODHD docs, 2026-05-03):**
> "the data for last trading day will be downloaded" for the entire
> specified exchange... "The 'symbols' parameter does not work for
> splits and dividends" — bulk CA calls cover the entire exchange
> at 100 credits flat.

**Replaces `mt data adjustment ingest`.** Verified
2026-05-03: the legacy command takes `--symbol X [--since
YYYY-MM-DD]` and does per-symbol fetch+upsert. `ca update --symbol X
[--since DATE]` covers it identically. **Delete the legacy command
when slice 146 ships** — no behavior gap. The `adjustment` Typer
sub-app loses its only command and can be removed entirely.

**How it integrates:** `mt data ca update` (no args) runs once per
UTC day. Could be triggered by:
- A cron / systemd timer (simplest, decoupled from the daemon)
- An inline once-per-day guarded action inside the long-running
  daemon's main loop (one less moving part to schedule)

Either is fine. Pick whichever has lower operational surface area
when implementing slice 146.

When new CAs land in `splits`/`dividends`, slice 146's snapshot-drift
detection (or the simpler `k_factor` recompute under proposal #3)
fires on the next daemon cycle and updates affected ranges.

**Implementation cost:** trivial. Three sub-commands, mostly DB I/O
and two HTTP calls in `ca update`. ~150 lines including the CLI.

---

### 3. ~~Reconsider `adj_*` storage — compute on read instead~~ — **WITHDRAWN**

This proposal was: drop stored `adj_*` columns, compute adjusted
prices at read time as `close * k_factor`.

**Why withdrawn (verified 2026-05-03 against actual cagg structure):**

Each minute bar carries its own `k_factor` because the factor changes
at every ex-date. Computing `adj_close = close * k_factor` per bar
is essentially free at read time — but **caggs cannot work this way**.

A 15-min cagg of raw `MAX(high)` over five source minute bars cannot
be multiplied by a single k_factor afterwards: bars in different
ex-date bands would carry different k_factors, and aggregating raw
then multiplying gives the wrong adjustment whenever the window
crosses an ex-date.

Correct cagg math requires aggregating values that are already
adjusted at the source-bar grain (`MAX(adj_high)`, etc.). That's
exactly what slice 150 plans, and it requires `adj_*` to be stored
on the source rows so the cagg has something to aggregate.

**The current architecture is right at the cagg boundary.** Storing
`adj_*` per minute bar is what makes correct adjusted-price caggs
possible. The proposed simplification breaks at the cagg layer with
no clean workaround.

**Kept from this thinking:**
- The 5-piece "CA pipeline" framing was misleading — most of those
  pieces are one mechanism described from different angles in
  different slices. The mechanism itself (per-bar `k_factor` +
  per-bar `adj_*` + band UPDATE on CA change + cagg refresh of
  affected ranges) is correct.
- Slice 150's "rebuild caggs as adjusted" remains right.
- Slice 145's `band_writer.py` remains right.
- The CA-drift detection mechanism (snapshot_id check per cycle)
  remains right.

**What WAS worth simplifying** is the operator surface (proposals #1,
#1a, #2, #4) and the CA polling mechanism (#2). The internal
adjustment machinery is fine.

---

### 4. Backtest dir + MarketDB cleanup (slice 151)

There is no real strategy backtest in the project. `src/manta_trading/
backtest/` is exploratory scaffolding that reads MarketDB, which
nothing load-bearing depends on. It causes "but what about the
backtest?" confusion in every architecture conversation.

**Slice 151 scope:**
- Delete `src/manta_trading/backtest/` entirely.
- Audit and delete dead MarketDB readers in
  `cli/commands/data.py`, `data/acquisition/daily/orchestrator.py`,
  `data/acquisition/daily/writer.py`.
- Decide MarketDB fate: drop entirely, or keep only what `news/`
  depends on.
- Don't touch `news/` unless mechanically required.
- Delete legacy `mt data daily update` / `mt data minute update` /
  `mt data minute backfill` commands that wrote to MarketDB.
- Tests and imports cleaned up.

**Effort:** 1/5. Mostly deletion.

---

## How "useful" the system gets, by milestone

| Milestone | What you can do |
|---|---|
| Today (post-145) | Single-cycle invocations. SPY backfill = 67 manual invocations. Universe backfill needs hand-orchestration. CAs require manual `mt data adjustment ingest`. |
| +long-running daemon (#1) | `mt data daemon run --symbols SPY` → SPY done in 90s, exits. `mt data daemon run` (no args) → universe, ~3–5 weeks unattended. |
| +named lists (#1a) | `--list priority1` → top 10 done in 30 min. `--list priority2` → SP500 in ~1 day. Universe in background. Test/develop against real data starting day 1. Lists are reusable for any symbol grouping (sectors, watchlists, test sets). |
| +bulk CA (#2) | CAs auto-current. No per-symbol CA poll. 200 credits/day for the entire universe. |
| ~~+`adj_*` simplification (#3)~~ | **Withdrawn — breaks cagg correctness across ex-date boundaries.** |
| +backtest cleanup (#4) | "What about the backtest" stops being a question. |

---

## Quota math (verified 2026-05-03 against EODHD docs)

**Plan ceilings:**
- 1,000 credits/minute burst
- 100,000 credits/day rolling

**Call costs:**
- `/intraday` (1-min, ≤120-day window): 5 credits
- `/eod` (per-symbol daily history): 1 credit
- `/eod-bulk-last-day/US` (full exchange, no symbols filter): 100 credits flat
- `/eod-bulk-last-day/US?symbols=A,B,C`: 100 + 1 per symbol
- `/eod-bulk-last-day/US?type=splits|dividends`: 100 credits flat (symbols filter unsupported for these types)

**Universe scale (12,636 minute-eligible active instruments,
verified live query 2026-05-03):**
- Full minute backfill (22-year worst case): 12,636 × 67 × 5 = **4.23M credits = 42 days**
- Realistic average history (~10–12 yrs): **~3–5 weeks**
- Steady-state daily minute updates: 12,636 × 5 = **63k credits/day** (63% of quota)
- Steady-state daily EOD (per-symbol): 12,636 × 1 = 12,636 credits/day
- Steady-state daily EOD (bulk): **100 credits/day** (120× cheaper)
- Steady-state CA polling: **200 credits/day**

**With +500k one-time bucket:**
- Universe backfill: 38 days (vs 42). 4-day improvement. Marginal.
- Top 1500 priority symbols full history: **all of them in ~1 day**, no quota dent. Meaningful for testing.
- Top 500 most-liquid symbols at 22-year worst: 167k credits = bucket covers it with 333k to spare.

Recommendation on the bucket: useful only if you want priority symbols
done immediately for strategy testing. Skip if the goal is just
universe completion.

---

## Per-invocation timing breakdown (single chunk, single symbol)

| Phase | Time |
|---|---|
| Process start (Python+import) | 0.5–1s cold (one-time per invocation; amortized in long-running daemon) |
| DB connection setup | 0.1s |
| Symbol selection query | <0.1s |
| Per-symbol: advisory lock + gap query | 0.05s |
| Per-symbol: EODHD `/intraday` call | **~1.3s** (paid tier, 76k bars) |
| Per-symbol: bulk insert + band UPDATE | 0.5–1s |
| **Per-symbol total** | **~2–3s** |
| Cycle teardown | 0.1s |

Per-symbol API+write is ~2.5s. The 100k/day quota at 5 credits/intraday
allows 20,000 chunks/day = 14 hours of API work. **Bottleneck is the
quota, not the processing speed.** No parallelism needed.

---

## What this proposal does NOT change

- Slice 141 (universe rebuild from EODHD) — done, leave it.
- Slice 142 (schema migration, `data_gaps`) — done, leave it.
- Slice 143's `compute_k_factor` SSOT — keep, it's the basis for
  per-bar `k_factor` writes in slice 145.
- Slice 144 (`trading_sessions` materialization) — done, leave it.
- Slice 145's gap-driven backfill loop AND band-based `adj_*`
  writer — both keep. The band writer is what makes correct adjusted
  caggs possible.
- The `data_gaps` model and lifecycle.
- Advisory locking discipline.
- Operator commands (status, refetch, audit) — slices 147/148/149.
  These mostly read the same data; some signatures shift slightly.

See companion doc for slice-by-slice impact.

---

## Decision points

1. **Long-running daemon (#1):** approve and slate as next slice (146 reframe)?
2. **Named symbol lists (#1a):** approve as part of #1's slice? List file format OK?
3. **Bulk CA (#2):** approve and roll into the same slice as #1, or separate?
4. ~~**`adj_*` simplification (#3):**~~ — **withdrawn**. Breaks cagg
   correctness across ex-date boundaries (caggs need pre-adjusted
   source values). Current architecture is right.
5. **Backtest cleanup (#4):** confirmed last conversation, slate as slice 151?
6. **+500k credit bucket ($25):** confirmed buy. Spend on priority1 + priority2
   day 1 (~170k credits used; remainder accelerates universe backfill by ~3 days).

