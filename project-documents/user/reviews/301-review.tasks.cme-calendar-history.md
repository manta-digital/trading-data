---
docType: review
layer: project
reviewType: tasks
slice: cme-calendar-history
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/301-tasks.cme-calendar-history.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261101
dateUpdated: 20261101
reviewedSha: c6057561494762b5c75b81cb9f80e6d4264ba61a
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 6
turns: 7
promptTokens: 348929
cachedTokens: 252396
cacheWriteTokens: 96521
completionTokens: 5372
durationSeconds: 47.8
costUsd: 0.465815
costBasis: provider
runId: run-20261101-p5-3ef67db9
squadronVersion: 0.24.10
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria coverage"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md"
  - id: F002
    severity: pass
    category: sequencing
    summary: "Sequencing, test-with pattern and checkpoints"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md"
  - id: F003
    severity: concern
    category: sequencing
    summary: "One shared `CME_DATASET_START` against per-calendar stop rules"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:121-125"
  - id: F004
    severity: concern
    category: process
    summary: "The sourcing research in 1.3 and 1.4 is held only in an uncommitted scratch note"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:118-120"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Test tasks that duplicate each other"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:376-401"
  - id: F006
    severity: note
    category: implementation
    summary: "Task 3.8 builder shape"
    location: "src/manta_trading/market/schema/seed_cme_calendar.py:450"
  - id: F007
    severity: note
    category: scoping
    summary: "Task sizing"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md"
---

# Review: tasks — slice 301

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria coverage

Every criterion maps to at least one task:
- **SC1:** 3.2–3.7 supply the sessions' exception rows; 4.3 checks the sessions have no gaps.
- **SC2 and SC8:** 4.2 compares sessions before and after, and 4.3 `test_cme_history_2020_unchanged` covers all four calendars.
- **SC3:** 1.6, 3.4 and 3.7 check for sources.
- **SC4:** 1.6 and 4.3.
- **SC5:** 1.2, 2.2 and 4.3.
- **SC6:** 4.4.
- **SC7:** 5.1 and 5.2.

6.1 maps each criterion to a test and adds one where it is missing. I found no scope creep. The `to_regclass` guard in 2.3, the grants check in 4.1 and the chain-pin updates in 4.5 each follow from a design requirement. I checked that `extend_calendar_sessions` takes explicit `start` and `end` (`session_extension.py:58`). Task 4.2's backward fill therefore needs no signature change.

### [PASS] Sequencing, test-with pattern and checkpoints

Dependencies run forward with no cycles: lookup, then eras, then population, then exceptions, then migration, then script, then validation. Each implementation task is followed by its test (1.1→1.2, 1.5→1.6, 2.1→2.2, 2.3→2.4, 4.1/4.2→4.3). A commit checkpoint closes each of the six sections (1.7, 2.5, 3.9, 4.6, 5.3, 6.2). The `cme_holidays_insert` and `closed_day` helpers named in 3.1 and 3.8 exist (`seed_cme_calendar.py:51,62,450`).

### [CONCERN] One shared `CME_DATASET_START` against per-calendar stop rules

The design defines one `CME_DATASET_START` for both calendars. The stop rules in 1.3, 1.4, 3.4 and 3.7 are written per calendar and can each move the constant. The tasks do not say what to do when ES and GC end up with different supported starts, or when 1.3 and 3.4 move it to different dates. They also do not say which calendar's gap wins. The unit tests in 1.6, 3.4 and 3.7 and the migration in 4.2 all read the constant, so an inconsistent result breaks them.

Add one sentence to the Working Rules: the constant is the latest of the supported starts across both calendars and both kinds of data (eras and exceptions). A calendar with an earlier supported start is trimmed to match, and the trim is recorded in the Implementation Notes.

### [CONCERN] The sourcing research in 1.3 and 1.4 is held only in an uncommitted scratch note

The era research is the riskiest and most expensive work in the slice. It has no commit checkpoint, and its output is a working-tree note that is explicitly not committed. A session break or a stray `git checkout` would lose the document list, and 1.5 depends on it. The citations also have to be re-entered by hand into the `source` strings.

Either commit the note as a document under `project-documents/user/`, or require each era's citation to be written directly into `seed_cme_hours.py` (or a stub of it) as it is found. The same applies to the exception research in 3.2, 3.3, 3.5 and 3.6, which does write `# source:` comments directly and is fine.

### [CONCERN] Test tasks that duplicate each other

These checks go beyond what the success criteria need:
- **Last era equals the `trading_calendars` row:** it is checked as a unit test in 1.6 and again as an integration case in 4.3. The design lists only the unit test.
- **First-session lower bound:** the 4.3 case "`CME_DATASET_START` equals the earliest session date's lower bound" overlaps the first 4.3 case, "first session on or before…".
- **3.7 and 3.4:** 3.7 repeats 3.4's cases against the metals module.
- **Builder tests in 3.8:** they check column match and row count, and 4.3 already exercises the inserts end to end.

Drop the integration "last era" case and merge the lower-bound cases in 4.3. Fold the 3.8 builder tests into the end-to-end check in 4.3, or keep only the column-match test.

### [NOTE] Task 3.8 builder shape

`cme_holidays_insert` takes a `CmeCalendarSeed` and reads `seed.exceptions`. The pre-2020 builder therefore has to pass a seed whose `exceptions` is the pre-2020 tuple, for example through `dataclasses.replace`. That reuses the existing column contract and is compatible with the "no `ON CONFLICT`" requirement. The task could say so, but a junior implementer can work it out.

### [NOTE] Task sizing

Tasks 1.3, 1.4, 3.2, 3.3, 3.5 and 3.6 are effort 3 each. They are bounded research tasks with explicit stop rules and are correctly split by calendar and period, so I recommend no change. Tasks 4.2 and 5.2 are the largest code tasks. Each does a single coherent job, and I would not split them further. A single task file for this slice is appropriate.

### Run Digest

- Response length: 5442 chars
- Response is newline-free: no
- Tool calls made: 6
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 7
- Tokens — prompt / cached / cache write / completion / reasoning: 348929 / 252396 / 96521 / 5372 / not computed
- Duration: 47.8 s
- Cost: $0.47 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
