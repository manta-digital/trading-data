---
docType: slice-design
slice: trade-tape-category-filter
project: trading
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [265, 267]
interfaces: []
effort: 2
dateCreated: 20260903
dateUpdated: 20260903
status: not_started
---

# Slice Design: Trade Tape Category Filter (268)

## Overview

A write-path category filter on the Kalshi trades tape. A new, trades-specific
setting — `MT_KALSHI_TRADES_EXCLUDED_CATEGORIES` — names series categories whose
trades are classified and counted but **not stored**, while candles for those
same categories keep collecting under the unchanged `MT_KALSHI_COLLECTION_*`
rule. The filter applies inside the one classify-and-write statement both tape
drains share (`trade_repository._write_page_statement`), so the live forward
drain (265) and the historical backward drain (267) inherit it identically and
cannot diverge.

Motivation is measured, not speculative: on 2026-07-21, Crypto alone was 90.5%
of the stored tape (3.67M of 4.05M trades), and tape volume at that rate drove
the ~100 GB/day WAL behind the 2026-09-02 disk-full incident. The intended
production state — excluding Crypto trades while continuing Crypto candles — is
exactly what the shared collection rule cannot express, which is why this is a
separate vocabulary (PM decision 2026-09-03).

## Value

- **Operational**: removes ~90% of tape write volume (and its WAL, compression,
  and backup churn) at the cost of one env var, without touching candle
  collection for the same categories.
- **Reversible in data terms**: unsetting the variable resumes full collection;
  ranges skipped while excluded remain refetchable from Kalshi's historical
  trades archive (a future slice if ever wanted — not built here).
- **Honest accounting**: filtered trades are a first-class counter in the page
  accounting identity, the phase log lines, the `phase_finished` event, and
  `mt data kalshi status` — never a silent drop.

## Technical Scope

**In scope**
- New `Settings` field `kalshi_trades_excluded_categories`
  (`MT_KALSHI_TRADES_EXCLUDED_CATEGORIES`), default empty = no filtering.
- Filter clause rendered in `selection.py` (the one module that spells rule
  SQL), embedded in the trades `write_page` statement.
- New counter `excluded_by_trades_filter` through the whole chain:
  `PageCounts` → accounting identity → `TradeSync` window totals and log lines
  → `TradeResult.counts()` / `to_dict()` → `phase_finished` event.
- `mt data kalshi status` trades block: a `trades filter` line (rule in force +
  count of tape-filtered markets), and the four closed-market buckets
  re-scoped to markets whose tape is actually being stored.
- README env-reference row for the new variable.
- Cutover: set the variable on the host, restart the daemon, verify.

**Out of scope**
- Any candle-path change. `CollectionRule`, `MT_KALSHI_COLLECTION_*`, and
  every candle query are untouched.
- Deleting or migrating already-stored Crypto trades (PM 2026-09-03: kept as
  study material). No schema change, no migration.
- Backfilling ranges skipped while a category was excluded.

## Dependencies

### Prerequisites
- **265 (public trades collection)** — `TradeSync`, `TradeRepository`,
  the classify-and-write statement, `PageCounts`, and the accounting identity
  this slice extends. Complete.
- **267 (historical backfill phase)** — the shared `TradeSync.drain` /
  `WindowDirection` structure that makes one filter cover both drains, and the
  `historical` surface `TradeRepository`. Complete.

### Interfaces Required
- `Settings` (config layer) — new field beside the five
  `kalshi_collection_*` fields, reusing the existing comma-split validator.
- `selection.py` — the filter's SQL is rendered here and only here, same
  bound-parameter discipline as `selection_sql` (values never in statement
  text).

## Architecture

### Component Structure

One new concept — the **trades filter**, a `frozenset[str]` of Kalshi
`series.category` values — flows through existing components; no new module.

```
Settings.kalshi_trades_excluded_categories        (config/__init__.py)
    │  passed at construction, both sites
    ▼
TradeRepository(conn, rule, trades_excluded=…)    (trade_repository.py)
    │  renders once via selection.trades_filter_sql(…)
    ▼
_write_page_statement(rule, trades_excluded)      one classified CTE,
    │                                             one more FILTER count
    ▼
PageCounts.excluded_by_trades_filter ─► TradeSync totals ─► TradeResult
                                                            ─► event / log
mt data kalshi status ─► trade_status.py reads the same Settings field
                         and renders the filter line + re-scoped buckets
```

Construction sites that must pass the filter (and thereby inherit it
identically): `TradesPhase.run` and `HistoricalPhase.run` in
`collection_pass.py` — the only two production constructions of
`TradeRepository`.

### Data Flow (delta over 265/267)

Unchanged: windows, pages, transactions, watermarks, cursors, events. The only
change is inside the per-page statement:

1. Page rows are `unnest`-ed and `LEFT JOIN`-ed onto the catalog (as today).
2. `classified` now computes three facts per row instead of two:
   - `known` — the market exists in the catalog (as today);
   - `selected` — the shared collection rule's `"any"` form holds (as today);
   - **`tape_filtered`** — `known AND selected AND` the row's series category
     is in the trades filter (new).
3. The insert stores `selected AND NOT tape_filtered` rows, conflict-ignore.
4. The statement returns five counts + unknown tickers: `unknown`,
   `excluded_by_rule` (`known AND NOT selected` — unchanged meaning),
   **`excluded_by_trades_filter`** (`tape_filtered`), `selected_for_store`
   (insert candidates), `written`.

**Precedence** (so every row lands in exactly one bucket): unknown →
excluded-by-rule → excluded-by-trades-filter → stored/duplicate. A Sports
trade counts as excluded-by-rule even if Sports is *also* named in the trades
filter — the shared rule is evaluated first.

## Technical Decisions

1. **Separate vocabulary, separate field.** `MT_KALSHI_TRADES_EXCLUDED_CATEGORIES`
   is a new `Settings` field, *not* a new clause of `CollectionRule` — the rule
   deliberately stays surface-neutral (one rule for candles and trades, 265
   Decision 3), and the intended state (Crypto candles yes, Crypto trades no)
   is expressible only with a trades-only vocabulary (PM 2026-09-03). Field:
   `kalshi_trades_excluded_categories: Annotated[frozenset[str], NoDecode] =
   frozenset()`, parsed by adding the field name to the existing
   `_split_category_list` validator — comma-separated in the environment,
   empty string = empty set. Category strings are Kalshi's own
   `series.category` values: data, not an enum (same rationale as the rule).

2. **Default empty = filter off.** Not a silent fallback: the unset state is
   the documented, neutral "store everything the rule selects" behavior every
   operator has today. Cutover to the intended production state is an explicit
   env change on the host (`Crypto`), not a shipped default that changes other
   operators' collections.

3. **One render point, in `selection.py`.** A small
   `trades_filter_sql(excluded: frozenset[str]) -> Selection` beside
   `selection_sql`: renders `COALESCE(s.category, '') = ANY(%(trades_excluded_categories)s)`
   (the *membership* test — the statement negates or counts it as needed), with
   the categories bound as a sorted list under a parameter name disjoint from
   every rule parameter, so rule and filter bind together in one statement.
   Empty set renders `FALSE` (nothing is filtered) so the statement shape is
   constant. NULL semantics follow the rule's established treatment: an
   uncategorised series COALESCEs to `''`, which is never in the filter — its
   trades are kept. A `describe_trades_filter(excluded) -> str` helper
   (`"excluding Crypto"` / `"none"`) gives the log and status lines one
   spelling.

4. **The filter is a required keyword argument, not a default.**
   `TradeRepository(conn, rule, *, trades_excluded, surface=…)` — no default
   value, so neither construction site (live or historical) can silently omit
   it and the two drains cannot diverge by omission. The repository exposes it
   as a read-only property; `TradeSync.run`'s start line logs it beside the
   rule (`… rule: … · trades filter: excluding Crypto`), reading it from the
   repository so there is exactly one carrier.

5. **A fifth accounting bucket, exact as before.** `PageCounts` gains
   `excluded_by_trades_filter: int`; the identity becomes
   `fetched == written + unknown_market + excluded_by_rule +
   excluded_by_trades_filter + duplicates`, still verified in
   `__post_init__` with `selected` carried (not derived) for the same
   arity-bug-catching reason as today. `TradeResult` gains the same counter in
   `counts()` and `to_dict()`; the per-window log line gains `filtered %d`.
   Name is `excluded_by_trades_filter` everywhere — parallel to
   `excluded_by_rule`, no abbreviations.

6. **Status shows the filter as persisted fact, and the buckets stay honest.**
   `trade_status.py` counts markets from the catalog, never rows in
   `kalshi.trades` — that philosophy holds. Two changes:
   - A `trades filter` line in the trades block:
     `describe_trades_filter(...)` plus the env var name (mirroring the candle
     block's rule line), and a count of **tape-filtered markets** — closed,
     rule-selected (`"ever"` form) markets whose category is in the filter.
   - The four closed-market buckets (`complete_through_close`,
     `partial_history`, `short_of_close`, `before_coverage`) re-scope to
     rule-selected markets **not** tape-filtered — otherwise a Crypto market
     would be reported "complete through close" while the filter guarantees
     its tape is empty. The partition check extends to
     `before + short + partial + complete + tape_filtered == total`.
   Both the filter line and the JSON payload (`trades.filter` block:
   `excluded_categories`, `tape_filtered_markets`) come from the same
   `Settings` the pass reads, so collection and reporting cannot disagree
   (the 264 Decision 2 invariant).

7. **No new env-rename guard.** The variable is new; nothing is renamed. The
   265 guard (`MT_KALSHI_CANDLE_*`) is untouched.

## Implementation Details

### Configuration

```
MT_KALSHI_TRADES_EXCLUDED_CATEGORIES   default: empty (filter off)
                                       production intent: Crypto
```

Comma-separated, whitespace-trimmed, same parsing as the rule's category
lists. Interplay with the shared rule: the rule runs first; the filter only
ever removes rows the rule selected. A category excluded by the rule needs no
filter entry (its trades — and candles — are already excluded).

### The statement (shape only — implementation writes the real SQL)

`_write_page_statement(rule, trades_excluded)` extends the existing CTE chain:

```sql
classified AS (
  SELECT p.*, m.ticker IS NOT NULL AS known,
         COALESCE({rule "any" predicate}, FALSE) AS selected,
         (m.ticker IS NOT NULL
          AND COALESCE({rule predicate}, FALSE)
          AND {filter membership})                AS tape_filtered
  ...)
ins AS (INSERT ... WHERE selected AND NOT tape_filtered ...)
SELECT count(*) FILTER (WHERE NOT known),
       count(*) FILTER (WHERE known AND NOT selected),
       count(*) FILTER (WHERE tape_filtered),
       count(*) FILTER (WHERE selected AND NOT tape_filtered),
       (SELECT count(*) FROM ins),
       ... unknown tickers ...
```

The rule predicate must not be evaluated twice in a way that permits drift —
spell it once in the CTE (e.g. compute `selected` first and derive
`tape_filtered` from it) rather than pasting the predicate into two clauses.

### Counters and events

`phase_finished` event `counts` gains `excluded_by_trades_filter` for both the
`trades` and `historical` phases (the historical core reuses `TradeSync.drain`
and `TradeResult`, so it inherits the counter with zero historical-specific
code). Existing journal rows without the key are simply old — no migration.

### CLI rendering

`kalshi_status_render.print_trade_status` gains one line, e.g.:

```
  trades filter       excluding Crypto (MT_KALSHI_TRADES_EXCLUDED_CATEGORIES) · tape-filtered 1,234 closed markets
```

(`none` when the filter is empty; the tape-filtered count renders only when a
filter is set.) JSON output gains the matching `filter` block.

### README

One row in the env reference table beside the `MT_KALSHI_COLLECTION_*` rows,
stating the candles-continue semantics explicitly.

## Integration Points

**Provides**: nothing new to other slices — `interfaces: []`. The counter and
status line are operator-facing surface.

**Consumes**: 265/267's trade write path and 264/265's selection module, as
described. No API, daemon-loop, or schema contract changes; a pass binary from
before this slice and after it write the same tables.

## Success Criteria

1. With `MT_KALSHI_TRADES_EXCLUDED_CATEGORIES` unset, behavior is bit-for-bit
   today's: same rows stored, `excluded_by_trades_filter` reports 0
   everywhere, existing tests pass unmodified (beyond the `PageCounts`
   construction-arity change).
2. With `…=Crypto`, a page containing Crypto-market trades stores none of
   them, counts each exactly once under `excluded_by_trades_filter`, and the
   extended accounting identity holds (enforced by `PageAccountingError`).
3. Precedence: a trade whose category is excluded by the shared rule counts as
   `excluded_by_rule`, never `excluded_by_trades_filter`, even when its
   category is also in the filter.
4. Both drains inherit the filter: the live `trades` phase and the
   `historical` backward drain, run under the same settings, produce
   `excluded_by_trades_filter` counts and store no filtered rows — verified by
   a test constructing each against a fixture catalog.
5. `mt data kalshi status` (text and `--json`) shows the filter in force and
   the tape-filtered market count; the four closed-market buckets exclude
   tape-filtered markets and the extended partition check passes.
6. The phase start log line names the filter; the per-window line carries
   `filtered N`.
7. Uncategorised series (NULL category) are unaffected: their trades store
   exactly as today under any filter value.
8. Already-stored trades of a filtered category remain readable and untouched
   — no statement in this slice deletes or rewrites `kalshi.trades`.
9. README documents the variable, its default, and the candles-continue
   semantics.

## Verification Walkthrough

Draft demo script; refined at end of Phase 6.

```bash
# 1. Filter off (default): status shows none, nothing changes
mt data kalshi status | grep -A1 "Kalshi trades"
#    → trades filter       none

# 2. Enable the filter in a shell (dev DB), run one pass
MT_KALSHI_TRADES_EXCLUDED_CATEGORIES=Crypto mt data kalshi sync
#    → phase start line: "... rule: ... · trades filter: excluding Crypto"
#    → window lines: "... fetched F written W unknown U excluded E filtered X"
#    → X > 0 during Crypto trading hours; W excludes all Crypto rows

# 3. Prove nothing Crypto landed after the cutover instant T
#    (psql, dev/prod read role)
SELECT count(*) FROM kalshi.trades t
JOIN kalshi.markets m ON m.ticker = t.market_ticker
JOIN kalshi.events e  ON e.event_ticker = m.event_ticker
JOIN kalshi.series s  ON s.ticker = e.series_ticker
WHERE s.category = 'Crypto' AND t.created_time > {T};
--   → 0

# 4. Prove stored history is intact (same query, < {T}) → unchanged count

# 5. Status reflects the filter
MT_KALSHI_TRADES_EXCLUDED_CATEGORIES=Crypto mt data kalshi status --json \
  | jq .trades.filter
#    → {"excluded_categories": ["Crypto"], "tape_filtered_markets": N}

# 6. [PM] Production cutover (after merge + release install):
#    add MT_KALSHI_TRADES_EXCLUDED_CATEGORIES=Crypto to the daemon's
#    environment file, restart the kalshi service, then read back:
mt data kalshi status          # filter line shows "excluding Crypto"
journalctl -u <kalshi unit> | grep "trades filter"   # start line confirms
#    Follow-up observation (days, not part of acceptance): pg_stat WAL rate
#    and /data growth drop toward the 5–15 GB/day steady state.
```

## Implementation Notes

### Development Approach

Suggested order — each step leaves the tree green:

1. `Settings` field + validator wiring + unit tests (parse, empty, whitespace).
2. `selection.trades_filter_sql` + `describe_trades_filter` + unit tests
   (empty set → `FALSE` / `"none"`, NULL-category semantics documented in a
   test against the fixture DB).
3. `PageCounts` fifth field + identity + `_write_page_statement` extension +
   repository tests against the fixture catalog (mixed page: unknown /
   rule-excluded / filtered / stored / duplicate rows — one page hitting all
   five buckets; precedence case of a category in both lists).
4. `TradeSync` totals + log lines + `TradeResult` counter + event; the
   existing scripted-tape fake grows filtered rows. Historical-drain test via
   the existing 267 harness confirms inheritance.
5. `collection_pass.py` construction sites; `trade_status.py` +
   `kalshi_status_render.py` + JSON payload + tests.
6. README row; cutover steps land in the walkthrough refinement.

### Testing strategy

Unit tier for parsing, rendering, and accounting; integration tier for the
statement against a real fixture catalog (the NULL-category and precedence
cases must run against real SQL — this is a parser-writes-SQL slice, and the
COALESCE/NULL behavior is exactly the kind of thing a mocked repository test
would falsely pass). No load-tier impact: the statement gains one boolean
column and one FILTER count on an already-page-bounded write.
