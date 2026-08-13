---
docType: review
layer: project
reviewType: tasks
slice: coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized
project: trading-data
verdict: FAIL
sourceDocument: project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-1.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260813
dateUpdated: 20260813
reviewedSha: 452b0b367d03dd87337a1c1fcec74a64e853c76e
findings:
  - id: F001
    severity: fail
    category: uncategorized
    summary: "Criterion 16 has no integration test asserting end-to-end generic bucket-lag freshness for coverage views"
    location: "unverified"
  - id: F002
    severity: fail
    category: uncategorized
    summary: "Criterion 18 has no part-1 task establishing the verification scaffold"
    location: "unverified"
  - id: F003
    severity: concern
    category: task-sizing
    summary: "Task B.6 may be too large and too coarse for a junior AI"
    location: "unverified"
  - id: F004
    severity: concern
    category: sequencing
    summary: "Sequencing: Task B.6 measurement depends on `start_offset`, which is selected in B.7"
    location: "unverified"
  - id: F005
    severity: concern
    category: scope-clarity
    summary: "Criterion 12's full-view NFR measurement is on the seed DB, not prod"
    location: "unverified"
  - id: F006
    severity: concern
    category: test-quality
    summary: "Task B.1's \"approximate\" seeding may invalidate B.4/B.5/B.6 measurements"
    location: "unverified"
  - id: F007
    severity: concern
    category: ci-coverage
    summary: "Missing CI wiring for the new integration tests"
    location: "unverified"
  - id: F008
    severity: concern
    category: completeness
    summary: "`_interval_seconds_sql` is referenced but never verified to render the new width correctly"
    location: "unverified"
  - id: F009
    severity: concern
    category: sequencing
    summary: "Task C.9 vs D.2 ordering ambiguity: doc comment rendered in 051 vs 052"
    location: "unverified"
  - id: F010
    severity: concern
    category: test-quality
    summary: "Task F.1 verification has no automated test added if the gap exists"
    location: "unverified"
  - id: F011
    severity: concern
    category: test-quality
    summary: "No test asserts the `_build_data_status_view_sql` re-execution in D.1 step ③ is byte-identical"
    location: "unverified"
  - id: F012
    severity: pass
    category: test-coverage
    summary: "Test-with-implementation pattern is respected throughout"
    location: "unverified"
  - id: F013
    severity: pass
    category: process
    summary: "Commit checkpoints are distributed, not batched"
    location: "unverified"
  - id: F014
    severity: pass
    category: traceability
    summary: "Success criteria 1–11, 13–15, 17, 19 trace cleanly to tasks"
    location: "unverified"
  - id: F015
    severity: pass
    category: task-sizing
    summary: "Tasks are scoped for a junior AI with clear success criteria"
    location: "unverified"
  - id: F016
    severity: pass
    category: scope-discipline
    summary: "No scope creep detected"
    location: "unverified"
---

# Review: tasks — slice 169

**Verdict:** FAIL
**Model:** minimax/minimax-m3

## Findings

### [FAIL] Criterion 16 has no integration test asserting end-to-end generic bucket-lag freshness for coverage views

The slice design's criterion 16 reads: "Both coverage views report **fresh** on the *generic* bucket-lag check as well as the content-edge check, with the seven pre-167 caggs' budgets unchanged (D3a)." Task C.6 covers the formula in unit tests (override applies to coverage views, pre-167 views fall back, lag at exactly one bucket width passes), but no integration test exists that wires the new C.4 map into `_resolve_threshold`/`_evaluate` and asserts the resulting `assert_cagg_fresh` verdict on a realistic database is `fresh` for `minute_coverage`/`daily_coverage`. Without this, criterion 16 is unverifiable from part 1 alone. Task D.3 covers cold-start migration application and criterion 13 (data_status exists), but does not assert criterion 16. Task E is documentation only. The criterion is a load-bearing acceptance gate per D3a's own argument ("without it they are unachievable in steady state"), so it must be exercised by an automated test, not merely by unit-level formula inspection.

### [FAIL] Criterion 18 has no part-1 task establishing the verification scaffold

Criterion 18 — "The refresh policy advances the head on its own" — is the only criterion the original defect could not satisfy, and the design's walkthrough 8a calls it out explicitly. The part-1 tasks defer everything to part 2's Task G (prod rebuild), but part 1 should establish what the verification looks like at the unit/integration tier so part 2 isn't a blank-slate operational task. Specifically: there is no task to define a regression test that simulates "policy tick fires → MAX(last_bucket) advances" against a test cagg, even though this is mechanically determinable. Without it, part 2 carries design + verification work undeclared, and criterion 18 has no automated guard.

### [CONCERN] Task B.6 may be too large and too coarse for a junior AI

Task B.6 ("Measure the refresh policy's per-run cost at candidate `start_offset` values") combines: spinning up a representative policy run, sizing `start_offset` per width per the engine floor + parent constraint + schedule-interval margin, and recording wall-clock against the 1-hour interval. This conflates three measurements (floor, parent window, schedule fit) and requires deriving the `start_offset` value that B.7 selects. B.7 is the actual selection step, so B.6 should be split: B.6a measures raw policy wall-clock at each candidate width at a fixed `start_offset`; B.6b derives the candidate `start_offset` value(s) from the B.6a measurement + the two non-runtime constraints. As written, the junior AI is being asked to make a coupled decision (width × start_offset) under three constraints simultaneously.

### [CONCERN] Sequencing: Task B.6 measurement depends on `start_offset`, which is selected in B.7

Task B.6 measures "representative policy run" at candidate `start_offset` values, but `start_offset` selection (Task B.7) is downstream of B.6's measurements (per the C.7 docstring requirement that docstrings cite "the B.6 measurement"). This is a circular dependency: B.6 needs a `start_offset` to measure, but `start_offset` selection needs B.6's measurement. The fix is to measure at a fixed `start_offset` (e.g., the engine-floor value) in B.6, then re-measure at the candidate `start_offset` after B.7 if needed, or split B.6 into two passes.

### [CONCERN] Criterion 12's full-view NFR measurement is on the seed DB, not prod

Criterion 12 (sub-second `SELECT count(*) FROM data_status`) and B.4 measure this on the seeded measurement database. The success criterion as written applies to prod ("meets the sub-second NFR that slice 167 exists to hold"). Task B.4 explicitly says "ephemeral or test database seeded to a representative shape" — but the seed's shape (B.1) is only "approximate" to slice 170's measured spans, and `data_status` joins against `symbols`, `acquisition_state`, and the exchange-close CTE over the larger intermediate. If the seed's row counts in those joined tables don't match prod, B.4's number is not criterion 12's number. The task breakdown should either (a) make criterion 12 a re-measurement on prod that part 2 performs, with B.4 a *predicted* number, or (b) require B.1 to seed the full joined view's shape, not just `daily_ohlcv`/`minute_ohlcv` spans. As written, criterion 12 looks covered by B.4 but actually isn't.

### [CONCERN] Task B.1's "approximate" seeding may invalidate B.4/B.5/B.6 measurements

Task B.1 says "exact replication is not required, but the row-count arithmetic in design D2 must be checkable against it." But criteria 12, 17, and 19 require *measured actuals* — not checkable arithmetic. If the seed's symbol count or span differs materially from prod, B.4 (sub-second data_status) and B.6 (policy wall-clock) measure the wrong system. The task should pin the seed's row counts for `symbols`, `acquisition_state`, and the joined view, not just `daily_ohlcv`/`minute_ohlcv`, and require them to match prod within a stated tolerance.

### [CONCERN] Missing CI wiring for the new integration tests

Task D.3, D.4, and D.5 add three new integration test files (`test_migration_051_052.py`, idempotency test, DROP-ordering test). No task wires these into CI (e.g., updates the integration-test job, the migration-chain gate, or a new marker). The slice design's risks table treats "Cagg refresh racing the rebuild silently loses rows" as the 163 lesson; without CI gating on the migration tests, regressions in 051/052's idempotency or DROP ordering will not be caught automatically. Either add a CI-wiring task or note explicitly that an existing integration-test job covers them.

### [CONCERN] `_interval_seconds_sql` is referenced but never verified to render the new width correctly

Task D.1 says migration 051 recreates both caggs "using `_interval_seconds_sql`, mirroring 046's structure." `_interval_seconds_sql` is the existing rendering path (per D5), so this is correct in principle. But D.1 also requires the description text to state "the new width" — and `description` text typically renders before DDL executes. If `_interval_seconds_sql` reads `COVERAGE_BUCKET_INTERVAL` at *migration-execution time* but the description text is a string literal built at migration-definition time, there is a subtle trap. Worth a one-line task or assertion that the description text reads `COVERAGE_BUCKET_INTERVAL` lazily (e.g., via a helper) or is verified after C.1 to not contain a hardcoded width.

### [CONCERN] Task C.9 vs D.2 ordering ambiguity: doc comment rendered in 051 vs 052

Task C.9 says the fix is "Done here, **before** Task D's migrations, so 051/052 can re-execute this function directly with no placeholder/supersede step." Task D.1 step ③ re-attaches the corrected comment as part of 051. Task D.2 says 052 also "Re-render `COMMENT ON VIEW data_status` from the corrected doc-comment function." So both migrations re-render the comment. That's fine and idempotent (the same function output), but the task breakdown doesn't state which migration owns the comment in the "happy path" — i.e., if both run cleanly, is the comment from 051 or 052? This is minor but the runbook in part 2 will want to know whose migration to re-run if the comment drifts. Add one line clarifying 052's comment re-render is a belt-and-braces idempotency guard, not the primary install path.

### [CONCERN] Task F.1 verification has no automated test added if the gap exists

Task F.1 says "Either outcome recorded — do not skip silently." But the only verification path is "Run any existing `restore_metadata` tests against a database that has 051/052 applied." If a gap is found and fixed, no new test is added to guard against regression. The slice design's risks call out "stale reference here fails exactly when it is least affordable" (the incident-recovery path). Either add a test task for the post-fix state, or explicitly note that the existing test (which is not named) covers this — but the task says "any existing `restore_metadata` tests," which is vague.

### [CONCERN] No test asserts the `_build_data_status_view_sql` re-execution in D.1 step ③ is byte-identical

Task D.1 step ③ says 051 re-executes 048's `_build_data_status_view_sql(...)` output "unchanged (not a rewrite)." Task D.3 asserts `data_status` "exists and returns zero rows without error" — but does not assert the column names, order, and types match the 167 D2 contract (criterion 7). Criterion 7 is a load-bearing data-contract criterion, and it should have its own test, not be subsumed under D.3's existence check. Add a D.3a or extend D.3 to assert the column schema matches what 167 D2 specifies.

### [PASS] Test-with-implementation pattern is respected throughout

Every constants change in Task C has a paired unit test (C.3, C.6, C.8, C.10), and the migration tasks have paired integration tests (D.3, D.4, D.5). The test-with pattern is correctly applied.

### [PASS] Commit checkpoints are distributed, not batched

Commits are placed at the end of Tasks B, C, D, and E — distributed across the work, with no batching at end. Each commit message is descriptive and maps to a logical unit.

### [PASS] Success criteria 1–11, 13–15, 17, 19 trace cleanly to tasks

Cross-referencing each criterion:
- 1 → C.1, plus the no-literal invariant re-stated in D.1, D.5
- 2 → C.2, C.3
- 3 → D.3 (cold-start migration application)
- 4 → C.8 (existing test passes unedited)
- 5 → deferred to part 2 (materialization) — appropriately
- 6, 8 → deferred to part 2 (prod rebuild + freshness checks)
- 7 → covered loosely by D.3 but see concern above about column-schema assertion
- 9 → deferred to part 2 (walkthrough step 4)
- 10 → deferred to part 2 (walkthrough step 9)
- 11 → deferred to part 2 (R1/R4 in the runbook)
- 13 → D.3 (existence after 051)
- 14 → C.10 (asserts bucket-width term present, two-hop-only absent)
- 15 → E.1 (140-arch amendment with measured values)
- 17 → B.5
- 19 → C.7 (docstring cites B.6 measurement)

### [PASS] Tasks are scoped for a junior AI with clear success criteria

With the exceptions of B.6 (flagged above), each subtask has a single verifiable success criterion and references specific files/lines. The non-negotiables block at the top of the file reinforces the constraints effectively.

### [PASS] No scope creep detected

The "Out of scope, per D7" footer correctly enumerates the exclusions (head-refresh, bars_summary reshape, mt data caggs repair extension, minute rollup parity, approximate_row_count). No task in A–F violates these.
