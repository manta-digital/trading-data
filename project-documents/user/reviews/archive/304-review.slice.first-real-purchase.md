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
reviewedSha: 92facc695a2c97e2550d7ca7aa3d8e83ff16d28b
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 114563
cachedTokens: 11162
cacheWriteTokens: 103397
completionTokens: 4610
durationSeconds: 42.6
costUsd: 0.461583
costBasis: provider
runId: run-20261107-p4-20aa594d
squadronVersion: 0.24.23
findings:
  - id: F001
    severity: pass
    category: alignment
    summary: "Purchase scope matches the architecture's sized purchase test"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md#D1 — What is bought, as one universe edit"
  - id: F002
    severity: concern
    category: scope-alignment
    summary: "Invoice reconciliation, the architecture's stated goal, is deferred"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md#D5 — Reconciliation: estimate, manifest, Databento's charge"
  - id: F003
    severity: concern
    category: proportionality
    summary: "Overweight for a \"2/5\" slice. Adoption, the D7 checks and the extra tooling can be cut or deferred"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md#D2 — Adopt 303's jobs, do not re-buy"
  - id: F004
    severity: concern
    category: error-handling
    summary: "The staging copy of the 303 jobs has no owner and no failure handling"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md#Data Flow"
  - id: F005
    severity: concern
    category: error-handling
    summary: "Failure modes at 900-request scale are only partly enumerated"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md#D3 — The purchase runs in production, through `sudo mt-run tick`, after release"
  - id: F006
    severity: note
    category: layer-boundaries
    summary: "The reconciliation script runs from the dev checkout against the production manifest"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md#Component Structure"
  - id: F007
    severity: pass
    category: integration
    summary: "Dependency directions, interfaces and spend guards are consistent"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md#Integration Points"
---

# Review: slice — slice 304

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Purchase scope matches the architecture's sized purchase test

D1 buys `statistics` and `definition` for ES and GC over 2010-06-06 to 2026-11-01, plus ES `ohlcv-1m` from 2017. This is the split the architecture's "Purchase test" slice entry records (arch line 99). The slice uses the archive generalisation and plan-inclusion work of 300–303 unchanged, as the architecture sequences them. It also respects the architecture's "no subscription, usage-billed" premise. Data is kept, and the fixed end dates keep the purchase bounded and re-runs idempotent. The $25 cap and the 30-day ceiling of $35 are stated with the measured $24.23 list price, so the guard's NFR is restated with specific numbers.

### [CONCERN] Invoice reconciliation, the architecture's stated goal, is deferred

The architecture's goal and Envisioned State say the purchase is "checked against the invoice" and that "the invoice agreed with the manifest". D5 declines this and compares the estimate with Databento's per-job reported charge. The slice admits that charge comes from the same provider channel as `actual_cost_usd`. The only reason given is that the invoice is issued after month end, which is a fair one, and the slice discloses the deviation. The comparison is deferred to issue #69, "before 307 relies on the ceilings", but nothing makes that a gate:
- It is not in 307's dependencies.
- It is not in the Provides section.
- The architecture is not amended.

Fix:
- Record the deferral in the architecture's purchase-test entry.
- Add #69 as an explicit prerequisite of 307.

### [CONCERN] Overweight for a "2/5" slice. Adoption, the D7 checks and the extra tooling can be cut or deferred

The architecture asks for one thing: a small real purchase that proves the pass, reconciled. The design adds mechanisms beyond that:
- **Adoption of 303's 16 jobs.** This saves $0.065. It brings a staging copy to `/data/market-data`, a cutover step, a rehearsal comparison against 303's unit counts (295, 211/212, 83/83) and first-time `adopt` coverage of the statistics and definition families. D2 itself calls it "the first thing to cut". Letting the pass re-buy the window removes all of that for six cents. If adoption stays because 303 committed to it, it should not be able to block the purchase.
- **D7's close-minute-bars SQL check.** This verifies a 301 calendar rule against `tick_trade`. It is not part of a purchase test and the architecture does not ask for it. Defer it to 305 or 306, or to a standalone fix.
- **`env_set_keys.sh` as a new `deploy/lib` helper.** It has a single caller. Inline it in `set-tick-spend-ceilings.sh`.
- **Duplicated narration.** The same findings appear in the evidence note, the walkthrough and the success criteria. Keep one place.

### [CONCERN] The staging copy of the 303 jobs has no owner and no failure handling

Data Flow step 1 copies the 16 job directories from `~/scratch-303/archive` (under `/home/manta`, unreadable by the service user) to `/data/market-data/databento/303-statistics-window/`. No component in D3's cutover steps 1–6 performs this copy. The preflight only checks that the target already holds 16 directories with `manifest.json`. The copy is therefore a hidden manual step that falls to someone or something unnamed. This is a hidden dependency. It also cuts against the project rule that slice designs contain no steps waiting on a human.

The design does not say:
- who runs the copy,
- with what privileges,
- what happens on a partial copy, or
- whether the copy is verified before adoption.

Assign it to the cutover script, as a check-then-act step with a hash or size check.

### [CONCERN] Failure modes at 900-request scale are only partly enumerated

D3 handles exit codes 1–6, a mid-download death, resume, and an unreadable job list (D5). The slice itself rates the risk "about 50 times any earlier pass". Several I/O paths at that scale have no stated handling:
- **Wait budget.** The slice does not give the pass's wait budget. It cannot show that `PASS_ATTEMPTS = 5` times that budget covers about 900 jobs.
- **Provider throttling.** Nothing says what happens if Databento rate-limits or rejects batch submissions part-way through. It is also unclear whether a partial submission is covered by "nothing is submitted twice".
- **Service unit time limit.** The slice does not say whether the systemd unit has a runtime limit that would kill a long pass.
- **Provider-job gap in the guard.** The guard counts provider jobs that the manifest lacks. The slice does not say what happens if the pass dies after submission but before the manifest row is written, which is a window the reconciliation would catch only after the money is spent.

State the budget and limit values, and the strategy for each case, in the slice.

### [NOTE] The reconciliation script runs from the dev checkout against the production manifest

The design relies on the dev `.env` reaching the same tick database and Databento account (runbook 100). D3 rejects the dev checkout as a purchase path because 234 retired it. Here it is used for read-only evidence, and `--cap-usd` is correctly never taken from the dev environment. This is acceptable as read-only, but 307 will reuse the script, so it should record that it must never write. The alternative is to run it through `mt-run` once it is stable.

### [PASS] Dependency directions, interfaces and spend guards are consistent

The slice depends on 302 and 303 and provides to 305, 306 and 307, in the direction the architecture's slice order implies. It changes no schema, and the migration plan preserves the tick tier's plan, proven by D8 step 3 (the base universe plans nothing). Ceilings stay under PM control: the env script is not added to `sudoers.d`, and the cutover also stops before buying if the estimate exceeds the per-pass ceiling. Both layers of the architecture's "cost is a first-class input" principle are therefore honoured. D6 correctly keeps ingest and calendar rule changes out of scope and routes real-data findings to separate designs.

### Run Digest

- Response length: 7035 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 114563 / 11162 / 103397 / 4610 / not computed
- Duration: 42.6 s
- Cost: $0.46 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
