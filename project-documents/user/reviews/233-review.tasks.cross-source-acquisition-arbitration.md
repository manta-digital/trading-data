---
docType: review
layer: project
reviewType: tasks
slice: cross-source-acquisition-arbitration
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261014
dateUpdated: 20261014
reviewedSha: 23f17c7b629c019da98a99c7a058c4c839193c1e
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
durationSeconds: 18.6
runId: run-20261015-p5-e1391d71
squadronVersion: 0.21.7
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria coverage"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md"
  - id: F002
    severity: concern
    category: sequencing
    summary: "Task 2.2 puts the commit before its own success check, and the failure-injection step is awkward"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md:172-185"
  - id: F003
    severity: concern
    category: coverage
    summary: "Drift guard does not check that the value matches the derivation"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md:172-180"
  - id: F004
    severity: concern
    category: scope
    summary: "Task 1.1 is on the heavy side and bundles four functions with a test file"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md:91-122"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Task 4.1 is largely repeated verification already done per task"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md:239-252"
  - id: F006
    severity: note
    category: sequencing
    summary: "Baseline task 0.1 depends on host state and on the design's facts being unchanged"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md:61-82"
  - id: F007
    severity: note
    category: process
    summary: "Commit checkpoints and load tests"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md"
---

# Review: tasks — slice 233

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria coverage

Every slice success criterion maps to a task.
- **SC1** (script output, three members, about 14 GiB): tasks 1.1–1.3.
- **SC2** (`MemoryMax` plus comment): task 2.1.
- **SC3** (declared, not installed): the context summary, the runbook and CHANGELOG wording in 3.1 and 3.2, and the 4.2 statement.
- **SC4** (stagger, evidence and triggers recorded): 3.1 and 3.2.
- **Technical Requirements**: the unit tests are in 1.1, 1.2 and 2.2, the unit tier run is in 4.1, and the three standing 220 obligations are in 4.2.

### [CONCERN] Task 2.2 puts the commit before its own success check, and the failure-injection step is awkward

The 2.2 success check requires deleting the `MemoryMax` line to prove the guard fails, then restoring it. That is a manual mutation of a committed file, and it has no recorded outcome. The item order also lists "Success" before "Commit". This is minor, but a junior could commit the mutated file. Reword it to say explicitly that the line is restored and `git diff` on the slice file is clean before committing. Alternatively, drop the mutation and rely on a `tmp_path` negative test inside `TestAcquisitionSlice`. The `tmp_path` test would also be repeatable.

### [CONCERN] Drift guard does not check that the value matches the derivation

SC2 says `MemoryMax` equals the script's result. The drift guard checks only that the value is a positive integer followed by `G`. The slice design deliberately keeps the guard standalone, since the journal needs the host. The equality check therefore lives only in 4.2, as a manual re-run. That is acceptable, but 2.1 has no step that reconciles the file value with 1.3's output beyond "N being the script's result". It is also not clear what the junior does when the 1.3 output and the current window differ (the window moves daily). Task 4.2 handles this. No change is required, but 2.1 should point to 4.2 for the final reconciliation.

### [CONCERN] Task 1.1 is on the heavy side and bundles four functions with a test file

Task 1.1 covers constants, discovery, the parser, max-peak and the rule, plus a new test package with four test groups, all for Effort 2. This is still completable. However, two items are more detailed than the slice requires:
- The "6.2 G + 426 M + 317 M gives 14" rule test hard-codes today's host inputs. Once production data changes, the numbers in the test remain valid only because they are fixtures, so this is fine.
- The "exact-GiB sum is not rounded up" case is a boundary test that the success criteria do not name.

Consider trimming the boundary case, or accept it as cheap. This is a NOTE-level proportionality point.

### [CONCERN] Task 4.1 is largely repeated verification already done per task

Tasks 1.2, 1.3 and 2.x already require ruff, mypy and test passes before each commit. 4.1 repeats those checks. The unit-tier run (`scripts/run_tests.py unit`) and the diff-scope check are the parts that add value. Merge the repeated lint and mypy items into one line, or keep only the unit tier and the diff-scope check. Task 4.1 and 4.2 could also be merged, since both are small close-out steps with a single commit.

### [NOTE] Baseline task 0.1 depends on host state and on the design's facts being unchanged

Task 0.1 sensibly stops on any difference from the design's host facts and requires synthetic fixture lines to be marked as such. This respects the project rule against silent fallbacks. No change is needed. The `{YYYY-MM-DD}` placeholder is clear and has no example value, so it is not a hallucination trap.

### [NOTE] Commit checkpoints and load tests

Commits are spread across every task except 4.1, which commits only if a fix is needed. The test-with pattern holds: tests accompany the implementation in 1.1, 1.2 and 2.2. The slice restates no NFR, so no `tests/load/` task or CI gating task is needed. The task list contains no merge step, which follows the Git Rules. The single task file for a one-effort slice is proportionate.

### Run Digest

- Response length: 4827 chars
- Response is newline-free: no
- Tool calls made: 2
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 18.6 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
