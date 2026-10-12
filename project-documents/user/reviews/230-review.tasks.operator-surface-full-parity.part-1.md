---
docType: review
layer: project
reviewType: tasks
slice: operator-surface-full-parity
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/230-tasks.operator-surface-full-parity-1.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261012
dateUpdated: 20261012
reviewedSha: a40e3808a94f5627c12a2b89e622ccf7741fed6b
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 7
durationSeconds: 54.5
squadronVersion: 0.21.5
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria trace to tasks across both parts"
    location: "project-documents/user/tasks/230-tasks.operator-surface-full-parity-1.md"
  - id: F002
    severity: pass
    category: sequencing
    summary: "Sequencing and commit checkpoints"
    location: "project-documents/user/tasks/230-tasks.operator-surface-full-parity-1.md:79-382"
  - id: F003
    severity: concern
    category: sequencing
    summary: "The window/segment type is defined in two places, left to the implementer to resolve"
    location: "project-documents/user/tasks/230-tasks.operator-surface-full-parity-1.md:176-178"
  - id: F004
    severity: concern
    category: test-coverage
    summary: "Plan tests skip the range-defaulting and error cases the design specifies"
    location: "project-documents/user/tasks/230-tasks.operator-surface-full-parity-1.md:273-282"
  - id: F005
    severity: concern
    category: test-coverage
    summary: "The `/api/v1/overview` byte-identical check has no stated method"
    location: "project-documents/user/tasks/230-tasks.operator-surface-full-parity-2.md:189-191"
  - id: F006
    severity: concern
    category: design-clarity
    summary: "The shared state-line renderer and the overview renderer overlap"
    location: "project-documents/user/tasks/230-tasks.operator-surface-full-parity-2.md:109-111"
  - id: F007
    severity: concern
    category: proportionality
    summary: "A few test tasks are heavier than the criteria need"
    location: "project-documents/user/tasks/230-tasks.operator-surface-full-parity-2.md:49-78"
  - id: F008
    severity: note
    category: nfr
    summary: "No load test or CI gating task, and none is required"
    location: "project-documents/user/slices/230-slice.operator-surface-full-parity.md:475-479"
  - id: F009
    severity: note
    category: scoping
    summary: "Task 1.1 is a long preflight but justified"
    location: "project-documents/user/tasks/230-tasks.operator-surface-full-parity-1.md:81-117"
---

# Review: tasks — slice 230

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria trace to tasks across both parts

- FR1–FR10a map to tasks 3.4, 4.2, 4.4, 5.2, 5.4 and 5.6.
- FR11 maps to 6.4, FR12 to 7.6, and FR13 and FR14 to 7.6 and 7.8.
- The standing Kalshi pass-contract diff is in 8.5, and the required Revision Log entry is 8.3.
- The runbook and contract rows (8.1, 8.2) match the Technical Requirements.
- Walkthrough steps 1–8 are split across 5.7, 6.5 and 7.9.
- I found no tasks outside the design's scope. 8.4 and 8.5 exist because the design asks for them (Development Approach step 7 and the Technical Requirements).

### [PASS] Sequencing and commit checkpoints

- The order is preflight, then constants and formatters, reads, plan, writers, the `get` verb and the walkthrough. In part 2, debug comes first, then line states and composed lines, then docs.
- No dependency runs backwards. 7.3 depends on finding 1.1, and 7.5 and 7.7 depend on 7.1.
- The test-with pattern holds throughout.
- Commits are spread across 2.3, 3.4, 4.4, 5.4, 5.6, 5.7, 6.4, 6.5, 7.2, 7.3, 7.6, 7.8, 7.9, 8.2, 8.4 and 8.5. Nothing is batched at the end.
- No task writes a merge.

### [CONCERN] The window/segment type is defined in two places, left to the implementer to resolve

- Task 3.2 defines a `(session_date, instrument_id, raw_symbol, open_ns, close_ns)` window type "or reuse the plan's segment type from task 4.1 if defined first".
- Task 4.1 defines the same tuple as the planned segment.
- 3.2 comes before 4.1, so the "if defined first" branch is the live one, and "one definition only" leaves the choice to a junior implementer.
- Fix: state that 3.2 defines the type and 4.1 imports it, or move the type into a shared module. Do the same for 3.2's duplication of the `Unresolved` shape.

### [CONCERN] Plan tests skip the range-defaulting and error cases the design specifies

- 4.4 tests "no range uses the held bounds" and the cap raised with an explicit range.
- Nothing tests TD2's `--start` alone (runs to the last held session) or `--end` alone (starts at the first), both in 4.3.
- FR8 says the default read "refuses with the cap message". 4.4 patches the cap small only for an explicit range, so no task exercises that path with no range.
- A range outside the populated calendar gives exit 1 in the TD5 exit table, and 5.4 does not list it among its bad-input cases.
- Fix: add these three cases to 4.4 and 5.4. They need only a few lines each.

### [CONCERN] The `/api/v1/overview` byte-identical check has no stated method

- 7.8 requires the response to be "byte-identical to `main`'s (snapshot comparison in the existing operations tests)".
- It does not say what the baseline is: a committed fixture, a golden file, or a run against `main`.
- A junior implementer cannot do a comparison against `main` inside one test run.
- Fix: have task 1.1 record whether such a snapshot test exists, as it already does for `SourceRecord` mapping. If none exists, 7.8 should name the smallest test, for example asserting the exact key set and values of the API payload built from fixed facts.

### [CONCERN] The shared state-line renderer and the overview renderer overlap

- 7.1 puts one renderer for the three TD8 lines in `cli/tick_composed.py`, "shared by accounting and overview".
- 7.7 separately has `cli/rendering/overview.py` render the `unavailable` text in place of the time, and the overview carries it as a plain string in `SourceFreshness.unavailable`.
- It is not stated whether `unavailable` holds the already-rendered line text or the state plus its detail, nor who owns the wording.
- Without that, the line text risks being built in two places, and CLAUDE.md says not to duplicate logic.
- Fix: say in 7.1 and 7.7 that `unavailable` carries exactly the renderer's text and that `rendering/overview.py` does not re-spell it.

### [CONCERN] A few test tasks are heavier than the criteria need

- 6.2 (`debug_reads` integration tests) and 6.4 (`debug` CLI integration tests) both seed rows and check the same filters. 6.4 repeats the filter coverage of 6.2 and adds only the enum rejection, the spreads default and the exit codes. Cut 6.4's filter checks and keep it to option validation, the spreads default and the exit codes.
- Task 3.1 is a separate smoke-test task for a test helper. Fold it into 3.4, where the helper is first used.
- The effort-1 formatter test task, 2.3, is fine but could merge with 2.2 if the task count matters.
- Across the two files, the 8-section split is justified by the slice's size, which covers four surfaces plus docs.

### [NOTE] No load test or CI gating task, and none is required

- The slice states a 10 s statement timeout and 5 s connect timeout for the composed read. It checks them in walkthrough step 6 (timing) and defers the 2020+ universe re-check to 232.
- It restates no throughput or latency NFR, so a `tests/load/` task and a CI gate are not owed.
- 7.8 does re-run the existing `test/load/test_922_overview_nfr.py`, which guards the overview's own NFR against regression from the new tick call.

### [NOTE] Task 1.1 is a long preflight but justified

Task 1.1 has nine investigation bullets. It is rated effort 2 and produces only a findings block. Each bullet feeds a named later task (2.1, 3.1, 4.3, 7.3, 7.7 and 7.9) and has a stop condition, so I would keep it as one task.

### Run Digest

- Response length: 6619 chars
- Response is newline-free: no
- Tool calls made: 7
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 54.5 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
