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
reviewedSha: c4f493b8204e7ea2b712213ba9f7fb7d73fe10ed
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 113033
cachedTokens: 8831
cacheWriteTokens: 104198
completionTokens: 3735
durationSeconds: 32.0
costUsd: 0.455805
costBasis: provider
runId: run-20261107-p5-5dab7324
squadronVersion: 0.24.23
findings:
  - id: F001
    severity: concern
    category: coverage-gap
    summary: "Integration requirements (health unchanged, second run plans nothing) have no task"
    location: "project-documents/user/tasks/304-tasks.first-real-purchase.md:136-141"
  - id: F002
    severity: concern
    category: sequencing
    summary: "PASS_ATTEMPTS placement conflicts with where the exit mapping lives"
    location: "project-documents/user/tasks/304-tasks.first-real-purchase.md:114,117"
  - id: F003
    severity: concern
    category: task-sizing
    summary: "Task 4.5 is overweight and should be split"
    location: "project-documents/user/tasks/304-tasks.first-real-purchase.md:136-141"
  - id: F004
    severity: note
    category: scope
    summary: "Phase 7 outcomes (criteria 2–4) are deliberately not tasks"
    location: "project-documents/user/tasks/304-tasks.first-real-purchase.md:37"
  - id: F005
    severity: pass
    category: traceability
    summary: "Remaining tasks trace to the design and are proportionate"
    location: "project-documents/user/tasks/304-tasks.first-real-purchase.md:48-175"
---

# Review: tasks — slice 304

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Integration requirements (health unchanged, second run plans nothing) have no task

The slice's Integration Requirements say `sudo mt-run data health` must exit as before and a second `sudo mt-run tick` must plan nothing. Walkthrough step 5 repeats both. Task 6.2's skeleton has a "Second-run result (nothing to buy, $0)" heading, but nothing in 4.5 produces that result.
- The evidence step runs the reconciliation, the status and coverage commands, the debug commands and the session check.
- It does not run a post-purchase `pass --estimate-only --json`.
- It does not run `mt-run data health`.
- Step 3's `nothing_to_buy` branch only helps on a re-run of the cutover. It does not verify the end state of the first run.

CLAUDE.md bars steps that wait on a person, so the cutover script has to do this. Add both commands to 4.5 and the exit rule. Add a 4.6 test that a post-purchase estimate other than `nothing_to_buy` is reported as a finding.

### [CONCERN] PASS_ATTEMPTS placement conflicts with where the exit mapping lives

Task 4.1 says `PASS_ATTEMPTS = 5` is named "at the top of the first-purchase script". The pass-exit mapping that consumes it, including the "after `PASS_ATTEMPTS` runs" error, is written in `cutover_304_checks.py`. 4.3 only creates the first-purchase script later, and that script imports the checks module. Either the checks module imports from its importer (circular) or the constant is duplicated.
- Define the constant once in `cutover_304_checks.py`, or pass the attempt count into the mapping function.
- Task 4.2 also says "the sixth-run case raises". With 5 attempts the failing run is the fifth, and the design says "up to 5 times". Fix the wording.

### [CONCERN] Task 4.5 is overweight and should be split

Task 4.5 holds the following in one task, for a junior AI:
- the reconciliation call;
- seven JSON evidence commands logged raw;
- a `verify_cme_sessions.py` run whose job directories must be derived from `debug requests` and the archive layout;
- the D6 classifier;
- the exit rule.

Its only success line is "within size guidance", which is not a behavioral check. Split it into two tasks, each with its own tests and a behavioral success line:
- **Evidence collection** (commands and the session check), plus the missing health and estimate commands from the first finding.
- **Classifier and exit rule**, as a pure function in `cutover_304_checks.py`. 4.6 already tests it separately.

Also add a commit marker after 4.4. That is a working, tested point (install, estimate and purchase loop), and Task 4 has six subtasks with a single commit at the end.

### [NOTE] Phase 7 outcomes (criteria 2–4) are deliberately not tasks

Success criteria 2 to 4 (production completes, `reconcile --cap-usd 7` exits 0, ledger equals raw) are verified only by the Phase 7 cutover run. The Context Summary states this and the script tasks make them achievable, so no gap exists. This is consistent with the rule against merge or release tasks in task files.

### [PASS] Remaining tasks trace to the design and are proportionate

Tasks 1 and 2 cover D1 and D4 with one test each, which matches the design's "one pytest". Task 3 covers D5, and its tests match the design's Technical Requirements list. The read-only session test, the provider-failure test and the required `--cap-usd` test are each tied to a design statement. Task 6 covers the runbook amendment and the evidence skeleton from the design's Included list. I found no scope creep. Task 5 is a validation pass tied to the design's format, lint, typecheck and tier requirements. It has a conditional commit marker, which is acceptable.

### Run Digest

- Response length: 4584 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 113033 / 8831 / 104198 / 3735 / not computed
- Duration: 32.0 s
- Cost: $0.46 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 5
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 5
- Finding-shaped matches — surviving validation: 5
