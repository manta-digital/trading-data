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
dateUpdated: 20260905
status: not_started
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
- Rules in force: no defaults for host paths or intervals — one constant
  each; never read a DB URL from ambient environment inside a tool; every
  new alarm must be observed firing before it counts as delivered; PM host
  steps are a script invocation plus a printed report, not a checklist.
- Commit checkpoint at the end of each section; scope `ruff format` and
  `shellcheck` (install via apt if absent) to touched files.
- Delivers (file 1): six named health failures on two flags, the atomic
  compressed archive command, prune `-x .zst`, `setup-backup.sh` with
  `--check`, the hourly WAL push, and the guarded weekly reconcile.
- Next planned slice after 920: none queued; PM decides.

## Section 1: Health-check named failures and the second flag

Design *D5*, *D6*. Read-only additions first, so the alarms exist before
anything they guard changes (D11 step 1).

- [ ] **Task 1.1: New arguments and constants in `check_archive_health.sh`**
      (effort: 1)
  - [ ] Add required arguments `--wal-dir <dir>`, `--stamp <path>`,
        `--stale-after <minutes>`, `--system-stamp <path>`,
        `--base-dir <dir>`. Missing any of them is a usage error (exit 2)
        naming the argument, matching the existing `--db-url` refusal.
  - [ ] Add named constants beside `MAX_UNARCHIVED_BYTES` /
        `MIN_FREE_PCT`: `WAL_SEGMENT_BYTES=16777216`,
        `TMP_LEFTOVER_MAX_AGE_MIN=10`, `SYSTEM_BACKUP_STALE_DAYS=2`,
        `WEEKLY_BASE_STALE_DAYS=9`, `PRUNE_CANARY=.prune-canary.tmp`.
  - [ ] Update the header comment's check list to name all ten checks and
        which flag each feeds (D6).
  - [ ] Success: script runs the existing four checks unchanged when all
        arguments are supplied; refuses when any is missing.

- [ ] **Task 1.2: `prune_permission` canary** (effort: 1)
  - [ ] Create then delete `$WAL_DIR/$PRUNE_CANARY` as the invoking user;
        either failure appends `prune_permission: …` naming the directory
        and the user (`id -un`). A `trap … EXIT` removes the canary on
        every exit path.
  - [ ] Success: with the ACL present the check passes; with a directory
        the user cannot write, `FAIL prune_permission` is printed.

- [ ] **Task 1.3: `archive_wedged` and `archive_tmp_leftover`** (effort: 2)
  - [ ] Derive the next-to-archive segment name: when `pg_stat_archiver`
        reports a current failure use `last_failed_wal`, otherwise
        `last_archived_wal` + 1 (the name arithmetic already exists in the
        backlog query — reuse its decoding, do not duplicate it; a small
        SQL expression returning the next name is acceptable).
  - [ ] `archive_wedged`: FAIL if `$WAL_DIR/<next>` exists with size ≠
        `WAL_SEGMENT_BYTES` and `$WAL_DIR/<next>.zst` does not exist.
  - [ ] `archive_tmp_leftover`: FAIL if any `*.tmp` in `$WAL_DIR` is older
        than `TMP_LEFTOVER_MAX_AGE_MIN` (`find -mmin`).
  - [ ] Success: both checks print their named FAIL line on a planted
        fault and nothing otherwise.

- [ ] **Task 1.4: `offsite_wal_stale`, `system_backup_stale`,
      `weekly_base_stale`** (effort: 1)
  - [ ] `offsite_wal_stale`: FAIL if `--stamp` is missing or older than
        `--stale-after` minutes.
  - [ ] `system_backup_stale`: FAIL if `--system-stamp` is missing or
        older than `SYSTEM_BACKUP_STALE_DAYS`.
  - [ ] `weekly_base_stale`: FAIL if the newest `YYYYMMDD` directory under
        `--base-dir` is older than `WEEKLY_BASE_STALE_DAYS` (compare the
        directory name as a date, not its mtime — a prune touches mtime).
  - [ ] Success: each prints its named FAIL line when its input is aged.

- [ ] **Task 1.5: Two-flag output contract** (effort: 1)
  - [ ] Print `FAIL <name>: …` lines as today, then a final summary line
        `FLAGS archive=<n> stale=<m>` giving the count per class:
        `archive` = the seven local-integrity checks (four existing plus
        `prune_permission`, `archive_wedged`, `archive_tmp_leftover`);
        `stale` = the three `*_stale` checks. Exit 1 on any FAIL.
  - [ ] Success: the class of every check is defined in exactly one
        place (an array or case at the top), not repeated per check.

- [ ] **Task 1.6: `archive_health_cron.sh` writes two flags** (effort: 1)
  - [ ] Add required `--wal-dir`, `--stamp`, `--stale-after`,
        `--system-stamp`, `--base-dir`, `--stale-flag <path>`; pass the
        first five through. Keep `--flag` as the archive flag.
  - [ ] From the summary line: write `--flag` when `archive>0`, write
        `--stale-flag` when `stale>0`, remove each when its count is 0.
        An uncheckable run (URL missing, DB unreachable) writes the
        archive flag as today.
  - [ ] Emit one `logger -t manta-backup` line on every flag transition
        (created or cleared), naming the flag and the FAIL names.
  - [ ] Success: `cron_weekly_base.sh` is untouched by this task and still
        gates only on `--health-flag` (the archive flag).

- [ ] **Task 1.7: Health-check tests** (effort: 2)
  - [ ] In `test/unit/test_backup_cron_glue.py` (no DB): argument refusal
        for each new required argument on both scripts; the glue writes
        the stale flag and not the archive flag when the check output
        reports `FLAGS archive=0 stale=1` (stub the inner check via a
        fake script on `PATH` or a `--check-cmd` seam consistent with how
        the existing `test_uncheckable_is_an_alarm_not_silence` works).
  - [ ] In `test/integration/` (test cluster, `MT_TIMESCALE_TEST_URL`):
        run `check_archive_health.sh` against `tmp_path` directories and
        assert each named line: canary in a read-only dir, a planted
        short file at the next segment name (compute from the same query
        the script uses), a 20-minute-old `x.zst.tmp`, an aged stamp, an
        aged system stamp, a `base/20260101` only. `archive_mode_off`
        will also fire on the test cluster; assert it is present and
        classed `archive`.
  - [ ] Success: unit tier passes; integration tests pass in isolation
        (run the tier separately per project convention).

- [ ] **Task 1.8: Checkpoint commit** (effort: 1)
  - [ ] Commit Section 1 (e.g.
        `feat: name six new archive-health failures on two flags`).

## Section 2: Compression measurement (go/no-go)

Design *D3*.

- [ ] **Task 2.1: Measure the archived-segment compression ratio**
      (effort: 1)
  - [ ] On manta9000, copy the 200 newest raw segments from
        `/data/backup/wal` to a scratch directory under `/data`
        (read-only against the archive), run `zstd -T2 -q` on each, and
        compute total raw bytes / total compressed bytes. Also record
        wall-clock per segment.
  - [ ] Write the result as a dated row in the design's D3 section
        ("Measured YYYY-MM-DD: ratio N.Nx over 200 segments, M ms/segment")
        and the decision: **go** if ≥ 1.5×, otherwise **no-go**.
  - [ ] Remove the scratch directory.
  - [ ] Success: the design carries the number and the decision; every
        later task in Sections 3–5 that says "if go" follows it.

- [ ] **Task 2.2: Checkpoint commit** (effort: 1)
  - [ ] Commit (e.g. `docs: record 920 archive compression measurement`).

## Section 3: `deploy/setup-backup.sh`

Design *D1*, *D2*, *D3*, *D7*, *D8*, *D9 step 7*. Build the skeleton and
`--check` first so every later step is testable as it lands.

- [ ] **Task 3.1: Skeleton, arguments, `--check`, reporting** (effort: 2)
  - [ ] Create `deploy/setup-backup.sh` in the `install-production.sh`
        mould: root check, `die`/`step` helpers, required arguments
        `--checkout`, `--env-file`, `--backup-root`, `--cluster`, no
        defaults; `--check` flag.
  - [ ] Constants block at the top, one definition each:
        `WAL_OFFSITE_INTERVAL_MIN=60`, `KEEP_DAYS=7`, `CRON_USER=manta`,
        `ARCHIVE_COMMAND` (D2 form, or the uncompressed atomic form if
        Task 2.1 said no-go), `WAL_COMPRESSION=zstd`,
        `TIMESHIFT_COUNT_WEEKLY=2`, the timeshift exclude list, the
        cron.d template path, the restic repo prefix `system`.
  - [ ] Reporting helper: each item calls `report OK|DRIFT|MISSING <item>
        [<expected> <actual>]`; in `--check` mode the script changes
        nothing and exits 1 if any item is not `OK`; in apply mode each
        step acts only when its check is not `OK` and re-reports.
  - [ ] Success: `sudo deploy/setup-backup.sh --check …` on manta9000
        runs to the end and prints one line per item (items from later
        tasks appear as they are added).

- [ ] **Task 3.2: Steps 1–3 — packages, directories, ACL** (effort: 1)
  - [ ] Step 1: `restic` installed (`dpkg -s`), install if missing.
  - [ ] Step 2: `base/ wal/ metadata/ system/` under `--backup-root`;
        `wal/` owner `postgres:postgres` mode 0755.
  - [ ] Step 3: `setfacl -m u:$CRON_USER:rwx -m d:u:$CRON_USER:rwx wal/`;
        check via `getfacl` for both the access and default entries.
  - [ ] Success: `--check` reports all three `OK` on manta9000 (they are
        already applied by hand) and `MISSING`/`DRIFT` on a scratch root.

- [ ] **Task 3.3: Step 4 — PostgreSQL settings via `ALTER SYSTEM`**
      (effort: 2)
  - [ ] As `postgres` (`runuser -u postgres -- psql`), compare
        `pg_settings.setting` for `archive_mode`, `archive_command`,
        `wal_compression` against the constants; apply with
        `ALTER SYSTEM SET` only on drift, then `SELECT pg_reload_conf()`.
  - [ ] After reload, re-read `pg_settings.pending_restart`; report any
        `PENDING RESTART` item by name. **Never restart.**
  - [ ] Report `DRIFT` if `postgresql.conf` (path from `--cluster`) still
        contains an uncommented `archive_command` line — the hand edit the
        runbook says to remove.
  - [ ] Success: on manta9000 `--check` shows `archive_command` as `DRIFT`
        (hand form → D2 form) until apply; after apply, `OK` and a `.zst`
        (or atomic raw) segment lands on the next switch — verified in
        file 2, not here.

- [ ] **Task 3.4: Step 5 — cron.d rendering** (effort: 2)
  - [ ] Create `deploy/cron.d/manta-trading-backup` template with the six
        entries of D7 and placeholders for checkout, env file, backup
        root, cron user, interval schedule (`0 * * * *` when
        `WAL_OFFSITE_INTERVAL_MIN=60`; render `*/N * * * *` for N < 60,
        refuse other values), `--stale-after` = 3 × interval, push
        `--timeout` = interval − 1 minute, and the flag/stamp paths.
  - [ ] Render with `sed` (or `envsubst`) into `/etc/cron.d/manta-trading-backup`
        0644 root; `--check` compares rendered content byte-for-byte.
  - [ ] Success: the interval number appears in exactly one place in the
        repo (`grep -rn WAL_OFFSITE_INTERVAL_MIN` returns the constant and
        its uses only); a rendered file diff is empty on re-run.

- [ ] **Task 3.5: Step 6 — timeshift managed keys** (effort: 1)
  - [ ] With `jq`, read `/etc/timeshift/timeshift.json`, compare managed
        keys (`schedule_*`, `count_weekly`, `exclude`), write back only
        those keys on drift; never touch `backup_device_uuid`,
        `snapshot_size`, `snapshot_count`. Report the device UUID as info.
  - [ ] Success: `--check` on manta9000 reports `DRIFT count_weekly 2 3`
        before apply and `OK` after; the file's other keys are unchanged
        (compare a `jq del(managed keys)` projection before/after).

- [ ] **Task 3.6: Steps 7–8 — restic repository and leftover crontab
      lines** (effort: 1)
  - [ ] Step 7: build the repo URL from the env file's `MT_BACKUP_S3_*`
        keys and the `system` prefix; `restic cat config` succeeds → `OK`;
        else `restic init` in apply mode. `MT_BACKUP_RESTIC_PASSWORD`
        missing from the env file is `MISSING` and blocks this step only.
  - [ ] Step 8: `DRIFT` if `crontab -l -u $CRON_USER` contains any of
        `archive_health_cron.sh`, `cron_nightly_metadata.sh`,
        `cron_weekly_base.sh`. The script never edits the user crontab.
  - [ ] Success: `--check` on manta9000 reports step 7 `MISSING` (no
        password yet) and step 8 `DRIFT` (lines present) — both expected
        before cutover.

- [ ] **Task 3.7: `setup-backup.sh` tests** (effort: 2)
  - [ ] In `test/unit/test_setup_backup.py` (subprocess, no root, no DB):
        argument refusal for each required argument; refusal when not
        root (assert the message names `sudo`); the cron.d template
        renders the six entries with substituted paths for an interval of
        60 and of 15, and refuses 45; the timeshift `jq` merge, run as a
        standalone function via a `--render-timeshift <in> <out>` test
        seam or by extracting the merge into a small sourced helper,
        leaves non-managed keys byte-identical.
  - [ ] `shellcheck deploy/setup-backup.sh scripts/check_archive_health.sh
        scripts/archive_health_cron.sh` clean.
  - [ ] Success: tests pass; shellcheck reports nothing.

- [ ] **Task 3.8: Checkpoint commit** (effort: 1)
  - [ ] Commit Section 3 (e.g.
        `feat: add deploy/setup-backup.sh with --check drift mode`).

## Section 4: Compressed archive path — prune, restore, runbook

Design *D2*, *D3*. Skip the `.zst` parts if Task 2.1 said no-go; the
atomic-write parts still apply.

- [ ] **Task 4.1: `prune_wal_archive.sh` — `-x .zst` and deletion count**
      (effort: 1)
  - [ ] Pass `-x .zst` to `pg_archivecleanup` (constant
        `ARCHIVE_EXT=.zst` at the top; empty string when no-go).
  - [ ] Print a machine-readable last line `PRUNED wal=<n> base=<m>`
        (count archive files before/after, dated dirs removed) for the
        reconcile in Section 5 to consume.
  - [ ] Success: existing prune tests pass; the new line is present.

- [ ] **Task 4.2: Mixed-archive prune verification** (effort: 1)
  - [ ] Extend `test_prunes_by_manifest_and_keeps_newest` (or add a
        sibling) with a `tmp_path` archive holding raw names, `.zst`
        names, and a `.backup` history file across the cutoff; assert
        that older raw **and** older `.zst` files are removed, newer of
        both kept, `.prune-canary.tmp` ignored, and the `PRUNED` line
        counts match.
  - [ ] Success: test passes against the real `pg_archivecleanup`.

- [ ] **Task 4.3: Runbook 200 — archive shape and `restore_command`**
      (effort: 1)
  - [ ] Replace Step 4's hand-edit instructions with "applied by
        `setup-backup.sh` step 4" and the D2 command verbatim (from the
        script's constant — copy, do not retype); add the "remove the
        hand-set `postgresql.conf` line" step.
  - [ ] PITR section: the D3 `restore_command` handling both shapes, the
        mixed-archive note with the date raw segments age out, and the
        measured compression ratio.
  - [ ] Recovery quick reference: the wedge remedy (`mv` the partial
        aside; confirm the source still has the segment in `pg_wal`
        first; never delete) and why `last_failed_wal` stays populated.
  - [ ] Success: `grep -c 'zstd -dq' 200-backup-and-restore.md` ≥ 1; the
        archive command string in the runbook equals the script constant
        (a test in Task 4.4 asserts this).

- [ ] **Task 4.4: Runbook/script consistency test** (effort: 1)
  - [ ] Unit test: extract `ARCHIVE_COMMAND` from `deploy/setup-backup.sh`
        and assert the exact string appears in
        `runbooks/200-backup-and-restore.md`, so the two cannot drift.
  - [ ] Success: test passes.

- [ ] **Task 4.5: Checkpoint commit** (effort: 1)
  - [ ] Commit Section 4 (e.g.
        `feat: prune and restore path for compressed atomic WAL archive`).

## Section 5: WAL offsite — hourly push and guarded weekly reconcile

Design *D4*, *D4a*.

- [ ] **Task 5.1: `scripts/sync_wal_offsite.sh`** (effort: 2)
  - [ ] Required `--wal-dir`, `--remote`, `--stamp`, `--timeout <min>`,
        `--lock <path>`. Constants: `MIN_AGE=2m`, `BWLIMIT=""` (off).
  - [ ] `flock -n "$LOCK"`: if held, print `skipped: previous run active`
        and exit 0. Run `timeout "${TIMEOUT}m" rclone copy "$WAL_DIR"
        "$REMOTE" --min-age "$MIN_AGE" --exclude '*.tmp'` (+ `--bwlimit`
        when set). On success `touch "$STAMP"`; on failure exit non-zero,
        stamp untouched, one `logger -t manta-backup` line.
  - [ ] Success: manual run against `b2:$BUCKET/wal` from the worktree
        (as `manta`, real credentials, additive only) uploads the current
        archive and touches the stamp; a second run uploads nothing new.

- [ ] **Task 5.2: Push tests** (effort: 1)
  - [ ] Unit (no network): argument refusal; lock held → exit 0 with the
        skip message; rclone stubbed on `PATH` to fail → non-zero and no
        stamp; stubbed to succeed → stamp exists.
  - [ ] Success: tests pass.

- [ ] **Task 5.3: `cron_weekly_base.sh` — guarded reconcile** (effort: 3)
  - [ ] New required `--remote-wal`, `--lock` (same lock as the push, so
        the push cannot run mid-reconcile). Constant
        `MAX_DELETE_MARGIN=50`.
  - [ ] Order after the base backup: (1) catch-up `rclone copy` of the
        WAL dir (reuse `sync_wal_offsite.sh`, do not duplicate); (2)
        `rclone check --one-way --exclude '*.tmp'`, abort on differences;
        (3) local prune, capturing its `PRUNED` line; (4) guards:
        `mountpoint -q` on the backup root, WAL dir non-empty, the segment
        named by `pg_stat_archiver.last_archived_wal` present locally (with
        or without `.zst`), the oldest retained manifest's start segment
        present locally; (5) `rclone sync … --max-delete $((wal + MARGIN))`
        of the WAL dir, then delete each offsite `base/<date>` prefix not
        present locally, logging every removal; (6) final `rclone check
        --one-way` of the WAL dir.
  - [ ] Every guard failure prints `reconcile refused: <reason>` and exits
        non-zero **before** step 5.
  - [ ] Success: the script never reaches `rclone sync` on a failed
        guard (tests below); the `weekly_base_stale` alarm from Section 1
        covers a refused run.

- [ ] **Task 5.4: Reconcile tests** (effort: 2)
  - [ ] Unit (no network; rclone/psql stubbed on `PATH` recording their
        argv): empty WAL dir → refused before sync; non-mountpoint root →
        refused; missing last-archived segment → refused; prune reporting
        `wal=3` → the recorded sync argv carries `--max-delete 53`; a
        differing check → abort before prune.
  - [ ] Success: tests pass; no stub is ever invoked with `sync` on a
        refused path.

- [ ] **Task 5.5: Scratch-prefix reconcile rehearsal against real B2**
      (effort: 2)
  - [ ] Using `b2:$BUCKET/scratch-920/` (create, then delete at the end):
        a `tmp` WAL dir with 30 fake segments; run the reconcile steps
        1–6 pointed at it with `--base-dir` holding one manifest whose
        start segment is among them; delete 5 local files and re-run —
        offsite loses exactly 5; then empty the local dir and re-run —
        refused, offsite unchanged. Delete the scratch prefix.
  - [ ] Success: `rclone lsf b2:$BUCKET/scratch-920/` is empty at the end
        and the observations are recorded in the runbook's drill table.

- [ ] **Task 5.6: Checkpoint commit** (effort: 1)
  - [ ] Commit Section 5 (e.g.
        `feat: hourly WAL offsite push and guarded weekly reconcile`).

Continue in `920-tasks.backup-hardening-and-host-bootstrap-2.md`.
