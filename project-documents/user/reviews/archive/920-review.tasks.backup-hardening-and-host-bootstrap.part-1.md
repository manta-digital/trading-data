---
docType: review
layer: project
reviewType: tasks
slice: backup-hardening-and-host-bootstrap
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-1.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260905
dateUpdated: 20260905
reviewedSha: aabaf1d9127dd5f4157ae70a2da9cf7417d37bd8
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "Wrapper spec omits the inner check's non-zero exit; the six new checks never run when a legacy check fails"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-1.md:103"
  - id: F002
    severity: concern
    category: sequencing
    summary: "Apply mode's first real execution is the production cutover"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:181"
  - id: F003
    severity: concern
    category: correctness
    summary: "Task 7.1's completeness check greps the wrong script, and Task 7.2 cites the wrong task"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:111"
  - id: F004
    severity: concern
    category: test-coverage
    summary: "Success criterion 11's \"device UUID is unchanged\" has no verifying task"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:601"
  - id: F005
    severity: note
    category: correctness
    summary: "The reconcile's `rclone sync` step does not specify `--exclude '*.tmp'`"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-1.md:411"
  - id: F006
    severity: note
    category: test-coverage
    summary: "Two of the six cron.d entries are never observed firing before the 915 lines are deleted"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:210"
  - id: F007
    severity: note
    category: task-scoping
    summary: "`deploy/lib/timeshift_merge.sh` is introduced in a test task rather than its implementation task"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-1.md:271"
  - id: F008
    severity: note
    category: scope-creep
    summary: "Two small items outside the slice's stated scope"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:139"
  - id: F009
    severity: pass
    category: requirements-coverage
    summary: "All 13 success criteria trace to at least one task"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:561-620"
  - id: F010
    severity: pass
    category: sequencing
    summary: "Sequencing respects D11, tests sit with their implementation, and commits are distributed"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-1.md:70"
  - id: F011
    severity: pass
    category: test-coverage
    summary: "No load-test task is required, and CI gating is not applicable to this repo"
    location: ".github/workflows/ci.yml:1-10"
---

# Review: tasks — slice 920

**Verdict:** CONCERNS
**Model:** claude-opus-5

## Findings

### [CONCERN] Wrapper spec omits the inner check's non-zero exit; the six new checks never run when a legacy check fails

Task 1.3 says only "Runs `check_archive_health.sh --db-url --pgdata` first and keeps its output lines verbatim, then appends its own checks." `scripts/check_archive_health.sh:100-104` exits 1 whenever any of its four checks fails, and the 915 scripts (which Task 1.3 tells the implementer to follow) all use `set -euo pipefail`. A junior implementer following the task literally produces a wrapper that aborts on the inner script's exit 1, so `prune_permission`, `archive_wedged`, `archive_tmp_leftover`, `offsite_wal_stale`, `system_backup_stale`, `weekly_base_stale`, and the `FLAGS` summary line are never evaluated in exactly the state where they matter most — an already-unhealthy archive. Task 1.7's "an uncheckable run writes the archive flag" then masks it: `BACKUP-STALE` conditions would be permanently invisible whenever `ARCHIVE-BROKEN` is set. Task 1.8's integration test (which runs against the `archive_mode=off` test cluster) would catch this, but the acceptance path should not depend on a test discovering a requirement the task never stated. Add an explicit bullet: capture the inner script's exit code (`set +e` around the call or `|| inner_rc=$?`), pass its lines through, fold its failures into the `archive` class, and always emit the `FLAGS` line.

### [CONCERN] Apply mode's first real execution is the production cutover

Every task in Section 3 restricts itself to `--check` on manta9000 (Tasks 3.2, 3.3, 3.5 each say "check only; no apply before Section 8"), and Task 3.7 exercises apply only with a stubbed `psql` and a copy of `timeshift.json`. Task 10.1 — the clean-state run that actually exercises apply for steps 1–3, 5, 6, 7 end to end — happens *after* the production cutover. So Task 8.2 is the first time `setfacl`, the cron.d render-and-install, the `jq` write-back to the live `/etc/timeshift/timeshift.json`, and `restic init` run for real, on production, under PM hands. This traces to D11's ordering (bootstrap last), so it is not a design deviation, but it is cheap to de-risk: add a task after 3.6 that runs the script in *apply* mode against a throwaway `--backup-root` (steps 1–3 and 7 with a scratch restic prefix; cron.d rendered to a temp path) and asserts a second run prints zero `APPLIED` lines. That also pre-validates success criterion 1's idempotence half before the PM depends on it.

### [CONCERN] Task 7.1's completeness check greps the wrong script, and Task 7.2 cites the wrong task

Task 7.1's success criterion is "every named failure in `check_archive_health.sh` appears in the runbook table". That script is deliberately never modified (file 1's Context Summary) and emits only the four 915 names; the six new names live in `check_backup_health.sh`. As written, the test would pass with none of the six new alarms documented — which is precisely the alarm table success criterion 8 depends on. Task 7.2 compounds this by saying the name set is parsed "from Task 1.5", while the authoritative single array mapping all ten names to their class is defined in Task 1.3. Fix both to name `scripts/check_backup_health.sh` and its class array (Task 1.3), and state that the expected set is all ten names.

### [CONCERN] Success criterion 11's "device UUID is unchanged" has no verifying task

Criterion 11 has three parts: `count_weekly = 2`, the four excludes, and `backup_device_uuid` unchanged. The first two are covered by Task 8.2's green `--check`. The third is not: Task 3.5 says the script "reports the device UUID as info", but Task 8.1's expected pre-cutover report does not list it and Task 8.2 does not compare it, so nothing on the host establishes that the `jq` write-back preserved it. Task 3.7's byte-identical-projection test covers the mechanism on a *copy*, not the live file. Add the UUID to Task 8.1's recorded pre-check report and a one-line before/after comparison to Task 8.3, alongside the settings and `getfacl` evidence.

### [NOTE] The reconcile's `rclone sync` step does not specify `--exclude '*.tmp'`

Task 5.4 specifies `--exclude '*.tmp'` on step 2's `rclone check` and Task 5.1 specifies it on the hourly `rclone copy`, but step 5's `rclone sync … --max-delete` carries no exclude. The sync would then mirror `%f.zst.tmp` partials and the `.prune-canary.tmp` (Task 1.4 creates it in `--wal-dir`) into `b2:$BUCKET/wal`, and the later `--one-way` check excludes `*.tmp` so the discrepancy stays invisible. Harmless to the restore chain, but it puts junk offsite and contradicts D5's stated "`rclone check` excludes it" reasoning. Add the same exclude to step 5 (and to step 6's check for symmetry).

### [NOTE] Two of the six cron.d entries are never observed firing before the 915 lines are deleted

Task 8.4 observes `backup_health_cron.sh` (`:00`/`:30`) and `sync_wal_offsite.sh` (hourly) executing from cron, then Task 8.6 deletes the 915 glue. The `cron_nightly_metadata.sh` (02:00), `cron_weekly_backup.sh` (Sunday 03:00), and `cron_system_backup.sh` (04:00) entries are only ever run by hand (Tasks 9.4, 9.5). A malformed user field or path in one of those three rendered lines would surface a day to a week after the predecessor was removed. The `RELOAD … no bad/error line` assertion in 8.4's first bullet is a partial guard. Consider adding a bullet to Task 8.4 asserting `crontab -T`-equivalent validation of every rendered line, or observing the 02:00 nightly entry firing before Task 8.6.

### [NOTE] `deploy/lib/timeshift_merge.sh` is introduced in a test task rather than its implementation task

The design's Implementation Notes list `deploy/lib/timeshift_merge.sh` as a new file. Task 3.5 (the implementation) does not mention it; the extraction appears only parenthetically inside Task 3.7's test bullet ("extracted into `deploy/lib/timeshift_merge.sh`, sourced by the script"). An implementer working Task 3.5 in isolation writes the merge inline and Task 3.7 then becomes a refactor plus a test. Move the file's creation into Task 3.5.

### [NOTE] Two small items outside the slice's stated scope

Task 7.4's third bullet files an issue for the `install-production.sh --ref` origin-resolution defect, which the slice explicitly places out of scope; Task 6.4 adds CHANGELOG entries not named in the slice. Both are recording-only, cost nothing, and prevent loss of a known defect — flagged for completeness, not for removal.

### [PASS] All 13 success criteria trace to at least one task

Mapping: 1 → Task 8.2; 2 → Tasks 8.2 (removes the hand-set `postgresql.conf` line), 8.3, 3.3 (`sourcefile` assertion); 3 → Task 8.3; 4 → Tasks 8.3, 1.4; 5 → Task 9.2; 6 → Tasks 8.4, 9.4, 3.4 (single-constant grep); 7 → Task 9.3; 8 → Tasks 9.1 and 5.6 (the empty-`--wal-dir` refusal half); 9 → Task 9.4; 10 → Tasks 3.4, 8.2, 8.6; 11 → Tasks 3.5, 8.2 (partial — see the device-UUID finding); 12 → Task 9.5; 13 → Tasks 7.3, 10.1. No task lacks a criterion or a design decision it traces to.

### [PASS] Sequencing respects D11, tests sit with their implementation, and commits are distributed

Order matches D11 exactly: read-only alarms (Section 1) → compression measurement (Section 2) → `setup-backup.sh` and archive path (Sections 3–4) → offsite (Section 5) → restic and runbooks (6–7) → cutover (8) → drills (9) → bootstrap acceptance (10). Dependencies hold in one direction: Task 1.1's `wal_segment_name.py` precedes its consumers (Tasks 1.5, 4.1, 5.3, 9.1); Task 2.1's go/no-go precedes every "if go" branch (Tasks 3.1, 4.1); Task 8.6's deletion is explicitly gated on Task 8.4; no cycles. Test-with pattern is honored (1.1→1.2, 1.3–1.7→1.8, 3.1–3.6→3.7, 4.1→4.2, 4.3→4.4, 5.1→5.2, 5.3/5.4→5.5, 6.2→6.3, 7.1→7.2). Checkpoint commits land at 1.9, 2.2, 3.8, 4.5, 5.7, 6.5, 7.5, 8.5, 8.6, 9.6, and 10.2 — one per section, none batched at the end. Task sizes are consistent (effort 1–3, largest being the two PITR drills and the hammerhead run, which are irreducible); no task needs splitting or merging. The worktree constraint that keeps the live crontab on `main` until cutover is stated once in file 1's Context Summary and honored throughout.

### [PASS] No load-test task is required, and CI gating is not applicable to this repo

The slice restates no application-level NFR of the kind covered by `test/load/` (which holds NFR suites for slices 146, 167, 169, 187). Its numeric budgets are host-operational — the ≥1.5× compression ratio, `zstd -T2` CPU, uplink throughput, first-snapshot size — and each has a measurement task (Task 2.1 for the ratio, Task 6.1 and 9.5 for restic size, Task 8.4 for first-push duration) with results recorded in the design and runbook. `.github/workflows/ci.yml` is publish-on-tag only and runs no test job, so there is no CI gate for a new tier to be wired into; the unit tests added by Tasks 1.2, 1.8, 3.7, 4.2, 4.4, 5.2, 5.5, 6.3, and 7.2 all land under `test/unit/` and are picked up by the existing `scripts/run_tests.py unit` tier with no allowlist change (they require no environment variables).
