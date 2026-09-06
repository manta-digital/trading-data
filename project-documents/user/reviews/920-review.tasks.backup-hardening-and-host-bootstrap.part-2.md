---
docType: review
layer: project
reviewType: tasks
slice: backup-hardening-and-host-bootstrap
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260905
dateUpdated: 20260905
reviewedSha: 8e3ce104882c10877d896821a4bb8a794530b007
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "Task 6.4's grep-based success line is contradicted by scripts and tests it doesn't name"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:89-90"
  - id: F002
    severity: concern
    category: completability
    summary: "Task 9.1's `cron_weekly_backup.sh` invocation names 2 of an 8+ argument set, unlike its sibling drill tasks"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:271-278"
  - id: F003
    severity: note
    category: traceability
    summary: "Task 9.1's success line implicitly claims all of success criterion 8, but the guard-refusal half is proven in file 1"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:277-278"
  - id: F004
    severity: note
    category: traceability
    summary: "Two stale cross-references survive from the pre-remediation draft"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:136,150-152"
  - id: F005
    severity: pass
    category: task-quality
    summary: "All previously-flagged CONCERNS are resolved in the current file"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:169-207,297-310,339-357"
  - id: F006
    severity: pass
    category: coverage
    summary: "Success criteria 1–13 each trace to a named verifying task across the two files"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:169-357"
  - id: F007
    severity: pass
    category: task-quality
    summary: "Sequencing, test-with pairing, and commit distribution"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:59-93,241-253"
  - id: F008
    severity: pass
    category: test-coverage
    summary: "No NFR restated by the parent slice, so no load-test or CI-gating task is required"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md"
---

# Review: tasks — slice 920

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] Task 6.4's grep-based success line is contradicted by scripts and tests it doesn't name

Task 6.4's success line requires `grep -rn MT_BACKUP_RESTIC_PASSWORD` to find "the script, the README, the env example, and the runbooks only." But the key is grep'd by at least two scripts, not one: `scripts/cron_system_backup.sh` (Task 6.2, this file) and `deploy/setup-backup.sh` step 7 (file 1, Task 3.6 — "`MT_BACKUP_RESTIC_PASSWORD` missing from the env file is `MISSING` and blocks this step only," per design D1). It will also appear in `test/unit/test_backup_scripts.py` or similar, since Task 6.3 requires asserting the "missing password" error names the key literally. A junior implementer following this success line verbatim would see extra grep hits and either report a false failure or — worse — try to scrub the key out of `setup-backup.sh` or the test to force the count to match, breaking Task 3.6's functionality. Fix: widen the expected set to name both scripts and the test file, or replace the exact-match grep with a non-absence check against `deploy/manta-trading.env.example`'s uncommented lines (as the original reviewer already suggested for this task, but the fix wasn't applied in the current file).

### [CONCERN] Task 9.1's `cron_weekly_backup.sh` invocation names 2 of an 8+ argument set, unlike its sibling drill tasks

Task 9.1's second bullet says to run `cron_weekly_backup.sh` "with the real `--health-flag` path and a deliberately nonexistent `--env-file`," naming only two arguments. Per file 1 Task 5.4, the script's full required set is the inherited `cron_weekly_base.sh` arguments (`--env-file --base-dir --wal-dir --keep-days --health-flag`, confirmed against the live `scripts/cron_weekly_base.sh`) plus `--remote-wal`, `--lock`, and `--armed <path>` (the D1/D4 arm-file gate). This codebase's convention is "no defaults... refuses ambient guesses" (CLAUDE.md, and D1's explicit "no ambient guesses" rule for `setup-backup.sh`), so a script built to that discipline should reject an invocation missing `--base-dir`, `--wal-dir`, `--keep-days`, `--remote-wal`, `--lock`, or `--armed` with a usage error *before* reaching the flag-check logic the drill is trying to exercise. That would defeat the drill's stated purpose ("it must get past the flag check and fail on the missing env file — that error is the evidence the flag did not gate it"): the observed failure would be a generic missing-argument error, not evidence about flag gating. Tasks 9.4 and 9.5 avoid this ambiguity by saying "with the cron.d arguments" (the full rendered set); Task 9.1 should say the same, e.g. "with the cron.d arguments, but a nonexistent `--env-file`."

### [NOTE] Task 9.1's success line implicitly claims all of success criterion 8, but the guard-refusal half is proven in file 1

Criterion 8 has two parts: the six alarms firing/clearing (this task), and "the weekly reconcile's guards have been observed refusing (empty scratch `--wal-dir`) before any `rclone sync`, deleting nothing offsite" — actually demonstrated in file 1's Task 5.6 (scratch-prefix rehearsal against real B2: "empty the local dir and re-run — refused, offsite unchanged"). Coverage is complete across the two files, but Task 9.1 cites "success criterion 8" without qualification, which reads as a full claim. A one-line pointer to Task 5.6 would keep a later auditor from concluding the guard-refusal proof is missing.

### [NOTE] Two stale cross-references survive from the pre-remediation draft

Task 7.3 still asks for "an acceptance-test section with placeholders for the Section 9 run," but the bootstrap acceptance run is Task 10.1 in Section 10 — Section 9 is the drills section. Separately, Task 7.4 says to file the `install-production.sh --ref` defect "to the issue tracker" without naming one; there is no `project-documents/user/issues/` directory in this repo, only the GitHub remote. Neither blocks completion (both are inferable from context), but naming `gh issue create` explicitly and correcting "Section 9" to "Section 10" would remove the ambiguity, consistent with this project's stated aversion to fuzzy handoffs.

### [PASS] All previously-flagged CONCERNS are resolved in the current file

Comparing against the prior review (`920-review.tasks.backup-hardening-and-host-bootstrap.part-2.md`, reviewedSha `aabaf1d`) and the working tree at `ff8cfa7`: Task 8.2's idempotence check now correctly separates "zero `APPLIED` lines" from "every item `OK`," with the latter deferred to after the crontab/postgresql.conf cleanup bullets and finally closed in Task 9.4 via the `RECONCILE-ARMED` gate — which also resolves the unwatched-first-reconcile risk, since the destructive sync/delete step only runs once the operator touches the arm file by hand in Task 9.4. Success criterion 11 (timeshift) now has an explicit pre-cutover baseline (Task 8.1) and post-cutover comparison (Task 8.3). Task 10.1's hammerhead teardown now copies/restores `timeshift.json` and notes the ACL is removed with the archive root. Tasks 7.1/7.2 now correctly point the alarm-table consistency test at `check_backup_health.sh`'s ten-name class array (file 1, Task 1.3) instead of the four-name inherited script. Task 10.1's acceptance run now checks `check_backup_health.sh` (ten checks) rather than the four-check `check_archive_health.sh`, so the bootstrap acceptance test actually exercises the new alarms as D10 intends.

### [PASS] Success criteria 1–13 each trace to a named verifying task across the two files

1→8.2/9.4; 2→8.3; 3,4→8.3; 5→9.2; 6→8.4+9.4 (interval-uniqueness half in file 1 Task 3.4); 7→9.3; 8→9.1(+file 1 Task 5.6); 9→9.4; 10→8.4; 11→8.1+8.3; 12→9.5; 13→7.3+10.1. No task in Sections 6–10 fails to trace to a design decision (D6–D11) or a numbered criterion; I found no scope creep beyond the design's own Implementation Notes (e.g., Task 7.4's issue-tracker entry is explicitly sanctioned by the design's out-of-scope list).

### [PASS] Sequencing, test-with pairing, and commit distribution

Implementation tasks are immediately followed by their tests (6.2→6.3, 7.1→7.2), Task 8.6 (deleting the superseded 915 glue) is explicitly gated on Task 8.4's cron-driven evidence rather than run eagerly, and no task exceeds effort 3. Commit checkpoints land at the end of every section (6.5, 7.5, 8.5, 9.6, 10.2) plus an extra one for the deletion step (8.6), matching this project's per-section checkpoint convention. No task waits on a calendar in the vetoed sense — Task 9.4 hand-runs the weekly job instead of waiting for Sunday, and Task 8.4's waits are bounded (≤30 min, ≤60 min) with the reason stated inline.

### [PASS] No NFR restated by the parent slice, so no load-test or CI-gating task is required

The slice states no throughput/latency NFR; its measured numbers (16 GiB/day WAL, ~40 min/day uplink, the ≥1.5× compression gate) are capacity-planning inputs and a one-time go/no-go measurement (file 1, Task 2.1), not a repeatable NFR needing a `test/load/` task or CI gate. `test/load/` exists in this repo and is correctly untouched by this breakdown. This project's only CI workflow (`.github/workflows/ci.yml`) is a tag-triggered PyPI publish job with no test stage, so the absence of a CI-wiring task here is consistent with existing project state, not a gap this slice introduces.
