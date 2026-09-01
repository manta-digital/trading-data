---
docType: review
layer: project
reviewType: code
slice: historical-backfill-phase
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/267-slice.historical-backfill-phase.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260901
dateUpdated: 20260901
reviewedSha: 91700af46b8b638105e5077fb3753b3ec0191d96
---

# Review: code — slice 267

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

Reviewed diff: `bd0b169^1..bd0b169^2` (pre-merge main `e5b84b7` → slice tip `91700af`).
Note: squadron's `--diff` mode printed this review to the terminal without writing an
artifact; this file was transcribed verbatim from that output. An earlier bare
`sq review code 267` run resolved an empty diff post-merge and produced a spurious
UNKNOWN result (preserved in `archive/`).

## Findings

[CONCERN] `migrate()` applies production ALTER migrations via an unverified ambient `.env`, with no target check
  category: production-db-safety

`migrate()` shells out to `uv run mt data migrate apply --track kalshi --json` from the dev checkout's cwd (no `mt-run`, no explicit DB-URL argument), relying on `_get_maintenance_url()` (`src/manta_trading/cli/commands/data.py:434`) resolving `MT_TIMESCALE_MAINTENANCE_URL` from whatever `.env` sits in that checkout. This is consistent with an existing, documented project design (`deploy/manta-trading.env.example:7-9`: the maintenance/DDL credential is deliberately kept out of `/etc/manta-trading.env` and migrations are "an operator action from an interactive shell," per slice 913's credential separation) — so this is not a new leak of production credentials into test/CI code, and the sub-agent's initial FAIL read was too severe given that context. However, `preflight()` (`scripts/cutover_common.py:72-88`) only checks that a file literally named `.env` *exists* — it never verifies its `MT_TIMESCALE_MAINTENANCE_URL` actually targets the same database as the production `mt-run`/`/etc/manta-trading.env` path used by `install()`/`fire()`/`restart_serve_if_active()`. Per the SQL rules' Production Database Protection section, destructive/maintenance tooling should "refuse to run when the target does not look like the database the operation expects (verify signature tables/rows first)." A stale or wrong-environment `.env` in the operator's checkout would let `migrate()` run `ALTER TABLE` migrations (`src/manta_trading/market/schema/migrations/kalshi.py`) against the wrong database while reporting success, after which the script proceeds to restart `mt-serve` against the real production DB with an unmigrated schema. Recommend adding a lightweight signature check (e.g. confirm a known table/row exists via the maintenance URL) before `migrate()` proceeds.

[NOTE] Dead `ENV_FILE` constant in cutover_common.py
  category: dead-code

`ENV_FILE = Path("/etc/manta-trading.env")` is declared but never referenced in `cutover_common.py` or `cutover_267_historical.py`. The predecessor `cutover_265_trades.py` used the equivalent constant to back up/rewrite the production env file for a variable rename; carrying the declaration over unused in the 265→267 extraction is dead code and worth removing or wiring in if a future release needs it.

[CONCERN] `log_unknown_prefixes` hardcodes the "trades" label, misidentifying historical-phase log lines
  category: correctness

The method's own docstring says "the historical core calls it after `drain` (slice 267)," and `historical_sync.py:198` does call it on a `TradeSync` built with `direction=WindowDirection.BACKWARD` over the historical surface. But the log line is hardcoded as `logger.info("trades unknown markets: %s", listed)` instead of using `self._label` (the same surface-derived property `drain`/`_window` already use, at lines 188 and 255, to distinguish "trades window …" from "historical window …"). An operator debugging unknown tickers from the historical backfill will see a misleading "trades unknown markets" line that looks like it came from the live phase. `test/unit/data/kalshi/test_trade_sync.py:308-314` only asserts the literal string for the forward/live path, so it didn't catch the mismatch for the backward/historical path.

[CONCERN] Trades-summary rendering block duplicated verbatim between CLI renderers
  category: dry-violation

`print_historical_summary`'s trades line and the `capped = " (capped)" if summary["capped"] else ""` line are byte-for-byte copies of the equivalent code in `print_trade_summary` (lines 133, 143-149 vs 170, 201-207). CLAUDE.md requires DRY; a future change to the trades-counter format now needs to be made in two places or the two phases will silently drift. Extract a shared helper (e.g. `_trade_counts_line(summary) -> str`).

[NOTE] `kalshi_render.py` has grown back past the ~300-line guideline it was created to enforce
  category: file-size

The file's docstring explains it was extracted from `kalshi.py` because that module exceeded the project's ~300-line guideline. This diff grows it from 327 to 421 lines. Consider splitting `print_historical_summary`/`print_historical_status` into a dedicated module, consistent with the prior extraction pattern.

[NOTE] Failed first-page archive request not counted toward request telemetry
  category: telemetry-accuracy

When `get_historical_markets` raises `ProviderPermanentError` on a page fetch and `resuming` is `False`, the code raises before incrementing `result.requests`, while every successful fetch and cursor-rejection retry does increment it. `run()`'s exception handler still emits `PHASE_FINISHED` with `result.counts()`, so the emitted request count undercounts by one on this abort path. Minor but verifiable accounting gap.

[NOTE] `FakeHistoricalSource` duplicates `FakeTradeSource`'s failure-injection engine instead of reusing it
  category: dry-violation

The `raise_on`/`_record`/`_failures` mechanism is copied verbatim (including docstrings) from `test/kalshi_support/fake_trade_source.py`, even though `FakeHistoricalSource` already composes `FakeTradeSource` elsewhere. Minor test-support duplication; low risk but worth sharing the mechanism if this file is touched again.

[PASS] Historical sync/migration core logic and test coverage

Archive-walk resume/restart logic, candle chunking, backward trade-drain wiring, cap enforcement, and the two new migrations (idempotent `DROP CONSTRAINT IF EXISTS` + re-render from enums, parameterized queries) are correct and well-covered by `test_historical_sync.py`, `test_historical_status.py`, and `test_kalshi_migrations.py`, including first-run seeding, cursor-rejection restart, slow-market warning, and both fresh-database and pre-existing-constraint migration paths.
