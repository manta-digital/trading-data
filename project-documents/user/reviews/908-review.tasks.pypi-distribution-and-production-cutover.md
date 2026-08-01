---
docType: review
layer: project
reviewType: tasks
slice: pypi-distribution-and-production-cutover
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/tasks/908-tasks.pypi-distribution-and-production-cutover.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260801
dateUpdated: 20260801
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "All five success criteria have explicit task coverage"
    location: project-documents/user/tasks/908-tasks.pypi-distribution-and-production-cutover.md
  - id: F002
    severity: pass
    category: uncategorized
    summary: "All eight decisions trace to tasks or are explicitly out-of-scope"
    location: project-documents/user/tasks/908-tasks.pypi-distribution-and-production-cutover.md
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Test-with pattern followed for the only testable unit"
    location: project-documents/user/tasks/908-tasks.pypi-distribution-and-production-cutover.md#task-1
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Commit checkpoints distributed, not batched"
    location: project-documents/user/tasks/908-tasks.pypi-distribution-and-production-cutover.md
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Task 4.4 honestly handles the criterion-4 deferral case"
    location: project-documents/user/tasks/908-tasks.pypi-distribution-and-production-cutover.md#task-4
  - id: F006
    severity: note
    category: uncategorized
    summary: "Task 2.4's test-suite run is a sanity check, not a gate"
    location: project-documents/user/tasks/908-tasks.pypi-distribution-and-production-cutover.md#task-2
  - id: F007
    severity: note
    category: uncategorized
    summary: "D6 is referenced inline rather than decomposed into sub-tasks"
    location: project-documents/user/tasks/908-tasks.pypi-distribution-and-production-cutover.md#task-4
  - id: F008
    severity: note
    category: uncategorized
    summary: "No load-test task required"
    location: unverified
  - id: F009
    severity: note
    category: uncategorized
    summary: "Task 3.1's success criterion acknowledges the 907 interface constraint"
    location: project-documents/user/tasks/908-tasks.pypi-distribution-and-production-cutover.md#task-3
---

# Review: tasks — slice 908

**Verdict:** PASS
**Model:** minimax/minimax-m3

## Findings

### [PASS] All five success criteria have explicit task coverage

Criterion 1 (`uv tool install` reports published version) → Tasks 1.1-1.3 + 2.1-2.3 + 4.3. Criterion 2 (`gh release list` shows v0.5.0 and v0.6.0) → Task 4.2 (with v0.6.0 release as the primary artifact and v0.5.0 retroactive). Criterion 3 (tag push → TestPyPI → PyPI, no credentials) → Tasks 3.1, 3.2, 4.1. Criterion 4 (upgrade between two versions) → Task 4.4, which honestly flags the chicken-and-egg constraint. Criterion 5 (README install without cloning) → Task 5.1.

### [PASS] All eight decisions trace to tasks or are explicitly out-of-scope

D1 (rename distribution, leave import package) → Task 2.1 + 2.5 (CHANGELOG explicitly states import/config unchanged). D2 (constant + warning) → Tasks 1.1, 1.2, 1.3. D3 (trusted publishing, no `environment:` key, `ci.yml` filename) → Task 3.1 with `no environment:` key called out in bold; Task 3.2 desk-checks against the publisher config. D4 (TestPyPI first, `skip-existing`, `continue-on-error`, install against real PyPI) → Task 3.1 sets the flags; Task 4.3 explicitly notes "Do not attempt this against TestPyPI." D5 (0.6.0 first, v0.5.0 retroactive) → Tasks 2.1, 4.1, 4.2. D6 (failure modes) → referenced inline in Task 4.1 ("classify against D6 before retrying") and surfaced operationally in Task 4.3 (the `dev` result). D7 and D8 are correctly handled by non-touch acknowledgements in the non-negotiables section, since both are about not changing things.

### [PASS] Test-with pattern followed for the only testable unit

Task 1.3 immediately follows the implementation in Task 1.2, asserts on the log record (not only on output) per the D2 design, and is scoped to the touched files only. This is the correct location for the test: the workflow's release job is intentionally not gated on lint/test (D3), so there is no "test the publish flow" step to follow.

### [PASS] Commit checkpoints distributed, not batched

One commit per logical unit (Tasks 1, 2, 3, 5), with Task 4 appropriately having no per-subtask commit because its steps (merge, tag, release, install-verify) are themselves the durable artefacts. The CHANGELOG entry (Task 2.5) rides the Task 2 commit, which is correct since the entry becomes the GitHub Release body (criterion 2).

### [PASS] Task 4.4 honestly handles the criterion-4 deferral case

Criterion 4 requires two published versions, but 0.6.0 is the first artifact. The task explicitly offers two acceptable outcomes — demonstrate the upgrade, or defer to the next release and note the deferral in close-out — rather than burning a version solely to satisfy the test. This is the right engineering call and keeps the criteria tractable.

### [NOTE] Task 2.4's test-suite run is a sanity check, not a gate

D3 explicitly states releases must not be gated behind test runs (907 waits on 905's lint sweep). Task 2.4's success criterion is "no failures beyond that baseline" — i.e., this slice must not regress existing tests — which is consistent with D3's stance (no gating, just verification). A junior AI could misread "Success: no failures beyond that baseline" as a hard gate; consider tightening the wording to "verification step, not a publish gate" to preempt that reading. Not blocking.

### [NOTE] D6 is referenced inline rather than decomposed into sub-tasks

D6 enumerates four failure modes (OIDC rejection, name collision, interrupted upload, workflow does not fire). Task 4.1 handles them with a single instruction "classify against D6 before retrying," and Task 4.3 surfaces the constant-wrong case operationally. This is adequate given D6 is fully documented in the slice design and is something a human runs at publish time rather than something code or tests execute. No dedicated sub-task needed.

### [NOTE] No load-test task required

The slice design does not restate any NFRs requiring load testing — the slice is purely about publication/distribution. No `tests/load/` task is needed and none is missing.

### [NOTE] Task 3.1's success criterion acknowledges the 907 interface constraint

The success line "named `ci.yml` so 907 extends it" makes the cross-slice interface contract visible to the implementer. This is good practice and prevents the common failure mode of someone renaming the file to `publish.yml` and breaking 907 later.
