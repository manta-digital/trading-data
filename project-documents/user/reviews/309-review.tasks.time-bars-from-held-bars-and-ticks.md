---
docType: review
layer: project
reviewType: tasks
slice: time-bars-from-held-bars-and-ticks
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261115
dateUpdated: 20261115
reviewedSha: b2414e40bebf3e52a67b07d24461839b30abf37e
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 116399
cachedTokens: 11436
cacheWriteTokens: 104959
completionTokens: 6476
durationSeconds: 53.6
costUsd: 0.48652
costBasis: provider
squadronVersion: 0.24.31
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria 1 to 8 trace to tasks"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:63-607"
  - id: F002
    severity: pass
    category: sequencing
    summary: "Sequencing and green-tree commit points"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:96-361"
  - id: F003
    severity: concern
    category: scope
    summary: "Load test 7.3 does not trace to a success criterion or a stated NFR"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:438-454"
  - id: F004
    severity: concern
    category: proportionality
    summary: "Route test matrix goes beyond what the criteria need"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:363-393"
  - id: F005
    severity: concern
    category: task-size
    summary: "Task 6.3 bundles too many responsibilities"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:313-335"
  - id: F006
    severity: note
    category: task-size
    summary: "Section 8.3 is wide but within the design"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:493-515"
  - id: F007
    severity: note
    category: process
    summary: "Validation section commit handling"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:552-586"
---

# Review: tasks — slice 309

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria 1 to 8 trace to tasks

- Criterion 1: route tests in 6.6, plus the production check in the Phase 7 handoff.
- Criterion 2: tests against real held ticks in 2.2.
- Criterion 3: 4.3, 5.4 and 6.6.
- Criterion 4: 6.1 and 6.6, covering both the no-data session and the default range.
- Criterion 5: the measurement and note in 3.1, and the admission refusal test in 5.4.
- Criterion 6: 6.5 and 10.1, via an empty `git diff main --stat`.
- Criterion 7: 8.3 at test scale, and the handoff for production.
- Criterion 8: the handoff, which is agent work and not a human-proxy step.

The design's "What is removed" and "Documents cleaned up" sections map to sections 8, 9 and 11.

### [PASS] Sequencing and green-tree commit points

- The dependency order is sound: grammar, then the cut module, then the measurement, then source choice, then the reader, then routes, then CLI, then removals, then docs.
- The trade-limit constant is measured in section 3 before 5.2 reads it. A `BLOCKED:` path is stated for when it cannot be measured.
- Each task that changes behavior is followed by its tests.
- The commit markers sit at working points. The bundled 6.2 to 6.5b commit is justified because the response shape, the old-shape tests and the document gates must change together to stay green.
- Removals are deferred to section 8 until after the verification greps.
- No circular dependencies.

### [CONCERN] Load test 7.3 does not trace to a success criterion or a stated NFR

- The slice restates no NFR, and no success criterion calls for a load test.
- 7.3 adds a 5-run timed bound and a starvation probe. Task 10.1 then adds roughly 77 minutes of load tier to validation.
- The task admits nothing gates it. CI has no test job, and the gate is deferred to #32 and slice 907. The deferral is explicit, so it is not an implicit gap.
- The test still costs real effort, and its only stated purpose is checking that the model build runs off the loop, which is an implementation detail.
- Either cut 7.3 and drop the load-tier files from 10.1, or name the requirement it protects in the slice.
- If it stays, the explicit deferral is adequate and a CI wiring task is not required.

### [CONCERN] Route test matrix goes beyond what the criteria need

- 6.6 runs four sizes on two routes on the ticks-only fixture. It runs three more sizes on two routes on the both fixture. The reader behavior it repeats is already covered by 5.4.
- 6.6b adds a `1s` route test that repeats the 2.2 oracle check.
- 6.6b also adds a row-ceiling test. That is existing 306 and 308 behavior and has no criterion here.
- To cut:
  - Run the 6.6 matrix on one size per source path: for example `1h` and `15s` on the ticks-only fixture, and `5m` and `15s` on the both fixture. Keep both routes only where the route code differs.
  - Fold the 6.6b `1s` route check into one `from`/`until` test.
  - Drop the row-ceiling test, or keep it only as the single check that `from`/`until` are echoed and honored.

### [CONCERN] Task 6.3 bundles too many responsibilities

- 6.3 covers a new parameter pair, replacing the granularity enum with parsing and 422s, planning and default-range changes, the reader and admit wiring, five error mappings, executor-based model building, dependency injection and docstring and response-table edits.
- It also says the 308 handling is to be "moved to one shared place if needed", a refactor of code that criterion 6 says must stay unchanged.
- A junior would struggle to complete it as one unit.
- Split it into:
  - (a) parameters and parsing, covering `from`/`until`, the string `granularity` and the removal of the enum;
  - (b) read wiring, error mapping and the executor.
- State that the shared-handler move happens only if it leaves the unchanged 308 tests passing without edits. Otherwise leave the 308 route's handling alone and duplicate the mapping minimally.

### [NOTE] Section 8.3 is wide but within the design

- Removing the production history wants touches many test files found by grep, and several health and overview expectations.
- It traces to "What is removed" and criterion 7, and it has its own commit marker.
- The design stops at "wants removed", so keep the work to what the grep surfaces and do not refactor the history mechanism.

### [NOTE] Validation section commit handling

- Section 10 correctly carries no commit of its own, so there is no check-box-only commit.
- Its check-offs ride in the section 11 docs commit. That is acceptable because section 11 changes files.

### Run Digest

- Response length: 5577 chars
- Response is newline-free: no
- Tool calls made: 2
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 3
- Tokens — prompt / cached / cache write / completion / reasoning: 116399 / 11436 / 104959 / 6476 / not computed
- Duration: 53.6 s
- Cost: $0.49 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
