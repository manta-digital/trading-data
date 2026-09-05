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
reviewedSha: 0fa2b51a74f69909dbc244b4dc4892a280e05ccf
findings:
  - id: F001
    severity: concern
    category: test-coverage
    summary: "No task observes a cron.d entry actually executing from cron"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:191-198"
  - id: F002
    severity: concern
    category: correctness
    summary: "Task 8.1's expected pre-cutover report contradicts the measured host state"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:166-171"
  - id: F003
    severity: concern
    category: test-coverage
    summary: "Criterion 1's second-run idempotence is claimed but never executed"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:185-186"
  - id: F004
    severity: concern
    category: correctness
    summary: "Task 6.1 points its size measurement at a repo that cannot exist yet, via a wrong task reference"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:47-56"
  - id: F005
    severity: concern
    category: test-coverage
    summary: "Criterion 2's `postgresql.auto.conf` sourcing has no verifying step"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:188-190"
  - id: F006
    severity: note
    category: completeness
    summary: "The B2 lifecycle-rule backstop is documented but never set"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:107-109"
  - id: F007
    severity: note
    category: correctness
    summary: "Task 9.5 diffs a restored copy of an actively-edited tree against live"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:258-262"
  - id: F008
    severity: note
    category: scope
    summary: "Task 7.4's issue-tracker entry is outside the slice's stated scope"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:139-152"
  - id: F009
    severity: pass
    category: completeness
    summary: "Every success criterion traces to a task"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:600-660"
  - id: F010
    severity: pass
    category: sequencing
    summary: "Checkpoints distributed, test tasks adjacent, no wait-blocked work"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:95-97"
  - id: F011
    severity: pass
    category: test-coverage
    summary: "No restated NFR, so no load test or CI gate is owed"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:1-120"
---

# Review: tasks — slice 920

**Verdict:** CONCERNS
**Model:** claude-opus-5

## Findings

### [CONCERN] No task observes a cron.d entry actually executing from cron

The cutover replaces three working user-crontab lines with six brand-new `/etc/cron.d/manta-trading-backup` entries (design D7). Task 8.3 says "Wait for the next `:30` health run **or** invoke `archive_health_cron.sh` by hand with the cron.d arguments" — the `or` makes the by-hand path sufficient, and every other new entry (`sync_wal_offsite.sh`, `cron_system_backup.sh`, the monthly `restic check`) is likewise only ever run by hand in Tasks 8.3, 9.4, and 9.5.

Failure scenario: the rendered cron.d file is missing a trailing newline, or has a wrong user field, or a `%` in the `archive_command`-adjacent arguments is unescaped — cron silently ignores the file or the line. Every by-hand run in Sections 8–9 passes, `--check` is green (Task 3.4 compares rendered content, not execution), and the slice closes. The alarms that would catch it (`offsite_wal_stale`, `system_backup_stale`) are themselves cron-driven, so nothing fires. Production is back to the day-one failure mode this slice exists to remove: a scheduled backup job that never runs and never says so.

Fix: make the `:30` health run a required observation, not an alternative — after cutover, `grep CRON /var/log/syslog` (or `journalctl -t CRON`) for each of the six entries firing, and confirm the offsite stamp mtime advanced from a run the operator did not launch. The `:30` boundary is at most 30 minutes away, so this stays measurable in-session and does not become a calendar wait.

### [CONCERN] Task 8.1's expected pre-cutover report contradicts the measured host state

Task 8.1 expects `DRIFT` for `archive_command`, cron.d, `count_weekly`, restic repo, and leftover crontab lines; and `OK` for "ACL, **directories**, `wal_compression`, `archive_mode`". Its success criterion is "the report matches the expectation above; anything else is investigated before Task 8.2."

Two items in the script's step list will not match. `deploy/setup-backup.sh` step 1 checks the `restic` package (file 1, Task 3.2) — `dpkg -s restic` on manta9000 today reports not installed, so that item is `MISSING`, and Task 8.1 does not list it. Step 2 requires `base/ wal/ metadata/ system/` under `--backup-root`; `ls -d /data/backup/*` shows `base`, `wal`, `metadata` only — `system/` does not exist, so "directories" is not `OK` either. The slice's own baseline table already states "restic not installed."

Failure scenario: the operator runs Task 8.1, sees two unexpected non-OK items, and per the task's own success wording must stop and investigate before the PM cutover — a false stall on the one task whose purpose is to confirm the host is in the expected state. Amend the expectation to `MISSING restic` and `MISSING system/`.

### [CONCERN] Criterion 1's second-run idempotence is claimed but never executed

Success criterion 1 has two clauses: the script runs to completion **and a second run changes nothing (every step reports "already")**, plus `--check` exits 0. Walkthrough step 1 accordingly runs apply, apply again, then `--check`. Task 8.2 runs apply exactly once and then `--check`, and its success line asserts "Success criterion 1."

Failure scenario: a step is written apply-always rather than check-then-act — e.g. step 5 rewrites `/etc/cron.d/manta-trading-backup` unconditionally, or step 4 issues `ALTER SYSTEM SET` on every run. `--check` still reports `OK` (the end state is correct), so the missing idempotence is invisible; the defect surfaces later as a bootstrap-runbook re-run that bumps `pg_settings` or rewrites a file the operator is mid-edit on. Add the second apply run to Task 8.2 between the apply and the `--check`.

### [CONCERN] Task 6.1 points its size measurement at a repo that cannot exist yet, via a wrong task reference

Task 6.1 says to measure the snapshot size with `restic backup --dry-run` against the "real repo after **Task 6.3** init". Task 6.3 is the restic *test* task; nothing in it initializes a repository. The repo is initialized by `setup-backup.sh` step 7 (file 1, Task 3.6), which only runs against the real host at cutover — Task 8.2 — and requires `MT_BACKUP_RESTIC_PASSWORD`, which the PM does not place in `.env` until that same task. Section 6 therefore cannot take the `--dry-run` path at all.

Failure scenario: the implementer follows the citation, finds no repo, and either fabricates a number or blocks; downstream, the PM's `Pictures`/`ai` include decision (67 G + 60 G) is made on the `du` fallback while the task's own success line says "D9 carries the measured size." Correct the reference to the `du`-with-excludes path as the primary method for Section 6, and if a true `--dry-run` number is wanted, add it as a bullet on Task 9.5, where the repo exists.

### [CONCERN] Criterion 2's `postgresql.auto.conf` sourcing has no verifying step

Criterion 2 requires the three settings to show the expected values **"all sourced from `postgresql.auto.conf`"** and the hand-set `postgresql.conf` line gone. Task 8.3's first bullet cites "Success criteria 2, 3, 4" but its checks (`pg_switch_wal()`, a `.zst` lands, no `.tmp`, `last_archived_wal` advanced) demonstrate only criterion 3. The value half of criterion 2 is covered indirectly by Task 8.2's green `--check` (file 1, Task 3.3 compares `pg_settings.setting` and flags an uncommented `postgresql.conf` line as DRIFT), but nothing anywhere queries `pg_settings.source`.

Failure scenario: the PM comments out rather than deletes the hand-set `postgresql.conf` line and a later hand edit reinstates it, or an include file elsewhere in `/etc/postgresql/17/main/conf.d/` sets `archive_command`. The effective value still matches the constant, `--check` is green, and the file that "wins" is not the one the slice claims. One `SELECT name, setting, source FROM pg_settings WHERE name IN (...)` in Task 8.3 closes it — and the criterion citations on that bullet should be corrected to 3 alone.

### [NOTE] The B2 lifecycle-rule backstop is documented but never set

D4 specifies a B2 bucket lifecycle rule (delete versions older than 30 days on the `wal/` and `base/` prefixes) as "a server-side backstop needing no host at all" for the case where the host is gone for weeks. Task 7.1 writes the console steps into runbook 200; no task actually sets the rule, and no success criterion covers it. A backstop that exists only as runbook prose is not a backstop. Consider a `[PM]` bullet on Task 8.2 or 9.4 to set it and paste the resulting rule back into the runbook.

### [NOTE] Task 9.5 diffs a restored copy of an actively-edited tree against live

The chosen home subtree is `.../trading-data/project-documents` — the tree these very tasks write into (runbook drill records, task checkboxes, the design's status). The snapshot and the `diff -r` happen in one task, so the window is short, but any checklist update or drill-table edit between them turns criterion 12 into a spurious failure. A stable subtree (or `git stash`-free read-only path such as `~/.ssh` metadata or `/home/manta/source/repos/manta/trading-data/deploy`) would prove the same thing without the race.

### [NOTE] Task 7.4's issue-tracker entry is outside the slice's stated scope

The slice explicitly places the `install-production.sh --ref <branch>` origin-resolution defect out of scope. Filing it as an issue is a reasonable, cheap way to not lose it, and it traces to no success criterion — flagged only so it is a deliberate addition rather than drift. No action needed if intentional.

### [PASS] Every success criterion traces to a task

Criteria 1–4 → Tasks 8.2/8.3 (with the gaps noted above); 5 → 9.2; 6, 9 → 9.4 (plus file 1's Task 3.4 for the single-interval-constant clause); 7 → 9.3; 8 → 9.1 (both flag behaviours, plus file 1's Task 5.5 for the guard-refusal clause); 10 → 8.2 via `--check` steps 5 and 8; 11 → 8.2 via step 6; 12 → 9.5, including all four restore targets; 13 → 7.3 + 10.1. No task in file 2 lacks a criterion or design decision to trace back to.

### [PASS] Checkpoints distributed, test tasks adjacent, no wait-blocked work

Commits land at 6.5, 7.5, 8.4, 9.6, and 10.2 — one per section, not batched. Tests immediately follow their implementation (6.2 → 6.3; 7.1 → 7.2), matching file 1's pattern. Every drill plants its own fault or ages its own stamp, and Task 10.1 carries an in-session fallback (fresh VM) if the PM declines hammerhead, so nothing waits on a calendar. Task 8.2 is a single script invocation plus a printed report with only operator-only items as checklist bullets, per the standing PM-host-step rule.

### [PASS] No restated NFR, so no load test or CI gate is owed

The slice states measured host quantities (16 GiB/day WAL, uplink throughput, zstd CPU) as design inputs, not as non-functional requirements to be enforced. It restates no NFR from a parent, so the `test/load/` + CI-gating requirement does not apply here; the acceptance instruments are `setup-backup.sh --check` and `check_archive_health.sh`, both of which have tasks.
