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
reviewedSha: 3d2ec9f63957c1c769e093224440bd067524ea3a
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 119563
cachedTokens: 11198
cacheWriteTokens: 108361
completionTokens: 4347
durationSeconds: 36.6
costUsd: 0.478814
costBasis: provider
runId: run-20261109-p5-f5b47c48
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria coverage"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md"
  - id: F002
    severity: concern
    category: sequencing
    summary: "Task 3.3 success line cannot be met at its position"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md:122"
  - id: F003
    severity: concern
    category: coverage
    summary: "Criterion 12 is weakened by a fallback in 3.3"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md:121"
  - id: F004
    severity: concern
    category: accuracy
    summary: "Task 7.5 refers to a block that is not in the task file"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md:256"
  - id: F005
    severity: concern
    category: process
    summary: "\"Stop for a decision\" in 3.4 does not use the BLOCKED convention"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md:126"
  - id: F006
    severity: concern
    category: proportionality
    summary: "Verification and doc-edit tasks are heavier than the criteria need"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md:259-263"
  - id: F007
    severity: note
    category: load-testing
    summary: "3.4 load-test rewire has no CI wiring task, and D10's bound is a Phase 7 measurement"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md:124-129"
  - id: F008
    severity: pass
    category: sequencing
    summary: "Sequencing and commit markers"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md:75"
---

# Review: tasks — slice 306

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria coverage

Each criterion maps to a task, and no task lacks a criterion:
- **1:** 3.3(a)
- **2, 3:** 2.3
- **4:** 3.1, 3.3(b)
- **5:** 4.3(a)
- **6:** 7.4
- **7:** 5.2–5.7
- **8, 9:** 5.8
- **10:** 6.1–6.3
- **11:** 6.4
- **12:** 7.1–7.3
- **13:** 7.1, 7.4

Section 1 (the `family` parameter) and 3.4 (the load-test rewire) serve D3 and criterion 11. They are not scope creep.

### [CONCERN] Task 3.3 success line cannot be met at its position

The 3.3 success line requires all bars tests in `test_futures_series.py` to pass. That file contains `test_contract_bars_equal_the_cli` and `test_product_bars_across_the_roll_equal_the_cli`, which 3.3 keeps. The note at line 131 admits these pass only once task 4 lands. A junior implementer following 3.3 literally cannot reach its success state. Reword the success line to exclude the CLI-equality tests and move that check to 4.3. Alternatively, merge sections 3 and 4 into one task group with a single commit, which is where the commit already sits.

### [CONCERN] Criterion 12 is weakened by a fallback in 3.3

Success criterion 12 requires that "the 200 schemas of both bars routes are published". Task 3.3 says that if `response_class=Response` hides the 200 schema, the test should assert only `source` on the component and record the gap. In that case criterion 12 goes unmet and no task fixes it. Either add a conditional task to publish the 200 schema, or state that the criterion is deliberately narrowed. The slice design should then say so, since 7.5 already edits it.

### [CONCERN] Task 7.5 refers to a block that is not in the task file

7.5 says to remove the "Design facts the tasks rely on" block from this file's Context Summary. The Context Summary contains no such block. It has a "Two design/code differences, corrected in task 7.5" paragraph instead. Name the actual paragraph to remove. Also decide whether editing the task file's own summary belongs in a docs commit. The task-checker flow already edits this file.

### [CONCERN] "Stop for a decision" in 3.4 does not use the BLOCKED convention

If the rewired load test exceeds `PRODUCT_BARS_BOUND_S`, the task says to record the median and "stop for a decision". Project rules treat a wait on the Project Manager as invalid unless it is written as a `BLOCKED:` line. Rewrite it as a conditional with a concrete outcome. For example: record the measured median in the docstring, leave the bound unchanged, and mark the task `BLOCKED:` with the reason if the test fails.

### [CONCERN] Verification and doc-edit tasks are heavier than the criteria need

Three items could be cut or merged:
- **7.6 (final check):** it largely repeats 6.4 and 7.1–7.4 success lines. Its one distinct job is mapping criteria 1–13 to tests. Fold that into 7.4, or keep 7.6 as a single line, and drop the file-size re-check already stated in 2.2, 5.1 and 5.4.
- **6.2:** it dictates line ranges (~99–149) and per-case porting detail. "Remove `read_bars` tests, port any case with no counterpart" is enough.
- **5.8:** it is a separate task for a headings-only note. It could be a sub-item of 5.5.

### [NOTE] 3.4 load-test rewire has no CI wiring task, and D10's bound is a Phase 7 measurement

3.4 keeps an existing `test/load/` test passing on provider bars. It states that D10's 10 s bound is a one-off Phase 7 production measurement with no CI gate. The slice design decides this explicitly, so no finding is raised. If the existing 231 load test is already gated somewhere, nothing more is needed. If it is not, the rewire only protects a manual gate. The task does not say which case applies.

### [PASS] Sequencing and commit markers

The dependencies run in a sound order:
- **1 → 2:** the `family` parameter comes before the reader.
- **2 → 5:** `bar_reads` supplies the bucket SQL fragment that `bar_parity` imports.
- **6:** it deletes `read_bars` only after both consumers have moved.
- **Test-with pattern:** each implementation task is followed by its test, or has a test task in the same group. The 4.1–4.2 to 4.3 and 5.4–5.5 to 5.6–5.7 gaps are small.

Commit markers sit at six working, tested points. The deliberate gap between sections 3 and 4 is explained.

### Run Digest

- Response length: 5264 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 119563 / 11198 / 108361 / 4347 / not computed
- Duration: 36.6 s
- Cost: $0.48 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
