---
docType: review
layer: project
reviewType: tasks
slice: backup-hardening-and-host-bootstrap
project: trading-data
verdict: FAIL
sourceDocument: project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-1.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260905
dateUpdated: 20260905
reviewedSha: 0fa2b51a74f69909dbc244b4dc4892a280e05ccf
findings:
  - id: F001
    severity: fail
    category: sequencing
    summary: "Merging file 1 to `main` breaks the live cron lines before the cutover replaces them"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:158-162"
  - id: F002
    severity: concern
    category: coverage-gap
    summary: "Success criterion 1's idempotence half is never executed"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:184-185"
  - id: F003
    severity: concern
    category: verification-gap
    summary: "Nothing observes the cron.d entries actually firing under cron"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:191-198"
  - id: F004
    severity: concern
    category: sequencing
    summary: "D3's \"four things change together, in one commit\" rule is split across two section commits"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-1.md:274-276"
  - id: F005
    severity: concern
    category: test-coverage
    summary: "Segment-name arithmetic has no independent test; the integration test is circular"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-1.md:141-148"
  - id: F006
    severity: concern
    category: ambiguity
    summary: "Section 3 success criteria read as instructions to apply changes to production before the cutover"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-1.md:221-224"
  - id: F007
    severity: note
    category: task-sizing
    summary: "Task 5.3 is the one task that should probably be split"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-1.md:349-368"
  - id: F008
    severity: note
    category: coverage-gap
    summary: "Criterion 2's \"sourced from `postgresql.auto.conf`\" is inferred, not asserted"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-1.md:210-224"
  - id: F009
    severity: pass
    category: traceability
    summary: "All 13 success criteria trace to tasks; no scope creep found"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:560-620"
  - id: F010
    severity: pass
    category: process
    summary: "Commit checkpoints are distributed, and tests mostly follow their implementation"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-1.md:152-155"
  - id: F011
    severity: note
    category: nfr
    summary: "No NFR is restated by this slice, so no load test or CI gating task is required"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md"
---

# Review: tasks — slice 920

**Verdict:** FAIL
**Model:** claude-opus-5

## Findings

### [FAIL] Merging file 1 to `main` breaks the live cron lines before the cutover replaces them

Section 8's preamble states "All of file 1 and Sections 6–7 are merged to `main` and the host checkout is on `main` before this section starts (ordinary workflow, not a task)". File 1's Context Summary correctly identifies that the host checkout at `/home/manta/source/repos/manta/trading-data` *is what cron runs* — but applies that constraint only to the worktree, not to the merge.

The live user crontab today is:

```
*/30 … archive_health_cron.sh --env-file … --pgdata … --flag … --log … >/dev/null 2>&1
0 3 * * 0 … cron_weekly_base.sh --env-file … --base-dir … --wal-dir … --keep-days 7 --health-flag …
```

Task 1.6 makes `--wal-dir`, `--stamp`, `--stale-after`, `--system-stamp`, `--base-dir`, `--stale-flag` **required** on `archive_health_cron.sh`; Task 5.3 makes `--remote-wal` and `--lock` **required** on `cron_weekly_base.sh`. Verified in `scripts/archive_health_cron.sh:33-37` and `scripts/cron_weekly_base.sh:31-35`: a missing required argument exits 2 *before* the log line is appended and *before* any flag is written.

Failure scenario: file 1 merges Friday; the cutover (Task 8.2) happens Monday. From the next half-hour onward the health check exits 2, its output is discarded by the crontab's `>/dev/null 2>&1`, no log line is written, and `ARCHIVE-BROKEN` is neither raised nor cleared — the archive is unmonitored and nothing says so. This is precisely the silent-failure class the slice was written to eliminate. On Sunday 03:00 `cron_weekly_base.sh` exits 2 on `--remote-wal`, so the weekly base backup is skipped; `weekly_base_stale` cannot report it because the health check is also dead.

Remedy: the breakage window must become an explicit, bounded task — e.g. merge and cutover in the same session as an ordered task pair, or a pre-merge task in Section 8 that installs `/etc/cron.d/manta-trading-backup` and removes the user lines *before* the new script versions land in the host checkout. "Ordinary workflow, not a task" is exactly what makes this invisible.

### [CONCERN] Success criterion 1's idempotence half is never executed

Criterion 1 has two parts: the script runs to completion **and a second run changes nothing (every step reports "already")**, plus `--check` exits 0. Task 8.2's success line runs the script once and then `--check`, and claims "Success criterion 1". No task performs the second *apply* run that the walkthrough's step 1 spells out (`sudo deploy/setup-backup.sh <same args>` a second time). A `--check` pass does not prove apply-mode idempotence: a step that unconditionally re-runs `setfacl`, re-renders the cron.d file, or re-issues `ALTER SYSTEM` would still leave `--check` green. Add the second apply run and the "already"/`OK` expectation to Task 8.2.

Related vocabulary gap: Task 3.1 (file 1:193-196) defines the report tokens as `OK|DRIFT|MISSING` only; the criterion's "already" wording has no counterpart in the script contract, so a junior implementer has no defined output to assert against.

### [CONCERN] Nothing observes the cron.d entries actually firing under cron

Every post-cutover check invokes the scripts *by hand* "with the cron.d arguments". Task 8.3 offers "Wait for the next `:30` health run **or** invoke `archive_health_cron.sh` by hand", and the `or` makes the cron-driven observation optional; Tasks 9.4 and 9.5 both run their jobs by hand. `/etc/cron.d` is a brand-new delivery mechanism for this host (D7 replaces the user crontab), and its classic failure modes are exactly the ones hand-invocation cannot catch: the per-line user field, cron's minimal `PATH` (these scripts call `rclone`, `restic`, `flock`, `zstd`, `setfacl`, `jq`, `pg_archivecleanup`), and a missing trailing newline in the rendered file.

Failure scenario: cutover completes, `--check` is green, every drill passes by hand, and the hourly `rclone` push never runs under cron because `rclone` is not on cron's `PATH`. `offsite_wal_stale` fires three hours later into `BACKUP-STALE` — a file nobody is told to `ls` until the runbook says so — and the just-signed-off slice ships with the offsite tier dead. Criterion 6's "the offsite stamp is younger than one interval **during normal operation**" is the criterion this misses: add a task that waits for one real cron-driven push and one real `:30` health run and asserts the stamp age and log line.

### [CONCERN] D3's "four things change together, in one commit" rule is split across two section commits

D3 states the rule and its reason plainly: `archive_command`, `restore_command`, the prune's `-x .zst`, and the health check's size expectations "change together, in one commit, because a restore that finds `.zst` files with a `cp`-shaped `restore_command` fails at the worst moment". The breakdown puts `ARCHIVE_COMMAND` in Section 3's commit (Task 3.8) and the prune `-x` plus the runbook `restore_command` in Section 4's commit (Task 4.5).

In practice deployment is deferred to Section 8, so the exposure is small — but the task file never says so, and the host checkout tracks `main` (see the FAIL above), which is how an intermediate commit becomes an effective state. Either merge Sections 3 and 4 into a single checkpoint for the four coupled pieces, or state explicitly in Section 4's preamble why the split is safe (nothing is applied to the host until Task 8.2) so the deviation is a recorded decision rather than a drift from the design.

### [CONCERN] Segment-name arithmetic has no independent test; the integration test is circular

The slice's Implementation Notes call this out directly: "Unit coverage for the health check's segment-name arithmetic via a bats-style fixture directory is worth one task." Task 1.3 implements "next segment name" derivation (`last_archived_wal` + 1, or `last_failed_wal` when failing) — arithmetic with real edge cases (log-file rollover at segment `FF`, timeline prefix, zero padding). Task 1.7's integration test then plants "a short file at the next segment name (**compute from the same query the script uses**)".

Failure scenario: the SQL expression rolls `000000010000121700000 0FF` to `…12170000000100` instead of `…1218 00000000`. The test computes the same wrong name, plants a file there, sees `FAIL archive_wedged`, and passes — while in production the wedge check silently examines a name PostgreSQL will never write, and `archive_wedged` never fires. Add the fixture-based unit task the design asked for, with expected names written as literals independent of the script.

### [CONCERN] Section 3 success criteria read as instructions to apply changes to production before the cutover

Task 3.3's success: "on manta9000 `--check` shows `archive_command` as `DRIFT` (hand form → D2 form) **until apply; after apply, `OK`**". Task 3.5's success (file 1:244-246): "`--check` on manta9000 reports `DRIFT count_weekly 2 3` before apply and **`OK` after**". Both are Section 3 tasks, executed from the worktree while the host is meant to stay untouched until Section 8 — and Task 8.1 (file 2:164-171) explicitly *expects* `DRIFT` for `archive_command` and `count_weekly` at pre-cutover time.

A junior implementer following Task 3.5 literally will run the script in apply mode against `/etc/timeshift/timeshift.json` and `ALTER SYSTEM` on the production cluster during Section 3, then find Task 8.1's expected report no longer matches and be told to "investigate before Task 8.2". Rewrite both success lines to be `--check`-only against production plus apply-mode verification against a scratch `--backup-root`/test cluster, as Task 3.2 already does correctly ("`MISSING`/`DRIFT` on a scratch root").

### [NOTE] Task 5.3 is the one task that should probably be split

Effort 3 covering: two new required arguments, a six-step reordering of the weekly job, reuse of `sync_wal_offsite.sh`, `rclone check` abort, consuming the prune's `PRUNED` line, four independent guards, `--max-delete` arithmetic, offsite `base/<date>` deletion with logging, and a final verify. The four guards (`mountpoint`, non-empty, `last_archived_wal` present, oldest manifest's start segment present) are separable, individually testable, and are the part of the job where a mistake deletes the offsite chain. Splitting into "guards + refusal contract" and "reconcile ordering + guarded sync" would let Task 5.4's guard tests land against a smaller surface. Every other task is appropriately sized; none are too granular.

### [NOTE] Criterion 2's "sourced from `postgresql.auto.conf`" is inferred, not asserted

Task 3.3 compares `pg_settings.setting` and separately reports `DRIFT` if `postgresql.conf` still has an uncommented `archive_command`. Criterion 2 additionally requires all three settings be *sourced from* `postgresql.auto.conf` — one `pg_settings.sourcefile` assertion. Cheap to add to Task 3.3's check and to Task 8.3's evidence; without it, an `include`d conf fragment or a `postgresql.conf` line the grep pattern misses could hold the effective value while the value comparison still passes.

### [PASS] All 13 success criteria trace to tasks; no scope creep found

Mapping: 1→8.2 (with the idempotence gap above); 2→3.3+8.2+8.3; 3→8.3; 4→3.2+8.3; 5→9.2; 6→3.4+9.4; 7→9.3; 8→9.1+5.4+5.5; 9→9.4; 10→3.4+3.6+8.2; 11→3.5; 12→9.5; 13→7.3+10.1. Every task traces back: the two that are not named by a criterion — Task 2.1 (compression measurement) and Task 5.5 (scratch-prefix reconcile rehearsal) — are required by D3's go/no-go and by criterion 8's "guards have been observed refusing … deleting nothing offsite" respectively. Task 7.4's issue-tracker entry for the `install-production.sh --ref` defect is recording, not touching the out-of-scope script. No circular dependencies; the section order matches D11's steps 1→7.

### [PASS] Commit checkpoints are distributed, and tests mostly follow their implementation

Ten checkpoints, one per section (1.8, 2.2, 3.8, 4.5, 5.6, 6.5, 7.5, 8.4, 9.6, 10.2) — matching the project's per-section commit convention, none batched at the end. Test-with pattern holds for Tasks 4.1/4.2, 4.3/4.4, 5.1/5.2, 5.3/5.4, 6.2/6.3, 7.1/7.2. Sections 1 and 3 batch their tests (1.7 after six implementation tasks; 3.7 after six steps of one script) — acceptable here since both are single-script sections whose steps share one argument parser, but per-task tests would tighten the feedback loop on Tasks 1.3 and 3.4 specifically.

### [NOTE] No NFR is restated by this slice, so no load test or CI gating task is required

Confirmed by search: the slice design contains no NFR restatement or performance requirement. Its quantitative content (D4a's retention capacity table, the 40 Mbps uplink and B2 cost estimates, `zstd -T2` CPU) is sizing rationale, not a threshold anything is required to meet. The `test/load/` requirement and its CI-gating corollary therefore do not apply to slice 920 — the absence of a load-test task here is correct, not a gap.
