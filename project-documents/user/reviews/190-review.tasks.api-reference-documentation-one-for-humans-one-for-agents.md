---
docType: review
layer: project
reviewType: tasks
slice: api-reference-documentation-one-for-humans-one-for-agents
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/190-tasks.api-reference-documentation-one-for-humans-one-for-agents.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260916
dateUpdated: 20260916
reviewedSha: 5129de51068ef5a2904a8eb7b228b66386150d47
findings:
  - id: F001
    severity: concern
    category: consistency
    summary: "Granularity and HealthStatus markers required only in agents.md, not reference.md"
    location: "project-documents/user/tasks/190-tasks.api-reference-documentation-one-for-humans-one-for-agents.md:267-270"
  - id: F002
    severity: concern
    category: correctness
    summary: "Section 3.5 and 3.7 titles miscount their path totals"
    location: "project-documents/user/tasks/190-tasks.api-reference-documentation-one-for-humans-one-for-agents.md:254-294"
  - id: F003
    severity: concern
    category: sequencing
    summary: "Tasks 5.4 and 5.5 have no commit checkpoint"
    location: "project-documents/user/tasks/190-tasks.api-reference-documentation-one-for-humans-one-for-agents.md:440-463"
  - id: F004
    severity: note
    category: process
    summary: "D3's code change reportedly landed directly on `main` ahead of the slice branch"
    location: "project-documents/user/tasks/190-tasks.api-reference-documentation-one-for-humans-one-for-agents.md:10-12"
  - id: F005
    severity: note
    category: completeness
    summary: "Stale \"trap\" comment in test_openapi_operations_schema.py not scheduled for update"
    location: "unverified"
  - id: F006
    severity: pass
    category: scope
    summary: "CI-gating / load-test criteria correctly judged inapplicable"
    location: "project-documents/user/slices/190-slice.api-reference-documentation-one-for-humans-one-for-agents.md:381"
  - id: F007
    severity: pass
    category: completeness
    summary: "SC-to-task traceability and sequencing are sound"
    location: "project-documents/user/tasks/190-tasks.api-reference-documentation-one-for-humans-one-for-agents.md:479-494"
---

# Review: tasks — slice 190

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] Granularity and HealthStatus markers required only in agents.md, not reference.md

SC5 requires all five enum vocabularies (`Granularity`, `HealthStatus`, `MarketStatus`, `PassKind`, `PassRunOutcome`) to carry a `<!-- from: -->` marker so the gate can catch drift. Task 3.6 explicitly requires the `MarketStatus` marker in `reference.md` (line 281-282), and task 3.7 explicitly requires `PassKind`/`PassRunOutcome` markers there (line 296-298). Task 3.5, which discusses `Granularity` (bars/gaps/status parameters) and `HealthStatus` (`health STALE` semantics) at length, never instructs adding a marker for either in `reference.md` — it only cross-references Conventions. Task 4.4 (agents.md, line 386-389) does cover all five markers, so SC5 is technically satisfiable via `agents.md` alone, but D2 states both documents describe one surface and facts must not fork. If `reference.md` lists Granularity or HealthStatus values inline without a marker, the gate cannot detect drift in that file specifically — only in `agents.md`. Task 3.5 should be made to mirror 3.6/3.7 and require the same two markers.

### [CONCERN] Section 3.5 and 3.7 titles miscount their path totals

Task 3.5's title claims "(7 paths)" but lists only 6 real paths (`/health`, `/bars/{symbol}`, `/symbols`, `/symbols/{symbol}`, `/status`, `/gaps/{symbol}`) plus a non-path vocabulary note. Task 3.7's title claims "(3 paths)" but lists 4 (`/candlesticks`, `/trades`, `/overview`, `/credits`). The errors cancel out in the running total (6+7+4=17 matches the SC1 target), but each section header is individually wrong, which risks an implementer treating the vocabulary note as a 7th "path" in 3.5, or missing that 3.7 actually covers 4 endpoint sections not 3, when self-verifying against the stated count.

### [CONCERN] Tasks 5.4 and 5.5 have no commit checkpoint

Every other multi-step unit in this file ends with an explicit `**Commit**:` line (e.g. 1.1-1.3 at line 97, 2.7 at line 194, 5.1 at line 407, 5.2 at line 418, 5.3 at line 438). Task 5.4 edits `user/architecture/180-slices.data-serving-api.md` (adds a Future Work entry, marks plan entry 10 materialized) but has no commit line, and 5.5 records the outcome of each walkthrough step with none either. Without an explicit checkpoint, 5.4's architecture-doc edit is likely to get folded silently into 5.6's final commit (`docs: complete slice 190 API reference documentation`), mixing an unrelated architecture-doc change into a commit message that doesn't mention it — inconsistent with the project's semantic-commit convention.

### [NOTE] D3's code change reportedly landed directly on `main` ahead of the slice branch

The task file's `projectState` states D3 (the only code change in this slice) "already landed on main in commit 3141815" before this task breakdown's Phase 6 work begins. CLAUDE.md's branch rules reserve direct-to-`main` commits for planning phases (0–5); a behavior-adjacent code change is Phase 6 work and should live on a slice branch. This is outside the task breakdown's ability to fix (it's already a fact on disk) and may have been a deliberate, PM-sanctioned isolated fix, but it's worth surfacing since it's a deviation from the documented git policy.

### [NOTE] Stale "trap" comment in test_openapi_operations_schema.py not scheduled for update

The slice design (D3) quotes `test_openapi_operations_schema.py:5-8` as naming "the trap 188 recorded … leaves a UI generator with no type to import" — a trap D3 has now fixed. No task updates that comment to reflect the fixed state, so the test suite will retain a description of a defect that no longer exists. Low impact (a comment, not a behavior check), but worth a one-line fix somewhere in section 1 or 5.

### [PASS] CI-gating / load-test criteria correctly judged inapplicable

The slice design's D8 failure-modes table explicitly states "the gate itself is bypassed" is a pre-existing exposure shared with the drift test and "not this slice's to close." No NFR is restated in this slice requiring a load test (SC7's byte-identity check is a one-time manual comparison, not an ongoing performance budget). The task breakdown correctly adds no load-test task and no CI-wiring task, matching the design's explicit scoping rather than silently leaving a gap.

### [PASS] SC-to-task traceability and sequencing are sound

Independently cross-checking SC1–SC12 against the task bodies confirms the file's own coverage table: every criterion has at least one concrete, testable task, the gate is built and unit-tested before the prose that depends on its markers (section 2 before 3/4, consistent with the design's Implementation Notes), and 2.7's whole-tree assertion is deliberately deferred to 5.1 rather than left unexplained. No circular dependencies found.
