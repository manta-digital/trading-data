---
docType: review
layer: project
reviewType: tasks
slice: eligible-rollups
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/172-tasks.eligible-rollups.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261016
dateUpdated: 20261016
reviewedSha: be16cb4a27f67c3224e981aec8391dcdf42ce9cf
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 123838
cachedTokens: 96164
cacheWriteTokens: 27670
completionTokens: 2650
durationSeconds: 22.0
costUsd: 0.147542
costBasis: provider
squadronVersion: 0.22.0
findings:
  - id: F001
    severity: concern
    category: coverage-gap
    summary: "Success criterion 1 asks for SPY, AVGO and MSFT values, but the tasks assert only SPY"
    location: "project-documents/user/tasks/172-tasks.eligible-rollups.md:192-194"
  - id: F002
    severity: concern
    category: coverage-gap
    summary: "Success criterion 3 is checked for `mt data health` only"
    location: "project-documents/user/tasks/172-tasks.eligible-rollups.md:216-218"
  - id: F003
    severity: note
    category: test-coverage
    summary: "Criterion 6 (rerun after a kill at any step) is only partly tested"
    location: "project-documents/user/tasks/172-tasks.eligible-rollups.md:264-274"
  - id: F004
    severity: note
    category: maintainability
    summary: "Brittle line references in task 1.2"
    location: "project-documents/user/tasks/172-tasks.eligible-rollups.md:89"
  - id: F005
    severity: pass
    category: structure
    summary: "Sequencing, sizing and commit checkpoints"
    location: "project-documents/user/tasks/172-tasks.eligible-rollups.md"
---

# Review: tasks — slice 172

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Success criterion 1 asks for SPY, AVGO and MSFT values, but the tasks assert only SPY

Slice success criterion 1 requires the SPY, AVGO and MSFT values from 171's criteria to be absent from the `_next` 5m and 1h rollups. Task 2.3 asserts only SPY 2026-09-08 (770.19 and 724.228), and it says "at least one real marked day". The slice's verification walkthrough step 2 (MSFT 2026-06-01, after-hours high near 487 raw versus near 460 eligible) appears in no test. Task 6.2 only records the walkthrough as run. Either add AVGO and MSFT assertions to 2.3, with the matching 171 fixtures named, or state that SPY alone satisfies criterion 1.

### [CONCERN] Success criterion 3 is checked for `mt data health` only

Criterion 3 says bars, `mt data status`, `/api/v1/status`, `mt data health` and the seeder return the same results before and after 063 and the build. Task 3.1 asserts unchanged results for `mt data health` and lists `caggs status`. Item 4 of 3.1 confirms the current objects' row counts, policy flags and definitions are unchanged. Nothing exercises the bars, `data_status` or seeder read paths against a partly built `_next` state. A cheap bars or `data_status` call in the 3.1 test would close this. Alternatively, name the object-unchanged check as the accepted proxy, since those readers resolve only current names. Section 6.1 correctly skips the load tier because no read path changes.

### [NOTE] Criterion 6 (rerun after a kill at any step) is only partly tested

Task 4.4 covers a leftover paused job (item 5) and a repair failure that still resumes (item 4). It does not cover a rerun when 063 is already applied or the version is already installed. Task 4.1 requires those skips, so one mocked test of them would match the criterion.

### [NOTE] Brittle line references in task 1.2

The task cites approximate line numbers (`cagg_repair.py` ~410, ~524, ~534; `cagg_parity.py` ~392; `minute.py` ~2441). They may drift, but they are marked approximate and the intent is clear. Keep them as hints and have the implementer find every `cagg_view` call site by search.

### [PASS] Sequencing, sizing and commit checkpoints

- **Sequencing:** the order follows the slice's Development Approach: targeting, then migration 063, then integration against real `_next` views, then the cutover script, then docs. There are no circular dependencies.
- **Tests:** each implementation section is followed by its tests (1.5, 2.3, 3.1, 4.4).
- **Commits:** there are six, one per section (1.6, 2.4, 3.2, 4.5, 5.2, 6.3), so none are batched at the end.
- **Proportionality:** one task file fits a slice of this size. No scope creep: every task traces to a criterion or scope item, including the migration chain-tip bump and the leftover-job resume.
- **NFR and load test:** the slice restates no NFR, so no load test or CI gating task is required.

### Run Digest

- Response length: 3571 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 123838 / 96164 / 27670 / 2650 / not computed
- Duration: 22.0 s
- Cost: $0.15 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 5
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 5
- Finding-shaped matches — surviving validation: 5
