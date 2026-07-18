---
docType: review
layer: project
reviewType: tasks
slice: urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/166-tasks.urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency.md
aiModel: claude-fable-5
status: complete
dateCreated: 20260718
dateUpdated: 20260718
findings:
  - id: F001
    severity: concern
    category: sequencing
    summary: "Pre-merge integrity baselines are captured too late in the sequence"
    location: project-documents/user/tasks/166-tasks.urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency.md:290-293
  - id: F002
    severity: concern
    category: gap
    summary: "No explicit task applies migration 043 to prod, and no task verifies prod's dimension interval post-apply"
    location: project-documents/user/tasks/166-tasks.urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency.md:157-165
  - id: F003
    severity: concern
    category: sequencing
    summary: "Phase C tooling commit is sequenced after the prod run it enables"
    location: project-documents/user/tasks/166-tasks.urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency.md:262-265
  - id: F004
    severity: concern
    category: test-coverage
    summary: "Restated NFR has no load test task in `tests/load/` and no CI gating task"
    location: unverified
  - id: F005
    severity: note
    category: task-scope
    summary: "C1 is large for a single task"
    location: project-documents/user/tasks/166-tasks.urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency.md:192-209
  - id: F006
    severity: pass
    category: coverage
    summary: "Success-criteria coverage is otherwise complete and traceable"
    location: project-documents/user/tasks/166-tasks.urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency.md:322-340
  - id: F007
    severity: pass
    category: sequencing
    summary: "Test-with pattern and dependency ordering are respected"
    location: project-documents/user/tasks/166-tasks.urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency.md:167-172
---

# Review: tasks — slice 166

**Verdict:** CONCERNS
**Model:** claude-fable-5

## Findings

### [CONCERN] Pre-merge integrity baselines are captured too late in the sequence

Success Criteria 4–5 (no data loss) require before/after comparison, but the baseline capture exists only as a parenthetical inside D3 ("capture the baselines before C4 if not already recorded"). A junior AI executing tasks in order reaches C4 (the irreversible prod bulk merge) with no task instructing it to capture bounded-window `count(*)`/`MIN`/`MAX` per symbol, the 162 grouped coverage query results, or per-cagg bar counts first. If baselines are skipped, D3 becomes unverifiable after the fact — the merge cannot be un-run. The baseline capture should be an explicit task (or checklist item) sequenced immediately before C4, not a conditional aside two tasks later.

### [CONCERN] No explicit task applies migration 043 to prod, and no task verifies prod's dimension interval post-apply

Phase B authors migration `043` (`set_chunk_time_interval`) and tests it on a dev/fixture DB (B4), and Phase D verifies cold-start (D5). But no task runs the migration chain against the prod `trading` DB, and Success Criterion 3's clause "a fresh chunk created by new inserts has a 7-day interval" is never verified on prod (e.g. checking `_timescaledb_catalog.dimension.interval_length` or `timescaledb_information.dimensions` after apply). The Phase C tasks (C1–C4) implicitly assume 043 has run — otherwise post-merge inserts would recreate 4-hour chunks — but nothing sequences or states it. Add an explicit "apply migrations to prod and confirm `minute_ohlcv` dimension interval equals the constant" step before C4.

### [CONCERN] Phase C tooling commit is sequenced after the prod run it enables

C8 commits the merge driver and its tests *after* C3–C7, meaning the high-risk prod bulk mutation (C4) runs on uncommitted code. If anything goes wrong mid-run (or the working tree is disturbed), there is no committed record of the exact driver version that mutated 126 GB of prod data. The project guideline is commit checkpoints distributed throughout, and the natural buildable checkpoint is after C2 (driver + tests green) and before C3/C4. Move the commit to immediately follow C2.

### [CONCERN] Restated NFR has no load test task in `tests/load/` and no CI gating task

The slice design restates the 140-arch NFR ("view latency stays sub-second at full-universe scope", Success Criterion 8). Per the review criteria, this requires a load test task in `tests/load/` covering the NFR (or this breakdown adding one) plus a CI wiring task to gate on it. The breakdown provides only D2 — a one-time manual before/after measurement with a PM escalation path. No `tests/load/` task and no CI gating task exist, so the NFR has no regression protection after this slice closes. Mitigating context: the NFR is measured against a 126 GB prod table, which a CI-run load test cannot realistically reproduce, and the slice explicitly defers a `bars_summary` rewrite to a PM decision — but the breakdown should at least record that decision explicitly (e.g. a task noting why no load test is added, or a scaled-fixture load test) rather than leaving the gap implicit. Location is `unverified` because I have not confirmed whether a `tests/load/` directory exists in the repo.

### [NOTE] C1 is large for a single task

C1 bundles catalog enumeration, window grouping, per-window transactional merge, skip logic (single-chunk and mixed windows), progress/error reporting, `--dry-run`, and job-pause pre-flight. It is well-specified with subitems and an existing template (`data extend`), so it is completable — but it is at the upper bound of what one task should carry. Splitting pre-flight + `--dry-run` planning from the mutation path would tighten it. Not blocking.

### [PASS] Success-criteria coverage is otherwise complete and traceable

Criteria 1–2 → D1; 3 → C4/B4/D5 (modulo the prod-apply gap above); 4–5 → D3; 6 → A7/D6; 7 → C5/D4; 8 → D2; 9 → C3/C6. The explicit Coverage Check table maps every design element to a task, review findings F001–F004 are each addressed (D2, C3/C6, C5, B5), and no task fails to trace back to the design — no scope creep found. The evidence-before-fix ordering (Phase A gate before any mutation) and the A5 rehearsal answering the three gating questions faithfully mirror the design.

### [PASS] Test-with pattern and dependency ordering are respected

B4 immediately follows its implementation tasks B2/B3; C2 immediately follows C1 and explicitly forbids touching the prod table. Phase ordering is linear with no circular dependencies, both PM gates from the design are present (A7 hard stop; fallback escalation noted in context), and commit checkpoints appear in all four phases (A7, B6, C8, D6) rather than batched at the end — with the C8 placement caveat noted above.
