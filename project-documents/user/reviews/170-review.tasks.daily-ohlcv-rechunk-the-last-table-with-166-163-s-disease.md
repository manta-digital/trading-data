---
docType: review
layer: project
reviewType: tasks
slice: daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/170-tasks.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260809
dateUpdated: 20260809
reviewedSha: bb350224e173aa1bf236ee7fefb84507d08b73a4
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "All 9 success criteria from the slice design have corresponding tasks"
    location: project-documents/user/tasks/170-tasks.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md
  - id: F002
    severity: pass
    category: sequencing
    summary: "Test-with pattern respected"
    location: project-documents/user/tasks/170-tasks.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md
  - id: F003
    severity: pass
    category: coverage
    summary: "No scope creep — all tasks trace to success criteria or to explicitly restated project constraints"
    location: project-documents/user/tasks/170-tasks.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md
  - id: F004
    severity: pass
    category: sequencing
    summary: "Sequencing and dependencies are correct"
    location: project-documents/user/tasks/170-tasks.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md
  - id: F005
    severity: pass
    category: safety
    summary: "Prod-protection constraints captured"
    location: project-documents/user/tasks/170-tasks.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md
  - id: F006
    severity: concern
    category: sequencing
    summary: "Commit checkpoints concentrated at phase boundaries rather than distributed throughout"
    location: project-documents/user/tasks/170-tasks.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md
  - id: F007
    severity: note
    category: documentation
    summary: "Prod query discipline mentioned once and not restated"
    location: project-documents/user/tasks/170-tasks.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md
  - id: F008
    severity: note
    category: testing
    summary: "B3.4 \"If any required editing\" is the right regression sentinel but the gate is implicit"
    location: project-documents/user/tasks/170-tasks.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md
---

# Review: tasks — slice 170

**Verdict:** CONCERNS
**Model:** minimax/minimax-m3

## Findings

### [PASS] All 9 success criteria from the slice design have corresponding tasks

Cross-referenced each criterion against the task list:
- **SC1** (chunk count ~120): C5.1 produces it; D1.2 verifies against C3.4 baseline.
- **SC2** (`MAX(time)` sub-second): D1.1 verifies against C3.4 timing baseline.
- **SC3** (31k-symbol EXPLAIN seconds): D1.3 verifies against C3.4 baseline.
- **SC4** (no data loss): C3.1/C3.3 capture baselines; D2.1/D2.2 verify with explicit stop-and-escalate on mismatch.
- **SC5** (cagg parity, R5 discriminator): D3.1 runs `mt data caggs verify`; D3.2 applies R5 per cagg; D3.3 handles the `SET` echo scripting pitfall.
- **SC6** (cold start at 70 days): B5.2 asserts `timescaledb_information.dimensions` after applying the full chain to a throwaway DB; B4.3 updates the slice-143 creation migration to render the constant.
- **SC7** (minute path bit-identical): B3.4 demands existing minute tests pass unmodified; B6.1/B6.2 default to `RechunkTarget.MINUTE`; D4.1/D4.2 verify on prod.
- **SC8** (no job left unscheduled, resumed jobs report Success): C6.1 resumes; C6.5 verifies both R4 zero-unscheduled and `last_run_status = 'Success'`.
- **SC9** (ANALYZE + approximate_row_count sanity): C6.4 runs both and cross-checks against C3.1 exact count.

### [PASS] Test-with pattern respected

Each implementation step is immediately followed by its test step:
- B1 (constant) → coverage in B5 (migration cold-start test, which depends on the constant)
- B2 (registry refactor) → B3 (registry unit tests)
- B4 (migration 050) → B5 (migration tests, including cold-start SC6 and idempotency)
- B6 (CLI `--table`) → B7 (CLI tests)
- B8 (integration on daily-shaped scratch) — self-contained driver test

The minute-path regression guard appears in B3.4 (unit-level) and D4 (prod-level), forming a strong two-tier guard.

### [PASS] No scope creep — all tasks trace to success criteria or to explicitly restated project constraints

The non-criterion tasks (B1.3, B2.5, D5.1–D5.7, C4.3) all trace to design-level rules: superseded docstrings, "untouched if scope creep," execution record / changelog / arch doc updates, and the R1 minute-job-still-scheduled verification. Nothing in the task list redesigns the driver or adds functionality beyond the slice scope.

### [PASS] Sequencing and dependencies are correct

- Phase B → Phase C → Phase D linear, no cycles.
- `DAILY_OHLCV_CHUNK_INTERVAL` (B1) precedes B2 (registry), B4 (migration), and B6 (CLI) — all of which consume it.
- Migration 050 must exist before B5 can assert it, before C2 can apply it, before the C2.2 dimension check makes sense.
- Phase D verification (D1/D2/D3) consumes baselines captured in C3 — ordering correct.
- D5.7 (merge to main) is correctly the final task.
- Phase C is correctly gated by an explicit STOP warning and the PM-authorization sub-task C1.1.

### [PASS] Prod-protection constraints captured

- B5.2 explicitly demands verifying the throwaway-DB fixture contains no TRUNCATE/DELETE against a production URL (2026-08-04 incident).
- Phase C is gated by an explicit STOP banner and the PM go/no-go sub-task.
- C4.1 mandates catalog-resolved job IDs (no hardcoded IDs).
- C4.3 explicitly verifies the minute family remains scheduled (R1).
- C6.2/C6.3 use `force => true` for cagg refresh, applying the 163 lesson.
- D2.3 is stop-and-escalate, not just a warning, on integrity mismatch.
- No CI gating task is needed because no `tests/load/` task is added; the perf NFRs (SC2/SC3) are verified directly on prod in D1, which is the only meaningful test environment for the 4.4 GB hypertable.

### [CONCERN] Commit checkpoints concentrated at phase boundaries rather than distributed throughout

Only two explicit commit points exist: B9.3 (one commit for all of Phase B, covering B1 through B8) and D5.7 (merge to main at end of Phase D). Phase B alone contains eight logical work units — constant (B1), driver refactor (B2), registry tests (B3), migration (B4), migration tests (B5), CLI flag (B6), CLI tests (B7), integration test (B8) — and Phase C contains six distinct operational steps. Batching the entire Phase B into one commit loses the bisect-friendly history that allowed 166's incident post-mortem to work backwards through changes. The instructions call for distributed checkpoints, not phase-end batching. Consider adding intermediate commits after B2 (registry refactor lands), after B4 (migration added), after B8 (integration test passes), and after C5 (prod run completes, before verification).

### [NOTE] Prod query discipline mentioned once and not restated

`SET statement_timeout` is explicitly called out in C3.1 but not restated in C3.2/C3.3/C3.4 or in D1.1/D1.2/D1.3, all of which also run prod queries. The slice design's verification walkthrough consistently prefixes with `SET statement_timeout = '120s';`; a single explicit "applies to every prod query in this phase" prefix on C3 and D1 would prevent a junior implementer from omitting it on, e.g., the per-cagg captures in C3.2. Minor copy-edit, not a correctness gap.

### [NOTE] B3.4 "If any required editing" is the right regression sentinel but the gate is implicit

B3.4 correctly identifies that any required edit to existing minute-path tests is evidence the refactor changed minute behavior (SC7), and D4 verifies it on prod. The chain is complete, but the "stop and raise it" instruction lives only in B2.5 ("If a change to any of these seems necessary, stop and raise it"). For a junior implementer, a single explicit "halt-and-escalate if B3.4 finds any required edit, before completing B9.3" pointer in B3.4 would make the SC7 guard unmissable. Currently the gate is enforceable but discoverable only by reading B2.5 in conjunction with B3.4.
