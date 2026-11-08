---
docType: review
layer: project
reviewType: slice
slice: futures-history-acquisition
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/307-slice.futures-history-acquisition.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261108
dateUpdated: 20261108
reviewedSha: 6146bc0f65d8053d472105e90fb0eab8af4b4bc1
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 7
effort: high
turns: 8
promptTokens: 405593
cachedTokens: 296403
cacheWriteTokens: 109180
completionTokens: 9193
durationSeconds: 85.5
costUsd: 0.55908
costBasis: provider
runId: run-20261108-slices-plan-d03f6d49
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: concern
    category: antipattern
    summary: "Spend-safety checks are opt-in flags, and one repeats a value the universe already holds"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md:171-188"
  - id: F002
    severity: concern
    category: error-handling
    summary: "The exit rule does not say what happens when a request fails at acquisition"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md:226 and :264-267"
  - id: F003
    severity: concern
    category: documentation-consistency
    summary: "The design says it corrects the architecture, but the architecture is left stale"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md:35-38"
  - id: F004
    severity: note
    category: consistency
    summary: "The rationale for the 2026-12-02 edge contradicts D1's own treatment of trailing statistics"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md:154,157"
  - id: F005
    severity: note
    category: under-specification
    summary: "The evidence note's path is not derivable from `--slice 307`"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md:73,176"
  - id: F006
    severity: pass
    category: alignment
    summary: "Scope matches the slice plan entry and the architecture's layering"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md#Technical-Scope"
  - id: F007
    severity: pass
    category: nfr
    summary: "The manifest-scale NFR and the cost guard are restated with numbers"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md#State-Management"
---

# Review: slice — slice 307

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Spend-safety checks are opt-in flags, and one repeats a value the universe already holds

D3 makes `--gate-issue` and `--min-dataset-edge` optional: "Without it there is no gate" and "Without it there is no edge check". D1 and D2 say these checks are what stop a ~$89 purchase from running early or splitting the reconciliation. A re-run of `--slice 307 <tag>` that leaves the flags off will skip both checks without any message. That is a silent default for the slice's main safety property, and the Core Principles reject silent fallbacks.

`2026-12-02T00:00Z` is also the universe's `_HISTORY_END` (2026-12-01) plus one day. The doc writes it as a command-line argument and defines it again in the universe (D1), so changing the end means editing two places. The project rule is to define a comparison value once.

Fix both:
- Derive the minimum edge from the latest want end in `TICK_UNIVERSE`, and drop `--min-dataset-edge`.
- Make the gate explicit. Either require `--gate-issue <n>` or an explicit `--no-gate-issue` (304's tests would pass the latter), so omitting it is an error and not "no gate".

This also shortens the walkthrough commands and removes the "new arguments" surface.

### [CONCERN] The exit rule does not say what happens when a request fails at acquisition

Functional requirement 4 accepts "every request is delivered or ends with a named failure". D6's exit rule requires "a second estimate is `nothing_to_buy`", and success criterion 7 requires $0. A request that fails at the provider and is never downloaded is not covered, so the planner would plan it again. The second estimate would then be non-zero and the cutover could never exit 0.

D5 covers units that fail at ingest and provider `missing` days. It does not say how an acquisition-failed request is classified or excluded from the `nothing_to_buy` check. This is the one failure path on a purchase 14 times the size of 304's, and 304 D3's cases are only referenced, not restated.

State one of two outcomes:
- A failed request is recorded and excluded from the second-estimate check, with the reason named in the evidence note.
- It is a stop that requires a re-run, and the cutover stays non-zero until the request delivers.

Either choice also settles whether a re-run could buy the same days twice.

### [CONCERN] The design says it corrects the architecture, but the architecture is left stale

The Overview says "Two corrections to the slice plan entry and the architecture are made with this design", and the first correction is that 304 did not buy ES bars from 2017.
- The slice plan entry (item 8) and the "No PM host steps" note already carry the corrections.
- The architecture's Anticipated Slices still says "ES `ohlcv-1m` from 2017; history acquisition takes the remainder" (`300-arch…md:99`).
- The task breakdown (Development Approach, steps 1–4) lists only runbook 100 and the evidence note as documents to edit. It does not list the architecture correction.

Without a task, the architecture will keep stating what the design calls a misreading. Add the one-line architecture edit to step 4, or drop the claim that the architecture is corrected.

### [NOTE] The rationale for the 2026-12-02 edge contradicts D1's own treatment of trailing statistics

D1 sets the minimum edge to the end plus one UTC day "so the statistics published for the 2026-11-30 session are available too". The same decision says the last statistics sessions stay not complete, because their records fall in a UTC day past the want. Those records are in the 2026-12-01 unit, which is outside the want and is never bought. A wanted range ending 2026-12-01 only needs the edge at or after 2026-12-01T00:00Z. The extra day is harmless, since the purchase already waits for December, but the stated reason is wrong. If the edge becomes derived from the universe (first finding), state the rule as "the want end", or give a correct reason for the extra day.

### [NOTE] The evidence note's path is not derivable from `--slice 307`

D3 says `--slice` supplies the prefix of the state files and the narration log. The evidence note is named `307-history-acquisition-evidence.md`, which has a descriptive suffix that a bare `307` cannot produce. The task file should say whether the script globs on the prefix, takes the name from a separate argument, or writes a differently named narration log that is not the evidence note. This is small, but it is exactly what the "re-run reconciles the whole purchase" claim depends on.

### [PASS] Scope matches the slice plan entry and the architecture's layering

- The remainder follows the slice plan's arithmetic: ES bars outside 2024, all GC bars, and every want out to the fixed end of 2026-12-01.
- The request counts reconcile: about 185 ES and about 198 GC month runs.
- The only pipeline change is a universe edit, which matches the architecture's file-first, schema-family and one-registry model.
- Completeness comes from the manifest and ledger through 305's surfaces. No aggregate is used and no new operator verb is added.
- Rule defects found in real data are recorded and designed separately (D5), which respects slice boundaries.
- The dependencies on 304 and 305, issue #69 and 301–303 match the slice plan.
- The exit criterion is the plan's wording and is operationalised in D6.
- The rejected options (a copied script, a registry, a new verb) keep the design close to the "resist complexity" principle.

### [PASS] The manifest-scale NFR and the cost guard are restated with numbers

- The 30,000-unit bound that 305 measured is restated: about 8,100 new units plus 304's about 16,500 gives about 24,600.
- A 305 timeout is routed to a 305 D10 miss, to be fixed by a query or index change and never by raising a bound.
- Spend is guarded twice, by the guard and by the cutover's estimate judgement. The ceiling values are derived from the estimate and rounded up to a named step.
- The ceilings stay with the PM under the named runbook 100 rule.
- Failure handling for the new `gh` and dataset-edge reads is explicit: a failure, an absent edge and an unparseable edge each stop the cutover and are never read as "closed" or "reached".

### Run Digest

- Response length: 7254 chars
- Response is newline-free: no
- Tool calls made: 7
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: high
- Turns: 8
- Tokens — prompt / cached / cache write / completion / reasoning: 405593 / 296403 / 109180 / 9193 / not computed
- Duration: 85.5 s
- Cost: $0.56 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
