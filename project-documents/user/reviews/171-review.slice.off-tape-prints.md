---
docType: review
layer: project
reviewType: slice
slice: off-tape-prints
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: FAIL
verdictSource: stated
sourceDocument: project-documents/user/slices/171-slice.off-tape-prints.md
aiModel: gpt-6.1-sol
aiProfile: openai-oauth
status: complete
dateCreated: 20261016
dateUpdated: 20261016
reviewedSha: 6650b5b6dfeabec81c1ed6d7ce5eed6ae536e5cb
turns: 1
promptTokens: 88576
cachedTokens: 84096
completionTokens: 759
reasoningTokens: 285
durationSeconds: 67.7
costUsd: 0.02496
costBasis: alias_pricing
squadronVersion: 0.23.0
findings:
  - id: F001
    severity: fail
    category: concurrency
    summary: "Sweep can overwrite masks computed from newer data"
    location: "project-documents/user/slices/171-slice.off-tape-prints.md:173-180"
  - id: F002
    severity: concern
    category: data-integrity
    summary: "Eligible reads silently treat unevaluated bars as clean"
    location: "project-documents/user/slices/171-slice.off-tape-prints.md:159"
  - id: F003
    severity: concern
    category: error-handling
    summary: "New database operations lack bounded failure handling"
    location: "project-documents/user/slices/171-slice.off-tape-prints.md:190-214"
---

# Review: slice — slice 171

**Verdict:** FAIL
**Model:** gpt-6.1-sol

## Findings

### [FAIL] Sweep can overwrite masks computed from newer data

The sweep reads bars and computes masks **before** acquiring the chunk’s rewrite lock. A permitted deep-backfill insert can commit between those steps, re-marking the whole day correctly; the sweep can then overwrite those masks using its older snapshot. The checksum starts afterward and covers raw columns, so it cannot detect this. Non-NULL masks also prevent a later sweep from repairing them.

This violates the whole-day insert/sweep agreement required by the architecture’s “Off-tape marks” section. Specify serialization or snapshot revalidation covering the complete symbol-days, including chunk-edge context, and test this interleaving.

### [CONCERN] Eligible reads silently treat unevaluated bars as clean

`COALESCE(off_tape,0)` collapses “not evaluated” into “evaluated and clean,” despite the architecture explicitly distinguishing those states. Eligible reads become available before the approximately 50-hour sweep finishes, so callers can receive untouched spikes labeled `eligible` without any indication that evaluation is incomplete.

Define an explicit incomplete-evaluation policy—for example, refuse eligible requests whose scope contains unevaluated rows—and test requests during a partially completed sweep. Do not silently substitute clean masks.

### [CONCERN] New database operations lack bounded failure handling

The failure table covers lock acquisition and process termination, but does not specify handling for hung database operations, connection loss during mask transfer/update, or uncertain commit acknowledgement. `lock_timeout` does not bound reads, checksums, updates or recompression; consequently, “bounded by one chunk rewrite” is not an enforced bound.

State the connection and execution budgets—or explicitly identify inherited policies—for sweep, marking and health queries. Define rollback, connection disposal, non-zero/error reporting and rerun behavior, including checking persisted progress after an ambiguous commit. Add corresponding failure tests.

### Run Digest

- Response length: 2395 chars
- Response is newline-free: no
- Tool calls made: not offered
- Tool calls failed: not computed
- Stop reason: not computed
- Output budget: backend default
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: not computed
- Effort: backend default
- Turns: 1
- Tokens — prompt / cached / cache write / completion / reasoning: 88576 / 84096 / not computed / 759 / 285
- Duration: 67.7 s
- Cost: $0.02 (alias_pricing)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 3
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 3
- Finding-shaped matches — surviving validation: 3
- Answering model: not reported by provider (aiModel is the requested id)
