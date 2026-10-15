---
docType: review
layer: project
reviewType: tasks
slice: tick-pass-production-wiring
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261014
dateUpdated: 20261014
reviewedSha: 6cc42bcd731b61d42aaa1332807792d4119e0645
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 4
durationSeconds: 36.9
runId: run-20261015-p5-7ef29d51
squadronVersion: 0.21.9
findings:
  - id: F001
    severity: concern
    category: scope-alignment
    summary: "Task 3.3 changes ingest pass code that the slice design excludes"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md:61"
  - id: F002
    severity: concern
    category: task-sizing
    summary: "Task 3 is oversized and its only commit comes after ten sub-items"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md:159-276"
  - id: F003
    severity: concern
    category: coverage-gap
    summary: "No test covers `tick` appearing in the API overview response"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md:101-103"
  - id: F004
    severity: concern
    category: sequencing
    summary: "`PassKind.TICK` is committed before migration 060, so tests that seed every kind can break in between"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md:73-123"
  - id: F005
    severity: note
    category: task-placement
    summary: "`TICK_RETENTION_WARN_DAYS` is defined in Task 3.5 but only used from Task 4.1"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md:209-211"
  - id: F006
    severity: note
    category: proportionality
    summary: "Test volume and the two-file split are heavy for an effort-3 slice"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md:1"
  - id: F007
    severity: note
    category: scope-creep
    summary: "The CHANGELOG entry in Task 8.5 is not in the slice design"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:223-226"
  - id: F008
    severity: pass
    category: coverage
    summary: "Success criteria trace to tasks, and test-with ordering holds"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md:70-340"
---

# Review: tasks — slice 234

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Task 3.3 changes ingest pass code that the slice design excludes

The slice design's "Excluded" list says "Changes to … the tick pass logic". It also says (line 106) that the ingest pass already "accepts" a per-batch callback handed over by 225. Task 3.3 adds the field to `IngestInputs` and wires it into `_copy` in `ingest_worker.py`, because the callback does not exist. The task file's Context Summary notes this mismatch. No task corrects the slice design: Task 8.4 only fixes the architecture text, and Task 10.4 only refines the walkthrough. The design and the tasks will contradict each other after closeout. Add a sub-item to Task 8.4 or 10.4 that corrects the slice design's Data Flow step 2 and Excluded list to match what shipped. Task 3.3's own "stop and raise it with the PM" branch for worker processes is a sound guard.

### [CONCERN] Task 3 is oversized and its only commit comes after ten sub-items

Task 3 (effort 4) combines three separable pieces: the outcome fold and map (3.1–3.2), the ingest callback and heartbeat (3.3–3.6), and the verb itself with its tests (3.7–3.9). It has one commit, at 3.10. The callback change touches ingest worker code from slice 225. It deserves its own checkpoint, because it is the riskiest sub-item. Add commits after 3.2 and after 3.6, or split the task. This follows the instruction to distribute commit checkpoints.

### [CONCERN] No test covers `tick` appearing in the API overview response

Functional Requirement 4 says `/api/v1/overview` lists `tick` with a next firing of `manual`. Task 1.3 only "confirms" the API model can carry the value. Task 1.4 tests the CLI overview and `render_next_firings`. Task 2.3 checks only the schema enum. The only check on the API's tick entry is the manual walkthrough in Task 10.3. Add a unit test in Task 1.4 for the API `_pass_line` or the overview response with a tick pass.

### [CONCERN] `PassKind.TICK` is committed before migration 060, so tests that seed every kind can break in between

The slice design notes that `test_922_overview_nfr.py` seeds a row for every `PassKind`. After the Task 1 commit, `TICK` exists but the test cluster's constraint does not accept it until migration 060 lands in Task 2. Task 1.1 asks the implementer to grep for every `for kind in PassKind` loop. It does not mention integration tests that insert rows for each kind. State in Task 1.1 that these will fail between commits 1 and 2, or reorder so 060 comes first, and record the intermediate state in the commit message.

### [NOTE] `TICK_RETENTION_WARN_DAYS` is defined in Task 3.5 but only used from Task 4.1

Task 3.5 adds the constant "used by Task 4.1" alongside the heartbeat constant, which splits one feature across two tasks. Move it into Task 4.1. It is a minor point and does not block progress.

### [NOTE] Test volume and the two-file split are heavy for an effort-3 slice

Ten tasks across two files give about 45 sub-items, with a separate test sub-item after almost every implementation sub-item. Most of the tests trace to a Technical Requirement. Candidates to merge are 3.4 into 3.3 and 3.6 into 3.5, and 9.3 and 10.1, which are commit or ref housekeeping. The split into two files is defensible at roughly 700 lines combined. I would not cut test content, only fold the test sub-items into their implementation items.

### [NOTE] The CHANGELOG entry in Task 8.5 is not in the slice design

The slice design lists no changelog deliverable. It is a small convention-driven item and does no harm. It is flagged only because it traces to no success criterion.

### [PASS] Success criteria trace to tasks, and test-with ordering holds

Each slice design criterion maps to a task:

| Slice design criterion | Task |
|---|---|
| Schedule, manual-iff-no-timer, `PassKind` | 1.x |
| Migration 060 and `openapi.json` | 2.x |
| Fold, map, heartbeat, `run`, orphan test, ingest after `REFUSED`/`PROVIDER_ABORT`/`IN_FLIGHT` | 3.x |
| Health INFO semantics, retention, primary outage with exit 2 | 4.x |
| Unit, `KINDS`, deploy tests | 5.x |
| `add-tick-env.sh` | 6.x |
| Cutover steps 1–9 | 7.x |
| Runbook, contract diff, mapping rows, architecture Revision Log | 8.x |
| Static checks and test tiers | 9.x |
| Cutover run, double-start check, `/api/v1/futures` check, issues #33 and #34 | 10.x |

The `_pass_line` and `render_next_firings` callers are covered in 1.3. Tests follow their implementations in every task, and the sequencing has no circular dependencies. Commits fall at the end of each task and are not batched at the end. The slice design restates no load-test NFR, so no `tests/load/` task is needed.

### Run Digest

- Response length: 5934 chars
- Response is newline-free: no
- Tool calls made: 4
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 36.9 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
