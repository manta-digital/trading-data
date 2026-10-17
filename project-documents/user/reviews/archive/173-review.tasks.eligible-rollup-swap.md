---
docType: review
layer: project
reviewType: tasks
slice: eligible-rollup-swap
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/173-tasks.eligible-rollup-swap.md
aiModel: xiaomi/mimo-v2.6-pro
aiProfile: openrouter
status: complete
dateCreated: 20261017
dateUpdated: 20261017
reviewedSha: b5a4c7b4a3a127d7e81f0852908019d890f4809e
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 25
turns: 13
promptTokens: 331892
cachedTokens: 288512
completionTokens: 16916
reasoningTokens: 14333
durationSeconds: 410.5
costUsd: 0.034741
costBasis: alias_pricing
runId: run-20261017-p5-05794663
squadronVersion: 0.23.0
findings:
  - id: F001
    severity: concern
    category: ci-cd
    summary: "Load-tier CI gating left implicit — no wiring task and no reference to slice 907"
    location: "project-documents/user/tasks/173-tasks.eligible-rollup-swap.md:277"
  - id: F002
    severity: concern
    category: traceability
    summary: "Integration Requirements are neither tested nor inside 8.3's traceability scope"
    location: "project-documents/user/tasks/173-tasks.eligible-rollup-swap.md:345"
  - id: F003
    severity: note
    category: test-coverage
    summary: "Success criterion 1's 1h half has no direct assertion"
    location: "project-documents/user/tasks/173-tasks.eligible-rollup-swap.md:184"
  - id: F004
    severity: note
    category: traceability
    summary: "Cutover post-swap checks omit criterion 3's residual-object assertion"
    location: "project-documents/user/tasks/173-tasks.eligible-rollup-swap.md:257"
---

# Review: tasks — slice 173

**Verdict:** CONCERNS
**Model:** xiaomi/mimo-v2.6-pro

## Findings

### [CONCERN] Load-tier CI gating left implicit — no wiring task and no reference to slice 907

Task 6.1 adds a new load case to `test/load/test_187_api_nfr.py` and 8.2 (line 334) runs the tier manually, but neither the task file nor the slice design says where the tier is gated. Verified in the repo: `.github/workflows/ci.yml` runs no test job at all (publish-on-tag only), and the load files gate only on `MT_RUN_LOAD_TESTS=1` (`test/load/test_187_api_nfr.py:37`). The project's established pattern for load-test work — recorded in `project-documents/user/tasks/167-tasks.cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr.md:406-410` and `project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-1.md:285-295` — is to state plainly that CI gating is slice 907's scope ("Reference 907 so the gap is tracked, not forgotten"; "do not claim CI gating that does not exist"). 173's file never mentions 907. The fix is one line in the conventions or under 6.1 — the load case runs under the documented manual gate recorded by 8.2, and CI gating is slice 907's — not a new CI task.

### [CONCERN] Integration Requirements are neither tested nor inside 8.3's traceability scope

The slice design's Success Criteria include an Integration Requirements subsection (`project-documents/user/slices/173-slice.eligible-rollup-swap.md:205`): "`mt data accounting`, `mt data status`, `/api/v1/status` and the coverage index read the same counts before and after the swap", plus the mt-ui chart behavior. Task 8.3 walks only "Success Criteria 1-5 and Technical Requirements", so these two requirements drop out of the traceability net entirely, and no task compares the count surfaces before and after 064 — 2.4's reader-gap test asserts only that no missing-name error occurs, and 5.2's report records row counts but no before/after equality on those commands. Slice 172's task 3.1 item 2 (172-tasks, "Capture each reader's output before 063, then again after … assert equality") is the in-repo precedent for the missing assertion. Either add that before/after comparison to 2.4 (extending the populated case), or widen 8.3's scope to the Integration Requirements and name how each is verified in Phase 7 — the mt-ui half is legitimately Phase 7 only, but it should be named somewhere.

### [NOTE] Success criterion 1's 1h half has no direct assertion

Criterion 1 (`173-slice.eligible-rollup-swap.md:186`) says the 171 values are absent from the 5m **and 1h** bars; task 3.4 asserts only the 5m response (and the all-marked-bucket case), and the design's Verification Walkthrough curls 5m only. Coverage is transitive — 172's task 2.3 asserted the prints absent from the `_next` 5m and 1h rows against the same fixtures, and 2.4.2 here asserts the current names return the former `_next` rows — so the residual risk is low. Adding one 1h assertion beside the 5m case in 3.4, or naming the transitive mapping in 8.3's notes, closes the gap cheaply.

### [NOTE] Cutover post-swap checks omit criterion 3's residual-object assertion

Success criterion 3 requires that after the cutover "no `_next` or `_prev` object remains", but 5.2's post-swap checks list only `caggs verify`, coverage freshness and the value-parity check. The substance is covered (the drops are 064's, asserted in 2.4.1/2.4.2, and the swap-state detection enumerates existing objects), so this is cosmetic: one post-swap line asserting no `_next`/`_prev` name exists would make the script's exit 0 mean criterion 3 literally holds in production.

### Run Digest

- Response length: 5079 chars
- Response is newline-free: no
- Tool calls made: 25
- Tool calls failed: 1
- Stop reason: stop
- Output budget: 131072 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 54494
- Effort: backend default
- Turns: 13
- Tokens — prompt / cached / cache write / completion / reasoning: 331892 / 288512 / not computed / 16916 / 14333
- Duration: 410.5 s
- Cost: $0.03 (alias_pricing)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 4
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 4
- Finding-shaped matches — surviving validation: 4
