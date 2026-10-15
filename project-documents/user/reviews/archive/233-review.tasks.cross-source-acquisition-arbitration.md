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
reviewedSha: 93bce99489682c7994e49d043e7973873063350c
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
durationSeconds: 51.4
runId: run-20261015-p5-e1391d71
squadronVersion: 0.21.7
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success-criteria coverage is complete"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md"
  - id: F002
    severity: concern
    category: test-design
    summary: "Discovery test pins the exact member set and will fail when 234 adds its unit"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md:108-110"
  - id: F003
    severity: concern
    category: commits
    summary: "Commit checkpoints are batched; tasks 1.1, 1.2, 2.1, 3.1 and 4.1 have no commit"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md:87-144"
  - id: F004
    severity: concern
    category: prompt-hygiene
    summary: "Expected values sit next to retrieval instructions (hallucination trap)"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md:76-77, 138-140"
  - id: F005
    severity: concern
    category: scope
    summary: "Drift guard asserts absence of `CPUWeight`/`IOWeight`, beyond the design and at odds with the reopen path"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md:167-170"
  - id: F006
    severity: concern
    category: git-workflow
    summary: "Branch target hard-coded as `main`; no step creates the slice branch"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md:45-46, 231-233, 257"
  - id: F007
    severity: note
    category: clarity
    summary: "Task 3.1 success line references text its steps never mention"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md:200"
  - id: F008
    severity: note
    category: clarity
    summary: "4.1 diff list is slightly miscounted"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md:231-233"
  - id: F009
    severity: pass
    category: structure
    summary: "Sequencing, sizing and proportionality fit an effort-1 slice"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md"
---

# Review: tasks — slice 233

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success-criteria coverage is complete

- **Functional 1 (script output):** tasks 0.1, 1.1, 1.2 and 1.3.
- **Functional 2 (`MemoryMax` plus comment):** task 2.1.
- **Functional 3 (declared, not in force):** the Context Summary, the CHANGELOG wording in 3.2, and the explicit statement in 4.2.
- **Functional 4 (stagger, evidence, triggers):** tasks 3.1 and 3.2.
- **Unit tests:** parser, rule, discovery and drift guard map to 1.1 and 2.2.
- **Lint, mypy and unit tier:** task 4.1.
- **Standing 220 obligations:** the Kalshi diff, realtime and data-correctness items are recorded in 4.2.
- **Walkthrough:** steps 1–3 appear in 1.3, 2.2, 2.1 and 4.2.

No task lacks a traceable criterion. Beyond the design's drift-guard spec, 2.2 adds assertions on `CPUWeight`/`IOWeight` and on at least one service naming the slice. I flag the `CPUWeight`/`IOWeight` assertion in its own finding below. No load-test or NFR obligation is restated in the slice, so none is required.

### [CONCERN] Discovery test pins the exact member set and will fail when 234 adds its unit

Task 1.1 asserts that discovery against the real `deploy/systemd/` returns "exactly the minute, daily and Kalshi services". The slice design says 234's tick pass unit will set `Slice=manta-acquisition.slice` and join the slice through the add-a-source step. That step is a supported path. The exact-set assertion would fail the moment 234 does it, and 234 would have to edit a 233 test to ship. Assert that those three are members (a subset check). Keep the `tmp_path` test for exclusion of units with no `Slice=` or another slice.

### [CONCERN] Commit checkpoints are batched; tasks 1.1, 1.2, 2.1, 3.1 and 4.1 have no commit

Project rules say to commit at least once per task. Tasks 1.1 and 1.2 (the script and its tests) go uncommitted until 1.3. Task 2.1 is committed only in 2.2, and 3.1 only in 3.2. Task 4.1 has no commit, though it may produce none. Add a commit at the end of 1.1, and one at the end of 1.2 or fold 1.2 into the 1.3 commit with a stated reason. Add a commit to 3.1. This slice is small, so the fix is cheap.

### [CONCERN] Expected values sit next to retrieval instructions (hallucination trap)

Task 0.1 tells the implementer to record the max peak from the journal, then puts "minute about 6.2 G" in the same success line. Task 1.3 puts "about 6.2 G, about 14 GiB" beside the run instruction. If a journal read returns nothing or something different, the nearest plausible value is the one written there. The 0.1 fixture step already handles missing journal lines, but the maxima step has no divergence rule. Remove the expected numbers from the success lines, or add an explicit rule. For example: "record what the journal shows; if a maximum differs materially from the design's inputs, stop and tell the Project Manager before 2.1."

### [CONCERN] Drift guard asserts absence of `CPUWeight`/`IOWeight`, beyond the design and at odds with the reopen path

The design's drift guard covers a parseable `MemoryMax` and every slice-naming unit being `Type=oneshot`. Task 2.2 adds "declares neither `CPUWeight` nor `IOWeight`" and "at least one `*.service` names the slice". The absence assertion will block a legitimate later change. For example, a reopened decision could add weights on a host that can enforce them. The design says the file's comment, not a test, is what keeps a reader from "fixing" it. Cut the absence assertion, or have the PM confirm it. The "at least one service" check is harmless but also beyond the design.

### [CONCERN] Branch target hard-coded as `main`; no step creates the slice branch

The conventions and 4.1 use `git diff main --stat`, and 4.2 claims "committed on the slice branch". Project rules say the target comes from `cf config get git.integration_branch`, falling back to `main`. If an integration branch is configured, the diff against `main` will list unrelated files and the "only these files" check fails. No task creates the `233-slice.…` branch from the target, even though 4.2 assumes it exists. Replace `main` with "the target (read from `cf config get git.integration_branch`, else `main`)". Add a one-line branch step to 0.1, or state that the branch is created before the tasks start.

### [NOTE] Task 3.1 success line references text its steps never mention

The Success line requires that "does not exist yet" be gone, but no step edits a phrase by that name. It presumably belongs to the second "limit of the pattern" bullet being rewritten. Say so, or drop the phrase. The "stop if a phrase is not found" rule in the section header covers a mismatch, but the intent is unclear.

### [NOTE] 4.1 diff list is slightly miscounted

The expected diff lists "the three documents, the runbook, the CHANGELOG". 3.1 and 3.2 touch four files: runbook, 900 plan, architecture and CHANGELOG. Reword it to name them, so a reviewer can match the diff without guessing which are the three.

### [PASS] Sequencing, sizing and proportionality fit an effort-1 slice

- **Dependencies:** they run in a clean line with no cycles. 0.1's fixture lines feed 1.1. 1.3's output feeds 2.1's comment and `N`. 2.1 precedes the drift guard in 2.2. Documents follow, then validation.
- **Test-with pattern:** 1.1 and 1.2 carry their tests, and 2.2 pairs the guard with the file change.
- **Size:** one task file with nine tasks across five sections is proportionate. Task 1.1 is the heaviest, but it is cohesive and under the design's size limits.
- **Test count:** it stays near what the criteria need.
- **No merge step:** the file correctly leaves out a merge, per the Phase 7 rule.

### Run Digest

- Response length: 7008 chars
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
- Duration: 51.4 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
