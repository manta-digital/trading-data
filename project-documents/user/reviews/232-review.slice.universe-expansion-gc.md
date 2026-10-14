---
docType: review
layer: project
reviewType: slice
slice: universe-expansion-gc
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/232-slice.universe-expansion-gc.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261014
dateUpdated: 20261014
reviewedSha: f95202e55acc17572c634a1a45a022b61d689b67
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 5
durationSeconds: 49.7
runId: run-20261014-p4-5335a10f
squadronVersion: 0.21.7
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Architecture boundaries, dependency direction and the superseded \"one calendar\" statement"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md#Technical Decisions"
  - id: F002
    severity: pass
    category: nfr
    summary: "Ingest NFR and failure modes restated"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md#Testing"
  - id: F003
    severity: concern
    category: scope-and-cost
    summary: "GC's spread decision is made on ES's evidence, and the money is spent before GC's share is known"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md#TD5"
  - id: F004
    severity: concern
    category: proportionality
    summary: "Proportionality: the eight-year sourcing and the seed refactor are heavy for a slice whose verification uses two months"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md#TD2"
  - id: F005
    severity: concern
    category: scope-consistency
    summary: "\"No code change\" boundary contradicts the scope list"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md#Integration Requirements"
  - id: F006
    severity: note
    category: scope
    summary: "Open-interest deferral and PM decision points are explicit"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md#TD9"
---

# Review: slice — slice 232

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Architecture boundaries, dependency direction and the superseded "one calendar" statement

`CME_METALS` is a second `calendar_id` in the shared calendar tables on the minute track. It is reached through `calendar_for_product`. This matches "Session model" and I4, and the tick tier still only reads the calendar. The "ES and GC share one row" statement was already superseded by the 221 and slice plan notes, and this slice records it again in the Revision Log. The `extend_calendar_sessions` clamp and the auto-extension loop are reused, not forked. The tick → production dependency direction is unchanged, and the production-outage behaviour matches the four-edge description.

### [PASS] Ingest NFR and failure modes restated

The architecture's ingest-throughput target ("far faster than a day of market time", a month within an operator's working session) is restated with 226's concrete bounds: the slowest unit at most 120 s, and the whole range at most 2 h. A miss fails the slice. The failure modes are explicit:
- A job still in flight when the 1,800 s wait ends is printed with its deadline, and the next pass reconciles it.
- A production-calendar outage halts ingest.
- An unsourceable calendar year moves the seed start rather than guessing.
- A lag disagreement stops the slice for the PM.

### [CONCERN] GC's spread decision is made on ES's evidence, and the money is spent before GC's share is known

The architecture's 2026-09-28 Revision Log entry says ES is configured with spreads as an explicit decision (2.35% of the adopted trades) and that "GC decides for itself". TD5 rests on 226's finding that spreads are immaterial in bytes. That finding is about ES. GC's spread share is "measured after ingest and recorded ... not a gate", which is after the $8.43 purchase. The architecture's cost principle is that cost is visible before purchase. The definition bundle is bought with the tier and is only $0.02. The design should therefore measure the spread share of GC's definitions (or of the tbbo record count) before the tbbo purchase. It should also state what share would change the decision to explicit outright symbols. As written, the decision is justified by a different product.

### [CONCERN] Proportionality: the eight-year sourcing and the seed refactor are heavy for a slice whose verification uses two months

The slice must deliver a metals calendar that covers the purchased range of 2024-11 to 2025-01 and the 2025–2027 horizon. The design also sources every metals exception from 2020 through 2023 by hand from Wayback spreadsheets. It cross-checks against `pandas_market_calendars` year by year and records each difference. This is justified by the PM's standing direction on the 2020 target, and the effort is honestly re-rated from 2/5 to 3/5. Even so, 2020–2023 is not needed by anything the slice verifies, and the design itself calls it the main schedule risk. Three things can be cut or bounded:
- Timebox the pre-2024 sourcing and defer any year that is not found quickly. The design already allows a later `CME_METALS_SEED_START`, so make that the default rather than the fallback.
- Drop the Data Flow block and the full seven-step Walkthrough, which restate the success criteria and the commands in other documents.
- Fold the `CmeCalendarSeed` / builder refactor (TD3) into the minimum needed to avoid copying SQL. Keeping 058 and its tests frozen is already the stated intent.

### [CONCERN] "No code change" boundary contradicts the scope list

The Integration Requirements say no code changed "apart from the two constants, the seed module and the migration list". The Technical Scope and TD8 also change `scripts/record_229_roll_fixture.py`, so it gains a required `--product` option. TD10 changes three test fixtures and assertions, and TD3 refactors the existing equity seed builders. The exclusion and the requirement should name these changes explicitly. Otherwise the "if a surface needs more than that, a product assumption leaked" test is hard to apply. The script is not a surface, but the sentence as written would fail on the slice's own diff.

### [NOTE] Open-interest deferral and PM decision points are explicit

The architecture says open interest "must be evaluated when roll methods are designed", and 229 deferred the GC case to this slice. TD9 records the evidence (`GC.n.0` rolls 6–12 days before `GC.v.0`), a cost and size argument, and a trigger. It sends the decision to the PM, with an effort delta of 3 to 5 if the PM directs otherwise. That discharges the obligation without scope creep. The two PM decision points (whether this counts as a rare out-of-plan purchase, and the open-interest deferral) are properly gating and not hidden.

### Run Digest

- Response length: 5767 chars
- Response is newline-free: no
- Tool calls made: 5
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 49.7 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
