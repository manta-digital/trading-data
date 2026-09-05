---
docType: tasks
slice: backup-hardening-and-host-bootstrap
project: trading-data
lld: user/slices/920-slice.backup-hardening-and-host-bootstrap.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [915, 916]
interfaces: [917, 919]
projectState: >
  Continues 920-tasks.backup-hardening-and-host-bootstrap-1.md. File 1
  delivered the health alarms, setup-backup.sh, the compressed atomic
  archive path, and WAL offsite, all in a worktree and not yet applied to
  manta9000.
dateCreated: 20260905
dateUpdated: 20260905
status: not_started
---

## Context Summary

- File 2 of 2 for **920 backup-hardening-and-host-bootstrap**: restic
  system backup, runbooks and amendments, the production cutover, the
  drills that prove every alarm and both PITR paths, and the bootstrap
  acceptance run. Read file 1's Context Summary first — the worktree
  constraint and the conventions apply throughout.
- Cutover shape (D11, project rule): the PM runs one script
  (`setup-backup.sh`) and reads its report; the two things it cannot do
  (a PostgreSQL restart if reported, removing the three user-crontab
  lines) are the only checklist items. Drills run after cutover, in the
  same session, and the first offsite reconcile is watched, not left to
  Sunday.
- Nothing here waits on a calendar. Every drill plants its own fault or
  ages its own stamp.

## Section 6: restic system backup

Design *D9*.

- [ ] **Task 6.1: `deploy/restic-excludes.txt` and the include set**
      (effort: 1)
  - [ ] Create the exclude file from the design's D9 table: Trash, Steam,
        `.cache`, `.npm`, `.local/share/uv`, `.vscode`, `pCloudDrive`,
        `GoogleDrive`, `**/.venv`, `**/node_modules`. Comment each line
        with the D9 reason.
  - [ ] Include set as constants in the backup script (Task 6.2): `/etc`,
        `/root`, `/var/spool/cron/crontabs`, `/home/manta`.
  - [ ] Measure the would-be snapshot size with `restic backup --dry-run
        --one-file-system` (root, real repo after Task 6.3 init; or `du`
        with the excludes applied if the repo does not exist yet) and
        record it in D9.
  - [ ] Ask the PM, with the measured number, whether `Pictures` (67 G)
        and `ai` (60 G) are included; record the answer and reason in
        the exclude file and in runbook 210. Proceed with the PM's
        answer; if none is given in the session, include both (the
        design's default rule: keep what is not derivable) and say so.
  - [ ] Success: exclude file exists; D9 carries the measured size and
        the PM decision.

- [ ] **Task 6.2: `scripts/cron_system_backup.sh`** (effort: 2)
  - [ ] Required `--env-file`, `--repo-prefix`, `--exclude-file`,
        `--stamp`, `--log`, `--lock`. Reads `MT_BACKUP_S3_*` and
        `MT_BACKUP_RESTIC_PASSWORD` by grep, exports them only into the
        restic environment (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
        `RESTIC_REPOSITORY`, `RESTIC_PASSWORD`), never echoes them.
  - [ ] `flock -n`; `restic unlock`; `restic backup --one-file-system
        --exclude-file … <include set>`; `restic forget --keep-daily 7
        --keep-weekly 4 --keep-monthly 3 --prune`. Any non-zero step:
        append reason to `--log`, `logger -t manta-backup`, exit non-zero,
        stamp untouched. Success: `touch "$STAMP"`.
  - [ ] Add `--check` mode running `restic check --read-data-subset=5%`
        (the monthly cron.d entry uses it).
  - [ ] Success: `shellcheck` clean; a missing password in the env file
        exits non-zero naming `MT_BACKUP_RESTIC_PASSWORD` without running
        restic.

- [ ] **Task 6.3: restic tests** (effort: 1)
  - [ ] Unit (restic stubbed on `PATH`): argument refusal; missing
        password → no restic invocation; stub failure on `backup` →
        non-zero, no stamp, log line present; success → stamp; the
        argv recorded by the stub never contains the secret values (the
        secrets travel by environment only).
  - [ ] Success: tests pass.

- [ ] **Task 6.4: Operator docs for the new key** (effort: 1)
  - [ ] Add `MT_BACKUP_RESTIC_PASSWORD` to the README env table and to
        `deploy/manta-trading.env.example` as a commented line stating it
        belongs in the dev checkout's `.env`, not the service env file.
        Add the restic tier to the CHANGELOG `[Unreleased]`.
  - [ ] Success: `grep -rn MT_BACKUP_RESTIC_PASSWORD` finds the script,
        the README, the env example, and the runbooks only.

- [ ] **Task 6.5: Checkpoint commit** (effort: 1)
  - [ ] Commit Section 6 (e.g. `feat: add restic system backup tier`).

## Section 7: Runbooks and recorded amendments

Design *D6*, *D7*, *D8*, *D10*, *Implementation Notes*.

- [ ] **Task 7.1: Runbook 200 — alarms, cron.d, retention** (effort: 2)
  - [ ] Alarm table with all ten named failures: name → cause → flag
        (`ARCHIVE-BROKEN` / `BACKUP-STALE`) → fix. State plainly that
        neither flag is pushed anywhere and give the `ls` and
        `journalctl -t manta-backup` lines.
  - [ ] Step 7 rewritten: cron.d file installed by `setup-backup.sh`, the
        six entries, "backups stay on cron (916) — now script-managed
        (920)", and the one-time removal of the user-crontab lines.
  - [ ] Add the D4a retention capacity table and the B2 lifecycle-rule
        backstop instructions (console steps, prefixes, 30 days).
  - [ ] Drill record gains rows for every drill in Section 9 (filled in
        there).
  - [ ] Success: every named failure in `check_archive_health.sh` appears
        in the runbook table (a unit test greps both and asserts the set
        equality).

- [ ] **Task 7.2: Alarm-table consistency test** (effort: 1)
  - [ ] Unit test: the set of `FAIL <name>` names the script can emit
        (parse its case/array from Task 1.5) equals the set of names in
        the runbook's alarm table.
  - [ ] Success: test passes.

- [ ] **Task 7.3: Runbook 210 — host bootstrap** (effort: 2)
  - [ ] Create `runbooks/210-host-bootstrap.md` (frontmatter per
        conventions) with the D10 ordered procedure from bare Ubuntu
        26.04 + PostgreSQL 17 + TimescaleDB to a drift-free host: packages,
        dev checkout clone, `.env` values from the password manager (list
        the keys, never the values), `install-production.sh --ref`,
        restore from B2 (pointer to 200), `setup-backup.sh`, restart if
        reported, crontab-line removal, `--check` green, first restic
        snapshot, health PASS. Every `sudo` is a step.
  - [ ] Timeshift section: snapshots live on `/data`, count 2, the
        exclude history (the 2026-09-03 include→exclude flip), device
        UUID reported by `--check`.
  - [ ] Acceptance-test section with placeholders for the Section 9 run.
  - [ ] Add the 210 row to `runbooks/__readme.md`.
  - [ ] Success: the runbook contains no step of the form "fix
        permissions" or "adjust as needed"; each step has a command and
        an expected output.

- [ ] **Task 7.4: Recorded amendments** (effort: 1)
  - [ ] Dated entry in `user/notes/000-process-journal.md`: the dev
        checkout's third operational role (the backup tier, because the
        maintenance credential stays out of `/etc` per 913), and the
        backup cron lines becoming script-managed via cron.d. Record the
        `/opt` alternative as future work with the reason it is deferred.
  - [ ] One-line "amended by 920" pointer in the 916 design's cron
        decision (decision 4 in its decisions list).
  - [ ] Add the `install-production.sh --ref <branch>` origin-resolution
        defect to the issue tracker (out of scope here; one issue).
  - [ ] Success: all three edits committed; the design's Implementation
        Notes list matches what changed.

- [ ] **Task 7.5: Checkpoint commit** (effort: 1)
  - [ ] Commit Section 7 (e.g.
        `docs: add host-bootstrap runbook and 920 alarm table`).

## Section 8: Production cutover

Design *D11*. All of file 1 and Sections 6–7 are merged to `main` and the
host checkout is on `main` before this section starts (ordinary workflow,
not a task). `wal_compression` and `archive_command` are reload-only and
`archive_mode` is already on, so no restart is expected; the script's
report is the authority.

- [ ] **Task 8.1: Pre-cutover `--check` on manta9000** (effort: 1)
  - [ ] Run `sudo deploy/setup-backup.sh --check …` from the host
        checkout. Expect `DRIFT` for `archive_command`, cron.d (missing),
        `count_weekly`, restic repo (`MISSING`, password not yet in
        `.env`), leftover crontab lines; `OK` for ACL, directories,
        `wal_compression`, `archive_mode`.
  - [ ] Success: the report matches the expectation above; anything else
        is investigated before Task 8.2.

- [ ] **Task 8.2: [PM] Cutover — one script, one report** (effort: 1)
  - [ ] [PM] Put `MT_BACKUP_RESTIC_PASSWORD` into the host checkout's
        `.env` (from the password manager; also stored there).
  - [ ] [PM] `sudo -v`, then `sudo deploy/setup-backup.sh --checkout
        … --env-file … --backup-root /data/backup --cluster 17/main`;
        read the report. If any item says `PENDING RESTART`, stop the
        acquisition timers per runbook 100, restart PostgreSQL, resume.
  - [ ] [PM] Remove the three 915 lines from `crontab -e` (the
        `@reboot rclone mount` line stays).
  - [ ] [PM] Remove the hand-set `archive_command` line from
        `/etc/postgresql/17/main/postgresql.conf` (report names it).
  - [ ] Success: `sudo deploy/setup-backup.sh --check …` exits 0, every
        item `OK`. Success criterion 1.

- [ ] **Task 8.3: First live evidence** (effort: 1)
  - [ ] `SELECT pg_switch_wal()`; within a minute a `.zst` segment (or
        atomic raw, per Task 2.1) appears, no `.tmp` remains,
        `last_archived_wal` advanced. Success criteria 2, 3, 4.
  - [ ] Wait for the next `:30` health run or invoke
        `archive_health_cron.sh` by hand with the cron.d arguments:
        `PASS`, no flags present, `journalctl -t manta-backup` shows the
        run.
  - [ ] Run `sync_wal_offsite.sh` by hand with the cron.d arguments; the
        first push uploads the whole archive (record duration); the
        stamp exists.
  - [ ] Success: recorded in runbook 200's drill table.

- [ ] **Task 8.4: Checkpoint commit** (effort: 1)
  - [ ] Commit the measurements (e.g.
        `docs: record 920 cutover observations`).

## Section 9: Drills — alarms, PITR both paths, reconcile, restic

Design *Verification Walkthrough* steps 3–8, *Success Criteria* 5–9, 12.
Each drill is observed, then recorded in runbook 200's drill table with
date, duration, and outcome. Faults needing `postgres` or root are marked.

- [ ] **Task 9.1: Alarm drill — six new failures fire and clear**
      (effort: 2)
  - [ ] For each row of walkthrough step 3 (ACL revoked [root]; wedge
        planted [postgres]; stale `.tmp` [postgres]; aged push stamp;
        aged restic stamp [root]; scratch `--base-dir` with only
        `20260801/`): plant, run the check, observe the named `FAIL` and
        which flag appears; repair (re-run `setup-backup.sh` for the ACL;
        `mv` planted files aside; delete aged-stamp copies), observe
        `PASS` and both flags gone.
  - [ ] With only `BACKUP-STALE` present, run `cron_weekly_base.sh` with
        the real `--health-flag` path and a deliberately nonexistent
        `--env-file`: it must get past the flag check and fail on the
        missing env file (that error is the evidence the flag did not
        gate it). Then plant `ARCHIVE-BROKEN` and repeat: it must refuse
        on the flag before anything else. Remove the planted flag.
  - [ ] Success: six FAIL lines and two flag behaviors observed; success
        criterion 8.

- [ ] **Task 9.2: PITR drill across the mixed archive (local)** (effort: 3)
  - [ ] Runbook 200 Step 6 + PITR section with the new `restore_command`:
        sentinel row committed now (after cutover), restore to just
        before and just after; the recovery log must show `.zst` segments
        being restored (and raw ones from before cutover if the target
        window spans it). Tear down `/data/restore-test`.
  - [ ] Success: absent-before / present-after; success criterion 5.

- [ ] **Task 9.3: PITR drill from B2-sourced WAL** (effort: 3)
  - [ ] `rclone copy` the segment range the drill needs from
        `b2:$BUCKET/wal` into `/data/restore-test/wal-b2`; set
        `restore_command` to read from that directory only (the local
        archive path must not appear in it); repeat one direction of
        Task 9.2. Tear down.
  - [ ] Success: recovery reaches the target using only B2 segments;
        success criterion 7.

- [ ] **Task 9.4: Watched first offsite reconcile** (effort: 2)
  - [ ] Run `cron_weekly_base.sh` by hand with the cron.d arguments while
        watching the log (the base backup runs first, ~2.5 h; the
        reconcile follows). Confirm the guards print their passes, the
        sync's `--max-delete` value, and `removed base/20260816
        base/20260817`; then `rclone lsd b2:$BUCKET/base/` lists only the
        locally retained dates and `rclone check --one-way` of the WAL dir
        reports 0 differences.
  - [ ] Success: success criteria 6 and 9.

- [ ] **Task 9.5: restic first snapshot and restore drill** (effort: 2)
  - [ ] `sudo scripts/cron_system_backup.sh …` with the cron.d arguments;
        record duration and repository size. `sudo … --check` passes.
  - [ ] Restore `latest` with `--include /etc/postgresql --include
        /etc/timeshift --include /var/spool/cron/crontabs --include
        /home/manta/source/repos/manta/trading-data/project-documents`
        into `/data/restore-test/restic`; `diff -r` each against live is
        clean. Remove the restore directory.
  - [ ] Success: success criterion 12.

- [ ] **Task 9.6: Checkpoint commit** (effort: 1)
  - [ ] Commit the drill records (e.g.
        `docs: record 920 alarm, PITR, reconcile, and restic drills`).

## Section 10: Bootstrap acceptance and close-out

Design *D10*, *Success Criteria* 13.

- [ ] **Task 10.1: Acceptance run on hammerhead (PM go) or a VM**
      (effort: 3)
  - [ ] Ask the PM for go on hammerhead (one restart of the test cluster).
        If the answer is no, use a fresh Ubuntu 26.04 VM with PostgreSQL
        17 + TimescaleDB installed per runbook 400's package steps. Either
        way, proceed in this session with the chosen host.
  - [ ] Follow `210-host-bootstrap.md` verbatim for the backup sections
        from clean state, with a throwaway `--backup-root`, a scratch
        restic prefix, and `--checkout` pointing at a clone of `main`.
        Every deviation from the runbook is a runbook bug: fix the
        runbook, not the run.
  - [ ] End state: `setup-backup.sh --check` green, a segment switch lands
        a `.zst` in the throwaway archive, `check_archive_health.sh`
        PASS. Then tear down: remove the archive root, cron.d file,
        `ALTER SYSTEM RESET` the three settings + reload (restart if the
        report said so), delete the scratch restic prefix, remove restic
        if it was absent before.
  - [ ] Record host, date, durations, and every runbook fix in the
        acceptance section of runbook 210.
  - [ ] Success: success criterion 13; hammerhead's `pg_lsclusters` and
        settings match their pre-run values.

- [ ] **Task 10.2: Close-out** (effort: 1)
  - [ ] Set the design's `status: complete` and `dateUpdated`; refine the
        Verification Walkthrough to the as-executed commands and numbers;
        update the 900 slice-plan entry 21 to `[x]` with a one-line
        completion note; CHANGELOG entry.
  - [ ] Task files: mark every item; `status: complete`.
  - [ ] Success: `cf check` reports no inconsistency for slice 920; final
        commit (e.g. `docs: close slice 920 — backup hardening cut over`).
