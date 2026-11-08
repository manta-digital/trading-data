---
docType: review
layer: project
reviewType: tasks
slice: futures-completeness-surfaces
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261108
dateUpdated: 20261108
reviewedSha: 292614b2c881178d453c965e58004e17900099ec
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 120535
cachedTokens: 11198
cacheWriteTokens: 109333
completionTokens: 5602
durationSeconds: 47.1
costUsd: 0.495251
costBasis: provider
runId: run-20261108-p5-33ccd68a
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria 1–10 each trace to a task and a named test"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md"
  - id: F002
    severity: concern
    category: sequencing
    summary: "`accounting_lines` signature change in 4.1 overlaps the API wiring in 7.2"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:171"
  - id: F003
    severity: concern
    category: task-clarity
    summary: "\"Output unchanged except `source_edge`\" in 2.2 and 2.3 conflicts with the fields added in 2.1"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:105-118"
  - id: F004
    severity: concern
    category: commit-placement
    summary: "The `fix:` commit after 10.2 precedes its re-measurement"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:336-338"
  - id: F005
    severity: note
    category: ci-gating
    summary: "The load test has no CI gate, and 9.4 says so explicitly"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:301"
  - id: F006
    severity: note
    category: proportionality
    summary: "`cross_tier_result.py` is a new module that isn't in the slice's component list"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:103"
  - id: F007
    severity: note
    category: proportionality
    summary: "Task and file sizing fits a 4/5-effort slice"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md"
---

# Review: tasks — slice 305

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria 1–10 each trace to a task and a named test

- Criterion 1 is covered by 1.3 (the pin test).
- Criterion 2 is covered by 1.5.
- Criterion 3 is covered by 8.1.
- Criterion 4 is covered by 5.2 and 5.4, including the timeout row.
- Criterion 5 is covered by 3.5, 4.3, 5.4 and 7.3.
- Criterion 6 is covered by 3.3.
- Criterion 7 is covered by 3.5 and 4.3.
- Criterion 8 is covered by 4.3.
- Criterion 9 is covered by 6.3.
- Criterion 10 is covered by 7.3.
- The measurement requirements are covered by 9.x and 10.x.
- 11.2 re-checks the criterion-to-test mapping at close-out.

I found no scope creep. Every task traces to a design decision (D1–D10) or to a criterion.

### [CONCERN] `accounting_lines` signature change in 4.1 overlaps the API wiring in 7.2

- 4.1 changes `accounting_lines` to take `reports_by_product` and says "Update its callers."
- One of those callers is `api_server/tick_access.read_tick`, which 7.2 then rewires. The task file doesn't say who updates `read_tick`, or when.
- If 4.1 follows its own instruction, it silently does part of 7.2. If it doesn't, the API breaks between the task 4 commit and the task 7 commit, so the task 4 commit is not a working point.
- Fix: state in 4.1 that the `read_tick` call-site change belongs to 4.1, with the existing top-level fields derived from the tick-tier line. Then reduce 7.2 to adding `families`. Alternatively, keep the old signature as the adapter until task 7.
- 7.2 also doesn't say how the existing top-level `TickAccountingRecord` fields are derived from the per-family lines, even though 4.2 does say so for accounting JSON.

### [CONCERN] "Output unchanged except `source_edge`" in 2.2 and 2.3 conflicts with the fields added in 2.1

- 2.1 adds `records`, `cross_tier` and `load_complete_only` to `FamilyStatus`, along with `source_edge`.
- 2.2 and 2.3 claim `tick status` text and JSON change only by `source_edge`, and that golden files are regenerated only for that key.
- If status renders the dataclass directly, the golden diffs will also include `records` and `load_complete_only`.
- 302's status already prints a `load-complete only` wording. 3.4 then "wires" the label into status, which suggests it is already there.
- Fix: say whether status JSON and text expose `records`, `cross_tier` and `load_complete_only` in task 2 or only from 3.4. Name the expected golden-file diff precisely so a junior implementer can't regenerate the goldens blindly.

### [CONCERN] The `fix:` commit after 10.2 precedes its re-measurement

- The conditional `**Commit**` marker sits between 10.2 (the fix) and 10.3 (re-measure until every bound passes).
- A commit there records a fix whose effect is not yet verified, which is not a working, tested point.
- Fix: move the marker after 10.3, or merge 10.2 and 10.3 into one task. 10.4 then commits the note separately, as it does now.

### [NOTE] The load test has no CI gate, and 9.4 says so explicitly

- 9.4 records that the load tier runs only under `MT_RUN_LOAD_TESTS=1` and that `.github/workflows` has no load step, so no CI wiring is added.
- The slice design does not require CI gating, and a roughly 19.4 M-row seed on the shared test cluster would not suit CI.
- I'm not raising a finding because I can't name an unmet requirement. The decision is visible rather than implicit, which is what the guidance asks for.
- If you later want the bounds enforced automatically, that needs a separate slice or task.

### [NOTE] `cross_tier_result.py` is a new module that isn't in the slice's component list

- 2.1 adds `data/tick/cross_tier_result.py` to avoid an import cycle between `family_report.py` and `cross_tier_checks.py`.
- It is justified and tiny, so I'm not flagging it.
- The slice's Component Structure lists only three new `data/tick` modules, so mention the addition in the close-out or the slice notes.

### [NOTE] Task and file sizing fits a 4/5-effort slice

- One task file with 11 top-level tasks is proportionate.
- Test tasks follow their implementation tasks in tasks 1 through 7.
- Tests that touch several surfaces (5.2 and 5.4, or 3.5 and 4.3) cover distinct criteria with little overlap.
- 9.1–9.3 split one seed file into three steps. That is slightly granular but acceptable, because 9.3 is the expensive 19.4 M-row step and a separate checkpoint helps.

### Run Digest

- Response length: 5434 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 120535 / 11198 / 109333 / 5602 / not computed
- Duration: 47.1 s
- Cost: $0.50 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
