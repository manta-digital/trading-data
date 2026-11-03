---
docType: review
layer: project
reviewType: slice
slice: statistics-and-the-open-interest-roll-rule
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261103
dateUpdated: 20261103
reviewedSha: 9cd38b19262351b3e52f63e6164186a90da355b0
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 4
turns: 5
promptTokens: 289050
cachedTokens: 185759
cacheWriteTokens: 103283
completionTokens: 7084
durationSeconds: 66.0
costUsd: 0.503333
costBasis: provider
runId: run-20261103-p4-7756c473
squadronVersion: 0.24.16
findings:
  - id: F001
    severity: pass
    category: alignment
    summary: "Statistics semantics and the family model follow the architecture"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md#D4 — Session assignment from `ts_ref`"
  - id: F002
    severity: pass
    category: alignment
    summary: "Completeness ships with the tier, and the split with 305 is consistent with the slice plan"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md#D7 — The family's verdict: load, then content"
  - id: F003
    severity: concern
    category: integration
    summary: "Tick and bar volume are mixed per session, but the API surface does not name the basis"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md#D10 — The volume rule's basis per session"
  - id: F004
    severity: concern
    category: integration
    summary: "The 305 interface for the volume check is under-specified"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md:269"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Proportionality: some mechanisms exceed what this slice needs"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md#Technical Scope"
  - id: F006
    severity: note
    category: scope
    summary: "Real spend in this slice departs from the architecture's wording"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md#D11 — The real purchase and the evaluation"
  - id: F007
    severity: note
    category: error-handling
    summary: "Failure modes for the purchase and ingest are handled by reuse, with one hard-fail policy to watch"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md#D4 — Session assignment from `ts_ref`"
  - id: F008
    severity: note
    category: alignment
    summary: "Dependency directions and the roll-path timeout are stated correctly"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md#Data Flow"
---

# Review: slice — slice 303

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Statistics semantics and the family model follow the architecture

- Sessions come from the reference time, not the event time (D4).
- Revisions use update action and receive time, resolved at read time (D5).
- `statistics` is its own family with its own projector, table and ledger (D1–D3). It is never written into `tick_trade` or `minute_ohlcv`, as the architecture's "Provider bars are a tier of their own" principle requires.
- The architecture said the rules would be confirmed against the DBN record definition at slice design. This slice does that against `v0.70.0` and says what remains unconfirmed (the flag bit layout and the instant `ts_ref` carries). The purchase confirms the second, and the first is deferred explicitly.

### [PASS] Completeness ships with the tier, and the split with 305 is consistent with the slice plan

- The slice carries its own load-complete and content checks and the cross-tier volume check.
- `health`, `accounting` and `overview` go to 305. The slice plan's Notes assign the cross-tier layer and operator surfaces to 305 and say 303 carries its own check, so the architecture principle is met.
- D8 narrows the architecture's "agrees" to `agree / under / over / no_trades`. It states the reason (off-Globex cleared volume) and a measurement-based tightening rule. This is a documented refinement, not drift.

### [CONCERN] Tick and bar volume are mixed per session, but the API surface does not name the basis

- D10 chooses tick volume or bar volume per session, so a `v.0` schedule over 2010–2024 will use bars before 2024 and ticks after. Its roll dates can differ between bases.
- The new `basis_family` field appears only on `ActiveContract` and in `mt data tick active --json`.
- D13 says no route change, and the API Contracts section never mentions the field. The `/api/v1/futures/*` rule answers (schedule and the others) would therefore blend two tiers without saying so.
- This conflicts with the architecture's "never splice tiers silently" and "response names its source" principles. Those principles are written for bars, but this is the same hazard applied to roll answers.
- Fix: either carry `basis_family` (or per-segment basis) through the route payloads and `openapi.json`, or state explicitly that this is deferred to 306 and why that is acceptable.

### [CONCERN] The 305 interface for the volume check is under-specified

- D8 says the check lives "in `coverage` only" and reads resolved values from the raw statistics table. D7 says status reads only the manifest, ledger and definitions.
- The Integration Requirements then say "305 can read the family's verdicts and the volume check without new reads."
- Those statements pull in different directions. The first two imply 305 needs a new raw-table read for `health`. The third claims it does not.
- The design should name the callable that returns the volume verdicts, in `statistics_reads` or elsewhere. It should say whether it is a pure function over inputs or a connection-bound query, and what it costs per session. As written, 305 inherits the ambiguity.

### [CONCERN] Proportionality: some mechanisms exceed what this slice needs

This is an effort-4/5 slice with 13 decisions. The following items can be cut or deferred without losing anything the slice plan assigns:
- **A separate `OPEN_INTEREST_RANK_LAG_SESSIONS` constant (D9).** It starts at 2, the same as `VOLUME_RANK_LAG_SESSIONS`, and both rest on the same publication fact. Share one constant until the equivalence test shows they must differ, then split.
- **The `over`/`no_trades` verdict taxonomy for both families (D8).** Bars are not purchased in this slice, and the only bar day is derived from ticks. The bar-versus-cleared-volume check therefore cannot meet real data here. Build the check once over a family-parameterised read, and let the tick-tier run be the evidence. Do not specify the "tightened to equality if everything agrees" branch, which is conditional design for an outcome that may not occur.
- **`ActiveContract.basis_family` (D10).** If the API concern above is resolved by deferring to 306, this field has no consumer in this slice other than the CLI JSON.
- **Storing and ledgering decisions for non-referenced stat types.** Storing every record for the counts check is justified. Make sure the projector does not grow type-specific logic for them.

### [NOTE] Real spend in this slice departs from the architecture's wording

- The architecture says "Only the purchase test spends money before a subscription." D11 buys about $0.037 of `statistics` plus the October definitions.
- The reasoning is sound: the rule cannot be evaluated and `ts_ref` cannot be confirmed without data. The amount is far inside the $0.50 ceiling, and 304 adopts the jobs, so the invoice-reconciliation purpose of 304 is kept.
- It is a literal departure from the architecture text, so note it in the architecture or the slice plan to keep the two consistent.

### [NOTE] Failure modes for the purchase and ingest are handled by reuse, with one hard-fail policy to watch

- Spend-guard refusal is handled ("the reason is recorded and the window is not forced through"). Download and verify failures use the existing pass.
- Ingest failures are enumerated as named checks: `stat_reference`, `unit_day`, `resolution`, `overlap`, `counts`.
- Content gaps become answers (`Unresolved`), not exceptions. That matches the "no silent fallback" rule.
- Hard-failing a whole unit when a `ts_ref` date is not a calendar session is correct for explicit failure. It has only been checked against about four months of data. The first 2010–2023 ingest (304 and 307) may meet partial-session or holiday-reference dates. The evaluation note should record how many distinct reference dates were checked against the calendar.

### [NOTE] Dependency directions and the roll-path timeout are stated correctly

- Resolution stays pure, and `roll_inputs` does the reads. The `statistics` projector is registered through the existing registry. The new tables sit in the tick database and follow the existing role and backup mechanics (D12).
- The roll path's statement timeout is restated and measured in D11. No other NFR in the parent architecture applies to this slice.

### Run Digest

- Response length: 7754 chars
- Response is newline-free: no
- Tool calls made: 4
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 5
- Tokens — prompt / cached / cache write / completion / reasoning: 289050 / 185759 / 103283 / 7084 / not computed
- Duration: 66.0 s
- Cost: $0.50 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
