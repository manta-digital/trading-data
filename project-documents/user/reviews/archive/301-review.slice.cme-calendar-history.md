---
docType: review
layer: project
reviewType: slice
slice: cme-calendar-history
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/301-slice.cme-calendar-history.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261101
dateUpdated: 20261101
reviewedSha: 00f21f4363010d74f031de900627e76cca3a6b7a
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 99151
cachedTokens: 11162
cacheWriteTokens: 87985
completionTokens: 3129
durationSeconds: 28.8
costUsd: 0.385103
costBasis: provider
runId: run-20261101-p4-56a09d5c
squadronVersion: 0.24.11
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Alignment with the session-model and \"what a day is\" contract"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md#Overview"
  - id: F002
    severity: pass
    category: error-handling
    summary: "Failure modes for the new metadata I/O path are enumerated"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md#D8 — A free count check against provider data"
  - id: F003
    severity: concern
    category: dependency-direction
    summary: "Dated-calendar set is defined in a seed module but consumed by runtime session code"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md#D3 — Population takes an hours schedule; no era is an error"
  - id: F004
    severity: concern
    category: under-specification
    summary: "The `trading_calendar_hours` schema is \"decided\" but conditional on unsourced facts"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md#D1 — Regular hours become dated eras"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Proportionality: separate cutover script for a rows-only change"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md#D9 — Production rollout is a cutover script"
  - id: F006
    severity: concern
    category: proportionality
    summary: "Proportionality: a one-off count-check script with an unquantified call volume"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md#D8 — A free count check against provider data"
  - id: F007
    severity: note
    category: risk
    summary: "Migration safety and the 2020-onward invariant"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md#D6 — One migration, plain inserts, a before-and-after proof"
  - id: F008
    severity: note
    category: risk
    summary: "Source-coverage risk is bounded by an explicit stop rule"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md#Risk Assessment"
---

# Review: slice — slice 301

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Alignment with the session-model and "what a day is" contract

The slice covers 2010-06-06 to 2019-12-31 and defines `CME_DATASET_START` once. It keeps the architecture's split between the CME session and the UTC-day unit. It leaves `trading_sessions` as the single source that 302, 303, 306 and 308 read. It restates the 221 D5 rule that exceptions are never generated from a rule. The "Provides to Other Slices" list matches the consumers the architecture's anticipated slices expect.

### [PASS] Failure modes for the new metadata I/O path are enumerated

D8 names a per-call timeout and a bounded retry with backoff on `ProviderTransientError`. It prints a failed session and never counts it as zero. It stops the run on auth or bad-request errors. It reruns a range with `--start` and `--end` instead of keeping resume state. This matches the architecture's rule that completeness is never inferred from absent data.

### [CONCERN] Dated-calendar set is defined in a seed module but consumed by runtime session code

`DATED_HOURS_CALENDARS` and `CME_DATASET_START` live in `market/schema/seed_cme_hours.py`. `data/base/session_extension.py` (the runtime reader) must import `DATED_HOURS_CALENDARS`, and 302 must import `CME_DATASET_START`. That makes runtime and consumer code depend on a migration-seed module. Check that `data/base` already imports from `market/schema` before accepting this. If it doesn't, define the constants in `data/base/session_hours.py` or a shared constants module, and let the seed data import them.

### [CONCERN] The `trading_calendar_hours` schema is "decided" but conditional on unsourced facts

D1 is marked `decided`, yet a Sunday-open column is added if sourcing finds a difference. Implementation Notes step 1 sources this before any code is written, so the risk is contained. The status should say so: either mark D1 as provisional on step 1, or state that a documented Sunday-open difference reopens the schema. As written, the migration DDL, `HoursEra` and the schedule lookup are all fixed at "decided" but could change.

### [CONCERN] Proportionality: separate cutover script for a rows-only change

D9 adds `scripts/cutover_301_calendar_history.py`. The slice's DDL is one small table, and everything else is rows. The cutover is justified only by the D7 window, where the new code is installed but the migration is not yet applied and CME extends fail. A lighter option meets the requirement. Have the migration step handle ordering, and use the existing `cutover_common` flow or a documented command sequence. The script's own verify step (first session date, `data health`, `overview`) could then be a few lines in the walkthrough. Either cut the script or justify why `cutover_common` alone cannot hold the timers across install and migrate.

### [CONCERN] Proportionality: a one-off count-check script with an unquantified call volume

D8 says no later slice uses the script, and 305 builds its own verdicts. That makes it a throwaway kept "as a record". Keep it, because it serves the architecture's check-before-spend principle, but limit it to what the one run needs. The retry, backoff and explicit timeout design is reasonable. The design does not give the call volume (about 2 calls × 2,500 sessions × 2 calendars, roughly 10,000 calls) or say whether Databento metadata rate limits apply. Add that number and a pacing or concurrency statement, since the throughput of this path decides whether the run finishes in a sitting. Alternatively, state that sequential calls are adequate.

### [NOTE] Migration safety and the 2020-onward invariant

The in-migration comparison of 2020-onward sessions, the plain `INSERT` and the hard-fail preconditions give the architecture's "never splice silently" posture at the calendar level. Pinning 058 and 059 to their own hours with a snapshot test (D7) is heavy but justified, because the dated-calendar rule would otherwise break fresh-database builds. I have no finding on it.

### [NOTE] Source-coverage risk is bounded by an explicit stop rule

D2 and D5 let `CME_DATASET_START` move later instead of entering a guessed row. That is consistent with the architecture's "never splice" and "file-first" principles. The architecture's goal of bars from 2010-06-06 may therefore not be fully met. Record the outcome for the PM as the slice already plans, because 302's and 304's history ranges depend on it.

### Run Digest

- Response length: 5854 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 99151 / 11162 / 87985 / 3129 / not computed
- Duration: 28.8 s
- Cost: $0.39 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
