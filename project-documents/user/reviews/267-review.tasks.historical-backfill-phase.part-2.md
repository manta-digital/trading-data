---
docType: review
layer: project
reviewType: tasks
slice: historical-backfill-phase
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/267-tasks.historical-backfill-phase-2.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260901
dateUpdated: 20260901
reviewedSha: 00c1f0a397f2a6f08bb41c3b1e6629fed2e1542d
findings:
  - id: F001
    severity: concern
    category: test-coverage
    summary: "No load-test task (or documented waiver) for the slice's explicit duration/budget NFR"
    location: "project-documents/user/slices/267-slice.historical-backfill-phase.md:292-294"
  - id: F002
    severity: pass
    category: uncategorized
    summary: "All ten success criteria trace to at least one task"
    location: "project-documents/user/tasks/267-tasks.historical-backfill-phase-2.md"
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Observation-only portion of Criterion 8 correctly kept out of the task list"
    location: "project-documents/user/tasks/267-tasks.historical-backfill-phase-2.md:518-528"
  - id: F004
    severity: note
    category: task-sizing
    summary: "Task 6.4's 17-case unit-test task is large but is an explicitly justified ceiling exception"
    location: "project-documents/user/tasks/267-tasks.historical-backfill-phase-2.md:180-233"
---

# Review: tasks — slice 267

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] No load-test task (or documented waiver) for the slice's explicit duration/budget NFR

Success Criterion 6 states a hard, numeric bound — "the total pass duration during the live drain stays under 45 minutes on production" and the cap "never exceeds it by more than one window/one market" — and the parent architecture doc restates the same budget ("30 minutes of the rate budget ≈ 30,000/pass", `260-slices.kalshi-event-contract-data.md:30`). This is exactly the shape of NFR this project's `tests/load/` tier exists for (`project-documents/ai-project-guide/project-guides/rules/python.md:65`: "any code on the simulation, network, concurrency, or environment-layer paths requires at least one load test... assert on latency, throughput, or resource bounds"), and the historical phase is squarely network+environment-layer code (paginated HTTP drain against an external API under a rate/time budget).

Neither task file mentions `tests/load/`, `MT_RUN_LOAD_TESTS`, or an NFR waiver anywhere (checked both part 1 and part 2 for "load"/"NFR" — no hits besides unrelated matches like `load_credentials`). Compare sibling slices in this same codebase, which either add a `test/load/` task (`167-tasks...`, `169-tasks...`, `187-tasks...`) or explicitly document why one is not needed (`166-tasks...:317-326`, `264-tasks...:579-582`, reasoning "the slice states no NFR, and its workload numbers are... estimates, not thresholds" — which does *not* apply here, since 267 states an explicit threshold).

Currently the only place the 45-minute bound and cap arithmetic get checked is: Task 7.1's unit test (cap *computation* only, not wall-clock behavior), and Task 11.1's one-shot production cutover script (`"total pass duration < 45 min (Criterion 6)"`, line 488 of part 2), which runs once on `main` and is not a repeatable regression gate. A future change (e.g. widening `MARKETS_PAGE_LIMIT`, changing `TRADE_WINDOW`, or adding per-page overhead) could silently blow the 45-minute/cap budget with nothing in the automated suites to catch it.

**Failure scenario:** a later change to the archive-walk page size or candle chunking increases per-request overhead by 20%; unit tests (which use fakes with no real timing) stay green, integration tests (Task 8.1, functional-only) stay green, and the regression isn't caught until the next production firing — the same "aspirational gate that nothing actually enforces" failure mode called out in `900-slices.foundation-cleanup.md:40` for the 146/167 NFRs.

Recommend either: (a) add a `tests/load/` task gated by `MT_RUN_LOAD_TESTS=1` exercising the cap/duration arithmetic against a production-shaped fixture (following the `prod_shaped_db` pattern from 187), with CI wiring explicitly deferred to slice 907 as 167 did — or (b) if the PM judges this out of scope (e.g. because the bound is inherently only provable against live Kalshi latency), add the same kind of explicit waiver note 166 and 264 used, so the gap is a recorded decision rather than a silent omission.

### [PASS] All ten success criteria trace to at least one task

Cross-referencing design Criteria 1–10 against tasks: 1 → Task 7.1 tests + Task 8.1 abort cases; 2 → Task 6.4 case 1; 3 → Task 6.4 case 6, Task 5.2 (part 1); 4 → Task 6.4 case 7, Task 8.1 second pass; 5 → Task 6.4 case 4, Task 8.1, Task 9.1/9.2; 6 → Task 7.1 (cap logged), Task 6.4 case 5, Task 11.1 (production duration check); 7 → Task 7.3, Task 8.1, Task 9.2; 8 → Task 11.1/11.2 (first-firing observation) plus the explicit handoff note for the multi-firing descent; 9 → Task 6.2, Task 6.4 cases 13–15, Task 8.1; 10 → Task 6.4 case 16, Task 8.1 abort case. No criterion is left uncovered.

### [PASS] Observation-only portion of Criterion 8 correctly kept out of the task list

The multi-firing descent to the floor (hours-to-days of observation) is explicitly called out as a "Handoff, not a task" rather than encoded as a task that would have to wait on wall-clock time, consistent with this project's rule that no task may wait on a future event. The note also correctly enumerates where each underlying mechanism is already proven (Task 6.4 cases 5–6, Task 9.2, Task 7.1, Task 8.1, Task 6.4 cases 13–15), so the deferred observation isn't standing in for missing verification.

### [NOTE] Task 6.4's 17-case unit-test task is large but is an explicitly justified ceiling exception

Task 6.4 (effort 4) carries seventeen distinct test cases covering the archive walk, candle sub-drain, trades sub-drain, cap sharing, and error semantics of `HistoricalSync.run`. This is large for a single task, but part 1's Context Summary explicitly names Task 6.2/6.4 as the only two tasks allowed to exceed the effort-3 ceiling ("Task 6.2 ... and Task 6.4 (its unit suite). Everything else is ≤ 3", part 1 lines 87-89), and the size is a direct consequence of testing one cohesive `run()` state machine rather than an arbitrary bundling of unrelated work — splitting it would fragment assertions about a single ordered sequence (archive → candles → trades) that only make sense together. No action needed.
