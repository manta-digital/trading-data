---
docType: review
layer: project
reviewType: slice
slice: futures-completeness-surfaces
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/305-slice.futures-completeness-surfaces.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261108
dateUpdated: 20261108
reviewedSha: 1347c9e7df555de648b45c652051b4a6f7cd371a
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 8
effort: high
turns: 9
promptTokens: 717914
cachedTokens: 601811
cacheWriteTokens: 116087
completionTokens: 10314
durationSeconds: 97.8
costUsd: 0.628472
costBasis: provider
runId: run-20261108-slices-plan-d03f6d49
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Source edge and caught up are defined per futures tier, in place"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#D1 — `COVERAGE_SOURCE_TABLE` gets no futures entries; families get `FAMILY_SOURCE_EDGE`"
  - id: F002
    severity: pass
    category: scope
    summary: "Scope boundaries and dependency direction match the slice plan"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#Technical Scope"
  - id: F003
    severity: concern
    category: architectural-principle
    summary: "\"Defect versus work not yet done\" cannot be told apart after acquisition"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#D5 — Health: one row per product and family, counted only for defects"
  - id: F004
    severity: concern
    category: under-specification
    summary: "Snapshot transaction and step tracking change shared plumbing that the component list omits"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#D5 — Failure and concurrency; #Component Structure"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Cross-tier registry and `FAMILY_SOURCE_EDGE` are heavier than the slice needs"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#D4, #D1"
  - id: F006
    severity: concern
    category: integration
    summary: "D2 is described as a refactor but changes statistics `caught_up` behavior"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#Consumes from Other Slices; #D2"
  - id: F007
    severity: concern
    category: failure-modes
    summary: "The health timeout-to-FAIL rule combines with a heavy recompute on a timer path"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#D5 (futures completeness FAIL row); #D4 step 5; #D9"
  - id: F008
    severity: note
    category: nfr
    summary: "The API block's 0.25 s bound is the most exposed measurement"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#D8, #D9, #D10"
  - id: F009
    severity: note
    category: error-handling
    summary: "Load-test cleanup on failure"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#D9"
---

# Review: slice — slice 305

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Source edge and caught up are defined per futures tier, in place

The architecture asks that "tick and the new tiers need their own definition of source edge and caught up" (Surface coverage). It also says completeness never comes from an aggregate. D1 and D2 do both, and D1 records why it departs from the slice plan's literal "extends `COVERAGE_SOURCE_TABLE`". The reasoning is sound: the table maps market-database continuous aggregates, and the futures tiers have no aggregate. `definition` is correctly left out of the family set, because `family_wants` already treats it as a companion with no ledger.

### [PASS] Scope boundaries and dependency direction match the slice plan

Dependencies 302 and 303 match the plan entry. 305 does not touch serving (306) or purchases (304, 307), and it keeps `/api/v1/health` and `/api/v1/overview` in their 231 shape. The 1s comparison is deferred, with the registry entry inactive and a named follow-up issue. That is what the plan calls "wired but inactive". The manifest-scale measurement the architecture asks for ("one measurement precedes the history acquisition slice") is in scope, with fixed bounds.

### [CONCERN] "Defect versus work not yet done" cannot be told apart after acquisition

The Value section promises that "a defect is told apart from work not yet done". D5 delivers this only through severity by bucket: `failed` and `retry_exhausted` are counted, while `missing` and not caught up are INFO. A session whose unit was never ingested or was lost looks the same as a product not yet bought. After 307 finishes, health therefore stays green on a real gap, which conflicts with the architecture's "a gap is a named finding, not a surprise in a backtest".

The design handles this by adding a 307 exit criterion, repeated in D5 and in Provides to Other Slices. That criterion exists only in this document. It is not in the slice plan's 307 entry, and nothing enforces it later.

Name a mechanism that separates the two cases. One option: a session the manifest says is `ingested` or `verified` but the ledger lacks is a counted defect, and a session with no manifest unit is INFO. Alternatively, record the 307 exit criterion in the slice plan entry so it is not a hidden cross-slice obligation.

### [CONCERN] Snapshot transaction and step tracking change shared plumbing that the component list omits

D5 relies on "one `REPEATABLE READ READ ONLY` transaction on the composed connection" for snapshot consistency across products and families. It also relies on a step recorder so error text can say `GC statistics: traded_vs_cleared: …`.

In the code, `cli/tick_composed.py` sets only `default_transaction_read_only=on` and the statement timeout. `api_server/tick_access.py` explicitly resets isolation to `None` at line 101. So the snapshot guarantee is a new behavior in `read_line` and `read_tick`, and neither module appears in Component Structure. The same holds for the step recorder.

Without that change, the "wholly in or wholly out" claim and failure criterion 4 (the timeout test) have no implementing component. Add both modules and the step-recording mechanism to the component list. State where isolation is set and how `read_tick`'s existing reset is reconciled.

### [CONCERN] Cross-tier registry and `FAMILY_SOURCE_EDGE` are heavier than the slice needs

- **Registry.** `CrossTierCheck(name, run, inactive_reason)` with a registry tuple serves one active check and one placeholder. Nothing else consumes the abstraction yet, because the 1s comparison is explicitly deferred to another slice. A module constant naming the inactive check and its reason, plus the `traded_vs_cleared` function called from `family_reports`, meets criterion 7 (listed as not active, with reason) at lower cost. The registry can be introduced when the second active check arrives.
- **Edge map.** `FAMILY_SOURCE_EDGE: Mapping[SchemaFamily, int]` stores only a lag: 0, 0 and 1. The real rule, including the statistics publication-window computation of E, lives in `source_edge()`. The map does not encode the rule, so its main work is the key-equality pin test. The "source edge" column in D2 and the mapping description in D1 are easy to confuse.

Neither blocks progress. Fold the registry down, or justify it in the document, and describe the map as lags and nothing more.

### [CONCERN] D2 is described as a refactor but changes statistics `caught_up` behavior

Consumes says 302 and 303 are "consumed unchanged except for the `_judged` move (D2) and the `_families` extraction (D3), both refactors pinned by the existing status tests." D2 is more than a move. A statistics session whose publication window is still open is no longer judged and now shows as `pending`, where before it kept `caught_up` false. The pinned regression test covers only the tick-tier and bar judged sets.

Existing 303 statistics `caught_up` tests may legitimately change. Say so explicitly, and list the 303 expectations that change. Otherwise this reads as a pure refactor and the behavior change goes unreviewed.

### [CONCERN] The health timeout-to-FAIL rule combines with a heavy recompute on a timer path

D5 makes any statement failure or timeout a counted `FAIL`, which is defensible under the 168 D3 precedent. D4 runs `volume_check` over the whole wanted history on every health call. That means roughly 4,000 sessions per family against a 19.4M-row raw table, on the timer path.

The bound is ≤5 s against a 10 s timeout, which is only 2× headroom. Heavier cleared-volume data, a concurrent ingest, or a cold cache could turn health red from load rather than from a defect. That would erode the "health stays a signal someone can act on" goal.

Either restate the headroom as an explicit requirement with a margin, or say what an operator does when a counted FAIL is only a timeout. The cause-naming detail in the row helps, but D10 covers only a miss found during measurement, not drift after the release.

### [NOTE] The API block's 0.25 s bound is the most exposed measurement

The 231 bound is restated at the right value, and the architecture sets no numeric NFR for these paths. But the block runs `family_reports` with per-session Python judging on every request, now across three families and about 4,100 sessions each. 231 set the 0.25 s figure at roughly 64 sessions.

D10 excludes "stored derived state" as a fix, and D8 offers no fallback for a miss, such as dropping the per-family records for that request or caching. If the bound is missed, 307 depends on a filed issue. Decide now whether a short-lived in-memory cache would count as a D10 fix, so a miss does not stall 307.

### [NOTE] Load-test cleanup on failure

D9 says the test "drops its database at the end". The seed is built once per test session and holds about 19.4M rows on the shared test cluster. Specify that the drop runs in a fixture finalizer, so an aborted or failed run does not leave a database that large behind. The drop must still target only the database the process created, per the project's destructive-statement rule.

### Run Digest

- Response length: 8685 chars
- Response is newline-free: no
- Tool calls made: 8
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: high
- Turns: 9
- Tokens — prompt / cached / cache write / completion / reasoning: 717914 / 601811 / 116087 / 10314 / not computed
- Duration: 97.8 s
- Cost: $0.63 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
