---
docType: review
layer: project
reviewType: code
slice: trade-tape-category-filter
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/268-slice.trade-tape-category-filter.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260904
dateUpdated: 20260904
reviewedSha: 7483baf2fd6d4aba51f9b0736353b3e435c8ca5a
findings:
  - id: F001
    severity: concern
    category: dry-violation
    summary: "New cutover script duplicates `cutover_common.py` instead of importing it"
    location: "scripts/cutover_268_trades_filter.py"
  - id: F002
    severity: concern
    category: magic-value-duplication
    summary: "Env-var name hardcoded instead of referencing the single source of truth"
    location: "src/manta_trading/data/kalshi/trade_types.py:61"
  - id: F003
    severity: note
    category: documentation
    summary: "Module docstring not updated for the new fifth bucket"
    location: "src/manta_trading/data/kalshi/trade_status.py:10-27"
  - id: F004
    severity: note
    category: typing
    summary: "Un-parameterized `dict` return type in the cutover script"
    location: "scripts/cutover_268_trades_filter.py:86"
  - id: F005
    severity: pass
    category: correctness
    summary: "Trades-filter SQL logic is correct and matches its own precedence contract"
    location: "src/manta_trading/data/kalshi/trade_repository.py:160-189"
  - id: F006
    severity: pass
    category: correctness
    summary: "Filter validation (Decision 9) placed correctly and covered end-to-end"
    location: "src/manta_trading/data/kalshi/trade_sync.py:134-141"
---

# Review: code — slice 268

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] New cutover script duplicates `cutover_common.py` instead of importing it

`scripts/cutover_common.py` was created explicitly ("Extracted from `cutover_265_trades.py`... so `cutover_267_historical.py` does not re-spell them") to hold `CutoverError`, `say`, `run`, `out`, `unit_active`, `wait_for_pass_to_end`, `ENV_FILE`, `PASS_UNIT`, `POLL_SECONDS`, `production_status`, `fire`, and `read_journal`. `scripts/cutover_267_historical.py` correctly imports these from `cutover_common`. The new `scripts/cutover_268_trades_filter.py` imports nothing from `cutover_common` (only `manta_trading.config.KALSHI_TRADES_FILTER_ENV`) and instead re-defines `CutoverError`, `say`, `run`, `out`, `unit_active`, `wait_for_pass_to_end`, `ENV_FILE`, `PASS_UNIT`, `POLL_SECONDS` verbatim, and its `fire_pass()` is a near-line-for-line copy of `cutover_common.fire()` (same log text, same command, same post-run `sudo -v` call), while `journal_after()`/`report()` re-implement a cruder version of `read_journal()`/`Firing` (plain substring search on raw text vs. parsed, timestamped, byte-safe journal entries). This is a direct violation of CLAUDE.md's "Do not duplicate logic. Respect DRY" and reintroduces the exact drift risk `cutover_common.py` was written to prevent (e.g., if `PASS_UNIT` or the `mt-run kalshi` invocation ever changes, this script silently diverges).

### [CONCERN] Env-var name hardcoded instead of referencing the single source of truth

`config/__init__.py` defines `KALSHI_TRADES_FILTER_ENV = "MT_KALSHI_TRADES_EXCLUDED_CATEGORIES"` specifically so the name is "spelled once." `UnknownTradesFilterCategoryError.__init__` (trade_types.py:61) instead hardcodes the literal string `"MT_KALSHI_TRADES_EXCLUDED_CATEGORIES names ..."` in the exception message. `config` does not import `trade_types` (directly or transitively — it only imports `manta_trading.data.kalshi.selection`), so importing `KALSHI_TRADES_FILTER_ENV` into `trade_types.py` would not create a cycle. This is exactly the "value used... in a lookup/message must be defined once and referenced everywhere" case CLAUDE.md calls out; a future rename of the env var would silently leave this error message with the stale name, which is especially bad given the error's whole purpose is to help an operator diagnose a typo'd variable name.

### [NOTE] Module docstring not updated for the new fifth bucket

The `trade_status.py` module docstring still says "The four closed-market counts partition the selected closed markets" and enumerates only the four buckets. The `TradeStatus` class docstring, the `TRADE_COUNTS` SQL comment, and the code itself were all correctly updated to describe the fifth `tape_filtered` bucket extending the partition, but this top-of-file overview was not. Low impact (the accurate docs are one section down), but worth a one-line addition given how precisely this module's docs are otherwise maintained.

### [NOTE] Un-parameterized `dict` return type in the cutover script

`def production_status_json() -> dict:` uses a bare `dict` rather than `dict[str, Any]`, which the project's typing rules ("Type hint all function signatures") call for. `scripts/` is outside `[tool.mypy] files = ["src/manta_trading"]`, so this won't be mechanically caught, but it's inconsistent with the rest of the script's otherwise-thorough type hints (`list[tuple[bool, str]]`, `dict[str, object]`, etc.).

### [PASS] Trades-filter SQL logic is correct and matches its own precedence contract

`_write_page_statement`'s `classified`/`flagged` CTE chain computes `tape_filtered = known AND selected AND tape_test` from the single `selected`/`tape_test` values computed once, so rule-exclusion structurally wins over the trades filter (a row must be `selected` before it can be `tape_filtered`), and the insert/count predicates (`selected AND NOT tape_filtered`) keep `duplicates` exact. Verified against the accompanying `TestTradesFilter` integration tests (mixed-bucket, rule-wins, null-category, empty-filter parity) — the traced-through counts match the SQL's actual behavior.

### [PASS] Filter validation (Decision 9) placed correctly and covered end-to-end

`assert_trades_filter_known()` is invoked after the catalog-walk guard and before `drain()` in both the live (`trade_sync.py`) and historical (`historical_sync.py`) paths, so a typo'd category aborts loudly before any fetch/watermark movement in either surface, and an empty filter set skips the query entirely (verified in `TestTradesFilterValidation`, including the zero-statements-executed case). The empty-filter fast path and case-sensitive, retired-category-tolerant matching are all exercised with real SQL, not just fakes.
