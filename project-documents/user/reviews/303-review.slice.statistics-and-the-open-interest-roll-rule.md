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
reviewedSha: 8679f2fc1dcc431d933044b40f52240b12524d05
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 106859
cachedTokens: 11162
cacheWriteTokens: 95693
completionTokens: 4672
durationSeconds: 44.3
costUsd: 0.431385
costBasis: provider
runId: run-20261103-p4-7756c473
squadronVersion: 0.24.16
findings:
  - id: F001
    severity: pass
    category: alignment
    summary: "Statistics semantics and layering match the architecture"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md#D4 — Session assignment from `ts_ref`"
  - id: F002
    severity: concern
    category: architecture-consistency
    summary: "The architecture's \"agrees\" cross-tier check is overridden but not amended"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md#D8 — Coverage and the cross-tier volume check"
  - id: F003
    severity: note
    category: scope
    summary: "The completeness-surface principle conflicts with the Anticipated Slices list, and the slice follows the list"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md#Technical Scope"
  - id: F004
    severity: note
    category: performance
    summary: "The resolved-read cost at full scale is deferred, and its failure mode is explicit"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md#D5 — Revisions: one resolved value per contract, statistic and session"
  - id: F005
    severity: note
    category: scope
    summary: "The `basis` field on API segments is a small expansion beyond roll-rule scope"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md#D10 — The volume rule's basis per session"
  - id: F006
    severity: pass
    category: error-handling
    summary: "Spend exception, failure modes and proportionality are handled"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md#D11 — The real purchase and the evaluation"
---

# Review: slice — slice 303

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Statistics semantics and layering match the architecture

The architecture's Statistics section requires session assignment from reference time rather than event time. It also requires record-level revision by update action and receive time, and a roll rule that reads only resolved values. D4, D5 and D9 implement all three. The slice keeps statistics in their own table with contract identity, as the "Provider bars are a tier of their own" principle requires. It reuses 300's family, projector and `FAMILY_LEDGER` abstractions instead of building parallel ones. Status reads only the manifest and ledger, as 220 requires. Dependencies run 300, 301, 302 → 303 → 304–307, and nothing flows upward.

### [CONCERN] The architecture's "agrees" cross-tier check is overridden but not amended

The architecture's "Completeness of sparse bars" says a session's `ohlcv-1m` volume "agrees with `statistics` cleared volume". D8 replaces that with a one-sided test: `under` (V < C) is normal and only `over` or `no_trades` is a finding. The slice says the architecture's "agrees" is refined to this, but the architecture text is unchanged. By contrast, the spend exception was written into the architecture's Anticipated Slices intro. 305 and later slice authors will read the architecture and could implement an equality check. Amend the architecture's Completeness paragraph to the V ≤ C rule when this slice is accepted.

### [NOTE] The completeness-surface principle conflicts with the Anticipated Slices list, and the slice follows the list

The principle "A tier ships with its completeness check" says the adding slice extends `status`, `coverage`, `health` and `accounting`. The Anticipated Slices list gives `health`, `accounting` and `overview` to a separate "Futures completeness surfaces" slice. The slice delivers `status`, `coverage` and the reusable `statistics_reads.volume_check`, and defers the rest to 305. That is a reasonable reading, and the integration requirement that 305 writes no new query keeps the tier verifiable. The architecture itself carries the inconsistency, so it is worth reconciling in a later amendment.

### [NOTE] The resolved-read cost at full scale is deferred, and its failure mode is explicit

The architecture states no latency NFR for this path. The slice states a target by reference: `TICK_ROLL_STATEMENT_TIMEOUT_SECONDS`. It times the read only over the four-month window. At 19.4 M rows, 307 measures the full-size read. The failure mode is stated: the request fails with a named database error and never returns a partial or empty schedule. This is acceptable. 307's plan entry should carry the measurement as an explicit task, because `n.0` is shipped before the cost is known.

### [NOTE] The `basis` field on API segments is a small expansion beyond roll-rule scope

D10 and D13 add `basis` to the shared `SegmentRecord`, which the schedule, ticks and bars responses all use. This is justified by "never splice tiers silently" and is additive. Slice 306 reworks the bars route and its source-tier naming, so it should treat `basis` here as roll provenance and keep the bar-source field separate. This is not a finding against 303, but 306's design should say so.

### [PASS] Spend exception, failure modes and proportionality are handled

The $0.051 purchase is bounded, runs through estimate and guard, and uses throwaway databases. A guard refusal is recorded and not forced through. The architecture's intro already names the exception. Ingest failure modes are enumerated (`unit_day`, `resolution`, `overlap`, `stat_reference` with named reasons, undefined values stored as NULL). Roll-time failure modes are separated into provider facts (an `Unresolved` answer) and broken invariants (`RollInputError`). Silent zeros are explicitly rejected.

The design is large (13 decisions, about 8 new modules, effort 4/5). Most of it traces to the architecture's slice entry: the schema, completeness, the cross-tier check, and the rule evaluated on ES and GC. I found nothing I can name as safely cuttable without dropping assigned scope. The stored-resolved-table and bars-first alternatives are already rejected, and the production universe and defaults are left untouched.

### Run Digest

- Response length: 5503 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 106859 / 11162 / 95693 / 4672 / not computed
- Duration: 44.3 s
- Cost: $0.43 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
