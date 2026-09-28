---
docType: review
layer: project
reviewType: tasks
slice: multi-database-migration-and-credential-plumbing
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/923-tasks.multi-database-migration-and-credential-plumbing.md
aiModel: x-ai/grok-4.7
status: complete
dateCreated: 20260927
dateUpdated: 20260927
reviewedSha: 7cad754121758fe067a1ab45ac37e62834336679
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 5
findings:
  - id: F001
    severity: concern
    category: sequencing
    summary: "Walkthrough baseline is on the integration target, not `main`"
    location: "project-documents/user/tasks/923-tasks.multi-database-migration-and-credential-plumbing.md"
  - id: F002
    severity: concern
    category: scope
    summary: "Privilege-suite failure path edits the LLD mid-implementation"
    location: "project-documents/user/tasks/923-tasks.multi-database-migration-and-credential-plumbing.md"
  - id: F003
    severity: pass
    category: completeness
    summary: "Functional requirements are covered"
    location: "project-documents/user/slices/923-slice.multi-database-migration-and-credential-plumbing.md"
  - id: F004
    severity: pass
    category: sequencing
    summary: "Sequencing, scope, and granularity are sound"
    location: "project-documents/user/tasks/923-tasks.multi-database-migration-and-credential-plumbing.md"
---

# Review: tasks — slice 923

**Verdict:** CONCERNS
**Model:** x-ai/grok-4.7

## Findings

### [CONCERN] Walkthrough baseline is on the integration target, not `main`

Functional Requirement 7 and Verification Walkthrough step 1 require `mt data migrate status --json` against production to be identical before and after. The LLD captures that baseline "on main"; task 0.1 captures it "on the target branch" and 7.3 diffs against that file. Project git rules say an optional `git.integration_branch` is the fork/merge target and may not be `main`. A junior following the tasks can produce a green diff against a branch that already differs from the baseline the slice design treats as proof that the primary path is unchanged. Task 0.1 should name the same ref the walkthrough uses (or explicitly reconcile target vs `main`).

### [CONCERN] Privilege-suite failure path edits the LLD mid-implementation

Task 3.3 says if `CREATE EXTENSION timescaledb` fails as `tick_migrate`, apply the LLD mitigation (install the extension in the artifact) and record the measurement in the LLD Risk Assessment. Functional Requirement 8 still requires that `tick_migrate` can `CREATE EXTENSION timescaledb`. The mitigation changes the artifact's contract and 222's first migration (`CREATE EXTENSION IF NOT EXISTS`), which the slice marks excluded until 222. A junior is told to rewrite design and success criteria inside an implementation task, with no separate decision gate or follow-up task if the measurement fails. Keep the measurement as a stop-and-record; do not authorize an in-task LLD rewrite that redefines FR8.

### [PASS] Functional requirements are covered

FR1–FR6 map to tasks 2.4–2.7 (unit, including no-fallback and connection mapping) and 4.3 (two-database integration, both misroute directions, unreachable port). FR7 is tasks 1.6, 2.5–2.7, 3.3, 0.1, and 7.3. FR8 is tasks 3.1–3.3, including catalog isolation and idempotent re-apply. Technical requirements (resolver, registry, guard, CLI, line limits, ruff/mypy) are tasks 1.2–2.2, 2.7, and 7.1. Integration requirements (222 append-only surface, full tiers) are tasks 1.4–1.5, 3.1, 4.2, and 7.2. Documentation scope items 11–12 are section 5 and 6. No load-test NFR is restated, so no `tests/load/` or CI gate is required.

### [PASS] Sequencing, scope, and granularity are sound

Registry (1.4) precedes the guard's function-local import (2.1). Routing helper (2.3) precedes the three commands. Shared throwaway helper (4.1) precedes tick fixtures (4.2). The stale tick integration test is deleted (5.1) before the ratchet allowlist is emptied (5.3). Privilege measurements (section 3) precede the two-database suite, matching the LLD development approach. Tests sit in the same section as the code they prove (1.3 after 1.2, 2.2 after 2.1, 2.7 after 2.3–2.6, 3.3 after 3.1–3.2, 4.3 after 4.2, 5.2–5.4 each carry their own assertions). Checkpoints are at 1.7, 2.8, 3.4, 4.1, 4.4, 5.5, 6.4, and 7.4, not batched at the end. Excluded production tick DB, tick tables, second pools, and backup work are not tasked. Tasks are junior-completable with explicit success lines; 2.7 and 3.1 are the largest (effort 3) but each is one deliverable with a closed checklist, not a slice that must be split.

### Run Digest

- Response length: 3677 chars
- Response is newline-free: no
- Tool calls made: 5
- Tool calls failed: 0
- Stop reason: stop
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 4
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 4
- Finding-shaped matches — surviving validation: 4
