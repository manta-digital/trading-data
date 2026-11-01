---
docType: review
layer: project
reviewType: slice
slice: ohlcv-1m-and-definition-history
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: PASS
verdictSource: stated
sourceDocument: project-documents/user/slices/302-slice.ohlcv-1m-and-definition-history.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261101
dateUpdated: 20261101
reviewedSha: 496dfaa7647d1acc5711c5d5826b5d4001a0be86
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 101638
cachedTokens: 11162
cacheWriteTokens: 90472
completionTokens: 5071
durationSeconds: 41.7
costUsd: 0.414472
costBasis: provider
runId: run-20261101-p4-b4d7fe01
squadronVersion: 0.24.11
findings:
  - id: F001
    severity: pass
    category: alignment
    summary: "Scope matches the architecture's `ohlcv-1m` with definition history slice"
    location: "project-documents/user/slices/302-slice.ohlcv-1m-and-definition-history.md#Technical Scope"
  - id: F002
    severity: pass
    category: boundaries
    summary: "Family-scoped supersession and projector registry follow \"Generalising the archive\""
    location: "project-documents/user/slices/302-slice.ohlcv-1m-and-definition-history.md#D1, D3"
  - id: F003
    severity: pass
    category: cost-control
    summary: "Spend guard and production behaviour are preserved"
    location: "project-documents/user/slices/302-slice.ohlcv-1m-and-definition-history.md#D5"
  - id: F004
    severity: pass
    category: error-handling
    summary: "Failure modes are enumerated for the new ingest path"
    location: "project-documents/user/slices/302-slice.ohlcv-1m-and-definition-history.md#D3, D4, D10"
  - id: F005
    severity: note
    category: scope
    summary: "Health, accounting and overview are deferred against the architecture's \"ships with its completeness check\" principle"
    location: "project-documents/user/slices/302-slice.ohlcv-1m-and-definition-history.md#Technical Scope (Excluded)"
  - id: F006
    severity: note
    category: proportionality
    summary: "Candidates to cut if the slice needs trimming"
    location: "project-documents/user/slices/302-slice.ohlcv-1m-and-definition-history.md#D5, D8"
  - id: F007
    severity: note
    category: under-specification
    summary: "`FAMILY_LEDGER` coverage test wording is ambiguous for the definition family"
    location: "project-documents/user/slices/302-slice.ohlcv-1m-and-definition-history.md#Technical Requirements"
---

# Review: slice — slice 302

**Verdict:** PASS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Scope matches the architecture's `ohlcv-1m` with definition history slice

The architecture lists this slice as the schema, its table, `definition` back to 2010, contract identity and tier completeness. The design covers each:
- **Schema and table:** D1 and D2.
- **Definition history:** D5 and D10.
- **Contract identity:** the `has_definitions` prerequisite, with each bar want required to sit inside a definition want.
- **Completeness:** D4 and D6.

Bars get their own hypertable and are never written to `minute_ohlcv` or `tick_trade`, as the "Provider bars are a tier of their own" principle requires.

### [PASS] Family-scoped supersession and projector registry follow "Generalising the archive"

- **Family isolation:** `BAR_1M` is its own family, so a bar unit never supersedes a `tbbo` unit, and a later `ohlcv-1s` unit cannot delete an `ohlcv-1m` unit.
- **Registry:** the projector is registered in `INGEST_PROJECTORS`.
- **Companion rule:** the companion bundle stays a tick-tier rule.
- **Ledger separation:** keeping `tick_bar_ledger` apart protects the volume roll rule from double-counted bar volume.
- **Dependency direction:** it points one way, from 302 to 300 and 301, and 302 provides to 303 through 307.

### [PASS] Spend guard and production behaviour are preserved

Production keeps `history=()`, so the pass plans exactly what it plans today (criterion 9, walkthrough step 4). D5 names the $2.72 definition plan against the $0.50 ceiling and leaves that decision to 304. This keeps the principle that cost is never a surprise.

### [PASS] Failure modes are enumerated for the new ingest path

Each failure gets a named check, a message and a status outcome:
- `unit_day`, `resolution`, `session_boundary` and `overlap`, plus `shape` and `decode`.
- The `counts` mismatch (provider, decoded and stored).
- A pre-2020 definition with an undefined window fails its unit by name. The bar days then show `awaiting definitions` in status.

A bar with no CME session fails loudly and is never placed in a wrong session. The write happens inside the unit's ingest transaction, so a mid-unit disconnect rolls back. This inherits from 300's existing ingest path and is not restated.

### [NOTE] Health, accounting and overview are deferred against the architecture's "ships with its completeness check" principle

The architecture says a tier's slice also extends `health` and `accounting`. The design instead ships `status` and `coverage` and assigns the other surfaces to 305. It cites the slice plan entry for this split, which fits the architecture's separate "Futures completeness surfaces" slice. D4 labels the bar section `load-complete only`, so the surface says what it cannot prove, as "Completeness of sparse bars" requires. No action needed.

### [NOTE] Candidates to cut if the slice needs trimming

The design is large (11 decisions, effort 4/5) but matches a new tier with a table, projector, planner and status surfaces. Two pieces are used by nothing in this slice:
- **D8, the `ohlcv-1m` estimate row.** Only 304 consumes it. It could move to 304, though it is small.
- **Open-ended `HistoryWant.end=None`.** It adds the "open end needs an open definition end" validation rule. Production configures no wants, and 304 can set explicit ends.

Neither blocks progress.

### [NOTE] `FAMILY_LEDGER` coverage test wording is ambiguous for the definition family

The unit test says `FAMILY_LEDGER` covers "every family that has a projector". D2 maps only `TICK_TIER` and `BAR_1M`. The doc does not say whether the definitions family counts as having a projector. Definition units are projected by the acquisition pass (300 D3) and are not in `INGEST_PROJECTORS`. Restating the test as "every key family of `INGEST_PROJECTORS`" would remove the ambiguity.

### Run Digest

- Response length: 4668 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 101638 / 11162 / 90472 / 5071 / not computed
- Duration: 41.7 s
- Cost: $0.41 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
