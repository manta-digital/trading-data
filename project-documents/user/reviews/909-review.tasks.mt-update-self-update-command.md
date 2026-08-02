---
docType: review
layer: project
reviewType: tasks
slice: mt-update-self-update-command
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/tasks/909-tasks.mt-update-self-update-command.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260802
dateUpdated: 20260802
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "All 9 functional success criteria have at least one task covering them"
    location: 909-tasks.mt-update-self-update-command.md
  - id: F002
    severity: pass
    category: uncategorized
    summary: "All technical requirements have explicit tasks"
    location: 909-tasks.mt-update-self-update-command.md
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Test-with pattern respected"
    location: 909-tasks.mt-update-self-update-command.md
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Task sizing is appropriate for a junior AI"
    location: 909-tasks.mt-update-self-update-command.md
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Sequencing and dependencies are respected"
    location: 909-tasks.mt-update-self-update-command.md
  - id: F006
    severity: pass
    category: uncategorized
    summary: "Commit checkpoints distributed throughout, not batched"
    location: 909-tasks.mt-update-self-update-command.md
  - id: F007
    severity: pass
    category: uncategorized
    summary: "No NFR requires a `tests/load/` task; no CI gating left implicit"
    location: unverified
  - id: F008
    severity: pass
    category: uncategorized
    summary: "Walkthrough steps in the slice design map to verification tasks"
    location: 909-tasks.mt-update-self-update-command.md
  - id: F009
    severity: pass
    category: uncategorized
    summary: "No scope creep detected"
    location: 909-tasks.mt-update-self-update-command.md
  - id: F010
    severity: note
    category: uncategorized
    summary: "Slice 908 deferred criterion 4 is correctly threaded in"
    location: 909-tasks.mt-update-self-update-command.md
---

# Review: tasks — slice 909

**Verdict:** PASS
**Model:** minimax/minimax-m3

## Findings

### [PASS] All 9 functional success criteria have at least one task covering them

Trace: SC1 (up-to-date) → 3.3 "Up-to-date → 'up to date' message, exit 0, no prompt, no subprocess"; SC2 (prompt + upgrade) → 3.3 TTY-confirm-accepted; SC3 (`--yes`/non-TTY) → 3.3 dedicated bullets; SC4 (`--json` purity) → 3.3 dedicated bullet + 2.3/2.7 unit tests; SC5 (editable refusal, no network) → 2.1 detection, 2.2 test, 3.3 editable/source bullet; SC6 (pipx/pip print-not-run) → 2.5 mapping + 2.6 test + 3.3 bullets; SC7 (registry unreachable clean exit 1) → 2.4 failure tests + 3.3 "Registry unreachable (human mode) → one-line message, exit 1, no traceback"; SC8 (post-upgrade migration count, never fails update) → 2.7 best-effort probe + 2.8 tests + 3.3 "Probe degradation → generic pointer line printed, update still exits 0"; SC9 (no startup registry call) → 3.2 success criterion explicitly cites "no registry traffic".

### [PASS] All technical requirements have explicit tasks

- `packaging` explicit dependency → Task 1.1.
- No scattered literals → Task 1.2 (URL template and all three timeouts in `constants.py`); task 3.1 enforces "no timeout or URL literal appears in `update.py` when Task 2 lands" as the success criterion.
- Unit test coverage matrix listed in the LLD → met by 2.2, 2.4, 2.6, 2.8, and 3.3.
- mypy/ruff clean → Task 3.4.

### [PASS] Test-with pattern respected

Every implementation sub-bullet is immediately followed by its test sub-bullet within the same task: 2.1/2.2, 2.3/2.4, 2.5/2.6, 2.7/2.8. The orchestrating command in 3.1 is followed by the behavior matrix in 3.3.

### [PASS] Task sizing is appropriate for a junior AI

Each subtask has a single, verifiable success criterion with concrete checks. The largest items (3.1 at Effort 3 and 3.3 at Effort 3) are necessarily coupled to the data-flow diagram but remain tightly scoped; 2.1's detection-order list and 2.7's binary-resolution rule are spelled out enough to be implementable without further design decisions.

### [PASS] Sequencing and dependencies are respected

Constants and dependency land in Task 1 before any helper imports them in Task 2; helpers and their tests land before the orchestrator in Task 3 references them; docs in Task 4 reference the command finalized in Task 3; release in Task 5.1 requires the merge from Tasks 1–4, and the e2e walkthrough in Task 5.2 explicitly requires 5.1's PyPI publication (the second published version needed to demonstrate `0.6.1 → 0.7.0`). No circular dependencies.

### [PASS] Commit checkpoints distributed throughout, not batched

Checkpoints at 1.3, 2.9, 3.4, 4.1, 5.1, 5.3 — one per major phase plus a release commit, with no batched "commit everything at the end."

### [PASS] No NFR requires a `tests/load/` task; no CI gating left implicit

The parent slice (`900-slices.foundation-cleanup.md`) is not provided, and no restated NFR (latency, throughput, concurrency) appears in the slice design itself. The slice design's technical requirements are about correctness, unit-test coverage, and lint — none are load-style NFRs. No CI gating is implied by the tasks; the publish workflow is already established by 908.

### [PASS] Walkthrough steps in the slice design map to verification tasks

Walkthrough step 1 (editable dev refusal) → 3.3 editable/source human-mode bullet + 2.1 detection. Step 2 (`--json` query) → 3.3 `--json` purity bullet. Step 3 (real 0.6.1 → 0.7.0 upgrade) → Task 5.2. Step 4 (registry outage) → 3.3 "Registry unreachable (human mode)" bullet + 2.4 failure tests. Step 5 (non-TTY safety) → 3.3 "Non-TTY without `--yes`" bullet. Task 5.3 captures observed output into the LLD walkthrough, closing the loop.

### [PASS] No scope creep detected

Every task ties back to a design decision (D1–D9) or a success criterion. Nothing in the task list introduces behavior not covered by the slice design (no update channels, no version pinning, no migrations, no DB connection — all correctly excluded). The release mechanics in Task 5.1/5.3 are the standard slice-closeout pattern, not net-new scope.

### [NOTE] Slice 908 deferred criterion 4 is correctly threaded in

Task 5.2's header explicitly notes it "closes 908 deferred criterion 4," and Task 5.3 closes out the LLD. Worth keeping an eye on: the pinned-receipt behavior (whether `uv tool install --upgrade` unpinned removes the `==0.6.1` pin from the previous install) is flagged as confirm-at-release-time. The task correctly records the observation rather than assuming it; no action needed.
