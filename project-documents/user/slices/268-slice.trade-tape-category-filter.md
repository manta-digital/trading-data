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
status: in_progress
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
  collection for the same categories. **Write-path only**: the tape is still
  fetched whole (265 Decision 3 — classification needs the rows), so the
  request budget, pass duration, and live catch-up rate are unchanged; what
  drops is stored rows and their WAL (expected steady state after cutover:
  the 5–15 GB/day baseline, observed over days, not an acceptance gate).
- **Reversible in configuration, not retroactively in data**: unsetting the
  variable resumes full collection from that pass forward. Ranges the drains
  walked while a category was excluded are **not stored and are not recovered
  by unsetting it**; whether they could later be refetched depends on Kalshi's
  historical-archive retention, which this slice does not verify or rely on
  (see Decision 8). The skipped-range loss is deliberate and PM-ratified via
  this design's approval.
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
- Loud-failure validation of configured category values against the catalog
  (Decision 9) — a typo can never become a silent no-op.
- Operator documentation, all three surfaces (F005): README env-reference row,
  a commented line in `deploy/manta-trading.env.example`, and runbook
  `100-production-operations.md` (the Kalshi env-line enumeration and the
  trades-phase description).
- Architecture amendment (F003): update
  `260-arch.kalshi-event-contract-data.md` — the *Design Goals*
  scope-of-complete paragraph and the completeness/caught-up definitions — to
  the amended wording in Decision 7, as 264/265 did for their PM decisions.
- Cutover: set the variable on the host, restart the daemon, verify
  (preconditions and verification in the walkthrough).

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
     rule-selected markets **not** tape-filtered — otherwise a market closing
     after cutover would be reported "complete through close" while the filter
     guarantees its tape is empty. The partition check extends to
     `before + short + partial + complete + tape_filtered == total`.
   Both the filter line and the JSON payload (`trades.filter` block:
   `excluded_categories`, `tape_filtered_markets`) come from the same
   `Settings` the pass reads, so collection and reporting cannot disagree
   (the 264 Decision 2 invariant).

   **Accepted reporting loss** (review F002, explicit): a filtered-category
   market whose stored tape genuinely reaches its close (e.g. a Crypto market
   that opened and closed in July 2026, before cutover) also moves out of
   `complete_through_close` into the tape-filtered bucket. Status cannot tell
   the two apart without either counting rows in `kalshi.trades` (which the
   trade-status philosophy forbids — journal 20260720) or persisting the
   filter-activation instant (new state this slice does not justify). The
   buckets' job is *"is the collector keeping up on what it intends to
   store"*; completeness of a filtered category's kept history is a
   study-material question answered by SQL over the stored tape, not an ops
   question. The rendered line says so:
   `tape-filtered N closed markets (stored history kept; completeness not
   evaluated)`.

7. **Architecture definitions are amended, in writing, by this slice**
   (review F003). After this slice, two selectors govern the trade tape, so
   the architecture's scope-of-complete and caught-up definitions are updated
   to (wording to land in `260-arch.kalshi-event-contract-data.md` as part of
   this slice, not merely referenced):
   - *Scope of complete*: "the time-series surfaces are complete for the
     markets the collection rule selects — except the trade tape, which is
     additionally scoped by the trades filter
     (`MT_KALSHI_TRADES_EXCLUDED_CATEGORIES`): a tape-filtered market's trade
     tape is deliberately not collected and is excluded from tape-completeness
     evaluation."
   - *Caught up*: "every market past close is complete, explicitly marked
     unrecoverable (behind the historical cutoff), or tape-filtered; and
     open-market surfaces are within one pass interval of now." (Candle and
     settlement completeness for tape-filtered markets are unchanged — the
     filter touches only the tape clause.)

8. **The filter applies to both drains, deliberately — and the historical
   drain's exposure is closed by a cutover precondition, not by code**
   (review F001). Uniform inheritance stands (Decision 4): a per-surface
   filter would let the two drains store divergent universes, the failure
   mode the slice plan warned about. The pre-cutoff exposure is real but
   bounded and already closed in fact: the historical backward drain reported
   `floor_reached: true` on 2026-09-03 (trades coverage from 2026-01-01), so
   there are no windows left for it to skip. The **cutover precondition**
   makes that timing explicit rather than incidental: do not set the variable
   in production until `mt data kalshi status` shows the historical tape at
   `floor reached` (walkthrough step 6). The live drain's catch-up range
   (tape_through → now at cutover) *will* skip filtered-category trades — that
   is the intended effect, not a side effect, and per the Value section it is
   not recoverable by unsetting the variable. Kalshi's historical-archive
   retention is **not** relied on as a recovery path and is not asserted here
   (the architecture forbids assuming it; verifying it belongs to a future
   refetch slice, if one is ever wanted).

9. **A configured category that matches nothing in the catalog fails the
   phase loudly** (review F004). The membership test is exact and
   case-sensitive, and the values are venue data, not an enum — so
   `…=crypto` (lowercase) would otherwise parse, render, log, and filter
   nothing forever: a silent no-op, the exact failure the "no silent
   fallbacks" rule targets. Specified handling: at trades-phase start, after
   the completed-catalog-walk check (the same guard that already no-ops the
   phase on an empty catalog), every configured category is checked against
   the catalog's known categories (`SELECT DISTINCT category FROM
   kalshi.series`); any configured value present in no series row raises a
   named error (`UnknownTradesFilterCategoryError`) that aborts the phase —
   config abort, not `PARTIAL`. A *retired* category keeps working: the
   catalog retains historical series rows, so only a value that has never
   existed fails. The historical phase performs the same check before its
   drain. The zero-match risk that remains (a correctly spelled category with
   genuinely no markets… which cannot happen, since the value comes from
   series rows) needs no warning path beyond the cutover verification step:
   `tape_filtered_markets > 0` in status is the named post-cutover check.

10. **No new env-rename guard.** The variable is new; nothing is renamed. The
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
  trades filter       excluding Crypto (MT_KALSHI_TRADES_EXCLUDED_CATEGORIES)
                      tape-filtered 1,234 closed markets (stored history kept; completeness not evaluated)
```

(`none` when the filter is empty; the tape-filtered line renders only when a
filter is set.) JSON output gains the matching `filter` block.

### Operator documentation (three surfaces — review F005)

- **README**: one row in the env reference table beside the
  `MT_KALSHI_COLLECTION_*` rows, stating the candles-continue semantics
  explicitly.
- **`deploy/manta-trading.env.example`**: a commented
  `# MT_KALSHI_TRADES_EXCLUDED_CATEGORIES=` line beside the five commented
  collection-rule lines, with the same one-line semantics note — so a host
  rebuild from the skeleton surfaces the variable.
- **Runbook `100-production-operations.md`**: add the variable to the Kalshi
  env-line enumeration for `/etc/manta-trading.env`, and one sentence in the
  trades-phase description naming the filter, its counter, and the
  `tape_filtered_markets > 0` post-cutover check — so a later operator finds
  the line explained where the production env file is documented.

### Architecture amendment (review F003)

`260-arch.kalshi-event-contract-data.md` is updated in this slice with
Decision 7's amended scope-of-complete and caught-up wording (the *Design
Goals* paragraph and the *Completeness definitions* block), the same pattern
264/265 used to land their PM decisions in the architecture.

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
9. A configured category present in no `kalshi.series` row aborts the trades
   and historical phases with `UnknownTradesFilterCategoryError` naming the
   value — verified by a test configuring `crypto` (lowercase) against a
   fixture catalog that knows only `Crypto`. A retired-but-once-real category
   does not abort.
10. README, `deploy/manta-trading.env.example`, and runbook
    `100-production-operations.md` all document the variable, its default,
    and the candles-continue semantics; the runbook names the
    `tape_filtered_markets > 0` post-cutover check.
11. `260-arch.kalshi-event-contract-data.md` carries the amended
    scope-of-complete and caught-up definitions (Decision 7).

## Verification Walkthrough

Refined at end of Phase 6 to implemented wording. Steps 1, 2, 5, and 6 are
proven verbatim by the integration tier against a throwaway database
(`test_kalshi_status.py::test_status_command_renders_the_trades_filter`,
`test_kalshi_trades.py::TestTradesFilterValidation`,
`test_trade_sync.py::TestTradesFilterAccounting`); an external verifier can
run them as written against any dev database seeded with a catalog.

```bash
# 1. Filter off (default): status shows none, nothing changes
mt data kalshi status | grep "trades filter"
#    → trades filter       none (MT_KALSHI_TRADES_EXCLUDED_CATEGORIES)
#    (no tape-filtered line renders while the filter is empty)

# 2. Enable the filter in a shell (dev DB), run one pass
MT_KALSHI_TRADES_EXCLUDED_CATEGORIES=Crypto mt data kalshi sync
#    → phase start line: "kalshi trades phase started run_id=... cutoff=...
#      watermark=... coverage_from=... rule: ... · trades filter: excluding
#      Crypto"
#    → window lines: "trades window {start}→{end} pages P fetched F written W
#      unknown U excluded E filtered X"
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
#    Text form adds, under the filter line:
#    "tape-filtered N closed markets (stored history kept; completeness not
#    evaluated)"
#    N > 0 is the named typo check: 0 with a filter set means the value
#    matched no category (should be impossible past step 6's error, but it is
#    the check an operator runs after any hand edit).

# 6. A typo fails loudly, never a silent no-op (Decision 9)
MT_KALSHI_TRADES_EXCLUDED_CATEGORIES=crypto mt data kalshi sync
#    → both the trades and historical phases abort pre-drain with
#      UnknownTradesFilterCategoryError:
#      "MT_KALSHI_TRADES_EXCLUDED_CATEGORIES names 'crypto' — present in no
#      kalshi.series row (the test is exact and case-sensitive, so this would
#      filter nothing, forever). Known categories: ..."
#      No watermark or cursor moves; the outcome is never PARTIAL.

# 7. [PM] Production cutover (after merge + release install) — scripted:
sudo python3 scripts/cutover_268_trades_filter.py
#    The script enforces the PRECONDITION (Decision 8): it aborts unless
#    `mt-run data kalshi status --json` shows the historical tape at floor
#    reached (2026-01-01). Then it adds
#    MT_KALSHI_TRADES_EXCLUDED_CATEGORIES=Crypto to /etc/manta-trading.env,
#    restarts the kalshi pass timer's service environment, and reports:
#      - the status filter line ("excluding Crypto")
#      - the journal's next start line carrying "trades filter: excluding
#        Crypto" (may be pending until the next hourly firing)
#      - trades.filter.tape_filtered_markets > 0 from --json (the typo check)
#    Nonzero exit on any failed check; safe to re-run.
#    Note: the live catch-up range (tape_through → now) skips Crypto trades
#    from this moment — deliberate and not recoverable by unsetting.
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
4. `TradeSync` totals + log lines + `TradeResult` counter + event + the
   Decision 9 category validation (`UnknownTradesFilterCategoryError`); the
   existing scripted-tape fake grows filtered rows. Historical-drain test via
   the existing 267 harness confirms inheritance and validation.
5. `collection_pass.py` construction sites; `trade_status.py` +
   `kalshi_status_render.py` + JSON payload + tests.
6. Documentation set: README row, `deploy/manta-trading.env.example` line,
   runbook 100 updates, and the Decision 7 architecture amendment; cutover
   steps land in the walkthrough refinement.

### Testing strategy

Unit tier for parsing, rendering, and accounting; integration tier for the
statement against a real fixture catalog (the NULL-category and precedence
cases must run against real SQL — this is a parser-writes-SQL slice, and the
COALESCE/NULL behavior is exactly the kind of thing a mocked repository test
would falsely pass). No load-tier impact: the statement gains one boolean
column and one FILTER count on an already-page-bounded write.
