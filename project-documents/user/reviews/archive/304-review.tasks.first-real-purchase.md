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
reviewedSha: 45d02303cf98b39c05e9fcbcea5b59c3c0ca07e5
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 113442
cachedTokens: 11198
cacheWriteTokens: 102240
completionTokens: 4144
durationSeconds: 35.4
costUsd: 0.452299
costBasis: provider
runId: run-20261107-p5-5dab7324
squadronVersion: 0.24.23
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria map to tasks"
    location: "project-documents/user/tasks/304-tasks.first-real-purchase.md:49-186"
  - id: F002
    severity: concern
    category: proportionality
    summary: "Task 4 is overweight, with four test tasks for one script"
    location: "project-documents/user/tasks/304-tasks.first-real-purchase.md:110-159"
  - id: F003
    severity: concern
    category: sequencing
    summary: "Test-with pattern broken in 4.5 and 4.6"
    location: "project-documents/user/tasks/304-tasks.first-real-purchase.md:137-148"
  - id: F004
    severity: concern
    category: scope
    summary: "Ambiguous file placement for the classifier"
    location: "project-documents/user/tasks/304-tasks.first-real-purchase.md:144"
  - id: F005
    severity: note
    category: consistency
    summary: "PASS_ATTEMPTS lives in the checks module, not the cutover script"
    location: "project-documents/user/tasks/304-tasks.first-real-purchase.md:115"
  - id: F006
    severity: note
    category: coverage
    summary: "No task asserts \"ledger equals raw\""
    location: "project-documents/user/tasks/304-tasks.first-real-purchase.md:150-153"
  - id: F007
    severity: note
    category: proportionality
    summary: "Validation pass is partly redundant"
    location: "project-documents/user/tasks/304-tasks.first-real-purchase.md:161-171"
---

# Review: tasks — slice 304

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria map to tasks

- **Criterion 1** (universe wants, pinned by a test) maps to 1.1 and 1.2.
- **Criteria 2–4** (production pass completes, reconciliation exits 0, verdicts and findings) map to the Phase 7 cutover script, tasks 4.3 to 4.8. The task file states that Phase 6 never spends money.
- **Ceiling script requirements** (shellcheck, replace and refuse tests) map to 2.1 and 2.2.
- **Reconciliation requirements** (unheld job, missing actual, delta, over cap, raising job-list read) map to 3.2 and 3.4. The read-only session test in 3.4 traces to the slice's D5 constraint.
- **Integration requirements** (health unchanged, second pass plans nothing) map to 4.8 and 4.9.
- **Runbook** maps to 6.1.

I found no gaps. The extras are small and trace to a D-decision: the "exactly at cap" case in 3.2 and the "both keys must already exist" refusal in 2.1.

### [CONCERN] Task 4 is overweight, with four test tasks for one script

- Task 4 has nine sub-tasks (4.1 to 4.9). Four of them are test-only tasks (4.2, 4.4, 4.7, 4.9) and share a single `**Commit**` at the very end. That is heavy for a slice rated 2/5 effort.
- Recommendation: merge 4.5, 4.6 and 4.8 into one "evidence, classification and exit rule" task with a single test task. Fold 4.7 and 4.9 into that.
- Add a `**Commit**` after 4.4. The install, estimate and purchase steps are tested at that point and give a working, reviewable increment.
- Several steps also dictate wording the slice does not need, such as the exact error strings in 4.1 and the fake-recorder mechanics in 3.4. Trim these to the behaviour that must hold.

### [CONCERN] Test-with pattern broken in 4.5 and 4.6

- Evidence collection (4.5) and the classifier and session check (4.6) are written back to back.
- Their tests are deferred to 4.7, and 4.8 builds on top of them before its own tests in 4.9.
- Tests should immediately follow their implementation.
- 4.7 also does not test 4.5's stated behaviour, that a non-zero exit from any evidence command stops the script with its name.
- Restructure as one implementation-plus-test pair per unit of logic, or merge as recommended above, and add that stop-on-failure test.

### [CONCERN] Ambiguous file placement for the classifier

- The 4.6 success line says the classifier and check are "in `cutover_304_checks.py` or its helper".
- Task 4 intro commits to exactly two files, and no helper file is defined.
- `cutover_304_checks.py` already holds the preflight, estimate and exit-mapping judgements from 4.1, and the classifier would come on top of those.
- That risks the ~300-line guideline, and a junior implementer will not know whether to create a third file.
- Decide now: either name a third file (for example `cutover_304_evidence.py`) or state that the classifier fits in `cutover_304_checks.py`.

### [NOTE] PASS_ATTEMPTS lives in the checks module, not the cutover script

- The slice's Patterns section says `PASS_ATTEMPTS` is named once at the top of the cutover script.
- The task puts it in `cutover_304_checks.py` and imports it into the first-purchase script.
- It is still defined once, so the intent holds. The placement is sensible because the exit mapping that uses it lives in the checks module.

### [NOTE] No task asserts "ledger equals raw"

- Success criterion 4 says `status` and `coverage` show ledger equal to raw in every session whose units ingested.
- 4.5 logs the raw coverage JSON, and the 4.8 exit rule deliberately excludes coverage findings.
- That matches the slice's rule that coverage findings do not fail the script, so this is acceptable.
- Verification of criterion 4 falls to the Phase 7 evidence note, which 6.2's skeleton covers.

### [NOTE] Validation pass is partly redundant

- Task 5 re-runs format, lint, mypy and the full tests, which earlier tasks already run per commit.
- It earns its place through the `git diff main` check for swept lines and the full unit and integration tiers, both of which the slice's Special Considerations require.
- The conditional commit marker is handled correctly.

### Run Digest

- Response length: 4974 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 113442 / 11198 / 102240 / 4144 / not computed
- Duration: 35.4 s
- Cost: $0.45 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
