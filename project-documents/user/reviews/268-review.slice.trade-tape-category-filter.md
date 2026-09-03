---
docType: review
layer: project
reviewType: slice
slice: trade-tape-category-filter
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/268-slice.trade-tape-category-filter.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260903
dateUpdated: 20260903
reviewedSha: 5f26bb7df47e001f694d3f5659bb762c60b1b053
findings:
  - id: F001
    severity: concern
    category: architecture-goal-conflict
    summary: "Filter applied to the historical backward drain permanently forfeits pre-cutoff tape, and the \"reversible\" claim is asserted, not verified"
    location: "project-documents/user/slices/268-slice.trade-tape-category-filter.md:39-42"
  - id: F002
    severity: concern
    category: correctness
    summary: "Re-scoping the closed-market buckets erases completeness reporting for already-collected trades of a filtered category"
    location: "project-documents/user/slices/268-slice.trade-tape-category-filter.md:193-199"
  - id: F003
    severity: concern
    category: architecture-drift
    summary: "Architecture's \"scope of complete\" and caught-up definitions are amended by this slice with no amendment path"
    location: "project-documents/user/slices/268-slice.trade-tape-category-filter.md#technical-decisions"
  - id: F004
    severity: concern
    category: error-handling
    summary: "Failure mode for a mistyped, mis-cased, or retired category value is not enumerated"
    location: "project-documents/user/slices/268-slice.trade-tape-category-filter.md:143-155"
  - id: F005
    severity: concern
    category: under-specification
    summary: "Operator-facing configuration documentation is scoped to README only; the production runbook and env skeleton are omitted"
    location: "project-documents/user/slices/268-slice.trade-tape-category-filter.md:59"
  - id: F006
    severity: note
    category: nfr-restatement
    summary: "Request budget and fetch volume are unchanged by the filter, but the slice never says so"
    location: "project-documents/user/slices/268-slice.trade-tape-category-filter.md:37-38"
  - id: F007
    severity: pass
    category: architecture-alignment
    summary: "Vocabulary separation, render point, and dependency direction respect the parent architecture and 265's decisions"
    location: "project-documents/user/slices/268-slice.trade-tape-category-filter.md:139-166"
  - id: F008
    severity: pass
    category: architecture-alignment
    summary: "Accounting and precedence keep filtered trades a first-class, non-silent count"
    location: "project-documents/user/slices/268-slice.trade-tape-category-filter.md:131-137"
---

# Review: slice — slice 268

**Verdict:** CONCERNS
**Model:** claude-opus-5

## Findings

### [CONCERN] Filter applied to the historical backward drain permanently forfeits pre-cutoff tape, and the "reversible" claim is asserted, not verified

The architecture's first Design Goal is "Capture before it disappears — the primary goal is completeness of the record while it is still reachable … collected ahead of Kalshi's historical-endpoint migration and retention decisions," and *Technical Considerations* explicitly requires the cutoff/retention behavior be "verified against Kalshi's published documentation during slice design — not assumed."

This slice makes the filter a required keyword on `TradeRepository` so that 267's `HistoricalPhase` inherits it identically (Decisions 4, Success Criterion 4, line 290). 267's drain walks one-hour windows **backward**, moving `kalshi.sync_state['historical'].watermark_ts` down per window and stopping at `HISTORICAL_TRADES_FLOOR` with `floor_reached: true`, never revisiting a descended window. 267 is complete and its drain is descending now. So with `MT_KALSHI_TRADES_EXCLUDED_CATEGORIES=Crypto` set at cutover, every Crypto trade in every window the drain descends past is skipped **permanently** — precisely the disappearing pre-cutoff data the architecture prioritizes — and the slice puts "Backfilling ranges skipped while a category was excluded" out of scope (line 67).

Failure scenario: cutover sets `=Crypto`; over the following passes the historical drain descends from the live floor toward `2026-01-01` and reports `floor_reached`. The Crypto tape for that entire span is never stored. The slice's mitigation — "ranges skipped while excluded remain refetchable from Kalshi's historical trades archive" (line 39-42) — rests on an assumption about Kalshi's retention that the architecture forbids assuming, and on a slice that is explicitly "not built here." If Kalshi advances or prunes the archive, the loss is unrecoverable and the design document recorded it as reversible.

At minimum the design should (a) state whether the filter is *intended* to apply to the historical drain during the drain (an operator could equally hold the filter off for `historical` until `floor_reached`, which conflicts with "cannot diverge" but is a live trade-off the doc never weighs), and (b) either cite the verified archive retention or restate the loss as irreversible pending PM ratification.

### [CONCERN] Re-scoping the closed-market buckets erases completeness reporting for already-collected trades of a filtered category

Decision 6 re-scopes `complete_through_close` / `partial_history` / `short_of_close` / `before_coverage` to rule-selected markets **not** tape-filtered, justified as "otherwise a Crypto market would be reported 'complete through close' while the filter guarantees its tape is empty." That justification holds only for markets whose tape window falls entirely after cutover. It does not hold for the 3.67M Crypto trades already stored — which PM decision 2026-09-03 deliberately **keeps** as study material (line 63-65) and which Success Criterion 8 promises remain intact.

Failure scenario: a Crypto market that opened and closed in July 2026 has a genuinely complete tape in `kalshi.trades`. After cutover its category is in the filter, so `trade_status.py` moves it out of `complete_through_close` into `tape_filtered_markets`. The operator now sees a status surface asserting a filter-scoped bucket for a market whose tape *is* complete, and the architecture's completeness definition ("a closed market is complete when … its trade tape reaches close") is no longer evaluable for those markets. The bucket becomes a config-derived classification rather than a persisted-fact one, which is the opposite of Decision 6's own stated philosophy ("`trade_status.py` counts markets from the catalog … that philosophy holds"). The design should distinguish tape-filtered markets whose stored tape already reaches close from those the filter actually emptied, or state explicitly that this reporting loss is accepted.

### [CONCERN] Architecture's "scope of complete" and caught-up definitions are amended by this slice with no amendment path

The architecture records, as a PM decision, that "the *time-series surfaces* are complete for the markets a configurable **collection rule** selects" (*Design Goals*), and defines *caught up* as "every market past close is complete or explicitly marked unrecoverable." This slice introduces a **second** selector governing one of those two surfaces, and a fifth partition bucket, so neither definition is accurate as written after this slice ships.

Failure scenario: a reader of `260-arch.kalshi-event-contract-data.md` after this slice lands concludes that trade-tape completeness is governed solely by `MT_KALSHI_COLLECTION_*`, and reasons about `status` output (or scopes a downstream analysis slice) on a definition the collector no longer implements. The slice should either restate the amended definitions in full ("a tape-filtered market is complete when …" / how tape-filtered markets count toward caught-up) or carry a task to amend the architecture's *Design Goals* scope-of-complete paragraph, as 264/265 did for their own PM decisions.

### [CONCERN] Failure mode for a mistyped, mis-cased, or retired category value is not enumerated

Decision 1 states category strings are "Kalshi's own `series.category` values: data, not an enum," and Decision 3 renders `COALESCE(s.category, '') = ANY(%(trades_excluded_categories)s)` — an exact, case-sensitive equality. No failure mode is enumerated for a value that matches nothing, and no validation, warning, or status signal is specified for it. This is the case the project's "never use silent fallback values" rule targets, and it is a live operational path (a hand-edited line in `/etc/manta-trading.env`).

Failure scenario: the operator writes `MT_KALSHI_TRADES_EXCLUDED_CATEGORIES=crypto` (lowercase) at cutover. Settings parse fine, `selection.trades_filter_sql` renders a valid predicate, the start log prints "trades filter: excluding crypto," and `excluded_by_trades_filter` reports 0 forever. The ~100 GB/day WAL rate the slice exists to fix continues, and nothing in the design fails or warns — the only signal is an operator noticing `tape_filtered_markets: 0` in `status`, which the design does not name as a check. The same silent-no-op recurs if Kalshi later renames or re-buckets the category. Specify the handling: fail loudly at startup on a configured category present in no `kalshi.series` row, or at minimum make the zero-match case an explicit, named warning in the phase start line and a documented cutover verification step.

### [CONCERN] Operator-facing configuration documentation is scoped to README only; the production runbook and env skeleton are omitted

*Technical Scope* lists only "README env-reference row for the new variable," and *Implementation Notes* step 6 is "README row." But the two surfaces an operator actually reads for the production environment file are `deploy/manta-trading.env.example` (which carries the five commented `MT_KALSHI_COLLECTION_*` lines) and runbook `100-production-operations.md` (which enumerates the Kalshi lines of `/etc/manta-trading.env` and describes the trades phase and the rule). 264 and 265 both updated those surfaces for their configuration changes; this slice does not.

Failure scenario: after cutover, `/etc/manta-trading.env` contains a permanent `MT_KALSHI_TRADES_EXCLUDED_CATEGORIES=Crypto` line that appears in no skeleton and no runbook. A later operator (or a host rebuild from the skeleton) reads runbook 100's list of Kalshi env lines, sees only the five `MT_KALSHI_COLLECTION_*` entries, and reinstates full-volume tape collection without knowing why, or drops the line during a rebuild and reproduces the disk-full condition.

### [NOTE] Request budget and fetch volume are unchanged by the filter, but the slice never says so

The architecture's *Rate-limit budget* consideration requires catalog sync, candles, and trades to share one budget across the pass, and 267 caps its historical phase at ~30,000 requests. This is a **write-path** filter: the tape is still fetched whole (265 Decision 3), so neither the request budget, the pass duration, nor 267's descent rate improves. The *Value* section states "removes ~90% of tape write volume" without the corresponding "and 0% of request volume / drain time," and the WAL target the walkthrough alludes to ("5–15 GB/day steady state") appears only as a non-acceptance follow-up. Stating both explicitly would prevent an operator from expecting the historical drain to finish faster after cutover.

### [PASS] Vocabulary separation, render point, and dependency direction respect the parent architecture and 265's decisions

Decision 1 correctly refuses to extend `CollectionRule`, preserving 265 Decision 3's surface-neutral "one rule, one universe" property that the architecture's *Design Goals* paragraph records as a PM decision, and matches the slice plan's explicit direction ("deliberately separate from the candle-side `MT_KALSHI_COLLECTION_*` vocabulary (PM decision 2026-09-03)"). Decision 3 keeps SQL rendering in `selection.py` — the one module that spells rule SQL — with bound parameters disjoint from rule parameters, honoring the no-magic-strings and DRY discipline. Decision 4's required keyword argument on `TradeRepository` structurally prevents the live/historical divergence the slice plan warned about. No new module, no upward dependency, no schema or API change, and no `public`-schema join is introduced.

### [PASS] Accounting and precedence keep filtered trades a first-class, non-silent count

The architecture requires that excluded markets be "counted and reported by `mt data kalshi status`, never silently dropped." Decision 5 extends 265's exact page-accounting identity to `fetched == written + unknown_market + excluded_by_rule + excluded_by_trades_filter + duplicates`, still verified in `__post_init__` with `selected` carried rather than derived, and the stated precedence (unknown → excluded-by-rule → excluded-by-trades-filter → stored/duplicate) yields provably disjoint buckets given `excluded_by_rule = known AND NOT selected` and `tape_filtered = known AND selected AND member`. The counter propagates through `TradeResult`, the `phase_finished` event, the per-window log line, and the JSON status payload, and the testing strategy correctly routes the NULL-category and precedence cases to the integration tier against real SQL rather than a mocked repository.
