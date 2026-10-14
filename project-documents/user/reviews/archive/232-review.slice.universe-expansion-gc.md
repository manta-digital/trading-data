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
reviewedSha: 2554fc7c99553db6cebed7155baa26eecf4be4e7
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 8
durationSeconds: 58.7
runId: run-20261014-p4-5335a10f
squadronVersion: 0.21.7
findings:
  - id: F001
    severity: concern
    category: proportionality
    summary: "Calendar seed range reaches back to 2020 although the purchased range is late 2024"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md#Technical Decisions (TD2, lines 132-142)"
  - id: F002
    severity: concern
    category: proportionality
    summary: "Refactoring applied migration 058 and renaming the recorder go beyond \"follow 221's pattern\""
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md:144-154, 200-207"
  - id: F003
    severity: concern
    category: nfr
    summary: "Ingest NFR is not restated for the GC ingest path"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md#Implementation Notes (Testing, line 367)"
  - id: F004
    severity: note
    category: scope
    summary: "Out-of-plan purchase is acknowledged but stretches the architecture's third stage"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md:64, 191"
  - id: F005
    severity: note
    category: scope
    summary: "Open-interest deferral is explicit, evidenced and gated on a PM decision"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md:209-215"
  - id: F006
    severity: pass
    category: architecture-alignment
    summary: "Architectural boundaries, dependency direction and integration points"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md#Architecture"
---

# Review: slice — slice 232

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Calendar seed range reaches back to 2020 although the purchased range is late 2024

TD2 and the Technical Scope require a sourced exception row for every weekday from 2020-01-01 to 2027-12-31. The data this slice buys and verifies is 2024-11-02 → 2025-01-01. The Risk section admits that pre-2022 metals schedules "may be unsourceable" because Wayback coverage has gaps. TD2 also forbids extrapolating those years. The mitigation is "report to the PM", so the slice can stall on 2020–2022 rows that nothing in it consumes.

The architecture's session model asks for the calendar to be populated "backward over the wanted range". It does not ask for it to match ES's 2020 start. The wanted range is Nov 2024 → Jan 2025, plus the plan year later.

Cut or defer:
- Seed from a start that covers the wanted range plus the Standard plan's included 12-month window, which ends 2027-12-31.
- Record the start as a declared lower bound, as 225 TD7 does for ES.
- Widening backward becomes a later, separately sourced edit.

This also drops the pandas_market_calendars cross-check for years that are never used. The slice plan entry (effort 2/5, risk Low) does not budget for eight years of sourcing.

### [CONCERN] Refactoring applied migration 058 and renaming the recorder go beyond "follow 221's pattern"

The slice plan scopes 232 as a configuration edit plus a calendar seeded "following 221's pattern". TD3 goes further. It renames the equity builders, moves the body of an already-applied production migration into a shared `_seed_cme_calendar`, and asserts that this changes no output. The design itself calls this "the only edit to an applied migration". The lighter option meets the requirement: the metals module defines its own `CmeCalendarSeed` instance and passes it to builders that take a seed argument. Existing 058 behaviour stays untouched, and the shared builders still avoid duplicated SQL. The design does not say why a signature-compatible extension is insufficient. If the refactor stays, record the reason, such as the DRY rule or the 440-line module size.

TD8 changes the recorder CLI to a required `--product` and renames the ES fixture files. That adds churn to a test that is deliberately "pinned to the data it was written against".

The design also contradicts itself on this point. The component diagram (line 86) says "`--product` (ES default kept explicit)", while TD8 says it is required with no default. Pick one. CLAUDE.md forbids hard-coded magic defaults, so the required form is the likelier intent.

### [CONCERN] Ingest NFR is not restated for the GC ingest path

GC is ingested through the existing tick-ingest path, and the architecture states an NFR for that path ("Ingest throughput and the write path"). A day's sessions for the configured universe must ingest far faster than a day of market time, and a month of sessions must ingest within an operator's working session. The slice doc does not restate this. It says only that no load test is needed because GC volume is 27% of ES, "already projected in 226". A projection is not a target the slice checks. The walkthrough ingests a roughly 4.13 M-record range with no timing check. Add the target to the slice, and record the GC ingest wall-clock in the walkthrough alongside the other recorded measurements. That costs one line in step 3 and ties the "no load test" claim to a measured number.

### [NOTE] Out-of-plan purchase is acknowledged but stretches the architecture's third stage

The architecture's sourcing sequence reserves out-of-plan purchases for rare cases such as studying an older period. It says the Standard plan is subscribed "ideally not before realtime work begins". The slice plan says 232 acquires "plan-included where possible". The design buys $8.63 outside the plan and labels it the "third stage". The reasoning is acceptable: the cost is small, the roll inside the range is needed, and PM ceilings gate the spend. The PM should confirm that this use counts as a "rare purchase" and not only authorize the ceilings.

The estimate command uses `--start 2024-11-01`, while the universe entry starts 2024-11-02 and the walkthrough plans days from 11-03. The design correctly makes the pass's `--estimate-only` figure the number of record, so this is only a presentation inconsistency.

### [NOTE] Open-interest deferral is explicit, evidenced and gated on a PM decision

The architecture and the 229 handoff require open interest to be evaluated, and TD9 does that with GC evidence (`n.0` rolls 6–12 days before `v.0`). Deferral to the first subscription month is consistent with the architecture's statement that the plan removes the purchase block. The trigger depends on a subscription date that is not set. Slice plan Future Work item 2 should therefore also say what happens if no subscription occurs, such as a consumer request or the 234 wiring slice. The PM decision point is correctly shown with its effort impact (2 → 4).

### [PASS] Architectural boundaries, dependency direction and integration points

- **Calendar placement:** `CME_METALS` is a second calendar row in the production minute-track calendar tables, reached through `calendar_for_product` and `extend_calendar_sessions`. That honors I4's single session function and the carve-out for shared reference data. The slice plan's note that the architecture's "one CME row for ES and GC" statement was superseded is respected.
- **Tick isolation:** no tick-track migration is added, and the tick → production coupling stays one-directional.
- **Reused machinery:** the 224 estimate → guard → manifest path, the spend ceilings, the 225 ingest checks and 229's roll rules and `VOLUME_RANK_LAG_SESSIONS` are used unchanged. A lag disagreement stops the slice instead of adding a per-product constant.
- **Spend:** the PM owns both ceilings, and no agent raises them.
- **Provider surfaces:** the design excludes pass, ingest, status and API code changes, and reports any needed change as a finding. It tests the "no code change" claim (TD10) by listing the three ES-only test assumptions.
- **Session verification:** the two-direction check (too narrow via the 225 boundary check, too wide via last-bar reads) covers a gap the architecture's boundary check cannot see.
- **Failure modes:** the slice adds no new I/O or message types. The one new write path, migration 059, is idempotent (`ON CONFLICT DO NOTHING`). A production calendar outage halts ingest and status as the architecture specifies. The purchase relies on the existing 1,800 s wait budget and the 30-day retention rule.
- **Realtime paths and cadence:** the realtime-paths check is present, and the cadence stays manual.

### Run Digest

- Response length: 7543 chars
- Response is newline-free: no
- Tool calls made: 8
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 58.7 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
