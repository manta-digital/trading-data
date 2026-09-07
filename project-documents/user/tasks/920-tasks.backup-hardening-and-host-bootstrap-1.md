---
docType: tasks
slice: backup-hardening-and-host-bootstrap
project: trading-data
lld: user/slices/920-slice.backup-hardening-and-host-bootstrap.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [915, 916]
interfaces: [917, 919]
projectState: >
  Slice 915 backup regime is live on manta9000 (WAL archive, weekly base,
  nightly metadata, B2 offsite, ARCHIVE-BROKEN alarm). The 2026-09-02/03
  disk-full incident was recovered by hand-applied host state that the
  repo does not capture. Design 920 committed at 611f9af (review CONCERNS,
  passes gate). Measured baseline 2026-09-05: archive_command hand-set,
  wal_compression=zstd, ACL applied by hand, WAL 16 GiB/day, 0 bytes of
  WAL offsite, B2 holds two superseded bases, restic not installed.
dateCreated: 20260905
dateUpdated: 20260907
status: complete
---

## Context Summary

- Working on **920 backup-hardening-and-host-bootstrap**, file 1 of 2:
  health-check alarms, the compression measurement, `setup-backup.sh`,
  the compressed/atomic archive path, and WAL offsite. File 2 covers
  restic, runbooks, cutover, drills, and the bootstrap acceptance run.
- Source of truth: `user/slices/920-slice.backup-hardening-and-host-bootstrap.md`.
  Tasks cite its Decisions (D1–D11, D4a); consult it before each section.
- **The host checkout is what cron runs.** The three backup cron lines on
  manta9000 invoke `scripts/` from
  `/home/manta/source/repos/manta/trading-data`, which is also this
  development checkout. Checking out the slice branch here changes what
  cron runs at the next half hour. Standing constraint for this slice:
  implement in a **git worktree** outside that path (for example
  `~/source/repos/manta/trading-data-920`), leaving the host checkout on
  `main` until the cutover in file 2. Every task below assumes the
  worktree.
- Existing patterns to follow: the 915 scripts (`set -euo pipefail`,
  explicit required arguments, named constants at the top, credentials
  grep'd from an env file and never sourced); `deploy/install-production.sh`
  (numbered check-then-act steps, `die`/`step` helpers, root check);
  pytest subprocess tests in `test/unit/test_backup_scripts.py` and
  `test/unit/test_backup_cron_glue.py` (argument refusal, `tmp_path`
  fixture directories, no database required).
- Integration-tier tests needing a database use `MT_TIMESCALE_TEST_URL`
  (export from `.env`, strip quotes). The test cluster on hammerhead has
  `archive_mode=off`; tests must not assume archiving there.
- **Composition, not extension, for the cron-invoked scripts** (task
  breakdown deviation from the design's D5 wording, recorded in the design):
  the 915 glue scripts the live crontab invokes (`archive_health_cron.sh`,
  `cron_weekly_base.sh`) and the inner `check_archive_health.sh` are **not
  modified**. New scripts wrap them: `check_backup_health.sh` runs
  `check_archive_health.sh` for the four database checks and adds the six
  new ones; `backup_health_cron.sh` and `cron_weekly_backup.sh` are the
  cron.d glue. So merging to `main` cannot break the live crontab lines —
  they keep calling untouched scripts until the cutover installs cron.d and
  Task 8.6 deletes them. Changes to shared tools (`prune_wal_archive.sh`)
  are additive and argument-compatible.
- Rules in force: no defaults for host paths or intervals — one constant
  each; never read a DB URL from ambient environment inside a tool; every
  new alarm must be observed firing before it counts as delivered; PM host
  steps are a script invocation plus a printed report, not a checklist.
- Commit checkpoint at the end of each section; scope `ruff format` and
  `shellcheck` (install via apt if absent) to touched files.
- Delivers (file 1): six named health failures on two flags, the atomic
  compressed archive command, prune `-x .zst`, `setup-backup.sh` with
  `--check`, the hourly WAL push, and the guarded weekly reconcile. Task
  numbers cited from file 2 refer to this numbering.
- Next planned slice after 920: none queued; PM decides.

## Section 1: Health-check named failures and the second flag

Design *D5*, *D6*. Read-only additions first, so the alarms exist before
anything they guard changes (D11 step 1). `check_archive_health.sh` and
`archive_health_cron.sh` are not edited (see Context Summary).

- [x] **Task 1.1: `scripts/wal_segment_name.py` — segment-name arithmetic
      in one place** (effort: 1)
  - [x] Small stdlib-only module with a CLI: `next <segment-name>` prints
        the following segment name (timeline preserved, 24 hex chars,
        log-file rollover at `…FF` → next log, zero padded);
        `from-lsn <tli> <lsn>` prints the segment holding an LSN (the
        arithmetic `prune_wal_archive.sh` currently inlines — move it
        here and have the prune call the module, so it exists once).
  - [x] Refuse malformed names (wrong length, non-hex) with exit 2.
  - [x] Success: `prune_wal_archive.sh` no longer contains inline Python;
        existing prune tests pass unchanged.

- [x] **Task 1.2: Segment-name unit tests** (effort: 1)
  - [x] `test/unit/test_wal_segment_name.py` with **literal** expected
        values written independently of the code: `000000010000121700000083`
        → `…84`; `0000000100001217000000FF` → `000000010000121800000000`;
        timeline `00000002` preserved; `from-lsn 1 1217/83A00000` →
        `000000010000121700000083`; malformed input refused.
  - [x] Success: tests pass.

- [x] **Task 1.3: `scripts/check_backup_health.sh` — wrapper and
      constants** (effort: 1)
  - [x] Required arguments `--db-url`, `--pgdata`, `--wal-dir`, `--stamp`,
        `--stale-after <minutes>`, `--system-stamp`, `--base-dir`; missing
        any is a usage error (exit 2) naming it.
  - [x] Runs `check_archive_health.sh --db-url --pgdata` first with its
        exit code **captured** (`|| inner_rc=$?`, not allowed to abort the
        wrapper under `set -e`), passes its output lines through verbatim,
        folds its FAIL lines into the `archive` class, and always continues
        to its own six checks and the `FLAGS` line. An unhealthy archive is
        exactly when the stale checks must still run.
  - [x] Named constants at the top: `WAL_SEGMENT_BYTES=16777216`,
        `TMP_LEFTOVER_MAX_AGE_MIN=10`, `SYSTEM_BACKUP_STALE_DAYS=2`,
        `WEEKLY_BASE_STALE_DAYS=9`, `PRUNE_CANARY=.prune-canary.tmp`; a
        single array mapping each of the ten check names to its class
        (`archive` or `stale`) — the one place the classes are defined.
  - [x] Header comment lists all ten checks with their class and flag.
  - [x] Success: with all arguments and a healthy target the output is the
        inner script's PASS line plus `FLAGS archive=0 stale=0`, exit 0.

- [x] **Task 1.3a: Wrapper tests** (effort: 1)
  - [x] Create `test/unit/test_backup_health.py` (subprocess; the inner
        `check_archive_health.sh` stubbed on `PATH` printing a chosen
        PASS/FAIL set and exit code): argument refusal for every required
        argument; inner PASS passes through; inner exit 1 with two FAIL
        lines → both lines present, `FLAGS archive=2 stale=0`, wrapper
        continues to its own checks.
  - [x] Success: tests pass.

- [x] **Task 1.4: `prune_permission` canary** (effort: 1)
  - [x] Create then delete `$WAL_DIR/$PRUNE_CANARY` as the invoking user;
        either failure appends `FAIL prune_permission: …` naming the
        directory and `id -un`. A `trap … EXIT` removes the canary on every
        exit path.
  - [x] Success: passes with the ACL present; `FAIL prune_permission` on a
        directory the user cannot write.

- [x] **Task 1.5: `archive_wedged` and `archive_tmp_leftover`** (effort: 2)
  - [x] Next-to-archive name: read `last_archived_wal`, `last_failed_wal`,
        and the current-failure boolean from `pg_stat_archiver` (one
        query); when failing use `last_failed_wal`, otherwise
        `wal_segment_name.py next <last_archived_wal>`. No name arithmetic
        in shell or SQL.
  - [x] `archive_wedged`: FAIL if `$WAL_DIR/<next>` exists with size ≠
        `WAL_SEGMENT_BYTES` and `$WAL_DIR/<next>.zst` does not exist.
  - [x] `archive_tmp_leftover`: FAIL if any `*.tmp` in `$WAL_DIR` is older
        than `TMP_LEFTOVER_MAX_AGE_MIN` (`find -mmin`).
  - [x] Success: both print their named FAIL on a planted fault only.

- [x] **Task 1.5a: Canary, wedge, and tmp-leftover tests** (effort: 1)
  - [x] In `test_backup_health.py`, `tmp_path` fixtures: read-only dir →
        `FAIL prune_permission` and no canary left behind; a 1000-byte
        file at a **literal** next segment name with the stubbed inner
        query reporting that `last_archived_wal` → `FAIL archive_wedged`;
        the same with a `.zst` sibling present → no FAIL; a 20-minute-old
        `x.zst.tmp` → `FAIL archive_tmp_leftover`; a fresh one → none.
  - [x] Success: tests pass.

- [x] **Task 1.6: `offsite_wal_stale`, `system_backup_stale`,
      `weekly_base_stale`, and the summary line** (effort: 1)
  - [x] `offsite_wal_stale`: `--stamp` missing or older than
        `--stale-after` minutes. `system_backup_stale`: `--system-stamp`
        missing or older than `SYSTEM_BACKUP_STALE_DAYS`.
        `weekly_base_stale`: newest `YYYYMMDD` directory under
        `--base-dir` older than `WEEKLY_BASE_STALE_DAYS`, comparing the
        name as a date (a prune touches mtime).
  - [x] Final line `FLAGS archive=<n> stale=<m>` counted from the class
        array; exit 1 on any FAIL.
  - [x] Success: each prints its named FAIL when its input is aged; the
        counts match the classes.

- [x] **Task 1.6a: Stale-check and summary-line tests** (effort: 1)
  - [x] In `test_backup_health.py`: aged push stamp, missing system
        stamp, `base/20260101` only → each named FAIL and
        `FLAGS archive=0 stale=<n>`; a mixed fault set → both counts
        correct; healthy → `FLAGS archive=0 stale=0` and exit 0.
  - [x] Success: tests pass.

- [x] **Task 1.7: `scripts/backup_health_cron.sh` — two-flag glue**
      (effort: 1)
  - [x] Same shape as `archive_health_cron.sh` (grep the URL from
        `--env-file`, never source): required `--env-file`, `--pgdata`,
        `--wal-dir`, `--stamp`, `--stale-after`, `--system-stamp`,
        `--base-dir`, `--flag`, `--stale-flag`, `--log`.
  - [x] From the summary line: write `--flag` (archive) when `archive>0`,
        `--stale-flag` when `stale>0`; remove each when its count is 0. An
        uncheckable run writes the archive flag, as today.
  - [x] One `logger -t manta-backup` line per flag transition naming the
        flag and the FAIL names; one log line per run.
  - [x] Success: the existing `cron_weekly_base.sh`/`cron_weekly_backup.sh`
        gate reads only the archive flag path.

- [x] **Task 1.8: Glue tests and one live run** (effort: 1)
  - [x] Unit (checker stubbed on `PATH`): glue argument refusal; the glue
        writes the stale flag and not the archive flag for
        `FLAGS archive=0 stale=1` and the reverse; both cleared on
        `archive=0 stale=0`; an uncheckable run writes the archive flag.
  - [x] `test/integration/test_backup_health_live.py` (test cluster via
        `MT_TIMESCALE_TEST_URL`): one run against a healthy `tmp_path`
        layout asserting the inner script's lines pass through and
        `archive_mode_off` (the test cluster's state) is classed `archive`.
  - [x] Success: unit tier passes; integration test passes in isolation.

- [x] **Task 1.9: Checkpoint commit** (effort: 1)
  - [x] Commit Section 1 (e.g.
        `feat: add check_backup_health with six named failures on two flags`).

## Section 2: Compression measurement (go/no-go)

Design *D3*.

- [x] **Task 2.1: Measure the archived-segment compression ratio**
      (effort: 1)
  - [x] On manta9000, copy the 200 newest raw segments from
        `/data/backup/wal` to a scratch directory under `/data`
        (read-only against the archive), run `zstd -T2 -q` on each, and
        compute total raw bytes / total compressed bytes. Also record
        wall-clock per segment.
  - [x] Write the result as a dated row in the design's D3 section
        ("Measured YYYY-MM-DD: ratio N.Nx over 200 segments, M ms/segment")
        and the decision: **go** if ≥ 1.5×, otherwise **no-go**.
  - [x] Remove the scratch directory.
  - [x] Success: the design carries the number and the decision; every
        later task in Sections 3–5 that says "if go" follows it.

- [x] **Task 2.2: Checkpoint commit** (effort: 1)
  - [x] Commit (e.g. `docs: record 920 archive compression measurement`).

## Section 3: `deploy/setup-backup.sh`

Design *D1*, *D2*, *D3*, *D7*, *D8*, *D9 step 7*. Build the skeleton and
`--check` first so every later step is testable as it lands.

- [x] **Task 3.1: Skeleton, arguments, `--check`, reporting** (effort: 2)
  - [x] Create `deploy/setup-backup.sh` in the `install-production.sh`
        mould: root check, `die`/`step` helpers, required arguments
        `--checkout`, `--env-file`, `--backup-root`, `--cluster`, no
        defaults; `--check` flag.
  - [x] Constants block at the top, one definition each:
        `WAL_OFFSITE_INTERVAL_MIN=60`, `KEEP_DAYS=7`, `CRON_USER=manta`,
        `ARCHIVE_COMMAND` (D2 form, or the uncompressed atomic form if
        Task 2.1 said no-go), `WAL_COMPRESSION=zstd`,
        `TIMESHIFT_COUNT_WEEKLY=2`, the timeshift exclude list, the
        cron.d template path, the restic repo prefix `system`.
  - [x] Reporting helper: each item calls `report OK|DRIFT|MISSING <item>
        [<expected> <actual>]`; in `--check` mode the script changes
        nothing and exits 1 if any item is not `OK`. In apply mode each
        step acts only when its check is not `OK`, prints `APPLIED <item>`,
        then re-reports. A run that changes nothing therefore prints zero
        `APPLIED` lines — that is the idempotence assertion Task 8.2 makes.
  - [x] Success: `sudo deploy/setup-backup.sh --check …` on manta9000
        runs to the end and prints one line per item (items from later
        tasks appear as they are added).

- [x] **Task 3.1a: Skeleton tests** (effort: 1)
  - [x] Create `test/unit/test_setup_backup.py` (subprocess, no root, no
        DB): argument refusal for each required argument; refusal when
        not root (message names `sudo`); `--check` with a scratch
        `--backup-root` exits 1 and prints `MISSING` lines, never
        `APPLIED`.
  - [x] Success: tests pass.

- [x] **Task 3.2: Steps 1–3 — packages, directories, ACL** (effort: 1)
  - [x] Step 1: `restic` installed (`dpkg -s`), install if missing.
  - [x] Step 2: `base/ wal/ metadata/ system/` under `--backup-root`;
        `wal/` owner `postgres:postgres` mode 0755.
  - [x] Step 3: `setfacl -m u:$CRON_USER:rwx -m d:u:$CRON_USER:rwx wal/`;
        check via `getfacl` for both the access and default entries.
  - [x] Success: `--check` reports all three `OK` on manta9000 (they are
        already applied by hand) and `MISSING`/`DRIFT` on a scratch root.

- [x] **Task 3.3: Step 4 — PostgreSQL settings via `ALTER SYSTEM`**
      (effort: 2)
  - [x] As `postgres` (`runuser -u postgres -- psql`), compare
        `pg_settings.setting` for `archive_mode`, `archive_command`,
        `wal_compression` against the constants; apply with
        `ALTER SYSTEM SET` only on drift, then `SELECT pg_reload_conf()`.
  - [x] After reload, re-read `pg_settings.pending_restart`; report any
        `PENDING RESTART` item by name. **Never restart.**
  - [x] Report `DRIFT` if `postgresql.conf` (path from `--cluster`) still
        contains an uncommented `archive_command` line — the hand edit the
        runbook says to remove.
  - [x] Also assert `pg_settings.sourcefile` for the three settings ends
        in `postgresql.auto.conf` (the script runs as `postgres`, so the
        column is visible); report `DRIFT <name> source` otherwise.
  - [x] Success: on manta9000 **`--check` only** shows `archive_command`
        as `DRIFT` (hand form → D2 form) and `archive_command source` as
        `DRIFT` (`postgresql.conf`). Apply mode is exercised in Task 3.7
        with a stubbed `psql`, never against production before Section 8.

- [x] 
  - [x] Done at the 2026-09-06 cutover: the PM's root run reported `DRIFT archive_command` (hand form → D2 form) and `DRIFT archive_mode source` before applying.

**Task 3.3a: PostgreSQL-step tests** (effort: 1)
  - [x] With a stubbed `psql` on `PATH` that answers `pg_settings` queries
        from a fixture and records statements: drifted settings → exactly
        those `ALTER SYSTEM SET` statements plus one `pg_reload_conf()`,
        never a restart command; matching settings → no statements; a
        `sourcefile` not ending in `postgresql.auto.conf` →
        `DRIFT <name> source`; `pending_restart = t` → `PENDING RESTART`
        line.
  - [x] Success: tests pass.

- [x] **Task 3.4: Step 5 — cron.d rendering** (effort: 2)
  - [x] Create `deploy/cron.d/manta-trading-backup` template with the six
        entries of D7 (glue names: `backup_health_cron.sh`,
        `sync_wal_offsite.sh`, `cron_nightly_metadata.sh`,
        `cron_weekly_backup.sh`, `cron_system_backup.sh` ×2) and placeholders for checkout, env file, backup
        root, cron user, interval schedule (`0 * * * *` when
        `WAL_OFFSITE_INTERVAL_MIN=60`; render `*/N * * * *` for N < 60,
        refuse other values), `--stale-after` = 3 × interval, push
        `--timeout` = interval − 1 minute, and the flag/stamp paths.
  - [x] Render with `sed` (or `envsubst`) into `/etc/cron.d/manta-trading-backup`
        0644 root; `--check` compares rendered content byte-for-byte.
  - [x] Success: the interval number appears in exactly one place in the
        repo (`grep -rn WAL_OFFSITE_INTERVAL_MIN` returns the constant and
        its uses only); a rendered file diff is empty on re-run.

- [x] **Task 3.4a: cron.d rendering tests** (effort: 1)
  - [x] Render to a temp path for an interval of 60 and of 15: six
        entries, substituted paths, expected user fields, `--stale-after`
        = 3 × interval, `--timeout` = interval − 1, trailing newline, no
        unescaped `%`; interval 45 refused.
  - [x] Success: tests pass.

- [x] **Task 3.5: Step 6 — timeshift managed keys** (effort: 1)
  - [x] With `jq`, read `/etc/timeshift/timeshift.json`, compare managed
        keys (`schedule_*`, `count_weekly`, `exclude`), write back only
        those keys on drift; never touch `backup_device_uuid`,
        `snapshot_size`, `snapshot_count`. Report the device UUID as info.
  - [x] Success: `--check` on manta9000 reports `DRIFT count_weekly 2 3`
        (check only; no apply before Section 8). Apply mode is exercised in
        Task 3.7 on a copy of the live file: managed keys change, a
        `jq del(managed keys)` projection is byte-identical before/after.

- [x] **Task 3.5a: Timeshift-merge tests** (effort: 1)
  - [x] `deploy/lib/timeshift_merge.sh` on a copy of the live file:
        managed keys change to the constants; the `jq del(managed keys)`
        projection is byte-identical; a file already conformant is
        unchanged byte-for-byte; a missing file is reported `MISSING` and
        not created.
  - [x] Success: tests pass.

- [x] **Task 3.6: Steps 7–8 — restic repository and leftover crontab
      lines** (effort: 1)
  - [x] Step 7: build the repo URL from the env file's `MT_BACKUP_S3_*`
        keys and the `system` prefix; `restic cat config` succeeds → `OK`;
        else `restic init` in apply mode. `MT_BACKUP_RESTIC_PASSWORD`
        missing from the env file is `MISSING` and blocks this step only.
  - [x] Step 7a: report the reconcile arm file
        `<backup-root>/RECONCILE-ARMED` as `OK` when present, `MISSING`
        otherwise; **never create it** — Task 9.4's watched run does, by
        hand. (Its absence is expected `MISSING` until then.)
  - [x] Step 8: `DRIFT` if `crontab -l -u $CRON_USER` contains any of
        `archive_health_cron.sh`, `cron_nightly_metadata.sh`,
        `cron_weekly_base.sh`. The script never edits the user crontab.
  - [x] Success: `--check` on manta9000 reports step 7 `MISSING` (no
        password yet) and step 8 `DRIFT` (lines present) — both expected
        before cutover.

- [x] **Task 3.6a: `--rehearse <dir>` and an apply-mode rehearsal**
      (effort: 1)
  - [x] Add `--rehearse <dir>`: cron.d renders to `<dir>/cron.d`, the
        timeshift file read/written is `<dir>/timeshift.json` (copy the
        live one in first), step 4 prints `SKIPPED step 4 (rehearse)` and
        touches no cluster, the restic prefix becomes `system-rehearse`.
        Everything else runs for real against the given `--backup-root`.
  - [x] Run it as root on manta9000 with a throwaway
        `--backup-root /data/backup-rehearse-920` and the real env file:
        first run prints `APPLIED` for steps 1 (if restic absent), 2, 3, 5,
        6, 7; the second run prints **zero** `APPLIED` lines. Then remove
        the throwaway root and the `system-rehearse` prefix in B2.
  - [x] Success: apply-mode idempotence is proven before the PM depends
        on it in Task 8.2; the rehearsal leaves no trace.

- [x] 
  - [x] Superseded by PM decision 2026-09-06: the cutover's own second run (`SUMMARY applied=0`) proved apply-mode idempotence on the real root; no throwaway rehearsal was run.

**Task 3.7: Steps 7–8 tests and shellcheck** (effort: 1)
  - [x] With a stubbed `restic` on `PATH`: `cat config` failing →
        `MISSING` in check mode and `restic init` in apply mode; missing
        password → `MISSING` and no restic call; arm file present/absent
        → `OK`/`MISSING` and never created; a fixture crontab containing a
        915 script name → `DRIFT`.
  - [x] `shellcheck deploy/setup-backup.sh deploy/lib/timeshift_merge.sh
        scripts/check_backup_health.sh scripts/backup_health_cron.sh`
        clean.
  - [x] Success: tests pass; shellcheck reports nothing.

- [x] **Task 3.8: Checkpoint commit** (effort: 1)
  - [x] Commit Section 3 (e.g.
        `feat: add deploy/setup-backup.sh with --check drift mode`).

## Section 4: Compressed archive path — prune, restore, runbook

Design *D2*, *D3*. Skip the `.zst` parts if Task 2.1 said no-go; the
atomic-write parts still apply. D3's "four coupled changes in one commit"
rule is met at the level that matters: nothing here takes effect on the
host until `setup-backup.sh` applies `ARCHIVE_COMMAND` in Task 8.2, and by
then Sections 3–5 are all on `main`. Intermediate commits are inert because
the live crontab keeps calling the untouched 915 scripts.

- [x] **Task 4.1: `prune_wal_archive.sh` — `-x .zst` and deletion count**
      (effort: 1)
  - [x] Pass `-x .zst` to `pg_archivecleanup` (constant
        `ARCHIVE_EXT=.zst` at the top; empty string when no-go).
  - [x] Print a machine-readable last line `PRUNED wal=<n> base=<m>`
        (count archive files before/after, dated dirs removed) for the
        reconcile in Section 5 to consume.
  - [x] Success: existing prune tests pass; the new line is present.

- [x] **Task 4.2: Mixed-archive prune verification** (effort: 1)
  - [x] Extend `test_prunes_by_manifest_and_keeps_newest` (or add a
        sibling) with a `tmp_path` archive holding raw names, `.zst`
        names, and a `.backup` history file across the cutoff; assert
        that older raw **and** older `.zst` files are removed, newer of
        both kept, `.prune-canary.tmp` ignored, and the `PRUNED` line
        counts match.
  - [x] Success: test passes against the real `pg_archivecleanup`.

- [x] **Task 4.3: Runbook 200 — archive shape and `restore_command`**
      (effort: 1)
  - [x] Replace Step 4's hand-edit instructions with "applied by
        `setup-backup.sh` step 4" and the D2 command verbatim (from the
        script's constant — copy, do not retype); add the "remove the
        hand-set `postgresql.conf` line" step.
  - [x] PITR section: the D3 `restore_command` handling both shapes, the
        mixed-archive note with the date raw segments age out, and the
        measured compression ratio.
  - [x] Recovery quick reference: the wedge remedy (`mv` the partial
        aside; confirm the source still has the segment in `pg_wal`
        first; never delete) and why `last_failed_wal` stays populated.
  - [x] Success: `grep -c 'zstd -dq' 200-backup-and-restore.md` ≥ 1; the
        archive command string in the runbook equals the script constant
        (a test in Task 4.4 asserts this).

- [x] **Task 4.4: Runbook/script consistency test** (effort: 1)
  - [x] Unit test: extract `ARCHIVE_COMMAND` from `deploy/setup-backup.sh`
        and assert the exact string appears in
        `runbooks/200-backup-and-restore.md`, so the two cannot drift.
  - [x] Success: test passes.

- [x] **Task 4.5: Checkpoint commit** (effort: 1)
  - [x] Commit Section 4 (e.g.
        `feat: prune and restore path for compressed atomic WAL archive`).

## Section 5: WAL offsite — hourly push and guarded weekly reconcile

Design *D4*, *D4a*.

- [x] **Task 5.1: `scripts/sync_wal_offsite.sh`** (effort: 2)
  - [x] Required `--wal-dir`, `--remote`, `--stamp`, `--timeout <min>`,
        `--lock <path>`. Constants: `MIN_AGE=2m`, `BWLIMIT=""` (off).
  - [x] `flock -n "$LOCK"`: if held, print `skipped: previous run active`
        and exit 0. Run `timeout "${TIMEOUT}m" rclone copy "$WAL_DIR"
        "$REMOTE" --min-age "$MIN_AGE" --exclude '*.tmp'` (+ `--bwlimit`
        when set). On success `touch "$STAMP"`; on failure exit non-zero,
        stamp untouched, one `logger -t manta-backup` line.
  - [x] Success: manual run against `b2:$BUCKET/wal` from the worktree
        (as `manta`, real credentials, additive only) uploads the current
        archive and touches the stamp; a second run uploads nothing new.

- [x] 
  - [x] Done: full push by hand 2026-09-06 10:08→17:15 (6,672 objects); hourly cron pushes since 18:00 upload only new segments and touch the stamp; `--verify` 0 differences (reconcile runs 2026-09-07).

**Task 5.2: Push tests** (effort: 1)
  - [x] Unit (no network): argument refusal; lock held → exit 0 with the
        skip message; rclone stubbed on `PATH` to fail → non-zero and no
        stamp; stubbed to succeed → stamp exists.
  - [x] Success: tests pass.

- [x] **Task 5.3: `scripts/reconcile_guards.sh` — the refusal contract**
      (effort: 2)
  - [x] Sourced helper (or standalone script) with required `--db-url`,
        `--backup-root`, `--wal-dir`, `--base-dir`. Four guards, each a
        function returning a reason: `mountpoint -q` on the backup root;
        WAL dir non-empty; the segment named by
        `pg_stat_archiver.last_archived_wal` present locally (with or
        without `.zst`); the oldest retained manifest's start segment
        (via `wal_segment_name.py from-lsn`) present locally.
  - [x] Any failure prints `reconcile refused: <reason>` and exits
        non-zero; all pass prints `reconcile guards passed`.
  - [x] Success: unit tests in Task 5.5 exercise each guard alone.

- [x] **Task 5.4: `scripts/cron_weekly_backup.sh` — ordered reconcile**
      (effort: 2)
  - [x] New glue replacing `cron_weekly_base.sh` (which stays untouched
        until Task 8.6): same arguments plus `--remote-wal`, `--lock`
        (the push's lock, so the push cannot run mid-reconcile). Constant
        `MAX_DELETE_MARGIN=50`. Keeps the archive-flag refusal as its
        first line.
  - [x] Order after the base backup: (1) catch-up push via
        `sync_wal_offsite.sh`; (2) `rclone check --one-way --exclude
        '*.tmp'`, abort on differences; (3) local prune, capturing its
        `PRUNED` line; (4) `reconcile_guards.sh`; (5) **only if the arm
        file `--armed <path>` exists**: `rclone sync … --max-delete
        $((wal + MARGIN))` of the WAL dir, then delete each offsite
        `base/<date>` prefix absent locally, logging every removal;
        otherwise log `reconcile skipped: not armed (<path>)` and exit 0
        after step 4 — the destructive step never runs unwatched; (6)
        final `rclone check --one-way` of the WAL dir.
  - [x] Success: `rclone sync` is unreachable on a failed guard or a
        failed check; `weekly_base_stale` covers a refused run.

- [x] **Task 5.5: Reconcile tests** (effort: 2)
  - [x] Unit (no network; rclone/psql stubbed on `PATH` recording their
        argv): each guard alone — empty WAL dir, non-mountpoint root,
        missing last-archived segment, missing manifest start segment —
        refused with its reason; prune reporting `wal=3` → the recorded
        sync argv carries `--max-delete 53`; a differing check → abort
        before prune; the archive flag present → refused before anything;
        arm file absent → guards run, `sync` never invoked, exit 0 with
        the skip line.
  - [x] Success: tests pass; no stub is ever invoked with `sync` on a
        refused path.

- [x] **Task 5.6: Scratch-prefix reconcile rehearsal against real B2**
      (effort: 2)
  - [x] Using `b2:$BUCKET/scratch-920/` (create, then delete at the end):
        a `tmp` WAL dir with 30 fake segments; run the reconcile steps
        1–6 pointed at it with `--base-dir` holding one manifest whose
        start segment is among them; delete 5 local files and re-run —
        offsite loses exactly 5; then empty the local dir and re-run —
        refused, offsite unchanged. Delete the scratch prefix.
  - [x] Success: `rclone lsf b2:$BUCKET/scratch-920/` is empty at the end
        and the observations are recorded in the runbook's drill table.

- [x] **Task 5.7: Checkpoint commit** (effort: 1)
  - [x] Commit Section 5 (e.g.
        `feat: hourly WAL offsite push and guarded weekly reconcile`).

Continue in `920-tasks.backup-hardening-and-host-bootstrap-2.md`.
