---
docType: review
layer: project
reviewType: tasks
slice: candlestick-collection
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/264-tasks.candlestick-collection-2.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260826
dateUpdated: 20260826
reviewedSha: 1abefcd0c0d6d0c75fc177dd3b0b3d2c3547a0be
findings:
  - id: F001
    severity: concern
    category: task-sizing
    summary: "Section 5 carries 17 effort points to one commit, and Tasks 5.2/5.3 are out of family"
    location: "project-documents/user/tasks/264-tasks.candlestick-collection-2.md:61-113"
  - id: F002
    severity: concern
    category: test-coverage
    summary: "The `CandleResult` JSON round-trip test has no owning task"
    location: "project-documents/user/tasks/264-tasks.candlestick-collection-2.md:58"
  - id: F003
    severity: note
    category: test-coverage
    summary: "Criterion 1's third clause is never asserted for the candle phase"
    location: "project-documents/user/tasks/264-tasks.candlestick-collection-2.md:141-146"
  - id: F004
    severity: note
    category: traceability
    summary: "Task 7.4 does not name the step-4 query that proves Criterion 6 in the rehearsal"
    location: "project-documents/user/tasks/264-tasks.candlestick-collection-2.md:246-263"
  - id: F005
    severity: pass
    category: correctness
    summary: "The renderer-dispatch fix in Task 5.4 is real, correctly diagnosed, and correctly scoped"
    location: "src/trading_data/cli/commands/kalshi_render.py:104-106"
  - id: F006
    severity: pass
    category: sequencing
    summary: "Sequencing, dependencies, and commit distribution"
    location: "project-documents/user/tasks/264-tasks.candlestick-collection-2.md"
  - id: F007
    severity: pass
    category: test-coverage
    summary: "No load-test or CI-gating task is required for this slice"
    location: "test/load"
  - id: F008
    severity: pass
    category: task-scoping
    summary: "Host boundary and no wait-blocked tasks"
    location: "project-documents/user/tasks/264-tasks.candlestick-collection-2.md:289-339"
---

# Review: tasks — slice 264

**Verdict:** CONCERNS
**Model:** claude-opus-5

## Findings

### [CONCERN] Section 5 carries 17 effort points to one commit, and Tasks 5.2/5.3 are out of family

Every other task in both files is effort 1–3; Task 5.2 (`CandleSync.run()`) and Task 5.3 (core unit tests) are each effort 5, and the section's only checkpoint is at line 151, after all five tasks. Task 5.2 alone encodes eight distinct behaviors (cutoff read, three pending sets, target mapping, planning, per-batch transaction with the sparseness upsert rule, the omission item-error path, the provider-abort rule, progress logging, terminal `sync_state` + events) into a module its own success bullet caps at ~300 lines. Task 5.3 bundles two different kinds of work — building `FakeCandleSource` plus candle methods on `fake_repository.py` — with nine separate assertions.

Suggested split, which also gives Section 5 an interim checkpoint consistent with the rest of the file:
- 5.2a — `run()` skeleton: cutoff, three pending sets, `target_window` mapping, `plan_batches`, terminal `sync_state`/`phase_finished`.
- 5.2b — the batch loop: one transaction per batch, conflict-ignore insert, the advance-on-empty-response rule, the omitted-ticker item error, the `ProviderError` abort, progress cadence.
- 5.3a — test doubles (`FakeCandleSource`, `fake_repository` candle methods with the existing `fail_on` pattern), then a **commit** (`test: add kalshi candle test doubles`).
- 5.3b — `test_candle_sync.py` assertions.

### [CONCERN] The `CandleResult` JSON round-trip test has no owning task

The design's *Tests* section lists "JSON round-trip" as a required unit test under *Unit — pass and rendering*, and the codebase already has the precedent (`test/unit/data/kalshi/test_collection_pass.py:218`, `test_to_dict_round_trips_through_json` for `PassResult`). In this breakdown the round-trip appears only as a **Success** bullet on Task 5.1 — an implementation task — and neither Task 5.3 nor Task 5.5 lists it among the tests to write. A junior implementer can satisfy a "Success:" bullet with a one-off REPL check and leave nothing committed; the phase's `summary` then reaches `--json` unguarded, and a `datetime` leaking into `pending` or `item_errors` surfaces only when the CLI raises at serialization time.

Add the assertion explicitly to Task 5.5's test list: `CandleResult.to_dict()` survives `json.dumps` with the design's exact key set, including a non-empty `item_errors` and a non-null `cutoff`.

### [NOTE] Criterion 1's third clause is never asserted for the candle phase

Criterion 1 has three clauses: two phases in order, a catalog abort skips candles, and *a candle abort leaves the catalog phase's outcome and state intact*. Task 5.5 covers the first two; the third is not named in any task. It is structurally guaranteed by 263's `CollectionPass` (`test/unit/data/kalshi/test_collection_pass.py:156`, `test_abort_skips_the_remainder`, plus `test_every_ordered_pair_takes_the_worst`), so this is very likely already true rather than a real hole — but the criterion is restated in this slice, and one line in Task 5.5 ("a `CandlesPhase` abort leaves the catalog `PhaseReport` at `ok` and its `sync_state` unchanged") closes it cheaply.

### [NOTE] Task 7.4 does not name the step-4 query that proves Criterion 6 in the rehearsal

The design's *where each is proven* table assigns Criterion 6 (first sight, `coverage_from_ts`, `partial_history`) a rehearsal proof of "step 4 partial/complete" — the `count(*) filter (where s.coverage_from_ts > m.open_time)` / `watermark_ts >= close_time + interval` query in walkthrough step 4. Task 7.4's record list names steps 1, 2, 3, 4 ("the first pass with its phase lines and counts") and 5, but does not call out that query, and Task 7.1's end-to-end assertions list Criteria 1, 3, 4, 7, 8, 10 — not 6. Criterion 6 is genuinely covered at the unit and repository level (p1 3.3, 4.4) and in `status` (p2 6.3), so this is a rehearsal-record gap, not a verification gap. Add "the partial/complete counts from step 4 (Criterion 6)" to Task 7.4's record list.

### [PASS] The renderer-dispatch fix in Task 5.4 is real, correctly diagnosed, and correctly scoped

Verified against the source: `print_pass_summary` calls `print_phase_summary(report.summary)` unconditionally for every report with a non-empty summary, and `print_phase_summary` indexes `summary["phases"]`, `["transitions"]`, `["awaiting"]`, `["windows_completed"]`, `["settled_captured"]` (lines 59-79) — all catalog-only keys. A candles summary would raise `KeyError` on the first pass that runs both phases, exactly as Task 5.4 states. Calling it a required fix rather than an enhancement is right, and the added requirement that an unregistered phase name fail loudly rather than print nothing is consistent with the project's no-silent-fallback rule.

### [PASS] Sequencing, dependencies, and commit distribution

No circular or violated dependencies: 5.1 (types) → 5.2 (core) → 5.4 (phase, needs `CandleSync`) → 5.5; 6.1 (`read_candle_status`) → 6.2 (CLI wiring) → 6.3; 7.1 needs Sections 5–6 and part 1; 8.4 consumes 7.4 and 8.1–8.3. Test tasks immediately follow their implementation tasks in every case (5.3 after 5.1/5.2, 5.5 after 5.4, 6.3 after 6.1/6.2). Both cross-file dependencies are stated up front — the frontmatter `projectState` and the Context Summary both require part 1 Sections 1–4 first, and Task 5.1 correctly extends `candle_types.py` "created in Task 1.2" rather than creating it. Commits land at 5.5, 6.3, 7.3, 7.4, 7.5, and 8.4 — one per section plus three in Section 7, not batched at the end. Task 7.2's dependency (only the `kalshi_005` migration from part 1 Section 2) is satisfied well before Section 7. Per the project's PM-confirmed checkpoint-per-section convention, this distribution is compliant; the Section 5 exception is raised above on size grounds, not convention grounds.

### [PASS] No load-test or CI-gating task is required for this slice

The repo has an established NFR load tier (`test/load/test_146_part1_nfrs.py`, `test_167_data_status_nfr.py`, `test_169_coverage_freshness_probe_nfr.py`, `test_187_api_nfr.py`), so the pattern exists and would be the right home if this slice restated an NFR. It does not: the slice design has no NFR section, and its quantitative content (~70 requests per steady-state pass, ~1,150 first-sight requests, 61 B/row compressed, 300 req/min budget) is recorded as *measurement and derived estimate* in Discovery Findings, never as a threshold the implementation must meet. Consistent with that, the design routes performance evidence to the rehearsal rather than to a gate — Decision 9 says a fetch pool stays a follow-up *with evidence*, and Task 8.2 correctly records the phase's wall time from the journal as that evidence rather than asserting a bound. Adding a `test/load/` task here would invent a threshold the design deliberately declined to set.

### [PASS] Host boundary and no wait-blocked tasks

Section 8's host steps are marked **[PM]** (8.1–8.3, executed on manta9000) versus **[agent]** (8.4), matching the 263 convention, so the tasks a junior AI cannot execute are explicitly flagged rather than silently unachievable. No task waits on a wall-clock event: Task 8.3 proves the steady-state criterion by starting the unit the timer activates ("Do not wait for the timer") and derives its success from two recorded `status` outputs rather than a projection — the correct restructuring of what the design's walkthrough step 8 phrased as "one day later". Task 8.1's note that this release adds no new unit (so 263's two-run installer dance does not apply) prevents a plausible misapplication of the prior slice's procedure. Merge and tagging are correctly excluded from the task list.
