---
docType: tasks
slice: trade-tape-category-filter
project: trading-data
lld: user/slices/268-slice.trade-tape-category-filter.md
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [265, 267]
interfaces: []
projectState: >
  Slices 265 (public trades collection) and 267 (historical backfill phase)
  are complete. The hourly pass runs CatalogPhase, CandlesPhase, TradesPhase,
  HistoricalPhase; the historical backward drain reported floor_reached
  (trades coverage from 2026-01-01) on 2026-09-03. Design 268 committed at
  5f26bb7, review findings F001-F006 remediated at 779fe63 (verdict
  CONCERNS, passes gate). Crypto is ~90% of stored tape volume; the intended
  production state is Crypto trades filtered, Crypto candles continuing.
dateCreated: 20260903
dateUpdated: 20260903
status: not_started
---

## Context Summary

- Working on **268 trade-tape-category-filter**: a write-path category
  filter on the Kalshi trades tape. New env
  `MT_KALSHI_TRADES_EXCLUDED_CATEGORIES` (default empty = off) names
  `series.category` values whose trades are classified and counted but not
  stored; candles for the same categories keep collecting under the
  unchanged `MT_KALSHI_COLLECTION_*` rule.
- Source of truth: `user/slices/268-slice.trade-tape-category-filter.md`.
  Tasks cite its Decisions by number; consult it before each section.
- The filter lives in the one classify-and-write statement both drains
  share (`trade_repository._write_page_statement`), so live (265) and
  historical (267) inherit it identically. A fifth accounting bucket
  `excluded_by_trades_filter` flows through `PageCounts` → `TradeSync` →
  `TradeResult` → `phase_finished` event → `mt data kalshi status`.
- Key modules (all under `src/manta_trading/` unless noted):
  `config/__init__.py`, `data/kalshi/selection.py`,
  `data/kalshi/trade_repository.py`, `data/kalshi/trade_sync.py`,
  `data/kalshi/trade_types.py`, `data/kalshi/collection_pass.py`,
  `data/kalshi/trade_status.py`, `cli/commands/kalshi_status_render.py`.
- Rules in force: no destructive SQL against any database this slice's
  tests did not create; tests never read the production database URL; no
  silent fallbacks (Decision 9 exists precisely to prevent one); bound
  parameters only — category values never appear in statement text.
- Ordering deviation from the design's suggested step order, deliberate:
  the `TradeRepository` constructor change (required keyword) and the two
  `collection_pass.py` construction sites land in the same section
  (Section 3), because a required keyword with no default breaks both
  construction sites the moment it lands — updating them together is what
  keeps the tree green between sections.
- Commit checkpoint at the end of each section (project convention:
  checkpoint per section). Scope any `ruff format` to touched files only.
- Phase 6 work happens on branch `268-slice.trade-tape-category-filter`
  from `main` (integration_branch unset).
- Delivers: the filter, its accounting, loud typo validation, status
  surfaces, operator docs (three surfaces), and the Decision 7
  architecture amendment. Next planned slice: 920 (backup hardening).

## Section 1: Settings field

Design *Technical Decisions 1, 2*; *Implementation Details: Configuration*.

- [ ] **Task 1.1: Add `kalshi_trades_excluded_categories` to `Settings`**
      (effort: 1)
  - [ ] In `config/__init__.py`, add field
        `kalshi_trades_excluded_categories: Annotated[frozenset[str], NoDecode] = frozenset()`
        beside the five `kalshi_collection_*` fields, env name
        `MT_KALSHI_TRADES_EXCLUDED_CATEGORIES`.
  - [ ] Add the field name to the existing `_split_category_list`
        validator (comma-separated, whitespace-trimmed, empty string →
        empty set). Do not duplicate the parsing logic.
  - [ ] Success: `Settings()` with the env unset yields `frozenset()`;
        `MT_KALSHI_TRADES_EXCLUDED_CATEGORIES="Crypto, Sports"` yields
        `frozenset({"Crypto", "Sports"})`. Values keep their exact case.

- [ ] **Task 1.2: Settings unit tests** (effort: 1)
  - [ ] In `test/unit/test_collection_rule_settings.py` (where the
        category-list parsing tests live), add cases for the new field:
        unset → empty set, single value, comma list with whitespace,
        empty string → empty set, case preserved (`crypto` stays
        `crypto` — validation is Decision 9's job, not the parser's).
  - [ ] Success: new tests pass; the full settings test module passes
        unmodified otherwise.

- [ ] **Task 1.3: Checkpoint commit** (effort: 1)
  - [ ] Commit Section 1 (e.g.
        `feat: add MT_KALSHI_TRADES_EXCLUDED_CATEGORIES setting`).

## Section 2: Filter SQL rendering in `selection.py`

Design *Technical Decision 3*.

- [ ] **Task 2.1: `trades_filter_sql` and `describe_trades_filter`**
      (effort: 2)
  - [ ] In `data/kalshi/selection.py`, add
        `trades_filter_sql(excluded: frozenset[str]) -> Selection`
        rendering the membership test
        `COALESCE(s.category, '') = ANY(%(trades_excluded_categories)s)`
        with the categories bound as a sorted list. The statement, not
        this function, negates or counts the test.
  - [ ] Parameter name `trades_excluded_categories` must be disjoint from
        every parameter `selection_sql` can emit, so rule and filter bind
        together in one statement. Verify against the rule's parameter
        names before choosing; if a collision exists, stop and flag it.
  - [ ] Empty set renders literal `FALSE` (nothing filtered) so the
        statement shape is constant across configurations.
  - [ ] Add `describe_trades_filter(excluded: frozenset[str]) -> str`
        returning `"none"` for empty, `"excluding Crypto"` /
        `"excluding Crypto, Sports"` (sorted) otherwise — the one
        spelling for log and status lines.
  - [ ] Success: category values appear only in bound parameters, never
        in statement text; both helpers exported beside `selection_sql`.

- [ ] **Task 2.2: Selection unit tests** (effort: 1)
  - [ ] In `test/unit/data/kalshi/test_selection_sql.py`: empty set →
        `FALSE` and empty/absent params; non-empty set → membership SQL
        with sorted bound list; parameter-name disjointness from a
        rendered `selection_sql` for a representative rule;
        `describe_trades_filter` for empty, one, and two categories.
  - [ ] Success: new tests pass; existing selection tests pass unmodified.

- [ ] **Task 2.3: Checkpoint commit** (effort: 1)
  - [ ] Commit Section 2 (e.g.
        `feat: render trades filter SQL in selection module`).

## Section 3: `PageCounts`, the write statement, and construction sites

Design *Architecture: Data Flow*, *Technical Decisions 4, 5*,
*Implementation Details: The statement*.

- [ ] **Task 3.1: Fifth `PageCounts` bucket** (effort: 1)
  - [ ] In `data/kalshi/trade_repository.py`, add
        `excluded_by_trades_filter: int` to `PageCounts`. Extend the
        `__post_init__` identity to
        `fetched == written + unknown_market + excluded_by_rule +
        excluded_by_trades_filter + duplicates`; keep `selected` carried,
        not derived. Extend the `PageAccountingError` message to name the
        new term.
  - [ ] Success: constructing a violating `PageCounts` raises
        `PageAccountingError` whose message includes the filtered count.

- [ ] **Task 3.2: `PageCounts` unit tests** (effort: 1)
  - [ ] In `test/unit/data/kalshi/test_trade_repository.py`: identity
        holds with a nonzero filtered count; identity violation via the
        filtered term raises; zero filtered count reproduces today's
        behavior. Update existing constructions for the new arity.
  - [ ] Success: tests pass; only construction-arity changes to existing
        tests, no semantic changes.

- [ ] **Task 3.3: Extend `_write_page_statement` and the repository
      constructor** (effort: 3)
  - [ ] `_write_page_statement(rule, trades_excluded)`: in the
        `classified` CTE compute `selected` once and derive
        `tape_filtered` from it (`known AND selected AND` the Task 2.1
        membership test) — never paste the rule predicate twice. Insert
        stores `selected AND NOT tape_filtered`; returned counts gain
        `count(*) FILTER (WHERE tape_filtered)` and `selected_for_store`
        becomes `selected AND NOT tape_filtered`. Precedence per design:
        unknown → excluded-by-rule → excluded-by-trades-filter →
        stored/duplicate.
  - [ ] Change the constructor to
        `TradeRepository(conn, rule, *, trades_excluded, surface=…)` —
        required keyword, no default — and expose `trades_excluded` as a
        read-only property.
  - [ ] Update the only two production construction sites,
        `TradesPhase.run` and `HistoricalPhase.run` in
        `data/kalshi/collection_pass.py`, to pass
        `settings.kalshi_trades_excluded_categories`. Update test
        constructions (`test/integration/test_kalshi_trades.py`,
        `test/unit/data/kalshi/test_collection_pass.py`, and any other
        caller `grep -rn "TradeRepository(" ` finds) with an explicit
        `trades_excluded=frozenset()` unless the test is about the filter.
  - [ ] Success: no `TradeRepository` construction anywhere omits
        `trades_excluded`; unit tier passes.

- [ ] **Task 3.4: Repository integration tests (fixture catalog)**
      (effort: 3)
  - [ ] In `test/integration/test_kalshi_trades.py`, against the fixture
        catalog (throwaway database only):
    - [ ] Mixed page hitting all five buckets in one write: unknown /
          rule-excluded / tape-filtered / stored / duplicate rows; the
          extended identity holds.
    - [ ] Precedence: a category named in both the rule's exclusions and
          the trades filter counts as `excluded_by_rule`, never
          `excluded_by_trades_filter` (design Success Criterion 3).
    - [ ] NULL-category series: its trades store under any filter value
          (`COALESCE` to `''`, never in the filter) — must run against
          real SQL, not a mock.
    - [ ] Empty filter: rows stored and counts are identical to a run of
          the pre-change statement shape (bit-for-bit unset behavior,
          Success Criterion 1); `excluded_by_trades_filter == 0`.
    - [ ] Filtered rows are not written: re-query the fixture
          `kalshi.trades` for the filtered category after the write → 0.
  - [ ] Success: integration tier for `test_kalshi_trades.py` passes.

- [ ] **Task 3.5: Checkpoint commit** (effort: 1)
  - [ ] Commit Section 3 (e.g.
        `feat: filter trades tape by category in write statement`).

## Section 4: `TradeSync`, `TradeResult`, event, and loud validation

Design *Technical Decisions 4, 5, 9*; *Implementation Details: Counters
and events*.

- [ ] **Task 4.1: Totals, log lines, result, event** (effort: 2)
  - [ ] `data/kalshi/trade_sync.py`: accumulate
        `excluded_by_trades_filter` into window totals; per-window log
        line gains `filtered %d`; the phase start line appends
        `· trades filter: {describe_trades_filter(...)}` read from the
        repository property (one carrier — do not pass the set
        separately).
  - [ ] `data/kalshi/trade_types.py`: `TradeResult` gains the counter in
        `counts()` and `to_dict()`, name `excluded_by_trades_filter`
        everywhere, no abbreviations. `phase_finished` event `counts`
        carries it for both `trades` and `historical` (the historical
        core reuses `TradeSync.drain`/`TradeResult`, so verify it needs
        zero historical-specific code rather than adding any).
  - [ ] Success: a drain over a scripted tape reports the counter in
        totals, log lines, `counts()`, `to_dict()`, and the event; old
        journal rows without the key need no migration.

- [ ] **Task 4.2: Sync unit tests** (effort: 2)
  - [ ] In `test/unit/data/kalshi/test_trade_sync.py`: grow the existing
        scripted-tape fake to emit filtered rows; assert window totals,
        the `filtered %d` line, the start-line filter description, and
        the event payload. Zero-filter run reports 0 everywhere and
        leaves every existing assertion intact.
  - [ ] Success: tests pass; existing sync tests pass unmodified beyond
        arity.

- [ ] **Task 4.3: `UnknownTradesFilterCategoryError` validation**
      (effort: 2)
  - [ ] Per Decision 9: at trades-phase start, after the
        completed-catalog-walk guard, check every configured category
        against `SELECT DISTINCT category FROM kalshi.series`; any value
        present in no series row raises
        `UnknownTradesFilterCategoryError` naming the value and listing
        the catalog's known categories. Config abort — the phase result
        is never `PARTIAL`. Empty filter skips the check entirely.
  - [ ] The historical phase performs the same check before its drain.
        Implement the check once (shared helper), called from both
        phases — do not duplicate the query.
  - [ ] Success: error type exists, is raised pre-drain by both phases,
        and aborts the phase without touching watermarks or cursors.

- [ ] **Task 4.4: Validation tests** (effort: 2)
  - [ ] Against a fixture catalog that knows only `Crypto`: configuring
        `crypto` (lowercase) aborts both the trades and historical
        phases with the error naming `crypto` (design Success
        Criterion 9). A retired-but-once-real category (present only on
        historical series rows) does not abort. Empty filter runs no
        check.
  - [ ] Success: tests pass in whichever tier hosts them (the catalog
        query needs real SQL — integration tier, beside Task 3.4's
        tests, unless an existing fixture makes the unit tier honest).

- [ ] **Task 4.5: Historical-drain inheritance test** (effort: 2)
  - [ ] Via the existing 267 harness
        (`test/unit/data/kalshi/test_historical_sync.py` and/or the
        integration pass tests): a historical drain under a filter
        produces `excluded_by_trades_filter` counts, stores no filtered
        rows, and emits the counter in its `phase_finished` event —
        confirming inheritance with zero historical-specific code
        (design Success Criterion 4).
  - [ ] Success: test passes; no change to `historical_sync.py` beyond
        what Task 4.1 verified as unnecessary.

- [ ] **Task 4.6: Checkpoint commit** (effort: 1)
  - [ ] Commit Section 4 (e.g.
        `feat: account and validate trades filter in sync phases`).

## Section 5: Status command surfaces

Design *Technical Decision 6*; *Implementation Details: CLI rendering*.

- [ ] **Task 5.1: `trade_status.py` — filter facts and re-scoped
      buckets** (effort: 3)
  - [ ] Read `kalshi_trades_excluded_categories` from the same
        `Settings` the pass reads (the 264 Decision 2 invariant). Count
        **tape-filtered markets**: closed, rule-selected (`"ever"` form)
        markets whose category is in the filter. Markets only — never
        count rows in `kalshi.trades`.
  - [ ] Re-scope the four closed-market buckets
        (`complete_through_close`, `partial_history`, `short_of_close`,
        `before_coverage`) to rule-selected markets not tape-filtered;
        extend the partition check to
        `before + short + partial + complete + tape_filtered == total`.
  - [ ] Success: with an empty filter, every existing number is
        unchanged and `tape_filtered_markets == 0`; with a filter, the
        extended partition check holds.

- [ ] **Task 5.2: Renderer and JSON payload** (effort: 2)
  - [ ] `cli/commands/kalshi_status_render.py`: add the `trades filter`
        line — `describe_trades_filter(...)` plus the env var name; when
        a filter is set, a second line
        `tape-filtered N closed markets (stored history kept;
        completeness not evaluated)` (the F002 accepted-loss wording —
        keep it verbatim). `none` when empty, no second line.
  - [ ] JSON output gains `trades.filter` block:
        `excluded_categories` (sorted list), `tape_filtered_markets`.
  - [ ] Success: text and `--json` render from the same status object;
        wording matches the design's CLI rendering example.

- [ ] **Task 5.3: Status tests** (effort: 2)
  - [ ] `test/unit/data/kalshi/test_trade_status.py`: bucket re-scoping,
        partition check, empty-filter invariance.
  - [ ] `test/integration/test_kalshi_status.py`: filter line and JSON
        block present with a filter set; `none` and no block content
        change when unset (design Success Criterion 5).
  - [ ] Success: both test modules pass.

- [ ] **Task 5.4: Checkpoint commit** (effort: 1)
  - [ ] Commit Section 5 (e.g.
        `feat: surface trades filter in kalshi status`).

## Section 6: Documentation and architecture amendment

Design *Implementation Details: Operator documentation, Architecture
amendment*; Decisions 6, 7.

- [ ] **Task 6.1: README env-reference row** (effort: 1)
  - [ ] One row beside the `MT_KALSHI_COLLECTION_*` rows:
        variable, default (empty = no filtering), and the
        candles-continue semantics stated explicitly.
- [ ] **Task 6.2: `deploy/manta-trading.env.example` line** (effort: 1)
  - [ ] Commented `# MT_KALSHI_TRADES_EXCLUDED_CATEGORIES=` beside the
        five commented collection-rule lines, with the same one-line
        semantics note.
- [ ] **Task 6.3: Runbook `100-production-operations.md`** (effort: 1)
  - [ ] Add the variable to the Kalshi env-line enumeration for
        `/etc/manta-trading.env`; one sentence in the trades-phase
        description naming the filter, its counter, and the
        `tape_filtered_markets > 0` post-cutover check.
- [ ] **Task 6.4: Architecture amendment (Decision 7)** (effort: 1)
  - [ ] In `260-arch.kalshi-event-contract-data.md`, land Decision 7's
        amended wording verbatim: the *Design Goals* scope-of-complete
        paragraph and the completeness/caught-up definitions (the
        264/265 amendment pattern). Bump the doc's `dateUpdated`.
- [ ] **Task 6.5: Checkpoint commit** (effort: 1)
  - [ ] Commit Section 6 (e.g.
        `docs: document trades filter across operator surfaces`).

## Section 7: Full validation, walkthrough, and cutover

- [ ] **Task 7.1: Full validation pass** (effort: 2)
  - [ ] Unit tier and integration tier run separately (known-flake
        policy); mypy in the established single invocation covering the
        kalshi src paths and tests together; `ruff` scoped to files
        changed on this branch (`git diff --name-only main`).
  - [ ] Verify design Success Criterion 1 end-to-end: with the env
        unset, the full suite passes with no changes beyond
        `PageCounts` construction arity.
  - [ ] Success: all tiers green; any flake re-run in isolation before
        investigation.

- [ ] **Task 7.2: Refine the verification walkthrough** (effort: 1)
  - [ ] Update the slice design's *Verification Walkthrough* to match
        implemented reality (exact log wording, error text, JSON paths).
        Keep the Decision 8 precondition step (historical `floor
        reached` before setting the variable in production) and the
        typo-failure demo (walkthrough steps 6–7).
  - [ ] Success: every command in the walkthrough runs as written
        against a dev database; commit with the final code checkpoint.

- [ ] **Task 7.3: Cutover script** (effort: 2)
  - [ ] Write `scripts/cutover_268_trades_filter.py` following the
        `scripts/cutover_265_trades.py` pattern: (1) precondition —
        abort unless `mt data kalshi status` shows the historical tape
        at `floor reached` (Decision 8); (2) add
        `MT_KALSHI_TRADES_EXCLUDED_CATEGORIES=Crypto` to the daemon
        environment file; (3) restart the kalshi service unit;
        (4) report — filter line from status, the start-line
        `trades filter` entry from the unit journal, and
        `trades.filter.tape_filtered_markets > 0` from `--json`
        (the named post-cutover typo check). Print each check's result;
        nonzero exit on any failure.
  - [ ] Success: script is idempotent (safe to re-run), touches only the
        env file and the service unit, and every check maps to a
        walkthrough step 7 line.

- [ ] **Task 7.4: [PM] Production cutover** (effort: 1)
  - [ ] [PM] Merge the slice branch, tag and install the release on
        manta9000.
  - [ ] [PM] Run `scripts/cutover_268_trades_filter.py`; read its
        report.
  - [ ] Note (not a gate): WAL rate and `/data` growth are expected to
        drop toward the 5–15 GB/day steady state over subsequent days —
        observation only, per the design's Value section.
