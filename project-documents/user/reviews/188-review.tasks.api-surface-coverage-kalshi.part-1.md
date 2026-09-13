---
docType: review
layer: project
reviewType: tasks
slice: api-surface-coverage-kalshi
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/188-tasks.api-surface-coverage-kalshi-1.md
aiModel: z-ai/glm-5.2
status: complete
dateCreated: 20260913
dateUpdated: 20260913
reviewedSha: b2875376ef2f6d9e339e1e69b490758341ca1899
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 11
findings:
  - id: F001
    severity: concern
    category: ci-gating
    summary: "No CI wiring task for the load tier"
    location: ".github/workflows/ci.yml"
  - id: F002
    severity: concern
    category: consistency
    summary: "Part 2 context summary says \"six routes\" for the catalog router"
    location: "project-documents/user/tasks/188-tasks.api-surface-coverage-kalshi-2.md:18"
  - id: F003
    severity: pass
    category: completeness
    summary: "Success criteria fully covered with no gaps"
    location: "project-documents/user/slices/188-slice.api-surface-coverage-kalshi.md"
  - id: F004
    severity: pass
    category: test-with
    summary: "Test tasks immediately follow their implementation tasks"
    location: "project-documents/user/tasks/188-tasks.api-surface-coverage-kalshi-1.md"
  - id: F005
    severity: pass
    category: commits
    summary: "Commit checkpoints distributed throughout, not batched"
    location: "project-documents/user/tasks/188-tasks.api-surface-coverage-kalshi-1.md"
  - id: F006
    severity: pass
    category: sequencing
    summary: "Task sequencing respects dependencies with no cycles"
    location: "project-documents/user/tasks/188-tasks.api-surface-coverage-kalshi-2.md"
---

# Review: tasks — slice 188

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.2

## Findings

### [CONCERN] No CI wiring task for the load tier

The slice design restates an NFR (D11, SC7) and the breakdown correctly adds a load test at `test/load/test_188_kalshi_api_nfr.py` (Task 6.3) gated on `MT_RUN_LOAD_TESTS=1`. Per the review criteria, "if a load test task exists, a CI wiring task exists to gate on it; CI gating is not left implicit." The repo's only workflow, `.github/workflows/ci.yml`, is a publish-on-tag pipeline (triggered only by `v*` tags) with no test job and no `MT_RUN_LOAD_TESTS` invocation. The project's own CHANGELOG (line 761) and slice-900 entry record that "CI gating for the tier is slice 907's" — i.e., the CI wiring is an acknowledged open gap across the codebase, not yet implemented. The task breakdown adds the load test but contains no task that wires it into CI or even defers-and-references slice 907 the way prior slice reviews (167, 187) required. The load NFR will therefore ship with the same aspirational-only gate that slice-907's charter explicitly calls out as a problem. Recommend adding one subtask (to Task 6.3 or Section 7) that either (a) adds the `MT_RUN_LOAD_TESTS=1` job to a CI workflow, or (b) records the explicit deferral to slice 907 with a one-line note matching the pattern prior reviews required, so the gap is a recorded decision rather than a silent omission.

### [CONCERN] Part 2 context summary says "six routes" for the catalog router

The Part 2 "Entering Section 5" summary states "`routes/kalshi_catalog.py` with its six routes registered," but the slice design's *API Specification* (D2 table) and Part 1's Task 3.5 both specify **seven** catalog routes (categories, series list, series seek, series→events, event seek, event→markets, market seek), and SC1/SC9 both say "nine routes" (7 catalog + 2 time-series). Task 3.6 in Part 1 also asserts "the seven routes appear in `create_app().openapi()`." The "six" in the Part 2 context is a copy/count error that could mislead the implementer of Section 5 (or a reviewer) into believing the catalog surface is incomplete when entering Part 2. Recommend correcting "six routes" → "seven routes" so the hand-off summary is accurate.

### [PASS] Success criteria fully covered with no gaps

All nine SCs map to concrete tasks: SC1 (OpenAPI drift + equities byte-identical) → Tasks 7.4, 5.3/3.5 route registration; SC2 (422 over ceiling, no fetch) → Tasks 3.6, 5.4, 6.3; SC3 (404 vs 200-empty with D5 facts) → Tasks 3.6, 5.4; SC4 (`tape_filtered`/`coverage_from` agree with CLI) → Tasks 4.4, 4.7; SC5 (`complete_through`/`collected` vs state row) → Tasks 4.3, 4.7, 5.4; SC6 (Decimal strings, UTC timestamps) → Tasks 1.1/1.3, 3.2/3.3, 5.2; SC7 (load tier passes) → Task 6.3; SC8 (bars uses helper, tests unchanged) → Tasks 1.1–1.3; SC9 (README, app description, 180 docs) → Tasks 7.1–7.3. No success criterion lacks a corresponding task, and no task traces to an out-of-scope concern (the explicitly-excluded cross-cutting markets list, derived candle periods, and freshness verdicts are all absent from the tasks, correctly).

### [PASS] Test tasks immediately follow their implementation tasks

The test-with pattern is consistently applied: Task 1.1 (helper) → 1.3 (tests); 2.1–2.3 (readers) → 2.4 (integration tests); 3.1–3.5 (models/dep/validator/routes) → 3.3 and 3.6 (model + route unit tests); 4.1 (promotion) → 4.2 (tests); 4.3–4.6 (readers) → 4.7 (integration tests); and in Part 2, 5.1 (models) → 5.2, 5.3 (routes) → 5.4, 6.2 (fixture) → 6.3 (load assertions). No implementation task lacks a paired test before the next section boundary.

### [PASS] Commit checkpoints distributed throughout, not batched

Each section ends with an explicit "Commit (section checkpoint)" instruction (Tasks 1.3, 2.4, 3.6, 4.7 in Part 1; 5.4, 6.3, 7.5 in Part 2), and the Context Summary requires "Commit at least once per section." No checkpoint is deferred to a single end-of-slice batch.

### [PASS] Task sequencing respects dependencies with no cycles

The section order is justified: routes (Section 3/5) depend on readers (Section 2/4) and models (Section 3.2/5.1); the load fixture (Section 6) needs both route sets; `openapi.json` (Section 7) is regenerated last "so the drift test is not fought section by section." The `effective_tape_floor` promotion (4.1) precedes its only new caller (4.4). The serialization extraction (1.x) precedes every route that uses it. No task references a later-section deliverable.
