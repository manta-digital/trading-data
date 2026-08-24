---
docType: review
layer: project
reviewType: tasks
slice: kalshi-provider-foundation
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/261-tasks.kalshi-provider-foundation.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260824
dateUpdated: 20260824
reviewedSha: a384c837ad81c61b87a43a2dbbbab9e3e46ffd04
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "All slice-design success criteria trace to specific tasks"
    location: "project-documents/user/tasks/261-tasks.kalshi-provider-foundation.md"
  - id: F002
    severity: pass
    category: sequencing
    summary: "No scope creep and no circular/out-of-order dependencies"
    location: "project-documents/user/tasks/261-tasks.kalshi-provider-foundation.md"
  - id: F003
    severity: concern
    category: task-scoping
    summary: "Endpoint-method tasks 5.1–5.6 have no independent success criteria and defer all verification to a single batched test task"
    location: "project-documents/user/tasks/261-tasks.kalshi-provider-foundation.md:189-217"
  - id: F004
    severity: concern
    category: task-scoping
    summary: "Migration-table tasks 8.2 and 8.3 lack explicit success criteria for the slice's most consequential schema work"
    location: "project-documents/user/tasks/261-tasks.kalshi-provider-foundation.md:312-334"
  - id: F005
    severity: pass
    category: commit-hygiene
    summary: "Commit checkpoints distributed throughout, not batched at the end"
    location: "project-documents/user/tasks/261-tasks.kalshi-provider-foundation.md"
  - id: F006
    severity: note
    category: nfr-coverage
    summary: "No load-test/CI-gating task needed — slice states no throughput NFR beyond a unit-level rate-limiter check"
    location: "unverified"
---

# Review: tasks — slice 261

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [PASS] All slice-design success criteria trace to specific tasks

Each of the design's success criteria maps cleanly to tasks: (1) provider registration → Tasks 2.1–2.3; (2) client contract + fixtures + error-taxonomy/pagination/rate-limiter unit tests → Section 4 (core), Section 5 (endpoints), Section 6 (fixtures); (3) Decimal parsing from fixed-point strings → Tasks 3.2 and 6.3; (3a) authenticated-mode signing tests with no real credentials → Section 7; (4) `TRACKS["kalshi"]` + `--track` CLI option with unchanged defaults → Task 8.4 and Section 9; (5) throwaway-DB integration test (bootstrap, idempotence, constraint rejection, teardown/re-apply) → Task 8.5; (6) Discovery Findings recorded with 266 gate decision → already present in the design doc, cross-checked/updated by Task 6.3; (7) ruff/pyright clean, exactly one new dependency (`cryptography`) → Task 1.1 and Task 10.1. No success criterion is orphaned.

### [PASS] No scope creep and no circular/out-of-order dependencies

Task ordering matches the real dependency graph: constants/enums (Section 1) → provider registry + models (Sections 2–3, both consume Section 1 constants) → client request core (Section 4) → endpoint methods (Section 5, consumes Section 4 + models) → fixture recording (Section 6, drives the now-complete client) → authenticated signing (Section 7, extends the request core while Task 7.1 explicitly requires public-mode behavior stay byte-identical to Sections 4–6) → migration track (Section 8) → CLI `--track` wiring (Section 9, consumes `TRACKS` from 8.4) → final validation (Section 10). No task references work from a later section, and every task traces back to an explicit line in the "Technical Scope" or "Implementation Details" sections of the design — no unrelated work was added.

### [CONCERN] Endpoint-method tasks 5.1–5.6 have no independent success criteria and defer all verification to a single batched test task

Every other implementation task in this document (1.2, 2.1, 2.2, 3.1, 4.1, 7.1, 8.1, 8.4, 9.1, etc.) ends with an explicit "Success:" line a junior AI can check before moving on. Tasks 5.1 (Series methods), 5.2 (Event methods), 5.3 (Market methods), 5.4 (Candlestick method), 5.5 (Trades methods), and 5.6 (Historical cutoff method) have none — each is just a bullet describing what to implement, with no lint/type/behavioral check of its own. All verification for six separate implementation tasks (effort 1+2+2+1+1+1 = 8) is deferred to the single Task 5.7 test task (effort 3), which also violates the test-immediately-follows-implementation pattern used everywhere else in this file (e.g., 1.2→1.3, 3.1→3.2, 4.1→4.2, 7.1→7.2). A junior AI implementing Task 5.3 in isolation has no way to confirm it succeeded until five more tasks are also done. Recommend adding a "Success:" line per task (at minimum: method signature matches the documented filters, `ruff`/`pyright` clean) and either splitting 5.7's tests to accompany each method task or explicitly justifying the batched-test exception (shared MockTransport harness) in the task text.

### [CONCERN] Migration-table tasks 8.2 and 8.3 lack explicit success criteria for the slice's most consequential schema work

Task 8.2 (catalog tables: `kalshi.series`/`events`/`markets` with PKs, FKs, CHECK constraints, indexes, extraction-discipline requirement) and Task 8.3 (collection-state tables: `sync_state`, `awaiting_settlement`, `market_candle_state`) are both multi-table, constraint-heavy schema changes — arguably the highest-risk tasks in the breakdown (bad FK/CHECK definitions silently pass migration `apply` but fail later query patterns) — yet neither has a "Success:" line, unlike sibling Tasks 8.1 and 8.4 in the same section. Verification is entirely deferred to Task 8.5's integration test. Per the review requirement that each task be independently completable by a junior AI with clear success criteria, these two should get their own checks (e.g., "migration SQL applies against a throwaway DB without error; `\d kalshi.markets` shows the CHECK/FK/index list matching the design") rather than relying solely on the downstream integration test to catch schema-authoring mistakes.

### [PASS] Commit checkpoints distributed throughout, not batched at the end

Eleven commit checkpoints are spread across all ten sections (1.3, 2.3, 3.2, 4.2, 5.7, 6.2, 6.3, 7.2, 8.5, 9.2, 10.3) rather than accumulated at the end of the slice, satisfying the review bar. Note for the record: CLAUDE.md's git-rules line "Git add and commit from project root at least once per task" could be read as requiring a commit after every numbered task (e.g., after 5.1 alone) rather than once per section; this breakdown interprets "task" at the section/checkpoint granularity. That reading is reasonable and matches the project's memory note that "commits ARE allowed (encourages interim commits)" without mandating one per subtask, but it's worth a quick confirmation with the PM if stricter per-task commits were intended.

### [NOTE] No load-test/CI-gating task needed — slice states no throughput NFR beyond a unit-level rate-limiter check

The design's only rate/throughput-related success criterion is "rate-limiter enforcement (N calls take ≥ the window time at the configured budget)" (Success Criterion 2), covered by a unit test in Task 4.2. There is no restated NFR in the design (e.g., a sustained-throughput or concurrency target) that would require a dedicated `tests/load/` task or a CI-gating task per the review checklist — this slice ships a thin client with no collection loop, so a load test would be premature here and is correctly deferred to slices that actually run sustained collection (262+).
