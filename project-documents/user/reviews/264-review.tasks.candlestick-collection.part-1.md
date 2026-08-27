---
docType: review
layer: project
reviewType: tasks
slice: candlestick-collection
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/264-tasks.candlestick-collection-1.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260826
dateUpdated: 20260826
reviewedSha: 1abefcd0c0d6d0c75fc177dd3b0b3d2c3547a0be
findings:
  - id: F001
    severity: concern
    category: coverage-gap
    summary: "Repository has no way to produce `backlog_remaining` or `behind_cutoff`, which the core must emit"
    location: "project-documents/user/tasks/264-tasks.candlestick-collection-1.md:395"
  - id: F002
    severity: concern
    category: test-infrastructure
    summary: "`kalshi_helpers.write_catalog` cannot build the predicate fixture set Task 4.4 specifies"
    location: "project-documents/user/tasks/264-tasks.candlestick-collection-1.md:422-433"
  - id: F003
    severity: concern
    category: correctness
    summary: "The selection predicate's NULL behaviour is unspecified and untested"
    location: "project-documents/user/tasks/264-tasks.candlestick-collection-1.md:361-367"
  - id: F004
    severity: concern
    category: test-coverage
    summary: "Criterion 1's candle-abort clause has no task"
    location: "project-documents/user/tasks/264-tasks.candlestick-collection-2.md:140-144"
  - id: F005
    severity: concern
    category: task-sizing
    summary: "Task 5.2 is the outlier in size and should be split at the batch boundary"
    location: "project-documents/user/tasks/264-tasks.candlestick-collection-2.md:61-92"
  - id: F006
    severity: note
    category: test-coverage
    summary: "`selection_sql`'s clause-omission matrix has no unit test"
    location: "project-documents/user/tasks/264-tasks.candlestick-collection-1.md:374-375"
  - id: F007
    severity: note
    category: coverage-gap
    summary: "Rehearsal does not record the pending queries' wall time the design asks for"
    location: "project-documents/user/tasks/264-tasks.candlestick-collection-2.md:246-263"
  - id: F008
    severity: note
    category: nfr-coverage
    summary: "No load test is required for this slice, and none is missing"
    location: "project-documents/user/slices/264-slice.candlestick-collection.md:415-430"
  - id: F009
    severity: pass
    category: traceability
    summary: "Every success criterion maps to at least one task, and every task traces back"
    location: "project-documents/user/tasks/264-tasks.candlestick-collection-1.md:97-449"
  - id: F010
    severity: pass
    category: sequencing
    summary: "Sequencing, checkpoint distribution, and the host boundary"
    location: "project-documents/user/tasks/264-tasks.candlestick-collection-1.md:88-95"
---

# Review: tasks — slice 264

**Verdict:** CONCERNS
**Model:** claude-opus-5

## Findings

### [CONCERN] Repository has no way to produce `backlog_remaining` or `behind_cutoff`, which the core must emit

Section 4 gives `CandleRepository` exactly `selection_sql`, `pending_live`/`pending_finishing`/`pending_backlog`, `insert_candles`, `advance_state`, `set_sync_state`, and `transaction()`. No count method exists. But part 2's Task 5.2 (`264-tasks.candlestick-collection-2.md:86-88`) requires `phase_finished` to carry "the counts plus `backlog_remaining` and `behind_cutoff`", and `CandleResult.to_dict()` (Task 5.1, and the design's JSON sketch at slice line 312) has `pending.backlog_remaining` alongside `pending.backlog`. Those are different numbers by construction: `pending_backlog` is capped at `CANDLE_BACKLOG_REQUESTS_PER_PASS × CANDLE_BATCH_MAX_TICKERS` rows, so the remainder cannot be derived from what it returns. The core is forbidden from issuing SQL itself (Section 5 preamble: "no SQL"), so this has nowhere to live.

Failure scenario: an implementer finishing Task 5.2 finds no repository call for `backlog_remaining`, and either (a) writes ad-hoc SQL inside `candle_sync.py`, breaking the module boundary the design's Architecture section states, or (b) emits `backlog_remaining` as a placeholder derived from `len(backlog_rows)`, which equals the cap on every pass until the backlog drains — silently defeating Criterion 8 ("`backlog_remaining` decreasing pass over pass") and the rehearsal check in Task 7.4. Add two counting methods (`count_backlog_remaining(period, cutoff)`, `count_behind_cutoff(period, cutoff)`, both via `selection_sql(rule, "ever")`) to Task 4.2 or 4.3, with assertions in Task 4.4.

### [CONCERN] `kalshi_helpers.write_catalog` cannot build the predicate fixture set Task 4.4 specifies

Task 4.4 says to build the five-market predicate fixture set "using the `kalshi_db` fixture and `kalshi_helpers.write_catalog`", with "synthesized series" carrying a Sports category, a `Mentions` category, and a mention-titled series in another category. `write_catalog` (`test/integration/kalshi_helpers.py:50-59`) derives its series from `parent_series(events)`, which builds `km.Series(ticker=t)` — ticker only. `category` and `title` both default to `None` on the model (`data/kalshi/models.py:43-44`), and `write_catalog` takes no series argument. The helper as it stands can only produce category-less, title-less series.

Failure scenario: an implementer runs Task 4.4 with `write_catalog`, every series lands with `category IS NULL` and `title IS NULL`, and the assertions "under the default rule only the traded Politics market is returned" pass for the wrong reason (see the NULL finding below) or fail confusingly — Criterion 2's integration proof is then either vacuous or blocked. Add a bullet to Task 4.4 (or a small task before it) extending `kalshi_helpers` with an explicit-series writer, e.g. `write_catalog(repo, markets, series=None)` that uses caller-supplied `km.Series` rows when given.

### [CONCERN] The selection predicate's NULL behaviour is unspecified and untested

Task 4.1 spells the exclusion clauses as `NOT (s.category = ANY(%s))`, `s.ticker !~ %s`, and `s.title !~* %s`. `series.category` and `series.title` are both nullable (`data/kalshi/models.py:43-44`), and every one of those operators yields NULL — not TRUE — on a NULL left operand, so a series with no category or no title is silently *excluded* from collection. The slice's own universe table (slice line 62) counts a "Companies / Social / World / unknown" cohort of 588 open markets, i.e. this case exists in production data. Nothing in Task 4.1 says which way it should go, and Task 4.4's fixture set (five markets, all with categories) cannot detect it either way.

Failure scenario: a Kalshi series with `category = NULL` (or a series whose `title` is null) is never selected for candles and never appears in `closed_excluded_by_rule` as an intentional exclusion — coverage is lost with no report of the loss, which is precisely the honesty property Criterion 12 and the design's "exclusions are visible" goal exist to guarantee. Specify the intent in Task 4.1 (`s.category IS DISTINCT FROM ALL(...)` / `COALESCE(s.title, '') !~* %s`, or an explicit decision to drop NULL-category series) and add a sixth fixture market with a NULL-category, NULL-title series to Task 4.4.

### [CONCERN] Criterion 1's candle-abort clause has no task

Criterion 1 (slice line 417) has three clauses: the pass reports `["catalog", "candles"]`; a catalog abort leaves the candle phase `skipped`; and **a candle abort leaves the catalog phase's outcome and state intact**. Task 5.5 covers the first two ("`PASS_PHASES` is exactly `(CatalogPhase(), CandlesPhase())` by name and order"; "a catalog abort leaves the candle phase `skipped`"). Task 7.1's end-to-end test covers phase order but not a candle abort. No task anywhere asserts the third clause, and the design's own proof table (slice line 516) doesn't name it either.

Failure scenario: `CandlesPhase` raises `psycopg.OperationalError` mid-batch; if the phase is wired to roll back or overwrite pass-level state, the catalog phase's `sync_state` writes (already committed) could be reverted or its `PhaseReport` outcome downgraded, and nothing in the suite notices. This is the abort-rule contract 263 established and 265 will copy. Add a bullet to Task 5.5: a pass whose candle phase aborts still reports the catalog phase's original outcome and leaves `sync_state['catalog']` unchanged.

### [CONCERN] Task 5.2 is the outlier in size and should be split at the batch boundary

Task 5.2 carries effort 5 (the file's maximum) and eight substantive bullets spanning all six Data Flow steps: cutoff read, three pending sets, target mapping, batch planning, per-batch transaction semantics, the sparseness advance rule, the omitted-ticker item-error path, the abort-on-`ProviderError` rule, sequential-execution constraint, two distinct INFO log lines, terminal `sync_state` write, event emission, and the backlog-only cap. Its test task (5.3, also effort 5) then covers nine behaviours in one go.

Failure scenario: a junior implementer completes the batch loop, declares 5.2 done, and the terminal `sync_state` write and `phase_finished` counts arrive half-formed — the checklist has no intermediate box to fail against, so partial completion is indistinguishable from completion until Task 5.3's nine-behaviour test task fails in aggregate. Split at a natural seam: 5.2a = cutoff → three pending sets → `target_window` mapping → `plan_batches` (Data Flow 1–4); 5.2b = per-batch fetch/write transaction, item errors, abort rule, progress logging, terminal state and events (Data Flow 5–6). Split 5.3 to follow each half, preserving the test-with pattern.

### [NOTE] `selection_sql`'s clause-omission matrix has no unit test

Task 4.1's success bullet reads "a unit-level call returns a `Composed` whose parameter list matches the clauses present", but no unit test task exists for it — Section 4's only test task (4.4) is integration-tier and asserts row outcomes, not clause structure. The "omitted entirely when its setting is empty" behaviour (five settings, each independently omittable) is combinatorial and cheap to test without a database. Not blocking: the semantic outcomes are proven in 4.4, and the design's Tests section also routes this to integration. Consider adding one unit test file asserting the rendered parameter list per configuration, which also documents the always-true empty-rule case Task 4.1 line 370 requires.

### [NOTE] Rehearsal does not record the pending queries' wall time the design asks for

The design's *Special Considerations* (slice line 569) states the pending queries join `markets` (3.5 M+ rows) to `events` and `series` once per pass each and that "rehearsal records their wall time", with a partial index named as the first lever if the backlog query dominates. Task 7.4 records passes, counts, the rule listing, and the duplicate check, but not per-query timing; Task 8.2 records the *phase* wall time on the host, which is the Decision 9 evidence, not the per-query evidence. One `\timing` bullet in 7.4 would close it.

### [NOTE] No load test is required for this slice, and none is missing

The review checklist asks whether a restated NFR needs a `test/load/` task and a CI gate. Neither the slice nor its parent (`260-slices.kalshi-event-contract-data.md`) contains an NFR section or the string "NFR" — the workload numbers in *Discovery Findings* and Decision 9 are capacity estimates whose stated proof is a recorded rehearsal measurement, not a threshold. That matches sibling slices 261–263, none of which have load tests, while the NFR-bearing slices in `test/load/` (146, 167, 169, 187) all do. Separately, CI gating is not left implicit by omission: `.github/workflows/ci.yml` is a publish-on-tag workflow that runs no tests at all in this repo, and gates run locally via `scripts/run_tests.py` — which Task 7.3 explicitly invokes.

### [PASS] Every success criterion maps to at least one task, and every task traces back

Criterion → task: 1 → 5.4/5.5/7.1; 2 → 1.2/1.3/4.1/4.4/5.3/7.4; 3 → 4.3/5.2/7.1; 4 → 4.4/7.1/7.4; 5 → 3.2/3.3/7.4/8.2; 6 → 3.1/3.3/4.4/6.3; 7 → 4.2/7.1; 8 → 4.2/5.2/7.1/8.3; 9 → 4.4/6.3; 10 → 5.2/5.3/7.1; 11 → 2.2/2.3/7.4/8.1; 12 → 6.1/6.2/6.3; 13 → 2.3/7.2; 14 → 8.1/8.2/8.3. In the other direction, no task lacks a design anchor — including the two that could read as additions: Task 5.4's renderer dispatch is named in the design's Component Structure (slice line 149) and is a genuine pre-existing defect, and Task 8.4's process-journal entry closes the Risk Assessment's open question about Kalshi revising completed candles (slice line 534).

### [PASS] Sequencing, checkpoint distribution, and the host boundary

Dependencies flow strictly forward with no cycles: constants (1.1) precede the migration that renders intervals from them (2.1) and the planner that reads the caps (3.3); `candle_types.py` is created in 1.2 and extended in 5.1; `selection_sql` (4.1) precedes both its consumers, the pending queries (4.2) and `status` (6.1); the migration (2.1) precedes every integration test that needs the table. Commits land at 1.6, 2.3, 3.3, 4.4, 5.5, 6.3, 7.3, 7.4, 7.5, and 8.4 — one per section plus three in the documentation/rehearsal stretch, consistent with the project's per-section checkpoint convention, not batched at the end. Test tasks immediately follow their implementation tasks in all eight sections. No task waits on wall-clock time: Task 8.3 explicitly runs a second pass on demand rather than waiting for the `:20` firing, and no merge or branch step is encoded as a task (part 1 line 82 keeps them out).
