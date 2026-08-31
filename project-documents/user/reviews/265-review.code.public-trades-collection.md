---
docType: review
layer: project
reviewType: code
slice: public-trades-collection
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/slices/265-slice.public-trades-collection.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260830
dateUpdated: 20260830
reviewedSha: 35500ef80ab2bf9209f146bac8ad6ad901db1f9a
findings:
  - id: F001
    severity: note
    category: async-correctness
    summary: "`Settings.__init__` does synchronous file I/O on every construction"
    location: "src/manta_trading/config/__init__.py:105-116"
  - id: F002
    severity: note
    category: design-quality
    summary: "Excellent single-source-of-truth discipline and test depth"
    location: "src/manta_trading/data/kalshi/selection.py"
---

# Review: code — slice 265

**Verdict:** PASS
**Model:** claude-sonnet-5

## Findings

### [NOTE] `Settings.__init__` does synchronous file I/O on every construction

`reject_renamed_settings` (called from `Settings.__init__` before `super().__init__()`) does a blocking `dotenv_values(path)` read plus a full `os.environ` scan every time a `Settings` instance is built. Every current call site I could find (`cli/app.py`'s Typer `main` callback, the daemon `run_*_cycle` functions, `api_server/app.py`) is synchronous, so this doesn't currently violate the project's "sync code inside `async def` must be <1ms" rule. It's worth a mental flag for future call sites: if `Settings()` is ever constructed inside an `async def` (e.g., a future per-request settings reload), this guard would block the event loop on file I/O. Not an actionable defect today — just something to watch as the settings object gets reused in new contexts.

### [NOTE] Excellent single-source-of-truth discipline and test depth

Calling this out explicitly since it's the main thing I checked hardest for and didn't find broken: the collection rule (`CollectionRule`/`selection_sql`) was cleanly extracted from `candle_selection.py` into `selection.py` so candles and trades share one predicate renderer and one `CATALOG_JOIN`/`CATALOG_TABLES`; the `"any"` selection form (no traded-volume clause) is correctly wired through `trade_repository.write_page` and `trade_status.read_trade_status`. `PageCounts.__post_init__`'s accounting identity, the four-way partition in `trade_status.TRADE_COUNTS` (with a runtime `RuntimeError` cross-check), and the config rename guard (`reject_renamed_settings`, scanning both `os.environ` and the env file so a stale `.env` line can't silently revert to defaults) are all correctly implemented and backed by targeted unit/integration tests (including a bind-parameter-ceiling test for 1,000-row pages in one statement, and an explicit `NotNullViolation` vs `OperationalError` distinction test for `is_block_trade`). Exception handling throughout (`trade_sync.run`, `collection_pass.TradesPhase.run`) follows the CLAUDE.md re-raise-after-`logger.exception` pattern with no bare/broad swallowing.
