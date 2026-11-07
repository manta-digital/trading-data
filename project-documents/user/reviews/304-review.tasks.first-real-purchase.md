---
docType: review
layer: project
reviewType: tasks
slice: first-real-purchase
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/304-tasks.first-real-purchase.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261107
dateUpdated: 20261107
reviewedSha: 0c2b1141980ee7e4d8c6e010adf9299234a15504
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 113587
cachedTokens: 11198
cacheWriteTokens: 102385
completionTokens: 5112
durationSeconds: 43.4
costUsd: 0.46256
costBasis: provider
runId: run-20261107-p5-5dab7324
squadronVersion: 0.24.23
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria are covered and sequenced"
    location: "project-documents/user/tasks/304-tasks.first-real-purchase.md:49-190"
  - id: F002
    severity: concern
    category: completeness
    summary: "D7 \"before\" snapshots and run durations are never collected"
    location: "project-documents/user/tasks/304-tasks.first-real-purchase.md:152-153"
  - id: F003
    severity: concern
    category: correctness
    summary: "Cutover start instant is wrong on a resumed run"
    location: "project-documents/user/tasks/304-tasks.first-real-purchase.md:144"
  - id: F004
    severity: concern
    category: task-scope
    summary: "Task 5.3 is oversized, and the health baseline sits in the wrong task"
    location: "project-documents/user/tasks/304-tasks.first-real-purchase.md:151-158"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Test load and file count are heavy for a 2/5 slice"
    location: "project-documents/user/tasks/304-tasks.first-real-purchase.md:120-132"
---

# Review: tasks — slice 304

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria are covered and sequenced

- **Universe wants and pinning test:** criterion 1 maps to 1.1 and 1.2.
- **Ceiling script:** the shellcheck and the replace-and-refuse pytest map to 2.1 and 2.2.
- **Reconciliation:** its tests, including the job-list read that raises, map to 3.1–3.4.
- **Production run:** the run, ledger equals raw, health unchanged and the second estimate planning nothing map to 5.1–5.4.
- **Ceiling command in preflight:** 4.1 names it when the ceilings are short.
- **Validation and runbook:** these map to 6.1 and 7.1.
- **Sequencing:** pure logic comes before I/O, and each test task follows its implementation task. There are no circular dependencies.
- **Commit markers:** they sit at tested points. Task 6's conditional marker is acceptable.

### [CONCERN] D7 "before" snapshots and run durations are never collected

- **Definitions "before" count:** D7 and the evidence skeleton in 7.2 (line 186) require `tick_definition` growth "before and after per product". 5.3 runs `debug definitions` only after the purchase. No step in 5.1 captures it before step 4 buys anything. After the purchase the "before" count cannot be recovered.
- **Pass durations:** 7.2 also needs "pass and ingest durations for 305", which come from the `pass_runs` row via `mt data overview` (D7). No task runs `mt data overview` or records the row.
- **Fix:** add a pre-purchase `debug definitions --json` capture to 5.1, ahead of the buy step. Add `mt data overview` to the 5.3 evidence commands.

### [CONCERN] Cutover start instant is wrong on a resumed run

- 5.1 records "the cutover's start instant" and 5.3 passes it as `--since` to the reconciliation.
- Every step is meant to be re-runnable, and D3 expects a re-run after exit 6 attempts run out.
- A re-run would record a new, later instant. The reconciliation would then skip the first run's requests and jobs. Its totals and the `--cap-usd` check would cover only part of the spend. An unheld job from the first run would also go undetected.
- **Fix:** state how the first run's instant is kept across re-runs, for example by reading it from the existing narration file. The alternative is to say explicitly that `--since` must be the earliest start.

### [CONCERN] Task 5.3 is oversized, and the health baseline sits in the wrong task

- 5.3 bundles three things:
  - about eight evidence commands;
  - the classifier and session-check calls;
  - five separate judgements: reconciliation, ledger equals raw, health unchanged, second estimate, and the exit rule.
- The health baseline ("Before the install step, record the exit code of `sudo mt-run data health`", line 155) is a step-1/step-2 action. It is specified in 5.3, so a junior implementing 5.1 in order would not build it.
- **Fix:** move the baseline capture into 5.1's preflight. Split 5.3 into evidence collection and the pass/fail judgements. Pair the judgements with 5.4's tests, or fold them into task 4's pure-function pattern.

### [CONCERN] Test load and file count are heavy for a 2/5 slice

- The slice's Technical Requirements list tests only for the ceiling script and the reconciliation script.
- The task file adds four further test tasks for the cutover scripts: 4.2, 4.4, 5.2 and 5.4. It also adds two helper files, `cutover_304_checks.py` and `cutover_304_evidence.py`, to the slice's single cutover script.
- Tests on the cutover are defensible, because it spends money and cannot be rehearsed. But 4.2 and 4.4 each test a helper that 5.2 and 5.4 then cover again through the steps.
- 3.4 also tests argparse's own refusal of a missing `--since` and a missing `--cap-usd`. The slice does not require these tests. Keep the read-only-session and raising-job-list tests, which D5 and the success criteria do require.
- **Fix:**
  - Merge 4.2 and 4.4 into one test task.
  - Merge 5.2 and 5.4 into one test task.
  - Drop the two missing-argument tests from 3.4.
  - Keep the three-file split only if the 300-line limit actually requires it, and say so in the task.

### Run Digest

- Response length: 4809 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 113587 / 11198 / 102385 / 5112 / not computed
- Duration: 43.4 s
- Cost: $0.46 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 5
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 5
- Finding-shaped matches — surviving validation: 5

## Resolution

- [pass] Success criteria are covered and sequenced: no change needed.
- [concern] D7 "before" snapshots and run durations: changed. Task 5.1 records the `tick_definition` baseline before the install; task 5.3 compares against it and narrates request and unit counts and `pass_runs` durations (from `mt-run data overview`).
- [concern] Cutover start instant on a resumed run: changed. Task 5.1 writes the start instant to `304-cutover-start.txt` on the first run and reuses it on re-runs; the baselines are stored beside it the same way; 5.2 tests it.
- [concern] Task 5.3 oversized, health baseline misplaced: changed. The health baseline moved to 5.1 with the other baselines; 5.3 is now evidence collection (5.4 tests) and 5.5 is integration checks and the exit rule (5.6 tests).
- [concern] Test load and file count: changed. The evidence module is merged into `cutover_304_checks.py` (two files, not three), and the classifier and session check are folded into 4.1 with their tests in 4.2, removing one implementation and one test task.
