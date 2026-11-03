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
reviewedSha: 42c4395f44282a058f0ba4a48ec21e8d8c12f7e1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 101376
cachedTokens: 8791
cacheWriteTokens: 92581
completionTokens: 5253
durationSeconds: 42.5
costUsd: 0.424508
costBasis: provider
runId: run-20261101-p5-3165da8f
squadronVersion: 0.24.11
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria trace to tasks"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:41-262"
  - id: F002
    severity: pass
    category: sequencing
    summary: "Sequencing and commit cadence"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:67-261"
  - id: F003
    severity: concern
    category: task-sizing
    summary: "Sourcing tasks 6.1 and 6.3 are too large for one task each"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:153-164"
  - id: F004
    severity: concern
    category: hallucination-trap
    summary: "Hard-coded example values sit beside retrieve-from-source instructions"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:73"
  - id: F005
    severity: concern
    category: coverage
    summary: "Success criterion 1 (no gaps except weekends and sourced closures) has no direct check"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:200-209"
  - id: F006
    severity: concern
    category: proportionality
    summary: "Duplicate boundary-instant coverage in 6.5 and 7.4"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:174"
  - id: F007
    severity: note
    category: proportionality
    summary: "Minor granularity and detail observations"
    location: "project-documents/user/tasks/301-tasks.cme-calendar-history.md:44"
---

# Review: tasks — slice 301

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria trace to tasks

- **Criterion 1 (sessions from 2010-06-07):** task 7.4.
- **Criterion 2 (2020 onward identical):** migration steps in 7.2 and the populated-database test in 7.4.
- **Criterion 3 (sources):** 2.2 and 6.5.
- **Criterion 4 (latest era equals calendar hours, no era from 2020 on):** 2.2, 7.2 step 4 and 7.4.
- **Criterion 5 (era segmentation, `NoHoursEraError`):** 4.4.
- **Criterion 6 (`locate_sessions`):** 6.5 and 7.4.
- **Criterion 7 (NYSE/NASDAQ and 058/059 unchanged):** 3.2 and 4.4.
- **Criterion 8 (count check run, findings resolved or recorded):** 5.x and 8.x.
- **Verification Walkthrough:** steps 1–4 are in 10.2. Step 5 (production) is correctly left out, because merge and cutover belong to Phase 7.

I found no scope creep. Every task traces to a design decision (D1–D9).

### [PASS] Sequencing and commit cadence

- **Order:** the 058/059 snapshot (task 3) comes before any edit to `session_extension.py` (task 4). The migration (7) follows the seeds (2, 6) and extension (4). The migrated-database count run (8) follows the migration. No circular dependencies.
- **Commits:** each of the 10 task groups ends in its own `**Commit**:` marker, so nothing is batched at the end.
- **Load tests and CI:** the slice has no NFR, so no load test or CI-gate task is needed.

### [CONCERN] Sourcing tasks 6.1 and 6.3 are too large for one task each

- **Scope of each task:** 6.1 and 6.3 each require reading ten years of CME holiday documents for one product group. They also cover unscheduled closures and ET-to-CT conversion.
- **Why it matters:** the slice itself names source coverage as the main risk. A junior agent has no intermediate success criterion short of "all ten years".
- **Fix:** split each into year ranges, for example 2010–2014 and 2015–2019, with a working list per range. Tasks 1.2 and 1.3 are similar but smaller, because eras are few.
- **Fix:** also let 6.2 and 6.4 follow each range's sourcing, so seed entry stays close to sourcing.

### [CONCERN] Hard-coded example values sit beside retrieve-from-source instructions

- **Task 2.1:** it says to define `CME_DATASET_START` "with the value fixed in 1.4 (default `date(2010, 6, 6)`)". If 1.4 yields nothing, or the era sourcing moves the start, an agent will reach for the stated default. The CLAUDE.md rules also forbid a magic default.
- **Task 7.1:** it says to read the last migration id from `minute.py`, then names `065_instruments_name` in the same line.
- **Task 1.2:** it names the 16:15 to 16:00 CT forum lead beside the instruction to find the ES hours. The task correctly says not to enter it unless a dated document confirms it. An agent that finds no document may still reach for that value.
- **Fix:** drop the example values from the instructions. For 2.1, say to use the value recorded in the slice design's Findings section and stop if none is recorded. For 7.1, say to read the id from the file. For 1.2, say to record "unsourced" when no document is found. The forum-lead warning can stay in the slice design.

### [CONCERN] Success criterion 1 (no gaps except weekends and sourced closures) has no direct check

- **Gap:** 7.4 asserts only that both calendars start at 2010-06-07. Nothing asserts that the 2010–2019 sessions have no unexpected missing weekdays.
- **Indirect cover:** the count check (task 8) tests the gap windows against provider data, but it is a manual run and not a regression test.
- **Fix:** add one 7.4 assertion that every weekday in `[CME_DATASET_START, 2020-01-01)` has a session or a holiday row, and that the only dates without sessions are weekends and exception rows.

### [CONCERN] Duplicate boundary-instant coverage in 6.5 and 7.4

- **Duplication:** 6.5 adds boundary instants "in the pattern of `test_cme_equity_boundaries.py`" for each era change. The last bullet of 7.4 tests `locate_sessions` on the same instants (inside the hours and in the break, both sides of each change).
- **Missing dependency:** `locate_sessions` reads `trading_sessions`. The 6.5 version can only run once sessions exist, which is after task 7. That makes 6.5 ambiguous or misordered, because the migration comes later.
- **Fix:** say whether 6.5 is a pure data or fixture test of the instants. If it is, scope it to that. Otherwise move the instants fixture into 7.4 and remove it from 6.5.

### [NOTE] Minor granularity and detail observations

- **Read-only tasks:** 1.1, 4.1, 5.1, 7.1 and 9.1 are standalone "read" tasks with a "you can state…" success line. These could be folded into the first implementation step of their groups.
- **Rerun task:** 8.3 (rerun tests) could merge into 8.2.
- **Restated steps:** 7.2 restates the design's six Data Flow steps. A pointer to the design's Data Flow section would do. The restatement is harmless, but it can drift from the design.
- **Missing test paths:** 4.4, 5.3 and 9.3 give no test file path, unlike 2.2 and 3.2. Naming them would help a junior agent.
- **Commit type:** task 8's commit is `fix:`, but it adds sourced data and findings. `feat:` or `docs:` fits better.

### Run Digest

- Response length: 6061 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 101376 / 8791 / 92581 / 5253 / not computed
- Duration: 42.5 s
- Cost: $0.42 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
