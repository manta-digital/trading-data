---
docType: review
layer: project
reviewType: tasks
slice: historical-acquisition-pass
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/224-tasks.historical-acquisition-pass.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20260929
dateUpdated: 20260929
reviewedSha: a7b9bf4538495d248e454af9ce37dd1a69d9a1d6
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 3
squadronVersion: 0.16.0
findings:
  - id: F001
    severity: concern
    category: sequencing
    summary: "Availability task needs manifest_repo queries that are only built later"
    location: "project-documents/user/tasks/224-tasks.historical-acquisition-pass.md:226-236"
  - id: F002
    severity: concern
    category: task-sizing
    summary: "Task 6.2 is too large for one junior-completable task"
    location: "project-documents/user/tasks/224-tasks.historical-acquisition-pass.md:265-286"
  - id: F003
    severity: concern
    category: error-handling
    summary: "Provider error during per-unit verification is not covered"
    location: "project-documents/user/tasks/224-tasks.historical-acquisition-pass.md:280-281"
  - id: F004
    severity: concern
    category: test-coverage
    summary: "Calendar-outage behaviour (TD6) has no test"
    location: "project-documents/user/tasks/224-tasks.historical-acquisition-pass.md:345-348"
  - id: F005
    severity: concern
    category: test-coverage
    summary: "FR4 \"a job a row holds is not counted twice\" is not explicitly tested"
    location: "project-documents/user/tasks/224-tasks.historical-acquisition-pass.md:210-219, 379-380"
  - id: F006
    severity: note
    category: scope
    summary: "Exit-code task overlaps 223's definitions"
    location: "project-documents/user/tasks/224-tasks.historical-acquisition-pass.md:130-137"
  - id: F007
    severity: note
    category: consistency
    summary: "Frontmatter `interfaces` differs from the LLD"
    location: "project-documents/user/tasks/224-tasks.historical-acquisition-pass.md:8"
  - id: F008
    severity: note
    category: nfr
    summary: "No performance NFR, so the load-test and CI gating criteria do not apply"
    location: "project-documents/user/slices/224-slice.historical-acquisition-pass.md:1222-1335"
  - id: F009
    severity: pass
    category: coverage
    summary: "Success criteria trace to tasks"
    location: "project-documents/user/tasks/224-tasks.historical-acquisition-pass.md:94-493"
  - id: F010
    severity: pass
    category: process
    summary: "Test-with pattern and commit distribution"
    location: "project-documents/user/tasks/224-tasks.historical-acquisition-pass.md:90-481"
---

# Review: tasks — slice 224

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Availability task needs manifest_repo queries that are only built later

Task 5.1 builds the span from "owned tier days and holed units' days". It reopens holed units "through `manifest_repo`". It runs in Section 5, before Section 6. Task 6.1 is the only task that adds "owned tier days" and "covered keys" to `manifest_repo.py`. No task adds a "holed unit days" query or a "reopen holed units for a day" transition. The LLD gives 223 the reset-side transitions, not this one. Task 5.2 therefore has nothing to call. Move the manifest functions 5.1 needs into Section 5, or move 6.1 ahead of Section 5. Also list the reopen-by-day function and the holed-days query explicitly.

### [CONCERN] Task 6.2 is too large for one junior-completable task

Task 6.2 (effort 4) holds four separate behaviours in one module: `resolve_unsubmitted`, `sweep_expired`, `advance` (poll, download in deadline order, header matching, hole detection, `manifest.json` writing, verify), and listing-lag logging. It also carries the error mapping. The task itself admits the 300-line limit may be exceeded. Split it into 6.2a (resolve and sweep) and 6.2b (advance and download). Follow each with its share of the 6.3 tests, so tests stay next to the code they cover.

### [CONCERN] Provider error during per-unit verification is not covered

The LLD's failure table says a verify call failure means `provider_abort`. Adoption leaves the remaining units *downloaded*. Task 6.2 only specifies download failure (transient attempt, `provider_abort`) and `OSError` (`storage_abort`). It does not say what `advance` does when `verify.check`'s record-count call raises `ProviderError`. Neither 6.2 nor 6.3 tests this. Add both.

### [CONCERN] Calendar-outage behaviour (TD6) has no test

The mapping of `OutOfPopulatedRangeError` or an unreachable calendar database to `storage_abort` is stated in the Section 8 preamble. Tasks 8.2 and 8.4 do not test it. TD6 also requires that reconcile, await and definitions do not need the calendar. Nothing verifies that a calendar failure buys nothing while deliveries still download. Add one purchase-phase test for `storage_abort` with no submit. Add one whole-pass test where the calendar fails but reconcile still downloads.

### [CONCERN] FR4 "a job a row holds is not counted twice" is not explicitly tested

FR4 has two clauses: an unheld listed job counts and is named, and a job a manifest row holds is not counted twice. Tasks 4.2 and 8.2 item 6 only test the unheld case. Add a case where the listed job's id is held by a row and the total is unchanged. For the phase, add a case where the unheld-job filter excludes held ids.

### [NOTE] Exit-code task overlaps 223's definitions

Task 1.4 is titled "Remaining exit codes" but its first bullet defines `EXIT_PARTIAL = 3`. The LLD says 223 already defines `EXIT_PARTIAL` and `EXIT_STORAGE`. Reword the task to add only 5 and 6 and to verify 3 and 4 exist. Otherwise an implementer may redefine or duplicate constants.

### [NOTE] Frontmatter `interfaces` differs from the LLD

The task file lists interfaces `[225, 226, 228, 229, 230, 231, 233]`. The LLD lists `[225, 226, 227, 228, 229, 231, 233]`. One of them has 227 and the other has 230. Reconcile them.

### [NOTE] No performance NFR, so the load-test and CI gating criteria do not apply

The slice restates no throughput or latency NFR. The wait budget and poll interval are configuration constants to be measured in 226. No `tests/load/` task or CI wiring task is needed.

### [PASS] Success criteria trace to tasks

This slice owns FR3–FR10, and every one has a task:
- FR3: 3.2, plus walkthrough 10.2.
- FR4: 4.1, 4.2 and 8.2.
- FR5: 6.3 and 8.4.
- FR6: 5.2 and 6.3.
- FR7: 7.2.
- FR8: 8.4 item 4.
- FR9: 1.2.
- FR10: 8.4 item 5.

The "no `fetch_range` / only `submit_batch`" unit test is in 8.2. The tier regression check is in 10.1. The documentation rows are in 9.1. Walkthrough steps 5–7 and 10 are in 10.2 and 10.3. I found no scope creep beyond the 1.3 diff review, which the LLD requires.

### [PASS] Test-with pattern and commit distribution

Tests follow their implementation immediately in Sections 1–5, 7 and 8. Each section commits at a semantic checkpoint, not batched at the end, and the 0.1 baseline precedes all changes. Validation and the live walkthrough come last, and the walkthrough has a documented deferral path if the ceilings are unset.

### Run Digest

- Response length: 5519 chars
- Response is newline-free: no
- Tool calls made: 3
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
