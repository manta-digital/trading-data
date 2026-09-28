---
docType: review
layer: project
reviewType: tasks
slice: cme-session-model-and-data-correctness-amendment
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/221-tasks.cme-session-model-and-data-correctness-amendment-1.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260928
dateUpdated: 20260928
reviewedSha: b7b7d778b4d337db4bd11041011e8d252c61da88
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 7
findings:
  - id: F001
    severity: pass
    category: completeness
    summary: "All eight functional requirements trace to tasks"
    location: "project-documents/user/tasks/221-tasks.cme-session-model-and-data-correctness-amendment-1.md"
  - id: F002
    severity: pass
    category: completeness
    summary: "Technical, integration, and walkthrough requirements are covered"
    location: "project-documents/user/tasks/221-tasks.cme-session-model-and-data-correctness-amendment-2.md:210-227"
  - id: F003
    severity: pass
    category: sequencing
    summary: "Sequencing is correct; no circular dependencies"
    location: "project-documents/user/tasks/221-tasks.cme-session-model-and-data-correctness-amendment-1.md"
  - id: F004
    severity: pass
    category: process
    summary: "Commit checkpoints distributed throughout, not batched"
    location: "project-documents/user/tasks/221-tasks.cme-session-model-and-data-correctness-amendment-1.md:169"
  - id: F005
    severity: concern
    category: correctness
    summary: "`SessionIndex` unsorted-input behavior contradicts the slice design's own stated test requirement"
    location: "project-documents/user/tasks/221-tasks.cme-session-model-and-data-correctness-amendment-1.md:141,166"
  - id: F006
    severity: note
    category: test-coverage
    summary: "`late_open` exception kind has no dedicated unit test"
    location: "project-documents/user/tasks/221-tasks.cme-session-model-and-data-correctness-amendment-1.md:124-126,306-314"
  - id: F007
    severity: note
    category: test-coverage
    summary: "\"The NYSE regression test\" (Technical Requirements) is split across two tasks without cross-reference"
    location: "project-documents/user/tasks/221-tasks.cme-session-model-and-data-correctness-amendment-1.md:127-128,347-351"
---

# Review: tasks — slice 221

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [PASS] All eight functional requirements trace to tasks

Slice design criteria 1–8 (CME_EQUITY row + bound, session coverage, real-date checks, NYSE/NASDAQ byte-identity, `session_containing` semantics, extension clamp + reporting, `calendars list` fix, verification script) each map cleanly to specific tasks: 3.1/4.3/5.1–5.2 (criteria 1–2), 5.2/8.1–8.2 (criterion 3), 1.2/5.2 (criterion 4), 6.1/6.4 (criterion 5), 3.2–3.5/5.2 (criterion 6), 7.1 (criterion 7), 8.1–8.3 (criterion 8). No functional requirement is left uncovered.

### [PASS] Technical, integration, and walkthrough requirements are covered

The unit/integration test lists, the seed-table validations, the contract-amendment items (I11–I14, I7 exception, vocabulary rules, sequence-gap move, mapping table), and Verification Walkthrough steps 1–7 all have corresponding tasks (Sections 1, 2, 4, 9, and 10.2 respectively). `ruff`/`mypy` and the two-tier pytest run are explicit in 10.1, matching the project's mypy-invocation convention.

### [PASS] Sequencing is correct; no circular dependencies

Section 0 (freeze pre-change NYSE/NASDAQ fixture) runs before any `src/` edit, which is required since 1.2 and 5.2 diff against it. Section 3 (extension routine, which calls `populate_trading_sessions`) follows Section 1. Section 5 (migration 058) follows both Section 3 (extension) and Section 4 (seed table), which it needs. Section 6 (lookup, imports `CME_EQUITY_CALENDAR_ID` from `seed_cme_calendar` in 4.3) follows Section 4, and its integration tests need migration 058 (Section 5). Section 7 (CLI) and Section 8 (verify script) correctly follow Section 6. Section 10 is last. No forward references found.

### [PASS] Commit checkpoints distributed throughout, not batched

Eight distinct commit checkpoints appear across the two files (end of Sections 0–2 combined, 3, 4, 5, 6, 7, 8, 9, plus the final walkthrough commit), consistent with the project's "checkpoint per section" convention rather than one commit at the end.

### [CONCERN] `SessionIndex` unsorted-input behavior contradicts the slice design's own stated test requirement

The slice design's Technical Requirements list "unsorted or overlapping input raising" as a required `SessionIndex` test (project-documents/user/slices/221-slice.cme-session-model-and-data-correctness-amendment.md:354), and the API Contract comments `# sorted, non-overlapping; raises otherwise` (same file, line 297). Task 2.1 instead specifies the constructor "sorts by `open_utc`" and task 2.2's own test list expects unsorted input to be "sorted, then correct" rather than raising — i.e., a silent fix-up, not an error. This is a direct behavioral contradiction between the design's literal success criterion and the task's chosen implementation, and it also runs against CLAUDE.md's "never use silent fallback values, fail explicitly" principle: if a caller (e.g., 224) ever hands `SessionIndex` sessions in the wrong order due to a bug, this design silently reorders them instead of surfacing the bug. Resolve by either changing 2.1/2.2 to raise on unsorted input (matching the LLD), or by editing the slice design to explicitly state sorting is intentional and drop "unsorted... raising" from the test list — but the two documents must agree before implementation starts.

### [NOTE] `late_open` exception kind has no dedicated unit test

Success criterion 2 calls out sessions opening late "where a `late_open` exception says otherwise," and D5 allows `late_open` "if one ever occurs." Task 1.2's population test only exercises `closed` and `early_close`, and 4.4's seed-table validation only checks early-close times and closed rows, not a `late_open` case. This is likely low-risk since `late_open` may not occur anywhere in the actual 2020–present CME data (making a synthetic test the only option), but it is a criterion-2 phrase with no corresponding test task, worth a conscious call during 4.1/1.2 rather than silent omission.

### [NOTE] "The NYSE regression test" (Technical Requirements) is split across two tasks without cross-reference

The slice design names one item, "The NYSE regression test, run in the integration tier against a throwaway database... require equality" (slice design lines 358). The task breakdown satisfies this in substance via two separate tasks: 1.2's unit-level identity check against the frozen 0.1 fixture, and 5.2's integration-level `test_nyse_sessions_unchanged` (which matches the walkthrough's exact `pytest -k nyse_sessions_unchanged` reference). This is a reasonable, arguably stronger split (fast unit feedback plus integration-tier proof), but neither task cites the other or the LLD bullet by name, so a reviewer checking off "is the NYSE regression test done" has to infer the mapping. Not a functional gap — a minor traceability note only.

### Run Digest

- Response length: 5776 chars
- Response is newline-free: no
- Tool calls made: 7
- Tool calls failed: 0
- Stop reason: end_turn
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
