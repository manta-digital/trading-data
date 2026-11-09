---
docType: review
layer: project
reviewType: tasks
slice: serving-the-new-tiers
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/306-tasks.serving-the-new-tiers.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261108
dateUpdated: 20261108
reviewedSha: 39a6968e729be3f2c158615336d1703212bad26d
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 119246
cachedTokens: 11198
cacheWriteTokens: 108044
completionTokens: 4994
durationSeconds: 42.3
costUsd: 0.484014
costBasis: provider
runId: run-20261109-p5-f5b47c48
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria coverage is complete"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md"
  - id: F002
    severity: pass
    category: sequencing
    summary: "Sequencing and commit markers are sound"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md"
  - id: F003
    severity: pass
    category: nfr
    summary: "Load-test and CI handling is explicit"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md"
  - id: F004
    severity: concern
    category: task-sizing
    summary: "Two subtasks bundle too much work for one junior-AI unit"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md"
  - id: F005
    severity: note
    category: consistency
    summary: "The design text contradicts earlier tasks until task 7.5"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md"
  - id: F006
    severity: note
    category: clarity
    summary: "Task 6.4 points to an unreachable source"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md"
  - id: F007
    severity: note
    category: test-coverage
    summary: "No unit test for the bucket formula"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md"
---

# Review: tasks — slice 306

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria coverage is complete

Criterion to task mapping:
- **1:** tasks 3.2 and 3.3(a).
- **2, 3:** tasks 2.2 and 2.3. Item 2.3(c) and (e) cover bucket boundaries, and the DST and early-close tests cover criterion 3.
- **4:** task 3.1.
- **5:** tasks 4.1 to 4.3(a).
- **6:** task 7.4, which verifies issue #73.
- **7:** tasks 5.1 to 5.7.
- **8, 9:** tasks 5.5 and 5.8.
- **10:** tasks 6.1 to 6.3.
- **11:** task 6.4.
- **12:** tasks 7.1 to 7.3.
- **13:** task 7.4.

Every task traces back to a criterion or a design decision (D1–D10). I found no scope creep.

### [PASS] Sequencing and commit markers are sound

- **Dependencies:** each section depends only on earlier ones.
  - The plan family parameter in section 1 comes first.
  - The reader in section 2 follows.
  - The routes and `get` in sections 3 and 4 use it.
  - Parity in section 5 uses the shared `_plan` and `bar_reads`.
  - Deleting `read_bars` in section 6 comes after every consumer has moved off it.
- **Test pairing:** test tasks follow their implementation tasks (1.3→1.4, 2.2→2.3, 3.1–3.2→3.3, 4.1–4.2→4.3, 5.4–5.5→5.6–5.7).
- **Commit markers:** they sit at working, tested points. Sections 3 and 4 share one commit because the CLI-equality tests need both. The task file says so explicitly.

### [PASS] Load-test and CI handling is explicit

Task 3.4 rewires the existing 231 load test to provider bars. It keeps the original bound and states that the load tier runs by hand, so CI wiring is not left implicit. The slice's only new bound is D10's 10 s production measurement, and that is correctly left to Phase 7.

### [CONCERN] Two subtasks bundle too much work for one junior-AI unit

- **Task 2.3** is one checkbox holding several different jobs:
  - the 1m equality test;
  - the rollup test for five sizes;
  - the 4h boundary test;
  - the empty-window test;
  - the pre-306 boundary test;
  - the DBN sample decode check;
  - the DST test across 2024-11-03;
  - the early-close test, which needs hand-built bar files via `write_bar_file`.

  The DST and early-close tests need their own fixtures and a calendar lookup. Split 2.3 into (i) the fixture-day tests (a)–(e) plus the DBN check, and (ii) the DST and early-close tests. Criterion 3 would then have a clear owner.
- **Task 5.4** combines these jobs:
  - planning with `TICK_TIER`;
  - the second verdict call in the same `REPEATABLE READ` snapshot;
  - the session join and the skipped-session rule;
  - reading both sides into memory;
  - the full `exit_code_for` error mapping, including `OperationalError`;
  - promoting private helpers out of `tick_get_cmds.py`.

  Split it into (i) promoting the shared helpers and the snapshot plus verdict join with the skipped-session list, and (ii) the compare loop with failure mapping.

### [NOTE] The design text contradicts earlier tasks until task 7.5

Criterion 10 and the Migration Plan say `TICK_BAR_GRANULARITIES` is removed. Task 6.3 and D4 keep it. The exit-number wording in D7 and D8 also differs from the code. The Context Summary flags both differences and task 7.5 corrects them, so they are not blocking. The corrections land in the last commit, so every earlier commit leaves the design out of step with the code.

### [NOTE] Task 6.4 points to an unreachable source

Task 6.4 says "see memory: known flakes and the 051/052, `test_policy_advances_head` failures". A junior AI cannot resolve "memory". Name the failing test files in the task, or drop the reference. The run-it-on-the-slice-base rule that follows already covers distinguishing pre-existing failures.

### [NOTE] No unit test for the bucket formula

Design Implementation Notes call for "unit tests for the bucket formula". Task 2.2 deliberately keeps a single SQL fragment with no Python copy and tests its boundaries through the database in 2.3. This is a reasonable DRY choice and satisfies criteria 2 and 3, so no change is needed.

### Run Digest

- Response length: 4797 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 119246 / 11198 / 108044 / 4994 / not computed
- Duration: 42.3 s
- Cost: $0.48 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7

## Resolution

- **[PASS] Success criteria coverage is complete:** no change needed.
- **[PASS] Sequencing and commit markers are sound:** no change needed. The parity task renumbering (5.4 split) kept the order and the section 5 commit marker.
- **[PASS] Load-test and CI handling is explicit:** no change needed.
- **[CONCERN] Two subtasks bundle too much work:** applied. Task 2.3 is now 2.3 (fixture-day tests and the DBN check) and 2.4 (DST and early-close tests, which own criterion 3). Task 5.4 is now 5.4 (shared helpers, snapshot plan and session join) and 5.5 (compare loop and failure mapping). Later 5.x tasks were renumbered and the references to the parity tests (5.1, 6.2) updated.
- **[NOTE] The design text contradicts earlier tasks until task 7.5:** applied. The design is corrected now: `TICK_BAR_GRANULARITIES` is dropped from the "grep is empty" statements (Migration Plan and criterion 10), and `get`'s partial exit is stated as 3 (`EXIT_PARTIAL`) in D8 and walkthrough step 2. D7's exit 3 was already correct. Task 7.5 is deleted and the Context Summary note reworded.
- **[NOTE] Task 6.4 points to an unreachable source:** applied. The "see memory" reference is removed. The run-on-the-slice-base rule stays.
- **[NOTE] No unit test for the bucket formula:** rejected. Task 2.2 keeps one SQL fragment on purpose, to avoid a test-only Python copy, and 2.3/2.4 test its boundaries through the database. The reviewer says no change is needed.
