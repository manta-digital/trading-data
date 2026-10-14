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
reviewedSha: 2191d15a63f97ae1a85e80fcc5301ccad728e2c8
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 7
durationSeconds: 52.5
runId: run-20261014-p4-5335a10f
squadronVersion: 0.21.7
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Layering and dependency direction match the architecture"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md#Architecture"
  - id: F002
    severity: pass
    category: nfr
    summary: "Ingest-throughput NFR is restated with specific targets"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md#Implementation Notes"
  - id: F003
    severity: concern
    category: testing
    summary: "Test fixture rewrite couples API tests to the production universe"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md#Patterns and Conventions (TD10)"
  - id: F004
    severity: concern
    category: proportionality
    summary: "Seed scope is the heaviest option relative to Effort 2/5"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md#Technical Decisions (TD2)"
  - id: F005
    severity: concern
    category: documentation-consistency
    summary: "Architecture text is left disagreeing with the slice and the plan"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md#Technical Scope (Documentation)"
  - id: F006
    severity: note
    category: error-handling
    summary: "Failure handling for the one real purchase is inherited but not restated"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md#Verification Walkthrough (step 3)"
  - id: F007
    severity: note
    category: scope
    summary: "Open-interest deferral and spend authorisation await PM decisions"
    location: "project-documents/user/slices/232-slice.universe-expansion-gc.md#Technical Decisions (TD6, TD9)"
---

# Review: slice — slice 232

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Layering and dependency direction match the architecture

- `CME_METALS` goes in the production database's calendar tables via a minute-track migration. This is the carve-out the architecture allows ("Operational state has one home", "Session model").
- GC reaches the tick tier only through `TICK_UNIVERSE` and `calendar_for_product`, which I4's single session-query surface requires.
- There is no tick-track migration and no source-to-source import.
- The realtime-paths check, the Kalshi parity test and the cadence statement (manual) are all present, as the slice plan requires.
- The scope matches the slice plan entry: a second calendar, the mapping entry, a roll inside the purchased range, a manually fired pass, and no code change on the surfaces.

### [PASS] Ingest-throughput NFR is restated with specific targets

The architecture's "Ingest throughput and the write path" target is restated with 226's bounds: slowest unit at most 120 s, whole range at most 2 h. A miss fails the slice. Walkthrough step 3 measures it, and no redundant load test is added.

### [CONCERN] Test fixture rewrite couples API tests to the production universe

- TD10 removes the monkeypatch of `TICK_UNIVERSE` and `FUTURES_PRODUCT_CALENDAR` from the `two_products` fixture. The API integration tests would then run against the real universe, GC's range and the live `CME_METALS` seed.
- The architecture's premise is that expanding the universe is "a configuration edit followed by a cost estimate." Under this change, every later universe or range edit (the Standard-plan widening, a third product) breaks fixtures that assert on shape.
- The stated reason is that the synthetic `SECOND = ("GC", …)` collides with the real GC. Renaming the synthetic product removes that collision and keeps the isolation.
- Cut or reconsider: keep the monkeypatch and give the synthetic second product a non-real symbol. Add one separate test that asserts the real universe lists GC on `CME_METALS`.

### [CONCERN] Seed scope is the heaviest option relative to Effort 2/5

- The slice plan rates this Effort 2/5, Risk Low. The design commits to sourcing every metals exception from 2020-01-01 through 2027-12-31, one CME source comment per row. It also commits to a `pandas_market_calendars` cross-check with each difference resolved from a CME document and recorded.
- The only data consumed is 2024-11-02 → 2025-01-01. TD2 already admits the early years may be unsourceable and defines a fallback `CME_METALS_SEED_START`.
- The 2020 reach-back follows the PM's standing direction and 221's precedent, so I'm not asking to drop it. The Effort 2/5 rating understates it, because the sourcing is open-ended. Either re-rate the effort, or make the 2024-onward rows the required deliverable and the reach-back a time-boxed best effort, with the GitHub issue filed up front.
- Also cuttable: the slice defines the `CmeCalendarSeed` abstraction and the new `cme_calendar_insert(seed)` / `cme_holidays_insert(seed)` builders, but it still keeps 058's functions as one-line wrappers. State that the wrappers exist only to leave 058 and its tests untouched, since the seed-taking builders are what 059 uses.

### [CONCERN] Architecture text is left disagreeing with the slice and the plan

- The architecture's "Session model" still says "ES and GC both trade … so one row serves both." The slice plan notes record that 221 superseded this with separate calendars, and 232 now implements it.
- The architecture's "Active contract and roll methods" still describes open interest as something to evaluate, and TD9 defers it a second time.
- The slice's documentation list covers the contract mapping rows, the runbook, and the slice plan's Future Work item 2. It does not name an entry in the architecture's Revision Log. Every earlier slice that superseded architecture text added one (for example 229 and 230), and the log exists so the two documents don't disagree.
- Add a Revision Log task. It should record the calendar split, the 232 open-interest decision and, once measured, GC's spread share.

### [NOTE] Failure handling for the one real purchase is inherited but not restated

The slice adds no I/O path, and the pass, ingest and guards are unchanged. This step is nevertheless a real $8.43 purchase. The walkthrough expects every unit to end *ingested*. It does not say what the operator does if the 1,800 s wait budget ends with the job still at *submitted*. The architecture's manual regime makes downloading by the 30-day deadline the operator's job, with the deadline printed loudly. One line pointing to that rule would cover this. Failed units are already addressed, since the success criterion rules them out.

### [NOTE] Open-interest deferral and spend authorisation await PM decisions

Both decision points are flagged correctly: whether this out-of-plan purchase counts as one of the architecture's "rare purchases", and whether to confirm deferring open interest or ship it here (effort 2→4, a second purchase). The architecture does not forbid deferring. It requires the rule to be evaluated, and 229 handed that evaluation to 232. TD9 evaluates it with GC evidence (`n.0` rolls 6–12 days before `v.0`) and sets a trigger. Success criterion 9 correctly makes the PM's confirmation a gate.

### Run Digest

- Response length: 6604 chars
- Response is newline-free: no
- Tool calls made: 7
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 52.5 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
