---
docType: review
layer: project
reviewType: tasks
slice: backup-hardening-and-host-bootstrap
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260905
dateUpdated: 20260905
reviewedSha: aabaf1d9127dd5f4157ae70a2da9cf7417d37bd8
findings:
  - id: F001
    severity: concern
    category: sequencing
    summary: "Task 8.2's idempotence check asserts \"every item OK\" before the DRIFT sources are removed"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:188-197"
  - id: F002
    severity: concern
    category: sequencing
    summary: "cron.d installs the weekly reconcile on a schedule at cutover, before the drills — the first reconcile can fire unwatched"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:181-197"
  - id: F003
    severity: concern
    category: coverage-gap
    summary: "Success criterion 11 (timeshift) has no verification task; \"device UUID unchanged\" cannot be demonstrated"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:199-212"
  - id: F004
    severity: concern
    category: correctness
    summary: "Task 10.1's hammerhead teardown does not revert the timeshift merge or the ACL"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:323-332"
  - id: F005
    severity: concern
    category: traceability
    summary: "Tasks 7.1 and 7.2 point the alarm-table consistency test at the wrong script and the wrong task"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:111-118"
  - id: F006
    severity: note
    category: correctness
    summary: "Task 6.4's grep assertion is contradicted by file 1's Task 3.6 and by Task 6.3"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:89-90"
  - id: F007
    severity: note
    category: traceability
    summary: "Task 9.1 claims criterion 8 in full, but its reconcile-guard half is proven in file 1"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:262-263"
  - id: F008
    severity: note
    category: traceability
    summary: "Two stale cross-references"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:133"
  - id: F009
    severity: note
    category: coverage-gap
    summary: "Task 10.1's bootstrap acceptance runs the four-check script, not the ten-check wrapper"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:323-325"
  - id: F010
    severity: pass
    category: coverage
    summary: "Criteria 1–9, 12, and 13 each trace to a named verifying task"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:181-332"
  - id: F011
    severity: pass
    category: task-quality
    summary: "Task sizing, test-with pattern, and commit distribution"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:47-93"
  - id: F012
    severity: pass
    category: test-coverage
    summary: "No NFR restated by the parent slice, so no load-test or CI-gating task is required"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md"
---

# Review: tasks — slice 920

**Verdict:** CONCERNS
**Model:** claude-opus-5

## Findings

### [CONCERN] Task 8.2's idempotence check asserts "every item OK" before the DRIFT sources are removed

Task 8.2's third bullet tells the PM to re-run the script and expect "zero `APPLIED` lines, every item `OK`", but the fourth bullet — removing the three 915 user-crontab lines and the hand-set `archive_command` line from `postgresql.conf` — has not happened yet. Per slice D1, step 8 reports leftover crontab lines as `DRIFT` ("success criterion 1 cannot pass until the cutover step is done"), and file 1's Task 3.3 reports `DRIFT archive_command source` while the `postgresql.conf` line remains. The second run will therefore print at least two `DRIFT` lines and exit non-zero. A PM following this literally stops mid-cutover on an expected-but-undocumented failure. Split the assertion: second run = zero `APPLIED` lines (idempotence), with `DRIFT` on step 4's source and step 8 expected; move "every item OK" to the final success bullet only (where it already correctly lives at line 196).

### [CONCERN] cron.d installs the weekly reconcile on a schedule at cutover, before the drills — the first reconcile can fire unwatched

Task 8.2 installs `/etc/cron.d/manta-trading-backup`, which includes `cron_weekly_backup.sh` at `0 3 * * 0` (D7). The slice's Risks section requires "The first run is watched, not scheduled," and D11 orders the first offsite reconcile — the first destructive offsite action — after the local and B2 PITR drills. Nothing in Section 8 or 9 prevents the scheduled Sunday entry from firing between the cutover (8.2) and the watched hand-run (9.4). If the cutover session lands on a Saturday, the first `rclone sync --max-delete` against B2 runs unobserved and before criteria 5 and 7 are proven. Add an explicit guard to Task 8.2 or 9.4: either the weekly entry is commented out in the rendered cron.d file until Task 9.4 completes, or Task 9.4 is a precondition on the cutover session's day-of-week (the former is preferable — the latter is a calendar dependency).

### [CONCERN] Success criterion 11 (timeshift) has no verification task; "device UUID unchanged" cannot be demonstrated

Slice criterion 11 requires `/etc/timeshift/timeshift.json` to show `count_weekly = 2` and the four excludes, with `backup_device_uuid` unchanged. No task in either file cites criterion 11, and Section 8's evidence tasks (8.3 settings, 8.4 cron) do not touch timeshift. It is covered only implicitly by Task 8.2's `--check` exit 0. Crucially, "unchanged" is a before/after comparison and Task 8.1's expected pre-cutover report (line 176) records only `DRIFT count_weekly 2 3` — the live UUID is never captured, so after the merge there is no baseline to compare against. Add a bullet to Task 8.1 recording the pre-cutover `jq '.backup_device_uuid, .count_weekly, .exclude'` output, and to Task 8.3 (or a new 8.3b) asserting the post-cutover values against it, citing criterion 11. Criterion 10's "six entries" is likewise only implicit via `--check`'s byte-for-byte template comparison (file 1, Task 3.4) — a one-line `cat /etc/cron.d/manta-trading-backup` assertion in Task 8.4 would close it cheaply.

### [CONCERN] Task 10.1's hammerhead teardown does not revert the timeshift merge or the ACL

The acceptance run executes `setup-backup.sh` in apply mode on hammerhead, which per D1/D8 merges managed keys into `/etc/timeshift/timeshift.json` (`schedule_*`, `count_weekly=2`, the four excludes) and applies `setfacl` to the throwaway WAL directory. Teardown covers the archive root, cron.d file, PostgreSQL settings, scratch restic prefix, and the restic package — but not timeshift. The success line ("hammerhead's `pg_lsclusters` and settings match their pre-run values") does not cover it either, so the run silently and permanently rewrites the test host's snapshot policy. Add a pre-run copy of hammerhead's `timeshift.json` and a restore step in teardown (or, better, have the acceptance run skip step 6 on a host where timeshift is not under management and record that as a runbook 210 note).

### [CONCERN] Tasks 7.1 and 7.2 point the alarm-table consistency test at the wrong script and the wrong task

Task 7.1's success says "every named failure in `check_archive_health.sh` appears in the runbook table," and Task 7.2 says to parse "its case/array from Task 1.5." Both references are wrong given file 1's composition decision: `check_archive_health.sh` is the untouched 915 script emitting only four names; the ten-name set the runbook table must match lives in the wrapper `check_backup_health.sh`, in the class array defined by file 1's **Task 1.3** ("a single array mapping each of the ten check names to its class"), not Task 1.5 (which adds only `archive_wedged` and `archive_tmp_leftover`). As written, a junior implementer greps a four-name script and the test asserts the wrong set equality. Retarget both to `scripts/check_backup_health.sh` and Task 1.3's class array.

### [NOTE] Task 6.4's grep assertion is contradicted by file 1's Task 3.6 and by Task 6.3

The success line expects `grep -rn MT_BACKUP_RESTIC_PASSWORD` to find "the script, the README, the env example, and the runbooks only." But `deploy/setup-backup.sh` step 7 reads that key from the env file (file 1, Task 3.6), and Task 6.3's unit test asserts the missing-password path names it — so the grep will legitimately also hit a second script and at least one test file. Either widen the expected set or drop the "only" and assert non-absence from `deploy/manta-trading.env.example`'s uncommented lines instead.

### [NOTE] Task 9.1 claims criterion 8 in full, but its reconcile-guard half is proven in file 1

Criterion 8 has two halves: the six alarms firing and clearing, and "the weekly reconcile's guards have been observed refusing (empty scratch `--wal-dir`) before any `rclone sync`, deleting nothing offsite." Task 9.1 covers the six alarms and the two flag-gating behaviors; the guard-refusal half is actually covered by file 1's Task 5.6 (scratch-prefix rehearsal: "empty the local dir and re-run — refused, offsite unchanged"). Coverage is complete, but the citation is misleading — add a pointer to Task 5.6 so a later auditor does not read Task 9.1 as the whole of criterion 8.

### [NOTE] Two stale cross-references

Task 7.3 asks for "an acceptance-test section with placeholders for the Section 9 run," but the acceptance run is Task 10.1 in Section 10; Section 9 is the drills. Separately, Task 7.4 (line 147-149) says to file the `install-production.sh --ref` defect "to the issue tracker" without naming one — there is no `project-documents/user/issues/` directory, and the repo's only tracker is the GitHub remote (`manta-digital/trading-data`). Name it explicitly (e.g. `gh issue create`) so the step is unambiguously completable. Filing the issue is a small addition beyond the slice's Implementation Notes but is consistent with the out-of-scope list ("this slice does not touch that script") and is not scope creep.

### [NOTE] Task 10.1's bootstrap acceptance runs the four-check script, not the ten-check wrapper

End state names `check_archive_health.sh` PASS. This is faithful to slice D10, which uses the same name, but D10 predates the D5 amendment that moved the six new checks into `check_backup_health.sh`. As written, the replacement-host acceptance test validates only the four inherited 915 checks — the new alarms are never exercised on a host built from repo + runbook alone, which is the whole point of the test. Suggest `check_backup_health.sh` (with the throwaway paths) and a matching correction to runbook 210's final step in Task 7.3.

### [PASS] Criteria 1–9, 12, and 13 each trace to a named verifying task

Criterion 1 → 8.2; 2 → 8.3; 3 and 4 → 8.3; 5 → 9.2; 6 → 8.4 and 9.4 (with the "interval appears exactly once" half in file 1's Task 3.4); 7 → 9.3; 8 → 9.1 (+5.6); 9 → 9.4; 12 → 9.5; 13 → 10.1. No task in Section 6–10 fails to trace back to a decision (D6–D11) or a success criterion; I found no scope creep.

### [PASS] Task sizing, test-with pattern, and commit distribution

Implementation tasks are immediately followed by their tests (6.2→6.3, 7.1→7.2), each task carries a concrete success line with a runnable assertion, and no task exceeds effort 3. Commit checkpoints appear at the end of every section (6.5, 7.5, 8.5, 9.6) plus 8.6 and 10.2, matching the project's per-section checkpoint convention rather than batching at the end. No task waits on a calendar — Task 9.4 hand-runs the weekly job rather than waiting for Sunday, and Task 8.4's waits are bounded at 30 and 60 minutes with the reason stated.

### [PASS] No NFR restated by the parent slice, so no load-test or CI-gating task is required

The slice design states no performance or throughput NFR — its measured numbers (16 GiB/day WAL, ~40 min/day uplink, compression ratio ≥1.5×) are sizing inputs and a go/no-go gate, not restated non-functional requirements. `test/load/` exists in this repo but is correctly untouched by this breakdown; the D3 ratio measurement (file 1, Task 2.1) is a one-time host measurement, not a repeatable load test, and does not need CI gating.
