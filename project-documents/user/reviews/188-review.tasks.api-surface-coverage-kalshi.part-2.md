---
docType: review
layer: project
reviewType: tasks
slice: api-surface-coverage-kalshi
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/188-tasks.api-surface-coverage-kalshi-2.md
aiModel: z-ai/glm-5.2
status: complete
dateCreated: 20260913
dateUpdated: 20260913
reviewedSha: b2875376ef2f6d9e339e1e69b490758341ca1899
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 9
findings:
  - id: F001
    severity: concern
    category: ci-gating
    summary: "No CI wiring task (or deferral note) for the load tier"
    location: ".github/workflows/ci.yml"
  - id: F002
    severity: concern
    category: consistency
    summary: "Part 2 context summary mis-counts the catalog routes as \"six\""
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
    location: "project-documents/user/tasks/188-tasks.api-surface-coverage-kalshi-2.md"
  - id: F005
    severity: pass
    category: commits
    summary: "Commit checkpoints distributed throughout, not batched"
    location: "project-documents/user/tasks/188-tasks.api-surface-coverage-kalshi-2.md"
  - id: F006
    severity: pass
    category: sequencing
    summary: "Task sequencing respects dependencies with no cycles"
    location: "project-documents/user/tasks/188-tasks.api-surface-coverage-kalshi-2.md"
  - id: F007
    severity: note
    category: task-sizing
    summary: "Tasks are appropriately sized; none need splitting or merging"
    location: "project-documents/user/tasks/188-tasks.api-surface-coverage-kalshi-2.md"
---

# Review: tasks — slice 188

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.2

## Findings

### [CONCERN] No CI wiring task (or deferral note) for the load tier

The slice design restates an NFR (D11, SC7) and Part 2 adds the load test at `test/load/test_188_kalshi_api_nfr.py` (Task 6.3), gated on `MT_RUN_LOAD_TESTS=1`. Per the review criteria, when a load test task exists a CI wiring task must also exist so gating is not left implicit. The repo's only workflow (`.github/workflows/ci.yml`, read and confirmed) is a publish-on-tag pipeline triggered only by `v*` tags with no test job and no `MT_RUN_LOAD_TESTS` invocation. Slice 907's charter (per `project-documents/user/architecture/900-slices.foundation-cleanup.md` and the CHANGELOG at line 761) explicitly owns CI gating for the load tier as a known open gap — but prior slice reviews (167, 187) required an explicit one-line deferral note when CI wiring was out of scope, so the gap is a recorded decision rather than a silent omission. Part 2's Task 6.3 and Section 7 contain neither a wiring task nor such a deferral note. Recommend adding one subtask that either (a) adds an `MT_RUN_LOAD_TESTS=1` job to a CI workflow, or (b) records the explicit deferral to slice 907 with a one-line note matching the pattern prior reviews required.

### [CONCERN] Part 2 context summary mis-counts the catalog routes as "six"

The Part 2 "Entering Section 5" summary states "`routes/kalshi_catalog.py` with its six routes registered," but the slice design's *API Specification* (D2 table) and Part 1's Task 3.5 both specify **seven** catalog routes (categories, series list, series seek, series→events, event seek, event→markets, market seek), and SC1/SC9 both say "nine routes" (7 catalog + 2 time-series). Part 1's Task 3.6 also asserts "the seven routes appear in `create_app().openapi()`." The "six" in the Part 2 hand-off summary is a copy/count error that could mislead the Section 5 implementer into believing the catalog surface is incomplete when entering Part 2. Recommend correcting "six routes" → "seven routes".

### [PASS] Success criteria fully covered with no gaps

All nine SCs map to concrete tasks across both parts. SC1 (OpenAPI drift + equities byte-identical) → Tasks 7.4, 5.3 (and Part 1's 3.5). SC2 (422 over ceiling, no fetch) → Tasks 5.4, 6.3 (and Part 1's 3.6). SC3 (404 vs 200-empty with D5 facts) → Tasks 5.4 (and 3.6). SC4 (`tape_filtered`/`coverage_from` agree with CLI) → Tasks 4.4, 4.7 (Part 1). SC5 (`complete_through`/`collected` vs state row) → Tasks 4.3, 4.7, 5.4. SC6 (Decimal strings, UTC timestamps, msgpack) → Tasks 5.2, 5.4 (and Part 1's 1.1/1.3, 3.2/3.3). SC7 (load tier passes with measured bounds) → Task 6.3. SC8 (bars uses helper, tests unchanged) → Part 1's Tasks 1.1–1.3. SC9 (README, app description, 180 docs) → Tasks 7.1–7.3. No success criterion lacks a corresponding task, and no Part 2 task traces to an out-of-scope concern (the explicitly-excluded cross-cutting markets list, derived candle periods, and freshness verdicts are all absent, correctly).

### [PASS] Test tasks immediately follow their implementation tasks

The test-with pattern is consistently applied in Part 2: Task 5.1 (time-series models) → 5.2 (model tests); Task 5.3 (time-series routes) → 5.4 (route unit tests); Task 6.2 (fixture) → 6.3 (load assertions). No implementation task in Part 2 lacks a paired test before the next section boundary.

### [PASS] Commit checkpoints distributed throughout, not batched

Each Part 2 section ends with an explicit "Commit (section checkpoint)" instruction (Tasks 5.4, 6.3, 7.5), and the Context Summary requires "Commit at least once per section." No checkpoint is deferred to a single end-of-slice batch.

### [PASS] Task sequencing respects dependencies with no cycles

Part 2's section order is justified: Section 5 (time-series models/routes) builds on the readers and serialization helper completed in Part 1's Sections 1 and 4; Section 6 (load tier) needs both route sets complete; Section 7 (OpenAPI artifact) is last "so the drift test is not fought section by section." Within Section 5, Task 5.1 (models) precedes 5.3 (routes that build the models); within Section 6, 6.1 (shared schema helpers) → 6.2 (fixture) → 6.3 (assertions). No task references a later-section deliverable and no cycles exist.

### [NOTE] Tasks are appropriately sized; none need splitting or merging

Part 2's tasks are well-scoped to the cited design decisions and effort estimates (1–3). Task 5.3 is the largest at effort 3 but is bounded by its success criteria (two routes, each under ~50 lines, registered in `create_app`); Task 6.3 is also effort 3 but decomposes into four named D11 assertions with explicit measurement-first guidance. None are too large to split or too granular to merge.
