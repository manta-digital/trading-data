---
docType: review
layer: project
reviewType: tasks
slice: collection-pass-and-supervised-install
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/263-tasks.collection-pass-and-supervised-install.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260825
dateUpdated: 20260825
reviewedSha: 148d8789ebff9e15f4adc4ce2296498f38189fcd
findings:
  - id: F001
    severity: concern
    category: coverage-gap
    summary: "\"mt-run follow kalshi attaches\" is never exercised by any task"
    location: "project-documents/user/tasks/263-tasks.collection-pass-and-supervised-install.md:481-492"
  - id: F002
    severity: note
    category: sequencing
    summary: "Section 2 test task has a forward dependency on Section 3's enum addition"
    location: "project-documents/user/tasks/263-tasks.collection-pass-and-supervised-install.md:175-177"
  - id: F003
    severity: pass
    category: completeness
    summary: "All 12 numbered success criteria trace to at least one task"
    location: "project-documents/user/tasks/263-tasks.collection-pass-and-supervised-install.md"
  - id: F004
    severity: pass
    category: scope
    summary: "No scope creep — every task traces back to a design decision or criterion"
    location: "project-documents/user/tasks/263-tasks.collection-pass-and-supervised-install.md"
  - id: F005
    severity: pass
    category: process
    summary: "Commit checkpoints are distributed, not batched"
    location: "project-documents/user/tasks/263-tasks.collection-pass-and-supervised-install.md"
  - id: F006
    severity: pass
    category: nfr-coverage
    summary: "No load-test task needed; none introduced"
    location: "unverified"
---

# Review: tasks — slice 263

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] "mt-run follow kalshi attaches" is never exercised by any task

Criterion 6 in the slice design explicitly requires proving that `mt-run follow kalshi` attaches (`263-slice...md:243`: "`mt-run follow kalshi` attaches"). Task 10.2, which is the task assigned to prove Criterion 6, runs `sudo mt-run kalshi`, the `journalctl -o verbose` grep, `mt-run status`, and both root-path `data kalshi status` commands — but never runs `mt-run follow kalshi`. The slice design's own walkthrough step 6 has the same gap (only `sudo mt-run kalshi` is shown, with a comment "live output; Ctrl-C detaches" that describes that command, not `follow`). Since the task breakdown is expected to close gaps found while designing (per this slice's own stated principle), Task 10.2 should add one line — e.g. `sudo mt-run kalshi & sleep 2; sudo mt-run follow kalshi` (Ctrl-C to detach) — and record the observed attach, so the full text of Criterion 6 is actually proven rather than partially proven by inference from `mt-run status`.

### [NOTE] Section 2 test task has a forward dependency on Section 3's enum addition

Task 2.3's tests assert on `SyncEventType.PASS_STARTED`/`PASS_FINISHED`, which are not added until Task 3.1 (Section 3, later in file order). The task text acknowledges this directly ("write these two assertions so they fail until then, or order the work so Task 3.1's one-line enum change lands first"), and the Implementation Notes' suggested dev order in the slice design resolves it the same way. Because the resolution is explicit and low-risk (a one-line enum addition), this doesn't block progress, but a junior AI following section numbers strictly (1→2→3) will hit an intentionally-red test in 2.3 without re-reading the caveat closely — worth a one-line reorder note at the top of Section 2 rather than buried in a sub-bullet.

### [PASS] All 12 numbered success criteria trace to at least one task

Criteria 1–5, 7–12 each have a clear, direct task (or pair of task) proving them: 1/2/3/4 → Sections 2–4, 7, 9.2; 5 → 6.1/6.2/6.7/6.8; 7 → 10.1/10.3; 8/9 → 10.4; 10 → Section 8; 11 → 5.1/5.2; 12 → 9.1. No criterion is silently dropped.

### [PASS] No scope creep — every task traces back to a design decision or criterion

Each section opens by citing the Decision/Implementation Details section it implements (e.g. Section 1 → Decision 1, Section 6 → Decisions 4–7/10), and no task introduces functionality (candle/trade phases, arbitration, authenticated-mode adoption) explicitly called out of scope in the slice design.

### [PASS] Commit checkpoints are distributed, not batched

Ten commit checkpoints appear at 1.3, 2.3, 3.3, 4.2, 5.2, 6.8, 7.1, 8.4, 9.2, 10.6 — one per logical unit of work, matching CLAUDE.md's "commit at least once per task" / checkpoint-per-section convention and avoiding an end-of-slice batch commit.

### [PASS] No load-test task needed; none introduced

The slice design restates no numeric NFR (throughput/latency/SLO) — Decision 4's request-budget analysis is a capacity estimate, not a testable threshold — so the reviewer instruction requiring a `tests/load/` task (and corresponding CI gate) does not apply here, and the task breakdown correctly does not fabricate one.
