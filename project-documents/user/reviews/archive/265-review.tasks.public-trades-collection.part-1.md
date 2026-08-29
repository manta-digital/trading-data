---
docType: review
layer: project
reviewType: tasks
slice: public-trades-collection
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/265-tasks.public-trades-collection-1.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260829
dateUpdated: 20260829
reviewedSha: d1208310e79c72ee6a42d7ed5c5861387fb5da4a
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "The rename guard as specified misses `.env`, the exact silent-fallback it exists to prevent"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:178"
  - id: F002
    severity: concern
    category: missing-interface
    summary: "No task defines how the core reads the catalog walk start, which the window end depends on"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:307-320"
  - id: F003
    severity: concern
    category: test-coverage
    summary: "The `PageCounts` accounting identity is specified so that it asserts nothing"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:336-343"
  - id: F004
    severity: concern
    category: error-handling
    summary: "Task 2.2 introduces a silent fallback for `is_block_trade` that the design never decided"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:260-264"
  - id: F005
    severity: concern
    category: test-coverage
    summary: "Task 1.1's \"byte-identical\" success criterion cannot be checked by the test Task 1.5 specifies"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:132-134"
  - id: F006
    severity: note
    category: test-coverage
    summary: "Task 3.2's one-statement-per-page success criterion has no corresponding assertion in Task 3.3"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:345-347"
  - id: F007
    severity: note
    category: completeness
    summary: "Two rename touchpoints named in the design have no task bullet"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:189"
  - id: F008
    severity: note
    category: task-sizing
    summary: "Task 4.3 bundles new test infrastructure with eleven behavioral tests"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:435"
  - id: F009
    severity: note
    category: test-coverage
    summary: "No load-test task, consistent with the 264 precedent; no CI gate exists to wire one into"
    location: ".github/workflows/ci.yml:1-5"
  - id: F010
    severity: pass
    category: coverage
    summary: "Every Success Criterion in part 1's remit traces to a task, and no task lacks a criterion"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md"
  - id: F011
    severity: pass
    category: sequencing
    summary: "Sequencing is correct, acyclic, and the cited code locations are accurate"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md"
  - id: F012
    severity: pass
    category: process
    summary: "Commit checkpoints are one per section, not batched at the end"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:221"
---

# Review: tasks — slice 265

**Verdict:** CONCERNS
**Model:** claude-opus-5

## Findings

### [CONCERN] The rename guard as specified misses `.env`, the exact silent-fallback it exists to prevent

Task 1.4 specifies a `model_validator(mode="after")` that "scans `os.environ` for any key starting with the old prefix". But `Settings` is configured with `env_file=ENV_FILE` (`src/manta_trading/config/__init__.py:23,34`) and `extra="ignore"`, and the existing field comments explicitly describe `.env` authorship ("A .env author writes ``Sports, Mentions``"). A stale `MT_KALSHI_CANDLE_EXCLUDED_CATEGORIES=` line in `.env` is never in `os.environ`, so the guard passes, pydantic-settings ignores the key, and the rule silently reverts to defaults — the precise failure Decision 3 and CLAUDE.md ("Never use silent fallback values") forbid. Systemd's `EnvironmentFile` puts production values in `os.environ`, so this hole is invisible on the host and only bites developers and the rehearsal. The task should require the scan to cover both `os.environ` **and** the parsed `env_file` values (`dotenv_values(ENV_FILE)`), and Task 1.5's parametrized guard test should cover the `.env` path as well as the environment path.

### [CONCERN] No task defines how the core reads the catalog walk start, which the window end depends on

Task 4.2 step 2 (line 409) requires the window end = `sync_state['catalog'].last_full_sync_at − TRADE_LATE_ARRIVAL_GUARD`, and Task 4.3 test 2 asserts the last window is clamped to it. But `TradeSync` has no SQL by design, and Task 3.1's enumerated `TradeRepository` surface is only `read_state`, `init_state`, `advance_watermark`, `set_last_full_sync`, `transaction` — no catalog-state reader. `CatalogRepository.get_sync_state(surface)` exists (`src/manta_trading/data/kalshi/repository.py:258`) and returns `last_full_sync_at`, so the plumbing is available, but the seam is unnamed. Consequence: the implementer of Task 4.2 either reaches into `CatalogRepository` from the core (breaking the Protocol boundary the slice insists on) or invents a method name that Task 4.3's `fake_trade_repository.py` was not specified to provide, and the "no catalog row → nothing fetched" case (Task 4.2 step 2, Task 4.3 test 3, Criterion 7) has no defined way to be simulated. Add the reader (e.g. `read_catalog_walk_start() -> datetime | None`) to Task 3.1's list and to the fake in Task 4.3.

### [CONCERN] The `PageCounts` accounting identity is specified so that it asserts nothing

Task 3.2 says `PageCounts` carries `fetched, written, unknown_market, excluded_by_rule` and that "`duplicates` is derived as `selected − written`" — but `selected` is not one of the carried fields, so the only way to derive it is `selected = fetched − unknown − excluded`. Substituting that into the identity the task then demands be asserted structurally in `__post_init__` (`fetched = written + unknown + excluded + duplicates`) yields `fetched = fetched`. Criterion 2 is "an exact accounting", and the task's own words say "make it structural", yet the assertion as specified can never fail. The design's SQL (`Repository` block) returns four values — `unknown`, `excluded`, `selected`, `written` — so `selected` must be carried from SQL and `fetched` set independently from `len(rows)`; only then does the assertion actually catch a page whose rows failed to reach `classified`. Task 3.3's per-case identity assertion inherits the same vacuity.

### [CONCERN] Task 2.2 introduces a silent fallback for `is_block_trade` that the design never decided

The design's migration makes `is_block_trade BOOLEAN NOT NULL` and Decision 11 lists it as a served field; 261's model types it `bool | None` (`src/manta_trading/data/kalshi/models.py:153`). Task 2.2 resolves the mismatch on its own authority by having `write_page` coalesce a missing value to `FALSE`, justified by "the recorded fixture and the 352,000-trade sample carry it on every row". That justification argues the opposite way: if the field is always served, coalescing costs nothing to omit and silently mislabels a real block trade as non-block on the one day Kalshi omits it. It is also inconsistent with the same slice's handling of the parallel mismatch — a non-UUID `trade_id` is required to "fail the write loudly" (Task 3.3 case 6) — and with CLAUDE.md's ban on silent fallback values. Either fail loudly on `None` (consistent, no design change) or route the choice back to the design as a decision; do not settle it in a task bullet.

### [CONCERN] Task 1.1's "byte-identical" success criterion cannot be checked by the test Task 1.5 specifies

Task 1.1 states: "Success: `candle_selection.MARKET_JOIN` renders byte-identical SQL to the pre-rename version (assert in Task 1.5)". Task 1.5's corresponding test is "assert `CATALOG_JOIN`'s rendered text is a prefix of `MARKET_JOIN`'s" — that proves composition, not equivalence to the pre-rename text, and the pre-rename text no longer exists once the `git mv` lands. There is currently no `MARKET_JOIN` assertion anywhere in `test/unit/data/kalshi/test_selection_sql.py` to serve as the baseline. Since Section 1 claims to add no behavior and Criterion 5's last clause is "the candle phase behaves exactly as before the rename", the plan should require capturing the rendered `MARKET_JOIN` string as a literal snapshot *before* the move (a first bullet in Task 1.1), then asserting equality afterwards.

### [NOTE] Task 3.2's one-statement-per-page success criterion has no corresponding assertion in Task 3.3

Task 3.2's success is "a page of 1,000 rows issues exactly one statement (assert via a counting cursor or the connection's query log in the integration test)", but Task 3.3's enumerated cases (six predicate cases, parity, `advance_watermark`, `init_state`) do not include it. A criterion whose only verification lives in another task's unlisted scope tends to be dropped. Add it explicitly to Task 3.3.

### [NOTE] Two rename touchpoints named in the design have no task bullet

(a) Task 1.4 renames `deploy/manta-trading.env.example` lines 25–29 "text otherwise unchanged", but lines 22–24 of that file are a header comment reading "Kalshi **candle** collection rule (slice 264): which markets the **candle phase** collects" — after Decision 3 that description is wrong, and the file is the operator's reference during the walkthrough step 8 rename. (b) The design's *Settings — the rename* requires that `traded_only` "is documented as not applying to trades"; Task 1.3 documents `SelectionForm` but no task documents `traded_only`'s surface asymmetry on the settings field itself. Both are one-line additions to Task 1.4.

### [NOTE] Task 4.3 bundles new test infrastructure with eleven behavioral tests

Task 4.3 (effort 5, tied for the largest in the file) creates two new fake modules in `test/kalshi_support/` *and* eleven distinct behavioral tests spanning six Success Criteria. The fakes are reusable infrastructure with their own shape decisions (recorded `min_ts`/`max_ts`/`cursor` per call, in-memory state) and are a natural split point — and, per the finding above, the fake repository's surface is currently under-specified. Splitting at the fakes boundary would also let Task 4.2 be exercised incrementally. Not blocking.

### [NOTE] No load-test task, consistent with the 264 precedent; no CI gate exists to wire one into

The slice restates no NFR as a threshold: its workload figures (~420 k trades/hour, ~3,000 requests/pass, ~15 min pass during the drain, per-window wall time) are measurements and derived estimates, and Criterion 13's "~7 hours per firing" is an observation the PM watches, not a bound. This matches the explicit ruling recorded for the sibling slice (`264-tasks.candlestick-collection-1.md:579`: "no load test is required… adding a `test/load/` task would invent a bound the design declined to set"). Note for completeness that the repository's only CI workflow (`.github/workflows/ci.yml`) runs on `tags: ["v*"]` and does build-and-publish only — it runs no test tier at all — so there is no CI gate to wire a `test/load/` case into even if one were added; the unit and integration tiers, and the compression proof in Task 2.3, are gated locally by the per-section gate tasks. If the PM ever wants the request-budget or per-window timing enforced rather than observed, that is a new slice-level decision, not a fix to this breakdown.

### [PASS] Every Success Criterion in part 1's remit traces to a task, and no task lacks a criterion

Criterion 1 → Task 4.4; 2 → Tasks 3.2/3.3, 4.3 test 4; 3 → Task 3.3 case 4; 4 → Task 4.3 tests 5–6; 5 → Section 1 in full (1.4 guard, 1.5 tests, 3.3 case 5); 6 → Tasks 4.2 step 1, 4.3 tests 1 and 8; 7 → Tasks 4.2 step 2, 4.3 tests 2–3; 8 → Tasks 2.1, 4.2 step 3, 4.3 test 7; 9 → Tasks 4.2, 4.3 test 10, 3.3 case 2; 12 → Task 2.3's real-chunk compression case. Criteria 10, 11, 13 are explicitly deferred to part 2 (Sections 6, 5, 9 respectively), and the hand-off note at line 519 names them. In the other direction, no task in Sections 1–4 lacks a criterion or design section: the only additions beyond the design text are the `selection_sql` bound-parameter rename (Task 1.2) and the structural accounting assertion (Task 3.2), both small and reasoned in place.

### [PASS] Sequencing is correct, acyclic, and the cited code locations are accurate

The order rename → constants/migration → repository → core/phase respects every real dependency: Task 3.2's `selection_sql(rule, "any")` comes from Task 1.3; Task 3.3's integration tests need the table from Task 2.2; Task 4.2 needs `TradeRepository` from Section 3 and the constants from Task 2.1; Task 4.6's `PHASE_RENDERERS[TRADES]` follows Task 4.4's enum member. Tests immediately follow their implementation in every section (1.5 after 1.1–1.4, 2.3 after 2.2, 3.3 after 3.2, 4.3 after 4.2, with 4.4–4.6 carrying inline test bullets). I verified every code reference the file cites: `cli/commands/kalshi.py:234`, `collection_pass.py:250`, `kalshi_render.py:223`, `manta-trading.env.example:25-29`, `test_data_kalshi.py:347`, `test_units.py:145-149`, and "`status.py` is already 309 lines" are all exact; `_interval_sql`, `WINDOW_OVERLAP`, `Surface.TRADES`, `PHASE_RENDERERS`, `record_trades`/`RECORDERS`, `test_status_imports.py` and the `test/kalshi_support/` fakes all exist as described; the Task 1.5 test-file list is complete against `grep` for `CandleRule`/`candle_rule`/`MT_KALSHI_CANDLE_`; and the branch name matches the `261-`/`262-`/`263-`/`264-slice.*` precedent in `git branch`.

### [PASS] Commit checkpoints are one per section, not batched at the end

Tasks 1.6, 2.4, 3.4 and 4.7 each pair the gate run (ruff scoped to touched files, mypy + pyright in a single invocation per the kalshi_support path artifact) with a semantic commit, distributed across the four sections — matching the PM-confirmed checkpoint-per-section granularity. Commit messages use correct semantic prefixes (`refactor:` for the rename, `feat:` for the migration, repository, and phase).
