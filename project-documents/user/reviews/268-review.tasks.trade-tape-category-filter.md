---
docType: review
layer: project
reviewType: tasks
slice: trade-tape-category-filter
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/268-tasks.trade-tape-category-filter.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260903
dateUpdated: 20260903
reviewedSha: 9d2552dedf8c1958a23f6db1a5af435742d4e5e5
findings:
  - id: F001
    severity: concern
    category: process-convention
    summary: "Merge/tag/install step listed as a task checklist item"
    location: "project-documents/user/tasks/268-tasks.trade-tape-category-filter.md:383"
  - id: F002
    severity: note
    category: test-with-pattern
    summary: "Section 5 batches two implementation tasks before a shared test task"
    location: "project-documents/user/tasks/268-tasks.trade-tape-category-filter.md:277-311"
  - id: F003
    severity: note
    category: task-clarity
    summary: "Task 4.4's test-tier placement is left to implementer judgment"
    location: "project-documents/user/tasks/268-tasks.trade-tape-category-filter.md:247-256"
  - id: F004
    severity: pass
    category: uncategorized
    summary: "All 11 success criteria trace to at least one task"
    location: "project-documents/user/tasks/268-tasks.trade-tape-category-filter.md"
  - id: F005
    severity: pass
    category: uncategorized
    summary: "No scope creep"
    location: "project-documents/user/tasks/268-tasks.trade-tape-category-filter.md"
  - id: F006
    severity: pass
    category: uncategorized
    summary: "Sequencing is dependency-correct and acyclic"
    location: "project-documents/user/tasks/268-tasks.trade-tape-category-filter.md"
  - id: F007
    severity: pass
    category: uncategorized
    summary: "Commit checkpoints distributed per section, not batched"
    location: "project-documents/user/tasks/268-tasks.trade-tape-category-filter.md"
---

# Review: tasks — slice 268

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] Merge/tag/install step listed as a task checklist item

Task 7.4's first bullet reads "[PM] Merge the slice branch, tag and install the release on manta9000." Project memory `no-git-tasks-in-slices` records a PM veto (2026-08-18): "No merge or branch operation may be a slice task... When writing or reviewing a task breakdown, strip merge and branch steps on sight." Commits are explicitly fine as tasks (and this breakdown does that correctly at every section checkpoint), but the merge/tag/install bullet is exactly the kind of item the veto targets — it re-litigates a workflow step whose target is already fixed by the branch rules in CLAUDE.md. Recommend removing that bullet from Task 7.4 (or rephrasing 7.4 to start from "run the cutover script" and leave merge/tag/install as ordinary out-of-band release workflow, not a checklist line).

### [NOTE] Section 5 batches two implementation tasks before a shared test task

Every other section in this breakdown pairs one implementation task with one immediately-following test task (1.1→1.2, 2.1→2.2, 3.1→3.2, 3.3→3.4, 4.1→4.2, 4.3→4.4). Section 5 instead runs 5.1 (`trade_status.py` bucket re-scoping) then 5.2 (renderer/JSON) before a single combined test task 5.3. This is defensible — 5.2 only has something to test once 5.1's status object exists, and 5.3 exercises both together — but it's a deviation from the pattern applied elsewhere and worth a conscious call rather than an incidental one.

### [NOTE] Task 4.4's test-tier placement is left to implementer judgment

"Success: tests pass in whichever tier hosts them (the catalog query needs real SQL — integration tier, beside Task 3.4's tests, unless an existing fixture makes the unit tier honest)" gives a junior AI a conditional rather than a fixed target. The parenthetical strongly implies integration tier is the answer, so this is low-risk, but pinning it explicitly (e.g. "place in `test/integration/test_kalshi_trades.py` beside Task 3.4") would remove the ambiguity entirely.

### [PASS] All 11 success criteria trace to at least one task

Cross-referencing the slice design's Success Criteria 1–11 against tasks: SC1 (unset = today's behavior) → Tasks 3.4, 7.1; SC2 (Crypto filter accounting) → Tasks 3.3, 3.4; SC3 (precedence) → Task 3.4; SC4 (both drains inherit) → Tasks 3.4, 4.5; SC5 (status text/JSON + re-scoped buckets) → Tasks 5.1–5.3; SC6 (log lines) → Tasks 4.1, 4.2; SC7 (NULL category unaffected) → Task 3.4; SC8 (stored trades untouched) → Task 7.2 (walkthrough step 4, "prove stored history is intact"); SC9 (`UnknownTradesFilterCategoryError`) → Tasks 4.3, 4.4; SC10 (three-surface docs) → Tasks 6.1–6.3; SC11 (architecture amendment) → Task 6.4. No orphaned criterion found.

### [PASS] No scope creep

Every task traces to a design element (Technical Scope, a numbered Decision, or a Success Criterion). Task 7.3 (cutover script) and 7.4 (PM cutover) map to the design's explicit in-scope "Cutover" bullet and Decision 8's precondition, not to invented work. No task touches candle-path code or introduces schema/migration work, consistent with the design's Out-of-scope list.

### [PASS] Sequencing is dependency-correct and acyclic

Sections build strictly forward: Settings field (1) → SQL rendering that needs no settings-shaped input yet but is consumed next (2) → write-path/`PageCounts`/constructor + call sites, which consumes Section 2's `trades_filter_sql` (3) → sync/result/validation built on Section 3's repository property (4) → status surfaces reading the same `Settings` field (5) → documentation (6) → full validation, walkthrough refinement, and cutover (7). The Context Summary explicitly and correctly justifies bundling the constructor change with both call sites in Task 3.3 (required keyword forces atomic landing to keep the tree green) rather than splitting artificially.

### [PASS] Commit checkpoints distributed per section, not batched

Checkpoint commits appear at 1.3, 2.3, 3.5, 4.6, 5.4, 6.5, and a final commit folded into 7.2 — one per section as the project's "checkpoint per section" convention requires, not accumulated into a single end-of-slice commit.
