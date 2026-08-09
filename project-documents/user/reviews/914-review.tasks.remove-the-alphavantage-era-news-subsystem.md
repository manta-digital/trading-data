---
docType: review
layer: project
reviewType: tasks
slice: remove-the-alphavantage-era-news-subsystem
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/914-tasks.remove-the-alphavantage-era-news-subsystem.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260809
dateUpdated: 20260809
reviewedSha: 1f10e9f76adc8ef7e8ccc3bf2a8f511de3d4db95
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "All Functional Requirements are traced to tasks"
    location: 914-tasks.remove-the-alphavantage-era-news-subsystem.md:54-91
  - id: F002
    severity: pass
    category: uncategorized
    summary: "All Technical Requirements are traced to tasks"
    location: 914-tasks.remove-the-alphavantage-era-news-subsystem.md:46-52,98-105,162-178
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Both Integration Requirements are traced to tasks"
    location: 914-tasks.remove-the-alphavantage-era-news-subsystem.md:183-194
  - id: F004
    severity: pass
    category: sequencing
    summary: "Sequencing and dependencies are respected"
    location: 914-tasks.remove-the-alphavantage-era-news-subsystem.md:25-228
  - id: F005
    severity: pass
    category: scope
    summary: "Task sizing is appropriate"
    location: 914-tasks.remove-the-alphavantage-era-news-subsystem.md
  - id: F006
    severity: concern
    category: scope-creep
    summary: "Phase 4 commit checkpoint has no file changes to commit"
    location: 914-tasks.remove-the-alphavantage-era-news-subsystem.md:159-205
  - id: F007
    severity: concern
    category: gaps
    summary: "Task 4.5 lacks a captured baseline for the `mt --help` comparison"
    location: 914-tasks.remove-the-alphavantage-era-news-subsystem.md:199-205
  - id: F008
    severity: note
    category: sequencing
    summary: "Commit granularity diverges from slice design with a documented justification"
    location: 914-tasks.remove-the-alphavantage-era-news-subsystem.md:18-23
  - id: F009
    severity: note
    category: scope
    summary: "No load test or CI wiring required by the slice"
    location: 914-slice.remove-the-alphavantage-era-news-subsystem.md:103-129
---

# Review: tasks — slice 914

**Verdict:** CONCERNS
**Model:** minimax/minimax-m3

## Findings

### [PASS] All Functional Requirements are traced to tasks

Tasks 1.2 (source deletion), 2.1+2.2 (8 test files), 3.1+3.2 (pymongo/motor in pyproject.toml and uv.lock), and 4.5 (CLI unchanged) each map 1:1 to a stated Functional Requirement in the slice design. No gaps.

### [PASS] All Technical Requirements are traced to tasks

Tasks 1.3 and 2.3 cover the `grep` Technical Requirement (with the `NEWSTOCK` exclusion correctly called out twice). Task 3.2 covers `uv sync`. Task 4.1 covers ruff/mypy static checks. Tasks 4.2+4.3 cover per-subpackage suite passes including load. All four Technical Requirements have corresponding tasks.

### [PASS] Both Integration Requirements are traced to tasks

The "exactly 4 fewer integration failures" assertion is covered by Task 4.2 with a precise arithmetic comparison (`(1.1 baseline − 4)`). The timeout-hazard elimination is covered by Task 4.4, framed as a by-construction check rooted in the dependency removal in 3.2 — a reasonable interpretation given that no behavioral NFR is restated.

### [PASS] Sequencing and dependencies are respected

Phase 1 baseline (1.1) precedes all later comparisons; source deletion (1.2) precedes its verification gate (1.3); test deletion (2.1, 2.2) precedes its verification gates (2.3, 2.4); pyproject.toml edits (3.1) precede lockfile refresh (3.2); lockfile refresh precedes the by-construction timeout check (4.4). No circular dependencies.

### [PASS] Task sizing is appropriate

Each task is mechanical and completable by a junior AI with explicit success criteria. The multi-file deletions (1.2, 2.1, 2.2) could be split per-file but are reasonably grouped because they share a single success criterion (`does not exist` checks). Documentation tasks in Phase 5 are appropriately scoped.

### [CONCERN] Phase 4 commit checkpoint has no file changes to commit

The `Commit: chore: verify news subsystem removal is clean` line at the end of Phase 4 implies a commit, but Tasks 4.1–4.5 are pure verification steps that produce no file diffs. Unless verification surfaces a fix (in which case that fix belongs in a different, more descriptive commit), this commit will be empty. Either remove the commit checkpoint and treat Phase 4 as a gate-only phase, or fold the Phase 5 documentation commits (5.1, 5.2, 5.3) under Phase 4's commit so the checkpoint has content.

### [CONCERN] Task 4.5 lacks a captured baseline for the `mt --help` comparison

The Phase 4.5 success criterion says "output is unchanged," but Task 1.1 does not include capturing the `mt --help` output as part of the baseline. The task's own description acknowledges "expect identical (no command referenced the news subsystem)" — true by construction, but a textual diff still requires captured output to compare against. Add a `mt --help > /tmp/mt-help-before.txt` step to Task 1.1, then a `diff` step to Task 4.5, so the verification is reproducible rather than asserted.

### [NOTE] Commit granularity diverges from slice design with a documented justification

The slice design's Implementation Notes recommend a single combined commit ("splitting further adds no safety"), whereas the task breakdown uses four commits across Phases 1–4 plus a docs commit. The preamble justifies this with bisectability. The decision is defensible and the slice design's preference is not a hard constraint, but the breakdown should be aware it is overriding the upstream guidance rather than following it.

### [NOTE] No load test or CI wiring required by the slice

The slice design restates no behavioral NFR (e.g., latency, throughput) — its Integration Requirements are verification assertions about a removal. Task 4.3 correctly runs the existing load tier for regression rather than adding a new load test, and no CI gate change is required. The reviewer criteria for load-test addition do not trigger.
