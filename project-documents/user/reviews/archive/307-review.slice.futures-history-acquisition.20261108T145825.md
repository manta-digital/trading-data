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
reviewedSha: 9f829ac7a6058810dd6ce5a0460125336665b2f0
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 5
effort: high
turns: 6
promptTokens: 224341
cachedTokens: 109916
cacheWriteTokens: 114419
completionTokens: 10630
durationSeconds: 94.8
costUsd: 0.575749
costBasis: provider
runId: run-20261108-slices-plan-d03f6d49
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: concern
    category: proportionality
    summary: "Cutover generalisation is heavier than the slice requires"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md#D3 — One purchase cutover, generalised from 304's"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Failure handling for the new dataset-edge read is implicit"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md#D1 — What is bought, as one universe edit with a fixed end"
  - id: F003
    severity: note
    category: integration
    summary: "Start date 2010-06-08 departs from the architecture goal, and only half the plan correction is recorded"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md#Overview"
  - id: F004
    severity: note
    category: integration
    summary: "Slice plan note \"No PM host steps\" is contradicted by D4"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md#D4 — Ceilings: the PM's one command, values from the estimate"
  - id: F005
    severity: pass
    category: architecture
    summary: "Architectural alignment, boundaries and dependencies"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md#Architecture"
  - id: F006
    severity: pass
    category: nfr
    summary: "Manifest-scale NFR restated with figures that reconcile"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md#State Management"
---

# Review: slice — slice 307

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Cutover generalisation is heavier than the slice requires

The slice plan entry assigns 307 the purchase and verification of the remaining history through the existing pass. D3 adds a refactor on top of that:
- Three scripts are renamed and re-homed (`cutover_purchase.py`, `purchase_checks.py`, `purchase_evidence.py`).
- A new `PurchaseSpec` dataclass and a `purchase_specs.py` registry are added.
- 304's tests are moved and re-run against a 304 spec entry.
- Runbook 100 and 304's walkthrough are edited.

Several parts of this serve no requirement the slice has:
- **The 304 spec entry** exists only to keep "its purchase reproducible from the generic script". 304's purchase has already run and is closed, so nothing consumes the reproducibility. The entry also forces the "moved tests pass unchanged in outcome" requirement, and with it the test migration.
- **`gate_issues` is a tuple**, though 307 uses exactly one issue (#69). The only reason for a tuple is a hypothetical later purchase. The same applies to the "Later purchases" bullet under Provides.
- **The file renames** touch every consumer (tests, runbook, 304 docs) for naming alone.

What 307 actually needs is small:
- per-product calendar and wanted keys derived from the universe, which D3 already lists as derived values;
- the gate-issue check;
- the dataset-edge check;
- the ceiling estimate mode.

A lighter option would parameterise 304's scripts in place with those inputs (slice id, gate issue, minimum edge), keep the file names, and drop the 304 entry and its test migration. Extract a registry only when a third purchase slice exists.

A smaller point in the same section: the Overview and Value sections restate the cost table and 304's scope at length. 304's evidence note and the slice plan entry already hold both.

### [CONCERN] Failure handling for the new dataset-edge read is implicit

D1 makes the preflight stop unless the dataset edge from `mt data tick status` is at or after 2026-12-02T00:00Z. It does not say what happens when:
- `status` fails or times out;
- `status` returns no edge, for example null or absent for a product;
- the edge is present but unparseable.

D2 and Technology Choices are explicit that a `gh` failure stops the cutover and is "never read as closed". The edge check guards the same kind of irreversible spend: buying a partial November would split the reconciliation. It deserves the same stated rule, that a missing or unreadable edge stops the cutover. The check applies to both products, so say whether the stop names each product's edge or only the earlier one. A unit test in the Technical Requirements list should cover it. Today the list covers only an edge before the minimum.

The `repeat while exit 6` loop is bounded only by reference ("304 D3 applies unchanged"). 304 does state `PASS_ATTEMPTS = 5`, so no new gap exists. Name the inherited bound in this document so the Phase 6 task can't drop it during the rename.

### [NOTE] Start date 2010-06-08 departs from the architecture goal, and only half the plan correction is recorded

The architecture goal is bars "from the start of the dataset (2010-06-06)", and the slice plan entry says "from 2010-06-06". The design, following 304 D1, uses 2010-06-08, the calendars' populated range. The Overview says the plan entry "is corrected" but names only the ES-bars-from-2017 error.

Record the 06-06 to 06-08 difference as a deliberate departure and correct the start date in the plan entry and the architecture. Otherwise a reader of the architecture will take the first two days as missing.

Also, the cost table's fourth row says "all four schemas" for 2026-11-01 to 2026-11-07. The purchase covers three schemas (`ohlcv-1m`, `statistics`, `definition`), and `tbbo` is excluded. Fix the wording.

### [NOTE] Slice plan note "No PM host steps" is contradicted by D4

The slice plan Notes say no slice needs a root step or a PM-run command. D4 requires the PM to run `deploy/set-tick-spend-ceilings.sh`. Runbook 100 reserves the ceilings to the PM, so the step is legitimate, and 304 did the same. The plan note is stale, so update it. The design handles the sequencing well: the agent runs the free estimate first and prints the exact command, and no task waits on a date.

### [PASS] Architectural alignment, boundaries and dependencies

- **Scope matches the plan.** The slice changes the universe wants and the cutover tooling only, with no schema, table or migration change. The "scripts read production state; only `mt` verbs write it" rule keeps the layering intact. Serving, tick bars and `ohlcv-1s` stay excluded, as the slice plan and architecture require.
- **Dependencies are correct.** `[304, 305]` plus issue #69 matches the plan, and the dependencies point at completed or earlier slices.
- **The exit criterion is the plan's.** It correctly rejects "zero not-caught-up sessions" as unpassable, since provider gaps and the trailing statistics sessions can never close. It requires each remaining session to be named with a classified reason.
- **Cost handling follows the architecture.** Cost is estimated first, the spend guard is checked, and the fixed end is chosen deliberately rather than left open. A follow-up issue is filed for the December invoice, because the invoice cannot exist inside the slice.
- **The architecture's invoice-reconciliation principle is carried forward.** The next purchase slice gates on that follow-up issue.

### [PASS] Manifest-scale NFR restated with figures that reconcile

The architecture's one pre-acquisition measurement (manifest scale) is restated with a target. 304's roughly 16,500 units plus 307's roughly 8,100 stay under the 30,000 units 305 measured against. The figures are consistent across the document:
- about 390 requests, from about 185 ES and 198 GC month runs plus the November statistics and definition requests;
- about 24 M rows, against the measured 24.1 M records;
- about $89, from $88.09 measured plus about $0.60 for the rest of November.

Integration Points routes a 305 bound miss to a 305 D10 issue and rules out raising the bound, which keeps the NFR owned by the slice that defined it.

### Run Digest

- Response length: 7261 chars
- Response is newline-free: no
- Tool calls made: 5
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: high
- Turns: 6
- Tokens — prompt / cached / cache write / completion / reasoning: 224341 / 109916 / 114419 / 10630 / not computed
- Duration: 94.8 s
- Cost: $0.58 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
