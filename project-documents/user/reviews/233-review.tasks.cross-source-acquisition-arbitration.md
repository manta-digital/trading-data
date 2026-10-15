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
reviewedSha: 9e6b848b660a669c4751ed3d94b36456bdd9f92d
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
durationSeconds: 18.2
runId: run-20261015-p5-e1391d71
squadronVersion: 0.21.7
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "All four functional success criteria map to tasks"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md"
  - id: F002
    severity: pass
    category: sequencing
    summary: "Sequencing and commit checkpoints are sound"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md"
  - id: F003
    severity: concern
    category: sequencing
    summary: "Test-with pattern is loose for the script entry point"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md:159-186"
  - id: F004
    severity: concern
    category: proportionality
    summary: "Task count and test granularity are heavy for a 1/5-effort slice"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md:55-381"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Task 1.1 and 1.5 dictate implementation detail the slice does not require"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md:99-120"
  - id: F006
    severity: concern
    category: risk
    summary: "Test fixtures depend on host-sourced lines with no fallback if the journal lacks a form"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md:79-86"
  - id: F007
    severity: note
    category: design
    summary: "Drift guard placement and independence"
    location: "project-documents/user/tasks/233-tasks.cross-source-acquisition-arbitration.md:232-235"
  - id: F008
    severity: note
    category: verification
    summary: "Doc-edit tasks lean on locations not verifiable here"
    location: "unverified"
---

# Review: tasks — slice 233

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] All four functional success criteria map to tasks

- SC1 (script prints three members, peaks, about 14 GiB) maps to 1.1–1.7.
- SC2 (`MemoryMax` plus its comment) maps to 2.1.
- SC3 (nothing installed, declared only) maps to the context summary, 3.1, 3.6 and 4.4.
- SC4 (stagger recorded with reopen triggers in the 900 plan and runbook) maps to 3.3 and 3.4.
- The technical requirements are covered too: parser, rule, discovery and drift-guard tests, ruff/mypy, the unit tier, and the three standing 220 obligations in 4.4.
- No NFR is restated in the slice, so no load test or CI gating task is needed.

### [PASS] Sequencing and commit checkpoints are sound

- Dependencies run forward only: baseline, then script, then slice file (which uses 1.7's output), then documents, then validation.
- 0.2 supplies the fixtures for 1.2.
- Commits come at 0.3, 1.7, 2.3, 3.7 and 4.5, so they are spread through the work.
- No merge step is written, which matches the project rule.

### [CONCERN] Test-with pattern is loose for the script entry point

- Tests for `main()` and the exit codes (1.6) follow the entry point (1.5), which is fine.
- 1.3 and 1.4 split a one-function rule from its tests. Merging them into one task would match how the other pairs are grouped.
- 1.4's second sub-item tests `max_peak` raising with the member named in the message, but 1.5 is where the name gets attached. The task text itself says "see 1.5", so this test cannot pass at 1.4. Move it to 1.6, or attach the member name inside the 1.4 scope.

### [CONCERN] Task count and test granularity are heavy for a 1/5-effort slice

- The breakdown has 24 tasks in 5 sections, for a slice the design re-rated to effort 1/5. Several tasks are one-liners: 0.3, 1.3, 1.7, 2.3, 3.7, 4.5.
- Suggested cuts:
  - Merge 0.1–0.3 into one baseline task, ending in a commit.
  - Merge 1.3 and 1.4 into one task.
  - Merge 3.1–3.3, which all edit runbook 100. Merge 3.4–3.6 too, which are small document edits.
  - Fold 4.1 and 4.2 into 2.3's verification, or into 4.3.
- 1.6 adds a "script created no file" assertion on top of the exit-code tests. The slice requires parser, rule, discovery and drift-guard tests, not entry-point tests. Cut that assertion, or drop 1.6 down to the two exit-2 cases.
- The "delete the line once, run, restore" step in 2.2 is useful. A single assertion that fails on a missing key is enough.

### [CONCERN] Task 1.1 and 1.5 dictate implementation detail the slice does not require

- The `configparser` option list is copied from an existing helper, and the `subprocess.run(check=False)` form is spelled out.
- The handler's exception types and the `RuntimeError` shape are specified too.
- The slice only requires lenient parsing, a raise carrying stderr, and exit code 2.
- Trim these to the behaviours: reuse `_parse` or the existing parsing approach, and name the exit codes. The implementer can choose the rest.

### [CONCERN] Test fixtures depend on host-sourced lines with no fallback if the journal lacks a form

- 0.2 requires four verbatim lines, including an `M`-peak minute line and a `1h 28min` Saturday line. If the 30-day journal lacks one of those forms, the task has no instruction.
- Add a stop rule: tell the Project Manager rather than hand-writing a synthetic line. A hand-written line would defeat the "real lines" requirement in the slice and in CLAUDE.md's parser-fixture rule.

### [NOTE] Drift guard placement and independence

- The guard repeats the `Slice=` check instead of importing the script. That duplicates a one-line check, which is acceptable given the stated reason.
- Make sure the "reuse if a clean import is possible" branch is not left to chance.

### [NOTE] Doc-edit tasks lean on locations not verifiable here

I did not open runbook 100, the 900 plan or the architecture log. The statements in 3.1–3.5 about step numbering, the "carries no resource limits yet" wording and the 2026-10-03 log entry are the task author's claims.

### Run Digest

- Response length: 4849 chars
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
- Duration: 18.2 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
