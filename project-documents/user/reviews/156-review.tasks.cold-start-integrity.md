---
docType: review
layer: project
reviewType: tasks
slice: cold-start-integrity
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/156-tasks.cold-start-integrity.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260508
dateUpdated: 20260508
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "All five success criteria have corresponding tasks"
    location: 156-tasks.cold-start-integrity.md
  - id: F002
    severity: concern
    category: ci-automation
    summary: "CI gating is not explicitly implemented"
    location: 156-tasks.cold-start-integrity.md
  - id: F003
    severity: concern
    category: task-sequencing
    summary: "Piece ordering differs from design's stated rationale"
    location: 156-tasks.cold-start-integrity.md
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Test-after-implementation pattern satisfied for all CLI and fixture tasks"
    location: 156-tasks.cold-start-integrity.md
  - id: F005
    severity: pass
    category: uncategorized
    summary: "All four pieces are represented with complete sub-tasks"
    location: 156-tasks.cold-start-integrity.md
  - id: F006
    severity: pass
    category: uncategorized
    summary: "All commits are distributed, not batched at end"
    location: 156-tasks.cold-start-integrity.md
  - id: F007
    severity: note
    category: documentation
    summary: "T02 \"pre-flight schema snapshot\" is referenced by two tasks"
    location: 156-tasks.cold-start-integrity.md
  - id: F008
    severity: pass
    category: uncategorized
    summary: "No tasks are oversized or should be split"
    location: 156-tasks.cold-start-integrity.md
  - id: F009
    severity: pass
    category: uncategorized
    summary: "Load test NFR — not applicable"
    location: unverified
---

# Review: tasks — slice 156

**Verdict:** CONCERNS
**Model:** minimax/minimax-m2.7

## Findings

### [PASS] All five success criteria have corresponding tasks

The five success criteria from the slice design are covered:
- SC1 (Fresh DB cold-start works): T22 + T24 docs
- SC2 (Existing DBs unaffected): T05, T11, T23
- SC3 (Integration test gates CI): T17, T18, T19, T20, T21
- SC4 (Single source of schema truth): T12
- SC5 (`mt data init` documented): T24

No orphaned tasks exist outside the success criteria scope.

---

### [CONCERN] CI gating is not explicitly implemented

SC3 requires "Integration test gates CI" and the slice design's "Verification walkthrough §D" shows the env var set inline for a pytest invocation. However, **no task adds this test to the CI pipeline configuration** (e.g., a GitHub Actions workflow step or `pytest.ini`/`pyproject.toml` entry with an `integration` marker). T20 documents the env var requirement and T21 runs the test locally, but neither wires it into CI for automated gating on every PR. The CI gating is left implicit. A task similar to:

```
- [ ] **TXX — Wire cold-start integration test into CI**
  - [ ] Add integration job to `.github/workflows/test.yml` (or equivalent)
    with `MT_TIMESCALE_TEST_URL` from a service container
  - [ ] Run `pytest -m integration test/integration/test_cold_start.py`
  - [ ] Success: job runs in CI and gates merge
```

should be added between T20 and T21, or folded into T20 with explicit success criteria for CI wiring.

---

### [CONCERN] Piece ordering differs from design's stated rationale

The slice design states the rationale for ordering: "The first three [pieces 1, 2, 3] deliver the immediate fix; the fourth [piece 4] folds `timescale_init.py`." The task breakdown executes piece 4 (T06–T13) before piece 2 (T14–T16), citing "run before piece 2 to keep `mt data init` simple." This is technically correct (post-fold, `init` only needs to call `apply_schema_migrations`), but the stated order diverges from the design's framing and could confuse future readers about which pieces constitute the "immediate fix" vs. the structural fix. A one-line note in T14's preamble ("[ ] Note: piece 4 must land first because `mt data init` relies on migrations for extension/hypertable creation") would make the dependency explicit.

---

### [PASS] Test-after-implementation pattern satisfied for all CLI and fixture tasks

- T14 (implement `init`) → T15 (test `init`) ✅
- T17 (implement fixture) → T18/T19 (tests using fixture) ✅

T05 and T11 are verification tasks ("verify against trading_test") rather than unit tests, so the pattern does not apply to them.

---

### [PASS] All four pieces are represented with complete sub-tasks

- Piece 1 (038 fixup): T03, T04, T05 ✅
- Piece 2 (`mt data init`): T14, T15, T16 ✅
- Piece 3 (integration test): T17, T18, T19, T20, T21 ✅
- Piece 4 (fold timescale_init.py): T06, T07, T08, T09, T10, T11, T12, T13 ✅

No piece is under-scoped.

---

### [PASS] All commits are distributed, not batched at end

Four clean checkpoint commits are distributed throughout:
- T13: `refactor: fold timescale_init.py into migration chain`
- T16: `feat(cli): add mt data init for one-step cold-start`
- T21: `test: add cold-start integration test`
- T25: `chore: bump version to 0.4.0`

---

### [NOTE] T02 "pre-flight schema snapshot" is referenced by two tasks

T02 captures `/tmp/trading_test.before.sql` for T11's diff verification and T23's final schema parity check. This is correct usage, but the task brief says "Commit nothing — this file is verification scaffolding." If T13 commits piece 4 without including this file, subsequent tasks that expect it (T11, T23) will silently find it missing. Consider adding a note in T13 or T16 that `/tmp/trading_test.before.sql` from T02 must still be on disk — or capture a fresh snapshot in T23 rather than relying on a stale T02 artifact.

---

### [PASS] No tasks are oversized or should be split

Each task has a single, clearly bounded action. The longest task (T22 — end-to-end cold-start) correctly bundles all required verifications (`init`, `migrate status`, `caggs status`) under one success criterion (SC1). No task exceeds the scope of its parent piece.

---

### [PASS] Load test NFR — not applicable

The slice design does not restate any NFR load test requirement. No load test task is expected in `tests/load/`.
