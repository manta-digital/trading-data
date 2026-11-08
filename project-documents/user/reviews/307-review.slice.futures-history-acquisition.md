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
reviewedSha: 01ac6ec60315bf2077c711bc5a7e7eedc79fca83
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 4
effort: high
turns: 5
promptTokens: 302280
cachedTokens: 198396
cacheWriteTokens: 103876
completionTokens: 9117
durationSeconds: 90.1
costUsd: 0.527299
costBasis: provider
runId: run-20261108-slices-plan-d03f6d49
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: concern
    category: layer-boundaries
    summary: "Completeness classification is built inside a one-off cutover script"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md:227"
  - id: F002
    severity: concern
    category: scope-and-proportionality
    summary: "Scope list omits the D5 and D6 script work, and the 300-line limit has no plan"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md:66-83"
  - id: F003
    severity: concern
    category: integration
    summary: "The issue #69 gate can be skipped by an argument on the production command"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md:178"
  - id: F004
    severity: note
    category: architecture-alignment
    summary: "Invoice reconciliation for 307's own purchase is deferred to a follow-up issue"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md:236-239"
  - id: F005
    severity: pass
    category: architecture-alignment
    summary: "Purchase range, cost and fixed end match the architecture"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md:33-58"
  - id: F006
    severity: pass
    category: error-handling
    summary: "Failure modes are enumerated for the new I/O paths"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md:155-157, 213-216"
  - id: F007
    severity: pass
    category: dependencies
    summary: "Manifest-scale NFR is restated and dependencies point the right way"
    location: "project-documents/user/slices/307-slice.futures-history-acquisition.md:136-138, 284"
---

# Review: slice — slice 307

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Completeness classification is built inside a one-off cutover script

The architecture says completeness is answered by the existing surfaces (`status`, `coverage`, `health`, `accounting`) and that "no parallel set" of them is built (Surface coverage). D6 item 3 builds a second verdict in `cutover_304_evidence.py`. It lists the non-complete sessions, assigns each a class (`publication after want end`, `acquisition failed`, the D5 classes and provider holes), and makes the cutover's exit rule fail on an unclassified session. That class vocabulary exists only in the script. The 305 surfaces and the evidence note could therefore disagree about why a session is not complete. The slice does not say whether `coverage` already emits a reason and bucket per session that the script only reads, or whether the script derives the reasons itself.
Name the source of each class. Reuse the reason and bucket that `coverage` emits wherever it has them. Limit the script's own labels to the two that only a cutover can know: `publication after want end` and `acquisition failed`. State that the surfaces stay the single source of the other classes.

### [CONCERN] Scope list omits the D5 and D6 script work, and the 300-line limit has no plan

Technical Scope names only the gate, the edge check, the per-product calendar and the evidence note. Several larger pieces of behaviour are not listed as included work, although they appear in D5, D6 and Development Approach tasks 2–3:
- The cutover stays non-zero while a request is failed-retryable.
- The estimate judgement accepts a resubmit made at or after the cutover's start instant.
- Retry-exhausted requests are classified and reported.
- The not-caught-up list, the health and accounting rows, and the cross-tier counts are collected.

D3 says the three existing scripts are about 640 lines in total. Success Criteria (line 278) requires "each script stays within about 300 lines". The added work lands mostly in the evidence and checks files, and no split is planned. The requirement and the scope are inconsistent unless the new logic goes into a new shared module.
List the D5 and D6 behaviours under Included. Say which of them already exist in 304's scripts. Name the module that takes the new code, so the line limit holds without a split at review time.

### [CONCERN] The issue #69 gate can be skipped by an argument on the production command

The slice plan makes the #69 invoice comparison a prerequisite of 307's purchase, and the gate exists because a wrong cost record would make the guard count the wrong figure. D3 adds `--no-gate-issue` so 304's tests can run. The same flag on the production CLI skips the gate for the $89 purchase. D3 prevents a silent skip but not an explicit one. The flag exists only for tests.
Keep the test seam out of the operator interface. Have the tests call the preflight function with the gate disabled, or make `--no-gate-issue` fail whenever `--slice` is not 304. That removes one flag and one either-or rule from the CLI.

### [NOTE] Invoice reconciliation for 307's own purchase is deferred to a follow-up issue

The architecture says invoice reconciliation applies to every usage-billed request. 307's own exit criterion checks only the per-job charge, and the invoice comparison becomes a gated follow-up issue (D7). This is a reasonable response to a monthly invoice cycle. It is consistent with how the architecture handled 304 and issue #69, and the next purchase slice is gated on it. No action is needed.

### [PASS] Purchase range, cost and fixed end match the architecture

- The slice buys the remainder named in the architecture's Anticipated Slices ("history acquisition takes the remainder"). That is `ohlcv-1m`, `statistics` and `definition` for ES and GC from 2010-06-08.
- Documented corrections to the architecture's first design goal and purchase-test entry are made and explained.
- The measured costs are consistent with the architecture's cost table once the 2024 ES window is excluded.
- The request and unit counts (about 390 and 8,100) are consistent with 304's figures.
- The fixed end, the dataset-edge preflight and the no-open-end rationale avoid unchosen spend and a split reconciliation.
- Spend stays inside the PM-set ceilings, with the values derived from the free estimate (D4).

### [PASS] Failure modes are enumerated for the new I/O paths

Each new external read has an explicit, tested stop that never reads as success:
- `gh` failure.
- `status` failure or timeout.
- Absent, null or unparseable dataset edge.
- Failed-retryable requests (exit 2 and resubmit).
- Retry-exhausted requests (final, with the provider error recorded).
- Jobs still processing (exit 6, re-run resumes).
- Partial purchases (the baseline state files make re-runs reconcile the whole purchase).

### [PASS] Manifest-scale NFR is restated and dependencies point the right way

The architecture's manifest-scale concern is carried into the slice: about 24,600 units against 305's measured 30,000. A miss is handled as a 305 D10 issue, never by raising a bound. 307 consumes 304 and 305 and provides to 306. It changes no ingest, statistics or calendar rule, and it records data findings instead of patching them (D5).

### Run Digest

- Response length: 6338 chars
- Response is newline-free: no
- Tool calls made: 4
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: high
- Turns: 5
- Tokens — prompt / cached / cache write / completion / reasoning: 302280 / 198396 / 103876 / 9117 / not computed
- Duration: 90.1 s
- Cost: $0.53 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7

## Resolution

- **[concern] Completeness classification built inside the cutover script** — Changed. D6 item 3 now copies the `coverage` bucket and the manifest's `failure_reason` word for word, and names the surfaces and manifest as the single source. The script adds only two labels of its own (`publication after want end` and `acquisition failed`). The exit rule names exactly which buckets it cannot account for.
- **[concern] Scope omits D5/D6 script work; no plan for the 300-line limit** — Changed. Technical Scope now lists the failed-request and evidence behaviours, says which already exist in 304's scripts and which are new, and puts the new code in one new module, `scripts/purchase_verdicts.py`. Development Approach steps 2–3 name that module.
- **[concern] Issue #69 gate skippable from the production command** — Changed. Removed `--no-gate-issue`; `--gate-issue` is required and nothing on the command line skips the gate. 304's tests call the preflight function with no gate issue directly.
- **[note] Invoice reconciliation for 307 deferred to a follow-up issue** — No change; the review needs no action. D7 already files the follow-up issue at close, and the next purchase slice gates on it.
- **[pass] Purchase range, cost and fixed end match the architecture** — No change.
- **[pass] Failure modes enumerated for the new I/O paths** — No change.
- **[pass] Manifest-scale NFR restated and dependencies point the right way** — No change.
