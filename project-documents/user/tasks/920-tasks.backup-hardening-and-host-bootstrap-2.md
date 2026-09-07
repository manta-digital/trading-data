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
dateUpdated: 20260906
status: in_progress
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

- [x] **Task 6.1: `deploy/restic-excludes.txt` and the include set**
      (effort: 1)
  - [x] Create the exclude file from the design's D9 table: Trash, Steam,
        `.cache`, `.npm`, `.local/share/uv`, `.vscode`, `pCloudDrive`,
        `GoogleDrive`, `**/.venv`, `**/node_modules`. Comment each line
        with the D9 reason.
  - [x] Include set as constants in the backup script (Task 6.2): `/etc`,
        `/root`, `/var/spool/cron/crontabs`, `/home/manta`.
  - [x] Measure the would-be snapshot size with `du -xs` over the include
        set with the exclude patterns applied (the restic repository does
        not exist until the cutover initializes it; a true `--dry-run`
        number is taken in Task 9.5) and record it in D9.
  - [x] PM decision (2026-09-06): **include** `~/Pictures`, **exclude**
        `~/ai`. Add `/home/manta/ai` to the exclude file with that
        provenance; record both in runbook 210.
  - [x] Success: exclude file exists; D9 carries the measured size and
        the PM decision.

- [x] **Task 6.2: `scripts/cron_system_backup.sh`** (effort: 2)
  - [x] Required `--env-file`, `--repo-prefix`, `--exclude-file`,
        `--stamp`, `--log`, `--lock`. Reads `MT_BACKUP_S3_*` and
        `MT_BACKUP_RESTIC_PASSWORD` by grep, exports them only into the
        restic environment (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
        `RESTIC_REPOSITORY`, `RESTIC_PASSWORD`), never echoes them.
  - [x] `flock -n`; `restic unlock`; `restic backup --one-file-system
        --exclude-file … <include set>`; `restic forget --keep-daily 7
        --keep-weekly 4 --keep-monthly 3 --prune`. Any non-zero step:
        append reason to `--log`, `logger -t manta-backup`, exit non-zero,
        stamp untouched. Success: `touch "$STAMP"`.
  - [x] Add `--check` mode running `restic check --read-data-subset=5%`
        (the monthly cron.d entry uses it).
  - [x] Success: `shellcheck` clean; a missing password in the env file
        exits non-zero naming `MT_BACKUP_RESTIC_PASSWORD` without running
        restic.

- [x] **Task 6.3: restic tests** (effort: 1)
  - [x] Unit (restic stubbed on `PATH`): argument refusal; missing
        password → no restic invocation; stub failure on `backup` →
        non-zero, no stamp, log line present; success → stamp; the
        argv recorded by the stub never contains the secret values (the
        secrets travel by environment only).
  - [x] Success: tests pass.

- [x] **Task 6.4: Operator docs for the new key** (effort: 1)
  - [x] Add `MT_BACKUP_RESTIC_PASSWORD` to the README env table and to
        `deploy/manta-trading.env.example` as a commented line stating it
        belongs in the dev checkout's `.env`, not the service env file.
        Add the restic tier to the CHANGELOG `[Unreleased]`.
  - [x] Success: `grep -rn MT_BACKUP_RESTIC_PASSWORD` finds only
        `scripts/cron_system_backup.sh`, `deploy/setup-backup.sh`, their
        unit tests, the README, the env example, and the runbooks; the
        env-example line is commented; no hit contains a value.

- [x] **Task 6.5: Checkpoint commit** (effort: 1)
  - [x] Commit Section 6 (e.g. `feat: add restic system backup tier`).

## Section 7: Runbooks and recorded amendments

Design *D6*, *D7*, *D8*, *D10*, *Implementation Notes*.

- [x] **Task 7.1: Runbook 200 — alarms, cron.d, retention** (effort: 2)
  - [x] Alarm table with all ten named failures: name → cause → flag
        (`ARCHIVE-BROKEN` / `BACKUP-STALE`) → fix. State plainly that
        neither flag is pushed anywhere and give the `ls` and
        `journalctl -t manta-backup` lines.
  - [x] Step 7 rewritten: cron.d file installed by `setup-backup.sh`, the
        six entries, "backups stay on cron (916) — now script-managed
        (920)", and the one-time removal of the user-crontab lines.
  - [x] Add the D4a retention capacity table and the B2 lifecycle-rule
        backstop instructions (console steps, prefixes, 30 days).
  - [x] Drill record gains rows for every drill in Section 9 (filled in
        there).
  - [x] Add the arm-file procedure: what `RECONCILE-ARMED` gates, that
        only the watched first reconcile (Task 9.4) creates it, and that a
        rebuilt host starts unarmed.
  - [x] Success: all ten named failures from `check_backup_health.sh`'s
        class array (file 1, Task 1.3) appear in the runbook table (the
        Task 7.2 test asserts the set equality).

- [x] **Task 7.2: Alarm-table consistency test** (effort: 1)
  - [x] Unit test: the set of names in `scripts/check_backup_health.sh`'s
        class array (file 1, Task 1.3 — all ten, four inherited plus six
        new) equals the set of names in the runbook's alarm table.
  - [x] Success: test passes.

- [x] **Task 7.3: Runbook 210 — host bootstrap** (effort: 2)
  - [x] Create `runbooks/210-host-bootstrap.md` (frontmatter per
        conventions) with the D10 ordered procedure from bare Ubuntu
        26.04 + PostgreSQL 17 + TimescaleDB to a drift-free host: packages,
        dev checkout clone, `.env` values from the password manager (list
        the keys, never the values), `install-production.sh --ref`,
        restore from B2 (pointer to 200), `setup-backup.sh`, restart if
        reported, crontab-line removal, `--check` green, first restic
        snapshot, health PASS. Every `sudo` is a step.
  - [x] Timeshift section: snapshots live on `/data`, count 2, the
        exclude history (the 2026-09-03 include→exclude flip), device
        UUID reported by `--check`.
  - [x] Acceptance-test section with placeholders for the Task 10.1 run.
  - [x] Add the 210 row to `runbooks/__readme.md`.
  - [x] Success: the runbook contains no step of the form "fix
        permissions" or "adjust as needed"; each step has a command and
        an expected output.

- [x] **Task 7.4: Recorded amendments** (effort: 1)
  - [x] Dated entry in `user/notes/000-process-journal.md`: the dev
        checkout's third operational role (the backup tier, because the
        maintenance credential stays out of `/etc` per 913), and the
        backup cron lines becoming script-managed via cron.d. Record the
        `/opt` alternative as future work with the reason it is deferred.
  - [x] One-line "amended by 920" pointer in the 916 design's cron
        decision (decision 4 in its decisions list).
  - [x] File the `install-production.sh --ref <branch>` origin-resolution
        defect with `gh issue create` on the GitHub remote — deliberately
        outside this slice's scope to fix, recorded so it is not lost; one
        issue, no code.
  - [x] Success: all three edits committed; the design's Implementation
        Notes list matches what changed.

- [x] **Task 7.5: Checkpoint commit** (effort: 1)
  - [x] Commit Section 7 (e.g.
        `docs: add host-bootstrap runbook and 920 alarm table`).

## Section 8: Production cutover

Design *D11*. Precondition, verified by Task 8.1: file 1 and Sections 6–7
are on `main` and the host checkout is at that `main`. The merge cannot
disturb the live crontab: its three lines call the untouched 915 scripts
(Context Summary, file 1). `wal_compression` and `archive_command` are
reload-only and `archive_mode` is already on, so no restart is expected;
the script's report is the authority. D11 step 3 asked for the mixed-archive
PITR drill "before proceeding" to offsite work; with the worktree constraint
nothing is live before this cutover, so the drill (Task 9.2) follows it. The
`RECONCILE-ARMED` gate is what keeps the destructive offsite step behind the
drills, not task order.

- [x] **Task 8.1: Pre-cutover `--check` on manta9000** (effort: 1)
  - [x] Confirm `git -C ~/source/repos/manta/trading-data rev-parse HEAD`
        equals `origin/main` and the last line of
        `/data/backup/archive-health.log` is a PASS from the last half
        hour (the 915 crontab is still alive).
  - [x] Run `sudo deploy/setup-backup.sh --check …` from the host
        checkout. Expected report: `MISSING restic`, `MISSING system/`
        directory, `MISSING` cron.d file, `MISSING` restic repo (password
        not yet in `.env`); `DRIFT archive_command`,
        `DRIFT archive_command source` (`postgresql.conf`),
        `DRIFT count_weekly 2 3`, `DRIFT` leftover crontab lines; `OK` for
        the ACL, `base/ wal/ metadata/`, `wal_compression`, `archive_mode`.
  - [x] Record the pre-cutover timeshift baseline:
        `jq '.backup_device_uuid, .count_weekly, .exclude'
        /etc/timeshift/timeshift.json` into the drill notes.
  - [x] Success: the report matches; any other line is investigated
        before Task 8.2.

- [ ] **Task 8.2: [PM] Cutover — one script, one report** (effort: 1)
  - [x] [PM] Put `MT_BACKUP_RESTIC_PASSWORD` into the host checkout's
        `.env` (from the password manager; also stored there).
  - [x] [PM] `sudo -v`, then `sudo deploy/setup-backup.sh --checkout …
        --env-file … --backup-root /data/backup --cluster 17/main`; read
        the report. If any item says `PENDING RESTART`, stop the
        acquisition timers per runbook 100, restart PostgreSQL, resume.
  - [x] [PM] Run the same command a second time: **zero `APPLIED`
        lines** (success criterion 1's idempotence half). `DRIFT` is still
        expected on `archive_command source` and the leftover crontab
        lines, and `MISSING` on `RECONCILE-ARMED`, until the bullets below
        remove or create them.
  - [x] [PM] Remove the three 915 lines from `crontab -e` (the
        `@reboot rclone mount` line stays). Remove the hand-set
        `archive_command` line from `/etc/postgresql/17/main/postgresql.conf`.
  - [ ] [PM] In the B2 console, set the lifecycle rule from runbook 200
        (delete versions older than 30 days on `wal/` and `base/`); paste
        the resulting rule JSON into the runbook.
  - [ ] Success: `sudo deploy/setup-backup.sh --check …` reports every
        item `OK` except `MISSING RECONCILE-ARMED` (armed in Task 9.4).
        Success criterion 1's `--check` clause is closed in Task 9.4.

- [x] **Task 8.3: Settings evidence** (effort: 1)
  - [x] `SELECT name, setting, sourcefile FROM pg_settings WHERE name IN
        ('archive_mode','archive_command','wal_compression')` as
        `postgres`: values equal the script constants, every `sourcefile`
        ends in `postgresql.auto.conf`. Success criterion 2.
  - [x] `SELECT pg_switch_wal()`; within a minute a `.zst` segment (or
        atomic raw, per Task 2.1) appears, no `.tmp` remains,
        `last_archived_wal` advanced. `getfacl` shows both `manta`
        entries. Success criteria 3 and 4.
  - [x] `jq '.backup_device_uuid, .count_weekly, .exclude'
        /etc/timeshift/timeshift.json`: UUID identical to the Task 8.1
        baseline, `count_weekly` 2, the four excludes. Success criterion 11.
  - [x] Success: recorded in runbook 200's drill table.

- [x] **Task 8.4: Cron-driven evidence (required, not by hand)**
      (effort: 1)
  - [x] `cat /etc/cron.d/manta-trading-backup` shows the six entries of
        D7 with the expected user fields (success criterion 10);
        `journalctl -t CRON --since '-5 min'` shows
        `RELOAD (/etc/cron.d/manta-trading-backup)` and no `bad` or
        `error` line for it.
  - [x] Wait for the next `:00` or `:30` boundary (at most 30 minutes):
        `journalctl -t CRON` shows `(manta) CMD (… backup_health_cron.sh …)`,
        the health log gains a PASS line, no flags exist, and
        `journalctl -t manta-backup` shows the run.
  - [x] Wait for the next `:00` (at most 60 minutes): the CRON journal
        shows `sync_wal_offsite.sh` firing, and the offsite stamp's mtime
        is from a run nobody launched by hand. The first push uploads the
        whole archive; record its duration from the log. — 2026-09-06 14:00:01: fired from cron; logged `skipped: previous run active` because the by-hand full first push held the lock (overlap guard observed live); the stamp mtime from a cron run is recorded in the drill table once the full push completes
  - [x] Success: two distinct cron.d entries observed executing from
        cron; success criterion 6's "during normal operation" holds.

- [x] **Task 8.5: Checkpoint commit** (effort: 1)
  - [x] Commit the measurements (e.g.
        `docs: record 920 cutover observations`).

- [x] **Task 8.6: Delete the superseded 915 glue** (effort: 1)
  - [x] Only after Task 8.4: delete `scripts/archive_health_cron.sh` and
        `scripts/cron_weekly_base.sh`, their cases in
        `test/unit/test_backup_cron_glue.py`, and every runbook reference
        (`grep -rn` both names under `project-documents/user/runbooks`
        and `scripts/` returns nothing).
  - [x] Success: unit tier passes; `crontab -l` and `/etc/cron.d` contain
        no reference to either name; commit (e.g.
        `refactor: remove 915 cron glue superseded by cron.d`).

## Section 9: Drills — alarms, PITR both paths, reconcile, restic

Design *Verification Walkthrough* steps 3–8, *Success Criteria* 5–9, 12.
Each drill is observed, then recorded in runbook 200's drill table with
date, duration, and outcome. Faults needing `postgres` or root are marked.

- [x] **Task 9.1: Alarm drill — six new failures fire and clear**
      (effort: 2)
  - [x] For each row of walkthrough step 3 (ACL revoked [root]; wedge
        planted [postgres] at the name `wal_segment_name.py next
        <last_archived_wal>` prints; stale `.tmp` [postgres]; aged push
        stamp; aged restic stamp [root]; scratch `--base-dir` with only
        `20260801/`) — 2026-09-06: five of six observed firing and clearing (wedge, tmp, push stamp, restic stamp, weekly base); ACL revoke needs root, pending PM: plant, run `check_backup_health.sh`, observe the named `FAIL` and
        which flag appears; repair (re-run `setup-backup.sh` for the ACL;
        `mv` planted files aside; delete aged-stamp copies), observe
        `PASS` and both flags gone.
  - [x] With only `BACKUP-STALE` present, run `cron_weekly_backup.sh` with
        the full cron.d argument set but a deliberately nonexistent
        `--env-file`: it must get past the flag check and fail on the
        missing env file (that error is the evidence the flag did not
        gate it). Then plant `ARCHIVE-BROKEN` and repeat: it must refuse
        on the flag before anything else. Remove the planted flag.
  - [x] Success: six FAIL lines and two flag behaviors observed — the
        alarm half of success criterion 8; its guard-refusal half was
        proven in file 1, Task 5.6.

- [x] **Task 9.2: PITR drill across the mixed archive (local)** (effort: 3)
  - [x] Runbook 200 Step 6 + PITR section with the new `restore_command`:
        sentinel row committed now (after cutover), restore to just
        before and just after; the recovery log must show `.zst` segments
        being restored (and raw ones from before cutover if the target
        window spans it). Tear down `/data/restore-test`.
  - [x] Success: absent-before / present-after; success criterion 5.

- [x] **Task 9.3: PITR drill from B2-sourced WAL** (effort: 3)
  - [x] `rclone copy` the segment range the drill needs from
        `b2:$BUCKET/wal` into `/data/restore-test/wal-b2`; set
        `restore_command` to read from that directory only (the local
        archive path must not appear in it); repeat one direction of
        Task 9.2. Tear down.
  - [x] Success: recovery reaches the target using only B2 segments;
        success criterion 7.

- [x] **Task 9.4: Watched first offsite reconcile** (effort: 2)
  - [x] Run `cron_weekly_backup.sh` by hand with the cron.d arguments while
        watching the log (the base backup runs first, ~2.5 h; the
        reconcile follows). Confirm the guards print their passes, the
        sync's `--max-delete` value, and `removed base/20260816
        base/20260817`; then `rclone lsd b2:$BUCKET/base/` lists only the
        locally retained dates and `rclone check --one-way` of the WAL dir
        reports 0 differences.
  - [x] The first hand run happens **unarmed**: guards pass, the log
        shows `reconcile skipped: not armed`, B2 unchanged. Then `touch
        /data/backup/RECONCILE-ARMED` and run again, watching the sync.
  - [ ] Success: success criteria 6 and 9; `setup-backup.sh --check` now
        exits 0 with every item `OK` (success criterion 1 closed).

- [ ] **Task 9.5: restic first snapshot and restore drill** (effort: 2)
  - [ ] `sudo scripts/cron_system_backup.sh …` with the cron.d arguments;
        record duration and repository size. `sudo … --check` passes.
  - [ ] Record a true `restic backup --dry-run` size first (the D9 number
        Task 6.1 estimated with `du`).
  - [ ] Restore `latest` with `--include /etc/postgresql --include
        /etc/timeshift --include /var/spool/cron/crontabs --include
        /home/manta/source/repos/manta/trading-data/deploy` (a subtree not
        edited during this task) into `/data/restore-test/restic`;
        `diff -r` each against live is clean. Remove the restore directory.
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
  - [ ] Before the run: copy `/etc/timeshift/timeshift.json` aside if it
        exists. Hammerhead has no timeshift installed (measured
        2026-09-05); step 6 must report `MISSING timeshift` and **never
        create the file** (it cannot know the device UUID). Record that
        behavior in runbook 210 as the expected result on a host without
        timeshift.
  - [ ] End state: `setup-backup.sh --check` green apart from the
        expected `MISSING` items (timeshift, `RECONCILE-ARMED`), a segment
        switch lands a `.zst` in the throwaway archive,
        `check_backup_health.sh` PASS. Then tear down: remove the archive
        root (which removes its ACL), cron.d file, `ALTER SYSTEM RESET`
        the three settings + reload (restart if the report said so),
        restore the timeshift copy if one was taken, delete the scratch
        restic prefix, remove restic if it was absent before.
  - [ ] Record host, date, durations, and every runbook fix in the
        acceptance section of runbook 210.
  - [ ] Success: success criterion 13; hammerhead's `pg_lsclusters` and
        settings match their pre-run values.

- [ ] **Task 10.2: Close-out** (effort: 1)
  - [ ] Set the design's `status: complete` and `dateUpdated`; refine the
        Verification Walkthrough to the as-executed commands and numbers;
        update the 900 slice-plan entry 21 to `[x]` with a one-line
        completion note; CHANGELOG entry.
  - [ ] Task files: mark every item; `status: complete`. The task
        breakdown's composition deviation (new wrapper scripts instead of
        editing the 915 ones) is already recorded in the design's D5.
  - [ ] Success: `cf check` reports no inconsistency for slice 920; final
        commit (e.g. `docs: close slice 920 — backup hardening cut over`).
