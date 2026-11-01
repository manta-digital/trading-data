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
reviewedSha: 6bfa16c4d030d79d7bb93cee243f481acfd6bb0b
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 99758
cachedTokens: 11162
cacheWriteTokens: 88592
completionTokens: 3746
durationSeconds: 34.6
costUsd: 0.393701
costBasis: provider
runId: run-20261101-p4-56a09d5c
squadronVersion: 0.24.11
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Scope, layering and provided interfaces match the architecture"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md#Integration Points"
  - id: F002
    severity: pass
    category: error-handling
    summary: "Failure modes and safety are enumerated for each new I/O path"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md#D8"
  - id: F003
    severity: concern
    category: proportionality
    summary: "Schedule abstraction changes a shared signature across about five callers for a data-only need"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md#D3"
  - id: F004
    severity: concern
    category: proportionality
    summary: "The count check is a heavy deliverable for a one-off, and its scope is not in the architecture"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md#D8"
  - id: F005
    severity: note
    category: under-specification
    summary: "The D1 schema is contingent on a sourcing result"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md#D1"
  - id: F006
    severity: note
    category: risk
    summary: "Source availability is the main delivery risk"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md#Risk Assessment"
---

# Review: slice — slice 301

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Scope, layering and provided interfaces match the architecture

- **Session day:** the design keeps the architecture's definition of a session day. Sessions are dated by close date and keyed per calendar and date in `trading_sessions`. Unit and manifest logic is left alone.
- **Provided interfaces:** `CME_DATASET_START` is defined once, which gives 302 its start gate. Session placement serves 302 and 303. Session open and close serve 306 and 308.
- **Layering:** `data/base` does not import from `market/schema`. The explicit `hours_dated` flag is read from the calendar row, so the dependency direction is preserved (D3).
- **Exclusions:** bars, statistics and definitions schemas, purchases, and NYSE/NASDAQ are all excluded, as the architecture requires.

### [PASS] Failure modes and safety are enumerated for each new I/O path

- **Count check:** `check_cme_history_counts.py` has explicit timeout, bounded retry for `ProviderTransientError`, a failed call that is never counted as zero, a hard stop on auth and bad-request errors, and a non-zero exit on any finding.
- **Migration:** it checks preconditions, compares 2020-onward rows before and after, and rolls back on any failure.
- **Cutover:** timers stay held if migrate fails (D7, D9).
- **No-era case:** a date before the first era raises `NoHoursEraError`, and there is no fallback to today's hours.
- The architecture states no NFR for this path, so none needs restating.

### [CONCERN] Schedule abstraction changes a shared signature across about five callers for a data-only need

- **What the design does:** it adds `HoursEra`, `HoursSchedule` and `HoursSchedule.undated(...)`, and changes the signature of `populate_trading_sessions`. Every caller is then edited:
  - migration 026
  - 058 and 059
  - `mt data extend`
  - `auto_extend`
  - `TradingCalendar._build_trading_hours`
- **Why that is heavy:** the slice must only make pre-2020 sessions come out with era-correct hours. That touches NYSE and NASDAQ paths that the slice itself says must stay unchanged.
- **Lighter option:** keep `populate_trading_sessions` taking fixed open and close. Have the dated-calendar reader in `session_extension.py` split a requested range at era boundaries and call it once per era segment. Sessions are keyed by close date, so a split at `effective_from` is clean, and the other callers stay untouched.
- **What this would cut:** the schedule class, the `undated` constructor, the caller edits, and most of D7's pinning machinery. The fresh-output regression test for 058 and 059 would still be useful.
- **Action:** either adopt the segmenting approach, or add a sentence explaining why a per-segment call cannot work.

### [CONCERN] The count check is a heavy deliverable for a one-off, and its scope is not in the architecture

- **What the design does:** it commits a script making about 9,600 sequential metadata calls, with timeout, bounded backoff, pacing, a `--start`/`--end` split, a stubbed-adapter unit test and a calibration run. Success criterion 8 makes a clean run (or recorded findings) part of the slice's definition of done.
- **Why that is heavy:** the architecture gives 301 only the dating of hours and sessions. Per-session completeness verdicts belong to 305. The design itself says no later slice uses this script.
- **Value:** the evidence is real. It finds wrong days before money is spent, and it is free.
- **Action:** keep the check, but state it as a throwaway verification run. Drop the permanent-script polish, specifically the pacing and the unit-tested window logic beyond what the run needs. Alternatively, move the success-criterion gate to "run once, findings recorded".

### [NOTE] The D1 schema is contingent on a sourcing result

- The `trading_calendar_hours` columns are final only if no era's Sunday open differs from its daily reopen.
- Implementation Notes step 1 gates this correctly. A difference would reopen D1 and the slice's schema before any code is written.
- This is a bounded and explicit risk, not a defect. It does mean the design should be re-read after step 1 completes.

### [NOTE] Source availability is the main delivery risk

- The ES close move around September 2015 is only a forum lead. The design handles this correctly: D2 refuses an unsourced era, and D5 moves `CME_DATASET_START` later if needed.
- If that fallback fires, 302 and 303 receive a later dataset start than the architecture's 2010-06-06 goal. Downstream slices should read the constant and not hardcode the date.

### Run Digest

- Response length: 5496 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 99758 / 11162 / 88592 / 3746 / not computed
- Duration: 34.6 s
- Cost: $0.39 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
