---
docType: review
layer: project
reviewType: tasks
slice: coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260813
dateUpdated: 20260813
reviewedSha: 55e661cde8c0e626d6936db21f494b924af4def0
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "All 19 success criteria have corresponding tasks across Parts 1 and 2"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md"
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Sequencing correctly operationalizes the design's two-window Rebuild Window model"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md"
  - id: F003
    severity: pass
    category: uncategorized
    summary: "G.5 stop-and-replan gate prevents committing to an unsafe full-sweep"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md"
  - id: F004
    severity: pass
    category: uncategorized
    summary: "G.7 detects partial materialization by content per the design's Rebuild Window guidance"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md"
  - id: F005
    severity: pass
    category: uncategorized
    summary: "G.9a closes criterion 12 with an actual prod measurement rather than Part 1's seeded prediction"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md"
  - id: F006
    severity: pass
    category: uncategorized
    summary: "G.13 (walkthrough 8a) is the only check that distinguishes the fix from the defect"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md"
  - id: F007
    severity: pass
    category: uncategorized
    summary: "Non-negotiables from Part 1's design are restated at the top of Part 2"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md"
  - id: F008
    severity: pass
    category: uncategorized
    summary: "Explicit Part 1 → Part 2 gating and PM-decision contingency"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md"
  - id: F009
    severity: pass
    category: uncategorized
    summary: "Task G is acknowledged as non-code, with the runbook/journal convention called out"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md"
  - id: F010
    severity: note
    category: uncategorized
    summary: "H.1 quality-gates language implies code changes despite Part 2 being operational"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md#H.1"
---

# Review: tasks — slice 169

**Verdict:** PASS
**Model:** minimax/minimax-m3

## Findings

### [PASS] All 19 success criteria have corresponding tasks across Parts 1 and 2

Walked all 19 criteria from the slice design. Criteria 1, 2, 3 (cold-start), 4, 15, 17, 19 are covered by Part 1 (Tasks A–F per the `projectState` field). Criteria 5–14, 16, 18 are covered in Part 2: G.6+G.8 (5), G.9 (6, 7, 8, 16 — including explicit "seven pre-167 caggs still report against their unchanged budgets"), G.15 (9), G.14 (10), G.3+G.11 (11), G.9a (12), G.4 (13 — verified directly by chain advancement, not inferred), G.10 (14), G.13 (18). H.2 audits all 19 with recorded evidence, which catches any drift in Part 1's coverage.

### [PASS] Sequencing correctly operationalizes the design's two-window Rebuild Window model

G.2 explicitly differentiates the daemon's stop duration (entire window — DDL through G.6) from the API server's (Window A only — restarted at G.4a, before materialization). This matches the slice design's "The daemon stays stopped for the entire window, not merely during DDL" and the rationale that running writes to `minute_ohlcv`/`daily_ohlcv` during a full-span refresh move the target mid-rebuild.

### [PASS] G.5 stop-and-replan gate prevents committing to an unsafe full-sweep

G.5's "Stop-and-replan condition" explicitly halts G.6 if the measured sub-window exceeds the host's safe envelope, with escalation to the PM before continuing. The success criterion is worded as a *judgment* ("explicitly judged safe") rather than a measurement — appropriate, because the Risks table makes clear that the cost of discovering a bad sub-window span partway through 64 years vastly exceeds the cost of one extra measurement cycle.

### [PASS] G.7 detects partial materialization by content per the design's Rebuild Window guidance

G.7 samples per-symbol `MIN(first_bucket)` against the known history floor rather than relying on `\dm` catalog presence. The task explicitly cites the 170 lesson (the daily rollups were half-materialized despite being present in the catalog), which is the same defect class the design flags in the Risks table and the sql.md rule.

### [PASS] G.9a closes criterion 12 with an actual prod measurement rather than Part 1's seeded prediction

G.9a runs `EXPLAIN (ANALYZE, BUFFERS) SELECT count(*) FROM data_status;` against prod after materialization is verified (so the measurement reflects the fully-materialized state, not Window B's empty-cagg state). It also escalates to the PM if the sub-second NFR is missed — preventing a width selection validated against a seeded test database from quietly passing on prod.

### [PASS] G.13 (walkthrough 8a) is the only check that distinguishes the fix from the defect

G.13's "policy-driven advance without manual refresh" correctly operationalizes the slice design's claim that "every other step is satisfiable by the bug." Placement after G.12 (daemon restart) and the explicit requirement to confirm raw also advanced over the same interval avoid the test being vacuous in either direction.

### [PASS] Non-negotiables from Part 1's design are restated at the top of Part 2

The four non-negotiables (catalog resolution by name, bounded sub-windows, exact counts only, content-based partial-materialization detection) are repeated verbatim from Part 1. This reduces the risk of a future editor dropping one when modifying Part 2 in isolation, and immediately orients any reader without forcing them to chase back to Part 1.

### [PASS] Explicit Part 1 → Part 2 gating and PM-decision contingency

Both the `projectState` field and the Context summary state "Task G cannot start until Part 1's Tasks A–F are merged." The Notes section further flags the PM Decisions (30-day provisional lag, widened `COVERAGE_CONTENT_STALENESS`) as operative — instructing the executor to stop and confirm if the PM revisits either before Task G runs. This is appropriate since the PM explicitly recorded both decisions as provisional.

### [PASS] Task G is acknowledged as non-code, with the runbook/journal convention called out

The Notes section explicitly states "Task G is prod execution and is not squashed into a single commit — use the runbook/journal convention for recording each step's result even though no code changes." This is the right framing for operational work and avoids the test-with-commit pattern being misapplied.

### [NOTE] H.1 quality-gates language implies code changes despite Part 2 being operational

H.1 says "ruff clean, mypy/pyright zero errors on all touched files" — but Part 2 has no code changes per the design (it is prod execution and close-out). The gate works as a final sanity check, but the phrasing reads as if code modifications are expected. Minor wording alignment (e.g., "no regressions in the touched files from Part 1, verified clean") would more accurately reflect what the task is checking. No action required — flagging for awareness.
