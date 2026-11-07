---
docType: review
layer: project
reviewType: slice
slice: first-real-purchase
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/304-slice.first-real-purchase.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261107
dateUpdated: 20261107
reviewedSha: 32fed71fe0059bf11ed2425bc2ba0c11754746b0
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 113984
cachedTokens: 8813
cacheWriteTokens: 105167
completionTokens: 5268
durationSeconds: 45.3
costUsd: 0.475008
costBasis: provider
runId: run-20261107-p4-20aa594d
squadronVersion: 0.24.23
findings:
  - id: F001
    severity: concern
    category: architectural-alignment
    summary: "\"Invoice\" reconciliation is redefined to a provider-reported figure"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md:224"
  - id: F002
    severity: concern
    category: under-specification
    summary: "Reconciliation compares against a ceiling read from the dev environment"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md:216-220"
  - id: F003
    severity: concern
    category: error-handling
    summary: "Cutover failure handling leaves timers held and the retry bound undefined"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md:184-192"
  - id: F004
    severity: concern
    category: proportionality
    summary: "Measurement and evidence scope is heavier than the slice needs"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md:240-248"
  - id: F005
    severity: note
    category: scope
    summary: "Purchase scope extends into the history-acquisition slice's territory"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md:36-39"
  - id: F006
    severity: pass
    category: architectural-alignment
    summary: "Layering, dependency direction and cost-guard alignment"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md#Architecture"
---

# Review: slice — slice 304

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] "Invoice" reconciliation is redefined to a provider-reported figure

The architecture's design goal is "A purchase path proven with real money — checked against the invoice". Its Envisioned State says "the invoice agreed with the manifest". The slice redefines "the invoice" as Databento's per-job reported charge from `batch.list_jobs`. That figure comes from the same provider channel as `actual_cost_usd`, which is also "set from the batch job's reported cost". The check can therefore confirm that `get_cost` matches the job cost. It cannot show that the amount billed matches the manifest, which is the architecture's stated risk ("every spend ceiling meaningless").

Deferring the month-end invoice is reasonable, since a task cannot wait for November's invoice. The slice should say so as a deviation from the architecture, not as a restatement of it. It should also assign the actual invoice comparison to a later point, such as 307's reconciliation after month-end, so the goal is not silently dropped. Otherwise the goal is unmet by any slice.

### [CONCERN] Reconciliation compares against a ceiling read from the dev environment

D5 says the script exits non-zero when "the total actual exceeds the per-pass ceiling read from the environment". The Architecture section says the script runs "from the dev checkout, whose `.env`" is used. D8 says the dev checkout's ceilings are the low dev values (the rehearsal verdict "will read `refused`"). Production's 25.00 lives in `/etc/manta-trading.env`, which the dev checkout does not read.

Success criterion 4 expects exit 0 with the total at most 25.00. As written, the script would either fail on a correct $24 purchase against the dev ceiling, or compare against a value unrelated to what the guard enforced. Pass the cap as an explicit argument, or read it from the production env file, and state which. Do not leave an implicit environment lookup.

### [CONCERN] Cutover failure handling leaves timers held and the retry bound undefined

The cutover holds production timers in step 2 and releases them in step 6. The stop paths in steps 4 and 5 are not tied to the release. These are the estimate stop, exit 1/2/4/5, and exhaustion of the exit-6 attempt count. The doc does not say whether the timers are released when the script stops there. A stop between hold and release leaves production's timers held, and "re-runnable" does not say who releases them.

Related gaps on the new I/O paths:
- The behavior when the exit-6 attempt count runs out is unspecified. The count is only "named once in the script".
- `batch.list_jobs` failing or timing out inside `reconcile_tick_spend.py` has no stated handling. A silent empty list would hide unheld provider jobs.
- A pass that dies mid-download for roughly 900 jobs is covered only by the idempotence claim in Mitigation Strategies. The cutover script itself has no stated behavior for it.

State each as an explicit outcome: release timers on every exit path, stop with a named reason when the attempts are exhausted, and fail the reconciliation when the job list cannot be read.

### [CONCERN] Measurement and evidence scope is heavier than the slice needs

The slice's evidence is the reconciliation plus the `status` and `coverage` verdicts. D7 adds a dedicated `measure_304_purchase.py` for four things:
- `tick_definition` growth, plus table sizes for `tick_statistics` and `tick_ohlcv_1m`.
- The close-minute trade analysis, which is a research question about the session rule.
- Pass and ingest durations.
- Request and unit counts.

The slice itself says "this slice does no other measuring" and that 305 owns manifest-scale measurement. The durations, counts and row counts are already available from `pass_runs`, `debug`, and `status`. Record them in the evidence note from those commands, and drop the new script for them. If the close-minute analysis is a 301 handover, make it a one-off query in the note, not a maintained script. The same applies to adopting the 16 jobs for $0.065, which needs a staging copy, a path-permission step and a rehearsal comparison. It is a 303 commitment, so keep it, but treat it as the first cut candidate if effort grows.

### [NOTE] Purchase scope extends into the history-acquisition slice's territory

The architecture describes the purchase test as "small" and leaves candidates to slice design. This slice buys the full 2010–2026 `statistics` and `definition` history for both products, plus ten years of ES bars. That covers part of what "History acquisition" would otherwise buy. It is PM-decided (cap $25, 2026-10-28), and the Provides section gives 307 a precise remainder, so this is consistent. The slice plan and the architecture's Anticipated Slices should reflect it, so 307's scope is not double-counted.

### [PASS] Layering, dependency direction and cost-guard alignment

The slice makes no schema change and no pipeline change beyond a universe constant. Only `mt` verbs write production state, and the scripts read it. This respects the architecture's file-first, manifest-first and marginal-spend principles, and no request is plan-included. The fixed end date keeps the purchase bounded and re-runs plan nothing. Dependencies on 302 and 303 point backward only, and the interfaces to 305, 306 and 307 match what those slices consume. The D1 fallback to 2018 ensures the $25 cap cannot be exceeded at the cutover. The PM-owned ceiling script respects the architecture's "The PM sets the ceilings" and the runbook's "No agent edits them".

### Run Digest

- Response length: 6520 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 113984 / 8813 / 105167 / 5268 / not computed
- Duration: 45.3 s
- Cost: $0.48 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
